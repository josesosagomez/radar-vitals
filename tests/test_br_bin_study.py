"""Tests for `src/br_features.py` and `src/br_bin_search.py`.

The BR bin-selection study can produce a confident wrong answer in four ways, and each one
is covered here because none of them is visible in the output:

1. a reference-derived column reaching the feature pool — the rule would then be the
   oracle wearing a rule's clothes, and would look excellent right up until deployment;
2. the two input CSVs joining positionally instead of by key, silently attaching every
   feature to the wrong cell;
3. the temporal feature peeking at window k+1, which improves every number and cannot
   exist in a live tracker;
4. the cross-validation leaking across folds, which would make the out-of-fold estimate —
   the only honest number in the study — meaningless.

The leak canary is a POSITIVE control: it defeats the guard on purpose and asserts the
resulting rule reproduces the oracle exactly. If that assertion ever fails, the harness is
broken and every other number it produces is uninterpretable.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from src import br_bin_search as bbs  # noqa: E402
from src import br_features as brf  # noqa: E402

BINS = list(range(19, 33))


def _sweep_frame(capture="20260713_172042_live_demo_massimo1", n_windows=6, seed=0):
    """A bin_sweep-shaped table with the real column set."""
    rng = np.random.default_rng(seed)
    rows = []
    for k in range(n_windows):
        energies = rng.uniform(1e3, 1e8, size=len(BINS))
        e_ref = energies.max()
        ranks = (-energies).argsort().argsort() + 1
        for i, b in enumerate(BINS):
            rows.append({
                "capture_id": capture, "k": k, "frame_start": k * 600,
                "frame_end": (k + 1) * 600, "bin": b, "range_m": round(b * 0.0436, 4),
                "is_locked_bin": int(b == 23), "energy": float(energies[i]),
                "energy_rank_in_window": int(ranks[i]),
                "rel_db_in_window": float(10 * np.log10(energies[i] / e_ref)),
                "warmup_settled_energy_db": "", "warmup_energy_eligible": "",
                "dsp_failed": 0, "dsp_error": "", "hr_valid": 0, "hr_bpm": "",
                "fallback_hr_bpm": 60.0, "rej_reason": "x", "ahet_ratio_db_best": -2.0,
                "accepted_candidate_rank": -1, "spectrum_stage": int(rng.integers(0, 3)),
                "br_bpm": float(12 + rng.normal(0, 3)), "br_confidence": "medium",
                "br_valid": 1, "f_r_hz": 0.25, "n_eca_skipped": 0, "k_max_eff": 6,
            })
    return pd.DataFrame(rows)


def _presence_frame(sweep, seed=1):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "capture_id": sweep["capture_id"], "k": sweep["k"], "bin": sweep["bin"],
        "range_m": sweep["range_m"], "is_locked_bin": sweep["is_locked_bin"],
        "phase_std_rad": rng.uniform(0.1, 5.0, len(sweep)),
        "hr_ref_bpm": 70.0, "hr_ref_admitted": 1, "hr_ref_spread_bpm": 2.0,
        "hr_argmax_bpm": 60.0, "hr_argmax_err_bpm": 10.0, "hr_peak_snr_db": 8.0,
        "hr_oracle_snr_db": 4.0, "hr_decoy_beat_frac": 0.5, "h2_oracle_snr_db": 6.0,
        "br_ref_bpm": 14.0, "br_argmax_bpm": 15.0, "br_argmax_err_bpm": 1.0,
        "br_peak_snr_db": rng.uniform(3.0, 20.0, len(sweep)),
        "br_oracle_snr_db": 5.0, "br_decoy_beat_frac": 0.4,
    })


def _write_pair(tmp_path, sweep, presence):
    a, b = tmp_path / "sweep.csv", tmp_path / "presence.csv"
    sweep.to_csv(a, index=False, lineterminator="\n")
    presence.to_csv(b, index=False, lineterminator="\n")
    return a, b


def _synthetic_window_set(n_windows=40, n_bins=6, n_features=3, seed=3, signal=True):
    """WindowSet arrays with a known planted signal (or none)."""
    rng = np.random.default_rng(seed)
    z = rng.normal(size=(n_windows, n_bins, n_features))
    valid = np.ones((n_windows, n_bins), dtype=bool)
    # Error falls as feature 0 rises, so the true rule is sign +1 on feature 0. Clipped
    # rather than made |.|, which would fold the relationship back on itself and destroy
    # the monotonicity the test is asserting.
    noise = rng.uniform(0.0, 1.0, size=(n_windows, n_bins))
    err = np.clip(5.0 - (2.0 * z[:, :, 0] if signal else 0.0) + noise, 0.05, None)
    subjects = np.array(["ABCD"[i % 4] for i in range(n_windows)])
    return bbs.WindowSet(
        capture_id=np.array([f"c{i % 4}" for i in range(n_windows)]),
        subject=subjects, k=np.arange(1, n_windows + 1),
        bin_id=np.tile(np.arange(n_bins), (n_windows, 1)),
        valid=valid, br=rng.normal(15, 2, size=(n_windows, n_bins)), err=err, z=z,
        labelled=np.ones(n_windows, dtype=bool),
        feature_names=tuple(f"f{i}" for i in range(n_features)),
    )


# ── 1. reference leakage ─────────────────────────────────────────────────────


@pytest.mark.parametrize("col", ["br_ref_bpm", "br_argmax_err_bpm", "br_oracle_snr_db",
                                 "hr_ref_bpm", "br_decoy_beat_frac"])
def test_reference_derived_columns_are_rejected(col):
    with pytest.raises(brf.ReferenceLeakError, match="reference-derived"):
        brf.assert_no_reference_leak(["rel_db_in_window", col])


@pytest.mark.parametrize("col", ["is_locked_bin", "br_bpm"])
def test_banned_columns_are_rejected(col):
    """`is_locked_bin` is the pre-M2-fix recorded lock and looks like the right thing."""
    with pytest.raises(brf.ReferenceLeakError, match="banned"):
        brf.assert_no_reference_leak([col])


def test_unclassified_column_raises(tmp_path):
    """A column added to either CSV later must be classified, not silently defaulted."""
    sweep = _sweep_frame()
    sweep["some_new_diagnostic"] = 1.0
    with pytest.raises(brf.FeatureTableError, match="unclassified"):
        brf.classify_columns(sweep.columns)


def test_every_search_feature_is_reference_free():
    """The declared feature pool must survive its own guard."""
    sources = [brf._SOURCE_OF_FEATURE.get(n, n) for n in brf.FEATURE_NAMES]
    brf.assert_no_reference_leak(sources)


def test_leak_canary_reproduces_the_oracle():
    """POSITIVE control: a leaked reference feature must behave exactly like the oracle.

    If this fails, the scoring harness is not measuring what it claims to and no other
    number in the study can be trusted.
    """
    ws = _synthetic_window_set()
    leaked = np.zeros_like(ws.z)
    leaked[:, :, 0] = -ws.err                      # the leak, unmasked by any guard
    leaked_ws = bbs.WindowSet(
        capture_id=ws.capture_id, subject=ws.subject, k=ws.k, bin_id=ws.bin_id,
        valid=ws.valid, br=ws.br, err=ws.err, z=leaked, labelled=ws.labelled,
        feature_names=ws.feature_names,
    )
    sel_leaked = bbs.select_from_scores(leaked_ws.z[:, :, 0], leaked_ws.valid)
    sel_oracle = bbs.rule_oracle(ws)
    assert bbs.score_selection(leaked_ws, sel_leaked)["mae_bpm"] == pytest.approx(
        bbs.score_selection(ws, sel_oracle)["mae_bpm"]
    )


# ── 2. the join ──────────────────────────────────────────────────────────────


def test_merge_is_keyed_not_positional(tmp_path):
    """Shuffling one CSV's row order must not change a single feature value."""
    sweep = _sweep_frame()
    presence = _presence_frame(sweep)
    a, b = _write_pair(tmp_path, sweep, presence)
    ordered = brf.load_merged(a, b, strict_grid=False)

    shuffled = presence.sample(frac=1.0, random_state=7)
    b2 = tmp_path / "presence_shuffled.csv"
    shuffled.to_csv(b2, index=False, lineterminator="\n")
    reshuffled = brf.load_merged(a, b2, strict_grid=False)

    key = list(brf.KEY_COLUMNS)
    left = ordered.sort_values(key).reset_index(drop=True)
    right = reshuffled.sort_values(key).reset_index(drop=True)
    pd.testing.assert_series_equal(left["phase_std_rad"], right["phase_std_rad"])


