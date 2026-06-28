"""Temporal tracker for Step 6 heart-rate extraction.

Shared between the production Step 6 runner (steps/step_6/extract_heart_rate.py)
and the diagnostic grid search (scripts/diagnose_step6_candidate_tracks.py).

Public API
----------
compute_fundamental_ratio_db  -- SNR of a candidate peak vs cardiac-band floor
node_score                    -- Viterbi node score (configurable weights)
build_candidate_pool          -- per-window candidate lists from Step 6 outputs
_filter_eligible              -- apply per-combo eligibility gate
viterbi_track                 -- max-score DP over candidate windows
apply_tracker                 -- production entry point: runs tracker, updates rows
"""
from __future__ import annotations

import dataclasses
import math
from typing import Optional

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Constants (defaults; production reads from config)
# ---------------------------------------------------------------------------

GAP_COST       = -8.0
HEART_BAND_HZ  = (0.8, 2.0)
GUARD_HZ       = 0.05
K_MAX_HARMONIC = 10

_AHET_MAX_CANDIDATES = 3  # matches vitals.AHET_MAX_CANDIDATES; local copy avoids scipy import

# tracker_decision_code values written to the NPZ
_CODE_CANDIDATE      = 0
_CODE_GAP            = 1
_CODE_FORCED_BLOCKED = 2
_CODE_NOT_RUN        = -1

# Mirrors _REJECTION_CODE_STR in extract_heart_rate.py
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


def _rejection_code_to_str(code) -> str:
    try:
        code = int(code)
    except (TypeError, ValueError):
        return f"unknown_code_{code}"
    return _REJECTION_CODE_STR.get(code, f"unknown_code_{code}")


# ---------------------------------------------------------------------------
# Harmonic helpers
# ---------------------------------------------------------------------------

def _nearest_resp_harmonic(
    hz_query: float,
    resp_peak_hz: float,
    k_max: int = K_MAX_HARMONIC,
) -> tuple[int, float, float]:
    """Return (best_k, harmonic_hz, distance_bpm) for k in 1..k_max."""
    if not (math.isfinite(hz_query) and math.isfinite(resp_peak_hz) and resp_peak_hz > 0):
        return (-1, float("nan"), float("nan"))
    best_k, best_dist = -1, float("inf")
    for k in range(1, k_max + 1):
        dist = abs(hz_query - k * resp_peak_hz)
        if dist < best_dist:
            best_k, best_dist = k, dist
    return (best_k, best_k * resp_peak_hz, best_dist * 60.0)


def _within_guard(
    hz_query: float,
    resp_peak_hz: float,
    guard_hz: float,
    k_max: int = K_MAX_HARMONIC,
) -> bool:
    if not (math.isfinite(hz_query) and math.isfinite(resp_peak_hz) and resp_peak_hz > 0):
        return False
    for k in range(1, k_max + 1):
        if abs(hz_query - k * resp_peak_hz) <= guard_hz:
            return True
    return False


# ---------------------------------------------------------------------------
# Fundamental-evidence metric
# ---------------------------------------------------------------------------

def compute_fundamental_ratio_db(
    npz,
    wi: int,
    ci: int,
    freqs_hz: np.ndarray,
    heart_band_hz: tuple[float, float] = HEART_BAND_HZ,
) -> float:
    """20*log10(candidate_peak_magnitude / median(heart_spectrum_first_pass[heart_band])).

    Uses the cardiac-band floor on the pre-second-ECA spectrum, unlike the existing
    peak_to_floor_ratio_db which uses the AHET harmonic comparison floor.
    Returns NaN if heart_spectrum_first_pass is absent or the computed floor is <= 0.
    """
    first_pass = npz.get("heart_spectrum_first_pass")
    if first_pass is None:
        return float("nan")
    peak_mag_arr = npz.get("candidate_peak_magnitude")
    if peak_mag_arr is None:
        return float("nan")
    try:
        peak_mag = float(peak_mag_arr[wi, ci])
    except (IndexError, TypeError, ValueError):
        return float("nan")
    if not math.isfinite(peak_mag) or peak_mag <= 0.0:
        return float("nan")

    lo, hi = heart_band_hz
    band_mask = (freqs_hz >= lo) & (freqs_hz <= hi)
    try:
        band_vals = first_pass[wi, band_mask]
    except (IndexError, TypeError):
        return float("nan")
    finite_vals = band_vals[np.isfinite(band_vals)]
    if len(finite_vals) == 0:
        return float("nan")

    floor = float(np.median(finite_vals))
    if not math.isfinite(floor) or floor <= 0.0:
        return float("nan")

    return 20.0 * math.log10(peak_mag / floor)


