"""Bounded all-candidate range-bin cache for 60-second phase reconstruction."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _readonly(value: np.ndarray, dtype=None) -> np.ndarray:
    result = np.array(value, dtype=dtype, copy=True)
    result.setflags(write=False)
    return result


@dataclass(frozen=True)
class RangeCacheSnapshot:
    frame_indices: np.ndarray
    valid: np.ndarray
    candidate_bins: np.ndarray
    samples: np.ndarray

    def __post_init__(self) -> None:
        object.__setattr__(self, "frame_indices", _readonly(self.frame_indices, np.int64))
        object.__setattr__(self, "valid", _readonly(self.valid, np.bool_))
        object.__setattr__(self, "candidate_bins", _readonly(self.candidate_bins, np.int32))
        object.__setattr__(self, "samples", _readonly(self.samples, np.complex64))
        n_frames = self.frame_indices.size
        if self.frame_indices.ndim != 1 or self.valid.shape != (n_frames,):
            raise ValueError("cache snapshot indices/valid arrays disagree")
        if self.candidate_bins.ndim != 1:
            raise ValueError("cache snapshot candidate bins must be one-dimensional")
        if self.samples.ndim != 4:
            raise ValueError("cache snapshot samples must be frames x chirps x rx x bins")
        if self.samples.shape[0] != n_frames or self.samples.shape[-1] != self.candidate_bins.size:
            raise ValueError("cache snapshot sample shape disagrees with indices/bins")

    @property
    def storage_nbytes(self) -> int:
        return int(
            self.frame_indices.nbytes
            + self.valid.nbytes
            + self.candidate_bins.nbytes
            + self.samples.nbytes
        )


class RangeBinCache:
    """Owned circular complex64 cache, preserving chirp and RX channels."""

    def __init__(
        self,
        capacity_frames: int,
        n_chirps: int,
        n_rx: int,
        candidate_bins: tuple[int, ...] | list[int],
    ):
        if capacity_frames <= 0 or n_chirps <= 0 or n_rx <= 0:
            raise ValueError("cache dimensions must be positive")
        bins = np.asarray(candidate_bins, dtype=np.int32)
        if bins.ndim != 1 or bins.size == 0 or np.unique(bins).size != bins.size:
            raise ValueError("candidate_bins must be a nonempty unique sequence")
        self.capacity_frames = int(capacity_frames)
        self.n_chirps = int(n_chirps)
        self.n_rx = int(n_rx)
        self.candidate_bins = _readonly(bins, np.int32)
        self._samples = np.empty(
            (self.capacity_frames, self.n_chirps, self.n_rx, bins.size),
            dtype=np.complex64,
        )
        self._indices = np.full(self.capacity_frames, -1, dtype=np.int64)
        self._valid = np.zeros(self.capacity_frames, dtype=np.bool_)
        self._write = 0
        self._count = 0

    @property
    def storage_nbytes(self) -> int:
        return int(
            self._samples.nbytes
            + self._indices.nbytes
            + self._valid.nbytes
            + self.candidate_bins.nbytes
        )

    def clear(self) -> None:
        self._write = 0
        self._count = 0
        self._indices.fill(-1)
        self._valid.fill(False)

    def append(
        self,
        frame_index: int,
        gate_bins: np.ndarray,
        gate_samples: np.ndarray,
        *,
        valid: bool,
    ) -> None:
        if not np.array_equal(np.asarray(gate_bins, dtype=np.int32), self.candidate_bins):
            raise ValueError("cache candidate bins changed")
        samples = np.asarray(gate_samples)
        expected = (self.n_chirps, self.n_rx, self.candidate_bins.size)
        if samples.shape != expected:
            raise ValueError(f"gate_samples shape {samples.shape} != {expected}")
        if type(valid) is not bool:
            raise TypeError("cache validity must be an exact bool")
        if self._count and int(frame_index) <= int(self._latest_index()):
            raise ValueError("cache frame indices must increase strictly")
        self._samples[self._write] = samples.astype(np.complex64, copy=False)
        self._indices[self._write] = int(frame_index)
        self._valid[self._write] = valid
        self._write = (self._write + 1) % self.capacity_frames
        self._count = min(self.capacity_frames, self._count + 1)

    def _ordered_positions(self) -> np.ndarray:
        if self._count == 0:
            return np.array([], dtype=np.int64)
        start = (self._write - self._count) % self.capacity_frames
        return (start + np.arange(self._count, dtype=np.int64)) % self.capacity_frames

    def _latest_index(self) -> int:
        if not self._count:
            raise ValueError("cache is empty")
        return int(self._indices[(self._write - 1) % self.capacity_frames])

    def snapshot(
        self,
        start_frame: int | None = None,
        stop_frame: int | None = None,
    ) -> RangeCacheSnapshot:
        positions = self._ordered_positions()
        indices = self._indices[positions]
        if start_frame is not None:
            positions = positions[indices >= int(start_frame)]
            indices = self._indices[positions]
        if stop_frame is not None:
            positions = positions[indices < int(stop_frame)]
        return RangeCacheSnapshot(
            frame_indices=self._indices[positions],
            valid=self._valid[positions],
            candidate_bins=self.candidate_bins,
            samples=self._samples[positions],
        )


def reconstruct_phase(snapshot: RangeCacheSnapshot, bin_index: int) -> np.ndarray:
    """Reconstruct one uninterrupted bin using the shared DSP operation order."""

    if snapshot.frame_indices.size == 0:
        raise ValueError("cannot reconstruct phase from an empty cache snapshot")
    expected = np.arange(
        int(snapshot.frame_indices[0]),
        int(snapshot.frame_indices[0]) + snapshot.frame_indices.size,
        dtype=np.int64,
    )
    if not np.array_equal(snapshot.frame_indices, expected):
        raise ValueError("cache snapshot frame indices are not contiguous")
    if not np.all(snapshot.valid):
        raise ValueError("cache snapshot contains invalid frames")
    matches = np.flatnonzero(snapshot.candidate_bins == int(bin_index))
    if matches.size != 1:
        raise ValueError(f"bin {bin_index} is absent from cache snapshot")
    values = snapshot.samples[..., int(matches[0])]
    if not np.all(np.isfinite(values)):
        raise ValueError("cache snapshot contains nonfinite complex samples")
    phase_delta = np.zeros(values.shape[0], dtype=np.float64)
    if values.shape[0] > 1:
        products = values[1:] * np.conj(values[:-1])
        # Preserve the existing delta_before_mean operation order exactly.
        phase_delta[1:] = np.angle(products.mean(axis=(1, 2))).astype(np.float64)
    return np.cumsum(phase_delta)
