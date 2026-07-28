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
from src.vitals import AHET_MAX_CANDIDATES  # noqa: E402


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


def get_available_memory_bytes() -> Optional[int]:
    """Best-effort available physical memory (Windows only). None off-Windows
    or on failure -- the preflight check degrades to a no-op rather than
    blocking a run it cannot evaluate."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class _MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = _MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        if not ok:
            return None
        return int(stat.ullAvailPhys)
    except Exception:
        return None


def preflight_check_memory(diag_cfg: "DiagnosticConfig", session_id: str) -> Optional[int]:
    """Enforce `memory.preflight_min_available_gb` (BDR-18 -- previously
    loaded and never checked). Returns the observed available bytes (for
    logging) or None if it could not be measured. Raises MemoryError, never
    silently proceeds, when a measurement IS available and is below bound."""
    available = get_available_memory_bytes()
    if available is None:
        return None
    required = diag_cfg.preflight_min_available_gb * 1e9
    if available < required:
        raise MemoryError(
            f"session {session_id!r}: preflight check failed -- {available / 1e9:.2f} GB "
            f"physical memory available, {diag_cfg.preflight_min_available_gb:.2f} GB required "
            "(scripts/diagnose_bin_drift_config.yaml: memory.preflight_min_available_gb)."
        )
    return available


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
    #: Support (seconds) for the trailing/leading centroid-drift statistic
    #: (BDR-23) -- governs `trailing_leading_centroid_medians`; was a hardcoded
    #: 10.0 literal until this field existed.
    centroid_summary_span_s: float
    require_clean_tree: bool
    preflight_min_available_gb: float
    #: Maps a capture's raw adc_stream.bin SHA-256 to the SHA-256 of the ONE
    #: approved replay's run_metadata.json for that capture (BDR-20). Checked
    #: by `match_replays_to_captures` before any replay's DSP outcomes are
    #: used -- a replay matching raw bytes but not this hash is rejected.
    approved_replays: dict
    raw: dict = field(repr=False)


def load_diagnostic_config(path: Path) -> DiagnosticConfig:
    raw_bytes = path.read_bytes()
    cfg = yaml.safe_load(raw_bytes)
    centroid_summary_span_s = float(cfg["centroid"]["summary_span_s"])
    if not np.isfinite(centroid_summary_span_s) or centroid_summary_span_s <= 0:
        raise ValueError(
            f"centroid.summary_span_s={centroid_summary_span_s!r} must be finite and "
            "positive (BDR-23 R2)."
        )
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
        centroid_summary_span_s=centroid_summary_span_s,
        require_clean_tree=bool(cfg["provenance"]["require_clean_tree"]),
        preflight_min_available_gb=float(cfg["memory"]["preflight_min_available_gb"]),
        approved_replays=dict(cfg.get("approved_replays") or {}),
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


def rank_of_bin_in_profile(profile: dict[int, float], bin_id: int) -> int:
    """1-indexed rank of `bin_id` by descending energy within `profile`
    (BDR-21) -- ties broken by ascending bin index for a deterministic order.
    Used to rank the locked bin within the diagnostic's OWN settled baseline
    profile, not the full-buffer warmup-JSON rank (a different quantity)."""
    ordered = sorted(profile.keys(), key=lambda b: (-profile[b], b))
    return ordered.index(bin_id) + 1


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
    #: Full per-bin energy matrix, shape (n_blocks, len(candidate_bins)) -- the
    #: promised "per-bin energy matrix" (plan §4), not just the derived
    #: argmax/centroid summary statistics (BDR-14).
    energy_matrix: np.ndarray
    candidate_bins: list[int]
    trailing_discarded_frames: int


def compute_block_series(cube: np.ndarray, candidate_bins: list[int],
                          cfg: DiagnosticConfig) -> BlockSeries:
    """1 s blocks starting at the frame after the calibration stratum
    (plan §3.2). The trailing-block policy is READ from config and governs
    behavior, not merely hashed decoration (BDR-19) -- an unsupported policy
    value fails closed rather than silently falling back to a default."""
    if cfg.trailing_block_policy != "discard":
        raise NotImplementedError(
            f"trailing_block_policy={cfg.trailing_block_policy!r} is not supported; "
            "only 'discard' is implemented (plan §7.1, BDR-08 R2)."
        )
    start = cfg.stratum_frames[1] + 1
    n_total = cube.shape[0]
    n_available = n_total - start
    block_frames = cfg.block_frames
    n_blocks = n_available // block_frames
    trailing = n_available - n_blocks * block_frames  # DISCARDED (see policy check above)

    argmax_bin = np.zeros(n_blocks, dtype=int)
    centroid = np.zeros(n_blocks, dtype=float)
    block_start_frame = np.zeros(n_blocks, dtype=int)
    energy_matrix = np.zeros((n_blocks, len(candidate_bins)), dtype=float)
    for i in range(n_blocks):
        lo = start + i * block_frames
        hi = lo + block_frames
        block_start_frame[i] = lo
        energies = _energy_by_bin_from_slice(cube, lo, hi, candidate_bins)
        argmax_bin[i] = max(energies, key=energies.get)
        centroid[i] = compute_centroid(energies)
        energy_matrix[i, :] = [energies[b] for b in candidate_bins]

    return BlockSeries(block_start_frame, argmax_bin, centroid, energy_matrix,
                        list(candidate_bins), trailing)


# ── Episodes ─────────────────────────────────────────────────────────────────

@dataclass
class Episode:
    start_s: float
    end_s: float                # exclusive
    bin_sequence: tuple[int, ...]
    modal_bin: int
    max_displacement_bins: int


def detect_episodes(blocks: BlockSeries, baseline_bin: int, fs: float,
                     block_frames: int, gap_rule: str = "no_bridging") -> list[Episode]:
    """Maximal runs of consecutive off-baseline blocks. `gap_rule` is READ from
    config and governs behavior (BDR-19): only `no_bridging` (a single
    on-baseline block ends the run) is implemented; any other value fails
    closed rather than silently defaulting."""
    if gap_rule != "no_bridging":
        raise NotImplementedError(
            f"episodes.gap_rule={gap_rule!r} is not supported; only 'no_bridging' is "
            "implemented (plan §4)."
        )
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
    """Count of episodes whose duration >= each grid value (plan §8, Option A).
    Session-level, radar-only, deliberately outcome-blind -- episode DETECTION
    needs no outcome data. See `duration_grid_by_outcome` for the per-window,
    outcome-stratified association (BDR-11 R2)."""
    return {
        d: sum(1 for e in episodes if (e.end_s - e.start_s) >= d)
        for d in duration_grid_s
    }


def compute_occupancy(blocks: BlockSeries, baseline_bin: int) -> dict[str, float]:
    """Fraction of post-calibration blocks with argmax at the baseline bin /
    within 1 / within 2 / outside (plan §4) -- promised but never persisted
    until BDR-14."""
    n = len(blocks.block_start_frame)
    if n == 0:
        return {"at_baseline": float("nan"), "within_1_bin": float("nan"),
                "within_2_bins": float("nan"), "outside_2_bins": float("nan")}
    disp = np.abs(blocks.argmax_bin - baseline_bin)
    return {
        "at_baseline": float(np.mean(disp == 0)),
        "within_1_bin": float(np.mean(disp <= 1)),
        "within_2_bins": float(np.mean(disp <= 2)),
        "outside_2_bins": float(np.mean(disp > 2)),
    }


def trailing_leading_centroid_medians(blocks: BlockSeries, cfg: DiagnosticConfig,
                                       fs: float) -> tuple[float, float, int]:
    """Robust centroid-drift inputs (plan §3.1): median centroid over the last
    N complete 1s blocks vs. the first N complete post-calibration blocks,
    N = round(cfg.centroid_summary_span_s / block duration) -- selected by
    POSITION in the block series, not by a frame-count threshold (BDR-15:
    `cube.shape[0]` includes the session's non-block-aligned trailing
    remainder, 10-15 frames on every real capture, so a threshold in raw
    frames does not land on a block boundary and silently drops one block
    from the "last N s"). The support itself is config-bound, not a hardcoded
    10.0 literal (BDR-23).

    Returns `(trailing_median, leading_median, n_blocks_used)` -- `n_blocks_used`
    is the ACTUAL block count applied, `min(n_window_blocks, len(blocks))`
    (BDR-23 R3, corrected: round-7 returned the REQUESTED `n_window_blocks`
    even when fewer blocks were actually available -- e.g. a 10-block
    configured support with only 3 blocks in the series silently reported 10,
    not the 3 actually used; 0, not 10, for an empty series). The requested
    span is already recorded separately as `summary_span_s` wherever this is
    serialized, so it is not duplicated here under another name.

    Raises `ValueError` if the configured span rounds to fewer than one
    complete block (BDR-23 R2) -- e.g. `summary_span_s` too small relative to
    `fs`/`block_frames` -- rather than silently degrading (`blocks.centroid[-0:]`
    selects the WHOLE series while `blocks.centroid[:0]` is empty/NaN, so an
    unvalidated zero-block support is neither a rejected config nor a genuine
    zero-span statistic)."""
    n_window_blocks = int(round(cfg.centroid_summary_span_s * fs / cfg.block_frames))
    if n_window_blocks < 1:
        raise ValueError(
            f"centroid.summary_span_s={cfg.centroid_summary_span_s} rounds to "
            f"{n_window_blocks} blocks at fs={fs}, block_frames={cfg.block_frames} -- "
            "must resolve to at least one complete block (BDR-23 R2)."
        )
    n_blocks = len(blocks.block_start_frame)
    if n_blocks == 0:
        return float("nan"), float("nan"), 0
    n_used = min(n_window_blocks, n_blocks)
    trailing_median = float(np.median(blocks.centroid[-n_used:]))
    leading_median = float(np.median(blocks.centroid[:n_used]))
    return trailing_median, leading_median, n_used


def centroid_drift_at_grid(trailing_median: float, leading_median: float,
                            centroid_grid_bins: tuple[float, ...]) -> dict[float, bool]:
    """Whether the session-level robust centroid drift (plan §3.1: trailing-10s
    vs. first-post-calibration-10s median) meets or exceeds each grid
    displacement value (plan §8, Option A). This is a SEPARATE, session-level
    statistic from the per-window duration grid (`episodes_at_grid`) -- the two
    grid axes are reported independently, never as a per-window Cartesian
    joint classifier (BDR-11: no per-window centroid-displacement statistic is
    defined, so a joint grid would be invented post hoc)."""
    if not (np.isfinite(trailing_median) and np.isfinite(leading_median)):
        return {b: False for b in centroid_grid_bins}
    displacement = abs(trailing_median - leading_median)
    return {b: bool(displacement >= b) for b in centroid_grid_bins}


# ── Window audit (NPZ hop grid) ─────────────────────────────────────────────

REJECTION_CODE_NOT_ATTEMPTED = -1
REJECTION_CODE_PASSED = 0
REJECTION_CODE_DOMAIN = frozenset(range(-1, 8))  # -1 gate_not_run, 0 passed, 1-7 rejection reasons (src/vitals.py)

# Physiological respiration gate (src/vitals.py:507-509 -- local to
# estimate_rate_from_phase there, so not importable as a module constant).
# An f_r_hz outside this range routes through the SAME no-ECA early-return
# branch as f_r_hz=None (src/vitals.py:523), producing accepted_candidate_rank=-1
# and all-not-run rejection codes with a FINITE f_r_hz (BDR-02 R3).
RESP_GATE_LO_HZ = 0.15
RESP_GATE_HI_HZ = 0.60


def classify_window_outcome(accepted_rank: int, rejection_codes: np.ndarray,
                             f_r_hz: float) -> str:
    """Mutually exclusive classifier (plan §4). Fail-closed on evidence the
    producer (scripts/live_demo.py, the strict_v1 AHET gate mode production
    runs use) can never actually emit (BDR-02 R2, BDR-02 R3, BDR-25):

    - `accepted_rank` outside the domain `{-1, 0, ..., AHET_MAX_CANDIDATES-1}`
      this generation's 3 candidate slots allow.
    - A MIX of `-1` and concrete codes in `rejection_codes` -- impossible
      under strict_v1: a row either has all codes `-1` (the no-ECA branch
      never touches them) or has NO `-1` anywhere, because
      `candidate_rejection_code[not_attempted] = 5` (src/vitals.py:940)
      overwrites every never-entered slot's `-1` with code 5 before the
      executed gate returns.
    - `accepted_rank >= 0` (a candidate accepted) paired with a non-finite
      `f_r_hz`, or a finite `f_r_hz` outside the physiological gate
      `[RESP_GATE_LO_HZ, RESP_GATE_HI_HZ]` -- impossible under strict_v1:
      ECA/AHET evaluation, the only path that can produce a non-negative
      rank, runs only when `f_r_hz` is finite AND within the gate; either
      failure routes through the no-ECA early return, which always hardcodes
      `accepted_rank=-1` (src/vitals.py:523).
    - `accepted_rank >= 0` paired with all-not-run (`-1`) rejection codes --
      also impossible: an executed strict_v1 gate always assigns concrete
      codes, including `REJECTION_CODE_PASSED` (0) for the accepted slot.
    - `accepted_rank >= 0` whose own slot's rejection code is not
      `REJECTION_CODE_PASSED` -- the accepted-slot-passed invariant the
      generation guarantees.
    - `accepted_rank >= 0` that is not the FIRST slot coded
      `REJECTION_CODE_PASSED` -- strict_v1 always selects
      `passed_ranks[0]` (src/vitals.py:941-943), so an earlier passed slot
      with a later accepted rank is contradictory.
    - `accepted_rank == -1` paired with any slot coded `REJECTION_CODE_PASSED`
      -- also impossible: a passed slot always forces the corresponding
      non-negative rank to be returned (src/vitals.py:941-943).
    - `accepted_rank == -1` with all-not-run codes paired with a finite
      `f_r_hz` WITHIN the physiological gate -- also impossible: ECA/AHET
      always executes (and assigns concrete, non-`-1` codes) whenever
      `f_r_hz` passes the gate.

    A malformed or replaced NPZ that violates any of these raises `ValueError`
    rather than silently landing in `"covered"`, `"gate_not_run"`, or
    `"other_rejected"`.

    `gate_not_run` is decided from all-not-run rejection codes ALONE (BDR-02
    R3, corrected): the no-ECA early return that produces them
    (src/vitals.py:523) fires for f_r_hz=None **or** a finite value outside
    the physiological gate -- round 7's classifier additionally required
    `f_r_hz` to be non-finite, which mislabeled real finite-outlier windows
    (massimo1 has 6, sweep has 1) as `other_rejected`."""
    if accepted_rank < -1 or accepted_rank >= AHET_MAX_CANDIDATES:
        raise ValueError(
            f"accepted_candidate_rank={accepted_rank} is outside the valid domain "
            f"{{-1, 0, ..., {AHET_MAX_CANDIDATES - 1}}} for AHET_MAX_CANDIDATES="
            f"{AHET_MAX_CANDIDATES}."
        )
    all_not_run = bool(np.all(rejection_codes == REJECTION_CODE_NOT_ATTEMPTED))
    any_not_run = bool(np.any(rejection_codes == REJECTION_CODE_NOT_ATTEMPTED))
    if any_not_run and not all_not_run:
        raise ValueError(
            f"rejection_codes={rejection_codes.tolist()} mixes 'not-run' (-1) with concrete "
            "codes -- a strict_v1 row either has ALL codes -1 (the gate never ran) or NO -1 "
            "anywhere (an executed gate always assigns every slot a concrete code, including "
            "5 for a never-attempted slot)."
        )
    if accepted_rank >= 0:
        if not np.isfinite(f_r_hz):
            raise ValueError(
                f"accepted_candidate_rank={accepted_rank} (a candidate was accepted) is "
                "contradictory with a non-finite f_r_hz -- ECA/AHET evaluation only runs "
                "when f_r_hz is finite."
            )
        if not (RESP_GATE_LO_HZ <= f_r_hz <= RESP_GATE_HI_HZ):
            raise ValueError(
                f"accepted_candidate_rank={accepted_rank} (a candidate was accepted) is "
                f"contradictory with f_r_hz={f_r_hz}, outside the physiological gate "
                f"[{RESP_GATE_LO_HZ}, {RESP_GATE_HI_HZ}] Hz -- ECA/AHET evaluation only "
                "runs when f_r_hz passes this gate."
            )
        if all_not_run:
            raise ValueError(
                f"accepted_candidate_rank={accepted_rank} is contradictory with all-"
                "not-run rejection codes -- an executed AHET gate always assigns "
                "concrete codes, including 'passed' for the accepted slot."
            )
        if int(rejection_codes[accepted_rank]) != REJECTION_CODE_PASSED:
            raise ValueError(
                f"accepted_candidate_rank={accepted_rank}'s own rejection_codes entry "
                f"is {int(rejection_codes[accepted_rank])}, not the 'passed' code "
                f"({REJECTION_CODE_PASSED})."
            )
        first_passed_idx = int(np.argmax(rejection_codes == REJECTION_CODE_PASSED))
        if first_passed_idx != accepted_rank:
            raise ValueError(
                f"accepted_candidate_rank={accepted_rank} is not the FIRST passed slot -- "
                f"slot {first_passed_idx} is also coded 'passed' in "
                f"rejection_codes={rejection_codes.tolist()}; strict_v1 always selects the "
                "first passing candidate."
            )
        return "covered"
    if bool(np.any(rejection_codes == REJECTION_CODE_PASSED)):
        raise ValueError(
            f"accepted_candidate_rank=-1 is contradictory with a 'passed' "
            f"({REJECTION_CODE_PASSED}) entry in rejection_codes={rejection_codes.tolist()} "
            "-- a passed slot always forces the corresponding non-negative rank."
        )
    if all_not_run:
        f_r_in_gate = np.isfinite(f_r_hz) and (RESP_GATE_LO_HZ <= f_r_hz <= RESP_GATE_HI_HZ)
        if f_r_in_gate:
            raise ValueError(
                f"accepted_candidate_rank=-1 with all-not-run rejection codes is "
                f"contradictory with an in-gate finite f_r_hz={f_r_hz} -- ECA/AHET always "
                "executes (and assigns concrete codes) whenever f_r_hz passes the "
                "physiological gate."
            )
        return "gate_not_run"
    return "other_rejected"


def _require_integer_valued(name: str, arr: np.ndarray) -> None:
    """Reject a boolean, non-finite, or fractional array before any numeric
    cast (BDR-24) -- exact-shape validation alone still let
    `frame_idx=[599.9, 659.9]`/`accepted_rank=[0.9, -1.0]`/fractional
    rejection codes through, after which `int(...)`/`.astype(np.int64)`
    downstream silently truncated them (e.g. 599.9 -> 599, 0.9 -> 0),
    changing alignment/classification instead of failing closed."""
    if arr.dtype == np.bool_:
        raise ValueError(f"{name} has boolean dtype; expected an integer-valued array.")
    if not np.all(np.isfinite(arr)):
        raise ValueError(f"{name} contains non-finite values; expected finite integers.")
    if not np.array_equal(arr, np.round(arr)):
        raise ValueError(f"{name} contains fractional values; expected integer-valued data.")


def _require_rejection_code_domain(rejection_codes: np.ndarray) -> None:
    """Validate every rejection code against the producer's own domain
    (BDR-24) -- `{-1, 0, ..., 7}` (src/vitals.py: -1=gate_not_run, 0=passed,
    1-7=specific rejection reasons). An out-of-domain code (e.g. from a
    corrupted or hand-edited NPZ) would otherwise be accepted as long as it
    happened to be integer-valued."""
    codes_int = rejection_codes.astype(np.int64)
    bad = ~np.isin(codes_int, np.array(sorted(REJECTION_CODE_DOMAIN)))
    if np.any(bad):
        raise ValueError(
            f"candidate_rejection_codes contains values outside the known domain "
            f"{sorted(REJECTION_CODE_DOMAIN)}: {codes_int[bad].tolist()}."
        )


def validate_frame_idx_grid(frame_idx: np.ndarray, window_frames: int, hop_s: float,
                             fs: float, n_cube_frames: int,
                             accepted_rank: np.ndarray, rejection_codes: np.ndarray,
                             f_r_hz: np.ndarray) -> None:
    """Reject a malformed NPZ hop grid before alignment (plan §7.1, BDR-19,
    BDR-19 R2, BDR-19 R3, BDR-24) -- a misanchored first endpoint, an endpoint
    beyond the last complete frame, non-monotonic or off-hop spacing, an
    outcome array whose declared SHAPE (not just row count) does not match
    what the generation guarantees, or one whose VALUES are not the
    integer-valued data the generation guarantees. Raises ValueError; never
    silently proceeds on bad input.

    BDR-19 R2 found the round-1 fix only rejected a first endpoint BELOW
    599 (`[659, 719]` passed, mislabeling row 0 -- which actually spans frames
    60-659 -- as the warmup window), had no cube length with which to reject
    an endpoint past the last complete frame (NumPy silently truncates an
    out-of-range slice), and only checked row COUNT for the outcome arrays.

    BDR-19 R3 found that row-count checking alone still passed a
    `candidate_rejection_codes` array shaped `(n, 1)` or `(n, 2)` instead of
    the generation's true `(n, AHET_MAX_CANDIDATES)`, and a `(n, 1)`
    `accepted_candidate_rank`/`f_r_hz` too (direct testing reproduced all
    three) -- a 2-D scalar field or a wrong-width code row would then either
    fail incidentally downstream or silently change `gate_not_run`
    classification. Every array's exact declared shape is now checked.

    BDR-24 found that exact-shape checking still permitted lossy numeric
    coercion: `frame_idx=[599.9, 659.9]`, `accepted_rank=[0.9, -1.0]`, and
    fractional/out-of-domain rejection codes all passed the shape check, then
    were silently truncated/miscast downstream. `frame_idx`,
    `accepted_candidate_rank`, and `candidate_rejection_codes` (never
    `f_r_hz`, which is a genuine float) must now be finite, non-boolean, and
    integer-valued before any cast; `candidate_rejection_codes` is
    additionally checked against the producer's own code domain."""
    n = len(frame_idx)
    for name, arr, expected_shape in (
        ("frame_idx", frame_idx, (n,)),
        ("accepted_candidate_rank", accepted_rank, (n,)),
        ("f_r_hz", f_r_hz, (n,)),
        ("candidate_rejection_codes", rejection_codes, (n, AHET_MAX_CANDIDATES)),
    ):
        if tuple(arr.shape) != expected_shape:
            raise ValueError(
                f"{name} has shape {tuple(arr.shape)}, expected {expected_shape}; every "
                "NPZ outcome array must be exactly shaped, not just row-count-matched "
                "(BDR-19 R3)."
            )
    for name, arr in (("frame_idx", frame_idx),
                       ("accepted_candidate_rank", accepted_rank),
                       ("candidate_rejection_codes", rejection_codes)):
        _require_integer_valued(name, arr)
    _require_rejection_code_domain(rejection_codes)
    if n == 0:
        return
    first_valid_end = window_frames - 1
    if int(frame_idx[0]) != first_valid_end:
        raise ValueError(
            f"frame_idx[0]={int(frame_idx[0])} does not equal the warmup "
            f"window's own end frame ({first_valid_end}) for a "
            f"{window_frames}-frame window; row 0 would be mislabeled as the "
            f"warmup window over the wrong frame span."
        )
    diffs = np.diff(frame_idx.astype(np.int64))
    if np.any(diffs <= 0):
        raise ValueError(f"frame_idx is not strictly monotonic increasing: diffs={diffs.tolist()}")
    expected_hop = int(round(hop_s * fs))
    bad = np.where(diffs != expected_hop)[0]
    if bad.size:
        raise ValueError(
            f"frame_idx hop spacing does not match hop_s*fs={expected_hop} frames at "
            f"index/indices {bad.tolist()}: diffs={diffs[bad].tolist()}."
        )
    last_end = int(frame_idx[-1])
    if last_end >= n_cube_frames:
        raise ValueError(
            f"frame_idx[-1]={last_end} is beyond the last complete frame index "
            f"({n_cube_frames - 1}) in a {n_cube_frames}-frame cube; NumPy would "
            "silently return a truncated slice for this window."
        )


