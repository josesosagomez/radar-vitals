"""Pre-flight for the BR bin-selection feature study: is there anything here to learn?

`HANDOFF.md` §4.2 designed a study to learn a per-window range-bin selection rule for
breathing rate from radar-side features. This script answers the question that must come
first, because the rest of the study is only worth building if the answer is yes:

    does a searched multi-feature rule beat a rule with NO fitted parameters,
    by enough to be resolvable at four subjects?

It reports, on the frozen 30 s window grid and `k>=1` only
(`notes/analysis_prespec.md` §7 labels `k=0` `lock_selection_in_sample`):

* zero-parameter baselines -- random valid bin (the null), production's re-derived warmup
  lock, highest-energy bin, medoid consensus -- and the reference-using oracle ceiling,
  pooled and per subject;
* every candidate feature's within-window rank correlation with error AND the MAE of its
  argmax rule, per subject. These diverge: a feature can correlate with error and still
  select worse than chance, so a gate on rank correlation would be actively misleading;
* an exhaustive search over signed within-window z-score rules with an l0 cap, evaluated
  by leave-one-SUBJECT-out cross-validation with the whole search re-run inside each fold;
* a permutation null over that entire search, which measures how much the search alone
  buys on shuffled errors;
* the fixed-bin degenerate control -- if the learned rule cannot beat "always bin b" out
  of fold, that is the finding.

The Masimo reference is used ONLY to score outcomes and to gate window admissibility. No
rule here reads it; `src/br_features.py` enforces that structurally, and the leak canary in
`tests/test_br_bin_study.py` proves the enforcement works by defeating it on purpose.

Usage
-----
    python -X utf8 scripts/br_bin_preflight.py \
        --sweep-run results/diagnose/bin_sweep/<stamp> \
        --presence-run results/diagnose/signal_presence/<stamp>
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from src import br_bin_search as bbs                                    # noqa: E402
from src import br_features as brf                                      # noqa: E402
from src import masimo as masimo_mod                                    # noqa: E402

import diagnose_bin_drift as bindrift                                   # noqa: E402
from score_offline import resolve_frame0_epoch                          # noqa: E402
from simulate_bin_policy import current_code_lock                       # noqa: E402

# ── go/no-go, written here before the run ────────────────────────────────────
#
# Disclosure, because it is the honest framing and this project does not pre-register
# anything (CLAUDE.md §4): these thresholds were chosen knowing an exploratory,
# uncommitted run had already suggested the answer was "no". They are a transparency
# device -- the criterion is written down and applied identically to every rule compared
# -- not a claim about timing.
#
# MIN_GAIN is a practical-significance floor, not a statistical one: the per-subject
# standard error at n=4 is around 0.5 bpm, so a rule that buys less than that is not
# resolvable by this dataset no matter how it is analysed.
MIN_GAIN_OVER_MEDOID_BPM = 0.5
#: The searched sign vector must be IDENTICAL across all folds. This is the failure mode
#: that killed the previous bin policy: a rule chosen on three subjects that is not the
#: rule three other subjects would have chosen is not a rule, it is a coin flip.
REQUIRE_VECTOR_STABLE_ACROSS_FOLDS = True

DEFAULT_NULL_DRAWS = 200
DEFAULT_PERM_DRAWS = 300
DEFAULT_SEED = 20260804


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    """LF-pinned stdlib CSV. Line endings are load-bearing in this repo (HANDOFF §7)."""
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(columns)
        for r in rows:
            w.writerow(["" if r.get(c) is None else r[c] for c in columns])


def rank_within(x: np.ndarray) -> np.ndarray:
    """Average ranks, ties shared. Hand-rolled to avoid `scipy.stats`, which fails with
    exit 127 and no traceback when the env's python.exe is invoked by absolute path."""
    order = np.argsort(x, kind="mergesort")
    ranks = np.empty(len(x), dtype=float)
    ranks[order] = np.arange(1, len(x) + 1, dtype=float)
    sx = x[order]
    i = 0
    while i < len(sx):
        j = i
        while j + 1 < len(sx) and sx[j + 1] == sx[i]:
            j += 1
        if j > i:
            ranks[order[i:j + 1]] = float(np.mean(ranks[order[i:j + 1]]))
        i = j + 1
    return ranks


