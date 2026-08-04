"""Freeze the BR bin-selection rule, then score it ONCE on held-out subjects.

The rule is `src/br_bin_search.py:BR_BIN_RULE_V1` — **medoid consensus with always-emit**:
among the `br_valid` bins of a window, report the one whose `br_bpm` is closest to the median
of `br_bpm` over those same valid bins; tie-break by higher energy, then lower bin index; emit
whenever at least one bin is valid.

It has **no fitted parameters**, and that is the entire argument for it. A searched
nine-feature rule was built and measured on 2026-08-04 (`scripts/br_bin_preflight.py`) and lost
to this out of fold — 2.384 vs 2.291 bpm — with its winning sign vector disagreeing across all
four leave-one-subject-out folds. With nothing fitted there is no selection optimism, so the
training-subject score IS an estimate of future performance rather than an upper bound on one.

Modes
-----
``--mode freeze``
    Score the rule and its baselines on the eight training captures (subjects A-D), derive
    prediction intervals from the per-subject spread, and write `frozen_rule.json`.

``--mode offset-scan``
    Requires ``--frozen-rule``. Re-scores the FROZEN rule under the fixed 3-point label-origin
    grid {0, +7.5, +15} s. Runs once, after the freeze, on the frozen rule only — it can
    neither enumerate candidates nor change what is frozen.

``--mode test``
    Requires ``--frozen-rule`` AND ``--i-have-frozen-the-rule`` AND ``--subject-map``. Scores
    the frozen rule once on new captures. There is no way to evaluate an alternative rule here;
    that is deliberate, and it is the guard the previous bin-policy study only had by
    convention until it was added in code (`scripts/simulate_bin_policy.py:315-330`).

Why the criteria are paired deltas and intervals, never absolute thresholds
--------------------------------------------------------------------------
Per-subject oracle MAE ranges 0.26-1.35 bpm across subjects A-D, so subject difficulty
dominates any absolute number: on a hard subject even a perfect selector gives ~1.3 bpm and
any real rule gives ~2.5-3. A criterion like "MAE < 2.5 on the holdout" would therefore be
measuring which subjects were recruited, not whether the rule works. The previous policy was
judged against a point prediction ("68 % coverage") and read as a failure at 46 %; an interval
honest about n=4 would not have been surprised.

Usage
-----
    python -X utf8 scripts/br_bin_rule.py --mode freeze
    python -X utf8 scripts/br_bin_rule.py --mode offset-scan --frozen-rule <path>
    python -X utf8 scripts/br_bin_rule.py --mode test --frozen-rule <path> \
        --i-have-frozen-the-rule --sweep-run <dir> --presence-run <dir> \
        --subject-map massimo8=E massimo9=F massimo10=G
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from src import br_bin_search as bbs                                    # noqa: E402
from src import br_features as brf                                      # noqa: E402

import diagnose_bin_drift as bindrift                                   # noqa: E402
from br_bin_preflight import (                                          # noqa: E402
    build_table, paired_subject_delta, sha256, write_csv,
)

#: Label-origin sensitivity grid, in seconds added to `frame0_epoch`. Fixed at three points and
#: run only after the freeze, so it cannot become another degree of freedom. The eight training
#: captures have no persisted frame-0 epoch; their origin is reconstructed from `start_wall_utc`,
#: which PRECEDES true frame 0 by 5-15 s (`HANDOFF.md` §4.3). Offset 0 is the primary because it
#: is the only value reconstructible from what was recorded. Captures with a true
#: `frame0_epoch_utc` are unaffected and the scan is meaningless for them.
OFFSET_GRID_S = (0.0, 7.5, 15.0)

DEFAULT_SEED = 20260804


def _rules(ws: bbs.WindowSet, lock_of: dict[str, int], seed: int) -> dict:
    """The frozen rule plus every baseline it must be reported against."""
    sel_p0 = np.full(ws.n_windows, -1, dtype=int)
    for cid, lock in lock_of.items():
        m = ws.capture_id == cid
        if m.any():
            sel_p0[m] = bbs.rule_fixed_bin(ws, lock)[m]
    return {
        "FROZEN_medoid_always_emit": bbs.rule_from_spec(ws),
        "P0_static_lock_rederived": sel_p0,
        "null_random_valid_bin": bbs.rule_random(ws, np.random.default_rng(seed)),
        "oracle_CEILING": bbs.rule_oracle(ws),
    }


def _evidence(ws: bbs.WindowSet, lock_of: dict[str, int], seed: int) -> dict:
    """Per-subject and pooled scores for the rule and its baselines, plus the paired delta."""
    sels = _rules(ws, lock_of, seed)
    scores = {name: bbs.per_subject_scores(ws, sel) for name, sel in sels.items()}

    delta = paired_subject_delta(
        ws, sels["P0_static_lock_rederived"], sels["FROZEN_medoid_always_emit"]
    )
    skill = {
        name: bbs.oracle_normalised_skill(
            s["pooled"]["mae_bpm"],
            scores["null_random_valid_bin"]["pooled"]["mae_bpm"],
            scores["oracle_CEILING"]["pooled"]["mae_bpm"],
        )
        for name, s in scores.items()
    }
    # Structural, not empirical: the medoid emits whenever ANY bin is valid, P0 only when the
    # locked bin is. P0's emitting set is therefore a subset of the rule's, always.
    cov_rule = scores["FROZEN_medoid_always_emit"]["pooled"]["coverage"]
    cov_p0 = scores["P0_static_lock_rederived"]["pooled"]["coverage"]
    return {
        "scores": scores,
        "paired_delta_P0_minus_rule": delta,
        "oracle_normalised_skill": skill,
        "coverage_rule": cov_rule,
        "coverage_P0": cov_p0,
        "coverage_dominance_holds": bool(cov_rule >= cov_p0),
    }


def _intervals(evidence: dict) -> dict:
    """Prediction intervals from the per-subject spread — the freeze's actual predictions."""
    by_subj = evidence["scores"]["FROZEN_medoid_always_emit"]["by_subject"]
    maes = [v["mae_bpm"] for v in by_subj.values() if v["mae_bpm"] is not None]
    deltas = [r["delta_a_minus_b"] for r in evidence["paired_delta_P0_minus_rule"]["per_subject"]]
    return {
        "mae_one_new_subject": bbs.prediction_interval(maes, 1, floor_at_zero=True),
        "mae_mean_of_three_new_subjects": bbs.prediction_interval(maes, 3, floor_at_zero=True),
        "paired_delta_one_new_subject": bbs.prediction_interval(deltas, 1),
        "paired_delta_mean_of_three_new_subjects": bbs.prediction_interval(deltas, 3),
    }


