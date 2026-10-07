"""Replay-specific attempt artifacts and independent verification."""

from __future__ import annotations

import csv
import hashlib
import json
import os
from collections import Counter
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from src.live_motion.evidence import REQUIRED_ANALYSIS_FIELDS
from src.range_coordinates import bin_range_m
from src.respiration import (
    fft_estimate_rr,
    fuse_estimates,
    ha_estimate_rr,
    stft_stability,
)
from src.vitals import estimate_rate_from_phase, remove_impulse_noise
from src.warmup_select import derive_candidate_bins
from src.window_pipeline import REJECTION_CODE_NAMES

REPLAY_EVIDENCE_SCHEMA = "replay_compare_evidence_v1"

ATTEMPT_METADATA_FIELDS = {
    "schema", "job_id", "generation", "epoch",
    "requested_selection_revision", "applied_selection_revision", "stage",
    "frame_start", "frame_stop", "request_epoch", "request_frame_start",
    "request_frame_stop", "requested_bin", "selection_mode", "actual_bin",
    "execution_status", "disposition", "reason", "application_boundary",
    "application_epoch",
    "hr_valid", "hr_raw_bpm", "hr_accepted_candidate_rank",
    "hr_ahet_verified", "br_valid", "br_raw_bpm", "hr_accepted",
    "br_accepted", "quiet_accepted", "display_hr_bpm", "display_hr_state",
    "display_br_bpm", "display_br_state", "selection_decision_json",
}
RETURNED_IDENTITY_FIELDS = {
    "result_identity_mismatch", "result_identity_mismatch_fields",
    "returned_job_id", "returned_epoch", "returned_selection_revision",
    "returned_stage", "returned_frame_start", "returned_frame_stop",
    "returned_actual_bin",
}


@dataclass(frozen=True)
class VerificationIssue:
    code: str
    details: str


@dataclass(frozen=True)
class AttemptRecord:
    index: int
    job_id: str
    npz_path: Path
    sha256: str


class ReplayEvidenceWriter:
    """Persist each executed attempt before indexing its disposition."""

    INDEX_FIELDS = (
        "schema", "attempt_index", "job_id", "generation", "epoch",
        "requested_selection_revision", "applied_selection_revision", "stage",
        "frame_start", "frame_stop", "request_epoch", "request_frame_start",
        "request_frame_stop", "requested_bin", "selection_mode",
        "actual_bin", "execution_status",
        "disposition", "reason", "application_boundary", "application_epoch",
        "hr_accepted",
        "br_accepted", "quiet_accepted", "display_hr_bpm", "display_hr_state",
        "display_br_bpm", "display_br_state", "npz_file", "npz_sha256",
    )

    def __init__(self, pass_dir: str | Path, run_metadata: Mapping[str, Any]):
        self.pass_dir = Path(pass_dir)
        self.attempt_dir = self.pass_dir / "analysis_attempts"
        self.attempt_dir.mkdir(parents=True, exist_ok=False)
        self.index_path = self.pass_dir / "analysis_attempts.csv"
        self.events_path = self.pass_dir / "controller_events.jsonl"
        self.playback_path = self.pass_dir / "playback_events.jsonl"
        self.performance_path = self.pass_dir / "performance.jsonl"
        self.metadata_path = self.pass_dir / "run_metadata.json"
        metadata = dict(run_metadata)
        metadata["schema"] = REPLAY_EVIDENCE_SCHEMA
        _write_json_atomic(self.metadata_path, metadata)
        self._next_index = 0
        self._event_kinds_by_job: dict[str, set[str]] = {}

    def write_attempt(self, executed: Any, disposition: Any) -> AttemptRecord:
        result = executed.result
        if result.executed is not True:
            raise ValueError("cancelled-before-execution jobs require event evidence only")
        index = self._next_index
        filename = f"attempt_{index:06d}_{result.job_id}.npz"
        final_path = self.attempt_dir / filename
        temporary_path = final_path.with_suffix(".npz.tmp")
        if final_path.exists() or temporary_path.exists():
            raise FileExistsError(f"refusing to overwrite {final_path}")

        dsp = result.dsp or {}
        evidence = dict(executed.evidence)
        missing = REQUIRED_ANALYSIS_FIELDS.difference(evidence)
        if missing:
            raise ValueError(f"analysis evidence omitted fields: {sorted(missing)}")
        request_bounds_are_explicit = (
            disposition.request_frame_stop > disposition.request_frame_start
        )
        request_frame_start = (
            disposition.request_frame_start
            if request_bounds_are_explicit else result.frame_start
        )
        request_frame_stop = (
            disposition.request_frame_stop
            if request_bounds_are_explicit else result.frame_stop
        )
        selection_mode = disposition.selection_mode
        if selection_mode not in {"select", "committed"}:
            selection_mode = (
                "select" if result.selection_decision is not None else "committed"
            )
        requested_bin = disposition.requested_bin
        if requested_bin is None and selection_mode == "committed":
            requested_bin = result.actual_bin
        requested_revision = disposition.requested_selection_revision
        if not request_bounds_are_explicit:
            requested_revision = result.selection_revision

        arrays: dict[str, np.ndarray] = {
            "schema": np.asarray(REPLAY_EVIDENCE_SCHEMA),
            "job_id": np.asarray(result.job_id),
            "generation": np.asarray(disposition.generation, dtype=np.int64),
            "epoch": np.asarray(result.epoch, dtype=np.int64),
            "requested_selection_revision": np.asarray(
                requested_revision, dtype=np.int64
            ),
            "applied_selection_revision": np.asarray(
                disposition.selection_revision, dtype=np.int64
            ),
            "stage": np.asarray(result.stage),
            "frame_start": np.asarray(result.frame_start, dtype=np.int64),
            "frame_stop": np.asarray(result.frame_stop, dtype=np.int64),
            "request_epoch": np.asarray(disposition.request_epoch, dtype=np.int64),
            "request_frame_start": np.asarray(request_frame_start, dtype=np.int64),
            "request_frame_stop": np.asarray(request_frame_stop, dtype=np.int64),
            "requested_bin": np.asarray(
                -1 if requested_bin is None else requested_bin,
                dtype=np.int64,
            ),
            "selection_mode": np.asarray(selection_mode),
            "actual_bin": np.asarray(
                -1 if result.actual_bin is None else result.actual_bin, dtype=np.int64
            ),
            "execution_status": np.asarray(result.status),
            "disposition": np.asarray(disposition.disposition),
            "reason": np.asarray(disposition.reason),
            "application_boundary": np.asarray(
                disposition.application_boundary, dtype=np.int64
            ),
            "application_epoch": np.asarray(
                disposition.application_epoch, dtype=np.int64
            ),
            "hr_valid": np.asarray(dsp.get("hr_valid") is True, dtype=np.bool_),
            "hr_raw_bpm": np.asarray(_finite_or_nan(dsp.get("hr_raw"))),
            "hr_accepted_candidate_rank": np.asarray(
                int((dsp.get("hr_result") or {}).get("accepted_candidate_rank", -1)),
                dtype=np.int64,
            ),
            "hr_ahet_verified": np.asarray(
                (dsp.get("hr_result") or {}).get("ahet_verified") is True,
                dtype=np.bool_,
            ),
            "br_valid": np.asarray(dsp.get("br_valid") is True, dtype=np.bool_),
            "br_raw_bpm": np.asarray(_finite_or_nan(dsp.get("br_bpm"))),
            "hr_accepted": np.asarray(disposition.hr_accepted, dtype=np.bool_),
            "br_accepted": np.asarray(disposition.br_accepted, dtype=np.bool_),
            "quiet_accepted": np.asarray(disposition.quiet_accepted, dtype=np.bool_),
            "display_hr_bpm": np.asarray(
                _finite_or_nan(disposition.display_hr_bpm), dtype=np.float64
            ),
            "display_hr_state": np.asarray(disposition.display_hr_state),
            "display_br_bpm": np.asarray(
                _finite_or_nan(disposition.display_br_bpm), dtype=np.float64
            ),
            "display_br_state": np.asarray(disposition.display_br_state),
            "selection_decision_json": np.asarray(
                _canonical_json(_selection_json(result.selection_decision))
            ),
        }
        for name, value in evidence.items():
            if name in arrays:
                raise ValueError(f"evidence field {name!r} collides with replay metadata")
            arrays[name] = _evidence_array(value, name)
        with temporary_path.open("xb") as handle:
            np.savez(handle, **arrays)
            handle.flush()
            os.fsync(handle.fileno())
        digest = _sha256_file(temporary_path)
        os.replace(temporary_path, final_path)

        row = {
            "schema": REPLAY_EVIDENCE_SCHEMA,
            "attempt_index": index,
            "job_id": result.job_id,
            "generation": disposition.generation,
            "epoch": result.epoch,
            "requested_selection_revision": requested_revision,
            "applied_selection_revision": disposition.selection_revision,
            "stage": result.stage,
            "frame_start": result.frame_start,
            "frame_stop": result.frame_stop,
            "request_epoch": disposition.request_epoch,
            "request_frame_start": request_frame_start,
            "request_frame_stop": request_frame_stop,
            "requested_bin": (
                "" if requested_bin is None else requested_bin
            ),
            "selection_mode": selection_mode,
            "actual_bin": "" if result.actual_bin is None else result.actual_bin,
            "execution_status": result.status,
            "disposition": disposition.disposition,
            "reason": disposition.reason,
            "application_boundary": disposition.application_boundary,
            "application_epoch": disposition.application_epoch,
            "hr_accepted": int(disposition.hr_accepted),
            "br_accepted": int(disposition.br_accepted),
            "quiet_accepted": int(disposition.quiet_accepted),
            "display_hr_bpm": _csv_number(disposition.display_hr_bpm),
            "display_hr_state": disposition.display_hr_state,
            "display_br_bpm": _csv_number(disposition.display_br_bpm),
            "display_br_state": disposition.display_br_state,
            "npz_file": (Path("analysis_attempts") / filename).as_posix(),
            "npz_sha256": digest,
        }
        new_file = not self.index_path.exists()
        with self.index_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.INDEX_FIELDS)
            if new_file:
                writer.writeheader()
            writer.writerow(row)
            handle.flush()
            os.fsync(handle.fileno())
        self.write_controller_event({
            "kind": "attempt_terminal",
            "job_id": result.job_id,
            "boundary": disposition.application_boundary,
            "execution_status": result.status,
            "disposition": disposition.disposition,
        })
        self._next_index += 1
        return AttemptRecord(index, result.job_id, final_path, digest)

    def write_controller_event(self, event: Any) -> None:
        record = _json_record(event)
        job_id = record.get("job_id")
        kind = record.get("kind")
        if isinstance(job_id, str) and isinstance(kind, str):
            self._event_kinds_by_job.setdefault(job_id, set()).add(kind)
        _append_jsonl(self.events_path, record)

    def write_playback_event(self, event: Mapping[str, Any]) -> None:
        _append_jsonl(self.playback_path, dict(event))

    def write_performance(self, event: Mapping[str, Any]) -> None:
        _append_jsonl(self.performance_path, dict(event))

    def finalize(
        self,
        *,
        summary: Mapping[str, Any],
        display_intervals: list[Mapping[str, Any]],
        reference_audit: list[Mapping[str, Any]],
    ) -> Path:
        if not self.index_path.exists():
            with self.index_path.open("w", newline="", encoding="utf-8") as handle:
                csv.DictWriter(handle, fieldnames=self.INDEX_FIELDS).writeheader()
        for path in (self.events_path, self.playback_path, self.performance_path):
            path.touch(exist_ok=True)
        _write_json_atomic(self.pass_dir / "summary.json", dict(summary))
        _write_json_atomic(
            self.pass_dir / "display_state_intervals.json", display_intervals
        )
        _write_json_atomic(self.pass_dir / "reference_audit.json", reference_audit)
        manifest: dict[str, str] = {}
        for path in sorted(self.pass_dir.rglob("*")):
            if path.is_file() and path.name != "manifest.json":
                manifest[path.relative_to(self.pass_dir).as_posix()] = _sha256_file(path)
        manifest_path = self.pass_dir / "manifest.json"
        _write_json_atomic(
            manifest_path,
            {"schema": REPLAY_EVIDENCE_SCHEMA, "files": manifest},
        )
        return manifest_path


