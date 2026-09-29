from __future__ import annotations

import json

import numpy as np
import pytest

from src.agreement import AgreementContractError, _failure_fraction_allows_ci, arm_loa


def _rows(groups: dict[str, list[float]], *, arm: str = "natural") -> list[dict]:
    rows = []
    for subject, differences in groups.items():
        for window_index, difference in enumerate(differences):
            rows.append(
                {
                    "arm": arm,
                    "subject_id": subject,
                    "session_id": f"{subject}_{arm}",
                    "window_index": window_index,
                    "radar_bpm": 70.0 + difference,
                    "reference_bpm": 70.0,
                }
            )
    return rows


def _analyse(groups: dict[str, list[float]], **kwargs) -> dict:
    return arm_loa(_rows(groups), allowed_subject_ids=set(groups), **kwargs)


def test_unbalanced_anova_matches_hand_derived_three_subject_example() -> None:
    # A=[1,3], B=[4,4,8], C=[-1].  The fractions below are derived directly
    # from analysis_prespec section 1 and exercise unequal group sizes.
    groups = {"A": [1.0, 3.0], "B": [4.0, 4.0, 8.0], "C": [-1.0]}
    result = _analyse(
        groups,
        bootstrap_replicates=200,
    )
    assert result["point_estimable"] is True
    assert result["bias_bpm"] == pytest.approx(19.0 / 9.0)
    assert result["ssw"] == pytest.approx(38.0 / 3.0)
    assert result["msw"] == pytest.approx(38.0 / 9.0)
    assert result["ssb"] == pytest.approx(205.0 / 6.0)
    assert result["msb"] == pytest.approx(205.0 / 12.0)
    assert result["n0"] == pytest.approx(11.0 / 6.0)
    assert result["variance_between_bpm2"] == pytest.approx(463.0 / 66.0)
    width = 1.96 * np.sqrt(38.0 / 9.0 + 463.0 / 66.0)
    assert result["loa_low_bpm"] == pytest.approx(19.0 / 9.0 - width)
    assert result["loa_high_bpm"] == pytest.approx(19.0 / 9.0 + width)


def test_bootstrap_is_subject_clustered_and_deterministic() -> None:
    rows = _rows(
        {
            "A": [1.0, 2.0],
            "B": [-1.0, 0.0, 1.0],
            "C": [3.0, 5.0],
            "D": [-2.0, -1.0, 0.0],
        }
    )
    first = arm_loa(
        rows, allowed_subject_ids={"A", "B", "C", "D"},
        bootstrap_replicates=500, seed=20260725,
    )
    second = arm_loa(
        rows, allowed_subject_ids={"A", "B", "C", "D"},
        bootstrap_replicates=500, seed=20260725,
    )
    assert first["loa_low_ci95_bpm"] == second["loa_low_ci95_bpm"]
    assert first["loa_high_ci95_bpm"] == second["loa_high_ci95_bpm"]
    assert first["bootstrap_replicates_valid"] + first["bootstrap_replicates_failed"] == 500
    assert first["bootstrap_percentile_method"] == "linear"
    assert first["bootstrap_duplicate_cluster_replicates"] > 0


def test_single_subject_is_descriptive_only() -> None:
    result = _analyse({"A": [1.0, 2.0, 3.0]}, bootstrap_replicates=10)
    assert result["point_estimable"] is False
    assert result["descriptive_only"] is True
    assert result["loa_low_bpm"] is None
    assert result["loa_low_ci95_bpm"] is None
    assert result["limitation_reason"] == "fewer_than_two_subjects"


def test_no_within_subject_replication_is_descriptive_only() -> None:
    result = _analyse({"A": [1.0], "B": [2.0]}, bootstrap_replicates=10)
    assert result["point_estimable"] is False
    assert result["loa_high_bpm"] is None
    assert result["limitation_reason"] == "no_within_subject_replication"
    sensitivity = result["diagnostics"]["regression_loa_sensitivity"]
    assert sensitivity["fit_available"] is False
    assert sensitivity["fit_failure_reason"] == "no_within_subject_replication"


def test_bootstrap_failure_above_five_percent_suppresses_population_reporting() -> None:
    # With two subjects, half of resamples contain only one original subject.
    result = _analyse(
        {"A": [1.0, 2.0], "B": [3.0, 4.0]}, bootstrap_replicates=500
    )
    assert result["point_estimable"] is True
    assert result["bootstrap_failure_fraction"] > 0.05
    assert result["report_population_loa"] is False
    assert result["descriptive_only"] is True
    assert result["loa_low_ci95_bpm"] is None


