"""Independent ownership, wraparound, and phase-equivalence tests for the cache."""
from __future__ import annotations

import numpy as np
import pytest

from src.live_motion.cache import RangeBinCache, reconstruct_phase
from src.live_motion.features import RangeFeatureExtractor
from src.respiration import extract_chest_phase


def _cfg(bins):
    return {
        "session": {"frame_rate_hz": 20.0},
        "profile": {"num_adc_samples": 16, "range_resolution_m": 0.1},
        "protocol": {"subject_distance_m": [0.0, 1.5]},
        "bin_selection": {"candidate_bins": list(bins)},
        "development_motion": {"monitor_window_s": 1.0, "monitor_hop_s": 0.25},
    }


def _cube(n=40, chirps=3, rx=2, adc=16):
    rng = np.random.default_rng(20261007)
    cube = (rng.normal(size=(n, chirps, rx, adc))
            + 1j * rng.normal(size=(n, chirps, rx, adc))).astype(np.complex64)
    # Add independent channel offsets and a wrapped slow-time phase at bin 5.
    time_phase = np.mod(np.arange(n) * 2.7 + np.pi, 2 * np.pi) - np.pi
    channel_phase = np.array([[0.0, 0.6], [-0.8, 1.1], [0.3, -1.4]])
    carrier = np.exp(2j * np.pi * 5 * np.arange(adc) / adc)
    cube += (20 * np.exp(1j * (time_phase[:, None, None] + channel_phase[None]))
             [..., None] * carrier).astype(np.complex64)
    return cube


@pytest.mark.parametrize("strided", [False, True])
def test_cache_samples_are_bitwise_owned_and_phase_matches_direct_extraction(strided):
    base = _cube(n=48)
    cube = base[::2] if strided else base[:24]
    assert cube.shape[0] == 24
    bins = (2, 5, 11)
    extractor = RangeFeatureExtractor(_cfg(bins))
    cache = RangeBinCache(len(cube), cube.shape[1], cube.shape[2], bins)
    emitted = []
    for index, frame in enumerate(cube):
        observation = extractor.observe(index, frame)
        emitted.append(observation.gate_samples.copy())
        cache.append(index, observation.gate_bins, observation.gate_samples, valid=True)

    snapshot = cache.snapshot()
    # The cache contract is exact for values produced by the same feature FFT.
    np.testing.assert_array_equal(snapshot.samples, np.stack(emitted))
    direct = extract_chest_phase(cube, 5, method="delta_before_mean", clutter_removal="none")
    cached = reconstruct_phase(snapshot, 5)
    np.testing.assert_allclose(cached, direct, rtol=0.0, atol=1e-6)


def test_ring_wraparound_returns_ordered_half_open_snapshots_and_owns_data():
    cache = RangeBinCache(5, 1, 1, [1, 7])
    inputs = []
    for index in range(8):
        values = np.array([[[index + 1j, 100 + index + 2j]]], dtype=np.complex64)
        inputs.append(values.copy())
        cache.append(index, np.array([1, 7]), values, valid=True)
        values[...] = -999

    snapshot = cache.snapshot(start_frame=4, stop_frame=7)
    assert snapshot.frame_indices.tolist() == [4, 5, 6]
    np.testing.assert_array_equal(snapshot.samples, np.stack(inputs[4:7]))
    assert not snapshot.samples.flags.writeable
    with pytest.raises(ValueError):
        snapshot.samples.flat[0] = 0


def test_reconstruction_never_stitches_bins():
    cache = RangeBinCache(6, 1, 1, [3, 8])
    expected_three = np.arange(6) * 0.2
    expected_eight = np.arange(6) * -0.45
    for index in range(6):
        samples = np.array(
            [[[np.exp(1j * expected_three[index]), np.exp(1j * expected_eight[index])]]],
            dtype=np.complex64,
        )
        cache.append(index, np.array([3, 8]), samples, valid=True)
    snapshot = cache.snapshot()
    np.testing.assert_allclose(reconstruct_phase(snapshot, 3), expected_three, atol=1e-7)
    np.testing.assert_allclose(reconstruct_phase(snapshot, 8), expected_eight, atol=1e-7)


@pytest.mark.parametrize("failure", ["gap", "invalid", "absent", "empty"])
def test_reconstruction_refuses_ineligible_history(failure):
    cache = RangeBinCache(4, 1, 1, [2])
    if failure != "empty":
        indices = [0, 1, 3] if failure == "gap" else [0, 1, 2]
        for index in indices:
            cache.append(
                index,
                np.array([2]),
                np.ones((1, 1, 1), dtype=np.complex64),
                valid=not (failure == "invalid" and index == 1),
            )
    snapshot = cache.snapshot()
    requested = 9 if failure == "absent" else 2
    with pytest.raises(ValueError):
        reconstruct_phase(snapshot, requested)


def test_append_rejects_changed_bins_shapes_nonboolean_validity_and_old_indices():
    cache = RangeBinCache(3, 2, 1, [1, 4])
    good = np.ones((2, 1, 2), dtype=np.complex64)
    cache.append(4, np.array([1, 4]), good, valid=True)
    with pytest.raises(ValueError, match="candidate bins"):
        cache.append(5, np.array([1, 5]), good, valid=True)
    with pytest.raises(ValueError, match="shape"):
        cache.append(5, np.array([1, 4]), np.ones((1, 1, 2)), valid=True)
    with pytest.raises(TypeError, match="exact bool"):
        cache.append(5, np.array([1, 4]), good, valid=np.bool_(True))
    with pytest.raises(ValueError, match="increase strictly"):
        cache.append(4, np.array([1, 4]), good, valid=True)


def test_clear_removes_epoch_history_and_allows_frame_index_restart():
    cache = RangeBinCache(3, 1, 1, [1])
    sample = np.ones((1, 1, 1), dtype=np.complex64)
    cache.append(9, np.array([1]), sample, valid=True)
    cache.clear()
    cache.append(0, np.array([1]), sample, valid=True)
    assert cache.snapshot().frame_indices.tolist() == [0]


def test_current_profile_storage_is_bounded_near_fifteen_mib():
    cache = RangeBinCache(1200, 32, 4, list(range(21, 34)))
    expected_signal_bytes = 1200 * 32 * 4 * 13 * np.dtype(np.complex64).itemsize
    assert cache.storage_nbytes >= expected_signal_bytes
    assert cache.storage_nbytes < 16 * 1024 * 1024
