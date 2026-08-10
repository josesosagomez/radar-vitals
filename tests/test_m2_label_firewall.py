"""Prospective-label state machine, audit binding, and privacy firewall tests."""
from __future__ import annotations

import copy
import dataclasses
import inspect
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "m2"))

from builders import acquisition_metadata, synthetic_registry, write_registry  # noqa: E402
from src.m2.acquisition_metadata import ContractError, validate_acquisition_metadata  # noqa: E402
from src.m2.cohort_registry import DEFAULT_REGISTRY_PATH, load_registry  # noqa: E402
from src.m2.common import canonical_json_bytes, sha256_file  # noqa: E402
from src.m2.label_firewall import (  # noqa: E402
    ReferenceOperation,
    ScoringAuthorization,
    authorize_scoring,
    guarded_reference_bytes,
    write_transition_audit,
)
import src.m2.label_firewall as label_firewall  # noqa: E402


def _real_state_registry(tmp_path: Path, *, subject_index: int, state: str) -> tuple[Path, str]:
    document = copy.deepcopy(load_registry(DEFAULT_REGISTRY_PATH))
    subject = document["subjects"][subject_index]
    subject_id = subject["subject_id"]
    registry1 = write_registry(tmp_path / f"registry_{subject_id}_v001.json", document)
    if state == "sealed":
        return registry1, subject_id

    arm = "natural" if state == "validation_opened" else "recovery"
    session_id = f"{subject_id}_{arm}"
    reference = tmp_path / f"{session_id}_reference.csv"
    reference.write_bytes(b"reference")
    registry2 = tmp_path / f"registry_{subject_id}_v002.json"
    audit1 = tmp_path / f"{subject_id}_stage1_audit.json"
    label_firewall.transition_label_access_atomically(
        registry_path=registry1,
        next_registry_path=registry2,
        audit_path=audit1,
        subject_id=subject_id,
        next_state="stage1_reference_only",
        operation=ReferenceOperation.REFERENCE_TIME_SENSITIVITY,
        utc="2030-01-01T00:00:00Z",
        capture_git_commit="abc123",
        capture_git_dirty=False,
        config_sha256="b" * 64,
        scorer_sha256="c" * 64,
        session_id=session_id,
        arm=arm,
        reference_path=reference,
        reference_sha256=sha256_file(reference),
    )
    if state == "stage1_reference_only":
        return registry2, subject_id

    registry3 = tmp_path / f"registry_{subject_id}_v003.json"
    audit2 = tmp_path / f"{subject_id}_{state}_audit.json"
    if state == "validation_opened":
        label_firewall.transition_label_access_atomically(
            registry_path=registry2,
            next_registry_path=registry3,
            audit_path=audit2,
            subject_id=subject_id,
            next_state=state,
            operation=ReferenceOperation.VALIDATION_SCORING,
            utc="2030-01-01T00:00:01Z",
            capture_git_commit="abc123",
            capture_git_dirty=False,
            config_sha256="b" * 64,
            scorer_sha256="c" * 64,
            session_id=session_id,
            arm=arm,
            reference_path=reference,
            reference_sha256=sha256_file(reference),
            previous_audit_path=audit1,
        )
    elif state == "final_scored":
        score = tmp_path / f"{subject_id}_sealed_score.json"

        def build_score(capability, _staged_path):
            return {
                "schema": "m2_sealed_scoring_output_v1",
                "state": "sealed",
                "subject_id": capability.subject_id,
                "session_id": capability.session_id,
                "arm": capability.arm,
                "data_role": capability.data_role,
                "operation": capability.operation.value,
                "config_sha256": capability.config_sha256,
                "scorer_sha256": capability.scorer_sha256,
                "reference_path": capability.reference_path,
                "reference_sha256": capability.reference_sha256,
            }

        label_firewall.transition_label_access_atomically(
            registry_path=registry2,
            next_registry_path=registry3,
            audit_path=audit2,
            subject_id=subject_id,
            next_state=state,
            operation=ReferenceOperation.FINAL_SCORING,
            utc="2030-01-01T00:00:01Z",
            capture_git_commit="abc123",
            capture_git_dirty=False,
            config_sha256="b" * 64,
            scorer_sha256="c" * 64,
            session_id=session_id,
            arm=arm,
            reference_path=reference,
            reference_sha256=sha256_file(reference),
            previous_audit_path=audit1,
            score_artifact_path=score,
            score_artifact_builder=build_score,
        )
    else:
        raise AssertionError(f"unsupported real-state fixture {state!r}")
    return registry3, subject_id


