"""M4 Stage 2 — the frozen non-overlapping window grid.

Transcribed from `notes/analysis_prespec.md` §7, which is FROZEN. Where this module and
that document disagree, **the document wins and this module is the bug**.

The grid, verbatim in effect:

* 30 s = **600 frames** at 20 Hz; windows are the consecutive, non-overlapping, **half-open
  frame intervals** `[k·600, (k+1)·600)`, indexed by **frame number, never wall-clock**
  (`elapsed_s` in replay NPZs is wall-clock and unusable — HANDOFF §5).
* **`k = 0` IS scored.** Warmup bin-selection runs on the `k = 0` buffer and is applied back
  to that same buffer, so the window is real, not a warmup discard.
* A window is scored **iff it is a complete 600 frames**; the incomplete tail is dropped.
* The reference span for window `k` is the half-open epoch interval
  `[E(k·600), E((k+1)·600))` where `E(i) = frame0_epoch + i / fs`. A Masimo sample at integer
  `epoch_utc = e` belongs iff `E(k·600) ≤ e < E((k+1)·600)`. **HR and BR share this rule**
  (M3R-40) — the HR comparator was harmonised from a closed span to this half-open one.
* `frame0_epoch` may be **fractional**; endpoint inclusion is tested for that case.
* Yields: 180 s → 6, 480 s → 16, 600 s → exactly 20.
* **Boundary-aligned, never greedy.** There is no "pick the best-matching estimate" here:
  window `k` is scored by the estimate whose analysis window is exactly `[k·600, (k+1)·600)`.
  Greedy selection would maximise accepted windows and is estimator-dependent.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: FROZEN by `notes/analysis_prespec.md` §7. Not a tunable — a change here is an amendment
#: to a pre-registered analysis decision, not a config edit.
WINDOW_SECONDS = 30.0
FRAME_RATE_HZ = 20.0
FRAMES_PER_WINDOW = 600


class WindowGridError(ValueError):
    """Raised when the grid is asked for something the frozen spec does not define."""


def frames_per_window(window_s: float = WINDOW_SECONDS, fs: float = FRAME_RATE_HZ) -> int:
    """Frames in one window, requiring an exact integer.

    A non-integer product means the requested (window_s, fs) pair cannot express the frozen
    half-open frame grid at all, so it raises rather than rounding: a silently rounded
    window length would shift every boundary and every reference span with it.
    """
    if not np.isfinite(window_s) or window_s <= 0:
        raise WindowGridError(f"window_s must be finite and > 0, got {window_s!r}")
    if not np.isfinite(fs) or fs <= 0:
        raise WindowGridError(f"fs must be finite and > 0, got {fs!r}")
    exact = window_s * fs
    n = int(round(exact))
    if abs(exact - n) > 1e-9:
        raise WindowGridError(
            f"window_s * fs = {exact!r} is not an integer number of frames "
            f"({window_s} s x {fs} Hz). The frozen grid is defined on whole frames."
        )
    return n


def n_complete_windows(n_frames: int, frames_per_win: int = FRAMES_PER_WINDOW) -> int:
    """How many complete windows a capture of `n_frames` frames yields.

    Floor division, because a window is scored **iff** it is complete: the trailing partial
    window is dropped, never padded and never scored short.
    """
    if frames_per_win <= 0:
        raise WindowGridError(f"frames_per_win must be > 0, got {frames_per_win!r}")
    if n_frames < 0:
        raise WindowGridError(f"n_frames must be >= 0, got {n_frames!r}")
    return int(n_frames) // int(frames_per_win)


def window_frame_span(k: int, frames_per_win: int = FRAMES_PER_WINDOW) -> tuple[int, int]:
    """Half-open frame interval `[k·N, (k+1)·N)` for window `k`."""
    if k < 0:
        raise WindowGridError(f"window index k must be >= 0, got {k!r}")
    if frames_per_win <= 0:
        raise WindowGridError(f"frames_per_win must be > 0, got {frames_per_win!r}")
    start = int(k) * int(frames_per_win)
    return start, start + int(frames_per_win)


def window_reference_span(
    k: int,
    frame0_epoch: float,
    fs: float = FRAME_RATE_HZ,
    frames_per_win: int = FRAMES_PER_WINDOW,
) -> tuple[float, float]:
    """Half-open epoch span `[E(k·N), E((k+1)·N))`, `E(i) = frame0_epoch + i / fs`.

    `frame0_epoch` may be fractional — it is the synchronised UTC time at receipt of frame 0,
    not an integer second, and **never** `start_wall_utc` (written before configuration).
    """
    if not np.isfinite(frame0_epoch):
        raise WindowGridError(f"frame0_epoch must be finite, got {frame0_epoch!r}")
    if not np.isfinite(fs) or fs <= 0:
        raise WindowGridError(f"fs must be finite and > 0, got {fs!r}")
    start_frame, end_frame = window_frame_span(k, frames_per_win)
    return frame0_epoch + start_frame / fs, frame0_epoch + end_frame / fs


def reference_sample_mask(
    epochs,
    k: int,
    frame0_epoch: float,
    fs: float = FRAME_RATE_HZ,
    frames_per_win: int = FRAMES_PER_WINDOW,
) -> np.ndarray:
    """Boolean mask of which reference samples fall in window `k`.

    Half-open at the upper end (`lo <= e < hi`) so a sample on a boundary second belongs to
    exactly one window. A closed span would double-count that second across adjacent windows
    — the defect M3R-40 harmonised away.
    """
    lo, hi = window_reference_span(k, frame0_epoch, fs, frames_per_win)
    e = np.asarray(epochs, dtype=float)
    return (e >= lo) & (e < hi)


@dataclass(frozen=True)
class Window:
    """One grid window: its index, frame span and reference epoch span."""

    k: int
    frame_start: int
    frame_end: int          # exclusive
    epoch_start: float
    epoch_end: float        # exclusive


def build_window_grid(
    n_frames: int,
    frame0_epoch: float,
    fs: float = FRAME_RATE_HZ,
    frames_per_win: int = FRAMES_PER_WINDOW,
) -> list[Window]:
    """Every complete window of a capture, in order, `k = 0 … n-1`.

    Boundary-aligned by construction: the windows are generated from the grid, never
    selected from a pool of candidate estimates.
    """
    return [
        Window(k, *window_frame_span(k, frames_per_win),
               *window_reference_span(k, frame0_epoch, fs, frames_per_win))
        for k in range(n_complete_windows(n_frames, frames_per_win))
    ]
