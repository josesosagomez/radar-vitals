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
See notes/live_demo_plan.md for architecture details.
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
from scipy.fft import fft as sp_fft

# ── sys.path so src/ imports work regardless of cwd ──────────────────────────
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.radar_io import ChirpConfig, read_adc_bin
from src.respiration import (
    extract_chest_phase,
    fft_estimate_rr,
    fuse_estimates,
    ha_estimate_rr,
    stft_stability,
)
from src.vitals import estimate_rate_from_phase, remove_impulse_noise

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


# ── Per-hop DSP ───────────────────────────────────────────────────────────────

_REJ_CODE_NAMES = {
    -1: "", 0: "passed",
    1: "no_second_harmonic_region", 2: "ratio_db_low",
    3: "prominence_low", 4: "low_candidate_competitor",
    5: "not_attempted", 6: "peak_to_floor_db_low",
    7: "low_candidate_floor_db_low",
}


def _run_dsp(ring_buffer: collections.deque, locked_bin: int, fs: float, cfg: dict) -> dict:
    cube = np.stack(list(ring_buffer))   # (window_frames, chirps, rx, adc)

    phase_raw = extract_chest_phase(
        cube,
        locked_bin=locked_bin,
        method=cfg["phase"]["method"],
    )
    phase_clean = remove_impulse_noise(
        phase_raw,
        thresh=float(cfg["phase"]["impulse_clip_rad"]),
    )

    resp_cfg = cfg["respiration"]
    band_hz = tuple(resp_cfg["band_hz"])

    fft_r = fft_estimate_rr(
        phase_clean, fs, band_hz,
        detrend_type=resp_cfg["detrend"],
    )
    ha_r = ha_estimate_rr(
        phase_clean, fs, band_hz,
        max_harmonics=resp_cfg["max_harmonics"],
        harmonic_max_hz=resp_cfg["harmonic_max_hz"],
        detrend_type=resp_cfg["detrend"],
    )
    stft_r = stft_stability(
        phase_clean, fs, band_hz,
        subwindow_s=float(resp_cfg["stft_subwindow_s"]),
        overlap=float(resp_cfg["stft_overlap"]),
        detrend_type=resp_cfg["detrend"],
    )
    br_result = fuse_estimates(fft_r, ha_r, stft_r, resp_cfg)

    # Low-confidence BR invalidated before ECA — do not feed a bad f_r to AHET
    if br_result.get("resp_confidence") == "low":
        br_result = dict(br_result)
        br_result["resp_valid"] = False
    f_r_hz: float | None = (
        float(br_result["resp_peak_hz"]) if br_result["resp_valid"] else None
    )

    hcfg = cfg["heart"]
    hr_result = estimate_rate_from_phase(
        phase_clean,
        fs,
        tuple(hcfg["band_hz"]),
        f_r_hz=f_r_hz,
        k_max=int(hcfg["k_max"]),
        ahet_deviation_hz=float(hcfg["ahet_deviation_hz"]),
        eca_mode=hcfg["eca_mode"],
        ahet_gate_mode=hcfg["ahet_gate_mode"],
        eca_forbidden_guard_hz=float(hcfg["eca_forbidden_guard_hz"]),
        candidate_min_second_harmonic_ratio_db=float(
            hcfg["candidate_min_second_harmonic_ratio_db"]
        ),
        candidate_min_prominence=float(hcfg["candidate_min_prominence"]),
        low_candidate_hz=float(hcfg["low_candidate_hz"]),
        high_candidate_preference_hz=float(hcfg["high_candidate_preference_hz"]),
        high_competitor_min_mag_ratio=float(hcfg["high_competitor_min_mag_ratio"]),
        candidate_min_peak_to_floor_db=float(hcfg["candidate_min_peak_to_floor_db"]),
        low_candidate_min_peak_to_floor_db=float(
            hcfg["low_candidate_min_peak_to_floor_db"]
        ),
    )

    # Diagnostic-only no-ECA baseline — never shown as a confident estimate
    baseline = estimate_rate_from_phase(
        phase_clean, fs, tuple(hcfg["band_hz"])
    )
    fallback_hr_bpm = float(baseline.get("rate_bpm", np.nan))
    baseline_spectrum = baseline.get("spectrum", np.array([]))
    baseline_freqs_hz = baseline.get("freqs_hz", np.array([]))

    hr_valid = bool(hr_result.get("ahet_verified", False))
    hr_raw = float(hr_result["rate_bpm"]) if hr_valid else np.nan

    rej_codes = hr_result.get("candidate_rejection_code", np.array([-1, -1, -1]))
    eca_skip = hr_result.get(
        "eca_skipped_harmonics", np.zeros(int(hcfg["k_max"]), dtype=bool)
    )

    accepted_rank = int(hr_result.get("accepted_candidate_rank", -1))
    if hr_valid and 0 <= accepted_rank < len(rej_codes):
        summary_code = int(rej_codes[accepted_rank])
    else:
        summary_code = int(rej_codes[0]) if len(rej_codes) > 0 else -1
    rej_reason = _REJ_CODE_NAMES.get(summary_code, str(summary_code))

    return {
        "hr_valid": hr_valid,
        "hr_raw": hr_raw,
        "fallback_hr_bpm": fallback_hr_bpm,
        "baseline_spectrum": baseline_spectrum,
        "baseline_freqs_hz": baseline_freqs_hz,
        "hr_result": hr_result,
        "br_result": br_result,
        "br_bpm": float(br_result.get("radar_rr_bpm", np.nan)),
        "br_confidence": br_result.get("resp_confidence", "low"),
        "br_valid": bool(br_result.get("resp_valid", False)),
        "f_r_hz": f_r_hz,
        "spectrum_stage": int(hr_result.get("spectrum_stage", 0)),
        "rej_reason": rej_reason,
        "n_eca_skipped": int(np.sum(eca_skip)),
        # Intermediates for NPZ
        "phase_raw": phase_raw,
        "phase_clean": phase_clean,
        "fft_r": fft_r,
        "ha_r": ha_r,
        "stft_r": stft_r,
    }


