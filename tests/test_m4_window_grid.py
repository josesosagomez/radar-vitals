"""M4 Stage 2 — the frozen window grid (`notes/analysis_prespec.md` §7).

The yields (6 / 16 / 20) and the endpoint rule are transcribed from a FROZEN document, so
these tests are hand-computed against it rather than against the implementation.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.m4.window_grid import (  # noqa: E402
    FRAMES_PER_WINDOW,
    FRAME_RATE_HZ,
    WINDOW_SECONDS,
    Window,
    WindowGridError,
    build_window_grid,
    frames_per_window,
    n_complete_windows,
    reference_sample_mask,
    window_frame_span,
    window_reference_span,
)


def test_frozen_constants_match_the_prespec():
    """30 s at 20 Hz = 600 frames. These are FROZEN, not tunables."""
    assert (WINDOW_SECONDS, FRAME_RATE_HZ, FRAMES_PER_WINDOW) == (30.0, 20.0, 600)
    assert frames_per_window() == 600


def test_frames_per_window_refuses_a_non_integer_grid():
    """A rounded window length would shift every boundary and every reference span."""
    assert frames_per_window(window_s=30.0, fs=20.5) == 615   # 615.0 IS integral: accepted
    with pytest.raises(WindowGridError, match="not an integer number of frames"):
        frames_per_window(window_s=30.0, fs=20.05)            # 601.5 is not
    with pytest.raises(WindowGridError, match="not an integer number of frames"):
        frames_per_window(window_s=0.5, fs=3.0)               # 1.5 is not
    for bad in (0, -1, float("nan"), float("inf")):
        with pytest.raises(WindowGridError):
            frames_per_window(window_s=bad)
        with pytest.raises(WindowGridError):
            frames_per_window(fs=bad)


# ── Yields, hand-computed from the frozen spec ────────────────────────────────

@pytest.mark.parametrize(
    "duration_s, expected",
    [(180.0, 6), (480.0, 16), (600.0, 20)],   # §7: "180 s -> 6; 480 s -> 16; 600 s -> exactly 20"
)
def test_yields_match_the_prespec_arithmetic(duration_s, expected):
    assert n_complete_windows(int(duration_s * FRAME_RATE_HZ)) == expected


def test_incomplete_tail_is_dropped_never_scored_short():
    """A window is scored IFF it is a complete 600 frames."""
    assert n_complete_windows(599) == 0
    assert n_complete_windows(600) == 1
    assert n_complete_windows(1199) == 1
    assert n_complete_windows(1200) == 2
    assert n_complete_windows(0) == 0


def test_real_capture_frame_counts_yield_the_recorded_window_counts():
    """The three Masimo captures, at their actual frame counts (HANDOFF §2 / the M0
    evidence memo): natural 6, paced16 6, sweep 16."""
    assert n_complete_windows(3610) == 6      # natural
    assert n_complete_windows(3611) == 6      # paced16
    assert n_complete_windows(9611) == 16     # sweep


# ── Frame spans ───────────────────────────────────────────────────────────────

def test_frame_spans_are_half_open_and_contiguous():
    assert window_frame_span(0) == (0, 600)
    assert window_frame_span(1) == (600, 1200)
    assert window_frame_span(19) == (11400, 12000)
    # Contiguous with no overlap and no gap: window k ends exactly where k+1 begins.
    for k in range(5):
        assert window_frame_span(k)[1] == window_frame_span(k + 1)[0]


def test_k0_is_a_real_window():
    """§7 is explicit: 'The first window k = 0 ([0, 30) s) IS scored'. Warmup
    bin-selection runs on that buffer and is applied back to it."""
    assert window_frame_span(0) == (0, 600)
    assert n_complete_windows(600) == 1


def test_negative_window_index_is_refused():
    with pytest.raises(WindowGridError, match="k must be >= 0"):
        window_frame_span(-1)


# ── Reference spans and the half-open endpoint rule ───────────────────────────

def test_reference_span_uses_E_i_equals_frame0_plus_i_over_fs():
    lo, hi = window_reference_span(0, frame0_epoch=1000.0)
    assert (lo, hi) == (1000.0, 1030.0)
    lo, hi = window_reference_span(2, frame0_epoch=1000.0)
    assert (lo, hi) == (1060.0, 1090.0)


def test_boundary_second_belongs_to_exactly_one_window_integer_origin():
    """Half-open: `E(k·600) <= e < E((k+1)·600)`. A closed span would double-count the
    boundary second across adjacent windows — the defect M3R-40 harmonised away."""
    epochs = np.array([1000, 1029, 1030, 1059, 1060])
    in0 = reference_sample_mask(epochs, 0, frame0_epoch=1000.0)
    in1 = reference_sample_mask(epochs, 1, frame0_epoch=1000.0)
    assert in0.tolist() == [True, True, False, False, False]
    assert in1.tolist() == [False, False, True, True, False]
    assert not (in0 & in1).any(), "no sample may belong to two windows"


def test_boundary_inclusion_under_a_FRACTIONAL_frame0_epoch():
    """§7 requires this case explicitly: `frame0_epoch` may be fractional, and endpoint
    inclusion must be tested for it. With origin 1000.5 the window-0 span is
    [1000.5, 1030.5), so epoch 1000 is BEFORE the capture and 1030 is inside window 0 —
    the opposite of the integer-origin case above."""
    epochs = np.array([1000, 1001, 1030, 1031])
    in0 = reference_sample_mask(epochs, 0, frame0_epoch=1000.5)
    in1 = reference_sample_mask(epochs, 1, frame0_epoch=1000.5)
    assert in0.tolist() == [False, True, True, False]
    assert in1.tolist() == [False, False, False, True]
    assert not (in0 & in1).any()


def test_every_integer_second_lands_in_exactly_one_window_fractional_origin():
    """The partition property itself, swept over a fractional origin: across the whole
    capture no second is dropped and none is counted twice."""
    frame0 = 1234.75
    n_win = 6
    epochs = np.arange(1234, 1234 + 30 * n_win + 2)
    counts = np.zeros(epochs.shape, dtype=int)
    for k in range(n_win):
        counts += reference_sample_mask(epochs, k, frame0_epoch=frame0).astype(int)
    assert counts.max() <= 1, "a second landed in two windows"
    inside = (epochs >= frame0) & (epochs < frame0 + 30 * n_win)
    assert (counts[inside] == 1).all(), "a second inside the grid landed in no window"


def test_reference_span_rejects_a_non_finite_origin():
    for bad in (float("nan"), float("inf")):
        with pytest.raises(WindowGridError, match="frame0_epoch must be finite"):
            window_reference_span(0, frame0_epoch=bad)


# ── The assembled grid ────────────────────────────────────────────────────────

def test_build_window_grid_is_boundary_aligned_and_ordered():
    grid = build_window_grid(n_frames=3610, frame0_epoch=1000.0)
    assert len(grid) == 6
    assert [w.k for w in grid] == [0, 1, 2, 3, 4, 5]
    assert all(isinstance(w, Window) for w in grid)
    assert grid[0].frame_start == 0 and grid[0].frame_end == 600
    assert grid[-1].frame_end == 3600 <= 3610       # tail of 10 frames dropped
    for a, b in zip(grid, grid[1:]):
        assert a.frame_end == b.frame_start
        assert a.epoch_end == pytest.approx(b.epoch_start)


def test_grid_is_generated_not_selected():
    """Boundary-aligned, never greedy: the grid is a function of frame count and origin
    only. Nothing about estimates, acceptance or quality can influence it — greedy
    selection would maximise accepted windows and is estimator-dependent (§7)."""
    import inspect

    params = set(inspect.signature(build_window_grid).parameters)
    assert params == {"n_frames", "frame0_epoch", "fs", "frames_per_win"}


# ── The grid API is FROZEN, not parameterised (S12R-08) ───────────────────────

@pytest.mark.parametrize("fs", [10.0, 20.5, 19.999, 40.0, 1.0])
def test_the_scoring_grid_refuses_a_non_frozen_frame_rate(fs):
    """`build_window_grid(1200, 1000.0, fs=10.0)` silently returned two 600-frame windows
    whose reference spans were **60 s each**, while every docstring still said 30 s. The
    estimand changed and nothing raised.

    The parameters exist so the frozen values are visible at the call site, not so a
    different grid can be built: `notes/analysis_prespec.md` §7 freezes 30 s = 600 frames at
    20 Hz and `E(i) = frame0_epoch + i/20`, and changing either shifts every window boundary
    and every reference span with it.
    """
    with pytest.raises(WindowGridError, match="FROZEN"):
        build_window_grid(1200, 1000.0, fs=fs)
    with pytest.raises(WindowGridError, match="FROZEN"):
        window_reference_span(0, 1000.0, fs=fs)
    with pytest.raises(WindowGridError, match="FROZEN"):
        reference_sample_mask([1000.0], 0, 1000.0, fs=fs)


@pytest.mark.parametrize("n", [1, 599, 601, 300, 1200])
def test_the_scoring_grid_refuses_a_non_frozen_window_length(n):
    with pytest.raises(WindowGridError, match="FROZEN"):
        build_window_grid(12000, 1000.0, frames_per_win=n)
    with pytest.raises(WindowGridError, match="FROZEN"):
        window_frame_span(0, frames_per_win=n)
    with pytest.raises(WindowGridError, match="FROZEN"):
        n_complete_windows(12000, frames_per_win=n)


def test_a_capture_shorter_than_one_window_still_validates_the_grid():
    """The gap that made this worth an explicit check: with `n_frames < 600` the comprehension
    body never runs, so a non-frozen `fs` would have slipped through and returned `[]`."""
    with pytest.raises(WindowGridError, match="FROZEN"):
        build_window_grid(100, 1000.0, fs=10.0)
    assert build_window_grid(100, 1000.0) == []


def test_the_frozen_defaults_are_still_accepted_everywhere():
    """The guard must not break the real path: passing the frozen values explicitly is
    exactly how the plan wants call sites to read."""
    explicit = build_window_grid(12000, 1000.0, fs=20.0, frames_per_win=600)
    assert explicit == build_window_grid(12000, 1000.0)
    assert len(explicit) == 20
    assert explicit[0].epoch_end - explicit[0].epoch_start == pytest.approx(30.0)
