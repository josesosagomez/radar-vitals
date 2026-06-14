"""Tests for production serialization of per-window diagnostic intermediates."""
from copy import deepcopy
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import intermediates  # noqa: E402


WINDOW_FRAMES = 4
HOP_FRAMES = 2
FRAME_RATE_HZ = 20.0
N_FFT = 3
TOTAL_CUBE_FRAMES = 6


def _make_window(window_index: int) -> dict:
    start_frame = window_index * HOP_FRAMES
    end_frame = start_frame + WINDOW_FRAMES
    start_epoch = 1_800_000_000.0 + start_frame / FRAME_RATE_HZ
    end_epoch = start_epoch + WINDOW_FRAMES / FRAME_RATE_HZ
    offset = float(window_index)

    return {
        "phase_unwrapped": np.arange(WINDOW_FRAMES, dtype=float) + offset,
        "phase_clean": np.arange(WINDOW_FRAMES, dtype=float) + offset + 0.1,
        "phase_eca": np.arange(WINDOW_FRAMES, dtype=float) + offset + 0.2,
        "resp_freqs_hz": np.array([0.0, 0.25, 0.5]),
        "resp_spectrum": np.array([0.1, 2.0, 0.2]) + offset,
        "resp_peak_raw_index": 1,
        "resp_peak_raw_hz": 0.25,
        "resp_peak_refined_hz": 0.251 + offset * 0.001,
        "heart_freqs_hz": np.array([0.0, 1.0, 2.0]),
        "heart_spectrum_first_pass": np.array([0.2, 3.0, 0.3]) + offset,
        "heart_spectrum": np.array([0.1, 2.5, 0.4]) + offset,
        "heart_spectrum_stage": 2,
        "heart_peak_hz": 1.18 + offset * 0.01,
        "accepted_candidate_rank": 0,
        "accepted_candidate_initial_hz": 1.17 + offset * 0.01,
        "accepted_candidate_refined_hz": 1.18 + offset * 0.01,
        "accepted_second_harmonic_refined_hz": 2.36 + offset * 0.02,
        "candidate_attempted": np.array([True, False, False]),
        "candidate_peak_bin_index": np.array([1, -1, -1]),
        "candidate_initial_hz": np.array([1.17, np.nan, np.nan]) + offset * 0.01,
        "candidate_refined_hz": np.array([1.18, np.nan, np.nan]) + offset * 0.01,
        "candidate_peak_magnitude": np.array([3.0, np.nan, np.nan]) + offset,
        "candidate_prominence": np.array([2.0, np.nan, np.nan]) + offset,
        "candidate_argmax_fallback": np.array([False, False, False]),
        "second_peak_bin_hz": np.array([2.35, np.nan, np.nan]) + offset * 0.01,
        "second_peak_refined_hz": np.array([2.36, np.nan, np.nan]) + offset * 0.01,
        "second_peak_magnitude": np.array([1.5, np.nan, np.nan]) + offset,
        "comparison_floor": np.array([0.5, np.nan, np.nan]) + offset * 0.1,
        "peak_to_floor_ratio": np.array([3.0, np.nan, np.nan]),
        "peak_to_floor_ratio_db": np.array([9.542, np.nan, np.nan]),
        "region_available": np.array([True, False, False]),
        "candidate_passed": np.array([True, False, False]),
        "ahet_attempt_spectrum": np.array(
            [
                [0.1, 2.5, 0.4],
                [np.nan, np.nan, np.nan],
                [np.nan, np.nan, np.nan],
            ]
        )
        + offset,
        "schema_version": 1,
        "window_index": window_index,
        "start_frame": start_frame,
        "end_frame": end_frame,
        "window_frames": WINDOW_FRAMES,
        "hop_frames": HOP_FRAMES,
        "frame_rate_hz": FRAME_RATE_HZ,
        "range_bin": 29,
        "range_m": 1.264,
        "eca_applied": True,
        "f_r_outlier": False,
        "ahet_verified": True,
        "start_epoch": start_epoch,
        "end_epoch": end_epoch,
    }


def _make_windows() -> list[dict]:
    return [_make_window(0), _make_window(1)]


def test_npz_round_trip_via_production_serializer(tmp_path):
    windows = _make_windows()
    output = intermediates.write_intermediates_npz(
        tmp_path / "intermediates",
        windows,
        total_cube_frames=TOTAL_CUBE_FRAMES,
    )

    assert output == tmp_path / "intermediates.npz"
    assert output.exists()
    with np.load(output, allow_pickle=False) as archive:
        assert set(archive.files) == set(intermediates.REQUIRED_FIELDS)
        for field in archive.files:
            assert not archive[field].dtype.hasobject

        np.testing.assert_array_equal(archive["window_index"], [0, 1])
        np.testing.assert_array_equal(archive["start_frame"], [0, 2])
        np.testing.assert_array_equal(archive["end_frame"], [4, 6])
        np.testing.assert_allclose(
            archive["phase_clean"],
            np.stack([window["phase_clean"] for window in windows]),
        )
        assert archive["phase_clean"].shape == (2, WINDOW_FRAMES)
        assert archive["candidate_attempted"].shape == (
            2,
            intermediates.AHET_MAX_CANDIDATES,
        )
        assert archive["ahet_attempt_spectrum"].shape == (
            2,
            intermediates.AHET_MAX_CANDIDATES,
            N_FFT,
        )


def test_serializer_raises_on_missing_field(tmp_path):
    windows = _make_windows()
    del windows[1]["phase_eca"]

    with pytest.raises(
        intermediates.IntermediateValidationError,
        match="missing required fields: phase_eca",
    ):
        intermediates.write_intermediates_npz(
            tmp_path / "missing.npz",
            windows,
            total_cube_frames=TOTAL_CUBE_FRAMES,
        )


def test_serializer_raises_on_inconsistent_shapes(tmp_path):
    windows = _make_windows()
    windows[1]["phase_clean"] = windows[1]["phase_clean"][:-1]

    with pytest.raises(
        intermediates.IntermediateValidationError,
        match="inconsistent shapes",
    ):
        intermediates.write_intermediates_npz(
            tmp_path / "shapes.npz",
            windows,
            total_cube_frames=TOTAL_CUBE_FRAMES,
        )


def test_serializer_raises_on_frame_duration_mismatch(tmp_path):
    windows = _make_windows()
    windows[0]["end_frame"] += 1

    with pytest.raises(
        intermediates.IntermediateValidationError,
        match="frame duration",
    ):
        intermediates.write_intermediates_npz(
            tmp_path / "duration.npz",
            windows,
            total_cube_frames=TOTAL_CUBE_FRAMES,
        )


def test_serializer_raises_on_non_contiguous_window_index(tmp_path):
    windows = _make_windows()
    windows[1]["window_index"] = 2

    with pytest.raises(
        intermediates.IntermediateValidationError,
        match="unique, contiguous, and ordered from 0",
    ):
        intermediates.write_intermediates_npz(
            tmp_path / "indices.npz",
            windows,
            total_cube_frames=TOTAL_CUBE_FRAMES,
        )


def test_serializer_raises_on_object_dtype(tmp_path):
    windows = deepcopy(_make_windows())
    windows[0]["phase_clean"] = np.array(
        windows[0]["phase_clean"],
        dtype=object,
    )

    with pytest.raises(
        intermediates.IntermediateValidationError,
        match="object dtype",
    ):
        intermediates.write_intermediates_npz(
            tmp_path / "objects.npz",
            windows,
            total_cube_frames=TOTAL_CUBE_FRAMES,
        )
