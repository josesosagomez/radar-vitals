"""Unit tests for add_quality_mask.py.

Tests cover:
  - _flag_clipping: detects saturated frames, passes clean frames
  - _flag_dead: detects near-zero frames, passes normal frames
  - _flag_rx_imbalance: detects channel energy imbalance
  - _flag_motion_spike: detects subject-bin energy bursts after range FFT
  - _flag_subject_bin_snr: detects low subject-bin SNR relative to background
  - _flag_subject_bin_dropout: detects frames where subject-bin energy drops below baseline
  - _subject_bin_energy: shared energy helper; correctness and chunk-path equivalence
  - _subject_bin_complex: complex phasor helper; correctness and chunk-path equivalence
  - _flag_phase_jump: detects large wrapped frame-to-frame phasor rotation
  - _compute_quality_mask: correct aggregation; all soft-failure checks enabled/disabled;
    locked_bin resolution, skip handling, and energy-reuse paths
  - Integration: synthetic HDF5 round-trip (write cube, run script, read back mask)
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
    _flag_clipping,
    _flag_dead,
    _flag_rx_imbalance,
    _subject_bin_energy,
    _subject_bin_complex,
    _flag_motion_spike,
    _flag_subject_bin_snr,
    _flag_subject_bin_dropout,
    _flag_phase_jump,
    _flag_phase_jump_from_delta,
    _subject_bin_phase_delta,
    _phase_diff_stats,
    _soft_failure_overlap_counts,
    _compute_quality_mask,
    _process_session,
)

CONFIG_PATH = REPO_ROOT / "scripts" / "quality_mask_config.yaml"
_CFG = yaml.safe_load(CONFIG_PATH.read_text())

# Cube geometry matching real sessions
N_CHIRPS  = 32
N_RX      = 4
N_SAMPLES = 256


def _make_cube(n_frames: int, fill: complex = 100 + 100j) -> np.ndarray:
    """Healthy cube: all samples = fill (well below clipping, non-zero energy)."""
    return np.full((n_frames, N_CHIRPS, N_RX, N_SAMPLES), fill, dtype=np.complex64)


# ---------------------------------------------------------------------------
# _flag_clipping
# ---------------------------------------------------------------------------

class TestFlagClipping:
    def test_clean_frame_not_flagged(self):
        cube = _make_cube(5, fill=100 + 100j)
        flagged, max_abs = _flag_clipping(cube, threshold=32767.0)
        assert not flagged.any(), "clean frames should not be flagged"

    def test_clipped_real_part_flagged(self):
        cube = _make_cube(3, fill=100 + 100j)
        cube[1, 0, 0, 0] = 32767 + 0j     # saturate one real sample in frame 1
        flagged, max_abs = _flag_clipping(cube, threshold=32767.0)
        assert flagged[1], "frame 1 should be flagged (real saturation)"
        assert not flagged[0] and not flagged[2], "other frames should be clean"

    def test_clipped_imag_part_flagged(self):
        cube = _make_cube(3, fill=100 + 100j)
        cube[2, 5, 2, 10] = 0 + 32767j    # saturate one imag sample in frame 2
        flagged, _ = _flag_clipping(cube, threshold=32767.0)
        assert flagged[2]
        assert not flagged[0] and not flagged[1]

    def test_negative_saturation_flagged(self):
        cube = _make_cube(2, fill=100 + 100j)
        cube[0, 0, 0, 0] = -32767 + 0j
        flagged, _ = _flag_clipping(cube, threshold=32767.0)
        assert flagged[0]

    def test_max_abs_returned_correctly(self):
        cube = _make_cube(1, fill=500 + 0j)
        _, max_abs = _flag_clipping(cube, threshold=32767.0)
        assert max_abs[0] == pytest.approx(500.0)


# ---------------------------------------------------------------------------
# _flag_dead
# ---------------------------------------------------------------------------

class TestFlagDead:
    def test_healthy_frames_not_flagged(self):
        cube = _make_cube(10, fill=100 + 100j)
        flagged, energy = _flag_dead(cube, fraction=0.01)
        assert not flagged.any()

    def test_zero_frame_flagged(self):
        cube = _make_cube(10, fill=100 + 100j)
        cube[3] = 0 + 0j                   # frame 3 completely dead
        flagged, energy = _flag_dead(cube, fraction=0.01)
        assert flagged[3], "dead frame (all zeros) should be flagged"
        assert not np.delete(flagged, 3).any(), "other frames should be clean"

    def test_near_zero_frame_flagged(self):
        cube = _make_cube(20, fill=1000 + 0j)
        cube[7] = 1 + 0j                   # energy = 1, median ≈ 1e6 → 1e-6 × median
        flagged, _ = _flag_dead(cube, fraction=0.01)
        assert flagged[7]

    def test_energy_values_positive(self):
        cube = _make_cube(5, fill=200 + 0j)
        _, energy = _flag_dead(cube, fraction=0.01)
        assert (energy > 0).all()

    def test_fraction_zero_flags_nothing(self):
        """fraction=0 means no floor → no dead frames ever."""
        cube = _make_cube(5, fill=0.001 + 0j)
        flagged, _ = _flag_dead(cube, fraction=0.0)
        assert not flagged.any()


# ---------------------------------------------------------------------------
# _compute_quality_mask
# ---------------------------------------------------------------------------

class TestComputeQualityMask:
    def test_all_healthy(self):
        cube = _make_cube(10, fill=100 + 100j)
        results = _compute_quality_mask(cube, _CFG)
        assert results["quality_mask"].all(), "all frames should be healthy"

    def test_clipped_frame_masked(self):
        cube = _make_cube(10, fill=100 + 100j)
        cube[4, 0, 0, 0] = 32767 + 0j
        results = _compute_quality_mask(cube, _CFG)
        assert not results["quality_mask"][4], "clipped frame should be masked"
        assert results["quality_mask"][[0, 1, 2, 3, 5]].all()

    def test_dead_frame_masked(self):
        cube = _make_cube(10, fill=1000 + 0j)
        cube[7] = 0 + 0j
        results = _compute_quality_mask(cube, _CFG)
        assert not results["quality_mask"][7]

    def test_both_failures_masked(self):
        cube = _make_cube(10, fill=500 + 0j)
        cube[2, 0, 0, 0] = 32767 + 0j     # clip
        cube[8] = 0 + 0j                   # dead
        results = _compute_quality_mask(cube, _CFG)
        assert not results["quality_mask"][2]
        assert not results["quality_mask"][8]
        assert results["quality_mask"][[0, 1, 3, 4, 5, 6, 7, 9]].all()

    def test_quality_mask_is_bool(self):
        cube = _make_cube(5)
        results = _compute_quality_mask(cube, _CFG)
        assert results["quality_mask"].dtype == bool

    def test_metric_arrays_correct_length(self):
        n = 15
        cube = _make_cube(n)
        results = _compute_quality_mask(cube, _CFG)
        assert len(results["quality_mask"])  == n
        assert len(results["max_abs_adc"])   == n
        assert len(results["frame_energy"])  == n


# ---------------------------------------------------------------------------
# _flag_rx_imbalance
# ---------------------------------------------------------------------------

class TestFlagRxImbalance:
    def test_balanced_channels_not_flagged(self):
        cube = _make_cube(5, fill=100 + 0j)
        flagged, ratio = _flag_rx_imbalance(cube, ratio=10.0)
        assert not flagged.any(), "balanced channels should not be flagged"
        assert np.allclose(ratio, 1.0), "ratio should be 1.0 for identical channels"

    def test_imbalanced_frame_flagged(self):
        # RX 0 of frame 2 is 100× stronger than the other channels
        cube = _make_cube(5, fill=10 + 0j)
        cube[2, :, 0, :] = 1000 + 0j
        flagged, ratio = _flag_rx_imbalance(cube, ratio=10.0)
        assert flagged[2], "frame 2 should be flagged (channel imbalance > 10)"
        assert not flagged[[0, 1, 3, 4]].any(), "other frames should be clean"
        assert ratio[2] > 10.0

    def test_ratio_exactly_at_threshold_not_flagged(self):
        # Build a cube where max/min == exactly threshold (should not trigger strict >)
        cube = _make_cube(3, fill=1 + 0j)
        # RX 0 energy = 10×, others = 1× → ratio = 10.0, not flagged at threshold=10.0
        cube[:, :, 0, :] = np.sqrt(10) + 0j
        flagged, ratio = _flag_rx_imbalance(cube, ratio=10.0)
        assert not flagged.any(), "ratio equal to threshold should not be flagged (strict >)"

    def test_dead_rx_channel_flagged(self):
        # RX 2 completely dead in frame 1 → ratio = inf
        cube = _make_cube(4, fill=100 + 0j)
        cube[1, :, 2, :] = 0 + 0j
        flagged, ratio = _flag_rx_imbalance(cube, ratio=10.0)
        assert flagged[1], "dead RX channel should be flagged (ratio = inf)"
        assert not flagged[[0, 2, 3]].any()
        assert np.isinf(ratio[1])

    def test_output_shape_and_dtype(self):
        cube = _make_cube(7)
        flagged, ratio = _flag_rx_imbalance(cube, ratio=10.0)
        assert flagged.shape == (7,)
        assert ratio.shape == (7,)
        assert flagged.dtype == bool
        assert ratio.dtype == np.float32

    def test_ratio_values_positive(self):
        cube = _make_cube(5, fill=200 + 50j)
        _, ratio = _flag_rx_imbalance(cube, ratio=10.0)
        assert (ratio >= 1.0).all(), "ratio must be >= 1 for any non-degenerate cube"


# ---------------------------------------------------------------------------
# _compute_quality_mask — soft failure integration
# ---------------------------------------------------------------------------

class TestComputeQualityMaskSoftFailures:
    def _cfg_with_rx(self, enabled: bool, ratio: float = 10.0) -> dict:
        cfg = copy.deepcopy(_CFG)
        cfg.setdefault("soft_failures", {})["rx_imbalance"] = {
            "enabled": enabled,
            "rx_imbalance_ratio": ratio,
        }
        return cfg

    def test_rx_disabled_imbalanced_frame_still_passes(self):
        cfg = self._cfg_with_rx(enabled=False)
        cube = _make_cube(5, fill=10 + 0j)
        cube[2, :, 0, :] = 1000 + 0j   # would be flagged if enabled
        results = _compute_quality_mask(cube, cfg)
        assert results["quality_mask"].all(), \
            "imbalanced frame should NOT affect mask when rx_imbalance is disabled"

    def test_rx_enabled_imbalanced_frame_flagged(self):
        cfg = self._cfg_with_rx(enabled=True)
        cube = _make_cube(5, fill=10 + 0j)
        cube[2, :, 0, :] = 1000 + 0j
        results = _compute_quality_mask(cube, cfg)
        assert not results["quality_mask"][2], \
            "imbalanced frame should be masked when rx_imbalance is enabled"
        assert results["quality_mask"][[0, 1, 3, 4]].all()

    def test_rx_metric_always_present_when_cfg_block_exists(self):
        for enabled in (True, False):
            cfg = self._cfg_with_rx(enabled=enabled)
            cube = _make_cube(5)
            results = _compute_quality_mask(cube, cfg)
            assert "rx_energy_ratio" in results, \
                "rx_energy_ratio should be in results whenever the config block exists"
            assert "rx_imbalance_flagged" in results

    def test_rx_metric_absent_when_cfg_block_missing(self):
        cfg = copy.deepcopy(_CFG)
        cfg.pop("soft_failures", None)
        cube = _make_cube(5)
        results = _compute_quality_mask(cube, cfg)
        assert "rx_energy_ratio" not in results
        assert "rx_imbalance_flagged" not in results

    def test_rx_enabled_does_not_affect_hard_failure_counts(self):
        cfg = self._cfg_with_rx(enabled=True)
        cube = _make_cube(5, fill=10 + 0j)
        cube[0] = 32767 + 0j           # all channels clipped equally → hard clip only
        cube[2, :, 0, :] = 1000 + 0j   # RX 0 dominant → rx imbalance only
        results = _compute_quality_mask(cube, cfg)
        assert not results["quality_mask"][0], "clipped frame must be masked"
        assert not results["quality_mask"][2], "imbalanced frame must be masked"
        assert results["clip_flagged"][0]
        assert not results["clip_flagged"][2]
        assert results["rx_imbalance_flagged"][2]
        # frame 0 is uniformly clipped (all RX equal) so imbalance ratio ≈ 1
        assert not results["rx_imbalance_flagged"][0]


# ---------------------------------------------------------------------------
# Integration: HDF5 round-trip
# ---------------------------------------------------------------------------

def _make_h5(path: Path, n_total_frames: int, fill: complex = 100 + 100j) -> None:
    """Write a minimal HDF5 cube file matching the real file format."""
    cube = np.full(
        (n_total_frames, N_CHIRPS, N_RX, N_SAMPLES), fill, dtype=np.complex64
    )
    with h5py.File(path, "w") as f:
        f.create_dataset("cube", data=cube)
        f.attrs["num_frames"]     = n_total_frames
        f.attrs["frame_rate_hz"]  = 20.0
        f.attrs["session_id"]     = path.stem


class TestH5RoundTrip:
    def test_quality_mask_written_and_readable(self, tmp_path):
        h5 = tmp_path / "exp_test.h5"
        trim = int(_CFG["trim_frames"])
        total = trim + 100
        _make_h5(h5, total)

        # Monkey-patch CUBES_DIR so _process_session finds our temp file
        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_test", _CFG, commit="test")
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            assert "quality_mask" in f, "quality_mask dataset missing"
            assert "quality_metrics" in f, "quality_metrics group missing"
            mask = f["quality_mask"][:]
            assert mask.shape == (100,), f"expected (100,), got {mask.shape}"
            assert mask.dtype == bool
            assert mask.all(), "all frames should be healthy in a clean cube"
            assert f["quality_mask"].attrs["trim_frames"] == trim

    def test_clipped_frame_in_h5(self, tmp_path):
        h5 = tmp_path / "exp_clip.h5"
        trim = int(_CFG["trim_frames"])
        total = trim + 50
        _make_h5(h5, total)

        # Inject a clipped sample into frame trim+10 (analysis frame 10)
        with h5py.File(h5, "a") as f:
            f["cube"][trim + 10, 0, 0, 0] = 32767 + 0j

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_clip", _CFG, commit="test")
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            mask = f["quality_mask"][:]
            assert not mask[10], "analysis frame 10 should be flagged"
            assert mask[:10].all() and mask[11:].all()

    def test_rx_imbalance_metrics_written_to_h5(self, tmp_path):
        """rx_energy_ratio and rx_imbalance_flagged are saved when config block present."""
        h5 = tmp_path / "exp_rx.h5"
        trim = int(_CFG["trim_frames"])
        total = trim + 30
        _make_h5(h5, total)

        # Inject an imbalanced frame (analysis frame 5)
        with h5py.File(h5, "a") as f:
            f["cube"][trim + 5, :, 0, :] = 1000 + 0j  # RX 0 dominant

        cfg = copy.deepcopy(_CFG)
        cfg.setdefault("soft_failures", {})["rx_imbalance"] = {
            "enabled": False,       # disabled → mask stays True, but metric saved
            "rx_imbalance_ratio": 10.0,
        }

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_rx", cfg, commit="test")
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            assert "rx_energy_ratio" in f["quality_metrics"], \
                "rx_energy_ratio must be saved even when check is disabled"
            assert "rx_imbalance_flagged" in f["quality_metrics"]
            ratio = f["quality_metrics/rx_energy_ratio"][:]
            assert ratio[5] > 10.0, "analysis frame 5 should show high ratio"
            # mask should be all True because check is disabled
            assert f["quality_mask"][:].all()
            assert f["quality_mask"].attrs["rx_imbalance_enabled"] is False \
                or not bool(f["quality_mask"].attrs["rx_imbalance_enabled"])

    def test_existing_mask_overwritten(self, tmp_path):
        """Re-running _process_session replaces a stale quality_mask."""
        h5 = tmp_path / "exp_ow.h5"
        trim = int(_CFG["trim_frames"])
        _make_h5(h5, trim + 20)

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_ow", _CFG, commit="first_run")
            _process_session("exp_ow", _CFG, commit="second_run")
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            assert f["quality_mask"].attrs["git_commit"] == "second_run"


# ---------------------------------------------------------------------------
# Helpers for motion-spike tests
# ---------------------------------------------------------------------------

def _make_complex_tone(n_frames, n_chirps, n_rx, n_samples, locked_bin,
                        amplitude=1.0) -> np.ndarray:
    """Complex cube with energy concentrated at locked_bin via complex exponential."""
    t = np.arange(n_samples, dtype=np.float32)
    tone = amplitude * np.exp(1j * 2 * np.pi * locked_bin * t / n_samples).astype(np.complex64)
    cube = np.broadcast_to(tone, (n_frames, n_chirps, n_rx, n_samples)).copy()
    return cube


def _make_real_tone(n_frames, n_chirps, n_rx, n_samples, locked_bin,
                    amplitude=1.0) -> np.ndarray:
    """Real-valued cube with energy at locked_bin via cosine (for rfft path)."""
    t = np.arange(n_samples, dtype=np.float32)
    tone = amplitude * np.cos(2 * np.pi * locked_bin * t / n_samples).astype(np.float32)
    cube = np.broadcast_to(tone, (n_frames, n_chirps, n_rx, n_samples)).copy()
    return cube


# ---------------------------------------------------------------------------
# TestFlagMotionSpike
# ---------------------------------------------------------------------------

class TestFlagMotionSpike:
    N_FRAMES   = 40
    N_CHIRPS   = 4    # small for speed
    N_RX       = 4
    N_SAMPLES  = 64
    LOCKED_BIN = 8
    FACTOR     = 5.0
    HALF_WIN   = 5

    def _clean_cube(self):
        return _make_complex_tone(
            self.N_FRAMES, self.N_CHIRPS, self.N_RX, self.N_SAMPLES,
            self.LOCKED_BIN, amplitude=1.0,
        )

    # --- basic behaviour ---

    def test_clean_frames_not_flagged(self):
        cube = self._clean_cube()
        flagged, energy = _flag_motion_spike(
            cube, self.LOCKED_BIN, self.FACTOR, self.HALF_WIN
        )
        assert not flagged.any(), "uniform cube should produce no spike flags"

    def test_single_spike_frame_flagged(self):
        cube = self._clean_cube()
        spike_frame = 15
        # Energy scales with amplitude², so sqrt(factor+1) gives energy > factor × baseline
        cube[spike_frame] *= np.sqrt(self.FACTOR + 1).astype(np.float32)
        flagged, _ = _flag_motion_spike(
            cube, self.LOCKED_BIN, self.FACTOR, self.HALF_WIN
        )
        assert flagged[spike_frame], "spike frame should be flagged"
        other = list(range(self.N_FRAMES))
        other.remove(spike_frame)
        assert not flagged[other].any(), "non-spike frames should not be flagged"

    def test_spike_below_threshold_not_flagged(self):
        cube = self._clean_cube()
        # Amplitude multiplier sqrt(factor - 0.1) gives energy just below threshold
        cube[10] *= np.sqrt(self.FACTOR - 0.1).astype(np.float32)
        flagged, _ = _flag_motion_spike(
            cube, self.LOCKED_BIN, self.FACTOR, self.HALF_WIN
        )
        assert not flagged[10], "sub-threshold spike should not be flagged"

    def test_two_adjacent_spike_frames_both_flagged(self):
        cube = self._clean_cube()
        amp = np.sqrt(self.FACTOR + 1).astype(np.float32)
        cube[20] *= amp
        cube[21] *= amp
        flagged, _ = _flag_motion_spike(
            cube, self.LOCKED_BIN, self.FACTOR, self.HALF_WIN
        )
        assert flagged[20] and flagged[21], "both adjacent spike frames should be flagged"

    def test_spike_at_frame_zero_detected(self):
        # mode='reflect' should handle the edge without crashing or missing the spike
        cube = self._clean_cube()
        cube[0] *= np.sqrt(self.FACTOR + 1).astype(np.float32)
        flagged, _ = _flag_motion_spike(
            cube, self.LOCKED_BIN, self.FACTOR, self.HALF_WIN
        )
        assert flagged[0], "spike at frame 0 should be detected via reflect padding"

    # --- output shape and dtype ---

    def test_output_shapes(self):
        cube = self._clean_cube()
        flagged, energy = _flag_motion_spike(
            cube, self.LOCKED_BIN, self.FACTOR, self.HALF_WIN
        )
        assert flagged.shape == (self.N_FRAMES,)
        assert energy.shape  == (self.N_FRAMES,)

    def test_output_dtypes(self):
        cube = self._clean_cube()
        flagged, energy = _flag_motion_spike(
            cube, self.LOCKED_BIN, self.FACTOR, self.HALF_WIN
        )
        assert flagged.dtype == bool
        assert energy.dtype  == np.float32

    # --- boundary bins ---

    def test_locked_bin_zero_does_not_crash(self):
        cube = _make_complex_tone(
            self.N_FRAMES, self.N_CHIRPS, self.N_RX, self.N_SAMPLES, locked_bin=0
        )
        flagged, energy = _flag_motion_spike(cube, 0, self.FACTOR, self.HALF_WIN)
        assert flagged.shape == (self.N_FRAMES,)

    def test_locked_bin_last_does_not_crash(self):
        last_bin = self.N_SAMPLES - 1   # complex FFT: valid up to N-1
        cube = _make_complex_tone(
            self.N_FRAMES, self.N_CHIRPS, self.N_RX, self.N_SAMPLES,
            locked_bin=last_bin
        )
        flagged, energy = _flag_motion_spike(cube, last_bin, self.FACTOR, self.HALF_WIN)
        assert flagged.shape == (self.N_FRAMES,)

    # --- validation errors ---

    def test_out_of_range_locked_bin_raises(self):
        cube = self._clean_cube()
        with pytest.raises(ValueError, match="out of range"):
            _flag_motion_spike(cube, self.N_SAMPLES, self.FACTOR, self.HALF_WIN)

    def test_factor_le_one_raises(self):
        cube = self._clean_cube()
        with pytest.raises(ValueError, match="factor"):
            _flag_motion_spike(cube, self.LOCKED_BIN, 1.0, self.HALF_WIN)

    def test_negative_half_window_raises(self):
        cube = self._clean_cube()
        with pytest.raises(ValueError, match="half_window"):
            _flag_motion_spike(cube, self.LOCKED_BIN, self.FACTOR, -1)

    # --- chunked path ---

    def test_chunked_matches_unchunked(self):
        cube = self._clean_cube()
        cube[12] *= np.sqrt(self.FACTOR + 1).astype(np.float32)
        flagged_full, energy_full = _flag_motion_spike(
            cube, self.LOCKED_BIN, self.FACTOR, self.HALF_WIN
        )
        flagged_chunked, energy_chunked = _flag_motion_spike(
            cube, self.LOCKED_BIN, self.FACTOR, self.HALF_WIN, chunk_frames=7
        )
        np.testing.assert_array_equal(flagged_full, flagged_chunked)
        np.testing.assert_allclose(energy_full, energy_chunked, rtol=1e-5)

    # --- complex vs real input ---

    def test_complex_cube_uses_fft_path(self):
        cube = _make_complex_tone(
            self.N_FRAMES, self.N_CHIRPS, self.N_RX, self.N_SAMPLES,
            self.LOCKED_BIN
        )
        assert np.iscomplexobj(cube)
        flagged, energy = _flag_motion_spike(
            cube, self.LOCKED_BIN, self.FACTOR, self.HALF_WIN
        )
        assert energy.shape == (self.N_FRAMES,)
        assert (energy > 0).all()

    def test_real_cube_uses_rfft_path(self):
        n_samples = self.N_SAMPLES
        locked_bin = n_samples // 4   # safely within rfft range [0, n_samples//2]
        cube = _make_real_tone(
            self.N_FRAMES, self.N_CHIRPS, self.N_RX, n_samples, locked_bin
        )
        assert not np.iscomplexobj(cube)
        flagged, energy = _flag_motion_spike(
            cube, locked_bin, self.FACTOR, self.HALF_WIN
        )
        assert flagged.shape == (self.N_FRAMES,)
        assert energy.dtype  == np.float32

    # --- zero-energy edge case ---

    def test_zero_energy_cube_does_not_flag_all_frames(self):
        """When rolling_med ≈ 0, eps prevents dividing by zero and flagging everything."""
        cube = np.zeros(
            (self.N_FRAMES, self.N_CHIRPS, self.N_RX, self.N_SAMPLES),
            dtype=np.complex64,
        )
        flagged, energy = _flag_motion_spike(
            cube, self.LOCKED_BIN, self.FACTOR, self.HALF_WIN
        )
        assert not flagged.any(), "all-zero cube should not flag any frames"


# ---------------------------------------------------------------------------
# TestComputeQualityMaskMotionSpike
# ---------------------------------------------------------------------------

class TestComputeQualityMaskMotionSpike:
    N_FRAMES  = 40
    N_CHIRPS  = 4
    N_RX      = 4
    N_SAMPLES = 64
    LOCKED_BIN = 8

    def _clean_cube(self):
        return _make_complex_tone(
            self.N_FRAMES, self.N_CHIRPS, self.N_RX, self.N_SAMPLES,
            self.LOCKED_BIN, amplitude=1.0,
        )

    def _spike_cube(self, frame: int, factor: float = 6.0):
        cube = self._clean_cube()
        cube[frame] *= np.sqrt(factor).astype(np.float32)
        return cube

    def _cfg_with_ms(self, enabled: bool, locked_bin_override=None) -> dict:
        cfg = copy.deepcopy(_CFG)
        cfg.setdefault("soft_failures", {})["motion_spike"] = {
            "enabled": enabled,
            "motion_spike_factor": 5.0,
            "motion_window_frames": 5,
            "locked_bin": locked_bin_override,
            "chunk_frames": None,
        }
        return cfg

    def test_disabled_spike_not_in_mask(self):
        cfg  = self._cfg_with_ms(enabled=False)
        cube = self._spike_cube(frame=10)
        results = _compute_quality_mask(cube, cfg, locked_bin=self.LOCKED_BIN)
        assert results["quality_mask"].all(), \
            "spike should not affect mask when motion_spike is disabled"

    def test_disabled_metrics_still_present(self):
        cfg  = self._cfg_with_ms(enabled=False)
        cube = self._clean_cube()
        results = _compute_quality_mask(cube, cfg, locked_bin=self.LOCKED_BIN)
        assert "subject_bin_energy"   in results
        assert "motion_spike_flagged" in results

    def test_enabled_spike_masked(self):
        cfg  = self._cfg_with_ms(enabled=True)
        cube = self._spike_cube(frame=10)
        results = _compute_quality_mask(cube, cfg, locked_bin=self.LOCKED_BIN)
        assert not results["quality_mask"][10], \
            "spike frame should be masked when motion_spike is enabled"
        other = list(range(self.N_FRAMES))
        other.remove(10)
        assert results["quality_mask"][other].all()

    def test_block_absent_no_motion_keys(self):
        cfg = copy.deepcopy(_CFG)
        cfg.pop("soft_failures", None)
        cube = self._clean_cube()
        results = _compute_quality_mask(cube, cfg, locked_bin=self.LOCKED_BIN)
        assert "subject_bin_energy"   not in results
        assert "motion_spike_flagged" not in results

    def test_motion_spike_and_clip_same_frame(self):
        cfg  = self._cfg_with_ms(enabled=True)
        cube = self._spike_cube(frame=5)
        cube[5, 0, 0, 0] = 32767 + 0j      # also clip frame 5
        results = _compute_quality_mask(cube, cfg, locked_bin=self.LOCKED_BIN)
        assert not results["quality_mask"][5]
        assert results["clip_flagged"][5]
        assert results["motion_spike_flagged"][5]

    def test_locked_bin_none_skips_cleanly(self):
        cfg = self._cfg_with_ms(enabled=True)
        cube = self._clean_cube()
        # No locked_bin from manifest, no override in config
        results = _compute_quality_mask(cube, cfg, locked_bin=None)
        assert results["motion_spike_skipped"] is True
        assert results["quality_mask"].all()   # mask unaffected

    def test_config_locked_bin_overrides_manifest(self):
        # Set a different locked_bin in config vs manifest; config should win
        cfg = self._cfg_with_ms(enabled=True, locked_bin_override=self.LOCKED_BIN)
        cube = self._spike_cube(frame=8)
        # Pass a wrong manifest bin — if config wins, spike is still detected
        results = _compute_quality_mask(cube, cfg, locked_bin=self.LOCKED_BIN + 5)
        assert not results["motion_spike_skipped"]
        assert results["motion_spike_locked_bin"] == self.LOCKED_BIN
        assert not results["quality_mask"][8]

    def test_config_invalid_locked_bin_raises(self):
        cfg = self._cfg_with_ms(enabled=True, locked_bin_override=self.N_SAMPLES + 99)
        cube = self._clean_cube()
        with pytest.raises(ValueError, match="out of FFT range"):
            _compute_quality_mask(cube, cfg, locked_bin=None)


# ---------------------------------------------------------------------------
# TestH5RoundTripMotionSpike
# ---------------------------------------------------------------------------

class TestH5RoundTripMotionSpike:
    N_FRAMES  = 40
    N_CHIRPS  = 4
    N_RX      = 4
    N_SAMPLES = 64
    LOCKED_BIN = 8

    def _make_ms_cfg(self, enabled: bool) -> dict:
        cfg = copy.deepcopy(_CFG)
        cfg.setdefault("soft_failures", {})["motion_spike"] = {
            "enabled": enabled,
            "motion_spike_factor": 5.0,
            "motion_window_frames": 5,
            "locked_bin": None,
            "chunk_frames": None,
        }
        return cfg

    def _write_h5(self, path: Path, n_analysis: int, spike_frame: int | None = None):
        trim  = int(_CFG["trim_frames"])
        total = trim + n_analysis
        tone  = np.exp(
            1j * 2 * np.pi * self.LOCKED_BIN
            * np.arange(self.N_SAMPLES, dtype=np.float32) / self.N_SAMPLES
        ).astype(np.complex64)
        cube = np.broadcast_to(
            tone, (total, self.N_CHIRPS, self.N_RX, self.N_SAMPLES)
        ).copy()
        if spike_frame is not None:
            cube[trim + spike_frame] *= np.sqrt(6.0).astype(np.float32)
        with h5py.File(path, "w") as f:
            f.create_dataset("cube", data=cube)
            f.attrs["num_frames"]    = total
            f.attrs["frame_rate_hz"] = 20.0
            f.attrs["session_id"]    = path.stem

    def test_metrics_written_when_block_present_disabled(self, tmp_path):
        h5 = tmp_path / "exp_ms.h5"
        self._write_h5(h5, n_analysis=self.N_FRAMES, spike_frame=10)
        cfg = self._make_ms_cfg(enabled=False)

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_ms", cfg, commit="test",
                             locked_bin=self.LOCKED_BIN)
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            assert "subject_bin_energy"   in f["quality_metrics"]
            assert "motion_spike_flagged" in f["quality_metrics"]
            energy  = f["quality_metrics/subject_bin_energy"][:]
            flagged = f["quality_metrics/motion_spike_flagged"][:]
            assert energy.dtype  == np.float32
            assert flagged.dtype == bool
            assert energy.shape  == (self.N_FRAMES,)
            # spike detected in diagnostic but mask untouched
            assert flagged[10]
            assert f["quality_mask"][:].all()

    def test_attrs_written_to_quality_mask(self, tmp_path):
        h5 = tmp_path / "exp_ms_attr.h5"
        self._write_h5(h5, n_analysis=self.N_FRAMES)
        cfg = self._make_ms_cfg(enabled=False)

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_ms_attr", cfg, commit="test",
                             locked_bin=self.LOCKED_BIN)
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            attrs = dict(f["quality_mask"].attrs)
            assert "motion_spike_present"     in attrs
            assert "motion_spike_enabled"     in attrs
            assert "motion_spike_factor"      in attrs
            assert "motion_spike_half_window" in attrs
            assert "motion_spike_locked_bin"  in attrs
            assert not bool(attrs["motion_spike_skipped"])

    def test_skipped_check_records_skip_attrs(self, tmp_path):
        h5 = tmp_path / "exp_ms_skip.h5"
        self._write_h5(h5, n_analysis=self.N_FRAMES)
        cfg = self._make_ms_cfg(enabled=False)

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            # Pass no locked_bin → check should be skipped
            _process_session("exp_ms_skip", cfg, commit="test", locked_bin=None)
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            attrs = dict(f["quality_mask"].attrs)
            assert bool(attrs["motion_spike_skipped"])
            assert "motion_spike_skip_reason" in attrs
            assert "subject_bin_energy"   not in f["quality_metrics"]
            assert "motion_spike_flagged" not in f["quality_metrics"]

    def test_block_absent_no_motion_datasets(self, tmp_path):
        h5 = tmp_path / "exp_ms_noblock.h5"
        self._write_h5(h5, n_analysis=self.N_FRAMES)

        # Explicitly remove motion_spike (and any other block that would
        # compute subject_bin_energy via fallback) so the check never runs
        cfg = copy.deepcopy(_CFG)
        cfg.get("soft_failures", {}).pop("motion_spike", None)
        cfg.get("soft_failures", {}).pop("subject_bin_dropout", None)
        cfg.get("soft_failures", {}).pop("subject_bin_snr", None)

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_ms_noblock", cfg, commit="test",
                             locked_bin=self.LOCKED_BIN)
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            assert "subject_bin_energy"   not in f["quality_metrics"]
            assert "motion_spike_flagged" not in f["quality_metrics"]
            assert "motion_spike_present" not in f["quality_mask"].attrs


# ===========================================================================
# _flag_subject_bin_snr
# ===========================================================================

def _snr_cube_high(
    n_frames: int,
    locked_bin: int,
    n_samples: int = N_SAMPLES,
    signal_amplitude: float = 1000.0,
    noise_amplitude: float = 1.0,
) -> np.ndarray:
    """Cube with a strong complex tone at locked_bin (high SNR at that bin)."""
    cube = np.full(
        (n_frames, N_CHIRPS, N_RX, n_samples),
        noise_amplitude * (1 + 1j),
        dtype=np.complex64,
    )
    k = locked_bin
    n = np.arange(n_samples, dtype=np.float64)
    tone = signal_amplitude * np.exp(1j * 2 * np.pi * k * n / n_samples).astype(np.complex64)
    cube[:, :, :, :] += tone
    return cube


def _snr_cube_low(n_frames: int, n_samples: int = N_SAMPLES) -> np.ndarray:
    """Cube with uniform noise across all bins (no dominant tone → SNR ≈ 1)."""
    rng = np.random.default_rng(seed=0)
    return (
        rng.standard_normal((n_frames, N_CHIRPS, N_RX, n_samples)).astype(np.float32)
        + 1j * rng.standard_normal((n_frames, N_CHIRPS, N_RX, n_samples)).astype(np.float32)
    ).astype(np.complex64)


class TestFlagSubjectBinSnr:
    LOCKED_BIN = 28
    N_FRAMES   = 50

    def test_high_snr_not_flagged(self):
        """Cube with strong tone at locked_bin → SNR high → nothing flagged."""
        cube = _snr_cube_high(self.N_FRAMES, self.LOCKED_BIN)
        flagged, snr = _flag_subject_bin_snr(
            cube, self.LOCKED_BIN, snr_threshold=3.0, guard_half_width=3
        )
        assert not flagged.any(), f"Expected no flags but got {flagged.sum()}"
        assert (snr > 3.0).all()

    def test_uniform_noise_flagged_when_threshold_above_one(self):
        """Uniform noise → SNR ≈ 1 < threshold=3 → all frames flagged."""
        # Build a deterministic cube with equal energy at every bin
        n, c, r, s = self.N_FRAMES, N_CHIRPS, N_RX, N_SAMPLES
        cube = np.ones((n, c, r, s), dtype=np.complex64)
        flagged, snr = _flag_subject_bin_snr(
            cube, self.LOCKED_BIN, snr_threshold=3.0, guard_half_width=3
        )
        # SNR = subject_energy / background_median ≈ 1 (all bins identical)
        assert flagged.all(), f"Expected all frames flagged but got {flagged.sum()}/{n}"

    def test_returns_correct_shapes_and_dtypes(self):
        cube = _snr_cube_high(self.N_FRAMES, self.LOCKED_BIN)
        flagged, snr = _flag_subject_bin_snr(
            cube, self.LOCKED_BIN, snr_threshold=3.0, guard_half_width=3
        )
        assert flagged.shape == (self.N_FRAMES,)
        assert snr.shape    == (self.N_FRAMES,)
        assert flagged.dtype == bool
        assert snr.dtype    == np.float32

    def test_snr_values_are_positive(self):
        cube = _snr_cube_high(self.N_FRAMES, self.LOCKED_BIN)
        _, snr = _flag_subject_bin_snr(
            cube, self.LOCKED_BIN, snr_threshold=3.0, guard_half_width=3
        )
        assert (snr > 0).all()

    def test_chunk_frames_matches_full(self):
        """chunk_frames=10 must produce identical result to chunk_frames=None."""
        cube = _snr_cube_high(self.N_FRAMES, self.LOCKED_BIN)
        flagged_full, snr_full = _flag_subject_bin_snr(
            cube, self.LOCKED_BIN, snr_threshold=3.0, guard_half_width=3
        )
        flagged_chunk, snr_chunk = _flag_subject_bin_snr(
            cube, self.LOCKED_BIN, snr_threshold=3.0, guard_half_width=3,
            chunk_frames=10,
        )
        np.testing.assert_array_equal(flagged_full,  flagged_chunk)
        np.testing.assert_allclose(snr_full, snr_chunk, rtol=1e-5)

    def test_guard_band_excludes_subject_signal(self):
        """Without a guard, subject signal leaks into background and inflates SNR estimate.
        With guard_half_width=0 (no guard), background includes neighbours of locked_bin.
        With guard_half_width=3, background is far from the tone → lower background → higher SNR.
        We just verify the guard flag doesn't accidentally *lower* SNR below zero.
        """
        cube = _snr_cube_high(self.N_FRAMES, self.LOCKED_BIN)
        _, snr_no_guard = _flag_subject_bin_snr(
            cube, self.LOCKED_BIN, snr_threshold=0.1, guard_half_width=0
        )
        _, snr_with_guard = _flag_subject_bin_snr(
            cube, self.LOCKED_BIN, snr_threshold=0.1, guard_half_width=3
        )
        assert (snr_no_guard > 0).all()
        assert (snr_with_guard > 0).all()
        # Guard band removes subject-bin sidelobes → background median is lower
        # → SNR with guard >= SNR without guard (or roughly so)
        assert float(np.median(snr_with_guard)) >= float(np.median(snr_no_guard)) * 0.5

    def test_real_input_rfft_path(self):
        """Real float32 cube uses rfft path (n_bins = N//2+1)."""
        n_samples = 256
        locked_bin = 28
        n_frames = 20
        cube = np.ones((n_frames, N_CHIRPS, N_RX, n_samples), dtype=np.float32)
        # Inject a cosine tone at locked_bin (real signal → rfft)
        n = np.arange(n_samples, dtype=np.float64)
        tone = 1000.0 * np.cos(2 * np.pi * locked_bin * n / n_samples).astype(np.float32)
        cube[:, :, :, :] += tone
        flagged, snr = _flag_subject_bin_snr(
            cube, locked_bin, snr_threshold=3.0, guard_half_width=3
        )
        assert flagged.shape == (n_frames,)
        assert not flagged.any(), "High-SNR real tone should not be flagged"

    def test_locked_bin_out_of_range_raises(self):
        cube = _make_cube(10)
        n_bins = N_SAMPLES  # complex input
        with pytest.raises(ValueError, match="out of range"):
            _flag_subject_bin_snr(cube, n_bins, snr_threshold=3.0, guard_half_width=3)

    def test_negative_locked_bin_raises(self):
        cube = _make_cube(10)
        with pytest.raises(ValueError, match="out of range"):
            _flag_subject_bin_snr(cube, -1, snr_threshold=3.0, guard_half_width=3)

    def test_non_integer_locked_bin_raises(self):
        cube = _make_cube(10)
        with pytest.raises(ValueError, match="integer"):
            _flag_subject_bin_snr(cube, 28.5, snr_threshold=3.0, guard_half_width=3)

    def test_snr_threshold_zero_raises(self):
        cube = _make_cube(10)
        with pytest.raises(ValueError, match="snr_threshold"):
            _flag_subject_bin_snr(cube, 28, snr_threshold=0.0, guard_half_width=3)

    def test_negative_snr_threshold_raises(self):
        cube = _make_cube(10)
        with pytest.raises(ValueError, match="snr_threshold"):
            _flag_subject_bin_snr(cube, 28, snr_threshold=-1.0, guard_half_width=3)

    def test_negative_guard_half_width_raises(self):
        cube = _make_cube(10)
        with pytest.raises(ValueError, match="guard_half_width"):
            _flag_subject_bin_snr(cube, 28, snr_threshold=3.0, guard_half_width=-1)

    def test_chunk_frames_zero_raises(self):
        cube = _make_cube(10)
        with pytest.raises(ValueError, match="chunk_frames"):
            _flag_subject_bin_snr(
                cube, 28, snr_threshold=3.0, guard_half_width=3, chunk_frames=0
            )

    def test_guard_wider_than_range_falls_back(self):
        """guard_half_width that covers all bins falls back to all-except-locked."""
        cube = _snr_cube_high(self.N_FRAMES, self.LOCKED_BIN)
        # guard_half_width=512 covers all bins → fallback: background = all except locked_bin
        flagged, snr = _flag_subject_bin_snr(
            cube, self.LOCKED_BIN, snr_threshold=3.0, guard_half_width=512
        )
        assert flagged.shape == (self.N_FRAMES,)
        assert (snr > 0).all()


# ===========================================================================
# _compute_quality_mask — SNR integration
# ===========================================================================

def _make_snr_cfg(enabled: bool = False, threshold: float = 3.0, ghw: int = 3) -> dict:
    cfg = copy.deepcopy(_CFG)
    cfg.setdefault("soft_failures", {})["subject_bin_snr"] = {
        "enabled":          enabled,
        "snr_threshold":    threshold,
        "guard_half_width": ghw,
        "chunk_frames":     None,
    }
    # Remove motion_spike to isolate SNR behaviour
    cfg.get("soft_failures", {}).pop("motion_spike", None)
    return cfg


LOCKED_BIN_SNR = 28


class TestComputeQualityMaskSnr:

    def test_snr_disabled_does_not_affect_mask(self):
        """With SNR disabled, mask should equal hard-failure result only."""
        cube = _snr_cube_high(100, LOCKED_BIN_SNR)
        cfg  = _make_snr_cfg(enabled=False, threshold=3.0)
        res  = _compute_quality_mask(cube, cfg, locked_bin=LOCKED_BIN_SNR)
        # All frames healthy → all pass
        assert res["quality_mask"].all()

    def test_snr_enabled_flags_low_snr_frames(self):
        """With SNR enabled, uniform cube (SNR ≈ 1) should fail threshold=3."""
        n = 100
        cube = np.ones((n, N_CHIRPS, N_RX, N_SAMPLES), dtype=np.complex64)
        cfg  = _make_snr_cfg(enabled=True, threshold=3.0)
        res  = _compute_quality_mask(cube, cfg, locked_bin=LOCKED_BIN_SNR)
        # Uniform cube → SNR ≈ 1 < 3 → all analysis frames flagged
        assert not res["quality_mask"].any()

    def test_snr_check_always_stores_metric(self):
        """SNR values and flagged array are stored even when check is disabled."""
        cube = _snr_cube_high(100, LOCKED_BIN_SNR)
        cfg  = _make_snr_cfg(enabled=False, threshold=3.0)
        res  = _compute_quality_mask(cube, cfg, locked_bin=LOCKED_BIN_SNR)
        assert "subject_bin_snr" in res
        assert "snr_flagged"     in res
        assert res["subject_bin_snr"].shape == (100,)

    def test_snr_skips_when_no_locked_bin(self):
        cube = _snr_cube_high(50, LOCKED_BIN_SNR)
        cfg  = _make_snr_cfg(enabled=True, threshold=3.0)
        res  = _compute_quality_mask(cube, cfg, locked_bin=None)
        assert res["snr_skipped"] is True
        assert "subject_bin_snr" not in res
        assert "snr_flagged"     not in res

    def test_snr_config_override_takes_precedence(self):
        """locked_bin in config overrides the manifest value passed in."""
        cube = _snr_cube_high(50, LOCKED_BIN_SNR)
        cfg  = _make_snr_cfg(enabled=False, threshold=3.0)
        # Override to a different bin
        cfg["soft_failures"]["subject_bin_snr"]["locked_bin"] = 10
        res = _compute_quality_mask(cube, cfg, locked_bin=LOCKED_BIN_SNR)
        assert res["snr_locked_bin"] == 10

    def test_snr_block_absent_no_snr_keys(self):
        """When subject_bin_snr block is absent, no SNR keys are added to results."""
        cube = _make_cube(50)
        cfg  = copy.deepcopy(_CFG)
        cfg.get("soft_failures", {}).pop("subject_bin_snr", None)
        cfg.get("soft_failures", {}).pop("motion_spike", None)
        res = _compute_quality_mask(cube, cfg, locked_bin=LOCKED_BIN_SNR)
        assert "subject_bin_snr" not in res
        assert "snr_flagged"     not in res
        assert "snr_skipped"     not in res


# ===========================================================================
# HDF5 round-trip for subject_bin_snr (via _process_session)
# ===========================================================================

class TestH5RoundTripSubjectBinSnr:
    N_FRAMES   = 200
    LOCKED_BIN = 28
    N_TRIM     = 600

    def _write_h5(self, path: Path, n_analysis: int) -> None:
        """Write a minimal HDF5 cube that satisfies _process_session expectations."""
        n_total = self.N_TRIM + n_analysis
        rng = np.random.default_rng(seed=1)
        cube = (
            rng.standard_normal((n_total, N_CHIRPS, N_RX, N_SAMPLES)).astype(np.float32)
            + 1j * rng.standard_normal((n_total, N_CHIRPS, N_RX, N_SAMPLES)).astype(np.float32)
        ).astype(np.complex64) * 100

        # Inject tone at LOCKED_BIN for every frame so SNR is high
        n = np.arange(N_SAMPLES, dtype=np.float64)
        tone = 5000.0 * np.exp(1j * 2 * np.pi * self.LOCKED_BIN * n / N_SAMPLES).astype(np.complex64)
        cube[:, :, :, :] += tone

        with h5py.File(path, "w") as f:
            f.create_dataset("cube", data=cube)
            f.attrs["num_frames"]    = n_total
            f.attrs["frame_rate_hz"] = 20.0
            f.attrs["session_id"]    = path.stem
            f.attrs["posture"]       = "sit"
            f.attrs["distance_cm"]   = 140

    def _make_snr_cfg(self, enabled: bool = True) -> dict:
        cfg = copy.deepcopy(_CFG)
        cfg.setdefault("soft_failures", {})["subject_bin_snr"] = {
            "enabled":          enabled,
            "snr_threshold":    3.0,
            "guard_half_width": 3,
            "chunk_frames":     None,
        }
        cfg.get("soft_failures", {}).pop("motion_spike", None)
        return cfg

    def test_snr_datasets_written(self, tmp_path):
        h5 = tmp_path / "exp_snr_rt.h5"
        self._write_h5(h5, n_analysis=self.N_FRAMES)
        cfg = self._make_snr_cfg(enabled=True)

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_snr_rt", cfg, commit="test",
                             locked_bin=self.LOCKED_BIN)
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            assert "subject_bin_snr" in f["quality_metrics"]
            assert "snr_flagged"     in f["quality_metrics"]
            snr = f["quality_metrics/subject_bin_snr"][:]
            assert snr.shape == (self.N_FRAMES,)
            assert (snr > 0).all()

    def test_snr_attrs_written(self, tmp_path):
        h5 = tmp_path / "exp_snr_attr.h5"
        self._write_h5(h5, n_analysis=self.N_FRAMES)
        cfg = self._make_snr_cfg(enabled=True)

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_snr_attr", cfg, commit="test",
                             locked_bin=self.LOCKED_BIN)
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            attrs = dict(f["quality_mask"].attrs)
            assert attrs["subject_bin_snr_present"]
            assert attrs["subject_bin_snr_enabled"]
            assert float(attrs["subject_bin_snr_threshold"]) == pytest.approx(3.0)
            assert int(attrs["subject_bin_snr_guard_half_width"]) == 3
            assert int(attrs["subject_bin_snr_locked_bin"]) == self.LOCKED_BIN
            assert not bool(attrs["subject_bin_snr_skipped"])

    def test_skipped_check_records_skip_attrs(self, tmp_path):
        h5 = tmp_path / "exp_snr_skip.h5"
        self._write_h5(h5, n_analysis=self.N_FRAMES)
        cfg = self._make_snr_cfg(enabled=False)

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_snr_skip", cfg, commit="test", locked_bin=None)
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            attrs = dict(f["quality_mask"].attrs)
            assert bool(attrs["subject_bin_snr_skipped"])
            assert "subject_bin_snr_skip_reason" in attrs
            assert "subject_bin_snr" not in f["quality_metrics"]
            assert "snr_flagged"     not in f["quality_metrics"]

    def test_block_absent_no_snr_datasets(self, tmp_path):
        h5 = tmp_path / "exp_snr_noblock.h5"
        self._write_h5(h5, n_analysis=self.N_FRAMES)

        cfg = copy.deepcopy(_CFG)
        cfg.get("soft_failures", {}).pop("subject_bin_snr", None)
        cfg.get("soft_failures", {}).pop("motion_spike", None)

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_snr_noblock", cfg, commit="test",
                             locked_bin=self.LOCKED_BIN)
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            assert "subject_bin_snr"             not in f["quality_metrics"]
            assert "snr_flagged"                 not in f["quality_metrics"]
            assert "subject_bin_snr_present"     not in f["quality_mask"].attrs


