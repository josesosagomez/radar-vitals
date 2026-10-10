"""Calibration-gated movement-recovery replay orchestration.

This module deliberately does not reuse ``LiveMotionRuntime``.  Live acquisition
interprets wall-clock source starvation as a physical/data event, while recorded
playback must treat pacing and pause as controls in recording time.  The accepted
scientific components remain unchanged: frame features, range cache, recovery
controller, bounded scheduler, production analysis, and motion evidence writer.
"""

from __future__ import annotations

import json
import math
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

import numpy as np

from src.live_motion.cache import RangeBinCache
from src.live_motion.calibration import CalibrationError, validate_calibration
from src.live_motion.config import LiveMotionSettings, parse_live_motion_settings
from src.live_motion.controller import (
    AttemptDisposition,
    ControllerEvent,
    ControllerUpdate,
    DisplaySnapshot,
    RecoveryController,
)
from src.live_motion.evidence import AttemptEvidenceWriter
from src.live_motion.features import FrameObservation, RangeFeatureExtractor
from src.live_motion.runtime import (
    ExecutedAnalysis,
    _exception_evidence,
    production_analysis_function,
)
from src.live_motion.scheduler import AnalysisJob, AnalysisResult, BoundedAnalysisScheduler
from src.m2.common import sha256_bytes
from src.window_pipeline import run_config_hash

from .clock import PlaybackClock
from .session import (
    LEGACY_VALIDITY_ID,
    SOURCE_KIND,
    SUPPORTED_CAPTURE_ID,
    ReplaySessionSpec,
    resolve_replay_session,
)
from .source import ProductionFrameDecoder, SequentialAdcSource


ACTIVE_CHECKOUT_ROOT = Path(__file__).resolve().parents[2]


class AdvancedReplayUnavailable(ValueError):
    """Raised before source/UI/worker creation when physical gates do not pass."""


@dataclass(frozen=True)
class AdvancedReplayAuthorization:
    """Validated production authorization for one advanced replay pass."""

    session: ReplaySessionSpec
    _config_json: bytes
    _calibration_record_json: bytes
    calibration_record_path: Path
    calibration_sha256: str
    config_sha256: str
    validity_provenance: str
    source_kind: str

    @property
    def config(self) -> dict[str, Any]:
        return json.loads(self._config_json)

    @property
    def calibration_record(self) -> dict[str, Any]:
        return json.loads(self._calibration_record_json)

    @property
    def calibration_record_bytes(self) -> bytes:
        """Exact file bytes whose SHA-256 authorized this replay."""

        return bytes(self._calibration_record_json)

    @property
    def settings(self) -> LiveMotionSettings:
        settings = parse_live_motion_settings(
            self.config,
            calibration_record=self.calibration_record,
            calibration_record_path=self.calibration_record_path,
            calibration_record_sha256=self.calibration_sha256,
        )
        if settings is None:
            raise AdvancedReplayUnavailable("validated motion settings disappeared")
        return settings


@dataclass(frozen=True)
class AdvancedReplaySnapshot:
    """Immutable radar state exported for composition into the GUI view model."""

    generation: int
    processed_boundary: int
    playback_state: str
    speed: float
    display: DisplaySnapshot
    measurement_window_age_frames: int | None
    analysis_lag_frames: int | None


class AdvancedReplayEventSink(Protocol):
    """Replay-schema event surface; motion attempt evidence stays separate."""

    def write_controller_event(self, event: Any) -> None: ...

    def write_playback_event(self, event: dict[str, Any]) -> None: ...

    def write_performance(self, row: dict[str, Any]) -> None: ...


