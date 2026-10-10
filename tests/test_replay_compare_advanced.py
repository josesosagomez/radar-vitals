"""Calibration-gated advanced replay and synthetic decoder integration tests."""

from __future__ import annotations

import csv
import copy
import hashlib
import inspect
import json
import queue
import threading
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

import src.replay_compare.advanced as advanced_module
import src.replay_compare.advanced_application as advanced_application_module
import src.replay_compare.verification as verification_module
from src.reference_access import LoadedReference, ReferenceSource
from src.live_motion.config import (
    FeatureThreshold,
    LiveMotionSettings,
    MovementThresholds,
    PresenceThresholds,
    validate_live_motion_preflight,
)
from src.live_motion.runtime import ExecutedAnalysis, _exception_evidence
from src.live_motion.scheduler import AnalysisJob, AnalysisResult
from src.live_motion.evidence import AttemptEvidenceWriter, verify_development_evidence
from src.m2.common import canonical_json_bytes
from src.replay_compare.advanced import (
    AdvancedReplayAuthorization,
    AdvancedReplayOrchestrator,
    AdvancedReplayUnavailable,
    authorize_advanced_replay,
)
from src.replay_compare.advanced_application import (
    create_advanced_replay_application,
)
from src.replay_compare.clock import PlaybackClock
from src.replay_compare.reference import PassiveReferenceTimeline
from src.replay_compare.session import (
    LEGACY_VALIDITY_ID,
    SOURCE_KIND,
    SUPPORTED_CAPTURE_ID,
    ReplaySessionSpec,
)
from src.replay_compare.source import ProductionFrameDecoder, SequentialAdcSource
from src.replay_compare.verification import verify_replay_with_source
from src.window_pipeline import run_config_hash


def _session(tmp_path: Path) -> ReplaySessionSpec:
    return ReplaySessionSpec(
        capture_id=SUPPORTED_CAPTURE_ID,
        capture_key="m3",
        capture_dir=tmp_path / "capture",
        adc_path=tmp_path / "capture" / "adc_stream.bin",
        metadata_path=tmp_path / "capture" / "run_metadata.json",
        warmup_path=tmp_path / "capture" / "warmup_bin_selection.json",
        registered_adc_sha256="a" * 64,
        metadata_sha256="b" * 64,
        warmup_sha256="c" * 64,
        original_config_sha256="d" * 64,
        effective_config_sha256="e" * 64,
        _original_config_json=b"{}",
        _effective_config_json=b"{}",
        frame_count=1000,
        frame_rate_hz=20.0,
        frame_bytes=131_072,
        num_adc_samples=256,
        num_rx=4,
        num_chirps_per_frame=32,
        iq_swap=True,
        range_resolution_m=0.0436,
        range_bias_m=0.0,
        anchor_utc_us=1_000_000,
        time_origin_id="start_wall_utc_fallback_v1",
        time_origin_approximate=True,
        validity_provenance=LEGACY_VALIDITY_ID,
        validity_assumed=True,
        source_kind=SOURCE_KIND,
        _metadata_json=b"{}",
    )


def _empty_reference() -> PassiveReferenceTimeline:
    return PassiveReferenceTimeline(
        LoadedReference(
            frame=pd.DataFrame(
                columns=["epoch_utc", "pr_bpm", "pi", "rr_bpm"]
            ),
            source=ReferenceSource(
                source_kind="development_registry",
                capture_id="synthetic",
                subject_id="A",
                resolved_path="synthetic.csv",
                sha256="9" * 64,
            ),
        ),
        anchor_utc_us=1_000_000,
        frame_rate_hz=20.0,
    )


def _gate_config(session: ReplaySessionSpec) -> dict:
    return {
        "session": {"frame_rate_hz": session.frame_rate_hz, "locked_bin": None},
        "profile": {
            "num_adc_samples": session.num_adc_samples,
            "num_rx": session.num_rx,
            "num_chirps_per_frame": session.num_chirps_per_frame,
            "iq_swap": session.iq_swap,
            "range_resolution_m": session.range_resolution_m,
            "range_bias_m": 0.0,
        },
        "phase": {"method": "delta_before_mean", "clutter_removal": "none"},
        "protocol": {"subject_distance_m": [0.8, 1.4]},
        "bin_selection": {"candidate_bins": [20, 21]},
        "capture": {"record_raw_stream": True},
        "development_motion": {
            "enabled": True,
            "extended_breathing_enabled": False,
            "preliminary_stages_s": [10, 20],
            "ordinary_window_s": 30,
            "ordinary_hop_s": 3,
            "monitor_window_s": 1.0,
            "monitor_hop_s": 0.25,
            "stillness_confirmation_s": 3,
            "extended_breathing_window_s": 60,
            "extended_breathing_band_hz": [0.05, 0.50],
            "calibration": {
                "record_path": "missing_physical_calibration.json",
                "record_sha256": "f" * 64,
            },
        },
    }


def _synthetic_config() -> dict:
    return {
        "seed": 42,
        "session": {"frame_rate_hz": 20.0, "locked_bin": None},
        "profile": {
            "num_adc_samples": 8,
            "num_rx": 1,
            "num_chirps_per_frame": 1,
            "range_resolution_m": 0.1,
            "range_bias_m": 0.0,
            "iq_swap": True,
        },
        "protocol": {"subject_distance_m": [0.0, 0.7]},
        "bin_selection": {
            "candidate_bins": [1, 2],
            "energy_eligibility_min_settled_db": -12.0,
        },
        "capture": {"record_raw_stream": True},
        "phase": {"method": "delta_before_mean", "clutter_removal": "none"},
        "respiration": {},
        "heart": {},
        "development_motion": {
            "enabled": True,
            "extended_breathing_enabled": False,
            "preliminary_stages_s": [10, 20],
            "ordinary_window_s": 30,
            "ordinary_hop_s": 3,
            "monitor_window_s": 1.0,
            "monitor_hop_s": 0.25,
            "stillness_confirmation_s": 3,
            "extended_breathing_window_s": 60,
            "extended_breathing_band_hz": [0.05, 0.50],
            # The live preflight parses the motion schema before it can report
            # replay incompatibility. Values may remain absent while that check
            # allows missing calibration, but the mapping itself is mandatory.
            "calibration": {"record_path": None, "record_sha256": None},
        },
    }


def _settings() -> LiveMotionSettings:
    return LiveMotionSettings(
        enabled=True,
        unavailable_reason="",
        frame_rate_hz=20.0,
        preview_frames=(200, 400),
        ordinary_window_frames=600,
        ordinary_hop_frames=60,
        monitor_window_frames=20,
        monitor_hop_frames=5,
        stillness_frames=60,
        extended_breathing_enabled=False,
        extended_breathing_window_frames=1200,
        extended_breathing_band_hz=(0.05, 0.50),
        candidate_bins=(1, 2),
        presence_thresholds=PresenceThresholds(-10.0, 0.0),
        movement_thresholds=MovementThresholds(
            presence=FeatureThreshold(False, None, None),
            phase_activity=FeatureThreshold(True, 0.5, 0.1),
            range_profile_change=FeatureThreshold(True, 0.5, 0.1),
        ),
        calibration_record={"accepted": True, "synthetic": True},
        calibration_record_path=None,
        calibration_record_sha256="f" * 64,
    )