def _criteria(intervals: dict) -> dict:
    """What would count as the rule working, written down before any new capture exists."""
    return {
        "primary": (
            "mean paired delta MAE(P0_rederived) - MAE(rule) over the new subjects is > 0, "
            "computed on the windows where BOTH rules report and the reference is admissible"
        ),
        "coverage": (
            "coverage(rule) >= coverage(P0) on the same windows. STRUCTURAL, not predicted: "
            "the rule emits whenever any bin is br_valid and P0 only when the locked bin is, "
            "so P0's emitting set is a subset. A violation means a bug, not a bad result."
        ),
        "failure": (
            "mean paired delta <= 0, OR the rule is worse than P0 in >= 2 of 3 new subjects"
        ),
        "predicted_mae_one_new_subject_95pi": intervals["mae_one_new_subject"],
        "predicted_mae_mean_of_three_95pi": intervals["mae_mean_of_three_new_subjects"],
        "predicted_paired_delta_mean_of_three_95pi":
            intervals["paired_delta_mean_of_three_new_subjects"],
        "interpretation": (
            "The MAE intervals are very wide and the paired-delta interval spans zero. That is "
            "the finding at four subjects, not a defect: the rule is expected to beat production "
            "on average, and a single new subject on which it does not would NOT falsify it. "
            "Only the pooled paired delta and the >=2-of-3 rule above are falsifying."
        ),
        "not_a_criterion": (
            "No absolute MAE threshold. Per-subject oracle MAE spans 0.26-1.35 bpm across A-D, "
            "so an absolute bar measures which subjects were recruited, not the rule."
        ),
    }


