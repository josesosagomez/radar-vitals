"""Hash-bound calibration for development-only live motion and breathing guards.

Thresholds are derived only from independent training acquisitions.  Held-out
acquisitions can accept or reject a locked candidate, but never alter it.  This
module deliberately contains no defaults for detector thresholds: absent or
incompatible physical calibration leaves the optional live feature unavailable.
"""
from __future__ import annotations

import copy
import math
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from src.m2.common import (
    canonical_json_bytes,
    read_json_object,
    require_sha256,
    resolve_bound_path,
    sha256_bytes,
    sha256_file,
)


CALIBRATION_SCHEMA = "live_motion_calibration_record_v1"
CANDIDATE_SCHEMA = "live_motion_calibration_candidate_v1"
SUMMARY_SCHEMA = "live_motion_acquisition_summary_v1"

FEATURE_NAMES = ("presence", "phase_activity", "range_profile_change")
PRESENCE_STATES = ("empty", "weak", "strong")
POSITIONS = ("near", "middle", "far")
MOVEMENT_NEGATIVE_STATES = ("normal", "deep", "slow")
BREATHING_STATES = ("periodic", "irregular", "quiet")
REQUIRED_MOVEMENT_SUBTYPES = ("same_bin_torso", "range_change")
CAPTURE_PROVENANCE = (
    "live_dca1000",
    "legacy_checkpoint_live_dca1000",
    "synthetic_fixture",
)
HARDWARE_CAPTURE_PROVENANCE = {
    "live_dca1000",
    "legacy_checkpoint_live_dca1000",
}
_GIT_OBJECT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_LIVE_SOURCE_ID_RE = re.compile(r"live:[^:]+:adc_stream\.bin\Z")

TRAINING_MIN_ACQUISITIONS = 3
HELDOUT_MIN_ACQUISITIONS = 2
FALLBACK_CHECKPOINT_SHA = "7f3dd2bfd405cdd73d5ae395347baeb991213acf"

# Each file can change the samples, features, phase, scores, or runtime decision
# to which a physical calibration applies.  Missing files are an error; silently
# weakening the fingerprint would make an old calibration appear compatible.
COMMON_SOURCE_FILES = (
    "scripts/calibrate_live_motion.py",
    "scripts/live_demo.py",
    "src/radar_io.py",
    "src/range_coordinates.py",
    "src/respiration.py",
    "src/vitals.py",
    "src/window_pipeline.py",
    "src/warmup_select.py",
    "src/live_motion/__init__.py",
    "src/live_motion/cache.py",
    "src/live_motion/calibration.py",
    "src/live_motion/config.py",
    "src/live_motion/controller.py",
    "src/live_motion/evidence.py",
    "src/live_motion/features.py",
    "src/live_motion/runtime.py",
    "src/live_motion/scheduler.py",
)
EXTENDED_BREATHING_SOURCE_FILES = ("src/live_motion/breathing.py",)


class CalibrationError(ValueError):
    """A calibration input, separation, or provenance contract is invalid."""


def _repo_root(root: str | Path | None = None) -> Path:
    return Path(root).resolve() if root is not None else Path(__file__).resolve().parents[2]


def _canonical_hash(value: object) -> str:
    return sha256_bytes(canonical_json_bytes(value, trailing_newline=False))


def _finite_number(value: object, field: str) -> float:
    if not isinstance(value, (int, float, np.integer, np.floating)) or isinstance(
        value, (bool, np.bool_)
    ):
        raise CalibrationError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise CalibrationError(f"{field} must be a finite number")
    return result


def _finite_array(
    value: object,
    field: str,
    *,
    allow_empty: bool = False,
    shape: tuple[int, ...] | None = None,
) -> np.ndarray:
    try:
        result = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError, OverflowError) as exc:
        raise CalibrationError(f"{field} must be a numeric array") from exc
    if shape is not None and result.shape != shape:
        raise CalibrationError(f"{field} must have shape {shape}, got {result.shape}")
    if not allow_empty and result.size == 0:
        raise CalibrationError(f"{field} must not be empty")
    if result.size and not np.all(np.isfinite(result)):
        raise CalibrationError(f"{field} contains NaN or infinity")
    return result


def _nonempty_string(value: object, field: str) -> str:
    if type(value) is not str or not value.strip():
        raise CalibrationError(f"{field} must be a non-empty string")
    return value


def _capabilities(cfg: Mapping[str, object]) -> tuple[str, ...]:
    try:
        extended = cfg["development_motion"]["extended_breathing_enabled"]
    except (KeyError, TypeError) as exc:
        raise CalibrationError("missing development_motion.extended_breathing_enabled") from exc
    if type(extended) is not bool:
        raise CalibrationError("development_motion.extended_breathing_enabled must be Boolean")
    return ("movement", "extended_breathing") if extended else ("movement",)


