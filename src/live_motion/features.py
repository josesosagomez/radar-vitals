"""Per-frame range features for live target and movement monitoring."""
from __future__ import annotations

import collections
from dataclasses import dataclass

import numpy as np
from scipy.fft import fft as sp_fft

from src.warmup_select import derive_candidate_bins


def _readonly_copy(value: np.ndarray, dtype=None) -> np.ndarray:
    copied = np.array(value, dtype=dtype, copy=True)
    copied.setflags(write=False)
    return copied


@dataclass(frozen=True)
class MonitorFeatures:
    frame_start: int
    frame_stop: int
    valid: bool
    presence_power_db: float
    phase_activity_rad_rms: float
    range_profile_change: float
    reason: str = ""


@dataclass(frozen=True)
class FrameObservation:
    frame_index: int
    valid: bool
    raw_frame: np.ndarray
    gate_bins: np.ndarray
    gate_samples: np.ndarray
    monitor: MonitorFeatures | None = None
    invalid_reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw_frame", _readonly_copy(self.raw_frame))
        object.__setattr__(self, "gate_bins", _readonly_copy(self.gate_bins, np.int32))
        object.__setattr__(self, "gate_samples", _readonly_copy(self.gate_samples, np.complex64))


def monitor_features(observations: list[FrameObservation]) -> MonitorFeatures:
    """Calculate P, E, and G for one exact contiguous 20-frame block."""

    if len(observations) != 20:
        raise ValueError("monitor block must contain exactly 20 frames")
    frame_start = observations[0].frame_index
    frame_stop = observations[-1].frame_index + 1
    indices = [observation.frame_index for observation in observations]
    expected = list(range(frame_start, frame_stop))
    if indices != expected:
        return MonitorFeatures(
            frame_start, frame_stop, False, np.nan, np.nan, np.nan,
            "discontinuous_frame_indices",
        )
    if any(not observation.valid for observation in observations):
        reason = next(
            (observation.invalid_reason for observation in observations if not observation.valid),
            "invalid_frame",
        )
        return MonitorFeatures(frame_start, frame_stop, False, np.nan, np.nan, np.nan, reason)

    bins = observations[0].gate_bins
    if any(not np.array_equal(observation.gate_bins, bins) for observation in observations):
        return MonitorFeatures(
            frame_start, frame_stop, False, np.nan, np.nan, np.nan,
            "candidate_bins_changed",
        )
    samples = np.stack([observation.gate_samples for observation in observations])
    if samples.ndim != 4 or samples.shape[-1] == 0 or not np.all(np.isfinite(samples)):
        return MonitorFeatures(
            frame_start, frame_stop, False, np.nan, np.nan, np.nan,
            "invalid_gate_samples",
        )

    # P: block-mean ADC-squared power, maximum over the corrected range gate.
    power_by_bin = np.mean(np.abs(samples) ** 2, axis=(0, 1, 2), dtype=np.float64)
    maximum_power = float(np.max(power_by_bin))
    if not np.all(np.isfinite(power_by_bin)) or maximum_power <= 0.0:
        return MonitorFeatures(
            frame_start, frame_stop, False, np.nan, np.nan, np.nan,
            "zero_or_invalid_presence_power",
        )
    presence_power_db = float(10.0 * np.log10(maximum_power))

    # E: phase increments remain separate for every chirp/RX channel until their
    # weighted squared energy is accumulated.  This detects activity before the
    # ordinary DSP's impulse clipping step.
    eligibility = power_by_bin >= maximum_power * (10.0 ** (-12.0 / 10.0))
    phase_activity_by_bin: list[float] = []
    for bin_offset in np.flatnonzero(eligibility):
        values = samples[..., int(bin_offset)]
        products = values[1:] * np.conj(values[:-1])
        weights = np.abs(products).astype(np.float64)
        increments = np.angle(products).astype(np.float64)
        weight_sum = float(np.sum(weights, dtype=np.float64))
        if (
            weight_sum <= 0.0
            or not np.isfinite(weight_sum)
            or not np.all(np.isfinite(increments))
            or not np.all(np.isfinite(weights))
        ):
            return MonitorFeatures(
                frame_start, frame_stop, False, presence_power_db, np.nan, np.nan,
                "invalid_phase_weights",
            )
        mean_square = float(np.sum(weights * increments * increments) / weight_sum)
        if not np.isfinite(mean_square) or mean_square < 0.0:
            return MonitorFeatures(
                frame_start, frame_stop, False, presence_power_db, np.nan, np.nan,
                "invalid_phase_activity",
            )
        phase_activity_by_bin.append(float(np.sqrt(mean_square)))
    if not phase_activity_by_bin:
        return MonitorFeatures(
            frame_start, frame_stop, False, presence_power_db, np.nan, np.nan,
            "no_energy_eligible_phase_bin",
        )
    phase_activity = float(max(phase_activity_by_bin))

    # G: total-variation distance between normalized first/second-half profiles.
    half = len(observations) // 2
    first = np.mean(np.abs(samples[:half]) ** 2, axis=(0, 1, 2), dtype=np.float64)
    second = np.mean(np.abs(samples[half:]) ** 2, axis=(0, 1, 2), dtype=np.float64)
    first_sum = float(np.sum(first, dtype=np.float64))
    second_sum = float(np.sum(second, dtype=np.float64))
    if (
        first_sum <= 0.0
        or second_sum <= 0.0
        or not np.isfinite(first_sum)
        or not np.isfinite(second_sum)
    ):
        return MonitorFeatures(
            frame_start, frame_stop, False, presence_power_db, phase_activity, np.nan,
            "invalid_range_profile_normalization",
        )
    profile_change = float(0.5 * np.sum(np.abs(first / first_sum - second / second_sum)))
    if not np.isfinite(profile_change):
        return MonitorFeatures(
            frame_start, frame_stop, False, presence_power_db, phase_activity, np.nan,
            "invalid_range_profile_change",
        )
    return MonitorFeatures(
        frame_start=frame_start,
        frame_stop=frame_stop,
        valid=True,
        presence_power_db=presence_power_db,
        phase_activity_rad_rms=phase_activity,
        range_profile_change=profile_change,
    )


