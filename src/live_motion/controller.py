"""Frame-indexed recovery state machine and immutable display publication."""
from __future__ import annotations

import collections
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from .cache import RangeBinCache
from .config import FeatureThreshold, LiveMotionSettings
from .features import FrameObservation, MonitorFeatures
from .scheduler import AnalysisJob, AnalysisResult


@dataclass(frozen=True)
class DisplayValue:
    value_bpm: float
    state: Literal["missing", "held", "preliminary", "fresh"]
    color: Literal["neutral", "red", "amber", "green"]


@dataclass(frozen=True)
class DisplaySnapshot:
    frame_index: int
    epoch: int
    status: str
    reason: str
    hr: DisplayValue
    br: DisplayValue
    breathing_status: str
    locked_bin: int | None
    selection_revision: int


@dataclass(frozen=True)
class ControllerEvent:
    kind: str
    reason: str
    frame_index: int
    epoch: int
    job_id: str | None = None


@dataclass(frozen=True)
class DependencyResolution:
    dependency_job_id: str
    selected_bin: int
    selection_revision: int


@dataclass(frozen=True)
class RequestedJobIdentity:
    job_id: str
    epoch: int
    selection_revision: int
    stage: str
    frame_start: int
    frame_stop: int
    requested_bin: int | None
    selection_mode: str


@dataclass(frozen=True)
class AttemptDisposition:
    result: AnalysisResult
    disposition: Literal[
        "published", "invalid", "failed", "expired", "superseded", "stale_epoch"
    ]
    reason: str
    hr_accepted: bool = False
    br_accepted: bool = False
    selection_applied: bool = False
    selection_revision: int | None = None
    snapshot: DisplaySnapshot | None = None


@dataclass(frozen=True)
class ControllerUpdate:
    snapshot: DisplaySnapshot
    jobs: tuple[AnalysisJob, ...] = ()
    events: tuple[ControllerEvent, ...] = ()
    dependency_resolutions: tuple[DependencyResolution, ...] = ()
    attempts: tuple[AttemptDisposition, ...] = ()
    reset_buffers: bool = False


def _missing_value() -> DisplayValue:
    return DisplayValue(float("nan"), "missing", "neutral")


def _held(value: DisplayValue) -> DisplayValue:
    if np.isfinite(value.value_bpm):
        return DisplayValue(float(value.value_bpm), "held", "red")
    return _missing_value()


