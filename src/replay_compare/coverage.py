"""Exact frame-domain availability accounting for replay presentation."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


DISPLAY_STATES = {
    "accepted",
    "preliminary",
    "held",
    "missing",
    "unresolved",
}


@dataclass(frozen=True)
class CoverageEvent:
    boundary: int
    reason: str
    attempt_id: str | None = None
    hr_state: str | None = None
    br_state: str | None = None
    quiet_state: str | None = None


@dataclass(frozen=True)
class StateInterval:
    channel: str
    start: int
    stop: int
    state: str
    reason: str
    attempt_id: str | None


@dataclass(frozen=True)
class CoverageSnapshot:
    denominator_frames: int
    hr_accepted_frames: int
    br_numeric_frames: int
    both_frames: int
    quiet_frames: int
    reference_pr_frames: int
    reference_pr_qualified_frames: int
    reference_rr_frames: int
    hr_state: str = "missing"
    br_state: str = "missing"
    quiet_state: str = "missing"

    @staticmethod
    def percentage(numerator: int, denominator: int) -> float | None:
        if denominator == 0:
            return None
        return 100.0 * numerator / denominator


class CoverageLedger:
    """Apply controller events and count leases as recording frames elapse."""

    def __init__(self, *, hop_frames: int = 60):
        if type(hop_frames) is not int or hop_frames <= 0:
            raise ValueError("hop_frames must be a positive exact integer")
        self.hop_frames = hop_frames
        self._boundary = 0
        self._states = {"hr": "missing", "br": "missing", "quiet": "missing"}
        self._lease_end = {"hr": 0, "br": 0, "quiet": 0}
        self._state_start = {"hr": 0, "br": 0, "quiet": 0}
        self._state_reason = {"hr": "initial", "br": "initial", "quiet": "initial"}
        self._state_attempt = {"hr": None, "br": None, "quiet": None}
        self._intervals: list[StateInterval] = []
        self._hr = 0
        self._br = 0
        self._both = 0
        self._quiet = 0
        self._pr = 0
        self._pr_quality = 0
        self._rr = 0

    @property
    def processed_boundary(self) -> int:
        return self._boundary

    @property
    def snapshot(self) -> CoverageSnapshot:
        return CoverageSnapshot(
            denominator_frames=self._boundary,
            hr_accepted_frames=self._hr,
            br_numeric_frames=self._br,
            both_frames=self._both,
            quiet_frames=self._quiet,
            reference_pr_frames=self._pr,
            reference_pr_qualified_frames=self._pr_quality,
            reference_rr_frames=self._rr,
            hr_state=self._states["hr"],
            br_state=self._states["br"],
            quiet_state=self._states["quiet"],
        )

    @property
    def current_states(self) -> Mapping[str, str]:
        """Return an immutable view of states at the current frame boundary."""

        return MappingProxyType(dict(self._states))

    def apply(self, event: CoverageEvent) -> None:
        if type(event.boundary) is not int or event.boundary != self._boundary:
            raise ValueError("coverage events apply at the current processed boundary")
        for channel, state in (
            ("hr", event.hr_state),
            ("br", event.br_state),
            ("quiet", event.quiet_state),
        ):
            if state is None:
                continue
            if state not in DISPLAY_STATES:
                raise ValueError(f"unknown {channel} display state {state!r}")
            self._change_state(
                channel, state, event.reason, event.attempt_id,
                lease=(state == "accepted"),
            )

    def advance_frame(
        self,
        *,
        reference_pr_present: bool = False,
        reference_pr_qualified: bool = False,
        reference_rr_present: bool = False,
    ) -> None:
        if reference_pr_qualified and not reference_pr_present:
            raise ValueError("PI-qualified PR implies positive finite PR availability")
        self._expire_at_boundary()
        hr = self._states["hr"] == "accepted"
        br = self._states["br"] == "accepted"
        quiet = self._states["quiet"] == "accepted"
        self._hr += int(hr)
        self._br += int(br)
        self._both += int(hr and br)
        self._quiet += int(quiet)
        self._pr += int(reference_pr_present)
        self._pr_quality += int(reference_pr_qualified)
        self._rr += int(reference_rr_present)
        self._boundary += 1
        # Make expiry observable at the exact half-open lease end, even when
        # playback pauses or reaches EOF before another frame is advanced.
        self._expire_at_boundary()

    def clip(self, *, reason: str, channels: tuple[str, ...] = ("hr", "br", "quiet")) -> None:
        """End active leases at a physical/data-quality boundary."""
        for channel in channels:
            if channel not in self._states:
                raise ValueError(f"unknown coverage channel {channel!r}")
            state = "held" if self._states[channel] == "accepted" else self._states[channel]
            self._change_state(channel, state, reason, None, lease=False)

    def finalized_intervals(self, stop_boundary: int | None = None) -> tuple[StateInterval, ...]:
        stop = self._boundary if stop_boundary is None else int(stop_boundary)
        if stop != self._boundary:
            raise ValueError("intervals can only be finalized at the current boundary")
        rows = list(self._intervals)
        for channel in ("hr", "br", "quiet"):
            start = self._state_start[channel]
            if stop > start:
                rows.append(StateInterval(
                    channel, start, stop, self._states[channel],
                    self._state_reason[channel], self._state_attempt[channel],
                ))
        return tuple(rows)

    def _expire_at_boundary(self) -> None:
        for channel in ("hr", "br", "quiet"):
            if (
                self._states[channel] == "accepted"
                and self._boundary >= self._lease_end[channel]
            ):
                self._change_state(
                    channel, "held", "display_lease_expired", None, lease=False
                )

    def _change_state(
        self,
        channel: str,
        state: str,
        reason: str,
        attempt_id: str | None,
        *,
        lease: bool,
    ) -> None:
        old_state = self._states[channel]
        old_reason = self._state_reason[channel]
        old_attempt = self._state_attempt[channel]
        start = self._state_start[channel]
        if self._boundary > start:
            self._intervals.append(StateInterval(
                channel, start, self._boundary, old_state, old_reason, old_attempt
            ))
        self._states[channel] = state
        self._state_start[channel] = self._boundary
        self._state_reason[channel] = reason
        self._state_attempt[channel] = attempt_id
        self._lease_end[channel] = (
            self._boundary + self.hop_frames if lease else self._boundary
        )


def reconstruct_coverage(
    events: tuple[CoverageEvent, ...] | list[CoverageEvent],
    *,
    total_frames: int,
    hop_frames: int = 60,
) -> CoverageSnapshot:
    """Independent radar-only reconstruction from persisted application events."""
    if type(total_frames) is not int or total_frames < 0:
        raise ValueError("total_frames must be a nonnegative exact integer")
    ledger = CoverageLedger(hop_frames=hop_frames)
    by_boundary: dict[int, list[CoverageEvent]] = {}
    for event in events:
        if type(event.boundary) is not int or not 0 <= event.boundary <= total_frames:
            raise ValueError("coverage event lies outside the replay denominator")
        by_boundary.setdefault(event.boundary, []).append(event)
    for boundary in range(total_frames):
        for event in by_boundary.get(boundary, ()):
            ledger.apply(event)
        ledger.advance_frame()
    for event in by_boundary.get(total_frames, ()):
        ledger.apply(event)
    return ledger.snapshot
