#!/usr/bin/env python3
"""Build and evaluate development live-motion calibration from owner captures.

This is an offline, owner-run tool.  It never configures radar hardware and it
never reads a physiological reference.  Each manifest entry names one complete,
independent development live capture and supplies operator labels before fitting.
The candidate is written and reloaded before any held-out ADC file is opened.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.live_demo import LiveFrameSource
from src.live_motion.cache import RangeBinCache, reconstruct_phase
from src.live_motion.calibration import (
    BREATHING_STATES,
    FALLBACK_CHECKPOINT_SHA,
    FEATURE_NAMES,
    POSITIONS,
    PRESENCE_STATES,
    REQUIRED_MOVEMENT_SUBTYPES,
    SUMMARY_SCHEMA,
    CalibrationError,
    build_candidate,
    evaluate_candidate,
)
from src.live_motion.features import RangeFeatureExtractor
from src.live_motion.config import _parse_live_motion_settings
from src.m2.common import (
    canonical_json_bytes,
    read_json_object,
    require_sha256,
    sha256_bytes,
    sha256_file,
    write_new_bytes,
    write_new_json,
)
from src.warmup_select import derive_candidate_bins, run_warmup_selection


MANIFEST_SCHEMA = "live_motion_calibration_manifest_v1"
PURPOSE = "development_live_motion_calibration"
_PROSPECTIVE_NAME = re.compile(r"(?:^|_)P\d{3}(?:_|$)", re.IGNORECASE)
_GIT_OBJECT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fit training-only live-motion thresholds and evaluate held-out captures."
    )
    parser.add_argument("--config", required=True, type=Path,
                        help="opt-in development live-motion YAML")
    parser.add_argument("--manifest", required=True, type=Path,
                        help="owner-authored calibration manifest YAML")
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="new directory for immutable calibration evidence")
    parser.add_argument(
        "--capture-root",
        type=Path,
        default=ROOT,
        help="repository checkout whose results/live_demo contains owner captures",
    )
    return parser.parse_args()


def _load_yaml(path: Path, field: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise CalibrationError(f"cannot read {field} {path}: {exc}") from exc
    if type(value) is not dict:
        raise CalibrationError(f"{field} must contain a YAML mapping")
    return value


def _relative_development_run(root: Path, value: object, field: str) -> Path:
    if type(value) is not str or not value.strip():
        raise CalibrationError(f"{field} must be a non-empty repository-relative path")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise CalibrationError(f"{field} must be a repository-relative path without '..'")
    lower_parts = tuple(part.lower() for part in relative.parts)
    joined = "/".join(lower_parts)
    if (
        lower_parts[:2] != ("results", "live_demo")
        or "prospective" in lower_parts
        or "m2_capture_work" in lower_parts
        or any(_PROSPECTIVE_NAME.search(part) for part in relative.parts)
    ):
        raise CalibrationError(
            f"{field} must identify an unsealed development run under results/live_demo"
        )
    allowed_root = (root / "results" / "live_demo").resolve(strict=True)
    candidate = root / relative
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise CalibrationError(f"{field} is unavailable: {candidate}") from exc
    if resolved != allowed_root and allowed_root not in resolved.parents:
        raise CalibrationError(f"{field} resolves outside results/live_demo")
    if candidate.absolute() != resolved or candidate.is_symlink():
        raise CalibrationError(f"{field} may not traverse a symlink or junction")
    resolved_parts = tuple(part.lower() for part in resolved.parts)
    if (
        "prospective" in resolved_parts
        or "m2_capture_work" in resolved_parts
        or any(_PROSPECTIVE_NAME.search(part) for part in resolved.parts)
    ):
        raise CalibrationError(f"{field} resolves to a protected capture path")
    return resolved


def _regular_run_artifact(run_dir: Path, name: str, acquisition_id: object) -> Path:
    path = run_dir / name
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise CalibrationError(f"{acquisition_id}: missing {name}") from exc
    if path.is_symlink() or resolved != path.absolute() or resolved.parent != run_dir:
        raise CalibrationError(
            f"{acquisition_id}: {name} may not be a symlink/reparse-point escape"
        )
    if not resolved.is_file():
        raise CalibrationError(f"{acquisition_id}: {name} is not a regular file")
    try:
        link_count = resolved.stat().st_nlink
    except OSError as exc:
        raise CalibrationError(f"{acquisition_id}: cannot inspect {name}") from exc
    if link_count != 1:
        raise CalibrationError(
            f"{acquisition_id}: {name} must not be a hard-linked artifact"
        )
    return resolved


def _frame_bounds(value: object, field: str) -> tuple[int, int]:
    if (
        type(value) is not list
        or len(value) != 2
        or any(type(item) is not int for item in value)
        or value[0] < 0
        or value[1] <= value[0]
    ):
        raise CalibrationError(f"{field} must be increasing half-open integer frame bounds")
    return int(value[0]), int(value[1])


def _validate_manifest(cfg: Mapping[str, object], manifest: Mapping[str, object]) -> list[dict[str, object]]:
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise CalibrationError(f"manifest schema must be {MANIFEST_SCHEMA!r}")
    if manifest.get("purpose") != PURPOSE:
        raise CalibrationError(f"manifest purpose must be {PURPOSE!r}")
    if manifest.get("development_only") is not True:
        raise CalibrationError("manifest development_only must be true")
    seed = manifest.get("seed")
    if type(seed) is not int or seed != cfg.get("seed"):
        raise CalibrationError("manifest seed must exactly match the development config seed")
    required_subtypes = manifest.get("required_movement_subtypes")
    if type(required_subtypes) is not list or not required_subtypes:
        raise CalibrationError("required_movement_subtypes must be a non-empty list")
    if len(set(required_subtypes)) != len(required_subtypes) or any(
        type(value) is not str or not value for value in required_subtypes
    ):
        raise CalibrationError("required_movement_subtypes must be unique non-empty strings")
    missing_required = set(REQUIRED_MOVEMENT_SUBTYPES) - set(required_subtypes)
    if missing_required:
        raise CalibrationError(
            "required_movement_subtypes omits plan-required labels: "
            + ", ".join(sorted(missing_required))
        )
    entries = manifest.get("acquisitions")
    if type(entries) is not list or not entries:
        raise CalibrationError("manifest acquisitions must be a non-empty list")

    normalized: list[dict[str, object]] = []
    ids: set[str] = set()
    hashes: set[str] = set()
    extended = bool(cfg["development_motion"]["extended_breathing_enabled"])
    for index, raw_entry in enumerate(entries):
        field = f"acquisitions[{index}]"
        if type(raw_entry) is not dict:
            raise CalibrationError(f"{field} must be a mapping")
        entry = dict(raw_entry)
        acquisition_id = entry.get("acquisition_id")
        if type(acquisition_id) is not str or not acquisition_id.strip():
            raise CalibrationError(f"{field}.acquisition_id must be a non-empty string")
        if acquisition_id in ids:
            raise CalibrationError(f"duplicate acquisition_id {acquisition_id!r}")
        ids.add(acquisition_id)
        split = entry.get("split")
        if split not in ("training", "heldout"):
            raise CalibrationError(f"{field}.split must be exactly 'training' or 'heldout'")
        try:
            raw_hash = require_sha256(entry.get("raw_sha256"), f"{field}.raw_sha256")
        except ValueError as exc:
            raise CalibrationError(str(exc)) from exc
        if raw_hash in hashes:
            raise CalibrationError("one raw acquisition/hash cannot supply multiple clips or splits")
        hashes.add(raw_hash)
        if entry.get("presence_state") not in PRESENCE_STATES:
            raise CalibrationError(f"{field}.presence_state must be one of {PRESENCE_STATES}")
        if entry.get("position") not in (*POSITIONS, "not_applicable"):
            raise CalibrationError(f"{field}.position is invalid")
        placement = entry.get("placement_m")
        if entry.get("position") == "not_applicable":
            if placement is not None:
                raise CalibrationError(f"{field}.placement_m must be null")
        else:
            if type(placement) not in (int, float) or isinstance(placement, bool) or not np.isfinite(placement):
                raise CalibrationError(f"{field}.placement_m must be a finite number")
            gate_lo, gate_hi = map(float, cfg["protocol"]["subject_distance_m"])
            if not gate_lo <= float(placement) <= gate_hi:
                raise CalibrationError(f"{field}.placement_m lies outside the physical gate")
        if entry.get("movement_negative_state") not in ("normal", "deep", "slow", None):
            raise CalibrationError(f"{field}.movement_negative_state is invalid")
        if entry.get("breathing_state") not in (*BREATHING_STATES, None):
            raise CalibrationError(f"{field}.breathing_state is invalid")
        target_kind = entry.get("target_kind")
        if target_kind not in (
            "participant", "stationary_reflector", "empty_scene", "weak_reflector"
        ):
            raise CalibrationError(f"{field}.target_kind is invalid")
        if entry.get("breathing_state") == "quiet" and target_kind != "stationary_reflector":
            raise CalibrationError(f"{field}: quiet epochs require a stationary reflector")
        if entry.get("breathing_state") in ("periodic", "irregular") and target_kind != "participant":
            raise CalibrationError(f"{field}: breathing activity clips require a participant")
        if entry.get("presence_state") == "empty" and target_kind != "empty_scene":
            raise CalibrationError(f"{field}: empty presence clips require target_kind=empty_scene")
        if entry.get("breathing_state") is not None and not extended:
            raise CalibrationError("motion-only calibration cannot include breathing labels")
        still_intervals = entry.get("still_intervals")
        if type(still_intervals) is not list:
            raise CalibrationError(f"{field}.still_intervals must be a list")
        entry["still_intervals"] = [
            list(_frame_bounds(bounds, f"{field}.still_intervals[{i}]"))
            for i, bounds in enumerate(still_intervals)
        ]
        if entry.get("movement_negative_state") is not None and not still_intervals:
            raise CalibrationError(f"{field} needs still intervals for its negative label")

        episodes = entry.get("movement_episodes")
        if type(episodes) is not list:
            raise CalibrationError(f"{field}.movement_episodes must be a list")
        cleaned_episodes = []
        for episode_index, episode_raw in enumerate(episodes):
            episode_field = f"{field}.movement_episodes[{episode_index}]"
            if type(episode_raw) is not dict:
                raise CalibrationError(f"{episode_field} must be a mapping")
            episode = dict(episode_raw)
            if episode.get("subtype") not in required_subtypes:
                raise CalibrationError(f"{episode_field}.subtype is not declared by the manifest")
            assigned = episode.get("assigned_features")
            if assigned is None and split == "heldout":
                assigned = []
                episode["assigned_features"] = assigned
            if type(assigned) is not list:
                raise CalibrationError(f"{episode_field}.assigned_features is invalid")
            if split == "training" and not assigned:
                raise CalibrationError(
                    f"{episode_field}.assigned_features must be labelled before fitting"
                )
            if split == "heldout" and assigned:
                raise CalibrationError(
                    f"{episode_field}.assigned_features must be absent/empty on held-out clips"
                )
            if len(set(assigned)) != len(assigned) or any(
                name not in FEATURE_NAMES for name in assigned
            ):
                raise CalibrationError(f"{episode_field}.assigned_features is invalid")
            episode["frame_bounds"] = list(
                _frame_bounds(episode.get("frame_bounds"), f"{episode_field}.frame_bounds")
            )
            label = episode.get("operator_label")
            if type(label) is not str or not label.strip():
                raise CalibrationError(f"{episode_field}.operator_label must be non-empty")
            cue = episode.get("cue_time_utc")
            if cue is not None and (type(cue) is not str or not cue.strip()):
                raise CalibrationError(f"{episode_field}.cue_time_utc must be null or a string")
            cleaned_episodes.append(episode)
        entry["movement_episodes"] = cleaned_episodes

        breathing_window = entry.get("breathing_window")
        if entry.get("breathing_state") is None:
            if breathing_window is not None:
                raise CalibrationError(f"{field}.breathing_window requires a breathing_state")
        else:
            start, stop = _frame_bounds(breathing_window, f"{field}.breathing_window")
            expected = int(round(float(cfg["development_motion"]["extended_breathing_window_s"])
                                 * float(cfg["session"]["frame_rate_hz"])))
            if stop - start != expected:
                raise CalibrationError(
                    f"{field}.breathing_window must contain exactly {expected} frames"
                )
            monitor_hop_frames = int(round(
                float(cfg["development_motion"]["monitor_hop_s"])
                * float(cfg["session"]["frame_rate_hz"])
            ))
            if start % monitor_hop_frames:
                raise CalibrationError(
                    f"{field}.breathing_window start must align to the "
                    f"{monitor_hop_frames}-frame monitor hop"
                )
            entry["breathing_window"] = [start, stop]
        normalized.append(entry)
    return normalized


def _reject_overlapping_labels(entry: Mapping[str, object], n_frames: int) -> None:
    bounds: list[tuple[int, int, str]] = []
    for index, value in enumerate(entry["still_intervals"]):
        start, stop = map(int, value)
        bounds.append((start, stop, f"still_intervals[{index}]"))
    for index, episode in enumerate(entry["movement_episodes"]):
        start, stop = map(int, episode["frame_bounds"])
        bounds.append((start, stop, f"movement_episodes[{index}]"))
    for start, stop, name in bounds:
        if stop > n_frames:
            raise CalibrationError(f"{entry['acquisition_id']}:{name} exceeds capture frames")
    ordered = sorted(bounds)
    for left, right in zip(ordered, ordered[1:]):
        if right[0] < left[1]:
            raise CalibrationError(
                f"{entry['acquisition_id']}: operator still/movement labels overlap"
            )
    breathing = entry.get("breathing_window")
    if breathing is not None and int(breathing[1]) > n_frames:
        raise CalibrationError(f"{entry['acquisition_id']}: breathing window exceeds capture")
    if breathing is not None:
        for episode in entry["movement_episodes"]:
            if _overlaps(int(breathing[0]), int(breathing[1]), episode["frame_bounds"]):
                raise CalibrationError(
                    f"{entry['acquisition_id']}: breathing window overlaps movement episode"
                )


def _capture_provenance(
    metadata: Mapping[str, object],
    acquisition_id: object,
    *,
    run_name: str,
) -> dict[str, object]:
    """Resolve trusted acquisition identity before validity or ADC bytes are read."""

    prefix = f"{acquisition_id}:"
    if metadata.get("replay_files") is not None or metadata.get("replay_file_hashes") is not None:
        raise CalibrationError(prefix + " replay captures cannot train calibration")
    synthetic_fixture = metadata.get("synthetic_fixture")
    if synthetic_fixture is not None and type(synthetic_fixture) is not bool:
        raise CalibrationError(prefix + " synthetic_fixture marker must be Boolean")
    if synthetic_fixture is True:
        raise CalibrationError(prefix + " synthetic captures cannot train calibration")

    commit = metadata.get("git_commit")
    dirty = metadata.get("git_dirty")
    if type(commit) is not str or _GIT_OBJECT_RE.fullmatch(commit) is None:
        raise CalibrationError(prefix + " capture Git commit is missing or invalid")
    if dirty is not False:
        raise CalibrationError(prefix + " calibration capture checkout must be clean")

    source_kind = metadata.get("source_kind")
    source_id = metadata.get("source_id")
    if source_kind is None:
        if source_id is not None or commit != FALLBACK_CHECKPOINT_SHA:
            raise CalibrationError(prefix + " legacy capture provenance is not authorized")
        resolved = "legacy_checkpoint_live_dca1000"
    else:
        if source_kind != "live_dca1000":
            raise CalibrationError(prefix + f" unsupported capture source_kind {source_kind!r}")
        expected_source_id = f"live:{run_name}:adc_stream.bin"
        if source_id != expected_source_id:
            raise CalibrationError(
                prefix + f" live capture source_id must equal {expected_source_id!r}"
            )
        resolved = "live_dca1000"
    return {
        "capture_provenance": resolved,
        "capture_source_id": source_id,
        "capture_git_commit": commit,
        "capture_git_dirty": False,
    }


def _prepare_capture(root: Path, entry: Mapping[str, object], cfg: Mapping[str, object]) -> dict[str, object]:
    run_dir = _relative_development_run(root, entry.get("run_dir"), "run_dir")
    metadata_path = _regular_run_artifact(run_dir, "run_metadata.json", entry["acquisition_id"])
    raw_path = _regular_run_artifact(run_dir, "adc_stream.bin", entry["acquisition_id"])
    validity_path = _regular_run_artifact(run_dir, "frame_validity.npy", entry["acquisition_id"])
    metadata = read_json_object(metadata_path)
    if (
        metadata.get("mode") != "live"
        or metadata.get("prospective_study_mode") is not False
        or metadata.get("completion_status") != "completed"
        or metadata.get("raw_stream_format") != "adc_bytes_no_packet_headers"
    ):
        raise CalibrationError(f"{entry['acquisition_id']}: capture is not a completed development live run")
    provenance = _capture_provenance(
        metadata,
        entry["acquisition_id"],
        run_name=run_dir.name,
    )
    expected_hash = str(entry["raw_sha256"])
    if metadata.get("live_raw_mirror_hash") != expected_hash:
        raise CalibrationError(f"{entry['acquisition_id']}: metadata/raw hash binding differs")

    capture_cfg = metadata.get("config")
    if not isinstance(capture_cfg, Mapping):
        raise CalibrationError(f"{entry['acquisition_id']}: metadata effective config is absent")
    for name in (
        "num_adc_samples", "num_rx", "num_chirps_per_frame",
        "range_resolution_m", "range_bias_m", "iq_swap",
    ):
        if capture_cfg.get("profile", {}).get(name) != cfg["profile"].get(name):
            raise CalibrationError(f"{entry['acquisition_id']}: profile.{name} differs from calibration config")
    for section_name in (
        "hw_profile", "hw_frame", "phase", "bin_selection", "respiration", "heart",
        "range_calibration",
    ):
        if capture_cfg.get(section_name) != cfg.get(section_name):
            raise CalibrationError(
                f"{entry['acquisition_id']}: {section_name} differs from calibration config"
            )
    if capture_cfg.get("protocol", {}).get("subject_distance_m") != cfg["protocol"].get("subject_distance_m"):
        raise CalibrationError(f"{entry['acquisition_id']}: corrected physical range gate differs")
    if capture_cfg.get("session", {}).get("frame_rate_hz") != cfg["session"].get("frame_rate_hz"):
        raise CalibrationError(f"{entry['acquisition_id']}: frame rate differs")

    try:
        validity = np.load(validity_path, allow_pickle=False)
    except (OSError, ValueError) as exc:
        raise CalibrationError(f"{entry['acquisition_id']}: cannot load frame validity: {exc}") from exc
    if validity.ndim != 1 or validity.dtype != np.bool_:
        raise CalibrationError(f"{entry['acquisition_id']}: frame validity must be a Boolean vector")
    packet_stats = metadata.get("live_packet_stats")
    if not isinstance(packet_stats, Mapping):
        raise CalibrationError(f"{entry['acquisition_id']}: live packet statistics are absent")
    validity_sha256 = sha256_file(validity_path)
    if packet_stats.get("frame_validity_map_sha256") != validity_sha256:
        raise CalibrationError(f"{entry['acquisition_id']}: frame validity hash mismatch")
    profile = cfg["profile"]
    bytes_per_frame = (
        int(profile["num_chirps_per_frame"])
        * int(profile["num_rx"])
        * int(profile["num_adc_samples"])
        * 4
    )
    size = raw_path.stat().st_size
    if size % bytes_per_frame or size // bytes_per_frame != validity.size:
        raise CalibrationError(f"{entry['acquisition_id']}: raw size and validity count disagree")
    _reject_overlapping_labels(entry, int(validity.size))
    breathing = entry.get("breathing_window")
    if breathing is not None and not np.all(validity[int(breathing[0]):int(breathing[1])]):
        raise CalibrationError(f"{entry['acquisition_id']}: breathing window contains invalid frames")
    return {
        "run_dir": run_dir,
        "raw_path": raw_path,
        "validity_path": validity_path,
        "validity": validity,
        "bytes_per_frame": bytes_per_frame,
        "n_frames": int(validity.size),
        "metadata_sha256": sha256_file(metadata_path),
        "validity_sha256": validity_sha256,
        "expected_raw_sha256": expected_hash,
        **provenance,
    }


def _verify_raw_hash(entry: Mapping[str, object], prepared: Mapping[str, object]) -> None:
    """First ADC-byte read for a split; called after candidate lock for held-out."""
    actual_hash = sha256_file(Path(prepared["raw_path"]))
    if actual_hash != prepared["expected_raw_sha256"]:
        raise CalibrationError(f"{entry['acquisition_id']}: adc_stream.bin hash mismatch")


def _inside_any(start: int, stop: int, intervals: Sequence[Sequence[int]]) -> bool:
    return any(start >= int(lo) and stop <= int(hi) for lo, hi in intervals)


def _overlaps(start: int, stop: int, interval: Sequence[int]) -> bool:
    return start < int(interval[1]) and stop > int(interval[0])


def _atomic_npz(path: Path, arrays: Mapping[str, object]) -> str:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("xb") as handle:
        np.savez_compressed(handle, **arrays)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return sha256_file(path)


def _summarize_capture(
    root: Path,
    entry: Mapping[str, object],
    cfg: Mapping[str, object],
    prepared: Mapping[str, object],
    evidence_dir: Path,
) -> dict[str, object]:
    profile = cfg["profile"]
    decoder = LiveFrameSource(dict(profile), net_cfg={})
    extractor = RangeFeatureExtractor(dict(cfg))
    validity = np.asarray(prepared["validity"], dtype=np.bool_)
    breathing_window = entry.get("breathing_window")
    cache = None
    selector_frames: list[np.ndarray] = []
    if breathing_window is not None:
        window_frames = int(breathing_window[1]) - int(breathing_window[0])
        cache = RangeBinCache(
            capacity_frames=window_frames,
            n_chirps=int(profile["num_chirps_per_frame"]),
            n_rx=int(profile["num_rx"]),
            candidate_bins=list(extractor.candidate_bins),
        )

    monitors: list[dict[str, object]] = []
    with Path(prepared["raw_path"]).open("rb") as handle:
        for frame_index in range(int(prepared["n_frames"])):
            frame_bytes = handle.read(int(prepared["bytes_per_frame"]))
            if len(frame_bytes) != int(prepared["bytes_per_frame"]):
                raise CalibrationError(f"{entry['acquisition_id']}: short raw frame read")
            raw_frame = decoder._decode_frame(frame_bytes)
            valid = bool(validity[frame_index])
            observation = extractor.observe(
                frame_index,
                raw_frame,
                valid=valid,
                invalid_reason="capture_frame_invalid" if not valid else "",
            )
            if observation.monitor is not None:
                monitor = observation.monitor
                monitors.append({
                    "frame_start": monitor.frame_start,
                    "frame_stop": monitor.frame_stop,
                    "valid": monitor.valid,
                    "reason": monitor.reason,
                    "presence": monitor.presence_power_db,
                    "phase_activity": monitor.phase_activity_rad_rms,
                    "range_profile_change": monitor.range_profile_change,
                })
            if breathing_window is not None and int(breathing_window[0]) <= frame_index < int(breathing_window[1]):
                assert cache is not None
                cache.append(
                    frame_index,
                    observation.gate_bins,
                    observation.gate_samples,
                    valid=observation.valid,
                )
                selector_stop = int(breathing_window[0]) + int(round(
                    float(cfg["development_motion"]["ordinary_window_s"])
                    * float(cfg["session"]["frame_rate_hz"])
                ))
                if frame_index < selector_stop:
                    selector_frames.append(np.array(raw_frame, copy=True))
        if handle.read(1):
            raise CalibrationError(f"{entry['acquisition_id']}: trailing raw bytes remain")

    valid_monitors = [monitor for monitor in monitors if bool(monitor["valid"])]
    if not valid_monitors:
        raise CalibrationError(f"{entry['acquisition_id']}: no valid monitor blocks")
    presence_values = [float(monitor["presence"]) for monitor in valid_monitors]
    still_features = {name: [] for name in FEATURE_NAMES}
    for monitor in valid_monitors:
        if _inside_any(
            int(monitor["frame_start"]), int(monitor["frame_stop"]), entry["still_intervals"]
        ):
            for name in FEATURE_NAMES:
                still_features[name].append(float(monitor[name]))
    if entry.get("movement_negative_state") is not None and any(
        not still_features[name] for name in FEATURE_NAMES
    ):
        raise CalibrationError(f"{entry['acquisition_id']}: labelled still intervals contain no full monitor block")

    episodes: list[dict[str, object]] = []
    for episode in entry["movement_episodes"]:
        selected = [
            monitor for monitor in valid_monitors
            if _overlaps(int(monitor["frame_start"]), int(monitor["frame_stop"]), episode["frame_bounds"])
        ]
        if not selected:
            raise CalibrationError(
                f"{entry['acquisition_id']}: movement episode contains no valid monitor block"
            )
        scores = {name: [float(monitor[name]) for monitor in selected] for name in FEATURE_NAMES}
        episodes.append({
            "subtype": episode["subtype"],
            "assigned_features": list(episode["assigned_features"]),
            "frame_bounds": list(episode["frame_bounds"]),
            "operator_label": episode["operator_label"],
            "cue_time_utc": episode.get("cue_time_utc"),
            "positive_monitor_selection_rule": "any_half_open_overlap_v1",
            "monitor_frame_bounds": [
                [int(monitor["frame_start"]), int(monitor["frame_stop"])] for monitor in selected
            ],
            "monitor_scores": scores,
            "feature_maxima": {name: max(scores[name]) for name in FEATURE_NAMES},
        })

    measurement = None
    selection_evidence: dict[str, object] | None = None
    selected_bin = None
    if breathing_window is not None:
        expected_selector_frames = int(round(
            float(cfg["development_motion"]["ordinary_window_s"])
            * float(cfg["session"]["frame_rate_hz"])
        ))
        if len(selector_frames) != expected_selector_frames:
            raise CalibrationError(f"{entry['acquisition_id']}: selector window is incomplete")
        selected_bin, winning_dsp, selection_evidence = run_warmup_selection(
            np.stack(selector_frames),
            derive_candidate_bins(dict(cfg)),
            dict(cfg),
            float(cfg["session"]["frame_rate_hz"]),
        )
        if entry["breathing_state"] in ("periodic", "irregular") and winning_dsp is None:
            raise CalibrationError(
                f"{entry['acquisition_id']}: all-DSP-failed selection cannot calibrate activity"
            )
        assert cache is not None
        snapshot = cache.snapshot(int(breathing_window[0]), int(breathing_window[1]))
        phase = reconstruct_phase(snapshot, int(selected_bin))
        from src.live_motion.breathing import measure_breathing

        measurement = measure_breathing(phase, float(cfg["session"]["frame_rate_hz"]))
        measurement = dict(measurement)
        measurement["phase"] = phase
        measurement["frame_start"] = int(breathing_window[0])
        measurement["frame_stop"] = int(breathing_window[1])
        measurement["frame_valid_count"] = int(np.count_nonzero(
            validity[int(breathing_window[0]):int(breathing_window[1])]
        ))
        measurement["target_kind"] = entry["target_kind"]

    breathing_monitor_blocks: list[dict[str, object]] = []
    if breathing_window is not None:
        breathing_monitor_blocks = [
            {
                "frame_start": int(monitor["frame_start"]),
                "frame_stop": int(monitor["frame_stop"]),
                "valid": bool(monitor["valid"]),
                "presence": float(monitor["presence"]),
                "phase_activity": float(monitor["phase_activity"]),
                "range_profile_change": float(monitor["range_profile_change"]),
            }
            for monitor in monitors
            if int(monitor["frame_start"]) >= int(breathing_window[0])
            and int(monitor["frame_stop"]) <= int(breathing_window[1])
        ]

    summary: dict[str, object] = {
        "schema": SUMMARY_SCHEMA,
        "acquisition_id": entry["acquisition_id"],
        "split": entry["split"],
        "raw_sha256": entry["raw_sha256"],
        "capture_provenance": prepared["capture_provenance"],
        "capture_source_id": prepared["capture_source_id"],
        "capture_git_commit": prepared["capture_git_commit"],
        "capture_git_dirty": prepared["capture_git_dirty"],
        "presence_state": entry["presence_state"],
        "position": entry["position"],
        "placement_m": entry["placement_m"],
        "movement_negative_state": entry.get("movement_negative_state"),
        "breathing_state": entry.get("breathing_state"),
        "target_kind": entry["target_kind"],
        "seed": int(cfg["seed"]),
        "breathing_frame_bounds": (
            None if breathing_window is None else list(breathing_window)
        ),
        "breathing_monitor_blocks": breathing_monitor_blocks,
        "presence_power_db": presence_values,
        "still_features": still_features,
        "movement_episodes": episodes,
        "breathing_measurements": [] if measurement is None else [measurement],
        "operator_notes": entry.get("operator_notes", ""),
        "run_dir": str(Path(prepared["run_dir"]).relative_to(root)),
        "run_metadata_sha256": prepared["metadata_sha256"],
        "frame_validity_sha256": prepared["validity_sha256"],
        "frame_count": prepared["n_frames"],
        "feature_fft_count": extractor.feature_fft_count,
        "monitor_blocks": monitors,
        "selected_bin": selected_bin,
        "selection_evidence": selection_evidence,
    }

    arrays: dict[str, object] = {
        "monitor_frame_start": np.asarray([item["frame_start"] for item in monitors], dtype=np.int64),
        "monitor_frame_stop": np.asarray([item["frame_stop"] for item in monitors], dtype=np.int64),
        "monitor_valid": np.asarray([item["valid"] for item in monitors], dtype=np.bool_),
        "monitor_reason": np.asarray([item["reason"] for item in monitors], dtype="U64"),
        "monitor_presence_power_db": np.asarray([item["presence"] for item in monitors], dtype=np.float64),
        "monitor_phase_activity_rad_rms": np.asarray([item["phase_activity"] for item in monitors], dtype=np.float64),
        "monitor_range_profile_change": np.asarray([item["range_profile_change"] for item in monitors], dtype=np.float64),
    }
    if measurement is not None:
        for name, value in measurement.items():
            if isinstance(value, np.ndarray):
                if value.dtype != object:
                    arrays["breathing_" + name] = value
            elif type(value) in (bool, int, float, str):
                arrays["breathing_" + name] = np.asarray(value)
    evidence_path = evidence_dir / f"{entry['acquisition_id']}.npz"
    summary["evidence_npz_path"] = str(evidence_path.relative_to(evidence_dir.parent))
    summary["evidence_npz_sha256"] = _atomic_npz(evidence_path, arrays)
    return summary


def _json_summary(summary: Mapping[str, object]) -> dict[str, object]:
    """Small readable index; large numerical arrays remain in the hash-bound NPZ."""
    return {
        "schema": summary["schema"],
        "acquisition_id": summary["acquisition_id"],
        "split": summary["split"],
        "raw_sha256": summary["raw_sha256"],
        "capture_provenance": summary["capture_provenance"],
        "capture_source_id": summary["capture_source_id"],
        "capture_git_commit": summary["capture_git_commit"],
        "capture_git_dirty": summary["capture_git_dirty"],
        "run_dir": summary["run_dir"],
        "run_metadata_sha256": summary["run_metadata_sha256"],
        "frame_validity_sha256": summary["frame_validity_sha256"],
        "evidence_npz_path": summary["evidence_npz_path"],
        "evidence_npz_sha256": summary["evidence_npz_sha256"],
        "selected_bin": summary["selected_bin"],
        "selection_evidence": summary["selection_evidence"],
    }


def _write_failure(
    output_dir: Path,
    *,
    phase: str,
    reason: str,
    candidate_path: Path | None,
) -> None:
    """Retain an immutable negative result after an output run has started."""
    failure = {
        "schema": "live_motion_calibration_failure_v1",
        "status": "rejected",
        "phase_completed": phase,
        "reason": reason,
        "manifest_sha256": sha256_file(output_dir / "manifest.yaml"),
        "config_sha256": sha256_file(output_dir / "effective_config.yaml"),
        "candidate_path": None if candidate_path is None else candidate_path.name,
        "candidate_file_sha256": (
            None if candidate_path is None or not candidate_path.is_file()
            else sha256_file(candidate_path)
        ),
    }
    path = output_dir / "calibration_failure.json"
    if not path.exists():
        write_new_json(path, failure)


def run(
    config_path: Path,
    manifest_path: Path,
    output_dir: Path,
    *,
    capture_root: Path = ROOT,
) -> dict[str, object]:
    root = capture_root.resolve()
    if not (root / "results" / "live_demo").is_dir():
        raise CalibrationError(
            f"capture root has no results/live_demo directory: {root}"
        )
    cfg = _load_yaml(config_path.resolve(), "config")
    settings = _parse_live_motion_settings(cfg, allow_missing_calibration=True)
    if settings is None:
        raise CalibrationError("config has no development_motion section")
    manifest = _load_yaml(manifest_path.resolve(), "manifest")
    entries = _validate_manifest(cfg, manifest)

    if output_dir.exists():
        raise CalibrationError(f"refusing to overwrite calibration output directory {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=False)
    evidence_dir = output_dir / "summaries"
    evidence_dir.mkdir()
    write_new_bytes(output_dir / "manifest.yaml", manifest_path.resolve().read_bytes())
    write_new_bytes(output_dir / "effective_config.yaml", config_path.resolve().read_bytes())
    phase = "output_initialized"
    candidate_path: Path | None = None
    try:
        training: list[dict[str, object]] = []
        phase = "training_prepare"
        training_prepared = {
            str(entry["acquisition_id"]): _prepare_capture(root, entry, cfg)
            for entry in entries
            if entry["split"] == "training"
        }
        phase = "training_decode"
        for entry in entries:
            if entry["split"] == "training":
                prepared_capture = training_prepared[str(entry["acquisition_id"])]
                _verify_raw_hash(entry, prepared_capture)
                training.append(_summarize_capture(
                    root, entry, cfg, prepared_capture, evidence_dir
                ))
        phase = "training_threshold_derivation"
        candidate = build_candidate(training, cfg)
        candidate_path = write_new_json(output_dir / "candidate.json", candidate)

        # Capture one immutable digest, parse exactly those bytes, then verify the
        # file still has that digest before opening the held-out split.
        candidate_bytes = candidate_path.read_bytes()
        locked_candidate_hash = sha256_bytes(candidate_bytes)
        locked_candidate = json.loads(candidate_bytes.decode("utf-8"))
        if type(locked_candidate) is not dict:
            raise CalibrationError("locked candidate is not a JSON mapping")
        if sha256_file(candidate_path) != locked_candidate_hash:
            raise CalibrationError("candidate changed after lock and before held-out evaluation")

        heldout: list[dict[str, object]] = []
        phase = "heldout_prepare"
        for entry in entries:
            if entry["split"] == "heldout":
                # Do not touch any held-out artifact until candidate.json is
                # immutable.  Preparation itself reads metadata and validity,
                # so it is part of held-out evaluation rather than preflight.
                prepared_capture = _prepare_capture(root, entry, cfg)
                phase = "heldout_decode"
                _verify_raw_hash(entry, prepared_capture)
                heldout.append(_summarize_capture(
                    root, entry, cfg, prepared_capture, evidence_dir
                ))
                phase = "heldout_prepare"
        if sha256_file(candidate_path) != locked_candidate_hash:
            raise CalibrationError("candidate changed during held-out evaluation")
        phase = "heldout_evaluation"
        record = evaluate_candidate(locked_candidate, heldout, cfg)
        record_path = write_new_json(output_dir / "calibration_record.json", record)
        phase = "record_written"
        index = {
            "schema": "live_motion_calibration_run_v1",
            "manifest_sha256": sha256_file(output_dir / "manifest.yaml"),
            "config_sha256": sha256_file(output_dir / "effective_config.yaml"),
            "candidate_path": candidate_path.name,
            "candidate_file_sha256": locked_candidate_hash,
            "record_path": record_path.name,
            "record_file_sha256": sha256_file(record_path),
            "summaries": [_json_summary(item) for item in training + heldout],
        }
        write_new_json(output_dir / "calibration_run.json", index)
        return {"record": record, "index": index, "output_dir": str(output_dir.resolve())}
    except Exception as exc:
        _write_failure(
            output_dir,
            phase=phase,
            reason=f"{type(exc).__name__}: {exc}",
            candidate_path=candidate_path,
        )
        raise


def main() -> int:
    args = _parse_args()
    try:
        result = run(
            args.config,
            args.manifest,
            args.output_dir,
            capture_root=args.capture_root,
        )
    except (CalibrationError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    record = result["record"]
    print(f"Calibration {record['status']}: {result['output_dir']}")
    print(f"record_sha256: {result['index']['record_file_sha256']}")
    if record["status"] != "accepted":
        for reason in record["rejection_reasons"]:
            print(f"  REJECTED: {reason}")
        return 2
    print("Set development_motion.calibration.record_path to the record above and")
    print("record_sha256 to the printed digest only after owner review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
