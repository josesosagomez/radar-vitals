#!/usr/bin/env python3
"""Diagnostic: distribution of hard-failure metrics across all sessions.

Computes per-frame metrics using the same logic as add_quality_mask.py
(no HDF5 quality_mask needed — works directly on the raw cubes) and
plots their distributions so the current thresholds can be evaluated.

Metrics
-------
max_abs_adc   : max |ADC sample| per frame (clip check).
                Threshold: clip_threshold (default 32767).
norm_energy   : frame energy / session median (dead-frame check).
                Threshold: dead_frame_fraction (default 0.01).

Output
------
figures/diag_hard_failure_thresholds.png  — saved (not shown).
Stdout summary with per-session stats and how many frames each threshold flags.

Run from repo root:
    python -X utf8 scripts/diag_hard_failure_thresholds.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import h5py
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

REPO_ROOT    = Path(__file__).resolve().parents[1]
CUBES_DIR    = REPO_ROOT / "data" / "processed" / "time_domain_cubes"
MANIFEST     = REPO_ROOT / "data" / "manifest.local.csv"
CONFIG_PATH  = REPO_ROOT / "steps" / "step_4" / "config.yaml"
FIGURES_DIR  = REPO_ROOT / "figures"

CHUNK_FRAMES = 200  # HDF5 read chunk — keeps RAM under ~150 MB per chunk


# ---------------------------------------------------------------------------
# Per-session metric computation
# ---------------------------------------------------------------------------

def _compute_session_metrics(
    h5_path: Path,
    trim_frames: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Return (max_abs_adc, norm_energy) for analysis frames of one session.

    Matches _flag_clipping / _flag_dead in add_quality_mask.py exactly.
    Reads the cube in chunks to avoid loading ~800 MB at once.

    max_abs_adc  : float32 (N_analysis,)  max |sample| (float32 view of complex64)
    norm_energy  : float32 (N_analysis,)  frame_energy / session_median_energy
    """
    max_abs_list: list[np.ndarray] = []
    energy_list:  list[np.ndarray] = []

    with h5py.File(h5_path, "r") as f:
        total_frames = int(f.attrs["num_frames"])
        n_analysis   = total_frames - trim_frames
        if n_analysis <= 0:
            raise ValueError(
                f"trim_frames={trim_frames} >= total_frames={total_frames}"
            )

        cube_ds = f["cube"]
        for start in range(0, n_analysis, CHUNK_FRAMES):
            stop  = min(start + CHUNK_FRAMES, n_analysis)
            chunk = cube_ds[trim_frames + start : trim_frames + stop].astype(np.complex64)

            # clip metric: view complex64 as float32 (I, Q interleaved)
            flat_f  = chunk.view(np.float32).reshape(chunk.shape[0], -1)
            max_abs = np.max(np.abs(flat_f), axis=1).astype(np.float32)
            max_abs_list.append(max_abs)

            # energy metric: mean |complex|² per frame
            flat_c = chunk.reshape(chunk.shape[0], -1)
            energy = np.mean(np.abs(flat_c) ** 2, axis=1).astype(np.float32)
            energy_list.append(energy)

    max_abs_adc  = np.concatenate(max_abs_list)
    frame_energy = np.concatenate(energy_list)

    median_e    = float(np.median(frame_energy))
    norm_energy = (
        (frame_energy / median_e).astype(np.float32)
        if median_e > 0
        else frame_energy.copy()
    )
    return max_abs_adc, norm_energy


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    cfg         = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    trim_frames = int(cfg["analysis"]["trim_frames"])
    clip_thresh = float(cfg["hard_failures"]["clip_threshold"])
    dead_frac   = float(cfg["hard_failures"]["dead_frame_fraction"])

    manifest    = pd.read_csv(MANIFEST)
    session_ids = manifest["session_id"].tolist()

    all_max_abs: list[np.ndarray] = []
    all_norm_e:  list[np.ndarray] = []
    session_labels: list[str]     = []
    missing: list[str]            = []

    print(f"Sessions: {len(session_ids)}  |  trim_frames={trim_frames}  "
          f"|  clip_threshold={clip_thresh:.0f}  "
          f"|  dead_frame_fraction={dead_frac}\n")

    for sid in session_ids:
        h5_path = CUBES_DIR / f"{sid}.h5"
        if not h5_path.exists():
            print(f"  SKIP {sid} — HDF5 not found")
            missing.append(sid)
            continue
        print(f"  {sid} … ", end="", flush=True)
        try:
            ma, ne = _compute_session_metrics(h5_path, trim_frames)
            all_max_abs.append(ma)
            all_norm_e.append(ne)
            session_labels.append(sid)
            print(
                f"{len(ma):5d} frames  "
                f"max_abs max={ma.max():.1f}  p99={np.percentile(ma, 99):.1f}  "
                f"norm_e min={ne.min():.2e}  p1={np.percentile(ne, 1):.4f}"
            )
        except Exception as exc:
            print(f"ERROR — {exc}")
            missing.append(sid)

    if not session_labels:
        print("No sessions loaded — nothing to plot.")
        sys.exit(1)

    combined_max_abs = np.concatenate(all_max_abs)
    combined_norm_e  = np.concatenate(all_norm_e)
    total_frames     = len(combined_max_abs)

    # ---- Aggregate statistics ----
    print(f"\n{'='*60}")
    print(f"  max_abs_adc  (clip_threshold = {clip_thresh:.0f})")
    print(f"{'='*60}")
    for p in (50, 90, 95, 99, 99.9, 100):
        print(f"  p{p:5.1f} = {np.percentile(combined_max_abs, p):.2f}")
    n_clip   = int((combined_max_abs >= clip_thresh).sum())
    pct_clip = 100.0 * n_clip / total_frames
    print(f"  Frames flagged by current threshold: {n_clip} / {total_frames} "
          f"({pct_clip:.4f}%)")

    print(f"\n{'='*60}")
    print(f"  norm_energy  (dead_frame_fraction = {dead_frac})")
    print(f"{'='*60}")
    for p in (0, 0.1, 0.5, 1, 2, 5, 50):
        print(f"  p{p:5.1f} = {np.percentile(combined_norm_e, p):.6f}")
    n_dead   = int((combined_norm_e < dead_frac).sum())
    pct_dead = 100.0 * n_dead / total_frames
    print(f"  Frames flagged by current threshold: {n_dead} / {total_frames} "
          f"({pct_dead:.4f}%)")

    # ---- Plot ----
    cmap   = plt.get_cmap("tab10")
    colors = [cmap(i % 10) for i in range(len(session_labels))]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 5))
    fig.suptitle(
        f"Hard-failure metric distributions — {len(session_labels)} sessions, "
        f"{total_frames:,} analysis frames  (trim={trim_frames} frames each)",
        fontsize=11,
    )

    # -- Panel 1: max_abs_adc --
    bins_abs = np.linspace(0, 32768, 300)
    for sid, arr, c in zip(session_labels, all_max_abs, colors):
        ax1.hist(arr, bins=bins_abs, alpha=0.45, color=c, label=sid, density=True)
    ax1.axvline(
        clip_thresh, color="red", linewidth=1.8, linestyle="--",
        label=f"clip_threshold = {clip_thresh:.0f}  ({n_clip} frames flagged)",
    )
    # Annotate observed maximum
    obs_max = float(combined_max_abs.max())
    ax1.axvline(obs_max, color="orange", linewidth=1.2, linestyle=":",
                label=f"observed max = {obs_max:.1f}")
    ax1.set_xlabel("max |ADC sample| per frame (float32 view of complex64)")
    ax1.set_ylabel("Density")
    ax1.set_title("Clip check — max_abs_adc")
    ax1.set_yscale("log")
    ax1.legend(fontsize=7, ncol=2)

    # Inset: tail of distribution (top 1% of observed range)
    tail_lo = np.percentile(combined_max_abs, 99)
    if tail_lo < obs_max:
        ax1_in = ax1.inset_axes([0.42, 0.42, 0.55, 0.50])
        bins_tail = np.linspace(tail_lo, max(obs_max * 1.01, tail_lo + 1), 80)
        for arr, c in zip(all_max_abs, colors):
            ax1_in.hist(arr, bins=bins_tail, alpha=0.55, color=c, density=True)
        ax1_in.axvline(clip_thresh, color="red", linewidth=1.2, linestyle="--")
        ax1_in.axvline(obs_max, color="orange", linewidth=1.0, linestyle=":")
        ax1_in.set_title(f"tail  (>{tail_lo:.0f})", fontsize=8)
        ax1_in.tick_params(labelsize=7)
        ax1_in.set_yscale("log")

    # -- Panel 2: normalised energy (log x-scale) --
    pos_vals = combined_norm_e[combined_norm_e > 0]
    log_lo   = max(np.floor(np.log10(pos_vals.min())) - 0.5, -8) if len(pos_vals) else -8
    bins_ne  = np.logspace(log_lo, np.ceil(np.log10(combined_norm_e.max()) + 0.2), 250)

    for sid, arr, c in zip(session_labels, all_norm_e, colors):
        ax2.hist(arr, bins=bins_ne, alpha=0.45, color=c, label=sid, density=True)
    ax2.axvline(
        dead_frac, color="red", linewidth=1.8, linestyle="--",
        label=f"dead_frame_fraction = {dead_frac}  ({n_dead} frames flagged)",
    )
    ax2.set_xscale("log")
    ax2.set_yscale("log")
    ax2.set_xlabel("frame energy / session median energy")
    ax2.set_ylabel("Density")
    ax2.set_title("Dead-frame check — normalised energy")
    ax2.legend(fontsize=7, ncol=2)

    fig.tight_layout()
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = FIGURES_DIR / "diag_hard_failure_thresholds.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"\nFigure saved -> {out_path}")
    if missing:
        print(f"Skipped sessions (file not found or error): {missing}")


if __name__ == "__main__":
    main()
