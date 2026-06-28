#!/usr/bin/env python3
"""Step 3 - Select the chest range bin from time-domain HDF5 cubes.

Reads each session's HDF5 cube (written by Step 2), applies the
stationary-interval frame window, then runs a 3-method consensus ranking to
pick the range bin that best corresponds to the subject's chest wall.

Methods
-------
  1. Mean range-profile energy (dB)         - strongest reflector
  2. Slow-time phase standard deviation      - most variable phase (breathing)
  3. Respiratory-band phase SNR              - spectral peak in resp_band Hz

A bin is qualified when it appears in the top-N of >= 2 methods.
Confidence: high (3/3 agree), medium (2/3 agree), low (fallback).

Outputs per session (results/<session_id>/step_3/):
  step_3.log               - diagnostic log (overwritten each run)
  selection.json           - main results: locked_bin, confidence, etc.
  ranking.csv              - full consensus ranking table
  mean_range_profile.png   - mean profile with selected bin marked
  range_heatmap.png        - time x range heatmap (stationary frames only)
  config_used.yaml         - config snapshot used for this run
  manifest_row_before.json - manifest row state before any write-back
  provenance.json          - git commit, script path, UTC timestamp

Run from repo root:
    python -X utf8 steps/step_3/select_chest_bin.py --session exp003
    python -X utf8 steps/step_3/select_chest_bin.py --all
    python -X utf8 steps/step_3/select_chest_bin.py --session exp003 --no-write
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import h5py
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

_CONFIDENCE_ORDER = {"high": 2, "medium": 1, "low": 0}


# ---------------------------------------------------------------------------
# DSP helpers (duplicated from steps/range_plot/run.py — refactor deferred)
# ---------------------------------------------------------------------------

def _build_range_profiles(
    cube: np.ndarray,
    range_resolution_m: float,
    max_range_m: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Non-coherent range profiles from a windowed FFT.

    cube: (frames, chirps, samples, rx) — already transposed by caller.
    Returns (rp_db, range_m): rp_db is (frames, n_bins), range_m is (n_bins,).
    """
    n_samples = cube.shape[2]
    win = np.hanning(n_samples).reshape(1, 1, n_samples, 1)
    rp = np.fft.fft(cube * win, axis=2)
    power = np.abs(rp) ** 2
    nci = power.mean(axis=1).mean(axis=-1)      # avg chirps then RX -> (frames, bins)
    eps = 1e-10
    rp_db = 10.0 * np.log10(nci + eps)
    n_pos = n_samples // 2
    rp_db = rp_db[:, :n_pos]
    range_m_full = np.arange(n_pos) * range_resolution_m
    clip_mask = range_m_full <= max_range_m
    return rp_db[:, clip_mask], range_m_full[clip_mask]


def _phase_metrics_per_bin(
    cube: np.ndarray,
    frame_rate_hz: float,
    resp_lo_hz: float = 0.10,
    resp_hi_hz: float = 0.50,
) -> tuple[np.ndarray, np.ndarray]:
    """Phase std and respiratory-band SNR per positive range bin.

    cube: (frames, chirps, samples, rx)
    Returns (phase_std, resp_snr), each (n_pos,).
    """
    n_pos = cube.shape[2] // 2
    n_samples = cube.shape[2]
    win = np.hanning(n_samples).reshape(1, 1, n_samples, 1)
    fft_data = np.fft.fft(cube * win, axis=2)[:, :, :n_pos, :]
    mean_complex = fft_data.mean(axis=1).mean(axis=-1)   # (frames, n_pos)
    phase_uw = np.unwrap(np.angle(mean_complex), axis=0)
    phase_std = phase_uw.std(axis=0)

    n_frames = phase_uw.shape[0]
    t = np.arange(n_frames, dtype=float)
    coeffs = np.polyfit(t, phase_uw, 1)
    phase_dt = phase_uw - (t[:, None] * coeffs[0] + coeffs[1])
    spec = np.abs(np.fft.rfft(phase_dt, axis=0)) ** 2
    freqs = np.fft.rfftfreq(n_frames, d=1.0 / frame_rate_hz)
    resp_mask = (freqs >= resp_lo_hz) & (freqs <= resp_hi_hz)
    if resp_mask.sum() == 0:
        resp_snr = np.zeros(n_pos)
    else:
        resp_peak = spec[resp_mask].max(axis=0)
        noise_floor = np.maximum(np.median(spec, axis=0), 1e-12)
        resp_snr = 10.0 * np.log10(resp_peak / noise_floor)
    return phase_std, resp_snr


