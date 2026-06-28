#!/usr/bin/env python3
"""exp002 — ECA + AHET harmonic rejection runner.

Same capture as exp001; improved HR estimator using respiration subspace cancellation
(Extensive Cancellation Algorithm) and second-harmonic consistency check (AHET).
Reference: arXiv:2503.07062 (Tang et al., Zhejiang/ASU, 2025).

Run from the repo root:
    python experiments/exp002_harmonic_rejection/run.py
"""
from __future__ import annotations

import argparse
import sys
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src import compare, intermediates, masimo, radar_io, vitals  # noqa: E402


def _resolve_session_overrides(session_id: str) -> dict:
    """Look up session_id in the manifest and return data-path + param overrides."""
    manifest_path = REPO_ROOT / "data" / "manifest.local.csv"
    manifest = pd.read_csv(manifest_path)
    rows = manifest[manifest["session_id"] == session_id]
    if rows.empty:
        available = manifest["session_id"].tolist()
        raise SystemExit(
            f"Session '{session_id}' not in manifest.\nAvailable: {available}"
        )
    row = rows.iloc[0]
    data_raw = REPO_ROOT / "data" / "raw"

    # Bin path(s) — handle single file or split (_0 / _1)
    split0 = data_raw / f"{session_id}_0.bin"
    single = data_raw / f"{session_id}.bin"
    if split0.exists():
        split1 = data_raw / f"{session_id}_1.bin"
        bin_paths = [split0, split1] if split1.exists() else [split0]
    elif single.exists():
        bin_paths = [single]
    else:
        raise FileNotFoundError(
            f"No .bin found for session '{session_id}' in {data_raw}"
        )

    # trim_start_s: first number from stationary_intervals (e.g. "30-285" → 30)
    intervals = str(row.get("stationary_intervals") or "")
    try:
        trim_start_s = int(intervals.split("-")[0])
    except (ValueError, IndexError):
        trim_start_s = None  # caller falls back to config value

    # locked_bin: may be blank or NaN in the manifest
    try:
        locked_bin = int(row["locked_bin"])
    except (ValueError, TypeError, KeyError):
        locked_bin = None  # caller falls back to config value

    iq_swap = str(row.get("iq_swap", "False")).strip().lower() == "true"

    return {
        "bin_paths":    bin_paths,
        "logfile_csv":  data_raw / f"{session_id}_LogFile.csv",
        "masimo_csv":   data_raw / f"{session_id}_masimo.csv",
        "trim_start_s": trim_start_s,
        "locked_bin":   locked_bin,
        "iq_swap":      iq_swap,
    }


