"""Reproducibility trace for the BR comparator design evidence (M3 / CLAUDE.md §3.1).

Regenerates the §1 table and the §2.3 exclusion percentages in
`notes/comparator_prespec_br.md` **from the Masimo reference alone**. NO radar output is read:
this script imports nothing from the radar pipeline and never opens `live_estimates.csv`,
`live_intermediates.npz`, or `adc_stream.bin`. The only radar-side file consulted is
`run_metadata.json`, and only for its `start_wall_utc` **timestamp** (to anchor the scoring
window grid) — never any estimate. Design evidence permitted by M3 = the Masimo trace, the
metronome command, and FFT-resolution arithmetic.

Determinism: fully deterministic, no RNG, no seed.

Window grid: non-overlapping 30 s windows anchored at `run_metadata.json`'s `start_wall_utc`,
`[start + k·30, start + (k+1)·30)` half-open, `k = 0, 1, …`. NOTE this origin is **approximate**:
`start_wall_utc` is written *before* capture startup (`live_demo.py:1213` vs `1300–1318`), so it is
not the frame-0 epoch — the exact scoring grid needs a persisted post-start `frame0_epoch`
(`notes/analysis_prespec.md` §7), which the exploratory captures do not have. This is adequate here
because the script produces only reference-characterization design evidence (within-window RRp
spread), which is robust to a few-second origin offset — not any frozen scoring number.

Gates: availability = >= 24 finite rr_bpm in the window; stationarity thresholds 2/3/5 bpm.

Run:
  conda run -n radar-vitals python scripts/derive_br_comparator_evidence.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from src.masimo import load_masimo  # noqa: E402  (reference parser only)

WINDOW_S = 30
MIN_FINITE = 24
STATIONARITY_BPM = (2.0, 3.0, 5.0)

# (label, session folder, commanded paced rate or None)
SESSIONS = [
    ("natural",           "20260713_172042_live_demo_massimo1", None),
    ("paced 16",          "20260713_182002_live_demo_massimo2", 16.0),
    ("sweep 12/15/18/21", "20260714_180523_live_demo_sweep",    None),
]
BASE = REPO / "results" / "live_demo"
CSV_NAME = {"20260713_172042_live_demo_massimo1": "demo_massimo1.csv",
            "20260713_182002_live_demo_massimo2": "demo_massimo2.csv",
            "20260714_180523_live_demo_sweep":    "demo_sweep.csv"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def start_epoch(folder: Path) -> float:
    meta = json.load(open(folder / "run_metadata.json"))
    return datetime.fromisoformat(meta["start_wall_utc"]).timestamp()


def windows(df, t0: float):
    """Yield finite rr_bpm arrays for the scoring-grid windows anchored at capture start t0."""
    e_last = int(df["epoch_utc"].max())
    k = 0
    while t0 + (k + 1) * WINDOW_S <= e_last + 1:
        lo, hi = t0 + k * WINDOW_S, t0 + (k + 1) * WINDOW_S
        w = df[(df["epoch_utc"] >= lo) & (df["epoch_utc"] < hi)]
        rr = w["rr_bpm"].to_numpy(dtype=float)
        yield rr[np.isfinite(rr)]
        k += 1


def main() -> None:
    hdr = (f"{'session':18s} {'n_win':>5s} {'rrFin%':>6s} {'PImed':>6s} "
           f"{'sprMed':>6s} {'sprMax':>6s} "
           + " ".join(f'>{t:g}%' for t in STATIONARITY_BPM)
           + f" {'availX%':>7s} {'metrOff':>7s} {'rrRange':>9s}")
    print(hdr)
    for label, folder, paced in SESSIONS:
        d = BASE / folder
        df = load_masimo(d / CSV_NAME[folder])
        t0 = start_epoch(d)
        rr_all = df["rr_bpm"].to_numpy(dtype=float)
        pi_all = df["pi"].to_numpy(dtype=float)

        spreads, avail_excl, metr_off, n_win = [], 0, [], 0
        for rr in windows(df, t0):
            n_win += 1
            if len(rr) < MIN_FINITE:
                avail_excl += 1
                continue
            spreads.append(np.percentile(rr, 90) - np.percentile(rr, 10))
            if paced is not None:
                metr_off.append(np.median(rr) - paced)
        spreads = np.array(spreads)
        excl = [100.0 * np.mean(spreads > t) if len(spreads) else float("nan")
                for t in STATIONARITY_BPM]
        rr_fin = rr_all[np.isfinite(rr_all)]
        moff = f"{np.median(metr_off):+.1f}" if metr_off else "-"
        rng = f"{np.nanmin(rr_fin):.0f}-{np.nanmax(rr_fin):.0f}"
        print(f"{label:18s} {n_win:>5d} {100*np.mean(np.isfinite(rr_all)):>5.0f}% "
              f"{np.nanmedian(pi_all):>6.1f} "
              f"{np.median(spreads):>6.1f} {spreads.max():>6.1f} "
              + " ".join(f'{e:>3.0f}' for e in excl)
              + f" {100*avail_excl/n_win:>6.0f}% {moff:>7s} {rng:>9s}")
    print("\nConsumed-input SHA-256 (CSV = reference; run_metadata.json = window origin):")
    for label, folder, _ in SESSIONS:
        d = BASE / folder
        print(f"  {label:18s} csv  {sha256(d / CSV_NAME[folder])}  {CSV_NAME[folder]}")
        print(f"  {'':18s} meta {sha256(d / 'run_metadata.json')}  run_metadata.json")


if __name__ == "__main__":
    main()
