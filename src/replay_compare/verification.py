"""Final replay verification with registered-source scientific binding."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.fft import fft as sp_fft

from src.clutter import remove_static_clutter
from src.live_motion.evidence import verify_development_evidence

from .coverage import CoverageEvent, CoverageLedger
from .evidence import (
    REPLAY_EVIDENCE_SCHEMA,
    VerificationIssue,
    verify_replay_artifacts,
)
from .reference import PassiveReferenceTimeline
from .session import ReplaySessionSpec
from .source import ProductionFrameDecoder, ReplaySourceError, SequentialAdcSource


@dataclass(frozen=True)
class ReplayVerificationReport:
    mode: str
    internal_issues: tuple[VerificationIssue, ...]
    source_issues: tuple[VerificationIssue, ...]
    source_binding_complete: bool

    @property
    def accepted(self) -> bool:
        return (
            self.source_binding_complete
            and not self.internal_issues
            and not self.source_issues
        )


@dataclass(frozen=True)
class _PhaseBinding:
    job_id: str
    npz_path: Path
    field: str
    frame_start: int
    frame_stop: int
    bin_index: int


def verify_replay_with_source(
    pass_dir: str | Path,
    spec: ReplaySessionSpec | None,
    *,
    reference_timeline: PassiveReferenceTimeline | None = None,
) -> ReplayVerificationReport:
    """Verify artifacts and bind every saved phase to the registered ADC.

    A missing authorized source is an explicit incomplete result.  It never
    degrades into a successful internal-only verification claim.
    """

    root = Path(pass_dir)
    mode, containment_issues = _artifact_containment_preflight(root)
    if containment_issues:
        return ReplayVerificationReport(
            mode,
            tuple(containment_issues),
            (VerificationIssue(
                "source_binding_not_attempted",
                "artifact containment preflight failed before scientific reads",
            ),),
            False,
        )
    if spec is None:
        return ReplayVerificationReport(
            mode,
            (),
            (VerificationIssue(
                "source_inputs_unavailable",
                "registered ADC authorization was not supplied",
            ),),
            False,
        )
    authorization_issues = (
        _verify_motion_authorization(root, spec) if mode == "motion" else []
    )
    if authorization_issues:
        # The native motion verifier reads the saved calibration snapshot to
        # recompute breathing evidence.  Do not let unbound snapshot bytes
        # influence that verification pass.
        return ReplayVerificationReport(
            mode, (), tuple(authorization_issues), False
        )
    if mode == "motion":
        internal = tuple(
            VerificationIssue(issue.code, issue.details)
            for issue in verify_development_evidence(root)
        )
    else:
        internal = verify_replay_artifacts(root)
    identity_issues = _verify_run_identity(root, spec, mode)
    common_issues = _verify_common_replay_artifacts(
        root, mode, reference_timeline=reference_timeline
    )
    try:
        bindings, preparation_issues = _load_phase_bindings(root, mode, spec)
    except (OSError, ValueError, TypeError, csv.Error, json.JSONDecodeError) as exc:
        return ReplayVerificationReport(
            mode,
            tuple(internal) + tuple(identity_issues) + tuple(common_issues),
            tuple(authorization_issues) + (
                VerificationIssue("source_binding_schema", str(exc)),
            ),
            False,
        )
    source_issues = list(authorization_issues)
    source_issues.extend(_verify_saved_source_provenance(root, spec, mode))
    source_issues.extend(preparation_issues)
    source_issues.extend(_verify_phase_bindings(bindings, spec))
    return ReplayVerificationReport(
        mode,
        tuple(internal) + tuple(identity_issues) + tuple(common_issues),
        tuple(source_issues),
        not source_issues,
    )


def _artifact_containment_preflight(
    pass_dir: Path,
) -> tuple[str, list[VerificationIssue]]:
    """Reject redirected artifacts before any scientific verifier opens them."""

    mode = "unknown"
    try:
        root = pass_dir.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("replay pass is not a directory")

        def exact_file(name: str) -> Path:
            candidate = root / name
            resolved = candidate.resolve(strict=True)
            if resolved != candidate or not resolved.is_file():
                raise ValueError(f"artifact is redirected or not a file: {name}")
            return resolved

        exact_file("manifest.json")
        metadata_path = exact_file("run_metadata.json")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if type(metadata) is not dict:
            raise TypeError("run metadata must be an object")
        candidate_mode = metadata.get("mode")
        if candidate_mode not in {"baseline", "motion"}:
            raise ValueError(f"unsupported replay mode {candidate_mode!r}")
        mode = str(candidate_mode)
        if (
            mode == "motion"
            and metadata.get("calibration_snapshot")
            != "development_calibration_snapshot.json"
        ):
            raise ValueError("calibration snapshot path differs from exact schema")
        common = {
            "analysis_attempts.csv", "summary.json",
            "display_state_intervals.json", "reference_audit.json",
            "reference_display_audit.json",
        }
        mode_files = (
            {"controller_events.jsonl", "playback_events.jsonl", "performance.jsonl"}
            if mode == "baseline"
            else {
                "controller_events.csv", "replay_controller_events.jsonl",
                "replay_playback_events.jsonl", "replay_performance.jsonl",
                "development_calibration_snapshot.json",
            }
        )
        for name in sorted(common | mode_files):
            exact_file(name)
        attempt_dir = root / "analysis_attempts"
        if attempt_dir.resolve(strict=True) != attempt_dir or not attempt_dir.is_dir():
            raise ValueError("analysis_attempts is redirected or not a directory")
        with (root / "analysis_attempts.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            rows = list(csv.DictReader(handle))
        for position, row in enumerate(rows):
            job_id = row.get("job_id")
            relative = row.get("npz_file")
            if (
                type(job_id) is not str
                or re.fullmatch(r"[A-Za-z0-9_-]+", job_id) is None
                or type(relative) is not str
            ):
                raise ValueError("attempt index contains an invalid job/path identity")
            normalized = relative.replace("\\", "/")
            expected = f"analysis_attempts/attempt_{position:06d}_{job_id}.npz"
            if normalized != expected:
                raise ValueError(f"attempt path differs from exact schema: {relative}")
            expected_path = root / "analysis_attempts" / Path(expected).name
            resolved = (root / relative).resolve(strict=True)
            if resolved != expected_path or not resolved.is_file():
                raise ValueError(f"attempt artifact is redirected: {relative}")
    except (OSError, KeyError, TypeError, ValueError, csv.Error, json.JSONDecodeError) as exc:
        return mode, [VerificationIssue("artifact_containment_preflight", str(exc))]
    return mode, []


def _verify_motion_authorization(
    root: Path, spec: ReplaySessionSpec
) -> list[VerificationIssue]:
    """Re-run the physical calibration gate from the active source checkout."""

    try:
        metadata = json.loads(
            (root / "run_metadata.json").read_text(encoding="utf-8")
        )
        config = metadata.get("motion_config")
        if type(config) is not dict:
            raise TypeError("motion_config must be an object")
        from .advanced import authorize_advanced_replay

        authorization = authorize_advanced_replay(
            spec,
            config,
            allow_legacy_validity_assumption=True,
        )
    except (OSError, ValueError, TypeError, RuntimeError, json.JSONDecodeError) as exc:
        return [VerificationIssue(
            "advanced_calibration_unavailable",
            f"{type(exc).__name__}: {exc}",
        )]

    try:
        if metadata.get("calibration_snapshot") != "development_calibration_snapshot.json":
            raise ValueError("calibration snapshot filename differs from exact schema")
        snapshot_path = (root / "development_calibration_snapshot.json").resolve()
        if (
            not snapshot_path.is_relative_to(root.resolve())
            or not snapshot_path.is_file()
        ):
            raise ValueError("calibration snapshot is missing or outside the pass")
        snapshot_bytes = snapshot_path.read_bytes()
        if hashlib.sha256(snapshot_bytes).hexdigest() != authorization.calibration_sha256:
            raise ValueError("calibration snapshot hash differs from authorization")
        if snapshot_bytes != authorization.calibration_record_bytes:
            raise ValueError("calibration snapshot bytes differ from authorization")
        snapshot_record = json.loads(snapshot_bytes)
        if (
            snapshot_record != authorization.calibration_record
            or snapshot_record != metadata.get("calibration_record")
        ):
            raise ValueError("calibration snapshot record differs from authorization")
    except (OSError, ValueError, TypeError, RuntimeError, json.JSONDecodeError) as exc:
        return [VerificationIssue(
            "advanced_authorization_mismatch",
            f"{type(exc).__name__}: {exc}",
        )]

    issues: list[VerificationIssue] = []
    comparisons = {
        "motion_config_sha256": authorization.config_sha256,
        "calibration_sha256": authorization.calibration_sha256,
        "calibration_record": authorization.calibration_record,
    }
    for field, expected in comparisons.items():
        if metadata.get(field) != expected:
            issues.append(VerificationIssue(
                "advanced_authorization_mismatch",
                f"{field} differs from the current accepted authorization",
            ))
    return issues


def _verify_run_identity(
    root: Path, spec: ReplaySessionSpec, mode: str
) -> list[VerificationIssue]:
    issues: list[VerificationIssue] = []
    try:
        metadata = json.loads((root / "run_metadata.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        return [VerificationIssue("run_metadata_unreadable", str(exc))]
    expected = {
        "capture_id": spec.capture_id,
        "capture_key": spec.capture_key,
        "source_kind": spec.source_kind,
        "registered_adc_sha256": spec.registered_adc_sha256,
        "metadata_sha256": spec.metadata_sha256,
        "warmup_sha256": spec.warmup_sha256,
        "original_config_sha256": spec.original_config_sha256,
        "effective_config_sha256": spec.effective_config_sha256,
        "frame_rate_hz": spec.frame_rate_hz,
        "validity_provenance": spec.validity_provenance,
        "validity_assumed": spec.validity_assumed,
        "anchor_utc_us": spec.anchor_utc_us,
        "time_origin_id": spec.time_origin_id,
        "time_origin_approximate": spec.time_origin_approximate,
        "mode": mode,
    }
    common_fields = {
        "schema", "capture_id", "capture_key", "source_kind",
        "registered_adc_sha256", "metadata_sha256", "warmup_sha256",
        "original_config_sha256", "effective_config_sha256",
        "effective_config", "generation", "mode", "frame_limit",
        "frame_rate_hz", "validity_provenance", "validity_assumed",
        "reference_is_passive", "reference_source", "source_commit",
        "scientific_source_status", "random_seed", "randomness",
        "anchor_utc_us", "time_origin_id", "time_origin_approximate",
        "geometry",
    }
    motion_fields = {
        "motion_config_sha256", "motion_config", "calibration_record_path",
        "calibration_sha256", "calibration_record", "calibration_snapshot",
    }
    expected_fields = common_fields | (motion_fields if mode == "motion" else set())
    expected_schema = (
        "replay_compare_motion_outer_v1"
        if mode == "motion"
        else REPLAY_EVIDENCE_SCHEMA
    )
    if set(metadata) != expected_fields or metadata.get("schema") != expected_schema:
        issues.append(VerificationIssue(
            "run_metadata_schema", "run metadata fields or schema differ"
        ))
    source_commit = metadata.get("source_commit")
    if (
        type(source_commit) is not str
        or len(source_commit) != 40
        or any(character not in "0123456789abcdef" for character in source_commit)
        or type(metadata.get("scientific_source_status")) is not str
        or metadata.get("random_seed") is not None
        or metadata.get("randomness") != "none"
        or metadata.get("reference_is_passive") is not True
    ):
        issues.append(VerificationIssue(
            "run_metadata_schema", "source/randomness/reference provenance is invalid"
        ))
    for name, value in expected.items():
        if metadata.get(name) != value:
            issues.append(VerificationIssue(
                "run_identity_mismatch", f"{name}: {metadata.get(name)!r} != {value!r}"
            ))
    if metadata.get("effective_config") != spec.effective_config:
        issues.append(VerificationIssue(
            "run_identity_mismatch", "effective_config differs from authorized session"
        ))
    geometry = metadata.get("geometry")
    expected_geometry = {
        "num_adc_samples": spec.num_adc_samples,
        "num_rx": spec.num_rx,
        "num_chirps_per_frame": spec.num_chirps_per_frame,
        "frame_bytes": spec.frame_bytes,
        "iq_swap": spec.iq_swap,
        "range_resolution_m": spec.range_resolution_m,
        "range_bias_m": spec.range_bias_m,
    }
    if geometry != expected_geometry:
        issues.append(VerificationIssue(
            "run_identity_mismatch", "geometry differs from authorized session"
        ))
    frame_limit = metadata.get("frame_limit")
    if type(frame_limit) is not int or not 1 <= frame_limit <= spec.frame_count:
        issues.append(VerificationIssue(
            "run_identity_mismatch", "frame_limit is outside authorized session"
        ))
    generation = metadata.get("generation")
    if type(generation) is not int or generation <= 0:
        issues.append(VerificationIssue(
            "run_identity_mismatch", "generation must be a positive exact integer"
        ))
    return issues


def _verify_saved_source_provenance(
    root: Path,
    spec: ReplaySessionSpec,
    mode: str,
) -> list[VerificationIssue]:
    """Bind the saved pass cursor and hashes to its registered read-only source."""

    issues: list[VerificationIssue] = []
    try:
        summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
        playback = _read_jsonl(root / (
            "playback_events.jsonl"
            if mode == "baseline"
            else "replay_playback_events.jsonl"
        ))
        integrity = summary["source_integrity"]
        if type(integrity) is not dict:
            raise TypeError("source_integrity must be an object")
        expected_fields = {
            "initial_sha256", "final_sha256", "streamed_sha256",
            "complete", "frames_read",
        }
        if set(integrity) != expected_fields:
            raise ValueError("source_integrity fields differ from the exact schema")
        frames_read = integrity["frames_read"]
        if type(frames_read) is not int or not 0 <= frames_read <= spec.frame_count:
            raise ValueError("source_integrity.frames_read is outside the recording")
        processed = summary.get("processed_frames")
        if type(processed) is not int or processed < 0:
            raise ValueError("summary.processed_frames must be nonnegative")
        frame_limit = summary.get("frame_limit")
        if type(frame_limit) is not int or not processed <= frame_limit <= spec.frame_count:
            raise ValueError("processed/source frames exceed the authorized frame limit")
        if frames_read > frame_limit:
            raise ValueError("source cursor exceeds the authorized frame limit")
        read_ahead = [
            event for event in playback
            if event.get("kind") == "read_ahead_discarded_at_stop"
        ]
        cursor_matches = frames_read == processed
        if not cursor_matches:
            cursor_matches = (
                frames_read == processed + 1
                and len(read_ahead) == 1
                and read_ahead[0].get("boundary") == processed
                and read_ahead[0].get("source_frames_read") == frames_read
            )
        if not cursor_matches:
            raise ValueError(
                "source cursor differs from processed frames without one evidenced "
                "stop-race read-ahead"
            )
        elif frames_read == processed and read_ahead:
            raise ValueError("read-ahead event exists without an advanced source cursor")
        expected_complete = frames_read == spec.frame_count
        if integrity["complete"] is not expected_complete:
            raise ValueError("source_integrity.complete differs from frames_read")
        expected_streamed = spec.registered_adc_sha256 if expected_complete else None
        if (
            integrity["initial_sha256"] != spec.registered_adc_sha256
            or integrity["final_sha256"] != spec.registered_adc_sha256
            or integrity["streamed_sha256"] != expected_streamed
        ):
            raise ValueError("saved source hashes differ from the registered ADC")
        prepared = [event for event in playback if event.get("kind") == "source_prepared"]
        prepared_identity = prepared[0].get("file_identity") if len(prepared) == 1 else None
        if (
            len(prepared) != 1
            or prepared[0].get("boundary") != 0
            or prepared[0].get("adc_sha256") != spec.registered_adc_sha256
            or type(prepared_identity) is not dict
            or set(prepared_identity) != {"st_dev", "st_ino", "st_size", "st_mtime_ns"}
            or any(type(value) is not int for value in prepared_identity.values())
        ):
            raise ValueError("source_prepared event does not bind the registered ADC")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        issues.append(VerificationIssue("source_integrity_mismatch", str(exc)))
    return issues


def _verify_common_replay_artifacts(
    root: Path,
    mode: str,
    *,
    reference_timeline: PassiveReferenceTimeline | None,
) -> list[VerificationIssue]:
    issues = _verify_outer_manifest(root, mode)
    try:
        summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
        saved_intervals = json.loads(
            (root / "display_state_intervals.json").read_text(encoding="utf-8")
        )
        saved_reference = json.loads(
            (root / "reference_audit.json").read_text(encoding="utf-8")
        )
        saved_reference_display = json.loads(
            (root / "reference_display_audit.json").read_text(encoding="utf-8")
        )
        metadata = json.loads(
            (root / "run_metadata.json").read_text(encoding="utf-8")
        )
        if type(summary) is not dict or type(metadata) is not dict:
            raise TypeError("summary and run metadata must be objects")
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        return issues + [VerificationIssue("common_artifact_unreadable", str(exc))]
    total = summary.get("processed_frames")
    if type(total) is not int or total < 0:
        return issues + [VerificationIssue(
            "coverage_summary_schema", "processed_frames must be nonnegative integer"
        )]
    expected_summary_fields = {
        "generation", "mode", "processed_frames", "frame_limit",
        "frame_rate_hz", "duration_s", "completion_state", "coverage",
        "coverage_intervals_half_open", "reference_is_passive",
        "source_integrity",
    }
    if set(summary) != expected_summary_fields:
        issues.append(VerificationIssue(
            "summary_schema", "summary fields differ from the exact common schema"
        ))
    saved_rate = summary.get("frame_rate_hz")
    saved_duration = summary.get("duration_s")
    numeric_summary = (
        not isinstance(saved_rate, bool)
        and isinstance(saved_rate, (int, float))
        and np.isfinite(float(saved_rate))
        and float(saved_rate) > 0.0
        and not isinstance(saved_duration, bool)
        and isinstance(saved_duration, (int, float))
        and np.isfinite(float(saved_duration))
    )
    expected_duration = (
        total / float(saved_rate) if numeric_summary else float("nan")
    )
    if (
        summary.get("mode") != mode
        or type(summary.get("generation")) is not int
        or summary.get("frame_rate_hz") != metadata.get("frame_rate_hz")
        or summary.get("frame_limit") != metadata.get("frame_limit")
        or summary.get("generation") != metadata.get("generation")
        or summary.get("coverage_intervals_half_open") is not True
        or summary.get("reference_is_passive") is not True
        or summary.get("completion_state") not in {"complete", "stopped", "failed"}
        or not numeric_summary
        or not np.isclose(
            float(saved_duration) if numeric_summary else float("nan"),
            expected_duration,
            rtol=0.0, atol=1e-12,
        )
    ):
        issues.append(VerificationIssue(
            "summary_schema", "summary identity/duration/completion fields disagree"
        ))
    if (
        summary.get("completion_state") == "complete"
        and total != summary.get("frame_limit")
    ):
        issues.append(VerificationIssue(
            "summary_completion_mismatch",
            "a complete pass must process its full authorized frame limit",
        ))
    frame_limit = metadata.get("frame_limit")
    if type(frame_limit) is not int or not 0 <= total <= frame_limit:
        issues.append(VerificationIssue(
            "coverage_summary_schema",
            "processed_frames lies outside the authorized frame limit",
        ))
    events_path = root / (
        "controller_events.jsonl" if mode == "baseline"
        else "replay_controller_events.jsonl"
    )
    try:
        events = _read_jsonl(events_path)
        generation = metadata.get("generation")
        generation_kinds = {
            "analysis_requested", "result_applied", "cancelled_before_execution",
            "rolling_coalesced", "denominator_frozen",
        }
        issues.extend(_verify_event_scalar_types(events))
        for event in events:
            if (
                event.get("kind") in generation_kinds
                and (
                    type(event.get("generation")) is not int
                    or event.get("generation") != generation
                )
            ):
                issues.append(VerificationIssue(
                    "event_generation_mismatch",
                    f"{event.get('kind')}: {event.get('generation')!r}",
                ))
        issues.extend(_verify_replay_lineage(root, events))
        if mode == "motion":
            issues.extend(_verify_motion_application_lineage(root, events))
        playback_path = root / (
            "playback_events.jsonl" if mode == "baseline"
            else "replay_playback_events.jsonl"
        )
        playback = _read_jsonl(playback_path)
        issues.extend(_verify_event_scalar_types(playback))
        if mode == "motion":
            for event in playback:
                if (
                    type(event.get("generation")) is not int
                    or event.get("generation") != generation
                ):
                    issues.append(VerificationIssue(
                        "event_generation_mismatch",
                        f"playback {event.get('kind')}: {event.get('generation')!r}",
                    ))
        frozen = [row for row in playback if row.get("kind") == "denominator_frozen"]
        if len(frozen) != 1 or frozen[0].get("boundary") != total:
            issues.append(VerificationIssue(
                "denominator_event_mismatch",
                "exactly one denominator_frozen event must match processed_frames",
            ))
        ledger = CoverageLedger(hop_frames=60)
        by_boundary: dict[int, list[dict[str, Any]]] = {}
        for event in events:
            boundary = event.get("application_boundary", event.get("boundary"))
            if boundary is None and type(event.get("frame_index")) is int:
                boundary = event["frame_index"] + (1 if mode == "motion" else 0)
            relevant = event.get("kind") in {
                "result_applied", "data_integrity_reset", "invalid_data", "data_gap",
                "movement", "target_unconfirmed", "target_lost", "selection_failed",
            }
            if relevant and (type(boundary) is not int or not 0 <= boundary <= total):
                issues.append(VerificationIssue(
                    "event_boundary", f"{event.get('kind')}: {boundary!r}"
                ))
            elif type(boundary) is int and 0 <= boundary <= total:
                by_boundary.setdefault(boundary, []).append(event)
        for boundary in range(total):
            for event in by_boundary.get(boundary, ()):
                _apply_replay_event(ledger, event, mode)
            reference = (
                reference_timeline.lookup_frame(boundary)
                if reference_timeline is not None else None
            )
            ledger.advance_frame(
                reference_pr_present=bool(reference and reference.pr_present),
                reference_pr_qualified=bool(reference and reference.pr_qualified),
                reference_rr_present=bool(reference and reference.rr_present),
            )
        for event in by_boundary.get(total, ()):
            _apply_replay_event(ledger, event, mode)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return issues + [VerificationIssue("coverage_reconstruction", str(exc))]
    if summary.get("coverage") != _json_safe_dataclass(ledger.snapshot):
        issues.append(VerificationIssue(
            "coverage_summary_mismatch", "summary coverage differs from replay events"
        ))
    intervals = [_json_safe_dataclass(row) for row in ledger.finalized_intervals()]
    if saved_intervals != intervals:
        issues.append(VerificationIssue(
            "display_interval_mismatch", "saved intervals differ from replay events"
        ))
    if reference_timeline is None:
        issues.append(VerificationIssue(
            "reference_inputs_unavailable",
            "guarded Masimo timeline was not supplied",
        ))
    else:
        if metadata.get("reference_source") != _json_safe_dataclass(
            reference_timeline.source
        ):
            issues.append(VerificationIssue(
                "reference_source_mismatch",
                "run metadata reference identity differs from guarded input",
            ))
        expected_audit: list[dict[str, Any]] = []
        attempt_bounds: dict[str, tuple[int, int, int]] = {}
        try:
            with (root / "analysis_attempts.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                for row in csv.DictReader(handle):
                    frame_start = int(row["frame_start"])
                    attempt_bounds[str(row["job_id"])] = (
                        int(row.get("hr_window_start") or frame_start),
                        int(row.get("br_window_start") or frame_start),
                        int(row["frame_stop"]),
                    )
        except (OSError, KeyError, TypeError, ValueError, csv.Error) as exc:
            issues.append(VerificationIssue("reference_audit_schema", str(exc)))
            attempt_bounds = {}
        accepted_events = [
            event for event in events
            if event.get("kind") == "result_applied"
            and (
                event.get("hr_accepted") is True
                or event.get("br_accepted") is True
            )
        ]
        for event in accepted_events:
            try:
                job_id = str(event["job_id"])
                hr_start, br_start, frame_stop = attempt_bounds[job_id]
                expected_audit.append({
                    "job_id": job_id,
                    "generation": int(event["generation"]),
                    "frame_start": int(event.get("signal_frame_start", hr_start)),
                    "frame_stop": frame_stop,
                    "hr_frame_start": hr_start,
                    "br_frame_start": br_start,
                    "reference_source": _json_safe_dataclass(reference_timeline.source),
                    "hr": _json_safe_dataclass(reference_timeline.window_summary(
                        hr_start, frame_stop, channel="hr"
                    )),
                    "br": _json_safe_dataclass(reference_timeline.window_summary(
                        br_start, frame_stop, channel="br"
                    )),
                })
            except (KeyError, TypeError, ValueError) as exc:
                issues.append(VerificationIssue("reference_audit_schema", str(exc)))
                break
        if saved_reference != expected_audit:
            issues.append(VerificationIssue(
                "reference_audit_mismatch",
                "saved reference summaries differ from guarded Masimo input",
            ))
        expected_display = {
            "schema": "replay_reference_display_audit_v1",
            "reference_source": _json_safe_dataclass(reference_timeline.source),
            "frames": [
                {
                    "frame_index": frame_index,
                    "display_boundary": frame_index + 1,
                    **_json_safe_dataclass(reference_timeline.lookup_frame(frame_index)),
                }
                for frame_index in range(total)
            ],
        }
        if saved_reference_display != expected_display:
            issues.append(VerificationIssue(
                "reference_display_audit_mismatch",
                "saved displayed reference values differ from guarded frame mapping",
            ))
    return issues


def _verify_event_scalar_types(
    events: list[dict[str, Any]],
) -> list[VerificationIssue]:
    """Reject JSON booleans/floats that compare equal to scientific integers."""

    integer_fields = {
        "generation", "boundary", "application_boundary", "frame_index",
        "epoch", "request_epoch", "applied_epoch", "selection_revision",
        "requested_selection_revision", "request_selection_revision",
        "applied_selection_revision", "frame_start", "frame_stop",
        "request_frame_start", "request_frame_stop", "signal_frame_start",
        "signal_frame_stop", "hr_window_start", "br_window_start",
        "requested_bin", "selected_bin", "source_frames_read",
    }
    boolean_fields = {"hr_accepted", "br_accepted", "quiet_accepted"}
    issues: list[VerificationIssue] = []
    for position, event in enumerate(events):
        for name in integer_fields:
            value = event.get(name)
            if name in event and value is not None and type(value) is not int:
                issues.append(VerificationIssue(
                    "event_scalar_type",
                    f"event {position} {name} must be an exact integer or null",
                ))
        for name in boolean_fields:
            value = event.get(name)
            if name in event and value is not None and type(value) is not bool:
                issues.append(VerificationIssue(
                    "event_scalar_type",
                    f"event {position} {name} must be an exact Boolean or null",
                ))
    return issues


def _verify_replay_lineage(
    root: Path, events: list[dict[str, Any]]
) -> list[VerificationIssue]:
    issues: list[VerificationIssue] = []
    request_positions: dict[str, int] = {}
    application_positions: dict[str, int] = {}
    cancellation_positions: dict[str, int] = {}
    cancellation_kinds = {"cancelled_before_execution", "rolling_coalesced"}
    for position, event in enumerate(events):
        job_id = event.get("job_id")
        if not isinstance(job_id, str):
            continue
        kind = event.get("kind")
        target = None
        if kind == "analysis_requested":
            target = request_positions
        elif kind == "result_applied":
            target = application_positions
        elif kind in cancellation_kinds:
            target = cancellation_positions
        if target is not None:
            if job_id in target:
                issues.append(VerificationIssue(
                    "duplicate_replay_lineage", f"{kind}: {job_id}"
                ))
            target[job_id] = position
    try:
        with (root / "analysis_attempts.csv").open(newline="", encoding="utf-8") as handle:
            attempt_jobs = [str(row.get("job_id", "")) for row in csv.DictReader(handle)]
    except OSError as exc:
        return issues + [VerificationIssue("missing_attempt_index", str(exc))]
    if len(attempt_jobs) != len(set(attempt_jobs)):
        issues.append(VerificationIssue(
            "duplicate_attempt_job", "attempt index contains duplicate job IDs"
        ))
    for job_id, request_position in request_positions.items():
        applied = job_id in application_positions
        cancelled = job_id in cancellation_positions
        if applied == cancelled:
            issues.append(VerificationIssue(
                "request_terminal_cardinality", job_id
            ))
            continue
        terminal_position = (
            application_positions[job_id] if applied else cancellation_positions[job_id]
        )
        if terminal_position <= request_position:
            issues.append(VerificationIssue("replay_lineage_order", job_id))
    unknown = (
        set(application_positions) | set(cancellation_positions)
    ) - set(request_positions)
    for job_id in sorted(unknown):
        issues.append(VerificationIssue("unknown_terminal_job", job_id))
    if set(attempt_jobs) != set(application_positions):
        issues.append(VerificationIssue(
            "attempt_application_set",
            "executed attempt jobs differ from result_applied jobs",
        ))
    return issues


def _verify_motion_application_lineage(
    root: Path, events: list[dict[str, Any]]
) -> list[VerificationIssue]:
    issues: list[VerificationIssue] = []
    try:
        metadata = json.loads((root / "run_metadata.json").read_text(encoding="utf-8"))
        generation = metadata["generation"]
        if type(generation) is not int or generation <= 0:
            raise TypeError("run generation must be a positive exact integer")
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return [VerificationIssue("motion_lineage_schema", str(exc))]
    requests = {
        str(event["job_id"]): event for event in events
        if event.get("kind") == "analysis_requested"
        and isinstance(event.get("job_id"), str)
    }
    applications = {
        str(event["job_id"]): event for event in events
        if event.get("kind") == "result_applied"
        and isinstance(event.get("job_id"), str)
    }
    try:
        with (root / "analysis_attempts.csv").open(newline="", encoding="utf-8") as handle:
            rows = {str(row["job_id"]): row for row in csv.DictReader(handle)}
    except (OSError, KeyError, csv.Error) as exc:
        return [VerificationIssue("motion_lineage_schema", str(exc))]
    for job_id, application in applications.items():
        request = requests.get(job_id)
        row = rows.get(job_id)
        if request is None or row is None:
            continue
        try:
            relative = row["npz_file"]
            if type(relative) is not str or not relative:
                raise ValueError("motion attempt path must be a nonempty string")
            candidate = root / relative
            path = candidate.resolve(strict=True)
            if not path.is_relative_to(root.resolve()):
                raise ValueError("motion attempt path escapes the pass directory")
            with np.load(path, allow_pickle=False) as archive:
                payload = {name: np.array(archive[name], copy=True) for name in archive.files}
            request_expected = {
                "epoch": _scalar_int(payload, "epoch"),
                "selection_revision": _scalar_int(
                    payload, "requested_selection_revision"
                ),
                "stage": str(np.asarray(payload["stage"]).item()),
                "frame_start": _scalar_int(payload, "frame_start"),
                "frame_stop": _scalar_int(payload, "frame_stop"),
            }
            if (
                type(request.get("generation")) is not int
                or request.get("generation") != generation
            ):
                raise ValueError("request generation differs from run metadata")
            if (
                type(application.get("generation")) is not int
                or application.get("generation") != generation
            ):
                raise ValueError("application generation differs from run metadata")
            requested_bin = request.get("requested_bin")
            selected_bin = application.get("selected_bin")
            if requested_bin is not None and type(requested_bin) is not int:
                raise ValueError("request requested_bin must be integer or null")
            if selected_bin is not None and type(selected_bin) is not int:
                raise ValueError("application selected_bin must be integer or null")
            if type(request.get("selection_mode")) is not str:
                raise ValueError("request selection_mode must be an exact string")
            for name, expected in request_expected.items():
                actual = request.get(name)
                if (
                    (type(expected) is int and type(actual) is not int)
                    or actual != expected
                ):
                    raise ValueError(f"request {name} differs from motion attempt")
            application_request_fields = {
                "request_epoch": request.get("epoch"),
                "request_selection_revision": request.get("selection_revision"),
                "request_stage": request.get("stage"),
                "request_frame_start": request.get("frame_start"),
                "request_frame_stop": request.get("frame_stop"),
                "requested_bin": request.get("requested_bin"),
                "selection_mode": request.get("selection_mode"),
            }
            for name, expected in application_request_fields.items():
                actual = application.get(name)
                if (
                    (type(expected) is int and type(actual) is not int)
                    or (
                        expected is None
                        and actual is not None
                    )
                    or actual != expected
                ):
                    raise ValueError(f"application {name} differs from request")
            scalar_pairs = {
                "disposition": "disposition",
                "reason": "rejection_reason",
                "signal_frame_start": "frame_start",
                "signal_frame_stop": "frame_stop",
                "applied_selection_revision": "selection_revision",
                "hr_window_start": "hr_window_start",
                "br_window_start": "br_window_start",
            }
            for event_name, payload_name in scalar_pairs.items():
                expected = np.asarray(payload[payload_name]).item()
                actual = application.get(event_name)
                if (
                    (isinstance(expected, (int, np.integer)) and type(actual) is not int)
                    or actual != expected
                ):
                    raise ValueError(
                        f"application {event_name} differs from motion attempt"
                    )
            expected_hr = bool(np.isfinite(float(payload["fresh_hr_bpm"].item())))
            expected_br = bool(np.isfinite(float(payload["fresh_br_bpm"].item())))
            expected_quiet = bool(payload["quiet_assessment_accepted"].item())
            if (
                application.get("hr_accepted") is not expected_hr
                or application.get("br_accepted") is not expected_br
                or application.get("quiet_accepted") is not expected_quiet
            ):
                raise ValueError("application admission flags differ from motion attempt")
            applied_epoch = application.get("applied_epoch")
            request_epoch = request_expected["epoch"]
            if type(applied_epoch) is not int or applied_epoch < request_epoch:
                raise ValueError("application epoch is invalid for its request")
            accepted = expected_hr or expected_br or expected_quiet
            if accepted:
                actual_bin = _scalar_int(payload, "actual_bin")
                if (
                    applied_epoch != request_epoch
                    or actual_bin < 0
                    or type(application.get("selected_bin")) is not int
                    or application.get("selected_bin") != actual_bin
                ):
                    raise ValueError(
                        "accepted application epoch/bin differs from native attempt"
                    )
            for channel in ("hr", "br"):
                saved_state = str(payload[f"display_{channel}_state"].item())
                saved_value = float(payload[f"display_{channel}_bpm"].item())
                if application.get(f"{channel}_state") != saved_state:
                    raise ValueError(f"application {channel} state differs")
                event_value = application.get(f"display_{channel}_bpm")
                if not _same_optional_number(event_value, saved_value):
                    raise ValueError(f"application {channel} value differs")
            boundary = application.get("application_boundary")
            frame_stop = _scalar_int(payload, "frame_stop")
            if type(boundary) is not int or boundary < frame_stop:
                raise ValueError("motion application precedes its signal window")
            if (expected_hr or expected_br or expected_quiet) and boundary - frame_stop > 60:
                raise ValueError("published motion result exceeds one-hop age")
        except (OSError, KeyError, TypeError, ValueError) as exc:
            issues.append(VerificationIssue(
                "motion_application_lineage", f"{job_id}: {exc}"
            ))
    return issues


def _same_optional_number(value: Any, expected: float) -> bool:
    if value is None:
        return not np.isfinite(expected)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return (
        (not np.isfinite(number) and not np.isfinite(expected))
        or np.isclose(number, expected, rtol=0.0, atol=1e-12)
    )


def _verify_outer_manifest(root: Path, mode: str) -> list[VerificationIssue]:
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return [VerificationIssue("missing_manifest", str(manifest_path))]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = manifest["files"]
        if type(files) is not dict:
            raise TypeError("manifest files must be an object")
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        return [VerificationIssue("invalid_manifest", str(exc))]
    issues: list[VerificationIssue] = []
    expected_schema = (
        REPLAY_EVIDENCE_SCHEMA
        if mode == "baseline"
        else "replay_compare_motion_outer_v1"
    )
    if set(manifest) != {"schema", "files"} or manifest.get("schema") != expected_schema:
        issues.append(VerificationIssue(
            "manifest_schema", f"expected exact schema {expected_schema!r}"
        ))
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    }
    if actual != set(files):
        issues.append(VerificationIssue("manifest_file_set", "artifact file set differs"))
    common = {
        "run_metadata.json", "analysis_attempts.csv", "summary.json",
        "display_state_intervals.json", "reference_audit.json",
        "reference_display_audit.json",
    }
    mode_required = (
        {"controller_events.jsonl", "playback_events.jsonl", "performance.jsonl"}
        if mode == "baseline"
        else {
            "controller_events.csv", "replay_controller_events.jsonl",
            "replay_playback_events.jsonl", "replay_performance.jsonl",
        }
    )
    missing_required = (common | mode_required) - set(files)
    if missing_required:
        issues.append(VerificationIssue(
            "manifest_required_files", repr(sorted(missing_required))
        ))
    for relative, expected_hash in files.items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            issues.append(VerificationIssue("manifest_containment", str(relative)))
            continue
        if _sha256(path) != expected_hash:
            issues.append(VerificationIssue("manifest_hash", str(relative)))
    return issues


def _apply_replay_event(
    ledger: CoverageLedger, event: dict[str, Any], mode: str
) -> None:
    kind = event.get("kind")
    if kind == "result_applied":
        quiet_state = None
        if mode == "motion":
            if event.get("quiet_accepted") is True:
                quiet_state = "accepted"
            elif ledger.current_states["quiet"] == "accepted":
                quiet_state = "held"
        ledger.apply(CoverageEvent(
            ledger.processed_boundary,
            str(event.get("reason", "result_applied")),
            event.get("job_id"),
            _display_state(event.get("hr_state")),
            _display_state(event.get("br_state")),
            quiet_state,
        ))
    elif kind in {
        "data_integrity_reset", "invalid_data", "data_gap", "movement",
        "target_unconfirmed", "target_lost", "selection_failed",
    }:
        ledger.clip(reason=str(event.get("reason", kind)))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise OSError(f"missing replay event stream {path}")
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if type(value) is not dict:
                    raise TypeError("replay event must be an object")
                rows.append(value)
    return rows


def _display_state(value: Any) -> str | None:
    if value is None:
        return None
    return "accepted" if value == "fresh" else str(value)


def _json_safe_dataclass(value: Any) -> dict[str, Any]:
    from dataclasses import asdict, is_dataclass
    if not is_dataclass(value):
        raise TypeError("expected dataclass value")
    return asdict(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _read_mode(root: Path) -> str:
    metadata_path = root / "run_metadata.json"
    if not metadata_path.is_file():
        return "baseline"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    mode = metadata.get("mode", "baseline")
    if mode not in {"baseline", "motion"}:
        raise ValueError(f"unsupported replay mode {mode!r}")
    return str(mode)


def _load_phase_bindings(
    root: Path,
    mode: str,
    spec: ReplaySessionSpec,
) -> tuple[list[_PhaseBinding], list[VerificationIssue]]:
    index_path = root / "analysis_attempts.csv"
    if not index_path.is_file():
        return [], [VerificationIssue("missing_attempt_index", str(index_path))]
    bindings: list[_PhaseBinding] = []
    issues: list[VerificationIssue] = []
    with index_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        job_id = str(row.get("job_id", ""))
        relative = row.get("npz_file", "")
        path = root / relative
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root.resolve()):
            issues.append(VerificationIssue("source_binding_containment", relative))
            continue
        with np.load(resolved, allow_pickle=False) as archive:
            payload = {name: np.array(archive[name], copy=True) for name in archive.files}
        identity_prefix = ""
        frame_start = _scalar_int(payload, "frame_start")
        frame_stop = _scalar_int(payload, "frame_stop")
        bin_index = _scalar_int(payload, "actual_bin")
        mismatch = _scalar_bool_optional(payload, "result_identity_mismatch")
        returned_phase_name = "returned_evidence__phase_raw"
        if mismatch:
            required_returned = (
                "returned_frame_start", "returned_frame_stop", "returned_actual_bin"
            )
            if not all(name in payload for name in required_returned):
                issues.append(VerificationIssue(
                    "unverified_result_identity",
                    f"{job_id}: returned scientific identity is incomplete",
                ))
                continue
            frame_start = _scalar_int(payload, "returned_frame_start")
            frame_stop = _scalar_int(payload, "returned_frame_stop")
            bin_index = _scalar_int(payload, "returned_actual_bin")
            if returned_phase_name in payload:
                identity_prefix = "returned_evidence__"
        phase_name = identity_prefix + "phase_raw"
        if phase_name in payload and payload[phase_name].size:
            _append_binding(
                bindings, issues, spec, job_id, resolved, phase_name,
                frame_start, frame_stop, bin_index,
            )
        if mode == "motion":
            extended_name = identity_prefix + "extended_phase"
            if extended_name in payload and payload[extended_name].size:
                start_name = identity_prefix + "br_window_start"
                extended_start = (
                    _scalar_int(payload, start_name)
                    if start_name in payload else frame_start
                )
                _append_binding(
                    bindings, issues, spec, job_id, resolved, extended_name,
                    extended_start, frame_stop, bin_index,
                )
            if mismatch:
                for name in payload:
                    if (
                        name.startswith("returned_evidence__")
                        and "phase" in name
                        and name not in {phase_name, extended_name}
                        and payload[name].size
                    ):
                        issues.append(VerificationIssue(
                            "unverified_returned_phase_component",
                            f"{job_id}: {name}",
                        ))
    return bindings, issues


def _append_binding(
    bindings: list[_PhaseBinding],
    issues: list[VerificationIssue],
    spec: ReplaySessionSpec,
    job_id: str,
    path: Path,
    field: str,
    start: int,
    stop: int,
    bin_index: int,
) -> None:
    if not (0 <= start < stop <= spec.frame_count):
        issues.append(VerificationIssue(
            "unverified_result_identity",
            f"{job_id}: invalid source bounds [{start}, {stop})",
        ))
        return
    if not 0 <= bin_index < spec.num_adc_samples:
        issues.append(VerificationIssue(
            "unverified_result_identity",
            f"{job_id}: invalid source bin {bin_index}",
        ))
        return
    if stop - start > 1200:
        issues.append(VerificationIssue(
            "unverified_result_identity",
            f"{job_id}: source window exceeds 1200 frames",
        ))
        return
    bindings.append(_PhaseBinding(job_id, path, field, start, stop, bin_index))


def _verify_phase_bindings(
    bindings: list[_PhaseBinding], spec: ReplaySessionSpec
) -> list[VerificationIssue]:
    issues: list[VerificationIssue] = []
    bindings_by_stop: dict[int, list[_PhaseBinding]] = {}
    for binding in bindings:
        bindings_by_stop.setdefault(binding.frame_stop, []).append(binding)
    bins = sorted({binding.bin_index for binding in bindings})
    maximum_window = max(
        (binding.frame_stop - binding.frame_start for binding in bindings),
        default=1,
    )
    sample_history: dict[int, deque[np.ndarray]] = {
        bin_index: deque(maxlen=maximum_window) for bin_index in bins
    }
    index_history: deque[int] = deque(maxlen=maximum_window)
    config = spec.effective_config
    hann = np.hanning(spec.num_adc_samples).astype(np.float32)
    decoder = ProductionFrameDecoder(config["profile"])
    try:
        with SequentialAdcSource(
            spec.adc_path,
            decoder,
            expected_sha256=spec.registered_adc_sha256,
            expected_frame_count=spec.frame_count,
        ) as source:
            final_stop = max(bindings_by_stop, default=0)
            while source.frames_read < final_stop:
                item = source.read_next()
                if item is None:
                    raise ValueError("registered ADC ended before an indexed attempt")
                frame_index, raw_frame = item
                profile = sp_fft(raw_frame * hann, axis=-1)
                for bin_index in bins:
                    sample_history[bin_index].append(
                        np.asarray(profile[..., bin_index], dtype=np.complex64)
                    )
                index_history.append(frame_index)
                stop = frame_index + 1
                for binding in bindings_by_stop.get(stop, ()):
                    count = binding.frame_stop - binding.frame_start
                    indices = np.asarray(tuple(index_history)[-count:], dtype=np.int64)
                    expected_indices = np.arange(
                        binding.frame_start, binding.frame_stop, dtype=np.int64
                    )
                    if not np.array_equal(indices, expected_indices):
                        issues.append(VerificationIssue(
                            "source_window_unavailable", binding.job_id
                        ))
                        continue
                    values = np.stack(tuple(sample_history[binding.bin_index])[-count:])
                    recomputed = _phase_from_bin_values(values, config)
                    with np.load(binding.npz_path, allow_pickle=False) as archive:
                        saved = np.asarray(archive[binding.field], dtype=np.float64)
                    if saved.shape != recomputed.shape or not np.allclose(
                        saved, recomputed, rtol=1e-12, atol=1e-12,
                        equal_nan=False,
                    ):
                        issues.append(VerificationIssue(
                            "source_phase_mismatch",
                            f"{binding.job_id}: {binding.field}",
                        ))
    except (OSError, ValueError, TypeError, ReplaySourceError) as exc:
        issues.append(VerificationIssue("source_inputs_unavailable", str(exc)))
    return issues


def _phase_from_bin_values(values: np.ndarray, config: dict[str, Any]) -> np.ndarray:
    method = config["phase"]["method"]
    clutter = config["phase"].get("clutter_removal", "none")
    bin_values = remove_static_clutter(values, clutter)
    if method == "mean_phasor":
        phasors = bin_values.mean(axis=(1, 2))
        return np.unwrap(np.angle(phasors)).astype(np.float64)
    if method != "delta_before_mean":
        raise ValueError(f"unsupported phase method {method!r}")
    phase_delta = np.zeros(bin_values.shape[0], dtype=np.float64)
    if bin_values.shape[0] > 1:
        products = bin_values[1:] * np.conj(bin_values[:-1])
        phase_delta[1:] = np.angle(products.mean(axis=(1, 2))).astype(np.float64)
    return np.cumsum(phase_delta)


def _scalar_int(payload: dict[str, np.ndarray], name: str) -> int:
    value = np.asarray(payload[name])
    if value.shape != () or value.dtype.kind not in "iu":
        raise ValueError(f"{name} must be an integer scalar")
    return int(value.item())


def _scalar_bool_optional(payload: dict[str, np.ndarray], name: str) -> bool:
    if name not in payload:
        return False
    value = np.asarray(payload[name])
    if value.shape != () or value.dtype != np.dtype(np.bool_):
        raise ValueError(f"{name} must be a Boolean scalar")
    return bool(value.item())