def _rank_bins(
    mean_profile_db: np.ndarray,
    phase_std: np.ndarray,
    resp_snr: np.ndarray,
    range_m: np.ndarray,
    search_min_m: float,
    search_max_m: float,
    top_n: int = 5,
) -> tuple[list[dict], int, str]:
    """3-method consensus ranking inside the search window.

    Returns (entries, recommended_bin, agree_note).
    recommended_bin is the range-FFT bin index (== array index in range_m).
    """
    mask = (range_m >= search_min_m) & (range_m <= search_max_m)
    idx = np.where(mask)[0]
    if len(idx) == 0:
        raise ValueError(
            f"No range bins in [{search_min_m:.2f}, {search_max_m:.2f}] m."
        )
    top_e  = idx[np.argsort(-mean_profile_db[idx])[:top_n]]
    top_pv = idx[np.argsort(-phase_std[idx])[:top_n]]
    top_rs = idx[np.argsort(-resp_snr[idx])[:top_n]]
    e_rank  = {int(b): i + 1 for i, b in enumerate(top_e)}
    pv_rank = {int(b): i + 1 for i, b in enumerate(top_pv)}
    rs_rank = {int(b): i + 1 for i, b in enumerate(top_rs)}
    penalty    = top_n + 1
    candidates = set(e_rank) | set(pv_rank) | set(rs_rank)
    combined   = {
        b: e_rank.get(b, penalty) + pv_rank.get(b, penalty) + rs_rank.get(b, penalty)
        for b in candidates
    }
    sorted_bins = sorted(candidates, key=lambda b: combined[b])[:top_n]
    entries = []
    for rank, b in enumerate(sorted_bins, 1):
        n_methods = sum([b in e_rank, b in pv_rank, b in rs_rank])
        entries.append({
            "rank":          rank,
            "bin_idx":       b,
            "range_m":       float(range_m[b]),
            "energy_db":     float(mean_profile_db[b]),
            "energy_rank":   e_rank.get(b, None),
            "phase_std_rad": float(phase_std[b]),
            "phase_rank":    pv_rank.get(b, None),
            "resp_snr_db":   float(resp_snr[b]),
            "resp_rank":     rs_rank.get(b, None),
            "n_methods":     n_methods,
        })
    qualified = [e for e in entries if e["n_methods"] >= 2]
    if qualified:
        # Sort qualified bins by: most methods first, then resp-supported first
        # (phase_std alone can be elevated by sidelobes/artifacts, so prefer bins
        # with direct respiratory spectral evidence), then existing combined-rank.
        qualified_sorted = sorted(
            qualified,
            key=lambda e: (
                -e["n_methods"],
                0 if e["resp_rank"] is not None else 1,
                e["rank"],
            ),
        )
        best = qualified_sorted[0]
        recommended_bin = best["bin_idx"]
        b = recommended_bin
        if best["n_methods"] == 3:
            agree_note = "all 3 methods agree"
        else:
            which = (
                (["energy"] if b in e_rank else [])
                + (["phase"]  if b in pv_rank else [])
                + (["resp"]   if b in rs_rank else [])
            )
            agree_note = f"{' + '.join(which)} agree"
    else:
        recommended_bin = sorted_bins[0]
        agree_note = "fallback (no 2-method overlap)"
    return entries, recommended_bin, agree_note