# ---------------------------------------------------------------------------
# Window-level blocking
# ---------------------------------------------------------------------------

def _blocked_reason(df_row: dict) -> str:
    """Return 'resp_invalid_or_edge_locked', 'quality_gated', or '' if unblocked."""
    inv = str(df_row.get("invalid_reason", "") or "").strip()
    if inv in ("resp_invalid", "resp_edge_locked", "resp_missing", "resp_match_too_far"):
        return "resp_invalid_or_edge_locked"
    if inv == "quality_gated":
        return "quality_gated"
    return ""


# ---------------------------------------------------------------------------
# Node score
# ---------------------------------------------------------------------------

def node_score(
    fundamental_ratio_db: float,
    peak_to_floor_ratio_db: float,
    prominence: float,
    candidate_rank: int,
    distance_to_nearest_resp_harmonic_bpm: float,
    resp_harmonic_mode: str,
    *,
    peak_to_floor_weight: float = 0.25,
    prominence_weight: float = 0.05,
    rank_weight: float = 1.5,
    harmonic_weight: float = 6.0,
    harmonic_min_distance_bpm: float = 0.5,
) -> float:
    """Candidate node score for the Viterbi tracker.

    node_score(c) =
        fundamental_ratio_db(c)
        + peak_to_floor_weight * max(peak_to_floor_ratio_db(c), -12)
        + prominence_weight    * min(candidate_prominence(c), 40)
        - rank_weight          * candidate_rank(c)
        - harmonic_weight / max(distance_to_resp_harmonic_bpm(c), harmonic_min_distance_bpm)
          [only when resp_harmonic_mode == 'score_penalty']
    """
    fdb  = peak_to_floor_ratio_db if math.isfinite(peak_to_floor_ratio_db) else -12.0
    prom = prominence if math.isfinite(prominence) else 0.0
    floor_term   = peak_to_floor_weight * max(fdb, -12.0)
    prom_term    = prominence_weight    * min(prom, 40.0)
    rank_penalty = rank_weight * float(candidate_rank)
    harm_penalty = 0.0
    if resp_harmonic_mode == "score_penalty":
        d = distance_to_nearest_resp_harmonic_bpm
        if math.isfinite(d):
            harm_penalty = harmonic_weight / max(d, harmonic_min_distance_bpm)
    return fundamental_ratio_db + floor_term + prom_term - rank_penalty - harm_penalty


# ---------------------------------------------------------------------------
# Candidate pool building  (session-level, once per session)
# ---------------------------------------------------------------------------