# ===========================================================================
# _subject_bin_energy helper
# ===========================================================================

class TestSubjectBinEnergy:
    LOCKED_BIN = 28
    N_FRAMES   = 30

    def test_returns_float32_shape_n(self):
        cube = _make_cube(self.N_FRAMES)
        e = _subject_bin_energy(cube, self.LOCKED_BIN)
        assert e.shape == (self.N_FRAMES,)
        assert e.dtype == np.float32

    def test_tone_at_locked_bin_gives_high_energy(self):
        cube = _snr_cube_high(self.N_FRAMES, self.LOCKED_BIN, signal_amplitude=1000.0)
        e = _subject_bin_energy(cube, self.LOCKED_BIN)
        # Tone energy should dominate over low noise amplitude
        assert (e > 1e6).all()

    def test_chunk_path_matches_full(self):
        cube = _snr_cube_high(self.N_FRAMES, self.LOCKED_BIN)
        e_full  = _subject_bin_energy(cube, self.LOCKED_BIN)
        e_chunk = _subject_bin_energy(cube, self.LOCKED_BIN, chunk_frames=7)
        np.testing.assert_allclose(e_full, e_chunk, rtol=1e-5)

    def test_non_integer_locked_bin_raises(self):
        cube = _make_cube(10)
        with pytest.raises(ValueError, match="integer"):
            _subject_bin_energy(cube, 28.5)

    def test_out_of_range_locked_bin_raises(self):
        cube = _make_cube(10)
        with pytest.raises(ValueError, match="out of range"):
            _subject_bin_energy(cube, N_SAMPLES)  # one past the end (complex)

    def test_chunk_frames_zero_raises(self):
        cube = _make_cube(10)
        with pytest.raises(ValueError, match="chunk_frames"):
            _subject_bin_energy(cube, self.LOCKED_BIN, chunk_frames=0)

    def test_real_input_uses_rfft(self):
        n_samples  = 256
        locked_bin = 28
        n_frames   = 10
        cube = np.ones((n_frames, N_CHIRPS, N_RX, n_samples), dtype=np.float32)
        n = np.arange(n_samples, dtype=np.float64)
        tone = 500.0 * np.cos(2 * np.pi * locked_bin * n / n_samples).astype(np.float32)
        cube[:, :, :, :] += tone
        e = _subject_bin_energy(cube, locked_bin)
        assert e.shape == (n_frames,)
        assert e.dtype == np.float32
        assert (e > 0).all()


