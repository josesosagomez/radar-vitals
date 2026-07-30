"""Tests for static clutter removal (src/clutter.py) and its wiring.

The load-bearing test here is `test_default_path_is_bit_identical`: the entire
justification for landing this now is that the default changes nothing. If that
test ever fails, this module has silently altered every existing result.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.clutter import (  # noqa: E402
    CLUTTER_METHODS,
    clutter_to_signal_db,
    remove_static_clutter,
)
from src.respiration import extract_chest_phase  # noqa: E402
from src.window_pipeline import run_window_dsp  # noqa: E402

FS = 20.0
N = 600
N_CHIRPS, N_RX, N_ADC = 4, 2, 64
BIN = 23


def _cube(clutter_amp: float, signal_amp: float = 1.0, seed: int = 7) -> np.ndarray:
    """Synthetic cube: a static reflector plus a breathing chest, both at BIN.

    Per-channel gains and fixed phase offsets differ, so a pooled (rather than
    per-channel) clutter estimate would leave a static residual behind.
    """
    rng = np.random.Generator(np.random.PCG64(seed))
    t = np.arange(N) / FS
    phi = 3.0 * np.sin(2 * np.pi * 0.25 * t)          # breathing, ~15 bpm
    tone = np.exp(2j * np.pi * BIN * np.arange(N_ADC) / N_ADC)

    gains = 1.0 + 0.3 * np.arange(N_CHIRPS * N_RX).reshape(N_CHIRPS, N_RX)
    offsets = 0.4 * np.arange(N_CHIRPS * N_RX).reshape(N_CHIRPS, N_RX)
    chan = (gains * np.exp(1j * offsets))[None, :, :]           # (1, chirps, rx)

    moving = (signal_amp * np.exp(1j * phi))[:, None, None] * chan   # (N, chirps, rx)
    static = clutter_amp * chan                                      # (1, chirps, rx)
    bin_series = moving + static

    cube = bin_series[:, :, :, None] * tone[None, None, None, :] / N_ADC
    cube = cube + 1e-4 * (rng.standard_normal(cube.shape) + 1j * rng.standard_normal(cube.shape))
    return cube.astype(np.complex64)


# ---------------------------------------------------------------------------
# The guarantee this whole change rests on
# ---------------------------------------------------------------------------

def test_default_path_is_bit_identical():
    """clutter_removal defaults to "none" and must not perturb a single bit."""
    cube = _cube(clutter_amp=5.0)
    for method in ("delta_before_mean", "mean_phasor"):
        baseline = extract_chest_phase(cube, BIN, method=method)
        explicit = extract_chest_phase(cube, BIN, method=method, clutter_removal="none")
        assert np.array_equal(baseline, explicit), f"{method}: default path perturbed"


def test_none_returns_the_same_object_not_a_copy():
    x = np.ones((4, 2, 2), dtype=np.complex64)
    assert remove_static_clutter(x, "none") is x


def test_run_window_dsp_defaults_when_config_key_absent():
    """A pre-2026-07-30 config has no `clutter_removal` key and must still run."""
    cfg = {
        "phase": {"method": "delta_before_mean", "impulse_clip_rad": 1.5},
        "respiration": {
            "band_hz": [0.10, 0.50], "detrend": "linear", "max_harmonics": 3,
            "harmonic_max_hz": 2.0, "stft_subwindow_s": 10.0, "stft_overlap": 0.5,
        },
    }
    assert "clutter_removal" not in cfg["phase"]
    cube = _cube(clutter_amp=2.0)
    out = run_window_dsp(cube, BIN, FS, _full_cfg(cfg))
    assert "br_bpm" in out


def _full_cfg(partial: dict) -> dict:
    """Fill the remaining sections run_window_dsp needs from the production config."""
    import yaml
    text = (REPO_ROOT / "scripts" / "live_demo_config.yaml").read_text(encoding="utf-8")
    cfg = yaml.safe_load(text)
    cfg["phase"] = partial["phase"]
    cfg["respiration"] = partial["respiration"]
    return cfg


# ---------------------------------------------------------------------------
# What the non-default actually does
# ---------------------------------------------------------------------------

def test_slow_time_mean_removes_the_static_component():
    cube = _cube(clutter_amp=8.0)
    from scipy.fft import fft as sp_fft
    win = np.hanning(N_ADC).astype(np.float32)
    bin_vals = sp_fft(cube * win, axis=3)[:, :, :, BIN]

    before = clutter_to_signal_db(bin_vals)
    after = clutter_to_signal_db(remove_static_clutter(bin_vals, "slow_time_mean"))

    assert before > 0, "fixture should be clutter-dominated"
    # The residual's own mean is zero to numerical precision.
    assert after < before - 100


def test_per_channel_not_pooled():
    """Channels have different fixed offsets; a pooled mean would leave a residual.

    Asserted RELATIVE to the magnitude actually removed. The cube is complex64 and
    the mean accumulates over 600 samples, so the achievable floor is a few times
    float32 eps (~1.2e-7), not an absolute epsilon — measured 6.3e-7 relative here.
    A pooled-mean implementation would leave a residual of order 1, i.e. ~1e6 times
    larger than this bound, so the test still discriminates sharply.
    """
    bin_vals = _bin_series(clutter_amp=6.0)
    removed = np.abs(bin_vals.mean(axis=0)).max()
    cleaned = remove_static_clutter(bin_vals, "slow_time_mean")
    residual = np.abs(cleaned.mean(axis=0)).max()    # per (chirp, rx)
    assert residual / removed < 1e-5, (
        f"per-(chirp,rx) means must each vanish, not merely their pooled average "
        f"(residual {residual:.3e} vs removed {removed:.3e})"
    )


def _bin_series(clutter_amp: float) -> np.ndarray:
    from scipy.fft import fft as sp_fft
    win = np.hanning(N_ADC).astype(np.float32)
    return sp_fft(_cube(clutter_amp) * win, axis=3)[:, :, :, BIN]


def test_removal_restores_compressed_phase_excursion():
    """The motivating effect: heavy clutter compresses phase; removing it un-compresses.

    Asserted as a direction and a wide band, not a precise value — this fixture is a
    first-order model of the compression, not a calibrated one.
    """
    cube = _cube(clutter_amp=20.0, signal_amp=1.0)
    compressed = extract_chest_phase(cube, BIN, clutter_removal="none")
    restored = extract_chest_phase(cube, BIN, clutter_removal="slow_time_mean")

    p2p_compressed = compressed.max() - compressed.min()
    p2p_restored = restored.max() - restored.min()
    assert p2p_compressed < 1.5, f"fixture not compressed enough: {p2p_compressed:.2f} rad"
    assert p2p_restored > 3.0 * p2p_compressed, (
        f"removal did not un-compress: {p2p_compressed:.2f} -> {p2p_restored:.2f} rad"
    )


def test_low_clutter_bin_is_barely_affected():
    """Where there is little static content, removal must be close to a no-op."""
    cube = _cube(clutter_amp=0.02)
    a = extract_chest_phase(cube, BIN, clutter_removal="none")
    b = extract_chest_phase(cube, BIN, clutter_removal="slow_time_mean")
    p2p_a = a.max() - a.min()
    p2p_b = b.max() - b.min()
    assert abs(p2p_a - p2p_b) / p2p_a < 0.10


# ---------------------------------------------------------------------------
# Contracts and failure modes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["mean", "SLOW_TIME_MEAN", "", None, "mti"])
def test_unknown_method_rejected(bad):
    with pytest.raises(ValueError, match="clutter_removal"):
        remove_static_clutter(np.ones((4, 2, 2), dtype=np.complex64), bad)


@pytest.mark.parametrize("bad", ["mean", "", "mti"])
def test_extract_chest_phase_rejects_unknown_method(bad):
    with pytest.raises(ValueError, match="clutter_removal"):
        extract_chest_phase(_cube(1.0), BIN, clutter_removal=bad)


def test_single_slow_time_sample_fails_loudly():
    """One sample is its own mean; subtracting yields zeros. Must raise, not zero out."""
    with pytest.raises(ValueError, match="at least 2 slow-time samples"):
        remove_static_clutter(np.ones((1, 2, 2), dtype=np.complex64), "slow_time_mean")


def test_wrong_rank_rejected():
    with pytest.raises(ValueError, match=r"\(N, chirps, rx\)"):
        remove_static_clutter(np.ones((4, 2), dtype=np.complex64), "slow_time_mean")


def test_clutter_to_signal_db_sign_convention():
    """Positive means static-dominated. Pins the sign so the diagnostic is readable."""
    heavy = clutter_to_signal_db(_bin_series(clutter_amp=10.0))
    light = clutter_to_signal_db(_bin_series(clutter_amp=0.05))
    assert heavy > 0 > light


def test_methods_tuple_is_the_single_source_of_truth():
    assert CLUTTER_METHODS == ("none", "slow_time_mean")
