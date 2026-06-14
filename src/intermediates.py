"""Validation and NPZ serialization for per-window vital-sign intermediates."""
from __future__ import annotations

from pathlib import Path
import tempfile
from typing import Any

import numpy as np


AHET_MAX_CANDIDATES = 3

SCALAR_FIELDS = (
    "resp_peak_raw_index",
    "resp_peak_raw_hz",
    "resp_peak_refined_hz",
    "heart_spectrum_stage",
    "heart_peak_hz",
    "accepted_candidate_rank",
    "accepted_candidate_initial_hz",
    "accepted_candidate_refined_hz",
    "accepted_second_harmonic_refined_hz",
    "schema_version",
    "window_index",
    "start_frame",
    "end_frame",
    "window_frames",
    "hop_frames",
    "frame_rate_hz",
    "range_bin",
    "range_m",
    "eca_applied",
    "f_r_outlier",
    "ahet_verified",
    "start_epoch",
    "end_epoch",
)

PHASE_FIELDS = (
    "phase_unwrapped",
    "phase_clean",
    "phase_eca",
)

SPECTRUM_FIELDS = (
    "resp_freqs_hz",
    "resp_spectrum",
    "heart_freqs_hz",
    "heart_spectrum_first_pass",
    "heart_spectrum",
)

AHET_ATTEMPT_FIELDS = (
    "candidate_attempted",
    "candidate_peak_bin_index",
    "candidate_initial_hz",
    "candidate_refined_hz",
    "candidate_peak_magnitude",
    "candidate_prominence",
    "candidate_argmax_fallback",
    "second_peak_bin_hz",
    "second_peak_refined_hz",
    "second_peak_magnitude",
    "comparison_floor",
    "peak_to_floor_ratio",
    "peak_to_floor_ratio_db",
    "region_available",
    "candidate_passed",
)

AHET_SPECTRUM_FIELD = "ahet_attempt_spectrum"
ARRAY_FIELDS = (
    *PHASE_FIELDS,
    *SPECTRUM_FIELDS,
    *AHET_ATTEMPT_FIELDS,
    AHET_SPECTRUM_FIELD,
)
REQUIRED_FIELDS = (*SCALAR_FIELDS, *ARRAY_FIELDS)


class IntermediateValidationError(ValueError):
    """Raised when per-window intermediate data violates the archive schema."""


def _as_numeric_array(value: Any, field: str, row_index: int) -> np.ndarray:
    array = np.asarray(value)
    if array.dtype.hasobject:
        raise IntermediateValidationError(
            f"Row {row_index} field {field!r} has object dtype."
        )
    if array.dtype.kind not in "biufc":
        raise IntermediateValidationError(
            f"Row {row_index} field {field!r} must be numeric or boolean; "
            f"got dtype {array.dtype}."
        )
    return array


def _validate_required_fields(windows: list[dict]) -> None:
    for row_index, window in enumerate(windows):
        missing = [field for field in REQUIRED_FIELDS if field not in window]
        if missing:
            raise IntermediateValidationError(
                f"Row {row_index} is missing required fields: {', '.join(missing)}."
            )


def _validate_scalar_fields(windows: list[dict]) -> None:
    for row_index, window in enumerate(windows):
        for field in SCALAR_FIELDS:
            array = _as_numeric_array(window[field], field, row_index)
            if array.ndim != 0:
                raise IntermediateValidationError(
                    f"Row {row_index} field {field!r} must be scalar; "
                    f"got shape {array.shape}."
                )