def test_massimo3_gate_requires_explicit_legacy_validity_assumption(tmp_path):
    session = _session(tmp_path)
    with pytest.raises(AdvancedReplayUnavailable, match="allow-legacy-validity"):
        authorize_advanced_replay(
            session,
            _gate_config(session),
            allow_legacy_validity_assumption=False,
        )


@pytest.mark.parametrize(
    "session_mutation",
    [
        {"capture_id": "different-capture"},
        {"source_kind": "synthetic"},
        {"validity_assumed": False},
        {"validity_provenance": "invented"},
    ],
)
def test_advanced_gate_rejects_wrong_capture_source_or_validity(
    tmp_path, session_mutation
):
    session = replace(_session(tmp_path), **session_mutation)
    with pytest.raises(AdvancedReplayUnavailable):
        authorize_advanced_replay(
            session,
            _gate_config(session),
            allow_legacy_validity_assumption=True,
        )


def test_tracked_live_configuration_reports_nonzero_bias_and_pending_calibration(
    tmp_path,
):
    session = _session(tmp_path)
    config = yaml.safe_load(
        (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "live_demo_calibrated_config.yaml"
        ).read_text(encoding="utf-8")
    )
    with pytest.raises(AdvancedReplayUnavailable) as caught:
        authorize_advanced_replay(
            session, config, allow_legacy_validity_assumption=True
        )
    message = str(caught.value)
    assert "physical calibration" in message
    assert "range_bias_m must be historical zero" in message


def test_wrong_calibration_hash_or_missing_record_fails_closed(tmp_path):
    session = _session(tmp_path)
    config = _gate_config(session)
    config["development_motion"]["calibration"]["record_sha256"] = "not-a-digest"
    with pytest.raises(AdvancedReplayUnavailable, match="physical calibration"):
        authorize_advanced_replay(
            session, config, allow_legacy_validity_assumption=True
        )

    config = _gate_config(session)
    with pytest.raises(
        AdvancedReplayUnavailable, match="physical calibration validation failed"
    ):
        authorize_advanced_replay(
            session, config, allow_legacy_validity_assumption=True
        )