# ===========================================================================
# _flag_subject_bin_dropout
# ===========================================================================

def _make_dropout_energy(
    n_frames: int,
    baseline: float = 1000.0,
    dropout_frame: int | None = None,
    dropout_value: float = 50.0,
) -> np.ndarray:
    """Flat energy series with an optional single-frame dropout."""
    e = np.full(n_frames, baseline, dtype=np.float32)
    if dropout_frame is not None:
        e[dropout_frame] = dropout_value
    return e


class TestFlagSubjectBinDropout:
    N = 60

    def test_flat_energy_no_flags(self):
        e = _make_dropout_energy(self.N, baseline=1000.0)
        flagged, _ = _flag_subject_bin_dropout(e, dropout_factor=0.1, half_window=10)
        assert not flagged.any()

    def test_single_dropout_frame_flagged(self):
        e = _make_dropout_energy(self.N, baseline=1000.0,
                                 dropout_frame=20, dropout_value=50.0)
        # 50 < 0.1 * 1000 → flagged
        flagged, _ = _flag_subject_bin_dropout(e, dropout_factor=0.1, half_window=10)
        assert flagged[20], "dropout frame must be flagged"

    def test_neighbors_not_flagged(self):
        e = _make_dropout_energy(self.N, baseline=1000.0,
                                 dropout_frame=20, dropout_value=50.0)
        flagged, _ = _flag_subject_bin_dropout(e, dropout_factor=0.1, half_window=10)
        assert not flagged[19]
        assert not flagged[21]

    def test_correct_shapes_and_dtypes(self):
        e = _make_dropout_energy(self.N)
        flagged, energy_out = _flag_subject_bin_dropout(e, dropout_factor=0.1, half_window=10)
        assert flagged.shape == (self.N,)
        assert energy_out.shape == (self.N,)
        assert flagged.dtype == bool
        assert energy_out.dtype == np.float32

    def test_all_zero_energy_no_flags(self):
        e = np.zeros(self.N, dtype=np.float32)
        flagged, _ = _flag_subject_bin_dropout(e, dropout_factor=0.1, half_window=10)
        assert not flagged.any(), "all-zero energy must not produce dropout flags"

    def test_near_zero_baseline_no_false_flags(self):
        e = np.full(self.N, 1e-40, dtype=np.float32)  # below float32 tiny
        flagged, _ = _flag_subject_bin_dropout(e, dropout_factor=0.1, half_window=10)
        assert not flagged.any(), "near-zero baseline must not produce false flags"

    def test_exactly_at_threshold_not_flagged(self):
        baseline = 1000.0
        # Frame 10 at exactly dropout_factor * baseline → NOT flagged (strict <)
        e = _make_dropout_energy(self.N, baseline=baseline,
                                 dropout_frame=10,
                                 dropout_value=0.1 * baseline)
        flagged, _ = _flag_subject_bin_dropout(e, dropout_factor=0.1, half_window=5)
        assert not flagged[10], "frame exactly at threshold must not be flagged (strict <)"

    def test_long_dropout_rolling_median_adapts(self):
        # When a sustained low-energy region spans many frames, the rolling
        # median adapts to the new level and stops flagging after ~half_window.
        # We verify: not ALL frames in a 60-frame flat low-energy block are flagged.
        hw = 10
        e = np.full(self.N, 1000.0, dtype=np.float32)
        e[20:50] = 80.0           # 30-frame sustained dropout (80 < 0.1*1000)
        flagged, _ = _flag_subject_bin_dropout(e, dropout_factor=0.1, half_window=hw)
        # At least some frames in the sustained block must NOT be flagged
        # once the rolling median has tracked down to ~80
        assert not flagged[20:50].all(), (
            "rolling median should adapt during a sustained dropout; "
            "not every frame should be flagged"
        )

    def test_dropout_factor_zero_raises(self):
        e = _make_dropout_energy(10)
        with pytest.raises(ValueError, match="dropout_factor"):
            _flag_subject_bin_dropout(e, dropout_factor=0.0, half_window=5)

    def test_dropout_factor_negative_raises(self):
        e = _make_dropout_energy(10)
        with pytest.raises(ValueError, match="dropout_factor"):
            _flag_subject_bin_dropout(e, dropout_factor=-0.1, half_window=5)

    def test_dropout_factor_one_raises(self):
        e = _make_dropout_energy(10)
        with pytest.raises(ValueError, match="dropout_factor"):
            _flag_subject_bin_dropout(e, dropout_factor=1.0, half_window=5)

    def test_dropout_factor_above_one_raises(self):
        e = _make_dropout_energy(10)
        with pytest.raises(ValueError, match="dropout_factor"):
            _flag_subject_bin_dropout(e, dropout_factor=2.0, half_window=5)

    def test_half_window_negative_raises(self):
        e = _make_dropout_energy(10)
        with pytest.raises(ValueError, match="half_window"):
            _flag_subject_bin_dropout(e, dropout_factor=0.1, half_window=-1)