def test_negative_between_subject_component_is_truncated() -> None:
    result = _analyse(
        {"A": [-10.0, 10.0], "B": [-10.0, 10.0], "C": [-10.0, 10.0]},
        bootstrap_replicates=50,
    )
    assert result["variance_between_untruncated_bpm2"] < 0.0
    assert result["variance_between_bpm2"] == 0.0
    assert result["variance_between_truncated"] is True


def test_five_percent_bootstrap_boundary_is_inclusive() -> None:
    assert _failure_fraction_allows_ci(5, 100) is True
    assert _failure_fraction_allows_ci(6, 100) is False


def test_regression_sensitivity_fits_random_intercept_population_limits() -> None:
    rows = []
    subject_offsets = {"A": -2.0, "B": -1.0, "C": 0.0, "D": 1.0, "E": 2.0}
    residual_patterns = {
        "A": [-0.5, 0.2, 0.1], "B": [0.3, -0.4, 0.2],
        "C": [-0.2, 0.4, -0.1], "D": [0.5, -0.1, -0.2],
        "E": [-0.3, 0.1, 0.4],
    }
    for subject, offset in subject_offsets.items():
        for window_index, (pair_mean, residual) in enumerate(
            zip([60.0, 75.0, 90.0], residual_patterns[subject], strict=True)
        ):
            difference = 1.0 + 0.12 * (pair_mean - 75.0) + offset + residual
            rows.append(
                {
                    "arm": "natural", "subject_id": subject,
                    "session_id": f"{subject}_natural", "window_index": window_index,
                    "radar_bpm": pair_mean + difference / 2.0,
                    "reference_bpm": pair_mean - difference / 2.0,
                }
            )
    result = arm_loa(
        rows, allowed_subject_ids=set(subject_offsets), bootstrap_replicates=100,
    )
    sensitivity = result["diagnostics"]["regression_loa_sensitivity"]
    assert sensitivity["fit_available"] is True
    assert sensitivity["difference_vs_mean_slope"] == pytest.approx(0.12, abs=0.02)
    assert sensitivity["variance_between_bpm2"] >= 0.0
    assert sensitivity["variance_residual_bpm2"] > 0.0
    assert len(sensitivity["limits_by_pair_mean"]) == 5
    assert sensitivity["bootstrap_ci_available"] is True
    assert all(
        "loa_low_ci95_bpm" in row and "loa_high_ci95_bpm" in row
        for row in sensitivity["limits_by_pair_mean"]
    )
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda rows: rows[0].update(arm="paced"), "exactly one arm"),
        (lambda rows: rows[0].update(radar_bpm=np.nan), "finite number"),
        (lambda rows: rows[0].update(subject_id=""), "non-empty string"),
        (lambda rows: rows[1].update(window_index=0), "duplicate agreement row identity"),
    ],
)
def test_contract_rejects_ambiguous_or_nonfinite_rows(mutation, message: str) -> None:
    rows = _rows({"A": [1.0, 2.0], "B": [3.0, 4.0]})
    mutation(rows)
    with pytest.raises(AgreementContractError, match=message):
        arm_loa(rows, allowed_subject_ids={"A", "B"}, bootstrap_replicates=10)


def test_contract_rejects_unknown_arm_subject_and_multiple_sessions() -> None:
    rows = _rows({"A": [1.0, 2.0], "B": [3.0, 4.0]})
    rows[0]["arm"] = rows[1]["arm"] = rows[2]["arm"] = rows[3]["arm"] = "other"
    with pytest.raises(AgreementContractError, match="arm must be one of"):
        arm_loa(rows, allowed_subject_ids={"A", "B"}, bootstrap_replicates=10)

    rows = _rows({"A": [1.0, 2.0], "B": [3.0, 4.0]})
    rows[0]["subject_id"] = "unknown"
    with pytest.raises(AgreementContractError, match="allowed_subject_ids"):
        arm_loa(rows, allowed_subject_ids={"A", "B"}, bootstrap_replicates=10)

    rows = _rows({"A": [1.0, 2.0], "B": [3.0, 4.0]})
    rows[1]["session_id"] = "A_natural_repeat"
    with pytest.raises(AgreementContractError, match="multiple sessions"):
        arm_loa(rows, allowed_subject_ids={"A", "B"}, bootstrap_replicates=10)
