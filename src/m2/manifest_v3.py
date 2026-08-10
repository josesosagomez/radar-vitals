"""Prospective three-arm session manifest, schema version 3.

Version 3 is a new contract.  It does not import v2 enums or reinterpret historical rows.
All scoring-mode file identities are verified while loading, before a session can be handed
to a scorer.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .acquisition_metadata import (
    Arm,
    DataRole,
    FRAME_RATE_HZ,
    INTENDED_DURATION_S,
    validate_acquisition_metadata,
    validate_finalization_metadata,
    validate_recovery_runtime_timing,
)
from .common import (
    ContractError,
    read_json_object,
    require_bool,
    require_int,
    require_nonempty_string,
    require_number,
    require_sha256,
    resolve_bound_path,
    sha256_file,
)
from .validity import load_frame_validity

MANIFEST_SCHEMA_VERSION = 3
FRAME0_EVENT_SOURCE = "frame_index_0_start_assignment_pc_utc"
PHYSICAL_BYTES_PER_FRAME = 32 * 4 * 256 * 4  # chirps * RX * ADC samples * complex-int16 bytes
SCORING_ROLES = frozenset(
    {DataRole.REPRESENTATION_VALIDATION, DataRole.FINAL_EVALUATION}
)


class ManifestError(ContractError):
    """A prospective manifest is malformed or its bound artifacts do not agree."""


class Mode(str, Enum):
    SCORING = "SCORING"
    DEVELOPMENT = "DEVELOPMENT"


class SessionDisposition(str, Enum):
    ADMITTED = "admitted"
    NO_AGREEMENT = "no_agreement"
    EXCLUDED = "excluded"


@dataclass(frozen=True)
class ProspectiveSession:
    mode: Mode
    session_id: str
    subject_id: str
    cohort_slot: int
    data_role: DataRole
    arm: Arm
    visit_number: int
    disposition: SessionDisposition
    acquisition_metadata: Mapping[str, object]
    frame0_epoch: float
    frame0_event_source: str
    clock_offset_start_s: float
    clock_offset_end_s: float
    intended_duration_s: float
    actual_duration_s: float
    raw_path: str
    raw_sha256: str
    frame_validity_map_path: str
    frame_validity_map_sha256: str
    n_frames: int
    n_invalid_frames: int
    packets_received: int
    packets_dropped: int
    packet_loss_ratio: float | None
    packet_loss_above_5_percent: bool
    reference_acquired: bool
    capture_git_commit: str
    capture_git_dirty: bool
    registry_sha256: str
    flags: tuple[str, ...] = ()
    raw: Mapping[str, object] = field(default_factory=dict, repr=False, compare=False)

    @property
    def is_scorable(self) -> bool:
        return (
            self.mode is Mode.SCORING
            and self.data_role in SCORING_ROLES
            and self.disposition is SessionDisposition.ADMITTED
            and self.reference_acquired
        )


_BOUND_ARTIFACTS = (
    ("raw_path", "raw_sha256"),
    ("source_config_yaml_path", "source_config_yaml_sha256"),
    ("effective_config_path", "effective_config_sha256"),
    ("capture_metadata_path", "capture_metadata_sha256"),
    ("frame_validity_map_path", "frame_validity_map_sha256"),
    ("radar_receipt_path", "radar_receipt_sha256"),
    ("reference_acquisition_path", "reference_acquisition_sha256"),
    ("cohort_registry_path", "registry_sha256"),
)


def _as_mode(mode: Mode | str) -> Mode:
    try:
        return mode if isinstance(mode, Mode) else Mode(mode)
    except ValueError:
        raise ManifestError(f"mode must be SCORING or DEVELOPMENT, got {mode!r}") from None


def _as_enum(enum_type: type[Enum], value: object, key: str) -> Enum:
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        allowed = ", ".join(member.value for member in enum_type)
        raise ManifestError(f"{key} must be one of [{allowed}], got {value!r}") from None


def _verify_hash_binding(fields: Mapping[str, object], root: Path, path_key: str, hash_key: str) -> Path:
    relative_path = require_nonempty_string(fields, path_key)
    expected_hash = require_sha256(fields.get(hash_key), hash_key)
    path = resolve_bound_path(root, relative_path, path_key)
    if not path.is_file():
        raise ManifestError(f"{path_key} does not exist: {path}")
    actual_hash = sha256_file(path)
    if actual_hash != expected_hash:
        raise ManifestError(
            f"{hash_key} mismatch for {relative_path}: expected {expected_hash}, got {actual_hash}"
        )
    return path


def _validate_reference_record(
    fields: Mapping[str, object], root: Path, acquisition_metadata: Mapping[str, object]
) -> bool:
    acquisition_path = resolve_bound_path(
        root, fields["reference_acquisition_path"], "reference_acquisition_path"
    )
    record = read_json_object(acquisition_path)
    if record.get("schema") != "m2_reference_acquisition_v1":
        raise ManifestError("reference acquisition record has the wrong schema")
    acquired = require_bool(record, "reference_acquired")
    if acquired != require_bool(fields, "reference_acquired"):
        raise ManifestError("reference_acquired disagrees with its bound acquisition record")
    if record.get("expected_reference_basename") != acquisition_metadata.get(
        "expected_reference_basename"
    ):
        raise ManifestError("reference acquisition record binds a different expected basename")

    has_path = fields.get("reference_path") is not None
    has_hash = fields.get("reference_sha256") is not None
    if has_path != has_hash:
        raise ManifestError("reference path/hash is only half bound")
    if acquired:
        if not has_path:
            raise ManifestError("acquired reference is not bound by path and SHA-256")
        reference_path = _verify_hash_binding(fields, root, "reference_path", "reference_sha256")
        if reference_path.name != acquisition_metadata["expected_reference_basename"]:
            raise ManifestError("reference filename differs from expected_reference_basename")
    elif has_path:
        raise ManifestError("unacquired reference must not carry a file binding")
    return acquired


def parse_session_v3(
    fields: Mapping[str, object], mode: Mode | str, *, root: str | Path | None = None
) -> ProspectiveSession:
    """Validate one v3 captured-session record and, in scoring mode, every file binding."""
    parsed_mode = _as_mode(mode)
    if type(fields) is not dict:
        raise ManifestError("each v3 session must be a JSON object")
    session_id = require_nonempty_string(fields, "session_id")
    metadata_raw = fields.get("acquisition_metadata")
    if type(metadata_raw) is not dict:
        raise ManifestError(f"session {session_id!r}: acquisition_metadata must be an object")
    try:
        metadata = validate_acquisition_metadata(metadata_raw)
    except ContractError as exc:
        raise ManifestError(f"session {session_id!r}: {exc}") from exc
    if (
        metadata["arm"] == Arm.RECOVERY.value
        and metadata.get("stopping_event_category") != "target_reached"
    ):
        raise ManifestError(
            f"session {session_id!r}: acquired recovery requires "
            "stopping_event_category=target_reached"
        )

    subject_id = require_nonempty_string(fields, "subject_id")
    cohort_slot = require_int(fields, "cohort_slot", minimum=1)
    role = _as_enum(DataRole, fields.get("data_role"), "data_role")
    arm = _as_enum(Arm, fields.get("arm"), "arm")
    visit_number = require_int(fields, "visit_number", minimum=1)
    for key, value in (
        ("session_id", session_id),
        ("subject_id", subject_id),
        ("cohort_slot", cohort_slot),
        ("data_role", role.value),
        ("arm", arm.value),
        ("visit_number", visit_number),
    ):
        if metadata.get(key) != value:
            raise ManifestError(f"session {session_id!r}: top-level {key} disagrees with sidecar")
    if parsed_mode is Mode.SCORING and role not in SCORING_ROLES:
        raise ManifestError(
            f"session {session_id!r}: data_role={role.value!r} is never scoring-loadable"
        )

    frame0_epoch = require_number(fields, "frame0_epoch")
    if fields.get("frame0_event_source") != FRAME0_EVENT_SOURCE:
        raise ManifestError(
            f"session {session_id!r}: frame0_event_source must be {FRAME0_EVENT_SOURCE!r}"
        )
    try:
        recovery_timing = validate_recovery_runtime_timing(
            metadata,
            fields,
            frame0_epoch_utc=frame0_epoch,
        )
    except ContractError as exc:
        raise ManifestError(f"session {session_id!r}: {exc}") from exc
    start_offset = require_number(fields, "clock_offset_start_s")
    end_offset = require_number(fields, "clock_offset_end_s")
    if abs(start_offset) > 1.0 or abs(end_offset) > 1.0:
        raise ManifestError("both PC-phone clock offsets must satisfy abs(offset) <= 1.0 s")
    if start_offset != float(metadata["clock_offset_start_s"]):
        raise ManifestError("clock_offset_start_s disagrees with acquisition metadata")

    intended_duration = require_number(fields, "intended_duration_s")
    actual_duration = require_number(fields, "actual_duration_s")
    if intended_duration != INTENDED_DURATION_S or actual_duration != INTENDED_DURATION_S:
        raise ManifestError("prospective intended and actual duration must both equal 600.0 s")
    finalization_raw = fields.get("finalization_metadata")
    if type(finalization_raw) is not dict:
        raise ManifestError("finalization_metadata must be an object")
    try:
        finalization = validate_finalization_metadata(
            finalization_raw,
            arm=arm,
            pre_exertion_resting_pr_bpm=(
                float(metadata["pre_exertion_resting_pr_bpm"])
                if arm is Arm.RECOVERY
                else None
            ),
        )
    except ContractError as exc:
        raise ManifestError(f"session {session_id!r}: {exc}") from exc
    if end_offset != float(finalization["clock_offset_end_s"]):
        raise ManifestError("clock_offset_end_s disagrees with finalization metadata")

    commit = require_nonempty_string(fields, "capture_git_commit")
    dirty = require_bool(fields, "capture_git_dirty")
    if dirty or commit == "unknown":
        raise ManifestError("prospective capture requires a known clean capture commit")
    if not require_bool(fields, "prospective_contract_enforced"):
        raise ManifestError("prospective_contract_enforced must be true")
    invocation = fields.get("exact_cli_invocation")
    if type(invocation) is not list or not invocation or any(type(item) is not str for item in invocation):
        raise ManifestError("exact_cli_invocation must be a non-empty array of argument strings")

    packets_received = require_int(fields, "packets_received", minimum=0)
    packets_dropped = require_int(fields, "packets_dropped", minimum=0)
    require_int(fields, "packets_short_discarded", minimum=0)
    require_int(fields, "packets_duplicate_or_late_discarded", minimum=0)
    if packets_received == 0:
        raise ManifestError("packets_received=0 has no exact usable radar stream")
    expected_loss_ratio = packets_dropped / packets_received
    declared_loss_ratio = require_number(fields, "packet_loss_ratio", minimum=0.0)
    if not math.isclose(declared_loss_ratio, expected_loss_ratio, rel_tol=0.0, abs_tol=1e-15):
        raise ManifestError("packet_loss_ratio disagrees with dropped/received counters")
    expected_loss_flag = 20 * packets_dropped > packets_received
    if require_bool(fields, "packet_loss_above_5_percent") != expected_loss_flag:
        raise ManifestError("packet_loss_above_5_percent must use 20*dropped > received")

    n_frames = require_int(fields, "n_frames", minimum=0)
    n_invalid = require_int(fields, "n_invalid_frames", minimum=0)
    trailing_bytes = require_int(fields, "trailing_partial_frame_bytes", minimum=0)
    bytes_per_frame = require_int(fields, "bytes_per_frame", minimum=1)
    if not metadata.get("synthetic_fixture", False) and bytes_per_frame != PHYSICAL_BYTES_PER_FRAME:
        raise ManifestError(
            f"physical prospective captures require bytes_per_frame={PHYSICAL_BYTES_PER_FRAME}"
        )
    if trailing_bytes >= bytes_per_frame:
        raise ManifestError("trailing_partial_frame_bytes must be smaller than one frame")
    expected_frames = int(INTENDED_DURATION_S * FRAME_RATE_HZ)
    if n_frames != expected_frames:
        raise ManifestError(
            f"600 s at 20 Hz must store exactly {expected_frames} complete frames, got {n_frames}"
        )
    if n_invalid > n_frames:
        raise ManifestError("n_invalid_frames cannot exceed n_frames")
    logical_stream_bytes = (packets_received + packets_dropped) * 1456
    represented_stream_bytes = n_frames * bytes_per_frame + trailing_bytes
    if logical_stream_bytes != represented_stream_bytes:
        raise ManifestError(
            "packet byte conservation failed: accepted+dropped packet intervals "
            "disagree with complete-frame and trailing bytes"
        )

    disposition = _as_enum(SessionDisposition, fields.get("disposition"), "disposition")
    reference_acquired = require_bool(fields, "reference_acquired")
    expected_disposition = (
        SessionDisposition.ADMITTED if reference_acquired else SessionDisposition.NO_AGREEMENT
    )
    if disposition is not expected_disposition:
        raise ManifestError(
            f"disposition must be {expected_disposition.value!r} for reference_acquired={reference_acquired}"
        )

    artifact_root = Path(root).resolve() if root is not None else None
    if parsed_mode is Mode.SCORING:
        if artifact_root is None:
            raise ManifestError("scoring-mode v3 parsing requires an artifact root")
        bound_paths: dict[str, Path] = {}
        for path_key, hash_key in _BOUND_ARTIFACTS:
            bound_paths[path_key] = _verify_hash_binding(fields, artifact_root, path_key, hash_key)
        if arm in {Arm.NATURAL, Arm.PACED}:
            _verify_hash_binding(
                fields, artifact_root, "settle_evidence_path", "settle_evidence_sha256"
            )
        if bound_paths["raw_path"].stat().st_size != n_frames * bytes_per_frame:
            raise ManifestError("raw byte size disagrees with n_frames * bytes_per_frame")

        # The receipt is the sealed acquisition boundary.  Its hash alone is not
        # sufficient: a coordinated edit could rehash a receipt after deleting a
        # semantic field.  Revalidate its complete artifact chain, then require every
        # fact duplicated by the scoring manifest to be present and identical.
        from .capture_artifacts import validate_sealed_radar_receipt

        try:
            receipt = validate_sealed_radar_receipt(bound_paths["radar_receipt_path"])
        except ContractError as exc:
            raise ManifestError(f"sealed radar receipt validation failed: {exc}") from exc
        if receipt.get("schema") != "m2_sealed_radar_receipt_v1":
            raise ManifestError("sealed receipt has the wrong schema")
        if receipt.get("receipt_state") != "sealed_radar_only":
            raise ManifestError("sealed receipt is not in the sealed_radar_only state")
        receipt_pairs = {
            "engineering_dry_run": bool(metadata.get("synthetic_fixture", False)),
            "session_id": session_id,
            "subject_id": subject_id,
            "cohort_slot": cohort_slot,
            "data_role": role.value,
            "arm": arm.value,
            "visit_number": visit_number,
            "frame0_epoch": frame0_epoch,
            "frame0_event_source": FRAME0_EVENT_SOURCE,
            "clock_offset_start_s": start_offset,
            "capture_git_commit": commit,
            "capture_git_dirty": dirty,
            "exact_cli_invocation": invocation,
            "prospective_contract_enforced": True,
            "packets_received": packets_received,
            "packets_dropped": packets_dropped,
            "packets_short_discarded": fields["packets_short_discarded"],
            "packets_duplicate_or_late_discarded": fields[
                "packets_duplicate_or_late_discarded"
            ],
            "packet_loss_ratio": fields["packet_loss_ratio"],
            "packet_loss_above_5_percent": fields["packet_loss_above_5_percent"],
            "n_frames": n_frames,
            "n_invalid_frames": n_invalid,
            "trailing_partial_frame_bytes": trailing_bytes,
            "bytes_per_frame": bytes_per_frame,
            **recovery_timing,
        }
        for key, expected in receipt_pairs.items():
            if receipt.get(key) != expected:
                raise ManifestError(f"sealed receipt semantic field {key} disagrees with manifest")
        if receipt.get("acquisition_metadata") != metadata:
            raise ManifestError("sealed receipt acquisition metadata disagrees with manifest")
        for path_key, hash_key in _BOUND_ARTIFACTS[:5]:
            if receipt.get(path_key) != fields[path_key]:
                raise ManifestError(f"sealed receipt {path_key} disagrees with manifest")
            if receipt.get(hash_key) != fields[hash_key]:
                raise ManifestError(f"sealed receipt {hash_key} disagrees with manifest")

        run_metadata = read_json_object(bound_paths["capture_metadata_path"])
        run_pairs = {
            "completion_status": "completed",
            "mode": "live",
            "prospective_study_mode": True,
            "session_id": session_id,
            "frame0_epoch_utc": frame0_epoch,
            "frame0_epoch_source": FRAME0_EVENT_SOURCE,
            "git_commit": commit,
            "git_dirty": dirty,
            "exact_cli_invocation": invocation,
            "acquisition_metadata": metadata,
            **recovery_timing,
        }
        for key, expected in run_pairs.items():
            if run_metadata.get(key) != expected:
                raise ManifestError(f"run metadata semantic field {key} disagrees with manifest")
        live_stats = run_metadata.get("live_packet_stats")
        if type(live_stats) is not dict:
            raise ManifestError("run metadata live_packet_stats must be an object")
        live_stat_pairs = {
            "packets_received": packets_received,
            "packets_dropped": packets_dropped,
            "packets_short_discarded": fields["packets_short_discarded"],
            "packets_duplicate_or_late_discarded": fields[
                "packets_duplicate_or_late_discarded"
            ],
            "n_frames": n_frames,
            "n_invalid_frames": n_invalid,
            "trailing_partial_frame_bytes": trailing_bytes,
            "bytes_per_frame": bytes_per_frame,
            "frame_validity_map_path": fields["frame_validity_map_path"],
            "frame_validity_map_sha256": fields["frame_validity_map_sha256"],
        }
        for key, expected in live_stat_pairs.items():
            if live_stats.get(key) != expected:
                raise ManifestError(f"run metadata packet field {key} disagrees with manifest")

        effective_config = read_json_object(bound_paths["effective_config_path"])
        effective_rate = effective_config.get("frame_rate_hz")
        if effective_rate is None and isinstance(effective_config.get("session"), Mapping):
            effective_rate = effective_config["session"].get("frame_rate_hz")
        if effective_rate is not None and float(effective_rate) != FRAME_RATE_HZ:
            raise ManifestError("effective config frame_rate_hz disagrees with prospective metadata")
        profile_config = effective_config.get("profile")
        if isinstance(profile_config, Mapping):
            configured_chirps = profile_config.get("num_chirps_per_frame")
            if configured_chirps is not None and configured_chirps != metadata[
                "configured_chirps_per_frame"
            ]:
                raise ManifestError(
                    "effective config chirps per frame disagrees with acquisition metadata"
                )
        validity = load_frame_validity(
            bound_paths["frame_validity_map_path"], expected_frames=n_frames
        )
        actual_invalid = int(np.count_nonzero(~validity))
        if actual_invalid != n_invalid:
            raise ManifestError(
                f"validity map marks {actual_invalid} invalid frames, manifest says {n_invalid}"
            )
        reference_acquired = _validate_reference_record(fields, artifact_root, metadata)
        reference_record = read_json_object(bound_paths["reference_acquisition_path"])
        if reference_record.get("session_id") != session_id:
            raise ManifestError("reference acquisition session_id disagrees with manifest")
        for key, value in finalization.items():
            if reference_record.get(key) != value:
                raise ManifestError(
                    f"reference acquisition semantic field {key} disagrees with finalization"
                )

        from .cohort_registry import load_registry, validate_membership

        try:
            validate_membership(
                bound_paths["cohort_registry_path"],
                subject_id=subject_id,
                cohort_slot=cohort_slot,
                data_role=role,
                session_id=session_id,
                arm=arm,
            )
            registry = load_registry(bound_paths["cohort_registry_path"])
        except ContractError as exc:
            raise ManifestError(f"cohort registry validation failed: {exc}") from exc
        registry_subject = next(
            entry for entry in registry["subjects"] if entry["subject_id"] == subject_id
        )
        registry_session = next(
            entry
            for entry in registry_subject["sessions"]
            if entry["session_id"] == session_id
        )
        if registry_session.get("state") != "captured" or registry_session.get(
            "radar_receipt_sha256"
        ) != fields["radar_receipt_sha256"]:
            raise ManifestError(
                "captured registry slot does not bind the exact sealed radar receipt"
            )
        if arm is Arm.PACED and metadata.get("commanded_rate_bpm") != registry_subject.get(
            "paced_rate_bpm"
        ):
            raise ManifestError("paced rate disagrees with fixed cohort registry assignment")

    return ProspectiveSession(
        mode=parsed_mode,
        session_id=session_id,
        subject_id=subject_id,
        cohort_slot=cohort_slot,
        data_role=role,
        arm=arm,
        visit_number=visit_number,
        disposition=disposition,
        acquisition_metadata=metadata,
        frame0_epoch=frame0_epoch,
        frame0_event_source=FRAME0_EVENT_SOURCE,
        clock_offset_start_s=start_offset,
        clock_offset_end_s=end_offset,
        intended_duration_s=intended_duration,
        actual_duration_s=actual_duration,
        raw_path=require_nonempty_string(fields, "raw_path"),
        raw_sha256=require_sha256(fields.get("raw_sha256"), "raw_sha256"),
        frame_validity_map_path=require_nonempty_string(fields, "frame_validity_map_path"),
        frame_validity_map_sha256=require_sha256(
            fields.get("frame_validity_map_sha256"), "frame_validity_map_sha256"
        ),
        n_frames=n_frames,
        n_invalid_frames=n_invalid,
        packets_received=packets_received,
        packets_dropped=packets_dropped,
        packet_loss_ratio=expected_loss_ratio,
        packet_loss_above_5_percent=expected_loss_flag,
        reference_acquired=reference_acquired,
        capture_git_commit=commit,
        capture_git_dirty=dirty,
        registry_sha256=require_sha256(fields.get("registry_sha256"), "registry_sha256"),
        flags=("packet_loss_above_5_percent",) if expected_loss_flag else (),
        raw=dict(fields),
    )


def load_manifest_v3(
    path: str | Path, mode: Mode | str, *, root: str | Path | None = None
) -> list[ProspectiveSession]:
    manifest_path = Path(path)
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read v3 manifest {manifest_path}: {exc}") from exc
    if type(document) is not dict:
        raise ManifestError("v3 manifest must be a JSON object")
    if document.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ManifestError("this reader accepts only manifest_schema_version=3")
    sessions = document.get("sessions")
    if type(sessions) is not list:
        raise ManifestError("v3 manifest sessions must be an array")
    artifact_root = Path(root).resolve() if root is not None else manifest_path.parent.resolve()
    parsed = [parse_session_v3(row, mode, root=artifact_root) for row in sessions]
    session_ids = [session.session_id for session in parsed]
    if len(session_ids) != len(set(session_ids)):
        raise ManifestError("v3 manifest contains duplicate session_id values")
    return parsed


def require_scoring_mode(sessions: list[ProspectiveSession], what: str) -> None:
    offenders = [session.session_id for session in sessions if not session.is_scorable]
    if offenders:
        raise ManifestError(f"{what} cannot score ineligible v3 sessions: {offenders}")
