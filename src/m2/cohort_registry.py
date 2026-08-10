"""Immutable canonical-JSON registry for the fixed P001-P015 prospective cohort."""
from __future__ import annotations

import json
import copy
from pathlib import Path
from typing import Iterable, Mapping

from .acquisition_metadata import Arm, DataRole, validate_acquisition_metadata
from .common import (
    ContractError,
    canonical_json_bytes,
    require_int,
    require_nonempty_string,
    require_sha256,
    sha256_bytes,
    sha256_file,
    write_new_bytes,
    write_new_json,
)

REGISTRY_SCHEMA = "m2_cohort_registry_v1"
DEFAULT_REGISTRY_DIR = Path(__file__).resolve().parents[2] / "cohort_registry"
DEFAULT_REGISTRY_PATH = DEFAULT_REGISTRY_DIR / "registry_v001.json"

LABEL_TRANSITIONS = {
    "sealed": {"stage1_reference_only"},
    "stage1_reference_only": {"validation_opened", "final_scored"},
    "validation_opened": set(),
    "final_scored": set(),
}
SESSION_STATES = {
    "planned",
    "captured",
    "withdrawn",
    "recovery_not_cleared",
    "missing",
    "technical_not_acquired",
}
NON_ACQUISITION_STATES = SESSION_STATES - {"planned", "captured"}

_VALIDATION_IDS = tuple(f"P{slot:03d}" for slot in range(1, 6))
_FINAL_IDS = tuple(f"P{slot:03d}" for slot in range(6, 16))
_VALIDATION_RATES = (12, 15, 18, 12, 15)
_FINAL_RATES = (12, 15, 18, 12, 15, 18, 12, 15, 18, 12)
_EXPECTED_ARMS = (("natural", 1), ("paced", 2), ("recovery", 3))
_PROSPECTIVE_LABEL_AUTHORITY = object()

_FORBIDDEN_PRIVATE_KEY_PARTS = (
    "diagnos",
    "eligibility_reason",
    "health",
    "medical",
    "medication",
    "participant_name",
    "pregnan",
    "screening_answer",
    "symptom",
)


class RegistryError(ContractError):
    """A cohort revision or revision chain violates the fixed identity contract."""


