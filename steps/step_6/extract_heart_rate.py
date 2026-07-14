#!/usr/bin/env python3
"""Step 6: Heart rate extraction from locked-bin HDF5 cubes.

Pipeline position:
  step_1 -> step_2 -> step_3 -> range_plot QC -> step_4 -> step_5 -> step_6

Reads:
  - Time-domain HDF5 cube from Step 2
  - Quality mask from Step 4 (inside HDF5)
  - Breathing-rate contract CSV from Step 5
  - Manifest for session metadata (locked_bin, locked_range_m, stationary_intervals)
  - Masimo CSV for ground-truth HR comparison

Writes:
  - results/<session_id>/step_6/heart_windows.csv      (full per-window diagnostics)
  - results/<session_id>/step_6/summary.json
  - results/<session_id>/step_6/heart_intermediates.npz
  - results/<session_id>/step_6/hr_comparison.png
  - results/<session_id>/step_6/method_agreement.png
  - results/<session_id>/step_6/phase_and_quality.png
  - data/processed/heart_rate/<session_id>.csv          (Step 7 contract)

In v1, only AHET-verified, non-gated, non-harmonic-coincident windows produce finite
radar_hr_bpm.  All other windows are preserved with radar_hr_bpm = NaN and a clear
invalid_reason so every NaN is diagnosable.

Run from repo root:
    python -X utf8 steps/step_6/extract_heart_rate.py --session test3
    python -X utf8 steps/step_6/extract_heart_rate.py --all
    python -X utf8 steps/step_6/extract_heart_rate.py --session test3 --overwrite
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import h5py
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src import masimo as masimo_mod                  # noqa: E402
from src.respiration import extract_chest_phase        # noqa: E402
from src.vitals import (                               # noqa: E402
    AHET_MAX_CANDIDATES,
    estimate_rate_from_phase,
    remove_impulse_noise,
)
from steps.step_6.temporal_tracker import apply_tracker  # noqa: E402

SEP  = "=" * 72
LINE = "-" * 72

# heart_spectrum_stage: numeric code (NPZ) <-> string enum (CSV)
_STAGE_TO_STR = {-1: "skipped", 0: "raw_fft", 1: "after_eca", 2: "after_ahet"}
_STR_TO_STAGE = {v: k for k, v in _STAGE_TO_STR.items()}

# candidate_rejection_code: int -> human-readable string for CSV
_REJECTION_CODE_STR: dict[int, str] = {
    -1: "gate_not_run",
    0:  "passed",
    1:  "no_second_harmonic_region",
    2:  "ratio_db_low",
    3:  "prominence_low",
    4:  "low_candidate_competitor",
    5:  "not_attempted",
    6:  "peak_to_floor_db_low",
    7:  "low_candidate_floor_db_low",
}

_CONFIDENCE_ORDER = {"high": 3, "medium": 2, "low": 1, "none": 0}

_INVALID_REASON_PRIORITY = [
    "quality_gated",
    "resp_match_too_far",
    "resp_invalid",
    "resp_edge_locked",
    "resp_missing",
    "ahet_failed",
    "resp_harmonic_coincident",
]

_STEP3_REQUIRED = [
    "locked_bin", "locked_range_m",
    "chest_bin_confidence", "chest_bin_review_required",
]
_STEP5_EDGE_LOCK_COLS = ["edge_locked", "edge_lock_side"]

# Columns included in the Step 7 contract CSV (compact subset of heart_windows.csv)
_CONTRACT_COLS = [
    "session_id", "window_index", "start_epoch", "end_epoch",
    "start_frame", "end_frame", "locked_bin", "locked_range_m",
    "radar_hr_bpm", "heart_peak_hz", "hr_valid", "hr_confidence", "invalid_reason",
    "hr_source",
    "quality_gated", "n_bad_frames", "bad_fraction",
    "matched_rr_window_index", "resp_match_distance_s",
    "radar_rr_bpm", "resp_peak_hz", "resp_valid", "resp_confidence",
    "resp_edge_locked", "resp_edge_lock_side",
    "masimo_pr_bpm", "masimo_pr_std_bpm", "masimo_n_total", "masimo_n_good_pi",
    "masimo_coverage_fraction", "masimo_good_pi_fraction", "masimo_low_quality",
    "hr_error_bpm", "hr_abs_error_bpm",
    # Tracker evidence
    "tracker_decision_type",
    "tracker_selected_candidate_rank",
    "tracker_selected_candidate_refined_hz",
    "tracker_node_score",
    "tracker_fundamental_ratio_db",
    "tracker_peak_to_floor_ratio_db",
    "tracker_candidate_prominence",
    "tracker_distance_to_resp_harmonic_bpm",
    "tracker_within_resp_harmonic_guard",
    # Pre-tracker AHET backup
    "pre_tracker_radar_hr_bpm",
    "pre_tracker_hr_valid",
    "pre_tracker_hr_confidence",
    "pre_tracker_invalid_reason",
    "pre_tracker_hr_error_bpm",
    "pre_tracker_hr_abs_error_bpm",
]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def _match_resp_window(
    hr_start_epoch: float,
    hr_end_epoch: float,
    resp_df: pd.DataFrame,
) -> tuple[int, float]:
    """Match an HR window to the best Step 5 breathing row.

    Priority:
    1. Find rows whose [start_epoch, end_epoch) contains the HR window center.
    2. If multiple containments: choose maximum overlap, then closest center.
    3. If no containment: choose closest Step 5 center.

    Returns (iloc_position, resp_match_distance_s).
    resp_match_distance_s is always the absolute center-to-center distance.
    """
    hr_center = (hr_start_epoch + hr_end_epoch) / 2.0
    starts  = resp_df["start_epoch"].values
    ends    = resp_df["end_epoch"].values
    centers = (starts + ends) / 2.0

    contained = np.where((starts <= hr_center) & (ends > hr_center))[0]

    if len(contained) == 0:
        dists    = np.abs(centers - hr_center)
        best_pos = int(np.argmin(dists))
    elif len(contained) == 1:
        best_pos = int(contained[0])
    else:
        # Maximum overlap with the HR window
        overlaps = (
            np.minimum(hr_end_epoch, ends[contained])
            - np.maximum(hr_start_epoch, starts[contained])
        )
        max_overlap = overlaps.max()
        overlap_ties = contained[overlaps >= max_overlap - 1e-9]
        if len(overlap_ties) == 1:
            best_pos = int(overlap_ties[0])
        else:
            dists    = np.abs(centers[overlap_ties] - hr_center)
            best_pos = int(overlap_ties[int(np.argmin(dists))])

    matched_center = centers[best_pos]
    distance       = abs(hr_center - matched_center)
    return best_pos, float(distance)


def _check_eca_forbidden_zone(
    resp_peak_hz: float,
    heart_band_hz: tuple[float, float],
    k_max: int,
) -> bool:
    """Return True if any respiratory harmonic k*f_r falls inside the cardiac band."""
    lo, hi = heart_band_hz
    for k in range(1, k_max + 1):
        f = k * resp_peak_hz
        if lo <= f <= hi:
            return True
    return False


def _check_resp_harmonic_coincident(
    heart_peak_hz: float,
    resp_peak_hz: float,
    resp_harmonic_guard_hz: float,
    heart_band_hz: tuple[float, float],
) -> bool:
    """Return True if the selected heart peak is too close to any respiratory harmonic.

    Checks k = 1..10 to cover all harmonics that could plausibly fall in the cardiac
    band or just outside it.  Uses Hz throughout (no bpm conversion).
    """
    if not (np.isfinite(heart_peak_hz) and np.isfinite(resp_peak_hz)):
        return False
    lo, hi = heart_band_hz
    for k in range(1, 11):
        f_harm = k * resp_peak_hz
        if f_harm < lo * 0.5:  # way below band — skip for efficiency
            continue
        if abs(heart_peak_hz - f_harm) <= resp_harmonic_guard_hz:
            return True
    return False


def _get_invalid_reason(flags: dict) -> str | None:
    """Return the highest-priority invalid_reason, or None if all checks pass."""
    for reason in _INVALID_REASON_PRIORITY:
        if flags.get(reason, False):
            return reason
    return None


def _assign_hr_confidence(
    invalid_reason: str | None,
    heart_spectrum_stage: int,
    ahet_verified: bool,
) -> str:
    """Assign hr_confidence from plan spec.

    high   : AHET accepted, no invalid reason.
    medium : ECA ran and AHET failed (after_eca stage), invalid_reason=ahet_failed.
    low    : estimator ran but produced nothing useful, or harmonic coincidence.
    none   : estimator skipped entirely.
    """
    if invalid_reason is None and ahet_verified:
        return "high"
    if heart_spectrum_stage == -1:       # skipped
        return "none"
    if invalid_reason == "ahet_failed":
        # medium only when ECA ran but AHET failed (after_eca); otherwise low
        return "medium" if heart_spectrum_stage == 1 else "low"
    # resp_harmonic_coincident or other post-run rejection
    return "low"


def _compute_masimo_pr(
    mas_df: pd.DataFrame,
    start_epoch: float,
    end_epoch: float,
    min_pi: float,
    min_coverage_frac: float,
    min_good_pi_frac: float,
) -> dict:
    """Compute Masimo PR reference stats for a window, per Step 6 spec.

    Coverage = n_total / expected_count (expected = round(window_s) for 1 Hz Masimo).
    PR mean and std are computed over PI-good samples only.
    masimo_low_quality = True when coverage OR good-PI fraction is below threshold.
    """
    _nan = float("nan")
    expected = max(1, round(end_epoch - start_epoch))
    w        = masimo_mod.window(mas_df, start_epoch, end_epoch)
    n_total  = len(w)

    if n_total == 0:
        return {
            "masimo_pr_bpm":          _nan,
            "masimo_pr_std_bpm":      _nan,
            "masimo_n_total":         0,
            "masimo_n_good_pi":       0,
            "masimo_coverage_fraction": 0.0,
            "masimo_good_pi_fraction":  0.0,
            "masimo_low_quality":      True,
        }

    good     = w[w["pi"] >= min_pi]
    n_good   = len(good)
    coverage = n_total / expected
    good_pi_frac = n_good / n_total

    if n_good > 0:
        pr_mean = float(good["pr_bpm"].mean())
        pr_std  = float(good["pr_bpm"].std(ddof=0))
    else:
        pr_mean = _nan
        pr_std  = _nan

    low_quality = (coverage < min_coverage_frac) or (good_pi_frac < min_good_pi_frac)

    return {
        "masimo_pr_bpm":          pr_mean,
        "masimo_pr_std_bpm":      pr_std,
        "masimo_n_total":         n_total,
        "masimo_n_good_pi":       n_good,
        "masimo_coverage_fraction": float(coverage),
        "masimo_good_pi_fraction":  float(good_pi_frac),
        "masimo_low_quality":      bool(low_quality),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_bool(val, default: bool = False) -> bool:
    """Robustly parse a boolean from a CSV cell.

    Handles numpy/Python bool, int 0/1, string "True"/"False" (any case),
    empty string, and NaN — all without the bool("False") == True footgun.
    Returns `default` for missing/NaN/empty.
    """
    if val is None:
        return default
    # NaN (float nan or pandas NA)
    try:
        if val != val:   # nan != nan
            return default
    except (TypeError, ValueError):
        pass
    if isinstance(val, (bool, np.bool_)):
        return bool(val)
    if isinstance(val, (int, np.integer)):
        return bool(val != 0)
    if isinstance(val, (float, np.floating)):
        return bool(val != 0)  # 1.0 -> True, 0.0 -> False (NaN already handled above)
    s = str(val).strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no", ""):
        return False
    return default


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------

def _nan_hr_row(
    session_id: str,
    window_index: int,
    start_epoch: float,
    end_epoch: float,
    start_frame: int,
    end_frame: int,
    locked_bin: int,
    locked_range_m: float,
    quality_gated: bool,
    n_bad_frames: int,
    bad_fraction: float,
    resp_match: dict,
    masimo: dict,
    invalid_reason: str,
    hr_confidence: str,
) -> dict:
    _nan = float("nan")
    err_bpm  = _nan
    abs_err  = _nan
    return {
        # Window metadata
        "session_id":          session_id,
        "window_index":        window_index,
        "start_epoch":         start_epoch,
        "end_epoch":           end_epoch,
        "start_frame":         start_frame,
        "end_frame":           end_frame,
        # Range metadata
        "locked_bin":          locked_bin,
        "locked_range_m":      locked_range_m,
        # Primary HR
        "radar_hr_bpm":        _nan,
        "heart_peak_hz":       _nan,
        "hr_valid":            False,
        "hr_confidence":       hr_confidence,
        "invalid_reason":      invalid_reason,
        # Quality
        "quality_gated":       quality_gated,
        "n_bad_frames":        n_bad_frames,
        "bad_fraction":        bad_fraction,
        # Step 5 link
        "matched_rr_window_index": resp_match.get("matched_rr_window_index", -1),
        "resp_match_distance_s":   resp_match.get("resp_match_distance_s",   _nan),
        "radar_rr_bpm":            resp_match.get("radar_rr_bpm",            _nan),
        "resp_peak_hz":            resp_match.get("resp_peak_hz",            _nan),
        "resp_valid":              resp_match.get("resp_valid",              False),
        "resp_confidence":         resp_match.get("resp_confidence",         "none"),
        "resp_edge_locked":        resp_match.get("resp_edge_locked",        False),
        "resp_edge_lock_side":     resp_match.get("resp_edge_lock_side",     "none"),
        # ECA / AHET diagnostics (all NaN / False for skipped windows)
        "baseline_hr_bpm":               _nan,
        "eca_applied":                   False,
        "eca_forbidden_zone":            False,
        "ahet_verified":                 False,
        "harmonic_suspect":              False,
        "resp_harmonic_coincident":      False,
        "heart_spectrum_stage":          "skipped",
        "accepted_candidate_rank":       -1,
        "accepted_candidate_refined_hz": _nan,
        "accepted_second_harmonic_refined_hz": _nan,
        # Masimo comparison
        **masimo,
        "hr_error_bpm":     err_bpm,
        "hr_abs_error_bpm": abs_err,
    }


def _build_hr_row(
    session_id: str,
    window_index: int,
    start_epoch: float,
    end_epoch: float,
    start_frame: int,
    end_frame: int,
    locked_bin: int,
    locked_range_m: float,
    quality_gated: bool,
    n_bad_frames: int,
    bad_fraction: float,
    resp_match: dict,
    masimo: dict,
    hr_result: dict,
    baseline_hr_bpm: float,
    eca_forbidden_zone: bool,
    resp_harmonic_coincident: bool,
    invalid_reason: str | None,
    hr_confidence: str,
) -> dict:
    _nan = float("nan")
    heart_peak_hz = float(hr_result.get("peak_hz", _nan))
    radar_hr_bpm  = float(hr_result.get("rate_bpm", _nan)) if hr_confidence == "high" else _nan
    hr_valid      = hr_confidence == "high"

    masimo_pr  = masimo["masimo_pr_bpm"]
    masimo_bad = masimo["masimo_low_quality"]
    if hr_valid and np.isfinite(radar_hr_bpm) and np.isfinite(masimo_pr) and not masimo_bad:
        err_bpm = float(radar_hr_bpm - masimo_pr)
        abs_err = abs(err_bpm)
    else:
        err_bpm = _nan
        abs_err = _nan

    stage_int = int(hr_result.get("spectrum_stage", -1))
    stage_str = _STAGE_TO_STR.get(stage_int, "skipped")

    return {
        # Window metadata
        "session_id":          session_id,
        "window_index":        window_index,
        "start_epoch":         start_epoch,
        "end_epoch":           end_epoch,
        "start_frame":         start_frame,
        "end_frame":           end_frame,
        # Range metadata
        "locked_bin":          locked_bin,
        "locked_range_m":      locked_range_m,
        # Primary HR
        "radar_hr_bpm":        radar_hr_bpm,
        "heart_peak_hz":       heart_peak_hz,
        "hr_valid":            hr_valid,
        "hr_confidence":       hr_confidence,
        "invalid_reason":      invalid_reason if invalid_reason is not None else "",
        # Quality
        "quality_gated":       quality_gated,
        "n_bad_frames":        n_bad_frames,
        "bad_fraction":        bad_fraction,
        # Step 5 link
        "matched_rr_window_index": resp_match.get("matched_rr_window_index", -1),
        "resp_match_distance_s":   resp_match.get("resp_match_distance_s",   _nan),
        "radar_rr_bpm":            resp_match.get("radar_rr_bpm",            _nan),
        "resp_peak_hz":            resp_match.get("resp_peak_hz",            _nan),
        "resp_valid":              resp_match.get("resp_valid",              False),
        "resp_confidence":         resp_match.get("resp_confidence",         "none"),
        "resp_edge_locked":        resp_match.get("resp_edge_locked",        False),
        "resp_edge_lock_side":     resp_match.get("resp_edge_lock_side",     "none"),
        # ECA / AHET diagnostics
        "baseline_hr_bpm":               float(baseline_hr_bpm),
        "eca_applied":                   bool(hr_result.get("eca_applied", False)),
        "eca_forbidden_zone":            bool(eca_forbidden_zone),
        "ahet_verified":                 bool(hr_result.get("ahet_verified", False)),
        "harmonic_suspect":              bool(hr_result.get("harmonic_suspect", False)),
        "resp_harmonic_coincident":      bool(resp_harmonic_coincident),
        "heart_spectrum_stage":          stage_str,
        "accepted_candidate_rank":       int(hr_result.get("accepted_candidate_rank", -1)),
        "accepted_candidate_refined_hz": float(hr_result.get("accepted_candidate_refined_hz", _nan)),
        "accepted_second_harmonic_refined_hz": float(
            hr_result.get("accepted_second_harmonic_refined_hz", _nan)
        ),
        # Masimo comparison
        **masimo,
        "hr_error_bpm":     err_bpm,
        "hr_abs_error_bpm": abs_err,
    }


# ---------------------------------------------------------------------------
# NPZ helpers
# ---------------------------------------------------------------------------

def _make_nan_intermediates(window_frames: int, n_fft: int, k_max: int = 6) -> dict:
    """Return NaN-filled intermediate arrays for a skipped window (fixed shapes)."""
    _nan = float("nan")
    return {
        "phase_unwrapped":       np.full(window_frames, _nan, dtype=np.float64),
        "phase_clean":           np.full(window_frames, _nan, dtype=np.float64),
        "phase_eca":             np.full(window_frames, _nan, dtype=np.float64),
        "heart_spectrum_pre_eca":    np.full(n_fft, _nan, dtype=np.float64),
        "heart_spectrum_first_pass": np.full(n_fft, _nan, dtype=np.float64),
        "heart_spectrum":        np.full(n_fft, _nan, dtype=np.float64),
        "baseline_spectrum":     np.full(n_fft, _nan, dtype=np.float64),
        "ahet_attempt_spectrum": np.full((AHET_MAX_CANDIDATES, n_fft), _nan, dtype=np.float64),
        "candidate_attempted":   np.zeros(AHET_MAX_CANDIDATES, dtype=bool),
        "candidate_initial_hz":  np.full(AHET_MAX_CANDIDATES, _nan, dtype=np.float64),
        "candidate_refined_hz":  np.full(AHET_MAX_CANDIDATES, _nan, dtype=np.float64),
        "candidate_peak_magnitude": np.full(AHET_MAX_CANDIDATES, _nan, dtype=np.float64),
        "candidate_prominence":  np.full(AHET_MAX_CANDIDATES, _nan, dtype=np.float64),
        "candidate_passed":      np.zeros(AHET_MAX_CANDIDATES, dtype=bool),
        "second_peak_refined_hz": np.full(AHET_MAX_CANDIDATES, _nan, dtype=np.float64),
        "second_peak_magnitude": np.full(AHET_MAX_CANDIDATES, _nan, dtype=np.float64),
        "comparison_floor":      np.full(AHET_MAX_CANDIDATES, _nan, dtype=np.float64),
        "peak_to_floor_ratio":   np.full(AHET_MAX_CANDIDATES, _nan, dtype=np.float64),
        "peak_to_floor_ratio_db": np.full(AHET_MAX_CANDIDATES, _nan, dtype=np.float64),
        "region_available":      np.zeros(AHET_MAX_CANDIDATES, dtype=bool),
        # Step 6.1 new fields
        "candidate_rejection_code": np.full(AHET_MAX_CANDIDATES, -1, dtype=np.int32),
        "all_candidates_rejected":  np.array(False, dtype=bool),
        "eca_skipped_harmonics":    np.zeros(k_max, dtype=bool),
        "k_max_eff":                 np.int32(0),
        "n_eca_projected":           np.int32(0),
        "n_eca_cols_selected":       np.int32(0),
        "n_eca_cols_retained":       np.int32(0),
        "n_eca_cols_dropped":        np.int32(0),
        "eca_retained_ks":           np.zeros(k_max, dtype=bool),
        "eca_cols_retained":         np.zeros((k_max, 2), dtype=bool),
        "candidate_eca_skipped":     np.zeros((AHET_MAX_CANDIDATES, k_max), dtype=bool),
        "candidate_eca_retained_ks": np.zeros((AHET_MAX_CANDIDATES, k_max), dtype=bool),
        "candidate_n_eca_cols_retained": np.zeros(AHET_MAX_CANDIDATES, dtype=np.int32),
    }


# ── Artifact-shape contracts (cross-review P2, 2026-07-14) ───────────────────────────────
# These used to truncate/pad silently. A cap mismatch would then yield artifacts that look
# plausible but are incomplete — the precise failure Stage 0 exists to prevent. They now FAIL
# LOUDLY. A missing key (older estimator / NaN window) is still allowed and yields zeros.

def _eca_vec(arr, k_max: int, name: str = "eca_vector") -> np.ndarray:
    """1-D per-harmonic bool vector; must be exactly (k_max,) if present."""
    if arr is None:
        return np.zeros(k_max, dtype=bool)
    a = np.asarray(arr, dtype=bool)
    if a.shape != (k_max,):
        raise ValueError(
            f"{name}: estimator returned shape {a.shape}, expected ({k_max},). "
            f"Refusing to truncate — that would produce plausible but incomplete evidence. "
            f"Check heart.k_max_cap vs heart.k_max."
        )
    return a


def _eca_cols(arr, k_max: int) -> np.ndarray:
    """Per-harmonic [sin, cos] column-survival matrix; must be exactly (k_max, 2)."""
    if arr is None:
        return np.zeros((k_max, 2), dtype=bool)
    a = np.asarray(arr, dtype=bool)
    if a.shape != (k_max, 2):
        raise ValueError(
            f"eca_cols_retained: estimator returned shape {a.shape}, expected ({k_max}, 2)."
        )
    return a


def _cand_eca_2d(arr, k_max: int, name: str = "candidate_eca") -> np.ndarray:
    """Candidate-wise ECA matrix; must be exactly (AHET_MAX_CANDIDATES, k_max)."""
    if arr is None:
        return np.zeros((AHET_MAX_CANDIDATES, k_max), dtype=bool)
    a = np.asarray(arr, dtype=bool)
    if a.shape != (AHET_MAX_CANDIDATES, k_max):
        raise ValueError(
            f"{name}: estimator returned shape {a.shape}, "
            f"expected ({AHET_MAX_CANDIDATES}, {k_max})."
        )
    return a


def _cand_counts(arr) -> np.ndarray:
    """Per-candidate retained-column counts; must be exactly (AHET_MAX_CANDIDATES,)."""
    if arr is None:
        return np.zeros(AHET_MAX_CANDIDATES, dtype=np.int32)
    a = np.asarray(arr, dtype=np.int32)
    if a.shape != (AHET_MAX_CANDIDATES,):
        raise ValueError(
            f"candidate_n_eca_cols_retained: got {a.shape}, "
            f"expected ({AHET_MAX_CANDIDATES},)."
        )
    return a


def _eca_skip(arr, k_max: int) -> np.ndarray:
    """Extract eca_skipped_harmonics from hr_result; must be exactly (k_max,) if present."""
    return _eca_vec(arr, k_max, "eca_skipped_harmonics")


def _intermediates_from_result(
    win_phase_unwrapped: np.ndarray,
    win_phase_clean: np.ndarray,
    hr_result: dict,
    baseline_result: dict,
    n_fft: int,
    k_max: int = 6,
) -> dict:
    """Extract intermediate arrays from estimator results (valid window)."""
    _nan = float("nan")
    window_frames = len(win_phase_clean)

    # ECA-projected phase (NaN if ECA wasn't applied)
    if hr_result.get("eca_applied", False):
        phase_eca = np.asarray(hr_result["phase_eca"], dtype=np.float64)
    else:
        phase_eca = np.full(window_frames, _nan, dtype=np.float64)

    def _spec(result: dict, key: str) -> np.ndarray:
        s = result.get(key)
        if s is None or not np.isfinite(np.asarray(s)).any():
            return np.full(n_fft, _nan, dtype=np.float64)
        arr = np.asarray(s, dtype=np.float64)
        if len(arr) != n_fft:
            out = np.full(n_fft, _nan, dtype=np.float64)
            out[:min(len(arr), n_fft)] = arr[:min(len(arr), n_fft)]
            return out
        return arr

    ahet_spec = hr_result.get("ahet_attempt_spectrum")
    if ahet_spec is None:
        ahet_spec_arr = np.full((AHET_MAX_CANDIDATES, n_fft), _nan, dtype=np.float64)
    else:
        ahet_spec_arr = np.asarray(ahet_spec, dtype=np.float64)
        if ahet_spec_arr.shape != (AHET_MAX_CANDIDATES, n_fft):
            tmp = np.full((AHET_MAX_CANDIDATES, n_fft), _nan, dtype=np.float64)
            r = min(ahet_spec_arr.shape[0], AHET_MAX_CANDIDATES)
            c = min(ahet_spec_arr.shape[1], n_fft)
            tmp[:r, :c] = ahet_spec_arr[:r, :c]
            ahet_spec_arr = tmp

    def _cand(key: str, fill=float("nan"), dtype=np.float64) -> np.ndarray:
        arr = hr_result.get(key)
        if arr is None:
            return np.full(AHET_MAX_CANDIDATES, fill, dtype=dtype)
        a = np.asarray(arr)
        if len(a) != AHET_MAX_CANDIDATES:
            out = np.full(AHET_MAX_CANDIDATES, fill, dtype=dtype)
            out[:min(len(a), AHET_MAX_CANDIDATES)] = a[:min(len(a), AHET_MAX_CANDIDATES)]
            return out
        return a.astype(dtype)

    return {
        "phase_unwrapped":       win_phase_unwrapped.astype(np.float64),
        "phase_clean":           win_phase_clean.astype(np.float64),
        "phase_eca":             phase_eca,
        "heart_spectrum_pre_eca":    _spec(hr_result, "spectrum_pre_eca"),
        "heart_spectrum_first_pass": _spec(hr_result, "spectrum_first_pass"),
        "heart_spectrum":        _spec(hr_result, "spectrum"),
        "baseline_spectrum":     _spec(baseline_result, "spectrum"),
        "ahet_attempt_spectrum": ahet_spec_arr,
        "candidate_attempted":   _cand("candidate_attempted", fill=False, dtype=bool),
        "candidate_initial_hz":  _cand("candidate_initial_hz"),
        "candidate_refined_hz":  _cand("candidate_refined_hz"),
        "candidate_peak_magnitude": _cand("candidate_peak_magnitude"),
        "candidate_prominence":  _cand("candidate_prominence"),
        "candidate_passed":      _cand("candidate_passed", fill=False, dtype=bool),
        "second_peak_refined_hz": _cand("second_peak_refined_hz"),
        "second_peak_magnitude": _cand("second_peak_magnitude"),
        "comparison_floor":      _cand("comparison_floor"),
        "peak_to_floor_ratio":   _cand("peak_to_floor_ratio"),
        "peak_to_floor_ratio_db": _cand("peak_to_floor_ratio_db"),
        "region_available":      _cand("region_available", fill=False, dtype=bool),
        # Step 6.1 new fields
        "candidate_rejection_code": _cand(
            "candidate_rejection_code", fill=-1, dtype=np.int32
        ),
        "all_candidates_rejected": np.array(
            bool(hr_result.get("all_candidates_rejected", False)), dtype=bool
        ),
        "eca_skipped_harmonics": _eca_skip(
            hr_result.get("eca_skipped_harmonics"), k_max
        ),
        # Cross-review 2026-07-14: the OFFLINE path is what scores the modes, so it must record
        # how many harmonics were CONSIDERED vs PROJECTED (12.1), which columns actually survived
        # Gram-Schmidt (20.6), and which projection each candidate was judged under (12.4).
        "k_max_eff":       np.int32(hr_result.get("k_max_eff", 0)),
        "n_eca_projected": np.int32(hr_result.get("n_eca_projected", 0)),
        "n_eca_cols_selected": np.int32(hr_result.get("n_eca_cols_selected", 0)),
        "n_eca_cols_retained": np.int32(hr_result.get("n_eca_cols_retained", 0)),
        "n_eca_cols_dropped":  np.int32(hr_result.get("n_eca_cols_dropped", 0)),
        "eca_retained_ks":   _eca_vec(hr_result.get("eca_retained_ks"), k_max,
                                      "eca_retained_ks"),
        "eca_cols_retained": _eca_cols(hr_result.get("eca_cols_retained"), k_max),
        "candidate_eca_skipped": _cand_eca_2d(
            hr_result.get("candidate_eca_skipped"), k_max, "candidate_eca_skipped"
        ),
        "candidate_eca_retained_ks": _cand_eca_2d(
            hr_result.get("candidate_eca_retained_ks"), k_max, "candidate_eca_retained_ks"
        ),
        "candidate_n_eca_cols_retained": _cand_counts(
            hr_result.get("candidate_n_eca_cols_retained")
        ),
    }


def _make_null_tracker_npz(n_windows: int) -> dict:
    """Tracker NPZ arrays when the tracker is not run (all NaN / -1)."""
    n_cand = AHET_MAX_CANDIDATES
    _nan   = float("nan")
    return {
        "candidate_fundamental_ratio_db": np.full((n_windows, n_cand), _nan,  dtype=np.float64),
        "candidate_tracker_eligible":      np.zeros((n_windows, n_cand),        dtype=bool),
        "candidate_tracker_node_score":    np.full((n_windows, n_cand), _nan,  dtype=np.float64),
        "tracker_decision_code":           np.full(n_windows, -1,               dtype=np.int32),
        "tracker_selected_candidate_rank": np.full(n_windows, -1,               dtype=np.int32),
    }


def _write_npz(
    path: Path,
    intermediates_list: list[dict],
    window_frames: int,
    n_fft: int,
    heart_freqs_hz: np.ndarray,
    tracker_arrays: dict | None = None,
) -> None:
    """Stack per-window intermediate dicts and write to a compressed NPZ atomically."""
    keys_1d = [
        "phase_unwrapped", "phase_clean", "phase_eca",
        "heart_spectrum_pre_eca", "heart_spectrum_first_pass", "heart_spectrum",
        "baseline_spectrum",
    ]
    keys_cand = [
        "candidate_attempted", "candidate_initial_hz", "candidate_refined_hz",
        "candidate_peak_magnitude", "candidate_prominence", "candidate_passed",
        "second_peak_refined_hz", "second_peak_magnitude", "comparison_floor",
        "peak_to_floor_ratio", "peak_to_floor_ratio_db", "region_available",
        "candidate_rejection_code",   # Step 6.1 (AHET_MAX_CANDIDATES,) int32
    ]

    stacked: dict[str, np.ndarray] = {}
    for k in keys_1d:
        stacked[k] = np.stack([d[k] for d in intermediates_list], axis=0)
    for k in keys_cand:
        stacked[k] = np.stack([d[k] for d in intermediates_list], axis=0)
    stacked["ahet_attempt_spectrum"] = np.stack(
        [d["ahet_attempt_spectrum"] for d in intermediates_list], axis=0
    )
    stacked["heart_freqs_hz"] = heart_freqs_hz  # (n_fft,) — same for all windows
    # Step 6.1 — scalar bool and variable-length (k_max,) arrays
    stacked["all_candidates_rejected"] = np.stack(
        [d["all_candidates_rejected"] for d in intermediates_list], axis=0
    )
    stacked["eca_skipped_harmonics"] = np.stack(
        [d["eca_skipped_harmonics"] for d in intermediates_list], axis=0
    )
    # Cross-review 2026-07-14 (P1): the ECA basis + candidate-wise evidence MUST reach disk.
    # These were added to the per-window dict but never stacked here, so they never landed in
    # the NPZ — and the offline experiment is the path that scores the modes.
    for k in (
        "k_max_eff", "n_eca_projected",
        "n_eca_cols_selected", "n_eca_cols_retained", "n_eca_cols_dropped",
        "eca_retained_ks", "eca_cols_retained",
        "candidate_eca_skipped", "candidate_eca_retained_ks",
        "candidate_n_eca_cols_retained",
    ):
        stacked[k] = np.stack([d[k] for d in intermediates_list], axis=0)
    # Step 6.3 — tracker arrays
    if tracker_arrays:
        stacked.update(tracker_arrays)

    # Atomic write via tempfile
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{path.name}.", suffix=".tmp.npz",
            dir=path.parent, delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
        np.savez_compressed(tmp_path, **stacked)
        with np.load(tmp_path, allow_pickle=False) as arch:
            if set(arch.files) != set(stacked):
                raise OSError("NPZ field mismatch after write")
        tmp_path.replace(path)
        tmp_path = None
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

def _plot_hr_comparison(df: pd.DataFrame, out_path: Path, session_id: str) -> None:
    t = pd.to_datetime(df["start_epoch"], unit="s", utc=True)
    fig, ax = plt.subplots(figsize=(11, 4))
    valid = df["hr_valid"].astype(bool)
    ax.plot(t, df["masimo_pr_bpm"], "o-", color="black",
            label="Masimo PR (ref)", zorder=3, markersize=4)
    if valid.any():
        ax.plot(t[valid], df.loc[valid, "radar_hr_bpm"], "s-", color="tab:blue",
                label="Radar HR (valid)", zorder=4, markersize=4)
    if (~valid).any():
        ax.plot(t[~valid], df.loc[~valid, "radar_hr_bpm"], "x", color="tab:gray",
                label="Radar HR (invalid/NaN)", zorder=2, markersize=5)
    ax.set_xlabel("time (UTC)")
    ax.set_ylabel("rate (bpm)")
    ax.set_title(f"{session_id} — Radar HR vs Masimo PR")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    try:
        fig.tight_layout()
    except Exception:
        pass
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def _plot_method_agreement(df: pd.DataFrame, out_path: Path, session_id: str) -> None:
    t = pd.to_datetime(df["start_epoch"], unit="s", utc=True)
    fig, axes = plt.subplots(2, 1, figsize=(11, 6), sharex=True)

    ax = axes[0]
    ax.plot(t, df["masimo_pr_bpm"], "o-", color="black",   label="Masimo PR",  markersize=3)
    ax.plot(t, df["baseline_hr_bpm"], "^-", color="tab:orange", label="Baseline (no ECA)", markersize=3)
    ax.plot(t, df["radar_hr_bpm"],   "s-", color="tab:blue",  label="ECA+AHET HR", markersize=3)
    ax.set_ylabel("HR (bpm)")
    ax.set_title(f"{session_id} — Method agreement")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    ax2 = axes[1]
    diff = (df["baseline_hr_bpm"] - df["radar_hr_bpm"]).abs()
    width_s = pd.Timedelta(seconds=max((df["start_epoch"].iloc[1] - df["start_epoch"].iloc[0])
                                       if len(df) > 1 else 5, 1) * 0.8)
    ax2.bar(t, diff.values, width=width_s, color="tab:red", alpha=0.7,
            label="|Baseline - ECA+AHET| (bpm)")
    ax2.set_xlabel("time (UTC)")
    ax2.set_ylabel("|baseline - ECA+AHET| (bpm)")
    ax2.legend(fontsize=8)
    ax2.grid(True, alpha=0.3)

    try:
        fig.tight_layout()
    except Exception:
        pass
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def _plot_phase_and_quality(
    phase: np.ndarray,
    quality_mask: np.ndarray,
    analysis_start: int,
    frame_rate_hz: float,
    out_path: Path,
    session_id: str,
) -> None:
    t       = np.arange(len(phase)) / frame_rate_hz
    bad     = ~quality_mask
    fig, axes = plt.subplots(2, 1, figsize=(11, 5), sharex=True)

    ax = axes[0]
    ax.plot(t, phase, color="tab:blue", linewidth=0.8, label="chest phase (rad)")
    ax.set_ylabel("phase (rad)")
    ax.set_title(f"{session_id} — Chest phase (analysis_start={analysis_start})")
    ax.grid(True, alpha=0.3)

    ax2 = axes[1]
    ax2.fill_between(t, bad.astype(float), step="mid",
                     color="tab:red", alpha=0.6, label="bad frames")
    ax2.set_ylim(-0.05, 1.05)
    ax2.set_xlabel(f"time (s from frame {analysis_start})")
    ax2.set_ylabel("bad frame")
    ax2.legend(fontsize=8)
    ax2.grid(True, alpha=0.3)

    try:
        fig.tight_layout()
    except Exception:
        pass
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Per-session processing
# ---------------------------------------------------------------------------

def _process_session(
    session_id: str,
    row: pd.Series,
    cfg: dict,
    cubes_dir: Path,
    data_raw: Path,
    breathing_rate_dir: Path,
    out_dir: Path,
    commit: str,
    *,
    no_write: bool = False,
    overwrite: bool = False,
    no_plots: bool = False,
) -> dict:
    """Process one session end-to-end.  Returns a summary dict.

    out_dir is results/<session_id>/step_6/.  Raises FileExistsError if the
    sentinel CSV already exists and --overwrite was not passed.
    """
    _nan = float("nan")

    # -- Config --
    paths       = cfg["paths"]
    windowing   = cfg["windowing"]
    phase_cfg   = cfg.get("phase", {})
    heart_cfg   = cfg["heart"]
    resp_match_cfg = cfg.get("respiration_matching", {})
    cmp_cfg     = cfg.get("comparison", {})
    input_cfg   = cfg.get("input_policy", {})

    window_s    = float(windowing["window_s"])
    hop_s       = float(windowing["hop_s"])
    use_intervals = bool(windowing.get("use_stationary_intervals", True))
    phase_method  = str(phase_cfg.get("method", "delta_before_mean"))
    clip_rad      = float(phase_cfg.get("impulse_clip_rad", 1.5))
    heart_band_hz = tuple(heart_cfg["heart_band_hz"])
    k_max         = int(heart_cfg["k_max"])
    ahet_dev_hz   = float(heart_cfg["ahet_deviation_hz"])
    harm_guard_hz = float(heart_cfg["resp_harmonic_guard_hz"])
    reject_coinc  = bool(heart_cfg.get("reject_resp_harmonic_coincidence", True))
    # Step 6.1 config
    eca_mode      = str(heart_cfg.get("eca_mode", "legacy"))
    ahet_gate_mode = str(heart_cfg.get("ahet_gate_mode", "legacy"))
    eca_forbidden_guard_hz = float(heart_cfg.get("eca_forbidden_guard_hz", 0.0))
    eca_cardiac_guard_hz   = float(heart_cfg.get("eca_cardiac_guard_hz", 0.10))
    k_max_cap              = int(heart_cfg.get("k_max_cap", 10))
    # Reporting length for eca_skipped_harmonics. k_max_eff varies per window under
    # guard_cardiac_candidate_v1, so the artifact must be sized by the CAP, not by k_max —
    # otherwise the NPZ stack truncates and silently hides k > k_max (plan S8.1b).
    skip_len               = max(k_max_cap, k_max)
    cand_min_ratio_db       = float(heart_cfg.get("candidate_min_second_harmonic_ratio_db", 1.0))
    cand_min_prominence     = float(heart_cfg.get("candidate_min_prominence", 3.0))
    low_cand_hz             = float(heart_cfg.get("low_candidate_hz", 1.20))
    high_cand_pref_hz       = float(heart_cfg.get("high_candidate_preference_hz", 1.25))
    high_comp_ratio         = float(heart_cfg.get("high_competitor_min_mag_ratio", 0.80))
    cand_min_floor_db       = float(heart_cfg.get("candidate_min_peak_to_floor_db", 0.0))
    low_cand_min_floor_db   = float(heart_cfg.get("low_candidate_min_peak_to_floor_db", 0.0))
    max_match_s   = float(resp_match_cfg.get("max_resp_match_distance_s", 15.0))
    min_pi        = float(cmp_cfg.get("min_pi", 0.5))
    min_cov_frac  = float(cmp_cfg.get("min_masimo_coverage_fraction", 0.8))
    min_pi_frac   = float(cmp_cfg.get("min_masimo_good_pi_fraction", 0.8))
    max_bad_frac  = float(input_cfg.get("max_bad_fraction", 0.10))

    # -- Input files --
    h5_path   = cubes_dir / f"{session_id}.h5"
    mas_path  = data_raw  / f"{session_id}_masimo.csv"
    resp_path = breathing_rate_dir / f"{session_id}.csv"

    if not h5_path.exists():
        raise FileNotFoundError(f"HDF5 not found: {h5_path} — run Step 2 first")
    if not mas_path.exists():
        raise FileNotFoundError(f"Masimo CSV not found: {mas_path}")
    if not resp_path.exists():
        raise FileNotFoundError(
            f"Step 5 contract not found: {resp_path} — run Step 5 first"
        )

    # -- Load Step 5 contract --
    resp_df = pd.read_csv(resp_path)
    missing_cols = [c for c in _STEP5_EDGE_LOCK_COLS if c not in resp_df.columns]
    if missing_cols:
        raise ValueError(
            f"Step 5 contract for '{session_id}' is missing edge-lock columns: "
            f"{missing_cols}.\n"
            "Re-run Step 5 to regenerate edge_locked / edge_lock_side columns:\n"
            f"  python -X utf8 steps/step_5/extract_breathing_rate.py "
            f"--session {session_id} --overwrite"
        )

    # -- Manifest fields --
    locked_bin    = int(float(str(row["locked_bin"]).strip()))
    locked_range_m = float(str(row["locked_range_m"]).strip())
    t0_base        = float(str(row["radar_start_epoch_seconds"]).strip())
    interval       = str(row.get("stationary_intervals", "") or "").strip()

    # -- Open HDF5 --
    with h5py.File(h5_path, "r") as f:
        frame_rate_hz = float(f.attrs.get("frame_rate_hz", 20.0))
        total_frames  = int(f.attrs["num_frames"])

        if "quality_mask" not in f:
            raise FileNotFoundError(
                f"No /quality_mask in {h5_path.name} — run Step 4 first"
            )
        quality_mask = f["quality_mask"][:].astype(bool)
        mask_trim    = int(f["quality_mask"].attrs["trim_frames"])

        # analysis_start from stationary_intervals (independent of mask_trim)
        if use_intervals and interval:
            parts          = interval.split("-")
            analysis_start = int(int(parts[0]) * frame_rate_hz)
            analysis_end   = int(int(parts[1]) * frame_rate_hz)
        else:
            analysis_start = mask_trim
            analysis_end   = total_frames

        analysis_end = min(analysis_end, total_frames)

        mask_end = mask_trim + len(quality_mask)
        if analysis_start < mask_trim or analysis_end > mask_end:
            raise ValueError(
                f"{session_id}: analysis window [{analysis_start}, {analysis_end}) "
                f"not fully covered by quality_mask [{mask_trim}, {mask_end}).\n"
                "Re-run Step 4 with trim_frames <= stationary_intervals start."
            )

        t0         = t0_base + analysis_start / frame_rate_hz
        n_analysis = analysis_end - analysis_start

        window_frames = int(round(window_s * frame_rate_hz))
        hop_frames    = int(round(hop_s    * frame_rate_hz))
        n_fft         = window_frames // 2 + 1

        if n_analysis <= 0 or window_frames > n_analysis:
            raise ValueError(
                f"Not enough analysis frames for {session_id}: "
                f"n_analysis={n_analysis}, window_frames={window_frames}"
            )

        print(f"  HDF5: {total_frames} total frames, {n_analysis} analysis frames "
              f"({n_analysis / frame_rate_hz:.1f} s), frame_rate={frame_rate_hz:.1f} Hz")
        print(f"  mask_trim={mask_trim}, analysis_start={analysis_start} "
              f"({analysis_start / frame_rate_hz:.1f} s)")
        print(f"  locked_bin={locked_bin} ({locked_range_m:.3f} m), "
              f"window={window_s:.0f}s hop={hop_s:.0f}s, n_fft={n_fft}")

        cube_slice = f["cube"][analysis_start:analysis_end].astype(np.complex64)

    # quality_mask slice aligned to analysis region
    qmask_analysis = quality_mask[analysis_start - mask_trim : analysis_end - mask_trim]

    # -- Extract full chest phase once over the analysis interval --
    phase_raw   = extract_chest_phase(cube_slice, locked_bin, method=phase_method)
    phase_clean = remove_impulse_noise(phase_raw, thresh=clip_rad)
    del cube_slice

    # -- Generate sliding HR windows (absolute frame indices) --
    windows: list[tuple[int, int]] = []
    abs_s = analysis_start
    while abs_s + window_frames <= analysis_end:
        windows.append((abs_s, abs_s + window_frames))
        abs_s += hop_frames

    if not windows:
        raise ValueError(f"No valid windows for {session_id}")

    print(f"  Step 5 windows: {len(resp_df)}  |  HR windows: {len(windows)}")

    # Precompute heart-band FFT frequency axis (same for all windows)
    heart_freqs_hz = np.fft.rfftfreq(window_frames, d=1.0 / frame_rate_hz)

    # -- Load Masimo --
    mas_df = masimo_mod.load_masimo(mas_path)

    # -- Per-window loop --
    rows:           list[dict]  = []
    intermediates:  list[dict]  = []

    # Raw flag counts (may overlap across windows)
    flag_counts = {r: 0 for r in _INVALID_REASON_PRIORITY}
    flag_counts["masimo_missing_or_low_quality"] = 0
    n_eca_forbidden = 0
    n_ahet_verified = 0
    n_resp_harmonic_coincident_raw = 0

    for wi, (abs_s, abs_e) in enumerate(windows):
        phase_s = abs_s - analysis_start
        phase_e = abs_e - analysis_start

        # -- Quality gate --
        mask_slice = qmask_analysis[phase_s:phase_e]
        n_bad      = int((~mask_slice).sum())
        bad_frac   = n_bad / (phase_e - phase_s)
        quality_gated = bad_frac > max_bad_frac

        start_epoch = t0 + phase_s / frame_rate_hz
        end_epoch   = t0 + phase_e / frame_rate_hz

        # -- Match Step 5 breathing row --
        match_pos, match_dist = _match_resp_window(start_epoch, end_epoch, resp_df)
        s5_row = resp_df.iloc[match_pos]

        resp_match = {
            "matched_rr_window_index": int(s5_row.get("window_index", match_pos)),
            "resp_match_distance_s":   match_dist,
            "radar_rr_bpm":            float(s5_row.get("radar_rr_bpm", _nan)),
            "resp_peak_hz":            float(s5_row.get("resp_peak_hz", _nan)),
            "resp_valid":              _parse_bool(s5_row.get("resp_valid"), default=False),
            "resp_confidence":         str(s5_row.get("resp_confidence", "none")),
            "resp_edge_locked":        _parse_bool(s5_row.get("edge_locked"), default=False),
            "resp_edge_lock_side":     str(s5_row.get("edge_lock_side", "none")),
        }

        # -- Masimo PR for this window --
        masimo_stats = _compute_masimo_pr(
            mas_df, start_epoch, end_epoch, min_pi, min_cov_frac, min_pi_frac
        )

        # -- Evaluate all invalid flags --
        resp_peak_hz  = resp_match["resp_peak_hz"]
        resp_valid    = resp_match["resp_valid"]
        resp_edge_locked = resp_match["resp_edge_locked"]
        resp_match_too_far = match_dist > max_match_s

        flags = {
            "quality_gated":       quality_gated,
            "resp_match_too_far":  resp_match_too_far,
            "resp_invalid":        not resp_valid,
            "resp_edge_locked":    resp_edge_locked,
            "resp_missing":        not np.isfinite(resp_peak_hz),
        }
        # Count all flags independently (before priority filtering)
        for reason, active in flags.items():
            if active:
                flag_counts[reason] += 1
        if masimo_stats["masimo_low_quality"]:
            flag_counts["masimo_missing_or_low_quality"] += 1

        # Determine if estimator can run
        can_run = not any(flags.values())

        if not can_run:
            invalid_reason_pre  = _get_invalid_reason(flags)
            hr_confidence_final = _assign_hr_confidence(invalid_reason_pre, -1, False)
            nan_row = _nan_hr_row(
                session_id, wi, start_epoch, end_epoch,
                abs_s, abs_e, locked_bin, locked_range_m,
                quality_gated, n_bad, bad_frac,
                resp_match, masimo_stats,
                invalid_reason_pre, hr_confidence_final,
            )
            nan_row.update({
                "candidate_rejection_reason": "gate_not_run",
                "low_candidate_competitor":   False,
                "all_candidates_rejected":    False,
                "ahet_gate_mode":             ahet_gate_mode,
                "n_eca_skipped_harmonics":    0,
                "eca_skipped_harmonic_ks":    "",
                "k_max_eff":                  0,
                "n_eca_projected":            0,
                "n_eca_cols_retained":        0,
                "n_eca_cols_dropped":         0,
            })
            rows.append(nan_row)
            intermediates.append(_make_nan_intermediates(window_frames, n_fft, skip_len))
            continue

        # -- Run estimator --
        win_phase_unwrapped = phase_raw[phase_s:phase_e].copy()
        win_phase_clean     = phase_clean[phase_s:phase_e].copy()

        # ECA forbidden-zone flag (pre-ECA diagnostic)
        eca_fz = _check_eca_forbidden_zone(resp_peak_hz, heart_band_hz, k_max)
        if eca_fz:
            n_eca_forbidden += 1

        # Primary: ECA + AHET with Step 5 resp_peak_hz (Step 6.1 params forwarded)
        hr_result = estimate_rate_from_phase(
            win_phase_clean, frame_rate_hz, heart_band_hz,
            f_r_hz=resp_peak_hz, k_max=k_max, ahet_deviation_hz=ahet_dev_hz,
            eca_mode=eca_mode, ahet_gate_mode=ahet_gate_mode,
            eca_forbidden_guard_hz=eca_forbidden_guard_hz,
            eca_cardiac_guard_hz=eca_cardiac_guard_hz,
            k_max_cap=k_max_cap,
            candidate_min_second_harmonic_ratio_db=cand_min_ratio_db,
            candidate_min_prominence=cand_min_prominence,
            low_candidate_hz=low_cand_hz,
            high_candidate_preference_hz=high_cand_pref_hz,
            high_competitor_min_mag_ratio=high_comp_ratio,
            candidate_min_peak_to_floor_db=cand_min_floor_db,
            low_candidate_min_peak_to_floor_db=low_cand_min_floor_db,
        )

        # Baseline: no ECA (diagnostics only)
        baseline_result = estimate_rate_from_phase(
            win_phase_clean, frame_rate_hz, heart_band_hz,
        )
        baseline_hr_bpm = float(baseline_result.get("rate_bpm", _nan))

        # Post-run flags
        ahet_verified   = bool(hr_result.get("ahet_verified", False))
        harmonic_suspect = bool(hr_result.get("harmonic_suspect", False))

        heart_peak_hz = float(hr_result.get("peak_hz", _nan))
        resp_harmonic_coincident = _check_resp_harmonic_coincident(
            heart_peak_hz, resp_peak_hz, harm_guard_hz, heart_band_hz
        )
        if resp_harmonic_coincident:
            n_resp_harmonic_coincident_raw += 1

        # Update flags with post-run results
        # ahet_failed = estimator ran but AHET did not accept any candidate (covers both
        # the harmonic_suspect=True path AND the f_r_outlier path where spectrum_stage=0
        # and harmonic_suspect=False).  harmonic_suspect stays as a broader diagnostic.
        flags["ahet_failed"]             = not ahet_verified
        flags["resp_harmonic_coincident"] = (
            resp_harmonic_coincident and reject_coinc and ahet_verified
        )
        for reason in ("ahet_failed", "resp_harmonic_coincident"):
            if flags[reason]:
                flag_counts[reason] += 1

        if ahet_verified:
            n_ahet_verified += 1

        invalid_reason_final = _get_invalid_reason(flags)
        stage_int            = int(hr_result.get("spectrum_stage", -1))
        hr_confidence_final  = _assign_hr_confidence(
            invalid_reason_final, stage_int, ahet_verified
        )

        # Derive Step 6.1 per-window diagnostic fields
        rej_codes = hr_result.get(
            "candidate_rejection_code", np.full(AHET_MAX_CANDIDATES, -1, dtype=int)
        )
        # When a candidate was accepted, report its code (always 0="passed").
        # When all failed, report rank-0's code (strongest candidate, most informative).
        accepted_rank = int(hr_result.get("accepted_candidate_rank", -1))
        if ahet_verified and 0 <= accepted_rank < len(rej_codes):
            summary_code = int(rej_codes[accepted_rank])
        else:
            summary_code = int(rej_codes[0]) if len(rej_codes) > 0 else -1
        eca_skip   = hr_result.get("eca_skipped_harmonics", np.zeros(skip_len, dtype=bool))
        n_skip     = int(np.sum(eca_skip))
        skip_ks_str = ",".join(
            str(i + 1) for i in range(len(eca_skip)) if eca_skip[i]
        ) if n_skip > 0 else ""

        built_row = _build_hr_row(
            session_id, wi, start_epoch, end_epoch,
            abs_s, abs_e, locked_bin, locked_range_m,
            quality_gated, n_bad, bad_frac,
            resp_match, masimo_stats,
            hr_result, baseline_hr_bpm, eca_fz,
            resp_harmonic_coincident,
            invalid_reason_final, hr_confidence_final,
        )
        built_row.update({
            "candidate_rejection_reason": _REJECTION_CODE_STR.get(summary_code, "gate_not_run"),
            "low_candidate_competitor":   bool(any(int(c) == 4 for c in rej_codes)),
            "all_candidates_rejected":    bool(hr_result.get("all_candidates_rejected", False)),
            "ahet_gate_mode":             ahet_gate_mode,
            "n_eca_skipped_harmonics":    n_skip,
            "eca_skipped_harmonic_ks":    skip_ks_str,
            # 12.1 — how many harmonics were CONSIDERED vs actually PROJECTED, and how many
            # sine/cosine columns survived Gram-Schmidt (20.6). Without these the offline
            # experiment can report full coverage while the projection used fewer columns.
            "k_max_eff":                  int(hr_result.get("k_max_eff", 0)),
            "n_eca_projected":            int(hr_result.get("n_eca_projected", 0)),
            "n_eca_cols_retained":        int(hr_result.get("n_eca_cols_retained", 0)),
            "n_eca_cols_dropped":         int(hr_result.get("n_eca_cols_dropped", 0)),
        })
        rows.append(built_row)
        intermediates.append(_intermediates_from_result(
            win_phase_unwrapped, win_phase_clean, hr_result, baseline_result, n_fft, skip_len
        ))

    # -- Add default tracker/source columns to all rows (before tracker runs) --
    for row in rows:
        row["hr_source"] = "ahet" if row.get("hr_valid", False) else ""
        # pre-tracker backup = current AHET decision (overwritten by apply_tracker if enabled)
        row["pre_tracker_radar_hr_bpm"]     = row.get("radar_hr_bpm",     _nan)
        row["pre_tracker_hr_valid"]         = row.get("hr_valid",         False)
        row["pre_tracker_hr_confidence"]    = row.get("hr_confidence",    "none")
        row["pre_tracker_invalid_reason"]   = row.get("invalid_reason",   "")
        row["pre_tracker_hr_error_bpm"]     = row.get("hr_error_bpm",     _nan)
        row["pre_tracker_hr_abs_error_bpm"] = row.get("hr_abs_error_bpm", _nan)
        # tracker evidence (null defaults)
        row["tracker_decision_type"]                 = ""
        row["tracker_selected_candidate_rank"]       = _nan
        row["tracker_selected_candidate_refined_hz"] = _nan
        row["tracker_node_score"]                    = _nan
        row["tracker_fundamental_ratio_db"]          = _nan
        row["tracker_peak_to_floor_ratio_db"]        = _nan
        row["tracker_candidate_prominence"]          = _nan
        row["tracker_distance_to_resp_harmonic_bpm"] = _nan
        row["tracker_within_resp_harmonic_guard"]    = False

    # -- Pre-tracker snapshot for summary --
    pre_n_valid = sum(1 for r in rows if r.get("hr_valid", False))
    _pre_paired = [
        r for r in rows
        if r.get("hr_valid", False)
        and not bool(r.get("masimo_low_quality", True))
        and math.isfinite(float(r["hr_abs_error_bpm"])
                          if r.get("hr_abs_error_bpm") is not None else _nan)
    ]
    pre_mae = float(np.mean([float(r["hr_abs_error_bpm"]) for r in _pre_paired])) \
        if _pre_paired else _nan

    # -- Temporal tracker --
    tracker_cfg     = heart_cfg.get("temporal_tracker", {})
    tracker_enabled = bool(tracker_cfg.get("enabled", False))

    if tracker_enabled:
        tracker_npz_arrays, tracker_stats = apply_tracker(
            rows, intermediates, heart_freqs_hz, tracker_cfg,
            heart_band_hz, harm_guard_hz,
        )
    else:
        tracker_npz_arrays = _make_null_tracker_npz(len(rows))
        tracker_stats = {
            "temporal_tracker_enabled":              False,
            "temporal_tracker_config":               tracker_cfg,
            "n_tracker_selected_windows":            0,
            "n_tracker_gap":                         0,
            "n_tracker_forced_blocked":              0,
            "n_tracker_selected_from_ahet_failed":   0,
        }

    df = pd.DataFrame(rows)

    # -- Summary metrics (reflect post-tracker primary output) --
    valid_mask = df["hr_valid"].astype(bool)
    n_total    = len(df)
    n_valid    = int(valid_mask.sum())

    # Windows where both radar HR and Masimo PR are usable
    paired = df[
        valid_mask
        & df["masimo_pr_bpm"].notna()
        & ~df["masimo_low_quality"].astype(bool)
        & df["hr_abs_error_bpm"].notna()
    ]
    n_paired = len(paired)

    if n_paired > 0:
        mae  = float(paired["hr_abs_error_bpm"].mean())
        rmse = float(math.sqrt((paired["hr_error_bpm"] ** 2).mean()))
        bias = float(paired["hr_error_bpm"].mean())
    else:
        mae = rmse = bias = _nan

    # Non-overlapping invalid_reason counts
    ir_counts = df["invalid_reason"].value_counts().to_dict()

    conf_counts = df["hr_confidence"].value_counts().to_dict()

    print(f"  Windows: {n_total} total | {n_valid} valid HR | "
          f"{flag_counts['quality_gated']} quality-gated | "
          f"{flag_counts['resp_match_too_far']} resp-match-too-far")
    print(f"  resp-invalid={flag_counts['resp_invalid']}  "
          f"resp-edge-locked={flag_counts['resp_edge_locked']}  "
          f"resp-missing={flag_counts['resp_missing']}  "
          f"ahet-failed={flag_counts['ahet_failed']}")
    print(f"  ECA-forbidden-zone={n_eca_forbidden}  "
          f"AHET-verified={n_ahet_verified}  "
          f"resp-harmonic-coincident(raw)={n_resp_harmonic_coincident_raw}  "
          f"resp-harmonic-coincident(rejected)={flag_counts['resp_harmonic_coincident']}")
    if math.isfinite(mae):
        print(f"  HR: MAE={mae:.2f}  RMSE={rmse:.2f}  bias={bias:+.2f} bpm  (N={n_paired})")
    else:
        print("  HR: MAE=NaN (no windows with both valid radar HR and Masimo PR)")
    print(f"  Confidence: {conf_counts}")

    summary = {
        "session_id":               session_id,
        "commit":                   commit,
        "n_windows":                n_total,
        "n_valid_hr":               n_valid,
        "n_quality_gated":          flag_counts["quality_gated"],
        "n_resp_match_too_far":     flag_counts["resp_match_too_far"],
        "n_resp_invalid":           flag_counts["resp_invalid"],
        "n_resp_edge_locked":       flag_counts["resp_edge_locked"],
        "n_resp_missing":           flag_counts["resp_missing"],
        "n_ahet_failed":            flag_counts["ahet_failed"],
        "n_resp_harmonic_coincident_raw":      n_resp_harmonic_coincident_raw,
        "n_resp_harmonic_coincident_rejected": flag_counts["resp_harmonic_coincident"],
        "n_eca_forbidden_zone":     n_eca_forbidden,
        "n_ahet_verified":          n_ahet_verified,
        "n_masimo_low_quality":     flag_counts["masimo_missing_or_low_quality"],
        "n_paired_for_metrics":     n_paired,
        "mae_bpm":                  mae  if math.isfinite(mae)  else None,
        "rmse_bpm":                 rmse if math.isfinite(rmse) else None,
        "bias_bpm":                 bias if math.isfinite(bias) else None,
        "invalid_reason_counts":    ir_counts,
        "confidence_counts":        conf_counts,
        "frame_rate_hz":            frame_rate_hz,
        "window_s":                 window_s,
        "hop_s":                    hop_s,
        "locked_bin":               locked_bin,
        "locked_range_m":           locked_range_m,
        "phase_method":             phase_method,
        "k_max":                    k_max,
        "heart_band_hz":            list(heart_band_hz),
        # Step 6.3 — tracker summary
        "pre_tracker_n_valid_hr":   pre_n_valid,
        "pre_tracker_mae_bpm":      pre_mae if math.isfinite(pre_mae) else None,
        **tracker_stats,
    }

    if no_write:
        print("  Write skipped (--no-write)")
        return summary

    # -- Overwrite guard --
    sentinel = out_dir / "heart_windows.csv"
    if sentinel.exists() and not overwrite:
        raise FileExistsError(
            f"Results already exist in:\n  {out_dir}\n"
            "Re-run with --overwrite to replace them."
        )

    out_dir.mkdir(parents=True, exist_ok=True)

    # heart_windows.csv (full diagnostic)
    df.to_csv(out_dir / "heart_windows.csv", index=False)
    print(f"  heart_windows.csv -> {out_dir / 'heart_windows.csv'}")

    # summary.json
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )

    # NPZ intermediates (including tracker arrays)
    npz_path = out_dir / "heart_intermediates.npz"
    _write_npz(npz_path, intermediates, window_frames, n_fft, heart_freqs_hz,
               tracker_arrays=tracker_npz_arrays)
    print(f"  heart_intermediates.npz -> {npz_path}")

    # Plots
    if not no_plots:
        _plot_hr_comparison(df, out_dir / "hr_comparison.png", session_id)
        _plot_method_agreement(df, out_dir / "method_agreement.png", session_id)
        _plot_phase_and_quality(
            phase_clean, qmask_analysis, analysis_start, frame_rate_hz,
            out_dir / "phase_and_quality.png", session_id,
        )
        print(f"  Plots written to {out_dir}")

    # Step 7 contract CSV (compact subset)
    processed_dir = REPO_ROOT / paths["processed_dir"]
    processed_dir.mkdir(parents=True, exist_ok=True)
    contract_path = processed_dir / f"{session_id}.csv"
    if contract_path.exists() and not overwrite:
        print(f"  WARNING: {contract_path.name} already exists — use --overwrite to replace")
    else:
        contract_cols = [c for c in _CONTRACT_COLS if c in df.columns]
        df[contract_cols].to_csv(contract_path, index=False)
        print(f"  Step 7 contract CSV -> {contract_path}")

    return summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Step 6: Extract heart rate from locked-bin HDF5 cubes"
    )
    parser.add_argument(
        "--config", default="steps/step_6/config.yaml",
        help="Path to config file (default: steps/step_6/config.yaml)"
    )
    parser.add_argument("--session", metavar="ID",
                        help="Single session ID to process")
    parser.add_argument("--all", action="store_true",
                        help="Process all non-excluded sessions in manifest")
    parser.add_argument("--no-write", action="store_true",
                        help="Compute but do not write any output files")
    parser.add_argument("--overwrite", action="store_true",
                        help="Overwrite existing Step 6 outputs")
    parser.add_argument("--no-plots", action="store_true",
                        help="Skip plot generation")
    args = parser.parse_args()

    if not args.session and not args.all:
        parser.print_help()
        sys.exit(0)

    cfg_path = REPO_ROOT / args.config
    if not cfg_path.exists():
        sys.exit(f"ERROR: config not found: {cfg_path}")
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))

    np.random.seed(int(cfg.get("seed", 42)))

    paths             = cfg["paths"]
    cubes_dir         = REPO_ROOT / paths["cubes_dir"]
    manifest_path     = REPO_ROOT / paths["manifest"]
    data_raw          = REPO_ROOT / paths["raw_dir"]
    breathing_rate_dir = REPO_ROOT / paths["breathing_rate_dir"]
    results_dir       = REPO_ROOT / paths["results_dir"]

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    commit   = _git_commit()

    input_cfg  = cfg.get("input_policy", {})
    req_conf   = str(input_cfg.get("require_step3_confidence", "medium"))
    reject_rev = bool(input_cfg.get("reject_step3_review_required", True))
    req_qmask  = bool(input_cfg.get("require_quality_mask", True))
    skip_excl  = bool(input_cfg.get("skip_excluded_sessions", True))
    skip_no_h5 = bool(input_cfg.get("skip_missing_h5", True))
    skip_no_s5 = bool(input_cfg.get("skip_missing_step5", True))

    _conf_order = {"high": 2, "medium": 1, "low": 0, "quality_gated": -1}

    session_ids = [args.session] if args.session else manifest["session_id"].tolist()

    failed:    list[str] = []
    skipped:   list[str] = []
    summaries: list[dict] = []

    for sid in session_ids:
        print(f"\n{SEP}\n  {sid}\n{SEP}")
        rows = manifest[manifest["session_id"] == sid]
        if rows.empty:
            print(f"  ERROR: '{sid}' not found in manifest")
            failed.append(sid)
            continue
        mrow = rows.iloc[0]

        # Exclusion
        exclusion = str(mrow.get("exclusion_reason", "")).strip()
        if exclusion and skip_excl:
            if args.all:
                print(f"  SKIPPED (excluded): {exclusion}")
                skipped.append(sid)
                continue
            print(f"  WARNING: session excluded in manifest: {exclusion}")

        # HDF5 existence
        h5_path = cubes_dir / f"{sid}.h5"
        if not h5_path.exists():
            if args.all and skip_no_h5:
                print(f"  SKIPPED (no HDF5): {h5_path.name} — run Step 2 first")
                skipped.append(sid)
                continue
            print(f"  ERROR: HDF5 not found: {h5_path}")
            failed.append(sid)
            continue

        # Step 3 fields
        missing_s3 = [f for f in _STEP3_REQUIRED if str(mrow.get(f, "")).strip() == ""]
        if missing_s3:
            msg = f"missing Step 3 fields: {missing_s3} — run Step 3 first"
            if args.all:
                print(f"  SKIPPED ({msg})")
                skipped.append(sid)
                continue
            print(f"  ERROR: {msg}")
            failed.append(sid)
            continue

        # Confidence check
        confidence = str(mrow.get("chest_bin_confidence", "")).strip()
        if confidence and (_conf_order.get(confidence, -1) < _conf_order.get(req_conf, 1)):
            msg = f"chest_bin_confidence={confidence!r} below required={req_conf!r}"
            if args.all:
                print(f"  SKIPPED ({msg})")
                skipped.append(sid)
                continue
            print(f"  ERROR: {msg}")
            failed.append(sid)
            continue

        # Review-required check
        review_req = str(mrow.get("chest_bin_review_required", "")).strip().lower()
        if review_req == "true" and reject_rev:
            msg = "chest_bin_review_required=True — inspect Step 3 output first"
            if args.all:
                print(f"  SKIPPED ({msg})")
                skipped.append(sid)
                continue
            print(f"  ERROR: {msg}")
            failed.append(sid)
            continue

        # Quality mask check
        if req_qmask:
            try:
                with h5py.File(h5_path, "r") as f:
                    has_qm = "quality_mask" in f
            except Exception:
                has_qm = False
            if not has_qm:
                msg = "no /quality_mask — run Step 4 first"
                if args.all:
                    print(f"  SKIPPED ({msg})")
                    skipped.append(sid)
                    continue
                print(f"  ERROR: {msg}")
                failed.append(sid)
                continue

        # Step 5 contract check
        resp_path = breathing_rate_dir / f"{sid}.csv"
        if not resp_path.exists():
            msg = f"no Step 5 contract: {resp_path.name} — run Step 5 first"
            if args.all and skip_no_s5:
                print(f"  SKIPPED ({msg})")
                skipped.append(sid)
                continue
            print(f"  ERROR: {msg}")
            failed.append(sid)
            continue

        out_dir = results_dir / sid / "step_6"

        try:
            s = _process_session(
                sid, mrow, cfg, cubes_dir, data_raw, breathing_rate_dir,
                out_dir, commit,
                no_write=args.no_write,
                overwrite=args.overwrite,
                no_plots=args.no_plots,
            )
            summaries.append(s)
        except Exception as exc:
            import traceback
            print(f"  ERROR: {exc}")
            traceback.print_exc()
            failed.append(sid)
            continue

    # Cross-session summary
    processed = len(session_ids) - len(failed) - len(skipped)
    print(f"\n{SEP}")
    print(f"  Done: {processed}/{len(session_ids)} processed  "
          f"skipped={len(skipped)}  failed={len(failed)}")
    if summaries:
        valid_sums = [s for s in summaries if s.get("mae_bpm") is not None]
        if valid_sums:
            print("\n  Session summary:")
            for s in summaries:
                mae_str = f"{s['mae_bpm']:.2f}" if s.get("mae_bpm") is not None else "NaN"
                print(f"    {s['session_id']:<20} MAE={mae_str} bpm  "
                      f"valid={s['n_valid_hr']}/{s['n_windows']}")
    if failed:
        print(f"  Failed:  {failed}")
    if skipped:
        print(f"  Skipped: {skipped}")
    print(SEP)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