@dataclass
class WindowRow:
    window_index: int
    frame_start: int
    frame_end: int
    is_warmup_window: bool
    post_calibration_observed_s: float
    #: Directly computed from range_energy_by_bin on THIS window's own
    #: [frame_start, frame_end] slice (BDR-14) -- not the mode/mean of its
    #: constituent 1 s blocks. Those are not equivalent: the argmax of a
    #: 600-frame aggregate's power need not equal the mode of twenty
    #: 1 s-block argmaxes, and likewise for the centroid.
    window_argmax_bin: Optional[int]
    window_centroid: Optional[float]
    off_baseline_duration_s: float
    longest_excursion_s: float
    accepted_candidate_rank: int
    rejection_codes: tuple[int, ...]
    f_r_hz: float
    outcome_class: str
    #: The full per-bin power profile this window's argmax/centroid were
    #: derived from (BDR-14 R2) -- persisted separately so the diagnostic's
    #: central "per-bin energy at the window time scale" measurement can be
    #: independently audited, not just its two derived scalars. Default empty
    #: so existing WindowRow construction sites (tests) are unaffected.
    window_energy_by_bin: dict = field(default_factory=dict)


def align_windows(cube: np.ndarray, candidate_bins: list[int], frame_idx: np.ndarray,
                   blocks: BlockSeries, baseline_bin: int,
                   accepted_rank: np.ndarray, rejection_codes: np.ndarray,
                   f_r_hz: np.ndarray, fs: float, window_frames: int,
                   hop_s: float, calibration_end_frame: int,
                   block_frames: int) -> list[WindowRow]:
    """One row per NPZ window (plan §3.2/§4). The window's own argmax/centroid
    are computed DIRECTLY on its 600-frame slice (BDR-14); off-baseline
    duration and longest excursion still use the finer 1 s-block series
    (a different, correctly-scoped sub-window statistic). Window 0 (the
    calibration stratum itself) is flagged is_warmup_window with zero
    exposure. Raises on a malformed frame_idx grid (BDR-19, BDR-19 R2)."""
    validate_frame_idx_grid(frame_idx, window_frames, hop_s, fs, cube.shape[0],
                             accepted_rank, rejection_codes, f_r_hz)
    block_s = block_frames / fs
    rows: list[WindowRow] = []
    off = blocks.argmax_bin != baseline_bin

    for i, end_frame in enumerate(frame_idx):
        end_frame = int(end_frame)
        start_frame = end_frame - window_frames + 1
        is_warmup = (i == 0)
        observed_s = 0.0 if is_warmup else min(float(window_frames) / fs, i * hop_s)

        window_energies = _energy_by_bin_from_slice(cube, start_frame, end_frame + 1, candidate_bins)
        window_argmax = int(max(window_energies, key=window_energies.get))
        window_centroid = compute_centroid(window_energies)

        mask = (
            (blocks.block_start_frame >= start_frame)
            & (blocks.block_start_frame < end_frame + 1)
            & (blocks.block_start_frame >= calibration_end_frame)
        )
        if mask.any():
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
            off_duration = 0.0
            longest = 0.0

        rows.append(WindowRow(
            window_index=i,
            frame_start=start_frame,
            frame_end=end_frame,
            is_warmup_window=is_warmup,
            post_calibration_observed_s=observed_s,
            window_argmax_bin=window_argmax,
            window_centroid=window_centroid,
            off_baseline_duration_s=off_duration,
            longest_excursion_s=longest,
            accepted_candidate_rank=int(accepted_rank[i]),
            rejection_codes=tuple(int(c) for c in rejection_codes[i]),
            f_r_hz=float(f_r_hz[i]),
            outcome_class=classify_window_outcome(int(accepted_rank[i]), rejection_codes[i], float(f_r_hz[i])),
            window_energy_by_bin=dict(window_energies),
        ))
    return rows