def build_candidate_pool(
    data: dict,
    heart_band_hz: tuple[float, float] = HEART_BAND_HZ,
    resp_harmonic_guard_hz: float = GUARD_HZ,
) -> list[list[dict]]:
    """Return per-window lists of candidate dicts with all needed fields.

    Blocked windows (resp_invalid / quality_gated) get an empty list.
    low_candidate_competitor candidates are always excluded (never in pool).
    """
    df       = data["df"]
    npz      = data["npz"]
    freqs_hz = data["freqs_hz"]
    n        = data["n_windows"]

    windows: list[list[dict]] = []

    for wi in range(n):
        row = df.iloc[wi].to_dict()
        if _blocked_reason(row):
            windows.append([])
            continue

        resp_peak_hz = float(row.get("resp_peak_hz", float("nan")) or float("nan"))
        cands: list[dict] = []

        for ci in range(_AHET_MAX_CANDIDATES):
            attempted_arr = npz.get("candidate_attempted")
            if attempted_arr is None or not bool(attempted_arr[wi, ci]):
                continue

            refined_arr = npz.get("candidate_refined_hz")
            if refined_arr is None:
                continue
            refined_hz = float(refined_arr[wi, ci])
            if not math.isfinite(refined_hz):
                continue

            lo, hi = heart_band_hz
            if not (lo <= refined_hz <= hi):
                continue

            rej_code_arr = npz.get("candidate_rejection_code")
            code = int(rej_code_arr[wi, ci]) if rej_code_arr is not None else -1
            if _rejection_code_to_str(code) == "low_candidate_competitor":
                continue

            mag_arr  = npz.get("candidate_peak_magnitude")
            mag      = float(mag_arr[wi, ci])  if mag_arr  is not None else float("nan")
            prom_arr = npz.get("candidate_prominence")
            prom     = float(prom_arr[wi, ci]) if prom_arr is not None else float("nan")
            fdb_arr  = npz.get("peak_to_floor_ratio_db")
            fdb      = float(fdb_arr[wi, ci])  if fdb_arr  is not None else float("nan")

            fund_db      = compute_fundamental_ratio_db(npz, wi, ci, freqs_hz, heart_band_hz)
            bpm          = refined_hz * 60.0
            _, _, dist   = _nearest_resp_harmonic(refined_hz, resp_peak_hz)
            within_guard = _within_guard(refined_hz, resp_peak_hz, resp_harmonic_guard_hz)

            ns_excl = node_score(fund_db, fdb, prom, ci, dist, "exclude")
            ns_pen  = node_score(fund_db, fdb, prom, ci, dist, "score_penalty")

            cands.append({
                "window_index":                      wi,
                "candidate_rank":                    ci,
                "candidate_refined_hz":              refined_hz,
                "bpm":                               bpm,
                "candidate_peak_magnitude":          mag,
                "candidate_prominence":              prom,
                "peak_to_floor_ratio_db":            fdb,
                "candidate_rejection_reason":        _rejection_code_to_str(code),
                "fundamental_ratio_db":              fund_db,
                "dist_to_nearest_resp_harmonic_bpm": dist,
                "within_resp_harmonic_guard":        within_guard,
                "node_score_exclude":                ns_excl,
                "node_score_penalty":                ns_pen,
            })

        windows.append(cands)

    return windows


def _filter_eligible(
    cands: list[dict],
    min_fundamental_ratio_db: float,
    resp_harmonic_mode: str,
) -> list[dict]:
    """Apply per-combo eligibility filter to a window's candidate list."""
    eligible = []
    for c in cands:
        fd = c["fundamental_ratio_db"]
        if not math.isfinite(fd) or fd < min_fundamental_ratio_db:
            continue
        if resp_harmonic_mode == "exclude" and c["within_resp_harmonic_guard"]:
            continue
        eligible.append(c)
    return eligible


# ---------------------------------------------------------------------------
# Viterbi DP
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class _VState:
    score: float
    cur_bpm: Optional[float]
    last_real_bpm: Optional[float]
    n_consec_gaps: int
    back: int
    cand_info: Optional[dict]


