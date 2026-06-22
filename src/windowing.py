"""
Window frame-index generators for the radar vital-signs pipeline.

All frame indices are absolute capture frames (0-based from start of recording).
Converting to trim-relative indices for cube slicing is the caller's responsibility.
"""

from __future__ import annotations


def common_center_windows(
    total_frames: int,
    trim_frames: int,
    window_lengths_frames: list[int],
    center_spacing_frames: int,
    first_center_offset_frames: int,
) -> dict[int, list[tuple[int, int]]]:
    """Return windows sharing common center times across multiple window lengths.

    Only centers where ALL window lengths produce a full window within
    [trim_frames, total_frames] are included. All returned lists have the same
    length (one entry per valid center).

    Parameters
    ----------
    total_frames:
        Total frames in the capture (not the usable length after trim).
    trim_frames:
        Frames to skip at the start of the capture. Non-negative.
    window_lengths_frames:
        Window lengths in frames. Must be non-empty; each element must be a
        positive even integer; all elements must be unique.
    center_spacing_frames:
        Frame spacing between successive center times.
    first_center_offset_frames:
        Absolute frame index of the first candidate center. Must be large enough
        that the first center supports every window length (no silent skip of the
        first center is permitted).

    Returns
    -------
    dict[int, list[tuple[int, int]]]
        Maps each window length to a list of (start_frame, end_frame) tuples.

    Raises
    ------
    ValueError
        For any invalid argument or if no valid centers exist.

    Examples
    --------
    >>> windows = common_center_windows(
    ...     total_frames=3000,
    ...     trim_frames=600,
    ...     window_lengths_frames=[400, 500, 600],
    ...     center_spacing_frames=100,
    ...     first_center_offset_frames=900,
    ... )
    >>> len(windows[400])
    19
    >>> windows[400][0]
    (700, 1100)
    >>> windows[400][-1]
    (2500, 2900)
    >>> windows[500][0]
    (650, 1150)
    >>> windows[600][0]
    (600, 1200)
    >>> windows[600][-1]
    (2400, 3000)
    """
    # --- Validate scalar arguments ---
    for name, val in [
        ("total_frames", total_frames),
        ("center_spacing_frames", center_spacing_frames),
        ("first_center_offset_frames", first_center_offset_frames),
    ]:
        if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
            raise ValueError(
                f"{name} must be a positive integer, got {val!r}"
            )

    if isinstance(trim_frames, bool) or not isinstance(trim_frames, int) or trim_frames < 0:
        raise ValueError(
            f"trim_frames must be a non-negative integer, got {trim_frames!r}"
        )
    if trim_frames >= total_frames:
        raise ValueError(
            f"trim_frames ({trim_frames}) must be less than total_frames ({total_frames})"
        )

    # --- Validate window_lengths_frames ---
    if not window_lengths_frames:
        raise ValueError("window_lengths_frames must be a non-empty list")
    seen: set[int] = set()
    for i, wl in enumerate(window_lengths_frames):
        if isinstance(wl, bool) or not isinstance(wl, int) or wl <= 0:
            raise ValueError(
                f"window_lengths_frames[{i}] must be a positive integer, got {wl!r}"
            )
        if wl % 2 != 0:
            raise ValueError(
                f"window_lengths_frames[{i}] must be even "
                f"(half-center semantics undefined for odd lengths), got {wl}"
            )
        if wl in seen:
            raise ValueError(
                f"window_lengths_frames contains duplicate value {wl}"
            )
        seen.add(wl)

    # --- Validate that the first center supports every window length ---
    max_half = max(window_lengths_frames) // 2
    first_start = first_center_offset_frames - max_half
    if first_start < trim_frames:
        raise ValueError(
            f"first_center_offset_frames ({first_center_offset_frames}) minus half "
            f"of the longest window ({max_half}) = {first_start}, which is less than "
            f"trim_frames ({trim_frames}); the first center does not support every "
            "window length"
        )

    # --- Generate windows ---
    result: dict[int, list[tuple[int, int]]] = {wl: [] for wl in window_lengths_frames}
    center = first_center_offset_frames
    while center <= total_frames:
        valid = all(
            (center - wl // 2) >= trim_frames and (center + wl // 2) <= total_frames
            for wl in window_lengths_frames
        )
        if valid:
            for wl in window_lengths_frames:
                half = wl // 2
                result[wl].append((center - half, center + half))
        center += center_spacing_frames

    if not result[next(iter(result))]:
        raise ValueError(
            "No valid centers found. Check that window lengths, trim_frames, and "
            "total_frames are compatible with the center grid."
        )

    return result


def sliding_windows(
    total_frames: int,
    trim_frames: int,
    window_frames: int,
    hop_frames: int,
) -> list[tuple[int, int]]:
    """Return sliding-window (start_frame, end_frame) tuples. Partial windows dropped.

    Parameters
    ----------
    total_frames:
        Total frames in the capture.
    trim_frames:
        Frames to skip at the start of the capture. Positive integer.
    window_frames:
        Window length in frames.
    hop_frames:
        Hop size in frames.

    Returns
    -------
    list[tuple[int, int]]
        Absolute frame (start, end) pairs for each full window.

    Raises
    ------
    ValueError
        For any invalid argument.

    Examples
    --------
    >>> wins = sliding_windows(3000, 600, 400, 100)
    >>> wins[0]
    (600, 1000)
    >>> wins[-1]
    (2600, 3000)
    >>> len(wins)
    21
    """
    for name, val in [
        ("total_frames", total_frames),
        ("trim_frames", trim_frames),
        ("window_frames", window_frames),
        ("hop_frames", hop_frames),
    ]:
        if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
            raise ValueError(
                f"{name} must be a positive integer, got {val!r}"
            )

    if trim_frames >= total_frames:
        raise ValueError(
            f"trim_frames ({trim_frames}) must be less than total_frames ({total_frames})"
        )
    if trim_frames + window_frames > total_frames:
        raise ValueError(
            f"trim_frames + window_frames ({trim_frames} + {window_frames} = "
            f"{trim_frames + window_frames}) exceeds total_frames ({total_frames}); "
            "no full window fits in the usable range"
        )

    windows: list[tuple[int, int]] = []
    start = trim_frames
    while start + window_frames <= total_frames:
        windows.append((start, start + window_frames))
        start += hop_frames
    return windows


if __name__ == "__main__":
    wins = common_center_windows(
        total_frames=3000,
        trim_frames=600,
        window_lengths_frames=[400, 500, 600],
        center_spacing_frames=100,
        first_center_offset_frames=900,
    )
    for length in [400, 500, 600]:
        starts = [s for s, _ in wins[length]]
        print(f"{length//20}s starts ({len(starts)} windows): {starts}")

    baseline = sliding_windows(3000, 600, 400, 100)
    starts = [s for s, _ in baseline]
    print(f"baseline starts ({len(starts)} windows): {starts}")
