"""Application boundary for an authorized recorded replay pass.

The baseline engine owns radar processing.  This module observes its durable
events to compose coverage and passive reference information for presentation;
reference values are never passed to the controller or analysis worker.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import threading
import time
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from src.range_coordinates import bin_range_m

from .clock import PlaybackClock
from .coverage import CoverageEvent, CoverageLedger
from .engine import (
    BaselineChannel,
    BaselineController,
    BaselineDisposition,
    BaselineSnapshot,
    ReplayRuntime,
)
from .evidence import ReplayEvidenceWriter
from .reference import PassiveReferenceTimeline, ReferenceValue
from .process_analysis import SpawnBaselineBackend
from .session import ReplaySessionSpec
from .source import ProductionFrameDecoder, SequentialAdcSource
from .view_model import DisplayChannel, DisplayTraceSample, PlaybackSnapshot


SourceFactory = Callable[[], SequentialAdcSource]
AnalysisFunction = Callable[[Any], Any]
ClockFactory = Callable[[float, float], PlaybackClock]


@dataclass
class _RunState:
    generation: int
    pass_dir: Path
    runtime: ReplayRuntime
    evidence: "_ComposingEvidenceWriter"
    monitor: threading.Thread | None = None
    finalized: bool = False
    finalize_error: BaseException | None = None


class ReplaySnapshotComposer:
    """Compose immutable UI snapshots without feeding reference into radar."""

    def __init__(
        self,
        spec: ReplaySessionSpec,
        *,
        generation: int,
        initial_snapshot: BaselineSnapshot,
        frame_limit: int,
        mode: str,
        reference_timeline: PassiveReferenceTimeline | None,
        hop_frames: int = 60,
    ):
        self.spec = spec
        self.generation = generation
        self.frame_limit = frame_limit
        self.mode = mode
        self.reference_timeline = reference_timeline
        self.ledger = CoverageLedger(hop_frames=hop_frames)
        self._lock = threading.Lock()
        self._hr_value: float | None = None
        self._br_value: float | None = None
        self._hr_reason = "initial_accumulation"
        self._br_reason = "initial_accumulation"
        self._last_reference: ReferenceValue | None = None
        self._hr_reference_summary = None
        self._br_reference_summary = None
        self._trace: list[DisplayTraceSample] = []
        self._reference_audit: list[dict[str, Any]] = []
        self._reference_display_frames: list[dict[str, Any]] = []
        self._pending_applications: dict[str, dict[str, Any]] = {}
        self._published_monotonic_s: float | None = None
        if initial_snapshot.generation != generation:
            raise ValueError("initial snapshot generation differs from composer")
        self._latest_snapshot = self._build_snapshot(initial_snapshot)

    @classmethod
    def for_advanced(
        cls,
        spec: ReplaySessionSpec,
        *,
        initial_snapshot: Any,
        frame_limit: int,
        reference_timeline: PassiveReferenceTimeline | None,
    ) -> "ReplaySnapshotComposer":
        return cls(
            spec,
            generation=int(initial_snapshot.generation),
            initial_snapshot=_advanced_as_baseline(initial_snapshot),
            frame_limit=frame_limit,
            mode="motion",
            reference_timeline=reference_timeline,
        )

    def observe_controller_event(self, event: Any) -> None:
        record = asdict(event) if is_dataclass(event) else dict(event)
        boundary = record.get(
            "application_boundary",
            record.get("boundary", record.get("frame_index")),
        )
        if type(boundary) is not int:
            return
        with self._lock:
            kind = record.get("kind")
            if kind == "result_applied":
                job_id = record.get("job_id")
                if isinstance(job_id, str):
                    self._pending_applications[job_id] = record
                return
            if kind not in {
                "data_integrity_reset", "invalid_data", "data_gap",
                "movement", "target_unconfirmed", "target_lost",
                "selection_failed",
            }:
                return
            self._advance_to(boundary)
            reason = str(record.get("reason", kind))
            if record.get("hr_state") is None and record.get("br_state") is None:
                self.ledger.clip(reason=reason)
            else:
                self.ledger.apply(CoverageEvent(
                    boundary=boundary,
                    reason=reason,
                    attempt_id=record.get("job_id"),
                    hr_state=_display_state(record.get("hr_state")),
                    br_state=_display_state(record.get("br_state")),
                    quiet_state=None,
                ))
            self._hr_reason = reason
            self._br_reason = reason

    def observe_attempt(self, executed: Any, disposition: BaselineDisposition) -> None:
        """Record values only after the attempt artifact has been made durable."""

        result = executed.result
        with self._lock:
            self._advance_to(disposition.application_boundary)
            application = self._pending_applications.pop(str(result.job_id), None)
            if application is not None:
                self.ledger.apply(CoverageEvent(
                    boundary=disposition.application_boundary,
                    reason=str(application.get("reason", disposition.reason)),
                    attempt_id=str(result.job_id),
                    hr_state=_display_state(application.get("hr_state")),
                    br_state=_display_state(application.get("br_state")),
                    quiet_state=_display_state(application.get("quiet_state")),
                ))
            self._hr_value = _finite_optional(disposition.display_hr_bpm)
            self._br_value = _finite_optional(disposition.display_br_bpm)
            self._hr_reason = disposition.reason
            self._br_reason = disposition.reason
            if (
                self.reference_timeline is None
                or not (disposition.hr_accepted or disposition.br_accepted)
            ):
                self._hr_reference_summary = None
                self._br_reference_summary = None
                return
            self._hr_reference_summary = self.reference_timeline.window_summary(
                int(result.frame_start), int(result.frame_stop), channel="hr"
            )
            self._br_reference_summary = self.reference_timeline.window_summary(
                int(result.frame_start), int(result.frame_stop), channel="br"
            )
            self._reference_audit.append({
                "job_id": str(result.job_id),
                "generation": int(disposition.generation),
                "frame_start": int(result.frame_start),
                "frame_stop": int(result.frame_stop),
                "hr_frame_start": int(result.frame_start),
                "br_frame_start": int(result.frame_start),
                "reference_source": asdict(self.reference_timeline.source),
                "hr": asdict(self._hr_reference_summary),
                "br": asdict(self._br_reference_summary),
            })

    def observe_snapshot(self, radar: BaselineSnapshot) -> None:
        """Advance and cache presentation state on the controller thread only."""

        if radar.generation != self.generation:
            raise ValueError("radar snapshot generation differs from composer")
        with self._lock:
            self._advance_to(radar.processed_boundary)
            self._published_monotonic_s = time.monotonic()
            self._latest_snapshot = self._build_snapshot(radar)

    def observe_advanced_snapshot(self, snapshot: Any) -> None:
        """Cache one motion-controller snapshot through the common view model."""

        self.observe_snapshot(_advanced_as_baseline(snapshot))

    def observe_durable_application(self, record: Mapping[str, Any]) -> None:
        """Apply a motion result only after its motion NPZ/index write succeeds."""

        boundary = record.get("application_boundary")
        if type(boundary) is not int:
            raise ValueError("application event needs an exact boundary")
        with self._lock:
            self._advance_to(boundary)
            quiet_state = None
            if record.get("quiet_accepted") is True:
                quiet_state = "accepted"
            elif self.ledger.current_states["quiet"] == "accepted":
                quiet_state = "held"
            hr_state = _display_state(record.get("hr_state"))
            br_state = _display_state(record.get("br_state"))
            reason = str(record.get("reason", "result_applied"))
            self.ledger.apply(CoverageEvent(
                boundary=boundary,
                reason=reason,
                attempt_id=record.get("job_id"),
                hr_state=hr_state,
                br_state=br_state,
                quiet_state=quiet_state,
            ))
            self._hr_value = _finite_optional(record.get("display_hr_bpm"))
            self._br_value = _finite_optional(record.get("display_br_bpm"))
            self._hr_reason = reason
            self._br_reason = reason
            accepted = record.get("hr_accepted") is True or record.get("br_accepted") is True
            if not accepted or self.reference_timeline is None:
                self._hr_reference_summary = None
                self._br_reference_summary = None
                return
            start = record.get("signal_frame_start")
            stop = record.get("signal_frame_stop")
            if type(start) is not int or type(stop) is not int or not 0 <= start < stop <= boundary:
                self._hr_reference_summary = None
                self._br_reference_summary = None
                return
            hr_start = record.get("hr_window_start", start)
            br_start = record.get("br_window_start", start)
            if (
                type(hr_start) is not int or type(br_start) is not int
                or not 0 <= hr_start < stop or not 0 <= br_start < stop
            ):
                self._hr_reference_summary = None
                self._br_reference_summary = None
                return
            self._hr_reference_summary = self.reference_timeline.window_summary(
                hr_start, stop, channel="hr"
            )
            self._br_reference_summary = self.reference_timeline.window_summary(
                br_start, stop, channel="br"
            )
            self._reference_audit.append({
                "job_id": str(record.get("job_id", "")),
                "generation": self.generation,
                "frame_start": start,
                "frame_stop": stop,
                "hr_frame_start": hr_start,
                "br_frame_start": br_start,
                "reference_source": asdict(self.reference_timeline.source),
                "hr": asdict(self._hr_reference_summary),
                "br": asdict(self._br_reference_summary),
            })

    @property
    def latest_snapshot(self) -> PlaybackSnapshot:
        with self._lock:
            return self._latest_snapshot

    def _build_snapshot(self, radar: BaselineSnapshot) -> PlaybackSnapshot:
        coverage = self.ledger.snapshot
        hr_state = coverage.hr_state
        br_state = coverage.br_state
        hr_value = self._hr_value
        br_value = self._br_value
        if hr_state == "missing":
            hr_value = None
        if br_state == "missing":
            br_value = None
        selected_range = (
            None
            if radar.selected_bin is None
            else bin_range_m(radar.selected_bin, self.spec.effective_config)
        )
        warnings_list = [
            "Development replay; coverage is availability, not accuracy.",
            "Masimo values are passive descriptive context and never affect radar.",
            f"Frame validity provenance: {self.spec.validity_provenance}.",
        ]
        if self.spec.time_origin_approximate:
            warnings_list.append(
                "Reference alignment uses an approximate capture time origin."
            )
        if self.mode == "baseline":
            warnings_list.append(
                "Advanced recovery is unavailable without compatible accepted "
                "physical calibration; the tracked configuration is disabled and "
                "its nonzero range bias is incompatible with this zero-bias capture."
            )
        warnings = tuple(warnings_list)
        return PlaybackSnapshot(
            generation=radar.generation,
            processed_boundary=radar.processed_boundary,
            total_frames=self.frame_limit,
            playback_state=radar.playback_state,
            speed=radar.speed,
            mode=self.mode,
            hr=DisplayChannel(hr_value, hr_state, self._hr_reason, radar.hr.attempt_id),
            br=DisplayChannel(br_value, br_state, self._br_reason, radar.br.attempt_id),
            quiet_state=coverage.quiet_state,
            selected_bin=radar.selected_bin,
            selection_revision=radar.selection_revision,
            accumulation_stage=radar.accumulation_stage,
            recovery_reason=radar.hr.reason or radar.br.reason,
            measurement_window_age_frames=radar.measurement_window_age_frames,
            analysis_lag_frames=radar.analysis_lag_frames,
            reference=self._last_reference,
            hr_reference_summary=self._hr_reference_summary,
            br_reference_summary=self._br_reference_summary,
            coverage=coverage,
            warning_messages=warnings,
            selected_range_m=selected_range,
            trace_samples=tuple(self._trace),
            published_monotonic_s=self._published_monotonic_s,
        )

    def finalize_payload(
        self,
    ) -> tuple[
        dict[str, Any],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, Any],
    ]:
        with self._lock:
            coverage = self.ledger.snapshot
            intervals = [asdict(row) for row in self.ledger.finalized_intervals()]
            summary = {
                "generation": self.generation,
                "mode": self.mode,
                "processed_frames": coverage.denominator_frames,
                "frame_limit": self.frame_limit,
                "frame_rate_hz": self.spec.frame_rate_hz,
                "duration_s": coverage.denominator_frames / self.spec.frame_rate_hz,
                "completion_state": self._latest_snapshot.playback_state,
                "coverage": asdict(coverage),
                "coverage_intervals_half_open": True,
                "reference_is_passive": True,
            }
            display_audit = {
                "schema": "replay_reference_display_audit_v1",
                "reference_source": (
                    None
                    if self.reference_timeline is None
                    else asdict(self.reference_timeline.source)
                ),
                "frames": list(self._reference_display_frames),
            }
            return summary, intervals, list(self._reference_audit), display_audit

    def _advance_to(self, boundary: int) -> None:
        if boundary < self.ledger.processed_boundary:
            raise ValueError("composer boundary cannot move backward")
        if boundary > self.frame_limit:
            raise ValueError("composer boundary exceeds authorized frame limit")
        while self.ledger.processed_boundary < boundary:
            frame_index = self.ledger.processed_boundary
            reference = (
                self.reference_timeline.lookup_frame(frame_index)
                if self.reference_timeline is not None else None
            )
            display_reference = (
                reference
                if reference is not None
                else ReferenceValue(None, None, None, None, False, False, False)
            )
            self._reference_display_frames.append({
                "frame_index": frame_index,
                "display_boundary": frame_index + 1,
                **asdict(display_reference),
            })
            states_before = self.ledger.current_states
            self._trace.append(DisplayTraceSample(
                boundary=frame_index + 1,
                generation=self.generation,
                radar_hr_bpm=self._hr_value,
                hr_state=states_before["hr"],
                radar_br_bpm=self._br_value,
                br_state=states_before["br"],
                reference_pr_bpm=None if reference is None else reference.pr_bpm,
                reference_rr_bpm=None if reference is None else reference.rr_bpm,
                reference_available=bool(
                    reference is not None
                    and (reference.pr_present or reference.rr_present)
                ),
            ))
            self.ledger.advance_frame(
                reference_pr_present=bool(reference and reference.pr_present),
                reference_pr_qualified=bool(reference and reference.pr_qualified),
                reference_rr_present=bool(reference and reference.rr_present),
            )
            self._last_reference = reference
            states_after = self.ledger.current_states
            if states_before["hr"] == "accepted" and states_after["hr"] == "held":
                self._hr_reason = "display_lease_expired"
            if states_before["br"] == "accepted" and states_after["br"] == "held":
                self._br_reason = "display_lease_expired"


class _ComposingEvidenceWriter:
    """Delegate durable writes, then update presentation-only composition."""

    def __init__(self, writer: ReplayEvidenceWriter, composer: ReplaySnapshotComposer):
        self.writer = writer
        self.composer = composer

    @property
    def pass_dir(self) -> Path:
        return self.writer.pass_dir

    def write_controller_event(self, event: Any) -> None:
        self.writer.write_controller_event(event)
        self.composer.observe_controller_event(event)

    def write_attempt(self, executed: Any, disposition: BaselineDisposition) -> Any:
        record = self.writer.write_attempt(executed, disposition)
        self.composer.observe_attempt(executed, disposition)
        return record

    def write_playback_event(self, event: Mapping[str, Any]) -> None:
        self.writer.write_playback_event(event)

    def write_performance(self, event: Mapping[str, Any]) -> None:
        self.writer.write_performance(event)

    def finalize(self, *, source_integrity: Mapping[str, Any] | None = None) -> Path:
        summary, intervals, reference_audit, reference_display_audit = (
            self.composer.finalize_payload()
        )
        summary["source_integrity"] = (
            None if source_integrity is None else dict(source_integrity)
        )
        return self.writer.finalize(
            summary=summary,
            display_intervals=intervals,
            reference_audit=reference_audit,
            reference_display_audit=reference_display_audit,
        )


class AdvancedReplayCompositionSink:
    """Replay sidecar for motion mode, separate from motion attempt evidence."""

    def __init__(self, pass_dir: Path):
        self.pass_dir = Path(pass_dir)
        self.events_path = self.pass_dir / "replay_controller_events.jsonl"
        self.playback_path = self.pass_dir / "replay_playback_events.jsonl"
        self.performance_path = self.pass_dir / "replay_performance.jsonl"
        self.composer: ReplaySnapshotComposer | None = None

    def attach_composer(self, composer: ReplaySnapshotComposer) -> None:
        if self.composer is not None:
            raise RuntimeError("advanced replay composer is already attached")
        self.composer = composer

    def write_controller_event(self, event: Any) -> None:
        record = asdict(event) if is_dataclass(event) else dict(event)
        if (
            "application_boundary" not in record
            and type(record.get("frame_index")) is int
        ):
            # Motion controller events are emitted after frame n is observed;
            # their public application boundary is therefore n+1.
            record["application_boundary"] = int(record["frame_index"]) + 1
        _append_jsonl(self.events_path, record)
        if self.composer is None:
            raise RuntimeError("advanced replay composer is not attached")
        if record.get("kind") == "result_applied":
            self.composer.observe_durable_application(record)
        else:
            self.composer.observe_controller_event(record)

    def write_application_event(self, event: Mapping[str, Any]) -> None:
        self.write_controller_event(event)

    def write_playback_event(self, event: Mapping[str, Any]) -> None:
        _append_jsonl(self.playback_path, dict(event))

    def write_performance(self, event: Mapping[str, Any]) -> None:
        _append_jsonl(self.performance_path, dict(event))

    def observe_snapshot(self, snapshot: Any) -> None:
        if self.composer is None:
            raise RuntimeError("advanced replay composer is not attached")
        self.composer.observe_advanced_snapshot(snapshot)

    def finalize(self, *, source_integrity: Mapping[str, Any] | None) -> Path:
        if self.composer is None:
            raise RuntimeError("advanced replay composer is not attached")
        summary, intervals, reference_audit, reference_display_audit = (
            self.composer.finalize_payload()
        )
        summary["source_integrity"] = (
            None if source_integrity is None else dict(source_integrity)
        )
        _write_json(self.pass_dir / "summary.json", summary)
        _write_json(self.pass_dir / "display_state_intervals.json", intervals)
        _write_json(self.pass_dir / "reference_audit.json", reference_audit)
        _write_json(
            self.pass_dir / "reference_display_audit.json",
            reference_display_audit,
        )
        for path in (self.events_path, self.playback_path, self.performance_path):
            path.touch(exist_ok=True)
        files = {
            path.relative_to(self.pass_dir).as_posix(): _file_sha256(path)
            for path in sorted(self.pass_dir.rglob("*"))
            if path.is_file() and path.name != "manifest.json"
        }
        manifest = self.pass_dir / "manifest.json"
        _write_json(manifest, {
            "schema": "replay_compare_motion_outer_v1",
            "files": files,
        })
        return manifest


class ReplayApplicationManager:
    """Own replay pass lifecycles behind the nonblocking dashboard protocol."""

    def __init__(
        self,
        spec: ReplaySessionSpec,
        *,
        output_root: str | Path,
        frame_limit: int,
        initial_speed: float,
        reference_timeline: PassiveReferenceTimeline | None,
        source_factory: SourceFactory,
        analysis_function: AnalysisFunction | None,
        clock_factory: ClockFactory,
        mode: str = "baseline",
        initial_generation: int = 1,
        shutdown_timeout_s: float = 5.0,
    ):
        if mode != "baseline":
            raise ValueError("baseline application manager accepts only baseline mode")
        if type(frame_limit) is not int:
            raise TypeError("frame_limit must be an exact integer")
        if type(initial_generation) is not int or initial_generation <= 0:
            raise ValueError("initial_generation must be a positive exact integer")
        if (
            isinstance(initial_speed, bool)
            or not isinstance(initial_speed, (int, float))
            or not math.isfinite(float(initial_speed))
            or float(initial_speed) not in {0.5, 1.0, 2.0, 4.0}
        ):
            raise ValueError("initial_speed must be one of 0.5, 1, 2, or 4")
        if (
            isinstance(shutdown_timeout_s, bool)
            or not isinstance(shutdown_timeout_s, (int, float))
            or not math.isfinite(float(shutdown_timeout_s))
            or float(shutdown_timeout_s) <= 0.0
        ):
            raise ValueError("shutdown_timeout_s must be finite and positive")
        self.spec = spec
        self.output_root = Path(output_root)
        self.frame_limit = frame_limit
        if not 1 <= self.frame_limit <= spec.frame_count:
            raise ValueError("frame_limit lies outside the authorized recording")
        self.initial_speed = float(initial_speed)
        self.reference_timeline = reference_timeline
        self.source_factory = source_factory
        self.analysis_function = analysis_function
        self.clock_factory = clock_factory
        self.mode = mode
        self.shutdown_timeout_s = float(shutdown_timeout_s)
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._transition: threading.Thread | None = None
        self._stop_thread: threading.Thread | None = None
        self._stopping = False
        self._transition_error: BaseException | None = None
        self._run = self._build_run(initial_generation, self.initial_speed)

    @property
    def latest_snapshot(self) -> PlaybackSnapshot:
        with self._lock:
            run = self._run
        return run.evidence.composer.latest_snapshot

    @property
    def generation(self) -> int:
        with self._lock:
            return self._run.generation

    @property
    def pass_dir(self) -> Path:
        with self._lock:
            return self._run.pass_dir

    @property
    def current_run(self) -> ReplayRuntime:
        with self._lock:
            return self._run.runtime

    @property
    def ended(self) -> bool:
        with self._lock:
            transition_active = (
                self._transition is not None and self._transition.is_alive()
            )
            return self._run.runtime.ended and not transition_active

    @property
    def clean_shutdown(self) -> bool:
        with self._lock:
            run = self._run
            transition_active = (
                self._transition is not None and self._transition.is_alive()
            )
            return (
                not transition_active
                and run.finalized
                and run.runtime.clean_shutdown
                and run.finalize_error is None
                and self._transition_error is None
            )

    @property
    def restart_in_progress(self) -> bool:
        with self._lock:
            return self._transition is not None and self._transition.is_alive()

    @property
    def stopping(self) -> bool:
        with self._lock:
            return self._stopping

    @property
    def failure(self) -> BaseException | None:
        with self._lock:
            run = self._run
            return self._transition_error or run.runtime.failure or run.finalize_error

    def start(self) -> None:
        with self._lock:
            run = self._run
        run.runtime.start()
        monitor = threading.Thread(
            target=self._monitor_run,
            args=(run,),
            name=f"ReplayFinalize-g{run.generation}",
            daemon=True,
        )
        run.monitor = monitor
        monitor.start()

    def request_pause(self) -> bool:
        """Queue pause for one stable generation, or reject during transition."""

        with self._lock:
            if self._control_transition_active_locked():
                return False
            self._run.runtime.request_pause()
            return True

    def resume(self) -> bool:
        """Queue resume for one stable generation, or reject during transition."""

        with self._lock:
            if self._control_transition_active_locked():
                return False
            self._run.runtime.resume()
            return True

    def set_speed(self, speed: float) -> bool:
        """Queue speed for one stable generation, or reject during transition."""

        with self._lock:
            if self._control_transition_active_locked():
                return False
            self._run.runtime.set_speed(speed)
            return True

    def _control_transition_active_locked(self) -> bool:
        return (
            self._stopping
            or self._run.runtime.ended
            or (self._transition is not None and self._transition.is_alive())
        )

    def _record_lifecycle_failure_locked(self, exc: BaseException) -> None:
        """Preserve the first causal lifecycle failure for operator diagnosis."""

        if self._transition_error is None:
            self._transition_error = exc

    def restart(self) -> bool:
        """Request a clean background restart; generation changes on success."""

        with self._lock:
            if self._stopping:
                return False
            if self._transition is not None and self._transition.is_alive():
                return False
            previous = self._run
            transition = threading.Thread(
                target=self._restart_worker,
                args=(previous,),
                name="ReplayRestart",
                daemon=True,
            )
            self._transition = transition
            transition.start()
        return True

    def request_stop(self) -> None:
        """Request stop without blocking the Qt main thread."""

        with self._lock:
            if self._stop_thread is not None and self._stop_thread.is_alive():
                return
            self._stopping = True
            stop_thread = threading.Thread(
                target=self._stop_worker,
                name="ReplayStop",
                daemon=True,
            )
            self._stop_thread = stop_thread
            stop_thread.start()

    def stop(self, timeout_s: float | None = None) -> None:
        with self._lock:
            self._stopping = True
            run = self._run
            transition = self._transition
        run.runtime.stop(timeout_s=timeout_s)
        wait_s = self.shutdown_timeout_s + 1.0 if timeout_s is None else timeout_s
        if transition is not None and transition is not threading.current_thread():
            transition.join(timeout=wait_s)
            if transition.is_alive():
                with self._lock:
                    self._record_lifecycle_failure_locked(RuntimeError(
                        "replay transition did not stop within the shutdown timeout"
                    ))
        with self._lock:
            latest = self._run
        if latest is not run:
            latest.runtime.stop(timeout_s=timeout_s)
        if run.monitor is not None:
            run.monitor.join(timeout=wait_s)
        if latest is not run and latest.monitor is not None:
            latest.monitor.join(timeout=wait_s)

    def _restart_worker(self, previous: _RunState) -> None:
        next_run: _RunState | None = None
        try:
            previous.runtime.stop(timeout_s=self.shutdown_timeout_s + 1.0)
            if previous.monitor is not None:
                previous.monitor.join(timeout=self.shutdown_timeout_s + 1.0)
            if (
                not previous.runtime.clean_shutdown
                or not previous.finalized
                or previous.finalize_error is not None
            ):
                raise RuntimeError(
                    "previous replay pass did not finalize cleanly; restart blocked"
                )
            with self._lock:
                if self._stopping or self._run is not previous:
                    return
            next_run = self._build_run(previous.generation + 1, self.initial_speed)
            # Spawn/config readiness may take seconds.  Complete it without the
            # manager lock so dashboard reads and Stop remain responsive.  No
            # worker/controller thread or ADC source is opened at this stage.
            next_run.runtime.prepare_analysis_backend()
            cancelled_after_prepare = False
            with self._lock:
                if self._stopping or self._run is not previous:
                    next_run.evidence.writer.write_playback_event({
                        "kind": "restart_cancelled_before_start",
                        "boundary": 0,
                        "generation": next_run.generation,
                    })
                    # The new pass never opened or verified the registered ADC.
                    # Leave it explicitly incomplete instead of creating a
                    # manifest that could be mistaken for a scientific replay.
                    self._record_lifecycle_failure_locked(RuntimeError(
                        "restart was cancelled after constructing an unopened pass"
                    ))
                    cancelled_after_prepare = True
                else:
                    # Child readiness is complete, so this locked start only
                    # publishes the generation and starts local threads.
                    next_run.runtime.start()
                    self._run = next_run
            if cancelled_after_prepare:
                next_run.runtime.stop(timeout_s=self.shutdown_timeout_s)
                return
            monitor = threading.Thread(
                target=self._monitor_run,
                args=(next_run,),
                name=f"ReplayFinalize-g{next_run.generation}",
                daemon=True,
            )
            next_run.monitor = monitor
            monitor.start()
        except BaseException as exc:
            if next_run is not None:
                try:
                    next_run.runtime.stop(timeout_s=self.shutdown_timeout_s + 1.0)
                except BaseException:
                    pass
            with self._lock:
                self._record_lifecycle_failure_locked(exc)

    def _stop_worker(self) -> None:
        try:
            with self._lock:
                run = self._run
                transition = self._transition
            run.runtime.stop(timeout_s=self.shutdown_timeout_s + 1.0)
            if transition is not None and transition is not threading.current_thread():
                transition.join(timeout=self.shutdown_timeout_s + 1.0)
                if transition.is_alive():
                    raise RuntimeError(
                        "replay transition did not stop within the shutdown timeout"
                    )
            with self._lock:
                latest = self._run
            if latest is not run:
                latest.runtime.stop(timeout_s=self.shutdown_timeout_s + 1.0)
        except BaseException as exc:
            with self._lock:
                self._record_lifecycle_failure_locked(exc)

    def _monitor_run(self, run: _RunState) -> None:
        while not run.runtime.ended:
            time.sleep(0.02)
        run.runtime.stop(timeout_s=self.shutdown_timeout_s + 1.0)
        try:
            integrity = getattr(run.runtime.source, "integrity", None)
            run.evidence.finalize(
                source_integrity=None if integrity is None else asdict(integrity)
            )
            run.finalized = True
        except BaseException as exc:
            run.finalize_error = exc

    def _build_run(self, generation: int, speed: float) -> _RunState:
        pass_dir = self.output_root / f"pass_{generation:04d}"
        if pass_dir.exists():
            raise FileExistsError(f"replay pass directory already exists: {pass_dir}")
        config = self.spec.effective_config
        metadata = {
            "capture_id": self.spec.capture_id,
            "capture_key": self.spec.capture_key,
            "source_kind": self.spec.source_kind,
            "registered_adc_sha256": self.spec.registered_adc_sha256,
            "metadata_sha256": self.spec.metadata_sha256,
            "warmup_sha256": self.spec.warmup_sha256,
            "original_config_sha256": self.spec.original_config_sha256,
            "effective_config_sha256": self.spec.effective_config_sha256,
            "effective_config": config,
            "generation": generation,
            "mode": self.mode,
            "frame_limit": self.frame_limit,
            "frame_rate_hz": self.spec.frame_rate_hz,
            "validity_provenance": self.spec.validity_provenance,
            "validity_assumed": self.spec.validity_assumed,
            "reference_is_passive": True,
            "reference_source": (
                None
                if self.reference_timeline is None
                else asdict(self.reference_timeline.source)
            ),
            "source_commit": _source_commit(),
            "scientific_source_status": scientific_source_status(),
            "random_seed": None,
            "randomness": "none",
            "anchor_utc_us": self.spec.anchor_utc_us,
            "time_origin_id": self.spec.time_origin_id,
            "time_origin_approximate": self.spec.time_origin_approximate,
            "geometry": {
                "num_adc_samples": self.spec.num_adc_samples,
                "num_rx": self.spec.num_rx,
                "num_chirps_per_frame": self.spec.num_chirps_per_frame,
                "frame_bytes": self.spec.frame_bytes,
                "iq_swap": self.spec.iq_swap,
                "range_resolution_m": self.spec.range_resolution_m,
                "range_bias_m": self.spec.range_bias_m,
            },
        }
        controller = BaselineController(config, generation=generation, speed=speed)
        composer = ReplaySnapshotComposer(
            self.spec,
            generation=generation,
            initial_snapshot=controller.snapshot,
            frame_limit=self.frame_limit,
            mode=self.mode,
            reference_timeline=self.reference_timeline,
        )
        writer = ReplayEvidenceWriter(pass_dir, metadata)
        evidence = _ComposingEvidenceWriter(writer, composer)
        runtime = ReplayRuntime(
            source=self.source_factory(),
            controller=controller,
            evidence=evidence,  # type: ignore[arg-type]
            analysis_function=self.analysis_function,
            process_backend=(
                SpawnBaselineBackend(config, self.spec.effective_config_sha256)
                if self.analysis_function is None
                else None
            ),
            frame_limit=self.frame_limit,
            clock=self.clock_factory(self.spec.frame_rate_hz, speed),
            shutdown_timeout_s=self.shutdown_timeout_s,
            snapshot_observer=composer.observe_snapshot,
        )
        return _RunState(generation, pass_dir, runtime, evidence)


def create_replay_application(
    spec: ReplaySessionSpec,
    *,
    output_root: str | Path,
    reference_timeline: PassiveReferenceTimeline | None = None,
    frame_limit: int | None = None,
    initial_speed: float = 1.0,
    source_factory: SourceFactory | None = None,
    analysis_function: AnalysisFunction | None = None,
    clock_factory: ClockFactory | None = None,
    initial_generation: int = 1,
    shutdown_timeout_s: float = 5.0,
) -> ReplayApplicationManager:
    """Create a baseline application from an already-authorized session.

    Injection points exist for deterministic tests.  The production defaults
    remain bound to the registered ADC and unchanged production decoder/DSP.
    """

    config = spec.effective_config
    if source_factory is None:
        def source_factory() -> SequentialAdcSource:
            return SequentialAdcSource(
                spec.adc_path,
                ProductionFrameDecoder(config["profile"]),
                expected_sha256=spec.registered_adc_sha256,
                expected_frame_count=spec.frame_count,
            )
    if clock_factory is None:
        clock_factory = PlaybackClock
    return ReplayApplicationManager(
        spec,
        output_root=output_root,
        frame_limit=spec.frame_count if frame_limit is None else frame_limit,
        initial_speed=initial_speed,
        reference_timeline=reference_timeline,
        source_factory=source_factory,
        analysis_function=analysis_function,
        clock_factory=clock_factory,
        initial_generation=initial_generation,
        shutdown_timeout_s=shutdown_timeout_s,
    )


def _finite_optional(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _display_state(value: Any) -> str | None:
    if value is None:
        return None
    state = str(value)
    return "accepted" if state == "fresh" else state


def _advanced_as_baseline(snapshot: Any) -> BaselineSnapshot:
    display = snapshot.display
    return BaselineSnapshot(
        generation=int(snapshot.generation),
        epoch=int(display.epoch),
        processed_boundary=int(snapshot.processed_boundary),
        playback_state=str(snapshot.playback_state),
        speed=float(snapshot.speed),
        selected_bin=display.locked_bin,
        selection_revision=int(display.selection_revision),
        accumulation_stage=str(display.status),
        hr=BaselineChannel(
            _finite_optional(display.hr.value_bpm),
            _display_state(display.hr.state) or "missing",
            str(display.reason),
        ),
        br=BaselineChannel(
            _finite_optional(display.br.value_bpm),
            _display_state(display.br.state) or "missing",
            str(display.reason),
        ),
        measurement_window_age_frames=snapshot.measurement_window_age_frames,
        analysis_lag_frames=snapshot.analysis_lag_frames,
    )


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(
            _json_safe(value), sort_keys=True, separators=(",", ":"), allow_nan=False
        ) + "\n")
        handle.flush()


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(_json_safe(value), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except ValueError:
            pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _source_commit() -> str:
    """Record the active software authority without inspecting other checkouts."""

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("cannot identify replay software commit") from exc
    commit = completed.stdout.strip().lower()
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise RuntimeError("git returned an invalid replay software commit")
    return commit


def scientific_source_status() -> str:
    """Return exact Git status for every tracked scientific input to replay."""
    try:
        completed = subprocess.run(
            [
                "git", "status", "--porcelain", "--untracked-files=all", "--",
                "src", "scripts", "experiments", "reference_registry",
                "environment.yml",
            ],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("cannot inspect replay scientific source status") from exc
    return completed.stdout
