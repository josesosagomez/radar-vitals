"""Independent M4 scoring verification from the approved Step 1b contracts.

All fixtures are generated in memory or under ``tmp_path``.  No real radar or
Masimo input is opened.  The expected counts and arithmetic below are derived
directly from the approved two-lock/seven-arm design.
"""
from __future__ import annotations

import json
import ast
import inspect
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.m4.capture_registry import ReferenceScope, load_registry  # noqa: E402
from src.m4.bundle import (  # noqa: E402
    BundleWriter,
    read_manifest,
    sha256_path,
    verify_bundle,
)
from src.m4.estimator_runner import CANONICAL_ARM_IDS, LOCK_ESTIMANDS  # noqa: E402
from src.m4.evidence_serialization import serialize_native_tree  # noqa: E402
import src.m4.estimator_scoring as scoring  # noqa: E402


CAPTURE_WINDOWS = {
    "m1": 6,
    "m2": 6,
    "sweep": 16,
    "m3": 20,
    "m4": 20,
    "m5": 20,
    "m6": 20,
    "m7": 20,
}
LOCK_BINS = {
    "m1": (23, 27),
    "m2": (20, 26),
    "sweep": (21, 26),
    "m3": (26, 26),
    "m4": (25, 25),
    "m5": (25, 25),
    "m6": (24, 24),
    "m7": (32, 32),
}
PROTOCOLS = {
    "m1": "natural",
    "m2": "paced_16_bpm",
    "sweep": "paced_schedule_target_unavailable",
    **{capture: "unknown_protocol_development" for capture in ("m3", "m4", "m5", "m6", "m7")},
}
STRATA = {
    "natural": ("m1",),
    "paced": ("m2", "sweep"),
    "unknown": ("m3", "m4", "m5", "m6", "m7"),
}


def _reference_scope(tmp_path: Path) -> ReferenceScope:
    return ReferenceScope(
        root=tmp_path,
        masimo_csv={capture: f"reference/{capture}.csv" for capture in CAPTURE_WINDOWS},
        masimo_sha256={capture: "a" * 64 for capture in CAPTURE_WINDOWS},
        protocol=PROTOCOLS,
        strata=STRATA,
    )


def _reference_frame() -> pd.DataFrame:
    epoch = np.arange(700, dtype=np.int64)
    pr = np.full(epoch.shape, 70.0)
    rr = np.full(epoch.shape, 15.0)
    # Distinct stable second window proves the exact half-open boundary at epoch 30.
    pr[(epoch >= 30) & (epoch < 60)] = 100.0
    return pd.DataFrame(
        {"epoch_utc": epoch, "pr_bpm": pr, "pi": np.ones(epoch.shape), "rr_bpm": rr}
    )


def _radar_row(capture: str, lock_index: int, k: int, arm_index: int) -> dict:
    lock_id = LOCK_ESTIMANDS[lock_index]
    arm_id = CANONICAL_ARM_IDS[arm_index]
    suite_is_production = arm_index == 0
    return {
        "schema_version": 2,
        "run_id": "independent-same-run",
        "run_hash": "1" * 64,
        "source_hash": "2" * 64,
        "capture_id": capture,
        "lock_estimand_id": lock_id,
        "locked_bin": LOCK_BINS[capture][lock_index],
        "k": k,
        "frame_start": 600 * k,
        "frame_stop": 600 * (k + 1),
        "epoch_start": float(30 * k),
        "epoch_stop": float(30 * (k + 1)),
        "time_origin_id": "start_wall_utc_approximate_v1",
        "origin_is_approximate": True,
        "evaluation_status": "exploratory_non_frozen",
        "window_set": "full_k0_diagnostic" if k == 0 else "evaluation_k_ge_1",
        "window_origin_role": "lock_selection_in_sample" if k == 0 else "evaluation",
        "suite_id": (
            "production_eca_ahet_suite_v1"
            if suite_is_production
            else "ahmed_fixed_h_phase_suite_v1"
        ),
        "suite_config_hash": ("3" if suite_is_production else "4") * 64,
        "arm_id": arm_id,
        "estimator_id": "eca_ahet_v1" if suite_is_production else "ahmed_fixed_h_phase_v1",
        "arm_config_hash": format(arm_index + 5, "x") * 64,
        "cube_hash": format(10 + list(CAPTURE_WINDOWS).index(capture), "x")[-1] * 64,
        "window_cube_hash": format((k % 5) + 1, "x") * 64,
        "shared_signal_hash": format(lock_index + 7, "x") * 64,
        "capture_config_hash": "c" * 64,
        "hr_valid": True,
        "hr_raw": 70.0 if suite_is_production else 72.0,
        "br_valid": True,
        "br_bpm": 15.0 if suite_is_production else 16.0,
        "validity_reason": "ok",
        "hr_validity_reason": "ok",
        "br_validity_reason": "ok",
        "outcome": None,
    }


def _canonical_rows() -> list[dict]:
    return [
        _radar_row(capture, lock_index, k, arm_index)
        for capture, windows in CAPTURE_WINDOWS.items()
        for lock_index in range(2)
        for k in range(windows)
        for arm_index in range(7)
    ]


def _execute(tmp_path: Path, rows: list[dict] | None = None, loader=None):
    selected_loader = (lambda _path: _reference_frame()) if loader is None else loader
    return scoring.execute_score(
        radar_rows=_canonical_rows() if rows is None else rows,
        reference=_reference_scope(tmp_path),
        file_hash=lambda _path: "a" * 64,
        masimo_loader=selected_loader,
    )


def test_exact_parent_and_scoring_universe_cardinalities(tmp_path):
    rows = _canonical_rows()
    identity = scoring.validate_radar_rows(rows)
    assert identity == {
        "run_id": "independent-same-run",
        "run_hash": "1" * 64,
        "source_hash": "2" * 64,
        "source_span_count": 128,
        "shared_row_count": 256,
        "estimator_row_count": 1792,
    }

    result = _execute(tmp_path, rows)
    assert len(result.metrics["per_capture"]) == 8 * 2 * 2 * 7 * 2 == 448
    assert len(result.metrics["protocol_summaries"]) == 3 * 2 * 2 * 7 * 2 * 2 == 336
    assert len(result.partitions["production_vs_ahmed"]) == 8 * 2 * 2 * 2 * 6 == 384
    assert len(result.partitions["protocol_summaries"]) == 3 * 2 * 2 * 2 * 6 * 2 == 288
    for vital in ("hr", "br"):
        diagnostic = [
            row for row in result.scored_rows
            if row["vital"] == vital and row["window_universe"] == "full_k0_diagnostic"
        ]
        comparative = [
            row for row in result.scored_rows
            if row["vital"] == vital and row["window_universe"] == "evaluation_k_ge_1"
        ]
        assert len(diagnostic) == 16 * 7 == 112
        assert len(comparative) == 240 * 7 == 1680
        assert {row["k"] for row in diagnostic} == {0}
        assert all(row["k"] >= 1 for row in comparative)


def test_half_open_integer_epoch_join_uses_unchanged_pr_and_rr_columns(tmp_path):
    result = _execute(tmp_path)
    rows = [
        row for row in result.scored_rows
        if row["capture_id"] == "m1"
        and row["lock_estimand_id"] == LOCK_ESTIMANDS[0]
        and row["arm_id"] == CANONICAL_ARM_IDS[0]
    ]
    hr0 = next(row for row in rows if row["vital"] == "hr" and row["k"] == 0)
    hr1 = next(row for row in rows if row["vital"] == "hr" and row["k"] == 1)
    br0 = next(row for row in rows if row["vital"] == "br" and row["k"] == 0)
    assert hr0["reference_value_bpm"] == 70.0
    assert hr1["reference_value_bpm"] == 100.0
    assert br0["reference_value_bpm"] == 15.0
    assert hr0["epoch_stop"] == hr1["epoch_start"] == 30.0


@pytest.mark.parametrize("mutation", ["non_integer_timestamp", "duplicate_timestamp"])
def test_reference_join_rejects_non_integer_or_many_to_many_timestamp(tmp_path, mutation):
    frame = _reference_frame()
    if mutation == "non_integer_timestamp":
        frame["epoch_utc"] = frame["epoch_utc"].astype(np.float64)
        expected = "integer Timestamp"
    else:
        frame.loc[1, "epoch_utc"] = frame.loc[0, "epoch_utc"]
        expected = "duplicate Timestamp"
    with pytest.raises(scoring.ScoreContractError, match=expected):
        _execute(tmp_path, loader=lambda _path: frame)


