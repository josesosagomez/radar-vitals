"""Atomic, typed evidence for every executed development analysis attempt."""
from __future__ import annotations

import csv
import hashlib
import json
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
# Every primitive returned by breathing.assess_breathing is persisted with an
# ``extended_`` prefix.  Keeping this list explicit makes silent omissions fail
# before an attempt is indexed and keeps the schema readable without executing
# signal processing at import time.
BREATHING_EVIDENCE_NAMES = {
    "valid", "reason", "phase", "detrended", "linear_trend",
    "respiratory_projection", "subband_projection",
    "respiratory_block_rms", "subband_block_rms", "drift_rms",
    "fft_score", "ha_score", "persistence", "persistence_valid",
    "persistence_reason", "persistence_evaluated",
    "persistence_frequency_policy", "candidate_bin", "candidate_hz",
    "rate_bpm", "peaks_agree", "candidate_local_max",
    "refinement_available", "refinement_reason", "fft_spectrum",
    "fft_freqs_hz", "ha_candidate_scores", "ha_harmonic_power",
    "fft_selected_bin", "ha_selected_bin",
    "respiratory_fourier_coefficients", "subband_fourier_coefficients",
    "raw_grid_spacing_bpm", "persistence_half_scores",
    "persistence_half_energy", "persistence_basis_norms",
    "persistence_coefficients", "persistence_bases",
    "persistence_centered_halves", "persistence_roundoff_tolerance",
    "fft_rr_bpm", "fft_peak_hz", "fft_peak_snr_db", "fft_peak_bin",
    "fft_band_argmax_bin", "fft_band_argmax_is_local_max",
    "fft_selected_is_edge_bin", "ha_rr_bpm", "ha_peak_hz",
    "ha_harmonics_used", "ha_candidate_freqs_hz", "ha_fund_is_local_max",
    "ha_selected_is_edge_bin", "ha_harmonic_freqs_hz", "ha_freqs_hz",
    "ha_spectrum", "threshold_fft_score_min", "threshold_ha_score_min",
    "threshold_quiet_resp_rms_max", "threshold_periodic_resp_rms_min",
    "threshold_subband_rms_max", "threshold_drift_rms_max",
    "threshold_persistence_min", "window_eligible", "positive_evaluated",
    "quiet_evaluated", "positive_rejections", "quiet_rejections",
}
BREATHING_TEXT_EVIDENCE_NAMES = {
    "reason", "persistence_reason", "persistence_frequency_policy",
    "refinement_reason", "positive_rejections", "quiet_rejections",
}
BREATHING_BOOL_EVIDENCE_NAMES = {
    "valid", "persistence_valid", "persistence_evaluated", "peaks_agree",
    "candidate_local_max", "refinement_available",
    "fft_band_argmax_is_local_max", "fft_selected_is_edge_bin",
    "ha_fund_is_local_max", "ha_selected_is_edge_bin", "window_eligible",
    "positive_evaluated", "quiet_evaluated",
}
BREATHING_INTEGER_EVIDENCE_NAMES = {
    "candidate_bin", "fft_selected_bin", "ha_selected_bin", "fft_peak_bin",
    "fft_band_argmax_bin", "ha_harmonics_used",
}
BREATHING_COMPLEX_EVIDENCE_NAMES = {
    "respiratory_fourier_coefficients", "subband_fourier_coefficients",
}
EXTENDED_DECISION_FIELDS = {
    "extended_analysis_computed", "extended_phase_reconstructed",
    "extended_phase_validated", "extended_assessment_computed",
    "extended_coupling_computed",
    "extended_failure_component", "ordinary_exception_reason",
    "extended_exception_reason",
    "extended_assessment_state", "extended_assessment_value_bpm",
    "extended_assessment_reason", "extended_br_state", "extended_br_bpm",
    "ahet_respiration_input_hz", "ahet_respiration_input_bpm",
    "extended_ahet_difference_bpm", "hr_veto_reason",
}
EXTENDED_ANALYSIS_FIELDS = {
    f"extended_{name}" for name in BREATHING_EVIDENCE_NAMES
} | EXTENDED_DECISION_FIELDS


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


