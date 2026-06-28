"""Coverage gap diagnostic after Step 6.3.

Reads existing Step 6 result and diagnostic files and writes a focused
diagnostic that explains why coverage is low and what the temporal tracker
was able to rescue.  The report is tracker-aware:

- Tracker stats are derived from heart_windows.csv (already loaded, no extra
  file dependency).  Old runs without tracker columns degrade cleanly.
- Section titles and recommendations reflect current primary_blocker values,
  not hard-coded assumptions about which session is "zero yield".

Inputs (all read-only):
  results/<session>/step_6/heart_windows.csv
  results/<session>/step_6/heart_intermediates.npz
  results/diagnose/step6_hr/candidate_diagnostics.csv  (optional)

Output: results/diagnose/coverage_gaps/

Usage
-----
python -X utf8 scripts/diagnose_coverage_gaps.py \\
    --sessions test test2 test3 test4 test5 \\
    --results-root results \\
    --diag-root results/diagnose/step6_hr \\
    --out results/diagnose \\
    --overwrite
"""
from __future__ import annotations

import argparse
import logging
import math
import sys
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams.update({
    "figure.autolayout": False,
    "text.usetex": False,
    "font.family": "DejaVu Sans",
})
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_GOOD_THRESH_BPM    = 5.0
_BAD_THRESH_BPM     = 10.0
_SEVERE_THRESH_BPM  = 15.0

# step6_valid_fraction >= this threshold → session is a "success case" and
# is excluded from the "Next Implementation Target" AHET yield list.
_TRACKER_SUCCESS_FRACTION = 0.75

# "Near Masimo PR" guard — from ahet_deviation_hz=0.1 Hz * 60 = 6 bpm.
MASIMO_CANDIDATE_GUARD_BPM   = 6.0
# dB drop from first_pass to final spectrum that counts as ECA attenuation.
ECA_ATTENUATION_DB_THRESHOLD = 3.0
# Cardiac band used to compute the band-median reference for "high" detection.
CARDIAC_BAND_HZ  = (0.8, 2.0)
# k=6 overlap window (same width as MASIMO_CANDIDATE_GUARD_BPM).
K6_OVERLAP_BPM   = MASIMO_CANDIDATE_GUARD_BPM

log = logging.getLogger("diagnose_coverage_gaps")


# ---------------------------------------------------------------------------
# Scalar helpers
# ---------------------------------------------------------------------------

def _bool_val(v) -> bool:
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    if isinstance(v, (int, float, np.integer, np.floating)):
        return bool(v)
    return str(v).strip().lower() in ("true", "1", "1.0")