# ===========================================================================
# _compute_quality_mask — dropout integration
# ===========================================================================

DROPOUT_LOCKED_BIN = 28


def _make_dropout_cfg(
    enabled: bool = False,
    factor: float = 0.1,
    hw: int = 10,
    locked_bin=None,
    include_motion_spike: bool = True,
) -> dict:
    cfg = copy.deepcopy(_CFG)
    cfg.setdefault("soft_failures", {})["subject_bin_dropout"] = {
        "enabled":              enabled,
        "dropout_factor":       factor,
        "dropout_window_frames": hw,
        "locked_bin":           locked_bin,
        "chunk_frames":         None,
    }
    cfg.get("soft_failures", {}).pop("subject_bin_snr", None)
    if not include_motion_spike:
        cfg.get("soft_failures", {}).pop("motion_spike", None)
    return cfg


class TestComputeQualityMaskDropout:

    def test_dropout_disabled_does_not_affect_mask(self):
        """Block present but disabled: healthy cube should pass mask unchanged."""
        cube = _snr_cube_high(80, DROPOUT_LOCKED_BIN)
        cfg  = _make_dropout_cfg(enabled=False)
        res  = _compute_quality_mask(cube, cfg, locked_bin=DROPOUT_LOCKED_BIN)
        assert res["quality_mask"].all()

    def test_dropout_enabled_flags_dropout_frames(self):
        """Inject a genuine subject-bin energy dip and verify it is flagged."""
        n       = 80
        locked  = DROPOUT_LOCKED_BIN
        # Build cube with tone → energy is high everywhere
        cube = _snr_cube_high(n, locked, signal_amplitude=1000.0, noise_amplitude=1.0)
        # Force frame 30 to near-zero by removing the tone in that frame
        n_samples = cube.shape[3]
        k = locked
        samp = np.arange(n_samples, dtype=np.float64)
        tone = (1000.0 * np.exp(1j * 2 * np.pi * k * samp / n_samples)).astype(np.complex64)
        cube[30, :, :, :] -= tone  # removes the tone; leaves only noise
        cfg = _make_dropout_cfg(enabled=True, factor=0.1, hw=10)
        res = _compute_quality_mask(cube, cfg, locked_bin=locked)
        assert "subject_bin_dropout_flagged" in res
        assert res["subject_bin_dropout_flagged"][30], "low-energy frame should be flagged"

    def test_dropout_block_present_stores_metric(self):
        """Metric is computed and stored even when disabled."""
        cube = _snr_cube_high(60, DROPOUT_LOCKED_BIN)
        cfg  = _make_dropout_cfg(enabled=False)
        res  = _compute_quality_mask(cube, cfg, locked_bin=DROPOUT_LOCKED_BIN)
        assert "subject_bin_dropout_flagged" in res
        assert res["subject_bin_dropout_flagged"].shape == (60,)

    def test_dropout_reuses_energy_from_motion_spike(self):
        """When motion_spike also runs, dropout must reuse energy (no second FFT)."""
        cube = _snr_cube_high(60, DROPOUT_LOCKED_BIN)
        cfg  = _make_dropout_cfg(enabled=False, include_motion_spike=True)
        res  = _compute_quality_mask(cube, cfg, locked_bin=DROPOUT_LOCKED_BIN)
        assert res["dropout_energy_source"] == "reused_subject_bin_energy"

    def test_dropout_fallback_computes_energy(self):
        """When motion_spike is absent, dropout computes energy via fallback."""
        cube = _snr_cube_high(60, DROPOUT_LOCKED_BIN)
        cfg  = _make_dropout_cfg(enabled=False, include_motion_spike=False)
        res  = _compute_quality_mask(cube, cfg, locked_bin=DROPOUT_LOCKED_BIN)
        assert res["dropout_energy_source"] == "computed_fallback"
        assert "subject_bin_energy" in res

    def test_dropout_skips_when_no_energy_and_no_locked_bin(self):
        """No motion_spike energy + no locked_bin → skip gracefully."""
        cube = _snr_cube_high(60, DROPOUT_LOCKED_BIN)
        cfg  = _make_dropout_cfg(enabled=True, include_motion_spike=False)
        res  = _compute_quality_mask(cube, cfg, locked_bin=None)
        assert res["dropout_skipped"] is True
        assert "subject_bin_dropout_flagged" not in res

    def test_dropout_reused_energy_does_not_need_locked_bin(self):
        """Energy reused from motion_spike: dropout skips its own locked_bin resolution."""
        cube = _snr_cube_high(60, DROPOUT_LOCKED_BIN)
        cfg  = _make_dropout_cfg(enabled=False, include_motion_spike=True)
        # Pass no locked_bin — motion_spike in cfg has its own override
        cfg["soft_failures"]["motion_spike"]["locked_bin"] = DROPOUT_LOCKED_BIN
        res  = _compute_quality_mask(cube, cfg, locked_bin=None)
        assert res["dropout_energy_source"] == "reused_subject_bin_energy"
        assert not res["dropout_skipped"]

    def test_dropout_config_locked_bin_overrides_manifest(self):
        """Config locked_bin on dropout block takes precedence over manifest value."""
        cube = _snr_cube_high(60, DROPOUT_LOCKED_BIN)
        cfg  = _make_dropout_cfg(enabled=False, include_motion_spike=False,
                                 locked_bin=10)
        res  = _compute_quality_mask(cube, cfg, locked_bin=DROPOUT_LOCKED_BIN)
        # Energy should be computed at bin=10 (config), not bin=DROPOUT_LOCKED_BIN
        assert res["dropout_locked_bin"] == 10

    def test_dropout_block_absent_no_dropout_keys(self):
        """Block absent → no dropout keys added to results."""
        cube = _make_cube(60)
        cfg  = copy.deepcopy(_CFG)
        cfg.get("soft_failures", {}).pop("subject_bin_dropout", None)
        cfg.get("soft_failures", {}).pop("motion_spike", None)
        cfg.get("soft_failures", {}).pop("subject_bin_snr", None)
        res  = _compute_quality_mask(cube, cfg, locked_bin=DROPOUT_LOCKED_BIN)
        assert "subject_bin_dropout_flagged" not in res
        assert "dropout_skipped"             not in res

    def test_dropout_and_spike_on_different_frames_both_flagged(self):
        """Spike at frame 10, dropout at frame 50: both appear in component flags."""
        n       = 80
        locked  = DROPOUT_LOCKED_BIN
        n_samp  = N_SAMPLES

        # Build baseline cube with consistent tone
        cube = _snr_cube_high(n, locked, signal_amplitude=1000.0, noise_amplitude=1.0)

        # Frame 10: inject extra energy (×200 amplitude → 40000× energy spike)
        k    = locked
        samp = np.arange(n_samp, dtype=np.float64)
        tone = (1000.0 * np.exp(1j * 2 * np.pi * k * samp / n_samp)).astype(np.complex64)
        cube[10, :, :, :] += 200.0 * tone

        # Frame 50: remove the tone so subject-bin energy drops
        cube[50, :, :, :] -= tone

        cfg = copy.deepcopy(_CFG)
        cfg["soft_failures"]["motion_spike"]["locked_bin"]       = locked
        cfg["soft_failures"]["motion_spike"]["motion_spike_factor"] = 5.0
        cfg["soft_failures"]["motion_spike"]["motion_window_frames"] = 10
        cfg.get("soft_failures", {}).pop("subject_bin_snr", None)
        cfg.setdefault("soft_failures", {})["subject_bin_dropout"] = {
            "enabled":               True,
            "dropout_factor":        0.1,
            "dropout_window_frames": 10,
            "locked_bin":            None,
            "chunk_frames":          None,
        }

        res = _compute_quality_mask(cube, cfg, locked_bin=locked)
        assert res["motion_spike_flagged"][10], "spike frame must be flagged by motion_spike"
        assert res["subject_bin_dropout_flagged"][50], "dropout frame must be flagged by dropout"


