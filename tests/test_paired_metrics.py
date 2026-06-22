"""Tests for paired_metrics and coverage_table in src/compare.py."""

import math

import pytest

from src.compare import coverage_table, paired_metrics


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def make_conditions(rows):
    """Build a ConditionResults dict from (center_frame, radar_hr, masimo_pr, ahet_verified) tuples.

    The stored "error" field is always NaN so that tests exercise the
    recomputation path inside paired_metrics.
    """
    result = {}
    for cf, rhr, mpr, ahet in rows:
        result[cf] = {
            "radar_hr": rhr,
            "masimo_pr": mpr,
            "error": float("nan"),  # paired_metrics must recompute this
            "ahet_verified": ahet,
        }
    return result


class _DupKeyMap(dict):
    """Dict subclass that exposes duplicate keys via keys() and items().

    Used to test the duplicate center_frame key validation path, which is
    otherwise unreachable through a plain Python dict (dicts enforce uniqueness).
    """

    def keys(self):
        return [900, 900]

    def items(self):
        entry = {
            "radar_hr": 72.0,
            "masimo_pr": 70.0,
            "error": 2.0,
            "ahet_verified": True,
        }
        return [(900, entry), (900, entry)]


# ---------------------------------------------------------------------------
# Correctness
# ---------------------------------------------------------------------------

def test_paired_metrics_basic_three_conditions():
    conds = {
        "20s": make_conditions([
            (900,  72.0, 70.0, True), (1000, 73.0, 71.0, True), (1100, 74.0, 72.0, True),
        ]),
        "25s": make_conditions([
            (900,  71.0, 70.0, True), (1000, 72.0, 71.0, True), (1100, 73.0, 72.0, True),
        ]),
        "30s": make_conditions([
            (900,  70.0, 70.0, True), (1000, 71.0, 71.0, True), (1100, 72.0, 72.0, True),
        ]),
    }
    result = paired_metrics(conds, "20s")

    assert len(result["all_centers"]) == 3
    assert len(result["per_condition"]) == 3
    assert result["intersection"]["n_centers"] == 3
    assert set(result["intersection"]["pairwise"].keys()) == {
        "20s_vs_25s", "20s_vs_30s", "25s_vs_30s"
    }
    for pw in result["intersection"]["pairwise"].values():
        assert pw["ahet_transitions"]["both_pass"] == 3


def test_paired_metrics_mae_rmse_bias_values():
    # Condition "A" errors: [+2, -4]
    # MAE = mean(2, 4) = 3.0
    # RMSE = sqrt(mean(4, 16)) = sqrt(10)
    # bias = mean(2, -4) = -1.0
    conds = {
        "A": make_conditions([(1000, 72.0, 70.0, True), (1100, 66.0, 70.0, True)]),
        "B": make_conditions([(1000, 71.0, 70.0, True), (1100, 67.0, 70.0, True)]),
    }
    result = paired_metrics(conds, "A")

    pc_a = result["per_condition"]["A"]
    assert abs(pc_a["mae"] - 3.0) < 1e-10
    assert abs(pc_a["rmse"] - math.sqrt(10)) < 1e-10
    assert abs(pc_a["bias"] - (-1.0)) < 1e-10


def test_paired_metrics_intersection_excludes_nan_centers():
    nan = float("nan")
    conds = {
        "20s": make_conditions([
            (900,  72.0, 70.0, True), (1000, 73.0, 71.0, True), (1100, 74.0, 72.0, True),
        ]),
        "25s": make_conditions([
            (900,  nan,  70.0, False), (1000, 72.0, 71.0, True), (1100, 73.0, 72.0, True),
        ]),
        "30s": make_conditions([
            (900,  71.0, 70.0, True), (1000, 71.0, 71.0, True), (1100, 72.0, 72.0, True),
        ]),
    }
    result = paired_metrics(conds, "20s")

    # Center 900 has NaN radar_hr in "25s" → excluded from intersection
    assert result["intersection"]["n_centers"] == 2
    pc_25s = result["per_condition"]["25s"]
    assert pc_25s["n_finite"] == 2
    assert pc_25s["n_nan_radar"] == 1


def test_paired_metrics_nan_reference_excluded():
    nan = float("nan")
    conds = {
        "20s": make_conditions([
            (900,  72.0, nan,  True), (1000, 73.0, 71.0, True), (1100, 74.0, 72.0, True),
        ]),
        "25s": make_conditions([
            (900,  71.0, nan,  True), (1000, 72.0, 71.0, True), (1100, 73.0, 72.0, True),
        ]),
    }
    result = paired_metrics(conds, "20s")

    # Center 900 has NaN masimo_pr in both conditions → excluded from intersection
    assert result["intersection"]["n_centers"] == 2
    assert result["per_condition"]["20s"]["n_nan_ref"] == 1
    assert result["per_condition"]["25s"]["n_nan_ref"] == 1
    assert result["per_condition"]["20s"]["n_finite"] == 2
    assert result["per_condition"]["25s"]["n_finite"] == 2


