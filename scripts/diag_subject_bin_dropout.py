#!/usr/bin/env python3
"""Diagnostic: subject-bin dropout threshold validation across all sessions.

Computes per-frame subject-bin energy directly from the HDF5 cubes (no
pre-existing quality mask required), then evaluates the dropout detector
parameters: dropout_factor and dropout_window_frames.

Dropout is the inverse of the motion-spike: a frame is flagged when the
subject-bin energy falls *below* dropout_factor × local rolling median,
indicating the subject has shifted out of the locked bin, a radar null has
formed, or a glitch suppressed the return without reaching the dead-frame
threshold.

Key validation question
-----------------------
The current config has dropout_window_frames=50 (half-width, ±2.5 s), but
motion_spike uses half_window=200 (±10 s). A ±2.5 s window partially tracks
the ~3.3 s breathing cycle — the baseline may dip with the signal, preventing
genuine dropouts from being flagged OR producing spurious flags at the trough.
This script compares both window sizes so the best choice can be confirmed.

Strategy
--------
  1. Chunked FFT to extract per-frame subject-bin energy (reuses the same
     logic as diag_motion_spike_thresholds.py — no cross-frame dependency).
  2. Rolling median applied to the FULL per-session energy series at two
     half-window sizes: 50 (current config) and 200 (motion-spike default).
  3. Ratio = energy / rolling_median per frame. Dropout flags where
     ratio < dropout_factor.

Outputs
-------
  Stdout: per-session dropout counts at candidate factors (0.05, 0.10, 0.15,
          0.20) for each window size; aggregate low-tail percentiles; and a
          data-driven recommendation.
  figures/diag_subject_bin_dropout_timeseries.png : energy + both rolling
      medians + dropout threshold lines per session, with flagged frames marked.
  figures/diag_subject_bin_dropout_distribution.png : ratio distribution
      (left tail ≤ 1.5) across all sessions for both window sizes.

Run from repo root:
    python -X utf8 scripts/diag_subject_bin_dropout.py
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

CANDIDATE_FACTORS = [0.05, 0.10, 0.15, 0.20]
HALF_WINDOWS      = [50, 200]   # ±2.5 s (current config) vs ±10 s (motion-spike)


# ---------------------------------------------------------------------------
# Step 1 — per-frame subject-bin energy (chunked FFT, no cross-frame dependency)
# ---------------------------------------------------------------------------

def _compute_energy(h5_path: Path, trim_frames: int, locked_bin: int) -> np.ndarray:
    """Return per-frame subject-bin energy for one session as float32 (N,).

    Applies range FFT per chunk, then concatenates scalars. No rolling
    operation here — the rolling median must be applied to the full series.
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


def _dropout_ratio(energy: np.ndarray, med: np.ndarray) -> np.ndarray:
    """energy / rolling_median, guarded against near-zero baseline."""
    baseline_valid = med > EPS
    ratio          = np.where(baseline_valid, energy / np.maximum(med, EPS), 1.0)
    return ratio.astype(np.float32)