def stratify_windows(rows: list[WindowRow], full_exposure_s: float) -> tuple[list[WindowRow], list[WindowRow]]:
    """Split into (full_exposure, transitional), excluding the warmup window
    entirely (plan §4, BDR-03 R3). `full_exposure_s` is the window duration
    (window_frames / fs) traced from the live config, not a hardcoded
    literal (BDR-19)."""
    eligible = [r for r in rows if not r.is_warmup_window]
    full = [r for r in eligible if r.post_calibration_observed_s >= full_exposure_s - 1e-9]
    transitional = [r for r in eligible if r.post_calibration_observed_s < full_exposure_s - 1e-9]
    return full, transitional


def offset_phase_subsets(rows: list[WindowRow], phases: tuple[int, ...]) -> dict[int, list[WindowRow]]:
    """All 10 disjoint non-overlapping hop-offset phases {k, k+10, k+20, ...}
    (plan §4)."""
    return {k: [r for r in rows if r.window_index % 10 == k] for k in phases}


OUTCOME_CLASSES = ("covered", "gate_not_run", "other_rejected")


def stratify_by_outcome(windows: list[WindowRow]) -> dict[str, dict]:
    """The primary report (plan §4): per-window off-baseline duration and
    longest excursion, stratified by outcome class. Always reports BOTH raw
    seconds and a normalized fraction (`off_baseline_duration_s /
    post_calibration_observed_s`, BDR-17) -- for full-exposure windows the
    fraction is a trivial rescaling (all denominators equal), but for
    transitional windows it is the whole point: raw seconds from a 3 s window
    and a 27 s window are not comparable, and BDR-03 R3's exposure correction
    is void unless the emitted report actually carries the normalized value,
    not just the raw one. Not a per-window Cartesian join with the centroid
    grid (BDR-11) -- outcome class comes only from the DSP rejection-code
    classifier."""
    report: dict[str, dict] = {}
    for cls in OUTCOME_CLASSES:
        subset = [r for r in windows if r.outcome_class == cls]
        if not subset:
            report[cls] = {"n": 0, "mean_off_baseline_duration_s": None,
                            "mean_longest_excursion_s": None,
                            "mean_off_baseline_fraction": None}
            continue
        fractions = [r.off_baseline_duration_s / r.post_calibration_observed_s
                     for r in subset if r.post_calibration_observed_s > 0]
        report[cls] = {
            "n": len(subset),
            "mean_off_baseline_duration_s": float(np.mean([r.off_baseline_duration_s for r in subset])),
            "mean_longest_excursion_s": float(np.mean([r.longest_excursion_s for r in subset])),
            "mean_off_baseline_fraction": float(np.mean(fractions)) if fractions else None,
        }
    return report