def _fully_bound_prospective_capability(
    registry: Path,
    subject_id: str,
    operation: ReferenceOperation,
    reference: Path,
    *,
    session_id: str,
    arm: str,
) -> ScoringAuthorization:
    """Mint every authority required before a prospective P* reference read."""
    document = load_registry(registry)
    subject = next(row for row in document["subjects"] if row["subject_id"] == subject_id)
    audit = registry.with_name(f"{registry.stem}_{session_id}_audit.json")
    audit.write_bytes(canonical_json_bytes({
        "schema": "m2_label_access_transition_v1",
        "subject_id": subject_id,
        "session_id": session_id,
        "arm": arm,
        "data_role": subject["data_role"],
        "permitted_operation": operation.value,
        "registry_sha256": sha256_file(registry),
        "config_sha256": "b" * 64,
        "scorer_sha256": "c" * 64,
        "reference_path": str(reference.resolve()),
        "reference_sha256": sha256_file(reference),
    }))
    return authorize_scoring(
        registry,
        subject_id,
        operation,
        session_id=session_id,
        arm=arm,
        reference_path=reference,
        reference_sha256=sha256_file(reference),
        audit_path=audit,
        audit_sha256=sha256_file(audit),
        config_sha256="b" * 64,
        scorer_sha256="c" * 64,
    )


def test_sealed_fails_before_reference_path_is_touched(tmp_path):
    registry = write_registry(tmp_path / "sealed.json", synthetic_registry("sealed"))
    missing = tmp_path / "must_not_be_opened.csv"
    with pytest.raises(ContractError, match="read forbidden"):
        guarded_reference_bytes(
            registry, "T001", ReferenceOperation.RECOVERY_STAGE1, missing
        )


@pytest.mark.parametrize(
    "operation",
    [ReferenceOperation.RECOVERY_STAGE1, ReferenceOperation.REFERENCE_TIME_SENSITIVITY],
)
def test_stage1_state_allows_only_reference_only_real_reads(tmp_path, operation):
    registry = write_registry(
        tmp_path / "stage1.json", synthetic_registry("stage1_reference_only")
    )
    reference = tmp_path / "T001_recovery_reference.csv"
    reference.write_bytes(b"reference bytes")
    assert guarded_reference_bytes(registry, "T001", operation, reference) == b"reference bytes"
    with pytest.raises(ContractError):
        guarded_reference_bytes(
            registry, "T001", ReferenceOperation.VALIDATION_SCORING, reference
        )