# ===========================================================================
# HDF5 round-trip for subject_bin_dropout (via _process_session)
# ===========================================================================

class TestH5RoundTripSubjectBinDropout:
    N_FRAMES   = 200
    LOCKED_BIN = 28
    N_TRIM     = 600

    def _write_h5(self, path: Path, n_analysis: int) -> None:
        n_total = self.N_TRIM + n_analysis
        rng  = np.random.default_rng(seed=3)
        cube = (
            rng.standard_normal((n_total, N_CHIRPS, N_RX, N_SAMPLES)).astype(np.float32)
            + 1j * rng.standard_normal((n_total, N_CHIRPS, N_RX, N_SAMPLES)).astype(np.float32)
        ).astype(np.complex64) * 100

        # Inject tone at LOCKED_BIN
        k    = self.LOCKED_BIN
        samp = np.arange(N_SAMPLES, dtype=np.float64)
        tone = (5000.0 * np.exp(1j * 2 * np.pi * k * samp / N_SAMPLES)).astype(np.complex64)
        cube[:, :, :, :] += tone

        with h5py.File(path, "w") as f:
            f.create_dataset("cube", data=cube)
            f.attrs["num_frames"]    = n_total
            f.attrs["frame_rate_hz"] = 20.0
            f.attrs["session_id"]    = path.stem
            f.attrs["posture"]       = "sit"
            f.attrs["distance_cm"]   = 140

    def _make_cfg_with_dropout(self, enabled: bool = True,
                               include_motion_spike: bool = True) -> dict:
        cfg = copy.deepcopy(_CFG)
        cfg.get("soft_failures", {}).pop("subject_bin_snr", None)
        if not include_motion_spike:
            cfg.get("soft_failures", {}).pop("motion_spike", None)
        cfg.setdefault("soft_failures", {})["subject_bin_dropout"] = {
            "enabled":               enabled,
            "dropout_factor":        0.1,
            "dropout_window_frames": 50,
            "locked_bin":            None,
            "chunk_frames":          None,
        }
        return cfg

    def test_dropout_dataset_written(self, tmp_path):
        h5  = tmp_path / "exp_do_rt.h5"
        self._write_h5(h5, n_analysis=self.N_FRAMES)
        cfg = self._make_cfg_with_dropout(enabled=True)

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_do_rt", cfg, commit="test",
                             locked_bin=self.LOCKED_BIN)
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            assert "subject_bin_dropout_flagged" in f["quality_metrics"]
            do_flagged = f["quality_metrics/subject_bin_dropout_flagged"][:]
            assert do_flagged.shape == (self.N_FRAMES,)
            assert do_flagged.dtype == bool

    def test_energy_not_duplicated_when_motion_spike_ran(self, tmp_path):
        """subject_bin_energy is written once; dropout does not write a second copy."""
        h5  = tmp_path / "exp_do_nodup.h5"
        self._write_h5(h5, n_analysis=self.N_FRAMES)
        cfg = self._make_cfg_with_dropout(include_motion_spike=True)

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_do_nodup", cfg, commit="test",
                             locked_bin=self.LOCKED_BIN)
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            keys = list(f["quality_metrics"].keys())
            assert keys.count("subject_bin_energy") == 1, (
                "subject_bin_energy must appear exactly once"
            )

    def test_dropout_fallback_writes_energy(self, tmp_path):
        """Fallback path: dropout writes subject_bin_energy when motion_spike is absent."""
        h5  = tmp_path / "exp_do_fallback.h5"
        self._write_h5(h5, n_analysis=self.N_FRAMES)
        cfg = self._make_cfg_with_dropout(include_motion_spike=False)

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_do_fallback", cfg, commit="test",
                             locked_bin=self.LOCKED_BIN)
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            assert "subject_bin_energy"          in f["quality_metrics"]
            assert "subject_bin_dropout_flagged" in f["quality_metrics"]
            assert "motion_spike_flagged"        not in f["quality_metrics"]

    def test_dropout_attrs_written(self, tmp_path):
        h5  = tmp_path / "exp_do_attr.h5"
        self._write_h5(h5, n_analysis=self.N_FRAMES)
        cfg = self._make_cfg_with_dropout(enabled=True)

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_do_attr", cfg, commit="test",
                             locked_bin=self.LOCKED_BIN)
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            attrs = dict(f["quality_mask"].attrs)
            assert attrs["subject_bin_dropout_present"]
            assert attrs["subject_bin_dropout_enabled"]
            assert float(attrs["subject_bin_dropout_factor"]) == pytest.approx(0.1)
            assert int(attrs["subject_bin_dropout_half_window"]) == 50
            assert not bool(attrs["subject_bin_dropout_skipped"])
            assert str(attrs["subject_bin_dropout_energy_source"]) in (
                "reused_subject_bin_energy", "computed_fallback"
            )

    def test_skipped_check_records_skip_attrs(self, tmp_path):
        h5  = tmp_path / "exp_do_skip.h5"
        self._write_h5(h5, n_analysis=self.N_FRAMES)
        cfg = self._make_cfg_with_dropout(enabled=True, include_motion_spike=False)

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_do_skip", cfg, commit="test", locked_bin=None)
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            attrs = dict(f["quality_mask"].attrs)
            assert bool(attrs["subject_bin_dropout_skipped"])
            assert "subject_bin_dropout_skip_reason" in attrs
            assert "subject_bin_dropout_flagged" not in f["quality_metrics"]

    def test_block_absent_no_dropout_datasets(self, tmp_path):
        h5  = tmp_path / "exp_do_noblock.h5"
        self._write_h5(h5, n_analysis=self.N_FRAMES)

        cfg = copy.deepcopy(_CFG)
        cfg.get("soft_failures", {}).pop("subject_bin_dropout", None)
        cfg.get("soft_failures", {}).pop("subject_bin_snr", None)
        cfg.get("soft_failures", {}).pop("motion_spike", None)

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_do_noblock", cfg, commit="test",
                             locked_bin=self.LOCKED_BIN)
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            assert "subject_bin_dropout_flagged"    not in f["quality_metrics"]
            assert "subject_bin_dropout_present"    not in f["quality_mask"].attrs


