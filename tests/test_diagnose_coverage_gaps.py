"""Unit and integration tests for scripts/diagnose_coverage_gaps.py."""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

# Ensure repo root is on path so the script is importable.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.diagnose_coverage_gaps import (
    MASIMO_CANDIDATE_GUARD_BPM,
    ECA_ATTENUATION_DB_THRESHOLD,
    _TRACKER_SUCCESS_FRACTION,
    classify_k6_window,
    _classify_resp_blocker,
    _classify_window_class,
    _bool_val,
    _float_or_nan,
    _is_above_band_median,
    _is_suppressed,
    _cardiac_band_median,
    _bin_power,
    _extract_tracker_stats,
    _load_session,
    _load_candidate_diagnostics,
    _write_coverage_summary,
    _write_resp_pass_through,
    _write_candidate_yield,
    _write_k6_overlap,
    _write_diagnostic_report,
)


# ============================================================================
# Constants
# ============================================================================

class TestConstants:
    def test_masimo_candidate_guard_bpm(self):
        assert MASIMO_CANDIDATE_GUARD_BPM == pytest.approx(6.0)

    def test_eca_attenuation_db_threshold(self):
        assert ECA_ATTENUATION_DB_THRESHOLD == pytest.approx(3.0)


# ============================================================================
# Scalar helpers
# ============================================================================

class TestBoolVal:
    def test_true_bool(self):
        assert _bool_val(True) is True

    def test_false_bool(self):
        assert _bool_val(False) is False

    def test_zero_int(self):
        assert _bool_val(0) is False

    def test_one_int(self):
        assert _bool_val(1) is True

    def test_string_true(self):
        assert _bool_val("True") is True

    def test_string_false(self):
        assert _bool_val("False") is False

    def test_string_one(self):
        assert _bool_val("1") is True

    def test_string_zero(self):
        assert _bool_val("0") is False


class TestFloatOrNan:
    def test_valid_float(self):
        assert _float_or_nan("3.14") == pytest.approx(3.14)

    def test_nan_string(self):
        assert math.isnan(_float_or_nan("nan"))

    def test_none(self):
        assert math.isnan(_float_or_nan(None))

    def test_empty_string(self):
        assert math.isnan(_float_or_nan(""))


# ============================================================================
# Spectrum helpers
# ============================================================================

def _make_flat_spectrum(n: int = 50, value: float = 1.0) -> np.ndarray:
    return np.full(n, value, dtype=float)


def _make_freqs(n: int = 50, lo: float = 0.4, hi: float = 2.5) -> np.ndarray:
    return np.linspace(lo, hi, n)


class TestBinPower:
    def test_returns_correct_bin(self):
        freqs = _make_freqs(50)
        spec  = np.arange(50, dtype=float)
        target = freqs[25]
        val = _bin_power(spec, freqs, target)
        assert val == pytest.approx(spec[25])

    def test_nan_target(self):
        freqs = _make_freqs(50)
        spec  = _make_flat_spectrum(50)
        assert math.isnan(_bin_power(spec, freqs, float("nan")))

    def test_zero_target(self):
        freqs = _make_freqs(50)
        spec  = _make_flat_spectrum(50)
        assert math.isnan(_bin_power(spec, freqs, 0.0))


class TestCardiacBandMedian:
    def test_flat_spectrum(self):
        freqs = _make_freqs(100, 0.0, 3.0)
        spec  = _make_flat_spectrum(100, 2.0)
        assert _cardiac_band_median(spec, freqs) == pytest.approx(2.0)

    def test_spike_outside_band_ignored(self):
        freqs = np.linspace(0.0, 3.0, 100)
        spec  = np.ones(100)
        # Spike outside cardiac band — should not affect median
        spec[np.argmin(np.abs(freqs - 0.1))] = 1000.0
        med = _cardiac_band_median(spec, freqs)
        assert med == pytest.approx(1.0)


class TestIsAboveBandMedian:
    def test_above_median_is_high(self):
        freqs = _make_freqs(50)
        spec  = _make_flat_spectrum(50, 1.0)
        assert _is_above_band_median(5.0, spec, freqs) is True

    def test_below_median_is_low(self):
        freqs = _make_freqs(50)
        spec  = _make_flat_spectrum(50, 10.0)
        assert _is_above_band_median(1.0, spec, freqs) is False

    def test_nan_power_is_not_high(self):
        freqs = _make_freqs(50)
        spec  = _make_flat_spectrum(50, 1.0)
        assert _is_above_band_median(float("nan"), spec, freqs) is False


class TestIsSuppressed:
    def test_suppressed_when_drop_exceeds_threshold(self):
        fp  = 10.0
        fin = fp * (10 ** (-(ECA_ATTENUATION_DB_THRESHOLD + 1) / 10))
        assert _is_suppressed(fp, fin) is True

    def test_not_suppressed_when_drop_small(self):
        fp  = 10.0
        fin = fp * 0.9
        assert _is_suppressed(fp, fin) is False

    def test_nan_fp_is_not_suppressed(self):
        assert _is_suppressed(float("nan"), 1.0) is False

    def test_zero_fp_is_not_suppressed(self):
        assert _is_suppressed(0.0, 1.0) is False


# ============================================================================
# _classify_window_class
# ============================================================================

class TestClassifyWindowClass:
    def _row(self, **kw) -> dict:
        defaults = {
            "quality_gated": False,
            "invalid_reason": "",
            "hr_valid": False,
            "hr_abs_error_bpm": float("nan"),
        }
        defaults.update(kw)
        return defaults

    def test_quality_gated(self):
        assert _classify_window_class(self._row(quality_gated=True)) == "quality_gated"

    def test_resp_harmonic_rejected(self):
        row = self._row(invalid_reason="resp_harmonic_coincident")
        assert _classify_window_class(row) == "resp_harmonic_rejected"

    def test_resp_invalid_or_edge_locked(self):
        row = self._row(invalid_reason="resp_invalid")
        assert _classify_window_class(row) == "resp_invalid_or_edge_locked"

    def test_ahet_failed(self):
        row = self._row(invalid_reason="ahet_failed")
        assert _classify_window_class(row) == "ahet_failed"

    def test_good_valid(self):
        row = self._row(hr_valid=True, hr_abs_error_bpm=2.0)
        assert _classify_window_class(row) == "good_valid"

    def test_acceptable_valid(self):
        row = self._row(hr_valid=True, hr_abs_error_bpm=7.0)
        assert _classify_window_class(row) == "acceptable_valid"

    def test_bad_valid(self):
        row = self._row(hr_valid=True, hr_abs_error_bpm=12.0)
        assert _classify_window_class(row) == "bad_valid"

    def test_severe_bad_valid(self):
        row = self._row(hr_valid=True, hr_abs_error_bpm=16.0)
        assert _classify_window_class(row) == "severe_bad_valid"

    def test_other_invalid(self):
        row = self._row(hr_valid=False, invalid_reason="")
        assert _classify_window_class(row) == "other_invalid"

    def test_quality_gate_takes_precedence_over_invalid_reason(self):
        row = self._row(quality_gated=True, invalid_reason="ahet_failed")
        assert _classify_window_class(row) == "quality_gated"


