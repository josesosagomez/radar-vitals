#!/usr/bin/env python3
"""Generate per-session overlay plots from the latest exp_eca_all run.

Reads each session's comparison.csv, calls compare.overlay_plot(), and
saves overlay.png alongside the CSV.

Run from repo root:
    python -X utf8 scripts/plot_overlays.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src import compare  # noqa: E402

RUN_DIR = REPO_ROOT / "results" / "exp_eca_all" / "20260621_095804"


def main() -> None:
    session_dirs = sorted(p for p in RUN_DIR.iterdir() if p.is_dir())

    for sess_dir in session_dirs:
        csv_path = sess_dir / "comparison.csv"
        if not csv_path.exists():
            print(f"  SKIP {sess_dir.name} — no comparison.csv")
            continue

        merged = pd.read_csv(csv_path)
        session_id = sess_dir.name
        n_total  = len(merged)
        n_finite = int(merged["hr_bpm"].notna().sum())
        n_nan    = n_total - n_finite

        fin = merged.dropna(subset=["hr_bpm", "error_bpm"])
        if len(fin) > 0:
            mae  = fin["abs_error_bpm"].mean()
            bias = fin["error_bpm"].mean()
            stats = f"MAE={mae:.1f} bpm  bias={bias:+.1f} bpm  N={n_finite}/{n_total}"
        else:
            stats = f"all NaN  N=0/{n_total}"

        title = f"{session_id}  |  ECA+AHET  |  {stats}"
        out   = sess_dir / "overlay.png"
        compare.overlay_plot(merged, out, title=title)
        print(f"  {session_id}: {out}")


if __name__ == "__main__":
    main()
