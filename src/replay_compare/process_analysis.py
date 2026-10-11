"""Spawned baseline analysis for responsive recorded replay.

The parent remains the sole owner of replay scheduling and evidence.  One
pass-scoped child receives a single decoded 600-frame window through one
parent-owned shared-memory region and runs the unchanged production analyzer.
Raw ADC paths, reference data, Qt objects, and hardware interfaces never cross
this boundary.
"""

from __future__ import annotations

import json
import math
import mmap
import multiprocessing
import os
import pickle
import threading
import time
from dataclasses import dataclass, field, replace
from multiprocessing.connection import Connection
from multiprocessing.shared_memory import SharedMemory
from typing import Any, Callable, Literal

import numpy as np

from src.live_motion.config import LiveMotionSettings
from src.live_motion.runtime import ExecutedAnalysis, production_analysis_function
from src.live_motion.scheduler import AnalysisJob, AnalysisResult
from src.warmup_select import derive_candidate_bins

from .telemetry import replay_process_memory_bytes


PROTOCOL_VERSION = 1
CONTROL_SCHEMA = "replay_baseline_process_control_v1"
RESULT_SCHEMA = "replay_baseline_process_result_v1"
MAX_CONTROL_BYTES = 65_536
MAX_RESULT_BYTES = 16_777_216
BASELINE_WINDOW_FRAMES = 600


def _expected_attached_mapping_size(
    payload_byte_count: int,
    *,
    platform_name: str | None = None,
    page_size: int | None = None,
) -> int:
    """Return the exact capacity reported by an attached shared mapping.

    Windows reports the page-rounded mapping capacity when a named mapping is
    attached in the child, even though its creator retains the requested
    payload size.  Other platforms retain the exact payload capacity.
    """

    if type(payload_byte_count) is not int or payload_byte_count <= 0:
        raise ValueError("payload_byte_count must be a positive exact integer")
    resolved_platform = os.name if platform_name is None else platform_name
    resolved_page_size = mmap.PAGESIZE if page_size is None else page_size
    if type(resolved_platform) is not str or not resolved_platform:
        raise ValueError("platform_name must be a nonempty exact string")
    if type(resolved_page_size) is not int or resolved_page_size <= 0:
        raise ValueError("page_size must be a positive exact integer")
    if resolved_platform != "nt":
        return payload_byte_count
    pages = (payload_byte_count + resolved_page_size - 1) // resolved_page_size
    return pages * resolved_page_size


@dataclass(frozen=True)
class _FrozenReplayAnalysisJob(AnalysisJob):
    """Carry either a frozen frame recipe or its materialized worker array."""

    raw_frames: np.ndarray | None = field(default=None, repr=False)
    captured_frames: tuple[np.ndarray, ...] = field(default_factory=tuple, repr=False)

    def __post_init__(self) -> None:
        super().__post_init__()
        if type(self.captured_frames) is not tuple:
            raise ValueError("captured replay frames must use an immutable tuple")
        if self.frame_stop - self.frame_start != BASELINE_WINDOW_FRAMES:
            raise ValueError("baseline replay jobs require exactly 600 frame bounds")

        request_state = self.raw_frames is None
        if request_state:
            if len(self.captured_frames) != BASELINE_WINDOW_FRAMES:
                raise ValueError(
                    "baseline replay requests require exactly 600 captured frames"
                )
            first_frame = self.captured_frames[0]
            if not isinstance(first_frame, np.ndarray) or first_frame.ndim != 3:
                raise ValueError(
                    "captured replay frames must be three-dimensional arrays"
                )
            first_shape = first_frame.shape
            first_dtype = first_frame.dtype
            for frame in self.captured_frames:
                if not isinstance(frame, np.ndarray) or frame.ndim != 3:
                    raise ValueError(
                        "captured replay frames must be three-dimensional arrays"
                    )
                if frame.shape != first_shape or frame.dtype != first_dtype:
                    raise ValueError("captured replay frames must share shape and dtype")
                if not frame.flags.owndata or frame.flags.writeable:
                    raise ValueError("captured replay frames must be owned and read-only")
            return

        if self.captured_frames:
            raise ValueError("materialized replay jobs cannot retain captured frames")
        assert self.raw_frames is not None
        if self.raw_frames.ndim != 4 or (
            self.raw_frames.shape[0] != BASELINE_WINDOW_FRAMES
        ):
            raise ValueError(
                "materialized baseline replay jobs require one 600-frame array"
            )
        if not self.raw_frames.flags.owndata or self.raw_frames.flags.writeable:
            raise ValueError("materialized replay frames must be owned and read-only")


def _materialize_replay_job(
    request: _FrozenReplayAnalysisJob,
) -> _FrozenReplayAnalysisJob:
    """Build the analysis array on the worker from an immutable frame recipe."""

    if request.raw_frames is not None or not request.captured_frames:
        raise ValueError("only an unmaterialized replay request can be materialized")
    window_frames = np.stack(request.captured_frames, axis=0)
    window_frames.setflags(write=False)
    return replace(request, raw_frames=window_frames, captured_frames=())


