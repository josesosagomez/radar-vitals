"""Fixed cohort, immutable revision-chain, and retry-predicate regressions."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "m2"))

from builders import retry_pair  # noqa: E402
from src.m2.cohort_registry import (  # noqa: E402
    Arm,
    DataRole,
    DEFAULT_REGISTRY_PATH,
    RegistryError,
    load_registry,
    validate_registry_chain,
    validate_registry_document,
    validate_membership,
    write_registry_revision,
)
from src.m2.common import ContractError, canonical_json_bytes, sha256_file  # noqa: E402
from src.m2.label_firewall import (  # noqa: E402
    ReferenceOperation,
    transition_label_access_atomically,
)
from src.m2.retry import RetryReason, validate_retry_pair  # noqa: E402


def _copy_initial(tmp_path: Path) -> Path:
    path = tmp_path / "registry_v001.json"
    path.write_bytes(DEFAULT_REGISTRY_PATH.read_bytes())
    path.with_suffix(path.suffix + ".sha256").write_bytes(
        DEFAULT_REGISTRY_PATH.with_suffix(DEFAULT_REGISTRY_PATH.suffix + ".sha256").read_bytes()
    )
    return path


def _atomic_stage1(tmp_path: Path, registry: Path, *, subject_id: str = "P001"):
    reference = tmp_path / f"{subject_id}_natural_reference.csv"
    reference.write_bytes(b"reference")
    next_registry = tmp_path / "registry_v002.json"
    audit = tmp_path / "stage1_audit.json"
    capability = transition_label_access_atomically(
        registry_path=registry,
        next_registry_path=next_registry,
        audit_path=audit,
        subject_id=subject_id,
        next_state="stage1_reference_only",
        operation=ReferenceOperation.REFERENCE_TIME_SENSITIVITY,
        utc="2030-01-01T00:00:00Z",
        capture_git_commit="abc123",
        capture_git_dirty=False,
        config_sha256="a" * 64,
        scorer_sha256="b" * 64,
        session_id=f"{subject_id}_natural",
        arm="natural",
        reference_path=reference,
        reference_sha256=sha256_file(reference),
    )
    return next_registry, audit, reference, capability


def test_committed_registry_and_digest_sidecar_exist_on_disk():
    """Guard the precondition every other M2 registry test assumes.

    Commit a5edecc deleted the canonical registry and its digest sidecar. The result was 68
    failures across this file and tests/test_m2_capture_artifacts.py, all surfacing as
    FileNotFoundError inside unrelated helpers rather than as a clear statement of what was
    wrong. This test fails first and says so directly.
    """
    digest_path = DEFAULT_REGISTRY_PATH.with_suffix(DEFAULT_REGISTRY_PATH.suffix + ".sha256")
    assert DEFAULT_REGISTRY_PATH.is_file(), (
        f"{DEFAULT_REGISTRY_PATH} is missing: no prospective capture can start without it, "
        "and --cohort-registry is mandatory in prospective study mode"
    )
    assert digest_path.is_file(), (
        f"{digest_path} is missing: load_registry treats the digest sidecar as mandatory"
    )
    assert digest_path.read_text(encoding="ascii").strip() == sha256_file(DEFAULT_REGISTRY_PATH), (
        "registry digest sidecar does not match the exact registry bytes"
    )


def test_committed_registry_materializes_exact_slots_roles_rates_and_arms():
    registry = load_registry()
    assert len(registry["subjects"]) == 15
    assert [s["data_role"] for s in registry["subjects"]] == [
        *(["representation_validation"] * 5), *(["final_evaluation"] * 10)
    ]
    assert [s["paced_rate_bpm"] for s in registry["subjects"][:5]] == [12, 15, 18, 12, 15]
    assert [s["paced_rate_bpm"] for s in registry["subjects"][5:]] == [
        12, 15, 18, 12, 15, 18, 12, 15, 18, 12
    ]
    for slot, subject in enumerate(registry["subjects"], 1):
        assert subject["subject_id"] == f"P{slot:03d}"
        assert [(x["arm"], x["visit_number"], x["state"]) for x in subject["sessions"]] == [
            ("natural", 1, "planned"), ("paced", 2, "planned"), ("recovery", 3, "planned")
        ]


@pytest.mark.parametrize("mutation", ["missing_recovery", "role", "slot", "rate", "session_owner"])
def test_registry_rejects_fixed_cohort_mutations(mutation):
    document = copy.deepcopy(load_registry())
    if mutation == "missing_recovery":
        document["subjects"][0]["sessions"].pop()
    elif mutation == "role":
        document["subjects"][0]["data_role"] = "final_evaluation"
    elif mutation == "slot":
        document["subjects"][0]["cohort_slot"] = 2
    elif mutation == "rate":
        document["subjects"][0]["paced_rate_bpm"] = 18
    else:
        document["subjects"][0]["sessions"][0]["session_id"] = "P006_natural"
    with pytest.raises(RegistryError):
        validate_registry_document(document)


@pytest.mark.parametrize(
    "state", ["withdrawn", "recovery_not_cleared", "missing", "technical_not_acquired"]
)
def test_controlled_nonacquisition_outcomes_remain_materialized(state):
    document = copy.deepcopy(load_registry())
    session = document["subjects"][0]["sessions"][2]
    session["state"] = state
    validate_registry_document(document)
    assert session["session_id"] == "P001_recovery"


@pytest.mark.parametrize(
    ("stopping_event_category", "disposition"),
    [
        ("participant_stop", "withdrawn"),
        ("researcher_safety_stop", "recovery_not_cleared"),
    ],
)
def test_recovery_stop_is_represented_only_as_privacy_safe_nonacquisition_disposition(
    stopping_event_category, disposition
):
    document = copy.deepcopy(load_registry())
    session = document["subjects"][0]["sessions"][2]
    session["state"] = disposition
    validate_registry_document(document)
    assert session["session_id"] == "P001_recovery"
    assert stopping_event_category not in session.values()
    assert "stopping_event_category" not in session


def test_nonacquisition_outcome_rejects_capture_bindings():
    document = copy.deepcopy(load_registry())
    session = document["subjects"][0]["sessions"][2]
    session.update({"state": "missing", "raw_sha256": "a" * 64})
    with pytest.raises(RegistryError, match="cannot fabricate capture bindings"):
        validate_registry_document(document)


@pytest.mark.parametrize("private_key", ["medical_reason", "diagnosis", "screening_answer"])
def test_nonacquisition_outcome_rejects_underlying_health_reason_fields(private_key):
    document = copy.deepcopy(load_registry())
    session = document["subjects"][0]["sessions"][2]
    session.update({"state": "recovery_not_cleared", private_key: "private health detail"})
    with pytest.raises(RegistryError, match="health|private|reason|screening"):
        validate_registry_document(document)


def test_revision_chain_accepts_genuine_atomic_one_step_label_transitions(tmp_path):
    rev1 = _copy_initial(tmp_path)
    rev2, audit1, reference, _stage1_capability = _atomic_stage1(tmp_path, rev1)
    rev3 = tmp_path / "registry_v003.json"
    audit2 = tmp_path / "validation_audit.json"
    capability = transition_label_access_atomically(
        registry_path=rev2,
        next_registry_path=rev3,
        audit_path=audit2,
        subject_id="P001",
        next_state="validation_opened",
        operation=ReferenceOperation.VALIDATION_SCORING,
        utc="2030-01-01T00:00:01Z",
        capture_git_commit="abc123",
        capture_git_dirty=False,
        config_sha256="a" * 64,
        scorer_sha256="b" * 64,
        session_id="P001_natural",
        arm="natural",
        reference_path=reference,
        reference_sha256=sha256_file(reference),
        previous_audit_path=audit1,
    )
    assert len(validate_registry_chain([rev1, rev2, rev3])) == 3
    assert capability.registry_sha256 == sha256_file(rev3)


@pytest.mark.parametrize("subject_id", [f"P{slot:03d}" for slot in range(1, 16)])
def test_public_registry_revision_cannot_mutate_prospective_label_state(tmp_path, subject_id):
    rev1 = _copy_initial(tmp_path)
    output = tmp_path / "registry_v002.json"
    with pytest.raises(RegistryError, match="label_state|atomic|firewall"):
        write_registry_revision(
            rev1,
            output,
            subject_updates={subject_id: {"label_state": "stage1_reference_only"}},
        )
    assert not output.exists()
    assert not output.with_suffix(output.suffix + ".sha256").exists()


def test_public_registry_cannot_skip_prospective_label_state(tmp_path):
    rev1 = _copy_initial(tmp_path)
    with pytest.raises(
        RegistryError, match="forbidden label-state transition|label_state|atomic|firewall"
    ):
        write_registry_revision(
            rev1, tmp_path / "skip.json",
            subject_updates={"P001": {"label_state": "validation_opened"}},
        )


def test_chain_rejects_deleted_reordered_altered_and_wrong_predecessor(tmp_path):
    rev1 = _copy_initial(tmp_path)
    rev2, _audit, _reference, _capability = _atomic_stage1(tmp_path, rev1)
    with pytest.raises(RegistryError, match="complete, consecutive, and ordered"):
        validate_registry_chain([rev2])
    with pytest.raises(RegistryError):
        validate_registry_chain([rev2, rev1])

    altered = tmp_path / "altered_v001.json"
    document = load_registry(rev1)
    document["subjects"][0]["sessions"][0]["state"] = "missing"
    altered.write_bytes(canonical_json_bytes(document))
    altered.with_suffix(altered.suffix + ".sha256").write_text(
        sha256_file(altered) + "\n", encoding="ascii"
    )
    with pytest.raises(RegistryError, match="predecessor digest"):
        validate_registry_chain([altered, rev2])

    rev2_document = load_registry(rev2)
    rev2_document["previous_registry_sha256"] = "0" * 64
    wrong = tmp_path / "wrong_v002.json"
    wrong.write_bytes(canonical_json_bytes(rev2_document))
    wrong.with_suffix(wrong.suffix + ".sha256").write_text(
        sha256_file(wrong) + "\n", encoding="ascii"
    )
    with pytest.raises(RegistryError, match="predecessor digest"):
        validate_registry_chain([rev1, wrong])


def test_revision_rejects_role_slot_session_mutation_and_terminal_state_repair(tmp_path):
    rev1 = _copy_initial(tmp_path)
    for update in (
        {"data_role": "final_evaluation"},
        {"cohort_slot": 2},
        {"sessions": {"P001_natural": {"session_id": "P002_natural"}}},
    ):
        with pytest.raises(RegistryError):
            write_registry_revision(
                rev1, tmp_path / f"bad_{len(list(tmp_path.iterdir()))}.json",
                subject_updates={"P001": update},
            )

    rev2 = write_registry_revision(
        rev1, tmp_path / "registry_v002.json",
        subject_updates={"P001": {"sessions": {"P001_recovery": {"state": "missing"}}}},
    )
    with pytest.raises(RegistryError, match="terminal session state"):
        write_registry_revision(
            rev2, tmp_path / "registry_v003.json",
            subject_updates={"P001": {"sessions": {"P001_recovery": {"state": "captured", "radar_receipt_sha256": "a" * 64}}}},
        )


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("protocol_abort", RetryReason.PROTOCOL_ABORT),
        ("corrupt_raw", RetryReason.CORRUPT_RAW),
        ("epoch_sync_failure", RetryReason.EPOCH_SYNC_FAILURE),
    ],
)
def test_exact_permitted_retry_predicates(reason, expected):
    predecessor, replacement = retry_pair(reason)
    assert validate_retry_pair(predecessor, replacement) is expected


@pytest.mark.parametrize(
    "forbidden",
    [
        "packet_loss", "invalid_frames", "missing_reference", "low_warmup_confidence",
        "recovery_stage1_failure", "recovery_stage2_failure", "low_radar_coverage",
        "poor_agreement", "participant_withdrawal",
    ],
)
def test_yield_outcome_and_packet_conditions_never_authorize_retry(forbidden):
    predecessor, replacement = retry_pair(forbidden)
    with pytest.raises(ContractError, match="retry_reason"):
        validate_retry_pair(predecessor, replacement)


def test_retry_requires_symmetric_links_exact_identity_and_sealed_state():
    predecessor, replacement = retry_pair()
    replacement["replaces_session_id"] = "wrong"
    with pytest.raises(ContractError, match="symmetrically"):
        validate_retry_pair(predecessor, replacement)

    for field in (
        "subject_id", "data_role", "cohort_slot", "arm", "commanded_rate_bpm",
        "protocol_identity_sha256", "capture_config_sha256",
    ):
        predecessor, replacement = retry_pair()
        replacement[field] = "changed"
        with pytest.raises(ContractError, match=field):
            validate_retry_pair(predecessor, replacement)

    predecessor, replacement = retry_pair()
    predecessor["label_state"] = "stage1_reference_only"
    with pytest.raises(ContractError, match="sealed"):
        validate_retry_pair(predecessor, replacement)


@pytest.mark.parametrize("offset", [-1.0, 1.0])
def test_epoch_retry_does_not_trigger_at_exact_clock_boundary(offset):
    predecessor, replacement = retry_pair("epoch_sync_failure")
    predecessor["retry_evidence"] = {
        "clock_offset_start_s": offset, "clock_offset_end_s": -offset
    }
    with pytest.raises(ContractError, match="strictly above"):
        validate_retry_pair(predecessor, replacement)


def test_recovery_cannot_use_the_normal_arm_settle_failure_retry_predicate():
    predecessor, replacement = retry_pair("protocol_abort")
    predecessor["arm"] = replacement["arm"] = "recovery"
    with pytest.raises(ContractError, match="normal|settle|recovery"):
        validate_retry_pair(predecessor, replacement)


def test_membership_consumer_rejects_successor_after_predecessor_was_mutated(tmp_path):
    rev1 = _copy_initial(tmp_path)
    rev2 = write_registry_revision(rev1, tmp_path / "registry_v002.json", subject_updates={})
    mutated = load_registry(rev1)
    mutated["subjects"][0]["paced_rate_bpm"] = 15
    rev1.write_bytes(canonical_json_bytes(mutated))
    with pytest.raises(RegistryError, match="chain|predecessor|digest|history"):
        validate_membership(
            rev2, subject_id="P001", cohort_slot=1,
            data_role=DataRole.REPRESENTATION_VALIDATION,
            session_id="P001_natural", arm=Arm.NATURAL,
        )


def test_registry_revision_appends_retry_attempts_under_fixed_slot_without_rewriting_prior_attempt(tmp_path):
    rev1 = _copy_initial(tmp_path)
    first, second = retry_pair("protocol_abort")
    rev2 = write_registry_revision(
        rev1, tmp_path / "registry_v002.json",
        subject_updates={"P001": {"sessions": {"P001_natural": {"attempts": [first]}}}},
    )
    write_registry_revision(
        rev2, tmp_path / "registry_v003.json",
        subject_updates={"P001": {"sessions": {"P001_natural": {"attempts": [first, second]}}}},
    )