def verify_replay_artifacts(pass_dir: str | Path) -> tuple[VerificationIssue, ...]:
    """Verify containment, hashes, attempt math, admission, and radar coverage."""

    root = Path(pass_dir).resolve()
    issues: list[VerificationIssue] = []
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return (VerificationIssue("missing_manifest", str(manifest_path)),)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return (VerificationIssue("invalid_manifest", str(exc)),)
    if manifest.get("schema") != REPLAY_EVIDENCE_SCHEMA:
        issues.append(VerificationIssue("manifest_schema", "unexpected schema"))
    files = manifest.get("files")
    if type(files) is not dict:
        return tuple(issues + [VerificationIssue("manifest_files", "files must be an object")])
    for relative, expected in files.items():
        candidate = root / relative
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            issues.append(VerificationIssue("missing_file", str(relative)))
            continue
        if not resolved.is_relative_to(root) or resolved.relative_to(root).as_posix() != relative:
            issues.append(VerificationIssue("file_containment", str(relative)))
            continue
        if _sha256_file(resolved) != expected:
            issues.append(VerificationIssue("file_hash", str(relative)))

    actual_files = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    if actual_files != set(files):
        issues.append(VerificationIssue(
            "manifest_file_set",
            f"unlisted={sorted(actual_files - set(files))}, "
            f"missing={sorted(set(files) - actual_files)}",
        ))

    required_artifacts = {
        "run_metadata.json", "analysis_attempts.csv", "controller_events.jsonl",
        "playback_events.jsonl", "performance.jsonl", "summary.json",
        "display_state_intervals.json", "reference_audit.json",
    }
    missing_artifacts = required_artifacts.difference(files)
    if missing_artifacts:
        issues.append(VerificationIssue(
            "required_artifacts", f"missing {sorted(missing_artifacts)}"
        ))

    metadata = _load_json(root / "run_metadata.json", issues, "run_metadata")
    summary = _load_json(root / "summary.json", issues, "summary")
    config = metadata.get("effective_config") if isinstance(metadata, dict) else None
    impulse_clip = None
    candidate_bins: set[int] | None = None
    if isinstance(config, dict):
        impulse_clip = config.get("phase", {}).get("impulse_clip_rad")
        try:
            candidate_bins = set(derive_candidate_bins(config))
        except (KeyError, TypeError, ValueError):
            candidate_bins = None

    rows = _read_csv(root / "analysis_attempts.csv", issues)
    events = _read_jsonl(root / "controller_events.jsonl", issues)
    _validate_controller_event_schema(events, issues)
    playback_events = _read_jsonl(
        root / "playback_events.jsonl", issues, label="playback_events"
    )
    freeze_boundaries = [
        int(event["boundary"]) for event in playback_events
        if event.get("kind") == "denominator_frozen"
        and type(event.get("boundary")) is int
    ]
    request_events = [
        event for event in events if event.get("kind") == "analysis_requested"
    ]
    request_counts = Counter(str(event.get("job_id")) for event in request_events)
    duplicate_requests = sorted(
        job_id for job_id, count in request_counts.items() if count != 1
    )
    if duplicate_requests:
        issues.append(VerificationIssue(
            "request_cardinality", f"duplicate requests {duplicate_requests}"
        ))
    requests_by_job = {
        str(event.get("job_id")): event
        for event in request_events
        if request_counts[str(event.get("job_id"))] == 1
    }
    terminal_jobs = [
        str(event.get("job_id")) for event in events
        if event.get("kind") == "attempt_terminal"
    ]
    indexed_jobs = [row.get("job_id", "") for row in rows]
    if sorted(terminal_jobs) != sorted(indexed_jobs):
        issues.append(VerificationIssue(
            "event_attempt_cardinality",
            f"terminal={terminal_jobs!r}, indexed={indexed_jobs!r}",
        ))
    result_applied_counts = Counter(
        str(event.get("job_id")) for event in events
        if event.get("kind") == "result_applied"
    )
    applications_by_job = {
        str(event.get("job_id")): event for event in events
        if event.get("kind") == "result_applied"
        and result_applied_counts[str(event.get("job_id"))] == 1
    }
    event_positions = {
        (str(event.get("job_id")), str(event.get("kind"))): index
        for index, event in enumerate(events)
        if isinstance(event.get("job_id"), str)
    }
    freeze_positions = [
        index for index, event in enumerate(events)
        if event.get("kind") == "denominator_frozen"
    ]
    terminal_counts = Counter(terminal_jobs)
    terminals_by_job = {
        str(event.get("job_id")): event for event in events
        if event.get("kind") == "attempt_terminal"
        and terminal_counts[str(event.get("job_id"))] == 1
    }
    cancellation_counts = Counter(
        str(event.get("job_id")) for event in events
        if event.get("kind") == "cancelled_before_execution"
    )
    unknown_cancellations = sorted(set(cancellation_counts).difference(requests_by_job))
    duplicate_cancellations = sorted(
        job_id for job_id, count in cancellation_counts.items() if count != 1
    )
    if unknown_cancellations or duplicate_cancellations:
        issues.append(VerificationIssue(
            "cancellation_lineage",
            f"unknown={unknown_cancellations}, duplicate={duplicate_cancellations}",
        ))
    unknown_applications = sorted(
        set(result_applied_counts).difference(requests_by_job)
    )
    unknown_terminals = sorted(set(terminal_jobs).difference(requests_by_job))
    if unknown_applications or unknown_terminals:
        issues.append(VerificationIssue(
            "terminal_lineage",
            f"applications_without_request={unknown_applications}, "
            f"terminals_without_request={unknown_terminals}",
        ))
    indexed_counts = Counter(indexed_jobs)
    for job_id, request in requests_by_job.items():
        indexed_count = indexed_counts[job_id]
        cancelled_count = cancellation_counts[job_id]
        if indexed_count + cancelled_count != 1:
            issues.append(VerificationIssue(
                "request_terminal_cardinality",
                f"{job_id}: indexed={indexed_count}, cancelled={cancelled_count}",
            ))
        if indexed_count == 1 and result_applied_counts[job_id] != 1:
            issues.append(VerificationIssue(
                "request_application_cardinality",
                f"{job_id}: result_applied={result_applied_counts[job_id]}",
            ))
        if request.get("generation") is None or request.get("epoch") is None:
            issues.append(VerificationIssue(
                "request_lineage", f"{job_id}: missing generation or epoch"
            ))
        request_position = event_positions.get((job_id, "analysis_requested"))
        application_position = event_positions.get((job_id, "result_applied"))
        terminal_position = event_positions.get((job_id, "attempt_terminal"))
        cancellation_position = event_positions.get(
            (job_id, "cancelled_before_execution")
        )
        if indexed_count == 1 and not (
            request_position is not None
            and application_position is not None
            and terminal_position is not None
            and request_position < application_position < terminal_position
        ):
            issues.append(VerificationIssue(
                "event_causal_order",
                f"{job_id}: expected request < application < terminal",
            ))
        if cancelled_count == 1 and not (
            request_position is not None
            and cancellation_position is not None
            and request_position < cancellation_position
            and application_position is None
            and terminal_position is None
        ):
            issues.append(VerificationIssue(
                "event_causal_order",
                f"{job_id}: cancelled request has impossible event order",
            ))
    unexplained_attempts = sorted(set(indexed_jobs).difference(requests_by_job))
    if unexplained_attempts:
        issues.append(VerificationIssue(
            "unexplained_attempts", f"missing requests {unexplained_attempts}"
        ))
    indexed_npz = {row.get("npz_file", "") for row in rows}
    actual_npz = {
        path.relative_to(root).as_posix()
        for path in (root / "analysis_attempts").glob("*")
        if path.is_file() and path.suffix == ".npz"
    }
    temporary_attempts = [
        path.name for path in (root / "analysis_attempts").glob("*.tmp")
    ]
    if actual_npz != indexed_npz:
        issues.append(VerificationIssue(
            "attempt_file_cardinality",
            f"unindexed={sorted(actual_npz - indexed_npz)}, "
            f"missing={sorted(indexed_npz - actual_npz)}",
        ))
    if temporary_attempts:
        issues.append(VerificationIssue(
            "temporary_attempt", f"interrupted temporary files {temporary_attempts}"
        ))
    expected_index = 0
    accepted_hr_history: list[float] = []
    current_lineage: tuple[int, int, int, int] | None = None
    last_hr_display: float | None = None
    last_br_display: float | None = None
    for row in rows:
        try:
            index = int(row["attempt_index"])
            if index != expected_index:
                raise ValueError("attempt indices are not contiguous")
            expected_index += 1
            relative = row["npz_file"]
            path = (root / relative).resolve(strict=True)
            if not path.is_relative_to(root):
                raise ValueError("attempt NPZ escapes pass directory")
            if _sha256_file(path) != row["npz_sha256"]:
                raise ValueError("attempt NPZ hash mismatch")
            with np.load(path, allow_pickle=False) as payload:
                _validate_attempt_schema(payload)
                if str(payload["job_id"].item()) != row["job_id"]:
                    raise ValueError("attempt job identity mismatch")
                frame_start = int(payload["frame_start"].item())
                frame_stop = int(payload["frame_stop"].item())
                if not 0 <= frame_start < frame_stop:
                    raise ValueError("attempt frame bounds are invalid")
                phase_raw = np.asarray(payload["phase_raw"], dtype=np.float64)
                phase_clean = np.asarray(payload["phase_clean"], dtype=np.float64)
                (
                    scientific_frame_start,
                    scientific_frame_stop,
                    scientific_bin,
                    scientific_identity_usable,
                ) = _validate_returned_identity(payload, phase_raw, phase_clean)
                if not scientific_identity_usable:
                    issues.append(VerificationIssue(
                        "scientific_identity_unverified",
                        f"{row['job_id']}: returned bounds/bin cannot bind retained arrays",
                    ))
                exceptional = bool(payload["exceptional_evidence"].item())
                if exceptional:
                    _validate_exceptional_evidence(payload)
                if not exceptional and scientific_identity_usable:
                    if phase_raw.shape != phase_clean.shape or phase_raw.ndim != 1:
                        raise ValueError("phase evidence cardinality mismatch")
                    if phase_raw.shape != (
                        scientific_frame_stop - scientific_frame_start,
                    ):
                        raise ValueError("phase evidence does not cover its returned window")
                    if impulse_clip is None:
                        raise ValueError("missing impulse threshold for numerical verification")
                    recomputed = remove_impulse_noise(phase_raw, float(impulse_clip))
                    if not np.allclose(recomputed, phase_clean, rtol=0.0, atol=1e-12):
                        raise ValueError("phase cleaning numerical verification failed")
                    for frequencies, spectrum in (
                        (payload["heart_freqs_hz"], payload["heart_spectrum"]),
                        (payload["resp_freqs_hz"], payload["resp_spectrum"]),
                    ):
                        if np.asarray(frequencies).shape != np.asarray(spectrum).shape:
                            raise ValueError("frequency/spectrum cardinality mismatch")
                    if np.asarray(payload["selected_peak_bins"]).shape != (3,):
                        raise ValueError("selected-peak evidence cardinality mismatch")
                    if np.asarray(payload["respiration_inputs_hz"]).shape != (1,):
                        raise ValueError("respiration-input evidence cardinality mismatch")
                    for name in (
                        "activity_resp_projection",
                        "activity_subband_projection",
                        "coherence_scores",
                    ):
                        if payload[name].size != 0:
                            raise ValueError(
                                f"baseline {name} must remain an empty production array"
                            )
                    if not isinstance(config, dict):
                        raise ValueError("missing effective config for threshold evidence")
                    expected_threshold = float(
                        config["bin_selection"]["energy_eligibility_min_settled_db"]
                    )
                    thresholds = np.asarray(payload["thresholds"])
                    if thresholds.shape != (1,) or not np.allclose(
                        thresholds,
                        np.asarray([expected_threshold], dtype=np.float64),
                        rtol=0.0,
                        atol=0.0,
                    ):
                        raise ValueError(
                            "baseline threshold evidence differs from effective config"
                        )
                actual_bin = int(payload["actual_bin"].item())
                corrected = float(payload["corrected_range_m"].item())
                if scientific_bin >= 0 and isinstance(config, dict):
                    expected_range = bin_range_m(scientific_bin, config)
                    if not np.isclose(corrected, expected_range, rtol=0.0, atol=1e-12):
                        raise ValueError("corrected range numerical verification failed")

                hr_accepted = bool(payload["hr_accepted"].item())
                hr_valid = bool(payload["hr_valid"].item())
                hr_raw = float(payload["hr_raw_bpm"].item())
                ahet_verified = bool(payload["hr_ahet_verified"].item())
                accepted_rank = int(payload["hr_accepted_candidate_rank"].item())
                br_valid = bool(payload["br_valid"].item())
                br_raw = float(payload["br_raw_bpm"].item())
                selection = json.loads(str(payload["selection_decision_json"].item()))
                selection_publishable = _validate_selection_lifecycle(
                    payload, selection, candidate_bins
                )
                recomputed_vitals: dict[str, Any] | None = None
                if (
                    not exceptional
                    and scientific_identity_usable
                    and isinstance(config, dict)
                ):
                    recomputed_vitals = _recompute_window_from_phase(phase_clean, config)
                    if hr_valid != recomputed_vitals["hr_valid"]:
                        raise ValueError("native HR validity differs from saved phase recomputation")
                    if ahet_verified != recomputed_vitals["ahet_verified"]:
                        raise ValueError("AHET verification differs from saved phase recomputation")
                    if accepted_rank != recomputed_vitals["accepted_rank"]:
                        raise ValueError("AHET accepted rank differs from saved phase recomputation")
                    if not _same_optional_rate(hr_raw, recomputed_vitals["hr_bpm"]):
                        raise ValueError("raw HR differs from saved phase recomputation")
                    if br_valid != recomputed_vitals["br_valid"]:
                        raise ValueError("native BR validity differs from saved phase recomputation")
                    if not _same_optional_rate(br_raw, recomputed_vitals["br_bpm"]):
                        raise ValueError("raw BR differs from saved phase recomputation")
                    component_pairs = (
                        (payload["heart_freqs_hz"], recomputed_vitals["heart_freqs_hz"]),
                        (payload["heart_spectrum"], recomputed_vitals["heart_spectrum"]),
                        (payload["resp_freqs_hz"], recomputed_vitals["resp_freqs_hz"]),
                        (payload["resp_spectrum"], recomputed_vitals["resp_spectrum"]),
                        (payload["selected_peak_bins"], recomputed_vitals["selected_peak_bins"]),
                        (payload["respiration_inputs_hz"], recomputed_vitals["respiration_inputs_hz"]),
                    )
                    for saved_component, expected_component in component_pairs:
                        if not np.allclose(
                            np.asarray(saved_component), np.asarray(expected_component),
                            rtol=0.0, atol=1e-12, equal_nan=True,
                        ):
                            raise ValueError("saved spectrum/peak component differs from recomputation")
                    saved_reasons = np.asarray(payload["rejection_reasons"]).astype(str)
                    if not np.array_equal(
                        saved_reasons, recomputed_vitals["rejection_reasons"]
                    ):
                        raise ValueError("saved rejection reason differs from recomputation")
                    if int(payload["hr_window_start"].item()) != scientific_frame_start:
                        raise ValueError("HR window start differs from scientific bounds")
                    if int(payload["br_window_start"].item()) != scientific_frame_start:
                        raise ValueError("BR window start differs from scientific bounds")

                lineage = (
                    int(payload["generation"].item()),
                    int(payload["epoch"].item()),
                    int(payload["applied_selection_revision"].item()),
                    int(payload["actual_bin"].item()),
                )
                if current_lineage is None or lineage != current_lineage:
                    accepted_hr_history.clear()
                    current_lineage = lineage

                application = applications_by_job.get(row["job_id"])
                if application is None:
                    raise ValueError("executed attempt lacks one application event")
                application_boundary = int(payload["application_boundary"].item())
                application_age = application_boundary - frame_stop
                if application_age < 0:
                    raise ValueError("application boundary precedes the analyzed window")
                if application_age > 60 and (
                    str(payload["disposition"].item()) == "published"
                    or bool(payload["hr_accepted"].item())
                    or bool(payload["br_accepted"].item())
                ):
                    raise ValueError(
                        "publication age exceeds the one-hop application limit"
                    )
                application_position = event_positions.get(
                    (row["job_id"], "result_applied"), -1
                )
                applied_after_freeze = any(
                    freeze_position < application_position
                    for freeze_position in freeze_positions
                )
                _validate_application_transition(
                    payload,
                    application,
                    events,
                    application_position,
                    applied_after_freeze=applied_after_freeze,
                )
                identity_mismatch = "result_identity_mismatch" in payload.files
                fresh_lifecycle = (
                    str(payload["execution_status"].item()) == "completed"
                    and int(payload["application_epoch"].item())
                    == int(payload["request_epoch"].item())
                    and 0 <= application_age <= 60
                    and not applied_after_freeze
                    and not identity_mismatch
                )
                expected_hr_accepted = bool(
                    fresh_lifecycle
                    and selection_publishable
                    and recomputed_vitals is not None
                    and recomputed_vitals["hr_valid"] is True
                    and recomputed_vitals["ahet_verified"] is True
                    and int(recomputed_vitals["accepted_rank"]) >= 0
                    and np.isfinite(float(recomputed_vitals["hr_bpm"]))
                    and float(recomputed_vitals["hr_bpm"]) > 0
                )
                expected_br_accepted = bool(
                    fresh_lifecycle
                    and selection_publishable
                    and recomputed_vitals is not None
                    and recomputed_vitals["br_valid"] is True
                    and np.isfinite(float(recomputed_vitals["br_bpm"]))
                    and float(recomputed_vitals["br_bpm"]) > 0
                )
                br_accepted = bool(payload["br_accepted"].item())
                if hr_accepted != expected_hr_accepted:
                    raise ValueError("saved HR admission differs from reconstructed eligibility")
                if br_accepted != expected_br_accepted:
                    raise ValueError("saved BR admission differs from reconstructed eligibility")
                if bool(payload["quiet_accepted"].item()):
                    raise ValueError("baseline attempts cannot claim quiet-mode admission")

                if expected_hr_accepted:
                    accepted_hr_history.append(hr_raw)
                    accepted_hr_history = accepted_hr_history[-5:]
                    expected_hr_display = float(np.median(accepted_hr_history))
                    expected_hr_state = "accepted"
                    last_hr_display = expected_hr_display
                else:
                    expected_hr_display = (
                        float("nan") if last_hr_display is None else last_hr_display
                    )
                    expected_hr_state = (
                        "missing" if last_hr_display is None else "held"
                    )
                saved_hr_display = float(payload["display_hr_bpm"].item())
                if not _same_optional_rate(saved_hr_display, expected_hr_display):
                    if expected_hr_accepted:
                        raise ValueError("accepted-only HR median reconstruction failed")
                    raise ValueError("held/missing HR display differs from prior accepted value")
                if str(payload["display_hr_state"].item()) != expected_hr_state:
                    raise ValueError("HR display state differs from reconstructed state")

                if expected_br_accepted:
                    expected_br_display = br_raw
                    expected_br_state = "accepted"
                    last_br_display = expected_br_display
                else:
                    expected_br_display = (
                        float("nan") if last_br_display is None else last_br_display
                    )
                    expected_br_state = (
                        "missing" if last_br_display is None else "held"
                    )
                saved_br_display = float(payload["display_br_bpm"].item())
                if not _same_optional_rate(saved_br_display, expected_br_display):
                    if expected_br_accepted:
                        raise ValueError("accepted BR display differs from raw accepted BR")
                    raise ValueError("held/missing BR display differs from prior accepted value")
                if str(payload["display_br_state"].item()) != expected_br_state:
                    raise ValueError("BR display state differs from reconstructed state")
                saved_disposition = str(payload["disposition"].item())
                if expected_hr_accepted or expected_br_accepted:
                    if saved_disposition != "published":
                        raise ValueError("eligible fresh result must be published")
                elif saved_disposition == "published":
                    raise ValueError("noneligible result cannot be marked published")
                if (
                    str(payload["execution_status"].item()) == "completed"
                    and not fresh_lifecycle
                    and saved_disposition != "expired"
                ):
                    raise ValueError("stale completed result must be explicitly expired")
                _assert_csv_npz_matches(row, payload)
                request = requests_by_job.get(row["job_id"])
                if request is not None:
                    _assert_request_matches_attempt(request, payload)
                _assert_application_matches_attempt(application, payload)
                terminal = terminals_by_job.get(row["job_id"])
                if terminal is not None:
                    _assert_terminal_matches_attempt(terminal, payload)
        except Exception as exc:
            issues.append(VerificationIssue("attempt", f"{row.get('job_id', '?')}: {exc}"))
    return tuple(issues)


