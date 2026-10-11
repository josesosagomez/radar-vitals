"""Event-synchronized replay runtime tests; no timing sleeps or hardware."""

from __future__ import annotations

import gc
import threading
import weakref
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import yaml

import src.replay_compare.engine as engine_module
from src.live_motion.runtime import ExecutedAnalysis
from src.live_motion.scheduler import AnalysisResult, SelectionDecision
from src.replay_compare.clock import PlaybackClock
from src.replay_compare.engine import (
    BaselineController,
    ReplayRuntime,
    baseline_analysis_function,
)
from src.replay_compare.evidence import ReplayEvidenceWriter, verify_replay_artifacts
from src.replay_compare.process_analysis import ProcessAnalysisError, ProcessExecution
from src.replay_compare.source import ReplaySourceError


FRAME = np.asarray([[[1.0 + 1.0j, 2.0 + 0.0j]]], dtype=np.complex64)


class _BarrierSource:
    initial_sha256 = "a" * 64
    opened_file_identity = {"size": 1234, "device": 1, "inode": 2}

    def __init__(
        self,
        frame_count: int,
        *,
        block_index: int | None = None,
        failure_index: int | None = None,
        malformed_index: int | None = None,
        before_return: threading.Event | None = None,
        frames: np.ndarray | None = None,
    ):
        self.frame_count = frame_count
        self.cursor = 0
        self.block_index = block_index
        self.failure_index = failure_index
        self.malformed_index = malformed_index
        self.before_return = before_return
        self.frames = frames
        self.block_entered = threading.Event()
        self.block_release = threading.Event()
        self.closed = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.closed.set()

    def read_next(self):
        index = self.cursor
        if self.failure_index == index:
            raise ReplaySourceError("injected decode failure")
        if self.block_index == index:
            self.block_entered.set()
            if not self.block_release.wait(timeout=3.0):
                raise TimeoutError("test did not release the bounded source")
        if self.before_return is not None and index == self.malformed_index:
            if not self.before_return.wait(timeout=3.0):
                raise TimeoutError("analysis result was not ready at invalid boundary")
        if index >= self.frame_count:
            return None
        self.cursor += 1
        cube = (
            np.array(self.frames[index], copy=True)
            if self.frames is not None
            else FRAME
        )
        if index == self.malformed_index:
            cube = np.zeros((1, 2), dtype=np.complex64)
        return index, cube


class _RecordingEvidence:
    def __init__(self):
        self.attempts = []
        self.controller_events = []
        self.playback_events = []
        self.performance = []
        self.attempt_written = threading.Event()
        self.pause_acknowledged = threading.Event()

    def write_attempt(self, executed, disposition):
        self.attempts.append((executed, disposition))
        self.attempt_written.set()

    def write_controller_event(self, event):
        self.controller_events.append(event)

    def write_playback_event(self, event):
        self.playback_events.append(dict(event))
        if event.get("kind") == "pause_acknowledged":
            self.pause_acknowledged.set()

    def write_performance(self, event):
        self.performance.append(dict(event))


class _DurabilityBarrierEvidence(_RecordingEvidence):
    """Hold the scientific-attempt write at its durability boundary."""

    def __init__(self, *, fail: bool = False):
        super().__init__()
        self.fail = fail
        self.write_entered = threading.Event()
        self.write_release = threading.Event()

    def write_attempt(self, executed, disposition):
        self.write_entered.set()
        if not self.write_release.wait(timeout=3.0):
            raise TimeoutError("test did not release the evidence write")
        if self.fail:
            raise OSError("injected evidence persistence failure")
        super().write_attempt(executed, disposition)


class _BarrierApplyController(BaselineController):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.apply_mutated = threading.Event()
        self.apply_release = threading.Event()

    def handle_executed(self, executed):
        update = super().handle_executed(executed)
        self.apply_mutated.set()
        if not self.apply_release.wait(timeout=3.0):
            raise TimeoutError("test did not release result application")
        return update


def _clock():
    return PlaybackClock(20.0, speed=4.0, monotonic=lambda: 0.0, wait=lambda _s: None)


class _FakeTime:
    def __init__(self):
        self.now = 0.0
        self.waits = []

    def monotonic(self):
        return self.now

    def wait(self, seconds):
        self.waits.append(seconds)
        self.now += seconds


def _successful_execution(job, *, ready: threading.Event | None = None):
    result = AnalysisResult(
        job_id=job.job_id,
        epoch=job.epoch,
        selection_revision=job.selection_revision,
        stage=job.stage,
        frame_start=job.frame_start,
        frame_stop=job.frame_stop,
        actual_bin=3,
        executed=True,
        status="completed",
        dsp={
            "hr_valid": True,
            "br_valid": True,
            "hr_raw": 70.0,
            "br_bpm": 12.0,
            "hr_result": {"accepted_candidate_rank": 0, "ahet_verified": True},
        },
        selection_decision=SelectionDecision(
            selected_bin=3,
            evidence={"fallback_used": False},
            selector_succeeded=True,
            dsp_succeeded=True,
            fallback_used=False,
            reason="eligible_dsp_pass",
        ),
    )
    if ready is not None:
        ready.set()
    return ExecutedAnalysis(result, {}, 0.01, 0.002)


