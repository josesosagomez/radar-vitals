"""Immutable values exchanged from replay controller to the Qt main thread."""

from __future__ import annotations

from dataclasses import dataclass

from .coverage import CoverageSnapshot
from .reference import ReferenceValue, ReferenceWindowSummary


@dataclass(frozen=True)
class DisplayChannel:
    value_bpm: float | None
    state: str
    reason: str
    attempt_id: str | None = None


@dataclass(frozen=True)
class DisplayTraceSample:
    """One fully observed frame-boundary sample for lossless GUI polling."""

    boundary: int
    generation: int
    radar_hr_bpm: float | None
    hr_state: str
    radar_br_bpm: float | None
    br_state: str
    reference_pr_bpm: float | None
    reference_rr_bpm: float | None
    reference_available: bool


@dataclass(frozen=True)
class PlaybackSnapshot:
    generation: int
    processed_boundary: int
    total_frames: int
    playback_state: str
    speed: float
    mode: str
    hr: DisplayChannel
    br: DisplayChannel
    quiet_state: str
    selected_bin: int | None
    selection_revision: int
    accumulation_stage: str
    recovery_reason: str
    measurement_window_age_frames: int | None
    analysis_lag_frames: int | None
    reference: ReferenceValue | None
    hr_reference_summary: ReferenceWindowSummary | None
    br_reference_summary: ReferenceWindowSummary | None
    coverage: CoverageSnapshot
    warning_messages: tuple[str, ...]
    selected_range_m: float | None = None
    trace_samples: tuple[DisplayTraceSample, ...] = ()
    published_monotonic_s: float | None = None
