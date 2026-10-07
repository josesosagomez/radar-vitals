"""Deterministic tests for bounded production decoding and playback pacing."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
import pytest

from src.radar_io import ChirpConfig, read_adc_bin
from src.replay_compare.clock import PlaybackClock, SUPPORTED_SPEEDS
import src.replay_compare.source as source_module
from src.replay_compare.source import (
    ProductionFrameDecoder,
    ReplaySourceError,
    SequentialAdcSource,
)


def _profile(*, iq_swap: bool, adc: int = 3, rx: int = 2, chirps: int = 2):
    return {
        "num_adc_samples": adc,
        "num_rx": rx,
        "num_chirps_per_frame": chirps,
        "iq_swap": iq_swap,
    }


def _chirp_config(*, iq_swap: bool, frames: int = 1) -> ChirpConfig:
    return ChirpConfig(
        num_adc_samples=3,
        num_rx=2,
        num_tx=1,
        num_chirps_per_frame=2,
        num_frames=frames,
        frame_rate_hz=20.0,
        range_resolution_m=0.0436,
        iq_swap=iq_swap,
    )


def _frame_bytes(offset: int = 0) -> bytes:
    # 2 chirps * 2 RX * 3 complex samples * 2 int16 words per complex sample.
    return (np.arange(24, dtype="<i2") + offset).tobytes()


@pytest.mark.parametrize("iq_swap", [False, True])
def test_production_frame_decoder_is_bit_exact_read_adc_bin(
    tmp_path: Path, iq_swap: bool
):
    path = tmp_path / "one_frame.bin"
    payload = _frame_bytes()
    path.write_bytes(payload)

    expected = read_adc_bin(path, _chirp_config(iq_swap=iq_swap))[0]
    decoder = ProductionFrameDecoder(_profile(iq_swap=iq_swap))
    actual = decoder.decode(payload)

    np.testing.assert_array_equal(actual, expected)
    assert actual.shape == (2, 2, 3)
    assert actual.dtype == np.complex64


def test_production_geometry_is_exactly_131072_bytes_per_frame():
    decoder = ProductionFrameDecoder(
        _profile(iq_swap=True, adc=256, rx=4, chirps=32)
    )

    assert decoder.frame_bytes == 131_072


def test_decoder_rejects_nonwhole_frame_without_padding_or_truncation():
    decoder = ProductionFrameDecoder(_profile(iq_swap=True))

    with pytest.raises(ReplaySourceError, match="expected exactly"):
        decoder.decode(_frame_bytes()[:-2])


class _TrackedBinaryHandle:
    def __init__(self, handle, reads: list[int]):
        self._handle = handle
        self._reads = reads

    def read(self, size=-1):
        self._reads.append(size)
        return self._handle.read(size)

    def __getattr__(self, name):
        return getattr(self._handle, name)


def test_sequential_source_uses_bounded_reads_and_never_materializes_recording(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    decoder = ProductionFrameDecoder(_profile(iq_swap=True))
    payload = _frame_bytes(0) + _frame_bytes(100)
    path = tmp_path / "two_frames.bin"
    path.write_bytes(payload)
    reads: list[int] = []
    original_open = Path.open

    def tracked_open(self, *args, **kwargs):
        handle = original_open(self, *args, **kwargs)
        return _TrackedBinaryHandle(handle, reads) if self == path else handle

    monkeypatch.setattr(Path, "open", tracked_open)
    source = SequentialAdcSource(
        path,
        decoder,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        expected_frame_count=2,
        hash_chunk_bytes=17,
    )
    with source:
        first = source.read_next()
        second = source.read_next()
        assert source.read_next() is None

    assert first is not None and first[0] == 0
    assert second is not None and second[0] == 1
    assert reads
    assert -1 not in reads
    assert max(reads) <= decoder.frame_bytes
    assert reads.count(decoder.frame_bytes) == 3  # two frames plus EOF probe
    assert source.frames_read == 2


@pytest.mark.parametrize("payload", [b"", _frame_bytes()[:-2]])
def test_sequential_source_rejects_empty_or_truncated_adc(tmp_path, payload):
    decoder = ProductionFrameDecoder(_profile(iq_swap=True))
    path = tmp_path / "bad.bin"
    path.write_bytes(payload)
    source = SequentialAdcSource(
        path,
        decoder,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        expected_frame_count=1,
    )

    with pytest.raises(ReplaySourceError, match="ADC size"):
        source.__enter__()


def test_sequential_source_rejects_pre_run_substitution_against_expected_digest(
    tmp_path: Path,
):
    decoder = ProductionFrameDecoder(_profile(iq_swap=True))
    registered = _frame_bytes(0)
    substituted = _frame_bytes(1)
    path = tmp_path / "substituted.bin"
    path.write_bytes(substituted)
    source = SequentialAdcSource(
        path,
        decoder,
        expected_sha256=hashlib.sha256(registered).hexdigest(),
        expected_frame_count=1,
        hash_chunk_bytes=11,
    )

    with pytest.raises(ReplaySourceError, match="committed radar registry"):
        source.__enter__()


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"expected_sha256": "A" * 64}, "SHA-256"),
        ({"expected_sha256": "g" * 64}, "SHA-256"),
        ({"expected_frame_count": True}, "expected_frame_count"),
        ({"expected_frame_count": 1.5}, "expected_frame_count"),
        ({"hash_chunk_bytes": True}, "hash_chunk_bytes"),
        ({"hash_chunk_bytes": 1.5}, "hash_chunk_bytes"),
    ],
)
def test_source_constructor_requires_exact_types_and_lowercase_digest(
    tmp_path, overrides, match
):
    decoder = ProductionFrameDecoder(_profile(iq_swap=True))
    payload = _frame_bytes()
    path = tmp_path / "frame.bin"
    path.write_bytes(payload)
    kwargs = {
        "expected_sha256": hashlib.sha256(payload).hexdigest(),
        "expected_frame_count": 1,
        "hash_chunk_bytes": 64,
    }
    kwargs.update(overrides)

    with pytest.raises(ValueError, match=match):
        SequentialAdcSource(path, decoder, **kwargs)


def test_enter_hash_read_failure_closes_handle(tmp_path, monkeypatch):
    decoder = ProductionFrameDecoder(_profile(iq_swap=True))
    payload = _frame_bytes()
    path = tmp_path / "enter_read_failure.bin"
    path.write_bytes(payload)
    source = SequentialAdcSource(
        path,
        decoder,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        expected_frame_count=1,
    )
    monkeypatch.setattr(
        source,
        "_hash_open_handle",
        lambda: (_ for _ in ()).throw(OSError("injected initial read failure")),
    )

    with pytest.raises(ReplaySourceError, match="initial|hash|read"):
        source.__enter__()
    assert source._handle is None


def test_partial_pass_finalizes_with_bounded_full_file_hash(tmp_path: Path):
    decoder = ProductionFrameDecoder(_profile(iq_swap=False))
    payload = _frame_bytes(0) + _frame_bytes(100)
    path = tmp_path / "partial.bin"
    path.write_bytes(payload)
    source = SequentialAdcSource(
        path,
        decoder,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        expected_frame_count=2,
        hash_chunk_bytes=13,
    )

    with source:
        assert source.read_next()[0] == 0

    integrity = source.verify_integrity()
    assert integrity.complete is False
    assert integrity.frames_read == 1
    assert integrity.streamed_sha256 is None
    assert integrity.initial_sha256 == integrity.final_sha256


def test_complete_pass_digest_is_from_exact_streamed_bytes(tmp_path: Path):
    decoder = ProductionFrameDecoder(_profile(iq_swap=True))
    payload = _frame_bytes(0) + _frame_bytes(100)
    digest = hashlib.sha256(payload).hexdigest()
    path = tmp_path / "complete.bin"
    path.write_bytes(payload)
    source = SequentialAdcSource(
        path,
        decoder,
        expected_sha256=digest,
        expected_frame_count=2,
    )

    with source:
        assert source.read_next()[0] == 0
        assert source.read_next()[0] == 1

    integrity = source.verify_integrity()
    assert integrity.complete is True
    assert integrity.streamed_sha256 == digest
    assert integrity.initial_sha256 == integrity.final_sha256 == digest


def test_source_detects_same_size_content_change_during_partial_pass(tmp_path: Path):
    decoder = ProductionFrameDecoder(_profile(iq_swap=True))
    payload = _frame_bytes(0) + _frame_bytes(100)
    path = tmp_path / "changed.bin"
    path.write_bytes(payload)
    source = SequentialAdcSource(
        path,
        decoder,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        expected_frame_count=2,
        hash_chunk_bytes=7,
    )

    source.__enter__()
    assert source.read_next()[0] == 0
    try:
        with path.open("r+b") as handle:
            handle.seek(decoder.frame_bytes)
            handle.write(_frame_bytes(101))
    except PermissionError as exc:
        source.close_without_verification()
        pytest.skip(f"open-file mutation unavailable in this environment: {exc}")

    with pytest.raises(ReplaySourceError, match="identity changed|content changed"):
        source.verify_integrity()


def test_partial_final_hash_failure_closes_handle(tmp_path, monkeypatch):
    decoder = ProductionFrameDecoder(_profile(iq_swap=True))
    payload = _frame_bytes(0) + _frame_bytes(100)
    path = tmp_path / "final_read_failure.bin"
    path.write_bytes(payload)
    source = SequentialAdcSource(
        path,
        decoder,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        expected_frame_count=2,
    )
    source.__enter__()
    assert source.read_next()[0] == 0
    monkeypatch.setattr(
        source,
        "_hash_open_handle",
        lambda: (_ for _ in ()).throw(OSError("injected final read failure")),
    )

    with pytest.raises(ReplaySourceError, match="final|hash|read"):
        source.verify_integrity()
    assert source._handle is None


def test_final_fstat_failure_closes_handle(tmp_path, monkeypatch):
    decoder = ProductionFrameDecoder(_profile(iq_swap=True))
    payload = _frame_bytes()
    path = tmp_path / "final_fstat_failure.bin"
    path.write_bytes(payload)
    source = SequentialAdcSource(
        path,
        decoder,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        expected_frame_count=1,
    )
    original_fstat = source_module.os.fstat
    calls = 0

    def failing_second_fstat(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected final fstat failure")
        return original_fstat(fd)

    monkeypatch.setattr(source_module.os, "fstat", failing_second_fstat)
    source.__enter__()
    assert source.read_next()[0] == 0

    with pytest.raises(ReplaySourceError, match="identity|stat"):
        source.verify_integrity()
    assert source._handle is None


class _FakeTime:
    def __init__(self):
        self.now = 10.0
        self.waits: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def wait(self, seconds: float) -> None:
        assert math.isfinite(seconds) and seconds >= 0
        self.waits.append(seconds)
        self.now += seconds


def test_clock_pause_is_acknowledged_and_resume_does_not_catch_up():
    fake = _FakeTime()
    clock = PlaybackClock(20.0, monotonic=fake.monotonic, wait=fake.wait)
    assert clock.wait_until_next_frame() is True
    clock.frame_processed(1)
    clock.request_pause()
    assert clock.snapshot.state == "pausing"
    assert clock.wait_until_next_frame() is False
    clock.acknowledge_pause()
    frozen = clock.snapshot
    fake.now += 100.0
    assert clock.wait_until_next_frame() is False
    assert clock.snapshot == frozen

    clock.resume()
    assert clock.wait_until_next_frame() is True
    assert fake.waits == pytest.approx([0.05, 0.05])
    assert clock.snapshot.processed_frames == 1


def test_clock_speed_change_reanchors_at_current_frame_without_skip_or_duplicate():
    fake = _FakeTime()
    clock = PlaybackClock(20.0, speed=1.0, monotonic=fake.monotonic, wait=fake.wait)
    for boundary in range(1, 4):
        assert clock.wait_until_next_frame() is True
        clock.frame_processed(boundary)
    clock.set_speed(4.0)
    assert clock.snapshot.processed_frames == 3
    assert clock.snapshot.speed == 4.0
    assert clock.wait_until_next_frame() is True
    clock.frame_processed(4)

    assert fake.waits[:3] == pytest.approx([0.05, 0.05, 0.05])
    assert fake.waits[3] == pytest.approx(0.0125)
    assert clock.snapshot.processed_frames == 4


@pytest.mark.parametrize("speed", SUPPORTED_SPEEDS)
def test_identical_controlled_frame_trace_is_speed_invariant(speed):
    fake = _FakeTime()
    clock = PlaybackClock(20.0, speed=speed, monotonic=fake.monotonic, wait=fake.wait)
    trace = []
    for boundary in range(1, 9):
        assert clock.wait_until_next_frame() is True
        clock.frame_processed(boundary)
        trace.append(clock.snapshot.processed_frames)

    assert trace == list(range(1, 9))
    assert sum(fake.waits) == pytest.approx(8 / (20.0 * speed))


@pytest.mark.parametrize("speed", [0.0, -1.0, 3.0, math.inf, math.nan])
def test_clock_rejects_unsupported_speed(speed):
    with pytest.raises(ValueError, match="speed must be one of"):
        PlaybackClock(20.0, speed=speed)