# ============================================================================
# _classify_resp_blocker
# ============================================================================

class TestClassifyRespBlocker:
    def _row(self, **kw) -> dict:
        defaults = {
            "quality_gated": False,
            "resp_edge_locked": False,
            "resp_edge_lock_side": "",
            "resp_valid": True,
        }
        defaults.update(kw)
        return defaults

    def test_quality_gated(self):
        assert _classify_resp_blocker(self._row(quality_gated=True)) == "quality_gated"

    def test_edge_locked_low(self):
        row = self._row(resp_edge_locked=True, resp_edge_lock_side="low")
        assert _classify_resp_blocker(row) == "resp_edge_locked_low"

    def test_edge_locked_high(self):
        row = self._row(resp_edge_locked=True, resp_edge_lock_side="high")
        assert _classify_resp_blocker(row) == "resp_edge_locked_high"

    def test_edge_locked_unknown_side(self):
        row = self._row(resp_edge_locked=True, resp_edge_lock_side="")
        assert _classify_resp_blocker(row) == "resp_edge_locked_unknown"

    def test_resp_invalid_not_edge(self):
        row = self._row(resp_valid=False)
        assert _classify_resp_blocker(row) == "resp_invalid_not_edge"

    def test_resp_available(self):
        assert _classify_resp_blocker(self._row()) == "resp_available"

    def test_quality_gate_takes_precedence_over_edge_lock(self):
        row = self._row(quality_gated=True, resp_edge_locked=True,
                        resp_edge_lock_side="low")
        assert _classify_resp_blocker(row) == "quality_gated"


# ============================================================================
# classify_k6_window — priority ordering
# ============================================================================

def _make_flat(n: int = 50, v: float = 1.0) -> np.ndarray:
    return np.full(n, v, dtype=float)


def _freqs(n: int = 50) -> np.ndarray:
    return np.linspace(0.4, 2.5, n)


def _k6(
    *,
    invalid_reason: str = "",
    quality_gated: bool = False,
    pre_eca_power: float = 20.0,
    pre_eca_spectrum: Optional[np.ndarray] = None,
    fp_power: float = 20.0,
    freqs: Optional[np.ndarray] = None,
    closest_cand_attempted: bool = False,
    closest_cand_passed: bool = False,
    pre_eca_available: bool = True,
) -> str:
    """Helper: call classify_k6_window with defaults."""
    return classify_k6_window(
        invalid_reason=invalid_reason,
        quality_gated=quality_gated,
        pre_eca_power=pre_eca_power,
        pre_eca_spectrum=pre_eca_spectrum if pre_eca_spectrum is not None else _make_flat(),
        fp_power=fp_power,
        freqs=freqs if freqs is not None else _freqs(),
        closest_cand_attempted=closest_cand_attempted,
        closest_cand_passed=closest_cand_passed,
        pre_eca_available=pre_eca_available,
    )