def test_validation_opened_allows_validation_scoring_only(tmp_path):
    document = copy.deepcopy(load_registry(DEFAULT_REGISTRY_PATH))
    registry1 = write_registry(tmp_path / "registry_v001.json", document)
    reference = tmp_path / "P001_natural_reference.csv"
    reference.write_bytes(b"validation")
    registry2 = tmp_path / "registry_v002.json"
    stage1_audit = tmp_path / "stage1_audit.json"
    label_firewall.transition_label_access_atomically(
        registry_path=registry1, next_registry_path=registry2, audit_path=stage1_audit,
        subject_id="P001", next_state="stage1_reference_only",
        operation=ReferenceOperation.REFERENCE_TIME_SENSITIVITY,
        utc="2030-01-01T00:00:00Z", capture_git_commit="abc123",
        capture_git_dirty=False, config_sha256="b" * 64, scorer_sha256="c" * 64,
        session_id="P001_natural", arm="natural", reference_path=reference,
        reference_sha256=sha256_file(reference),
    )
    registry = tmp_path / "registry_v003.json"
    audit = tmp_path / "validation_audit.json"
    authorization = label_firewall.transition_label_access_atomically(
        registry_path=registry2, next_registry_path=registry, audit_path=audit,
        subject_id="P001", next_state="validation_opened",
        operation=ReferenceOperation.VALIDATION_SCORING,
        utc="2030-01-01T00:00:01Z", capture_git_commit="abc123",
        capture_git_dirty=False, config_sha256="b" * 64, scorer_sha256="c" * 64,
        session_id="P001_natural", arm="natural", reference_path=reference,
        reference_sha256=sha256_file(reference), previous_audit_path=stage1_audit,
    )
    assert guarded_reference_bytes(
        registry, "P001", ReferenceOperation.VALIDATION_SCORING, reference,
        capability=authorization,
    ) == b"validation"
    assert authorization.registry_sha256 == sha256_file(registry)
    for operation in (
        ReferenceOperation.RECOVERY_STAGE1,
        ReferenceOperation.REFERENCE_TIME_SENSITIVITY,
        ReferenceOperation.FINAL_SCORING,
    ):
        with pytest.raises(ContractError):
            guarded_reference_bytes(registry, "P001", operation, reference)


def test_final_scored_allows_final_scoring_only(tmp_path):
    registry2, prior_audit, reference = _stage1_final_subject(tmp_path)
    registry = tmp_path / "registry_v003.json"
    audit = tmp_path / "final_audit.json"
    score = tmp_path / "sealed_score.json"

    def build_score(capability, staged_path):
        Path(staged_path).write_bytes(canonical_json_bytes({
            "schema": "m2_sealed_scoring_output_v1", "state": "sealed",
            "subject_id": capability.subject_id, "session_id": capability.session_id,
            "arm": capability.arm, "data_role": capability.data_role,
            "operation": capability.operation.value,
            "config_sha256": capability.config_sha256,
            "scorer_sha256": capability.scorer_sha256,
            "reference_path": capability.reference_path,
            "reference_sha256": capability.reference_sha256,
        }))

    authorization = label_firewall.transition_label_access_atomically(
        registry_path=registry2, next_registry_path=registry, audit_path=audit,
        subject_id="P006", next_state="final_scored",
        operation=ReferenceOperation.FINAL_SCORING,
        utc="2030-01-01T00:00:01Z", capture_git_commit="abc123",
        capture_git_dirty=False, config_sha256="a" * 64, scorer_sha256="b" * 64,
        session_id="P006_recovery", arm="recovery", reference_path=reference,
        reference_sha256=sha256_file(reference), previous_audit_path=prior_audit,
        score_artifact_path=score, score_artifact_builder=build_score,
    )
    assert guarded_reference_bytes(
        registry, "P006", ReferenceOperation.FINAL_SCORING, reference,
        capability=authorization,
    ) == b"reference"
    assert authorization.data_role == "final_evaluation"
    with pytest.raises(ContractError):
        guarded_reference_bytes(
            registry, "P006", ReferenceOperation.VALIDATION_SCORING, reference
        )


def test_prospective_reference_read_without_fully_bound_capability_fails_closed(tmp_path):
    registry, subject = _real_state_registry(
        tmp_path, subject_index=0, state="validation_opened"
    )
    reference = tmp_path / "P001_natural_reference.csv"
    reference.write_bytes(b"must remain unreadable without complete authority")
    with pytest.raises(ContractError, match="capability"):
        guarded_reference_bytes(
            registry, subject, ReferenceOperation.VALIDATION_SCORING, reference
        )

    with pytest.raises(ContractError, match="atomic label transition|minted"):
        authorize_scoring(
            registry, subject, ReferenceOperation.VALIDATION_SCORING
        )


