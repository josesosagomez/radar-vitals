"""Physical range coordinates for raw FFT bins (TI subtracts measured range bias).

This changes coordinates only; ADC samples and FFT indices are never shifted.
Old configurations without range_bias_m keep their original zero-bias behavior.
"""
from __future__ import annotations

import math

RANGE_COORDINATE_MODEL = "fft_bin_center_minus_bias_v1"


def _finite_float(value: object, name: str) -> float:
    try:
        if isinstance(value, bool):
            raise ValueError
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def range_bias_m(cfg: dict) -> float:
    """Signed measured bias in meters, defaulting to zero for old configs."""
    return _finite_float(cfg.get("profile", {}).get("range_bias_m", 0.0),
                         "profile.range_bias_m")


def range_resolution_m(cfg: dict) -> float:
    result = _finite_float(cfg["profile"]["range_resolution_m"],
                           "profile.range_resolution_m")
    if result <= 0:
        raise ValueError("profile.range_resolution_m must be positive")
    return result


def bin_range_m(bin_index: int, cfg: dict) -> float:
    """Corrected physical coordinate; keep negative coordinates rather than clamp."""
    return bin_index * range_resolution_m(cfg) - range_bias_m(cfg)


def range_coordinate_fields(bin_index: int | None, cfg: dict) -> dict:
    """Unrounded coordinates and model for metadata and diagnostic records."""
    resolution = range_resolution_m(cfg)
    bias = range_bias_m(cfg)
    raw = None if bin_index is None else bin_index * resolution
    return {
        "range_coordinate_model": RANGE_COORDINATE_MODEL,
        "range_bias_m": bias,
        "selected_raw_range_m": raw,
        "selected_corrected_range_m": None if raw is None else raw - bias,
    }


def validate_range_gate(cfg: dict) -> tuple[float, float, int]:
    """Validate physical gate and FFT length before deriving integer candidates."""
    distances = cfg["protocol"]["subject_distance_m"]
    if not isinstance(distances, (list, tuple)) or len(distances) != 2:
        raise ValueError("protocol.subject_distance_m must contain two distances")
    lo, hi = (_finite_float(x, "protocol.subject_distance_m") for x in distances)
    if lo > hi:
        raise ValueError("protocol.subject_distance_m must be ordered low to high")
    n_adc = cfg["profile"]["num_adc_samples"]
    if type(n_adc) is not int or n_adc <= 0:
        raise ValueError("profile.num_adc_samples must be a positive integer")
    return lo, hi, n_adc
