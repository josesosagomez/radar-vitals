"""Quantify what the LFR-01/LFR-02 band-pass fix changed on the existing captures.

Cross-model review 2026-07-26 (`plans/m4_linalg_free_dsp_review.md`). `bandpass_filter` was
restored from a brick-wall FFT mask to the documented zero-phase Butterworth response with an
odd-reflected edge policy. That moves every HR/BR number the pipeline produces, so this script
measures the move rather than asserting it is small.

Pairs each post-fix replay with the pre-fix (2026-07-25, post-M2) replay of the SAME source
capture, joins their `live_estimates.csv` on `frame_idx`, and reports the deltas — both over
every 3 s hop and over the frozen non-overlapping scoring grid of `notes/analysis_prespec.md`
§7 (`frame_idx = 600*k + 599`).

WHAT THIS IS NOT
----------------
Not a comparator run. No Masimo scoring happens here: the frozen HR/BR comparators
(`notes/comparator_prespec*.md`) are M4's job, M4 does not exist yet, and alignment on these
captures is approximate anyway (no persisted `frame0_epoch`). This measures how far the RADAR's
own output moved — which is exactly the question the fix raises and is answerable exactly.

Usage:  conda run -n radar-vitals python scripts/compare_filter_fix_impact.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
LIVE_DEMO = REPO / "results" / "live_demo"
# The pre-fix reference generation: post-M2-fix replays of 2026-07-25 (HANDOFF §2).
PRE_FIX_PREFIX = "20260725_"
SCORING_HOP_MODULUS = 600      # frozen §7 grid: one window per 600 frames
SCORING_HOP_OFFSET = 599


def _sha256(path: Path, limit: int | None = None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read() if limit is None else fh.read(limit))
    return h.hexdigest()


def _load_runs() -> list[dict]:
    runs = []
    for d in sorted(LIVE_DEMO.glob("*_replay_unknown")):
        meta_path = d / "run_metadata.json"
        csv_path = d / "live_estimates.csv"
        if not (meta_path.exists() and csv_path.exists()):
            continue
        meta = json.loads(meta_path.read_text())
        src = (meta.get("replay_files") or [None])[0]
        if src is None:
            continue
        runs.append({
            "dir": d,
            "name": d.name,
            "source": Path(src).parent.name,
            "locked_bin": meta.get("locked_bin"),
            "warmup_bin": meta.get("warmup_selected_bin"),
            "warmup_conf": meta.get("warmup_selection_confidence"),
            "git_commit": (meta.get("git_commit") or "")[:8],
            "is_pre_fix": d.name.startswith(PRE_FIX_PREFIX),
            "csv": csv_path,
        })
    return runs


def _delta_stats(old: pd.Series, new: pd.Series, tol: float = 1e-9) -> dict:
    """Compare two aligned value columns that may contain NaN (= no estimate)."""
    o_fin, n_fin = old.notna().to_numpy(), new.notna().to_numpy()
    both = o_fin & n_fin
    d = (new[both].to_numpy() - old[both].to_numpy())
    changed = int(np.sum(np.abs(d) > tol))
    return {
        "n": int(len(old)),
        "old_valid": int(o_fin.sum()),
        "new_valid": int(n_fin.sum()),
        "gained": int(np.sum(~o_fin & n_fin)),   # NaN before, a value now
        "lost": int(np.sum(o_fin & ~n_fin)),     # a value before, NaN now
        "both": int(both.sum()),
        "changed": changed,
        "median_abs": float(np.median(np.abs(d))) if len(d) else float("nan"),
        "p95_abs": float(np.percentile(np.abs(d), 95)) if len(d) else float("nan"),
        "max_abs": float(np.max(np.abs(d))) if len(d) else float("nan"),
        "mean_signed": float(np.mean(d)) if len(d) else float("nan"),
    }


def _fmt(label: str, s: dict, unit: str = "bpm") -> str:
    return (
        f"    {label:<18} valid {s['old_valid']:>3} -> {s['new_valid']:>3} "
        f"(+{s['gained']}/-{s['lost']})   comparable {s['both']:>3}, "
        f"changed {s['changed']:>3}\n"
        f"    {'':<18} |delta|  median {s['median_abs']:7.3f}  p95 {s['p95_abs']:7.3f}  "
        f"max {s['max_abs']:8.3f} {unit}   (signed mean {s['mean_signed']:+.3f})"
    )


def main() -> int:
    runs = _load_runs()
    if not runs:
        print(f"No replay artifacts found under {LIVE_DEMO}")
        return 1

    by_source: dict[str, dict[str, list]] = {}
    for r in runs:
        slot = by_source.setdefault(r["source"], {"pre": [], "post": []})
        slot["pre" if r["is_pre_fix"] else "post"].append(r)

    print("=" * 100)
    print("LFR-01/LFR-02 band-pass fix — impact on the existing captures")
    print("brick-wall FFT mask  ->  zero-phase Butterworth (order 4) + odd-reflected edges")
    print("=" * 100)

    compared = 0
    for source in sorted(by_source):
        pre_runs, post_runs = by_source[source]["pre"], by_source[source]["post"]
        if not pre_runs or not post_runs:
            print(f"\n### {source}\n    SKIPPED — "
                  f"{len(pre_runs)} pre-fix, {len(post_runs)} post-fix replay(s); need both.")
            continue

        # If several pre-fix replays exist for one capture (e.g. a forced-bin variant), prefer
        # the one whose locked bin matches the post-fix run, so the comparison is like-for-like.
        post = sorted(post_runs, key=lambda r: r["name"])[-1]
        same_bin = [r for r in pre_runs if r["locked_bin"] == post["locked_bin"]]
        pre = (sorted(same_bin, key=lambda r: r["name"])[-1]
               if same_bin else sorted(pre_runs, key=lambda r: r["name"])[-1])

        old = pd.read_csv(pre["csv"])
        new = pd.read_csv(post["csv"])
        merged = old.merge(new, on="frame_idx", suffixes=("_old", "_new"))

        print(f"\n### {source}")
        print(f"    pre-fix  : {pre['name']}  (bin {pre['locked_bin']}, "
              f"warmup {pre['warmup_bin']}/{pre['warmup_conf']}, commit {pre['git_commit']})")
        print(f"    post-fix : {post['name']}  (bin {post['locked_bin']}, "
              f"warmup {post['warmup_bin']}/{post['warmup_conf']}, commit {post['git_commit']})")
        if pre["locked_bin"] != post["locked_bin"]:
            print(f"    *** WARMUP BIN LOCK MOVED: {pre['locked_bin']} -> {post['locked_bin']} — "
                  f"the two runs are NOT on the same range bin, so per-hop deltas below mix a "
                  f"bin change with the filter change. ***")
        if len(merged) != len(old) or len(merged) != len(new):
            print(f"    note: hop counts differ (old {len(old)}, new {len(new)}, "
                  f"joined {len(merged)}) — comparing the {len(merged)} shared frame_idx values.")

        for scope, sub in (
            ("ALL HOPS (3 s)", merged),
            ("FROZEN §7 GRID (non-overlapping 30 s)",
             merged[merged["frame_idx"] % SCORING_HOP_MODULUS == SCORING_HOP_OFFSET]),
        ):
            if sub.empty:
                continue
            print(f"\n  {scope}  — {len(sub)} windows")
            hr_old = sub["hr_bpm_raw_old"].where(sub["ahet_verified_old"] == 1)
            hr_new = sub["hr_bpm_raw_new"].where(sub["ahet_verified_new"] == 1)
            br_old = sub["br_bpm_old"].where(sub["resp_valid_old"] == 1)
            br_new = sub["br_bpm_new"].where(sub["resp_valid_new"] == 1)
            print(_fmt("HR (AHET-verified)", _delta_stats(hr_old, hr_new)))
            print(_fmt("BR (resp_valid)", _delta_stats(br_old, br_new)))

            a_old, a_new = int((sub["ahet_verified_old"] == 1).sum()), \
                int((sub["ahet_verified_new"] == 1).sum())
            r_old, r_new = int((sub["resp_valid_old"] == 1).sum()), \
                int((sub["resp_valid_new"] == 1).sum())
            n = len(sub)
            print(f"    coverage           HR {a_old:>3}/{n} ({100*a_old/n:5.1f}%) -> "
                  f"{a_new:>3}/{n} ({100*a_new/n:5.1f}%)    "
                  f"BR {r_old:>3}/{n} ({100*r_old/n:5.1f}%) -> "
                  f"{r_new:>3}/{n} ({100*r_new/n:5.1f}%)")

        # M2 invariant: a first-in-band-bin selection may never be resp_valid (HANDOFF §4).
        floor_pinned = merged[(merged["resp_valid_new"] == 1)
                              & (np.abs(merged["br_bpm_new"] - 6.0) < 1e-6)]
        print(f"\n    M2 invariant check: floor-pinned-and-valid windows (br=6.0 & resp_valid) "
              f"= {len(floor_pinned)}  (must be 0)")
        compared += 1

    print("\n" + "=" * 100)
    print(f"Compared {compared} capture(s).")
    print("Reproduce: conda run -n radar-vitals python scripts/compare_filter_fix_impact.py")
    print("=" * 100)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