def _provenance(args, extra: dict | None = None) -> dict:
    p = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": bindrift.get_git_commit(),
        "git_tree_clean": bindrift.is_tree_clean(),
        "sweep_run": str(args.sweep_run),
        "presence_run": str(args.presence_run),
        "input_hashes": {
            "bin_sweep_windows_csv": sha256(Path(args.sweep_run) / "windows.csv"),
            "signal_presence_windows_csv": sha256(Path(args.presence_run) / "windows.csv"),
        },
        "gate": args.gate,
        "seed": args.seed,
        "window_grid": {
            "frames_per_window": 600, "fs_hz": 20.0, "k0_excluded": True,
            "authority": "notes/analysis_prespec.md §7",
        },
        "reference_origin_caveat": (
            "Training captures have no persisted frame-0 epoch; alignment is reconstructed from "
            "run_metadata.json:start_wall_utc and is APPROXIMATE (notes/analysis_prespec.md §7). "
            "Never a frozen scoring number. See offset_scan for the sensitivity."
        ),
    }
    p.update(extra or {})
    return p


# ── modes ────────────────────────────────────────────────────────────────────


def do_freeze(args, out_dir: Path) -> dict:
    table, census, locks = build_table(args.sweep_run, args.presence_run, args.gate)
    lock_of = {cid: int(v["current_code_lock"]) for cid, v in locks.items()}
    ws = bbs.build_window_set(table, brf.FEATURE_NAMES, gate=args.gate)

    evidence = _evidence(ws, lock_of, args.seed)
    intervals = _intervals(evidence)
    frozen = {
        "rule": bbs.BR_BIN_RULE_V1,
        "status": "FROZEN",
        "frozen_utc": datetime.now(timezone.utc).isoformat(),
        "why_this_rule": (
            "A searched nine-feature rule (834 sign vectors, l0 cap 3) was built and measured on "
            "2026-08-04 and LOST to this one out of fold: 2.384 vs 2.291 bpm, with its winning "
            "sign vector disagreeing across all four leave-one-subject-out folds. A permutation "
            "null showed searching those vectors on shuffled errors reaches 2.665 bpm for free "
            "against a 3.370 null. This rule fits nothing, so it cannot be optimistic."
        ),
        "training_subjects": sorted(set(ws.subject.tolist())),
        "training_captures": sorted(set(ws.capture_id.tolist())),
        "n_training_windows_scored": int(ws.labelled.sum()),
        "loso_equals_per_subject": (
            "With no fitted parameters, leave-one-subject-out cross-validation and per-subject "
            "scoring are the same numbers -- there is no training step for a fold to hold out "
            "from. Asserted by tests/test_br_bin_rule.py, not assumed."
        ),
        "training_evidence": evidence,
        "prediction_intervals": intervals,
        "success_criteria": _criteria(intervals),
        "locks_rederived": locks,
        "provenance": _provenance(args),
    }

    (out_dir / "frozen_rule.json").write_text(
        json.dumps(frozen, indent=2, default=str), encoding="utf-8", newline="\n"
    )
    census.to_csv(out_dir / "label_census.csv", index=False, lineterminator="\n")
    rows = []
    for name, sc in evidence["scores"].items():
        rows.append({"rule": name, "scope": "pooled", **sc["pooled"]})
        for s, v in sc["by_subject"].items():
            rows.append({"rule": name, "scope": f"subject_{s}", **v})
    write_csv(out_dir / "training_scores.csv",
              ["rule", "scope", "n_windows", "n_reported", "coverage", "n_scored",
               "mae_bpm", "rmse_bpm", "hit_2bpm", "hit_3bpm"], rows)
    (out_dir / "report.md").write_text(_render_freeze(frozen), encoding="utf-8", newline="\n")
    return frozen