class TestClassifyK6Window:
    """Priority: pre_candidate_blocked > pre_eca_missing > weak_cardiac_snr
                 > eca_attenuation_likely > ahet_gate_failure > candidate_selection_miss."""

    def test_quality_gated_yields_pre_candidate_blocked(self):
        assert _k6(quality_gated=True) == "pre_candidate_blocked"

    def test_resp_invalid_yields_pre_candidate_blocked(self):
        assert _k6(invalid_reason="resp_invalid") == "pre_candidate_blocked"

    def test_resp_harmonic_yields_pre_candidate_blocked(self):
        assert _k6(invalid_reason="resp_harmonic_coincident") == "pre_candidate_blocked"

    def test_pre_eca_not_available_yields_pre_eca_missing(self):
        assert _k6(pre_eca_available=False) == "pre_eca_missing"

    def test_none_spectrum_yields_pre_eca_missing(self):
        cls = classify_k6_window(
            invalid_reason="",
            quality_gated=False,
            pre_eca_power=float("nan"),
            pre_eca_spectrum=None,
            fp_power=float("nan"),
            freqs=None,
            closest_cand_attempted=False,
            closest_cand_passed=False,
            pre_eca_available=True,
        )
        assert cls == "pre_eca_missing"

    def test_nan_pre_eca_power_yields_pre_eca_missing(self):
        assert _k6(pre_eca_power=float("nan")) == "pre_eca_missing"

    def test_low_pre_eca_power_yields_weak_cardiac_snr(self):
        # pre_eca_power below median of the pre-ECA spectrum
        spec = _make_flat(50, 10.0)
        assert _k6(pre_eca_power=1.0, pre_eca_spectrum=spec) == "weak_cardiac_snr"

    def test_high_pre_eca_but_suppressed_fp_yields_eca_attenuation(self):
        # pre_eca high, fp suppressed relative to pre_eca
        spec    = _make_flat(50, 1.0)
        pre_pow = 20.0
        # _is_suppressed uses 10*log10; threshold 3 dB → ratio < 10^(-3/10) ≈ 0.5
        fp_pow  = pre_pow * (10 ** (-(ECA_ATTENUATION_DB_THRESHOLD + 1) / 10))
        cls = _k6(pre_eca_power=pre_pow, pre_eca_spectrum=spec, fp_power=fp_pow)
        assert cls == "eca_attenuation_likely"

    def test_both_high_cand_attempted_not_passed_yields_ahet_gate_failure(self):
        spec    = _make_flat(50, 1.0)
        pre_pow = 20.0
        fp_pow  = pre_pow * 0.9   # small drop — not suppressed
        cls = _k6(
            pre_eca_power=pre_pow, pre_eca_spectrum=spec,
            fp_power=fp_pow,
            closest_cand_attempted=True, closest_cand_passed=False,
        )
        assert cls == "ahet_gate_failure"

    def test_both_high_no_cand_attempted_yields_candidate_selection_miss(self):
        spec    = _make_flat(50, 1.0)
        pre_pow = 20.0
        fp_pow  = pre_pow * 0.9
        cls = _k6(
            pre_eca_power=pre_pow, pre_eca_spectrum=spec,
            fp_power=fp_pow,
            closest_cand_attempted=False,
        )
        assert cls == "candidate_selection_miss"

    def test_priority_pre_candidate_blocked_beats_pre_eca_missing(self):
        cls = _k6(quality_gated=True, pre_eca_available=False)
        assert cls == "pre_candidate_blocked"

    def test_priority_pre_candidate_blocked_beats_eca(self):
        spec    = _make_flat(50, 1.0)
        pre_pow = 20.0
        fp_pow  = pre_pow * (10 ** (-(ECA_ATTENUATION_DB_THRESHOLD + 1) / 10))
        cls = _k6(
            quality_gated=True,
            pre_eca_power=pre_pow, pre_eca_spectrum=spec, fp_power=fp_pow,
            closest_cand_attempted=True,
        )
        assert cls == "pre_candidate_blocked"

    def test_priority_pre_eca_missing_beats_weak_snr(self):
        """pre_eca_available=False must win over weak spectrum check."""
        spec = _make_flat(50, 10.0)
        cls = _k6(pre_eca_power=1.0, pre_eca_spectrum=spec, pre_eca_available=False)
        assert cls == "pre_eca_missing"

    def test_priority_weak_snr_beats_eca(self):
        """Low pre_eca_power wins over ECA check."""
        spec    = _make_flat(50, 10.0)
        pre_pow = 1.0   # below median → weak
        fp_pow  = pre_pow * (10 ** (-(ECA_ATTENUATION_DB_THRESHOLD + 1) / 10))
        cls = _k6(pre_eca_power=pre_pow, pre_eca_spectrum=spec, fp_power=fp_pow)
        assert cls == "weak_cardiac_snr"

    def test_priority_eca_beats_ahet_gate_failure(self):
        """ECA suppression wins even if a close candidate was attempted."""
        spec    = _make_flat(50, 1.0)
        pre_pow = 20.0
        fp_pow  = pre_pow * (10 ** (-(ECA_ATTENUATION_DB_THRESHOLD + 1) / 10))
        cls = _k6(
            pre_eca_power=pre_pow, pre_eca_spectrum=spec, fp_power=fp_pow,
            closest_cand_attempted=True, closest_cand_passed=False,
        )
        assert cls == "eca_attenuation_likely"

    def test_passed_candidate_is_not_ahet_gate_failure(self):
        """If closest candidate passed, ahet_gate_failure must not fire."""
        spec    = _make_flat(50, 1.0)
        pre_pow = 20.0
        cls = _k6(
            pre_eca_power=pre_pow, pre_eca_spectrum=spec, fp_power=pre_pow * 0.9,
            closest_cand_attempted=True, closest_cand_passed=True,
        )
        assert cls != "ahet_gate_failure"


# ============================================================================
# I/O and integration: synthetic data
# ============================================================================

def _make_hw_df(
    n: int = 5,
    session_id: str = "sess1",
    *,
    include_tracker: bool = False,
) -> pd.DataFrame:
    """Build a synthetic heart_windows.csv DataFrame.

    When include_tracker=True, adds Step 6.3 tracker columns.  Window 0 is
    hr_valid and gets tracker_decision_type="candidate"; windows 1..n-1 are
    ahet_failed and get tracker_decision_type="gap".
    """
    data = {
        "session_id":          [session_id] * n,
        "window_index":        list(range(n)),
        "start_epoch":         [1_700_000_000.0 + i * 30 for i in range(n)],
        "end_epoch":           [1_700_000_030.0 + i * 30 for i in range(n)],
        "start_frame":         list(range(0, n * 100, 100)),
        "end_frame":           list(range(100, (n + 1) * 100, 100)),
        "locked_bin":          [5] * n,
        "locked_range_m":      [1.4] * n,
        "radar_hr_bpm":        [80.0] * n,
        "heart_peak_hz":       [80.0 / 60.0] * n,
        "hr_valid":            [True] + [False] * (n - 1),
        "hr_confidence":       [0.9] + [0.0] * (n - 1),
        "invalid_reason":      [""] + ["ahet_failed"] * (n - 1),
        "quality_gated":       [False] * n,
        "n_bad_frames":        [0] * n,
        "bad_fraction":        [0.0] * n,
        "matched_rr_window_index": list(range(n)),
        "resp_match_distance_s": [0.5] * n,
        "radar_rr_bpm":        [13.0] * n,
        "resp_peak_hz":        [13.0 / 60.0] * n,
        "resp_valid":          [True] * n,
        "resp_confidence":     [0.8] * n,
        "resp_edge_locked":    [False] * n,
        "resp_edge_lock_side": [""] * n,
        "baseline_hr_bpm":     [55.0] * n,
        "eca_applied":         [True] * n,
        "eca_forbidden_zone":  ["(72.0, 84.0)"] * n,
        "ahet_verified":       [False] + [False] * (n - 1),
        "harmonic_suspect":    [False] * n,
        "resp_harmonic_coincident": [False] * n,
        "heart_spectrum_stage": ["post_eca"] * n,
        "accepted_candidate_rank": [0] + [-1] * (n - 1),
        "accepted_candidate_refined_hz": [80.0 / 60.0] + [float("nan")] * (n - 1),
        "accepted_second_harmonic_refined_hz": [160.0 / 60.0] + [float("nan")] * (n - 1),
        "masimo_pr_bpm":       [80.0] * n,
        "masimo_pr_std_bpm":   [1.0] * n,
        "masimo_n_total":      [30] * n,
        "masimo_n_good_pi":    [28] * n,
        "masimo_coverage_fraction": [1.0] * n,
        "masimo_good_pi_fraction":  [0.9] * n,
        "masimo_low_quality":  [False] * n,
        "hr_error_bpm":        [0.0] + [float("nan")] * (n - 1),
        "hr_abs_error_bpm":    [0.0] + [float("nan")] * (n - 1),
        "candidate_rejection_reason": [""] + ["ratio_db_low"] * (n - 1),
        "low_candidate_competitor":   [False] * n,
        "all_candidates_rejected":    [False] + [True] * (n - 1),
        "ahet_gate_mode":             ["strict_v1"] * n,
        "n_eca_skipped_harmonics":    [3] * n,
        "eca_skipped_harmonic_ks":    ["[4, 5, 6]"] * n,
    }
    if include_tracker:
        # Window 0: selected by tracker from AHET-valid pre-tracker state.
        # Windows 1..n-1: gapped by tracker (all had ahet_failed pre-tracker).
        data["hr_source"]                  = ["ahet"] + [""] * (n - 1)
        data["pre_tracker_hr_valid"]       = [True]   + [False] * (n - 1)
        data["pre_tracker_invalid_reason"] = [""]     + ["ahet_failed"] * (n - 1)
        data["tracker_decision_type"]      = ["candidate"] + ["gap"] * (n - 1)
        data["tracker_fundamental_ratio_db"] = [5.0] + [float("nan")] * (n - 1)
        data["tracker_node_score"]           = [2.5] + [float("nan")] * (n - 1)
    return pd.DataFrame(data)