def test_advanced_gate_rejects_recorded_rf_timing_mismatch(tmp_path):
    session = _session(tmp_path)
    recorded = {
        "hw_profile": {
            "start_freq_ghz": 77.0,
            "idle_time_us": 100.0,
            "adc_start_time_us": 6.0,
            "ramp_end_time_us": 60.0,
            "freq_slope_mhz_us": 70.0,
            "sample_rate_ksps": 5000,
        },
        "hw_frame": {
            "chirp_start_idx": 0,
            "chirp_end_idx": 0,
            "loops": 32,
            "period_ms": 50.0,
        },
        "capture": {"raw_stream_format": "dca1000_iq_int16"},
    }
    recorded_bytes = json.dumps(
        recorded, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    session = replace(
        session,
        _original_config_json=recorded_bytes,
        _effective_config_json=recorded_bytes,
        original_config_sha256=hashlib.sha256(recorded_bytes).hexdigest(),
        effective_config_sha256=hashlib.sha256(recorded_bytes).hexdigest(),
    )
    config = _gate_config(session)
    config.update(copy.deepcopy(recorded))
    config["hw_profile"]["start_freq_ghz"] = 76.0

    with pytest.raises(
        AdvancedReplayUnavailable,
        match="motion hw_profile is incompatible with the recorded capture",
    ):
        authorize_advanced_replay(
            session, config, allow_legacy_validity_assumption=True
        )


def test_advanced_authorization_preserves_exact_calibration_file_bytes(
    tmp_path, monkeypatch
):
    session = _session(tmp_path / "development" / "capture")
    config = _gate_config(session)
    record = {"candidate": {"capabilities": ["movement"]}}
    exact_bytes = canonical_json_bytes(record)
    calibration_path = tmp_path / "physical_calibration.json"
    calibration_path.write_bytes(exact_bytes)
    config["development_motion"]["calibration"] = {
        "record_path": calibration_path.name,
        "record_sha256": hashlib.sha256(exact_bytes).hexdigest(),
    }
    monkeypatch.setattr(advanced_module, "ACTIVE_CHECKOUT_ROOT", tmp_path)
    monkeypatch.setattr(
        advanced_module, "validate_calibration", lambda *_args, **_kwargs: record
    )
    monkeypatch.setattr(
        advanced_module, "parse_live_motion_settings", lambda *_args, **_kwargs: _settings()
    )
    monkeypatch.setattr(
        advanced_module, "resolve_replay_session", lambda *_args, **_kwargs: session
    )

    authorization = authorize_advanced_replay(
        session, config, allow_legacy_validity_assumption=True
    )

    assert authorization.calibration_record_bytes == exact_bytes
    assert hashlib.sha256(authorization.calibration_record_bytes).hexdigest() == (
        authorization.calibration_sha256
    )

@pytest.mark.parametrize(
    ("extended", "capabilities", "message"),
    [
        (False, [], "movement recovery capability"),
        (True, ["movement"], "extended_breathing capability"),
    ],
)
def test_accepted_record_still_requires_declared_capabilities(
    tmp_path, monkeypatch, extended, capabilities, message
):
    session = _session(tmp_path)
    config = _gate_config(session)
    config["development_motion"]["extended_breathing_enabled"] = extended
    monkeypatch.setattr(
        advanced_module,
        "validate_calibration",
        lambda cfg, *, root: {"candidate": {"capabilities": capabilities}},
    )
    with pytest.raises(AdvancedReplayUnavailable, match=message):
        authorize_advanced_replay(
            session, config, allow_legacy_validity_assumption=True
        )


def test_public_advanced_authorizer_has_no_root_or_synthetic_override():
    assert tuple(inspect.signature(authorize_advanced_replay).parameters) == (
        "session",
        "cfg",
        "allow_legacy_validity_assumption",
    )


def test_existing_live_motion_preflight_still_forbids_replay():
    with pytest.raises(ValueError, match="replay is incompatible"):
        validate_live_motion_preflight(
            _synthetic_config(),
            mode="replay",
            replay_requested=True,
            prospective=False,
            cli_locked_bin=None,
            manifest_locked_bin=None,
        )


class _MotionEvidence:
    def __init__(self):
        self.events = []
        self.attempts = []

    def write_event(self, event):
        self.events.append(event)

    def write_attempt(self, *args):
        self.attempts.append(args)


class _ReplaySink:
    def __init__(self):
        self.controller = []
        self.playback = []
        self.performance = []

    def write_controller_event(self, event):
        self.controller.append(event)

    def write_playback_event(self, event):
        self.playback.append(dict(event))

    def write_performance(self, event):
        self.performance.append(dict(event))


def test_synthetic_advanced_path_uses_bounded_production_decoder_without_authorizing_capture(
    tmp_path,
):
    payload = np.arange(16, dtype="<i2").tobytes()
    adc_path = tmp_path / "one_frame.bin"
    adc_path.write_bytes(payload)
    decoder = ProductionFrameDecoder(
        {
            "num_adc_samples": 8,
            "num_rx": 1,
            "num_chirps_per_frame": 1,
            "iq_swap": True,
        }
    )
    source = SequentialAdcSource(
        adc_path,
        decoder,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        expected_frame_count=1,
    )
    evidence = _MotionEvidence()
    sink = _ReplaySink()
    clock = PlaybackClock(20.0, speed=4.0, monotonic=lambda: 0.0, wait=lambda _s: None)
    runtime = AdvancedReplayOrchestrator(
        source=source,
        cfg=_synthetic_config(),
        settings=_settings(),
        clock=clock,
        frame_limit=1,
        evidence_writer=evidence,
        event_sink=sink,
        generation=3,
    )
    runtime.start()
    assert runtime._ended.wait(timeout=3.0)
    runtime.stop(timeout_s=2.0)

    assert runtime.clean_shutdown is True
    assert runtime.latest_snapshot.generation == 3
    assert runtime.latest_snapshot.processed_boundary == 1
    assert runtime.latest_snapshot.playback_state == "complete"
    assert source.frames_read == 1
    assert evidence.attempts == []
    assert [event["kind"] for event in sink.playback] == [
        "source_prepared",
        "denominator_frozen",
    ]


def _unit_orchestrator(analysis_function=None) -> AdvancedReplayOrchestrator:
    return AdvancedReplayOrchestrator(
        source=object(),
        cfg=_synthetic_config(),
        settings=_settings(),
        clock=PlaybackClock(
            20.0, speed=1.0, monotonic=lambda: 0.0, wait=lambda _s: None
        ),
        frame_limit=1,
        evidence_writer=_MotionEvidence(),
        event_sink=_ReplaySink(),
        analysis_function=analysis_function,
    )


def _unit_job() -> AnalysisJob:
    return AnalysisJob(
        job_id="advanced-job",
        epoch=2,
        selection_revision=3,
        stage="rolling",
        frame_start=0,
        frame_stop=600,
        requested_bin=1,
        selection_mode="committed",
        raw_frames=np.zeros((600, 1, 1, 8), dtype=np.complex64),
    )


def test_advanced_malformed_worker_identity_preserves_returned_arrays_explicitly():
    runtime = _unit_orchestrator()
    requested = _unit_job()
    returned = AnalysisResult(
        job_id="wrong-job",
        epoch=requested.epoch,
        selection_revision=requested.selection_revision,
        stage=requested.stage,
        frame_start=requested.frame_start,
        frame_stop=requested.frame_stop,
        actual_bin=1,
        executed=True,
        status="completed",
        dsp={"hr_valid": False, "br_valid": False},
    )
    phase = np.asarray([1.0, 2.0, 3.0])
    executed = ExecutedAnalysis(returned, {"phase_raw": phase}, 0.01, None)

    normalized = runtime._validate_worker_return(requested, executed)

    assert normalized.result.job_id == requested.job_id
    assert normalized.result.status == "failed"
    assert normalized.result.error == "analysis_result_identity_mismatch:job_id"
    assert normalized.evidence["returned_job_id"].item() == "wrong-job"
    assert normalized.evidence["result_identity_mismatch_fields"].tolist() == [
        "job_id"
    ]
    np.testing.assert_array_equal(
        normalized.evidence["returned_evidence__phase_raw"], phase
    )


@pytest.mark.parametrize(
    ("hr_result", "hr_raw", "reason"),
    [
        (None, 70.0, "missing_hr_result"),
        ({"ahet_verified": False, "accepted_candidate_rank": 0}, 70.0, "ahet_not_verified"),
        ({"ahet_verified": True, "accepted_candidate_rank": None}, 70.0, "invalid_accepted_candidate_rank"),
        ({"ahet_verified": True, "accepted_candidate_rank": 0}, 0.0, "hr_not_finite_positive"),
    ],
)
def test_advanced_worker_cannot_claim_native_hr_without_exact_admission_proof(
    hr_result, hr_raw, reason
):
    runtime = _unit_orchestrator()
    requested = _unit_job()
    returned = AnalysisResult(
        job_id=requested.job_id,
        epoch=requested.epoch,
        selection_revision=requested.selection_revision,
        stage=requested.stage,
        frame_start=requested.frame_start,
        frame_stop=requested.frame_stop,
        actual_bin=requested.requested_bin,
        executed=True,
        status="completed",
        dsp={
            "hr_valid": True,
            "hr_raw": hr_raw,
            "hr_result": hr_result,
            "br_valid": True,
            "br_bpm": 0.0,
        },
    )
    executed = ExecutedAnalysis(
        returned,
        {"heart_spectrum": np.asarray([4.0, 5.0])},
        0.01,
        None,
    )

    normalized = runtime._validate_worker_return(requested, executed)

    assert normalized.result.status == "failed"
    assert reason in normalized.result.error
    assert normalized.evidence["native_hr_admission_failure"].item().endswith(reason)
    np.testing.assert_array_equal(
        normalized.evidence["returned_evidence__heart_spectrum"],
        np.asarray([4.0, 5.0]),
    )


class _ObservedQueue(queue.Queue):
    def __init__(self):
        super().__init__(maxsize=1)
        self.got_item = threading.Event()

    def get(self, *args, **kwargs):
        item = super().get(*args, **kwargs)
        self.got_item.set()
        return item


def test_pause_state_is_atomic_with_unopened_worker_start():
    analysis_entered = threading.Event()

    def analyze(job):
        analysis_entered.set()
        return ExecutedAnalysis(
            AnalysisResult(
                job_id=job.job_id,
                epoch=job.epoch,
                selection_revision=job.selection_revision,
                stage=job.stage,
                frame_start=job.frame_start,
                frame_stop=job.frame_stop,
                actual_bin=job.requested_bin,
                executed=True,
                status="failed",
                error="synthetic test completion",
            ),
            {},
            0.0,
            None,
        )

    runtime = _unit_orchestrator(analysis_function=analyze)
    observed_work = _ObservedQueue()
    runtime._work = observed_work
    worker = threading.Thread(target=runtime._worker_loop, daemon=True)

    # Hold the same condition used by pause acknowledgment. The worker may take
    # the queued request, but cannot classify it as started before this thread
    # records the paused state under that lock.
    with runtime._execution_condition:
        worker.start()
        observed_work.put_nowait(_unit_job())
        assert observed_work.got_item.wait(timeout=2.0)
        runtime._execution_paused = True

    with runtime._execution_condition:
        assert analysis_entered.is_set() is False
        assert runtime._worker_started == set()
        runtime._execution_paused = False
        runtime._execution_condition.notify_all()

    assert analysis_entered.wait(timeout=2.0)
    observed_work.put(None, timeout=2.0)
    worker.join(timeout=2.0)
    assert worker.is_alive() is False


class _AdvancedArraySource:
    initial_sha256 = "b" * 64
    opened_file_identity = {"size": 261 * 32, "device": 1, "inode": 2}

    def __init__(self, frame_count=261, frames=None):
        if frames is None:
            carrier = np.exp(2j * np.pi * np.arange(8) / 8.0).astype(np.complex64)
            frames = np.broadcast_to(
                carrier[None, None, None, :], (frame_count, 1, 1, 8)
            ).copy()
        self.frames = np.asarray(frames, dtype=np.complex64)
        self.frame_count = frame_count
        self.cursor = 0
        self.verified = False

    @property
    def frames_read(self):
        return self.cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def read_next(self):
        if self.cursor >= self.frame_count:
            return None
        index = self.cursor
        self.cursor += 1
        return index, np.array(self.frames[index], copy=True)

    def verify_integrity(self):
        self.verified = True


def _advanced_phase_frames(count=261):
    time_s = np.arange(count, dtype=np.float64) / 20.0
    phase = (
        3.0 * np.sin(2 * np.pi * 0.25 * time_s)
        + 1.5 * np.sin(2 * np.pi * 0.50 * time_s)
        + 0.3 * np.sin(2 * np.pi * (71.0 / 60.0) * time_s)
    )
    carrier = np.exp(2j * np.pi * np.arange(8, dtype=np.float64) / 8.0)
    return (
        np.exp(1j * phase)[:, None, None, None]
        * carrier[None, None, None, :]
    ).astype(np.complex64)


class _FreezeAwareSink(_ReplaySink):
    def __init__(self):
        super().__init__()
        self.denominator_frozen = threading.Event()

    def write_playback_event(self, event):
        super().write_playback_event(event)
        if event.get("kind") == "denominator_frozen":
            self.denominator_frozen.set()


def test_advanced_real_writer_has_request_xor_attempt_or_cancellation_lineage(tmp_path):
    config = _synthetic_config()
    source = _AdvancedArraySource()
    sink = _ReplaySink()
    writer = AttemptEvidenceWriter(
        tmp_path / "motion-pass", "a" * 64, source.initial_sha256, "c" * 64
    )

    def fail_honestly(job):
        reason = "synthetic deterministic analysis failure"
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
        return ExecutedAnalysis(
            result,
            _exception_evidence(job, config, reason, job.requested_bin),
            0.0,
            None,
        )

    runtime = AdvancedReplayOrchestrator(
        source=source,
        cfg=config,
        settings=_settings(),
        clock=PlaybackClock(
            20.0, speed=4.0, monotonic=lambda: 0.0, wait=lambda _s: None
        ),
        frame_limit=261,
        evidence_writer=writer,
        event_sink=sink,
        analysis_function=fail_honestly,
        shutdown_timeout_s=2.0,
    )
    runtime.start()
    assert runtime._ended.wait(timeout=5.0)
    runtime.stop(timeout_s=3.0)

    assert runtime.failure is None
    assert runtime.clean_shutdown is True
    assert source.verified is True
    requests = [
        event for event in sink.controller
        if isinstance(event, dict) and event.get("kind") == "analysis_requested"
    ]
    applications = [
        event for event in sink.controller
        if isinstance(event, dict) and event.get("kind") == "result_applied"
    ]
    cancellations = [
        event for event in sink.controller
        if isinstance(event, dict)
        and event.get("kind") == "cancelled_before_execution"
    ]
    assert requests
    for requested in requests:
        job_id = requested["job_id"]
        terminal_count = sum(event.get("job_id") == job_id for event in applications)
        terminal_count += sum(event.get("job_id") == job_id for event in cancellations)
        assert terminal_count == 1
    assert verify_development_evidence(writer.run_dir) == []


def test_advanced_stop_before_worker_start_is_one_cancellation_and_no_attempt(
    tmp_path, request
):
    config = _synthetic_config()
    source = _AdvancedArraySource()
    sink = _ReplaySink()
    writer = AttemptEvidenceWriter(
        tmp_path / "cancel-pass", "a" * 64, source.initial_sha256, "c" * 64
    )
    worker_release = threading.Event()
    analysis_entered = threading.Event()

    def must_not_run(_job):
        analysis_entered.set()
        raise AssertionError("cancelled unopened work reached analysis")

    runtime = AdvancedReplayOrchestrator(
        source=source,
        cfg=config,
        settings=_settings(),
        clock=PlaybackClock(
            20.0, speed=4.0, monotonic=lambda: 0.0, wait=lambda _s: None
        ),
        frame_limit=261,
        evidence_writer=writer,
        event_sink=sink,
        analysis_function=must_not_run,
        shutdown_timeout_s=1.0,
    )
    actual_worker_loop = runtime._worker_loop

    def delayed_worker_loop():
        if not worker_release.wait(timeout=3.0):
            return
        actual_worker_loop()

    runtime._worker_loop = delayed_worker_loop

    def cleanup():
        worker_release.set()
        runtime.stop(timeout_s=1.0)

    request.addfinalizer(cleanup)
    runtime.start()
    assert runtime._ended.wait(timeout=3.0)
    assert analysis_entered.is_set() is False
    requests = [
        event for event in sink.controller
        if isinstance(event, dict) and event.get("kind") == "analysis_requested"
    ]
    cancellations = [
        event for event in sink.controller
        if isinstance(event, dict)
        and event.get("kind") == "cancelled_before_execution"
    ]
    assert len(requests) == len(cancellations) == 1
    assert requests[0]["job_id"] == cancellations[0]["job_id"]
    assert list(writer.attempt_dir.glob("*.npz")) == []
    assert writer.csv_path.exists() is False

    worker_release.set()
    runtime.stop(timeout_s=2.0)
    assert runtime.clean_shutdown is True


def test_advanced_late_completion_after_denominator_freeze_is_evidenced_not_published(
    tmp_path, request
):
    config = _synthetic_config()
    settings = _settings()
    source = _AdvancedArraySource()
    sink = _FreezeAwareSink()
    writer = AttemptEvidenceWriter(
        tmp_path / "late-pass", "a" * 64, source.initial_sha256, "c" * 64
    )
    analysis_started = threading.Event()
    analysis_release = threading.Event()
    returned = []
    production = advanced_module.production_analysis_function(config, settings)

    def blocked_production(job):
        analysis_started.set()
        if not analysis_release.wait(timeout=3.0):
            raise TimeoutError("test did not release late advanced analysis")
        executed = production(job)
        returned.append(executed)
        return executed

    observed_snapshots = []
    runtime = AdvancedReplayOrchestrator(
        source=source,
        cfg=config,
        settings=settings,
        clock=PlaybackClock(
            20.0, speed=4.0, monotonic=lambda: 0.0, wait=lambda _s: None
        ),
        frame_limit=261,
        evidence_writer=writer,
        event_sink=sink,
        analysis_function=blocked_production,
        snapshot_observer=observed_snapshots.append,
        shutdown_timeout_s=2.0,
    )

    def cleanup():
        analysis_release.set()
        runtime.stop(timeout_s=1.0)

    request.addfinalizer(cleanup)
    runtime.start()
    assert analysis_started.wait(timeout=3.0)
    assert sink.denominator_frozen.wait(timeout=3.0)
    snapshot_at_freeze = runtime.latest_snapshot
    observer_at_freeze = observed_snapshots[-1]

    analysis_release.set()
    assert runtime._ended.wait(timeout=5.0)
    runtime.stop(timeout_s=3.0)

    assert runtime.failure is None
    assert runtime.clean_shutdown is True
    assert len(returned) == 1
    assert returned[0].result.status == "completed"
    assert runtime.latest_snapshot == snapshot_at_freeze
    assert observed_snapshots[-1] == observer_at_freeze
    with writer.csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["disposition"] == "expired"
    assert rows[0]["reason"] == "playback_denominator_frozen"
    applications = [
        event for event in sink.controller
        if isinstance(event, dict) and event.get("kind") == "result_applied"
    ]
    assert len(applications) == 1
    assert applications[0]["reason"] == "playback_denominator_frozen"
    assert applications[0]["hr_accepted"] is False
    assert applications[0]["br_accepted"] is False
    assert verify_development_evidence(writer.run_dir) == []


def _production_authorization_fixture(tmp_path, monkeypatch):
    config = _gate_config(_session(tmp_path))
    config["profile"].update(
        {
            "num_adc_samples": 256,
            "num_rx": 4,
            "num_chirps_per_frame": 32,
            "range_resolution_m": 0.0436,
            "range_bias_m": 0.0,
            "iq_swap": True,
        }
    )
    config["protocol"]["subject_distance_m"] = [0.8, 1.4]
    config["bin_selection"]["candidate_bins"] = [20, 21]
    config_bytes = json.dumps(
        config, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    capture_dir = tmp_path / "capture"
    capture_dir.mkdir(parents=True, exist_ok=True)
    adc_path = capture_dir / "adc_stream.bin"
    adc_path.write_bytes(bytes(131_072))
    session = replace(
        _session(tmp_path),
        capture_dir=capture_dir,
        adc_path=adc_path,
        frame_count=1,
        frame_bytes=131_072,
        num_adc_samples=256,
        num_rx=4,
        num_chirps_per_frame=32,
        range_resolution_m=0.0436,
        range_bias_m=0.0,
        registered_adc_sha256=hashlib.sha256(bytes(131_072)).hexdigest(),
        original_config_sha256=hashlib.sha256(config_bytes).hexdigest(),
        effective_config_sha256=hashlib.sha256(config_bytes).hexdigest(),
        _original_config_json=config_bytes,
        _effective_config_json=config_bytes,
    )
    calibration_bytes = b"{}\n"
    calibration_sha256 = hashlib.sha256(calibration_bytes).hexdigest()
    authorization = AdvancedReplayAuthorization(
        session=session,
        _config_json=config_bytes,
        _calibration_record_json=calibration_bytes,
        calibration_record_path=tmp_path / "calibration.json",
        calibration_sha256=calibration_sha256,
        config_sha256=run_config_hash(config),
        validity_provenance=session.validity_provenance,
        source_kind=session.source_kind,
    )
    monkeypatch.setattr(
        advanced_module,
        "authorize_advanced_replay",
        lambda *_args, **_kwargs: authorization,
    )
    monkeypatch.setattr(
        advanced_module,
        "parse_live_motion_settings",
        lambda *_args, **_kwargs: replace(_settings(), candidate_bins=(20, 21)),
    )
    decoder = ProductionFrameDecoder(config["profile"])
    source = SequentialAdcSource(
        adc_path,
        decoder,
        expected_sha256=session.registered_adc_sha256,
        expected_frame_count=1,
    )
    writer = AttemptEvidenceWriter(
        tmp_path / "motion-pass",
        authorization.config_sha256,
        session.registered_adc_sha256,
        authorization.calibration_sha256,
    )
    kwargs = {
        "source": source,
        "clock": PlaybackClock(
            20.0, speed=1.0, monotonic=lambda: 0.0, wait=lambda _s: None
        ),
        "frame_limit": 1,
        "evidence_writer": writer,
        "event_sink": _ReplaySink(),
    }
    return authorization, kwargs


def _resign_motion_artifact(pass_dir: Path, name: str) -> None:
    path = pass_dir / name
    manifest_path = pass_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _completed_advanced_application(tmp_path, monkeypatch, label):
    authorization, _kwargs = _production_authorization_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        advanced_application_module,
        "authorize_advanced_replay",
        lambda *_args, **_kwargs: authorization,
    )
    manager = create_advanced_replay_application(
        authorization,
        output_root=tmp_path / label,
        reference_timeline=_empty_reference(),
        frame_limit=1,
        initial_speed=4.0,
        shutdown_timeout_s=1.0,
    )
    manager.start()
    assert manager.current_run._ended.wait(timeout=3.0)
    monitor = manager._run.monitor
    assert monitor is not None
    monitor.join(timeout=5.0)
    assert monitor.is_alive() is False
    assert manager.clean_shutdown is True
    assert manager.failure is None
    return manager, authorization


def _assert_advanced_pass_is_accepted(manager, authorization) -> None:
    report = verify_replay_with_source(
        manager.pass_dir,
        authorization.session,
        reference_timeline=manager.reference_timeline,
    )
    assert report.internal_issues == ()
    assert report.source_issues == ()
    assert report.source_binding_complete is True
    assert report.accepted is True


def test_from_authorization_accepts_only_exact_fresh_production_dependencies(
    tmp_path, monkeypatch
):
    authorization, kwargs = _production_authorization_fixture(tmp_path, monkeypatch)

    runtime = AdvancedReplayOrchestrator.from_authorization(
        authorization, **kwargs
    )

    assert type(runtime.source) is SequentialAdcSource
    assert type(runtime.evidence_writer) is AttemptEvidenceWriter
    assert runtime.frame_validity(0) is True


@pytest.mark.parametrize("override", ["analysis_function", "frame_validity"])
def test_from_authorization_rejects_scientific_overrides(
    tmp_path, monkeypatch, override
):
    authorization, kwargs = _production_authorization_fixture(tmp_path, monkeypatch)
    kwargs[override] = lambda *_args: True

    with pytest.raises(AdvancedReplayUnavailable, match="cannot override"):
        AdvancedReplayOrchestrator.from_authorization(authorization, **kwargs)


@pytest.mark.parametrize(
    ("attribute", "value", "message"),
    [
        ("expected_sha256", "0" * 64, "SHA-256"),
        ("expected_frame_count", 2, "frame count"),
    ],
)
def test_from_authorization_rejects_wrong_source_binding(
    tmp_path, monkeypatch, attribute, value, message
):
    authorization, kwargs = _production_authorization_fixture(tmp_path, monkeypatch)
    setattr(kwargs["source"], attribute, value)

    with pytest.raises(AdvancedReplayUnavailable, match=message):
        AdvancedReplayOrchestrator.from_authorization(authorization, **kwargs)


def test_from_authorization_rejects_opened_or_instance_overridden_source(
    tmp_path, monkeypatch
):
    authorization, kwargs = _production_authorization_fixture(tmp_path, monkeypatch)
    source = kwargs["source"]
    source.__enter__()
    try:
        with pytest.raises(AdvancedReplayUnavailable, match="fresh unopened"):
            AdvancedReplayOrchestrator.from_authorization(authorization, **kwargs)
    finally:
        source.__exit__(None, None, None)

    authorization, kwargs = _production_authorization_fixture(
        tmp_path / "override", monkeypatch
    )
    kwargs["source"].read_next = lambda: None
    with pytest.raises(AdvancedReplayUnavailable, match="methods cannot be overridden"):
        AdvancedReplayOrchestrator.from_authorization(authorization, **kwargs)


def test_from_authorization_rejects_wrong_path_or_decoder_identity(
    tmp_path, monkeypatch
):
    authorization, kwargs = _production_authorization_fixture(tmp_path, monkeypatch)
    alias = tmp_path / "other-adc.bin"
    alias.write_bytes(bytes(131_072))
    kwargs["source"].path = alias
    with pytest.raises(AdvancedReplayUnavailable, match="path is not authorized"):
        AdvancedReplayOrchestrator.from_authorization(authorization, **kwargs)

    authorization, kwargs = _production_authorization_fixture(
        tmp_path / "decoder", monkeypatch
    )
    kwargs["source"].decoder.decode = lambda _payload: np.empty(0)
    with pytest.raises(AdvancedReplayUnavailable, match="decoder method"):
        AdvancedReplayOrchestrator.from_authorization(authorization, **kwargs)


@pytest.mark.parametrize(
    ("attribute", "value", "message"),
    [
        ("config_hash", "0" * 64, "config_hash"),
        ("source_hash", "0" * 64, "source_hash"),
        ("calibration_hash", "0" * 64, "calibration_hash"),
        ("_next_index", 1, "fresh pass"),
    ],
)
def test_from_authorization_rejects_wrong_or_nonfresh_evidence_writer(
    tmp_path, monkeypatch, attribute, value, message
):
    authorization, kwargs = _production_authorization_fixture(tmp_path, monkeypatch)
    setattr(kwargs["evidence_writer"], attribute, value)

    with pytest.raises(AdvancedReplayUnavailable, match=message):
        AdvancedReplayOrchestrator.from_authorization(authorization, **kwargs)


def test_from_authorization_rejects_instance_overridden_evidence_writer(
    tmp_path, monkeypatch
):
    authorization, kwargs = _production_authorization_fixture(tmp_path, monkeypatch)
    kwargs["evidence_writer"].write_event = lambda _event: None

    with pytest.raises(AdvancedReplayUnavailable, match="methods cannot be overridden"):
        AdvancedReplayOrchestrator.from_authorization(authorization, **kwargs)


def test_advanced_application_factory_rechecks_gate_before_creating_output(
    tmp_path, monkeypatch
):
    authorization, _kwargs = _production_authorization_fixture(tmp_path, monkeypatch)
    output_root = tmp_path / "must-not-exist"

    def refuse_gate(*_args, **_kwargs):
        raise AdvancedReplayUnavailable("physical calibration is no longer valid")

    monkeypatch.setattr(
        advanced_application_module, "authorize_advanced_replay", refuse_gate
    )

    with pytest.raises(AdvancedReplayUnavailable, match="physical calibration"):
        create_advanced_replay_application(
            authorization,
            output_root=output_root,
            frame_limit=1,
            initial_speed=4.0,
        )

    assert output_root.exists() is False


@pytest.mark.parametrize("generation", [0, -1, True, 1.5])
def test_advanced_application_rejects_nonpositive_or_coercive_generation_before_output(
    tmp_path, monkeypatch, generation
):
    authorization, _kwargs = _production_authorization_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        advanced_application_module,
        "authorize_advanced_replay",
        lambda *_args, **_kwargs: authorization,
    )
    output_root = tmp_path / f"invalid-generation-{generation!s}"

    with pytest.raises(ValueError, match="initial_generation"):
        create_advanced_replay_application(
            authorization,
            output_root=output_root,
            frame_limit=1,
            initial_generation=generation,
        )

    assert output_root.exists() is False