def duration_grid_by_outcome(windows: list[WindowRow],
                              duration_grid_s: tuple[float, ...]) -> dict[str, dict]:
    """The per-window duration-grid <-> outcome association BDR-11 R2 found
    missing: for each outcome class, the count of windows (from the set
    passed in -- caller restricts to full-exposure) whose OWN
    `longest_excursion_s` meets or exceeds each grid duration. This is the
    per-window rule the config's sensitivity_grid.duration_s axis actually
    governs; `episodes_at_grid` remains the separate, outcome-blind,
    session-level episode count."""
    report: dict[str, dict] = {}
    for cls in OUTCOME_CLASSES:
        subset = [r for r in windows if r.outcome_class == cls]
        report[cls] = {
            "n": len(subset),
            "count_at_grid": {
                str(d): sum(1 for r in subset if r.longest_excursion_s >= d)
                for d in duration_grid_s
            },
        }
    return report


# ── Motion energy (channel-preserving, per-window) ──────────────────────────

def _motion_energy_slice(slice_cube: np.ndarray, candidate_bins: list[int]) -> dict[int, float]:
    """motion_energy(b) = mean_(t,c,r) |X(t,c,r,b) - mean_t' X(t',c,r,b)|^2
    (plan §3.3) over the given slice. Per-channel mean subtracted BEFORE
    averaging across chirps/RX, so a genuinely moving reflector cannot
    destructively cancel across RX channels with different static phases."""
    n_adc = slice_cube.shape[-1]
    hann = np.hanning(n_adc).astype(np.float32)
    windowed = slice_cube * hann
    range_fft = np.fft.fft(windowed, axis=-1)  # (frames, chirps, rx, n_adc)

    out: dict[int, float] = {}
    for b in candidate_bins:
        X = range_fft[:, :, :, b]                       # (frames, chirps, rx)
        channel_mean = X.mean(axis=0, keepdims=True)     # (1, chirps, rx)
        deviation = X - channel_mean
        out[b] = float(np.mean(np.abs(deviation) ** 2))
    return out