def spearman_within_windows(ws: bbs.WindowSet, f_index: int) -> float | None:
    """Mean over windows of Spearman(feature, |error|) across that window's valid bins."""
    rhos = []
    for w in range(ws.n_windows):
        if not ws.labelled[w]:
            continue
        idx = np.flatnonzero(ws.valid[w] & np.isfinite(ws.err[w]))
        if idx.size < 3:
            continue
        a, b = ws.z[w, idx, f_index], ws.err[w, idx]
        if np.std(a) <= 0 or np.std(b) <= 0:
            continue
        ra, rb = rank_within(a), rank_within(b)
        rhos.append(float(np.corrcoef(ra, rb)[0, 1]))
    return round(float(np.mean(rhos)), 4) if rhos else None


def paired_subject_delta(ws: bbs.WindowSet, sel_a: np.ndarray, sel_b: np.ndarray) -> dict:
    """MAE(a) - MAE(b) per subject, on the windows where BOTH rules report and are scored.

    A common scored set is not a nicety: the two rules can differ in coverage, and without
    it a coverage change masquerades as an accuracy change.
    """
    ea = bbs.gather_errors(ws.err, sel_a)[:, 0]
    eb = bbs.gather_errors(ws.err, sel_b)[:, 0]
    common = ws.labelled & (sel_a >= 0) & (sel_b >= 0) & np.isfinite(ea) & np.isfinite(eb)
    per_subject, deltas = [], []
    for s in sorted(set(ws.subject.tolist())):
        m = common & (ws.subject == s)
        if not m.any():
            continue
        d = float(np.mean(ea[m]) - np.mean(eb[m]))
        deltas.append(d)
        per_subject.append({
            "subject": s, "n_common_windows": int(m.sum()),
            "mae_a": round(float(np.mean(ea[m])), 4),
            "mae_b": round(float(np.mean(eb[m])), 4),
            "delta_a_minus_b": round(d, 4),
        })
    n = len(deltas)
    mean = float(np.mean(deltas)) if n else None
    se = float(np.std(deltas, ddof=1) / np.sqrt(n)) if n > 1 else None
    return {
        "per_subject": per_subject,
        "mean_delta": round(mean, 4) if mean is not None else None,
        "se_delta": round(se, 4) if se is not None else None,
        "n_subjects": n,
        "n_subjects_favouring_b": int(sum(d > 0 for d in deltas)),
    }


