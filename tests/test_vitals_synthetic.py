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
