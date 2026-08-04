"""Rules, scoring, cross-validation and the permutation null for BR bin selection.

Separated from `src/br_features.py` (which builds the table) so the search can be tested
against synthetic arrays without any CSV or capture on disk.

Everything is arranged around one array, `sel[window, rule]` — the bin each rule would
have reported for each window. Once that exists, a score is a gather plus a mean, and the
permutation null costs almost nothing: shuffling the error vector changes which rule
*wins*, but not what any rule *selects*, because no rule sees the reference.

Two conventions, both consequential and both stated rather than implied:

* **Ties break by higher energy, then lower bin index.** Bins are stored sorted by
  `(-energy, bin)` so `np.argmax` — which returns the first maximum — implements this
  exactly, with no epsilon arithmetic.
* **Coverage's denominator is every in-scope window**, including windows where no bin is
  valid and windows the reference gate dropped. Accuracy's denominator is the scored
  subset. They differ, so both counts are always reported; `notes/comparator_prespec_br.md`
  §2.6 requires coverage alongside accuracy in any case.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass
from itertools import combinations, product

import numpy as np
import pandas as pd

HIT_BANDS_BPM = (2.0, 3.0)

#: Maximum number of non-zero signs in a candidate rule. An honest, declarable constraint
#: on degrees of freedom: with ~105 scorable windows over four subjects, a rule that reads
#: more than three features cannot be identified, and the previous bin-policy study
#: (38 combinations, 41 windows) already produced a "best" that was substantially
#: selection noise -- train MAE 0.97 became 2.60 out of sample.
DEFAULT_L0_CAP = 3


@dataclass(frozen=True)
class WindowSet:
    """Per-window x per-bin arrays. Bins sorted by `(-energy, bin)` within each window."""

    capture_id: np.ndarray      # (W,)
    subject: np.ndarray         # (W,)
    k: np.ndarray               # (W,)
    bin_id: np.ndarray          # (W, B) original bin numbers, in sorted order
    valid: np.ndarray           # (W, B) bool
    br: np.ndarray              # (W, B) radar BR, NaN where invalid
    err: np.ndarray             # (W, B) |br - ref|, NaN where invalid or unlabelled
    z: np.ndarray               # (W, B, F)
    labelled: np.ndarray        # (W,) bool: window carries an admissible reference
    feature_names: tuple

    @property
    def n_windows(self) -> int:
        return int(self.valid.shape[0])

    @property
    def n_bins(self) -> int:
        return int(self.valid.shape[1])

    @property
    def n_features(self) -> int:
        return int(self.z.shape[2])


def build_window_set(df: pd.DataFrame, feature_names, *, gate: str = "admitted") -> WindowSet:
    """Pivot the tidy feature table into `WindowSet` arrays.

    `gate` selects which windows carry a usable label: `"admitted"` applies the frozen BR
    admissibility gate (`notes/comparator_prespec_br.md` §2.2), `"finite"` keeps any window
    with a finite reference median — the looser convention
    `scripts/simulate_bin_policy.py:load_reference` used, retained only as a sensitivity.
    """
    if gate not in ("admitted", "finite"):
        raise ValueError(f"gate must be 'admitted' or 'finite', got {gate!r}")

    z_cols = [f"z_{n}" for n in feature_names]
    missing = [c for c in z_cols if c not in df.columns]
    if missing:
        raise ValueError(f"table is missing z-scored column(s) {missing}")

    caps, subs, ks = [], [], []
    bin_rows, valid_rows, br_rows, err_rows, z_rows, lab = [], [], [], [], [], []

    for (cid, k), g in df.groupby(["capture_id", "k"], sort=True):
        order = np.lexsort((
            g["bin"].to_numpy(dtype=int),
            -pd.to_numeric(g["energy"], errors="coerce").to_numpy(dtype=float),
        ))
        gg = g.iloc[order]
        caps.append(str(cid))
        subs.append(str(gg["subject"].iloc[0]))
        ks.append(int(k))
        bin_rows.append(gg["bin"].to_numpy(dtype=int))
        valid_rows.append(gg["valid"].to_numpy(dtype=bool))
        br_rows.append(pd.to_numeric(gg["br_bpm"], errors="coerce").to_numpy(dtype=float))
        err_rows.append(gg["err_bpm"].to_numpy(dtype=float))
        z_rows.append(np.stack([gg[c].to_numpy(dtype=float) for c in z_cols], axis=-1))
        ref_ok = bool(np.isfinite(float(gg["ref_bpm"].iloc[0])))
        lab.append(ref_ok and (bool(gg["admitted"].iloc[0]) if gate == "admitted" else True))

    return WindowSet(
        capture_id=np.array(caps), subject=np.array(subs), k=np.array(ks, dtype=int),
        bin_id=np.stack(bin_rows), valid=np.stack(valid_rows), br=np.stack(br_rows),
        err=np.stack(err_rows), z=np.stack(z_rows), labelled=np.array(lab, dtype=bool),
        feature_names=tuple(feature_names),
    )


# ── selection ────────────────────────────────────────────────────────────────


def select_from_scores(scores: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """argmax over valid bins; -1 where the window has no valid bin ("no estimate").

    `scores` is (W, B) or (W, B, V); the return is (W,) or (W, V).
    """
    masked = np.where(valid[..., None] if scores.ndim == 3 else valid, scores, -np.inf)
    sel = np.argmax(masked, axis=1)
    any_valid = valid.any(axis=1)
    if scores.ndim == 3:
        return np.where(any_valid[:, None], sel, -1)
    return np.where(any_valid, sel, -1)


def sign_vectors(n_features: int, l0_cap: int = DEFAULT_L0_CAP) -> np.ndarray:
    """Every {-1,0,+1} vector with 1..l0_cap non-zeros. Shape (V, F).

    Enumerated exhaustively rather than reached by greedy forward selection. The space is
    then exactly known and exactly reportable, which is what makes the permutation null
    meaningful: greedy still searches tens of combinations, but with an effective degrees
    of freedom nobody can compute or calibrate against.
    """
    out = []
    for size in range(1, l0_cap + 1):
        for idx in combinations(range(n_features), size):
            for signs in product((-1.0, 1.0), repeat=size):
                v = np.zeros(n_features, dtype=float)
                for j, s in zip(idx, signs):
                    v[j] = s
                out.append(v)
    return np.stack(out)


def selections_for_vectors(ws: WindowSet, vectors: np.ndarray) -> np.ndarray:
    """(W, V) selected bin column per window per sign vector.

    Independent of the reference by construction — this is the whole reason the
    permutation null is cheap, and also the reason it is a real control.
    """
    scores = np.einsum("wbf,vf->wbv", ws.z, vectors)
    return select_from_scores(scores, ws.valid)


def gather_errors(err: np.ndarray, sel: np.ndarray) -> np.ndarray:
    """err[w, sel[w, v]] -> (W, V), NaN where sel is -1."""
    sel2 = sel if sel.ndim == 2 else sel[:, None]
    taken = np.take_along_axis(err, np.clip(sel2, 0, None), axis=1)
    return np.where(sel2 >= 0, taken, np.nan)


# ── scoring ──────────────────────────────────────────────────────────────────


def score_selection(ws: WindowSet, sel: np.ndarray, mask: np.ndarray | None = None) -> dict:
    """Coverage and accuracy for one rule over the windows selected by `mask`."""
    mask = np.ones(ws.n_windows, dtype=bool) if mask is None else np.asarray(mask, bool)
    reported = (sel >= 0) & mask
    scored = reported & ws.labelled
    errs = gather_errors(ws.err, sel)[:, 0][scored]
    errs = errs[np.isfinite(errs)]
    n_win = int(mask.sum())
    return {
        "n_windows": n_win,
        "n_reported": int(reported.sum()),
        "coverage": round(float(reported.sum() / n_win), 4) if n_win else None,
        "n_scored": int(errs.size),
        "mae_bpm": round(float(np.mean(errs)), 4) if errs.size else None,
        "rmse_bpm": round(float(np.sqrt(np.mean(errs ** 2))), 4) if errs.size else None,
        "hit_2bpm": round(float(np.mean(errs <= 2.0)), 4) if errs.size else None,
        "hit_3bpm": round(float(np.mean(errs <= 3.0)), 4) if errs.size else None,
    }


def _mae_from_gathered(err_wv: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Column-wise MAE over the masked rows. (W, V) -> (V,), NaN-safe."""
    sub = err_wv[mask]
    with np.errstate(invalid="ignore"):
        return np.nanmean(np.where(np.isfinite(sub), sub, np.nan), axis=0)