def _runtime(source, evidence, analyze, *, frame_limit=1000, shutdown_timeout=1.0):
    return ReplayRuntime(
        source=source,
        controller=BaselineController({}, generation=1, speed=4.0),
        evidence=evidence,
        analysis_function=analyze,
        frame_limit=frame_limit,
        clock=_clock(),
        shutdown_timeout_s=shutdown_timeout,
    )


def _join_thread(thread: threading.Thread, label: str) -> None:
    thread.join(timeout=3.0)
    assert not thread.is_alive(), f"{label} did not terminate"


def _arm_bounded_cleanup(request, runtime, source, *release_events) -> None:
    def cleanup():
        source.block_release.set()
        for event in release_events:
            event.set()
        runtime.stop(timeout_s=0.5)

    request.addfinalizer(cleanup)


def test_stop_while_analysis_active_persists_terminal_attempt_without_late_publication(
    request,
):
    source = _BarrierSource(1000, block_index=600)
    evidence = _RecordingEvidence()
    analysis_started = threading.Event()
    analysis_release = threading.Event()
    materialized_job_ref = None
    materialized_array_ref = None

    def analyze(job):
        nonlocal materialized_job_ref, materialized_array_ref
        assert isinstance(job, engine_module._FrozenReplayAnalysisJob)
        assert job.raw_frames is not None and job.captured_frames == ()
        materialized_job_ref = weakref.ref(job)
        materialized_array_ref = weakref.ref(job.raw_frames)
        analysis_started.set()
        assert analysis_release.wait(timeout=3.0)
        return _successful_execution(job)

    runtime = _runtime(source, evidence, analyze)
    _arm_bounded_cleanup(request, runtime, source, analysis_release)
    runtime.start()
    assert analysis_started.wait(timeout=3.0)
    assert source.block_entered.wait(timeout=3.0)
    stopper = threading.Thread(target=runtime.stop, kwargs={"timeout_s": 2.0})
    stopper.start()
    assert runtime._stop.wait(timeout=1.0)
    stopped_boundary = runtime.latest_snapshot.processed_boundary
    stopped_selection = (
        runtime.latest_snapshot.selected_bin,
        runtime.latest_snapshot.selection_revision,
    )
    source.block_release.set()
    analysis_release.set()
    _join_thread(stopper, "runtime stop")

    assert len(evidence.attempts) == 1
    _, disposition = evidence.attempts[0]
    assert disposition.disposition == "expired"
    assert disposition.reason == "playback_denominator_frozen"
    assert disposition.hr_accepted is False
    assert disposition.br_accepted is False
    assert runtime.latest_snapshot.selected_bin is None
    assert runtime.latest_snapshot.processed_boundary == stopped_boundary
    assert (
        runtime.latest_snapshot.selected_bin,
        runtime.latest_snapshot.selection_revision,
    ) == stopped_selection
    assert source.cursor == stopped_boundary + 1
    assert any(
        event.get("kind") == "read_ahead_discarded_at_stop"
        and event.get("boundary") == stopped_boundary
        for event in evidence.playback_events
    )
    assert runtime.clean_shutdown is True
    assert source.closed.is_set()
    gc.collect()
    assert materialized_job_ref is not None and materialized_job_ref() is None
    assert materialized_array_ref is not None and materialized_array_ref() is None


def test_source_preflight_time_is_excluded_by_reanchoring_before_first_frame():
    fake_time = _FakeTime()

    class PreflightSource(_BarrierSource):
        def __enter__(self):
            fake_time.now += 5.0
            return self

    source = PreflightSource(1)
    evidence = _RecordingEvidence()
    clock = PlaybackClock(
        20.0,
        speed=1.0,
        monotonic=fake_time.monotonic,
        wait=fake_time.wait,
    )
    runtime = ReplayRuntime(
        source=source,
        controller=BaselineController({}, generation=1),
        evidence=evidence,
        analysis_function=lambda _job: (_ for _ in ()).throw(
            AssertionError("one-frame run cannot analyze")
        ),
        frame_limit=1,
        clock=clock,
        shutdown_timeout_s=1.0,
    )
    runtime.start()
    assert runtime._ended.wait(timeout=3.0)
    runtime.stop(timeout_s=2.0)

    assert fake_time.waits == pytest.approx([0.05])
    assert runtime.latest_snapshot.processed_boundary == 1


def test_honest_runtime_denominator_freeze_artifacts_verify_end_to_end(tmp_path):
    source = _BarrierSource(1)
    writer = ReplayEvidenceWriter(
        tmp_path / "pass_1",
        {
            "generation": 1,
            "mode": "baseline",
            "source_kind": "synthetic_test_replay",
            "effective_config": {},
        },
    )
    runtime = _runtime(
        source,
        writer,
        lambda _job: (_ for _ in ()).throw(
            AssertionError("one-frame run cannot analyze")
        ),
        frame_limit=1,
    )
    runtime.start()
    assert runtime._ended.wait(timeout=3.0)
    runtime.stop(timeout_s=2.0)
    assert runtime.clean_shutdown is True
    writer.finalize(
        summary={"completion": "complete", "denominator_frames": 1},
        display_intervals=[],
        reference_audit=[],
    )

    assert verify_replay_artifacts(writer.pass_dir) == ()


