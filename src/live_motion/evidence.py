"""Atomic, typed evidence for every executed development analysis attempt."""
from __future__ import annotations

import csv
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np

from .controller import AttemptDisposition, ControllerEvent, DisplaySnapshot

DEVELOPMENT_EVIDENCE_VERSION = 1
REQUIRED_ANALYSIS_FIELDS = {
    "phase_raw",
    "phase_clean",
    "heart_freqs_hz",
    "heart_spectrum",
    "resp_freqs_hz",
    "resp_spectrum",
    "selected_peak_bins",
    "respiration_inputs_hz",
    "activity_resp_projection",
    "activity_subband_projection",
    "coherence_scores",
    "thresholds",
    "rejection_reasons",
    "hr_window_start",
    "br_window_start",
    "exceptional_evidence",
    "corrected_range_m",
}
EXTENDED_ANALYSIS_FIELDS = {
    "extended_phase",
    "extended_fft_freqs_hz",
    "extended_fft_spectrum",
    "extended_selected_peak_bins",
    "respiratory_projection",
    "subband_projection",
    "respiratory_block_rms",
    "subband_block_rms",
    "drift_rms",
    "persistence_half_scores",
    "persistence_half_energy",
    "persistence_basis_norms",
    "persistence_coefficients",
    "persistence_bases",
    "persistence_centered_halves",
    "extended_threshold_values",
    "positive_rejections",
    "quiet_rejections",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _primitive_array(value: Any, name: str) -> np.ndarray:
    if value is None:
        return np.array([], dtype=np.float64)
    if type(value) in (bool, int, float, str):
        return np.asarray(value)
    array = np.asarray(value)
    if array.dtype.hasobject:
        raise TypeError(f"evidence field {name!r} has object dtype")
    if array.dtype.kind not in "biufcSU":
        raise TypeError(f"evidence field {name!r} has unsupported dtype {array.dtype}")
    return array


@dataclass(frozen=True)
class AttemptRecord:
    attempt_index: int
    job_id: str
    npz_path: Path
    npz_sha256: str
    disposition: str


@dataclass(frozen=True)
class VerificationIssue:
    code: str
    details: str


class AttemptEvidenceWriter:
    """Write NPZ first, hash it, then append its CSV index row."""

    CSV_FIELDS = (
        "evidence_version", "attempt_index", "job_id", "epoch",
        "requested_selection_revision", "selection_revision", "selection_decision_applied",
        "stage", "frame_start", "frame_stop", "actual_bin", "disposition", "reason",
        "corrected_range_m",
        "executed", "npz_file", "npz_sha256", "config_sha256", "source_sha256",
        "calibration_sha256", "fresh_hr_bpm", "fresh_br_bpm", "held_hr_bpm",
        "held_br_bpm", "hr_window_start", "br_window_start",
    )

    def __init__(
        self,
        run_dir: Path,
        config_hash: str,
        source_hash: str,
        calibration_hash: str,
        failure_injector: Callable[[str], None] | None = None,
    ):
        self.run_dir = Path(run_dir)
        self.attempt_dir = self.run_dir / "analysis_attempts"
        self.attempt_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = self.run_dir / "analysis_attempts.csv"
        self.events_path = self.run_dir / "controller_events.csv"
        self.config_hash = str(config_hash)
        self.source_hash = str(source_hash)
        self.calibration_hash = str(calibration_hash)
        self.failure_injector = failure_injector
        self._next_index = self._existing_row_count()

    def _existing_row_count(self) -> int:
        if not self.csv_path.is_file():
            return 0
        with self.csv_path.open("r", newline="", encoding="utf-8") as handle:
            return sum(1 for _ in csv.DictReader(handle))

    def _inject(self, point: str) -> None:
        if self.failure_injector is not None:
            self.failure_injector(point)

    def write_attempt(
        self,
        attempt: AttemptDisposition,
        snapshot: DisplaySnapshot,
        evidence: dict[str, Any],
    ) -> AttemptRecord:
        result = attempt.result
        if not result.executed:
            raise ValueError(
                "jobs cancelled before execution require event evidence, not an attempt NPZ"
            )
        required_evidence = set(REQUIRED_ANALYSIS_FIELDS)
        br_window_start = int(np.asarray(evidence.get("br_window_start", -1)).item())
        has_extended_window = result.frame_stop - br_window_start == 1200
        if result.stage == "extended_60" or has_extended_window:
            required_evidence.update(EXTENDED_ANALYSIS_FIELDS)
        missing_evidence = required_evidence.difference(evidence)
        if missing_evidence:
            raise ValueError(
                "analysis evidence omitted required fields: "
                + ", ".join(sorted(missing_evidence))
            )
        corrected_range_array = np.asarray(evidence["corrected_range_m"])
        if corrected_range_array.shape != ():
            raise ValueError("corrected_range_m must be a scalar")
        corrected_range_m = float(corrected_range_array.item())
        if result.actual_bin is None:
            if not np.isnan(corrected_range_m) or not attempt.reason:
                raise ValueError(
                    "an attempt without an actual bin requires NaN corrected range and a reason"
                )
        elif not (np.isfinite(corrected_range_m) and corrected_range_m >= 0.0):
            raise ValueError("an actual bin requires a finite nonnegative corrected range")
        index = self._next_index
        filename = f"attempt_{index:06d}_{result.job_id}.npz"
        final_path = self.attempt_dir / filename
        temporary_path = self.attempt_dir / (filename + ".tmp")
        if final_path.exists() or temporary_path.exists():
            raise FileExistsError(f"refusing to overwrite attempt evidence {final_path}")

        fresh_hr = (
            float((result.dsp or {}).get("hr_raw", np.nan))
            if attempt.hr_accepted else np.nan
        )
        fresh_br = float("nan")
        if attempt.br_accepted:
            breathing_state = getattr(result.breathing, "state", None)
            breathing_value = getattr(result.breathing, "value_bpm", np.nan)
            if isinstance(result.breathing, dict):
                breathing_state = result.breathing.get("state")
                breathing_value = result.breathing.get("value_bpm", np.nan)
            fresh_br = (
                float(breathing_value)
                if breathing_state == "positive"
                else float((result.dsp or {}).get("br_bpm", np.nan))
            )
        # Buffered results may be released together. Capture evidence against the
        # immutable state saved when this individual result was applied.
        attempt_snapshot = attempt.snapshot or snapshot
        held_hr = (
            attempt_snapshot.hr.value_bpm if attempt_snapshot.hr.state == "held" else np.nan
        )
        held_br = (
            attempt_snapshot.br.value_bpm if attempt_snapshot.br.state == "held" else np.nan
        )
        selection_applied = bool(attempt.selection_applied)
        recorded_revision = (
            attempt.selection_revision
            if attempt.selection_revision is not None
            else attempt_snapshot.selection_revision
        )
        arrays: dict[str, np.ndarray] = {
            "evidence_version": np.asarray(DEVELOPMENT_EVIDENCE_VERSION, dtype=np.int64),
            "job_id": np.asarray(result.job_id),
            "epoch": np.asarray(result.epoch, dtype=np.int64),
            "requested_selection_revision": np.asarray(
                result.selection_revision, dtype=np.int64
            ),
            "selection_revision": np.asarray(recorded_revision, dtype=np.int64),
            "selection_decision_applied": np.asarray(selection_applied, dtype=np.bool_),
            "stage": np.asarray(result.stage),
            "frame_start": np.asarray(result.frame_start, dtype=np.int64),
            "frame_stop": np.asarray(result.frame_stop, dtype=np.int64),
            "actual_bin": np.asarray(-1 if result.actual_bin is None else result.actual_bin, dtype=np.int64),
            "executed": np.asarray(result.executed, dtype=np.bool_),
            "execution_status": np.asarray(result.status),
            "disposition": np.asarray(attempt.disposition),
            "rejection_reason": np.asarray(attempt.reason),
            "config_sha256": np.asarray(self.config_hash),
            "source_sha256": np.asarray(self.source_hash),
            "calibration_sha256": np.asarray(self.calibration_hash),
            "fresh_hr_bpm": np.asarray(fresh_hr, dtype=np.float64),
            "fresh_br_bpm": np.asarray(fresh_br, dtype=np.float64),
            "held_hr_bpm": np.asarray(held_hr, dtype=np.float64),
            "held_br_bpm": np.asarray(held_br, dtype=np.float64),
            "display_hr_bpm": np.asarray(attempt_snapshot.hr.value_bpm, dtype=np.float64),
            "display_br_bpm": np.asarray(attempt_snapshot.br.value_bpm, dtype=np.float64),
            "display_hr_state": np.asarray(attempt_snapshot.hr.state),
            "display_br_state": np.asarray(attempt_snapshot.br.state),
        }
        for name, value in evidence.items():
            if name in arrays:
                raise ValueError(f"evidence field {name!r} collides with required metadata")
            arrays[name] = _primitive_array(value, name)

        self._inject("before_npz_write")
        with temporary_path.open("xb") as handle:
            np.savez(handle, **arrays)
            handle.flush()
            os.fsync(handle.fileno())
        self._inject("after_npz_close")
        digest = sha256_file(temporary_path)
        os.replace(temporary_path, final_path)
        self._inject("after_npz_replace")

        row = {
            "evidence_version": DEVELOPMENT_EVIDENCE_VERSION,
            "attempt_index": index,
            "job_id": result.job_id,
            "epoch": result.epoch,
            "requested_selection_revision": result.selection_revision,
            "selection_revision": recorded_revision,
            "selection_decision_applied": int(selection_applied),
            "stage": result.stage,
            "frame_start": result.frame_start,
            "frame_stop": result.frame_stop,
            "actual_bin": "" if result.actual_bin is None else result.actual_bin,
            "corrected_range_m": (
                "" if not np.isfinite(corrected_range_m)
                else f"{corrected_range_m:.12g}"
            ),
            "disposition": attempt.disposition,
            "reason": attempt.reason,
            "executed": int(result.executed),
            "npz_file": str(Path("analysis_attempts") / filename),
            "npz_sha256": digest,
            "config_sha256": self.config_hash,
            "source_sha256": self.source_hash,
            "calibration_sha256": self.calibration_hash,
            "fresh_hr_bpm": "" if not np.isfinite(fresh_hr) else f"{fresh_hr:.12g}",
            "fresh_br_bpm": "" if not np.isfinite(fresh_br) else f"{fresh_br:.12g}",
            "held_hr_bpm": "" if not np.isfinite(held_hr) else f"{held_hr:.12g}",
            "held_br_bpm": "" if not np.isfinite(held_br) else f"{held_br:.12g}",
            "hr_window_start": evidence.get("hr_window_start", ""),
            "br_window_start": evidence.get("br_window_start", ""),
        }
        new_file = not self.csv_path.exists()
        self._inject("before_csv_append")
        with self.csv_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.CSV_FIELDS)
            if new_file:
                writer.writeheader()
            writer.writerow(row)
            handle.flush()
            os.fsync(handle.fileno())
        self._next_index += 1
        return AttemptRecord(index, result.job_id, final_path, digest, attempt.disposition)

    def write_event(self, event: ControllerEvent | Any) -> None:
        fields = ("kind", "reason", "frame_index", "epoch", "job_id")
        row = {name: getattr(event, name, "") for name in fields}
        new_file = not self.events_path.exists()
        with self.events_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            if new_file:
                writer.writeheader()
            writer.writerow(row)
            handle.flush()