def do_offset_scan(args, out_dir: Path, frozen: dict) -> dict:
    """Re-score the FROZEN rule under the fixed label-origin grid. Cannot change the freeze."""
    # Built once: the radar side does not depend on the label origin, and re-deriving each
    # capture's warmup lock three times would be pure waste.
    base, _census, locks = build_table(args.sweep_run, args.presence_run, args.gate,
                                       verbose=False)
    lock_of = {cid: int(v["current_code_lock"]) for cid, v in locks.items()}

    rows = []
    for off in OFFSET_GRID_S:
        # Labels move with the origin; the radar windows do not.
        ws = bbs.build_window_set(_relabel(base, off), brf.FEATURE_NAMES, gate=args.gate)
        ev = _evidence(ws, lock_of, args.seed)
        for name, sc in ev["scores"].items():
            rows.append({
                "offset_s": off, "rule": name,
                "n_scored": sc["pooled"]["n_scored"],
                "coverage": sc["pooled"]["coverage"],
                "mae_bpm": sc["pooled"]["mae_bpm"],
                "hit_3bpm": sc["pooled"]["hit_3bpm"],
            })
        rows.append({
            "offset_s": off, "rule": "_paired_delta_P0_minus_rule",
            "mae_bpm": ev["paired_delta_P0_minus_rule"]["mean_delta"],
        })
    write_csv(out_dir / "offset_scan.csv",
              ["offset_s", "rule", "n_scored", "coverage", "mae_bpm", "hit_3bpm"], rows)

    ranks = {}
    for off in OFFSET_GRID_S:
        sub = [r for r in rows if r["offset_s"] == off and not r["rule"].startswith("_")
               and r["mae_bpm"] is not None]
        ranks[off] = [r["rule"] for r in sorted(sub, key=lambda r: r["mae_bpm"])]
    stable = len({tuple(v) for v in ranks.values()}) == 1
    result = {
        "grid_s": list(OFFSET_GRID_S), "primary_offset_s": 0.0,
        "rule_ordering_by_offset": {str(k): v for k, v in ranks.items()},
        "ordering_stable_across_offsets": bool(stable),
        "frozen_rule_id": frozen["rule"]["rule_id"],
        "provenance": _provenance(args, {"scanned_frozen_rule_only": True}),
    }
    (out_dir / "offset_scan.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8", newline="\n"
    )
    return result


def _relabel(table, offset_s: float):
    """Shift the label origin by `offset_s` and recompute per-window references."""
    if offset_s == 0.0:
        return table
    import json as _json

    from src import masimo as masimo_mod
    from score_offline import resolve_frame0_epoch

    out = []
    for cid, g in table.groupby("capture_id", sort=False):
        capture_dir = REPO_ROOT / "results" / "live_demo" / cid
        meta = _json.loads((capture_dir / "run_metadata.json").read_text(encoding="utf-8"))
        frame0, _src, _approx, _c = resolve_frame0_epoch(meta)
        csv_path = [p for p in capture_dir.glob("*.csv") if p.name != "live_estimates.csv"][0]
        lab = brf.window_labels(
            masimo_mod.load_masimo(csv_path), frame0 + offset_s, int(g["k"].max()) + 1
        )
        g2 = g.drop(columns=[c for c in ("ref_bpm", "n_finite_rr", "spread_bpm",
                                         "availability_ok", "stationarity_ok", "admitted",
                                         "err_bpm") if c in g.columns])
        out.append(brf.attach_labels(g2, {cid: lab}))
    import pandas as _pd
    return _pd.concat(out).loc[table.index]