def test_real_production_attempt_runtime_writer_and_eof_verify_without_repair(
    tmp_path, request
):
    config = yaml.safe_load(
        (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "live_demo_calibrated_config.yaml"
        ).read_text(encoding="utf-8")
    )
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

    frame_count = 601
    time_s = np.arange(frame_count, dtype=np.float64) / 20.0
    rng = np.random.default_rng(456)
    phase = (
        3.0 * np.sin(2 * np.pi * 0.25 * time_s)
        + 1.5 * np.sin(2 * np.pi * 0.50 * time_s)
        + 1.0 * np.sin(2 * np.pi * 0.75 * time_s)
        + 0.75 * np.sin(2 * np.pi * 1.00 * time_s)
        + 0.30 * np.sin(2 * np.pi * (71.0 / 60.0) * time_s)
        + 0.03 * rng.standard_normal(frame_count)
    )
    carrier = np.exp(2j * np.pi * np.arange(8, dtype=np.float64) / 8.0)
    frames = (
        np.exp(1j * phase)[:, None, None, None]
        * carrier[None, None, None, :]
    ).astype(np.complex64)
    source = _BarrierSource(
        frame_count,
        block_index=600,
        frames=frames,
    )
    writer = ReplayEvidenceWriter(
        tmp_path / "pass_1",
        {
            "generation": 1,
            "mode": "baseline",
            "source_kind": "synthetic_test_replay",
            "effective_config": config,
        },
    )
    production = baseline_analysis_function(config)
    analysis_done = threading.Event()

    def analyze(job):
        assert isinstance(job, engine_module._FrozenReplayAnalysisJob)
        assert job.captured_frames == ()
        assert job.raw_frames is not None
        assert job.raw_frames.flags.owndata is True
        assert job.raw_frames.flags.writeable is False
        np.testing.assert_array_equal(job.raw_frames, frames[:600])
        result = production(job)
        analysis_done.set()
        return result

    runtime = ReplayRuntime(
        source=source,
        controller=BaselineController(config, generation=1),
        evidence=writer,
        analysis_function=analyze,
        frame_limit=frame_count,
        clock=_clock(),
        shutdown_timeout_s=2.0,
    )
    _arm_bounded_cleanup(request, runtime, source)
    runtime.start()
    assert source.block_entered.wait(timeout=10.0)
    assert analysis_done.wait(timeout=10.0)
    source.block_release.set()
    assert runtime._ended.wait(timeout=10.0)
    runtime.stop(timeout_s=3.0)
    assert runtime.clean_shutdown is True
    writer.finalize(
        summary={"completion": "complete", "denominator_frames": frame_count},
        display_intervals=[],
        reference_audit=[],
    )

    assert verify_replay_artifacts(writer.pass_dir) == ()


def test_worker_materializes_once_for_injected_callback_and_releases_idle_locals(
    monkeypatch, request
):
    frame_count = 601
    frames = np.stack(
        [FRAME + np.complex64(index) for index in range(frame_count)], axis=0
    )
    source = _BarrierSource(
        frame_count, block_index=600, frames=frames
    )
    evidence = _RecordingEvidence()
    original_materialize = engine_module._materialize_replay_job
    materialized_count = 0
    callback_done = threading.Event()
    job_ref = None
    array_ref = None

    def materialize_once(job):
        nonlocal materialized_count
        materialized_count += 1
        return original_materialize(job)

    def analyze(job):
        nonlocal job_ref, array_ref
        assert isinstance(job, engine_module._FrozenReplayAnalysisJob)
        assert job.captured_frames == ()
        assert job.raw_frames is not None
        assert job.raw_frames.flags.owndata is True
        assert job.raw_frames.flags.writeable is False
        np.testing.assert_array_equal(job.raw_frames, frames[:600])
        job_ref = weakref.ref(job)
        array_ref = weakref.ref(job.raw_frames)
        callback_done.set()
        return _successful_execution(job)

    monkeypatch.setattr(engine_module, "_materialize_replay_job", materialize_once)
    runtime = _runtime(source, evidence, analyze, frame_limit=frame_count)
    assert runtime.process_backend is None
    _arm_bounded_cleanup(request, runtime, source)
    runtime.start()
    assert source.block_entered.wait(timeout=3.0)
    assert callback_done.wait(timeout=3.0)
    source.block_release.set()
    assert runtime._ended.wait(timeout=3.0)
    runtime.stop(timeout_s=2.0)

    assert materialized_count == 1
    assert len(evidence.attempts) == 1
    gc.collect()
    assert job_ref is not None and job_ref() is None
    assert array_ref is not None and array_ref() is None


