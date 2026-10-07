"""Thread-safe bounded scheduling for live analysis jobs."""
from __future__ import annotations

import collections
import threading
from dataclasses import dataclass, replace
from typing import Any, Literal

import numpy as np

from .cache import RangeCacheSnapshot

Stage = Literal["preview_10", "preview_20", "ordinary_30", "extended_60", "rolling"]
SelectionMode = Literal["select", "reuse_provisional", "committed"]


def _owned_readonly(value: np.ndarray | None) -> np.ndarray | None:
    if value is None:
        return None
    if value.flags.owndata and not value.flags.writeable:
        return value
    result = np.array(value, copy=True)
    result.setflags(write=False)
    return result


@dataclass(frozen=True)
class AnalysisJob:
    job_id: str
    epoch: int
    selection_revision: int
    stage: Stage
    frame_start: int
    frame_stop: int
    requested_bin: int | None
    selection_mode: SelectionMode
    raw_frames: np.ndarray | None = None
    range_cache_snapshot: RangeCacheSnapshot | None = None
    depends_on: str | None = None

    def __post_init__(self) -> None:
        if self.stage not in {"preview_10", "preview_20", "ordinary_30", "extended_60", "rolling"}:
            raise ValueError(f"unknown analysis stage {self.stage!r}")
        if self.selection_mode not in {"select", "reuse_provisional", "committed"}:
            raise ValueError(f"unknown selection mode {self.selection_mode!r}")
        if self.frame_start < 0 or self.frame_stop <= self.frame_start:
            raise ValueError("analysis job needs positive half-open frame bounds")
        object.__setattr__(self, "raw_frames", _owned_readonly(self.raw_frames))

    @property
    def mandatory(self) -> bool:
        return self.stage != "rolling"


@dataclass(frozen=True)
class SelectionDecision:
    selected_bin: int | None
    evidence: dict[str, Any]
    selector_succeeded: bool
    dsp_succeeded: bool
    fallback_used: bool
    reason: str


@dataclass(frozen=True)
class AnalysisResult:
    job_id: str
    epoch: int
    selection_revision: int
    stage: Stage
    frame_start: int
    frame_stop: int
    actual_bin: int | None
    executed: bool
    status: Literal["completed", "failed", "interrupted"]
    dsp: dict[str, Any] | None = None
    breathing: Any | None = None
    selection_decision: SelectionDecision | None = None
    error: str = ""


@dataclass(frozen=True)
class SchedulerEvent:
    kind: str
    job_id: str
    reason: str
    replacement_job_id: str | None = None
    epoch: int | None = None


