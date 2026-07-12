#!/usr/bin/env python3
"""Diagnostic: motion-spike threshold validation across all sessions.

Computes per-frame subject-bin energy directly from the HDF5 cubes (no
pre-existing quality mask required), then evaluates the motion-spike detector
parameters: motion_spike_factor and motion_window_frames.

Strategy
--------
  1. Chunked FFT to extract per-frame subject-bin energy (no cross-frame
     dependency — chunking is exact).
  2. Rolling median applied to the FULL per-session energy series (avoids the
     chunk-boundary artifact that occurs when median_filter is applied
     chunk-by-chunk).
  3. Ratio = energy / rolling_median per frame. The spike detector flags
     frames where ratio > factor.

Outputs
-------
  Stdout: per-session spike counts at candidate factors (2×, 3×, 5×, 7×, 10×),
          aggregate ratio statistics, and data-driven threshold candidates.
  figures/diag_motion_spike_timeseries.png : energy + rolling median overlay
      per session, with flagged frames marked.
  figures/diag_motion_spike_distribution.png : ratio distribution across all
      sessions (full + tail panel).

Run from repo root:
    python -X utf8 scripts/diag_motion_spike_thresholds.py
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
from scipy.fft import fft as _fft
from scipy      import ndimage as _ndimage

REPO_ROOT    = Path(__file__).resolve().parents[1]
CUBES_DIR    = REPO_ROOT / "data" / "processed" / "time_domain_cubes"
MANIFEST     = REPO_ROOT / "data" / "manifest.local.csv"
CONFIG_PATH  = REPO_ROOT / "steps" / "step_4" / "config.yaml"
FIGURES_DIR  = REPO_ROOT / "figures"

CHUNK_FRAMES  = 200
FRAME_RATE_HZ = 20.0
EPS           = np.finfo(np.float32).tiny

CANDIDATE_FACTORS = [2.0, 3.0, 5.0, 7.0, 10.0]


# ---------------------------------------------------------------------------
# Step 1 — per-frame subject-bin energy (chunked FFT, no cross-frame dependency)
# ---------------------------------------------------------------------------

def _compute_energy(h5_path: Path, trim_frames: int, locked_bin: int) -> np.ndarray:
    """Return per-frame subject-bin energy for one session as float32 (N,).

    Applies range FFT per chunk to keep peak RAM under control, then
    concatenates scalars. No rolling operation here — the energy series is
    returned raw so the rolling median can be applied to the full series.
    """
    energy_list: list[np.ndarray] = []

    with h5py.File(h5_path, "r") as f:
        total_frames = int(f.attrs["num_frames"])
        n_analysis   = total_frames - trim_frames
        if n_analysis <= 0:
            raise ValueError(
                f"trim_frames={trim_frames} >= total_frames={total_frames}"
            )
        n_samples = f["cube"].shape[3]
        if not (0 <= locked_bin < n_samples):
            raise ValueError(
                f"locked_bin={locked_bin} out of range [0, {n_samples - 1}]"
            )

        cube_ds = f["cube"]
        for start in range(0, n_analysis, CHUNK_FRAMES):
            stop  = min(start + CHUNK_FRAMES, n_analysis)
            chunk = cube_ds[trim_frames + start : trim_frames + stop].astype(np.complex64)

            fft_c   = _fft(chunk, axis=3)                      # (C, chirps, rx, bins)
            bin_col = fft_c[:, :, :, locked_bin]               # (C, chirps, rx)
            energy  = np.mean(np.abs(bin_col) ** 2, axis=(1, 2)).astype(np.float32)
            energy_list.append(energy)

    return np.concatenate(energy_list)


# ---------------------------------------------------------------------------
# Step 2 — rolling median on the FULL series (exact, no boundary artifacts)
# ---------------------------------------------------------------------------

def _rolling_median(energy: np.ndarray, half_window: int) -> np.ndarray:
    return _ndimage.median_filter(
        energy,
        size=2 * half_window + 1,
        mode="reflect",
    ).astype(np.float32)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    cfg         = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    trim_frames = int(cfg["analysis"]["trim_frames"])
    ms_cfg      = cfg.get("soft_failures", {}).get("motion_spike", {})
    factor      = float(ms_cfg.get("motion_spike_factor", 5.0))
    half_window = int(ms_cfg.get("motion_window_frames", 50))
    enabled     = bool(ms_cfg.get("enabled", False))

    manifest    = pd.read_csv(MANIFEST)

    def _parse_bin(raw) -> int | None:
        if raw is None or (isinstance(raw, float) and np.isnan(raw)):
            return None
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return None

    bin_map = {
        str(row["session_id"]): _parse_bin(row.get("locked_bin"))
        for _, row in manifest.iterrows()
    }

    all_ratios:   list[np.ndarray] = []
    all_energies: list[np.ndarray] = []
    all_medians:  list[np.ndarray] = []
    all_time:     list[np.ndarray] = []
    labels:       list[str]        = []
    missing:      list[str]        = []

    print(
        f"Sessions: {len(manifest)}  |  trim_frames={trim_frames}  "
        f"|  motion_spike_factor={factor}  "
        f"|  motion_window_frames={half_window}  "
        f"|  enabled={enabled}\n"
    )

    # Per-session count table header
    factor_cols = "  ".join(f">{f:.0f}x" for f in CANDIDATE_FACTORS)
    print(f"{'Session':<10}  {'Frames':>6}  {factor_cols}")
    print("-" * (10 + 8 + len(factor_cols) + 4))

    for sid in manifest["session_id"].tolist():
        h5_path    = CUBES_DIR / f"{sid}.h5"
        locked_bin = bin_map.get(sid)

        if not h5_path.exists():
            print(f"  SKIP {sid} — HDF5 not found")
            missing.append(sid)
            continue
        if locked_bin is None:
            print(f"  SKIP {sid} — no locked_bin in manifest")
            missing.append(sid)
            continue

        try:
            energy = _compute_energy(h5_path, trim_frames, locked_bin)
            med    = _rolling_median(energy, half_window)
            ratio  = (energy / np.maximum(med, EPS)).astype(np.float32)

            counts    = [int((ratio > f).sum()) for f in CANDIDATE_FACTORS]
            count_str = "  ".join(f"{c:>4}" for c in counts)
            print(f"{sid:<10}  {len(energy):>6}  {count_str}")

            all_ratios.append(ratio)
            all_energies.append(energy)
            all_medians.append(med)
            all_time.append(np.arange(len(energy), dtype=np.float32) / FRAME_RATE_HZ)
            labels.append(sid)

        except Exception as exc:
            print(f"{sid:<10}  ERROR — {exc}")
            missing.append(sid)

    if not labels:
        print("No sessions loaded.")
        sys.exit(1)

    combined = np.concatenate(all_ratios)
    total    = len(combined)
    n_flag   = int((combined > factor).sum())

    # ---- Aggregate statistics ----
    print(f"\n{'='*60}")
    print(f"  energy / rolling_median  (factor = {factor}, half_window = {half_window})")
    print(f"{'='*60}")
    for p in (50, 90, 95, 99, 99.9, 100):
        print(f"  p{p:5.1f} = {np.percentile(combined, p):.4f}")
    print(
        f"  Frames flagged at factor {factor}: "
        f"{n_flag} / {total} ({100.0*n_flag/total:.4f}%)"
    )

    p999 = float(np.percentile(combined, 99.9))
    print(f"\n  p99.9 = {p999:.4f}")
    print(f"  Data-driven candidates:")
    print(f"    Conservative (2 × p99.9)  = {round(p999 * 2.0, 1)}")
    print(f"    Tight        (1.5 × p99.9) = {round(p999 * 1.5, 1)}")

    # ================================================================
    # Figure 1 — time series: energy + rolling median, one subplot/session
    # ================================================================
    ncols = 2
    nrows = (len(labels) + 1) // ncols

    fig1, axes1 = plt.subplots(nrows, ncols, figsize=(15, 3.2 * nrows), squeeze=False)
    fig1.suptitle(
        f"Subject-bin energy + rolling median  "
        f"(half_window={half_window} fr = ±{half_window/FRAME_RATE_HZ:.1f} s)",
        fontsize=11,
    )

    for i, (sid, energy, med, ratio, t) in enumerate(
        zip(labels, all_energies, all_medians, all_ratios, all_time)
    ):
        ax      = axes1[i // ncols][i % ncols]
        n_spike = int((ratio > factor).sum())

        ax.plot(t, energy, lw=0.6, color="steelblue", alpha=0.8, label="energy")
        ax.plot(t, med,    lw=1.2, color="orange",               label=f"rolling median ±{half_window} fr")
        ax.plot(t, factor * np.maximum(med, EPS),
                lw=0.8, color="red", linestyle="--", alpha=0.7,
                label=f"{factor:.0f}× median (threshold)")

        # Mark flagged frames as red dots
        spike_mask = ratio > factor
        if spike_mask.any():
            ax.scatter(t[spike_mask], energy[spike_mask],
                       color="red", s=14, zorder=5,
                       label=f"flagged ({n_spike})")

        ax.set_yscale("log")
        ax.set_xlabel("Time (s)", fontsize=7)
        ax.set_ylabel("Energy", fontsize=7)
        ax.set_title(
            f"{sid}  bin={bin_map[sid]}  spikes@{factor:.0f}×={n_spike}",
            fontsize=8,
        )
        ax.tick_params(labelsize=7)
        ax.legend(fontsize=6, loc="upper right", ncol=2)

    for i in range(len(labels), nrows * ncols):
        axes1[i // ncols][i % ncols].set_visible(False)

    fig1.tight_layout()
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    out1 = FIGURES_DIR / "diag_motion_spike_timeseries.png"
    fig1.savefig(out1, dpi=150, bbox_inches="tight")
    print(f"\nFigure 1 (time series)   saved -> {out1}")

    # ================================================================
    # Figure 2 — ratio distribution (full + tail)
    # ================================================================
    cmap   = plt.get_cmap("tab10")
    colors = [cmap(i % 10) for i in range(len(labels))]

    fig2, (ax_main, ax_tail) = plt.subplots(1, 2, figsize=(15, 5))
    fig2.suptitle(
        f"Motion-spike ratio distribution — {len(labels)} sessions, "
        f"{total:,} frames  (half_window={half_window})",
        fontsize=11,
    )

    # Full distribution — log x-scale
    pos      = combined[combined > 0]
    lo       = max(np.floor(np.log10(pos.min())) - 0.1, -2) if len(pos) else -2
    hi       = np.ceil(np.log10(combined.max()) + 0.2)
    bins_all = np.logspace(lo, hi, 300)

    for sid, ratio, c in zip(labels, all_ratios, colors):
        ax_main.hist(ratio, bins=bins_all, alpha=0.45, color=c,
                     label=sid, density=True)

    ax_main.axvline(factor, color="red", linewidth=1.8, linestyle="--",
                    label=f"factor = {factor}  ({n_flag} flagged)")
    ax_main.set_xscale("log")
    ax_main.set_yscale("log")
    ax_main.set_xlabel("energy / rolling_median")
    ax_main.set_ylabel("Density")
    ax_main.set_title("Full ratio distribution")
    ax_main.legend(fontsize=7, ncol=2)

    # Tail — linear x, raw counts
    p95      = float(np.percentile(combined, 95))
    tail_hi  = max(float(combined.max()) * 1.05, factor * 1.1)
    bins_tail = np.linspace(p95, tail_hi, 150)

    for sid, ratio, c in zip(labels, all_ratios, colors):
        tail = ratio[ratio >= p95]
        if len(tail):
            ax_tail.hist(tail, bins=bins_tail, alpha=0.55, color=c,
                         label=sid, density=False)

    ax_tail.axvline(factor, color="red", linewidth=1.8, linestyle="--",
                    label=f"factor = {factor}")
    ax_tail.axvline(p999, color="purple", linewidth=1.2, linestyle=":",
                    label=f"p99.9 = {p999:.3f}")
    ax_tail.set_xlabel(f"energy / rolling_median  (tail ≥ p95 = {p95:.2f})")
    ax_tail.set_ylabel("Frame count")
    ax_tail.set_title("Tail  (≥ p95)")
    ax_tail.legend(fontsize=7, ncol=2)

    fig2.tight_layout()
    out2 = FIGURES_DIR / "diag_motion_spike_distribution.png"
    fig2.savefig(out2, dpi=150, bbox_inches="tight")
    print(f"Figure 2 (distribution)  saved -> {out2}")

    if missing:
        print(f"\nSkipped: {missing}")


if __name__ == "__main__":
    main()
