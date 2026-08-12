"""Validation for the prospective acquisition and finalization sidecars.

The sidecars contain operational measurements and positive attestations only.  They are not
medical records: recovery screening answers, diagnoses, names, consent documents, and health
free text are rejected before any capture artifact is created.
"""
from __future__ import annotations

import re
import math
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

import yaml

from .common import (
    ContractError,
    require_bool,
    require_int,
    require_nonempty_string,
    require_number,
    require_sha256,
)


class Arm(str, Enum):
    NATURAL = "natural"
    PACED = "paced"
    RECOVERY = "recovery"


class DataRole(str, Enum):
    DEVELOPMENT = "development"
    ENGINEERING_ONLY = "engineering_only"
    REPRESENTATION_VALIDATION = "representation_validation"
    FINAL_EVALUATION = "final_evaluation"


VISIT_BY_ARM = {Arm.NATURAL: 1, Arm.PACED: 2, Arm.RECOVERY: 3}
PACED_RATES_BPM = (12, 15, 18)
FRAME_RATE_HZ = 20.0
CHIRPS_PER_FRAME = 32
INTENDED_DURATION_S = 600.0
MAX_CLOCK_OFFSET_S = 1.0
RECOVERY_SEATED_START_SOURCE = "operator_observed_seated_start_synchronized_pc_utc"

# Acquisition-time admission values, named so operator tooling imports them instead of re-typing
# the numbers into prompts and checklists (CLAUDE.md section 2, no magic numbers).  This module
# remains the sole authority on whether a sidecar is admissible: importing a threshold is not
# permission to re-implement the check.
#
# SCOPE, and what these names are NOT.  They cover acquisition-sidecar admission only, and they
# are not the only definition of these numbers in the project.  src/m4/manifest.py:142-143 and
# :174-175 independently define DISTANCE_MIN_M/DISTANCE_MAX_M and
# SETTLE_MAX_PR_SPREAD_BPM/SETTLE_MAX_PR_DRIFT_BPM for the offline scoring admissibility gate, and
# MAX_CLOCK_OFFSET_S and PACED_RATES_BPM are duplicated there too.  A protocol change must be
# applied in both modules or acquisition and scoring will silently disagree;
# tests/test_m2_acquisition_metadata.py pins them equal so a one-sided edit fails.  Beware the
# near-transposed spellings: M4's SETTLE_MAX_PR_SPREAD_BPM is this module's
# SETTLE_SPREAD_MAX_BPM.  validate_finalization_metadata is deliberately not covered here - the
# scaffold tool emits only the acquisition sidecar, so it never prints those values.

#: notes/protocol.md:70 and :177 - chest 0.8-1.4 m from the radar, inclusive at both ends.
#: Duplicated at src/m4/manifest.py:142-143.
DISTANCE_MIN_M = 0.8
DISTANCE_MAX_M = 1.4

#: notes/protocol.md:201 SETTLE CRITERION - PR spread measured over a **continuous 60 s** window.
#: Checked below by exact equality, so it is a real constant here.  Contrast
#: src/m4/manifest.py:170-173, which deliberately omits this window because nothing in that
#: module can verify it; M4 receives spread/drift already reduced over the window.
SETTLE_EVIDENCE_WINDOW_S = 60.0