def _confidence_from_entries(
    entries: list[dict], recommended_bin: int
) -> tuple[str, str]:
    """Return (confidence_level, methods_str) for the recommended bin."""
    for e in entries:
        if e["bin_idx"] == recommended_bin:
            n = e["n_methods"]
            which = []
            if e["energy_rank"] is not None:
                which.append("energy")
            if e["phase_rank"] is not None:
                which.append("phase")
            if e["resp_rank"] is not None:
                which.append("resp")
            methods_str = "+".join(which)
            if n >= 3:
                return "high", methods_str
            elif n == 2:
                return "medium", methods_str
            else:
                return "low", methods_str
    return "low", ""


# ---------------------------------------------------------------------------
# Stationary interval helpers
# ---------------------------------------------------------------------------

def _parse_intervals(
    interval_str: str, frame_rate_hz: float, n_frames: int
) -> list[tuple[int, int]]:
    """Parse "start_s-end_s[,start_s-end_s...]" into (start_frame, end_frame) tuples."""
    intervals = []
    for part in str(interval_str).split(","):
        part = part.strip()
        if not part:
            continue
        lo_s, hi_s = part.split("-", 1)
        start_f = int(float(lo_s) * frame_rate_hz)
        end_f   = int(float(hi_s) * frame_rate_hz)
        start_f = max(0, min(start_f, n_frames))
        end_f   = max(0, min(end_f,   n_frames))
        if start_f < end_f:
            intervals.append((start_f, end_f))
    return intervals


def _apply_intervals(cube: np.ndarray, intervals: list[tuple[int, int]]) -> np.ndarray:
    return np.concatenate([cube[s:e] for s, e in intervals], axis=0)


# ---------------------------------------------------------------------------
# Plotting helpers
# ---------------------------------------------------------------------------

def _plot_mean_profile(
    mean_profile: np.ndarray,
    range_m: np.ndarray,
    recommended_bin: int,
    range_resolution_m: float,
    search_min_m: float,
    search_max_m: float,
    max_range_m: float,
    out_path: Path,
    distance_m: float | None = None,
    agree_note: str = "",
) -> None:
    rec_range_m = recommended_bin * range_resolution_m
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(range_m, mean_profile, linewidth=1.0, color="steelblue")
    ax.axvspan(search_min_m, search_max_m, alpha=0.10, color="orange",
               label=f"search [{search_min_m:.2f}-{search_max_m:.2f} m]")
    ax.axvline(x=rec_range_m, color="red", linestyle="--", linewidth=1.8,
               label=f"bin {recommended_bin} ({rec_range_m:.3f} m) [{agree_note}]")
    if distance_m is not None:
        ax.axvline(x=distance_m, color="gray", linestyle=":", linewidth=1.0,
                   label=f"tape ({distance_m:.2f} m)")
    ax.set_xlabel("Range (m)")
    ax.set_ylabel("Magnitude (dB)")
    ax.set_title("Mean range profile (stationary frames only)")
    ax.set_xlim(0, max_range_m)
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.4)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def _plot_heatmap(
    rp_db: np.ndarray,
    range_m: np.ndarray,
    frame_rate_hz: float,
    recommended_bin: int,
    range_resolution_m: float,
    search_min_m: float,
    search_max_m: float,
    out_path: Path,
    distance_m: float | None = None,
) -> None:
    n_frames = rp_db.shape[0]
    total_s = (n_frames - 1) / frame_rate_hz
    rec_range_m = recommended_bin * range_resolution_m
    fig, ax = plt.subplots(figsize=(12, 7))
    extent = [float(range_m[0]), float(range_m[-1]), total_s, 0.0]
    im = ax.imshow(rp_db, aspect="auto", extent=extent, cmap="viridis", origin="upper")
    ax.axvspan(search_min_m, search_max_m, alpha=0.08, color="white",
               label=f"search [{search_min_m:.2f}-{search_max_m:.2f} m]")
    ax.axvline(x=rec_range_m, color="red", linestyle="--", linewidth=1.2,
               label=f"bin {recommended_bin} ({rec_range_m:.3f} m)")
    if distance_m is not None:
        ax.axvline(x=distance_m, color="yellow", linestyle=":", linewidth=0.9,
                   label=f"tape ({distance_m:.2f} m)")
    ax.set_xlabel("Range (m)")
    ax.set_ylabel("Time (s) [stationary frames]")
    ax.set_title("Range heatmap (stationary frames only)")
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("Magnitude (dB)")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Git / provenance
# ---------------------------------------------------------------------------

