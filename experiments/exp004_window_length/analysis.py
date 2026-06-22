"""Pure analysis functions for the exp004 window-length study.

All functions are side-effect free except collect_provenance(), which shells out
to git and may print a dirty-tree warning to stdout.
"""
from __future__ import annotations

import copy
import hashlib
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import scipy


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _arr_stats(vec: list[float]) -> dict:
    """MAE, RMSE, bias from a list of signed errors."""
    a = np.array(vec, dtype=float)
    return {
        "mae": float(np.mean(np.abs(a))),
        "rmse": float(np.sqrt(np.mean(a ** 2))),
        "bias": float(np.mean(a)),
    }


def _trend(mae_20: float, mae_25: float, mae_30: float) -> str:
    """Classify the MAE trajectory across window lengths.

    Three adjacent-pair comparisons: 20→25 and 25→30.
    'monotonic_improvement': both pairs show non-strict improvement (>=).
    'partial_improvement':   exactly one pair shows non-strict improvement.
    'no_improvement':        neither pair shows non-strict improvement.
    """
    imp_ab = mae_20 >= mae_25
    imp_bc = mae_25 >= mae_30
    if imp_ab and imp_bc:
        return "monotonic_improvement"
    if imp_ab or imp_bc:
        return "partial_improvement"
    return "no_improvement"


# ---------------------------------------------------------------------------
# Function 1
# ---------------------------------------------------------------------------

def pooled_window_length_summary(cap_results: dict[str, dict]) -> dict:
    """Pool per-capture intersection error vectors across window lengths.

    Parameters
    ----------
    cap_results:
        Mapping of capture ID to a dict with at minimum:
          "paired"        → paired_metrics() output (must include error_vector)
          "per_condition" → per-condition coverage dicts (not used here)

    Returns
    -------
    dict with keys "per_window_length" and "trend".

    Raises
    ------
    ValueError
        If cap_results is empty, any required window length is absent, or the
        concatenated error vector for any window length is empty after skipping
        captures with no intersection windows.
    """
    if not cap_results:
        raise ValueError("cap_results must be non-empty")

    _WLS = ["20s", "25s", "30s"]

    for cap_id, cap in cap_results.items():
        try:
            pc = cap["paired"]["intersection"]["per_condition"]
        except KeyError as exc:
            raise ValueError(
                f"Capture {cap_id!r} missing paired.intersection.per_condition"
            ) from exc
        for wl in _WLS:
            if wl not in pc:
                raise ValueError(
                    f"Capture {cap_id!r} missing window length {wl!r} in "
                    "paired.intersection.per_condition"
                )

    per_window_length: dict[str, dict] = {}

    for wl in _WLS:
        all_errors: list[float] = []
        per_capture: dict[str, dict] = {}
        cap_maes: list[float] = []
        cap_rmses: list[float] = []
        cap_biases: list[float] = []

        for cap_id, cap in cap_results.items():
            ev = cap["paired"]["intersection"]["per_condition"][wl]["error_vector"]
            if not ev:
                per_capture[cap_id] = {"mae": None, "rmse": None, "bias": None, "n": 0}
                continue
            stats = _arr_stats(ev)
            per_capture[cap_id] = stats
            all_errors.extend(ev)
            cap_maes.append(stats["mae"])
            cap_rmses.append(stats["rmse"])
            cap_biases.append(stats["bias"])

        if not all_errors:
            raise ValueError(
                f"All error vectors empty for window length {wl!r}; "
                "cannot compute micro-average"
            )

        micro = {**_arr_stats(all_errors), "n": len(all_errors)}

        if cap_maes:
            macro = {
                "mae_mean": float(np.mean(cap_maes)),
                "mae_min": float(np.min(cap_maes)),
                "mae_max": float(np.max(cap_maes)),
                "rmse_mean": float(np.mean(cap_rmses)),
                "bias_mean": float(np.mean(cap_biases)),
            }
        else:
            _nan = float("nan")
            macro = {
                "mae_mean": _nan, "mae_min": _nan, "mae_max": _nan,
                "rmse_mean": _nan, "bias_mean": _nan,
            }

        per_window_length[wl] = {
            "micro": micro,
            "macro": macro,
            "per_capture": per_capture,
        }

    # Micro-average trend
    micro_trend = _trend(
        per_window_length["20s"]["micro"]["mae"],
        per_window_length["25s"]["micro"]["mae"],
        per_window_length["30s"]["micro"]["mae"],
    )

    # Per-capture trend
    per_cap_trend: dict[str, str] = {}
    for cap_id in cap_results:
        vals = {wl: per_window_length[wl]["per_capture"][cap_id]["mae"] for wl in _WLS}
        if any(v is None for v in vals.values()):
            per_cap_trend[cap_id] = "unavailable"
        else:
            per_cap_trend[cap_id] = _trend(vals["20s"], vals["25s"], vals["30s"])

    return {
        "per_window_length": per_window_length,
        "trend": {
            "micro": micro_trend,
            "per_capture": per_cap_trend,
        },
    }