def test_paired_metrics_error_recomputed_from_components():
    # Stored "error" is NaN while radar_hr and masimo_pr are both finite.
    # paired_metrics must recompute error = radar_hr - masimo_pr.
    cond_dict = {
        1000: {"radar_hr": 72.0, "masimo_pr": 70.0, "error": float("nan"), "ahet_verified": True},
        1100: {"radar_hr": 68.0, "masimo_pr": 70.0, "error": float("nan"), "ahet_verified": True},
    }
    conds = {"A": cond_dict}
    result = paired_metrics(conds, "A")

    pc = result["per_condition"]["A"]
    # Recomputed errors: [72-70=+2, 68-70=-2]; MAE=2, RMSE=2, bias=0
    assert abs(pc["mae"] - 2.0) < 1e-10
    assert abs(pc["rmse"] - 2.0) < 1e-10
    assert abs(pc["bias"] - 0.0) < 1e-10
    assert pc["n_finite"] == 2


def test_paired_metrics_ahet_transitions():
    # Pairs: (T,T), (T,F), (F,T), (F,F) → one of each transition type
    conds = {
        "A": make_conditions([
            (900,  72.0, 70.0, True),   # A=T, B=T → both_pass
            (1000, 73.0, 71.0, True),   # A=T, B=F → only_a
            (1100, 74.0, 72.0, False),  # A=F, B=T → only_b
            (1200, 75.0, 73.0, False),  # A=F, B=F → neither
        ]),
        "B": make_conditions([
            (900,  71.0, 70.0, True),
            (1000, 72.0, 71.0, False),
            (1100, 73.0, 72.0, True),
            (1200, 74.0, 73.0, False),
        ]),
    }
    result = paired_metrics(conds, "A")

    t = result["intersection"]["pairwise"]["A_vs_B"]["ahet_transitions"]
    assert t["both_pass"] == 1
    assert t["only_a_passes"] == 1
    assert t["only_b_passes"] == 1
    assert t["neither_passes"] == 1


def test_paired_metrics_pairwise_error_diff():
    # A errors: +3, -1, +2
    # B errors: +1, +2, -1
    # error_diffs (A-B): +2, -3, +3  → mean = 2/3
    # abs_diffs |A|-|B|:  +2, -1, +1 → mean = 2/3
    conds = {
        "A": make_conditions([
            (900,  73.0, 70.0, True),   # error +3
            (1000, 70.0, 71.0, True),   # error -1
            (1100, 72.0, 70.0, True),   # error +2
        ]),
        "B": make_conditions([
            (900,  71.0, 70.0, True),   # error +1
            (1000, 73.0, 71.0, True),   # error +2
            (1100, 69.0, 70.0, True),   # error -1
        ]),
    }
    result = paired_metrics(conds, "A")
    pw = result["intersection"]["pairwise"]["A_vs_B"]

    assert abs(pw["mean_error_diff"] - 2 / 3) < 1e-10
    assert abs(pw["mean_abs_error_diff"] - 2 / 3) < 1e-10


def test_paired_metrics_all_nan_condition():
    nan = float("nan")
    conds = {
        "A": make_conditions([(900, nan, 70.0, False), (1000, nan, 71.0, False)]),
        "B": make_conditions([(900, 72.0, 70.0, True),  (1000, 73.0, 71.0, True)]),
    }
    result = paired_metrics(conds, "A")

    pc_a = result["per_condition"]["A"]
    assert pc_a["n_finite"] == 0
    assert pc_a["n_nan_radar"] == 2
    assert math.isnan(pc_a["mae"])
    assert math.isnan(pc_a["rmse"])
    assert math.isnan(pc_a["bias"])
    # Intersection excludes all centers because A has no finite values
    assert result["intersection"]["n_centers"] == 0


def test_coverage_table_returns_string():
    conds = {
        "20s": make_conditions([(900, 72.0, 70.0, True), (1000, 73.0, 71.0, True)]),
        "25s": make_conditions([(900, 71.0, 70.0, True), (1000, 72.0, 71.0, True)]),
    }
    result = paired_metrics(conds, "20s")
    table = coverage_table(result)

    assert isinstance(table, str)
    assert len(table) > 0
    assert "20s" in table
    assert "25s" in table
    assert "n_total" in table
    assert "MAE" in table


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_paired_metrics_raises_on_empty_intersection():
    # Disjoint center_frame keys → key intersection is empty → ValueError
    conds = {
        "20s": make_conditions([(900, 72.0, 70.0, True)]),
        "25s": make_conditions([(1000, 71.0, 70.0, True)]),
    }
    with pytest.raises(ValueError):
        paired_metrics(conds, "20s")


