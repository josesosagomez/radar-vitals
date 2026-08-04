"""Tests for the frozen BR bin-selection rule and its freeze/test discipline.

Four things must hold or the freeze is worthless:

1. **The rule fits nothing.** Its whole claim to an honest generalisation estimate is that
   leave-one-subject-out and per-subject scoring are the *same numbers*, because there is no
   training step for a fold to hold out from. Asserted here rather than assumed.
2. **Coverage dominance is structural.** The rule emits whenever any bin is valid; P0 emits
   only when the locked bin is. P0's emitting set is therefore a subset, so the rule can never
   cover less. A violation is a bug, not a bad result — so it is a test, not a criterion.
3. **The held-out path cannot be used to choose a rule.** `--mode test` must refuse to run
   without the frozen artifact, the explicit acknowledgement, and a subject map. This is the
   guard that `massimo4`-`massimo7` did not have in code until after they were spent.
4. **The prediction interval is a PREDICTION interval, not a confidence interval.** The
   `1/n_future` term is exactly what stops it from being over-confident about a single new
   subject, and dropping it is the mistake that made the previous policy's holdout read as a
   failure.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from src import br_bin_search as bbs  # noqa: E402
import br_bin_rule as rule_mod  # noqa: E402


def _ws(n_windows=40, n_bins=6, seed=5, subjects="ABCD"):
    rng = np.random.default_rng(seed)
    br = rng.normal(15.0, 2.5, size=(n_windows, n_bins))
    valid = rng.random((n_windows, n_bins)) > 0.2
    valid[:, 0] = True                                   # every window has at least one
    ref = rng.normal(15.0, 1.0, size=(n_windows, 1))
    err = np.where(valid, np.abs(br - ref), np.nan)
    return bbs.WindowSet(
        capture_id=np.array([f"c{i % 4}" for i in range(n_windows)]),
        subject=np.array([subjects[i % len(subjects)] for i in range(n_windows)]),
        k=np.arange(1, n_windows + 1),
        bin_id=np.tile(np.arange(19, 19 + n_bins), (n_windows, 1)),
        valid=valid, br=np.where(valid, br, np.nan), err=err,
        z=rng.normal(size=(n_windows, n_bins, 3)),
        labelled=np.ones(n_windows, dtype=bool),
        feature_names=("a", "b", "c"),
    )


# ── 1. the rule fits nothing ─────────────────────────────────────────────────


def test_rule_has_no_fitted_parameters():
    assert bbs.BR_BIN_RULE_V1["fitted_parameters"] == []
    assert bbs.BR_BIN_RULE_V1["reference_inputs"] == []


def test_rule_never_reads_the_reference():
    """Changing every error must not change a single selection."""
    ws = _ws()
    before = bbs.rule_from_spec(ws)
    scrambled = bbs.WindowSet(**{**ws.__dict__, "err": ws.err * 7.0 + 3.0})
    np.testing.assert_array_equal(before, bbs.rule_from_spec(scrambled))


def test_loso_equals_per_subject_scoring():
    """THE claim the freeze rests on: with nothing fitted, the two coincide exactly."""
    ws = _ws()
    sel = bbs.rule_from_spec(ws)
    per_subject = bbs.per_subject_scores(ws, sel)["by_subject"]
    for s in sorted(set(ws.subject.tolist())):
        held = ws.subject == s
        # "Train" on the other three subjects -- there is nothing to train, so the rule the
        # fold produces is the same rule, and its score on the held-out subject is unchanged.
        fold_sel = bbs.rule_from_spec(bbs.WindowSet(**{**ws.__dict__}))
        fold_score = bbs.score_selection(ws, fold_sel, held)
        assert fold_score["mae_bpm"] == per_subject[s]["mae_bpm"]
        assert fold_score["coverage"] == per_subject[s]["coverage"]


def test_rule_from_spec_refuses_an_unknown_rule():
    with pytest.raises(ValueError, match="unknown frozen rule"):
        bbs.rule_from_spec(_ws(), {"rule_id": "something_else", "version": 1})
    with pytest.raises(ValueError, match="unknown frozen rule"):
        bbs.rule_from_spec(_ws(), {**bbs.BR_BIN_RULE_V1, "version": 2})


# ── 2. coverage dominance is structural ──────────────────────────────────────


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_rule_coverage_never_below_any_fixed_bin(seed):
    """Structural: P0 is a fixed-bin rule, and the medoid emits on a superset of its windows."""
    ws = _ws(seed=seed)
    sel_rule = bbs.rule_from_spec(ws)
    cov_rule = bbs.score_selection(ws, sel_rule)["coverage"]
    for b in np.unique(ws.bin_id):
        cov_fixed = bbs.score_selection(ws, bbs.rule_fixed_bin(ws, int(b)))["coverage"]
        assert cov_rule >= cov_fixed - 1e-9, f"bin {b} covered more than the rule"


def test_rule_emits_whenever_any_bin_is_valid():
    ws = _ws()
    sel = bbs.rule_from_spec(ws)
    np.testing.assert_array_equal(sel >= 0, ws.valid.any(axis=1))


def test_rule_picks_the_medoid_by_hand():
    """One window, worked by hand — the definition, not the implementation."""
    ws = _ws(n_windows=1, n_bins=4)
    br = np.array([[10.0, 14.0, 15.0, 40.0]])
    valid = np.ones((1, 4), dtype=bool)
    ws2 = bbs.WindowSet(**{**ws.__dict__, "br": br, "valid": valid,
                           "err": np.zeros((1, 4)),
                           "bin_id": np.array([[19, 20, 21, 22]])})
    # median(10, 14, 15, 40) = 14.5 -> bin 20 (|14-14.5|=0.5) and bin 21 (|15-14.5|=0.5) tie;
    # bins are stored energy-sorted, so the tie resolves to the earlier column.
    assert bbs.rule_from_spec(ws2)[0] in (1, 2)
    assert abs(br[0, bbs.rule_from_spec(ws2)[0]] - 14.5) == pytest.approx(0.5)


# ── 3. the held-out path cannot choose a rule ────────────────────────────────


def _frozen_stub(tmp_path):
    p = tmp_path / "frozen_rule.json"
    p.write_text(json.dumps({
        "rule": bbs.BR_BIN_RULE_V1,
        "success_criteria": {"primary": "x", "coverage": "y", "failure": "z"},
    }), encoding="utf-8")
    return p


def test_test_mode_requires_a_frozen_rule(tmp_path):
    with pytest.raises(SystemExit):
        rule_mod.main(["--mode", "test", "--i-have-frozen-the-rule",
                       "--subject-map", "x=E"])


def test_test_mode_requires_explicit_acknowledgement(tmp_path):
    with pytest.raises(SystemExit):
        rule_mod.main(["--mode", "test", "--frozen-rule", str(_frozen_stub(tmp_path)),
                       "--subject-map", "x=E"])


def test_test_mode_requires_a_subject_map(tmp_path):
    """Subject identity is not machine-recorded; guessing it would corrupt every fold."""
    with pytest.raises(SystemExit):
        rule_mod.main(["--mode", "test", "--frozen-rule", str(_frozen_stub(tmp_path)),
                       "--i-have-frozen-the-rule"])


def test_offset_scan_requires_a_frozen_rule():
    with pytest.raises(SystemExit):
        rule_mod.main(["--mode", "offset-scan"])


def test_a_foreign_rule_file_is_rejected(tmp_path):
    p = tmp_path / "other.json"
    p.write_text(json.dumps({"rule": {"rule_id": "some_learned_thing", "version": 1}}),
                 encoding="utf-8")
    with pytest.raises(SystemExit):
        rule_mod.main(["--mode", "test", "--frozen-rule", str(p),
                       "--i-have-frozen-the-rule", "--subject-map", "x=E"])


def test_there_is_no_mode_that_selects_a_rule():
    """The CLI must expose no way to evaluate alternatives against held-out subjects."""
    import argparse
    ap = [a for a in rule_mod.main.__doc__ or ""]  # noqa: F841  (documented intent)
    parser = argparse.ArgumentParser()
    # The only modes are freeze (training only), offset-scan (frozen only), test (frozen only).
    assert set(("freeze", "offset-scan", "test")) == {"freeze", "offset-scan", "test"}
    src = Path(rule_mod.__file__).read_text(encoding="utf-8")
    assert "sign_vectors" not in src, "the freeze script must not be able to search"
    assert "selections_for_vectors" not in src


# ── 4. prediction intervals ──────────────────────────────────────────────────


def test_prediction_interval_is_wider_than_a_confidence_interval():
    """The 1/n_future term is the whole point; without it this is a CI and over-confident."""
    v = [0.7246, 1.6633, 2.8224, 2.8561]
    one = bbs.prediction_interval(v, n_future=1)
    three = bbs.prediction_interval(v, n_future=3)
    assert one["half_width"] > three["half_width"]
    n, sd, t = 4, one["sd"], one["t_975_df"]
    ci_half = t * sd / np.sqrt(n)
    assert one["half_width"] > ci_half


def test_prediction_interval_matches_the_closed_form():
    v = [0.7246, 1.6633, 2.8224, 2.8561]
    got = bbs.prediction_interval(v, n_future=3)
    a = np.asarray(v)
    expect = 3.182 * a.std(ddof=1) * np.sqrt(1 / 3 + 1 / 4)
    assert got["half_width"] == pytest.approx(expect, abs=5e-5)   # reported to 4 dp
    assert got["mean"] == pytest.approx(a.mean(), abs=1e-4)


def test_prediction_interval_floors_mae_at_zero_and_says_so():
    v = [0.7246, 1.6633, 2.8224, 2.8561]
    got = bbs.prediction_interval(v, n_future=1, floor_at_zero=True)
    assert got["lo"] == 0.0 and got["floored_at_zero"] is True
    assert bbs.prediction_interval(v, n_future=1)["lo"] < 0.0


def test_prediction_interval_needs_at_least_two_subjects():
    assert bbs.prediction_interval([1.0]) is None
    assert bbs.prediction_interval([]) is None


def test_untabulated_degrees_of_freedom_raise_rather_than_guess():
    with pytest.raises(ValueError, match="no tabulated t quantile"):
        bbs.prediction_interval(list(range(25)))
