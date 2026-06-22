#!/usr/bin/env python3
"""Diagnostic: verify that the manifest locked bin is the true energy peak.

Hypothesis: sessions with low subject-bin SNR may have an incorrect locked bin —
the pipeline is extracting phase at the wrong range bin, which both lowers SNR
and degrades HR estimation.

For each session this script:
  1. Loads the HDF5 cube (analysis frames only).
  2. Applies a range FFT to recover the range profile.
  3. Computes mean energy per range bin (averaged over all frames, chirps, RX).
  4. Within a ±gate_margin_m gate around the stated distance, finds the
     highest-energy bin (the data-driven peak).
  5. Compares that peak to the manifest locked_bin.
  6. Computes SNR at both the locked bin and the peak bin so the impact of any
     mis-assignment is immediately visible.

Outputs
-------
  Stdout table: locked_bin, peak_bin, match, energy ratio, SNR comparison.
  figures/diag_range_bin_check.png: range energy profiles for all sessions
      with locked bin and peak bin marked.

Run from repo root:
    python -X utf8 scripts/diag_range_bin_check.py
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

REPO_ROOT    = Path(__file__).resolve().parents[1]
CUBES_DIR    = REPO_ROOT / "data" / "processed" / "time_domain_cubes"
MANIFEST     = REPO_ROOT / "data" / "manifest.local.csv"
CONFIG_PATH  = Path(__file__).resolve().parent / "quality_mask_config.yaml"
FIGURES_DIR  = REPO_ROOT / "figures"

# Hardware constants — same for all sessions
NUM_ADC_SAMPLES     = 256
RANGE_RESOLUTION_M  = 0.0436   # metres per bin

# Gate margin around the manifest distance_cm value
GATE_MARGIN_M       = 0.20     # ± 20 cm (same as reselect_bins.py)

# SNR background guard band (same as quality_mask_config.yaml)
GUARD_HALF_WIDTH    = 3

CHUNK_FRAMES        = 200

EPS = np.finfo(np.float32).tiny


def _range_axis() -> np.ndarray:
    """Range values (metres) for each FFT bin, shape (NUM_ADC_SAMPLES,)."""
    return np.arange(NUM_ADC_SAMPLES, dtype=np.float32) * RANGE_RESOLUTION_M


def _accumulate_bin_energy(h5_path: Path, trim_frames: int) -> np.ndarray:
    """Return mean energy per range bin across all analysis frames.

    Applies complex FFT along the ADC-samples axis. Averages |FFT|² over
    frames, chirps, and RX channels. Returns float32 array shape (n_bins,).
    """
    energy_sum = np.zeros(NUM_ADC_SAMPLES, dtype=np.float64)
    n_frames_total = 0

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
            # Range FFT → (chunk, chirps, rx, n_bins)
            fft_c = _fft(chunk, axis=3)
            # Mean |FFT|² over chirps and RX → (chunk, n_bins)
            bin_e = np.mean(np.abs(fft_c) ** 2, axis=(1, 2))
            energy_sum     += bin_e.sum(axis=0).astype(np.float64)
            n_frames_total += (stop - start)

    return (energy_sum / n_frames_total).astype(np.float32)


def _snr_at_bin(energy: np.ndarray, b: int) -> float:
    """SNR at bin b: energy[b] / median(background bins)."""
    lo = max(0, b - GUARD_HALF_WIDTH)
    hi = min(len(energy) - 1, b + GUARD_HALF_WIDTH)
    bg_mask = np.ones(len(energy), dtype=bool)
    bg_mask[lo : hi + 1] = False
    if not bg_mask.any():
        bg_mask[:] = True
        bg_mask[b] = False
    bg_median = float(np.median(energy[bg_mask]))
    return float(energy[b]) / max(bg_median, EPS)


def _gated_peak(energy: np.ndarray, raxis: np.ndarray, dist_m: float) -> int:
    """Return the highest-energy bin within ±GATE_MARGIN_M of dist_m."""
    gate_mask = (raxis >= dist_m - GATE_MARGIN_M) & (raxis <= dist_m + GATE_MARGIN_M)
    if not gate_mask.any():
        return int(np.argmax(energy))
    gate_bins   = np.where(gate_mask)[0]
    best_local  = int(np.argmax(energy[gate_bins]))
    return int(gate_bins[best_local])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    cfg         = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    trim_frames = int(cfg["trim_frames"])
    raxis       = _range_axis()

    manifest    = pd.read_csv(MANIFEST)

    results = []
    all_energy: list[np.ndarray] = []
    labels: list[str]            = []

    print(
        f"Sessions: {len(manifest)}  |  trim_frames={trim_frames}  "
        f"|  gate_margin=±{GATE_MARGIN_M:.2f} m  "
        f"|  guard_half_width={GUARD_HALF_WIDTH}\n"
    )
    print(
        f"{'Session':<10}  {'Dist':>5}  {'Locked':>6}  {'Peak':>6}  "
        f"{'Match':>5}  {'E_peak/E_lock':>13}  "
        f"{'SNR_locked':>10}  {'SNR_peak':>8}  {'SNR_gain':>8}"
    )
    print("-" * 85)

    for _, row in manifest.iterrows():
        sid        = str(row["session_id"])
        dist_m     = float(row["distance_cm"]) / 100.0
        locked_bin = int(row["locked_bin"])
        h5_path    = CUBES_DIR / f"{sid}.h5"

        if not h5_path.exists():
            print(f"{sid:<10}  SKIP — HDF5 not found")
            continue

        try:
            energy   = _accumulate_bin_energy(h5_path, trim_frames)
            peak_bin = _gated_peak(energy, raxis, dist_m)

            snr_locked = _snr_at_bin(energy, locked_bin)
            snr_peak   = _snr_at_bin(energy, peak_bin)
            e_ratio    = float(energy[peak_bin]) / max(float(energy[locked_bin]), EPS)
            match      = "YES" if peak_bin == locked_bin else "NO  <--"

            print(
                f"{sid:<10}  {dist_m*100:>5.0f}  {locked_bin:>6}  {peak_bin:>6}  "
                f"{match:>7}  {e_ratio:>13.2f}  "
                f"{snr_locked:>10.1f}  {snr_peak:>8.1f}  "
                f"{snr_peak/max(snr_locked, EPS):>8.2f}x"
            )

            results.append({
                "session":     sid,
                "dist_m":      dist_m,
                "locked_bin":  locked_bin,
                "peak_bin":    peak_bin,
                "match":       peak_bin == locked_bin,
                "e_ratio":     e_ratio,
                "snr_locked":  snr_locked,
                "snr_peak":    snr_peak,
            })
            all_energy.append(energy)
            labels.append(sid)

        except Exception as exc:
            print(f"{sid:<10}  ERROR — {exc}")

    if not results:
        print("No sessions loaded.")
        sys.exit(1)

    mismatches = [r for r in results if not r["match"]]
    print(f"\n{'='*60}")
    print(f"  Sessions with bin mismatch: {len(mismatches)} / {len(results)}")
    for r in mismatches:
        print(
            f"  {r['session']}: locked={r['locked_bin']}  peak={r['peak_bin']}  "
            f"energy ratio={r['e_ratio']:.2f}x  "
            f"SNR {r['snr_locked']:.1f} → {r['snr_peak']:.1f}"
        )

    # ---- Plot: range energy profiles ----
    n_sessions = len(labels)
    ncols = 2
    nrows = (n_sessions + 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(14, 3.0 * nrows), squeeze=False)
    fig.suptitle(
        f"Range bin energy profiles — {n_sessions} sessions  "
        f"(trim={trim_frames}, gate ±{GATE_MARGIN_M:.2f} m)",
        fontsize=11,
    )

    for ax_idx, (sid, energy) in enumerate(zip(labels, all_energy)):
        ax  = axes[ax_idx // ncols][ax_idx % ncols]
        r   = next(x for x in results if x["session"] == sid)

        ax.plot(raxis, energy, color="steelblue", linewidth=0.8, label="mean energy")

        # Locked bin
        ax.axvline(
            raxis[r["locked_bin"]], color="red", linewidth=1.5, linestyle="--",
            label=f"locked bin {r['locked_bin']} ({raxis[r['locked_bin']]:.2f} m)",
        )
        # Peak bin (only draw separately if different)
        if not r["match"]:
            ax.axvline(
                raxis[r["peak_bin"]], color="green", linewidth=1.5, linestyle="-",
                label=f"peak bin {r['peak_bin']} ({raxis[r['peak_bin']]:.2f} m)",
            )

        # Gate boundaries
        dist_m = r["dist_m"]
        ax.axvspan(
            dist_m - GATE_MARGIN_M, dist_m + GATE_MARGIN_M,
            alpha=0.08, color="orange", label=f"gate ±{GATE_MARGIN_M:.2f} m",
        )

        match_str = "" if r["match"] else f"  *** MISMATCH  peak=bin{r['peak_bin']}"
        ax.set_title(
            f"{sid}  locked={r['locked_bin']}  "
            f"SNR={r['snr_locked']:.0f}{match_str}",
            fontsize=8,
        )
        ax.set_xlabel("Range (m)", fontsize=7)
        ax.set_ylabel("Mean energy", fontsize=7)
        ax.tick_params(labelsize=7)
        ax.set_yscale("log")
        ax.legend(fontsize=6, loc="upper right")

    # Hide any unused subplots
    for ax_idx in range(n_sessions, nrows * ncols):
        axes[ax_idx // ncols][ax_idx % ncols].set_visible(False)

    fig.tight_layout()
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = FIGURES_DIR / "diag_range_bin_check.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"\nFigure saved -> {out_path}")


if __name__ == "__main__":
    main()
