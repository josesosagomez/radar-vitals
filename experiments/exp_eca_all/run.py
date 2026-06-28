#!/usr/bin/env python3
"""exp_eca_all — ECA+AHET harmonic rejection on all sessions from manifest.

Reads every session listed in data/manifest.local.csv, runs the locked-bin
ECA+AHET pipeline, compares against Masimo PR, and prints a cross-session
summary table.

Run from repo root:
    python experiments/exp_eca_all/run.py                          # all sessions
    python experiments/exp_eca_all/run.py --session test           # single session
    python experiments/exp_eca_all/run.py --session test --no-quality-mask  # skip gating
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import h5py
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src import compare, masimo, radar_io, vitals  # noqa: E402


SEP  = "=" * 72
LINE = "-" * 72


def _plot_rr(merged: pd.DataFrame, out_path: Path, session_id: str) -> None:
    """Radar RR vs Masimo Breaths/min over time."""
    t = pd.to_datetime(merged["start_epoch"], unit="s", utc=True)
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(t, merged["masimo_rr_bpm"], "o-", label="Masimo BR (ref)", color="black")
    ax.plot(t, merged["rr_bpm"],        "s-", label="Radar RR",        color="tab:blue")
    ax.set_xlabel("time (UTC)")
    ax.set_ylabel("rate (bpm)")
    ax.set_title(f"{session_id} — Radar RR vs Masimo Breaths/min")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Quality gating (Option A)
# ---------------------------------------------------------------------------

def _load_quality_mask(h5_path: Path, pipeline_trim_frames: int) -> np.ndarray:
    """Load quality_mask from HDF5 and verify trim alignment.

    quality_mask[i] is True when frame (trim_frames + i) is healthy.
    The pipeline's window start_frame / end_frame are 0-indexed within the
    analysis window — the same index space — so quality_mask[s:e] is the
    correct slice for a window [s, e).

    Raises FileNotFoundError if the HDF5 or quality_mask dataset is missing.
    Raises ValueError if the stored trim_frames does not match the pipeline's.
    """
    if not h5_path.exists():
        raise FileNotFoundError(
            f"HDF5 not found: {h5_path}. Run scripts/add_quality_mask.py --all first."
        )
    with h5py.File(h5_path, "r") as f:
        if "quality_mask" not in f:
            raise FileNotFoundError(
                f"No /quality_mask dataset in {h5_path.name}. "
                "Run scripts/add_quality_mask.py --all first."
            )
        stored_trim = int(f["quality_mask"].attrs["trim_frames"])
        if stored_trim != pipeline_trim_frames:
            raise ValueError(
                f"trim_frames mismatch for {h5_path.name}: "
                f"HDF5 has {stored_trim}, pipeline expects {pipeline_trim_frames}. "
                "Re-run scripts/add_quality_mask.py after changing trim_frames."
            )
        return f["quality_mask"][:].astype(bool)


def _apply_quality_gating(
    window_results: list[dict],
    quality_mask: np.ndarray,
    max_bad_fraction: float,
) -> int:
    """Gate windows in-place: set hr_bpm=NaN when bad frame fraction exceeds threshold.

    Adds three keys to every window dict:
      quality_gated  : bool  — True if this window was gated out
      n_bad_frames   : int   — flagged frames inside the window
      bad_fraction   : float — n_bad_frames / window_size

    Returns the count of gated windows.
    """
    n_gated = 0
    for out in window_results:
        s, e    = out["start_frame"], out["end_frame"]
        n_bad   = int((~quality_mask[s:e]).sum())
        bad_frac = n_bad / (e - s)
        gated   = bad_frac > max_bad_fraction
        if gated:
            out["hr_bpm"] = float("nan")
            n_gated += 1
        out["quality_gated"] = gated
        out["n_bad_frames"]  = n_bad
        out["bad_fraction"]  = bad_frac
    return n_gated


# ---------------------------------------------------------------------------
# File helpers (mirrors steps/range_plot)
# ---------------------------------------------------------------------------

def _resolve_bin_paths(session_id: str, data_raw: Path) -> list[Path]:
    split0 = data_raw / f"{session_id}_0.bin"
    split1 = data_raw / f"{session_id}_1.bin"
    single = data_raw / f"{session_id}.bin"
    if split0.exists():
        return [split0, split1] if split1.exists() else [split0]
    if single.exists():
        return [single]
    raise FileNotFoundError(
        f"No .bin file for session '{session_id}' in {data_raw}"
    )


def _total_frames(bin_paths: list[Path], cfg: radar_io.ChirpConfig) -> int:
    total_bytes = sum(p.stat().st_size for p in bin_paths)
    bpf = cfg.num_chirps_per_frame * cfg.num_rx * cfg.num_adc_samples * 4
    return total_bytes // bpf


# ---------------------------------------------------------------------------
# Session diagnostics writer
# ---------------------------------------------------------------------------

def _write_session_diagnostics(
    window_results: list[dict],
    merged: pd.DataFrame,
    sess_dir: Path,
    cfg: dict,
    session_meta: dict,
    hr_metrics: dict,
    rr_metrics: dict,
) -> None:
    """Write diagnostics.json: per-window table + session-level ECA analysis.

    Saved to <sess_dir>/diagnostics.json so it can be loaded in any future
    conversation without re-running the pipeline.
    """
    k_max         = cfg["eca"]["k_max"]
    frame_rate_hz = session_meta["frame_rate_hz"]
    WARN_BPM      = 6.0

    def _safe(v) -> float | None:
        try:
            fv = float(v)
            return None if math.isnan(fv) else round(fv, 3)
        except (TypeError, ValueError):
            return None

    # ---- per-window records (merge window_results with Masimo reference) ----
    mlu = merged.set_index("window_index").to_dict(orient="index")

    windows = []
    for o in window_results:
        wi          = int(o["window_index"])
        t_s         = round(float(o["start_frame"]) / frame_rate_hz, 1)
        ref         = mlu.get(wi, {})

        f_r_hz      = o.get("f_r_hz_used")
        f_r_valid   = f_r_hz is not None and not math.isnan(float(f_r_hz))
        f_r_bpm     = round(float(f_r_hz) * 60, 2) if f_r_valid else None
        f_r_outlier = bool(o.get("f_r_outlier", False))
        eca_applied = f_r_valid and not f_r_outlier

        hr       = o.get("hr_bpm")
        hr_valid = hr is not None and not math.isnan(float(hr))
        gated    = bool(o.get("quality_gated", False))
        status   = "gated" if gated else ("nan" if not hr_valid else "ok")

        windows.append({
            "window_index":  wi,
            "t_s":           t_s,
            "f_r_bpm":       f_r_bpm,
            "f_r_outlier":   f_r_outlier,
            "eca_applied":   eca_applied,
            "hr_bpm":        _safe(hr),
            "ahet_verified": bool(o.get("ahet_verified", False)),
            "rr_bpm":        _safe(o.get("rr_bpm")),
            "quality_gated": gated,
            "bad_fraction":  round(float(o.get("bad_fraction", 0.0)), 4),
            "masimo_pr_bpm": _safe(ref.get("masimo_pr_bpm")),
            "masimo_rr_bpm": _safe(ref.get("masimo_rr_bpm")),
            "error_bpm":     _safe(ref.get("error_bpm")),
            "low_quality":   bool(ref.get("low_quality", False)),
            "status":        status,
        })

    # ---- f_r statistics (ECA-applied windows only, outliers excluded) ----
    fr_vals = [w["f_r_bpm"] for w in windows
               if w["f_r_bpm"] is not None and not w["f_r_outlier"]]
    fr_arr  = np.array(fr_vals) if fr_vals else np.array([])
    f_r_stats = {
        "n_windows":  len(fr_arr),
        "mean_bpm":   round(float(fr_arr.mean()), 2)     if len(fr_arr) else None,
        "median_bpm": round(float(np.median(fr_arr)), 2) if len(fr_arr) else None,
        "std_bpm":    round(float(fr_arr.std()), 2)      if len(fr_arr) else None,
        "min_bpm":    round(float(fr_arr.min()), 2)      if len(fr_arr) else None,
        "max_bpm":    round(float(fr_arr.max()), 2)      if len(fr_arr) else None,
    }

    # ---- Masimo HR stats ----
    mas_hr = merged["masimo_pr_bpm"].dropna()
    masimo_hr_stats = {
        "mean_bpm":   round(float(mas_hr.mean()), 2)   if len(mas_hr) else None,
        "median_bpm": round(float(mas_hr.median()), 2) if len(mas_hr) else None,
        "min_bpm":    round(float(mas_hr.min()), 2)    if len(mas_hr) else None,
        "max_bpm":    round(float(mas_hr.max()), 2)    if len(mas_hr) else None,
    }

    # ---- ECA forbidden zone: session level (using median f_r) ----
    median_fr = f_r_stats["median_bpm"]
    median_hr = masimo_hr_stats["median_bpm"]

    harmonics = []
    if median_fr is not None and median_hr is not None:
        for k in range(1, k_max + 1):
            h = round(k * median_fr, 1)
            d = round(abs(h - median_hr), 1)
            harmonics.append({
                "k": k, "k_x_fr_bpm": h,
                "delta_to_hr_bpm": d, "warning": d < WARN_BPM,
            })

    # ---- ECA forbidden zone: per-window violations ----
    violations = []
    if median_hr is not None:
        for w in windows:
            if w["f_r_bpm"] is None or w["f_r_outlier"]:
                continue
            for k in range(1, k_max + 1):
                h = k * w["f_r_bpm"]
                d = abs(h - median_hr)
                if d < WARN_BPM:
                    violations.append({
                        "window_index": w["window_index"],
                        "t_s":         w["t_s"],
                        "k":           k,
                        "f_r_bpm":     w["f_r_bpm"],
                        "k_x_fr_bpm":  round(h, 1),
                        "delta_bpm":   round(d, 1),
                        "hr_bpm":      w["hr_bpm"],
                        "status":      w["status"],
                    })

    # ---- window count breakdown ----
    n_nan_eca_ahet = sum(
        1 for w in windows
        if w["status"] == "nan" and w["eca_applied"] and not w["ahet_verified"]
    )

    diag = {
        "session_id":  session_meta["session_id"],
        "posture":     session_meta["posture"],
        "distance_cm": session_meta["distance_cm"],
        "locked_bin":  session_meta["locked_bin"],
        "config": {
            "frame_rate_hz":     frame_rate_hz,
            "window_s":          cfg["processing"]["window_s"],
            "hop_s":             cfg["processing"]["hop_s"],
            "k_max":             k_max,
            "ahet_deviation_hz": cfg["eca"]["ahet_deviation_hz"],
            "min_pi":            cfg["compare"]["min_pi"],
        },
        "window_counts": {
            "total":                         len(windows),
            "finite_hr":                     sum(1 for w in windows if w["status"] == "ok"),
            "nan_hr":                        sum(1 for w in windows if w["status"] == "nan"),
            "quality_gated":                 sum(1 for w in windows if w["status"] == "gated"),
            "eca_skipped_fr_outlier":        sum(1 for w in windows if w["f_r_outlier"]),
            "ahet_verified":                 sum(1 for w in windows if w["ahet_verified"]),
            "nan_eca_applied_ahet_rejected": n_nan_eca_ahet,
        },
        "hr_metrics":      hr_metrics,
        "rr_metrics":      rr_metrics,
        "f_r_stats":       f_r_stats,
        "masimo_hr_stats": masimo_hr_stats,
        "eca_forbidden_zone": {
            "warning_threshold_bpm":   WARN_BPM,
            "median_f_r_bpm":          median_fr,
            "median_masimo_hr_bpm":    median_hr,
            "session_level_harmonics": harmonics,
            "per_window_violations":   violations,
        },
        "windows": windows,
    }

    out_path = sess_dir / "diagnostics.json"
    out_path.write_text(json.dumps(diag, indent=2, default=str))
    print(f"  Diagnostics -> {out_path.name}")


# ---------------------------------------------------------------------------
# Per-window diagnostics
# ---------------------------------------------------------------------------

def _print_window_diagnostics(window_results: list[dict], frame_rate_hz: float) -> None:
    """Print a compact per-window table: f_r, ECA status, HR, outcome."""
    print(f"\n  {'Win':>3}  {'t(s)':>6}  {'f_r(bpm)':>9}  {'ECA':>5}  {'HR(bpm)':>8}  {'AHET':>4}  Status")
    print(f"  {'---':>3}  {'------':>6}  {'---------':>9}  {'-----':>5}  {'--------':>8}  {'----':>4}  ------")
    for o in window_results:
        t0_s = o["start_frame"] / frame_rate_hz

        f_r_hz = o.get("f_r_hz_used")
        f_r_valid = f_r_hz is not None and not math.isnan(float(f_r_hz))
        f_r_str = f"{float(f_r_hz) * 60:9.1f}" if f_r_valid else "       --"

        f_r_outlier = bool(o.get("f_r_outlier", False))
        eca_str = "skip" if f_r_outlier else ("ok" if f_r_valid else "--")

        hr = o.get("hr_bpm")
        hr_valid = hr is not None and not math.isnan(float(hr))
        hr_str = f"{float(hr):8.1f}" if hr_valid else "     NaN"

        ahet_str = "yes" if o.get("ahet_verified") else "--"

        gated = bool(o.get("quality_gated", False))
        status = "gated" if gated else ("nan" if not hr_valid else "ok")

        print(f"  {o['window_index']:>3}  {t0_s:>6.1f}  {f_r_str}  {eca_str:>5}  {hr_str}  {ahet_str:>4}  {status}")


# ---------------------------------------------------------------------------
# Per-session pipeline
# ---------------------------------------------------------------------------

def _run_session(row: pd.Series, cfg: dict, data_raw: Path, run_dir: Path,
                 h5_dir: Path, use_quality_mask: bool = True) -> dict:
    session_id  = str(row["session_id"])
    locked_bin  = int(row["locked_bin"])
    t0_base     = int(row["radar_start_epoch_seconds"])
    posture     = str(row["posture"])
    distance_cm = int(row["distance_cm"])

    # Parse stationary_intervals → trim_s, end_s
    interval     = str(row["stationary_intervals"])
    trim_s, end_s = (int(x) for x in interval.split("-"))

    r              = cfg["radar"]
    frame_rate_hz  = float(r["frame_rate_hz"])
    trim_frames    = int(trim_s * frame_rate_hz)
    end_frames     = int(end_s  * frame_rate_hz)
    t0             = t0_base + trim_s   # UTC epoch of first analysis frame

    window_s   = cfg["processing"]["window_s"]
    hop_s      = cfg["processing"]["hop_s"]
    window_frames = int(window_s * frame_rate_hz)
    hop_frames    = int(hop_s    * frame_rate_hz)
    k_max         = cfg["eca"]["k_max"]
    ahet_dev      = cfg["eca"]["ahet_deviation_hz"]
    min_pi        = cfg["compare"]["min_pi"]

    bin_paths = _resolve_bin_paths(session_id, data_raw)
    iq_swap   = str(row.get("iq_swap", "False")).strip().lower() == "true"

    chirp_cfg = radar_io.ChirpConfig(
        num_adc_samples      = r["num_adc_samples"],
        num_rx               = r["num_rx"],
        num_tx               = r["num_tx"],
        num_chirps_per_frame = r["num_chirps_per_frame"],
        num_frames           = 1,           # placeholder; set below
        frame_rate_hz        = frame_rate_hz,
        range_resolution_m   = r["range_resolution_m"],
        iq_swap              = iq_swap,
    )
    num_frames = _total_frames(bin_paths, chirp_cfg)
    chirp_cfg = radar_io.ChirpConfig(
        num_adc_samples      = r["num_adc_samples"],
        num_rx               = r["num_rx"],
        num_tx               = r["num_tx"],
        num_chirps_per_frame = r["num_chirps_per_frame"],
        num_frames           = num_frames,
        frame_rate_hz        = frame_rate_hz,
        range_resolution_m   = r["range_resolution_m"],
        iq_swap              = iq_swap,
    )

    print(f"  Bin file(s): {[p.name for p in bin_paths]}")
    print(f"  Total frames: {num_frames}  |  trim: {trim_s}s  |  analysis: {trim_s}–{end_s}s")
    print(f"  Locked bin: {locked_bin}  ({locked_bin * r['range_resolution_m']:.3f} m)")

    # Load cube → range profiles
    bin_arg  = bin_paths[0] if len(bin_paths) == 1 else bin_paths
    cube     = radar_io.read_adc_bin(bin_arg, chirp_cfg, trim_frames=trim_frames)
    profiles = radar_io.range_profile(cube)
    del cube

    # Slice to stationary analysis window [trim_frames : end_frames]
    analysis_profiles = profiles[trim_frames:end_frames]
    del profiles

    raxis = radar_io.range_axis_m(r["num_adc_samples"], r["range_resolution_m"])

    params = vitals.VitalsParams(
        fs_hz       = frame_rate_hz,
        gate_min_m  = 0.5,
        gate_max_m  = 2.5,
    )

    window_results = vitals.run_pipeline_locked(
        analysis_profiles, raxis, params,
        window_frames, hop_frames,
        locked_bin   = locked_bin,
        k_max        = k_max,
        ahet_deviation_hz = ahet_dev,
    )

    # Attach epochs
    for out in window_results:
        out["start_epoch"] = t0 + out["start_frame"] / frame_rate_hz
        out["end_epoch"]   = t0 + out["end_frame"]   / frame_rate_hz

    # Quality gating (Option A)
    qg_cfg   = cfg.get("quality_gating", {})
    qg_on    = use_quality_mask
    n_gated  = 0
    if qg_on:
        max_bad  = float(qg_cfg["max_bad_fraction"])
        h5_path  = h5_dir / f"{session_id}.h5"
        qmask    = _load_quality_mask(h5_path, trim_frames)
        n_gated  = _apply_quality_gating(window_results, qmask, max_bad)
        print(
            f"  Quality gating: {n_gated} / {len(window_results)} windows gated "
            f"({100.0 * n_gated / max(len(window_results), 1):.1f}%)  "
            f"|  max_bad_fraction={max_bad}"
        )

    _print_window_diagnostics(window_results, frame_rate_hz)

    # Build radar DataFrame
    radar_df = pd.DataFrame([{
        "start_epoch":   o["start_epoch"],
        "end_epoch":     o["end_epoch"],
        "hr_bpm":        o["hr_bpm"],
        "window_index":  o["window_index"],
        "rr_bpm":        o.get("rr_bpm"),
        "f_r_hz_used":   o.get("f_r_hz_used"),
        "f_r_outlier":   o.get("f_r_outlier", False),
        "ahet_verified": o.get("ahet_verified", False),
        "quality_gated": o.get("quality_gated", False),
        "bad_fraction":  o.get("bad_fraction", 0.0),
    } for o in window_results])

    # Load Masimo and compare
    mas_path = data_raw / f"{session_id}_masimo.csv"
    mas_df   = masimo.load_masimo(mas_path)
    merged   = compare.compare(radar_df, mas_df, min_pi=min_pi)

    # Add Masimo RR per window (window-mean Breaths/min, no PI gating needed)
    merged["masimo_rr_bpm"] = [
        masimo.reference_br(mas_df, r["start_epoch"], r["end_epoch"])
        for _, r in merged.iterrows()
    ]

    # Save per-session CSV
    sess_dir = run_dir / session_id
    sess_dir.mkdir(parents=True, exist_ok=True)
    csv_path = sess_dir / "comparison.csv"
    merged.to_csv(csv_path, index=False)

    # Plots
    hr_plot_path = sess_dir / "hr_comparison.png"
    compare.overlay_plot(
        merged, hr_plot_path,
        title=f"{session_id} — Radar HR vs Masimo PR",
    )
    rr_plot_path = sess_dir / "rr_comparison.png"
    _plot_rr(merged, rr_plot_path, session_id)
    print(f"  Plots: {hr_plot_path.name}  |  {rr_plot_path.name}")

    # Compute HR metrics
    fin = merged.dropna(subset=["error_bpm"])
    n_total  = len(merged)
    n_finite = len(fin)
    n_nan    = n_total - n_finite

    if n_finite > 0:
        mae   = float(fin["abs_error_bpm"].mean())
        rmse  = float(math.sqrt((fin["error_bpm"] ** 2).mean()))
        bias  = float(fin["error_bpm"].mean())
        n_ahet = int(fin["ahet_verified"].sum()) if "ahet_verified" in fin else 0
    else:
        mae = rmse = bias = float("nan")
        n_ahet = 0

    print(f"  Windows: {n_total} total, {n_finite} finite, {n_nan} NaN  "
          f"(of which {n_gated} quality-gated)")
    if not math.isnan(mae):
        print(f"  HR:  MAE={mae:.2f}  RMSE={rmse:.2f}  bias={bias:+.2f} bpm")
    else:
        print("  HR:  MAE=NaN (no finite windows)")

    # Compute RR metrics
    rr_fin      = merged.dropna(subset=["rr_bpm", "masimo_rr_bpm"])
    n_finite_rr = len(rr_fin)
    n_nan_rr    = n_total - n_finite_rr
    if n_finite_rr > 0:
        rr_err  = rr_fin["rr_bpm"] - rr_fin["masimo_rr_bpm"]
        rr_mae  = float(rr_err.abs().mean())
        rr_rmse = float(math.sqrt((rr_err ** 2).mean()))
        rr_bias = float(rr_err.mean())
    else:
        rr_mae = rr_rmse = rr_bias = float("nan")

    if not math.isnan(rr_mae):
        print(f"  RR:  MAE={rr_mae:.2f}  RMSE={rr_rmse:.2f}  bias={rr_bias:+.2f} bpm"
              f"  ({n_finite_rr} finite, {n_nan_rr} NaN)")
    else:
        print(f"  RR:  MAE=NaN ({n_nan_rr} NaN windows)")

    _write_session_diagnostics(
        window_results, merged, sess_dir, cfg,
        session_meta={
            "session_id":    session_id,
            "posture":       posture,
            "distance_cm":   distance_cm,
            "locked_bin":    locked_bin,
            "frame_rate_hz": frame_rate_hz,
            "t0":            t0,
        },
        hr_metrics={
            "mae": mae, "rmse": rmse, "bias": bias,
            "n_finite": n_finite, "n_nan": n_nan, "n_ahet": n_ahet,
        },
        rr_metrics={
            "mae": rr_mae, "rmse": rr_rmse, "bias": rr_bias,
            "n_finite": n_finite_rr, "n_nan": n_nan_rr,
        },
    )

    return {
        "session_id":  session_id,
        "posture":     posture,
        "distance_cm": distance_cm,
        "locked_bin":  locked_bin,
        "n_total":     n_total,
        "n_finite":    n_finite,
        "n_nan":       n_nan,
        "n_gated":     n_gated,
        "mae":         mae,
        "rmse":        rmse,
        "bias":        bias,
        "n_ahet":      n_ahet,
        "n_finite_rr": n_finite_rr,
        "n_nan_rr":    n_nan_rr,
        "rr_mae":      rr_mae,
        "rr_rmse":     rr_rmse,
        "rr_bias":     rr_bias,
        "csv":         str(csv_path),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(config_path: Path) -> None:
    ap = argparse.ArgumentParser(description="exp_eca_all ECA+AHET pipeline")
    ap.add_argument("--session", metavar="ID",
                    help="Run a single session ID from the manifest instead of all")
    ap.add_argument("--no-quality-mask", dest="quality_mask",
                    action="store_false", default=True,
                    help="Disable quality-mask gating (default: on)")
    args = ap.parse_args()

    cfg = yaml.safe_load(config_path.read_text())
    np.random.seed(cfg["seed"])

    manifest_path = REPO_ROOT / cfg["manifest"]
    manifest      = pd.read_csv(manifest_path)
    data_raw      = REPO_ROOT / "data" / "raw"
    qg_cfg        = cfg.get("quality_gating", {})
    h5_dir        = REPO_ROOT / qg_cfg.get("h5_dir", "data/processed/time_domain_cubes")

    if args.session:
        mask = manifest["session_id"] == args.session
        if not mask.any():
            available = manifest["session_id"].tolist()
            raise SystemExit(
                f"Session '{args.session}' not in manifest.\nAvailable: {available}"
            )
        manifest = manifest[mask]

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    run_dir   = REPO_ROOT / cfg["results_dir"] / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    n_sessions = len(manifest)
    print(SEP)
    print(f"  exp_eca_all  |  ECA+AHET on {n_sessions} session(s)  |  {timestamp}")
    print(SEP)

    session_results: list[dict] = []
    failed: list[tuple[str, str]] = []

    for _, row in manifest.iterrows():
        sid = str(row["session_id"])
        print(f"\n{LINE}")
        print(f"  SESSION: {sid}  |  {row['posture']}  |  {row['distance_cm']} cm")
        print(LINE)
        try:
            result = _run_session(row, cfg, data_raw, run_dir, h5_dir,
                                  use_quality_mask=args.quality_mask)
            session_results.append(result)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            failed.append((sid, str(exc)))

    # Save summary JSON
    summary = {
        "timestamp":   timestamp,
        "config":      cfg,
        "sessions":    session_results,
        "failed":      [{"session_id": s, "error": e} for s, e in failed],
    }
    summary_path = run_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str))

    # Print cross-session summary table
    print(f"\n{SEP}")
    print("  CROSS-SESSION SUMMARY — ECA+AHET (k_max=6, window=20 s)")
    print(SEP)
    print(f"  {'Session':<10}  {'Posture':<20}  {'Dist':>5}  {'Bin':>4}  "
          f"{'N':>4}  {'NaN':>4}  {'Gated':>5}  {'MAE':>7}  {'RMSE':>7}  {'Bias':>7}")
    print(f"  {'-'*10}  {'-'*20}  {'-'*5}  {'-'*4}  "
          f"{'-'*4}  {'-'*4}  {'-'*5}  {'-'*7}  {'-'*7}  {'-'*7}")

    maes = []
    for r in session_results:
        mae_s   = f"{r['mae']:7.2f}"  if not math.isnan(r['mae'])  else "    NaN"
        rmse_s  = f"{r['rmse']:7.2f}" if not math.isnan(r['rmse']) else "    NaN"
        bias_s  = f"{r['bias']:+7.2f}" if not math.isnan(r['bias']) else "    NaN"
        gated_s = f"{r.get('n_gated', 0):>5}"
        print(f"  {r['session_id']:<10}  {r['posture']:<20}  {r['distance_cm']:>5}  "
              f"{r['locked_bin']:>4}  {r['n_finite']:>4}  {r['n_nan']:>4}  {gated_s}  "
              f"{mae_s}  {rmse_s}  {bias_s}")
        if not math.isnan(r["mae"]):
            maes.append(r["mae"])

    if maes:
        print(f"\n  Overall mean MAE ({len(maes)} sessions): {sum(maes)/len(maes):.2f} bpm")

    # RR summary table
    print(f"\n{SEP}")
    print("  BREATHING RATE SUMMARY — ECA+AHET (k_max=6, window=20 s)")
    print(SEP)
    print(f"  {'Session':<10}  {'Posture':<20}  {'Dist':>5}  {'Bin':>4}  "
          f"{'N':>4}  {'NaN':>4}  {'MAE':>7}  {'RMSE':>7}  {'Bias':>7}")
    print(f"  {'-'*10}  {'-'*20}  {'-'*5}  {'-'*4}  "
          f"{'-'*4}  {'-'*4}  {'-'*7}  {'-'*7}  {'-'*7}")

    rr_maes = []
    for r in session_results:
        rr_mae_s  = f"{r['rr_mae']:7.2f}"  if not math.isnan(r['rr_mae'])  else "    NaN"
        rr_rmse_s = f"{r['rr_rmse']:7.2f}" if not math.isnan(r['rr_rmse']) else "    NaN"
        rr_bias_s = f"{r['rr_bias']:+7.2f}" if not math.isnan(r['rr_bias']) else "    NaN"
        print(f"  {r['session_id']:<10}  {r['posture']:<20}  {r['distance_cm']:>5}  "
              f"{r['locked_bin']:>4}  {r['n_finite_rr']:>4}  {r['n_nan_rr']:>4}  "
              f"{rr_mae_s}  {rr_rmse_s}  {rr_bias_s}")
        if not math.isnan(r["rr_mae"]):
            rr_maes.append(r["rr_mae"])

    if rr_maes:
        print(f"\n  Overall mean RR MAE ({len(rr_maes)} sessions): {sum(rr_maes)/len(rr_maes):.2f} bpm")

    if failed:
        print(f"\n  FAILED ({len(failed)}): {[s for s, _ in failed]}")

    print(f"\n  Results -> {run_dir}")
    print(SEP)


if __name__ == "__main__":
    main(Path(__file__).resolve().parent / "config.yaml")