#: notes/protocol.md SETTLE CRITERION limb 3: total settle >= 120 s.  Owner decision 2026-08-12
#: based on operator judgement, NOT derived from measurement - see the protocol, which states this
#: explicitly so the paper cannot inherit it as an empirical settling time.  Its purpose is that the
#: SETTLE_EVIDENCE_WINDOW_S window sits inside a settled period rather than constituting all of it.
#:
#: Applies to natural and paced only.  Diagnostic captures are not bound by it (protocol limbs 1-2
#: cover diagnostic, limb 3 does not).  Recovery is exempt from settle evidence entirely - it starts
#: recording as the subject sits - and the validator rejects every settle field for that arm.  For
#: paced, the >= 120 s of pacing counts toward this settle; the two are not sequential.
#:
#: Was 60.0 until 2026-08-12, derived from the window rather than protocol-sourced since no minimum
#: was specified anywhere.  That floor admitted a capture whose entire settle *was* the evidence
#: window.  Raising it tightened admission; no session existed at the time, so nothing was
#: retro-rejected.
#:
#: MUST NOT be set below SETTLE_EVIDENCE_WINDOW_S: nothing checks
#: settle_duration_s >= settle_evidence_window_s (the two are validated independently below), so a
#: lower value would admit a sidecar declaring a 60 s continuous evidence window inside a shorter
#: settle - a criterion it cannot have demonstrated, sealed permanently into the cohort.  At 120.0
#: the cross-check is unreachable, so it is not pending work.
#:
#: There is deliberately NO MAX_SETTLE_S.  notes/protocol.md bounds settle above as well ("if the
#: criterion is not met within 5 minutes, abort and re-seat"), so an admissible settle is 120-300 s,
#: but only the lower bound is enforced here: settle_duration_s = 3600.0 is admitted and would seal a
#: documented protocol violation into the cohort.  Pre-existing asymmetry; adding the upper bound is
#: a behaviour change for its own commit.
MIN_SETTLE_S = 120.0

#: notes/protocol.md:201-202 SETTLE CRITERION, transcribed: PR spread <= 5 bpm over a continuous
#: 60 s, and last-20 s vs first-20 s drift <= 3 bpm.  Both limbs are <=, so 5.0 and 3.0 exactly
#: are PASSES - the comparisons below are >.  Duplicated at src/m4/manifest.py:174-175.
SETTLE_SPREAD_MAX_BPM = 5.0
SETTLE_DRIFT_MAX_BPM = 3.0

#: notes/m2_capture_runbook.md section 3 "Paced": pace at least 120 s before recording, and
#: confirm Masimo BR stability for at least 60 s.
MIN_PACED_SETTLE_S = 120.0
MIN_BR_STABILITY_S = 60.0

#: notes/m2_capture_runbook.md section 3 "Recovery": stop exertion on the live Masimo reading at
#: 100-120 bpm, inclusive at both ends.
EXERTION_STOP_PR_MIN_BPM = 100.0
EXERTION_STOP_PR_MAX_BPM = 120.0

#: Privacy limit on the sidecar's only free-text field, so a scene note cannot grow into a
#: narrative.  See _FORBIDDEN_SCENE_NOTE_TERMS for the content rule.
MAX_SCENE_NOTES_CHARS = 500

#: Paced metronome is one beat per inhale and one per exhale, i.e. twice the commanded breathing
#: rate (notes/m2_capture_runbook.md section 3 "Paced").  RESPIRATION_HARMONIC_ORDER is the
#: respiration harmonic whose collision with the cardiac band the margin measures.  Named so
#: operator tooling deriving metronome_rate_bpm and computing harmonic_collision_margin_bpm
#: reproduces these formulas instead of re-typing them: a re-typed formula fails later, and less
#: obviously, than a re-typed threshold.
METRONOME_BEATS_PER_BREATH = 2
RESPIRATION_HARMONIC_ORDER = 4.0
HARMONIC_MARGIN_TOLERANCE_BPM = 1e-12

# Controlled vocabularies.  Operator tooling prompts from these in order; the validator admits
# only these values.  Tuples rather than sets: iteration order of a set of str is
# PYTHONHASHSEED-dependent, and prompt order must be reproducible (CLAUDE.md section 3).
SCENE_DESCRIPTION_CATEGORIES = ("clear_dominant_subject", "controlled_known_reflectors")
DISTURBANCE_CATEGORIES = ("none", "minor_logged", "protocol_abort")
STOPPING_EVENT_CATEGORIES = ("target_reached", "participant_stop", "researcher_safety_stop")

_PROSPECTIVE_SUBJECT_RE = re.compile(r"^P\d{3}$")
_ENGINEERING_SUBJECT_RE = re.compile(r"^(?:T|SYN)[A-Z0-9_-]{1,30}$")
_SESSION_RE = re.compile(r"^[A-Z][A-Z0-9]{1,15}_[a-z0-9][a-z0-9_-]{1,63}$")
_REFERENCE_RE = re.compile(r"^[A-Z][A-Z0-9]{1,15}_[a-z0-9][a-z0-9_-]{1,63}\.csv$")