def test_advanced_application_finalizes_source_and_restarts_in_isolated_pass(
    tmp_path, monkeypatch
):
    authorization, _kwargs = _production_authorization_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        advanced_application_module,
        "authorize_advanced_replay",
        lambda *_args, **_kwargs: authorization,
    )
    manager = create_advanced_replay_application(
        authorization,
        output_root=tmp_path / "advanced-application",
        reference_timeline=_empty_reference(),
        frame_limit=1,
        initial_speed=4.0,
        shutdown_timeout_s=2.0,
    )

    manager.start()
    first_runtime = manager.current_run
    first_pass = manager.pass_dir
    assert first_runtime._ended.wait(timeout=3.0)
    assert manager.restart() is True
    transition = manager._transition
    assert transition is not None
    transition.join(timeout=5.0)
    assert transition.is_alive() is False

    assert manager.failure is None
    assert manager.generation == 2
    assert manager.pass_dir != first_pass
    assert manager.pass_dir.name == "pass_0002"
    assert manager.current_run._ended.wait(timeout=3.0)
    manager.stop(timeout_s=3.0)
    assert manager.clean_shutdown is True
    assert manager.failure is None

    for pass_dir, generation in ((first_pass, 1), (manager.pass_dir, 2)):
        metadata = json.loads(
            (pass_dir / "run_metadata.json").read_text(encoding="utf-8")
        )
        summary = json.loads(
            (pass_dir / "summary.json").read_text(encoding="utf-8")
        )
        manifest = json.loads(
            (pass_dir / "manifest.json").read_text(encoding="utf-8")
        )
        assert metadata["generation"] == generation
        assert metadata["registered_adc_sha256"] == (
            authorization.session.registered_adc_sha256
        )
        assert metadata["calibration_sha256"] == authorization.calibration_sha256
        assert summary["processed_frames"] == 1
        assert summary["source_integrity"] == {
            "complete": True,
            "final_sha256": authorization.session.registered_adc_sha256,
            "frames_read": 1,
            "initial_sha256": authorization.session.registered_adc_sha256,
            "streamed_sha256": authorization.session.registered_adc_sha256,
        }
        assert set(manifest["files"]) >= {
            "run_metadata.json",
            "analysis_attempts.csv",
            "controller_events.csv",
            "summary.json",
            "display_state_intervals.json",
            "reference_audit.json",
            "replay_controller_events.jsonl",
            "replay_playback_events.jsonl",
            "replay_performance.jsonl",
        }
        with (pass_dir / "analysis_attempts.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            attempts = csv.DictReader(handle)
            assert attempts.fieldnames is not None
            assert list(attempts) == []
        assert verify_development_evidence(pass_dir) == []
        report = verify_replay_with_source(
            pass_dir,
            authorization.session,
            reference_timeline=manager.reference_timeline,
        )
        assert report.internal_issues == ()
        assert report.source_issues == ()
        assert report.source_binding_complete is True
        assert report.accepted is True


def test_full_verifier_rejects_resigned_calibration_snapshot_tampering(
    tmp_path, monkeypatch
):
    manager, authorization = _completed_advanced_application(
        tmp_path, monkeypatch, "calibration-snapshot-tamper"
    )
    _assert_advanced_pass_is_accepted(manager, authorization)
    snapshot = manager.pass_dir / "development_calibration_snapshot.json"
    assert snapshot.read_bytes() == authorization.calibration_record_bytes
    snapshot.write_bytes(b"{ }\n")
    _resign_motion_artifact(
        manager.pass_dir, "development_calibration_snapshot.json"
    )

    report = verify_replay_with_source(
        manager.pass_dir,
        authorization.session,
        reference_timeline=manager.reference_timeline,
    )

    assert report.accepted is False
    assert report.source_binding_complete is False
    assert any(
        issue.code == "advanced_authorization_mismatch"
        for issue in report.source_issues
    )


@pytest.mark.parametrize(
    "mutate_integrity",
    [
        lambda summary: summary["source_integrity"].__setitem__("frames_read", 0),
        lambda summary: summary.__setitem__("processed_frames", 0),
        lambda summary: summary["source_integrity"].__setitem__("complete", False),
    ],
    ids=("source-cursor", "processed-boundary", "completion"),
)
def test_motion_verifier_rejects_resigned_source_integrity_tampering(
    tmp_path, monkeypatch, mutate_integrity
):
    manager, authorization = _completed_advanced_application(
        tmp_path, monkeypatch, "motion-source-integrity-tamper"
    )
    _assert_advanced_pass_is_accepted(manager, authorization)
    summary_path = manager.pass_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    mutate_integrity(summary)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _resign_motion_artifact(manager.pass_dir, "summary.json")

    report = verify_replay_with_source(
        manager.pass_dir,
        authorization.session,
        reference_timeline=manager.reference_timeline,
    )

    assert report.accepted is False
    assert report.source_binding_complete is False
    assert any(
        issue.code == "source_integrity_mismatch"
        for issue in report.source_issues
    )


@pytest.mark.parametrize(
    ("field", "value", "issue_code"),
    [
        ("generation", True, "event_generation_mismatch"),
        ("generation", 1.0, "event_generation_mismatch"),
        ("boundary", True, "event_scalar_type"),
        ("boundary", 1.0, "event_scalar_type"),
    ],
)
def test_zero_attempt_motion_verifier_rejects_coercive_playback_integers(
    tmp_path, monkeypatch, field, value, issue_code
):
    manager, authorization = _completed_advanced_application(
        tmp_path, monkeypatch, "motion-playback-generation-tamper"
    )
    _assert_advanced_pass_is_accepted(manager, authorization)
    path = manager.pass_dir / "replay_playback_events.jsonl"
    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert events
    for event in events:
        event[field] = value
    path.write_text(
        "".join(
            json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
            for event in events
        ),
        encoding="utf-8",
        newline="\n",
    )
    _resign_motion_artifact(manager.pass_dir, "replay_playback_events.jsonl")

    report = verify_replay_with_source(
        manager.pass_dir,
        authorization.session,
        reference_timeline=manager.reference_timeline,
    )

    assert report.accepted is False
    assert any(
        issue.code == issue_code
        for issue in report.internal_issues
    )


@pytest.mark.parametrize(
    ("target", "field", "value"),
    [
        ("request", "generation", True),
        ("request", "generation", 1.0),
        ("request", "epoch", 2.0),
        ("request", "selection_revision", 3.0),
        ("request", "frame_start", 0.0),
        ("request", "requested_bin", True),
        ("application", "generation", 1.0),
        ("application", "request_epoch", 2.0),
        ("application", "applied_epoch", 2.0),
        ("application", "selected_bin", 5),
    ],
)
def test_motion_lineage_rejects_coercive_or_mismatched_identity(
    tmp_path, target, field, value
):
    job_id = "motion-attempt"
    run_dir = tmp_path / "motion-lineage"
    attempt_dir = run_dir / "analysis_attempts"
    attempt_dir.mkdir(parents=True)
    (run_dir / "run_metadata.json").write_text(
        '{"generation":1}\n', encoding="utf-8", newline="\n"
    )
    npz_name = "analysis_attempts/attempt_000000.npz"
    np.savez(
        run_dir / npz_name,
        epoch=np.asarray(2, dtype=np.int64),
        requested_selection_revision=np.asarray(3, dtype=np.int64),
        stage=np.asarray("rolling"),
        frame_start=np.asarray(0, dtype=np.int64),
        frame_stop=np.asarray(600, dtype=np.int64),
        actual_bin=np.asarray(4, dtype=np.int64),
        disposition=np.asarray("accepted"),
        rejection_reason=np.asarray("accepted"),
        selection_revision=np.asarray(3, dtype=np.int64),
        hr_window_start=np.asarray(0, dtype=np.int64),
        br_window_start=np.asarray(0, dtype=np.int64),
        fresh_hr_bpm=np.asarray(70.0, dtype=np.float64),
        fresh_br_bpm=np.asarray(np.nan, dtype=np.float64),
        quiet_assessment_accepted=np.asarray(False, dtype=np.bool_),
        display_hr_state=np.asarray("fresh"),
        display_hr_bpm=np.asarray(70.0, dtype=np.float64),
        display_br_state=np.asarray("missing"),
        display_br_bpm=np.asarray(np.nan, dtype=np.float64),
    )
    with (run_dir / "analysis_attempts.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=("job_id", "npz_file"))
        writer.writeheader()
        writer.writerow({"job_id": job_id, "npz_file": npz_name})
    request_event = {
        "kind": "analysis_requested",
        "job_id": job_id,
        "generation": 1,
        "epoch": 2,
        "selection_revision": 3,
        "stage": "rolling",
        "frame_start": 0,
        "frame_stop": 600,
        "requested_bin": 4,
        "selection_mode": "committed",
    }
    application_event = {
        "kind": "result_applied",
        "job_id": job_id,
        "generation": 1,
        "disposition": "accepted",
        "reason": "accepted",
        "request_epoch": 2,
        "request_selection_revision": 3,
        "request_stage": "rolling",
        "request_frame_start": 0,
        "request_frame_stop": 600,
        "requested_bin": 4,
        "selection_mode": "committed",
        "signal_frame_start": 0,
        "signal_frame_stop": 600,
        "applied_epoch": 2,
        "applied_selection_revision": 3,
        "selected_bin": 4,
        "hr_window_start": 0,
        "br_window_start": 0,
        "hr_accepted": True,
        "br_accepted": False,
        "quiet_accepted": False,
        "hr_state": "fresh",
        "br_state": "missing",
        "display_hr_bpm": 70.0,
        "display_br_bpm": None,
        "application_boundary": 600,
    }
    event = request_event if target == "request" else application_event
    event[field] = value

    issues = verification_module._verify_motion_application_lineage(
        run_dir, [request_event, application_event]
    )

    assert any(issue.code == "motion_application_lineage" for issue in issues)


def test_advanced_application_revalidates_calibration_before_restart_output(
    tmp_path, monkeypatch
):
    authorization, _kwargs = _production_authorization_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        advanced_application_module,
        "authorize_advanced_replay",
        lambda *_args, **_kwargs: authorization,
    )
    manager = create_advanced_replay_application(
        authorization,
        output_root=tmp_path / "revoked-calibration",
        reference_timeline=_empty_reference(),
        frame_limit=1,
        initial_speed=4.0,
        shutdown_timeout_s=2.0,
    )
    manager.start()
    assert manager.current_run._ended.wait(timeout=3.0)
    monitor = manager._run.monitor
    assert monitor is not None
    monitor.join(timeout=5.0)
    assert monitor.is_alive() is False
    assert manager.clean_shutdown is True

    def revoked(*_args, **_kwargs):
        raise AdvancedReplayUnavailable("physical calibration was revoked")

    monkeypatch.setattr(
        advanced_application_module, "authorize_advanced_replay", revoked
    )
    assert manager.restart() is True
    transition = manager._transition
    assert transition is not None
    transition.join(timeout=5.0)

    assert transition.is_alive() is False
    assert manager.generation == 1
    assert isinstance(manager.failure, AdvancedReplayUnavailable)
    assert "revoked" in str(manager.failure)
    assert (manager.output_root / "pass_0002").exists() is False


def test_advanced_stop_during_blocked_restart_prevents_new_pass(
    tmp_path, monkeypatch
):
    authorization, _kwargs = _production_authorization_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        advanced_application_module,
        "authorize_advanced_replay",
        lambda *_args, **_kwargs: authorization,
    )
    manager = create_advanced_replay_application(
        authorization,
        output_root=tmp_path / "stop-during-restart",
        reference_timeline=_empty_reference(),
        frame_limit=1,
        initial_speed=4.0,
        shutdown_timeout_s=2.0,
    )
    manager.start()
    assert manager.current_run._ended.wait(timeout=3.0)
    monitor = manager._run.monitor
    assert monitor is not None
    monitor.join(timeout=5.0)
    assert monitor.is_alive() is False
    assert manager.clean_shutdown is True

    stop_entered = threading.Event()
    stop_release = threading.Event()
    original_stop = manager.current_run.stop

    def blocked_stop(*, timeout_s=None):
        stop_entered.set()
        assert stop_release.wait(timeout=3.0)
        original_stop(timeout_s=timeout_s)

    monkeypatch.setattr(manager.current_run, "stop", blocked_stop)
    assert manager.restart() is True
    assert stop_entered.wait(timeout=2.0)
    manager.request_stop()
    stop_release.set()
    transition = manager._transition
    stop_thread = manager._stop_thread
    assert transition is not None and stop_thread is not None
    transition.join(timeout=5.0)
    stop_thread.join(timeout=5.0)

    assert transition.is_alive() is False
    assert stop_thread.is_alive() is False
    assert manager.generation == 1
    assert (manager.output_root / "pass_0002").exists() is False


