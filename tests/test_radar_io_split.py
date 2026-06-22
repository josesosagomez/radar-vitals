"""Tests for multi-file (split) support in read_adc_bin.

mmWave Studio splits recordings when they exceed the configured max file size
(typically 1 GB).  The split point does NOT necessarily fall on a frame
boundary.  These tests guard both the frame-aligned case (simpler synthetic
files) and the mid-frame-split case that matches the actual cap3_retake data.

Tests 1-6 match the task spec; test 7 covers the real-world mid-frame split.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.radar_io import ChirpConfig, infer_num_frames, read_adc_bin  # noqa: E402


# ---------------------------------------------------------------------------
# Tiny config used across most tests: small dimensions so files stay tiny.
# bytes_per_frame = 1 * 1 * 4 * 4 = 16 bytes; int16 words/frame = 8
# ---------------------------------------------------------------------------
TINY_CFG = ChirpConfig(
    num_adc_samples=4,
    num_rx=1,
    num_tx=1,
    num_chirps_per_frame=1,
    num_frames=4,
    frame_rate_hz=20.0,
    range_resolution_m=1.0,
)
BYTES_PER_FRAME = 1 * 1 * 4 * 4  # 16


def _make_frame_raw(value: int, cfg: ChirpConfig = TINY_CFG) -> np.ndarray:
    """Return raw int16 words for one frame where all 4-word packets are [v,v,v,v].

    Decodes to complex samples all equal to value + value*j.
    """
    words_per_frame = cfg.num_chirps_per_frame * cfg.num_rx * cfg.num_adc_samples * 2
    packet = np.array([value, value, value, value], dtype="<i2")
    packets_per_frame = words_per_frame // 4
    return np.tile(packet, packets_per_frame)


def _write_frames(path: Path, values: list[int], cfg: ChirpConfig = TINY_CFG) -> None:
    """Write a bin file containing one frame per value in values."""
    raw = np.concatenate([_make_frame_raw(v, cfg) for v in values])
    raw.tofile(path)


# ---------------------------------------------------------------------------
# Test 1 — single file passthrough (regression guard)
# ---------------------------------------------------------------------------
def test_single_file_passthrough(tmp_path: Path) -> None:
    """read_adc_bin with a single Path behaves identically to before."""
    f = tmp_path / "single.bin"
    _write_frames(f, [1, 2, 3, 4])
    cube = read_adc_bin(f, TINY_CFG)
    assert cube.shape == (4, 1, 1, 4)
    assert np.all(cube[0] == 1 + 1j)
    assert np.all(cube[3] == 4 + 4j)


# ---------------------------------------------------------------------------
# Test 2 — two frame-aligned files: shape check
# ---------------------------------------------------------------------------
def test_two_file_split_shape(tmp_path: Path) -> None:
    """List of two frame-aligned files returns correct total shape."""
    f0 = tmp_path / "cap_0.bin"
    f1 = tmp_path / "cap_1.bin"
    _write_frames(f0, [1, 2, 3, 4])   # 4 frames
    _write_frames(f1, [5, 6, 7, 8])   # 4 frames
    cube = read_adc_bin([f0, f1], TINY_CFG)
    assert cube.shape == (8, 1, 1, 4)


# ---------------------------------------------------------------------------
# Test 3 — two frame-aligned files: content ordering
# ---------------------------------------------------------------------------
def test_two_file_split_content_order(tmp_path: Path) -> None:
    """Frames from _0 come before frames from _1 in the returned cube."""
    f0 = tmp_path / "cap_0.bin"
    f1 = tmp_path / "cap_1.bin"
    _write_frames(f0, [10, 20])   # distinct from _1
    _write_frames(f1, [30, 40])
    cube = read_adc_bin([f0, f1], TINY_CFG)
    assert cube.shape == (4, 1, 1, 4)
    assert np.all(cube[0] == 10 + 10j), "frame 0 should come from _0"
    assert np.all(cube[1] == 20 + 20j), "frame 1 should come from _0"
    assert np.all(cube[2] == 30 + 30j), "frame 2 should come from _1"
    assert np.all(cube[3] == 40 + 40j), "frame 3 should come from _1"


# ---------------------------------------------------------------------------
# Test 4 — frame count consistency (frame-aligned files)
# ---------------------------------------------------------------------------
def test_frame_count_consistency(tmp_path: Path) -> None:
    """Total frames returned equals sum of individually inferred frame counts."""
    cfg = ChirpConfig(
        num_adc_samples=4, num_rx=1, num_tx=1, num_chirps_per_frame=1,
        num_frames=3, frame_rate_hz=20.0, range_resolution_m=1.0,
    )
    f0 = tmp_path / "cap_0.bin"
    f1 = tmp_path / "cap_1.bin"
    _write_frames(f0, [1, 2], cfg)     # 2 frames
    _write_frames(f1, [3, 4, 5], cfg)  # 3 frames
    n0 = infer_num_frames(f0, cfg)
    n1 = infer_num_frames(f1, cfg)
    cube = read_adc_bin([f0, f1], cfg)
    assert cube.shape[0] == n0 + n1


# ---------------------------------------------------------------------------
# Test 5 — missing file raises FileNotFoundError before reading
# ---------------------------------------------------------------------------
def test_missing_file_raises_before_read(tmp_path: Path) -> None:
    """FileNotFoundError is raised when any listed file is absent."""
    f0 = tmp_path / "cap_0.bin"
    _write_frames(f0, [1, 2])
    ghost = tmp_path / "nonexistent.bin"
    with pytest.raises(FileNotFoundError):
        read_adc_bin([f0, ghost], TINY_CFG)


# ---------------------------------------------------------------------------
# Test 6 — single-element list behaves identically to passing path directly
# ---------------------------------------------------------------------------
def test_single_element_list_equals_single_path(tmp_path: Path) -> None:
    """[path] produces the same cube as path."""
    f = tmp_path / "only.bin"
    _write_frames(f, [7, 8, 9])
    cfg = ChirpConfig(
        num_adc_samples=4, num_rx=1, num_tx=1, num_chirps_per_frame=1,
        num_frames=3, frame_rate_hz=20.0, range_resolution_m=1.0,
    )
    cube_single = read_adc_bin(f, cfg)
    cube_list   = read_adc_bin([f], cfg)
    np.testing.assert_array_equal(cube_single, cube_list)


# ---------------------------------------------------------------------------
# Test 7 — mid-frame split (matches real cap3_retake situation)
# ---------------------------------------------------------------------------
def test_mid_frame_split_decodes_correctly(tmp_path: Path) -> None:
    """A split that falls inside a frame (not on a frame boundary) is decoded.

    Replicates the cap3_retake situation: mmWave Studio split at 1 GB which
    was mid-frame.  _0.bin has 8191 complete frames + a partial frame;
    _1.bin completes that partial frame and adds 808 more.  This synthetic
    test uses 3 frames split exactly 1.5 frames into each file.
    """
    cfg = ChirpConfig(
        num_adc_samples=4, num_rx=1, num_tx=1, num_chirps_per_frame=1,
        num_frames=3, frame_rate_hz=20.0, range_resolution_m=1.0,
    )
    # Build complete 3-frame raw stream
    all_raw = np.concatenate([_make_frame_raw(v, cfg) for v in [1, 2, 3]])
    # 3 frames × 8 int16 words = 24 words total; split after 12 (= 1.5 frames)
    assert len(all_raw) == 24
    split = 12
    f0 = tmp_path / "cap_0.bin"
    f1 = tmp_path / "cap_1.bin"
    all_raw[:split].tofile(f0)   # 24 bytes — NOT frame-aligned
    all_raw[split:].tofile(f1)   # 24 bytes — NOT frame-aligned

    # Individual files must NOT be frame-aligned (guards the test design)
    assert (f0.stat().st_size % BYTES_PER_FRAME) != 0
    assert (f1.stat().st_size % BYTES_PER_FRAME) != 0

    cube = read_adc_bin([f0, f1], cfg)
    assert cube.shape == (3, 1, 1, 4)
    assert np.all(cube[0] == 1 + 1j), f"frame 0: {cube[0]}"
    assert np.all(cube[1] == 2 + 2j), f"frame 1: {cube[1]}"
    assert np.all(cube[2] == 3 + 3j), f"frame 2: {cube[2]}"
