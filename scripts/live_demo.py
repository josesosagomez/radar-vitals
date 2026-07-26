"""scripts/live_demo.py — Live and replay radar vital-signs demo.

CLI usage
---------
# Replay by session (resolves bin path and locked_bin from manifest):
    python scripts/live_demo.py --replay-session test5

# Replay an explicit file with explicit locked bin:
    python scripts/live_demo.py --replay data/raw/test5.bin --locked-bin 24

# Fast replay for debugging:
    python scripts/live_demo.py --replay-session test5 --replay-fast

# Exit after N seconds (smoke test):
    python scripts/live_demo.py --replay-session test5 --duration-s 45

# Headless replay artifact smoke test:
    python scripts/live_demo.py --replay data/raw/test5.bin --replay-fast --headless

# Live hardware, locked_bin from manifest:
    python scripts/live_demo.py --live-session test3

# Live hardware, manual bin override:
    python scripts/live_demo.py --locked-bin 25

# Radar already streaming (skip UART/DCA1000 setup):
    python scripts/live_demo.py --locked-bin 25 --no-configure

Artifacts are written to results/live_demo/<timestamp>_<mode>_<session_id>/.

The readouts here are a live sanity check, not paper-grade results (CLAUDE.md S4):
the live path uses an online median smoother, not the validated offline estimator.
Paper metrics come from re-processing the run's saved raw adc_stream.bin offline.
"""
from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import json
import queue
import struct
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

# ── sys.path so src/ imports work regardless of cwd ──────────────────────────
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.radar_io import ChirpConfig, read_adc_bin
# The window-level DSP composition and the warmup bin-selection policy live in src/
# so the M4 offline harness runs the SAME code, not a copy of it (M4 plan §5.1).
from src.warmup_select import (
    derive_candidate_bins,
    resolve_locked_bin,
    run_warmup_selection,
)
from src.window_pipeline import run_window_dsp

PAYLOAD_BYTES_PER_PKT = 1456   # DCA1000 ADC payload bytes per UDP packet

# Sentinel for end-of-replay stream
_REPLAY_END = object()


