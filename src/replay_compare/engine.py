"""Bounded replay controller and baseline analysis worker."""

from __future__ import annotations

import collections
import math
import queue
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Literal

import numpy as np

from src.live_motion.runtime import ExecutedAnalysis
from src.live_motion.scheduler import (
    AnalysisJob,
    AnalysisResult,
    BoundedAnalysisScheduler,
)
from src.warmup_select import derive_candidate_bins

from .clock import PlaybackClock
from .evidence import ReplayEvidenceWriter
from .process_analysis import (
    ProcessAnalysisError,
    ProcessExecution,
    SpawnBaselineBackend,
    _FrozenReplayAnalysisJob,
    _materialize_replay_job,
    baseline_analysis_function,
)
from .session import ReplaySessionSpec
from .source import SequentialAdcSource
from .telemetry import replay_rss_bytes


_BASELINE_WINDOW_FRAMES = 600


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


@dataclass(frozen=True)
class WorkerOutcome:
    """One complete attempt result and an optional fatal backend cause."""

    executed: ExecutedAnalysis
    fatal_backend_error: str | None = None
    performance: dict[str, Any] = field(default_factory=dict)


class BaselineController:
    """Own baseline frame history, selection, admission, and publication state."""

    ordinary_window_frames = _BASELINE_WINDOW_FRAMES
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
        owned_frame = np.array(raw, copy=True)
        owned_frame.setflags(write=False)
        self._frames.append(owned_frame)
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

        publication_age_frames = self.processed_boundary - result.frame_stop
        if publication_age_frames > self.ordinary_hop_frames:
            # A current selector result still establishes the bin identity.  Its
            # numerical values are too old for a causal one-hop display update.
            disposition = self._held_disposition(
                "expired", "publication_age_exceeded_one_hop"
            )
        elif result.status != "completed" or result.dsp is None:
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
        return _FrozenReplayAnalysisJob(
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
            raw_frames=None,
            captured_frames=tuple(self._frames),
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
        analysis_function: Callable[[AnalysisJob], ExecutedAnalysis] | None,
        process_backend: SpawnBaselineBackend | None = None,
        frame_limit: int,
        clock: PlaybackClock,
        shutdown_timeout_s: float = 5.0,
        snapshot_observer: Callable[[BaselineSnapshot], None] | None = None,
    ):
        self.source = source
        self.controller = controller
        self.evidence = evidence
        if (analysis_function is None) == (process_backend is None):
            raise ValueError(
                "replay runtime requires exactly one analysis function/backend"
            )
        self.analysis_function = analysis_function
        self.process_backend = process_backend
        self.frame_limit = int(frame_limit)
        self.clock = clock
        self.shutdown_timeout_s = float(shutdown_timeout_s)
        self.snapshot_observer = snapshot_observer
        self._work: queue.Queue[AnalysisJob | None] = queue.Queue(maxsize=1)
        self._results: queue.Queue[WorkerOutcome] = queue.Queue(maxsize=1)
        self._commands: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._stop = threading.Event()
        self._ended = threading.Event()
        self._execution_condition = threading.Condition()
        self._stop_lock = threading.Lock()
        self._shutdown_lock = threading.Lock()
        self._shutdown_started_s: float | None = None
        self._shutdown_deadline_s: float | None = None
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
        self.prepare_analysis_backend()
        self._worker = threading.Thread(
            target=self._worker_loop, name="ReplayAnalysis", daemon=True
        )
        self._controller = threading.Thread(
            target=self._controller_loop, name="ReplayController", daemon=True
        )
        self._worker.start()
        self._controller.start()

    def prepare_analysis_backend(self) -> None:
        """Complete child readiness before worker/controller/source startup."""

        if self.process_backend is not None:
            if self.process_backend.is_started:
                return
            # Child readiness and config identity are established before either
            # thread can open the registered ADC source.
            try:
                self.process_backend.start(
                    timeout_s=min(5.0, self.shutdown_timeout_s)
                )
            except BaseException as exc:
                self.failure = exc
                self._ended.set()
                raise

    def _shutdown_deadlines(
        self, requested_timeout_s: float | None = None
    ) -> tuple[float, float]:
        duration_s = (
            min(5.0, self.shutdown_timeout_s)
            if requested_timeout_s is None
            else min(5.0, max(0.0, float(requested_timeout_s)))
        )
        with self._shutdown_lock:
            if self._shutdown_deadline_s is None:
                self._shutdown_started_s = time.monotonic()
                self._shutdown_deadline_s = self._shutdown_started_s + duration_s
            normal_deadline = self._shutdown_deadline_s
        return normal_deadline, normal_deadline + 1.0

    def request_pause(self) -> None:
        self._commands.put(("pause", None))

    def resume(self) -> None:
        self._commands.put(("resume", None))

    def set_speed(self, speed: float) -> None:
        self._commands.put(("speed", float(speed)))

    def _worker_loop(self) -> None:
        while True:
            original_request = self._work.get()
            if original_request is None:
                return
            with self._execution_condition:
                while self._execution_paused:
                    self._execution_condition.wait(timeout=0.05)
            started = time.monotonic()
            materialized_job: _FrozenReplayAnalysisJob | None = None
            raw_frames: np.ndarray | None = None
            executed: ExecutedAnalysis | None = None
            process_execution: ProcessExecution | None = None
            fatal_backend_error: str | None = None
            performance: dict[str, Any] = {}
            try:
                if not isinstance(original_request, _FrozenReplayAnalysisJob):
                    raise TypeError("baseline worker requires a frozen replay request")
                if self.process_backend is not None:
                    process_execution = self.process_backend.execute(original_request)
                    executed = process_execution.executed
                    performance = {
                        "process_sequence": process_execution.sequence,
                        "parent_copy_elapsed_s": (
                            process_execution.parent_copy_elapsed_s
                        ),
                        "parent_send_elapsed_s": (
                            process_execution.parent_send_elapsed_s
                        ),
                        "parent_wait_receive_elapsed_s": (
                            process_execution.parent_wait_receive_elapsed_s
                        ),
                        "child_materialization_elapsed_s": (
                            process_execution.child_materialization_elapsed_s
                        ),
                        "child_callback_elapsed_s": (
                            process_execution.child_callback_elapsed_s
                        ),
                        "process_round_trip_elapsed_s": (
                            process_execution.round_trip_elapsed_s
                        ),
                        "process_control_bytes": process_execution.control_bytes,
                        "process_result_bytes": process_execution.result_bytes,
                    }
                else:
                    materialized_job = _materialize_replay_job(original_request)
                    raw_frames = materialized_job.raw_frames
                    if raw_frames is None:
                        raise RuntimeError(
                            "materialized replay job omitted its frame array"
                        )
                    assert self.analysis_function is not None
                    executed = self.analysis_function(materialized_job)
                executed = replace(
                    executed,
                    analysis_elapsed_s=time.monotonic() - started,
                )
            except Exception as exc:
                if self.process_backend is not None:
                    fatal_backend_error = f"{type(exc).__name__}: {exc}"
                    if isinstance(exc, ProcessAnalysisError):
                        performance = dict(exc.performance)
                    performance["fatal_backend_error"] = fatal_backend_error
                result = AnalysisResult(
                    job_id=original_request.job_id, epoch=original_request.epoch,
                    selection_revision=original_request.selection_revision,
                    stage=original_request.stage,
                    frame_start=original_request.frame_start,
                    frame_stop=original_request.frame_stop,
                    actual_bin=original_request.requested_bin,
                    executed=True, status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
                from src.live_motion.runtime import _exception_evidence
                executed = ExecutedAnalysis(
                    result=result,
                    evidence=_exception_evidence(
                        original_request,
                        self.controller.config,
                        result.error,
                        original_request.requested_bin,
                    ),
                    analysis_elapsed_s=time.monotonic() - started,
                    selector_elapsed_s=None,
                )
            assert executed is not None
            outcome = WorkerOutcome(
                executed=executed,
                fatal_backend_error=fatal_backend_error,
                performance=performance,
            )
            if original_request.job_id in self._tombstoned_jobs:
                self._tombstoned_jobs.discard(original_request.job_id)
                original_request = None
                materialized_job = None
                raw_frames = None
                executed = None
                process_execution = None
                outcome = None
                continue
            while True:
                try:
                    self._results.put(outcome, timeout=0.05)
                    break
                except queue.Full:
                    if original_request.job_id in self._tombstoned_jobs:
                        self._tombstoned_jobs.discard(original_request.job_id)
                        break
            original_request = None
            materialized_job = None
            raw_frames = None
            executed = None
            process_execution = None
            outcome = None

    def _controller_loop(self) -> None:
        try:
            with self.source:
                self.evidence.write_playback_event({
                    "kind": "source_prepared",
                    "boundary": 0,
                    "adc_sha256": self.source.initial_sha256,
                    "file_identity": self.source.opened_file_identity,
                    "monotonic_s": time.monotonic(),
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
                    controller_started_s = time.monotonic()
                    update = self.controller.handle_frame(frame_index, cube)
                    self.clock.frame_processed(self.controller.processed_boundary)
                    self._write_update(update)
                    # Physical validity for this frame is durable before a worker
                    # result at the same boundary can be applied and published.
                    self._drain_one_result()
                    self.evidence.write_performance({
                        "kind": "frame",
                        "monotonic_s": time.monotonic(),
                        "processed_boundary": self.controller.processed_boundary,
                        "pacing_lag_s": self.clock.pacing_lag_s,
                        "controller_work_elapsed_s": (
                            time.monotonic() - controller_started_s
                        ),
                        "rss_bytes": replay_rss_bytes(),
                        "work_queue_depth": self._work.qsize(),
                        "result_queue_depth": self._results.qsize(),
                        "active_analysis": self.controller.active_job is not None,
                        "pending_analysis": self.controller.pending_job is not None,
                    })
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
            outcome = self._results.get_nowait()
        except queue.Empty:
            return
        executed = outcome.executed
        executed = self._normalize_returned_identity(executed)
        update = self.controller.handle_executed(executed)
        assert update.disposition is not None
        self._persist_update_events(update)
        self.evidence.write_attempt(executed, update.disposition)
        self._publish_snapshot(update.snapshot)
        performance = {
            "kind": "analysis", "job_id": executed.result.job_id,
            "boundary": self.controller.processed_boundary,
            "analysis_elapsed_s": executed.analysis_elapsed_s,
            "selector_elapsed_s": executed.selector_elapsed_s,
            **outcome.performance,
            "fatal_backend_error": outcome.fatal_backend_error,
        }
        self.evidence.write_performance(performance)
        if outcome.fatal_backend_error is not None:
            # The failed attempt, application event, NPZ/index, snapshot and
            # performance row are durable before the pass is frozen as failed.
            raise ProcessAnalysisError(outcome.fatal_backend_error)

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
        deadline, containment_deadline = self._shutdown_deadlines()
        while self.controller.active_job is not None and time.monotonic() < deadline:
            self._drain_one_result()
            time.sleep(0.01)
        if self.controller.active_job is not None:
            active = self.controller.active_job
            assert active is not None
            if self.process_backend is not None:
                backend_error = "analysis process exceeded the five-second deadline"
                try:
                    self.process_backend.close(
                        deadline_monotonic_s=deadline,
                        containment_deadline_monotonic_s=containment_deadline,
                        force=True,
                    )
                except BaseException as exc:
                    backend_error = f"{type(exc).__name__}: {exc}"
                # A transport-fatal worker outcome may already be queued even
                # when containment consumed the full allowance.  Drain it once
                # so its measured partial spans and first causal error win.
                self._drain_one_result()
                while (
                    self.controller.active_job is not None
                    and time.monotonic() < containment_deadline
                ):
                    self._drain_one_result()
                    time.sleep(0.01)
                if self.controller.active_job is None:
                    raise ProcessAnalysisError(backend_error)
                self._tombstoned_jobs.add(active.job_id)
                from src.live_motion.runtime import _exception_evidence
                failure_result = AnalysisResult(
                    job_id=active.job_id,
                    epoch=active.epoch,
                    selection_revision=active.selection_revision,
                    stage=active.stage,
                    frame_start=active.frame_start,
                    frame_stop=active.frame_stop,
                    actual_bin=active.requested_bin,
                    executed=True,
                    status="failed",
                    error=backend_error,
                )
                failed = ExecutedAnalysis(
                    result=failure_result,
                    evidence=_exception_evidence(
                        active,
                        self.controller.config,
                        backend_error,
                        active.requested_bin,
                    ),
                    analysis_elapsed_s=self._shutdown_elapsed_s(),
                    selector_elapsed_s=None,
                )
                update = self.controller.handle_executed(failed)
                assert update.disposition is not None
                self._persist_update_events(update)
                self.evidence.write_attempt(failed, update.disposition)
                self._publish_snapshot(update.snapshot)
                self.evidence.write_performance({
                    "kind": "analysis",
                    "job_id": failed.result.job_id,
                    "boundary": self.controller.processed_boundary,
                    "analysis_elapsed_s": failed.analysis_elapsed_s,
                    "selector_elapsed_s": None,
                    "fatal_backend_error": backend_error,
                })
                raise ProcessAnalysisError(backend_error)
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
        # Application composition runs on this controller thread.  Consumers only
        # read the immutable snapshot cached by that observer, so reference and
        # coverage state can never be advanced by a Qt polling callback.
        if self.snapshot_observer is not None:
            self.snapshot_observer(snapshot)
        with self._snapshot_lock:
            self._latest_snapshot = snapshot

    def _shutdown_elapsed_s(self) -> float:
        with self._shutdown_lock:
            started_s = self._shutdown_started_s
        if started_s is None:
            return 0.0
        return max(0.0, time.monotonic() - started_s)

    def stop(self, timeout_s: float | None = None) -> None:
        # Monitor, restart, and operator Stop can converge here.  Serialize the
        # whole local shutdown while the backend independently serializes its
        # process/resource cleanup.
        with self._stop_lock:
            self._stop_runtime(timeout_s)

    def _stop_runtime(self, timeout_s: float | None) -> None:
        deadline, containment_deadline = self._shutdown_deadlines(timeout_s)
        self._stop.set()
        with self._execution_condition:
            self._execution_paused = False
            self._execution_condition.notify_all()
        if self._controller is not None:
            self._controller.join(timeout=max(0.0, deadline - time.monotonic()))
        try:
            self._work.put_nowait(None)
        except queue.Full:
            pass
        if self._worker is not None:
            self._worker.join(timeout=max(0.0, deadline - time.monotonic()))
        process_cleanup_failed = False
        if self.process_backend is not None:
            force = bool(
                (self._controller is not None and self._controller.is_alive())
                or (self._worker is not None and self._worker.is_alive())
                or time.monotonic() >= deadline
            )
            try:
                self.process_backend.close(
                    deadline_monotonic_s=deadline,
                    containment_deadline_monotonic_s=containment_deadline,
                    force=force,
                )
            except BaseException as exc:
                process_cleanup_failed = True
                if self.failure is None:
                    self.failure = exc
            if self._worker is not None and self._worker.is_alive():
                self._worker.join(
                    timeout=max(0.0, containment_deadline - time.monotonic())
                )
            if self._controller is not None and self._controller.is_alive():
                self._controller.join(
                    timeout=max(0.0, containment_deadline - time.monotonic())
                )
        if (
            (self._controller is not None and self._controller.is_alive())
            or (self._worker is not None and self._worker.is_alive())
            or process_cleanup_failed
        ) and self.failure is None:
            self.failure = TimeoutError(
                "replay runtime exceeded the five-second shutdown deadline"
            )
        self.clean_shutdown = bool(
            self.failure is None
            and self._controller is not None
            and not self._controller.is_alive()
            and self._worker is not None
            and not self._worker.is_alive()
            and (
                self.process_backend is None or self.process_backend.is_closed
            )
        )

    def analysis_process_observation(self) -> dict[str, int | bool | None]:
        """Return owned-child state for the driver's low-rate memory sampling."""

        if self.process_backend is None:
            return {
                "pid": None,
                "alive": False,
                "exitcode": None,
                "current_rss_bytes": None,
                "peak_rss_bytes": None,
            }
        return self.process_backend.observation()


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