_COMMON_BOOLEAN_FIELDS = (
    "back_straight",
    "both_hands_on_thighs",
    "facing_radar",
    "radar_at_chest_height",
    "radar_face_horizontal",
    "phone_level_check_passed",
    "sensored_hand_still",
    "scene_changed",
    "quiet_room_confirmed",
    "no_walkers_confirmed",
    "no_fan_airflow_confirmed",
    "radar_ready",
    "dca1000_ready",
    "masimo_battery_ready",
    "masimo_logging_ready",
    "masimo_pr_ready",
    "masimo_pi_ready",
)
_TRUE_AT_CAPTURE_FIELDS = tuple(field for field in _COMMON_BOOLEAN_FIELDS if field != "scene_changed")

_FORBIDDEN_KEY_PARTS = (
    "parq",
    "diagnos",
    "condition",
    "medication",
    "pregnan",
    "participant_name",
    "consent_document",
    "consent_path",
    "symptom",
    "eligibility_reason",
    "screening_answer",
)

# This field may describe the capture environment, but it must never become an
# accidental health-history narrative.  Match stems/phrases so common inflections
# are rejected while useful notes such as furniture and reflector placement remain.
_FORBIDDEN_SCENE_NOTE_TERMS = (
    "cardiovascular",
    "chest pain",
    "diagnos",
    "dizz",
    "faint",
    "health condition",
    "illness",
    "medical",
    "medication",
    "nausea",
    "palpitation",
    "pregnan",
    "shortness of breath",
    "symptom",
)

_COMMON_ACQUISITION_FIELDS = frozenset(
    {
        "subject_id",
        "cohort_slot",
        "data_role",
        "session_id",
        "arm",
        "visit_number",
        "visit_utc",
        "expected_reference_basename",
        "posture",
        "distance_m",
        "scene_description_category",
        "scene_non_health_notes",
        "configured_chirps_per_frame",
        "configured_frame_rate_hz",
        "clock_offset_start_s",
        "intended_duration_s",
        "session_order",
        "disturbances_category",
        "synthetic_fixture",
        *_COMMON_BOOLEAN_FIELDS,
    }
)
_SETTLE_FIELDS = frozenset(
    {
        "settle_duration_s",
        "start_pr_bpm",
        "settle_evidence_window_s",
        "settle_pr_spread_bpm",
        "settle_pr_drift_bpm",
        "settle_evidence_path",
        "settle_evidence_sha256",
    }
)
_PACED_FIELDS = frozenset(
    {
        "commanded_rate_bpm",
        "metronome_rate_bpm",
        "paced_settle_duration_s",
        "br_stability_duration_s",
        "br_stability_confirmed",
        "resting_pr_bpm",
        "harmonic_collision_margin_bpm",
    }
)
_RECOVERY_FIELDS = frozenset(
    {
        "screening_completed",
        "recovery_clearance_attested",
        "amended_materials_current_consent_confirmed",
        "researcher_present",
        "exertion_modality",
        "suitable_footwear_confirmed",
        "dry_unobstructed_area_confirmed",
        "pre_exertion_resting_pr_bpm",
        "exertion_stop_pr_bpm",
        "exertion_duration_s",
        "seated_pr_t0_bpm",
        "seated_at_record_start",
        "still_at_record_start",
        "hands_resting_at_record_start",
        "no_pacing_confirmed",
        "stopping_event_category",
    }
)


def _reject_unknown_fields(
    metadata: Mapping[str, object], allowed_fields: frozenset[str], *, where: str
) -> None:
    unknown_fields = sorted(set(metadata) - allowed_fields)
    if unknown_fields:
        raise ContractError(
            f"{where} contains unknown fields forbidden by the privacy schema: "
            + ", ".join(unknown_fields)
        )


def _enum(enum_type: type[Enum], value: object, field_name: str) -> Enum:
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        allowed = ", ".join(member.value for member in enum_type)
        raise ContractError(f"{field_name} must be one of [{allowed}], got {value!r}") from None


