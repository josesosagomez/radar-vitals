"""Regenerable evidence for warmup range-bin selection.

Re-runs the REAL `src.warmup_select.run_warmup_selection` (same code path live_demo.py uses at
session start) on the first 600 frames of each recorded session's raw
`adc_stream.bin`, using that session's own stored config, and compares the
selected bin against the expected pick established by offline forensics
(see HISTORY.md). This is the tracked, regenerable replacement for the
throwaway scratchpad scripts used during the original investigation —
CLAUDE.md S3: every number in the paper (and every fix justified by a number)
must trace to a committed script + config + input-file hash.

Originally written for the 2026-07-14/15 energy-eligibility fix. It is NOT scoped
to that fix: it pins the selection against ANY later change to the DSP feeding it,
which is how the M2 respiration fix's effect on massimo1 surfaced. When a change
moves an expectation, update the note below to say which fix moved it and why —
do not just re-baseline the number, or the next disagreement will be unreadable.

Usage:
    conda run -n radar-vitals python scripts/validate_warmup_selection.py

Exits 0 if every session's selection matches the expected bin, 1 otherwise.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.live_demo import _sha256_file
from src.radar_io import ChirpConfig, read_adc_bin
from src.warmup_select import derive_candidate_bins, run_warmup_selection

WARMUP_FRAMES = 600

# (run_dir_name, expected_selected_bin, note)
#
# Every expectation must name the fix that last moved it. Two different fixes are
# in play and conflating them is what let this list go stale:
#   * "energy fix"  — the energy-eligibility prior (2026-07-14/15), which this
#                     script was originally written to validate.
#   * "M2 fix"      — the respiration band-edge veto in src/respiration.py, which
#                     landed later. It changes which bins produce a usable f_r, so
#                     it can move a selection even in a session the energy fix left
#                     alone. massimo1 is exactly that case.
#
# 20260713_170323_live_demo_live_test1 (expected 22) was removed on 2026-07-30:
# the capture directory no longer exists, so the entry made this script exit 1 on
# a missing input rather than on a real disagreement.
SESSIONS: list[tuple[str, int, str]] = [
    ("20260713_172042_live_demo_massimo1", 27,
     "M2 fix moved this from 23 — the bin this session's own live run recorded. "
     "Live, bins 24-29 were all pinned at the 6 bpm band edge (f_r=0.1 Hz), which "
     "makes ECA inert, so no bin earned an hr_valid pass and highest-energy bin 23 "
     "won on breathing evidence alone. With the edge veto, bin 27 resolves a real "
     "19.19 bpm, ECA cancels, AHET passes, and the +1000 hr_valid bonus takes it "
     "(score 120 -> 1120). Bins 22/23 are unchanged. NOTE: offline no longer "
     "reproduces this session's live bin — see HISTORY.md 2026-07-30."),
    ("20260713_182002_live_demo_massimo2", 26,
     "energy fix: was 20 pre-fix (energy rank 12, ground truth 23%). Unmoved by M2."),
    ("20260714_180523_live_demo_sweep", 26,
     "energy fix: was 21 pre-fix (replay-verified MAE 9.6->0.9 bpm). Unmoved by M2."),
]


def _validate_one(run_dir: Path, expected_bin: int, note: str) -> bool:
    meta_path = run_dir / "run_metadata.json"
    adc_path = run_dir / "adc_stream.bin"
    if not meta_path.exists() or not adc_path.exists():
        print(f"[{run_dir.name}] SKIP - missing run_metadata.json or adc_stream.bin")
        return False

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    cfg = meta["config"]
    prof = cfg["profile"]
    fs = float(cfg["session"]["frame_rate_hz"])

    cc = ChirpConfig(
        num_adc_samples=prof["num_adc_samples"],
        num_rx=prof["num_rx"],
        num_tx=1,
        num_chirps_per_frame=prof["num_chirps_per_frame"],
        num_frames=0,
        frame_rate_hz=fs,
        range_resolution_m=prof["range_resolution_m"],
        iq_swap=bool(prof["iq_swap"]),
    )
    cube = read_adc_bin(adc_path, cc)[:WARMUP_FRAMES]
    candidate_bins = derive_candidate_bins(cfg)

    selected_bin, _, evidence = run_warmup_selection(cube, candidate_bins, cfg, fs=fs)

    ineligible_hr = [
        (c["bin"], c["settled_energy_db"])
        for c in evidence["candidates"]
        if not c["failed"] and c.get("hr_bonus_vetoed")
    ]

    ok = selected_bin == expected_bin
    status = "PASS" if ok else "FAIL"
    print(
        f"[{run_dir.name}] {status}  adc_stream.bin sha256={_sha256_file(adc_path)[:12]}...  "
        f"expected={expected_bin} observed={selected_bin}  "
        f"confidence={evidence['selected_confidence']}  ({note})"
    )
    if ineligible_hr:
        print(f"    energy-ineligible hr_valid bins vetoed: {ineligible_hr}")
    return ok


def main() -> None:
    results_dir = _ROOT / "results" / "live_demo"
    all_ok = True
    for name, expected_bin, note in SESSIONS:
        ok = _validate_one(results_dir / name, expected_bin, note)
        all_ok = all_ok and ok
    print("\nALL SESSIONS MATCH EXPECTED SELECTION" if all_ok else "\nVALIDATION FAILED — see FAIL rows above")
    raise SystemExit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
