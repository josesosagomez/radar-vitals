"""Independent tests for the prospective v3 manifest and v2 dispatch boundary."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "m2"))

from builders import acquisition_metadata  # noqa: E402
from src.m2.acquisition_metadata import (  # noqa: E402
    Arm,
    ContractError,
    DataRole,
    validate_acquisition_metadata,
    validate_finalization_metadata,
)
from src.m2.manifest import load_manifest  # noqa: E402
from src.m2.manifest_v3 import (  # noqa: E402
    FRAME0_EVENT_SOURCE,
    ManifestError,
    Mode,
    parse_session_v3,
)
from src.m2.preflight import build_synthetic_dry_run  # noqa: E402
from src.m2.common import canonical_json_bytes, sha256_file  # noqa: E402


COMMON_REQUIRED = (
    "subject_id", "cohort_slot", "data_role", "session_id", "arm", "visit_number",
    "visit_utc", "expected_reference_basename", "posture", "back_straight",
    "both_hands_on_thighs", "facing_radar", "radar_at_chest_height",
    "radar_face_horizontal", "phone_level_check_passed", "sensored_hand_still",
    "distance_m", "scene_description_category", "scene_non_health_notes", "scene_changed",
    "quiet_room_confirmed", "no_walkers_confirmed", "no_fan_airflow_confirmed",
    "radar_ready", "dca1000_ready", "configured_chirps_per_frame",
    "configured_frame_rate_hz", "masimo_battery_ready", "masimo_logging_ready",
    "masimo_pr_ready", "masimo_pi_ready", "clock_offset_start_s", "intended_duration_s",
    "session_order", "disturbances_category",
)
RECOVERY_REQUIRED = (
    "screening_completed", "recovery_clearance_attested",
    "amended_materials_current_consent_confirmed", "researcher_present",
    "exertion_modality", "suitable_footwear_confirmed",
    "dry_unobstructed_area_confirmed", "pre_exertion_resting_pr_bpm",
    "exertion_stop_pr_bpm", "exertion_duration_s", "seated_pr_t0_bpm",
    "seated_at_record_start", "still_at_record_start", "hands_resting_at_record_start",
    "no_pacing_confirmed", "stopping_event_category",
)
RECOVERY_RUNTIME_REQUIRED = (
    "recovery_seated_start_utc",
    "recovery_seated_start_event_source",
    "sit_to_record_delay_s",
)


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    path = tmp_path_factory.mktemp("m2_manifest") / "bundle"
    build_synthetic_dry_run(path)
    return path


def _session(bundle: Path) -> dict:
    return json.loads((bundle / "session_manifest_v3.json").read_text(encoding="utf-8"))[
        "sessions"
    ][0]


def _load_changed(bundle: Path, tmp_path: Path, session: dict):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"manifest_schema_version": 3, "sessions": [session]}),
        encoding="utf-8",
    )
    return load_manifest(manifest, Mode.SCORING, root=bundle)


def test_complete_v3_scoring_manifest_loads_and_preserves_start_assignment(bundle):
    (session,) = load_manifest(bundle / "session_manifest_v3.json", Mode.SCORING, root=bundle)
    assert session.is_scorable
    assert session.frame0_event_source == FRAME0_EVENT_SOURCE
    assert session.frame0_epoch == 1_900_000_000.0
    assert session.data_role is DataRole.REPRESENTATION_VALIDATION
    assert session.arm is Arm.RECOVERY


def test_recovery_manifest_binds_runtime_seating_event_and_exact_derived_delay(bundle):
    session = _session(bundle)
    assert session["recovery_seated_start_utc"] == 1_899_999_980.0
    assert (
        session["recovery_seated_start_event_source"]
        == "operator_observed_seated_start_synchronized_pc_utc"
    )
    assert session["sit_to_record_delay_s"] == pytest.approx(
        session["frame0_epoch"] - session["recovery_seated_start_utc"]
    )
    assert "sit_to_record_delay_s" not in session["acquisition_metadata"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("recovery_seated_start_utc", 1_900_000_000.001),
        ("recovery_seated_start_event_source", "post_hoc_operator_edit"),
        ("sit_to_record_delay_s", 19.0),
    ],
)
def test_recovery_manifest_rejects_future_or_posthoc_edited_timing(bundle, field, value):
    session = _session(bundle)
    session[field] = value
    with pytest.raises((ManifestError, ContractError), match="recovery|seated|future|source|delay"):
        parse_session_v3(session, Mode.DEVELOPMENT, root=bundle)


@pytest.mark.parametrize("missing", COMMON_REQUIRED + RECOVERY_REQUIRED)
def test_recovery_metadata_rejects_every_missing_required_field_by_name(missing):
    metadata = acquisition_metadata()
    del metadata[missing]
    with pytest.raises(ContractError) as error:
        validate_acquisition_metadata(metadata)
    assert missing in str(error.value)


def test_all_arms_validate_and_recovery_is_structurally_exempt_from_stable_settle():
    natural = validate_acquisition_metadata(acquisition_metadata("natural"))
    paced = validate_acquisition_metadata(acquisition_metadata("paced"))
    recovery = validate_acquisition_metadata(acquisition_metadata("recovery"))
    assert natural["settle_pr_spread_bpm"] == 5.0
    assert paced["settle_pr_drift_bpm"] == 3.0
    assert not any(key.startswith("settle_") for key in recovery)

    for forbidden in ("settle_duration_s", "settle_pr_spread_bpm", "settle_pr_drift_bpm"):
        malformed = acquisition_metadata("recovery")
        malformed[forbidden] = 0.0
        with pytest.raises(ContractError, match="recovery.*(?:exempt|unknown fields)"):
            validate_acquisition_metadata(malformed)


def test_recovery_sidecar_cannot_prestate_runtime_derived_sit_to_record_delay():
    pending = acquisition_metadata("recovery")
    assert "sit_to_record_delay_s" not in pending
    assert validate_acquisition_metadata(pending)["arm"] == "recovery"

    guessed = dict(pending, sit_to_record_delay_s=20.0)
    with pytest.raises(ContractError, match="runtime|derived|sit_to_record_delay_s|unknown"):
        validate_acquisition_metadata(guessed)


@pytest.mark.parametrize(
    "stopping_event_category", ["participant_stop", "researcher_safety_stop"]
)
def test_recovery_metadata_preserves_controlled_non_target_stop_for_nonacquisition_record(
    stopping_event_category,
):
    metadata = acquisition_metadata("recovery")
    metadata["stopping_event_category"] = stopping_event_category
    validated = validate_acquisition_metadata(metadata)
    assert validated["stopping_event_category"] == stopping_event_category


@pytest.mark.parametrize(
    "stopping_event_category", ["participant_stop", "researcher_safety_stop"]
)
def test_acquired_recovery_manifest_rejects_non_target_stop(bundle, stopping_event_category):
    session = _session(bundle)
    session["acquisition_metadata"]["stopping_event_category"] = stopping_event_category
    with pytest.raises(
        (ManifestError, ContractError), match="target_reached|non-acquisition|participant|safety"
    ):
        parse_session_v3(session, Mode.DEVELOPMENT, root=bundle)


@pytest.mark.parametrize(
    ("role", "subject", "synthetic", "accepted"),
    [
        ("representation_validation", "P001", False, True),
        ("final_evaluation", "P006", False, True),
        ("engineering_only", "T001", False, True),
        ("development", "D001", False, True),
        ("engineering_only", "P001", False, False),
        ("final_evaluation", "T001", True, False),
    ],
)
def test_v3_roles_use_separate_identity_rules(role, subject, synthetic, accepted):
    slot = 6 if role == "final_evaluation" and subject == "P006" else 1
    metadata = acquisition_metadata(
        subject_id=subject,
        cohort_slot=slot,
        data_role=role,
        synthetic_fixture=synthetic,
    )
    if accepted:
        assert validate_acquisition_metadata(metadata)["data_role"] == role
    else:
        with pytest.raises(ContractError):
            validate_acquisition_metadata(metadata)


@pytest.mark.parametrize("offset", [-1.0, 1.0])
def test_clock_offset_exact_inclusive_boundaries_pass(bundle, offset):
    session = _session(bundle)
    session["clock_offset_start_s"] = offset
    session["acquisition_metadata"]["clock_offset_start_s"] = offset
    session["clock_offset_end_s"] = -offset
    session["finalization_metadata"]["clock_offset_end_s"] = -offset
    parsed = parse_session_v3(session, Mode.DEVELOPMENT)
    assert parsed.clock_offset_start_s == offset
    assert parsed.clock_offset_end_s == -offset


@pytest.mark.parametrize("offset", [-1.0000001, 1.0000001])
def test_clock_offset_one_epsilon_outside_fails_for_start_and_end(bundle, offset):
    session = _session(bundle)
    session["clock_offset_start_s"] = offset
    session["acquisition_metadata"]["clock_offset_start_s"] = offset
    with pytest.raises(ManifestError, match="clock_offset_start_s|clock offsets"):
        parse_session_v3(session, Mode.DEVELOPMENT)

    session = _session(bundle)
    session["clock_offset_end_s"] = offset
    session["finalization_metadata"]["clock_offset_end_s"] = offset
    with pytest.raises(ManifestError, match="clock_offset_end_s|clock offsets"):
        parse_session_v3(session, Mode.DEVELOPMENT)


@pytest.mark.parametrize(
    "missing",
    (
        "session_id", "subject_id", "cohort_slot", "data_role", "arm", "visit_number",
        "disposition", "acquisition_metadata", "finalization_metadata", "frame0_epoch",
        "frame0_event_source", "clock_offset_start_s", "clock_offset_end_s",
        "intended_duration_s", "actual_duration_s", "capture_git_commit",
        "capture_git_dirty", "exact_cli_invocation", "prospective_contract_enforced",
        "packets_received", "packets_dropped", "packets_short_discarded",
        "packets_duplicate_or_late_discarded", "packet_loss_ratio",
        "packet_loss_above_5_percent", "n_frames", "n_invalid_frames",
        "trailing_partial_frame_bytes", "bytes_per_frame", "reference_acquired",
        "raw_path", "raw_sha256", "source_config_yaml_path", "source_config_yaml_sha256",
        "effective_config_path", "effective_config_sha256", "capture_metadata_path",
        "capture_metadata_sha256", "frame_validity_map_path", "frame_validity_map_sha256",
        "radar_receipt_path", "radar_receipt_sha256", "reference_acquisition_path",
        "reference_acquisition_sha256", "cohort_registry_path", "registry_sha256",
        "reference_path", "reference_sha256",
        *RECOVERY_RUNTIME_REQUIRED,
    ),
)
def test_scoring_manifest_rejects_each_missing_required_field_with_named_error(
    bundle, tmp_path, missing
):
    session = _session(bundle)
    del session[missing]
    with pytest.raises((ManifestError, ContractError)) as error:
        _load_changed(bundle, tmp_path, session)
    message = str(error.value)
    if missing in {"reference_path", "reference_sha256"}:
        assert "reference path/hash" in message
    else:
        assert missing in message, "failure must identify the omitted contract field"


def test_scoring_rejects_development_and_engineering_roles(bundle):
    for role, subject in (("development", "D001"), ("engineering_only", "T001")):
        session = _session(bundle)
        metadata = acquisition_metadata(
            subject_id=subject, data_role=role, synthetic_fixture=False
        )
        session.update(
            {
                "session_id": metadata["session_id"],
                "subject_id": subject,
                "data_role": role,
                "acquisition_metadata": metadata,
            }
        )
        with pytest.raises(ManifestError, match="never scoring-loadable"):
            parse_session_v3(session, Mode.SCORING, root=bundle)


def test_v2_dispatch_retains_historical_m4_semantics(tmp_path):
    from test_m4_manifest import admissible, manifest_doc, materialise

    fields = materialise(tmp_path, admissible())
    path = tmp_path / "v2.json"
    path.write_text(json.dumps(manifest_doc(fields)), encoding="utf-8")
    (session,) = load_manifest(path, Mode.SCORING, root=tmp_path)
    assert session.session_id == "S01_natural"
    assert session.raw_digest_ok is True
    assert not hasattr(session, "cohort_slot")


@pytest.mark.parametrize("version", [None, 0, 1, 4, "3", True])
def test_dispatch_is_exact_and_rejects_unknown_or_noninteger_versions(tmp_path, version):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"manifest_schema_version": version, "sessions": []}))
    with pytest.raises(ManifestError, match="manifest_schema_version"):
        load_manifest(path, Mode.SCORING, root=tmp_path)


def test_recovery_final_monitoring_boundary_is_inclusive():
    metadata = {
        "clock_offset_end_s": 1.0,
        "actual_duration_s": 600.0,
        "final_pr_bpm": 70.0,
        "reference_acquired": False,
        "reference_missing_reason": "file_missing",
        "post_monitoring_pr_bpm": 75.0,
        "monitoring_duration_s": 0.0,
    }
    validate_finalization_metadata(
        metadata, arm=Arm.RECOVERY, pre_exertion_resting_pr_bpm=70.0
    )
    metadata["post_monitoring_pr_bpm"] = 75.000001
    with pytest.raises(ContractError, match="within 5 bpm"):
        validate_finalization_metadata(
            metadata, arm=Arm.RECOVERY, pre_exertion_resting_pr_bpm=70.0
        )


def _rewrite_json_binding(bundle: Path, artifact_name: str, hash_key: str, mutate):
    manifest_path = bundle / "session_manifest_v3.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact_path = bundle / artifact_name
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    mutate(artifact)
    artifact_path.write_text(json.dumps(artifact, sort_keys=True), encoding="utf-8")
    document["sessions"][0][hash_key] = sha256_file(artifact_path)
    manifest_path.write_text(json.dumps(document), encoding="utf-8")


@pytest.mark.parametrize(
    ("artifact_name", "hash_key", "mutation"),
    [
        (
            "sealed_radar_receipt.json", "radar_receipt_sha256",
            {"frame0_epoch": -123.0, "capture_git_commit": "foreign",
             "exact_cli_invocation": ["foreign"], "packets_received": 999,
             "session_id": "T999_recovery"},
        ),
        (
            "run_metadata.json", "capture_metadata_sha256",
            {"prospective_study_mode": False, "frame0_epoch_utc": -123.0,
             "git_commit": "foreign", "session_id": "T999_recovery"},
        ),
        (
            "reference_acquisition.json", "reference_acquisition_sha256",
            {"actual_duration_s": 599.0, "clock_offset_end_s": 0.75,
             "session_id": "T999_recovery"},
        ),
    ],
)
def test_v3_rejects_rehashed_artifacts_whose_semantics_disagree_with_manifest(
    tmp_path, artifact_name, hash_key, mutation
):
    bundle = tmp_path / "bundle"
    build_synthetic_dry_run(bundle)
    _rewrite_json_binding(bundle, artifact_name, hash_key, lambda artifact: artifact.update(mutation))
    with pytest.raises(ManifestError, match="receipt|metadata|reference|disagree|semantic"):
        load_manifest(bundle / "session_manifest_v3.json", Mode.SCORING, root=bundle)


def test_v3_rejects_rehashed_effective_config_semantically_inconsistent_with_capture(tmp_path):
    bundle = tmp_path / "bundle"
    build_synthetic_dry_run(bundle)
    manifest_path = bundle / "session_manifest_v3.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    effective = bundle / "effective_config.json"
    effective.write_text(json.dumps({"synthetic_fixture": True, "frame_rate_hz": 19.0}))
    document["sessions"][0]["effective_config_sha256"] = sha256_file(effective)
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ManifestError, match="effective|frame_rate|config|disagree"):
        load_manifest(manifest_path, Mode.SCORING, root=bundle)


def test_v3_rejects_packet_counter_tamper_even_when_ratio_and_flag_are_recomputed(tmp_path):
    bundle = tmp_path / "bundle"
    build_synthetic_dry_run(bundle)
    manifest_path = bundle / "session_manifest_v3.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    session = document["sessions"][0]
    session["packets_received"] += 1
    session["packet_loss_ratio"] = session["packets_dropped"] / session["packets_received"]
    session["packet_loss_above_5_percent"] = (
        20 * session["packets_dropped"] > session["packets_received"]
    )
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ManifestError, match="byte|packet|conservation|trailing"):
        load_manifest(manifest_path, Mode.SCORING, root=bundle)


@pytest.mark.parametrize(
    "binding",
    ["radar_receipt", "reference_acquisition", "cohort_registry"],
)
def test_v3_rejects_coordinated_removal_of_required_path_and_hash_binding(tmp_path, binding):
    bundle = tmp_path / "bundle"
    build_synthetic_dry_run(bundle)
    manifest_path = bundle / "session_manifest_v3.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    session = document["sessions"][0]
    path_key = f"{binding}_path"
    hash_key = "registry_sha256" if binding == "cohort_registry" else f"{binding}_sha256"
    session.pop(path_key)
    session.pop(hash_key)
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(
        (ManifestError, ContractError),
        match="required|receipt|reference|registry|non-empty string",
    ):
        load_manifest(manifest_path, Mode.SCORING, root=bundle)


def test_v3_rejects_rehashed_registry_whose_captured_slot_binds_another_receipt(tmp_path):
    bundle = tmp_path / "bundle"
    build_synthetic_dry_run(bundle)
    manifest_path = bundle / "session_manifest_v3.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))
    registry_path = bundle / "cohort_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["subjects"][0]["sessions"][2]["radar_receipt_sha256"] = "f" * 64
    registry_path.write_bytes(canonical_json_bytes(registry))
    document["sessions"][0]["registry_sha256"] = sha256_file(registry_path)
    manifest_path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ManifestError, match="registry|receipt|captured|binding"):
        load_manifest(manifest_path, Mode.SCORING, root=bundle)