def compute_motion_energy_per_window(cube: np.ndarray, candidate_bins: list[int],
                                      window_rows: list[WindowRow]) -> dict[int, dict[int, float]]:
    """Per-window, per-bin motion-energy matrix (plan §3.3/§4, BDR-18) -- one
    600-frame slice processed at a time, bounded temporaries, not one
    whole-capture FFT. Keyed by `window_index`; empty for a session with no
    NPZ-defined windows (`window_rows` empty, e.g. live_test1 under BDR-07
    Option A -- motion energy is a window-scale statistic and has no
    equivalent for a replay-less session, same as every other window-level
    field)."""
    out: dict[int, dict[int, float]] = {}
    for r in window_rows:
        slice_cube = cube[r.frame_start:r.frame_end + 1]
        out[r.window_index] = _motion_energy_slice(slice_cube, candidate_bins)
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
    #: The ORIGINAL capture's own recorded run_metadata.json -- always loaded from
    #: capture_dir, used for decode-geometry validation (BDR-16). A replay's raw-file
    #: hash proves which BYTES were replayed; it does not prove the replay's config
    #: snapshot is the geometry those bytes were originally captured with.
    capture_run_metadata: dict
    capture_run_metadata_path: Path
    #: The replay's own run_metadata.json (None if there is no replay). Used only for
    #: replay provenance / outcome-generation pairing, never for geometry validation.
    replay_run_metadata: Optional[dict]
    replay_run_metadata_path: Optional[Path]


def match_replays_to_captures(
    capture_dirs: list[Path], replay_dirs: list[Path],
    approved_replays: Optional[dict[str, str]] = None,
) -> dict[Path, Optional[Path]]:
    """Pair each capture to the (at most one) replay whose recorded
    replay_file_hashes matches the capture's raw SHA-256 -- never by CLI
    position (plan §5).

    Raw-hash equality proves only that a replay used the same BYTES; it does
    not prove the replay was produced by an approved estimator GENERATION
    (BDR-20 -- at least six replay directories share massimo1's raw hash
    across generations from 2026-07-15 through 2026-07-27). Two additional
    checks, both fail-closed:

    - Two different replay dirs matching the same capture's raw hash is
      rejected outright (never silently keeps whichever came last on the CLI).
    - If `approved_replays` is given (maps a capture's raw SHA-256 to the
      SHA-256 of that capture's ONE approved replay `run_metadata.json`), the
      matched replay's own `run_metadata.json` hash must equal the approved
      value; a capture with no approval entry at all rejects any replay
      offered for it, and a matching-raw-bytes-but-wrong-generation replay is
      rejected even though its content hash matches.
    """
    capture_hashes = {c: sha256_file(c / "adc_stream.bin") for c in capture_dirs}
    mapping: dict[Path, Optional[Path]] = {c: None for c in capture_dirs}
    claimed_by_capture: dict[Path, Path] = {}

    for r in replay_dirs:
        meta_path = r / "run_metadata.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
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
        if found in claimed_by_capture:
            raise ValueError(
                f"capture {found} matches more than one replay by raw hash: "
                f"{claimed_by_capture[found]} and {r}. Pass exactly one "
                f"replay per capture (BDR-20)."
            )
        if approved_replays is not None:
            approved_meta_hash = approved_replays.get(replay_hash)
            replay_meta_hash = sha256_file(meta_path)
            if approved_meta_hash is None:
                raise ValueError(
                    f"capture {found} (raw hash {replay_hash}) has no approved "
                    f"replay registered in diagnose_bin_drift_config.yaml: "
                    f"approved_replays; refusing to pair replay {r} without an "
                    f"explicit approval (BDR-20)."
                )
            if replay_meta_hash != approved_meta_hash:
                raise ValueError(
                    f"replay {r} (run_metadata.json sha256 {replay_meta_hash}) "
                    f"is not the approved generation for capture {found} "
                    f"(expected {approved_meta_hash}) (BDR-20)."
                )
        claimed_by_capture[found] = r
        mapping[found] = r
    return mapping