def _json_safe(value: Any) -> Any:
    """Convert selector diagnostics to canonical JSON without nonfinite tokens."""

    if isinstance(value, np.ndarray):
        return _json_safe(value.tolist())
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float):
        return value if np.isfinite(value) else None
    if type(value) in (bool, int, str) or value is None:
        return value
    raise TypeError(f"selector evidence contains unsupported value {type(value).__name__}")


def _selection_decision_json(result: Any) -> str:
    decision = result.selection_decision
    if decision is None:
        return ""
    value = {
        "selected_bin": decision.selected_bin,
        "selector_succeeded": bool(decision.selector_succeeded),
        "dsp_succeeded": bool(decision.dsp_succeeded),
        "fallback_used": bool(decision.fallback_used),
        "reason": str(decision.reason),
        "evidence": decision.evidence,
    }
    return json.dumps(
        _json_safe(value), sort_keys=True, separators=(",", ":"), allow_nan=False
    )


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
        "executed", "execution_status", "npz_file", "npz_sha256",
        "config_sha256", "source_sha256",
        "calibration_sha256", "fresh_hr_bpm", "fresh_br_bpm", "held_hr_bpm",
        "held_br_bpm", "quiet_assessment_accepted",
        "hr_window_start", "br_window_start",
        "selection_decision_sha256", "selection_commit_frame",
        "selection_commit_revision", "application_frame",
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
        decision_json = _selection_decision_json(result)
        decision_digest = hashlib.sha256(decision_json.encode("utf-8")).hexdigest()
        # Inclusive acquisition frame observed when the controller applied the
        # asynchronous decision.  This is intentionally distinct from the
        # request's half-open signal bound (result.frame_stop).
        selection_commit_frame = attempt_snapshot.frame_index if selection_applied else -1
        selection_commit_revision = recorded_revision if selection_applied else -1
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
            "quiet_assessment_accepted": np.asarray(
                attempt.quiet_assessment_accepted, dtype=np.bool_
            ),
            "selection_decision_json": np.asarray(decision_json),
            "selection_decision_sha256": np.asarray(decision_digest),
            "selection_commit_frame": np.asarray(selection_commit_frame, dtype=np.int64),
            "selection_commit_revision": np.asarray(
                selection_commit_revision, dtype=np.int64
            ),
            "application_frame": np.asarray(
                attempt_snapshot.frame_index, dtype=np.int64
            ),
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
            "execution_status": result.status,
            "npz_file": str(Path("analysis_attempts") / filename),
            "npz_sha256": digest,
            "config_sha256": self.config_hash,
            "source_sha256": self.source_hash,
            "calibration_sha256": self.calibration_hash,
            "fresh_hr_bpm": "" if not np.isfinite(fresh_hr) else f"{fresh_hr:.12g}",
            "fresh_br_bpm": "" if not np.isfinite(fresh_br) else f"{fresh_br:.12g}",
            "held_hr_bpm": "" if not np.isfinite(held_hr) else f"{held_hr:.12g}",
            "held_br_bpm": "" if not np.isfinite(held_br) else f"{held_br:.12g}",
            "quiet_assessment_accepted": int(
                attempt.quiet_assessment_accepted
            ),
            "hr_window_start": evidence.get("hr_window_start", ""),
            "br_window_start": evidence.get("br_window_start", ""),
            "selection_decision_sha256": decision_digest,
            "selection_commit_frame": selection_commit_frame,
            "selection_commit_revision": selection_commit_revision,
            "application_frame": attempt_snapshot.frame_index,
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
        fields = (
            "kind", "reason", "frame_index", "epoch", "job_id",
            "selected_bin", "selection_revision", "signal_frame_stop",
        )
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
    breathing_thresholds: dict[str, Any] | None = None
    metadata_path = root / "run_metadata.json"
    if metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            calibration_path = root / str(metadata.get("calibration_snapshot", ""))
            if calibration_path.is_file():
                calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
                candidate = calibration.get("thresholds", {}).get("breathing")
                if isinstance(candidate, dict):
                    breathing_thresholds = candidate
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            issues.append(VerificationIssue("breathing_calibration_unreadable", str(exc)))
    if not csv_path.is_file():
        return [VerificationIssue("missing_attempt_index", str(csv_path))]
    if not attempt_dir.is_dir():
        return [VerificationIssue("missing_attempt_directory", str(attempt_dir))]
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        if tuple(reader.fieldnames or ()) != AttemptEvidenceWriter.CSV_FIELDS:
            issues.append(VerificationIssue("attempt_index_schema", str(reader.fieldnames)))
    selection_events_by_job: dict[str, list[dict[str, str]]] = {}
    events_path = root / "controller_events.csv"
    if events_path.is_file():
        with events_path.open("r", newline="", encoding="utf-8") as handle:
            event_reader = csv.DictReader(handle)
            expected_event_fields = (
                "kind", "reason", "frame_index", "epoch", "job_id",
                "selected_bin", "selection_revision", "signal_frame_stop",
            )
            if tuple(event_reader.fieldnames or ()) != expected_event_fields:
                issues.append(VerificationIssue(
                    "controller_event_schema", str(event_reader.fieldnames)
                ))
            for event in event_reader:
                if event.get("kind") in {
                    "provisional_bin_selected", "final_bin_selected"
                }:
                    selection_events_by_job.setdefault(
                        str(event.get("job_id", "")), []
                    ).append(event)
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
        "display_br_state", "quiet_assessment_accepted",
        "selection_decision_json",
        "selection_decision_sha256", "selection_commit_frame",
        "selection_commit_revision", "application_frame",
    }
    required.update(REQUIRED_ANALYSIS_FIELDS)
    seen_jobs: set[str] = set()
    last_revision_by_epoch: dict[int, int] = {}
    applied_selection_records: dict[str, dict[str, int | str]] = {}
    extended_lineage_records: list[dict[str, int | str]] = []

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

    def evidence_matches(saved: np.ndarray, expected: Any) -> bool:
        expected_array = _primitive_array(expected, "recomputed_breathing_evidence")
        if saved.shape != expected_array.shape or saved.dtype.kind != expected_array.dtype.kind:
            return False
        if saved.dtype.kind in "fc":
            return bool(np.allclose(
                saved, expected_array, rtol=1e-12, atol=1e-12, equal_nan=True
            ))
        return bool(np.array_equal(saved, expected_array))

    for row_position, row in enumerate(rows):
        relative = str(row.get("npz_file", ""))
        path = root / relative
        job_id = str(row.get("job_id", ""))
        try:
            indexed_position = row_integer(row, "attempt_index")
        except ValueError:
            indexed_position = -1
        expected_name = f"attempt_{row_position:06d}_{job_id}.npz"
        expected_relative = str(Path("analysis_attempts") / expected_name)
        if indexed_position != row_position or relative != expected_relative:
            issues.append(VerificationIssue(
                "attempt_index_order", relative
            ))
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
                    "execution_status": str(row.get("execution_status", "")),
                    "disposition": str(row.get("disposition", "")),
                    "rejection_reason": str(row.get("reason", "")),
                    "config_sha256": str(row.get("config_sha256", "")),
                    "source_sha256": str(row.get("source_sha256", "")),
                    "calibration_sha256": str(row.get("calibration_sha256", "")),
                    "selection_decision_sha256": str(
                        row.get("selection_decision_sha256", "")
                    ),
                    "selection_commit_frame": row_integer(
                        row, "selection_commit_frame"
                    ),
                    "selection_commit_revision": row_integer(
                        row, "selection_commit_revision"
                    ),
                    "application_frame": row_integer(row, "application_frame"),
                    "quiet_assessment_accepted": bool(row_integer(
                        row, "quiet_assessment_accepted"
                    )),
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

                decision_json = str(scalar(payload, "selection_decision_json"))
                decision_digest = hashlib.sha256(decision_json.encode("utf-8")).hexdigest()
                if decision_digest != str(scalar(payload, "selection_decision_sha256")):
                    issues.append(VerificationIssue("selection_decision_hash_mismatch", relative))
                decision_applied_value = bool(scalar(payload, "selection_decision_applied"))
                commit_frame = int(scalar(payload, "selection_commit_frame"))
                commit_revision = int(scalar(payload, "selection_commit_revision"))
                if decision_applied_value:
                    if not decision_json or commit_frame < int(scalar(payload, "frame_stop")) - 1:
                        issues.append(VerificationIssue("invalid_selection_commit", relative))
                    if commit_revision != int(scalar(payload, "selection_revision")):
                        issues.append(VerificationIssue("invalid_selection_commit", relative))
                    matching_events = selection_events_by_job.get(job_id, [])
                    if len(matching_events) != 1:
                        issues.append(VerificationIssue(
                            "selection_event_cardinality", relative
                        ))
                    else:
                        event = matching_events[0]
                        expected_kind = (
                            "provisional_bin_selected"
                            if stage_text == "preview_10"
                            else "final_bin_selected"
                        )
                        if (
                            event.get("kind") != expected_kind
                            or int(event.get("frame_index", -1)) != commit_frame
                            or int(event.get("epoch", -1)) != int(scalar(payload, "epoch"))
                            or int(event.get("selected_bin", -1))
                            != int(scalar(payload, "actual_bin"))
                            or int(event.get("selection_revision", -1)) != commit_revision
                        ):
                            issues.append(VerificationIssue(
                                "selection_event_mismatch", relative
                            ))
                elif commit_frame != -1 or commit_revision != -1:
                    issues.append(VerificationIssue("unexpected_selection_commit", relative))
                elif selection_events_by_job.get(job_id):
                    issues.append(VerificationIssue("unexpected_selection_event", relative))
                decision_payload: dict[str, Any] | None = None
                if decision_json:
                    try:
                        decision_payload = json.loads(decision_json)
                        if not isinstance(decision_payload, dict):
                            raise ValueError("selector decision must be an object")
                        selected = decision_payload.get("selected_bin")
                        actual = int(scalar(payload, "actual_bin"))
                        if selected is not None and int(selected) != actual:
                            issues.append(VerificationIssue(
                                "selection_decision_bin_mismatch", relative
                            ))
                        selector_evidence = decision_payload.get("evidence")
                        semantic_ok = isinstance(selector_evidence, dict)
                        if semantic_ok:
                            semantic_ok = (
                                selector_evidence.get("selected_bin") == selected
                                and bool(selector_evidence.get("fallback_used"))
                                == bool(decision_payload.get("fallback_used"))
                                and str(selector_evidence.get("selection_reason", ""))
                                == str(decision_payload.get("reason", ""))
                            )
                            candidates = selector_evidence.get("candidates")
                            semantic_ok = semantic_ok and isinstance(candidates, list) and any(
                                isinstance(candidate, dict)
                                and candidate.get("bin") == selected
                                for candidate in candidates
                            )
                        if not semantic_ok:
                            issues.append(VerificationIssue(
                                "selection_decision_semantic_mismatch", relative
                            ))
                        if decision_applied_value and (
                            not bool(decision_payload.get("selector_succeeded"))
                            or stage_text not in {"preview_10", "ordinary_30"}
                            or commit_revision
                            != int(scalar(payload, "requested_selection_revision")) + 1
                        ):
                            issues.append(VerificationIssue(
                                "invalid_applied_selection_decision", relative
                            ))
                        if decision_applied_value:
                            applied_selection_records[job_id] = {
                                "stage": stage_text,
                                "epoch": int(scalar(payload, "epoch")),
                                "revision": commit_revision,
                                "selected_bin": actual,
                                "commit_frame": commit_frame,
                                "signal_frame_stop": int(scalar(payload, "frame_stop")),
                                "attempt_index": row_integer(row, "attempt_index"),
                            }
                    except (TypeError, ValueError, json.JSONDecodeError) as exc:
                        issues.append(VerificationIssue(
                            "invalid_selection_decision_json", f"{relative}: {exc}"
                        ))

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
                if has_1200_br_window and actual_bin >= 0:
                    extended_lineage_records.append({
                        "job_id": job_id,
                        "epoch": epoch,
                        "revision": revision,
                        "selected_bin": actual_bin,
                        "application_frame": int(scalar(payload, "application_frame")),
                        "signal_frame_stop": frame_stop,
                        "attempt_index": row_integer(row, "attempt_index"),
                        "relative": relative,
                    })
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

                has_extended = stage_text == "extended_60" or has_1200_br_window
                if has_extended:
                    if actual_bin < 0:
                        issues.append(VerificationIssue(
                            "extended_attempt_missing_committed_bin", relative
                        ))
                    missing_extended = EXTENDED_ANALYSIS_FIELDS.difference(payload.files)
                    if missing_extended:
                        issues.append(VerificationIssue(
                            "missing_extended_fields",
                            f"{relative}: {sorted(missing_extended)}",
                        ))
                    elif frame_stop - br_start != 1200:
                        issues.append(VerificationIssue("invalid_br_window_length", relative))
                    else:
                        flag_names = (
                            "extended_analysis_computed",
                            "extended_phase_reconstructed",
                            "extended_phase_validated",
                            "extended_assessment_computed",
                            "extended_coupling_computed",
                        )
                        flags: dict[str, bool] = {}
                        for name in flag_names:
                            value = payload[name]
                            if value.shape != () or value.dtype.kind != "b":
                                issues.append(VerificationIssue(
                                    "extended_computed_flag_dtype", f"{relative}: {name}"
                                ))
                                flags[name] = False
                            else:
                                flags[name] = bool(value.item())
                        phase_done = flags["extended_phase_reconstructed"]
                        phase_validated = flags["extended_phase_validated"]
                        assessment_done = flags["extended_assessment_computed"]
                        coupling_done = flags["extended_coupling_computed"]
                        fully_computed = flags["extended_analysis_computed"]
                        if (
                            phase_validated and not phase_done
                            or assessment_done and not phase_validated
                            or coupling_done and not assessment_done
                            or fully_computed
                            != (phase_validated and assessment_done and coupling_done)
                        ):
                            issues.append(VerificationIssue(
                                "inconsistent_extended_component_flags", relative
                            ))

                        execution_status_value = str(scalar(payload, "execution_status"))
                        disposition_value = str(scalar(payload, "disposition"))
                        exception_reason = str(scalar(
                            payload, "extended_exception_reason"
                        ))
                        if fully_computed:
                            if execution_status_value != "completed" or exception_reason:
                                issues.append(VerificationIssue(
                                    "invalid_completed_extended_status", relative
                                ))
                        elif (
                            execution_status_value not in {"failed", "interrupted"}
                            or disposition_value != "failed"
                            or not exception_reason
                        ):
                            issues.append(VerificationIssue(
                                "invalid_failed_extended_status", relative
                            ))

                        failure_component = str(scalar(
                            payload, "extended_failure_component"
                        ))
                        expected_failure_component = (
                            "" if fully_computed
                            else "phase_reconstruction" if not phase_done
                            else "phase_validation" if not phase_validated
                            else "breathing_assessment" if not assessment_done
                            else "hr_coupling"
                        )
                        if failure_component != expected_failure_component:
                            issues.append(VerificationIssue(
                                "extended_failure_component_mismatch", relative
                            ))

                        if phase_done:
                            phase = payload["extended_phase"]
                            if phase.ndim != 1:
                                issues.append(VerificationIssue(
                                    "invalid_extended_phase", relative
                                ))
                            if phase_validated and (
                                phase.shape != (1200,) or not np.isfinite(phase).all()
                            ):
                                issues.append(VerificationIssue(
                                    "invalid_extended_phase", relative
                                ))
                        elif payload["extended_phase"].size != 0:
                            issues.append(VerificationIssue(
                                "fabricated_failed_extended_evidence",
                                f"{relative}: extended_phase",
                            ))

                        assessment = None
                        if assessment_done:
                            if breathing_thresholds is None:
                                issues.append(VerificationIssue(
                                    "missing_breathing_calibration", relative
                                ))
                            else:
                                try:
                                    from .breathing import assess_breathing, hr_veto_decision

                                    eligible = bool(scalar(
                                        payload, "extended_window_eligible"
                                    ))
                                    assessment = assess_breathing(
                                        payload["extended_phase"], 20.0,
                                        breathing_thresholds, eligible=eligible,
                                    )
                                    for name in BREATHING_EVIDENCE_NAMES:
                                        field = f"extended_{name}"
                                        if not evidence_matches(
                                            payload[field], assessment.evidence[name]
                                        ):
                                            issues.append(VerificationIssue(
                                                "extended_numerical_mismatch",
                                                f"{relative}: {field}",
                                            ))
                                    for name, expected in {
                                        "extended_assessment_state": assessment.state,
                                        "extended_assessment_value_bpm": assessment.value_bpm,
                                        "extended_assessment_reason": assessment.reason,
                                    }.items():
                                        if not evidence_matches(payload[name], expected):
                                            issues.append(VerificationIssue(
                                                "extended_decision_mismatch",
                                                f"{relative}: {name}",
                                            ))
                                except (KeyError, TypeError, ValueError) as exc:
                                    issues.append(VerificationIssue(
                                        "extended_recomputation_failed",
                                        f"{relative}: {exc}",
                                    ))
                        else:
                            for name in BREATHING_EVIDENCE_NAMES - {"phase"}:
                                field = payload[f"extended_{name}"]
                                expected_kind = (
                                    "SU" if name in BREATHING_TEXT_EVIDENCE_NAMES
                                    else "b" if name in BREATHING_BOOL_EVIDENCE_NAMES
                                    else "iu" if name in BREATHING_INTEGER_EVIDENCE_NAMES
                                    else "c" if name in BREATHING_COMPLEX_EVIDENCE_NAMES
                                    else "f"
                                )
                                if field.size != 0 or field.dtype.kind not in expected_kind:
                                    issues.append(VerificationIssue(
                                        "fabricated_failed_extended_evidence",
                                        f"{relative}: extended_{name}",
                                    ))
                            if (
                                str(scalar(payload, "extended_assessment_state"))
                                != "unresolved"
                                or np.isfinite(float(scalar(
                                    payload, "extended_assessment_value_bpm"
                                )))
                            ):
                                issues.append(VerificationIssue(
                                    "invalid_failed_extended_assessment", relative
                                ))

                        if coupling_done and assessment is not None:
                            ahet_input = float(scalar(
                                payload, "ahet_respiration_input_hz"
                            ))
                            respiration_inputs = payload["respiration_inputs_hz"]
                            ordinary_reason = str(scalar(
                                payload, "ordinary_exception_reason"
                            ))
                            ahet_input_bound = (
                                np.isnan(ahet_input) if ordinary_reason else (
                                    respiration_inputs.shape == (1,)
                                    and (
                                        float(respiration_inputs[0]) == ahet_input
                                        or (
                                            np.isnan(float(respiration_inputs[0]))
                                            and np.isnan(ahet_input)
                                        )
                                    )
                                )
                            )
                            if not ahet_input_bound:
                                issues.append(VerificationIssue(
                                    "ahet_respiration_input_mismatch", relative
                                ))
                            decisions = dict(hr_veto_decision(
                                assessment, {"f_r_hz": ahet_input}
                            ))
                            for name, expected in decisions.items():
                                if not evidence_matches(payload[name], expected):
                                    issues.append(VerificationIssue(
                                        "extended_decision_mismatch",
                                        f"{relative}: {name}",
                                    ))
                        elif not coupling_done:
                            numeric_sentinels = (
                                "extended_br_bpm", "extended_ahet_difference_bpm",
                            )
                            if (
                                str(scalar(payload, "extended_br_state")) != "unresolved"
                                or any(np.isfinite(float(scalar(payload, name)))
                                       for name in numeric_sentinels)
                                or str(scalar(payload, "hr_veto_reason"))
                                != "extended_analysis_failed"
                            ):
                                issues.append(VerificationIssue(
                                    "invalid_failed_extended_coupling", relative
                                ))
                            ahet_input = float(scalar(
                                payload, "ahet_respiration_input_hz"
                            ))
                            ahet_input_bpm = float(scalar(
                                payload, "ahet_respiration_input_bpm"
                            ))
                            if assessment_done:
                                ordinary_reason = str(scalar(
                                    payload, "ordinary_exception_reason"
                                ))
                                respiration_inputs = payload["respiration_inputs_hz"]
                                input_bound = (
                                    np.isnan(ahet_input) if ordinary_reason else (
                                        respiration_inputs.shape == (1,)
                                        and (
                                            float(respiration_inputs[0]) == ahet_input
                                            or (
                                                np.isnan(float(respiration_inputs[0]))
                                                and np.isnan(ahet_input)
                                            )
                                        )
                                    )
                                )
                                bpm_bound = (
                                    ahet_input_bpm == ahet_input * 60.0
                                    or (np.isnan(ahet_input_bpm) and np.isnan(ahet_input))
                                )
                                if not input_bound or not bpm_bound:
                                    issues.append(VerificationIssue(
                                        "ahet_respiration_input_mismatch", relative
                                    ))
                            elif not (np.isnan(ahet_input) and np.isnan(ahet_input_bpm)):
                                issues.append(VerificationIssue(
                                    "unexpected_failed_ahet_input", relative
                                ))

                        fresh_hr = float(scalar(payload, "fresh_hr_bpm"))
                        fresh_br = float(scalar(payload, "fresh_br_bpm"))
                        quiet_accepted = bool(scalar(
                            payload, "quiet_assessment_accepted"
                        ))
                        if not fully_computed:
                            if (
                                np.isfinite(fresh_hr) or np.isfinite(fresh_br)
                                or quiet_accepted
                            ):
                                issues.append(VerificationIssue(
                                    "failed_extended_result_published", relative
                                ))
                        elif assessment is not None:
                            veto = str(scalar(payload, "hr_veto_reason"))
                            if veto and np.isfinite(fresh_hr):
                                issues.append(VerificationIssue(
                                    "vetoed_hr_published", relative
                                ))
                            state = assessment.state
                            stale = disposition_value in {
                                "expired", "superseded", "stale_epoch", "failed"
                            }
                            if state in {"quiet", "unresolved"} and np.isfinite(fresh_br):
                                issues.append(VerificationIssue(
                                    "nonnumeric_state_br_published", relative
                                ))
                            if state == "quiet" and not stale and (
                                not quiet_accepted or disposition_value != "published"
                            ):
                                issues.append(VerificationIssue(
                                    "quiet_assessment_not_published", relative
                                ))
                            if (state != "quiet" or stale) and quiet_accepted:
                                issues.append(VerificationIssue(
                                    "unexpected_assessment_acceptance", relative
                                ))
                            if state == "positive" and not stale and not np.isclose(
                                fresh_br, float(assessment.value_bpm),
                                rtol=0.0, atol=1e-12, equal_nan=False,
                            ):
                                issues.append(VerificationIssue(
                                    "positive_br_publication_mismatch", relative
                                ))
        except (OSError, ValueError, KeyError) as exc:
            issues.append(VerificationIssue("npz_unreadable", f"{relative}: {exc}"))

    for event_job_id, events in selection_events_by_job.items():
        record = applied_selection_records.get(event_job_id)
        if record is None:
            issues.append(VerificationIssue(
                "orphan_selection_event", event_job_id
            ))
            continue
        expected_kind = (
            "provisional_bin_selected"
            if record["stage"] == "preview_10"
            else "final_bin_selected"
        )
        matching = [event for event in events if event.get("kind") == expected_kind]
        if len(matching) != 1:
            issues.append(VerificationIssue(
                "selection_event_cardinality", event_job_id
            ))
            continue
        event = matching[0]
        if (
            int(event.get("epoch", -1)) != record["epoch"]
            or int(event.get("selected_bin", -1)) != record["selected_bin"]
            or int(event.get("selection_revision", -1)) != record["revision"]
            or int(event.get("frame_index", -1)) != record["commit_frame"]
            or int(event.get("signal_frame_stop", -1)) != record["signal_frame_stop"]
        ):
            issues.append(VerificationIssue(
                "selection_event_mismatch", event_job_id
            ))

    for extended in extended_lineage_records:
        relative = str(extended["relative"])
        final_events = [
            event
            for events in selection_events_by_job.values()
            for event in events
            if event.get("kind") == "final_bin_selected"
            and int(event.get("epoch", -1)) == extended["epoch"]
            and int(event.get("selection_revision", -1)) == extended["revision"]
            and int(event.get("selected_bin", -1)) == extended["selected_bin"]
        ]
        if len(final_events) != 1:
            issues.append(VerificationIssue(
                "final_selection_event_cardinality", relative
            ))
            continue
        final_event = final_events[0]
        selection_job_id = str(final_event.get("job_id", ""))
        selection_record = applied_selection_records.get(selection_job_id)
        if (
            selection_record is None
            or selection_record["stage"] != "ordinary_30"
            or selection_record["epoch"] != extended["epoch"]
            or selection_record["revision"] != extended["revision"]
            or selection_record["selected_bin"] != extended["selected_bin"]
            or int(selection_record["attempt_index"]) >= int(extended["attempt_index"])
        ):
            issues.append(VerificationIssue(
                "final_selection_attempt_mismatch", relative
            ))
        if (
            int(final_event.get("frame_index", -1))
            > int(extended["application_frame"])
            or int(final_event.get("signal_frame_stop", -1))
            > int(extended["signal_frame_stop"])
        ):
            issues.append(VerificationIssue(
                "final_selection_event_order", relative
            ))
    return issues