def _validate_attempt_schema(payload: Any) -> None:
    """Require the exact baseline NPZ field set and primitive dtypes."""

    names = set(payload.files)
    identity_fields = names.intersection(RETURNED_IDENTITY_FIELDS)
    if identity_fields and identity_fields != RETURNED_IDENTITY_FIELDS:
        missing = sorted(RETURNED_IDENTITY_FIELDS.difference(identity_fields))
        raise ValueError(f"incomplete returned-identity schema: {missing}")
    expected = ATTEMPT_METADATA_FIELDS | REQUIRED_ANALYSIS_FIELDS | identity_fields
    if names != expected:
        raise ValueError(
            "attempt field set mismatch: "
            f"missing={sorted(expected - names)}, unexpected={sorted(names - expected)}"
        )

    string_scalars = {
        "schema", "job_id", "stage", "selection_mode", "execution_status",
        "disposition", "reason", "display_hr_state", "display_br_state",
        "selection_decision_json",
    }
    integer_scalars = {
        "generation", "epoch", "requested_selection_revision",
        "applied_selection_revision", "frame_start", "frame_stop",
        "request_epoch", "request_frame_start", "request_frame_stop",
        "requested_bin", "actual_bin", "application_boundary", "application_epoch",
        "hr_accepted_candidate_rank", "hr_window_start", "br_window_start",
    }
    boolean_scalars = {
        "hr_valid", "hr_ahet_verified", "br_valid", "hr_accepted",
        "br_accepted", "quiet_accepted", "exceptional_evidence",
    }
    float_scalars = {
        "hr_raw_bpm", "br_raw_bpm", "display_hr_bpm", "display_br_bpm",
        "corrected_range_m",
    }
    if identity_fields:
        string_scalars |= {"returned_job_id", "returned_stage"}
        integer_scalars |= {
            "returned_epoch", "returned_selection_revision",
            "returned_frame_start", "returned_frame_stop", "returned_actual_bin",
        }
        boolean_scalars.add("result_identity_mismatch")

    for name in string_scalars:
        array = payload[name]
        if array.shape != () or array.dtype.kind not in "SU":
            raise ValueError(f"{name} must be a scalar string")
    for name in integer_scalars:
        array = payload[name]
        if array.shape != () or array.dtype != np.dtype(np.int64):
            raise ValueError(f"{name} must be a scalar int64")
    for name in boolean_scalars:
        array = payload[name]
        if array.shape != () or array.dtype.kind != "b":
            raise ValueError(f"{name} must be a scalar boolean")
    for name in float_scalars:
        array = payload[name]
        if array.shape != () or array.dtype != np.dtype(np.float64):
            raise ValueError(f"{name} must be a scalar float64")

    float_vectors = {
        "phase_raw", "phase_clean", "heart_freqs_hz", "heart_spectrum",
        "resp_freqs_hz", "resp_spectrum", "respiration_inputs_hz",
        "activity_resp_projection", "activity_subband_projection",
        "coherence_scores", "thresholds",
    }
    for name in float_vectors:
        array = payload[name]
        if array.ndim != 1 or array.dtype != np.dtype(np.float64):
            raise ValueError(f"{name} must be a one-dimensional float64 array")
    if payload["selected_peak_bins"].ndim != 1 or (
        payload["selected_peak_bins"].dtype != np.dtype(np.int64)
    ):
        raise ValueError("selected_peak_bins must be a one-dimensional int64 array")
    reasons = payload["rejection_reasons"]
    if reasons.ndim != 1 or reasons.dtype.kind not in "SU":
        raise ValueError("rejection_reasons must be a one-dimensional string array")
    if identity_fields:
        mismatch_fields = payload["result_identity_mismatch_fields"]
        if mismatch_fields.ndim != 1 or mismatch_fields.dtype.kind not in "SU":
            raise ValueError("result_identity_mismatch_fields must be a string array")
        if payload["result_identity_mismatch"].item() is not True:
            raise ValueError("returned-identity fields require a true mismatch flag")