def test_reference_precedence_and_vital_specific_radar_reasons(tmp_path):
    rows = _canonical_rows()
    target = next(
        row for row in rows
        if row["capture_id"] == "m1" and row["lock_estimand_id"] == LOCK_ESTIMANDS[0]
        and row["k"] == 0 and row["arm_id"] == CANONICAL_ARM_IDS[0]
    )
    target.update(
        hr_valid=False,
        hr_raw=None,
        validity_reason="heart_contract_invalid",
        hr_validity_reason="heart_contract_invalid",
        br_valid=False,
        br_bpm=None,
        br_validity_reason="breath_contract_invalid",
    )

    def loader(path: Path):
        frame = _reference_frame()
        capture = path.stem
        if capture == "m1":
            frame.loc[frame["epoch_utc"] < 30, "pr_bpm"] = np.nan
        if capture == "m2":
            first = frame["epoch_utc"] < 30
            frame.loc[first, "pr_bpm"] = np.tile([60.0, 80.0], 15)
            frame.loc[first, "rr_bpm"] = np.tile([10.0, 20.0], 15)
        return frame

    result = _execute(tmp_path, rows, loader)
    scored = [
        row for row in result.scored_rows
        if row["capture_id"] == "m1" and row["lock_estimand_id"] == LOCK_ESTIMANDS[0]
        and row["k"] == 0 and row["arm_id"] == CANONICAL_ARM_IDS[0]
    ]
    heart = next(row for row in scored if row["vital"] == "hr")
    breath = next(row for row in scored if row["vital"] == "br")
    assert heart["disposition"] == "reference_insufficient_usable"
    assert breath["disposition"] == "radar_breath_contract_invalid"
    assert heart["radar_validity_reason"] == "heart_contract_invalid"
    assert breath["radar_validity_reason"] == "breath_contract_invalid"

    m2_k0 = [
        row for row in result.scored_rows
        if row["capture_id"] == "m2" and row["k"] == 0
        and row["lock_estimand_id"] == LOCK_ESTIMANDS[0]
        and row["arm_id"] == CANONICAL_ARM_IDS[0]
    ]
    assert {row["disposition"] for row in m2_k0} == {"reference_nonstationary"}


def test_hand_calculated_error_coverage_and_linear_percentiles():
    rows = [
        scoring.ScoredRow("m1", LOCK_ESTIMANDS[0], 1, "a", 72.0, True, 70.0, True, radar_reason="ok"),
        scoring.ScoredRow("m1", LOCK_ESTIMANDS[0], 2, "a", 66.0, True, 70.0, True, radar_reason="ok"),
        scoring.ScoredRow("m1", LOCK_ESTIMANDS[0], 3, "a", 76.0, True, 70.0, True, radar_reason="ok"),
        scoring.ScoredRow("m1", LOCK_ESTIMANDS[0], 4, "a", None, False, 70.0, True, radar_reason="no_peak"),
        scoring.ScoredRow(
            "m1", LOCK_ESTIMANDS[0], 5, "a", 999.0, True, None, False,
            reference_reason="insufficient_usable", radar_reason="ok",
        ),
    ]
    summary = scoring.coverage_and_metrics(rows)
    assert (summary.n_source, summary.n_reference_admitted, summary.n_radar_valid, summary.n_joint) == (5, 4, 4, 3)
    assert summary.reference_coverage == pytest.approx(4 / 5)
    assert summary.radar_coverage == pytest.approx(4 / 5)
    assert summary.joint_coverage == pytest.approx(3 / 5)
    assert summary.joint_given_reference == pytest.approx(3 / 4)
    assert summary.mae == pytest.approx(4.0)
    assert summary.rmse == pytest.approx(np.sqrt(56 / 3))
    assert summary.bias == pytest.approx(4 / 3)
    absolute = np.array([2.0, 4.0, 6.0])
    for point in (50, 75, 90, 95):
        assert summary.error_percentiles[f"p{point}"] == pytest.approx(
            np.percentile(absolute, point, method="linear")
        )
    assert summary.exclusions == {
        "joint": 3,
        "radar_no_peak": 1,
        "reference_insufficient_usable": 1,
    }


def test_empty_pairwise_intersection_has_null_metrics_and_four_way_total():
    prod = [
        scoring.ScoredRow("m1", LOCK_ESTIMANDS[0], 1, "prod", 70.0, True, 70.0, True, radar_reason="ok"),
        scoring.ScoredRow("m1", LOCK_ESTIMANDS[0], 2, "prod", None, False, 70.0, True, radar_reason="pbad"),
    ]
    ahmed = [
        scoring.ScoredRow("m1", LOCK_ESTIMANDS[0], 1, "arm", None, False, 70.0, True, radar_reason="abad"),
        scoring.ScoredRow("m1", LOCK_ESTIMANDS[0], 2, "arm", 72.0, True, 70.0, True, radar_reason="ok"),
    ]
    result = scoring.paired_partitions(prod, ahmed)
    assert result["partitions"] == {
        "both": 0,
        "production_only": 1,
        "ahmed_only": 1,
        "neither": 0,
    }
    assert sum(result["partitions"].values()) == result["n_reference_admitted"] == 2
    assert result["n_intersection"] == 0
    assert result["production_metrics_on_intersection"]["mae"] is None
    assert result["ahmed_metrics_on_intersection"]["rmse"] is None
    assert result["descriptive_difference_ahmed_minus_production"]["p95"] is None


def test_pairwise_comparisons_have_protocol_micro_and_capture_macro_summaries(tmp_path):
    result = _execute(tmp_path)
    # Step 1b section 3.5 applies the within-stratum summary rule to the
    # production-versus-each-Ahmed comparison as well as arm-wise metrics.
    summaries = result.partitions["protocol_summaries"]
    expected = {
        (stratum, lock, universe, vital, arm, kind)
        for stratum in STRATA
        for lock in LOCK_ESTIMANDS
        for universe in ("full_k0_diagnostic", "evaluation_k_ge_1")
        for vital in ("hr", "br")
        for arm in CANONICAL_ARM_IDS[1:]
        for kind in ("protocol_pooled_window_micro", "protocol_capture_macro")
    }
    observed = {
        (
            row["protocol_stratum"], row["lock_estimand_id"], row["window_universe"],
            row["vital"], row["ahmed_arm_id"], row["summary_kind"],
        )
        for row in summaries
    }
    assert len(summaries) == len(expected)
    assert observed == expected
    assert all("all" not in str(row["protocol_stratum"]).lower() for row in summaries)
    macro = next(row for row in summaries if row["summary_kind"] == "protocol_capture_macro")
    assert "contributing_capture_count_by_metric" in macro


def test_pairwise_protocol_micro_and_macro_use_partial_intersections_correctly(tmp_path):
    rows = _canonical_rows()
    arm_id = CANONICAL_ARM_IDS[1]
    # Every m2 comparative Ahmed HR is invalid; sweep remains both-valid.  In the
    # paced stratum this yields 5 production-only + 15 both-valid windows.
    for row in rows:
        if (
            row["capture_id"] == "m2"
            and row["k"] >= 1
            and row["arm_id"] == arm_id
        ):
            row["hr_valid"] = False
            row["hr_raw"] = None
            row["validity_reason"] = "non_unique_maximum"
            row["hr_validity_reason"] = "non_unique_maximum"
    constant = _reference_frame()
    constant["pr_bpm"] = 70.0
    result = _execute(tmp_path, rows, loader=lambda _path: constant)
    matching = [
        row for row in result.partitions["protocol_summaries"]
        if row["protocol_stratum"] == "paced"
        and row["lock_estimand_id"] == LOCK_ESTIMANDS[0]
        and row["window_universe"] == "evaluation_k_ge_1"
        and row["vital"] == "hr"
        and row["ahmed_arm_id"] == arm_id
    ]
    micro = next(row for row in matching if row["summary_kind"] == "protocol_pooled_window_micro")
    macro = next(row for row in matching if row["summary_kind"] == "protocol_capture_macro")
    assert micro["n_reference_admitted"] == 20
    assert micro["partitions"] == {
        "both": 15,
        "production_only": 5,
        "ahmed_only": 0,
        "neither": 0,
    }
    assert micro["production_metrics_on_intersection"]["mae"] == 0.0
    assert micro["ahmed_metrics_on_intersection"]["mae"] == 2.0
    assert macro["metric_means"]["partitions.production_only"] == pytest.approx(2.5)
    assert macro["contributing_capture_count_by_metric"]["partitions.production_only"] == 2
    assert macro["metric_means"]["production_metrics_on_intersection.mae"] == 0.0
    assert macro["contributing_capture_count_by_metric"][
        "production_metrics_on_intersection.mae"
    ] == 1
    assert macro["metric_means"]["ahmed_metrics_on_intersection.mae"] == 2.0
    assert macro["contributing_capture_count_by_metric"][
        "ahmed_metrics_on_intersection.mae"
    ] == 1