def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

_MANIFEST_STEP3_COLS = [
    "locked_bin",
    "locked_range_m",
    "radar_chest_distance_cm",
    "chest_bin_confidence",
    "chest_bin_methods",
    "chest_bin_review_required",
    "chest_bin_run_id",
    "highest_bins",
]


def _atomic_manifest_update(
    manifest_path: Path, session_id: str, updates: dict
) -> None:
    """Read -> update one row -> write via temp file + rename (atomic on same volume)."""
    df = pd.read_csv(manifest_path, keep_default_na=False)
    for col in _MANIFEST_STEP3_COLS:
        if col not in df.columns:
            df[col] = ""
    idx_list = df.index[df["session_id"] == session_id].tolist()
    if not idx_list:
        raise ValueError(
            f"Session '{session_id}' not found in manifest during write-back."
        )
    i = idx_list[0]
    for col, val in updates.items():
        df.at[i, col] = val
    tmp_path = manifest_path.with_suffix(".tmp")
    df.to_csv(tmp_path, index=False)
    check = pd.read_csv(tmp_path, keep_default_na=False)
    if len(check) != len(df):
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(
            "Manifest temp-write validation failed (row count mismatch). "
            "Original manifest is untouched."
        )
    tmp_path.replace(manifest_path)


# ---------------------------------------------------------------------------
# Per-session runner
# ---------------------------------------------------------------------------

