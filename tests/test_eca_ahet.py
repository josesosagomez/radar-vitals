"""Unit tests for ECA + AHET harmonic rejection (exp002).

Tests verify each new DSP stage in isolation — the "verify before chaining" rule
from CLAUDE.md §5.3. Reference: arXiv:2503.07062 (Tang et al., 2025).

Signal model used throughout:
  fs=20 Hz, N=400 (20 s window), f_r=0.25 Hz (15 bpm), f_h=71/60 Hz (~71 bpm).
  Respiratory harmonics up to 4th order with decreasing amplitude simulate the
  real-capture failure mode: 4th harmonic (60 bpm) dominates the cardiac band
  before ECA and would win a naive argmax.

Run: pytest tests/test_eca_ahet.py -v
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import vitals  # noqa: E402

FS = 20.0
N  = 400                # 20 s window at 20 Hz
F_R = 0.25              # 15 bpm respiration
F_H = 71.0 / 60.0       # ~1.1833 Hz = 71 bpm cardiac


def _make_harmonic_signal(seed: int = 0) -> np.ndarray:
    """Chest phase with 4 respiratory harmonics + cardiac fundamental + 2nd harmonic.

    Amplitudes chosen so the 4th respiratory harmonic (1.0 Hz = 60 bpm) exceeds
    the cardiac fundamental (1.183 Hz = 71 bpm) in the raw spectrum — the exact
    failure mode that defeated exp001.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(N) / FS
    sig = (
        3.00 * np.sin(2 * np.pi * 1 * F_R * t) +   # resp fundamental
        1.50 * np.sin(2 * np.pi * 2 * F_R * t) +   # resp 2nd harmonic
        1.00 * np.sin(2 * np.pi * 3 * F_R * t) +   # resp 3rd harmonic
        0.75 * np.sin(2 * np.pi * 4 * F_R * t) +   # resp 4th harmonic — 60 bpm
        0.30 * np.sin(2 * np.pi * 1 * F_H * t) +   # cardiac fundamental
        0.15 * np.sin(2 * np.pi * 2 * F_H * t) +   # cardiac 2nd harmonic
        0.03 * rng.standard_normal(N)
    )
    return sig


def test_eca_removes_respiratory_harmonic():
    """ECA projection removes the 4th respiratory harmonic from the cardiac band.

    Before ECA: 4th harmonic at 1.0 Hz (60 bpm, amplitude 0.75) dominates over
    the cardiac at 1.183 Hz (71 bpm, amplitude 0.30).
    After ECA: cardiac should be the dominant peak in [0.8, 2.0] Hz.
    """
    sig = _make_harmonic_signal()

    # Verify that before ECA the 4th harmonic (1.0 Hz) wins the argmax in cardiac band
    raw_spec = np.abs(np.fft.rfft(sig * np.hanning(N)))
    freqs = np.fft.rfftfreq(N, d=1.0 / FS)
    cardiac_mask = (freqs >= 0.8) & (freqs <= 2.0)
    raw_peak_hz = freqs[cardiac_mask][np.argmax(raw_spec[cardiac_mask])]
    assert abs(raw_peak_hz - 1.0) < 0.1, (
        f"Pre-condition failed: expected 4th harmonic (~1.0 Hz) to dominate raw, "
        f"got {raw_peak_hz:.3f} Hz"
    )

    # After ECA the cardiac (1.183 Hz) should dominate
    sig_eca = vitals.eca_project(sig, F_R, FS)
    eca_spec = np.abs(np.fft.rfft(sig_eca * np.hanning(N)))
    eca_peak_hz = freqs[cardiac_mask][np.argmax(eca_spec[cardiac_mask])]
    assert abs(eca_peak_hz - F_H) < 0.1, (
        f"After ECA expected cardiac peak near {F_H:.3f} Hz, got {eca_peak_hz:.3f} Hz"
    )


def test_ahet_refinement_improves_accuracy():
    """ECA + AHET recovers HR within 1.5 bpm and sets ahet_verified=True.

    The 3 bpm FFT bin width (20 s @ 20 Hz) limits argmax precision; parabolic
    interpolation + 2nd-harmonic blending should tighten the estimate to <1.5 bpm.
    """
    sig = _make_harmonic_signal()
    out = vitals.estimate_rate_from_phase(sig, FS, vitals.HEART_BAND_HZ, f_r_hz=F_R)

    assert out["ahet_verified"] is True, (
        f"Expected AHET to verify the cardiac peak; got ahet_verified={out['ahet_verified']}, "
        f"harmonic_suspect={out['harmonic_suspect']}"
    )
    assert not np.isnan(out["rate_bpm"]), "rate_bpm should not be NaN when AHET verifies"
    assert abs(out["rate_bpm"] - 71.0) <= 1.5, (
        f"Expected HR within 1.5 bpm of 71; got {out['rate_bpm']:.2f} bpm"
    )


def test_nan_on_no_credible_candidate():
    """Signal with only respiration harmonics (no cardiac) returns NaN + harmonic_suspect=True.

    After ECA removes the respiratory subspace, the cardiac band contains only
    noise. No candidate can pass the AHET 2nd-harmonic check, so the pipeline
    returns NaN rather than fabricating a value (CLAUDE.md §4).
    """
    t = np.arange(N) / FS
    # Purely sinusoidal respiratory harmonics, no cardiac, no noise.
    # After ECA the cardiac band is numerically zero — no AHET check can pass.
    sig = (
        3.00 * np.sin(2 * np.pi * 1 * F_R * t) +
        1.50 * np.sin(2 * np.pi * 2 * F_R * t) +
        1.00 * np.sin(2 * np.pi * 3 * F_R * t) +
        0.75 * np.sin(2 * np.pi * 4 * F_R * t)
    )
    out = vitals.estimate_rate_from_phase(sig, FS, vitals.HEART_BAND_HZ, f_r_hz=F_R)

    assert np.isnan(out["rate_bpm"]), (
        f"Expected NaN for a respiration-only signal; got {out['rate_bpm']:.1f} bpm"
    )
    assert out["harmonic_suspect"] is True
    assert out["ahet_verified"] is False
