"""Range-bin drift diagnostic (plans/bin_drift_diagnostic.md).

Read-only. Measures whether the in-gate radar energy profile moves away from its
settled warmup baseline over a session, and whether that movement is temporally
associated with radar DSP outcomes (gate_not_run etc.). This is an EVIDENCE
SUMMARY, not a tracker verdict: no single threshold ever decides drift/no-drift
(CLAUDE.md §4; plan §8 BDR-04, decided Option A). No Masimo data is read anywhere.

A capture with no matched replay generation (currently: live_test1, plan §8
BDR-07, decided Option A) still gets a full baseline/drift measurement -- that
needs only the raw ADC bytes -- but its outcome-correlation entries are reported
as `correlation_not_available`.

Usage
-----
    python -X utf8 scripts/diagnose_bin_drift.py \\
        --config scripts/live_demo_config.yaml \\
        --diagnostic-config scripts/diagnose_bin_drift_config.yaml \\
        --captures results/live_demo/<cap1> results/live_demo/<cap2> ... \\
        --replays results/live_demo/<replay1> results/live_demo/<replay2> ... \\
        --out results/diagnose/bin_drift

Captures and replays are paired by SHA-256 of the raw `adc_stream.bin`, never by
CLI position (plan §5) -- a replay whose `replay_file_hashes` does not match any
`--captures` entry is an error; a capture with no matching replay silently gets
Option-A-only treatment.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.radar_io import ChirpConfig, read_adc_bin  # noqa: E402
from src.warmup_select import derive_candidate_bins, range_energy_by_bin  # noqa: E402


# ── Provenance ────────────────────────────────────────────────────────────────

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def get_git_commit(repo_root: Path = REPO_ROOT) -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root,
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def is_tree_clean(repo_root: Path = REPO_ROOT) -> bool:
    """True iff no tracked file differs from HEAD (staged or unstaged).

    Untracked files do not count -- HEAD's record of "this file does not exist
    yet" is itself a well-defined, reproducible statement. What broke
    reproducibility in BDR-07 was a TRACKED file's content silently differing
    from what the recorded commit says (plan §5).
    """
    out = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--"], cwd=repo_root,
    )
    return out.returncode == 0


def get_peak_working_set_bytes() -> Optional[int]:
    """Best-effort measurement of this process's peak working set (Windows only).

    Returns None off-Windows or if the call fails -- never raises; memory
    logging is diagnostic, not load-bearing.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt

        class _PMCX(ctypes.Structure):
            _fields_ = [
                ("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        ctypes.windll.kernel32.GetCurrentProcess.restype = wt.HANDLE
        ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [
            wt.HANDLE, ctypes.POINTER(_PMCX), wt.DWORD
        ]
        ctypes.windll.psapi.GetProcessMemoryInfo.restype = wt.BOOL
        counters = _PMCX()
        counters.cb = ctypes.sizeof(_PMCX)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb)
        if not ok:
            return None
        return int(counters.PeakWorkingSetSize)
    except Exception:
        return None


# ── Config loading ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DiagnosticConfig:
    path: Path
    sha256: str
    stratum_frames: tuple[int, int]
    settled_start_frame: int
    block_frames: int
    trailing_block_policy: str
    gap_rule: str
    offset_phases: tuple[int, ...]
    duration_grid_s: tuple[float, ...]
    centroid_grid_bins: tuple[float, ...]
    require_clean_tree: bool
    preflight_min_available_gb: float
    raw: dict = field(repr=False)


def load_diagnostic_config(path: Path) -> DiagnosticConfig:
    raw_bytes = path.read_bytes()
    cfg = yaml.safe_load(raw_bytes)
    return DiagnosticConfig(
        path=path,
        sha256=sha256_bytes(raw_bytes),
        stratum_frames=tuple(cfg["calibration"]["stratum_frames"]),
        settled_start_frame=int(cfg["calibration"]["settled_start_frame"]),
        block_frames=int(cfg["blocks"]["block_frames"]),
        trailing_block_policy=str(cfg["blocks"]["trailing_block_policy"]),
        gap_rule=str(cfg["episodes"]["gap_rule"]),
        offset_phases=tuple(int(p) for p in cfg["offsets"]["phases"]),
        duration_grid_s=tuple(float(x) for x in cfg["sensitivity_grid"]["duration_s"]),
        centroid_grid_bins=tuple(float(x) for x in cfg["sensitivity_grid"]["centroid_drift_bins"]),
        require_clean_tree=bool(cfg["provenance"]["require_clean_tree"]),
        preflight_min_available_gb=float(cfg["memory"]["preflight_min_available_gb"]),
        raw=cfg,
    )


def load_live_demo_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def validate_decode_geometry(live_cfg: dict, capture_run_metadata: dict, session_id: str) -> ChirpConfig:
    """Cross-check the diagnostic's active decode geometry against the capture's
    OWN recorded snapshot (plan §5) and raise on any mismatch, so a future config
    edit cannot silently reinterpret old bytes."""
    active = live_cfg["profile"]
    recorded_cfg = capture_run_metadata.get("config")
    if recorded_cfg is None:
        raise ValueError(
            f"session {session_id!r}: run_metadata.json has no 'config' snapshot; "
            "cannot validate decode geometry against the capture's own record."
        )
    recorded_profile = recorded_cfg["profile"]
    for key in ("num_adc_samples", "num_rx", "num_chirps_per_frame", "range_resolution_m", "iq_swap"):
        if active[key] != recorded_profile[key]:
            raise ValueError(
                f"session {session_id!r}: active profile.{key}={active[key]!r} does not "
                f"match the capture's recorded profile.{key}={recorded_profile[key]!r}. "
                "Refusing to decode with mismatched geometry."
            )
    active_frame_rate = float(live_cfg["session"]["frame_rate_hz"])
    recorded_frame_rate = float(recorded_cfg["session"]["frame_rate_hz"])
    if active_frame_rate != recorded_frame_rate:
        raise ValueError(
            f"session {session_id!r}: active session.frame_rate_hz={active_frame_rate} != "
            f"recorded session.frame_rate_hz={recorded_frame_rate}."
        )
    period_ms = float(recorded_cfg["hw_frame"]["period_ms"])
    cross_check = 1000.0 / period_ms
    if abs(cross_check - recorded_frame_rate) > 1e-9:
        raise ValueError(
            f"session {session_id!r}: recorded session.frame_rate_hz={recorded_frame_rate} "
            f"is inconsistent with recorded hw_frame.period_ms={period_ms} "
            f"(1000/period_ms={cross_check})."
        )
    return ChirpConfig(
        num_adc_samples=int(active["num_adc_samples"]),
        num_rx=int(active["num_rx"]),
        num_tx=1,
        num_chirps_per_frame=int(active["num_chirps_per_frame"]),
        num_frames=0,
        frame_rate_hz=active_frame_rate,
        range_resolution_m=float(active["range_resolution_m"]),
        iq_swap=bool(active["iq_swap"]),
    )


# ── Energy / baseline computation ───────────────────────────────────────────

def _energy_by_bin_from_slice(cube: np.ndarray, frame_lo: int, frame_hi: int,
                               candidate_bins: list[int]) -> dict[int, float]:
    """range_energy_by_bin over cube[frame_lo:frame_hi] (frame_hi exclusive)."""
    return range_energy_by_bin(cube[frame_lo:frame_hi], candidate_bins)


def compute_centroid(power_by_bin: dict[int, float]) -> float:
    """Power-weighted centroid: sum(bin * power) / sum(power) (plan §3.1)."""
    total = sum(power_by_bin.values())
    if total <= 0:
        return float("nan")
    return sum(b * p for b, p in power_by_bin.items()) / total


def compute_baseline(cube: np.ndarray, candidate_bins: list[int],
                      cfg: DiagnosticConfig) -> dict:
    """Settled energy profile from the calibration stratum (plan §3.1)."""
    lo = cfg.settled_start_frame
    hi = cfg.stratum_frames[1] + 1  # inclusive JSON convention -> exclusive slice
    settled = _energy_by_bin_from_slice(cube, lo, hi, candidate_bins)
    argmax_bin = max(settled, key=settled.get)
    return {
        "settled_energy_by_bin": settled,
        "baseline_argmax_bin": argmax_bin,
        "baseline_centroid": compute_centroid(settled),
    }


def warmup_recompute_check(cube: np.ndarray, candidate_bins: list[int],
                            cfg: DiagnosticConfig, warmup_json: dict,
                            rtol: float = 1e-6) -> dict:
    """Schema-aware comparison of the recomputed calibration-stratum energies
    against the session's own warmup_bin_selection.json (plan §3.1, BDR-07 R3).

    Full-buffer resolution is always checked. The settled resolution
    (`settled_energy_db`) is checked only if the JSON's candidate records
    actually carry it -- a legacy-schema JSON (no such field) is reported as
    `not_available_legacy_schema`, never silently skipped or fabricated.
    """
    lo0, hi0 = cfg.stratum_frames[0], cfg.stratum_frames[1] + 1
    full = _energy_by_bin_from_slice(cube, lo0, hi0, candidate_bins)
    candidates_by_bin = {int(c["bin"]): c for c in warmup_json["candidates"]}

    full_matches = []
    for b in candidate_bins:
        json_energy = candidates_by_bin[b]["energy"]
        full_matches.append(bool(np.isclose(full[b], json_energy, rtol=rtol)))
    full_ok = all(full_matches)

    has_settled_schema = all(
        "settled_energy_db" in candidates_by_bin[b] for b in candidate_bins
    )
    if not has_settled_schema:
        return {
            "full_buffer_check": "ok" if full_ok else "mismatch",
            "settled_warmup_json_validation": "not_available_legacy_schema",
        }

    settled_lo, settled_hi = cfg.settled_start_frame, cfg.stratum_frames[1] + 1
    settled = _energy_by_bin_from_slice(cube, settled_lo, settled_hi, candidate_bins)
    e_ref = max(settled.values())
    settled_matches = []
    for b in candidate_bins:
        computed_db = 10.0 * np.log10(settled[b] / e_ref) if (settled[b] > 0 and e_ref > 0) else float("-inf")
        json_db = candidates_by_bin[b]["settled_energy_db"]
        if json_db is None:
            settled_matches.append(not np.isfinite(computed_db))
        else:
            settled_matches.append(bool(np.isclose(computed_db, json_db, atol=0.05)))
    settled_ok = all(settled_matches)

    return {
        "full_buffer_check": "ok" if full_ok else "mismatch",
        "settled_warmup_json_validation": "ok" if settled_ok else "mismatch",
    }


# ── Block series (post-calibration) ─────────────────────────────────────────

@dataclass
class BlockSeries:
    block_start_frame: np.ndarray   # (n_blocks,) int
    argmax_bin: np.ndarray          # (n_blocks,) int
    centroid: np.ndarray            # (n_blocks,) float
    trailing_discarded_frames: int


def compute_block_series(cube: np.ndarray, candidate_bins: list[int],
                          cfg: DiagnosticConfig) -> BlockSeries:
    """1 s blocks starting at the frame after the calibration stratum. The
    trailing incomplete block is DISCARDED, never weighted in (plan §3.2,
    BDR-08 R2)."""
    start = cfg.stratum_frames[1] + 1
    n_total = cube.shape[0]
    n_available = n_total - start
    block_frames = cfg.block_frames
    n_blocks = n_available // block_frames
    trailing = n_available - n_blocks * block_frames

    argmax_bin = np.zeros(n_blocks, dtype=int)
    centroid = np.zeros(n_blocks, dtype=float)
    block_start_frame = np.zeros(n_blocks, dtype=int)
    for i in range(n_blocks):
        lo = start + i * block_frames
        hi = lo + block_frames
        block_start_frame[i] = lo
        energies = _energy_by_bin_from_slice(cube, lo, hi, candidate_bins)
        argmax_bin[i] = max(energies, key=energies.get)
        centroid[i] = compute_centroid(energies)

    return BlockSeries(block_start_frame, argmax_bin, centroid, trailing)


# ── Episodes ─────────────────────────────────────────────────────────────────

@dataclass
class Episode:
    start_s: float
    end_s: float                # exclusive
    bin_sequence: tuple[int, ...]
    modal_bin: int
    max_displacement_bins: int


def detect_episodes(blocks: BlockSeries, baseline_bin: int, fs: float,
                     block_frames: int) -> list[Episode]:
    """Maximal runs of consecutive off-baseline blocks. No gap-bridging: a
    single on-baseline block ends the run (plan §4)."""
    off = blocks.argmax_bin != baseline_bin
    episodes: list[Episode] = []
    i = 0
    n = len(off)
    block_s = block_frames / fs
    while i < n:
        if not off[i]:
            i += 1
            continue
        j = i
        while j < n and off[j]:
            j += 1
        seq = tuple(int(b) for b in blocks.argmax_bin[i:j])
        modal = int(np.bincount(np.array(seq) - min(seq)).argmax() + min(seq))
        max_disp = max(abs(b - baseline_bin) for b in seq)
        episodes.append(Episode(
            start_s=i * block_s,
            end_s=j * block_s,
            bin_sequence=seq,
            modal_bin=modal,
            max_displacement_bins=max_disp,
        ))
        i = j
    return episodes


def episodes_at_grid(episodes: list[Episode], duration_grid_s: tuple[float, ...]) -> dict[float, int]:
    """Count of episodes whose duration >= each grid value (plan §8, Option A)."""
    return {
        d: sum(1 for e in episodes if (e.end_s - e.start_s) >= d)
        for d in duration_grid_s
    }


# ── Window audit (NPZ hop grid) ─────────────────────────────────────────────

REJECTION_CODE_NOT_ATTEMPTED = -1


def classify_window_outcome(accepted_rank: int, rejection_codes: np.ndarray,
                             f_r_hz: float) -> str:
    """Mutually exclusive classifier (plan §4)."""
    if accepted_rank >= 0:
        return "covered"
    all_not_run = bool(np.all(rejection_codes == REJECTION_CODE_NOT_ATTEMPTED))
    f_r_invalid = not np.isfinite(f_r_hz)
    if all_not_run and f_r_invalid:
        return "gate_not_run"
    return "other_rejected"


@dataclass
class WindowRow:
    window_index: int
    frame_start: int
    frame_end: int
    is_warmup_window: bool
    post_calibration_observed_s: float
    dominant_argmax_mode: Optional[int]
    mean_centroid: Optional[float]
    off_baseline_duration_s: float
    longest_excursion_s: float
    rejection_codes: tuple[int, ...]
    f_r_hz: float
    outcome_class: str


def align_windows(frame_idx: np.ndarray, blocks: BlockSeries, baseline_bin: int,
                   accepted_rank: np.ndarray, rejection_codes: np.ndarray,
                   f_r_hz: np.ndarray, fs: float, window_frames: int,
                   hop_s: float, calibration_end_frame: int,
                   block_frames: int) -> list[WindowRow]:
    """One row per NPZ window, aligned to its constituent post-calibration
    1 s blocks (plan §3.2/§4). Window 0 (the calibration stratum itself) is
    flagged is_warmup_window with zero exposure."""
    block_s = block_frames / fs
    rows: list[WindowRow] = []
    off = blocks.argmax_bin != baseline_bin

    for i, end_frame in enumerate(frame_idx):
        end_frame = int(end_frame)
        start_frame = end_frame - window_frames + 1
        is_warmup = (i == 0)
        observed_s = 0.0 if is_warmup else min(float(window_frames) / fs, i * hop_s)

        mask = (
            (blocks.block_start_frame >= start_frame)
            & (blocks.block_start_frame < end_frame + 1)
            & (blocks.block_start_frame >= calibration_end_frame)
        )
        if mask.any():
            window_argmax = blocks.argmax_bin[mask]
            window_centroid = blocks.centroid[mask]
            vals, counts = np.unique(window_argmax, return_counts=True)
            dominant = int(vals[np.argmax(counts)])
            mean_c = float(np.mean(window_centroid))
            off_mask = off[mask]
            off_duration = float(np.sum(off_mask)) * block_s
            longest = 0.0
            run = 0.0
            for is_off in off_mask:
                if is_off:
                    run += block_s
                    longest = max(longest, run)
                else:
                    run = 0.0
        else:
            dominant = None
            mean_c = None
            off_duration = 0.0
            longest = 0.0

        rows.append(WindowRow(
            window_index=i,
            frame_start=start_frame,
            frame_end=end_frame,
            is_warmup_window=is_warmup,
            post_calibration_observed_s=observed_s,
            dominant_argmax_mode=dominant,
            mean_centroid=mean_c,
            off_baseline_duration_s=off_duration,
            longest_excursion_s=longest,
            rejection_codes=tuple(int(c) for c in rejection_codes[i]),
            f_r_hz=float(f_r_hz[i]),
            outcome_class=classify_window_outcome(int(accepted_rank[i]), rejection_codes[i], float(f_r_hz[i])),
        ))
    return rows


FULL_EXPOSURE_S = 30.0


def stratify_windows(rows: list[WindowRow]) -> tuple[list[WindowRow], list[WindowRow]]:
    """Split into (full_exposure, transitional), excluding the warmup window
    entirely (plan §4, BDR-03 R3)."""
    eligible = [r for r in rows if not r.is_warmup_window]
    full = [r for r in eligible if r.post_calibration_observed_s >= FULL_EXPOSURE_S - 1e-9]
    transitional = [r for r in eligible if r.post_calibration_observed_s < FULL_EXPOSURE_S - 1e-9]
    return full, transitional


def offset_phase_subsets(rows: list[WindowRow], phases: tuple[int, ...]) -> dict[int, list[WindowRow]]:
    """All 10 disjoint non-overlapping hop-offset phases {k, k+10, k+20, ...}
    (plan §4)."""
    return {k: [r for r in rows if r.window_index % 10 == k] for k in phases}


# ── Motion energy (channel-preserving) ──────────────────────────────────────

def compute_motion_energy(cube: np.ndarray, candidate_bins: list[int]) -> dict[int, float]:
    """motion_energy(b) = mean_(t,c,r) |X(t,c,r,b) - mean_t' X(t',c,r,b)|^2
    (plan §3.3). Per-channel mean subtracted BEFORE averaging across
    chirps/RX, so a genuinely moving reflector cannot destructively cancel
    across RX channels with different static phases."""
    n_adc = cube.shape[-1]
    hann = np.hanning(n_adc).astype(np.float32)
    windowed = cube * hann
    range_fft = np.fft.fft(windowed, axis=-1)  # (frames, chirps, rx, n_adc)

    out: dict[int, float] = {}
    for b in candidate_bins:
        X = range_fft[:, :, :, b]                       # (frames, chirps, rx)
        channel_mean = X.mean(axis=0, keepdims=True)     # (1, chirps, rx)
        deviation = X - channel_mean
        out[b] = float(np.mean(np.abs(deviation) ** 2))
    return out


# ── Provenance-bound loading ─────────────────────────────────────────────────

@dataclass
class SessionInputs:
    session_id: str
    capture_dir: Path
    replay_dir: Optional[Path]
    raw_sha256: str
    warmup_json: dict
    warmup_json_path: Path
    npz: Optional[dict]
    npz_path: Optional[Path]
    run_metadata: dict
    run_metadata_path: Path


def match_replays_to_captures(capture_dirs: list[Path], replay_dirs: list[Path]) -> dict[Path, Optional[Path]]:
    """Pair each capture to the (at most one) replay whose recorded
    replay_file_hashes matches the capture's raw SHA-256 -- never by CLI
    position (plan §5)."""
    capture_hashes = {c: sha256_file(c / "adc_stream.bin") for c in capture_dirs}
    mapping: dict[Path, Optional[Path]] = {c: None for c in capture_dirs}
    matched_replay_hashes: set[str] = set()

    for r in replay_dirs:
        meta = json.loads((r / "run_metadata.json").read_text(encoding="utf-8"))
        hashes = list((meta.get("replay_file_hashes") or {}).values())
        if not hashes:
            raise ValueError(f"replay {r} has no replay_file_hashes recorded.")
        replay_hash = hashes[0]
        found = None
        for c, chash in capture_hashes.items():
            if chash == replay_hash:
                found = c
                break
        if found is None:
            raise ValueError(
                f"replay {r} (source hash {replay_hash}) does not match any "
                f"--captures entry."
            )
        mapping[found] = r
        matched_replay_hashes.add(replay_hash)
    return mapping


def load_session_inputs(session_id: str, capture_dir: Path, replay_dir: Optional[Path]) -> SessionInputs:
    raw_path = capture_dir / "adc_stream.bin"
    raw_sha256 = sha256_file(raw_path)

    evidence_dir = replay_dir if replay_dir is not None else capture_dir
    warmup_json_path = evidence_dir / "warmup_bin_selection.json"
    warmup_json = json.loads(warmup_json_path.read_text(encoding="utf-8"))
    run_metadata_path = evidence_dir / "run_metadata.json"
    run_metadata = json.loads(run_metadata_path.read_text(encoding="utf-8"))

    if replay_dir is not None:
        replay_hashes = list((run_metadata.get("replay_file_hashes") or {}).values())
        if not replay_hashes or replay_hashes[0] != raw_sha256:
            raise ValueError(
                f"session {session_id!r}: replay {replay_dir} does not match raw "
                f"capture {raw_path} (SHA-256 mismatch)."
            )
        npz_path = replay_dir / "live_intermediates.npz"
        npz = dict(np.load(npz_path, allow_pickle=True))
    else:
        npz_path = None
        npz = None

    return SessionInputs(
        session_id=session_id, capture_dir=capture_dir, replay_dir=replay_dir,
        raw_sha256=raw_sha256, warmup_json=warmup_json, warmup_json_path=warmup_json_path,
        npz=npz, npz_path=npz_path, run_metadata=run_metadata, run_metadata_path=run_metadata_path,
    )


# ── Per-session pipeline ─────────────────────────────────────────────────────

def run_session(session: SessionInputs, live_cfg: dict, diag_cfg: DiagnosticConfig,
                 out_dir: Path) -> dict:
    chirp_cfg = validate_decode_geometry(live_cfg, session.run_metadata, session.session_id)
    candidate_bins = derive_candidate_bins(live_cfg)

    mem_before = get_peak_working_set_bytes()
    cube = read_adc_bin(session.capture_dir / "adc_stream.bin", chirp_cfg)
    mem_after = get_peak_working_set_bytes()

    baseline = compute_baseline(cube, candidate_bins, diag_cfg)
    recompute = warmup_recompute_check(cube, candidate_bins, diag_cfg, session.warmup_json)

    locked_bin = int(session.warmup_json["selected_bin"])
    lock_candidates = {int(c["bin"]): c for c in session.warmup_json["candidates"]}
    baseline_rank_of_lock = None
    if "energy_rank" in lock_candidates.get(locked_bin, {}):
        baseline_rank_of_lock = int(lock_candidates[locked_bin]["energy_rank"])

    blocks = compute_block_series(cube, candidate_bins, diag_cfg)
    fs = float(live_cfg["session"]["frame_rate_hz"])
    episodes = detect_episodes(blocks, baseline["baseline_argmax_bin"], fs, diag_cfg.block_frames)
    episode_grid = episodes_at_grid(episodes, diag_cfg.duration_grid_s)

    trailing_10s_start_frame = cube.shape[0] - int(round(10.0 * fs))
    trailing_mask = blocks.block_start_frame >= trailing_10s_start_frame
    leading_10s_end_frame = diag_cfg.stratum_frames[1] + 1 + int(round(10.0 * fs))
    leading_mask = blocks.block_start_frame < leading_10s_end_frame
    trailing_centroid_median = float(np.median(blocks.centroid[trailing_mask])) if trailing_mask.any() else float("nan")
    leading_centroid_median = float(np.median(blocks.centroid[leading_mask])) if leading_mask.any() else float("nan")

    motion_energy = compute_motion_energy(cube, candidate_bins)

    window_rows: list[WindowRow] = []
    if session.npz is not None:
        npz = session.npz
        window_frames = int(round(30.0 * fs))
        hop_s = float(live_cfg["session"]["hop_s"])
        window_rows = align_windows(
            npz["frame_idx"], blocks, baseline["baseline_argmax_bin"],
            npz["accepted_candidate_rank"], npz["candidate_rejection_codes"],
            npz["f_r_hz"], fs, window_frames, hop_s, diag_cfg.stratum_frames[1] + 1,
            diag_cfg.block_frames,
        )

    full_exposure, transitional = stratify_windows(window_rows)
    phase_subsets = offset_phase_subsets(window_rows, diag_cfg.offset_phases)

    del cube  # free the decoded cube before writing output artifacts

    reproducible = is_tree_clean() if diag_cfg.require_clean_tree else None

    result = {
        "session_id": session.session_id,
        "raw_sha256": session.raw_sha256,
        "warmup_json_path": str(session.warmup_json_path),
        "warmup_json_sha256": sha256_file(session.warmup_json_path),
        "run_metadata_path": str(session.run_metadata_path),
        "npz_path": str(session.npz_path) if session.npz_path else None,
        "npz_sha256": sha256_file(session.npz_path) if session.npz_path else None,
        "correlation_available": session.npz is not None,
        "locked_bin": locked_bin,
        "baseline_argmax_bin": baseline["baseline_argmax_bin"],
        "baseline_centroid": baseline["baseline_centroid"],
        "baseline_rank_of_locked_bin": baseline_rank_of_lock,
        "warmup_recompute_check": recompute,
        "centroid_drift": {
            "trailing_10s_median": trailing_centroid_median,
            "first_post_calibration_10s_median": leading_centroid_median,
        },
        "n_blocks": int(len(blocks.block_start_frame)),
        "trailing_discarded_frames": blocks.trailing_discarded_frames,
        "episodes": [
            {"start_s": e.start_s, "end_s": e.end_s, "bin_sequence": e.bin_sequence,
             "modal_bin": e.modal_bin, "max_displacement_bins": e.max_displacement_bins}
            for e in episodes
        ],
        "episode_count_at_grid": {str(d): n for d, n in episode_grid.items()},
        "motion_energy_by_bin": motion_energy,
        "n_windows": len(window_rows),
        "n_full_exposure_windows": len(full_exposure),
        "n_transitional_windows": len(transitional),
        "reproducible": reproducible,
        "mem_peak_working_set_before_decode": mem_before,
        "mem_peak_working_set_after_decode": mem_after,
    }

    session_dir = _write_session_outputs(out_dir, session.session_id, blocks, window_rows, motion_energy, result)
    plot_drift_overview(session_dir, blocks, baseline["baseline_argmax_bin"], window_rows)
    return result


# ── Output writers ───────────────────────────────────────────────────────────

def _write_session_outputs(out_dir: Path, session_id: str, blocks: BlockSeries,
                            window_rows: list[WindowRow], motion_energy: dict[int, float],
                            summary_fragment: dict) -> Path:
    session_dir = out_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=False)

    import csv
    with (session_dir / "bin_energy_blocks.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["block_index", "block_start_frame", "argmax_bin", "centroid"])
        for i in range(len(blocks.block_start_frame)):
            w.writerow([i, int(blocks.block_start_frame[i]), int(blocks.argmax_bin[i]),
                        float(blocks.centroid[i])])

    with (session_dir / "window_audit.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["window_index", "frame_start", "frame_end", "is_warmup_window",
                     "post_calibration_observed_s", "dominant_argmax_mode", "mean_centroid",
                     "off_baseline_duration_s", "longest_excursion_s", "rejection_codes",
                     "f_r_hz", "outcome_class"])
        for r in window_rows:
            w.writerow([r.window_index, r.frame_start, r.frame_end, r.is_warmup_window,
                        r.post_calibration_observed_s, r.dominant_argmax_mode, r.mean_centroid,
                        r.off_baseline_duration_s, r.longest_excursion_s,
                        ";".join(str(c) for c in r.rejection_codes), r.f_r_hz, r.outcome_class])

    np.savez(
        session_dir / "motion_energy_windows.npz",
        bins=np.array(list(motion_energy.keys())),
        values=np.array(list(motion_energy.values())),
    )

    with (session_dir / "summary.json").open("w", encoding="utf-8") as fh:
        json.dump(summary_fragment, fh, indent=2, default=str)

    return session_dir


