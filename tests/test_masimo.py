"""Tests for src/masimo.py — half-open window interval [start, end)."""
import pytest
import pandas as pd
from src.masimo import load_masimo, window, reference_pr, reference_br


def _make_df(epochs, pr_bpm=None, pi=None):
    n = len(epochs)
    return pd.DataFrame({
        "epoch_utc": list(epochs),
        "pr_bpm": pr_bpm if pr_bpm is not None else [70.0] * n,
        "pi": pi if pi is not None else [1.0] * n,
        "spo2": [98] * n,
        "pvi": [15] * n,
        "rr_bpm": [15] * n,
    })


def test_endpoint_excluded():
    """window(100, 102) returns epochs [100, 101]; epoch 102 is excluded."""
    df = _make_df([100, 101, 102])
    w = window(df, 100, 102)
    assert list(w["epoch_utc"]) == [100, 101]


def test_adjacent_windows_no_duplicate():
    """Adjacent [100,102) and [102,104) partition without duplicating epoch 102."""
    df = _make_df([100, 101, 102, 103, 104])
    w1 = window(df, 100, 102)
    w2 = window(df, 102, 104)
    assert 102 not in w1["epoch_utc"].values
    assert 102 in w2["epoch_utc"].values
    assert len(w1) + len(w2) == 4


def test_reference_pr_mean_and_counts():
    """reference_pr over [100, 102) with PR [60, 70, 100] returns mean=65.0, n=2."""
    df = _make_df([100, 101, 102], pr_bpm=[60.0, 70.0, 100.0])
    result = reference_pr(df, 100, 102)
    assert result["pr_bpm_mean"] == pytest.approx(65.0)
    assert result["n_total"] == 2
    assert result["n_good_pi"] == 2


def test_low_pi_at_excluded_epoch_not_counted():
    """Low PI at excluded epoch 102 does not degrade the [100, 102) quality flag."""
    df = _make_df([100, 101, 102], pi=[1.0, 1.0, 0.1])
    result = reference_pr(df, 100, 102)
    assert result["low_quality"] is False
    assert result["n_good_pi"] == 2


def test_empty_interval_raises():
    """No Masimo rows in the interval raises ValueError."""
    df = _make_df([100, 101, 102])
    with pytest.raises(ValueError):
        reference_pr(df, 200, 210)


def test_invalid_interval_raises():
    """end_epoch <= start_epoch raises ValueError."""
    df = _make_df([100, 101, 102])
    with pytest.raises(ValueError):
        window(df, 102, 100)
    with pytest.raises(ValueError):
        window(df, 100, 100)


_CSV_HEADER = (
    "Session,Index,Timestamp,Date,Time,O2 Saturation,"
    "Beats / min,Perfusion Index,Pleth Variability,Breaths / min\n"
)


def test_load_masimo_deduplicates(tmp_path):
    """Duplicate epochs are merged: mean PR kept, single row per epoch."""
    csv = tmp_path / "test.csv"
    csv.write_text(
        _CSV_HEADER
        + "0,1,1000,1/1/25,12:00:00 PM,98,73,11,15,15\n"
        + "0,2,1000,1/1/25,12:00:00 PM,98,75,11,15,15\n"
        + "0,3,1001,1/1/25,12:00:01 PM,98,70,1.5,15,15\n"
    )
    df = load_masimo(csv)
    epoch_1000 = df[df["epoch_utc"] == 1000]
    assert len(epoch_1000) == 1
    assert epoch_1000["pr_bpm"].iloc[0] == pytest.approx(74.0)


def test_load_masimo_coverage_attrs(tmp_path):
    """Coverage attrs: one duplicate merged, one missing second reported."""
    csv = tmp_path / "test.csv"
    csv.write_text(
        _CSV_HEADER
        + "0,1,1000,1/1/25,12:00:00 PM,98,70,1.5,15,15\n"
        + "0,2,1001,1/1/25,12:00:01 PM,98,70,1.5,15,15\n"
        + "0,3,1001,1/1/25,12:00:01 PM,98,72,1.5,15,15\n"
        + "0,4,1003,1/1/25,12:00:03 PM,98,70,1.5,15,15\n"
    )
    df = load_masimo(csv)
    assert df.attrs["n_duplicates_merged"] == 1
    assert df.attrs["n_missing_seconds"] == 1


def test_reference_br_mean():
    """reference_br over [100, 102) with rr_bpm [15, 16, 17] returns 15.5."""
    df = pd.DataFrame({
        "epoch_utc": [100, 101, 102],
        "rr_bpm":    [15.0, 16.0, 17.0],
        "pr_bpm":    [70.0, 70.0, 70.0],
        "pi":        [1.0,  1.0,  1.0],
        "spo2":      [98,   98,   98],
        "pvi":       [15,   15,   15],
    })
    assert reference_br(df, 100, 102) == pytest.approx(15.5)
