#!/usr/bin/env python3
"""exp004 — multi-capture window-length study: 20/25/30 s.

Processes three radar captures (cap1, cap2, cap3) with a common ECA+AHET
pipeline, compares window-length conditions on a shared-center grid, and
runs pooled + chair-condition analysis across all captures.

Directory layout written by this runner:
    results/exp004_window_length/<timestamp>/
        # per-capture artifacts — all three caps use their own subdirectory
        cap1/baseline_20s/comparison.csv, intermediates.npz
        cap1/condition_{20,25,30}s/comparison.csv, intermediates.npz
        cap1/paired_summary.json
        cap2/baseline_20s/..., cap2/condition_*/..., cap2/paired_summary.json
        cap3/baseline_20s/..., cap3/condition_*/..., cap3/paired_summary.json
        # cross-capture analysis (at results root)
        window_length_summary.json
        chair_condition_summary.json
        provenance.json
        config_used.yaml
        stdout.log

Run from repo root:
    python experiments/exp004_window_length/run.py
"""
from __future__ import annotations

import argparse
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

from src import compare, intermediates, masimo, radar_io, vitals  # noqa: E402
from src.windowing import common_center_windows, sliding_windows  # noqa: E402
from src.compare import paired_metrics, coverage_table  # noqa: E402
from experiments.exp004_window_length.analysis import (  # noqa: E402
    pooled_window_length_summary,
    chair_condition_summary,
    masimo_summary as _masimo_summary,
    collect_provenance,
)


# ---------------------------------------------------------------------------
# stdout tee — mirrors output to a log file
# ---------------------------------------------------------------------------

class _Tee:
    def __init__(self, file):
        self._file = file
        self._stdout = sys.__stdout__

    def write(self, data: str) -> None:
        self._stdout.write(data)
        self._file.write(data)

    def flush(self) -> None:
        self._stdout.flush()
        self._file.flush()


