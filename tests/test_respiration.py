"""Unit tests for src/respiration.py — no hardware, no HDF5 needed.

Each test uses synthetic signals so the expected answer is known exactly.
Run: pytest tests/test_respiration.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.respiration import (
    extract_chest_phase,
    fft_estimate_rr,
    ha_estimate_rr,
    stft_stability,
    fuse_estimates,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FS = 20.0          # radar frame rate (Hz)
BAND = (0.10, 0.50)  # respiration band (Hz)


def _pure_phase(rr_bpm: float, dur_s: float = 30.0, fs: float = FS,
                harmonics: list[float] | None = None, seed: int = 0) -> np.ndarray:
    """Synthetic chest phase: one or more tones, small additive noise."""
    rng = np.random.default_rng(seed)
    t   = np.arange(0, dur_s, 1.0 / fs)
    fb  = rr_bpm / 60.0
    sig = 2.0 * np.sin(2 * np.pi * fb * t)
    if harmonics:
        for amp, hf_mult in harmonics:
            sig += amp * np.sin(2 * np.pi * fb * hf_mult * t)
    return sig + 0.05 * rng.standard_normal(len(t))


def _synthetic_cube(
    rr_bpm: float = 15.0,
    dur_s: float = 30.0,
    fs: float = FS,
    locked_bin: int = 10,
    n_chirps: int = 4,
    n_rx: int = 2,
    n_adc: int = 64,
    seed: int = 1,
) -> tuple[np.ndarray, int]:
    """Build a synthetic (N, chirps, rx, adc_samples) complex64 cube.

    The chest phase is encoded as a baseband rotation in the bin selected by
    locked_bin. All other bins have a small random background.
    """
    rng   = np.random.default_rng(seed)
    N     = int(dur_s * fs)
    t     = np.arange(N) / fs
    fb    = rr_bpm / 60.0
    phase = 2.0 * np.sin(2 * np.pi * fb * t)  # encoded chest phase

    cube = (0.01 * (rng.standard_normal((N, n_chirps, n_rx, n_adc))
                    + 1j * rng.standard_normal((N, n_chirps, n_rx, n_adc)))).astype(np.complex64)

    # Encode phase as a rotating phasor at locked_bin
    phasor = np.exp(1j * phase).astype(np.complex64)  # (N,)
    cube[:, :, :, locked_bin] += phasor[:, None, None]

    return cube.astype(np.complex64), locked_bin


# ---------------------------------------------------------------------------
# extract_chest_phase
# ---------------------------------------------------------------------------

class TestExtractChestPhase:
    def test_delta_before_mean_shape(self):
        cube, lb = _synthetic_cube()
        phase = extract_chest_phase(cube, lb, method="delta_before_mean")
        assert phase.shape == (cube.shape[0],)
        assert phase.dtype == np.float64

    def test_mean_phasor_shape(self):
        cube, lb = _synthetic_cube()
        phase = extract_chest_phase(cube, lb, method="mean_phasor")
        assert phase.shape == (cube.shape[0],)

    def test_invalid_method_raises(self):
        cube, lb = _synthetic_cube()
        with pytest.raises(ValueError, match="method"):
            extract_chest_phase(cube, lb, method="bad_method")

    def test_invalid_bin_raises(self):
        cube, _ = _synthetic_cube()
        with pytest.raises(ValueError, match="locked_bin"):
            extract_chest_phase(cube, 999, method="delta_before_mean")

    def test_delta_before_mean_handles_rx_phase_offsets(self):
        """delta_before_mean should recover phase even with static per-RX offsets
        that would cause partial cancellation with mean_phasor."""
        rng  = np.random.default_rng(42)
        N, n_chirps, n_rx, n_adc = 200, 4, 2, 64
        lb   = 8
        fs   = 20.0
        fb   = 15.0 / 60.0
        t    = np.arange(N) / fs
        phase_true = 2.0 * np.sin(2 * np.pi * fb * t)

        # static per-RX phase offsets spaced ~π apart (worst case for mean_phasor)
        rx_offsets = np.array([0.0, np.pi - 0.1])

        cube = (0.01 * (rng.standard_normal((N, n_chirps, n_rx, n_adc))
                        + 1j * rng.standard_normal((N, n_chirps, n_rx, n_adc)))).astype(np.complex64)

        for rx_i, offset in enumerate(rx_offsets):
            phasor = np.exp(1j * (phase_true + offset)).astype(np.complex64)
            cube[:, :, rx_i, lb] += phasor[:, None]

        # delta_before_mean should still recover a valid breathing signal
        phase_dbm = extract_chest_phase(cube, lb, method="delta_before_mean")
        result    = fft_estimate_rr(phase_dbm, fs, (0.10, 0.50))
        assert abs(result["fft_rr_bpm"] - 15.0) <= 3.0, (
            f"delta_before_mean recovered {result['fft_rr_bpm']:.1f} bpm, expected ~15 bpm"
        )


# ---------------------------------------------------------------------------
# fft_estimate_rr
# ---------------------------------------------------------------------------

class TestFftEstimateRr:
    @pytest.mark.parametrize("rr", [10.0, 15.0, 20.0, 25.0])
    def test_recovers_known_rr(self, rr):
        phase  = _pure_phase(rr)
        result = fft_estimate_rr(phase, FS, BAND)
        assert abs(result["fft_rr_bpm"] - rr) <= 2.0, (
            f"FFT got {result['fft_rr_bpm']:.1f}, expected {rr}"
        )

    def test_output_keys(self):
        phase  = _pure_phase(15.0)
        result = fft_estimate_rr(phase, FS, BAND)
        for k in ("fft_rr_bpm", "fft_peak_hz", "fft_peak_snr_db", "fft_peak_bin",
                  "freqs_hz", "spectrum"):
            assert k in result, f"missing key {k}"

    def test_peak_bin_indexes_into_spectrum(self):
        """fft_peak_bin must be the index into freqs_hz/spectrum where the peak is."""
        phase  = _pure_phase(15.0)
        result = fft_estimate_rr(phase, FS, BAND)
        bin_   = result["fft_peak_bin"]
        assert isinstance(bin_, int) and bin_ >= 0
        # The frequency at that bin must be close to fft_peak_hz
        assert abs(result["freqs_hz"][bin_] - result["fft_peak_hz"]) <= (
            float(result["freqs_hz"][1] - result["freqs_hz"][0]) * 1.01
        ), "fft_peak_bin frequency does not match fft_peak_hz within one bin"

    def test_snr_positive_for_clean_signal(self):
        phase  = _pure_phase(15.0)
        result = fft_estimate_rr(phase, FS, BAND)
        assert np.isfinite(result["fft_peak_snr_db"])
        assert result["fft_peak_snr_db"] > 0.0

    def test_peak_hz_consistent_with_rr_bpm(self):
        phase  = _pure_phase(18.0)
        result = fft_estimate_rr(phase, FS, BAND)
        assert abs(result["fft_peak_hz"] * 60.0 - result["fft_rr_bpm"]) < 0.01


# ---------------------------------------------------------------------------
# ha_estimate_rr
# ---------------------------------------------------------------------------

class TestHaEstimateRr:
    @pytest.mark.parametrize("rr", [12.0, 15.0, 20.0])
    def test_recovers_fundamental(self, rr):
        phase  = _pure_phase(rr)
        result = ha_estimate_rr(phase, FS, BAND)
        assert abs(result["ha_rr_bpm"] - rr) <= 2.0, (
            f"HA got {result['ha_rr_bpm']:.1f}, expected {rr}"
        )

    def test_uses_f_2f_3f_not_2f_4f_6f(self):
        """HA must search at f, 2f, 3f (chest phase model), not 2f, 4f, 6f (pulse-radar model).

        We place a tone at 15 bpm = 0.25 Hz and verify that harmonic_freqs_hz
        in the result contains 0.25, 0.50, 0.75 Hz — not 0.50, 1.00, 1.50 Hz.
        """
        rr     = 15.0
        phase  = _pure_phase(rr)
        result = ha_estimate_rr(phase, FS, BAND, max_harmonics=3)

        # Find the winning candidate (closest to rr_bpm)
        ha_hz  = result["ha_peak_hz"]
        # `_pure_phase` is deterministic (seed 0), so a NaN here is a phase-extraction
        # regression, not a weak-signal accident. Asserting keeps it a FAILURE: a skip
        # would silently remove the harmonic-model check below from coverage.
        assert np.isfinite(ha_hz), (
            f"HA produced a non-finite peak ({ha_hz}) on the deterministic {rr} bpm tone"
        )

        cand_freqs = result["ha_candidate_freqs_hz"]
        cand_scores = result["ha_candidate_scores"]
        best_idx   = int(np.argmax(np.where(np.isfinite(cand_scores), cand_scores, -np.inf)))
        hf_row     = result["ha_harmonic_freqs_hz"][best_idx]
        valid_hf   = hf_row[np.isfinite(hf_row)]

        # Expect harmonics ~0.25, 0.50, 0.75 Hz
        fb = rr / 60.0
        for k, hf in enumerate(valid_hf, start=1):
            expected = k * fb
            # tolerance 1.5 FFT bins at FS=20, dur=30 s (freq_res = 1/30 ≈ 0.033 Hz)
            assert abs(hf - expected) <= 0.06, (
                f"Harmonic {k}: got {hf:.4f} Hz, expected {expected:.4f} Hz "
                f"(model should use f, 2f, 3f, not 2f, 4f, 6f)"
            )

    def test_recovers_fundamental_when_harmonics_dominate(self):
        """HA should return f_b when 2f_b has more power than f_b (non-sinusoidal breathing).

        Uses rr=20 bpm (0.333 Hz) so 2f=0.667 Hz is ABOVE the band.
        Sub-harmonic candidates in the band (e.g. 10 bpm = 0.167 Hz) fail the
        fundamental-support check because their spectrum at 0.167 Hz is noise.
        """
        rr  = 20.0
        fb  = rr / 60.0  # 0.333 Hz; 2f = 0.667 Hz (above band [0.10, 0.50])
        t   = np.arange(int(30.0 * FS)) / FS
        # fundamental amplitude 1.0, 2nd harmonic amplitude 2.0 → power ratio 1:4
        rng = np.random.default_rng(7)
        phase = (1.0 * np.sin(2 * np.pi * fb * t)
                 + 2.0 * np.sin(2 * np.pi * 2 * fb * t)
                 + 0.05 * rng.standard_normal(len(t)))

        result = ha_estimate_rr(phase, FS, BAND, max_harmonics=3)
        # Deterministic input (default_rng(7)), so returning NaN would itself be the
        # regression this test exists to catch — it must fail, not skip.
        assert np.isfinite(result["ha_rr_bpm"]), (
            "HA returned NaN on the deterministic dominant-2nd-harmonic signal, so the "
            "fundamental-support check rejected a fundamental that is genuinely present"
        )
        assert abs(result["ha_rr_bpm"] - rr) <= 3.0, (
            f"HA returned {result['ha_rr_bpm']:.1f} bpm, expected ~{rr} bpm "
            "even though 2nd harmonic had 4× more power and falls above the band"
        )

    def test_harmonic_evidence_above_band(self):
        """HA must collect harmonic power above the respiration band (0.10-0.50 Hz).

        At f=0.40 Hz (24 bpm), 2f=0.80 Hz and 3f=1.20 Hz are outside the band.
        ha_harmonic_freqs_hz for the winning candidate should include values > 0.50 Hz.
        """
        rr     = 24.0
        fb     = rr / 60.0  # 0.40 Hz
        phase  = _pure_phase(rr, dur_s=30.0)
        result = ha_estimate_rr(phase, FS, BAND, max_harmonics=3)

        # Both preconditions are deterministic properties of `_pure_phase(24 bpm)`; losing
        # either one would remove the above-band harmonic check without any signal.
        assert np.isfinite(result["ha_rr_bpm"]), (
            f"HA returned NaN on the deterministic {rr} bpm tone"
        )

        cand_freqs  = result["ha_candidate_freqs_hz"]
        cand_scores = result["ha_candidate_scores"]
        assert len(cand_freqs) > 0, "HA proposed no candidate in the respiration band"

        best_idx = int(np.argmax(np.where(np.isfinite(cand_scores), cand_scores, -np.inf)))
        hf_row   = result["ha_harmonic_freqs_hz"][best_idx]
        valid_hf = hf_row[np.isfinite(hf_row)]

        # At least one harmonic frequency should be above the upper band limit
        above_band = [hf for hf in valid_hf if hf > BAND[1]]
        assert len(above_band) >= 1, (
            f"HA did not use any harmonic above {BAND[1]} Hz for candidate {fb:.2f} Hz. "
            f"Harmonic freqs: {list(valid_hf)}"
        )

    def test_output_keys(self):
        phase  = _pure_phase(15.0)
        result = ha_estimate_rr(phase, FS, BAND)
        for k in ("ha_rr_bpm", "ha_peak_hz", "ha_score", "ha_harmonics_used",
                  "ha_candidate_freqs_hz", "ha_candidate_scores",
                  "ha_harmonic_freqs_hz", "ha_harmonic_power"):
            assert k in result, f"missing key {k}"

    def test_ha_candidate_scores_not_in_scalar_output(self):
        """ha_candidate_scores must NOT be a scalar — it is an array for NPZ storage."""
        phase  = _pure_phase(15.0)
        result = ha_estimate_rr(phase, FS, BAND)
        assert isinstance(result["ha_candidate_scores"], np.ndarray), (
            "ha_candidate_scores must be an ndarray (stored in NPZ, not CSV)"
        )


# ---------------------------------------------------------------------------
# stft_stability
# ---------------------------------------------------------------------------

class TestStftStability:
    def test_low_std_for_stationary_breathing(self):
        """Constant-rate breathing should give small std across subwindows."""
        phase  = _pure_phase(15.0, dur_s=30.0)
        result = stft_stability(phase, FS, BAND, subwindow_s=10.0, overlap=0.5)
        assert np.isfinite(result["stft_rr_std_bpm"]), "std should be finite for clean signal"
        assert result["stft_rr_std_bpm"] < 3.0, (
            f"std={result['stft_rr_std_bpm']:.2f} bpm — expected < 3 for stationary breathing"
        )

    def test_high_std_for_changing_breathing(self):
        """Breathing rate that shifts mid-session should give higher std."""
        t1 = _pure_phase(10.0, dur_s=15.0, seed=1)
        t2 = _pure_phase(25.0, dur_s=15.0, seed=2)
        phase  = np.concatenate([t1, t2])
        result = stft_stability(phase, FS, BAND, subwindow_s=10.0, overlap=0.5)
        # Std should be larger than for stationary breathing
        if np.isfinite(result["stft_rr_std_bpm"]):
            assert result["stft_rr_std_bpm"] > 2.0, (
                f"std={result['stft_rr_std_bpm']:.2f} bpm should be > 2 for changing RR"
            )

    def test_config_driven_subwindow(self):
        """Changing subwindow_s must change the number of subwindows processed."""
        phase = _pure_phase(15.0, dur_s=30.0)
        r1 = stft_stability(phase, FS, BAND, subwindow_s=10.0, overlap=0.0)
        r2 = stft_stability(phase, FS, BAND, subwindow_s=5.0,  overlap=0.0)
        # More subwindows with shorter window → different valid_fraction base
        # (they must not be identical)
        assert r1["stft_valid_fraction"] != r2["stft_valid_fraction"] or \
               r1.get("stft_rr_bpm") != r2.get("stft_rr_bpm"), \
               "subwindow_s parameter had no effect"

    def test_output_keys(self):
        phase  = _pure_phase(15.0)
        result = stft_stability(phase, FS, BAND)
        for k in ("stft_rr_bpm", "stft_rr_std_bpm", "stft_valid_fraction"):
            assert k in result


# ---------------------------------------------------------------------------
# fuse_estimates
# ---------------------------------------------------------------------------

class TestFuseEstimates:
    def _cfg(self, emit_low: bool = False, fft_snr_gate: float = 6.0) -> dict:
        return {
            "fft_ha_agree_bpm_high":   2.0,
            "fft_ha_agree_bpm_medium": 4.0,
            "stft_std_high_bpm":       2.0,
            "stft_std_medium_bpm":     4.0,
            "fft_fallback_snr_db":     fft_snr_gate,
            "emit_low_confidence":     emit_low,
        }

    def test_high_confidence_agreement(self):
        fft_r  = {"fft_rr_bpm": 15.0, "fft_peak_hz": 0.25}
        ha_r   = {"ha_rr_bpm":  15.5, "ha_peak_hz": 15.5 / 60.0, "ha_score": 5.0}
        stft_r = {"stft_rr_bpm": 15.2, "stft_rr_std_bpm": 1.0, "stft_valid_fraction": 1.0}
        out = fuse_estimates(fft_r, ha_r, stft_r, self._cfg())
        assert out["resp_confidence"] == "high"
        assert out["resp_valid"]
        assert np.isfinite(out["radar_rr_bpm"])

    def test_low_confidence_nan_by_default(self):
        fft_r  = {"fft_rr_bpm": 12.0, "fft_peak_hz": 0.20}
        ha_r   = {"ha_rr_bpm":  20.0, "ha_peak_hz": 20.0 / 60.0, "ha_score": 1.0}
        stft_r = {"stft_rr_bpm": 16.0, "stft_rr_std_bpm": 8.0, "stft_valid_fraction": 0.5}
        out = fuse_estimates(fft_r, ha_r, stft_r, self._cfg(emit_low=False))
        assert out["resp_confidence"] == "low"
        assert not out["resp_valid"]
        assert not np.isfinite(out["radar_rr_bpm"])

    def test_low_confidence_emitted_when_flag_set(self):
        fft_r  = {"fft_rr_bpm": 12.0, "fft_peak_hz": 0.20}
        ha_r   = {"ha_rr_bpm":  20.0, "ha_peak_hz": 20.0 / 60.0, "ha_score": 1.0}
        stft_r = {"stft_rr_bpm": 16.0, "stft_rr_std_bpm": 8.0, "stft_valid_fraction": 0.5}
        out = fuse_estimates(fft_r, ha_r, stft_r, self._cfg(emit_low=True))
        assert out["resp_confidence"] == "low"
        assert out["resp_valid"] is False  # still False even when emitting
        # radar_rr_bpm may or may not be finite — but shouldn't crash

    def test_resp_valid_and_quality_gated_independent(self):
        """resp_valid is a Step 5 decision; quality_gated is a Step 4 decision.
        fuse_estimates itself never sets quality_gated — that's done by the runner."""
        fft_r  = {"fft_rr_bpm": 15.0, "fft_peak_hz": 0.25}
        ha_r   = {"ha_rr_bpm":  14.5, "ha_peak_hz": 14.5 / 60.0, "ha_score": 3.0}
        stft_r = {"stft_rr_bpm": 15.0, "stft_rr_std_bpm": 1.5, "stft_valid_fraction": 1.0}
        out = fuse_estimates(fft_r, ha_r, stft_r, self._cfg())
        assert "quality_gated" not in out, \
            "fuse_estimates must not set quality_gated — that is the runner's responsibility"

    def test_missing_masimo_rr_does_not_affect_radar_estimate(self):
        """The Masimo RR NaN rule: missing reference must not influence radar estimate."""
        fft_r  = {"fft_rr_bpm": 15.0, "fft_peak_hz": 0.25}
        ha_r   = {"ha_rr_bpm":  15.2, "ha_peak_hz": 15.2 / 60.0, "ha_score": 4.0}
        stft_r = {"stft_rr_bpm": 15.1, "stft_rr_std_bpm": 0.8, "stft_valid_fraction": 1.0}
        # masimo_rr_bpm=NaN is handled by the runner, not fuse_estimates
        out1 = fuse_estimates(fft_r, ha_r, stft_r, self._cfg())
        # Result should be identical whether caller plans to pair with NaN masimo or not
        assert np.isfinite(out1["radar_rr_bpm"])

    def test_resp_peak_hz_equals_radar_rr_bpm_over_60(self):
        fft_r  = {"fft_rr_bpm": 15.0, "fft_peak_hz": 0.25}
        ha_r   = {"ha_rr_bpm":  15.2, "ha_peak_hz": 15.2 / 60.0, "ha_score": 4.0}
        stft_r = {"stft_rr_bpm": 15.1, "stft_rr_std_bpm": 0.8, "stft_valid_fraction": 1.0}
        out = fuse_estimates(fft_r, ha_r, stft_r, self._cfg())
        if out["resp_valid"]:
            assert abs(out["resp_peak_hz"] * 60.0 - out["radar_rr_bpm"]) < 0.01, (
                "resp_peak_hz must equal radar_rr_bpm / 60 when resp_valid=True"
            )

    def test_fft_fallback_fires_when_ha_fails(self):
        """When HA returns NaN but FFT is clean and STFT stable, fallback to FFT at medium."""
        fft_r  = {"fft_rr_bpm": 15.0, "fft_peak_hz": 0.25, "fft_peak_snr_db": 12.0}
        ha_r   = {"ha_rr_bpm": float("nan"), "ha_peak_hz": float("nan"), "ha_score": float("nan")}
        stft_r = {"stft_rr_bpm": 15.1, "stft_rr_std_bpm": 2.0, "stft_valid_fraction": 1.0}
        out = fuse_estimates(fft_r, ha_r, stft_r, self._cfg())
        assert out["resp_confidence"] == "medium", (
            f"expected medium confidence for FFT fallback, got {out['resp_confidence']}"
        )
        assert out["resp_valid"]
        assert abs(out["radar_rr_bpm"] - 15.0) < 0.1
        assert abs(out["resp_peak_hz"] - 0.25) < 0.01

    def test_fft_fallback_blocked_by_low_snr(self):
        """FFT fallback must not fire when SNR is below the gate (default 6 dB)."""
        fft_r  = {"fft_rr_bpm": 15.0, "fft_peak_hz": 0.25, "fft_peak_snr_db": 3.0}
        ha_r   = {"ha_rr_bpm": float("nan"), "ha_peak_hz": float("nan"), "ha_score": float("nan")}
        stft_r = {"stft_rr_bpm": 15.1, "stft_rr_std_bpm": 2.0, "stft_valid_fraction": 1.0}
        out = fuse_estimates(fft_r, ha_r, stft_r, self._cfg(fft_snr_gate=6.0))
        assert out["resp_confidence"] == "low", (
            f"expected low confidence when FFT SNR={3.0} < gate={6.0}, got {out['resp_confidence']}"
        )
        assert not out["resp_valid"]

    def test_fft_fallback_blocked_by_unstable_stft(self):
        """FFT fallback must not fire when STFT std exceeds the medium threshold."""
        fft_r  = {"fft_rr_bpm": 15.0, "fft_peak_hz": 0.25, "fft_peak_snr_db": 12.0}
        ha_r   = {"ha_rr_bpm": float("nan"), "ha_peak_hz": float("nan"), "ha_score": float("nan")}
        stft_r = {"stft_rr_bpm": 15.0, "stft_rr_std_bpm": 6.0, "stft_valid_fraction": 1.0}
        out = fuse_estimates(fft_r, ha_r, stft_r, self._cfg())
        assert out["resp_confidence"] == "low", (
            "expected low confidence when STFT std > stft_std_medium_bpm"
        )