def test_pairwise_macro_retains_null_metrics_with_zero_contributing_captures(tmp_path):
    rows = _canonical_rows()
    arm_id = CANONICAL_ARM_IDS[1]
    for row in rows:
        if row["capture_id"] in {"m2", "sweep"} and row["k"] >= 1 and row["arm_id"] == arm_id:
            row["hr_valid"] = False
            row["hr_raw"] = None
            row["validity_reason"] = "non_unique_maximum"
            row["hr_validity_reason"] = "non_unique_maximum"
    result = _execute(tmp_path, rows)
    macro = next(
        row for row in result.partitions["protocol_summaries"]
        if row["summary_kind"] == "protocol_capture_macro"
        and row["protocol_stratum"] == "paced"
        and row["lock_estimand_id"] == LOCK_ESTIMANDS[0]
        and row["window_universe"] == "evaluation_k_ge_1"
        and row["vital"] == "hr"
        and row["ahmed_arm_id"] == arm_id
    )
    metric = "ahmed_metrics_on_intersection.mae"
    difference = "descriptive_difference_ahmed_minus_production.mae"
    assert macro["metric_means"][metric] is None
    assert macro["contributing_capture_count_by_metric"][metric] == 0
    assert macro["metric_means"][difference] is None
    assert macro["contributing_capture_count_by_metric"][difference] == 0

    arm_macro = next(
        row for row in result.metrics["protocol_summaries"]
        if row["summary_kind"] == "protocol_capture_macro"
        and row["protocol_stratum"] == "paced"
        and row["lock_estimand_id"] == LOCK_ESTIMANDS[0]
        and row["window_universe"] == "evaluation_k_ge_1"
        and row["vital"] == "hr"
        and row["arm_id"] == arm_id
    )
    assert arm_macro["metric_means"]["mae"] is None
    assert arm_macro["contributing_capture_count_by_metric"]["mae"] == 0


@pytest.mark.parametrize(
    "field,new_value,error",
    [
        ("lock_estimand_id", "not_a_lock", "unknown lock"),
        ("arm_id", "not_an_arm", "unknown arm"),
        ("suite_id", "wrong_suite", "suite/estimator identity"),
        ("window_set", "full_k0_diagnostic", "k/window-universe"),
    ],
)
def test_parent_row_schema_rejects_wrong_lock_arm_suite_and_universe(field, new_value, error):
    rows = [
        _radar_row("m1", lock_index, k, arm_index)
        for lock_index in range(2)
        for k in range(2)
        for arm_index in range(7)
    ]
    target = next(row for row in rows if row["k"] == 1 and row["arm_id"] == CANONICAL_ARM_IDS[0])
    target[field] = new_value
    with pytest.raises(scoring.ScoreContractError, match=error):
        scoring.validate_radar_rows(rows, expected_capture_windows={"m1": 2})


def test_parent_rejects_cross_lock_window_identity_mutation():
    rows = [
        _radar_row("m1", lock_index, 0, arm_index)
        for lock_index in range(2)
        for arm_index in range(7)
    ]
    for row in rows:
        if row["lock_estimand_id"] == LOCK_ESTIMANDS[1]:
            row["window_cube_hash"] = "f" * 64
    with pytest.raises(scoring.ScoreContractError, match="same source cube/window/span"):
        scoring.validate_radar_rows(rows, expected_capture_windows={"m1": 1})


@pytest.mark.parametrize("field", ["capture_config_hash", "suite_config_hash"])
def test_parent_rejects_scientific_configuration_hash_drift(field):
    rows = [
        _radar_row("m1", lock_index, k, arm_index)
        for lock_index in range(2)
        for k in range(2)
        for arm_index in range(7)
    ]
    for row in rows:
        if row["k"] == 1:
            row[field] = "f" * 64
    with pytest.raises(scoring.ScoreContractError, match="config|configuration|same"):
        scoring.validate_radar_rows(rows, expected_capture_windows={"m1": 2})


def _write_minimal_evidence(directory: Path, rows: list[dict]) -> None:
    directory.mkdir()
    shared_rows = [row for row in rows if row["arm_id"] == CANONICAL_ARM_IDS[0]]
    ahmed_rows = [row for row in rows if row["arm_id"] != CANONICAL_ARM_IDS[0]]
    shared = {
        "capture_id": np.asarray([row["capture_id"] for row in shared_rows]),
        "lock_estimand_id": np.asarray([row["lock_estimand_id"] for row in shared_rows]),
        "k": np.asarray([row["k"] for row in shared_rows], dtype=np.int64),
        "cube_hash": np.asarray([row["cube_hash"] for row in shared_rows]),
        "window_cube_hash": np.asarray([row["window_cube_hash"] for row in shared_rows]),
        "signal_hash": np.asarray([row["shared_signal_hash"] for row in shared_rows]),
        "config_hash": np.asarray([row["capture_config_hash"] for row in shared_rows]),
        "source_hash": np.asarray([row["source_hash"] for row in shared_rows]),
        "run_hash": np.asarray([row["run_hash"] for row in shared_rows]),
    }
    ahmed = {
        "capture_id": np.asarray([row["capture_id"] for row in ahmed_rows]),
        "lock_estimand_id": np.asarray([row["lock_estimand_id"] for row in ahmed_rows]),
        "k": np.asarray([row["k"] for row in ahmed_rows], dtype=np.int64),
        "arm_id": np.asarray([row["arm_id"] for row in ahmed_rows]),
        "cube_hash": np.asarray([row["cube_hash"] for row in ahmed_rows]),
        "signal_hash": np.asarray([row["shared_signal_hash"] for row in ahmed_rows]),
        "config_hash": np.asarray([row["arm_config_hash"] for row in ahmed_rows]),
        "source_hash": np.asarray([row["source_hash"] for row in ahmed_rows]),
        "run_hash": np.asarray([row["run_hash"] for row in ahmed_rows]),
    }
    np.savez(directory / "shared_evidence.npz", **shared)
    np.savez(directory / "ahmed_evidence.npz", **ahmed)
    native_records = [
        {
            "capture_id": row["capture_id"],
            "lock_estimand_id": row["lock_estimand_id"],
            "k": row["k"],
            "cube_hash": row["cube_hash"],
            "signal_hash": row["shared_signal_hash"],
            "config_hash": row["arm_config_hash"],
            "source_hash": row["source_hash"],
            "run_hash": row["run_hash"],
        }
        for row in shared_rows
    ]
    tree = serialize_native_tree(native_records)
    (directory / "production_native_index.json").write_text(
        json.dumps(tree.index), encoding="utf-8"
    )
    np.savez(directory / "production_evidence.npz", **tree.arrays)


def test_evidence_key_hash_and_cardinality_mutations_are_rejected(tmp_path):
    rows = [
        _radar_row("m1", lock_index, 0, arm_index)
        for lock_index in range(2)
        for arm_index in range(7)
    ]
    evidence = tmp_path / "evidence"
    _write_minimal_evidence(evidence, rows)
    scoring._validate_evidence_keys(evidence, rows)

    with np.load(evidence / "shared_evidence.npz", allow_pickle=False) as loaded:
        mutated = {name: np.array(value, copy=True) for name, value in loaded.items()}
    mutated["source_hash"][0] = "f" * 64
    np.savez(evidence / "shared_evidence.npz", **mutated)
    with pytest.raises(scoring.ScoreContractError, match="source_hash disagrees"):
        scoring._validate_evidence_keys(evidence, rows)


@pytest.mark.parametrize("mutation", ["duplicate_shared_key", "missing_ahmed_row"])
def test_evidence_duplicate_and_missing_keys_are_rejected(tmp_path, mutation):
    rows = [
        _radar_row("m1", lock_index, 0, arm_index)
        for lock_index in range(2)
        for arm_index in range(7)
    ]
    evidence = tmp_path / mutation
    _write_minimal_evidence(evidence, rows)
    if mutation == "duplicate_shared_key":
        path = evidence / "shared_evidence.npz"
        with np.load(path, allow_pickle=False) as loaded:
            arrays = {name: np.array(value, copy=True) for name, value in loaded.items()}
        arrays["lock_estimand_id"][1] = arrays["lock_estimand_id"][0]
        np.savez(path, **arrays)
        message = "shared evidence keys are duplicate"
    else:
        path = evidence / "ahmed_evidence.npz"
        with np.load(path, allow_pickle=False) as loaded:
            arrays = {name: np.array(value[:-1], copy=True) for name, value in loaded.items()}
        np.savez(path, **arrays)
        message = "Ahmed evidence keys are duplicate, missing, or extra"
    with pytest.raises(scoring.ScoreContractError, match=message):
        scoring._validate_evidence_keys(evidence, rows)


def test_all_gate_authorization_and_parent_rejections_precede_reference_path_access(
    tmp_path, monkeypatch
):
    class BombReference:
        def csv_path(self, _capture):
            raise AssertionError("Masimo path constructed before scoring preflight finished")

    def preflight_failure(**_kwargs):
        raise scoring.PreflightError("source/gate/authorization rejected")

    monkeypatch.setattr(scoring, "verify_preflight", preflight_failure)
    with pytest.raises(scoring.PreflightError, match="source/gate/authorization"):
        scoring.run_score_stage(
            gate_dir=tmp_path / "gate",
            authorization_path=tmp_path / "authorization.yaml",
            source_manifest_sha256="2" * 64,
            radar_dir=tmp_path / "radar",
            reference=BombReference(),
            run_id="independent",
        )