def plot_drift_overview(session_dir: Path, blocks: BlockSeries, baseline_bin: int,
                         window_rows: list[WindowRow]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams.update({
        "figure.autolayout": False, "text.usetex": False, "font.family": "DejaVu Sans",
    })
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(11, 4))
    t = blocks.block_start_frame / 20.0
    ax.plot(t, blocks.argmax_bin, color="#1f6f8b", linewidth=1.5, label="argmax bin")
    ax.plot(t, blocks.centroid, color="#e0782f", linewidth=1.0, linestyle="--",
            label="power-weighted centroid")
    ax.axhline(baseline_bin, color="#444444", linewidth=1.0, linestyle=":",
               label=f"baseline (bin {baseline_bin})")
    ax.set_xlabel("time since capture start (s)")
    ax.set_ylabel("range bin")
    ax.legend(loc="upper right", frameon=False)
    ax.set_title("Bin-drift evidence summary")
    fig.tight_layout()
    fig.savefig(session_dir / "drift_overview.png", dpi=120)
    plt.close(fig)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="scripts/live_demo_config.yaml", type=Path)
    ap.add_argument("--diagnostic-config", default="scripts/diagnose_bin_drift_config.yaml", type=Path)
    ap.add_argument("--captures", nargs="+", required=True, type=Path)
    ap.add_argument("--replays", nargs="*", default=[], type=Path)
    ap.add_argument("--out", default="results/diagnose/bin_drift", type=Path)
    ap.add_argument("--allow-dirty", action="store_true",
                     help="Permit a dirty tree; the run is stamped reproducible: false.")
    return ap.parse_args()