def test_merge_rejects_duplicate_keys(tmp_path):
    sweep = _sweep_frame()
    presence = _presence_frame(sweep)
    dup = pd.concat([presence, presence.iloc[[0]]], ignore_index=True)
    a, b = _write_pair(tmp_path, sweep, dup)
    with pytest.raises(brf.FeatureTableError, match="duplicate"):
        brf.load_merged(a, b, strict_grid=False)


def test_merge_rejects_non_overlapping_cells(tmp_path):
    sweep = _sweep_frame()
    presence = _presence_frame(sweep).iloc[:-1]
    a, b = _write_pair(tmp_path, sweep, presence)
    with pytest.raises(brf.FeatureTableError, match="merge lost rows"):
        brf.load_merged(a, b, strict_grid=False)


def test_grid_assertion_catches_a_missing_window(tmp_path):
    sweep = _sweep_frame(n_windows=5)                       # massimo1 must have 6
    presence = _presence_frame(sweep)
    a, b = _write_pair(tmp_path, sweep, presence)
    with pytest.raises(brf.FeatureTableError, match="windows, expected 6"):
        brf.load_merged(a, b)


# ── 3. causality and liveness ────────────────────────────────────────────────


def test_temporal_feature_is_causal(tmp_path):
    """Mutating window k+1 must leave the temporal feature at k bit-identical."""
    sweep = _sweep_frame(n_windows=6)
    presence = _presence_frame(sweep)
    a, b = _write_pair(tmp_path, sweep, presence)
    base = brf.add_derived_features(brf.load_merged(a, b, strict_grid=False))

    future = sweep.copy()
    tail = future["k"] >= 4
    future.loc[tail, "br_bpm"] = future.loc[tail, "br_bpm"] + 25.0
    a2 = tmp_path / "sweep_future.csv"
    future.to_csv(a2, index=False, lineterminator="\n")
    perturbed = brf.add_derived_features(brf.load_merged(a2, b, strict_grid=False))

    past = base["k"] < 4
    np.testing.assert_array_equal(
        base.loc[past, "temporal_dev"].to_numpy(),
        perturbed.loc[past, "temporal_dev"].to_numpy(),
    )


