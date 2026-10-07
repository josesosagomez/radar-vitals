"""Bounded, read-only decoding of recorded DCA1000 frame bytes."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import numpy as np

from scripts.live_demo import LiveFrameSource


class ReplaySourceError(RuntimeError):
    """The recorded ADC source failed an integrity or decoding contract."""


@dataclass(frozen=True)
class SourceIntegrity:
    initial_sha256: str
    final_sha256: str
    streamed_sha256: str | None
    complete: bool
    frames_read: int


class ProductionFrameDecoder:
    """Geometry-only adapter around the calibration-fingerprinted live decoder.

    ``LiveFrameSource`` is deliberately not initialized: its constructor prepares
    network state.  The adapter creates only the four attributes read by
    ``_decode_frame`` and never calls ``start`` or any hardware path.
    """

    def __init__(self, profile: dict[str, object]):
        self.num_chirps = _positive_int(profile, "num_chirps_per_frame")
        self.num_rx = _positive_int(profile, "num_rx")
        self.num_adc_samples = _positive_int(profile, "num_adc_samples")
        iq_swap = profile.get("iq_swap")
        if type(iq_swap) is not bool:
            raise ValueError("profile.iq_swap must be an exact boolean")
        self.iq_swap = iq_swap
        self.frame_bytes = (
            self.num_chirps * self.num_rx * self.num_adc_samples * 4
        )

        decoder = object.__new__(LiveFrameSource)
        decoder._n_chirps = self.num_chirps
        decoder._n_rx = self.num_rx
        decoder._n_adc = self.num_adc_samples
        decoder._iq_swap = self.iq_swap
        self._decoder = decoder

    def decode(self, frame_bytes: bytes) -> np.ndarray:
        if len(frame_bytes) != self.frame_bytes:
            raise ReplaySourceError(
                f"frame has {len(frame_bytes)} bytes; expected exactly {self.frame_bytes}"
            )
        return LiveFrameSource._decode_frame(self._decoder, frame_bytes)


def _positive_int(mapping: dict[str, object], key: str) -> int:
    value = mapping.get(key)
    if type(value) is not int or value <= 0:
        raise ValueError(f"profile.{key} must be a positive exact integer")
    return value


class SequentialAdcSource:
    """One-frame-at-a-time source with whole-file before/after integrity checks."""

    def __init__(
        self,
        path: str | Path,
        decoder: ProductionFrameDecoder,
        *,
        expected_sha256: str,
        expected_frame_count: int,
        hash_chunk_bytes: int = 1024 * 1024,
    ):
        self.path = Path(path)
        self.decoder = decoder
        self.frame_bytes = decoder.frame_bytes
        if type(expected_sha256) is not str:
            raise TypeError("expected_sha256 must be an exact string")
        if type(expected_frame_count) is not int or expected_frame_count <= 0:
            raise ValueError("expected_frame_count must be a positive exact integer")
        if type(hash_chunk_bytes) is not int or hash_chunk_bytes <= 0:
            raise ValueError("hash_chunk_bytes must be a positive exact integer")
        self.expected_sha256 = expected_sha256
        self.expected_frame_count = expected_frame_count
        self.hash_chunk_bytes = hash_chunk_bytes
        if len(self.expected_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.expected_sha256
        ):
            raise ValueError("expected_sha256 must be a SHA-256 hex digest")
        self._handle: BinaryIO | None = None
        self._opened_stat: os.stat_result | None = None
        self._stream_hash = hashlib.sha256()
        self._initial_sha256 = ""
        self._frames_read = 0
        self._eof = False
        self._integrity: SourceIntegrity | None = None

    @property
    def frame_count(self) -> int:
        return self.expected_frame_count

    @property
    def frames_read(self) -> int:
        return self._frames_read

    @property
    def initial_sha256(self) -> str:
        if not self._initial_sha256:
            raise ReplaySourceError("ADC source has not completed initial hashing")
        return self._initial_sha256

    @property
    def opened_file_identity(self) -> dict[str, int]:
        if self._opened_stat is None:
            raise ReplaySourceError("ADC source is not open")
        return {
            name: int(getattr(self._opened_stat, name))
            for name in ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        }

    def __enter__(self) -> "SequentialAdcSource":
        if self._handle is not None:
            raise ReplaySourceError("ADC source is already open")
        try:
            self._handle = self.path.open("rb", buffering=0)
            self._opened_stat = os.fstat(self._handle.fileno())
            expected_size = self.expected_frame_count * self.frame_bytes
            if self._opened_stat.st_size != expected_size:
                raise ReplaySourceError(
                    f"ADC size {self._opened_stat.st_size} != {self.expected_frame_count} "
                    f"complete frames x {self.frame_bytes} bytes"
                )
            self._initial_sha256 = self._hash_open_handle()
            if self._initial_sha256 != self.expected_sha256:
                raise ReplaySourceError(
                    "ADC SHA-256 differs from the committed radar registry"
                )
            self._handle.seek(0)
        except BaseException as exc:
            self.close_without_verification()
            if isinstance(exc, ReplaySourceError):
                raise
            raise ReplaySourceError(
                f"ADC source preparation failed: {type(exc).__name__}: {exc}"
            ) from exc
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._handle is None:
            return
        if exc_type is None:
            self.verify_integrity()
        else:
            self.close_without_verification()

    def _hash_open_handle(self) -> str:
        assert self._handle is not None
        self._handle.seek(0)
        digest = hashlib.sha256()
        while True:
            chunk = self._handle.read(self.hash_chunk_bytes)
            if not chunk:
                break
            digest.update(chunk)
        return digest.hexdigest()

    def read_next(self) -> tuple[int, np.ndarray] | None:
        if self._handle is None:
            raise ReplaySourceError("ADC source is not open")
        if self._integrity is not None:
            raise ReplaySourceError("ADC source has already been finalized")
        if self._eof:
            return None
        frame_bytes = self._handle.read(self.frame_bytes)
        if not frame_bytes:
            self._eof = True
            if self._frames_read != self.expected_frame_count:
                raise ReplaySourceError("ADC stream ended before the registered frame count")
            return None
        if len(frame_bytes) != self.frame_bytes:
            raise ReplaySourceError("ADC stream ended with an incomplete frame")
        index = self._frames_read
        self._frames_read += 1
        self._stream_hash.update(frame_bytes)
        return index, self.decoder.decode(frame_bytes)

    def verify_integrity(self) -> SourceIntegrity:
        if self._integrity is not None:
            return self._integrity
        if self._handle is None or self._opened_stat is None:
            raise ReplaySourceError("ADC source is not open")

        try:
            complete = self._frames_read == self.expected_frame_count
            streamed_sha256 = self._stream_hash.hexdigest() if complete else None
            final_sha256 = (
                streamed_sha256 if complete else self._hash_open_handle()
            )
            final_handle_stat = os.fstat(self._handle.fileno())
            final_path_stat = self.path.stat()
            identity_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
            identity_changed = any(
                getattr(final_handle_stat, name) != getattr(self._opened_stat, name)
                or getattr(final_path_stat, name) != getattr(self._opened_stat, name)
                for name in identity_fields
            )
        except BaseException as exc:
            self.close_without_verification()
            if isinstance(exc, ReplaySourceError):
                raise
            raise ReplaySourceError(
                f"ADC final integrity check failed: {type(exc).__name__}: {exc}"
            ) from exc
        self._handle.close()
        self._handle = None
        if identity_changed:
            raise ReplaySourceError("ADC file identity changed during replay")
        if final_sha256 != self._initial_sha256 or final_sha256 != self.expected_sha256:
            raise ReplaySourceError("ADC content changed during replay")
        self._integrity = SourceIntegrity(
            initial_sha256=self._initial_sha256,
            final_sha256=final_sha256,
            streamed_sha256=streamed_sha256,
            complete=complete,
            frames_read=self._frames_read,
        )
        return self._integrity

    def close_without_verification(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
