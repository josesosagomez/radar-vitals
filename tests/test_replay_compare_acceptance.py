"""Pure validation tests for the native replay acceptance driver."""

from __future__ import annotations

import copy
import math

import pytest

from scripts.run_replay_compare_acceptance import (
    memory_observation,
    process_performance_errors,
)


SPANS = (
    "parent_copy_elapsed_s",
    "parent_send_elapsed_s",
    "parent_wait_receive_elapsed_s",
    "child_materialization_elapsed_s",
    "child_callback_elapsed_s",
    "process_round_trip_elapsed_s",
)


def _analysis_row(job_id: str, sequence: int) -> dict:
    row = {
        "kind": "analysis",
        "job_id": job_id,
        "process_sequence": sequence,
        **{name: 0.01 for name in SPANS},
        "analysis_elapsed_s": 0.05,
        "process_control_bytes": 65_536,
        "process_result_bytes": 16_777_216,
        "fatal_backend_error": None,
    }
    row["process_round_trip_elapsed_s"] = 0.04
    return row


def test_process_performance_accepts_complete_ordered_rows_at_inclusive_limits():
    performance = [
        {"kind": "frame", "boundary": 1},
        _analysis_row("attempt-a", 1),
        _analysis_row("attempt-b", 2),
    ]

    assert process_performance_errors(
        performance, ["attempt-a", "attempt-b"]
    ) == []


@pytest.mark.parametrize(
    ("performance", "attempt_ids", "message"),
    [
        ([], ["attempt-a"], "does not match every durable attempt"),
        ([], [], "production analysis process telemetry is missing"),
        (
            [_analysis_row("attempt-a", 1)],
            ["attempt-a", "attempt-a"],
            "does not match every durable attempt",
        ),
        (
            [_analysis_row("attempt-b", 1), _analysis_row("attempt-a", 2)],
            ["attempt-a", "attempt-b"],
            "does not match every durable attempt",
        ),
        (
            [_analysis_row("attempt-a", 1), _analysis_row("attempt-a", 2)],
            ["attempt-a", "attempt-b"],
            "does not match every durable attempt",
        ),
    ],
)
def test_process_performance_rejects_missing_duplicate_or_reordered_binding(
    performance, attempt_ids, message
):
    assert any(
        message in error
        for error in process_performance_errors(performance, attempt_ids)
    )


@pytest.mark.parametrize("sequence", [None, True, 1.0, 0, 2, "1"])
def test_process_performance_rejects_missing_coercive_or_discontinuous_sequence(
    sequence,
):
    row = _analysis_row("attempt-a", 1)
    row["process_sequence"] = sequence

    assert "analysis process sequence is missing or discontinuous" in (
        process_performance_errors([row], ["attempt-a"])
    )


@pytest.mark.parametrize("span", SPANS)
@pytest.mark.parametrize(
    "value",
    [None, True, "0.1", -0.01, math.nan, math.inf, -math.inf],
)
def test_process_performance_rejects_missing_nonfinite_or_negative_span(
    span, value
):
    row = _analysis_row("attempt-a", 1)
    row[span] = value

    assert f"analysis process span {span} is missing or invalid" in (
        process_performance_errors([row], ["attempt-a"])
    )


@pytest.mark.parametrize(
    "value", [None, True, "0.1", -0.01, math.nan, math.inf, -math.inf]
)
def test_process_performance_rejects_invalid_total_analysis_elapsed(value):
    row = _analysis_row("attempt-a", 1)
    row["analysis_elapsed_s"] = value

    assert "analysis process span analysis_elapsed_s is missing or invalid" in (
        process_performance_errors([row], ["attempt-a"])
    )


def test_process_performance_rejects_impossible_nested_elapsed_spans():
    analysis_too_short = _analysis_row("attempt-a", 1)
    analysis_too_short["analysis_elapsed_s"] = 0.03
    round_trip_too_short = _analysis_row("attempt-a", 1)
    round_trip_too_short["process_round_trip_elapsed_s"] = 0.02

    assert "analysis process durations have impossible nesting" in (
        process_performance_errors([analysis_too_short], ["attempt-a"])
    )
    assert "analysis process durations have impossible nesting" in (
        process_performance_errors([round_trip_too_short], ["attempt-a"])
    )