@pytest.mark.parametrize("scope", ["locks", "arms", "captures"])
def test_authorization_scope_mismatch_precedes_reference_path_access(tmp_path, monkeypatch, scope):
    class BombReference:
        def csv_path(self, _capture):
            raise AssertionError("reference path accessed after bad authorization scope")

    authorization = type(
        "Authorization",
        (),
        {
            "lock_estimands": (LOCK_ESTIMANDS[0],) if scope == "locks" else LOCK_ESTIMANDS,
            "arm_ids": CANONICAL_ARM_IDS[:-1] if scope == "arms" else CANONICAL_ARM_IDS,
            "capture_ids": tuple(CAPTURE_WINDOWS)[:-1] if scope == "captures" else tuple(CAPTURE_WINDOWS),
            "authorization_id": "bad-scope",
        },
    )()
    approved = type(
        "Preflight",
        (),
        {"authorization": authorization, "gate_manifest_sha256": "3" * 64},
    )()
    monkeypatch.setattr(scoring, "verify_preflight", lambda **_kwargs: approved)
    with pytest.raises(scoring.PreflightError, match="canonical locks|seven canonical arms|eight captures"):
        scoring.run_score_stage(
            gate_dir=tmp_path / "gate",
            authorization_path=tmp_path / "authorization.yaml",
            source_manifest_sha256="2" * 64,
            radar_dir=tmp_path / "radar",
            reference=BombReference(),
            run_id="independent",
        )

    class Approved:
        authorization = type(
            "Authorization",
            (),
            {
                "lock_estimands": LOCK_ESTIMANDS,
                "arm_ids": CANONICAL_ARM_IDS,
                "capture_ids": tuple(CAPTURE_WINDOWS),
                "authorization_id": "approved",
            },
        )()
        gate_manifest_sha256 = "3" * 64

    monkeypatch.setattr(scoring, "verify_preflight", lambda **_kwargs: Approved())
    monkeypatch.setattr(scoring, "sha256_path", lambda _path: "4" * 64)
    monkeypatch.setattr(
        scoring,
        "validate_radar_parent",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            scoring.ScoreContractError("parent/schema rejected")
        ),
    )
    with pytest.raises(scoring.ScoreContractError, match="parent/schema"):
        scoring.run_score_stage(
            gate_dir=tmp_path / "gate",
            authorization_path=tmp_path / "authorization.yaml",
            source_manifest_sha256="2" * 64,
            radar_dir=tmp_path / "radar",
            reference=BombReference(),
            run_id="independent",
        )


def test_registry_exact_paths_hashes_protocols_and_partition():
    registry = load_registry()
    reference = registry.reference_scope()
    assert reference.protocol == PROTOCOLS
    assert reference.strata == STRATA
    assert set(reference.masimo_csv) == set(CAPTURE_WINDOWS)
    assert len(set(reference.masimo_csv.values())) == 8
    assert all(not Path(path).is_absolute() and ".." not in Path(path).parts for path in reference.masimo_csv.values())
    assert all(Path(path).suffix.lower() == ".csv" for path in reference.masimo_csv.values())
    assert all(len(digest) == 64 and set(digest) <= set("0123456789abcdef") for digest in reference.masimo_sha256.values())


def test_every_scored_row_and_artifact_carries_nonpromotion_labels(tmp_path):
    result = _execute(tmp_path)
    for row in result.scored_rows:
        assert row["evaluation_status"] == "exploratory_non_frozen"
        assert row["origin_is_approximate"] is True
        assert row["time_origin_id"] == "start_wall_utc_approximate_v1"
        assert row["data_role"] == "development_apparent_single_subject"
        assert row["claim_status"] == "not_eligible_for_promotion_or_final_agreement_claims"
    for artifact in (result.metrics, result.partitions):
        assert artifact["origin_is_approximate"] is True
        assert artifact["claim_status"] == "not_eligible_for_promotion_or_final_agreement_claims"
        assert artifact["no_all_protocol_pooling"] is True
        assert artifact["no_arm_ranking"] is True


def test_scoring_module_never_calls_radar_decoder_or_estimator():
    tree = ast.parse(inspect.getsource(scoring))
    called_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                called_names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                called_names.add(node.func.attr)
    assert called_names.isdisjoint(
        {
            "decode_dca1000",
            "run_window_dsp",
            "estimate_phase_ha",
            "extract_chest_phase",
            "run_warmup_selection",
            "adc_path",
        }
    )
    assert not hasattr(ReferenceScope, "adc_path")


def test_persisted_score_bundle_is_immutable_labeled_and_never_publishes_latest(tmp_path):
    result = _execute(tmp_path)
    authorization = scoring.Authorization(
        authorization_id="independent",
        gate_manifest_sha256="3" * 64,
        source_manifest_sha256="2" * 64,
        allowed_stages=("real-smoke", "real-radar", "score"),
        capture_ids=tuple(CAPTURE_WINDOWS),
        lock_estimands=LOCK_ESTIMANDS,
        arm_ids=CANONICAL_ARM_IDS,
        approved_by="independent-test",
        approved_on="2026-08-08",
    )
    output = tmp_path / "scored"
    bundle = scoring.persist_score_artifacts(
        result,
        out_root=output,
        run_id="immutable-score",
        source_manifest_sha256="2" * 64,
        authorization=authorization,
        authorization_sha256="4" * 64,
        gate_manifest_sha256="3" * 64,
        radar_manifest_sha256="5" * 64,
    )
    manifest = verify_bundle(bundle.root)
    manifest_document, manifest_sha256 = read_manifest(bundle.root)
    assert manifest == manifest_document
    assert manifest_sha256 == bundle.manifest_sha256
    assert manifest["promotion_eligible"] is False
    assert manifest["evaluation_status"] == "exploratory_non_frozen"
    assert manifest["origin_is_approximate"] is True
    assert manifest["claim_status"] == "not_eligible_for_promotion_or_final_agreement_claims"
    assert not (output / "LATEST").exists()
    with pytest.raises(FileExistsError):
        scoring.persist_score_artifacts(
            result,
            out_root=output,
            run_id="immutable-score",
            source_manifest_sha256="2" * 64,
            authorization=authorization,
            authorization_sha256="4" * 64,
            gate_manifest_sha256="3" * 64,
            radar_manifest_sha256="5" * 64,
        )


def test_score_cli_stdout_labels_ineligibility_and_does_not_publish_latest(
    tmp_path, monkeypatch, capsys
):
    import scripts.m8_ahmed_transfer as cli

    result = _execute(tmp_path)
    authorization = scoring.Authorization(
        authorization_id="independent-cli",
        gate_manifest_sha256="3" * 64,
        source_manifest_sha256="2" * 64,
        allowed_stages=("real-smoke", "real-radar", "score"),
        capture_ids=tuple(CAPTURE_WINDOWS),
        lock_estimands=LOCK_ESTIMANDS,
        arm_ids=CANONICAL_ARM_IDS,
        approved_by="independent-test",
        approved_on="2026-08-08",
    )
    bundle = scoring.persist_score_artifacts(
        result,
        out_root=tmp_path / "cli-output",
        run_id="cli-score",
        source_manifest_sha256="2" * 64,
        authorization=authorization,
        authorization_sha256="4" * 64,
        gate_manifest_sha256="3" * 64,
        radar_manifest_sha256="5" * 64,
    )
    source = type("Source", (), {"manifest_sha256": "2" * 64})()
    monkeypatch.setattr(cli, "verify_gate_bundle", lambda _path: None)
    monkeypatch.setattr(cli, "load_source_manifest", lambda _path: source)
    monkeypatch.setattr(cli, "verify_source_manifest", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "run_score_stage", lambda **_kwargs: bundle)
    monkeypatch.setattr(
        cli,
        "publish_latest",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("score must never publish LATEST")
        ),
    )
    assert cli.run_score(
        gate_dir=tmp_path / "gate",
        authorization_path=tmp_path / "authorization.yaml",
        radar_dir=tmp_path / "radar",
        out_root=tmp_path / "ignored",
        publish=True,
    ) == 0
    stdout = capsys.readouterr().out
    assert "evaluation   : exploratory_non_frozen" in stdout
    assert "time_origin  : start_wall_utc_approximate_v1 (approximate=true)" in stdout
    assert "claim_status : not_eligible_for_promotion_or_final_agreement_claims" in stdout
    assert "development_apparent_single_subject; no population claim" in stdout
    assert "LATEST       : not published" in stdout


# ═══════════════════════════════════════════════════════════════════════════════
# M4 milestone audit: k=0 quarantine, hand-calculated agreement, measured
# coverage, protocol stratification, and paired-identity rejection.
#
# Every expected number in this section is worked out on paper from the approved
# plan (section 3 window universe / comparative universe / pairing contract) and
# the frozen comparator specification in `src/comparator.py`.  Nothing here is
# read back from the scorer, and no fixture re-implements the scorer's own
# arithmetic.
# ═══════════════════════════════════════════════════════════════════════════════


def _flat_reference_frame() -> pd.DataFrame:
    """A constant, always-admissible reference: PR 70, RR 15, PI 1.0, integer epochs.

    Constant within every 30 s window means every window clears the frozen
    comparator gates (>=24 usable samples, HR spread <=5 bpm, BR spread <=2 bpm),
    so any coverage loss observed on top of this frame is one the fixture injected
    deliberately and can be hand-counted.
    """
    epoch = np.arange(700, dtype=np.int64)
    return pd.DataFrame(
        {
            "epoch_utc": epoch,
            "pr_bpm": np.full(epoch.shape, 70.0),
            "pi": np.ones(epoch.shape),
            "rr_bpm": np.full(epoch.shape, 15.0),
        }
    )


