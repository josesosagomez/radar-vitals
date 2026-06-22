#!/usr/bin/env python3
"""Diagnostic: inspect heart_spectrum intermediates for failing seated sessions.

Runs the ECA+AHET pipeline in-memory on one or more sessions, loads the
per-window Masimo comparison from the latest exp_eca_all run, and plots the
heart-band spectrum for the worst-error windows.  The key question:

    Is the picked HR peak sitting at 4 × f_r (the 4th respiratory harmonic)?

If the 4×f_r hypothesis is correct, every bad window will show:
  - A dominant spectrum peak at ~4 × f_r × 60 bpm
  - A green "Masimo target" line sitting at a distinctly different frequency
  - The picked peak (red) coinciding with the 4×f_r harmonic (orange dashed)

Outputs
-------
  Stdout: per-session table (window, time, hr_bpm, masimo_pr, error, f_r_hz,
          4×f_r bpm, distance between picked peak and 4×f_r).
  figures/diag_heart_spectrum_<session>.png : heart spectrum plots for the
      N_WORST worst-error windows (3 columns, rows as needed).

Run from repo root:
    python -X utf8 scripts/diag_heart_spectrum.py
    python -X utf8 scripts/diag_heart_spectrum.py --sessions exp003 exp005
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src import masimo as masimo_mod, radar_io, vitals  # noqa: E402

MANIFEST      = REPO_ROOT / "data" / "manifest.local.csv"
DATA_RAW      = REPO_ROOT / "data" / "raw"
RESULTS_BASE  = REPO_ROOT / "results" / "exp_eca_all"
EXP_CFG_PATH  = REPO_ROOT / "experiments" / "exp_eca_all" / "config.yaml"
FIGURES_DIR   = REPO_ROOT / "figures"

DEFAULT_SESSIONS = ["exp003", "exp005"]
N_WORST          = 6          # worst-error windows to plot per session
N_BEST           = 2          # best-error windows to plot as a "good" reference
HARMONIC_COLORS  = {2: "goldenrod", 3: "darkorange", 4: "red", 5: "purple", 6: "brown"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _latest_run_dir() -> Path:
    runs = sorted(RESULTS_BASE.iterdir())
    if not runs:
        raise FileNotFoundError(f"No runs found under {RESULTS_BASE}")
    return runs[-1]


def _resolve_bin_paths(session_id: str) -> list[Path]:
    split0 = DATA_RAW / f"{session_id}_0.bin"
    split1 = DATA_RAW / f"{session_id}_1.bin"
    single  = DATA_RAW / f"{session_id}.bin"
    if split0.exists():
        return [split0, split1] if split1.exists() else [split0]
    if single.exists():
        return [single]
    raise FileNotFoundError(f"No .bin file for '{session_id}' in {DATA_RAW}")


def _total_frames(bin_paths: list[Path], cfg_r: dict) -> int:
    total_bytes = sum(p.stat().st_size for p in bin_paths)
    bpf = cfg_r["num_chirps_per_frame"] * cfg_r["num_rx"] * cfg_r["num_adc_samples"] * 4
    return total_bytes // bpf


def _run_session_intermediates(
    row: pd.Series,
    cfg: dict,
) -> list[dict]:
    """Run the pipeline and return full window_results list (all intermediates in memory)."""
    session_id = str(row["session_id"])
    locked_bin = int(row["locked_bin"])
    interval   = str(row["stationary_intervals"])
    trim_s, end_s = (int(x) for x in interval.split("-"))

    r             = cfg["radar"]
    frame_rate_hz = float(r["frame_rate_hz"])
    trim_frames   = int(trim_s * frame_rate_hz)
    end_frames    = int(end_s  * frame_rate_hz)

    window_s      = cfg["processing"]["window_s"]
    hop_s         = cfg["processing"]["hop_s"]
    window_frames = int(window_s * frame_rate_hz)
    hop_frames    = int(hop_s    * frame_rate_hz)
    k_max         = cfg["eca"]["k_max"]
    ahet_dev      = cfg["eca"]["ahet_deviation_hz"]

    bin_paths = _resolve_bin_paths(session_id)
    n_frames  = _total_frames(bin_paths, r)

    chirp_cfg = radar_io.ChirpConfig(
        num_adc_samples      = r["num_adc_samples"],
        num_rx               = r["num_rx"],
        num_tx               = r["num_tx"],
        num_chirps_per_frame = r["num_chirps_per_frame"],
        num_frames           = n_frames,
        frame_rate_hz        = frame_rate_hz,
        range_resolution_m   = r["range_resolution_m"],
    )

    bin_arg  = bin_paths[0] if len(bin_paths) == 1 else bin_paths
    cube     = radar_io.read_adc_bin(bin_arg, chirp_cfg, trim_frames=trim_frames)
    profiles = radar_io.range_profile(cube)
    del cube

    analysis_profiles = profiles[trim_frames:end_frames]
    del profiles

    raxis  = radar_io.range_axis_m(r["num_adc_samples"], r["range_resolution_m"])
    params = vitals.VitalsParams(fs_hz=frame_rate_hz, gate_min_m=0.5, gate_max_m=2.5)

    window_results = vitals.run_pipeline_locked(
        analysis_profiles, raxis, params,
        window_frames, hop_frames,
        locked_bin        = locked_bin,
        k_max             = k_max,
        ahet_deviation_hz = ahet_dev,
    )

    # Attach epochs (same as run.py)
    t0 = int(row["radar_start_epoch_seconds"]) + trim_s
    for out in window_results:
        out["start_epoch"] = t0 + out["start_frame"] / frame_rate_hz
        out["end_epoch"]   = t0 + out["end_frame"]   / frame_rate_hz

    return window_results


def _load_comparison(session_id: str, run_dir: Path) -> pd.DataFrame:
    csv = run_dir / session_id / "comparison.csv"
    if not csv.exists():
        raise FileNotFoundError(
            f"comparison.csv not found for {session_id} in {run_dir}. "
            "Run experiments/exp_eca_all/run.py first."
        )
    return pd.read_csv(csv)


def _merge_intermediates(
    window_results: list[dict],
    comparison: pd.DataFrame,
) -> pd.DataFrame:
    """Join window_results (list of dicts) with comparison CSV on window_index."""
    wdf = pd.DataFrame([{
        "window_index":     o["window_index"],
        "start_frame":      o["start_frame"],
        "hr_bpm":           o["hr_bpm"],
        "f_r_hz":           o["f_r_hz_used"],
        "ahet_verified":    o["ahet_verified"],
        "heart_freqs_hz":   o["heart_freqs_hz"],
        "heart_spectrum":   o["heart_spectrum"],
        "heart_spectrum_fp":o["heart_spectrum_first_pass"],
        "heart_peak_hz":    o["heart_peak_hz"],
        "resp_peak_hz":     o["resp_peak_refined_hz"],
        "start_epoch":      o["start_epoch"],
    } for o in window_results])

    comp_slim = comparison[["window_index", "masimo_pr_bpm", "error_bpm", "abs_error_bpm"]].copy()
    merged = wdf.merge(comp_slim, on="window_index", how="left")
    return merged


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def _plot_spectrum_window(
    ax: plt.Axes,
    freqs_hz: np.ndarray,
    spectrum: np.ndarray,
    spectrum_fp: np.ndarray,
    heart_peak_hz: float,
    f_r_hz: float,
    masimo_pr_bpm: float,
    hr_bpm: float,
    error_bpm: float,
    win_idx: int,
    start_s: float,
) -> None:
    freqs_bpm = freqs_hz * 60.0

    # Spectra
    ax.plot(freqs_bpm, spectrum_fp, lw=0.8, color="steelblue", alpha=0.4,
            label="spectrum (1st pass)")
    ax.plot(freqs_bpm, spectrum,    lw=1.2, color="steelblue", alpha=0.9,
            label="spectrum (final)")

    # Picked peak
    ax.axvline(heart_peak_hz * 60, color="red", lw=1.8, linestyle="-",
               label=f"picked: {hr_bpm:.1f} bpm")

    # Masimo target
    if np.isfinite(masimo_pr_bpm):
        ax.axvline(masimo_pr_bpm, color="green", lw=1.8, linestyle="--",
                   label=f"Masimo: {masimo_pr_bpm:.1f} bpm")

    # f_r harmonics k=2..6
    if np.isfinite(f_r_hz) and f_r_hz > 0:
        for k, col in HARMONIC_COLORS.items():
            hz = k * f_r_hz
            bpm_val = hz * 60
            ax.axvline(bpm_val, color=col, lw=1.0, linestyle=":",
                       alpha=0.85, label=f"{k}×f_r={bpm_val:.1f}")

    ax.set_yscale("log")
    ax.set_xlabel("Frequency (bpm)", fontsize=7)
    ax.set_ylabel("Power (log)", fontsize=7)
    err_str = f"{error_bpm:+.1f}" if np.isfinite(error_bpm) else "NaN"
    ax.set_title(
        f"win={win_idx}  t={start_s:.0f}s  "
        f"radar={hr_bpm:.1f}  masimo={masimo_pr_bpm:.1f}  err={err_str} bpm",
        fontsize=7,
    )
    ax.tick_params(labelsize=6)
    ax.legend(fontsize=5, loc="upper right", ncol=2)


def _plot_session(
    session_id: str,
    merged: pd.DataFrame,
    frame_rate_hz: float,
) -> None:
    # Select worst-N and best-N windows that have finite masimo_pr_bpm
    fin = merged.dropna(subset=["masimo_pr_bpm", "hr_bpm", "error_bpm"])
    fin = fin.sort_values("abs_error_bpm", ascending=False)

    worst = fin.head(N_WORST)
    best  = fin.tail(N_BEST)
    selected = pd.concat([worst, best]).drop_duplicates("window_index")
    selected = selected.sort_values("abs_error_bpm", ascending=False)

    n_plots = len(selected)
    if n_plots == 0:
        print(f"  {session_id}: no finite windows to plot")
        return

    ncols = 3
    nrows = (n_plots + ncols - 1) // ncols
    fig, axes = plt.subplots(nrows, ncols, figsize=(18, 4.5 * nrows), squeeze=False)
    fig.suptitle(
        f"{session_id} — heart spectrum: worst {N_WORST} + best {N_BEST} windows  "
        f"(red=picked, green=Masimo, dotted=k×f_r)",
        fontsize=10,
    )

    for i, (_, row) in enumerate(selected.iterrows()):
        ax = axes[i // ncols][i % ncols]
        start_s = float(row["start_frame"]) / frame_rate_hz
        _plot_spectrum_window(
            ax,
            freqs_hz      = row["heart_freqs_hz"],
            spectrum      = row["heart_spectrum"],
            spectrum_fp   = row["heart_spectrum_fp"],
            heart_peak_hz = float(row["heart_peak_hz"]),
            f_r_hz        = float(row["f_r_hz"]),
            masimo_pr_bpm = float(row["masimo_pr_bpm"]),
            hr_bpm        = float(row["hr_bpm"]),
            error_bpm     = float(row["error_bpm"]),
            win_idx       = int(row["window_index"]),
            start_s       = start_s,
        )

    for i in range(n_plots, nrows * ncols):
        axes[i // ncols][i % ncols].set_visible(False)

    fig.tight_layout()
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    out = FIGURES_DIR / f"diag_heart_spectrum_{session_id}.png"
    fig.savefig(out, dpi=150, bbox_inches="tight")
    print(f"  Figure saved -> {out}")
    plt.close(fig)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(sessions: list[str]) -> None:
    cfg      = yaml.safe_load(EXP_CFG_PATH.read_text(encoding="utf-8"))
    manifest = pd.read_csv(MANIFEST)
    run_dir  = _latest_run_dir()
    frame_rate_hz = float(cfg["radar"]["frame_rate_hz"])

    print(f"Latest run dir: {run_dir.name}\n")

    for sid in sessions:
        rows = manifest[manifest["session_id"] == sid]
        if rows.empty:
            print(f"  SKIP {sid} — not in manifest")
            continue

        row = rows.iloc[0]
        print(f"{'='*60}")
        print(f"  {sid}  |  {row['posture']}  |  {row['distance_cm']} cm  "
              f"|  bin {int(row['locked_bin'])}")
        print(f"{'='*60}")

        try:
            print(f"  Running pipeline (loading .bin)...")
            window_results = _run_session_intermediates(row, cfg)
            comparison     = _load_comparison(sid, run_dir)
            merged         = _merge_intermediates(window_results, comparison)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            continue

        # ---- Stdout table ----
        fin = merged.dropna(subset=["masimo_pr_bpm", "hr_bpm", "error_bpm"])
        fin = fin.sort_values("abs_error_bpm", ascending=False)

        print(f"\n  {'win':>4}  {'t(s)':>5}  {'hr_bpm':>7}  {'masimo':>7}  "
              f"{'error':>7}  {'f_r_hz':>7}  {'4xfr':>7}  {'|pk-4fr|':>9}  ahet")
        print(f"  {'-'*4}  {'-'*5}  {'-'*7}  {'-'*7}  "
              f"{'-'*7}  {'-'*7}  {'-'*7}  {'-'*9}  {'-'*4}")

        for _, r in fin.iterrows():
            f_r  = float(r["f_r_hz"])
            four = f_r * 4 * 60 if np.isfinite(f_r) and f_r > 0 else float("nan")
            dist = abs(float(r["hr_bpm"]) - four) if np.isfinite(four) else float("nan")
            t_s  = float(r["start_frame"]) / frame_rate_hz
            ahet = "Y" if r["ahet_verified"] else "N"
            print(
                f"  {int(r['window_index']):>4}  {t_s:>5.0f}  "
                f"{float(r['hr_bpm']):>7.1f}  {float(r['masimo_pr_bpm']):>7.1f}  "
                f"{float(r['error_bpm']):>+7.1f}  {f_r:>7.4f}  "
                f"{four:>7.1f}  {dist:>9.2f}  {ahet}"
            )

        print()
        _plot_session(sid, merged, frame_rate_hz)
        print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sessions", nargs="+", default=DEFAULT_SESSIONS,
        help="Session IDs to diagnose (default: exp003 exp005)",
    )
    args = parser.parse_args()
    main(args.sessions)
