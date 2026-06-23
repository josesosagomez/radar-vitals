#!/usr/bin/env python3
"""Diagnostic: verify spectral leakage hypothesis for seated no-back failures.

Hypothesis: power dominating the bottom of the cardiac band (48-55 bpm) is the
sidelobe tail of the 2nd respiratory harmonic (2×f_r ≈ 0.60-0.80 Hz), which
sits just below the cardiac band edge at 0.8 Hz (48 bpm). ECA removes the
discrete spike at 2×f_r but cannot remove its spectral leakage, which bleeds
across the ~0.1 Hz gap into the cardiac band.

Verification strategy
---------------------
Compute the full phase power spectrum (rfft of phase_clean, before any bandpass
or ECA) for representative bad and good windows. Plot it alongside the cardiac-
band-only spectrum already stored in heart_spectrum. If leakage is the cause:
  - A strong 2×f_r spike will be visible just LEFT of the 0.8 Hz band edge
  - The cardiac band spectrum will be the CONTINUATION of that spike's tail
  - Power at the band edge (0.8 Hz) will be >> power at the true HR frequency
  - The pattern will be absent (or much weaker) in good windows / exp008

Outputs
-------
  Stdout: per-window table — f_r_hz, 2xfr_hz, gap to band edge, power ratio
          (band edge vs Masimo frequency), confirming or refuting leakage.
  figures/diag_leakage_<session>.png : 2-panel plot per window (full spectrum
      0.1-3 Hz + zoom 0.5-1.8 Hz), with harmonic markers and band boundaries.

Run from repo root:
    python -X utf8 scripts/diag_leakage.py
    python -X utf8 scripts/diag_leakage.py --sessions exp003 exp008
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from scipy.fft import rfft, rfftfreq

REPO_ROOT    = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src import radar_io, vitals  # noqa: E402

MANIFEST      = REPO_ROOT / "data" / "manifest.local.csv"
DATA_RAW      = REPO_ROOT / "data" / "raw"
RESULTS_BASE  = REPO_ROOT / "results" / "exp_eca_all"
EXP_CFG_PATH  = REPO_ROOT / "experiments" / "exp_eca_all" / "config.yaml"
FIGURES_DIR   = REPO_ROOT / "figures"

CARDIAC_LO_HZ = 0.8    # cardiac band lower edge (Hz)
CARDIAC_HI_HZ = 2.0    # cardiac band upper edge (Hz)
RESP_LO_HZ    = 0.1
RESP_HI_HZ    = 0.5

DEFAULT_SESSIONS = ["exp003", "exp008"]
N_WORST          = 4
N_BEST           = 2

HARMONIC_COLORS = {1: "steelblue", 2: "goldenrod", 3: "darkorange",
                   4: "red", 5: "purple", 6: "brown"}


# ---------------------------------------------------------------------------
# Session loading (mirrors diag_heart_spectrum.py)
# ---------------------------------------------------------------------------

def _latest_run_dir() -> Path:
    runs = sorted(RESULTS_BASE.iterdir())
    if not runs:
        raise FileNotFoundError(f"No runs found under {RESULTS_BASE}")
    return runs[-1]


def _resolve_bin_paths(session_id: str) -> list[Path]:
    for pat in [f"{session_id}_0.bin", f"{session_id}.bin"]:
        p = DATA_RAW / pat
        if p.exists():
            partner = DATA_RAW / f"{session_id}_1.bin"
            return [p, partner] if "0.bin" in pat and partner.exists() else [p]
    raise FileNotFoundError(f"No .bin file for '{session_id}' in {DATA_RAW}")


def _total_frames(bin_paths: list[Path], cfg_r: dict) -> int:
    total_bytes = sum(p.stat().st_size for p in bin_paths)
    bpf = cfg_r["num_chirps_per_frame"] * cfg_r["num_rx"] * cfg_r["num_adc_samples"] * 4
    return total_bytes // bpf


def _run_session(row: pd.Series, cfg: dict) -> list[dict]:
    session_id = str(row["session_id"])
    locked_bin = int(row["locked_bin"])
    trim_s, end_s = (int(x) for x in str(row["stationary_intervals"]).split("-"))

    r             = cfg["radar"]
    fs            = float(r["frame_rate_hz"])
    trim_frames   = int(trim_s * fs)
    end_frames    = int(end_s  * fs)
    window_frames = int(cfg["processing"]["window_s"] * fs)
    hop_frames    = int(cfg["processing"]["hop_s"]    * fs)

    bin_paths = _resolve_bin_paths(session_id)
    n_frames  = _total_frames(bin_paths, r)
    chirp_cfg = radar_io.ChirpConfig(
        num_adc_samples      = r["num_adc_samples"],
        num_rx               = r["num_rx"],
        num_tx               = r["num_tx"],
        num_chirps_per_frame = r["num_chirps_per_frame"],
        num_frames           = n_frames,
        frame_rate_hz        = fs,
        range_resolution_m   = r["range_resolution_m"],
    )
    bin_arg  = bin_paths[0] if len(bin_paths) == 1 else bin_paths
    cube     = radar_io.read_adc_bin(bin_arg, chirp_cfg, trim_frames=trim_frames)
    profiles = radar_io.range_profile(cube);  del cube
    analysis = profiles[trim_frames:end_frames];  del profiles

    raxis  = radar_io.range_axis_m(r["num_adc_samples"], r["range_resolution_m"])
    params = vitals.VitalsParams(fs_hz=fs, gate_min_m=0.5, gate_max_m=2.5)

    window_results = vitals.run_pipeline_locked(
        analysis, raxis, params, window_frames, hop_frames,
        locked_bin        = locked_bin,
        k_max             = cfg["eca"]["k_max"],
        ahet_deviation_hz = cfg["eca"]["ahet_deviation_hz"],
    )
    t0 = int(row["radar_start_epoch_seconds"]) + trim_s
    for out in window_results:
        out["start_epoch"] = t0 + out["start_frame"] / fs
        out["end_epoch"]   = t0 + out["end_frame"]   / fs
    return window_results


def _load_comparison(session_id: str, run_dir: Path) -> pd.DataFrame:
    csv = run_dir / session_id / "comparison.csv"
    if not csv.exists():
        raise FileNotFoundError(
            f"comparison.csv not found for {session_id}. "
            "Run experiments/exp_eca_all/run.py first."
        )
    return pd.read_csv(csv)


def _merge(window_results: list[dict], comparison: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for o in window_results:
        rows.append({
            "window_index":      o["window_index"],
            "start_frame":       o["start_frame"],
            "hr_bpm":            o["hr_bpm"],
            "f_r_hz":            o["f_r_hz_used"],
            "heart_peak_hz":     o["heart_peak_hz"],
            "heart_freqs_hz":    o["heart_freqs_hz"],
            "heart_spectrum":    o["heart_spectrum"],
            "phase_clean":       o["phase_clean"],
            "phase_eca":         o["phase_eca"],
        })
    wdf  = pd.DataFrame(rows)
    comp = comparison[["window_index", "masimo_pr_bpm",
                        "error_bpm", "abs_error_bpm"]].copy()
    return wdf.merge(comp, on="window_index", how="left")


# ---------------------------------------------------------------------------
# Full spectrum computation
# ---------------------------------------------------------------------------

def _full_spectrum(phase: np.ndarray, fs: float) -> tuple[np.ndarray, np.ndarray]:
    """Return (freqs_hz, power) for the full phase signal via rfft with Hann window.

    Uses the same np.hanning(n) window as vitals.py (lines 171, 256) so the
    spectrum matches exactly what the pipeline computes internally.
    """
    n      = len(phase)
    win    = np.hanning(n)
    freqs  = rfftfreq(n, d=1.0 / fs)
    power  = (np.abs(rfft(phase * win)) ** 2) / n
    return freqs, power.astype(np.float64)


def _interp_power(freqs: np.ndarray, power: np.ndarray, target_hz: float) -> float:
    """Linear-interpolate power at target_hz from the spectrum."""
    idx = np.searchsorted(freqs, target_hz)
    if idx == 0:
        return float(power[0])
    if idx >= len(power):
        return float(power[-1])
    lo, hi = freqs[idx - 1], freqs[idx]
    t = (target_hz - lo) / (hi - lo) if hi > lo else 0.0
    return float((1 - t) * power[idx - 1] + t * power[idx])


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def _annotate_spectrum(
    ax: plt.Axes,
    freqs_hz: np.ndarray,
    f_r_hz: float,
    masimo_pr_bpm: float,
    heart_peak_hz: float,
    lo_hz: float,
    hi_hz: float,
) -> None:
    """Add band shading and vertical marker lines to one spectrum axes."""
    # Band shading
    ax.axvspan(RESP_LO_HZ, RESP_HI_HZ, alpha=0.08, color="gold",    zorder=0)
    ax.axvspan(RESP_HI_HZ, CARDIAC_LO_HZ, alpha=0.08, color="red",  zorder=0)
    ax.axvspan(CARDIAC_LO_HZ, CARDIAC_HI_HZ, alpha=0.08, color="dodgerblue", zorder=0)

    # Cardiac band edges
    ax.axvline(CARDIAC_LO_HZ, color="gray", lw=1.0, linestyle="-", alpha=0.6,
               label=f"band edge {CARDIAC_LO_HZ:.1f} Hz")
    ax.axvline(CARDIAC_HI_HZ, color="gray", lw=0.8, linestyle="--", alpha=0.5)

    # f_r harmonics
    if np.isfinite(f_r_hz) and f_r_hz > 0:
        for k, col in HARMONIC_COLORS.items():
            hz = k * f_r_hz
            if lo_hz <= hz <= hi_hz * 1.1:
                ax.axvline(hz, color=col, lw=0.9, linestyle=":",
                           alpha=0.9, label=f"{k}×f_r={hz*60:.1f} bpm")

    # Masimo target
    if np.isfinite(masimo_pr_bpm):
        ax.axvline(masimo_pr_bpm / 60.0, color="green", lw=1.5, linestyle="--",
                   label=f"Masimo {masimo_pr_bpm:.1f} bpm")

    # Picked peak
    ax.axvline(heart_peak_hz, color="red", lw=1.5, linestyle="-",
               label=f"picked {heart_peak_hz*60:.1f} bpm")


def _plot_window_row(
    axes: list[plt.Axes],
    freqs_full: np.ndarray,
    power_full: np.ndarray,
    heart_freqs: np.ndarray,
    heart_spectrum: np.ndarray,
    f_r_hz: float,
    masimo_pr_bpm: float,
    heart_peak_hz: float,
    hr_bpm: float,
    error_bpm: float,
    win_idx: int,
    start_s: float,
) -> None:
    title = (
        f"win={win_idx}  t={start_s:.0f}s  "
        f"radar={hr_bpm:.1f}  masimo={masimo_pr_bpm:.1f}  "
        f"err={error_bpm:+.1f} bpm  f_r={f_r_hz:.4f} Hz"
    )

    for ax, (lo, hi), label in zip(
        axes,
        [(0.1, 3.0), (0.5, 1.8)],
        ["Full spectrum 0.1–3.0 Hz", "Zoom 0.5–1.8 Hz (gap + cardiac band)"],
    ):
        mask = (freqs_full >= lo) & (freqs_full <= hi)
        ax.semilogy(freqs_full[mask], power_full[mask],
                    lw=0.9, color="steelblue", alpha=0.85, label="full spectrum")

        # Overlay heart_spectrum in cardiac band region
        hm = (heart_freqs >= max(lo, CARDIAC_LO_HZ)) & (heart_freqs <= min(hi, CARDIAC_HI_HZ))
        if hm.any():
            ax.semilogy(heart_freqs[hm], heart_spectrum[hm],
                        lw=1.6, color="darkorange", alpha=0.8,
                        linestyle="--", label="heart_spectrum (pipeline)")

        _annotate_spectrum(ax, freqs_full, f_r_hz, masimo_pr_bpm,
                           heart_peak_hz, lo, hi)
        ax.set_xlim(lo, hi)
        ax.set_xlabel("Frequency (Hz)", fontsize=7)
        ax.set_ylabel("Power (log)", fontsize=7)
        ax.set_title(f"{label}\n{title}", fontsize=7)
        ax.tick_params(labelsize=6)
        ax.legend(fontsize=5, loc="upper right", ncol=2)

    # Add secondary bpm axis to first axes
    ax0 = axes[0]
    ax0_bpm = ax0.secondary_xaxis("top",
        functions=(lambda hz: hz * 60, lambda bpm: bpm / 60))
    ax0_bpm.set_xlabel("Frequency (bpm)", fontsize=6)
    ax0_bpm.tick_params(labelsize=5)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(sessions: list[str]) -> None:
    cfg      = yaml.safe_load(EXP_CFG_PATH.read_text(encoding="utf-8"))
    manifest = pd.read_csv(MANIFEST)
    run_dir  = _latest_run_dir()
    fs       = float(cfg["radar"]["frame_rate_hz"])

    print(f"Latest run dir: {run_dir.name}")
    print(f"Cardiac band: {CARDIAC_LO_HZ}–{CARDIAC_HI_HZ} Hz  "
          f"({CARDIAC_LO_HZ*60:.0f}–{CARDIAC_HI_HZ*60:.0f} bpm)\n")

    for sid in sessions:
        rows = manifest[manifest["session_id"] == sid]
        if rows.empty:
            print(f"SKIP {sid} — not in manifest");  continue

        row = rows.iloc[0]
        print(f"{'='*60}")
        print(f"  {sid}  |  {row['posture']}  |  {row['distance_cm']} cm  "
              f"|  bin {int(row['locked_bin'])}")
        print(f"{'='*60}")

        try:
            print("  Running pipeline...")
            window_results = _run_session(row, cfg)
            comparison     = _load_comparison(sid, run_dir)
            merged         = _merge(window_results, comparison)
        except Exception as exc:
            print(f"  ERROR: {exc}");  continue

        # Select windows: worst N + best N (with finite masimo)
        fin = merged.dropna(subset=["masimo_pr_bpm", "hr_bpm", "error_bpm"])
        fin = fin.sort_values("abs_error_bpm", ascending=False)
        worst  = fin.head(N_WORST)
        best   = fin.tail(N_BEST)
        sel    = pd.concat([worst, best]).drop_duplicates("window_index")
        sel    = sel.sort_values("abs_error_bpm", ascending=False)

        # ---- Stdout table ----
        print(f"\n  {'win':>4}  {'t(s)':>5}  {'err':>6}  "
              f"{'f_r_hz':>7}  {'2xfr_hz':>8}  {'gap_hz':>7}  "
              f"{'pwr_edge/pwr_masimo':>20}")
        print(f"  {'-'*4}  {'-'*5}  {'-'*6}  "
              f"{'-'*7}  {'-'*8}  {'-'*7}  {'-'*20}")

        for _, r in sel.iterrows():
            phase   = r["phase_clean"]
            freqs_f, pwr_f = _full_spectrum(phase, fs)

            f_r     = float(r["f_r_hz"])
            two_fr  = 2 * f_r if np.isfinite(f_r) and f_r > 0 else float("nan")
            gap_hz  = CARDIAC_LO_HZ - two_fr if np.isfinite(two_fr) else float("nan")

            p_edge  = _interp_power(freqs_f, pwr_f, CARDIAC_LO_HZ)
            masimo_hz = float(r["masimo_pr_bpm"]) / 60.0 if np.isfinite(float(r["masimo_pr_bpm"])) else float("nan")
            p_mas   = _interp_power(freqs_f, pwr_f, masimo_hz) if np.isfinite(masimo_hz) else float("nan")
            ratio   = p_edge / p_mas if np.isfinite(p_mas) and p_mas > 0 else float("nan")

            t_s     = float(r["start_frame"]) / fs
            err_s   = f"{float(r['error_bpm']):+.1f}"
            print(
                f"  {int(r['window_index']):>4}  {t_s:>5.0f}  {err_s:>6}  "
                f"{f_r:>7.4f}  {two_fr:>8.4f}  {gap_hz:>7.4f}  "
                f"{ratio:>20.1f}×"
            )

        print()

        # ---- Figure ----
        n_win  = len(sel)
        n_cols = 2    # full + zoom side by side
        fig    = plt.figure(figsize=(16, 4.0 * n_win))
        gs     = gridspec.GridSpec(n_win, n_cols, figure=fig, hspace=0.55, wspace=0.3)
        fig.suptitle(
            f"{sid} — leakage diagnostic  "
            f"(blue=full spectrum, orange=pipeline heart_spectrum,\n"
            f"red=picked peak, green=Masimo, dotted=k×f_r,\n"
            f"shading: gold=resp band, pink=gap zone, blue=cardiac band)",
            fontsize=9,
        )

        for i, (_, r) in enumerate(sel.iterrows()):
            phase         = r["phase_clean"]
            freqs_f, pwr_f = _full_spectrum(phase, fs)
            axes_row      = [fig.add_subplot(gs[i, j]) for j in range(n_cols)]
            t_s           = float(r["start_frame"]) / fs

            _plot_window_row(
                axes          = axes_row,
                freqs_full    = freqs_f,
                power_full    = pwr_f,
                heart_freqs   = r["heart_freqs_hz"],
                heart_spectrum= r["heart_spectrum"],
                f_r_hz        = float(r["f_r_hz"]),
                masimo_pr_bpm = float(r["masimo_pr_bpm"]),
                heart_peak_hz = float(r["heart_peak_hz"]),
                hr_bpm        = float(r["hr_bpm"]),
                error_bpm     = float(r["error_bpm"]),
                win_idx       = int(r["window_index"]),
                start_s       = t_s,
            )

        FIGURES_DIR.mkdir(parents=True, exist_ok=True)
        out = FIGURES_DIR / f"diag_leakage_{sid}.png"
        fig.savefig(out, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"  Figure saved -> {out}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sessions", nargs="+", default=DEFAULT_SESSIONS,
        help="Session IDs to diagnose (default: exp003 exp008)",
    )
    args = parser.parse_args()
    main(args.sessions)