def baseline_analysis_function(
    config: dict[str, Any],
) -> Callable[[AnalysisJob], ExecutedAnalysis]:
    """Build production analysis with a private, calibration-free baseline contract."""

    frame_rate_hz = float(config["session"]["frame_rate_hz"])
    settings = LiveMotionSettings(
        enabled=False,
        unavailable_reason="baseline replay does not use motion calibration",
        frame_rate_hz=frame_rate_hz,
        preview_frames=(200, 400),
        ordinary_window_frames=600,
        ordinary_hop_frames=60,
        monitor_window_frames=20,
        monitor_hop_frames=5,
        stillness_frames=60,
        extended_breathing_enabled=False,
        extended_breathing_window_frames=1200,
        extended_breathing_band_hz=(0.05, 0.50),
        candidate_bins=tuple(derive_candidate_bins(config)),
        presence_thresholds=None,
        movement_thresholds=None,
        calibration_record=None,
        calibration_record_path=None,
        calibration_record_sha256=None,
    )
    if not settings.candidate_bins:
        raise ValueError("baseline candidate-bin gate is empty")
    return production_analysis_function(config, settings)


class ProcessAnalysisError(RuntimeError):
    """A fatal failure in the owned baseline analysis process."""

    def __init__(
        self,
        message: str,
        *,
        performance: dict[str, int | float] | None = None,
    ):
        super().__init__(message)
        self.performance = {} if performance is None else dict(performance)


@dataclass(frozen=True)
class ProcessJobMetadata:
    """Exact scalar AnalysisJob identity transported outside shared memory."""

    job_id: str
    epoch: int
    selection_revision: int
    stage: str
    frame_start: int
    frame_stop: int
    requested_bin: int | None
    selection_mode: str
    depends_on: str | None


@dataclass(frozen=True)
class SharedWindowDescriptor:
    """One complete window in the parent-owned shared-memory mapping."""

    name: str
    shape: tuple[int, ...]
    dtype_str: str
    byte_count: int


@dataclass(frozen=True)
class ControlEnvelope:
    schema: str
    version: int
    kind: Literal["analyze", "shutdown"]
    sequence: int
    config_sha256: str
    job: ProcessJobMetadata | None = None
    window: SharedWindowDescriptor | None = None


@dataclass(frozen=True)
class ResultEnvelope:
    schema: str
    version: int
    kind: Literal["ready", "result", "shutdown_ack", "fatal"]
    sequence: int
    config_sha256: str
    executed: ExecutedAnalysis | None = None
    child_materialization_elapsed_s: float | None = None
    child_callback_elapsed_s: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class ProcessExecution:
    """Complete scientific result plus measured transport-stage durations."""

    executed: ExecutedAnalysis
    sequence: int
    parent_copy_elapsed_s: float
    parent_send_elapsed_s: float
    parent_wait_receive_elapsed_s: float
    child_materialization_elapsed_s: float
    child_callback_elapsed_s: float
    round_trip_elapsed_s: float
    control_bytes: int
    result_bytes: int


def _serialize_bounded(value: Any, *, limit: int, label: str) -> bytes:
    payload = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
    if len(payload) > limit:
        raise ProcessAnalysisError(
            f"{label} payload has {len(payload)} bytes; inclusive limit is {limit}"
        )
    return payload


def serialize_control_envelope(value: ControlEnvelope) -> bytes:
    """Serialize one trusted control envelope under the objective byte bound."""

    return _serialize_bounded(value, limit=MAX_CONTROL_BYTES, label="control")


def serialize_result_envelope(value: ResultEnvelope) -> bytes:
    """Serialize one complete result without filtering scientific content."""

    return _serialize_bounded(value, limit=MAX_RESULT_BYTES, label="result")


def _deserialize_bounded(
    payload: bytes, *, limit: int, expected_type: type[Any], label: str
) -> Any:
    if len(payload) > limit:
        raise ProcessAnalysisError(
            f"{label} payload has {len(payload)} bytes; inclusive limit is {limit}"
        )
    try:
        value = pickle.loads(payload)
    except Exception as exc:
        raise ProcessAnalysisError(f"{label} payload is not valid pickle") from exc
    if type(value) is not expected_type:
        raise ProcessAnalysisError(
            f"{label} payload has unexpected type {type(value).__name__}"
        )
    return value


def _send_control(connection: Connection, envelope: ControlEnvelope) -> int:
    payload = serialize_control_envelope(envelope)
    connection.send_bytes(payload)
    return len(payload)


def _send_result(connection: Connection, envelope: ResultEnvelope) -> int:
    payload = serialize_result_envelope(envelope)
    connection.send_bytes(payload)
    return len(payload)


def _receive_control(connection: Connection) -> tuple[ControlEnvelope, int]:
    try:
        payload = connection.recv_bytes(MAX_CONTROL_BYTES)
    except OSError as exc:
        raise ProcessAnalysisError("control payload exceeded its byte limit") from exc
    value = _deserialize_bounded(
        payload,
        limit=MAX_CONTROL_BYTES,
        expected_type=ControlEnvelope,
        label="control",
    )
    return value, len(payload)


def _receive_result(connection: Connection) -> tuple[ResultEnvelope, int]:
    try:
        payload = connection.recv_bytes(MAX_RESULT_BYTES)
    except OSError as exc:
        raise ProcessAnalysisError("result payload exceeded its byte limit") from exc
    value = _deserialize_bounded(
        payload,
        limit=MAX_RESULT_BYTES,
        expected_type=ResultEnvelope,
        label="result",
    )
    return value, len(payload)


