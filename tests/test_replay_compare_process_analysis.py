"""Spawn-process isolation, transport, and scientific-equivalence regressions."""

from __future__ import annotations

import ast
import copy
import dataclasses
import json
import math
import pickle
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

import src.replay_compare.process_analysis as process_module
from src.replay_compare.engine import (
    BaselineController,
    _materialize_replay_job,
    baseline_analysis_function,
)
from src.replay_compare.process_analysis import (
    CONTROL_SCHEMA,
    MAX_CONTROL_BYTES,
    MAX_RESULT_BYTES,
    PROTOCOL_VERSION,
    RESULT_SCHEMA,
    ControlEnvelope,
    ProcessAnalysisError,
    ProcessJobMetadata,
    ResultEnvelope,
    SharedWindowDescriptor,
    SpawnBaselineBackend,
)
from src.window_pipeline import run_config_hash


ROOT = Path(__file__).resolve().parents[1]
TIMING_NAMES = {
    "analysis_elapsed_s",
    "selector_elapsed_s",
    "t_warmup_scan_ms",
}


def _config() -> dict:
    config = yaml.safe_load(
        (ROOT / "scripts" / "live_demo_calibrated_config.yaml").read_text(
            encoding="utf-8"
        )
    )
    config = copy.deepcopy(config)
    config["profile"].update(
        {
            "num_adc_samples": 8,
            "num_rx": 1,
            "num_chirps_per_frame": 1,
            "range_resolution_m": 0.1,
            "range_bias_m": 0.0,
            "iq_swap": True,
        }
    )
    config["phase"]["clutter_removal"] = "none"
    config["protocol"]["subject_distance_m"] = [0.1, 0.1]
    config["bin_selection"]["candidate_bins"] = [1]
    return config


def _frames(count: int = 600) -> np.ndarray:
    time_s = np.arange(count, dtype=np.float64) / 20.0
    phase = (
        3.0 * np.sin(2 * np.pi * 0.25 * time_s)
        + 1.5 * np.sin(2 * np.pi * 0.50 * time_s)
        + 1.0 * np.sin(2 * np.pi * 0.75 * time_s)
        + 0.3 * np.sin(2 * np.pi * (71.0 / 60.0) * time_s)
    )
    carrier = np.exp(2j * np.pi * np.arange(8, dtype=np.float64) / 8.0)
    return (
        np.exp(1j * phase)[:, None, None, None]
        * carrier[None, None, None, :]
    ).astype(np.complex64)


def _selector_request(config: dict, frames: np.ndarray | None = None):
    controller = BaselineController(config, generation=1)
    source_frames = _frames() if frames is None else frames
    for index, frame in enumerate(source_frames):
        controller.handle_frame(index, frame, valid=True)
    request = controller.claim_pending_job()
    assert request is not None and request.selection_mode == "select"
    return request


def _assert_nested_equal(actual, expected, *, path="root"):
    if path.rsplit(".", 1)[-1] in TIMING_NAMES:
        return
    assert type(actual) is type(expected), (
        f"{path}: {type(actual).__name__} != {type(expected).__name__}"
    )
    if dataclasses.is_dataclass(expected):
        for field in dataclasses.fields(expected):
            if field.name not in TIMING_NAMES:
                _assert_nested_equal(
                    getattr(actual, field.name),
                    getattr(expected, field.name),
                    path=f"{path}.{field.name}",
                )
        return
    if isinstance(expected, np.ndarray):
        assert actual.shape == expected.shape, path
        assert actual.dtype == expected.dtype, path
        np.testing.assert_array_equal(actual, expected, err_msg=path)
        return
    if isinstance(expected, dict):
        assert actual.keys() == expected.keys(), path
        for key in expected:
            if str(key) not in TIMING_NAMES:
                _assert_nested_equal(
                    actual[key], expected[key], path=f"{path}.{key}"
                )
        return
    if isinstance(expected, (tuple, list)):
        assert len(actual) == len(expected), path
        for index, (actual_item, expected_item) in enumerate(
            zip(actual, expected, strict=True)
        ):
            _assert_nested_equal(
                actual_item, expected_item, path=f"{path}[{index}]"
            )
        return
    if isinstance(expected, float) and math.isnan(expected):
        assert math.isnan(actual), path
        return
    assert actual == expected, path