def _make_npz(n: int = 5, n_freqs: int = 60, *, include_pre_eca: bool = True) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(42)
    freqs = np.linspace(0.4, 2.5, n_freqs)
    d = {
        "phase_unwrapped":           rng.standard_normal((n, 200)),
        "heart_freqs_hz":            freqs,
        "heart_spectrum_first_pass": rng.exponential(1.0, (n, n_freqs)),
        "heart_spectrum":            rng.exponential(0.5, (n, n_freqs)),
        "baseline_spectrum":         rng.exponential(0.5, (n, n_freqs)),
        "candidate_rejection_code":  np.full((n, 3), -1, dtype=int),
    }
    if include_pre_eca:
        d["heart_spectrum_pre_eca"] = rng.exponential(1.0, (n, n_freqs))
    return d


@pytest.fixture
def tmp_session(tmp_path: Path) -> dict:
    """Writes a synthetic session to a temp directory and returns path info."""
    session_id   = "sess1"
    results_root = tmp_path / "results"
    step6_dir    = results_root / session_id / "step_6"
    step6_dir.mkdir(parents=True)

    hw = _make_hw_df(5, session_id)
    hw.to_csv(step6_dir / "heart_windows.csv", index=False)

    npz_data = _make_npz(5)
    np.savez(step6_dir / "heart_intermediates.npz", **npz_data)

    return {
        "session_id":   session_id,
        "results_root": results_root,
        "step6_dir":    step6_dir,
    }


class TestLoadSession:
    def test_returns_dict_with_keys(self, tmp_session):
        sd = _load_session(tmp_session["session_id"], tmp_session["results_root"])
        assert sd is not None
        assert "df" in sd
        assert "npz" in sd
        assert "freqs_hz" in sd
        assert sd["n_windows"] == 5

    def test_missing_csv_returns_none(self, tmp_path):
        results_root = tmp_path / "results"
        results_root.mkdir()
        sd = _load_session("nonexistent", results_root)
        assert sd is None

    def test_missing_npz_returns_none(self, tmp_path):
        sid      = "sess_no_npz"
        step6    = tmp_path / "results" / sid / "step_6"
        step6.mkdir(parents=True)
        _make_hw_df(3, sid).to_csv(step6 / "heart_windows.csv", index=False)
        sd = _load_session(sid, tmp_path / "results")
        assert sd is None

    def test_freqs_hz_loaded(self, tmp_session):
        sd = _load_session(tmp_session["session_id"], tmp_session["results_root"])
        assert sd["freqs_hz"] is not None
        assert len(sd["freqs_hz"]) == 60

    def test_npz_mismatch_returns_none(self, tmp_path):
        """CSV has 5 rows but NPZ has 3 windows → mismatch → None."""
        sid    = "mismatch"
        step6  = tmp_path / "results" / sid / "step_6"
        step6.mkdir(parents=True)
        _make_hw_df(5, sid).to_csv(step6 / "heart_windows.csv", index=False)
        npz_data = _make_npz(3)
        np.savez(step6 / "heart_intermediates.npz", **npz_data)
        sd = _load_session(sid, tmp_path / "results")
        assert sd is None


class TestLoadCandidateDiagnostics:
    def test_returns_none_when_file_absent(self, tmp_path):
        diag_root = tmp_path / "diag"
        diag_root.mkdir()
        result = _load_candidate_diagnostics(diag_root)
        assert result is None

    def test_returns_dataframe_when_file_exists(self, tmp_path):
        diag_root = tmp_path / "diag"
        diag_root.mkdir()
        df = pd.DataFrame({
            "session_id":   ["a", "a"],
            "window_index": [0, 1],
        })
        df.to_csv(diag_root / "candidate_diagnostics.csv", index=False)
        result = _load_candidate_diagnostics(diag_root)
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 2


# ============================================================================
# Output writers
# ============================================================================

def _make_session_dict(
    tmp_path: Path,
    session_id: str = "sess1",
    n: int = 5,
    *,
    include_tracker: bool = False,
) -> dict:
    hw = _make_hw_df(n, session_id, include_tracker=include_tracker)
    npz_data = _make_npz(n)
    return {
        "session_id": session_id,
        "df":         hw,
        "npz":        dict(npz_data),
        "freqs_hz":   npz_data["heart_freqs_hz"],
        "n_windows":  n,
        "step6_dir":  tmp_path / "results" / session_id / "step_6",
    }


