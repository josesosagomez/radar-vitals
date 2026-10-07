"""Recording-time playback pacing independent of scientific boundaries."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Callable


SUPPORTED_SPEEDS = (0.5, 1.0, 2.0, 4.0)


@dataclass(frozen=True)
class ClockSnapshot:
    state: str
    speed: float
    processed_frames: int


class PlaybackClock:
    """Pace frame boundaries while making pause acknowledgment explicit."""

    def __init__(
        self,
        frame_rate_hz: float,
        speed: float = 1.0,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        wait: Callable[[float], None] = time.sleep,
    ):
        if not math.isfinite(frame_rate_hz) or frame_rate_hz <= 0:
            raise ValueError("frame_rate_hz must be finite and positive")
        self.frame_rate_hz = float(frame_rate_hz)
        self._monotonic = monotonic
        self._wait = wait
        self._speed = self._validate_speed(speed)
        self._state = "running"
        self._anchor_wall_s = self._monotonic()
        self._anchor_frame = 0
        self._processed_frames = 0

    @staticmethod
    def _validate_speed(speed: float) -> float:
        value = float(speed)
        if value not in SUPPORTED_SPEEDS:
            raise ValueError(f"speed must be one of {SUPPORTED_SPEEDS}")
        return value

    @property
    def snapshot(self) -> ClockSnapshot:
        return ClockSnapshot(self._state, self._speed, self._processed_frames)

    def request_pause(self) -> None:
        if self._state == "running":
            self._state = "pausing"

    def acknowledge_pause(self) -> None:
        if self._state != "pausing":
            raise RuntimeError("pause can be acknowledged only after a request")
        self._state = "paused"

    def resume(self) -> None:
        if self._state not in {"paused", "pausing"}:
            return
        self._state = "running"
        self._reanchor()

    def set_speed(self, speed: float) -> None:
        value = self._validate_speed(speed)
        if value == self._speed:
            return
        self._speed = value
        self._reanchor()

    def frame_processed(self, processed_frames: int) -> None:
        if type(processed_frames) is not int or processed_frames < self._processed_frames:
            raise ValueError("processed frame boundary must be monotone")
        self._processed_frames = processed_frames

    def wait_until_next_frame(self) -> bool:
        """Wait for the next boundary; return False while paused/pausing."""
        if self._state != "running":
            return False
        next_boundary = self._processed_frames + 1
        elapsed_frames = next_boundary - self._anchor_frame
        deadline = self._anchor_wall_s + elapsed_frames / (
            self.frame_rate_hz * self._speed
        )
        remaining = deadline - self._monotonic()
        if remaining > 0:
            self._wait(remaining)
        return self._state == "running"

    def _reanchor(self) -> None:
        self._anchor_wall_s = self._monotonic()
        self._anchor_frame = self._processed_frames