def test_direct_registry_revision_and_caller_fabricated_audit_cannot_authorize_scoring(tmp_path):
    registry, subject = _real_state_registry(
        tmp_path, subject_index=0, state="validation_opened"
    )
    reference = tmp_path / "P001_natural_reference.csv"
    reference.write_bytes(b"must require atomic provenance")
    with pytest.raises(ContractError, match="atomic label transition|minted"):
        _fully_bound_prospective_capability(
            registry, subject, ReferenceOperation.VALIDATION_SCORING, reference,
            session_id="P001_natural", arm="natural",
        )


def test_forged_public_stage1_revision_and_minimal_audit_cannot_enter_validation(tmp_path):
    """Hash chaining alone must not substitute for atomic label-firewall provenance."""
    registry1 = write_registry(
        tmp_path / "registry_v001.json",
        copy.deepcopy(load_registry(DEFAULT_REGISTRY_PATH)),
    )
    forged = copy.deepcopy(load_registry(registry1))
    forged["revision"] = 2
    forged["previous_registry_sha256"] = sha256_file(registry1)
    forged["subjects"][0]["label_state"] = "stage1_reference_only"
    registry2 = tmp_path / "registry_v002.json"
    registry2.write_bytes(canonical_json_bytes(forged))
    registry2.with_suffix(registry2.suffix + ".sha256").write_text(
        sha256_file(registry2) + "\n", encoding="ascii"
    )

    # This is the smallest document the former bypass accepted: it names the current
    # subject/state/hash but has no complete atomic transition provenance or bindings.
    fabricated_audit = tmp_path / "fabricated_stage1_audit.json"
    fabricated_audit.write_bytes(canonical_json_bytes({
        "schema": "m2_label_access_transition_v1",
        "subject_id": "P001",
        "to_state": "stage1_reference_only",
        "registry_sha256": sha256_file(registry2),
    }))
    reference = tmp_path / "P001_natural_reference.csv"
    reference.write_bytes(b"reference")
    registry3 = tmp_path / "registry_v003.json"
    validation_audit = tmp_path / "validation_audit.json"
    with pytest.raises(ContractError, match="prior|audit|provenance|atomic|semantic"):
        label_firewall.transition_label_access_atomically(
            registry_path=registry2,
            next_registry_path=registry3,
            audit_path=validation_audit,
            subject_id="P001",
            next_state="validation_opened",
            operation=ReferenceOperation.VALIDATION_SCORING,
            utc="2030-01-01T00:00:01Z",
            capture_git_commit="abc123",
            capture_git_dirty=False,
            config_sha256="b" * 64,
            scorer_sha256="c" * 64,
            session_id="P001_natural",
            arm="natural",
            reference_path=reference,
            reference_sha256=sha256_file(reference),
            previous_audit_path=fabricated_audit,
        )
    assert not registry3.exists()
    assert not registry3.with_suffix(registry3.suffix + ".sha256").exists()
    assert not validation_audit.exists()


def test_wrong_role_operation_combinations_never_mint_authorization(tmp_path):
    validation_registry, validation_subject = _real_state_registry(
        tmp_path, subject_index=0, state="validation_opened"
    )
    final_registry, final_subject = _real_state_registry(
        tmp_path, subject_index=5, state="final_scored"
    )
    with pytest.raises(ContractError):
        authorize_scoring(validation_registry, validation_subject, ReferenceOperation.FINAL_SCORING)
    with pytest.raises(ContractError):
        authorize_scoring(final_registry, final_subject, ReferenceOperation.VALIDATION_SCORING)