def test_temporal_feature_uses_history_when_present(tmp_path):
    """A bin holding a constant value has zero temporal deviation from k=1 onwards."""
    sweep = _sweep_frame(n_windows=6)
    sweep.loc[sweep["bin"] == 25, "br_bpm"] = 14.0
    presence = _presence_frame(sweep)
    a, b = _write_pair(tmp_path, sweep, presence)
    df = brf.add_derived_features(brf.load_merged(a, b, strict_grid=False))
    steady = df[(df["bin"] == 25) & (df["k"] >= 1)]["temporal_dev"]
    assert np.allclose(steady.to_numpy(), 0.0)
    assert np.isnan(df[(df["bin"] == 25) & (df["k"] == 0)]["temporal_dev"].iloc[0])


def test_liveness_flags_dead_and_constant_features(tmp_path):
    """`warmup_settled_energy_db` is empty on all 1792 real rows; that class must be caught."""
    sweep = _sweep_frame()
    sweep["spectrum_stage"] = 1                       # constant within every window
    presence = _presence_frame(sweep)
    presence["br_peak_snr_db"] = np.nan               # dead
    a, b = _write_pair(tmp_path, sweep, presence)
    df = brf.add_derived_features(brf.load_merged(a, b, strict_grid=False))
    live = brf.feature_liveness(df, ("spectrum_stage", "br_snr", "rel_db"))
    by = live.set_index("feature")
    assert not bool(by.loc["spectrum_stage", "alive"])
    assert not bool(by.loc["br_snr", "alive"])
    assert bool(by.loc["rel_db", "alive"])