def build_table(sweep_run: Path, presence_run: Path, gate: str, verbose: bool = True,
                subject_map: dict | None = None, strict_grid: bool = True):
    """Merge, label and z-score. Returns (table_k_ge_1, label_census, locks).

    `subject_map` / `strict_grid` exist so `scripts/br_bin_rule.py` can score captures the
    study has not seen (subjects E, F, G), whose window counts are not in the frozen table.
    """
    merged = brf.load_merged(sweep_run / "windows.csv", presence_run / "windows.csv",
                             strict_grid=strict_grid)
    if verbose:
        print(f"merged {len(merged)} cells from {merged['capture_id'].nunique()} captures",
              flush=True)

    live_cfg = yaml.safe_load(
        (REPO_ROOT / "scripts" / "live_demo_config.yaml").read_text(encoding="utf-8")
    )

    labels_by_capture, census, locks = {}, [], {}
    for cid, g in merged.groupby("capture_id", sort=True):
        capture_dir = REPO_ROOT / "results" / "live_demo" / cid
        meta = json.loads((capture_dir / "run_metadata.json").read_text(encoding="utf-8"))
        frame0, origin_source, is_approx, _caveat = resolve_frame0_epoch(meta)
        csv_path = [p for p in capture_dir.glob("*.csv") if p.name != "live_estimates.csv"][0]
        masimo_df = masimo_mod.load_masimo(csv_path)
        n_windows = int(g["k"].nunique())
        lab = brf.window_labels(masimo_df, frame0, n_windows)
        labels_by_capture[cid] = lab

        # Production's baseline bin, re-derived with current code rather than read from
        # run_metadata.json -- the recorded lock for massimo1/massimo2/sweep came from
        # pre-M2-fix code and is a documented mislock.
        recorded = int(meta["locked_bin"])
        derived = current_code_lock(capture_dir, live_cfg)
        locks[cid] = {"recorded_live_lock": recorded, "current_code_lock": int(derived),
                      "differs": recorded != int(derived)}

        k1 = lab[lab["k"] >= 1]
        census.append({
            "capture_id": cid,
            "subject": (subject_map or {}).get(
                brf.capture_suffix(cid),
                brf.SUBJECT_BY_CAPTURE.get(brf.capture_suffix(cid)),
            ),
            "frame0_origin": origin_source,
            "frame0_is_approximate": bool(is_approx),
            "recorded_live_lock": recorded,
            "current_code_lock": int(derived),
            "n_windows_k_ge_1": int(len(k1)),
            "n_finite_ref": int(np.isfinite(k1["ref_bpm"]).sum()),
            "n_admitted": int(k1["admitted"].sum()),
            "n_dropped_availability": int((~k1["availability_ok"]).sum()),
            "n_dropped_stationarity": int(
                (k1["availability_ok"] & ~k1["stationarity_ok"]).sum()
            ),
        })
        if verbose:
            print(f"  {brf.capture_suffix(cid):>9}: {len(k1)} windows k>=1, "
                  f"{int(k1['admitted'].sum())} admitted, lock {recorded}->{derived}",
                  flush=True)

    table = brf.add_derived_features(merged, subject_map=subject_map)
    table = brf.attach_labels(table, labels_by_capture)
    table = brf.zscore_within_window(table, brf.FEATURE_NAMES)
    table = table[table["k"] >= 1].reset_index(drop=True)
    return table, pd.DataFrame(census), locks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sweep-run", type=Path,
                    default=REPO_ROOT / "results/diagnose/bin_sweep/20260804T131040Z")
    ap.add_argument("--presence-run", type=Path,
                    default=REPO_ROOT / "results/diagnose/signal_presence/20260731T155946Z")
    ap.add_argument("--gate", choices=("admitted", "finite"), default="admitted",
                    help="admitted = the frozen BR comparator gate (primary); "
                         "finite = the looser convention, reported as a sensitivity")
    ap.add_argument("--l0-cap", type=int, default=bbs.DEFAULT_L0_CAP)
    ap.add_argument("--n-perm", type=int, default=DEFAULT_PERM_DRAWS)
    ap.add_argument("--n-null-draws", type=int, default=DEFAULT_NULL_DRAWS)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--out", type=Path,
                    default=REPO_ROOT / "results" / "diagnose" / "br_bin_preflight")
    args = ap.parse_args(argv)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"-> {out_dir}", flush=True)

    table, census, locks = build_table(args.sweep_run, args.presence_run, args.gate)
    liveness = brf.feature_liveness(table, brf.FEATURE_NAMES)
    dead = liveness[~liveness["alive"]]["feature"].tolist()
    if dead:
        print(f"  WARNING: features not alive (constant or mostly NaN): {dead}", flush=True)

    lock_of = {cid: int(info["current_code_lock"]) for cid, info in locks.items()}
    results: dict[str, dict] = {}
    for gate in ("admitted", "finite"):
        ws = bbs.build_window_set(table, brf.FEATURE_NAMES, gate=gate)
        results[gate] = evaluate(ws, args, lock_of)
        if gate == args.gate:
            primary_ws = ws

    write_artifacts(out_dir, args, table, census, liveness, locks, results, primary_ws)
    verdict = results[args.gate]["verdict"]
    print("\n" + "=" * 72)
    print(f"GO/NO-GO ({args.gate} gate): {verdict['decision']}")
    for line in verdict["reasons"]:
        print(f"  - {line}")
    print("=" * 72)
    return 0