def test_spawn_backend_matches_thread_science_for_selector_cleaned_nan_and_committed():
    config = _config()
    config_sha256 = run_config_hash(config)
    selector = _selector_request(config)
    nonfinite_frames = []
    for index, frame in enumerate(selector.captured_frames):
        copied = np.array(frame, copy=True)
        if index == 317:
            copied[0, 0, 1] = np.complex64(complex(math.nan, math.nan))
        copied.setflags(write=False)
        nonfinite_frames.append(copied)
    cleaned_nonfinite = replace(
        selector,
        job_id="cleaned-nonfinite",
        selection_revision=1,
        stage="rolling",
        requested_bin=1,
        selection_mode="committed",
        captured_frames=tuple(nonfinite_frames),
    )
    committed = replace(
        selector,
        job_id="committed-success",
        selection_revision=1,
        stage="rolling",
        requested_bin=1,
        selection_mode="committed",
    )
    analyze = baseline_analysis_function(config)
    expected = tuple(
        analyze(_materialize_replay_job(request))
        for request in (selector, cleaned_nonfinite, committed)
    )
    assert expected[1].result.status == "completed"
    backend = SpawnBaselineBackend(config, config_sha256)
    try:
        backend.start(timeout_s=5.0)
        first_observation = backend.observation()
        assert type(first_observation["pid"]) is int
        assert first_observation["pid"] > 0
        assert first_observation["alive"] is True

        process_results = tuple(
            backend.execute(request)
            for request in (selector, cleaned_nonfinite, committed)
        )
        assert [result.sequence for result in process_results] == [1, 2, 3]
        assert process_results[1].executed.result.status == "completed"
        assert process_results[2].executed.result.status == "completed"
        for process_result, thread_result in zip(
            process_results, expected, strict=True
        ):
            _assert_nested_equal(process_result.executed, thread_result)
            assert 0 < process_result.control_bytes <= MAX_CONTROL_BYTES
            assert 0 < process_result.result_bytes <= MAX_RESULT_BYTES
            assert process_result.parent_copy_elapsed_s >= 0.0
            assert process_result.parent_send_elapsed_s >= 0.0
            assert process_result.parent_wait_receive_elapsed_s >= 0.0
            assert process_result.child_materialization_elapsed_s >= 0.0
            assert process_result.child_callback_elapsed_s >= 0.0
            assert process_result.round_trip_elapsed_s >= 0.0
    finally:
        backend.close(force=False)
    assert backend.is_closed is True
    final_observation = backend.observation()
    assert final_observation["alive"] is False
    assert final_observation["exitcode"] == 0
    assert backend._process is None
    assert backend._parent_connection is None
    assert backend._shared_memory is None


def test_spawn_backend_returns_two_scientific_failures_without_child_death():
    config = _config()
    config["phase"]["method"] = "unsupported-test-phase-method"
    config_sha256 = run_config_hash(config)
    selector = _selector_request(config)
    requests = tuple(
        replace(
            selector,
            job_id=f"scientific-failure-{index}",
            selection_revision=1,
            stage="rolling",
            requested_bin=1,
            selection_mode="committed",
        )
        for index in (1, 2)
    )
    analyze = baseline_analysis_function(config)
    expected = tuple(
        analyze(_materialize_replay_job(request)) for request in requests
    )
    assert [item.result.status for item in expected] == ["failed", "failed"]
    assert all("method must be" in item.result.error for item in expected)

    backend = SpawnBaselineBackend(config, config_sha256)
    try:
        backend.start(timeout_s=5.0)
        process_results = tuple(backend.execute(request) for request in requests)
        assert [item.sequence for item in process_results] == [1, 2]
        assert backend.observation()["alive"] is True
        for process_result, thread_result in zip(
            process_results, expected, strict=True
        ):
            assert process_result.executed.result.status == "failed"
            assert "method must be" in process_result.executed.result.error
            _assert_nested_equal(process_result.executed, thread_result)
    finally:
        backend.close(force=False)
    assert backend.is_closed is True


@pytest.mark.parametrize(
    ("payload_bytes", "platform_name", "page_size", "expected"),
    [
        (1, "nt", 4096, 4096),
        (4096, "nt", 4096, 4096),
        (4097, "nt", 4096, 8192),
        (38400, "nt", 4096, 40960),
        (1, "posix", 4096, 1),
        (4096, "posix", 4096, 4096),
        (4097, "posix", 4096, 4097),
    ],
)
def test_expected_attached_mapping_size_is_platform_exact(
    payload_bytes, platform_name, page_size, expected
):
    assert process_module._expected_attached_mapping_size(
        payload_bytes,
        platform_name=platform_name,
        page_size=page_size,
    ) == expected


@pytest.mark.parametrize(
    "payload_bytes,platform_name,page_size",
    [
        (True, "nt", 4096),
        (0, "nt", 4096),
        (-1, "nt", 4096),
        (1.0, "nt", 4096),
        (1, "", 4096),
        (1, 1, 4096),
        (1, "nt", True),
        (1, "nt", 0),
        (1, "nt", -1),
        (1, "nt", 4096.0),
    ],
)
def test_expected_attached_mapping_size_rejects_coercive_inputs(
    payload_bytes, platform_name, page_size
):
    with pytest.raises((TypeError, ValueError)):
        process_module._expected_attached_mapping_size(
            payload_bytes,
            platform_name=platform_name,
            page_size=page_size,
        )


def test_out_of_range_committed_bin_is_protocol_fatal_before_scientific_dsp():
    config = _config()
    digest = run_config_hash(config)
    selector = _selector_request(config)
    out_of_range = replace(
        selector,
        job_id="out-of-range-bin",
        selection_revision=1,
        stage="rolling",
        requested_bin=99,
        selection_mode="committed",
    )
    backend = SpawnBaselineBackend(config, digest)
    close_error = None
    try:
        backend.start(timeout_s=5.0)
        with pytest.raises(ProcessAnalysisError, match="out of range") as captured:
            backend.execute(out_of_range)
        assert captured.value.performance["process_sequence"] == 1
        assert captured.value.performance["process_control_bytes"] > 0
        assert captured.value.performance["process_result_bytes"] > 0
    finally:
        try:
            backend.close(force=False)
        except ProcessAnalysisError as exc:
            close_error = exc
    assert isinstance(close_error, ProcessAnalysisError)
    assert "analysis child cleanup failed" in str(close_error)
    assert backend.is_closed is True
    assert backend._process is None
    assert backend._parent_connection is None
    assert backend._shared_memory is None