def load_session_inputs(session_id: str, capture_dir: Path, replay_dir: Optional[Path]) -> SessionInputs:
    raw_path = capture_dir / "adc_stream.bin"
    raw_sha256 = sha256_file(raw_path)

    # The capture's OWN metadata -- always loaded from capture_dir, the only input
    # decode-geometry validation may use (BDR-16).
    capture_run_metadata_path = capture_dir / "run_metadata.json"
    capture_run_metadata = json.loads(capture_run_metadata_path.read_text(encoding="utf-8"))

    # Warmup evidence and DSP outcomes come from the replay when one is matched (it
    # reflects the estimator generation that actually produced those outcomes), else
    # from the capture's own original run.
    evidence_dir = replay_dir if replay_dir is not None else capture_dir
    warmup_json_path = evidence_dir / "warmup_bin_selection.json"
    warmup_json = json.loads(warmup_json_path.read_text(encoding="utf-8"))

    if replay_dir is not None:
        replay_run_metadata_path = replay_dir / "run_metadata.json"
        replay_run_metadata = json.loads(replay_run_metadata_path.read_text(encoding="utf-8"))
        replay_hashes = list((replay_run_metadata.get("replay_file_hashes") or {}).values())
        if not replay_hashes or replay_hashes[0] != raw_sha256:
            raise ValueError(
                f"session {session_id!r}: replay {replay_dir} does not match raw "
                f"capture {raw_path} (SHA-256 mismatch)."
            )
        npz_path = replay_dir / "live_intermediates.npz"
        npz = dict(np.load(npz_path, allow_pickle=True))
    else:
        replay_run_metadata_path = None
        replay_run_metadata = None
        npz_path = None
        npz = None

    return SessionInputs(
        session_id=session_id, capture_dir=capture_dir, replay_dir=replay_dir,
        raw_sha256=raw_sha256, warmup_json=warmup_json, warmup_json_path=warmup_json_path,
        npz=npz, npz_path=npz_path,
        capture_run_metadata=capture_run_metadata,
        capture_run_metadata_path=capture_run_metadata_path,
        replay_run_metadata=replay_run_metadata,
        replay_run_metadata_path=replay_run_metadata_path,
    )


@dataclass(frozen=True)
class RunContext:
    """The one run-level manifest, embedded into EVERY session's own
    summary.json (BDR-22), not just the parent run_summary.json -- so a
    session directory cited or copied apart from its parent is still
    independently bound to the commit/config/run that produced it."""
    run_id: str
    git_commit: str
    diagnostic_config_path: str
    diagnostic_config_sha256: str
    live_demo_config_path: str
    live_demo_config_sha256: str


# ── Per-session pipeline ─────────────────────────────────────────────────────

def run_session(session: SessionInputs, live_cfg: dict, diag_cfg: DiagnosticConfig,
                 run_ctx: "RunContext", out_dir: Path) -> dict:
    chirp_cfg = validate_decode_geometry(live_cfg, session.capture_run_metadata, session.session_id)
    candidate_bins = derive_candidate_bins(live_cfg)

    mem_before = get_peak_working_set_bytes()
    mem_available_preflight = preflight_check_memory(diag_cfg, session.session_id)  # BDR-18
    cube = read_adc_bin(session.capture_dir / "adc_stream.bin", chirp_cfg)
    mem_after_decode = get_peak_working_set_bytes()

    baseline = compute_baseline(cube, candidate_bins, diag_cfg)
    recompute = warmup_recompute_check(cube, candidate_bins, diag_cfg, session.warmup_json)

    locked_bin = int(session.warmup_json["selected_bin"])
    lock_candidates = {int(c["bin"]): c for c in session.warmup_json["candidates"]}
    # Rank the locked bin within the diagnostic's OWN settled baseline profile
    # (frames 100-599), not the full-buffer warmup-JSON energy_rank (BDR-21 --
    # those are different quantities; the full-buffer rank happens to agree on
    # all four real sessions today, but is not what this field's name claims).
    baseline_rank_of_lock = rank_of_bin_in_profile(baseline["settled_energy_by_bin"], locked_bin)
    full_buffer_warmup_rank_of_lock = None
    if "energy_rank" in lock_candidates.get(locked_bin, {}):
        full_buffer_warmup_rank_of_lock = int(lock_candidates[locked_bin]["energy_rank"])

    blocks = compute_block_series(cube, candidate_bins, diag_cfg)
    fs = float(live_cfg["session"]["frame_rate_hz"])
    occupancy = compute_occupancy(blocks, baseline["baseline_argmax_bin"])
    episodes = detect_episodes(blocks, baseline["baseline_argmax_bin"], fs, diag_cfg.block_frames,
                                diag_cfg.gap_rule)
    episode_grid = episodes_at_grid(episodes, diag_cfg.duration_grid_s)

    trailing_centroid_median, leading_centroid_median, centroid_n_blocks_used = (
        trailing_leading_centroid_medians(blocks, diag_cfg, fs)
    )
    centroid_grid = centroid_drift_at_grid(trailing_centroid_median, leading_centroid_median,
                                            diag_cfg.centroid_grid_bins)

    window_rows: list[WindowRow] = []
    if session.npz is not None:
        npz = session.npz
        window_s = float(live_cfg["session"]["window_s"])  # traced from config, not hardcoded (BDR-19)
        window_frames = int(round(window_s * fs))
        hop_s = float(live_cfg["session"]["hop_s"])
        window_rows = align_windows(
            cube, candidate_bins, npz["frame_idx"], blocks, baseline["baseline_argmax_bin"],
            npz["accepted_candidate_rank"], npz["candidate_rejection_codes"],
            npz["f_r_hz"], fs, window_frames, hop_s, diag_cfg.stratum_frames[1] + 1,
            diag_cfg.block_frames,
        )
    else:
        window_s = float(live_cfg["session"]["window_s"])

    full_exposure, transitional = stratify_windows(window_rows, window_s)
    outcome_stratified_report = stratify_by_outcome(full_exposure)
    outcome_stratified_transitional = stratify_by_outcome(transitional)
    duration_grid_report = duration_grid_by_outcome(full_exposure, diag_cfg.duration_grid_s)

    # All 10 disjoint non-overlapping hop-offset phases, each independently
    # split into its own full-exposure/transitional stratum (plan §4) --
    # computed and PERSISTED, not just derivable from window_audit.csv.
    phase_subsets = offset_phase_subsets(window_rows, diag_cfg.offset_phases)
    offset_phase_report = {}
    for k, subset in phase_subsets.items():
        phase_full, phase_transitional = stratify_windows(subset, window_s)
        offset_phase_report[str(k)] = {
            "n_full_exposure_windows": len(phase_full),
            "n_transitional_windows": len(phase_transitional),
            "full_exposure": stratify_by_outcome(phase_full),
            "transitional": stratify_by_outcome(phase_transitional),
            "duration_grid_by_outcome": duration_grid_by_outcome(phase_full, diag_cfg.duration_grid_s),
        }

    # Bounded per-window motion energy (BDR-18) -- one 600-frame slice at a time,
    # computed before the cube is freed, AFTER all other per-session work so the
    # end-of-session memory sample below reflects the whole pipeline.
    motion_energy_per_window = compute_motion_energy_per_window(cube, candidate_bins, window_rows)

    mem_after_session = get_peak_working_set_bytes()  # BDR-18: not just post-decode
    del cube  # free the decoded cube before writing output artifacts

    reproducible = is_tree_clean() if diag_cfg.require_clean_tree else None

    result = {
        # Own run manifest (BDR-22) -- previously only the parent
        # run_summary.json carried these; a session directory cited or copied
        # apart from its parent had no independent binding to the commit,
        # config, or run that produced it.
        "run_id": run_ctx.run_id,
        "git_commit": run_ctx.git_commit,
        "diagnostic_config_path": run_ctx.diagnostic_config_path,
        "diagnostic_config_sha256": run_ctx.diagnostic_config_sha256,
        "live_demo_config_path": run_ctx.live_demo_config_path,
        "live_demo_config_sha256": run_ctx.live_demo_config_sha256,
        "session_id": session.session_id,
        # BDR-22 R2: the exact raw input path, not just its hash -- a session
        # summary is not a self-contained manifest without it (session_id is
        # only the capture directory's basename, not the CLI input path).
        "raw_path": str(session.capture_dir / "adc_stream.bin"),
        "raw_sha256": session.raw_sha256,
        "warmup_json_path": str(session.warmup_json_path),
        "warmup_json_sha256": sha256_file(session.warmup_json_path),
        "capture_run_metadata_path": str(session.capture_run_metadata_path),
        "capture_run_metadata_sha256": sha256_file(session.capture_run_metadata_path),
        "replay_run_metadata_path": str(session.replay_run_metadata_path) if session.replay_run_metadata_path else None,
        "replay_run_metadata_sha256": sha256_file(session.replay_run_metadata_path) if session.replay_run_metadata_path else None,
        "npz_path": str(session.npz_path) if session.npz_path else None,
        "npz_sha256": sha256_file(session.npz_path) if session.npz_path else None,
        "correlation_available": session.npz is not None,
        "locked_bin": locked_bin,
        "baseline_argmax_bin": baseline["baseline_argmax_bin"],
        "baseline_centroid": baseline["baseline_centroid"],
        "baseline_rank_of_locked_bin": baseline_rank_of_lock,
        "full_buffer_warmup_rank_of_locked_bin": full_buffer_warmup_rank_of_lock,
        "baseline_profile": {str(b): e for b, e in baseline["settled_energy_by_bin"].items()},
        "occupancy": occupancy,
        "warmup_recompute_check": recompute,
        "centroid_drift": {
            # Neutral field names + the configured span/block count actually
            # used (BDR-23 R2) -- "trailing_10s_median" hardcoded "10s"
            # regardless of the configured summary_span_s, so a run with a
            # different (validly configured) span would have mislabeled its
            # own statistic.
            "summary_span_s": diag_cfg.centroid_summary_span_s,
            "n_blocks_used": centroid_n_blocks_used,
            "trailing_median": trailing_centroid_median,
            "leading_median": leading_centroid_median,
        },
        "n_blocks": int(len(blocks.block_start_frame)),
        "trailing_discarded_frames": blocks.trailing_discarded_frames,
        "episodes": [
            {"start_s": e.start_s, "end_s": e.end_s, "bin_sequence": e.bin_sequence,
             "modal_bin": e.modal_bin, "max_displacement_bins": e.max_displacement_bins}
            for e in episodes
        ],
        "episode_count_at_grid": {str(d): n for d, n in episode_grid.items()},
        "centroid_drift_at_grid": {str(b): met for b, met in centroid_grid.items()},
        "n_windows": len(window_rows),
        "n_full_exposure_windows": len(full_exposure),
        "n_transitional_windows": len(transitional),
        "outcome_stratified_report": {
            "full_exposure": outcome_stratified_report,
            "transitional": outcome_stratified_transitional,
        },
        "duration_grid_by_outcome": duration_grid_report,
        "offset_phase_report": offset_phase_report,
        "reproducible": reproducible,
        "mem_available_preflight": mem_available_preflight,
        "mem_peak_working_set_before_decode": mem_before,
        "mem_peak_working_set_after_decode": mem_after_decode,
        "mem_peak_working_set_after_session": mem_after_session,
    }

    session_dir = _write_session_outputs(out_dir, session.session_id, blocks, window_rows,
                                          motion_energy_per_window, result)
    plot_drift_overview(session_dir, blocks, baseline["baseline_argmax_bin"], window_rows, fs)
    return result