def test_fatal_process_failure_is_durable_once_then_fails_without_redispatch(
    monkeypatch, request
):
    class FailingBackend:
        def __init__(self):
            self.is_closed = False
            self.is_started = False
            self.start_calls = 0
            self.execute_calls = 0
            self.close_calls = 0
            self.execute_entered = threading.Event()

        def start(self, timeout_s):
            assert timeout_s == pytest.approx(1.0)
            self.start_calls += 1
            self.is_started = True

        def execute(self, _job):
            self.execute_calls += 1
            self.execute_entered.set()
            raise ProcessAnalysisError(
                "synthetic fatal child transport",
                performance={
                    "process_sequence": 1,
                    "parent_copy_elapsed_s": 0.01,
                    "parent_send_elapsed_s": 0.02,
                    "parent_wait_receive_elapsed_s": 0.03,
                    "child_materialization_elapsed_s": 0.0,
                    "child_callback_elapsed_s": 0.0,
                    "process_round_trip_elapsed_s": 0.06,
                    "process_control_bytes": 123,
                    "process_result_bytes": 0,
                },
            )

        def close(
            self,
            *,
            deadline_monotonic_s,
            containment_deadline_monotonic_s,
            force,
        ):
            assert isinstance(deadline_monotonic_s, float)
            assert containment_deadline_monotonic_s >= deadline_monotonic_s
            self.close_calls += 1
            self.is_closed = True

        def observation(self):
            return {
                "pid": 123,
                "alive": not self.is_closed,
                "exitcode": None,
                "current_rss_bytes": None,
                "peak_rss_bytes": None,
            }

    source = _BarrierSource(601, block_index=600)
    evidence = _RecordingEvidence()
    backend = FailingBackend()
    runtime = ReplayRuntime(
        source=source,
        controller=BaselineController({}, generation=1, speed=4.0),
        evidence=evidence,
        analysis_function=None,
        process_backend=backend,
        frame_limit=601,
        clock=_clock(),
        shutdown_timeout_s=1.0,
    )
    result_queued = threading.Event()
    original_result_put = runtime._results.put

    def signal_result_put(*args, **kwargs):
        result = original_result_put(*args, **kwargs)
        result_queued.set()
        return result

    monkeypatch.setattr(runtime._results, "put", signal_result_put)
    _arm_bounded_cleanup(request, runtime, source)
    runtime.start()
    assert source.block_entered.wait(timeout=3.0)
    assert backend.execute_entered.wait(timeout=3.0)
    assert result_queued.wait(timeout=3.0)
    source.block_release.set()
    assert runtime._ended.wait(timeout=3.0)
    runtime.stop(timeout_s=2.0)

    assert backend.start_calls == 1
    assert backend.execute_calls == 1
    assert backend.close_calls == 1
    assert backend.is_closed is True
    assert len(evidence.attempts) == 1
    executed, disposition = evidence.attempts[0]
    assert executed.result.status == "failed"
    assert "synthetic fatal child transport" in executed.result.error
    assert disposition.disposition == "rejected"
    performance = [
        row for row in evidence.performance if row.get("kind") == "analysis"
    ]
    assert len(performance) == 1
    assert performance[0]["process_sequence"] == 1
    assert performance[0]["parent_copy_elapsed_s"] == pytest.approx(0.01)
    assert performance[0]["parent_send_elapsed_s"] == pytest.approx(0.02)
    assert performance[0]["parent_wait_receive_elapsed_s"] == pytest.approx(0.03)
    assert performance[0]["child_materialization_elapsed_s"] == 0.0
    assert performance[0]["child_callback_elapsed_s"] == 0.0
    assert performance[0]["process_round_trip_elapsed_s"] == pytest.approx(0.06)
    assert performance[0]["process_control_bytes"] == 123
    assert performance[0]["process_result_bytes"] == 0
    assert "synthetic fatal child transport" in performance[0][
        "fatal_backend_error"
    ]
    assert isinstance(runtime.failure, ProcessAnalysisError)
    assert "synthetic fatal child transport" in str(runtime.failure)
    assert runtime.clean_shutdown is False


