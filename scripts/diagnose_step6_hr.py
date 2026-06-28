"""Step 6 HR diagnostic script.

Reads existing Step 6 outputs (heart_windows.csv + heart_intermediates.npz +
summary.json) for one or more sessions, joins them, and writes diagnostic
files to results/diagnose/step6_hr/.

This script is READ-ONLY with respect to Step 5, Step 6, data/raw, and
data/processed.  It only writes to the --out directory.

Usage
-----
python -X utf8 scripts/diagnose_step6_hr.py \\
    --sessions test test2 test3 test4 test5 \\
    --out results/diagnose \\
    --overwrite

Output layout
-------------
<out>/step6_hr/
    diagnostic.log
    summary.json
    summary.md
    per_window_diagnostics.csv
    candidate_diagnostics.csv
    top_peaks.csv
    bad_valid_windows.csv
    good_valid_windows.csv
    threshold_sweep.csv
    threshold_sweep_window_decisions.csv
    plots/*.png
"""
from __future__ import annotations

import argparse
import itertools
import json
import logging
import math
import subprocess
import sys
import textwrap
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

GOOD_THRESH_BPM   = 5.0
BAD_THRESH_BPM    = 10.0
SEVERE_THRESH_BPM = 15.0
N_TOP_PEAKS       = 5
K_MAX_HARMONIC    = 10   # search k=1..10 for nearest resp harmonic
LOW_CANDIDATE_HZ  = 1.20  # diagnostic label threshold matching Step 6 config

# Mirrors _REJECTION_CODE_STR from steps/step_6/extract_heart_rate.py.
# Kept here so the diagnostic script is self-contained.
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

log = logging.getLogger("diagnose_step6")


def _rejection_code_to_str(code) -> str:
    try:
        code = int(code)
    except (TypeError, ValueError):
        return f"unknown_code_{code}"
    return _REJECTION_CODE_STR.get(code, f"unknown_code_{code}")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _load_session(session_id: str, results_root: Path) -> dict | None:
    """Load heart_windows.csv, heart_intermediates.npz, summary.json for one session.

    Returns None and logs a warning if any required file is missing.
    """
    step6_dir = results_root / session_id / "step_6"
    csv_path  = step6_dir / "heart_windows.csv"
    npz_path  = step6_dir / "heart_intermediates.npz"
    jsn_path  = step6_dir / "summary.json"

    missing = [p for p in (csv_path, npz_path, jsn_path) if not p.exists()]
    if missing:
        log.warning("Session %s: missing files %s — skipping",
                    session_id, [str(p) for p in missing])
        return None

    df   = pd.read_csv(csv_path, keep_default_na=False, na_values=[""])
    npz  = np.load(npz_path, allow_pickle=False)
    jsn  = json.loads(jsn_path.read_text(encoding="utf-8"))

    n_csv = len(df)
    n_npz = npz["heart_freqs_hz"].shape[0] if "heart_freqs_hz" in npz.files else -1
    # First dim of any stacked array (e.g. phase_unwrapped) = n_windows
    n_npz_windows = npz["phase_unwrapped"].shape[0] if "phase_unwrapped" in npz.files else -1

    if n_npz_windows != n_csv:
        log.error(
            "Session %s: CSV has %d windows but NPZ first dimension is %d — skipping",
            session_id, n_csv, n_npz_windows,
        )
        return None

    freqs_hz = npz["heart_freqs_hz"]   # (n_fft,)
    return {
        "session_id": session_id,
        "df":         df,
        "npz":        npz,
        "summary":    jsn,
        "freqs_hz":   freqs_hz,
        "n_windows":  n_csv,
        "step6_dir":  step6_dir,
    }


# ---------------------------------------------------------------------------
# Per-window diagnostics
# ---------------------------------------------------------------------------

def _nearest_resp_harmonic(hz_query: float, resp_peak_hz: float) -> tuple[int, float, float]:
    """Return (best_k, harmonic_hz, distance_bpm) for k in 1..K_MAX_HARMONIC."""
    if not (math.isfinite(hz_query) and math.isfinite(resp_peak_hz) and resp_peak_hz > 0):
        return (-1, float("nan"), float("nan"))
    best_k, best_dist = -1, float("inf")
    for k in range(1, K_MAX_HARMONIC + 1):
        dist = abs(hz_query - k * resp_peak_hz)
        if dist < best_dist:
            best_k, best_dist = k, dist
    harmonic_hz = best_k * resp_peak_hz
    return (best_k, harmonic_hz, best_dist * 60.0)


def _within_guard(hz_query: float, resp_peak_hz: float, guard_hz: float) -> bool:
    if not (math.isfinite(hz_query) and math.isfinite(resp_peak_hz) and resp_peak_hz > 0):
        return False
    for k in range(1, K_MAX_HARMONIC + 1):
        if abs(hz_query - k * resp_peak_hz) <= guard_hz:
            return True
    return False