def _source_hashes(root: Path, capabilities: Sequence[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    files = list(COMMON_SOURCE_FILES)
    if "extended_breathing" in capabilities:
        files.extend(EXTENDED_BREATHING_SOURCE_FILES)
    for relative in files:
        path = root / relative
        if not path.is_file():
            raise CalibrationError(f"load-bearing calibration source is missing: {relative}")
        hashes[relative] = sha256_file(path)
    return hashes


def _semantic_settings(cfg: Mapping[str, object]) -> dict[str, object]:
    """Select settings that change calibrated samples or decisions.

    Startup delay, UI preferences, output paths, feature enable flags, and the
    calibration record's own path/hash are intentionally excluded.
    """
    try:
        motion = cfg["development_motion"]
        session = cfg["session"]
        profile = cfg["profile"]
        hardware = cfg["hw_profile"]
        frame = cfg["hw_frame"]
        protocol = cfg["protocol"]
        phase = cfg["phase"]
        selection = cfg["bin_selection"]
        respiration = cfg["respiration"]
        heart = cfg["heart"]
    except (KeyError, TypeError) as exc:
        raise CalibrationError(f"missing semantic calibration setting: {exc}") from exc
    if not all(isinstance(item, Mapping) for item in (
        motion, session, profile, hardware, frame, protocol, phase, selection,
        respiration, heart,
    )):
        raise CalibrationError("semantic calibration sections must be mappings")

    def take(mapping: Mapping[str, object], names: Sequence[str], prefix: str) -> dict[str, object]:
        missing = [name for name in names if name not in mapping]
        if missing:
            raise CalibrationError(f"{prefix} is missing semantic fields: {', '.join(missing)}")
        return {name: copy.deepcopy(mapping[name]) for name in names}

    motion_names = [
                "preliminary_stages_s",
                "ordinary_window_s",
                "ordinary_hop_s",
                "monitor_window_s",
                "monitor_hop_s",
                "stillness_confirmation_s",
    ]
    if bool(motion.get("extended_breathing_enabled", False)):
        motion_names.extend((
                "extended_breathing_window_s",
                "extended_breathing_band_hz",
        ))
    return {
        "capabilities": list(_capabilities(cfg)),
        "seed": cfg.get("seed"),
        "development_motion": take(motion, motion_names, "development_motion"),
        "session": take(session, ("frame_rate_hz",), "session"),
        "profile": take(
            profile,
            (
                "num_adc_samples",
                "num_rx",
                "num_chirps_per_frame",
                "range_resolution_m",
                "range_bias_m",
                "iq_swap",
            ),
            "profile",
        ),
        "hw_profile": copy.deepcopy(dict(hardware)),
        "hw_frame": copy.deepcopy(dict(frame)),
        "protocol": take(protocol, ("subject_distance_m",), "protocol"),
        "phase": copy.deepcopy(dict(phase)),
        "bin_selection": copy.deepcopy(dict(selection)),
        "respiration": copy.deepcopy(dict(respiration)),
        "heart": copy.deepcopy(dict(heart)),
        "feature_definition": {
            "range_window": "float32_hann",
            "range_fft": "scipy_fft",
            "monitor_presence": "maximum_mean_gate_bin_power_db",
            "monitor_phase_activity": "weighted_rms_circular_increment_before_clipping",
            "monitor_profile_change": "half_l1_normalized_half_profiles",
            "energy_eligibility_db": selection["energy_eligibility_min_settled_db"],
            "breathing_activity_blocks": 6,
            "breathing_persistence_halves": 2,
        },
    }


def semantic_identity(cfg: Mapping[str, object]) -> dict[str, str]:
    """Return source and semantic-setting identities for calibration binding."""
    root = _repo_root()
    source_hashes = _source_hashes(root, _capabilities(cfg))
    settings = _semantic_settings(cfg)
    return {
        "source_sha256": _canonical_hash(source_hashes),
        "settings_sha256": _canonical_hash(settings),
    }


def _validate_measurement(value: object, field: str, *, require_phase: bool) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise CalibrationError(f"{field} must be a mapping")
    measurement = dict(value)
    for name in ("fft_score", "ha_score", "drift_rms"):
        _finite_number(measurement.get(name), f"{field}.{name}")
    _finite_array(
        measurement.get("respiratory_block_rms"),
        f"{field}.respiratory_block_rms",
        shape=(6,),
    )
    _finite_array(
        measurement.get("subband_block_rms"),
        f"{field}.subband_block_rms",
        shape=(6,),
    )
    persistence = measurement.get("persistence")
    if persistence is not None:
        try:
            persistence_value = float(persistence)
        except (TypeError, ValueError, OverflowError) as exc:
            raise CalibrationError(f"{field}.persistence must be numeric") from exc
        if math.isfinite(persistence_value) and not 0.0 <= persistence_value <= 1.0:
            raise CalibrationError(f"{field}.persistence must lie in [0, 1]")
    if require_phase:
        phase = _finite_array(measurement.get("phase"), f"{field}.phase")
        if phase.ndim != 1:
            raise CalibrationError(f"{field}.phase must be one-dimensional")
    return measurement


def _validate_summary(value: object, expected_split: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise CalibrationError("each acquisition summary must be a mapping")
    summary = dict(value)
    if summary.get("schema") != SUMMARY_SCHEMA:
        raise CalibrationError(f"summary schema must be {SUMMARY_SCHEMA!r}")
    if summary.get("split") != expected_split:
        raise CalibrationError(f"summary split must be exactly {expected_split!r}")
    _nonempty_string(summary.get("acquisition_id"), "acquisition_id")
    try:
        require_sha256(summary.get("raw_sha256"), "raw_sha256")
    except ValueError as exc:
        raise CalibrationError(str(exc)) from exc
    provenance = summary.get("capture_provenance")
    if provenance not in CAPTURE_PROVENANCE:
        raise CalibrationError(f"capture_provenance must be one of {CAPTURE_PROVENANCE}")
    source_id = summary.get("capture_source_id")
    if provenance == "live_dca1000":
        if type(source_id) is not str or _LIVE_SOURCE_ID_RE.fullmatch(source_id) is None:
            raise CalibrationError(
                "live_dca1000 capture_source_id must match live:<run>:adc_stream.bin"
            )
        lowered_source_id = source_id.lower()
        if any(token in lowered_source_id for token in ("fake", "synthetic", "replay")):
            raise CalibrationError("live_dca1000 capture_source_id has non-hardware provenance")
    elif provenance == "legacy_checkpoint_live_dca1000":
        if source_id is not None:
            raise CalibrationError("legacy capture_source_id must be null")
    elif type(source_id) is not str or not source_id.startswith("synthetic:"):
        raise CalibrationError("synthetic fixture capture_source_id must start with 'synthetic:'")
    capture_commit = summary.get("capture_git_commit")
    if type(capture_commit) is not str or _GIT_OBJECT_RE.fullmatch(capture_commit) is None:
        raise CalibrationError("capture_git_commit must be a full Git object ID")
    if (
        provenance == "legacy_checkpoint_live_dca1000"
        and capture_commit != FALLBACK_CHECKPOINT_SHA
    ):
        raise CalibrationError("legacy capture must come from the exact fallback checkpoint")
    if type(summary.get("capture_git_dirty")) is not bool:
        raise CalibrationError("capture_git_dirty must be Boolean")
    if provenance in HARDWARE_CAPTURE_PROVENANCE and summary["capture_git_dirty"]:
        raise CalibrationError("physical calibration captures require a clean Git checkout")
    for name in ("run_metadata_sha256", "frame_validity_sha256"):
        try:
            require_sha256(summary.get(name), name)
        except ValueError as exc:
            raise CalibrationError(str(exc)) from exc

    presence_state = summary.get("presence_state")
    if presence_state not in PRESENCE_STATES:
        raise CalibrationError(f"presence_state must be one of {PRESENCE_STATES}")
    position = summary.get("position")
    if position not in (*POSITIONS, "not_applicable"):
        raise CalibrationError(f"position must be one of {POSITIONS} or 'not_applicable'")
    placement = summary.get("placement_m")
    if position == "not_applicable":
        if placement is not None:
            raise CalibrationError("placement_m must be null when position is not_applicable")
    else:
        _finite_number(placement, "placement_m")
    movement_state = summary.get("movement_negative_state")
    if movement_state not in (*MOVEMENT_NEGATIVE_STATES, None):
        raise CalibrationError(
            f"movement_negative_state must be one of {MOVEMENT_NEGATIVE_STATES} or null"
        )
    breathing_state = summary.get("breathing_state")
    if breathing_state not in (*BREATHING_STATES, None):
        raise CalibrationError(f"breathing_state must be one of {BREATHING_STATES} or null")
    if breathing_state is not None and presence_state != "strong":
        raise CalibrationError("breathing calibration requires a strong confirmed target label")
    target_kind = summary.get("target_kind")
    if target_kind not in ("participant", "stationary_reflector", "empty_scene", "weak_reflector"):
        raise CalibrationError("target_kind is invalid")
    if breathing_state == "quiet" and target_kind != "stationary_reflector":
        raise CalibrationError("quiet calibration requires a stationary_reflector target")
    if breathing_state in ("periodic", "irregular") and target_kind != "participant":
        raise CalibrationError("periodic/irregular breathing calibration requires a participant")
    if type(summary.get("seed")) is not int:
        raise CalibrationError("summary seed must be an exact integer")
    breathing_bounds = summary.get("breathing_frame_bounds")
    breathing_monitors = summary.get("breathing_monitor_blocks")
    if type(breathing_monitors) is not list:
        raise CalibrationError("breathing_monitor_blocks must be a list")
    if breathing_state is None:
        if breathing_bounds is not None:
            raise CalibrationError("breathing_frame_bounds requires a breathing label")
        if breathing_monitors:
            raise CalibrationError("breathing_monitor_blocks requires a breathing label")
    elif (
        type(breathing_bounds) is not list
        or len(breathing_bounds) != 2
        or any(type(item) is not int for item in breathing_bounds)
        or breathing_bounds[0] < 0
        or breathing_bounds[1] - breathing_bounds[0] != 1200
    ):
        raise CalibrationError("breathing_frame_bounds must prove one complete 1200-frame epoch")
    else:
        expected_starts = list(range(breathing_bounds[0], breathing_bounds[1] - 19, 5))
        if len(breathing_monitors) != len(expected_starts):
            raise CalibrationError(
                "breathing_monitor_blocks must completely cover the epoch at the 5-frame hop"
            )
        for index, (monitor, expected_start) in enumerate(zip(breathing_monitors, expected_starts)):
            field = f"breathing_monitor_blocks[{index}]"
            if not isinstance(monitor, Mapping):
                raise CalibrationError(f"{field} must be a mapping")
            if monitor.get("frame_start") != expected_start or monitor.get("frame_stop") != expected_start + 20:
                raise CalibrationError(f"{field} has a gap or wrong 20-frame bounds")
            if monitor.get("valid") is not True:
                raise CalibrationError(f"{field} must be valid")
            for feature in FEATURE_NAMES:
                _finite_number(monitor.get(feature), f"{field}.{feature}")

    _finite_array(summary.get("presence_power_db"), "presence_power_db")
    still = summary.get("still_features")
    if not isinstance(still, Mapping) or set(still) != set(FEATURE_NAMES):
        raise CalibrationError(f"still_features must contain exactly {FEATURE_NAMES}")
    for feature in FEATURE_NAMES:
        array = _finite_array(
            still[feature],
            f"still_features.{feature}",
            allow_empty=movement_state is None,
        )
        if movement_state is not None and array.size == 0:
            raise CalibrationError(f"still_features.{feature} is required for still negatives")

    episodes = summary.get("movement_episodes")
    if type(episodes) is not list:
        raise CalibrationError("movement_episodes must be a list")
    for index, episode_value in enumerate(episodes):
        field = f"movement_episodes[{index}]"
        if not isinstance(episode_value, Mapping):
            raise CalibrationError(f"{field} must be a mapping")
        episode = dict(episode_value)
        _nonempty_string(episode.get("subtype"), f"{field}.subtype")
        assigned = episode.get("assigned_features")
        if type(assigned) is not list:
            raise CalibrationError(f"{field}.assigned_features must be a list")
        if expected_split == "training" and not assigned:
            raise CalibrationError(
                f"{field}.assigned_features must be labelled before training"
            )
        if expected_split == "heldout" and assigned:
            raise CalibrationError(
                f"{field}.assigned_features must be empty for held-out evaluation"
            )
        if len(set(assigned)) != len(assigned) or any(name not in FEATURE_NAMES for name in assigned):
            raise CalibrationError(f"{field}.assigned_features contains unknown or duplicate names")
        maxima = episode.get("feature_maxima")
        if not isinstance(maxima, Mapping) or set(maxima) != set(FEATURE_NAMES):
            raise CalibrationError(f"{field}.feature_maxima must contain exactly {FEATURE_NAMES}")
        for feature in FEATURE_NAMES:
            _finite_number(maxima[feature], f"{field}.feature_maxima.{feature}")
        bounds = episode.get("frame_bounds")
        if (
            type(bounds) is not list
            or len(bounds) != 2
            or any(type(item) is not int for item in bounds)
            or bounds[0] < 0
            or bounds[1] <= bounds[0]
        ):
            raise CalibrationError(f"{field}.frame_bounds must be increasing half-open integers")
        if (
            breathing_bounds is not None
            and bounds[0] < breathing_bounds[1]
            and bounds[1] > breathing_bounds[0]
        ):
            raise CalibrationError(f"{field} overlaps the breathing calibration epoch")

    measurements = summary.get("breathing_measurements")
    if type(measurements) is not list:
        raise CalibrationError("breathing_measurements must be a list")
    if breathing_state is not None and not measurements:
        raise CalibrationError("breathing-labelled acquisitions need a complete measurement")
    for index, measurement in enumerate(measurements):
        _validate_measurement(
            measurement,
            f"breathing_measurements[{index}]",
            require_phase=breathing_state is not None,
        )
        if breathing_state is not None:
            if (
                measurement.get("frame_start") != breathing_bounds[0]
                or measurement.get("frame_stop") != breathing_bounds[1]
                or measurement.get("frame_valid_count") != 1200
            ):
                raise CalibrationError(
                    "breathing measurement must prove one complete 1200-valid-frame epoch"
                )
            if np.asarray(measurement["phase"]).shape != (1200,):
                raise CalibrationError("breathing phase must contain exactly 1200 frames")
    return summary


def _normalized_summaries(values: Sequence[Mapping[str, object]], split: str) -> list[dict[str, object]]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence) or not values:
        raise CalibrationError(f"{split} summaries must be a non-empty sequence")
    summaries = [_validate_summary(value, split) for value in values]
    ids = [str(item["acquisition_id"]) for item in summaries]
    raw_hashes = [str(item["raw_sha256"]) for item in summaries]
    if len(ids) != len(set(ids)):
        raise CalibrationError(f"{split} acquisition_id values must be unique")
    if len(raw_hashes) != len(set(raw_hashes)):
        raise CalibrationError(f"{split} raw hashes must identify independent acquisitions")
    return summaries


def _coverage(
    summaries: Sequence[Mapping[str, object]],
    minimum: int,
    *,
    include_breathing: bool,
) -> dict[str, int]:
    counts: dict[str, int] = {}

    def count(field: str, value: str) -> None:
        key = f"{field}:{value}"
        counts[key] = sum(item.get(field) == value for item in summaries)
        if counts[key] < minimum:
            raise CalibrationError(f"coverage {key} needs >= {minimum} independent acquisitions")

    for state in PRESENCE_STATES:
        count("presence_state", state)
    for position in POSITIONS:
        count("position", position)
    placements_by_position = {
        position: [
            float(item["placement_m"])
            for item in summaries
            if item["position"] == position
        ]
        for position in POSITIONS
    }
    placement_bounds = {
        position: (min(values), max(values))
        for position, values in placements_by_position.items()
    }
    if not (
        placement_bounds["near"][1] < placement_bounds["middle"][0]
        and placement_bounds["middle"][1] < placement_bounds["far"][0]
    ):
        raise CalibrationError(
            "near/middle/far placement labels must have strictly ordered, non-overlapping "
            "numeric distances"
        )
    for state in MOVEMENT_NEGATIVE_STATES:
        count("movement_negative_state", state)
    if include_breathing:
        for state in BREATHING_STATES:
            count("breathing_state", state)

    subtypes = set(REQUIRED_MOVEMENT_SUBTYPES)
    subtypes.update(
        str(episode["subtype"])
        for summary in summaries
        for episode in summary["movement_episodes"]
    )
    for subtype in sorted(subtypes):
        count_value = sum(
            any(episode["subtype"] == subtype for episode in summary["movement_episodes"])
            for summary in summaries
        )
        counts[f"movement_subtype:{subtype}"] = count_value
        if count_value < minimum:
            raise CalibrationError(
                f"coverage movement_subtype:{subtype} needs >= {minimum} independent acquisitions"
            )
    return counts


def _measurement_groups(
    summaries: Sequence[Mapping[str, object]],
) -> dict[str, list[tuple[str, Mapping[str, object]]]]:
    groups = {state: [] for state in BREATHING_STATES}
    for summary in summaries:
        state = summary["breathing_state"]
        if state is None:
            continue
        for measurement in summary["breathing_measurements"]:
            groups[str(state)].append((str(summary["acquisition_id"]), measurement))
    return groups


def _verify_measurements_against_phase(
    summaries: Sequence[Mapping[str, object]],
    cfg: Mapping[str, object],
) -> None:
    """Recompute every threshold-driving score from its saved phase evidence."""
    from .breathing import measure_breathing

    fs = float(cfg["session"]["frame_rate_hz"])
    scalar_fields = ("fft_score", "ha_score", "drift_rms", "persistence")
    array_fields = ("respiratory_block_rms", "subband_block_rms")
    bool_fields = ("valid", "peaks_agree", "candidate_local_max")
    for summary in summaries:
        if summary["breathing_state"] is None:
            continue
        for index, measurement in enumerate(summary["breathing_measurements"]):
            recomputed = measure_breathing(np.asarray(measurement["phase"], dtype=np.float64), fs)
            prefix = f"{summary['acquisition_id']}:breathing_measurements[{index}]"
            for field in scalar_fields:
                recorded = _finite_optional(measurement.get(field))
                actual = _finite_optional(recomputed.get(field))
                if not (
                    recorded is None and actual is None
                    or recorded is not None
                    and actual is not None
                    and math.isclose(recorded, actual, rel_tol=1e-12, abs_tol=1e-12)
                ):
                    raise CalibrationError(f"{prefix}.{field} differs from phase recomputation")
            for field in array_fields:
                if not np.allclose(
                    np.asarray(measurement[field], dtype=np.float64),
                    np.asarray(recomputed[field], dtype=np.float64),
                    rtol=1e-12,
                    atol=1e-12,
                    equal_nan=True,
                ):
                    raise CalibrationError(f"{prefix}.{field} differs from phase recomputation")
            for field in bool_fields:
                if bool(measurement.get(field)) != bool(recomputed.get(field)):
                    raise CalibrationError(f"{prefix}.{field} differs from phase recomputation")


def _strict_midpoint(negative_max: float, positive_min: float, name: str) -> float:
    if not negative_max < positive_min:
        raise CalibrationError(
            f"{name} lacks strict training separation: max negative {negative_max} "
            f">= min positive {positive_min}"
        )
    return 0.5 * (negative_max + positive_min)


def _finite_optional(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _breathing_quality_failures(
    summary: Mapping[str, object],
    presence_thresholds: Mapping[str, object],
    movement_thresholds: Mapping[str, Mapping[str, object]],
) -> list[str]:
    failures: list[str] = []
    for index, monitor in enumerate(summary["breathing_monitor_blocks"]):
        prefix = (
            f"{summary['acquisition_id']}:breathing_monitor_blocks[{index}]"
        )
        if float(monitor["presence"]) < float(presence_thresholds["occupied_min_db"]):
            failures.append(prefix + ": target not confirmed")
        for feature, threshold in movement_thresholds.items():
            if threshold["enabled"] and float(monitor[feature]) > float(threshold["exit"]):
                failures.append(prefix + f": {feature} is not quiet")
    return failures


def _derive_thresholds(
    training_summaries: Sequence[Mapping[str, object]],
    *,
    include_breathing: bool,
) -> dict[str, object]:
    """Derive all thresholds from training summaries only.

    Movement positives are the maximum score in each pre-labelled episode.  The
    assignment of episodes to features is operator supplied before fitting.
    """
    summaries = _normalized_summaries(training_summaries, "training")
    _coverage(
        summaries,
        TRAINING_MIN_ACQUISITIONS,
        include_breathing=include_breathing,
    )

    empty_values = np.concatenate([
        _finite_array(item["presence_power_db"], "presence_power_db")
        for item in summaries if item["presence_state"] == "empty"
    ])
    strong_values = np.concatenate([
        _finite_array(item["presence_power_db"], "presence_power_db")
        for item in summaries if item["presence_state"] == "strong"
    ])
    empty_max = float(np.max(empty_values))
    occupied_min = float(np.min(strong_values))
    if not empty_max < occupied_min:
        raise CalibrationError("presence power lacks strict empty/strong separation")

    movement_thresholds: dict[str, dict[str, object]] = {}
    for feature in FEATURE_NAMES:
        still_values = np.concatenate([
            _finite_array(item["still_features"][feature], f"still_features.{feature}")
            for item in summaries if item["movement_negative_state"] is not None
        ])
        still_max = float(np.max(still_values))
        assigned_positive = [
            float(episode["feature_maxima"][feature])
            for item in summaries
            for episode in item["movement_episodes"]
            if feature in episode["assigned_features"]
        ]
        if assigned_positive and still_max < min(assigned_positive):
            positive_min = float(min(assigned_positive))
            movement_thresholds[feature] = {
                "enabled": True,
                "entry": 0.5 * (still_max + positive_min),
                "exit": still_max,
            }
        else:
            movement_thresholds[feature] = {"enabled": False, "entry": None, "exit": None}

    for summary in summaries:
        for episode in summary["movement_episodes"]:
            triggered = any(
                threshold["enabled"]
                and float(episode["feature_maxima"][feature]) >= float(threshold["entry"])
                for feature, threshold in movement_thresholds.items()
            )
            if not triggered:
                raise CalibrationError(
                    "movement ensemble misses training episode "
                    f"{summary['acquisition_id']}:{episode['subtype']}:{episode['frame_bounds']}"
                )

    thresholds: dict[str, object] = {
        "presence": {
            "empty_max_db": empty_max,
            "occupied_min_db": occupied_min,
        },
        "movement": movement_thresholds,
    }
    if not include_breathing:
        return thresholds

    for summary in summaries:
        if summary["breathing_state"] is None:
            continue
        quality_failures = _breathing_quality_failures(
            summary,
            thresholds["presence"],
            movement_thresholds,
        )
        if quality_failures:
            raise CalibrationError(
                "breathing training window failed physical-quality guard: "
                + "; ".join(quality_failures)
            )

    groups = _measurement_groups(summaries)
    periodic = groups["periodic"]
    irregular = groups["irregular"]
    quiet = groups["quiet"]
    negatives = quiet + irregular

    fft_negative_max = max(
        [0.0] + [float(measurement["fft_score"]) for _, measurement in negatives]
    )
    fft_positive_min = min(float(measurement["fft_score"]) for _, measurement in periodic)
    ha_negative_max = max(
        [0.0] + [float(measurement["ha_score"]) for _, measurement in negatives]
    )
    ha_positive_min = min(float(measurement["ha_score"]) for _, measurement in periodic)

    quiet_resp_max = max(
        float(value)
        for _, measurement in quiet
        for value in _finite_array(measurement["respiratory_block_rms"], "quiet respiratory RMS")
    )
    periodic_resp_min = min(
        float(value)
        for _, measurement in periodic
        for value in _finite_array(measurement["respiratory_block_rms"], "periodic respiratory RMS")
    )
    if not quiet_resp_max < periodic_resp_min:
        raise CalibrationError("quiet and periodic respiratory amplitudes lack strict separation")

    periodic_persistence = [
        (acquisition, persistence)
        for acquisition, measurement in periodic
        if (persistence := _finite_optional(measurement.get("persistence"))) is not None
    ]
    irregular_persistence = [
        (acquisition, persistence)
        for acquisition, measurement in irregular
        if (persistence := _finite_optional(measurement.get("persistence"))) is not None
    ]
    for name, values in (
        ("periodic persistence", periodic_persistence),
        ("irregular persistence", irregular_persistence),
    ):
        if len({acquisition for acquisition, _ in values}) < TRAINING_MIN_ACQUISITIONS:
            raise CalibrationError(
                f"{name} needs >= {TRAINING_MIN_ACQUISITIONS} finite independent acquisitions"
            )
    persistence_min = _strict_midpoint(
        max(value for _, value in irregular_persistence),
        min(value for _, value in periodic_persistence),
        "breathing persistence",
    )

    thresholds["breathing"] = {
            "fft_score_min": _strict_midpoint(
                fft_negative_max, fft_positive_min, "FFT score"
            ),
            "ha_score_min": _strict_midpoint(
                ha_negative_max, ha_positive_min, "harmonic accumulation score"
            ),
            "quiet_resp_rms_max": quiet_resp_max,
            "periodic_resp_rms_min": periodic_resp_min,
            "subband_rms_max": max(
                float(value)
                for _, measurement in quiet
                for value in _finite_array(measurement["subband_block_rms"], "quiet sub-band RMS")
            ),
            "drift_rms_max": max(float(measurement["drift_rms"]) for _, measurement in quiet),
            "persistence_min": persistence_min,
        }
    return thresholds


def derive_thresholds(training_summaries: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Derive the full movement and extended-breathing threshold set."""
    return _derive_thresholds(training_summaries, include_breathing=True)


def _summary_threshold_payload(
    summaries: Sequence[Mapping[str, object]],
    *,
    include_phase: bool = False,
) -> list[dict[str, object]]:
    payload: list[dict[str, object]] = []
    for summary in sorted(summaries, key=lambda item: str(item["acquisition_id"])):
        payload.append({
            "schema": SUMMARY_SCHEMA,
            "acquisition_id": summary["acquisition_id"],
            "raw_sha256": summary["raw_sha256"],
            "capture_provenance": summary["capture_provenance"],
            "capture_source_id": summary["capture_source_id"],
            "capture_git_commit": summary["capture_git_commit"],
            "capture_git_dirty": summary["capture_git_dirty"],
            "run_metadata_sha256": summary["run_metadata_sha256"],
            "frame_validity_sha256": summary["frame_validity_sha256"],
            "split": summary["split"],
            "presence_state": summary["presence_state"],
            "position": summary["position"],
            "placement_m": summary["placement_m"],
            "movement_negative_state": summary["movement_negative_state"],
            "breathing_state": summary["breathing_state"],
            "target_kind": summary["target_kind"],
            "seed": summary["seed"],
            "breathing_frame_bounds": copy.deepcopy(summary["breathing_frame_bounds"]),
            "breathing_monitor_blocks": copy.deepcopy(summary["breathing_monitor_blocks"]),
            "presence_power_db": np.asarray(summary["presence_power_db"], dtype=float).tolist(),
            "still_features": {
                name: np.asarray(summary["still_features"][name], dtype=float).tolist()
                for name in FEATURE_NAMES
            },
            "movement_episodes": copy.deepcopy(summary["movement_episodes"]),
            "breathing_measurements": [
                ({
                    "fft_score": float(measurement["fft_score"]),
                    "ha_score": float(measurement["ha_score"]),
                    "respiratory_block_rms": np.asarray(
                        measurement["respiratory_block_rms"], dtype=float
                    ).tolist(),
                    "subband_block_rms": np.asarray(
                        measurement["subband_block_rms"], dtype=float
                    ).tolist(),
                    "drift_rms": float(measurement["drift_rms"]),
                    "persistence": (
                        _finite_optional(measurement.get("persistence"))
                    ),
                    "valid": bool(measurement.get("valid", False)),
                    "peaks_agree": bool(measurement.get("peaks_agree", False)),
                    "candidate_local_max": bool(
                        measurement.get("candidate_local_max", False)
                    ),
                    "frame_start": measurement.get("frame_start"),
                    "frame_stop": measurement.get("frame_stop"),
                    "frame_valid_count": measurement.get("frame_valid_count"),
                    "target_kind": measurement.get("target_kind"),
                    **(
                        {"phase": np.asarray(measurement["phase"], dtype=float).tolist()}
                        if include_phase else {}
                    ),
                })
                for measurement in summary["breathing_measurements"]
            ],
        })
    return payload


def build_candidate(
    training_summaries: Sequence[Mapping[str, object]],
    cfg: Mapping[str, object],
) -> dict[str, object]:
    """Build a deterministic, training-only threshold candidate."""
    summaries = _normalized_summaries(training_summaries, "training")
    if any(item.get("seed") != cfg.get("seed") for item in summaries):
        raise CalibrationError("training summary seed must exactly match config seed")
    capabilities = _capabilities(cfg)
    include_breathing = "extended_breathing" in capabilities
    if include_breathing:
        _verify_measurements_against_phase(summaries, cfg)
    thresholds = _derive_thresholds(summaries, include_breathing=include_breathing)
    root = _repo_root()
    source_hashes = _source_hashes(root, capabilities)
    settings = _semantic_settings(cfg)
    identity = {
        "source_sha256": _canonical_hash(source_hashes),
        "settings_sha256": _canonical_hash(settings),
    }
    candidate = {
        "schema": CANDIDATE_SCHEMA,
        "status": "candidate_locked",
        "capabilities": list(capabilities),
        **identity,
        "source_hashes": source_hashes,
        "semantic_settings": settings,
        "thresholds": thresholds,
        "thresholds_sha256": _canonical_hash(thresholds),
        "training_summary_sha256": _canonical_hash(
            _summary_threshold_payload(summaries, include_phase=include_breathing)
        ),
        "training_summaries": _summary_threshold_payload(
            summaries,
            include_phase=include_breathing,
        ),
        "training_acquisition_ids": sorted(str(item["acquisition_id"]) for item in summaries),
        "training_raw_sha256": sorted(str(item["raw_sha256"]) for item in summaries),
        "training_coverage": _coverage(
            summaries,
            TRAINING_MIN_ACQUISITIONS,
            include_breathing=include_breathing,
        ),
    }
    candidate["candidate_payload_sha256"] = _canonical_hash(candidate)
    return candidate


def _validate_candidate(candidate_value: Mapping[str, object], cfg: Mapping[str, object]) -> dict[str, object]:
    candidate = copy.deepcopy(dict(candidate_value))
    if candidate.get("schema") != CANDIDATE_SCHEMA or candidate.get("status") != "candidate_locked":
        raise CalibrationError("candidate schema/status is invalid")
    recorded_payload_hash = candidate.pop("candidate_payload_sha256", None)
    try:
        require_sha256(recorded_payload_hash, "candidate_payload_sha256")
    except ValueError as exc:
        raise CalibrationError(str(exc)) from exc
    if _canonical_hash(candidate) != recorded_payload_hash:
        raise CalibrationError("candidate payload hash mismatch")
    candidate["candidate_payload_sha256"] = recorded_payload_hash
    thresholds = candidate.get("thresholds")
    if not isinstance(thresholds, Mapping):
        raise CalibrationError("candidate thresholds are missing")
    if _canonical_hash(thresholds) != candidate.get("thresholds_sha256"):
        raise CalibrationError("candidate threshold hash mismatch")

    training_payload = candidate.get("training_summaries")
    if not isinstance(training_payload, list):
        raise CalibrationError("candidate training derivation evidence is missing")
    normalized_training = _normalized_summaries(training_payload, "training")
    include_breathing = "extended_breathing" in candidate.get("capabilities", [])
    if include_breathing:
        _verify_measurements_against_phase(normalized_training, cfg)
    if _canonical_hash(_summary_threshold_payload(
        normalized_training, include_phase=include_breathing
    )) != candidate.get(
        "training_summary_sha256"
    ):
        raise CalibrationError("candidate training summary hash mismatch")
    derived_ids = sorted(str(item["acquisition_id"]) for item in normalized_training)
    derived_hashes = sorted(str(item["raw_sha256"]) for item in normalized_training)
    derived_coverage = _coverage(
        normalized_training,
        TRAINING_MIN_ACQUISITIONS,
        include_breathing=include_breathing,
    )
    if candidate.get("training_acquisition_ids") != derived_ids:
        raise CalibrationError("candidate training acquisition IDs differ from evidence")
    if candidate.get("training_raw_sha256") != derived_hashes:
        raise CalibrationError("candidate training raw hashes differ from evidence")
    if candidate.get("training_coverage") != derived_coverage:
        raise CalibrationError("candidate training coverage differs from evidence")
    rederived = _derive_thresholds(
        normalized_training,
        include_breathing=include_breathing,
    )
    if rederived != thresholds:
        raise CalibrationError("candidate thresholds do not match training derivation evidence")

    root = _repo_root()
    capabilities = _capabilities(cfg)
    if candidate.get("capabilities") != list(capabilities):
        raise CalibrationError("candidate capabilities are incompatible with current config")
    source_hashes = _source_hashes(root, capabilities)
    settings = _semantic_settings(cfg)
    expected_identity = {
        "source_sha256": _canonical_hash(source_hashes),
        "settings_sha256": _canonical_hash(settings),
    }
    for name, expected in expected_identity.items():
        if candidate.get(name) != expected:
            raise CalibrationError(f"candidate {name} is incompatible with current software/config")
    if candidate.get("source_hashes") != source_hashes:
        raise CalibrationError("candidate individual source hashes are incompatible")
    if candidate.get("semantic_settings") != settings:
        raise CalibrationError("candidate semantic settings were modified")
    expected_seed = cfg.get("seed")
    if any(item.get("seed") != expected_seed for item in normalized_training):
        raise CalibrationError("candidate training summary seed differs from config")
    return candidate


def evaluate_candidate(
    candidate: Mapping[str, object],
    heldout_summaries: Sequence[Mapping[str, object]],
    cfg: Mapping[str, object],
) -> dict[str, object]:
    """Evaluate a locked candidate without mutating thresholds or candidate."""
    locked = _validate_candidate(candidate, cfg)
    summaries = _normalized_summaries(heldout_summaries, "heldout")
    if any(item.get("seed") != cfg.get("seed") for item in summaries):
        raise CalibrationError("held-out summary seed must exactly match config seed")
    if "extended_breathing" in locked["capabilities"]:
        _verify_measurements_against_phase(summaries, cfg)
    include_breathing = "extended_breathing" in locked["capabilities"]
    coverage = _coverage(
        summaries,
        HELDOUT_MIN_ACQUISITIONS,
        include_breathing=include_breathing,
    )
    training_subtypes = {
        key.split(":", 1)[1]
        for key in locked["training_coverage"]
        if str(key).startswith("movement_subtype:")
    }
    heldout_subtypes = {
        str(episode["subtype"])
        for summary in summaries
        for episode in summary["movement_episodes"]
    }
    if training_subtypes != heldout_subtypes:
        missing = sorted(training_subtypes - heldout_subtypes)
        unseen = sorted(heldout_subtypes - training_subtypes)
        details = []
        if missing:
            details.append("missing held-out subtypes " + ", ".join(missing))
        if unseen:
            details.append("subtypes absent from training " + ", ".join(unseen))
        raise CalibrationError("movement subtype split mismatch: " + "; ".join(details))
    for subtype in training_subtypes:
        key = f"movement_subtype:{subtype}"
        if coverage.get(key, 0) < HELDOUT_MIN_ACQUISITIONS:
            raise CalibrationError(
                f"coverage {key} needs >= {HELDOUT_MIN_ACQUISITIONS} held-out acquisitions"
            )
    training_ids = set(locked["training_acquisition_ids"])
    training_hashes = set(locked["training_raw_sha256"])
    heldout_ids = {str(item["acquisition_id"]) for item in summaries}
    heldout_hashes = {str(item["raw_sha256"]) for item in summaries}
    if training_ids & heldout_ids:
        raise CalibrationError("training and held-out acquisition IDs overlap")
    if training_hashes & heldout_hashes:
        raise CalibrationError("training and held-out raw hashes overlap")

    thresholds = locked["thresholds"]
    failures: list[str] = []
    breathing_eligible: dict[str, bool] = {}
    presence = thresholds["presence"]
    movement = thresholds["movement"]

    for summary in summaries:
        acquisition = str(summary["acquisition_id"])
        powers = _finite_array(summary["presence_power_db"], "presence_power_db")
        state = summary["presence_state"]
        if state == "empty" and np.any(powers > float(presence["empty_max_db"])):
            failures.append(f"{acquisition}: empty scene crossed empty limit")
        elif state == "strong" and np.any(powers < float(presence["occupied_min_db"])):
            failures.append(f"{acquisition}: strong target fell below occupied limit")
        elif state == "weak" and np.any(powers >= float(presence["occupied_min_db"])):
            failures.append(f"{acquisition}: weak target was falsely confirmed occupied")

        if summary["movement_negative_state"] is not None:
            for feature, threshold in movement.items():
                if threshold["enabled"] and np.any(
                    _finite_array(summary["still_features"][feature], feature)
                    > float(threshold["exit"])
                ):
                    failures.append(f"{acquisition}: still {feature} did not clear motion")
        for episode in summary["movement_episodes"]:
            if not any(
                threshold["enabled"]
                and float(episode["feature_maxima"][feature]) >= float(threshold["entry"])
                for feature, threshold in movement.items()
            ):
                failures.append(
                    f"{acquisition}: movement subtype {episode['subtype']} was not detected"
                )
        if summary["breathing_state"] is not None:
            quality_failures = _breathing_quality_failures(summary, presence, movement)
            breathing_eligible[acquisition] = not quality_failures
            failures.extend(quality_failures)

    if include_breathing:
        # Lazy import keeps the movement-only milestone independent of the later
        # extended-breathing implementation.
        from .breathing import assess_breathing

        fs = float(cfg["session"]["frame_rate_hz"])
        for summary in summaries:
            expected = summary["breathing_state"]
            if expected is None:
                continue
            acquisition = str(summary["acquisition_id"])
            for measurement in summary["breathing_measurements"]:
                assessment = assess_breathing(
                    np.asarray(measurement["phase"], dtype=np.float64),
                    fs,
                    thresholds["breathing"],
                    eligible=breathing_eligible.get(acquisition, False),
                )
                state = assessment.state if hasattr(assessment, "state") else assessment["state"]
                expected_state = {
                    "periodic": "positive",
                    "irregular": "unresolved",
                    "quiet": "quiet",
                }[str(expected)]
                if state != expected_state:
                    failures.append(
                        f"{acquisition}: expected breathing {expected_state}, got {state}"
                    )

    candidate_hash = _canonical_hash(locked)
    record = {
        "schema": CALIBRATION_SCHEMA,
        "status": "accepted" if not failures else "rejected",
        "rejection_reasons": failures,
        "candidate_sha256": candidate_hash,
        "candidate": locked,
        "source_sha256": locked["source_sha256"],
        "settings_sha256": locked["settings_sha256"],
        "source_hashes": copy.deepcopy(locked["source_hashes"]),
        "semantic_settings": copy.deepcopy(locked["semantic_settings"]),
        "thresholds": copy.deepcopy(locked["thresholds"]),
        "thresholds_sha256": locked["thresholds_sha256"],
        "training_acquisition_ids": copy.deepcopy(locked["training_acquisition_ids"]),
        "training_raw_sha256": copy.deepcopy(locked["training_raw_sha256"]),
        "heldout_summary_sha256": _canonical_hash(
            _summary_threshold_payload(summaries, include_phase=include_breathing)
        ),
        "heldout_summaries": _summary_threshold_payload(
            summaries,
            include_phase=include_breathing,
        ),
        "heldout_acquisition_ids": sorted(heldout_ids),
        "heldout_raw_sha256": sorted(heldout_hashes),
        "heldout_coverage": coverage,
    }
    return record


def _validate_threshold_shape(thresholds: object, *, include_breathing: bool) -> None:
    expected_sections = {"presence", "movement"}
    if include_breathing:
        expected_sections.add("breathing")
    if not isinstance(thresholds, Mapping) or set(thresholds) != expected_sections:
        raise CalibrationError("calibration threshold sections are invalid")
    presence = thresholds["presence"]
    if not isinstance(presence, Mapping) or set(presence) != {"empty_max_db", "occupied_min_db"}:
        raise CalibrationError("presence threshold fields are invalid")
    empty = _finite_number(presence["empty_max_db"], "presence.empty_max_db")
    occupied = _finite_number(presence["occupied_min_db"], "presence.occupied_min_db")
    if not empty < occupied:
        raise CalibrationError("presence thresholds lack strict separation")
    movement = thresholds["movement"]
    if not isinstance(movement, Mapping) or set(movement) != set(FEATURE_NAMES):
        raise CalibrationError("movement threshold fields are invalid")
    if not any(bool(item.get("enabled")) for item in movement.values() if isinstance(item, Mapping)):
        raise CalibrationError("at least one movement feature must be enabled")
    for feature in FEATURE_NAMES:
        item = movement[feature]
        if not isinstance(item, Mapping) or set(item) != {"enabled", "entry", "exit"}:
            raise CalibrationError(f"movement.{feature} fields are invalid")
        if type(item["enabled"]) is not bool:
            raise CalibrationError(f"movement.{feature}.enabled must be Boolean")
        if item["enabled"]:
            entry = _finite_number(item["entry"], f"movement.{feature}.entry")
            exit_value = _finite_number(item["exit"], f"movement.{feature}.exit")
            if not exit_value < entry:
                raise CalibrationError(f"movement.{feature} thresholds lack hysteresis")
        elif item["entry"] is not None or item["exit"] is not None:
            raise CalibrationError(f"disabled movement.{feature} thresholds must be null")
    if not include_breathing:
        return
    breathing = thresholds["breathing"]
    expected = {
        "fft_score_min", "ha_score_min", "quiet_resp_rms_max",
        "periodic_resp_rms_min", "subband_rms_max", "drift_rms_max", "persistence_min",
    }
    if not isinstance(breathing, Mapping) or set(breathing) != expected:
        raise CalibrationError("breathing threshold fields are invalid")
    values = {name: _finite_number(breathing[name], f"breathing.{name}") for name in expected}
    if not values["quiet_resp_rms_max"] < values["periodic_resp_rms_min"]:
        raise CalibrationError("breathing amplitude thresholds lack strict separation")
    if not 0.0 <= values["persistence_min"] <= 1.0:
        raise CalibrationError("breathing.persistence_min must lie in [0, 1]")


def _require_runtime_hardware_provenance(
    summaries: Sequence[Mapping[str, object]],
    field: str,
) -> None:
    for summary in summaries:
        if summary.get("capture_provenance") not in HARDWARE_CAPTURE_PROVENANCE:
            raise CalibrationError(
                f"{field} contains non-hardware calibration provenance for "
                f"{summary.get('acquisition_id')!r}"
            )


def validate_calibration(
    cfg: Mapping[str, object],
    root: str | Path | None = None,
    *,
    record_root: str | Path | None = None,
) -> dict[str, object]:
    """Load and validate the accepted hash-bound record configured for runtime.

    ``root`` remains the authority for load-bearing software source hashes.
    Artifact verification may separately constrain a copied record beneath
    ``record_root`` without treating that portable artifact directory as source.
    """
    repo_root = _repo_root(root)
    calibration_record_root = (
        _repo_root(record_root) if record_root is not None else repo_root
    )
    try:
        calibration = cfg["development_motion"]["calibration"]
        record_path_value = calibration["record_path"]
        expected_file_hash = require_sha256(
            calibration["record_sha256"], "development_motion.calibration.record_sha256"
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CalibrationError(f"invalid development motion calibration config: {exc}") from exc
    record_path = resolve_bound_path(
        calibration_record_root,
        record_path_value,
        "development_motion.calibration.record_path",
    )
    if not record_path.is_file():
        raise CalibrationError(f"calibration record is unavailable: {record_path}")
    content = record_path.read_bytes()
    actual_file_hash = sha256_bytes(content)
    if actual_file_hash != expected_file_hash:
        raise CalibrationError("calibration record file hash mismatch")
    record = read_json_object(record_path)
    if content != canonical_json_bytes(record):
        raise CalibrationError("calibration record is not canonical JSON")
    if record.get("schema") != CALIBRATION_SCHEMA or record.get("status") != "accepted":
        raise CalibrationError("calibration record is not an accepted v1 record")
    if record.get("rejection_reasons") != []:
        raise CalibrationError("accepted calibration record contains rejection reasons")
    candidate = record.get("candidate")
    if not isinstance(candidate, Mapping):
        raise CalibrationError("calibration candidate is missing")
    locked = _validate_candidate(candidate, cfg)
    _require_runtime_hardware_provenance(
        locked["training_summaries"], "training evidence"
    )
    if _canonical_hash(locked) != record.get("candidate_sha256"):
        raise CalibrationError("embedded candidate hash mismatch")
    if record.get("thresholds") != locked.get("thresholds"):
        raise CalibrationError("record thresholds differ from locked candidate")
    if record.get("thresholds_sha256") != locked.get("thresholds_sha256"):
        raise CalibrationError("record threshold hash differs from locked candidate")
    include_breathing = "extended_breathing" in locked["capabilities"]
    _validate_threshold_shape(record.get("thresholds"), include_breathing=include_breathing)
    identity = semantic_identity(cfg)
    if any(record.get(name) != digest for name, digest in identity.items()):
        raise CalibrationError("calibration semantic identity is incompatible")
    if record.get("source_hashes") != _source_hashes(repo_root, locked["capabilities"]):
        raise CalibrationError("calibration individual source hashes are incompatible")
    if record.get("semantic_settings") != _semantic_settings(cfg):
        raise CalibrationError("calibration semantic settings are incompatible")
    heldout_payload = record.get("heldout_summaries")
    if not isinstance(heldout_payload, list):
        raise CalibrationError("held-out evaluation evidence is missing")
    _require_runtime_hardware_provenance(heldout_payload, "held-out evidence")
    recomputed = evaluate_candidate(locked, heldout_payload, cfg)
    if record != recomputed:
        raise CalibrationError("calibration record differs from recomputed held-out evaluation")
    return record