def test_every_declared_feature_is_numeric_and_alive(tmp_path):
    """No declared feature may silently resolve to NaN.

    Regression: `conf_ord` pointed at the raw `br_confidence` STRINGS rather than at the
    ordinal derived from them, so every value coerced to NaN, z-scored to a constant zero,
    and contributed nothing to any ranking — while still inflating the reported size of
    the search space, and therefore the permutation null it is calibrated against.
    """
    rng = np.random.default_rng(5)
    sweep = _sweep_frame()
    sweep["br_confidence"] = rng.choice(["low", "medium", "high"], len(sweep))
    presence = _presence_frame(sweep)
    a, b = _write_pair(tmp_path, sweep, presence)
    df = brf.add_derived_features(brf.load_merged(a, b, strict_grid=False))
    # k>=1 only, as the study scores it: `temporal_dev` is undefined at k=0 by design,
    # and liveness is a property of the scorable set, not of the raw table.
    df = df[df["k"] >= 1].reset_index(drop=True)
    live = brf.feature_liveness(df, brf.FEATURE_NAMES).set_index("feature")
    dead = [f for f in brf.FEATURE_NAMES if not bool(live.loc[f, "alive"])]
    assert not dead, f"declared but dead: {dead}"

    z = brf.zscore_within_window(df, brf.FEATURE_NAMES)
    for f in brf.FEATURE_NAMES:
        assert np.any(z[f"z_{f}"].to_numpy() != 0.0), f"z_{f} is identically zero"


def test_zscore_is_within_window_and_scale_free(tmp_path):
    """A per-window affine rescale of a feature must not change its z-scores."""
    sweep = _sweep_frame()
    presence = _presence_frame(sweep)
    a, b = _write_pair(tmp_path, sweep, presence)
    df = brf.add_derived_features(brf.load_merged(a, b, strict_grid=False))
    z1 = brf.zscore_within_window(df, ("phase_std",))["z_phase_std"].to_numpy()

    scaled = df.copy()
    scaled["phase_std_rad"] = scaled["phase_std_rad"] * 100.0 + 7.0
    z2 = brf.zscore_within_window(scaled, ("phase_std",))["z_phase_std"].to_numpy()
    np.testing.assert_allclose(z1, z2, atol=1e-9)


# ── 4. selection, scoring and cross-validation ───────────────────────────────


def test_argmax_resolves_ties_to_the_first_column():
    """All-equal scores must resolve deterministically, not by array-order accident."""
    ws = _synthetic_window_set(n_windows=2, n_bins=4)
    sel = bbs.select_from_scores(np.zeros((ws.n_windows, ws.n_bins)), ws.valid)
    assert np.all(sel == 0)


