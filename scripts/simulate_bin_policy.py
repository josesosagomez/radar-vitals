"""Simulate bin-selection / relock policies for BR on already-computed per-bin estimates.

Answers, without writing a line of tracker code: **would a 5-bin relock policy have improved
breathing-rate coverage or accuracy on the captures we already have?**

`scripts/diagnose_bin_sweep.py` already produces the production BR estimate for *every* candidate
bin on *every* window. That is exactly the input a tracker would see at runtime, so policies can
be replayed against it offline. If nothing here helps, the stashed tracker implementation
(`git stash@{0}`, commit `0022845`) does not get ported.

Train / test discipline (agreed 2026-08-04)
-------------------------------------------
The eight captures span four subjects (`notes/capture_inventory.md` "Subject map").

* **TRAIN — subjects A + B:** `massimo1`, `massimo2`, `massimo3`, `sweep`. All policy exploration,
  every parameter sweep, and every "try it and see" happens here.
* **TEST — subjects C + D:** `massimo4`, `massimo5`, `massimo6`, `massimo7`. Touched **once**, with
  the policy already frozen.

This is enforced rather than documented: the script runs on TRAIN unless `--split test` is passed
*together with* `--i-have-frozen-the-policy`. Repeatedly evaluating candidate policies on TEST
would fit to the held-out subjects and destroy the only generalisation estimate available.

What the policies may and may not use
-------------------------------------
A policy decides using **radar-side information only** — the per-bin BR estimates, their validity
and confidence flags, and their own history. The Masimo reference is used **solely to score the
outcome afterwards**, never to make a decision (HANDOFF §3.5, CLAUDE.md §4). `P1_oracle` is the
deliberate exception and is labelled a ceiling, not a policy: it picks the best bin per window
using the reference, so no honest selector can beat it.

Usage
-----
    python -X utf8 scripts/simulate_bin_policy.py --sweep-run <bin_sweep dir>
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from src import masimo as masimo_mod  # noqa: E402
from src.comparator import br_reference  # noqa: E402
from src.m4.window_grid import FRAMES_PER_WINDOW, build_window_grid  # noqa: E402
from src.warmup_select import derive_candidate_bins, run_warmup_selection  # noqa: E402

import diagnose_bin_drift as bindrift  # noqa: E402
from diagnose_bin_sweep import decode_frame_range, words_per_frame  # noqa: E402

get_git_commit = bindrift.get_git_commit
is_tree_clean = bindrift.is_tree_clean
validate_decode_geometry = bindrift.validate_decode_geometry


def current_code_lock(capture_dir: Path, cfg: dict) -> int:
    """The bin TODAY's warmup picks, re-derived from window 0.

    Not the same as `run_metadata.json`'s `locked_bin`, and the difference is not cosmetic.
    The 2026-07-13/14 captures (`massimo1`, `massimo2`, `sweep`) were recorded before the M2
    respiration fix, when the BR argmax collapsed onto the 6 bpm band floor; their recorded
    locks were chosen by that buggy code, and `notes/capture_inventory.md` records massimo2's
    and sweep's live locks as outright mislocks. Anchoring the baseline policy on those would
    compare every new policy against a known-bad reference and flatter it.

    So the baseline is "what production would do today", re-derived with the same tested
    `run_warmup_selection` the live path calls.
    """
    meta = json.loads((capture_dir / "run_metadata.json").read_text(encoding="utf-8"))
    chirp = validate_decode_geometry(cfg, meta, capture_dir.name)
    raw = np.memmap(capture_dir / "adc_stream.bin", dtype="<i2", mode="r")
    try:
        cube = decode_frame_range(raw, chirp, 0, FRAMES_PER_WINDOW)
        selected, _dsp, _evidence = run_warmup_selection(
            cube, derive_candidate_bins(cfg), cfg, float(chirp.frame_rate_hz)
        )
    finally:
        del raw
    return int(selected)

#: Subject-level split. Values are capture-directory name suffixes.
TRAIN_CAPTURES = ("massimo1", "massimo2", "massimo3", "sweep")
TEST_CAPTURES = ("massimo4", "massimo5", "massimo6", "massimo7")


def load_reference(capture_dir: Path, n_windows: int) -> dict[int, float]:
    """Masimo RR per window `k`, on the frozen grid. Scoring only — never a policy input."""
    meta = json.loads((capture_dir / "run_metadata.json").read_text(encoding="utf-8"))
    csv_path = [p for p in capture_dir.glob("*.csv") if p.name != "live_estimates.csv"][0]
    df = masimo_mod.load_masimo(csv_path)
    frame0 = datetime.fromisoformat(meta["start_wall_utc"]).timestamp()
    windows = build_window_grid(
        n_windows * FRAMES_PER_WINDOW, frame0, fs=20.0, frames_per_win=FRAMES_PER_WINDOW
    )
    out = {}
    for w in windows:
        rr = br_reference(df, w.epoch_start, w.epoch_end).get("median_rr_bpm", float("nan"))
        if np.isfinite(rr):
            out[w.k] = float(rr)
    return out


# ── Policies ────────────────────────────────────────────────────────────────
#
# Each returns, per window k, the (bin, br_bpm) it would have reported, or None for "no
# estimate this window" — which costs coverage, exactly as it would live.


def p0_static_lock(rows_by_k, locked_bin, **_):
    """Production today: warmup locks once, that bin is used for the whole session."""
    out = {}
    for k, per_bin in rows_by_k.items():
        cell = per_bin.get(locked_bin)
        if cell and cell["br_valid"] and cell["br_bpm"] is not None:
            out[k] = (locked_bin, cell["br_bpm"])
    return out


def p1_oracle(rows_by_k, locked_bin, *, reference=None, **_):
    """CEILING, NOT A POLICY — picks the best bin per window using the reference."""
    out = {}
    for k, per_bin in rows_by_k.items():
        ref = reference.get(k)
        if ref is None:
            continue
        best = min(
            (c for c in per_bin.values() if c["br_valid"] and c["br_bpm"] is not None),
            key=lambda c: abs(c["br_bpm"] - ref), default=None,
        )
        if best:
            out[k] = (best["bin"], best["br_bpm"])
    return out


def _neighbourhood(anchor: int, half_width: int, available: set[int]) -> list[int]:
    return [b for b in range(anchor - half_width, anchor + half_width + 1) if b in available]


def p2_consistency(rows_by_k, locked_bin, *, half_width=2, history=5, tol_bpm=3.0, **_):
    """Keep the warmup anchor; when it looks implausible, read a neighbour instead.

    "Implausible" is judged against this policy's OWN recent accepted output — radar-side,
    no reference. The anchor never moves, so a genuine posture shift is tracked only as a
    permanent read-from-neighbour, which is why P3 exists.
    """
    out, recent = {}, []
    available = {b for per_bin in rows_by_k.values() for b in per_bin}
    for k in sorted(rows_by_k):
        per_bin = rows_by_k[k]
        anchor = per_bin.get(locked_bin)
        anchor_ok = bool(anchor and anchor["br_valid"] and anchor["br_bpm"] is not None)
        ref_hist = statistics.median(recent) if recent else None

        chosen = None
        if anchor_ok and (ref_hist is None or abs(anchor["br_bpm"] - ref_hist) <= tol_bpm):
            chosen = (locked_bin, anchor["br_bpm"])
        elif ref_hist is not None:
            cands = [
                per_bin[b] for b in _neighbourhood(locked_bin, half_width, available)
                if b in per_bin and per_bin[b]["br_valid"] and per_bin[b]["br_bpm"] is not None
            ]
            if cands:
                best = min(cands, key=lambda c: abs(c["br_bpm"] - ref_hist))
                if abs(best["br_bpm"] - ref_hist) <= tol_bpm:
                    chosen = (best["bin"], best["br_bpm"])
        elif anchor_ok:
            chosen = (locked_bin, anchor["br_bpm"])

        if chosen:
            out[k] = chosen
            recent.append(chosen[1])
            recent[:] = recent[-history:]
    return out


def p3_relock(rows_by_k, locked_bin, *, half_width=2, history=5, tol_bpm=3.0, dwell=3, **_):
    """As P2, but a neighbour that wins `dwell` windows in a row becomes the new anchor.

    Hysteresis is the point. The bin-drift diagnostic measured frequent short (<2 s) argmax
    flicker but almost no sustained (>=5 s) drift, so a policy that relocks on a single bad
    window would chase noise all session.
    """
    out, recent = {}, []
    anchor = locked_bin
    streak_bin, streak_len, n_relocks = None, 0, 0
    available = {b for per_bin in rows_by_k.values() for b in per_bin}

    for k in sorted(rows_by_k):
        per_bin = rows_by_k[k]
        cur = per_bin.get(anchor)
        cur_ok = bool(cur and cur["br_valid"] and cur["br_bpm"] is not None)
        ref_hist = statistics.median(recent) if recent else None

        chosen = None
        if cur_ok and (ref_hist is None or abs(cur["br_bpm"] - ref_hist) <= tol_bpm):
            chosen = (anchor, cur["br_bpm"])
            streak_bin, streak_len = None, 0
        elif ref_hist is not None:
            cands = [
                per_bin[b] for b in _neighbourhood(anchor, half_width, available)
                if b in per_bin and per_bin[b]["br_valid"] and per_bin[b]["br_bpm"] is not None
            ]
            if cands:
                best = min(cands, key=lambda c: abs(c["br_bpm"] - ref_hist))
                if abs(best["br_bpm"] - ref_hist) <= tol_bpm:
                    chosen = (best["bin"], best["br_bpm"])
                    if best["bin"] == streak_bin:
                        streak_len += 1
                    else:
                        streak_bin, streak_len = best["bin"], 1
                    if streak_bin != anchor and streak_len >= dwell:
                        anchor = streak_bin
                        n_relocks += 1
                        streak_bin, streak_len = None, 0
        elif cur_ok:
            chosen = (anchor, cur["br_bpm"])

        if chosen:
            out[k] = chosen
            recent.append(chosen[1])
            recent[:] = recent[-history:]
    out["_n_relocks"] = n_relocks          # sentinel, stripped by the scorer
    return out


POLICIES = {
    "P0_static_lock": p0_static_lock,
    "P2_consistency": p2_consistency,
    "P3_relock": p3_relock,
    "P1_oracle_CEILING": p1_oracle,
}


# ── Scoring ─────────────────────────────────────────────────────────────────


def score(result: dict, reference: dict[int, float], n_windows: int) -> dict:
    n_relocks = result.pop("_n_relocks", 0) if isinstance(result, dict) else 0
    errs, hits2, hits3 = [], [], []
    for k, (_bin, br) in result.items():
        ref = reference.get(k)
        if ref is None:
            continue
        e = abs(br - ref)
        errs.append(e)
        hits2.append(e <= 2.0)
        hits3.append(e <= 3.0)
    scored = len(errs)
    return {
        "n_windows": n_windows,
        "n_reported": len(result),
        "coverage": round(len(result) / n_windows, 4) if n_windows else None,
        "n_scored": scored,
        "mae_bpm": round(float(np.mean(errs)), 3) if errs else None,
        "hit_2bpm": round(float(np.mean(hits2)), 4) if hits2 else None,
        "hit_3bpm": round(float(np.mean(hits3)), 4) if hits3 else None,
        "n_relocks": n_relocks,
    }


def load_sweep(sweep_csv: Path) -> dict[str, dict[int, dict[int, dict]]]:
    """{capture: {k: {bin: cell}}} from a diagnose_bin_sweep windows.csv."""
    out: dict[str, dict[int, dict[int, dict]]] = {}
    for r in csv.DictReader(sweep_csv.open(encoding="utf-8")):
        cell = {
            "bin": int(r["bin"]),
            "br_bpm": float(r["br_bpm"]) if r["br_bpm"] else None,
            "br_valid": r["br_valid"] == "1",
            "br_confidence": r["br_confidence"],
        }
        out.setdefault(r["capture_id"], {}).setdefault(int(r["k"]), {})[cell["bin"]] = cell
    return out


def _write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    """CSV via the stdlib writer, LF-pinned.

    Hand-rolled quoting was wrong here: `params` holds a JSON object containing BOTH commas
    and double quotes, and wrapping it in quotes without doubling the inner ones produced a
    file that `csv.DictReader` silently mis-parsed into the wrong columns — a corrupted table
    that still looked plausible.
    """
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(columns)
        for r in rows:
            writer.writerow(["" if r.get(c) is None else r[c] for c in columns])


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sweep-run", required=True, type=Path,
                    help="a results/diagnose/bin_sweep/<timestamp>/ directory")
    ap.add_argument("--split", choices=("train", "test"), default="train")
    ap.add_argument("--i-have-frozen-the-policy", action="store_true",
                    help="required with --split test; see the module docstring")
    ap.add_argument("--only-policy", default=None,
                    help="evaluate a single policy (required with --split test)")
    ap.add_argument("--only-params", default=None,
                    help="JSON params for --only-policy (required with --split test)")
    ap.add_argument("--out", default=REPO_ROOT / "results" / "diagnose" / "bin_policy", type=Path)
    args = ap.parse_args(argv)

    if args.split == "test":
        if not args.i_have_frozen_the_policy:
            ap.error(
                "--split test requires --i-have-frozen-the-policy. The held-out subjects are "
                "scored ONCE, with the policy already chosen on train. Re-running candidate "
                "policies against them fits to the holdout and destroys the only generalisation "
                "estimate available."
            )
        if not (args.only_policy and args.only_params):
            ap.error(
                "--split test also requires --only-policy and --only-params. Evaluating the whole "
                "grid against the holdout would write 38 test numbers to disk, and the frozen "
                "choice could then be revised to whichever won. Restricting the run to the single "
                "frozen combination makes that structurally impossible, not merely discouraged."
            )

    wanted = TRAIN_CAPTURES if args.split == "train" else TEST_CAPTURES
    sweep = load_sweep(Path(args.sweep_run) / "windows.csv")
    captures = {cid: v for cid, v in sweep.items() if cid.rsplit("_", 1)[-1] in wanted}
    missing = set(wanted) - {c.rsplit("_", 1)[-1] for c in captures}
    if missing:
        raise SystemExit(f"sweep run is missing {sorted(missing)}; re-run diagnose_bin_sweep.py")

    live_cfg = yaml.safe_load((REPO_ROOT / "scripts" / "live_demo_config.yaml").read_text(encoding="utf-8"))
    print("Re-deriving each capture's warmup lock with current code ...", flush=True)
    grid = {
        "half_width": [1, 2],
        "history": [3, 5],
        "tol_bpm": [2.0, 3.0, 5.0],
        "dwell": [2, 3],
    }

    rows = []
    lock_note = {}
    for cid, rows_by_k in sorted(captures.items()):
        capture_dir = REPO_ROOT / "results" / "live_demo" / cid
        meta = json.loads((capture_dir / "run_metadata.json").read_text(encoding="utf-8"))
        recorded = int(meta["locked_bin"])
        locked_bin = current_code_lock(capture_dir, live_cfg)
        lock_note[cid.rsplit("_", 1)[-1]] = {
            "recorded_live_lock": recorded,
            "current_code_lock": locked_bin,
            "differs": recorded != locked_bin,
        }
        n_windows = len(rows_by_k)
        reference = load_reference(capture_dir, n_windows)

        selected = POLICIES if not args.only_policy else {
            k: v for k, v in POLICIES.items()
            if k in (args.only_policy, "P0_static_lock", "P1_oracle_CEILING")
        }
        for name, fn in selected.items():
            if name in ("P0_static_lock", "P1_oracle_CEILING"):
                combos = [{}]
            elif name == "P2_consistency":
                combos = [dict(zip(("half_width", "history", "tol_bpm"), c))
                          for c in itertools.product(grid["half_width"], grid["history"], grid["tol_bpm"])]
            else:
                combos = [dict(zip(("half_width", "history", "tol_bpm", "dwell"), c))
                          for c in itertools.product(grid["half_width"], grid["history"],
                                                     grid["tol_bpm"], grid["dwell"])]
            if args.only_params and name == args.only_policy:
                combos = [json.loads(args.only_params)]
            for params in combos:
                res = fn(rows_by_k, locked_bin, reference=reference, **params)
                rows.append({
                    "capture": cid.rsplit("_", 1)[-1], "policy": name,
                    "params": json.dumps(params, sort_keys=True) if params else "",
                    "locked_bin": locked_bin,
                    **score(res, reference, n_windows),
                })

    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    cols = ["capture", "policy", "params", "locked_bin", "n_windows", "n_reported", "coverage",
            "n_scored", "mae_bpm", "hit_2bpm", "hit_3bpm", "n_relocks"]
    _write_csv(out_dir / "per_capture.csv", cols, rows)

    # Pooled across the split's captures, per (policy, params).
    pooled: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        pooled.setdefault((r["policy"], r["params"]), []).append(r)
    summary = []
    for (policy, params), rs in pooled.items():
        cov = [r["coverage"] for r in rs if r["coverage"] is not None]
        mae = [r["mae_bpm"] for r in rs if r["mae_bpm"] is not None]
        h3 = [r["hit_3bpm"] for r in rs if r["hit_3bpm"] is not None]
        summary.append({
            "policy": policy, "params": params,
            "mean_coverage": round(float(np.mean(cov)), 4) if cov else None,
            "mean_mae_bpm": round(float(np.mean(mae)), 3) if mae else None,
            "mean_hit_3bpm": round(float(np.mean(h3)), 4) if h3 else None,
            "total_relocks": sum(r["n_relocks"] for r in rs),
        })
    summary.sort(key=lambda s: (-(s["mean_hit_3bpm"] or 0), s["mean_mae_bpm"] or 9e9))

    scols = ["policy", "params", "mean_coverage", "mean_mae_bpm", "mean_hit_3bpm", "total_relocks"]
    _write_csv(out_dir / "summary.csv", scols, summary)

    (out_dir / "run_meta.json").write_text(json.dumps({
        "generated_utc": datetime.utcnow().isoformat() + "Z",
        "script": "scripts/simulate_bin_policy.py",
        "git_commit": get_git_commit(), "git_tree_clean": is_tree_clean(),
        "sweep_run": str(args.sweep_run),
        "split": args.split, "captures": sorted(c.rsplit("_", 1)[-1] for c in captures),
        "grid": grid, "n_combinations_evaluated": len(pooled),
        "frozen_policy": args.only_policy, "frozen_params": args.only_params,
        "reference_used_for": "scoring only; never a policy input (P1_oracle is a labelled ceiling)",
        "run_config_hash_source": "scripts/live_demo_config.yaml",
        "band_hz": live_cfg["respiration"]["band_hz"],
        "anchor_bin_source": (
            "re-derived with current code via run_warmup_selection on window 0, NOT the "
            "run_metadata locked_bin — the 2026-07-13/14 captures were recorded pre-M2-fix "
            "and their recorded locks came from code carrying the respiration-collapse bug"
        ),
        "locks": lock_note,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

    print("\nAnchor bin per capture (current code vs what was recorded live):")
    for cap, v in sorted(lock_note.items()):
        flag = "   <-- DIFFERS" if v["differs"] else ""
        print(f"  {cap:<9} current={v['current_code_lock']:<3} "
              f"recorded_live={v['recorded_live_lock']:<3}{flag}")
    print(f"\nSplit: {args.split}  captures: {sorted(c.rsplit('_', 1)[-1] for c in captures)}")
    print(f"{len(pooled)} policy/parameter combinations evaluated\n")
    print(f"{'policy':<20} {'params':<58} {'cov':>6} {'MAE':>7} {'hit3':>6} {'relock':>7}")
    for s in summary[:14]:
        print(f"{s['policy']:<20} {s['params'][:56]:<58} "
              f"{(s['mean_coverage'] or 0):>6.0%} {(s['mean_mae_bpm'] or 0):>7.2f} "
              f"{(s['mean_hit_3bpm'] or 0):>6.0%} {s['total_relocks']:>7}")
    print(f"\nWrote {out_dir}")


if __name__ == "__main__":
    main()
