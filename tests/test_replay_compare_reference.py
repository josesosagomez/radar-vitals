"""Passive integer-timestamp reference mapping and descriptive summaries."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.reference_access import LoadedReference, ReferenceSource
from src.replay_compare.reference import PassiveReferenceTimeline


def _loaded(rows) -> LoadedReference:
    frame = pd.DataFrame(rows, columns=["epoch_utc", "pr_bpm", "pi", "rr_bpm"])
    return LoadedReference(
        frame=frame,
        source=ReferenceSource(
            source_kind="development_registry",
            capture_id="synthetic",
            subject_id="A",
            resolved_path="synthetic.csv",
            sha256="a" * 64,
        ),
    )


def _timeline(rows, *, anchor_us=1_000_250_000) -> PassiveReferenceTimeline:
    return PassiveReferenceTimeline(
        _loaded(rows), anchor_utc_us=anchor_us, frame_rate_hz=20.0
    )


def test_fractional_origin_uses_integer_microsecond_bucket_boundaries():
    timeline = _timeline(
        [(1000, 70.0, 1.0, 12.0), (1001, 71.0, 1.0, 13.0)]
    )

    assert timeline.lookup_frame(14).timestamp_s == 1000
    assert timeline.lookup_frame(14).pr_bpm == pytest.approx(70.0)
    assert timeline.lookup_frame(15).timestamp_s == 1001
    assert timeline.lookup_frame(15).pr_bpm == pytest.approx(71.0)
    assert timeline.lookup_frame(16).timestamp_s == 1001


def test_future_reference_bucket_remains_blank_until_playback_reaches_it():
    timeline = _timeline([(1001, 71.0, 1.0, 13.0)])

    before = timeline.lookup_frame(14)
    on = timeline.lookup_frame(15)
    assert before.timestamp_s is None
    assert before.pr_bpm is None
    assert before.rr_bpm is None
    assert on.timestamp_s == 1001
    assert on.pr_bpm == pytest.approx(71.0)


def test_missing_seconds_are_not_carried_interpolated_or_smoothed():
    timeline = _timeline(
        [(1000, 70.0, 1.0, 12.0), (1002, 90.0, 1.0, 20.0)]
    )

    missing = timeline.lookup_frame(20)  # 1001.25 s
    assert missing.timestamp_s is None
    assert missing.pr_bpm is None
    assert missing.rr_bpm is None


def test_positive_pr_with_low_pi_remains_visible_but_unqualified():
    timeline = _timeline([(1000, 73.0, 0.49, 15.0)])

    value = timeline.lookup_frame(0)
    assert value.pr_bpm == pytest.approx(73.0)
    assert value.pr_present is True
    assert value.pr_qualified is False
    assert value.pi == pytest.approx(0.49)
    assert value.rr_present is True


@pytest.mark.parametrize(
    ("pr", "pi", "rr", "pr_present", "qualified", "rr_present"),
    [
        (0.0, 1.0, 12.0, False, False, True),
        (-1.0, 1.0, 12.0, False, False, True),
        (np.nan, 1.0, 12.0, False, False, True),
        (70.0, np.nan, 12.0, True, False, True),
        (70.0, 0.5, 0.0, True, True, False),
        (70.0, 0.5, np.inf, True, True, False),
    ],
)
def test_nonpositive_nonfinite_values_have_channel_specific_availability(
    pr, pi, rr, pr_present, qualified, rr_present
):
    value = _timeline([(1000, pr, pi, rr)]).lookup_frame(0)
    assert value.pr_present is pr_present
    assert value.pr_qualified is qualified
    assert value.rr_present is rr_present
    assert (value.pr_bpm is not None) is pr_present
    assert (value.rr_bpm is not None) is rr_present


def test_duplicate_integer_timestamps_are_rejected_instead_of_silently_overwritten():
    with pytest.raises(ValueError, match="duplicate.*timestamp"):
        _timeline(
            [(1000, 70.0, 1.0, 12.0), (1000, 99.0, 2.0, 30.0)]
        )


def test_descriptive_hr_summary_uses_only_positive_pi_qualified_pr():
    timeline = _timeline(
        [
            (1000, 70.0, 1.0, 12.0),
            (1001, 200.0, 0.1, 13.0),
            (1002, 74.0, 0.5, 14.0),
            (1003, 0.0, 2.0, 15.0),
        ],
        anchor_us=1_000_000_000,
    )

    summary = timeline.window_summary(0, 80, channel="hr")
    assert summary.frame_start == 0
    assert summary.frame_stop == 80
    assert summary.duration_s == pytest.approx(4.0)
    assert summary.expected_timestamp_count == 4
    assert summary.observed_count == 4
    assert summary.finite_count == 3
    assert summary.pr_qualified_count == 2
    assert summary.median_bpm == pytest.approx(72.0)


def test_descriptive_br_summary_is_not_gated_by_pi():
    timeline = _timeline(
        [
            (1000, 70.0, np.nan, 12.0),
            (1001, 71.0, 0.0, 14.0),
            (1002, 72.0, 2.0, 0.0),
        ],
        anchor_us=1_000_000_000,
    )

    summary = timeline.window_summary(0, 60, channel="br")
    assert summary.expected_timestamp_count == 3
    assert summary.observed_count == 3
    assert summary.finite_count == 2
    assert summary.pr_qualified_count == 0
    assert summary.median_bpm == pytest.approx(13.0)


def test_fractional_window_reports_each_intersected_integer_bucket():
    timeline = _timeline([(1000, 70.0, 1.0, 12.0), (1030, 80.0, 1.0, 14.0)])

    summary = timeline.window_summary(0, 600, channel="hr")
    assert summary.duration_s == pytest.approx(30.0)
    assert summary.expected_timestamp_count == 31
    assert summary.observed_count == 2
    assert summary.median_bpm == pytest.approx(75.0)


def test_separate_30_and_60_second_channel_windows_do_not_share_bounds():
    rows = [(second, 60.0 + second, 1.0, 10.0 + second) for second in range(1000, 1060)]
    timeline = _timeline(rows, anchor_us=1_000_000_000)

    hr = timeline.window_summary(600, 1200, channel="hr")
    br = timeline.window_summary(0, 1200, channel="br")
    assert (hr.frame_start, hr.frame_stop, hr.expected_timestamp_count) == (600, 1200, 30)
    assert (br.frame_start, br.frame_stop, br.expected_timestamp_count) == (0, 1200, 60)
    assert hr.median_bpm != br.median_bpm


def test_summary_without_eligible_values_is_explicitly_unavailable():
    timeline = _timeline([(1000, 0.0, 0.1, 0.0)], anchor_us=1_000_000_000)

    hr = timeline.window_summary(0, 20, channel="hr")
    br = timeline.window_summary(0, 20, channel="br")
    assert hr.median_bpm is None
    assert br.median_bpm is None