# ── the frozen rule ──────────────────────────────────────────────────────────

#: **BR bin-selection rule v1.** Declarative on purpose: this dict is what gets hashed into a
#: freeze artifact and what a future reader must be able to re-implement from, without reading
#: the code. `fitted_parameters` being empty is the whole argument for the rule — with nothing
#: fitted there is no selection optimism, so a leave-one-subject-out score IS an estimate of
#: future performance rather than an upper bound on one.
#:
#: Chosen over a searched multi-feature rule on 2026-08-04 because the search lost to it out of
#: fold (2.384 vs 2.291 bpm) and its winning sign vector disagreed across all four folds. See
#: `HISTORY.md` 2026-08-04 and `scripts/br_bin_preflight.py`.
BR_BIN_RULE_V1 = {
    "rule_id": "medoid_consensus_always_emit",
    "version": 1,
    "selection": (
        "Among the bins with br_valid in this window, report the one whose br_bpm is closest "
        "to the median of br_bpm over those same valid bins."
    ),
    "tie_break": "higher energy, then lower bin index",
    "abstain": "none - emits whenever at least one bin in the window is br_valid",
    "fitted_parameters": [],
    "radar_inputs": ["br_bpm", "br_valid", "energy"],
    "reference_inputs": [],
    "window_grid": "notes/analysis_prespec.md §7 - non-overlapping 600-frame windows, k>=1 scored",
}


