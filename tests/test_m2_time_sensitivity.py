"""Fixed -1/0/+1-second reference-only and later-agreement sensitivity tests."""
from __future__ import annotations

import inspect
import json
import math
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "m2"))

from builders import synthetic_registry, write_registry  # noqa: E402
from src.m2.cohort_registry import DEFAULT_REGISTRY_PATH, load_registry, write_registry_revision  # noqa: E402
from src.m2.common import ContractError, sha256_file  # noqa: E402
from src.m2.label_firewall import (  # noqa: E402
    ReferenceOperation,
    authorize_scoring,
    transition_label_access_atomically,
)
from src.m2.time_sensitivity import (  # noqa: E402
    TIME_SHIFTS_S,
    _recovery_stage1,
    build_agreement_sensitivity,
    build_reference_only_sensitivity,
)


def _reference(start=99, stop=162, pr=70.0) -> pd.DataFrame:
    epochs = np.arange(start, stop, dtype=np.int64)
    values = np.full(epochs.size, pr, dtype=float)
    return pd.DataFrame(
        {"epoch_utc": epochs, "pr_bpm": values, "pi": 5.0, "rr_bpm": 15.0}
    )


def _artifact(
    reference=None, *, windows=2, arm="recovery", subject_id="T001",
    session_id=None, reference_sha256="a" * 64, comparator_config_sha256="b" * 64,
):
    session_id = session_id or f"{subject_id}_{arm}"
    return build_reference_only_sensitivity(
        _reference() if reference is None else reference,
        subject_id=subject_id, session_id=session_id, arm=arm,
        primary_frame0_epoch=100.0, n_complete_windows=windows,
        reference_sha256=reference_sha256,
        comparator_config_sha256=comparator_config_sha256,
    )


def _assert_no_forbidden_keys(value):
    forbidden = {"best", "selected", "optimizer", "promotion"}
    if isinstance(value, dict):
        assert not forbidden.intersection(value)
        for nested in value.values():
            _assert_no_forbidden_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_forbidden_keys(nested)


def test_reference_only_interface_has_no_radar_or_agreement_input_and_emits_all_shifts():
    parameters = set(inspect.signature(build_reference_only_sensitivity).parameters)
    assert not any("radar" in name or "agreement" in name for name in parameters)
    artifact = _artifact()
    assert artifact["artifact_kind"] == "reference_only_sensitivity"
    assert artifact["fixed_shifts_s"] == [-1.0, 0.0, 1.0]
    assert [entry["shift_s"] for entry in artifact["shifts"]] == [-1.0, 0.0, 1.0]
    assert [entry["shifted_frame0_epoch"] for entry in artifact["shifts"]] == [99.0, 100.0, 101.0]
    _assert_no_forbidden_keys(artifact)
    assert "radar" not in repr(artifact).lower()
    assert "agreement" not in repr(artifact).lower()


def test_prospective_reference_only_requires_exact_operation_and_current_authority_paths():
    parameters = set(inspect.signature(build_reference_only_sensitivity).parameters)
    assert {"authorization", "registry_path", "audit_path", "reference_path"} <= parameters


def test_integer_second_half_open_boundaries_move_exactly_with_sign_convention():
    artifact = _artifact(windows=1)
    expected = [(99.0, 129.0), (100.0, 130.0), (101.0, 131.0)]
    for shift, (start, stop) in zip(artifact["shifts"], expected):
        row = shift["windows"][0]
        assert (row["epoch_start"], row["epoch_stop"]) == (start, stop)
        assert row["hr_n_total"] == 30
        assert row["br_n_total"] == 30
        assert row["hr_admitted"] and row["br_admitted"]


def test_reference_artifact_records_null_safe_empty_totals_and_recovery_stage1():
    empty = _reference().iloc[0:0]
    artifact = _artifact(empty, windows=1)
    for shift in artifact["shifts"]:
        row = shift["windows"][0]
        assert row["median_pr_bpm"] is None and row["median_rr_bpm"] is None
        assert shift["totals"] == {"complete_windows": 1, "hr_admitted": 0, "br_admitted": 0}
        assert shift["recovery_stage1"] == {
            "n": 0, "range_bpm": None, "c_s_bpm": None,
            "within_5_bpm_hit_rate": None, "status": "fail_count",
        }


