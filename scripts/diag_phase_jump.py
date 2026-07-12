#!/usr/bin/env python3
"""Diagnostic: phase-jump threshold validation across all sessions.

Computes frame-to-frame wrapped phase delta at the subject's locked range bin
directly from the HDF5 cubes (no pre-existing quality mask required), then
evaluates the phase-jump detector parameter: threshold_rad.

Unlike the energy-based checks (motion_spike, subject_bin_dropout), this
detector catches frames where energy looks healthy but the phase is corrupted —
subject startles, hardware glitches, or short-range multipath flips.

Key validation question
-----------------------
Does normal seated breathing push past threshold_rad=1.5 rad in good sessions?
At 77 GHz (λ ≈ 3.9 mm), 1 rad ≈ 0.31 mm of chest displacement in one 50 ms
frame. Quiet breathing at ~0.3 Hz and 1–3 mm amplitude gives peak frame-to-frame
steps of ~0.09–0.28 rad — well below 1.5 rad if the threshold is safe.

Two methods are compared (matching add_quality_mask.py):
  mean_phasor       — average chirp/RX phasors first, then conjugate-product delta.
                      This is what the config currently uses.
  delta_before_mean — conjugate-product per chirp/RX first, then average the
                      complex products. More stable when static phase offsets
                      across RX channels partially cancel the mean phasor.

Outputs
-------
  Stdout: per-session flag-count table at candidate thresholds (0.5, 1.0, 1.5,
          2.0, 2.5 rad) for both methods; aggregate |phase_delta| percentiles;
          exp008 (best session) percentiles.
  figures/diag_phase_jump_timeseries.png   : |phase_delta| vs time per session,
      flagged frames marked, threshold line overlaid.
  figures/diag_phase_jump_distribution.png : distribution of |phase_delta|,
      2×2 grid (rows = methods, cols = full / right-tail zoom).

Run from repo root:
    python -X utf8 scripts/diag_phase_jump.py
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

CHUNK_FRAMES        = 200
FRAME_RATE_HZ       = 20.0
CANDIDATE_THRESHOLDS = [0.5, 1.0, 1.5, 2.0, 2.5]
METHODS             = ["mean_phasor", "delta_before_mean"]


# ---------------------------------------------------------------------------
# Phase delta computation — two methods, chunked to control RAM
# ---------------------------------------------------------------------------

def _phase_delta_mean_phasor(
    h5_path: Path,
    trim_frames: int,
    locked_bin: int,
) -> np.ndarray:
    """Return per-frame wrapped phase delta (float32, N,) using mean_phasor method.

    Average chirp×RX phasors into one complex number per frame, then compute
    angle(z[i] * conj(z[i-1])). phase_delta[0] = 0 (no predecessor).
    Rolling median is NOT applied — this is a purely frame-to-frame operation.
    """
    phasors: list[np.ndarray] = []

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

            fft_c   = _fft(chunk, axis=3)              # (C, chirps, rx, bins)
            bin_col = fft_c[:, :, :, locked_bin]       # (C, chirps, rx)
            z_mean  = np.mean(bin_col, axis=(1, 2))    # (C,) — mean phasor per frame
            phasors.append(z_mean.astype(np.complex64))

    z = np.concatenate(phasors)                        # (N,)
    phase_delta = np.zeros(len(z), dtype=np.float32)
    if len(z) > 1:
        phase_delta[1:] = np.angle(z[1:] * np.conj(z[:-1])).astype(np.float32)
    return phase_delta


def _phase_delta_delta_before_mean(
    h5_path: Path,
    trim_frames: int,
    locked_bin: int,
) -> np.ndarray:
    """Return per-frame wrapped phase delta (float32, N,) using delta_before_mean method.

    Compute conjugate-product per (chirp, rx) first, then average. Chunk
    boundary is handled correctly: the first frame of each non-first chunk
    compares against the last frame of the previous chunk.
    phase_delta[0] = 0 (no predecessor).
    """
    phase_delta_list: list[np.ndarray] = []
    prev_last_bin: np.ndarray | None   = None  # shape (chirps, rx), complex64

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
            stop      = min(start + CHUNK_FRAMES, n_analysis)
            chunk     = cube_ds[trim_frames + start : trim_frames + stop].astype(np.complex64)
            fft_c     = _fft(chunk, axis=3)                  # (C, chirps, rx, bins)
            bin_chunk = fft_c[:, :, :, locked_bin]           # (C, chirps, rx)
            c_len     = stop - start
            chunk_delta = np.zeros(c_len, dtype=np.float32)

            if prev_last_bin is not None:
                # Predecessor: prev_last_bin for frame 0 of chunk, bin_chunk[k-1] for k>0
                extended_prev = np.concatenate(
                    [prev_last_bin[np.newaxis], bin_chunk[:-1]], axis=0
                )                                            # (C, chirps, rx)
                dp = bin_chunk * np.conj(extended_prev)     # (C, chirps, rx)
                chunk_delta[:] = np.angle(
                    np.mean(dp, axis=(1, 2))
                ).astype(np.float32)
            else:
                # First chunk: frame 0 has no predecessor → stays 0
                if c_len > 1:
                    dp = bin_chunk[1:] * np.conj(bin_chunk[:-1])
                    chunk_delta[1:] = np.angle(
                        np.mean(dp, axis=(1, 2))
                    ).astype(np.float32)

            phase_delta_list.append(chunk_delta)
            prev_last_bin = bin_chunk[-1].copy()             # (chirps, rx)

    return np.concatenate(phase_delta_list)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    cfg         = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    trim_frames = int(cfg["analysis"]["trim_frames"])
    pj_cfg      = cfg.get("soft_failures", {}).get("phase_jump", {})
    cfg_thr     = float(pj_cfg.get("threshold_rad", 1.5))
    cfg_method  = pj_cfg.get("method", "mean_phasor")
    enabled     = bool(pj_cfg.get("enabled", False))

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

    # session_data[sid][method] = {"phase_delta": ..., "phase_diff": ...}
    session_data: dict[str, dict] = {}
    labels:  list[str] = []
    missing: list[str] = []

    print(
        f"Sessions: {len(manifest)}  |  trim_frames={trim_frames}  "
        f"|  threshold_rad={cfg_thr} (config)  "
        f"|  method={cfg_method} (config)  "
        f"|  enabled={enabled}\n"
    )
    print(f"Candidate thresholds (rad): {CANDIDATE_THRESHOLDS}")
    print(f"Methods compared: {METHODS}\n")

    # Load phase deltas for all sessions and both methods
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
            mp_delta  = _phase_delta_mean_phasor(h5_path, trim_frames, locked_bin)
            dbm_delta = _phase_delta_delta_before_mean(h5_path, trim_frames, locked_bin)

            session_data[sid] = {
                "mean_phasor": {
                    "phase_delta": mp_delta,
                    "phase_diff":  np.abs(mp_delta).astype(np.float32),
                },
                "delta_before_mean": {
                    "phase_delta": dbm_delta,
                    "phase_diff":  np.abs(dbm_delta).astype(np.float32),
                },
            }
            labels.append(sid)
            print(f"  Loaded {sid}  ({len(mp_delta)} frames)")
        except Exception as exc:
            print(f"  {sid}  ERROR — {exc}")
            missing.append(sid)

    if not labels:
        print("No sessions loaded.")
        sys.exit(1)

    print()

    # ---- Per-session count tables, one per method ----
    for method in METHODS:
        thr_cols = "  ".join(f">{t:.1f}" for t in CANDIDATE_THRESHOLDS)
        print(f"--- method={method} ---")
        print(f"{'Session':<10}  {'Frames':>6}  {thr_cols}")
        print("-" * (10 + 8 + len(thr_cols) + 4))

        for sid in labels:
            diff   = session_data[sid][method]["phase_diff"]
            counts = [int((diff > t).sum()) for t in CANDIDATE_THRESHOLDS]
            c_str  = "  ".join(f"{c:>5}" for c in counts)
            print(f"{sid:<10}  {len(diff):>6}  {c_str}")
        print()

    # ---- Aggregate percentile tables ----
    print(f"{'='*60}")
    print("  |phase_delta| percentiles (rad)")
    print(f"{'='*60}")

    for method in METHODS:
        all_diff = np.concatenate([
            session_data[sid][method]["phase_diff"] for sid in labels
        ])
        total  = len(all_diff)
        n_flag = int((all_diff > cfg_thr).sum())

        print(f"\n  method={method}  —  {total:,} frames")
        for p in (50, 90, 95, 99, 99.9, 100):
            print(f"    p{p:5.1f} = {np.percentile(all_diff, p):.4f} rad")
        print(
            f"    Frames flagged at threshold {cfg_thr} rad: "
            f"{n_flag} / {total} ({100.0*n_flag/total:.4f}%)"
        )

    # ---- exp008 (reference good session) ----
    if "exp008" in session_data:
        print(f"\n{'='*60}")
        print("  exp008 (reference good session) — |phase_delta| percentiles")
        print(f"{'='*60}")
        for method in METHODS:
            diff   = session_data["exp008"][method]["phase_diff"]
            n_flag = int((diff > cfg_thr).sum())
            print(f"\n  method={method}  —  {len(diff)} frames")
            for p in (50, 90, 95, 99, 99.9, 100):
                print(f"    p{p:5.1f} = {np.percentile(diff, p):.4f} rad")
            print(f"    Flagged at {cfg_thr} rad: {n_flag}")

    # ================================================================
    # Figure 1 — time series: |phase_delta| per session, cfg method
    # ================================================================
    ncols = 2
    nrows = (len(labels) + 1) // ncols

    fig1, axes1 = plt.subplots(nrows, ncols, figsize=(16, 3.5 * nrows), squeeze=False)
    fig1.suptitle(
        f"|phase_delta| vs time  (method={cfg_method}, threshold={cfg_thr} rad)",
        fontsize=11,
    )

    for i, sid in enumerate(labels):
        ax   = axes1[i // ncols][i % ncols]
        diff = session_data[sid][cfg_method]["phase_diff"]
        t    = np.arange(len(diff), dtype=np.float32) / FRAME_RATE_HZ

        ax.plot(t, diff, lw=0.5, color="steelblue", alpha=0.7, label="|phase_delta|", zorder=2)
        ax.axhline(cfg_thr, color="red", linewidth=1.2, linestyle="--",
                   label=f"threshold = {cfg_thr} rad", zorder=3)

        flagged = diff > cfg_thr
        n_flag  = int(flagged.sum())
        if flagged.any():
            ax.scatter(t[flagged], diff[flagged],
                       color="red", s=16, zorder=5,
                       label=f"flagged ({n_flag})")

        ax.set_xlabel("Time (s)", fontsize=7)
        ax.set_ylabel("|Δphase| (rad)", fontsize=7)
        ax.set_title(
            f"{sid}  bin={bin_map.get(sid, '?')}  flagged@{cfg_thr}rad={n_flag}",
            fontsize=8,
        )
        ax.tick_params(labelsize=7)
        ax.legend(fontsize=6, loc="upper right", ncol=2)

    for i in range(len(labels), nrows * ncols):
        axes1[i // ncols][i % ncols].set_visible(False)

    fig1.tight_layout()
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    out1 = FIGURES_DIR / "diag_phase_jump_timeseries.png"
    fig1.savefig(out1, dpi=150, bbox_inches="tight")
    print(f"\nFigure 1 (time series)    saved -> {out1}")

    # ================================================================
    # Figure 2 — distribution: 2×2 grid (rows=methods, cols=full/tail)
    # ================================================================
    cmap   = plt.get_cmap("tab10")
    colors = [cmap(i % 10) for i in range(len(labels))]

    fig2, axes2 = plt.subplots(2, 2, figsize=(16, 10))
    fig2.suptitle(
        f"|phase_delta| distribution — {len(labels)} sessions  "
        f"(both methods compared, threshold={cfg_thr} rad)",
        fontsize=11,
    )

    for row_idx, method in enumerate(METHODS):
        ax_full = axes2[row_idx][0]
        ax_tail = axes2[row_idx][1]

        all_diff = np.concatenate([
            session_data[sid][method]["phase_diff"] for sid in labels
        ])
        total  = len(all_diff)
        n_flag = int((all_diff > cfg_thr).sum())

        # Full distribution (0 to π rad)
        bins_full = np.linspace(0.0, np.pi, 200)
        for sid, c in zip(labels, colors):
            ax_full.hist(
                session_data[sid][method]["phase_diff"],
                bins=bins_full, alpha=0.45, color=c, label=sid, density=True,
            )
        ax_full.axvline(cfg_thr, color="red", linewidth=1.8, linestyle="--",
                        label=f"threshold={cfg_thr} rad  ({n_flag} flagged)")
        ax_full.set_yscale("log")
        ax_full.set_xlabel("|phase_delta| (rad)")
        ax_full.set_ylabel("Density (log)")
        ax_full.set_title(f"Full distribution  method={method}")
        ax_full.legend(fontsize=7, ncol=2)

        # Right-tail zoom (>0.5 rad)
        tail_lo   = 0.5
        bins_tail = np.linspace(tail_lo, np.pi, 150)
        for sid, c in zip(labels, colors):
            tail = session_data[sid][method]["phase_diff"]
            tail = tail[tail >= tail_lo]
            if len(tail):
                ax_tail.hist(tail, bins=bins_tail, alpha=0.55, color=c,
                             label=sid, density=False)

        for thr, ls in zip(
            CANDIDATE_THRESHOLDS,
            ["--", "-.", ":", (0, (3, 1, 1, 1)), (0, (5, 2))],
        ):
            n_t = int((all_diff > thr).sum())
            ax_tail.axvline(thr, color="red", linewidth=1.2, linestyle=ls,
                            label=f"{thr:.1f} rad  ({n_t})")
        ax_tail.set_xlabel(f"|phase_delta| (rad)  (tail ≥ {tail_lo} rad)")
        ax_tail.set_ylabel("Frame count")
        ax_tail.set_title(f"Right-tail zoom  method={method}")
        ax_tail.legend(fontsize=7, ncol=2)

    fig2.tight_layout()
    out2 = FIGURES_DIR / "diag_phase_jump_distribution.png"
    fig2.savefig(out2, dpi=150, bbox_inches="tight")
    print(f"Figure 2 (distribution)   saved -> {out2}")

    if missing:
        print(f"\nSkipped: {missing}")


if __name__ == "__main__":
    main()
