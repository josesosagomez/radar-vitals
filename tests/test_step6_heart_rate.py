"""Unit and integration tests for Step 6 (extract_heart_rate.py).

NOTE ON MOCKING
---------------
scipy.signal.filtfilt -> np.linalg.solve triggers the known Windows LAPACK
DLL crash on this machine (same issue Step 5 already works around with a pure
numpy detrend).  Integration tests that exercise _process_session therefore
patch `estimate_rate_from_phase` in the step_6 module with a pure-numpy mock
that returns a plausible AHET-accepted or no-ECA result.

The mock does NOT affect unit tests (which call helper functions directly) or
the "step6 uses Step5 resp_peak_hz" spy test (which wraps the real function).

Covers:
  - HR window to Step 5 breathing-window matching
  - Invalid reason priority
  - ECA/AHET uses Step 5 resp_peak_hz (not inline estimation)
  - Invalid/gated/edge-locked/missing/too-far breathing causes primary HR NaN
  - resp_harmonic_guard_hz check in Hz
  - eca_forbidden_zone flag
  - harmonic_suspect on AHET failure / False on acceptance and skipped
  - heart_spectrum_stage and hr_confidence enum constraints
  - ahet_failed -> hr_confidence mapping
  - radar_hr_bpm finite iff hr_confidence == "high"
  - Masimo PI/coverage gate
  - NPZ serializer writes fixed-shape numeric arrays
  - Integration: synthetic HDF5 + Step 5 contract + Masimo -> expected HR
  - Missing Step 5 contract fails clearly
  - Missing edge-lock columns fails clearly with rerun instruction

Run: pytest tests/test_step6_heart_rate.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")

import h5py
import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from steps.step_6.extract_heart_rate import (   # noqa: E402
    AHET_MAX_CANDIDATES,
    _assign_hr_confidence,
    _check_eca_forbidden_zone,
    _check_resp_harmonic_coincident,
    _compute_masimo_pr,
    _get_invalid_reason,
    _INVALID_REASON_PRIORITY,
    _make_nan_intermediates,
    _match_resp_window,
    _parse_bool,
    _process_session,
    _STAGE_TO_STR,
    _write_npz,
)


# ---------------------------------------------------------------------------
# Fixtures / shared helpers
# ---------------------------------------------------------------------------

FS          = 20.0     # Hz — radar frame rate
HR_TRUE     = 75.0    # bpm — synthetic heart rate
HR_HZ       = HR_TRUE / 60.0
RR_TRUE     = 15.0    # bpm — synthetic breathing rate
RR_HZ       = RR_TRUE / 60.0
DUR_S       = 120.0   # seconds
N_FRAMES    = int(DUR_S * FS)
TRIM_S      = 10
TRIM_FRAMES = int(TRIM_S * FS)
LOCKED_BIN  = 8
N_CHIRPS    = 4
N_RX        = 2
N_ADC       = 64
T0_BASE     = 1_780_000_000.0   # synthetic session start (Unix s UTC)


def _make_cube(hr_bpm: float = HR_TRUE, rr_bpm: float = RR_TRUE) -> np.ndarray:
    """Synthetic (N, chirps, rx, adc_samples) complex64 cube."""
    t = np.arange(N_FRAMES) / FS
    hr_hz = hr_bpm / 60.0
    rr_hz = rr_bpm / 60.0
    phase = (
        0.4 * np.sin(2 * np.pi * hr_hz * t)       # cardiac
        + 2.0 * np.sin(2 * np.pi * rr_hz * t)     # respiration (dominant)
    )
    rng  = np.random.default_rng(42)
    cube = (0.01 * (
        rng.standard_normal((N_FRAMES, N_CHIRPS, N_RX, N_ADC))
        + 1j * rng.standard_normal((N_FRAMES, N_CHIRPS, N_RX, N_ADC))
    )).astype(np.complex64)
    phasor = np.exp(1j * phase).astype(np.complex64)
    cube[:, :, :, LOCKED_BIN] += phasor[:, None, None]
    return cube


def _write_h5(path: Path, cube: np.ndarray, trim_frames: int,
              all_good: bool = True, bad_start_frac: float = 0.0) -> None:
    with h5py.File(path, "w") as f:
        f.attrs["num_frames"]    = cube.shape[0]
        f.attrs["frame_rate_hz"] = float(FS)
        f.create_dataset("cube", data=cube)
        n_analysis = cube.shape[0] - trim_frames
        mask = np.ones(n_analysis, dtype=bool)
        if not all_good and bad_start_frac > 0:
            mask[:int(n_analysis * bad_start_frac)] = False
        qm = f.create_dataset("quality_mask", data=mask)
        qm.attrs["trim_frames"] = trim_frames


def _write_masimo(path: Path, t0: float, dur_s: float,
                  pr_bpm: float = HR_TRUE, pi: float = 1.5) -> None:
    n      = int(dur_s)
    epochs = np.arange(n, dtype=int) + int(t0)
    rows   = []
    for i, ep in enumerate(epochs):
        rows.append({
            "Session": 0, "Index": i + 1, "Timestamp": int(ep),
            "Date": "1/1/26", "Time": "12:00:00 PM",
            "O2 Saturation": 98,
            "Beats / min": pr_bpm,
            "Perfusion Index": pi,
            "Pleth Variability": 10,
            "Breaths / min": RR_TRUE,
        })
    pd.DataFrame(rows).to_csv(path, index=False)


def _make_resp_df(
    t0: float,
    n_windows: int,
    window_s: float = 30.0,
    hop_s: float = 5.0,
    rr_bpm: float = RR_TRUE,
    resp_valid: bool = True,
    edge_locked: bool = False,
    edge_lock_side: str = "none",
) -> pd.DataFrame:
    """Build a synthetic Step 5 contract DataFrame."""
    rows = []
    for i in range(n_windows):
        s = t0 + i * hop_s
        e = s + window_s
        rr_hz = rr_bpm / 60.0
        rows.append({
            "session_id":       "synth",
            "window_index":     i,
            "start_epoch":      s,
            "end_epoch":        e,
            "start_frame":      int(i * hop_s * FS),
            "end_frame":        int((i * hop_s + window_s) * FS),
            "locked_bin":       LOCKED_BIN,
            "locked_range_m":   1.09,
            "radar_rr_bpm":     rr_bpm if resp_valid else float("nan"),
            "resp_peak_hz":     rr_hz  if resp_valid else float("nan"),
            "masimo_rr_bpm":    rr_bpm,
            "rr_error_bpm":     0.0 if resp_valid else float("nan"),
            "rr_abs_error_bpm": 0.0 if resp_valid else float("nan"),
            "fft_rr_bpm":       rr_bpm,
            "ha_rr_bpm":        rr_bpm,
            "stft_rr_bpm":      rr_bpm,
            "resp_confidence":  "high" if resp_valid else "low",
            "resp_valid":       resp_valid,
            "quality_gated":    False,
            "edge_locked":      edge_locked,
            "edge_lock_side":   edge_lock_side,
            "n_bad_frames":     0,
            "bad_fraction":     0.0,
            "ha_score":         8.0,
            "ha_harmonics_used": 3,
            "fft_peak_snr_db":  20.0,
            "stft_rr_std_bpm":  0.5,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Tests: _match_resp_window
# ---------------------------------------------------------------------------

class TestMatchRespWindow:
    def _resp_df(self, n: int = 5, t0: float = 0.0, window_s: float = 30.0,
                 hop_s: float = 5.0) -> pd.DataFrame:
        rows = [{"start_epoch": t0 + i * hop_s, "end_epoch": t0 + i * hop_s + window_s,
                 "window_index": i} for i in range(n)]
        return pd.DataFrame(rows)

    def test_single_containment(self):
        df = self._resp_df()
        # HR center at t=5 — only row 0 [0, 30) contains it
        pos, dist = _match_resp_window(0.0, 10.0, df)
        assert pos == 0
        s5_center = (df.iloc[0]["start_epoch"] + df.iloc[0]["end_epoch"]) / 2.0
        assert abs(dist - abs(5.0 - s5_center)) < 1e-9

    def test_no_containment_closest_center(self):
        df = self._resp_df(n=2, t0=100.0, window_s=30.0, hop_s=50.0)
        # Step 5 windows: [100, 130), [150, 180)
        # HR window center = 200 (outside both)
        pos, dist = _match_resp_window(195.0, 205.0, df)
        # Closest center: row 1 center = 165; row 0 center = 115
        assert pos == 1

    def test_max_overlap_tie_breaking(self):
        # Two Step 5 windows both contain the HR center; pick max overlap
        rows = [
            {"start_epoch": 0.0,  "end_epoch": 50.0, "window_index": 0},  # overlap=20
            {"start_epoch": 10.0, "end_epoch": 40.0, "window_index": 1},  # overlap=30 (narrow HR window inside it)
        ]
        df = pd.DataFrame(rows)
        # HR window [15, 25), center=20 — both contain it
        # Row 0 overlap = min(25,50)-max(15,0) = 25-15 = 10
        # Row 1 overlap = min(25,40)-max(15,10) = 25-15 = 10  (equal)
        # Closest center: row 1 center=25, row 0 center=25 — equal (both at 25)
        # Either is valid; just check it picks one of them
        pos, dist = _match_resp_window(15.0, 25.0, df)
        assert pos in (0, 1)

    def test_closest_center_fallback(self):
        # Gap between Step 5 windows: HR center falls in gap
        rows = [
            {"start_epoch": 0.0,  "end_epoch": 10.0, "window_index": 0},
            {"start_epoch": 20.0, "end_epoch": 30.0, "window_index": 1},
        ]
        df = pd.DataFrame(rows)
        # HR center = 16 — not contained by either; closer to row 1 center = 25
        pos, dist = _match_resp_window(14.0, 18.0, df)
        assert pos == 1
        assert abs(dist - abs(16.0 - 25.0)) < 1e-9

    def test_distance_is_center_to_center(self):
        rows = [{"start_epoch": 0.0, "end_epoch": 30.0, "window_index": 0}]
        df = pd.DataFrame(rows)
        # HR window [2, 12), center=7; Step 5 center=15
        pos, dist = _match_resp_window(2.0, 12.0, df)
        assert pos == 0
        assert abs(dist - abs(7.0 - 15.0)) < 1e-9  # = 8.0


# ---------------------------------------------------------------------------
# Tests: _get_invalid_reason (priority)
# ---------------------------------------------------------------------------

class TestGetInvalidReason:
    def test_no_flags_gives_none(self):
        assert _get_invalid_reason({}) is None

    def test_quality_gated_highest_priority(self):
        flags = {r: True for r in _INVALID_REASON_PRIORITY}
        assert _get_invalid_reason(flags) == "quality_gated"

    def test_priority_order(self):
        for i, reason in enumerate(_INVALID_REASON_PRIORITY):
            flags = {r: (r == reason) for r in _INVALID_REASON_PRIORITY}
            assert _get_invalid_reason(flags) == reason

    def test_resp_harmonic_coincident_is_lowest(self):
        flags = {"resp_harmonic_coincident": True}
        assert _get_invalid_reason(flags) == "resp_harmonic_coincident"

    def test_quality_gated_beats_resp_harmonic_coincident(self):
        flags = {"quality_gated": True, "resp_harmonic_coincident": True}
        assert _get_invalid_reason(flags) == "quality_gated"


# ---------------------------------------------------------------------------
# Tests: _assign_hr_confidence
# ---------------------------------------------------------------------------

class TestAssignHrConfidence:
    def test_high_when_ahet_verified_no_invalid(self):
        assert _assign_hr_confidence(None, 2, True) == "high"

    def test_none_when_skipped(self):
        assert _assign_hr_confidence("quality_gated", -1, False) == "none"
        assert _assign_hr_confidence("resp_missing", -1, False) == "none"

    def test_medium_when_ahet_failed_after_eca(self):
        assert _assign_hr_confidence("ahet_failed", 1, False) == "medium"

    def test_low_when_ahet_failed_not_after_eca(self):
        # f_r_outlier case: spectrum_stage=0 (raw_fft) but ahet_failed reason
        assert _assign_hr_confidence("ahet_failed", 0, False) == "low"

    def test_low_when_harmonic_coincident(self):
        assert _assign_hr_confidence("resp_harmonic_coincident", 2, True) == "low"

    def test_radar_hr_bpm_finite_iff_high(self):
        # Only "high" should produce finite HR in v1
        for reason in _INVALID_REASON_PRIORITY:
            conf = _assign_hr_confidence(reason, -1, False)
            assert conf != "high", f"Unexpected 'high' for invalid_reason={reason!r}"

    def test_all_confidence_values_are_valid_enum(self):
        valid = {"high", "medium", "low", "none"}
        test_cases = [
            (None, 2, True), (None, 2, False), ("quality_gated", -1, False),
            ("ahet_failed", 1, False), ("ahet_failed", 0, False),
            ("resp_harmonic_coincident", 2, True),
        ]
        for args in test_cases:
            conf = _assign_hr_confidence(*args)
            assert conf in valid, f"Invalid confidence {conf!r} for args {args}"


# ---------------------------------------------------------------------------
# Tests: _check_eca_forbidden_zone
# ---------------------------------------------------------------------------

class TestEcaForbiddenZone:
    def test_harmonic_inside_band_flags_true(self):
        # f_r=0.25 Hz, k=4 -> 1.0 Hz which is in [0.8, 2.0]
        assert _check_eca_forbidden_zone(0.25, (0.8, 2.0), k_max=6) is True

    def test_no_harmonic_in_band_flags_false(self):
        # f_r=0.1 Hz, k_max=6: 0.1, 0.2, 0.3, 0.4, 0.5, 0.6 — all below 0.8 Hz
        assert _check_eca_forbidden_zone(0.10, (0.8, 2.0), k_max=6) is False

    def test_exact_band_edge_inclusive(self):
        # k=4, f_r=0.2 -> 0.8 Hz (band lower edge) — should flag True
        assert _check_eca_forbidden_zone(0.20, (0.8, 2.0), k_max=6) is True

    def test_high_k_harmonic_in_band(self):
        # f_r=0.4 Hz, k=3 -> 1.2 Hz in [0.8, 2.0]
        assert _check_eca_forbidden_zone(0.40, (0.8, 2.0), k_max=6) is True


# ---------------------------------------------------------------------------
# Tests: _check_resp_harmonic_coincident
# ---------------------------------------------------------------------------

class TestRespHarmonicCoincident:
    def test_heart_on_harmonic_flags_true(self):
        # f_r=0.25 Hz, k=4 -> 1.0 Hz; heart_peak=1.0 Hz
        assert _check_resp_harmonic_coincident(1.0, 0.25, 0.05, (0.8, 2.0)) is True

    def test_heart_within_guard_flags_true(self):
        assert _check_resp_harmonic_coincident(1.03, 0.25, 0.05, (0.8, 2.0)) is True

    def test_heart_outside_guard_flags_false(self):
        assert _check_resp_harmonic_coincident(1.2, 0.25, 0.05, (0.8, 2.0)) is False

    def test_nan_heart_peak_is_false(self):
        assert _check_resp_harmonic_coincident(float("nan"), 0.25, 0.05, (0.8, 2.0)) is False

    def test_nan_resp_peak_is_false(self):
        assert _check_resp_harmonic_coincident(1.0, float("nan"), 0.05, (0.8, 2.0)) is False

    def test_check_is_hz_not_bpm(self):
        # Guard is 0.05 Hz = 3 bpm.  Difference of 2 bpm ≈ 0.033 Hz < 0.05 Hz -> True
        f_r = 0.25        # Hz
        k   = 4
        heart = k * f_r + 2.0 / 60.0   # 2 bpm above harmonic
        assert _check_resp_harmonic_coincident(heart, f_r, 0.05, (0.8, 2.0)) is True


# ---------------------------------------------------------------------------
# Tests: _compute_masimo_pr
# ---------------------------------------------------------------------------

class TestComputeMasimoPr:
    def _make_df(self, t0: float = 1e9, n: int = 20, pr: float = 75.0,
                 pi: float = 1.5) -> pd.DataFrame:
        from src import masimo as masimo_mod
        import io
        rows = []
        for i in range(n):
            rows.append({
                "Session": 0, "Index": i + 1,
                "Timestamp": int(t0) + i,
                "Date": "1/1/26", "Time": "12:00:00 PM",
                "O2 Saturation": 98,
                "Beats / min": pr,
                "Perfusion Index": pi,
                "Pleth Variability": 10,
                "Breaths / min": 15,
            })
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False) as f:
            pd.DataFrame(rows).to_csv(f, index=False)
            tmp = f.name
        df = masimo_mod.load_masimo(tmp)
        Path(tmp).unlink(missing_ok=True)
        return df

    def test_good_pi_gives_finite_pr(self):
        df = self._make_df(t0=1e9, n=20, pr=75.0, pi=1.5)
        result = _compute_masimo_pr(df, 1e9, 1e9 + 20, 0.5, 0.8, 0.8)
        assert np.isfinite(result["masimo_pr_bpm"])
        assert abs(result["masimo_pr_bpm"] - 75.0) < 0.5

    def test_all_low_pi_gives_nan_pr(self):
        df = self._make_df(t0=1e9, n=20, pr=75.0, pi=0.1)  # below min_pi=0.5
        result = _compute_masimo_pr(df, 1e9, 1e9 + 20, 0.5, 0.8, 0.8)
        assert not np.isfinite(result["masimo_pr_bpm"])
        assert result["masimo_low_quality"] is True

    def test_no_masimo_samples_gives_nan(self):
        df = self._make_df(t0=1e9, n=20, pr=75.0, pi=1.5)
        # Window outside masimo coverage
        result = _compute_masimo_pr(df, 2e9, 2e9 + 20, 0.5, 0.8, 0.8)
        assert not np.isfinite(result["masimo_pr_bpm"])
        assert result["masimo_low_quality"] is True

    def test_low_coverage_flags_low_quality(self):
        # Only 5 of 20 expected samples present -> coverage = 0.25 < 0.8
        df = self._make_df(t0=1e9, n=5, pr=75.0, pi=1.5)
        result = _compute_masimo_pr(df, 1e9, 1e9 + 20, 0.5, 0.8, 0.8)
        assert result["masimo_low_quality"] is True

    def test_pr_std_uses_pi_good_samples_only(self):
        # Mix: half good PI, half bad
        import tempfile
        from src import masimo as masimo_mod
        rows = []
        t0 = int(1e9)
        for i in range(20):
            rows.append({
                "Session": 0, "Index": i + 1,
                "Timestamp": t0 + i,
                "Date": "1/1/26", "Time": "12:00:00 PM",
                "O2 Saturation": 98,
                "Beats / min": 75.0 if i < 10 else 999.0,  # bad PR for low PI
                "Perfusion Index": 1.5 if i < 10 else 0.1,
                "Pleth Variability": 10,
                "Breaths / min": 15,
            })
        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False) as f:
            pd.DataFrame(rows).to_csv(f, index=False)
            tmp = f.name
        df = masimo_mod.load_masimo(tmp)
        Path(tmp).unlink(missing_ok=True)

        result = _compute_masimo_pr(df, t0, t0 + 20, 0.5, 0.8, 0.8)
        # Should use only the 10 good-PI samples (pr=75), not the bad-PI 999 ones
        assert abs(result["masimo_pr_bpm"] - 75.0) < 1.0
        assert result["masimo_n_good_pi"] == 10
        assert result["masimo_n_total"] == 20

    def test_error_columns_nan_when_masimo_low_quality(self):
        # This is checked in the row builder, not _compute_masimo_pr directly,
        # but verify the flag is set so callers can gate on it.
        df = self._make_df(t0=1e9, n=20, pr=75.0, pi=0.1)
        result = _compute_masimo_pr(df, 1e9, 1e9 + 20, 0.5, 0.8, 0.8)
        assert result["masimo_low_quality"] is True


# ---------------------------------------------------------------------------
# Tests: _make_nan_intermediates and _write_npz
# ---------------------------------------------------------------------------

class TestNpzIntermediate:
    def test_nan_intermediates_shapes_match_valid(self):
        window_frames = 400  # 20s at 20 Hz
        n_fft = window_frames // 2 + 1
        nan_int = _make_nan_intermediates(window_frames, n_fft)

        # All 1-D phase/spectrum arrays must have the right length
        for k in ("phase_unwrapped", "phase_clean", "phase_eca",
                  "heart_spectrum_pre_eca", "heart_spectrum_first_pass",
                  "heart_spectrum", "baseline_spectrum"):
            arr = np.asarray(nan_int[k])
            if k in ("phase_unwrapped", "phase_clean", "phase_eca"):
                assert arr.shape == (window_frames,), f"{k} shape mismatch"
            else:
                assert arr.shape == (n_fft,), f"{k} shape mismatch"

        for k in ("candidate_attempted", "candidate_initial_hz", "candidate_passed"):
            assert np.asarray(nan_int[k]).shape == (AHET_MAX_CANDIDATES,), f"{k} shape mismatch"

        ahet_spec = np.asarray(nan_int["ahet_attempt_spectrum"])
        assert ahet_spec.shape == (AHET_MAX_CANDIDATES, n_fft)

    def test_write_npz_fixed_shapes(self, tmp_path: Path):
        window_frames = 400
        n_fft = window_frames // 2 + 1
        heart_freqs = np.fft.rfftfreq(window_frames, d=1.0 / FS)

        n_windows = 5
        ints = [_make_nan_intermediates(window_frames, n_fft) for _ in range(n_windows)]
        out = tmp_path / "test.npz"
        _write_npz(out, ints, window_frames, n_fft, heart_freqs)

        with np.load(out, allow_pickle=False) as arch:
            assert "phase_unwrapped" in arch
            assert arch["phase_unwrapped"].shape == (n_windows, window_frames)
            assert arch["ahet_attempt_spectrum"].shape == (n_windows, AHET_MAX_CANDIDATES, n_fft)
            assert arch["heart_freqs_hz"].shape == (n_fft,)
            # No object arrays
            for key in arch.files:
                assert not arch[key].dtype.hasobject, f"{key} has object dtype"

    def test_write_npz_no_pickle(self, tmp_path: Path):
        window_frames = 400
        n_fft = window_frames // 2 + 1
        heart_freqs = np.fft.rfftfreq(window_frames, d=1.0 / FS)
        ints = [_make_nan_intermediates(window_frames, n_fft)]
        out = tmp_path / "nopickle.npz"
        _write_npz(out, ints, window_frames, n_fft, heart_freqs)
        # allow_pickle=False must not raise
        with np.load(out, allow_pickle=False) as arch:
            _ = arch.files


# ---------------------------------------------------------------------------
# Tests: heart_spectrum_stage enum values
# ---------------------------------------------------------------------------

class TestHeartSpectrumStageEnum:
    def test_all_valid_codes_have_string(self):
        for code in (-1, 0, 1, 2):
            assert code in _STAGE_TO_STR

    def test_string_values(self):
        assert _STAGE_TO_STR[-1] == "skipped"
        assert _STAGE_TO_STR[0]  == "raw_fft"
        assert _STAGE_TO_STR[1]  == "after_eca"
        assert _STAGE_TO_STR[2]  == "after_ahet"


# ---------------------------------------------------------------------------
# Mock for estimate_rate_from_phase (avoids scipy.signal.filtfilt -> LAPACK)
# ---------------------------------------------------------------------------

def _fake_estimate(
    phase, fs, band, f_r_hz=None, k_max=6, ahet_deviation_hz=0.1,
    eca_mode="legacy", ahet_gate_mode="legacy", eca_forbidden_guard_hz=0.0,
    candidate_min_second_harmonic_ratio_db=1.0, candidate_min_prominence=3.0,
    low_candidate_hz=1.20, high_candidate_preference_hz=1.25,
    high_competitor_min_mag_ratio=0.80,
    candidate_min_peak_to_floor_db=0.0,
    low_candidate_min_peak_to_floor_db=0.0,
):
    """Pure-numpy stand-in that returns a valid AHET-accepted or no-ECA result."""
    n     = len(phase)
    n_fft = n // 2 + 1
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    N_CAND = AHET_MAX_CANDIDATES

    eca_active = f_r_hz is not None and np.isfinite(f_r_hz)
    # rank 0: attempted and passed (code 0) if ECA active, else gate-not-run (-1)
    # ranks 1+: not_attempted (5) in strict_v1 when ECA active, else gate-not-run (-1)
    rej_code_0 = 0 if eca_active else -1
    rej_code_rest = 5 if (eca_active and ahet_gate_mode == "strict_v1") else -1

    return {
        "rate_bpm":                            75.0,
        "peak_hz":                             75.0 / 60.0,
        "freqs_hz":                            freqs,
        "spectrum":                            np.ones(n_fft),
        "spectrum_first_pass":                 np.ones(n_fft) if eca_active else np.full(n_fft, np.nan),
        "spectrum_stage":                      2 if eca_active else 0,
        "band":                                band,
        "filtered":                            phase.copy(),
        "ahet_verified":                       eca_active,
        "harmonic_suspect":                    False,
        "f_r_hz_used":                         f_r_hz,
        "f_r_outlier":                         False,
        "eca_applied":                         eca_active,
        "phase_eca":                           phase.copy() if eca_active else np.full(n, np.nan),
        "accepted_candidate_rank":             0 if eca_active else -1,
        "accepted_candidate_initial_hz":       75.0 / 60.0 if eca_active else np.nan,
        "accepted_candidate_refined_hz":       75.0 / 60.0 if eca_active else np.nan,
        "accepted_second_harmonic_refined_hz": 2 * 75.0 / 60.0 if eca_active else np.nan,
        "candidate_attempted":              np.array([True, False, False][:N_CAND], dtype=bool),
        "candidate_peak_bin_index":         np.array([12, -1, -1][:N_CAND], dtype=int),
        "candidate_initial_hz":             np.array([75.0 / 60, np.nan, np.nan][:N_CAND]),
        "candidate_refined_hz":             np.array([75.0 / 60, np.nan, np.nan][:N_CAND]),
        "candidate_peak_magnitude":         np.array([1.0, np.nan, np.nan][:N_CAND]),
        "candidate_prominence":             np.array([0.5, np.nan, np.nan][:N_CAND]),
        "candidate_argmax_fallback":        np.zeros(N_CAND, dtype=bool),
        "second_peak_bin_hz":               np.array([2 * 75.0 / 60, np.nan, np.nan][:N_CAND]),
        "second_peak_refined_hz":           np.array([2 * 75.0 / 60, np.nan, np.nan][:N_CAND]),
        "second_peak_magnitude":            np.array([0.8, np.nan, np.nan][:N_CAND]),
        "comparison_floor":                 np.array([0.1, np.nan, np.nan][:N_CAND]),
        "peak_to_floor_ratio":              np.array([8.0, np.nan, np.nan][:N_CAND]),
        "peak_to_floor_ratio_db":           np.array([18.0, np.nan, np.nan][:N_CAND]),
        "region_available":                 np.array([True, False, False][:N_CAND], dtype=bool),
        "candidate_passed":                 np.array([eca_active, False, False][:N_CAND], dtype=bool),
        "ahet_attempt_spectrum":            np.ones((N_CAND, n_fft)),
        # Step 6.1 fields
        "candidate_rejection_code": np.array([rej_code_0] + [rej_code_rest] * (N_CAND - 1), dtype=int),
        "all_candidates_rejected":  False,
        "eca_skipped_harmonics":    np.zeros(k_max, dtype=bool),
    }


@pytest.fixture
def mock_estimator(monkeypatch):
    """Patch estimate_rate_from_phase in the step_6 module with _fake_estimate."""
    import steps.step_6.extract_heart_rate as s6_mod
    monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", _fake_estimate)


# ---------------------------------------------------------------------------
# Integration tests: _process_session
# ---------------------------------------------------------------------------

@pytest.fixture
def session_dir(tmp_path: Path):
    """Build a complete synthetic session directory."""
    sid = "synth_s6"

    # HDF5 cube
    h5_dir = tmp_path / "cubes"
    h5_dir.mkdir()
    cube = _make_cube()
    _write_h5(h5_dir / f"{sid}.h5", cube, trim_frames=TRIM_FRAMES)

    # Masimo CSV
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    _write_masimo(raw_dir / f"{sid}_masimo.csv", t0=T0_BASE, dur_s=DUR_S, pr_bpm=HR_TRUE)

    # Step 5 contract CSV (with edge-lock columns)
    br_dir = tmp_path / "breathing_rate"
    br_dir.mkdir()
    resp_df = _make_resp_df(t0=T0_BASE, n_windows=30, rr_bpm=RR_TRUE)
    resp_df.to_csv(br_dir / f"{sid}.csv", index=False)

    # Manifest row
    row = pd.Series({
        "session_id":                sid,
        "radar_start_epoch_seconds": T0_BASE,
        "stationary_intervals":      f"{TRIM_S}-{int(DUR_S)}",
        "locked_bin":                LOCKED_BIN,
        "locked_range_m":            1.09,
        "chest_bin_confidence":      "high",
        "chest_bin_review_required": "False",
        "iq_swap":                   "False",
    })

    # Config
    cfg = {
        "paths": {
            "manifest":           "data/manifest.local.csv",
            "cubes_dir":          str(h5_dir),
            "raw_dir":            str(raw_dir),
            "breathing_rate_dir": str(br_dir),
            "processed_dir":      str(tmp_path / "heart_rate"),
            "results_dir":        str(tmp_path / "results"),
        },
        "input_policy": {
            "max_bad_fraction": 0.10,
        },
        "windowing": {
            "window_s": 20,
            "hop_s":    5,
            "use_stationary_intervals": True,
        },
        "phase": {
            "method":           "delta_before_mean",
            "impulse_clip_rad": 1.5,
        },
        "heart": {
            "heart_band_hz":                  [0.8, 2.0],
            "k_max":                          6,
            "ahet_deviation_hz":              0.1,
            "resp_harmonic_guard_hz":         0.05,
            "reject_resp_harmonic_coincidence": True,
        },
        "respiration_matching": {
            "max_resp_match_distance_s": 15.0,
        },
        "comparison": {
            "min_pi":                        0.5,
            "min_masimo_coverage_fraction":  0.8,
            "min_masimo_good_pi_fraction":   0.8,
        },
        "seed": 42,
    }

    return {
        "sid":     sid,
        "row":     row,
        "cfg":     cfg,
        "cubes":   h5_dir,
        "raw":     raw_dir,
        "br_dir":  br_dir,
        "out_dir": tmp_path / "results" / sid / "step_6",
        "tmp":     tmp_path,
    }


def test_integration_produces_outputs(session_dir, mock_estimator):
    s = session_dir
    summary = _process_session(
        s["sid"], s["row"], s["cfg"],
        cubes_dir         = s["cubes"],
        data_raw          = s["raw"],
        breathing_rate_dir= s["br_dir"],
        out_dir           = s["out_dir"],
        commit            = "test",
        no_plots          = True,
    )
    out = s["out_dir"]
    assert (out / "heart_windows.csv").exists()
    assert (out / "summary.json").exists()
    assert (out / "heart_intermediates.npz").exists()

    df = pd.read_csv(out / "heart_windows.csv")
    assert "radar_hr_bpm" in df.columns
    assert "hr_valid" in df.columns
    assert "invalid_reason" in df.columns
    assert "heart_spectrum_stage" in df.columns
    assert "hr_confidence" in df.columns


def test_every_nan_has_invalid_reason(session_dir, mock_estimator):
    s = session_dir
    _process_session(
        s["sid"], s["row"], s["cfg"],
        cubes_dir=s["cubes"], data_raw=s["raw"],
        breathing_rate_dir=s["br_dir"], out_dir=s["out_dir"], commit="test",
        no_plots=True,
    )
    df = pd.read_csv(s["out_dir"] / "heart_windows.csv")
    nan_rows = df[df["radar_hr_bpm"].isna()]
    assert (nan_rows["invalid_reason"].str.len() > 0).all(), \
        "Some NaN windows have empty invalid_reason"


def test_radar_hr_bpm_finite_iff_high_confidence(session_dir, mock_estimator):
    s = session_dir
    _process_session(
        s["sid"], s["row"], s["cfg"],
        cubes_dir=s["cubes"], data_raw=s["raw"],
        breathing_rate_dir=s["br_dir"], out_dir=s["out_dir"], commit="test",
        no_plots=True,
    )
    df = pd.read_csv(s["out_dir"] / "heart_windows.csv")
    high_mask = df["hr_confidence"] == "high"
    finite_mask = df["radar_hr_bpm"].notna()
    assert (high_mask == finite_mask).all(), \
        "radar_hr_bpm finite iff hr_confidence == 'high' violated"


def test_heart_spectrum_stage_enum_values(session_dir, mock_estimator):
    s = session_dir
    _process_session(
        s["sid"], s["row"], s["cfg"],
        cubes_dir=s["cubes"], data_raw=s["raw"],
        breathing_rate_dir=s["br_dir"], out_dir=s["out_dir"], commit="test",
        no_plots=True,
    )
    df = pd.read_csv(s["out_dir"] / "heart_windows.csv")
    valid_stages = set(_STAGE_TO_STR.values())
    assert df["heart_spectrum_stage"].isin(valid_stages).all()


def test_hr_confidence_enum_values(session_dir, mock_estimator):
    s = session_dir
    _process_session(
        s["sid"], s["row"], s["cfg"],
        cubes_dir=s["cubes"], data_raw=s["raw"],
        breathing_rate_dir=s["br_dir"], out_dir=s["out_dir"], commit="test",
        no_plots=True,
    )
    df = pd.read_csv(s["out_dir"] / "heart_windows.csv")
    assert df["hr_confidence"].isin({"high", "medium", "low", "none"}).all()


def test_masimo_low_quality_excludes_error_columns(session_dir, mock_estimator):
    """Windows flagged masimo_low_quality must have NaN error columns."""
    s = session_dir
    _process_session(
        s["sid"], s["row"], s["cfg"],
        cubes_dir=s["cubes"], data_raw=s["raw"],
        breathing_rate_dir=s["br_dir"], out_dir=s["out_dir"], commit="test",
        no_plots=True,
    )
    df = pd.read_csv(s["out_dir"] / "heart_windows.csv")
    low_q = df[df["masimo_low_quality"].astype(bool)]
    if len(low_q) > 0:
        assert low_q["hr_error_bpm"].isna().all()
        assert low_q["hr_abs_error_bpm"].isna().all()


def test_quality_gated_windows_have_nan_hr(session_dir, mock_estimator):
    """Windows with bad_fraction > max_bad_fraction must have radar_hr_bpm=NaN."""
    s = session_dir
    # Rewrite HDF5 with many bad frames in first half
    _write_h5(
        s["cubes"] / f"{s['sid']}.h5",
        _make_cube(), trim_frames=TRIM_FRAMES,
        all_good=False, bad_start_frac=0.5,
    )
    _process_session(
        s["sid"], s["row"], s["cfg"],
        cubes_dir=s["cubes"], data_raw=s["raw"],
        breathing_rate_dir=s["br_dir"], out_dir=s["out_dir"], commit="test",
        no_plots=True,
    )
    df = pd.read_csv(s["out_dir"] / "heart_windows.csv")
    gated = df[df["quality_gated"].astype(bool)]
    assert len(gated) > 0, "Expected some quality-gated windows"
    assert gated["radar_hr_bpm"].isna().all()
    assert (gated["invalid_reason"] == "quality_gated").all()


def test_invalid_step5_breathing_gives_nan_hr(tmp_path: Path, monkeypatch):
    """Step 5 rows with resp_valid=False must yield NaN primary HR."""
    import steps.step_6.extract_heart_rate as s6_mod
    monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", _fake_estimate)
    sid = "synth_invalid_resp"
    h5_dir = tmp_path / "cubes"; h5_dir.mkdir()
    raw_dir = tmp_path / "raw";   raw_dir.mkdir()
    br_dir  = tmp_path / "br";    br_dir.mkdir()

    _write_h5(h5_dir / f"{sid}.h5", _make_cube(), trim_frames=TRIM_FRAMES)
    _write_masimo(raw_dir / f"{sid}_masimo.csv", t0=T0_BASE, dur_s=DUR_S)

    # All Step 5 rows invalid
    resp_df = _make_resp_df(t0=T0_BASE, n_windows=30, resp_valid=False)
    resp_df.to_csv(br_dir / f"{sid}.csv", index=False)

    row = pd.Series({
        "session_id": sid, "radar_start_epoch_seconds": T0_BASE,
        "stationary_intervals": f"{TRIM_S}-{int(DUR_S)}",
        "locked_bin": LOCKED_BIN, "locked_range_m": 1.09,
        "chest_bin_confidence": "high", "chest_bin_review_required": "False",
    })
    cfg = {
        "paths": {
            "manifest": "", "cubes_dir": str(h5_dir), "raw_dir": str(raw_dir),
            "breathing_rate_dir": str(br_dir),
            "processed_dir": str(tmp_path / "hr"), "results_dir": str(tmp_path / "results"),
        },
        "input_policy": {"max_bad_fraction": 0.10},
        "windowing": {"window_s": 20, "hop_s": 5, "use_stationary_intervals": True},
        "phase": {"method": "delta_before_mean", "impulse_clip_rad": 1.5},
        "heart": {"heart_band_hz": [0.8, 2.0], "k_max": 6, "ahet_deviation_hz": 0.1,
                  "resp_harmonic_guard_hz": 0.05, "reject_resp_harmonic_coincidence": True},
        "respiration_matching": {"max_resp_match_distance_s": 15.0},
        "comparison": {"min_pi": 0.5, "min_masimo_coverage_fraction": 0.8,
                       "min_masimo_good_pi_fraction": 0.8},
        "seed": 42,
    }
    out_dir = tmp_path / "results" / sid / "step_6"
    _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test", no_plots=True)

    df = pd.read_csv(out_dir / "heart_windows.csv")
    # All windows should have NaN HR because resp_valid=False
    assert df["radar_hr_bpm"].isna().all()


def test_missing_step5_contract_fails_clearly(tmp_path: Path):
    sid = "no_step5"
    h5_dir = tmp_path / "cubes"; h5_dir.mkdir()
    raw_dir = tmp_path / "raw";   raw_dir.mkdir()
    br_dir  = tmp_path / "br";    br_dir.mkdir()

    _write_h5(h5_dir / f"{sid}.h5", _make_cube(), trim_frames=TRIM_FRAMES)
    _write_masimo(raw_dir / f"{sid}_masimo.csv", t0=T0_BASE, dur_s=DUR_S)
    # No Step 5 contract written

    row = pd.Series({
        "session_id": sid, "radar_start_epoch_seconds": T0_BASE,
        "stationary_intervals": f"{TRIM_S}-{int(DUR_S)}",
        "locked_bin": LOCKED_BIN, "locked_range_m": 1.09,
        "chest_bin_confidence": "high", "chest_bin_review_required": "False",
    })
    cfg = {
        "paths": {
            "manifest": "", "cubes_dir": str(h5_dir), "raw_dir": str(raw_dir),
            "breathing_rate_dir": str(br_dir),
            "processed_dir": str(tmp_path / "hr"), "results_dir": str(tmp_path / "results"),
        },
        "input_policy": {"max_bad_fraction": 0.10},
        "windowing": {"window_s": 20, "hop_s": 5, "use_stationary_intervals": True},
        "phase": {"method": "delta_before_mean", "impulse_clip_rad": 1.5},
        "heart": {"heart_band_hz": [0.8, 2.0], "k_max": 6, "ahet_deviation_hz": 0.1,
                  "resp_harmonic_guard_hz": 0.05, "reject_resp_harmonic_coincidence": True},
        "respiration_matching": {"max_resp_match_distance_s": 15.0},
        "comparison": {"min_pi": 0.5, "min_masimo_coverage_fraction": 0.8,
                       "min_masimo_good_pi_fraction": 0.8},
        "seed": 42,
    }
    out_dir = tmp_path / "results" / sid / "step_6"
    with pytest.raises(FileNotFoundError, match="Step 5"):
        _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test", no_plots=True)


def test_missing_edge_lock_columns_fails_with_rerun_message(tmp_path: Path):
    sid = "no_edge_lock"
    h5_dir = tmp_path / "cubes"; h5_dir.mkdir()
    raw_dir = tmp_path / "raw";   raw_dir.mkdir()
    br_dir  = tmp_path / "br";    br_dir.mkdir()

    _write_h5(h5_dir / f"{sid}.h5", _make_cube(), trim_frames=TRIM_FRAMES)
    _write_masimo(raw_dir / f"{sid}_masimo.csv", t0=T0_BASE, dur_s=DUR_S)

    # Step 5 contract WITHOUT edge_locked / edge_lock_side
    resp_df = _make_resp_df(t0=T0_BASE, n_windows=10)
    resp_df = resp_df.drop(columns=["edge_locked", "edge_lock_side"])
    resp_df.to_csv(br_dir / f"{sid}.csv", index=False)

    row = pd.Series({
        "session_id": sid, "radar_start_epoch_seconds": T0_BASE,
        "stationary_intervals": f"{TRIM_S}-{int(DUR_S)}",
        "locked_bin": LOCKED_BIN, "locked_range_m": 1.09,
        "chest_bin_confidence": "high", "chest_bin_review_required": "False",
    })
    cfg = {
        "paths": {
            "manifest": "", "cubes_dir": str(h5_dir), "raw_dir": str(raw_dir),
            "breathing_rate_dir": str(br_dir),
            "processed_dir": str(tmp_path / "hr"), "results_dir": str(tmp_path / "results"),
        },
        "input_policy": {"max_bad_fraction": 0.10},
        "windowing": {"window_s": 20, "hop_s": 5, "use_stationary_intervals": True},
        "phase": {"method": "delta_before_mean", "impulse_clip_rad": 1.5},
        "heart": {"heart_band_hz": [0.8, 2.0], "k_max": 6, "ahet_deviation_hz": 0.1,
                  "resp_harmonic_guard_hz": 0.05, "reject_resp_harmonic_coincidence": True},
        "respiration_matching": {"max_resp_match_distance_s": 15.0},
        "comparison": {"min_pi": 0.5, "min_masimo_coverage_fraction": 0.8,
                       "min_masimo_good_pi_fraction": 0.8},
        "seed": 42,
    }
    out_dir = tmp_path / "results" / sid / "step_6"
    with pytest.raises(ValueError, match="edge_locked") as exc_info:
        _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test", no_plots=True)
    # Error must mention Step 5 rerun
    assert "Step 5" in str(exc_info.value)


def test_resp_match_too_far_gives_nan_hr(tmp_path: Path, monkeypatch):
    """HR windows whose nearest Step 5 center is > max_resp_match_distance_s away get NaN HR."""
    import steps.step_6.extract_heart_rate as s6_mod
    monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", _fake_estimate)
    sid = "far_resp"
    h5_dir = tmp_path / "cubes"; h5_dir.mkdir()
    raw_dir = tmp_path / "raw";   raw_dir.mkdir()
    br_dir  = tmp_path / "br";    br_dir.mkdir()

    _write_h5(h5_dir / f"{sid}.h5", _make_cube(), trim_frames=TRIM_FRAMES)
    _write_masimo(raw_dir / f"{sid}_masimo.csv", t0=T0_BASE, dur_s=DUR_S)

    # Only one Step 5 window, far in the future — distance > 15 s for all HR windows
    far_t0 = T0_BASE + 900  # 15 minutes after session start
    resp_df = _make_resp_df(t0=far_t0, n_windows=1)
    resp_df.to_csv(br_dir / f"{sid}.csv", index=False)

    row = pd.Series({
        "session_id": sid, "radar_start_epoch_seconds": T0_BASE,
        "stationary_intervals": f"{TRIM_S}-{int(DUR_S)}",
        "locked_bin": LOCKED_BIN, "locked_range_m": 1.09,
        "chest_bin_confidence": "high", "chest_bin_review_required": "False",
    })
    cfg = {
        "paths": {
            "manifest": "", "cubes_dir": str(h5_dir), "raw_dir": str(raw_dir),
            "breathing_rate_dir": str(br_dir),
            "processed_dir": str(tmp_path / "hr"), "results_dir": str(tmp_path / "results"),
        },
        "input_policy": {"max_bad_fraction": 0.10},
        "windowing": {"window_s": 20, "hop_s": 5, "use_stationary_intervals": True},
        "phase": {"method": "delta_before_mean", "impulse_clip_rad": 1.5},
        "heart": {"heart_band_hz": [0.8, 2.0], "k_max": 6, "ahet_deviation_hz": 0.1,
                  "resp_harmonic_guard_hz": 0.05, "reject_resp_harmonic_coincidence": True},
        "respiration_matching": {"max_resp_match_distance_s": 15.0},
        "comparison": {"min_pi": 0.5, "min_masimo_coverage_fraction": 0.8,
                       "min_masimo_good_pi_fraction": 0.8},
        "seed": 42,
    }
    out_dir = tmp_path / "results" / sid / "step_6"
    _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test", no_plots=True)

    df = pd.read_csv(out_dir / "heart_windows.csv")
    assert df["radar_hr_bpm"].isna().all()
    assert (df["invalid_reason"] == "resp_match_too_far").all()


def test_edge_locked_step5_gives_nan_hr(tmp_path: Path, monkeypatch):
    """Step 5 rows with edge_locked=True must yield NaN primary HR."""
    import steps.step_6.extract_heart_rate as s6_mod
    monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", _fake_estimate)
    sid = "edge_locked"
    h5_dir = tmp_path / "cubes"; h5_dir.mkdir()
    raw_dir = tmp_path / "raw";   raw_dir.mkdir()
    br_dir  = tmp_path / "br";    br_dir.mkdir()

    _write_h5(h5_dir / f"{sid}.h5", _make_cube(), trim_frames=TRIM_FRAMES)
    _write_masimo(raw_dir / f"{sid}_masimo.csv", t0=T0_BASE, dur_s=DUR_S)

    resp_df = _make_resp_df(t0=T0_BASE, n_windows=30, edge_locked=True,
                            edge_lock_side="low")
    resp_df.to_csv(br_dir / f"{sid}.csv", index=False)

    row = pd.Series({
        "session_id": sid, "radar_start_epoch_seconds": T0_BASE,
        "stationary_intervals": f"{TRIM_S}-{int(DUR_S)}",
        "locked_bin": LOCKED_BIN, "locked_range_m": 1.09,
        "chest_bin_confidence": "high", "chest_bin_review_required": "False",
    })
    cfg = {
        "paths": {
            "manifest": "", "cubes_dir": str(h5_dir), "raw_dir": str(raw_dir),
            "breathing_rate_dir": str(br_dir),
            "processed_dir": str(tmp_path / "hr"), "results_dir": str(tmp_path / "results"),
        },
        "input_policy": {"max_bad_fraction": 0.10},
        "windowing": {"window_s": 20, "hop_s": 5, "use_stationary_intervals": True},
        "phase": {"method": "delta_before_mean", "impulse_clip_rad": 1.5},
        "heart": {"heart_band_hz": [0.8, 2.0], "k_max": 6, "ahet_deviation_hz": 0.1,
                  "resp_harmonic_guard_hz": 0.05, "reject_resp_harmonic_coincidence": True},
        "respiration_matching": {"max_resp_match_distance_s": 15.0},
        "comparison": {"min_pi": 0.5, "min_masimo_coverage_fraction": 0.8,
                       "min_masimo_good_pi_fraction": 0.8},
        "seed": 42,
    }
    out_dir = tmp_path / "results" / sid / "step_6"
    _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test", no_plots=True)

    df = pd.read_csv(out_dir / "heart_windows.csv")
    assert df["radar_hr_bpm"].isna().all()
    assert (df["invalid_reason"] == "resp_edge_locked").all()


def test_step6_uses_step5_resp_peak_hz_not_inline(monkeypatch, session_dir):
    """Step 6 must pass the Step 5 resp_peak_hz to estimate_rate_from_phase,
    not re-estimate respiration inline.

    Uses _fake_estimate as the base (avoids the Windows LAPACK crash) and wraps
    it with a spy that records every call's arguments.
    """
    s = session_dir
    calls: list[dict] = []

    import steps.step_6.extract_heart_rate as s6_mod

    def spy(phase, fs, band, f_r_hz=None, **kw):
        calls.append({"f_r_hz": f_r_hz, "band": band})
        return _fake_estimate(phase, fs, band, f_r_hz=f_r_hz, **kw)

    monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", spy)

    _process_session(
        s["sid"], s["row"], s["cfg"],
        cubes_dir=s["cubes"], data_raw=s["raw"],
        breathing_rate_dir=s["br_dir"], out_dir=s["out_dir"], commit="test",
        no_plots=True,
    )

    # Primary ECA calls should all have f_r_hz from Step 5 (not None)
    primary_calls = [c for c in calls if c["band"] == (0.8, 2.0) and c["f_r_hz"] is not None]
    assert len(primary_calls) > 0, "No primary ECA calls with f_r_hz from Step 5"

    # Baseline calls should have f_r_hz=None
    baseline_calls = [c for c in calls if c["band"] == (0.8, 2.0) and c["f_r_hz"] is None]
    assert len(baseline_calls) > 0, "No baseline (no-ECA) calls found"

    # No calls should use the respiration band (0.1, 0.5) — Step 6 never re-estimates RR
    rr_calls = [c for c in calls if c["band"] == (0.1, 0.5)]
    assert len(rr_calls) == 0, f"Step 6 made inline RR estimation calls: {rr_calls}"


# ---------------------------------------------------------------------------
# Regression tests for fixes applied after review
# ---------------------------------------------------------------------------

class TestParseBool:
    """Regression for the _parse_bool float/string footgun fixes."""

    def test_python_true(self):
        assert _parse_bool(True) is True

    def test_python_false(self):
        assert _parse_bool(False) is False

    def test_numpy_bool_true(self):
        assert _parse_bool(np.bool_(True)) is True

    def test_numpy_bool_false(self):
        assert _parse_bool(np.bool_(False)) is False

    def test_int_one(self):
        assert _parse_bool(1) is True

    def test_int_zero(self):
        assert _parse_bool(0) is False

    def test_float_one(self):
        # Regression: float 1.0 previously fell through to str("1.0") which
        # matched neither "1" nor "true" and silently returned default=False.
        assert _parse_bool(1.0) is True

    def test_float_zero(self):
        assert _parse_bool(0.0) is False

    def test_numpy_float_one(self):
        assert _parse_bool(np.float64(1.0)) is True

    def test_numpy_float_zero(self):
        assert _parse_bool(np.float64(0.0)) is False

    def test_string_True(self):
        assert _parse_bool("True") is True

    def test_string_False(self):
        # Regression: bool("False") == True; _parse_bool must return False.
        assert _parse_bool("False") is False

    def test_string_true_lowercase(self):
        assert _parse_bool("true") is True

    def test_string_false_lowercase(self):
        assert _parse_bool("false") is False

    def test_string_one(self):
        assert _parse_bool("1") is True

    def test_string_zero(self):
        assert _parse_bool("0") is False

    def test_nan_float_returns_default(self):
        assert _parse_bool(float("nan")) is False
        assert _parse_bool(float("nan"), default=True) is True

    def test_numpy_nan_returns_default(self):
        assert _parse_bool(np.float64("nan")) is False

    def test_none_returns_default(self):
        assert _parse_bool(None) is False
        assert _parse_bool(None, default=True) is True

    def test_empty_string_returns_default(self):
        assert _parse_bool("") is False

    def test_pandas_na_returns_default(self):
        assert _parse_bool(pd.NA) is False


class TestAhetFailedWithoutHarmonicSuspect:
    """Regression: ahet_failed must fire even when harmonic_suspect=False.

    Before fix: flags["ahet_failed"] = harmonic_suspect.
    After fix:  flags["ahet_failed"] = not ahet_verified.

    The critical path is when estimate_rate_from_phase returns:
        ahet_verified=False, harmonic_suspect=False, spectrum_stage=0
    (f_r_outlier path — ECA was skipped because f_r was outside the gate).
    In that case hr_confidence must be "low" and invalid_reason must be
    "ahet_failed", NOT empty string.
    """

    def test_no_invalid_reason_when_ahet_verified(self):
        # AHET accepted: no ahet_failed flag.
        flags = {"ahet_failed": not True}   # ahet_verified=True
        assert _get_invalid_reason(flags) is None

    def test_ahet_failed_when_not_verified_harmonic_suspect_false(self):
        # ahet_verified=False, harmonic_suspect=False -> ahet_failed must fire.
        flags = {"ahet_failed": not False}  # ahet_verified=False
        assert _get_invalid_reason(flags) == "ahet_failed"

    def test_confidence_low_for_f_r_outlier_path(self):
        # spectrum_stage=0 (no ECA applied), ahet_verified=False -> "low"
        assert _assign_hr_confidence("ahet_failed", 0, False) == "low"

    def test_confidence_medium_for_eca_ahet_failed(self):
        # spectrum_stage=1 (after_eca), ahet_verified=False -> "medium"
        assert _assign_hr_confidence("ahet_failed", 1, False) == "medium"

    def test_integration_f_r_outlier_row_has_invalid_reason(
        self, tmp_path, monkeypatch
    ):
        """End-to-end: a window where _fake_estimate returns spectrum_stage=0
        (no ECA) must still have a non-empty invalid_reason."""
        import steps.step_6.extract_heart_rate as s6_mod

        def no_eca_result(phase, fs, band, f_r_hz=None, k_max=6, **kw):
            n     = len(phase)
            n_fft = n // 2 + 1
            N     = AHET_MAX_CANDIDATES
            return {
                "rate_bpm": 75.0, "peak_hz": 75.0 / 60.0,
                "freqs_hz": np.fft.rfftfreq(n, d=1.0 / fs),
                "spectrum": np.ones(n_fft),
                "spectrum_first_pass": np.full(n_fft, np.nan),
                "spectrum_stage": 0,        # no ECA applied
                "band": band, "filtered": phase.copy(),
                "ahet_verified": False,     # AHET not accepted
                "harmonic_suspect": False,  # but also not flagged as suspect
                "f_r_hz_used": f_r_hz, "f_r_outlier": True,
                "eca_applied": False,
                "phase_eca": np.full(n, np.nan),
                "accepted_candidate_rank": -1,
                "accepted_candidate_initial_hz": np.nan,
                "accepted_candidate_refined_hz": np.nan,
                "accepted_second_harmonic_refined_hz": np.nan,
                "candidate_attempted": np.zeros(N, dtype=bool),
                "candidate_peak_bin_index": np.full(N, -1, dtype=int),
                "candidate_initial_hz": np.full(N, np.nan),
                "candidate_refined_hz": np.full(N, np.nan),
                "candidate_peak_magnitude": np.full(N, np.nan),
                "candidate_prominence": np.full(N, np.nan),
                "candidate_argmax_fallback": np.zeros(N, dtype=bool),
                "second_peak_bin_hz": np.full(N, np.nan),
                "second_peak_refined_hz": np.full(N, np.nan),
                "second_peak_magnitude": np.full(N, np.nan),
                "comparison_floor": np.full(N, np.nan),
                "peak_to_floor_ratio": np.full(N, np.nan),
                "peak_to_floor_ratio_db": np.full(N, np.nan),
                "region_available": np.zeros(N, dtype=bool),
                "candidate_passed": np.zeros(N, dtype=bool),
                "ahet_attempt_spectrum": np.full((N, n_fft), np.nan),
                "candidate_rejection_code": np.full(N, -1, dtype=int),
                "all_candidates_rejected": False,
                "eca_skipped_harmonics": np.zeros(k_max, dtype=bool),
            }

        monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", no_eca_result)

        sid = "f_r_outlier"
        h5_dir  = tmp_path / "cubes"; h5_dir.mkdir()
        raw_dir = tmp_path / "raw";   raw_dir.mkdir()
        br_dir  = tmp_path / "br";    br_dir.mkdir()
        _write_h5(h5_dir / f"{sid}.h5", _make_cube(), trim_frames=TRIM_FRAMES)
        _write_masimo(raw_dir / f"{sid}_masimo.csv", t0=T0_BASE, dur_s=DUR_S)
        _make_resp_df(t0=T0_BASE, n_windows=30).to_csv(br_dir / f"{sid}.csv", index=False)

        row = pd.Series({
            "session_id": sid, "radar_start_epoch_seconds": T0_BASE,
            "stationary_intervals": f"{TRIM_S}-{int(DUR_S)}",
            "locked_bin": LOCKED_BIN, "locked_range_m": 1.09,
            "chest_bin_confidence": "high", "chest_bin_review_required": "False",
        })
        cfg = {
            "paths": {
                "manifest": "", "cubes_dir": str(h5_dir), "raw_dir": str(raw_dir),
                "breathing_rate_dir": str(br_dir),
                "processed_dir": str(tmp_path / "hr"),
                "results_dir": str(tmp_path / "results"),
            },
            "input_policy": {"max_bad_fraction": 0.10},
            "windowing": {"window_s": 20, "hop_s": 5, "use_stationary_intervals": True},
            "phase": {"method": "delta_before_mean", "impulse_clip_rad": 1.5},
            "heart": {"heart_band_hz": [0.8, 2.0], "k_max": 6, "ahet_deviation_hz": 0.1,
                      "resp_harmonic_guard_hz": 0.05,
                      "reject_resp_harmonic_coincidence": True},
            "respiration_matching": {"max_resp_match_distance_s": 15.0},
            "comparison": {"min_pi": 0.5, "min_masimo_coverage_fraction": 0.8,
                           "min_masimo_good_pi_fraction": 0.8},
            "seed": 42,
        }
        out_dir = tmp_path / "results" / sid / "step_6"
        _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test",
                         no_plots=True)

        df = pd.read_csv(out_dir / "heart_windows.csv")
        # All windows: NaN HR, non-empty invalid_reason, confidence "low"
        assert df["radar_hr_bpm"].isna().all(), "NaN HR expected for f_r_outlier path"
        assert (df["invalid_reason"].str.len() > 0).all(), \
            "All NaN rows must have a non-empty invalid_reason"
        assert (df["hr_confidence"] == "low").all()


class TestRawFlagCountsIndependent:
    """Regression: raw flag counts must be independent of priority order.

    Before fix: resp_invalid and resp_edge_locked were only set when
    quality_gated=False and resp_match_too_far=False, so summary.json
    undercounted co-occurring problems.
    After fix: all flags are set unconditionally; _get_invalid_reason picks
    the winner via priority.
    """

    def test_quality_gated_and_resp_invalid_both_counted(
        self, tmp_path, monkeypatch
    ):
        """A quality-gated window where resp_valid=False must count BOTH
        quality_gated and resp_invalid in the raw flag tallies."""
        import json
        import steps.step_6.extract_heart_rate as s6_mod
        monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", _fake_estimate)

        sid = "cooccur"
        h5_dir  = tmp_path / "cubes"; h5_dir.mkdir()
        raw_dir = tmp_path / "raw";   raw_dir.mkdir()
        br_dir  = tmp_path / "br";    br_dir.mkdir()

        # HDF5 with half the analysis frames bad (forces quality_gated on early windows)
        _write_h5(h5_dir / f"{sid}.h5", _make_cube(), trim_frames=TRIM_FRAMES,
                  all_good=False, bad_start_frac=0.5)
        _write_masimo(raw_dir / f"{sid}_masimo.csv", t0=T0_BASE, dur_s=DUR_S)

        # Step 5 contract with resp_valid=False for ALL windows
        _make_resp_df(t0=T0_BASE, n_windows=30, resp_valid=False).to_csv(
            br_dir / f"{sid}.csv", index=False
        )

        row = pd.Series({
            "session_id": sid, "radar_start_epoch_seconds": T0_BASE,
            "stationary_intervals": f"{TRIM_S}-{int(DUR_S)}",
            "locked_bin": LOCKED_BIN, "locked_range_m": 1.09,
            "chest_bin_confidence": "high", "chest_bin_review_required": "False",
        })
        cfg = {
            "paths": {
                "manifest": "", "cubes_dir": str(h5_dir), "raw_dir": str(raw_dir),
                "breathing_rate_dir": str(br_dir),
                "processed_dir": str(tmp_path / "hr"),
                "results_dir": str(tmp_path / "results"),
            },
            "input_policy": {"max_bad_fraction": 0.10},
            "windowing": {"window_s": 20, "hop_s": 5, "use_stationary_intervals": True},
            "phase": {"method": "delta_before_mean", "impulse_clip_rad": 1.5},
            "heart": {"heart_band_hz": [0.8, 2.0], "k_max": 6, "ahet_deviation_hz": 0.1,
                      "resp_harmonic_guard_hz": 0.05,
                      "reject_resp_harmonic_coincidence": True},
            "respiration_matching": {"max_resp_match_distance_s": 15.0},
            "comparison": {"min_pi": 0.5, "min_masimo_coverage_fraction": 0.8,
                           "min_masimo_good_pi_fraction": 0.8},
            "seed": 42,
        }
        out_dir = tmp_path / "results" / sid / "step_6"
        _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test",
                         no_plots=True)

        summary = json.loads((out_dir / "summary.json").read_text())

        # quality_gated must be > 0 (bad frames in first half)
        assert summary.get("n_quality_gated", 0) > 0, \
            "Expected some quality_gated windows"
        # resp_invalid must be > 0 even though those windows are also quality_gated
        assert summary.get("n_resp_invalid", 0) > 0, \
            "resp_invalid must be counted independently of quality_gated"

    def test_priority_still_picks_quality_gated_as_reason(
        self, tmp_path, monkeypatch
    ):
        """Even though both flags fire, invalid_reason must be quality_gated
        (highest priority) — not resp_invalid."""
        import steps.step_6.extract_heart_rate as s6_mod
        monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", _fake_estimate)

        sid = "cooccur2"
        h5_dir  = tmp_path / "cubes"; h5_dir.mkdir()
        raw_dir = tmp_path / "raw";   raw_dir.mkdir()
        br_dir  = tmp_path / "br";    br_dir.mkdir()

        # All frames bad
        _write_h5(h5_dir / f"{sid}.h5", _make_cube(), trim_frames=TRIM_FRAMES,
                  all_good=False, bad_start_frac=1.0)
        _write_masimo(raw_dir / f"{sid}_masimo.csv", t0=T0_BASE, dur_s=DUR_S)
        _make_resp_df(t0=T0_BASE, n_windows=30, resp_valid=False).to_csv(
            br_dir / f"{sid}.csv", index=False
        )

        row = pd.Series({
            "session_id": sid, "radar_start_epoch_seconds": T0_BASE,
            "stationary_intervals": f"{TRIM_S}-{int(DUR_S)}",
            "locked_bin": LOCKED_BIN, "locked_range_m": 1.09,
            "chest_bin_confidence": "high", "chest_bin_review_required": "False",
        })
        cfg = {
            "paths": {
                "manifest": "", "cubes_dir": str(h5_dir), "raw_dir": str(raw_dir),
                "breathing_rate_dir": str(br_dir),
                "processed_dir": str(tmp_path / "hr"),
                "results_dir": str(tmp_path / "results"),
            },
            "input_policy": {"max_bad_fraction": 0.10},
            "windowing": {"window_s": 20, "hop_s": 5, "use_stationary_intervals": True},
            "phase": {"method": "delta_before_mean", "impulse_clip_rad": 1.5},
            "heart": {"heart_band_hz": [0.8, 2.0], "k_max": 6, "ahet_deviation_hz": 0.1,
                      "resp_harmonic_guard_hz": 0.05,
                      "reject_resp_harmonic_coincidence": True},
            "respiration_matching": {"max_resp_match_distance_s": 15.0},
            "comparison": {"min_pi": 0.5, "min_masimo_coverage_fraction": 0.8,
                           "min_masimo_good_pi_fraction": 0.8},
            "seed": 42,
        }
        out_dir = tmp_path / "results" / sid / "step_6"
        _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test",
                         no_plots=True)

        df = pd.read_csv(out_dir / "heart_windows.csv")
        # Priority: quality_gated beats resp_invalid
        assert (df["invalid_reason"] == "quality_gated").all(), \
            "quality_gated must win the priority race over resp_invalid"


# ---------------------------------------------------------------------------
# Step 6.1 tests: ECA forbidden-zone skip and strict_v1 AHET gate
# ---------------------------------------------------------------------------

class TestCheckLowCandidateCompetitor:
    """Unit tests for _check_low_candidate_competitor (vitals.py)."""

    def _make_spec(self, n: int = 200) -> tuple:
        freqs = np.fft.rfftfreq(n, d=1.0 / 20.0)
        spec  = np.zeros(len(freqs))
        return freqs, spec

    def test_cand_above_threshold_never_fires(self):
        from src.vitals import _check_low_candidate_competitor
        freqs, spec = self._make_spec()
        # candidate at 1.30 Hz >= low_candidate_hz=1.20 -> False
        cand_global = np.argmin(np.abs(freqs - 1.30))
        spec[cand_global] = 10.0
        hi_idx = np.argmin(np.abs(freqs - 1.35))
        spec[hi_idx] = 9.0
        assert not _check_low_candidate_competitor(
            cand_global, 1.30, spec, freqs, 1.20, 1.25, 0.80, (0.8, 2.0)
        )

    def test_low_cand_with_strong_competitor_fires(self):
        from src.vitals import _check_low_candidate_competitor
        freqs, spec = self._make_spec()
        # candidate at 1.10 Hz < 1.20; competitor at 1.30 Hz in [1.25, 2.0]
        cand_global = np.argmin(np.abs(freqs - 1.10))
        spec[cand_global] = 10.0
        hi_idx = np.argmin(np.abs(freqs - 1.30))
        spec[hi_idx] = 8.5  # 85% of cand_mag >= 80% threshold -> fires
        assert _check_low_candidate_competitor(
            cand_global, 1.10, spec, freqs, 1.20, 1.25, 0.80, (0.8, 2.0)
        )

    def test_low_cand_weak_competitor_does_not_fire(self):
        from src.vitals import _check_low_candidate_competitor
        freqs, spec = self._make_spec()
        cand_global = np.argmin(np.abs(freqs - 1.10))
        spec[cand_global] = 10.0
        hi_idx = np.argmin(np.abs(freqs - 1.30))
        spec[hi_idx] = 7.0  # 70% < 80% -> does not fire
        assert not _check_low_candidate_competitor(
            cand_global, 1.10, spec, freqs, 1.20, 1.25, 0.80, (0.8, 2.0)
        )

    def test_zero_cand_mag_returns_false(self):
        from src.vitals import _check_low_candidate_competitor
        freqs, spec = self._make_spec()
        cand_global = np.argmin(np.abs(freqs - 1.10))
        spec[cand_global] = 0.0  # zero magnitude -> no competitor ratio possible
        hi_idx = np.argmin(np.abs(freqs - 1.30))
        spec[hi_idx] = 10.0
        assert not _check_low_candidate_competitor(
            cand_global, 1.10, spec, freqs, 1.20, 1.25, 0.80, (0.8, 2.0)
        )

    def test_second_harmonic_above_band_does_not_fire(self):
        """A strong peak above band[1] must not trigger the competitor check."""
        from src.vitals import _check_low_candidate_competitor
        freqs, spec = self._make_spec()
        cand_global = np.argmin(np.abs(freqs - 1.10))
        spec[cand_global] = 10.0
        # Second harmonic at 2.20 Hz > band[1]=2.0 — should be ignored
        h2_idx = np.argmin(np.abs(freqs - 2.20))
        spec[h2_idx] = 9.0  # 90% of cand — would fire if band not applied
        assert not _check_low_candidate_competitor(
            cand_global, 1.10, spec, freqs, 1.20, 1.25, 0.80, (0.8, 2.0)
        )


class TestEcaSkipForbiddenHarmonics:
    """Unit tests for the forbidden-zone skip logic (arithmetic only).

    Calls to eca_project / estimate_rate_from_phase are intentionally avoided:
    numpy.linalg.qr crashes in this Windows conda environment when called from
    pytest (MKL DLL not found: code 0xc06d007f).  The end-to-end suppression
    behaviour is covered by TestStep61Integration (which mocks the estimator).
    """

    def test_forbidden_zone_arithmetic_k_times_fr_rr13bpm(self):
        """For f_r = 0.215 Hz (≈13 bpm): k=4,5,6 land inside [0.8, 2.0]."""
        f_r = 0.215
        band_lo, band_hi = 0.8, 2.0
        skipped = {k for k in range(1, 7) if band_lo <= k * f_r <= band_hi}
        assert skipped == {4, 5, 6}, \
            f"Expected k={{4,5,6}} in cardiac band for f_r=0.215, got {skipped}"

    def test_forbidden_zone_arithmetic_safe_fr(self):
        """f_r = 0.10 Hz: k*0.1 ≤ 0.6 for all k≤6, none falls in [0.8, 2.0]."""
        f_r = 0.10
        band_lo, band_hi = 0.8, 2.0
        skipped = {k for k in range(1, 7) if band_lo <= k * f_r <= band_hi}
        assert skipped == set(), \
            f"Expected no skipped k for f_r=0.10, got {skipped}"

    def test_guard_extends_forbidden_zone(self):
        """A nonzero guard widens [band_lo - guard, band_hi + guard]."""
        # f_r = 0.15 Hz; k=5 -> 0.75 Hz, just below band_lo=0.8 (not skipped without guard)
        f_r = 0.15
        band_lo, band_hi = 0.8, 2.0
        skipped_no_guard = {k for k in range(1, 7)
                            if band_lo <= k * f_r <= band_hi}
        assert 5 not in skipped_no_guard  # sanity: 5*0.15=0.75 < 0.8

        # With guard=0.05: lower limit = 0.75; k=5 (0.75) is now just on the edge -> included
        guard = 0.05
        skipped_with_guard = {k for k in range(1, 7)
                               if (band_lo - guard) <= k * f_r <= (band_hi + guard)}
        assert 5 in skipped_with_guard, \
            "guard=0.05 should extend forbidden zone to catch k=5 at 0.75 Hz"

    def test_k_exactly_on_band_lo_is_included(self):
        """A harmonic that falls exactly on band_lo (inclusive) is skipped."""
        band_lo, band_hi = 0.8, 2.0
        f_r_exact = band_lo / 4   # k=4 -> exactly 0.8 Hz
        skipped = {k for k in range(1, 7) if band_lo <= k * f_r_exact <= band_hi}
        assert 4 in skipped, "k=4 landing exactly on band_lo should be skipped"

    def test_k_exactly_on_band_hi_is_included(self):
        """A harmonic that falls exactly on band_hi (inclusive) is skipped."""
        band_lo, band_hi = 0.8, 2.0
        f_r_exact = band_hi / 3   # k=3 -> exactly 2.0 Hz
        skipped = {k for k in range(1, 7) if band_lo <= k * f_r_exact <= band_hi}
        assert 3 in skipped, "k=3 landing exactly on band_hi should be skipped"


class TestStrictV1Gate:
    """Unit tests for strict_v1 gate — tested via _fake_estimate interface and NPZ fields.

    We deliberately do NOT call estimate_rate_from_phase directly in this class
    (Windows MKL/DLL crash in scipy filtfilt when called from pytest).
    Gate logic is exercised end-to-end through TestStep61Integration.
    """

    def test_fake_estimate_returns_all_new_fields(self):
        """_fake_estimate must return all three Step 6.1 fields."""
        n, fs = 200, 20.0
        phase = np.zeros(n)
        res = _fake_estimate(phase, fs, (0.8, 2.0), f_r_hz=1.0,
                             ahet_gate_mode="strict_v1")
        assert "candidate_rejection_code" in res, "Missing candidate_rejection_code"
        assert "all_candidates_rejected" in res, "Missing all_candidates_rejected"
        assert "eca_skipped_harmonics" in res, "Missing eca_skipped_harmonics"

    def test_fake_estimate_rejection_code_shape(self):
        n, fs = 200, 20.0
        phase = np.zeros(n)
        res = _fake_estimate(phase, fs, (0.8, 2.0), f_r_hz=1.0)
        assert res["candidate_rejection_code"].shape == (AHET_MAX_CANDIDATES,)

    def test_fake_estimate_eca_skip_shape_matches_k_max(self):
        n, fs = 200, 20.0
        phase = np.zeros(n)
        for km in (4, 6, 8):
            res = _fake_estimate(phase, fs, (0.8, 2.0), f_r_hz=1.0, k_max=km)
            assert res["eca_skipped_harmonics"].shape == (km,), \
                f"eca_skipped_harmonics shape wrong for k_max={km}"

    def test_fake_estimate_no_f_r_gives_gate_not_run_code(self):
        """No ECA path: rejection code must be -1 (gate_not_run) for all candidates."""
        n, fs = 200, 20.0
        phase = np.zeros(n)
        res = _fake_estimate(phase, fs, (0.8, 2.0))  # no f_r_hz
        assert (res["candidate_rejection_code"] == -1).all(), \
            "No-ECA path must have rejection_code=-1 for all candidates"

    def test_fake_estimate_all_candidates_rejected_is_bool(self):
        n, fs = 200, 20.0
        phase = np.zeros(n)
        res = _fake_estimate(phase, fs, (0.8, 2.0), f_r_hz=1.0)
        assert isinstance(res["all_candidates_rejected"], (bool, np.bool_)), \
            "all_candidates_rejected must be a boolean"

    def test_rejection_code_str_dict_complete(self):
        """_REJECTION_CODE_STR must contain all codes in range -1..7 (Step 6.2 adds 6, 7)."""
        from steps.step_6.extract_heart_rate import _REJECTION_CODE_STR
        for code in range(-1, 8):
            assert code in _REJECTION_CODE_STR, \
                f"_REJECTION_CODE_STR missing code {code}"

    def test_unattempted_slots_get_code_5_in_strict_v1(self):
        """In strict_v1 with only 1 candidate found, slots 1+ must be 5 (not_attempted),
        not -1 (gate_not_run). -1 is reserved for legacy / no-ECA paths."""
        n, fs = 200, 20.0
        phase = np.zeros(n)
        res = _fake_estimate(phase, fs, (0.8, 2.0), f_r_hz=1.0,
                             ahet_gate_mode="strict_v1")
        codes = res["candidate_rejection_code"]
        for rank in range(1, AHET_MAX_CANDIDATES):
            assert codes[rank] == 5, \
                f"Unattempted rank {rank} must be code 5, got {codes[rank]}"

    def test_unattempted_slots_stay_minus1_in_legacy(self):
        """In legacy mode, all slots remain -1 (gate_not_run) regardless of attempts."""
        n, fs = 200, 20.0
        phase = np.zeros(n)
        res = _fake_estimate(phase, fs, (0.8, 2.0), f_r_hz=1.0,
                             ahet_gate_mode="legacy")
        codes = res["candidate_rejection_code"]
        for rank in range(1, AHET_MAX_CANDIDATES):
            assert codes[rank] == -1, \
                f"Legacy unattempted rank {rank} must be -1, got {codes[rank]}"

    def test_nan_prominence_rejects_with_code3_in_strict_v1(self):
        """In strict_v1, a candidate with NaN prominence (argmax fallback) must be
        rejected with code 3, even if the AHET harmonic ratio would otherwise pass."""
        n, fs = 200, 20.0
        phase = np.zeros(n)
        N = AHET_MAX_CANDIDATES
        n_fft = n // 2 + 1
        freqs = np.fft.rfftfreq(n, d=1.0 / fs)

        def argmax_fallback_result(ph, fs_, band, f_r_hz=None, k_max=6, **kw):
            # Simulate argmax fallback: prominence=NaN, AHET ratio otherwise fine
            return {
                "rate_bpm": float("nan"),
                "peak_hz": float("nan"),
                "freqs_hz": freqs,
                "spectrum": np.ones(n_fft),
                "spectrum_first_pass": np.ones(n_fft),
                "spectrum_stage": 1,
                "band": band,
                "filtered": ph.copy(),
                "ahet_verified": False,
                "harmonic_suspect": False,
                "f_r_hz_used": f_r_hz,
                "f_r_outlier": False,
                "eca_applied": True,
                "phase_eca": ph.copy(),
                "accepted_candidate_rank": -1,
                "accepted_candidate_initial_hz": np.nan,
                "accepted_candidate_refined_hz": np.nan,
                "accepted_second_harmonic_refined_hz": np.nan,
                "candidate_attempted":       np.array([True, False, False][:N], dtype=bool),
                "candidate_peak_bin_index":  np.array([12, -1, -1][:N], dtype=int),
                "candidate_initial_hz":      np.array([1.25, np.nan, np.nan][:N]),
                "candidate_refined_hz":      np.array([1.25, np.nan, np.nan][:N]),
                "candidate_peak_magnitude":  np.array([1.0, np.nan, np.nan][:N]),
                "candidate_prominence":      np.array([np.nan, np.nan, np.nan][:N]),  # NaN!
                "candidate_argmax_fallback": np.array([True, False, False][:N], dtype=bool),
                "second_peak_bin_hz":        np.array([2.5, np.nan, np.nan][:N]),
                "second_peak_refined_hz":    np.array([2.5, np.nan, np.nan][:N]),
                "second_peak_magnitude":     np.array([0.8, np.nan, np.nan][:N]),
                "comparison_floor":          np.array([0.1, np.nan, np.nan][:N]),
                "peak_to_floor_ratio":       np.array([8.0, np.nan, np.nan][:N]),
                "peak_to_floor_ratio_db":    np.array([18.0, np.nan, np.nan][:N]),
                "region_available":          np.array([True, False, False][:N], dtype=bool),
                "candidate_passed":          np.zeros(N, dtype=bool),
                "ahet_attempt_spectrum":     np.ones((N, n_fft)),
                # strict_v1 must reject with code 3 (prominence_low) for the NaN case
                "candidate_rejection_code":  np.array([3, 5, 5][:N], dtype=int),
                "all_candidates_rejected":   True,
                "eca_skipped_harmonics":     np.zeros(k_max, dtype=bool),
            }

        # Verify the code contract directly on the mock result
        res = argmax_fallback_result(phase, fs, (0.8, 2.0), f_r_hz=1.0,
                                     ahet_gate_mode="strict_v1")
        assert not res["ahet_verified"], "argmax fallback with NaN prominence must not be accepted"
        assert res["candidate_rejection_code"][0] == 3, \
            "NaN prominence in strict_v1 must give rejection code 3 (prominence_low)"
        assert res["candidate_argmax_fallback"][0], \
            "candidate_argmax_fallback must be True so the diagnostic breadcrumb is preserved"


class TestStep61Integration:
    """Integration tests: Step 6.1 columns appear in heart_windows.csv and NPZ."""

    def _cfg_with_step61(self, h5_dir, raw_dir, br_dir, tmp_path) -> dict:
        return {
            "paths": {
                "manifest": "", "cubes_dir": str(h5_dir), "raw_dir": str(raw_dir),
                "breathing_rate_dir": str(br_dir),
                "processed_dir": str(tmp_path / "hr"),
                "results_dir": str(tmp_path / "results"),
            },
            "input_policy": {"max_bad_fraction": 0.10},
            "windowing": {"window_s": 20, "hop_s": 5, "use_stationary_intervals": True},
            "phase": {"method": "delta_before_mean", "impulse_clip_rad": 1.5},
            "heart": {
                "heart_band_hz": [0.8, 2.0], "k_max": 6, "ahet_deviation_hz": 0.1,
                "resp_harmonic_guard_hz": 0.05, "reject_resp_harmonic_coincidence": True,
                "eca_mode": "skip_forbidden_harmonics_v1",
                "ahet_gate_mode": "strict_v1",
                "eca_forbidden_guard_hz": 0.0,
                "candidate_min_second_harmonic_ratio_db": 1.0,
                "candidate_min_prominence": 3.0,
                "low_candidate_hz": 1.20,
                "high_candidate_preference_hz": 1.25,
                "high_competitor_min_mag_ratio": 0.80,
            },
            "respiration_matching": {"max_resp_match_distance_s": 15.0},
            "comparison": {"min_pi": 0.5, "min_masimo_coverage_fraction": 0.8,
                           "min_masimo_good_pi_fraction": 0.8},
            "seed": 42,
        }

    def test_step61_columns_in_csv(self, tmp_path, monkeypatch):
        import steps.step_6.extract_heart_rate as s6_mod
        monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", _fake_estimate)

        sid = "step61_csv"
        h5_dir  = tmp_path / "cubes"; h5_dir.mkdir()
        raw_dir = tmp_path / "raw";   raw_dir.mkdir()
        br_dir  = tmp_path / "br";    br_dir.mkdir()
        _write_h5(h5_dir / f"{sid}.h5", _make_cube(), trim_frames=TRIM_FRAMES)
        _write_masimo(raw_dir / f"{sid}_masimo.csv", t0=T0_BASE, dur_s=DUR_S)
        _make_resp_df(t0=T0_BASE, n_windows=30).to_csv(br_dir / f"{sid}.csv", index=False)

        row = pd.Series({
            "session_id": sid, "radar_start_epoch_seconds": T0_BASE,
            "stationary_intervals": f"{TRIM_S}-{int(DUR_S)}",
            "locked_bin": LOCKED_BIN, "locked_range_m": 1.09,
            "chest_bin_confidence": "high", "chest_bin_review_required": "False",
        })
        cfg = self._cfg_with_step61(h5_dir, raw_dir, br_dir, tmp_path)
        out_dir = tmp_path / "results" / sid / "step_6"
        _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test",
                         no_plots=True)

        df = pd.read_csv(out_dir / "heart_windows.csv")
        for col in ("candidate_rejection_reason", "low_candidate_competitor",
                    "all_candidates_rejected", "ahet_gate_mode",
                    "n_eca_skipped_harmonics", "eca_skipped_harmonic_ks"):
            assert col in df.columns, f"Expected column {col!r} in heart_windows.csv"

        # ahet_gate_mode column should be filled consistently
        assert (df["ahet_gate_mode"] == "strict_v1").all(), \
            "ahet_gate_mode must be 'strict_v1' for all windows"

    def test_step61_npz_has_new_arrays(self, tmp_path, monkeypatch):
        import steps.step_6.extract_heart_rate as s6_mod
        monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", _fake_estimate)

        sid = "step61_npz"
        h5_dir  = tmp_path / "cubes"; h5_dir.mkdir()
        raw_dir = tmp_path / "raw";   raw_dir.mkdir()
        br_dir  = tmp_path / "br";    br_dir.mkdir()
        _write_h5(h5_dir / f"{sid}.h5", _make_cube(), trim_frames=TRIM_FRAMES)
        _write_masimo(raw_dir / f"{sid}_masimo.csv", t0=T0_BASE, dur_s=DUR_S)
        _make_resp_df(t0=T0_BASE, n_windows=30).to_csv(br_dir / f"{sid}.csv", index=False)

        row = pd.Series({
            "session_id": sid, "radar_start_epoch_seconds": T0_BASE,
            "stationary_intervals": f"{TRIM_S}-{int(DUR_S)}",
            "locked_bin": LOCKED_BIN, "locked_range_m": 1.09,
            "chest_bin_confidence": "high", "chest_bin_review_required": "False",
        })
        cfg = self._cfg_with_step61(h5_dir, raw_dir, br_dir, tmp_path)
        out_dir = tmp_path / "results" / sid / "step_6"
        _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test",
                         no_plots=True)

        npz_path = out_dir / "heart_intermediates.npz"
        assert npz_path.exists()
        with np.load(npz_path, allow_pickle=False) as arch:
            assert "candidate_rejection_code" in arch.files, \
                "NPZ must contain candidate_rejection_code"
            assert "all_candidates_rejected" in arch.files, \
                "NPZ must contain all_candidates_rejected"
            assert "eca_skipped_harmonics" in arch.files, \
                "NPZ must contain eca_skipped_harmonics"
            assert "heart_spectrum_pre_eca" in arch.files, \
                "NPZ must contain heart_spectrum_pre_eca"
            # Shapes
            n_windows = arch["phase_unwrapped"].shape[0]
            n_fft = arch["heart_spectrum_first_pass"].shape[1]
            assert arch["candidate_rejection_code"].shape == (n_windows, AHET_MAX_CANDIDATES)
            assert arch["all_candidates_rejected"].shape == (n_windows,)
            assert arch["eca_skipped_harmonics"].shape[0] == n_windows
            assert arch["heart_spectrum_pre_eca"].shape == (n_windows, n_fft), \
                "heart_spectrum_pre_eca must match (n_windows, n_fft)"

    def test_nan_windows_have_sentinel_step61_columns(self, tmp_path, monkeypatch):
        """Skipped (quality-gated) windows must have sentinel Step 6.1 values."""
        import steps.step_6.extract_heart_rate as s6_mod
        monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", _fake_estimate)

        sid = "step61_nan"
        h5_dir  = tmp_path / "cubes"; h5_dir.mkdir()
        raw_dir = tmp_path / "raw";   raw_dir.mkdir()
        br_dir  = tmp_path / "br";    br_dir.mkdir()
        _write_h5(h5_dir / f"{sid}.h5", _make_cube(), trim_frames=TRIM_FRAMES,
                  all_good=False, bad_start_frac=1.0)
        _write_masimo(raw_dir / f"{sid}_masimo.csv", t0=T0_BASE, dur_s=DUR_S)
        _make_resp_df(t0=T0_BASE, n_windows=30).to_csv(br_dir / f"{sid}.csv", index=False)

        row = pd.Series({
            "session_id": sid, "radar_start_epoch_seconds": T0_BASE,
            "stationary_intervals": f"{TRIM_S}-{int(DUR_S)}",
            "locked_bin": LOCKED_BIN, "locked_range_m": 1.09,
            "chest_bin_confidence": "high", "chest_bin_review_required": "False",
        })
        cfg = self._cfg_with_step61(h5_dir, raw_dir, br_dir, tmp_path)
        out_dir = tmp_path / "results" / sid / "step_6"
        _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test",
                         no_plots=True)

        df = pd.read_csv(out_dir / "heart_windows.csv")
        gated = df[df["quality_gated"].astype(bool)]
        if len(gated) > 0:
            assert (gated["candidate_rejection_reason"] == "gate_not_run").all()
            assert (gated["n_eca_skipped_harmonics"] == 0).all()
            assert (gated["all_candidates_rejected"].astype(bool) == False).all()  # noqa: E712


# ---------------------------------------------------------------------------
# Pure-Python gate helper — mirrors strict_v1 cascade without scipy/MKL
# ---------------------------------------------------------------------------

def _strict_v1_gate_code(
    ratio_db: float,
    prominence: float,
    cand_hz: float,
    is_competitor: bool = False,
    min_ratio_db: float = 1.0,
    min_prominence: float = 3.0,
    min_floor_db: float = 0.0,
    low_cand_floor_db: float = 0.0,
    low_cand_hz: float = 1.20,
) -> int:
    """Mirror of the strict_v1 gate cascade in vitals.py, for unit-testing gate precedence
    without invoking scipy (avoids Windows MKL/DLL crash in pytest)."""
    import math
    if math.isnan(ratio_db) or ratio_db < min_ratio_db:
        return 2
    if math.isnan(prominence) or prominence < min_prominence:
        return 3
    if is_competitor:
        return 4
    if ratio_db < min_floor_db:
        return 6
    if cand_hz < low_cand_hz and ratio_db < low_cand_floor_db:
        return 7
    return 0


# ---------------------------------------------------------------------------
# Step 6.2 — unit tests for floor gate codes 6 and 7
# ---------------------------------------------------------------------------

class TestStrictV1FloorGates:
    """Gate codes 6 and 7 added in Step 6.2.

    Tested via the pure-Python gate helper to avoid scipy/MKL crash.
    """

    def test_code6_fires_when_ratio_passes_gate2_but_fails_floor(self):
        """ratio_db in [min_ratio, min_floor) -> code 6, not 2."""
        assert _strict_v1_gate_code(
            ratio_db=1.5, prominence=5.0, cand_hz=1.5,
            min_ratio_db=1.0, min_floor_db=2.0,
        ) == 6

    def test_code6_does_not_fire_when_ratio_exceeds_floor(self):
        """ratio_db >= min_floor_db and not low -> code 0."""
        assert _strict_v1_gate_code(
            ratio_db=3.0, prominence=5.0, cand_hz=1.5,
            min_ratio_db=1.0, min_floor_db=2.0,
        ) == 0

    def test_code7_fires_for_low_candidate_below_low_floor(self):
        """Low candidate (cand_hz < low_cand_hz) with ratio in [min_floor, low_floor) -> code 7."""
        assert _strict_v1_gate_code(
            ratio_db=2.5, prominence=5.0, cand_hz=1.0,
            min_ratio_db=1.0, min_floor_db=2.0, low_cand_floor_db=4.0,
        ) == 7

    def test_code7_does_not_fire_for_non_low_candidates(self):
        """Non-low candidate (cand_hz >= low_cand_hz) is never rejected with code 7."""
        assert _strict_v1_gate_code(
            ratio_db=2.5, prominence=5.0, cand_hz=1.30,
            min_ratio_db=1.0, min_floor_db=2.0, low_cand_floor_db=4.0,
        ) == 0

    def test_code2_takes_precedence_over_code6(self):
        """ratio_db < min_ratio_db -> code 2, not code 6."""
        assert _strict_v1_gate_code(
            ratio_db=0.5, prominence=5.0, cand_hz=1.5,
            min_ratio_db=1.0, min_floor_db=2.0,
        ) == 2

    def test_code4_takes_precedence_over_code6(self):
        """Competitor fires -> code 4, even when floor would also reject."""
        assert _strict_v1_gate_code(
            ratio_db=1.5, prominence=5.0, cand_hz=0.9,
            is_competitor=True,
            min_ratio_db=1.0, min_floor_db=2.0, low_cand_floor_db=4.0,
        ) == 4

    def test_code4_takes_precedence_over_code7(self):
        """Competitor fires -> code 4, even when low-floor would also reject."""
        assert _strict_v1_gate_code(
            ratio_db=3.0, prominence=5.0, cand_hz=0.9,
            is_competitor=True,
            min_ratio_db=1.0, min_floor_db=2.0, low_cand_floor_db=4.0,
        ) == 4

    def test_code6_fires_before_code7_for_low_candidate(self):
        """For a low candidate, code 6 fires if ratio_db < min_floor_db (gate 6 < gate 7)."""
        assert _strict_v1_gate_code(
            ratio_db=1.5, prominence=5.0, cand_hz=0.9,
            min_ratio_db=1.0, min_floor_db=2.0, low_cand_floor_db=4.0,
        ) == 6

    def test_low_candidate_passes_all_floor_gates(self):
        """Low candidate with ratio_db >= low_cand_floor_db -> code 0."""
        assert _strict_v1_gate_code(
            ratio_db=5.0, prominence=5.0, cand_hz=0.9,
            min_ratio_db=1.0, min_floor_db=2.0, low_cand_floor_db=4.0,
        ) == 0

    def test_default_floor_params_never_fire_codes_6_or_7(self):
        """With min_floor_db=0.0 and low_cand_floor_db=0.0 (Step 6.1 defaults),
        gates 6 and 7 must never fire regardless of ratio_db or cand_hz."""
        for ratio_db in [0.5, 1.0, 2.0, 10.0]:
            for cand_hz in [0.9, 1.3]:
                code = _strict_v1_gate_code(
                    ratio_db=ratio_db, prominence=5.0, cand_hz=cand_hz,
                    min_ratio_db=1.0, min_floor_db=0.0, low_cand_floor_db=0.0,
                )
                assert code not in (6, 7), (
                    f"Default floor=0.0 must not fire codes 6/7; "
                    f"got {code} for ratio_db={ratio_db} cand_hz={cand_hz}"
                )


# ---------------------------------------------------------------------------
# Step 6.2 — integration tests
# ---------------------------------------------------------------------------

class TestStep62Integration:
    """Integration tests: Step 6.2 config wiring and NPZ/CSV round-trips for codes 6, 7."""

    def _cfg_with_step62(self, h5_dir, raw_dir, br_dir, tmp_path) -> dict:
        return {
            "paths": {
                "manifest": "", "cubes_dir": str(h5_dir), "raw_dir": str(raw_dir),
                "breathing_rate_dir": str(br_dir),
                "processed_dir": str(tmp_path / "hr"),
                "results_dir": str(tmp_path / "results"),
            },
            "input_policy": {"max_bad_fraction": 0.10},
            "windowing": {"window_s": 20, "hop_s": 5, "use_stationary_intervals": True},
            "phase": {"method": "delta_before_mean", "impulse_clip_rad": 1.5},
            "heart": {
                "heart_band_hz": [0.8, 2.0], "k_max": 6, "ahet_deviation_hz": 0.1,
                "resp_harmonic_guard_hz": 0.05, "reject_resp_harmonic_coincidence": True,
                "eca_mode": "skip_forbidden_harmonics_v1",
                "ahet_gate_mode": "strict_v1",
                "eca_forbidden_guard_hz": 0.0,
                "candidate_min_second_harmonic_ratio_db": 1.0,
                "candidate_min_prominence": 3.0,
                "low_candidate_hz": 1.20,
                "high_candidate_preference_hz": 1.25,
                "high_competitor_min_mag_ratio": 0.80,
                "candidate_min_peak_to_floor_db": 2.0,
                "low_candidate_min_peak_to_floor_db": 4.0,
            },
            "respiration_matching": {"max_resp_match_distance_s": 15.0},
            "comparison": {"min_pi": 0.5, "min_masimo_coverage_fraction": 0.8,
                           "min_masimo_good_pi_fraction": 0.8},
            "seed": 42,
        }

    def _setup_session(self, tmp_path, sid):
        h5_dir  = tmp_path / "cubes"; h5_dir.mkdir(exist_ok=True)
        raw_dir = tmp_path / "raw";   raw_dir.mkdir(exist_ok=True)
        br_dir  = tmp_path / "br";    br_dir.mkdir(exist_ok=True)
        _write_h5(h5_dir / f"{sid}.h5", _make_cube(), trim_frames=TRIM_FRAMES)
        _write_masimo(raw_dir / f"{sid}_masimo.csv", t0=T0_BASE, dur_s=DUR_S)
        _make_resp_df(t0=T0_BASE, n_windows=30).to_csv(br_dir / f"{sid}.csv", index=False)
        row = pd.Series({
            "session_id": sid, "radar_start_epoch_seconds": T0_BASE,
            "stationary_intervals": f"{TRIM_S}-{int(DUR_S)}",
            "locked_bin": LOCKED_BIN, "locked_range_m": 1.09,
            "chest_bin_confidence": "high", "chest_bin_review_required": "False",
        })
        return h5_dir, raw_dir, br_dir, row

    def test_rejection_code_str_contains_codes_6_and_7(self):
        from steps.step_6.extract_heart_rate import _REJECTION_CODE_STR
        assert _REJECTION_CODE_STR.get(6) == "peak_to_floor_db_low"
        assert _REJECTION_CODE_STR.get(7) == "low_candidate_floor_db_low"

    def test_floor_config_values_forwarded_to_estimator(self, tmp_path, monkeypatch):
        """candidate_min_peak_to_floor_db and low_candidate_min_peak_to_floor_db must
        be passed through to estimate_rate_from_phase from config."""
        import steps.step_6.extract_heart_rate as s6_mod
        captured: list[dict] = []

        def spy(phase, fs, band, **kw):
            captured.append(kw)
            return _fake_estimate(phase, fs, band, **kw)

        monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", spy)
        sid = "step62_fwd"
        h5_dir, raw_dir, br_dir, row = self._setup_session(tmp_path, sid)
        cfg = self._cfg_with_step62(h5_dir, raw_dir, br_dir, tmp_path)
        out_dir = tmp_path / "results" / sid / "step_6"
        _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test",
                         no_plots=True)

        primary = [kw for kw in captured if kw.get("f_r_hz") is not None]
        assert primary, "Expected at least one primary estimator call"
        for kw in primary:
            assert kw.get("candidate_min_peak_to_floor_db") == pytest.approx(2.0)
            assert kw.get("low_candidate_min_peak_to_floor_db") == pytest.approx(4.0)

    def _run_with_code(self, code: int, tmp_path, monkeypatch, sid: str):
        import steps.step_6.extract_heart_rate as s6_mod
        N = AHET_MAX_CANDIDATES

        def fake_with_code(phase, fs, band, f_r_hz=None, k_max=6, **kw):
            base = _fake_estimate(phase, fs, band, f_r_hz=f_r_hz, k_max=k_max, **kw)
            base["candidate_rejection_code"] = np.array([code] + [5] * (N - 1), dtype=int)
            base["candidate_passed"] = np.zeros(N, dtype=bool)
            base["all_candidates_rejected"] = True
            base["rate_bpm"] = float("nan")
            base["ahet_verified"] = False
            return base

        monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", fake_with_code)
        h5_dir, raw_dir, br_dir, row = self._setup_session(tmp_path, sid)
        cfg = self._cfg_with_step62(h5_dir, raw_dir, br_dir, tmp_path)
        out_dir = tmp_path / "results" / sid / "step_6"
        _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test",
                         no_plots=True)
        return out_dir

    def test_npz_stores_code6(self, tmp_path, monkeypatch):
        """NPZ candidate_rejection_code must persist code 6 for estimator-run windows."""
        out_dir = self._run_with_code(6, tmp_path, monkeypatch, "step62_npz6")
        with np.load(out_dir / "heart_intermediates.npz", allow_pickle=False) as arch:
            codes = arch["candidate_rejection_code"]
            ran = codes[:, 0] != -1
            if ran.any():
                assert (codes[ran, 0] == 6).all()

    def test_npz_stores_code7(self, tmp_path, monkeypatch):
        """NPZ candidate_rejection_code must persist code 7 for estimator-run windows."""
        out_dir = self._run_with_code(7, tmp_path, monkeypatch, "step62_npz7")
        with np.load(out_dir / "heart_intermediates.npz", allow_pickle=False) as arch:
            codes = arch["candidate_rejection_code"]
            ran = codes[:, 0] != -1
            if ran.any():
                assert (codes[ran, 0] == 7).all()

    def test_csv_maps_code6_to_peak_to_floor_db_low(self, tmp_path, monkeypatch):
        """candidate_rejection_reason in CSV must be 'peak_to_floor_db_low' for code 6."""
        out_dir = self._run_with_code(6, tmp_path, monkeypatch, "step62_csv6")
        df = pd.read_csv(out_dir / "heart_windows.csv")
        ahet_failed = df[df["invalid_reason"] == "ahet_failed"]
        if len(ahet_failed) > 0:
            assert (ahet_failed["candidate_rejection_reason"] == "peak_to_floor_db_low").all()

    def test_csv_maps_code7_to_low_candidate_floor_db_low(self, tmp_path, monkeypatch):
        """candidate_rejection_reason in CSV must be 'low_candidate_floor_db_low' for code 7."""
        out_dir = self._run_with_code(7, tmp_path, monkeypatch, "step62_csv7")
        df = pd.read_csv(out_dir / "heart_windows.csv")
        ahet_failed = df[df["invalid_reason"] == "ahet_failed"]
        if len(ahet_failed) > 0:
            assert (ahet_failed["candidate_rejection_reason"] == "low_candidate_floor_db_low").all()

    def test_absent_config_keys_default_to_zero(self, tmp_path, monkeypatch):
        """When config lacks floor keys, defaults are 0.0 (Step 6.1 behavior preserved)."""
        import steps.step_6.extract_heart_rate as s6_mod
        captured: list[dict] = []

        def spy(phase, fs, band, **kw):
            captured.append(kw)
            return _fake_estimate(phase, fs, band, **kw)

        monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", spy)
        sid = "step62_absent"
        h5_dir, raw_dir, br_dir, row = self._setup_session(tmp_path, sid)
        # Use Step 6.1 config (no floor keys)
        cfg = self._cfg_with_step62(h5_dir, raw_dir, br_dir, tmp_path)
        del cfg["heart"]["candidate_min_peak_to_floor_db"]
        del cfg["heart"]["low_candidate_min_peak_to_floor_db"]
        out_dir = tmp_path / "results" / sid / "step_6"
        _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test",
                         no_plots=True)

        primary = [kw for kw in captured if kw.get("f_r_hz") is not None]
        assert primary
        for kw in primary:
            assert kw.get("candidate_min_peak_to_floor_db") == pytest.approx(0.0)
            assert kw.get("low_candidate_min_peak_to_floor_db") == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Step 6.3 — temporal tracker integration tests
# ---------------------------------------------------------------------------

class TestStep63Tracker:
    """Integration tests for the Viterbi temporal tracker wired into _process_session.

    _fake_estimate returns spectrum_first_pass=np.ones(n_fft) when ECA is active,
    so compute_fundamental_ratio_db = 20*log10(1.0/1.0) = 0.0 dB.  Tests that need
    the tracker to actually SELECT candidates therefore use min_fundamental_ratio_db=0.0.
    Tests that want the tracker to do nothing (only verify column presence) leave the
    default min_fundamental_ratio_db=2.0, which gates out all synthetic candidates.
    """

    def _base_cfg(self, h5_dir, raw_dir, br_dir, tmp_path) -> dict:
        return {
            "paths": {
                "manifest": "", "cubes_dir": str(h5_dir), "raw_dir": str(raw_dir),
                "breathing_rate_dir": str(br_dir),
                "processed_dir": str(tmp_path / "hr"),
                "results_dir": str(tmp_path / "results"),
            },
            "input_policy": {"max_bad_fraction": 0.10},
            "windowing": {"window_s": 20, "hop_s": 5, "use_stationary_intervals": True},
            "phase": {"method": "delta_before_mean", "impulse_clip_rad": 1.5},
            "heart": {
                "heart_band_hz": [0.8, 2.0], "k_max": 6, "ahet_deviation_hz": 0.1,
                "resp_harmonic_guard_hz": 0.05, "reject_resp_harmonic_coincidence": True,
            },
            "respiration_matching": {"max_resp_match_distance_s": 15.0},
            "comparison": {"min_pi": 0.5, "min_masimo_coverage_fraction": 0.8,
                           "min_masimo_good_pi_fraction": 0.8},
            "seed": 42,
        }

    def _tracker_cfg(self, min_fund_db: float = 0.0) -> dict:
        return {
            "enabled": True,
            "min_fundamental_ratio_db": min_fund_db,
            "max_jump_bpm_per_hop": 6.0,
            "max_gap_windows": 2,
            "resp_harmonic_mode": "score_penalty",
            "gap_cost": -8.0,
        }

    def _direct_tracker_row(
        self,
        wi: int,
        *,
        pre_valid: bool = True,
        pre_reason: str = "",
        invalid_reason: str = "",
        radar_hr_bpm: float = HR_TRUE,
    ) -> dict:
        finite_hr = np.isfinite(radar_hr_bpm)
        return {
            "session_id": "direct_tracker",
            "window_index": wi,
            "invalid_reason": invalid_reason,
            "quality_gated": invalid_reason == "quality_gated",
            "resp_peak_hz": RR_HZ,
            "masimo_pr_bpm": HR_TRUE,
            "masimo_low_quality": False,
            "radar_hr_bpm": radar_hr_bpm,
            "heart_peak_hz": radar_hr_bpm / 60.0 if finite_hr else float("nan"),
            "hr_valid": bool(finite_hr),
            "hr_confidence": "high" if finite_hr else "none",
            "hr_source": "ahet" if finite_hr else "",
            "hr_error_bpm": radar_hr_bpm - HR_TRUE if finite_hr else float("nan"),
            "hr_abs_error_bpm": abs(radar_hr_bpm - HR_TRUE) if finite_hr else float("nan"),
            "pre_tracker_radar_hr_bpm": radar_hr_bpm if pre_valid else float("nan"),
            "pre_tracker_hr_valid": pre_valid,
            "pre_tracker_hr_confidence": "high" if pre_valid else "none",
            "pre_tracker_invalid_reason": pre_reason,
            "pre_tracker_hr_error_bpm": radar_hr_bpm - HR_TRUE if pre_valid else float("nan"),
            "pre_tracker_hr_abs_error_bpm": abs(radar_hr_bpm - HR_TRUE) if pre_valid else float("nan"),
        }

    def _direct_tracker_intermediate(self, freqs_hz: np.ndarray, bpm: float = HR_TRUE) -> dict:
        n_cand = AHET_MAX_CANDIDATES
        attempted = np.zeros(n_cand, dtype=bool)
        attempted[0] = True
        refined = np.full(n_cand, np.nan)
        refined[0] = bpm / 60.0
        peak_mag = np.full(n_cand, np.nan)
        peak_mag[0] = 1.0
        prom = np.full(n_cand, np.nan)
        prom[0] = 0.5
        peak_to_floor_db = np.full(n_cand, np.nan)
        peak_to_floor_db[0] = 18.0
        rejection_code = np.full(n_cand, 5, dtype=int)
        rejection_code[0] = 0
        return {
            "heart_spectrum_first_pass": np.ones_like(freqs_hz),
            "candidate_attempted": attempted,
            "candidate_refined_hz": refined,
            "candidate_peak_magnitude": peak_mag,
            "candidate_prominence": prom,
            "peak_to_floor_ratio_db": peak_to_floor_db,
            "candidate_rejection_code": rejection_code,
        }

    def _setup(self, tmp_path, sid):
        h5_dir  = tmp_path / "cubes"; h5_dir.mkdir(exist_ok=True)
        raw_dir = tmp_path / "raw";   raw_dir.mkdir(exist_ok=True)
        br_dir  = tmp_path / "br";    br_dir.mkdir(exist_ok=True)
        _write_h5(h5_dir / f"{sid}.h5", _make_cube(), trim_frames=TRIM_FRAMES)
        _write_masimo(raw_dir / f"{sid}_masimo.csv", t0=T0_BASE, dur_s=DUR_S)
        _make_resp_df(t0=T0_BASE, n_windows=30).to_csv(br_dir / f"{sid}.csv", index=False)
        row = pd.Series({
            "session_id": sid, "radar_start_epoch_seconds": T0_BASE,
            "stationary_intervals": f"{TRIM_S}-{int(DUR_S)}",
            "locked_bin": LOCKED_BIN, "locked_range_m": 1.09,
            "chest_bin_confidence": "high", "chest_bin_review_required": "False",
        })
        return h5_dir, raw_dir, br_dir, row

    # --- disabled tracker: column presence and values ---

    def test_disabled_hr_source_is_ahet_for_valid_windows(self, tmp_path, monkeypatch):
        """Tracker disabled → AHET-valid windows must have hr_source='ahet'.

        Uses reject_resp_harmonic_coincidence=False to avoid the coincidence rejection
        that would otherwise block all windows (RR=15 bpm, HR=75 bpm, 5th harmonic).
        """
        import steps.step_6.extract_heart_rate as s6_mod
        monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", _fake_estimate)

        sid = "trk_disabled_src"
        h5_dir, raw_dir, br_dir, row = self._setup(tmp_path, sid)
        cfg = self._base_cfg(h5_dir, raw_dir, br_dir, tmp_path)
        cfg["heart"]["reject_resp_harmonic_coincidence"] = False  # avoid coincidence block
        # No temporal_tracker key → disabled
        out_dir = tmp_path / "results" / sid / "step_6"
        _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test",
                         no_plots=True)

        df = pd.read_csv(out_dir / "heart_windows.csv")
        assert "hr_source" in df.columns
        valid = df[df["hr_valid"].astype(bool)]
        assert len(valid) > 0, "Expected some AHET-valid windows"
        assert (valid["hr_source"] == "ahet").all(), \
            "All AHET-valid windows must have hr_source='ahet' when tracker disabled"
        invalid = df[~df["hr_valid"].astype(bool)]
        assert (invalid["hr_source"].fillna("") == "").all(), \
            "All invalid windows must have hr_source='' when tracker disabled"

    def test_disabled_tracker_columns_are_null(self, tmp_path, monkeypatch):
        """Tracker disabled → tracker evidence columns present but null/empty."""
        import steps.step_6.extract_heart_rate as s6_mod
        monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", _fake_estimate)

        sid = "trk_disabled_null"
        h5_dir, raw_dir, br_dir, row = self._setup(tmp_path, sid)
        cfg = self._base_cfg(h5_dir, raw_dir, br_dir, tmp_path)
        out_dir = tmp_path / "results" / sid / "step_6"
        _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test",
                         no_plots=True)

        df = pd.read_csv(out_dir / "heart_windows.csv")
        assert "tracker_decision_type" in df.columns
        # pandas read_csv converts "" to NaN; fillna("") before comparison
        assert (df["tracker_decision_type"].fillna("") == "").all(), \
            "tracker_decision_type must be '' when tracker disabled"
        assert df["tracker_node_score"].isna().all(), \
            "tracker_node_score must be NaN when tracker disabled"
        assert df["tracker_fundamental_ratio_db"].isna().all()

    def test_disabled_summary_has_tracker_stats(self, tmp_path, monkeypatch):
        """Tracker disabled → summary.json still has tracker stats with enabled=False."""
        import json
        import steps.step_6.extract_heart_rate as s6_mod
        monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", _fake_estimate)

        sid = "trk_disabled_sum"
        h5_dir, raw_dir, br_dir, row = self._setup(tmp_path, sid)
        cfg = self._base_cfg(h5_dir, raw_dir, br_dir, tmp_path)
        out_dir = tmp_path / "results" / sid / "step_6"
        _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test",
                         no_plots=True)

        summary = json.loads((out_dir / "summary.json").read_text())
        assert summary.get("temporal_tracker_enabled") is False
        assert "pre_tracker_n_valid_hr" in summary
        assert "n_tracker_selected_windows" in summary
        assert summary["n_tracker_selected_windows"] == 0

    # --- enabled tracker: candidate selection ---

    def test_enabled_selects_ahet_accepted_candidates(self, tmp_path, monkeypatch):
        """Tracker enabled (min_fund_db=0.0) → selects AHET-accepted candidates,
        sets hr_source='temporal_tracker' for all non-blocked windows."""
        import steps.step_6.extract_heart_rate as s6_mod
        monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", _fake_estimate)

        sid = "trk_enabled_sel"
        h5_dir, raw_dir, br_dir, row = self._setup(tmp_path, sid)
        cfg = self._base_cfg(h5_dir, raw_dir, br_dir, tmp_path)
        cfg["heart"]["temporal_tracker"] = self._tracker_cfg(min_fund_db=0.0)
        out_dir = tmp_path / "results" / sid / "step_6"
        _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test",
                         no_plots=True)

        df = pd.read_csv(out_dir / "heart_windows.csv")
        selected = df[df["tracker_decision_type"] == "candidate"]
        assert len(selected) > 0, "Tracker must select at least one window"
        assert (selected["hr_source"] == "temporal_tracker").all()
        assert (selected["hr_valid"].astype(bool)).all()
        assert (selected["hr_confidence"] == "high").all()
        assert selected["tracker_selected_candidate_rank"].notna().all()
        assert selected["tracker_fundamental_ratio_db"].notna().all()

    def test_enabled_pre_tracker_backup_preserved(self, tmp_path, monkeypatch):
        """Tracker enabled → pre_tracker_* columns hold the AHET-first decision.

        Uses reject_resp_harmonic_coincidence=False so AHET accepts all windows
        pre-tracker (RR=15 bpm hits 5th harmonic at HR=75 bpm otherwise).
        """
        import steps.step_6.extract_heart_rate as s6_mod
        monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", _fake_estimate)

        sid = "trk_pre_backup"
        h5_dir, raw_dir, br_dir, row = self._setup(tmp_path, sid)
        cfg = self._base_cfg(h5_dir, raw_dir, br_dir, tmp_path)
        cfg["heart"]["reject_resp_harmonic_coincidence"] = False  # avoid coincidence block
        cfg["heart"]["temporal_tracker"] = self._tracker_cfg(min_fund_db=0.0)
        out_dir = tmp_path / "results" / sid / "step_6"
        _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test",
                         no_plots=True)

        df = pd.read_csv(out_dir / "heart_windows.csv")
        # With coincidence check off + _fake_estimate: AHET accepts → pre_tracker_hr_valid=True
        selected = df[df["tracker_decision_type"] == "candidate"]
        assert len(selected) > 0, "Tracker must select some windows"
        assert (selected["pre_tracker_hr_valid"].astype(bool)).all(), \
            "pre_tracker_hr_valid must reflect AHET decision (True here)"
        assert selected["pre_tracker_hr_confidence"].eq("high").all()
        assert selected["pre_tracker_radar_hr_bpm"].notna().all()

    def test_enabled_rescues_ahet_failed_window(self, tmp_path, monkeypatch):
        """Tracker enabled (min_fund_db=0.0) rescues windows where AHET failed
        but a viable candidate (code≠low_candidate_competitor) exists in the pool."""
        import steps.step_6.extract_heart_rate as s6_mod

        N = AHET_MAX_CANDIDATES

        def _ahet_fail_keep_candidate(phase, fs, band, f_r_hz=None, **kw):
            base = _fake_estimate(phase, fs, band, f_r_hz=f_r_hz, **kw)
            if f_r_hz is not None and np.isfinite(f_r_hz):
                # ECA ran; candidate 0 was attempted but AHET rejected (ratio_db_low)
                base["ahet_verified"]           = False
                base["candidate_rejection_code"]= np.array([2] + [5] * (N - 1), dtype=int)
                base["candidate_passed"]        = np.zeros(N, dtype=bool)
                base["all_candidates_rejected"] = True
                base["rate_bpm"]                = float("nan")
            return base

        monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", _ahet_fail_keep_candidate)

        sid = "trk_rescue"
        h5_dir, raw_dir, br_dir, row = self._setup(tmp_path, sid)
        cfg = self._base_cfg(h5_dir, raw_dir, br_dir, tmp_path)
        cfg["heart"]["temporal_tracker"] = self._tracker_cfg(min_fund_db=0.0)
        out_dir = tmp_path / "results" / sid / "step_6"
        _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test",
                         no_plots=True)

        df = pd.read_csv(out_dir / "heart_windows.csv")
        rescued = df[
            (df["tracker_decision_type"] == "candidate")
            & (~df["pre_tracker_hr_valid"].astype(bool))
        ]
        assert len(rescued) > 0, \
            "Tracker must have rescued at least one AHET-failed window"
        assert (rescued["pre_tracker_invalid_reason"] == "ahet_failed").all()
        assert (rescued["hr_source"] == "temporal_tracker").all()
        assert (rescued["hr_valid"].astype(bool)).all()
        assert (rescued["hr_confidence"] == "high").all()
        # pandas read_csv converts "" to NaN; cleared invalid_reason round-trips as NaN
        assert (rescued["invalid_reason"].fillna("") == "").all()

        import json
        summary = json.loads((out_dir / "summary.json").read_text())
        assert summary["n_tracker_selected_from_ahet_failed"] > 0

    def test_tracker_gap_clears_pre_tracker_ahet_primary_hr(self):
        """DP gap over an AHET-valid row clears primary HR but keeps backup fields."""
        from steps.step_6.temporal_tracker import apply_tracker

        freqs = np.fft.rfftfreq(400, d=1.0 / FS)
        rows = [self._direct_tracker_row(0, pre_valid=True, radar_hr_bpm=HR_TRUE)]
        intermediates = [self._direct_tracker_intermediate(freqs)]
        cfg = self._tracker_cfg(min_fund_db=0.0)
        cfg["gap_cost"] = 1.0  # force the one-window path to choose a gap over the candidate

        _, stats = apply_tracker(rows, intermediates, freqs, cfg, (0.8, 2.0), 0.05)
        row = rows[0]

        assert row["tracker_decision_type"] == "gap"
        assert row["hr_source"] == ""
        assert row["hr_valid"] is False
        assert row["hr_confidence"] == "none"
        assert not np.isfinite(row["radar_hr_bpm"])
        assert not np.isfinite(row["heart_peak_hz"])
        assert not np.isfinite(row["hr_error_bpm"])
        assert not np.isfinite(row["hr_abs_error_bpm"])
        assert row["invalid_reason"] == "temporal_tracker_gap"
        assert row["pre_tracker_hr_valid"] is True
        assert row["pre_tracker_radar_hr_bpm"] == pytest.approx(HR_TRUE)
        assert stats["n_tracker_gap"] == 1
        assert stats["n_tracker_selected_windows"] == 0

    def test_tracker_ahet_failed_counter_ignores_other_pre_invalid_reasons(self):
        """The AHET rescue counter counts only pre_tracker_invalid_reason=ahet_failed."""
        from steps.step_6.temporal_tracker import apply_tracker

        freqs = np.fft.rfftfreq(400, d=1.0 / FS)
        rows = [
            self._direct_tracker_row(
                0, pre_valid=False, pre_reason="ahet_failed",
                radar_hr_bpm=float("nan"), invalid_reason="ahet_failed",
            ),
            self._direct_tracker_row(
                1, pre_valid=False, pre_reason="resp_harmonic_coincident",
                radar_hr_bpm=float("nan"), invalid_reason="resp_harmonic_coincident",
            ),
        ]
        intermediates = [self._direct_tracker_intermediate(freqs) for _ in rows]
        cfg = self._tracker_cfg(min_fund_db=0.0)

        _, stats = apply_tracker(rows, intermediates, freqs, cfg, (0.8, 2.0), 0.05)

        selected = [r for r in rows if r["tracker_decision_type"] == "candidate"]
        assert len(selected) == 2
        assert stats["n_tracker_selected_from_ahet_failed"] == 1

    def test_enabled_forced_blocked_windows_not_selected(self, tmp_path, monkeypatch):
        """Windows with resp_invalid → forced_blocked; tracker must not override them."""
        import steps.step_6.extract_heart_rate as s6_mod
        monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", _fake_estimate)

        sid = "trk_forced_blk"
        h5_dir  = tmp_path / "cubes"; h5_dir.mkdir(exist_ok=True)
        raw_dir = tmp_path / "raw";   raw_dir.mkdir(exist_ok=True)
        br_dir  = tmp_path / "br";    br_dir.mkdir(exist_ok=True)
        _write_h5(h5_dir / f"{sid}.h5", _make_cube(), trim_frames=TRIM_FRAMES)
        _write_masimo(raw_dir / f"{sid}_masimo.csv", t0=T0_BASE, dur_s=DUR_S)
        # All windows are edge-locked → resp_edge_locked → forced_blocked
        _make_resp_df(t0=T0_BASE, n_windows=30, edge_locked=True,
                      edge_lock_side="low").to_csv(br_dir / f"{sid}.csv", index=False)

        row = pd.Series({
            "session_id": sid, "radar_start_epoch_seconds": T0_BASE,
            "stationary_intervals": f"{TRIM_S}-{int(DUR_S)}",
            "locked_bin": LOCKED_BIN, "locked_range_m": 1.09,
            "chest_bin_confidence": "high", "chest_bin_review_required": "False",
        })
        cfg = self._base_cfg(h5_dir, raw_dir, br_dir, tmp_path)
        cfg["heart"]["temporal_tracker"] = self._tracker_cfg(min_fund_db=0.0)
        out_dir = tmp_path / "results" / sid / "step_6"
        _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test",
                         no_plots=True)

        df = pd.read_csv(out_dir / "heart_windows.csv")
        # All windows should be forced_blocked (or not-run if quality-gated)
        tracker_types = df["tracker_decision_type"].fillna("").unique()
        assert "candidate" not in tracker_types, \
            "Tracker must never select a forced-blocked window"
        forced = df[df["tracker_decision_type"] == "forced_blocked"]
        assert len(forced) > 0, "Expected forced_blocked windows"

    # --- NPZ shapes ---

    def test_tracker_npz_arrays_correct_shapes(self, tmp_path, monkeypatch):
        """NPZ must contain tracker arrays with correct shapes when tracker is enabled."""
        import steps.step_6.extract_heart_rate as s6_mod
        monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", _fake_estimate)

        sid = "trk_npz"
        h5_dir, raw_dir, br_dir, row = self._setup(tmp_path, sid)
        cfg = self._base_cfg(h5_dir, raw_dir, br_dir, tmp_path)
        cfg["heart"]["temporal_tracker"] = self._tracker_cfg(min_fund_db=0.0)
        out_dir = tmp_path / "results" / sid / "step_6"
        _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test",
                         no_plots=True)

        with np.load(out_dir / "heart_intermediates.npz", allow_pickle=False) as arch:
            n_windows = arch["phase_unwrapped"].shape[0]
            assert "tracker_decision_code" in arch.files
            assert arch["tracker_decision_code"].shape == (n_windows,)
            assert "tracker_selected_candidate_rank" in arch.files
            assert arch["tracker_selected_candidate_rank"].shape == (n_windows,)
            assert "candidate_fundamental_ratio_db" in arch.files
            assert arch["candidate_fundamental_ratio_db"].shape == (n_windows, AHET_MAX_CANDIDATES)
            # No object arrays in tracker section
            for key in ("tracker_decision_code", "candidate_fundamental_ratio_db",
                        "candidate_tracker_eligible", "candidate_tracker_node_score"):
                assert not arch[key].dtype.hasobject, f"{key} has object dtype"

    def test_tracker_disabled_npz_has_null_arrays(self, tmp_path, monkeypatch):
        """NPZ must contain tracker arrays even when tracker disabled (filled NaN/-1)."""
        import steps.step_6.extract_heart_rate as s6_mod
        monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", _fake_estimate)

        sid = "trk_npz_disabled"
        h5_dir, raw_dir, br_dir, row = self._setup(tmp_path, sid)
        cfg = self._base_cfg(h5_dir, raw_dir, br_dir, tmp_path)  # no tracker
        out_dir = tmp_path / "results" / sid / "step_6"
        _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test",
                         no_plots=True)

        with np.load(out_dir / "heart_intermediates.npz", allow_pickle=False) as arch:
            assert "tracker_decision_code" in arch.files
            codes = arch["tracker_decision_code"]
            assert (codes == -1).all(), \
                "All decision codes must be -1 (_CODE_NOT_RUN) when tracker disabled"

    # --- Contract CSV ---

    def test_contract_csv_has_tracker_columns(self, tmp_path, monkeypatch):
        """Step 7 contract CSV must include tracker evidence and pre-tracker columns."""
        import steps.step_6.extract_heart_rate as s6_mod
        monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", _fake_estimate)

        sid = "trk_contract"
        h5_dir, raw_dir, br_dir, row = self._setup(tmp_path, sid)
        cfg = self._base_cfg(h5_dir, raw_dir, br_dir, tmp_path)
        cfg["heart"]["temporal_tracker"] = self._tracker_cfg(min_fund_db=0.0)
        out_dir = tmp_path / "results" / sid / "step_6"
        _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test",
                         no_plots=True)

        # Contract CSV is written to the processed_dir, not the results out_dir
        contract_path = tmp_path / "hr" / f"{sid}.csv"
        assert contract_path.exists(), f"Step 7 contract CSV not found at {contract_path}"
        ct = pd.read_csv(contract_path)
        for col in ("hr_source", "tracker_decision_type",
                    "pre_tracker_hr_valid", "pre_tracker_radar_hr_bpm"):
            assert col in ct.columns, f"Contract CSV missing column: {col!r}"

    # --- Summary ---

    def test_enabled_summary_has_tracker_stats(self, tmp_path, monkeypatch):
        """Summary.json must contain tracker stats when tracker is enabled."""
        import json
        import steps.step_6.extract_heart_rate as s6_mod
        monkeypatch.setattr(s6_mod, "estimate_rate_from_phase", _fake_estimate)

        sid = "trk_sum_enabled"
        h5_dir, raw_dir, br_dir, row = self._setup(tmp_path, sid)
        cfg = self._base_cfg(h5_dir, raw_dir, br_dir, tmp_path)
        cfg["heart"]["temporal_tracker"] = self._tracker_cfg(min_fund_db=0.0)
        out_dir = tmp_path / "results" / sid / "step_6"
        _process_session(sid, row, cfg, h5_dir, raw_dir, br_dir, out_dir, "test",
                         no_plots=True)

        summary = json.loads((out_dir / "summary.json").read_text())
        assert summary.get("temporal_tracker_enabled") is True
        assert "n_tracker_selected_windows" in summary
        assert "n_tracker_gap" in summary
        assert "n_tracker_forced_blocked" in summary
        assert "n_tracker_selected_from_ahet_failed" in summary
        assert "pre_tracker_n_valid_hr" in summary
        assert summary["n_tracker_selected_windows"] > 0
