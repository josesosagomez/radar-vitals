#!/usr/bin/env python3
"""Plot subject-bin energy and motion-spike flags for one or all sessions.

Reads quality_metrics/subject_bin_energy and quality_metrics/motion_spike_flagged
from the time-domain HDF5 cubes produced by add_quality_mask.py, then saves a
PNG for each requested session to results/diagnostics/.

Usage
-----
Single session:
    python -X utf8 scripts/diag_motion_spike.py --session exp001

All sessions:
    python -X utf8 scripts/diag_motion_spike.py --all

Override factor / half-window to overlay a different threshold for comparison:
    python -X utf8 scripts/diag_motion_spike.py --session exp001 --factor 8 --half-window 100
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import yaml
from scipy import ndimage

REPO_ROOT   = Path(__file__).resolve().parents[1]
CUBES_DIR   = REPO_ROOT / "data" / "processed" / "time_domain_cubes"
MANIFEST    = REPO_ROOT / "data" / "manifest.local.csv"
CONFIG_PATH = REPO_ROOT / "steps" / "step_4" / "config.yaml"
OUT_DIR     = REPO_ROOT / "results" / "diagnostics"

try:
    import matplotlib
    matplotlib.use("Agg")          # headless — always save to file
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
except ImportError:
    print("ERROR: matplotlib is required. Install it with: pip install matplotlib")
    sys.exit(1)


def _plot_session(
    session_id: str,
    factor: float,
    half_window: int,
    frame_rate_hz: float = 20.0,
) -> Path:
    h5_path = CUBES_DIR / f"{session_id}.h5"
    if not h5_path.exists():
        raise FileNotFoundError(
            f"{h5_path} not found — run add_quality_mask.py first"
        )

    with h5py.File(h5_path, "r") as f:
        if "quality_metrics/subject_bin_energy" not in f:
            raise KeyError(
                f"{session_id}: subject_bin_energy not found in HDF5 — "
                "re-run add_quality_mask.py with motion_spike block present"
            )
        energy     = f["quality_metrics/subject_bin_energy"][:]
        stored_flagged = f["quality_metrics/motion_spike_flagged"][:]
        stored_factor  = float(f["quality_mask"].attrs.get("motion_spike_factor", factor))
        stored_hw      = int(f["quality_mask"].attrs.get("motion_spike_half_window", half_window))
        posture    = str(f.attrs.get("posture", "unknown"))
        locked_bin = int(f["quality_mask"].attrs.get("motion_spike_locked_bin", -1))

    n = len(energy)
    t = np.arange(n) / frame_rate_hz   # seconds into analysis window

    # Always recompute flags from the stored energy using the requested parameters.
    # This ensures the shading is consistent with the displayed threshold line,
    # even when --factor / --half-window differ from what was used to write the HDF5.
    eps = np.finfo(np.float32).tiny
    rolling_med = ndimage.median_filter(
        energy, size=2 * half_window + 1, mode="reflect"
    )
    threshold = factor * rolling_med
    flagged   = energy > factor * np.maximum(rolling_med, eps)

    using_stored = (factor == stored_factor and half_window == stored_hw)

    n_flagged = int(flagged.sum())
    pct       = 100.0 * n_flagged / n if n else 0.0

    # ------------------------------------------------------------------ figure
    fig, ax = plt.subplots(figsize=(14, 4))

    ax.plot(t, energy,    lw=0.5, color="steelblue", label="subject_bin_energy")
    ax.plot(t, threshold, lw=1.0, color="tomato",    label=f"{factor}× rolling median")

    # Shade flagged spans
    in_run = False
    for i, fl in enumerate(flagged):
        if fl and not in_run:
            x0, in_run = t[i], True
        elif not fl and in_run:
            ax.axvspan(x0, t[i], color="tomato", alpha=0.25, linewidth=0)
            in_run = False
    if in_run:
        ax.axvspan(x0, t[-1], color="tomato", alpha=0.25, linewidth=0)

    override_note = "" if using_stored else f"  [stored: factor={stored_factor}, hw={stored_hw}]"
    patch = mpatches.Patch(color="tomato", alpha=0.4,
                           label=f"flagged ({n_flagged}/{n}, {pct:.1f}%){override_note}")
    ax.legend(handles=[ax.lines[0], ax.lines[1], patch], fontsize=9)

    ax.set_xlabel("Time into analysis window (s)")
    ax.set_ylabel("Mean |FFT[bin]|²  (a.u.)")
    ax.set_title(
        f"{session_id}  —  {posture}  —  locked_bin={locked_bin}  —  "
        f"factor={factor}, half_window={half_window}{override_note}"
    )
    ax.set_xlim(0, t[-1])
    fig.tight_layout()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{session_id}_motion_spike.png"
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def main() -> None:
    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    ms_cfg = cfg.get("soft_failures", {}).get("motion_spike", {})

    default_factor  = float(ms_cfg.get("motion_spike_factor",  5.0))
    default_hw      = int(ms_cfg.get("motion_window_frames", 50))

    parser = argparse.ArgumentParser(
        description="Plot subject-bin energy and motion-spike flags per session"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--session", metavar="EXPID",
                       help="Single session ID (e.g. exp001)")
    group.add_argument("--all", action="store_true",
                       help="Plot all sessions in manifest")
    parser.add_argument("--factor", type=float, default=default_factor,
                        help=f"Spike factor to overlay (default: {default_factor} from config)")
    parser.add_argument("--half-window", type=int, default=default_hw,
                        dest="half_window",
                        help=f"Rolling-median half-window (default: {default_hw} from config)")
    args = parser.parse_args()

    manifest    = pd.read_csv(MANIFEST)
    session_ids = manifest["session_id"].tolist() if args.all else [args.session]

    failed = []
    for sid in session_ids:
        try:
            out = _plot_session(sid, args.factor, args.half_window)
            print(f"  {sid:10s}  →  {out}")
        except Exception as exc:
            print(f"  {sid:10s}  ERROR: {exc}")
            failed.append(sid)

    if failed:
        print(f"\nFailed: {failed}")
    else:
        print(f"\nAll plots saved to {OUT_DIR}/")


if __name__ == "__main__":
    main()