def _validate_exceptional_evidence(payload: Any) -> None:
    empty_fields = {
        "phase_raw", "phase_clean", "heart_freqs_hz", "heart_spectrum",
        "resp_freqs_hz", "resp_spectrum", "selected_peak_bins",
        "respiration_inputs_hz", "activity_resp_projection",
        "activity_subband_projection", "coherence_scores", "thresholds",
    }
    nonempty = sorted(name for name in empty_fields if payload[name].size != 0)
    if nonempty:
        raise ValueError(f"exceptional evidence must use typed empty arrays: {nonempty}")
    reasons = np.asarray(payload["rejection_reasons"]).astype(str)
    if reasons.shape != (1,) or not reasons[0].strip():
        raise ValueError("exceptional evidence requires one explicit rejection reason")
    execution_status = str(payload["execution_status"].item())
    if execution_status == "completed":
        try:
            decision = json.loads(str(payload["selection_decision_json"].item()))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                "completed exceptional evidence requires a selection decision"
            ) from exc
        all_dsp_failed = (
            str(payload["selection_mode"].item()) == "select"
            and isinstance(decision, dict)
            and decision.get("selector_succeeded") is True
            and decision.get("dsp_succeeded") is False
            and decision.get("fallback_used") is True
            and type(decision.get("selected_bin")) is int
            and decision.get("selected_bin")
            == int(payload["actual_bin"].item())
            and decision.get("reason") == "all_dsp_failed_energy_fallback"
            and int(payload["applied_selection_revision"].item())
            == int(payload["requested_selection_revision"].item())
        )
        if not all_dsp_failed:
            raise ValueError(
                "completed exceptional evidence requires explicit all-DSP-failed "
                "selection with unchanged selection revision"
            )
    if any(bool(payload[name].item()) for name in (
        "hr_valid", "br_valid", "hr_accepted", "br_accepted", "quiet_accepted"
    )):
        raise ValueError("exceptional evidence cannot claim validity or admission")