def compute_window_diagnostics(
    session_id: str,
    df: pd.DataFrame,
    npz: np.ndarray,
    resp_harmonic_guard_hz: float = 0.05,
) -> pd.DataFrame:
    """Enrich the CSV DataFrame with NPZ-joined fields and derived diagnostics."""
    rows = []
    n = len(df)

    for wi in range(n):
        row = df.iloc[wi].to_dict()
        row["session_id"] = session_id

        resp_peak_hz   = float(row.get("resp_peak_hz", float("nan")) or float("nan"))
        radar_hr_hz    = float(row.get("heart_peak_hz", float("nan")) or float("nan"))
        masimo_pr_bpm  = float(row.get("masimo_pr_bpm", float("nan")) or float("nan"))
        masimo_pr_hz   = masimo_pr_bpm / 60.0 if math.isfinite(masimo_pr_bpm) else float("nan")
        accepted_rank  = _parse_rank(row.get("accepted_candidate_rank"))

        # Nearest resp harmonic to accepted radar HR
        nk_r, nhz_r, nd_r = _nearest_resp_harmonic(radar_hr_hz, resp_peak_hz)
        row["nearest_k_to_radar_hr"]           = nk_r
        row["nearest_harmonic_hz_to_radar_hr"] = nhz_r
        row["distance_to_nearest_k_bpm"]       = nd_r

        # Nearest resp harmonic to Masimo PR
        nk_m, nhz_m, nd_m = _nearest_resp_harmonic(masimo_pr_hz, resp_peak_hz)
        row["nearest_k_to_masimo_pr"]             = nk_m
        row["nearest_harmonic_hz_to_masimo_pr"]   = nhz_m
        row["distance_masimo_to_nearest_k_bpm"]   = nd_m

        # Guard flag
        row["accepted_hr_within_resp_harmonic_guard"] = _within_guard(
            radar_hr_hz, resp_peak_hz, resp_harmonic_guard_hz
        )

        # Window classification
        hr_valid  = str(row.get("hr_valid", "")).lower() in ("true", "1")
        abs_err   = float(row.get("hr_abs_error_bpm", float("nan")) or float("nan"))
        inv_rsn   = str(row.get("invalid_reason", "")).strip()
        row["window_class"] = _classify_window(hr_valid, abs_err, inv_rsn)

        rows.append(row)

    return pd.DataFrame(rows)


def _classify_window(hr_valid: bool, abs_err: float, invalid_reason: str) -> str:
    if hr_valid and math.isfinite(abs_err):
        if abs_err <= GOOD_THRESH_BPM:
            return "good_valid"
        if abs_err > SEVERE_THRESH_BPM:
            return "severe_bad_valid"
        if abs_err > BAD_THRESH_BPM:
            return "bad_valid"
        return "acceptable_valid"
    if invalid_reason == "ahet_failed":
        return "ahet_failed"
    if invalid_reason == "resp_harmonic_coincident":
        return "resp_harmonic_rejected"
    if invalid_reason in ("resp_invalid", "resp_edge_locked",
                          "resp_missing", "resp_match_too_far"):
        return "resp_invalid_or_edge_locked"
    if invalid_reason == "quality_gated":
        return "quality_gated"
    return "other_invalid"


# ---------------------------------------------------------------------------
# Candidate diagnostics
# ---------------------------------------------------------------------------

def compute_candidate_diagnostics(
    session_id: str,
    df: pd.DataFrame,
    npz,
    freqs_hz: np.ndarray,
    resp_harmonic_guard_hz: float = 0.05,
) -> pd.DataFrame:
    """One row per (window, candidate_rank) — 3 rows per window."""
    n = len(df)
    rows = []
    rej_code_arr = npz.get("candidate_rejection_code")  # (n_windows, 3) int32 or None

    for wi in range(n):
        csv_row = df.iloc[wi]
        accepted_rank = _parse_rank(csv_row.get("accepted_candidate_rank"))
        start_epoch   = float(csv_row.get("start_epoch", float("nan")) or float("nan"))
        resp_peak_hz  = float(csv_row.get("resp_peak_hz", float("nan")) or float("nan"))

        for ci in range(3):
            r: dict = {
                "session_id":        session_id,
                "window_index":      wi,
                "start_epoch":       start_epoch,
                "candidate_rank":    ci,
                "is_accepted":       ci == accepted_rank,
            }
            for field in (
                "candidate_attempted", "candidate_initial_hz", "candidate_refined_hz",
                "candidate_peak_magnitude", "candidate_prominence", "candidate_passed",
                "second_peak_refined_hz", "second_peak_magnitude",
                "comparison_floor", "peak_to_floor_ratio", "peak_to_floor_ratio_db",
                "region_available",
            ):
                arr = npz.get(field)
                r[field] = float(arr[wi, ci]) if arr is not None else float("nan")

            # Peak frequency from ahet_attempt_spectrum for this candidate
            spec = npz.get("ahet_attempt_spectrum")
            if spec is not None and len(freqs_hz) == spec.shape[2]:
                s = spec[wi, ci, :]
                finite = np.isfinite(s)
                if finite.any():
                    peak_idx = int(np.nanargmax(np.where(finite, s, np.nan)))
                    r["ahet_spec_peak_hz"]  = float(freqs_hz[peak_idx])
                    r["ahet_spec_peak_mag"] = float(s[peak_idx])
                else:
                    r["ahet_spec_peak_hz"]  = float("nan")
                    r["ahet_spec_peak_mag"] = float("nan")
            else:
                r["ahet_spec_peak_hz"]  = float("nan")
                r["ahet_spec_peak_mag"] = float("nan")

            # --- Step 6.1 fields ---

            # Rejection code: read from NPZ (int32); fall back to -1 for old NPZs
            if rej_code_arr is not None:
                try:
                    code = int(rej_code_arr[wi, ci])
                except (IndexError, TypeError, ValueError):
                    code = -1
            else:
                code = -1
            r["candidate_rejection_code"]   = code
            r["candidate_rejection_reason"] = _rejection_code_to_str(code)

            # Is this a low-HR candidate?
            cand_hz = float(r.get("candidate_refined_hz", float("nan")))
            r["candidate_is_low"] = math.isfinite(cand_hz) and cand_hz < LOW_CANDIDATE_HZ

            # Respiratory harmonic proximity for this candidate
            r["candidate_within_resp_harmonic_guard"] = _within_guard(
                cand_hz, resp_peak_hz, resp_harmonic_guard_hz
            )
            nk, _nhz, nd_bpm = _nearest_resp_harmonic(cand_hz, resp_peak_hz)
            r["candidate_nearest_resp_harmonic_k"]              = nk
            r["candidate_distance_to_nearest_resp_harmonic_bpm"] = nd_bpm

            # Strict-gate selection: passed the AHET gate and code==0
            cp = r.get("candidate_passed", 0.0)
            try:
                cand_passed = math.isfinite(float(cp)) and float(cp) != 0.0
            except (TypeError, ValueError):
                cand_passed = False
            r["candidate_selected_by_strict_gate"] = cand_passed and code == 0

            rows.append(r)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Top peaks per window
# ---------------------------------------------------------------------------

