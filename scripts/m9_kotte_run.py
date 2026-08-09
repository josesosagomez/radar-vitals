r"""Radar-only M9.3 runner for the fixed Kotte project adaptation.

This module reads only the M9 estimator and radar-evaluation config sections. It performs
no accuracy evaluation. The command is deliberately opt-in because the implementation and
fixture tests must pass independent review before the fixed capture cohort is opened.

Documented command after that review::

    C:\ProgramData\anaconda3\condabin\conda.bat run -n radar-vitals python \
        scripts/m9_kotte_run.py --execute-radar-only
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import Callable, Mapping, Sequence
import zipfile

import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.m9.kotte_core import (  # noqa: E402
    KotteJointDopplerConfig,
    canonical_hash,
    estimate_window,
    extract_rx_slow_time,
    validate_project_frame_continuity,
)
from src import warmup_select as warmup_select_module  # noqa: E402

# These names intentionally refer to the unchanged shared production callables. Tests pin
# their object identity so this runner cannot drift into a private range selector.
derive_candidate_bins = warmup_select_module.derive_candidate_bins
run_warmup_selection = warmup_select_module.run_warmup_selection

CONFIG_PATH = REPO_ROOT / "experiments" / "m9_kotte" / "config.yaml"
OUTPUT_ROOT = REPO_ROOT / "results" / "m9_kotte_radar"
BYTES_PER_PRODUCTION_FRAME = 32 * 4 * 256 * 4
PAPER_PDF_PATH = REPO_ROOT / (
    "literature/ref_papers/joint_estimation_high_amplitude_doppler/"
    "Joint_Estimation_of_Single_Targets_High_Amplitude_Difference_Doppler_"
    "Frequencies_in_FMCW_Radar.pdf"
)
CANONICAL_PAPER_PDF_SHA256 = (
    "4d2c99f9c1a6fd71e4100fba610e0297cc1a54a0ef6a7558c37a6f0135fc1ddd"
)
DOCUMENTED_INVOCATION = (
    r"C:\ProgramData\anaconda3\condabin\conda.bat run -n radar-vitals python "
    r"scripts/m9_kotte_run.py --execute-radar-only"
)

CANONICAL_CAPTURE_DIRECTORIES = {
    "massimo1": "results/live_demo/20260713_172042_live_demo_massimo1",
    "massimo2": "results/live_demo/20260713_182002_live_demo_massimo2",
    "massimo3": "results/live_demo/20260728_224902_live_demo_massimo3",
    "sweep": "results/live_demo/20260714_180523_live_demo_sweep",
    "massimo4": "results/live_demo/20260728_230903_live_demo_massimo4",
    "massimo5": "results/live_demo/20260728_232415_live_demo_massimo5",
    "massimo6": "results/live_demo/20260729_002158_live_demo_massimo6",
    "massimo7": "results/live_demo/20260729_004815_live_demo_massimo7",
}

OFFICIAL_REVIEWED_PATHS = (
    "experiments/m9_kotte/config.yaml",
    "plans/m9_kotte_plan.md",
    "scripts/live_demo_config.yaml",
    "scripts/m9_kotte_run.py",
    "src/m9/kotte_core.py",
    "src/radar_io.py",
    "src/warmup_select.py",
    "src/window_pipeline.py",
    "tests/test_m9_kotte_core.py",
    "tests/test_m9_kotte_run.py",
    (
        "literature/ref_papers/joint_estimation_high_amplitude_doppler/"
        "Joint_Estimation_of_Single_Targets_High_Amplitude_Difference_Doppler_"
        "Frequencies_in_FMCW_Radar.pdf"
    ),
)


class CaptureInputError(Exception):
    """A capture cannot safely enter the estimator."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.context: dict[str, object] = {}


@dataclass(frozen=True)
class RadarGeometry:
    num_chirps_per_frame: int
    num_rx: int
    num_adc_samples: int
    num_tx: int
    frame_period_s: float
    iq_swap: bool

    @property
    def bytes_per_frame(self) -> int:
        return self.num_chirps_per_frame * self.num_rx * self.num_adc_samples * 4


@dataclass(frozen=True)
class CaptureSpec:
    capture_id: str
    directory: Path
    subject: str
    protocol_role: str
    data_role: str


@dataclass(frozen=True)
class ValidatedCapture:
    spec: CaptureSpec
    raw_path: Path
    metadata_path: Path
    metadata: dict
    metadata_sha256: str
    raw_sha256: str
    geometry: RadarGeometry
    num_frames: int
    num_windows: int
    mirror_truncated_bytes: int
    recorded_warmup_bin: int | None


def sha256_file(path: Path) -> str:
    """Streaming file digest; raw streams are never loaded merely to hash them."""
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def array_sha256(values: np.ndarray) -> str:
    array = np.asarray(values)
    digest = sha256()
    if array.flags.c_contiguous:
        digest.update(memoryview(array).cast("B"))
    else:
        digest.update(np.ascontiguousarray(array).tobytes())
    return digest.hexdigest()


def _json_plain(value):
    """Convert NumPy values and nonfinite diagnostics to strict, readable JSON."""
    if isinstance(value, np.ndarray):
        return _json_plain(value.tolist())
    if isinstance(value, np.generic):
        return _json_plain(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        if np.isnan(value):
            return "NaN"
        return "+Inf" if value > 0 else "-Inf"
    if isinstance(value, Mapping):
        return {str(key): _json_plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_plain(item) for item in value]
    return value


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(_json_plain(payload), indent=2, sort_keys=True, allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    temporary.replace(path)


def atomic_write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = sorted({key for row in rows for key in row})
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=path.parent, delete=False
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _json_plain(row.get(key, "")) for key in fieldnames})
        temporary = Path(handle.name)
    temporary.replace(path)