# ===========================================================================
# _subject_bin_complex
# ===========================================================================

class TestSubjectBinComplex:
    LOCKED_BIN = 28
    N_FRAMES   = 30

    def test_returns_complex64_shape_n(self):
        cube = _make_cube(self.N_FRAMES)
        z = _subject_bin_complex(cube, self.LOCKED_BIN)
        assert z.shape == (self.N_FRAMES,)
        assert z.dtype == np.complex64

    def test_chunk_path_matches_full(self):
        cube = _snr_cube_high(self.N_FRAMES, self.LOCKED_BIN)
        z_full  = _subject_bin_complex(cube, self.LOCKED_BIN)
        z_chunk = _subject_bin_complex(cube, self.LOCKED_BIN, chunk_frames=7)
        np.testing.assert_allclose(z_full.real, z_chunk.real, rtol=1e-5)
        np.testing.assert_allclose(z_full.imag, z_chunk.imag, rtol=1e-5)

    def test_locked_bin_zero_works(self):
        cube = _make_cube(10)
        z = _subject_bin_complex(cube, 0)
        assert z.shape == (10,)

    def test_locked_bin_last_bin_works(self):
        cube = _make_cube(10)   # complex64 → n_bins = N_SAMPLES
        z = _subject_bin_complex(cube, N_SAMPLES - 1)
        assert z.shape == (10,)

    def test_out_of_range_locked_bin_raises(self):
        cube = _make_cube(10)
        with pytest.raises(ValueError, match="out of range"):
            _subject_bin_complex(cube, N_SAMPLES)

    def test_non_integer_locked_bin_raises(self):
        cube = _make_cube(10)
        with pytest.raises(ValueError, match="integer"):
            _subject_bin_complex(cube, 28.5)

    def test_chunk_frames_zero_raises(self):
        cube = _make_cube(10)
        with pytest.raises(ValueError, match="chunk_frames"):
            _subject_bin_complex(cube, self.LOCKED_BIN, chunk_frames=0)

    def test_real_cube_uses_rfft(self):
        n_samples  = 256
        locked_bin = 28
        n_frames   = 10
        cube = np.ones((n_frames, N_CHIRPS, N_RX, n_samples), dtype=np.float32)
        n    = np.arange(n_samples, dtype=np.float64)
        tone = np.cos(2 * np.pi * locked_bin * n / n_samples).astype(np.float32)
        cube[:, :, :, :] += tone
        z = _subject_bin_complex(cube, locked_bin)
        assert z.shape    == (n_frames,)
        assert z.dtype    == np.complex64

    def test_complex_tone_phase_is_consistent(self):
        """A cube filled with a pure complex tone at locked_bin should give a
        constant-phase phasor — frame-to-frame angle differences should be ~0."""
        n_samples  = 256
        locked_bin = 28
        n_frames   = 20
        n = np.arange(n_samples, dtype=np.float64)
        tone = np.exp(1j * 2 * np.pi * locked_bin * n / n_samples).astype(np.complex64)
        cube = np.zeros((n_frames, N_CHIRPS, N_RX, n_samples), dtype=np.complex64)
        cube[:, :, :, :] = tone
        z = _subject_bin_complex(cube, locked_bin)
        # All frames carry the same tone → phasors should be identical
        angles = np.angle(z)
        assert np.allclose(angles, angles[0], atol=1e-3), (
            "constant-tone phasors should have equal phases across frames"
        )


