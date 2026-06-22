#!/usr/bin/env python3
"""exp_eca_all — ECA+AHET harmonic rejection on all sessions from manifest.

Reads every session listed in data/manifest.local.csv, runs the locked-bin
ECA+AHET pipeline, compares against Masimo PR, and prints a cross-session
summary table.

Run from repo root:
    python experiments/exp_eca_all/run.py
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src import compare, masimo, radar_io, vitals  # noqa: E402


SEP  = "=" * 72
LINE = "-" * 72


# ---------------------------------------------------------------------------
# File helpers (mirrors exp000_range_plot)
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
# Per-session pipeline
# ---------------------------------------------------------------------------

def _run_session(row: pd.Series, cfg: dict, data_raw: Path, run_dir: Path) -> dict:
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

    chirp_cfg = radar_io.ChirpConfig(
        num_adc_samples      = r["num_adc_samples"],
        num_rx               = r["num_rx"],
        num_tx               = r["num_tx"],
        num_chirps_per_frame = r["num_chirps_per_frame"],
        num_frames           = 1,           # placeholder; set below
        frame_rate_hz        = frame_rate_hz,
        range_resolution_m   = r["range_resolution_m"],
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

    # Build radar DataFrame
    radar_df = pd.DataFrame([{
        "start_epoch": o["start_epoch"],
        "end_epoch":   o["end_epoch"],
        "hr_bpm":      o["hr_bpm"],
        "window_index": o["window_index"],
        "rr_bpm":      o.get("rr_bpm"),
        "ahet_verified": o.get("ahet_verified", False),
    } for o in window_results])

    # Load Masimo and compare
    mas_path = data_raw / f"{session_id}_masimo.csv"
    mas_df   = masimo.load_masimo(mas_path)
    merged   = compare.compare(radar_df, mas_df, min_pi=min_pi)

    # Save per-session CSV
    sess_dir = run_dir / session_id
    sess_dir.mkdir(parents=True, exist_ok=True)
    csv_path = sess_dir / "comparison.csv"
    merged.to_csv(csv_path, index=False)

    # Compute metrics
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

    print(f"  Windows: {n_total} total, {n_finite} finite, {n_nan} NaN")
    if not math.isnan(mae):
        print(f"  MAE={mae:.2f}  RMSE={rmse:.2f}  bias={bias:+.2f} bpm")
    else:
        print("  MAE=NaN (no finite windows)")

    return {
        "session_id":  session_id,
        "posture":     posture,
        "distance_cm": distance_cm,
        "locked_bin":  locked_bin,
        "n_total":     n_total,
        "n_finite":    n_finite,
        "n_nan":       n_nan,
        "mae":         mae,
        "rmse":        rmse,
        "bias":        bias,
        "n_ahet":      n_ahet,
        "csv":         str(csv_path),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(config_path: Path) -> None:
    cfg = yaml.safe_load(config_path.read_text())
    np.random.seed(cfg["seed"])

    manifest_path = REPO_ROOT / cfg["manifest"]
    manifest      = pd.read_csv(manifest_path)
    data_raw      = REPO_ROOT / "data" / "raw"

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    run_dir   = REPO_ROOT / cfg["results_dir"] / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    print(SEP)
    print(f"  exp_eca_all  |  ECA+AHET on {len(manifest)} sessions  |  {timestamp}")
    print(SEP)

    session_results: list[dict] = []
    failed: list[tuple[str, str]] = []

    for _, row in manifest.iterrows():
        sid = str(row["session_id"])
        print(f"\n{LINE}")
        print(f"  SESSION: {sid}  |  {row['posture']}  |  {row['distance_cm']} cm")
        print(LINE)
        try:
            result = _run_session(row, cfg, data_raw, run_dir)
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
          f"{'N':>4}  {'NaN':>4}  {'MAE':>7}  {'RMSE':>7}  {'Bias':>7}")
    print(f"  {'-'*10}  {'-'*20}  {'-'*5}  {'-'*4}  "
          f"{'-'*4}  {'-'*4}  {'-'*7}  {'-'*7}  {'-'*7}")

    maes = []
    for r in session_results:
        mae_s  = f"{r['mae']:7.2f}" if not math.isnan(r['mae']) else "    NaN"
        rmse_s = f"{r['rmse']:7.2f}" if not math.isnan(r['rmse']) else "    NaN"
        bias_s = f"{r['bias']:+7.2f}" if not math.isnan(r['bias']) else "    NaN"
        print(f"  {r['session_id']:<10}  {r['posture']:<20}  {r['distance_cm']:>5}  "
              f"{r['locked_bin']:>4}  {r['n_finite']:>4}  {r['n_nan']:>4}  "
              f"{mae_s}  {rmse_s}  {bias_s}")
        if not math.isnan(r["mae"]):
            maes.append(r["mae"])

    if maes:
        print(f"\n  Overall mean MAE ({len(maes)} sessions): {sum(maes)/len(maes):.2f} bpm")

    if failed:
        print(f"\n  FAILED ({len(failed)}): {[s for s, _ in failed]}")

    print(f"\n  Results -> {run_dir}")
    print(SEP)


if __name__ == "__main__":
    main(Path(__file__).resolve().parent / "config.yaml")