def main(config_path: Path) -> None:
    cfg = yaml.safe_load(config_path.read_text())
    np.random.seed(cfg["seed"])

    ap = argparse.ArgumentParser(description="exp002 ECA+AHET harmonic rejection")
    ap.add_argument("--session", metavar="ID",
                    help="Session ID from manifest (overrides data: block in config.yaml)")
    args = ap.parse_args()

    run_dir = (
        REPO_ROOT / "results" / "exp002_harmonic_rejection" / time.strftime("%Y%m%d_%H%M%S")
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    c = cfg["chirp"]
    d = cfg["data"]
    v = cfg["vitals"]

    # ── resolve data paths (manifest override or config default) ─────────────
    if args.session:
        ov = _resolve_session_overrides(args.session)
        bin_paths    = ov["bin_paths"]
        logfile_path = ov["logfile_csv"]
        masimo_path  = ov["masimo_csv"]
        iq_swap      = ov["iq_swap"]
        if ov["trim_start_s"] is not None:
            d["trim_start_s"] = ov["trim_start_s"]
        if ov["locked_bin"] is not None:
            v["locked_bin"] = ov["locked_bin"]
        print(f"Session: {args.session}  (manifest override)  iq_swap={iq_swap}")
    else:
        bin_paths    = [REPO_ROOT / d["radar_bin"]]
        logfile_path = REPO_ROOT / d["logfile_csv"]
        masimo_path  = REPO_ROOT / d["masimo_csv"]
        iq_swap      = False  # all exp001-exp010 are Studio captures (SampleSwap=0)

    bin_arg = bin_paths[0] if len(bin_paths) == 1 else bin_paths

    # --- Parse LogFile for UTC capture timestamps ---
    log_info = radar_io.parse_logfile(logfile_path, d["utc_offset_hours"])
    start_epoch_utc = log_info["start_epoch_utc"]
    end_epoch_utc   = log_info["end_epoch_utc"]
    duration_s      = log_info["duration_s"]

    chirp_cfg = radar_io.ChirpConfig(
        num_adc_samples=c["num_adc_samples"], num_rx=c["num_rx"], num_tx=c["num_tx"],
        num_chirps_per_frame=c["num_chirps_per_frame"], num_frames=c["num_frames"],
        frame_rate_hz=c["frame_rate_hz"], range_resolution_m=c["range_resolution_m"],
        iq_swap=iq_swap,
    )
    if len(bin_paths) == 1:
        radar_io.infer_num_frames(bin_paths[0], chirp_cfg)

    trim_start_s = d["trim_start_s"]
    trim_frames  = int(trim_start_s * c["frame_rate_hz"])
    t0           = start_epoch_utc + trim_start_s

    print(f"Capture:  {start_epoch_utc} -> {end_epoch_utc} ({duration_s} s)")
    print(f"Trim:     first {trim_start_s} s skipped ({trim_frames} frames)")
    print(f"Analysis: {t0} -> {end_epoch_utc} ({duration_s - trim_start_s:.0f} s)")

    # 1) Radar cube -> range profile
    cube     = radar_io.read_adc_bin(bin_arg, chirp_cfg,
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
    eca_k_max = cfg["eca"]["k_max"]
    eca_ahet_dev = cfg["eca"]["ahet_deviation_hz"]
    analysis_profiles = profiles[trim_frames:]
    window_results = vitals.run_pipeline_locked(
        analysis_profiles, raxis, params, window_frames, hop_frames,
        locked_bin=cfg_locked_bin, k_max=eca_k_max, ahet_deviation_hz=eca_ahet_dev,
    )

    locked_bin     = window_results[0]["chosen_bin"]     if window_results else None
    locked_range_m = window_results[0]["chosen_range_m"] if window_results else None
    print(f"Locked bin: {locked_bin}  ({locked_range_m:.3f} m)")

    # 3) Add runner-owned epochs and persist all per-window intermediate evidence
    for out in window_results:
        out["start_epoch"] = t0 + out["start_frame"] / c["frame_rate_hz"]
        out["end_epoch"] = t0 + out["end_frame"] / c["frame_rate_hz"]

    intermediates_path = intermediates.write_intermediates_npz(
        run_dir / "intermediates.npz",
        window_results,
        total_cube_frames=analysis_profiles.shape[0],
    )
    print(f"Intermediates -> {intermediates_path}")

    # 4) Build radar DataFrame (includes ECA+AHET diagnostic columns)
    rows = []
    for out in window_results:
        rows.append({
            "start_epoch":       out["start_epoch"],
            "end_epoch":         out["end_epoch"],
            "hr_bpm":            out["hr_bpm"],
            "rr_bpm":            out["rr_bpm"],
            "chosen_range_m":    out["chosen_range_m"],
            "heart_spectrum_stage": out["heart_spectrum_stage"],
            "heart_peak_hz": out["heart_peak_hz"],
            "accepted_candidate_rank": out["accepted_candidate_rank"],
            "accepted_candidate_initial_hz": out["accepted_candidate_initial_hz"],
            "accepted_candidate_refined_hz": out["accepted_candidate_refined_hz"],
            "accepted_second_harmonic_refined_hz": out[
                "accepted_second_harmonic_refined_hz"
            ],
            "schema_version": out["schema_version"],
            "window_index": out["window_index"],
            "start_frame": out["start_frame"],
            "end_frame": out["end_frame"],
            "window_frames": out["window_frames"],
            "hop_frames": out["hop_frames"],
            "frame_rate_hz": out["frame_rate_hz"],
            "range_bin": out["range_bin"],
            "range_m": out["range_m"],
            "eca_applied": out["eca_applied"],
            "ahet_verified": out["ahet_verified"],
            "harmonic_suspect":  out.get("harmonic_suspect", False),
            "f_r_hz_used":       out.get("f_r_hz_used",  float("nan")),
            "f_r_raw_hz":        out.get("f_r_raw_hz",   float("nan")),
            "f_r_outlier":       out["f_r_outlier"],
        })
    radar_df = pd.DataFrame(rows)

    # 5) Compare to Masimo
    mas = masimo.load_masimo(masimo_path)
    radar_df["masimo_br"] = [
        masimo.reference_br(mas, row["start_epoch"], row["end_epoch"])
        for _, row in radar_df.iterrows()
    ]
    merged = compare.compare(radar_df, mas, min_pi=cfg["compare"]["min_pi"])

    # 6) Metrics — all windows, then AHET-verified only
    m_all = compare.metrics(merged)

    merged_verified = merged[merged["ahet_verified"] == True].copy()  # noqa: E712
    if len(merged_verified) > 0 and not merged_verified["low_quality"].all():
        m_verified = compare.metrics(merged_verified)
    else:
        m_verified = None

    # 7) Per-window summary table
    _SEP = "-" * 127
    print(f"\n{_SEP}")
    print(
        f"{'win':>3}  {'t_start':>10}  {'radar_HR':>8}  {'masimo_PR':>9}  "
        f"{'error':>6}  {'f_r_bpm':>7}  {'masimo_br':>9}  {'outlier':>7}  {'AHET':>6}  {'suspect':>7}"
    )
    print(_SEP)
    for i, row in merged.iterrows():
        radar_hr   = row["hr_bpm"]
        hr_str     = f"{radar_hr:8.1f}" if not pd.isna(radar_hr) else "     NaN"
        err_str    = f"{row['error_bpm']:+6.1f}" if not pd.isna(row.get("error_bpm")) else "   NaN"
        f_r_raw_hz = row.get("f_r_raw_hz", float("nan"))
        f_r_str    = f"{f_r_raw_hz * 60:7.1f}" if not pd.isna(f_r_raw_hz) else "    NaN"
        br_val     = row.get("masimo_br", float("nan"))
        br_str     = f"{br_val:9.1f}" if not pd.isna(br_val) else "      NaN"
        outl_str   = "    YES" if row.get("f_r_outlier") else "     no"
        ahet_str   = "  YES" if row.get("ahet_verified") else "   no"
        susp_str   = "    YES" if row.get("harmonic_suspect") else "     no"
        print(
            f"{i:>3}  {row['start_epoch']:>10.0f}  {hr_str}  "
            f"{row['masimo_pr_bpm']:>9.1f}  {err_str}  {f_r_str}  {br_str}  "
            f"{outl_str}  {ahet_str}  {susp_str}"
        )
    print(_SEP)

    # 8) Summary statistics
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

    # 9) Save outputs
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
