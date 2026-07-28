"""Tests for src/comparator.py — the frozen HR/BR comparator spec, implemented.

Fixtures build minimal Masimo-shaped DataFrames directly (epoch_utc/pr_bpm/pi/rr_bpm),
rather than going through src/masimo.py's CSV parser, since only the reference-side
gate logic in src/comparator.py is under test here.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.comparator import br_metronome_concordance, br_reference, hr_reference


def _masimo_df(pr_bpm=None, pi=None, rr_bpm=None, epoch_start: int = 1_700_000_000) -> pd.DataFrame:
    """Build a minimal Masimo-shaped frame, one row per second starting at epoch_start.

    Any of pr_bpm/pi/rr_bpm may be omitted (defaults to a benign constant of the
    same length as whichever series IS given) or given as an explicit sequence.
    """
    lens = [len(v) for v in (pr_bpm, pi, rr_bpm) if v is not None]
    n = lens[0] if lens else 30
    assert all(L == n for L in lens)
    pr_bpm = np.asarray(pr_bpm, dtype=float) if pr_bpm is not None else np.full(n, 72.0)
    pi = np.asarray(pi, dtype=float) if pi is not None else np.full(n, 2.0)
    rr_bpm = np.asarray(rr_bpm, dtype=float) if rr_bpm is not None else np.full(n, 15.0)
    epoch = epoch_start + np.arange(n)
    return pd.DataFrame({"epoch_utc": epoch, "pr_bpm": pr_bpm, "pi": pi, "rr_bpm": rr_bpm})


# ── HR: usable-sample definition (finite pr_bpm AND finite pi AND pi>=0.5) ──────

def test_hr_reference_usable_excludes_non_finite_pr():
    pr = [72.0] * 29 + [float("nan")]
    df = _masimo_df(pr_bpm=pr)
    ref = hr_reference(df, 1_700_000_000, 1_700_000_030)
    assert ref["n_total"] == 30
    assert ref["n_finite_pr"] == 29
    assert ref["n_usable"] == 29


def test_hr_reference_usable_excludes_low_pi():
    pi = [2.0] * 29 + [0.2]
    df = _masimo_df(pi=pi)
    ref = hr_reference(df, 1_700_000_000, 1_700_000_030)
    assert ref["n_pi_qualified"] == 29
    assert ref["n_usable"] == 29


def test_hr_reference_usable_excludes_non_finite_pi():
    pi = [2.0] * 29 + [float("nan")]
    df = _masimo_df(pi=pi)
    ref = hr_reference(df, 1_700_000_000, 1_700_000_030)
    assert ref["n_pi_qualified"] == 29
    assert ref["n_usable"] == 29


def test_hr_reference_pi_boundary_inclusive():
    """pi exactly 0.5 is a usable sample (>= 0.5), 0.499999 is not."""
    pi = [0.5] * 24 + [0.499999] * 6
    df = _masimo_df(pi=pi)
    ref = hr_reference(df, 1_700_000_000, 1_700_000_030)
    assert ref["n_pi_qualified"] == 24
    assert ref["n_usable"] == 24
    assert ref["coverage_ok"] is True


# ── HR: coverage-gate boundary at exactly 24/30 ─────────────────────────────────

def test_hr_reference_coverage_boundary_24_admits():
    pi = [2.0] * 24 + [0.0] * 6  # exactly 24 usable
    df = _masimo_df(pi=pi)
    ref = hr_reference(df, 1_700_000_000, 1_700_000_030)
    assert ref["n_usable"] == 24
    assert ref["coverage_ok"] is True


def test_hr_reference_coverage_boundary_23_excludes():
    pi = [2.0] * 23 + [0.0] * 7  # exactly 23 usable
    df = _masimo_df(pi=pi)
    ref = hr_reference(df, 1_700_000_000, 1_700_000_030)
    assert ref["n_usable"] == 23
    assert ref["coverage_ok"] is False
    assert ref["admitted"] is False


# ── HR: the spec's own worked stationarity example (comparator_prespec.md §2.2) ─

def test_hr_reference_worked_stationarity_example_excludes():
    pr = [71.0] * 3 + [72.0] * 23 + [77.0] * 4
    df = _masimo_df(pr_bpm=pr, pi=[2.0] * 30)
    ref = hr_reference(df, 1_700_000_000, 1_700_000_030)
    assert ref["spread_bpm"] == pytest.approx(5.100, abs=1e-3)
    assert ref["stationarity_ok"] is False
    assert ref["coverage_ok"] is True
    assert ref["admitted"] is False


# n=31 sorted so index 3 (p10, h=(31-1)*0.10=3.0) and index 27 (p90, h=27.0) land
# exactly on data points under method="linear" — no interpolation blending, so the
# boundary is exact rather than approximate.
_STATIONARITY_BOUNDARY_PR = [50.0] * 3 + [70.0] + [72.0] * 23 + [75.0] + [100.0] * 3


def test_hr_reference_stationarity_boundary_exactly_5_retained():
    df = _masimo_df(pr_bpm=_STATIONARITY_BOUNDARY_PR, pi=[2.0] * 31)
    ref = hr_reference(df, 1_700_000_000, 1_700_000_031)
    assert ref["spread_bpm"] == pytest.approx(5.0, abs=1e-9)
    assert ref["stationarity_ok"] is True
    assert ref["admitted"] is True


def test_hr_reference_stationarity_boundary_5_000001_excluded():
    pr = list(_STATIONARITY_BOUNDARY_PR)
    pr[27] = 75.000001
    df = _masimo_df(pr_bpm=pr, pi=[2.0] * 31)
    ref = hr_reference(df, 1_700_000_000, 1_700_000_031)
    assert ref["spread_bpm"] == pytest.approx(5.000001, abs=1e-9)
    assert ref["stationarity_ok"] is False
    assert ref["admitted"] is False


def test_hr_reference_rejects_threshold_kwarg():
    """OSR-10: no keyword may move a primary HR threshold."""
    df = _masimo_df()
    with pytest.raises(TypeError):
        hr_reference(df, 1_700_000_000, 1_700_000_030, min_pi=0.9)  # type: ignore[call-arg]


# ── BR: availability-gate boundary at 24 ────────────────────────────────────────

def test_br_reference_availability_boundary_24_admits():
    rr = [15.0] * 24 + [float("nan")] * 6
    df = _masimo_df(rr_bpm=rr)
    ref = br_reference(df, 1_700_000_000, 1_700_000_030)
    assert ref["n_finite_rr"] == 24
    assert ref["availability_ok"] is True


def test_br_reference_availability_boundary_23_excludes():
    rr = [15.0] * 23 + [float("nan")] * 7
    df = _masimo_df(rr_bpm=rr)
    ref = br_reference(df, 1_700_000_000, 1_700_000_030)
    assert ref["n_finite_rr"] == 23
    assert ref["availability_ok"] is False
    assert ref["admitted"] is False


# n=31 boundary construction mirrors the HR stationarity boundary test above.
_BR_STATIONARITY_BOUNDARY_RR = [10.0] * 3 + [14.0] + [15.0] * 23 + [16.0] + [20.0] * 3


def test_br_reference_stationarity_boundary_exactly_2_retained():
    df = _masimo_df(rr_bpm=_BR_STATIONARITY_BOUNDARY_RR)
    ref = br_reference(df, 1_700_000_000, 1_700_000_031)
    assert ref["spread_bpm"] == pytest.approx(2.0, abs=1e-9)
    assert ref["stationarity_ok"] is True
    assert ref["admitted"] is True


def test_br_reference_stationarity_boundary_2_000001_excluded():
    rr = list(_BR_STATIONARITY_BOUNDARY_RR)
    rr[27] = 16.000001
    df = _masimo_df(rr_bpm=rr)
    ref = br_reference(df, 1_700_000_000, 1_700_000_031)
    assert ref["spread_bpm"] == pytest.approx(2.000001, abs=1e-9)
    assert ref["stationarity_ok"] is False
    assert ref["admitted"] is False


def test_br_reference_pi_never_gates():
    """PI is reported but must never affect availability_ok/stationarity_ok/admitted."""
    rr = [15.0] * 30
    pi_low = [0.05] * 30
    df = _masimo_df(rr_bpm=rr, pi=pi_low)
    ref = br_reference(df, 1_700_000_000, 1_700_000_030)
    assert ref["availability_ok"] is True
    assert ref["admitted"] is True
    assert ref["pi_median"] == pytest.approx(0.05)


def test_br_reference_rejects_threshold_kwarg():
    """OSR-10: no keyword may move a primary BR threshold."""
    df = _masimo_df()
    with pytest.raises(TypeError):
        br_reference(df, 1_700_000_000, 1_700_000_030, min_finite=10)  # type: ignore[call-arg]


# ── br_metronome_concordance arithmetic ─────────────────────────────────────────

def test_br_metronome_concordance_arithmetic():
    result = br_metronome_concordance(radar_br_bpm=18.0, commanded_rate_bpm=16.0)
    assert result["target_error_bpm"] == pytest.approx(2.0)
    assert result["radar_br_bpm"] == pytest.approx(18.0)
    assert result["commanded_rate_bpm"] == pytest.approx(16.0)


def test_br_metronome_concordance_nan_radar():
    result = br_metronome_concordance(radar_br_bpm=float("nan"), commanded_rate_bpm=16.0)
    assert np.isnan(result["target_error_bpm"])
