"""New tests for phase-jump diagnostics added in exp004-windowlength branch.

Covers:
  - _phase_diff_stats: percentile computation, NaN/Inf handling, empty input
  - _soft_failure_overlap_counts: pairwise/multi-way overlaps, union, "only" counts
  - _subject_bin_phase_delta: mean_phasor / delta_before_mean methods
  - _flag_phase_jump_from_delta: basic correctness, wrapping, thresholding
  - _compute_quality_mask: method selection, invalid method error, new attrs/stats
  - TestH5 integration: method attr, phase-diff percentile attrs, overlap stats
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import h5py
import numpy as np
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.add_quality_mask import (  # noqa: E402
    _phase_diff_stats,
    _soft_failure_overlap_counts,
    _subject_bin_phase_delta,
    _subject_bin_complex,
    _flag_phase_jump_from_delta,
    _compute_quality_mask,
    _process_session,
)

CONFIG_PATH = REPO_ROOT / "scripts" / "quality_mask_config.yaml"
_CFG = yaml.safe_load(CONFIG_PATH.read_text())

N_CHIRPS  = 32
N_RX      = 4
N_SAMPLES = 256
LOCKED_BIN = 28


def _make_cube(n_frames: int, fill: complex = 100 + 100j) -> np.ndarray:
    return np.full((n_frames, N_CHIRPS, N_RX, N_SAMPLES), fill, dtype=np.complex64)


def _make_pj_cfg(
    enabled: bool = False,
    threshold: float = 0.5,
    locked_bin=None,
    method: str = "mean_phasor",
) -> dict:
    cfg = copy.deepcopy(_CFG)
    cfg.setdefault("soft_failures", {})["phase_jump"] = {
        "enabled":       enabled,
        "threshold_rad": threshold,
        "locked_bin":    locked_bin,
        "chunk_frames":  None,
        "method":        method,
    }
    cfg.get("soft_failures", {}).pop("motion_spike", None)
    cfg.get("soft_failures", {}).pop("subject_bin_snr", None)
    cfg.get("soft_failures", {}).pop("subject_bin_dropout", None)
    return cfg


def _make_complex_tone_cube(n_frames, locked_bin, *, amplitude=1000.0):
    n = np.arange(N_SAMPLES, dtype=np.float64)
    tone = amplitude * np.exp(
        1j * 2 * np.pi * locked_bin * n / N_SAMPLES
    ).astype(np.complex64)
    cube = np.zeros((n_frames, N_CHIRPS, N_RX, N_SAMPLES), dtype=np.complex64)
    cube[:, :, :, :] = tone
    return cube


# ===========================================================================
# _phase_diff_stats
# ===========================================================================

class TestPhaseDiffStats:

    def test_known_array_p50(self):
        arr = np.array([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9], dtype=np.float32)
        stats = _phase_diff_stats(arr)
        assert stats is not None
        assert stats["p50"] == pytest.approx(float(np.percentile(arr, 50)), rel=1e-4)

    def test_known_array_p90_p95_p99(self):
        rng = np.random.default_rng(42)
        arr = rng.uniform(0, np.pi, 1000).astype(np.float32)
        stats = _phase_diff_stats(arr)
        assert stats is not None
        assert stats["p90"] == pytest.approx(float(np.percentile(arr, 90)), rel=1e-3)
        assert stats["p95"] == pytest.approx(float(np.percentile(arr, 95)), rel=1e-3)
        assert stats["p99"] == pytest.approx(float(np.percentile(arr, 99)), rel=1e-3)

    def test_known_array_max_and_mean(self):
        arr = np.array([0.1, 0.5, 2.0, 1.0], dtype=np.float32)
        stats = _phase_diff_stats(arr)
        assert stats["max"]  == pytest.approx(2.0, rel=1e-5)
        assert stats["mean"] == pytest.approx(0.9, rel=1e-4)

    def test_nan_values_ignored(self):
        arr = np.array([0.1, np.nan, 0.3, np.nan, 0.5], dtype=np.float32)
        stats = _phase_diff_stats(arr)
        assert stats is not None
        assert stats["p50"] == pytest.approx(0.3, rel=1e-4)
        assert stats["max"] == pytest.approx(0.5, rel=1e-5)

    def test_inf_values_ignored(self):
        arr = np.array([0.1, np.inf, 0.2, -np.inf], dtype=np.float32)
        stats = _phase_diff_stats(arr)
        assert stats is not None
        assert stats["max"] == pytest.approx(0.2, rel=1e-5)
        assert stats["min"] == pytest.approx(0.1, rel=1e-5)

    def test_all_nan_returns_none(self):
        arr = np.array([np.nan, np.nan, np.nan], dtype=np.float32)
        assert _phase_diff_stats(arr) is None

    def test_empty_array_returns_none(self):
        arr = np.array([], dtype=np.float32)
        assert _phase_diff_stats(arr) is None

    def test_single_value(self):
        arr = np.array([1.57], dtype=np.float32)
        stats = _phase_diff_stats(arr)
        assert stats is not None
        assert stats["min"] == pytest.approx(1.57, rel=1e-4)
        assert stats["max"] == pytest.approx(1.57, rel=1e-4)
        assert stats["p50"] == pytest.approx(1.57, rel=1e-4)

    def test_returns_all_keys(self):
        arr = np.linspace(0, 1, 20, dtype=np.float32)
        stats = _phase_diff_stats(arr)
        for key in ("min", "p50", "p90", "p95", "p99", "max", "mean"):
            assert key in stats


# ===========================================================================
# _soft_failure_overlap_counts
# ===========================================================================

class TestSoftFailureOverlapCounts:

    def _make_results(self, **flags: np.ndarray) -> dict:
        """Build a minimal results dict with the given flag arrays."""
        return {k: v for k, v in flags.items()}

    def test_fewer_than_two_returns_empty(self):
        res = self._make_results(
            motion_spike_flagged=np.array([True, False, True])
        )
        assert _soft_failure_overlap_counts(res) == {}

    def test_two_no_overlap(self):
        motion  = np.array([True,  False, False, False])
        dropout = np.array([False, True,  False, False])
        res = self._make_results(
            motion_spike_flagged=motion,
            subject_bin_dropout_flagged=dropout,
        )
        counts = _soft_failure_overlap_counts(res)
        assert counts["motion_and_dropout"] == 0
        assert counts["motion_only"]        == 1
        assert counts["dropout_only"]       == 1
        assert counts["soft_failure_union"] == 2

    def test_two_partial_overlap(self):
        motion  = np.array([True,  True,  False, False])
        dropout = np.array([False, True,  True,  False])
        res = self._make_results(
            motion_spike_flagged=motion,
            subject_bin_dropout_flagged=dropout,
        )
        counts = _soft_failure_overlap_counts(res)
        assert counts["motion_and_dropout"] == 1
        assert counts["motion_only"]        == 1
        assert counts["dropout_only"]       == 1
        assert counts["soft_failure_union"] == 3

    def test_phase_jump_only_count_correct(self):
        n = 10
        motion     = np.zeros(n, dtype=bool)
        snr        = np.zeros(n, dtype=bool)
        dropout    = np.zeros(n, dtype=bool)
        phase_jump = np.zeros(n, dtype=bool)
        # Only phase_jump flags frame 5
        phase_jump[5] = True
        # Motion flags frames 0 and 1
        motion[[0, 1]] = True

        res = self._make_results(
            motion_spike_flagged=motion,
            snr_flagged=snr,
            subject_bin_dropout_flagged=dropout,
            phase_jump_flagged=phase_jump,
        )
        counts = _soft_failure_overlap_counts(res)
        assert counts["phase_jump_only"] == 1, (
            "phase_jump_only should be 1: only frame 5 is flagged by phase_jump alone"
        )
        assert counts["motion_only"] == 2

    def test_four_flags_known_overlaps(self):
        # 8 frames: each bit of the 3-bit index maps to a flag
        n = 8
        motion     = np.array([i & 1 for i in range(n)], dtype=bool)
        snr        = np.array([(i >> 1) & 1 for i in range(n)], dtype=bool)
        dropout    = np.zeros(n, dtype=bool)
        phase_jump = np.zeros(n, dtype=bool)
        # Frame 7: motion=1, snr=1, but not the last two
        res = self._make_results(
            motion_spike_flagged=motion,
            snr_flagged=snr,
            subject_bin_dropout_flagged=dropout,
            phase_jump_flagged=phase_jump,
        )
        counts = _soft_failure_overlap_counts(res)
        # motion: frames 1,3,5,7 → 4; snr: frames 2,3,6,7 → 4; overlap: frames 3,7 → 2
        assert counts["motion"] == 4
        assert counts["snr"]    == 4
        assert counts["motion_and_snr"] == 2
        assert counts["soft_failure_union"] == int((motion | snr).sum())

    def test_union_count_correct(self):
        a = np.array([True, False, True, False])
        b = np.array([False, True, True, False])
        res = self._make_results(
            motion_spike_flagged=a,
            phase_jump_flagged=b,
        )
        counts = _soft_failure_overlap_counts(res)
        assert counts["soft_failure_union"] == 3

    def test_missing_optional_flags_no_crash(self):
        # Only snr and dropout present (no motion, no phase_jump)
        snr     = np.array([True, False, False])
        dropout = np.array([False, True,  False])
        res = self._make_results(
            snr_flagged=snr,
            subject_bin_dropout_flagged=dropout,
        )
        counts = _soft_failure_overlap_counts(res)
        assert "snr_only"     in counts
        assert "dropout_only" in counts
        assert "snr_and_dropout" in counts


# ===========================================================================
# _flag_phase_jump_from_delta
# ===========================================================================

class TestFlagPhaseJumpFromDelta:
    N = 20

    def test_constant_delta_zero_no_flags(self):
        delta = np.zeros(self.N, dtype=np.float32)
        flagged, diff, out_delta = _flag_phase_jump_from_delta(delta, threshold_rad=0.5)
        assert not flagged.any()
        assert (diff == 0.0).all()

    def test_positive_jump_flagged(self):
        delta = np.zeros(self.N, dtype=np.float32)
        delta[10] = 1.6
        flagged, diff, _ = _flag_phase_jump_from_delta(delta, threshold_rad=1.5)
        assert flagged[10]
        assert not flagged[:10].any()
        assert not flagged[11:].any()

    def test_negative_jump_flagged(self):
        delta = np.zeros(self.N, dtype=np.float32)
        delta[5] = -2.0
        flagged, diff, _ = _flag_phase_jump_from_delta(delta, threshold_rad=1.5)
        assert flagged[5]
        assert diff[5] == pytest.approx(2.0, rel=1e-5)

    def test_exactly_at_threshold_not_flagged(self):
        delta = np.zeros(self.N, dtype=np.float32)
        delta[3] = 1.5
        flagged, diff, _ = _flag_phase_jump_from_delta(delta, threshold_rad=1.5)
        assert not flagged[3], "strict > means equal threshold must not flag"

    def test_phase_diff_equals_abs_delta(self):
        delta = np.array([-1.0, 0.0, 2.0, -0.5], dtype=np.float32)
        _, diff, out_delta = _flag_phase_jump_from_delta(delta, threshold_rad=3.0)
        np.testing.assert_allclose(diff, np.abs(delta), rtol=1e-6)

    def test_phase_delta_zero_at_frame_zero(self):
        # Only meaningful if input has 0 at frame 0 (contract of the callers)
        delta = np.zeros(10, dtype=np.float32)
        _, diff, out_delta = _flag_phase_jump_from_delta(delta, 0.5)
        assert diff[0]      == pytest.approx(0.0, abs=1e-7)
        assert out_delta[0] == pytest.approx(0.0, abs=1e-7)

    def test_output_shapes_and_dtypes(self):
        delta = np.zeros(15, dtype=np.float32)
        flagged, diff, out_delta = _flag_phase_jump_from_delta(delta, 0.5)
        assert flagged.shape    == (15,)
        assert diff.shape       == (15,)
        assert out_delta.shape  == (15,)
        assert flagged.dtype    == bool
        assert diff.dtype       == np.float32
        assert out_delta.dtype  == np.float32

    def test_threshold_zero_raises(self):
        with pytest.raises(ValueError, match="threshold_rad"):
            _flag_phase_jump_from_delta(np.zeros(5), 0.0)

    def test_threshold_negative_raises(self):
        with pytest.raises(ValueError, match="threshold_rad"):
            _flag_phase_jump_from_delta(np.zeros(5), -1.0)


# ===========================================================================
# _subject_bin_phase_delta
# ===========================================================================

class TestSubjectBinPhaseDelta:
    N_FRAMES   = 40
    N_CHIRPS   = 4
    N_RX       = 4
    N_SAMPLES  = 64
    LOCKED_BIN = 8

    def _tone_cube(self, amplitude=1.0):
        n = np.arange(self.N_SAMPLES, dtype=np.float64)
        tone = amplitude * np.exp(
            1j * 2 * np.pi * self.LOCKED_BIN * n / self.N_SAMPLES
        ).astype(np.complex64)
        cube = np.broadcast_to(
            tone, (self.N_FRAMES, self.N_CHIRPS, self.N_RX, self.N_SAMPLES)
        ).copy()
        return cube

    # --- mean_phasor ---

    def test_mean_phasor_returns_complex_series(self):
        cube = self._tone_cube()
        phase_delta, z = _subject_bin_phase_delta(
            cube, self.LOCKED_BIN, method="mean_phasor"
        )
        assert z is not None
        assert z.shape == (self.N_FRAMES,)
        assert z.dtype == np.complex64

    def test_mean_phasor_matches_subject_bin_complex(self):
        cube = self._tone_cube()
        phase_delta, z = _subject_bin_phase_delta(
            cube, self.LOCKED_BIN, method="mean_phasor"
        )
        z_ref = _subject_bin_complex(cube, self.LOCKED_BIN)
        np.testing.assert_allclose(z.real, z_ref.real, rtol=1e-5)
        np.testing.assert_allclose(z.imag, z_ref.imag, rtol=1e-5)

    def test_mean_phasor_constant_tone_small_delta(self):
        # Constant phase tone → frame-to-frame deltas should be near 0
        cube = self._tone_cube()
        phase_delta, _ = _subject_bin_phase_delta(
            cube, self.LOCKED_BIN, method="mean_phasor"
        )
        assert phase_delta.shape == (self.N_FRAMES,)
        # All deltas near zero for a constant-phase series
        np.testing.assert_allclose(phase_delta[1:], 0.0, atol=1e-4)

    def test_mean_phasor_phase_delta_zero_at_frame_zero(self):
        cube = self._tone_cube()
        phase_delta, _ = _subject_bin_phase_delta(
            cube, self.LOCKED_BIN, method="mean_phasor"
        )
        assert float(phase_delta[0]) == pytest.approx(0.0, abs=1e-7)

    # --- delta_before_mean ---

    def test_delta_before_mean_returns_none_complex_series(self):
        cube = self._tone_cube()
        phase_delta, z = _subject_bin_phase_delta(
            cube, self.LOCKED_BIN, method="delta_before_mean"
        )
        assert z is None

    def test_delta_before_mean_shape_and_dtype(self):
        cube = self._tone_cube()
        phase_delta, _ = _subject_bin_phase_delta(
            cube, self.LOCKED_BIN, method="delta_before_mean"
        )
        assert phase_delta.shape == (self.N_FRAMES,)
        assert phase_delta.dtype == np.float32

    def test_delta_before_mean_frame_zero_is_zero(self):
        cube = self._tone_cube()
        phase_delta, _ = _subject_bin_phase_delta(
            cube, self.LOCKED_BIN, method="delta_before_mean"
        )
        assert float(phase_delta[0]) == pytest.approx(0.0, abs=1e-7)

    def test_delta_before_mean_constant_tone_small_delta(self):
        cube = self._tone_cube()
        phase_delta, _ = _subject_bin_phase_delta(
            cube, self.LOCKED_BIN, method="delta_before_mean"
        )
        np.testing.assert_allclose(phase_delta[1:], 0.0, atol=1e-4)

    def test_chunked_and_unchunked_mean_phasor_match(self):
        cube = self._tone_cube()
        pd_full, _  = _subject_bin_phase_delta(
            cube, self.LOCKED_BIN, method="mean_phasor"
        )
        pd_chunk, _ = _subject_bin_phase_delta(
            cube, self.LOCKED_BIN, method="mean_phasor", chunk_frames=7
        )
        np.testing.assert_allclose(pd_full, pd_chunk, atol=1e-5)

    def test_chunked_and_unchunked_delta_before_mean_match(self):
        cube = self._tone_cube()
        pd_full, _  = _subject_bin_phase_delta(
            cube, self.LOCKED_BIN, method="delta_before_mean"
        )
        pd_chunk, _ = _subject_bin_phase_delta(
            cube, self.LOCKED_BIN, method="delta_before_mean", chunk_frames=7
        )
        np.testing.assert_allclose(pd_full, pd_chunk, atol=1e-5)

    def test_chunk_boundary_does_not_reset_to_zero(self):
        """The first frame of each chunk (except the very first) must compare against
        the last frame of the previous chunk, not produce a spurious zero delta."""
        cube = self._tone_cube()
        # Inject a phase step at frame 7 (which crosses a chunk boundary for chunk_frames=7)
        k = self.LOCKED_BIN
        n = np.arange(self.N_SAMPLES, dtype=np.float64)
        rotated = np.exp(1j * 2 * np.pi * k * n / self.N_SAMPLES) * np.exp(1j * 2.0)
        cube[7:, :, :, :] = rotated.astype(np.complex64)

        pd_full, _  = _subject_bin_phase_delta(
            cube, self.LOCKED_BIN, method="delta_before_mean"
        )
        pd_chunk, _ = _subject_bin_phase_delta(
            cube, self.LOCKED_BIN, method="delta_before_mean", chunk_frames=7
        )
        # Frame 7 should show the step; it must not be zero in the chunked path
        assert abs(float(pd_chunk[7])) > 1.0, (
            "Chunk boundary frame should detect the phase step, not reset to zero"
        )
        np.testing.assert_allclose(pd_full, pd_chunk, atol=1e-4)

    def test_invalid_method_raises(self):
        cube = self._tone_cube()
        with pytest.raises(ValueError, match="method"):
            _subject_bin_phase_delta(cube, self.LOCKED_BIN, method="invalid_method")

    def test_non_integer_locked_bin_raises(self):
        cube = self._tone_cube()
        with pytest.raises(ValueError, match="integer"):
            _subject_bin_phase_delta(cube, 8.5, method="mean_phasor")

    def test_out_of_range_locked_bin_raises(self):
        cube = self._tone_cube()
        with pytest.raises(ValueError, match="out of range"):
            _subject_bin_phase_delta(cube, self.N_SAMPLES, method="mean_phasor")

    def test_chunk_frames_zero_raises(self):
        cube = self._tone_cube()
        with pytest.raises(ValueError, match="chunk_frames"):
            _subject_bin_phase_delta(cube, self.LOCKED_BIN, chunk_frames=0)

    def test_real_cube_uses_rfft(self):
        n = np.arange(self.N_SAMPLES, dtype=np.float64)
        tone = np.cos(
            2 * np.pi * self.LOCKED_BIN * n / self.N_SAMPLES
        ).astype(np.float32)
        cube = np.broadcast_to(
            tone, (self.N_FRAMES, self.N_CHIRPS, self.N_RX, self.N_SAMPLES)
        ).copy()
        assert not np.iscomplexobj(cube)
        pd, _ = _subject_bin_phase_delta(cube, self.LOCKED_BIN, method="mean_phasor")
        assert pd.shape == (self.N_FRAMES,)
        pd2, _ = _subject_bin_phase_delta(
            cube, self.LOCKED_BIN, method="delta_before_mean"
        )
        assert pd2.shape == (self.N_FRAMES,)

    def test_delta_before_mean_more_stable_than_mean_phasor(self):
        """Construct two RX channels with opposite static phase offsets.

        When opposite offsets partially cancel the mean phasor, mean_phasor
        can produce a near-zero complex_series and an unreliable phase estimate.
        delta_before_mean averages the *deltas*, not the phasors, so it stays
        stable even when the mean phasor amplitude is near zero.
        """
        n_frames = 30
        n_chirps = 2
        n_rx     = 2
        n_samp   = 64
        locked   = 8

        n = np.arange(n_samp, dtype=np.float64)
        base_tone = np.exp(1j * 2 * np.pi * locked * n / n_samp).astype(np.complex64)

        # Known frame-to-frame phase step of 0.3 rad
        step_rad = 0.3
        cube = np.zeros((n_frames, n_chirps, n_rx, n_samp), dtype=np.complex64)
        for i in range(n_frames):
            # Both channels carry the same phase progression
            # but with opposite static offsets: +π/2 vs -π/2 → mean phasor ≈ 0
            cube[i, :, 0, :] = base_tone * np.exp(1j * (i * step_rad + np.pi / 2))
            cube[i, :, 1, :] = base_tone * np.exp(1j * (i * step_rad - np.pi / 2))

        # mean_phasor: the mean across rx channels partially cancels
        pd_mp, z = _subject_bin_phase_delta(cube, locked, method="mean_phasor")
        # delta_before_mean: computes per-rx delta first, then averages
        pd_dm, _ = _subject_bin_phase_delta(cube, locked, method="delta_before_mean")

        # delta_before_mean should recover the known step for frames 1..
        np.testing.assert_allclose(pd_dm[1:], step_rad, atol=1e-3, err_msg=(
            "delta_before_mean should recover the known phase step even with "
            "opposite RX offsets"
        ))

        # mean_phasor may or may not work (depends on how much cancellation occurs),
        # but the implementation must not crash regardless
        assert pd_mp.shape == (n_frames,)


# ===========================================================================
# _compute_quality_mask — phase_jump.method integration
# ===========================================================================

class TestComputeQualityMaskPhaseJumpMethod:
    LOCKED_BIN = 28
    N          = 60

    def _cube(self):
        """Stable complex tone cube."""
        return _make_complex_tone_cube(self.N, self.LOCKED_BIN)

    def test_mean_phasor_method_works(self):
        cube = self._cube()
        cfg  = _make_pj_cfg(enabled=False, threshold=0.5, method="mean_phasor")
        res  = _compute_quality_mask(cube, cfg, locked_bin=self.LOCKED_BIN)
        assert "phase_jump_flagged" in res
        assert res.get("phase_jump_method") == "mean_phasor"

    def test_delta_before_mean_method_works(self):
        cube = self._cube()
        cfg  = _make_pj_cfg(enabled=False, threshold=0.5, method="delta_before_mean")
        res  = _compute_quality_mask(cube, cfg, locked_bin=self.LOCKED_BIN)
        assert "phase_jump_flagged" in res
        assert res.get("phase_jump_method") == "delta_before_mean"

    def test_invalid_method_raises_value_error(self):
        cube = self._cube()
        cfg  = _make_pj_cfg(enabled=False, threshold=0.5, method="wrong_method")
        with pytest.raises(ValueError, match="method"):
            _compute_quality_mask(cube, cfg, locked_bin=self.LOCKED_BIN)

    def test_phase_diff_stats_present_when_ran(self):
        cube = self._cube()
        cfg  = _make_pj_cfg(enabled=False, threshold=0.5)
        res  = _compute_quality_mask(cube, cfg, locked_bin=self.LOCKED_BIN)
        assert "phase_diff_stats" in res
        assert res["phase_diff_stats"] is not None
        for key in ("min", "p50", "p90", "p95", "p99", "max", "mean"):
            assert key in res["phase_diff_stats"]

    def test_phase_diff_stats_absent_when_skipped(self):
        cube = self._cube()
        cfg  = _make_pj_cfg(enabled=True, threshold=0.5)
        # No locked_bin → check skips
        res  = _compute_quality_mask(cube, cfg, locked_bin=None)
        assert res["phase_jump_skipped"] is True
        assert "phase_diff_stats" not in res

    def test_soft_failure_overlaps_computed_when_multiple_flags(self):
        """With phase_jump + motion_spike both present, overlaps should be computed."""
        cube = _make_complex_tone_cube(self.N, self.LOCKED_BIN)
        cfg  = copy.deepcopy(_CFG)
        cfg.get("soft_failures", {}).pop("subject_bin_snr", None)
        cfg.get("soft_failures", {}).pop("subject_bin_dropout", None)
        cfg["soft_failures"]["motion_spike"]["locked_bin"] = self.LOCKED_BIN
        cfg.setdefault("soft_failures", {})["phase_jump"] = {
            "enabled": False, "threshold_rad": 0.5,
            "locked_bin": None, "chunk_frames": None, "method": "mean_phasor",
        }
        res  = _compute_quality_mask(cube, cfg, locked_bin=self.LOCKED_BIN)
        from scripts.add_quality_mask import _soft_failure_overlap_counts
        overlaps = _soft_failure_overlap_counts(res)
        assert "soft_failure_union" in overlaps

    def test_method_stored_even_when_block_skipped(self):
        cube = self._cube()
        cfg  = _make_pj_cfg(enabled=True, threshold=0.5, method="delta_before_mean")
        res  = _compute_quality_mask(cube, cfg, locked_bin=None)
        assert res.get("phase_jump_method") == "delta_before_mean"

    def test_block_absent_no_method_key(self):
        cube = self._cube()
        cfg  = copy.deepcopy(_CFG)
        cfg.get("soft_failures", {}).pop("phase_jump", None)
        cfg.get("soft_failures", {}).pop("motion_spike", None)
        cfg.get("soft_failures", {}).pop("subject_bin_snr", None)
        cfg.get("soft_failures", {}).pop("subject_bin_dropout", None)
        res  = _compute_quality_mask(cube, cfg, locked_bin=self.LOCKED_BIN)
        assert "phase_jump_method" not in res


# ===========================================================================
# HDF5 round-trip: new attrs (method, phase-diff percentiles)
# ===========================================================================

class TestH5RoundTripPhaseJumpNewAttrs:
    N_FRAMES   = 200
    LOCKED_BIN = 28
    N_TRIM     = 600

    def _write_h5(self, path: Path, n_analysis: int) -> None:
        n_total = self.N_TRIM + n_analysis
        k       = self.LOCKED_BIN
        samp    = np.arange(N_SAMPLES, dtype=np.float64)
        tone    = (5000.0 * np.exp(
            1j * 2 * np.pi * k * samp / N_SAMPLES
        )).astype(np.complex64)
        rng  = np.random.default_rng(seed=99)
        cube = (
            rng.standard_normal((n_total, N_CHIRPS, N_RX, N_SAMPLES)).astype(np.float32)
            + 1j * rng.standard_normal((n_total, N_CHIRPS, N_RX, N_SAMPLES)).astype(np.float32)
        ).astype(np.complex64) * 10
        cube[:, :, :, :] += tone
        with h5py.File(path, "w") as f:
            f.create_dataset("cube", data=cube)
            f.attrs["num_frames"]    = n_total
            f.attrs["frame_rate_hz"] = 20.0
            f.attrs["session_id"]    = path.stem
            f.attrs["posture"]       = "sit"
            f.attrs["distance_cm"]   = 140

    def _pj_cfg(self, method: str = "mean_phasor") -> dict:
        cfg = copy.deepcopy(_CFG)
        cfg.get("soft_failures", {}).pop("subject_bin_snr", None)
        cfg.get("soft_failures", {}).pop("motion_spike", None)
        cfg.get("soft_failures", {}).pop("subject_bin_dropout", None)
        cfg.setdefault("soft_failures", {})["phase_jump"] = {
            "enabled":       True,
            "threshold_rad": 1.5,
            "locked_bin":    None,
            "chunk_frames":  None,
            "method":        method,
        }
        return cfg

    def test_phase_jump_method_attr_written(self, tmp_path):
        h5 = tmp_path / "exp_pj_method.h5"
        self._write_h5(h5, self.N_FRAMES)
        cfg = self._pj_cfg(method="mean_phasor")

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_pj_method", cfg, commit="test",
                             locked_bin=self.LOCKED_BIN)
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            assert f["quality_mask"].attrs["phase_jump_method"] == "mean_phasor"

    def test_delta_before_mean_method_attr_written(self, tmp_path):
        h5 = tmp_path / "exp_pj_dbm.h5"
        self._write_h5(h5, self.N_FRAMES)
        cfg = self._pj_cfg(method="delta_before_mean")

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_pj_dbm", cfg, commit="test",
                             locked_bin=self.LOCKED_BIN)
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            assert f["quality_mask"].attrs["phase_jump_method"] == "delta_before_mean"

    def test_phase_diff_percentile_attrs_written(self, tmp_path):
        h5 = tmp_path / "exp_pj_pct.h5"
        self._write_h5(h5, self.N_FRAMES)
        cfg = self._pj_cfg()

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_pj_pct", cfg, commit="test",
                             locked_bin=self.LOCKED_BIN)
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            attrs = dict(f["quality_mask"].attrs)
            for key in ("p50", "p90", "p95", "p99", "max", "min", "mean"):
                attr_name = f"phase_jump_phase_diff_{key}"
                assert attr_name in attrs, f"missing attr {attr_name}"
                assert np.isfinite(float(attrs[attr_name]))

    def test_no_none_attrs_stored(self, tmp_path):
        """Python None must never be stored as an HDF5 attr."""
        h5 = tmp_path / "exp_pj_nonone.h5"
        self._write_h5(h5, self.N_FRAMES)
        cfg = self._pj_cfg()

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_pj_nonone", cfg, commit="test",
                             locked_bin=self.LOCKED_BIN)
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            for key, val in f["quality_mask"].attrs.items():
                assert val is not None, f"attr '{key}' is None"
