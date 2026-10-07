"""Authorization and immutable provenance for a recorded replay session."""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from src.m2.common import ContractError, sha256_bytes
from src.m4.capture_registry import DEFAULT_REGISTRY, load_registry
from src.window_pipeline import run_config_hash
from scripts.live_demo import PAYLOAD_BYTES_PER_PKT


SUPPORTED_CAPTURE_ID = "20260728_224902_live_demo_massimo3"
SUPPORTED_REGISTRY_KEY = "m3"
SOURCE_KIND = "recorded_development_replay"
LEGACY_VALIDITY_ID = "legacy_zero_loss_inferred_v1"
BASELINE_VALIDITY_ID = "legacy_validity_unobserved"
_PROSPECTIVE_COMPONENT = re.compile(r"(?:^|_)P\d{3}(?:_|$)", re.IGNORECASE)


@dataclass(frozen=True)
class ReplaySessionSpec:
    capture_id: str
    capture_key: str
    capture_dir: Path
    adc_path: Path
    metadata_path: Path
    warmup_path: Path
    registered_adc_sha256: str
    metadata_sha256: str
    warmup_sha256: str
    original_config_sha256: str
    effective_config_sha256: str
    _original_config_json: bytes
    _effective_config_json: bytes
    frame_count: int
    frame_rate_hz: float
    frame_bytes: int
    num_adc_samples: int
    num_rx: int
    num_chirps_per_frame: int
    iq_swap: bool
    range_resolution_m: float
    range_bias_m: float
    anchor_utc_us: int
    time_origin_id: str
    time_origin_approximate: bool
    validity_provenance: str
    validity_assumed: bool
    source_kind: str
    _metadata_json: bytes

    @property
    def duration_s(self) -> float:
        return self.frame_count / self.frame_rate_hz

    @property
    def original_config(self) -> dict:
        return json.loads(self._original_config_json)

    @property
    def effective_config(self) -> dict:
        return json.loads(self._effective_config_json)

    @property
    def metadata(self) -> dict:
        return json.loads(self._metadata_json)


@dataclass(frozen=True)
class ReplayOptions:
    mode: Literal["baseline", "motion"]
    initial_speed: float
    frame_limit: int
    output_root: Path
    allow_legacy_validity_assumption: bool = False
    motion_config_path: Path | None = None


def duration_frame_limit(
    duration_s: float | None, *, total_frames: int, frame_rate_hz: float
) -> int:
    if duration_s is None:
        return int(total_frames)
    if type(duration_s) not in (int, float) or not math.isfinite(float(duration_s)):
        raise ValueError("duration_s must be a finite number")
    if float(duration_s) <= 0:
        raise ValueError("duration_s must be positive")
    frame_limit = math.floor(float(duration_s) * float(frame_rate_hz))
    if frame_limit < 1:
        raise ValueError("duration_s must include at least one recording frame")
    return min(int(total_frames), int(frame_limit))