# ── Warmup bin-selection helpers ──────────────────────────────────────────────

def _derive_candidate_bins(cfg: dict) -> list[int]:
    """Candidate range bins from protocol distance + range resolution, or explicit list."""
    bsel = cfg.get("bin_selection", {})
    explicit = bsel.get("candidate_bins")
    if explicit is not None:
        return [int(b) for b in explicit]
    dist_range = cfg["protocol"]["subject_distance_m"]
    res = float(cfg["profile"]["range_resolution_m"])
    n_adc = int(cfg["profile"]["num_adc_samples"])
    lo = int(np.ceil(float(dist_range[0]) / res))
    hi = int(np.floor(float(dist_range[1]) / res))
    return list(range(max(0, lo), min(n_adc - 1, hi) + 1))


def _range_energy_by_bin(
    cube: np.ndarray, candidate_bins: list[int]
) -> dict[int, float]:
    """Mean power per range bin — Hann window + sp_fft, same as extract_chest_phase."""
    n_adc = cube.shape[3]
    hann_win = np.hanning(n_adc).astype(np.float32)
    windowed = cube * hann_win                  # broadcast over last dim
    range_fft = sp_fft(windowed, axis=3)
    return {
        b: float(np.mean(np.abs(range_fft[:, :, :, b]) ** 2))
        for b in candidate_bins
    }


def _resolve_locked_bin(
    args_locked_bin: int | None,
    manifest_bin: int | None,
    bin_selection_enabled: bool,
) -> tuple[int | None, str | None, bool]:
    """Return (locked_bin_or_none, source_or_none, warmup_pending).

    (None, None, False) is the error sentinel — caller must call sys.exit().
    """
    if args_locked_bin is not None:
        return args_locked_bin, "manual", False
    if manifest_bin is not None:
        return manifest_bin, "manifest", False
    if bin_selection_enabled:
        return None, "warmup_auto", True
    return None, None, False