def _cell_rows(rows, *, capture, lock_index=0, arm_index=0):
    """Every window of one (capture, lock, arm) column of the canonical cube."""
    return [
        row
        for row in rows
        if row["capture_id"] == capture
        and row["lock_estimand_id"] == LOCK_ESTIMANDS[lock_index]
        and row["arm_id"] == CANONICAL_ARM_IDS[arm_index]
    ]


def _arm_summary(result, *, capture, universe, arm_index=0, lock_index=0, vital="hr"):
    return next(
        record["metrics"]
        for record in result.metrics["per_capture"]
        if record["capture_id"] == capture
        and record["lock_estimand_id"] == LOCK_ESTIMANDS[lock_index]
        and record["window_universe"] == universe
        and record["arm_id"] == CANONICAL_ARM_IDS[arm_index]
        and record["vital"] == vital
    )


def _pairwise_summary(result, *, capture, universe, ahmed_index=1, lock_index=0, vital="hr"):
    return next(
        record
        for record in result.partitions["production_vs_ahmed"]
        if record["capture_id"] == capture
        and record["lock_estimand_id"] == LOCK_ESTIMANDS[lock_index]
        and record["window_universe"] == universe
        and record["ahmed_arm_id"] == CANONICAL_ARM_IDS[ahmed_index]
        and record["vital"] == vital
    )


# m1 carries 6 windows.  Against a constant 70 bpm reference the k>=1 production
# errors are +2, -4, +6, 0, -5 bpm; k=0 is deliberately absurd (+929 bpm) so any
# leak of the in-sample window into a comparative metric is unmissable.
_M1_PRODUCTION_HR = {0: 999.0, 1: 72.0, 2: 66.0, 3: 76.0, 4: 70.0, 5: 65.0}


def _k0_poisoned_rows() -> list[dict]:
    rows = _canonical_rows()
    for row in _cell_rows(rows, capture="m1"):
        row["hr_raw"] = _M1_PRODUCTION_HR[row["k"]]
    return rows


def test_k0_extremes_cannot_move_a_comparative_arm_metric(tmp_path):
    """Plan section 3: ``evaluation_k_ge_1`` is the only comparative universe.

    Hand arithmetic on the k>=1 errors +2, -4, +6, 0, -5 bpm:
      MAE  = (2+4+6+0+5)/5           = 3.4
      RMSE = sqrt((4+16+36+0+25)/5)  = sqrt(16.2)
      bias = (2-4+6+0-5)/5           = -0.2      (radar minus reference)
      |errors| sorted = [0, 2, 4, 5, 6] -> linear p50 = 4.0, p90 = 5.6
    The k=0 window's +929 bpm error appears in none of them.
    """
    result = _execute(tmp_path, _k0_poisoned_rows(), loader=lambda _path: _flat_reference_frame())

    comparative = _arm_summary(result, capture="m1", universe="evaluation_k_ge_1")
    assert comparative["n_source"] == 5
    assert comparative["n_joint"] == 5
    assert comparative["mae"] == pytest.approx(3.4)
    assert comparative["rmse"] == pytest.approx(np.sqrt(16.2))
    assert comparative["bias"] == pytest.approx(-0.2)
    assert comparative["error_percentiles"]["p50"] == pytest.approx(4.0)
    assert comparative["error_percentiles"]["p90"] == pytest.approx(5.6)

    # The k=0 estimate is not discarded: it is retained in its own labelled
    # in-sample diagnostic, where its 929 bpm error is fully visible.
    diagnostic = _arm_summary(result, capture="m1", universe="full_k0_diagnostic")
    assert diagnostic["n_source"] == 1
    assert diagnostic["mae"] == pytest.approx(929.0)
    assert diagnostic["bias"] == pytest.approx(929.0)


def test_k0_extremes_cannot_move_the_production_versus_ahmed_comparison(tmp_path):
    """The same quarantine must hold for the paired comparative headline.

    Ahmed arm 2 reports a constant 72 bpm, so on the k>=1 intersection its MAE is
    exactly 2.0 while production's is 3.4; the descriptive difference is -1.4.
    """
    result = _execute(tmp_path, _k0_poisoned_rows(), loader=lambda _path: _flat_reference_frame())

    comparative = _pairwise_summary(result, capture="m1", universe="evaluation_k_ge_1")
    assert comparative["n_reference_admitted"] == 5
    assert comparative["partitions"] == {
        "both": 5, "production_only": 0, "ahmed_only": 0, "neither": 0
    }
    assert comparative["n_intersection"] == 5
    assert comparative["production_metrics_on_intersection"]["mae"] == pytest.approx(3.4)
    assert comparative["ahmed_metrics_on_intersection"]["mae"] == pytest.approx(2.0)
    assert comparative["descriptive_difference_ahmed_minus_production"]["mae"] == pytest.approx(-1.4)

    diagnostic = _pairwise_summary(result, capture="m1", universe="full_k0_diagnostic")
    assert diagnostic["n_intersection"] == 1
    assert diagnostic["production_metrics_on_intersection"]["mae"] == pytest.approx(929.0)


def test_k0_rows_are_labelled_in_sample_and_never_comparative(tmp_path):
    """No scored row may carry a k / universe / origin-role combination it did not earn."""
    result = _execute(tmp_path, loader=lambda _path: _flat_reference_frame())

    for row in result.scored_rows:
        if row["k"] == 0:
            assert row["window_universe"] == "full_k0_diagnostic"
            assert row["window_origin_role"] == "lock_selection_in_sample"
        else:
            assert row["k"] >= 1
            assert row["window_universe"] == "evaluation_k_ge_1"
            assert row["window_origin_role"] == "evaluation"
    assert result.metrics["k0_status"] == "in_sample_diagnostic_only"
    assert result.metrics["comparative_universe"] == "evaluation_k_ge_1"
    # Every comparative summary is explicitly flagged eligible and every k=0
    # summary explicitly ineligible, so a consumer cannot mistake the two.
    for record in result.metrics["per_capture"] + result.partitions["production_vs_ahmed"]:
        assert record["comparative_eligible"] == (
            record["window_universe"] == "evaluation_k_ge_1"
        )


def test_signed_error_on_each_scored_row_is_radar_minus_reference(tmp_path):
    """Bias sign convention, checked on the persisted per-row field itself."""
    result = _execute(tmp_path, _k0_poisoned_rows(), loader=lambda _path: _flat_reference_frame())

    by_k = {
        row["k"]: row
        for row in result.scored_rows
        if row["capture_id"] == "m1"
        and row["lock_estimand_id"] == LOCK_ESTIMANDS[0]
        and row["arm_id"] == CANONICAL_ARM_IDS[0]
        and row["vital"] == "hr"
    }
    assert by_k[1]["error_radar_minus_reference_bpm"] == pytest.approx(2.0)   # 72 - 70
    assert by_k[2]["error_radar_minus_reference_bpm"] == pytest.approx(-4.0)  # 66 - 70
    assert by_k[2]["absolute_error_bpm"] == pytest.approx(4.0)
    assert by_k[4]["error_radar_minus_reference_bpm"] == pytest.approx(0.0)

    for row in result.scored_rows:
        if row["disposition"] == "joint":
            expected = row["radar_value_bpm"] - row["reference_value_bpm"]
            assert row["error_radar_minus_reference_bpm"] == pytest.approx(expected)
            assert row["absolute_error_bpm"] == pytest.approx(abs(expected))
        else:
            assert row["error_radar_minus_reference_bpm"] is None
            assert row["absolute_error_bpm"] is None


def _partly_unusable_reference(_path: Path) -> pd.DataFrame:
    """Constant reference with two deliberately unusable m1 windows.

    * k=3, epochs [90, 120): every PR sample is NaN -> 0 usable < 24 -> the frozen
      HR spec fails ``coverage_ok`` -> ``insufficient_usable``.
    * k=5, epochs [150, 180): PR alternates 60/80 bpm -> p90-p10 = 20 bpm > 5 bpm
      -> ``coverage_ok`` holds but stationarity fails -> ``nonstationary``.
    """
    frame = _flat_reference_frame()
    unusable = (frame["epoch_utc"] >= 90) & (frame["epoch_utc"] < 120)
    frame.loc[unusable, "pr_bpm"] = np.nan
    unstable = (frame["epoch_utc"] >= 150) & (frame["epoch_utc"] < 180)
    frame.loc[unstable, "pr_bpm"] = np.tile([60.0, 80.0], 15)
    return frame


def _partly_invalid_rows() -> list[dict]:
    """Declared estimator invalidity on the m1 production arm at k=2 and k=4."""
    rows = _canonical_rows()
    for row in _cell_rows(rows, capture="m1"):
        if row["k"] in (2, 4):
            row.update(
                hr_valid=False,
                hr_raw=None,
                validity_reason="low_snr",
                hr_validity_reason="low_snr",
            )
    return rows