def viterbi_track(
    eligible_windows: list[list[dict]],
    max_jump_bpm_per_hop: float,
    max_gap_windows: int,
    resp_harmonic_mode: str,
    gap_cost: float = GAP_COST,
) -> list[Optional[dict]]:
    """Max-score DP track over windows.

    Jump check across multi-window gaps uses max_jump_bpm_per_hop regardless of gap
    length (not scaled by gap span). Deduplicates states by (last_real_bpm, n_consec_gaps)
    to cap state count at O(n_candidates * max_gap_windows) per window.
    """
    score_key = "node_score_penalty" if resp_harmonic_mode == "score_penalty" else "node_score_exclude"
    n = len(eligible_windows)
    if n == 0:
        return []

    all_states: list[list[_VState]] = [[] for _ in range(n)]

    def _init(wi: int) -> None:
        """Seed fresh-segment states for window wi (no jump constraint)."""
        for c in eligible_windows[wi]:
            all_states[wi].append(_VState(
                score=c[score_key], cur_bpm=c["bpm"], last_real_bpm=c["bpm"],
                n_consec_gaps=0, back=-1, cand_info=c,
            ))
        if max_gap_windows >= 1:
            all_states[wi].append(_VState(
                score=gap_cost, cur_bpm=None, last_real_bpm=None,
                n_consec_gaps=1, back=-1, cand_info=None,
            ))

    _init(0)

    for w in range(1, n):
        # Collect all candidate transitions, then deduplicate by (last_real_bpm, n_consec_gaps).
        # Without deduplication, state count grows as ~4^n_windows (4^33 ≈ 73 trillion for
        # these sessions). Dedup caps it at ~12 states per window regardless of session length.
        raw: list[_VState] = []
        for prev_idx, prev in enumerate(all_states[w - 1]):
            for c in eligible_windows[w]:
                if prev.last_real_bpm is not None:
                    if abs(c["bpm"] - prev.last_real_bpm) > max_jump_bpm_per_hop:
                        continue
                raw.append(_VState(
                    score=prev.score + c[score_key],
                    cur_bpm=c["bpm"], last_real_bpm=c["bpm"],
                    n_consec_gaps=0, back=prev_idx, cand_info=c,
                ))
            new_n = prev.n_consec_gaps + 1
            if new_n <= max_gap_windows:
                raw.append(_VState(
                    score=prev.score + gap_cost, cur_bpm=None,
                    last_real_bpm=prev.last_real_bpm, n_consec_gaps=new_n,
                    back=prev_idx, cand_info=None,
                ))

        best_map: dict[tuple, _VState] = {}
        for s in raw:
            key = (s.last_real_bpm, s.n_consec_gaps)
            if key not in best_map or s.score > best_map[key].score:
                best_map[key] = s
        all_states[w] = list(best_map.values())

        if not all_states[w]:
            _init(w)

    if not all_states[n - 1]:
        return [None] * n

    best = max(all_states[n - 1], key=lambda s: s.score)
    path: list[Optional[dict]] = [None] * n
    w = n - 1
    state = best
    while w >= 0:
        path[w] = state.cand_info
        if state.back < 0:
            break
        state = all_states[w - 1][state.back]
        w -= 1

    return path


# ---------------------------------------------------------------------------
# Stacked-intermediates adapter  (production use)
# ---------------------------------------------------------------------------

class _StackedIntermediate:
    """Wraps a list of per-window intermediate dicts as a single NPZ-like object.

    Each intermediate dict has arrays of shape (n_candidates,) or scalars.
    Stacking gives arrays of shape (n_windows, …), matching the NPZ layout
    expected by build_candidate_pool.
    """
    def __init__(self, intermediates: list[dict]) -> None:
        self._d: dict[str, np.ndarray] = {}
        if not intermediates:
            return
        for key in intermediates[0]:
            try:
                self._d[key] = np.stack([d[key] for d in intermediates], axis=0)
            except (ValueError, TypeError):
                pass

    def get(self, key, default=None):
        return self._d.get(key, default)

    def __contains__(self, key: str) -> bool:
        return key in self._d


# ---------------------------------------------------------------------------
# Production entry point
# ---------------------------------------------------------------------------

