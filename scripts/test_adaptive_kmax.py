"""Diagnostic script: adaptive k_max ECA investigation on cap3_retake (and cap5).

Tests the hypothesis that reducing k_max so the highest suppressed harmonic stays
below the cardiac band floor fixes the supine capture failures.

Adaptive formula:
    k_max_adaptive = floor((CARDIAC_FLOOR_HZ - MARGIN_HZ) / f_r_hz)
    clamped to [1, K_MAX_CONFIG=6]

Does NOT modify vitals.py, radar_io.py, compare.py, or any config file.
Writes CSV to results/diagnostics/adaptive_kmax/.

Usage:
    python scripts/test_adaptive_kmax.py
    python scripts/test_adaptive_kmax.py --fixed-kmax 3
    python scripts/test_adaptive_kmax.py --capture cap5
    python scripts/test_adaptive_kmax.py --capture cap5 --fixed-kmax 3
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src import compare, masimo, radar_io, vitals  # noqa: E402
from src.windowing import sliding_windows           # noqa: E402

# ─── Adaptive k_max formula parameters ────────────────────────────────────────
CARDIAC_FLOOR_HZ = 0.833   # 50 bpm — bottom of cardiac search band
MARGIN_HZ        = 0.083   # 5 bpm safety margin below cardiac floor
K_MAX_CONFIG     = 6       # hard ceiling from config


def adaptive_kmax(f_r_hz: float) -> int:
    """Floor((cardiac_floor - margin) / f_r) clamped to [1, K_MAX_CONFIG]."""
    k = math.floor((CARDIAC_FLOOR_HZ - MARGIN_HZ) / f_r_hz)
    return max(1, min(k, K_MAX_CONFIG))


# ─── Capture definitions (hard-coded from exp004 config; no config mutation) ──
CAPTURES = {
    "cap3_retake": {
        "bin_files": [
            "data/raw/cap3_retake_20260615_160417_0.bin",
            "data/raw/cap3_retake_20260615_160417_1.bin",
        ],
        "logfile":         "data/raw/cap3_retake_20260615_160417_LogFile.csv",
        "masimo_file":     "data/raw/cap3_retake_20260615_160417_masimo.csv",
        "locked_bin":      28,
        "expected_frames": 9000,
        "canonical_mae":   23.534,
        "canonical_bias":  -19.608,
        "canonical_nan":   32,
    },
    "cap5": {
        "bin_files": [
            "data/raw/cap5_20260615_172410_0.bin",
            "data/raw/cap5_20260615_172410_1.bin",
        ],
        "logfile":         "data/raw/cap5_20260615_172410_LogFile.csv",
        "masimo_file":     "data/raw/cap5_20260615_172410_masimo.csv",
        "locked_bin":      28,
        "expected_frames": 9000,
        "canonical_mae":   19.498,
        "canonical_bias":  -17.227,
        "canonical_nan":   27,
    },
}

SEP = "=" * 68
LINE = "-" * 68


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Adaptive k_max ECA diagnostic — no pipeline code modified"
    )
    parser.add_argument(
        "--capture", choices=list(CAPTURES.keys()), default="cap3_retake",
        help="Capture to process (default: cap3_retake)"
    )
    parser.add_argument(
        "--fixed-kmax", type=int, default=None, metavar="N",
        help="Use a fixed k_max=N for all windows instead of adaptive formula"
    )
    args = parser.parse_args()

    cap_key  = args.capture
    cap_info = CAPTURES[cap_key]
    mode     = (f"fixed k_max={args.fixed_kmax}"
                if args.fixed_kmax is not None else "adaptive k_max")

    print(SEP)
    print(f"  ADAPTIVE k_max DIAGNOSTIC  |  {cap_key}  |  {mode}")
    print(SEP)
    print(f"\n  Formula: k_max = floor(({CARDIAC_FLOOR_HZ} - {MARGIN_HZ}) / f_r_hz)")
    print(f"           clamped to [1, {K_MAX_CONFIG}]")
    print(f"\n  Cardiac floor: {CARDIAC_FLOOR_HZ} Hz = {CARDIAC_FLOOR_HZ*60:.0f} bpm")
    print(f"  Safety margin: {MARGIN_HZ} Hz = {MARGIN_HZ*60:.0f} bpm\n")

    # ── Load shared radar / processing parameters from exp004 config ──────────
    cfg_path = REPO_ROOT / "experiments/exp004_window_length/config.yaml"
    cfg      = yaml.safe_load(cfg_path.read_text())

    r             = cfg["radar"]
    proc          = cfg["processing"]
    eca_cfg       = cfg["eca"]
    exp4          = cfg["exp004"]
    min_pi        = cfg["compare"]["min_pi"]
    utc_offset    = cfg["utc_offset_hours"]
    frame_rate_hz = float(r["frame_rate_hz"])
    trim_s        = int(proc["trim_s"])
    trim_frames   = int(trim_s * frame_rate_hz)
    ahet_dev      = eca_cfg["ahet_deviation_hz"]
    locked_bin    = cap_info["locked_bin"]

    print(f"  ECA config k_max (suppressed):  {eca_cfg['k_max']}")
    print(f"  AHET deviation:                 {ahet_dev} Hz")
    print(f"  Trim:                           {trim_s} s = {trim_frames} frames")
    print(f"  Locked bin:                     {locked_bin}")

    # ── Build ChirpConfig ────────────────────────────────────────────────────
    chirp_cfg = radar_io.ChirpConfig(
        num_adc_samples      = r["num_adc_samples"],
        num_rx               = r["num_rx"],
        num_tx               = r["num_tx"],
        num_chirps_per_frame = r["num_chirps_per_frame"],
        num_frames           = cap_info["expected_frames"],
        frame_rate_hz        = frame_rate_hz,
        range_resolution_m   = r["range_resolution_m"],
    )

    # ── Timestamps ───────────────────────────────────────────────────────────
    log_info        = radar_io.parse_logfile(
        REPO_ROOT / cap_info["logfile"], utc_offset
    )
    start_epoch_utc = log_info["start_epoch_utc"]
    t0              = start_epoch_utc + trim_s   # epoch of first analysis sample

    # ── Masimo ───────────────────────────────────────────────────────────────
    mas_df = masimo.load_masimo(REPO_ROOT / cap_info["masimo_file"])

    # ── Load radar cube → range profiles → trimmed phase ─────────────────────
    bin_paths = [REPO_ROOT / p for p in cap_info["bin_files"]]
    print(f"\n  Loading radar data ({len(bin_paths)} files)...")
    cube     = radar_io.read_adc_bin(bin_paths, chirp_cfg, trim_frames=trim_frames)
    profiles = radar_io.range_profile(cube)
    del cube  # free ~1.5 GB

    profiles_trimmed    = profiles[trim_frames:]
    del profiles

    print(f"  profiles_trimmed shape: {profiles_trimmed.shape}")
    phase_unwrapped_all = vitals.phase_at_bin(profiles_trimmed, locked_bin)
    phase_clean_all     = vitals.remove_impulse_noise(phase_unwrapped_all)
    del profiles_trimmed

    # ── Window grid (baseline_20s: same as exp004 baseline) ──────────────────
    baseline_wf = int(exp4["baseline_window_s"] * frame_rate_hz)  # 400 frames = 20 s
    baseline_hf = int(exp4["baseline_hop_s"]    * frame_rate_hz)  # 100 frames =  5 s

    abs_windows = sliding_windows(
        total_frames  = cap_info["expected_frames"],
        trim_frames   = trim_frames,
        window_frames = baseline_wf,
        hop_frames    = baseline_hf,
    )
    print(f"  Window grid: {len(abs_windows)} windows "
          f"({baseline_wf} frames / {baseline_hf} hop)\n")

    # ── Per-window processing ─────────────────────────────────────────────────
    rows: list[dict] = []
    kmax_used_list: list[int] = []

    for win_idx, (abs_start, abs_end) in enumerate(abs_windows):
        s = abs_start - trim_frames   # trim-relative start
        e = abs_end   - trim_frames   # trim-relative end
        win_phase = phase_clean_all[s:e]

        # Respiration rate (same method as pipeline)
        resp = vitals.estimate_rate_from_phase(
            win_phase, frame_rate_hz, vitals.RESP_BAND_HZ
        )
        resp_peak_raw_idx = int(np.argmin(np.abs(resp["freqs_hz"] - resp["peak_hz"])))
        f_r_hz = vitals.refine_freq_hz(
            resp["spectrum"], resp["freqs_hz"], resp_peak_raw_idx
        )

        # Determine k_max for this window
        k_used = args.fixed_kmax if args.fixed_kmax is not None else adaptive_kmax(f_r_hz)
        kmax_used_list.append(k_used)

        # Heart rate: ECA + AHET with this window's k_max
        heart = vitals.estimate_rate_from_phase(
            win_phase, frame_rate_hz, vitals.HEART_BAND_HZ,
            f_r_hz=f_r_hz, k_max=k_used, ahet_deviation_hz=ahet_dev,
        )

        rows.append({
            "window_index":     win_idx,
            "start_epoch":      t0 + s / frame_rate_hz,
            "end_epoch":        t0 + e / frame_rate_hz,
            "start_frame":      s,
            "end_frame":        e,
            "f_r_hz":           f_r_hz,
            "f_r_bpm":          f_r_hz * 60.0,
            "k_max_used":       k_used,
            "hr_bpm":           heart["rate_bpm"],
            "ahet_verified":    bool(heart["ahet_verified"]),
            "f_r_outlier":      bool(heart["f_r_outlier"]),
            "harmonic_suspect": bool(heart.get("harmonic_suspect", False)),
        })

    radar_df = pd.DataFrame(rows)

    # ── Masimo alignment ──────────────────────────────────────────────────────
    merged = compare.compare(radar_df, mas_df, min_pi=min_pi)

    # ── Per-window table (sorted by abs_error_bpm desc) ───────────────────────
    fin = merged.dropna(subset=["error_bpm"]).copy()
    n_nan = int(merged["hr_bpm"].isna().sum())

    print(LINE)
    print(f"PER-WINDOW TABLE  ({mode})  |  {cap_key}")
    print(f"N_finite={len(fin)}, N_NaN={n_nan}. *** = abs_error > 5 bpm")
    print(LINE)
    print(f"{'Wi':>4}  {'MasPR':>7}  {'Radar':>7}  {'Error':>7}  "
          f"{'AbsErr':>7}  {'f_r':>6}  {'k':>2}  {'AHET':<5}  flag")
    print(LINE)

    for _, row in fin.sort_values("abs_error_bpm", ascending=False).iterrows():
        flag = "***" if row["abs_error_bpm"] > 5 else "   "
        ahet = "PASS" if row["ahet_verified"] else "fail"
        print(f"{int(row['window_index']):>4}  {row['masimo_pr_bpm']:>7.2f}  "
              f"{row['hr_bpm']:>7.2f}  {row['error_bpm']:>7.2f}  "
              f"{row['abs_error_bpm']:>7.2f}  {row['f_r_bpm']:>6.2f}  "
              f"{int(row['k_max_used']):>2}  {ahet:<5}  {flag}")

    nan_idx = sorted(
        merged[merged["hr_bpm"].isna()]["window_index"].astype(int).tolist()
    )
    print(f"\n  NaN windows ({n_nan}): {nan_idx}")

    # ── Summary ───────────────────────────────────────────────────────────────
    n_total  = len(merged)
    n_finite = len(fin)

    if n_finite > 0:
        mae  = float(fin["abs_error_bpm"].mean())
        rmse = float(math.sqrt((fin["error_bpm"] ** 2).mean()))
        bias = float(fin["error_bpm"].mean())
        n_ahet     = int(fin["ahet_verified"].sum())
        ahet_rate  = n_ahet / n_finite * 100
    else:
        mae = rmse = bias = float("nan")
        n_ahet = 0
        ahet_rate = float("nan")

    print(f"\n{SEP}")
    print(f"  SUMMARY  |  {cap_key}  |  {mode}")
    print(SEP)
    print(f"  N total:      {n_total}")
    print(f"  N finite HR:  {n_finite}")
    print(f"  N NaN HR:     {n_nan}")
    if not math.isnan(mae):
        print(f"  MAE:          {mae:.3f} bpm")
        print(f"  RMSE:         {rmse:.3f} bpm")
        print(f"  Bias:         {bias:.3f} bpm")
        print(f"  AHET pass:    {n_ahet}/{n_finite} ({ahet_rate:.1f}%)")
    else:
        print("  MAE/RMSE/bias: NaN (no finite windows)")

    # k_max distribution
    kmax_counts = Counter(kmax_used_list)
    print(f"\n  k_max distribution ({mode}):")
    for k in sorted(kmax_counts):
        bar = "#" * kmax_counts[k]
        print(f"    k={k}: {kmax_counts[k]:3d} windows  {bar}")

    # Comparison to canonical exp004 baseline_20s (k_max=6)
    c_mae  = cap_info["canonical_mae"]
    c_bias = cap_info["canonical_bias"]
    c_nan  = cap_info["canonical_nan"]
    print(f"\n  COMPARISON TO CANONICAL (exp004 baseline_20s, k_max=6):")
    print(f"    Canonical:  MAE={c_mae:.3f} bpm  bias={c_bias:.3f}  N_NaN={c_nan}/81")
    if not math.isnan(mae):
        print(f"    This run:   MAE={mae:.3f} bpm  bias={bias:.3f}  N_NaN={n_nan}/81")
        d_mae = mae - c_mae
        d_nan = n_nan - c_nan
        impr  = "IMPROVEMENT" if d_mae < 0 else "WORSE"
        nan_s = "FEWER NaN" if d_nan < 0 else ("MORE NaN" if d_nan > 0 else "same NaN")
        print(f"    Delta MAE:  {d_mae:+.3f} bpm  [{impr}]")
        print(f"    Delta NaN:  {d_nan:+d}         [{nan_s}]")
    else:
        print(f"    This run:   MAE=NaN (all windows NaN)")

    # Worst 3 remaining failures
    print(f"\n  WORST 3 REMAINING WINDOWS:")
    if n_finite > 0:
        worst3 = fin.nlargest(3, "abs_error_bpm")
        print(f"  {'Wi':>4}  {'f_r_bpm':>8}  {'k':>2}  {'Radar':>8}  "
              f"{'MasPR':>7}  {'Error':>8}  {'AHET':<5}")
        print("  " + "-" * 58)
        for _, row in worst3.iterrows():
            ahet = "PASS" if row["ahet_verified"] else "fail"
            print(f"  {int(row['window_index']):>4}  {row['f_r_bpm']:>8.2f}  "
                  f"{int(row['k_max_used']):>2}  {row['hr_bpm']:>8.2f}  "
                  f"{row['masimo_pr_bpm']:>7.2f}  {row['error_bpm']:>8.2f}  {ahet}")
    else:
        print("  (no finite windows)")

    # ── Save CSV ──────────────────────────────────────────────────────────────
    out_dir = REPO_ROOT / "results/diagnostics/adaptive_kmax"
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix  = (f"_fixed_kmax{args.fixed_kmax}"
               if args.fixed_kmax is not None else "_adaptive_kmax")
    out_path = out_dir / f"{cap_key}_20s{suffix}.csv"
    merged.to_csv(out_path, index=False)
    print(f"\n  Results -> {out_path}")

    print(f"\n{SEP}")
    print("END")
    print(SEP)


if __name__ == "__main__":
    main()
