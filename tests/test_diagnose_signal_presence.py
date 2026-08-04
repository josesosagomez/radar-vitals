"""Tests for `scripts/diagnose_signal_presence.py`.

This script's output is about to inform a protocol decision, so the parts that could
silently produce a confident wrong answer are pinned here: that the oracle statistic
actually finds a known planted tone, that the decoy control returns ~0.5 on a spectrum
with nothing special at the reference, and — most important — that the tracking tests
distinguish real tracking from a constant predictor that happens to score well.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import diagnose_signal_presence as dsp  # noqa: E402

FS = 20.0
N = 600


def _spectrum(signal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    spec = np.abs(np.fft.rfft(signal * np.hanning(len(signal))))
    return spec, np.fft.rfftfreq(len(signal), d=1.0 / FS)


# ── band_metrics ────────────────────────────────────────────────────────────


def test_band_metrics_finds_a_planted_tone():
    t = np.arange(N) / FS
    f_true = 1.25                                    # 75 bpm
    rng = np.random.default_rng(0)
    spec, freqs = _spectrum(np.sin(2 * np.pi * f_true * t) + 0.05 * rng.standard_normal(N))

    m = dsp.band_metrics(spec, freqs, (0.8, 2.0), f_true, 0.05)
    assert abs(m["argmax_hz"] - f_true) < 0.02
    assert abs(m["oracle_peak_hz"] - f_true) < 0.02
    assert m["oracle_snr_db"] > 20.0
    # A strong tone IS the band argmax, so oracle and reference-free SNR agree.
    assert m["oracle_snr_db"] == pytest.approx(m["peak_snr_db"], abs=0.5)


def test_band_metrics_oracle_and_peak_diverge_when_the_argmax_is_not_the_truth():
    """The diagnostic case: a strong interferer elsewhere in the band."""
    t = np.arange(N) / FS
    spec, freqs = _spectrum(np.sin(2 * np.pi * 1.0 * t) + 0.05 * np.sin(2 * np.pi * 1.6 * t))
    m = dsp.band_metrics(spec, freqs, (0.8, 2.0), 1.6, 0.05)
    assert abs(m["argmax_hz"] - 1.0) < 0.02          # argmax is the interferer
    assert m["peak_snr_db"] > m["oracle_snr_db"]     # truth is weaker than the argmax


def test_band_metrics_without_a_reference_reports_no_oracle():
    spec, freqs = _spectrum(np.random.default_rng(1).standard_normal(N))
    m = dsp.band_metrics(spec, freqs, (0.8, 2.0), None, 0.05)
    assert m["oracle_snr_db"] is None and m["argmax_hz"] is not None


def test_band_metrics_handles_an_empty_band():
    spec, freqs = _spectrum(np.zeros(N))
    assert dsp.band_metrics(spec, freqs, (50.0, 60.0), 55.0, 0.05)["argmax_hz"] is None


# ── decoy control ───────────────────────────────────────────────────────────


def test_decoy_fraction_averages_to_half_for_an_unremarkable_frequency():
    """Noise only: the reference frequency is no better than a random one.

    Asserted as a MEAN over independent spectra, not on a single one. For any one
    spectrum the reference frequency has a fixed oracle SNR, so its beat fraction is
    itself a random draw and lands anywhere in [0, 1] — which is exactly why the script
    sign-tests across windows instead of trusting a single window.
    """
    fracs = [
        dsp.decoy_fraction(
            *_spectrum(np.random.default_rng(100 + i).standard_normal(N)),
            (0.8, 2.0), 1.3, 0.05, np.random.default_rng(200 + i), n_decoy=40,
        )
        for i in range(40)
    ]
    assert 0.4 < float(np.mean(fracs)) < 0.6


def test_decoy_fraction_is_near_one_for_a_planted_tone():
    t = np.arange(N) / FS
    rng = np.random.default_rng(5)
    spec, freqs = _spectrum(np.sin(2 * np.pi * 1.25 * t) + 0.1 * rng.standard_normal(N))
    frac = dsp.decoy_fraction(spec, freqs, (0.8, 2.0), 1.25, 0.05,
                              np.random.default_rng(6), n_decoy=200)
    # Not 1.0: a decoy drawn within a search half-width of the tone captures the same
    # peak and ties, so a few percent of draws legitimately do not lose.
    assert frac > 0.9


def test_decoy_fraction_without_a_reference_is_none():
    spec, freqs = _spectrum(np.random.default_rng(7).standard_normal(N))
    assert dsp.decoy_fraction(spec, freqs, (0.8, 2.0), None, 0.05,
                              np.random.default_rng(8)) is None


# ── tracking tests ──────────────────────────────────────────────────────────


def _audit(argmax_by_bin: dict[int, list[float]], ref: list[float], locked: int) -> dict:
    rows = []
    for b, series in argmax_by_bin.items():
        for k, v in enumerate(series):
            rows.append({
                "capture_id": "c", "k": k, "bin": b, "is_locked_bin": b == locked,
                "hr_argmax_bpm": v, "hr_ref_bpm": ref[k],
                "br_argmax_bpm": v, "br_ref_bpm": ref[k],
                "hr_decoy_beat_frac": None, "br_decoy_beat_frac": None,
            })
    return {"capture_id": "c", "rows": rows, "locked_bin": locked}


def test_tracking_detects_a_perfectly_tracking_bin():
    ref = [60.0, 70.0, 80.0, 90.0, 100.0, 110.0, 65.0, 75.0, 85.0, 95.0]
    noise = [90.0, 60.0, 110.0, 70.0, 100.0, 65.0, 105.0, 80.0, 62.0, 95.0]
    audit = _audit({5: list(ref), 6: noise}, ref, locked=5)
    t = dsp.tracking_tests(audit, "hr", hit_bpm=5.0, n_perm=500, seed=1)

    assert t["locked_hit_rate"] == 1.0
    assert t["permutation_p"] < 0.05           # beats the shuffled null
    assert t["spearman_locked"] == pytest.approx(1.0)
    assert t["spearman_locked_p"] < 0.01
    # The reference genuinely moves here, so a constant cannot score well.
    assert t["constant_baseline_hit_rate"] < 0.5
    assert t["spread_below_tolerance"] is False


def test_tracking_flags_the_constant_baseline_trap():
    """The failure mode this project actually has: a reference that barely moves.

    Every window's reference sits inside the tolerance of the session median, so a
    predictor that ignores the radar scores 100% and the hit rate proves nothing.
    """
    ref = [80.0, 81.0, 79.0, 80.5, 79.5, 81.0, 80.0, 79.0, 80.5, 80.0]
    scattered = [95.0, 60.0, 110.0, 70.0, 100.0, 65.0, 105.0, 62.0, 108.0, 58.0]
    audit = _audit({5: scattered, 6: scattered}, ref, locked=5)
    t = dsp.tracking_tests(audit, "hr", hit_bpm=5.0, n_perm=500, seed=1)

    assert t["constant_baseline_hit_rate"] == 1.0
    assert t["spread_below_tolerance"] is True
    assert t["permutation_p"] > 0.05           # no tracking evidence


def test_tracking_reports_bonferroni_for_the_best_bin_but_not_the_locked_one():
    ref = [60.0, 70.0, 80.0, 90.0, 100.0, 110.0, 65.0, 75.0, 85.0, 95.0]
    audit = _audit({b: list(ref) for b in range(5, 15)}, ref, locked=5)
    t = dsp.tracking_tests(audit, "hr", hit_bpm=5.0, n_perm=200, seed=1)
    assert t["spearman_best_p_bonferroni"] >= t["spearman_best_p_uncorrected"]
    assert t["spearman_best_p_bonferroni"] <= 1.0


def test_tracking_declines_on_too_few_windows():
    ref = [80.0, 81.0]
    audit = _audit({5: [80.0, 81.0]}, ref, locked=5)
    assert dsp.tracking_tests(audit, "hr", 5.0, 100, 1)["insufficient_data"] is True


def test_spearman_needs_three_finite_pairs():
    assert dsp._spearman(np.array([1.0, np.nan]), np.array([1.0, 2.0])) == (None, None)
    rho, p = dsp._spearman(np.array([1.0, 2.0, 3.0]), np.array([2.0, 4.0, 6.0]))
    assert rho == pytest.approx(1.0)


def test_decoy_verdict_sign_test_uses_locked_bin_only():
    rows = [
        {"is_locked_bin": True, "hr_decoy_beat_frac": 0.9},
        {"is_locked_bin": True, "hr_decoy_beat_frac": 0.8},
        {"is_locked_bin": True, "hr_decoy_beat_frac": 0.7},
        {"is_locked_bin": False, "hr_decoy_beat_frac": 0.1},
    ]
    v = dsp.decoy_verdict({"rows": rows}, "hr")
    assert v["n"] == 3 and v["n_windows_above_half"] == 3
    assert v["mean_beat_fraction"] == pytest.approx(0.8)
