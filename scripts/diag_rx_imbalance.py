#!/usr/bin/env python3
"""Diagnostic: RX channel energy imbalance distribution across all sessions.

Computes per-frame rx_energy_ratio = max(channel_energy) / min(channel_energy)
using the same logic as add_quality_mask.py, without needing a pre-existing
quality mask. Plots the distribution to guide threshold selection.

Run from repo root:
    python -X utf8 scripts/diag_rx_imbalance.py
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
CONFIG_PATH  = Path(__file__).resolve().parent / "quality_mask_config.yaml"
FIGURES_DIR  = REPO_ROOT / "figures"

CHUNK_FRAMES = 200


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------

def _compute_rx_ratio(h5_path: Path, trim_frames: int) -> np.ndarray:
    """Return per-frame rx_energy_ratio for one session.

    rx_energy_ratio[i] = max(channel_energy) / min(channel_energy)
    where channel_energy[rx] = mean(|sample|²) over chirps and ADC samples.

    Matches _flag_rx_imbalance() in add_quality_mask.py exactly.
    Returns float32 array of shape (N_analysis,). Frames where min channel
    energy is zero get ratio = inf.
    """
    ratio_list: list[np.ndarray] = []

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
            # shape: (chunk, chirps, rx, adc_samples)
            chunk = cube_ds[trim_frames + start : trim_frames + stop].astype(np.complex64)

            # mean |sample|² over chirps (axis=1) and ADC samples (axis=3)
            # result shape: (chunk, rx)
            energy_per_rx = np.mean(np.abs(chunk) ** 2, axis=(1, 3))

            max_e = energy_per_rx.max(axis=1)   # (chunk,)
            min_e = energy_per_rx.min(axis=1)   # (chunk,)

            with np.errstate(divide="ignore", invalid="ignore"):
                rx_ratio = np.where(
                    min_e > 0, max_e / min_e, np.inf
                ).astype(np.float32)

            ratio_list.append(rx_ratio)

    return np.concatenate(ratio_list)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    cfg         = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    trim_frames = int(cfg["trim_frames"])
    rx_cfg      = cfg.get("soft_failures", {}).get("rx_imbalance", {})
    threshold   = float(rx_cfg.get("rx_imbalance_ratio", 10.0))
    enabled     = bool(rx_cfg.get("enabled", False))

    manifest    = pd.read_csv(MANIFEST)
    session_ids = manifest["session_id"].tolist()

    all_ratios: list[np.ndarray] = []
    labels: list[str]            = []
    missing: list[str]           = []

    print(
        f"Sessions: {len(session_ids)}  |  trim_frames={trim_frames}  "
        f"|  rx_imbalance_ratio={threshold}  "
        f"|  enabled={enabled}\n"
    )

    for sid in session_ids:
        h5_path = CUBES_DIR / f"{sid}.h5"
        if not h5_path.exists():
            print(f"  SKIP {sid} — HDF5 not found")
            missing.append(sid)
            continue
        print(f"  {sid} … ", end="", flush=True)
        try:
            rx = _compute_rx_ratio(h5_path, trim_frames)
            all_ratios.append(rx)
            labels.append(sid)
            n_flag = int((rx > threshold).sum())
            print(
                f"{len(rx):5d} frames  "
                f"median={np.median(rx):.3f}  "
                f"p99={np.percentile(rx, 99):.3f}  "
                f"p99.9={np.percentile(rx, 99.9):.3f}  "
                f"max={rx[np.isfinite(rx)].max():.3f}  "
                f"flagged@{threshold:.1f}: {n_flag}"
            )
        except Exception as exc:
            print(f"ERROR — {exc}")
            missing.append(sid)

    if not labels:
        print("No sessions loaded — nothing to plot.")
        sys.exit(1)

    combined = np.concatenate(all_ratios)
    finite   = combined[np.isfinite(combined)]
    total    = len(combined)
    n_inf    = int(np.isinf(combined).sum())
    n_flag   = int((combined > threshold).sum())

    # ---- Aggregate statistics ----
    print(f"\n{'='*60}")
    print(f"  rx_energy_ratio  (threshold = {threshold})")
    print(f"{'='*60}")
    print(f"  Total frames : {total:,}  |  inf frames (min_ch=0): {n_inf}")
    for p in (50, 90, 95, 99, 99.9, 100):
        val = np.percentile(finite, p) if len(finite) else float("nan")
        print(f"  p{p:5.1f} = {val:.4f}")
    print(
        f"  Frames flagged at threshold {threshold}: "
        f"{n_flag} / {total} ({100.0*n_flag/total:.4f}%)"
    )

    # Suggest a data-driven threshold candidate
    p999 = float(np.percentile(finite, 99.9)) if len(finite) else float("nan")
    conservative = round(p999 * 2.0, 1)
    tight        = round(p999 * 1.5, 1)
    print(f"\n  p99.9 = {p999:.4f}")
    print(f"  Data-driven candidates:")
    print(f"    Conservative (2 × p99.9) = {conservative}")
    print(f"    Tight        (1.5 × p99.9) = {tight}")

    # ---- Plot ----
    cmap   = plt.get_cmap("tab10")
    colors = [cmap(i % 10) for i in range(len(labels))]

    fig, (ax_main, ax_tail) = plt.subplots(1, 2, figsize=(15, 5))
    fig.suptitle(
        f"RX channel energy imbalance — {len(labels)} sessions, "
        f"{total:,} analysis frames  (trim={trim_frames})",
        fontsize=11,
    )

    # -- Left panel: full distribution on log x-scale --
    lo   = max(np.floor(np.log10(finite.min())) - 0.1, 0) if len(finite) else 0
    hi   = np.ceil(np.log10(finite.max()) + 0.2) if len(finite) else 2
    bins = np.logspace(lo, hi, 300)

    for sid, rx, c in zip(labels, all_ratios, colors):
        fin = rx[np.isfinite(rx)]
        ax_main.hist(fin, bins=bins, alpha=0.45, color=c, label=sid, density=True)

    ax_main.axvline(
        threshold, color="red", linewidth=1.8, linestyle="--",
        label=f"threshold = {threshold}  ({n_flag} flagged)",
    )
    ax_main.set_xscale("log")
    ax_main.set_yscale("log")
    ax_main.set_xlabel("rx_energy_ratio  (max channel energy / min channel energy)")
    ax_main.set_ylabel("Density")
    ax_main.set_title("Full distribution")
    ax_main.legend(fontsize=7, ncol=2)

    # -- Right panel: tail only (above p95) --
    p95    = float(np.percentile(finite, 95)) if len(finite) else 1.0
    tail   = finite[finite >= p95]
    tail_hi = max(finite.max() * 1.05, threshold * 1.1) if len(finite) else threshold * 2

    bins_tail = np.linspace(p95, tail_hi, 150)
    for sid, rx, c in zip(labels, all_ratios, colors):
        t = rx[np.isfinite(rx)]
        t = t[t >= p95]
        if len(t):
            ax_tail.hist(t, bins=bins_tail, alpha=0.55, color=c,
                         label=sid, density=False)

    ax_tail.axvline(
        threshold, color="red", linewidth=1.8, linestyle="--",
        label=f"threshold = {threshold}",
    )
    ax_tail.axvline(
        p999, color="purple", linewidth=1.2, linestyle=":",
        label=f"p99.9 = {p999:.3f}",
    )
    ax_tail.set_xlabel("rx_energy_ratio (tail, ≥ p95)")
    ax_tail.set_ylabel("Frame count")
    ax_tail.set_title(f"Tail  (≥ p95 = {p95:.3f})")
    ax_tail.legend(fontsize=7, ncol=2)

    fig.tight_layout()
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = FIGURES_DIR / "diag_rx_imbalance.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"\nFigure saved -> {out_path}")
    if missing:
        print(f"Skipped: {missing}")


if __name__ == "__main__":
    main()
