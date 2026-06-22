"""Tests for src/windowing.py — common_center_windows and sliding_windows."""

import pytest

from src.windowing import common_center_windows, sliding_windows

# ---------------------------------------------------------------------------
# Shared fixture for common_center_windows canonical call
# ---------------------------------------------------------------------------

@pytest.fixture
def ccw():
    return common_center_windows(
        total_frames=3000,
        trim_frames=600,
        window_lengths_frames=[400, 500, 600],
        center_spacing_frames=100,
        first_center_offset_frames=900,
    )


# ---------------------------------------------------------------------------
# common_center_windows — correctness
# ---------------------------------------------------------------------------

def test_common_center_19_windows(ccw):
    assert len(ccw[400]) == 19
    assert len(ccw[500]) == 19
    assert len(ccw[600]) == 19


def test_common_center_starts_correct_20s(ccw):
    expected = [
        700, 800, 900, 1000, 1100, 1200, 1300, 1400, 1500,
        1600, 1700, 1800, 1900, 2000, 2100, 2200, 2300, 2400, 2500,
    ]
    assert [s for s, _ in ccw[400]] == expected


def test_common_center_starts_correct_25s(ccw):
    expected = [
        650, 750, 850, 950, 1050, 1150, 1250, 1350, 1450,
        1550, 1650, 1750, 1850, 1950, 2050, 2150, 2250, 2350, 2450,
    ]
    assert [s for s, _ in ccw[500]] == expected


def test_common_center_starts_correct_30s(ccw):
    expected = [
        600, 700, 800, 900, 1000, 1100, 1200, 1300, 1400,
        1500, 1600, 1700, 1800, 1900, 2000, 2100, 2200, 2300, 2400,
    ]
    assert [s for s, _ in ccw[600]] == expected


def test_common_center_all_lengths_same_count(ccw):
    assert len(ccw[400]) == len(ccw[500]) == len(ccw[600])


def test_common_center_all_windows_within_bounds(ccw):
    for length, windows in ccw.items():
        for start, end in windows:
            assert start >= 600, f"length={length}: start {start} < trim 600"
            assert end <= 3000, f"length={length}: end {end} > total 3000"


def test_common_center_no_partial_windows(ccw):
    for length, windows in ccw.items():
        for start, end in windows:
            assert end - start == length, (
                f"length={length}: window ({start},{end}) has width {end - start}"
            )


# ---------------------------------------------------------------------------
# common_center_windows — validation errors
# ---------------------------------------------------------------------------

def test_common_center_raises_if_first_center_cannot_support_all_lengths():
    # 30 s window: start = 650 - 300 = 350 < 600 (trim_frames)
    with pytest.raises(ValueError):
        common_center_windows(
            total_frames=3000,
            trim_frames=600,
            window_lengths_frames=[400, 500, 600],
            center_spacing_frames=100,
            first_center_offset_frames=650,
        )


def test_common_center_raises_if_no_valid_centers():
    # Only candidate center is 2900; end = 2900 + 300 = 3200 > 3000
    with pytest.raises(ValueError):
        common_center_windows(
            total_frames=3000,
            trim_frames=600,
            window_lengths_frames=[600],
            center_spacing_frames=100,
            first_center_offset_frames=2900,
        )


def test_common_center_raises_on_odd_window_length():
    with pytest.raises(ValueError):
        common_center_windows(
            total_frames=3000,
            trim_frames=600,
            window_lengths_frames=[401],
            center_spacing_frames=100,
            first_center_offset_frames=900,
        )


def test_common_center_raises_on_duplicate_lengths():
    with pytest.raises(ValueError):
        common_center_windows(
            total_frames=3000,
            trim_frames=600,
            window_lengths_frames=[400, 400],
            center_spacing_frames=100,
            first_center_offset_frames=900,
        )


def test_common_center_raises_on_bool_argument():
    base = dict(
        trim_frames=600,
        window_lengths_frames=[400, 500, 600],
        center_spacing_frames=100,
        first_center_offset_frames=900,
    )
    with pytest.raises(ValueError):
        common_center_windows(total_frames=True, **base)

    base2 = dict(
        total_frames=3000,
        window_lengths_frames=[400, 500, 600],
        center_spacing_frames=100,
        first_center_offset_frames=900,
    )
    with pytest.raises(ValueError):
        common_center_windows(trim_frames=False, **base2)


# ---------------------------------------------------------------------------
# sliding_windows — correctness
# ---------------------------------------------------------------------------

def test_sliding_windows_reproduces_exp002_baseline():
    expected = [
        (600, 1000), (700, 1100), (800, 1200), (900, 1300), (1000, 1400),
        (1100, 1500), (1200, 1600), (1300, 1700), (1400, 1800), (1500, 1900),
        (1600, 2000), (1700, 2100), (1800, 2200), (1900, 2300), (2000, 2400),
        (2100, 2500), (2200, 2600), (2300, 2700), (2400, 2800), (2500, 2900),
        (2600, 3000),
    ]
    assert sliding_windows(3000, 600, 400, 100) == expected


def test_sliding_windows_drops_partial_exact():
    # total_frames=3001: window at 2600 ends at 3000 ≤ 3001 (included);
    # window at 2700 ends at 3100 > 3001 (dropped).
    result = sliding_windows(total_frames=3001, trim_frames=600,
                             window_frames=400, hop_frames=100)
    assert len(result) == 21
    assert result[-1] == (2600, 3000)


# ---------------------------------------------------------------------------
# sliding_windows — validation errors
# ---------------------------------------------------------------------------

def test_sliding_windows_raises_if_no_full_window():
    # 2700 + 400 = 3100 > 3000
    with pytest.raises(ValueError):
        sliding_windows(total_frames=3000, trim_frames=2700,
                        window_frames=400, hop_frames=100)


def test_sliding_windows_raises_on_bool_argument():
    with pytest.raises(ValueError):
        sliding_windows(total_frames=3000, trim_frames=600,
                        window_frames=400, hop_frames=True)