def compute_top_peaks(
    session_id: str,
    df: pd.DataFrame,
    npz,
    freqs_hz: np.ndarray,
    heart_band_hz: tuple[float, float] = (0.8, 2.0),
    n_top: int = N_TOP_PEAKS,
) -> pd.DataFrame:
    """Top-N spectral peaks inside heart_band_hz for each spectrum type per window."""
    lo, hi     = heart_band_hz
    band_mask  = (freqs_hz >= lo) & (freqs_hz <= hi)
    rows       = []

    spec_keys = {
        "baseline_spectrum":         "baseline",
        "heart_spectrum_first_pass": "after_eca",
        "heart_spectrum":            "after_ahet",
    }

    for wi in range(len(df)):
        csv_row     = df.iloc[wi]
        start_epoch = float(csv_row.get("start_epoch", float("nan")) or float("nan"))

        for npz_key, stage_label in spec_keys.items():
            spec = npz.get(npz_key)
            if spec is None:
                continue
            s = spec[wi, :]
            in_band = np.where(band_mask & np.isfinite(s), s, -np.inf)
            if not np.any(np.isfinite(in_band)):
                continue
            top_idx = np.argsort(in_band)[::-1][:n_top]
            for rank, idx in enumerate(top_idx):
                if not np.isfinite(in_band[idx]):
                    break
                rows.append({
                    "session_id":    session_id,
                    "window_index":  wi,
                    "start_epoch":   start_epoch,
                    "spectrum_stage": stage_label,
                    "peak_rank":     rank,
                    "peak_hz":       float(freqs_hz[idx]),
                    "peak_bpm":      float(freqs_hz[idx] * 60.0),
                    "peak_magnitude": float(in_band[idx]),
                })

    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["session_id", "window_index", "start_epoch",
                 "spectrum_stage", "peak_rank", "peak_hz", "peak_bpm", "peak_magnitude"]
    )


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

def _parse_rank(val) -> int:
    """Parse accepted_candidate_rank safely: returns -1 for missing/NaN, integer otherwise.
    The `or -1` idiom converts rank 0 to -1 because 0 is falsy — never use it here."""
    if val is None:
        return -1
    try:
        f = float(val)
    except (TypeError, ValueError):
        return -1
    if not math.isfinite(f):
        return -1
    return int(f)


def _epoch_to_elapsed_min(epoch_series: pd.Series) -> np.ndarray:
    """Convert Unix epoch floats to elapsed minutes from session start (plain float array)."""
    s = pd.to_numeric(epoch_series, errors="coerce").to_numpy(dtype=float)
    t0 = np.nanmin(s)
    return (s - t0) / 60.0


