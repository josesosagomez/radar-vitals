"""Bounded replay controller and baseline analysis worker."""

from __future__ import annotations

import collections
import math
import queue
import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Callable, Literal

import numpy as np

from src.live_motion.config import LiveMotionSettings
from src.live_motion.runtime import ExecutedAnalysis, production_analysis_function
from src.live_motion.scheduler import (
    AnalysisJob,
    AnalysisResult,
    BoundedAnalysisScheduler,
)
from src.warmup_select import derive_candidate_bins

from .clock import PlaybackClock
from .evidence import ReplayEvidenceWriter
from .session import ReplaySessionSpec
from .source import SequentialAdcSource


@dataclass(frozen=True)
class BaselineChannel:
    value_bpm: float | None
    state: Literal["accepted", "held", "missing"]
    reason: str
    attempt_id: str | None = None


@dataclass(frozen=True)
class BaselineSnapshot:
    generation: int
    epoch: int
    processed_boundary: int
    playback_state: str
    speed: float
    selected_bin: int | None
    selection_revision: int
    accumulation_stage: str
    hr: BaselineChannel
    br: BaselineChannel
    measurement_window_age_frames: int | None
    analysis_lag_frames: int | None


@dataclass(frozen=True)
class ReplayControllerEvent:
    kind: str
    reason: str
    boundary: int
    epoch: int
    generation: int
    job_id: str | None = None
    selected_bin: int | None = None
    selection_revision: int | None = None
    hr_state: str | None = None
    br_state: str | None = None
    frame_start: int | None = None
    frame_stop: int | None = None
    requested_bin: int | None = None
    selection_mode: str | None = None
    requested_selection_revision: int | None = None
    replacement_job_id: str | None = None
    disposition: str | None = None
    hr_accepted: bool | None = None
    br_accepted: bool | None = None


@dataclass(frozen=True)
class BaselineDisposition:
    generation: int
    application_boundary: int
    selection_revision: int
    disposition: str
    reason: str
    hr_accepted: bool
    br_accepted: bool
    quiet_accepted: bool
    display_hr_bpm: float | None
    display_hr_state: str
    display_br_bpm: float | None
    display_br_state: str
    request_epoch: int = 0
    request_frame_start: int = 0
    request_frame_stop: int = 0
    requested_bin: int | None = None
    selection_mode: str = ""
    requested_selection_revision: int = 0
    application_epoch: int = 0


@dataclass(frozen=True)
class BaselineUpdate:
    snapshot: BaselineSnapshot
    events: tuple[ReplayControllerEvent, ...] = ()
    disposition: BaselineDisposition | None = None
    cancelled_job_ids: tuple[str, ...] = ()


def baseline_analysis_function(
    config: dict[str, Any],
) -> Callable[[AnalysisJob], ExecutedAnalysis]:
    """Build production analysis with a private, calibration-free baseline contract."""

    frame_rate_hz = float(config["session"]["frame_rate_hz"])
    settings = LiveMotionSettings(
        enabled=False,
        unavailable_reason="baseline replay does not use motion calibration",
        frame_rate_hz=frame_rate_hz,
        preview_frames=(200, 400),
        ordinary_window_frames=600,
        ordinary_hop_frames=60,
        monitor_window_frames=20,
        monitor_hop_frames=5,
        stillness_frames=60,
        extended_breathing_enabled=False,
        extended_breathing_window_frames=1200,
        extended_breathing_band_hz=(0.05, 0.50),
        candidate_bins=tuple(derive_candidate_bins(config)),
        presence_thresholds=None,
        movement_thresholds=None,
        calibration_record=None,
        calibration_record_path=None,
        calibration_record_sha256=None,
    )
    if not settings.candidate_bins:
        raise ValueError("baseline candidate-bin gate is empty")
    return production_analysis_function(config, settings)


