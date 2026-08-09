r"""Exploratory, reference-only scoring for the immutable M9 Kotte radar artifact.

This stage never runs a radar estimator or selects a range bin.  It verifies the complete
radar-only directory before and after scoring, applies the existing HR/BR reference gates on
the frozen half-open window grid, and labels every output ``exploratory_non_frozen``.

Official command after independent review::

    C:\ProgramData\anaconda3\condabin\conda.bat run -n radar-vitals python \
        scripts/m9_kotte_score.py --execute-scoring
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
import subprocess
import sys
import tempfile
from typing import Callable, Mapping, Sequence
import zipfile

import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src import masimo  # noqa: E402
from src.comparator import br_reference, hr_reference  # noqa: E402
from src.m4.window_grid import window_reference_span  # noqa: E402


CONFIG_PATH = REPO_ROOT / "experiments" / "m9_kotte" / "config.yaml"
RADAR_RUN_ID = "20260809T001318.369165Z_d01cbc2d8404_radar_only_unscored"
RADAR_INPUT = REPO_ROOT / "results" / "m9_kotte_radar" / RADAR_RUN_ID
OUTPUT_ROOT = REPO_ROOT / "results" / "m9_kotte_score"
ACCEPTED_RADAR_COMMIT = "3ac060eee57c1c31eb6d6635735a76e8bbc14483"
ACCEPTED_HANDOFF_SHA256 = "d1da83a39a6fbd48638ae671be4f4897c162cef1a9347061b0e91bb4e3a8c5ff"
ACCEPTED_RADAR_CONFIG_SHA256 = "81bb25145a4eebc51b7170d9e39717130a22126c6a4d7b033ad5f7c99d91e404"
CLASSIFICATION = "exploratory_non_frozen"
CANONICAL_ARMS = {
    "kotte_cpi_medoid_nc16_dl1em2": 1.0e-2,
    "kotte_cpi_medoid_nc16_dl1em4": 1.0e-4,
}
DOCUMENTED_INVOCATION = (
    r"C:\ProgramData\anaconda3\condabin\conda.bat run -n radar-vitals python "
    r"scripts/m9_kotte_score.py --execute-scoring"
)
OFFICIAL_REVIEWED_PATHS = (
    "experiments/m9_kotte/config.yaml",
    "plans/m9_kotte_plan.md",
    "scripts/m9_kotte_score.py",
    "src/comparator.py",
    "src/masimo.py",
    "src/m4/window_grid.py",
    "tests/test_m9_kotte_score.py",
)


class ScoreContractError(RuntimeError):
    """A scoring input or immutable-provenance contract was violated."""


@dataclass(frozen=True)
class DirectorySnapshot:
    digest_sha256: str
    files: tuple[dict, ...]

    @property
    def by_path(self) -> dict[str, dict]:
        return {str(item["path"]): item for item in self.files}


@dataclass(frozen=True)
class ScoreResult:
    output_dir: Path
    scored_rows: tuple[dict, ...]
    summaries: tuple[dict, ...]


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(encoded.encode("utf-8")).hexdigest()


def snapshot_directory(root: Path) -> DirectorySnapshot:
    """Streaming content snapshot over every file, ordered by relative POSIX path."""
    if not root.is_dir():
        raise ScoreContractError(f"radar artifact directory is missing: {root}")
    rows = []
    for path in sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: p.as_posix()):
        if path.is_symlink() or root.resolve() not in path.resolve().parents:
            raise ScoreContractError(f"radar artifact contains an unsafe file identity: {path}")
        relative = path.relative_to(root).as_posix()
        stat_before = path.stat()
        file_sha256 = sha256_file(path)
        stat_after = path.stat()
        if (stat_before.st_size, stat_before.st_mtime_ns) != (
            stat_after.st_size,
            stat_after.st_mtime_ns,
        ):
            raise ScoreContractError(f"radar artifact changed while hashing: {relative}")
        rows.append({"path": relative, "size_bytes": stat_after.st_size, "sha256": file_sha256})
    if not rows:
        raise ScoreContractError("radar artifact directory contains no files")
    return DirectorySnapshot(canonical_hash(rows), tuple(rows))


def snapshot_load_bearing_sources(paths: Mapping[str, Path]) -> dict[str, dict]:
    """Bind the exact regular files whose code/contracts govern this scorer run."""
    snapshot: dict[str, dict] = {}
    for name, declared_path in sorted(paths.items()):
        try:
            if declared_path.is_symlink():
                raise ScoreContractError(f"load-bearing source is a symlink: {name}")
            resolved = declared_path.resolve(strict=True)
            if not resolved.is_file():
                raise ScoreContractError(f"load-bearing source is not a regular file: {name}")
            stat_before = resolved.stat()
            file_sha256 = sha256_file(resolved)
            stat_after = resolved.stat()
        except ScoreContractError:
            raise
        except OSError as exc:
            raise ScoreContractError(
                f"load-bearing source is unavailable or unhashable: {name}: {exc}"
            ) from exc
        identity_before = (
            stat_before.st_dev,
            stat_before.st_ino,
            stat_before.st_size,
            stat_before.st_mtime_ns,
        )
        identity_after = (
            stat_after.st_dev,
            stat_after.st_ino,
            stat_after.st_size,
            stat_after.st_mtime_ns,
        )
        if identity_before != identity_after:
            raise ScoreContractError(f"load-bearing source changed while hashing: {name}")
        snapshot[name] = {
            "resolved_path": str(resolved),
            "device": int(stat_after.st_dev),
            "inode": int(stat_after.st_ino),
            "size_bytes": int(stat_after.st_size),
            "mtime_ns": int(stat_after.st_mtime_ns),
            "sha256": file_sha256,
        }
    return snapshot


def _json_plain(value):
    if isinstance(value, np.generic):
        return _json_plain(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
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


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
            writer.writerow({key: _json_plain(row.get(key)) for key in fieldnames})
        temporary = Path(handle.name)
    temporary.replace(path)


def atomic_write_npz(path: Path, arrays: Mapping[str, np.ndarray]) -> None:
    """Deterministic ZIP timestamps and no object arrays/pickle."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(temporary, mode="w") as archive:
            for name in sorted(arrays):
                array = np.asarray(arrays[name])
                if array.dtype == object:
                    raise TypeError(f"scoring evidence {name!r} has forbidden object dtype")
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