def setup_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _log_fh = open(log_path, "w", buffering=1, encoding="utf-8")
    sys.stdout = _Tee(_log_fh)


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _json_safe(obj):
    """Recursively replace NaN/inf with None for JSON serialisation."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(x) for x in obj]
    return obj


def save_json(obj, path: Path) -> None:
    path.write_text(json.dumps(_json_safe(obj), indent=2))


# ---------------------------------------------------------------------------
# Per-window processing (shared across all conditions)
# ---------------------------------------------------------------------------

_SEP = "-" * 127


def _process_windows(
    abs_windows: list[tuple[int, int]],
    phase_unwrapped_all: np.ndarray,
    phase_clean_all: np.ndarray,
    trim_frames: int,
    frame_rate_hz: float,
    t0: float,
    locked_bin: int,
    locked_range_m: float,
    eca_k_max: int,
    eca_ahet_dev: float,
    hop_frames: int,
) -> list[dict]:
    """Process a list of (abs_start, abs_end) windows using pre-extracted phase.

    All frame indices are absolute. Slicing into the trimmed phase arrays requires
    subtracting trim_frames.
    """
    results: list[dict] = []
    for win_idx, (abs_start, abs_end) in enumerate(abs_windows):
        s = abs_start - trim_frames   # trim-relative start
        e = abs_end   - trim_frames   # trim-relative end
        window_frames = e - s
        win_phase = phase_clean_all[s:e]

        resp = vitals.estimate_rate_from_phase(
            win_phase, frame_rate_hz, vitals.RESP_BAND_HZ
        )
        resp_peak_raw_index = int(
            np.argmin(np.abs(resp["freqs_hz"] - resp["peak_hz"]))
        )
        resp_peak_raw_hz = float(resp["freqs_hz"][resp_peak_raw_index])
        resp_peak_refined_hz = vitals.refine_freq_hz(
            resp["spectrum"], resp["freqs_hz"], resp_peak_raw_index
        )
        f_r_hz = resp_peak_refined_hz

        heart = vitals.estimate_rate_from_phase(
            win_phase, frame_rate_hz, vitals.HEART_BAND_HZ,
            f_r_hz=f_r_hz, k_max=eca_k_max, ahet_deviation_hz=eca_ahet_dev,
        )
        if heart["f_r_outlier"]:
            print(
                f"  Window {win_idx}: f_r={f_r_hz * 60:.1f} bpm outside "
                "physiological gate — ECA skipped, fell back to bandpass+argmax"
            )

        start_epoch = t0 + s / frame_rate_hz
        end_epoch   = t0 + e / frame_rate_hz

        results.append({
            "hr_bpm":         heart["rate_bpm"],
            "rr_bpm":         resp["rate_bpm"],
            "chosen_bin":     locked_bin,
            "chosen_range_m": locked_range_m,
            "bin_locked":     True,
            "bin_energy":     None,
            "phase_unwrapped": phase_unwrapped_all[s:e],
            "phase_clean":     win_phase,
            "phase_eca":       heart["phase_eca"],
            "resp_freqs_hz":        resp["freqs_hz"],
            "resp_spectrum":        resp["spectrum"],
            "resp_peak_raw_index":  resp_peak_raw_index,
            "resp_peak_raw_hz":     resp_peak_raw_hz,
            "resp_peak_refined_hz": resp_peak_refined_hz,
            "heart_freqs_hz":            heart["freqs_hz"],
            "heart_spectrum_first_pass": heart["spectrum_first_pass"],
            "heart_spectrum":            heart["spectrum"],
            "heart_spectrum_stage":      heart["spectrum_stage"],
            "heart_peak_hz":             heart["peak_hz"],
            "accepted_candidate_rank":             heart["accepted_candidate_rank"],
            "accepted_candidate_initial_hz":       heart["accepted_candidate_initial_hz"],
            "accepted_candidate_refined_hz":       heart["accepted_candidate_refined_hz"],
            "accepted_second_harmonic_refined_hz": heart["accepted_second_harmonic_refined_hz"],
            "candidate_attempted":       heart["candidate_attempted"],
            "candidate_peak_bin_index":  heart["candidate_peak_bin_index"],
            "candidate_initial_hz":      heart["candidate_initial_hz"],
            "candidate_refined_hz":      heart["candidate_refined_hz"],
            "candidate_peak_magnitude":  heart["candidate_peak_magnitude"],
            "candidate_prominence":      heart["candidate_prominence"],
            "candidate_argmax_fallback": heart["candidate_argmax_fallback"],
            "second_peak_bin_hz":        heart["second_peak_bin_hz"],
            "second_peak_refined_hz":    heart["second_peak_refined_hz"],
            "second_peak_magnitude":     heart["second_peak_magnitude"],
            "comparison_floor":          heart["comparison_floor"],
            "peak_to_floor_ratio":       heart["peak_to_floor_ratio"],
            "peak_to_floor_ratio_db":    heart["peak_to_floor_ratio_db"],
            "region_available":          heart["region_available"],
            "candidate_passed":          heart["candidate_passed"],
            "ahet_attempt_spectrum":     heart["ahet_attempt_spectrum"],
            "schema_version":  vitals.INTERMEDIATE_SCHEMA_VERSION,
            "window_index":    win_idx,
            "start_frame":     s,
            "end_frame":       e,
            "window_frames":   window_frames,
            "hop_frames":      hop_frames,
            "frame_rate_hz":   float(frame_rate_hz),
            "range_bin":       locked_bin,
            "range_m":         locked_range_m,
            "eca_applied":     bool(heart["eca_applied"]),
            "f_r_outlier":     bool(heart["f_r_outlier"]),
            "ahet_verified":   bool(heart["ahet_verified"]),
            "start_epoch":     start_epoch,
            "end_epoch":       end_epoch,
            "window_start_frame": s,
            "window_end_frame":   e,
            "harmonic_suspect":   bool(heart.get("harmonic_suspect", False)),
            "f_r_hz_used":        float(f_r_hz),
            "f_r_raw_hz":         float(f_r_hz),
        })
    return results


def _build_radar_df(window_results: list[dict]) -> pd.DataFrame:
    rows = []
    for out in window_results:
        rows.append({
            "start_epoch":                out["start_epoch"],
            "end_epoch":                  out["end_epoch"],
            "hr_bpm":                     out["hr_bpm"],
            "rr_bpm":                     out["rr_bpm"],
            "chosen_range_m":             out["chosen_range_m"],
            "heart_spectrum_stage":       out["heart_spectrum_stage"],
            "heart_peak_hz":              out["heart_peak_hz"],
            "accepted_candidate_rank":    out["accepted_candidate_rank"],
            "accepted_candidate_initial_hz":       out["accepted_candidate_initial_hz"],
            "accepted_candidate_refined_hz":       out["accepted_candidate_refined_hz"],
            "accepted_second_harmonic_refined_hz": out["accepted_second_harmonic_refined_hz"],
            "schema_version":   out["schema_version"],
            "window_index":     out["window_index"],
            "start_frame":      out["start_frame"],
            "end_frame":        out["end_frame"],
            "window_frames":    out["window_frames"],
            "hop_frames":       out["hop_frames"],
            "frame_rate_hz":    out["frame_rate_hz"],
            "range_bin":        out["range_bin"],
            "range_m":          out["range_m"],
            "eca_applied":      out["eca_applied"],
            "ahet_verified":    out["ahet_verified"],
            "harmonic_suspect": out.get("harmonic_suspect", False),
            "f_r_hz_used":      out.get("f_r_hz_used", float("nan")),
            "f_r_raw_hz":       out.get("f_r_raw_hz", float("nan")),
            "f_r_outlier":      out["f_r_outlier"],
        })
    return pd.DataFrame(rows)


def _print_table(merged: pd.DataFrame) -> None:
    print(f"\n{_SEP}")
    print(
        f"{'win':>3}  {'t_start':>10}  {'radar_HR':>8}  {'masimo_PR':>9}  "
        f"{'error':>6}  {'f_r_bpm':>7}  {'masimo_br':>9}  {'outlier':>7}  "
        f"{'AHET':>6}  {'suspect':>7}"
    )
    print(_SEP)
    for i, row in merged.iterrows():
        radar_hr = row["hr_bpm"]
        hr_str   = f"{radar_hr:8.1f}" if not pd.isna(radar_hr) else "     NaN"
        err_val  = row.get("error_bpm", float("nan"))
        err_str  = f"{err_val:+6.1f}" if not pd.isna(err_val) else "   NaN"
        f_r_val  = row.get("f_r_raw_hz", float("nan"))
        f_r_str  = f"{float(f_r_val) * 60:7.1f}" if not pd.isna(f_r_val) else "    NaN"
        br_val   = row.get("masimo_br", float("nan"))
        br_str   = f"{br_val:9.1f}" if not pd.isna(br_val) else "      NaN"
        outl_str = "    YES" if row.get("f_r_outlier") else "     no"
        ahet_str = "  YES" if row.get("ahet_verified") else "   no"
        susp_str = "    YES" if row.get("harmonic_suspect") else "     no"
        print(
            f"{i:>3}  {row['start_epoch']:>10.0f}  {hr_str}  "
            f"{row['masimo_pr_bpm']:>9.1f}  {err_str}  {f_r_str}  {br_str}  "
            f"{outl_str}  {ahet_str}  {susp_str}"
        )
    print(_SEP)


# ---------------------------------------------------------------------------
# Per-condition runner (write artifacts, return results)
# ---------------------------------------------------------------------------

def _run_and_save(
    label: str,
    abs_windows: list[tuple[int, int]],
    hop_frames: int,
    out_dir: Path,
    phase_unwrapped_all: np.ndarray,
    phase_clean_all: np.ndarray,
    trim_frames: int,
    frame_rate_hz: float,
    t0: float,
    locked_bin: int,
    locked_range_m: float,
    eca_k_max: int,
    eca_ahet_dev: float,
    mas: pd.DataFrame,
    min_pi: float,
    total_trimmed_frames: int,
) -> tuple[list[dict], pd.DataFrame]:
    print(f"\n{'=' * 60}")
    print(f"  {label}  ({len(abs_windows)} windows)")
    print(f"{'=' * 60}")

    window_results = _process_windows(
        abs_windows, phase_unwrapped_all, phase_clean_all,
        trim_frames, frame_rate_hz, t0, locked_bin, locked_range_m,
        eca_k_max, eca_ahet_dev, hop_frames,
    )

    radar_df = _build_radar_df(window_results)
    radar_df["masimo_br"] = [
        masimo.reference_br(mas, row["start_epoch"], row["end_epoch"])
        for _, row in radar_df.iterrows()
    ]
    merged = compare.compare(radar_df, mas, min_pi=min_pi)
    _print_table(merged)

    n_nan      = int(radar_df["hr_bpm"].isna().sum())
    n_verified = int(radar_df["ahet_verified"].sum())
    n_suspect  = int(radar_df["harmonic_suspect"].sum())
    n_outlier  = int(radar_df["f_r_outlier"].sum())
    print(f"\nTotal windows: {len(radar_df)}")
    print(f"  AHET verified:       {n_verified}")
    print(f"  Harmonic suspect:    {n_suspect}")
    print(f"  f_r outlier (gated): {n_outlier}")
    print(f"  NaN (no estimate):   {n_nan}  (not fabricated per CLAUDE.md s.4)")
    try:
        m_all = compare.metrics(merged)
        print("\n--- Metrics: all non-NaN windows ---")
        print(json.dumps(m_all, indent=2))
    except ValueError as exc:
        print(f"\n--- Metrics: {exc} ---")

    try:
        merged_verified = merged[merged["ahet_verified"].astype(bool)].copy()
        m_verified = compare.metrics(merged_verified)
        print("\n--- Metrics: AHET-verified windows only ---")
        print(json.dumps(m_verified, indent=2))
    except ValueError:
        pass

    out_dir.mkdir(parents=True, exist_ok=True)
    merged.to_csv(out_dir / "comparison.csv", index=False)
    npz_path = intermediates.write_intermediates_npz(
        out_dir / "intermediates.npz",
        window_results,
        total_cube_frames=total_trimmed_frames,
    )
    print(f"Intermediates -> {npz_path}")

    return window_results, merged


def _condition_coverage(window_results: list[dict], merged: pd.DataFrame) -> dict:
    """Extract coverage counts and error metrics from a condition's results."""
    n_total            = len(window_results)
    n_nan_radar        = int(merged["hr_bpm"].isna().sum())
    n_finite           = n_total - n_nan_radar
    n_nan_ref          = int(merged["masimo_pr_bpm"].isna().sum())
    n_ahet             = int(merged["ahet_verified"].astype(bool).sum())
    n_f_r_outlier      = sum(1 for r in window_results if r.get("f_r_outlier", False))
    n_harmonic_suspect = sum(1 for r in window_results if r.get("harmonic_suspect", False))

    try:
        m    = compare.metrics(merged)
        mae  = m["mae_bpm"]
        rmse = m["rmse_bpm"]
        bias = m["bias_bpm"]
    except (ValueError, KeyError):
        mae = rmse = bias = float("nan")

    return {
        "n_total":            n_total,
        "n_finite":           n_finite,
        "n_nan_radar":        n_nan_radar,
        "n_nan_ref":          n_nan_ref,
        "n_ahet":             n_ahet,
        "n_f_r_outlier":      n_f_r_outlier,
        "n_harmonic_suspect": n_harmonic_suspect,
        "mae":  mae,
        "rmse": rmse,
        "bias": bias,
    }


