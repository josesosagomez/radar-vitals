"""Ordered M2 artifact preflight and isolated synthetic engineering dry run."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from .acquisition_metadata import Arm, DataRole, validate_acquisition_metadata, validate_finalization_metadata
from .cohort_registry import role_membership_digest, validate_membership
from .common import (
    ContractError,
    canonical_json_bytes,
    read_json_object,
    require_bool,
    require_int,
    require_nonempty_string,
    require_sha256,
    sha256_file,
    write_new_bytes,
    write_new_json,
)
from .label_firewall import ReferenceOperation, authorize_scoring
from .manifest_v3 import FRAME0_EVENT_SOURCE, Mode, load_manifest_v3
from .time_sensitivity import (
    TIME_SHIFTS_S,
    build_agreement_sensitivity,
    build_reference_only_sensitivity,
)
from .validity import invalid_window_indices, materialize_radar_nan_windows


def _verify_binding(root: Path, session: Mapping[str, object], path_key: str, hash_key: str) -> None:
    path = root / require_nonempty_string(session, path_key)
    expected = require_sha256(session.get(hash_key), hash_key)
    if not path.is_file() or sha256_file(path) != expected:
        raise ContractError(f"{path_key}/{hash_key} binding failed")


def validate_preflight(
    manifest_path: str | Path,
    *,
    root: str | Path | None = None,
    reference_sensitivity_path: str | Path | None = None,
) -> dict[str, object]:
    """Run the accepted seven gates in their declared order and return an audit report."""
    path = Path(manifest_path)
    artifact_root = Path(root).resolve() if root is not None else path.parent.resolve()
    document = read_json_object(path)
    sessions = document.get("sessions")
    if document.get("manifest_schema_version") != 3 or type(sessions) is not list or len(sessions) != 1:
        raise ContractError("preflight expects one version-3 session manifest")
    session = sessions[0]
    if type(session) is not dict:
        raise ContractError("preflight session must be an object")
    stages: list[str] = []

    # 1. Clean capture commit and exact source/effective configuration identities.
    if require_bool(session, "capture_git_dirty") or session.get("capture_git_commit") in {None, "unknown"}:
        raise ContractError("stage 1: capture commit is absent or dirty")
    _verify_binding(artifact_root, session, "source_config_yaml_path", "source_config_yaml_sha256")
    _verify_binding(artifact_root, session, "effective_config_path", "effective_config_sha256")
    stages.append("clean_commit_and_config_identity")

    # 2. Immutable registry identity and subject/slot/role/arm membership.
    _verify_binding(artifact_root, session, "cohort_registry_path", "registry_sha256")
    validate_membership(
        artifact_root / str(session["cohort_registry_path"]),
        subject_id=str(session["subject_id"]),
        cohort_slot=int(session["cohort_slot"]),
        data_role=DataRole(session["data_role"]),
        session_id=str(session["session_id"]),
        arm=Arm(session["arm"]),
    )
    stages.append("registry_membership")

    # 3. Arm-specific protocol metadata and inclusive clock gates.
    metadata = validate_acquisition_metadata(session["acquisition_metadata"])
    validate_finalization_metadata(
        session["finalization_metadata"],
        arm=Arm(session["arm"]),
        pre_exertion_resting_pr_bpm=(
            float(metadata["pre_exertion_resting_pr_bpm"])
            if session["arm"] == "recovery"
            else None
        ),
    )
    if abs(float(session["clock_offset_start_s"])) > 1.0 or abs(float(session["clock_offset_end_s"])) > 1.0:
        raise ContractError("stage 3: PC-phone offset exceeds inclusive +/-1 second gate")
    stages.append("arm_metadata_and_clock_offsets")

    # 4. Raw/map/config/reference-acquisition bindings.
    for path_key, hash_key in (
        ("raw_path", "raw_sha256"),
        ("frame_validity_map_path", "frame_validity_map_sha256"),
        ("capture_metadata_path", "capture_metadata_sha256"),
        ("radar_receipt_path", "radar_receipt_sha256"),
        ("reference_acquisition_path", "reference_acquisition_sha256"),
    ):
        _verify_binding(artifact_root, session, path_key, hash_key)
    if session["arm"] in {"natural", "paced"}:
        _verify_binding(artifact_root, session, "settle_evidence_path", "settle_evidence_sha256")
    if session.get("reference_acquired"):
        _verify_binding(artifact_root, session, "reference_path", "reference_sha256")
    stages.append("artifact_hash_bindings")

    # 5. Packet counters, exact frame origin, map shape/count, and duration grid.
    received = require_int(session, "packets_received", minimum=0)
    dropped = require_int(session, "packets_dropped", minimum=0)
    if received == 0 or require_bool(session, "packet_loss_above_5_percent") != (
        20 * dropped > received
    ):
        raise ContractError("stage 5: packet counts/loss flag are inconsistent")
    if session.get("frame0_event_source") != FRAME0_EVENT_SOURCE:
        raise ContractError("stage 5: frame origin event is not the start-assignment event")
    validity = np.load(artifact_root / str(session["frame_validity_map_path"]), allow_pickle=False)
    if validity.dtype != np.bool_ or validity.ndim != 1:
        raise ContractError("stage 5: validity map is not one-dimensional Boolean")
    if validity.size != int(session["n_frames"]) or int(np.count_nonzero(~validity)) != int(
        session["n_invalid_frames"]
    ):
        raise ContractError("stage 5: validity map length/count mismatch")
    if session.get("intended_duration_s") != 600.0 or session.get("actual_duration_s") != 600.0:
        raise ContractError("stage 5: prospective duration is not exactly 600 seconds")
    stages.append("packet_map_origin_duration")

    # 6. Full scoring-mode v3 loading (re-checks every binding as one capability boundary).
    loaded = load_manifest_v3(path, Mode.SCORING, root=artifact_root)
    stages.append("v3_scoring_load")

    # 7. Optional reference-only fixed-sensitivity shape.
    if reference_sensitivity_path is not None:
        sensitivity = read_json_object(reference_sensitivity_path)
        if sensitivity.get("artifact_kind") != "reference_only_sensitivity":
            raise ContractError("stage 7: sensitivity artifact is not reference-only")
        if sensitivity.get("fixed_shifts_s") != list(TIME_SHIFTS_S):
            raise ContractError("stage 7: sensitivity shifts are not [-1,0,+1]")
        shift_values = [entry.get("shift_s") for entry in sensitivity.get("shifts", [])]
        if shift_values != list(TIME_SHIFTS_S):
            raise ContractError("stage 7: per-shift artifact order/shape is wrong")
        forbidden = {"best", "selected", "optimizer", "promotion"}
        if forbidden.intersection(sensitivity):
            raise ContractError("stage 7: sensitivity artifact contains a selection field")
        stages.append("reference_sensitivity_shape")
    else:
        stages.append("reference_sensitivity_not_supplied")
    return {
        "schema": "m2_preflight_report_v1",
        "status": "pass",
        "session_id": loaded[0].session_id,
        "stages": stages,
        "invalid_window_indices": list(invalid_window_indices(validity)),
    }


def _synthetic_recovery_metadata() -> dict[str, object]:
    return {
        "synthetic_fixture": True,
        "subject_id": "T001",
        "cohort_slot": 1,
        "data_role": "representation_validation",
        "session_id": "T001_recovery",
        "arm": "recovery",
        "visit_number": 3,
        "visit_utc": "2030-01-01T00:00:00Z",
        "expected_reference_basename": "T001_recovery_reference.csv",
        "posture": "seated",
        "back_straight": True,
        "both_hands_on_thighs": True,
        "facing_radar": True,
        "radar_at_chest_height": True,
        "radar_face_horizontal": True,
        "phone_level_check_passed": True,
        "sensored_hand_still": True,
        "distance_m": 1.0,
        "scene_description_category": "clear_dominant_subject",
        "scene_non_health_notes": "synthetic empty-room fixture",
        "scene_changed": False,
        "quiet_room_confirmed": True,
        "no_walkers_confirmed": True,
        "no_fan_airflow_confirmed": True,
        "radar_ready": True,
        "dca1000_ready": True,
        "configured_chirps_per_frame": 32,
        "configured_frame_rate_hz": 20.0,
        "masimo_battery_ready": True,
        "masimo_logging_ready": True,
        "masimo_pr_ready": True,
        "masimo_pi_ready": True,
        "clock_offset_start_s": -1.0,
        "intended_duration_s": 600.0,
        "session_order": 3,
        "disturbances_category": "none",
        "screening_completed": True,
        "recovery_clearance_attested": True,
        "amended_materials_current_consent_confirmed": True,
        "researcher_present": True,
        "exertion_modality": "self_paced_step_ups",
        "suitable_footwear_confirmed": True,
        "dry_unobstructed_area_confirmed": True,
        "pre_exertion_resting_pr_bpm": 70.0,
        "exertion_stop_pr_bpm": 110.0,
        "exertion_duration_s": 120.0,
        "seated_pr_t0_bpm": 105.0,
        "seated_at_record_start": True,
        "still_at_record_start": True,
        "hands_resting_at_record_start": True,
        "no_pacing_confirmed": True,
        "stopping_event_category": "target_reached",
    }


def build_synthetic_dry_run(output_dir: str | Path) -> dict[str, object]:
    """Create a self-contained, explicitly synthetic v3 preflight directory."""
    output = Path(output_dir)
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise ContractError(f"dry-run output already exists: {output}") from exc
    metadata = validate_acquisition_metadata(_synthetic_recovery_metadata())
    receipt_path = output / "sealed_radar_receipt.json"
    registry = {
        "schema": "m2_cohort_registry_v1",
        "registry_kind": "synthetic_fixture",
        "revision": 1,
        "previous_registry_sha256": None,
        "role_membership_sha256": {
            "representation_validation": role_membership_digest(["T001"])
        },
        "subjects": [
            {
                "subject_id": "T001",
                "cohort_slot": 1,
                "data_role": "representation_validation",
                "paced_rate_bpm": 12,
                "label_state": "validation_opened",
                "sessions": [
                    {"session_id": "T001_natural", "arm": "natural", "visit_number": 1, "state": "planned"},
                    {"session_id": "T001_paced", "arm": "paced", "visit_number": 2, "state": "planned"},
                    {
                        "session_id": "T001_recovery",
                        "arm": "recovery",
                        "visit_number": 3,
                        "state": "captured",
                        "radar_receipt_sha256": "0" * 64,
                    },
                ],
            }
        ],
    }
    registry_path = write_new_json(output / "cohort_registry.json", registry)
    write_new_bytes(
        registry_path.with_suffix(registry_path.suffix + ".sha256"),
        (sha256_file(registry_path) + "\n").encode("ascii"),
    )
    write_new_bytes(output / "source_config.yaml", b"synthetic_fixture: true\nframe_rate_hz: 20.0\n")
    write_new_json(output / "effective_config.json", {"synthetic_fixture": True, "frame_rate_hz": 20.0})
    run_metadata_path = output / "run_metadata.json"

    # One byte stands for one synthetic frame.  This exercises stream/hash/window integrity
    # without fabricating 1.5 GB of physical ADC data.
    raw_path = write_new_bytes(output / "adc_stream.bin", b"\x00" * 1_092_000)
    validity = np.ones(12000, dtype=np.bool_)
    validity[599] = False
    validity[600] = False
    np.save(output / "frame_validity.npy", validity)

    acquisition_sidecar_path = write_new_bytes(
        output / "acquisition_sidecar.yaml", canonical_json_bytes(metadata)
    )
    exact_cli_invocation = [
        "m2_preflight.py",
        "--dry-run",
        "--recovery-seated-start-utc",
        "1899999980.0",
    ]
    recovery_timing = {
        "recovery_seated_start_utc": 1_899_999_980.0,
        "recovery_seated_start_event_source": (
            "operator_observed_seated_start_synchronized_pc_utc"
        ),
        "sit_to_record_delay_s": 20.0,
    }
    packet_stats = {
        "packets_received": 750,
        "packets_dropped": 0,
        "packets_short_discarded": 1,
        "packets_duplicate_or_late_discarded": 1,
        "n_frames": 12000,
        "n_invalid_frames": 2,
        "trailing_partial_frame_bytes": 0,
        "bytes_per_frame": 91,
        "frame_validity_map_path": "frame_validity.npy",
        "frame_validity_map_sha256": sha256_file(output / "frame_validity.npy"),
    }
    write_new_json(
        run_metadata_path,
        {
            "completion_status": "completed",
            "mode": "live",
            "prospective_study_mode": True,
            "session_id": "T001_recovery",
            "git_commit": "SYNTHETIC_FIXTURE_NO_PHYSICAL_CAPTURE",
            "git_dirty": False,
            "frame0_epoch_source": FRAME0_EVENT_SOURCE,
            "frame0_epoch_utc": 1_900_000_000.0,
            "exact_cli_invocation": exact_cli_invocation,
            "acquisition_metadata": metadata,
            "live_packet_stats": packet_stats,
            **recovery_timing,
            "synthetic_fixture": True,
            "no_physical_capture": True,
        },
    )
    receipt = {
        "schema": "m2_sealed_radar_receipt_v1",
        "receipt_state": "sealed_radar_only",
        "engineering_dry_run": True,
        "acquisition_metadata": metadata,
        "session_id": "T001_recovery",
        "subject_id": "T001",
        "cohort_slot": 1,
        "data_role": "representation_validation",
        "arm": "recovery",
        "visit_number": 3,
        "frame0_epoch": 1_900_000_000.0,
        "frame0_event_source": FRAME0_EVENT_SOURCE,
        "clock_offset_start_s": -1.0,
        "capture_git_commit": "SYNTHETIC_FIXTURE_NO_PHYSICAL_CAPTURE",
        "capture_git_dirty": False,
        "exact_cli_invocation": exact_cli_invocation,
        "prospective_contract_enforced": True,
        "raw_path": raw_path.name,
        "raw_sha256": sha256_file(raw_path),
        "source_config_yaml_path": "source_config.yaml",
        "source_config_yaml_sha256": sha256_file(output / "source_config.yaml"),
        "effective_config_path": "effective_config.json",
        "effective_config_sha256": sha256_file(output / "effective_config.json"),
        "capture_metadata_path": run_metadata_path.name,
        "capture_metadata_sha256": sha256_file(run_metadata_path),
        "acquisition_sidecar_path": acquisition_sidecar_path.name,
        "acquisition_sidecar_sha256": sha256_file(acquisition_sidecar_path),
        "frame_validity_map_path": "frame_validity.npy",
        "frame_validity_map_sha256": sha256_file(output / "frame_validity.npy"),
        "registry_sha256": sha256_file(registry_path),
        **packet_stats,
        "n_invalid_frames": 2,
        "packet_loss_ratio": 0.0,
        "packet_loss_above_5_percent": False,
        **recovery_timing,
    }
    write_new_json(receipt_path, receipt)
    registry["subjects"][0]["sessions"][2]["radar_receipt_sha256"] = sha256_file(
        receipt_path
    )
    registry_path.write_bytes(canonical_json_bytes(registry))
    registry_path.with_suffix(registry_path.suffix + ".sha256").write_bytes(
        (sha256_file(registry_path) + "\n").encode("ascii")
    )

    epochs = np.arange(1_900_000_000, 1_900_000_601, dtype=np.int64)
    reference = pd.DataFrame(
        {
            "epoch_utc": epochs,
            "pr_bpm": np.linspace(105.0, 70.0, epochs.size),
            "pi": np.full(epochs.size, 5.0),
            "rr_bpm": np.full(epochs.size, 15.0),
        }
    )
    reference_path = output / "T001_recovery_reference.csv"
    reference.to_csv(reference_path, index=False)
    finalization = {
        "clock_offset_end_s": 1.0,
        "actual_duration_s": 600.0,
        "final_pr_bpm": 70.0,
        "reference_acquired": True,
        "reference_basename": reference_path.name,
        "reference_export_utc": "2030-01-01T00:10:01Z",
        "post_monitoring_pr_bpm": 74.0,
        "monitoring_duration_s": 60.0,
    }
    validate_finalization_metadata(
        finalization, arm=Arm.RECOVERY, pre_exertion_resting_pr_bpm=70.0
    )
    reference_acquisition_path = write_new_json(
        output / "reference_acquisition.json",
        {
            "schema": "m2_reference_acquisition_v1",
            "session_id": "T001_recovery",
            "expected_reference_basename": reference_path.name,
            **finalization,
        },
    )

    def binding(name: str) -> tuple[str, str]:
        path = output / name
        return name, sha256_file(path)

    session = {
        "session_id": "T001_recovery",
        "subject_id": "T001",
        "cohort_slot": 1,
        "data_role": "representation_validation",
        "arm": "recovery",
        "visit_number": 3,
        "disposition": "admitted",
        "acquisition_metadata": metadata,
        "finalization_metadata": finalization,
        "frame0_epoch": 1_900_000_000.0,
        "frame0_event_source": FRAME0_EVENT_SOURCE,
        "clock_offset_start_s": -1.0,
        "clock_offset_end_s": 1.0,
        "intended_duration_s": 600.0,
        "actual_duration_s": 600.0,
        "capture_git_commit": "SYNTHETIC_FIXTURE_NO_PHYSICAL_CAPTURE",
        "capture_git_dirty": False,
        "exact_cli_invocation": exact_cli_invocation,
        **recovery_timing,
        "prospective_contract_enforced": True,
        "packets_received": 750,
        "packets_dropped": 0,
        "packets_short_discarded": 1,
        "packets_duplicate_or_late_discarded": 1,
        "packet_loss_ratio": 0.0,
        "packet_loss_above_5_percent": False,
        "n_frames": 12000,
        "n_invalid_frames": 2,
        "trailing_partial_frame_bytes": 0,
        "bytes_per_frame": 91,
        "reference_acquired": True,
    }
    for path_key, filename in (
        ("raw_path", "adc_stream.bin"),
        ("source_config_yaml_path", "source_config.yaml"),
        ("effective_config_path", "effective_config.json"),
        ("capture_metadata_path", "run_metadata.json"),
        ("frame_validity_map_path", "frame_validity.npy"),
        ("radar_receipt_path", "sealed_radar_receipt.json"),
        ("reference_acquisition_path", "reference_acquisition.json"),
        ("cohort_registry_path", "cohort_registry.json"),
        ("reference_path", reference_path.name),
    ):
        session[path_key] = filename
        hash_key = "registry_sha256" if path_key == "cohort_registry_path" else path_key.replace("_path", "_sha256")
        session[hash_key] = sha256_file(output / filename)
    manifest_path = write_new_json(
        output / "session_manifest_v3.json",
        {"manifest_schema_version": 3, "sessions": [session]},
    )

    reference_artifact = build_reference_only_sensitivity(
        reference,
        subject_id="T001",
        session_id="T001_recovery",
        arm=Arm.RECOVERY,
        primary_frame0_epoch=1_900_000_000.0,
        n_complete_windows=20,
        reference_sha256=sha256_file(reference_path),
        comparator_config_sha256=sha256_file(output / "effective_config.json"),
    )
    reference_sensitivity_path = write_new_json(
        output / "reference_time_sensitivity.json", reference_artifact
    )
    source_rows = [
        {"window_index": index, "hr_bpm": 80.0, "br_bpm": 15.0}
        for index in range(20)
    ]
    materialized = materialize_radar_nan_windows(source_rows, validity)
    write_new_json(output / "materialized_radar_rows.json", {"rows": materialized})
    authorization = authorize_scoring(
        registry_path, "T001", ReferenceOperation.VALIDATION_SCORING
    )
    agreement = build_agreement_sensitivity(
        reference_artifact,
        {shift: materialized for shift in TIME_SHIFTS_S},
        authorization=authorization,
        scorer_config_sha256=sha256_file(output / "effective_config.json"),
    )
    write_new_json(output / "agreement_time_sensitivity.json", agreement)
    report = validate_preflight(
        manifest_path,
        root=output,
        reference_sensitivity_path=reference_sensitivity_path,
    )
    if report["invalid_window_indices"] != [0, 1]:
        raise ContractError("synthetic dry run did not identify exactly invalid windows 0 and 1")
    write_new_json(output / "preflight_report.json", report)
    return report