def resolve_replay_session(
    capture_id: str,
    *,
    development_data_root: str | Path,
    mode: Literal["baseline", "motion"] = "baseline",
    allow_legacy_validity_assumption: bool = False,
) -> ReplaySessionSpec:
    """Resolve the one approved recording from committed radar-only identities."""

    if capture_id != SUPPORTED_CAPTURE_ID:
        raise ContractError(
            f"this player accepts only the registered capture {SUPPORTED_CAPTURE_ID!r}"
        )
    if mode not in {"baseline", "motion"}:
        raise ValueError("mode must be 'baseline' or 'motion'")
    if allow_legacy_validity_assumption and mode != "motion":
        raise ValueError("the legacy validity opt-in is only meaningful in motion mode")

    data_root = Path(development_data_root)
    _reject_protected_path(data_root)
    if ".." in data_root.parts:
        raise ContractError("development data root traversal is not permitted")
    try:
        data_root = data_root.resolve(strict=True)
    except OSError as exc:
        raise ContractError("development data root is unavailable") from exc
    if not data_root.is_dir():
        raise ContractError("development data root must be a directory")
    _reject_protected_path(data_root)

    # The registry file and its expected hashes always come from this source checkout.
    # Only the data root supplied to RadarScope changes.
    registry = load_registry(
        DEFAULT_REGISTRY, root=data_root / "results" / "live_demo"
    )
    radar = registry.radar_scope()
    registered = radar.capture(SUPPORTED_REGISTRY_KEY)
    if registered.directory != capture_id:
        raise ContractError("committed radar registry capture identity is incompatible")

    capture_dir = data_root / "results" / "live_demo" / capture_id
    adc_path = _exact_regular_file(capture_dir, "adc_stream.bin", data_root)
    metadata_path = _exact_regular_file(capture_dir, "run_metadata.json", data_root)
    warmup_path = _exact_regular_file(
        capture_dir, "warmup_bin_selection.json", data_root
    )

    # Recompute every committed identity.  Initial/final self-consistency alone would
    # accept a file substituted before replay began.
    try:
        metadata_content = metadata_path.read_bytes()
        warmup_content = warmup_path.read_bytes()
    except OSError as exc:
        raise ContractError("replay metadata/warmup input became unavailable") from exc
    metadata_hash = sha256_bytes(metadata_content)
    warmup_hash = sha256_bytes(warmup_content)
    expected_hashes = (
        (metadata_hash, registered.metadata_sha256, "metadata"),
        (warmup_hash, registered.warmup_sha256, "warmup"),
    )
    for actual, expected, label in expected_hashes:
        if actual != expected:
            raise ContractError(
                f"{label} SHA-256 {actual} differs from committed radar registry {expected}"
            )

    metadata = _read_json_object_bytes(metadata_content, "run metadata")
    warmup = _read_json_object_bytes(warmup_content, "warmup selection")
    original_config = metadata.get("config")
    if type(original_config) is not dict:
        raise ContractError("run metadata config must be a mapping")
    original_config = copy.deepcopy(original_config)
    config_hash = run_config_hash(original_config)
    if config_hash != registered.capture_config_sha256:
        raise ContractError(
            "embedded capture configuration differs from the committed radar registry"
        )

    geometry = radar.geometry
    profile = original_config.get("profile")
    session = original_config.get("session")
    if type(profile) is not dict or type(session) is not dict:
        raise ContractError("capture configuration lacks profile/session mappings")
    expected_geometry = {
        "num_adc_samples": int(geometry["adc_samples"]),
        "num_rx": int(geometry["rx"]),
        "num_chirps_per_frame": int(geometry["chirps_per_frame"]),
        "iq_swap": bool(geometry["iq_swap"]),
    }
    for name, expected in expected_geometry.items():
        if type(profile.get(name)) is not type(expected) or profile.get(name) != expected:
            raise ContractError(f"capture profile.{name} differs from radar registry")
    frame_rate_hz = _finite_number(session.get("frame_rate_hz"), "frame rate")
    if frame_rate_hz != float(geometry["frame_rate_hz"]):
        raise ContractError("capture frame rate differs from radar registry")
    range_resolution_m = _finite_number(
        profile.get("range_resolution_m"), "range resolution"
    )
    if range_resolution_m != float(geometry["range_resolution_m_approx"]):
        raise ContractError("capture range resolution differs from radar registry")
    if metadata.get("iq_swap") is not expected_geometry["iq_swap"]:
        raise ContractError("metadata IQ convention differs from radar registry")

    frame_bytes = (
        expected_geometry["num_adc_samples"]
        * expected_geometry["num_rx"]
        * expected_geometry["num_chirps_per_frame"]
        * 4
    )
    if frame_bytes != int(geometry["bytes_per_frame"]):
        raise ContractError("radar registry bytes-per-frame is internally inconsistent")
    file_size = adc_path.stat().st_size
    if file_size != registered.frames * frame_bytes:
        raise ContractError("ADC file does not contain the registered complete frames")

    metadata_lock = metadata.get("locked_bin")
    warmup_lock = warmup.get("selected_bin", warmup.get("selected_locked_bin"))
    if not (
        type(metadata_lock) is int
        and type(warmup_lock) is int
        and metadata_lock == warmup_lock == registered.recorded_lock
    ):
        raise ContractError("metadata/warmup/registry recorded-bin identities disagree")

    explicit_bias = profile.get("range_bias_m")
    if explicit_bias is not None and (
        type(explicit_bias) not in (int, float) or float(explicit_bias) != 0.0
    ):
        raise ContractError("historical replay requires zero range bias")
    phase = original_config.get("phase")
    if type(phase) is not dict:
        raise ContractError("capture configuration lacks a phase mapping")
    explicit_clutter = phase.get("clutter_removal")
    if explicit_clutter is not None and explicit_clutter != "none":
        raise ContractError("historical replay requires clutter_removal=none")
    effective_config = copy.deepcopy(original_config)
    effective_config["profile"].setdefault("range_bias_m", 0.0)
    effective_config["phase"].setdefault("clutter_removal", "none")
    effective_hash = run_config_hash(effective_config)
    anchor_us = _utc_microseconds(metadata.get("start_wall_utc"))

    validity_provenance = BASELINE_VALIDITY_ID
    validity_assumed = False
    if mode == "motion":
        if not allow_legacy_validity_assumption:
            raise ContractError(
                "advanced replay requires --allow-legacy-validity-assumption for "
                "this historical capture"
            )
        _validate_legacy_zero_loss(metadata, file_size, frame_bytes)
        validity_provenance = LEGACY_VALIDITY_ID
        validity_assumed = True

    return ReplaySessionSpec(
        capture_id=capture_id,
        capture_key=SUPPORTED_REGISTRY_KEY,
        capture_dir=capture_dir,
        adc_path=adc_path,
        metadata_path=metadata_path,
        warmup_path=warmup_path,
        registered_adc_sha256=registered.adc_stream_sha256,
        metadata_sha256=metadata_hash,
        warmup_sha256=warmup_hash,
        original_config_sha256=config_hash,
        effective_config_sha256=effective_hash,
        _original_config_json=_canonical_json_bytes(original_config),
        _effective_config_json=_canonical_json_bytes(effective_config),
        frame_count=registered.frames,
        frame_rate_hz=frame_rate_hz,
        frame_bytes=frame_bytes,
        num_adc_samples=expected_geometry["num_adc_samples"],
        num_rx=expected_geometry["num_rx"],
        num_chirps_per_frame=expected_geometry["num_chirps_per_frame"],
        iq_swap=expected_geometry["iq_swap"],
        range_resolution_m=range_resolution_m,
        range_bias_m=0.0,
        anchor_utc_us=anchor_us,
        time_origin_id="start_wall_utc_approximate_v1",
        time_origin_approximate=True,
        validity_provenance=validity_provenance,
        validity_assumed=validity_assumed,
        source_kind=SOURCE_KIND,
        _metadata_json=_canonical_json_bytes(metadata),
    )