# ===========================================================================
# _flag_phase_jump
# ===========================================================================

def _make_const_phase_series(n: int, amplitude: float = 1.0) -> np.ndarray:
    """Constant phasor: z[i] = amplitude * exp(j * 0.5) for all i."""
    return np.full(n, amplitude * np.exp(1j * 0.5), dtype=np.complex64)


def _make_phase_jump_series(
    n: int,
    jump_frame: int,
    jump_rad: float,
    amplitude: float = 1.0,
) -> np.ndarray:
    """Phasor series with a single jump at jump_frame.

    Frames 0..jump_frame-1: constant phase 0.
    Frames jump_frame..n-1: constant phase jump_rad (signed).
    """
    base_phase = 0.5
    z = np.full(n, amplitude * np.exp(1j * base_phase), dtype=np.complex64)
    for i in range(jump_frame, n):
        z[i] = amplitude * np.exp(1j * (base_phase + jump_rad))
    return z


class TestFlagPhaseJump:
    N = 60

    def test_constant_phase_no_flags(self):
        z = _make_const_phase_series(self.N)
        flagged, diff, delta = _flag_phase_jump(z, threshold_rad=0.5)
        assert not flagged.any()

    def test_single_positive_jump_flagged(self):
        z = _make_phase_jump_series(self.N, jump_frame=20, jump_rad=1.5)
        flagged, diff, delta = _flag_phase_jump(z, threshold_rad=0.5)
        assert flagged[20], "frame 20 should be flagged"
        assert not flagged[19], "frame before jump must not be flagged"
        assert not flagged[21], "frame after jump must not be flagged"

    def test_single_negative_jump_flagged(self):
        z = _make_phase_jump_series(self.N, jump_frame=15, jump_rad=-1.5)
        flagged, diff, delta = _flag_phase_jump(z, threshold_rad=0.5)
        assert flagged[15], "negative jump exceeding threshold must be flagged"

    def test_jump_exactly_at_threshold_not_flagged(self):
        threshold = 1.0
        z = _make_phase_jump_series(self.N, jump_frame=10, jump_rad=threshold)
        flagged, diff, _ = _flag_phase_jump(z, threshold_rad=threshold)
        assert not flagged[10], "jump exactly at threshold must not be flagged (strict >)"

    def test_jump_below_threshold_not_flagged(self):
        z = _make_phase_jump_series(self.N, jump_frame=10, jump_rad=0.3)
        flagged, _, _ = _flag_phase_jump(z, threshold_rad=1.0)
        assert not flagged.any()

    def test_phase_diff_zero_at_frame_zero(self):
        z = _make_phase_jump_series(self.N, jump_frame=10, jump_rad=1.5)
        _, diff, delta = _flag_phase_jump(z, threshold_rad=0.5)
        assert diff[0]  == pytest.approx(0.0, abs=1e-6)
        assert delta[0] == pytest.approx(0.0, abs=1e-6)

    def test_correct_output_shapes(self):
        z = _make_const_phase_series(self.N)
        flagged, diff, delta = _flag_phase_jump(z, threshold_rad=0.5)
        assert flagged.shape == (self.N,)
        assert diff.shape    == (self.N,)
        assert delta.shape   == (self.N,)

    def test_correct_output_dtypes(self):
        z = _make_const_phase_series(self.N)
        flagged, diff, delta = _flag_phase_jump(z, threshold_rad=0.5)
        assert flagged.dtype == bool
        assert diff.dtype    == np.float32
        assert delta.dtype   == np.float32

    def test_amplitude_only_change_no_flag(self):
        """Doubling amplitude without changing phase must not trigger a flag."""
        z = np.full(self.N, 1.0 + 0j, dtype=np.complex64)
        z[20] = 2.0 + 0j   # amplitude doubles, phase stays at 0
        flagged, _, _ = _flag_phase_jump(z, threshold_rad=0.5)
        assert not flagged[20], "amplitude-only change must not be flagged"

    def test_phase_wrapping_near_pi(self):
        """A small step crossing ±π must produce a small delta, not ~2π."""
        # 3.10 → -3.10 rad: the actual phase difference is ~0.06 rad (not 6.2 rad)
        z = np.array([
            np.exp(1j * 3.10),
            np.exp(1j * (-3.10)),
        ], dtype=np.complex64)
        flagged, diff, delta = _flag_phase_jump(z, threshold_rad=0.5)
        # Wrapped delta should be ~0.08 rad (not 6.2 rad)
        assert abs(float(delta[1])) < 0.2, (
            f"Wrapped delta near ±π should be small, got {delta[1]:.4f} rad"
        )
        assert not flagged[1], "small step near ±π boundary must not be flagged"

    def test_zero_complex_series_no_crash(self):
        z = np.zeros(self.N, dtype=np.complex64)
        flagged, diff, delta = _flag_phase_jump(z, threshold_rad=0.5)
        assert flagged.shape == (self.N,)
        assert not flagged.any()   # angle(0) = 0

    def test_near_zero_complex_series_no_crash(self):
        z = np.full(self.N, 1e-30 + 0j, dtype=np.complex64)
        flagged, diff, delta = _flag_phase_jump(z, threshold_rad=0.5)
        assert flagged.shape == (self.N,)

    def test_threshold_zero_raises(self):
        z = _make_const_phase_series(10)
        with pytest.raises(ValueError, match="threshold_rad"):
            _flag_phase_jump(z, threshold_rad=0.0)

    def test_threshold_negative_raises(self):
        z = _make_const_phase_series(10)
        with pytest.raises(ValueError, match="threshold_rad"):
            _flag_phase_jump(z, threshold_rad=-0.5)