def do_test(args, out_dir: Path, frozen: dict) -> dict:
    subject_map = dict(kv.split("=", 1) for kv in args.subject_map)
    table, census, locks = build_table(
        args.sweep_run, args.presence_run, args.gate,
        subject_map=subject_map, strict_grid=False,
    )
    lock_of = {cid: int(v["current_code_lock"]) for cid, v in locks.items()}
    ws = bbs.build_window_set(table, brf.FEATURE_NAMES, gate=args.gate)
    evidence = _evidence(ws, lock_of, args.seed)

    crit = frozen["success_criteria"]
    delta = evidence["paired_delta_P0_minus_rule"]
    n_sub = delta["n_subjects"]
    worse = n_sub - delta["n_subjects_favouring_b"]
    primary_ok = delta["mean_delta"] is not None and delta["mean_delta"] > 0
    majority_ok = worse < max(2, (n_sub // 2) + 1) if n_sub else False
    verdict = {
        "primary_mean_paired_delta": delta["mean_delta"],
        "primary_passed": bool(primary_ok),
        "n_subjects": n_sub,
        "n_subjects_rule_worse": int(worse),
        "majority_passed": bool(majority_ok),
        "coverage_dominance_holds": evidence["coverage_dominance_holds"],
        "decision": "PASS" if (primary_ok and majority_ok) else "FAIL",
        "criteria_as_frozen": crit,
    }
    result = {
        "rule": frozen["rule"], "frozen_rule_path": str(args.frozen_rule),
        "test_subjects": sorted(set(ws.subject.tolist())),
        "test_captures": sorted(set(ws.capture_id.tolist())),
        "subject_map": subject_map,
        "n_windows_scored": int(ws.labelled.sum()),
        "evidence": evidence, "verdict": verdict,
        "provenance": _provenance(args, {"single_touch": True}),
    }
    (out_dir / "test_result.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8", newline="\n"
    )
    census.to_csv(out_dir / "label_census.csv", index=False, lineterminator="\n")
    return result


def _render_freeze(f: dict) -> str:
    L, A = [], None
    A = L.append
    A(f"# FROZEN BR bin-selection rule — `{f['rule']['rule_id']}` v{f['rule']['version']}\n")
    A(f"Frozen {f['frozen_utc']}. Fitted parameters: **{len(f['rule']['fitted_parameters'])}**.\n")
    A(f"> {f['provenance']['reference_origin_caveat']}\n")
    A("\n## The rule\n")
    A(f"- **Selection:** {f['rule']['selection']}")
    A(f"- **Tie-break:** {f['rule']['tie_break']}")
    A(f"- **Abstain:** {f['rule']['abstain']}")
    A(f"- **Radar inputs:** `{'`, `'.join(f['rule']['radar_inputs'])}`")
    A(f"- **Reference inputs:** none")
    A(f"\n{f['why_this_rule']}\n")
    A(f"\n{f['loso_equals_per_subject']}\n")

    A("\n## Training evidence (subjects "
      f"{', '.join(f['training_subjects'])}; {f['n_training_windows_scored']} scored windows)\n")
    A("| rule | coverage | MAE bpm | RMSE | hit±3 | n scored |")
    A("|---|---|---|---|---|---|")
    for name, sc in f["training_evidence"]["scores"].items():
        p = sc["pooled"]
        A(f"| {name} | {p['coverage']} | {p['mae_bpm']} | {p['rmse_bpm']} | "
          f"{p['hit_3bpm']} | {p['n_scored']} |")

    A("\n### Per subject (MAE bpm)\n")
    subs = f["training_subjects"]
    A("| rule | " + " | ".join(subs) + " |")
    A("|---" * (len(subs) + 1) + "|")
    for name, sc in f["training_evidence"]["scores"].items():
        A(f"| {name} | " + " | ".join(
            str(sc["by_subject"].get(s, {}).get("mae_bpm")) for s in subs) + " |")

    d = f["training_evidence"]["paired_delta_P0_minus_rule"]
    A(f"\n### Paired delta, P0 − rule (common scored windows)\n")
    A(f"mean **{d['mean_delta']}** bpm, SE {d['se_delta']}, rule better in "
      f"**{d['n_subjects_favouring_b']}/{d['n_subjects']}** subjects\n")
    A("| subject | n common | MAE P0 | MAE rule | delta |")
    A("|---|---|---|---|---|")
    for r in d["per_subject"]:
        A(f"| {r['subject']} | {r['n_common_windows']} | {r['mae_a']} | {r['mae_b']} | "
          f"{r['delta_a_minus_b']:+} |")

    A("\n## Predictions for the held-out subjects\n")
    for k, v in f["prediction_intervals"].items():
        if v:
            A(f"- **{k}**: mean {v['mean']}, 95 % PI **[{v['lo']}, {v['hi']}]** "
              f"(sd {v['sd']}, n={v['n_observed']}"
              + (", lower bound floored at 0" if v["floored_at_zero"] else "") + ")")

    A("\n## Success criteria — as frozen, before any new capture exists\n")
    c = f["success_criteria"]
    A(f"- **Primary:** {c['primary']}")
    A(f"- **Coverage:** {c['coverage']}")
    A(f"- **Failure:** {c['failure']}")
    A(f"\n> {c['interpretation']}\n")
    A(f"\n> **Not a criterion.** {c['not_a_criterion']}\n")

    A("\n## Warmup locks re-derived with current code\n")
    A("| capture | recorded live lock | current-code lock | differs |")
    A("|---|---|---|---|")
    for cid, v in f["locks_rederived"].items():
        A(f"| {cid.rsplit('_', 1)[-1]} | {v['recorded_live_lock']} | "
          f"{v['current_code_lock']} | {'YES' if v['differs'] else 'no'} |")
    return "\n".join(L) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", choices=("freeze", "offset-scan", "test"), required=True)
    ap.add_argument("--sweep-run", type=Path,
                    default=REPO_ROOT / "results/diagnose/bin_sweep/20260804T131040Z")
    ap.add_argument("--presence-run", type=Path,
                    default=REPO_ROOT / "results/diagnose/signal_presence/20260731T155946Z")
    ap.add_argument("--gate", choices=("admitted", "finite"), default="admitted")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--frozen-rule", type=Path, default=None,
                    help="path to a frozen_rule.json; required by offset-scan and test")
    ap.add_argument("--i-have-frozen-the-rule", action="store_true")
    ap.add_argument("--subject-map", nargs="+", default=[],
                    help="suffix=SUBJECT pairs, e.g. massimo8=E (required by test)")
    ap.add_argument("--out", type=Path,
                    default=REPO_ROOT / "results" / "diagnose" / "br_bin_rule")
    args = ap.parse_args(argv)

    frozen = None
    if args.mode in ("offset-scan", "test"):
        if not args.frozen_rule:
            ap.error(
                f"--mode {args.mode} requires --frozen-rule <frozen_rule.json>. This mode may "
                "only ever score the already-frozen rule; it cannot choose one."
            )
        frozen = json.loads(Path(args.frozen_rule).read_text(encoding="utf-8"))
        if frozen.get("rule", {}).get("rule_id") != bbs.BR_BIN_RULE_V1["rule_id"]:
            ap.error(f"{args.frozen_rule} does not hold a rule this build implements")
    if args.mode == "test":
        if not args.i_have_frozen_the_rule:
            ap.error(
                "--mode test requires --i-have-frozen-the-rule. Held-out subjects are scored "
                "ONCE, with the rule already chosen. Re-running candidate rules against them "
                "fits to the holdout and destroys the only generalisation estimate available -- "
                "which is exactly how massimo4-massimo7 were spent."
            )
        if not args.subject_map:
            ap.error(
                "--mode test requires --subject-map suffix=SUBJECT. Subject identity is not "
                "machine-recorded anywhere and must not be guessed: the by-subject folds and "
                "every paired delta depend on it."
            )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) / f"{stamp}_{args.mode}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"-> {out_dir}", flush=True)

    if args.mode == "freeze":
        f = do_freeze(args, out_dir)
        ev = f["training_evidence"]
        print("\nFROZEN:", f["rule"]["rule_id"],
              f"({len(f['rule']['fitted_parameters'])} fitted parameters)")
        print(f"  rule  MAE {ev['scores']['FROZEN_medoid_always_emit']['pooled']['mae_bpm']} "
              f"cov {ev['coverage_rule']}")
        print(f"  P0    MAE {ev['scores']['P0_static_lock_rederived']['pooled']['mae_bpm']} "
              f"cov {ev['coverage_P0']}")
        pi = f["prediction_intervals"]["mae_mean_of_three_new_subjects"]
        print(f"  predicted MAE, mean of 3 new subjects: {pi['mean']} "
              f"95% PI [{pi['lo']}, {pi['hi']}]")
        print(f"  SHA-256 frozen_rule.json: {sha256(out_dir / 'frozen_rule.json')}")
    elif args.mode == "offset-scan":
        r = do_offset_scan(args, out_dir, frozen)
        print(f"\nlabel-origin scan {list(OFFSET_GRID_S)} s: rule ordering stable = "
              f"{r['ordering_stable_across_offsets']}")
    else:
        r = do_test(args, out_dir, frozen)
        v = r["verdict"]
        print(f"\nHELD-OUT RESULT: {v['decision']}")
        print(f"  mean paired delta (P0 - rule): {v['primary_mean_paired_delta']}")
        print(f"  rule worse in {v['n_subjects_rule_worse']}/{v['n_subjects']} subjects")
        print(f"  coverage dominance holds: {v['coverage_dominance_holds']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
