#!/usr/bin/env python3
"""Characterise the M2 respiration floor-pin from checkpointed live-replay NPZs.

Reference-of-record for the counts in plans/m2_respiration_fix.md §1 (M2R-01): for each
NPZ it reports, per floor-pinned window (br <= PIN_BPM_MAX), the selection class
(Class A = both-branch edge selection; Class B = HA-only edge selection), whether the
6 bpm bin is a strict local maximum under the M2 plateau policy, and — for Class-B
windows — the harmonic-support ratios that decide whether 3rd-harmonic inheritance is
attributable. Deterministic, read-only over `results/`; no RNG. Records the SHA-256 of
every NPZ consumed, the git revision, and the analysis constants (CLAUDE.md §3).

Post-fix NPZs additionally get the §4.4 invariant check: no window may be floor-pinned
AND resp_valid (pre-fix NPZs lack the `resp_valid` field; validity is then inferred
from `f_r_hz` finiteness, which live_demo only sets for resp_valid windows).

Run from repo root:
    python scripts/diagnose_respiration_collapse.py results/live_demo/<run>/live_intermediates.npz [...]
    python scripts/diagnose_respiration_collapse.py --json out.json <npz> [...]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]

# Analysis constants (mirror the frozen band and the plan's pin definition).
BAND_HZ     = (0.10, 0.50)
PIN_BPM_MAX = 6.5          # "floor-pinned": br <= band floor + quarter bin


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_rev() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def _is_local_max(spec: np.ndarray, idx: int) -> bool:
    """M2 plateau policy — duplicated from src.respiration so this diagnostic stays
    runnable against historical NPZs even if the estimator moves on."""
    if idx <= 0 or idx >= len(spec) - 1:
        return False
    return bool(spec[idx] > spec[idx - 1] and spec[idx] >= spec[idx + 1])


def diagnose_npz(npz_path: Path) -> dict:
    z = np.load(npz_path, allow_pickle=True)
    br   = np.atleast_1d(z["br_bpm"]).astype(float)
    fft  = np.atleast_1d(z["fft_rr_bpm"]).astype(float)
    ha   = np.atleast_1d(z["ha_rr_bpm"]).astype(float)
    stft = np.atleast_1d(z["stft_rr_bpm"]).astype(float)
    freqs = np.atleast_1d(z["resp_freqs_hz"])
    freqs0 = freqs[0] if freqs.ndim == 2 else freqs
    spec = np.atleast_1d(z["resp_spectrum"])

    if "resp_valid" in z.files:
        valid = np.atleast_1d(z["resp_valid"]).astype(bool)
        valid_source = "resp_valid"
    else:  # pre-fix NPZ: f_r_hz is finite only when the live path deemed resp valid
        valid = np.isfinite(np.atleast_1d(z["f_r_hz"]).astype(float))
        valid_source = "f_r_hz (inferred; pre-fix NPZ)"

    edge_bin = int(np.argmin(np.abs(freqs0 - BAND_HZ[0])))
    band = (freqs0 >= BAND_HZ[0]) & (freqs0 <= BAND_HZ[1])

    def _bin(s: np.ndarray, f_hz: float) -> float:
        return float(s[int(np.argmin(np.abs(freqs0 - f_hz)))])

    pinned = np.where(np.isfinite(br) & (br <= PIN_BPM_MAX))[0]
    windows = []
    n_class_a = n_class_b = n_local_max = n_pinned_valid = 0
    for i in pinned:
        fft_pinned = bool(np.isfinite(fft[i]) and fft[i] <= PIN_BPM_MAX)
        cls = "A_both_branch" if fft_pinned else "B_ha_only"
        n_class_a += cls == "A_both_branch"
        n_class_b += cls == "B_ha_only"
        lm = _is_local_max(spec[i], edge_bin)
        n_local_max += lm
        n_pinned_valid += bool(valid[i])
        row = {
            "window": int(i),
            "class": cls,
            "fft_rr_bpm": round(float(fft[i]), 2),
            "ha_rr_bpm": round(float(ha[i]), 2),
            "stft_rr_bpm": round(float(stft[i]), 2),
            "br_bpm": round(float(br[i]), 2),
            "resp_valid": bool(valid[i]),
            "edge_bin_is_local_max": bool(lm),
        }
        if cls == "B_ha_only":
            floor = float(np.median(spec[i][band]))
            row["harmonic_support"] = {
                "band_floor": round(floor, 3),
                "spec_6bpm_over_floor":  round(_bin(spec[i], 0.10) / floor, 2),
                "spec_18bpm_over_floor": round(_bin(spec[i], 0.30) / floor, 2),
            }
        windows.append(row)

    return {
        "npz": str(npz_path),
        "sha256": _sha256(npz_path),
        "n_windows": int(len(br)),
        "validity_source": valid_source,
        "n_floor_pinned": int(len(pinned)),
        "n_pinned_and_valid": int(n_pinned_valid),
        "n_class_a_both_branch": int(n_class_a),
        "n_class_b_ha_only": int(n_class_b),
        "n_pinned_edge_bin_local_max": int(n_local_max),
        "invariant_no_valid_floor_pin": bool(n_pinned_valid == 0),
        "windows": windows,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("npz", nargs="+", type=Path, help="live_intermediates.npz path(s)")
    ap.add_argument("--json", type=Path, default=None, help="also write full JSON report")
    args = ap.parse_args(argv)

    report = {
        "git_revision": _git_rev(),
        "band_hz": list(BAND_HZ),
        "pin_bpm_max": PIN_BPM_MAX,
        "sessions": [diagnose_npz(p) for p in args.npz],
    }

    for s in report["sessions"]:
        print(f"== {s['npz']}")
        print(f"   sha256={s['sha256'][:16]}…  windows={s['n_windows']}  "
              f"validity from: {s['validity_source']}")
        print(f"   floor-pinned={s['n_floor_pinned']}  of-those-valid={s['n_pinned_and_valid']}"
              f"  (invariant holds: {s['invariant_no_valid_floor_pin']})")
        print(f"   Class A (both-branch)={s['n_class_a_both_branch']}  "
              f"Class B (HA-only)={s['n_class_b_ha_only']}  "
              f"edge-bin strict local max in {s['n_pinned_edge_bin_local_max']} pinned windows")
        for w in s["windows"]:
            hs = w.get("harmonic_support")
            hs_txt = (f"  18bpm/floor={hs['spec_18bpm_over_floor']}x "
                      f"6bpm/floor={hs['spec_6bpm_over_floor']}x" if hs else "")
            print(f"     win {w['window']:3d} [{w['class']}] fft={w['fft_rr_bpm']:6.2f} "
                  f"ha={w['ha_rr_bpm']:6.2f} stft={w['stft_rr_bpm']:6.2f} "
                  f"valid={int(w['resp_valid'])} local_max={int(w['edge_bin_is_local_max'])}"
                  f"{hs_txt}")
        print()
    print(f"git revision: {report['git_revision']}")

    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"JSON report written to {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