# ---------------------------------------------------------------------------
# Function 2
# ---------------------------------------------------------------------------

def chair_condition_summary(cap2_results: dict, cap3_results: dict) -> dict:
    """Descriptive comparison of cap2 (no chair) vs cap3 (chair) results.

    Confounds are always listed; no causal claims are supported.

    Parameters
    ----------
    cap2_results, cap3_results:
        _run_capture() return dicts containing at minimum a "per_condition"
        key with "20s"/"25s"/"30s" sub-dicts.

    Returns
    -------
    dict with keys "per_window_length" and "confounds".
    """
    _WLS = ["20s", "25s", "30s"]
    _CONFOUNDS = [
        "locked_bin_range_m differs: cap2=1.439m vs cap3=1.308m",
        "recording order: cap2 first, cap3 second in same session",
        "HR and respiration trajectories differ between recordings",
        "no causal inference about chair condition is supported",
    ]

    def _extract(pc: dict) -> dict:
        return {
            "n_total": int(pc.get("n_total", 0)),
            "n_finite": int(pc.get("n_finite", 0)),
            "n_nan_radar": int(pc.get("n_nan_radar", 0)),
            "n_nan_ref": int(pc.get("n_nan_ref", 0)),
            "n_ahet": int(pc.get("n_ahet", 0)),
            "n_f_r_outlier": int(pc.get("n_f_r_outlier", 0)),
            "n_harmonic_suspect": int(pc.get("n_harmonic_suspect", 0)),
            "mae": float(pc["mae"]),
            "rmse": float(pc["rmse"]),
            "bias": float(pc["bias"]),
        }

    per_window_length: dict[str, dict] = {}
    for wl in _WLS:
        c2 = _extract(cap2_results["per_condition"][wl])
        c3 = _extract(cap3_results["per_condition"][wl])
        per_window_length[wl] = {
            "cap2": c2,
            "cap3": c3,
            "difference_cap2_minus_cap3": {
                "mae": c2["mae"] - c3["mae"],
                "rmse": c2["rmse"] - c3["rmse"],
            },
        }

    return {
        "per_window_length": per_window_length,
        "confounds": _CONFOUNDS,
    }


# ---------------------------------------------------------------------------
# Function 3
# ---------------------------------------------------------------------------