def _run_bin_selection_scan(
    cube: np.ndarray,
    candidate_bins: list[int],
    cfg: dict,
    fs: float,
    dsp_fn=_run_dsp,
    context_label: str = "warmup",
    artifact_hint: str = "warmup_bin_selection.json",
) -> tuple[int, dict | None, dict]:
    """Scan candidate bins, score by radar evidence, return the best bin.

    Shared by initial warmup selection and mid-run relock recovery scans;
    `context_label`/`artifact_hint` keep console warnings and artifact
    references distinct between the two contexts.

    Returns (selected_bin, winning_dsp_dict_or_none, evidence_for_json).
    winning_dsp_dict is None when every candidate's DSP call raised (all-fail
    case); the caller must not emit a row from synthetic DSP data.
    """
    res = float(cfg["profile"]["range_resolution_m"])
    dist_range = cfg["protocol"]["subject_distance_m"]
    center_m = (float(dist_range[0]) + float(dist_range[1])) / 2.0

    t0 = time.monotonic()

    energies = _range_energy_by_bin(cube, candidate_bins)
    sorted_by_energy = sorted(candidate_bins, key=lambda b: energies[b], reverse=True)
    energy_rank = {b: i + 1 for i, b in enumerate(sorted_by_energy)}

    results: list[dict] = []
    for b in candidate_bins:
        try:
            dsp = dsp_fn(cube, b, fs, cfg)
            results.append({
                "bin": b, "dsp": dsp,
                "energy": energies[b], "energy_rank": energy_rank[b],
                "failed": False, "error": None,
            })
        except Exception as exc:
            print(
                f"  WARNING: {context_label} DSP failed for bin {b}: {exc}",
                file=sys.stderr,
            )
            results.append({
                "bin": b, "dsp": None,
                "energy": energies[b], "energy_rank": energy_rank[b],
                "failed": True, "error": str(exc),
            })

    t_scan_ms = (time.monotonic() - t0) * 1000.0
    good = [r for r in results if not r["failed"]]

    if not good:
        fallback = min(results, key=lambda r: r["energy_rank"])
        selected_bin = fallback["bin"]
        winning_dsp = None
        selection_confidence = "low"
        selection_reason = "all_dsp_failed_energy_fallback"
        print(
            f"  WARNING: {context_label} DSP failed for every candidate. "
            f"Falling back to highest-energy bin {selected_bin}; no emitted "
            f"estimate from this scan.",
            file=sys.stderr,
        )
    else:
        def _br_conf_order(conf: str) -> int:
            return {"high": 0, "medium": 1, "low": 2}.get(conf, 3)

        for r in good:
            dsp = r["dsp"]
            score = 0
            if dsp["hr_valid"]:
                score += 1000
            br_conf = dsp["br_confidence"]
            if br_conf == "high":
                score += 250
            elif br_conf == "medium":
                score += 100
            else:
                score -= 100
            if dsp["br_valid"]:
                score += 50
            score -= 5 * r["energy_rank"]
            r["score"] = score

        good.sort(key=lambda r: (
            -r["score"],
            int(not r["dsp"]["hr_valid"]),
            _br_conf_order(r["dsp"]["br_confidence"]),
            int(not r["dsp"]["br_valid"]),
            r["energy_rank"],
            abs(r["bin"] * res - center_m),
            r["bin"],
        ))
        winner = good[0]
        selected_bin = winner["bin"]
        winning_dsp = winner["dsp"]

        if winning_dsp["hr_valid"] and winning_dsp["br_valid"]:
            selection_confidence = "high"
        elif winning_dsp["br_valid"] and winning_dsp["br_confidence"] in ("high", "medium"):
            selection_confidence = "medium"
        else:
            selection_confidence = "low"

        selection_reason = (
            f"score={winner['score']}"
            f"_hr={int(winning_dsp['hr_valid'])}"
            f"_br={winning_dsp['br_confidence']}"
        )

    if selection_confidence == "low":
        print(
            f"  WARNING: {context_label} selection confidence is low for bin "
            f"{selected_bin} (~{selected_bin * res:.2f} m). Check {artifact_hint}.",
            file=sys.stderr,
        )

    evidence: dict = {
        "selected_bin": int(selected_bin),
        "selected_range_m": round(selected_bin * res, 4),
        "selected_confidence": selection_confidence,
        "selection_reason": selection_reason,
        "t_scan_ms": round(t_scan_ms, 1),
        "candidates": [],
    }
    for r in results:
        cand: dict = {
            "bin": r["bin"],
            "range_m": round(r["bin"] * res, 4),
            "energy": r["energy"],
            "energy_rank": r["energy_rank"],
            "failed": r["failed"],
            "error": r["error"],
        }
        if not r["failed"]:
            dsp = r["dsp"]
            cand.update({
                "score": r.get("score"),
                "hr_valid": bool(dsp["hr_valid"]),
                "hr_raw": (
                    float(dsp["hr_raw"]) if np.isfinite(dsp["hr_raw"]) else None
                ),
                "fallback_hr_bpm": (
                    float(dsp["fallback_hr_bpm"])
                    if np.isfinite(dsp["fallback_hr_bpm"])
                    else None
                ),
                "br_bpm": (
                    float(dsp["br_bpm"]) if np.isfinite(dsp["br_bpm"]) else None
                ),
                "br_confidence": dsp["br_confidence"],
                "resp_valid": bool(dsp["br_valid"]),
                "f_r_hz": (
                    None if dsp.get("f_r_hz") is None else float(dsp["f_r_hz"])
                ),
                "spectrum_stage": int(dsp["spectrum_stage"]),
                "rej_reason": dsp["rej_reason"],
                "n_eca_skipped": int(dsp["n_eca_skipped"]),
                "accepted_candidate_rank": int(
                    dsp["hr_result"].get("accepted_candidate_rank", -1)
                ),
            })
        evidence["candidates"].append(cand)

    return selected_bin, winning_dsp, evidence


