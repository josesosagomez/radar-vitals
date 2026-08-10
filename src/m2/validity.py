"""Packet-derived frame validity and fixed-window radar-NaN materialization."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np

from .common import ContractError

FRAMES_PER_WINDOW = 600


@dataclass(frozen=True)
class WindowValidity:
    window_index: int
    frame_start: int
    frame_stop: int
    radar_valid: bool


def load_frame_validity(path: str | Path, *, expected_frames: int | None = None) -> np.ndarray:
    try:
        validity = np.load(Path(path), allow_pickle=False)
    except Exception as exc:  # noqa: BLE001 - normalized to a named contract error
        raise ContractError(f"cannot read frame validity map {path}: {exc}") from exc
    if validity.dtype != np.bool_ or validity.ndim != 1:
        raise ContractError(
            "frame validity map must be a one-dimensional Boolean .npy array"
        )
    if expected_frames is not None and validity.size != expected_frames:
        raise ContractError(
            f"frame validity map has {validity.size} frames; expected {expected_frames}"
        )
    return validity


def validity_by_window(
    frame_validity: np.ndarray, *, frames_per_window: int = FRAMES_PER_WINDOW
) -> tuple[WindowValidity, ...]:
    """Return every complete non-overlapping window; an incomplete tail is not a row."""
    if frame_validity.dtype != np.bool_ or frame_validity.ndim != 1:
        raise ContractError("frame_validity must be a one-dimensional Boolean array")
    if type(frames_per_window) is not int or frames_per_window <= 0:
        raise ContractError("frames_per_window must be a positive exact integer")
    n_complete_windows = int(frame_validity.size // frames_per_window)
    windows: list[WindowValidity] = []
    for window_index in range(n_complete_windows):
        frame_start = window_index * frames_per_window
        frame_stop = frame_start + frames_per_window
        windows.append(
            WindowValidity(
                window_index=window_index,
                frame_start=frame_start,
                frame_stop=frame_stop,
                radar_valid=bool(np.all(frame_validity[frame_start:frame_stop])),
            )
        )
    return tuple(windows)


def invalid_window_indices(
    frame_validity: np.ndarray, *, frames_per_window: int = FRAMES_PER_WINDOW
) -> tuple[int, ...]:
    return tuple(
        window.window_index
        for window in validity_by_window(
            frame_validity, frames_per_window=frames_per_window
        )
        if not window.radar_valid
    )


def materialize_radar_nan_windows(
    radar_rows: Iterable[Mapping[str, object]],
    frame_validity: np.ndarray,
    *,
    value_fields: tuple[str, ...] = ("hr_bpm", "br_bpm"),
    frames_per_window: int = FRAMES_PER_WINDOW,
) -> list[dict[str, object]]:
    """Return exactly one row per complete source window, including missing/invalid rows.

    This is an acquisition-quality wrapper only.  It does not call or change an estimator.
    Valid source rows pass through.  A window touching zero-fill, or with no estimator row,
    is retained with null numerical values and an exact radar-invalid reason.  JSON null is
    used on disk; scoring adapters may convert it to floating NaN at their numeric boundary.
    """
    rows_by_index: dict[int, dict[str, object]] = {}
    for source_row in radar_rows:
        row = dict(source_row)
        index = row.get("window_index", row.get("k"))
        if type(index) is not int or index < 0:
            raise ContractError(f"radar row has invalid window index {index!r}")
        if index in rows_by_index:
            raise ContractError(f"duplicate radar row for window {index}")
        rows_by_index[index] = row

    output: list[dict[str, object]] = []
    windows = validity_by_window(frame_validity, frames_per_window=frames_per_window)
    for window in windows:
        source_present = window.window_index in rows_by_index
        row = rows_by_index.pop(window.window_index, {"window_index": window.window_index})
        row.setdefault("window_index", window.window_index)
        row["frame_start"] = window.frame_start
        row["frame_stop"] = window.frame_stop
        if not window.radar_valid:
            reason = "frame_validity_zero_fill"
        elif not source_present:
            reason = "missing_estimator_output"
        elif row.get("radar_valid") is False:
            reason = str(row.get("radar_validity_reason") or "estimator_invalid")
        else:
            reason = None
        if reason is not None:
            for field_name in value_fields:
                row[field_name] = None
            row["radar_valid"] = False
            row["radar_validity_reason"] = reason
        else:
            row["radar_valid"] = True
            row["radar_validity_reason"] = None
        output.append(row)

    if rows_by_index:
        raise ContractError(
            "radar rows include indices outside the complete-window grid: "
            f"{sorted(rows_by_index)}"
        )
    return output
