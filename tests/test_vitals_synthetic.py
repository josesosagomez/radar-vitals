"""Synthetic sanity checks for the vitals DSP — no hardware needed.

This is the canonical "verify each stage in isolation" test from CLAUDE.md s.5. If this
fails, the heart-rate estimator is broken regardless of the radar. Run: pytest tests/
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import vitals  # noqa: E402


def make_phase(hr_bpm, rr_bpm=18.0, fs=20.0, dur_s=60.0, noise=0.05, seed=0):
    """Synthetic chest-phase signal: a cardiac tone + a stronger respiration tone + noise."""
    rng = np.random.default_rng(seed)
    t = np.arange(0, dur_s, 1.0 / fs)
    heart = np.sin(2 * np.pi * (hr_bpm / 60.0) * t)
    resp = 3.0 * np.sin(2 * np.pi * (rr_bpm / 60.0) * t)   # respiration is larger than heartbeat
    return heart + resp + noise * rng.standard_normal(len(t)), fs


@pytest.mark.parametrize("hr", [54.0, 72.0, 96.0, 110.0])
def test_recovers_heart_rate(hr):
    phase, fs = make_phase(hr)
    out = vitals.estimate_rate_from_phase(phase, fs, vitals.HEART_BAND_HZ)
    assert abs(out["rate_bpm"] - hr) <= 2.0, f"got {out['rate_bpm']:.1f}, expected {hr}"


def test_respiration_does_not_capture_heart_band():
    """Even with respiration dominant, the heart band should lock to the cardiac tone."""
    phase, fs = make_phase(hr_bpm=72.0, rr_bpm=18.0)
    out = vitals.estimate_rate_from_phase(phase, fs, vitals.HEART_BAND_HZ)
    assert 48.0 <= out["rate_bpm"] <= 120.0
    assert abs(out["rate_bpm"] - 72.0) <= 2.0


def test_recovers_respiration_rate():
    phase, fs = make_phase(hr_bpm=72.0, rr_bpm=18.0)
    out = vitals.estimate_rate_from_phase(phase, fs, vitals.RESP_BAND_HZ)
    assert abs(out["rate_bpm"] - 18.0) <= 2.0


# ---------------------------------------------------------------------------
# parabolic_interpolate_peak unit tests
# ---------------------------------------------------------------------------

def test_parabolic_symmetric_peak():
    """Symmetric parabola (alpha==gamma) gives delta=0 -> raw bin centre."""
    spec = np.zeros(10)
    spec[4] = 1.0
    spec[5] = 2.0
    spec[6] = 1.0
    freq_res = 0.05
    result = vitals.parabolic_interpolate_peak(spec, 5, freq_res)
    assert abs(result - 5 * freq_res) < 1e-12


def test_parabolic_off_centre_peak():
    """Asymmetric parabola gives expected sub-bin shift."""
    spec = np.zeros(10)
    spec[4] = 3.0
    spec[5] = 4.0
    spec[6] = 2.0
    freq_res = 0.05
    # delta = 0.5*(3-2)/(3-8+2) = 0.5/(−3) = −1/6
    delta_expected = 0.5 * (3.0 - 2.0) / (3.0 - 8.0 + 2.0)
    expected = (5 + delta_expected) * freq_res
    result = vitals.parabolic_interpolate_peak(spec, 5, freq_res)
    assert abs(result - expected) < 1e-6


def test_parabolic_left_edge_fallback():
    """peak_idx==0 must return raw bin centre without exception."""
    spec = np.ones(10)
    result = vitals.parabolic_interpolate_peak(spec, 0, 0.05)
    assert result == 0.0


def test_parabolic_right_edge_fallback():
    """peak_idx==len-1 must return raw bin centre without exception."""
    spec = np.ones(10)
    result = vitals.parabolic_interpolate_peak(spec, 9, 0.05)
    assert abs(result - 9 * 0.05) < 1e-12


def test_parabolic_flat_top_fallback():
    """Denominator==0 (flat top) must return raw bin centre without exception."""
    spec = np.ones(10)
    freq_res = 0.05
    result = vitals.parabolic_interpolate_peak(spec, 5, freq_res)
    assert abs(result - 5 * freq_res) < 1e-12


def test_parabolic_large_delta_fallback():
    """When |delta|>1 the function must return the raw bin centre."""
    spec = np.zeros(10)
    # alpha=10, beta=5, gamma=1 -> delta = 0.5*(10-1)/(10-10+1) = 4.5
    spec[4] = 10.0
    spec[5] = 5.0
    spec[6] = 1.0
    freq_res = 0.05
    result = vitals.parabolic_interpolate_peak(spec, 5, freq_res)
    assert abs(result - 5 * freq_res) < 1e-12


def test_parabolic_end_to_end_no_eca():
    """No-ECA path with parabolic interpolation returns HR within 0.5 bpm of truth."""
    phase, fs = make_phase(hr_bpm=72.0)  # 72 bpm = 1.2 Hz, on-bin at 60 s / 20 Hz
    out = vitals.estimate_rate_from_phase(phase, fs, vitals.HEART_BAND_HZ)
    assert abs(out["rate_bpm"] - 72.0) <= 0.5
