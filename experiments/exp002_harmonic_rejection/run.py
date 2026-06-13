#!/usr/bin/env python3
"""exp002 — ECA + AHET harmonic rejection runner.

Same capture as exp001; improved HR estimator using respiration subspace cancellation
(Extensive Cancellation Algorithm) and second-harmonic consistency check (AHET).
Reference: arXiv:2503.07062 (Tang et al., Zhejiang/ASU, 2025).

Run from the repo root:
    python experiments/exp002_harmonic_rejection/run.py
"""
from __future__ import annotations

import sys
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src import masimo, radar_io, vitals, compare  # noqa: E402


def main(config_path: Path) -> None:
    cfg = yaml.safe_load(config_path.read_text())
    np.random.seed(cfg["seed"])

    run_dir = (
        REPO_ROOT / "results" / "exp002_harmonic_rejection" / time.strftime("%Y%m%d_%H%M%S")
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    c = cfg["chirp"]
    d = cfg["data"]
    v = cfg["vitals"]

    # --- Parse LogFile for UTC capture timestamps ---
    log_info = radar_io.parse_logfile(REPO_ROOT / d["logfile_csv"], d["utc_offset_hours"])
    start_epoch_utc = log_info["start_epoch_utc"]
    end_epoch_utc   = log_info["end_epoch_utc"]
    duration_s      = log_info["duration_s"]

    chirp_cfg = radar_io.ChirpConfig(
        num_adc_samples=c["num_adc_samples"], num_rx=c["num_rx"], num_tx=c["num_tx"],
        num_chirps_per_frame=c["num_chirps_per_frame"], num_frames=c["num_frames"],
        frame_rate_hz=c["frame_rate_hz"], range_resolution_m=c["range_resolution_m"],
    )
    radar_io.infer_num_frames(REPO_ROOT / d["radar_bin"], chirp_cfg)

    trim_start_s = d["trim_start_s"]
    trim_frames  = int(trim_start_s * c["frame_rate_hz"])
    t0           = start_epoch_utc + trim_start_s

    print(f"Capture:  {start_epoch_utc} -> {end_epoch_utc} ({duration_s} s)")
    print(f"Trim:     first {trim_start_s} s skipped ({trim_frames} frames)")
    print(f"Analysis: {t0} -> {end_epoch_utc} ({duration_s - trim_start_s:.0f} s)")

    # 1) Radar cube -> range profile
    cube     = radar_io.read_adc_bin(REPO_ROOT / d["radar_bin"], chirp_cfg,
                                     trim_frames=trim_frames)
    profiles = radar_io.range_profile(cube)
    raxis    = radar_io.range_axis_m(c["num_adc_samples"], c["range_resolution_m"])

    # 2) Sliding-window HR estimates with ECA + AHET
    params = vitals.VitalsParams(
        fs_hz=c["frame_rate_hz"],
        gate_min_m=v["gate_min_m"], gate_max_m=v["gate_max_m"],
        heart_band_hz=tuple(v["heart_band_hz"]),
        resp_band_hz=tuple(v["resp_band_hz"]),
    )
    window_frames  = int(v["window_s"] * c["frame_rate_hz"])
    hop_frames     = int(v["hop_s"]    * c["frame_rate_hz"])
    cfg_locked_bin = v.get("locked_bin", None)
    window_results = vitals.run_pipeline_locked(
        profiles[trim_frames:], raxis, params, window_frames, hop_frames,
        locked_bin=cfg_locked_bin,
    )

    locked_bin     = window_results[0]["chosen_bin"]     if window_results else None
    locked_range_m = window_results[0]["chosen_range_m"] if window_results else None
    print(f"Locked bin: {locked_bin}  ({locked_range_m:.3f} m)")

    # 3) Build radar DataFrame (includes ECA+AHET diagnostic columns)
    rows = []
    for out in window_results:
        s = out["window_start_frame"]
        e = out["window_end_frame"]
        rows.append({
            "start_epoch":       t0 + s / c["frame_rate_hz"],
            "end_epoch":         t0 + e / c["frame_rate_hz"],
            "hr_bpm":            out["hr_bpm"],
            "rr_bpm":            out["rr_bpm"],
            "chosen_range_m":    out["chosen_range_m"],
            "ahet_verified":     out.get("ahet_verified", False),
            "harmonic_suspect":  out.get("harmonic_suspect", False),
            "f_r_hz_used":       out.get("f_r_hz_used",  float("nan")),
            "f_r_raw_hz":        out.get("f_r_raw_hz",   float("nan")),
            "f_r_outlier":       out.get("f_r_outlier",  False),
        })
    radar_df = pd.DataFrame(rows)

    # 4) Compare to Masimo
    mas    = masimo.load_masimo(REPO_ROOT / d["masimo_csv"])
    merged = compare.compare(radar_df, mas, min_pi=cfg["compare"]["min_pi"])

    # 5) Metrics — all windows, then AHET-verified only
    m_all = compare.metrics(merged)

    merged_verified = merged[merged["ahet_verified"] == True].copy()  # noqa: E712
    if len(merged_verified) > 0 and not merged_verified["low_quality"].all():
        m_verified = compare.metrics(merged_verified)
    else:
        m_verified = None

    # 6) Per-window summary table
    _SEP = "-" * 116
    print(f"\n{_SEP}")
    print(
        f"{'win':>3}  {'t_start':>10}  {'radar_HR':>8}  {'masimo_PR':>9}  "
        f"{'error':>6}  {'f_r_bpm':>7}  {'outlier':>7}  {'AHET':>6}  {'suspect':>7}"
    )
    print(_SEP)
    for i, row in merged.iterrows():
        radar_hr   = row["hr_bpm"]
        hr_str     = f"{radar_hr:8.1f}" if not pd.isna(radar_hr) else "     NaN"
        err_str    = f"{row['error_bpm']:+6.1f}" if not pd.isna(row.get("error_bpm")) else "   NaN"
        f_r_raw_hz = row.get("f_r_raw_hz", float("nan"))
        f_r_str    = f"{f_r_raw_hz * 60:7.1f}" if not pd.isna(f_r_raw_hz) else "    NaN"
        outl_str   = "    YES" if row.get("f_r_outlier") else "     no"
        ahet_str   = "  YES" if row.get("ahet_verified") else "   no"
        susp_str   = "    YES" if row.get("harmonic_suspect") else "     no"
        print(
            f"{i:>3}  {row['start_epoch']:>10.0f}  {hr_str}  "
            f"{row['masimo_pr_bpm']:>9.1f}  {err_str}  {f_r_str}  "
            f"{outl_str}  {ahet_str}  {susp_str}"
        )
    print(_SEP)

    # 7) Summary statistics
    n_nan      = int(radar_df["hr_bpm"].isna().sum())
    n_verified = int(radar_df["ahet_verified"].sum())
    n_suspect  = int(radar_df["harmonic_suspect"].sum())
    n_outlier  = int(radar_df["f_r_outlier"].sum())
    print(f"\nTotal windows: {len(radar_df)}")
    print(f"  AHET verified:       {n_verified}")
    print(f"  Harmonic suspect:    {n_suspect}")
    print(f"  f_r outlier (gated): {n_outlier}  (ECA skipped, fell back to bandpass+argmax)")
    print(f"  NaN (no estimate):   {n_nan}  (not fabricated per CLAUDE.md s.4)")

    print("\n--- Metrics: all non-NaN windows ---")
    print(json.dumps(m_all, indent=2))
    if m_verified is not None:
        print("\n--- Metrics: AHET-verified windows only ---")
        print(json.dumps(m_verified, indent=2))

    # 8) Save outputs
    merged.to_csv(run_dir / "comparison.csv", index=False)
    (run_dir / "metrics_all.json").write_text(json.dumps(m_all, indent=2))
    if m_verified is not None:
        (run_dir / "metrics_verified.json").write_text(json.dumps(m_verified, indent=2))
    (run_dir / "config_used.yaml").write_text(config_path.read_text())
    compare.overlay_plot(
        merged, run_dir / "overlay.png", title="exp002 ECA+AHET radar vs Masimo"
    )
    print(f"\nResults -> {run_dir}")

    if cfg["wandb"]["enabled"]:
        import wandb
        wandb.init(project=cfg["wandb"]["project"], config=cfg)
        wandb.log({**m_all, "n_verified": n_verified, "n_nan": n_nan})
        if m_verified:
            wandb.log({"verified_" + k: v for k, v in m_verified.items()})
        wandb.log({"overlay": wandb.Image(str(run_dir / "overlay.png"))})
        wandb.finish()


if __name__ == "__main__":
    main(Path(__file__).resolve().parent / "config.yaml")