def test_recovery_stage1_exact_count_range_and_hit_rate_boundaries():
    assert _recovery_stage1(list(range(9)))["status"] == "fail_count"
    range_boundary = [-10, -9, -8, -7, -6, 6, 7, 8, 9, 10]
    result = _recovery_stage1(range_boundary)
    assert result["range_bpm"] == 20.0 and result["status"] == "pass"

    exact_half = [-20, -10, -6, -5, -4, 2, 3, 20, 30, 40]
    result = _recovery_stage1(exact_half)
    assert result["within_5_bpm_hit_rate"] == 0.5
    assert result["status"] == "fail_constant_predictor"


def _authorization(tmp_path):
    registry = write_registry(
        tmp_path / "registry.json", synthetic_registry("validation_opened")
    )
    return authorize_scoring(
        registry, "T001", ReferenceOperation.VALIDATION_SCORING
    )


def _prospective_agreement_context(tmp_path):
    registry1 = tmp_path / "registry_v001.json"
    document = load_registry(DEFAULT_REGISTRY_PATH)
    write_registry(registry1, document)
    reference = tmp_path / "P001_recovery_reference.csv"
    reference.write_text("epoch_utc,pr_bpm\n100,70\n", encoding="utf-8")
    config_sha256 = "b" * 64
    scorer_sha256 = "c" * 64
    # Construct the already-frozen reference-only artifact without reopening P001
    # reference bytes.  Prospective reference construction itself is separately
    # capability-gated; this fixture exercises the later agreement boundary only.
    artifact = _artifact()
    artifact.update(
        subject_id="P001", session_id="P001_recovery", arm="recovery",
        reference_sha256=sha256_file(reference),
        comparator_config_sha256=config_sha256,
        data_role="representation_validation",
    )
    registry2 = tmp_path / "registry_v002.json"
    stage1_audit = tmp_path / "stage1_audit.json"
    transition_label_access_atomically(
        registry_path=registry1, next_registry_path=registry2, audit_path=stage1_audit,
        subject_id="P001", next_state="stage1_reference_only",
        operation=ReferenceOperation.REFERENCE_TIME_SENSITIVITY,
        utc="2030-01-01T00:00:00Z", capture_git_commit="abc123",
        capture_git_dirty=False, config_sha256=config_sha256,
        scorer_sha256=scorer_sha256, session_id="P001_recovery", arm="recovery",
        reference_path=reference, reference_sha256=sha256_file(reference),
    )
    registry = tmp_path / "registry_v003.json"
    audit = tmp_path / "validation_audit.json"
    authorization = transition_label_access_atomically(
        registry_path=registry2, next_registry_path=registry, audit_path=audit,
        subject_id="P001", next_state="validation_opened",
        operation=ReferenceOperation.VALIDATION_SCORING,
        utc="2030-01-01T00:00:01Z", capture_git_commit="abc123",
        capture_git_dirty=False, config_sha256=config_sha256,
        scorer_sha256=scorer_sha256, session_id="P001_recovery", arm="recovery",
        reference_path=reference, reference_sha256=sha256_file(reference),
        previous_audit_path=stage1_audit,
    )
    rows = [
        {"window_index": 0, "radar_valid": True, "hr_bpm": 70.0},
        {"window_index": 1, "radar_valid": True, "hr_bpm": 70.0},
    ]
    return {
        "reference_artifact": artifact,
        "radar_rows_by_shift": {shift: rows for shift in TIME_SHIFTS_S},
        "authorization": authorization,
        "scorer_config_sha256": scorer_sha256,
        "registry_path": registry,
        "audit_path": audit,
        "reference_path": reference,
    }