def _reject_private_registry_fields(value: object, path: str = "registry") -> None:
    """Reject underlying private facts; the controlled session state is sufficient."""
    if isinstance(value, Mapping):
        for key, nested in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _FORBIDDEN_PRIVATE_KEY_PARTS):
                raise RegistryError(
                    f"{path}.{key} is forbidden private/health or screening metadata"
                )
            _reject_private_registry_fields(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_private_registry_fields(nested, f"{path}[{index}]")


def registry_digest(path: str | Path) -> str:
    return sha256_file(path)


def role_membership_digest(subject_ids: Iterable[str]) -> str:
    return sha256_bytes(canonical_json_bytes(list(subject_ids), trailing_newline=False))


def _load_exact_canonical(path: Path) -> dict:
    try:
        raw = path.read_bytes()
        document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RegistryError(f"cannot read registry {path}: {exc}") from exc
    if type(document) is not dict:
        raise RegistryError(f"{path}: registry must be a JSON object")
    if raw != canonical_json_bytes(document):
        raise RegistryError(
            f"{path}: registry bytes are not canonical sorted compact UTF-8 JSON with one newline"
        )
    return document


def validate_registry_document(document: Mapping[str, object], *, where: str = "registry") -> None:
    if type(document) is not dict:
        raise RegistryError(f"{where}: registry must be an object")
    _reject_private_registry_fields(document, where)
    if document.get("schema") != REGISTRY_SCHEMA:
        raise RegistryError(f"{where}: schema must be {REGISTRY_SCHEMA!r}")
    registry_kind = document.get("registry_kind", "prospective_fixed")
    if registry_kind == "synthetic_fixture":
        _validate_synthetic_registry(document, where=where)
        return
    if registry_kind != "prospective_fixed":
        raise RegistryError(f"{where}: unknown registry_kind {registry_kind!r}")
    revision = require_int(document, "revision", minimum=1)
    predecessor = document.get("previous_registry_sha256")
    if revision == 1:
        if predecessor is not None:
            raise RegistryError("revision 1 previous_registry_sha256 must be null")
    else:
        require_sha256(predecessor, "previous_registry_sha256")

    subjects = document.get("subjects")
    if type(subjects) is not list or len(subjects) != 15:
        raise RegistryError(f"{where}: subjects must contain exactly 15 fixed slots")
    ids = [subject.get("subject_id") if type(subject) is dict else None for subject in subjects]
    expected_ids = list(_VALIDATION_IDS + _FINAL_IDS)
    if ids != expected_ids:
        raise RegistryError(f"{where}: subjects must be P001-P015 in cohort-slot order")

    seen_sessions: set[str] = set()
    for index, subject in enumerate(subjects, start=1):
        if type(subject) is not dict:
            raise RegistryError(f"{where}: subject slot {index} must be an object")
        subject_id = f"P{index:03d}"
        if require_int(subject, "cohort_slot", minimum=1) != index:
            raise RegistryError(f"{subject_id}: cohort_slot is immutable and must equal {index}")
        expected_role = (
            DataRole.REPRESENTATION_VALIDATION.value
            if index <= 5
            else DataRole.FINAL_EVALUATION.value
        )
        if subject.get("data_role") != expected_role:
            raise RegistryError(f"{subject_id}: immutable data_role must be {expected_role}")
        rate_index = index - 1 if index <= 5 else index - 6
        expected_rate = (
            _VALIDATION_RATES[rate_index] if index <= 5 else _FINAL_RATES[rate_index]
        )
        if require_int(subject, "paced_rate_bpm") != expected_rate:
            raise RegistryError(f"{subject_id}: paced-rate rotation must assign {expected_rate}")
        label_state = subject.get("label_state")
        if label_state not in LABEL_TRANSITIONS:
            raise RegistryError(f"{subject_id}: invalid label_state {label_state!r}")
        if expected_role == DataRole.REPRESENTATION_VALIDATION.value and label_state == "final_scored":
            raise RegistryError(f"{subject_id}: validation subject cannot enter final_scored")
        if expected_role == DataRole.FINAL_EVALUATION.value and label_state == "validation_opened":
            raise RegistryError(f"{subject_id}: final subject cannot enter validation_opened")

        sessions = subject.get("sessions")
        if type(sessions) is not list or len(sessions) != 3:
            raise RegistryError(
                f"{subject_id}: all natural/paced/recovery slots must remain materialized"
            )
        for session, (expected_arm, expected_visit) in zip(sessions, _EXPECTED_ARMS):
            if type(session) is not dict:
                raise RegistryError(f"{subject_id}: session entries must be objects")
            if session.get("arm") != expected_arm or session.get("visit_number") != expected_visit:
                raise RegistryError(f"{subject_id}: session arm/visit slots cannot change order")
            session_id = require_nonempty_string(session, "session_id")
            if session_id != f"{subject_id}_{expected_arm}":
                raise RegistryError(f"{subject_id}: fixed session ID for {expected_arm} is wrong")
            if session_id in seen_sessions:
                raise RegistryError(f"session_id {session_id!r} belongs to more than one subject")
            seen_sessions.add(session_id)
            state = session.get("state")
            if state not in SESSION_STATES:
                raise RegistryError(f"{session_id}: invalid session state {state!r}")
            for key, value in session.items():
                if key.endswith("_sha256"):
                    require_sha256(value, f"{session_id}.{key}")
            if state == "captured" and "radar_receipt_sha256" not in session:
                raise RegistryError(f"{session_id}: captured state must bind radar_receipt_sha256")
            if state in NON_ACQUISITION_STATES:
                forbidden = {key for key in session if key.endswith("_sha256") or key == "capture_id"}
                if forbidden:
                    raise RegistryError(
                        f"{session_id}: non-acquisition outcome cannot fabricate capture bindings"
                    )
            attempts = session.get("attempts")
            if attempts is not None:
                if type(attempts) is not list or not attempts:
                    raise RegistryError(f"{session_id}: attempts must be a non-empty array")
                from .retry import validate_retry_pair

                for attempt in attempts:
                    if type(attempt) is not dict:
                        raise RegistryError(f"{session_id}: retry attempt must be an object")
                    expected_identity = {
                        "subject_id": subject_id,
                        "cohort_slot": index,
                        "data_role": expected_role,
                        "arm": expected_arm,
                    }
                    for identity_key, expected_value in expected_identity.items():
                        if attempt.get(identity_key) != expected_value:
                            raise RegistryError(
                                f"{session_id}: retry attempt changed {identity_key}"
                            )
                for predecessor, replacement in zip(attempts, attempts[1:]):
                    validate_retry_pair(predecessor, replacement)

    role_hashes = document.get("role_membership_sha256")
    if type(role_hashes) is not dict:
        raise RegistryError("role_membership_sha256 must be an object")
    expected_hashes = {
        DataRole.REPRESENTATION_VALIDATION.value: role_membership_digest(_VALIDATION_IDS),
        DataRole.FINAL_EVALUATION.value: role_membership_digest(_FINAL_IDS),
    }
    if role_hashes != expected_hashes:
        raise RegistryError("role-membership hashes disagree with fixed cohort-slot lists")


def _validate_synthetic_registry(document: Mapping[str, object], *, where: str) -> None:
    """Small isolated registry shape used only by the engineering dry run."""
    revision = require_int(document, "revision", minimum=1)
    predecessor = document.get("previous_registry_sha256")
    if revision == 1:
        if predecessor is not None:
            raise RegistryError("synthetic revision 1 predecessor must be null")
    else:
        require_sha256(predecessor, "previous_registry_sha256")
    subjects = document.get("subjects")
    if type(subjects) is not list or not subjects:
        raise RegistryError(f"{where}: synthetic registry needs at least one fixture subject")
    ids: list[str] = []
    sessions_seen: set[str] = set()
    for expected_slot, subject in enumerate(subjects, start=1):
        if type(subject) is not dict:
            raise RegistryError("synthetic subject must be an object")
        subject_id = require_nonempty_string(subject, "subject_id")
        if not (subject_id.startswith("T") or subject_id.startswith("SYN")):
            raise RegistryError("synthetic registry IDs must use T*/SYN* pseudonyms")
        ids.append(subject_id)
        if require_int(subject, "cohort_slot", minimum=1) != expected_slot:
            raise RegistryError("synthetic cohort slots must be consecutive")
        if subject.get("data_role") != DataRole.REPRESENTATION_VALIDATION.value:
            raise RegistryError("synthetic registry may exercise only representation_validation")
        if subject.get("label_state") not in LABEL_TRANSITIONS:
            raise RegistryError("synthetic subject has an invalid label state")
        require_int(subject, "paced_rate_bpm")
        sessions = subject.get("sessions")
        if type(sessions) is not list or len(sessions) != 3:
            raise RegistryError("synthetic subject must materialize all three session slots")
        for session, (arm, visit) in zip(sessions, _EXPECTED_ARMS):
            if (
                type(session) is not dict
                or session.get("arm") != arm
                or session.get("visit_number") != visit
                or session.get("state") not in SESSION_STATES
            ):
                raise RegistryError("synthetic session arm/visit/state is malformed")
            session_id = require_nonempty_string(session, "session_id")
            if session_id in sessions_seen:
                raise RegistryError("synthetic session IDs must be subject-disjoint")
            sessions_seen.add(session_id)
            for key, value in session.items():
                if key.endswith("_sha256"):
                    require_sha256(value, f"{session_id}.{key}")
            if session["state"] == "captured" and "radar_receipt_sha256" not in session:
                raise RegistryError("synthetic captured session must bind radar_receipt_sha256")
    role_hashes = document.get("role_membership_sha256")
    expected = {
        DataRole.REPRESENTATION_VALIDATION.value: role_membership_digest(ids)
    }
    if role_hashes != expected:
        raise RegistryError("synthetic role-membership hash is wrong")


def load_registry(path: str | Path = DEFAULT_REGISTRY_PATH) -> dict:
    registry_path = Path(path)
    document = _load_exact_canonical(registry_path)
    validate_registry_document(document, where=str(registry_path))
    digest_sidecar = registry_path.with_suffix(registry_path.suffix + ".sha256")
    if not digest_sidecar.is_file():
        raise RegistryError(f"{digest_sidecar}: registry digest sidecar is mandatory")
    declared = digest_sidecar.read_text(encoding="ascii").strip()
    require_sha256(declared, str(digest_sidecar))
    actual = sha256_file(registry_path)
    if declared != actual:
        raise RegistryError(f"{digest_sidecar}: digest does not match exact registry bytes")
    return document


def validate_registry_history(path: str | Path) -> list[dict]:
    """Load the complete hash-linked history ending at ``path``.

    Revision consumers must not trust a latest document in isolation: a predecessor
    can otherwise be changed after the successor was minted.
    """
    latest_path = Path(path)
    latest = load_registry(latest_path)
    if latest["revision"] == 1:
        return [latest]

    candidates: dict[str, Path] = {}
    for candidate in latest_path.parent.glob("*.json"):
        try:
            document = _load_exact_canonical(candidate)
        except RegistryError:
            continue
        if document.get("schema") == REGISTRY_SCHEMA:
            candidates[sha256_file(candidate)] = candidate

    history_paths = [latest_path]
    current = latest
    while current["revision"] > 1:
        predecessor_hash = current["previous_registry_sha256"]
        predecessor_path = candidates.get(predecessor_hash)
        if predecessor_path is None:
            raise RegistryError(
                "registry history is incomplete or a predecessor digest was mutated"
            )
        history_paths.append(predecessor_path)
        current = load_registry(predecessor_path)
    history_paths.reverse()
    return validate_registry_chain(history_paths)


def validate_registry_chain(paths: Iterable[str | Path]) -> list[dict]:
    registry_paths = [Path(path) for path in paths]
    if not registry_paths:
        raise RegistryError("registry history is empty; revision 1 may not be deleted")
    documents: list[dict] = []
    previous_path: Path | None = None
    previous_document: dict | None = None
    for expected_revision, path in enumerate(registry_paths, start=1):
        document = load_registry(path)
        if document["revision"] != expected_revision:
            raise RegistryError("registry revisions must be complete, consecutive, and ordered")
        if previous_path is not None:
            if document["previous_registry_sha256"] != sha256_file(previous_path):
                raise RegistryError(f"{path}: wrong exact-byte predecessor digest")
            _validate_append_only(previous_document, document)
        documents.append(document)
        previous_path = path
        previous_document = document
    return documents


def validate_registry_directory(directory: str | Path = DEFAULT_REGISTRY_DIR) -> list[dict]:
    paths = sorted(Path(directory).glob("registry_v[0-9][0-9][0-9].json"))
    return validate_registry_chain(paths)


def _validate_append_only(previous: Mapping[str, object], current: Mapping[str, object]) -> None:
    previous_subjects = previous["subjects"]
    current_subjects = current["subjects"]
    for old_subject, new_subject in zip(previous_subjects, current_subjects):
        subject_id = old_subject["subject_id"]
        for key in ("subject_id", "cohort_slot", "data_role", "paced_rate_bpm"):
            if old_subject[key] != new_subject[key]:
                raise RegistryError(f"{subject_id}: immutable {key} changed across revisions")
        old_label = old_subject["label_state"]
        new_label = new_subject["label_state"]
        if new_label != old_label and new_label not in LABEL_TRANSITIONS[old_label]:
            raise RegistryError(f"{subject_id}: forbidden label-state transition {old_label}->{new_label}")
        if (
            old_subject["data_role"] == DataRole.REPRESENTATION_VALIDATION.value
            and new_label == "final_scored"
        ) or (
            old_subject["data_role"] == DataRole.FINAL_EVALUATION.value
            and new_label == "validation_opened"
        ):
            raise RegistryError(f"{subject_id}: label transition crosses immutable data role")
        for old_session, new_session in zip(old_subject["sessions"], new_subject["sessions"]):
            session_id = old_session["session_id"]
            for key in ("session_id", "arm", "visit_number"):
                if old_session[key] != new_session[key]:
                    raise RegistryError(f"{session_id}: immutable session {key} changed")
            old_state = old_session["state"]
            new_state = new_session["state"]
            if old_state != "planned" and new_state != old_state:
                raise RegistryError(f"{session_id}: terminal session state {old_state} changed")
            for key, value in old_session.items():
                if key == "state":
                    continue
                if key == "attempts":
                    old_attempts = value
                    new_attempts = new_session.get("attempts")
                    if type(old_attempts) is not list or type(new_attempts) is not list:
                        raise RegistryError(f"{session_id}: attempts must be arrays")
                    if new_attempts[: len(old_attempts)] != old_attempts:
                        raise RegistryError(
                            f"{session_id}: prior retry attempts changed or were deleted"
                        )
                    if len(new_attempts) != len(old_attempts) + 1:
                        raise RegistryError(
                            f"{session_id}: a revision may append exactly one retry attempt"
                        )
                    from .retry import validate_retry_pair

                    validate_retry_pair(old_attempts[-1], new_attempts[-1])
                    continue
                if new_session.get(key) != value:
                    raise RegistryError(f"{session_id}: prior field {key!r} changed or was deleted")
        for key, value in old_subject.items():
            if key in {"label_state", "sessions"}:
                continue
            if new_subject.get(key) != value:
                raise RegistryError(f"{subject_id}: prior field {key!r} changed or was deleted")


def validate_membership(
    registry_path: str | Path,
    *,
    subject_id: str,
    cohort_slot: int,
    data_role: DataRole,
    session_id: str,
    arm: Arm,
) -> None:
    document = validate_registry_history(registry_path)[-1]
    subjects = {subject["subject_id"]: subject for subject in document["subjects"]}
    subject = subjects.get(subject_id)
    if subject is None:
        raise RegistryError(f"subject {subject_id!r} is absent from registry")
    if subject["cohort_slot"] != cohort_slot or subject["data_role"] != data_role.value:
        raise RegistryError("manifest subject slot/role disagrees with immutable registry membership")
    sessions = {session["session_id"]: session for session in subject["sessions"]}
    session = sessions.get(session_id)
    if session is None or session["arm"] != arm.value:
        raise RegistryError("manifest session/arm disagrees with its subject registry slot")


def register_captured_session(
    registry_path: str | Path,
    output_path: str | Path,
    receipt_path: str | Path,
) -> Path:
    """Append one validated planned→captured fact bound to an exact sealed receipt."""
    previous_path = Path(registry_path)
    next_path = Path(output_path)
    sealed_receipt_path = Path(receipt_path)
    if next_path.suffix.lower() != ".json":
        raise RegistryError("output registry revision must be a .json file")
    if next_path.resolve().parent != previous_path.resolve().parent:
        raise RegistryError(
            "output registry revision must remain in the predecessor history directory"
        )
    if next_path.exists() or next_path.with_suffix(next_path.suffix + ".sha256").exists():
        raise RegistryError(f"refusing to overwrite registry revision {next_path}")

    history = validate_registry_history(previous_path)
    latest = history[-1]
    # Validate every radar/config/map binding before the registry records acquisition.
    from .capture_artifacts import (
        SEALED_RECEIPT_SCHEMA,
        validate_sealed_radar_receipt,
    )

    receipt = validate_sealed_radar_receipt(sealed_receipt_path)
    if receipt.get("schema") != SEALED_RECEIPT_SCHEMA:
        raise RegistryError("capture registration requires an M2 sealed radar receipt")
    if latest.get("registry_kind", "prospective_fixed") != "prospective_fixed":
        raise RegistryError("operational capture registration requires the prospective registry")
    if receipt.get("engineering_dry_run") is not False:
        raise RegistryError(
            "engineering dry-run/nonphysical receipts cannot enter the prospective captured registry"
        )

    subject_id = require_nonempty_string(receipt, "subject_id")
    session_id = require_nonempty_string(receipt, "session_id")
    metadata_raw = receipt.get("acquisition_metadata")
    if type(metadata_raw) is not dict:
        raise RegistryError("sealed receipt acquisition_metadata is malformed")
    metadata = validate_acquisition_metadata(metadata_raw)
    try:
        role = DataRole(receipt.get("data_role"))
        arm = Arm(receipt.get("arm"))
    except ValueError as exc:
        raise RegistryError("sealed receipt has an invalid data role or arm") from exc
    cohort_slot = require_int(receipt, "cohort_slot", minimum=1)
    for key, expected_value in (
        ("subject_id", subject_id),
        ("session_id", session_id),
        ("cohort_slot", cohort_slot),
        ("data_role", role.value),
        ("arm", arm.value),
    ):
        if metadata.get(key) != expected_value:
            raise RegistryError(
                f"sealed receipt {key} disagrees with embedded acquisition metadata"
            )
    validate_membership(
        previous_path,
        subject_id=subject_id,
        cohort_slot=cohort_slot,
        data_role=role,
        session_id=session_id,
        arm=arm,
    )

    historical_hashes = {
        sha256_bytes(canonical_json_bytes(document)) for document in history
    }
    receipt_registry_hash = require_sha256(
        receipt.get("registry_sha256"), "sealed_receipt.registry_sha256"
    )
    if receipt_registry_hash not in historical_hashes:
        raise RegistryError(
            "sealed receipt registry hash is not in the validated immutable history"
        )

    subject = next(row for row in latest["subjects"] if row["subject_id"] == subject_id)
    session = next(row for row in subject["sessions"] if row["session_id"] == session_id)
    if session["state"] != "planned":
        raise RegistryError(
            f"capture registration requires a planned session, got {session['state']!r}"
        )
    receipt_sha256 = sha256_file(sealed_receipt_path)
    written = write_registry_revision(
        previous_path,
        next_path,
        subject_updates={
            subject_id: {
                "sessions": {
                    session_id: {
                        "state": "captured",
                        "radar_receipt_sha256": receipt_sha256,
                    }
                }
            }
        },
    )
    validate_registry_history(written)
    return written


def _write_registry_revision(
    previous_path: str | Path,
    output_path: str | Path,
    *,
    subject_updates: Mapping[str, Mapping[str, object]],
    _label_authority: object | None = None,
) -> Path:
    """Append assignment/session/hash/state facts in a new immutable revision."""
    previous_file = Path(previous_path)
    previous = validate_registry_history(previous_file)[-1]
    current = copy.deepcopy(previous)
    current["revision"] = int(previous["revision"]) + 1
    current["previous_registry_sha256"] = sha256_file(previous_file)
    subjects = {subject["subject_id"]: subject for subject in current["subjects"]}
    for subject_id, update in subject_updates.items():
        if subject_id not in subjects or type(update) is not dict:
            raise RegistryError(f"invalid subject revision update for {subject_id!r}")
        subject = subjects[subject_id]
        if "label_state" in update:
            if (
                subject_id in (_VALIDATION_IDS + _FINAL_IDS)
                and _label_authority is not _PROSPECTIVE_LABEL_AUTHORITY
            ):
                raise RegistryError(
                    "prospective label_state changes require internal atomic authority"
                )
            subject["label_state"] = update["label_state"]
        session_updates = update.get("sessions", {})
        if type(session_updates) is not dict:
            raise RegistryError(f"{subject_id}: sessions update must be keyed by session_id")
        sessions = {session["session_id"]: session for session in subject["sessions"]}
        for session_id, session_update in session_updates.items():
            if session_id not in sessions or type(session_update) is not dict:
                raise RegistryError(f"unknown/malformed session update {session_id!r}")
            sessions[session_id].update(session_update)
        for key, value in update.items():
            if key not in {"label_state", "sessions"}:
                subject[key] = value
    validate_registry_document(current, where=str(output_path))
    _validate_append_only(previous, current)
    written = write_new_json(output_path, current)
    write_new_bytes(
        written.with_suffix(written.suffix + ".sha256"),
        (sha256_file(written) + "\n").encode("ascii"),
    )
    return written


def write_registry_revision(
    previous_path: str | Path,
    output_path: str | Path,
    *,
    subject_updates: Mapping[str, Mapping[str, object]],
) -> Path:
    """Append public acquisition facts without granting prospective label access.

    P001--P015 label-state transitions are security-relevant operations.  They are
    published only by the label firewall's private atomic registry+audit path below.
    Synthetic fixtures retain the public revision helper for isolated engineering tests.
    """
    for subject_id, update in subject_updates.items():
        if (
            subject_id in (_VALIDATION_IDS + _FINAL_IDS)
            and isinstance(update, Mapping)
            and "label_state" in update
        ):
            raise RegistryError(
                "prospective label_state changes require the atomic label-firewall transition"
            )
    return _write_registry_revision(
        previous_path,
        output_path,
        subject_updates=subject_updates,
    )


def _write_prospective_label_revision(
    previous_path: str | Path,
    output_path: str | Path,
    *,
    subject_id: str,
    next_state: str,
) -> Path:
    """Private writer used only inside the atomic label-firewall transaction."""
    if subject_id not in (_VALIDATION_IDS + _FINAL_IDS):
        raise RegistryError("private prospective label writer accepts only P001-P015")
    return _write_registry_revision(
        previous_path,
        output_path,
        subject_updates={subject_id: {"label_state": next_state}},
        _label_authority=_PROSPECTIVE_LABEL_AUTHORITY,
    )