def evaluate(ws: bbs.WindowSet, args, lock_of: dict[str, int]) -> dict:
    """Every number for one labelling gate."""
    rng = np.random.default_rng(args.seed)
    subjects = sorted(set(ws.subject.tolist()))

    # ── zero-parameter baselines ─────────────────────────────────────────────
    sel_medoid = bbs.rule_medoid(ws)
    sel_energy = bbs.rule_max_energy(ws)
    sel_oracle = bbs.rule_oracle(ws)

    sel_p0 = np.full(ws.n_windows, -1, dtype=int)
    for cid, lock in lock_of.items():
        m = ws.capture_id == cid
        if m.any():
            sel_p0[m] = bbs.rule_fixed_bin(ws, lock)[m]

    # The null is averaged over many draws: a single random selection is itself noisy,
    # and this number is the denominator of every skill figure below.
    null_errs = []
    for _ in range(int(args.n_null_draws)):
        s = bbs.rule_random(ws, rng)
        e = bbs.gather_errors(ws.err, s)[:, 0]
        null_errs.append(np.where(ws.labelled & (s >= 0), e, np.nan))
    null_stack = np.stack(null_errs)
    with np.errstate(invalid="ignore"):
        mae_null_pooled = float(np.nanmean(null_stack))

    rules = {
        "null_random_valid_bin": None,          # scored from null_stack
        "P0_static_lock_rederived": sel_p0,
        "max_energy": sel_energy,
        "medoid_consensus": sel_medoid,
        "oracle_CEILING": sel_oracle,
    }

    baselines = []
    for name, sel in rules.items():
        if sel is None:
            with np.errstate(invalid="ignore"):
                row = {"rule": name, "scope": "pooled", "n_windows": ws.n_windows,
                       "coverage": round(float(np.mean(ws.valid.any(axis=1))), 4),
                       "mae_bpm": round(mae_null_pooled, 4),
                       "rmse_bpm": round(float(np.sqrt(np.nanmean(null_stack ** 2))), 4),
                       "hit_2bpm": round(float(np.nanmean(null_stack <= 2.0)), 4),
                       "hit_3bpm": round(float(np.nanmean(null_stack <= 3.0)), 4),
                       "n_scored": int(np.isfinite(null_stack[0]).sum())}
        else:
            row = {"rule": name, "scope": "pooled", **bbs.score_selection(ws, sel)}
        baselines.append(row)
        for s in subjects:
            m = ws.subject == s
            if sel is None:
                with np.errstate(invalid="ignore"):
                    sub = null_stack[:, m]
                    baselines.append({
                        "rule": name, "scope": f"subject_{s}", "n_windows": int(m.sum()),
                        "coverage": round(float(np.mean(ws.valid[m].any(axis=1))), 4),
                        "mae_bpm": round(float(np.nanmean(sub)), 4),
                        "rmse_bpm": round(float(np.sqrt(np.nanmean(sub ** 2))), 4),
                        "hit_2bpm": round(float(np.nanmean(sub <= 2.0)), 4),
                        "hit_3bpm": round(float(np.nanmean(sub <= 3.0)), 4),
                        "n_scored": int(np.isfinite(sub[0]).sum()),
                    })
            else:
                baselines.append({"rule": name, "scope": f"subject_{s}",
                                  **bbs.score_selection(ws, sel, m)})

    mae_of = {r["rule"]: r["mae_bpm"] for r in baselines if r["scope"] == "pooled"}
    medoid_mae = mae_of["medoid_consensus"]
    oracle_mae = mae_of["oracle_CEILING"]

    # ── per-feature diagnostics ──────────────────────────────────────────────
    feature_rows = []
    for f_i, fname in enumerate(ws.feature_names):
        rho = spearman_within_windows(ws, f_i)
        for sign in (1.0, -1.0):
            sel = bbs.select_from_scores(sign * ws.z[:, :, f_i], ws.valid)
            sc = bbs.score_selection(ws, sel)
            per_sub = {}
            for s in subjects:
                per_sub[s] = bbs.score_selection(ws, sel, ws.subject == s)["mae_bpm"]
            feature_rows.append({
                "feature": fname, "sign": int(sign),
                "mean_within_window_spearman_vs_err": rho,
                "argmax_rule_mae_bpm": sc["mae_bpm"],
                "argmax_rule_hit_3bpm": sc["hit_3bpm"],
                **{f"mae_subject_{s}": per_sub[s] for s in subjects},
            })

    # ── fixed-bin degenerate control ─────────────────────────────────────────
    fixed_rows = []
    for b in sorted(set(ws.bin_id.ravel().tolist())):
        sel = bbs.rule_fixed_bin(ws, b)
        fixed_rows.append({"bin": int(b), **bbs.score_selection(ws, sel)})
    scored_fixed = [r for r in fixed_rows if r["mae_bpm"] is not None]
    best_fixed = min(scored_fixed, key=lambda r: r["mae_bpm"]) if scored_fixed else None

    # ── exhaustive search + LOSO ─────────────────────────────────────────────
    vectors = bbs.sign_vectors(ws.n_features, args.l0_cap)
    sel_wv = bbs.selections_for_vectors(ws, vectors)
    err_wv = bbs.gather_errors(ws.err, sel_wv)
    in_sample = bbs.in_sample_best(ws, vectors, err_wv)
    loso = bbs.loso_search(ws, vectors, sel_wv, err_wv=err_wv)

    combos = []
    mae_all = np.array([
        np.nanmean(np.where(np.isfinite(err_wv[ws.labelled, v]), err_wv[ws.labelled, v], np.nan))
        for v in range(vectors.shape[0])
    ])
    for v in np.argsort(np.where(np.isfinite(mae_all), mae_all, np.inf)):
        combos.append({
            "rank": len(combos) + 1,
            "vector": json.dumps({
                n: int(s) for n, s in zip(ws.feature_names, vectors[v]) if s != 0
            }),
            "l0": int(np.count_nonzero(vectors[v])),
            "in_sample_mae_bpm": round(float(mae_all[v]), 4) if np.isfinite(mae_all[v]) else None,
        })

    perm = bbs.permutation_null(ws, vectors, sel_wv, args.n_perm, args.seed)

    # ── the searched rule, out of fold, against the parameter-free rule ──────
    oof = loso["oof_mae_bpm_window_weighted"]
    gain = round(medoid_mae - oof, 4) if (oof is not None and medoid_mae is not None) else None

    reasons, ok = [], True
    if gain is None:
        ok = False
        reasons.append("no out-of-fold estimate could be computed")
    else:
        passed = gain >= MIN_GAIN_OVER_MEDOID_BPM
        ok &= passed
        reasons.append(
            f"{'PASS' if passed else 'FAIL'} gain over zero-parameter medoid: "
            f"{gain:+.3f} bpm (need >= {MIN_GAIN_OVER_MEDOID_BPM}); "
            f"medoid {medoid_mae}, searched rule out-of-fold {oof}"
        )
    if REQUIRE_VECTOR_STABLE_ACROSS_FOLDS:
        stable = bool(loso["vector_stable_across_folds"])
        ok &= stable
        reasons.append(
            f"{'PASS' if stable else 'FAIL'} fold stability: "
            f"{loso['n_distinct_vectors_chosen']} distinct sign vector(s) chosen across "
            f"{loso['n_folds']} folds (need 1)"
        )
    if perm["in_sample_best"] is not None and in_sample["mae_bpm"] is not None:
        draws = np.asarray(perm["_in_sample_draws"], dtype=float)
        p = float(np.mean(draws <= in_sample["mae_bpm"]))
        reasons.append(
            f"INFO permutation null: searching {vectors.shape[0]} vectors on shuffled errors "
            f"reaches mean {perm['in_sample_best']['mean']} bpm (best {perm['in_sample_best']['min']}); "
            f"observed in-sample {in_sample['mae_bpm']}, p={p:.4f}"
        )
    if best_fixed is not None and oof is not None:
        reasons.append(
            f"INFO best fixed-bin rule (always bin {best_fixed['bin']}) in-sample MAE "
            f"{best_fixed['mae_bpm']} vs searched rule out-of-fold {oof}"
        )

    return {
        "baselines": baselines,
        "feature_diagnostics": feature_rows,
        "fixed_bin_control": fixed_rows,
        "best_fixed_bin": best_fixed,
        "search": {"n_vectors": int(vectors.shape[0]), "l0_cap": int(args.l0_cap),
                   "in_sample": in_sample, "loso": loso},
        "permutation_null": {k: v for k, v in perm.items() if not k.startswith("_")},
        "combinations": combos,
        "skill": {
            "medoid": bbs.oracle_normalised_skill(medoid_mae, mae_of["null_random_valid_bin"],
                                                  oracle_mae),
            "P0": bbs.oracle_normalised_skill(mae_of["P0_static_lock_rederived"],
                                              mae_of["null_random_valid_bin"], oracle_mae),
            "searched_rule_oof": bbs.oracle_normalised_skill(
                oof, mae_of["null_random_valid_bin"], oracle_mae),
        },
        "paired_delta_P0_minus_medoid": paired_subject_delta(ws, sel_p0, sel_medoid),
        "gain_over_medoid_bpm": gain,
        "verdict": {"decision": "GO" if ok else "NO-GO", "reasons": reasons},
    }


