"""Tests for scripts/diagnose_step6_hr.py.

Run: pytest tests/test_diagnose_step6_hr.py -v
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.diagnose_step6_hr import (
    SWEEP_GRIDS,
    _NON_CANDIDATE_BLOCKED,
    _candidate_fail_reason,
    _classify_window,
    _load_session,
    _nearest_resp_harmonic,
    _parse_rank,
    _rejection_code_to_str,
    _replay_window_single,
    _to_bool,
    _to_float,
    _within_guard,
    build_aggregate_summary,
    build_markdown_report,
    compute_candidate_diagnostics,
    compute_top_peaks,
    compute_window_diagnostics,
    main,
    run_threshold_sweep,
)

# ---------------------------------------------------------------------------
# Constants matching Step 6 schema
# ---------------------------------------------------------------------------

FS            = 20.0
WINDOW_S      = 20.0
WINDOW_FRAMES = int(WINDOW_S * FS)   # 400
N_FFT         = WINDOW_FRAMES // 2 + 1  # 201
N_CAND        = 3
RR_HZ         = 15.0 / 60.0           # 0.25 Hz
HR_HZ         = 75.0 / 60.0           # 1.25 Hz
T0            = 1_780_000_000.0


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_freqs() -> np.ndarray:
    return np.fft.rfftfreq(WINDOW_FRAMES, d=1.0 / FS)


def _make_csv(n_windows: int = 10, hr_bpm: float = 75.0,
              masimo_bpm: float = 75.0, rr_bpm: float = 15.0,
              hr_valid: bool = True, invalid_reason: str = "",
              abs_err: float = 0.0) -> pd.DataFrame:
    rows = []
    for i in range(n_windows):
        rows.append({
            "session_id":           "synth",
            "window_index":         i,
            "start_epoch":          T0 + i * 5.0,
            "end_epoch":            T0 + i * 5.0 + WINDOW_S,
            "start_frame":          i * 100,
            "end_frame":            i * 100 + WINDOW_FRAMES,
            "locked_bin":           8,
            "locked_range_m":       1.09,
            "radar_hr_bpm":         hr_bpm if hr_valid else float("nan"),
            "heart_peak_hz":        hr_bpm / 60.0 if hr_valid else float("nan"),
            "hr_valid":             hr_valid,
            "hr_confidence":        "high" if hr_valid else "none",
            "invalid_reason":       invalid_reason,
            "quality_gated":        False,
            "n_bad_frames":         0,
            "bad_fraction":         0.0,
            "matched_rr_window_index": i,
            "resp_match_distance_s":   0.5,
            "radar_rr_bpm":         rr_bpm,
            "resp_peak_hz":         rr_bpm / 60.0,
            "resp_valid":           True,
            "resp_edge_locked":     False,
            "resp_edge_lock_side":  "none",
            "resp_confidence":      "high",
            "ahet_verified":        hr_valid,
            "harmonic_suspect":     False,
            "resp_harmonic_coincident": False,
            "heart_spectrum_stage": "after_ahet" if hr_valid else "skipped",
            "accepted_candidate_rank": 0 if hr_valid else -1,
            "accepted_candidate_refined_hz": hr_bpm / 60.0 if hr_valid else float("nan"),
            "accepted_second_harmonic_refined_hz": 2 * hr_bpm / 60.0 if hr_valid else float("nan"),
            "masimo_pr_bpm":        masimo_bpm,
            "masimo_pr_std_bpm":    0.5,
            "masimo_n_good_pi":     20,
            "masimo_n_total":       20,
            "masimo_low_quality":   False,
            "hr_error_bpm":         (hr_bpm - masimo_bpm) if hr_valid else float("nan"),
            "hr_abs_error_bpm":     abs_err if hr_valid else float("nan"),
            "baseline_hr_bpm":      hr_bpm,
            "locked_bin_dup":       8,
        })
    return pd.DataFrame(rows)


class _FakeNpz:
    """Minimal dict-backed stand-in for np.load output (allow_pickle=False)."""
    def __init__(self, d: dict):
        self._d = d
        self.files = list(d.keys())

    def __contains__(self, key):
        return key in self._d

    def get(self, key, default=None):
        return self._d.get(key, default)

    def __getitem__(self, key):
        return self._d[key]


def _make_npz(n_windows: int, freqs: np.ndarray) -> _FakeNpz:
    """Build a synthetic NPZ archive as a _FakeNpz (mimics np.load output).

    Rank 0: attempted, passed, rejection_code=0.
    Ranks 1-2: not attempted, rejection_code=5 (not_attempted).
    """
    rng = np.random.default_rng(42)
    phase = rng.standard_normal((n_windows, WINDOW_FRAMES))

    # Synthetic spectra — peak at HR_HZ
    spec = np.zeros((n_windows, N_FFT))
    spec[:, np.argmin(np.abs(freqs - HR_HZ))] = 10.0

    cand_attempted   = np.zeros((n_windows, N_CAND), dtype=bool)
    cand_attempted[:, 0] = True
    cand_initial     = np.full((n_windows, N_CAND), np.nan)
    cand_initial[:, 0] = HR_HZ
    cand_refined     = cand_initial.copy()
    cand_passed      = np.zeros((n_windows, N_CAND), dtype=bool)
    cand_passed[:, 0] = True
    cand_mag         = np.full((n_windows, N_CAND), np.nan)
    cand_mag[:, 0]   = 10.0
    cand_prom        = np.full((n_windows, N_CAND), np.nan)
    cand_prom[:, 0]  = 5.0
    floor            = np.full((n_windows, N_CAND), np.nan)
    floor[:, 0]      = 1.0
    ratio            = np.full((n_windows, N_CAND), np.nan)
    ratio[:, 0]      = 10.0
    ratio_db         = np.full((n_windows, N_CAND), np.nan)
    ratio_db[:, 0]   = 20.0
    second_ref       = np.full((n_windows, N_CAND), np.nan)
    second_ref[:, 0] = 2 * HR_HZ
    second_mag       = np.full((n_windows, N_CAND), np.nan)
    second_mag[:, 0] = 5.0
    region_avail     = np.zeros((n_windows, N_CAND), dtype=bool)
    region_avail[:, 0] = True
    ahet_spec = np.zeros((n_windows, N_CAND, N_FFT))
    ahet_spec[:, 0, np.argmin(np.abs(freqs - HR_HZ))] = 10.0

    # Step 6.1: rank 0 passed (code 0); ranks 1-2 not attempted (code 5)
    rej_codes = np.full((n_windows, N_CAND), 5, dtype=np.int32)
    rej_codes[:, 0] = 0

    return _FakeNpz({
        "heart_freqs_hz":           freqs,
        "phase_unwrapped":          phase,
        "phase_clean":              phase,
        "phase_eca":                phase,
        "heart_spectrum":           spec,
        "heart_spectrum_first_pass": spec,
        "baseline_spectrum":        spec * 0.5,
        "ahet_attempt_spectrum":    ahet_spec,
        "candidate_attempted":      cand_attempted,
        "candidate_initial_hz":     cand_initial,
        "candidate_refined_hz":     cand_refined,
        "candidate_peak_magnitude": cand_mag,
        "candidate_prominence":     cand_prom,
        "candidate_passed":         cand_passed,
        "second_peak_refined_hz":   second_ref,
        "second_peak_magnitude":    second_mag,
        "comparison_floor":         floor,
        "peak_to_floor_ratio":      ratio,
        "peak_to_floor_ratio_db":   ratio_db,
        "region_available":         region_avail,
        "candidate_rejection_code": rej_codes,   # Step 6.1
    })


def _write_session_files(tmp_path: Path, session_id: str,
                         n_windows: int = 10,
                         hr_bpm: float = 75.0,
                         masimo_bpm: float = 75.0,
                         abs_err: float = 0.0,
                         hr_valid: bool = True) -> Path:
    """Write synthetic Step 6 outputs to tmp_path/results/<session_id>/step_6/."""
    step6_dir = tmp_path / "results" / session_id / "step_6"
    step6_dir.mkdir(parents=True)

    freqs = _make_freqs()
    df    = _make_csv(n_windows, hr_bpm=hr_bpm, masimo_bpm=masimo_bpm,
                      abs_err=abs_err, hr_valid=hr_valid)
    df.to_csv(step6_dir / "heart_windows.csv", index=False)

    npz_data = _make_npz(n_windows, freqs)
    # Write a real npz file
    np.savez_compressed(step6_dir / "heart_intermediates.npz", **npz_data._d)

    summary = {"n_windows": n_windows, "n_valid_hr": n_windows if hr_valid else 0}
    (step6_dir / "summary.json").write_text(json.dumps(summary))
    return step6_dir


# ---------------------------------------------------------------------------
# Tests: helpers
# ---------------------------------------------------------------------------

class TestNearestRespHarmonic:
    def test_exact_harmonic(self):
        k, hz, dist_bpm = _nearest_resp_harmonic(4 * RR_HZ, RR_HZ)
        assert k == 4
        assert abs(dist_bpm) < 1e-6

    def test_near_harmonic(self):
        # 0.02 Hz above k=5 harmonic
        query = 5 * RR_HZ + 0.02
        k, hz, dist_bpm = _nearest_resp_harmonic(query, RR_HZ)
        assert k == 5
        assert abs(dist_bpm - 0.02 * 60.0) < 1e-4

    def test_nan_query_returns_sentinel(self):
        k, hz, d = _nearest_resp_harmonic(float("nan"), RR_HZ)
        assert k == -1
        assert not np.isfinite(hz)

    def test_nan_resp_returns_sentinel(self):
        k, hz, d = _nearest_resp_harmonic(HR_HZ, float("nan"))
        assert k == -1

    def test_zero_resp_returns_sentinel(self):
        k, hz, d = _nearest_resp_harmonic(HR_HZ, 0.0)
        assert k == -1


class TestWithinGuard:
    def test_on_harmonic_is_true(self):
        # HR at exactly k=5 of RR
        assert _within_guard(5 * RR_HZ, RR_HZ, 0.05) is True

    def test_within_guard_is_true(self):
        assert _within_guard(5 * RR_HZ + 0.03, RR_HZ, 0.05) is True

    def test_outside_guard_is_false(self):
        assert _within_guard(5 * RR_HZ + 0.10, RR_HZ, 0.05) is False

    def test_nan_query_is_false(self):
        assert _within_guard(float("nan"), RR_HZ, 0.05) is False

    def test_nan_resp_is_false(self):
        assert _within_guard(HR_HZ, float("nan"), 0.05) is False


class TestClassifyWindow:
    def test_good_valid(self):
        assert _classify_window(True, 2.0, "") == "good_valid"

    def test_bad_valid(self):
        assert _classify_window(True, 12.0, "") == "bad_valid"

    def test_severe_bad_valid(self):
        assert _classify_window(True, 18.0, "") == "severe_bad_valid"

    def test_ahet_failed(self):
        assert _classify_window(False, float("nan"), "ahet_failed") == "ahet_failed"

    def test_resp_harmonic_rejected(self):
        assert _classify_window(False, float("nan"), "resp_harmonic_coincident") \
               == "resp_harmonic_rejected"

    def test_resp_invalid_or_edge_locked(self):
        for rsn in ("resp_invalid", "resp_edge_locked", "resp_missing", "resp_match_too_far"):
            assert _classify_window(False, float("nan"), rsn) == "resp_invalid_or_edge_locked"

    def test_quality_gated(self):
        assert _classify_window(False, float("nan"), "quality_gated") == "quality_gated"


class TestParseRank:
    """Regression: rank 0 was converted to -1 because `int(0 or -1)` == -1."""

    def test_rank_zero_is_zero(self):
        assert _parse_rank(0) == 0

    def test_rank_zero_string_is_zero(self):
        assert _parse_rank("0") == 0

    def test_rank_zero_float_is_zero(self):
        assert _parse_rank(0.0) == 0

    def test_rank_one(self):
        assert _parse_rank(1) == 1

    def test_rank_two(self):
        assert _parse_rank(2) == 2

    def test_none_returns_minus_one(self):
        assert _parse_rank(None) == -1

    def test_nan_returns_minus_one(self):
        assert _parse_rank(float("nan")) == -1

    def test_nan_string_returns_minus_one(self):
        assert _parse_rank("nan") == -1

    def test_minus_one_passthrough(self):
        assert _parse_rank(-1) == -1

    def test_numpy_int_zero(self):
        import numpy as np
        assert _parse_rank(np.int64(0)) == 0

    def test_numpy_float_zero(self):
        import numpy as np
        assert _parse_rank(np.float64(0.0)) == 0


# ---------------------------------------------------------------------------
# Tests: compute functions
# ---------------------------------------------------------------------------

class TestComputeWindowDiagnostics:
    def test_basic_shape(self):
        freqs = _make_freqs()
        df    = _make_csv(n_windows=5)
        npz   = _make_npz(5, freqs)
        out   = compute_window_diagnostics("synth", df, npz)
        assert len(out) == 5
        assert "nearest_k_to_radar_hr" in out.columns
        assert "accepted_hr_within_resp_harmonic_guard" in out.columns
        assert "window_class" in out.columns

    def test_near_harmonic_flagged(self):
        # HR at k=5 * RR -> within_guard should be True
        hr_bpm = 5 * RR_HZ * 60.0  # = 75.0 bpm
        freqs  = _make_freqs()
        df     = _make_csv(n_windows=3, hr_bpm=hr_bpm, rr_bpm=RR_HZ * 60.0)
        npz    = _make_npz(3, freqs)
        out    = compute_window_diagnostics("synth", df, npz, resp_harmonic_guard_hz=0.05)
        # RR_HZ * 5 = 1.25 Hz = 75 bpm; guard=0.05 Hz; distance=0 -> within guard
        assert out["accepted_hr_within_resp_harmonic_guard"].astype(bool).all()

    def test_far_from_harmonic_not_flagged(self):
        # HR at 80 bpm, RR at 15 bpm (0.25 Hz); nearest harmonic: k=5 at 75, k=6 at 90
        # distance to k=5: 5 bpm = 0.083 Hz > 0.05 Hz -> not within guard
        freqs = _make_freqs()
        df    = _make_csv(n_windows=3, hr_bpm=80.0, rr_bpm=15.0)
        npz   = _make_npz(3, freqs)
        out   = compute_window_diagnostics("synth", df, npz, resp_harmonic_guard_hz=0.05)
        assert not out["accepted_hr_within_resp_harmonic_guard"].astype(bool).any()


class TestComputeCandidateDiagnostics:
    def test_n_rows_is_n_windows_times_3(self):
        n = 7
        freqs = _make_freqs()
        df    = _make_csv(n)
        npz   = _make_npz(n, freqs)
        cand_df = compute_candidate_diagnostics("synth", df, npz, freqs)
        assert len(cand_df) == n * N_CAND

    def test_accepted_candidate_rank_column(self):
        freqs = _make_freqs()
        df    = _make_csv(5)
        npz   = _make_npz(5, freqs)
        cand_df = compute_candidate_diagnostics("synth", df, npz, freqs)
        # Rank 0 is accepted in the synthetic CSV
        accepted = cand_df[cand_df["is_accepted"].astype(str).str.lower().isin(("true", "1"))]
        assert (accepted["candidate_rank"] == 0).all()

    def test_rank_zero_not_treated_as_missing(self):
        # Regression: `int(0 or -1)` converts accepted rank 0 to -1, marking
        # it as "not accepted". Verify rank-0 windows show is_accepted=True.
        freqs = _make_freqs()
        df    = _make_csv(3)   # synthetic CSV has accepted_candidate_rank=0
        npz   = _make_npz(3, freqs)
        cand_df = compute_candidate_diagnostics("synth", df, npz, freqs)
        # Every window has rank 0 accepted
        for wi in range(3):
            row = cand_df[(cand_df["window_index"] == wi) & (cand_df["candidate_rank"] == 0)]
            assert len(row) == 1
            assert str(row["is_accepted"].iloc[0]).lower() in ("true", "1"), \
                f"window {wi}: rank-0 candidate marked as not accepted"

    def test_three_ranks_per_window(self):
        freqs = _make_freqs()
        df    = _make_csv(4)
        npz   = _make_npz(4, freqs)
        cand_df = compute_candidate_diagnostics("synth", df, npz, freqs)
        for wi in range(4):
            sub = cand_df[cand_df["window_index"] == wi]
            assert set(sub["candidate_rank"]) == {0, 1, 2}


class TestComputeTopPeaks:
    def test_peaks_inside_band(self):
        freqs = _make_freqs()
        df    = _make_csv(5)
        npz   = _make_npz(5, freqs)
        band  = (0.8, 2.0)
        peaks = compute_top_peaks("synth", df, npz, freqs, heart_band_hz=band)
        assert len(peaks) > 0
        assert (peaks["peak_hz"] >= band[0]).all()
        assert (peaks["peak_hz"] <= band[1]).all()

    def test_accepted_candidate_spectrum_used_correctly(self):
        # ahet_attempt_spectrum[:, 0, :] should be accessible as spectrum_stage "after_ahet"
        # verify the accepted spectrum peak is in band
        freqs = _make_freqs()
        df    = _make_csv(3)
        npz   = _make_npz(3, freqs)
        peaks = compute_top_peaks("synth", df, npz, freqs)
        ahet_peaks = peaks[peaks["spectrum_stage"] == "after_ahet"]
        # Our synthetic spectrum has peak at HR_HZ which is in [0.8, 2.0]
        assert len(ahet_peaks) > 0
        assert (ahet_peaks["peak_hz"] >= 0.8).all()


# ---------------------------------------------------------------------------
# Tests: _load_session
# ---------------------------------------------------------------------------

class TestLoadSession:
    def test_missing_csv_returns_none(self, tmp_path):
        sid = "nosession"
        results = tmp_path / "results"
        # No files written
        result = _load_session(sid, results)
        assert result is None

    def test_missing_npz_returns_none(self, tmp_path):
        sid = "partial"
        step6 = tmp_path / "results" / sid / "step_6"
        step6.mkdir(parents=True)
        freqs = _make_freqs()
        df    = _make_csv(5)
        df.to_csv(step6 / "heart_windows.csv", index=False)
        (step6 / "summary.json").write_text("{}")
        # npz missing
        result = _load_session(sid, tmp_path / "results")
        assert result is None

    def test_csv_npz_mismatch_returns_none(self, tmp_path):
        sid = "mismatch"
        step6 = tmp_path / "results" / sid / "step_6"
        step6.mkdir(parents=True)
        freqs = _make_freqs()
        df    = _make_csv(5)
        df.to_csv(step6 / "heart_windows.csv", index=False)
        # Write NPZ with different n_windows
        npz_data = _make_npz(8, freqs)  # 8 != 5
        np.savez_compressed(step6 / "heart_intermediates.npz", **npz_data._d)
        (step6 / "summary.json").write_text("{}")
        result = _load_session(sid, tmp_path / "results")
        assert result is None

    def test_valid_session_loads(self, tmp_path):
        _write_session_files(tmp_path, "s1", n_windows=6)
        result = _load_session("s1", tmp_path / "results")
        assert result is not None
        assert result["n_windows"] == 6
        assert result["session_id"] == "s1"
        assert "freqs_hz" in result


# ---------------------------------------------------------------------------
# Tests: integration — main()
# ---------------------------------------------------------------------------

class TestMain:
    def test_produces_all_required_files(self, tmp_path):
        _write_session_files(tmp_path, "s1", n_windows=5, abs_err=2.0)
        _write_session_files(tmp_path, "s2", n_windows=5, abs_err=12.0)
        out = tmp_path / "diagnose"
        main([
            "--sessions", "s1", "s2",
            "--out", str(out),
            "--results-root", str(tmp_path / "results"),
            "--no-plots",
        ])
        step6_dir = out / "step6_hr"
        for fname in (
            "diagnostic.log", "summary.json", "summary.md",
            "per_window_diagnostics.csv", "candidate_diagnostics.csv",
            "top_peaks.csv", "bad_valid_windows.csv", "good_valid_windows.csv",
        ):
            assert (step6_dir / fname).exists(), f"Missing {fname}"
        assert (step6_dir / "plots").is_dir()

    def test_failed_session_does_not_abort(self, tmp_path):
        _write_session_files(tmp_path, "good", n_windows=4)
        out = tmp_path / "diagnose"
        rc = main([
            "--sessions", "good", "nosuchsession",
            "--out", str(out),
            "--results-root", str(tmp_path / "results"),
            "--no-plots",
        ])
        assert rc == 0
        summary = json.loads((out / "step6_hr" / "summary.json").read_text())
        assert "nosuchsession" in summary["sessions_failed"]
        assert "good" in summary["sessions_processed"]

    def test_bad_valid_csv_contains_bad_windows(self, tmp_path):
        # s_bad has abs_err=12.0 > BAD_THRESH (10.0)
        _write_session_files(tmp_path, "s_bad",  n_windows=5, abs_err=12.0)
        _write_session_files(tmp_path, "s_good", n_windows=5, abs_err=2.0)
        out = tmp_path / "diagnose"
        main([
            "--sessions", "s_bad", "s_good",
            "--out", str(out),
            "--results-root", str(tmp_path / "results"),
            "--no-plots",
        ])
        bad_df  = pd.read_csv(out / "step6_hr" / "bad_valid_windows.csv")
        good_df = pd.read_csv(out / "step6_hr" / "good_valid_windows.csv")
        assert (bad_df["session_id"] == "s_bad").all()
        assert (good_df["session_id"] == "s_good").all()

    def test_overwrite_flag_required_on_second_run(self, tmp_path):
        _write_session_files(tmp_path, "s1", n_windows=3)
        out = tmp_path / "diagnose"
        main(["--sessions", "s1", "--out", str(out),
              "--results-root", str(tmp_path / "results"), "--no-plots"])
        # Second run without --overwrite should fail
        rc = main(["--sessions", "s1", "--out", str(out),
                   "--results-root", str(tmp_path / "results"), "--no-plots"])
        assert rc == 1

    def test_candidate_diagnostics_has_n_windows_times_3_rows(self, tmp_path):
        n = 6
        _write_session_files(tmp_path, "s1", n_windows=n)
        out = tmp_path / "diagnose"
        main(["--sessions", "s1", "--out", str(out),
              "--results-root", str(tmp_path / "results"), "--no-plots"])
        cand_df = pd.read_csv(out / "step6_hr" / "candidate_diagnostics.csv")
        assert len(cand_df) == n * 3

    def test_no_output_outside_diagnose_dir(self, tmp_path):
        _write_session_files(tmp_path, "s1", n_windows=3)
        out      = tmp_path / "diagnose"
        results  = tmp_path / "results"
        results_before = set(results.rglob("*"))
        main(["--sessions", "s1", "--out", str(out),
              "--results-root", str(results), "--no-plots"])
        results_after = set(results.rglob("*"))
        new_in_results = results_after - results_before
        assert len(new_in_results) == 0, \
            f"Unexpected writes inside results/: {new_in_results}"

    def test_accepted_hr_within_guard_correct_for_near_harmonic(self, tmp_path):
        # HR at 5 * RR (exact harmonic) -> within_guard must be True
        rr_bpm  = RR_HZ * 60.0  # 15 bpm
        hr_bpm  = 5 * RR_HZ * 60.0  # 75 bpm
        _write_session_files(tmp_path, "nearh", n_windows=4,
                             hr_bpm=hr_bpm, masimo_bpm=hr_bpm, abs_err=0.0)
        out = tmp_path / "diagnose"
        main([
            "--sessions", "nearh",
            "--out", str(out),
            "--results-root", str(tmp_path / "results"),
            "--resp-harmonic-guard-hz", "0.05",
            "--no-plots",
        ])
        diag = pd.read_csv(out / "step6_hr" / "per_window_diagnostics.csv")
        valid_rows = diag[diag["hr_valid"].astype(str).str.lower().isin(("true", "1"))]
        assert valid_rows["accepted_hr_within_resp_harmonic_guard"].astype(
            str).str.lower().isin(("true", "1")).all()


# ---------------------------------------------------------------------------
# Tests: rejection-code mapping
# ---------------------------------------------------------------------------

class TestRejectionCodeToStr:
    def test_known_codes(self):
        expected = {
            -1: "gate_not_run",
            0:  "passed",
            1:  "no_second_harmonic_region",
            2:  "ratio_db_low",
            3:  "prominence_low",
            4:  "low_candidate_competitor",
            5:  "not_attempted",
            6:  "peak_to_floor_db_low",
            7:  "low_candidate_floor_db_low",
        }
        for code, reason in expected.items():
            assert _rejection_code_to_str(code) == reason, f"code {code}"

    def test_unknown_code_returns_sentinel(self):
        assert _rejection_code_to_str(99) == "unknown_code_99"

    def test_float_code_coerced(self):
        assert _rejection_code_to_str(0.0) == "passed"
        assert _rejection_code_to_str(2.0) == "ratio_db_low"


# ---------------------------------------------------------------------------
# Tests: Step 6.1 candidate fields in compute_candidate_diagnostics
# ---------------------------------------------------------------------------

class TestCandidateDiagnosticsStep61:
    def test_new_columns_present(self):
        freqs = _make_freqs()
        df    = _make_csv(4)
        npz   = _make_npz(4, freqs)
        cand_df = compute_candidate_diagnostics("synth", df, npz, freqs)
        for col in (
            "candidate_rejection_code", "candidate_rejection_reason",
            "candidate_is_low", "candidate_within_resp_harmonic_guard",
            "candidate_nearest_resp_harmonic_k",
            "candidate_distance_to_nearest_resp_harmonic_bpm",
            "candidate_selected_by_strict_gate",
        ):
            assert col in cand_df.columns, f"missing column: {col}"

    def test_shape_invariant_with_new_columns(self):
        n = 5
        freqs = _make_freqs()
        df    = _make_csv(n)
        npz   = _make_npz(n, freqs)
        cand_df = compute_candidate_diagnostics("synth", df, npz, freqs)
        assert len(cand_df) == n * N_CAND

    def test_rejection_code_and_reason_from_npz(self):
        # Rank 0 code=0 "passed"; ranks 1,2 code=5 "not_attempted"
        freqs = _make_freqs()
        df    = _make_csv(3)
        npz   = _make_npz(3, freqs)
        cand_df = compute_candidate_diagnostics("synth", df, npz, freqs)
        rank0  = cand_df[cand_df["candidate_rank"] == 0]
        rank12 = cand_df[cand_df["candidate_rank"].isin([1, 2])]
        assert (rank0["candidate_rejection_code"] == 0).all()
        assert (rank0["candidate_rejection_reason"] == "passed").all()
        assert (rank12["candidate_rejection_code"] == 5).all()
        assert (rank12["candidate_rejection_reason"] == "not_attempted").all()

    def test_backward_compat_no_rejection_code_in_npz(self):
        """Old NPZ without candidate_rejection_code → code -1, reason gate_not_run."""
        freqs = _make_freqs()
        df    = _make_csv(3)
        npz   = _make_npz(3, freqs)
        # Strip the Step 6.1 field to simulate an old NPZ
        d_old = {k: v for k, v in npz._d.items() if k != "candidate_rejection_code"}
        npz_old = _FakeNpz(d_old)
        cand_df = compute_candidate_diagnostics("synth", df, npz_old, freqs)
        assert (cand_df["candidate_rejection_code"] == -1).all()
        assert (cand_df["candidate_rejection_reason"] == "gate_not_run").all()

    def test_candidate_on_resp_harmonic_within_guard(self):
        # RR = 15 bpm (0.25 Hz); rank-0 candidate at 5 × 0.25 = 1.25 Hz = HR_HZ → within guard
        freqs = _make_freqs()
        df    = _make_csv(3, rr_bpm=15.0)
        npz   = _make_npz(3, freqs)  # candidate_refined[:, 0] = HR_HZ = 5 × RR_HZ
        cand_df = compute_candidate_diagnostics("synth", df, npz, freqs, resp_harmonic_guard_hz=0.05)
        rank0 = cand_df[cand_df["candidate_rank"] == 0]
        assert rank0["candidate_within_resp_harmonic_guard"].astype(bool).all()

    def test_candidate_outside_resp_harmonic_guard(self):
        # RR = 15 bpm; candidate at 80/60 Hz; nearest harmonic k=5 at 75 bpm → 5 bpm = 0.083 Hz > 0.05
        freqs = _make_freqs()
        df    = _make_csv(3, rr_bpm=15.0)
        npz   = _make_npz(3, freqs)
        d = dict(npz._d)
        d["candidate_refined_hz"] = np.full((3, N_CAND), np.nan)
        d["candidate_refined_hz"][:, 0] = 80.0 / 60.0
        npz2 = _FakeNpz(d)
        cand_df = compute_candidate_diagnostics("synth", df, npz2, freqs, resp_harmonic_guard_hz=0.05)
        rank0 = cand_df[cand_df["candidate_rank"] == 0]
        assert not rank0["candidate_within_resp_harmonic_guard"].astype(bool).any()

    def test_strict_gate_true_only_for_passed_code0(self):
        # Rank 0: passed=True, code=0 → selected_by_strict_gate=True
        # Ranks 1,2: passed=False, code=5 → selected_by_strict_gate=False
        freqs = _make_freqs()
        df    = _make_csv(3)
        npz   = _make_npz(3, freqs)
        cand_df = compute_candidate_diagnostics("synth", df, npz, freqs)
        rank0  = cand_df[cand_df["candidate_rank"] == 0]
        rank12 = cand_df[cand_df["candidate_rank"].isin([1, 2])]
        assert rank0["candidate_selected_by_strict_gate"].astype(bool).all()
        assert not rank12["candidate_selected_by_strict_gate"].astype(bool).any()

    def test_strict_gate_false_when_passed_but_code_nonzero(self):
        # Candidate passed=True but code=2 (ratio_db_low) → not selected
        freqs = _make_freqs()
        df    = _make_csv(2)
        npz   = _make_npz(2, freqs)
        d = dict(npz._d)
        rej = d["candidate_rejection_code"].copy()
        rej[:, 0] = 2  # override rank-0 to code 2 (ratio_db_low)
        d["candidate_rejection_code"] = rej
        npz2 = _FakeNpz(d)
        cand_df = compute_candidate_diagnostics("synth", df, npz2, freqs)
        rank0 = cand_df[cand_df["candidate_rank"] == 0]
        assert not rank0["candidate_selected_by_strict_gate"].astype(bool).any()

    def test_candidate_is_low_below_threshold(self):
        # Candidate refined at 1.10 Hz (66 bpm) < LOW_CANDIDATE_HZ (1.20) → is_low=True
        freqs = _make_freqs()
        df    = _make_csv(3)
        npz   = _make_npz(3, freqs)
        d = dict(npz._d)
        d["candidate_refined_hz"] = np.full((3, N_CAND), np.nan)
        d["candidate_refined_hz"][:, 0] = 1.10
        npz2 = _FakeNpz(d)
        cand_df = compute_candidate_diagnostics("synth", df, npz2, freqs)
        rank0 = cand_df[cand_df["candidate_rank"] == 0]
        assert rank0["candidate_is_low"].astype(bool).all()

    def test_candidate_is_low_at_threshold_false(self):
        # HR_HZ = 1.25 Hz >= 1.20 → not low
        freqs = _make_freqs()
        df    = _make_csv(3)
        npz   = _make_npz(3, freqs)  # candidate_refined[:, 0] = HR_HZ = 1.25
        cand_df = compute_candidate_diagnostics("synth", df, npz, freqs)
        rank0 = cand_df[cand_df["candidate_rank"] == 0]
        assert not rank0["candidate_is_low"].astype(bool).any()


# ---------------------------------------------------------------------------
# Tests: Step 6.1 fields appear in integration output (candidate_diagnostics.csv)
# ---------------------------------------------------------------------------

class TestMainStep61CandidateColumns:
    def test_candidate_diagnostics_has_step61_columns(self, tmp_path):
        _write_session_files(tmp_path, "s1", n_windows=4)
        out = tmp_path / "diagnose"
        main(["--sessions", "s1", "--out", str(out),
              "--results-root", str(tmp_path / "results"), "--no-plots"])
        cand_df = pd.read_csv(out / "step6_hr" / "candidate_diagnostics.csv")
        for col in (
            "candidate_rejection_code", "candidate_rejection_reason",
            "candidate_is_low", "candidate_within_resp_harmonic_guard",
            "candidate_nearest_resp_harmonic_k",
            "candidate_distance_to_nearest_resp_harmonic_bpm",
            "candidate_selected_by_strict_gate",
        ):
            assert col in cand_df.columns, f"missing column in CSV: {col}"
        # Shape invariant preserved
        assert len(cand_df) == 4 * N_CAND


# ---------------------------------------------------------------------------
# Helpers for Phase 2 tests
# ---------------------------------------------------------------------------

def _make_cand_row(
    *,
    session_id: str = "s",
    window_index: int = 0,
    candidate_rank: int = 0,
    attempted: bool = True,
    ratio_db: float = 10.0,
    prominence: float = 5.0,
    is_low: bool = False,
    rej_reason: str = "passed",
    refined_hz: float = HR_HZ,
) -> dict:
    return {
        "session_id":               session_id,
        "window_index":             window_index,
        "candidate_rank":           candidate_rank,
        "candidate_attempted":      float(attempted),
        "peak_to_floor_ratio_db":   ratio_db,
        "candidate_prominence":     prominence,
        "candidate_is_low":         is_low,
        "candidate_rejection_reason": rej_reason,
        "candidate_refined_hz":     refined_hz,
    }


def _make_cand_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _make_diag_row(
    *,
    session_id: str = "s",
    window_index: int = 0,
    masimo_bpm: float = 75.0,
    masimo_low: bool = False,
    window_class: str = "good_valid",
) -> dict:
    return {
        "session_id":       session_id,
        "window_index":     window_index,
        "masimo_pr_bpm":    masimo_bpm,
        "masimo_low_quality": masimo_low,
        "window_class":     window_class,
        "hr_valid":         window_class in ("good_valid", "acceptable_valid",
                                              "bad_valid", "severe_bad_valid"),
        "hr_abs_error_bpm": abs(HR_HZ * 60.0 - masimo_bpm) if not masimo_low else float("nan"),
    }


EXPECTED_N_COMBOS = (
    len(SWEEP_GRIDS["min_second_harmonic_ratio_db"])
    * len(SWEEP_GRIDS["min_candidate_prominence"])
    * len(SWEEP_GRIDS["min_peak_to_floor_db"])
    * len(SWEEP_GRIDS["low_candidate_min_peak_to_floor_db"])
)  # 6 × 1 × 6 × 5 = 180


# ---------------------------------------------------------------------------
# Tests: _to_float / _to_bool helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_to_float_normal(self):
        assert _to_float(3.5) == 3.5

    def test_to_float_none_returns_nan(self):
        assert not math.isfinite(_to_float(None))

    def test_to_float_string_number(self):
        assert _to_float("2.5") == 2.5

    def test_to_bool_true(self):
        assert _to_bool(True) is True
        assert _to_bool(1)    is True
        assert _to_bool(1.0)  is True
        assert _to_bool("true") is True

    def test_to_bool_false(self):
        assert _to_bool(False) is False
        assert _to_bool(0)     is False
        assert _to_bool(0.0)   is False
        assert _to_bool("false") is False

    def test_to_bool_nan_is_false(self):
        assert _to_bool(float("nan")) is False


# ---------------------------------------------------------------------------
# Tests: _candidate_fail_reason
# ---------------------------------------------------------------------------

class TestCandidateFailReason:
    def _good(self):
        return _make_cand_row(ratio_db=10.0, prominence=5.0, is_low=False, rej_reason="passed")

    def test_passes_all_gates(self):
        c = self._good()
        assert _candidate_fail_reason(c, 1.0, 3.0, 0.0, 0.0) == ""

    def test_fails_ratio_db_low(self):
        c = _make_cand_row(ratio_db=0.5)
        assert _candidate_fail_reason(c, 1.0, 3.0, 0.0, 0.0) == "ratio_db_low"

    def test_fails_nan_ratio_db(self):
        c = _make_cand_row(ratio_db=float("nan"))
        assert _candidate_fail_reason(c, 1.0, 3.0, 0.0, 0.0) == "ratio_db_low"

    def test_fails_prominence_low(self):
        c = _make_cand_row(ratio_db=10.0, prominence=1.0)
        assert _candidate_fail_reason(c, 1.0, 3.0, 0.0, 0.0) == "prominence_low"

    def test_fails_nan_prominence(self):
        c = _make_cand_row(ratio_db=10.0, prominence=float("nan"))
        assert _candidate_fail_reason(c, 1.0, 3.0, 0.0, 0.0) == "prominence_low"

    def test_fails_low_candidate_competitor(self):
        c = _make_cand_row(ratio_db=10.0, prominence=5.0, rej_reason="low_candidate_competitor")
        assert _candidate_fail_reason(c, 1.0, 3.0, 0.0, 0.0) == "low_candidate_competitor"

    def test_fails_peak_to_floor_db(self):
        # ratio_db=2.5 passes gate 1 (min_ratio=1.0) but fails gate 4 (min_floor=3.0)
        c = _make_cand_row(ratio_db=2.5, prominence=5.0)
        assert _candidate_fail_reason(c, 1.0, 3.0, 3.0, 0.0) == "peak_to_floor_db_low"

    def test_fails_low_candidate_floor(self):
        # is_low=True, ratio_db=2.5 passes gates 1,4 (min_floor=0.0) but fails gate 5
        c = _make_cand_row(ratio_db=2.5, prominence=5.0, is_low=True)
        assert _candidate_fail_reason(c, 1.0, 3.0, 0.0, 4.0) == "low_candidate_floor_db_low"

    def test_non_low_candidate_skips_gate5(self):
        # is_low=False — gate 5 never triggers
        c = _make_cand_row(ratio_db=2.5, prominence=5.0, is_low=False)
        assert _candidate_fail_reason(c, 1.0, 3.0, 0.0, 6.0) == ""


# ---------------------------------------------------------------------------
# Tests: _replay_window_single
# ---------------------------------------------------------------------------

class TestReplayWindowSingle:
    def test_rank0_passes(self):
        rows = [_make_cand_row(candidate_rank=0, ratio_db=10.0, prominence=5.0)]
        rank, hz, reason = _replay_window_single(rows, 1.0, 3.0, 0.0, 0.0)
        assert rank == 0
        assert math.isfinite(hz)
        assert reason == ""

    def test_rank0_fails_rank1_passes(self):
        rows = [
            _make_cand_row(candidate_rank=0, ratio_db=0.5),          # fails gate 1
            _make_cand_row(candidate_rank=1, ratio_db=10.0, prominence=5.0),
        ]
        rank, hz, reason = _replay_window_single(rows, 1.0, 3.0, 0.0, 0.0)
        assert rank == 1
        assert math.isfinite(hz)
        assert reason == ""

    def test_all_fail_returns_nan(self):
        rows = [
            _make_cand_row(candidate_rank=0, ratio_db=0.0),
            _make_cand_row(candidate_rank=1, ratio_db=0.0),
            _make_cand_row(candidate_rank=2, ratio_db=0.0),
        ]
        rank, hz, reason = _replay_window_single(rows, 1.0, 3.0, 0.0, 0.0)
        assert rank == -1
        assert not math.isfinite(hz)
        assert reason == "ratio_db_low"

    def test_not_attempted_skipped(self):
        rows = [
            _make_cand_row(candidate_rank=0, attempted=False),
            _make_cand_row(candidate_rank=1, ratio_db=10.0, prominence=5.0),
        ]
        rank, hz, reason = _replay_window_single(rows, 1.0, 3.0, 0.0, 0.0)
        assert rank == 1

    def test_all_not_attempted(self):
        rows = [_make_cand_row(candidate_rank=i, attempted=False) for i in range(3)]
        rank, hz, reason = _replay_window_single(rows, 1.0, 3.0, 0.0, 0.0)
        assert rank == -1
        assert reason == "no_attempted_candidates"

    def test_peak_to_floor_gate_binds(self):
        # ratio_db=2.5 passes gate1 (min=1.0) but fails gate4 (min_floor=3.0)
        rows = [_make_cand_row(candidate_rank=0, ratio_db=2.5, prominence=5.0)]
        rank, hz, reason = _replay_window_single(rows, 1.0, 3.0, 3.0, 0.0)
        assert rank == -1
        assert reason == "peak_to_floor_db_low"

    def test_low_candidate_floor_gate_binds(self):
        rows = [_make_cand_row(candidate_rank=0, ratio_db=2.5, prominence=5.0,
                               is_low=True, rej_reason="passed")]
        rank, hz, reason = _replay_window_single(rows, 1.0, 3.0, 0.0, 4.0)
        assert rank == -1
        assert reason == "low_candidate_floor_db_low"

    def test_masimo_not_used_in_candidate_selection(self):
        """Replay result must not depend on Masimo data (which is in diag_df, not cand_df)."""
        rows = [_make_cand_row(candidate_rank=0, ratio_db=10.0, prominence=5.0,
                               refined_hz=HR_HZ)]
        r1, hz1, _ = _replay_window_single(rows, 1.0, 3.0, 0.0, 0.0)
        # No masimo info is passed — always the same result regardless of masimo_bpm
        r2, hz2, _ = _replay_window_single(rows, 1.0, 3.0, 0.0, 0.0)
        assert r1 == r2 and hz1 == hz2


# ---------------------------------------------------------------------------
# Tests: run_threshold_sweep
# ---------------------------------------------------------------------------

class TestRunThresholdSweep:
    def _one_window_data(self, ratio_db=10.0, prominence=5.0, is_low=False,
                          masimo_bpm=75.0, masimo_low=False, window_class="good_valid"):
        """Single window, rank-0 only (attempted), with specified properties."""
        cand_rows = [
            _make_cand_row(session_id="s", window_index=0, candidate_rank=0,
                           ratio_db=ratio_db, prominence=prominence, is_low=is_low),
            _make_cand_row(session_id="s", window_index=0, candidate_rank=1,
                           attempted=False, ratio_db=float("nan"), prominence=float("nan")),
            _make_cand_row(session_id="s", window_index=0, candidate_rank=2,
                           attempted=False, ratio_db=float("nan"), prominence=float("nan")),
        ]
        diag_rows = [_make_diag_row(session_id="s", window_index=0,
                                    masimo_bpm=masimo_bpm, masimo_low=masimo_low,
                                    window_class=window_class)]
        return _make_cand_df(cand_rows), pd.DataFrame(diag_rows)

    def test_sweep_row_count(self):
        cand_df, diag_df = self._one_window_data()
        sweep_df, _ = run_threshold_sweep(cand_df, diag_df)
        assert len(sweep_df) == EXPECTED_N_COMBOS

    def test_window_decisions_row_count(self):
        cand_df, diag_df = self._one_window_data()
        _, decisions_df = run_threshold_sweep(cand_df, diag_df)
        assert len(decisions_df) == EXPECTED_N_COMBOS * 1  # 1 window

    def test_sweep_sorted_by_pipeline_score(self):
        cand_df, diag_df = self._one_window_data()
        sweep_df, _ = run_threshold_sweep(cand_df, diag_df)
        scores = sweep_df["pipeline_score"].tolist()
        assert scores == sorted(scores)

    def test_masimo_not_used_for_candidate_selection(self):
        """Changing masimo_pr_bpm must not change replay_accepted_candidate_rank."""
        cand_rows = [
            _make_cand_row(session_id="s", window_index=0, candidate_rank=0,
                           ratio_db=10.0, prominence=5.0, refined_hz=HR_HZ),
            _make_cand_row(session_id="s", window_index=0, candidate_rank=1,
                           attempted=False, ratio_db=float("nan"), prominence=float("nan")),
            _make_cand_row(session_id="s", window_index=0, candidate_rank=2,
                           attempted=False, ratio_db=float("nan"), prominence=float("nan")),
        ]
        cand_df = _make_cand_df(cand_rows)

        diag_a = pd.DataFrame([_make_diag_row(masimo_bpm=70.0)])
        diag_b = pd.DataFrame([_make_diag_row(masimo_bpm=90.0)])

        _, dec_a = run_threshold_sweep(cand_df, diag_a)
        _, dec_b = run_threshold_sweep(cand_df, diag_b)

        # Accepted rank and replay HR bpm must be identical
        assert (dec_a["replay_accepted_candidate_rank"].values ==
                dec_b["replay_accepted_candidate_rank"].values).all()
        np.testing.assert_array_equal(
            dec_a["replay_radar_hr_bpm"].values,
            dec_b["replay_radar_hr_bpm"].values,
        )

    def test_bad_window_rejected_by_high_floor_threshold(self):
        """Candidate with low ratio_db should be rejected when min_peak_to_floor_db is raised."""
        # ratio_db=2.0 — passes low thresholds but fails when floor=3.0
        cand_df, diag_df = self._one_window_data(ratio_db=2.0, window_class="bad_valid")
        sweep_df, decisions_df = run_threshold_sweep(cand_df, diag_df)

        # When min_peak_to_floor_db=3.0 AND min_second_harmonic_ratio_db<=2.0:
        # ratio_db=2.0 passes gate1 (ratio<=2.0) but fails gate4 (floor=3.0)
        tight = decisions_df[
            (decisions_df["min_second_harmonic_ratio_db"] <= 2.0) &
            (decisions_df["min_peak_to_floor_db"] >= 3.0)
        ]
        assert (tight["replay_hr_valid"] == False).all()  # noqa: E712

    def test_low_quality_masimo_in_decisions_not_metrics(self):
        """Masimo-low window appears in decisions CSV but abs_err must be NaN."""
        cand_df, diag_df = self._one_window_data(masimo_bpm=75.0, masimo_low=True,
                                                   window_class="good_valid")
        _, decisions_df = run_threshold_sweep(cand_df, diag_df)
        assert len(decisions_df) == EXPECTED_N_COMBOS  # window IS in the CSV
        # abs_err must be NaN for masimo-low windows
        assert decisions_df["replay_hr_abs_error_bpm"].isna().all()
        # And metrics must reflect this: n_good_valid_replay = 0 for all combos
        sweep_df, _ = run_threshold_sweep(cand_df, diag_df)
        assert (sweep_df["n_good_valid_replay"] == 0).all()

    def test_required_sweep_columns_present(self):
        cand_df, diag_df = self._one_window_data()
        sweep_df, decisions_df = run_threshold_sweep(cand_df, diag_df)
        for col in (
            "min_second_harmonic_ratio_db", "min_candidate_prominence",
            "min_peak_to_floor_db", "low_candidate_min_peak_to_floor_db",
            # Candidate-only metrics
            "n_valid_replay", "n_paired_replay",
            "n_good_valid_replay", "n_acceptable_valid_replay",
            "n_bad_valid_replay", "n_severe_bad_valid_replay",
            "mae_bpm", "rmse_bpm", "bias_bpm",
            "lost_current_good_windows", "rejected_current_bad_windows", "score",
            # Pipeline-realistic metrics
            "pipeline_n_valid_replay", "pipeline_n_paired_replay",
            "pipeline_n_good_valid_replay", "pipeline_n_acceptable_valid_replay",
            "pipeline_n_bad_valid_replay", "pipeline_n_severe_bad_valid_replay",
            "pipeline_mae_bpm", "pipeline_rmse_bpm", "pipeline_bias_bpm",
            "pipeline_lost_current_good_windows", "pipeline_rejected_current_bad_windows",
            "pipeline_score",
        ):
            assert col in sweep_df.columns, f"missing sweep column: {col}"
        for col in (
            "session_id", "window_index",
            "min_second_harmonic_ratio_db", "min_candidate_prominence",
            "min_peak_to_floor_db", "low_candidate_min_peak_to_floor_db",
            # Candidate-only columns
            "replay_hr_valid", "replay_radar_hr_bpm", "replay_accepted_candidate_rank",
            "replay_rejection_reason",
            "replay_hr_error_bpm", "replay_hr_abs_error_bpm", "replay_window_class",
            # Pipeline-realistic columns
            "pipeline_replay_valid", "pipeline_replay_hr_error_bpm",
            "pipeline_replay_hr_abs_error_bpm", "pipeline_replay_window_class",
            # Shared
            "masimo_pr_bpm", "masimo_low_quality", "current_window_class",
        ):
            assert col in decisions_df.columns, f"missing decisions column: {col}"

    def test_non_candidate_blocked_class_is_pipeline_invalid(self):
        """A window whose current Step 6 class is resp_harmonic_rejected must have
        pipeline_replay_valid=False even when the candidate passes all threshold gates."""
        for blocked_class in _NON_CANDIDATE_BLOCKED:
            cand_df, diag_df = self._one_window_data(
                ratio_db=10.0, prominence=5.0, window_class=blocked_class,
            )
            _, decisions_df = run_threshold_sweep(cand_df, diag_df)
            # replay_hr_valid should be True (candidate passes), but pipeline must be False
            assert decisions_df["replay_hr_valid"].all(), \
                f"candidate should pass for blocked_class={blocked_class}"
            assert (~decisions_df["pipeline_replay_valid"]).all(), \
                f"pipeline should be blocked for window_class={blocked_class}"
            assert (decisions_df["pipeline_replay_window_class"] == "pipeline_blocked").all(), \
                f"pipeline_wc should be 'pipeline_blocked' for {blocked_class}"

    def test_good_valid_window_not_blocked_by_pipeline(self):
        """A window_class=good_valid candidate that passes thresholds must also be
        pipeline_replay_valid=True — good_valid is not in _NON_CANDIDATE_BLOCKED."""
        cand_df, diag_df = self._one_window_data(
            ratio_db=10.0, prominence=5.0, window_class="good_valid",
        )
        _, decisions_df = run_threshold_sweep(cand_df, diag_df)
        # For combos where the candidate passes all gates, pipeline should also be valid
        passing = decisions_df[decisions_df["replay_hr_valid"]]
        assert (passing["pipeline_replay_valid"]).all()

    def test_pipeline_metrics_exclude_blocked_windows(self):
        """pipeline_n_good_valid_replay must be lower than n_good_valid_replay when a
        good-error-but-blocked window (resp_harmonic_rejected) is present."""
        # Window A: good_valid, small error → counts toward both candidate and pipeline good
        cand_rows_a = [
            _make_cand_row(session_id="s", window_index=0, candidate_rank=0,
                           ratio_db=10.0, prominence=5.0, refined_hz=HR_HZ),
            _make_cand_row(session_id="s", window_index=0, candidate_rank=1,
                           attempted=False, ratio_db=float("nan"), prominence=float("nan")),
            _make_cand_row(session_id="s", window_index=0, candidate_rank=2,
                           attempted=False, ratio_db=float("nan"), prominence=float("nan")),
        ]
        diag_a = _make_diag_row(session_id="s", window_index=0,
                                masimo_bpm=HR_HZ * 60.0 + 1.0, window_class="good_valid")
        # Window B: resp_harmonic_rejected, small error → candidate good, pipeline blocked
        cand_rows_b = [
            _make_cand_row(session_id="s", window_index=1, candidate_rank=0,
                           ratio_db=10.0, prominence=5.0, refined_hz=HR_HZ),
            _make_cand_row(session_id="s", window_index=1, candidate_rank=1,
                           attempted=False, ratio_db=float("nan"), prominence=float("nan")),
            _make_cand_row(session_id="s", window_index=1, candidate_rank=2,
                           attempted=False, ratio_db=float("nan"), prominence=float("nan")),
        ]
        diag_b = _make_diag_row(session_id="s", window_index=1,
                                masimo_bpm=HR_HZ * 60.0 + 1.0,
                                window_class="resp_harmonic_rejected")
        cand_df = _make_cand_df(cand_rows_a + cand_rows_b)
        diag_df = pd.DataFrame([diag_a, diag_b])
        sweep_df, _ = run_threshold_sweep(cand_df, diag_df)
        # For combos where the ratio_db threshold is low (both candidates pass):
        low_thresh = sweep_df[sweep_df["min_second_harmonic_ratio_db"] == 1.0]
        # candidate counts both windows as good, pipeline counts only window A
        assert (low_thresh["n_good_valid_replay"] > low_thresh["pipeline_n_good_valid_replay"]).all()

    def test_rank_fallthrough_in_sweep(self):
        """When rank 0 fails and rank 1 passes, decisions must show accepted_rank=1."""
        cand_rows = [
            # rank 0: ratio_db=0.5 — fails gate1 for any min_ratio>=1.0
            _make_cand_row(session_id="s", window_index=0, candidate_rank=0, ratio_db=0.5),
            # rank 1: ratio_db=10.0, prominence=5.0 — passes all gates
            _make_cand_row(session_id="s", window_index=0, candidate_rank=1,
                           ratio_db=10.0, prominence=5.0, refined_hz=HR_HZ),
            _make_cand_row(session_id="s", window_index=0, candidate_rank=2,
                           attempted=False, ratio_db=float("nan"), prominence=float("nan")),
        ]
        cand_df  = _make_cand_df(cand_rows)
        diag_df  = pd.DataFrame([_make_diag_row(masimo_bpm=75.0)])
        _, decisions_df = run_threshold_sweep(cand_df, diag_df)

        # For combos where min_ratio=1.0 (lowest value in grid):
        # rank 0 fails (0.5 < 1.0), rank 1 passes → accepted_rank=1
        low_ratio = decisions_df[decisions_df["min_second_harmonic_ratio_db"] == 1.0]
        assert (low_ratio["replay_accepted_candidate_rank"] == 1).all()


# ---------------------------------------------------------------------------
# Tests: Phase 2 integration via main()
# ---------------------------------------------------------------------------

class TestMainPhase2:
    def test_produces_sweep_files(self, tmp_path):
        _write_session_files(tmp_path, "s1", n_windows=5, abs_err=2.0)
        out = tmp_path / "diagnose"
        main(["--sessions", "s1", "--out", str(out),
              "--results-root", str(tmp_path / "results"), "--no-plots"])
        step6_dir = out / "step6_hr"
        assert (step6_dir / "threshold_sweep.csv").exists()
        assert (step6_dir / "threshold_sweep_window_decisions.csv").exists()

    def test_sweep_has_correct_row_count(self, tmp_path):
        _write_session_files(tmp_path, "s1", n_windows=3)
        out = tmp_path / "diagnose"
        main(["--sessions", "s1", "--out", str(out),
              "--results-root", str(tmp_path / "results"), "--no-plots"])
        sweep_df = pd.read_csv(out / "step6_hr" / "threshold_sweep.csv")
        assert len(sweep_df) == EXPECTED_N_COMBOS

    def test_decisions_row_count_matches_windows_times_combos(self, tmp_path):
        n = 4
        _write_session_files(tmp_path, "s1", n_windows=n)
        out = tmp_path / "diagnose"
        main(["--sessions", "s1", "--out", str(out),
              "--results-root", str(tmp_path / "results"), "--no-plots"])
        dec_df = pd.read_csv(out / "step6_hr" / "threshold_sweep_window_decisions.csv")
        assert len(dec_df) == n * EXPECTED_N_COMBOS

    def test_sweep_files_inside_diagnose_dir(self, tmp_path):
        _write_session_files(tmp_path, "s1", n_windows=3)
        results_root = tmp_path / "results"
        before       = set(results_root.rglob("*"))
        out          = tmp_path / "diagnose"
        main(["--sessions", "s1", "--out", str(out),
              "--results-root", str(results_root), "--no-plots"])
        after = set(results_root.rglob("*"))
        assert after == before, f"Unexpected writes inside results/: {after - before}"

    def test_produces_all_required_files_phase2(self, tmp_path):
        _write_session_files(tmp_path, "s1", n_windows=4, abs_err=2.0)
        out = tmp_path / "diagnose"
        main(["--sessions", "s1", "--out", str(out),
              "--results-root", str(tmp_path / "results"), "--no-plots"])
        step6_dir = out / "step6_hr"
        for fname in (
            "diagnostic.log", "summary.json", "summary.md",
            "per_window_diagnostics.csv", "candidate_diagnostics.csv",
            "top_peaks.csv", "bad_valid_windows.csv", "good_valid_windows.csv",
            "threshold_sweep.csv", "threshold_sweep_window_decisions.csv",
        ):
            assert (step6_dir / fname).exists(), f"Missing: {fname}"


# ---------------------------------------------------------------------------
# Step 6.2 — rejection codes 6 and 7 in compute_candidate_diagnostics
# ---------------------------------------------------------------------------

class TestRejectionCodeStep62:
    """Codes 6 and 7 are correctly mapped and diagnosed."""

    def _make_npz_with_code(self, n_windows: int, code: int) -> "_FakeNpz":
        """NPZ where rank 0 has the given rejection code and did NOT pass."""
        freqs = _make_freqs()
        npz   = _make_npz(n_windows, freqs)
        d = dict(npz._d)
        rej = d["candidate_rejection_code"].copy()
        rej[:, 0] = code
        d["candidate_rejection_code"] = rej
        # Mark rank 0 as not-passed so the code is meaningful
        cand_passed = d["candidate_passed"].copy()
        cand_passed[:, 0] = False
        d["candidate_passed"] = cand_passed
        return _FakeNpz(d)

    def test_code6_maps_to_peak_to_floor_db_low(self):
        from scripts.diagnose_step6_hr import compute_candidate_diagnostics
        freqs  = _make_freqs()
        n      = 3
        df     = _make_csv(n)
        npz    = self._make_npz_with_code(n, 6)
        cand_df = compute_candidate_diagnostics("synth", df, npz, freqs)
        rank0 = cand_df[cand_df["candidate_rank"] == 0]
        assert (rank0["candidate_rejection_code"] == 6).all()
        assert (rank0["candidate_rejection_reason"] == "peak_to_floor_db_low").all()

    def test_code7_maps_to_low_candidate_floor_db_low(self):
        from scripts.diagnose_step6_hr import compute_candidate_diagnostics
        freqs  = _make_freqs()
        n      = 3
        df     = _make_csv(n)
        npz    = self._make_npz_with_code(n, 7)
        cand_df = compute_candidate_diagnostics("synth", df, npz, freqs)
        rank0 = cand_df[cand_df["candidate_rank"] == 0]
        assert (rank0["candidate_rejection_code"] == 7).all()
        assert (rank0["candidate_rejection_reason"] == "low_candidate_floor_db_low").all()

    def test_codes_6_and_7_do_not_corrupt_shape(self):
        """n_windows * N_CAND rows even with codes 6 and 7 present."""
        from scripts.diagnose_step6_hr import compute_candidate_diagnostics
        freqs  = _make_freqs()
        n      = 5
        df     = _make_csv(n)
        # Mix of codes 6 and 7 in different windows
        npz6 = _make_npz(n, freqs)
        d = dict(npz6._d)
        rej = d["candidate_rejection_code"].copy()
        rej[0, 0] = 6
        rej[1, 0] = 7
        rej[2, 0] = 6
        d["candidate_rejection_code"] = rej
        npz_mixed = _FakeNpz(d)
        cand_df = compute_candidate_diagnostics("synth", df, npz_mixed, freqs)
        assert len(cand_df) == n * N_CAND

    def test_unknown_code_falls_back_gracefully(self):
        """An NPZ with an unknown code produces a sentinel string, not a crash."""
        from scripts.diagnose_step6_hr import compute_candidate_diagnostics
        freqs  = _make_freqs()
        n      = 2
        df     = _make_csv(n)
        npz    = self._make_npz_with_code(n, 99)
        cand_df = compute_candidate_diagnostics("synth", df, npz, freqs)
        rank0 = cand_df[cand_df["candidate_rank"] == 0]
        assert (rank0["candidate_rejection_code"] == 99).all()
        assert (rank0["candidate_rejection_reason"].str.startswith("unknown_code")).all()