def test_process_success_applies_once_reaches_eof_and_reports_owned_child_state(
    request,
):
    class SuccessfulBackend:
        def __init__(self):
            self.is_closed = False
            self.is_started = False
            self.start_calls = []
            self.execute_calls = []
            self.close_calls = []
            self.executed = threading.Event()

        def start(self, timeout_s):
            self.start_calls.append(timeout_s)
            self.is_started = True

        def execute(self, job):
            assert isinstance(job, engine_module._FrozenReplayAnalysisJob)
            assert job.raw_frames is None
            assert len(job.captured_frames) == 600
            self.execute_calls.append(job.job_id)
            self.executed.set()
            return ProcessExecution(
                executed=_successful_execution(job),
                sequence=1,
                parent_copy_elapsed_s=0.01,
                parent_send_elapsed_s=0.02,
                parent_wait_receive_elapsed_s=0.03,
                child_materialization_elapsed_s=0.04,
                child_callback_elapsed_s=0.05,
                round_trip_elapsed_s=0.15,
                control_bytes=100,
                result_bytes=200,
            )

        def close(
            self,
            *,
            deadline_monotonic_s,
            containment_deadline_monotonic_s,
            force,
        ):
            assert containment_deadline_monotonic_s >= deadline_monotonic_s
            self.close_calls.append((deadline_monotonic_s, force))
            self.is_closed = True

        def observation(self):
            return {
                "pid": 4321,
                "alive": not self.is_closed,
                "exitcode": None if not self.is_closed else 0,
                "current_rss_bytes": 123_456,
                "peak_rss_bytes": 234_567,
            }

    source = _BarrierSource(601, block_index=600)
    evidence = _RecordingEvidence()
    backend = SuccessfulBackend()
    runtime = ReplayRuntime(
        source=source,
        controller=BaselineController({}, generation=1, speed=4.0),
        evidence=evidence,
        analysis_function=None,
        process_backend=backend,
        frame_limit=601,
        clock=_clock(),
        shutdown_timeout_s=1.0,
    )
    _arm_bounded_cleanup(request, runtime, source)
    runtime.start()
    assert backend.executed.wait(timeout=3.0)
    assert source.block_entered.wait(timeout=3.0)
    assert runtime.analysis_process_observation() == {
        "pid": 4321,
        "alive": True,
        "exitcode": None,
        "current_rss_bytes": 123_456,
        "peak_rss_bytes": 234_567,
    }
    source.block_release.set()
    assert runtime._ended.wait(timeout=3.0)
    runtime.stop(timeout_s=2.0)

    assert backend.start_calls == [pytest.approx(1.0)]
    assert len(backend.execute_calls) == 1
    assert len(backend.close_calls) == 1
    assert backend.is_closed is True
    assert runtime.clean_shutdown is True
    assert runtime.latest_snapshot.processed_boundary == 601
    assert runtime.latest_snapshot.selected_bin == 3
    assert len(evidence.attempts) == 1
    assert evidence.attempts[0][0].result.status == "completed"
    performance = [
        row for row in evidence.performance if row.get("kind") == "analysis"
    ]
    assert len(performance) == 1
    assert performance[0]["process_sequence"] == 1
    assert performance[0]["process_control_bytes"] == 100
    assert performance[0]["process_result_bytes"] == 200


def test_child_death_after_last_result_is_fatal_at_eof_without_losing_attempt(
    request,
):
    class DiesAfterResultBackend:
        def __init__(self):
            self.is_closed = False
            self.is_started = False
            self.execute_calls = 0
            self.close_calls = 0
            self.result_returned = threading.Event()

        def start(self, timeout_s):
            assert timeout_s == 1.0
            self.is_started = True

        def execute(self, job):
            self.execute_calls += 1
            execution = ProcessExecution(
                executed=_successful_execution(job),
                sequence=1,
                parent_copy_elapsed_s=0.01,
                parent_send_elapsed_s=0.01,
                parent_wait_receive_elapsed_s=0.01,
                child_materialization_elapsed_s=0.01,
                child_callback_elapsed_s=0.01,
                round_trip_elapsed_s=0.05,
                control_bytes=100,
                result_bytes=200,
            )
            self.result_returned.set()
            return execution

        def close(
            self,
            *,
            deadline_monotonic_s,
            containment_deadline_monotonic_s,
            force,
        ):
            assert isinstance(deadline_monotonic_s, float)
            assert containment_deadline_monotonic_s >= deadline_monotonic_s
            assert force is False
            self.close_calls += 1
            self.is_closed = True
            raise ProcessAnalysisError(
                "analysis child exited before graceful shutdown acknowledgment"
            )

        def observation(self):
            return {
                "pid": 4321,
                "alive": False,
                "exitcode": 7,
                "current_rss_bytes": None,
                "peak_rss_bytes": 234_567,
            }

    source = _BarrierSource(601, block_index=600)
    evidence = _RecordingEvidence()
    backend = DiesAfterResultBackend()
    runtime = ReplayRuntime(
        source=source,
        controller=BaselineController({}, generation=1, speed=4.0),
        evidence=evidence,
        analysis_function=None,
        process_backend=backend,
        frame_limit=601,
        clock=_clock(),
        shutdown_timeout_s=1.0,
    )
    _arm_bounded_cleanup(request, runtime, source)
    runtime.start()
    assert backend.result_returned.wait(timeout=3.0)
    assert source.block_entered.wait(timeout=3.0)
    source.block_release.set()
    assert runtime._ended.wait(timeout=3.0)
    runtime.stop(timeout_s=2.0)

    assert backend.execute_calls == 1
    assert backend.close_calls == 1
    assert backend.is_closed is True
    assert len(evidence.attempts) == 1
    assert evidence.attempts[0][0].result.status == "completed"
    assert runtime.latest_snapshot.selected_bin == 3
    assert isinstance(runtime.failure, ProcessAnalysisError)
    assert "graceful shutdown acknowledgment" in str(runtime.failure)
    assert runtime.clean_shutdown is False


