#!/usr/bin/env python3
"""exp003 — Generalisation test: ECA+AHET pipeline on two new captures (different day).

Runs the exp002 pipeline (ECA + AHET) on two captures from a second session:
  - capture 2a: seated, chair with back
  - capture 2b: seated, chair without back
Purpose: assess whether MAE ~5 bpm generalises beyond the original exp001/exp002 recording.

At startup the user selects a .bin file interactively for each capture; associated
LogFile and Masimo CSVs are found automatically. The chest range bin is not fixed —
it is chosen as the gate bin with the highest mean energy across all frames.

Acceptance criterion (pre-registered in SESSION.md 2026-06-13):
  Strong pass : MAE <= 7 bpm on both recordings
  Pass        : MAE <= 7 bpm on at least one recording
  Marginal    : MAE 7–9 bpm — proceed to exp004 with caveat
  Fail        : MAE > 9 bpm on both — investigate before proceeding

Run from the repo root:
    python experiments/exp003_generalisation/run.py
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


def _select_bin_file(data_raw_dir: Path) -> Path:
    """Print a numbered list of .bin files and return the user's selection."""
    bin_files = sorted(data_raw_dir.glob("*.bin"))
    if not bin_files:
        raise FileNotFoundError(f"No .bin files found in {data_raw_dir}")
    rel = data_raw_dir.relative_to(REPO_ROOT)
    print(f"\nAvailable .bin files in {rel}:")
    for i, f in enumerate(bin_files, 1):
        print(f"  [{i}] {f.name}")
    while True:
        raw = input(f"Select a file (1-{len(bin_files)}): ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(bin_files):
            return bin_files[int(raw) - 1]
        print(f"  Please enter a number between 1 and {len(bin_files)}.")


def _find_associated_files(bin_path: Path) -> dict[str, Path | None]:
    """Return {'logfile': Path|None, 'masimo': Path|None} and print what was found."""
    parent = bin_path.parent

    # LogFile
    if "_radar" in bin_path.name:
        logfile_name = bin_path.name.replace("_radar.bin", "_radar_LogFile.csv")
        logfile_path = parent / logfile_name
        logfile: Path | None = logfile_path if logfile_path.exists() else None
    else:
        prefix = bin_path.stem
        candidates = sorted(parent.glob(f"{prefix}*LogFile*.csv"))
        logfile = candidates[0] if candidates else None
        logfile_name = logfile.name if logfile else f"{prefix}_LogFile.csv"

    # Masimo
    if "_radar.bin" in bin_path.name:
        masimo_name = bin_path.name.replace("_radar.bin", "_masimo.csv")
    else:
        masimo_name = bin_path.stem + "_masimo.csv"
    masimo_path = parent / masimo_name
    masimo_found: Path | None = masimo_path if masimo_path.exists() else None

    print(f"\nSelected: {bin_path.name}")
    print("Associated files found:")
    if logfile:
        print(f"  {'LogFile':<7} : {logfile.name}")
    else:
        print(f"  {'LogFile':<7} : {logfile_name} (not found — skipping)")
    if masimo_found:
        print(f"  {'Masimo':<7} : {masimo_found.name}")
    else:
        print(f"  {'Masimo':<7} : {masimo_name} (not found — skipping)")

    return {"logfile": logfile, "masimo": masimo_found}


def run_capture(
    config_path: Path,
    run_dir: Path,
    label: str,
    bin_path: Path,
    logfile_path: Path,
    masimo_path: Path,
) -> dict:
    """Run the exp002 (ECA+AHET) pipeline on one capture. Saves outputs to run_dir.

    Returns a summary dict: label, mae, rmse, bias, n_windows, n_nan, run_dir.
    """
    run_dir.mkdir(parents=True, exist_ok=True)

    cfg = yaml.safe_load(config_path.read_text())
    np.random.seed(cfg["seed"])

    c = cfg["chirp"]
    d = cfg["data"]
    v = cfg["vitals"]

    log_info = radar_io.parse_logfile(logfile_path, d["utc_offset_hours"])
    start_epoch_utc = log_info["start_epoch_utc"]
    end_epoch_utc   = log_info["end_epoch_utc"]
    duration_s      = log_info["duration_s"]

    chirp_cfg = radar_io.ChirpConfig(
        num_adc_samples=c["num_adc_samples"], num_rx=c["num_rx"], num_tx=c["num_tx"],
        num_chirps_per_frame=c["num_chirps_per_frame"], num_frames=c["num_frames"],
        frame_rate_hz=c["frame_rate_hz"], range_resolution_m=c["range_resolution_m"],
    )
    radar_io.infer_num_frames(bin_path, chirp_cfg)

    trim_start_s = d["trim_start_s"]
    trim_frames  = int(trim_start_s * c["frame_rate_hz"])
    t0           = start_epoch_utc + trim_start_s

    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    print(f"Capture:  {start_epoch_utc} -> {end_epoch_utc} ({duration_s} s)")
    print(f"Trim:     first {trim_start_s} s skipped ({trim_frames} frames)")
    print(f"Analysis: {t0} -> {end_epoch_utc} ({duration_s - trim_start_s:.0f} s)")

    cube     = radar_io.read_adc_bin(bin_path, chirp_cfg, trim_frames=trim_frames)
    profiles = radar_io.range_profile(cube)
    raxis    = radar_io.range_axis_m(c["num_adc_samples"], c["range_resolution_m"])

    params = vitals.VitalsParams(
        fs_hz=c["frame_rate_hz"],
        gate_min_m=v["gate_min_m"], gate_max_m=v["gate_max_m"],
        heart_band_hz=tuple(v["heart_band_hz"]),
        resp_band_hz=tuple(v["resp_band_hz"]),
    )
    window_frames  = int(v["window_s"] * c["frame_rate_hz"])
    hop_frames     = int(v["hop_s"]    * c["frame_rate_hz"])
    # locked_bin=None: select_range_bin picks the peak-energy bin in the gate
    # across all frames — same data-driven approach as exp000.
    window_results = vitals.run_pipeline_locked(
        profiles[trim_frames:], raxis, params, window_frames, hop_frames,
        locked_bin=None,
    )

    locked_bin     = window_results[0]["chosen_bin"]     if window_results else None
    locked_range_m = window_results[0]["chosen_range_m"] if window_results else None
    print(f"Locked bin: {locked_bin}  ({locked_range_m:.3f} m)")

    rows = []
    for out in window_results:
        s = out["window_start_frame"]
        e = out["window_end_frame"]
        rows.append({
            "start_epoch":      t0 + s / c["frame_rate_hz"],
            "end_epoch":        t0 + e / c["frame_rate_hz"],
            "hr_bpm":           out["hr_bpm"],
            "rr_bpm":           out["rr_bpm"],
            "chosen_range_m":   out["chosen_range_m"],
            "ahet_verified":    out.get("ahet_verified", False),
            "harmonic_suspect": out.get("harmonic_suspect", False),
            "f_r_hz_used":      out.get("f_r_hz_used",  float("nan")),
            "f_r_raw_hz":       out.get("f_r_raw_hz",   float("nan")),
            "f_r_outlier":      out.get("f_r_outlier",  False),
        })
    radar_df = pd.DataFrame(rows)

    mas = masimo.load_masimo(masimo_path)
    radar_df["masimo_br"] = [
        masimo.reference_br(mas, row["start_epoch"], row["end_epoch"])
        for _, row in radar_df.iterrows()
    ]
    merged = compare.compare(radar_df, mas, min_pi=cfg["compare"]["min_pi"])

    m_all = compare.metrics(merged)

    merged_verified = merged[merged["ahet_verified"] == True].copy()  # noqa: E712
    if len(merged_verified) > 0 and not merged_verified["low_quality"].all():
        m_verified = compare.metrics(merged_verified)
    else:
        m_verified = None

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

    n_nan     = int(radar_df["hr_bpm"].isna().sum())
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

    merged.to_csv(run_dir / "comparison.csv", index=False)
    (run_dir / "metrics_all.json").write_text(json.dumps(m_all, indent=2))
    if m_verified is not None:
        (run_dir / "metrics_verified.json").write_text(json.dumps(m_verified, indent=2))
    (run_dir / "config_used.yaml").write_text(config_path.read_text())
    compare.overlay_plot(
        merged, run_dir / "overlay.png",
        title=f"exp003 ECA+AHET — {label} — radar vs Masimo",
    )
    print(f"\nResults -> {run_dir}")

    if cfg["wandb"]["enabled"]:
        import wandb
        wandb.init(project=cfg["wandb"]["project"], config=cfg, name=f"exp003_{label}")
        wandb.log({**m_all, "n_verified": n_verified, "n_nan": n_nan, "label": label})
        if m_verified:
            wandb.log({"verified_" + k: v for k, v in m_verified.items()})
        wandb.log({"overlay": wandb.Image(str(run_dir / "overlay.png"))})
        wandb.finish()

    return {
        "label":     label,
        "mae":       m_all["mae_bpm"],
        "rmse":      m_all["rmse_bpm"],
        "bias":      m_all["bias_bpm"],
        "n_windows": m_all["n_windows"],
        "n_nan":     m_all["n_nan_windows"],
        "run_dir":   run_dir,
    }


def _print_summary(summaries: list[dict]) -> None:
    col = 28
    sep = "=" * (16 + col * len(summaries) + 2)

    print(f"\n\n{sep}")
    print(f"  exp003 GENERALISATION SUMMARY  "
          f"(exp002 reference: MAE 5.29, RMSE 7.03, bias −2.27 bpm)")
    print(sep)
    header = f"  {'Metric':<14}" + "".join(f"  {s['label']:<{col}}" for s in summaries)
    print(header)
    print("-" * len(header))

    rows = [
        ("MAE (bpm)",  lambda s: f"{s['mae']:.2f}"),
        ("RMSE (bpm)", lambda s: f"{s['rmse']:.2f}"),
        ("Bias (bpm)", lambda s: f"{s['bias']:+.2f}"),
        ("N windows",  lambda s: str(s["n_windows"])),
        ("N NaN",      lambda s: str(s["n_nan"])),
    ]
    for label, fmt in rows:
        print(f"  {label:<14}" + "".join(f"  {fmt(s):<{col}}" for s in summaries))

    print(sep)

    maes = [s["mae"] for s in summaries]
    if all(m <= 7.0 for m in maes):
        verdict = "STRONG PASS — MAE <= 7 bpm on both recordings"
    elif any(m <= 7.0 for m in maes):
        verdict = "PASS — MAE <= 7 bpm on at least one recording"
    elif all(m <= 9.0 for m in maes):
        verdict = "MARGINAL — MAE 7–9 bpm on both; proceed to exp004 with caveat"
    elif any(m <= 9.0 for m in maes):
        verdict = "MARGINAL (mixed) — at least one recording MAE <= 9 bpm"
    else:
        verdict = "FAIL — MAE > 9 bpm on both; investigate before any further algorithm work"

    print(f"\n  Acceptance criterion (pre-registered SESSION.md 2026-06-13):")
    print(f"  -> {verdict}")
    print(sep)


def main() -> None:
    session_ts = time.strftime("%Y%m%d_%H%M%S")
    exp_root = REPO_ROOT / "results" / "exp003_generalisation" / session_ts

    here = Path(__file__).resolve().parent
    data_raw_dir = REPO_ROOT / "data" / "raw"
    captures = [
        ("chair_back",    here / "config_chair_back.yaml"),
        ("chair_no_back", here / "config_chair_no_back.yaml"),
    ]

    # --- Interactive file selection for each capture ---
    selected = []
    for i, (label, config_path) in enumerate(captures, 1):
        print(f"\n--- Capture {i} of {len(captures)}: {label} ---")
        bin_path = _select_bin_file(data_raw_dir)
        assoc = _find_associated_files(bin_path)
        if assoc["logfile"] is None:
            raise FileNotFoundError(
                f"LogFile not found for {bin_path.name} — cannot parse capture timestamps."
            )
        if assoc["masimo"] is None:
            raise FileNotFoundError(
                f"Masimo CSV not found for {bin_path.name} — cannot compare against ground truth."
            )
        selected.append((label, config_path, bin_path, assoc["logfile"], assoc["masimo"]))

    summaries = []
    for label, config_path, bin_path, logfile_path, masimo_path in selected:
        summary = run_capture(
            config_path, exp_root / label, label,
            bin_path, logfile_path, masimo_path,
        )
        summaries.append(summary)

    _print_summary(summaries)
    print(f"\nFull results in: {exp_root}")


if __name__ == "__main__":
    main()