def rule_from_spec(ws: WindowSet, spec: dict = BR_BIN_RULE_V1) -> np.ndarray:
    """Dispatch a frozen rule spec to its implementation. Refuses anything it does not know."""
    if spec.get("rule_id") != BR_BIN_RULE_V1["rule_id"] or spec.get("version") != 1:
        raise ValueError(
            f"unknown frozen rule {spec.get('rule_id')!r} v{spec.get('version')!r}; "
            "this build implements only "
            f"{BR_BIN_RULE_V1['rule_id']!r} v{BR_BIN_RULE_V1['version']}"
        )
    return rule_medoid(ws)


# ── zero-parameter reference rules ───────────────────────────────────────────


def rule_medoid(ws: WindowSet) -> np.ndarray:
    """Bin whose BR is closest to the window median over valid bins. Zero parameters."""
    br = np.where(ws.valid, ws.br, np.nan)
    # A window with no valid bin medians to NaN and is then dropped by `select_from_scores`
    # as "no estimate" -- an expected state, not a numerical accident, so the warning is
    # suppressed rather than left to litter every run.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        med = np.nanmedian(br, axis=1, keepdims=True)
    return select_from_scores(-np.abs(br - med), ws.valid)


def rule_max_energy(ws: WindowSet) -> np.ndarray:
    """Highest-energy valid bin. Bins are energy-sorted, so this is the first valid column."""
    ramp = -np.arange(ws.n_bins, dtype=float)[None, :] * np.ones((ws.n_windows, 1))
    return select_from_scores(ramp, ws.valid)


def rule_fixed_bin(ws: WindowSet, bin_number: int) -> np.ndarray:
    """Always report `bin_number`, and only when it is valid — production's P0 behaviour."""
    is_target = ws.bin_id == int(bin_number)
    usable = is_target & ws.valid
    sel = np.argmax(usable.astype(float), axis=1)
    return np.where(usable.any(axis=1), sel, -1)


def rule_random(ws: WindowSet, rng: np.random.Generator) -> np.ndarray:
    """Uniformly random valid bin — the null a selection rule must beat to mean anything."""
    scores = rng.random((ws.n_windows, ws.n_bins))
    return select_from_scores(scores, ws.valid)


def rule_oracle(ws: WindowSet) -> np.ndarray:
    """CEILING, NOT A RULE. Uses the reference; no honest selector can beat it."""
    err = np.where(np.isfinite(ws.err), ws.err, np.inf)
    return select_from_scores(-err, ws.valid)


def oracle_normalised_skill(mae_rule, mae_null, mae_oracle) -> float | None:
    """(null - rule) / (null - oracle). Immune to subject difficulty, which moves all three."""
    if None in (mae_rule, mae_null, mae_oracle):
        return None
    denom = mae_null - mae_oracle
    if denom <= 0:
        return None
    return round(float((mae_null - mae_rule) / denom), 4)


# ── leave-one-subject-out ────────────────────────────────────────────────────