def _process_session(
    session_id: str,
    row: pd.Series,
    cfg: dict,
    cubes_dir: Path,
    commit: str,
) -> dict:
    """Run bin selection for one session. Returns result dict."""
    h5_path = cubes_dir / f"{session_id}.h5"
    if not h5_path.exists():
        raise FileNotFoundError(f"HDF5 not found: {h5_path}  (run Step 2 first)")

    with h5py.File(h5_path, "r") as f:
        cube = f["cube"][:]                             # (frames, chirps, rx, adc_samples)
        range_resolution_m = float(f.attrs["range_resolution_m"])
        frame_rate_hz      = float(f.attrs["frame_rate_hz"])

    # HDF5 canonical shape is (frames, chirps, rx, adc_samples);
    # DSP helpers expect (frames, chirps, samples, rx).
    cube = cube.transpose(0, 1, 3, 2)

    n_frames = cube.shape[0]
    interval_str = str(row.get("stationary_intervals", "")).strip()
    if not interval_str:
        raise ValueError(
            f"'stationary_intervals' is blank for '{session_id}'. "
            "Fill it in the manifest before running Step 3."
        )
    intervals = _parse_intervals(interval_str, frame_rate_hz, n_frames)
    if not intervals:
        raise ValueError(
            f"No valid intervals parsed from '{interval_str}' for '{session_id}'."
        )
    cube_stat = _apply_intervals(cube, intervals)
    del cube
    n_stat = cube_stat.shape[0]

    search_min_m = float(cfg["search"]["min_m"])
    search_max_m = float(cfg["search"]["max_m"])
    top_n        = int(cfg["search"]["top_n"])
    resp_lo_hz   = float(cfg["resp_band"]["lo_hz"])
    resp_hi_hz   = float(cfg["resp_band"]["hi_hz"])
    max_range_m  = float(cfg["plot"]["max_range_m"])
    tape_thr_m   = float(cfg["tape_threshold_m"])

    rp_db, range_m = _build_range_profiles(cube_stat, range_resolution_m, max_range_m)
    mean_profile_db = rp_db.mean(axis=0)

    phase_std, resp_snr = _phase_metrics_per_bin(
        cube_stat, frame_rate_hz, resp_lo_hz, resp_hi_hz
    )
    phase_std = phase_std[:len(range_m)]
    resp_snr  = resp_snr[:len(range_m)]

    entries, recommended_bin, agree_note = _rank_bins(
        mean_profile_db, phase_std, resp_snr, range_m,
        search_min_m, search_max_m, top_n,
    )

    confidence, methods_str = _confidence_from_entries(entries, recommended_bin)
    locked_range_m  = float(recommended_bin * range_resolution_m)
    radar_chest_cm  = round(locked_range_m * 100)
    distance_cm_val = row.get("distance_cm")
    distance_m      = (
        float(distance_cm_val) / 100.0
        if pd.notna(distance_cm_val) and str(distance_cm_val).strip() != ""
        else None
    )
    tape_delta_m = abs(locked_range_m - distance_m) if distance_m is not None else None
    review_required = (
        confidence == "low"
        or (tape_delta_m is not None and tape_delta_m > tape_thr_m)
    )
    highest_bins_str = ",".join(str(e["bin_idx"]) for e in entries[1:])

    # --- create output directory ---
    ts      = time.strftime("%Y%m%d_%H%M%S")
    run_id  = f"{session_id}_{ts}"
    run_dir = REPO_ROOT / "results" / session_id / "step_3"
    run_dir.mkdir(parents=True, exist_ok=True)

    # selection.json
    selection = {
        "session_id":               session_id,
        "locked_bin":               recommended_bin,
        "locked_range_m":           locked_range_m,
        "radar_chest_distance_cm":  radar_chest_cm,
        "chest_bin_confidence":     confidence,
        "chest_bin_methods":        methods_str,
        "agree_note":               agree_note,
        "chest_bin_review_required": review_required,
        "tape_distance_m":          distance_m,
        "tape_delta_m":             tape_delta_m,
        "n_total_frames":            n_frames,
        "n_stationary_frames":      n_stat,
        "stationary_intervals":     interval_str,
        "range_resolution_m":       range_resolution_m,
        "frame_rate_hz":            frame_rate_hz,
        "highest_bins":             highest_bins_str,
        "run_id":                   run_id,
        "git_commit":               commit,
        "entries":                  entries,
    }
    (run_dir / "selection.json").write_text(
        json.dumps(selection, indent=2, default=str), encoding="utf-8"
    )

    # ranking.csv
    pd.DataFrame(entries).to_csv(run_dir / "ranking.csv", index=False)

    # plots
    _plot_mean_profile(
        mean_profile_db, range_m, recommended_bin, range_resolution_m,
        search_min_m, search_max_m, max_range_m,
        run_dir / "mean_range_profile.png",
        distance_m=distance_m,
        agree_note=agree_note,
    )
    _plot_heatmap(
        rp_db, range_m, frame_rate_hz, recommended_bin, range_resolution_m,
        search_min_m, search_max_m,
        run_dir / "range_heatmap.png",
        distance_m=distance_m,
    )

    # config snapshot
    (run_dir / "config_used.yaml").write_text(
        yaml.dump(cfg, default_flow_style=False, allow_unicode=True), encoding="utf-8"
    )

    # manifest row before
    row_dict = {k: (None if pd.isna(v) else v) for k, v in row.items()}
    (run_dir / "manifest_row_before.json").write_text(
        json.dumps(row_dict, indent=2, default=str), encoding="utf-8"
    )

    # provenance
    (run_dir / "provenance.json").write_text(
        json.dumps({
            "git_commit":    commit,
            "script":        str(Path(__file__).resolve()),
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "run_id":        run_id,
        }, indent=2),
        encoding="utf-8",
    )

    # --- console summary ---
    print(f"  Selected:   bin {recommended_bin}  ({locked_range_m:.3f} m)  [{agree_note}]")
    print(f"  Confidence: {confidence}  methods={methods_str}")
    if tape_delta_m is not None:
        flag = "  -- WARNING: delta > threshold" if tape_delta_m > tape_thr_m else ""
        print(f"  Tape:       {distance_m:.2f} m  delta={tape_delta_m:.3f} m{flag}")
    if review_required:
        print("  REVIEW REQUIRED (low confidence or large tape delta)")
    print(f"  Stationary: {n_stat} frames from '{interval_str}'")
    print(f"  Run dir:    {run_dir}")

    selection["_run_dir"] = run_dir
    return selection