def test_advanced_stop_after_restart_build_leaves_explicit_incomplete_pass(
    tmp_path, monkeypatch, request
):
    manager, _authorization = _completed_advanced_application(
        tmp_path, monkeypatch, "stop-after-advanced-build"
    )
    original_build = manager._build_run
    built = {}
    build_complete = threading.Event()
    release_install = threading.Event()

    def blocked_build(*args, **kwargs):
        run = original_build(*args, **kwargs)
        built["run"] = run
        build_complete.set()
        if not release_install.wait(timeout=3.0):
            raise TimeoutError("test did not release advanced restart install")
        return run

    monkeypatch.setattr(manager, "_build_run", blocked_build)

    def cleanup():
        release_install.set()
        manager.stop(timeout_s=1.0)

    request.addfinalizer(cleanup)
    assert manager.restart() is True
    assert build_complete.wait(timeout=3.0)
    previous = manager.current_run
    calls = []
    monkeypatch.setattr(previous, "request_pause", lambda: calls.append("pause"))
    monkeypatch.setattr(previous, "resume", lambda: calls.append("resume"))
    monkeypatch.setattr(previous, "set_speed", lambda value: calls.append(value))
    assert manager.request_pause() is False
    assert manager.resume() is False
    assert manager.set_speed(2.0) is False
    assert calls == []

    manager.request_stop()
    release_install.set()
    transition = manager._transition
    stop_thread = manager._stop_thread
    assert transition is not None and stop_thread is not None
    transition.join(timeout=5.0)
    stop_thread.join(timeout=5.0)

    assert transition.is_alive() is False
    assert stop_thread.is_alive() is False
    assert manager.generation == 1
    assert isinstance(manager.failure, RuntimeError)
    assert "incomplete pass" in str(manager.failure)
    assert manager.clean_shutdown is False
    next_run = built["run"]
    assert next_run.runtime._worker is None
    assert next_run.runtime._controller_thread is None
    assert (next_run.pass_dir / "summary.json").exists() is False
    assert (next_run.pass_dir / "manifest.json").exists() is False