def verify_development_evidence(run_dir: Path) -> list[VerificationIssue]:
    """Return every structural/hash/cardinality failure; an empty list passes."""

    root = Path(run_dir)
    csv_path = root / "analysis_attempts.csv"
    attempt_dir = root / "analysis_attempts"
    issues: list[VerificationIssue] = []
    if not csv_path.is_file():
        return [VerificationIssue("missing_attempt_index", str(csv_path))]
    if not attempt_dir.is_dir():
        return [VerificationIssue("missing_attempt_directory", str(attempt_dir))]
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        if tuple(reader.fieldnames or ()) != AttemptEvidenceWriter.CSV_FIELDS:
            issues.append(VerificationIssue("attempt_index_schema", str(reader.fieldnames)))
    indexed = {str(row.get("npz_file", "")) for row in rows}
    actual = {
        str(path.relative_to(root))
        for path in attempt_dir.glob("*.npz")
    }
    if indexed != actual:
        issues.append(VerificationIssue(
            "attempt_npz_cardinality", f"indexed={sorted(indexed)} actual={sorted(actual)}"
        ))
    temporary = list(attempt_dir.glob("*.tmp"))
    if temporary:
        issues.append(VerificationIssue("partial_attempt_files", str(temporary)))
    required = {
        "evidence_version", "job_id", "epoch", "requested_selection_revision",
        "selection_revision", "selection_decision_applied", "stage",
        "frame_start", "frame_stop", "actual_bin", "executed", "execution_status",
        "disposition", "rejection_reason", "config_sha256", "source_sha256",
        "calibration_sha256", "fresh_hr_bpm", "fresh_br_bpm", "held_hr_bpm",
        "held_br_bpm", "display_hr_bpm", "display_br_bpm", "display_hr_state",
        "display_br_state",
    }
    required.update(REQUIRED_ANALYSIS_FIELDS)
    seen_jobs: set[str] = set()
    last_revision_by_epoch: dict[int, int] = {}

    def scalar(payload, name: str):
        value = payload[name]
        if value.shape != ():
            raise ValueError(f"{name} must be a scalar, got shape {value.shape}")
        return value.item()

    def row_integer(row: dict, name: str) -> int:
        return int(str(row.get(name, "")))

    def row_float_or_nan(row: dict, name: str) -> float:
        text = str(row.get(name, ""))
        return float("nan") if text == "" else float(text)

    for row in rows:
        relative = str(row.get("npz_file", ""))
        path = root / relative
        job_id = str(row.get("job_id", ""))
        if job_id in seen_jobs:
            issues.append(VerificationIssue("duplicate_job_id", job_id))
        seen_jobs.add(job_id)
        if not path.is_file():
            issues.append(VerificationIssue("missing_npz", relative))
            continue
        if sha256_file(path) != row.get("npz_sha256"):
            issues.append(VerificationIssue("npz_hash_mismatch", relative))
            continue
        try:
            with np.load(path, allow_pickle=False) as payload:
                stage_text = str(row.get("stage", ""))
                stage_required = set(required)
                has_1200_br_window = False
                if "br_window_start" in payload.files and "frame_stop" in payload.files:
                    br_start_value = scalar(payload, "br_window_start")
                    frame_stop_value = scalar(payload, "frame_stop")
                    has_1200_br_window = int(frame_stop_value) - int(br_start_value) == 1200
                if stage_text == "extended_60" or has_1200_br_window:
                    stage_required.update(EXTENDED_ANALYSIS_FIELDS)
                missing = stage_required.difference(payload.files)
                if missing:
                    issues.append(VerificationIssue(
                        "npz_missing_fields", f"{relative}: {sorted(missing)}"
                    ))
                    continue
                if str(scalar(payload, "job_id")) != job_id:
                    issues.append(VerificationIssue("npz_job_mismatch", relative))
                comparisons = {
                    "evidence_version": row_integer(row, "evidence_version"),
                    "epoch": row_integer(row, "epoch"),
                    "requested_selection_revision": row_integer(
                        row, "requested_selection_revision"
                    ),
                    "selection_revision": row_integer(row, "selection_revision"),
                    "selection_decision_applied": bool(
                        row_integer(row, "selection_decision_applied")
                    ),
                    "stage": stage_text,
                    "frame_start": row_integer(row, "frame_start"),
                    "frame_stop": row_integer(row, "frame_stop"),
                    "actual_bin": (
                        -1 if str(row.get("actual_bin", "")) == ""
                        else row_integer(row, "actual_bin")
                    ),
                    "executed": bool(row_integer(row, "executed")),
                    "disposition": str(row.get("disposition", "")),
                    "rejection_reason": str(row.get("reason", "")),
                    "config_sha256": str(row.get("config_sha256", "")),
                    "source_sha256": str(row.get("source_sha256", "")),
                    "calibration_sha256": str(row.get("calibration_sha256", "")),
                }
                for name, expected in comparisons.items():
                    actual_value = scalar(payload, name)
                    if isinstance(expected, bool):
                        matches = type(actual_value) in (bool, np.bool_) and bool(actual_value) == expected
                    elif isinstance(expected, int):
                        matches = int(actual_value) == expected
                    else:
                        matches = str(actual_value) == expected
                    if not matches:
                        issues.append(VerificationIssue(
                            "csv_npz_mismatch", f"{relative}: {name}"
                        ))

                evidence_version = int(scalar(payload, "evidence_version"))
                if evidence_version != DEVELOPMENT_EVIDENCE_VERSION:
                    issues.append(VerificationIssue("unsupported_evidence_version", relative))
                if not bool(scalar(payload, "executed")):
                    issues.append(VerificationIssue("unexecuted_attempt_evidence", relative))

                corrected_range_m = float(scalar(payload, "corrected_range_m"))
                indexed_range_m = row_float_or_nan(row, "corrected_range_m")
                if not (
                    corrected_range_m == indexed_range_m
                    or (np.isnan(corrected_range_m) and np.isnan(indexed_range_m))
                ):
                    issues.append(VerificationIssue(
                        "csv_npz_mismatch", f"{relative}: corrected_range_m"
                    ))
                actual_bin = int(scalar(payload, "actual_bin"))
                if actual_bin >= 0 and not (
                    np.isfinite(corrected_range_m) and corrected_range_m >= 0.0
                ):
                    issues.append(VerificationIssue("invalid_corrected_range", relative))
                if actual_bin < 0 and not np.isnan(corrected_range_m):
                    issues.append(VerificationIssue("invalid_corrected_range", relative))
                if actual_bin < 0 and not str(scalar(payload, "rejection_reason")):
                    issues.append(VerificationIssue("missing_bin_rejection_reason", relative))

                frame_start = int(scalar(payload, "frame_start"))
                frame_stop = int(scalar(payload, "frame_stop"))
                if frame_start >= frame_stop:
                    issues.append(VerificationIssue("invalid_frame_bounds", relative))
                epoch = int(scalar(payload, "epoch"))
                revision = int(scalar(payload, "selection_revision"))
                decision_applied = bool(scalar(payload, "selection_decision_applied"))
                previous_revision = last_revision_by_epoch.get(epoch)
                if previous_revision is not None:
                    if revision < previous_revision or revision > previous_revision + 1:
                        issues.append(VerificationIssue(
                            "unexplained_selection_revision", relative
                        ))
                    elif revision == previous_revision + 1 and not decision_applied:
                        issues.append(VerificationIssue(
                            "unexplained_selection_revision", relative
                        ))
                last_revision_by_epoch[epoch] = max(
                    revision, previous_revision if previous_revision is not None else revision
                )

                disposition = str(scalar(payload, "disposition"))
                for name in ("fresh_hr_bpm", "fresh_br_bpm", "held_hr_bpm", "held_br_bpm"):
                    value = float(scalar(payload, name))
                    row_value = row_float_or_nan(row, name)
                    if not (
                        value == row_value or (np.isnan(value) and np.isnan(row_value))
                    ):
                        issues.append(VerificationIssue(
                            "csv_npz_measurement_mismatch", f"{relative}: {name}"
                        ))
                if disposition in {"expired", "superseded", "stale_epoch", "failed"}:
                    if np.isfinite(float(scalar(payload, "fresh_hr_bpm"))) or np.isfinite(
                        float(scalar(payload, "fresh_br_bpm"))
                    ):
                        issues.append(VerificationIssue(
                            "invalid_fresh_measurement_for_disposition", relative
                        ))

                exceptional_flag = payload["exceptional_evidence"]
                if exceptional_flag.shape != () or exceptional_flag.dtype.kind != "b":
                    issues.append(VerificationIssue("exceptional_flag_dtype", relative))
                    exceptional = True
                else:
                    exceptional = bool(exceptional_flag.item())
                hr_start = int(scalar(payload, "hr_window_start"))
                br_start = int(scalar(payload, "br_window_start"))
                if not (frame_start <= hr_start < frame_stop and 0 <= br_start < frame_stop):
                    issues.append(VerificationIssue("invalid_window_starts", relative))

                if not exceptional:
                    expected_hr_frames = {
                        "preview_10": 200,
                        "preview_20": 400,
                    }.get(stage_text, 600)
                    if frame_stop - hr_start != expected_hr_frames:
                        issues.append(VerificationIssue("invalid_hr_window_length", relative))
                    phase_raw = payload["phase_raw"]
                    phase_clean = payload["phase_clean"]
                    if phase_raw.shape != (expected_hr_frames,) or phase_clean.shape != (
                        expected_hr_frames,
                    ):
                        issues.append(VerificationIssue("invalid_phase_shape", relative))
                    for frequency_name, spectrum_name in (
                        ("heart_freqs_hz", "heart_spectrum"),
                        ("resp_freqs_hz", "resp_spectrum"),
                    ):
                        frequencies = payload[frequency_name]
                        spectrum = payload[spectrum_name]
                        if (
                            frequencies.ndim != 1
                            or frequencies.size == 0
                            or spectrum.shape != frequencies.shape
                        ):
                            issues.append(VerificationIssue(
                                "invalid_spectrum_shape", f"{relative}: {spectrum_name}"
                            ))
                    if payload["selected_peak_bins"].dtype.kind not in "iu":
                        issues.append(VerificationIssue("selected_peak_bins_dtype", relative))
                    if payload["rejection_reasons"].dtype.kind not in "SU":
                        issues.append(VerificationIssue("rejection_reasons_dtype", relative))
                    if payload["thresholds"].size == 0 or not np.all(
                        np.isfinite(payload["thresholds"])
                    ):
                        issues.append(VerificationIssue("invalid_threshold_evidence", relative))

                    has_extended = stage_text == "extended_60" or (
                        "extended_phase" in payload.files and payload["extended_phase"].size > 0
                    )
                    if has_extended:
                        missing_extended = EXTENDED_ANALYSIS_FIELDS.difference(payload.files)
                        if missing_extended:
                            issues.append(VerificationIssue(
                                "missing_extended_fields",
                                f"{relative}: {sorted(missing_extended)}",
                            ))
                        else:
                            if frame_stop - br_start != 1200:
                                issues.append(VerificationIssue(
                                    "invalid_br_window_length", relative
                                ))
                            expected_shapes = {
                                "extended_phase": (1200,),
                                "respiratory_projection": (1200,),
                                "subband_projection": (1200,),
                                "respiratory_block_rms": (6,),
                                "subband_block_rms": (6,),
                                "persistence_half_scores": (2,),
                                "persistence_half_energy": (2,),
                                "persistence_basis_norms": (2, 2),
                                "persistence_coefficients": (2, 2),
                                "persistence_bases": (2, 2, 600),
                                "persistence_centered_halves": (2, 600),
                                "extended_threshold_values": (7,),
                            }
                            for name, shape in expected_shapes.items():
                                if payload[name].shape != shape:
                                    issues.append(VerificationIssue(
                                        "invalid_extended_shape", f"{relative}: {name}"
                                    ))
                            ext_freq = payload["extended_fft_freqs_hz"]
                            ext_spec = payload["extended_fft_spectrum"]
                            if ext_freq.ndim != 1 or ext_freq.size == 0 or ext_spec.shape != ext_freq.shape:
                                issues.append(VerificationIssue(
                                    "invalid_extended_spectrum", relative
                                ))
                            if payload["extended_selected_peak_bins"].dtype.kind not in "iu":
                                issues.append(VerificationIssue(
                                    "extended_peak_bins_dtype", relative
                                ))
                            for name in ("positive_rejections", "quiet_rejections"):
                                if payload[name].dtype.kind not in "SU":
                                    issues.append(VerificationIssue(
                                        "extended_rejection_dtype", f"{relative}: {name}"
                                    ))
        except (OSError, ValueError, KeyError) as exc:
            issues.append(VerificationIssue("npz_unreadable", f"{relative}: {exc}"))
    return issues