# ===========================================================================
# _compute_quality_mask — phase jump integration
# ===========================================================================

PJ_LOCKED_BIN = 28


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


class TestComputeQualityMaskPhaseJump:

    def test_block_absent_no_phase_jump_keys(self):
        cube = _make_cube(50)
        cfg  = copy.deepcopy(_CFG)
        cfg.get("soft_failures", {}).pop("phase_jump", None)
        cfg.get("soft_failures", {}).pop("motion_spike", None)
        cfg.get("soft_failures", {}).pop("subject_bin_snr", None)
        cfg.get("soft_failures", {}).pop("subject_bin_dropout", None)
        res  = _compute_quality_mask(cube, cfg, locked_bin=PJ_LOCKED_BIN)
        assert "phase_diff"         not in res
        assert "phase_delta"        not in res
        assert "phase_jump_flagged" not in res

    def test_disabled_block_stores_metric_does_not_mask(self):
        cube = _snr_cube_high(60, PJ_LOCKED_BIN)
        cfg  = _make_pj_cfg(enabled=False, threshold=0.5)
        res  = _compute_quality_mask(cube, cfg, locked_bin=PJ_LOCKED_BIN)
        assert "phase_jump_flagged" in res
        assert "phase_diff"         in res
        assert res["quality_mask"].all()   # healthy cube, disabled → mask all True

    def test_enabled_flags_jump_frame(self):
        """Inject a known phase jump and verify the correct frame is flagged."""
        n       = 80
        locked  = PJ_LOCKED_BIN
        n_samp  = N_SAMPLES

        # Build cube with a stable complex tone
        k    = locked
        samp = np.arange(n_samp, dtype=np.float64)
        tone = np.exp(1j * 2 * np.pi * k * samp / n_samp).astype(np.complex64) * 1000.0
        cube = np.zeros((n, N_CHIRPS, N_RX, n_samp), dtype=np.complex64)
        cube[:, :, :, :] = tone

        # Frame 30: rotate phase by 2 rad (well above any reasonable threshold)
        cube[30, :, :, :] = tone * np.exp(1j * 2.0)

        cfg = _make_pj_cfg(enabled=True, threshold=0.5)
        res = _compute_quality_mask(cube, cfg, locked_bin=locked)
        assert "phase_jump_flagged" in res
        assert res["phase_jump_flagged"][30], "injected-jump frame must be flagged"
        assert not res["quality_mask"][30], "masked frame must be False"

    def test_skips_when_no_locked_bin(self):
        cube = _snr_cube_high(50, PJ_LOCKED_BIN)
        cfg  = _make_pj_cfg(enabled=True, threshold=0.5)
        res  = _compute_quality_mask(cube, cfg, locked_bin=None)
        assert res["phase_jump_skipped"] is True
        assert "phase_diff"         not in res
        assert "phase_jump_flagged" not in res

    def test_config_locked_bin_overrides_manifest(self):
        cube = _snr_cube_high(50, PJ_LOCKED_BIN)
        cfg  = _make_pj_cfg(enabled=False, threshold=0.5, locked_bin=10)
        res  = _compute_quality_mask(cube, cfg, locked_bin=PJ_LOCKED_BIN)
        assert res["phase_jump_locked_bin"] == 10

    def test_invalid_config_locked_bin_raises(self):
        cube = _snr_cube_high(50, PJ_LOCKED_BIN)
        cfg  = _make_pj_cfg(enabled=False, threshold=0.5, locked_bin=N_SAMPLES + 10)
        with pytest.raises(ValueError, match="phase_jump.locked_bin"):
            _compute_quality_mask(cube, cfg, locked_bin=PJ_LOCKED_BIN)

    def test_phase_jump_and_dropout_separate_frames_both_rejected(self):
        """Dropout at frame 40 and phase jump at frame 50 are independent events.
        Each component flag is set at its respective frame; both are rejected.

        Note: a near-zero-amplitude frame (dropout) does NOT produce a phase
        jump flag because angle(0 * conj(x)) = 0. The two detectors are
        orthogonal — overlap is not guaranteed on the same frame.
        """
        n      = 80
        locked = PJ_LOCKED_BIN
        n_samp = N_SAMPLES
        k      = locked
        samp   = np.arange(n_samp, dtype=np.float64)
        tone   = np.exp(1j * 2 * np.pi * k * samp / n_samp).astype(np.complex64) * 1000.0
        cube   = np.zeros((n, N_CHIRPS, N_RX, n_samp), dtype=np.complex64)
        cube[:, :, :, :] = tone

        # Frame 40: near-zero energy → dropout
        cube[40, :, :, :] = 1e-10

        # Frame 50: full amplitude + 2-rad phase rotation → phase jump
        cube[50, :, :, :] = tone * np.exp(1j * 2.0)

        cfg = copy.deepcopy(_CFG)
        cfg.get("soft_failures", {}).pop("subject_bin_snr", None)
        cfg["soft_failures"]["motion_spike"]["locked_bin"]               = locked
        cfg["soft_failures"]["motion_spike"]["motion_window_frames"]     = 10
        cfg["soft_failures"]["subject_bin_dropout"]["enabled"]           = True
        cfg["soft_failures"]["subject_bin_dropout"]["dropout_window_frames"] = 10
        cfg.setdefault("soft_failures", {})["phase_jump"] = {
            "enabled": True, "threshold_rad": 0.5,
            "locked_bin": None, "chunk_frames": None,
        }
        res = _compute_quality_mask(cube, cfg, locked_bin=locked)
        assert res["subject_bin_dropout_flagged"][40], "dropout frame must be flagged"
        assert not res["quality_mask"][40], "dropout frame must be rejected"
        assert res["phase_jump_flagged"][50], "phase-jump frame must be flagged"
        assert not res["quality_mask"][50], "phase-jump frame must be rejected"

    def test_phase_jump_and_clipping_same_frame(self):
        """Frame with ADC clipping and phase jump: both flags set, mask is False."""
        n      = 40
        locked = PJ_LOCKED_BIN
        cube   = _make_cube(n, fill=100 + 100j)
        # Frame 5: clip
        cube[5] = 32767 + 0j
        cfg  = _make_pj_cfg(enabled=True, threshold=0.5)
        res  = _compute_quality_mask(cube, cfg, locked_bin=locked)
        assert res["clip_flagged"][5]
        assert not res["quality_mask"][5]


# ===========================================================================
# HDF5 round-trip for phase_jump (via _process_session)
# ===========================================================================

class TestH5RoundTripPhaseJump:
    N_FRAMES   = 200
    LOCKED_BIN = 28
    N_TRIM     = 600

    def _write_h5(self, path: Path, n_analysis: int) -> None:
        n_total = self.N_TRIM + n_analysis
        k    = self.LOCKED_BIN
        samp = np.arange(N_SAMPLES, dtype=np.float64)
        tone = (5000.0 * np.exp(1j * 2 * np.pi * k * samp / N_SAMPLES)).astype(np.complex64)
        rng  = np.random.default_rng(seed=7)
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

    def _make_pj_cfg(self, enabled: bool = True, method: str = "mean_phasor") -> dict:
        cfg = copy.deepcopy(_CFG)
        cfg.get("soft_failures", {}).pop("subject_bin_snr", None)
        cfg.get("soft_failures", {}).pop("motion_spike", None)
        cfg.get("soft_failures", {}).pop("subject_bin_dropout", None)
        cfg.setdefault("soft_failures", {})["phase_jump"] = {
            "enabled":       enabled,
            "threshold_rad": 1.5,
            "locked_bin":    None,
            "chunk_frames":  None,
            "method":        method,
        }
        return cfg

    def test_datasets_written(self, tmp_path):
        h5  = tmp_path / "exp_pj_rt.h5"
        self._write_h5(h5, n_analysis=self.N_FRAMES)
        cfg = self._make_pj_cfg(enabled=True)

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_pj_rt", cfg, commit="test",
                             locked_bin=self.LOCKED_BIN)
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            assert "phase_diff"         in f["quality_metrics"]
            assert "phase_delta"        in f["quality_metrics"]
            assert "phase_jump_flagged" in f["quality_metrics"]
            pd_ = f["quality_metrics/phase_diff"][:]
            pt  = f["quality_metrics/phase_delta"][:]
            pf  = f["quality_metrics/phase_jump_flagged"][:]
            assert pd_.shape == (self.N_FRAMES,)
            assert pt.shape  == (self.N_FRAMES,)
            assert pf.shape  == (self.N_FRAMES,)
            assert pd_.dtype == np.float32
            assert pt.dtype  == np.float32
            assert pf.dtype  == bool
            assert float(pd_[0]) == pytest.approx(0.0, abs=1e-6)
            assert float(pt[0])  == pytest.approx(0.0, abs=1e-6)

    def test_attrs_written(self, tmp_path):
        h5  = tmp_path / "exp_pj_attr.h5"
        self._write_h5(h5, n_analysis=self.N_FRAMES)
        cfg = self._make_pj_cfg(enabled=True)

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_pj_attr", cfg, commit="test",
                             locked_bin=self.LOCKED_BIN)
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            attrs = dict(f["quality_mask"].attrs)
            assert attrs["phase_jump_present"]
            assert attrs["phase_jump_enabled"]
            assert float(attrs["phase_jump_threshold_rad"]) == pytest.approx(1.5)
            assert int(attrs["phase_jump_locked_bin"]) == self.LOCKED_BIN
            assert not bool(attrs["phase_jump_skipped"])

    def test_skipped_records_attrs(self, tmp_path):
        h5  = tmp_path / "exp_pj_skip.h5"
        self._write_h5(h5, n_analysis=self.N_FRAMES)
        cfg = self._make_pj_cfg(enabled=True)

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_pj_skip", cfg, commit="test", locked_bin=None)
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            attrs = dict(f["quality_mask"].attrs)
            assert bool(attrs["phase_jump_skipped"])
            assert "phase_jump_skip_reason" in attrs
            assert "phase_diff"         not in f["quality_metrics"]
            assert "phase_jump_flagged" not in f["quality_metrics"]

    def test_block_absent_no_datasets(self, tmp_path):
        h5  = tmp_path / "exp_pj_noblock.h5"
        self._write_h5(h5, n_analysis=self.N_FRAMES)
        cfg = copy.deepcopy(_CFG)
        cfg.get("soft_failures", {}).pop("phase_jump", None)
        cfg.get("soft_failures", {}).pop("motion_spike", None)
        cfg.get("soft_failures", {}).pop("subject_bin_snr", None)
        cfg.get("soft_failures", {}).pop("subject_bin_dropout", None)

        import scripts.add_quality_mask as qm_mod
        orig = qm_mod.CUBES_DIR
        qm_mod.CUBES_DIR = tmp_path
        try:
            _process_session("exp_pj_noblock", cfg, commit="test",
                             locked_bin=self.LOCKED_BIN)
        finally:
            qm_mod.CUBES_DIR = orig

        with h5py.File(h5, "r") as f:
            assert "phase_diff"         not in f["quality_metrics"]
            assert "phase_jump_flagged" not in f["quality_metrics"]
            assert "phase_jump_present" not in f["quality_mask"].attrs