def _validate_returned_identity(
    payload: Any,
    phase_raw: np.ndarray,
    phase_clean: np.ndarray,
) -> tuple[int, int, int, bool]:
    """Return the coordinates that actually describe retained scientific arrays."""

    if "result_identity_mismatch" not in payload.files:
        return (
            int(payload["frame_start"].item()),
            int(payload["frame_stop"].item()),
            int(payload["actual_bin"].item()),
            True,
        )

    returned = {
        "job_id": str(payload["returned_job_id"].item()),
        "epoch": int(payload["returned_epoch"].item()),
        "selection_revision": int(
            payload["returned_selection_revision"].item()
        ),
        "stage": str(payload["returned_stage"].item()),
        "frame_start": int(payload["returned_frame_start"].item()),
        "frame_stop": int(payload["returned_frame_stop"].item()),
        "actual_bin": int(payload["returned_actual_bin"].item()),
    }
    requested = {
        "job_id": str(payload["job_id"].item()),
        "epoch": int(payload["request_epoch"].item()),
        "selection_revision": int(
            payload["requested_selection_revision"].item()
        ),
        "stage": str(payload["stage"].item()),
        "frame_start": int(payload["request_frame_start"].item()),
        "frame_stop": int(payload["request_frame_stop"].item()),
        "actual_bin": int(payload["requested_bin"].item()),
    }
    expected_mismatches = [
        name for name in (
            "job_id", "epoch", "selection_revision", "stage",
            "frame_start", "frame_stop",
        )
        if returned[name] != requested[name]
    ]
    if (
        str(payload["selection_mode"].item()) == "committed"
        and returned["actual_bin"] != requested["actual_bin"]
    ):
        expected_mismatches.append("actual_bin")
    saved_mismatches = list(
        np.asarray(payload["result_identity_mismatch_fields"]).astype(str)
    )
    if saved_mismatches != expected_mismatches or not expected_mismatches:
        raise ValueError("returned identity mismatch list does not match saved identities")
    if str(payload["execution_status"].item()) != "failed":
        raise ValueError("malformed returned identity must be a failed lifecycle attempt")
    if bool(payload["hr_accepted"].item()) or bool(payload["br_accepted"].item()):
        raise ValueError("malformed returned identity cannot publish channel values")
    if not str(payload["reason"].item()).startswith(
        "analysis_result_identity_mismatch:"
    ):
        raise ValueError("malformed returned identity requires an explicit disposition reason")

    returned_start = returned["frame_start"]
    returned_stop = returned["frame_stop"]
    returned_bin = returned["actual_bin"]
    usable = (
        0 <= returned_start < returned_stop
        and returned_bin >= 0
        and phase_raw.ndim == 1
        and phase_clean.ndim == 1
        and phase_raw.shape == phase_clean.shape
        and phase_raw.shape == (returned_stop - returned_start,)
    )
    return returned_start, returned_stop, returned_bin, usable