def test_paired_metrics_raises_on_non_integer_center_key():
    conds = {
        "20s": {
            900.0: {"radar_hr": 72.0, "masimo_pr": 70.0, "error": 2.0, "ahet_verified": True},
        },
    }
    with pytest.raises(ValueError):
        paired_metrics(conds, "20s")


def test_paired_metrics_raises_on_duplicate_center_key():
    # _DupKeyMap overrides keys() and items() to expose duplicate key 900.
    # This is the only testable route since plain Python dicts enforce uniqueness.
    conds = {
        "20s": _DupKeyMap({
            900: {"radar_hr": 72.0, "masimo_pr": 70.0, "error": 2.0, "ahet_verified": True},
        }),
        "25s": make_conditions([(900, 71.0, 70.0, True)]),
    }
    with pytest.raises(ValueError):
        paired_metrics(conds, "20s")


def test_paired_metrics_raises_on_missing_field():
    conds = {
        "20s": {
            900: {"radar_hr": 72.0, "masimo_pr": 70.0, "error": 2.0},  # missing ahet_verified
        },
    }
    with pytest.raises(ValueError):
        paired_metrics(conds, "20s")


def test_paired_metrics_raises_on_unknown_reference_condition():
    conds = {"20s": make_conditions([(900, 72.0, 70.0, True)])}
    with pytest.raises(ValueError):
        paired_metrics(conds, "30s")


def test_paired_metrics_raises_on_empty_conditions():
    with pytest.raises(ValueError):
        paired_metrics({}, "20s")


# ---------------------------------------------------------------------------
# Error vector tests
# ---------------------------------------------------------------------------

def test_paired_metrics_error_vector_length():
    conds = {
        "20s": make_conditions([
            (900,  72.0, 70.0, True), (1000, 73.0, 71.0, True),
            (1100, 74.0, 72.0, True), (1200, 75.0, 73.0, True),
        ]),
        "25s": make_conditions([
            (900,  71.0, 70.0, True), (1000, 72.0, 71.0, True),
            (1100, 73.0, 72.0, True), (1200, 74.0, 73.0, True),
        ]),
    }
    result = paired_metrics(conds, "20s")
    intersection = result["intersection"]

    assert len(intersection["per_condition"]["20s"]["error_vector"]) == 4
    assert len(intersection["per_condition"]["25s"]["error_vector"]) == 4


def test_paired_metrics_error_vector_values():
    # "20s" errors (radar_hr - masimo_pr): +2, -1, +3  (centers 900, 1000, 1100)
    # "25s" errors:                         +1, -2, +4
    conds = {
        "20s": make_conditions([
            (900,  72.0, 70.0, True),   # error +2
            (1000, 70.0, 71.0, True),   # error -1
            (1100, 75.0, 72.0, True),   # error +3
        ]),
        "25s": make_conditions([
            (900,  71.0, 70.0, True),   # error +1
            (1000, 69.0, 71.0, True),   # error -2
            (1100, 76.0, 72.0, True),   # error +4
        ]),
    }
    result = paired_metrics(conds, "20s")
    ev_20 = result["intersection"]["per_condition"]["20s"]["error_vector"]
    ev_25 = result["intersection"]["per_condition"]["25s"]["error_vector"]

    # Values in ascending center_frame order (900, 1000, 1100)
    expected_20 = [2.0, -1.0, 3.0]
    expected_25 = [1.0, -2.0, 4.0]
    for got, exp in zip(ev_20, expected_20):
        assert abs(got - exp) < 1e-10, f"20s error_vector mismatch: {got} != {exp}"
    for got, exp in zip(ev_25, expected_25):
        assert abs(got - exp) < 1e-10, f"25s error_vector mismatch: {got} != {exp}"

    # Length matches n_centers
    assert len(ev_20) == result["intersection"]["n_centers"]
    assert len(ev_25) == result["intersection"]["n_centers"]


def test_paired_metrics_error_diff_vector():
    # "20s" errors: +3, -1, +2 (centers 900, 1000, 1100)
    # "25s" errors: +1, +2, -1
    # error_diff (20s - 25s): +2, -3, +3
    conds = {
        "20s": make_conditions([
            (900,  73.0, 70.0, True),   # error +3
            (1000, 70.0, 71.0, True),   # error -1
            (1100, 72.0, 70.0, True),   # error +2
        ]),
        "25s": make_conditions([
            (900,  71.0, 70.0, True),   # error +1
            (1000, 73.0, 71.0, True),   # error +2
            (1100, 69.0, 70.0, True),   # error -1
        ]),
    }
    result = paired_metrics(conds, "20s")
    pw = result["intersection"]["pairwise"]["20s_vs_25s"]
    edv = pw["error_diff_vector"]

    expected = [2.0, -3.0, 3.0]
    assert len(edv) == result["intersection"]["n_centers"]
    for got, exp in zip(edv, expected):
        assert abs(got - exp) < 1e-10, f"error_diff_vector mismatch: {got} != {exp}"