def write_artifacts(out_dir, args, table, census, liveness, locks, results, ws) -> None:
    primary = results[args.gate]

    write_csv(out_dir / "baselines.csv",
              ["gate", "rule", "scope", "n_windows", "n_reported", "coverage", "n_scored",
               "mae_bpm", "rmse_bpm", "hit_2bpm", "hit_3bpm"],
              [{"gate": g, **r} for g, res in results.items() for r in res["baselines"]])

    fd = primary["feature_diagnostics"]
    write_csv(out_dir / "feature_diagnostics.csv", list(fd[0].keys()), fd)
    write_csv(out_dir / "search_combinations.csv",
              ["rank", "vector", "l0", "in_sample_mae_bpm"], primary["combinations"])
    write_csv(out_dir / "fixed_bin_control.csv",
              ["bin", "n_windows", "n_reported", "coverage", "n_scored", "mae_bpm",
               "rmse_bpm", "hit_2bpm", "hit_3bpm"], primary["fixed_bin_control"])
    write_csv(out_dir / "loso_folds.csv",
              ["held_out_subject", "best_vector_index", "best_vector", "n_train_windows",
               "n_test_windows", "train_mae_bpm", "test_mae_bpm", "test_hit_3bpm"],
              primary["search"]["loso"]["folds"])
    census.to_csv(out_dir / "label_census.csv", index=False, lineterminator="\n")
    liveness.to_csv(out_dir / "feature_liveness.csv", index=False, lineterminator="\n")
    table.to_csv(out_dir / "feature_table.csv", index=False, lineterminator="\n")

    meta = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": bindrift.get_git_commit(),
        "git_tree_clean": bindrift.is_tree_clean(),
        "sweep_run": str(args.sweep_run),
        "presence_run": str(args.presence_run),
        "input_hashes": {
            "bin_sweep_windows_csv": sha256(Path(args.sweep_run) / "windows.csv"),
            "signal_presence_windows_csv": sha256(Path(args.presence_run) / "windows.csv"),
        },
        "primary_gate": args.gate,
        "seed": args.seed,
        "n_permutation_draws": args.n_perm,
        "n_null_draws": args.n_null_draws,
        "l0_cap": args.l0_cap,
        "feature_names": list(brf.FEATURE_NAMES),
        "window_grid": {"frames_per_window": 600, "fs_hz": 20.0,
                        "k0_excluded": True,
                        "authority": "notes/analysis_prespec.md §7"},
        "go_no_go": {
            "min_gain_over_medoid_bpm": MIN_GAIN_OVER_MEDOID_BPM,
            "require_vector_stable_across_folds": REQUIRE_VECTOR_STABLE_ACROSS_FOLDS,
            "disclosure": (
                "Thresholds were written into the script before this run, but were chosen "
                "with knowledge of an earlier exploratory, uncommitted computation that "
                "suggested the answer. Nothing in this project is pre-registered; this is a "
                "transparency statement, not a timing claim."
            ),
        },
        "locks": locks,
        "n_windows_scored": int(ws.n_windows),
        "reference_origin_caveat": (
            "The eight existing captures have no persisted frame-0 epoch; alignment is "
            "reconstructed from run_metadata.json:start_wall_utc and is APPROXIMATE "
            "(notes/analysis_prespec.md §7). Never a frozen scoring number."
        ),
        "results": {g: {k: v for k, v in r.items() if k != "combinations"}
                    for g, r in results.items()},
    }
    (out_dir / "run_meta.json").write_text(
        json.dumps(meta, indent=2, default=str), encoding="utf-8", newline="\n"
    )
    (out_dir / "report.md").write_text(render_report(args, census, liveness, results),
                                       encoding="utf-8", newline="\n")