def _assert_csv_npz_matches(row: Mapping[str, str], payload: Any) -> None:
    string_fields = {
        "schema", "job_id", "stage", "selection_mode", "execution_status",
        "disposition", "reason", "display_hr_state", "display_br_state",
    }
    integer_fields = {
        "generation", "epoch", "requested_selection_revision",
        "applied_selection_revision", "frame_start", "frame_stop",
        "request_epoch", "request_frame_start", "request_frame_stop",
        "application_boundary", "application_epoch",
    }
    boolean_fields = {"hr_accepted", "br_accepted", "quiet_accepted"}
    optional_integer_fields = {"requested_bin", "actual_bin"}
    optional_float_fields = {"display_hr_bpm", "display_br_bpm"}
    for name in string_fields:
        if row[name] != str(payload[name].item()):
            raise ValueError(f"CSV/NPZ mismatch for {name}")
    for name in integer_fields:
        if int(row[name]) != int(payload[name].item()):
            raise ValueError(f"CSV/NPZ mismatch for {name}")
    for name in boolean_fields:
        if int(row[name]) != int(bool(payload[name].item())):
            raise ValueError(f"CSV/NPZ mismatch for {name}")
    for name in optional_integer_fields:
        saved = int(payload[name].item())
        csv_value = -1 if row[name] == "" else int(row[name])
        if csv_value != saved:
            raise ValueError(f"CSV/NPZ mismatch for {name}")
    for name in optional_float_fields:
        saved = float(payload[name].item())
        csv_value = float("nan") if row[name] == "" else float(row[name])
        if not (
            (np.isnan(saved) and np.isnan(csv_value))
            or np.isclose(saved, csv_value, rtol=0.0, atol=1e-12)
        ):
            raise ValueError(f"CSV/NPZ mismatch for {name}")


def _validate_selection_lifecycle(
    payload: Any,
    selection: Any,
    candidate_bins: set[int] | None,
) -> bool:
    """Validate the exact selector revision/bin transition.

    The return value states whether this attempt may publish otherwise eligible
    channel values.  Energy-fallback selection can establish a provisional fixed
    bin, but its selection-window vitals remain presentation-ineligible.
    """

    mode = str(payload["selection_mode"].item())
    requested_revision = int(payload["requested_selection_revision"].item())
    applied_revision = int(payload["applied_selection_revision"].item())
    execution_status = str(payload["execution_status"].item())
    requested_bin = int(payload["requested_bin"].item())
    actual_bin = int(payload["actual_bin"].item())

    if mode == "committed":
        if selection is not None:
            raise ValueError("committed-bin attempt must have a null selection decision")
        if requested_bin < 0 or actual_bin != requested_bin:
            raise ValueError("committed-bin attempt requested/actual bin mismatch")
        stale_application = (
            int(payload["application_epoch"].item())
            != int(payload["request_epoch"].item())
            or applied_revision != requested_revision
        )
        if stale_application:
            if (
                str(payload["disposition"].item()) != "expired"
                or bool(payload["hr_accepted"].item())
                or bool(payload["br_accepted"].item())
            ):
                raise ValueError(
                    "stale committed attempt must have no admission and be expired"
                )
            return False
        if applied_revision != requested_revision:
            raise ValueError("committed-bin attempt changed selection revision")
        return execution_status == "completed"

    if mode != "select":
        raise ValueError(f"unknown baseline selection mode {mode!r}")
    if not isinstance(selection, dict):
        raise ValueError("selector attempt requires a structured selection decision")
    for flag in ("selector_succeeded", "dsp_succeeded", "fallback_used"):
        if type(selection.get(flag)) is not bool:
            raise ValueError(f"selection decision {flag} must be boolean")
    selected_bin = selection.get("selected_bin")
    if selected_bin is not None and type(selected_bin) is not int:
        raise ValueError("selection decision selected_bin must be integer or null")
    if not isinstance(selection.get("reason"), str) or not selection["reason"].strip():
        raise ValueError("selection decision requires an explicit reason")

    committed = (
        execution_status == "completed"
        and selection["selector_succeeded"] is True
        and selection["dsp_succeeded"] is True
        and selected_bin is not None
    )
    if committed:
        if actual_bin != selected_bin:
            raise ValueError("selector selected bin differs from actual analyzed bin")
        if candidate_bins is None:
            raise ValueError("selector candidate-bin gate is unavailable")
        if selected_bin not in candidate_bins:
            raise ValueError("selector selected bin is outside candidate gate")
        if applied_revision != requested_revision + 1:
            raise ValueError("successful selector must increment revision exactly once")
        return selection["fallback_used"] is False

    if selected_bin is not None:
        if candidate_bins is None or selected_bin not in candidate_bins:
            raise ValueError("diagnostic selector fallback bin is outside candidate gate")
    if selection["dsp_succeeded"] is False and (
        selection["fallback_used"] is not True
        or selection["reason"] != "all_dsp_failed_energy_fallback"
    ):
        raise ValueError("all-DSP-failed selector decision is not production-authentic")

    if applied_revision != requested_revision:
        raise ValueError("unsuccessful selector changed selection revision")
    return False


