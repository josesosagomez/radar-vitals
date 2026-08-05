"""M8 Step 1b real-data arm: Ahmed fixed-H harmonic accumulation at EVERY candidate bin.

The first time this published method has been run on real radar. It was validated only in
simulation (Ahmed et al., "Discovering the Unseen", DOI 10.1109/TRS.2024.3412915), and
M8 Step 1a already found it selects subharmonics on the paper's own reconstructed figure
(`notes/approach.md` §5.7) — so the question this answers is narrow and specific:

    on real captures, does Ahmed produce an estimate, at ANY range bin, at what coverage?

**Why every bin and not one.** The range bin is chosen upstream of the estimator, and
Ahmed's original model is a pulse radar at a fixed fast-time sample — it never had FMCW
range bins to choose between, so the method simply assumes the slow-time signal is handed
to it. Feeding it one bin would risk measuring the bin rather than the method. Sweeping all
14 removes the choice entirely: the deployable number (production's re-derived warmup lock)
and the ceiling (best bin per capture) both fall out of the same run, and the comparison
against production is on identical cells.

**This script opens NO Masimo file**, exactly like `scripts/diagnose_bin_sweep.py`. Scoring
against the reference is a separate step, so no bin, band or threshold here can be chosen
by reference agreement.

Parented by the frozen synthetic gate bundle (`results/m8/step1b/LATEST.json`), whose run_id
and manifest hash are recorded in `run_meta.json`. That link is what makes the ordering
claim — synthetic control passed *before* real data was opened — checkable rather than
asserted.

Usage
-----
    conda run -n radar-vitals python -X utf8 scripts/m8_ahmed_all_bins.py --all
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from src.m4.window_grid import (                                        # noqa: E402
    FRAMES_PER_WINDOW, n_complete_windows, window_frame_span,
)
from src.m8.ahmed_transfer import (                                     # noqa: E402
    ESTIMATOR_ID, REAL_REPRESENTATIVE_DOMAIN, AhmedPhaseConfig,
    AhmedPhaseEstimatorSuite,
)
from src.warmup_select import derive_candidate_bins                     # noqa: E402

import diagnose_bin_drift as bindrift                                   # noqa: E402
from diagnose_bin_sweep import decode_frame_range, words_per_frame      # noqa: E402

#: The eight canonical captures. `live_test1` is excluded: no Masimo reference, so it can
#: never contribute an agreement number (`notes/capture_inventory.md` §1).
DEFAULT_CAPTURES = (
    "massimo1", "massimo2", "sweep", "massimo3",
    "massimo4", "massimo5", "massimo6", "massimo7",
)

ROW_COLUMNS = [
    "capture_id", "k", "frame_start", "frame_end", "bin", "range_m",
    "arm_id", "harmonics", "suppression_profile",
    "br_valid", "br_bpm", "br_selected_fft_bin", "br_reason",
    "hr_valid", "hr_bpm", "hr_selected_fft_bin", "hr_margin", "hr_reason",
    "f_r_hz", "n_candidates", "n_supported", "shared_signal_hash",
    "dsp_failed", "dsp_error",
]


def _f(x) -> float | None:
    """NaN/None -> empty cell, so a missing value never reads as a number."""
    if x is None:
        return None
    v = float(x)
    return None if not np.isfinite(v) else round(v, 6)


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    """LF-pinned stdlib CSV. Line endings are load-bearing in this repo."""
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(columns)
        for r in rows:
            w.writerow(["" if r.get(c) is None else r[c] for c in columns])


def gate_parent(root: Path) -> dict:
    """The frozen synthetic gate bundle this real-data run descends from."""
    latest = root / "results" / "m8" / "step1b" / "LATEST.json"
    if not latest.is_file():
        raise SystemExit(
            f"no frozen gate bundle at {latest}. Run "
            "`scripts/m8_ahmed_transfer.py synthetic --out results/m8/step1b` first: the "
            "real-data arm is only meaningful if the synthetic control passed before it."
        )
    return json.loads(latest.read_text(encoding="utf-8"))


def sweep_capture(capture_dir: Path, cfg: dict, suite: AhmedPhaseEstimatorSuite,
                  max_windows: int | None, verbose: bool) -> tuple[list[dict], dict]:
    """Every arm, every candidate bin, every complete window of one capture."""
    capture_id = capture_dir.name
    meta = json.loads((capture_dir / "run_metadata.json").read_text(encoding="utf-8"))
    chirp = bindrift.validate_decode_geometry(cfg, meta, capture_id)
    raw_path = capture_dir / "adc_stream.bin"

    bytes_per_frame = words_per_frame(chirp) * 2
    size = raw_path.stat().st_size
    if size % bytes_per_frame != 0:
        raise ValueError(f"{capture_id}: {size} B is not a whole number of frames")
    n_frames = size // bytes_per_frame
    n_windows = n_complete_windows(int(n_frames), FRAMES_PER_WINDOW)
    if max_windows is not None:
        n_windows = min(n_windows, max_windows)

    bins = derive_candidate_bins(cfg)
    res_m = float(cfg["profile"]["range_resolution_m"])
    fs = float(chirp.frame_rate_hz)

    if verbose:
        print(f"  {capture_id}: {n_windows} windows x {len(bins)} bins x "
              f"{len(suite.arm_specs)} arms = "
              f"{n_windows * len(bins) * len(suite.arm_specs)} rows", flush=True)

    rows: list[dict] = []
    raw = np.memmap(raw_path, dtype="<i2", mode="r")
    t0 = time.monotonic()
    try:
        for k in range(n_windows):
            start, end = window_frame_span(k, FRAMES_PER_WINDOW)
            # Decoded ONCE per window and reused across all 14 bins -- the decode, not the
            # estimator, is the expensive part.
            cube = decode_frame_range(raw, chirp, start, end - start)
            for b in bins:
                base = {
                    "capture_id": capture_id, "k": k,
                    "frame_start": start, "frame_end": end,
                    "bin": b, "range_m": round(b * res_m, 4),
                }
                try:
                    result = suite(cube, b, fs)
                except Exception as exc:                        # noqa: BLE001
                    for spec in suite.arm_specs:
                        rows.append({**base, "arm_id": spec.arm_id,
                                     "harmonics": spec.harmonic_count,
                                     "suppression_profile": spec.suppression_profile,
                                     "dsp_failed": True, "dsp_error": str(exc)})
                    continue
                shared = result.shared_evidence
                for spec in suite.arm_specs:
                    native = result.arm_native_results[spec.arm_id]
                    heart = native.get("heart_evidence") or {}
                    breath = native.get("breath_evidence") or {}
                    rows.append({
                        **base,
                        "arm_id": spec.arm_id,
                        "harmonics": spec.harmonic_count,
                        "suppression_profile": spec.suppression_profile,
                        "br_valid": bool(native.get("br_valid", False)),
                        "br_bpm": _f(native.get("br_bpm")),
                        "br_selected_fft_bin": breath.get("selected_bin"),
                        "br_reason": native.get("br_confidence"),
                        "hr_valid": bool(native.get("hr_valid", False)),
                        "hr_bpm": _f(native.get("hr_raw")),
                        "hr_selected_fft_bin": heart.get("selected_bin"),
                        "hr_margin": _f(heart.get("margin")),
                        "hr_reason": native.get("rej_reason"),
                        "f_r_hz": _f(native.get("f_r_hz")),
                        "n_candidates": heart.get("n_candidates"),
                        "n_supported": heart.get("n_supported"),
                        "shared_signal_hash": native.get("shared_signal_hash"),
                        "dsp_failed": False, "dsp_error": None,
                    })
            del cube
            if verbose and (k + 1) % 5 == 0:
                print(f"    window {k + 1}/{n_windows} "
                      f"({time.monotonic() - t0:.0f}s)", flush=True)
    finally:
        del raw

    summary = {
        "capture_id": capture_id, "n_frames": int(n_frames),
        "n_windows": int(n_windows), "n_bins": len(bins),
        "n_arms": len(suite.arm_specs), "n_rows": len(rows),
        "recorded_live_lock": meta.get("locked_bin"),
        "elapsed_s": round(time.monotonic() - t0, 1),
    }
    return rows, summary


def summarise(rows: list[dict], arm_ids: list[str]) -> list[dict]:
    """Per (capture, arm) yield, before any reference is consulted."""
    out = []
    caps = sorted({r["capture_id"] for r in rows})
    for cid in caps:
        for arm in arm_ids:
            sub = [r for r in rows if r["capture_id"] == cid and r["arm_id"] == arm]
            if not sub:
                continue
            n = len(sub)
            hr_ok = sum(1 for r in sub if r.get("hr_valid"))
            br_ok = sum(1 for r in sub if r.get("br_valid"))
            hr_vals = [r["hr_bpm"] for r in sub if r.get("hr_valid") and r.get("hr_bpm")]
            br_vals = [r["br_bpm"] for r in sub if r.get("br_valid") and r.get("br_bpm")]
            # Per-window ANY-bin yield: the question "does it work at any bin at all".
            ks = sorted({r["k"] for r in sub})
            any_bin_hr = sum(
                1 for k in ks
                if any(r.get("hr_valid") for r in sub if r["k"] == k)
            )
            out.append({
                "capture_id": cid, "arm_id": arm, "n_cells": n,
                "hr_valid_cells": hr_ok, "hr_valid_frac": round(hr_ok / n, 4),
                "br_valid_cells": br_ok, "br_valid_frac": round(br_ok / n, 4),
                "n_windows": len(ks),
                "windows_with_hr_at_any_bin": any_bin_hr,
                "any_bin_hr_frac": round(any_bin_hr / len(ks), 4) if ks else None,
                "hr_bpm_median": round(float(np.median(hr_vals)), 3) if hr_vals else None,
                "br_bpm_median": round(float(np.median(br_vals)), 3) if br_vals else None,
            })
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--captures", nargs="+", default=None,
                    help="capture-directory suffixes; default is all eight")
    ap.add_argument("--all", action="store_true", help="explicit form of the default")
    ap.add_argument("--config", type=Path,
                    default=REPO_ROOT / "scripts" / "live_demo_config.yaml")
    ap.add_argument("--max-windows", type=int, default=None)
    ap.add_argument("--out", type=Path,
                    default=REPO_ROOT / "results" / "m8" / "ahmed_all_bins")
    args = ap.parse_args(argv)

    parent = gate_parent(REPO_ROOT)
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))

    # REAL_REPRESENTATIVE_DOMAIN only. COLLISION_DOMAIN_FROM_FB's heart band starts at the
    # KNOWN SYNTHETIC breathing frequency -- a quantity that does not exist on real data.
    suite = AhmedPhaseEstimatorSuite(AhmedPhaseConfig(domain=REAL_REPRESENTATIVE_DOMAIN))
    arm_ids = [s.arm_id for s in suite.arm_specs]

    wanted = tuple(args.captures) if args.captures else DEFAULT_CAPTURES
    live_demo = REPO_ROOT / "results" / "live_demo"
    dirs = [d for d in sorted(live_demo.iterdir())
            if d.is_dir() and d.name.rsplit("_", 1)[-1] in wanted]
    missing = set(wanted) - {d.name.rsplit("_", 1)[-1] for d in dirs}
    if missing:
        raise SystemExit(f"missing capture(s) {sorted(missing)} under {live_demo}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"-> {out_dir}", flush=True)
    print(f"parent gate bundle: {parent['run_id']} ({parent['stage']})", flush=True)

    all_rows: list[dict] = []
    summaries = []
    t0 = time.monotonic()
    for d in dirs:
        rows, summary = sweep_capture(d, cfg, suite, args.max_windows, verbose=True)
        all_rows.extend(rows)
        summaries.append(summary)

    write_csv(out_dir / "windows.csv", ROW_COLUMNS, all_rows)
    per_arm = summarise(all_rows, arm_ids)
    write_csv(out_dir / "per_capture_arm.csv", list(per_arm[0].keys()), per_arm)

    meta = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": bindrift.get_git_commit(),
        "git_tree_clean": bindrift.is_tree_clean(),
        "estimator_id": ESTIMATOR_ID,
        "suite_id": suite.suite_id,
        "suite_config_hash": suite.suite_config_hash,
        "domain": REAL_REPRESENTATIVE_DOMAIN.domain_id,
        "arms": [
            {"arm_id": s.arm_id, "harmonics": s.harmonic_count,
             "suppression_profile": s.suppression_profile,
             "run_config_hash": s.run_config_hash}
            for s in suite.arm_specs
        ],
        "parent_gate_bundle": parent,
        "config": str(args.config),
        "captures": summaries,
        "n_rows": len(all_rows),
        "elapsed_s": round(time.monotonic() - t0, 1),
        "masimo_opened": False,
        "window_grid": {"frames_per_window": FRAMES_PER_WINDOW, "fs_hz": 20.0,
                        "authority": "notes/analysis_prespec.md §7"},
        "scope_note": (
            "Radar-side only; no reference was read. Scoring against Masimo is a separate "
            "step, so no bin, band or threshold here was chosen by reference agreement."
        ),
    }
    (out_dir / "run_meta.json").write_text(
        json.dumps(meta, indent=2, default=str), encoding="utf-8", newline="\n"
    )

    print(f"\n{len(all_rows)} rows in {meta['elapsed_s']}s")
    print("\nper-arm HR yield (fraction of cells with a verified estimate):")
    for arm in arm_ids:
        sub = [r for r in per_arm if r["arm_id"] == arm]
        cells = sum(r["n_cells"] for r in sub)
        hr = sum(r["hr_valid_cells"] for r in sub)
        wins = sum(r["windows_with_hr_at_any_bin"] for r in sub)
        nwin = sum(r["n_windows"] for r in sub)
        print(f"  {arm:<44} {hr}/{cells} cells ({hr / cells:.1%})   "
              f"any-bin windows {wins}/{nwin} ({wins / nwin:.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