def _validate_array_fields(windows: list[dict]) -> dict[str, tuple[int, ...]]:
    expected_shapes: dict[str, tuple[int, ...]] = {}
    for row_index, window in enumerate(windows):
        for field in ARRAY_FIELDS:
            array = _as_numeric_array(window[field], field, row_index)
            expected_ndim = 2 if field == AHET_SPECTRUM_FIELD else 1
            if array.ndim != expected_ndim:
                raise IntermediateValidationError(
                    f"Row {row_index} field {field!r} must be {expected_ndim}-D; "
                    f"got shape {array.shape}."
                )
            if field not in expected_shapes:
                expected_shapes[field] = array.shape
            elif array.shape != expected_shapes[field]:
                raise IntermediateValidationError(
                    f"Field {field!r} has inconsistent shapes: expected "
                    f"{expected_shapes[field]}, got {array.shape} at row {row_index}."
                )
    return expected_shapes


def _validate_window_alignment(
    windows: list[dict],
    total_cube_frames: int,
) -> None:
    indices: list[int] = []
    for row_index, window in enumerate(windows):
        window_index = int(window["window_index"])
        start_frame = int(window["start_frame"])
        end_frame = int(window["end_frame"])
        window_frames = int(window["window_frames"])
        frame_rate_hz = float(window["frame_rate_hz"])
        start_epoch = float(window["start_epoch"])
        end_epoch = float(window["end_epoch"])

        indices.append(window_index)
        if start_frame < 0:
            raise IntermediateValidationError(
                f"Row {row_index} start_frame must be >= 0; got {start_frame}."
            )
        if end_frame > total_cube_frames:
            raise IntermediateValidationError(
                f"Row {row_index} end_frame={end_frame} exceeds "
                f"total_cube_frames={total_cube_frames}."
            )
        if end_frame - start_frame != window_frames:
            raise IntermediateValidationError(
                f"Row {row_index} frame duration is {end_frame - start_frame}, "
                f"but window_frames is {window_frames}."
            )
        if window_frames <= 0:
            raise IntermediateValidationError(
                f"Row {row_index} window_frames must be positive."
            )
        if frame_rate_hz <= 0.0 or not np.isfinite(frame_rate_hz):
            raise IntermediateValidationError(
                f"Row {row_index} frame_rate_hz must be finite and positive."
            )
        if not np.isfinite(start_epoch) or not np.isfinite(end_epoch):
            raise IntermediateValidationError(
                f"Row {row_index} start_epoch and end_epoch must be finite."
            )

        epoch_duration = end_epoch - start_epoch
        expected_duration = window_frames / frame_rate_hz
        one_frame_tolerance = 1.0 / frame_rate_hz
        if not np.isclose(
            epoch_duration,
            expected_duration,
            rtol=0.0,
            atol=one_frame_tolerance,
        ):
            raise IntermediateValidationError(
                f"Row {row_index} epoch duration {epoch_duration:.9g} s does not "
                f"match {expected_duration:.9g} s within one frame "
                f"({one_frame_tolerance:.9g} s)."
            )

        for field in PHASE_FIELDS:
            if np.asarray(window[field]).shape != (window_frames,):
                raise IntermediateValidationError(
                    f"Row {row_index} field {field!r} must have shape "
                    f"({window_frames},)."
                )

    expected_indices = list(range(len(windows)))
    if indices != expected_indices:
        raise IntermediateValidationError(
            "window_index values must be unique, contiguous, and ordered from 0; "
            f"got {indices}."
        )


