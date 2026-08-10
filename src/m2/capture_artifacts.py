"""Two-phase prospective capture artifacts: sealed radar receipt, then finalization.

The live producer never claims scoring completion.  It seals radar/config/metadata/map
identities first.  Only the separate finalizer may bind the reference-acquisition record,
copy immutable inputs without overwrite, and emit a version-3 session manifest.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .acquisition_metadata import (
    Arm,
    DataRole,
    load_sidecar,
    validate_acquisition_metadata,
    validate_finalization_metadata,
    validate_recovery_runtime_timing,
)
from .cohort_registry import validate_membership, validate_registry_history
from .common import (
    ContractError,
    canonical_json_bytes,
    read_json_object,
    require_bool,
    require_int,
    require_nonempty_string,
    require_number,
    require_sha256,
    sha256_file,
    write_new_bytes,
    write_new_json,
)
from .manifest_v3 import FRAME0_EVENT_SOURCE
from .validity import load_frame_validity

SEALED_RECEIPT_SCHEMA = "m2_sealed_radar_receipt_v1"
REFERENCE_ACQUISITION_SCHEMA = "m2_reference_acquisition_v1"


def _copy_new(source: Path, destination: Path) -> None:
    if destination.exists():
        raise ContractError(f"refusing to overwrite immutable artifact {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with source.open("rb") as input_handle, destination.open("xb") as output_handle:
            shutil.copyfileobj(input_handle, output_handle, length=1 << 20)
    except FileExistsError as exc:
        raise ContractError(f"refusing to overwrite immutable artifact {destination}") from exc


def prepare_capture_inputs(
    run_dir: str | Path,
    *,
    source_config_path: str | Path,
    effective_config: Mapping[str, object],
    acquisition_sidecar_path: str | Path,
) -> dict[str, str]:
    """Snapshot exact source YAML/sidecar bytes and canonical effective configuration."""
    output = Path(run_dir)
    config_source = Path(source_config_path)
    sidecar_source = Path(acquisition_sidecar_path)
    acquisition_metadata = load_sidecar(sidecar_source)
    config_destination = output / "source_config.yaml"
    sidecar_destination = output / "acquisition_sidecar.yaml"
    effective_destination = output / "effective_config.json"
    _copy_new(config_source, config_destination)
    _copy_new(sidecar_source, sidecar_destination)
    write_new_bytes(effective_destination, canonical_json_bytes(dict(effective_config)))
    identities = {
        "source_config_yaml_path": config_destination.name,
        "source_config_yaml_sha256": sha256_file(config_destination),
        "effective_config_path": effective_destination.name,
        "effective_config_sha256": sha256_file(effective_destination),
        "acquisition_sidecar_path": sidecar_destination.name,
        "acquisition_sidecar_sha256": sha256_file(sidecar_destination),
        "session_id": str(acquisition_metadata["session_id"]),
    }
    if acquisition_metadata["arm"] in {"natural", "paced"}:
        evidence_source = (
            sidecar_source.parent / str(acquisition_metadata["settle_evidence_path"])
        ).resolve()
        if not evidence_source.is_file():
            raise ContractError(f"settle evidence does not exist: {evidence_source}")
        if sha256_file(evidence_source) != acquisition_metadata["settle_evidence_sha256"]:
            raise ContractError("settle evidence SHA-256 differs from acquisition sidecar")
        evidence_destination = output / ("settle_evidence" + evidence_source.suffix.lower())
        _copy_new(evidence_source, evidence_destination)
        identities["settle_evidence_path"] = evidence_destination.name
        identities["settle_evidence_sha256"] = sha256_file(evidence_destination)
    return identities


def write_sealed_radar_receipt(
    run_dir: str | Path,
    *,
    acquisition_sidecar_path: str | Path,
    cohort_registry_path: str | Path,
    exact_cli_invocation: list[str],
    engineering_dry_run: bool = False,
) -> Path:
    """Verify final live artifacts and write the no-overwrite radar-only receipt."""
    run_path = Path(run_dir)
    metadata = load_sidecar(acquisition_sidecar_path)
    registry_path = Path(cohort_registry_path)
    registry_history = validate_registry_history(registry_path)
    registry_document = registry_history[-1]
    role = DataRole(metadata["data_role"])
    arm = Arm(metadata["arm"])
    if arm is Arm.RECOVERY and metadata.get("stopping_event_category") != "target_reached":
        raise ContractError(
            "recovery radar receipt requires stopping_event_category=target_reached; "
            "participant/safety stops are non-acquisition outcomes"
        )
    validate_membership(
        registry_path,
        subject_id=str(metadata["subject_id"]),
        cohort_slot=int(metadata["cohort_slot"]),
        data_role=role,
        session_id=str(metadata["session_id"]),
        arm=arm,
    )
    registry_subject = next(
        row for row in registry_document["subjects"]
        if row["subject_id"] == metadata["subject_id"]
    )
    if arm is Arm.PACED and metadata["commanded_rate_bpm"] != registry_subject[
        "paced_rate_bpm"
    ]:
        raise ContractError("paced rate disagrees with fixed cohort registry assignment")
    if type(engineering_dry_run) is not bool:
        raise ContractError("engineering_dry_run must be a Boolean")
    metadata_is_synthetic = bool(metadata.get("synthetic_fixture", False))
    registry_is_synthetic = registry_document.get("registry_kind") == "synthetic_fixture"
    if metadata_is_synthetic != registry_is_synthetic:
        raise ContractError(
            "synthetic fixture metadata and registry source must agree"
        )
    if registry_is_synthetic and not engineering_dry_run:
        raise ContractError(
            "synthetic fixture receipts must remain engineering dry runs and cannot be promotable"
        )
    if engineering_dry_run and not registry_is_synthetic:
        raise ContractError(
            "engineering dry runs require a synthetic fixture registry and metadata"
        )
    if engineering_dry_run and role is not DataRole.REPRESENTATION_VALIDATION:
        raise ContractError("isolated engineering dry run must exercise a temporary validation registry")
    if not engineering_dry_run and role not in {
        DataRole.REPRESENTATION_VALIDATION,
        DataRole.FINAL_EVALUATION,
    }:
        raise ContractError("prospective receipt requires a prospective scoring role")
    if type(exact_cli_invocation) is not list or not exact_cli_invocation or any(
        type(argument) is not str for argument in exact_cli_invocation
    ):
        raise ContractError("exact_cli_invocation must be a non-empty string array")

    run_metadata_path = run_path / "run_metadata.json"
    run_metadata = read_json_object(run_metadata_path)
    if run_metadata.get("completion_status") != "completed":
        raise ContractError("cannot seal an incomplete live run")
    if run_metadata.get("mode") != "live":
        raise ContractError("prospective receipt cannot be produced from replay")
    if run_metadata.get("prospective_study_mode") is not True:
        raise ContractError("sealed receipt requires prospective strict-mode run metadata")
    if run_metadata.get("session_id") != metadata["session_id"]:
        raise ContractError("live run session_id disagrees with acquisition sidecar")
    if run_metadata.get("exact_cli_invocation") != exact_cli_invocation:
        raise ContractError("sealed receipt CLI invocation disagrees with exact run metadata")
    if run_metadata.get("acquisition_metadata") != metadata:
        raise ContractError("run acquisition metadata disagrees with validated sidecar")
    if run_metadata.get("git_dirty") is not False or run_metadata.get("git_commit") in {None, "unknown"}:
        raise ContractError("prospective capture requires a known clean capture commit")
    if run_metadata.get("frame0_epoch_source") != FRAME0_EVENT_SOURCE:
        raise ContractError("run metadata did not persist the exact frame-0 start-assignment event")
    frame0_epoch = run_metadata.get("frame0_epoch_utc")
    if type(frame0_epoch) not in (int, float):
        raise ContractError("frame0_epoch_utc is missing or nonnumeric")
    recovery_timing = validate_recovery_runtime_timing(
        metadata,
        run_metadata,
        frame0_epoch_utc=float(frame0_epoch),
    )

    packet_stats = run_metadata.get("live_packet_stats")
    if type(packet_stats) is not dict:
        raise ContractError("live_packet_stats is missing")
    packets_received = require_int(packet_stats, "packets_received", minimum=0)
    packets_dropped = require_int(packet_stats, "packets_dropped", minimum=0)
    packets_short = require_int(packet_stats, "packets_short_discarded", minimum=0)
    packets_late = require_int(packet_stats, "packets_duplicate_or_late_discarded", minimum=0)
    n_frames = require_int(packet_stats, "n_frames", minimum=0)
    trailing_bytes = require_int(packet_stats, "trailing_partial_frame_bytes", minimum=0)
    bytes_per_frame = require_int(packet_stats, "bytes_per_frame", minimum=1)
    if packets_received == 0:
        raise ContractError("packets_received=0 cannot be sealed as an exact radar stream")
    if n_frames != 12_000:
        raise ContractError(
            f"prospective capture is incomplete: expected exactly 12000 frames, got {n_frames}"
        )

    raw_path = run_path / "adc_stream.bin"
    validity_path = run_path / "frame_validity.npy"
    source_config_path = run_path / "source_config.yaml"
    effective_config_path = run_path / "effective_config.json"
    snapshotted_sidecar_path = run_path / "acquisition_sidecar.yaml"
    for path in (
        raw_path,
        validity_path,
        source_config_path,
        effective_config_path,
        snapshotted_sidecar_path,
    ):
        if not path.is_file():
            raise ContractError(f"required sealed-capture input is missing: {path}")
    if sha256_file(snapshotted_sidecar_path) != sha256_file(acquisition_sidecar_path):
        raise ContractError("acquisition sidecar changed after its capture-time snapshot")
    validity = load_frame_validity(validity_path, expected_frames=n_frames)
    n_invalid = int(np.count_nonzero(~validity))
    if raw_path.stat().st_size != n_frames * bytes_per_frame:
        raise ContractError("raw mirror size disagrees with complete stored-frame count")
    logical_stream_bytes = (packets_received + packets_dropped) * 1456
    represented_stream_bytes = n_frames * bytes_per_frame + trailing_bytes
    if logical_stream_bytes != represented_stream_bytes:
        raise ContractError(
            "packet byte conservation failed: accepted+dropped 1456-byte intervals "
            "must equal complete-frame bytes plus trailing bytes"
        )

    loss_ratio = packets_dropped / packets_received
    receipt = {
        "schema": SEALED_RECEIPT_SCHEMA,
        "receipt_state": "sealed_radar_only",
        "engineering_dry_run": engineering_dry_run,
        "acquisition_metadata": metadata,
        "session_id": metadata["session_id"],
        "subject_id": metadata["subject_id"],
        "cohort_slot": metadata["cohort_slot"],
        "data_role": metadata["data_role"],
        "arm": metadata["arm"],
        "visit_number": metadata["visit_number"],
        "frame0_epoch": float(frame0_epoch),
        "frame0_event_source": FRAME0_EVENT_SOURCE,
        "start_wall_utc": run_metadata.get("start_wall_utc"),
        "clock_offset_start_s": metadata["clock_offset_start_s"],
        "capture_git_commit": run_metadata["git_commit"],
        "capture_git_dirty": False,
        "exact_cli_invocation": exact_cli_invocation,
        "prospective_contract_enforced": True,
        "raw_path": raw_path.name,
        "raw_sha256": sha256_file(raw_path),
        "source_config_yaml_path": source_config_path.name,
        "source_config_yaml_sha256": sha256_file(source_config_path),
        "effective_config_path": effective_config_path.name,
        "effective_config_sha256": sha256_file(effective_config_path),
        "capture_metadata_path": run_metadata_path.name,
        "capture_metadata_sha256": sha256_file(run_metadata_path),
        "acquisition_sidecar_path": snapshotted_sidecar_path.name,
        "acquisition_sidecar_sha256": sha256_file(snapshotted_sidecar_path),
        "frame_validity_map_path": validity_path.name,
        "frame_validity_map_sha256": sha256_file(validity_path),
        "cohort_registry_filename": registry_path.name,
        "registry_sha256": sha256_file(registry_path),
        "packets_received": packets_received,
        "packets_dropped": packets_dropped,
        "packets_short_discarded": packets_short,
        "packets_duplicate_or_late_discarded": packets_late,
        "packet_loss_ratio": loss_ratio,
        "packet_loss_above_5_percent": 20 * packets_dropped > packets_received,
        "n_frames": n_frames,
        "n_invalid_frames": n_invalid,
        "trailing_partial_frame_bytes": trailing_bytes,
        "bytes_per_frame": bytes_per_frame,
        **recovery_timing,
    }
    if arm in {Arm.NATURAL, Arm.PACED}:
        evidence_candidates = list(run_path.glob("settle_evidence.*"))
        if len(evidence_candidates) != 1:
            raise ContractError("natural/paced receipt requires exactly one settle evidence snapshot")
        evidence_path = evidence_candidates[0]
        if sha256_file(evidence_path) != metadata["settle_evidence_sha256"]:
            raise ContractError("settle evidence snapshot does not match acquisition metadata")
        receipt["settle_evidence_path"] = evidence_path.name
        receipt["settle_evidence_sha256"] = sha256_file(evidence_path)
    return write_new_json(run_path / "sealed_radar_receipt.json", receipt)


def _verify_receipt(run_path: Path, receipt: Mapping[str, object]) -> None:
    if receipt.get("schema") != SEALED_RECEIPT_SCHEMA or receipt.get("receipt_state") != "sealed_radar_only":
        raise ContractError("sealed radar receipt has the wrong schema/state")
    metadata_raw = receipt.get("acquisition_metadata")
    if type(metadata_raw) is not dict:
        raise ContractError("sealed receipt acquisition_metadata is malformed")
    metadata = validate_acquisition_metadata(metadata_raw)
    if (
        metadata["arm"] == Arm.RECOVERY.value
        and metadata.get("stopping_event_category") != "target_reached"
    ):
        raise ContractError(
            "sealed recovery receipt requires stopping_event_category=target_reached"
        )
    expected_engineering = bool(metadata.get("synthetic_fixture", False))
    if receipt.get("engineering_dry_run") is not expected_engineering:
        raise ContractError("sealed receipt engineering identity disagrees with acquisition metadata")
    bindings = (
        ("raw_path", "raw_sha256"),
        ("source_config_yaml_path", "source_config_yaml_sha256"),
        ("effective_config_path", "effective_config_sha256"),
        ("capture_metadata_path", "capture_metadata_sha256"),
        ("acquisition_sidecar_path", "acquisition_sidecar_sha256"),
        ("frame_validity_map_path", "frame_validity_map_sha256"),
    )
    for path_key, hash_key in bindings:
        path = run_path / require_nonempty_string(receipt, path_key)
        expected_hash = require_sha256(receipt.get(hash_key), hash_key)
        if not path.is_file() or sha256_file(path) != expected_hash:
            raise ContractError(f"sealed receipt binding failed for {path_key}")
    run_metadata = read_json_object(run_path / str(receipt["capture_metadata_path"]))
    exact_cli_invocation = receipt.get("exact_cli_invocation")
    if (
        type(exact_cli_invocation) is not list
        or not exact_cli_invocation
        or any(type(argument) is not str for argument in exact_cli_invocation)
    ):
        raise ContractError("sealed receipt exact_cli_invocation is malformed")
    run_semantics = {
        "completion_status": "completed",
        "mode": "live",
        "prospective_study_mode": True,
        "session_id": metadata["session_id"],
        "git_commit": receipt.get("capture_git_commit"),
        "git_dirty": False,
        "frame0_epoch_source": FRAME0_EVENT_SOURCE,
        "frame0_epoch_utc": receipt.get("frame0_epoch"),
        "exact_cli_invocation": exact_cli_invocation,
        "acquisition_metadata": metadata,
    }
    for key, expected_value in run_semantics.items():
        if run_metadata.get(key) != expected_value:
            raise ContractError(f"sealed receipt run metadata semantic field {key} disagrees")
    receipt_semantics = {
        "session_id": metadata["session_id"],
        "subject_id": metadata["subject_id"],
        "cohort_slot": metadata["cohort_slot"],
        "data_role": metadata["data_role"],
        "arm": metadata["arm"],
        "visit_number": metadata["visit_number"],
        "frame0_event_source": FRAME0_EVENT_SOURCE,
        "clock_offset_start_s": metadata["clock_offset_start_s"],
        "capture_git_dirty": False,
        "prospective_contract_enforced": True,
    }
    for key, expected_value in receipt_semantics.items():
        if receipt.get(key) != expected_value:
            raise ContractError(f"sealed receipt semantic field {key} disagrees")
    recovery_timing = validate_recovery_runtime_timing(
        metadata,
        receipt,
        frame0_epoch_utc=float(receipt["frame0_epoch"]),
    )
    for key, expected_value in recovery_timing.items():
        if run_metadata.get(key) != expected_value:
            raise ContractError(f"sealed receipt run recovery timing field {key} disagrees")
    if receipt.get("capture_git_commit") in {None, "", "unknown"}:
        raise ContractError("sealed receipt requires a known clean capture commit")
    if "settle_evidence_path" in receipt or "settle_evidence_sha256" in receipt:
        if "settle_evidence_path" not in receipt or "settle_evidence_sha256" not in receipt:
            raise ContractError("sealed receipt contains a half-bound settle evidence artifact")
        evidence_path = run_path / require_nonempty_string(receipt, "settle_evidence_path")
        evidence_hash = require_sha256(
            receipt.get("settle_evidence_sha256"), "settle_evidence_sha256"
        )
        if not evidence_path.is_file() or sha256_file(evidence_path) != evidence_hash:
            raise ContractError("sealed receipt settle evidence binding failed")
    validity = load_frame_validity(
        run_path / str(receipt["frame_validity_map_path"]),
        expected_frames=require_int(receipt, "n_frames", minimum=0),
    )
    if int(np.count_nonzero(~validity)) != require_int(receipt, "n_invalid_frames", minimum=0):
        raise ContractError("sealed receipt invalid-frame count disagrees with validity map")
    packets_received = require_int(receipt, "packets_received", minimum=1)
    packets_dropped = require_int(receipt, "packets_dropped", minimum=0)
    n_frames = require_int(receipt, "n_frames", minimum=0)
    bytes_per_frame = require_int(receipt, "bytes_per_frame", minimum=1)
    trailing_bytes = require_int(receipt, "trailing_partial_frame_bytes", minimum=0)
    if n_frames != 12_000:
        raise ContractError("sealed prospective receipt must contain exactly 12000 frames")
    if require_number(metadata, "intended_duration_s") != 600.0:
        raise ContractError("sealed prospective receipt requires exactly 600 seconds")
    packet_stats = run_metadata.get("live_packet_stats")
    if type(packet_stats) is not dict:
        raise ContractError("sealed receipt run metadata is missing live_packet_stats")
    packet_semantics = {
        "packets_received": packets_received,
        "packets_dropped": packets_dropped,
        "packets_short_discarded": require_int(
            receipt, "packets_short_discarded", minimum=0
        ),
        "packets_duplicate_or_late_discarded": require_int(
            receipt, "packets_duplicate_or_late_discarded", minimum=0
        ),
        "n_frames": n_frames,
        "n_invalid_frames": require_int(receipt, "n_invalid_frames", minimum=0),
        "trailing_partial_frame_bytes": trailing_bytes,
        "bytes_per_frame": bytes_per_frame,
        "frame_validity_map_path": receipt["frame_validity_map_path"],
        "frame_validity_map_sha256": receipt["frame_validity_map_sha256"],
    }
    for key, expected_value in packet_semantics.items():
        if packet_stats.get(key) != expected_value:
            raise ContractError(f"sealed receipt run packet field {key} disagrees")
    expected_loss_ratio = packets_dropped / packets_received
    if require_number(receipt, "packet_loss_ratio") != expected_loss_ratio:
        raise ContractError("sealed receipt packet loss ratio disagrees with counters")
    if require_bool(receipt, "packet_loss_above_5_percent") != (
        20 * packets_dropped > packets_received
    ):
        raise ContractError("sealed receipt packet loss flag disagrees with counters")
    raw_path = run_path / str(receipt["raw_path"])
    if raw_path.stat().st_size != n_frames * bytes_per_frame:
        raise ContractError("sealed receipt raw byte size disagrees with complete frames")
    if (packets_received + packets_dropped) * 1456 != (
        n_frames * bytes_per_frame + trailing_bytes
    ):
        raise ContractError("sealed receipt packet byte conservation failed")


def validate_sealed_radar_receipt(receipt_path: str | Path) -> dict[str, Any]:
    """Load and validate a sealed receipt and every radar-side artifact it binds."""
    path = Path(receipt_path)
    receipt = read_json_object(path)
    _verify_receipt(path.parent, receipt)
    return receipt


def finalize_capture(
    run_dir: str | Path,
    *,
    finalization_metadata: Mapping[str, object],
    cohort_registry_path: str | Path,
    destination_dir: str | Path,
    reference_path: str | Path | None,
) -> Path:
    """Promote a sealed receipt without overwrite and emit the loadable v3 manifest."""
    run_path = Path(run_dir)
    receipt_path = run_path / "sealed_radar_receipt.json"
    receipt = read_json_object(receipt_path)
    _verify_receipt(run_path, receipt)
    if require_bool(receipt, "engineering_dry_run"):
        raise ContractError("engineering dry-run receipts can never be promoted")
    metadata = receipt.get("acquisition_metadata")
    if type(metadata) is not dict:
        raise ContractError("sealed receipt acquisition_metadata is malformed")
    arm = Arm(metadata["arm"])
    finalization = validate_finalization_metadata(
        finalization_metadata,
        arm=arm,
        pre_exertion_resting_pr_bpm=(
            float(metadata["pre_exertion_resting_pr_bpm"])
            if arm is Arm.RECOVERY
            else None
        ),
    )
    if finalization["reference_acquired"]:
        if reference_path is None or not Path(reference_path).is_file():
            raise ContractError("reference_acquired=true requires an existing reference_path")
        if Path(reference_path).name != metadata["expected_reference_basename"]:
            raise ContractError("reference file basename differs from capture-time expectation")
        if finalization["reference_basename"] != metadata["expected_reference_basename"]:
            raise ContractError("finalization reference_basename differs from capture-time expectation")
    elif reference_path is not None:
        raise ContractError("reference_acquired=false must not be given a reference_path")

    registry_path = Path(cohort_registry_path)
    registry_history = validate_registry_history(registry_path)
    registry_document = registry_history[-1]
    current_registry_hash = sha256_file(registry_path)
    capture_registry_hash = receipt["registry_sha256"]
    if (
        current_registry_hash != capture_registry_hash
        and registry_document.get("previous_registry_sha256") != capture_registry_hash
    ):
        raise ContractError(
            "finalization registry must be the capture revision or its directly chained update"
        )
    validate_membership(
        registry_path,
        subject_id=str(metadata["subject_id"]),
        cohort_slot=int(metadata["cohort_slot"]),
        data_role=DataRole(metadata["data_role"]),
        session_id=str(metadata["session_id"]),
        arm=arm,
    )
    subject_record = next(
        subject
        for subject in registry_document["subjects"]
        if subject["subject_id"] == metadata["subject_id"]
    )
    registry_session = next(
        session
        for session in subject_record["sessions"]
        if session["session_id"] == metadata["session_id"]
    )
    if registry_session.get("state") != "captured":
        raise ContractError("finalization requires a registry revision marking the slot captured")
    if registry_session.get("radar_receipt_sha256") != sha256_file(receipt_path):
        raise ContractError("captured registry slot does not bind this exact sealed radar receipt")

    destination = Path(destination_dir)
    try:
        destination.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise ContractError(f"refusing to overwrite promotion directory {destination}") from exc

    source_to_name = {
        run_path / str(receipt["raw_path"]): "adc_stream.bin",
        run_path / str(receipt["source_config_yaml_path"]): "source_config.yaml",
        run_path / str(receipt["effective_config_path"]): "effective_config.json",
        run_path / str(receipt["capture_metadata_path"]): "run_metadata.json",
        run_path / str(receipt["acquisition_sidecar_path"]): "acquisition_sidecar.yaml",
        run_path / str(receipt["frame_validity_map_path"]): "frame_validity.npy",
        receipt_path: "sealed_radar_receipt.json",
        registry_path: "cohort_registry.json",
        registry_path.with_suffix(registry_path.suffix + ".sha256"): (
            "cohort_registry.json.sha256"
        ),
    }
    if "settle_evidence_path" in receipt:
        evidence_name = str(receipt["settle_evidence_path"])
        source_to_name[run_path / evidence_name] = evidence_name
    for source, name in source_to_name.items():
        _copy_new(source, destination / name)
    for predecessor in registry_history[:-1]:
        predecessor_path = write_new_json(
            destination / f"cohort_registry_v{int(predecessor['revision']):03d}.json",
            predecessor,
        )
        write_new_bytes(
            predecessor_path.with_suffix(predecessor_path.suffix + ".sha256"),
            (sha256_file(predecessor_path) + "\n").encode("ascii"),
        )

    reference_acquisition = {
        "schema": REFERENCE_ACQUISITION_SCHEMA,
        "session_id": metadata["session_id"],
        "expected_reference_basename": metadata["expected_reference_basename"],
        **finalization,
    }
    acquisition_record_path = write_new_json(
        destination / "reference_acquisition.json", reference_acquisition
    )
    promoted_reference_path: Path | None = None
    if reference_path is not None:
        promoted_reference_path = destination / str(metadata["expected_reference_basename"])
        _copy_new(Path(reference_path), promoted_reference_path)

    bindings = {
        "raw_path": "adc_stream.bin",
        "source_config_yaml_path": "source_config.yaml",
        "effective_config_path": "effective_config.json",
        "capture_metadata_path": "run_metadata.json",
        "frame_validity_map_path": "frame_validity.npy",
        "radar_receipt_path": "sealed_radar_receipt.json",
        "reference_acquisition_path": "reference_acquisition.json",
        "cohort_registry_path": "cohort_registry.json",
    }
    hashes = {
        key.replace("_path", "_sha256"): sha256_file(destination / value)
        for key, value in bindings.items()
    }
    acquired = bool(finalization["reference_acquired"])
    session = {
        "session_id": metadata["session_id"],
        "subject_id": metadata["subject_id"],
        "cohort_slot": metadata["cohort_slot"],
        "data_role": metadata["data_role"],
        "arm": metadata["arm"],
        "visit_number": metadata["visit_number"],
        "disposition": "admitted" if acquired else "no_agreement",
        "acquisition_metadata": metadata,
        "finalization_metadata": finalization,
        "frame0_epoch": receipt["frame0_epoch"],
        "frame0_event_source": FRAME0_EVENT_SOURCE,
        "clock_offset_start_s": metadata["clock_offset_start_s"],
        "clock_offset_end_s": finalization["clock_offset_end_s"],
        "intended_duration_s": metadata["intended_duration_s"],
        "actual_duration_s": finalization["actual_duration_s"],
        "capture_git_commit": receipt["capture_git_commit"],
        "capture_git_dirty": False,
        "exact_cli_invocation": receipt["exact_cli_invocation"],
        "prospective_contract_enforced": True,
        "packets_received": receipt["packets_received"],
        "packets_dropped": receipt["packets_dropped"],
        "packets_short_discarded": receipt["packets_short_discarded"],
        "packets_duplicate_or_late_discarded": receipt[
            "packets_duplicate_or_late_discarded"
        ],
        "packet_loss_ratio": receipt["packet_loss_ratio"],
        "packet_loss_above_5_percent": receipt["packet_loss_above_5_percent"],
        "n_frames": receipt["n_frames"],
        "n_invalid_frames": receipt["n_invalid_frames"],
        "trailing_partial_frame_bytes": receipt["trailing_partial_frame_bytes"],
        "bytes_per_frame": receipt["bytes_per_frame"],
        "reference_acquired": acquired,
        **bindings,
        **hashes,
    }
    for key in (
        "recovery_seated_start_utc",
        "recovery_seated_start_event_source",
        "sit_to_record_delay_s",
    ):
        if key in receipt:
            session[key] = receipt[key]
    session["registry_sha256"] = sha256_file(destination / "cohort_registry.json")
    if promoted_reference_path is not None:
        session["reference_path"] = promoted_reference_path.name
        session["reference_sha256"] = sha256_file(promoted_reference_path)
    if "settle_evidence_path" in receipt:
        session["settle_evidence_path"] = receipt["settle_evidence_path"]
        session["settle_evidence_sha256"] = receipt["settle_evidence_sha256"]
    from .manifest_v3 import Mode, parse_session_v3

    parse_session_v3(session, Mode.SCORING, root=destination)
    manifest = {"manifest_schema_version": 3, "sessions": [session]}
    return write_new_json(destination / "session_manifest_v3.json", manifest)