def test_coverage_is_measured_and_both_marginals_reconcile_end_to_end(tmp_path):
    """Hand-counted coverage on the m1 / recorded-lock / production / HR column.

    | k | reference             | radar          | disposition                   |
    |---|-----------------------|----------------|-------------------------------|
    | 1 | admitted 70 bpm       | valid 70 bpm   | joint                         |
    | 2 | admitted 70 bpm       | invalid low_snr| radar_low_snr                 |
    | 3 | insufficient_usable   | valid 70 bpm   | reference_insufficient_usable |
    | 4 | admitted 70 bpm       | invalid low_snr| radar_low_snr                 |
    | 5 | nonstationary         | valid 70 bpm   | reference_nonstationary       |

    Declared invalidity is an expected scientific outcome: the run continues and
    the two failures are counted, never dropped from the denominator.
    """
    result = _execute(tmp_path, _partly_invalid_rows(), loader=_partly_unusable_reference)
    summary = _arm_summary(result, capture="m1", universe="evaluation_k_ge_1")

    assert summary["n_source"] == 5
    assert summary["n_reference_admitted"] == 3
    assert summary["n_radar_valid"] == 3
    assert summary["n_joint"] == 1
    assert summary["reference_coverage"] == pytest.approx(3 / 5)
    assert summary["radar_coverage"] == pytest.approx(3 / 5)
    assert summary["joint_coverage"] == pytest.approx(1 / 5)
    assert summary["joint_given_reference"] == pytest.approx(1 / 3)
    assert summary["exclusions"] == {
        "joint": 1,
        "radar_low_snr": 2,
        "reference_insufficient_usable": 1,
        "reference_nonstationary": 1,
    }
    assert summary["reference_exclusions"] == {"insufficient_usable": 1, "nonstationary": 1}
    assert summary["radar_exclusions"] == {"low_snr": 2}
    # Measured, not "100% by construction".
    assert summary["joint_coverage"] < 1.0
    assert summary["mae"] == pytest.approx(0.0)


def test_every_emitted_summary_reconciles_and_totals_to_the_frozen_universe(tmp_path):
    """Reconciliation is a property of every emitted grouping, not one fixture."""
    result = _execute(tmp_path, _partly_invalid_rows(), loader=_partly_unusable_reference)

    summaries = [record["metrics"] for record in result.metrics["per_capture"]]
    summaries += [
        record["metrics"]
        for record in result.metrics["protocol_summaries"]
        if record["summary_kind"] == "protocol_pooled_window_micro"
    ]
    for summary in summaries:
        n_source = summary["n_source"]
        assert sum(summary["exclusions"].values()) == n_source
        assert n_source - summary["n_reference_admitted"] == sum(
            summary["reference_exclusions"].values()
        )
        assert n_source - summary["n_radar_valid"] == sum(summary["radar_exclusions"].values())
        if n_source:
            assert summary["reference_coverage"] == pytest.approx(
                summary["n_reference_admitted"] / n_source
            )
            assert summary["radar_coverage"] == pytest.approx(
                summary["n_radar_valid"] / n_source
            )
            assert summary["joint_coverage"] == pytest.approx(summary["n_joint"] / n_source)

    # 120 evaluation windows x 2 locks x 7 arms = 1,680; 8 k=0 windows x 2 x 7 = 112.
    for universe, expected in (("evaluation_k_ge_1", 1680), ("full_k0_diagnostic", 112)):
        for vital in ("hr", "br"):
            assert (
                sum(
                    record["metrics"]["n_source"]
                    for record in result.metrics["per_capture"]
                    if record["window_universe"] == universe and record["vital"] == vital
                )
                == expected
            )


def test_missing_reference_rows_become_a_measured_exclusion_not_a_fabricated_value(tmp_path):
    """A window with no Masimo rows at all is excluded, never silently imputed."""

    def truncated(_path: Path) -> pd.DataFrame:
        frame = _flat_reference_frame()
        return frame[frame["epoch_utc"] < 60].copy()

    result = _execute(tmp_path, loader=truncated)
    summary = _arm_summary(result, capture="m1", universe="evaluation_k_ge_1")

    # Only k=1 [30, 60) still has reference rows; k=2..5 have none.
    assert summary["n_source"] == 5
    assert summary["n_reference_admitted"] == 1
    assert summary["n_radar_valid"] == 5
    assert summary["n_joint"] == 1
    assert summary["reference_coverage"] == pytest.approx(1 / 5)
    assert summary["radar_coverage"] == pytest.approx(1.0)
    assert summary["joint_given_reference"] == pytest.approx(1.0)
    assert summary["exclusions"] == {"joint": 1, "reference_insufficient_usable": 4}
    assert summary["radar_exclusions"] == {}

    empty = [
        row
        for row in result.scored_rows
        if row["capture_id"] == "m1"
        and row["lock_estimand_id"] == LOCK_ESTIMANDS[0]
        and row["arm_id"] == CANONICAL_ARM_IDS[0]
        and row["vital"] == "hr"
        and row["k"] >= 2
    ]
    assert len(empty) == 4
    for row in empty:
        assert row["reference_admitted"] is False
        assert row["reference_value_bpm"] is None
        assert row["error_radar_minus_reference_bpm"] is None
        assert row["reference_diagnostics"]["n_usable"] == 0


def test_masimo_alignment_never_uses_the_date_or_time_text_columns(tmp_path):
    """CLAUDE.md section 9: alignment is by the integer ``Timestamp`` column only."""

    def contradictory(_path: Path) -> pd.DataFrame:
        frame = _flat_reference_frame()
        # Deliberately inconsistent with `epoch_utc` and with each other.
        frame["Date"] = "2099-12-31"
        frame["Time"] = "99:99:99"
        frame["date"] = "1970-01-01"
        frame["time"] = ""
        return frame

    baseline = _execute(tmp_path, loader=lambda _path: _flat_reference_frame())
    with_text = _execute(tmp_path, loader=contradictory)

    assert with_text.scored_rows == baseline.scored_rows
    assert with_text.metrics == baseline.metrics


@pytest.mark.parametrize("mutation", ["no_timestamp_column", "text_timestamp_column"])
def test_a_reference_without_an_integer_timestamp_is_refused_not_reparsed(tmp_path, mutation):
    """Date/Time text is never a fallback when the integer ``Timestamp`` is absent."""

    def loader(_path: Path) -> pd.DataFrame:
        frame = _flat_reference_frame()
        frame["Date"] = "2026-07-13"
        frame["Time"] = "17:20:42"
        if mutation == "no_timestamp_column":
            return frame.drop(columns=["epoch_utc"])
        frame["epoch_utc"] = frame["epoch_utc"].astype(str)
        return frame

    with pytest.raises(scoring.ScoreContractError, match="integer Timestamp"):
        _execute(tmp_path, loader=loader)


def test_protocol_strata_pool_only_their_own_captures_and_are_never_merged(tmp_path):
    """Approved strata: natural={m1}, paced={m2, sweep}, unknown={m3..m7}.

    Comparative window counts per stratum, per lock / arm / vital, are therefore
    5, 5+15=20, and 5x19=95, summing to the frozen 120 evaluation windows.
    """
    result = _execute(tmp_path, loader=lambda _path: _flat_reference_frame())

    expected = {
        "evaluation_k_ge_1": {"natural": 5, "paced": 20, "unknown": 95},
        "full_k0_diagnostic": {"natural": 1, "paced": 2, "unknown": 5},
    }
    for universe, per_stratum in expected.items():
        assert sum(per_stratum.values()) == (120 if universe == "evaluation_k_ge_1" else 8)
        for stratum, n_source in per_stratum.items():
            micro = next(
                record
                for record in result.metrics["protocol_summaries"]
                if record["summary_kind"] == "protocol_pooled_window_micro"
                and record["protocol_stratum"] == stratum
                and record["window_universe"] == universe
                and record["lock_estimand_id"] == LOCK_ESTIMANDS[0]
                and record["arm_id"] == CANONICAL_ARM_IDS[0]
                and record["vital"] == "hr"
            )
            assert tuple(micro["capture_ids"]) == STRATA[stratum]
            assert micro["metrics"]["n_source"] == n_source
            assert micro["single_subject_descriptive_only"] is True

    # No stratum pools incompatible protocols, and the natural / paced / unknown
    # partition is exactly the approved one.
    emitted = {record["protocol_stratum"] for record in result.metrics["protocol_summaries"]}
    assert emitted == set(STRATA)
    assert result.metrics["no_all_protocol_pooling"] is True
    # Per-capture rows carry the exact registry protocol identity, so the sweep
    # capture's unavailable paced schedule is never printed as a known rate.
    for record in result.metrics["per_capture"]:
        assert record["protocol"] == PROTOCOLS[record["capture_id"]]
        assert record["capture_id"] in STRATA[record["protocol_stratum"]]
    assert PROTOCOLS["sweep"] == "paced_schedule_target_unavailable"


