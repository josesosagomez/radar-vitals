#!/usr/bin/env python3
"""exp001 — offline baseline runner.

Wires raw capture -> range profile -> sliding-window HR -> Masimo comparison -> overlay
plot + metrics, with per-run logging. Run from the repo root:

    python experiments/exp001_offline_baseline/run.py

The radar reader (radar_io.read_adc_bin) is a stub until your chirp config is final, so
this script will stop there with a clear message — that's intentional (no silent wrong
numbers). The Masimo + compare + vitals paths are complete and unit-tested on synthetic data.
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

    run_dir = REPO_ROOT / "results" / "exp001_offline_baseline" / time.strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    c = cfg["chirp"]
    d = cfg["data"]
    v = cfg["vitals"]

    # --- Parse LogFile for UTC capture timestamps ---
    log_info = radar_io.parse_logfile(REPO_ROOT / d["logfile_csv"], d["utc_offset_hours"])
    start_epoch_utc = log_info["start_epoch_utc"]
    end_epoch_utc = log_info["end_epoch_utc"]
    duration_s = log_info["duration_s"]

    chirp_cfg = radar_io.ChirpConfig(
        num_adc_samples=c["num_adc_samples"], num_rx=c["num_rx"], num_tx=c["num_tx"],
        num_chirps_per_frame=c["num_chirps_per_frame"], num_frames=c["num_frames"],
        frame_rate_hz=c["frame_rate_hz"], range_resolution_m=c["range_resolution_m"],
    )

    # --- Infer actual frame count from .bin size (overrides config value) ---
    # Return value unused; infer_num_frames prints the inferred vs configured count.
    radar_io.infer_num_frames(REPO_ROOT / d["radar_bin"], chirp_cfg)

    # --- Trim & analysis window ---
    trim_start_s = d["trim_start_s"]
    trim_frames = int(trim_start_s * c["frame_rate_hz"])
    t0 = start_epoch_utc + trim_start_s

    print(f"Capture: {start_epoch_utc} -> {end_epoch_utc} ({duration_s} s)")
    print(f"Trim:    first {trim_start_s} s skipped ({trim_frames} frames)")
    print(f"Analysis window: {t0} -> {end_epoch_utc} ({duration_s - trim_start_s} s)")
    print(f"Masimo epoch range to check: {t0} to {end_epoch_utc}")

    # 1) Radar cube -> range profile  (radar_io.read_adc_bin is a stub; finish it first)
    cube = radar_io.read_adc_bin(REPO_ROOT / d["radar_bin"], chirp_cfg,
                                  trim_frames=trim_frames)
    profiles = radar_io.range_profile(cube)
    raxis = radar_io.range_axis_m(c["num_adc_samples"], c["range_resolution_m"])

    # 2) Sliding-window HR estimates — bin locked for entire capture, epoch base is t0
    params = vitals.VitalsParams(
        fs_hz=c["frame_rate_hz"], gate_min_m=v["gate_min_m"], gate_max_m=v["gate_max_m"],
        heart_band_hz=tuple(v["heart_band_hz"]), resp_band_hz=tuple(v["resp_band_hz"]),
    )
    window_frames = int(v["window_s"] * c["frame_rate_hz"])
    hop_frames    = int(v["hop_s"]    * c["frame_rate_hz"])
    cfg_locked_bin = v.get("locked_bin", None)
    window_results = vitals.run_pipeline_locked(
        profiles[trim_frames:], raxis, params, window_frames, hop_frames,
        locked_bin=cfg_locked_bin,
    )
    locked_bin = window_results[0]["chosen_bin"] if window_results else None
    locked_range_m = window_results[0]["chosen_range_m"] if window_results else None
    print(f"Locked bin:      {locked_bin}  ({locked_range_m:.3f} m)")

    rows = []
    for out in window_results:
        s = out["window_start_frame"]
        e = out["window_end_frame"]
        rows.append({
            "start_epoch": t0 + s / c["frame_rate_hz"],
            "end_epoch": t0 + e / c["frame_rate_hz"],
            "hr_bpm": out["hr_bpm"],
            "rr_bpm": out["rr_bpm"],
            "chosen_range_m": out["chosen_range_m"],
        })
    radar_df = pd.DataFrame(rows)

    # 3) Compare to Masimo
    mas = masimo.load_masimo(REPO_ROOT / d["masimo_csv"])
    merged = compare.compare(radar_df, mas, min_pi=cfg["compare"]["min_pi"])
    m = compare.metrics(merged)
    compare.overlay_plot(merged, run_dir / "overlay.png", title="exp001 radar vs Masimo")

    merged.to_csv(run_dir / "comparison.csv", index=False)
    (run_dir / "metrics.json").write_text(json.dumps(m, indent=2))
    (run_dir / "config_used.yaml").write_text(config_path.read_text())
    print(json.dumps(m, indent=2))
    print(f"\nResults -> {run_dir}")

    if cfg["wandb"]["enabled"]:
        import wandb
        wandb.init(project=cfg["wandb"]["project"], config=cfg)
        wandb.log(m)
        wandb.log({"overlay": wandb.Image(str(run_dir / "overlay.png"))})
        wandb.finish()


if __name__ == "__main__":
    main(Path(__file__).resolve().parent / "config.yaml")
