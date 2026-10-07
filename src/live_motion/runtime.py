"""Responsive live-motion acquisition, scheduling, analysis, and evidence runtime."""
from __future__ import annotations

import csv
import ctypes
import json
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np

from src.range_coordinates import bin_range_m
from src.warmup_select import run_warmup_selection
from src.window_pipeline import run_window_dsp

from .cache import RangeBinCache, reconstruct_phase
from .config import LiveMotionSettings
from .controller import ControllerEvent, ControllerUpdate, DisplaySnapshot, RecoveryController
from .evidence import (
    BREATHING_BOOL_EVIDENCE_NAMES,
    BREATHING_COMPLEX_EVIDENCE_NAMES,
    BREATHING_EVIDENCE_NAMES,
    BREATHING_INTEGER_EVIDENCE_NAMES,
    BREATHING_TEXT_EVIDENCE_NAMES,
    EXTENDED_ANALYSIS_FIELDS,
    AttemptEvidenceWriter,
)
from .features import FrameObservation, RangeFeatureExtractor
from .scheduler import (
    AnalysisJob,
    AnalysisResult,
    BoundedAnalysisScheduler,
    SelectionDecision,
)


@dataclass(frozen=True)
class ExecutedAnalysis:
    result: AnalysisResult
    evidence: dict[str, Any]
    analysis_elapsed_s: float
    selector_elapsed_s: float | None


AnalysisFunction = Callable[[AnalysisJob], ExecutedAnalysis]


def _rss_bytes() -> int | None:
    """Return process resident memory using only the standard library."""

    try:
        if hasattr(ctypes, "windll"):
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong),
                    ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            get_current_process = ctypes.windll.kernel32.GetCurrentProcess
            get_current_process.argtypes = []
            get_current_process.restype = wintypes.HANDLE
            get_process_memory = ctypes.windll.psapi.GetProcessMemoryInfo
            get_process_memory.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD,
            ]
            get_process_memory.restype = wintypes.BOOL
            process = get_current_process()
            ok = get_process_memory(
                process, ctypes.byref(counters), counters.cb
            )
            return int(counters.WorkingSetSize) if ok else None
        # resource.ru_maxrss is a platform-dependent peak, not current RSS.
        # Leave the current-working-set metric unavailable off Windows.
        return None
    except (AttributeError, ImportError, OSError, ValueError):
        return None