def _safe_relative_file(root: Path, relative: str, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ScoreContractError(f"{label} must be a nonempty relative path")
    resolved_root = root.resolve()
    resolved = (resolved_root / relative).resolve()
    if resolved_root not in resolved.parents:
        raise ScoreContractError(f"{label} escapes its declared root: {relative!r}")
    return resolved


def verify_radar_artifact(root: Path, snapshot: DirectorySnapshot) -> tuple[list[dict], dict, dict]:
    """Verify handoff, evidence manifest, row identities, and their recorded hashes."""
    files = snapshot.by_path

    def load_json(relative: str):
        if relative not in files:
            raise ScoreContractError(f"radar artifact is missing {relative}")
        try:
            return json.loads((root / relative).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ScoreContractError(f"radar artifact {relative} is unreadable: {exc}") from exc

    handoff = load_json("radar_only_handoff.json")
    if not isinstance(handoff, dict):
        raise ScoreContractError("radar handoff must be a mapping")
    if handoff.get("role") != "immutable_radar_only_handoff":
        raise ScoreContractError("radar handoff role is not immutable_radar_only_handoff")
    if handoff.get("estimator_settings_mutable") is not False:
        raise ScoreContractError("radar handoff permits estimator mutation")
    handoff_paths = {
        "capture_failures": "capture_failures.json",
        "estimates_csv": "estimates.csv",
        "estimates_json": "estimates.json",
        "evidence_manifest": "evidence_manifest.json",
        "lock_map": "lock_map.json",
        "run_meta": "run_meta.json",
    }
    handoff_artifacts = handoff.get("artifacts")
    if not isinstance(handoff_artifacts, dict) or set(handoff_artifacts) != set(handoff_paths):
        raise ScoreContractError("radar handoff artifact identity set changed")
    for key, relative in handoff_paths.items():
        expected = handoff_artifacts[key]
        if relative not in files or files[relative]["sha256"] != expected:
            raise ScoreContractError(f"radar handoff hash mismatch for {relative}")

    manifest = load_json("evidence_manifest.json")
    if not isinstance(manifest, list):
        raise ScoreContractError("radar evidence manifest must be a list")
    seen_artifacts: set[str] = set()
    for item in manifest:
        relative = item.get("artifact") if isinstance(item, dict) else None
        if relative in seen_artifacts:
            raise ScoreContractError(f"duplicate radar evidence identity: {relative!r}")
        seen_artifacts.add(relative)
        _safe_relative_file(root, relative, "radar evidence artifact")
        if relative not in files or files[relative]["sha256"] != item.get("sha256"):
            raise ScoreContractError(f"radar evidence hash mismatch: {relative}")

    estimates = load_json("estimates.json")
    run_meta = load_json("run_meta.json")
    radar_config = load_json("radar_runner_config.json")
    if not isinstance(estimates, list) or not all(isinstance(row, dict) for row in estimates):
        raise ScoreContractError("radar estimates must be a list of mappings")
    if not isinstance(run_meta, dict) or not isinstance(radar_config, dict):
        raise ScoreContractError("radar run metadata/config must be mappings")
    keys = []
    for row in estimates:
        key = (
            row.get("capture_id"),
            row.get("window_index"),
            row.get("arm_id"),
            row.get("lock_estimand_id"),
        )
        keys.append(key)
        for path_key, hash_key in (
            ("evidence_artifact", "evidence_sha256"),
            ("shared_z_artifact", "shared_z_artifact_sha256"),
            ("lock_evidence", "lock_evidence_sha256"),
        ):
            relative = row.get(path_key)
            expected_hash = row.get(hash_key)
            if relative is None and expected_hash is None:
                continue
            _safe_relative_file(root, relative, path_key)
            if relative not in files or files[relative]["sha256"] != expected_hash:
                raise ScoreContractError(f"radar row hash mismatch for {path_key}: {relative}")
    if len(keys) != len(set(keys)):
        raise ScoreContractError("duplicate radar estimate key")
    expected_files = {
        "capture_failures.json", "estimates.csv", "estimates.json",
        "evidence_manifest.json", "lock_map.json", "radar_only_handoff.json",
        "radar_runner_config.json", "run_meta.json",
    }
    expected_files.update(seen_artifacts)
    expected_files.update(
        str(row["lock_evidence"]) for row in estimates if row.get("lock_evidence") is not None
    )
    unexpected = set(files) - expected_files
    missing = expected_files - set(files)
    if unexpected or missing:
        raise ScoreContractError(
            f"radar artifact file identity mismatch; unexpected={sorted(unexpected)}, "
            f"missing={sorted(missing)}"
        )
    return estimates, run_meta, radar_config


def validate_scoring_config(scoring: Mapping[str, object]) -> None:
    expected = {
        "estimator_settings_mutable_after_scoring": False,
        "outcome_threshold": "none",
        "timestamp_column": "Timestamp",
        "heart_reference_column": "Beats / min",
        "breath_reference_column": "Breaths / min",
        "window_interval": "half_open",
        "comparative_windows_start_k": 1,
        "reuse_existing_pi_and_stationarity_gates": True,
        "metrics": ["coverage", "mae_bpm", "rmse_bpm", "bias_bpm", "bland_altman_limits"],
        "summaries": ["capture", "subject", "protocol", "lock_estimand"],
        "constant_session_median_hr_comparator": True,
        "approximate_origin_label": CLASSIFICATION,
        "promotion_eligible": False,
    }
    for key, value in expected.items():
        if scoring.get(key) != value:
            raise ScoreContractError(f"scoring config changed at {key}: {scoring.get(key)!r}")


def reference_map(
    scoring: Mapping[str, object], capture_ids: set[str], repo_root: Path
) -> dict[str, Path]:
    entries = scoring.get("capture_references")
    if not isinstance(entries, list):
        raise ScoreContractError("capture_references must be a list")
    mapping: dict[str, Path] = {}
    root = repo_root.resolve()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ScoreContractError("capture reference entry must be a mapping")
        capture_id = entry.get("capture_id")
        if capture_id in mapping:
            raise ScoreContractError(f"duplicate reference identity for {capture_id!r}")
        relative = entry.get("csv")
        if not isinstance(capture_id, str) or not isinstance(relative, str):
            raise ScoreContractError("capture reference identity/path is malformed")
        path = (root / relative).resolve()
        if root not in path.parents:
            raise ScoreContractError(f"reference path escapes repository: {relative}")
        mapping[capture_id] = path
    if set(mapping) != capture_ids:
        raise ScoreContractError(
            f"reference identity mismatch: expected {sorted(capture_ids)}, got {sorted(mapping)}"
        )
    for capture_id, path in mapping.items():
        if not path.is_file():
            raise ScoreContractError(f"missing reference for {capture_id}: {path}")
    return mapping


REQUIRED_REFERENCE_COLUMNS = (
    "Session",
    "Index",
    "Timestamp",
    "Date",
    "Time",
    "O2 Saturation",
    "Beats / min",
    "Perfusion Index",
    "Pleth Variability",
    "Breaths / min",
)

NORMALIZED_REFERENCE_COLUMNS = (
    "epoch_utc", "pr_bpm", "spo2", "pi", "pvi", "rr_bpm",
)
REFERENCE_DIAGNOSTIC_KEYS = (
    "n_raw_rows", "n_unique_epochs", "n_duplicates_merged", "n_missing_seconds",
)


def _normalized_reference_diagnostics(reference: pd.DataFrame) -> dict[str, int]:
    """Validate the unchanged parser output, including its duplicate normalization counts."""
    if not isinstance(reference, pd.DataFrame):
        raise ScoreContractError("normalized reference must be a pandas DataFrame")
    missing = [column for column in NORMALIZED_REFERENCE_COLUMNS if column not in reference]
    if missing:
        raise ScoreContractError(f"normalized reference is missing columns: {missing}")
    timestamps = reference["epoch_utc"]
    if timestamps.isna().any() or not pd.api.types.is_integer_dtype(timestamps.dtype):
        raise ScoreContractError("Masimo parser did not preserve integer Timestamp")
    if timestamps.duplicated().any():
        raise ScoreContractError("normalized reference contains duplicate epoch_utc keys")
    if reference.empty:
        raise ScoreContractError("normalized reference contains no epochs")

    diagnostics: dict[str, int] = {}
    for key in REFERENCE_DIAGNOSTIC_KEYS:
        value = reference.attrs.get(key)
        if type(value) is not int or value < 0:
            raise ScoreContractError(f"normalized reference has invalid parser diagnostic {key}")
        diagnostics[key] = value
    if diagnostics["n_unique_epochs"] != len(reference):
        raise ScoreContractError("normalized reference unique-epoch count is inconsistent")
    if diagnostics["n_duplicates_merged"] != (
        diagnostics["n_raw_rows"] - diagnostics["n_unique_epochs"]
    ):
        raise ScoreContractError("normalized reference duplicate-merge count is inconsistent")
    expected_missing = int(timestamps.max() - timestamps.min() + 1 - len(reference))
    if diagnostics["n_missing_seconds"] != expected_missing:
        raise ScoreContractError("normalized reference missing-second count is inconsistent")
    return diagnostics


def load_reference_strict(path: Path) -> pd.DataFrame:
    """Validate raw identity, then apply and verify the unchanged Masimo normalization."""
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle))
    except (OSError, StopIteration, UnicodeError) as exc:
        raise ScoreContractError(f"reference is unreadable: {path}: {exc}") from exc
    if len(header) != len(set(header)):
        raise ScoreContractError(f"duplicate reference column identity: {path}")
    missing = [column for column in REQUIRED_REFERENCE_COLUMNS if header.count(column) != 1]
    if missing:
        raise ScoreContractError(f"reference missing exact columns {missing}: {path}")
    try:
        raw = pd.read_csv(path)
    except Exception as exc:
        raise ScoreContractError(f"reference CSV parse failed: {path}: {exc}") from exc
    timestamps = raw["Timestamp"]
    if not pd.api.types.is_integer_dtype(timestamps.dtype) or timestamps.isna().any():
        raise ScoreContractError("reference requires an integer Timestamp column")
    if raw.empty:
        raise ScoreContractError("reference contains no rows")
    try:
        parsed = masimo.load_masimo(path)
    except Exception as exc:
        raise ScoreContractError(f"Masimo reference contract failed: {path}: {exc}") from exc
    diagnostics = _normalized_reference_diagnostics(parsed)
    expected_raw_rows = len(raw)
    expected_unique_epochs = int(timestamps.nunique())
    expected_duplicates_merged = expected_raw_rows - expected_unique_epochs
    expected_missing_seconds = int(
        timestamps.max() - timestamps.min() + 1 - expected_unique_epochs
    )
    expected = {
        "n_raw_rows": expected_raw_rows,
        "n_unique_epochs": expected_unique_epochs,
        "n_duplicates_merged": expected_duplicates_merged,
        "n_missing_seconds": expected_missing_seconds,
    }
    if diagnostics != expected:
        raise ScoreContractError("Masimo parser diagnostics disagree with the raw reference")
    return parsed