def test_window_set_orders_bins_by_energy_then_bin(tmp_path):
    """Column 0 must be the highest-energy bin — that is what makes the tie-break correct."""
    sweep = _sweep_frame()
    presence = _presence_frame(sweep)
    a, b = _write_pair(tmp_path, sweep, presence)
    df = brf.add_derived_features(brf.load_merged(a, b, strict_grid=False))
    labels = pd.DataFrame({
        "k": range(6), "ref_bpm": [14.0] * 6, "n_finite_rr": [30] * 6,
        "spread_bpm": [1.0] * 6, "availability_ok": [True] * 6,
        "stationarity_ok": [True] * 6, "admitted": [True] * 6,
    })
    df = brf.zscore_within_window(
        brf.attach_labels(df, {sweep["capture_id"].iloc[0]: labels}), brf.FEATURE_NAMES
    )
    ws = bbs.build_window_set(df, brf.FEATURE_NAMES)
    for w in range(ws.n_windows):
        k = int(ws.k[w])
        got = df[df["k"] == k].set_index("bin")["energy"]
        ordered = [float(got.loc[b]) for b in ws.bin_id[w]]
        assert ordered == sorted(ordered, reverse=True)


def test_no_valid_bin_means_no_estimate():
    ws = _synthetic_window_set(n_windows=3, n_bins=4)
    valid = ws.valid.copy()
    valid[1, :] = False
    ws2 = bbs.WindowSet(**{**ws.__dict__, "valid": valid})
    sel = bbs.rule_medoid(ws2)
    assert sel[1] == -1
    scored = bbs.score_selection(ws2, sel)
    assert scored["n_reported"] == 2
    assert scored["coverage"] == pytest.approx(2 / 3, abs=1e-4)


def test_oracle_is_a_ceiling_no_rule_beats_it():
    ws = _synthetic_window_set()
    oracle = bbs.score_selection(ws, bbs.rule_oracle(ws))["mae_bpm"]
    vectors = bbs.sign_vectors(ws.n_features, l0_cap=3)
    sel_wv = bbs.selections_for_vectors(ws, vectors)
    err_wv = bbs.gather_errors(ws.err, sel_wv)
    assert bbs.in_sample_best(ws, vectors, err_wv)["mae_bpm"] >= oracle - 1e-9
    assert bbs.score_selection(ws, bbs.rule_medoid(ws))["mae_bpm"] >= oracle - 1e-9


def test_sign_vector_enumeration_is_exact():
    """The search space must be exactly reportable — it is the permutation null's basis."""
    v = bbs.sign_vectors(4, l0_cap=2)
    assert v.shape == (4 * 2 + 6 * 4, 4)                  # C(4,1)*2 + C(4,2)*4
    assert set(np.unique(v).tolist()) <= {-1.0, 0.0, 1.0}
    assert np.all(np.count_nonzero(v, axis=1) <= 2)
    assert len({tuple(r) for r in v.tolist()}) == v.shape[0]


def test_search_recovers_a_planted_signal():
    """Sanity in the other direction: when a signal IS present, the search must find it."""
    ws = _synthetic_window_set(signal=True)
    vectors = bbs.sign_vectors(ws.n_features, l0_cap=1)
    sel_wv = bbs.selections_for_vectors(ws, vectors)
    best = bbs.in_sample_best(ws, vectors, bbs.gather_errors(ws.err, sel_wv))
    assert best["best_vector"][0] == 1.0                  # +1 on feature 0
    loso = bbs.loso_search(ws, vectors, sel_wv)
    assert loso["vector_stable_across_folds"]
    assert loso["oof_mae_bpm_window_weighted"] < bbs.score_selection(
        ws, bbs.rule_random(ws, np.random.default_rng(0))
    )["mae_bpm"]


def test_shuffle_canary_out_of_fold_matches_the_null():
    """SHUFFLE CANARY: with the association destroyed, honest CV must find nothing.

    The out-of-fold MAE under shuffled errors has to land at the random-pick null. If it
    lands better, the fold grouping is leaking and every out-of-fold number is inflated.
    """
    ws = _synthetic_window_set(n_windows=60, n_bins=6, n_features=4, signal=True)
    rng = np.random.default_rng(11)
    vectors = bbs.sign_vectors(ws.n_features, l0_cap=2)
    sel_wv = bbs.selections_for_vectors(ws, vectors)

    oofs, nulls = [], []
    for _ in range(60):
        err_perm = bbs.permute_errors_within_window(ws.err, ws.valid, rng)
        shuffled = bbs.WindowSet(**{**ws.__dict__, "err": err_perm})
        oofs.append(bbs.loso_search(shuffled, vectors, sel_wv)["oof_mae_bpm_window_weighted"])
        nulls.append(float(np.mean(err_perm[ws.valid])))

    assert np.mean(oofs) == pytest.approx(np.mean(nulls), rel=0.10)