# ── Window-scale ordinary energy (BDR-14 R2) ────────────────────────────────

def build_window_energy_matrix(
    window_rows: list[WindowRow], candidate_bins: list[int],
    baseline_by_bin: dict[int, float],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The ordinary (non-motion) per-window per-bin power profile, raw +
    baseline-relative dB (BDR-14 R2). `align_windows` already computes this
    profile directly on each window's own 600-frame slice to derive
    `window_argmax_bin`/`window_centroid`, but previously discarded it --
    `bin_energy_blocks.csv` only ever carried the 1 s-block-scale matrix, and
    `motion_energy_windows.npz` is a different statistic (a channel-preserving
    temporal-variance measure) that cannot audit this one. Returns
    `(window_indices, matrix, matrix_rel_baseline_db)`."""
    window_indices = np.array([r.window_index for r in window_rows], dtype=int)
    n = len(window_rows)
    matrix = np.full((n, len(candidate_bins)), np.nan, dtype=float)
    matrix_rel_db = np.full((n, len(candidate_bins)), np.nan, dtype=float)
    for i, r in enumerate(window_rows):
        for j, b in enumerate(candidate_bins):
            e = r.window_energy_by_bin.get(b, float("nan"))
            matrix[i, j] = e
            base = baseline_by_bin.get(b, 0.0)
            matrix_rel_db[i, j] = 10.0 * np.log10(e / base) if (e > 0 and base > 0) else float("-inf")
    return window_indices, matrix, matrix_rel_db


def write_window_energy_npz(
    session_dir: Path, window_rows: list[WindowRow], candidate_bins: list[int],
    baseline_by_bin: dict[int, float],
) -> Path:
    """Persist the per-window ordinary energy profile (BDR-14 R2): explicit
    `window_indices`, `bins`, a real `(n_windows, n_bins)` power matrix, and
    its baseline-relative dB counterpart -- so the window-scale statistic can
    be independently audited (every saved `window_argmax_bin`/`window_centroid`
    must be exactly recomputable from this file) without re-decoding the raw
    capture."""
    window_indices, matrix, matrix_rel_db = build_window_energy_matrix(
        window_rows, candidate_bins, baseline_by_bin
    )
    path = session_dir / "window_energy_windows.npz"
    np.savez(
        path,
        window_indices=window_indices,
        bins=np.array(candidate_bins, dtype=int),
        matrix=matrix,
        matrix_rel_baseline_db=matrix_rel_db,
    )
    return path


# ── Output writers ───────────────────────────────────────────────────────────

def _write_session_outputs(out_dir: Path, session_id: str, blocks: BlockSeries,
                            window_rows: list[WindowRow],
                            motion_energy_per_window: dict[int, dict[int, float]],
                            summary_fragment: dict) -> Path:
    session_dir = out_dir / session_id
    session_dir.mkdir(parents=True, exist_ok=False)

    import csv
    baseline_by_bin = {int(b): e for b, e in summary_fragment["baseline_profile"].items()}
    with (session_dir / "bin_energy_blocks.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        # Full per-bin energy matrix (raw + baseline-relative dB), not just the
        # derived argmax/centroid summary statistics (BDR-14).
        header = ["block_index", "block_start_frame", "argmax_bin", "centroid"]
        for b in blocks.candidate_bins:
            header += [f"energy_bin_{b}", f"energy_rel_baseline_db_bin_{b}"]
        w.writerow(header)
        for i in range(len(blocks.block_start_frame)):
            row = [i, int(blocks.block_start_frame[i]), int(blocks.argmax_bin[i]),
                   float(blocks.centroid[i])]
            for j, b in enumerate(blocks.candidate_bins):
                e = float(blocks.energy_matrix[i, j])
                base = baseline_by_bin.get(b, 0.0)
                rel_db = 10.0 * np.log10(e / base) if (e > 0 and base > 0) else float("-inf")
                row += [e, rel_db]
            w.writerow(row)

    with (session_dir / "window_audit.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["window_index", "frame_start", "frame_end", "is_warmup_window",
                     "post_calibration_observed_s", "window_argmax_bin", "window_centroid",
                     "off_baseline_duration_s", "longest_excursion_s",
                     "accepted_candidate_rank", "rejection_codes",
                     "f_r_hz", "outcome_class"])
        for r in window_rows:
            w.writerow([r.window_index, r.frame_start, r.frame_end, r.is_warmup_window,
                        r.post_calibration_observed_s, r.window_argmax_bin, r.window_centroid,
                        r.off_baseline_duration_s, r.longest_excursion_s,
                        r.accepted_candidate_rank,
                        ";".join(str(c) for c in r.rejection_codes), r.f_r_hz, r.outcome_class])

    # Real (n_windows, n_bins) motion-energy matrix (BDR-18), not one scalar
    # per bin for the whole capture. Empty (0, n_bins) when there are no
    # NPZ-defined windows (e.g. live_test1, BDR-07 Option A).
    window_indices = sorted(motion_energy_per_window.keys())
    me_bins = blocks.candidate_bins
    me_matrix = np.zeros((len(window_indices), len(me_bins)), dtype=float)
    for i, widx in enumerate(window_indices):
        row = motion_energy_per_window[widx]
        me_matrix[i, :] = [row.get(b, float("nan")) for b in me_bins]
    np.savez(
        session_dir / "motion_energy_windows.npz",
        window_indices=np.array(window_indices, dtype=int),
        bins=np.array(me_bins, dtype=int),
        matrix=me_matrix,
    )

    # The ordinary per-window per-bin energy profile (BDR-14 R2) -- separate
    # from motion energy above, and separate from the 1 s-block matrix in
    # bin_energy_blocks.csv.
    write_window_energy_npz(session_dir, window_rows, blocks.candidate_bins, baseline_by_bin)

    with (session_dir / "summary.json").open("w", encoding="utf-8") as fh:
        json.dump(summary_fragment, fh, indent=2, default=str)

    return session_dir


_OUTCOME_COLORS = {
    "covered": ("#2a9d5c", "o"),
    "gate_not_run": ("#d1495b", "s"),
    "other_rejected": ("#e0a72a", "^"),
    "warmup": ("#6b6b6b", "D"),
}


def plot_drift_overview(session_dir: Path, blocks: BlockSeries, baseline_bin: int,
                         window_rows: list[WindowRow], fs: float) -> None:
    """Energy heatmap (time x bin, dB rel. per-block max) + baseline line +
    argmax/centroid overlay + a window-outcome strip (BDR-14 -- the plan's
    original spec, not the two-line plot this diagnostic shipped with)."""
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams.update({
        "figure.autolayout": False, "text.usetex": False, "font.family": "DejaVu Sans",
    })
    import matplotlib.pyplot as plt

    t = blocks.block_start_frame / fs
    n_blocks, n_bins = blocks.energy_matrix.shape
    has_strip = len(window_rows) > 0

    if has_strip:
        fig, (ax, ax_strip) = plt.subplots(
            2, 1, figsize=(11, 5.5), sharex=True, layout="constrained",
            gridspec_kw={"height_ratios": [4, 1], "hspace": 0.08},
        )
    else:
        fig, ax = plt.subplots(figsize=(11, 4), layout="constrained")
        ax_strip = None

    if n_blocks > 0:
        per_block_max = np.maximum(blocks.energy_matrix.max(axis=1, keepdims=True), 1e-30)
        db_rel = 10.0 * np.log10(np.maximum(blocks.energy_matrix, 1e-30) / per_block_max)
        block_s = (t[1] - t[0]) if n_blocks > 1 else 1.0
        extent = [t[0] - block_s / 2, t[-1] + block_s / 2,
                  blocks.candidate_bins[0] - 0.5, blocks.candidate_bins[-1] + 0.5]
        im = ax.imshow(db_rel.T, aspect="auto", origin="lower", extent=extent,
                        cmap="viridis", vmin=-20, vmax=0)
        cbar = fig.colorbar(im, ax=ax, pad=0.01)
        cbar.set_label("dB rel. per-block max")

    ax.plot(t, blocks.argmax_bin, color="#f4f4f4", linewidth=1.3, label="argmax bin")
    ax.plot(t, blocks.centroid, color="#ff8c3b", linewidth=1.0, linestyle="--",
            label="power-weighted centroid")
    ax.axhline(baseline_bin, color="#ffffff", linewidth=1.0, linestyle=":",
               label=f"baseline (bin {baseline_bin})")
    ax.set_ylabel("range bin")
    ax.legend(loc="upper right", frameon=True, fontsize=8)
    ax.set_title("Bin-drift evidence summary")

    if has_strip and ax_strip is not None:
        for cls, (color, marker) in _OUTCOME_COLORS.items():
            if cls == "warmup":
                rows = [r for r in window_rows if r.is_warmup_window]
            else:
                rows = [r for r in window_rows if not r.is_warmup_window and r.outcome_class == cls]
            if not rows:
                continue
            xs = [r.frame_end / fs for r in rows]
            ax_strip.scatter(xs, [0] * len(xs), color=color, marker=marker, s=22,
                              label=cls, zorder=3)
        ax_strip.set_yticks([])
        ax_strip.set_xlabel("time since capture start (s)")
        ax_strip.legend(loc="upper right", ncol=4, frameon=True, fontsize=7,
                         handletextpad=0.3, columnspacing=0.8)
    else:
        ax.set_xlabel("time since capture start (s)")

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

    # diag_cfg.approved_replays is always a dict (possibly empty) once loaded
    # from a real config file, so the approved-generation check is always
    # enforced for a production run -- a replay for a capture with no
    # registered entry is rejected, not silently permitted (BDR-20).
    mapping = match_replays_to_captures(args.captures, args.replays, diag_cfg.approved_replays)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out / run_id
    if out_dir.exists():
        print(f"ERROR: run directory {out_dir} already exists.", file=sys.stderr)
        sys.exit(1)
    out_dir.mkdir(parents=True)

    run_ctx = RunContext(
        run_id=run_id,
        git_commit=get_git_commit(),
        diagnostic_config_path=str(args.diagnostic_config),
        diagnostic_config_sha256=diag_cfg.sha256,
        live_demo_config_path=str(args.config),
        live_demo_config_sha256=sha256_file(args.config),
    )
    run_manifest = {
        "run_id": run_ctx.run_id,
        "git_commit": run_ctx.git_commit,
        "diagnostic_config_path": run_ctx.diagnostic_config_path,
        "diagnostic_config_sha256": run_ctx.diagnostic_config_sha256,
        "live_demo_config_path": run_ctx.live_demo_config_path,
        "live_demo_config_sha256": run_ctx.live_demo_config_sha256,
        "sessions": {},
    }

    for capture_dir, replay_dir in mapping.items():
        session_id = capture_dir.name
        session = load_session_inputs(session_id, capture_dir, replay_dir)
        result = run_session(session, live_cfg, diag_cfg, run_ctx, out_dir)
        run_manifest["sessions"][session_id] = result
        print(f"{session_id}: baseline_argmax_bin={result['baseline_argmax_bin']} "
              f"locked_bin={result['locked_bin']} "
              f"n_episodes={len(result['episodes'])} "
              f"episode_count_at_grid={result['episode_count_at_grid']} "
              f"centroid_drift_at_grid={result['centroid_drift_at_grid']} "
              f"correlation_available={result['correlation_available']}")

    with (out_dir / "run_summary.json").open("w", encoding="utf-8") as fh:
        json.dump(run_manifest, fh, indent=2, default=str)

    print(f"Artifacts: {out_dir}")


if __name__ == "__main__":
    main()