def test_advanced_stuck_restart_transition_times_out_without_new_worker(
    tmp_path, monkeypatch, request
):
    manager, _authorization = _completed_advanced_application(
        tmp_path, monkeypatch, "stuck-advanced-restart"
    )
    original_build = manager._build_run
    built = {}
    build_complete = threading.Event()
    release_install = threading.Event()

    def stuck_build(*args, **kwargs):
        run = original_build(*args, **kwargs)
        built["run"] = run
        build_complete.set()
        if not release_install.wait(timeout=3.0):
            raise TimeoutError("test did not release stuck advanced restart")
        return run

    monkeypatch.setattr(manager, "_build_run", stuck_build)

    def cleanup():
        release_install.set()
        transition = manager._transition
        if transition is not None:
            transition.join(timeout=3.0)

    request.addfinalizer(cleanup)
    assert manager.restart() is True
    assert build_complete.wait(timeout=3.0)

    manager.stop(timeout_s=0.01)

    transition = manager._transition
    assert transition is not None and transition.is_alive()
    assert isinstance(manager.failure, TimeoutError)
    assert manager.clean_shutdown is False
    first_failure = manager.failure
    assert manager.generation == 1
    next_run = built["run"]
    assert next_run.runtime._worker is None
    assert next_run.runtime._controller_thread is None

    release_install.set()
    transition.join(timeout=5.0)
    assert transition.is_alive() is False
    assert manager.failure is first_failure