class BaselineController:
    """Own baseline frame history, selection, admission, and publication state."""

    ordinary_window_frames = 600
    ordinary_hop_frames = 60

    def __init__(
        self,
        config: dict[str, Any],
        *,
        generation: int = 1,
        speed: float = 1.0,
    ):
        self.config = config
        try:
            self._candidate_bins: set[int] | None = set(derive_candidate_bins(config))
        except (KeyError, TypeError, ValueError):
            self._candidate_bins = None
        self.generation = int(generation)
        self.speed = float(speed)
        self.epoch = 0
        self.selection_revision = 0
        self.selected_bin: int | None = None
        self.processed_boundary = 0
        self.playback_state = "running"
        self._frames: collections.deque[np.ndarray] = collections.deque(
            maxlen=self.ordinary_window_frames
        )
        self._hr_history: collections.deque[float] = collections.deque(maxlen=5)
        self._hr = BaselineChannel(None, "missing", "initial_accumulation")
        self._br = BaselineChannel(None, "missing", "initial_accumulation")
        self._scheduler = BoundedAnalysisScheduler()
        self._pending_job_ref: AnalysisJob | None = None
        self._last_request_boundary: int | None = None
        self._job_sequence = 0
        self._last_window_stop: int | None = None
        self._last_analysis_lag: int | None = None
        self._complete = False

    @property
    def snapshot(self) -> BaselineSnapshot:
        if len(self._frames) < self.ordinary_window_frames:
            stage = f"accumulating_{len(self._frames)}_of_{self.ordinary_window_frames}"
        elif self.selected_bin is None:
            stage = "selecting_range_bin"
        else:
            stage = "ordinary_tracking"
        age = (
            None if self._last_window_stop is None
            else self.processed_boundary - self._last_window_stop
        )
        return BaselineSnapshot(
            generation=self.generation,
            epoch=self.epoch,
            processed_boundary=self.processed_boundary,
            playback_state=self.playback_state,
            speed=self.speed,
            selected_bin=self.selected_bin,
            selection_revision=self.selection_revision,
            accumulation_stage=stage,
            hr=self._hr,
            br=self._br,
            measurement_window_age_frames=age,
            analysis_lag_frames=self._last_analysis_lag,
        )

    @property
    def active_job(self) -> AnalysisJob | None:
        return self._scheduler.active

    @property
    def pending_job(self) -> AnalysisJob | None:
        return self._pending_job_ref

    def set_playback_state(self, state: str) -> BaselineUpdate:
        if state not in {"running", "pausing", "paused", "stopped", "complete", "failed"}:
            raise ValueError(f"unknown playback state {state!r}")
        self.playback_state = state
        return BaselineUpdate(self.snapshot)

    def set_speed(self, speed: float) -> BaselineUpdate:
        self.speed = float(speed)
        return BaselineUpdate(self.snapshot)

    def handle_frame(
        self, frame_index: int, cube: np.ndarray, *, valid: bool = True
    ) -> BaselineUpdate:
        events: list[ReplayControllerEvent] = []
        cancelled: list[str] = []
        if self._complete:
            raise RuntimeError("cannot advance a completed replay pass")
        if type(frame_index) is not int or frame_index != self.processed_boundary:
            events.extend(self._reset_for_integrity("frame_index_discontinuity"))
            raise ValueError(
                f"expected frame {self.processed_boundary}, received {frame_index}"
            )
        raw = np.asarray(cube)
        if raw.ndim != 3 or not valid:
            reason = "source_frame_invalid" if not valid else "malformed_frame_geometry"
            events.extend(self._reset_for_integrity(reason))
            self.processed_boundary += 1
            return BaselineUpdate(self.snapshot, tuple(events))
        self._frames.append(np.array(raw, copy=True))
        self.processed_boundary += 1

        at_boundary = (
            len(self._frames) == self.ordinary_window_frames
            and (
                self._last_request_boundary is None
                or self.processed_boundary - self._last_request_boundary
                >= self.ordinary_hop_frames
            )
        )
        if at_boundary:
            job = self._build_job()
            mandatory_pending, _rolling_pending = self._scheduler.pending_counts
            if job.selection_mode == "select" and mandatory_pending:
                # The production scheduler intentionally never replaces mandatory
                # motion milestones. Baseline has only one selector retry and no
                # active job here, so replace the whole unopened wrapper safely.
                assert self._scheduler.active is None
                old = self._pending_job_ref
                self._scheduler.close("newer_selector_retry")
                self._scheduler = BoundedAnalysisScheduler()
                self._scheduler.submit(job)
                if old is not None:
                    cancelled.append(old.job_id)
                    events.append(self._event(
                        "cancelled_before_execution", "newer_ordinary_boundary",
                        old.job_id, replacement_job_id=job.job_id,
                    ))
                self._pending_job_ref = job
                events.append(self._request_event(job))
            else:
                scheduler_events = self._scheduler.submit(job)
                for scheduler_event in scheduler_events:
                    cancelled.append(scheduler_event.job_id)
                    event_kind = (
                        "cancelled_before_execution"
                        if scheduler_event.kind == "rolling_coalesced"
                        else scheduler_event.kind
                    )
                    events.append(self._event(
                        event_kind, scheduler_event.reason,
                        scheduler_event.job_id,
                        replacement_job_id=scheduler_event.replacement_job_id,
                    ))
                self._pending_job_ref = job
                events.append(self._request_event(job))
            self._last_request_boundary = self.processed_boundary
        return BaselineUpdate(self.snapshot, tuple(events), cancelled_job_ids=tuple(cancelled))

    def claim_pending_job(self) -> AnalysisJob | None:
        if self._scheduler.active is not None or self.playback_state != "running":
            return None
        job = self._scheduler.claim_next()
        if job is not None and self._pending_job_ref is not None:
            if job.job_id == self._pending_job_ref.job_id:
                self._pending_job_ref = None
        return job

    def register_dispatched_job(self, job: AnalysisJob) -> None:
        """Confirm the one claimed job was handed to the bounded worker."""
        if self._scheduler.active is None or self._scheduler.active.job_id != job.job_id:
            raise ValueError("dispatched job does not match the claimed baseline job")

    def handle_executed(self, executed: ExecutedAnalysis) -> BaselineUpdate:
        result = executed.result
        active = self._scheduler.active
        if active is None or result.job_id != active.job_id:
            raise ValueError("analysis result does not match the active baseline job")
        requested = active
        self._scheduler.complete(result)
        self._last_window_stop = result.frame_stop
        self._last_analysis_lag = self.processed_boundary - result.frame_stop

        stale_reason = self._stale_reason(result, requested)
        if stale_reason:
            disposition = self._held_disposition("expired", stale_reason)
            disposition = self._attach_request(disposition, requested)
            event = self._application_event(result.job_id, disposition)
            return BaselineUpdate(self.snapshot, (event,), disposition)

        selection_fallback = False
        if requested.selection_mode == "select":
            decision = result.selection_decision
            if (
                result.status != "completed"
                or decision is None
                or decision.selector_succeeded is not True
                or decision.dsp_succeeded is not True
                or decision.selected_bin is None
            ):
                reason = result.error or (
                    "selector_no_verified_dsp" if decision is not None
                    else "selector_failed"
                )
                disposition = self._held_disposition("rejected", reason)
                disposition = self._attach_request(disposition, requested)
                event = self._application_event(result.job_id, disposition)
                return BaselineUpdate(self.snapshot, (event,), disposition)
            if result.actual_bin != int(decision.selected_bin):
                disposition = self._held_disposition(
                    "rejected", "selector_actual_bin_mismatch"
                )
                disposition = self._attach_request(disposition, requested)
                event = self._application_event(result.job_id, disposition)
                return BaselineUpdate(self.snapshot, (event,), disposition)
            if (
                self._candidate_bins is not None
                and int(decision.selected_bin) not in self._candidate_bins
            ):
                disposition = self._held_disposition(
                    "rejected", "selector_bin_outside_candidate_gate"
                )
                disposition = self._attach_request(disposition, requested)
                event = self._application_event(result.job_id, disposition)
                return BaselineUpdate(self.snapshot, (event,), disposition)
            self.selected_bin = int(decision.selected_bin)
            self.selection_revision += 1
            selection_fallback = bool(decision.fallback_used)
            cancelled_after_selection = self._scheduler.cancel_epoch(
                self.epoch, "selection_committed"
            )
            self._pending_job_ref = None
        else:
            cancelled_after_selection = ()

        if result.status != "completed" or result.dsp is None:
            disposition = self._held_disposition(
                "failed", result.error or "analysis_returned_no_dsp"
            )
        elif selection_fallback:
            # A winner outside the settled-energy eligibility pool may establish a
            # provisional fixed bin, but its selection-window values are not promoted.
            disposition = self._held_disposition(
                "rejected", "selection_energy_fallback_not_presentation_eligible"
            )
        else:
            disposition = self._publish_dsp(result)
        disposition = self._attach_request(disposition, requested)
        event = self._application_event(result.job_id, disposition)
        cancellation_events = tuple(
            self._event(item.kind, item.reason, item.job_id)
            for item in cancelled_after_selection
        )
        cancelled_ids = tuple(
            item.job_id for item in cancelled_after_selection
            if item.kind == "cancelled_before_execution"
        )
        return BaselineUpdate(
            self.snapshot, cancellation_events + (event,), disposition,
            cancelled_job_ids=cancelled_ids,
        )

    def cancel_pending(self, reason: str) -> BaselineUpdate:
        scheduler_events = self._scheduler.close(reason)
        self._pending_job_ref = None
        events = tuple(
            self._event(event.kind, event.reason, event.job_id)
            for event in scheduler_events
        )
        cancelled = tuple(
            event.job_id for event in scheduler_events
            if event.kind == "cancelled_before_execution"
        )
        return BaselineUpdate(self.snapshot, events, cancelled_job_ids=cancelled)

    def finish(self, state: str = "complete") -> BaselineUpdate:
        self._complete = True
        self.playback_state = state
        return self.cancel_pending("playback_finished")

    def _build_job(self) -> AnalysisJob:
        stop = self.processed_boundary
        start = stop - self.ordinary_window_frames
        self._job_sequence += 1
        selection_mode = "select" if self.selected_bin is None else "committed"
        return AnalysisJob(
            job_id=(
                f"g{self.generation}_e{self.epoch}_b{stop}_n{self._job_sequence}"
            ),
            epoch=self.epoch,
            selection_revision=self.selection_revision,
            stage=(
                "ordinary_30"
                if selection_mode == "select" and self._scheduler.active is None
                else "rolling"
            ),
            frame_start=start,
            frame_stop=stop,
            requested_bin=self.selected_bin,
            selection_mode=selection_mode,
            raw_frames=np.stack(tuple(self._frames)),
        )

    def _stale_reason(self, result: AnalysisResult, requested: AnalysisJob) -> str:
        if self._complete:
            return "playback_denominator_frozen"
        if result.epoch != self.epoch:
            return "stale_epoch"
        if result.selection_revision != requested.selection_revision:
            return "requested_revision_mismatch"
        if result.frame_start != requested.frame_start or result.frame_stop != requested.frame_stop:
            return "requested_bounds_mismatch"
        if result.selection_revision != self.selection_revision:
            return "stale_selection_revision"
        if self.processed_boundary - result.frame_stop > self.ordinary_hop_frames:
            return "publication_age_exceeded_one_hop"
        if (
            requested.selection_mode != "select"
            and result.actual_bin != self.selected_bin
        ):
            return "actual_bin_mismatch"
        return ""

    def _publish_dsp(self, result: AnalysisResult) -> BaselineDisposition:
        assert result.dsp is not None
        dsp = result.dsp
        hr_result = dsp.get("hr_result")
        accepted_rank = (
            hr_result.get("accepted_candidate_rank")
            if isinstance(hr_result, dict) else None
        )
        hr_raw = _positive_finite(dsp.get("hr_raw"))
        hr_accepted = (
            dsp.get("hr_valid") is True
            and hr_raw is not None
            and isinstance(hr_result, dict)
            and hr_result.get("ahet_verified") is True
            and type(accepted_rank) is int
            and accepted_rank >= 0
        )
        br_raw = _positive_finite(dsp.get("br_bpm"))
        br_accepted = dsp.get("br_valid") is True and br_raw is not None
        if hr_accepted:
            assert hr_raw is not None
            self._hr_history.append(hr_raw)
            display = float(np.median(tuple(self._hr_history)))
            self._hr = BaselineChannel(display, "accepted", "verified_ordinary", result.job_id)
        else:
            self._hr = _held_channel(self._hr, str(dsp.get("rej_reason", "hr_rejected")))
        if br_accepted:
            assert br_raw is not None
            self._br = BaselineChannel(br_raw, "accepted", "valid_positive_ordinary", result.job_id)
        else:
            self._br = _held_channel(self._br, str(dsp.get("rej_reason", "br_rejected")))
        reason = "accepted" if hr_accepted or br_accepted else str(
            dsp.get("rej_reason", "both_channels_rejected")
        )
        return BaselineDisposition(
            generation=self.generation,
            application_boundary=self.processed_boundary,
            selection_revision=self.selection_revision,
            disposition="published" if hr_accepted or br_accepted else "rejected",
            reason=reason,
            hr_accepted=hr_accepted,
            br_accepted=br_accepted,
            quiet_accepted=False,
            display_hr_bpm=self._hr.value_bpm,
            display_hr_state=self._hr.state,
            display_br_bpm=self._br.value_bpm,
            display_br_state=self._br.state,
            application_epoch=self.epoch,
        )

    def _held_disposition(self, disposition: str, reason: str) -> BaselineDisposition:
        self._hr = _held_channel(self._hr, reason)
        self._br = _held_channel(self._br, reason)
        return BaselineDisposition(
            generation=self.generation,
            application_boundary=self.processed_boundary,
            selection_revision=self.selection_revision,
            disposition=disposition,
            reason=reason,
            hr_accepted=False,
            br_accepted=False,
            quiet_accepted=False,
            display_hr_bpm=self._hr.value_bpm,
            display_hr_state=self._hr.state,
            display_br_bpm=self._br.value_bpm,
            display_br_state=self._br.state,
            application_epoch=self.epoch,
        )

    @staticmethod
    def _attach_request(
        disposition: BaselineDisposition, requested: AnalysisJob
    ) -> BaselineDisposition:
        return replace(
            disposition,
            request_epoch=requested.epoch,
            request_frame_start=requested.frame_start,
            request_frame_stop=requested.frame_stop,
            requested_bin=requested.requested_bin,
            selection_mode=requested.selection_mode,
            requested_selection_revision=requested.selection_revision,
        )

    def _reset_for_integrity(self, reason: str) -> tuple[ReplayControllerEvent, ...]:
        old_epoch = self.epoch
        self.epoch += 1
        self.selection_revision = 0
        self.selected_bin = None
        self._frames.clear()
        self._hr_history.clear()
        self._hr = _held_channel(self._hr, reason)
        self._br = _held_channel(self._br, reason)
        events = [ReplayControllerEvent(
            "data_integrity_reset", reason, self.processed_boundary, old_epoch,
            self.generation, selected_bin=None, selection_revision=0,
            hr_state=self._hr.state, br_state=self._br.state,
        )]
        self._last_request_boundary = None
        for scheduler_event in self._scheduler.cancel_epoch(old_epoch, reason):
            events.append(self._event(
                scheduler_event.kind, scheduler_event.reason,
                scheduler_event.job_id,
            ))
        self._pending_job_ref = None
        return tuple(events)

    def _event(
        self,
        kind: str,
        reason: str,
        job_id: str | None = None,
        *,
        replacement_job_id: str | None = None,
    ) -> ReplayControllerEvent:
        return ReplayControllerEvent(
            kind, reason, self.processed_boundary, self.epoch, self.generation,
            job_id, self.selected_bin, self.selection_revision,
            self._hr.state, self._br.state,
            replacement_job_id=replacement_job_id,
        )

    def _application_event(
        self, job_id: str, disposition: BaselineDisposition
    ) -> ReplayControllerEvent:
        return ReplayControllerEvent(
            "result_applied", disposition.reason, self.processed_boundary,
            disposition.application_epoch, self.generation, job_id, self.selected_bin,
            self.selection_revision, disposition.display_hr_state,
            disposition.display_br_state,
            disposition=disposition.disposition,
            hr_accepted=disposition.hr_accepted,
            br_accepted=disposition.br_accepted,
        )

    def _request_event(self, job: AnalysisJob) -> ReplayControllerEvent:
        return ReplayControllerEvent(
            "analysis_requested", "ordinary_boundary", self.processed_boundary,
            self.epoch, self.generation, job.job_id, self.selected_bin,
            self.selection_revision, self._hr.state, self._br.state,
            job.frame_start, job.frame_stop, job.requested_bin,
            job.selection_mode, job.selection_revision,
        )