class BoundedAnalysisScheduler:
    """Four mandatory slots, one rolling slot, and one active job."""

    def __init__(self, max_mandatory_pending: int = 4):
        if max_mandatory_pending != 4:
            raise ValueError("the accepted queue bound is exactly four mandatory jobs")
        self._mandatory: collections.deque[AnalysisJob] = collections.deque()
        self._rolling: AnalysisJob | None = None
        self._active: AnalysisJob | None = None
        self._closed = False
        self._condition = threading.Condition()

    @property
    def active(self) -> AnalysisJob | None:
        with self._condition:
            return self._active

    @property
    def pending_counts(self) -> tuple[int, int]:
        with self._condition:
            return len(self._mandatory), int(self._rolling is not None)

    def submit(self, job: AnalysisJob) -> tuple[SchedulerEvent, ...]:
        with self._condition:
            if self._closed:
                raise RuntimeError("analysis scheduler is closed")
            events: list[SchedulerEvent] = []
            if job.mandatory:
                if len(self._mandatory) >= 4:
                    raise RuntimeError("mandatory analysis queue bound exceeded")
                self._mandatory.append(job)
            else:
                if self._rolling is not None:
                    replaced = self._rolling
                    events.append(SchedulerEvent(
                        kind="rolling_coalesced",
                        job_id=replaced.job_id,
                        reason="newer_rolling_request",
                        replacement_job_id=job.job_id,
                        epoch=replaced.epoch,
                    ))
                self._rolling = job
            self._condition.notify_all()
            return tuple(events)

    def claim_next(self) -> AnalysisJob | None:
        with self._condition:
            if self._active is not None:
                return None
            job: AnalysisJob | None = None
            if self._mandatory:
                # A dependent milestone holds stage order; rolling work cannot pass it.
                if self._mandatory[0].depends_on is None:
                    job = self._mandatory.popleft()
            elif self._rolling is not None and self._rolling.depends_on is None:
                job, self._rolling = self._rolling, None
            self._active = job
            return job

    def wait_and_claim(self, timeout_s: float | None = None) -> AnalysisJob | None:
        with self._condition:
            if self._active is not None:
                return None
            ready = self._has_ready_job()
            if not ready and not self._closed:
                self._condition.wait(timeout=timeout_s)
            if self._active is not None:
                return None
            if self._mandatory and self._mandatory[0].depends_on is None:
                self._active = self._mandatory.popleft()
            elif not self._mandatory and self._rolling is not None and self._rolling.depends_on is None:
                self._active, self._rolling = self._rolling, None
            return self._active

    def _has_ready_job(self) -> bool:
        if self._mandatory:
            return self._mandatory[0].depends_on is None
        return self._rolling is not None and self._rolling.depends_on is None

    def resolve_dependency(
        self,
        dependency_job_id: str,
        *,
        selected_bin: int,
        selection_revision: int,
    ) -> tuple[str, ...]:
        """Make jobs waiting on a selection decision claimable without mutating inputs."""

        resolved: list[str] = []
        with self._condition:
            updated: collections.deque[AnalysisJob] = collections.deque()
            for job in self._mandatory:
                if job.depends_on == dependency_job_id:
                    job = replace(
                        job,
                        requested_bin=int(selected_bin),
                        selection_revision=int(selection_revision),
                        depends_on=None,
                    )
                    resolved.append(job.job_id)
                updated.append(job)
            self._mandatory = updated
            if self._rolling is not None and self._rolling.depends_on == dependency_job_id:
                self._rolling = replace(
                    self._rolling,
                    requested_bin=int(selected_bin),
                    selection_revision=int(selection_revision),
                    depends_on=None,
                )
                resolved.append(self._rolling.job_id)
            self._condition.notify_all()
        return tuple(resolved)

    def complete(self, result: AnalysisResult) -> None:
        with self._condition:
            if self._active is None or self._active.job_id != result.job_id:
                raise ValueError("analysis result does not match the active job")
            active = self._active
            identity = (
                result.epoch == active.epoch
                and result.selection_revision == active.selection_revision
                and result.stage == active.stage
                and result.frame_start == active.frame_start
                and result.frame_stop == active.frame_stop
            )
            if not identity:
                raise ValueError("analysis result identity/bounds disagree with active job")
            self._active = None
            self._condition.notify_all()

    def cancel_epoch(self, epoch: int, reason: str) -> tuple[SchedulerEvent, ...]:
        events: list[SchedulerEvent] = []
        with self._condition:
            kept: collections.deque[AnalysisJob] = collections.deque()
            for job in self._mandatory:
                if job.epoch == epoch:
                    events.append(SchedulerEvent(
                        "cancelled_before_execution", job.job_id, reason, epoch=epoch
                    ))
                else:
                    kept.append(job)
            self._mandatory = kept
            if self._rolling is not None and self._rolling.epoch == epoch:
                events.append(SchedulerEvent(
                    "cancelled_before_execution", self._rolling.job_id, reason, epoch=epoch
                ))
                self._rolling = None
            if self._active is not None and self._active.epoch == epoch:
                events.append(SchedulerEvent(
                    "active_invalidated", self._active.job_id, reason, epoch=epoch
                ))
            self._condition.notify_all()
        return tuple(events)

    def close(self, reason: str = "shutdown") -> tuple[SchedulerEvent, ...]:
        events: list[SchedulerEvent] = []
        with self._condition:
            self._closed = True
            while self._mandatory:
                job = self._mandatory.popleft()
                events.append(SchedulerEvent(
                    "cancelled_before_execution", job.job_id, reason, epoch=job.epoch
                ))
            if self._rolling is not None:
                events.append(SchedulerEvent(
                    "cancelled_before_execution", self._rolling.job_id, reason,
                    epoch=self._rolling.epoch,
                ))
                self._rolling = None
            self._condition.notify_all()
        return tuple(events)