def _require_true(mapping: Mapping[str, object], key: str) -> None:
    if not require_bool(mapping, key):
        raise ContractError(f"{key} must be true before prospective capture")


def _validate_utc(value: object, field_name: str) -> str:
    if type(value) is not str or not value.endswith("Z"):
        raise ContractError(f"{field_name} must be an ISO-8601 UTC string ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ContractError(f"{field_name} is not valid ISO-8601 UTC: {value!r}") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise ContractError(f"{field_name} must describe UTC")
    return value


def _reject_private_fields(value: object, path: str = "sidecar") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            lowered = str(key).lower()
            contains_private_health_key = "health" in lowered and "non_health" not in lowered
            if contains_private_health_key or any(part in lowered for part in _FORBIDDEN_KEY_PARTS):
                raise ContractError(
                    f"{path}.{key} is forbidden recovery/private health metadata"
                )
            _reject_private_fields(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_private_fields(nested, f"{path}[{index}]")


def load_sidecar(path: str | Path) -> dict[str, Any]:
    sidecar_path = Path(path)
    try:
        document = yaml.safe_load(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ContractError(f"cannot read acquisition sidecar {sidecar_path}: {exc}") from exc
    if type(document) is not dict:
        raise ContractError("acquisition sidecar must be a mapping")
    return validate_acquisition_metadata(document)


def validate_acquisition_metadata(metadata: Mapping[str, object]) -> dict[str, Any]:
    """Validate capture-time metadata and return a plain normalized dictionary."""
    if type(metadata) is not dict:
        raise ContractError("acquisition metadata must be a JSON/YAML object")
    _reject_private_fields(metadata)

    role = _enum(DataRole, metadata.get("data_role"), "data_role")
    arm = _enum(Arm, metadata.get("arm"), "arm")
    subject_id = require_nonempty_string(metadata, "subject_id")
    synthetic_fixture = metadata.get("synthetic_fixture", False)
    if type(synthetic_fixture) is not bool:
        raise ContractError("synthetic_fixture must be a Boolean when present")
    if synthetic_fixture:
        if role is not DataRole.REPRESENTATION_VALIDATION:
            raise ContractError("synthetic fixtures exercise only representation_validation")
        if not _ENGINEERING_SUBJECT_RE.fullmatch(subject_id):
            raise ContractError("synthetic fixture subject_id must use a T*/SYN* identifier")
        require_int(metadata, "cohort_slot", minimum=1)
    elif role in (DataRole.REPRESENTATION_VALIDATION, DataRole.FINAL_EVALUATION):
        if not _PROSPECTIVE_SUBJECT_RE.fullmatch(subject_id):
            raise ContractError("prospective subject_id must be pseudonymous P001-P015 form")
        slot = require_int(metadata, "cohort_slot", minimum=1)
        if slot > 15 or subject_id != f"P{slot:03d}":
            raise ContractError("subject_id and cohort_slot must identify the same P001-P015 slot")
    elif role is DataRole.ENGINEERING_ONLY:
        if not _ENGINEERING_SUBJECT_RE.fullmatch(subject_id):
            raise ContractError("engineering fixtures must use a T*/SYN* synthetic subject ID")
        require_int(metadata, "cohort_slot", minimum=1)
    else:
        require_int(metadata, "cohort_slot", minimum=1)

    session_id = require_nonempty_string(metadata, "session_id")
    if not _SESSION_RE.fullmatch(session_id) or not session_id.startswith(subject_id + "_"):
        raise ContractError("session_id must be a pseudonymous basename prefixed by subject_id")
    visit_number = require_int(metadata, "visit_number", minimum=1)
    if visit_number != VISIT_BY_ARM[arm]:
        raise ContractError(
            f"visit_number must be {VISIT_BY_ARM[arm]} for the fixed {arm.value} arm order"
        )
    _validate_utc(metadata.get("visit_utc"), "visit_utc")

    expected_reference_basename = require_nonempty_string(
        metadata, "expected_reference_basename"
    )
    if (
        Path(expected_reference_basename).name != expected_reference_basename
        or not _REFERENCE_RE.fullmatch(expected_reference_basename)
        or not expected_reference_basename.startswith(session_id + "_")
    ):
        raise ContractError(
            "expected_reference_basename must be a pseudonymous CSV basename prefixed by session_id"
        )

    if metadata.get("posture") != "seated":
        raise ContractError("posture must be exactly 'seated'")
    distance_m = require_number(metadata, "distance_m")
    if not (DISTANCE_MIN_M <= distance_m <= DISTANCE_MAX_M):
        raise ContractError(
            f"distance_m must be in [{DISTANCE_MIN_M}, {DISTANCE_MAX_M}] metres, inclusive"
        )
    for field_name in _COMMON_BOOLEAN_FIELDS:
        require_bool(metadata, field_name)
    for field_name in _TRUE_AT_CAPTURE_FIELDS:
        _require_true(metadata, field_name)

    scene_category = require_nonempty_string(metadata, "scene_description_category")
    if scene_category not in SCENE_DESCRIPTION_CATEGORIES:
        raise ContractError("scene_description_category is not a controlled category")
    scene_notes = require_nonempty_string(metadata, "scene_non_health_notes")
    if len(scene_notes) > MAX_SCENE_NOTES_CHARS:
        raise ContractError(
            f"scene_non_health_notes must be at most {MAX_SCENE_NOTES_CHARS} characters"
        )
    normalized_scene_notes = " ".join(scene_notes.lower().split())
    if any(term in normalized_scene_notes for term in _FORBIDDEN_SCENE_NOTE_TERMS):
        raise ContractError(
            "scene_non_health_notes violates privacy: health narrative terms are forbidden in free text"
        )
    disturbances = require_nonempty_string(metadata, "disturbances_category")
    if disturbances not in DISTURBANCE_CATEGORIES:
        raise ContractError("disturbances_category is not a controlled category")

    if require_int(metadata, "configured_chirps_per_frame") != CHIRPS_PER_FRAME:
        raise ContractError("configured_chirps_per_frame must be exactly 32")
    if require_number(metadata, "configured_frame_rate_hz") != FRAME_RATE_HZ:
        raise ContractError("configured_frame_rate_hz must be exactly 20 Hz")
    if require_number(metadata, "intended_duration_s") != INTENDED_DURATION_S:
        raise ContractError("intended_duration_s must be exactly 600.0 seconds")
    start_offset_s = require_number(metadata, "clock_offset_start_s")
    if abs(start_offset_s) > MAX_CLOCK_OFFSET_S:
        raise ContractError("clock_offset_start_s exceeds the inclusive +/-1 second gate")
    if require_int(metadata, "session_order") != visit_number:
        raise ContractError("session_order must equal the fixed visit_number")

    if arm in (Arm.NATURAL, Arm.PACED):
        require_number(metadata, "settle_duration_s", minimum=MIN_SETTLE_S)
        require_number(metadata, "start_pr_bpm", minimum=0.0)
        if require_number(metadata, "settle_evidence_window_s") != SETTLE_EVIDENCE_WINDOW_S:
            raise ContractError(
                f"settle_evidence_window_s must be exactly {SETTLE_EVIDENCE_WINDOW_S} seconds"
            )
        spread = require_number(metadata, "settle_pr_spread_bpm", minimum=0.0)
        drift = require_number(metadata, "settle_pr_drift_bpm", minimum=0.0)
        if spread > SETTLE_SPREAD_MAX_BPM or drift > SETTLE_DRIFT_MAX_BPM:
            raise ContractError(
                "natural/paced settle evidence does not pass "
                f"<={SETTLE_SPREAD_MAX_BPM:g}/<={SETTLE_DRIFT_MAX_BPM:g} bpm gates"
            )
        require_nonempty_string(metadata, "settle_evidence_path")
        require_sha256(metadata.get("settle_evidence_sha256"), "settle_evidence_sha256")
    else:
        forbidden_settle = {
            "settle_duration_s",
            "settle_evidence_window_s",
            "settle_pr_spread_bpm",
            "settle_pr_drift_bpm",
            "settle_evidence_path",
            "settle_evidence_sha256",
        }
        present = sorted(forbidden_settle.intersection(metadata))
        if present:
            raise ContractError(
                "recovery is exempt from stable settle evidence; remove " + ", ".join(present)
            )

    if arm is Arm.PACED:
        commanded_rate_bpm = require_int(metadata, "commanded_rate_bpm")
        if commanded_rate_bpm not in PACED_RATES_BPM:
            raise ContractError("commanded_rate_bpm must be one of 12/15/18")
        expected_metronome = METRONOME_BEATS_PER_BREATH * commanded_rate_bpm
        if require_int(metadata, "metronome_rate_bpm") != expected_metronome:
            raise ContractError("metronome_rate_bpm must be exactly twice commanded_rate_bpm")
        require_number(metadata, "paced_settle_duration_s", minimum=MIN_PACED_SETTLE_S)
        require_number(metadata, "br_stability_duration_s", minimum=MIN_BR_STABILITY_S)
        _require_true(metadata, "br_stability_confirmed")
        resting_pr_bpm = require_number(metadata, "resting_pr_bpm", minimum=0.0)
        expected_margin = abs(resting_pr_bpm - RESPIRATION_HARMONIC_ORDER * commanded_rate_bpm)
        supplied_margin = require_number(metadata, "harmonic_collision_margin_bpm", minimum=0.0)
        if abs(supplied_margin - expected_margin) > HARMONIC_MARGIN_TOLERANCE_BPM:
            raise ContractError(
                "harmonic_collision_margin_bpm must equal abs(resting_pr_bpm - 4*commanded_rate_bpm)"
            )
    elif "commanded_rate_bpm" in metadata or "metronome_rate_bpm" in metadata:
        raise ContractError("only the paced arm may contain commanded/metronome rates")

    if arm is Arm.RECOVERY:
        for field_name in (
            "screening_completed",
            "recovery_clearance_attested",
            "amended_materials_current_consent_confirmed",
            "researcher_present",
            "suitable_footwear_confirmed",
            "dry_unobstructed_area_confirmed",
            "seated_at_record_start",
            "still_at_record_start",
            "hands_resting_at_record_start",
            "no_pacing_confirmed",
        ):
            _require_true(metadata, field_name)
        if metadata.get("exertion_modality") != "self_paced_step_ups":
            raise ContractError("recovery exertion_modality must be self_paced_step_ups")
        require_number(metadata, "pre_exertion_resting_pr_bpm", minimum=0.0)
        stop_pr_bpm = require_number(metadata, "exertion_stop_pr_bpm")
        if not (EXERTION_STOP_PR_MIN_BPM <= stop_pr_bpm <= EXERTION_STOP_PR_MAX_BPM):
            raise ContractError(
                "exertion_stop_pr_bpm must be in "
                f"[{EXERTION_STOP_PR_MIN_BPM:g}, {EXERTION_STOP_PR_MAX_BPM:g}] bpm"
            )
        require_number(metadata, "exertion_duration_s", minimum=0.0)
        require_number(metadata, "seated_pr_t0_bpm", minimum=0.0)
        stopping_event = require_nonempty_string(metadata, "stopping_event_category")
        if stopping_event not in STOPPING_EVENT_CATEGORIES:
            raise ContractError("stopping_event_category is not a controlled category")

    allowed_fields = _COMMON_ACQUISITION_FIELDS
    if arm is Arm.NATURAL:
        allowed_fields |= _SETTLE_FIELDS
    elif arm is Arm.PACED:
        allowed_fields |= _SETTLE_FIELDS | _PACED_FIELDS
    else:
        allowed_fields |= _RECOVERY_FIELDS
    _reject_unknown_fields(metadata, allowed_fields, where=f"{arm.value} acquisition metadata")
    return dict(metadata)


def validate_finalization_metadata(
    metadata: Mapping[str, object], *, arm: Arm, pre_exertion_resting_pr_bpm: float | None = None
) -> dict[str, Any]:
    """Validate values measured only after radar capture/reference export."""
    if type(metadata) is not dict:
        raise ContractError("finalization metadata must be an object")
    _reject_private_fields(metadata, "finalization")
    end_offset_s = require_number(metadata, "clock_offset_end_s")
    if abs(end_offset_s) > MAX_CLOCK_OFFSET_S:
        raise ContractError("clock_offset_end_s exceeds the inclusive +/-1 second gate")
    if require_number(metadata, "actual_duration_s") != INTENDED_DURATION_S:
        raise ContractError("actual_duration_s must be exactly 600.0 seconds")
    require_number(metadata, "final_pr_bpm", minimum=0.0)
    acquired = require_bool(metadata, "reference_acquired")
    if acquired:
        basename = require_nonempty_string(metadata, "reference_basename")
        if Path(basename).name != basename or not _REFERENCE_RE.fullmatch(basename):
            raise ContractError("reference_basename must be a pseudonymous CSV basename")
        _validate_utc(metadata.get("reference_export_utc"), "reference_export_utc")
    else:
        reason = require_nonempty_string(metadata, "reference_missing_reason")
        if reason not in {"export_failed", "logging_failed", "file_missing"}:
            raise ContractError("reference_missing_reason is not a controlled category")
        if "reference_basename" in metadata:
            raise ContractError("an unacquired reference must not declare reference_basename")

    if arm is Arm.RECOVERY:
        post_pr = require_number(metadata, "post_monitoring_pr_bpm", minimum=0.0)
        require_number(metadata, "monitoring_duration_s", minimum=0.0)
        if pre_exertion_resting_pr_bpm is None:
            raise ContractError("recovery finalization needs pre_exertion_resting_pr_bpm")
        if abs(post_pr - pre_exertion_resting_pr_bpm) > 5.0:
            raise ContractError(
                "post_monitoring_pr_bpm is not within 5 bpm of pre-exertion resting PR"
            )
    elif "post_monitoring_pr_bpm" in metadata or "monitoring_duration_s" in metadata:
        raise ContractError("post-monitoring recovery fields are recovery-only")

    allowed_fields = {
        "clock_offset_end_s",
        "actual_duration_s",
        "final_pr_bpm",
        "reference_acquired",
    }
    if acquired:
        allowed_fields.update({"reference_basename", "reference_export_utc"})
    else:
        allowed_fields.add("reference_missing_reason")
    if arm is Arm.RECOVERY:
        allowed_fields.update({"post_monitoring_pr_bpm", "monitoring_duration_s"})
    _reject_unknown_fields(
        metadata, frozenset(allowed_fields), where="finalization metadata"
    )
    return dict(metadata)


def validate_recovery_runtime_timing(
    acquisition_metadata: Mapping[str, object],
    timing: Mapping[str, object],
    *,
    frame0_epoch_utc: float,
) -> dict[str, float | str]:
    """Validate the observed recovery seating event and frame-0-derived delay."""
    timing_keys = (
        "recovery_seated_start_utc",
        "recovery_seated_start_event_source",
        "sit_to_record_delay_s",
    )
    if acquisition_metadata.get("arm") != Arm.RECOVERY.value:
        if any(timing.get(key) is not None for key in timing_keys):
            raise ContractError("recovery start timing is valid only for the recovery arm")
        return {}
    seated_start_utc = require_number(timing, "recovery_seated_start_utc")
    if timing.get("recovery_seated_start_event_source") != RECOVERY_SEATED_START_SOURCE:
        raise ContractError(
            "recovery_seated_start_event_source is missing or invalid"
        )
    delay_s = require_number(timing, "sit_to_record_delay_s", minimum=0.0)
    if not math.isfinite(frame0_epoch_utc):
        raise ContractError("recovery runtime timing requires a finite frame-0 epoch")
    expected_delay_s = float(frame0_epoch_utc) - seated_start_utc
    if expected_delay_s < 0.0:
        raise ContractError("recovery seated-start UTC cannot be after frame 0")
    if not math.isclose(delay_s, expected_delay_s, rel_tol=0.0, abs_tol=1e-9):
        raise ContractError("sit_to_record_delay_s disagrees with frame0 minus seated-start UTC")
    return {
        "recovery_seated_start_utc": seated_start_utc,
        "recovery_seated_start_event_source": RECOVERY_SEATED_START_SOURCE,
        "sit_to_record_delay_s": delay_s,
    }