class ReplayRuntime:
    """One controller thread, one worker, and bounded single-item queues."""

    def __init__(
        self,
        *,
        source: SequentialAdcSource,
        controller: BaselineController,
        evidence: ReplayEvidenceWriter,
        analysis_function: Callable[[AnalysisJob], ExecutedAnalysis],
        frame_limit: int,
        clock: PlaybackClock,
        shutdown_timeout_s: float = 5.0,
    ):
        self.source = source
        self.controller = controller
        self.evidence = evidence
        self.analysis_function = analysis_function
        self.frame_limit = int(frame_limit)
        self.clock = clock
        self.shutdown_timeout_s = float(shutdown_timeout_s)
        self._work: queue.Queue[AnalysisJob | None] = queue.Queue(maxsize=1)
        self._results: queue.Queue[ExecutedAnalysis] = queue.Queue(maxsize=1)
        self._commands: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._stop = threading.Event()
        self._ended = threading.Event()
        self._execution_condition = threading.Condition()
        self._snapshot_lock = threading.Lock()
        self._latest_snapshot = controller.snapshot
        self._execution_paused = False
        self._tombstoned_jobs: set[str] = set()
        self._worker: threading.Thread | None = None
        self._controller: threading.Thread | None = None
        self.failure: BaseException | None = None
        self.clean_shutdown = False

    @property
    def ended(self) -> bool:
        return self._ended.is_set()

    @property
    def latest_snapshot(self) -> BaselineSnapshot:
        with self._snapshot_lock:
            return self._latest_snapshot

    def start(self) -> None:
        if self._controller is not None:
            raise RuntimeError("replay runtime can only be started once")
        self._worker = threading.Thread(
            target=self._worker_loop, name="ReplayAnalysis", daemon=True
        )
        self._controller = threading.Thread(
            target=self._controller_loop, name="ReplayController", daemon=True
        )
        self._worker.start()
        self._controller.start()

    def request_pause(self) -> None:
        self._commands.put(("pause", None))

    def resume(self) -> None:
        self._commands.put(("resume", None))

    def set_speed(self, speed: float) -> None:
        self._commands.put(("speed", float(speed)))

    def _worker_loop(self) -> None:
        while True:
            job = self._work.get()
            if job is None:
                return
            with self._execution_condition:
                while self._execution_paused:
                    self._execution_condition.wait(timeout=0.05)
            started = time.monotonic()
            try:
                executed = self.analysis_function(job)
            except Exception as exc:
                result = AnalysisResult(
                    job_id=job.job_id, epoch=job.epoch,
                    selection_revision=job.selection_revision, stage=job.stage,
                    frame_start=job.frame_start, frame_stop=job.frame_stop,
                    actual_bin=job.requested_bin, executed=True, status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
                from src.live_motion.runtime import _exception_evidence
                executed = ExecutedAnalysis(
                    result=result,
                    evidence=_exception_evidence(
                        job, self.controller.config, result.error, job.requested_bin
                    ),
                    analysis_elapsed_s=time.monotonic() - started,
                    selector_elapsed_s=None,
                )
            if job.job_id in self._tombstoned_jobs:
                self._tombstoned_jobs.discard(job.job_id)
                continue
            while True:
                try:
                    self._results.put(executed, timeout=0.05)
                    break
                except queue.Full:
                    if job.job_id in self._tombstoned_jobs:
                        self._tombstoned_jobs.discard(job.job_id)
                        break
                    continue

    def _controller_loop(self) -> None:
        try:
            with self.source:
                self.evidence.write_playback_event({
                    "kind": "source_prepared",
                    "boundary": 0,
                    "adc_sha256": self.source.initial_sha256,
                    "file_identity": self.source.opened_file_identity,
                })
                self.clock.reanchor()
                while (
                    not self._stop.is_set()
                    and self.controller.processed_boundary < self.frame_limit
                ):
                    self._handle_commands()
                    if self.clock.snapshot.state in {"pausing", "paused"}:
                        if self.clock.snapshot.state == "pausing":
                            self.clock.acknowledge_pause()
                            self._write_update(
                                self.controller.set_playback_state("paused")
                            )
                            self.evidence.write_playback_event({
                                "kind": "pause_acknowledged",
                                "boundary": self.controller.processed_boundary,
                            })
                        time.sleep(0.01)
                        continue
                    self._dispatch()
                    if not self.clock.wait_until_next_frame():
                        continue
                    if self._stop.is_set():
                        break
                    item = self.source.read_next()
                    if self._stop.is_set():
                        if item is not None:
                            source_frames_read = getattr(
                                self.source, "frames_read",
                                getattr(self.source, "cursor", None),
                            )
                            self.evidence.write_playback_event({
                                "kind": "read_ahead_discarded_at_stop",
                                "boundary": self.controller.processed_boundary,
                                "source_frames_read": source_frames_read,
                            })
                        break
                    if item is None:
                        self._drain_one_result()
                        break
                    frame_index, cube = item
                    update = self.controller.handle_frame(frame_index, cube)
                    self.clock.frame_processed(self.controller.processed_boundary)
                    self._write_update(update)
                    # Source validity/discontinuity at this boundary is applied before
                    # any worker completion can publish into the same boundary.
                    self._drain_one_result()
                self.evidence.write_playback_event({
                    "kind": "denominator_frozen",
                    "boundary": self.controller.processed_boundary,
                    "reason": "stop_requested" if self._stop.is_set() else "source_complete",
                })
                self.evidence.write_controller_event(ReplayControllerEvent(
                    kind="denominator_frozen",
                    reason="playback_denominator_frozen",
                    boundary=self.controller.processed_boundary,
                    epoch=self.controller.epoch,
                    generation=self.controller.generation,
                    selected_bin=self.controller.selected_bin,
                    selection_revision=self.controller.selection_revision,
                    hr_state=self.controller.snapshot.hr.state,
                    br_state=self.controller.snapshot.br.state,
                ))
                finish_update = self.controller.finish(
                    "stopped" if self._stop.is_set() else "complete"
                )
                self._write_update(finish_update)
                with self._execution_condition:
                    self._execution_paused = False
                    self._execution_condition.notify_all()
                self._shutdown_active()
        except BaseException as exc:
            self.failure = exc
            try:
                self.evidence.write_playback_event({
                    "kind": "denominator_frozen",
                    "boundary": self.controller.processed_boundary,
                    "reason": "replay_failure",
                })
                self.evidence.write_controller_event(ReplayControllerEvent(
                    kind="denominator_frozen",
                    reason="playback_denominator_frozen",
                    boundary=self.controller.processed_boundary,
                    epoch=self.controller.epoch,
                    generation=self.controller.generation,
                    selected_bin=self.controller.selected_bin,
                    selection_revision=self.controller.selection_revision,
                    hr_state=self.controller.snapshot.hr.state,
                    br_state=self.controller.snapshot.br.state,
                ))
                finish_update = self.controller.finish("failed")
                # A persistence failure after result application may have mutated
                # controller internals.  Persist cleanup events, but never expose
                # values whose attempt artifact did not complete durably.
                public_before_failure = self.latest_snapshot
                failed_public_snapshot = replace(
                    public_before_failure, playback_state="failed"
                )
                self._persist_update_events(finish_update)
                self._publish_snapshot(failed_public_snapshot)
                with self._execution_condition:
                    self._execution_paused = False
                    self._execution_condition.notify_all()
                try:
                    self._shutdown_active()
                finally:
                    # Shutdown bookkeeping may inspect a controller whose result
                    # was applied before persistence failed.  Keep the consumer
                    # view pinned to the last durably supported snapshot.
                    self._publish_snapshot(failed_public_snapshot)
            except BaseException as cleanup_exc:
                if self.failure is None:
                    self.failure = cleanup_exc
        finally:
            self._stop.set()
            try:
                self._work.put_nowait(None)
            except queue.Full:
                pass
            self._ended.set()

    def _handle_commands(self) -> None:
        while True:
            try:
                command, value = self._commands.get_nowait()
            except queue.Empty:
                return
            if command == "pause":
                with self._execution_condition:
                    self._execution_paused = True
                    self.clock.request_pause()
                    self._write_update(
                        self.controller.set_playback_state("pausing")
                    )
                self.evidence.write_playback_event({
                    "kind": "pause_requested",
                    "boundary": self.controller.processed_boundary,
                })
            elif command == "resume":
                with self._execution_condition:
                    self.clock.resume()
                    self._write_update(
                        self.controller.set_playback_state("running")
                    )
                    self._execution_paused = False
                    self._execution_condition.notify_all()
                self.evidence.write_playback_event({
                    "kind": "resumed", "boundary": self.controller.processed_boundary,
                })
            elif command == "speed":
                self.clock.set_speed(value)
                self._write_update(self.controller.set_speed(value))
                self.evidence.write_playback_event({
                    "kind": "speed_changed", "speed": value,
                    "boundary": self.controller.processed_boundary,
                })

    def _dispatch(self) -> None:
        if not self._work.empty():
            return
        job = self.controller.claim_pending_job()
        if job is not None:
            self.controller.register_dispatched_job(job)
            self._work.put_nowait(job)

    def _drain_one_result(self) -> None:
        try:
            executed = self._results.get_nowait()
        except queue.Empty:
            return
        executed = self._normalize_returned_identity(executed)
        update = self.controller.handle_executed(executed)
        assert update.disposition is not None
        self._persist_update_events(update)
        self.evidence.write_attempt(executed, update.disposition)
        self._publish_snapshot(update.snapshot)
        self.evidence.write_performance({
            "kind": "analysis", "job_id": executed.result.job_id,
            "boundary": self.controller.processed_boundary,
            "analysis_elapsed_s": executed.analysis_elapsed_s,
            "selector_elapsed_s": executed.selector_elapsed_s,
        })

    def _normalize_returned_identity(
        self, executed: ExecutedAnalysis
    ) -> ExecutedAnalysis:
        """Fail a malformed worker return while preserving its scientific arrays.

        The bounded scheduler owns the request identity.  A worker return with a
        different identity must still consume exactly that active request, otherwise
        the scheduler remains wedged and the actual returned intermediates are lost.
        The normalized result therefore uses the request identity solely for
        lifecycle accounting and records every returned identity field alongside
        the untouched evidence arrays.
        """
        requested = self.controller.active_job
        if requested is None:
            return executed
        returned = executed.result
        comparisons = {
            "job_id": returned.job_id == requested.job_id,
            "epoch": returned.epoch == requested.epoch,
            "selection_revision": (
                returned.selection_revision == requested.selection_revision
            ),
            "stage": returned.stage == requested.stage,
            "frame_start": returned.frame_start == requested.frame_start,
            "frame_stop": returned.frame_stop == requested.frame_stop,
            "actual_bin": (
                returned.actual_bin == requested.requested_bin
                or requested.selection_mode == "select"
            ),
        }
        mismatches = tuple(name for name, equal in comparisons.items() if not equal)
        if not mismatches:
            return executed

        evidence = dict(executed.evidence)
        evidence.update({
            "result_identity_mismatch": np.asarray(True, dtype=np.bool_),
            "result_identity_mismatch_fields": np.asarray(mismatches, dtype="U"),
            "returned_job_id": np.asarray(returned.job_id),
            "returned_epoch": np.asarray(returned.epoch, dtype=np.int64),
            "returned_selection_revision": np.asarray(
                returned.selection_revision, dtype=np.int64
            ),
            "returned_stage": np.asarray(returned.stage),
            "returned_frame_start": np.asarray(returned.frame_start, dtype=np.int64),
            "returned_frame_stop": np.asarray(returned.frame_stop, dtype=np.int64),
            "returned_actual_bin": np.asarray(
                -1 if returned.actual_bin is None else returned.actual_bin,
                dtype=np.int64,
            ),
        })
        error = "analysis_result_identity_mismatch:" + ",".join(mismatches)
        normalized = AnalysisResult(
            job_id=requested.job_id,
            epoch=requested.epoch,
            selection_revision=requested.selection_revision,
            stage=requested.stage,
            frame_start=requested.frame_start,
            frame_stop=requested.frame_stop,
            actual_bin=requested.requested_bin,
            executed=True,
            status="failed",
            dsp=returned.dsp,
            breathing=returned.breathing,
            selection_decision=returned.selection_decision,
            error=error,
        )
        return ExecutedAnalysis(
            result=normalized,
            evidence=evidence,
            analysis_elapsed_s=executed.analysis_elapsed_s,
            selector_elapsed_s=executed.selector_elapsed_s,
        )

    def _shutdown_active(self) -> None:
        update = self.controller.cancel_pending("playback_finished")
        self._write_update(update)
        deadline = time.monotonic() + self.shutdown_timeout_s
        while self.controller.active_job is not None and time.monotonic() < deadline:
            self._drain_one_result()
            time.sleep(0.01)
        if self.controller.active_job is not None:
            active = self.controller.active_job
            assert active is not None
            self._tombstoned_jobs.add(active.job_id)
            from src.live_motion.runtime import _exception_evidence
            interrupted = AnalysisResult(
                job_id=active.job_id, epoch=active.epoch,
                selection_revision=active.selection_revision, stage=active.stage,
                frame_start=active.frame_start, frame_stop=active.frame_stop,
                actual_bin=active.requested_bin, executed=True, status="interrupted",
                error="shutdown_timeout",
            )
            synthetic = ExecutedAnalysis(
                result=interrupted,
                evidence=_exception_evidence(
                    active, self.controller.config, "shutdown_timeout",
                    active.requested_bin,
                ),
                analysis_elapsed_s=self.shutdown_timeout_s,
                selector_elapsed_s=None,
            )
            update = self.controller.handle_executed(synthetic)
            assert update.disposition is not None
            self._persist_update_events(update)
            self.evidence.write_attempt(synthetic, update.disposition)
            self._publish_snapshot(update.snapshot)
            raise RuntimeError("analysis worker did not stop within five seconds")

    def _write_update(self, update: BaselineUpdate) -> None:
        self._persist_update_events(update)
        self._publish_snapshot(update.snapshot)

    def _persist_update_events(self, update: BaselineUpdate) -> None:
        for event in update.events:
            self.evidence.write_controller_event(event)

    def _publish_snapshot(self, snapshot: BaselineSnapshot) -> None:
        with self._snapshot_lock:
            self._latest_snapshot = snapshot

    def stop(self, timeout_s: float | None = None) -> None:
        self._stop.set()
        with self._execution_condition:
            self._execution_paused = False
            self._execution_condition.notify_all()
        wait_s = self.shutdown_timeout_s + 1.0 if timeout_s is None else float(timeout_s)
        if self._controller is not None:
            self._controller.join(timeout=wait_s)
        try:
            self._work.put_nowait(None)
        except queue.Full:
            pass
        if self._worker is not None:
            self._worker.join(timeout=wait_s)
        self.clean_shutdown = bool(
            self.failure is None
            and self._controller is not None
            and not self._controller.is_alive()
            and self._worker is not None
            and not self._worker.is_alive()
        )


def _positive_finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _held_channel(channel: BaselineChannel, reason: str) -> BaselineChannel:
    if channel.value_bpm is None:
        return BaselineChannel(None, "missing", reason)
    return BaselineChannel(channel.value_bpm, "held", reason, channel.attempt_id)
