"""Executable prospective-reference label firewall and immutable transition audits."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping
from uuid import uuid4

from .cohort_registry import (
    LABEL_TRANSITIONS,
    _write_prospective_label_revision,
    validate_registry_history,
    write_registry_revision,
)
from .common import (
    ContractError,
    read_json_object,
    require_nonempty_string,
    require_sha256,
    sha256_file,
    write_new_json,
)


class ReferenceOperation(str, Enum):
    RECOVERY_STAGE1 = "recovery_stage1"
    REFERENCE_TIME_SENSITIVITY = "reference_time_sensitivity"
    VALIDATION_SCORING = "validation_scoring"
    FINAL_SCORING = "final_scoring"


_SCORING_TOKEN = object()
_ACTIVE_ATOMIC_SCORING: dict[str, Mapping[str, object]] = {}


@dataclass(frozen=True)
class ScoringAuthorization:
    subject_id: str
    data_role: str
    operation: ReferenceOperation
    registry_sha256: str
    _token: object
    session_id: str = ""
    arm: str = ""
    reference_path: str = ""
    reference_sha256: str = ""
    audit_path: str = ""
    audit_sha256: str = ""
    config_sha256: str = ""
    scorer_sha256: str = ""
    _transaction_nonce: str = ""

    def __post_init__(self) -> None:
        if self._token is not _SCORING_TOKEN:
            raise ContractError("ScoringAuthorization is minted only by the label firewall")


def _subject_record(registry_path: str | Path, subject_id: str) -> tuple[dict, str]:
    document = validate_registry_history(registry_path)[-1]
    subject = next(
        (entry for entry in document["subjects"] if entry["subject_id"] == subject_id),
        None,
    )
    if subject is None:
        raise ContractError(f"subject {subject_id!r} is absent from cohort registry")
    return subject, sha256_file(registry_path)


def _operation_allowed(subject: Mapping[str, object], operation: ReferenceOperation) -> bool:
    state = subject["label_state"]
    role = subject["data_role"]
    if state == "sealed":
        return False
    if state == "stage1_reference_only":
        return operation in {
            ReferenceOperation.RECOVERY_STAGE1,
            ReferenceOperation.REFERENCE_TIME_SENSITIVITY,
        }
    if state == "validation_opened":
        return role == "representation_validation" and operation is ReferenceOperation.VALIDATION_SCORING
    if state == "final_scored":
        return role == "final_evaluation" and operation is ReferenceOperation.FINAL_SCORING
    return False


def authorize_scoring(
    registry_path: str | Path,
    subject_id: str,
    operation: ReferenceOperation | str,
    *,
    session_id: str = "",
    arm: str = "",
    reference_path: str | Path | None = None,
    reference_sha256: str = "",
    audit_path: str | Path | None = None,
    audit_sha256: str = "",
    config_sha256: str = "",
    scorer_sha256: str = "",
) -> ScoringAuthorization:
    try:
        parsed_operation = ReferenceOperation(operation)
    except ValueError:
        raise ContractError(f"unknown reference operation {operation!r}") from None
    if parsed_operation not in {
        ReferenceOperation.VALIDATION_SCORING,
        ReferenceOperation.FINAL_SCORING,
    }:
        raise ContractError("authorize_scoring accepts only declared scoring operations")
    subject, digest = _subject_record(registry_path, subject_id)
    if subject_id.startswith("P"):
        raise ContractError(
            "prospective scoring capabilities are minted only by the atomic label transition"
        )
    if not _operation_allowed(subject, parsed_operation):
        raise ContractError(
            f"reference operation {parsed_operation.value!r} is forbidden while "
            f"{subject_id} is {subject['label_state']!r} in role {subject['data_role']!r}"
        )
    return ScoringAuthorization(
        subject_id=subject_id,
        data_role=str(subject["data_role"]),
        operation=parsed_operation,
        registry_sha256=digest,
        _token=_SCORING_TOKEN,
        session_id=session_id,
        arm=arm,
        reference_path=str(Path(reference_path).resolve()) if reference_path else "",
        reference_sha256=reference_sha256,
        audit_path=str(Path(audit_path).resolve()) if audit_path else "",
        audit_sha256=audit_sha256,
        config_sha256=config_sha256,
        scorer_sha256=scorer_sha256,
    )


def guarded_reference_bytes(
    registry_path: str | Path,
    subject_id: str,
    operation: ReferenceOperation | str,
    reference_path: str | Path,
    *,
    capability: ScoringAuthorization | None = None,
) -> bytes:
    """Fail on state/role before touching the prospective reference path."""
    try:
        parsed_operation = ReferenceOperation(operation)
    except ValueError:
        raise ContractError(f"unknown reference operation {operation!r}") from None
    subject, _ = _subject_record(registry_path, subject_id)
    active_atomic = (
        _ACTIVE_ATOMIC_SCORING.get(capability._transaction_nonce)
        if type(capability) is ScoringAuthorization and capability._transaction_nonce
        else None
    )
    provisional_final_read = (
        active_atomic is not None
        and parsed_operation is ReferenceOperation.FINAL_SCORING
        and active_atomic.get("subject_id") == subject_id
        and active_atomic.get("from_state") == subject["label_state"]
        and active_atomic.get("to_state") == "final_scored"
    )
    if not _operation_allowed(subject, parsed_operation) and not provisional_final_read:
        raise ContractError(
            f"reference read forbidden for {subject_id}: state={subject['label_state']}, "
            f"role={subject['data_role']}, operation={parsed_operation.value}"
        )
    if subject_id.startswith("P") and capability is None:
        raise ContractError(
            "prospective P001-P015 reference reads require a fully bound capability"
        )
    requested_path = Path(reference_path).resolve()
    basename = requested_path.name
    if not basename.startswith(subject_id + "_"):
        raise ContractError("reference path does not bind the authorized subject/session")
    if parsed_operation is ReferenceOperation.RECOVERY_STAGE1 and "_recovery_" not in basename:
        raise ContractError("RECOVERY_STAGE1 capability is valid only for the recovery arm")
    if capability is not None:
        if type(capability) is not ScoringAuthorization:
            raise ContractError("reference capability has the wrong type")
        required_capability_values = (
            capability.session_id,
            capability.arm,
            capability.reference_path,
            capability.reference_sha256,
            capability.audit_path,
            capability.config_sha256,
            capability.scorer_sha256,
        )
        if active_atomic is None:
            required_capability_values += (capability.audit_sha256,)
        if (
            capability._token is not _SCORING_TOKEN
            or not all(required_capability_values)
            or capability.subject_id != subject_id
            or capability.operation is not parsed_operation
            or capability.registry_sha256 != sha256_file(registry_path)
            or (capability.reference_path and Path(capability.reference_path) != requested_path)
        ):
            raise ContractError("reference capability subject/session/arm/path authority mismatch")
        if capability.session_id and not basename.startswith(capability.session_id + "_"):
            raise ContractError("reference capability session does not match reference path")
        if capability.arm and f"_{capability.arm}_" not in basename:
            raise ContractError("reference capability arm does not match reference path")
        require_scoring_authorization(
            capability,
            subject_id=subject_id,
            session_id=capability.session_id,
            arm=capability.arm,
            reference_path=requested_path,
            reference_sha256=capability.reference_sha256,
            config_sha256=capability.config_sha256,
            scorer_sha256=capability.scorer_sha256,
            registry_path=registry_path,
            audit_path=capability.audit_path,
            allowed_operations=frozenset({parsed_operation}),
        )
    # Deliberately first filesystem access to the reference: all guards above have passed.
    content = requested_path.read_bytes()
    if capability is not None and capability.reference_sha256:
        from .common import sha256_bytes

        if sha256_bytes(content) != capability.reference_sha256:
            raise ContractError("reference capability hash does not match reference bytes")
    return content


def transition_label_access_atomically(
    *,
    registry_path: str | Path,
    next_registry_path: str | Path,
    audit_path: str | Path,
    subject_id: str,
    next_state: str,
    operation: ReferenceOperation | str,
    utc: str,
    capture_git_commit: str,
    capture_git_dirty: bool,
    config_sha256: str,
    scorer_sha256: str,
    session_id: str = "",
    arm: str = "",
    reference_path: str | Path | None = None,
    reference_sha256: str = "",
    previous_audit_path: str | Path | None = None,
    score_artifact_path: str | Path | None = None,
    score_artifact_sha256: str = "",
    score_artifact_builder: Callable[[ScoringAuthorization, Path], object] | None = None,
    failure_injector: Callable[[str], None] | None = None,
) -> ScoringAuthorization:
    """Stage and publish one registry revision and matching audit as one transaction."""
    history = validate_registry_history(registry_path)
    current = history[-1]
    subject = next(
        (row for row in current["subjects"] if row["subject_id"] == subject_id), None
    )
    if subject is None:
        raise ContractError(f"subject {subject_id!r} is absent from cohort registry")
    current_state = str(subject["label_state"])
    if next_state not in LABEL_TRANSITIONS[current_state]:
        raise ContractError(f"forbidden label transition {current_state}->{next_state}")
    parsed_operation = ReferenceOperation(operation)
    if next_state == "validation_opened" and (
        subject["data_role"] != "representation_validation"
        or parsed_operation is not ReferenceOperation.VALIDATION_SCORING
    ):
        raise ContractError("validation_opened requires validation role/operation")
    if next_state == "final_scored" and (
        subject["data_role"] != "final_evaluation"
        or parsed_operation is not ReferenceOperation.FINAL_SCORING
    ):
        raise ContractError("final_scored requires final role/operation")
    if next_state == "stage1_reference_only" and parsed_operation not in {
        ReferenceOperation.RECOVERY_STAGE1,
        ReferenceOperation.REFERENCE_TIME_SENSITIVITY,
    }:
        raise ContractError("stage1 transition requires a reference-only operation")
    if parsed_operation is ReferenceOperation.RECOVERY_STAGE1 and arm and arm != "recovery":
        raise ContractError("RECOVERY_STAGE1 is valid only for the recovery arm")
    if capture_git_dirty or not capture_git_commit or capture_git_commit == "unknown":
        raise ContractError("label transition requires a known clean commit")
    require_nonempty_string({"utc": utc}, "utc")
    require_sha256(config_sha256, "config_sha256")
    require_sha256(scorer_sha256, "scorer_sha256")
    if not session_id or not arm or reference_path is None or not reference_sha256:
        raise ContractError(
            "successful transition capability requires session, arm, reference path and hash"
        )
    require_sha256(reference_sha256, "reference_sha256")
    reference_file = Path(reference_path).resolve()
    if not reference_file.is_file() or sha256_file(reference_file) != reference_sha256:
        raise ContractError("transition reference path/hash binding failed")

    current_registry_sha256 = sha256_file(registry_path)
    if current_state == "sealed":
        if previous_audit_path is not None:
            raise ContractError("the first label transition must not name a prior audit")
    else:
        if previous_audit_path is None:
            raise ContractError("every transition after the first requires its prior audit")
        prior_audit = read_json_object(previous_audit_path)
        expected_prior_operation = prior_audit.get("permitted_operation")
        if expected_prior_operation not in {
            ReferenceOperation.RECOVERY_STAGE1.value,
            ReferenceOperation.REFERENCE_TIME_SENSITIVITY.value,
        }:
            raise ContractError("prior audit operation is invalid for stage1 label access")
        if (
            expected_prior_operation == ReferenceOperation.RECOVERY_STAGE1.value
            and arm != "recovery"
        ):
            raise ContractError("prior recovery-stage audit is bound to a non-recovery arm")
        require_nonempty_string(prior_audit, "utc")
        prior_bindings = {
            "schema": "m2_label_access_transition_v1",
            "subject_id": subject_id,
            "session_id": session_id,
            "arm": arm,
            "data_role": subject["data_role"],
            "from_state": "sealed",
            "to_state": current_state,
            "registry_sha256": current_registry_sha256,
            "previous_registry_sha256": current.get("previous_registry_sha256"),
            "capture_git_commit": capture_git_commit,
            "capture_git_dirty": False,
            "config_sha256": config_sha256,
            "scorer_sha256": scorer_sha256,
            "reference_path": str(reference_file),
            "reference_sha256": reference_sha256,
            "previous_audit_path": None,
            "previous_audit_sha256": None,
            "score_artifact_path": None,
            "score_artifact_sha256": None,
        }
        for key, expected_value in prior_bindings.items():
            if prior_audit.get(key) != expected_value:
                raise ContractError(
                    f"prior audit {key} is not semantically chained to current registry"
                )
    if next_state == "final_scored":
        if score_artifact_path is None or score_artifact_builder is None:
            raise ContractError(
                "final_scored publication requires an in-transaction scoring-output builder"
            )
        if score_artifact_sha256:
            raise ContractError(
                "caller-supplied score hashes cannot authorize final_scored publication"
            )
    elif score_artifact_path is not None or score_artifact_sha256 or score_artifact_builder is not None:
        raise ContractError("score output arguments are valid only for final_scored")

    next_path = Path(next_registry_path)
    audit_output = Path(audit_path)
    score_output = Path(score_artifact_path) if score_artifact_path is not None else None
    next_digest = next_path.with_suffix(next_path.suffix + ".sha256")
    if (
        next_path.exists()
        or next_digest.exists()
        or audit_output.exists()
        or (score_output is not None and score_output.exists())
    ):
        raise ContractError("atomic label transition refuses overwrite")
    token = uuid4().hex
    staged_registry = next_path.with_name(next_path.name + f".{token}.staged")
    staged_audit = audit_output.with_name(audit_output.name + f".{token}.staged")
    staged_digest = staged_registry.with_suffix(staged_registry.suffix + ".sha256")
    staged_score = (
        score_output.with_name(score_output.name + f".{token}.staged")
        if score_output is not None
        else None
    )
    published_registry = False
    published_audit = False
    published_score = False
    try:
        if subject_id.startswith("P"):
            _write_prospective_label_revision(
                registry_path,
                staged_registry,
                subject_id=subject_id,
                next_state=next_state,
            )
        else:
            write_registry_revision(
                registry_path,
                staged_registry,
                subject_updates={subject_id: {"label_state": next_state}},
            )
        registry_hash = sha256_file(staged_registry)
        produced_score_sha256: str | None = None
        if next_state == "final_scored":
            assert staged_score is not None
            assert score_artifact_builder is not None
            provisional_capability = ScoringAuthorization(
                subject_id=subject_id,
                data_role=str(subject["data_role"]),
                operation=parsed_operation,
                registry_sha256=current_registry_sha256,
                _token=_SCORING_TOKEN,
                session_id=session_id,
                arm=arm,
                reference_path=str(Path(reference_path).resolve()) if reference_path else "",
                reference_sha256=reference_sha256,
                audit_path=str(audit_output.resolve()),
                config_sha256=config_sha256,
                scorer_sha256=scorer_sha256,
                _transaction_nonce=token,
            )
            _ACTIVE_ATOMIC_SCORING[token] = {
                "registry_path": str(Path(registry_path).resolve()),
                "registry_sha256": current_registry_sha256,
                "from_state": current_state,
                "to_state": next_state,
                "subject_id": subject_id,
                "session_id": session_id,
                "arm": arm,
                "reference_path": provisional_capability.reference_path,
                "reference_sha256": reference_sha256,
                "config_sha256": config_sha256,
                "scorer_sha256": scorer_sha256,
            }
            try:
                produced = score_artifact_builder(provisional_capability, staged_score)
            finally:
                _ACTIVE_ATOMIC_SCORING.pop(token, None)
            if produced is not None:
                if staged_score.exists():
                    raise ContractError(
                        "score builder must either write its staged path or return a JSON object"
                    )
                if not isinstance(produced, Mapping):
                    raise ContractError("score builder must return a JSON object or None")
                write_new_json(staged_score, produced)
            if not staged_score.is_file():
                raise ContractError("score builder did not produce its staged output")
            score_artifact = read_json_object(staged_score)
            expected_score_fields = {
                "schema": "m2_sealed_scoring_output_v1",
                "state": "sealed",
                "subject_id": subject_id,
                "session_id": session_id,
                "arm": arm,
                "data_role": subject["data_role"],
                "operation": parsed_operation.value,
                "config_sha256": config_sha256,
                "scorer_sha256": scorer_sha256,
                "reference_path": provisional_capability.reference_path,
                "reference_sha256": reference_sha256,
            }
            for key, expected_value in expected_score_fields.items():
                if score_artifact.get(key) != expected_value:
                    raise ContractError(
                        f"sealed scoring output {key} does not match the atomic authorization"
                    )
            produced_score_sha256 = sha256_file(staged_score)
        audit_document = {
            "schema": "m2_label_access_transition_v1",
            "subject_id": subject_id,
            "session_id": session_id,
            "arm": arm,
            "data_role": subject["data_role"],
            "from_state": current_state,
            "to_state": next_state,
            "permitted_operation": parsed_operation.value,
            "utc": utc,
            "capture_git_commit": capture_git_commit,
            "capture_git_dirty": False,
            "config_sha256": config_sha256,
            "scorer_sha256": scorer_sha256,
            "registry_sha256": registry_hash,
            "previous_registry_sha256": sha256_file(registry_path),
            "previous_audit_sha256": (
                sha256_file(previous_audit_path) if previous_audit_path else None
            ),
            "previous_audit_path": (
                str(Path(previous_audit_path).resolve()) if previous_audit_path else None
            ),
            "reference_path": str(Path(reference_path).resolve()) if reference_path else "",
            "reference_sha256": reference_sha256,
            "score_artifact_path": (
                str(score_output.resolve()) if score_output else None
            ),
            "score_artifact_sha256": produced_score_sha256,
        }
        write_new_json(staged_audit, audit_document)
        if failure_injector is not None:
            failure_injector("after_staging")
        staged_registry.replace(next_path)
        published_registry = True
        if staged_score is not None and score_output is not None:
            staged_score.replace(score_output)
            published_score = True
        staged_audit.replace(audit_output)
        published_audit = True
        if staged_digest.exists():
            staged_digest.replace(next_digest)
    except Exception:
        for path in (staged_registry, staged_audit, staged_digest, staged_score):
            if path is not None and path.exists():
                path.unlink()
        if published_registry and next_path.exists():
            next_path.unlink()
        if published_audit and audit_output.exists():
            audit_output.unlink()
        if published_score and score_output is not None and score_output.exists():
            score_output.unlink()
        if next_digest.exists():
            next_digest.unlink()
        raise

    return ScoringAuthorization(
        subject_id=subject_id,
        data_role=str(subject["data_role"]),
        operation=parsed_operation,
        registry_sha256=sha256_file(next_path),
        _token=_SCORING_TOKEN,
        session_id=session_id,
        arm=arm,
        reference_path=str(Path(reference_path).resolve()) if reference_path else "",
        reference_sha256=reference_sha256,
        audit_path=str(audit_output.resolve()),
        audit_sha256=sha256_file(audit_output),
        config_sha256=config_sha256,
        scorer_sha256=scorer_sha256,
    )


def write_transition_audit(
    output_path: str | Path,
    *,
    registry_path: str | Path,
    subject_id: str,
    next_state: str,
    operation: ReferenceOperation | str,
    utc: str,
    capture_git_commit: str,
    capture_git_dirty: bool,
    config_sha256: str,
    scorer_sha256: str,
    previous_audit_path: str | Path | None = None,
) -> Path:
    """Write a no-overwrite transition record bound to registry and prior audit bytes."""
    subject, registry_hash = _subject_record(registry_path, subject_id)
    current_state = str(subject["label_state"])
    if next_state not in LABEL_TRANSITIONS[current_state]:
        raise ContractError(f"forbidden label transition {current_state}->{next_state}")
    parsed_operation = ReferenceOperation(operation)
    if next_state == "validation_opened":
        if subject["data_role"] != "representation_validation" or parsed_operation is not ReferenceOperation.VALIDATION_SCORING:
            raise ContractError("validation_opened requires validation role/operation")
    elif next_state == "final_scored":
        if subject["data_role"] != "final_evaluation" or parsed_operation is not ReferenceOperation.FINAL_SCORING:
            raise ContractError("final_scored requires final role/operation")
    elif parsed_operation not in {
        ReferenceOperation.RECOVERY_STAGE1,
        ReferenceOperation.REFERENCE_TIME_SENSITIVITY,
    }:
        raise ContractError("stage1_reference_only requires an allow-listed reference-only operation")
    if type(capture_git_dirty) is not bool or capture_git_dirty:
        raise ContractError("label transition requires a clean committed checkout")
    if not capture_git_commit or capture_git_commit == "unknown":
        raise ContractError("label transition requires a known capture_git_commit")
    require_sha256(config_sha256, "config_sha256")
    require_sha256(scorer_sha256, "scorer_sha256")
    require_nonempty_string({"utc": utc}, "utc")
    previous_audit_sha256 = (
        sha256_file(previous_audit_path) if previous_audit_path is not None else None
    )
    document = {
        "schema": "m2_label_access_transition_v1",
        "subject_id": subject_id,
        "data_role": subject["data_role"],
        "from_state": current_state,
        "to_state": next_state,
        "permitted_operation": parsed_operation.value,
        "utc": utc,
        "capture_git_commit": capture_git_commit,
        "capture_git_dirty": False,
        "config_sha256": config_sha256,
        "scorer_sha256": scorer_sha256,
        "registry_sha256": registry_hash,
        "previous_audit_sha256": previous_audit_sha256,
    }
    return write_new_json(output_path, document)


def require_scoring_authorization(
    authorization: ScoringAuthorization,
    *,
    subject_id: str,
    session_id: str | None = None,
    arm: str | None = None,
    reference_path: str | Path | None = None,
    reference_sha256: str | None = None,
    config_sha256: str | None = None,
    scorer_sha256: str | None = None,
    registry_path: str | Path | None = None,
    audit_path: str | Path | None = None,
    allowed_operations: frozenset[ReferenceOperation] | None = None,
) -> None:
    """Validate a scoring capability against this exact invocation and current files."""
    if (
        type(authorization) is not ScoringAuthorization
        or authorization._token is not _SCORING_TOKEN
        or authorization.subject_id != subject_id
    ):
        raise ContractError("later agreement sensitivity requires firewall scoring authorization")

    active_atomic = (
        _ACTIVE_ATOMIC_SCORING.get(authorization._transaction_nonce)
        if authorization._transaction_nonce
        else None
    )

    if allowed_operations is None:
        expected_operation_by_role = {
            "representation_validation": ReferenceOperation.VALIDATION_SCORING,
            "final_evaluation": ReferenceOperation.FINAL_SCORING,
        }
        expected_operation = expected_operation_by_role.get(authorization.data_role)
        if expected_operation is None or authorization.operation is not expected_operation:
            raise ContractError(
                "scoring authorization role and operation do not match the prospective scoring lane"
            )
    elif authorization.operation not in allowed_operations:
        raise ContractError("authorization operation is not permitted for this reference-only entry point")

    prospective = subject_id.startswith("P")
    if prospective and active_atomic is None and not all(
        (
            authorization.session_id,
            authorization.arm,
            authorization.reference_path,
            authorization.reference_sha256,
            authorization.audit_path,
            authorization.audit_sha256,
            authorization.config_sha256,
            authorization.scorer_sha256,
        )
    ):
        raise ContractError(
            "prospective scoring requires capability-bound registry/audit/config/scorer/reference identities"
        )

    expected_bindings = (
        ("session", authorization.session_id, session_id),
        ("arm", authorization.arm, arm),
        ("reference hash", authorization.reference_sha256, reference_sha256),
        ("config hash", authorization.config_sha256, config_sha256),
        ("scorer hash", authorization.scorer_sha256, scorer_sha256),
    )
    for name, authorized_value, invocation_value in expected_bindings:
        if invocation_value is not None:
            binding_matches = (
                authorized_value == invocation_value
                if prospective
                else authorized_value in {"", invocation_value}
            )
            if not binding_matches:
                raise ContractError(
                    f"scoring authorization {name} binding disagrees with invocation"
                )

    resolved_reference: Path | None = None
    if reference_path is not None:
        resolved_reference = Path(reference_path).resolve()
        if authorization.reference_path and Path(authorization.reference_path) != resolved_reference:
            raise ContractError("scoring authorization reference path binding disagrees")
    elif prospective:
        raise ContractError(
            "prospective scoring invocation must provide the exact reference path"
        )
    if prospective:
        if registry_path is None or audit_path is None or resolved_reference is None:
            raise ContractError(
                "prospective scoring must provide current registry, audit, and reference paths"
            )
        registry_file = Path(registry_path)
        registry = validate_registry_history(registry_file)[-1]
        if active_atomic is not None:
            active_bindings = {
                "registry_path": str(registry_file.resolve()),
                "registry_sha256": sha256_file(registry_file),
                "subject_id": subject_id,
                "session_id": authorization.session_id,
                "arm": authorization.arm,
                "reference_path": str(resolved_reference),
                "reference_sha256": authorization.reference_sha256,
                "config_sha256": authorization.config_sha256,
                "scorer_sha256": authorization.scorer_sha256,
            }
            for key, expected_value in active_bindings.items():
                if active_atomic.get(key) != expected_value:
                    raise ContractError(
                        f"provisional atomic scoring {key} binding disagrees with invocation"
                    )
            if authorization.operation is not ReferenceOperation.FINAL_SCORING:
                raise ContractError("only FINAL_SCORING receives provisional atomic authority")
            subject = next(
                (row for row in registry["subjects"] if row["subject_id"] == subject_id),
                None,
            )
            if (
                subject is None
                or subject.get("data_role") != "final_evaluation"
                or subject.get("label_state") != active_atomic.get("from_state")
                or active_atomic.get("to_state") != "final_scored"
            ):
                raise ContractError("provisional scoring authority disagrees with registry state")
            if not resolved_reference.is_file() or sha256_file(
                resolved_reference
            ) != authorization.reference_sha256:
                raise ContractError("provisional scoring reference hash is not current")
            return
        if sha256_file(registry_file) != authorization.registry_sha256:
            raise ContractError("scoring authorization registry hash is not current")
        subject = next(
            (row for row in registry["subjects"] if row["subject_id"] == subject_id), None
        )
        if subject is None or subject["data_role"] != authorization.data_role:
            raise ContractError("scoring authorization role disagrees with current registry")
        if not _operation_allowed(subject, authorization.operation):
            raise ContractError("current registry label state does not permit this scoring operation")
        session = next(
            (row for row in subject["sessions"] if row["session_id"] == authorization.session_id),
            None,
        )
        if session is None or session["arm"] != authorization.arm:
            raise ContractError("scoring authorization session/arm disagrees with registry")

        audit_file = Path(audit_path)
        if sha256_file(audit_file) != authorization.audit_sha256:
            raise ContractError("scoring authorization audit hash is not current")
        audit = read_json_object(audit_file)
        if audit.get("schema") != "m2_label_access_transition_v1":
            raise ContractError("scoring audit has the wrong schema")
        audit_bindings = {
            "subject_id": authorization.subject_id,
            "session_id": authorization.session_id,
            "arm": authorization.arm,
            "data_role": authorization.data_role,
            "permitted_operation": authorization.operation.value,
            "registry_sha256": authorization.registry_sha256,
            "config_sha256": authorization.config_sha256,
            "scorer_sha256": authorization.scorer_sha256,
            "reference_path": authorization.reference_path,
            "reference_sha256": authorization.reference_sha256,
            "capture_git_dirty": False,
        }
        for key, expected_value in audit_bindings.items():
            if audit.get(key) != expected_value:
                raise ContractError(f"scoring audit {key} binding disagrees with capability")

        expected_states_by_operation = {
            ReferenceOperation.VALIDATION_SCORING: (
                "stage1_reference_only",
                "validation_opened",
            ),
            ReferenceOperation.FINAL_SCORING: (
                "stage1_reference_only",
                "final_scored",
            ),
            ReferenceOperation.RECOVERY_STAGE1: ("sealed", "stage1_reference_only"),
            ReferenceOperation.REFERENCE_TIME_SENSITIVITY: (
                "sealed",
                "stage1_reference_only",
            ),
        }
        expected_from_state, expected_to_state = expected_states_by_operation[
            authorization.operation
        ]
        if (
            audit.get("from_state") != expected_from_state
            or audit.get("to_state") != expected_to_state
            or audit.get("capture_git_commit") in {None, "", "unknown"}
            or not audit.get("utc")
        ):
            raise ContractError("scoring audit transition semantics are incomplete or invalid")
        if audit.get("previous_registry_sha256") != registry.get(
            "previous_registry_sha256"
        ):
            raise ContractError("scoring audit predecessor registry binding is invalid")
        prior_audit_path = audit.get("previous_audit_path")
        prior_audit_sha256 = audit.get("previous_audit_sha256")
        if expected_from_state == "sealed":
            if prior_audit_path is not None or prior_audit_sha256 is not None:
                raise ContractError("first scoring audit must not bind a prior audit")
        else:
            if type(prior_audit_path) is not str or not prior_audit_path:
                raise ContractError("scoring audit is missing its predecessor audit path")
            prior_file = Path(prior_audit_path)
            if not prior_file.is_file() or sha256_file(prior_file) != prior_audit_sha256:
                raise ContractError("scoring audit predecessor audit binding is invalid")
            prior_audit = read_json_object(prior_file)
            if (
                prior_audit.get("schema") != "m2_label_access_transition_v1"
                or prior_audit.get("subject_id") != subject_id
                or prior_audit.get("to_state") != expected_from_state
                or prior_audit.get("registry_sha256")
                != registry.get("previous_registry_sha256")
            ):
                raise ContractError("scoring audit predecessor semantics are invalid")

        if not resolved_reference.is_file():
            raise ContractError("scoring capability reference path is not a current file")
        if sha256_file(resolved_reference) != authorization.reference_sha256:
            raise ContractError("scoring capability reference hash is not current")