# ---------------------------------------------------------------------------
# Per-capture runner
# ---------------------------------------------------------------------------

def _run_capture(cap_cfg: dict, shared_cfg: dict, cap_dir: Path) -> dict:
    """Run the full exp004 pipeline for one capture.

    cap_dir is the output root for this capture's artifacts (results_root/<cap_id>).
    Large arrays (cube, range profiles, phase) are explicitly deleted before
    return so the GC can reclaim memory before the next capture loads.
    """
    cap_id = cap_cfg["id"]
    cap_dir.mkdir(parents=True, exist_ok=True)

    r       = shared_cfg["radar"]
    proc    = shared_cfg["processing"]
    eca_cfg = shared_cfg["eca"]
    exp4    = shared_cfg["exp004"]
    min_pi  = shared_cfg["compare"]["min_pi"]
    utc_offset    = shared_cfg["utc_offset_hours"]
    frame_rate_hz = float(r["frame_rate_hz"])
    trim_s        = int(proc["trim_s"])
    trim_frames   = int(trim_s * frame_rate_hz)

    print(f"\n{'=' * 70}")
    print(f"  CAPTURE: {cap_id}  (locked_bin={cap_cfg['locked_bin']}, "
          f"range={cap_cfg['locked_bin_range_m']:.3f} m)")
    print(f"{'=' * 70}")

    # 1. Build ChirpConfig — matches infer_num_frames() API
    chirp_cfg = radar_io.ChirpConfig(
        num_adc_samples      = r["num_adc_samples"],
        num_rx               = r["num_rx"],
        num_tx               = r["num_tx"],
        num_chirps_per_frame = r["num_chirps_per_frame"],
        num_frames           = cap_cfg["expected_frames"],
        frame_rate_hz        = frame_rate_hz,
        range_resolution_m   = r["range_resolution_m"],
    )
    # Handle single-file (bin_file) and split captures (bin_files).
    # infer_num_frames() expects a single path; for multi-file captures we
    # compute total_frames from the summed file sizes directly.
    _bin_spec = cap_cfg.get("bin_files") or cap_cfg.get("bin_file")
    if isinstance(_bin_spec, list):
        bin_path_arg = [REPO_ROOT / p for p in _bin_spec]
        _bpf = (r["num_chirps_per_frame"] * r["num_rx"]
                * r["num_adc_samples"] * 4)
        _total_bytes = sum(Path(p).stat().st_size for p in bin_path_arg)
        if _total_bytes % _bpf != 0:
            raise ValueError(
                f"{cap_id}: combined size {_total_bytes} B is not divisible "
                f"by {_bpf} B/frame (remainder {_total_bytes % _bpf} B)"
            )
        total_frames = _total_bytes // _bpf
        print(
            f"Multi-file ({len(bin_path_arg)} files): {_total_bytes} B total "
            f"-> {total_frames} frames  (config: {cap_cfg['expected_frames']})"
        )
    else:
        bin_path_arg = REPO_ROOT / _bin_spec
        total_frames = radar_io.infer_num_frames(bin_path_arg, chirp_cfg)
    if total_frames != cap_cfg["expected_frames"]:
        msg = (
            f"{cap_id}: inferred {total_frames} frames but expected "
            f"{cap_cfg['expected_frames']}"
        )
        if not exp4.get("allow_frame_count_mismatch", False):
            raise ValueError(msg)
        print(f"WARNING: {msg}")

    # 2. Load capture metadata
    log_info        = radar_io.parse_logfile(REPO_ROOT / cap_cfg["logfile"], utc_offset)
    start_epoch_utc = log_info["start_epoch_utc"]
    end_epoch_utc   = log_info["end_epoch_utc"]
    duration_s      = log_info["duration_s"]
    t0              = start_epoch_utc + trim_s   # UTC epoch at start of analysis window

    print(f"Capture:  {start_epoch_utc} -> {end_epoch_utc} ({duration_s} s)")
    print(f"Trim:     first {trim_s} s skipped ({trim_frames} frames)")
    print(f"Analysis: {t0} -> {end_epoch_utc} ({duration_s - trim_s:.0f} s)")

    # 3. Load Masimo
    mas_df = masimo.load_masimo(REPO_ROOT / cap_cfg["masimo_file"])

    # 4. Masimo summary
    # masimo_summary() expects columns: epoch, PR, BR, PI.
    # load_masimo() produces: epoch_utc, pr_bpm, rr_bpm, pi — rename before calling.
    mas_summary_df = mas_df.rename(columns={
        "epoch_utc": "epoch",
        "pr_bpm":    "PR",
        "rr_bpm":    "BR",
        "pi":        "PI",
    })
    masimo_result = _masimo_summary(
        mas_summary_df,
        trim_start_epoch=int(t0),
        trim_end_epoch=int(end_epoch_utc),
        min_pi=min_pi,
    )
    print(f"\nMasimo summary ({cap_id}):")
    pr_mean = masimo_result["pr"]["mean"]
    pi_frac = masimo_result["pi"]["fraction_above_min_pi"]
    if not math.isnan(pr_mean):
        print(f"  PR: {pr_mean:.1f} ± {masimo_result['pr']['std']:.1f} bpm")
        print(f"  PI: mean={masimo_result['pi']['mean']:.2f}, "
              f"fraction_above_min_pi={pi_frac:.2%}")
    else:
        print("  PR: NaN (no usable samples in analysis window)")
    cov = masimo_result["temporal_coverage_fraction"]
    print(f"  n_unique={masimo_result['n_unique_samples']}, "
          f"n_pi_filtered={masimo_result['n_pi_filtered']}, "
          f"coverage={'NaN' if math.isnan(cov) else f'{cov:.1%}'}")

    # 5. Load radar cube → range profiles → phase
    cube     = radar_io.read_adc_bin(
        bin_path_arg, chirp_cfg, trim_frames=trim_frames
    )
    profiles = radar_io.range_profile(cube)
    raxis    = radar_io.range_axis_m(r["num_adc_samples"], r["range_resolution_m"])
    del cube  # release ~1.5 GB before phase extraction

    profiles_trimmed     = profiles[trim_frames:]
    total_trimmed_frames = profiles_trimmed.shape[0]

    locked_bin     = int(cap_cfg["locked_bin"])
    locked_range_m = float(raxis[locked_bin])
    print(f"Locked bin (config): {locked_bin} ({locked_range_m:.3f} m)")

    # Extract full trimmed phase once — shared across all conditions for this capture
    phase_unwrapped_all = vitals.phase_at_bin(profiles_trimmed, locked_bin)
    phase_clean_all     = vitals.remove_impulse_noise(phase_unwrapped_all)
    # profiles_trimmed is a numpy VIEW of profiles; delete view before base array
    del profiles_trimmed
    del profiles

    eca_k_max    = eca_cfg["k_max"]
    eca_ahet_dev = eca_cfg["ahet_deviation_hz"]

    # 6. Generate window grids
    window_lengths_s      = exp4["window_lengths_s"]
    window_lengths_frames = [int(wl * frame_rate_hz) for wl in window_lengths_s]
    center_spacing_frames = int(exp4["center_spacing_s"] * frame_rate_hz)
    first_center          = trim_frames + int(exp4["first_center_s"] * frame_rate_hz)
    baseline_wf           = int(exp4["baseline_window_s"] * frame_rate_hz)
    baseline_hf           = int(exp4["baseline_hop_s"] * frame_rate_hz)

    common_wins = common_center_windows(
        total_frames               = total_frames,
        trim_frames                = trim_frames,
        window_lengths_frames      = window_lengths_frames,
        center_spacing_frames      = center_spacing_frames,
        first_center_offset_frames = first_center,
    )
    baseline_wins = sliding_windows(
        total_frames  = total_frames,
        trim_frames   = trim_frames,
        window_frames = baseline_wf,
        hop_frames    = baseline_hf,
    )

    print(f"\nBaseline windows: {len(baseline_wins)}")
    for wl_s, wl_f in zip(window_lengths_s, window_lengths_frames):
        print(f"Condition {wl_s}s windows: {len(common_wins[wl_f])}")

    # Shared kwargs passed to every _run_and_save call
    _shared = dict(
        phase_unwrapped_all  = phase_unwrapped_all,
        phase_clean_all      = phase_clean_all,
        trim_frames          = trim_frames,
        frame_rate_hz        = frame_rate_hz,
        t0                   = t0,
        locked_bin           = locked_bin,
        locked_range_m       = locked_range_m,
        eca_k_max            = eca_k_max,
        eca_ahet_dev         = eca_ahet_dev,
        mas                  = mas_df,
        min_pi               = min_pi,
        total_trimmed_frames = total_trimmed_frames,
    )

    # 7. Baseline (20 s sliding grid — reproduces exp002 canonical for cap1)
    baseline_results, baseline_merged = _run_and_save(
        f"BASELINE 20s — {cap_id}",
        baseline_wins,
        hop_frames = baseline_hf,
        out_dir    = cap_dir / "baseline_20s",
        **_shared,
    )

    # 8. Per-window-length common-center conditions
    cond_results: list[tuple[str, int, list, pd.DataFrame, list]] = []
    for wl_s, wl_f in zip(window_lengths_s, window_lengths_frames):
        wins = common_wins[wl_f]
        wins_results, merged_cond = _run_and_save(
            f"CONDITION {wl_s}s — {cap_id}",
            wins,
            hop_frames = center_spacing_frames,
            out_dir    = cap_dir / f"condition_{wl_s}s",
            **_shared,
        )
        cond_results.append((f"{wl_s}s", wl_f, wins, merged_cond, wins_results))

    # Release phase arrays — all conditions processed for this capture
    del _shared
    del phase_unwrapped_all
    del phase_clean_all

    # 9. Paired analysis across window-length conditions
    conditions: dict[str, dict[int, dict]] = {}
    for cond_name, _wl_f, wins, merged_cond, _ in cond_results:
        cond_dict: dict[int, dict] = {}
        for win_idx, (abs_start, abs_end) in enumerate(wins):
            center_frame = (abs_start + abs_end) // 2
            row = merged_cond.iloc[win_idx]
            cond_dict[center_frame] = {
                "radar_hr":      row["hr_bpm"]       if not pd.isna(row["hr_bpm"])       else float("nan"),
                "masimo_pr":     row["masimo_pr_bpm"] if not pd.isna(row["masimo_pr_bpm"]) else float("nan"),
                "error":         float("nan"),  # recomputed inside paired_metrics
                "ahet_verified": bool(row["ahet_verified"]),
            }
        conditions[cond_name] = cond_dict

    print(f"\n{'=' * 60}")
    print(f"  PAIRED ANALYSIS — {cap_id}")
    print(f"{'=' * 60}")
    print(
        "NOTE: AHET pass-rate comparisons across window lengths are indicative\n"
        "only. The ±0.1 Hz search region contains ~5/5/7 bins at 20/25/30 s\n"
        "respectively. Criterion not independently validated.\n"
        "See notes/approach.md for details."
    )

    paired = paired_metrics(conditions, reference_condition="20s")
    print("\n" + coverage_table(paired))
    save_json(paired, cap_dir / "paired_summary.json")

    # 10. Build and return result dict
    per_condition: dict[str, dict] = {
        "baseline": _condition_coverage(baseline_results, baseline_merged),
    }
    for cond_name, _wl_f, _wins, merged_cond, wins_results in cond_results:
        per_condition[cond_name] = _condition_coverage(wins_results, merged_cond)

    return {
        "paired":        paired,
        "per_condition": per_condition,
        "masimo":        masimo_result,
        "cap_cfg":       cap_cfg,
    }