def _float_or_nan(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def _extract_tracker_stats(df: pd.DataFrame) -> dict:
    """Derive tracker statistics from a heart_windows.csv DataFrame.

    Returns backward-compatible defaults when tracker columns are absent
    (old runs without Step 6.3): present=False, counts=0,
    n_pre_tracker_valid=NaN (not applicable, not "known zero").
    """
    if "tracker_decision_type" not in df.columns:
        return {
            "temporal_tracker_present":            False,
            "n_pre_tracker_valid":                 float("nan"),
            "n_tracker_selected":                  0,
            "n_tracker_gap":                       0,
            "n_tracker_forced_blocked":            0,
            "n_tracker_selected_from_ahet_failed": 0,
            "n_tracker_gap_from_pre_valid":        0,
        }
    decision = df["tracker_decision_type"].fillna("")
    n_selected       = int((decision == "candidate").sum())
    n_gap            = int((decision == "gap").sum())
    n_forced_blocked = int((decision == "forced_blocked").sum())

    pre_valid_mask = pd.Series([False] * len(df), index=df.index)
    n_pre_valid = 0
    if "pre_tracker_hr_valid" in df.columns:
        pre_valid_mask = df["pre_tracker_hr_valid"].apply(_bool_val)
        n_pre_valid = int(pre_valid_mask.sum())

    n_gap_from_pre_valid = int(
        ((decision == "gap") & pre_valid_mask).sum()
    )

    n_ahet_rescued = 0
    if "pre_tracker_invalid_reason" in df.columns:
        pre_reason = df["pre_tracker_invalid_reason"].fillna("")
        n_ahet_rescued = int(
            ((decision == "candidate") & (pre_reason == "ahet_failed")).sum()
        )

    return {
        "temporal_tracker_present":            True,
        "n_pre_tracker_valid":                 n_pre_valid,
        "n_tracker_selected":                  n_selected,
        "n_tracker_gap":                       n_gap,
        "n_tracker_forced_blocked":            n_forced_blocked,
        "n_tracker_selected_from_ahet_failed": n_ahet_rescued,
        "n_tracker_gap_from_pre_valid":        n_gap_from_pre_valid,
    }


# ---------------------------------------------------------------------------
# Window classification
# ---------------------------------------------------------------------------

def _classify_window_class(row: dict) -> str:
    """Derive window class from a heart_windows.csv row dict."""
    if _bool_val(row.get("quality_gated", False)):
        return "quality_gated"
    invalid = str(row.get("invalid_reason", "") or "")
    if invalid == "resp_harmonic_coincident":
        return "resp_harmonic_rejected"
    if invalid == "resp_invalid":
        return "resp_invalid_or_edge_locked"
    if invalid == "ahet_failed":
        return "ahet_failed"
    if _bool_val(row.get("hr_valid", False)):
        abs_err = _float_or_nan(row.get("hr_abs_error_bpm"))
        if math.isfinite(abs_err):
            if abs_err < _GOOD_THRESH_BPM:
                return "good_valid"
            elif abs_err < _BAD_THRESH_BPM:
                return "acceptable_valid"
            elif abs_err < _SEVERE_THRESH_BPM:
                return "bad_valid"
            else:
                return "severe_bad_valid"
        return "good_valid"
    return "other_invalid"


def _classify_resp_blocker(row: dict) -> str:
    """Classify the respiration pass-through state for a heart_windows.csv row."""
    if _bool_val(row.get("quality_gated", False)):
        return "quality_gated"
    edge_locked = _bool_val(row.get("resp_edge_locked", False))
    edge_side   = str(row.get("resp_edge_lock_side", "") or "").strip().lower()
    resp_valid  = _bool_val(row.get("resp_valid", True))
    if edge_locked and edge_side == "low":
        return "resp_edge_locked_low"
    if edge_locked and edge_side == "high":
        return "resp_edge_locked_high"
    if edge_locked:
        return "resp_edge_locked_unknown"
    if not resp_valid:
        return "resp_invalid_not_edge"
    return "resp_available"


# ---------------------------------------------------------------------------
# Spectrum helpers
# ---------------------------------------------------------------------------

def _bin_power(spectrum: np.ndarray, freqs: np.ndarray, target_hz: float) -> float:
    """Power at the spectrum bin nearest to target_hz."""
    if not (math.isfinite(target_hz) and target_hz > 0):
        return float("nan")
    idx = int(np.argmin(np.abs(freqs - target_hz)))
    return float(spectrum[idx])


def _cardiac_band_median(spectrum: np.ndarray, freqs: np.ndarray) -> float:
    mask = (freqs >= CARDIAC_BAND_HZ[0]) & (freqs <= CARDIAC_BAND_HZ[1])
    if not mask.any():
        return float(np.median(spectrum))
    return float(np.median(spectrum[mask]))


def _is_above_band_median(
    power: float, spectrum: np.ndarray, freqs: np.ndarray
) -> bool:
    if not math.isfinite(power):
        return False
    return power > _cardiac_band_median(spectrum, freqs)


def _is_suppressed(fp_power: float, final_power: float) -> bool:
    if not (math.isfinite(fp_power) and math.isfinite(final_power) and fp_power > 0):
        return False
    ratio_db = 10.0 * math.log10((final_power + 1e-30) / (fp_power + 1e-30))
    return ratio_db < -ECA_ATTENUATION_DB_THRESHOLD


# ---------------------------------------------------------------------------
# k6 classification (pure function — exposed for unit tests)
# ---------------------------------------------------------------------------

def classify_k6_window(
    *,
    invalid_reason: str,
    quality_gated: bool,
    pre_eca_power: float,
    pre_eca_spectrum: Optional[np.ndarray],
    fp_power: float,
    freqs: Optional[np.ndarray],
    closest_cand_attempted: bool,
    closest_cand_passed: bool,
    pre_eca_available: bool,
) -> str:
    """Classify one window for the k6 overlap diagnostic.

    Priority (first matching rule wins):
    1. pre_candidate_blocked   - window blocked before AHET ran.
    2. pre_eca_missing         - AHET ran but old NPZ lacks heart_spectrum_pre_eca.
    3. weak_cardiac_snr        - pre-ECA power at Masimo bin below cardiac-band floor.
    4. eca_attenuation_likely  - pre-ECA high, first-pass ECA suppresses Masimo bin.
    5. ahet_gate_failure       - first-pass high, nearest candidate rejected by AHET.
    6. candidate_selection_miss - first-pass high, no candidate near Masimo attempted.
    """
    if quality_gated or invalid_reason in ("resp_invalid", "resp_harmonic_coincident"):
        return "pre_candidate_blocked"
    if (not pre_eca_available or pre_eca_spectrum is None
            or freqs is None or not math.isfinite(pre_eca_power)):
        return "pre_eca_missing"
    if not _is_above_band_median(pre_eca_power, pre_eca_spectrum, freqs):
        return "weak_cardiac_snr"
    if _is_suppressed(pre_eca_power, fp_power):
        return "eca_attenuation_likely"
    if closest_cand_attempted and not closest_cand_passed:
        return "ahet_gate_failure"
    return "candidate_selection_miss"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _load_session(session_id: str, results_root: Path) -> Optional[dict]:
    step6_dir = results_root / session_id / "step_6"
    csv_path  = step6_dir / "heart_windows.csv"
    npz_path  = step6_dir / "heart_intermediates.npz"
    missing = [p for p in (csv_path, npz_path) if not p.exists()]
    if missing:
        log.warning("Session %s: missing %s — skipping",
                    session_id, [str(p) for p in missing])
        return None
    df  = pd.read_csv(csv_path, keep_default_na=False, na_values=[""])
    npz = np.load(npz_path, allow_pickle=False)
    n_csv = len(df)
    n_npz = npz["phase_unwrapped"].shape[0] if "phase_unwrapped" in npz.files else n_csv
    if n_npz != n_csv:
        log.error("Session %s: CSV %d rows vs NPZ %d windows — skipping",
                  session_id, n_csv, n_npz)
        return None
    freqs = npz["heart_freqs_hz"] if "heart_freqs_hz" in npz.files else None
    return {
        "session_id": session_id,
        "df":         df,
        "npz":        npz,
        "freqs_hz":   freqs,
        "n_windows":  n_csv,
        "step6_dir":  step6_dir,
    }


def _load_candidate_diagnostics(diag_root: Path) -> Optional[pd.DataFrame]:
    p = diag_root / "candidate_diagnostics.csv"
    if not p.exists():
        log.warning("candidate_diagnostics.csv not found at %s — candidate columns absent",
                    diag_root)
        return None
    return pd.read_csv(p, keep_default_na=False, na_values=[""])


# ---------------------------------------------------------------------------
# Primary-blocker determination
# ---------------------------------------------------------------------------

def _determine_primary_blocker(
    class_counts: dict[str, int],
    k6_df: Optional[pd.DataFrame],
) -> str:
    n_good  = class_counts.get("good_valid", 0) + class_counts.get("acceptable_valid", 0)
    n_resp  = class_counts.get("resp_invalid_or_edge_locked", 0)
    n_ahet  = class_counts.get("ahet_failed", 0)
    n_harm  = class_counts.get("resp_harmonic_rejected", 0)
    n_qgate = class_counts.get("quality_gated", 0)

    if n_good > 0 and n_ahet == 0 and n_resp == 0:
        return "mostly_valid"

    candidates = {
        "resp_invalid_or_edge_locked": n_resp,
        "ahet_failed":                 n_ahet,
        "resp_harmonic_guard_blocked": n_harm,
        "quality_gated":               n_qgate,
    }
    dominant = max(candidates, key=lambda k: candidates[k])

    if dominant == "ahet_failed":
        if k6_df is not None and "k6_classification" in k6_df.columns:
            run = k6_df[k6_df["k6_classification"] != "pre_candidate_blocked"]
            if len(run) > 0:
                n_eca = (run["k6_classification"] == "eca_attenuation_likely").sum()
                if n_eca / len(run) > 0.5:
                    return "k6_resp_cardiac_overlap"
        return "step6_ahet_ratio_low"

    if dominant == "resp_harmonic_guard_blocked":
        return "resp_harmonic_guard_blocked"

    return dominant


# ---------------------------------------------------------------------------
# Output: coverage_summary.csv
# ---------------------------------------------------------------------------

def _write_coverage_summary(
    sessions: list[dict],
    k6_dfs: dict[str, pd.DataFrame],
    out_dir: Path,
) -> pd.DataFrame:
    rows = []
    for sd in sessions:
        sid = sd["session_id"]
        df  = sd["df"]
        n   = len(df)
        classes = [_classify_window_class(r) for r in df.to_dict("records")]
        cc: dict[str, int] = {}
        for c in classes:
            cc[c] = cc.get(c, 0) + 1
        n_good = cc.get("good_valid", 0)
        n_acc  = cc.get("acceptable_valid", 0)
        n_bad  = cc.get("bad_valid", 0)
        n_sev  = cc.get("severe_bad_valid", 0)
        n_valid = n_good + n_acc + n_bad + n_sev
        primary = _determine_primary_blocker(cc, k6_dfs.get(sid))
        tracker_stats = _extract_tracker_stats(df)
        rows.append({
            "session_id":                    sid,
            "n_windows":                     n,
            "n_good_valid":                  n_good,
            "n_acceptable_valid":            n_acc,
            "n_bad_valid":                   n_bad,
            "n_severe_bad_valid":            n_sev,
            "n_valid":                       n_valid,
            "step6_valid_fraction":          round(n_valid / n, 4) if n > 0 else float("nan"),
            "n_ahet_failed":                 cc.get("ahet_failed", 0),
            "n_resp_invalid_or_edge_locked": cc.get("resp_invalid_or_edge_locked", 0),
            "n_resp_harmonic_rejected":      cc.get("resp_harmonic_rejected", 0),
            "n_quality_gated":               cc.get("quality_gated", 0),
            "n_other_invalid":               cc.get("other_invalid", 0),
            "primary_blocker":               primary,
            # tracker-aware columns (backward-compat: absent cols → False/0/NaN)
            "temporal_tracker_present":            tracker_stats["temporal_tracker_present"],
            "n_pre_tracker_valid":                 tracker_stats["n_pre_tracker_valid"],
            "n_tracker_selected":                  tracker_stats["n_tracker_selected"],
            "n_tracker_gap":                       tracker_stats["n_tracker_gap"],
            "n_tracker_forced_blocked":            tracker_stats["n_tracker_forced_blocked"],
            "n_tracker_selected_from_ahet_failed": tracker_stats["n_tracker_selected_from_ahet_failed"],
            "n_tracker_gap_from_pre_valid":        tracker_stats["n_tracker_gap_from_pre_valid"],
        })
    out = pd.DataFrame(rows)
    p = out_dir / "coverage_summary.csv"
    out.to_csv(p, index=False)
    log.info("Wrote %s (%d sessions)", p, len(out))
    return out


# ---------------------------------------------------------------------------
# Output: resp_pass_through_diagnostics.csv
# ---------------------------------------------------------------------------

def _write_resp_pass_through(sessions: list[dict], out_dir: Path) -> pd.DataFrame:
    _KEEP = [
        "session_id", "window_index", "start_epoch", "end_epoch",
        "resp_valid", "resp_confidence", "resp_edge_locked", "resp_edge_lock_side",
        "radar_rr_bpm", "resp_peak_hz", "quality_gated", "invalid_reason",
    ]
    _OPTIONAL = ["masimo_rr_bpm"]
    dfs = []
    for sd in sessions:
        df = sd["df"].copy()
        df["resp_blocker"] = [_classify_resp_blocker(r) for r in df.to_dict("records")]
        cols = [c for c in _KEEP if c in df.columns] + ["resp_blocker"]
        for oc in _OPTIONAL:
            if oc in df.columns:
                cols.append(oc)
        dfs.append(df[cols])
    out = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    p = out_dir / "resp_pass_through_diagnostics.csv"
    out.to_csv(p, index=False)
    log.info("Wrote %s (%d rows)", p, len(out))
    return out


# ---------------------------------------------------------------------------
# Output: step6_candidate_yield_diagnostics.csv
# ---------------------------------------------------------------------------

def _write_candidate_yield(
    sessions: list[dict],
    cand_df: Optional[pd.DataFrame],
    out_dir: Path,
) -> Optional[pd.DataFrame]:
    if cand_df is None:
        log.warning("No candidate_diagnostics — skipping step6_candidate_yield_diagnostics.csv")
        return None

    _HW_COLS = [
        "session_id", "window_index", "hr_valid", "invalid_reason",
        "masimo_pr_bpm", "radar_hr_bpm",
        # tracker context — additive, absent in old runs without Step 6.3
        "hr_source", "tracker_decision_type", "pre_tracker_hr_valid",
        "pre_tracker_invalid_reason", "tracker_fundamental_ratio_db",
        "tracker_node_score",
    ]
    _CAND_COLS = [
        "session_id", "window_index",
        "candidate_rank", "candidate_attempted", "candidate_refined_hz",
        "candidate_peak_magnitude", "candidate_prominence",
        "peak_to_floor_ratio_db", "candidate_rejection_code",
        "candidate_rejection_reason", "candidate_nearest_resp_harmonic_k",
        "candidate_distance_to_nearest_resp_harmonic_bpm",
        "is_accepted", "candidate_is_low",
    ]
    hw_parts = []
    for sd in sessions:
        df = sd["df"].copy()
        hw_parts.append(df[[c for c in _HW_COLS if c in df.columns]])
    hw = pd.concat(hw_parts, ignore_index=True) if hw_parts else pd.DataFrame()
    cand = cand_df[[c for c in _CAND_COLS if c in cand_df.columns]].copy()
    out = cand.merge(hw, on=["session_id", "window_index"], how="left")
    p = out_dir / "step6_candidate_yield_diagnostics.csv"
    out.to_csv(p, index=False)
    log.info("Wrote %s (%d rows)", p, len(out))
    return out


# ---------------------------------------------------------------------------
# Output: test5_k6_overlap_diagnostics.csv
# ---------------------------------------------------------------------------

def _find_closest_candidate(
    window_cands: pd.DataFrame, masimo_hz: float
) -> Optional[pd.Series]:
    """Return the attempted candidate row closest to masimo_hz, or None."""
    if not math.isfinite(masimo_hz) or len(window_cands) == 0:
        return None
    attempted = window_cands[window_cands["candidate_attempted"].apply(_bool_val)]
    if attempted.empty:
        return None
    refined = pd.to_numeric(attempted["candidate_refined_hz"], errors="coerce")
    dist = (refined - masimo_hz).abs()
    if dist.isna().all():
        return None
    best_idx = dist.idxmin()
    dist_bpm = float(dist[best_idx]) * 60.0
    if dist_bpm > MASIMO_CANDIDATE_GUARD_BPM:
        return None
    return attempted.loc[best_idx]


def _delta_db(num: float, den: float) -> float:
    """20*log10(num/den), matching vitals.py amplitude-spectrum convention."""
    if not (math.isfinite(num) and math.isfinite(den) and den > 0 and num > 0):
        return float("nan")
    return 20.0 * math.log10(num / den)


def _write_k6_overlap(
    sessions: list[dict],
    cand_df: Optional[pd.DataFrame],
    out_dir: Path,
    k6_session: str = "test5",
) -> Optional[pd.DataFrame]:
    sd = next((s for s in sessions if s["session_id"] == k6_session), None)
    if sd is None:
        log.warning("Session %s not in loaded sessions — skipping k6 overlap table",
                    k6_session)
        return None

    df    = sd["df"]
    npz   = sd["npz"]
    freqs = sd["freqs_hz"]

    _npz_keys = set(npz.files) if hasattr(npz, "files") else set(npz.keys())
    pre_eca_available = "heart_spectrum_pre_eca" in _npz_keys
    pre_eca_arr = npz["heart_spectrum_pre_eca"] if pre_eca_available else None
    fp_arr      = npz["heart_spectrum_first_pass"] if "heart_spectrum_first_pass" in _npz_keys else None
    fin_arr     = npz["heart_spectrum"]             if "heart_spectrum"             in _npz_keys else None
    base_arr    = npz["baseline_spectrum"]          if "baseline_spectrum"          in _npz_keys else None

    if not pre_eca_available:
        log.warning("Session %s NPZ lacks heart_spectrum_pre_eca "
                    "(old run) — windows will classify as pre_eca_missing", k6_session)

    sess_cand: Optional[pd.DataFrame] = None
    if cand_df is not None:
        sess_cand = cand_df[cand_df["session_id"] == k6_session].copy()
        if "window_index" in sess_cand.columns:
            sess_cand["window_index"] = pd.to_numeric(
                sess_cand["window_index"], errors="coerce"
            ).astype("Int64")

    rows = []
    records = df.to_dict("records")
    for wi, row in enumerate(records):
        window_idx = int(row.get("window_index", wi))
        rr_bpm     = _float_or_nan(row.get("radar_rr_bpm"))
        masimo_bpm = _float_or_nan(row.get("masimo_pr_bpm"))
        masimo_hz  = masimo_bpm / 60.0 if math.isfinite(masimo_bpm) else float("nan")
        k6_bpm     = 6.0 * rr_bpm if math.isfinite(rr_bpm) else float("nan")
        k6_dist    = (
            abs(k6_bpm - masimo_bpm)
            if math.isfinite(k6_bpm) and math.isfinite(masimo_bpm)
            else float("nan")
        )
        k6_overlaps = bool(math.isfinite(k6_dist) and k6_dist < K6_OVERLAP_BPM)

        def _pw(arr: Optional[np.ndarray]) -> float:
            if arr is None or freqs is None:
                return float("nan")
            return _bin_power(arr[wi], freqs, masimo_hz)

        pre_eca_power = _pw(pre_eca_arr)
        fp_power      = _pw(fp_arr)
        fin_power     = _pw(fin_arr)
        base_power    = _pw(base_arr)

        pre_to_fp_db  = _delta_db(fp_power, pre_eca_power)
        fp_to_fin_db  = _delta_db(fin_power, fp_power)

        # Closest attempted candidate to Masimo PR
        cand_refined_hz = float("nan")
        cand_ratio_db   = float("nan")
        cand_reason     = ""
        cand_attempted  = False
        cand_passed     = False
        if sess_cand is not None:
            wcand = sess_cand[sess_cand["window_index"] == window_idx]
            best = _find_closest_candidate(wcand, masimo_hz)
            if best is not None:
                cand_refined_hz = _float_or_nan(best.get("candidate_refined_hz"))
                cand_ratio_db   = _float_or_nan(best.get("peak_to_floor_ratio_db"))
                cand_reason     = str(best.get("candidate_rejection_reason", ""))
                cand_attempted  = True
                cand_passed     = _bool_val(best.get("candidate_passed", False))

        invalid = str(row.get("invalid_reason", "") or "")
        qgated  = _bool_val(row.get("quality_gated", False))
        blocked = (qgated or invalid in ("resp_invalid", "resp_harmonic_coincident"))

        pre_eca_row = pre_eca_arr[wi] if pre_eca_arr is not None else None

        k6_cls = classify_k6_window(
            invalid_reason=invalid,
            quality_gated=qgated,
            pre_eca_power=pre_eca_power,
            pre_eca_spectrum=pre_eca_row,
            fp_power=fp_power,
            freqs=freqs,
            closest_cand_attempted=cand_attempted,
            closest_cand_passed=cand_passed,
            pre_eca_available=pre_eca_available,
        )

        rows.append({
            "session_id":                          k6_session,
            "window_index":                        window_idx,
            "start_epoch":                         _float_or_nan(row.get("start_epoch")),
            "radar_rr_bpm":                        rr_bpm,
            "k6_bpm":                              k6_bpm,
            "masimo_pr_bpm":                       masimo_bpm,
            "k6_masimo_dist_bpm":                  k6_dist,
            "k6_overlaps_masimo":                  k6_overlaps,
            "window_blocked_before_ahet":          blocked,
            "closest_cand_refined_hz":             cand_refined_hz,
            "closest_cand_peak_to_floor_ratio_db": cand_ratio_db,
            "closest_cand_rejection_reason":       cand_reason,
            "closest_cand_attempted":              cand_attempted,
            "closest_cand_passed":                 cand_passed,
            "pre_eca_power_at_masimo_bin":         pre_eca_power,
            "first_pass_power_at_masimo_bin":      fp_power,
            "final_power_at_masimo_bin":           fin_power,
            "baseline_power_at_masimo_bin":        base_power,
            "pre_to_first_pass_delta_db":          pre_to_fp_db,
            "first_pass_to_final_delta_db":        fp_to_fin_db,
            "k6_classification":                   k6_cls,
        })

    out = pd.DataFrame(rows)
    p = out_dir / "test5_k6_overlap_diagnostics.csv"
    out.to_csv(p, index=False)
    log.info("Wrote %s (%d rows)", p, len(out))
    return out


# ---------------------------------------------------------------------------
# Output: diagnostic_report.md
# ---------------------------------------------------------------------------

def _write_diagnostic_report(
    sessions: list[dict],
    summary_df: pd.DataFrame,
    k6_df: Optional[pd.DataFrame],
    out_dir: Path,
) -> None:
    L: list[str] = []

    def h(text: str, level: int = 2) -> None:
        L.append("\n" + "#" * level + " " + text + "\n")

    def p(text: str) -> None:
        L.append(text + "\n")

    L.append("# Coverage Gap Diagnostic Report\n")

    h("Session Overview")
    hdr = ["session_id", "n_windows", "n_good_valid", "n_valid",
           "step6_valid_fraction", "n_ahet_failed",
           "n_resp_invalid_or_edge_locked", "primary_blocker"]
    hdr = [c for c in hdr if c in summary_df.columns]
    p(summary_df[hdr].to_string(index=False))

    # --- test2 ---
    # Determine test2's primary_blocker so we can frame the section correctly.
    _t2_sum = (
        summary_df[summary_df["session_id"] == "test2"]
        if "session_id" in summary_df.columns
        else pd.DataFrame()
    )
    t2_primary_blocker = (
        str(_t2_sum["primary_blocker"].values[0])
        if len(_t2_sum) > 0 and "primary_blocker" in _t2_sum.columns
        else "resp_invalid_or_edge_locked"
    )

    if t2_primary_blocker == "resp_invalid_or_edge_locked":
        h("test2: Respiration Pass-Through Failure")
    else:
        h("test2: Residual Respiration Pass-Through")

    t2 = next((sd for sd in sessions if sd["session_id"] == "test2"), None)
    if t2 is not None:
        df2 = t2["df"]
        n   = len(df2)
        bc: dict[str, int] = {}
        for r in df2.to_dict("records"):
            b = _classify_resp_blocker(r)
            bc[b] = bc.get(b, 0) + 1
        p(f"Total windows: {n}")
        p("\nRespiration blocker breakdown:")
        for b, cnt in sorted(bc.items(), key=lambda x: -x[1]):
            p(f"  {b}: {cnt} ({100*cnt/n:.0f}%)")
        n_blocked = sum(v for k, v in bc.items() if k != "resp_available")

        if t2_primary_blocker != "resp_invalid_or_edge_locked":
            p(f"\nRespiration is not the dominant blocker "
              f"(primary_blocker: {t2_primary_blocker}).")
            p(f"Only {n_blocked}/{n} windows ({100*n_blocked/n:.0f}%) have "
              f"residual resp failures. "
              f"The dominant yield target is Step 6 tracker/AHET throughput.")
        else:
            p(f"\n{n_blocked}/{n} windows ({100*n_blocked/n:.0f}%) have invalid or "
              f"edge-locked respiration before AHET runs.")
            dominant = max(bc, key=lambda k: bc[k])
            if dominant in ("resp_edge_locked_low", "resp_edge_locked_high"):
                p("\nPrimary cause: respiration estimator edge-locked to boundary.")
                p("Recommendation: fix Step 5 edge-lock behaviour for test2 "
                  "before tuning Step 6.")
            elif dominant == "resp_invalid_not_edge":
                p("\nPrimary cause: respiration estimation failed (not edge-lock).")
                p("Recommendation: investigate Step 5 respiration estimation for test2.")
    else:
        p("test2 data not available.")

    # --- test5 ---
    _t5_sum = (
        summary_df[summary_df["session_id"] == "test5"]
        if "session_id" in summary_df.columns
        else pd.DataFrame()
    )
    _t5_n_valid   = int(_t5_sum["n_valid"].values[0])   if len(_t5_sum) > 0 and "n_valid"   in _t5_sum.columns else 0
    _t5_n_windows = int(_t5_sum["n_windows"].values[0]) if len(_t5_sum) > 0 and "n_windows" in _t5_sum.columns else 0
    if _t5_n_valid > 0:
        h(f"test5: Partial Heart Rate Yield ({_t5_n_valid}/{_t5_n_windows} valid)")
    else:
        h("test5: Heart Rate Yield Zero")
    if k6_df is not None and len(k6_df) > 0:
        n5  = len(k6_df)
        cls = k6_df["k6_classification"].value_counts().to_dict()
        p(f"Total windows: {n5}")
        p("\nk6 classification breakdown:")
        for c, cnt in sorted(cls.items(), key=lambda x: -x[1]):
            p(f"  {c}: {cnt} ({100*cnt/n5:.0f}%)")
        n_overlap = int(k6_df["k6_overlaps_masimo"].sum()) if "k6_overlaps_masimo" in k6_df.columns else 0
        p(f"\nk=6 harmonic overlaps Masimo PR (within {K6_OVERLAP_BPM:.0f} bpm): "
          f"{n_overlap}/{n5} windows")
        run = k6_df[k6_df["k6_classification"] != "pre_candidate_blocked"]
        if len(run) > 0:
            n_eca   = int((run["k6_classification"] == "eca_attenuation_likely").sum())
            n_weak  = int((run["k6_classification"] == "weak_cardiac_snr").sum())
            n_ahet  = int((run["k6_classification"] == "ahet_gate_failure").sum())
            frac_eca  = n_eca  / len(run)
            frac_weak = n_weak / len(run)
            frac_ahet = n_ahet / len(run)

            # Report peak-to-floor ratio stats if available.
            if "closest_cand_peak_to_floor_ratio_db" in k6_df.columns:
                ratios = pd.to_numeric(
                    k6_df.loc[k6_df["k6_classification"] == "ahet_gate_failure",
                              "closest_cand_peak_to_floor_ratio_db"],
                    errors="coerce",
                ).dropna()
                ratio_summary = (
                    f" (ratio_db range {ratios.min():.1f} to {ratios.max():.1f} dB, "
                    f"median {ratios.median():.1f} dB)"
                    if len(ratios) > 0 else ""
                )
            else:
                ratio_summary = ""

            # Check ECA suppression using pre_to_first_pass_delta_db (new field).
            # Fall back to fp==final comparison for old NPZs without pre_eca.
            if "pre_to_first_pass_delta_db" in k6_df.columns:
                deltas = pd.to_numeric(
                    k6_df["pre_to_first_pass_delta_db"], errors="coerce"
                ).dropna()
                if len(deltas) > 0:
                    median_delta = float(deltas.median())
                    # ECA is not acting if median delta is within 1 dB of 0
                    eca_not_acting = abs(median_delta) < 1.0
                    eca_note = (f"Median pre-ECA to first-pass delta = "
                                f"{median_delta:+.1f} dB "
                                f"({len(deltas)} estimator-run windows).")
                else:
                    eca_not_acting = False
                    eca_note = "pre_to_first_pass_delta_db has no finite values."
            else:
                eca_not_acting = False
                eca_note = "pre_to_first_pass_delta_db column absent (old NPZ)."

            if frac_eca > 0.5:
                p("\nPrimary cause: ECA suppression of cardiac signal "
                  "(eca_attenuation_likely dominates estimator-run windows).")
                p("heart_spectrum_pre_eca has power near Masimo PR but "
                  "heart_spectrum_first_pass does not.")
                p("Recommendation: investigate ECA forbidden-zone handling for k=6 "
                  "and consider a cardiac-safe ECA strategy.")
            elif frac_ahet > 0.5:
                p(f"\nPrimary cause: AHET ratio_db gate ({n_ahet}/{len(run)} "
                  f"estimator-run windows){ratio_summary}.")
                p("The cardiac peak near Masimo PR is present in the pre-ECA "
                  "spectrum but has negative peak-to-floor ratio after first-pass "
                  "ECA — it does not stand above the local noise floor in spec1.")
                p(eca_note)
                if eca_not_acting:
                    p("ECA is NOT attenuating the Masimo PR bin between pre-ECA "
                      "and first-pass (delta near 0 dB). The low ratio_db reflects "
                      "genuine weak cardiac SNR in the AHET evidence check, not "
                      "ECA over-suppression.")
                    p("The geometric k=6 overlap is real but ECA is not the failure "
                      "mechanism. The cardiac peak-to-floor ratio is simply "
                      "insufficient in this session.")
                else:
                    p("ECA IS attenuating the Masimo PR bin: pre-ECA power drops "
                      "into first-pass. The weak ratio_db may be a consequence of "
                      "ECA suppression of the cardiac signal at the k=6 harmonic.")
                p("Recommendation: investigate signal conditions in test5 "
                  "(subject motion, distance, posture). "
                  "Consider whether the AHET floor threshold is too tight or "
                  "whether a lower-noise re-capture is needed.")
            elif frac_weak > 0.5:
                p("\nPrimary cause: weak cardiac SNR in pre-ECA spectrum.")
                p("Recommendation: investigate signal acquisition quality for test5.")
            else:
                p("\nMixed failure modes — further investigation needed.")
    else:
        p("test5 k6 overlap data not available.")

    # --- Guard-blocked ---
    h("Respiratory-Harmonic Guard Blocked Windows")
    n_harm = int(summary_df["n_resp_harmonic_rejected"].sum()) \
        if "n_resp_harmonic_rejected" in summary_df.columns else 0
    p(f"Windows blocked by respiratory-harmonic guard: {n_harm}")
    if n_harm > 0:
        p("Note: small number of windows affected — low priority.")
        for sd in sessions:
            cc2: dict[str, int] = {}
            for c in [_classify_window_class(r) for r in sd["df"].to_dict("records")]:
                cc2[c] = cc2.get(c, 0) + 1
            nh = cc2.get("resp_harmonic_rejected", 0)
            if nh > 0:
                p(f"  {sd['session_id']}: {nh} windows")

    # --- Next target ---
    h("Next Implementation Target")
    resp_blocked_sids: list[str] = []
    ahet_blocked_sids: list[str] = []
    for _, row in summary_df.iterrows():
        blocker  = str(row.get("primary_blocker", ""))
        fraction = _float_or_nan(row.get("step6_valid_fraction"))
        sid      = str(row.get("session_id", ""))
        if blocker == "resp_invalid_or_edge_locked":
            resp_blocked_sids.append(sid)
        elif (blocker == "step6_ahet_ratio_low"
              and math.isfinite(fraction)
              and fraction < _TRACKER_SUCCESS_FRACTION):
            ahet_blocked_sids.append(sid)

    item_n = 0
    if resp_blocked_sids:
        item_n += 1
        p(f"{item_n}. Fix respiration failures for: "
          f"{', '.join(resp_blocked_sids)} "
          f"(primary_blocker: resp_invalid_or_edge_locked).")
    if ahet_blocked_sids:
        item_n += 1
        p(f"{item_n}. Improve Step 6 tracker/AHET yield for: "
          f"{', '.join(ahet_blocked_sids)} "
          f"(primary_blocker: step6_ahet_ratio_low).")
    if n_harm > 0:
        p(f"Note: {n_harm} window(s) blocked by respiratory-harmonic guard — "
          f"low priority, small number affected.")
    if item_n == 0:
        p("No critical yield gaps identified across loaded sessions.")

    p("\n---")
    p("*Generated by `scripts/diagnose_coverage_gaps.py`.*")

    out_dir.joinpath("diagnostic_report.md").write_text(
        "\n".join(L), encoding="utf-8"
    )
    log.info("Wrote %s", out_dir / "diagnostic_report.md")


# ---------------------------------------------------------------------------
# Optional plots
# ---------------------------------------------------------------------------

def _plot_test2_resp_timeline(sessions: list[dict], out_dir: Path) -> None:
    sd = next((s for s in sessions if s["session_id"] == "test2"), None)
    if sd is None:
        return
    df = sd["df"]
    rr = pd.to_numeric(df["radar_rr_bpm"], errors="coerce")
    edge = df["resp_edge_locked"].apply(_bool_val)
    xs   = list(range(len(df)))

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(xs, rr, color="steelblue", linewidth=1.2, label="Radar RR (bpm)")
    edge_xs = [i for i, e in enumerate(edge) if e]
    if edge_xs:
        ax.scatter(edge_xs, rr.iloc[edge_xs], color="red", zorder=5,
                   s=25, label="Edge-locked")
    ax.set_xlabel("Window index")
    ax.set_ylabel("Respiratory rate (bpm)")
    ax.set_title("test2: Radar RR and edge-lock state")
    ax.legend()
    _save_plot(fig, out_dir, "test2_resp_pass_through_timeline.png")


def _plot_test5_k6_vs_masimo(k6_df: pd.DataFrame, out_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4))
    xs = k6_df["window_index"].tolist()
    ax.plot(xs, pd.to_numeric(k6_df["masimo_pr_bpm"], errors="coerce"),
            label="Masimo PR (bpm)", color="green", linewidth=1.2)
    ax.plot(xs, pd.to_numeric(k6_df["k6_bpm"], errors="coerce"),
            label="k=6 harmonic (bpm)", color="orange", linestyle="--", linewidth=1.2)
    ax.fill_between(
        xs,
        pd.to_numeric(k6_df["k6_bpm"], errors="coerce") - K6_OVERLAP_BPM,
        pd.to_numeric(k6_df["k6_bpm"], errors="coerce") + K6_OVERLAP_BPM,
        alpha=0.15, color="orange", label=f"k=6 ± {K6_OVERLAP_BPM:.0f} bpm",
    )
    ax.set_xlabel("Window index")
    ax.set_ylabel("BPM")
    ax.set_title("test5: Masimo PR vs k=6 respiratory harmonic")
    ax.legend()
    _save_plot(fig, out_dir, "test5_k6_vs_masimo_pr.png")