def main() -> None:
    args = _parse_args()
    live_cfg = load_live_demo_config(args.config)
    diag_cfg = load_diagnostic_config(args.diagnostic_config)

    if diag_cfg.require_clean_tree and not is_tree_clean() and not args.allow_dirty:
        print(
            "ERROR: working tree has uncommitted changes to tracked files. "
            "Commit first, or pass --allow-dirty to run anyway (the result will "
            "be stamped reproducible: false and must not be cited as evidence).",
            file=sys.stderr,
        )
        sys.exit(1)

    mapping = match_replays_to_captures(args.captures, args.replays)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out / run_id
    if out_dir.exists():
        print(f"ERROR: run directory {out_dir} already exists.", file=sys.stderr)
        sys.exit(1)
    out_dir.mkdir(parents=True)

    run_manifest = {
        "run_id": run_id,
        "git_commit": get_git_commit(),
        "diagnostic_config_path": str(args.diagnostic_config),
        "diagnostic_config_sha256": diag_cfg.sha256,
        "live_demo_config_path": str(args.config),
        "live_demo_config_sha256": sha256_file(args.config),
        "sessions": {},
    }

    for capture_dir, replay_dir in mapping.items():
        session_id = capture_dir.name
        session = load_session_inputs(session_id, capture_dir, replay_dir)
        result = run_session(session, live_cfg, diag_cfg, out_dir)
        run_manifest["sessions"][session_id] = result
        print(f"{session_id}: baseline_argmax_bin={result['baseline_argmax_bin']} "
              f"locked_bin={result['locked_bin']} "
              f"n_episodes={len(result['episodes'])} "
              f"episode_count_at_grid={result['episode_count_at_grid']} "
              f"correlation_available={result['correlation_available']}")

    with (out_dir / "run_summary.json").open("w", encoding="utf-8") as fh:
        json.dump(run_manifest, fh, indent=2, default=str)

    print(f"Artifacts: {out_dir}")


if __name__ == "__main__":
    main()