def test_parent_shared_window_copy_is_exact_chronological_and_never_stacks(
    monkeypatch,
):
    config = _config()
    frames = np.empty((600, 1, 1, 8), dtype=np.complex64)
    for index in range(600):
        frames[index].real = index
        frames[index].imag = -index
    request = _selector_request(config, frames)
    expected = np.asarray(request.captured_frames).copy()
    backend = SpawnBaselineBackend(config, run_config_hash(config))

    def forbidden_stack(*_args, **_kwargs):
        raise AssertionError("parent process bridge must copy frames without np.stack")

    monkeypatch.setattr(process_module.np, "stack", forbidden_stack)
    try:
        descriptor, elapsed = backend._copy_request(request)
        assert elapsed >= 0.0
        assert descriptor.shape == expected.shape
        assert descriptor.dtype_str == expected.dtype.str
        assert descriptor.byte_count == expected.nbytes
        assert backend._shared_memory is not None
        copied = np.ndarray(
            descriptor.shape,
            dtype=np.dtype(descriptor.dtype_str),
            buffer=backend._shared_memory.buf,
        )
        assert copied.tobytes(order="C") == expected.tobytes(order="C")
        np.testing.assert_array_equal(copied[0], frames[0])
        np.testing.assert_array_equal(copied[317], frames[317])
        np.testing.assert_array_equal(copied[-1], frames[-1])
    finally:
        backend.close(force=False)
    assert backend._shared_memory is None


@pytest.mark.parametrize(
    ("serializer_name", "limit"),
    [
        ("serialize_control_envelope", MAX_CONTROL_BYTES),
        ("serialize_result_envelope", MAX_RESULT_BYTES),
    ],
)
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_ipc_serialized_payload_limit_is_inclusive(
    monkeypatch, serializer_name, limit, offset
):
    payload = b"x" * (limit + offset)
    monkeypatch.setattr(process_module.pickle, "dumps", lambda *_a, **_k: payload)
    serializer = getattr(process_module, serializer_name)
    if offset <= 0:
        assert serializer(object()) == payload
    else:
        with pytest.raises(ProcessAnalysisError, match="inclusive limit"):
            serializer(object())


def test_ipc_deserializer_rejects_oversize_invalid_pickle_and_wrong_exact_type():
    with pytest.raises(ProcessAnalysisError, match="inclusive limit"):
        process_module._deserialize_bounded(
            b"x" * (MAX_CONTROL_BYTES + 1),
            limit=MAX_CONTROL_BYTES,
            expected_type=ControlEnvelope,
            label="control",
        )
    with pytest.raises(ProcessAnalysisError, match="not valid pickle"):
        process_module._deserialize_bounded(
            b"not-pickle",
            limit=MAX_CONTROL_BYTES,
            expected_type=ControlEnvelope,
            label="control",
        )
    with pytest.raises(ProcessAnalysisError, match="unexpected type"):
        process_module._deserialize_bounded(
            pickle.dumps(SimpleNamespace()),
            limit=MAX_CONTROL_BYTES,
            expected_type=ControlEnvelope,
            label="control",
        )