def _validate_common_envelope(
    envelope: ControlEnvelope | ResultEnvelope,
    *,
    expected_config_sha256: str,
) -> None:
    expected_schema = (
        CONTROL_SCHEMA if isinstance(envelope, ControlEnvelope) else RESULT_SCHEMA
    )
    if (
        type(envelope.schema) is not str
        or envelope.schema != expected_schema
        or type(envelope.version) is not int
        or envelope.version != PROTOCOL_VERSION
    ):
        raise ProcessAnalysisError("analysis process protocol schema/version mismatch")
    if (
        type(envelope.config_sha256) is not str
        or envelope.config_sha256 != expected_config_sha256
    ):
        raise ProcessAnalysisError("analysis process config hash mismatch")
    if type(envelope.sequence) is not int or envelope.sequence < 0:
        raise ProcessAnalysisError("analysis process sequence must be nonnegative int")


def _validate_job_metadata(job: ProcessJobMetadata) -> None:
    if type(job.job_id) is not str or not job.job_id:
        raise ProcessAnalysisError("process job_id must be a nonempty string")
    for name in ("epoch", "selection_revision", "frame_start", "frame_stop"):
        if type(getattr(job, name)) is not int:
            raise ProcessAnalysisError(f"process job {name} must be an exact integer")
    if job.epoch < 0 or job.selection_revision < 0:
        raise ProcessAnalysisError("process job epoch/revision must be nonnegative")
    if job.frame_start < 0 or job.frame_stop - job.frame_start != BASELINE_WINDOW_FRAMES:
        raise ProcessAnalysisError("process job must span exactly 600 frames")
    if job.requested_bin is not None and (
        type(job.requested_bin) is not int or job.requested_bin < 0
    ):
        raise ProcessAnalysisError(
            "process job requested_bin must be nonnegative exact int or None"
        )
    if type(job.stage) is not str or job.stage not in {
        "preview_10", "preview_20", "ordinary_30", "extended_60", "rolling"
    }:
        raise ProcessAnalysisError("process job stage is invalid")
    if (
        type(job.selection_mode) is not str
        or job.selection_mode not in {"select", "reuse_provisional", "committed"}
    ):
        raise ProcessAnalysisError("process job selection_mode is invalid")
    if job.selection_mode == "select" and job.requested_bin is not None:
        raise ProcessAnalysisError("selection process jobs cannot request a fixed bin")
    if job.selection_mode != "select" and type(job.requested_bin) is not int:
        raise ProcessAnalysisError("fixed-bin process jobs require an exact bin")
    if job.depends_on is not None:
        raise ProcessAnalysisError("dispatched process jobs cannot retain a dependency")


def _job_metadata(request: Any) -> ProcessJobMetadata:
    return ProcessJobMetadata(
        job_id=request.job_id,
        epoch=request.epoch,
        selection_revision=request.selection_revision,
        stage=request.stage,
        frame_start=request.frame_start,
        frame_stop=request.frame_stop,
        requested_bin=request.requested_bin,
        selection_mode=request.selection_mode,
        depends_on=request.depends_on,
    )


def _validate_executed_identity(
    executed: ExecutedAnalysis, request: Any, *, num_adc_samples: int
) -> None:
    if type(executed.result) is not AnalysisResult:
        raise ProcessAnalysisError("analysis result payload has an invalid result type")
    result = executed.result
    exact_integer_fields = (
        "epoch", "selection_revision", "frame_start", "frame_stop"
    )
    if any(type(getattr(result, name)) is not int for name in exact_integer_fields):
        raise ProcessAnalysisError("analysis result identity requires exact integers")
    if result.actual_bin is not None and type(result.actual_bin) is not int:
        raise ProcessAnalysisError("analysis result actual_bin must be exact int or None")
    if result.actual_bin is not None and not 0 <= result.actual_bin < num_adc_samples:
        raise ProcessAnalysisError("analysis result actual_bin is out of range")
    identity_matches = (
        type(result.job_id) is str
        and result.job_id == request.job_id
        and result.epoch == request.epoch
        and result.selection_revision == request.selection_revision
        and type(result.stage) is str
        and result.stage == request.stage
        and result.frame_start == request.frame_start
        and result.frame_stop == request.frame_stop
        and (
            request.selection_mode == "select"
            or result.actual_bin == request.requested_bin
        )
    )
    if not identity_matches:
        raise ProcessAnalysisError("analysis result job identity mismatch")


