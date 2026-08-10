"""Independent builders for the prospective M2 contract tests."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from src.m2.cohort_registry import role_membership_digest
from src.m2.common import canonical_json_bytes, sha256_bytes


SHA_A = "a" * 64
SHA_B = "b" * 64


def acquisition_metadata(
    arm: str = "recovery",
    *,
    subject_id: str = "T001",
    cohort_slot: int = 1,
    data_role: str = "representation_validation",
    synthetic_fixture: bool = True,
) -> dict[str, Any]:
    visit = {"natural": 1, "paced": 2, "recovery": 3}[arm]
    metadata: dict[str, Any] = {
        "subject_id": subject_id,
        "cohort_slot": cohort_slot,
        "data_role": data_role,
        "session_id": f"{subject_id}_{arm}",
        "arm": arm,
        "visit_number": visit,
        "visit_utc": "2030-01-02T03:04:05Z",
        "expected_reference_basename": f"{subject_id}_{arm}_reference.csv",
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
        "scene_non_health_notes": "Synthetic clear-room description only.",
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
        "clock_offset_start_s": 0.0,
        "intended_duration_s": 600.0,
        "session_order": visit,
        "disturbances_category": "none",
    }
    if synthetic_fixture:
        metadata["synthetic_fixture"] = True
    if arm in {"natural", "paced"}:
        metadata.update(
            {
                "settle_duration_s": 120.0,
                "start_pr_bpm": 70.0,
                "settle_evidence_window_s": 60.0,
                "settle_pr_spread_bpm": 5.0,
                "settle_pr_drift_bpm": 3.0,
                "settle_evidence_path": "settle.json",
                "settle_evidence_sha256": SHA_A,
            }
        )
    if arm == "paced":
        metadata.update(
            {
                "commanded_rate_bpm": 12,
                "metronome_rate_bpm": 24,
                "paced_settle_duration_s": 120.0,
                "br_stability_duration_s": 60.0,
                "br_stability_confirmed": True,
                "resting_pr_bpm": 70.0,
                "harmonic_collision_margin_bpm": 22.0,
            }
        )
    if arm == "recovery":
        metadata.update(
            {
                "screening_completed": True,
                "recovery_clearance_attested": True,
                "amended_materials_current_consent_confirmed": True,
                "researcher_present": True,
                "exertion_modality": "self_paced_step_ups",
                "suitable_footwear_confirmed": True,
                "dry_unobstructed_area_confirmed": True,
                "pre_exertion_resting_pr_bpm": 70.0,
                "exertion_stop_pr_bpm": 100.0,
                "exertion_duration_s": 120.0,
                "seated_pr_t0_bpm": 95.0,
                "seated_at_record_start": True,
                "still_at_record_start": True,
                "hands_resting_at_record_start": True,
                "no_pacing_confirmed": True,
                "stopping_event_category": "target_reached",
            }
        )
    return metadata


def finalization_metadata(
    arm: str = "recovery", *, reference_acquired: bool = True
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "clock_offset_end_s": 0.0,
        "actual_duration_s": 600.0,
        "final_pr_bpm": 70.0,
        "reference_acquired": reference_acquired,
    }
    if reference_acquired:
        result.update(
            {
                "reference_basename": f"T001_{arm}_reference.csv",
                "reference_export_utc": "2030-01-02T03:14:06Z",
            }
        )
    else:
        result["reference_missing_reason"] = "file_missing"
    if arm == "recovery":
        result.update(
            {"post_monitoring_pr_bpm": 75.0, "monitoring_duration_s": 60.0}
        )
    return result


def retry_pair(reason: str = "protocol_abort") -> tuple[dict[str, Any], dict[str, Any]]:
    identity = {
        "subject_id": "P001",
        "data_role": "representation_validation",
        "cohort_slot": 1,
        "arm": "natural",
        "commanded_rate_bpm": None,
        "protocol_identity_sha256": SHA_A,
        "capture_config_sha256": SHA_B,
        "label_state": "sealed",
        "retry_reason": reason,
    }
    predecessor = {
        **identity,
        "session_id": "P001_natural_attempt1",
        "replaced_by_session_id": "P001_natural_attempt2",
    }
    replacement = {
        **identity,
        "session_id": "P001_natural_attempt2",
        "replaces_session_id": "P001_natural_attempt1",
    }
    evidence_by_reason = {
        "protocol_abort": {
            "settle_gate_failed_before_capture": True,
            "intentional_early_stop": False,
        },
        "corrupt_raw": {
            "stored_raw_checksum_failed": True,
            "raw_truncation_cut_nonfinal_window": False,
        },
        "epoch_sync_failure": {
            "clock_offset_start_s": 1.000001,
            "clock_offset_end_s": 0.0,
        },
    }
    predecessor["retry_evidence"] = evidence_by_reason.get(reason, {})
    return predecessor, replacement


def synthetic_registry(state: str = "sealed", *, subject_id: str = "T001") -> dict[str, Any]:
    ids = [subject_id]
    return {
        "schema": "m2_cohort_registry_v1",
        "registry_kind": "synthetic_fixture",
        "revision": 1,
        "previous_registry_sha256": None,
        "role_membership_sha256": {
            "representation_validation": role_membership_digest(ids)
        },
        "subjects": [
            {
                "subject_id": subject_id,
                "cohort_slot": 1,
                "data_role": "representation_validation",
                "paced_rate_bpm": 12,
                "label_state": state,
                "sessions": [
                    {
                        "session_id": f"{subject_id}_natural",
                        "arm": "natural",
                        "visit_number": 1,
                        "state": "planned",
                    },
                    {
                        "session_id": f"{subject_id}_paced",
                        "arm": "paced",
                        "visit_number": 2,
                        "state": "planned",
                    },
                    {
                        "session_id": f"{subject_id}_recovery",
                        "arm": "recovery",
                        "visit_number": 3,
                        "state": "planned",
                    },
                ],
            }
        ],
    }


def write_registry(path: Path, document: dict[str, Any]) -> Path:
    content = canonical_json_bytes(document)
    path.write_bytes(content)
    path.with_suffix(path.suffix + ".sha256").write_text(
        sha256_bytes(content) + "\n", encoding="ascii"
    )
    return path


def canonical_clone(value: Any) -> Any:
    return copy.deepcopy(value)


def byte_digest(content: bytes) -> str:
    return sha256_bytes(content)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