def atomic_write_npz(path: Path, arrays: Mapping[str, np.ndarray]) -> None:
    """Write a deterministic, pickle-free NPZ using fixed ZIP metadata."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, mode="w") as archive:
            for name in sorted(arrays):
                array = np.asarray(arrays[name])
                if array.dtype == object:
                    raise TypeError(f"evidence array {name!r} has forbidden object dtype")
                buffer = io.BytesIO()
                np.lib.format.write_array(buffer, array, allow_pickle=False)
                info = zipfile.ZipInfo(f"{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, buffer.getvalue())
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def select_runner_sections(document: Mapping[str, object]) -> tuple[dict, dict]:
    """Return only the estimator and radar-evaluation sections."""
    stage_a = document["stage_a"]
    radar_evaluation = document["radar_evaluation"]
    if not isinstance(stage_a, dict) or not isinstance(radar_evaluation, dict):
        raise TypeError("stage_a and radar_evaluation must be mappings")
    return stage_a, radar_evaluation


def load_runner_config(path: Path) -> tuple[dict, dict]:
    with path.open(encoding="utf-8") as handle:
        document = yaml.safe_load(handle)
    if not isinstance(document, dict):
        raise TypeError("M9 config must contain a YAML mapping")
    return select_runner_sections(document)


def assert_radar_section_reference_isolated(radar_evaluation: Mapping[str, object]) -> None:
    forbidden_key_fragments = ("reference", "ground_truth", "score")

    def visit(value: object, location: str) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                lowered = str(key).lower()
                if any(token in lowered for token in forbidden_key_fragments):
                    raise ValueError(f"forbidden radar-evaluation key at {location}.{key}")
                visit(item, f"{location}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{location}[{index}]")
        elif isinstance(value, str) and value.lower().endswith(".csv"):
            raise ValueError(f"forbidden table path in radar-evaluation at {location}")

    visit(radar_evaluation, "radar_evaluation")


def validate_manifest(
    radar_evaluation: Mapping[str, object],
    *,
    repo_root: Path,
    require_canonical: bool,
) -> tuple[CaptureSpec, ...]:
    assert_radar_section_reference_isolated(radar_evaluation)
    entries = radar_evaluation.get("captures")
    if not isinstance(entries, list) or not entries:
        raise ValueError("radar_evaluation.captures must be a nonempty list")
    capture_ids = [str(entry["capture_id"]) for entry in entries]
    if len(set(capture_ids)) != len(capture_ids):
        raise ValueError("capture IDs must be unique")
    if require_canonical:
        if set(capture_ids) != set(CANONICAL_CAPTURE_DIRECTORIES):
            raise ValueError("canonical M9 radar manifest must contain exactly eight fixed IDs")
        observed = {str(entry["capture_id"]): str(entry["directory"]) for entry in entries}
        if observed != CANONICAL_CAPTURE_DIRECTORIES:
            raise ValueError("canonical M9 radar manifest directories changed")

    root = repo_root.resolve()
    specs = []
    for entry in entries:
        directory = (root / str(entry["directory"])).resolve()
        if directory != root and root not in directory.parents:
            raise ValueError(f"capture directory escapes repository: {directory}")
        specs.append(
            CaptureSpec(
                capture_id=str(entry["capture_id"]),
                directory=directory,
                subject=str(entry["subject"]),
                protocol_role=str(entry["protocol_role"]),
                data_role=str(entry["data_role"]),
            )
        )
    return tuple(specs)


def validate_frozen_contract(
    stage_a: Mapping[str, object], radar_evaluation: Mapping[str, object]
) -> KotteJointDopplerConfig:
    config = KotteJointDopplerConfig.from_stage_a(stage_a)
    if config.grid_step_bpm != 0.5:
        raise ValueError("canonical M9 grid step must remain 0.5 bpm")
    expected_arms = {
        ("kotte_cpi_medoid_nc16_dl1em2", 16, "cpi_medoid", 1.0e-2),
        ("kotte_cpi_medoid_nc16_dl1em4", 16, "cpi_medoid", 1.0e-4),
    }
    observed_arms = {
        (arm.arm_id, arm.n_c, arm.estimator_form, arm.loading_delta) for arm in config.arms
    }
    if observed_arms != expected_arms:
        raise ValueError("canonical M9 must contain exactly the two frozen loading arms")
    range_selection = radar_evaluation["range_selection"]
    expected_range = {
        "source": "current_production_rerun_lock",
        "selector": "src.warmup_select.run_warmup_selection",
        "selector_config": "scripts/live_demo_config.yaml",
        "recorded_warmup_lock_role": "diagnostic_only_never_pooled",
        "kotte_all_bin_selection_allowed": False,
    }
    if range_selection != expected_range:
        raise ValueError("canonical range-selection contract changed")
    expected_window = {
        "window_frames": 600,
        "step_frames": 600,
        "interval": "half_open",
        "first_window_role": "lock_selection_in_sample",
        "comparative_windows_start_k": 1,
    }
    if radar_evaluation["window_grid"] != expected_window:
        raise ValueError("canonical radar window grid changed")
    return config


def _require_equal(actual: object, expected: object, reason: str, label: str) -> None:
    if actual != expected:
        raise CaptureInputError(reason, f"{label} must equal {expected!r}, got {actual!r}")


def _validate_capture_input_inner(
    spec: CaptureSpec,
    *,
    window_frames: int,
    raw_hasher: Callable[[Path], str],
    metadata_hasher: Callable[[Path], str],
    failure_context: dict[str, object],
) -> ValidatedCapture:
    raw_path = spec.directory / "adc_stream.bin"
    metadata_path = spec.directory / "run_metadata.json"
    try:
        raw_stat = raw_path.stat()
    except FileNotFoundError as exc:
        raise CaptureInputError("raw_missing", f"missing {raw_path}") from exc
    except OSError as exc:
        raise CaptureInputError("raw_stat_failed", str(exc)) from exc
    if not stat.S_ISREG(raw_stat.st_mode):
        raise CaptureInputError("raw_missing", f"not a regular file: {raw_path}")
    failure_context["raw_size_bytes"] = raw_stat.st_size
    failure_context["num_frames_floor"] = raw_stat.st_size // BYTES_PER_PRODUCTION_FRAME
    failure_context["num_windows_floor"] = (
        int(failure_context["num_frames_floor"]) // window_frames
    )

    try:
        metadata_stat = metadata_path.stat()
    except FileNotFoundError as exc:
        raise CaptureInputError("metadata_missing", f"missing {metadata_path}") from exc
    except OSError as exc:
        raise CaptureInputError("metadata_stat_failed", str(exc)) from exc
    if not stat.S_ISREG(metadata_stat.st_mode):
        raise CaptureInputError("metadata_missing", f"not a regular file: {metadata_path}")
    try:
        metadata_bytes = metadata_path.read_bytes()
    except OSError as exc:
        raise CaptureInputError("metadata_read_failed", str(exc)) from exc
    metadata_bytes_sha256 = sha256(metadata_bytes).hexdigest()
    try:
        metadata_sha256 = metadata_hasher(metadata_path)
    except OSError as exc:
        raise CaptureInputError("metadata_hash_failed", str(exc)) from exc
    if metadata_sha256 != metadata_bytes_sha256:
        raise CaptureInputError(
            "metadata_changed_during_validation",
            "run_metadata changed between its content read and streaming hash",
        )
    failure_context["metadata_sha256"] = metadata_sha256
    try:
        metadata = json.loads(metadata_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureInputError("metadata_unreadable", str(exc)) from exc
    if not isinstance(metadata, dict):
        raise CaptureInputError("metadata_not_mapping", "run_metadata JSON must be an object")

    _require_equal(metadata.get("completion_status"), "completed", "capture_incomplete", "completion_status")
    _require_equal(
        metadata.get("raw_stream_format"),
        "adc_bytes_no_packet_headers",
        "raw_stream_format",
        "raw_stream_format",
    )
    recorded_config = metadata.get("config")
    if not isinstance(recorded_config, dict):
        raise CaptureInputError("metadata_geometry", "metadata.config is missing")
    profile = recorded_config.get("profile")
    hw_profile = recorded_config.get("hw_profile")
    hw_frame = recorded_config.get("hw_frame")
    session = recorded_config.get("session")
    capture = recorded_config.get("capture")
    if not all(isinstance(item, dict) for item in (profile, hw_profile, hw_frame, session, capture)):
        raise CaptureInputError("metadata_geometry", "required config geometry sections missing")

    _require_equal(capture.get("raw_stream_format"), "adc_bytes_no_packet_headers", "raw_stream_format", "config.capture.raw_stream_format")
    _require_equal(profile.get("num_chirps_per_frame"), 32, "metadata_geometry", "num_chirps_per_frame")
    _require_equal(profile.get("num_rx"), 4, "metadata_geometry", "num_rx")
    _require_equal(profile.get("num_adc_samples"), 256, "metadata_geometry", "num_adc_samples")
    _require_equal(hw_frame.get("num_loops"), 32, "metadata_geometry", "hw_frame.num_loops")
    _require_equal(hw_frame.get("period_ms"), 50.0, "frame_timing", "hw_frame.period_ms")
    _require_equal(session.get("frame_rate_hz"), 20.0, "frame_timing", "session.frame_rate_hz")
    _require_equal(hw_profile.get("num_rx"), 4, "metadata_geometry", "hw_profile.num_rx")
    _require_equal(hw_profile.get("num_adc_samples"), 256, "metadata_geometry", "hw_profile.num_adc_samples")
    tx_mask = hw_profile.get("tx_channel_en")
    if type(tx_mask) is not int or tx_mask.bit_count() != 1:
        raise CaptureInputError("metadata_geometry", "exactly one TX must be enabled")
    iq_swap = metadata.get("iq_swap")
    if type(iq_swap) is not bool:
        raise CaptureInputError("metadata_iq_swap", "top-level iq_swap must be boolean")
    _require_equal(profile.get("iq_swap"), iq_swap, "metadata_iq_swap", "config.profile.iq_swap")

    packet_stats = metadata.get("live_packet_stats")
    if not isinstance(packet_stats, dict):
        raise CaptureInputError("packet_stats_missing", "live_packet_stats is missing")
    if type(packet_stats.get("n_dropped")) is not int:
        raise CaptureInputError("packet_drop", "n_dropped must be an integer")
    if type(packet_stats.get("zero_filled_bytes")) is not int:
        raise CaptureInputError("zero_fill", "zero_filled_bytes must be an integer")
    _require_equal(packet_stats.get("n_dropped"), 0, "packet_drop", "n_dropped")
    _require_equal(packet_stats.get("zero_filled_bytes"), 0, "zero_fill", "zero_filled_bytes")
    mirror_truncated = packet_stats.get("mirror_truncated_bytes")
    if type(mirror_truncated) is not int or mirror_truncated < 0:
        raise CaptureInputError("packet_stats_invalid", "mirror_truncated_bytes must be a nonnegative integer")

    geometry = RadarGeometry(32, 4, 256, 1, 0.05, iq_swap)
    file_size = raw_stat.st_size
    remainder = file_size % geometry.bytes_per_frame
    if remainder:
        raise CaptureInputError(
            "raw_size_not_frame_divisible",
            f"raw size has {remainder} trailing bytes that cannot form a decoded frame",
        )
    num_frames = file_size // geometry.bytes_per_frame
    if num_frames == 0:
        raise CaptureInputError("raw_empty", "raw stream contains no complete frame")
    recorded_bin = metadata.get("warmup_selected_bin")
    if recorded_bin is None and metadata.get("locked_bin_source") == "warmup_auto":
        recorded_bin = metadata.get("locked_bin")
    if recorded_bin is not None and (type(recorded_bin) is not int or not 0 <= recorded_bin < 256):
        raise CaptureInputError("recorded_lock_invalid", "recorded warmup bin is invalid")

    try:
        raw_sha256 = raw_hasher(raw_path)
    except OSError as exc:
        raise CaptureInputError("raw_hash_failed", str(exc)) from exc
    failure_context["raw_sha256"] = raw_sha256

    return ValidatedCapture(
        spec=spec,
        raw_path=raw_path,
        metadata_path=metadata_path,
        metadata=metadata,
        metadata_sha256=str(failure_context["metadata_sha256"]),
        raw_sha256=raw_sha256,
        geometry=geometry,
        num_frames=num_frames,
        num_windows=num_frames // window_frames,
        mirror_truncated_bytes=mirror_truncated,
        recorded_warmup_bin=recorded_bin,
    )


def validate_capture_input(
    spec: CaptureSpec,
    *,
    window_frames: int,
    raw_hasher: Callable[[Path], str] = sha256_file,
    metadata_hasher: Callable[[Path], str] = sha256_file,
) -> ValidatedCapture:
    """Validate one capture and attach safely acquired context to any rejection."""
    failure_context: dict[str, object] = {}
    try:
        return _validate_capture_input_inner(
            spec,
            window_frames=window_frames,
            raw_hasher=raw_hasher,
            metadata_hasher=metadata_hasher,
            failure_context=failure_context,
        )
    except CaptureInputError as exc:
        exc.context = dict(failure_context)
        raise


def decode_frame_window(
    path: Path,
    *,
    geometry: RadarGeometry,
    start_frame: int,
    frame_count: int,
) -> np.ndarray:
    """Decode one half-open frame window using radar_io's exact 2-lane word contract."""
    if type(start_frame) is not int or start_frame < 0:
        raise ValueError("start_frame must be a nonnegative integer")
    if type(frame_count) is not int or frame_count <= 0:
        raise ValueError("frame_count must be a positive integer")
    file_size = path.stat().st_size
    start_byte = start_frame * geometry.bytes_per_frame
    byte_count = frame_count * geometry.bytes_per_frame
    if start_byte + byte_count > file_size:
        raise ValueError("requested frame window extends beyond the complete raw stream")
    with path.open("rb") as handle:
        handle.seek(start_byte)
        raw_bytes = handle.read(byte_count)
    if len(raw_bytes) != byte_count:
        raise ValueError("short read while decoding frame window")
    raw = np.frombuffer(raw_bytes, dtype="<i2")
    if raw.size % 4:
        raise ValueError("raw word count does not contain complete 4-word packets")
    words = raw.reshape(-1, 4)
    complex_data = np.empty(raw.size // 2, dtype=np.complex64)
    if geometry.iq_swap:
        complex_data[0::2] = words[:, 2].astype(np.float32) + 1j * words[:, 0].astype(np.float32)
        complex_data[1::2] = words[:, 3].astype(np.float32) + 1j * words[:, 1].astype(np.float32)
    else:
        complex_data[0::2] = words[:, 0].astype(np.float32) + 1j * words[:, 2].astype(np.float32)
        complex_data[1::2] = words[:, 1].astype(np.float32) + 1j * words[:, 3].astype(np.float32)
    return complex_data.reshape(
        frame_count,
        geometry.num_chirps_per_frame,
        geometry.num_rx,
        geometry.num_adc_samples,
    )


def complete_window_count(num_frames: int, window_frames: int = 600) -> int:
    if type(num_frames) is not int or num_frames < 0:
        raise ValueError("num_frames must be a nonnegative integer")
    if type(window_frames) is not int or window_frames <= 0:
        raise ValueError("window_frames must be a positive integer")
    return num_frames // window_frames


def _git_text(*args: str, repo_root: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_root, text=True, capture_output=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _official_dirty_paths(repo_root: Path) -> list[str]:
    """Return dirty load-bearing M9 paths; unrelated thesis notes are out of scope."""
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--", *OFFICIAL_REVIEWED_PATHS],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("official run could not verify reviewed git paths are clean")
    return [line for line in result.stdout.splitlines() if line.strip()]


def _source_hashes(repo_root: Path, config_path: Path, live_config_path: Path) -> dict:
    paths = {
        "m9_config": config_path,
        "live_selector_config": live_config_path,
        "kotte_core": repo_root / "src" / "m9" / "kotte_core.py",
        "runner": Path(__file__).resolve(),
        "warmup_selector": repo_root / "src" / "warmup_select.py",
        "window_pipeline": repo_root / "src" / "window_pipeline.py",
        "radar_io": repo_root / "src" / "radar_io.py",
        "m9_plan": repo_root / "plans" / "m9_kotte_plan.md",
        "focused_core_tests": repo_root / "tests" / "test_m9_kotte_core.py",
        "runner_tests": repo_root / "tests" / "test_m9_kotte_run.py",
    }
    return {name: sha256_file(path) for name, path in paths.items()}


def _cube_identity_hash(validated: ValidatedCapture, start: int, end: int) -> str:
    return canonical_hash(
        {
            "raw_sha256": validated.raw_sha256,
            "frame_start": start,
            "frame_end": end,
            "iq_swap": validated.geometry.iq_swap,
            "shape": [end - start, 32, 4, 256],
        }
    )


def _failure_rows(
    *,
    validated: ValidatedCapture | None,
    spec: CaptureSpec,
    arms,
    window_indices: Sequence[int | None],
    reason: str,
    detail: str,
    common_hashes: Mapping[str, object],
    lock_binding: Mapping[str, object],
    failure_artifact: str,
    failure_artifact_sha256: str,
) -> list[dict]:
    rows = []
    for window_index in window_indices:
        frame_start = None if window_index is None else window_index * 600
        frame_end = None if window_index is None else (window_index + 1) * 600
        for arm in arms:
            rows.append(
                {
                    "capture_id": spec.capture_id,
                    "subject": spec.subject,
                    "protocol_role": spec.protocol_role,
                    "lock_estimand": "current_production_rerun_lock",
                    "window_index": window_index,
                    "frame_start": frame_start,
                    "frame_end": frame_end,
                    "window_fields_status": (
                        "unavailable_capture_input_failure"
                        if window_index is None
                        else "available"
                    ),
                    "arm_id": arm.arm_id,
                    "loading_delta": arm.loading_delta,
                    "valid": False,
                    "failure_reason": reason,
                    "failure_detail": detail,
                    "evidence_artifact": failure_artifact,
                    "evidence_sha256": failure_artifact_sha256,
                    "raw_sha256": validated.raw_sha256 if validated else "unavailable",
                    "metadata_sha256": validated.metadata_sha256 if validated else "unavailable",
                    **lock_binding,
                    **common_hashes,
                }
            )
    return rows


def _validated_output_directory(output_root: Path, run_id: str) -> Path:
    """Return a direct child output path for one safe, portable run identifier."""
    if type(run_id) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id) is None:
        raise ValueError("run_id must be one safe filename component")
    if run_id in {".", ".."}:
        raise ValueError("run_id cannot be '.' or '..'")
    resolved_root = output_root.resolve()
    resolved_output = (resolved_root / run_id).resolve()
    if resolved_output.parent != resolved_root:
        raise ValueError("run_id output must be a direct child of output_root")
    return resolved_output


def run_radar_only(
    *,
    config_path: Path = CONFIG_PATH,
    output_root: Path = OUTPUT_ROOT,
    repo_root: Path = REPO_ROOT,
    paper_pdf_path: Path = PAPER_PDF_PATH,
    official: bool = True,
    decoder: Callable[..., np.ndarray] = decode_frame_window,
    selector: Callable = run_warmup_selection,
    candidate_deriver: Callable[[dict], list[int]] = derive_candidate_bins,
    slow_time_adapter: Callable[..., np.ndarray] = extract_rx_slow_time,
    window_estimator: Callable[..., tuple[dict, dict]] = estimate_window,
    raw_hasher: Callable[[Path], str] = sha256_file,
    source_hashes_override: Mapping[str, str] | None = None,
    run_id: str | None = None,
    created_utc: str | None = None,
) -> Path:
    """Execute radar-only estimation; injectable boundaries exist solely for fixtures."""
    verified_official_pdf_sha256: str | None = None
    if official:
        if config_path.resolve() != CONFIG_PATH.resolve():
            raise ValueError("official run must use the canonical M9 config path")
        if output_root.resolve() != OUTPUT_ROOT.resolve():
            raise ValueError("official run must use the canonical M9 radar output root")
        if repo_root.resolve() != REPO_ROOT.resolve():
            raise ValueError("official run must use the canonical repository root")
        if paper_pdf_path.resolve() != PAPER_PDF_PATH.resolve():
            raise ValueError("official run must use the canonical original Kotte PDF")
        if run_id is not None or created_utc is not None:
            raise ValueError("official run IDs and UTC timestamps are generated internally")
        if (
            selector is not run_warmup_selection
            or candidate_deriver is not derive_candidate_bins
        ):
            raise ValueError("canonical run must use the unchanged shared warmup selector")
        if (
            slow_time_adapter is not extract_rx_slow_time
            or window_estimator is not estimate_window
        ):
            raise ValueError("canonical run must use the audited M9 project adapter and estimator")
        if decoder is not decode_frame_window or raw_hasher is not sha256_file:
            raise ValueError("canonical run must use the audited decoder and streaming raw hash")
        if source_hashes_override is not None:
            raise ValueError("source-hash overrides are fixture-only")
        dirty_paths = _official_dirty_paths(repo_root)
        if dirty_paths:
            raise ValueError(
                "official run requires committed reviewed M9 paths; dirty: "
                + "; ".join(dirty_paths)
            )
        try:
            verified_official_pdf_sha256 = sha256_file(paper_pdf_path)
        except OSError as exc:
            raise ValueError(f"official Kotte PDF could not be hashed: {exc}") from exc
        if verified_official_pdf_sha256 != CANONICAL_PAPER_PDF_SHA256:
            raise ValueError("official Kotte PDF hash differs from the reviewed original")
    stage_a, radar_evaluation = load_runner_config(config_path)
    if official:
        estimator_config = validate_frozen_contract(stage_a, radar_evaluation)
    else:
        estimator_config = KotteJointDopplerConfig.from_stage_a(stage_a)
    specs = validate_manifest(
        radar_evaluation,
        repo_root=repo_root,
        require_canonical=official,
    )
    window_frames = int(radar_evaluation["window_grid"]["window_frames"])
    if window_frames != 600:
        raise ValueError("M9 Kotte windows must contain exactly 600 frames")
    live_config_path = (repo_root / radar_evaluation["range_selection"]["selector_config"]).resolve()
    with live_config_path.open(encoding="utf-8") as handle:
        live_config = yaml.safe_load(handle)
    if not isinstance(live_config, dict):
        raise TypeError("live selector config must be a mapping")

    config_sha256 = sha256_file(config_path)
    paper_pdf_sha256 = verified_official_pdf_sha256 or sha256_file(paper_pdf_path)
    stage_a_hash = canonical_hash(stage_a)
    radar_evaluation_hash = canonical_hash(radar_evaluation)
    source_hashes = (
        dict(source_hashes_override)
        if source_hashes_override is not None
        else _source_hashes(repo_root, config_path, live_config_path)
    )
    required_source_hashes = {
        "kotte_core",
        "runner",
        "warmup_selector",
        "window_pipeline",
        "radar_io",
        "live_selector_config",
        "m9_config",
        "m9_plan",
        "focused_core_tests",
        "runner_tests",
    }
    missing_source_hashes = required_source_hashes - source_hashes.keys()
    if missing_source_hashes:
        raise ValueError(
            "source hash map is incomplete: " + ", ".join(sorted(missing_source_hashes))
        )
    timestamp = created_utc or datetime.now(timezone.utc).isoformat()
    git_commit = _git_text("rev-parse", "HEAD", repo_root=repo_root)
    git_status_lines = _git_text("status", "--short", repo_root=repo_root).splitlines()
    git_status_sha256 = canonical_hash(git_status_lines)
    resolved_run_id = run_id or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        + f"_{config_sha256[:12]}_radar_only_unscored"
    )
    out_dir = _validated_output_directory(output_root, resolved_run_id)
    out_dir.mkdir(parents=True, exist_ok=False)
    # The full config is hashed above, but the radar artifact deliberately snapshots only
    # the two sections it is authorized to consume.  M9.4 reference paths must not leak
    # into an unscored radar-only artifact.
    atomic_write_json(
        out_dir / "radar_runner_config.json",
        {
            "stage_a": stage_a,
            "radar_evaluation": radar_evaluation,
        },
    )

    common_hashes = {
        "config_sha256": config_sha256,
        "stage_a_sha256": stage_a_hash,
        "radar_evaluation_sha256": radar_evaluation_hash,
        "core_sha256": source_hashes["kotte_core"],
        "runner_sha256": source_hashes["runner"],
        "selector_sha256": source_hashes["warmup_selector"],
        "selector_config_sha256": source_hashes["live_selector_config"],
        "decoder_sha256": source_hashes["radar_io"],
        "window_pipeline_sha256": source_hashes["window_pipeline"],
        "plan_sha256": source_hashes["m9_plan"],
        "focused_core_tests_sha256": source_hashes["focused_core_tests"],
        "runner_tests_sha256": source_hashes["runner_tests"],
        "paper_pdf_sha256": paper_pdf_sha256,
        "git_commit": git_commit,
        "git_status_sha256": git_status_sha256,
    }
    pre_lock_binding = {
        "lock_estimand_id": "current_production_rerun_lock",
        "lock_source": "src.warmup_select.run_warmup_selection",
        "locked_bin": None,
        "lock_evidence": None,
        "lock_evidence_sha256": None,
        "lock_binding_status": "unavailable_phase_before_lock",
    }
    rows: list[dict] = []
    lock_map: list[dict] = []
    capture_failures: list[dict] = []
    evidence_manifest: list[dict] = []
    capture_inputs: list[dict] = []
    decode_events: list[dict] = []

    for spec in specs:
        try:
            validated = validate_capture_input(
                spec, window_frames=window_frames, raw_hasher=raw_hasher
            )
        except CaptureInputError as exc:
            failure_binding = dict(common_hashes)
            # Never retry stat/read/hash operations during recovery: a disappearing or
            # permission-denied input must not abort continuation to the next capture.
            failure_binding["raw_sha256"] = exc.context.get("raw_sha256", "unavailable")
            failure_binding["metadata_sha256"] = exc.context.get(
                "metadata_sha256", "unavailable"
            )
            failure_payload = {
                "capture_id": spec.capture_id,
                "reason": exc.reason,
                "detail": str(exc),
                "capture_stopped": True,
                "alternate_bin_rescue_attempted": False,
                "raw_sha256": failure_binding.get("raw_sha256", "unavailable"),
                "metadata_sha256": failure_binding.get("metadata_sha256", "unavailable"),
                "input_context": exc.context,
                **pre_lock_binding,
                **failure_binding,
            }
            failure_path = out_dir / "failures" / f"{spec.capture_id}.json"
            atomic_write_json(failure_path, failure_payload)
            failure_rel = failure_path.relative_to(out_dir).as_posix()
            failure_hash = sha256_file(failure_path)
            capture_failures.append({**failure_payload, "artifact": failure_rel, "sha256": failure_hash})
            estimated_windows = int(exc.context.get("num_windows_floor", 0))
            failure_window_indices: Sequence[int | None] = (
                range(estimated_windows) if estimated_windows > 0 else [None]
            )
            rows.extend(
                _failure_rows(
                    validated=None,
                    spec=spec,
                    arms=estimator_config.arms,
                    window_indices=failure_window_indices,
                    reason=exc.reason,
                    detail=str(exc),
                    common_hashes=failure_binding,
                    lock_binding=pre_lock_binding,
                    failure_artifact=failure_rel,
                    failure_artifact_sha256=failure_hash,
                )
            )
            continue

        capture_inputs.append(
            {
                "capture_id": spec.capture_id,
                "raw_sha256": validated.raw_sha256,
                "metadata_sha256": validated.metadata_sha256,
                "num_frames": validated.num_frames,
                "num_complete_windows": validated.num_windows,
                "partial_tail_frames_ignored": validated.num_frames % window_frames,
                "mirror_truncated_bytes": validated.mirror_truncated_bytes,
            }
        )
        if validated.num_windows == 0:
            reason = "no_complete_window"
            detail = "capture contains fewer than 600 complete frames"
            failure_payload = {
                "capture_id": spec.capture_id,
                "reason": reason,
                "detail": detail,
                "capture_stopped": True,
                "alternate_bin_rescue_attempted": False,
                "raw_sha256": validated.raw_sha256,
                "metadata_sha256": validated.metadata_sha256,
                **pre_lock_binding,
                **common_hashes,
            }
            failure_path = out_dir / "failures" / f"{spec.capture_id}.json"
            atomic_write_json(failure_path, failure_payload)
            failure_rel = failure_path.relative_to(out_dir).as_posix()
            failure_hash = sha256_file(failure_path)
            capture_failures.append(
                {**failure_payload, "artifact": failure_rel, "sha256": failure_hash}
            )
            rows.extend(
                _failure_rows(
                    validated=validated,
                    spec=spec,
                    arms=estimator_config.arms,
                    window_indices=[None],
                    reason=reason,
                    detail=detail,
                    common_hashes=common_hashes,
                    lock_binding=pre_lock_binding,
                    failure_artifact=failure_rel,
                    failure_artifact_sha256=failure_hash,
                )
            )
            continue

        try:
            decode_events.append(
                {
                    "capture_id": spec.capture_id,
                    "frame_start": 0,
                    "frame_count": window_frames,
                    "purpose": "k0_shared_warmup_and_estimation",
                }
            )
            cube_zero = decoder(
                validated.raw_path,
                geometry=validated.geometry,
                start_frame=0,
                frame_count=window_frames,
            )
            if cube_zero.shape != (600, 32, 4, 256):
                raise ValueError(f"decoder returned shape {cube_zero.shape}")
            if not np.issubdtype(cube_zero.dtype, np.complexfloating):
                raise TypeError(f"decoder returned noncomplex dtype {cube_zero.dtype}")
            if not np.all(np.isfinite(cube_zero)):
                raise ValueError("decoder returned NaN or Inf")
            candidate_bins = candidate_deriver(live_config)
            selected_bin, _winning_dsp, selector_evidence = selector(
                cube_zero, candidate_bins, live_config, fs=20.0
            )
            if type(selected_bin) is not int or not 0 <= selected_bin < 256:
                raise ValueError(f"warmup selector returned invalid bin {selected_bin!r}")
        except Exception as exc:
            reason = "warmup_selection_failed"
            failure_path = out_dir / "failures" / f"{spec.capture_id}_warmup.json"
            atomic_write_json(
                failure_path,
                {
                    "capture_id": spec.capture_id,
                    "reason": reason,
                    "detail": str(exc),
                    "capture_stopped": True,
                    "alternate_bin_rescue_attempted": False,
                    "raw_sha256": validated.raw_sha256,
                    "metadata_sha256": validated.metadata_sha256,
                    **pre_lock_binding,
                    **common_hashes,
                },
            )
            failure_rel = failure_path.relative_to(out_dir).as_posix()
            failure_hash = sha256_file(failure_path)
            rows.extend(
                _failure_rows(
                    validated=validated,
                    spec=spec,
                    arms=estimator_config.arms,
                    window_indices=range(validated.num_windows),
                    reason=reason,
                    detail=str(exc),
                    common_hashes=common_hashes,
                    lock_binding=pre_lock_binding,
                    failure_artifact=failure_rel,
                    failure_artifact_sha256=failure_hash,
                )
            )
            capture_failures.append(
                {
                    "capture_id": spec.capture_id,
                    "reason": reason,
                    "detail": str(exc),
                    "artifact": failure_rel,
                    "sha256": failure_hash,
                    "raw_sha256": validated.raw_sha256,
                    "metadata_sha256": validated.metadata_sha256,
                    **pre_lock_binding,
                    **common_hashes,
                }
            )
            continue

        lock_payload = {
            "capture_id": spec.capture_id,
            "lock_estimand_id": "current_production_rerun_lock",
            "lock_source": "src.warmup_select.run_warmup_selection",
            "locked_bin": selected_bin,
            "lock_binding_status": "available",
            "canonical": {
                "lock_estimand": "current_production_rerun_lock",
                "selected_bin": selected_bin,
                "candidate_bins": candidate_bins,
                "window_index": 0,
                "selector_evidence": selector_evidence,
            },
            "diagnostic_recorded": {
                "lock_estimand": "recorded_warmup_lock_diagnostic_only",
                "selected_bin": validated.recorded_warmup_bin,
                "role": "diagnostic_only_never_pooled",
                "estimates_generated": False,
            },
            "raw_sha256": validated.raw_sha256,
            "metadata_sha256": validated.metadata_sha256,
            **common_hashes,
        }
        lock_path = out_dir / "locks" / f"{spec.capture_id}.json"
        atomic_write_json(lock_path, lock_payload)
        lock_rel = lock_path.relative_to(out_dir).as_posix()
        lock_hash = sha256_file(lock_path)
        post_lock_binding = {
            "lock_estimand_id": "current_production_rerun_lock",
            "lock_source": "src.warmup_select.run_warmup_selection",
            "locked_bin": selected_bin,
            "lock_evidence": lock_rel,
            "lock_evidence_sha256": lock_hash,
            "lock_binding_status": "available",
        }
        lock_map.append(
            {
                "capture_id": spec.capture_id,
                "lock_estimand_id": "current_production_rerun_lock",
                "lock_source": "src.warmup_select.run_warmup_selection",
                "lock_binding_status": "available",
                "current_production_rerun_lock": selected_bin,
                "recorded_warmup_lock_diagnostic_only": validated.recorded_warmup_bin,
                "recorded_and_rerun_are_separate": True,
                "lock_evidence": lock_rel,
                "lock_evidence_sha256": lock_hash,
            }
        )

        for window_index in range(validated.num_windows):
            frame_start = window_index * window_frames
            frame_end = frame_start + window_frames
            try:
                if window_index == 0:
                    cube = cube_zero
                else:
                    decode_events.append(
                        {
                            "capture_id": spec.capture_id,
                            "frame_start": frame_start,
                            "frame_count": window_frames,
                            "purpose": "estimation",
                        }
                    )
                    cube = decoder(
                        validated.raw_path,
                        geometry=validated.geometry,
                        start_frame=frame_start,
                        frame_count=window_frames,
                    )
                if cube.shape != (600, 32, 4, 256):
                    raise ValueError(f"decoder returned shape {cube.shape}")
                if not np.issubdtype(cube.dtype, np.complexfloating):
                    raise TypeError(f"decoder returned noncomplex dtype {cube.dtype}")
                if not np.all(np.isfinite(cube)):
                    raise ValueError("decoder returned NaN or Inf")
                validate_project_frame_continuity(
                    np.arange(600, dtype=np.int64),
                    frame_period_s=validated.geometry.frame_period_s,
                    metadata={
                        "frame_count": 600,
                        "num_chirps_per_frame": 32,
                        "num_rx": 4,
                        "num_adc_samples": 256,
                        "file_size_divisible": True,
                        "packet_drop_count": 0,
                    },
                )
                z = slow_time_adapter(cube, selected_bin, chirp_loop_index=0)
            except Exception as exc:
                reason = "window_decode_or_adapter_failed"
                failure_path = out_dir / "failures" / f"{spec.capture_id}_k{window_index:04d}.json"
                atomic_write_json(
                    failure_path,
                    {
                        "capture_id": spec.capture_id,
                        "window_index": window_index,
                        "reason": reason,
                        "detail": str(exc),
                        "capture_stopped": True,
                        "alternate_bin_rescue_attempted": False,
                        "raw_sha256": validated.raw_sha256,
                        "metadata_sha256": validated.metadata_sha256,
                        **post_lock_binding,
                        **common_hashes,
                    },
                )
                failure_rel = failure_path.relative_to(out_dir).as_posix()
                failure_hash = sha256_file(failure_path)
                rows.extend(
                    _failure_rows(
                        validated=validated,
                        spec=spec,
                        arms=estimator_config.arms,
                        window_indices=range(window_index, validated.num_windows),
                        reason=reason,
                        detail=str(exc),
                        common_hashes=common_hashes,
                        lock_binding=post_lock_binding,
                        failure_artifact=failure_rel,
                        failure_artifact_sha256=failure_hash,
                    )
                )
                capture_failures.append(
                    {
                        "capture_id": spec.capture_id,
                        "reason": reason,
                        "detail": str(exc),
                        "artifact": failure_rel,
                        "sha256": failure_hash,
                        "raw_sha256": validated.raw_sha256,
                        "metadata_sha256": validated.metadata_sha256,
                        **post_lock_binding,
                        **common_hashes,
                    }
                )
                break

            cube_identity = _cube_identity_hash(validated, frame_start, frame_end)
            z_hash = array_sha256(z)
            shared_path = out_dir / "evidence" / spec.capture_id / f"k{window_index:04d}_shared_z.npz"
            atomic_write_npz(
                shared_path,
                {
                    "z": z,
                    "capture_id": np.asarray(spec.capture_id),
                    "window_index": np.asarray(window_index, dtype=np.int64),
                    "frame_span": np.asarray([frame_start, frame_end], dtype=np.int64),
                    "cube_shape": np.asarray(cube.shape, dtype=np.int64),
                    "cube_dtype": np.asarray(str(cube.dtype)),
                    "cube_identity_sha256": np.asarray(cube_identity),
                    "z_shape": np.asarray(z.shape, dtype=np.int64),
                    "z_dtype": np.asarray(str(z.dtype)),
                    "z_sha256": np.asarray(z_hash),
                    "locked_bin": np.asarray(selected_bin, dtype=np.int64),
                    "lock_estimand": np.asarray("current_production_rerun_lock"),
                    "frame_period_s": np.asarray(0.05, dtype=np.float64),
                    "mirror_truncated_bytes": np.asarray(
                        validated.mirror_truncated_bytes, dtype=np.int64
                    ),
                    "raw_sha256": np.asarray(validated.raw_sha256),
                    "metadata_sha256": np.asarray(validated.metadata_sha256),
                    **{
                        key: np.asarray(value)
                        for key, value in post_lock_binding.items()
                    },
                    **{
                        key: np.asarray(value)
                        for key, value in common_hashes.items()
                    },
                },
            )
            shared_rel = shared_path.relative_to(out_dir).as_posix()
            shared_hash = sha256_file(shared_path)
            evidence_manifest.append({"artifact": shared_rel, "sha256": shared_hash, "role": "shared_z"})

            for arm in estimator_config.arms:
                try:
                    native, evidence = window_estimator(
                        z, config=estimator_config, arm=arm, fs_hz=20.0
                    )
                    failure_reason = str(native.get("rej_reason", ""))
                    valid = bool(native["br_valid"] and native["hr_valid"])
                except Exception as exc:
                    native = {"br_valid": False, "hr_valid": False}
                    evidence = {}
                    failure_reason = f"estimator_exception:{type(exc).__name__}:{exc}"
                    valid = False
                arm_path = out_dir / "evidence" / spec.capture_id / (
                    f"k{window_index:04d}_{arm.arm_id}.npz"
                )
                cpi_spans = np.column_stack(
                    (
                        frame_start + np.arange(37, dtype=np.int64) * 16,
                        frame_start + (np.arange(37, dtype=np.int64) + 1) * 16,
                    )
                )
                arrays = {}
                for key, value in evidence.items():
                    if key == "z":
                        continue
                    array = np.asarray(value)
                    if array.dtype == object:
                        raise TypeError(f"estimator evidence {key!r} has object dtype")
                    arrays[key] = array
                if "cpi_estimates_bpm" in arrays:
                    arrays["cpi_canonical_pairs_hz"] = (
                        np.asarray(arrays["cpi_estimates_bpm"], dtype=np.float64) / 60.0
                    )
                arrays.update(
                    {
                        "capture_id": np.asarray(spec.capture_id),
                        "arm_id": np.asarray(arm.arm_id),
                        "window_index": np.asarray(window_index, dtype=np.int64),
                        "frame_span": np.asarray([frame_start, frame_end], dtype=np.int64),
                        "cpi_frame_spans": cpi_spans,
                        "locked_bin": np.asarray(selected_bin, dtype=np.int64),
                        "lock_estimand": np.asarray("current_production_rerun_lock"),
                        "lock_evidence": np.asarray(lock_rel),
                        "lock_evidence_sha256": np.asarray(lock_hash),
                        "shared_z_artifact": np.asarray(shared_rel),
                        "shared_z_artifact_sha256": np.asarray(shared_hash),
                        "z_sha256": np.asarray(z_hash),
                        "cube_identity_sha256": np.asarray(cube_identity),
                        "raw_sha256": np.asarray(validated.raw_sha256),
                        "metadata_sha256": np.asarray(validated.metadata_sha256),
                        "config_sha256": np.asarray(config_sha256),
                        "stage_a_sha256": np.asarray(stage_a_hash),
                        "radar_evaluation_sha256": np.asarray(radar_evaluation_hash),
                        "loading_delta": np.asarray(arm.loading_delta, dtype=np.float64),
                        "valid": np.asarray(valid, dtype=np.bool_),
                        "failure_reason": np.asarray(failure_reason),
                        "selected_hz": np.asarray(
                            native.get("selected_hz", [np.nan, np.nan]), dtype=np.float64
                        ),
                        "selected_raw_signed_hz": np.asarray(
                            native.get("selected_raw_signed_hz", [np.nan, np.nan]),
                            dtype=np.float64,
                        ),
                        "medoid_cpi_index": np.asarray(
                            native.get("medoid_cpi_index", -1), dtype=np.int64
                        ),
                        "pair_margin_db": np.asarray(
                            native.get("pair_margin_db", np.nan), dtype=np.float64
                        ),
                        "n_cpis_valid": np.asarray(
                            native.get("n_cpis_valid", 0), dtype=np.int64
                        ),
                        **{
                            key: np.asarray(value)
                            for key, value in post_lock_binding.items()
                        },
                        **{
                            key: np.asarray(value)
                            for key, value in common_hashes.items()
                        },
                    }
                )
                atomic_write_npz(arm_path, arrays)
                arm_rel = arm_path.relative_to(out_dir).as_posix()
                arm_hash = sha256_file(arm_path)
                evidence_manifest.append({"artifact": arm_rel, "sha256": arm_hash, "role": "arm"})
                row = {
                    "capture_id": spec.capture_id,
                    "subject": spec.subject,
                    "protocol_role": spec.protocol_role,
                    "data_role": spec.data_role,
                    "lock_estimand": "current_production_rerun_lock",
                    "locked_bin": selected_bin,
                    "recorded_warmup_bin_diagnostic_only": validated.recorded_warmup_bin,
                    "window_index": window_index,
                    "window_role": "lock_selection_in_sample" if window_index == 0 else "comparative",
                    "frame_start": frame_start,
                    "frame_end": frame_end,
                    "arm_id": arm.arm_id,
                    "loading_delta": arm.loading_delta,
                    "valid": valid,
                    "breath_bpm": native.get("br_bpm"),
                    "heart_bpm": native.get("hr_raw"),
                    "failure_reason": failure_reason,
                    "selected_hz": native.get("selected_hz"),
                    "selected_raw_signed_hz": native.get("selected_raw_signed_hz"),
                    "medoid_cpi_index": native.get("medoid_cpi_index"),
                    "pair_margin_db": native.get("pair_margin_db"),
                    "n_cpis_valid": native.get("n_cpis_valid"),
                    "raw_sha256": validated.raw_sha256,
                    "metadata_sha256": validated.metadata_sha256,
                    "cube_identity_sha256": cube_identity,
                    "z_sha256": z_hash,
                    "lock_evidence": lock_rel,
                    "lock_evidence_sha256": lock_hash,
                    "shared_z_artifact": shared_rel,
                    "shared_z_artifact_sha256": shared_hash,
                    "evidence_artifact": arm_rel,
                    "evidence_sha256": arm_hash,
                    **post_lock_binding,
                    **common_hashes,
                }
                rows.append(row)

    estimates_json = out_dir / "estimates.json"
    estimates_csv = out_dir / "estimates.csv"
    lock_map_path = out_dir / "lock_map.json"
    failures_path = out_dir / "capture_failures.json"
    evidence_manifest_path = out_dir / "evidence_manifest.json"
    atomic_write_json(estimates_json, rows)
    atomic_write_csv(estimates_csv, rows)
    atomic_write_json(lock_map_path, lock_map)
    atomic_write_json(failures_path, capture_failures)
    atomic_write_json(evidence_manifest_path, evidence_manifest)

    actual_argv = [sys.executable, *sys.argv]
    run_meta = {
        "run_id": resolved_run_id,
        "created_utc": timestamp,
        "role": "radar_only_unscored",
        "actual_argv": actual_argv,
        "actual_invocation": subprocess.list2cmdline(actual_argv),
        "documented_invocation": DOCUMENTED_INVOCATION,
        "git_commit": git_commit,
        "git_dirty_paths": git_status_lines,
        "git_status_sha256": git_status_sha256,
        "source_hashes": source_hashes,
        "config_sha256": config_sha256,
        "stage_a_sha256": stage_a_hash,
        "radar_evaluation_sha256": radar_evaluation_hash,
        "paper_pdf_sha256": paper_pdf_sha256,
        "capture_inputs": capture_inputs,
        "decode_events": decode_events,
        "capture_failure_count": len(capture_failures),
        "estimate_row_count": len(rows),
    }
    run_meta_path = out_dir / "run_meta.json"
    atomic_write_json(run_meta_path, run_meta)
    handoff = {
        "role": "immutable_radar_only_handoff",
        "run_id": resolved_run_id,
        "artifacts": {
            "estimates_json": sha256_file(estimates_json),
            "estimates_csv": sha256_file(estimates_csv),
            "lock_map": sha256_file(lock_map_path),
            "capture_failures": sha256_file(failures_path),
            "evidence_manifest": sha256_file(evidence_manifest_path),
            "run_meta": sha256_file(run_meta_path),
        },
        "estimator_settings_mutable": False,
    }
    atomic_write_json(out_dir / "radar_only_handoff.json", handoff)
    return out_dir


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-radar-only", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute_radar_only:
        parser.error("real radar execution requires --execute-radar-only after review")
    out_dir = run_radar_only()
    print(f"radar-only artifact: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