def _child_entry(
    connection: Connection,
    config_json: bytes,
    expected_config_sha256: str,
) -> None:
    """Import-safe child entry point for one pass-scoped analyzer."""

    fatal_sequence = 0
    materialization_started: float | None = None
    materialization_elapsed: float | None = None
    callback_started: float | None = None
    callback_elapsed: float | None = None
    try:
        config = json.loads(config_json.decode("utf-8"))
        if type(config) is not dict:
            raise ProcessAnalysisError("child config must be a JSON object")
        from src.window_pipeline import run_config_hash
        if run_config_hash(config) != expected_config_sha256:
            raise ProcessAnalysisError("child effective config hash verification failed")
        analyze = baseline_analysis_function(config)
        profile = config.get("profile")
        if type(profile) is not dict:
            raise ProcessAnalysisError("child config profile must be an object")
        geometry_values = (
            profile.get("num_chirps_per_frame"),
            profile.get("num_rx"),
            profile.get("num_adc_samples"),
        )
        if any(type(value) is not int or value <= 0 for value in geometry_values):
            raise ProcessAnalysisError("child config frame geometry is invalid")
        expected_shape = (BASELINE_WINDOW_FRAMES, *geometry_values)
        _send_result(connection, ResultEnvelope(
            schema=RESULT_SCHEMA,
            version=PROTOCOL_VERSION,
            kind="ready",
            sequence=0,
            config_sha256=expected_config_sha256,
        ))

        expected_sequence = 1
        while True:
            envelope, _ = _receive_control(connection)
            _validate_common_envelope(
                envelope, expected_config_sha256=expected_config_sha256
            )
            # Timing state is request-local.  Reset it before validating the
            # request kind/sequence so a protocol failure cannot report spans
            # measured for the preceding request.
            materialization_started = None
            materialization_elapsed = None
            callback_started = None
            callback_elapsed = None
            if type(envelope.kind) is not str:
                raise ProcessAnalysisError("child control kind must be a string")
            if envelope.kind == "shutdown":
                if envelope.sequence != expected_sequence:
                    raise ProcessAnalysisError("shutdown sequence mismatch")
                _send_result(connection, ResultEnvelope(
                    schema=RESULT_SCHEMA,
                    version=PROTOCOL_VERSION,
                    kind="shutdown_ack",
                    sequence=envelope.sequence,
                    config_sha256=expected_config_sha256,
                ))
                return
            if envelope.kind != "analyze":
                raise ProcessAnalysisError("child received an unknown control kind")
            fatal_sequence = envelope.sequence
            if envelope.sequence != expected_sequence:
                raise ProcessAnalysisError("analysis request sequence mismatch")
            if (
                type(envelope.job) is not ProcessJobMetadata
                or type(envelope.window) is not SharedWindowDescriptor
            ):
                raise ProcessAnalysisError("analysis request omitted job/window")
            _validate_job_metadata(envelope.job)
            if (
                envelope.job.requested_bin is not None
                and envelope.job.requested_bin >= expected_shape[3]
            ):
                raise ProcessAnalysisError("process job requested_bin is out of range")
            descriptor = envelope.window
            if (
                type(descriptor.name) is not str
                or not descriptor.name
                or type(descriptor.shape) is not tuple
                or descriptor.shape != expected_shape
                or any(type(value) is not int or value <= 0 for value in descriptor.shape)
                or type(descriptor.dtype_str) is not str
                or type(descriptor.byte_count) is not int
                or descriptor.byte_count <= 0
            ):
                raise ProcessAnalysisError("shared-window descriptor is malformed")
            dtype = np.dtype(descriptor.dtype_str)
            if dtype != np.dtype(np.complex64) or dtype.hasobject:
                raise ProcessAnalysisError("shared-window dtype must be complex64")
            expected_bytes = math.prod(descriptor.shape) * dtype.itemsize
            if expected_bytes != descriptor.byte_count:
                raise ProcessAnalysisError("shared-window byte count is inconsistent")

            materialization_started = time.monotonic()
            shared = SharedMemory(name=descriptor.name, create=False)
            shared_view: np.ndarray | None = None
            owned_frames: np.ndarray | None = None
            try:
                expected_mapping_size = _expected_attached_mapping_size(
                    descriptor.byte_count
                )
                if shared.size != expected_mapping_size:
                    raise ProcessAnalysisError("shared-window mapping capacity mismatch")
                shared_view = np.ndarray(
                    descriptor.shape, dtype=dtype, buffer=shared.buf
                )
                shared_view.setflags(write=False)
                owned_frames = np.array(shared_view, copy=True, order="C")
                owned_frames.setflags(write=False)
            finally:
                shared_view = None
                shared.close()
            materialization_elapsed = time.monotonic() - materialization_started

            metadata = envelope.job
            execution_job = _FrozenReplayAnalysisJob(
                job_id=metadata.job_id,
                epoch=metadata.epoch,
                selection_revision=metadata.selection_revision,
                stage=metadata.stage,
                frame_start=metadata.frame_start,
                frame_stop=metadata.frame_stop,
                requested_bin=metadata.requested_bin,
                selection_mode=metadata.selection_mode,
                raw_frames=owned_frames,
                range_cache_snapshot=None,
                depends_on=metadata.depends_on,
                captured_frames=(),
            )
            callback_started = time.monotonic()
            executed = analyze(execution_job)
            callback_elapsed = time.monotonic() - callback_started
            response = ResultEnvelope(
                schema=RESULT_SCHEMA,
                version=PROTOCOL_VERSION,
                kind="result",
                sequence=envelope.sequence,
                config_sha256=expected_config_sha256,
                executed=executed,
                child_materialization_elapsed_s=materialization_elapsed,
                child_callback_elapsed_s=callback_elapsed,
            )
            _send_result(connection, response)
            execution_job = None
            owned_frames = None
            expected_sequence += 1
    except BaseException as exc:
        if materialization_started is not None and materialization_elapsed is None:
            materialization_elapsed = time.monotonic() - materialization_started
        if callback_started is not None and callback_elapsed is None:
            callback_elapsed = time.monotonic() - callback_started
        try:
            _send_result(connection, ResultEnvelope(
                schema=RESULT_SCHEMA,
                version=PROTOCOL_VERSION,
                kind="fatal",
                sequence=fatal_sequence,
                config_sha256=expected_config_sha256,
                child_materialization_elapsed_s=materialization_elapsed,
                child_callback_elapsed_s=callback_elapsed,
                error=f"{type(exc).__name__}: {exc}",
            ))
        except BaseException:
            pass
    finally:
        connection.close()