def test_later_agreement_schema_fixed_shifts_metrics_and_severe_error_boundary(tmp_path):
    reference = _artifact()
    rows = [
        {"window_index": 0, "radar_valid": True, "hr_bpm": 75.0},
        {"window_index": 1, "radar_valid": True, "hr_bpm": 75.000001},
    ]
    artifact = build_agreement_sensitivity(
        reference, {shift: rows for shift in TIME_SHIFTS_S},
        authorization=_authorization(tmp_path), scorer_config_sha256="c" * 64,
    )
    assert artifact["artifact_kind"] == "later_agreement_sensitivity"
    assert artifact["fixed_shifts_s"] == [-1.0, 0.0, 1.0]
    for shift in artifact["shifts"]:
        assert shift["joint_count"] == 2
        assert shift["coverage"] == 1.0
        assert shift["hr_severe_error_count"] == 1
        assert shift["mae_bpm"] == pytest.approx(5.0000005)
        assert shift["rmse_bpm"] == pytest.approx(
            math.sqrt((5.0**2 + 5.000001**2) / 2)
        )
        assert shift["arm_specific_loa"] is None
    _assert_no_forbidden_keys(artifact)


def test_agreement_null_totals_never_emit_nan_or_infinity(tmp_path):
    reference = _artifact(_reference().iloc[0:0])
    rows = [{"window_index": i, "radar_valid": False, "hr_bpm": None} for i in range(2)]
    artifact = build_agreement_sensitivity(
        reference, {shift: rows for shift in TIME_SHIFTS_S},
        authorization=_authorization(tmp_path), scorer_config_sha256="c" * 64,
    )
    for shift in artifact["shifts"]:
        assert shift["joint_count"] == 0
        assert shift["coverage"] is None
        assert shift["mae_bpm"] is None and shift["rmse_bpm"] is None and shift["bias_bpm"] is None


@pytest.mark.parametrize(
    "rows",
    [
        [{"window_index": 0, "radar_valid": True, "hr_bpm": 70.0}],
        [
            {"window_index": 0, "radar_valid": True, "hr_bpm": 70.0},
            {"window_index": 0, "radar_valid": True, "hr_bpm": 71.0},
            {"window_index": 1, "radar_valid": True, "hr_bpm": 70.0},
        ],
    ],
)
def test_agreement_requires_exactly_one_radar_row_per_reference_ledger_identity(tmp_path, rows):
    with pytest.raises(ContractError, match="ledger|duplicate|missing|exactly one"):
        build_agreement_sensitivity(
            _artifact(), {shift: rows for shift in TIME_SHIFTS_S},
            authorization=_authorization(tmp_path), scorer_config_sha256="c" * 64,
        )


def test_agreement_requires_firewall_minted_authorization(tmp_path):
    rows = [{"window_index": i, "radar_valid": True, "hr_bpm": 70.0} for i in range(2)]
    with pytest.raises(ContractError, match="authorization"):
        build_agreement_sensitivity(
            _artifact(), {shift: rows for shift in TIME_SHIFTS_S},
            authorization=object(), scorer_config_sha256="c" * 64,
        )


@pytest.mark.parametrize(
    ("field", "changed"),
    [("hr_bpm", 71.0), ("radar_valid", False)],
)
def test_agreement_shifts_must_reuse_one_identical_frozen_radar_ledger(
    tmp_path, field, changed
):
    rows = [
        {"window_index": 0, "radar_valid": True, "hr_bpm": 70.0},
        {"window_index": 1, "radar_valid": True, "hr_bpm": 72.0},
    ]
    ledgers = {shift: [dict(row) for row in rows] for shift in TIME_SHIFTS_S}
    ledgers[1.0][0][field] = changed
    with pytest.raises(ContractError, match="identical|frozen|radar ledger"):
        build_agreement_sensitivity(
            _artifact(), ledgers,
            authorization=_authorization(tmp_path), scorer_config_sha256="c" * 64,
        )


def test_prospective_agreement_requires_current_registry_audit_and_reference_invocation_paths():
    parameters = set(inspect.signature(build_agreement_sensitivity).parameters)
    assert {"registry_path", "audit_path", "reference_path"} <= parameters