def render_report(args, census, liveness, results) -> str:
    p = results[args.gate]
    L = []
    A = L.append
    A(f"# BR bin-selection pre-flight ({args.gate} gate)\n")
    A(f"Windows: `k>=1` only, frozen 600-frame grid. Gate: **{args.gate}**.\n")
    A("> Labels for these eight captures are reconstructed from `start_wall_utc` and are\n"
      "> APPROXIMATE (`notes/analysis_prespec.md` §7). Never a frozen scoring number.\n")

    A("\n## Verdict\n")
    A(f"**{p['verdict']['decision']}**\n")
    for r in p["verdict"]["reasons"]:
        A(f"- {r}")

    A("\n## Zero-parameter baselines (pooled)\n")
    A("| rule | coverage | MAE bpm | RMSE | hit±2 | hit±3 | n scored |")
    A("|---|---|---|---|---|---|---|")
    for r in p["baselines"]:
        if r["scope"] == "pooled":
            A(f"| {r['rule']} | {r['coverage']} | {r['mae_bpm']} | {r.get('rmse_bpm')} | "
              f"{r['hit_2bpm']} | {r['hit_3bpm']} | {r['n_scored']} |")

    A("\n## Per subject (MAE bpm)\n")
    subs = sorted({r["scope"] for r in p["baselines"] if r["scope"].startswith("subject_")})
    A("| rule | " + " | ".join(s.replace("subject_", "") for s in subs) + " |")
    A("|---" * (len(subs) + 1) + "|")
    for rule in dict.fromkeys(r["rule"] for r in p["baselines"]):
        cells = []
        for s in subs:
            hit = [r for r in p["baselines"] if r["rule"] == rule and r["scope"] == s]
            cells.append(str(hit[0]["mae_bpm"]) if hit else "-")
        A(f"| {rule} | " + " | ".join(cells) + " |")

    A("\n## Searched rule\n")
    s = p["search"]
    A(f"- search space: **{s['n_vectors']}** sign vectors, l0 cap {s['l0_cap']}")
    A(f"- in-sample best: **{s['in_sample']['mae_bpm']}** bpm, vector "
      f"`{s['in_sample']['best_vector']}`")
    A(f"- leave-one-subject-out, window-weighted: **{s['loso']['oof_mae_bpm_window_weighted']}** bpm")
    A(f"- leave-one-subject-out, fold mean: {s['loso']['oof_mae_bpm_fold_mean']} bpm")
    A(f"- fold MAE spread: {s['loso']['fold_mae_spread']}")
    A(f"- distinct vectors chosen across folds: **{s['loso']['n_distinct_vectors_chosen']}** "
      f"of {s['loso']['n_folds']}")
    A(f"- gain over zero-parameter medoid: **{p['gain_over_medoid_bpm']:+} bpm**"
      if p["gain_over_medoid_bpm"] is not None else "- gain: n/a")

    A("\n### Folds\n")
    A("| held out | train MAE | test MAE | test hit±3 | vector |")
    A("|---|---|---|---|---|")
    for f in s["loso"]["folds"]:
        vec = {n: int(v) for n, v in zip(brf.FEATURE_NAMES, f["best_vector"]) if v != 0}
        A(f"| {f['held_out_subject']} | {f['train_mae_bpm']} | {f['test_mae_bpm']} | "
          f"{f['test_hit_3bpm']} | `{vec}` |")

    A("\n## Permutation null over the search\n")
    pn = p["permutation_null"]
    A(f"{pn['n_draws']} draws, seed {pn['seed']}. Errors shuffled within each window across "
      "its valid bins; the entire search re-run on each draw.\n")
    if pn["in_sample_best"]:
        d = pn["in_sample_best"]
        A(f"- best in-sample MAE on shuffled errors: mean **{d['mean']}**, p05 {d['p05']}, "
          f"min {d['min']}")
    if pn["oof"]:
        d = pn["oof"]
        A(f"- out-of-fold MAE on shuffled errors: mean **{d['mean']}**, p05 {d['p05']}, "
          f"min {d['min']}")

    A("\n## Oracle-normalised skill\n")
    A("`(MAE_null - MAE_rule) / (MAE_null - MAE_oracle)`. Subject difficulty moves null, "
      "rule and oracle together; skill does not.\n")
    for k, v in p["skill"].items():
        A(f"- {k}: **{v}**")

    A("\n## Paired per-subject delta, P0 minus medoid (common scored windows)\n")
    pd_ = p["paired_delta_P0_minus_medoid"]
    A(f"mean **{pd_['mean_delta']}** bpm, SE {pd_['se_delta']}, n={pd_['n_subjects']} subjects, "
      f"medoid better in {pd_['n_subjects_favouring_b']}/{pd_['n_subjects']}\n")
    A("| subject | n common | MAE P0 | MAE medoid | delta |")
    A("|---|---|---|---|---|")
    for r in pd_["per_subject"]:
        A(f"| {r['subject']} | {r['n_common_windows']} | {r['mae_a']} | {r['mae_b']} | "
          f"{r['delta_a_minus_b']:+} |")

    A("\n## Feature diagnostics\n")
    A("Rank correlation and selection skill are different questions — a feature can "
      "correlate with error and still select worse than chance.\n")
    A("| feature | sign | mean ρ vs err | argmax MAE | hit±3 |")
    A("|---|---|---|---|---|")
    for r in p["feature_diagnostics"]:
        A(f"| {r['feature']} | {r['sign']:+} | "
          f"{r['mean_within_window_spearman_vs_err']} | {r['argmax_rule_mae_bpm']} | "
          f"{r['argmax_rule_hit_3bpm']} |")

    # `to_string`, not `to_markdown`: the latter needs `tabulate`, which is not in
    # environment.yml, and a report generator that dies on a missing optional dependency
    # after a ten-minute run is a bad trade for prettier pipes.
    A("\n## Feature liveness\n")
    A("```\n" + liveness.to_string(index=False) + "\n```")

    A("\n## Label census\n")
    A("```\n" + census.to_string(index=False) + "\n```")
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
