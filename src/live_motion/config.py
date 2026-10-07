"""Strict configuration boundary for the development live-motion path."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from src.warmup_select import derive_candidate_bins


@dataclass(frozen=True)
class FeatureThreshold:
    """Entry/exit thresholds for one increasing movement feature."""

    enabled: bool
    entry: float | None
    exit: float | None


@dataclass(frozen=True)
class PresenceThresholds:
    """Absolute power bounds in dB ADC-squared units."""

    empty_max_db: float
    occupied_min_db: float


@dataclass(frozen=True)
class MovementThresholds:
    presence: FeatureThreshold
    phase_activity: FeatureThreshold
    range_profile_change: FeatureThreshold


@dataclass(frozen=True)
class LiveMotionSettings:
    enabled: bool
    unavailable_reason: str
    frame_rate_hz: float
    preview_frames: tuple[int, int]
    ordinary_window_frames: int
    ordinary_hop_frames: int
    monitor_window_frames: int
    monitor_hop_frames: int
    stillness_frames: int
    extended_breathing_enabled: bool
    extended_breathing_window_frames: int
    extended_breathing_band_hz: tuple[float, float]
    candidate_bins: tuple[int, ...]
    presence_thresholds: PresenceThresholds | None
    movement_thresholds: MovementThresholds | None
    calibration_record: dict[str, Any] | None
    calibration_record_path: Path | None
    calibration_record_sha256: str | None


_EXPECTED = {
    "preliminary_stages_s": (10.0, 20.0),
    "ordinary_window_s": 30.0,
    "ordinary_hop_s": 3.0,
    "monitor_window_s": 1.0,
    "monitor_hop_s": 0.25,
    "stillness_confirmation_s": 3.0,
    "extended_breathing_window_s": 60.0,
    "extended_breathing_band_hz": (0.05, 0.50),
}


def _exact_bool(section: dict, key: str) -> bool:
    value = section.get(key)
    if type(value) is not bool:
        raise ValueError(f"development_motion.{key} must be an exact boolean")
    return value


def _finite_number(section: dict, key: str) -> float:
    value = section.get(key)
    if type(value) not in (int, float) or not np.isfinite(float(value)):
        raise ValueError(f"development_motion.{key} must be a finite number")
    return float(value)


def _fixed_number(section: dict, key: str, expected: float) -> float:
    value = _finite_number(section, key)
    if value != expected:
        raise ValueError(
            f"development_motion.{key} must be {expected:g}, got {value:g}"
        )
    return value


def _fixed_pair(section: dict, key: str, expected: tuple[float, float]) -> tuple[float, float]:
    value = section.get(key)
    if type(value) is not list or len(value) != 2:
        raise ValueError(f"development_motion.{key} must be a two-value list")
    if any(type(item) not in (int, float) for item in value):
        raise ValueError(f"development_motion.{key} values must be exact numbers")
    pair = tuple(float(v) for v in value)
    if not all(np.isfinite(v) for v in pair) or pair != expected:
        raise ValueError(
            f"development_motion.{key} must be [{expected[0]:g}, {expected[1]:g}]"
        )
    return pair


def _frames(seconds: float, frame_rate_hz: float, name: str) -> int:
    exact = seconds * frame_rate_hz
    rounded = int(round(exact))
    if not np.isclose(exact, rounded, rtol=0.0, atol=1e-12):
        raise ValueError(f"{name} does not map to an integer frame count")
    return rounded


def _feature_threshold(record: dict, name: str) -> FeatureThreshold:
    movement = record.get("thresholds", {}).get("movement", {})
    raw = movement.get(name)
    if type(raw) is not dict:
        raise ValueError(f"calibration thresholds.movement.{name} is missing")
    enabled = raw.get("enabled")
    if type(enabled) is not bool:
        raise ValueError(f"calibration movement {name}.enabled must be boolean")
    if not enabled:
        if raw.get("entry") is not None or raw.get("exit") is not None:
            raise ValueError(f"disabled calibration movement {name} thresholds must be null")
        return FeatureThreshold(enabled=False, entry=None, exit=None)
    if type(raw.get("entry")) not in (int, float) or type(raw.get("exit")) not in (int, float):
        raise ValueError(f"calibration movement {name} thresholds must be exact numbers")
    entry = float(raw["entry"])
    exit_value = float(raw["exit"])
    if (
        not np.isfinite(entry)
        or not np.isfinite(exit_value)
        or not exit_value < entry
    ):
        raise ValueError(
            f"enabled calibration movement {name} needs finite exit < entry"
        )
    return FeatureThreshold(enabled=True, entry=entry, exit=exit_value)


def _thresholds(record: dict) -> tuple[PresenceThresholds, MovementThresholds]:
    presence = record.get("thresholds", {}).get("presence")
    if type(presence) is not dict:
        raise ValueError("calibration thresholds.presence is missing")
    if (
        type(presence.get("empty_max_db")) not in (int, float)
        or type(presence.get("occupied_min_db")) not in (int, float)
    ):
        raise ValueError("calibration presence thresholds must be exact numbers")
    empty = float(presence["empty_max_db"])
    occupied = float(presence["occupied_min_db"])
    if not np.isfinite(empty) or not np.isfinite(occupied) or not empty < occupied:
        raise ValueError("calibration presence needs finite empty_max_db < occupied_min_db")
    movement = MovementThresholds(
        presence=_feature_threshold(record, "presence"),
        phase_activity=_feature_threshold(record, "phase_activity"),
        range_profile_change=_feature_threshold(record, "range_profile_change"),
    )
    if not any(
        threshold.enabled
        for threshold in (
            movement.presence,
            movement.phase_activity,
            movement.range_profile_change,
        )
    ):
        raise ValueError("calibration must enable at least one movement feature")
    return PresenceThresholds(empty, occupied), movement


def _parse_live_motion_settings(
    cfg: dict,
    *,
    calibration_record: dict[str, Any] | None = None,
    calibration_record_path: Path | None = None,
    calibration_record_sha256: str | None = None,
    allow_missing_calibration: bool = False,
) -> LiveMotionSettings | None:
    """Parse exact development settings without performing external I/O."""

    section = cfg.get("development_motion")
    if section is None:
        return None
    if type(section) is not dict:
        raise ValueError("development_motion must be a mapping")

    enabled = _exact_bool(section, "enabled")
    extended_enabled = _exact_bool(section, "extended_breathing_enabled")
    if extended_enabled and not enabled:
        raise ValueError("extended breathing requires development movement recovery")

    fs = float(cfg.get("session", {}).get("frame_rate_hz", np.nan))
    if not np.isfinite(fs) or fs != 20.0:
        raise ValueError("development motion requires session.frame_rate_hz = 20")
    phase = cfg.get("phase", {})
    if phase.get("method") != "delta_before_mean":
        raise ValueError("development motion requires phase.method=delta_before_mean")
    if phase.get("clutter_removal", "none") != "none":
        raise ValueError("development motion requires phase.clutter_removal=none")

    preview_s = _fixed_pair(section, "preliminary_stages_s", _EXPECTED["preliminary_stages_s"])
    ordinary_s = _fixed_number(section, "ordinary_window_s", _EXPECTED["ordinary_window_s"])
    hop_s = _fixed_number(section, "ordinary_hop_s", _EXPECTED["ordinary_hop_s"])
    monitor_s = _fixed_number(section, "monitor_window_s", _EXPECTED["monitor_window_s"])
    monitor_hop_s = _fixed_number(section, "monitor_hop_s", _EXPECTED["monitor_hop_s"])
    still_s = _fixed_number(
        section, "stillness_confirmation_s", _EXPECTED["stillness_confirmation_s"]
    )
    extended_s = _fixed_number(
        section, "extended_breathing_window_s", _EXPECTED["extended_breathing_window_s"]
    )
    extended_band = _fixed_pair(
        section, "extended_breathing_band_hz", _EXPECTED["extended_breathing_band_hz"]
    )
    unavailable_reason = str(section.get("unavailable_reason", ""))
    candidate_bins = tuple(derive_candidate_bins(cfg))
    if not candidate_bins:
        raise ValueError("development motion candidate-bin gate is empty")

    calibration = section.get("calibration")
    if type(calibration) is not dict:
        raise ValueError("development_motion.calibration must be a mapping")

    presence_thresholds = None
    movement_thresholds = None
    if enabled and calibration_record is None and not allow_missing_calibration:
        raise ValueError("physical-calibration-required: no accepted calibration record")
    if enabled and calibration_record is not None:
        presence_thresholds, movement_thresholds = _thresholds(calibration_record)

    return LiveMotionSettings(
        enabled=enabled,
        unavailable_reason=unavailable_reason,
        frame_rate_hz=fs,
        preview_frames=tuple(_frames(v, fs, "preliminary stage") for v in preview_s),
        ordinary_window_frames=_frames(ordinary_s, fs, "ordinary window"),
        ordinary_hop_frames=_frames(hop_s, fs, "ordinary hop"),
        monitor_window_frames=_frames(monitor_s, fs, "monitor window"),
        monitor_hop_frames=_frames(monitor_hop_s, fs, "monitor hop"),
        stillness_frames=_frames(still_s, fs, "stillness confirmation"),
        extended_breathing_enabled=extended_enabled,
        extended_breathing_window_frames=_frames(extended_s, fs, "extended window"),
        extended_breathing_band_hz=extended_band,
        candidate_bins=candidate_bins,
        presence_thresholds=presence_thresholds,
        movement_thresholds=movement_thresholds,
        calibration_record=calibration_record,
        calibration_record_path=calibration_record_path,
        calibration_record_sha256=calibration_record_sha256,
    )


def parse_live_motion_settings(
    cfg: dict,
    *,
    calibration_record: dict[str, Any] | None = None,
    calibration_record_path: Path | None = None,
    calibration_record_sha256: str | None = None,
) -> LiveMotionSettings | None:
    """Public pure parser; enabled operation always requires a validated record."""

    return _parse_live_motion_settings(
        cfg,
        calibration_record=calibration_record,
        calibration_record_path=calibration_record_path,
        calibration_record_sha256=calibration_record_sha256,
        allow_missing_calibration=False,
    )


def validate_live_motion_preflight(
    cfg: dict,
    *,
    mode: str,
    replay_requested: bool,
    prospective: bool,
    cli_locked_bin: int | None,
    manifest_locked_bin: int | None,
    root: Path | None = None,
) -> LiveMotionSettings | None:
    """Validate feature compatibility before countdown, output, or hardware access."""

    section = cfg.get("development_motion")
    if section is None:
        return None
    # Parse malformed disabled blocks too; a disabled flag must not hide drift.
    preliminary = _parse_live_motion_settings(cfg, allow_missing_calibration=True)
    assert preliminary is not None
    if not preliminary.enabled:
        return preliminary

    violations: list[str] = []
    if mode != "live" or replay_requested:
        violations.append("replay is incompatible with development movement recovery")
    if prospective:
        violations.append("prospective capture is incompatible with development movement recovery")
    if cli_locked_bin is not None:
        violations.append("--locked-bin is incompatible with automatic recovery")
    if manifest_locked_bin is not None:
        violations.append("manifest locked_bin is incompatible with automatic recovery")
    if cfg.get("session", {}).get("locked_bin") is not None:
        violations.append("session.locked_bin must be null for automatic recovery")
    if cfg.get("capture", {}).get("record_raw_stream") is not True:
        violations.append(
            "development movement recovery requires capture.record_raw_stream=true"
        )
    if violations:
        raise ValueError("; ".join(violations))

    # Imported lazily so a disabled configuration does not touch a calibration path.
    from .calibration import validate_calibration

    record = validate_calibration(cfg, root=root)
    calibration = section["calibration"]
    record_path = Path(str(calibration["record_path"]))
    if root is not None and not record_path.is_absolute():
        record_path = root / record_path
    return parse_live_motion_settings(
        cfg,
        calibration_record=record,
        calibration_record_path=record_path.resolve(),
        calibration_record_sha256=str(calibration["record_sha256"]),
    )