def test_unexpected_reference_loader_failure_aborts_the_score(tmp_path):
    """An unexpected I/O fault is not a scientific outcome: it must abort.

    It may never be absorbed into a validity/exclusion reason or turned into a
    partial score covering the captures that happened to load.
    """
    opened: list[Path] = []

    class ReferenceIOFault(OSError):
        pass

    def broken(path: Path):
        opened.append(path)
        if len(opened) == 3:
            raise ReferenceIOFault("reference payload truncated")
        return _flat_reference_frame()

    with pytest.raises(ReferenceIOFault, match="truncated"):
        _execute(tmp_path, loader=broken)
    assert len(opened) == 3


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf")])
def test_a_row_claiming_validity_without_a_finite_value_aborts_the_score(tmp_path, bad_value):
    """A contradictory radar row is a contract failure, not a new exclusion bucket."""
    rows = _canonical_rows()
    for row in _cell_rows(rows, capture="m1"):
        if row["k"] == 1:
            row["hr_raw"] = bad_value

    with pytest.raises(scoring.ScoreContractError, match="valid radar row has no finite value"):
        _execute(tmp_path, rows, loader=lambda _path: _flat_reference_frame())


@pytest.mark.parametrize(
    "field,value,arm_index,expected",
    [
        # One Ahmed arm claims a different shared signal than its six cell-mates.
        ("shared_signal_hash", "f" * 64, 1, "seven arms are not same-run/same-signal"),
        ("window_cube_hash", "f" * 64, 3, "seven arms are not same-run/same-signal"),
        # The production arm is imported from some other run: an external comparator.
        ("run_id", "external_production_artifact", 0, "mixed run/source identity"),
        ("run_hash", "e" * 64, 0, "mixed run/source identity"),
        ("cube_hash", "d" * 64, 0, "cube hash changes within one run"),
        ("locked_bin", 99, 0, "numeric lock does not match frozen registry"),
    ],
)
def test_a_deliberate_paired_identity_mismatch_is_rejected(field, value, arm_index, expected):
    """Plan section 3 pairing contract: all seven arms share one cube/window/lock/run."""
    rows = [
        _radar_row("m1", lock_index, 0, index)
        for lock_index in range(2)
        for index in range(7)
    ]
    assert scoring.validate_radar_rows(rows, expected_capture_windows={"m1": 1})[
        "estimator_row_count"
    ] == 14

    target = next(
        row
        for row in rows
        if row["lock_estimand_id"] == LOCK_ESTIMANDS[0]
        and row["arm_id"] == CANONICAL_ARM_IDS[arm_index]
    )
    target[field] = value
    with pytest.raises(scoring.ScoreContractError, match=expected):
        scoring.validate_radar_rows(rows, expected_capture_windows={"m1": 1})


# ── The M3 parent admitted for scoring: one same-run, complete, bound artifact ──


def _evidence_payloads(rows: list[dict]):
    """Shared / Ahmed / production evidence built straight from the canonical rows."""
    shared_rows = [row for row in rows if row["arm_id"] == CANONICAL_ARM_IDS[0]]
    ahmed_rows = [row for row in rows if row["arm_id"] != CANONICAL_ARM_IDS[0]]

    def column(source, name, dtype=None):
        return np.asarray([row[name] for row in source], dtype=dtype)

    shared = {
        "capture_id": column(shared_rows, "capture_id"),
        "lock_estimand_id": column(shared_rows, "lock_estimand_id"),
        "k": column(shared_rows, "k", np.int64),
        "cube_hash": column(shared_rows, "cube_hash"),
        "window_cube_hash": column(shared_rows, "window_cube_hash"),
        "signal_hash": column(shared_rows, "shared_signal_hash"),
        "config_hash": column(shared_rows, "capture_config_hash"),
        "source_hash": column(shared_rows, "source_hash"),
        "run_hash": column(shared_rows, "run_hash"),
    }
    ahmed = {
        "capture_id": column(ahmed_rows, "capture_id"),
        "lock_estimand_id": column(ahmed_rows, "lock_estimand_id"),
        "k": column(ahmed_rows, "k", np.int64),
        "arm_id": column(ahmed_rows, "arm_id"),
        "cube_hash": column(ahmed_rows, "cube_hash"),
        "signal_hash": column(ahmed_rows, "shared_signal_hash"),
        "config_hash": column(ahmed_rows, "arm_config_hash"),
        "source_hash": column(ahmed_rows, "source_hash"),
        "run_hash": column(ahmed_rows, "run_hash"),
    }
    tree = serialize_native_tree(
        [
            {
                "capture_id": row["capture_id"],
                "lock_estimand_id": row["lock_estimand_id"],
                "k": row["k"],
                "cube_hash": row["cube_hash"],
                "signal_hash": row["shared_signal_hash"],
                "config_hash": row["arm_config_hash"],
                "source_hash": row["source_hash"],
                "run_hash": row["run_hash"],
            }
            for row in shared_rows
        ]
    )
    return shared, ahmed, tree


_PARENT_GATE_SHA = "3" * 64
_PARENT_SOURCE_SHA = "2" * 64
_PARENT_AUTHORIZATION_ID = "m4-audit-authorization"
_PARENT_AUTHORIZATION_SHA = "4" * 64


def _write_radar_parent(
    root: Path,
    *,
    rows: list[dict] | None = None,
    stage: str = "radar",
    status: str = "complete",
    promotion_eligible: bool = True,
    parents: dict | None = None,
    manifest_overrides: dict | None = None,
    provenance_overrides: dict | None = None,
    resolved_overrides: dict | None = None,
    extra_payloads: dict | None = None,
    rows_document_overrides: dict | None = None,
):
    """One complete, hash-bound M3 radar bundle exactly as plan section 5/M3 defines it."""
    rows = _canonical_rows() if rows is None else rows
    shared, ahmed, tree = _evidence_payloads(rows)
    writer = BundleWriter(stage_root=root, stage=stage, run_id="radar-parent")
    writer.add_json(
        "rows.json", {"schema_version": 2, "rows": rows, **(rows_document_overrides or {})}
    )
    writer.add_json("selector_evidence.json", {"selector_id": "production_warmup_selector_v1"})
    writer.add_npz("shared_evidence.npz", shared)
    writer.add_npz("ahmed_evidence.npz", ahmed)
    writer.add_json("production_native_index.json", tree.index)
    writer.add_npz("production_evidence.npz", tree.arrays)
    writer.add_text(
        "resolved_config.yaml",
        yaml.safe_dump(
            {
                "capture_ids": sorted(CAPTURE_WINDOWS),
                "lock_estimands": list(LOCK_ESTIMANDS),
                "arm_ids": list(CANONICAL_ARM_IDS),
                "score_status": "disabled_until_m4",
                **(resolved_overrides or {}),
            },
            sort_keys=True,
        ),
    )
    for name, payload in (extra_payloads or {}).items():
        writer.add_json(name, payload)
    return writer.finalize(
        status=status,
        promotion_eligible=promotion_eligible,
        parents={"synthetic": _PARENT_GATE_SHA} if parents is None else parents,
        provenance={
            "source_manifest_sha256": _PARENT_SOURCE_SHA,
            "authorization_id": _PARENT_AUTHORIZATION_ID,
            "authorization_sha256": _PARENT_AUTHORIZATION_SHA,
            **(provenance_overrides or {}),
        },
        extra_manifest={
            "source_span_count": 128,
            "shared_row_count": 256,
            "estimator_row_count": 1792,
            "ahmed_evidence_count": 1536,
            "production_evidence_count": 256,
            "score_status": "disabled_until_m4",
            **(manifest_overrides or {}),
        },
    )


def _validate_parent(bundle_root: Path):
    return scoring.validate_radar_parent(
        bundle_root,
        expected_gate_manifest_sha256=_PARENT_GATE_SHA,
        expected_source_manifest_sha256=_PARENT_SOURCE_SHA,
        expected_authorization_id=_PARENT_AUTHORIZATION_ID,
        expected_authorization_sha256=_PARENT_AUTHORIZATION_SHA,
    )