def _plot_test5_bin_power(k6_df: pd.DataFrame, out_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4))
    xs = k6_df["window_index"].tolist()
    for col, label, style in [
        ("fp_power_at_masimo_bin",       "first_pass",  "-"),
        ("final_power_at_masimo_bin",    "final",       "--"),
        ("baseline_power_at_masimo_bin", "baseline",    ":"),
    ]:
        if col in k6_df.columns:
            ax.plot(xs, pd.to_numeric(k6_df[col], errors="coerce"),
                    label=label, linestyle=style, linewidth=1.2)
    ax.set_xlabel("Window index")
    ax.set_ylabel("Spectrum power at Masimo-expected bin")
    ax.set_title("test5: Spectrum power at Masimo PR frequency by stage")
    ax.legend()
    _save_plot(fig, out_dir, "test5_masimo_bin_power_by_stage.png")


def _plot_test5_candidates(
    sessions: list[dict],
    cand_df: Optional[pd.DataFrame],
    k6_session: str,
    out_dir: Path,
) -> None:
    sd = next((s for s in sessions if s["session_id"] == k6_session), None)
    if sd is None or cand_df is None:
        return
    sc = cand_df[cand_df["session_id"] == k6_session]
    r0 = sc[sc["candidate_rank"] == 0].copy()
    if r0.empty:
        return
    wi   = pd.to_numeric(r0["window_index"],      errors="coerce")
    hz   = pd.to_numeric(r0["candidate_refined_hz"], errors="coerce") * 60.0
    rdb  = pd.to_numeric(r0["peak_to_floor_ratio_db"], errors="coerce")

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
    ax1.plot(wi, hz, color="steelblue", linewidth=1.2, label="Rank-0 candidate (bpm)")
    hw = sd["df"]
    masimo = pd.to_numeric(hw["masimo_pr_bpm"], errors="coerce")
    ax1.plot(range(len(hw)), masimo, color="green", linestyle="--",
             linewidth=1.0, label="Masimo PR")
    ax1.set_ylabel("BPM")
    ax1.set_title(f"{k6_session}: Candidate evidence over time")
    ax1.legend()

    ax2.plot(wi, rdb, color="darkorange", linewidth=1.2)
    ax2.axhline(0, color="red", linestyle=":", linewidth=0.8)
    ax2.set_xlabel("Window index")
    ax2.set_ylabel("Peak-to-floor ratio (dB)")
    _save_plot(fig, out_dir, "test5_candidate_evidence_timeline.png")