def _sha256_file(path: Path) -> str:
    """SHA256 of a file via 1 MB streaming reads — avoids loading large .bin into memory."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


# ── Backend selection (must happen before pyplot import) ─────────────────────

def _select_backend(cfg: dict) -> None:
    preferred = cfg.get("display", {}).get("matplotlib_backend", "QtAgg")
    fallbacks = list(cfg.get("display", {}).get("backend_fallbacks", ["TkAgg"]))
    import matplotlib
    for name in [preferred] + fallbacks:
        try:
            matplotlib.use(name)
            # Force a test import to confirm the backend loads
            from matplotlib import pyplot as _plt  # noqa: F401
            print(f"  matplotlib backend: {name}")
            return
        except Exception as exc:
            print(f"  backend {name} failed: {exc}")
    print("  WARNING: no GUI backend available; falling back to default")


# ── FrameSource interface ─────────────────────────────────────────────────────

class FrameSource(ABC):
    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def get_frame(self, timeout_s: float = 0.1) -> "tuple[int, np.ndarray] | None | object":
        """Return (frame_idx, cube) or None (empty) or _REPLAY_END (stream done)."""
        ...

    @abstractmethod
    def stop(self) -> None: ...


# ── ReplayFrameSource ─────────────────────────────────────────────────────────

class ReplayFrameSource(FrameSource):
    """Reads existing .bin files and feeds frames at real-time (or fast) speed."""

    def __init__(self, paths: list[Path], chirp_cfg: ChirpConfig, fast: bool = False):
        self._paths = paths
        self._chirp_cfg = chirp_cfg
        self._fast = fast
        self._stop = threading.Event()
        self._cube: np.ndarray | None = None
        self._idx = 0
        self._t_start: float | None = None
        self._ended = False
        self.file_hashes: dict[str, str] = {}

    def start(self) -> None:
        for p in self._paths:
            self.file_hashes[str(p)] = _sha256_file(p)
        path_arg = self._paths[0] if len(self._paths) == 1 else self._paths
        try:
            self._cube = read_adc_bin(path_arg, self._chirp_cfg)
        except Exception as exc:
            print(f"ReplayFrameSource: read_adc_bin failed: {exc}", file=sys.stderr)
            self._ended = True
            return
        self._idx = 0
        self._t_start = time.monotonic()
        self._ended = False

    def get_frame(self, timeout_s: float = 0.0):
        if self._ended:
            return _REPLAY_END
        if self._cube is None or self._stop.is_set():
            self._ended = True
            return _REPLAY_END
        if self._idx >= self._cube.shape[0]:
            self._ended = True
            return _REPLAY_END

        if not self._fast:
            assert self._t_start is not None
            frame_period = 1.0 / self._chirp_cfg.frame_rate_hz
            target = self._t_start + self._idx * frame_period
            if time.monotonic() < target:
                if timeout_s > 0:
                    time.sleep(min(timeout_s, target - time.monotonic()))
                if time.monotonic() < target:
                    return None

        idx = self._idx
        self._idx += 1
        try:
            return idx, self._cube[idx].copy()
        except IndexError:
            self._ended = True
            return None

    def stop(self) -> None:
        self._stop.set()


# ── LiveFrameSource ───────────────────────────────────────────────────────────

class LiveFrameSource(FrameSource):
    """Receives DCA1000 UDP packets, assembles frames, optionally mirrors ADC bytes.

    Two construction modes:
      sock_dat provided  — normal live run; DCA1000.configure() already bound the
                           data socket, pass dca._sock_dat.  LiveFrameSource does
                           NOT close it (DCA1000.close() owns it).
      net_cfg provided   — --no-configure mode; no DCA1000 object exists, so
                           LiveFrameSource opens and owns its own data socket.
    """

    def __init__(
        self,
        profile_cfg: dict,
        *,
        sock_dat=None,
        net_cfg: dict | None = None,
        zero_fill_leading_loss: bool = True,
        raw_mirror_path: Path | None = None,
    ):
        if sock_dat is None and net_cfg is None:
            raise ValueError("LiveFrameSource: provide sock_dat or net_cfg")
        self._provided_sock = sock_dat
        self._net_cfg = net_cfg
        self._owns_sock = sock_dat is None
        self._zero_fill_leading = zero_fill_leading_loss

        self._n_chirps = int(profile_cfg["num_chirps_per_frame"])
        self._n_rx = int(profile_cfg["num_rx"])
        self._n_adc = int(profile_cfg["num_adc_samples"])
        self._iq_swap = bool(profile_cfg["iq_swap"])
        self._bytes_per_frame = self._n_chirps * self._n_rx * self._n_adc * 4
        self._raw_mirror_path = raw_mirror_path
        self._q: queue.Queue = queue.Queue(maxsize=400)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock = None
        # Public stats (read after stop)
        self.n_received: int = 0
        self.n_dropped: int = 0
        self.zero_filled_bytes: int = 0
        self.mirror_truncated_bytes: int = 0
        self.mirror_sha256: str | None = None
        self._frame_idx: int = 0

    def start(self) -> None:
        import socket as _socket
        if self._owns_sock:
            s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
            s.bind((self._net_cfg["host_ip"], self._net_cfg["data_port"]))
            s.setsockopt(_socket.SOL_SOCKET, _socket.SO_RCVBUF, 8 * 1024 * 1024)
            self._sock = s
        else:
            self._sock = self._provided_sock
        self._thread = threading.Thread(target=self._loop, daemon=True, name="LiveSource")
        self._thread.start()

    def _decode_frame(self, frame_bytes: bytes) -> np.ndarray:
        raw = np.frombuffer(frame_bytes, dtype="<i2")
        words = raw.reshape(-1, 4)
        out = np.empty(raw.size // 2, dtype=np.complex64)
        if self._iq_swap:
            # SampleSwap=1: packet = [Q_n, Q_{n+1}, I_n, I_{n+1}]
            out[0::2] = words[:, 2].astype(np.float32) + 1j * words[:, 0].astype(np.float32)
            out[1::2] = words[:, 3].astype(np.float32) + 1j * words[:, 1].astype(np.float32)
        else:
            # SampleSwap=0: packet = [I_n, I_{n+1}, Q_n, Q_{n+1}]
            out[0::2] = words[:, 0].astype(np.float32) + 1j * words[:, 2].astype(np.float32)
            out[1::2] = words[:, 1].astype(np.float32) + 1j * words[:, 3].astype(np.float32)
        return out.reshape(self._n_chirps, self._n_rx, self._n_adc)

    def _loop(self) -> None:
        buf = bytearray()
        first_seq: int | None = None
        last_seq: int = 0
        mirror = open(self._raw_mirror_path, "wb") if self._raw_mirror_path else None
        self._sock.settimeout(0.1)
        try:
            while not self._stop.is_set():
                try:
                    pkt = self._sock.recv(1470)
                except Exception:
                    continue
                if len(pkt) < 10:
                    continue

                seq = struct.unpack_from("<I", pkt, 0)[0]
                payload = pkt[10:]

                # Discard duplicates and out-of-order late arrivals (mirrors capture.py)
                if last_seq and seq <= last_seq:
                    continue

                if first_seq is None:
                    first_seq = seq
                    # Leading-loss zero-fill: only for a fresh stream we started.
                    # In --no-configure mode we are attaching mid-stream; seq may be
                    # 50000+, so zero-filling that many packets would corrupt alignment.
                    if self._zero_fill_leading and seq > 1:
                        gap = seq - 1
                        self.n_dropped += gap
                        gap_bytes = gap * PAYLOAD_BYTES_PER_PKT
                        zeros = b"\x00" * gap_bytes
                        buf.extend(zeros)
                        if mirror:
                            mirror.write(zeros)
                        self.zero_filled_bytes += gap_bytes
                elif seq > last_seq + 1:
                    # Mid-stream gap
                    gap = seq - last_seq - 1
                    self.n_dropped += gap
                    gap_bytes = gap * PAYLOAD_BYTES_PER_PKT
                    zeros = b"\x00" * gap_bytes
                    buf.extend(zeros)
                    if mirror:
                        mirror.write(zeros)
                    self.zero_filled_bytes += gap_bytes

                buf.extend(payload)
                if mirror:
                    mirror.write(payload)
                self.n_received += 1
                last_seq = seq

                while len(buf) >= self._bytes_per_frame:
                    frame_bytes = bytes(buf[: self._bytes_per_frame])
                    del buf[: self._bytes_per_frame]
                    try:
                        self._q.put_nowait((self._frame_idx, self._decode_frame(frame_bytes)))
                    except queue.Full:
                        pass
                    self._frame_idx += 1
        finally:
            if mirror:
                mirror.close()
                if self._raw_mirror_path and self._raw_mirror_path.exists():
                    size = self._raw_mirror_path.stat().st_size
                    remainder = size % self._bytes_per_frame
                    if remainder:
                        with self._raw_mirror_path.open("r+b") as fh:
                            fh.truncate(size - remainder)
                        self.mirror_truncated_bytes = remainder
                    self.mirror_sha256 = _sha256_file(self._raw_mirror_path)

    def get_frame(self, timeout_s: float = 0.0):
        try:
            return self._q.get(timeout=timeout_s)
        except queue.Empty:
            return None

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        if self._owns_sock and self._sock:
            try:
                self._sock.close()
            except Exception:
                pass


# ── Manifest helpers ──────────────────────────────────────────────────────────

def _load_manifest(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def _find_session(rows: list[dict], session_id: str) -> dict | None:
    for row in rows:
        if row.get("session_id") == session_id:
            return row
    return None


def _resolve_replay_paths(session_id: str, raw_dir: Path, shard_limit: int) -> list[Path]:
    single = raw_dir / f"{session_id}.bin"
    if single.exists():
        return [single]
    shards = []
    for i in range(shard_limit):
        s = raw_dir / f"{session_id}_{i}.bin"
        if not s.exists():
            break
        shards.append(s)
    if shards:
        return shards
    raise FileNotFoundError(
        f"No .bin file found for session '{session_id}' in {raw_dir}. "
        f"Searched: {single}, {raw_dir / f'{session_id}_0.bin'}, ..."
    )


# ── Run directory + metadata ──────────────────────────────────────────────────

def _create_run_dir(results_dir: Path, mode: str, session_id: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = results_dir / f"{ts}_{mode}_{session_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _git_info() -> dict:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
        dirty_out = subprocess.check_output(
            ["git", "status", "--porcelain"], stderr=subprocess.DEVNULL
        ).decode().strip()
        return {"git_commit": commit, "git_dirty": bool(dirty_out)}
    except Exception:
        return {"git_commit": "unknown", "git_dirty": None}


def _json_serialise(obj):
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (np.integer, np.floating)):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    raise TypeError(f"Not JSON serialisable: {type(obj)}")


def _write_metadata(path: Path, meta: dict) -> None:
    path.write_text(json.dumps(meta, indent=2, default=_json_serialise))


def _save_intermediates(path: Path, records: list[dict]) -> None:
    if not records:
        return
    arrays: dict[str, np.ndarray] = {}
    for key in records[0]:
        vals = [r.get(key) for r in records]
        try:
            arrays[key] = np.array(vals)
        except Exception:
            pass
    np.savez_compressed(str(path), **arrays)


# ── Display ───────────────────────────────────────────────────────────────────

class _LiveDisplay:
    _CONF_COLOR = {"high": "#00cc66", "medium": "orange", "low": "#cc3333"}

    def __init__(self, history_s: float):
        self._hist_s = history_s
        self._fig = None
        self._ax_status = None
        self._ax_hr = None
        self._ax_hr_val = None
        self._ax_br = None
        self._ax_br_val = None
        self._line_hr_s = None
        self._line_hr_r = None
        self._line_br = None
        self._txt_status = None
        self._txt_hr_big = None
        self._txt_hr_label = None
        self._txt_br_big = None
        self._txt_br_label = None

    def _style_axis(self, ax) -> None:
        ax.set_facecolor("#1a1a1a")
        ax.tick_params(colors="#ccc")
        for sp in ax.spines.values():
            sp.set_edgecolor("#444")

    def setup(self):
        from matplotlib import pyplot as plt

        fig = plt.figure(figsize=(12, 6.5))
        fig.patch.set_facecolor("#111")
        gs = fig.add_gridspec(
            nrows=3, ncols=2,
            height_ratios=[0.28, 1, 1], width_ratios=[4, 1],
            left=0.07, right=0.98, top=0.96, bottom=0.09,
            hspace=0.45, wspace=0.06,
        )

        ax_status = fig.add_subplot(gs[0, :])
        ax_hr = fig.add_subplot(gs[1, 0])
        ax_hr_val = fig.add_subplot(gs[1, 1])
        ax_br = fig.add_subplot(gs[2, 0], sharex=ax_hr)
        ax_br_val = fig.add_subplot(gs[2, 1])
        for ax in (ax_status, ax_hr, ax_hr_val, ax_br, ax_br_val):
            self._style_axis(ax)

        # Status bar: HR/BR confidence, locked bin/distance, elapsed clock.
        ax_status.set_xticks([])
        ax_status.set_yticks([])
        txt_status = ax_status.text(
            0.015, 0.5, "", transform=ax_status.transAxes,
            ha="left", va="center", color="#eee", fontsize=13,
        )

        # HR graph (left) + HR value readout (right).
        (self._line_hr_s,) = ax_hr.plot(
            [], [], color="#00cc66", lw=2, label="HR smooth (AHET-verified)"
        )
        (self._line_hr_r,) = ax_hr.plot(
            [], [], ".", color="#00cc66", alpha=0.35, ms=4, label="HR raw"
        )
        ax_hr.set_ylim(40, 160)
        ax_hr.set_ylabel("Heart rate (bpm)", color="#ccc")
        ax_hr.legend(loc="upper right", fontsize=8, facecolor="#333", labelcolor="#ccc")

        ax_hr_val.set_xticks([])
        ax_hr_val.set_yticks([])
        txt_hr_big = ax_hr_val.text(
            0.5, 0.60, "--", transform=ax_hr_val.transAxes,
            ha="center", va="center", color="#ccc", fontsize=50, fontweight="bold",
        )
        txt_hr_label = ax_hr_val.text(
            0.5, 0.22, "bpm", transform=ax_hr_val.transAxes,
            ha="center", va="center", color="#999", fontsize=10,
        )

        # BR graph (left) + BR value readout (right).
        (self._line_br,) = ax_br.plot([], [], color="#4499ff", lw=2)
        ax_br.set_ylim(6, 30)
        ax_br.set_ylabel("Breathing rate (bpm)", color="#ccc")
        ax_br.set_xlabel("Elapsed (s)", color="#ccc")

        ax_br_val.set_xticks([])
        ax_br_val.set_yticks([])
        txt_br_big = ax_br_val.text(
            0.5, 0.60, "--", transform=ax_br_val.transAxes,
            ha="center", va="center", color="#ccc", fontsize=50, fontweight="bold",
        )
        txt_br_label = ax_br_val.text(
            0.5, 0.22, "bpm", transform=ax_br_val.transAxes,
            ha="center", va="center", color="#999", fontsize=10,
        )

        self._fig = fig
        self._ax_status = ax_status
        self._ax_hr, self._ax_hr_val = ax_hr, ax_hr_val
        self._ax_br, self._ax_br_val = ax_br, ax_br_val
        self._txt_status = txt_status
        self._txt_hr_big, self._txt_hr_label = txt_hr_big, txt_hr_label
        self._txt_br_big, self._txt_br_label = txt_br_big, txt_br_label
        return fig

    def warmup(self, n_buf: int, n_needed: int, elapsed: float) -> None:
        self._txt_status.set_text(
            f"Warming up: {n_buf}/{n_needed} frames  ({elapsed:.0f} s)"
        )
        self._fig.canvas.draw_idle()

    def update(
        self,
        times: list,
        hr_smooth: list,
        hr_raw: list,
        br: list,
        hr_conf: str,
        br_conf: str,
        elapsed: float,
        cur_hr: float,
        cur_br: float,
        locked_bin: int | None,
        locked_range_m: float | None,
    ) -> None:
        t = np.array(times, dtype=float)
        mask = t >= elapsed - self._hist_s

        def _m(lst):
            a = np.array(lst, dtype=float)
            return t[mask], a[mask]

        t_hr, hs = _m(hr_smooth)
        _, hr = _m(hr_raw)
        t_br, b = _m(br)

        self._line_hr_s.set_data(t_hr, hs)
        self._line_hr_r.set_data(t_hr, hr)
        self._line_br.set_data(t_br, b)

        x_lo = max(0.0, elapsed - self._hist_s)
        self._ax_hr.set_xlim(x_lo, elapsed + 3)
        self._ax_br.set_xlim(x_lo, elapsed + 3)

        hr_num = f"{cur_hr:.0f}" if np.isfinite(cur_hr) else "--"
        self._txt_hr_big.set_text(hr_num)
        self._txt_hr_big.set_color(self._CONF_COLOR.get(hr_conf, "#ccc"))

        br_num = f"{cur_br:.0f}" if np.isfinite(cur_br) else "--"
        self._txt_br_big.set_text(br_num)
        self._txt_br_big.set_color(self._CONF_COLOR.get(br_conf, "#ccc"))

        elapsed_fmt = time.strftime("%H:%M:%S", time.gmtime(int(elapsed)))
        bin_str = "bin: --"
        if locked_bin is not None and locked_range_m is not None:
            bin_str = f"bin: {locked_bin} (~{locked_range_m:.2f} m)"

        self._txt_status.set_text(
            f"HR conf: {hr_conf}   |   BR conf: {br_conf}   |   "
            f"{bin_str}   |   elapsed: {elapsed_fmt}"
        )
        self._fig.canvas.draw_idle()


class _HeadlessDisplay:
    def warmup(self, n_buf: int, n_needed: int, elapsed: float) -> None:
        return

    def update(
        self,
        times: list,
        hr_smooth: list,
        hr_raw: list,
        br: list,
        hr_conf: str,
        br_conf: str,
        elapsed: float,
        cur_hr: float,
        cur_br: float,
        locked_bin: int | None,
        locked_range_m: float | None,
    ) -> None:
        return


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args():
    ap = argparse.ArgumentParser(description="Live / replay radar vital-signs demo")
    ap.add_argument("--config", default="scripts/live_demo_config.yaml")

    mode_grp = ap.add_mutually_exclusive_group()
    mode_grp.add_argument("--replay-session", metavar="SESSION_ID")
    mode_grp.add_argument(
        "--replay", metavar="BIN_PATH", action="append", dest="replay_paths"
    )
    mode_grp.add_argument("--live-session", metavar="SESSION_ID")

    ap.add_argument("--locked-bin", type=int, default=None)
    ap.add_argument("--replay-fast", action="store_true")
    ap.add_argument("--duration-s", type=float, default=None)
    ap.add_argument("--no-configure", action="store_true")
    ap.add_argument(
        "--headless",
        action="store_true",
        help="Run without the Matplotlib display; intended for replay artifact smoke tests.",
    )
    return ap.parse_args()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = _parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        sys.exit(f"ERROR: config not found: {cfg_path}")
    with cfg_path.open() as fh:
        cfg = yaml.safe_load(fh)

    delay_s = 120
    
    if delay_s > 0:
        for remaining in range(int(delay_s), 0, -1):
            print(f"Starting capture in {remaining} s...")
            time.sleep(1)

    np.random.seed(int(cfg.get("seed", 42)))

    # Backend must be selected before pyplot is imported anywhere
    if args.headless:
        cfg.setdefault("display", {})["matplotlib_backend"] = "Agg"
        cfg["display"]["backend_fallbacks"] = []
    _select_backend(cfg)
    from matplotlib import animation, pyplot as plt

    # ── Mode resolution ───────────────────────────────────────────────────────
    if args.live_session or (not args.replay_session and not args.replay_paths):
        mode = "live"
    else:
        mode = "replay"

    # ── Session metadata from manifest ────────────────────────────────────────
    manifest = _load_manifest(Path(cfg["paths"]["manifest"]))
    session_id = "unknown"
    session_row: dict | None = None
    manifest_locked_bin: int | None = None
    iq_swap = bool(cfg["profile"]["iq_swap"])
    posture: str | None = None
    distance_cm: str | None = None

    if args.replay_session:
        session_id = args.replay_session
        session_row = _find_session(manifest, session_id)
    elif args.live_session:
        session_id = args.live_session
        session_row = _find_session(manifest, session_id)

    if session_row:
        lb_str = session_row.get("locked_bin", "").strip()
        if lb_str:
            try:
                manifest_locked_bin = int(lb_str)
            except ValueError:
                pass
        iq_str = session_row.get("iq_swap", "").strip().lower()
        if iq_str in ("true", "1"):
            iq_swap = True
        elif iq_str in ("false", "0"):
            iq_swap = False
        posture = session_row.get("posture") or None
        distance_cm = session_row.get("distance_cm") or None

    # --locked-bin wins over manifest; otherwise warmup may choose the bin.
    bin_selection_enabled = bool(cfg.get("bin_selection", {}).get("enabled", False))
    locked_bin, locked_bin_source, warmup_pending = resolve_locked_bin(
        args.locked_bin, manifest_locked_bin, bin_selection_enabled
    )
    if locked_bin is None and not warmup_pending:
        sys.exit(
            f"ERROR: locked_bin not set. "
            f"Use --locked-bin or ensure manifest has locked_bin for session '{session_id}'."
        )

    # ── Radar parameters ──────────────────────────────────────────────────────
    pcfg = cfg["profile"]
    fs = float(cfg["session"]["frame_rate_hz"])
    window_s = float(cfg["session"]["window_s"])
    hop_s = float(cfg["session"]["hop_s"])
    frames_per_hop = max(1, int(round(hop_s * fs)))
    ring_maxlen = int(round(window_s * fs))
    shard_limit = int(cfg["capture"].get("replay_shard_limit", 20))
    display_history_s = float(cfg["session"]["display_history_s"])
    checkpoint_n = int(cfg["capture"].get("intermediates_checkpoint_windows", 60))
    duration_s: float | None = args.duration_s
    smoother_n = int(cfg["heart"].get("online_smoother_n", 5))

    chirp_cfg = ChirpConfig(
        num_adc_samples=int(pcfg["num_adc_samples"]),
        num_rx=int(pcfg["num_rx"]),
        num_tx=1,
        num_chirps_per_frame=int(pcfg["num_chirps_per_frame"]),
        num_frames=0,
        frame_rate_hz=fs,
        range_resolution_m=float(pcfg["range_resolution_m"]),
        iq_swap=iq_swap,
    )

    # ── Run directory ─────────────────────────────────────────────────────────
    results_dir = Path(cfg["paths"]["results_dir"])
    run_dir = _create_run_dir(results_dir, mode, session_id)
    print(f"Run directory: {run_dir}")
    warmup_selection_path = run_dir / "warmup_bin_selection.json"

    # ── Initial run_metadata.json ─────────────────────────────────────────────
    git = _git_info()
    run_meta: dict = {
        "command": " ".join(sys.argv),
        "start_wall_utc": datetime.now(timezone.utc).isoformat(),
        "end_wall_utc": None,
        "mode": mode,
        "session_id": session_id,
        "locked_bin": locked_bin,
        "manifest_locked_bin": manifest_locked_bin,
        "locked_bin_source": locked_bin_source,
        "locked_bin_overridden": locked_bin_source == "manual",
        "warmup_selected_bin": None,
        "warmup_selection_confidence": None,
        "warmup_selection_path": None,
        "t_warmup_scan_ms": None,
        "range_resolution_m": float(pcfg["range_resolution_m"]),
        "iq_swap": iq_swap,
        "posture": posture,
        "distance_cm": distance_cm,
        "config": cfg,
        "config_path": str(cfg_path.resolve()),
        "seed": int(cfg.get("seed", 42)),
        "completion_status": "running",
        "replay_files": None,
        "replay_file_hashes": None,
        "raw_stream_format": "adc_bytes_no_packet_headers",
        "live_raw_mirror_hash": None,
        "live_packet_stats": None,
        **git,
    }
    meta_path = run_dir / "run_metadata.json"
    _write_metadata(meta_path, run_meta)

    # ── CSV output ────────────────────────────────────────────────────────────
    csv_path = run_dir / "live_estimates.csv"
    _csv_fields = [
        "elapsed_s", "frame_idx", "locked_bin",
        "hr_bpm_raw", "hr_bpm_smooth", "hr_valid",
        "fallback_hr_bpm", "hr_confidence", "ahet_verified",
        "heart_peak_hz", "f_r_hz_used",
        "br_bpm", "br_confidence", "resp_valid", "spectrum_stage",
        "candidate_rejection_reason", "n_eca_skipped_harmonics",
    ]
    csv_fh = csv_path.open("w", newline="")
    csv_writer = csv.DictWriter(csv_fh, fieldnames=_csv_fields)
    csv_writer.writeheader()

    # ── Frame source ──────────────────────────────────────────────────────────
    frame_source: FrameSource
    dca = None
    iwr = None

    if mode == "replay":
        if args.replay_paths:
            paths = [Path(p) for p in args.replay_paths]
        else:
            raw_dir = _ROOT / "data" / "raw"
            paths = _resolve_replay_paths(session_id, raw_dir, shard_limit)
        print(f"Replay: {[str(p) for p in paths]}")
        frame_source = ReplayFrameSource(paths, chirp_cfg, fast=args.replay_fast)
        frame_source.start()
        run_meta["replay_files"] = [str(p) for p in paths]
        run_meta["replay_file_hashes"] = frame_source.file_hashes
        _write_metadata(meta_path, run_meta)

    else:
        # Live mode: import capture module only now
        sys.path.insert(0, str(_ROOT / "steps" / "step_1"))
        from capture import DCA1000, IWR1642  # type: ignore[import]

        net_cfg = cfg["network"]
        raw_mirror: Path | None = (
            run_dir / "adc_stream.bin"
            if cfg["capture"].get("record_raw_stream", True)
            else None
        )

        if not args.no_configure:
            uart_cfg = cfg["uart"]
            iwr = IWR1642(uart_cfg["port"], int(uart_cfg["baud"]))
            dca = DCA1000(net_cfg)

            print("[1/3] Configuring DCA1000 ...")
            dca.configure()

            hw_cfg = {"profile": cfg["hw_profile"], "frame": cfg["hw_frame"]}
            print("[2/3] Configuring IWR1642 ...")
            iwr.configure(n_frames=int(cfg["capture"]["continuous_num_frames"]), cfg=hw_cfg)

            print("[3/3] Starting capture ...")
            dca.start()
            iwr.start()

            frame_source = LiveFrameSource(
                pcfg,
                sock_dat=dca._sock_dat,
                zero_fill_leading_loss=True,
                raw_mirror_path=raw_mirror,
            )
        else:
            print("--no-configure: attaching to already-running stream.")
            frame_source = LiveFrameSource(
                pcfg,
                net_cfg=net_cfg,
                zero_fill_leading_loss=False,
                raw_mirror_path=raw_mirror,
            )

        frame_source.start()

    # ── Shared mutable state (captured by closures) ───────────────────────────
    ring_buffer: collections.deque = collections.deque(maxlen=ring_maxlen)
    hr_history: collections.deque = collections.deque(maxlen=smoother_n)
    _state = {
        "frames_since_dsp": 0,
        "last_frame_idx": 0,
        "emitted_first_window": False,
        "shutdown_done": False,
        "replay_ended": False,
        "locked_bin": locked_bin,
        "locked_bin_source": locked_bin_source,
        "warmup_pending": warmup_pending,
    }

    elapsed_s_hist: list = []
    hr_raw_hist: list = []
    hr_smooth_hist: list = []
    br_hist: list = []
    intermediates: list = []
    intermediates_path = run_dir / "live_intermediates.npz"

    t_start = time.monotonic()

    # ── Shutdown helper (idempotent) ──────────────────────────────────────────
    def _shutdown() -> None:
        if _state["shutdown_done"]:
            return
        _state["shutdown_done"] = True

        frame_source.stop()
        if mode == "live":
            if iwr:
                try:
                    iwr.stop()
                    iwr.close()
                except Exception:
                    pass
            if dca:
                try:
                    dca.stop()
                    dca.close()
                except Exception:
                    pass

        csv_fh.flush()
        csv_fh.close()
        _save_intermediates(intermediates_path, intermediates)

        run_meta["end_wall_utc"] = datetime.now(timezone.utc).isoformat()
        run_meta["completion_status"] = "completed"
        if mode == "live" and isinstance(frame_source, LiveFrameSource):
            run_meta["live_raw_mirror_hash"] = frame_source.mirror_sha256
            run_meta["live_packet_stats"] = {
                "n_received": frame_source.n_received,
                "n_dropped": frame_source.n_dropped,
                "zero_filled_bytes": frame_source.zero_filled_bytes,
                "mirror_truncated_bytes": frame_source.mirror_truncated_bytes,
            }
        _write_metadata(meta_path, run_meta)
        print(f"Artifacts: {run_dir}")

    # ── Safe figure close (must be called from within the animation callback) ──
    def _close_figure() -> None:
        # plt.close() calls FuncAnimation._stop() which sets event_source=None.
        # _close_figure() is called from _update(), which runs inside _step().
        # After _update() returns, _step() still executes:
        #   self.event_source.interval = self._interval   ← AttributeError on None
        # Stopping the timer here prevents the NEXT tick but does not prevent
        # the current _step() from finishing with event_source already None.
        # Fix: defer plt.close() via a short canvas timer so it runs in the
        # next event-loop iteration, after the current _step() has returned.
        if ani.event_source is not None:
            ani.event_source.stop()
        try:
            _t = fig.canvas.new_timer(interval=10)
            _t.add_callback(plt.close, "all")
            _t.start()
        except Exception:
            plt.close("all")  # fallback if the canvas is already gone

    def _request_close() -> None:
        if not args.headless:
            _close_figure()

    def _process_dsp_hop(
        elapsed: float,
        frame_idx: int,
        dsp_override: dict | None = None,
    ) -> None:
        if _state["locked_bin"] is None:
            raise RuntimeError("DSP hop called before warmup selection")

        try:
            dsp = (
                dsp_override
                if dsp_override is not None
                else run_window_dsp(ring_buffer, int(_state["locked_bin"]), fs, cfg)
            )
        except Exception as exc:
            print(f"DSP error (window skipped): {exc}", file=sys.stderr)
            return

        hr_valid = dsp["hr_valid"]
        hr_raw = dsp["hr_raw"]
        if np.isfinite(hr_raw):
            hr_history.append(hr_raw)

        hr_smooth = (
            float(np.median(list(hr_history))) if hr_history else np.nan
        )
        br_bpm = dsp["br_bpm"]
        hr_conf = "high" if (hr_valid and np.isfinite(hr_smooth)) else "low"
        br_conf = dsp["br_confidence"]

        elapsed_s_hist.append(elapsed)
        hr_raw_hist.append(hr_raw)
        hr_smooth_hist.append(hr_smooth)
        br_hist.append(br_bpm)

        # CSV
        hr_result = dsp["hr_result"]
        peak_hz = float(hr_result.get("peak_hz", np.nan))
        csv_writer.writerow({
            "elapsed_s": f"{elapsed:.2f}",
            "frame_idx": frame_idx,
            "locked_bin": _state["locked_bin"],
            "hr_bpm_raw": "" if not np.isfinite(hr_raw) else f"{hr_raw:.2f}",
            "hr_bpm_smooth": "" if not np.isfinite(hr_smooth) else f"{hr_smooth:.2f}",
            "hr_valid": int(hr_valid),
            "fallback_hr_bpm": (
                "" if not np.isfinite(dsp["fallback_hr_bpm"])
                else f"{dsp['fallback_hr_bpm']:.2f}"
            ),
            "hr_confidence": hr_conf,
            "ahet_verified": int(bool(hr_result.get("ahet_verified", False))),
            "heart_peak_hz": "" if not np.isfinite(peak_hz) else f"{peak_hz:.4f}",
            "f_r_hz_used": "" if dsp["f_r_hz"] is None else f"{dsp['f_r_hz']:.4f}",
            "br_bpm": "" if not np.isfinite(br_bpm) else f"{br_bpm:.2f}",
            "br_confidence": br_conf,
            "resp_valid": int(dsp["br_valid"]),
            "spectrum_stage": dsp["spectrum_stage"],
            "candidate_rejection_reason": dsp["rej_reason"],
            "n_eca_skipped_harmonics": dsp["n_eca_skipped"],
        })
        csv_fh.flush()

        # Intermediates
        inter = {
            "elapsed_s": elapsed,
            "frame_idx": frame_idx,
            "locked_bin": _state["locked_bin"],
            "phase_raw": dsp["phase_raw"],
            "phase_clean": dsp["phase_clean"],
            # Respiration scalar outputs
            "fft_rr_bpm": dsp["fft_r"].get("fft_rr_bpm", np.nan),
            "ha_rr_bpm": dsp["ha_r"].get("ha_rr_bpm", np.nan),
            "stft_rr_bpm": dsp["stft_r"].get("stft_rr_bpm", np.nan),
            # Respiration frequency/spectrum arrays
            "resp_freqs_hz": dsp["fft_r"].get("freqs_hz", np.array([])),
            "resp_spectrum": dsp["fft_r"].get("spectrum", np.array([])),
            "resp_ha_freqs_hz": dsp["ha_r"].get("freqs_hz", np.array([])),
            "resp_ha_spectrum": dsp["ha_r"].get("spectrum", np.array([])),
            # M2 respiration decision evidence (plans/m2_respiration_fix.md §4.3) — a
            # field that exists only in an in-memory result dict is not evidence; every
            # picker/veto decision is persisted per window.
            "fft_band_argmax_bin": int(dsp["fft_r"].get("fft_band_argmax_bin", -1)),
            "fft_band_argmax_is_local_max": bool(
                dsp["fft_r"].get("fft_band_argmax_is_local_max", False)
            ),
            "fft_selected_bin": int(dsp["fft_r"].get("fft_selected_bin", -1)),
            "ha_candidate_freqs_hz": dsp["ha_r"].get(
                "ha_candidate_freqs_hz", np.array([])
            ),
            "ha_candidate_scores": dsp["ha_r"].get(
                "ha_candidate_scores", np.array([])
            ),
            "ha_fund_is_local_max": dsp["ha_r"].get(
                "ha_fund_is_local_max", np.array([], dtype=bool)
            ),
            "ha_selected_bin": int(dsp["ha_r"].get("ha_selected_bin", -1)),
            "stft_rr_std_bpm": dsp["stft_r"].get("stft_rr_std_bpm", np.nan),
            "stft_valid_fraction": dsp["stft_r"].get("stft_valid_fraction", np.nan),
            "resp_edge_veto": bool(dsp["br_result"].get("resp_edge_veto", False)),
            "resp_edge_veto_reason": str(
                dsp["br_result"].get("resp_edge_veto_reason", "")
            ),
            "resp_fusion_branch": str(
                dsp["br_result"].get("resp_fusion_branch", "")
            ),
            "resp_valid": bool(dsp["br_valid"]),
            # Heart spectra
            "heart_freqs_hz": hr_result.get("freqs_hz", np.array([])),
            "heart_spectrum_pre_eca": hr_result.get("spectrum_pre_eca", np.array([])),
            "heart_spectrum_first_pass": hr_result.get("spectrum_first_pass", np.array([])),
            "heart_spectrum": hr_result.get("spectrum", np.array([])),
            "spectrum_stage": dsp["spectrum_stage"],
            # Diagnostic no-ECA baseline
            "baseline_freqs_hz": dsp["baseline_freqs_hz"],
            "baseline_spectrum": dsp["baseline_spectrum"],
            # HR/BR estimates
            "hr_raw": hr_raw,
            "hr_smooth": hr_smooth,
            "fallback_hr_bpm": dsp["fallback_hr_bpm"],
            "br_bpm": br_bpm,
            "f_r_hz": dsp["f_r_hz"] if dsp["f_r_hz"] is not None else np.nan,
            # AHET candidate evidence
            "candidate_rejection_codes": hr_result.get(
                "candidate_rejection_code", np.array([])
            ),
            "accepted_candidate_rank": int(
                hr_result.get("accepted_candidate_rank", -1)
            ),
            "candidate_initial_hz": hr_result.get(
                "candidate_initial_hz", np.array([])
            ),
            "candidate_refined_hz": hr_result.get(
                "candidate_refined_hz", np.array([])
            ),
            "second_peak_refined_hz": hr_result.get(
                "second_peak_refined_hz", np.array([])
            ),
            "peak_to_floor_ratio_db": hr_result.get(
                "peak_to_floor_ratio_db", np.array([])
            ),
            "n_eca_skipped": dsp["n_eca_skipped"],
            "eca_skipped_harmonics": dsp["eca_skipped_harmonics"],
            "k_max_eff": dsp["k_max_eff"],
            "n_eca_projected": dsp["n_eca_projected"],
            "n_eca_cols_retained": dsp["n_eca_cols_retained"],
            "n_eca_cols_dropped": dsp["n_eca_cols_dropped"],
        }
        intermediates.append(inter)

        if len(intermediates) % checkpoint_n == 0:
            _save_intermediates(intermediates_path, intermediates)

        display.update(
            elapsed_s_hist, hr_smooth_hist, hr_raw_hist, br_hist,
            hr_conf, br_conf, elapsed, hr_smooth, br_bpm,
            _state["locked_bin"],
            (
                None if _state["locked_bin"] is None
                else int(_state["locked_bin"]) * float(pcfg["range_resolution_m"])
            ),
        )

    # ── FuncAnimation update ──────────────────────────────────────────────────
    def _update(_frame_num) -> None:
        elapsed = time.monotonic() - t_start

        # Duration limit: shut down before processing another callback batch.
        if duration_s and elapsed >= duration_s:
            _shutdown()
            _request_close()
            return

        # Drain all available frames from source. Emit estimates as each hop
        # boundary is reached so replay backlog cannot reuse the same final buffer.
        while True:
            result = frame_source.get_frame(timeout_s=0.0)
            if result is None:
                break
            if result is _REPLAY_END:
                _state["replay_ended"] = True
                break
            fidx, cube = result
            ring_buffer.append(cube)
            _state["last_frame_idx"] = fidx

            if len(ring_buffer) < ring_maxlen:
                continue

            if not _state["emitted_first_window"]:
                _state["emitted_first_window"] = True
                _state["frames_since_dsp"] = 0

                dsp_override = None
                if _state["warmup_pending"]:
                    first_window = np.stack(list(ring_buffer))
                    candidate_bins = derive_candidate_bins(cfg)
                    selected_bin, dsp_override, evidence = run_warmup_selection(
                        first_window, candidate_bins, cfg, fs
                    )

                    _state["locked_bin"] = selected_bin
                    _state["locked_bin_source"] = "warmup_auto"
                    _state["warmup_pending"] = False

                    with warmup_selection_path.open("w", encoding="utf-8") as fh:
                        json.dump(evidence, fh, indent=2)

                    run_meta["locked_bin"] = selected_bin
                    run_meta["locked_bin_source"] = "warmup_auto"
                    run_meta["locked_bin_overridden"] = False
                    run_meta["warmup_selected_bin"] = selected_bin
                    run_meta["warmup_selection_confidence"] = evidence[
                        "selected_confidence"
                    ]
                    run_meta["warmup_selection_path"] = str(warmup_selection_path)
                    run_meta["t_warmup_scan_ms"] = evidence["t_warmup_scan_ms"]
                    _write_metadata(meta_path, run_meta)

                    print(
                        f"Warmup selected bin {selected_bin} "
                        f"(~{evidence['selected_range_m']:.2f} m, "
                        f"confidence: {evidence['selected_confidence']})."
                    )
                    print(
                        "To lock this for future runs, add "
                        f"locked_bin={selected_bin} to the manifest row for "
                        f"session '{session_id}'."
                    )

                    if dsp_override is None:
                        continue

                _process_dsp_hop(elapsed, fidx, dsp_override=dsp_override)
                continue

            _state["frames_since_dsp"] += 1
            while _state["frames_since_dsp"] >= frames_per_hop:
                _state["frames_since_dsp"] -= frames_per_hop
                _process_dsp_hop(elapsed, fidx)

        # Warmup
        n_buf = len(ring_buffer)
        if n_buf < ring_maxlen:
            if _state["replay_ended"]:
                _shutdown()
                _request_close()
            else:
                display.warmup(n_buf, ring_maxlen, elapsed)
            return

        # All pending DSP hops processed; now safe to shut down for replay end
        if _state["replay_ended"]:
            _shutdown()
            _request_close()

    # ── Display + animation ───────────────────────────────────────────────────
    if args.headless:
        display = _HeadlessDisplay()
        try:
            while not _state["shutdown_done"]:
                _update(None)
                if not _state["shutdown_done"]:
                    time.sleep(0.05)
        except KeyboardInterrupt:
            pass
        finally:
            _shutdown()
    else:
        display = _LiveDisplay(display_history_s)
        fig = display.setup()
        fig.canvas.mpl_connect("close_event", lambda _e: _shutdown())

        ani = animation.FuncAnimation(  # noqa: F841 — must be held alive
            fig, _update, interval=1000, cache_frame_data=False
        )

        try:
            plt.show()
        except KeyboardInterrupt:
            pass
        finally:
            _shutdown()


if __name__ == "__main__":
    main()
