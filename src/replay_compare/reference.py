"""Passive Masimo mapping that cannot affect radar execution."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from src.reference_access import LoadedReference, load_reference


MICROSECONDS_PER_SECOND = 1_000_000


@dataclass(frozen=True)
class ReferenceValue:
    timestamp_s: int | None
    pr_bpm: float | None
    pi: float | None
    rr_bpm: float | None
    pr_present: bool
    pr_qualified: bool
    rr_present: bool


@dataclass(frozen=True)
class ReferenceWindowSummary:
    channel: str
    frame_start: int
    frame_stop: int
    duration_s: float
    expected_timestamp_count: int
    observed_count: int
    finite_count: int
    pr_qualified_count: int
    median_bpm: float | None


class PassiveReferenceTimeline:
    """Integer-timestamp lookup with missing buckets left missing."""

    def __init__(
        self,
        loaded: LoadedReference,
        *,
        anchor_utc_us: int,
        frame_rate_hz: float,
        min_pi: float = 0.5,
    ):
        if type(anchor_utc_us) is not int:
            raise ValueError("anchor_utc_us must be an exact integer")
        if frame_rate_hz != 20.0:
            raise ValueError("replay reference mapping requires 20 Hz")
        if not math.isfinite(min_pi):
            raise ValueError("min_pi must be finite")
        self.source = loaded.source
        self.anchor_utc_us = anchor_utc_us
        self.frame_rate_hz = float(frame_rate_hz)
        self.frame_period_us = 50_000
        self.min_pi = float(min_pi)
        frame = loaded.frame.copy(deep=True)
        required = {"epoch_utc", "pr_bpm", "pi", "rr_bpm"}
        if not required.issubset(frame.columns):
            raise ValueError("parsed Masimo reference lacks required numeric columns")
        self._rows: dict[int, tuple[float, float, float]] = {}
        for row in frame.itertuples(index=False):
            timestamp = int(getattr(row, "epoch_utc"))
            if timestamp in self._rows:
                raise ValueError(
                    f"parsed Masimo reference has duplicate timestamp {timestamp}"
                )
            self._rows[timestamp] = (
                float(getattr(row, "pr_bpm")),
                float(getattr(row, "pi")),
                float(getattr(row, "rr_bpm")),
            )

    @classmethod
    def load(
        cls,
        capture_dir: str | Path,
        *,
        development_data_root: str | Path,
        anchor_utc_us: int,
        frame_rate_hz: float,
        min_pi: float = 0.5,
    ) -> "PassiveReferenceTimeline":
        loaded = load_reference(
            capture_dir, development_data_root=development_data_root
        )
        return cls(
            loaded,
            anchor_utc_us=anchor_utc_us,
            frame_rate_hz=frame_rate_hz,
            min_pi=min_pi,
        )

    def lookup_frame(self, frame_index: int) -> ReferenceValue:
        if type(frame_index) is not int or frame_index < 0:
            raise ValueError("frame_index must be a nonnegative exact integer")
        interval_start_us = self.anchor_utc_us + frame_index * self.frame_period_us
        timestamp = interval_start_us // MICROSECONDS_PER_SECOND
        values = self._rows.get(timestamp)
        if values is None:
            return ReferenceValue(None, None, None, None, False, False, False)
        pr, pi, rr = values
        pr_present = bool(np.isfinite(pr) and pr > 0)
        pi_finite = bool(np.isfinite(pi))
        rr_present = bool(np.isfinite(rr) and rr > 0)
        return ReferenceValue(
            timestamp_s=timestamp,
            pr_bpm=pr if pr_present else None,
            pi=pi if pi_finite else None,
            rr_bpm=rr if rr_present else None,
            pr_present=pr_present,
            pr_qualified=pr_present and pi_finite and pi >= self.min_pi,
            rr_present=rr_present,
        )

    def window_summary(
        self, frame_start: int, frame_stop: int, *, channel: str
    ) -> ReferenceWindowSummary:
        if (
            type(frame_start) is not int
            or type(frame_stop) is not int
            or frame_start < 0
            or frame_stop <= frame_start
        ):
            raise ValueError("reference summary needs positive half-open frame bounds")
        if channel not in {"hr", "br"}:
            raise ValueError("channel must be 'hr' or 'br'")
        start_us = self.anchor_utc_us + frame_start * self.frame_period_us
        stop_us = self.anchor_utc_us + frame_stop * self.frame_period_us
        first_bucket = start_us // MICROSECONDS_PER_SECOND
        last_bucket_exclusive = (
            stop_us + MICROSECONDS_PER_SECOND - 1
        ) // MICROSECONDS_PER_SECOND
        timestamps = range(first_bucket, last_bucket_exclusive)
        observed = [self._rows[timestamp] for timestamp in timestamps if timestamp in self._rows]
        if channel == "hr":
            finite = [row[0] for row in observed if np.isfinite(row[0]) and row[0] > 0]
            qualified = [
                row[0]
                for row in observed
                if np.isfinite(row[0])
                and row[0] > 0
                and np.isfinite(row[1])
                and row[1] >= self.min_pi
            ]
            values = qualified
            qualified_count = len(qualified)
        else:
            finite = [row[2] for row in observed if np.isfinite(row[2]) and row[2] > 0]
            values = finite
            qualified_count = 0
        median = float(np.median(values)) if values else None
        return ReferenceWindowSummary(
            channel=channel,
            frame_start=frame_start,
            frame_stop=frame_stop,
            duration_s=(frame_stop - frame_start) / self.frame_rate_hz,
            expected_timestamp_count=last_bucket_exclusive - first_bucket,
            observed_count=len(observed),
            finite_count=len(finite),
            pr_qualified_count=qualified_count,
            median_bpm=median,
        )