def test_transition_audits_bind_atomic_hash_inputs_prior_audit_and_refuse_overwrite(tmp_path):
    sealed = write_registry(tmp_path / "sealed.json", synthetic_registry("sealed"))
    audit1 = write_transition_audit(
        tmp_path / "audit1.json", registry_path=sealed, subject_id="T001",
        next_state="stage1_reference_only", operation=ReferenceOperation.RECOVERY_STAGE1,
        utc="2030-01-01T00:00:00Z", capture_git_commit="abc123", capture_git_dirty=False,
        config_sha256="a" * 64, scorer_sha256="b" * 64,
    )
    first = json.loads(audit1.read_text(encoding="utf-8"))
    assert first["registry_sha256"] == sha256_file(sealed)
    assert first["config_sha256"] == "a" * 64
    assert first["scorer_sha256"] == "b" * 64
    assert first["previous_audit_sha256"] is None
    with pytest.raises(ContractError, match="overwrite"):
        write_transition_audit(
            audit1, registry_path=sealed, subject_id="T001",
            next_state="stage1_reference_only", operation=ReferenceOperation.RECOVERY_STAGE1,
            utc="2030-01-01T00:00:00Z", capture_git_commit="abc123",
            capture_git_dirty=False, config_sha256="a" * 64, scorer_sha256="b" * 64,
        )

    stage1 = write_registry(
        tmp_path / "stage1.json", synthetic_registry("stage1_reference_only")
    )
    audit2 = write_transition_audit(
        tmp_path / "audit2.json", registry_path=stage1, subject_id="T001",
        next_state="validation_opened", operation=ReferenceOperation.VALIDATION_SCORING,
        utc="2030-01-01T00:00:01Z", capture_git_commit="abc123", capture_git_dirty=False,
        config_sha256="c" * 64, scorer_sha256="d" * 64, previous_audit_path=audit1,
    )
    assert json.loads(audit2.read_text())["previous_audit_sha256"] == sha256_file(audit1)


def test_forged_scoring_authorization_is_rejected():
    with pytest.raises(ContractError, match="minted only"):
        ScoringAuthorization(
            subject_id="P001", data_role="representation_validation",
            operation=ReferenceOperation.VALIDATION_SCORING,
            registry_sha256="a" * 64, _token=object(),
        )


def test_reference_capability_binds_subject_session_arm_path_hash_and_all_authorities():
    fields = {field.name for field in dataclasses.fields(ScoringAuthorization)}
    assert {
        "subject_id", "session_id", "arm", "reference_path", "reference_sha256",
        "registry_sha256", "audit_sha256", "config_sha256", "scorer_sha256",
    } <= fields
    guarded_parameters = set(inspect.signature(guarded_reference_bytes).parameters)
    assert "capability" in guarded_parameters


def test_stage1_reference_read_rejects_foreign_session_arm_path_and_hash(tmp_path):
    registry = write_registry(
        tmp_path / "stage1.json", synthetic_registry("stage1_reference_only")
    )
    foreign = tmp_path / "T999_natural_reference.csv"
    foreign.write_bytes(b"foreign reference")
    with pytest.raises(ContractError, match="subject|session|arm|path|hash|capability"):
        guarded_reference_bytes(
            registry, "T001", ReferenceOperation.RECOVERY_STAGE1, foreign
        )


def test_reference_only_sensitivity_requires_registry_audit_config_scorer_capability():
    from src.m2.time_sensitivity import build_reference_only_sensitivity

    parameters = set(inspect.signature(build_reference_only_sensitivity).parameters)
    assert "authorization" in parameters or "capability" in parameters


def test_one_atomic_transition_api_returns_capability_and_exposes_failure_injection():
    api = getattr(label_firewall, "transition_label_access_atomically", None)
    assert callable(api), "label firewall needs one atomic registry+audit transition API"
    parameters = set(inspect.signature(api).parameters)
    assert {"next_registry_path", "audit_path", "failure_injector"} <= parameters