def masimo_summary(
    masimo_df: pd.DataFrame,
    trim_start_epoch: int,
    trim_end_epoch: int,
    min_pi: float,
) -> dict:
    """Descriptive statistics of the Masimo signal over the usable recording window.

    Parameters
    ----------
    masimo_df:
        DataFrame with columns: epoch (int), PR (float), BR (float), PI (float).
    trim_start_epoch:
        First usable epoch (inclusive).
    trim_end_epoch:
        Last usable epoch (exclusive).
    min_pi:
        Minimum PI threshold; samples below this are excluded from PR/BR/PI stats.

    Returns
    -------
    dict of statistics. All metric fields are NaN if no samples survive filtering.

    Raises
    ------
    ValueError
        If required columns are missing or if trim_start_epoch >= trim_end_epoch.
    """
    required_cols = {"epoch", "PR", "BR", "PI"}
    missing_cols = required_cols - set(masimo_df.columns)
    if missing_cols:
        raise ValueError(f"masimo_df missing required columns: {sorted(missing_cols)}")
    if trim_start_epoch >= trim_end_epoch:
        raise ValueError(
            f"trim_start_epoch ({trim_start_epoch}) must be less than "
            f"trim_end_epoch ({trim_end_epoch})"
        )

    # Deduplicate by epoch (keep first occurrence) then filter to interval
    df = masimo_df.drop_duplicates(subset="epoch", keep="first")
    mask = (df["epoch"] >= trim_start_epoch) & (df["epoch"] < trim_end_epoch)
    df_interval = df[mask].copy()

    n_unique_samples = len(df_interval)
    interval_duration_s = trim_end_epoch - trim_start_epoch

    fraction_above_min_pi = (
        float((df_interval["PI"] >= min_pi).sum() / n_unique_samples)
        if n_unique_samples > 0
        else float("nan")
    )

    df_valid = df_interval[df_interval["PI"] >= min_pi]
    n_pi_filtered = n_unique_samples - len(df_valid)

    _nan = float("nan")

    if len(df_valid) == 0:
        return {
            "pr": {"mean": _nan, "std": _nan, "min": _nan, "max": _nan},
            "br": {"mean": _nan, "std": _nan, "min": _nan, "max": _nan},
            "pi": {"mean": _nan, "std": _nan, "min": _nan, "max": _nan,
                   "fraction_above_min_pi": fraction_above_min_pi},
            "n_unique_samples": n_unique_samples,
            "n_pi_filtered": n_pi_filtered,
            "temporal_coverage_s": n_unique_samples,
            "temporal_coverage_fraction": (
                n_unique_samples / interval_duration_s if interval_duration_s > 0 else _nan
            ),
        }

    def _col_stats(col: str) -> dict:
        a = df_valid[col].to_numpy(dtype=float)
        return {
            "mean": float(np.mean(a)),
            "std": float(np.std(a)),
            "min": float(np.min(a)),
            "max": float(np.max(a)),
        }

    pi_stats = _col_stats("PI")
    pi_stats["fraction_above_min_pi"] = fraction_above_min_pi

    return {
        "pr": _col_stats("PR"),
        "br": _col_stats("BR"),
        "pi": pi_stats,
        "n_unique_samples": n_unique_samples,
        "n_pi_filtered": n_pi_filtered,
        "temporal_coverage_s": n_unique_samples,
        "temporal_coverage_fraction": n_unique_samples / interval_duration_s,
    }


# ---------------------------------------------------------------------------
# Function 4
# ---------------------------------------------------------------------------

def collect_provenance(cfg: dict, results_root: Path, source_files: list) -> dict:
    """Collect reproducibility metadata for a run.

    Git failures are caught silently; missing source files are recorded as
    "missing" rather than raising.  If the working tree is dirty, a warning
    is printed to stdout.

    Parameters
    ----------
    cfg:
        Full parsed config dict (deep-copied into provenance).
    results_root:
        Timestamped results directory (stored for reference).
    source_files:
        Paths to hash with SHA-256.

    Returns
    -------
    dict of provenance fields.
    """
    try:
        git_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        status_out = subprocess.check_output(
            ["git", "status", "--porcelain"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        git_dirty = bool(status_out.strip())
        git_diff = subprocess.check_output(
            ["git", "diff", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        git_untracked = [
            line[3:].strip()
            for line in status_out.splitlines()
            if line.startswith("??")
        ]
    except Exception:
        git_commit = "unavailable"
        git_dirty = False
        git_diff = ""
        git_untracked = []

    file_hashes: dict[str, str] = {}
    for p in source_files:
        p = Path(p)
        if not p.is_file():
            file_hashes[str(p)] = "missing"
        else:
            file_hashes[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()

    if git_dirty:
        print(
            "WARNING: git working tree is dirty. Full diff saved to provenance.json.\n"
            "Results may not be exactly reproducible from the committed state."
        )

    return {
        "git_commit": git_commit,
        "git_dirty": git_dirty,
        "git_diff": git_diff,
        "git_untracked": git_untracked,
        "file_hashes": file_hashes,
        "python_version": sys.version,
        "numpy_version": np.__version__,
        "scipy_version": scipy.__version__,
        "timestamp_iso": datetime.now(timezone.utc).isoformat(),
        "config": copy.deepcopy(cfg),
    }