class RecoveryController:
    """Own recovery state, fresh buffers, selection revisions, and display values."""

    def __init__(self, settings: LiveMotionSettings, cache: RangeBinCache):
        if not settings.enabled:
            raise ValueError("RecoveryController requires enabled development settings")
        if settings.presence_thresholds is None or settings.movement_thresholds is None:
            raise ValueError("RecoveryController requires accepted calibrated thresholds")
        self.settings = settings
        self.cache = cache
        self._raw: collections.deque[tuple[int, np.ndarray]] = collections.deque(
            maxlen=settings.ordinary_window_frames
        )
        self._state = "settling"
        self._epoch = 0
        self._epoch_start: int | None = None
        self._selection_revision = 0
        self._provisional_bin: int | None = None
        self._committed_bin: int | None = None
        self._motion_latched = False
        self._quiet_confirmed_through: int | None = None
        self._quiet_frames = 0
        self._last_frame_index: int | None = None
        self._job_counter = 0
        self._stage_jobs: dict[str, str] = {}
        self._requested_jobs: dict[str, RequestedJobIdentity] = {}
        self._job_order: collections.deque[str] = collections.deque()
        self._result_buffer: dict[str, AnalysisResult] = {}
        self._hr_history: collections.deque[float] = collections.deque(maxlen=5)
        self._hr = _missing_value()
        self._br = _missing_value()
        self._breathing_status = ""
        self._reason = "waiting_for_confirmed_target_and_stillness"
        self._status = "settling"
        self._latest_published_stop = -1

    def snapshot(self) -> DisplaySnapshot:
        return DisplaySnapshot(
            frame_index=-1 if self._last_frame_index is None else self._last_frame_index,
            epoch=self._epoch,
            status=self._status,
            reason=self._reason,
            hr=self._hr,
            br=self._br,
            breathing_status=self._breathing_status,
            locked_bin=(
                self._committed_bin
                if self._committed_bin is not None
                else self._provisional_bin
            ),
            selection_revision=self._selection_revision,
        )

    def _event_update(
        self,
        frame_index: int,
        kind: str,
        reason: str,
        *,
        moving: bool = False,
    ) -> ControllerUpdate:
        old_epoch = self._epoch
        if self._state == "active":
            self._epoch += 1
        self._state = "moving" if moving else "settling"
        self._motion_latched = moving
        self._epoch_start = None
        self._quiet_confirmed_through = None
        self._quiet_frames = 0
        self._provisional_bin = None
        self._committed_bin = None
        self._selection_revision = 0
        self._raw.clear()
        self.cache.clear()
        self._hr_history.clear()
        self._hr = _held(self._hr)
        self._br = _held(self._br)
        self._breathing_status = ""
        self._status = self._state
        self._reason = reason
        self._stage_jobs.clear()
        self._latest_published_stop = -1
        return ControllerUpdate(
            snapshot=self.snapshot(),
            events=(ControllerEvent(kind, reason, frame_index, old_epoch),),
            reset_buffers=True,
        )

    @staticmethod
    def _feature_entered(value: float, threshold: FeatureThreshold) -> bool:
        return threshold.enabled and threshold.entry is not None and value >= threshold.entry

    @staticmethod
    def _feature_cleared(value: float, threshold: FeatureThreshold) -> bool:
        return (not threshold.enabled) or (
            threshold.exit is not None and value <= threshold.exit
        )

    def _movement_entered(self, monitor: MonitorFeatures) -> bool:
        thresholds = self.settings.movement_thresholds
        assert thresholds is not None
        return any((
            self._feature_entered(monitor.presence_power_db, thresholds.presence),
            self._feature_entered(monitor.phase_activity_rad_rms, thresholds.phase_activity),
            self._feature_entered(monitor.range_profile_change, thresholds.range_profile_change),
        ))

    def _movement_cleared(self, monitor: MonitorFeatures) -> bool:
        thresholds = self.settings.movement_thresholds
        assert thresholds is not None
        return all((
            self._feature_cleared(monitor.presence_power_db, thresholds.presence),
            self._feature_cleared(monitor.phase_activity_rad_rms, thresholds.phase_activity),
            self._feature_cleared(monitor.range_profile_change, thresholds.range_profile_change),
        ))

    def _monitor_event(self, monitor: MonitorFeatures, frame_index: int) -> ControllerUpdate | None:
        if not monitor.valid:
            return self._event_update(frame_index, "target_unconfirmed", monitor.reason)
        presence = self.settings.presence_thresholds
        assert presence is not None
        if monitor.presence_power_db <= presence.empty_max_db:
            return self._event_update(frame_index, "target_lost", "missing_or_weak_target")
        if monitor.presence_power_db < presence.occupied_min_db:
            return self._event_update(frame_index, "target_unconfirmed", "ambiguous_target_presence")

        if self._motion_latched:
            if not self._movement_cleared(monitor):
                self._status = "moving"
                self._reason = "physical_movement"
                return ControllerUpdate(self.snapshot())
            self._motion_latched = False
            self._state = "settling"
            self._quiet_confirmed_through = None
            self._quiet_frames = 0
        elif self._movement_entered(monitor):
            return self._event_update(frame_index, "movement", "physical_movement", moving=True)

        if self._state != "active":
            if not self._movement_cleared(monitor):
                self._quiet_confirmed_through = None
                self._quiet_frames = 0
                self._status = "settling"
                self._reason = "movement_features_above_quiet_exit"
                return ControllerUpdate(self.snapshot())
            if self._quiet_confirmed_through is None or monitor.frame_start > self._quiet_confirmed_through:
                self._quiet_frames = monitor.frame_stop - monitor.frame_start
            else:
                self._quiet_frames += max(0, monitor.frame_stop - self._quiet_confirmed_through)
            self._quiet_confirmed_through = monitor.frame_stop
            if self._quiet_frames >= self.settings.stillness_frames:
                # The 60 still frames establish eligibility. Fresh analysis begins
                # at the next half-open frame index and never includes settling data.
                self._state = "active"
                self._epoch_start = monitor.frame_stop
                self._status = "accumulating"
                self._reason = "fresh_epoch_started"
                self._raw.clear()
                self.cache.clear()
                return ControllerUpdate(
                    self.snapshot(),
                    events=(ControllerEvent(
                        "epoch_started", "stillness_confirmed", frame_index, self._epoch
                    ),),
                    reset_buffers=False,
                )
            self._status = "settling"
            self._reason = f"stillness_confirmation_{self._quiet_frames}_of_{self.settings.stillness_frames}"
        return None

    def handle_frame(self, observation: FrameObservation) -> ControllerUpdate:
        frame_index = observation.frame_index
        if self._last_frame_index is not None and frame_index != self._last_frame_index + 1:
            self._last_frame_index = frame_index
            return self._event_update(frame_index, "data_gap", "discontinuous_frame_index")
        self._last_frame_index = frame_index
        if not observation.valid:
            return self._event_update(
                frame_index, "invalid_data", observation.invalid_reason or "invalid_frame"
            )
        if observation.monitor is not None:
            event = self._monitor_event(observation.monitor, frame_index)
            if event is not None:
                return event
        if self._state != "active":
            return ControllerUpdate(self.snapshot())

        assert self._epoch_start is not None
        expected_gate_shape = (
            observation.raw_frame.shape[0],
            observation.raw_frame.shape[1],
            len(self.settings.candidate_bins),
        )
        if observation.gate_samples.shape != expected_gate_shape:
            return self._event_update(frame_index, "invalid_data", "invalid_gate_sample_shape")
        self._raw.append((frame_index, observation.raw_frame))
        self.cache.append(
            frame_index, observation.gate_bins, observation.gate_samples, valid=True
        )
        fresh_count = frame_index + 1 - self._epoch_start
        jobs = self._jobs_at_boundary(fresh_count, frame_index + 1)
        return ControllerUpdate(self.snapshot(), jobs=jobs)

    def _next_job_id(self, stage: str, stop: int) -> str:
        self._job_counter += 1
        return f"e{self._epoch:04d}-{stage}-{stop:09d}-{self._job_counter:06d}"

    def _raw_array(self, count: int) -> np.ndarray:
        if len(self._raw) < count:
            raise RuntimeError(f"raw ring has {len(self._raw)} frames, need {count}")
        return np.stack([frame for _, frame in list(self._raw)[-count:]])

    def _job(
        self,
        stage: str,
        start: int,
        stop: int,
        mode: str,
        requested_bin: int | None,
        *,
        depends_on: str | None = None,
        cache_snapshot=None,
    ) -> AnalysisJob:
        job_id = self._next_job_id(stage, stop)
        self._stage_jobs[stage] = job_id
        job = AnalysisJob(
            job_id=job_id,
            epoch=self._epoch,
            selection_revision=self._selection_revision,
            stage=stage,  # type: ignore[arg-type]
            frame_start=start,
            frame_stop=stop,
            requested_bin=requested_bin,
            selection_mode=mode,  # type: ignore[arg-type]
            raw_frames=self._raw_array(min(stop - start, self.settings.ordinary_window_frames)),
            range_cache_snapshot=cache_snapshot,
            depends_on=depends_on,
        )
        self._requested_jobs[job_id] = RequestedJobIdentity(
            job.job_id, job.epoch, job.selection_revision, job.stage,
            job.frame_start, job.frame_stop, job.requested_bin, job.selection_mode,
        )
        self._job_order.append(job_id)
        return job

    def _jobs_at_boundary(self, fresh_count: int, stop: int) -> tuple[AnalysisJob, ...]:
        start = self._epoch_start
        assert start is not None
        if fresh_count == self.settings.preview_frames[0]:
            return (self._job("preview_10", start, stop, "select", None),)
        if fresh_count == self.settings.preview_frames[1]:
            dependency = None if self._provisional_bin is not None else self._stage_jobs.get("preview_10")
            return (self._job(
                "preview_20", start, stop, "reuse_provisional", self._provisional_bin,
                depends_on=dependency,
            ),)
        if fresh_count == self.settings.ordinary_window_frames:
            dependency = None if self._provisional_bin is not None else self._stage_jobs.get("preview_10")
            return (self._job(
                "ordinary_30", start, stop, "select", None, depends_on=dependency
            ),)
        if fresh_count < self.settings.ordinary_window_frames:
            return ()
        if (fresh_count - self.settings.ordinary_window_frames) % self.settings.ordinary_hop_frames:
            return ()
        final_dependency = None if self._committed_bin is not None else self._stage_jobs.get("ordinary_30")
        if (
            self.settings.extended_breathing_enabled
            and fresh_count == self.settings.extended_breathing_window_frames
        ):
            snapshot = self.cache.snapshot(stop - self.settings.extended_breathing_window_frames, stop)
            return (self._job(
                "extended_60", stop - self.settings.extended_breathing_window_frames, stop,
                "committed", self._committed_bin, depends_on=final_dependency,
                cache_snapshot=snapshot,
            ),)
        snapshot = None
        if self.settings.extended_breathing_enabled and fresh_count > self.settings.extended_breathing_window_frames:
            snapshot = self.cache.snapshot(stop - self.settings.extended_breathing_window_frames, stop)
        overall_start = (
            stop - self.settings.extended_breathing_window_frames
            if snapshot is not None
            else stop - self.settings.ordinary_window_frames
        )
        return (self._job(
            "rolling", overall_start, stop,
            "committed", self._committed_bin, depends_on=final_dependency,
            cache_snapshot=snapshot,
        ),)

    def handle_result(self, result: AnalysisResult) -> ControllerUpdate:
        requested = self._requested_jobs.get(result.job_id)
        if requested is None:
            return ControllerUpdate(
                self.snapshot(),
                attempts=(self._attempt(result, "superseded", "unknown_job_id"),),
            )
        if result.job_id not in self._job_order:
            return ControllerUpdate(
                self.snapshot(),
                attempts=(self._attempt(result, "superseded", "job_already_resolved"),),
            )
        identity_matches = (
            result.epoch == requested.epoch
            and result.stage == requested.stage
            and result.frame_start == requested.frame_start
            and result.frame_stop == requested.frame_stop
            and result.selection_revision == requested.selection_revision
            and (
                requested.requested_bin is None
                or result.actual_bin == requested.requested_bin
            )
            and (
                (requested.selection_mode == "select")
                or result.selection_decision is None
            )
            and (
                result.selection_decision is None
                or not result.selection_decision.selector_succeeded
                or result.selection_decision.selected_bin == result.actual_bin
            )
        )
        if not identity_matches:
            return self._discard_registered_result(
                result,
                "job_identity_or_bounds_mismatch",
                reset_selection=requested.selection_mode == "select",
            )
        if self._job_order[0] != result.job_id:
            if len(self._result_buffer) >= 6:
                raise RuntimeError("out-of-order analysis result buffer bound exceeded")
            self._result_buffer[result.job_id] = result
            return ControllerUpdate(
                self.snapshot(),
                events=(ControllerEvent(
                    "result_buffered", "awaiting_prior_stage",
                    self._last_frame_index if self._last_frame_index is not None else -1,
                    self._epoch, result.job_id,
                ),),
            )

        updates: list[ControllerUpdate] = []
        current = result
        while True:
            self._job_order.popleft()
            updates.append(self._apply_result(current))
            self._requested_jobs.pop(current.job_id, None)
            if not self._job_order or self._job_order[0] not in self._result_buffer:
                break
            current = self._result_buffer.pop(self._job_order[0])
        return self._merge_updates(updates)

    def _discard_registered_result(
        self,
        result: AnalysisResult,
        reason: str,
        *,
        reset_selection: bool,
    ) -> ControllerUpdate:
        """Consume a completed bad result so it cannot poison ordered publication."""

        was_head = bool(self._job_order and self._job_order[0] == result.job_id)
        self._job_order.remove(result.job_id)
        self._requested_jobs.pop(result.job_id, None)
        self._result_buffer.pop(result.job_id, None)
        if reset_selection:
            event_update = self._event_update(
                self._last_frame_index or result.frame_stop - 1,
                "selection_failed",
                reason,
            )
            return ControllerUpdate(
                event_update.snapshot,
                events=event_update.events,
                attempts=(self._attempt(result, "superseded", reason),),
                reset_buffers=True,
            )

        if was_head:
            self._hold_current_analysis(reason)

        updates = [ControllerUpdate(
            self.snapshot(),
            attempts=(self._attempt(result, "superseded", reason),),
        )]
        if was_head:
            while self._job_order and self._job_order[0] in self._result_buffer:
                job_id = self._job_order.popleft()
                buffered = self._result_buffer.pop(job_id)
                updates.append(self._apply_result(buffered))
                self._requested_jobs.pop(job_id, None)
        return self._merge_updates(updates)

    def _hold_current_analysis(self, reason: str) -> None:
        """Expose a current analysis failure without starting movement recovery."""

        self._hr = _held(self._hr)
        self._br = _held(self._br)
        self._status = "active"
        self._reason = reason

    def register_dispatched_job(self, job: AnalysisJob) -> None:
        """Bind scheduler-resolved bin/revision immediately before worker execution."""

        requested = self._requested_jobs.get(job.job_id)
        if requested is None:
            raise ValueError("cannot dispatch an unknown or cancelled analysis job")
        if (
            job.epoch != requested.epoch
            or job.stage != requested.stage
            or job.frame_start != requested.frame_start
            or job.frame_stop != requested.frame_stop
        ):
            raise ValueError("dispatched job identity/bounds changed")
        self._requested_jobs[job.job_id] = RequestedJobIdentity(
            job.job_id, job.epoch, job.selection_revision, job.stage,
            job.frame_start, job.frame_stop, job.requested_bin, job.selection_mode,
        )

    def _merge_updates(self, updates: list[ControllerUpdate]) -> ControllerUpdate:
        return ControllerUpdate(
            snapshot=updates[-1].snapshot,
            jobs=tuple(job for update in updates for job in update.jobs),
            events=tuple(event for update in updates for event in update.events),
            dependency_resolutions=tuple(
                resolution for update in updates for resolution in update.dependency_resolutions
            ),
            attempts=tuple(attempt for update in updates for attempt in update.attempts),
            reset_buffers=any(update.reset_buffers for update in updates),
        )

    def _attempt(
        self,
        result: AnalysisResult,
        disposition: Literal[
            "published", "invalid", "failed", "expired", "superseded", "stale_epoch"
        ],
        reason: str,
        *,
        hr_accepted: bool = False,
        br_accepted: bool = False,
        selection_applied: bool = False,
        recorded_selection_revision: int | None = None,
    ) -> AttemptDisposition:
        """Capture the state belonging to this attempt before another result is applied."""

        snapshot = self.snapshot()
        return AttemptDisposition(
            result=result,
            disposition=disposition,
            reason=reason,
            hr_accepted=hr_accepted,
            br_accepted=br_accepted,
            selection_applied=selection_applied,
            selection_revision=(
                snapshot.selection_revision
                if recorded_selection_revision is None
                else recorded_selection_revision
            ),
            snapshot=snapshot,
        )

    def _apply_result(self, result: AnalysisResult) -> ControllerUpdate:
        if result.epoch != self._epoch:
            attempt = self._attempt(
                result,
                "stale_epoch",
                "epoch_changed",
                recorded_selection_revision=result.selection_revision,
            )
            return ControllerUpdate(self.snapshot(), attempts=(attempt,))
        if result.selection_revision != self._selection_revision:
            return ControllerUpdate(
                self.snapshot(),
                attempts=(self._attempt(
                    result, "superseded", "obsolete_selection_revision"
                ),),
            )

        if result.stage in {"preview_10", "ordinary_30"} and result.selection_decision is None:
            reason = result.error or "selector_result_missing"
            update = self._event_update(
                self._last_frame_index or result.frame_stop - 1,
                "selection_failed",
                reason,
            )
            return ControllerUpdate(
                update.snapshot,
                events=update.events,
                attempts=(self._attempt(result, "failed", reason),),
                reset_buffers=True,
            )

        resolutions: list[DependencyResolution] = []
        events: list[ControllerEvent] = []
        selection_applied = False
        decision = result.selection_decision
        if decision is not None:
            if not decision.selector_succeeded or decision.selected_bin is None:
                update = self._event_update(
                    self._last_frame_index or result.frame_stop - 1,
                    "selection_failed",
                    decision.reason or "selector_failed",
                )
                return ControllerUpdate(
                    update.snapshot,
                    events=update.events,
                    attempts=(self._attempt(result, "failed", "selector_failed"),),
                    reset_buffers=True,
                )
            selected = int(decision.selected_bin)
            if selected not in self.settings.candidate_bins:
                update = self._event_update(
                    self._last_frame_index or result.frame_stop - 1,
                    "selection_failed",
                    "selected_bin_outside_candidate_gate",
                )
                return ControllerUpdate(
                    update.snapshot,
                    events=update.events,
                    attempts=(self._attempt(
                        result, "failed", "selected_bin_outside_candidate_gate"
                    ),),
                    reset_buffers=True,
                )
            previous_bin = self._committed_bin or self._provisional_bin
            self._selection_revision += 1
            selection_applied = True
            if result.stage == "preview_10":
                self._provisional_bin = selected
            elif result.stage == "ordinary_30":
                self._committed_bin = selected
                if previous_bin is not None and selected != previous_bin:
                    self._hr_history.clear()
                    self._hr = _held(self._hr)
                    self._br = _held(self._br)
                    events.append(ControllerEvent(
                        "final_bin_changed", "bin_specific_results_cleared",
                        self._last_frame_index or result.frame_stop - 1, self._epoch,
                        result.job_id,
                    ))
            resolutions.append(DependencyResolution(
                result.job_id, selected, self._selection_revision
            ))

            if not decision.dsp_succeeded:
                # A fallback selection may safely advance bin-control state and
                # unblock dependent stages, but it is not measurement evidence.
                reason = decision.reason or "all_dsp_failed"
                self._hold_current_analysis(reason)
                return ControllerUpdate(
                    self.snapshot(),
                    events=tuple(events),
                    dependency_resolutions=tuple(resolutions),
                    attempts=(self._attempt(
                        result,
                        "invalid",
                        reason,
                        selection_applied=selection_applied,
                    ),),
                )

        if result.status != "completed" or result.dsp is None:
            reason = result.error or "analysis_failed"
            self._hold_current_analysis(reason)
            return ControllerUpdate(
                self.snapshot(),
                events=tuple(events),
                dependency_resolutions=tuple(resolutions),
                attempts=(self._attempt(
                    result, "failed", reason, selection_applied=selection_applied
                ),),
            )

        current_stop = (self._last_frame_index + 1) if self._last_frame_index is not None else result.frame_stop
        if current_stop - result.frame_stop > self.settings.ordinary_hop_frames:
            return ControllerUpdate(
                self.snapshot(), events=tuple(events),
                dependency_resolutions=tuple(resolutions),
                attempts=(self._attempt(
                    result, "expired", "older_than_one_ordinary_hop",
                    selection_applied=selection_applied,
                ),),
            )
        if result.frame_stop <= self._latest_published_stop:
            return ControllerUpdate(
                self.snapshot(), events=tuple(events),
                dependency_resolutions=tuple(resolutions),
                attempts=(self._attempt(
                    result, "superseded", "newer_result_already_published",
                    selection_applied=selection_applied,
                ),),
            )

        hr_accepted, br_accepted = self._publish_measurement(result)
        published = hr_accepted or br_accepted
        self._latest_published_stop = result.frame_stop
        disposition = "published" if published else "invalid"
        return ControllerUpdate(
            self.snapshot(), events=tuple(events),
            dependency_resolutions=tuple(resolutions),
            attempts=(self._attempt(
                result,
                disposition,
                self._reason,
                hr_accepted=hr_accepted,
                br_accepted=br_accepted,
                selection_applied=selection_applied,
            ),),
        )

    def _publish_measurement(self, result: AnalysisResult) -> tuple[bool, bool]:
        dsp = result.dsp or {}
        preview = result.stage in {"preview_10", "preview_20"}
        hr_valid = bool(dsp.get("hr_valid", False))
        br_valid = bool(dsp.get("br_valid", False))
        hr_value = float(dsp.get("hr_raw", np.nan))
        br_value = float(dsp.get("br_bpm", np.nan))
        if preview:
            # The cycle floor is an additional requirement. It never bypasses
            # existing DSP validity or the existing first-bin edge rejection.
            duration_s = (result.frame_stop - result.frame_start) / self.settings.frame_rate_hz
            cycles = br_value * duration_s / 60.0 if np.isfinite(br_value) else 0.0
            preview_valid = br_valid and cycles >= 2.0
            hr_valid = hr_valid and preview_valid
            br_valid = br_valid and preview_valid

        breathing = result.breathing
        breathing_state = getattr(breathing, "state", None)
        if isinstance(breathing, dict):
            breathing_state = breathing.get("state")
        if breathing_state == "positive":
            extended_value = getattr(breathing, "value_bpm", np.nan)
            if isinstance(breathing, dict):
                extended_value = breathing.get("value_bpm", np.nan)
            if np.isfinite(float(extended_value)) and float(extended_value) > 0.0:
                br_valid, br_value = True, float(extended_value)
                self._breathing_status = ""
            else:
                br_valid = False
        elif breathing_state == "quiet":
            br_valid = False
            self._breathing_status = "No breathing motion detected"
            hr_valid = False
        elif breathing_state == "unresolved":
            br_valid = False
            self._breathing_status = "Breathing activity unresolved"

        veto_reason = str(dsp.get("hr_veto_reason", ""))
        if veto_reason:
            hr_valid = False

        hr_accepted = False
        br_accepted = False
        if hr_valid and np.isfinite(hr_value):
            if preview:
                self._hr = DisplayValue(hr_value, "preliminary", "amber")
            else:
                self._hr_history.append(hr_value)
                smoothed = float(np.median(np.asarray(self._hr_history)))
                self._hr = DisplayValue(smoothed, "fresh", "green")
            hr_accepted = True
        else:
            self._hr = _held(self._hr)
        if br_valid and np.isfinite(br_value) and br_value > 0.0:
            self._br = DisplayValue(br_value, "preliminary" if preview else "fresh",
                                    "amber" if preview else "green")
            br_accepted = True
        else:
            self._br = _held(self._br)
        accepted = hr_accepted or br_accepted
        self._status = "preliminary" if preview and accepted else "active"
        self._reason = veto_reason or ("estimate_accepted" if accepted else "estimate_rejected")
        return hr_accepted, br_accepted

    def cancel_job(self, job_id: str, reason: str) -> ControllerUpdate:
        if job_id in self._job_order:
            self._job_order.remove(job_id)
        self._result_buffer.pop(job_id, None)
        self._requested_jobs.pop(job_id, None)
        return ControllerUpdate(
            self.snapshot(),
            events=(ControllerEvent(
                "job_cancelled", reason,
                self._last_frame_index if self._last_frame_index is not None else -1,
                self._epoch, job_id,
            ),),
        )