def _validate_cross_field_shapes(
    windows: list[dict],
    shapes: dict[str, tuple[int, ...]],
) -> None:
    if shapes["resp_freqs_hz"] != shapes["resp_spectrum"]:
        raise IntermediateValidationError(
            "resp_freqs_hz and resp_spectrum must have identical shapes."
        )

    heart_shape = shapes["heart_freqs_hz"]
    for field in ("heart_spectrum_first_pass", "heart_spectrum"):
        if shapes[field] != heart_shape:
            raise IntermediateValidationError(
                f"{field} must match heart_freqs_hz shape {heart_shape}; "
                f"got {shapes[field]}."
            )

    for field in AHET_ATTEMPT_FIELDS:
        if shapes[field] != (AHET_MAX_CANDIDATES,):
            raise IntermediateValidationError(
                f"{field} must have shape ({AHET_MAX_CANDIDATES},); "
                f"got {shapes[field]}."
            )

    expected_attempt_shape = (AHET_MAX_CANDIDATES, heart_shape[0])
    if shapes[AHET_SPECTRUM_FIELD] != expected_attempt_shape:
        raise IntermediateValidationError(
            f"{AHET_SPECTRUM_FIELD} must have shape {expected_attempt_shape}; "
            f"got {shapes[AHET_SPECTRUM_FIELD]}."
        )

    for row_index, window in enumerate(windows):
        raw_index = int(window["resp_peak_raw_index"])
        if raw_index < 0 or raw_index >= shapes["resp_freqs_hz"][0]:
            raise IntermediateValidationError(
                f"Row {row_index} resp_peak_raw_index={raw_index} is outside "
                f"the respiratory spectrum."
            )


def _stack_fields(windows: list[dict]) -> dict[str, np.ndarray]:
    stacked: dict[str, np.ndarray] = {}
    for field in REQUIRED_FIELDS:
        values = [_as_numeric_array(window[field], field, i) for i, window in enumerate(windows)]
        stacked[field] = np.stack(values, axis=0)

    row_count = len(windows)
    for field, array in stacked.items():
        if array.shape[0] != row_count:
            raise IntermediateValidationError(
                f"Stacked field {field!r} has {array.shape[0]} rows; "
                f"expected {row_count}."
            )
        if array.dtype.hasobject:
            raise IntermediateValidationError(
                f"Stacked field {field!r} has object dtype."
            )
    return stacked


def _validate_and_stack(
    windows: list[dict],
    total_cube_frames: int,
) -> dict[str, np.ndarray]:
    if not isinstance(windows, list) or not windows:
        raise IntermediateValidationError("windows must be a non-empty list.")
    if isinstance(total_cube_frames, bool) or not isinstance(
        total_cube_frames, (int, np.integer)
    ):
        raise IntermediateValidationError("total_cube_frames must be an integer.")
    if total_cube_frames <= 0:
        raise IntermediateValidationError("total_cube_frames must be positive.")
    if not all(isinstance(window, dict) for window in windows):
        raise IntermediateValidationError("Every window must be a dict.")

    _validate_required_fields(windows)
    _validate_scalar_fields(windows)
    shapes = _validate_array_fields(windows)
    _validate_window_alignment(windows, int(total_cube_frames))
    _validate_cross_field_shapes(windows, shapes)
    return _stack_fields(windows)


def write_intermediates_npz(
    path: str | Path,
    windows: list[dict],
    total_cube_frames: int,
) -> Path:
    """Validate, stack, and write per-window intermediates as a compressed NPZ.

    The caller must add ``start_epoch`` and ``end_epoch`` to every window dict.
    The returned path always ends in ``.npz``. The completed archive is verified
    by loading and accessing every member with ``allow_pickle=False``.
    """
    arrays = _validate_and_stack(windows, total_cube_frames)

    requested_path = Path(path)
    archive_path = (
        requested_path
        if requested_path.suffix.lower() == ".npz"
        else Path(f"{requested_path}.npz")
    )
    if not archive_path.parent.exists():
        raise FileNotFoundError(
            f"Output directory does not exist: {archive_path.parent}"
        )

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{archive_path.name}.",
            suffix=".tmp.npz",
            dir=archive_path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)

        np.savez_compressed(temporary_path, **arrays)
        with np.load(temporary_path, allow_pickle=False) as archive:
            if set(archive.files) != set(arrays):
                raise OSError("Written NPZ fields do not match the serializer schema.")
            for field in arrays:
                loaded = archive[field]
                if loaded.dtype.hasobject:
                    raise OSError(
                        f"Written NPZ field {field!r} unexpectedly has object dtype."
                    )

        temporary_path.replace(archive_path)
        temporary_path = None
        return archive_path
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