class SpawnBaselineBackend:
    """One owned spawn child and one reusable parent-owned signal window."""

    def __init__(self, config: dict[str, Any], expected_config_sha256: str):
        if type(config) is not dict:
            raise TypeError("baseline process config must be a dictionary")
        if (
            type(expected_config_sha256) is not str
            or len(expected_config_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in expected_config_sha256
            )
        ):
            raise ValueError("expected_config_sha256 must be a 64-character digest")
        self._config_json = json.dumps(
            config, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        self._config_sha256 = expected_config_sha256
        profile = config.get("profile")
        if type(profile) is not dict:
            raise ValueError("baseline process config profile must be an object")
        geometry_values = (
            profile.get("num_chirps_per_frame"),
            profile.get("num_rx"),
            profile.get("num_adc_samples"),
        )
        if any(type(value) is not int or value <= 0 for value in geometry_values):
            raise ValueError("baseline process config frame geometry is invalid")
        self._expected_shape = (BASELINE_WINDOW_FRAMES, *geometry_values)
        self._context = multiprocessing.get_context("spawn")
        self._parent_connection: Connection | None = None
        self._process: multiprocessing.Process | None = None
        self._shared_memory: SharedMemory | None = None
        self._shared_shape: tuple[int, ...] | None = None
        self._shared_dtype: np.dtype[Any] | None = None
        self._sequence = 1
        self._state_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._active = False
        self._ready = False
        self._closing = False
        self._closed = False
        self._pid: int | None = None
        self._exitcode: int | None = None
        self._final_peak_rss_bytes: int | None = None
        self._close_error: str | None = None
        self._graceful_shutdown_acknowledged = False

    @property
    def is_closed(self) -> bool:
        with self._state_lock:
            return self._closed

    @property
    def is_started(self) -> bool:
        with self._state_lock:
            process = self._process
            ready = self._ready
            closed = self._closed
        try:
            return bool(ready and not closed and process is not None and process.is_alive())
        except ValueError:
            return False

    def start(self, timeout_s: float = 5.0) -> None:
        if not 0.0 < float(timeout_s) <= 5.0:
            raise ValueError("child startup timeout must be in (0, 5] seconds")
        with self._state_lock:
            if self._process is not None or self._closed:
                raise RuntimeError("baseline analysis backend can only start once")
            parent_connection, child_connection = self._context.Pipe(duplex=True)
            process = self._context.Process(
                target=_child_entry,
                args=(child_connection, self._config_json, self._config_sha256),
                name="ReplayBaselineDSP",
                daemon=True,
            )
            self._parent_connection = parent_connection
            self._process = process
            try:
                process.start()
                self._pid = process.pid
            except BaseException:
                parent_connection.close()
                try:
                    process.close()
                except ValueError:
                    pass
                self._parent_connection = None
                self._process = None
                self._closed = True
                raise
            finally:
                child_connection.close()
        deadline = time.monotonic() + float(timeout_s)
        try:
            response, _ = self._wait_for_result(deadline)
            _validate_common_envelope(
                response, expected_config_sha256=self._config_sha256
            )
            if response.kind != "ready" or response.sequence != 0:
                raise ProcessAnalysisError("child did not return the ready handshake")
            with self._state_lock:
                self._ready = True
        except BaseException:
            try:
                self.close(
                    deadline_monotonic_s=deadline,
                    containment_deadline_monotonic_s=deadline + 1.0,
                    force=True,
                )
            except BaseException:
                pass
            raise

    def execute(self, request: Any) -> ProcessExecution:
        with self._state_lock:
            if self._closed or self._closing:
                raise ProcessAnalysisError("analysis backend is closing")
            if self._process is None or self._parent_connection is None:
                raise ProcessAnalysisError("analysis backend has not started")
            if self._active:
                raise ProcessAnalysisError("analysis backend permits one in-flight job")
            self._active = True
            sequence = self._sequence
        round_trip_started = time.monotonic()
        copy_elapsed = 0.0
        send_elapsed = 0.0
        wait_started: float | None = None
        control_bytes = 0
        result_bytes = 0
        try:
            descriptor, copy_elapsed = self._copy_request(request)
            envelope = ControlEnvelope(
                schema=CONTROL_SCHEMA,
                version=PROTOCOL_VERSION,
                kind="analyze",
                sequence=sequence,
                config_sha256=self._config_sha256,
                job=_job_metadata(request),
                window=descriptor,
            )
            send_started = time.monotonic()
            connection = self._require_connection()
            try:
                control_payload = serialize_control_envelope(envelope)
                control_bytes = len(control_payload)
                connection.send_bytes(control_payload)
            except (BrokenPipeError, EOFError, OSError) as exc:
                raise ProcessAnalysisError(
                    "analysis child transport failed while sending request"
                ) from exc
            send_elapsed = time.monotonic() - send_started
            wait_started = time.monotonic()
            response, result_bytes = self._wait_for_result(None)
            wait_elapsed = time.monotonic() - wait_started
            _validate_common_envelope(
                response, expected_config_sha256=self._config_sha256
            )
            if response.kind == "fatal":
                if response.sequence != sequence:
                    raise ProcessAnalysisError(
                        "fatal analysis result sequence mismatch"
                    )
                fatal_performance: dict[str, int | float] = {}
                for field_name, output_name in (
                    (
                        "child_materialization_elapsed_s",
                        "child_materialization_elapsed_s",
                    ),
                    ("child_callback_elapsed_s", "child_callback_elapsed_s"),
                ):
                    value = getattr(response, field_name)
                    if value is None:
                        continue
                    if (
                        type(value) is not float
                        or not math.isfinite(value)
                        or value < 0.0
                    ):
                        raise ProcessAnalysisError(
                            f"fatal child timing {field_name} is invalid"
                        )
                    fatal_performance[output_name] = value
                raise ProcessAnalysisError(
                    response.error or "child analysis fatal error",
                    performance=fatal_performance,
                )
            if response.kind != "result" or response.sequence != sequence:
                raise ProcessAnalysisError("analysis result kind/sequence mismatch")
            if type(response.executed) is not ExecutedAnalysis:
                raise ProcessAnalysisError("analysis result omitted ExecutedAnalysis")
            _validate_executed_identity(
                response.executed,
                request,
                num_adc_samples=self._expected_shape[3],
            )
            materialization = response.child_materialization_elapsed_s
            callback = response.child_callback_elapsed_s
            if (
                type(materialization) is not float
                or not math.isfinite(materialization)
                or materialization < 0.0
            ):
                raise ProcessAnalysisError("child materialization timing is invalid")
            if (
                type(callback) is not float
                or not math.isfinite(callback)
                or callback < 0.0
            ):
                raise ProcessAnalysisError("child callback timing is invalid")
            with self._state_lock:
                self._sequence += 1
            return ProcessExecution(
                executed=response.executed,
                sequence=sequence,
                parent_copy_elapsed_s=copy_elapsed,
                parent_send_elapsed_s=send_elapsed,
                parent_wait_receive_elapsed_s=wait_elapsed,
                child_materialization_elapsed_s=materialization,
                child_callback_elapsed_s=callback,
                round_trip_elapsed_s=time.monotonic() - round_trip_started,
                control_bytes=control_bytes,
                result_bytes=result_bytes,
            )
        except BaseException as exc:
            performance: dict[str, int | float] = {
                "process_sequence": sequence,
                "parent_copy_elapsed_s": copy_elapsed,
                "parent_send_elapsed_s": send_elapsed,
                "parent_wait_receive_elapsed_s": (
                    0.0
                    if wait_started is None
                    else time.monotonic() - wait_started
                ),
                "process_round_trip_elapsed_s": (
                    time.monotonic() - round_trip_started
                ),
                "process_control_bytes": control_bytes,
                "process_result_bytes": result_bytes,
            }
            if isinstance(exc, ProcessAnalysisError):
                performance.update(exc.performance)
                raise ProcessAnalysisError(
                    str(exc), performance=performance
                ) from exc
            raise ProcessAnalysisError(
                f"{type(exc).__name__}: {exc}", performance=performance
            ) from exc
        finally:
            with self._state_lock:
                self._active = False

    def _copy_request(self, request: Any) -> tuple[SharedWindowDescriptor, float]:
        frames = getattr(request, "captured_frames", None)
        if type(frames) is not tuple or len(frames) != BASELINE_WINDOW_FRAMES:
            raise ProcessAnalysisError("process request requires 600 captured frames")
        if getattr(request, "raw_frames", None) is not None:
            raise ProcessAnalysisError("process request must be unmaterialized")
        if getattr(request, "range_cache_snapshot", None) is not None:
            raise ProcessAnalysisError(
                "baseline process requests cannot carry a range cache"
            )
        first = frames[0]
        if not isinstance(first, np.ndarray) or first.ndim != 3:
            raise ProcessAnalysisError("captured process frames must be 3-D arrays")
        shape = (BASELINE_WINDOW_FRAMES, *first.shape)
        if shape != self._expected_shape:
            raise ProcessAnalysisError(
                "captured process frame geometry disagrees with config"
            )
        dtype = first.dtype
        if dtype != np.dtype(np.complex64) or dtype.hasobject:
            raise ProcessAnalysisError("captured process frame dtype must be complex64")
        metadata = _job_metadata(request)
        _validate_job_metadata(metadata)
        byte_count = math.prod(shape) * dtype.itemsize
        self._ensure_shared_memory(shape, dtype, byte_count)
        assert self._shared_memory is not None
        shared_array = np.ndarray(shape, dtype=dtype, buffer=self._shared_memory.buf)
        started = time.monotonic()
        for frame in frames:
            if (
                not isinstance(frame, np.ndarray)
                or frame.shape != first.shape
                or frame.dtype != dtype
                or not frame.flags.owndata
                or frame.flags.writeable
            ):
                raise ProcessAnalysisError(
                    "captured process frames must be homogeneous, owned, and read-only"
                )
        for index, frame in enumerate(frames):
            np.copyto(shared_array[index], frame, casting="no")
        copy_elapsed = time.monotonic() - started
        return SharedWindowDescriptor(
            name=self._shared_memory.name,
            shape=shape,
            dtype_str=dtype.str,
            byte_count=byte_count,
        ), copy_elapsed

    def _ensure_shared_memory(
        self, shape: tuple[int, ...], dtype: np.dtype[Any], byte_count: int
    ) -> None:
        if self._shared_memory is None:
            self._shared_memory = SharedMemory(create=True, size=byte_count)
            self._shared_shape = shape
            self._shared_dtype = dtype
            return
        if (
            self._shared_shape != shape
            or self._shared_dtype != dtype
            or self._shared_memory.size != byte_count
        ):
            raise ProcessAnalysisError("replay frame geometry changed within one pass")

    def _require_connection(self) -> Connection:
        with self._state_lock:
            connection = self._parent_connection
        if connection is None:
            raise ProcessAnalysisError("analysis child transport is closed")
        return connection

    def _wait_for_result(
        self, deadline_monotonic_s: float | None
    ) -> tuple[ResultEnvelope, int]:
        while True:
            connection = self._require_connection()
            if deadline_monotonic_s is not None:
                remaining = deadline_monotonic_s - time.monotonic()
                if remaining <= 0.0:
                    raise TimeoutError("analysis child response exceeded its deadline")
                poll_s = min(0.05, remaining)
            else:
                poll_s = 0.05
            try:
                if connection.poll(poll_s):
                    return _receive_result(connection)
            except (EOFError, OSError) as exc:
                raise ProcessAnalysisError("analysis child transport closed") from exc
            with self._state_lock:
                process = self._process
                closing = self._closing
            if closing:
                raise ProcessAnalysisError("analysis child was stopped")
            if process is None or not process.is_alive():
                exitcode = None if process is None else process.exitcode
                raise ProcessAnalysisError(
                    f"analysis child exited before a result (exitcode={exitcode})"
                )

    def observation(self) -> dict[str, int | bool | None]:
        with self._state_lock:
            process = self._process
            pid = self._pid
            cached_exitcode = self._exitcode
            cached_peak = self._final_peak_rss_bytes
        try:
            alive = bool(process is not None and process.is_alive())
            exitcode = process.exitcode if process is not None else cached_exitcode
        except ValueError:
            alive = False
            exitcode = cached_exitcode
        current = None
        peak = cached_peak
        if pid is not None and alive:
            current, observed_peak = replay_process_memory_bytes(pid)
            with self._state_lock:
                same_process = self._process is process
                if same_process and observed_peak is not None:
                    self._final_peak_rss_bytes = observed_peak
                    peak = observed_peak
                elif not same_process:
                    current = None
                    peak = self._final_peak_rss_bytes
        return {
            "pid": pid,
            "alive": alive,
            "exitcode": exitcode,
            "current_rss_bytes": current,
            "peak_rss_bytes": peak,
        }

    def close(
        self,
        *,
        deadline_monotonic_s: float | None = None,
        containment_deadline_monotonic_s: float | None = None,
        force: bool = False,
    ) -> None:
        with self._close_lock:
            try:
                self._close_impl(
                    deadline_monotonic_s=deadline_monotonic_s,
                    containment_deadline_monotonic_s=(
                        containment_deadline_monotonic_s
                    ),
                    force=force,
                )
            except BaseException as exc:
                if self._close_error is None:
                    self._close_error = str(exc)
            if self._close_error is not None:
                # A transient close/unlink failure must not prevent a later
                # call from retrying the still-owned resource.  The original
                # failure remains causal even after cleanup eventually finishes.
                raise ProcessAnalysisError(self._close_error)

    def _close_impl(
        self,
        *,
        deadline_monotonic_s: float | None,
        containment_deadline_monotonic_s: float | None,
        force: bool,
    ) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closing = True
            process = self._process
            connection = self._parent_connection
            active = self._active
            sequence = self._sequence
            graceful_acknowledged = self._graceful_shutdown_acknowledged
        if deadline_monotonic_s is None:
            deadline_monotonic_s = time.monotonic() + 5.0
        if containment_deadline_monotonic_s is None:
            containment_deadline_monotonic_s = deadline_monotonic_s + 1.0

        first_failure: BaseException | None = None
        forced_cleanup = False

        def record_failure(exc: BaseException) -> None:
            nonlocal first_failure
            if first_failure is None:
                first_failure = exc

        def process_is_alive() -> bool:
            if process is None:
                return False
            try:
                return bool(process.is_alive())
            except BaseException as exc:
                # Treat an indeterminate process state as live so terminal
                # containment is still attempted within the shared allowance.
                record_failure(exc)
                return True

        if self._pid is not None and process_is_alive():
            try:
                _, observed_peak = replay_process_memory_bytes(self._pid)
                if observed_peak is not None:
                    self._final_peak_rss_bytes = observed_peak
            except BaseException as exc:
                record_failure(exc)

        if (
            process is not None
            and process_is_alive()
            and connection is not None
            and not active
            and not force
            and not graceful_acknowledged
        ):
            try:
                _send_control(connection, ControlEnvelope(
                    schema=CONTROL_SCHEMA,
                    version=PROTOCOL_VERSION,
                    kind="shutdown",
                    sequence=sequence,
                    config_sha256=self._config_sha256,
                ))
                response, _ = self._wait_for_shutdown_ack(
                    connection, deadline_monotonic_s
                )
                _validate_common_envelope(
                    response, expected_config_sha256=self._config_sha256
                )
                if response.kind != "shutdown_ack" or response.sequence != sequence:
                    raise ProcessAnalysisError("child shutdown acknowledgment mismatch")
                graceful_acknowledged = True
                with self._state_lock:
                    self._graceful_shutdown_acknowledged = True
            except BaseException as exc:
                record_failure(exc)
                force = True
        elif process is not None and not force and not graceful_acknowledged:
            record_failure(ProcessAnalysisError(
                "analysis child exited before graceful shutdown acknowledgment"
            ))
            force = True

        remaining = max(0.0, deadline_monotonic_s - time.monotonic())
        if process is not None:
            try:
                process.join(timeout=remaining)
            except BaseException as exc:
                record_failure(exc)
                force = True
        if process_is_alive():
            force = True
        if force and process_is_alive():
            forced_cleanup = True
            try:
                process.terminate()
            except BaseException as exc:
                record_failure(exc)
            try:
                process.join(
                    timeout=max(
                        0.0,
                        containment_deadline_monotonic_s - time.monotonic(),
                    )
                )
            except BaseException as exc:
                record_failure(exc)
            if process_is_alive() and hasattr(process, "kill"):
                try:
                    process.kill()
                except BaseException as exc:
                    record_failure(exc)
                try:
                    process.join(
                        timeout=max(
                            0.0,
                            containment_deadline_monotonic_s - time.monotonic(),
                        )
                    )
                except BaseException as exc:
                    record_failure(exc)
            if process_is_alive():
                record_failure(ProcessAnalysisError(
                    "owned analysis child survived terminal containment"
                ))

        if self._pid is not None:
            try:
                _, observed_peak = replay_process_memory_bytes(self._pid)
                if observed_peak is not None:
                    self._final_peak_rss_bytes = observed_peak
            except BaseException as exc:
                record_failure(exc)
        if process is not None:
            try:
                self._exitcode = process.exitcode
            except BaseException as exc:
                record_failure(exc)
            if graceful_acknowledged and self._exitcode != 0:
                record_failure(ProcessAnalysisError(
                    f"analysis child exited with code {self._exitcode}"
                ))
        if connection is not None:
            try:
                connection.close()
                with self._state_lock:
                    if self._parent_connection is connection:
                        self._parent_connection = None
            except BaseException as exc:
                record_failure(exc)
        shared = self._shared_memory
        if shared is not None:
            shared_close_succeeded = False
            shared_unlink_succeeded = False
            try:
                shared.close()
                shared_close_succeeded = True
            except BaseException as exc:
                record_failure(exc)
            try:
                shared.unlink()
                shared_unlink_succeeded = True
            except FileNotFoundError:
                shared_unlink_succeeded = True
            except BaseException as exc:
                record_failure(exc)
            if shared_close_succeeded and shared_unlink_succeeded:
                with self._state_lock:
                    if self._shared_memory is shared:
                        self._shared_memory = None
                        self._shared_shape = None
                        self._shared_dtype = None
        if process is not None and not process_is_alive():
            try:
                process.close()
                with self._state_lock:
                    if self._process is process:
                        self._process = None
            except BaseException as exc:
                record_failure(exc)
        with self._state_lock:
            if (
                self._parent_connection is None
                and self._process is None
                and self._shared_memory is None
            ):
                self._ready = False
                self._closed = True
        if forced_cleanup:
            record_failure(ProcessAnalysisError(
                "analysis child required terminal containment"
            ))
        if first_failure is not None:
            raise ProcessAnalysisError(
                f"analysis child cleanup failed: {first_failure}"
            ) from first_failure

    def _wait_for_shutdown_ack(
        self, connection: Connection, deadline_monotonic_s: float
    ) -> tuple[ResultEnvelope, int]:
        while True:
            remaining = deadline_monotonic_s - time.monotonic()
            if remaining <= 0.0:
                raise TimeoutError("analysis child shutdown exceeded its deadline")
            if connection.poll(min(0.05, remaining)):
                return _receive_result(connection)


__all__ = [
    "CONTROL_SCHEMA",
    "MAX_CONTROL_BYTES",
    "MAX_RESULT_BYTES",
    "PROTOCOL_VERSION",
    "RESULT_SCHEMA",
    "ControlEnvelope",
    "ProcessAnalysisError",
    "ProcessExecution",
    "ProcessJobMetadata",
    "ResultEnvelope",
    "SharedWindowDescriptor",
    "SpawnBaselineBackend",
    "serialize_control_envelope",
    "serialize_result_envelope",
]
