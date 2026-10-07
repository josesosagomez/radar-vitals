"""Independent numerical and boundary tests for live-motion range features."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import numpy as np
import pytest
from scipy.fft import fft as scipy_fft

import src.live_motion.features as features
from src.live_motion.features import FrameObservation, RangeFeatureExtractor, monitor_features


def _cfg(*, bins=(1, 2), fs=20.0):
    return {
        "session": {"frame_rate_hz": fs},
        "profile": {"num_adc_samples": 8, "range_resolution_m": 0.1},
        "protocol": {"subject_distance_m": [0.0, 0.7]},
        "bin_selection": {"candidate_bins": list(bins)},
        "development_motion": {"monitor_window_s": 1.0, "monitor_hop_s": 0.25},
    }


def _observation(index: int, samples: np.ndarray, *, valid=True, reason=""):
    samples = np.asarray(samples, dtype=np.complex64)
    return FrameObservation(
        frame_index=index,
        valid=valid,
        raw_frame=np.zeros((*samples.shape[:-1], 8), dtype=np.complex64),
        gate_bins=np.arange(samples.shape[-1], dtype=np.int32),
        gate_samples=samples,
        invalid_reason=reason,
    )


def test_monitor_features_match_independent_p_e_g_equations():
    """P/E/G use block power, channel-wise circular increments, and profile TV."""
    n = 20
    samples = np.empty((n, 2, 2, 2), dtype=np.complex64)
    phases = np.arange(n, dtype=np.float64) * 0.11
    # Unequal channel amplitudes make averaging increments before energy measurably wrong.
    amplitudes = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
    samples[..., 0] = amplitudes[None] * np.exp(1j * phases[:, None, None])
    samples[:10, ..., 1] = 0.5
    samples[10:, ..., 1] = 1.5 * np.exp(1j * 0.07 * np.arange(10))[:, None, None]
    observations = [_observation(i, samples[i]) for i in range(n)]

    actual = monitor_features(observations)

    power = np.mean(np.abs(samples) ** 2, axis=(0, 1, 2), dtype=np.float64)
    expected_p = 10.0 * np.log10(power.max())
    expected_e = []
    for local_bin in np.flatnonzero(power >= power.max() * 10 ** (-12.0 / 10.0)):
        products = samples[1:, ..., local_bin] * np.conj(samples[:-1, ..., local_bin])
        weights = np.abs(products).astype(np.float64)
        increments = np.angle(products).astype(np.float64)
        expected_e.append(np.sqrt(np.sum(weights * increments**2) / np.sum(weights)))
    first = np.mean(np.abs(samples[:10]) ** 2, axis=(0, 1, 2), dtype=np.float64)
    second = np.mean(np.abs(samples[10:]) ** 2, axis=(0, 1, 2), dtype=np.float64)
    expected_g = 0.5 * np.sum(np.abs(first / first.sum() - second / second.sum()))

    assert actual.valid is True
    assert (actual.frame_start, actual.frame_stop) == (0, 20)
    assert actual.presence_power_db == pytest.approx(expected_p, abs=1e-12)
    assert actual.phase_activity_rad_rms == pytest.approx(max(expected_e), abs=1e-12)
    assert actual.range_profile_change == pytest.approx(expected_g, abs=1e-12)


def test_phase_activity_is_separate_by_channel_and_ignores_below_minus_12_db_bin():
    n = 20
    strong = np.ones((n, 1, 2), dtype=np.complex64)
    # Opposite channel increments cancel if phasors are averaged before increment energy.
    phase = 0.4 * np.arange(n)
    strong[:, 0, 0] = np.exp(1j * phase)
    strong[:, 0, 1] = np.exp(-1j * phase)
    weak_fast = np.repeat(
        (0.1 * np.exp(1j * 1.7 * np.arange(n)))[:, None, None], 2, axis=2
    )
    samples = np.concatenate((strong[..., None], weak_fast[..., None]), axis=-1)

    result = monitor_features([_observation(i, samples[i]) for i in range(n)])

    assert result.valid
    assert result.phase_activity_rad_rms == pytest.approx(0.4, abs=2e-7)


@pytest.mark.parametrize(
    "observations, reason",
    [
        ([_observation(i if i < 10 else i + 1, np.ones((1, 1, 1))) for i in range(20)],
         "discontinuous_frame_indices"),
        ([_observation(i, np.ones((1, 1, 1)), valid=i != 7, reason="packet_gap")
          for i in range(20)], "packet_gap"),
        ([_observation(i, np.zeros((1, 1, 1))) for i in range(20)],
         "zero_or_invalid_presence_power"),
    ],
)
def test_invalidity_precedes_any_quiet_feature_interpretation(observations, reason):
    result = monitor_features(observations)
    assert result.valid is False
    assert result.reason == reason
    assert np.isnan(result.phase_activity_rad_rms)
    assert np.isnan(result.range_profile_change)


def test_nonfinite_gate_samples_are_invalid_and_never_quiet():
    observations = [_observation(i, np.ones((1, 1, 1))) for i in range(20)]
    observations[3] = _observation(3, np.full((1, 1, 1), np.nan + 0j))
    result = monitor_features(observations)
    assert not result.valid
    assert result.reason == "invalid_gate_samples"
    assert np.isnan(result.presence_power_db)


def test_observe_performs_exactly_one_fft_per_valid_finite_frame(monkeypatch):
    calls = []
    original = scipy_fft

    def counted_fft(value, *, axis):
        calls.append((np.asarray(value).shape, axis))
        return original(value, axis=axis)

    monkeypatch.setattr(features, "sp_fft", counted_fft)
    extractor = RangeFeatureExtractor(_cfg())
    frame = np.ones((2, 3, 8), dtype=np.complex64)

    valid = extractor.observe(0, frame)
    invalid = extractor.observe(1, frame, valid=False, invalid_reason="packet_gap")
    nonfinite = frame.copy()
    nonfinite[0, 0, 0] = np.nan
    rejected = extractor.observe(2, nonfinite)

    assert calls == [((2, 3, 8), -1)]
    assert extractor.feature_fft_count == 1
    assert valid.valid is True
    assert invalid.invalid_reason == "packet_gap"
    assert rejected.invalid_reason == "nonfinite_raw_frame"


def test_monitor_emission_uses_20_frame_window_and_five_frame_hop():
    extractor = RangeFeatureExtractor(_cfg())
    frame = np.ones((1, 1, 8), dtype=np.complex64)
    emitted = []
    for index in range(31):
        observation = extractor.observe(index, frame)
        if observation.monitor is not None:
            emitted.append((index, observation.monitor.frame_start, observation.monitor.frame_stop))
    assert emitted == [(19, 0, 20), (24, 5, 25), (29, 10, 30)]


def test_observation_owns_read_only_copies_of_input_arrays():
    raw = np.ones((1, 1, 8), dtype=np.complex64)
    bins = np.array([1], dtype=np.int32)
    gate = np.ones((1, 1, 1), dtype=np.complex64)
    observation = FrameObservation(0, True, raw, bins, gate)
    raw[...] = 9
    bins[...] = 7
    gate[...] = 4

    assert np.all(observation.raw_frame == 1)
    assert observation.gate_bins.tolist() == [1]
    assert np.all(observation.gate_samples == 1)
    for array in (observation.raw_frame, observation.gate_bins, observation.gate_samples):
        assert not array.flags.writeable
        with pytest.raises(ValueError):
            array.flat[0] = 0
    with pytest.raises(FrozenInstanceError):
        observation.valid = False


def test_observe_requires_exact_boolean_validity_and_frame_shape():
    extractor = RangeFeatureExtractor(_cfg())
    with pytest.raises(TypeError, match="exact bool"):
        extractor.observe(0, np.ones((1, 1, 8)), valid=np.bool_(True))
    with pytest.raises(ValueError, match="shape"):
        extractor.observe(0, np.ones((1, 8)))