class PerformanceWriter:
    """Stream bounded-size telemetry rows; signal arrays never enter this file."""

    FIELDS = (
        "kind", "monotonic_s", "frame_index", "epoch", "job_id", "stage",
        "controller_lag_s", "ui_snapshot_age_s", "analysis_elapsed_s",
        "selector_elapsed_s", "source_queue_overflow_count", "source_queue_depth",
        "rss_bytes", "analysis_status",
    )

    def __init__(self, run_dir: Path):
        self.path = Path(run_dir) / "performance.csv"
        self._handle = self.path.open("w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._handle, fieldnames=self.FIELDS)
        self._writer.writeheader()
        self._lock = threading.Lock()

    def write(self, kind: str, **values: Any) -> None:
        row = {name: "" for name in self.FIELDS}
        with self._lock:
            # Timestamp in serialization order so the CSV is strictly monotonic
            # even when controller, analysis, and UI threads write concurrently.
            row.update(kind=kind, monotonic_s=f"{time.monotonic():.9f}")
            row.update(values)
            self._writer.writerow(row)
            self._handle.flush()

    def close(self) -> None:
        with self._lock:
            if not self._handle.closed:
                self._handle.flush()
                self._handle.close()


def _add_empty_extended_evidence(
    evidence: dict[str, Any],
    reason: str,
    *,
    ordinary_exception_reason: str,
    extended_exception_reason: str,
) -> None:
    """Attach a typed, non-fabricated extended schema after a failed component."""

    for name in BREATHING_EVIDENCE_NAMES:
        if name in BREATHING_TEXT_EVIDENCE_NAMES:
            value = np.array([], dtype="U64")
        elif name in BREATHING_BOOL_EVIDENCE_NAMES:
            value = np.array([], dtype=np.bool_)
        elif name in BREATHING_INTEGER_EVIDENCE_NAMES:
            value = np.array([], dtype=np.int64)
        elif name in BREATHING_COMPLEX_EVIDENCE_NAMES:
            value = np.array([], dtype=np.complex128)
        else:
            value = np.array([], dtype=np.float64)
        evidence[f"extended_{name}"] = value
    evidence.update({
        "extended_analysis_computed": False,
        "extended_phase_reconstructed": False,
        "extended_phase_validated": False,
        "extended_assessment_computed": False,
        "extended_coupling_computed": False,
        "extended_failure_component": "phase_reconstruction",
        "ordinary_exception_reason": str(ordinary_exception_reason),
        "extended_exception_reason": str(extended_exception_reason),
        "extended_assessment_state": "unresolved",
        "extended_assessment_value_bpm": np.nan,
        "extended_assessment_reason": str(reason),
        "extended_br_state": "unresolved",
        "extended_br_bpm": np.nan,
        "ahet_respiration_input_hz": np.nan,
        "ahet_respiration_input_bpm": np.nan,
        "extended_ahet_difference_bpm": np.nan,
        "hr_veto_reason": "extended_analysis_failed",
    })
    missing = EXTENDED_ANALYSIS_FIELDS.difference(evidence)
    if missing:
        raise RuntimeError(f"internal extended exception schema omission: {sorted(missing)}")


def _exception_evidence(
    job: AnalysisJob,
    cfg: dict,
    reason: str,
    actual_bin: int | None = None,
) -> dict[str, Any]:
    if actual_bin is None:
        actual_bin = job.requested_bin
    evidence = {
        "phase_raw": np.array([], dtype=np.float64),
        "phase_clean": np.array([], dtype=np.float64),
        "heart_freqs_hz": np.array([], dtype=np.float64),
        "heart_spectrum": np.array([], dtype=np.float64),
        "resp_freqs_hz": np.array([], dtype=np.float64),
        "resp_spectrum": np.array([], dtype=np.float64),
        "selected_peak_bins": np.array([], dtype=np.int64),
        "respiration_inputs_hz": np.array([], dtype=np.float64),
        "activity_resp_projection": np.array([], dtype=np.float64),
        "activity_subband_projection": np.array([], dtype=np.float64),
        "coherence_scores": np.array([], dtype=np.float64),
        "thresholds": np.array([], dtype=np.float64),
        "rejection_reasons": np.asarray([reason], dtype="U"),
        "hr_window_start": max(job.frame_start, job.frame_stop - 600),
        "br_window_start": job.frame_start,
        "exceptional_evidence": True,
        "corrected_range_m": (
            np.nan if actual_bin is None else bin_range_m(int(actual_bin), cfg)
        ),
    }
    if job.stage == "extended_60" or job.frame_stop - job.frame_start == 1200:
        _add_empty_extended_evidence(
            evidence,
            reason,
            ordinary_exception_reason=reason,
            extended_exception_reason=reason,
        )
    return evidence


def _ordinary_evidence(
    job: AnalysisJob,
    cfg: dict,
    dsp: dict[str, Any],
    actual_bin: int,
) -> dict[str, Any]:
    hr_result = dsp.get("hr_result", {})
    fft_r = dsp.get("fft_r", {})
    ha_r = dsp.get("ha_r", {})
    selected_bins = np.asarray([
        int(fft_r.get("fft_selected_bin", -1)),
        int(ha_r.get("ha_selected_bin", -1)),
        int(hr_result.get("accepted_candidate_rank", -1)),
    ], dtype=np.int64)
    f_r_hz = dsp.get("f_r_hz")
    respiration_inputs = np.asarray([
        np.nan if f_r_hz is None else float(f_r_hz)
    ], dtype=np.float64)
    threshold = float(cfg.get("bin_selection", {}).get(
        "energy_eligibility_min_settled_db", -12.0
    ))
    return {
        "phase_raw": np.asarray(dsp.get("phase_raw", []), dtype=np.float64),
        "phase_clean": np.asarray(dsp.get("phase_clean", []), dtype=np.float64),
        "heart_freqs_hz": np.asarray(hr_result.get("freqs_hz", []), dtype=np.float64),
        "heart_spectrum": np.asarray(hr_result.get("spectrum", []), dtype=np.float64),
        "resp_freqs_hz": np.asarray(fft_r.get("freqs_hz", []), dtype=np.float64),
        "resp_spectrum": np.asarray(fft_r.get("spectrum", []), dtype=np.float64),
        "selected_peak_bins": selected_bins,
        "respiration_inputs_hz": respiration_inputs,
        "activity_resp_projection": np.array([], dtype=np.float64),
        "activity_subband_projection": np.array([], dtype=np.float64),
        "coherence_scores": np.array([], dtype=np.float64),
        "thresholds": np.asarray([threshold], dtype=np.float64),
        "rejection_reasons": np.asarray([str(dsp.get("rej_reason", ""))], dtype="U"),
        "hr_window_start": max(job.frame_start, job.frame_stop - job.raw_frames.shape[0]),
        "br_window_start": job.frame_start,
        "exceptional_evidence": False,
        "corrected_range_m": bin_range_m(actual_bin, cfg),
    }


def _extended_evidence(
    job: AnalysisJob,
    cfg: dict,
    dsp: dict[str, Any] | None,
    actual_bin: int,
    assessment: Any,
    veto_decision: dict[str, Any],
    ordinary_exception_reason: str = "",
) -> dict[str, Any]:
    """Combine ordinary 30-second evidence with the independent 60-second assessment.

    The ordinary DSP can reject all of its estimates while the cached-bin phase
    remains suitable for a quiet or positive breathing decision.  In that case
    the ordinary portion stays explicitly exceptional, but the complete breathing
    calculation is still persisted and can be recomputed by the verifier.
    """

    if ordinary_exception_reason or dsp is None:
        evidence = _exception_evidence(
            job,
            cfg,
            ordinary_exception_reason or "ordinary_dsp_returned_no_result",
            actual_bin,
        )
    else:
        evidence = _ordinary_evidence(job, cfg, dsp, actual_bin)

    _add_computed_assessment_evidence(evidence, assessment)
    evidence["extended_analysis_computed"] = True
    evidence["extended_coupling_computed"] = True
    evidence["extended_failure_component"] = ""
    evidence["ordinary_exception_reason"] = str(ordinary_exception_reason)
    evidence["extended_exception_reason"] = ""
    evidence.update(veto_decision)
    return evidence


def _add_computed_assessment_evidence(
    evidence: dict[str, Any], assessment: Any
) -> None:
    """Persist a completed breathing assessment before HR coupling is attempted."""

    evidence["extended_phase_reconstructed"] = True
    evidence["extended_phase_validated"] = True
    evidence["extended_assessment_computed"] = True
    evidence["extended_assessment_state"] = str(assessment.state)
    evidence["extended_assessment_value_bpm"] = float(assessment.value_bpm)
    evidence["extended_assessment_reason"] = str(assessment.reason)
    for name, value in assessment.evidence.items():
        evidence[f"extended_{name}"] = value


def production_analysis_function(cfg: dict, settings: LiveMotionSettings) -> AnalysisFunction:
    """Build the production selector/DSP callable used by the single worker."""

    fs = settings.frame_rate_hz
    candidate_bins = list(settings.candidate_bins)
    breathing_thresholds = None
    assess_breathing = None
    hr_veto_decision = None
    if settings.extended_breathing_enabled:
        # Keep movement-only operation independent of the optional 60-second
        # assessment.  An enabled configuration has already passed the accepted
        # calibration validator, so these thresholds are never runtime defaults.
        record = settings.calibration_record or {}
        breathing_thresholds = record.get("thresholds", {}).get("breathing")
        if not isinstance(breathing_thresholds, dict):
            raise ValueError(
                "extended breathing requires accepted calibrated breathing thresholds"
            )
        from .breathing import assess_breathing as _assess_breathing
        from .breathing import hr_veto_decision as _hr_veto_decision

        assess_breathing = _assess_breathing
        hr_veto_decision = _hr_veto_decision

    def execute(job: AnalysisJob) -> ExecutedAnalysis:
        started = time.monotonic()
        selector_elapsed_s = None
        actual_bin = job.requested_bin
        ordinary_dsp: dict[str, Any] | None = None
        ordinary_exception_reason = ""
        phase_60_s: np.ndarray | None = None
        phase_reconstruction_returned = False
        phase_reconstruction_validated = False
        breathing: Any | None = None
        coupling_respiration_input_hz = float("nan")
        try:
            decision = None
            if job.selection_mode == "select":
                selector_started = time.monotonic()
                selected_bin, dsp, selection_evidence = run_warmup_selection(
                    job.raw_frames, candidate_bins, cfg, fs
                )
                selector_elapsed_s = time.monotonic() - selector_started
                actual_bin = int(selected_bin)
                fallback_used = bool(selection_evidence.get("fallback_used", False))
                decision = SelectionDecision(
                    selected_bin=actual_bin,
                    evidence=selection_evidence,
                    selector_succeeded=True,
                    dsp_succeeded=dsp is not None,
                    fallback_used=fallback_used,
                    reason=str(selection_evidence.get("selection_reason", "")),
                )
            else:
                if actual_bin is None:
                    raise ValueError("resolved non-selection job has no requested bin")
                if job.range_cache_snapshot is None:
                    dsp = run_window_dsp(job.raw_frames, int(actual_bin), fs, cfg)
                else:
                    # Extended BR is a separate measurement.  An ordinary DSP
                    # exception must not suppress a valid cached-phase quiet or
                    # positive assessment at the same publication boundary.
                    try:
                        dsp = run_window_dsp(job.raw_frames, int(actual_bin), fs, cfg)
                    except Exception as exc:
                        ordinary_exception_reason = f"{type(exc).__name__}: {exc}"
                        dsp = None

            ordinary_dsp = dsp
            if job.range_cache_snapshot is not None and ordinary_dsp is None:
                if not ordinary_exception_reason:
                    ordinary_exception_reason = "ordinary_dsp_returned_no_result"
            veto: dict[str, Any] = {}
            if job.range_cache_snapshot is not None:
                if not settings.extended_breathing_enabled:
                    raise ValueError(
                        "60-second cache snapshot supplied while extended breathing is disabled"
                    )
                if assess_breathing is None or hr_veto_decision is None:
                    raise RuntimeError("extended breathing assessment was not initialized")
                phase_60_s = reconstruct_phase(job.range_cache_snapshot, int(actual_bin))
                phase_reconstruction_returned = True
                if (
                    phase_60_s.shape != (settings.extended_breathing_window_frames,)
                    or not np.isfinite(phase_60_s).all()
                ):
                    raise ValueError(
                        "extended breathing phase must contain 1200 finite frames"
                    )
                phase_reconstruction_validated = True
                breathing = assess_breathing(
                    phase_60_s,
                    fs,
                    breathing_thresholds,
                    eligible=True,
                )
                try:
                    coupling_respiration_input_hz = float(
                        (ordinary_dsp or {}).get("f_r_hz", np.nan)
                    )
                except (TypeError, ValueError):
                    coupling_respiration_input_hz = float("nan")
                if not np.isfinite(coupling_respiration_input_hz):
                    coupling_respiration_input_hz = float("nan")
                veto = dict(hr_veto_decision(breathing, ordinary_dsp or {}))
                # The controller reads the veto from the ordinary result so HR
                # and extended BR are published atomically at the shared boundary.
                dsp_with_veto = dict(ordinary_dsp or {})
                dsp_with_veto.update(veto)
                dsp = dsp_with_veto

            result = AnalysisResult(
                job_id=job.job_id,
                epoch=job.epoch,
                selection_revision=job.selection_revision,
                stage=job.stage,
                frame_start=job.frame_start,
                frame_stop=job.frame_stop,
                actual_bin=actual_bin,
                executed=True,
                status="completed",
                dsp=dsp,
                breathing=breathing,
                selection_decision=decision,
            )
            if breathing is not None:
                evidence = _extended_evidence(
                    job,
                    cfg,
                    ordinary_dsp,
                    int(actual_bin),
                    breathing,
                    veto,
                    ordinary_exception_reason,
                )
            elif dsp is None:
                evidence = _exception_evidence(
                    job,
                    cfg,
                    (
                        decision.reason
                        if decision is not None and decision.reason
                        else "ordinary_dsp_returned_no_result"
                    ),
                    actual_bin,
                )
            else:
                evidence = _ordinary_evidence(job, cfg, dsp, int(actual_bin))
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            result = AnalysisResult(
                job_id=job.job_id,
                epoch=job.epoch,
                selection_revision=job.selection_revision,
                stage=job.stage,
                frame_start=job.frame_start,
                frame_stop=job.frame_stop,
                actual_bin=actual_bin,
                executed=True,
                status="failed",
                dsp=ordinary_dsp,
                error=reason,
            )
            if (
                job.range_cache_snapshot is not None
                and ordinary_dsp is not None
                and actual_bin is not None
            ):
                try:
                    evidence = _ordinary_evidence(
                        job, cfg, ordinary_dsp, int(actual_bin)
                    )
                    _add_empty_extended_evidence(
                        evidence,
                        reason,
                        ordinary_exception_reason="",
                        extended_exception_reason=reason,
                    )
                except Exception:
                    evidence = _exception_evidence(job, cfg, reason, actual_bin)
            else:
                evidence = _exception_evidence(job, cfg, reason, actual_bin)
                if job.range_cache_snapshot is not None:
                    evidence["ordinary_exception_reason"] = (
                        ordinary_exception_reason or reason
                    )
                    evidence["extended_exception_reason"] = reason
            if job.range_cache_snapshot is not None:
                evidence["extended_analysis_computed"] = False
                evidence["extended_coupling_computed"] = False
                evidence["extended_exception_reason"] = reason
                if not phase_reconstruction_returned:
                    evidence["extended_failure_component"] = "phase_reconstruction"
                elif not phase_reconstruction_validated:
                    evidence["extended_failure_component"] = "phase_validation"
                    evidence["extended_phase_reconstructed"] = True
                    evidence["extended_phase"] = phase_60_s
                elif breathing is None:
                    evidence["extended_failure_component"] = "breathing_assessment"
                    evidence["extended_phase_reconstructed"] = True
                    evidence["extended_phase_validated"] = True
                    evidence["extended_phase"] = phase_60_s
                else:
                    evidence["extended_failure_component"] = "hr_coupling"
                    _add_computed_assessment_evidence(evidence, breathing)
                # Coupling outputs remain explicit sentinels after any failure;
                # only successfully computed components above retain values.
                evidence.update({
                    "extended_br_state": "unresolved",
                    "extended_br_bpm": np.nan,
                    "ahet_respiration_input_hz": np.nan,
                    "ahet_respiration_input_bpm": np.nan,
                    "extended_ahet_difference_bpm": np.nan,
                    "hr_veto_reason": "extended_analysis_failed",
                })
                if breathing is not None:
                    evidence["ahet_respiration_input_hz"] = coupling_respiration_input_hz
                    evidence["ahet_respiration_input_bpm"] = (
                        coupling_respiration_input_hz * 60.0
                    )
        return ExecutedAnalysis(
            result=result,
            evidence=evidence,
            analysis_elapsed_s=time.monotonic() - started,
            selector_elapsed_s=selector_elapsed_s,
        )

    return execute


class LiveMotionRuntime:
    """Own source draining and controller mutation on one dedicated thread."""

    def __init__(
        self,
        *,
        frame_source: Any,
        cfg: dict,
        settings: LiveMotionSettings,
        run_dir: Path,
        config_sha256: str,
        source_sha256: str,
        calibration_sha256: str,
        source_id: str,
        source_kind: str = "unknown",
        checkout_commit: str = "unknown",
        shutdown_timeout_s: float = 5.0,
        monotonic_fn: Callable[[], float] = time.monotonic,
        end_sentinel: object | None = None,
        analysis_function: AnalysisFunction | None = None,
    ):
        if not settings.enabled:
            raise ValueError("LiveMotionRuntime requires enabled settings")
        self.frame_source = frame_source
        self.cfg = cfg
        self.settings = settings
        self.run_dir = Path(run_dir)
        profile = cfg["profile"]
        self.extractor = RangeFeatureExtractor(cfg)
        self.cache = RangeBinCache(
            settings.extended_breathing_window_frames,
            int(profile["num_chirps_per_frame"]),
            int(profile["num_rx"]),
            settings.candidate_bins,
        )
        self.controller = RecoveryController(settings, self.cache)
        self.scheduler = BoundedAnalysisScheduler()
        self.evidence_writer = AttemptEvidenceWriter(
            self.run_dir, config_sha256, source_sha256, calibration_sha256
        )
        self.performance = PerformanceWriter(self.run_dir)
        self.analysis_function = analysis_function or production_analysis_function(cfg, settings)
        self.end_sentinel = end_sentinel
        self.source_id = str(source_id)
        self.source_kind = str(source_kind)
        self.checkout_commit = str(checkout_commit)
        if not np.isfinite(shutdown_timeout_s) or shutdown_timeout_s <= 0:
            raise ValueError("shutdown_timeout_s must be finite and positive")
        self.shutdown_timeout_s = float(shutdown_timeout_s)
        self._monotonic = monotonic_fn
        self._last_frame_delivery_monotonic: float | None = None
        self._starvation_latched = False
        self._work: queue.Queue[AnalysisJob | None] = queue.Queue(maxsize=1)
        self._results: queue.Queue[ExecutedAnalysis] = queue.Queue(maxsize=1)
        self._evidence_by_job: dict[str, dict[str, Any]] = {}
        self._tombstoned_jobs: set[str] = set()
        self._worker_started_jobs: set[str] = set()
        self._worker_state_lock = threading.Lock()
        self._stop = threading.Event()
        self._ended = threading.Event()
        self._source_started = False
        self._source_stopped = False
        self._worker: threading.Thread | None = None
        self._controller_thread: threading.Thread | None = None
        self._snapshot_lock = threading.Lock()
        self._snapshot = self.controller.snapshot()
        self._snapshot_monotonic = time.monotonic()
        self.failure: BaseException | None = None
        self.clean_shutdown = False
        self.shutdown_interrupted = False
        self._start_monotonic: float | None = None
        self._run_start_utc = datetime.now(timezone.utc).isoformat()
        self._meta_path = self.run_dir / "performance_meta.json"
        self._write_performance_meta(clean_shutdown=False, run_stop_utc=None)

    @property
    def latest_snapshot(self) -> DisplaySnapshot:
        with self._snapshot_lock:
            return self._snapshot

    @property
    def ended(self) -> bool:
        return self._ended.is_set()

    def _publish_snapshot(self, snapshot: DisplaySnapshot) -> None:
        with self._snapshot_lock:
            self._snapshot = snapshot
            self._snapshot_monotonic = time.monotonic()

    def start(self) -> None:
        if self._source_started:
            raise RuntimeError("live-motion runtime can only be started once")
        self._source_started = True
        self._start_monotonic = time.monotonic()
        self.frame_source.start()
        self._worker = threading.Thread(
            target=self._worker_loop, name="LiveMotionAnalysis", daemon=True
        )
        self._controller_thread = threading.Thread(
            target=self._controller_loop, name="LiveMotionController", daemon=True
        )
        self._worker.start()
        self._controller_thread.start()

    def _worker_loop(self) -> None:
        while True:
            try:
                job = self._work.get(timeout=0.05)
            except queue.Empty:
                if self._stop.is_set():
                    return
                continue
            if job is None:
                return
            with self._worker_state_lock:
                if job.job_id in self._tombstoned_jobs:
                    self._tombstoned_jobs.discard(job.job_id)
                    continue
                self._worker_started_jobs.add(job.job_id)
            started = time.monotonic()
            try:
                executed = self.analysis_function(job)
            except Exception as exc:
                # Convert worker failures into ordinary executed-result evidence.
                # The worker never owns controller state or artifact publication.
                reason = f"{type(exc).__name__}: {exc}"
                result = AnalysisResult(
                    job_id=job.job_id,
                    epoch=job.epoch,
                    selection_revision=job.selection_revision,
                    stage=job.stage,
                    frame_start=job.frame_start,
                    frame_stop=job.frame_stop,
                    actual_bin=job.requested_bin,
                    executed=True,
                    status="failed",
                    error=reason,
                )
                executed = ExecutedAnalysis(
                    result=result,
                    evidence=_exception_evidence(job, self.cfg, reason),
                    analysis_elapsed_s=time.monotonic() - started,
                    selector_elapsed_s=(
                        time.monotonic() - started
                        if job.selection_mode == "select" else None
                    ),
                )
            while job.job_id not in self._tombstoned_jobs:
                try:
                    self._results.put(executed, timeout=0.05)
                    break
                except queue.Full:
                    # A live controller must eventually drain the one result slot.
                    # After timeout accounting tombstones this job, return without
                    # mutating controller state or writing a second attempt.
                    continue
            if job.job_id in self._tombstoned_jobs:
                with self._worker_state_lock:
                    self._worker_started_jobs.discard(job.job_id)
                    self._tombstoned_jobs.discard(job.job_id)

    def _frame_valid(self, frame_index: int) -> bool:
        validity = getattr(self.frame_source, "frame_validity", None)
        if isinstance(validity, list) and 0 <= frame_index < len(validity):
            return bool(validity[frame_index])
        return True

    def _observe(self, frame_index: int, cube: np.ndarray, valid: bool) -> FrameObservation:
        try:
            return self.extractor.observe(
                frame_index, cube, valid=valid,
                invalid_reason="source_frame_invalid" if not valid else "",
            )
        except (TypeError, ValueError, IndexError) as exc:
            raw = np.asarray(cube)
            return FrameObservation(
                frame_index=frame_index,
                valid=False,
                raw_frame=raw,
                gate_bins=np.asarray(self.settings.candidate_bins, dtype=np.int32),
                gate_samples=np.empty((0, 0, 0), dtype=np.complex64),
                invalid_reason=f"malformed_frame_geometry:{type(exc).__name__}",
            )

    def _write_events(self, update: ControllerUpdate) -> None:
        for event in update.events:
            self.evidence_writer.write_event(event)

    def _write_scheduler_event(self, event: Any) -> None:
        self.evidence_writer.write_event(ControllerEvent(
            kind=str(event.kind),
            reason=str(event.reason),
            frame_index=self.latest_snapshot.frame_index,
            epoch=int(event.epoch if event.epoch is not None else self.latest_snapshot.epoch),
            job_id=str(event.job_id),
        ))

    def _apply_update(self, update: ControllerUpdate) -> None:
        self._publish_snapshot(update.snapshot)
        self._write_events(update)
        for resolution in update.dependency_resolutions:
            self.scheduler.resolve_dependency(
                resolution.dependency_job_id,
                selected_bin=resolution.selected_bin,
                selection_revision=resolution.selection_revision,
            )
        for attempt in update.attempts:
            evidence = self._evidence_by_job.pop(attempt.result.job_id)
            self.evidence_writer.write_attempt(
                attempt, attempt.snapshot or update.snapshot, evidence
            )
        for job in update.jobs:
            for event in self.scheduler.submit(job):
                cancelled = self.controller.cancel_job(event.job_id, event.reason)
                self._write_events(cancelled)
        if update.reset_buffers:
            old_epoch = update.events[0].epoch if update.events else update.snapshot.epoch
            for event in self.scheduler.cancel_epoch(old_epoch, update.snapshot.reason):
                self._write_scheduler_event(event)
                if event.kind == "cancelled_before_execution":
                    cancelled = self.controller.cancel_job(event.job_id, event.reason)
                    self._write_events(cancelled)
                elif event.kind == "active_invalidated":
                    self._cancel_active_if_not_started(event.job_id, event.reason)

    def _cancel_active_if_not_started(self, job_id: str, reason: str) -> bool:
        with self._worker_state_lock:
            if job_id in self._worker_started_jobs:
                return False
            self._tombstoned_jobs.add(job_id)
        active = self.scheduler.active
        if active is None or active.job_id != job_id:
            return False
        cancelled_result = AnalysisResult(
            job_id=active.job_id,
            epoch=active.epoch,
            selection_revision=active.selection_revision,
            stage=active.stage,
            frame_start=active.frame_start,
            frame_stop=active.frame_stop,
            actual_bin=active.requested_bin,
            executed=False,
            status="interrupted",
            error=reason,
        )
        self.scheduler.complete(cancelled_result)
        self.evidence_writer.write_event(ControllerEvent(
            "cancelled_before_execution", reason, self.latest_snapshot.frame_index,
            active.epoch, active.job_id,
        ))
        cancelled = self.controller.cancel_job(job_id, reason)
        self._write_events(cancelled)
        return True

    def _drain_results(self) -> None:
        while True:
            try:
                executed = self._results.get_nowait()
            except queue.Empty:
                return
            if executed.result.job_id in self._tombstoned_jobs:
                with self._worker_state_lock:
                    self._worker_started_jobs.discard(executed.result.job_id)
                    self._tombstoned_jobs.discard(executed.result.job_id)
                continue
            self.scheduler.complete(executed.result)
            self._evidence_by_job[executed.result.job_id] = executed.evidence
            update = self.controller.handle_result(executed.result)
            self._apply_update(update)
            with self._worker_state_lock:
                self._worker_started_jobs.discard(executed.result.job_id)
            self.performance.write(
                "analysis",
                job_id=executed.result.job_id,
                stage=executed.result.stage,
                epoch=executed.result.epoch,
                frame_index=executed.result.frame_stop - 1,
                analysis_status=executed.result.status,
                analysis_elapsed_s=f"{executed.analysis_elapsed_s:.9f}",
                selector_elapsed_s=(
                    "" if executed.selector_elapsed_s is None
                    else f"{executed.selector_elapsed_s:.9f}"
                ),
            )

    def _dispatch(self) -> None:
        if self.scheduler.active is not None or self._work.full():
            return
        job = self.scheduler.claim_next()
        if job is None:
            return
        self.controller.register_dispatched_job(job)
        self._work.put_nowait(job)

    def _controller_loop(self) -> None:
        try:
            while not self._stop.is_set():
                self._drain_results()
                self._dispatch()
                item = self.frame_source.get_frame(timeout_s=0.02)
                source_failure = getattr(self.frame_source, "failure", None)
                if source_failure is not None:
                    raise RuntimeError("frame source failed") from source_failure
                if item is None:
                    starvation_s = (
                        self.settings.monitor_hop_frames / self.settings.frame_rate_hz
                    )
                    if (
                        self._last_frame_delivery_monotonic is not None
                        and not self._starvation_latched
                        and self._monotonic() - self._last_frame_delivery_monotonic
                        >= starvation_s
                    ):
                        self._starvation_latched = True
                        self.extractor.reset()
                        self._apply_update(self.controller.handle_source_timeout())
                    continue
                if self.end_sentinel is not None and item is self.end_sentinel:
                    break
                if len(item) == 3:
                    frame_index, cube, valid = item
                    valid = bool(valid)
                else:
                    frame_index, cube = item
                    valid = self._frame_valid(int(frame_index))
                self._last_frame_delivery_monotonic = self._monotonic()
                self._starvation_latched = False
                arrival_reader = getattr(
                    self.frame_source, "pop_frame_arrival_monotonic", None
                )
                arrival_monotonic = (
                    arrival_reader(int(frame_index)) if callable(arrival_reader) else None
                )
                observation = self._observe(int(frame_index), cube, valid)
                update = self.controller.handle_frame(observation)
                if update.reset_buffers:
                    self.extractor.reset()
                self._apply_update(update)
                controller_lag = (
                    None if arrival_monotonic is None
                    else time.monotonic() - float(arrival_monotonic)
                )
                overflow = int(getattr(self.frame_source, "queue_overflow_count", 0))
                source_queue = getattr(self.frame_source, "_q", None)
                queue_depth = source_queue.qsize() if source_queue is not None else ""
                self.performance.write(
                    "controller", frame_index=int(frame_index), epoch=update.snapshot.epoch,
                    controller_lag_s=(
                        "" if controller_lag is None else f"{controller_lag:.9f}"
                    ),
                    source_queue_overflow_count=overflow, source_queue_depth=queue_depth,
                    rss_bytes=(
                        _rss_bytes() if int(frame_index) % int(self.settings.frame_rate_hz) == 0
                        else ""
                    ),
                )
        except BaseException as exc:
            self.failure = exc
            self._stop.set()
        finally:
            try:
                self._account_for_shutdown()
            except BaseException as cleanup_exc:
                if self.failure is None:
                    self.failure = cleanup_exc
            finally:
                self._stop_source_once()
                self._ended.set()

    def _account_for_shutdown(self) -> None:
        reason = "runtime_failure" if self.failure is not None else "shutdown"
        for event in self.scheduler.close(reason):
            self._write_scheduler_event(event)
            cancelled = self.controller.cancel_job(event.job_id, event.reason)
            self._write_events(cancelled)

        active = self.scheduler.active
        if active is not None:
            self.evidence_writer.write_event(ControllerEvent(
                "active_invalidated", reason, self.latest_snapshot.frame_index,
                active.epoch, active.job_id,
            ))
            self._cancel_active_if_not_started(active.job_id, reason)
        deadline = time.monotonic() + self.shutdown_timeout_s
        while self.scheduler.active is not None and time.monotonic() < deadline:
            self._drain_results()
            time.sleep(0.01)
        self._drain_results()
        active = self.scheduler.active
        if active is not None:
            if self._cancel_active_if_not_started(active.job_id, "shutdown_timeout"):
                return
            self.shutdown_interrupted = True
            with self._worker_state_lock:
                self._tombstoned_jobs.add(active.job_id)
            interrupted = AnalysisResult(
                job_id=active.job_id,
                epoch=active.epoch,
                selection_revision=active.selection_revision,
                stage=active.stage,
                frame_start=active.frame_start,
                frame_stop=active.frame_stop,
                actual_bin=active.requested_bin,
                executed=True,
                status="interrupted",
                error="shutdown_timeout",
            )
            self.scheduler.complete(interrupted)
            self._evidence_by_job[active.job_id] = _exception_evidence(
                active, self.cfg, "shutdown_timeout"
            )
            update = self.controller.handle_result(interrupted)
            self._apply_update(update)
            self.evidence_writer.write_event(ControllerEvent(
                "active_tombstoned", "shutdown_timeout_future_completion_discarded",
                self.latest_snapshot.frame_index, active.epoch, active.job_id,
            ))

    def _stop_source_once(self) -> None:
        if not self._source_stopped:
            try:
                self.frame_source.stop()
            except BaseException as source_stop_exc:
                if self.failure is None:
                    self.failure = source_stop_exc
            finally:
                self._source_stopped = True
            source_failure = getattr(self.frame_source, "failure", None)
            if source_failure is not None and self.failure is None:
                self.failure = RuntimeError("frame source failed during finalization")
                self.failure.__cause__ = source_failure

    def record_ui_snapshot(self) -> None:
        with self._snapshot_lock:
            snapshot = self._snapshot
            measured_age_s = time.monotonic() - self._snapshot_monotonic
        snapshot_age_s = measured_age_s
        self.performance.write(
            "ui", frame_index=snapshot.frame_index, epoch=snapshot.epoch,
            ui_snapshot_age_s=f"{float(snapshot_age_s):.9f}",
        )

    def stop(self, timeout_s: float | None = None) -> None:
        self._stop.set()
        join_timeout = self.shutdown_timeout_s + 1.0 if timeout_s is None else float(timeout_s)
        if self._controller_thread and self._controller_thread.is_alive():
            self._controller_thread.join(timeout=join_timeout)
        self._stop_source_once()
        try:
            self._work.put_nowait(None)
        except queue.Full:
            pass
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=max(0.0, join_timeout))
        source_thread = getattr(self.frame_source, "_thread", None)
        source_alive = bool(source_thread is not None and source_thread.is_alive())
        clean = not self.shutdown_interrupted and self.failure is None and not source_alive and not (
            (self._controller_thread and self._controller_thread.is_alive())
            or (self._worker and self._worker.is_alive())
        )
        self.clean_shutdown = clean
        self.performance.close()
        self._write_performance_meta(
            clean_shutdown=clean,
            run_stop_utc=datetime.now(timezone.utc).isoformat(),
        )

    def _write_performance_meta(
        self, *, clean_shutdown: bool, run_stop_utc: str | None
    ) -> None:
        payload = {
            "schema_version": "live_motion_performance_v1",
            "nominal_frame_rate_hz": self.settings.frame_rate_hz,
            "ordinary_hop_s": (
                self.settings.ordinary_hop_frames / self.settings.frame_rate_hz
            ),
            "run_start_utc": self._run_start_utc,
            "run_stop_utc": run_stop_utc,
            "source_id": self.source_id,
            "source_kind": self.source_kind,
            "checkout_commit": self.checkout_commit,
            "config_sha256": self.evidence_writer.config_hash,
            "source_sha256": self.evidence_writer.source_hash,
            "calibration_sha256": self.evidence_writer.calibration_hash,
            "clean_shutdown": bool(clean_shutdown),
            "frame_arrival_clock_available": callable(getattr(
                self.frame_source, "pop_frame_arrival_monotonic", None
            )),
        }
        temporary = self._meta_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self._meta_path)