def test_agreement_rejects_recovery_stage1_capability_even_when_every_hash_is_bound(tmp_path):
    registry = tmp_path / "registry_v001.json"
    registry.write_bytes(DEFAULT_REGISTRY_PATH.read_bytes())
    registry.with_suffix(registry.suffix + ".sha256").write_bytes(
        DEFAULT_REGISTRY_PATH.with_suffix(DEFAULT_REGISTRY_PATH.suffix + ".sha256").read_bytes()
    )
    reference = tmp_path / "P001_recovery_reference.csv"
    reference.write_text("epoch_utc,pr_bpm\n100,70\n", encoding="utf-8")
    next_registry = tmp_path / "registry_v002.json"
    audit = tmp_path / "stage1_audit.json"
    authorization = transition_label_access_atomically(
        registry_path=registry, next_registry_path=next_registry, audit_path=audit,
        subject_id="P001", next_state="stage1_reference_only",
        operation=ReferenceOperation.RECOVERY_STAGE1,
        utc="2030-01-01T00:00:00Z", capture_git_commit="abc123",
        capture_git_dirty=False, config_sha256="b" * 64, scorer_sha256="c" * 64,
        session_id="P001_recovery", arm="recovery", reference_path=reference,
        reference_sha256=sha256_file(reference),
    )
    artifact = _artifact()
    artifact.update(
        subject_id="P001", session_id="P001_recovery", arm="recovery",
        reference_sha256=sha256_file(reference),
        comparator_config_sha256="b" * 64,
        data_role="representation_validation",
    )
    rows = [{"window_index": i, "radar_valid": True, "hr_bpm": 70.0} for i in range(2)]
    with pytest.raises(ContractError, match="scoring|operation|RECOVERY_STAGE1"):
        build_agreement_sensitivity(
            artifact, {shift: rows for shift in TIME_SHIFTS_S},
            authorization=authorization, scorer_config_sha256="c" * 64,
        )


@pytest.mark.parametrize("mismatch", ["role", "session", "arm", "reference", "config"])
def test_prospective_agreement_rejects_capability_semantic_binding_mismatch(tmp_path, mismatch):
    context = _prospective_agreement_context(tmp_path)
    artifact = context["reference_artifact"]
    if mismatch == "role":
        artifact["data_role"] = "final_evaluation"
    else:
        reference = context["reference_path"]
        if mismatch == "reference":
            reference = tmp_path / "P001_recovery_foreign_reference.csv"
            reference.write_text("different reference", encoding="utf-8")
        overrides = {
            "session_id": "P001_natural" if mismatch == "session" else "P001_recovery",
            "arm": "natural" if mismatch == "arm" else "recovery",
            "reference_path": str(reference.resolve()),
            "reference_sha256": sha256_file(reference),
            "config_sha256": "d" * 64 if mismatch == "config" else "b" * 64,
        }
        context["authorization"] = replace(context["authorization"], **overrides)
    with pytest.raises(ContractError, match="role|session|arm|reference|config|binding"):
        build_agreement_sensitivity(
            artifact, context["radar_rows_by_shift"],
            authorization=context["authorization"], scorer_config_sha256="c" * 64,
        )


@pytest.mark.parametrize("mismatch", ["registry", "audit", "reference"])
def test_prospective_agreement_rejects_stale_or_tampered_authority_files(tmp_path, mismatch):
    context = _prospective_agreement_context(tmp_path)
    if mismatch == "registry":
        context["registry_path"] = write_registry_revision(
            context["registry_path"], tmp_path / "registry_v004.json", subject_updates={}
        )
    elif mismatch == "audit":
        context["audit_path"].write_text("tampered audit", encoding="utf-8")
    else:
        context["reference_path"].write_text("tampered reference", encoding="utf-8")
    with pytest.raises(ContractError, match="registry|audit|reference|hash|binding"):
        build_agreement_sensitivity(**context)