class TestWriteCoverageSummary:
    def test_writes_csv(self, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        sd = _make_session_dict(tmp_path, "s1", 5)
        summary = _write_coverage_summary([sd], {}, out_dir)
        p = out_dir / "coverage_summary.csv"
        assert p.exists()
        df = pd.read_csv(p)
        assert len(df) == 1
        assert "session_id" in df.columns
        assert "n_windows" in df.columns
        assert "primary_blocker" in df.columns

    def test_valid_fraction_correct(self, tmp_path):
        out_dir = tmp_path / "out2"
        out_dir.mkdir()
        sd = _make_session_dict(tmp_path, "s2", 5)
        # 1 hr_valid=True window → good_valid; 4 ahet_failed
        summary = _write_coverage_summary([sd], {}, out_dir)
        assert summary["n_good_valid"].iloc[0] == 1
        assert summary["step6_valid_fraction"].iloc[0] == pytest.approx(1 / 5)

    def test_multiple_sessions(self, tmp_path):
        out_dir = tmp_path / "out3"
        out_dir.mkdir()
        s1 = _make_session_dict(tmp_path, "a", 5)
        s2 = _make_session_dict(tmp_path, "b", 3)
        summary = _write_coverage_summary([s1, s2], {}, out_dir)
        assert len(summary) == 2


class TestWriteRespPassThrough:
    def test_writes_csv_with_resp_blocker_column(self, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        sd = _make_session_dict(tmp_path, "s1", 5)
        out = _write_resp_pass_through([sd], out_dir)
        p = out_dir / "resp_pass_through_diagnostics.csv"
        assert p.exists()
        df = pd.read_csv(p)
        assert "resp_blocker" in df.columns

    def test_row_count_matches_total_windows(self, tmp_path):
        out_dir = tmp_path / "out2"
        out_dir.mkdir()
        s1 = _make_session_dict(tmp_path, "a", 5)
        s2 = _make_session_dict(tmp_path, "b", 3)
        out = _write_resp_pass_through([s1, s2], out_dir)
        assert len(out) == 8


class TestWriteCandidateYield:
    def _make_cand(self, session_id: str, n_windows: int, n_cands: int = 3) -> pd.DataFrame:
        rows = []
        for wi in range(n_windows):
            for cr in range(n_cands):
                rows.append({
                    "session_id":          session_id,
                    "window_index":        wi,
                    "candidate_rank":      cr,
                    "candidate_attempted": cr == 0,
                    "candidate_refined_hz": 80.0 / 60.0,
                    "candidate_peak_magnitude": 1.0,
                    "candidate_prominence":  2.0,
                    "peak_to_floor_ratio_db": 3.0,
                    "candidate_rejection_code": 2,
                    "candidate_rejection_reason": "ratio_db_low",
                    "candidate_nearest_resp_harmonic_k": 6,
                    "candidate_distance_to_nearest_resp_harmonic_bpm": 2.0,
                    "is_accepted": False,
                    "candidate_is_low": False,
                })
        return pd.DataFrame(rows)

    def test_returns_none_without_candidate_df(self, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        sd = _make_session_dict(tmp_path, "s1", 5)
        result = _write_candidate_yield([sd], None, out_dir)
        assert result is None

    def test_row_count_is_n_windows_times_n_cands(self, tmp_path):
        out_dir = tmp_path / "out2"
        out_dir.mkdir()
        sid = "s1"
        n_w = 5
        n_c = 3
        sd     = _make_session_dict(tmp_path, sid, n_w)
        cand   = self._make_cand(sid, n_w, n_c)
        result = _write_candidate_yield([sd], cand, out_dir)
        assert result is not None
        # Outer join from candidate side
        assert len(result) == n_w * n_c

    def test_output_file_written(self, tmp_path):
        out_dir = tmp_path / "out3"
        out_dir.mkdir()
        sid = "s2"
        sd     = _make_session_dict(tmp_path, sid, 3)
        cand   = self._make_cand(sid, 3)
        _write_candidate_yield([sd], cand, out_dir)
        assert (out_dir / "step6_candidate_yield_diagnostics.csv").exists()


class TestWriteK6Overlap:
    def _setup(self, tmp_path: Path, session_id: str = "test5", n: int = 4):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        # Use npz backed by real numpy arrays
        hw      = _make_hw_df(n, session_id)
        npz_raw = _make_npz(n)
        sd = {
            "session_id": session_id,
            "df":         hw,
            "npz":        npz_raw,
            "freqs_hz":   npz_raw["heart_freqs_hz"],
            "n_windows":  n,
            "step6_dir":  tmp_path / "results" / session_id / "step_6",
        }
        return sd, out_dir

    def test_returns_none_for_missing_session(self, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        sd = _make_session_dict(tmp_path, "other", 4)
        result = _write_k6_overlap([sd], None, out_dir, k6_session="test5")
        assert result is None

    def test_writes_csv(self, tmp_path):
        sd, out_dir = self._setup(tmp_path, "test5", 4)
        _write_k6_overlap([sd], None, out_dir, k6_session="test5")
        assert (out_dir / "test5_k6_overlap_diagnostics.csv").exists()

    def test_one_row_per_window(self, tmp_path):
        n = 6
        sd, out_dir = self._setup(tmp_path, "test5", n)
        result = _write_k6_overlap([sd], None, out_dir, k6_session="test5")
        assert result is not None
        assert len(result) == n

    def test_k6_classification_column_present(self, tmp_path):
        sd, out_dir = self._setup(tmp_path, "test5", 4)
        result = _write_k6_overlap([sd], None, out_dir, k6_session="test5")
        assert "k6_classification" in result.columns

    def test_k6_overlaps_masimo_column_present(self, tmp_path):
        sd, out_dir = self._setup(tmp_path, "test5", 4)
        result = _write_k6_overlap([sd], None, out_dir, k6_session="test5")
        assert "k6_overlaps_masimo" in result.columns

    def test_npz_missing_first_pass_does_not_crash(self, tmp_path):
        """Script should handle NPZ without heart_spectrum_first_pass gracefully."""
        sd, out_dir = self._setup(tmp_path, "test5", 4)
        # Remove the first_pass key from npz dict
        del sd["npz"]["heart_spectrum_first_pass"]
        result = _write_k6_overlap([sd], None, out_dir, k6_session="test5")
        assert result is not None
        assert len(result) == 4
        # Classification for windows without fp data should be insufficient_data
        # (or pre_candidate_blocked depending on invalid_reason, but NOT crash)


class TestWriteDiagnosticReport:
    def test_creates_markdown_file(self, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        sd      = _make_session_dict(tmp_path, "test2", 5)
        summary = pd.DataFrame([{
            "session_id":                    "test2",
            "n_windows":                     5,
            "n_good_valid":                  0,
            "n_acceptable_valid":            0,
            "n_bad_valid":                   0,
            "n_severe_bad_valid":            0,
            "n_valid":                       0,
            "step6_valid_fraction":          0.0,
            "n_ahet_failed":                 4,
            "n_resp_invalid_or_edge_locked": 4,
            "n_resp_harmonic_rejected":      0,
            "n_quality_gated":               0,
            "n_other_invalid":               0,
            "primary_blocker":               "resp_invalid_or_edge_locked",
        }])
        _write_diagnostic_report([sd], summary, None, out_dir)
        rpt = out_dir / "diagnostic_report.md"
        assert rpt.exists()
        text = rpt.read_text(encoding="utf-8")
        assert "test2" in text

    def test_report_mentions_coverage(self, tmp_path):
        out_dir = tmp_path / "rpt_test"
        out_dir.mkdir()
        sd      = _make_session_dict(tmp_path, "test2", 5)
        summary = pd.DataFrame([{
            "session_id":                    "test2",
            "n_windows":                     5,
            "n_good_valid":                  0,
            "n_acceptable_valid":            0,
            "n_bad_valid":                   0,
            "n_severe_bad_valid":            0,
            "n_valid":                       0,
            "step6_valid_fraction":          0.0,
            "n_ahet_failed":                 4,
            "n_resp_invalid_or_edge_locked": 4,
            "n_resp_harmonic_rejected":      0,
            "n_quality_gated":               0,
            "n_other_invalid":               0,
            "primary_blocker":               "resp_invalid_or_edge_locked",
        }])
        _write_diagnostic_report([sd], summary, None, out_dir)
        text = (out_dir / "diagnostic_report.md").read_text(encoding="utf-8")
        assert "Respiration" in text

    def test_report_ahet_gate_failure_branch(self, tmp_path):
        """When ahet_gate_failure dominates k6_df, report names it as primary cause."""
        out_dir = tmp_path / "ahet_branch"
        out_dir.mkdir()
        sd      = _make_session_dict(tmp_path, "test5", 5)
        summary = pd.DataFrame([{
            "session_id":                    "test5",
            "n_windows":                     5,
            "n_good_valid":                  0,
            "n_acceptable_valid":            0,
            "n_bad_valid":                   0,
            "n_severe_bad_valid":            0,
            "n_valid":                       0,
            "step6_valid_fraction":          0.0,
            "n_ahet_failed":                 5,
            "n_resp_invalid_or_edge_locked": 0,
            "n_resp_harmonic_rejected":      0,
            "n_quality_gated":               0,
            "n_other_invalid":               0,
            "primary_blocker":               "step6_ahet_ratio_low",
        }])
        # Synthetic k6_df where ahet_gate_failure dominates (4/5 estimator-run windows).
        # Window 4 is pre_candidate_blocked with NaN power — mirrors the real test5
        # windows 23/24 which have no spectrum power. The ECA-not-acting check must
        # still trigger based only on the finite-valued rows.
        fp_val = 20.0
        k6_df = pd.DataFrame({
            "session_id":                            ["test5"] * 5,
            "window_index":                          list(range(5)),
            "radar_rr_bpm":                          [13.0] * 4 + [float("nan")],
            "k6_bpm":                                [78.0] * 4 + [float("nan")],
            "masimo_pr_bpm":                         [80.0] * 5,
            "k6_masimo_dist_bpm":                    [2.0] * 4 + [float("nan")],
            "k6_overlaps_masimo":                    [True] * 4 + [False],
            "window_blocked_before_ahet":            [False] * 4 + [True],
            "closest_cand_refined_hz":               [80.0 / 60.0] * 4 + [float("nan")],
            "closest_cand_peak_to_floor_ratio_db":   [-4.5, -3.0, -5.2, -2.1, float("nan")],
            "closest_cand_rejection_reason":         ["ratio_db_low"] * 4 + [""],
            "closest_cand_attempted":                [True] * 4 + [False],
            "closest_cand_passed":                   [False] * 5,
            "pre_eca_power_at_masimo_bin":           [fp_val] * 4 + [float("nan")],
            "first_pass_power_at_masimo_bin":        [fp_val] * 4 + [float("nan")],
            "final_power_at_masimo_bin":             [fp_val] * 4 + [float("nan")],
            "baseline_power_at_masimo_bin":          [fp_val] * 4 + [float("nan")],
            # Near-zero delta → ECA is not attenuating at Masimo PR bin
            "pre_to_first_pass_delta_db":            [0.0] * 4 + [float("nan")],
            "first_pass_to_final_delta_db":          [0.0] * 4 + [float("nan")],
            "k6_classification":                     ["ahet_gate_failure"] * 4
                                                     + ["pre_candidate_blocked"],
        })
        _write_diagnostic_report([sd], summary, k6_df, out_dir)
        text = (out_dir / "diagnostic_report.md").read_text(encoding="utf-8")
        # Must name the AHET ratio gate as primary cause.
        assert "AHET ratio_db gate" in text
        # Must note that ECA is not acting (near-zero delta).
        assert "ECA is NOT attenuating" in text
        # Must NOT claim mixed failure modes.
        assert "Mixed failure modes" not in text


# ============================================================================
# Robustness
# ============================================================================

class TestRobustness:
    def test_classify_window_class_handles_nan_error(self):
        row = {
            "quality_gated": False,
            "invalid_reason": "",
            "hr_valid": True,
            "hr_abs_error_bpm": float("nan"),
        }
        # Valid with NaN error → treated as good_valid (no threshold applied)
        result = _classify_window_class(row)
        assert result == "good_valid"

    def test_classify_resp_blocker_string_true(self):
        row = {
            "quality_gated": "False",
            "resp_edge_locked": "True",
            "resp_edge_lock_side": "low",
            "resp_valid": "True",
        }
        assert _classify_resp_blocker(row) == "resp_edge_locked_low"

    def test_classify_k6_window_nan_pre_eca_power(self):
        spec = _make_flat(50, 1.0)
        cls  = classify_k6_window(
            invalid_reason="",
            quality_gated=False,
            pre_eca_power=float("nan"),
            pre_eca_spectrum=spec,
            fp_power=float("nan"),
            freqs=_freqs(),
            closest_cand_attempted=False,
            closest_cand_passed=False,
            pre_eca_available=True,
        )
        # nan pre_eca_power with pre_eca_available=True → pre_eca_missing
        assert cls == "pre_eca_missing"

    def test_classify_k6_window_old_npz_without_pre_eca_field(self):
        """Old NPZ (pre_eca_available=False) must yield pre_eca_missing, not crash."""
        cls = _k6(pre_eca_available=False)
        assert cls == "pre_eca_missing"

    def test_write_coverage_summary_zero_windows(self, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        hw  = pd.DataFrame(columns=list(_make_hw_df(1, "empty").columns))
        freqs = _make_freqs()
        sd  = {
            "session_id": "empty",
            "df":         hw,
            "npz":        {"heart_freqs_hz": freqs, "phase_unwrapped": np.zeros((0, 200))},
            "freqs_hz":   freqs,
            "n_windows":  0,
            "step6_dir":  tmp_path / "empty" / "step_6",
        }
        # Should not crash; valid_fraction should be NaN
        summary = _write_coverage_summary([sd], {}, out_dir)
        assert math.isnan(float(summary["step6_valid_fraction"].iloc[0]))


# ============================================================================
# Tracker-aware coverage summary
# ============================================================================

class TestTrackerAwareSummary:
    def test_summary_includes_tracker_columns_when_present(self, tmp_path):
        """When heart_windows.csv has tracker columns, summary carries them."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        # n=5: window 0 hr_valid + tracker candidate, windows 1-4 ahet_failed + tracker gap
        sd = _make_session_dict(tmp_path, "s1", 5, include_tracker=True)
        summary = _write_coverage_summary([sd], {}, out_dir)

        assert "temporal_tracker_present" in summary.columns
        assert bool(summary["temporal_tracker_present"].iloc[0]) is True
        assert int(summary["n_pre_tracker_valid"].iloc[0]) == 1
        assert int(summary["n_tracker_selected"].iloc[0]) == 1
        assert int(summary["n_tracker_gap"].iloc[0]) == 4
        assert int(summary["n_tracker_forced_blocked"].iloc[0]) == 0
        # window 0 has pre_tracker_invalid_reason="" (not "ahet_failed") → 0 rescued from AHET
        assert int(summary["n_tracker_selected_from_ahet_failed"].iloc[0]) == 0

    def test_summary_backward_compatible_without_tracker_columns(self, tmp_path):
        """Old heart_windows.csv without tracker columns → present=False,
        counts=0, n_pre_tracker_valid=NaN (not applicable, not 'known zero')."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        sd = _make_session_dict(tmp_path, "s1", 5, include_tracker=False)
        summary = _write_coverage_summary([sd], {}, out_dir)

        assert "temporal_tracker_present" in summary.columns
        assert bool(summary["temporal_tracker_present"].iloc[0]) is False
        assert math.isnan(float(summary["n_pre_tracker_valid"].iloc[0]))
        assert int(summary["n_tracker_selected"].iloc[0]) == 0
        assert int(summary["n_tracker_gap"].iloc[0]) == 0
        assert int(summary["n_tracker_forced_blocked"].iloc[0]) == 0
        assert int(summary["n_tracker_selected_from_ahet_failed"].iloc[0]) == 0
        assert int(summary["n_tracker_gap_from_pre_valid"].iloc[0]) == 0

    def test_summary_gap_from_pre_valid_counts_correctly(self, tmp_path):
        """n_tracker_gap_from_pre_valid counts windows where a pre-tracker-valid
        AHET reading was turned into a tracker gap (isolated window scenario)."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        # Build a df with 3 windows:
        #   wi=0: pre_valid=True, decision="gap"   → counts as gap_from_pre_valid
        #   wi=1: pre_valid=True, decision="candidate" → does NOT count
        #   wi=2: pre_valid=False, decision="gap"  → does NOT count
        hw = _make_hw_df(3, "s1", include_tracker=True)
        # Override to set up the scenario explicitly
        hw["pre_tracker_hr_valid"]  = [True,       True,       False]
        hw["tracker_decision_type"] = ["gap",       "candidate", "gap"]
        sd = {
            "session_id": "s1",
            "df":         hw,
            "npz":        dict(_make_npz(3)),
            "freqs_hz":   _make_npz(3)["heart_freqs_hz"],
            "n_windows":  3,
            "step6_dir":  tmp_path / "results" / "s1" / "step_6",
        }
        summary = _write_coverage_summary([sd], {}, out_dir)

        assert int(summary["n_tracker_gap_from_pre_valid"].iloc[0]) == 1


# ============================================================================
# Next-target logic
# ============================================================================

def _make_summary_row(
    session_id: str,
    primary_blocker: str,
    step6_valid_fraction: float,
    n_valid: int = 0,
    n_windows: int = 10,
    n_resp_invalid_or_edge_locked: int = 0,
    n_resp_harmonic_rejected: int = 0,
) -> dict:
    return {
        "session_id":                    session_id,
        "n_windows":                     n_windows,
        "n_good_valid":                  n_valid,
        "n_acceptable_valid":            0,
        "n_bad_valid":                   0,
        "n_severe_bad_valid":            0,
        "n_valid":                       n_valid,
        "step6_valid_fraction":          step6_valid_fraction,
        "n_ahet_failed":                 max(0, n_windows - n_valid - n_resp_invalid_or_edge_locked),
        "n_resp_invalid_or_edge_locked": n_resp_invalid_or_edge_locked,
        "n_resp_harmonic_rejected":      n_resp_harmonic_rejected,
        "n_quality_gated":               0,
        "n_other_invalid":               0,
        "primary_blocker":               primary_blocker,
        "temporal_tracker_present":            False,
        "n_pre_tracker_valid":                 float("nan"),
        "n_tracker_selected":                  n_valid,
        "n_tracker_gap":                       0,
        "n_tracker_forced_blocked":            0,
        "n_tracker_selected_from_ahet_failed": 0,
        "n_tracker_gap_from_pre_valid":        0,
    }


class TestNextTargetLogic:
    def test_no_resp_recommendation_when_primary_blocker_is_ahet(self, tmp_path):
        """test2 with primary_blocker=step6_ahet_ratio_low and 3 residual resp
        failures must NOT generate a 'Fix respiration failures' recommendation."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        # test2: AHET dominant, only 3/33 resp failures
        sd = _make_session_dict(tmp_path, "test2", 5)
        summary = pd.DataFrame([
            _make_summary_row(
                "test2", "step6_ahet_ratio_low", 0.06,
                n_valid=2, n_windows=33,
                n_resp_invalid_or_edge_locked=3,
            )
        ])
        _write_diagnostic_report([sd], summary, None, out_dir)
        text = (out_dir / "diagnostic_report.md").read_text(encoding="utf-8")

        # Step 5 / resp recommendations must not appear in Next Target
        assert "Fix respiration failures for" not in text
        # AHET yield must be recommended
        assert "Step 6 tracker/AHET yield" in text

    def test_resp_recommendation_when_primary_blocker_is_resp(self, tmp_path):
        """A session with primary_blocker=resp_invalid_or_edge_locked must
        appear in the 'Fix respiration failures' recommendation."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        sd = _make_session_dict(tmp_path, "alpha", 10)
        summary = pd.DataFrame([
            _make_summary_row(
                "alpha", "resp_invalid_or_edge_locked", 0.0,
                n_valid=0, n_windows=10,
                n_resp_invalid_or_edge_locked=8,
            )
        ])
        _write_diagnostic_report([sd], summary, None, out_dir)
        text = (out_dir / "diagnostic_report.md").read_text(encoding="utf-8")

        assert "Fix respiration failures for" in text
        assert "alpha" in text

    def test_success_case_excluded_from_ahet_recommendation(self, tmp_path):
        """Session with step6_valid_fraction >= 0.75 must not appear in AHET
        yield recommendation even if primary_blocker is step6_ahet_ratio_low."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        sd = _make_session_dict(tmp_path, "success", 10)
        summary = pd.DataFrame([
            _make_summary_row(
                "success", "step6_ahet_ratio_low", 0.85,
                n_valid=8, n_windows=10,
            )
        ])
        _write_diagnostic_report([sd], summary, None, out_dir)
        text = (out_dir / "diagnostic_report.md").read_text(encoding="utf-8")

        # Neither recommendation should appear; should say no critical gaps.
        assert "Fix respiration failures for" not in text
        assert "Step 6 tracker/AHET yield" not in text
        assert "No critical yield gaps" in text


# ============================================================================
# Report titles
# ============================================================================

class TestReportTitles:
    def test_no_yield_zero_title_for_session_with_valid_hr(self, tmp_path):
        """test5 with n_valid > 0 must NOT use 'Heart Rate Yield Zero' heading."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        sd = _make_session_dict(tmp_path, "test5", 10)
        summary = pd.DataFrame([
            _make_summary_row(
                "test5", "step6_ahet_ratio_low", 0.24,
                n_valid=8, n_windows=33,
            )
        ])
        _write_diagnostic_report([sd], summary, None, out_dir)
        text = (out_dir / "diagnostic_report.md").read_text(encoding="utf-8")

        assert "Heart Rate Yield Zero" not in text
        assert "Partial Heart Rate Yield" in text

    def test_yield_zero_title_when_no_valid_hr(self, tmp_path):
        """test5 with n_valid=0 must use the original 'Heart Rate Yield Zero' heading."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        sd = _make_session_dict(tmp_path, "test5", 10)
        summary = pd.DataFrame([
            _make_summary_row(
                "test5", "step6_ahet_ratio_low", 0.0,
                n_valid=0, n_windows=10,
            )
        ])
        _write_diagnostic_report([sd], summary, None, out_dir)
        text = (out_dir / "diagnostic_report.md").read_text(encoding="utf-8")

        assert "Heart Rate Yield Zero" in text

    def test_test2_residual_title_when_primary_blocker_is_ahet(self, tmp_path):
        """test2 with primary_blocker=step6_ahet_ratio_low must use
        'Residual Respiration Pass-Through', not 'Respiration Pass-Through Failure'."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        sd = _make_session_dict(tmp_path, "test2", 5)
        summary = pd.DataFrame([
            _make_summary_row(
                "test2", "step6_ahet_ratio_low", 0.06,
                n_valid=2, n_windows=33,
            )
        ])
        _write_diagnostic_report([sd], summary, None, out_dir)
        text = (out_dir / "diagnostic_report.md").read_text(encoding="utf-8")

        assert "Residual Respiration Pass-Through" in text
        assert "Respiration Pass-Through Failure" not in text


# ============================================================================
# Candidate-yield CSV tracker context
# ============================================================================

class TestCandidateYieldTrackerColumns:
    def _make_cand(self, session_id: str, n_windows: int) -> pd.DataFrame:
        rows = []
        for wi in range(n_windows):
            rows.append({
                "session_id":          session_id,
                "window_index":        wi,
                "candidate_rank":      0,
                "candidate_attempted": True,
                "candidate_refined_hz": 80.0 / 60.0,
                "candidate_peak_magnitude": 1.0,
                "candidate_prominence":  2.0,
                "peak_to_floor_ratio_db": 3.0,
                "candidate_rejection_code": 0,
                "candidate_rejection_reason": "",
                "candidate_nearest_resp_harmonic_k": 6,
                "candidate_distance_to_nearest_resp_harmonic_bpm": 2.0,
                "is_accepted": True,
                "candidate_is_low": False,
            })
        return pd.DataFrame(rows)

    def test_tracker_context_columns_present_when_hw_has_them(self, tmp_path):
        """When heart_windows.csv has tracker columns, they appear in the
        step6_candidate_yield_diagnostics.csv output."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        sid = "s1"
        sd    = _make_session_dict(tmp_path, sid, 5, include_tracker=True)
        cand  = self._make_cand(sid, 5)
        result = _write_candidate_yield([sd], cand, out_dir)
        assert result is not None
        for col in ("hr_source", "tracker_decision_type", "pre_tracker_hr_valid",
                    "pre_tracker_invalid_reason", "tracker_fundamental_ratio_db",
                    "tracker_node_score"):
            assert col in result.columns, f"missing column: {col}"

    def test_tracker_context_absent_for_old_run(self, tmp_path):
        """When heart_windows.csv lacks tracker columns (old run), output CSV
        is still written without those columns and without crashing."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        sid = "s1"
        sd    = _make_session_dict(tmp_path, sid, 5, include_tracker=False)
        cand  = self._make_cand(sid, 5)
        result = _write_candidate_yield([sd], cand, out_dir)
        assert result is not None
        # Tracker columns must not appear since hw df didn't have them
        assert "tracker_decision_type" not in result.columns