# ---------------------------------------------------------------------------
# Final summary printer
# ---------------------------------------------------------------------------

def print_final_summary(wl_summary: dict, cc_summary: dict) -> None:
    _LINE = "=" * 60

    print(f"\n{_LINE}")
    print("  WINDOW-LENGTH STUDY — POOLED SUMMARY")
    print(_LINE)
    print("Micro-average (length-weighted across all captures):")
    for wl in ("20s", "25s", "30s"):
        m = wl_summary["per_window_length"][wl]["micro"]
        if not math.isnan(m["mae"]):
            print(f"  {wl}: MAE {m['mae']:.2f}, RMSE {m['rmse']:.2f}, "
                  f"bias {m['bias']:.2f} bpm (N={m['n']})")
        else:
            print(f"  {wl}: MAE NaN (N={m['n']})")
    print(f"Trend: {wl_summary['trend']['micro']}")

    print("\nMacro-average (equal capture weighting):")
    for wl in ("20s", "25s", "30s"):
        mac = wl_summary["per_window_length"][wl]["macro"]
        if not math.isnan(mac["mae_mean"]):
            print(f"  {wl}: MAE {mac['mae_mean']:.2f} "
                  f"(range {mac['mae_min']:.2f}–{mac['mae_max']:.2f} bpm)")
        else:
            print(f"  {wl}: MAE NaN")

    if cc_summary:
        print(f"\n{_LINE}")
        print("  CHAIR-CONDITION COMPARISON (descriptive only)")
        print(_LINE)
        print("NOTE: Confounded by distance, recording order, and HR trajectory.")
        print("No causal inference supported.")
        print()
        for wl in ("20s", "25s", "30s"):
            entry = cc_summary["per_window_length"][wl]
            c2    = entry["cap2"]
            c3    = entry["cap3"]
            diff  = entry["difference_cap2_minus_cap3"]["mae"]
            if not math.isnan(c2["mae"]) and not math.isnan(c3["mae"]):
                print(f"  {wl}: cap2 MAE {c2['mae']:.2f} vs cap3 MAE {c3['mae']:.2f} "
                      f"(diff {diff:+.2f} bpm)")
            else:
                print(f"  {wl}: cap2 MAE NaN vs cap3 MAE NaN")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="exp004 window-length study")
    parser.add_argument(
        "--captures", nargs="+", metavar="ID",
        help="Run only the specified capture IDs (default: all in config)"
    )
    args = parser.parse_args()

    config_path = Path(__file__).resolve().parent / "config.yaml"
    cfg = yaml.safe_load(config_path.read_text())
    np.random.seed(cfg.get("seed", 0))

    exp4      = cfg["exp004"]
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    results_root = REPO_ROOT / exp4["results_dir"] / timestamp
    results_root.mkdir(parents=True, exist_ok=True)

    setup_logging(results_root / "stdout.log")

    captures_to_run = cfg["captures"]
    if args.captures:
        captures_to_run = [c for c in captures_to_run if c["id"] in args.captures]
        if not captures_to_run:
            raise ValueError(f"No captures matched: {args.captures}")

    print(f"exp004 window-length study — {timestamp}")
    print(f"Results root: {results_root}")
    print(f"Captures: {[c['id'] for c in captures_to_run]}")

    # Save config snapshot
    (results_root / "config_used.yaml").write_text(config_path.read_text())

    # Process captures sequentially; large arrays freed between each one.
    # Every capture writes to results_root/<cap_id>/ — no special-casing for cap1.
    cap_results: dict[str, dict] = {}
    for cap_cfg in captures_to_run:
        cap_dir = results_root / cap_cfg["id"]
        cap_results[cap_cfg["id"]] = _run_capture(cap_cfg, cfg, cap_dir)

    # Cross-capture analysis
    wl_summary = pooled_window_length_summary(cap_results)
    # Chair comparison only when both original seated captures were run
    cc_summary: dict = {}
    if "cap2" in cap_results and "cap3" in cap_results:
        cc_summary = chair_condition_summary(
            cap_results["cap2"], cap_results["cap3"]
        )

    source_files = (
        [Path(__file__), Path(__file__).parent / "analysis.py",
         Path(__file__).parent / "config.yaml"]
        + list((REPO_ROOT / "src").glob("*.py"))
    )
    provenance = collect_provenance(cfg, results_root, source_files)

    save_json(wl_summary, results_root / "window_length_summary.json")
    save_json(cc_summary, results_root / "chair_condition_summary.json")
    save_json(provenance, results_root / "provenance.json")

    print_final_summary(wl_summary, cc_summary)

    print(f"\nAll results -> {results_root}")


if __name__ == "__main__":
    main()