def test_atomic_transition_injected_failure_leaves_no_registry_audit_or_state(tmp_path):
    api = getattr(label_firewall, "transition_label_access_atomically", None)
    assert callable(api)
    registry = write_registry(tmp_path / "registry_v001.json", synthetic_registry("sealed"))
    next_registry = tmp_path / "registry_v002.json"
    audit = tmp_path / "audit.json"
    reference = tmp_path / "T001_recovery_reference.csv"
    reference.write_bytes(b"reference")

    def fail_after_staging(_stage):
        raise RuntimeError("injected transition failure")

    with pytest.raises(RuntimeError, match="injected"):
        api(
            registry_path=registry,
            next_registry_path=next_registry,
            audit_path=audit,
            subject_id="T001",
            next_state="stage1_reference_only",
            operation=ReferenceOperation.RECOVERY_STAGE1,
            utc="2030-01-01T00:00:00Z",
            capture_git_commit="abc123",
            capture_git_dirty=False,
            config_sha256="a" * 64,
            scorer_sha256="b" * 64,
            session_id="T001_recovery",
            arm="recovery",
            reference_path=reference,
            reference_sha256=sha256_file(reference),
            failure_injector=fail_after_staging,
        )
    assert not next_registry.exists()
    assert not audit.exists()
    assert load_registry(registry)["subjects"][0]["label_state"] == "sealed"


def test_final_scored_atomic_publication_requires_bound_sealed_score_artifact():
    parameters = set(
        inspect.signature(label_firewall.transition_label_access_atomically).parameters
    )
    assert {"score_artifact_path", "score_artifact_builder"} <= parameters


def test_final_scored_uses_in_transaction_score_builder_not_caller_supplied_hash():
    parameters = set(
        inspect.signature(label_firewall.transition_label_access_atomically).parameters
    )
    assert "score_artifact_builder" in parameters


def _stage1_final_subject(tmp_path):
    document = copy.deepcopy(load_registry(DEFAULT_REGISTRY_PATH))
    registry1 = write_registry(tmp_path / "registry_v001.json", document)
    reference = tmp_path / "P006_recovery_reference.csv"
    reference.write_bytes(b"reference")
    registry2 = tmp_path / "registry_v002.json"
    prior_audit = tmp_path / "stage1_audit.json"
    label_firewall.transition_label_access_atomically(
        registry_path=registry1, next_registry_path=registry2, audit_path=prior_audit,
        subject_id="P006", next_state="stage1_reference_only",
        operation=ReferenceOperation.REFERENCE_TIME_SENSITIVITY,
        utc="2030-01-01T00:00:00Z", capture_git_commit="abc123",
        capture_git_dirty=False, config_sha256="a" * 64, scorer_sha256="b" * 64,
        session_id="P006_recovery", arm="recovery", reference_path=reference,
        reference_sha256=sha256_file(reference),
    )
    return registry2, prior_audit, reference


def test_final_score_builder_failure_rolls_back_registry_audit_digest_and_score(tmp_path):
    registry, prior_audit, reference = _stage1_final_subject(tmp_path)
    next_registry = tmp_path / "registry_v003.json"
    audit = tmp_path / "final_audit.json"
    score = tmp_path / "sealed_score.json"

    def fail_builder(capability, staged_path):
        assert capability.operation is ReferenceOperation.FINAL_SCORING
        assert capability.subject_id == "P006"
        assert Path(staged_path) != score
        raise RuntimeError("injected scoring failure")

    with pytest.raises(RuntimeError, match="injected scoring"):
        label_firewall.transition_label_access_atomically(
            registry_path=registry, next_registry_path=next_registry, audit_path=audit,
            subject_id="P006", next_state="final_scored",
            operation=ReferenceOperation.FINAL_SCORING,
            utc="2030-01-01T00:00:01Z", capture_git_commit="abc123",
            capture_git_dirty=False, config_sha256="a" * 64, scorer_sha256="b" * 64,
            session_id="P006_recovery", arm="recovery", reference_path=reference,
            reference_sha256=sha256_file(reference), previous_audit_path=prior_audit,
            score_artifact_path=score, score_artifact_builder=fail_builder,
        )
    assert not next_registry.exists()
    assert not next_registry.with_suffix(".json.sha256").exists()
    assert not audit.exists()
    assert not score.exists()