def apply_tracker(
    rows: list[dict],
    intermediates: list[dict],
    freqs_hz: np.ndarray,
    tracker_cfg: dict,
    heart_band_hz: tuple[float, float],
    resp_harmonic_guard_hz: float,
) -> tuple[dict, dict]:
    """Apply the temporal tracker to Step 6 per-window rows (modifies rows in-place).

    Caller must have already set default tracker evidence columns and pre_tracker_*
    backup columns on all rows before calling this function.

    Returns (tracker_npz_arrays, tracker_stats).

    tracker_npz_arrays: dict of (n_windows, …) arrays to merge into the session NPZ.
    tracker_stats: dict of summary fields to add to summary.json.
    """
    _nan = float("nan")
    n      = len(rows)
    n_cand = _AHET_MAX_CANDIDATES

    # -- Config --
    min_fund_db     = float(tracker_cfg.get("min_fundamental_ratio_db", 0.0))
    max_jump        = float(tracker_cfg.get("max_jump_bpm_per_hop",     6.0))
    max_gap         = int(  tracker_cfg.get("max_gap_windows",           2))
    harm_mode       = str(  tracker_cfg.get("resp_harmonic_mode",        "score_penalty"))
    gap_cost_cfg    = float(tracker_cfg.get("gap_cost",                  GAP_COST))
    peak_floor_w    = float(tracker_cfg.get("peak_to_floor_weight",      0.25))
    prom_w          = float(tracker_cfg.get("prominence_weight",         0.05))
    rank_w          = float(tracker_cfg.get("rank_weight",               1.5))
    harm_w          = float(tracker_cfg.get("harmonic_weight",           6.0))
    harm_min_dist   = float(tracker_cfg.get("harmonic_min_distance_bpm", 0.5))

    use_default_weights = (
        peak_floor_w == 0.25 and prom_w == 0.05 and
        rank_w == 1.5 and harm_w == 6.0 and harm_min_dist == 0.5
    )

    # -- Build candidate pool --
    stacked = _StackedIntermediate(intermediates)
    df      = pd.DataFrame(rows)
    data    = {"df": df, "npz": stacked, "freqs_hz": freqs_hz, "n_windows": n}
    pool    = build_candidate_pool(data, heart_band_hz, resp_harmonic_guard_hz)

    # -- NPZ arrays (computed over full pool, before eligibility filter) --
    fund_db_arr    = np.full((n, n_cand), _nan,  dtype=np.float64)
    eligible_arr   = np.zeros((n, n_cand),        dtype=bool)
    ns_arr         = np.full((n, n_cand), _nan,  dtype=np.float64)
    decision_code  = np.full(n, _CODE_NOT_RUN,   dtype=np.int32)
    sel_rank_arr   = np.full(n, -1,               dtype=np.int32)

    score_key = "node_score_penalty" if harm_mode == "score_penalty" else "node_score_exclude"

    for wi, cands in enumerate(pool):
        for c in cands:
            ci = c["candidate_rank"]
            fund_db_arr[wi, ci] = c["fundamental_ratio_db"]
            ns_arr[wi, ci]      = c[score_key]

    # -- Filter eligible (optionally recompute scores with config weights) --
    eligible: list[list[dict]] = []
    for wi, cands in enumerate(pool):
        elig = _filter_eligible(cands, min_fund_db, harm_mode)
        if not use_default_weights:
            for c in elig:
                c["node_score_exclude"] = node_score(
                    c["fundamental_ratio_db"], c["peak_to_floor_ratio_db"],
                    c["candidate_prominence"], c["candidate_rank"],
                    c["dist_to_nearest_resp_harmonic_bpm"], "exclude",
                    peak_to_floor_weight=peak_floor_w, prominence_weight=prom_w,
                    rank_weight=rank_w, harmonic_weight=harm_w,
                    harmonic_min_distance_bpm=harm_min_dist,
                )
                c["node_score_penalty"] = node_score(
                    c["fundamental_ratio_db"], c["peak_to_floor_ratio_db"],
                    c["candidate_prominence"], c["candidate_rank"],
                    c["dist_to_nearest_resp_harmonic_bpm"], "score_penalty",
                    peak_to_floor_weight=peak_floor_w, prominence_weight=prom_w,
                    rank_weight=rank_w, harmonic_weight=harm_w,
                    harmonic_min_distance_bpm=harm_min_dist,
                )
                ci = c["candidate_rank"]
                ns_arr[wi, ci] = c[score_key]
        for c in elig:
            eligible_arr[wi, c["candidate_rank"]] = True
        eligible.append(elig)

    # -- Viterbi --
    path = viterbi_track(eligible, max_jump, max_gap, harm_mode, gap_cost_cfg)

    # -- Apply decisions to rows --
    n_selected = n_gap_dp = n_forced_blk = n_rescued_ahet = 0

    for wi, (row, decision) in enumerate(zip(rows, path)):
        blocked = _blocked_reason(row)

        if blocked:
            n_forced_blk += 1
            decision_code[wi] = _CODE_FORCED_BLOCKED
            row["tracker_decision_type"] = "forced_blocked"
            # primary fields and hr_source unchanged

        elif decision is not None:
            n_selected += 1
            decision_code[wi] = _CODE_CANDIDATE
            sel_rank_arr[wi]  = decision["candidate_rank"]

            pre_reason = str(row.get("pre_tracker_invalid_reason", "") or "").strip()
            if pre_reason == "ahet_failed":
                n_rescued_ahet += 1

            # Recompute error against Masimo
            masimo_pr  = _nan
            raw_pr = row.get("masimo_pr_bpm")
            if raw_pr is not None:
                try:
                    masimo_pr = float(raw_pr)
                except (ValueError, TypeError):
                    pass
            masimo_bad = bool(row.get("masimo_low_quality", True))
            radar_hr   = decision["bpm"]

            if math.isfinite(radar_hr) and math.isfinite(masimo_pr) and not masimo_bad:
                err_bpm = radar_hr - masimo_pr
                abs_err = abs(err_bpm)
            else:
                err_bpm = _nan
                abs_err = _nan

            # Update primary
            row["radar_hr_bpm"]   = radar_hr
            row["heart_peak_hz"]  = decision["candidate_refined_hz"]
            row["hr_valid"]       = True
            row["hr_confidence"]  = "high"
            row["invalid_reason"] = ""
            row["hr_source"]      = "temporal_tracker"
            row["hr_error_bpm"]     = err_bpm
            row["hr_abs_error_bpm"] = abs_err

            # Tracker evidence
            row["tracker_decision_type"]                 = "candidate"
            row["tracker_selected_candidate_rank"]       = decision["candidate_rank"]
            row["tracker_selected_candidate_refined_hz"] = decision["candidate_refined_hz"]
            row["tracker_node_score"]                    = decision[score_key]
            row["tracker_fundamental_ratio_db"]          = decision["fundamental_ratio_db"]
            row["tracker_peak_to_floor_ratio_db"]        = decision["peak_to_floor_ratio_db"]
            row["tracker_candidate_prominence"]          = decision["candidate_prominence"]
            row["tracker_distance_to_resp_harmonic_bpm"] = decision["dist_to_nearest_resp_harmonic_bpm"]
            row["tracker_within_resp_harmonic_guard"]    = decision["within_resp_harmonic_guard"]

        else:
            # DP-chosen gap (not forced-blocked)
            n_gap_dp += 1
            decision_code[wi] = _CODE_GAP
            row["tracker_decision_type"] = "gap"
            row["hr_source"] = ""
            row["radar_hr_bpm"] = _nan
            row["heart_peak_hz"] = _nan
            row["hr_valid"] = False
            row["hr_confidence"] = "none"
            row["hr_error_bpm"] = _nan
            row["hr_abs_error_bpm"] = _nan
            if not row.get("invalid_reason", ""):
                row["invalid_reason"] = "temporal_tracker_gap"

    tracker_npz = {
        "candidate_fundamental_ratio_db": fund_db_arr,
        "candidate_tracker_eligible":      eligible_arr,
        "candidate_tracker_node_score":    ns_arr,
        "tracker_decision_code":           decision_code,
        "tracker_selected_candidate_rank": sel_rank_arr,
    }

    tracker_stats = {
        "temporal_tracker_enabled":              True,
        "temporal_tracker_config":               dict(tracker_cfg),
        "n_tracker_selected_windows":            n_selected,
        "n_tracker_gap":                         n_gap_dp,
        "n_tracker_forced_blocked":              n_forced_blk,
        "n_tracker_selected_from_ahet_failed":   n_rescued_ahet,
    }

    return tracker_npz, tracker_stats
