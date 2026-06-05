"""IWR1642 + DCA1000 raw ADC reader (STUB — complete once your chirp config is fixed).

WHY THIS IS A STUB: the exact byte layout depends on parameters you set in mmWave Studio
(ADC samples per chirp, RX/TX count, chirps per frame, frame count) and the DCA1000 LVDS
lane mode. This file gives the correct *shape* of the reader and the IWR1642-specific
detail; fill the reshape once the config is final, and verify against the TI raw ADC data
capture / DCA1000 data-format application note.  [CITATION NEEDED]

IWR1642-specific facts:
- Data is 16-bit signed (2's complement), complex (I and Q).
- The 1642 stores data NON-interleaved (unlike interleaved 12xx/1443). Confirm against
  the app note for your LVDS lane setting before trusting the reshape.

The downstream vitals pipeline (vitals.py) only needs, per frame, a complex range profile
(or the raw cube to build one). Keep that contract stable so vitals.py is unaffected by
how this reader is finished.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import numpy as np


@dataclass
class ChirpConfig:
    """Capture geometry — fill from your mmWave Studio profile/frame config."""
    num_adc_samples: int       # ADC samples per chirp (fast time)
    num_rx: int                # active RX antennas (IWR1642: up to 4)
    num_tx: int                # active TX (IWR1642: up to 2)
    num_chirps_per_frame: int  # loops per frame (per TX)
    num_frames: int            # total frames captured
    frame_rate_hz: float       # = slow-time / phase sample rate (target ~20 Hz)
    range_resolution_m: float  # c / (2 * sweep_bandwidth); compute from your profile
    # NOTE: enforce Low Power ADC mode + IF bandwidth <= 5 MHz in the profile (TI xWR1642).


def read_adc_bin(bin_path: str | Path, cfg: ChirpConfig) -> np.ndarray:
    """Read a DCA1000 raw .bin into a complex radar cube.

    TARGET output shape: (num_frames, num_chirps_per_frame, num_rx, num_adc_samples),
    complex64.

    TODO(you): implement the IWR1642 non-interleaved I/Q reshape for your lane mode,
    validated against the TI data-format app note. Until then this raises, so nothing
    silently produces wrong numbers (CLAUDE.md s.4).
    """
    bin_path = Path(bin_path)
    raw = np.fromfile(bin_path, dtype=np.int16)  # 16-bit signed, 2's complement
    _ = raw  # silence linters until implemented
    raise NotImplementedError(
        "Implement the IWR1642 non-interleaved I/Q reshape for your DCA1000 lane mode. "
        "Verify byte order against the TI raw ADC data-capture app note, then return a "
        "(frames, chirps, rx, samples) complex64 cube. Add a round-trip unit test."
    )


def range_profile(cube: np.ndarray, window_fn=np.hanning) -> np.ndarray:
    """Range FFT along fast time (last axis) -> complex range profile.

    Input:  (frames, chirps, rx, samples) complex
    Output: (frames, chirps, rx, samples) complex (range bins along last axis)
    """
    n = cube.shape[-1]
    win = window_fn(n).astype(cube.dtype)
    return np.fft.fft(cube * win, axis=-1)


def range_axis_m(num_samples: int, range_resolution_m: float) -> np.ndarray:
    """Range (metres) for each range bin index."""
    return np.arange(num_samples) * range_resolution_m