def resolve_frame0_epoch(metadata: Mapping[str, object]) -> tuple[float, str, bool]:
    """Use the existing project precedence: first-packet epoch, else approximate wall start."""
    exact = metadata.get("frame0_epoch_utc")
    if exact is not None:
        value = float(exact)
        if not np.isfinite(value):
            raise ScoreContractError("frame0_epoch_utc must be finite")
        return value, str(metadata.get("frame0_epoch_source") or "frame0_epoch_utc"), False
    start = metadata.get("start_wall_utc")
    if not isinstance(start, str):
        raise ScoreContractError("capture metadata has no usable frame-zero origin")
    try:
        parsed = datetime.fromisoformat(start)
    except ValueError as exc:
        raise ScoreContractError(f"start_wall_utc is malformed: {start!r}") from exc
    if parsed.tzinfo is None:
        raise ScoreContractError("start_wall_utc must carry an explicit UTC offset")
    return parsed.timestamp(), "start_wall_utc_approximate", True


def metric_summary(errors: Sequence[float], *, n_total: int, n_radar_valid: int,
                   n_reference_admitted: int) -> dict:
    """Coverage and agreement metrics on one already-fixed paired cell set."""
    if (
        type(n_total) is not int or type(n_radar_valid) is not int
        or type(n_reference_admitted) is not int or n_total < 0
        or not 0 <= n_radar_valid <= n_total
        or not 0 <= n_reference_admitted <= n_total
    ):
        raise ScoreContractError("metric coverage counts are inconsistent")
    values = np.asarray(errors, dtype=np.float64)
    values = values[np.isfinite(values)]
    n_scored = int(values.size)
    if n_scored > min(n_total, n_radar_valid, n_reference_admitted):
        raise ScoreContractError("joint scored count exceeds its coverage inputs")
    bias = float(np.mean(values)) if n_scored else None
    if n_scored >= 2:
        standard_deviation = float(np.std(values, ddof=1))
        loa_low = float(bias - 1.96 * standard_deviation)
        loa_high = float(bias + 1.96 * standard_deviation)
    else:
        standard_deviation = loa_low = loa_high = None
    return {
        "n_windows": int(n_total),
        "n_radar_valid": int(n_radar_valid),
        "n_radar_invalid": int(n_total - n_radar_valid),
        "algorithmic_coverage": n_radar_valid / n_total if n_total else None,
        "n_reference_admitted": int(n_reference_admitted),
        "n_reference_excluded": int(n_total - n_reference_admitted),
        "reference_coverage": n_reference_admitted / n_total if n_total else None,
        "n_joint_scored": n_scored,
        "n_joint_unscored": int(n_total - n_scored),
        "joint_coverage": n_scored / n_total if n_total else None,
        "mae_bpm": float(np.mean(np.abs(values))) if n_scored else None,
        "rmse_bpm": float(np.sqrt(np.mean(values ** 2))) if n_scored else None,
        "bias_bpm": bias,
        "error_sd_bpm": standard_deviation,
        "bland_altman_loa_low_bpm": loa_low,
        "bland_altman_loa_high_bpm": loa_high,
    }