def test_process_performance_nesting_allows_only_documented_float_tolerance():
    row = _analysis_row("attempt-a", 1)
    parent_sum = sum(row[name] for name in SPANS[:3])
    row["process_round_trip_elapsed_s"] = parent_sum - 0.5e-9
    row["analysis_elapsed_s"] = row["process_round_trip_elapsed_s"] - 0.5e-9

    assert process_performance_errors([row], ["attempt-a"]) == []


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("process_control_bytes", 65_536),
        ("process_result_bytes", 16_777_216),
    ],
)
@pytest.mark.parametrize("value_kind", ["one", "limit"])
def test_process_performance_byte_bounds_are_positive_and_inclusive(
    field, limit, value_kind
):
    row = _analysis_row("attempt-a", 1)
    row[field] = 1 if value_kind == "one" else limit

    assert process_performance_errors([row], ["attempt-a"]) == []


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("process_control_bytes", 65_536),
        ("process_result_bytes", 16_777_216),
    ],
)
@pytest.mark.parametrize("value", [None, True, 1.0, 0, -1])
def test_process_performance_rejects_missing_coercive_or_nonpositive_byte_count(
    field, limit, value
):
    del limit
    row = _analysis_row("attempt-a", 1)
    row[field] = value

    assert f"analysis process byte count {field} is outside its inclusive bound" in (
        process_performance_errors([row], ["attempt-a"])
    )


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("process_control_bytes", 65_536),
        ("process_result_bytes", 16_777_216),
    ],
)
def test_process_performance_rejects_byte_count_above_inclusive_limit(field, limit):
    row = _analysis_row("attempt-a", 1)
    row[field] = limit + 1

    assert f"analysis process byte count {field} is outside its inclusive bound" in (
        process_performance_errors([row], ["attempt-a"])
    )


@pytest.mark.parametrize("value", ["missing", False, 0, "", "child exited"])
def test_process_performance_requires_exact_none_fatal_backend_error(value):
    row = _analysis_row("attempt-a", 1)
    if value == "missing":
        row.pop("fatal_backend_error")
    else:
        row["fatal_backend_error"] = value

    assert "analysis fatal-error field is missing or reports a failure" in (
        process_performance_errors([row], ["attempt-a"])
    )


def test_process_performance_validation_does_not_mutate_artifact_rows():
    rows = [_analysis_row("attempt-a", 1)]
    before = copy.deepcopy(rows)

    process_performance_errors(rows, ["attempt-a"])

    assert rows == before


def test_memory_observation_filters_generation_and_reports_diagnostic_slope():
    samples = [
        {
            "generation": 1,
            "boundary": 9_999,
            "monotonic_s": -1.0,
            "child_current_rss_bytes": 999,
        },
        {
            "generation": 2,
            "boundary": 1_799,
            "monotonic_s": -0.5,
            "child_current_rss_bytes": 888,
        },
        {
            "generation": 2,
            "boundary": 1_800,
            "monotonic_s": -0.25,
            "child_current_rss_bytes": None,
        },
        {
            "generation": 2,
            "boundary": 1_800,
            "monotonic_s": -0.1,
            "child_current_rss_bytes": 123.0,
        },
    ]
    samples.extend(
        {
            "generation": 2,
            "boundary": 1_800 + 10 * index,
            "monotonic_s": float(index),
            "child_current_rss_bytes": 100 + 10 * index,
        }
        for index in range(8)
    )

    observation = memory_observation(
        samples, "child_current_rss_bytes", generation=2
    )

    assert observation["peak_bytes"] == 999
    assert observation["steady"] == {
        "first_boundary": 1_800,
        "last_boundary": 1_870,
        "early_quarter_median_bytes": 105.0,
        "late_quarter_median_bytes": 165.0,
        "late_minus_early_bytes": 60.0,
        "late_quarter_endpoint_bytes_per_s": 10.0,
        "interpretation": (
            "Observed memory; inspect alongside bounded ownership tests."
        ),
    }