def _run_warmup_selection(
    cube: np.ndarray,
    candidate_bins: list[int],
    cfg: dict,
    fs: float,
    dsp_fn=_run_dsp,
) -> tuple[int, dict | None, dict]:
    """Initial warmup selection — backward-compatible wrapper for the shared scan."""
    selected_bin, winning_dsp, evidence = _run_bin_selection_scan(
        cube, candidate_bins, cfg, fs, dsp_fn=dsp_fn,
        context_label="warmup", artifact_hint="warmup_bin_selection.json",
    )
    evidence["t_warmup_scan_ms"] = evidence.pop("t_scan_ms")
    return selected_bin, winning_dsp, evidence


# ── Display holdover + relock state (notes/relocking_bin_plan.md) ────────────

class _DisplayHoldoverState:
    """Display-only holdover for the HR/BR UI readouts.

    Saved CSV/NPZ estimates are never affected. On an invalid hop the UI may
    briefly show the average of recent real-valid readings ("held"); once the
    holdover window expires the readout goes blank until a new real-valid
    value appears. All expiry logic is DSP-hop-count based, never wall-clock.
    """

    _METRICS = ("hr", "br")

    def __init__(
        self,
        holdover_n: int,
        holdover_hops: int,
        source_max_hops: int,
        enabled: bool = True,
    ):
        self._n = int(holdover_n)
        self._holdover_hops = int(holdover_hops)
        self._source_max_hops = int(source_max_hops)
        self._enabled = bool(enabled)
        self._readings: dict[str, collections.deque] = {}
        self._last_valid_hop: dict[str, int | None] = {}
        self.reset_all()

    def reset_metric(self, metric: str) -> None:
        self._readings[metric] = collections.deque(maxlen=self._n)
        self._last_valid_hop[metric] = None

    def reset_all(self) -> None:
        for metric in self._METRICS:
            self.reset_metric(metric)

    def update(
        self, metric: str, value: float, valid: bool, hop_idx: int
    ) -> tuple[float, str]:
        """Return (display_value, display_state) for this hop.

        display_state is "real" (current valid reading), "held" (display-only
        average of recent valid readings), or "blank" (show nothing).
        """
        if valid and np.isfinite(value):
            self._readings[metric].append((int(hop_idx), float(value)))
            self._last_valid_hop[metric] = int(hop_idx)
            return float(value), "real"

        last_valid = self._last_valid_hop[metric]
        if (
            not self._enabled
            or last_valid is None
            or hop_idx - last_valid > self._holdover_hops
        ):
            return float("nan"), "blank"

        eligible = [
            v for h, v in self._readings[metric]
            if hop_idx - h <= self._source_max_hops
        ]
        if not eligible:
            return float("nan"), "blank"
        return float(np.mean(eligible)), "held"


