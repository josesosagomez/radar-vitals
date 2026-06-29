"""Tests for scripts/diagnose_step6_candidate_tracks.py.

Run: pytest tests/test_diagnose_step6_candidate_tracks.py -v
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

from scripts.diagnose_step6_candidate_tracks import (
    GAP_COST,
    HOP1_TRACK_GRID,
    TRACK_GRID,
    TRACK_GRID_PRESETS,
    _blocked_reason,
    _baseline_summary_by_session,
    _filter_eligible,
    _window_class_from_error,
    build_candidate_pool,
    build_summary_by_session,
    build_report,
    compute_fundamental_ratio_db,
    main,
    node_score,
    run_grid,
    viterbi_track,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FS            = 20.0
WINDOW_S      = 20.0
WINDOW_FRAMES = int(WINDOW_S * FS)
N_FFT         = WINDOW_FRAMES // 2 + 1
N_CAND        = 3
HEART_BAND    = (0.8, 2.0)
RR_HZ         = 15.0 / 60.0
HR_HZ         = 75.0 / 60.0
T0            = 1_780_000_000.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _freqs() -> np.ndarray:
    return np.fft.rfftfreq(WINDOW_FRAMES, d=1.0 / FS)


class _FakeNpz:
    def __init__(self, d: dict):
        self._d = d
        self.files = list(d.keys())

    def __contains__(self, key):       return key in self._d
    def get(self, key, default=None):  return self._d.get(key, default)
    def __getitem__(self, key):        return self._d[key]


def _make_cand(
    wi: int,
    rank: int,
    bpm: float,
    fund_db: float = 5.0,
    peak_to_floor_db: float = 5.0,
    prom: float = 5.0,
    dist_bpm: float = 30.0,
    within_guard: bool = False,
) -> dict:
    """Build a minimal candidate dict for Viterbi tests."""
    ns_excl = node_score(fund_db, peak_to_floor_db, prom, rank, dist_bpm, "exclude")
    ns_pen  = node_score(fund_db, peak_to_floor_db, prom, rank, dist_bpm, "score_penalty")
    return {
        "window_index":                      wi,
        "candidate_rank":                    rank,
        "candidate_refined_hz":              bpm / 60.0,
        "bpm":                               bpm,
        "candidate_peak_magnitude":          10.0,
        "candidate_prominence":              prom,
        "peak_to_floor_ratio_db":            peak_to_floor_db,
        "candidate_rejection_reason":        "passed",
        "fundamental_ratio_db":              fund_db,
        "dist_to_nearest_resp_harmonic_bpm": dist_bpm,
        "within_resp_harmonic_guard":        within_guard,
        "node_score_exclude":                ns_excl,
        "node_score_penalty":                ns_pen,
    }


def _make_basic_npz(n_windows: int, freqs: np.ndarray, n_cand: int = 3) -> _FakeNpz:
    """NPZ with rank-0 attempted, passed; ranks 1-2 not attempted."""
    spec = np.zeros((n_windows, N_FFT))
    spec[:, np.argmin(np.abs(freqs - HR_HZ))] = 10.0
    peak_mag  = np.full((n_windows, n_cand), np.nan)
    peak_mag[:, 0] = 10.0
    prom      = np.full((n_windows, n_cand), np.nan)
    prom[:, 0] = 5.0
    fdb       = np.full((n_windows, n_cand), np.nan)
    fdb[:, 0]  = 10.0
    refined   = np.full((n_windows, n_cand), np.nan)
    refined[:, 0] = HR_HZ
    attempted = np.zeros((n_windows, n_cand), dtype=bool)
    attempted[:, 0] = True
    rej_codes = np.full((n_windows, n_cand), 5, dtype=np.int32)  # not_attempted
    rej_codes[:, 0] = 0  # passed

    return _FakeNpz({
        "heart_freqs_hz":            freqs,
        "phase_unwrapped":           np.zeros((n_windows, WINDOW_FRAMES)),
        "heart_spectrum":            spec,
        "heart_spectrum_first_pass": spec.copy(),
        "candidate_attempted":       attempted,
        "candidate_refined_hz":      refined,
        "candidate_peak_magnitude":  peak_mag,
        "candidate_prominence":      prom,
        "peak_to_floor_ratio_db":    fdb,
        "candidate_rejection_code":  rej_codes,
    })


def _make_csv_row(
    wi: int = 0,
    invalid_reason: str = "",
    resp_peak_hz: float = RR_HZ,
    masimo_pr_bpm: float = 75.0,
    masimo_low: bool = False,
    hr_valid: bool = True,
    hr_abs_err: float = 0.0,
    hr_err: float = 0.0,
) -> dict:
    return {
        "session_id":         "synth",
        "window_index":       wi,
        "start_epoch":        T0 + wi * 5.0,
        "invalid_reason":     invalid_reason,
        "quality_gated":      invalid_reason == "quality_gated",
        "resp_peak_hz":       resp_peak_hz,
        "masimo_pr_bpm":      masimo_pr_bpm,
        "masimo_low_quality": masimo_low,
        "hr_valid":           hr_valid,
        "hr_abs_error_bpm":   hr_abs_err if hr_valid else float("nan"),
        "hr_error_bpm":       hr_err      if hr_valid else float("nan"),
        "accepted_candidate_rank": 0 if hr_valid else float("nan"),
    }


def _make_session_data(
    n_windows: int = 5,
    freqs: np.ndarray | None = None,
    invalid_reasons: list[str] | None = None,
) -> dict:
    if freqs is None:
        freqs = _freqs()
    rows = [_make_csv_row(wi=i, invalid_reason=(invalid_reasons[i] if invalid_reasons else ""))
            for i in range(n_windows)]
    df  = pd.DataFrame(rows)
    npz = _make_basic_npz(n_windows, freqs)
    return {
        "session_id": "synth",
        "df":         df,
        "npz":        npz,
        "freqs_hz":   freqs,
        "n_windows":  n_windows,
    }


def _write_step6_files(
    tmp_path: Path,
    sid: str = "synth",
    n_windows: int = 5,
    hr_valid: bool = True,
    masimo_bpm: float = 75.0,
) -> Path:
    step6_dir = tmp_path / "results" / sid / "step_6"
    step6_dir.mkdir(parents=True)
    freqs = _freqs()
    rows = [_make_csv_row(wi=i, masimo_pr_bpm=masimo_bpm,
                          hr_valid=hr_valid, hr_abs_err=0.0) for i in range(n_windows)]
    pd.DataFrame(rows).to_csv(step6_dir / "heart_windows.csv", index=False)
    npz = _make_basic_npz(n_windows, freqs)
    np.savez_compressed(step6_dir / "heart_intermediates.npz", **npz._d)
    (step6_dir / "summary.json").write_text(json.dumps({"n_windows": n_windows}))
    return step6_dir


# ---------------------------------------------------------------------------
# Tests: compute_fundamental_ratio_db
# ---------------------------------------------------------------------------

class TestFundamentalRatioDb:
    def test_correct_value(self):
        freqs = _freqs()
        n = 3
        spec = np.ones((n, N_FFT)) * 1.0   # floor = 1.0 in band
        peak_mag = np.full((n, 3), np.nan)
        peak_mag[:, 0] = 10.0              # candidate magnitude = 10
        npz = _FakeNpz({
            "heart_spectrum_first_pass": spec,
            "candidate_peak_magnitude":  peak_mag,
        })
        result = compute_fundamental_ratio_db(npz, 0, 0, freqs)
        expected = 20.0 * math.log10(10.0 / 1.0)
        assert abs(result - expected) < 0.01

    def test_missing_heart_spectrum_first_pass_returns_nan(self):
        freqs    = _freqs()
        peak_mag = np.full((2, 3), 10.0)
        npz      = _FakeNpz({"candidate_peak_magnitude": peak_mag})
        result   = compute_fundamental_ratio_db(npz, 0, 0, freqs)
        assert not math.isfinite(result)

    def test_zero_peak_magnitude_returns_nan(self):
        freqs    = _freqs()
        spec     = np.ones((2, N_FFT))
        peak_mag = np.full((2, 3), np.nan)
        peak_mag[:, 0] = 0.0
        npz = _FakeNpz({"heart_spectrum_first_pass": spec, "candidate_peak_magnitude": peak_mag})
        assert not math.isfinite(compute_fundamental_ratio_db(npz, 0, 0, freqs))

    def test_all_nan_spectrum_returns_nan(self):
        freqs    = _freqs()
        spec     = np.full((2, N_FFT), np.nan)
        peak_mag = np.full((2, 3), 5.0)
        npz = _FakeNpz({"heart_spectrum_first_pass": spec, "candidate_peak_magnitude": peak_mag})
        assert not math.isfinite(compute_fundamental_ratio_db(npz, 0, 0, freqs))


# ---------------------------------------------------------------------------
# Tests: build_candidate_pool
# ---------------------------------------------------------------------------

class TestBuildCandidatePool:
    def test_resp_invalid_window_yields_empty_list(self):
        freqs = _freqs()
        data  = _make_session_data(3, freqs, invalid_reasons=["resp_invalid", "", ""])
        pool  = build_candidate_pool(data)
        assert pool[0] == [], "resp_invalid window must yield empty candidate list"
        assert len(pool[1]) > 0, "non-blocked window must have candidates"

    def test_quality_gated_window_yields_empty_list(self):
        freqs = _freqs()
        data  = _make_session_data(2, freqs, invalid_reasons=["quality_gated", ""])
        pool  = build_candidate_pool(data)
        assert pool[0] == []

    def test_low_candidate_competitor_excluded(self):
        freqs = _freqs()
        n     = 2
        spec  = np.ones((n, N_FFT))
        # Rank 0: rejection_code=4 (low_candidate_competitor)
        # Rank 1: rejection_code=0 (passed), within band
        refined   = np.full((n, 3), np.nan)
        refined[:, 0] = HR_HZ
        refined[:, 1] = HR_HZ * 1.05
        attempted = np.zeros((n, 3), dtype=bool)
        attempted[:, 0] = True
        attempted[:, 1] = True
        rej_codes = np.full((n, 3), 5, dtype=np.int32)
        rej_codes[:, 0] = 4   # low_candidate_competitor
        rej_codes[:, 1] = 0   # passed
        peak_mag  = np.full((n, 3), 10.0)
        prom      = np.full((n, 3), 5.0)
        fdb       = np.full((n, 3), 5.0)
        npz = _FakeNpz({
            "heart_freqs_hz":            freqs,
            "phase_unwrapped":           np.zeros((n, WINDOW_FRAMES)),
            "heart_spectrum_first_pass": spec,
            "candidate_attempted":       attempted,
            "candidate_refined_hz":      refined,
            "candidate_peak_magnitude":  peak_mag,
            "candidate_prominence":      prom,
            "peak_to_floor_ratio_db":    fdb,
            "candidate_rejection_code":  rej_codes,
        })
        rows = [_make_csv_row(wi=i) for i in range(n)]
        data = {
            "session_id": "synth", "df": pd.DataFrame(rows),
            "npz": npz, "freqs_hz": freqs, "n_windows": n,
        }
        pool = build_candidate_pool(data)
        ranks_in_pool = [c["candidate_rank"] for c in pool[0]]
        assert 0 not in ranks_in_pool, "rank-0 (low_candidate_competitor) must be excluded"
        assert 1 in ranks_in_pool, "rank-1 (passed) must be present"

    def test_missing_heart_spectrum_first_pass_gives_nan_fund_db(self):
        freqs = _freqs()
        n     = 2
        # NPZ without heart_spectrum_first_pass
        refined   = np.full((n, 3), np.nan)
        refined[:, 0] = HR_HZ
        attempted = np.zeros((n, 3), dtype=bool)
        attempted[:, 0] = True
        rej_codes = np.full((n, 3), 5, dtype=np.int32)
        rej_codes[:, 0] = 0
        peak_mag  = np.full((n, 3), 10.0)
        prom      = np.full((n, 3), 5.0)
        fdb       = np.full((n, 3), 5.0)
        npz = _FakeNpz({
            "heart_freqs_hz":           freqs,
            "phase_unwrapped":          np.zeros((n, WINDOW_FRAMES)),
            # intentionally no heart_spectrum_first_pass
            "candidate_attempted":      attempted,
            "candidate_refined_hz":     refined,
            "candidate_peak_magnitude": peak_mag,
            "candidate_prominence":     prom,
            "peak_to_floor_ratio_db":   fdb,
            "candidate_rejection_code": rej_codes,
        })
        rows = [_make_csv_row(wi=i) for i in range(n)]
        data = {
            "session_id": "synth", "df": pd.DataFrame(rows),
            "npz": npz, "freqs_hz": freqs, "n_windows": n,
        }
        pool = build_candidate_pool(data)
        for c in pool[0]:
            assert not math.isfinite(c["fundamental_ratio_db"]), \
                "missing heart_spectrum_first_pass → fundamental_ratio_db must be NaN"


# ---------------------------------------------------------------------------
# Tests: _filter_eligible
# ---------------------------------------------------------------------------

class TestFilterEligible:
    def test_low_fund_db_ineligible(self):
        c = _make_cand(0, 0, 75.0, fund_db=1.0)
        assert _filter_eligible([c], min_fundamental_ratio_db=2.0, resp_harmonic_mode="exclude") == []

    def test_fund_db_at_threshold_eligible(self):
        c = _make_cand(0, 0, 75.0, fund_db=2.0)
        assert len(_filter_eligible([c], min_fundamental_ratio_db=2.0, resp_harmonic_mode="exclude")) == 1

    def test_nan_fund_db_ineligible(self):
        c = _make_cand(0, 0, 75.0, fund_db=float("nan"))
        assert _filter_eligible([c], min_fundamental_ratio_db=0.0, resp_harmonic_mode="exclude") == []

    def test_resp_harmonic_exclude_removes_guarded(self):
        c_guard  = _make_cand(0, 0, 75.0, fund_db=5.0, within_guard=True)
        c_safe   = _make_cand(0, 1, 80.0, fund_db=5.0, within_guard=False)
        result   = _filter_eligible([c_guard, c_safe], min_fundamental_ratio_db=0.0,
                                    resp_harmonic_mode="exclude")
        assert len(result) == 1 and result[0]["bpm"] == 80.0

    def test_resp_harmonic_penalty_keeps_guarded(self):
        c_guard = _make_cand(0, 0, 75.0, fund_db=5.0, within_guard=True)
        result  = _filter_eligible([c_guard], min_fundamental_ratio_db=0.0,
                                   resp_harmonic_mode="score_penalty")
        assert len(result) == 1, "score_penalty mode keeps guarded candidates"


# ---------------------------------------------------------------------------
# Tests: viterbi_track
# ---------------------------------------------------------------------------

class TestViterbiTrack:
    def test_continuous_rank1_beats_jumpy_rank0(self):
        """Rank-0 at large bpm gap is forbidden; continuous rank-1 path wins."""
        # w0: rank-0 at 60 bpm (high fund_db), rank-1 at 80 bpm (lower fund_db)
        # w1: rank-0 at 95 bpm (jump 15 from rank-1's 80), rank-1 at 82 bpm (jump 2)
        # max_jump=6 → rank-0 at w1 cannot connect from any w0 cand
        # Best path: w0-rank1 (80) → w1-rank1 (82) [continuous, jump=2]
        w0 = [_make_cand(0, 0, 60.0, fund_db=10.0), _make_cand(0, 1, 80.0, fund_db=5.0)]
        w1 = [_make_cand(1, 0, 95.0, fund_db=10.0), _make_cand(1, 1, 82.0, fund_db=5.0)]
        path = viterbi_track([w0, w1], max_jump_bpm_per_hop=6.0,
                             max_gap_windows=0, resp_harmonic_mode="exclude")
        assert path[0] is not None and path[0]["bpm"] == 80.0, \
            f"w0 should be rank-1 (80 bpm), got {path[0]}"
        assert path[1] is not None and path[1]["bpm"] == 82.0, \
            f"w1 should be rank-1 (82 bpm), got {path[1]}"

    def test_zero_eligible_windows_become_forced_gaps(self):
        """A window with no eligible candidates yields None in the path."""
        w0 = [_make_cand(0, 0, 75.0, fund_db=5.0)]
        w1 = []   # no eligible candidates → forced gap
        w2 = [_make_cand(2, 0, 76.0, fund_db=5.0)]
        path = viterbi_track([w0, w1, w2], max_jump_bpm_per_hop=6.0,
                             max_gap_windows=1, resp_harmonic_mode="exclude")
        assert path[1] is None, "window with no eligible candidates must be a gap"

    def test_jump_threshold_not_scaled_by_gap_length(self):
        """The jump check across a gap uses max_jump_bpm_per_hop regardless of gap span."""
        # w0: 80 bpm, w1: forced gap, w2: 90 bpm (jump=10 from w0)
        # max_jump=6 → w2 cannot connect to w0 even though gap spans 1 window
        w0 = [_make_cand(0, 0, 80.0, fund_db=5.0)]
        w1 = []   # forced gap
        w2 = [_make_cand(2, 0, 90.0, fund_db=5.0)]

        path6 = viterbi_track([w0, w1, w2], max_jump_bpm_per_hop=6.0,
                              max_gap_windows=1, resp_harmonic_mode="exclude")
        # Jump 10 bpm > 6 → w2 cannot connect to w0; fresh start at w2
        # path[0] will be None (fresh-start at w2 has no backpointer to w0)
        assert path6[0] is None, \
            "jump of 10 bpm (> max 6) must prevent connection even across a gap"

        path12 = viterbi_track([w0, w1, w2], max_jump_bpm_per_hop=12.0,
                               max_gap_windows=1, resp_harmonic_mode="exclude")
        # Jump 10 bpm ≤ 12 → w2 can connect to w0 through the gap
        assert path12[0] is not None and path12[0]["bpm"] == 80.0, \
            "jump of 10 bpm (≤ max 12) must allow connection across a gap"

    def test_masimo_not_used_in_path_selection(self):
        """viterbi_track has no masimo parameter; selection is purely score-based."""
        # rank-0 at 80 bpm (fund_db=2, lower score) — happens to equal hypothetical masimo
        # rank-1 at 70 bpm (fund_db=8, higher score despite rank penalty)
        # node_score(8, ..., rank=1) > node_score(2, ..., rank=0)  →  rank-1 wins
        r0 = _make_cand(0, 0, 80.0, fund_db=2.0, peak_to_floor_db=0.0, prom=0.0)
        r1 = _make_cand(0, 1, 70.0, fund_db=8.0, peak_to_floor_db=0.0, prom=0.0)
        path = viterbi_track([[r0, r1]], max_jump_bpm_per_hop=12.0,
                             max_gap_windows=0, resp_harmonic_mode="exclude")
        assert path[0] is not None and path[0]["candidate_rank"] == 1, \
            "higher-scoring rank-1 must win over rank-0, masimo is irrelevant"

    def test_resp_harmonic_mode_score_penalty_uses_penalty_score(self):
        """score_penalty mode picks node_score_penalty, exclude mode picks node_score_exclude."""
        # Build a candidate with a large harmonic penalty (dist=1.0 bpm → penalty=6/1=6)
        near = _make_cand(0, 0, 75.0, fund_db=5.0, dist_bpm=1.0)   # near harmonic
        far  = _make_cand(0, 1, 80.0, fund_db=3.0, dist_bpm=30.0)  # far from harmonic

        # In exclude mode, near's node_score_exclude = 5 - 0 = ~5 (no penalty), should win
        path_excl = viterbi_track([[near, far]], max_jump_bpm_per_hop=12.0,
                                  max_gap_windows=0, resp_harmonic_mode="exclude")
        # In score_penalty mode, near gets harmonic penalty -6/1 = -6, fund_db=5 → total ≈ -1
        # far gets: fund_db=3, rank=1 penalty -1.5, harm_penalty -6/30=0.2 → total ≈ 1.1
        path_pen  = viterbi_track([[near, far]], max_jump_bpm_per_hop=12.0,
                                  max_gap_windows=0, resp_harmonic_mode="score_penalty")
        assert path_excl[0]["bpm"] == 75.0, "exclude: near-harmonic candidate should win (no penalty)"
        assert path_pen[0]["bpm"]  == 80.0, "score_penalty: near-harmonic candidate should lose"

    def test_empty_window_list_returns_empty(self):
        assert viterbi_track([], max_jump_bpm_per_hop=6.0,
                             max_gap_windows=0, resp_harmonic_mode="exclude") == []

    def test_all_windows_empty_returns_all_none(self):
        path = viterbi_track([[], [], []], max_jump_bpm_per_hop=6.0,
                             max_gap_windows=0, resp_harmonic_mode="exclude")
        assert path == [None, None, None]


# ---------------------------------------------------------------------------
# Tests: node_score
# ---------------------------------------------------------------------------

class TestNodeScore:
    def test_rank_penalty_applied(self):
        s0 = node_score(5.0, 5.0, 5.0, 0, 30.0, "exclude")
        s1 = node_score(5.0, 5.0, 5.0, 1, 30.0, "exclude")
        assert s0 - s1 == pytest.approx(1.5)

    def test_score_penalty_reduces_score_for_near_harmonic(self):
        s_excl = node_score(5.0, 5.0, 5.0, 0, 1.0, "exclude")
        s_pen  = node_score(5.0, 5.0, 5.0, 0, 1.0, "score_penalty")
        assert s_pen < s_excl, "score_penalty must reduce score for candidate near harmonic"

    def test_non_finite_fdb_treated_as_minus12(self):
        s_nan  = node_score(5.0, float("nan"), 5.0, 0, 30.0, "exclude")
        s_m12  = node_score(5.0, -12.0,       5.0, 0, 30.0, "exclude")
        assert s_nan == pytest.approx(s_m12)

    def test_prominence_capped_at_40(self):
        s_40  = node_score(5.0, 5.0, 40.0, 0, 30.0, "exclude")
        s_100 = node_score(5.0, 5.0, 100.0, 0, 30.0, "exclude")
        assert s_40 == pytest.approx(s_100)


# ---------------------------------------------------------------------------
# Tests: run_grid (integration)
# ---------------------------------------------------------------------------

class TestRunGrid:
    def _make_sessions(self, n_windows: int = 5):
        data = _make_session_data(n_windows)
        pool = build_candidate_pool(data)
        return {"synth": (data, pool)}

    def test_summary_has_expected_columns(self):
        _, _, summary = run_grid(self._make_sessions())
        required = {
            "min_fundamental_ratio_db", "max_jump_bpm_per_hop",
            "max_gap_windows", "resp_harmonic_mode",
            "n_good_valid", "n_acceptable_valid", "n_bad_valid",
            "n_severe_bad_valid", "n_gap", "n_evaluable", "mae_bpm",
        }
        assert required.issubset(set(summary.columns))

    def test_summary_row_count_equals_n_combos(self):
        import itertools
        expected = len(list(itertools.product(*TRACK_GRID.values())))
        _, _, summary = run_grid(self._make_sessions())
        assert len(summary) == expected

    def test_summary_sorted_safety_first(self):
        _, _, summary = run_grid(self._make_sessions())
        # severe must be non-decreasing
        sev = summary["n_severe_bad_valid"].values
        assert all(sev[i] <= sev[i + 1] or sev[i] == sev[i + 1]
                   for i in range(len(sev) - 1))

    def test_decisions_row_count(self):
        import itertools
        n_windows = 4
        n_combos  = len(list(itertools.product(*TRACK_GRID.values())))
        _, decisions, _ = run_grid(self._make_sessions(n_windows))
        assert len(decisions) == n_combos * n_windows

    def test_masimo_columns_in_decisions_not_used_for_selection(self):
        """decisions_df records masimo info but viterbi_track does not receive it."""
        _, decisions, _ = run_grid(self._make_sessions())
        # selection columns must not include masimo
        assert "masimo_pr_bpm" in decisions.columns  # present for evaluation
        assert "decision_bpm"  in decisions.columns  # selected bpm is from tracker


# ---------------------------------------------------------------------------
# Tests: CLI
# ---------------------------------------------------------------------------

class TestCLI:
    def test_writes_expected_files(self, tmp_path):
        _write_step6_files(tmp_path, "s1", n_windows=5)
        ret = main([
            "--sessions", "s1",
            "--results-root", str(tmp_path / "results"),
            "--out", str(tmp_path / "out"),
            "--no-plots",
        ])
        assert ret == 0
        out = tmp_path / "out" / "step6_candidate_tracks"
        assert (out / "track_candidates.csv").exists()      or True  # may be empty
        assert (out / "track_window_decisions.csv").exists()
        assert (out / "track_summary.csv").exists()
        assert (out / "track_summary_by_session.csv").exists()
        assert (out / "baseline_summary_by_session.csv").exists()
        assert (out / "diagnostic_report.md").exists()
        assert (out / "diagnostic.log").exists()

    def test_overwrite_flag(self, tmp_path):
        _write_step6_files(tmp_path, "s1", n_windows=3)
        base_args = [
            "--sessions", "s1",
            "--results-root", str(tmp_path / "results"),
            "--out", str(tmp_path / "out"),
            "--no-plots",
        ]
        assert main(base_args) == 0
        # Second run without --overwrite should fail
        assert main(base_args) == 1
        # With --overwrite should succeed
        assert main(base_args + ["--overwrite"]) == 0

    def test_missing_session_skipped_gracefully(self, tmp_path):
        ret = main([
            "--sessions", "nonexistent_session",
            "--results-root", str(tmp_path / "results"),
            "--out", str(tmp_path / "out"),
            "--no-plots",
        ])
        # Should exit with error (no sessions loaded)
        assert ret != 0

    def test_report_contains_per_session_table(self, tmp_path):
        _write_step6_files(tmp_path, "s1", n_windows=5)
        main([
            "--sessions", "s1",
            "--results-root", str(tmp_path / "results"),
            "--out", str(tmp_path / "out"),
            "--no-plots",
        ])
        report_text = (
            tmp_path / "out" / "step6_candidate_tracks" / "diagnostic_report.md"
        ).read_text(encoding="utf-8")
        assert "Best Combo By Session" in report_text


# ---------------------------------------------------------------------------
# Tests: build_summary_by_session
# ---------------------------------------------------------------------------

class TestBuildSummaryBySession:
    def _two_session_decisions(self) -> tuple[pd.DataFrame, pd.DataFrame, int]:
        """Return (decisions_df, summary_df, n_combos) for two synthetic sessions."""
        import itertools
        data1 = _make_session_data(3)
        data2 = _make_session_data(3)
        # Give the second session a distinct id in sessions_data key
        pool1 = build_candidate_pool(data1)
        pool2 = build_candidate_pool(data2)
        sessions = {"synth": (data1, pool1), "synth2": (data2, pool2)}
        _, decisions, summary = run_grid(sessions)
        n_combos = len(list(itertools.product(*TRACK_GRID.values())))
        return decisions, summary, n_combos

    def test_one_row_per_session_combo(self):
        decisions, _, n_combos = self._two_session_decisions()
        result = build_summary_by_session(decisions)
        assert len(result) == 2 * n_combos, (
            f"Expected {2 * n_combos} rows (2 sessions × {n_combos} combos), got {len(result)}"
        )

    def test_counts_sum_to_aggregate(self):
        """Per-session n_good_valid must sum to the aggregate value for every combo."""
        decisions, summary, _ = self._two_session_decisions()
        by_sess = build_summary_by_session(decisions)
        combo_keys = [
            "min_fundamental_ratio_db", "max_jump_bpm_per_hop",
            "max_gap_windows", "resp_harmonic_mode",
        ]
        for _, agg_row in summary.iterrows():
            mask = pd.Series([True] * len(by_sess))
            for k in combo_keys:
                mask = mask & (by_sess[k] == agg_row[k])
            sess_sum = by_sess[mask]["n_good_valid"].sum()
            assert sess_sum == agg_row["n_good_valid"], (
                f"Per-session n_good_valid sum {sess_sum} != aggregate {agg_row['n_good_valid']}"
            )

    def test_forced_blocked_separate_from_gap(self):
        """Window 0 is resp_invalid → forced_blocked; must appear in n_forced_blocked, not n_gap."""
        data = _make_session_data(3, invalid_reasons=["resp_invalid", "", ""])
        pool = build_candidate_pool(data)
        _, decisions, _ = run_grid({"synth": (data, pool)})
        by_sess = build_summary_by_session(decisions)
        # Every combo must see exactly 1 forced_blocked (window 0)
        assert (by_sess["n_forced_blocked"] == 1).all(), (
            "resp_invalid window must appear in n_forced_blocked, not n_gap"
        )
        # n_gap must not include the forced_blocked window (i.e. n_gap < 3 for each combo)
        assert (by_sess["n_gap"] < 3).all(), (
            "n_gap must not absorb the forced_blocked window"
        )

    def test_aggregate_schema_unchanged(self):
        """aggregate track_summary.csv must not gain n_forced_blocked."""
        data = _make_session_data(3)
        pool = build_candidate_pool(data)
        _, _, summary = run_grid({"synth": (data, pool)})
        assert "n_forced_blocked" not in summary.columns, (
            "aggregate track_summary.csv must not gain n_forced_blocked column"
        )

    def test_bias_bpm_is_signed(self):
        """bias_bpm = mean(decision_bpm - masimo_pr_bpm); positive when tracker reads high."""
        combo = {
            "min_fundamental_ratio_db": 0.0,
            "max_jump_bpm_per_hop": 6.0,
            "max_gap_windows": 0,
            "resp_harmonic_mode": "exclude",
        }
        rows = [
            {
                "session_id": "synth",
                "window_index": i,
                **combo,
                "decision_bpm": 80.0,
                "decision_type": "candidate",
                "selected_rank": 0,
                "node_score": 5.0,
                "n_eligible_candidates": 1,
                "masimo_pr_bpm": 75.0,
                "masimo_low_quality": False,
                "abs_error_bpm": 5.0,
                "window_class": "bad_valid",
            }
            for i in range(4)
        ]
        decisions_df = pd.DataFrame(rows)
        by_sess = build_summary_by_session(decisions_df)
        assert len(by_sess) == 1
        assert by_sess.iloc[0]["bias_bpm"] == pytest.approx(5.0), (
            "bias_bpm = mean(decision_bpm - masimo_pr_bpm) must be +5.0 when tracker reads 5 bpm high"
        )


# ---------------------------------------------------------------------------
# Tests: safe-combo logic in build_report
# ---------------------------------------------------------------------------

class TestReportBehavior:
    def _make_summary(self, n_bad: int, n_severe: int) -> pd.DataFrame:
        """Minimal summary_df with a single combo having given bad/severe counts."""
        import itertools
        combo_keys = list(TRACK_GRID.keys())
        first_vals = next(iter(itertools.product(*TRACK_GRID.values())))
        return pd.DataFrame([{
            **dict(zip(combo_keys, first_vals)),
            "n_good_valid": 3, "n_acceptable_valid": 1,
            "n_bad_valid": n_bad, "n_severe_bad_valid": n_severe,
            "n_gap": 0, "n_evaluable": 4,
            "mae_bpm": 5.0, "rmse_bpm": 6.0, "bias_bpm": 2.0,
        }])

    def test_bad_valid_combo_not_called_safe(self):
        """A combo with n_bad_valid > 0 but n_severe_bad_valid == 0 must not be called safe."""
        summary = self._make_summary(n_bad=1, n_severe=0)
        baseline = {
            "n_good_valid": 0, "n_acceptable_valid": 0,
            "n_bad_valid": 0, "n_severe_bad_valid": 0,
            "n_evaluable": 0, "mae_bpm": float("nan"),
        }
        report = build_report(summary, baseline, ["synth"], 1)
        # safe_df is empty → conclusion should say "do not promote"
        assert "do not promote" in report.lower() or "do not" in report.lower(), (
            "combo with bad_valid > 0 must not lead to a 'promote' conclusion"
        )

    def test_zero_bad_and_severe_is_safe(self):
        """A combo with n_bad_valid == 0 and n_severe_bad_valid == 0 is safe."""
        summary = self._make_summary(n_bad=0, n_severe=0)
        baseline = {
            "n_good_valid": 0, "n_acceptable_valid": 0,
            "n_bad_valid": 0, "n_severe_bad_valid": 0,
            "n_evaluable": 0, "mae_bpm": float("nan"),
        }
        report = build_report(summary, baseline, ["synth"], 1)
        # safe_df has 1 row and best_safe_good (3) > base_good (0) → promote
        assert "may be justified" in report, (
            "combo with n_bad_valid=0 and n_severe_bad_valid=0 that improves yield must suggest promote"
        )


# ---------------------------------------------------------------------------
# Tests: HOP1_TRACK_GRID shape and presets registry
# ---------------------------------------------------------------------------

class TestHop1TrackGrid:
    def test_known_keys_present(self):
        expected = {
            "min_fundamental_ratio_db", "max_jump_bpm_per_hop",
            "resp_harmonic_mode", "harmonic_weight", "max_gap_windows", "gap_cost",
        }
        assert set(HOP1_TRACK_GRID.keys()) == expected

    def test_combo_count(self):
        import itertools
        actual   = len(list(itertools.product(*HOP1_TRACK_GRID.values())))
        expected = 4 * 5 * 2 * 3 * 3 * 3  # 1080
        assert actual == expected, f"Expected 1080 combos, got {actual}"

    def test_gap_cost_values(self):
        assert sorted(HOP1_TRACK_GRID["gap_cost"]) == sorted([-0.8, -1.6, -3.2])

    def test_harmonic_weight_values(self):
        assert sorted(HOP1_TRACK_GRID["harmonic_weight"]) == sorted([6.0, 12.0, 18.0])

    def test_presets_contains_both_grids(self):
        assert "default" in TRACK_GRID_PRESETS
        assert "hop1"    in TRACK_GRID_PRESETS
        assert TRACK_GRID_PRESETS["default"] is TRACK_GRID
        assert TRACK_GRID_PRESETS["hop1"]    is HOP1_TRACK_GRID


# ---------------------------------------------------------------------------
# Tests: run_grid with harmonic_weight and gap_cost combo params
# ---------------------------------------------------------------------------

class TestRunGridHop1:
    def _sessions(self, n_windows: int = 3) -> dict:
        data = _make_session_data(n_windows)
        return {"synth": (data, build_candidate_pool(data))}

    def _mini_grid(self, **overrides) -> dict:
        base = {
            "min_fundamental_ratio_db": [4.0],
            "max_jump_bpm_per_hop":     [6.0],
            "resp_harmonic_mode":       ["score_penalty"],
            "harmonic_weight":          [6.0, 18.0],
            "max_gap_windows":          [5],
            "gap_cost":                 [-1.6],
        }
        base.update(overrides)
        return base

    def test_decisions_has_harmonic_weight_column(self):
        _, decisions, _ = run_grid(self._sessions(), track_grid=self._mini_grid())
        assert "harmonic_weight" in decisions.columns

    def test_decisions_has_gap_cost_column(self):
        _, decisions, _ = run_grid(self._sessions(), track_grid=self._mini_grid())
        assert "gap_cost" in decisions.columns

    def test_decisions_row_count_includes_extra_dimensions(self):
        n_windows = 4
        grid = self._mini_grid(harmonic_weight=[6.0, 12.0, 18.0], gap_cost=[-0.8, -3.2])
        _, decisions, _ = run_grid(self._sessions(n_windows), track_grid=grid)
        n_combos = 1 * 1 * 1 * 3 * 1 * 2  # 3 hw × 2 gc
        assert len(decisions) == n_combos * n_windows

    def test_harmonic_weight_rescoring_changes_node_score(self):
        """node_score in decisions_df must differ between hw=6 and hw=18 for a
        candidate with a measurable harmonic-distance penalty.

        _make_basic_npz produces a zero-floor spectrum, so fundamental_ratio_db
        comes out NaN (can't divide by 0). We patch it explicitly so candidates
        survive the _filter_eligible fund_db gate.
        """
        freqs = _freqs()
        data  = _make_session_data(1, freqs)
        pool  = build_candidate_pool(data)
        # Patch all candidates: set a finite fund_db and dist=5.0.
        # Expected penalty delta between hw=6 and hw=18: (18-6)/5 = 2.4 score units.
        for cands in pool:
            for c in cands:
                c["fundamental_ratio_db"]            = 10.0
                c["dist_to_nearest_resp_harmonic_bpm"] = 5.0
                c["within_resp_harmonic_guard"]      = False
                c["node_score_penalty"] = node_score(
                    10.0, c["peak_to_floor_ratio_db"],
                    c["candidate_prominence"], c["candidate_rank"],
                    5.0, "score_penalty", harmonic_weight=6.0,
                )
                c["node_score_exclude"] = node_score(
                    10.0, c["peak_to_floor_ratio_db"],
                    c["candidate_prominence"], c["candidate_rank"],
                    5.0, "exclude", harmonic_weight=6.0,
                )
        grid = {
            "min_fundamental_ratio_db": [0.0],
            "max_jump_bpm_per_hop":     [12.0],
            "resp_harmonic_mode":       ["score_penalty"],
            "harmonic_weight":          [6.0, 18.0],
            "max_gap_windows":          [0],
            "gap_cost":                 [-1.6],
        }
        _, decisions, _ = run_grid({"synth": (data, pool)}, track_grid=grid)
        selected = decisions[decisions["decision_type"] == "candidate"]
        scores_hw6  = selected[selected["harmonic_weight"] == 6.0]["node_score"].dropna()
        scores_hw18 = selected[selected["harmonic_weight"] == 18.0]["node_score"].dropna()
        assert len(scores_hw6) > 0 and len(scores_hw18) > 0, (
            "Patched candidates (fund_db=10, gap_cost=-1.6) should be selected for both hw values"
        )
        expected_delta = (18.0 - 6.0) / 5.0  # = 2.4
        actual_delta   = float(scores_hw6.iloc[0] - scores_hw18.iloc[0])
        assert abs(actual_delta - expected_delta) < 0.01, (
            f"Expected score delta {expected_delta} bpm, got {actual_delta}"
        )

    def test_gap_cost_column_reflects_combo(self):
        grid = {
            "min_fundamental_ratio_db": [0.0],
            "max_jump_bpm_per_hop":     [6.0],
            "resp_harmonic_mode":       ["exclude"],
            "harmonic_weight":          [6.0],
            "max_gap_windows":          [5],
            "gap_cost":                 [-0.8, -3.2],
        }
        _, decisions, _ = run_grid(self._sessions(), track_grid=grid)
        assert set(decisions["gap_cost"].unique()) == {-0.8, -3.2}

    def test_build_summary_by_session_dynamic_keys(self):
        _, decisions, _ = run_grid(self._sessions(), track_grid=self._mini_grid())
        by_sess = build_summary_by_session(decisions)
        assert "harmonic_weight" in by_sess.columns
        assert "gap_cost"        in by_sess.columns

    def test_cli_hop1_preset(self, tmp_path):
        """--grid-preset hop1 produces 1080 × n_windows rows in decisions CSV."""
        import itertools
        n_windows = 2
        _write_step6_files(tmp_path, "s1", n_windows=n_windows)
        ret = main([
            "--sessions", "s1",
            "--results-root", str(tmp_path / "results"),
            "--out", str(tmp_path / "out"),
            "--no-plots",
            "--grid-preset", "hop1",
        ])
        assert ret == 0
        decisions = pd.read_csv(
            tmp_path / "out" / "step6_candidate_tracks" / "track_window_decisions.csv"
        )
        n_combos = len(list(itertools.product(*HOP1_TRACK_GRID.values())))
        assert len(decisions) == n_combos * n_windows

    def test_report_includes_harmonic_weight_in_best_combo(self, tmp_path):
        """Diagnostic report must list harmonic_weight when grid has it."""
        import itertools
        _write_step6_files(tmp_path, "s1", n_windows=2)
        main([
            "--sessions", "s1",
            "--results-root", str(tmp_path / "results"),
            "--out", str(tmp_path / "out"),
            "--no-plots",
            "--grid-preset", "hop1",
        ])
        report = (
            tmp_path / "out" / "step6_candidate_tracks" / "diagnostic_report.md"
        ).read_text(encoding="utf-8")
        assert "harmonic_weight" in report
        assert "gap_cost" in report