def loso_search(ws: WindowSet, vectors: np.ndarray, sel_wv: np.ndarray,
                err_wv: np.ndarray | None = None) -> dict:
    """Re-run the whole search inside each held-out-subject fold.

    Folds are by SUBJECT, not by capture: the two captures of a subject share posture,
    scene, chest geometry and breathing style, so a capture-level fold is close to
    in-sample and would understate optimism -- the exact error this study exists to avoid.

    Both pooling conventions are returned. They are not interchangeable: the folds are
    badly unbalanced (subject A contributes roughly a quarter of the windows C and D do),
    so the unweighted fold mean and the window-weighted pooled figure can differ by more
    than the effect being measured. The window-weighted number is the primary.
    """
    err_wv = gather_errors(ws.err, sel_wv) if err_wv is None else err_wv
    subjects = sorted(set(ws.subject.tolist()))
    folds, oof_err, oof_subject = [], [], []

    for s in subjects:
        held = ws.subject == s
        train = (~held) & ws.labelled
        test = held & ws.labelled
        if not train.any() or not test.any():
            continue
        train_mae = _mae_from_gathered(err_wv, train)
        if not np.isfinite(train_mae).any():
            continue
        best = int(np.nanargmin(train_mae))
        e_test = err_wv[test, best]
        e_test = e_test[np.isfinite(e_test)]
        folds.append({
            "held_out_subject": s,
            "best_vector_index": best,
            "best_vector": vectors[best].tolist(),
            "n_train_windows": int(train.sum()),
            "n_test_windows": int(test.sum()),
            "train_mae_bpm": round(float(train_mae[best]), 4),
            "test_mae_bpm": round(float(np.mean(e_test)), 4) if e_test.size else None,
            "test_hit_3bpm": round(float(np.mean(e_test <= 3.0)), 4) if e_test.size else None,
        })
        oof_err.append(e_test)
        oof_subject.extend([s] * e_test.size)

    pooled = np.concatenate(oof_err) if oof_err else np.array([])
    fold_maes = [f["test_mae_bpm"] for f in folds if f["test_mae_bpm"] is not None]
    chosen = {tuple(f["best_vector"]) for f in folds}
    return {
        "folds": folds,
        "n_folds": len(folds),
        "oof_mae_bpm_window_weighted": round(float(np.mean(pooled)), 4) if pooled.size else None,
        "oof_mae_bpm_fold_mean": round(float(np.mean(fold_maes)), 4) if fold_maes else None,
        "oof_hit_3bpm": round(float(np.mean(pooled <= 3.0)), 4) if pooled.size else None,
        "fold_mae_spread": (
            [round(min(fold_maes), 4), round(max(fold_maes), 4)] if fold_maes else None
        ),
        "n_distinct_vectors_chosen": len(chosen),
        "vector_stable_across_folds": len(chosen) == 1 and len(folds) > 1,
    }


def in_sample_best(ws: WindowSet, vectors: np.ndarray, err_wv: np.ndarray) -> dict:
    """The best sign vector fit and scored on the same windows. Always optimistic."""
    mae = _mae_from_gathered(err_wv, ws.labelled)
    best = int(np.nanargmin(mae))
    return {
        "best_vector_index": best,
        "best_vector": vectors[best].tolist(),
        "mae_bpm": round(float(mae[best]), 4),
        "n_vectors": int(vectors.shape[0]),
    }


# ── permutation null over the search itself ──────────────────────────────────


def permute_errors_within_window(err: np.ndarray, valid: np.ndarray,
                                 rng: np.random.Generator) -> np.ndarray:
    """Shuffle each window's errors among its own valid bins.

    Destroys any association between a bin's features and its accuracy while preserving
    the window structure, the validity mask and the marginal distribution of errors. What
    survives this is real; what does not was the search finding shape in noise.
    """
    out = err.copy()
    for w in range(err.shape[0]):
        idx = np.flatnonzero(valid[w])
        if idx.size > 1:
            out[w, idx] = err[w, rng.permutation(idx)]
    return out


