#!/usr/bin/env python3
"""Diagnostic: subject-bin SNR distribution across all sessions.

Computes per-frame subject_bin_snr using the same logic as
add_quality_mask.py (_flag_subject_bin_snr), without needing a
pre-existing quality mask.

SNR definition
--------------
    SNR[frame] = subject_bin_energy / median(background_bin_energies)

    subject_bin_energy      = mean(|FFT[locked_bin]|²) over chirps and RX
    background_bin_energies = per-bin mean |FFT|² for all bins OUTSIDE the
                              guard band [locked_bin ± guard_half_width]

Locked bin is read from the manifest per session.

Run from repo root:
    python -X utf8 scripts/diag_subject_bin_snr.py
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
CONFIG_PATH  = REPO_ROOT / "steps" / "step_4" / "config.yaml"
FIGURES_DIR  = REPO_ROOT / "figures"

CHUNK_FRAMES = 200


# ---------------------------------------------------------------------------
# Metric computation
# ---------------------------------------------------------------------------

def _compute_snr(
    h5_path: Path,
    trim_frames: int,
    locked_bin: int,
    guard_half_width: int,
) -> np.ndarray:
    """Return per-frame subject_bin_snr for one session.

    Matches _flag_subject_bin_snr() in add_quality_mask.py exactly.

    Parameters
    ----------
    locked_bin       : range-FFT bin index for the subject.
    guard_half_width : bins excluded on each side of locked_bin from background.

    Returns
    -------
    snr : float32 (N_analysis,)
    """
    snr_list: list[np.ndarray] = []

    with h5py.File(h5_path, "r") as f:
        total_frames = int(f.attrs["num_frames"])
        n_analysis   = total_frames - trim_frames
        if n_analysis <= 0:
            raise ValueError(
                f"trim_frames={trim_frames} >= total_frames={total_frames}"
            )

        cube_ds  = f["cube"]
        n_samples = cube_ds.shape[3]
        # IQ data → complex FFT, n_bins = n_samples
        n_bins   = n_samples

        if not (0 <= locked_bin < n_bins):
            raise ValueError(
                f"locked_bin={locked_bin} out of range [0, {n_bins-1}]"
            )

        # Build background mask once: all bins except the guard band
        lo = max(0, locked_bin - guard_half_width)
        hi = min(n_bins - 1, locked_bin + guard_half_width)
        background_mask = np.ones(n_bins, dtype=bool)
        background_mask[lo : hi + 1] = False
        if not background_mask.any():
            # Guard band covers everything — fall back to all bins except locked_bin
            background_mask[:] = True
            background_mask[locked_bin] = False

        n_background = int(background_mask.sum())
        eps = np.finfo(np.float32).tiny

        for start in range(0, n_analysis, CHUNK_FRAMES):
            stop  = min(start + CHUNK_FRAMES, n_analysis)
            # shape: (chunk, chirps, rx, adc_samples)
            chunk = cube_ds[trim_frames + start : trim_frames + stop].astype(np.complex64)

            # Range FFT along ADC samples axis
            # result shape: (chunk, chirps, rx, n_bins)
            fft_chunk = _fft(chunk, axis=3)

            # Mean |FFT|² over chirps and RX for every bin → (chunk, n_bins)
            bin_energy = np.mean(np.abs(fft_chunk) ** 2, axis=(1, 2)).astype(np.float32)

            # Subject-bin energy per frame
            subject_e = bin_energy[:, locked_bin]   # (chunk,)

            # Background: median over background bins per frame
            bg_e      = bin_energy[:, background_mask]          # (chunk, n_background)
            bg_median = np.median(bg_e, axis=1).astype(np.float32)  # (chunk,)

            snr_chunk = (subject_e / np.maximum(bg_median, eps)).astype(np.float32)
            snr_list.append(snr_chunk)

    return np.concatenate(snr_list), n_background


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    cfg         = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    trim_frames = int(cfg["analysis"]["trim_frames"])
    snr_cfg     = cfg.get("soft_failures", {}).get("subject_bin_snr", {})
    threshold   = float(snr_cfg.get("snr_threshold", 5.0))
    guard_hw    = int(snr_cfg.get("guard_half_width", 3))
    enabled     = bool(snr_cfg.get("enabled", False))

    manifest    = pd.read_csv(MANIFEST)

    # Parse locked_bin per session
    def _parse_bin(raw) -> int | None:
        if raw is None or (isinstance(raw, float) and np.isnan(raw)):
            return None
        try:
            v = int(float(raw))
        except (TypeError, ValueError):
            return None
        return v

    bin_map = {
        str(row["session_id"]): _parse_bin(row.get("locked_bin"))
        for _, row in manifest.iterrows()
    }

    session_ids = manifest["session_id"].tolist()

    all_snr:  list[np.ndarray] = []
    labels:   list[str]        = []
    missing:  list[str]        = []
    per_session_stats: list[dict] = []

    print(
        f"Sessions: {len(session_ids)}  |  trim_frames={trim_frames}  "
        f"|  snr_threshold={threshold}  "
        f"|  guard_half_width={guard_hw}  "
        f"|  enabled={enabled}\n"
    )

    for sid in session_ids:
        h5_path   = CUBES_DIR / f"{sid}.h5"
        locked_bin = bin_map.get(sid)
        if not h5_path.exists():
            print(f"  SKIP {sid} — HDF5 not found")
            missing.append(sid)
            continue
        if locked_bin is None:
            print(f"  SKIP {sid} — no locked_bin in manifest")
            missing.append(sid)
            continue

        print(f"  {sid} (bin={locked_bin}) … ", end="", flush=True)
        try:
            snr, n_bg = _compute_snr(h5_path, trim_frames, locked_bin, guard_hw)
            all_snr.append(snr)
            labels.append(sid)
            n_flag = int((snr < threshold).sum())
            stats = {
                "session": sid,
                "locked_bin": locked_bin,
                "n_frames": len(snr),
                "n_background_bins": n_bg,
                "min":   float(snr.min()),
                "p1":    float(np.percentile(snr, 1)),
                "p5":    float(np.percentile(snr, 5)),
                "p50":   float(np.median(snr)),
                "p95":   float(np.percentile(snr, 95)),
                "p99":   float(np.percentile(snr, 99)),
                "max":   float(snr.max()),
                "n_flagged": n_flag,
            }
            per_session_stats.append(stats)
            print(
                f"{len(snr):5d} frames  bg_bins={n_bg}  "
                f"min={stats['min']:.1f}  p1={stats['p1']:.1f}  "
                f"p5={stats['p5']:.1f}  median={stats['p50']:.1f}  "
                f"max={stats['max']:.1f}  flagged@{threshold}: {n_flag}"
            )
        except Exception as exc:
            print(f"ERROR — {exc}")
            missing.append(sid)

    if not labels:
        print("No sessions loaded — nothing to plot.")
        sys.exit(1)

    combined = np.concatenate(all_snr)
    total    = len(combined)
    n_flag   = int((combined < threshold).sum())

    # ---- Aggregate statistics ----
    print(f"\n{'='*60}")
    print(f"  subject_bin_snr  (threshold = {threshold})")
    print(f"{'='*60}")
    for p in (0, 1, 5, 50, 95, 99, 100):
        print(f"  p{p:5.1f} = {np.percentile(combined, p):.2f}")
    print(
        f"  Frames flagged at threshold {threshold}: "
        f"{n_flag} / {total} ({100.0*n_flag/total:.4f}%)"
    )

    # Highlight exp008 (best performer) as the healthy baseline
    exp008_idx = next((i for i, l in enumerate(labels) if l == "exp008"), None)
    if exp008_idx is not None:
        s8 = all_snr[exp008_idx]
        p1_exp008 = float(np.percentile(s8, 1))
        candidate = round(0.8 * p1_exp008, 1)
        print(f"\n  exp008 (best performer) p1 = {p1_exp008:.2f}")
        print(f"  Tight candidate (0.8 × exp008 p1) = {candidate}")

    # p99.9-based candidates (same formula as rx_imbalance diagnostic,
    # but SNR is a "higher is better" metric so we use the LOW tail)
    p1_all  = float(np.percentile(combined, 1))
    p01_all = float(np.percentile(combined, 0.1))
    print(f"\n  Combined p1   = {p1_all:.2f}  →  conservative candidate = {round(p1_all*0.5,1)}")
    print(f"  Combined p0.1 = {p01_all:.2f}  →  tight candidate        = {round(p01_all*0.8,1)}")

    # ---- Plot ----
    cmap   = plt.get_cmap("tab10")
    colors = [cmap(i % 10) for i in range(len(labels))]

    fig, (ax_main, ax_tail) = plt.subplots(1, 2, figsize=(15, 5))
    fig.suptitle(
        f"Subject-bin SNR — {len(labels)} sessions, {total:,} frames  "
        f"(trim={trim_frames}, guard_half_width={guard_hw})",
        fontsize=11,
    )

    # -- Left panel: full distribution on log x-scale --
    pos_vals = combined[combined > 0]
    lo = max(np.floor(np.log10(pos_vals.min())) - 0.2, 0) if len(pos_vals) else 0
    hi = np.ceil(np.log10(combined.max()) + 0.2)
    bins_main = np.logspace(lo, hi, 300)

    for sid, snr, c in zip(labels, all_snr, colors):
        lw  = 2.0 if sid == "exp008" else 1.0
        alpha = 0.65 if sid == "exp008" else 0.40
        ax_main.hist(snr, bins=bins_main, alpha=alpha, color=c,
                     label=sid, density=True, linewidth=lw)

    ax_main.axvline(
        threshold, color="red", linewidth=1.8, linestyle="--",
        label=f"snr_threshold = {threshold}  ({n_flag} flagged)",
    )
    ax_main.set_xscale("log")
    ax_main.set_yscale("log")
    ax_main.set_xlabel("subject_bin_snr  (subject bin energy / background median)")
    ax_main.set_ylabel("Density")
    ax_main.set_title("Full SNR distribution")
    ax_main.legend(fontsize=7, ncol=2)

    # -- Right panel: low-SNR tail (below p10 of combined) --
    p10 = float(np.percentile(combined, 10))
    bins_tail = np.linspace(max(combined.min() * 0.9, 0), p10 * 1.05, 150)

    for sid, snr, c in zip(labels, all_snr, colors):
        tail = snr[snr <= p10]
        if len(tail):
            lw    = 1.5 if sid == "exp008" else 0.8
            ax_tail.hist(tail, bins=bins_tail, alpha=0.55, color=c,
                         label=sid, density=False, linewidth=lw)

    ax_tail.axvline(
        threshold, color="red", linewidth=1.8, linestyle="--",
        label=f"snr_threshold = {threshold}",
    )
    if exp008_idx is not None:
        p1_8 = float(np.percentile(all_snr[exp008_idx], 1))
        ax_tail.axvline(
            p1_8, color="green", linewidth=1.2, linestyle=":",
            label=f"exp008 p1 = {p1_8:.1f}",
        )
    ax_tail.set_xlabel(f"subject_bin_snr  (tail, ≤ p10 = {p10:.1f})")
    ax_tail.set_ylabel("Frame count")
    ax_tail.set_title(f"Low-SNR tail  (≤ p10)")
    ax_tail.legend(fontsize=7, ncol=2)

    fig.tight_layout()
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    out_path = FIGURES_DIR / "diag_subject_bin_snr.png"
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    print(f"\nFigure saved -> {out_path}")
    if missing:
        print(f"Skipped: {missing}")


if __name__ == "__main__":
    main()