def _radar_rows_contract(rows: list[dict], radar_config: Mapping[str, object], official: bool) -> None:
    if not rows:
        raise ScoreContractError("radar estimates are empty")
    arms = {str(row.get("arm_id")): row.get("loading_delta") for row in rows}
    if official and arms != CANONICAL_ARMS:
        raise ScoreContractError(f"canonical radar arm identity changed: {arms}")
    for row in rows:
        k = row.get("window_index")
        if k is None and not row.get("valid"):
            if row.get("window_fields_status") != "unavailable_capture_input_failure":
                raise ScoreContractError("null radar window requires an explicit unavailable status")
            continue
        if type(k) is not int or k < 0:
            raise ScoreContractError("radar window_index must be a nonnegative integer")
        if row.get("frame_start") != k * 600 or row.get("frame_end") != (k + 1) * 600:
            raise ScoreContractError("radar frame span changed from the half-open 600-frame grid")
        expected_role = "lock_selection_in_sample" if k == 0 else "comparative"
        if row.get("window_role") != expected_role:
            raise ScoreContractError("radar window_role is inconsistent with k")
        if row.get("lock_estimand_id") != "current_production_rerun_lock":
            raise ScoreContractError("unexpected lock estimand in canonical radar rows")
    if official:
        unique_windows = {(row["capture_id"], row["window_index"]) for row in rows}
        if len(rows) != 256 or len(unique_windows) != 128:
            raise ScoreContractError("canonical radar universe must be 256 rows / 128 windows")
        if set(row["capture_id"] for row in rows) != {
            "massimo1", "massimo2", "massimo3", "sweep",
            "massimo4", "massimo5", "massimo6", "massimo7",
        }:
            raise ScoreContractError("canonical radar capture identity changed")


def _reference_for_row(reference: pd.DataFrame, vital: str, start: float, end: float) -> dict:
    if vital == "hr":
        result = hr_reference(reference, start, end)
        return {
            "reference_value_bpm": result["median_pr_bpm"],
            "reference_admitted": result["admitted"],
            "reference_n_total": result["n_total"],
            "reference_n_usable": result["n_usable"],
            "reference_spread_bpm": result["spread_bpm"],
            "reference_coverage_ok": result["coverage_ok"],
            "reference_stationarity_ok": result["stationarity_ok"],
            "reference_pi_median": None,
        }
    result = br_reference(reference, start, end)
    return {
        "reference_value_bpm": result["median_rr_bpm"],
        "reference_admitted": result["admitted"],
        "reference_n_total": result["n_total"],
        "reference_n_usable": result["n_finite_rr"],
        "reference_spread_bpm": result["spread_bpm"],
        "reference_coverage_ok": result["availability_ok"],
        "reference_stationarity_ok": result["stationarity_ok"],
        "reference_pi_median": result["pi_median"],
    }