class _RelockController:
    """Arm/disarm state machine deciding when a nearby-bin relock scan runs.

    A metric arms after `arm_hops` consecutive real-valid hops. Any
    real-invalid hop resets that consecutive-valid counter, but does NOT clear
    an already-set armed flag — the armed flag survives the holdover window so
    the blank/expired hop can trigger one scan. Holdover expiry is supplied by
    _DisplayHoldoverState via the blank_expired arguments; this class must not
    keep a second independent holdover counter.

    The caller runs the scan when update() returns should_scan=True, then
    calls disarm_all() after every scan attempt (reset() instead, after an
    accepted bin switch).
    """

    _METRICS = ("hr", "br")

    def __init__(self, arm_hops: int):
        self._arm_hops = int(arm_hops)
        self._consec_valid: dict[str, int] = {}
        self._armed: dict[str, bool] = {}
        self.reset()

    def disarm_all(self) -> None:
        for metric in self._METRICS:
            self._consec_valid[metric] = 0
            self._armed[metric] = False

    def reset(self) -> None:
        self.disarm_all()

    def update(
        self,
        hr_valid: bool,
        br_valid: bool,
        hr_blank_expired: bool,
        br_blank_expired: bool,
        hop_idx: int,
    ) -> tuple[bool, list[str]]:
        del hop_idx  # cadence is implicit: exactly one update() per DSP hop
        triggers: list[str] = []
        per_metric = (
            ("hr", hr_valid, hr_blank_expired),
            ("br", br_valid, br_blank_expired),
        )
        for metric, valid, blank_expired in per_metric:
            if valid:
                self._consec_valid[metric] += 1
                if self._consec_valid[metric] >= self._arm_hops:
                    self._armed[metric] = True
            else:
                self._consec_valid[metric] = 0
                if self._armed[metric] and blank_expired:
                    triggers.append(metric)
        return bool(triggers), triggers


def _derive_relock_candidate_bins(locked_bin: int, cfg: dict, radius: int) -> list[int]:
    """Nearby-bin recovery candidates: locked_bin ± radius, clipped.

    ADC bounds always apply. The protocol distance range applies only when
    bin_selection.candidate_bins is not explicitly configured. The current
    locked bin is always included even if it sits outside the protocol range
    (possible for manual overrides with relocking force-enabled).
    """
    n_adc = int(cfg["profile"]["num_adc_samples"])
    lo = max(0, int(locked_bin) - int(radius))
    hi = min(n_adc - 1, int(locked_bin) + int(radius))
    bins = list(range(lo, hi + 1))
    if cfg.get("bin_selection", {}).get("candidate_bins") is None:
        dist_range = cfg["protocol"]["subject_distance_m"]
        res = float(cfg["profile"]["range_resolution_m"])
        proto_lo = int(np.ceil(float(dist_range[0]) / res))
        proto_hi = int(np.floor(float(dist_range[1]) / res))
        bins = [b for b in bins if proto_lo <= b <= proto_hi]
    if int(locked_bin) not in bins and 0 <= int(locked_bin) <= n_adc - 1:
        bins.append(int(locked_bin))
        bins.sort()
    return bins


