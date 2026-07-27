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


def _exact_index(value, name: str) -> int:
    """An exact integer frame/window index — no coercion (S12R-08 R2).

    `int(600.5)` is 600 and `int(1.9)` is 1, so validating a *coerced copy* while the caller
    keeps the original let `frames_per_win=600.5` and `k=1.9` through silently: the grid
    then addressed a different window than the argument named, while still returning
    plausible spans. §7 defines the grid on integer frame numbers, so anything else is a
    caller defect, not a value to round. `bool` is excluded because `type(True) is bool`.
    """
    if type(value) is not int:
        raise WindowGridError(
            f"{name}={value!r} must be an exact integer (got {type(value).__name__}); the "
            "frozen grid of `notes/analysis_prespec.md` §7 is defined on whole frames and "
            "nothing here rounds."
        )
    return value


def _require_frozen_grid(fs, frames_per_win) -> tuple[float, int]:
    """Reject any (fs, frames_per_win) pair that is not **exactly** the frozen grid.

    The parameters exist so the frozen values are *visible at the call site* rather than
    buried as literals — not so a different grid can be built. Accepting `fs=10.0` silently
    produced 60 s reference spans while every docstring still said 30 s: the estimand changed
    and nothing raised. A different grid is an amendment to a pre-registered analysis decision
    (`notes/analysis_prespec.md` §4 amendment mechanism), never a keyword argument.

    Returns the **validated** values, which callers must use in place of their arguments —
    validating a coerced copy and then computing from the original is what S12R-08 R2 found.
    """
    frames = _exact_index(frames_per_win, "frames_per_win")
    if type(fs) not in (int, float) or not np.isfinite(fs):
        raise WindowGridError(
            f"fs={fs!r} must be a finite number (got {type(fs).__name__})."
        )
    if float(fs) != FRAME_RATE_HZ or frames != FRAMES_PER_WINDOW:
        raise WindowGridError(
            f"the M4 scoring grid is FROZEN at fs={FRAME_RATE_HZ:g} Hz and "
            f"{FRAMES_PER_WINDOW} frames/window ({WINDOW_SECONDS:g} s), got fs={fs!r} and "
            f"frames_per_win={frames_per_win!r}. `notes/analysis_prespec.md` §7 fixes "
            "30 s = 600 frames at 20 Hz and E(i) = frame0_epoch + i/20; changing either "
            "shifts every window boundary and every reference span. If you need generic "
            "window arithmetic for diagnostics, do it outside the scoring grid."
        )
    return float(fs), frames


def frames_per_window(window_s: float = WINDOW_SECONDS, fs: float = FRAME_RATE_HZ) -> int:
    """Frames in one window, requiring an exact integer.

    A non-integer product means the requested (window_s, fs) pair cannot express the frozen
    half-open frame grid at all, so it raises rather than rounding: a silently rounded
    window length would shift every boundary and every reference span with it.

    This is the one deliberately generic helper — it *derives* the frozen 600 from the frozen
    30 s x 20 Hz so the arithmetic is visible and testable. It does not build a grid, so it
    does not gate on `_require_frozen_grid`; every function that returns frame or epoch spans
    does.
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
    _, frames = _require_frozen_grid(FRAME_RATE_HZ, frames_per_win)
    n = _exact_index(n_frames, "n_frames")
    if n < 0:
        raise WindowGridError(f"n_frames must be >= 0, got {n_frames!r}")
    return n // frames


def window_frame_span(k: int, frames_per_win: int = FRAMES_PER_WINDOW) -> tuple[int, int]:
    """Half-open frame interval `[k·N, (k+1)·N)` for window `k`."""
    _, frames = _require_frozen_grid(FRAME_RATE_HZ, frames_per_win)
    idx = _exact_index(k, "k")
    if idx < 0:
        raise WindowGridError(f"window index k must be >= 0, got {k!r}")
    start = idx * frames
    return start, start + frames


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
    rate, frames = _require_frozen_grid(fs, frames_per_win)
    if type(frame0_epoch) not in (int, float) or not np.isfinite(frame0_epoch):
        raise WindowGridError(
            f"frame0_epoch={frame0_epoch!r} must be a finite number "
            f"(got {type(frame0_epoch).__name__})."
        )
    origin = float(frame0_epoch)
    start_frame, end_frame = window_frame_span(k, frames)
    return origin + start_frame / rate, origin + end_frame / rate


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
    # No frozen-grid guard here: `window_reference_span` is called unconditionally below and
    # carries it. A duplicate guard would be a line no test could ever fail on — which is
    # how vacuous checks accumulate.
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
    # Gate here explicitly: a capture shorter than one window yields an empty list without
    # ever reaching `window_reference_span`, so a non-frozen `fs` would pass unnoticed.
    rate, frames = _require_frozen_grid(fs, frames_per_win)
    return [
        Window(k, *window_frame_span(k, frames),
               *window_reference_span(k, frame0_epoch, rate, frames))
        for k in range(n_complete_windows(n_frames, frames))
    ]