def _flag_dropout(ratio: np.ndarray, med: np.ndarray, factor: float) -> np.ndarray:
    baseline_valid = med > EPS
    return (baseline_valid & (ratio < factor)).astype(bool)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    cfg         = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    trim_frames = int(cfg["analysis"]["trim_frames"])
    do_cfg      = cfg.get("soft_failures", {}).get("subject_bin_dropout", {})
    cfg_factor  = float(do_cfg.get("dropout_factor", 0.1))
    cfg_hw      = int(do_cfg.get("dropout_window_frames", 50))
    enabled     = bool(do_cfg.get("enabled", False))

    manifest = pd.read_csv(MANIFEST)

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

    # Per-session data: one entry per session, per window size
    #   session_data[sid] = {hw: {"energy", "med", "ratio"}, ...}
    session_data: dict[str, dict] = {}
    labels:  list[str] = []
    missing: list[str] = []

    print(
        f"Sessions: {len(manifest)}  |  trim_frames={trim_frames}  "
        f"|  dropout_factor={cfg_factor} (config)  "
        f"|  dropout_window_frames={cfg_hw} (config)  "
        f"|  enabled={enabled}\n"
    )
    print(f"Comparing half-windows: {HALF_WINDOWS}")
    print(f"Candidate dropout factors: {CANDIDATE_FACTORS}\n")

    # Per-session count tables, one per window size
    for hw in HALF_WINDOWS:
        factor_cols = "  ".join(f"<{f:.2f}" for f in CANDIDATE_FACTORS)
        print(f"--- half_window={hw} (±{hw/FRAME_RATE_HZ:.1f} s) ---")
        print(f"{'Session':<10}  {'Frames':>6}  {factor_cols}")
        print("-" * (10 + 8 + len(factor_cols) + 4))

        for sid in manifest["session_id"].tolist():
            h5_path    = CUBES_DIR / f"{sid}.h5"
            locked_bin = bin_map.get(sid)

            if not h5_path.exists() or locked_bin is None:
                if hw == HALF_WINDOWS[0]:
                    reason = "HDF5 not found" if not h5_path.exists() else "no locked_bin"
                    print(f"  SKIP {sid} — {reason}")
                    missing.append(sid)
                else:
                    print(f"  SKIP {sid}")
                continue

            try:
                # Only compute energy once (for first window); reuse after
                if sid not in session_data:
                    energy = _compute_energy(h5_path, trim_frames, locked_bin)
                    session_data[sid] = {"energy": energy}
                    labels.append(sid)

                energy = session_data[sid]["energy"]
                med    = _rolling_median(energy, hw)
                ratio  = _dropout_ratio(energy, med)

                session_data[sid][hw] = {"med": med, "ratio": ratio}

                counts    = [int(_flag_dropout(ratio, med, f).sum()) for f in CANDIDATE_FACTORS]
                count_str = "  ".join(f"{c:>5}" for c in counts)
                print(f"{sid:<10}  {len(energy):>6}  {count_str}")

            except Exception as exc:
                print(f"{sid:<10}  ERROR — {exc}")
                if sid not in missing:
                    missing.append(sid)

        print()

    if not labels:
        print("No sessions loaded.")
        sys.exit(1)

    # ---- Aggregate low-tail percentile table ----
    print(f"{'='*60}")
    print("  Left-tail percentiles of  energy / rolling_median")
    print(f"{'='*60}")
    for hw in HALF_WINDOWS:
        all_ratios = np.concatenate([
            session_data[sid][hw]["ratio"]
            for sid in labels
            if hw in session_data[sid]
        ])
        total  = len(all_ratios)
        n_flag = int((all_ratios < cfg_factor).sum())

        print(f"\n  half_window={hw} (±{hw/FRAME_RATE_HZ:.1f} s)  —  {total:,} frames")
        for p in (0, 0.1, 1, 5, 10, 50):
            print(f"    p{p:5.1f} = {np.percentile(all_ratios, p):.4f}")
        print(
            f"    Frames flagged at factor {cfg_factor}: "
            f"{n_flag} / {total} ({100.0*n_flag/total:.4f}%)"
        )

    # Data-driven recommendation
    print(f"\n{'='*60}")
    print("  Frames flagged — summary matrix")
    print(f"{'='*60}")
    header = "  ".join(f"factor={f:.2f}" for f in CANDIDATE_FACTORS)
    print(f"  {'hw':<12}  {header}")
    for hw in HALF_WINDOWS:
        all_ratios = np.concatenate([
            session_data[sid][hw]["ratio"]
            for sid in labels
            if hw in session_data[sid]
        ])
        total  = len(all_ratios)
        counts = [int((all_ratios < f).sum()) for f in CANDIDATE_FACTORS]
        pcts   = " ".join(f"  {c:>5} ({100.0*c/total:.3f}%)" for c in counts)
        print(f"  hw={hw:<8}  {pcts}")

    # ================================================================
    # Figure 1 — time series: energy + both rolling medians per session
    # ================================================================
    ncols = 2
    nrows = (len(labels) + 1) // ncols
    hw_colors  = {50: "orange", 200: "green"}
    hw_labels  = {50: f"median ±50 fr (±{50/FRAME_RATE_HZ:.1f} s)",
                  200: f"median ±200 fr (±{200/FRAME_RATE_HZ:.1f} s)"}

    fig1, axes1 = plt.subplots(nrows, ncols, figsize=(16, 3.5 * nrows), squeeze=False)
    fig1.suptitle(
        f"Subject-bin dropout — energy + rolling medians  "
        f"(dropout_factor={cfg_factor}, both windows compared)",
        fontsize=11,
    )

    for i, sid in enumerate(labels):
        ax     = axes1[i // ncols][i % ncols]
        energy = session_data[sid]["energy"]
        t      = np.arange(len(energy), dtype=np.float32) / FRAME_RATE_HZ

        ax.plot(t, energy, lw=0.6, color="steelblue", alpha=0.8, label="energy", zorder=2)

        for hw in HALF_WINDOWS:
            if hw not in session_data[sid]:
                continue
            med   = session_data[sid][hw]["med"]
            ratio = session_data[sid][hw]["ratio"]

            ax.plot(t, med, lw=1.2, color=hw_colors[hw],
                    label=hw_labels[hw], zorder=3)
            ax.plot(t, cfg_factor * np.maximum(med, EPS),
                    lw=0.8, color=hw_colors[hw], linestyle="--", alpha=0.7,
                    label=f"{cfg_factor:.2f}× median (hw={hw})", zorder=3)

            flagged   = _flag_dropout(ratio, med, cfg_factor)
            n_dropout = int(flagged.sum())
            if flagged.any():
                ax.scatter(t[flagged], energy[flagged],
                           color=hw_colors[hw], s=14, zorder=5, marker="v",
                           label=f"flagged hw={hw} ({n_dropout})")

        ax.set_yscale("log")
        ax.set_xlabel("Time (s)", fontsize=7)
        ax.set_ylabel("Energy", fontsize=7)
        ax.set_title(
            f"{sid}  bin={bin_map.get(sid, '?')}",
            fontsize=8,
        )
        ax.tick_params(labelsize=7)
        ax.legend(fontsize=6, loc="upper right", ncol=2)

    for i in range(len(labels), nrows * ncols):
        axes1[i // ncols][i % ncols].set_visible(False)

    fig1.tight_layout()
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    out1 = FIGURES_DIR / "diag_subject_bin_dropout_timeseries.png"
    fig1.savefig(out1, dpi=150, bbox_inches="tight")
    print(f"\nFigure 1 (time series)    saved -> {out1}")

    # ================================================================
    # Figure 2 — left-tail ratio distribution (both windows)
    # ================================================================
    cmap   = plt.get_cmap("tab10")
    colors = [cmap(i % 10) for i in range(len(labels))]

    fig2, axes2 = plt.subplots(2, 2, figsize=(16, 10))
    fig2.suptitle(
        f"Subject-bin dropout ratio distribution — {len(labels)} sessions  "
        f"(energy / rolling_median, left-tail focus)",
        fontsize=11,
    )

    for row_idx, hw in enumerate(HALF_WINDOWS):
        ax_full = axes2[row_idx][0]
        ax_tail = axes2[row_idx][1]

        all_ratios = np.concatenate([
            session_data[sid][hw]["ratio"]
            for sid in labels
            if hw in session_data[sid]
        ])
        total  = len(all_ratios)
        n_flag = int((all_ratios < cfg_factor).sum())

        # Full distribution (0 to 2.0 — dropout is left tail, spikes are right)
        bins_full = np.linspace(0.0, 2.0, 200)
        for sid, c in zip(labels, colors):
            if hw not in session_data[sid]:
                continue
            ax_full.hist(
                session_data[sid][hw]["ratio"],
                bins=bins_full, alpha=0.45, color=c, label=sid, density=True,
            )
        ax_full.axvline(cfg_factor, color="red", linewidth=1.8, linestyle="--",
                        label=f"factor={cfg_factor}  ({n_flag} flagged)")
        ax_full.set_xlabel("energy / rolling_median")
        ax_full.set_ylabel("Density")
        ax_full.set_title(f"Full distribution  hw={hw} (±{hw/FRAME_RATE_HZ:.1f} s)")
        ax_full.legend(fontsize=7, ncol=2)

        # Left-tail zoom (ratio 0–0.5)
        bins_tail = np.linspace(0.0, 0.5, 150)
        for sid, c in zip(labels, colors):
            if hw not in session_data[sid]:
                continue
            tail = session_data[sid][hw]["ratio"]
            tail = tail[tail <= 0.5]
            if len(tail):
                ax_tail.hist(tail, bins=bins_tail, alpha=0.55, color=c,
                             label=sid, density=False)

        for f, ls in zip(CANDIDATE_FACTORS, ["--", "-.", ":", (0,(3,1,1,1))]):
            n_f = int((all_ratios < f).sum())
            ax_tail.axvline(f, color="red", linewidth=1.2, linestyle=ls,
                            label=f"factor={f:.2f}  ({n_f})")
        ax_tail.set_xlabel("energy / rolling_median  (tail ≤ 0.5)")
        ax_tail.set_ylabel("Frame count")
        ax_tail.set_title(f"Left-tail zoom  hw={hw} (±{hw/FRAME_RATE_HZ:.1f} s)")
        ax_tail.legend(fontsize=7, ncol=2)

    fig2.tight_layout()
    out2 = FIGURES_DIR / "diag_subject_bin_dropout_distribution.png"
    fig2.savefig(out2, dpi=150, bbox_inches="tight")
    print(f"Figure 2 (distribution)   saved -> {out2}")

    if missing:
        print(f"\nSkipped: {missing}")


if __name__ == "__main__":
    main()