def test_advanced_application_refuses_restart_after_unclean_shutdown(
    tmp_path, monkeypatch
):
    authorization, _kwargs = _production_authorization_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        advanced_application_module,
        "authorize_advanced_replay",
        lambda *_args, **_kwargs: authorization,
    )
    manager = create_advanced_replay_application(
        authorization,
        output_root=tmp_path / "unclean-advanced-application",
        frame_limit=1,
        initial_speed=4.0,
        shutdown_timeout_s=1.0,
    )
    initial_pass = manager.pass_dir
    monkeypatch.setattr(manager.current_run, "clean_shutdown", False)

    assert manager.restart() is True
    transition = manager._transition
    assert transition is not None
    transition.join(timeout=3.0)

    assert transition.is_alive() is False
    assert manager.generation == 1
    assert manager.pass_dir == initial_pass
    assert (manager.output_root / "pass_0002").exists() is False


def test_advanced_application_controls_forward_without_blocking_stop_caller(
    tmp_path, monkeypatch
):
    authorization, _kwargs = _production_authorization_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        advanced_application_module,
        "authorize_advanced_replay",
        lambda *_args, **_kwargs: authorization,
    )
    manager = create_advanced_replay_application(
        authorization,
        output_root=tmp_path / "advanced-controls",
        frame_limit=1,
        initial_speed=1.0,
        shutdown_timeout_s=1.0,
    )
    runtime = manager.current_run
    calls = []
    stop_entered = threading.Event()
    stop_release = threading.Event()
    monkeypatch.setattr(runtime, "request_pause", lambda: calls.append("pause"))
    monkeypatch.setattr(runtime, "resume", lambda: calls.append("resume"))
    monkeypatch.setattr(runtime, "set_speed", lambda value: calls.append(value))

    def blocking_stop(*, timeout_s=None):
        calls.append(("stop", timeout_s))
        stop_entered.set()
        assert stop_release.wait(timeout=3.0)

    monkeypatch.setattr(runtime, "stop", blocking_stop)
    manager.request_pause()
    manager.set_speed(2.0)
    manager.resume()
    manager.request_stop()

    assert calls[:3] == ["pause", 2.0, "resume"]
    assert stop_entered.wait(timeout=2.0)
    stop_thread = manager._stop_thread
    assert stop_thread is not None and stop_thread.is_alive()
    stop_release.set()
    stop_thread.join(timeout=3.0)
    assert stop_thread.is_alive() is False