def test_process_startup_failure_precedes_source_open_and_thread_start():
    class StartupFailureBackend:
        is_closed = True
        is_started = False

        def start(self, timeout_s):
            assert timeout_s == pytest.approx(1.0)
            raise ProcessAnalysisError("synthetic ready/config failure")

        def close(self, **_kwargs):
            raise AssertionError("failed backend owns its startup cleanup")

        def observation(self):
            return {
                "pid": None,
                "alive": False,
                "exitcode": 1,
                "current_rss_bytes": None,
                "peak_rss_bytes": None,
            }

    source = _BarrierSource(1)
    runtime = ReplayRuntime(
        source=source,
        controller=BaselineController({}, generation=1),
        evidence=_RecordingEvidence(),
        analysis_function=None,
        process_backend=StartupFailureBackend(),
        frame_limit=1,
        clock=_clock(),
        shutdown_timeout_s=1.0,
    )

    with pytest.raises(ProcessAnalysisError, match="ready/config failure"):
        runtime.start()
    assert source.cursor == 0
    assert source.closed.is_set() is False
    assert runtime._worker is None
    assert runtime._controller is None
    assert runtime.ended is True
    assert isinstance(runtime.failure, ProcessAnalysisError)


@pytest.mark.parametrize("failure_stage", ["assembly", "callback"])
def test_worker_failure_is_timed_persisted_and_does_not_wedge(
    monkeypatch, request, failure_stage
):
    # The barrier holds the controller immediately after the first request is
    # dispatched.  Releasing it only after the failed result reaches the result
    # queue makes recovery independent of thread scheduling speed.
    source = _BarrierSource(662, block_index=600)
    evidence = _RecordingEvidence()
    original_materialize = engine_module._materialize_replay_job
    assembly_calls = 0
    callback_calls = 0
    recovered = threading.Event()
    after_start = False

    def fake_monotonic():
        return 12.0 if after_start else 10.0

    def fail_once(job):
        nonlocal assembly_calls, after_start
        assembly_calls += 1
        if failure_stage == "assembly" and assembly_calls == 1:
            after_start = True
            raise MemoryError("synthetic deferred assembly failure")
        return original_materialize(job)

    def analyze(job):
        nonlocal callback_calls, after_start
        callback_calls += 1
        if failure_stage == "callback" and callback_calls == 1:
            after_start = True
            raise RuntimeError("synthetic deferred callback failure")
        recovered.set()
        return _successful_execution(job)

    monkeypatch.setattr(engine_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(engine_module, "_materialize_replay_job", fail_once)
    runtime = _runtime(source, evidence, analyze, frame_limit=662)
    first_result_queued = threading.Event()
    original_result_put = runtime._results.put

    def signal_result_put(*args, **kwargs):
        result = original_result_put(*args, **kwargs)
        first_result_queued.set()
        return result

    monkeypatch.setattr(runtime._results, "put", signal_result_put)
    _arm_bounded_cleanup(request, runtime, source)
    runtime.start()
    assert source.block_entered.wait(timeout=3.0)
    assert first_result_queued.wait(timeout=3.0)
    source.block_release.set()
    assert recovered.wait(timeout=3.0)
    assert runtime._ended.wait(timeout=3.0)
    runtime.stop(timeout_s=2.0)

    assert assembly_calls == 2
    assert callback_calls == (1 if failure_stage == "assembly" else 2)
    assert len(evidence.attempts) == 2
    failed, failed_disposition = evidence.attempts[0]
    assert failed.result.status == "failed"
    assert f"synthetic deferred {failure_stage} failure" in failed.result.error
    assert failed.selector_elapsed_s is None
    assert failed.analysis_elapsed_s == pytest.approx(2.0)
    assert failed_disposition.disposition == "rejected"
    assert evidence.attempts[1][0].result.status == "completed"
    assert runtime.controller.active_job is None


def test_pause_ack_freezes_boundary_and_completion_waits_unpublished_until_resume(
    request,
):
    source = _BarrierSource(602, block_index=600)
    evidence = _RecordingEvidence()
    analysis_started = threading.Event()
    analysis_release = threading.Event()
    analysis_returned = threading.Event()

    def analyze(job):
        analysis_started.set()
        assert analysis_release.wait(timeout=3.0)
        result = _successful_execution(job)
        analysis_returned.set()
        return result

    runtime = _runtime(source, evidence, analyze, frame_limit=602)
    _arm_bounded_cleanup(request, runtime, source, analysis_release)
    runtime.start()
    assert analysis_started.wait(timeout=3.0)
    assert source.block_entered.wait(timeout=3.0)
    runtime.request_pause()
    source.block_release.set()
    assert evidence.pause_acknowledged.wait(timeout=3.0)
    paused_boundary = runtime.latest_snapshot.processed_boundary
    assert runtime.latest_snapshot.playback_state == "paused"

    analysis_release.set()
    assert analysis_returned.wait(timeout=3.0)
    with runtime._results.not_empty:
        assert runtime._results.not_empty.wait_for(
            # Queue.qsize() reacquires the same non-reentrant mutex held by
            # not_empty.  Inspect the protected deque only while holding it.
            lambda: len(runtime._results.queue) == 1,
            timeout=2.0,
        )
    assert evidence.attempts == []
    assert runtime.latest_snapshot.processed_boundary == paused_boundary

    runtime.resume()
    assert evidence.attempt_written.wait(timeout=3.0)
    assert runtime._ended.wait(timeout=3.0)
    runtime.stop(timeout_s=2.0)
    assert len(evidence.attempts) == 1
    assert evidence.attempts[0][1].application_boundary >= paused_boundary
    assert any(event["kind"] == "resumed" for event in evidence.playback_events)


def test_latest_snapshot_is_cached_and_coherent_during_result_application(request):
    source = _BarrierSource(601, block_index=600)
    evidence = _RecordingEvidence()
    result_ready = threading.Event()
    controller = _BarrierApplyController({}, generation=1)

    def analyze(job):
        return _successful_execution(job, ready=result_ready)

    runtime = ReplayRuntime(
        source=source,
        controller=controller,
        evidence=evidence,
        analysis_function=analyze,
        frame_limit=601,
        clock=_clock(),
        shutdown_timeout_s=1.0,
    )
    _arm_bounded_cleanup(request, runtime, source, controller.apply_release)
    runtime.start()
    assert source.block_entered.wait(timeout=3.0)
    assert result_ready.wait(timeout=3.0)
    source.block_release.set()
    assert controller.apply_mutated.wait(timeout=3.0)

    during = runtime.latest_snapshot
    assert during.processed_boundary == 601
    assert during.selected_bin is None
    assert during.selection_revision == 0
    assert during.hr.state == "missing"
    assert during.br.state == "missing"

    controller.apply_release.set()
    assert runtime._ended.wait(timeout=3.0)
    runtime.stop(timeout_s=2.0)
    after = runtime.latest_snapshot
    assert after.processed_boundary == 601
    assert after.selected_bin == 3
    assert after.selection_revision == 1
    assert after.hr.state == "accepted"
    assert after.br.state == "accepted"


def test_accepted_values_are_not_visible_before_attempt_evidence_is_durable(request):
    source = _BarrierSource(601, block_index=600)
    evidence = _DurabilityBarrierEvidence()
    result_ready = threading.Event()

    runtime = _runtime(
        source,
        evidence,
        lambda job: _successful_execution(job, ready=result_ready),
        frame_limit=601,
    )
    _arm_bounded_cleanup(request, runtime, source, evidence.write_release)
    runtime.start()
    assert source.block_entered.wait(timeout=3.0)
    assert result_ready.wait(timeout=3.0)
    source.block_release.set()
    assert evidence.write_entered.wait(timeout=3.0)

    # The controller has evaluated the result, but its accepted values cannot be
    # exposed until the corresponding NPZ/index/terminal write has succeeded.
    during = runtime.latest_snapshot
    assert during.processed_boundary == 601
    assert during.selection_revision == 0
    assert during.selected_bin is None
    assert during.hr.state == "missing"
    assert during.hr.value_bpm is None
    assert during.br.state == "missing"
    assert during.br.value_bpm is None
    assert any(event.kind == "result_applied" for event in evidence.controller_events)

    evidence.write_release.set()
    assert runtime._ended.wait(timeout=3.0)
    runtime.stop(timeout_s=2.0)
    after = runtime.latest_snapshot
    assert after.selection_revision == 1
    assert after.selected_bin == 3
    assert after.hr.state == "accepted"
    assert after.hr.value_bpm == pytest.approx(70.0)
    assert after.br.state == "accepted"
    assert after.br.value_bpm == pytest.approx(12.0)


def test_failed_attempt_evidence_write_never_exposes_unrecoverable_values(request):
    source = _BarrierSource(601, block_index=600)
    evidence = _DurabilityBarrierEvidence(fail=True)
    result_ready = threading.Event()

    runtime = _runtime(
        source,
        evidence,
        lambda job: _successful_execution(job, ready=result_ready),
        frame_limit=601,
    )
    _arm_bounded_cleanup(request, runtime, source, evidence.write_release)
    runtime.start()
    assert source.block_entered.wait(timeout=3.0)
    assert result_ready.wait(timeout=3.0)
    source.block_release.set()
    assert evidence.write_entered.wait(timeout=3.0)
    assert runtime.latest_snapshot.hr.value_bpm is None
    assert runtime.latest_snapshot.br.value_bpm is None

    evidence.write_release.set()
    assert runtime._ended.wait(timeout=3.0)
    runtime.stop(timeout_s=2.0)

    assert isinstance(runtime.failure, OSError)
    assert evidence.attempts == []
    after = runtime.latest_snapshot
    assert after.hr.state in {"missing", "held"}
    assert after.hr.value_bpm is None
    assert after.br.state in {"missing", "held"}
    assert after.br.value_bpm is None


def test_source_failure_with_active_analysis_still_persists_executed_attempt(request):
    source = _BarrierSource(1000, failure_index=600)
    evidence = _RecordingEvidence()
    analysis_started = threading.Event()
    analysis_release = threading.Event()

    def analyze(job):
        analysis_started.set()
        assert analysis_release.wait(timeout=3.0)
        return _successful_execution(job)

    runtime = _runtime(source, evidence, analyze)
    _arm_bounded_cleanup(request, runtime, source, analysis_release)
    runtime.start()
    assert analysis_started.wait(timeout=3.0)
    analysis_release.set()
    assert runtime._ended.wait(timeout=3.0)
    runtime.stop(timeout_s=2.0)

    assert isinstance(runtime.failure, ReplaySourceError)
    assert len(evidence.attempts) == 1
    assert evidence.attempts[0][1].disposition == "expired"
    assert evidence.attempts[0][1].reason == "playback_denominator_frozen"
    assert source.closed.is_set()


def test_invalid_source_boundary_precedes_ready_result_application():
    analysis_ready = threading.Event()
    source = _BarrierSource(
        601,
        malformed_index=600,
        before_return=analysis_ready,
    )
    evidence = _RecordingEvidence()

    def analyze(job):
        return _successful_execution(job, ready=analysis_ready)

    runtime = _runtime(source, evidence, analyze, frame_limit=601)
    runtime.start()
    assert runtime._ended.wait(timeout=3.0)
    runtime.stop(timeout_s=2.0)

    assert len(evidence.attempts) == 1
    _, disposition = evidence.attempts[0]
    assert disposition.disposition == "expired"
    assert disposition.reason == "stale_epoch"
    assert disposition.hr_accepted is False
    assert disposition.br_accepted is False
    kinds = [event.kind for event in evidence.controller_events]
    assert kinds.index("data_integrity_reset") < kinds.index("result_applied")


@pytest.mark.parametrize(
    "mutate_result",
    [
        lambda result: replace(result, job_id="wrong-job"),
        lambda result: replace(result, epoch=result.epoch + 1),
        lambda result: replace(
            result, selection_revision=result.selection_revision + 1
        ),
        lambda result: replace(result, frame_start=result.frame_start + 1),
        lambda result: replace(result, stage="unexpected-stage"),
    ],
    ids=["job-id", "epoch", "revision", "bounds", "stage"],
)
def test_malformed_executed_result_preserves_actual_evidence_without_publication(
    mutate_result, request,
):
    source = _BarrierSource(601, block_index=600)
    evidence = _RecordingEvidence()
    analysis_returned = threading.Event()

    def analyze(job):
        executed = _successful_execution(job)
        malformed = ExecutedAnalysis(
            result=mutate_result(executed.result),
            evidence={"phase_raw": np.asarray([1.25, 2.5])},
            analysis_elapsed_s=executed.analysis_elapsed_s,
            selector_elapsed_s=executed.selector_elapsed_s,
        )
        analysis_returned.set()
        return malformed

    runtime = _runtime(source, evidence, analyze, frame_limit=601)
    _arm_bounded_cleanup(request, runtime, source)
    runtime.start()
    assert analysis_returned.wait(timeout=3.0)
    assert source.block_entered.wait(timeout=3.0)
    source.block_release.set()
    assert runtime._ended.wait(timeout=3.0)
    runtime.stop(timeout_s=2.0)

    assert runtime.latest_snapshot.hr.value_bpm is None
    assert runtime.latest_snapshot.br.value_bpm is None
    assert len(evidence.attempts) == 1
    persisted, disposition = evidence.attempts[0]
    assert persisted.evidence["phase_raw"].tolist() == [1.25, 2.5]
    assert persisted.result.status == "failed"
    assert persisted.result.error.startswith("analysis_result_identity_mismatch:")
    assert persisted.evidence["result_identity_mismatch"].item() is True
    assert persisted.evidence["result_identity_mismatch_fields"].size >= 1
    assert "returned_job_id" in persisted.evidence
    assert "returned_epoch" in persisted.evidence
    assert "returned_selection_revision" in persisted.evidence
    assert "returned_stage" in persisted.evidence
    assert "returned_frame_start" in persisted.evidence
    assert "returned_frame_stop" in persisted.evidence
    assert "returned_actual_bin" in persisted.evidence
    assert disposition.hr_accepted is False
    assert disposition.br_accepted is False
    assert disposition.disposition in {"expired", "failed", "rejected"}
    assert runtime.controller.active_job is None
    assert runtime.clean_shutdown is True


def test_analysis_shutdown_timeout_is_evidenced_and_blocks_clean_restart(request):
    source = _BarrierSource(1000, block_index=600)
    evidence = _RecordingEvidence()
    analysis_started = threading.Event()
    never_release = threading.Event()

    def analyze(_job):
        analysis_started.set()
        assert never_release.wait(timeout=3.0)
        raise AssertionError("unreachable")

    runtime = _runtime(
        source, evidence, analyze, shutdown_timeout=0.05
    )
    _arm_bounded_cleanup(request, runtime, source, never_release)
    runtime.start()
    assert analysis_started.wait(timeout=3.0)
    assert source.block_entered.wait(timeout=3.0)
    source.block_release.set()
    runtime.stop(timeout_s=1.0)

    assert runtime.clean_shutdown is False
    assert runtime.failure is not None
    assert len(evidence.attempts) == 1
    executed, disposition = evidence.attempts[0]
    assert executed.result.status == "interrupted"
    assert executed.result.error == "shutdown_timeout"
    assert disposition.hr_accepted is False
    assert disposition.br_accepted is False
    never_release.set()
    assert runtime._worker is not None
    _join_thread(runtime._worker, "timed-out worker cleanup")