def _relock_allowed(locked_bin_source: str | None, cfg: dict) -> bool:
    """Pinned sources (manual/manifest) do not auto-relock unless opted in."""
    bsel = cfg.get("bin_selection", {})
    if not bool(bsel.get("relock_enabled", False)):
        return False
    if locked_bin_source in ("manual", "manifest") and not bool(
        bsel.get("relock_pinned_sources_enabled", False)
    ):
        return False
    return True


# ── Display ───────────────────────────────────────────────────────────────────

class _LiveDisplay:
    _CONF_COLOR = {"high": "#00cc66", "medium": "orange", "low": "#cc3333"}

    def __init__(self, history_s: float):
        self._hist_s = history_s
        self._fig = None
        self._ax_hr = None
        self._ax_br = None
        self._line_hr_s = None
        self._line_hr_r = None
        self._line_br = None

    def setup(self):
        from matplotlib import pyplot as plt

        fig, (ax_hr, ax_br) = plt.subplots(2, 1, figsize=(11, 6), sharex=True)
        fig.patch.set_facecolor("#111")
        for ax in (ax_hr, ax_br):
            ax.set_facecolor("#1a1a1a")
            ax.tick_params(colors="#ccc")
            for sp in ax.spines.values():
                sp.set_edgecolor("#444")

        (self._line_hr_s,) = ax_hr.plot(
            [], [], color="#00cc66", lw=2, label="HR smooth (AHET-verified)"
        )
        (self._line_hr_r,) = ax_hr.plot(
            [], [], ".", color="#00cc66", alpha=0.35, ms=4, label="HR raw"
        )
        ax_hr.set_ylim(40, 160)
        ax_hr.set_ylabel("Heart rate (bpm)", color="#ccc")
        ax_hr.legend(loc="upper right", fontsize=8, facecolor="#333", labelcolor="#ccc")

        (self._line_br,) = ax_br.plot([], [], color="#4499ff", lw=2)
        ax_br.set_ylim(6, 30)
        ax_br.set_ylabel("Breathing rate (bpm)", color="#ccc")
        ax_br.set_xlabel("Elapsed (s)", color="#ccc")

        fig.subplots_adjust(left=0.08, right=0.98, bottom=0.10, top=0.88, hspace=0.18)
        self._fig, self._ax_hr, self._ax_br = fig, ax_hr, ax_br
        return fig

    def warmup(self, n_buf: int, n_needed: int, elapsed: float) -> None:
        self._fig.suptitle(
            f"Warming up: {n_buf}/{n_needed} frames  ({elapsed:.0f} s)",
            color="#ccc", fontsize=12,
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
        hr_state: str = "real",
        br_state: str = "real",
        status_text: str | None = None,
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

        hr_str = f"{cur_hr:.0f} bpm" if np.isfinite(cur_hr) else "--"
        if hr_state == "held" and np.isfinite(cur_hr):
            hr_str += " (held)"
        br_str = f"{cur_br:.0f} bpm" if np.isfinite(cur_br) else "--"
        if br_state == "held" and np.isfinite(cur_br):
            br_str += " (held)"
        elapsed_fmt = time.strftime("%H:%M:%S", time.gmtime(int(elapsed)))
        bin_str = ""
        if locked_bin is not None and locked_range_m is not None:
            bin_str = f"  |  bin: {locked_bin} (~{locked_range_m:.2f} m)"
        status_str = f"  |  {status_text}" if status_text else ""

        self._fig.suptitle(
            f"HR: {hr_str}  |  BR: {br_str}  |  "
            f"HR conf: {hr_conf}  |  BR conf: {br_conf}  |  "
            f"elapsed: {elapsed_fmt}{bin_str}{status_str}",
            color="#eee", fontsize=16.5,
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
        hr_state: str = "real",
        br_state: str = "real",
        status_text: str | None = None,
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

    delay_s = 15
    
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
    locked_bin, locked_bin_source, warmup_pending = _resolve_locked_bin(
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

    # Display holdover + relock timing: hop-count based, never wall-clock
    # (notes/relocking_bin_plan.md — live/replay/--replay-fast must behave the same).
    dcfg = cfg.get("display", {})
    holdover_hops = max(1, int(np.ceil(float(dcfg.get("holdover_s", 3.0)) / hop_s)))
    display_holdover = _DisplayHoldoverState(
        holdover_n=int(dcfg.get("holdover_n", 3)),
        holdover_hops=holdover_hops,
        source_max_hops=int(dcfg.get("holdover_source_max_hops", 5)),
        enabled=bool(dcfg.get("holdover_enabled", True)),
    )
    bsel_cfg = cfg.get("bin_selection", {})
    relock_arm_hops = max(
        1, int(np.ceil(float(bsel_cfg.get("relock_arm_s", 5.0)) / hop_s))
    )
    relock_radius = int(bsel_cfg.get("relock_radius_bins", 2))
    relock_min_confidence = str(bsel_cfg.get("relock_min_confidence", "medium"))
    relock_ctrl = _RelockController(arm_hops=relock_arm_hops)

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
    relock_events_path = run_dir / "relock_events.json"
    relock_events: list[dict] = []

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
        "relock_enabled": _relock_allowed(locked_bin_source, cfg),
        "n_relock_scans": 0,
        "n_relock_switches": 0,
        "relock_events_path": None,
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
        "dsp_hop_idx": 0,
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
                else _run_dsp(ring_buffer, int(_state["locked_bin"]), fs, cfg)
            )
        except Exception as exc:
            print(f"DSP error (window skipped): {exc}", file=sys.stderr)
            return

        hop_idx = _state["dsp_hop_idx"]
        _state["dsp_hop_idx"] += 1

        def _derive_hop_values(d: dict) -> tuple[bool, float, float, float]:
            hr_valid_ = bool(d["hr_valid"])
            hr_raw_ = d["hr_raw"]
            if np.isfinite(hr_raw_):
                hr_history.append(hr_raw_)
                hr_smooth_ = float(np.median(list(hr_history)))
            else:
                # Real smoothed HR is NaN on invalid hops; only the display-side
                # holdover may bridge short gaps (never CSV/NPZ).
                hr_smooth_ = np.nan
            return hr_valid_, hr_raw_, hr_smooth_, d["br_bpm"]

        hr_valid, hr_raw, hr_smooth, br_bpm = _derive_hop_values(dsp)
        hr_disp, hr_disp_state = display_holdover.update(
            "hr", hr_smooth, hr_valid, hop_idx
        )
        br_disp, br_disp_state = display_holdover.update(
            "br", br_bpm, bool(dsp["br_valid"]), hop_idx
        )

        status_text = None
        if _relock_allowed(_state["locked_bin_source"], cfg):
            should_scan, trigger_metrics = relock_ctrl.update(
                hr_valid,
                bool(dsp["br_valid"]),
                hr_disp_state == "blank",
                br_disp_state == "blank",
                hop_idx,
            )
            if should_scan:
                old_bin = int(_state["locked_bin"])
                cand_bins = _derive_relock_candidate_bins(old_bin, cfg, relock_radius)
                print(
                    f"Relock scan (trigger: {'+'.join(trigger_metrics)}) over "
                    f"bins {min(cand_bins)}-{max(cand_bins)} ..."
                )
                sel_bin, win_dsp, evidence = _run_bin_selection_scan(
                    np.stack(list(ring_buffer)), cand_bins, cfg, fs,
                    context_label="relock", artifact_hint="relock_events.json",
                )
                t_relock_scan_ms = evidence.pop("t_scan_ms")
                conf_rank = {"high": 0, "medium": 1, "low": 2}
                conf_ok = (
                    conf_rank.get(evidence["selected_confidence"], 3)
                    <= conf_rank.get(relock_min_confidence, 1)
                )
                switched = (
                    int(sel_bin) != old_bin and conf_ok and win_dsp is not None
                )
                if switched:
                    reason = (
                        f"switched: confidence "
                        f"{evidence['selected_confidence']} >= {relock_min_confidence}"
                    )
                elif int(sel_bin) == old_bin:
                    reason = "no_switch: current bin still scores best"
                elif win_dsp is None:
                    reason = "no_switch: all candidate DSP failed"
                else:
                    reason = (
                        f"no_switch: confidence {evidence['selected_confidence']} "
                        f"below {relock_min_confidence}"
                    )
                relock_events.append({
                    "elapsed_s": round(elapsed, 2),
                    "dsp_hop_idx": hop_idx,
                    "old_locked_bin": old_bin,
                    "candidate_bins": cand_bins,
                    "selected_bin": int(sel_bin),
                    "selected_confidence": evidence["selected_confidence"],
                    "switched": switched,
                    "reason": reason,
                    "trigger_metrics": trigger_metrics,
                    "t_relock_scan_ms": t_relock_scan_ms,
                    "evidence": evidence,
                })
                with relock_events_path.open("w", encoding="utf-8") as fh:
                    json.dump(relock_events, fh, indent=2, default=_json_serialise)
                run_meta["n_relock_scans"] += 1
                run_meta["relock_events_path"] = str(relock_events_path)
                if switched:
                    run_meta["n_relock_switches"] += 1
                    run_meta["locked_bin"] = int(sel_bin)
                    _state["locked_bin"] = int(sel_bin)
                    # New physical bin: old-bin smoothing/holdover history is
                    # meaningless — clear everything before emitting this row.
                    hr_history.clear()
                    display_holdover.reset_all()
                    relock_ctrl.reset()
                    dsp = win_dsp
                    hr_valid, hr_raw, hr_smooth, br_bpm = _derive_hop_values(dsp)
                    hr_disp, hr_disp_state = display_holdover.update(
                        "hr", hr_smooth, hr_valid, hop_idx
                    )
                    br_disp, br_disp_state = display_holdover.update(
                        "br", br_bpm, bool(dsp["br_valid"]), hop_idx
                    )
                    status_text = f"relocked bin {old_bin} -> {int(sel_bin)}"
                    print(
                        f"Relock: switched bin {old_bin} -> {int(sel_bin)} "
                        f"(~{evidence['selected_range_m']:.2f} m, "
                        f"confidence: {evidence['selected_confidence']})."
                    )
                else:
                    relock_ctrl.disarm_all()
                    status_text = f"relock scan: kept bin {old_bin}"
                    print(f"Relock: kept bin {old_bin} ({reason}).")
                _write_metadata(meta_path, run_meta)

        hr_conf = "high" if (hr_valid and np.isfinite(hr_smooth)) else "low"
        br_conf = dsp["br_confidence"]

        elapsed_s_hist.append(elapsed)
        hr_raw_hist.append(hr_raw)
        # UI histories carry display values (held bridging); CSV/NPZ stay real.
        hr_smooth_hist.append(hr_disp)
        br_hist.append(br_disp)

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
        }
        intermediates.append(inter)

        if len(intermediates) % checkpoint_n == 0:
            _save_intermediates(intermediates_path, intermediates)

        display.update(
            elapsed_s_hist, hr_smooth_hist, hr_raw_hist, br_hist,
            hr_conf, br_conf, elapsed, hr_disp, br_disp,
            _state["locked_bin"],
            (
                None if _state["locked_bin"] is None
                else int(_state["locked_bin"]) * float(pcfg["range_resolution_m"])
            ),
            hr_state=hr_disp_state,
            br_state=br_disp_state,
            status_text=status_text,
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
                    candidate_bins = _derive_candidate_bins(cfg)
                    selected_bin, dsp_override, evidence = _run_warmup_selection(
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