def test_final_scored_rejects_preexisting_caller_fabricated_placeholder(tmp_path):
    registry, prior_audit, reference = _stage1_final_subject(tmp_path)
    score = tmp_path / "sealed_score.json"
    score.write_bytes(canonical_json_bytes({
        "schema": "m2_sealed_scoring_output_v1", "state": "sealed",
        "subject_id": "P006", "session_id": "P006_recovery", "arm": "recovery",
        "data_role": "final_evaluation", "scorer_sha256": "b" * 64,
    }))
    with pytest.raises(ContractError, match="preexisting|overwrite|builder|transaction|placeholder"):
        label_firewall.transition_label_access_atomically(
            registry_path=registry, next_registry_path=tmp_path / "registry_v003.json",
            audit_path=tmp_path / "final_audit.json", subject_id="P006",
            next_state="final_scored", operation=ReferenceOperation.FINAL_SCORING,
            utc="2030-01-01T00:00:01Z", capture_git_commit="abc123",
            capture_git_dirty=False, config_sha256="a" * 64, scorer_sha256="b" * 64,
            session_id="P006_recovery", arm="recovery", reference_path=reference,
            reference_sha256=sha256_file(reference), previous_audit_path=prior_audit,
            score_artifact_path=score, score_artifact_sha256=sha256_file(score),
        )


def test_atomic_transition_rejects_semantically_invalid_prior_audit_without_publication(tmp_path):
    registry = write_registry(
        tmp_path / "registry_v001.json", synthetic_registry("stage1_reference_only")
    )
    bogus_prior = tmp_path / "prior_audit.json"
    bogus_prior.write_text('{"schema":"not_a_transition_audit"}\n', encoding="utf-8")
    reference = tmp_path / "T001_recovery_reference.csv"
    reference.write_bytes(b"reference")
    next_registry = tmp_path / "registry_v002.json"
    audit = tmp_path / "audit.json"
    with pytest.raises(ContractError, match="prior|audit|chain|schema|semantic"):
        label_firewall.transition_label_access_atomically(
            registry_path=registry, next_registry_path=next_registry, audit_path=audit,
            subject_id="T001", next_state="validation_opened",
            operation=ReferenceOperation.VALIDATION_SCORING,
            utc="2030-01-01T00:00:00Z", capture_git_commit="abc123",
            capture_git_dirty=False, config_sha256="a" * 64, scorer_sha256="b" * 64,
            session_id="T001_recovery", arm="recovery", reference_path=reference,
            reference_sha256=sha256_file(reference), previous_audit_path=bogus_prior,
        )
    assert not next_registry.exists()
    assert not audit.exists()


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "parq_answer", "diagnosis", "medical_condition", "medication",
        "pregnancy_status", "participant_name", "consent_document_path",
        "symptom_notes", "eligibility_reason", "health_notes", "screening_answers",
    ],
)
def test_private_or_health_keys_are_rejected_recursively(forbidden_key):
    metadata = acquisition_metadata()
    metadata["nested"] = {forbidden_key: "must not be stored"}
    with pytest.raises(ContractError, match="forbidden"):
        validate_acquisition_metadata(metadata)


def test_health_narrative_is_rejected_even_when_hidden_in_allowed_free_text():
    metadata = acquisition_metadata()
    metadata["scene_non_health_notes"] = (
        "Participant reports chest pain and a cardiovascular diagnosis."
    )
    with pytest.raises(ContractError, match="health|free text|privacy"):
        validate_acquisition_metadata(metadata)


def test_health_narrative_is_rejected_in_generic_extension_free_text():
    metadata = acquisition_metadata()
    metadata["operator_notes"] = "Participant reports chest pain during the visit."
    with pytest.raises(ContractError, match="health|free text|privacy|unknown"):
        validate_acquisition_metadata(metadata)


@pytest.mark.parametrize(
    "basename",
    ["../P001_recovery_reference.csv", "Jose_recovery.csv", "P001 recovery.csv", "P001.csv"],
)
def test_reference_basenames_are_pseudonymous_relative_files_only(basename):
    metadata = acquisition_metadata(
        subject_id="P001", data_role="representation_validation", synthetic_fixture=False
    )
    metadata["expected_reference_basename"] = basename
    with pytest.raises(ContractError, match="pseudonymous CSV basename"):
        validate_acquisition_metadata(metadata)