def _exact_regular_file(capture_dir: Path, name: str, root: Path) -> Path:
    supplied = capture_dir / name
    _reject_protected_path(supplied)
    try:
        resolved = supplied.resolve(strict=True)
    except OSError as exc:
        raise ContractError(f"required replay input is unavailable: {supplied}") from exc
    expected_relative = Path("results") / "live_demo" / SUPPORTED_CAPTURE_ID / name
    if not resolved.is_relative_to(root):
        raise ContractError("replay input escapes the development data root")
    _reject_protected_path(resolved)
    if resolved.relative_to(root).parts != expected_relative.parts:
        raise ContractError("replay input path contains a substituted link")
    if not resolved.is_file():
        raise ContractError(f"replay input is not a regular file: {supplied}")
    return resolved


def _read_json_object_bytes(content: bytes, label: str) -> dict:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} is not valid UTF-8 JSON") from exc
    if type(value) is not dict:
        raise ContractError(f"{label} must be a JSON object")
    return value


def _canonical_json_bytes(value: dict) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _finite_number(value: object, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ContractError(f"{label} must be a finite number")
    return float(value)


def _utc_microseconds(value: object) -> int:
    if type(value) is not str:
        raise ContractError("start_wall_utc must be a timestamp string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ContractError("start_wall_utc is malformed") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError("start_wall_utc must include a UTC offset")
    parsed = parsed.astimezone(timezone.utc)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = parsed - epoch
    return (
        delta.days * 86_400_000_000
        + delta.seconds * 1_000_000
        + delta.microseconds
    )


def _validate_legacy_zero_loss(metadata: dict, raw_size: int, frame_bytes: int) -> None:
    if metadata.get("mode") != "live":
        raise ContractError("legacy validity requires completed live-capture metadata")
    if metadata.get("raw_stream_format") != "adc_bytes_no_packet_headers":
        raise ContractError("legacy validity requires the production ADC mirror format")
    if metadata.get("completion_status") != "completed":
        raise ContractError("legacy validity requires completed capture metadata")
    stats = metadata.get("live_packet_stats")
    if type(stats) is not dict:
        raise ContractError("legacy validity requires live packet statistics")
    received = stats.get("n_received")
    dropped = stats.get("n_dropped")
    zero_filled = stats.get("zero_filled_bytes")
    truncated = stats.get("mirror_truncated_bytes")
    if type(received) is not int or received <= 0:
        raise ContractError("legacy validity requires a positive packet count")
    if type(dropped) is not int or dropped != 0:
        raise ContractError("legacy validity requires an explicit zero dropped-packet count")
    if type(zero_filled) is not int or zero_filled != 0:
        raise ContractError("legacy validity requires explicit zero-filled bytes = 0")
    if type(truncated) is not int or not 0 <= truncated < frame_bytes:
        raise ContractError("legacy terminal truncation must be less than one frame")
    if received * PAYLOAD_BYTES_PER_PKT - truncated != raw_size:
        raise ContractError("legacy packet byte conservation check failed")
    for name in (
        "packets_short_discarded",
        "packets_duplicate_or_late_discarded",
        "queue_overflow_count",
    ):
        value = stats.get(name)
        if value is not None and (type(value) is not int or value != 0):
            raise ContractError(f"legacy diagnostic {name} proves or may indicate loss")


def _reject_protected_path(path: Path) -> None:
    parts = tuple(part.lower() for part in path.parts)
    joined = "/".join(parts)
    if "data/raw/prospective" in joined or "m2_capture_work" in parts:
        raise ContractError("prospective/sealed paths are forbidden in development replay")
    if any(_PROSPECTIVE_COMPONENT.search(part) for part in path.parts):
        raise ContractError("P001-P015 paths are forbidden in development replay")