def test_a_complete_same_run_radar_parent_is_admitted_with_the_frozen_cardinalities(tmp_path):
    """The only artifact M4 may consume: 128 / 256 / 1,792 from one bound M3 run."""
    bundle = _write_radar_parent(tmp_path / "radar")

    manifest, manifest_sha256, rows = _validate_parent(bundle.root)

    assert manifest_sha256 == bundle.manifest_sha256
    assert manifest["stage"] == "radar" and manifest["status"] == "complete"
    assert len(rows) == 1792
    assert len({(r["capture_id"], r["lock_estimand_id"], r["k"], r["arm_id"]) for r in rows}) == 1792
    assert len({(r["capture_id"], r["lock_estimand_id"], r["k"]) for r in rows}) == 256
    assert len({(r["capture_id"], r["k"]) for r in rows}) == 128
    assert len({r["run_id"] for r in rows}) == 1
    assert sum(r["k"] >= 1 for r in rows) == 1680
    assert len({(r["capture_id"], r["lock_estimand_id"], r["k"]) for r in rows if r["k"] >= 1}) == 240


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        # An artifact produced against a different synthetic gate.
        ({"parents": {"synthetic": "0" * 64}}, "wrong synthetic ancestor"),
        ({"parents": {}}, "wrong synthetic ancestor"),
        # A partial or abandoned radar run may not be scored.
        ({"status": "incomplete_abandoned"}, "one completed radar stage"),
        ({"stage": "smoke"}, "one completed radar stage"),
        ({"promotion_eligible": False}, "not promotion-eligible"),
        # An extra payload is an external artifact smuggled in beside the run.
        (
            {"extra_payloads": {"external_production_results.json": {"hr": 60.0}}},
            "payload allowlist is incomplete or substituted",
        ),
        # Declared cardinalities must be the frozen ones.
        ({"manifest_overrides": {"estimator_row_count": 1791}}, "estimator_row_count"),
        ({"manifest_overrides": {"shared_row_count": 240}}, "shared_row_count"),
        ({"manifest_overrides": {"source_span_count": 120}}, "source_span_count"),
        ({"manifest_overrides": {"ahmed_evidence_count": 1530}}, "ahmed_evidence_count"),
        ({"manifest_overrides": {"score_status": "already_scored"}}, "score_status"),
        # The parent must belong to this exact source / authorization chain.
        (
            {"provenance_overrides": {"source_manifest_sha256": "9" * 64}},
            "source identity differs from frozen source",
        ),
        (
            {"provenance_overrides": {"authorization_id": "some_other_authorization"}},
            "authorization ID differs",
        ),
        (
            {"provenance_overrides": {"authorization_sha256": "9" * 64}},
            "authorization hash differs",
        ),
        # The executed scope must be the canonical two-lock / seven-arm scope.
        ({"resolved_overrides": {"arm_ids": list(CANONICAL_ARM_IDS[:-1])}}, "canonical scope"),
        ({"resolved_overrides": {"lock_estimands": [LOCK_ESTIMANDS[0]]}}, "canonical scope"),
        ({"resolved_overrides": {"capture_ids": sorted(CAPTURE_WINDOWS)[:-1]}}, "canonical scope"),
        ({"rows_document_overrides": {"schema_version": 1}}, "rows.json has the wrong schema"),
    ],
)
def test_a_radar_parent_outside_the_frozen_chain_or_scope_is_refused(tmp_path, kwargs, expected):
    bundle = _write_radar_parent(tmp_path / "radar", **kwargs)
    with pytest.raises(scoring.ScoreContractError, match=expected):
        _validate_parent(bundle.root)


def test_a_radar_parent_with_a_missing_estimator_row_is_refused(tmp_path):
    """A short run is fatal, never a smaller denominator."""
    rows = _canonical_rows()
    bundle = _write_radar_parent(tmp_path / "radar", rows=rows[:-1])
    with pytest.raises(scoring.ScoreContractError, match="incomplete or contain extras"):
        _validate_parent(bundle.root)


def test_a_byte_tampered_radar_parent_payload_is_refused(tmp_path):
    bundle = _write_radar_parent(tmp_path / "radar")
    _validate_parent(bundle.root)

    document = json.loads((bundle.root / "rows.json").read_text(encoding="utf-8"))
    document["rows"][0]["hr_raw"] = 61.0
    (bundle.root / "rows.json").write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(scoring.ScoreContractError, match="failed hash/schema verification"):
        _validate_parent(bundle.root)


@pytest.mark.parametrize("arm_index", [0, 1])
def test_a_radar_parent_whose_evidence_disagrees_with_its_rows_is_refused(tmp_path, arm_index):
    """Rows and persisted evidence must describe the same estimates.

    The bundle's evidence is written from the canonical rows; the validator is then
    handed rows whose arm configuration hash was changed, so the persisted Ahmed and
    production evidence can no longer be the evidence for those rows.
    """
    bundle = _write_radar_parent(tmp_path / "radar")
    scoring._validate_evidence_keys(bundle.root, _canonical_rows())

    mutated = [
        {**row, "arm_config_hash": "b" * 64}
        if row["arm_id"] == CANONICAL_ARM_IDS[arm_index]
        else dict(row)
        for row in _canonical_rows()
    ]
    with pytest.raises(scoring.ScoreContractError, match="config_hash disagrees with rows"):
        scoring._validate_evidence_keys(bundle.root, mutated)


# ── One official end-to-end score over the real gate/authorization/parent chain ──


def _write_gate_and_authorization(root: Path):
    gate_writer = BundleWriter(stage_root=root / "synthetic", stage="synthetic", run_id="gate")
    gate_writer.add_json("gate.json", {"gate_status": "passed"})
    gate = gate_writer.finalize(
        status="complete",
        provenance={"source_manifest_sha256": _PARENT_SOURCE_SHA},
        promotion_eligible=True,
        extra_manifest={"gate_status": "passed"},
    )
    _manifest, gate_digest = read_manifest(gate.root)
    authorization_path = root / "authorization.yaml"
    authorization_path.write_text(
        yaml.safe_dump(
            {
                "authorization_id": _PARENT_AUTHORIZATION_ID,
                "gate_manifest_sha256": gate_digest,
                "source_manifest_sha256": _PARENT_SOURCE_SHA,
                "allowed_stages": ["real-smoke", "real-radar", "score"],
                "capture_ids": sorted(CAPTURE_WINDOWS),
                "lock_estimands": list(LOCK_ESTIMANDS),
                "arm_ids": list(CANONICAL_ARM_IDS),
                "approved_by": "m4-audit",
                "approved_on": "2026-08-08",
            }
        ),
        encoding="utf-8",
    )
    return gate, gate_digest, authorization_path, sha256_path(authorization_path)


def test_run_score_stage_scores_one_real_chain_and_persists_a_linked_bundle(tmp_path):
    """Gate -> authorization -> complete M3 parent -> Masimo, in that order, once."""
    gate, gate_digest, authorization_path, authorization_sha = _write_gate_and_authorization(
        tmp_path
    )
    parent = _write_radar_parent(
        tmp_path / "radar",
        parents={"synthetic": gate_digest},
        provenance_overrides={"authorization_sha256": authorization_sha},
    )
    _parent_manifest, parent_manifest_sha256 = read_manifest(parent.root)
    opened: list[Path] = []

    def loader(path: Path) -> pd.DataFrame:
        opened.append(path)
        return _flat_reference_frame()

    bundle = scoring.run_score_stage(
        gate_dir=gate.root,
        authorization_path=authorization_path,
        source_manifest_sha256=_PARENT_SOURCE_SHA,
        radar_dir=parent.root,
        reference=_reference_scope(tmp_path),
        run_id="m4-audit-score",
        out_root=tmp_path / "scored",
        file_hash=lambda _path: "a" * 64,
        masimo_loader=loader,
    )

    # Exactly one reference file per capture, opened only after the whole chain passed.
    assert len(opened) == len(set(opened)) == 8

    manifest = verify_bundle(bundle.root)
    assert manifest["stage"] == "scored" and manifest["status"] == "complete"
    assert manifest["scored_row_count"] == 3584          # 1,792 estimator rows x 2 vitals
    assert manifest["promotion_eligible"] is False
    assert manifest["claim_status"] == scoring.CLAIM_STATUS
    assert manifest["evaluation_status"] == "exploratory_non_frozen"
    assert manifest["origin_is_approximate"] is True
    assert manifest["parents"] == {"synthetic": gate_digest, "radar": parent_manifest_sha256}
    assert manifest["radar_parent_manifest_sha256"] == parent_manifest_sha256
    assert manifest["source_manifest_sha256"] == _PARENT_SOURCE_SHA
    assert manifest["authorization_sha256"] == authorization_sha
    assert set(manifest["reference_sha256_by_capture"]) == set(CAPTURE_WINDOWS)

    metrics = json.loads((bundle.root / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["comparative_universe"] == "evaluation_k_ge_1"
    assert metrics["k0_status"] == "in_sample_diagnostic_only"
    assert metrics["no_arm_ranking"] is True
    assert metrics["cardinalities"] == {
        "full_k0_shared_cells": 16,
        "full_k0_estimator_rows_per_vital": 112,
        "evaluation_k_ge_1_shared_cells": 240,
        "evaluation_k_ge_1_estimator_rows_per_vital": 1680,
        "total_scored_rows_both_vitals": 3584,
    }


def test_a_rejected_real_parent_stops_before_any_reference_path_is_built(tmp_path):
    """Same real chain, but the parent belongs to a different gate: nothing is read."""
    gate, _gate_digest, authorization_path, authorization_sha = _write_gate_and_authorization(
        tmp_path
    )
    parent = _write_radar_parent(
        tmp_path / "radar",
        parents={"synthetic": "0" * 64},
        provenance_overrides={"authorization_sha256": authorization_sha},
    )

    class BombReference:
        protocol = PROTOCOLS
        strata = STRATA

        def csv_path(self, _capture_id):
            raise AssertionError("a Masimo path was built for a rejected radar parent")

        def digest(self, _capture_id):
            raise AssertionError("a Masimo digest was read for a rejected radar parent")

    def forbidden_loader(_path):
        raise AssertionError("the Masimo parser ran for a rejected radar parent")

    with pytest.raises(Exception, match="gate|parent|synthetic"):
        scoring.run_score_stage(
            gate_dir=gate.root,
            authorization_path=authorization_path,
            source_manifest_sha256=_PARENT_SOURCE_SHA,
            radar_dir=parent.root,
            reference=BombReference(),
            run_id="must-not-score",
            file_hash=lambda _path: (_ for _ in ()).throw(
                AssertionError("a reference file was hashed for a rejected radar parent")
            ),
            masimo_loader=forbidden_loader,
        )