# ---------------------------------------------------------------------------
# Diagnostic log
# ---------------------------------------------------------------------------

def _write_diagnostic_log(
    session_id: str,
    result: dict,
    cfg: dict,
    *,
    no_write: bool,
    lock_fields_written: bool,
    fields_written: list[str],
) -> None:
    """Write results/<session_id>/step_3/step_3.log (overwritten each run)."""
    log_dir = REPO_ROOT / "results" / session_id / "step_3"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "step_3.log"

    frame_rate_hz      = result["frame_rate_hz"]
    range_resolution_m = result["range_resolution_m"]
    tape_thr_m         = float(cfg["tape_threshold_m"])
    entries            = result["entries"]

    lines: list[str] = []
    SEP = "=" * 72

    lines += [
        SEP,
        f"Step 3 diagnostic log -- {session_id}",
        f"Generated (UTC): {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        f"Git commit:      {result['git_commit']}",
        f"Run ID:          {result['run_id']}",
        SEP,
        "",
        "SESSION",
        f"  session_id          : {session_id}",
        f"  total frames        : {result['n_total_frames']}  "
        f"({result['n_total_frames'] / frame_rate_hz:.1f} s at {frame_rate_hz:.0f} Hz)",
        f"  stationary_intervals: {result['stationary_intervals']}",
        f"  stationary frames   : {result['n_stationary_frames']}  "
        f"({result['n_stationary_frames'] / frame_rate_hz:.1f} s)",
        "",
        "RADAR (from HDF5 attrs)",
        f"  range_resolution_m  : {range_resolution_m:.4f} m/bin",
        f"  frame_rate_hz       : {frame_rate_hz:.1f} Hz",
        "",
        "SEARCH WINDOW",
        f"  min_m               : {cfg['search']['min_m']} m",
        f"  max_m               : {cfg['search']['max_m']} m",
        f"  top_n               : {cfg['search']['top_n']}",
        f"  resp_band           : {cfg['resp_band']['lo_hz']}-{cfg['resp_band']['hi_hz']} Hz",
        "",
        "RANKING TABLE",
        f"  {'Rank':>4}  {'Bin':>4}  {'Range(m)':>8}  "
        f"{'E.rank':>6}  {'Energy(dB)':>10}  "
        f"{'P.rank':>6}  {'Phase_s(rad)':>12}  "
        f"{'R.rank':>6}  {'RespSNR(dB)':>11}  {'n/3':>3}",
        f"  {'----':>4}  {'----':>4}  {'--------':>8}  "
        f"{'------':>6}  {'----------':>10}  "
        f"{'------':>6}  {'------------':>12}  "
        f"{'------':>6}  {'-----------':>11}  {'---':>3}",
    ]
    for e in entries:
        er = f"{e['energy_rank']:>6}" if e["energy_rank"] is not None else f"{'--':>6}"
        pr = f"{e['phase_rank']:>6}"  if e["phase_rank"]  is not None else f"{'--':>6}"
        rr = f"{e['resp_rank']:>6}"   if e["resp_rank"]   is not None else f"{'--':>6}"
        tag = " <--" if e["bin_idx"] == result["locked_bin"] else ""
        lines.append(
            f"  {e['rank']:>4}  {e['bin_idx']:>4}  {e['range_m']:>8.3f}  "
            f"{er}  {e['energy_db']:>10.1f}  "
            f"{pr}  {e['phase_std_rad']:>12.2f}  "
            f"{rr}  {e['resp_snr_db']:>11.1f}  {e['n_methods']:>3}{tag}"
        )

    tape_m    = result["tape_distance_m"]
    delta_m   = result["tape_delta_m"]
    if tape_m is not None:
        tape_flag = (
            "  WARNING: exceeds threshold" if delta_m > tape_thr_m else "  OK"
        )
        tape_lines = [
            f"  tape_distance_m     : {tape_m:.3f} m  (manifest distance_cm)",
            f"  tape_delta_m        : {delta_m:.3f} m{tape_flag}",
            f"  tape_threshold_m    : {tape_thr_m:.3f} m",
        ]
    else:
        tape_lines = ["  tape_distance_m     : N/A (distance_cm not in manifest)"]

    if no_write:
        manifest_lines = ["  --no-write flag set; manifest not touched."]
    elif not fields_written:
        manifest_lines = ["  No fields written (unexpected state)."]
    else:
        manifest_lines = [
            f"  lock_fields_written : {lock_fields_written}",
            f"  fields_written      : {', '.join(fields_written)}",
        ]
        if not lock_fields_written:
            manifest_lines.append(
                f"  (locked_bin/locked_range_m/radar_chest_distance_cm skipped -- "
                f"confidence below write_min_confidence)"
            )

    lines += [
        "",
        "SELECTION",
        f"  recommended_bin     : {result['locked_bin']}",
        f"  locked_range_m      : {result['locked_range_m']:.4f} m",
        f"  agree_note          : {result['agree_note']}",
        f"  confidence          : {result['chest_bin_confidence']}",
        f"  methods             : {result['chest_bin_methods']}",
        f"  review_required     : {result['chest_bin_review_required']}",
        "",
        "TAPE DISTANCE",
    ] + tape_lines + [
        "",
        "MANIFEST WRITE",
    ] + manifest_lines + [
        "",
        SEP,
    ]

    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Diagnostic log: {log_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Step 3: Select chest range bin from HDF5 cubes"
    )
    parser.add_argument(
        "--config", default="steps/step_3/config.yaml",
        help="Path to config.yaml (default: steps/step_3/config.yaml)"
    )
    parser.add_argument("--session", metavar="EXPID",
                        help="Single session ID from manifest (e.g. exp003)")
    parser.add_argument("--all", action="store_true",
                        help="Process all non-excluded sessions in manifest")
    parser.add_argument("--no-write", action="store_true",
                        help="Run analysis and save outputs but do not update manifest")
    parser.add_argument("--allow-low-confidence-write", action="store_true",
                        help="Write locked_bin to manifest even when confidence is 'low'")
    args = parser.parse_args()

    if not args.session and not args.all:
        parser.print_help()
        sys.exit(0)

    cfg_path = REPO_ROOT / args.config
    if not cfg_path.exists():
        sys.exit(f"ERROR: config not found: {cfg_path}")
    cfg = yaml.safe_load(cfg_path.read_text())

    np.random.seed(int(cfg.get("seed", 42)))

    paths         = cfg["paths"]
    manifest_path = REPO_ROOT / paths["manifest"]
    cubes_dir     = REPO_ROOT / paths["cubes_dir"]

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    commit   = _git_commit()

    write_min_confidence = cfg.get("write_policy", {}).get("write_min_confidence", "medium")
    if args.allow_low_confidence_write:
        write_min_confidence = "low"

    session_ids = [args.session] if args.session else manifest["session_id"].tolist()

    SEP = "=" * 60
    failed  = []
    skipped = []
    for sid in session_ids:
        print(f"\n{SEP}\n  {sid}\n{SEP}")
        rows = manifest[manifest["session_id"] == sid]
        if rows.empty:
            print(f"  ERROR: '{sid}' not found in manifest.")
            failed.append(sid)
            continue

        row = rows.iloc[0]
        exclusion = str(row.get("exclusion_reason", "")).strip()
        if exclusion:
            if args.all:
                print(f"  SKIPPED (excluded): {exclusion}")
                skipped.append(sid)
                continue
            else:
                print(f"  WARNING: session excluded in manifest: {exclusion}")

        h5_path = cubes_dir / f"{sid}.h5"
        if not h5_path.exists():
            if args.all:
                print(f"  SKIPPED (no HDF5 cube): {h5_path.name}  -- run Step 2 first")
                skipped.append(sid)
                continue
            else:
                print(f"  ERROR: HDF5 not found: {h5_path}  -- run Step 2 first")
                failed.append(sid)
                continue

        try:
            result = _process_session(sid, row, cfg, cubes_dir, commit)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            failed.append(sid)
            continue

        # Manifest write-back
        # Audit fields are always written (unless --no-write) so a low-confidence
        # run is still traceable in the manifest.
        # Lock fields (locked_bin, locked_range_m, radar_chest_distance_cm) are
        # only written when confidence meets the threshold.
        confidence     = result["chest_bin_confidence"]
        lock_fields_ok = (
            _CONFIDENCE_ORDER.get(confidence, 0)
            >= _CONFIDENCE_ORDER.get(write_min_confidence, 1)
        )
        fields_written: list[str] = []
        if args.no_write:
            print("  Manifest write skipped (--no-write)")
        else:
            updates: dict = {
                "chest_bin_confidence":      confidence,
                "chest_bin_methods":         result["chest_bin_methods"],
                "chest_bin_review_required": str(result["chest_bin_review_required"]),
                "chest_bin_run_id":          result["run_id"],
                "highest_bins":              result["highest_bins"],
            }
            if lock_fields_ok:
                updates["locked_bin"]              = result["locked_bin"]
                updates["locked_range_m"]          = f"{result['locked_range_m']:.4f}"
                updates["radar_chest_distance_cm"] = result["radar_chest_distance_cm"]
            else:
                print(
                    f"  Lock fields NOT written: confidence={confidence!r} < "
                    f"write_min_confidence={write_min_confidence!r} "
                    f"(use --allow-low-confidence-write to override)"
                )
            try:
                _atomic_manifest_update(manifest_path, sid, updates)
                manifest = pd.read_csv(manifest_path, keep_default_na=False)
                fields_written = list(updates.keys())
                lock_note = (
                    f"  locked_bin={updates['locked_bin']}" if lock_fields_ok
                    else "  locked_bin NOT written"
                )
                print(
                    f"  Manifest updated: confidence={confidence}"
                    f"  run_id={updates['chest_bin_run_id']}{lock_note}"
                )
            except Exception as exc:
                print(f"  ERROR writing manifest: {exc}")
                failed.append(sid)
                continue

        try:
            _write_diagnostic_log(
                sid, result, cfg,
                no_write=args.no_write,
                lock_fields_written=lock_fields_ok if not args.no_write else False,
                fields_written=fields_written,
            )
        except Exception as exc:
            print(f"  WARNING: diagnostic log failed: {exc}")

    processed = len(session_ids) - len(failed) - len(skipped)
    print(f"\n{SEP}")
    print(f"  Done: {processed}/{len(session_ids)} processed  "
          f"skipped={len(skipped)}  failed={len(failed)}")
    if failed:
        print(f"  Failed:  {failed}")
    if skipped:
        print(f"  Skipped: {skipped}")
    print(SEP)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