def authorize_advanced_replay(
    session: ReplaySessionSpec,
    cfg: dict[str, Any],
    *,
    allow_legacy_validity_assumption: bool,
) -> AdvancedReplayAuthorization:
    """Validate the replay-only physical gate using this checkout as authority.

    The public entrypoint intentionally has no root override and no synthetic
    authorization argument.  Tests may instantiate ``AdvancedReplayOrchestrator``
    directly with synthetic settings, but that cannot create this authorization.
    """

    violations: list[str] = []
    if allow_legacy_validity_assumption is not True:
        violations.append(
            "advanced replay requires --allow-legacy-validity-assumption for "
            "this historical capture"
        )
    if session.capture_id != SUPPORTED_CAPTURE_ID:
        violations.append("advanced replay accepts only the registered massimo3 capture")
    if session.capture_key != "m3":
        violations.append("advanced replay requires the committed m3 registry identity")
    if session.source_kind != SOURCE_KIND:
        violations.append("advanced replay source provenance is not recorded_development_replay")
    if (
        session.validity_provenance != LEGACY_VALIDITY_ID
        or session.validity_assumed is not True
    ):
        violations.append(
            "advanced replay requires the strictly checked legacy_zero_loss_inferred_v1 "
            "validity provenance"
        )
    registered_geometry = {
        "num_adc_samples": 256,
        "num_rx": 4,
        "num_chirps_per_frame": 32,
        "iq_swap": True,
    }
    for name, expected in registered_geometry.items():
        if getattr(session, name) != expected:
            violations.append(
                f"session {name} is incompatible with registered massimo3 geometry"
            )
    if session.frame_bytes != 256 * 4 * 32 * 4:
        violations.append("session frame byte geometry is incompatible with massimo3")
    if not _same_finite(session.frame_rate_hz, 20.0):
        violations.append("session frame rate is incompatible with registered massimo3")
    if not _same_finite(session.range_resolution_m, 0.0436):
        violations.append(
            "session range resolution is incompatible with registered massimo3"
        )
    if not _same_finite(session.range_bias_m, 0.0):
        violations.append("session range bias must be historical zero for massimo3")

    section = cfg.get("development_motion")
    if type(section) is not dict:
        violations.append("an explicit development_motion configuration is required")
        section = {}
    enabled = section.get("enabled")
    extended_enabled = section.get("extended_breathing_enabled")
    if enabled is not True:
        violations.append("development movement recovery is disabled")
    if type(extended_enabled) is not bool:
        violations.append("development_motion.extended_breathing_enabled must be Boolean")
    if extended_enabled is True and enabled is not True:
        violations.append("extended breathing requires development movement recovery")

    calibration = section.get("calibration")
    if type(calibration) is not dict:
        violations.append("physical calibration and held-out acceptance are pending")
        calibration = {}
    record_path_value = calibration.get("record_path")
    record_hash_value = calibration.get("record_sha256")
    if not isinstance(record_path_value, str) or not record_path_value:
        violations.append("physical calibration and held-out acceptance are pending")
    if (
        not isinstance(record_hash_value, str)
        or len(record_hash_value) != 64
        or any(character not in "0123456789abcdef" for character in record_hash_value)
    ):
        if "physical calibration and held-out acceptance are pending" not in violations:
            violations.append("physical calibration and held-out acceptance are pending")

    profile = cfg.get("profile")
    session_cfg = cfg.get("session")
    phase = cfg.get("phase")
    if type(profile) is not dict:
        violations.append("motion configuration profile is missing")
        profile = {}
    if type(session_cfg) is not dict:
        violations.append("motion configuration session is missing")
        session_cfg = {}
    if type(phase) is not dict:
        violations.append("motion configuration phase settings are missing")
        phase = {}
    expected_profile = {
        "num_adc_samples": session.num_adc_samples,
        "num_rx": session.num_rx,
        "num_chirps_per_frame": session.num_chirps_per_frame,
        "iq_swap": session.iq_swap,
    }
    for name, expected in expected_profile.items():
        if type(profile.get(name)) is not type(expected) or profile.get(name) != expected:
            violations.append(
                f"motion profile.{name} is incompatible with the registered recording"
            )
    if not _same_finite(profile.get("range_resolution_m"), session.range_resolution_m):
        violations.append(
            "motion profile.range_resolution_m is incompatible with the registered recording"
        )
    if "range_bias_m" not in profile:
        violations.append("motion profile.range_bias_m must be explicit")
    elif not _same_finite(profile.get("range_bias_m"), 0.0):
        configured_bias = profile.get("range_bias_m")
        violations.append(
            "motion profile.range_bias_m must be historical zero for massimo3 "
            f"(configured {configured_bias!r} m is incompatible)"
        )
    if not _same_finite(session_cfg.get("frame_rate_hz"), session.frame_rate_hz):
        violations.append("motion session.frame_rate_hz is incompatible with the recording")
    if phase.get("method") != "delta_before_mean":
        violations.append("motion phase.method must be delta_before_mean")
    if phase.get("clutter_removal", "none") != "none":
        violations.append("motion phase.clutter_removal must be none")
    _validate_recorded_acquisition_profile(session, cfg, violations)

    if violations:
        raise AdvancedReplayUnavailable("; ".join(dict.fromkeys(violations)))

    try:
        record = validate_calibration(cfg, root=ACTIVE_CHECKOUT_ROOT)
    except (CalibrationError, KeyError, TypeError, ValueError, OSError) as exc:
        raise AdvancedReplayUnavailable(
            f"accepted physical calibration validation failed: {exc}"
        ) from exc

    capabilities = record.get("candidate", {}).get("capabilities")
    if not isinstance(capabilities, list):
        capabilities = record.get("capabilities")
    if not isinstance(capabilities, list) or "movement" not in capabilities:
        raise AdvancedReplayUnavailable(
            "accepted calibration lacks the movement recovery capability"
        )
    if extended_enabled and "extended_breathing" not in capabilities:
        raise AdvancedReplayUnavailable(
            "accepted calibration lacks the extended_breathing capability"
        )

    record_path = Path(record_path_value)
    if not record_path.is_absolute():
        record_path = ACTIVE_CHECKOUT_ROOT / record_path
    try:
        resolved_record_path = record_path.resolve(strict=True)
        record_bytes, record = _snapshot_validated_calibration(
            resolved_record_path,
            record_hash_value,
            record,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise AdvancedReplayUnavailable(
            f"accepted physical calibration snapshot failed: {exc}"
        ) from exc
    try:
        settings = parse_live_motion_settings(
            cfg,
            calibration_record=record,
            calibration_record_path=resolved_record_path,
            calibration_record_sha256=record_hash_value,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise AdvancedReplayUnavailable(
            f"validated calibration could not produce motion settings: {exc}"
        ) from exc
    if settings is None or not settings.enabled:
        raise AdvancedReplayUnavailable("development movement recovery is disabled")

    # Re-resolve from the guarded registry after the physical calibration gate.
    # This prevents a hand-built ReplaySessionSpec from becoming production
    # authority merely because its scalar fields look like massimo3.
    try:
        development_data_root = session.capture_dir.parents[2]
        rebound_session = resolve_replay_session(
            SUPPORTED_CAPTURE_ID,
            development_data_root=development_data_root,
            mode="motion",
            allow_legacy_validity_assumption=True,
        )
    except (OSError, TypeError, ValueError) as exc:
        raise AdvancedReplayUnavailable(
            f"registered replay session reauthorization failed: {exc}"
        ) from exc
    if rebound_session != session:
        raise AdvancedReplayUnavailable(
            "supplied replay session differs from the guarded registered session"
        )

    return AdvancedReplayAuthorization(
        session=session,
        _config_json=_canonical_json_bytes(cfg),
        _calibration_record_json=record_bytes,
        calibration_record_path=resolved_record_path,
        calibration_sha256=record_hash_value,
        config_sha256=run_config_hash(cfg),
        validity_provenance=session.validity_provenance,
        source_kind=session.source_kind,
    )


class AdvancedReplayOrchestrator:
    """Frame-domain replay runtime around unchanged motion-recovery components.

    Synthetic tests may construct this class directly with validated test
    ``LiveMotionSettings``.  Production callers first obtain an
    :class:`AdvancedReplayAuthorization`; no CLI option exposes this lower-level
    constructor as an authorization bypass.
    """

    def __init__(
        self,
        *,
        source: Any,
        cfg: dict[str, Any],
        settings: LiveMotionSettings,
        clock: PlaybackClock,
        frame_limit: int,
        evidence_writer: AttemptEvidenceWriter,
        event_sink: AdvancedReplayEventSink,
        generation: int = 0,
        frame_validity: Callable[[int], bool] | None = None,
        analysis_function: Callable[[AnalysisJob], ExecutedAnalysis] | None = None,
        snapshot_observer: Callable[[AdvancedReplaySnapshot], None] | None = None,
        shutdown_timeout_s: float = 5.0,
    ):
        if not settings.enabled:
            raise ValueError("advanced replay requires enabled motion settings")
        if settings.presence_thresholds is None or settings.movement_thresholds is None:
            raise ValueError("advanced replay requires accepted physical thresholds")
        if type(frame_limit) is not int or frame_limit <= 0:
            raise ValueError("frame_limit must be a positive exact integer")
        if type(generation) is not int or generation < 0:
            raise ValueError("generation must be a nonnegative exact integer")
        if not math.isfinite(shutdown_timeout_s) or shutdown_timeout_s <= 0:
            raise ValueError("shutdown_timeout_s must be finite and positive")

        self.source = source
        self.cfg = cfg
        self.settings = settings
        self.clock = clock
        self.frame_limit = frame_limit
        self.evidence_writer = evidence_writer
        self.event_sink = event_sink
        self.generation = generation
        self.frame_validity = frame_validity or (lambda _index: True)
        self.analysis_function = analysis_function or production_analysis_function(
            cfg, settings
        )
        self.snapshot_observer = snapshot_observer
        self.shutdown_timeout_s = float(shutdown_timeout_s)

        profile = cfg.get("profile", {})
        self.extractor = RangeFeatureExtractor(cfg)
        self.cache = RangeBinCache(
            settings.extended_breathing_window_frames,
            int(profile["num_chirps_per_frame"]),
            int(profile["num_rx"]),
            settings.candidate_bins,
        )
        self.controller = RecoveryController(settings, self.cache)
        self.scheduler = BoundedAnalysisScheduler()

        self._work: queue.Queue[AnalysisJob | None] = queue.Queue(maxsize=1)
        self._results: queue.Queue[ExecutedAnalysis] = queue.Queue(maxsize=1)
        self._commands: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._execution_condition = threading.Condition()
        self._execution_paused = False
        self._worker_started: set[str] = set()
        self._worker_state_lock = threading.Lock()
        self._tombstoned: set[str] = set()
        self._evidence_by_job: dict[str, dict[str, Any]] = {}
        self._dispatched_jobs: dict[str, AnalysisJob] = {}
        self._stop = threading.Event()
        self._ended = threading.Event()
        self._worker: threading.Thread | None = None
        self._controller_thread: threading.Thread | None = None
        self._snapshot_lock = threading.Lock()
        self._processed_boundary = 0
        self._playback_state = "running"
        self._latest_published_stop: int | None = None
        self._latest_result_stop: int | None = None
        self._denominator_frozen = False
        self._denominator_event_written = False
        self._snapshot = self._compose_snapshot(self.controller.snapshot())
        self.failure: BaseException | None = None
        self.clean_shutdown = False
        self.shutdown_interrupted = False

    @classmethod
    def from_authorization(
        cls,
        authorization: AdvancedReplayAuthorization,
        **kwargs: Any,
    ) -> "AdvancedReplayOrchestrator":
        """Production constructor requiring a successful physical preflight."""

        # Re-run the physical gate at runtime construction.  The frozen
        # authorization stores canonical bytes, not caller-mutable dictionaries.
        refreshed = authorize_advanced_replay(
            authorization.session,
            authorization.config,
            allow_legacy_validity_assumption=True,
        )
        if refreshed.calibration_sha256 != authorization.calibration_sha256:
            raise AdvancedReplayUnavailable("calibration identity changed after preflight")
        return cls._from_refreshed_authorization(refreshed, **kwargs)

    @classmethod
    def _from_refreshed_authorization(
        cls,
        authorization: AdvancedReplayAuthorization,
        **kwargs: Any,
    ) -> "AdvancedReplayOrchestrator":
        """Build from an authorization refreshed immediately by the app manager."""

        if "analysis_function" in kwargs and kwargs["analysis_function"] is not None:
            raise AdvancedReplayUnavailable(
                "production advanced replay cannot override the analysis function"
            )
        if "frame_validity" in kwargs and kwargs["frame_validity"] is not None:
            raise AdvancedReplayUnavailable(
                "production advanced replay cannot override registered frame validity"
            )
        kwargs.pop("analysis_function", None)
        kwargs.pop("frame_validity", None)
        supplied_cfg = kwargs.pop("cfg", None)
        supplied_differs = (
            supplied_cfg is not None
            and _canonical_json_bytes(supplied_cfg)
            != _canonical_json_bytes(authorization.config)
        )
        if supplied_differs:
            raise AdvancedReplayUnavailable(
                "runtime motion configuration differs from the authorized configuration"
            )
        source = kwargs.get("source")
        _validate_production_source(source, authorization.session)
        frame_limit = kwargs.get("frame_limit")
        if (
            type(frame_limit) is not int
            or frame_limit <= 0
            or frame_limit > authorization.session.frame_count
        ):
            raise AdvancedReplayUnavailable(
                "production frame_limit must lie within the authorized capture"
            )
        evidence_writer = kwargs.get("evidence_writer")
        _validate_production_evidence_writer(evidence_writer, authorization)
        return cls(
            settings=authorization.settings,
            cfg=authorization.config,
            frame_validity=None,
            analysis_function=None,
            **kwargs,
        )

    @property
    def latest_snapshot(self) -> AdvancedReplaySnapshot:
        with self._snapshot_lock:
            return self._snapshot

    @property
    def ended(self) -> bool:
        return self._ended.is_set()

    def start(self) -> None:
        if self._controller_thread is not None:
            raise RuntimeError("advanced replay orchestrator can only be started once")
        self._worker = threading.Thread(
            target=self._worker_loop,
            name=f"ReplayMotionAnalysis-{self.generation}",
            daemon=True,
        )
        self._controller_thread = threading.Thread(
            target=self._controller_loop,
            name=f"ReplayMotionController-{self.generation}",
            daemon=True,
        )
        self._worker.start()
        self._controller_thread.start()

    def request_pause(self) -> None:
        self._commands.put(("pause", None))

    def resume(self) -> None:
        self._commands.put(("resume", None))

    def set_speed(self, speed: float) -> None:
        self._commands.put(("speed", float(speed)))

    def request_stop(self) -> None:
        self._stop.set()
        with self._execution_condition:
            self._execution_paused = False
            self._execution_condition.notify_all()

    def stop(self, timeout_s: float | None = None) -> None:
        self.request_stop()
        wait_s = self.shutdown_timeout_s + 1.0 if timeout_s is None else float(timeout_s)
        if self._controller_thread is not None:
            self._controller_thread.join(timeout=max(0.0, wait_s))
        try:
            self._work.put_nowait(None)
        except queue.Full:
            pass
        if self._worker is not None:
            self._worker.join(timeout=max(0.0, wait_s))
        self.clean_shutdown = bool(
            not self.shutdown_interrupted
            and self.failure is None
            and self._controller_thread is not None
            and not self._controller_thread.is_alive()
            and self._worker is not None
            and not self._worker.is_alive()
        )

    def _worker_loop(self) -> None:
        while True:
            job = self._work.get()
            if job is None:
                return
            with self._execution_condition:
                while self._execution_paused and not self._stop.is_set():
                    self._execution_condition.wait(timeout=0.05)
                if self._stop.is_set():
                    # The controller accounts for this claimed-but-unopened
                    # request as cancelled-before-execution during shutdown.
                    return
                # This start boundary is atomic with pause acknowledgement.
                # The controller sets _execution_paused while holding the same
                # condition, so it cannot acknowledge pause between this final
                # check and classification of the job as already active.
                with self._worker_state_lock:
                    if job.job_id in self._tombstoned:
                        self._tombstoned.discard(job.job_id)
                        continue
                    self._worker_started.add(job.job_id)
            started = time.monotonic()
            try:
                executed = self.analysis_function(job)
            except Exception as exc:
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
                    evidence=_exception_evidence(job, self.cfg, reason, job.requested_bin),
                    analysis_elapsed_s=time.monotonic() - started,
                    selector_elapsed_s=(
                        time.monotonic() - started
                        if job.selection_mode == "select"
                        else None
                    ),
                )
            while True:
                if job.job_id in self._tombstoned:
                    with self._worker_state_lock:
                        self._worker_started.discard(job.job_id)
                        self._tombstoned.discard(job.job_id)
                    break
                try:
                    self._results.put(executed, timeout=0.05)
                    break
                except queue.Full:
                    continue

    def _controller_loop(self) -> None:
        entered = False
        try:
            self.source.__enter__()
            entered = True
            self.event_sink.write_playback_event({
                "kind": "source_prepared",
                "generation": self.generation,
                "boundary": 0,
                "adc_sha256": getattr(self.source, "initial_sha256", ""),
                "file_identity": getattr(self.source, "opened_file_identity", {}),
            })
            self.clock.reanchor()
            while (
                not self._stop.is_set()
                and self._processed_boundary < self.frame_limit
            ):
                self._handle_commands()
                clock_state = self.clock.snapshot.state
                if clock_state in {"pausing", "paused"}:
                    if clock_state == "pausing":
                        self.clock.acknowledge_pause()
                        self._playback_state = "paused"
                        self._publish_snapshot(self.controller.snapshot())
                        self.event_sink.write_playback_event({
                            "kind": "pause_acknowledged",
                            "generation": self.generation,
                            "boundary": self._processed_boundary,
                        })
                    time.sleep(0.01)
                    continue

                self._dispatch()
                if not self.clock.wait_until_next_frame():
                    continue
                if self._stop.is_set():
                    break
                item = self.source.read_next()
                if self._stop.is_set():
                    if item is not None:
                        self.event_sink.write_playback_event({
                            "kind": "read_ahead_discarded_at_stop",
                            "generation": self.generation,
                            "boundary": self._processed_boundary,
                            "source_frames_read": getattr(self.source, "frames_read", None),
                        })
                    break
                if item is None:
                    break
                frame_index, cube = item
                if frame_index != self._processed_boundary:
                    raise RuntimeError(
                        "advanced replay source frame index disagrees with processed boundary"
                    )
                valid = self.frame_validity(int(frame_index))
                if type(valid) is not bool:
                    raise TypeError("frame_validity must return an exact bool")
                observation = self._observe(int(frame_index), cube, valid)
                # Physical/data state always applies before a same-boundary result.
                update = self.controller.handle_frame(observation)
                if update.reset_buffers:
                    self.extractor.reset()
                self._processed_boundary = int(frame_index) + 1
                self.clock.frame_processed(self._processed_boundary)
                self._apply_update(update)
                self._drain_results()
                self.event_sink.write_performance({
                    "kind": "controller",
                    "generation": self.generation,
                    "frame_index": int(frame_index),
                    "epoch": update.snapshot.epoch,
                    "pending_mandatory": self.scheduler.pending_counts[0],
                    "pending_rolling": self.scheduler.pending_counts[1],
                    "worker_active": self.scheduler.active is not None,
                })
            self._denominator_frozen = True
            self._playback_state = (
                "stopped" if self._stop.is_set() else "complete"
            )
            self.event_sink.write_playback_event({
                "kind": "denominator_frozen",
                "generation": self.generation,
                "boundary": self._processed_boundary,
                "completion_state": self._playback_state,
            })
            self._denominator_event_written = True
            self._publish_snapshot(self.controller.snapshot())
            self._shutdown_scheduler()
        except BaseException as exc:
            self.failure = exc
            self._denominator_frozen = True
            self._playback_state = "failed"
            with self._snapshot_lock:
                last_durable_display = self._snapshot.display
            try:
                if self._denominator_event_written:
                    self.event_sink.write_playback_event({
                        "kind": "replay_failed_after_denominator_frozen",
                        "generation": self.generation,
                        "boundary": self._processed_boundary,
                        "reason": f"{type(exc).__name__}: {exc}",
                    })
                else:
                    self.event_sink.write_playback_event({
                        "kind": "denominator_frozen",
                        "generation": self.generation,
                        "boundary": self._processed_boundary,
                        "completion_state": "failed",
                        "reason": f"{type(exc).__name__}: {exc}",
                    })
                    self._denominator_event_written = True
            except BaseException:
                # Preserve the original runtime failure as the primary cause.
                pass
            self._publish_snapshot(last_durable_display)
            self._stop.set()
            try:
                self._shutdown_scheduler()
            except BaseException as cleanup_exc:
                if self.failure is None:
                    self.failure = cleanup_exc
        finally:
            with self._execution_condition:
                self._execution_paused = False
                self._execution_condition.notify_all()
            if entered:
                try:
                    # Integrity is checked for complete and partial/stopped passes.
                    self.source.verify_integrity()
                except BaseException as integrity_exc:
                    if self.failure is None:
                        self.failure = integrity_exc
                        self._playback_state = "failed"
                        try:
                            self.event_sink.write_playback_event({
                                "kind": "replay_failed_after_denominator_frozen",
                                "generation": self.generation,
                                "boundary": self._processed_boundary,
                                "reason": (
                                    f"{type(integrity_exc).__name__}: {integrity_exc}"
                                ),
                            })
                        except BaseException:
                            pass
                        with self._snapshot_lock:
                            last_durable_display = self._snapshot.display
                        self._publish_snapshot(last_durable_display)
            try:
                self._work.put_nowait(None)
            except queue.Full:
                pass
            self._ended.set()

    def _observe(self, frame_index: int, cube: np.ndarray, valid: bool) -> FrameObservation:
        try:
            return self.extractor.observe(
                frame_index,
                cube,
                valid=valid,
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

    def _handle_commands(self) -> None:
        while True:
            try:
                command, value = self._commands.get_nowait()
            except queue.Empty:
                return
            if command == "pause":
                with self._execution_condition:
                    self._execution_paused = True
                    self.clock.request_pause()
                self._playback_state = "pausing"
                self._publish_snapshot(self.controller.snapshot())
                self.event_sink.write_playback_event({
                    "kind": "pause_requested",
                    "generation": self.generation,
                    "boundary": self._processed_boundary,
                })
            elif command == "resume":
                with self._execution_condition:
                    self.clock.resume()
                    self._execution_paused = False
                    self._execution_condition.notify_all()
                self._playback_state = "running"
                self._publish_snapshot(self.controller.snapshot())
                self.event_sink.write_playback_event({
                    "kind": "resumed",
                    "generation": self.generation,
                    "boundary": self._processed_boundary,
                })
            elif command == "speed":
                self.clock.set_speed(value)
                self._publish_snapshot(self.controller.snapshot())
                self.event_sink.write_playback_event({
                    "kind": "speed_changed",
                    "generation": self.generation,
                    "boundary": self._processed_boundary,
                    "speed": self.clock.snapshot.speed,
                })

    def _apply_update(self, update: ControllerUpdate) -> None:
        for event in update.events:
            self.evidence_writer.write_event(event)
            self.event_sink.write_controller_event(event)
        for resolution in update.dependency_resolutions:
            resolved = self.scheduler.resolve_dependency(
                resolution.dependency_job_id,
                selected_bin=resolution.selected_bin,
                selection_revision=resolution.selection_revision,
            )
            self.event_sink.write_controller_event({
                "kind": "dependency_resolved",
                "dependency_job_id": resolution.dependency_job_id,
                "resolved_job_ids": resolved,
                "selected_bin": resolution.selected_bin,
                "selection_revision": resolution.selection_revision,
                "boundary": self._processed_boundary,
                "generation": self.generation,
            })
        for attempt in update.attempts:
            evidence = self._evidence_by_job.pop(attempt.result.job_id)
            self.evidence_writer.write_attempt(
                attempt, attempt.snapshot or update.snapshot, evidence
            )
            requested = self._dispatched_jobs.pop(attempt.result.job_id, None)
            if requested is None:
                raise RuntimeError("applied advanced result lacks its dispatched request")
            self._write_application_event(attempt, requested, evidence)
            self._latest_result_stop = attempt.result.frame_stop
            if attempt.disposition == "published":
                self._latest_published_stop = attempt.result.frame_stop
        for job in update.jobs:
            scheduler_events = self.scheduler.submit(job)
            for event in scheduler_events:
                self._write_scheduler_event(event)
                cancelled = self.controller.cancel_job(event.job_id, event.reason)
                self._write_controller_update_events(cancelled)
            self.event_sink.write_controller_event({
                "kind": "analysis_requested",
                "generation": self.generation,
                "application_boundary": self._processed_boundary,
                "job_id": job.job_id,
                "epoch": job.epoch,
                "selection_revision": job.selection_revision,
                "stage": job.stage,
                "frame_start": job.frame_start,
                "frame_stop": job.frame_stop,
                "requested_bin": job.requested_bin,
                "selection_mode": job.selection_mode,
                "depends_on": job.depends_on,
            })
        if update.reset_buffers:
            old_epoch = update.events[0].epoch if update.events else update.snapshot.epoch
            for event in self.scheduler.cancel_epoch(old_epoch, update.snapshot.reason):
                self._write_scheduler_event(event)
                if event.kind == "cancelled_before_execution":
                    cancelled = self.controller.cancel_job(event.job_id, event.reason)
                    self._write_controller_update_events(cancelled)
                elif event.kind == "active_invalidated":
                    self._cancel_active_if_not_started(event.job_id, event.reason)
        # Attempt arrays and application/controller events are durable before a
        # fresh display snapshot becomes visible to the GUI thread.
        self._publish_snapshot(update.snapshot)

    def _write_controller_update_events(self, update: ControllerUpdate) -> None:
        for event in update.events:
            self.evidence_writer.write_event(event)
            self.event_sink.write_controller_event(event)

    def _write_scheduler_event(self, event: Any) -> None:
        snapshot = self.controller.snapshot()
        controller_event = ControllerEvent(
            kind=str(event.kind),
            reason=str(event.reason),
            frame_index=snapshot.frame_index,
            epoch=int(event.epoch if event.epoch is not None else snapshot.epoch),
            job_id=str(event.job_id),
        )
        self.evidence_writer.write_event(controller_event)
        replay_kind = (
            "cancelled_before_execution"
            if event.kind == "rolling_coalesced"
            else str(event.kind)
        )
        self.event_sink.write_controller_event({
            "kind": replay_kind,
            "scheduler_kind": str(event.kind),
            "generation": self.generation,
            "application_boundary": self._processed_boundary,
            "job_id": str(event.job_id),
            "reason": str(event.reason),
            "replacement_job_id": getattr(event, "replacement_job_id", None),
            "epoch": event.epoch,
        })

    def _dispatch(self) -> None:
        if self.scheduler.active is not None or self._work.full():
            return
        job = self.scheduler.claim_next()
        if job is None:
            return
        self.controller.register_dispatched_job(job)
        self._dispatched_jobs[job.job_id] = job
        self._work.put_nowait(job)

    def _drain_results(self) -> None:
        while True:
            try:
                executed = self._results.get_nowait()
            except queue.Empty:
                return
            if executed.result.job_id in self._tombstoned:
                with self._worker_state_lock:
                    self._worker_started.discard(executed.result.job_id)
                    self._tombstoned.discard(executed.result.job_id)
                continue
            requested = self.scheduler.active
            if requested is None:
                raise RuntimeError("advanced result arrived without an active request")
            executed = self._validate_worker_return(requested, executed)
            if self._denominator_frozen:
                self._persist_frozen_result(requested, executed)
                with self._worker_state_lock:
                    self._worker_started.discard(executed.result.job_id)
                continue
            # Scheduler ownership is released before the controller can publish.
            self.scheduler.complete(executed.result)
            self._evidence_by_job[executed.result.job_id] = executed.evidence
            update = self.controller.handle_result(executed.result)
            self._apply_update(update)
            with self._worker_state_lock:
                self._worker_started.discard(executed.result.job_id)
            self.event_sink.write_performance({
                "kind": "analysis",
                "generation": self.generation,
                "job_id": executed.result.job_id,
                "stage": executed.result.stage,
                "epoch": executed.result.epoch,
                "frame_stop": executed.result.frame_stop,
                "analysis_status": executed.result.status,
                "analysis_elapsed_s": executed.analysis_elapsed_s,
                "selector_elapsed_s": executed.selector_elapsed_s,
                "application_boundary": self._processed_boundary,
            })

    def _persist_frozen_result(
        self, requested: AnalysisJob, executed: ExecutedAnalysis
    ) -> None:
        """Evidence a late completion without mutating selection or display history."""

        self.scheduler.complete(executed.result)
        cancelled = self.controller.cancel_job(
            requested.job_id, "playback_denominator_frozen"
        )
        self._write_controller_update_events(cancelled)
        snapshot = self.controller.snapshot()
        attempt = AttemptDisposition(
            result=executed.result,
            disposition="expired",
            reason="playback_denominator_frozen",
            hr_accepted=False,
            br_accepted=False,
            quiet_assessment_accepted=False,
            selection_applied=False,
            selection_revision=snapshot.selection_revision,
            snapshot=snapshot,
        )
        self.evidence_writer.write_attempt(attempt, snapshot, executed.evidence)
        stored_request = self._dispatched_jobs.pop(requested.job_id, None)
        if stored_request is None:
            raise RuntimeError("late advanced result lacks its dispatched request")
        self._write_application_event(attempt, stored_request, executed.evidence)
        self.event_sink.write_performance({
            "kind": "analysis",
            "generation": self.generation,
            "job_id": executed.result.job_id,
            "stage": executed.result.stage,
            "epoch": executed.result.epoch,
            "frame_stop": executed.result.frame_stop,
            "analysis_status": executed.result.status,
            "analysis_elapsed_s": executed.analysis_elapsed_s,
            "selector_elapsed_s": executed.selector_elapsed_s,
            "application_boundary": self._processed_boundary,
            "late_after_denominator_frozen": True,
        })

    def _validate_worker_return(
        self, requested: AnalysisJob, executed: ExecutedAnalysis
    ) -> ExecutedAnalysis:
        """Fail malformed worker returns while preserving their exact evidence."""

        returned = executed.result
        comparisons = {
            "job_id": returned.job_id == requested.job_id,
            "epoch": returned.epoch == requested.epoch,
            "selection_revision": (
                returned.selection_revision == requested.selection_revision
            ),
            "stage": returned.stage == requested.stage,
            "frame_start": returned.frame_start == requested.frame_start,
            "frame_stop": returned.frame_stop == requested.frame_stop,
            "actual_bin": (
                returned.actual_bin == requested.requested_bin
                or requested.selection_mode == "select"
            ),
        }
        mismatches = tuple(name for name, matches in comparisons.items() if not matches)
        if mismatches:
            return self._failed_worker_return(
                requested,
                executed,
                reason="analysis_result_identity_mismatch:" + ",".join(mismatches),
                mismatch_fields=mismatches,
                actual_bin=requested.requested_bin,
            )

        admission_error = _native_hr_admission_error(returned)
        if admission_error:
            return self._failed_worker_return(
                requested,
                executed,
                reason=admission_error,
                mismatch_fields=(),
                actual_bin=returned.actual_bin,
            )
        return executed

    def _failed_worker_return(
        self,
        requested: AnalysisJob,
        executed: ExecutedAnalysis,
        *,
        reason: str,
        mismatch_fields: tuple[str, ...],
        actual_bin: int | None,
    ) -> ExecutedAnalysis:
        returned = executed.result
        # The required motion schema describes the lifecycle failure.  Every
        # scientific array returned by the worker remains present under an
        # explicit returned_evidence__ prefix, with returned identity alongside.
        evidence = _exception_evidence(requested, self.cfg, reason, actual_bin)
        for name, value in executed.evidence.items():
            evidence[f"returned_evidence__{name}"] = value
        evidence.update({
            "result_identity_mismatch": np.asarray(bool(mismatch_fields), dtype=np.bool_),
            "result_identity_mismatch_fields": np.asarray(mismatch_fields, dtype="U"),
            "returned_job_id": np.asarray(returned.job_id),
            "returned_epoch": np.asarray(returned.epoch, dtype=np.int64),
            "returned_selection_revision": np.asarray(
                returned.selection_revision, dtype=np.int64
            ),
            "returned_stage": np.asarray(returned.stage),
            "returned_frame_start": np.asarray(returned.frame_start, dtype=np.int64),
            "returned_frame_stop": np.asarray(returned.frame_stop, dtype=np.int64),
            "returned_actual_bin": np.asarray(
                -1 if returned.actual_bin is None else returned.actual_bin,
                dtype=np.int64,
            ),
            "native_hr_admission_failure": np.asarray(
                "" if mismatch_fields else reason
            ),
        })
        lifecycle_result = AnalysisResult(
            job_id=requested.job_id,
            epoch=requested.epoch,
            selection_revision=requested.selection_revision,
            stage=requested.stage,
            frame_start=requested.frame_start,
            frame_stop=requested.frame_stop,
            actual_bin=actual_bin,
            executed=True,
            status="failed",
            error=reason,
        )
        return ExecutedAnalysis(
            result=lifecycle_result,
            evidence=evidence,
            analysis_elapsed_s=executed.analysis_elapsed_s,
            selector_elapsed_s=executed.selector_elapsed_s,
        )

    def _write_application_event(
        self,
        attempt: Any,
        requested: AnalysisJob,
        evidence: dict[str, Any],
    ) -> None:
        snapshot = attempt.snapshot or self.controller.snapshot()
        record = {
            "kind": "result_applied",
            "generation": self.generation,
            "application_boundary": self._processed_boundary,
            "job_id": attempt.result.job_id,
            "disposition": attempt.disposition,
            "reason": attempt.reason,
            "request_epoch": requested.epoch,
            "request_selection_revision": requested.selection_revision,
            "request_stage": requested.stage,
            "request_frame_start": requested.frame_start,
            "request_frame_stop": requested.frame_stop,
            "requested_bin": requested.requested_bin,
            "selection_mode": requested.selection_mode,
            "applied_epoch": snapshot.epoch,
            "applied_selection_revision": snapshot.selection_revision,
            "selected_bin": snapshot.locked_bin,
            "signal_frame_start": attempt.result.frame_start,
            "signal_frame_stop": attempt.result.frame_stop,
            "hr_window_start": _evidence_exact_int(evidence, "hr_window_start"),
            "br_window_start": _evidence_exact_int(evidence, "br_window_start"),
            "hr_accepted": bool(attempt.hr_accepted),
            "br_accepted": bool(attempt.br_accepted),
            "quiet_accepted": bool(attempt.quiet_assessment_accepted),
            "hr_state": snapshot.hr.state,
            "br_state": snapshot.br.state,
            "display_hr_bpm": snapshot.hr.value_bpm,
            "display_br_bpm": snapshot.br.value_bpm,
        }
        writer = getattr(self.event_sink, "write_application_event", None)
        if callable(writer):
            writer(record)
        else:
            self.event_sink.write_controller_event(record)

    def _cancel_active_if_not_started(self, job_id: str, reason: str) -> bool:
        with self._worker_state_lock:
            if job_id in self._worker_started:
                return False
            self._tombstoned.add(job_id)
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
        requested = self._dispatched_jobs.pop(job_id, None)
        event = ControllerEvent(
            "cancelled_before_execution",
            reason,
            self.controller.snapshot().frame_index,
            active.epoch,
            active.job_id,
        )
        self.evidence_writer.write_event(event)
        if requested is None:
            raise RuntimeError("cancelled advanced job lacks its dispatched request")
        self.event_sink.write_controller_event({
            "kind": "cancelled_before_execution",
            "generation": self.generation,
            "application_boundary": self._processed_boundary,
            "job_id": requested.job_id,
            "reason": reason,
            "epoch": requested.epoch,
            "selection_revision": requested.selection_revision,
            "stage": requested.stage,
            "frame_start": requested.frame_start,
            "frame_stop": requested.frame_stop,
            "requested_bin": requested.requested_bin,
            "selection_mode": requested.selection_mode,
        })
        cancelled = self.controller.cancel_job(job_id, reason)
        self._write_controller_update_events(cancelled)
        return True

    def _shutdown_scheduler(self) -> None:
        with self._execution_condition:
            self._execution_paused = False
            self._execution_condition.notify_all()
        reason = "runtime_failure" if self.failure is not None else "playback_finished"
        for event in self.scheduler.close(reason):
            self._write_scheduler_event(event)
            cancelled = self.controller.cancel_job(event.job_id, event.reason)
            self._write_controller_update_events(cancelled)

        active = self.scheduler.active
        if active is not None:
            event = ControllerEvent(
                "active_invalidated",
                reason,
                self.controller.snapshot().frame_index,
                active.epoch,
                active.job_id,
            )
            self.evidence_writer.write_event(event)
            self.event_sink.write_controller_event(event)
            self._cancel_active_if_not_started(active.job_id, reason)
        deadline = time.monotonic() + self.shutdown_timeout_s
        while self.scheduler.active is not None and time.monotonic() < deadline:
            self._drain_results()
            time.sleep(0.01)
        self._drain_results()
        active = self.scheduler.active
        if active is None:
            return
        if self._cancel_active_if_not_started(active.job_id, "shutdown_timeout"):
            return

        self.shutdown_interrupted = True
        with self._worker_state_lock:
            self._tombstoned.add(active.job_id)
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
        synthetic = ExecutedAnalysis(
            result=interrupted,
            evidence=_exception_evidence(
                active, self.cfg, "shutdown_timeout", active.requested_bin
            ),
            analysis_elapsed_s=self.shutdown_timeout_s,
            selector_elapsed_s=None,
        )
        self._persist_frozen_result(active, synthetic)
        event = ControllerEvent(
            "active_tombstoned",
            "shutdown_timeout_future_completion_discarded",
            self.controller.snapshot().frame_index,
            active.epoch,
            active.job_id,
        )
        self.evidence_writer.write_event(event)
        self.event_sink.write_controller_event(event)
        raise RuntimeError("advanced replay analysis worker did not stop within five seconds")

    def _publish_snapshot(self, display: DisplaySnapshot) -> None:
        snapshot = self._compose_snapshot(display)
        # Reference, coverage, and trace composition runs on this controller
        # thread.  Qt consumers only read the resulting immutable cached view.
        if self.snapshot_observer is not None:
            self.snapshot_observer(snapshot)
        with self._snapshot_lock:
            self._snapshot = snapshot

    def _compose_snapshot(self, display: DisplaySnapshot) -> AdvancedReplaySnapshot:
        measurement_age = (
            None
            if self._latest_published_stop is None
            else max(0, self._processed_boundary - self._latest_published_stop)
        )
        analysis_lag = (
            None
            if self._latest_result_stop is None
            else max(0, self._processed_boundary - self._latest_result_stop)
        )
        return AdvancedReplaySnapshot(
            generation=self.generation,
            processed_boundary=self._processed_boundary,
            playback_state=self._playback_state,
            speed=self.clock.snapshot.speed,
            display=display,
            measurement_window_age_frames=measurement_age,
            analysis_lag_frames=analysis_lag,
        )


def _same_finite(value: Any, expected: float) -> bool:
    return (
        type(value) in (int, float)
        and math.isfinite(float(value))
        and float(value) == float(expected)
    )


def _validate_recorded_acquisition_profile(
    session: ReplaySessionSpec,
    cfg: dict[str, Any],
    violations: list[str],
) -> None:
    """Bind RF/chirp timing to the configuration recorded with the ADC bytes."""

    original = session.original_config
    effective = session.effective_config
    for section_name in ("hw_profile", "hw_frame"):
        recorded = original.get(section_name)
        effective_recorded = effective.get(section_name)
        if recorded is None and effective_recorded is None:
            # Older synthetic contracts may contain only decode geometry.  Real
            # massimo3 metadata contains both sections and therefore takes the
            # strict branch below.
            continue
        if type(recorded) is not dict or type(effective_recorded) is not dict:
            violations.append(
                f"recorded {section_name} acquisition identity is incomplete"
            )
            continue
        if not _acquisition_values_equal(recorded, effective_recorded):
            violations.append(
                f"recorded original/effective {section_name} identities disagree"
            )
            continue
        configured = cfg.get(section_name)
        if type(configured) is not dict:
            violations.append(
                f"motion {section_name} must explicitly match the recorded capture"
            )
            continue
        if not _acquisition_values_equal(recorded, configured):
            violations.append(
                f"motion {section_name} is incompatible with the recorded capture"
            )

    original_capture = original.get("capture")
    effective_capture = effective.get("capture")
    if type(original_capture) is dict and type(effective_capture) is dict:
        recorded_format = original_capture.get("raw_stream_format")
        effective_format = effective_capture.get("raw_stream_format")
        metadata_format = session.metadata.get("raw_stream_format")
        configured_capture = cfg.get("capture")
        configured_format = (
            configured_capture.get("raw_stream_format")
            if type(configured_capture) is dict
            else None
        )
        if (
            not isinstance(recorded_format, str)
            or recorded_format != effective_format
            or recorded_format != metadata_format
            or configured_format != recorded_format
        ):
            violations.append(
                "motion capture.raw_stream_format is incompatible with the recording"
            )


def _acquisition_values_equal(recorded: Any, configured: Any) -> bool:
    if isinstance(recorded, bool) or isinstance(configured, bool):
        return type(recorded) is bool and type(configured) is bool and recorded == configured
    if isinstance(recorded, (int, float)) and isinstance(configured, (int, float)):
        return (
            math.isfinite(float(recorded))
            and math.isfinite(float(configured))
            and float(recorded) == float(configured)
        )
    if type(recorded) is dict and type(configured) is dict:
        return set(recorded) == set(configured) and all(
            _acquisition_values_equal(recorded[key], configured[key])
            for key in recorded
        )
    if type(recorded) is list and type(configured) is list:
        return len(recorded) == len(configured) and all(
            _acquisition_values_equal(left, right)
            for left, right in zip(recorded, configured)
        )
    return type(recorded) is type(configured) and recorded == configured


def _evidence_exact_int(evidence: dict[str, Any], name: str) -> int:
    """Read one scalar frame coordinate from already-persisted attempt evidence."""

    if name not in evidence:
        raise ValueError(f"advanced attempt evidence omitted {name}")
    array = np.asarray(evidence[name])
    if array.shape != ():
        raise ValueError(f"advanced attempt evidence {name} must be scalar")
    value = array.item()
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError(f"advanced attempt evidence {name} must be an exact integer")
    return int(value)


def _validate_production_source(source: Any, session: ReplaySessionSpec) -> None:
    if type(source) is not SequentialAdcSource:
        raise AdvancedReplayUnavailable(
            "production advanced replay requires the guarded SequentialAdcSource"
        )
    if any(
        name in source.__dict__
        for name in ("read_next", "__enter__", "verify_integrity")
    ):
        raise AdvancedReplayUnavailable(
            "production advanced replay source methods cannot be overridden"
        )
    try:
        source_path = source.path.resolve(strict=True)
        authorized_path = session.adc_path.resolve(strict=True)
    except OSError as exc:
        raise AdvancedReplayUnavailable(
            f"authorized advanced replay ADC is unavailable: {exc}"
        ) from exc
    if source_path != authorized_path:
        raise AdvancedReplayUnavailable("advanced replay source path is not authorized")
    if source.expected_sha256 != session.registered_adc_sha256:
        raise AdvancedReplayUnavailable("advanced replay source SHA-256 is not authorized")
    if source.expected_frame_count != session.frame_count:
        raise AdvancedReplayUnavailable("advanced replay source frame count is not authorized")
    if source.frame_bytes != session.frame_bytes:
        raise AdvancedReplayUnavailable("advanced replay source frame bytes are not authorized")
    decoder = source.decoder
    if type(decoder) is not ProductionFrameDecoder:
        raise AdvancedReplayUnavailable("advanced replay decoder is not the production adapter")
    if "decode" in decoder.__dict__:
        raise AdvancedReplayUnavailable(
            "production advanced replay decoder method cannot be overridden"
        )
    decoder_identity = (
        decoder.num_adc_samples == session.num_adc_samples
        and decoder.num_rx == session.num_rx
        and decoder.num_chirps == session.num_chirps_per_frame
        and decoder.iq_swap is session.iq_swap
        and decoder.frame_bytes == session.frame_bytes
    )
    if not decoder_identity:
        raise AdvancedReplayUnavailable(
            "advanced replay decoder geometry/IQ convention is not authorized"
        )
    if (
        source.frames_read != 0
        or getattr(source, "_handle", None) is not None
        or getattr(source, "_integrity", None) is not None
    ):
        raise AdvancedReplayUnavailable(
            "production advanced replay requires a fresh unopened ADC source"
        )


def _validate_production_evidence_writer(
    writer: Any, authorization: AdvancedReplayAuthorization
) -> None:
    if type(writer) is not AttemptEvidenceWriter:
        raise AdvancedReplayUnavailable(
            "production advanced replay requires the authentic motion evidence writer"
        )
    if any(name in writer.__dict__ for name in ("write_attempt", "write_event")):
        raise AdvancedReplayUnavailable(
            "production advanced evidence writer methods cannot be overridden"
        )
    expected = {
        "config_hash": authorization.config_sha256,
        "source_hash": authorization.session.registered_adc_sha256,
        "calibration_hash": authorization.calibration_sha256,
    }
    for attribute, expected_value in expected.items():
        if getattr(writer, attribute, None) != expected_value:
            raise AdvancedReplayUnavailable(
                f"advanced replay evidence writer {attribute} is not authorized"
            )
    if getattr(writer, "_next_index", None) != 0:
        raise AdvancedReplayUnavailable(
            "production advanced replay requires a fresh pass evidence writer"
        )


def _native_hr_admission_error(result: AnalysisResult) -> str:
    """Reject a worker record that claims HR validity without native AHET proof."""

    if result.status != "completed" or result.dsp is None:
        return ""
    dsp = result.dsp
    if type(dsp) is not dict:
        return "native_hr_admission_failed:malformed_dsp"
    claimed_valid = dsp.get("hr_valid", False)
    if claimed_valid is False:
        return ""
    if claimed_valid is not True:
        return "native_hr_admission_failed:hr_valid_not_exact_boolean"
    try:
        hr_value = float(dsp.get("hr_raw", np.nan))
    except (TypeError, ValueError):
        hr_value = float("nan")
    hr_result = dsp.get("hr_result")
    if type(hr_result) is not dict:
        return "native_hr_admission_failed:missing_hr_result"
    if hr_result.get("ahet_verified") is not True:
        return "native_hr_admission_failed:ahet_not_verified"
    accepted_rank = hr_result.get("accepted_candidate_rank")
    if type(accepted_rank) is not int or accepted_rank < 0:
        return "native_hr_admission_failed:invalid_accepted_candidate_rank"
    if not math.isfinite(hr_value) or hr_value <= 0.0:
        return "native_hr_admission_failed:hr_not_finite_positive"
    return ""


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AdvancedReplayUnavailable(
            f"advanced configuration is not canonical JSON data: {exc}"
        ) from exc


def _snapshot_validated_calibration(
    path: Path,
    expected_sha256: str,
    validated_record: dict[str, Any],
) -> tuple[bytes, dict[str, Any]]:
    """Read the accepted record once and bind the replay to those exact bytes.

    The unchanged live validator establishes physical provenance and semantic
    compatibility.  Replay then performs one final read so a file replacement
    between the validator's internal reads cannot make the authorization retain
    bytes different from the configured SHA-256.  The returned mapping is parsed
    from this same byte string and must equal the validator's accepted record.
    """

    content = path.read_bytes()
    if sha256_bytes(content) != expected_sha256:
        raise AdvancedReplayUnavailable(
            "calibration record changed after physical validation"
        )
    try:
        parsed = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdvancedReplayUnavailable(
            "calibration record became invalid JSON after physical validation"
        ) from exc
    if type(parsed) is not dict:
        raise AdvancedReplayUnavailable("calibration record must be a JSON object")
    if parsed != validated_record:
        raise AdvancedReplayUnavailable(
            "calibration record changed after physical validation"
        )
    return bytes(content), parsed


__all__ = [
    "AdvancedReplayAuthorization",
    "AdvancedReplayEventSink",
    "AdvancedReplayOrchestrator",
    "AdvancedReplaySnapshot",
    "AdvancedReplayUnavailable",
    "authorize_advanced_replay",
]