def test_in_sample_beats_out_of_fold_on_pure_noise():
    """The optimism this whole design exists to measure must be visible in the harness."""
    ws = _synthetic_window_set(n_windows=48, n_bins=6, n_features=5, signal=False)
    vectors = bbs.sign_vectors(ws.n_features, l0_cap=3)
    sel_wv = bbs.selections_for_vectors(ws, vectors)
    err_wv = bbs.gather_errors(ws.err, sel_wv)
    in_sample = bbs.in_sample_best(ws, vectors, err_wv)["mae_bpm"]
    oof = bbs.loso_search(ws, vectors, sel_wv, err_wv=err_wv)["oof_mae_bpm_window_weighted"]
    assert in_sample < oof


def test_permutation_within_window_preserves_structure():
    ws = _synthetic_window_set(n_windows=10, n_bins=5)
    rng = np.random.default_rng(2)
    perm = bbs.permute_errors_within_window(ws.err, ws.valid, rng)
    for w in range(ws.n_windows):
        np.testing.assert_allclose(np.sort(perm[w]), np.sort(ws.err[w]))


def test_folds_are_by_subject_not_capture():
    """Held-out windows must never appear in their own fold's training set."""
    ws = _synthetic_window_set(n_windows=40)
    vectors = bbs.sign_vectors(ws.n_features, l0_cap=1)
    sel_wv = bbs.selections_for_vectors(ws, vectors)
    res = bbs.loso_search(ws, vectors, sel_wv)
    assert res["n_folds"] == len(set(ws.subject.tolist()))
    for f in res["folds"]:
        held = int((ws.subject == f["held_out_subject"]).sum())
        assert f["n_test_windows"] == held
        assert f["n_train_windows"] == ws.n_windows - held


def test_oracle_normalised_skill_is_difficulty_free():
    """Scaling every rule's error by a difficulty factor must leave skill unchanged."""
    a = bbs.oracle_normalised_skill(2.5, 3.5, 1.5)
    b = bbs.oracle_normalised_skill(5.0, 7.0, 3.0)
    assert a == pytest.approx(b)


def test_admitted_gate_is_stricter_than_finite(tmp_path):
    """The gate must only ever remove windows, never add or alter them."""
    sweep = _sweep_frame()
    presence = _presence_frame(sweep)
    a, b = _write_pair(tmp_path, sweep, presence)
    df = brf.add_derived_features(brf.load_merged(a, b, strict_grid=False))
    labels = pd.DataFrame({
        "k": range(6), "ref_bpm": [14.0] * 6, "n_finite_rr": [30] * 6,
        "spread_bpm": [1.0, 1.0, 9.0, 1.0, 9.0, 1.0],
        "availability_ok": [True] * 6,
        "stationarity_ok": [True, True, False, True, False, True],
        "admitted": [True, True, False, True, False, True],
    })
    df = brf.attach_labels(df, {sweep["capture_id"].iloc[0]: labels})
    df = brf.zscore_within_window(df, brf.FEATURE_NAMES)
    strict = bbs.build_window_set(df, brf.FEATURE_NAMES, gate="admitted")
    loose = bbs.build_window_set(df, brf.FEATURE_NAMES, gate="finite")
    assert strict.labelled.sum() == 4
    assert loose.labelled.sum() == 6
    assert np.all(loose.labelled[strict.labelled])