def _save_plot(fig: plt.Figure, out_dir: Path, name: str) -> None:
    p = out_dir / "plots" / name
    p.parent.mkdir(exist_ok=True)
    fig.savefig(p, dpi=120, bbox_inches="tight")
    plt.close(fig)
    log.info("Wrote %s", p)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sessions", nargs="+", required=True,
                    help="Session IDs to process")
    ap.add_argument("--results-root",
                    default=str(REPO_ROOT / "results"),
                    help="Root directory containing per-session step_6/ results")
    ap.add_argument("--diag-root",
                    default=str(REPO_ROOT / "results" / "diagnose" / "step6_hr"),
                    help="Directory containing candidate_diagnostics.csv")
    ap.add_argument("--out",
                    default=str(REPO_ROOT / "results" / "diagnose"),
                    help="Parent output directory; writes to <out>/coverage_gaps/")
    ap.add_argument("--overwrite", action="store_true",
                    help="Overwrite existing output directory")
    ap.add_argument("--no-plots", action="store_true",
                    help="Skip optional plot generation")
    ap.add_argument("--k6-session", default="test5",
                    help="Session to analyse for k=6 overlap (default: test5)")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )

    results_root = Path(args.results_root)
    diag_root    = Path(args.diag_root)
    out_dir      = Path(args.out) / "coverage_gaps"

    if out_dir.exists() and not args.overwrite:
        log.error("%s exists; re-run with --overwrite", out_dir)
        sys.exit(1)
    out_dir.mkdir(parents=True, exist_ok=True)

    sessions = []
    for sid in args.sessions:
        sd = _load_session(sid, results_root)
        if sd is not None:
            sessions.append(sd)

    if not sessions:
        log.error("No sessions loaded — aborting")
        sys.exit(1)

    cand_df = _load_candidate_diagnostics(diag_root)

    # k6 table must be built before coverage_summary (needed for primary_blocker).
    k6_df  = _write_k6_overlap(sessions, cand_df, out_dir, k6_session=args.k6_session)
    k6_dfs = {args.k6_session: k6_df} if k6_df is not None else {}

    summary_df = _write_coverage_summary(sessions, k6_dfs, out_dir)
    _write_resp_pass_through(sessions, out_dir)
    _write_candidate_yield(sessions, cand_df, out_dir)
    _write_diagnostic_report(sessions, summary_df, k6_df, out_dir)

    if not args.no_plots:
        _plot_test2_resp_timeline(sessions, out_dir)
        if k6_df is not None:
            _plot_test5_k6_vs_masimo(k6_df, out_dir)
            _plot_test5_bin_power(k6_df, out_dir)
        _plot_test5_candidates(sessions, cand_df, args.k6_session, out_dir)

    log.info("Done — output: %s", out_dir)


if __name__ == "__main__":
    main()