def _assert_request_matches_attempt(request: Mapping[str, Any], payload: Any) -> None:
    expected = {
        "generation": int(payload["generation"].item()),
        "epoch": int(payload["request_epoch"].item()),
        "frame_start": int(payload["request_frame_start"].item()),
        "frame_stop": int(payload["request_frame_stop"].item()),
        "requested_bin": (
            None if int(payload["requested_bin"].item()) < 0
            else int(payload["requested_bin"].item())
        ),
        "selection_mode": str(payload["selection_mode"].item()),
        "requested_selection_revision": int(
            payload["requested_selection_revision"].item()
        ),
    }
    for name, expected_value in expected.items():
        if request.get(name) != expected_value:
            raise ValueError(f"request/attempt lineage mismatch for {name}")
    if int(payload["epoch"].item()) != expected["epoch"]:
        raise ValueError("executed epoch differs from requested epoch")
    if int(payload["frame_start"].item()) != expected["frame_start"] or (
        int(payload["frame_stop"].item()) != expected["frame_stop"]
    ):
        raise ValueError("executed bounds differ from requested bounds")


def _assert_application_matches_attempt(
    application: Mapping[str, Any], payload: Any
) -> None:
    requested_revision = int(payload["requested_selection_revision"].item())
    applied_revision = int(payload["applied_selection_revision"].item())
    selection_mode = str(payload["selection_mode"].item())
    stale_application_lineage = (
        int(payload["application_epoch"].item())
        != int(payload["request_epoch"].item())
        or (
            selection_mode == "committed"
            and applied_revision != requested_revision
        )
    )
    expected_selected_bin = (
        int(payload["actual_bin"].item())
        if not stale_application_lineage
        and (
            selection_mode == "committed"
            or applied_revision == requested_revision + 1
        )
        else None
    )
    expected = {
        "reason": str(payload["reason"].item()),
        "boundary": int(payload["application_boundary"].item()),
        "epoch": int(payload["application_epoch"].item()),
        "generation": int(payload["generation"].item()),
        "selection_revision": int(payload["applied_selection_revision"].item()),
        "selected_bin": expected_selected_bin,
        "hr_state": str(payload["display_hr_state"].item()),
        "br_state": str(payload["display_br_state"].item()),
        "disposition": str(payload["disposition"].item()),
        "hr_accepted": bool(payload["hr_accepted"].item()),
        "br_accepted": bool(payload["br_accepted"].item()),
    }
    for name, expected_value in expected.items():
        if application.get(name) != expected_value:
            raise ValueError(f"application/attempt mismatch for {name}")
    application_age = expected["boundary"] - int(payload["frame_stop"].item())
    if application_age < 0:
        raise ValueError("application boundary precedes the analyzed window")
    if expected["disposition"] == "published" and application_age > 60:
        raise ValueError("published application exceeds the one-hop age limit")
    if application_age > 60 and (
        expected["disposition"] != "expired"
        or expected["hr_accepted"]
        or expected["br_accepted"]
    ):
        raise ValueError("over-age application must be nonpublishing and expired")


def _validate_application_transition(
    payload: Any,
    application: Mapping[str, Any],
    events: list[dict[str, Any]],
    application_position: int,
    *,
    applied_after_freeze: bool,
) -> None:
    request_epoch = int(payload["request_epoch"].item())
    application_epoch = int(payload["application_epoch"].item())
    requested_revision = int(payload["requested_selection_revision"].item())
    applied_revision = int(payload["applied_selection_revision"].item())
    generation = int(payload["generation"].item())

    if application_epoch < request_epoch:
        raise ValueError("application epoch precedes request epoch")
    if application_epoch > request_epoch:
        reset_epochs = {
            int(event["epoch"])
            for index, event in enumerate(events)
            if index < application_position
            and event.get("kind") == "data_integrity_reset"
            and event.get("generation") == generation
            and type(event.get("epoch")) is int
            and event.get("selection_revision") == 0
            and event.get("selected_bin") is None
        }
        expected_reset_epochs = set(range(request_epoch, application_epoch))
        if not expected_reset_epochs.issubset(reset_epochs):
            raise ValueError("stale epoch lacks the complete integrity-reset lineage")
        if applied_revision != 0 or application.get("selected_bin") is not None:
            raise ValueError("post-reset application must use reset revision/bin state")
    elif (
        str(payload["selection_mode"].item()) == "committed"
        and applied_revision != requested_revision
    ):
        raise ValueError("committed application revision changed without an epoch reset")

    if applied_after_freeze:
        prior_freezes = [
            event for index, event in enumerate(events)
            if index < application_position
            and event.get("kind") == "denominator_frozen"
            and event.get("generation") == generation
        ]
        if not prior_freezes:
            raise ValueError("application claims post-freeze timing without freeze evidence")
        freeze = prior_freezes[-1]
        for name, expected in (
            ("epoch", application_epoch),
            ("selection_revision", applied_revision),
            ("selected_bin", application.get("selected_bin")),
        ):
            if freeze.get(name) != expected:
                raise ValueError(f"post-freeze application differs from frozen {name}")


def _assert_terminal_matches_attempt(
    terminal: Mapping[str, Any], payload: Any
) -> None:
    expected = {
        "boundary": int(payload["application_boundary"].item()),
        "execution_status": str(payload["execution_status"].item()),
        "disposition": str(payload["disposition"].item()),
    }
    for name, expected_value in expected.items():
        if terminal.get(name) != expected_value:
            raise ValueError(f"terminal/attempt mismatch for {name}")


def _validate_controller_event_schema(
    events: list[dict[str, Any]], issues: list[VerificationIssue]
) -> None:
    controller_fields = {
        "kind", "reason", "boundary", "epoch", "generation", "job_id",
        "selected_bin", "selection_revision", "hr_state", "br_state",
        "frame_start", "frame_stop", "requested_bin", "selection_mode",
        "requested_selection_revision", "replacement_job_id", "disposition",
        "hr_accepted", "br_accepted",
    }
    special_fields = {
        "attempt_terminal": {
            "kind", "job_id", "boundary", "execution_status", "disposition"
        },
    }
    controller_kinds = {
        "analysis_requested", "result_applied", "cancelled_before_execution",
        "active_invalidated", "data_integrity_reset", "denominator_frozen",
    }
    for index, event in enumerate(events):
        kind = event.get("kind")
        expected = special_fields.get(str(kind))
        if kind in controller_kinds:
            expected = controller_fields
        if expected is None:
            issues.append(VerificationIssue(
                "controller_event_schema", f"row {index}: unknown kind {kind!r}"
            ))
            continue
        names = set(event)
        if names != expected:
            issues.append(VerificationIssue(
                "controller_event_schema",
                f"row {index} {kind}: missing={sorted(expected - names)}, "
                f"unexpected={sorted(names - expected)}",
            ))