def build_scored_rows(
    radar_rows: list[dict], references: Mapping[str, pd.DataFrame],
    origins: Mapping[str, tuple[float, str, bool]],
) -> list[dict]:
    rows: list[dict] = []
    by_capture: dict[str, list[dict]] = {}
    for radar in radar_rows:
        if radar.get("window_index") is None:
            continue
        by_capture.setdefault(str(radar["capture_id"]), []).append(radar)

    reference_cache: dict[tuple[str, int, str], dict] = {}
    for capture_id, capture_rows in sorted(by_capture.items()):
        frame0, origin_source, origin_approximate = origins[capture_id]
        for radar in sorted(capture_rows, key=lambda r: (r["window_index"], r["arm_id"])):
            k = int(radar["window_index"])
            epoch_start, epoch_end = window_reference_span(k, frame0)
            for vital, estimate_key in (("hr", "heart_bpm"), ("br", "breath_bpm")):
                cache_key = (capture_id, k, vital)
                if cache_key not in reference_cache:
                    reference_cache[cache_key] = _reference_for_row(
                        references[capture_id], vital, epoch_start, epoch_end
                    )
                ref = reference_cache[cache_key]
                estimate = radar.get(estimate_key)
                estimate_finite = isinstance(estimate, (int, float)) and np.isfinite(estimate)
                radar_valid = bool(radar.get("valid")) and estimate_finite
                reference_value = ref["reference_value_bpm"]
                joint = bool(
                    k >= 1 and radar_valid and ref["reference_admitted"]
                    and np.isfinite(reference_value)
                )
                error = float(estimate - reference_value) if joint else None
                rows.append(
                    {
                        "capture_id": capture_id,
                        "subject": radar["subject"],
                        "protocol": radar["protocol_role"],
                        "lock_estimand_id": radar["lock_estimand_id"],
                        "method": radar["arm_id"],
                        "arm_id": radar["arm_id"],
                        "loading_delta": radar["loading_delta"],
                        "vital": vital,
                        "window_index": k,
                        "window_role": radar["window_role"],
                        "window_universe": (
                            "lock_selection_in_sample_k0" if k == 0 else "evaluation_k_ge_1"
                        ),
                        "metric_eligible": k >= 1,
                        "epoch_start": epoch_start,
                        "epoch_end": epoch_end,
                        "origin_source": origin_source,
                        "origin_is_approximate": origin_approximate,
                        "estimate_bpm": float(estimate) if estimate_finite else None,
                        "radar_valid": radar_valid,
                        "radar_failure_reason": radar.get("failure_reason") or "valid",
                        "pair_margin_db": radar.get("pair_margin_db"),
                        **ref,
                        "joint_scored": joint,
                        "error_bpm": error,
                        "absolute_error_bpm": abs(error) if error is not None else None,
                        "classification": CLASSIFICATION,
                        "promotion_eligible": False,
                        "claim_eligible": False,
                        "taint_reason": (
                            "lock_selection_in_sample" if k == 0
                            else "approximate_origin_low_hr_dynamic_range"
                        ),
                    }
                )

        # The no-radar comparator is fixed from admitted k>=1 HR reference windows only.
        window_indices = sorted({int(row["window_index"]) for row in capture_rows if row["window_index"] >= 1})
        admitted_values = []
        for k in window_indices:
            ref = reference_cache.get((capture_id, k, "hr"))
            if ref is None:
                lo, hi = window_reference_span(k, frame0)
                ref = _reference_for_row(references[capture_id], "hr", lo, hi)
                reference_cache[(capture_id, k, "hr")] = ref
            if ref["reference_admitted"] and np.isfinite(ref["reference_value_bpm"]):
                admitted_values.append(float(ref["reference_value_bpm"]))
        session_median = float(np.median(admitted_values)) if admitted_values else None
        template = capture_rows[0]
        for k in window_indices:
            epoch_start, epoch_end = window_reference_span(k, frame0)
            ref = reference_cache[(capture_id, k, "hr")]
            joint = bool(
                session_median is not None and ref["reference_admitted"]
                and np.isfinite(ref["reference_value_bpm"])
            )
            error = session_median - ref["reference_value_bpm"] if joint else None
            rows.append(
                {
                    "capture_id": capture_id,
                    "subject": template["subject"],
                    "protocol": template["protocol_role"],
                    "lock_estimand_id": template["lock_estimand_id"],
                    "method": "constant_session_median",
                    "arm_id": None,
                    "loading_delta": None,
                    "vital": "hr",
                    "window_index": k,
                    "window_role": "comparative",
                    "window_universe": "evaluation_k_ge_1",
                    "metric_eligible": True,
                    "epoch_start": epoch_start,
                    "epoch_end": epoch_end,
                    "origin_source": origin_source,
                    "origin_is_approximate": origin_approximate,
                    "estimate_bpm": session_median,
                    "radar_valid": session_median is not None,
                    "radar_failure_reason": "not_applicable_no_radar_comparator",
                    "pair_margin_db": None,
                    **ref,
                    "joint_scored": joint,
                    "error_bpm": float(error) if error is not None else None,
                    "absolute_error_bpm": abs(float(error)) if error is not None else None,
                    "classification": CLASSIFICATION,
                    "promotion_eligible": False,
                    "claim_eligible": False,
                    "taint_reason": "approximate_origin_low_hr_dynamic_range",
                }
            )
    return rows


