#!/usr/bin/env python3
"""Step 5: Breathing rate extraction from locked-bin HDF5 cubes.

Pipeline position:
  step_1 -> step_2 -> step_3 -> range_plot QC -> step_4 -> step_5 -> step_6

Reads the time-domain HDF5 cube written by Step 2, applies a Hann-windowed
range FFT, extracts the chest-bin phase using delta_before_mean (default), then
estimates breathing rate per 30 s sliding window using three methods:
  A. FFT peak + parabolic refinement (baseline)
  B. Harmonic accumulation (primary)
  C. STFT subwindow stability (confidence check)

Compares radar RR against Masimo Breaths/min (evaluation only).
Writes a Step 6 contract CSV to data/processed/breathing_rate/<session_id>.csv.

Run from repo root:
    python -X utf8 steps/step_5/extract_breathing_rate.py --session test
    python -X utf8 steps/step_5/extract_breathing_rate.py --all
    python -X utf8 steps/step_5/extract_breathing_rate.py --session test --no-write
    python -X utf8 steps/step_5/extract_breathing_rate.py --session test --overwrite
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import subprocess
import sys
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

from src import masimo as masimo_mod          # noqa: E402
from src.respiration import (                 # noqa: E402
    extract_chest_phase,
    fft_estimate_rr,
    ha_estimate_rr,
    stft_stability,
    fuse_estimates,
)

SEP  = "=" * 72
LINE = "-" * 72

_CONFIDENCE_ORDER = {"high": 2, "medium": 1, "low": 0, "quality_gated": -1}
_STEP3_REQUIRED   = [
    "locked_bin", "locked_range_m",
    "chest_bin_confidence", "chest_bin_review_required",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def _nan_row(
    session_id: str,
    window_index: int,
    start_epoch: float,
    end_epoch: float,
    start_frame: int,
    end_frame: int,
    locked_bin: int,
    locked_range_m: float,
    n_bad_frames: int,
    bad_fraction: float,
    quality_gated: bool,
    masimo_rr_bpm: float,
) -> dict:
    """Build a CSV row where all radar estimates are NaN (quality-gated or estimation failed)."""
    _nan = float("nan")
    masimo_valid = np.isfinite(masimo_rr_bpm)
    return {
        "session_id":        session_id,
        "window_index":      window_index,
        "start_epoch":       start_epoch,
        "end_epoch":         end_epoch,
        "start_frame":       start_frame,
        "end_frame":         end_frame,
        "locked_bin":        locked_bin,
        "locked_range_m":    locked_range_m,
        "radar_rr_bpm":      _nan,
        "resp_peak_hz":      _nan,
        "masimo_rr_bpm":     masimo_rr_bpm,
        "rr_error_bpm":      _nan,
        "rr_abs_error_bpm":  _nan,
        "fft_rr_bpm":        _nan,
        "ha_rr_bpm":         _nan,
        "stft_rr_bpm":       _nan,
        "resp_confidence":   "quality_gated" if quality_gated else "low",
        "resp_valid":        False,
        "quality_gated":     quality_gated,
        "edge_locked":       False,
        "edge_lock_side":    "none",
        "n_bad_frames":      n_bad_frames,
        "bad_fraction":      bad_fraction,
        "ha_score":          _nan,
        "ha_harmonics_used": 0,
        "fft_peak_snr_db":   _nan,
        "stft_rr_std_bpm":   _nan,
    }


def _build_row(
    session_id: str,
    window_index: int,
    start_epoch: float,
    end_epoch: float,
    start_frame: int,
    end_frame: int,
    locked_bin: int,
    locked_range_m: float,
    fft_r: dict,
    ha_r: dict,
    stft_r: dict,
    fusion: dict,
    masimo_rr_bpm: float,
    n_bad_frames: int,
    bad_fraction: float,
    edge_locked: bool = False,
    edge_lock_side: str = "",
) -> dict:
    """Build a full CSV row from estimator outputs."""
    _nan = float("nan")
    radar_rr = fusion["radar_rr_bpm"]
    masimo_valid = np.isfinite(masimo_rr_bpm)
    radar_valid  = np.isfinite(radar_rr)
    err     = (radar_rr - masimo_rr_bpm) if (radar_valid and masimo_valid) else _nan
    abs_err = abs(err) if np.isfinite(err) else _nan
    return {
        "session_id":        session_id,
        "window_index":      window_index,
        "start_epoch":       start_epoch,
        "end_epoch":         end_epoch,
        "start_frame":       start_frame,
        "end_frame":         end_frame,
        "locked_bin":        locked_bin,
        "locked_range_m":    locked_range_m,
        "radar_rr_bpm":      radar_rr,
        "resp_peak_hz":      fusion["resp_peak_hz"],
        "masimo_rr_bpm":     masimo_rr_bpm,
        "rr_error_bpm":      err,
        "rr_abs_error_bpm":  abs_err,
        "fft_rr_bpm":        fft_r.get("fft_rr_bpm", _nan),
        "ha_rr_bpm":         ha_r.get("ha_rr_bpm",   _nan),
        "stft_rr_bpm":       stft_r.get("stft_rr_bpm", _nan),
        "resp_confidence":   fusion["resp_confidence"],
        "resp_valid":        bool(fusion["resp_valid"]),
        "quality_gated":     False,
        "edge_locked":       edge_locked,
        "edge_lock_side":    edge_lock_side,
        "n_bad_frames":      n_bad_frames,
        "bad_fraction":      bad_fraction,
        "ha_score":          ha_r.get("ha_score",           _nan),
        "ha_harmonics_used": ha_r.get("ha_harmonics_used",  0),
        "fft_peak_snr_db":   fft_r.get("fft_peak_snr_db",  _nan),
        "stft_rr_std_bpm":   stft_r.get("stft_rr_std_bpm", _nan),
    }


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------

def _plot_rr_comparison(df: pd.DataFrame, out_path: Path, session_id: str) -> None:
    t = pd.to_datetime(df["start_epoch"], unit="s", utc=True)
    fig, ax = plt.subplots(figsize=(11, 4))
    valid = df["resp_valid"].astype(bool)
    ax.plot(t, df["masimo_rr_bpm"], "o-", color="black",    label="Masimo RR (ref)", zorder=3)
    ax.plot(t[valid],  df.loc[valid,  "radar_rr_bpm"], "s-", color="tab:blue",
            label="Radar RR (valid)", zorder=4)
    ax.plot(t[~valid], df.loc[~valid, "radar_rr_bpm"], "x", color="tab:gray",
            label="Radar RR (invalid/NaN)", zorder=2, markersize=6)
    ax.set_xlabel("time (UTC)")
    ax.set_ylabel("rate (bpm)")
    ax.set_title(f"{session_id} — Radar RR vs Masimo Breaths/min")
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
    ax.plot(t, df["fft_rr_bpm"],  "o-", label="FFT",  color="tab:orange")
    ax.plot(t, df["ha_rr_bpm"],   "s-", label="HA",   color="tab:blue")
    ax.plot(t, df["stft_rr_bpm"], "^-", label="STFT", color="tab:green")
    ax.plot(t, df["radar_rr_bpm"], "D-", label="Final", color="black", linewidth=1.5)
    ax.set_ylabel("RR (bpm)")
    ax.set_title(f"{session_id} — Method agreement")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    ax2 = axes[1]
    agree = (df["fft_rr_bpm"] - df["ha_rr_bpm"]).abs()
    ax2.bar(range(len(agree)), agree.values, color="tab:red", alpha=0.7, label="|FFT - HA| (bpm)")
    ax2.axhline(2.0, color="green", linestyle="--", linewidth=0.8, label="high threshold (2 bpm)")
    ax2.axhline(4.0, color="orange", linestyle="--", linewidth=0.8, label="medium threshold (4 bpm)")
    ax2.set_xlabel("window index")
    ax2.set_ylabel("|FFT - HA| (bpm)")
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
    trim_frames: int,
    frame_rate_hz: float,
    out_path: Path,
    session_id: str,
) -> None:
    t = np.arange(len(phase)) / frame_rate_hz
    bad_frames = ~quality_mask
    fig, axes = plt.subplots(2, 1, figsize=(11, 5), sharex=True)

    ax = axes[0]
    ax.plot(t, phase, color="tab:blue", linewidth=0.8, label="chest phase (rad)")
    ax.set_ylabel("phase (rad)")
    ax.set_title(f"{session_id} — Chest phase (trim_frames={trim_frames})")
    ax.grid(True, alpha=0.3)

    ax2 = axes[1]
    ax2.fill_between(t, bad_frames.astype(float), step="mid",
                     color="tab:red", alpha=0.6, label="bad frames")
    ax2.set_ylim(-0.05, 1.05)
    ax2.set_xlabel(f"time (s from frame {trim_frames})")
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
    out_dir: Path,
    commit: str,
    *,
    no_write: bool = False,
    overwrite: bool = False,
    no_plots: bool = False,
) -> dict:
    """Process one session. Returns a summary dict.

    out_dir is the final output directory for this session (results/<sid>/step_5/).
    If it already contains breathing_windows.csv and --overwrite was not passed,
    the function raises FileExistsError before doing any work.
    """
    _nan  = float("nan")
    paths = cfg["paths"]

    h5_path = cubes_dir / f"{session_id}.h5"
    if not h5_path.exists():
        raise FileNotFoundError(f"HDF5 not found: {h5_path} — run Step 2 first")

    mas_path = data_raw / f"{session_id}_masimo.csv"
    if not mas_path.exists():
        raise FileNotFoundError(f"Masimo CSV not found: {mas_path}")

    # --- Parse manifest fields ---
    locked_bin    = int(float(str(row["locked_bin"]).strip()))
    locked_range_m = float(str(row["locked_range_m"]).strip())
    t0_base        = float(str(row["radar_start_epoch_seconds"]).strip())
    interval       = str(row.get("stationary_intervals", "") or "").strip()

    windowing_cfg = cfg["windowing"]
    use_intervals = bool(windowing_cfg.get("use_stationary_intervals", True))
    window_s      = float(windowing_cfg["window_s"])
    hop_s         = float(windowing_cfg["hop_s"])

    phase_cfg = cfg.get("phase", {})
    phase_method  = str(phase_cfg.get("method", "delta_before_mean"))
    detrend_type  = str(phase_cfg.get("detrend", "linear"))

    resp_cfg    = cfg["respiration"]
    input_cfg   = cfg.get("input_policy", {})
    max_bad_frac = float(input_cfg.get("max_bad_fraction", 0.10))

    # --- Open HDF5 ---
    with h5py.File(h5_path, "r") as f:
        frame_rate_hz = float(f.attrs.get("frame_rate_hz", 20.0))
        total_frames  = int(f.attrs["num_frames"])

        if "quality_mask" not in f:
            raise FileNotFoundError(
                f"No /quality_mask in {h5_path.name} — run Step 4 first"
            )
        quality_mask = f["quality_mask"][:].astype(bool)
        # mask_trim: ONLY used to convert absolute frame index → quality_mask index.
        # quality_mask[i] corresponds to absolute frame mask_trim + i.
        mask_trim    = int(f["quality_mask"].attrs["trim_frames"])

        # analysis_start / analysis_end: the actual frames to process.
        # Driven by stationary_intervals (settling time) — independent of mask_trim.
        if use_intervals and interval:
            parts          = interval.split("-")
            analysis_start = int(int(parts[0]) * frame_rate_hz)
            analysis_end   = int(int(parts[1]) * frame_rate_hz)
        else:
            analysis_start = mask_trim
            analysis_end   = total_frames

        analysis_end = min(analysis_end, total_frames)

        # Validate: analysis region must be covered by quality_mask
        mask_end = mask_trim + len(quality_mask)
        if analysis_start < mask_trim or analysis_end > mask_end:
            raise ValueError(
                f"{session_id}: analysis window [{analysis_start}, {analysis_end}) "
                f"is not fully covered by quality_mask [{mask_trim}, {mask_end}). "
                "Re-run Step 4 with a trim_frames ≤ stationary_intervals start."
            )

        t0        = t0_base + analysis_start / frame_rate_hz
        n_analysis = analysis_end - analysis_start

        window_frames = int(round(window_s * frame_rate_hz))
        hop_frames    = int(round(hop_s    * frame_rate_hz))

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
              f"window={window_s:.0f}s, hop={hop_s:.0f}s")

        # Load only the analysis region of the cube.
        # At IWR1642 rates (≤20 Hz, 4 chirps, 2 RX, 64 ADC) this is ~5 MB/min —
        # safe for sessions up to ~1 hour.  For longer recordings, replace with
        # chunked reads + one-frame overlap for delta_before_mean continuity.
        cube_slice = f["cube"][analysis_start:analysis_end].astype(np.complex64)

    # Slice quality_mask to the analysis region using mask_trim as the offset
    qmask_analysis = quality_mask[
        analysis_start - mask_trim : analysis_end - mask_trim
    ]

    # --- Extract chest phase for the full analysis window ---
    phase = extract_chest_phase(cube_slice, locked_bin, method=phase_method)
    del cube_slice

    # --- Generate sliding windows (absolute frame indices) ---
    windows: list[tuple[int, int]] = []
    abs_start = analysis_start
    while abs_start + window_frames <= analysis_end:
        windows.append((abs_start, abs_start + window_frames))
        abs_start += hop_frames

    if not windows:
        raise ValueError(f"No valid windows for {session_id}")

    print(f"  Windows: {len(windows)} (absolute frames {windows[0][0]}–{windows[-1][1]})")

    # --- Load Masimo ---
    mas_df = masimo_mod.load_masimo(mas_path)

    # --- Per-window estimation ---
    rows:     list[dict]  = []

    ha_cand_freqs_list:  list[np.ndarray] = []
    ha_cand_scores_list: list[np.ndarray] = []
    ha_harm_freqs_list:  list[np.ndarray] = []
    ha_harm_power_list:  list[np.ndarray] = []
    window_phase_list:   list[np.ndarray] = []
    fft_freqs_list:      list[np.ndarray] = []
    fft_spectrum_list:   list[np.ndarray] = []
    fft_peak_bin_list:   list[int]        = []

    _nan = float("nan")
    band_hz        = tuple(resp_cfg["band_hz"])
    lo_bpm         = float(band_hz[0]) * 60.0
    hi_bpm         = float(band_hz[1]) * 60.0
    edge_margin    = float(resp_cfg.get("edge_lock_margin_bpm", 2.0))
    max_harms      = int(resp_cfg.get("max_harmonics", 3))
    harm_max       = resp_cfg.get("harmonic_max_hz") or None
    sub_s          = float(resp_cfg.get("stft_subwindow_s", 10.0))
    overlap        = float(resp_cfg.get("stft_overlap", 0.5))

    for wi, (abs_s, abs_e) in enumerate(windows):
        # phase array is indexed from analysis_start, so use that as the offset
        phase_s = abs_s - analysis_start
        phase_e = abs_e - analysis_start

        # Quality gating
        mask_slice = qmask_analysis[phase_s:phase_e]   # same length as phase slice
        n_bad      = int((~mask_slice).sum())
        bad_frac   = n_bad / (phase_e - phase_s)
        quality_gated = bad_frac > max_bad_frac

        start_epoch = t0 + phase_s / frame_rate_hz
        end_epoch   = t0 + phase_e / frame_rate_hz

        masimo_rr = masimo_mod.reference_br(mas_df, start_epoch, end_epoch)

        if quality_gated:
            rows.append(_nan_row(
                session_id, wi, start_epoch, end_epoch,
                abs_s, abs_e, locked_bin, locked_range_m,
                n_bad, bad_frac, True, masimo_rr,
            ))
            # Append empty placeholders so NPZ arrays stay aligned with window index
            ha_cand_freqs_list.append(np.array([], dtype=np.float64))
            ha_cand_scores_list.append(np.array([], dtype=np.float64))
            ha_harm_freqs_list.append(np.empty((0, max_harms), dtype=np.float64))
            ha_harm_power_list.append(np.empty((0, max_harms), dtype=np.float64))
            window_phase_list.append(np.full(window_frames, _nan, dtype=np.float64))
            fft_freqs_list.append(np.array([], dtype=np.float64))
            fft_spectrum_list.append(np.array([], dtype=np.float64))
            fft_peak_bin_list.append(-1)
            continue

        win_phase = phase[phase_s:phase_e]

        fft_r  = fft_estimate_rr(win_phase, frame_rate_hz, band_hz, detrend_type=detrend_type)
        ha_r   = ha_estimate_rr(win_phase, frame_rate_hz, band_hz,
                                max_harmonics=max_harms, harmonic_max_hz=harm_max,
                                detrend_type=detrend_type)
        stft_r = stft_stability(win_phase, frame_rate_hz, band_hz,
                                subwindow_s=sub_s, overlap=overlap,
                                detrend_type=detrend_type)
        fusion = fuse_estimates(fft_r, ha_r, stft_r, resp_cfg)

        # Edge-lock detection: check final radar_rr_bpm (post-fusion) against band margins
        raw_rr = fusion["radar_rr_bpm"]
        if np.isfinite(raw_rr) and (raw_rr <= lo_bpm + edge_margin or raw_rr >= hi_bpm - edge_margin):
            edge_locked    = True
            edge_lock_side = "low" if raw_rr <= lo_bpm + edge_margin else "high"
            fusion = dict(fusion)
            fusion["radar_rr_bpm"]  = _nan
            fusion["resp_peak_hz"]  = _nan
            fusion["resp_valid"]    = False
            fusion["resp_confidence"] = "low"
        else:
            edge_locked    = False
            edge_lock_side = "none"

        rows.append(_build_row(
            session_id, wi, start_epoch, end_epoch,
            abs_s, abs_e, locked_bin, locked_range_m,
            fft_r, ha_r, stft_r, fusion,
            masimo_rr, n_bad, bad_frac,
            edge_locked=edge_locked, edge_lock_side=edge_lock_side,
        ))

        ha_cand_freqs_list.append(ha_r.get("ha_candidate_freqs_hz", np.array([])))
        ha_cand_scores_list.append(ha_r.get("ha_candidate_scores",   np.array([])))
        ha_harm_freqs_list.append(ha_r.get("ha_harmonic_freqs_hz",  np.empty((0, max_harms))))
        ha_harm_power_list.append(ha_r.get("ha_harmonic_power",     np.empty((0, max_harms))))
        window_phase_list.append(win_phase.copy())
        fft_freqs_list.append(fft_r.get("freqs_hz",  np.array([])))
        fft_spectrum_list.append(fft_r.get("spectrum", np.array([])))
        fft_peak_bin_list.append(int(fft_r.get("fft_peak_bin", -1)))

    df = pd.DataFrame(rows)

    # --- Summary metrics ---
    valid_rows = df[df["resp_valid"].astype(bool) & df["masimo_rr_bpm"].notna() & df["rr_abs_error_bpm"].notna()]
    n_total    = len(df)
    n_valid    = int(df["resp_valid"].astype(bool).sum())
    n_gated    = int(df["quality_gated"].astype(bool).sum())
    n_finite   = len(valid_rows)

    n_edge_locked       = int(df["edge_locked"].astype(bool).sum())
    n_valid_edge_locked = int((df["edge_locked"].astype(bool) & df["resp_valid"].astype(bool)).sum())
    edge_locked_fraction = n_edge_locked / n_total if n_total > 0 else 0.0

    if n_finite > 0:
        mae  = float(valid_rows["rr_abs_error_bpm"].mean())
        rmse = float(math.sqrt((valid_rows["rr_error_bpm"] ** 2).mean()))
        bias = float(valid_rows["rr_error_bpm"].mean())
    else:
        mae = rmse = bias = float("nan")

    conf_counts = df["resp_confidence"].value_counts().to_dict()

    print(f"  Windows: {n_total} total | {n_valid} valid | {n_gated} quality-gated | {n_edge_locked} edge-locked")
    if n_valid_edge_locked > 0:
        print(f"  WARNING: n_valid_edge_locked={n_valid_edge_locked} — edge-lock suppression logic has a bug")
    if math.isfinite(mae):
        print(f"  RR:  MAE={mae:.2f}  RMSE={rmse:.2f}  bias={bias:+.2f} bpm  (N={n_finite})")
    else:
        print("  RR:  MAE=NaN (no windows with both valid radar RR and Masimo RR)")
    print(f"  Confidence: {conf_counts}")

    high_el_threshold = float(resp_cfg.get("high_edge_lock_fraction_threshold", 0.5))
    summary = {
        "session_id":            session_id,
        "commit":                commit,
        "n_windows":             n_total,
        "n_valid":               n_valid,
        "n_gated":               n_gated,
        "n_edge_locked":         n_edge_locked,
        "edge_locked_fraction":  edge_locked_fraction,
        "high_edge_lock_fraction": edge_locked_fraction >= high_el_threshold,
        "n_valid_edge_locked":   n_valid_edge_locked,
        "n_finite_rr":           n_finite,
        "mae_bpm":               mae  if math.isfinite(mae)  else None,
        "rmse_bpm":              rmse if math.isfinite(rmse) else None,
        "bias_bpm":              bias if math.isfinite(bias) else None,
        "conf_counts":           conf_counts,
        "frame_rate_hz":         frame_rate_hz,
        "window_s":              window_s,
        "hop_s":                 hop_s,
        "locked_bin":            locked_bin,
        "locked_range_m":        locked_range_m,
        "phase_method":          phase_method,
    }

    if no_write:
        print("  Write skipped (--no-write)")
        return summary

    # --- Overwrite guard ---
    sentinel = out_dir / "breathing_windows.csv"
    if sentinel.exists() and not overwrite:
        raise FileExistsError(
            f"Results already exist in:\n  {out_dir}\n"
            "Re-run with --overwrite to replace them."
        )

    # --- Write results directory outputs ---
    out_dir.mkdir(parents=True, exist_ok=True)

    windows_csv = out_dir / "breathing_windows.csv"
    df.to_csv(windows_csv, index=False)
    print(f"  breathing_windows.csv -> {windows_csv}")

    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )

    # NPZ intermediates — pad ragged HA arrays to uniform shape before saving
    max_cands = max((len(a) for a in ha_cand_freqs_list), default=0)
    max_harms = resp_cfg.get("max_harmonics", 3)
    n_win     = len(windows)

    def _pad_1d(arrays: list[np.ndarray], length: int) -> np.ndarray:
        out = np.full((len(arrays), length), float("nan"), dtype=np.float64)
        for i, a in enumerate(arrays):
            out[i, : len(a)] = a
        return out

    def _pad_2d(arrays: list[np.ndarray], rows: int, cols: int) -> np.ndarray:
        out = np.full((len(arrays), rows, cols), float("nan"), dtype=np.float64)
        for i, a in enumerate(arrays):
            r = min(a.shape[0], rows)
            c = min(a.shape[1], cols) if a.ndim == 2 else 0
            if r > 0 and c > 0:
                out[i, :r, :c] = a[:r, :c]
        return out

    # FFT spectrum arrays have the same length for all non-gated windows (rfftfreq length
    # depends only on window size and fs, both fixed). Gated windows store empty arrays
    # (length 0), so pad to the longest observed length.
    max_spec_len = max((len(a) for a in fft_freqs_list), default=0)

    npz_path = out_dir / "respiration_intermediates.npz"
    np.savez(
        npz_path,
        phase_full=phase,
        quality_mask_analysis=qmask_analysis,
        window_starts=np.array([s for s, _ in windows]),
        window_ends=np.array([e for _, e in windows]),
        window_phase=np.array(window_phase_list, dtype=np.float64),
        ha_candidate_freqs_hz=_pad_1d(ha_cand_freqs_list, max_cands),
        ha_candidate_scores=_pad_1d(ha_cand_scores_list, max_cands),
        ha_harmonic_freqs_hz=_pad_2d(ha_harm_freqs_list, max_cands, max_harms),
        ha_harmonic_power=_pad_2d(ha_harm_power_list, max_cands, max_harms),
        fft_freqs_hz=_pad_1d(fft_freqs_list, max_spec_len),
        fft_spectrum=_pad_1d(fft_spectrum_list, max_spec_len),
        fft_peak_bin=np.array(fft_peak_bin_list, dtype=np.int32),
    )
    print(f"  respiration_intermediates.npz -> {npz_path}")

    # Plots
    if not no_plots:
        _plot_rr_comparison(df, out_dir / "rr_comparison.png", session_id)
        _plot_method_agreement(df, out_dir / "method_agreement.png", session_id)
        _plot_phase_and_quality(
            phase, qmask_analysis, analysis_start, frame_rate_hz,
            out_dir / "phase_and_quality.png", session_id,
        )
        print(f"  Plots written to {out_dir}")

    # --- Write Step 6 contract CSV ---
    step6_cfg     = cfg.get("step6_contract", {})
    write_for_hr  = bool(step6_cfg.get("write_for_heart_rate", True))
    if write_for_hr:
        processed_dir = REPO_ROOT / paths["processed_dir"]
        processed_dir.mkdir(parents=True, exist_ok=True)
        contract_path = processed_dir / f"{session_id}.csv"
        if contract_path.exists() and not overwrite:
            print(
                f"  WARNING: {contract_path.name} already exists — use --overwrite to replace"
            )
        else:
            step6_cols = [
                "session_id", "window_index", "start_epoch", "end_epoch",
                "start_frame", "end_frame", "locked_bin", "locked_range_m",
                "radar_rr_bpm", "resp_peak_hz", "masimo_rr_bpm",
                "rr_error_bpm", "rr_abs_error_bpm",
                "fft_rr_bpm", "ha_rr_bpm", "stft_rr_bpm",
                "resp_confidence", "resp_valid", "quality_gated",
                "edge_locked", "edge_lock_side",
                "n_bad_frames", "bad_fraction",
                "ha_score", "ha_harmonics_used", "fft_peak_snr_db", "stft_rr_std_bpm",
            ]
            df[step6_cols].to_csv(contract_path, index=False)
            print(f"  Step 6 contract CSV -> {contract_path}")

    return summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Step 5: Extract breathing rate from locked-bin HDF5 cubes"
    )
    parser.add_argument(
        "--config", default="steps/step_5/config.yaml",
        help="Path to config file (default: steps/step_5/config.yaml)"
    )
    parser.add_argument("--session", metavar="ID",
                        help="Single session ID to process")
    parser.add_argument("--all", action="store_true",
                        help="Process all non-excluded sessions in manifest")
    parser.add_argument("--no-write", action="store_true",
                        help="Compute but do not write any output files")
    parser.add_argument("--overwrite", action="store_true",
                        help="Overwrite existing Step 6 contract CSV and results")
    args = parser.parse_args()

    if not args.session and not args.all:
        parser.print_help()
        sys.exit(0)

    cfg_path = REPO_ROOT / args.config
    if not cfg_path.exists():
        sys.exit(f"ERROR: config not found: {cfg_path}")
    cfg_text = cfg_path.read_text(encoding="utf-8")
    cfg      = yaml.safe_load(cfg_text)

    np.random.seed(int(cfg.get("seed", 42)))

    paths         = cfg["paths"]
    cubes_dir     = REPO_ROOT / paths["cubes_dir"]
    manifest_path = REPO_ROOT / paths["manifest"]
    data_raw      = REPO_ROOT / paths["raw_dir"]
    results_dir   = REPO_ROOT / paths["results_dir"]

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    commit   = _git_commit()


    input_cfg   = cfg.get("input_policy", {})
    req_conf    = str(input_cfg.get("require_step3_confidence", "medium"))
    reject_rev  = bool(input_cfg.get("reject_step3_review_required", True))
    req_qmask   = bool(input_cfg.get("require_quality_mask", True))
    skip_excl   = bool(input_cfg.get("skip_excluded_sessions", True))
    skip_no_h5  = bool(input_cfg.get("skip_missing_h5", True))

    session_ids = [args.session] if args.session else manifest["session_id"].tolist()

    failed:   list[str] = []
    skipped:  list[str] = []
    summaries: list[dict] = []

    for sid in session_ids:
        print(f"\n{SEP}\n  {sid}\n{SEP}")
        rows = manifest[manifest["session_id"] == sid]
        if rows.empty:
            print(f"  ERROR: '{sid}' not found in manifest")
            failed.append(sid)
            continue
        row = rows.iloc[0]

        # Exclusion check
        exclusion = str(row.get("exclusion_reason", "")).strip()
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
        missing_s3 = [f for f in _STEP3_REQUIRED if str(row.get(f, "")).strip() == ""]
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
        confidence = str(row.get("chest_bin_confidence", "")).strip()
        if confidence and (_CONFIDENCE_ORDER.get(confidence, -1)
                           < _CONFIDENCE_ORDER.get(req_conf, 1)):
            msg = (f"chest_bin_confidence={confidence!r} below required={req_conf!r}")
            if args.all:
                print(f"  SKIPPED ({msg})")
                skipped.append(sid)
                continue
            print(f"  ERROR: {msg}")
            failed.append(sid)
            continue

        # Review-required check
        review_req = str(row.get("chest_bin_review_required", "")).strip().lower()
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

        out_dir = results_dir / sid / "step_5"

        try:
            s = _process_session(
                sid, row, cfg, cubes_dir, data_raw, out_dir, commit,
                no_write=args.no_write,
                overwrite=args.overwrite,
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
            print(f"\n  Session summary:")
            for s in summaries:
                mae_str = f"{s['mae_bpm']:.2f}" if s.get("mae_bpm") is not None else "NaN"
                print(f"    {s['session_id']:<20} MAE={mae_str} bpm  "
                      f"valid={s['n_valid']}/{s['n_windows']}")
    if failed:
        print(f"  Failed:  {failed}")
    if skipped:
        print(f"  Skipped: {skipped}")
    print(SEP)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