class RangeFeatureExtractor:
    """Perform one range FFT per valid frame and emit monitor blocks by frame index."""

    def __init__(self, cfg: dict):
        self.candidate_bins = tuple(derive_candidate_bins(cfg))
        if not self.candidate_bins:
            raise ValueError("range feature extractor candidate-bin gate is empty")
        self.monitor_window_frames = int(round(float(cfg["development_motion"]["monitor_window_s"])
                                               * float(cfg["session"]["frame_rate_hz"])))
        self.monitor_hop_frames = int(round(float(cfg["development_motion"]["monitor_hop_s"])
                                            * float(cfg["session"]["frame_rate_hz"])))
        if self.monitor_window_frames <= 1 or self.monitor_hop_frames <= 0:
            raise ValueError("invalid monitor frame counts")
        self._observations: collections.deque[FrameObservation] = collections.deque(
            maxlen=self.monitor_window_frames
        )
        self._last_monitor_stop: int | None = None
        self.feature_fft_count = 0

    def reset(self) -> None:
        self._observations.clear()
        self._last_monitor_stop = None

    def observe(
        self,
        frame_index: int,
        raw_frame: np.ndarray,
        *,
        valid: bool = True,
        invalid_reason: str = "",
    ) -> FrameObservation:
        raw = np.asarray(raw_frame)
        if raw.ndim != 3:
            raise ValueError("raw frame must have shape (chirps, rx, adc_samples)")
        if type(valid) is not bool:
            raise TypeError("frame validity must be an exact bool")
        bins = np.asarray(self.candidate_bins, dtype=np.int32)
        if valid:
            if not np.all(np.isfinite(raw)):
                valid = False
                invalid_reason = invalid_reason or "nonfinite_raw_frame"
                gate_samples = np.empty((raw.shape[0], raw.shape[1], 0), dtype=np.complex64)
            else:
                window = np.hanning(raw.shape[-1]).astype(np.float32)
                profile = sp_fft(raw * window, axis=-1)
                self.feature_fft_count += 1
                gate_samples = np.asarray(profile[..., bins], dtype=np.complex64)
        else:
            gate_samples = np.empty((raw.shape[0], raw.shape[1], 0), dtype=np.complex64)

        provisional = FrameObservation(
            frame_index=int(frame_index),
            valid=valid,
            raw_frame=raw,
            gate_bins=bins,
            gate_samples=gate_samples,
            invalid_reason=invalid_reason or ("invalid_frame" if not valid else ""),
        )
        self._observations.append(provisional)
        monitor = None
        if len(self._observations) == self.monitor_window_frames:
            stop = int(frame_index) + 1
            if self._last_monitor_stop is None or stop - self._last_monitor_stop >= self.monitor_hop_frames:
                monitor = monitor_features(list(self._observations))
                self._last_monitor_stop = stop
        return FrameObservation(
            frame_index=provisional.frame_index,
            valid=provisional.valid,
            raw_frame=provisional.raw_frame,
            gate_bins=provisional.gate_bins,
            gate_samples=provisional.gate_samples,
            monitor=monitor,
            invalid_reason=provisional.invalid_reason,
        )