def _selection_json(decision: Any) -> Any:
    if decision is None:
        return None
    if is_dataclass(decision):
        return asdict(decision)
    if isinstance(decision, Mapping):
        return dict(decision)
    return str(decision)


def _primitive_array(value: Any, name: str) -> np.ndarray:
    if isinstance(value, Mapping):
        return np.asarray(_canonical_json(dict(value)))
    array = np.asarray(value)
    if array.dtype.hasobject:
        raise TypeError(f"evidence field {name!r} has object dtype")
    return array


def _evidence_array(value: Any, name: str) -> np.ndarray:
    float_fields = {
        "phase_raw", "phase_clean", "heart_freqs_hz", "heart_spectrum",
        "resp_freqs_hz", "resp_spectrum", "respiration_inputs_hz",
        "activity_resp_projection", "activity_subband_projection",
        "coherence_scores", "thresholds", "corrected_range_m",
    }
    if name in float_fields:
        return np.asarray(value, dtype=np.float64)
    if name in {"selected_peak_bins", "hr_window_start", "br_window_start"}:
        return np.asarray(value, dtype=np.int64)
    if name == "exceptional_evidence":
        return np.asarray(value, dtype=np.bool_)
    return _primitive_array(value, name)


def _finite_or_nan(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return result if np.isfinite(result) else float("nan")


def _csv_number(value: Any) -> str:
    number = _finite_or_nan(value)
    return "" if not np.isfinite(number) else f"{number:.17g}"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _json_record(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    return {"value": str(value)}


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(_canonical_json(dict(value)) + "\n")
        handle.flush()


def _write_json_atomic(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    os.replace(temporary, path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path, issues: list[VerificationIssue], label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if type(value) is not dict:
            raise ValueError("expected object")
        return value
    except Exception as exc:
        issues.append(VerificationIssue(label, str(exc)))
        return {}


def _read_csv(path: Path, issues: list[VerificationIssue]) -> list[dict[str, str]]:
    if not path.is_file():
        issues.append(VerificationIssue("attempt_index", "missing analysis_attempts.csv"))
        return []
    try:
        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != ReplayEvidenceWriter.INDEX_FIELDS:
                raise ValueError("analysis attempt CSV field set/order mismatch")
            return list(reader)
    except Exception as exc:
        issues.append(VerificationIssue("attempt_index", str(exc)))
        return []


def _read_jsonl(
    path: Path,
    issues: list[VerificationIssue],
    *,
    label: str = "controller_events",
) -> list[dict[str, Any]]:
    if not path.is_file():
        issues.append(VerificationIssue(label, f"missing {label}"))
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            value = json.loads(line)
            if type(value) is not dict:
                raise ValueError("event row must be an object")
            rows.append(value)
    except Exception as exc:
        issues.append(VerificationIssue(label, str(exc)))
    return rows


def _same_optional_rate(saved: float, expected: float) -> bool:
    if np.isnan(saved) and np.isnan(expected):
        return True
    return bool(np.isclose(saved, expected, rtol=0.0, atol=1e-12))


def _recompute_window_from_phase(phase_clean: np.ndarray, config: dict) -> dict[str, Any]:
    """Independently repeat the production vital-rate composition from saved phase."""

    fs = float(config["session"]["frame_rate_hz"])
    respiration = config["respiration"]
    band_hz = tuple(respiration["band_hz"])
    fft_result = fft_estimate_rr(
        phase_clean, fs, band_hz, detrend_type=respiration["detrend"]
    )
    harmonic_result = ha_estimate_rr(
        phase_clean,
        fs,
        band_hz,
        max_harmonics=respiration["max_harmonics"],
        harmonic_max_hz=respiration["harmonic_max_hz"],
        detrend_type=respiration["detrend"],
    )
    stability = stft_stability(
        phase_clean,
        fs,
        band_hz,
        subwindow_s=float(respiration["stft_subwindow_s"]),
        overlap=float(respiration["stft_overlap"]),
        detrend_type=respiration["detrend"],
    )
    breathing = fuse_estimates(
        fft_result, harmonic_result, stability, respiration
    )
    if breathing.get("resp_confidence") == "low":
        breathing = dict(breathing)
        breathing["resp_valid"] = False
    respiration_hz = (
        float(breathing["resp_peak_hz"])
        if breathing.get("resp_valid") is True else None
    )

    heart = config["heart"]
    heart_result = estimate_rate_from_phase(
        phase_clean,
        fs,
        tuple(heart["band_hz"]),
        f_r_hz=respiration_hz,
        k_max=int(heart["k_max"]),
        ahet_deviation_hz=float(heart["ahet_deviation_hz"]),
        eca_mode=heart["eca_mode"],
        ahet_gate_mode=heart["ahet_gate_mode"],
        eca_forbidden_guard_hz=float(heart["eca_forbidden_guard_hz"]),
        eca_cardiac_guard_hz=float(heart.get("eca_cardiac_guard_hz", 0.10)),
        k_max_cap=int(heart.get("k_max_cap", 10)),
        candidate_min_second_harmonic_ratio_db=float(
            heart["candidate_min_second_harmonic_ratio_db"]
        ),
        candidate_min_prominence=float(heart["candidate_min_prominence"]),
        low_candidate_hz=float(heart["low_candidate_hz"]),
        high_candidate_preference_hz=float(heart["high_candidate_preference_hz"]),
        high_competitor_min_mag_ratio=float(heart["high_competitor_min_mag_ratio"]),
        candidate_min_peak_to_floor_db=float(
            heart["candidate_min_peak_to_floor_db"]
        ),
        low_candidate_min_peak_to_floor_db=float(
            heart["low_candidate_min_peak_to_floor_db"]
        ),
    )
    ahet_verified = heart_result.get("ahet_verified") is True
    fft_selected = int(fft_result.get("fft_selected_bin", -1))
    harmonic_selected = int(harmonic_result.get("ha_selected_bin", -1))
    accepted_rank = int(heart_result.get("accepted_candidate_rank", -1))
    rejection_codes = np.asarray(
        heart_result.get("candidate_rejection_code", np.asarray([-1])), dtype=np.int64
    )
    if ahet_verified and 0 <= accepted_rank < len(rejection_codes):
        summary_code = int(rejection_codes[accepted_rank])
    else:
        summary_code = int(rejection_codes[0]) if len(rejection_codes) else -1
    rejection_reason = REJECTION_CODE_NAMES.get(summary_code, str(summary_code))
    return {
        "hr_valid": ahet_verified,
        "ahet_verified": ahet_verified,
        "accepted_rank": accepted_rank,
        "hr_bpm": (
            float(heart_result["rate_bpm"])
            if ahet_verified else float("nan")
        ),
        "br_valid": breathing.get("resp_valid") is True,
        "br_bpm": float(breathing.get("radar_rr_bpm", np.nan)),
        "heart_freqs_hz": np.asarray(heart_result.get("freqs_hz", [])),
        "heart_spectrum": np.asarray(heart_result.get("spectrum", [])),
        "resp_freqs_hz": np.asarray(fft_result.get("freqs_hz", [])),
        "resp_spectrum": np.asarray(fft_result.get("spectrum", [])),
        "selected_peak_bins": np.asarray(
            [fft_selected, harmonic_selected, accepted_rank], dtype=np.int64
        ),
        "respiration_inputs_hz": np.asarray([
            np.nan if respiration_hz is None else respiration_hz
        ], dtype=np.float64),
        "rejection_reasons": np.asarray([rejection_reason], dtype="U"),
    }