def permutation_null(ws: WindowSet, vectors: np.ndarray, sel_wv: np.ndarray,
                     n_draws: int, seed: int) -> dict:
    """Re-run the entire search on shuffled errors, `n_draws` times.

    Reports two distributions, because they answer different questions: the best in-sample
    MAE says how much the SEARCH alone buys on pure noise, and the out-of-fold MAE says
    whether anything survives honest cross-validation.
    """
    rng = np.random.default_rng(seed)
    in_sample, oof = [], []
    for _ in range(int(n_draws)):
        err_perm = permute_errors_within_window(ws.err, ws.valid, rng)
        err_wv = gather_errors(err_perm, sel_wv)
        in_sample.append(float(np.nanmin(_mae_from_gathered(err_wv, ws.labelled))))
        shuffled = WindowSet(
            capture_id=ws.capture_id, subject=ws.subject, k=ws.k, bin_id=ws.bin_id,
            valid=ws.valid, br=ws.br, err=err_perm, z=ws.z, labelled=ws.labelled,
            feature_names=ws.feature_names,
        )
        res = loso_search(shuffled, vectors, sel_wv, err_wv=err_wv)
        if res["oof_mae_bpm_window_weighted"] is not None:
            oof.append(res["oof_mae_bpm_window_weighted"])

    def _dist(xs):
        a = np.asarray(xs, dtype=float)
        if not a.size:
            return None
        return {
            "n": int(a.size), "mean": round(float(np.mean(a)), 4),
            "p05": round(float(np.percentile(a, 5, method="linear")), 4),
            "p50": round(float(np.percentile(a, 50, method="linear")), 4),
            "p95": round(float(np.percentile(a, 95, method="linear")), 4),
            "min": round(float(np.min(a)), 4), "max": round(float(np.max(a)), 4),
        }

    return {"seed": int(seed), "n_draws": int(n_draws),
            "in_sample_best": _dist(in_sample), "oof": _dist(oof),
            "_in_sample_draws": [round(x, 4) for x in in_sample],
            "_oof_draws": [round(x, 4) for x in oof]}


# ── prediction intervals (n=4 subjects; the honest way to state what E/F/G will do) ──

#: Two-sided 95 % Student-t quantiles by degrees of freedom. A ten-entry table rather than
#: `scipy.stats.t.ppf` because `from scipy import stats` fails with exit 127 and NO traceback
#: when the env's `python.exe` is invoked by absolute path (HANDOFF §7) — a silent import
#: failure inside a freeze artifact's provenance is a worse trade than a table anyone can check.
_T_975 = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
    7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179,
    13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101,
    19: 2.093, 20: 2.086,
}


def prediction_interval(values, n_future: int = 1, floor_at_zero: bool = False) -> dict | None:
    """95 % prediction interval for the MEAN of `n_future` new subjects.

    Not a confidence interval on the current mean — a **prediction** interval for what a new
    subject (or the mean of three new subjects) will produce, which is the quantity anyone
    reading a held-out result actually wants. Half-width is
    `t(0.975, n-1) * s * sqrt(1/n_future + 1/n)`; the `1/n_future` term is what a CI omits and
    what makes this interval honest about a single new subject.

    At n=4 subjects it comes out very wide. That is the finding, not a defect of the method:
    a point prediction is exactly what made the previous bin policy's holdout read as a failure
    (46 % coverage against "68 %"), when an interval this honest would not have been surprised.
    """
    a = np.asarray(list(values), dtype=float)
    a = a[np.isfinite(a)]
    n = int(a.size)
    if n < 2:
        return None
    df = n - 1
    if df not in _T_975:
        raise ValueError(f"no tabulated t quantile for df={df}; extend _T_975 deliberately")
    mean = float(np.mean(a))
    s = float(np.std(a, ddof=1))
    half = float(_T_975[df] * s * np.sqrt(1.0 / float(n_future) + 1.0 / n))
    lo, hi = mean - half, mean + half
    out = {
        "n_observed": n, "n_future": int(n_future),
        "mean": round(mean, 4), "sd": round(s, 4),
        "t_975_df": _T_975[df], "half_width": round(half, 4),
        "lo": round(lo, 4), "hi": round(hi, 4),
        "floored_at_zero": False,
    }
    if floor_at_zero and lo < 0.0:
        out["lo"] = 0.0
        out["floored_at_zero"] = True   # MAE cannot be negative; the arithmetic bound can be
    return out


def per_subject_scores(ws: WindowSet, sel: np.ndarray) -> dict:
    """Score one rule per subject, plus the pooled figure.

    For a rule with no fitted parameters, per-subject scoring and leave-one-subject-out
    cross-validation are the SAME NUMBERS — there is no training step for a fold to hold out
    from. That equivalence is why this rule can be frozen from the training subjects and still
    carry an honest generalisation estimate, and it is asserted by test rather than assumed.
    """
    out = {"pooled": score_selection(ws, sel), "by_subject": {}}
    for s in sorted(set(ws.subject.tolist())):
        out["by_subject"][s] = score_selection(ws, sel, ws.subject == s)
    return out