def _valid_metadata() -> ProcessJobMetadata:
    return ProcessJobMetadata(
        job_id="job-1",
        epoch=0,
        selection_revision=0,
        stage="ordinary_30",
        frame_start=0,
        frame_stop=600,
        requested_bin=None,
        selection_mode="select",
        depends_on=None,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"job_id": ""},
        {"epoch": True},
        {"selection_revision": 1.0},
        {"frame_start": -1, "frame_stop": 599},
        {"frame_stop": 599},
        {"requested_bin": True},
        {"requested_bin": -1},
        {"stage": "invented"},
        {"selection_mode": "invented"},
        {"depends_on": 1},
    ],
)
def test_process_job_metadata_rejects_coercive_or_malformed_identity(changes):
    with pytest.raises(ProcessAnalysisError):
        process_module._validate_job_metadata(replace(_valid_metadata(), **changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"schema": "wrong"},
        {"version": 2},
        {"sequence": True},
        {"sequence": -1},
        {"config_sha256": "b" * 64},
    ],
)
def test_process_protocol_rejects_schema_version_sequence_and_config(changes):
    envelope = ControlEnvelope(
        schema=CONTROL_SCHEMA,
        version=PROTOCOL_VERSION,
        kind="shutdown",
        sequence=0,
        config_sha256="a" * 64,
    )
    with pytest.raises(ProcessAnalysisError):
        process_module._validate_common_envelope(
            replace(envelope, **changes),
            expected_config_sha256="a" * 64,
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"name": ""},
        {"shape": [600, 1, 1, 8]},
        {"shape": (600, 1, 1, 9)},
        {"dtype_str": np.dtype(np.complex128).str},
        {"byte_count": True},
        {"byte_count": 600 * 1 * 1 * 8 * np.dtype(np.complex64).itemsize + 1},
    ],
)
def test_child_rejects_malformed_shared_window_before_opening_mapping(changes):
    config = _config()
    digest = run_config_hash(config)
    parent, child = process_module.multiprocessing.Pipe(duplex=True)
    thread = threading.Thread(
        target=process_module._child_entry,
        args=(
            child,
            json.dumps(
                config,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8"),
            digest,
        ),
    )
    thread.start()
    try:
        assert parent.poll(3.0)
        ready, _ = process_module._receive_result(parent)
        assert ready.kind == "ready" and ready.sequence == 0
        descriptor = SharedWindowDescriptor(
            name="mapping-must-not-open",
            shape=(600, 1, 1, 8),
            dtype_str=np.dtype(np.complex64).str,
            byte_count=600 * 1 * 1 * 8 * np.dtype(np.complex64).itemsize,
        )
        forged = ControlEnvelope(
            schema=CONTROL_SCHEMA,
            version=PROTOCOL_VERSION,
            kind="analyze",
            sequence=1,
            config_sha256=digest,
            job=_valid_metadata(),
            window=replace(descriptor, **changes),
        )
        parent.send_bytes(process_module.serialize_control_envelope(forged))
        assert parent.poll(3.0)
        fatal, _ = process_module._receive_result(parent)
        assert fatal.kind == "fatal"
        assert fatal.sequence == 1
        assert fatal.config_sha256 == digest
        assert fatal.error
    finally:
        parent.close()
        thread.join(timeout=3.0)
    assert thread.is_alive() is False


@pytest.mark.parametrize("capacity_delta", [-1, 1, 4096])
def test_child_rejects_inexact_mapping_capacity_before_accessing_view(
    monkeypatch, capacity_delta
):
    config = _config()
    digest = run_config_hash(config)
    frames = _frames()
    byte_count = frames.nbytes
    expected_capacity = process_module._expected_attached_mapping_size(byte_count)
    opened = []
    callback_calls = []

    def callback_factory(_config):
        def forbidden_callback(_job):
            callback_calls.append(True)
            raise AssertionError("analyzer must not run for wrong mapping capacity")

        return forbidden_callback

    class InexactMapping:
        def __init__(self, *, name, create):
            assert name == "inexact-capacity"
            assert create is False
            self.size = expected_capacity + capacity_delta
            self.closed = False
            opened.append(self)

        @property
        def buf(self):
            raise AssertionError("mapping view must not be opened for wrong capacity")

        def close(self):
            self.closed = True

    monkeypatch.setattr(process_module, "SharedMemory", InexactMapping)
    monkeypatch.setattr(
        process_module, "baseline_analysis_function", callback_factory
    )
    parent, child = process_module.multiprocessing.Pipe(duplex=True)
    thread = threading.Thread(
        target=process_module._child_entry,
        args=(
            child,
            json.dumps(
                config,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8"),
            digest,
        ),
    )
    thread.start()
    try:
        assert parent.poll(3.0)
        ready, _ = process_module._receive_result(parent)
        assert ready.kind == "ready"
        envelope = ControlEnvelope(
            schema=CONTROL_SCHEMA,
            version=PROTOCOL_VERSION,
            kind="analyze",
            sequence=1,
            config_sha256=digest,
            job=_valid_metadata(),
            window=SharedWindowDescriptor(
                name="inexact-capacity",
                shape=frames.shape,
                dtype_str=frames.dtype.str,
                byte_count=byte_count,
            ),
        )
        parent.send_bytes(process_module.serialize_control_envelope(envelope))
        assert parent.poll(3.0)
        fatal, _ = process_module._receive_result(parent)
        assert fatal.kind == "fatal"
        assert fatal.sequence == 1
        assert "mapping capacity mismatch" in fatal.error
    finally:
        parent.close()
        thread.join(timeout=3.0)
    assert thread.is_alive() is False
    assert len(opened) == 1
    assert opened[0].closed is True
    assert callback_calls == []


def test_child_callback_fatal_reports_completed_materialization_and_callback_time(
    monkeypatch,
):
    config = _config()
    digest = run_config_hash(config)
    frames = _frames()
    byte_count = frames.nbytes
    shared = process_module.SharedMemory(create=True, size=byte_count)
    np.copyto(
        np.ndarray(frames.shape, dtype=frames.dtype, buffer=shared.buf),
        frames,
    )
    callback_entered = threading.Event()

    def callback_factory(_config):
        def fail_after_materialization(job):
            assert job.raw_frames is not None
            assert job.raw_frames.shape == frames.shape
            assert job.raw_frames.dtype == frames.dtype
            assert job.raw_frames.nbytes == byte_count
            assert job.raw_frames.flags.owndata is True
            assert job.raw_frames.flags.writeable is False
            np.testing.assert_array_equal(job.raw_frames, frames)
            callback_entered.set()
            raise RuntimeError("synthetic child callback failure")

        return fail_after_materialization

    monkeypatch.setattr(
        process_module, "baseline_analysis_function", callback_factory
    )
    parent, child = process_module.multiprocessing.Pipe(duplex=True)
    thread = threading.Thread(
        target=process_module._child_entry,
        args=(
            child,
            json.dumps(
                config,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8"),
            digest,
        ),
    )
    thread.start()
    try:
        assert parent.poll(3.0)
        ready, _ = process_module._receive_result(parent)
        assert ready.kind == "ready"
        envelope = ControlEnvelope(
            schema=CONTROL_SCHEMA,
            version=PROTOCOL_VERSION,
            kind="analyze",
            sequence=1,
            config_sha256=digest,
            job=_valid_metadata(),
            window=SharedWindowDescriptor(
                name=shared.name,
                shape=frames.shape,
                dtype_str=frames.dtype.str,
                byte_count=byte_count,
            ),
        )
        parent.send_bytes(process_module.serialize_control_envelope(envelope))
        assert parent.poll(3.0)
        fatal, _ = process_module._receive_result(parent)
        assert callback_entered.is_set()
        assert fatal.kind == "fatal"
        assert fatal.sequence == 1
        assert "synthetic child callback failure" in fatal.error
        assert type(fatal.child_materialization_elapsed_s) is float
        assert fatal.child_materialization_elapsed_s >= 0.0
        assert type(fatal.child_callback_elapsed_s) is float
        assert fatal.child_callback_elapsed_s >= 0.0
    finally:
        parent.close()
        shared.close()
        shared.unlink()
        thread.join(timeout=3.0)
    assert thread.is_alive() is False


def test_parent_fatal_error_preserves_available_child_and_transport_timings(
    monkeypatch,
):
    config = _config()
    digest = run_config_hash(config)
    request = _selector_request(config)
    backend = SpawnBaselineBackend(config, digest)

    class AliveProcess:
        exitcode = None

        @staticmethod
        def is_alive():
            return True

    class Connection:
        def __init__(self):
            self.payloads = []

        def send_bytes(self, payload):
            self.payloads.append(payload)

    connection = Connection()
    backend._process = AliveProcess()
    backend._parent_connection = connection
    descriptor = SharedWindowDescriptor(
        name="synthetic",
        shape=(600, 1, 1, 8),
        dtype_str=np.dtype(np.complex64).str,
        byte_count=600 * 1 * 1 * 8 * np.dtype(np.complex64).itemsize,
    )
    monkeypatch.setattr(backend, "_copy_request", lambda _job: (descriptor, 0.01))
    monkeypatch.setattr(
        backend,
        "_wait_for_result",
        lambda _deadline: (
            ResultEnvelope(
                schema=RESULT_SCHEMA,
                version=PROTOCOL_VERSION,
                kind="fatal",
                sequence=1,
                config_sha256=digest,
                child_materialization_elapsed_s=0.11,
                child_callback_elapsed_s=0.22,
                error="RuntimeError: synthetic child callback failure",
            ),
            321,
        ),
    )

    with pytest.raises(ProcessAnalysisError) as captured:
        backend.execute(request)

    performance = captured.value.performance
    assert performance["process_sequence"] == 1
    assert performance["parent_copy_elapsed_s"] == pytest.approx(0.01)
    assert performance["parent_send_elapsed_s"] >= 0.0
    assert performance["parent_wait_receive_elapsed_s"] >= 0.0
    assert performance["child_materialization_elapsed_s"] == pytest.approx(0.11)
    assert performance["child_callback_elapsed_s"] == pytest.approx(0.22)
    assert performance["process_round_trip_elapsed_s"] >= 0.0
    assert performance["process_control_bytes"] == len(connection.payloads[0])
    assert performance["process_result_bytes"] == 321
    backend._process = None
    backend._parent_connection = None
    backend.close(force=False)


@pytest.mark.parametrize(
    "changes",
    [
        {"captured_frames": ()},
        {"captured_frames": [np.zeros((1, 1, 8), dtype=np.complex64)] * 600},
        {"raw_frames": np.zeros((600, 1, 1, 8), dtype=np.complex64)},
    ],
)
def test_parent_bridge_rejects_wrong_request_storage_before_transport(changes):
    config = _config()
    request = _selector_request(config)
    invalid = SimpleNamespace(**request.__dict__)
    for name, value in changes.items():
        setattr(invalid, name, value)
    backend = SpawnBaselineBackend(config, run_config_hash(config))
    try:
        with pytest.raises(ProcessAnalysisError):
            backend._copy_request(invalid)
    finally:
        backend.close(force=False)


def test_parent_bridge_rejects_geometry_dtype_or_mutability_change_within_pass():
    config = _config()
    request = _selector_request(config)
    backend = SpawnBaselineBackend(config, run_config_hash(config))
    try:
        backend._copy_request(request)
        changed_geometry = SimpleNamespace(**request.__dict__)
        changed_frames = []
        for frame in request.captured_frames:
            changed = np.zeros((1, 1, 9), dtype=frame.dtype)
            changed.setflags(write=False)
            changed_frames.append(changed)
        changed_geometry.captured_frames = tuple(changed_frames)
        with pytest.raises(ProcessAnalysisError, match="disagrees with config"):
            backend._copy_request(changed_geometry)

        for forged in (
            np.array(request.captured_frames[0], copy=True),
            request.captured_frames[0].astype(np.complex128),
            request.captured_frames[0][:, :, :4],
        ):
            invalid = SimpleNamespace(**request.__dict__)
            if (
                forged.dtype == request.captured_frames[0].dtype
                and forged.shape == request.captured_frames[0].shape
            ):
                # The exact-shape case is intentionally writable.
                assert forged.flags.writeable
            else:
                forged.setflags(write=False)
            invalid.captured_frames = (forged, *request.captured_frames[1:])
            with pytest.raises(
                ProcessAnalysisError,
                match="(disagrees with config|homogeneous|dtype must be complex64)",
            ):
                backend._copy_request(invalid)
    finally:
        backend.close(force=False)


def test_late_invalid_frame_is_rejected_before_any_shared_window_byte_changes():
    config = _config()
    request = _selector_request(config)
    backend = SpawnBaselineBackend(config, run_config_hash(config))
    try:
        backend._copy_request(request)
        assert backend._shared_memory is not None
        before = bytes(backend._shared_memory.buf)
        invalid_last = np.array(request.captured_frames[-1], copy=True)
        assert invalid_last.flags.writeable is True
        forged = SimpleNamespace(**request.__dict__)
        forged.captured_frames = (*request.captured_frames[:-1], invalid_last)

        with pytest.raises(ProcessAnalysisError, match="homogeneous"):
            backend._copy_request(forged)

        assert bytes(backend._shared_memory.buf) == before
    finally:
        backend.close(force=False)


def test_spawn_config_mismatch_fails_ready_handshake_and_cleans_owned_resources():
    config = _config()
    backend = SpawnBaselineBackend(config, "f" * 64)
    with pytest.raises(ProcessAnalysisError):
        backend.start(timeout_s=5.0)
    assert backend.is_closed is True
    assert backend._parent_connection is None
    assert backend._process is None
    assert backend._shared_memory is None


def test_owned_child_death_is_fatal_without_respawn_and_new_backend_has_fresh_pid():
    config = _config()
    digest = run_config_hash(config)
    request = _selector_request(config)
    first = SpawnBaselineBackend(config, digest)
    second = None
    first_close_error = None
    try:
        first.start(timeout_s=5.0)
        first_pid = first.observation()["pid"]
        assert first._process is not None
        first._process.terminate()
        first._process.join(timeout=3.0)
        with pytest.raises(ProcessAnalysisError, match="(exited|transport)"):
            first.execute(request)
        assert first.observation()["pid"] == first_pid
        assert first.observation()["alive"] is False
    finally:
        try:
            first.close(force=False)
        except ProcessAnalysisError as exc:
            first_close_error = exc
    assert isinstance(first_close_error, ProcessAnalysisError)
    assert "graceful shutdown acknowledgment" in str(first_close_error)

    second = SpawnBaselineBackend(config, digest)
    try:
        second.start(timeout_s=5.0)
        second_pid = second.observation()["pid"]
        assert type(second_pid) is int and second_pid > 0
        assert second_pid != first_pid
    finally:
        second.close(force=False)
    assert first.is_closed is True and second.is_closed is True


def test_one_in_flight_guard_precedes_copy_and_cannot_overwrite_shared_window(
    monkeypatch,
):
    config = _config()
    request = _selector_request(config)
    backend = SpawnBaselineBackend(config, run_config_hash(config))
    copy_calls = []
    with backend._state_lock:
        backend._process = SimpleNamespace()
        backend._parent_connection = SimpleNamespace()
        backend._active = True
    monkeypatch.setattr(
        backend,
        "_copy_request",
        lambda job: copy_calls.append(job) or (_ for _ in ()).throw(
            AssertionError("second request reached shared-memory copy")
        ),
    )

    with pytest.raises(ProcessAnalysisError, match="one in-flight"):
        backend.execute(request)
    assert copy_calls == []
    with backend._state_lock:
        backend._process = None
        backend._parent_connection = None
        backend._active = False
    backend.close(force=False)


def test_result_sequence_config_kind_and_job_identity_are_fail_closed(monkeypatch):
    config = _config()
    digest = run_config_hash(config)
    request = _selector_request(config)
    executed = baseline_analysis_function(config)(_materialize_replay_job(request))
    backend = SpawnBaselineBackend(config, digest)

    class AliveProcess:
        exitcode = None

        @staticmethod
        def is_alive():
            return True

    backend._process = AliveProcess()
    backend._parent_connection = SimpleNamespace()
    descriptor = SharedWindowDescriptor(
        name="synthetic",
        shape=(600, 1, 1, 8),
        dtype_str=np.dtype(np.complex64).str,
        byte_count=600 * 1 * 1 * 8 * np.dtype(np.complex64).itemsize,
    )
    monkeypatch.setattr(backend, "_copy_request", lambda _request: (descriptor, 0.0))
    monkeypatch.setattr(process_module, "_send_control", lambda *_args: 100)

    forged_responses = (
        replace(
            ResultEnvelope(
                schema=RESULT_SCHEMA,
                version=PROTOCOL_VERSION,
                kind="result",
                sequence=1,
                config_sha256=digest,
                executed=executed,
                child_materialization_elapsed_s=0.1,
                child_callback_elapsed_s=0.2,
            ),
            sequence=2,
        ),
        ResultEnvelope(
            schema=RESULT_SCHEMA,
            version=PROTOCOL_VERSION,
            kind="ready",
            sequence=1,
            config_sha256=digest,
        ),
        ResultEnvelope(
            schema=RESULT_SCHEMA,
            version=PROTOCOL_VERSION,
            kind="result",
            sequence=1,
            config_sha256="f" * 64,
            executed=executed,
            child_materialization_elapsed_s=0.1,
            child_callback_elapsed_s=0.2,
        ),
        ResultEnvelope(
            schema=RESULT_SCHEMA,
            version=PROTOCOL_VERSION,
            kind="result",
            sequence=1,
            config_sha256=digest,
            executed=replace(
                executed,
                result=replace(executed.result, job_id="forged-job"),
            ),
            child_materialization_elapsed_s=0.1,
            child_callback_elapsed_s=0.2,
        ),
        ResultEnvelope(
            schema=RESULT_SCHEMA,
            version=PROTOCOL_VERSION,
            kind="result",
            sequence=1,
            config_sha256=digest,
            executed=None,
            child_materialization_elapsed_s=0.1,
            child_callback_elapsed_s=0.2,
        ),
    )
    for response in forged_responses:
        monkeypatch.setattr(
            backend,
            "_wait_for_result",
            lambda _deadline, response=response: (response, 200),
        )
        with pytest.raises(ProcessAnalysisError):
            backend.execute(request)
        assert backend._active is False
        assert backend._sequence == 1

    backend._process = None
    backend._parent_connection = None
    backend.close(force=False)


def test_forced_close_uses_one_containment_allowance_and_cleans_every_owned_handle(
    monkeypatch,
):
    config = _config()
    backend = SpawnBaselineBackend(config, run_config_hash(config))
    monotonic_s = [10.0]

    class StuckProcess:
        pid = 4321
        exitcode = None

        def __init__(self):
            self.alive = True
            self.killed = False
            self.join_timeouts = []
            self.terminated = 0
            self.kill_calls = 0
            self.close_calls = 0

        def is_alive(self):
            return self.alive

        def join(self, timeout):
            self.join_timeouts.append(timeout)
            monotonic_s[0] += timeout
            if self.killed:
                self.alive = False
                self.exitcode = -9

        def terminate(self):
            self.terminated += 1

        def kill(self):
            self.kill_calls += 1
            self.killed = True

        def close(self):
            self.close_calls += 1

    class FakeConnection:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1

    class FakeSharedMemory:
        def __init__(self):
            self.close_calls = 0
            self.unlink_calls = 0

        def close(self):
            self.close_calls += 1

        def unlink(self):
            self.unlink_calls += 1

    process = StuckProcess()
    connection = FakeConnection()
    shared = FakeSharedMemory()
    backend._process = process
    backend._parent_connection = connection
    backend._shared_memory = shared
    backend._pid = process.pid
    backend._active = True
    monkeypatch.setattr(
        process_module.time, "monotonic", lambda: monotonic_s[0]
    )
    monkeypatch.setattr(
        process_module,
        "replay_process_memory_bytes",
        lambda _pid: (None, None),
    )

    with pytest.raises(ProcessAnalysisError, match="terminal containment"):
        backend.close(deadline_monotonic_s=10.0, force=True)

    assert process.terminated == 1
    assert process.kill_calls == 1
    assert sum(process.join_timeouts) <= 1.0
    assert process.alive is False
    assert process.close_calls == 1
    assert connection.close_calls == 1
    assert shared.close_calls == 1
    assert shared.unlink_calls == 1
    assert backend.is_closed is True
    assert backend._process is None
    assert backend._parent_connection is None
    assert backend._shared_memory is None


def test_concurrent_close_sends_one_ack_and_cleans_owned_resources_once(
    monkeypatch,
):
    config = _config()
    digest = run_config_hash(config)
    backend = SpawnBaselineBackend(config, digest)
    ack_entered = threading.Event()
    release_ack = threading.Event()
    second_started = threading.Event()
    calls = {"send": 0, "ack": 0}

    class GracefulProcess:
        pid = 4321

        def __init__(self):
            self.alive = True
            self.exitcode = None
            self.join_calls = 0
            self.close_calls = 0

        def is_alive(self):
            return self.alive

        def join(self, timeout):
            assert timeout >= 0.0
            self.join_calls += 1

        def close(self):
            self.close_calls += 1

    class OwnedResource:
        def __init__(self):
            self.close_calls = 0
            self.unlink_calls = 0

        def close(self):
            self.close_calls += 1

        def unlink(self):
            self.unlink_calls += 1

    process = GracefulProcess()
    connection = OwnedResource()
    shared = OwnedResource()
    backend._process = process
    backend._parent_connection = connection
    backend._shared_memory = shared
    backend._pid = None

    def send_once(_connection, envelope):
        assert envelope.kind == "shutdown"
        calls["send"] += 1
        return 10

    def acknowledge_once(_connection, _deadline):
        calls["ack"] += 1
        ack_entered.set()
        assert release_ack.wait(timeout=3.0)
        process.alive = False
        process.exitcode = 0
        return (
            ResultEnvelope(
                schema=RESULT_SCHEMA,
                version=PROTOCOL_VERSION,
                kind="shutdown_ack",
                sequence=1,
                config_sha256=digest,
            ),
            10,
        )

    monkeypatch.setattr(process_module, "_send_control", send_once)
    monkeypatch.setattr(backend, "_wait_for_shutdown_ack", acknowledge_once)
    outcomes = []

    def close_backend(label):
        if label == "second":
            second_started.set()
        try:
            backend.close(
                deadline_monotonic_s=process_module.time.monotonic() + 2.0,
                force=False,
            )
            outcomes.append((label, None))
        except BaseException as exc:  # pragma: no cover - asserted below
            outcomes.append((label, exc))

    first = threading.Thread(target=close_backend, args=("first",))
    second = threading.Thread(target=close_backend, args=("second",))
    first.start()
    assert ack_entered.wait(timeout=3.0)
    second.start()
    assert second_started.wait(timeout=3.0)
    release_ack.set()
    first.join(timeout=3.0)
    second.join(timeout=3.0)

    assert first.is_alive() is False and second.is_alive() is False
    assert sorted(label for label, _ in outcomes) == ["first", "second"]
    assert all(error is None for _, error in outcomes)
    assert calls == {"send": 1, "ack": 1}
    assert process.join_calls == 1
    assert process.close_calls == 1
    assert connection.close_calls == 1
    assert shared.close_calls == 1
    assert shared.unlink_calls == 1
    assert backend.is_closed is True
    assert backend.observation()["exitcode"] == 0


def test_cleanup_failure_retries_remaining_handle_without_losing_first_error():
    config = _config()
    backend = SpawnBaselineBackend(config, run_config_hash(config))

    class FlakyConnection:
        def __init__(self):
            self.close_calls = 0

        def close(self):
            self.close_calls += 1
            if self.close_calls == 1:
                raise OSError("synthetic first connection close failure")

    class SharedResource:
        def __init__(self):
            self.close_calls = 0
            self.unlink_calls = 0

        def close(self):
            self.close_calls += 1

        def unlink(self):
            self.unlink_calls += 1

    connection = FlakyConnection()
    shared = SharedResource()
    backend._parent_connection = connection
    backend._shared_memory = shared

    with pytest.raises(ProcessAnalysisError) as first:
        backend.close(force=False)
    assert "synthetic first connection close failure" in str(first.value)
    assert backend.is_closed is False

    with pytest.raises(ProcessAnalysisError) as second:
        backend.close(force=False)
    assert str(second.value) == str(first.value)
    assert connection.close_calls == 2
    assert shared.close_calls == 1
    assert shared.unlink_calls == 1
    assert backend._parent_connection is None
    assert backend._shared_memory is None
    assert backend._process is None
    assert backend.is_closed is True

    with pytest.raises(ProcessAnalysisError) as third:
        backend.close(force=False)
    assert str(third.value) == str(first.value)
    assert connection.close_calls == 2
    assert shared.close_calls == 1
    assert shared.unlink_calls == 1


def test_observation_racing_close_never_uses_a_closed_process_handle(monkeypatch):
    config = _config()
    backend = SpawnBaselineBackend(config, run_config_hash(config))
    telemetry_entered = threading.Event()
    release_telemetry = threading.Event()
    telemetry_lock = threading.Lock()
    telemetry_calls = 0

    class RacingProcess:
        pid = 4321

        def __init__(self):
            self.alive = True
            self.exitcode = None
            self.closed = False

        def is_alive(self):
            if self.closed:
                raise ValueError("process handle is closed")
            return self.alive

        def terminate(self):
            self.alive = False
            self.exitcode = -15

        def join(self, timeout):
            assert timeout >= 0.0

        def kill(self):
            self.alive = False
            self.exitcode = -9

        def close(self):
            self.closed = True

    class Connection:
        def close(self):
            pass

    process = RacingProcess()
    backend._process = process
    backend._parent_connection = Connection()
    backend._pid = process.pid

    def observe_memory(_pid):
        nonlocal telemetry_calls
        with telemetry_lock:
            telemetry_calls += 1
            call = telemetry_calls
        if call == 1:
            telemetry_entered.set()
            assert release_telemetry.wait(timeout=3.0)
        return (111, 222)

    monkeypatch.setattr(
        process_module, "replay_process_memory_bytes", observe_memory
    )
    monkeypatch.setattr(process_module, "_send_control", lambda *_args: 10)

    def graceful_ack(_connection, _deadline):
        process.alive = False
        process.exitcode = 0
        return (
            ResultEnvelope(
                schema=RESULT_SCHEMA,
                version=PROTOCOL_VERSION,
                kind="shutdown_ack",
                sequence=1,
                config_sha256=run_config_hash(config),
            ),
            10,
        )

    monkeypatch.setattr(backend, "_wait_for_shutdown_ack", graceful_ack)
    observations = []
    observation_errors = []

    def observe():
        try:
            observations.append(backend.observation())
        except BaseException as exc:  # pragma: no cover - asserted below
            observation_errors.append(exc)

    observer = threading.Thread(target=observe)
    observer.start()
    assert telemetry_entered.wait(timeout=3.0)
    close_error = None
    try:
        backend.close(
            deadline_monotonic_s=process_module.time.monotonic() + 1.0,
            force=False,
        )
    except BaseException as exc:  # pragma: no cover - asserted below
        close_error = exc
    finally:
        release_telemetry.set()
    observer.join(timeout=3.0)

    assert observer.is_alive() is False
    assert observation_errors == []
    assert close_error is None
    assert len(observations) == 1
    assert observations[0]["pid"] == 4321
    assert observations[0]["current_rss_bytes"] is None
    assert backend.is_closed is True


def test_never_started_backend_cleanup_is_idempotent_and_allocates_no_mapping():
    config = _config()
    backend = SpawnBaselineBackend(config, run_config_hash(config))
    backend.close(force=False)
    backend.close(force=False)
    assert backend.is_closed is True
    assert backend._process is None
    assert backend._parent_connection is None
    assert backend._shared_memory is None


def test_spawn_child_module_imports_no_source_reference_qt_or_hardware_modules():
    path = ROOT / "src" / "replay_compare" / "process_analysis.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(("." * node.level) + (node.module or ""))
    forbidden_fragments = (
        "replay_compare.source",
        "replay_compare.reference",
        "replay_compare.ui",
        "PySide6",
        "pyqtgraph",
        "serial",
    )
    assert not any(
        fragment in imported
        for imported in imports
        for fragment in forbidden_fragments
    )