def aggregate_summaries(scored_rows: Sequence[dict]) -> list[dict]:
    comparative = [row for row in scored_rows if row["metric_eligible"]]
    levels = {
        "capture": ("capture_id", "subject", "protocol", "lock_estimand_id"),
        "subject": ("subject", "protocol", "lock_estimand_id"),
        "protocol": ("protocol", "lock_estimand_id"),
        # Protocol remains in this group: lock estimands and protocols are never pooled.
        "lock_estimand": ("lock_estimand_id", "protocol"),
    }
    summaries: list[dict] = []
    for level, stratum_fields in levels.items():
        groups: dict[tuple, list[dict]] = {}
        for row in comparative:
            key = tuple(row[field] for field in stratum_fields) + (
                row["method"], row["vital"], row["loading_delta"]
            )
            groups.setdefault(key, []).append(row)
        for key, cells in sorted(groups.items(), key=lambda item: tuple(str(x) for x in item[0])):
            stratum_values = key[: len(stratum_fields)]
            errors = [row["error_bpm"] for row in cells if row["joint_scored"]]
            summary = {
                "stratum_level": level,
                **dict(zip(stratum_fields, stratum_values)),
                "method": key[-3],
                "vital": key[-2],
                "loading_delta": key[-1],
                **metric_summary(
                    errors,
                    n_total=len(cells),
                    n_radar_valid=sum(bool(row["radar_valid"]) for row in cells),
                    n_reference_admitted=sum(bool(row["reference_admitted"]) for row in cells),
                ),
                "classification": CLASSIFICATION,
                "promotion_eligible": False,
                "claim_eligible": False,
            }
            summaries.append(summary)
    return summaries


def failure_census(radar_rows: Sequence[dict]) -> list[dict]:
    counts: dict[tuple, int] = {}
    for row in radar_rows:
        if row.get("window_index") == 0:
            continue
        reason = row.get("failure_reason") or "valid"
        key = (
            row["capture_id"], row["subject"], row["protocol_role"],
            row["lock_estimand_id"], row["arm_id"], reason,
        )
        counts[key] = counts.get(key, 0) + 1
    return [
        {
            "capture_id": key[0], "subject": key[1], "protocol": key[2],
            "lock_estimand_id": key[3], "arm_id": key[4], "failure_reason": key[5],
            "n_windows": count, "classification": CLASSIFICATION,
            "promotion_eligible": False,
        }
        for key, count in sorted(counts.items())
    ]


def margin_diagnostics(radar_rows: Sequence[dict]) -> list[dict]:
    groups: dict[tuple, list[float]] = {}
    for row in radar_rows:
        margin = row.get("pair_margin_db")
        if row["window_index"] < 1 or not row.get("valid") or not isinstance(margin, (int, float)):
            continue
        if np.isfinite(margin):
            key = (
                row["capture_id"], row["subject"], row["protocol_role"],
                row["lock_estimand_id"], row["arm_id"],
            )
            groups.setdefault(key, []).append(float(margin))
    output = []
    for key, values in sorted(groups.items()):
        array = np.asarray(values)
        output.append(
            {
                "capture_id": key[0], "subject": key[1], "protocol": key[2],
                "lock_estimand_id": key[3], "arm_id": key[4], "n_finite": len(values),
                "minimum_db": float(np.min(array)), "median_db": float(np.median(array)),
                "p10_db": float(np.percentile(array, 10, method="linear")),
                "p90_db": float(np.percentile(array, 90, method="linear")),
                "classification": CLASSIFICATION, "promotion_eligible": False,
            }
        )
    return output