def _plot_hr_vs_masimo(diag_df: pd.DataFrame, out_path: Path, session_id: str) -> None:
    t      = _epoch_to_elapsed_min(diag_df["start_epoch"])
    valid  = diag_df["hr_valid"].astype(str).str.lower().isin(("true", "1"))
    bad_v  = diag_df["window_class"].isin(("bad_valid", "severe_bad_valid"))
    good_v = diag_df["window_class"] == "good_valid"

    fig, ax = plt.subplots(figsize=(12, 4))
    ax.plot(t, pd.to_numeric(diag_df["masimo_pr_bpm"], errors="coerce"),
            "o-", color="black", label="Masimo PR", markersize=3, zorder=3)
    if good_v.any():
        ax.plot(t[good_v.to_numpy()], pd.to_numeric(diag_df.loc[good_v, "radar_hr_bpm"], errors="coerce"),
                "s", color="tab:blue", label="radar HR (good)", markersize=5, zorder=4)
    if (valid & ~bad_v & ~good_v).any():
        m = (valid & ~bad_v & ~good_v).to_numpy()
        ax.plot(t[m], pd.to_numeric(diag_df.loc[valid & ~bad_v & ~good_v, "radar_hr_bpm"], errors="coerce"),
                "s", color="tab:cyan", label="radar HR (acceptable)", markersize=4, zorder=4)
    if bad_v.any():
        ax.plot(t[bad_v.to_numpy()], pd.to_numeric(diag_df.loc[bad_v, "radar_hr_bpm"], errors="coerce"),
                "X", color="tab:red", label="radar HR (bad/severe)", markersize=7, zorder=5)
    ax.set_xlabel("elapsed time (min)")
    ax.set_ylabel("HR (bpm)")
    ax.set_title(f"{session_id} - Radar HR vs Masimo PR (bad windows highlighted)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.subplots_adjust(left=0.08, right=0.98, top=0.92, bottom=0.12)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def _plot_hr_vs_resp_harmonics(diag_df: pd.DataFrame, out_path: Path, session_id: str) -> None:
    t = _epoch_to_elapsed_min(diag_df["start_epoch"])
    fig, ax = plt.subplots(figsize=(12, 5))

    rr_hz = pd.to_numeric(diag_df["resp_peak_hz"], errors="coerce").to_numpy(dtype=float)
    radar_bpm = pd.to_numeric(diag_df["radar_hr_bpm"], errors="coerce")
    masimo_bpm = pd.to_numeric(diag_df["masimo_pr_bpm"], errors="coerce")

    ax.plot(t, masimo_bpm, "o-", color="black",   label="Masimo PR",  markersize=3, zorder=3)
    ax.plot(t, radar_bpm,  "s-", color="tab:blue", label="Radar HR",   markersize=3, zorder=4)

    # Overlay respiratory harmonics k=1..6
    colors = plt.cm.Reds(np.linspace(0.3, 0.9, 6))
    for k in range(1, 7):
        harmonic_bpm = rr_hz * k * 60.0
        ax.plot(t, harmonic_bpm, "--", color=colors[k - 1],
                label=f"k={k}xRR", linewidth=0.8, alpha=0.7)

    ax.set_xlabel("elapsed time (min)")
    ax.set_ylabel("rate (bpm)")
    ax.set_title(f"{session_id} - Accepted HR vs respiratory harmonics")
    ax.legend(fontsize=7, ncol=3)
    ax.grid(True, alpha=0.3)
    fig.subplots_adjust(left=0.08, right=0.98, top=0.92, bottom=0.12)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def _plot_candidate_scatter(
    diag_df: pd.DataFrame,
    cand_df: pd.DataFrame,
    freqs_hz: np.ndarray,
    out_path: Path,
    session_id: str,
) -> None:
    fig, ax = plt.subplots(figsize=(12, 5))

    masimo_bpm = pd.to_numeric(diag_df["masimo_pr_bpm"], errors="coerce")
    ax.plot(diag_df.index, masimo_bpm, "o-", color="black",
            label="Masimo PR", markersize=3, zorder=5)

    colors_c = ["tab:orange", "tab:green", "tab:purple"]
    for ci in range(3):
        sub = cand_df[cand_df["candidate_rank"] == ci]
        att = sub["candidate_attempted"].astype(str).str.lower().isin(("true", "1.0", "1"))
        if att.any():
            bpm = pd.to_numeric(sub.loc[att, "candidate_refined_hz"], errors="coerce") * 60.0
            ax.scatter(sub.loc[att].index, bpm, marker="^", s=20,
                       color=colors_c[ci], alpha=0.6,
                       label=f"candidate {ci} (attempted)")

    # Mark accepted candidates in red
    accepted = cand_df["is_accepted"].astype(str).str.lower().isin(("true", "1"))
    if accepted.any():
        bpm_acc = pd.to_numeric(cand_df.loc[accepted, "candidate_refined_hz"], errors="coerce") * 60.0
        ax.scatter(cand_df.loc[accepted].index, bpm_acc, marker="*", s=80,
                   color="tab:red", label="accepted candidate", zorder=6)

    ax.set_xlabel("window index")
    ax.set_ylabel("HR (bpm)")
    ax.set_title(f"{session_id} - AHET candidate scatter")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.subplots_adjust(left=0.08, right=0.98, top=0.92, bottom=0.12)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def _plot_spectrum_overlay(
    wi: int,
    df_row: pd.Series,
    npz,
    freqs_hz: np.ndarray,
    out_path: Path,
    session_id: str,
    heart_band_hz: tuple[float, float] = (0.8, 2.0),
) -> None:
    """Spectrum overlay for a single window showing all stages + accepted candidate."""
    fig, axes = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
    spec_pairs = [
        ("baseline_spectrum",         "Baseline (no ECA)", "tab:orange",  axes[0]),
        ("heart_spectrum_first_pass", "After ECA",         "tab:blue",    axes[1]),
        ("heart_spectrum",            "After AHET",        "tab:green",   axes[2]),
    ]
    for npz_key, label, color, ax in spec_pairs:
        s = npz.get(npz_key)
        if s is not None:
            ax.semilogy(freqs_hz * 60.0, np.abs(s[wi]), color=color,
                        linewidth=0.9, label=label)
        ax.set_ylabel("magnitude")
        ax.set_xlim(heart_band_hz[0] * 60.0 - 5, heart_band_hz[1] * 60.0 + 5)
        ax.legend(fontsize=7)
        ax.grid(True, alpha=0.3)
        ax.axvspan(heart_band_hz[0] * 60.0, heart_band_hz[1] * 60.0,
                   alpha=0.08, color="gray")

    # Accepted candidate spectrum (from ahet_attempt_spectrum)
    accepted_rank = _parse_rank(df_row.get("accepted_candidate_rank"))
    if accepted_rank >= 0:
        ahet_spec = npz.get("ahet_attempt_spectrum")
        if ahet_spec is not None:
            s_acc = ahet_spec[wi, accepted_rank, :]
            axes[2].semilogy(freqs_hz * 60.0, np.abs(s_acc), "--",
                             color="tab:red", linewidth=1.2,
                             label=f"AHET spec (accepted cand {accepted_rank})")
            axes[2].legend(fontsize=7)

    # Mark accepted HR and Masimo PR
    radar_bpm  = float(df_row.get("radar_hr_bpm", float("nan")) or float("nan"))
    masimo_bpm = float(df_row.get("masimo_pr_bpm", float("nan")) or float("nan"))
    for ax in axes:
        if math.isfinite(radar_bpm):
            ax.axvline(radar_bpm, color="tab:red",   linestyle=":",
                       linewidth=1.2, label=f"Radar HR {radar_bpm:.1f}")
        if math.isfinite(masimo_bpm):
            ax.axvline(masimo_bpm, color="black", linestyle="--",
                       linewidth=1.0, label=f"Masimo PR {masimo_bpm:.1f}")

    window_class = str(df_row.get("window_class", ""))
    axes[0].set_title(
        f"{session_id} w{wi} - {window_class} | "
        f"radar={radar_bpm:.1f} masimo={masimo_bpm:.1f} bpm"
    )
    axes[-1].set_xlabel("rate (bpm)")
    fig.subplots_adjust(left=0.08, right=0.98, top=0.93, bottom=0.07, hspace=0.35)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def _select_spotlight_windows(diag_df: pd.DataFrame, n_good: int = 2, n_bad: int = 3) -> list[int]:
    """Pick representative good and bad windows for spectrum overlays."""
    masimo_ok = diag_df["masimo_low_quality"].astype(str).str.lower().isin(("false", "0", ""))
    good = diag_df.index[
        (diag_df["window_class"] == "good_valid") & masimo_ok
    ].tolist()[:n_good]
    bad = diag_df.index[
        diag_df["window_class"].isin(("bad_valid", "severe_bad_valid")) & masimo_ok
    ].tolist()[:n_bad]
    return sorted(set(good) | set(bad))


# ---------------------------------------------------------------------------
# Per-session processing
# ---------------------------------------------------------------------------

def process_session(
    data: dict,
    out_plots: Path,
    resp_harmonic_guard_hz: float,
    heart_band_hz: tuple[float, float],
    no_plots: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Returns (diag_df, cand_df, peaks_df) for one session."""
    sid      = data["session_id"]
    df       = data["df"]
    npz      = data["npz"]
    freqs_hz = data["freqs_hz"]

    diag_df  = compute_window_diagnostics(sid, df, npz, resp_harmonic_guard_hz)
    cand_df  = compute_candidate_diagnostics(sid, df, npz, freqs_hz, resp_harmonic_guard_hz)
    peaks_df = compute_top_peaks(sid, df, npz, freqs_hz, heart_band_hz)

    if not no_plots:
        _plot_hr_vs_masimo(
            diag_df, out_plots / f"{sid}_hr_vs_masimo.png", sid,
        )
        _plot_hr_vs_resp_harmonics(
            diag_df, out_plots / f"{sid}_hr_vs_resp_harmonics.png", sid,
        )
        _plot_candidate_scatter(
            diag_df, cand_df, freqs_hz,
            out_plots / f"{sid}_candidate_scatter.png", sid,
        )
        spotlight = _select_spotlight_windows(diag_df)
        for wi in spotlight:
            row  = diag_df.iloc[wi]
            path = out_plots / f"{sid}_spectrum_w{wi:03d}_{row['window_class']}.png"
            _plot_spectrum_overlay(wi, row, npz, freqs_hz, path, sid, heart_band_hz)

    return diag_df, cand_df, peaks_df


# ---------------------------------------------------------------------------
# Aggregate summary
# ---------------------------------------------------------------------------

def _session_counts(diag_df: pd.DataFrame) -> dict:
    masimo_ok = ~diag_df["masimo_low_quality"].astype(str).str.lower().isin(("true", "1"))
    valid = diag_df["hr_valid"].astype(str).str.lower().isin(("true", "1"))
    counts = diag_df["window_class"].value_counts().to_dict()
    n_bad_v = int(diag_df["window_class"].isin(("bad_valid", "severe_bad_valid")).sum())
    within_guard = int(
        diag_df.loc[
            diag_df["window_class"].isin(("bad_valid", "severe_bad_valid")),
            "accepted_hr_within_resp_harmonic_guard",
        ].astype(str).str.lower().isin(("true", "1")).sum()
    )
    paired = valid & masimo_ok
    errs   = pd.to_numeric(diag_df.loc[paired, "hr_abs_error_bpm"], errors="coerce").dropna()
    mae    = float(errs.mean()) if len(errs) > 0 else float("nan")
    return {
        "n_windows":             len(diag_df),
        "class_counts":          counts,
        "n_bad_valid":           n_bad_v,
        "n_bad_within_guard":    within_guard,
        "mae_bpm":               mae,
        "n_paired_for_mae":      len(errs),
    }


def build_aggregate_summary(
    session_results: dict[str, dict],
    failed_sessions: list[str],
) -> dict:
    sessions_out = {}
    for sid, counts in session_results.items():
        sessions_out[sid] = counts
    return {
        "sessions_processed": list(session_results.keys()),
        "sessions_failed":    failed_sessions,
        "per_session":        sessions_out,
    }


def build_markdown_report(agg: dict) -> str:
    lines = [
        "# Step 6 HR Diagnostic Report",
        "",
        f"Sessions processed: {', '.join(agg['sessions_processed'])}",
    ]
    if agg["sessions_failed"]:
        lines.append(f"**Sessions failed (missing outputs):** "
                     f"{', '.join(agg['sessions_failed'])}")
    lines += ["", "## Per-session summary", ""]
    lines.append("| Session | n_windows | good_valid | bad_valid | severe_bad_valid | "
                 "bad_within_guard | MAE (bpm) |")
    lines.append("|---------|-----------|------------|-----------|-----------------|"
                 "-----------------|-----------|")
    for sid, counts in agg["per_session"].items():
        cc  = counts.get("class_counts", {})
        mae = counts.get("mae_bpm", float("nan"))
        mae_str = f"{mae:.2f}" if math.isfinite(mae) else "—"
        lines.append(
            f"| {sid} "
            f"| {counts['n_windows']} "
            f"| {cc.get('good_valid', 0)} "
            f"| {cc.get('bad_valid', 0)} "
            f"| {cc.get('severe_bad_valid', 0)} "
            f"| {counts['n_bad_within_guard']} / {counts['n_bad_valid']} "
            f"| {mae_str} |"
        )
    lines += ["", "## Failure-mode attribution", ""]
    for sid, counts in agg["per_session"].items():
        n_bad = counts["n_bad_valid"]
        n_guard = counts["n_bad_within_guard"]
        if n_bad == 0:
            lines.append(f"**{sid}**: no bad-valid windows.")
        else:
            pct = 100.0 * n_guard / n_bad if n_bad > 0 else 0.0
            if pct > 50:
                mode = "likely respiratory harmonic leak (accepted HR within guard)"
            else:
                mode = "likely AHET false-accept or low-band-edge artifact"
            lines.append(f"**{sid}**: {n_bad} bad-valid windows; "
                         f"{n_guard}/{n_bad} ({pct:.0f}%) within resp guard. → {mode}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Threshold sweep (Phase 2)
# ---------------------------------------------------------------------------

SWEEP_GRIDS: dict[str, list[float]] = {
    "min_second_harmonic_ratio_db":       [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
    "min_candidate_prominence":            [3.0],
    "min_peak_to_floor_db":               [0.0, 2.0, 3.0, 4.0, 5.0, 6.0],
    "low_candidate_min_peak_to_floor_db":  [0.0, 3.0, 4.0, 5.0, 6.0],
}
# Total combinations: 6 × 1 × 6 × 5 = 180

# Window classes whose rejection cannot be fixed by candidate-threshold changes alone.
# These gates fire before or after the AHET candidate step (resp. rate gate,
# resp. harmonic coincidence check, frame-quality gate) and persist regardless
# of which candidate thresholds are chosen.
_NON_CANDIDATE_BLOCKED: frozenset[str] = frozenset({
    "resp_harmonic_rejected",      # resp_harmonic_coincident post-AHET check
    "resp_invalid_or_edge_locked", # breathing-rate gate (pre-estimator)
    "quality_gated",               # frame-quality gate (pre-estimator)
    "other_invalid",               # catch-all; conservatively not fixable
})


def _to_float(val) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return float("nan")


def _to_bool(val) -> bool:
    if isinstance(val, (bool, np.bool_)):
        return bool(val)
    try:
        f = float(val)
        return math.isfinite(f) and f != 0.0
    except (TypeError, ValueError):
        pass
    return str(val).lower().strip() in ("true", "1", "yes")


def _candidate_fail_reason(
    c: dict,
    min_ratio_db: float,
    min_prominence: float,
    min_floor_db: float,
    low_cand_floor_db: float,
) -> str:
    """Return "" if the candidate passes all replay gates, else the first failing gate name."""
    ratio_db = _to_float(c.get("peak_to_floor_ratio_db"))
    prom     = _to_float(c.get("candidate_prominence"))
    is_low   = _to_bool(c.get("candidate_is_low", False))
    rej_rsn  = str(c.get("candidate_rejection_reason", ""))

    if not math.isfinite(ratio_db) or ratio_db < min_ratio_db:
        return "ratio_db_low"
    if not math.isfinite(prom) or prom < min_prominence:
        return "prominence_low"
    # Preserve the low_candidate_competitor decision from Step 6.1
    if rej_rsn == "low_candidate_competitor":
        return "low_candidate_competitor"
    if ratio_db < min_floor_db:
        return "peak_to_floor_db_low"
    if is_low and ratio_db < low_cand_floor_db:
        return "low_candidate_floor_db_low"
    return ""


def _replay_window_single(
    cand_rows: list[dict],
    min_ratio_db: float,
    min_prominence: float,
    min_floor_db: float,
    low_cand_floor_db: float,
) -> tuple[int, float, str]:
    """Replay acceptance for one window under one threshold combination.

    Candidates in cand_rows must be sorted by candidate_rank ascending.
    Returns (accepted_rank, refined_hz_of_accepted, replay_rejection_reason).
    accepted_rank = -1 and refined_hz = nan when no candidate survives.
    replay_rejection_reason = "" when a candidate is accepted.
    """
    first_fail: str | None = None
    for c in cand_rows:
        if not _to_bool(c.get("candidate_attempted", False)):
            continue
        reason = _candidate_fail_reason(c, min_ratio_db, min_prominence, min_floor_db, low_cand_floor_db)
        if reason == "":
            return int(c.get("candidate_rank", -1)), _to_float(c.get("candidate_refined_hz")), ""
        if first_fail is None:
            first_fail = reason
    return -1, float("nan"), first_fail or "no_attempted_candidates"


def run_threshold_sweep(
    cand_df: pd.DataFrame,
    diag_df: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Replay candidate acceptance across SWEEP_GRIDS without modifying Step 6 outputs.

    Returns (sweep_df, window_decisions_df), both sorted by pipeline_score ascending.

    Two parallel sets of metrics are computed per threshold combination:

    candidate_* — candidate-only: valid when a candidate survives the threshold gates,
        regardless of what other Step 6 gates would do.  Useful for diagnosing the
        candidate filter in isolation.

    pipeline_* — pipeline-realistic: valid only when a candidate survives AND the
        current Step 6 run did not already block the window via a non-candidate gate
        (resp_harmonic_coincident, resp_rate gate, quality gate).  This is the correct
        predictor of what a Step 6 threshold change alone would actually deliver.

    Sort and primary score are based on pipeline_score.  Only PI-good Masimo windows
    (masimo_low_quality=False, masimo_pr_bpm finite) count toward error metrics.
    """
    thresh_keys = list(SWEEP_GRIDS.keys())
    all_combos  = list(itertools.product(*[SWEEP_GRIDS[k] for k in thresh_keys]))

    # Group candidates by window key, sorted by rank
    cand_by_win: dict[tuple, list[dict]] = {}
    for (sid, wi), g in cand_df.groupby(["session_id", "window_index"], sort=True):
        cand_by_win[(str(sid), int(wi))] = g.sort_values("candidate_rank").to_dict("records")

    # Index diag rows by window key
    diag_by_win: dict[tuple, dict] = {}
    for _, row in diag_df.iterrows():
        key = (str(row["session_id"]), int(row["window_index"]))
        diag_by_win[key] = row.to_dict()

    # Current classification sets for delta tracking
    current_good: set[tuple] = set()
    current_bad:  set[tuple] = set()
    for key, drow in diag_by_win.items():
        wc = str(drow.get("window_class", ""))
        if wc == "good_valid":
            current_good.add(key)
        elif wc in ("bad_valid", "severe_bad_valid"):
            current_bad.add(key)

    sweep_rows:    list[dict] = []
    decision_rows: list[dict] = []

    for combo in all_combos:
        t          = dict(zip(thresh_keys, combo))
        min_ratio  = t["min_second_harmonic_ratio_db"]
        min_prom   = t["min_candidate_prominence"]
        min_floor  = t["min_peak_to_floor_db"]
        low_floor  = t["low_candidate_min_peak_to_floor_db"]

        # Candidate-only accumulators
        abs_errs:    list[float] = []
        signed_errs: list[float] = []
        n_valid = n_good = n_accept = n_bad = n_severe = 0
        lost_good = rejected_bad = 0

        # Pipeline-realistic accumulators
        p_abs_errs:    list[float] = []
        p_signed_errs: list[float] = []
        p_n_valid = p_n_good = p_n_accept = p_n_bad = p_n_severe = 0
        p_lost_good = p_rejected_bad = 0

        for key, c_rows in cand_by_win.items():
            drow       = diag_by_win.get(key, {})
            masimo_bpm = _to_float(drow.get("masimo_pr_bpm"))
            masimo_low = _to_bool(drow.get("masimo_low_quality", False))
            current_wc = str(drow.get("window_class", "other_invalid"))

            acc_rank, refined_hz, rej_rsn = _replay_window_single(
                c_rows, min_ratio, min_prom, min_floor, low_floor,
            )

            # Candidate-only validity: a candidate survived the threshold gates
            replay_valid  = acc_rank >= 0 and math.isfinite(refined_hz)
            replay_hr_bpm = refined_hz * 60.0 if replay_valid else float("nan")

            # Pipeline validity: candidate survived AND not blocked by a non-candidate gate
            pipeline_replay_valid = replay_valid and (current_wc not in _NON_CANDIDATE_BLOCKED)

            if replay_valid:
                n_valid += 1
            if pipeline_replay_valid:
                p_n_valid += 1

            masimo_ok = math.isfinite(masimo_bpm) and not masimo_low

            # --- Candidate-only error metrics ---
            if replay_valid and masimo_ok:
                err     = replay_hr_bpm - masimo_bpm
                abs_err = abs(err)
                abs_errs.append(abs_err)
                signed_errs.append(err)
                if abs_err <= GOOD_THRESH_BPM:
                    n_good  += 1;  replay_wc = "good_valid"
                elif abs_err <= BAD_THRESH_BPM:
                    n_accept += 1; replay_wc = "acceptable_valid"
                elif abs_err <= SEVERE_THRESH_BPM:
                    n_bad   += 1;  replay_wc = "bad_valid"
                else:
                    n_severe += 1; replay_wc = "severe_bad_valid"
                replay_err     = err
                replay_abs_err = abs_err
            else:
                replay_err = replay_abs_err = float("nan")
                replay_wc  = "replay_invalid" if not replay_valid else "replay_valid_masimo_low"

            # --- Pipeline-realistic error metrics ---
            if pipeline_replay_valid and masimo_ok:
                p_err     = replay_hr_bpm - masimo_bpm  # same HR as candidate
                p_abs_err = abs(p_err)
                p_abs_errs.append(p_abs_err)
                p_signed_errs.append(p_err)
                if p_abs_err <= GOOD_THRESH_BPM:
                    p_n_good  += 1;  pipeline_wc = "good_valid"
                elif p_abs_err <= BAD_THRESH_BPM:
                    p_n_accept += 1; pipeline_wc = "acceptable_valid"
                elif p_abs_err <= SEVERE_THRESH_BPM:
                    p_n_bad   += 1;  pipeline_wc = "bad_valid"
                else:
                    p_n_severe += 1; pipeline_wc = "severe_bad_valid"
                pipeline_err     = p_err
                pipeline_abs_err = p_abs_err
            else:
                pipeline_err = pipeline_abs_err = float("nan")
                if not pipeline_replay_valid and replay_valid:
                    pipeline_wc = "pipeline_blocked"   # non-candidate gate would fire
                elif not pipeline_replay_valid:
                    pipeline_wc = "replay_invalid"
                else:
                    pipeline_wc = "replay_valid_masimo_low"

            # Delta tracking
            if key in current_good and not replay_valid:
                lost_good += 1
            if key in current_bad and not replay_valid:
                rejected_bad += 1
            if key in current_good and not pipeline_replay_valid:
                p_lost_good += 1
            if key in current_bad and not pipeline_replay_valid:
                p_rejected_bad += 1

            decision_rows.append({
                "session_id":                         key[0],
                "window_index":                       key[1],
                "min_second_harmonic_ratio_db":       min_ratio,
                "min_candidate_prominence":           min_prom,
                "min_peak_to_floor_db":               min_floor,
                "low_candidate_min_peak_to_floor_db": low_floor,
                # Candidate-only columns
                "replay_hr_valid":                    replay_valid,
                "replay_radar_hr_bpm":                replay_hr_bpm,
                "replay_accepted_candidate_rank":     acc_rank,
                "replay_rejection_reason":            rej_rsn,
                "replay_hr_error_bpm":                replay_err,
                "replay_hr_abs_error_bpm":            replay_abs_err,
                "replay_window_class":                replay_wc,
                # Pipeline-realistic columns
                "pipeline_replay_valid":              pipeline_replay_valid,
                "pipeline_replay_hr_error_bpm":       pipeline_err,
                "pipeline_replay_hr_abs_error_bpm":   pipeline_abs_err,
                "pipeline_replay_window_class":       pipeline_wc,
                # Shared
                "masimo_pr_bpm":                      masimo_bpm,
                "masimo_low_quality":                 masimo_low,
                "current_window_class":               current_wc,
            })

        def _agg(errs: list[float], s_errs: list[float]) -> tuple:
            if not errs:
                return float("nan"), float("nan"), float("nan")
            mae  = float(np.mean(errs))
            rmse = float(np.sqrt(np.mean(np.array(s_errs) ** 2)))
            bias = float(np.mean(s_errs))
            return mae, rmse, bias

        mae,   rmse,   bias   = _agg(abs_errs,   signed_errs)
        p_mae, p_rmse, p_bias = _agg(p_abs_errs, p_signed_errs)

        score   = 100 * n_severe   + 50 * n_bad   + 5 * lost_good   - 2 * n_good
        p_score = 100 * p_n_severe + 50 * p_n_bad + 5 * p_lost_good - 2 * p_n_good

        sweep_rows.append({
            "min_second_harmonic_ratio_db":       min_ratio,
            "min_candidate_prominence":           min_prom,
            "min_peak_to_floor_db":               min_floor,
            "low_candidate_min_peak_to_floor_db": low_floor,
            # Candidate-only metrics
            "n_valid_replay":                     n_valid,
            "n_paired_replay":                    len(abs_errs),
            "n_good_valid_replay":                n_good,
            "n_acceptable_valid_replay":          n_accept,
            "n_bad_valid_replay":                 n_bad,
            "n_severe_bad_valid_replay":          n_severe,
            "mae_bpm":                            mae,
            "rmse_bpm":                           rmse,
            "bias_bpm":                           bias,
            "lost_current_good_windows":          lost_good,
            "rejected_current_bad_windows":       rejected_bad,
            "score":                              score,
            # Pipeline-realistic metrics
            "pipeline_n_valid_replay":                     p_n_valid,
            "pipeline_n_paired_replay":                    len(p_abs_errs),
            "pipeline_n_good_valid_replay":                p_n_good,
            "pipeline_n_acceptable_valid_replay":          p_n_accept,
            "pipeline_n_bad_valid_replay":                 p_n_bad,
            "pipeline_n_severe_bad_valid_replay":          p_n_severe,
            "pipeline_mae_bpm":                            p_mae,
            "pipeline_rmse_bpm":                           p_rmse,
            "pipeline_bias_bpm":                           p_bias,
            "pipeline_lost_current_good_windows":          p_lost_good,
            "pipeline_rejected_current_bad_windows":       p_rejected_bad,
            "pipeline_score":                              p_score,
        })

    sweep_df     = pd.DataFrame(sweep_rows).sort_values("pipeline_score").reset_index(drop=True)
    decisions_df = pd.DataFrame(decision_rows)
    return sweep_df, decisions_df


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO_ROOT), text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Step 6 HR diagnostic")
    ap.add_argument("--sessions", nargs="+", required=True)
    ap.add_argument("--out", default="results/diagnose",
                    help="Output root (default: results/diagnose)")
    ap.add_argument("--results-root", default="results",
                    help="Results root where session step_6 dirs live")
    ap.add_argument("--heart-band-hz", nargs=2, type=float, default=[0.8, 2.0],
                    metavar=("LO", "HI"))
    ap.add_argument("--resp-harmonic-guard-hz", type=float, default=0.05)
    ap.add_argument("--no-plots", action="store_true",
                    help="Skip all plot generation (useful for CI/tests)")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args(argv)

    out_root     = REPO_ROOT / args.out / "step6_hr"
    results_root = REPO_ROOT / args.results_root
    heart_band   = tuple(args.heart_band_hz)
    guard_hz     = args.resp_harmonic_guard_hz

    if out_root.exists() and not args.overwrite:
        print(f"Output already exists: {out_root}\nRe-run with --overwrite.", file=sys.stderr)
        return 1
    out_root.mkdir(parents=True, exist_ok=True)
    out_plots = out_root / "plots"
    out_plots.mkdir(exist_ok=True)

    # Configure logging
    log_path = out_root / "diagnostic.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, mode="w", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    log.info("diagnose_step6_hr — commit %s", _git_commit())
    log.info("Sessions: %s", args.sessions)
    log.info("Output:   %s", out_root)

    all_diag_dfs  : list[pd.DataFrame] = []
    all_cand_dfs  : list[pd.DataFrame] = []
    all_peaks_dfs : list[pd.DataFrame] = []
    session_results: dict[str, dict]   = {}
    failed_sessions: list[str]         = []

    for sid in args.sessions:
        log.info("--- Session %s ---", sid)
        data = _load_session(sid, results_root)
        if data is None:
            failed_sessions.append(sid)
            continue

        diag_df, cand_df, peaks_df = process_session(
            data, out_plots, guard_hz, heart_band, no_plots=args.no_plots,
        )
        all_diag_dfs.append(diag_df)
        all_cand_dfs.append(cand_df)
        all_peaks_dfs.append(peaks_df)
        session_results[sid] = _session_counts(diag_df)
        log.info(
            "  %d windows | %d good_valid | %d bad_valid | %d severe_bad_valid",
            session_results[sid]["n_windows"],
            session_results[sid]["class_counts"].get("good_valid", 0),
            session_results[sid]["class_counts"].get("bad_valid", 0),
            session_results[sid]["class_counts"].get("severe_bad_valid", 0),
        )

    # Concatenate all sessions
    if not all_diag_dfs:
        log.error("No sessions produced output — aborting.")
        return 1

    diag_all  = pd.concat(all_diag_dfs,  ignore_index=True)
    cand_all  = pd.concat(all_cand_dfs,  ignore_index=True)
    peaks_all = pd.concat(all_peaks_dfs, ignore_index=True)

    # Write CSVs
    diag_all.to_csv(out_root / "per_window_diagnostics.csv",  index=False)
    cand_all.to_csv(out_root / "candidate_diagnostics.csv",   index=False)
    peaks_all.to_csv(out_root / "top_peaks.csv",              index=False)

    valid_mask = diag_all["hr_valid"].astype(str).str.lower().isin(("true", "1"))
    abs_err    = pd.to_numeric(diag_all["hr_abs_error_bpm"], errors="coerce")
    bad_mask   = valid_mask & (abs_err > BAD_THRESH_BPM)
    good_mask  = valid_mask & (abs_err <= GOOD_THRESH_BPM)
    diag_all[bad_mask].to_csv(out_root / "bad_valid_windows.csv",  index=False)
    diag_all[good_mask].to_csv(out_root / "good_valid_windows.csv", index=False)

    # Threshold sweep
    log.info("Running threshold sweep (%d combos)...", len(list(itertools.product(*SWEEP_GRIDS.values()))))
    sweep_df, decisions_df = run_threshold_sweep(cand_all, diag_all)
    sweep_df.to_csv(out_root / "threshold_sweep.csv", index=False)
    decisions_df.to_csv(out_root / "threshold_sweep_window_decisions.csv", index=False)
    best = sweep_df.iloc[0]
    log.info(
        "Sweep best (pipeline_score=%.0f): ratio_db>=%.1f floor_db>=%.1f low_floor_db>=%.1f"
        " -> good=%d bad=%d severe=%d lost_good=%d",
        best["pipeline_score"],
        best["min_second_harmonic_ratio_db"],
        best["min_peak_to_floor_db"],
        best["low_candidate_min_peak_to_floor_db"],
        best["pipeline_n_good_valid_replay"],
        best["pipeline_n_bad_valid_replay"],
        best["pipeline_n_severe_bad_valid_replay"],
        best["pipeline_lost_current_good_windows"],
    )

    # Summary
    agg = build_aggregate_summary(session_results, failed_sessions)
    (out_root / "summary.json").write_text(
        json.dumps(agg, indent=2, default=str), encoding="utf-8"
    )
    (out_root / "summary.md").write_text(
        build_markdown_report(agg), encoding="utf-8"
    )

    if failed_sessions:
        log.warning("Failed sessions (missing Step 6 outputs): %s", failed_sessions)
    log.info("Done — outputs in %s", out_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
