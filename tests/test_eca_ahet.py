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


def _run_single_window(phase: np.ndarray) -> dict:
    """Run one synthetic phase window through the locked-bin production path."""
    cube = np.exp(1j * phase)[:, None, None, None]
    params = vitals.VitalsParams(
        fs_hz=FS,
        gate_min_m=1.3,
        gate_max_m=1.6,
    )
    results = vitals.run_pipeline_locked(
        cube,
        np.array([1.4]),
        params,
        window_frames=N,
        hop_frames=N,
        locked_bin=0,
    )
    assert len(results) == 1
    return results[0]


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


def test_successful_ahet_window_records_consistent_intermediates():
    out = _run_single_window(_make_harmonic_signal())

    required_fields = {
        "phase_unwrapped",
        "phase_clean",
        "phase_eca",
        "resp_freqs_hz",
        "resp_spectrum",
        "resp_peak_raw_index",
        "resp_peak_raw_hz",
        "resp_peak_refined_hz",
        "heart_freqs_hz",
        "heart_spectrum_first_pass",
        "heart_spectrum",
        "heart_spectrum_stage",
        "heart_peak_hz",
        "accepted_candidate_rank",
        "accepted_candidate_initial_hz",
        "accepted_candidate_refined_hz",
        "accepted_second_harmonic_refined_hz",
        "candidate_attempted",
        "candidate_peak_bin_index",
        "candidate_initial_hz",
        "candidate_refined_hz",
        "candidate_peak_magnitude",
        "candidate_prominence",
        "candidate_argmax_fallback",
        "second_peak_bin_hz",
        "second_peak_refined_hz",
        "second_peak_magnitude",
        "comparison_floor",
        "peak_to_floor_ratio",
        "peak_to_floor_ratio_db",
        "region_available",
        "candidate_passed",
        "ahet_attempt_spectrum",
        "schema_version",
        "window_index",
        "start_frame",
        "end_frame",
        "window_frames",
        "hop_frames",
        "frame_rate_hz",
        "range_bin",
        "range_m",
        "eca_applied",
        "f_r_outlier",
        "ahet_verified",
    }
    assert required_fields <= out.keys()
    assert "start_epoch" not in out
    assert "end_epoch" not in out

    rank = out["accepted_candidate_rank"]
    assert out["heart_spectrum_stage"] == 2
    assert out["ahet_verified"] is True
    assert 0 <= rank < vitals.AHET_MAX_CANDIDATES
    assert out["candidate_passed"].shape == (vitals.AHET_MAX_CANDIDATES,)
    assert out["ahet_attempt_spectrum"].shape == (
        vitals.AHET_MAX_CANDIDATES,
        len(out["heart_freqs_hz"]),
    )
    assert out["candidate_passed"].sum() == 1
    assert bool(out["candidate_passed"][rank])
    assert out["accepted_candidate_initial_hz"] == pytest.approx(
        out["candidate_initial_hz"][rank]
    )
    assert out["accepted_candidate_refined_hz"] == pytest.approx(
        out["candidate_refined_hz"][rank]
    )
    assert out["accepted_second_harmonic_refined_hz"] == pytest.approx(
        out["second_peak_refined_hz"][rank]
    )
    np.testing.assert_allclose(
        out["heart_spectrum"], out["ahet_attempt_spectrum"][rank]
    )

    assert out["schema_version"] == vitals.INTERMEDIATE_SCHEMA_VERSION
    assert out["window_index"] == 0
    assert out["start_frame"] == 0
    assert out["end_frame"] == N
    assert out["window_frames"] == N
    assert out["hop_frames"] == N
    assert out["frame_rate_hz"] == FS
    assert out["range_bin"] == 0
    assert out["range_m"] == pytest.approx(1.4)


def test_failed_ahet_window_uses_first_pass_stage():
    t = np.arange(N) / FS
    phase = (
        3.00 * np.sin(2 * np.pi * 1 * F_R * t)
        + 1.50 * np.sin(2 * np.pi * 2 * F_R * t)
        + 1.00 * np.sin(2 * np.pi * 3 * F_R * t)
        + 0.75 * np.sin(2 * np.pi * 4 * F_R * t)
    )
    out = _run_single_window(phase)

    assert out["accepted_candidate_rank"] == -1
    assert out["heart_spectrum_stage"] == 1
    assert out["ahet_verified"] is False
    assert not out["candidate_passed"].any()
    assert np.isnan(out["accepted_candidate_initial_hz"])
    assert np.isnan(out["accepted_candidate_refined_hz"])
    assert np.isnan(out["accepted_second_harmonic_refined_hz"])
    np.testing.assert_allclose(
        out["heart_spectrum"], out["heart_spectrum_first_pass"]
    )


def test_respiratory_outlier_window_uses_no_eca_stage():
    t = np.arange(N) / FS
    phase = (
        3.0 * np.sin(2 * np.pi * 0.1 * t)
        + 0.4 * np.sin(2 * np.pi * 1.2 * t)
    )
    out = _run_single_window(phase)

    assert out["f_r_outlier"] is True
    assert out["eca_applied"] is False
    assert out["heart_spectrum_stage"] == 0
    assert out["accepted_candidate_rank"] == -1
    assert np.isnan(out["phase_eca"]).all()
    assert np.isnan(out["heart_spectrum_first_pass"]).all()
    assert not out["candidate_attempted"].any()
    assert not out["candidate_passed"].any()


def test_argmax_fallback_flag_when_find_peaks_returns_nothing(monkeypatch):
    monkeypatch.setattr(
        vitals,
        "find_peaks",
        lambda *args, **kwargs: (np.array([], dtype=int), {}),
    )

    out = _run_single_window(_make_harmonic_signal())

    assert bool(out["candidate_attempted"][0])
    assert bool(out["candidate_argmax_fallback"][0])
    assert not out["candidate_argmax_fallback"][1:].any()
    assert np.isnan(out["candidate_prominence"][0])