def _git_text(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_root, text=True, capture_output=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _official_dirty_paths(repo_root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--", *OFFICIAL_REVIEWED_PATHS],
        cwd=repo_root, text=True, capture_output=True, check=False,
    )
    if result.returncode != 0:
        raise ScoreContractError("could not verify official scoring paths are clean")
    return [line for line in result.stdout.splitlines() if line.strip()]


def _validated_output_directory(output_root: Path, run_id: str) -> Path:
    if type(run_id) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", run_id) is None:
        raise ScoreContractError("run_id must be one safe filename component")
    root = output_root.resolve()
    output = (root / run_id).resolve()
    if output.parent != root:
        raise ScoreContractError("scoring output must be a direct child of output_root")
    return output


def execute_score(
    *,
    radar_input: Path = RADAR_INPUT,
    config_path: Path = CONFIG_PATH,
    output_root: Path = OUTPUT_ROOT,
    repo_root: Path = REPO_ROOT,
    official: bool = True,
    run_id: str | None = None,
    created_utc: str | None = None,
    reference_loader: Callable[[Path], pd.DataFrame] = load_reference_strict,
) -> ScoreResult:
    """Score an immutable artifact.  Non-official arguments exist only for fixtures."""
    if official:
        if radar_input.resolve() != RADAR_INPUT.resolve():
            raise ScoreContractError("official scorer must use the accepted radar handoff")
        if config_path.resolve() != CONFIG_PATH.resolve() or repo_root.resolve() != REPO_ROOT.resolve():
            raise ScoreContractError("official scorer must use canonical config/repository paths")
        if output_root.resolve() != OUTPUT_ROOT.resolve():
            raise ScoreContractError("official scorer must use the canonical output root")
        if run_id is not None or created_utc is not None or reference_loader is not load_reference_strict:
            raise ScoreContractError("official scorer rejects fixture provenance/loader overrides")
        dirty = _official_dirty_paths(repo_root)
        if dirty:
            raise ScoreContractError("official scoring paths must be committed: " + "; ".join(dirty))

    source_paths = {
        "scorer": Path(__file__).resolve(),
        "config": config_path,
        "plan": REPO_ROOT / "plans" / "m9_kotte_plan.md",
        "comparator": REPO_ROOT / "src" / "comparator.py",
        "masimo_parser": REPO_ROOT / "src" / "masimo.py",
        "window_grid": REPO_ROOT / "src" / "m4" / "window_grid.py",
        "focused_tests": REPO_ROOT / "tests" / "test_m9_kotte_score.py",
    }
    sources_before = snapshot_load_bearing_sources(source_paths)

    before = snapshot_directory(radar_input)
    if official:
        before_files = before.by_path
        if before_files.get("radar_only_handoff.json", {}).get("sha256") != ACCEPTED_HANDOFF_SHA256:
            raise ScoreContractError("accepted radar handoff identity hash mismatch")
        if before_files.get("radar_runner_config.json", {}).get("sha256") != ACCEPTED_RADAR_CONFIG_SHA256:
            raise ScoreContractError("accepted radar config identity hash mismatch")
    radar_rows, radar_run_meta, radar_config = verify_radar_artifact(radar_input, before)
    if official:
        if radar_run_meta.get("git_commit") != ACCEPTED_RADAR_COMMIT:
            raise ScoreContractError("accepted radar commit identity mismatch")
        if radar_input.name != RADAR_RUN_ID:
            raise ScoreContractError("accepted radar run identity mismatch")
    _radar_rows_contract(radar_rows, radar_config, official)

    config_sha256 = sha256_file(config_path)
    try:
        config_document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ScoreContractError(f"M9 scoring config is unreadable: {exc}") from exc
    if not isinstance(config_document, dict) or not isinstance(config_document.get("scoring"), dict):
        raise ScoreContractError("M9 config has no scoring mapping")
    scoring = config_document["scoring"]
    validate_scoring_config(scoring)
    capture_ids = {str(row["capture_id"]) for row in radar_rows}
    references_by_id = reference_map(scoring, capture_ids, repo_root)

    radar_capture_entries = radar_config.get("radar_evaluation", {}).get("captures", [])
    capture_directories = {
        str(entry["capture_id"]): (repo_root / str(entry["directory"])).resolve()
        for entry in radar_capture_entries
    }
    if set(capture_directories) != capture_ids:
        raise ScoreContractError("radar config capture identity mismatch")
    if official:
        for capture_id in capture_ids:
            if references_by_id[capture_id].parent != capture_directories[capture_id]:
                raise ScoreContractError(f"reference path does not match capture identity: {capture_id}")

    capture_input_meta = {
        str(item["capture_id"]): item for item in radar_run_meta.get("capture_inputs", [])
    }
    if set(capture_input_meta) != capture_ids:
        raise ScoreContractError("radar run metadata capture identity mismatch")
    origins: dict[str, tuple[float, str, bool]] = {}
    references: dict[str, pd.DataFrame] = {}
    reference_hashes: dict[str, str] = {}
    reference_duplicate_diagnostics: dict[str, dict] = {}
    metadata_hashes: dict[str, str] = {}
    metadata_paths: dict[str, Path] = {}
    for capture_id in sorted(capture_ids):
        metadata_path = capture_directories[capture_id] / "run_metadata.json"
        if not metadata_path.is_file():
            raise ScoreContractError(f"missing capture metadata for {capture_id}")
        metadata_bytes = metadata_path.read_bytes()
        metadata_hash = sha256(metadata_bytes).hexdigest()
        if metadata_hash != capture_input_meta[capture_id].get("metadata_sha256"):
            raise ScoreContractError(f"capture metadata identity mismatch for {capture_id}")
        try:
            metadata = json.loads(metadata_bytes.decode("utf-8"))
        except Exception as exc:
            raise ScoreContractError(f"capture metadata malformed for {capture_id}: {exc}") from exc
        if not isinstance(metadata, dict):
            raise ScoreContractError(f"capture metadata is not a mapping for {capture_id}")
        metadata_hashes[capture_id] = metadata_hash
        metadata_paths[capture_id] = metadata_path
        origins[capture_id] = resolve_frame0_epoch(metadata)
        reference_path = references_by_id[capture_id]
        reference_hashes[capture_id] = sha256_file(reference_path)
        reference = reference_loader(reference_path)
        parser_diagnostics = _normalized_reference_diagnostics(reference)
        references[capture_id] = reference
        reference_duplicate_diagnostics[capture_id] = {
            "capture_id": capture_id,
            "reference_path": str(reference_path.resolve()),
            "reference_sha256": reference_hashes[capture_id],
            **parser_diagnostics,
            "normalization_policy": (
                "src.masimo.load_masimo: numeric mean rounded to 1 dp; "
                "non-numeric columns retain first"
            ),
        }

    if official and not all(value[2] for value in origins.values()):
        raise ScoreContractError(
            "canonical M9 cohort must retain its approximate-origin exploratory status"
        )

    scored_rows = build_scored_rows(radar_rows, references, origins)
    summaries = aggregate_summaries(scored_rows)
    failures = failure_census(radar_rows)
    margins = margin_diagnostics(radar_rows)

    for capture_id, path in references_by_id.items():
        if sha256_file(path) != reference_hashes[capture_id]:
            raise ScoreContractError(f"reference changed during scoring: {capture_id}")
        if sha256_file(metadata_paths[capture_id]) != metadata_hashes[capture_id]:
            raise ScoreContractError(f"capture metadata changed during scoring: {capture_id}")
    if sha256_file(config_path) != config_sha256:
        raise ScoreContractError("M9 scoring config changed during scoring")
    after = snapshot_directory(radar_input)
    if after.digest_sha256 != before.digest_sha256 or after.files != before.files:
        raise ScoreContractError("immutable radar artifact changed during scoring")
    sources_after = snapshot_load_bearing_sources(source_paths)
    if sources_after != sources_before:
        raise ScoreContractError("load-bearing source identity changed during scoring")
    verified_sources = sources_before

    timestamp = created_utc or datetime.now(timezone.utc).isoformat()
    resolved_run_id = run_id or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        + f"_{before.digest_sha256[:12]}_{CLASSIFICATION}"
    )
    output_dir = _validated_output_directory(output_root, resolved_run_id)
    output_dir.mkdir(parents=True, exist_ok=False)
    atomic_write_json(output_dir / "scored_rows.json", scored_rows)
    atomic_write_csv(output_dir / "scored_rows.csv", scored_rows)
    atomic_write_json(output_dir / "summaries.json", summaries)
    atomic_write_csv(output_dir / "summaries.csv", summaries)
    atomic_write_json(output_dir / "failure_census.json", failures)
    atomic_write_json(output_dir / "pair_margin_diagnostics.json", margins)
    atomic_write_json(
        output_dir / "reference_duplicate_diagnostics.json",
        reference_duplicate_diagnostics,
    )
    atomic_write_json(
        output_dir / "radar_input_snapshot.json",
        {"before": before.files, "before_digest_sha256": before.digest_sha256,
         "after": after.files, "after_digest_sha256": after.digest_sha256},
    )
    paired = [row for row in scored_rows if row["joint_scored"]]
    atomic_write_npz(
        output_dir / "paired_evidence.npz",
        {
            "capture_id": np.asarray([row["capture_id"] for row in paired]),
            "method": np.asarray([row["method"] for row in paired]),
            "vital": np.asarray([row["vital"] for row in paired]),
            "lock_estimand_id": np.asarray([row["lock_estimand_id"] for row in paired]),
            "window_index": np.asarray([row["window_index"] for row in paired], dtype=np.int64),
            "estimate_bpm": np.asarray([row["estimate_bpm"] for row in paired], dtype=np.float64),
            "reference_bpm": np.asarray(
                [row["reference_value_bpm"] for row in paired], dtype=np.float64
            ),
            "error_bpm": np.asarray([row["error_bpm"] for row in paired], dtype=np.float64),
            "classification": np.asarray(CLASSIFICATION),
            "promotion_eligible": np.asarray(False, dtype=np.bool_),
            "radar_input_digest_sha256": np.asarray(before.digest_sha256),
        },
    )

    actual_argv = [sys.executable, *sys.argv]
    source_hashes = {
        name: identity["sha256"] for name, identity in verified_sources.items()
    }
    run_meta = {
        "run_id": resolved_run_id,
        "created_utc": timestamp,
        "classification": CLASSIFICATION,
        "promotion_eligible": False,
        "claim_eligible": False,
        "radar_run_id": radar_input.name,
        "radar_input_digest_before_sha256": before.digest_sha256,
        "radar_input_digest_after_sha256": after.digest_sha256,
        "radar_input_unchanged": True,
        "radar_input_commit": radar_run_meta.get("git_commit"),
        "config_sha256": config_sha256,
        "source_hashes": source_hashes,
        "source_identities": verified_sources,
        "reference_hashes": reference_hashes,
        "reference_duplicate_diagnostics": reference_duplicate_diagnostics,
        "capture_metadata_hashes": metadata_hashes,
        "origins": {
            capture_id: {
                "frame0_epoch": value[0], "origin_source": value[1],
                "origin_is_approximate": value[2],
            }
            for capture_id, value in origins.items()
        },
        "actual_argv": actual_argv,
        "actual_invocation": subprocess.list2cmdline(actual_argv),
        "documented_invocation": DOCUMENTED_INVOCATION,
        "git_commit": _git_text(repo_root, "rev-parse", "HEAD"),
        "git_status": _git_text(repo_root, "status", "--short").splitlines(),
        "python_version": sys.version,
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
        "k0_role": "lock_selection_in_sample_excluded_from_comparative_metrics",
        "comparative_universe": "k_ge_1_only",
        "hr_limitation": (
            "Approximate time origin and low within-session HR dynamic range; HR is "
            "descriptive beside constant_session_median and cannot support a final agreement claim."
        ),
        "arm_policy": "both loading arms reported separately; no ranking or promotion",
        "outcome_threshold": None,
    }
    atomic_write_json(output_dir / "run_meta.json", run_meta)
    report = (
        "# M9 Kotte exploratory scoring\n\n"
        f"Classification: `{CLASSIFICATION}`; promotion eligible: **no**.\n\n"
        "The immutable radar-only artifact was content-hashed before and after scoring and "
        "was unchanged. Comparative metrics use only `k >= 1`; `k = 0` remains a labelled "
        "lock-selection-in-sample diagnostic. Both loading arms remain separate. HR results "
        "are descriptive beside `constant_session_median` because approximate timing and low "
        "within-session HR dynamic range preclude a final agreement claim. Poor performance "
        "is a scientific result and does not change the estimator.\n"
    )
    atomic_write_text(output_dir / "report.md", report)
    output_hashes = {
        path.relative_to(output_dir).as_posix(): sha256_file(path)
        for path in sorted(output_dir.rglob("*")) if path.is_file()
    }
    atomic_write_json(
        output_dir / "scoring_handoff.json",
        {
            "classification": CLASSIFICATION,
            "promotion_eligible": False,
            "radar_input_digest_sha256": before.digest_sha256,
            "reference_hashes": reference_hashes,
            "output_hashes": output_hashes,
        },
    )
    return ScoreResult(output_dir, tuple(scored_rows), tuple(summaries))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-scoring", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute_scoring:
        parser.error("canonical reference scoring requires --execute-scoring after review")
    result = execute_score()
    print(f"M9 exploratory scoring artifact: {result.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
