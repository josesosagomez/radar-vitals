"""Synthetic end-to-end tests for the live-motion acquisition runtime.

The fixtures deliberately use the production SampleSwap decoder, but never open a
socket or touch radar hardware.  Thread progress is controlled with events rather
than sleeps so stage-boundary assertions remain deterministic.
"""
from __future__ import annotations

import copy
import csv
import ctypes
import hashlib
import json
import threading
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import yaml

import scripts.live_demo as live_demo
from scripts.live_demo import LiveFrameSource
from scripts.verify_live_demo_artifacts import verify_run
from src.live_motion import calibration as calibration_module
from src.live_motion.calibration import semantic_identity
from src.live_motion.cache import RangeCacheSnapshot, reconstruct_phase
from src.live_motion.config import (
    FeatureThreshold,
    LiveMotionSettings,
    MovementThresholds,
    PresenceThresholds,
)
from src.live_motion.controller import (
    AttemptDisposition,
    ControllerEvent,
    DisplaySnapshot,
    DisplayValue,
)
from src.live_motion.evidence import (
    EXTENDED_ANALYSIS_FIELDS,
    AttemptEvidenceWriter,
    verify_development_evidence,
)
import src.live_motion.runtime as runtime_module
import src.live_motion.breathing as breathing_module
from src.live_motion.runtime import (
    ExecutedAnalysis,
    LiveMotionRuntime,
    production_analysis_function,
)
from src.live_motion.scheduler import AnalysisJob, AnalysisResult, SelectionDecision
from src.m2.common import canonical_json_bytes


_END = object()


def _cfg() -> dict:
    return {
        "seed": 42,
        "session": {"frame_rate_hz": 20.0},
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
        "hw_profile": {},
        "hw_frame": {},
        "capture": {"record_raw_stream": True},
        "phase": {"method": "delta_before_mean", "clutter_removal": "none"},
        "respiration": {},
        "heart": {},
        "development_motion": {
            "extended_breathing_enabled": False,
            "preliminary_stages_s": [10, 20],
            "ordinary_window_s": 30,
            "ordinary_hop_s": 3,
            "monitor_window_s": 1.0,
            "monitor_hop_s": 0.25,
            "stillness_confirmation_s": 3,
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
        extended_breathing_enabled=True,
        extended_breathing_window_frames=1200,
        extended_breathing_band_hz=(0.05, 0.50),
        candidate_bins=(1, 2),
        presence_thresholds=PresenceThresholds(-10.0, 0.0),
        movement_thresholds=MovementThresholds(
            presence=FeatureThreshold(False, None, None),
            phase_activity=FeatureThreshold(True, 0.5, 0.1),
            range_profile_change=FeatureThreshold(True, 0.5, 0.1),
        ),
        calibration_record={"accepted": True},
        calibration_record_path=None,
        calibration_record_sha256="c" * 64,
    )


def _synthetic_motion_summaries(split: str, per_state: int) -> list[dict[str, object]]:
    summaries = []
    for presence in calibration_module.PRESENCE_STATES:
        for index in range(per_state):
            row_index = len(summaries)
            acquisition_id = f"integration-{split}-{presence}-{index}"
            episodes = []
            if presence == "strong":
                episodes = [{
                    "subtype": subtype,
                    "assigned_features": (
                        ["phase_activity", "range_profile_change"]
                        if split == "training" else []
                    ),
                    "feature_maxima": {
                        "presence": 0.0,
                        "phase_activity": 0.9,
                        "range_profile_change": 0.9,
                    },
                    "frame_bounds": [20, 40],
                } for subtype in calibration_module.REQUIRED_MOVEMENT_SUBTYPES]
            summaries.append({
                "schema": calibration_module.SUMMARY_SCHEMA,
                "acquisition_id": acquisition_id,
                "raw_sha256": hashlib.sha256(acquisition_id.encode()).hexdigest(),
                "capture_provenance": "synthetic_fixture",
                "capture_source_id": f"synthetic:{acquisition_id}",
                "capture_git_commit": "f" * 40,
                "capture_git_dirty": False,
                "run_metadata_sha256": hashlib.sha256(
                    f"{acquisition_id}:metadata".encode()
                ).hexdigest(),
                "frame_validity_sha256": hashlib.sha256(
                    f"{acquisition_id}:validity".encode()
                ).hexdigest(),
                "split": split,
                "presence_state": presence,
                "position": calibration_module.POSITIONS[row_index % 3],
                "placement_m": (0.85, 1.10, 1.35)[row_index % 3],
                "movement_negative_state": calibration_module.MOVEMENT_NEGATIVE_STATES[row_index % 3],
                "breathing_state": None,
                "breathing_frame_bounds": None,
                "breathing_monitor_blocks": [],
                "breathing_measurements": [],
                "seed": 42,
                "presence_power_db": [{"empty": -10.0, "weak": -5.0, "strong": 0.0}[presence]],
                "still_features": {
                    "presence": [0.0],
                    "phase_activity": [0.1],
                    "range_profile_change": [0.1],
                },
                "target_kind": {
                    "empty": "empty_scene",
                    "weak": "weak_reflector",
                    "strong": "participant",
                }[presence],
                "movement_episodes": episodes,
            })
    return summaries


def _synthetic_motion_calibration_record(cfg: dict) -> dict[str, object]:
    candidate = calibration_module.build_candidate(
        _synthetic_motion_summaries("training", 3), cfg
    )
    record = calibration_module.evaluate_candidate(
        candidate, _synthetic_motion_summaries("heldout", 2), cfg
    )
    assert record["status"] == "accepted"
    return record


def _breathing_thresholds() -> dict[str, float]:
    return {
        "fft_score_min": 1.0,
        "ha_score_min": 0.0,
        "quiet_resp_rms_max": 1e-6,
        "periodic_resp_rms_min": 0.1,
        "subband_rms_max": 1e-6,
        "drift_rms_max": 1e-6,
        "persistence_min": 0.7,
    }


def _breathing_settings() -> LiveMotionSettings:
    return replace(
        _settings(),
        calibration_record={"thresholds": {"breathing": _breathing_thresholds()}},
    )


def _movement_only_settings() -> LiveMotionSettings:
    return replace(_settings(), extended_breathing_enabled=False)


def _extended_job(phase: np.ndarray, *, actual_bin=1) -> AnalysisJob:
    phase = np.asarray(phase, dtype=np.float64)
    assert phase.shape == (1200,)
    samples = np.ones((1200, 1, 1, 2), dtype=np.complex64)
    samples[:, 0, 0, 0] = np.exp(1j * phase).astype(np.complex64)
    samples[:, 0, 0, 1] = np.exp(-0.3j * phase).astype(np.complex64)
    snapshot = RangeCacheSnapshot(
        frame_indices=np.arange(60, 1260, dtype=np.int64),
        valid=np.ones(1200, dtype=np.bool_),
        candidate_bins=np.asarray([1, 2], dtype=np.int32),
        samples=samples,
    )
    return AnalysisJob(
        job_id="job-extended-production",
        epoch=0,
        selection_revision=2,
        stage="extended_60",
        frame_start=60,
        frame_stop=1260,
        requested_bin=actual_bin,
        selection_mode="committed",
        raw_frames=np.zeros((600, 1, 1, 8), dtype=np.complex64),
        range_cache_snapshot=snapshot,
    )


def _breathing_tone(rate_bpm: float) -> np.ndarray:
    time_s = np.arange(1200, dtype=np.float64) / 20.0
    return np.sin(2.0 * np.pi * (rate_bpm / 60.0) * time_s)


def _sample_swap_bytes(samples: np.ndarray) -> bytes:
    """Encode complex integer samples using SDK SampleSwap=1 word ordering."""
    flat = np.asarray(samples, dtype=np.complex64).reshape(-1)
    assert flat.size % 2 == 0
    assert np.all(flat.real == flat.real.astype(np.int16))
    assert np.all(flat.imag == flat.imag.astype(np.int16))
    words = np.empty((flat.size // 2, 4), dtype="<i2")
    words[:, 0] = flat[0::2].imag.astype(np.int16)
    words[:, 1] = flat[1::2].imag.astype(np.int16)
    words[:, 2] = flat[0::2].real.astype(np.int16)
    words[:, 3] = flat[1::2].real.astype(np.int16)
    return words.tobytes()


def _decoder() -> LiveFrameSource:
    decoder = object.__new__(LiveFrameSource)
    decoder._n_chirps = 1
    decoder._n_rx = 1
    decoder._n_adc = 8
    decoder._iq_swap = True
    return decoder


class _DecodedSource:
    """Finite source which mirrors exact bytes and decodes every frame in production."""

    def __init__(
        self,
        frame_bytes: list[bytes],
        mirror_path: Path,
        *,
        validity: list[bool] | None = None,
        gates: dict[int, threading.Event] | None = None,
    ):
        self._frame_bytes = list(frame_bytes)
        self._mirror_path = mirror_path
        self._decoder = _decoder()
        self._cursor = 0
        self._gates = gates or {}
        self.frame_validity = validity or [True] * len(frame_bytes)
        self.queue_overflow_count = 0
        self.start_count = 0
        self.stop_count = 0

    def start(self) -> None:
        self.start_count += 1
        self._mirror_path.write_bytes(b"".join(self._frame_bytes))

    def get_frame(self, timeout_s: float):
        del timeout_s
        gate = self._gates.get(self._cursor)
        if gate is not None and not gate.wait(timeout=3.0):
            raise TimeoutError(f"analysis at frame boundary {self._cursor} did not finish")
        if self._cursor == len(self._frame_bytes):
            return _END
        index = self._cursor
        self._cursor += 1
        cube = self._decoder._decode_frame(self._frame_bytes[index])
        return index, cube, self.frame_validity[index]

    def stop(self) -> None:
        self.stop_count += 1


def _stationary_frame_bytes(frame_count: int) -> list[bytes]:
    # An interior ADC impulse survives the production Hann window and has equal,
    # nonzero range-FFT power at every candidate bin.
    samples = np.zeros((1, 1, 8), dtype=np.complex64)
    samples[0, 0, 3] = 16 + 4j
    encoded = _sample_swap_bytes(samples)
    return [encoded] * frame_count


def _impulse_frame_bytes(value: complex) -> bytes:
    samples = np.zeros((1, 1, 8), dtype=np.complex64)
    samples[0, 0, 3] = value
    return _sample_swap_bytes(samples)


def _evidence(job, *, exceptional=False) -> dict[str, object]:
    reason = "synthetic_exception" if exceptional else ""
    phase_length = int(job.raw_frames.shape[0]) if job.raw_frames is not None else 600
    phase = np.zeros(phase_length, dtype=np.float64)
    frequency = np.asarray([0.2, 1.2], dtype=np.float64)
    spectrum = np.asarray([1.0, 2.0], dtype=np.float64)
    evidence: dict[str, object] = {
        "phase_raw": phase,
        "phase_clean": phase.copy(),
        "heart_freqs_hz": frequency,
        "heart_spectrum": spectrum,
        "resp_freqs_hz": frequency,
        "resp_spectrum": spectrum,
        "selected_peak_bins": np.asarray([1, 1], dtype=np.int64),
        "respiration_inputs_hz": np.asarray([0.2], dtype=np.float64),
        "activity_resp_projection": np.array([], dtype=np.float64),
        "activity_subband_projection": np.array([], dtype=np.float64),
        "coherence_scores": np.array([], dtype=np.float64),
        "thresholds": np.asarray([0.5], dtype=np.float64),
        "rejection_reasons": np.asarray([reason], dtype="U"),
        "hr_window_start": max(job.frame_start, job.frame_stop - 600),
        "br_window_start": job.frame_start,
        "exceptional_evidence": exceptional,
        "corrected_range_m": 0.1,
    }
    if job.stage == "extended_60" or job.frame_stop - job.frame_start == 1200:
        for field in EXTENDED_ANALYSIS_FIELDS:
            evidence[field] = np.array([], dtype=np.float64)
    return evidence


class _DeterministicAnalysis:
    def __init__(self, gates: dict[int, threading.Event] | None = None):
        self.jobs = []
        self._gates = gates or {}

    def __call__(self, job):
        self.jobs.append(job)
        decision = None
        actual_bin = job.requested_bin
        if job.selection_mode == "select":
            actual_bin = 1
            selection_evidence = {
                "selected_bin": 1,
                "fallback_used": False,
                "selection_reason": "passed",
                "candidates": [{"bin": 1, "dsp_passed": True}],
            }
            decision = SelectionDecision(
                1, selection_evidence, True, True, False, "passed"
            )
        dsp = {"hr_valid": True, "br_valid": True, "hr_raw": 70.0, "br_bpm": 12.0}
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
            selection_decision=decision,
        )
        executed = ExecutedAnalysis(result, _evidence(job), 0.001, 0.0004 if decision else None)
        event = self._gates.get(job.frame_stop)
        if event is not None:
            event.set()
        return executed


def _runtime(tmp_path: Path, source, analysis, **kwargs) -> LiveMotionRuntime:
    cfg = kwargs.pop("cfg", _cfg())
    settings = kwargs.pop("settings", _settings())
    config_sha256 = kwargs.pop("config_sha256", "a" * 64)
    source_sha256 = kwargs.pop("source_sha256", "b" * 64)
    calibration_sha256 = kwargs.pop("calibration_sha256", "c" * 64)
    return LiveMotionRuntime(
        frame_source=source,
        cfg=cfg,
        settings=settings,
        run_dir=tmp_path,
        config_sha256=config_sha256,
        source_sha256=source_sha256,
        calibration_sha256=calibration_sha256,
        source_id="synthetic:production-sampleswap",
        source_kind="synthetic",
        checkout_commit="test-commit",
        end_sentinel=_END,
        analysis_function=analysis,
        **kwargs,
    )


def _finish(runtime: LiveMotionRuntime) -> None:
    runtime.start()
    assert runtime._ended.wait(timeout=5.0), "controller did not reach source end"
    runtime.stop(timeout_s=2.0)
    assert runtime.failure is None


def test_production_decoder_runtime_stages_raw_identity_and_artifact_verification(
    tmp_path, capsys, monkeypatch
):
    gates = {stop: threading.Event() for stop in (260, 460, 660)}
    raw_path = tmp_path / "adc_stream.bin"
    frame_bytes = _stationary_frame_bytes(660)
    source = _DecodedSource(frame_bytes, raw_path, gates=gates)
    analysis = _DeterministicAnalysis(gates)
    cfg = _cfg()
    calibration_record = _synthetic_motion_calibration_record(cfg)
    calibration_snapshot = tmp_path / "calibration.snapshot.json"
    calibration_snapshot.write_bytes(canonical_json_bytes(calibration_record))
    calibration_hash = hashlib.sha256(calibration_snapshot.read_bytes()).hexdigest()
    cfg["development_motion"]["calibration"] = {
        "record_path": calibration_snapshot.name,
        "record_sha256": calibration_hash,
    }
    runtime_validation_cfg = copy.deepcopy(cfg)
    runtime_validation_cfg["development_motion"]["calibration"]["record_path"] = str(
        calibration_snapshot.resolve()
    )
    with pytest.raises(calibration_module.CalibrationError, match="non-hardware"):
        calibration_module.validate_calibration(runtime_validation_cfg)

    def permit_explicit_synthetic_fixture(summaries, field):
        assert field in {"training evidence", "held-out evidence"}
        assert summaries
        assert all(item["capture_provenance"] == "synthetic_fixture" for item in summaries)

    monkeypatch.setattr(
        calibration_module,
        "_require_runtime_hardware_provenance",
        permit_explicit_synthetic_fixture,
    )
    config_snapshot = tmp_path / "configuration.snapshot.yaml"
    config_snapshot.write_text(yaml.safe_dump(cfg, sort_keys=True), encoding="utf-8")
    identity = semantic_identity(cfg)
    identity_path = tmp_path / "source_identity.json"
    identity_path.write_text(json.dumps(identity, sort_keys=True), encoding="utf-8")
    config_hash = hashlib.sha256(config_snapshot.read_bytes()).hexdigest()
    runtime = _runtime(
        tmp_path,
        source,
        analysis,
        cfg=cfg,
        config_sha256=config_hash,
        source_sha256=identity["source_sha256"],
        calibration_sha256=calibration_hash,
    )

    _finish(runtime)

    assert source.start_count == source.stop_count == 1
    assert raw_path.read_bytes() == b"".join(frame_bytes)
    assert [job.stage for job in analysis.jobs] == ["preview_10", "preview_20", "ordinary_30"]
    assert [(job.frame_start, job.frame_stop, job.raw_frames.shape[0]) for job in analysis.jobs] == [
        (60, 260, 200),
        (60, 460, 400),
        (60, 660, 600),
    ]
    assert all(not job.raw_frames.flags.writeable for job in analysis.jobs)
    assert runtime.latest_snapshot.hr.value_bpm == pytest.approx(70.0)
    assert runtime.latest_snapshot.br.value_bpm == pytest.approx(12.0)
    assert verify_development_evidence(tmp_path) == []

    validity_path = tmp_path / "frame_validity.npy"
    np.save(validity_path, np.ones(660, dtype=np.bool_), allow_pickle=False)
    meta = {
        "mode": "live",
        "completion_status": "completed",
        "evidence_version": 1,
        "live_motion_enabled": True,
        "source_id": "synthetic:production-sampleswap",
        "source_kind": "synthetic",
        "config_sha256": config_hash,
        "source_sha256": identity["source_sha256"],
        "calibration_sha256": calibration_hash,
        "config_snapshot": config_snapshot.name,
        "calibration_snapshot": calibration_snapshot.name,
        "source_identity_manifest": identity_path.name,
        "live_raw_mirror_hash": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "live_packet_stats": {
            "source_queue_overflow_count": 0,
            "frame_validity_map_path": validity_path.name,
            "frame_validity_map_sha256": hashlib.sha256(validity_path.read_bytes()).hexdigest(),
            "n_frames": 660,
            "n_invalid_frames": 0,
            "bytes_per_frame": 32,
        },
    }
    (tmp_path / "run_metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    assert verify_run(tmp_path, expect_mode="live") == 0
    assert "development_evidence: PASS" in capsys.readouterr().out

    calibration_snapshot.write_bytes(calibration_snapshot.read_bytes() + b" ")
    assert verify_run(tmp_path, expect_mode="live") == 1
    assert "calibration_snapshot_hash: FAIL" in capsys.readouterr().out
    calibration_snapshot.write_bytes(canonical_json_bytes(calibration_record))

    performance_meta_path = tmp_path / "performance_meta.json"
    def bind_forged_record(record):
        calibration_snapshot.write_bytes(canonical_json_bytes(record))
        forged_calibration_hash = hashlib.sha256(calibration_snapshot.read_bytes()).hexdigest()
        forged_cfg = copy.deepcopy(cfg)
        forged_cfg["development_motion"]["calibration"] = {
            "record_path": calibration_snapshot.name,
            "record_sha256": forged_calibration_hash,
        }
        config_snapshot.write_text(yaml.safe_dump(forged_cfg, sort_keys=True), encoding="utf-8")
        forged_config_hash = hashlib.sha256(config_snapshot.read_bytes()).hexdigest()
        meta.update(
            calibration_sha256=forged_calibration_hash,
            config_sha256=forged_config_hash,
        )
        (tmp_path / "run_metadata.json").write_text(json.dumps(meta), encoding="utf-8")
        performance_meta = json.loads(performance_meta_path.read_text(encoding="utf-8"))
        performance_meta.update(
            calibration_sha256=forged_calibration_hash,
            config_sha256=forged_config_hash,
        )
        performance_meta_path.write_text(json.dumps(performance_meta), encoding="utf-8")

    rejected_record = copy.deepcopy(calibration_record)
    rejected_record["status"] = "rejected"
    rejected_record["rejection_reasons"] = ["forged rejection"]
    bind_forged_record(rejected_record)
    assert verify_run(tmp_path, expect_mode="live") == 1
    assert "detector_calibration_production_validation: FAIL" in capsys.readouterr().out

    threshold_forgery = copy.deepcopy(calibration_record)
    forged_candidate = threshold_forgery["candidate"]
    forged_candidate["thresholds"]["movement"]["phase_activity"]["entry"] = 0.45
    forged_candidate["thresholds_sha256"] = calibration_module._canonical_hash(
        forged_candidate["thresholds"]
    )
    forged_candidate.pop("candidate_payload_sha256")
    forged_candidate["candidate_payload_sha256"] = calibration_module._canonical_hash(
        forged_candidate
    )
    threshold_forgery["candidate_sha256"] = calibration_module._canonical_hash(
        forged_candidate
    )
    threshold_forgery["thresholds"] = copy.deepcopy(forged_candidate["thresholds"])
    threshold_forgery["thresholds_sha256"] = forged_candidate["thresholds_sha256"]
    bind_forged_record(threshold_forgery)
    assert verify_run(tmp_path, expect_mode="live") == 1
    assert "detector_calibration_production_validation: FAIL" in capsys.readouterr().out


def test_invalid_frame_after_accepted_estimate_keeps_continuous_raw_and_independent_history_red(tmp_path):
    gates = {stop: threading.Event() for stop in (260, 460, 660)}
    frame_bytes = _stationary_frame_bytes(701)
    validity = [True] * len(frame_bytes)
    validity[700] = False
    source = _DecodedSource(frame_bytes, tmp_path / "adc_stream.bin", validity=validity, gates=gates)
    analysis = _DeterministicAnalysis(gates)
    runtime = _runtime(tmp_path, source, analysis)

    _finish(runtime)

    snapshot = runtime.latest_snapshot
    assert snapshot.hr.value_bpm == pytest.approx(70.0)
    assert snapshot.br.value_bpm == pytest.approx(12.0)
    assert (snapshot.hr.state, snapshot.hr.color) == ("held", "red")
    assert (snapshot.br.state, snapshot.br.color) == ("held", "red")
    assert snapshot.reason == "source_frame_invalid"
    assert (tmp_path / "adc_stream.bin").stat().st_size == sum(map(len, frame_bytes))
    with (tmp_path / "controller_events.csv").open(newline="", encoding="utf-8") as handle:
        events = list(csv.DictReader(handle))
    assert any(row["kind"] == "invalid_data" and row["frame_index"] == "700" for row in events)


def test_recovery_after_data_loss_reselects_changed_bin_from_only_fresh_frames(tmp_path):
    gates = {stop: threading.Event() for stop in (260, 460, 660, 961)}
    source = _DecodedSource(
        _stationary_frame_bytes(961),
        tmp_path / "adc_stream.bin",
        validity=[False if index == 700 else True for index in range(961)],
        gates=gates,
    )
    base = _DeterministicAnalysis(gates)

    def changed_bin_analysis(job):
        output = base(job)
        if job.selection_mode == "select" and job.epoch >= 1:
            decision = SelectionDecision(
                2,
                {
                    "selected_bin": 2,
                    "fallback_used": False,
                    "selection_reason": "passed",
                    "candidates": [{"bin": 2, "dsp_passed": True}],
                },
                True,
                True,
                False,
                "passed",
            )
            output = replace(
                output,
                result=replace(output.result, actual_bin=2, selection_decision=decision),
                evidence={**output.evidence, "corrected_range_m": 0.2},
            )
        return output

    runtime = _runtime(tmp_path, source, changed_bin_analysis)

    _finish(runtime)

    assert [(job.epoch, job.stage, job.frame_start, job.frame_stop) for job in base.jobs] == [
        (0, "preview_10", 60, 260),
        (0, "preview_20", 60, 460),
        (0, "ordinary_30", 60, 660),
        (1, "preview_10", 761, 961),
    ]
    assert runtime.latest_snapshot.epoch == 1
    assert runtime.latest_snapshot.locked_bin == 2
    assert runtime.latest_snapshot.hr.state == runtime.latest_snapshot.br.state == "preliminary"
    with (tmp_path / "controller_events.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert any(row["kind"] == "invalid_data" and row["epoch"] == "0" for row in rows)
    assert any(row["kind"] == "epoch_started" and row["epoch"] == "1" for row in rows)
    assert verify_development_evidence(tmp_path) == []


def test_physical_phase_activity_invalidates_measurements_without_stopping_raw_capture(tmp_path):
    gates = {stop: threading.Event() for stop in (260, 460, 660)}
    frame_bytes = _stationary_frame_bytes(680)
    frame_bytes.extend(
        _impulse_frame_bytes(16 + 0j if index % 2 == 0 else -16 + 0j)
        for index in range(25)
    )
    source = _DecodedSource(frame_bytes, tmp_path / "adc_stream.bin", gates=gates)
    runtime = _runtime(tmp_path, source, _DeterministicAnalysis(gates))

    _finish(runtime)

    snapshot = runtime.latest_snapshot
    assert (snapshot.hr.state, snapshot.hr.color) == ("held", "red")
    assert (snapshot.br.state, snapshot.br.color) == ("held", "red")
    with (tmp_path / "controller_events.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    motion = [
        row for row in rows
        if row["kind"] == "movement" and row["reason"] == "physical_movement"
    ]
    assert len(motion) == 1
    assert int(motion[0]["frame_index"]) >= 680
    assert (tmp_path / "adc_stream.bin").read_bytes() == b"".join(frame_bytes)


def test_busy_worker_keeps_owned_input_and_bounded_pending_queues(tmp_path):
    started = threading.Event()
    release = threading.Event()
    captured = []

    def blocked(job):
        captured.append(job)
        started.set()
        assert release.wait(timeout=3.0)
        return _DeterministicAnalysis()(job)

    source = _DecodedSource(_stationary_frame_bytes(900), tmp_path / "adc_stream.bin")
    runtime = _runtime(tmp_path, source, blocked)
    runtime.start()
    assert started.wait(timeout=3.0)
    job = captured[0]
    owned_copy = job.raw_frames.copy()
    assert not job.raw_frames.flags.writeable
    mandatory, rolling = runtime.scheduler.pending_counts
    assert mandatory <= 4
    assert rolling <= 1
    assert runtime._work.maxsize == runtime._results.maxsize == 1

    release.set()
    assert runtime._ended.wait(timeout=5.0)
    runtime.stop(timeout_s=2.0)
    assert runtime.failure is None
    np.testing.assert_array_equal(job.raw_frames, owned_copy)
    assert source.start_count == source.stop_count == 1


def test_stop_is_idempotent_and_prevents_late_snapshot_mutation(tmp_path):
    source = _DecodedSource(_stationary_frame_bytes(100), tmp_path / "adc_stream.bin")
    runtime = _runtime(tmp_path, source, _DeterministicAnalysis())
    _finish(runtime)
    stopped_snapshot = runtime.latest_snapshot

    runtime.stop(timeout_s=0.1)

    assert source.stop_count == 1
    assert runtime.latest_snapshot == stopped_snapshot
    performance_meta = json.loads((tmp_path / "performance_meta.json").read_text(encoding="utf-8"))
    assert performance_meta["clean_shutdown"] is True


def test_worker_exception_is_indexed_as_failed_evidence_and_does_not_hang_shutdown(tmp_path):
    def fail_analysis(job):
        raise RuntimeError(f"synthetic worker failure for {job.stage}")

    source = _DecodedSource(_stationary_frame_bytes(261), tmp_path / "adc_stream.bin")
    runtime = _runtime(tmp_path, source, fail_analysis)

    _finish(runtime)

    with (tmp_path / "analysis_attempts.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["stage"] == "preview_10"
    assert rows[0]["executed"] == "1"
    assert "synthetic worker failure" in rows[0]["reason"]
    attempt = np.load(tmp_path / rows[0]["npz_file"], allow_pickle=False)
    assert bool(attempt["exceptional_evidence"].item()) is True
    assert "synthetic worker failure" in attempt["rejection_reasons"].tolist()[0]
    assert verify_development_evidence(tmp_path) == []
    assert runtime.scheduler.active is None


def test_blocked_active_job_is_tombstoned_once_and_late_completion_cannot_mutate_state(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def blocked(job):
        started.set()
        assert release.wait(timeout=3.0)
        return _DeterministicAnalysis()(job)

    source = _DecodedSource(_stationary_frame_bytes(261), tmp_path / "adc_stream.bin")
    runtime = _runtime(tmp_path, source, blocked, shutdown_timeout_s=0.05)
    runtime.start()
    assert started.wait(timeout=2.0)
    assert runtime._ended.wait(timeout=2.0)
    snapshot_at_accounting = runtime.latest_snapshot

    runtime.stop(timeout_s=0.05)
    with (tmp_path / "analysis_attempts.csv").open(newline="", encoding="utf-8") as handle:
        first_rows = list(csv.DictReader(handle))
    assert len(first_rows) == 1
    assert first_rows[0]["executed"] == "1"
    assert first_rows[0]["disposition"] in {"failed", "invalid"}
    assert len(list((tmp_path / "analysis_attempts").glob("*.npz"))) == 1
    assert not list((tmp_path / "analysis_attempts").glob("*.tmp"))

    release.set()
    runtime.stop(timeout_s=1.0)
    assert runtime.latest_snapshot == snapshot_at_accounting
    with (tmp_path / "analysis_attempts.csv").open(newline="", encoding="utf-8") as handle:
        assert len(list(csv.DictReader(handle))) == 1
    assert len(list((tmp_path / "analysis_attempts").glob("*.npz"))) == 1
    with (tmp_path / "controller_events.csv").open(newline="", encoding="utf-8") as handle:
        kinds = [row["kind"] for row in csv.DictReader(handle)]
    assert kinds.count("active_invalidated") == 1
    assert kinds.count("active_tombstoned") == 1
    assert runtime.shutdown_interrupted is True


def test_event_time_active_invalidation_is_logged_and_obsolete_result_is_stale(tmp_path):
    started = threading.Event()
    release = threading.Event()
    controller_reached_end = threading.Event()

    class EndNotifyingSource(_DecodedSource):
        def get_frame(self, timeout_s):
            if self._cursor == len(self._frame_bytes):
                controller_reached_end.set()
            return super().get_frame(timeout_s)

    def blocked(job):
        started.set()
        assert release.wait(timeout=3.0)
        return _DeterministicAnalysis()(job)

    source = EndNotifyingSource(
        _stationary_frame_bytes(301),
        tmp_path / "adc_stream.bin",
        validity=[False if index == 300 else True for index in range(301)],
        gates={301: release},
    )
    runtime = _runtime(tmp_path, source, blocked, shutdown_timeout_s=0.5)
    runtime.start()
    assert started.wait(timeout=2.0)
    assert controller_reached_end.wait(timeout=2.0)
    with (tmp_path / "controller_events.csv").open(newline="", encoding="utf-8") as handle:
        before_release = list(csv.DictReader(handle))
    invalidations = [row for row in before_release if row["kind"] == "active_invalidated"]
    assert len(invalidations) == 1
    assert invalidations[0]["reason"] == "source_frame_invalid"

    release.set()
    assert runtime._ended.wait(timeout=2.0)
    runtime.stop(timeout_s=1.0)
    with (tmp_path / "analysis_attempts.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["disposition"] == "stale_epoch"
    assert rows[0]["fresh_hr_bpm"] == rows[0]["fresh_br_bpm"] == ""
    assert runtime.latest_snapshot.hr.state == runtime.latest_snapshot.br.state == "missing"
    assert runtime.latest_snapshot.locked_bin is None


def test_dispatched_but_not_started_job_is_cancelled_without_evidence_then_fresh_job_runs(tmp_path):
    allow_worker = threading.Event()
    fresh_executed = threading.Event()

    class ReleaseWorkerAtEndSource(_DecodedSource):
        def __init__(self):
            super().__init__(
                _stationary_frame_bytes(521),
                tmp_path / "adc_stream.bin",
                validity=[False if index == 260 else True for index in range(521)],
            )
            self.released = False

        def get_frame(self, timeout_s):
            if self._cursor == len(self._frame_bytes):
                if not self.released:
                    self.released = True
                    allow_worker.set()
                return _END if fresh_executed.wait(timeout=0.02) else None
            return super().get_frame(timeout_s)

    analysis = _DeterministicAnalysis()

    def record_fresh(job):
        result = analysis(job)
        fresh_executed.set()
        return result

    source = ReleaseWorkerAtEndSource()
    runtime = _runtime(tmp_path, source, record_fresh, shutdown_timeout_s=0.5)
    actual_worker_loop = runtime._worker_loop

    def delayed_worker_loop():
        assert allow_worker.wait(timeout=3.0)
        actual_worker_loop()

    runtime._worker_loop = delayed_worker_loop

    _finish(runtime)

    assert [(job.epoch, job.stage, job.frame_start, job.frame_stop) for job in analysis.jobs] == [
        (1, "preview_10", 321, 521)
    ]
    with (tmp_path / "analysis_attempts.csv").open(newline="", encoding="utf-8") as handle:
        attempts = list(csv.DictReader(handle))
    assert len(attempts) == 1
    assert attempts[0]["job_id"] == analysis.jobs[0].job_id
    assert len(list((tmp_path / "analysis_attempts").glob("*.npz"))) == 1
    with (tmp_path / "controller_events.csv").open(newline="", encoding="utf-8") as handle:
        events = list(csv.DictReader(handle))
    cancelled = [row for row in events if row["kind"] == "cancelled_before_execution"]
    assert len(cancelled) == 1
    assert cancelled[0]["job_id"] != attempts[0]["job_id"]
    assert any(row["kind"] == "active_invalidated" for row in events)
    assert runtime.failure is None


def test_source_failure_stops_source_once_and_leaves_no_partial_evidence(tmp_path):
    class FailingSource(_DecodedSource):
        def get_frame(self, timeout_s):
            if self._cursor == 10:
                raise OSError("synthetic source failure")
            return super().get_frame(timeout_s)

    source = FailingSource(_stationary_frame_bytes(30), tmp_path / "adc_stream.bin")
    runtime = _runtime(tmp_path, source, _DeterministicAnalysis(), shutdown_timeout_s=0.05)
    runtime.start()
    assert runtime._ended.wait(timeout=2.0)
    runtime.stop(timeout_s=1.0)

    assert isinstance(runtime.failure, OSError)
    assert str(runtime.failure) == "synthetic source failure"
    assert source.start_count == source.stop_count == 1
    assert runtime.clean_shutdown is False
    assert not list((tmp_path / "analysis_attempts").glob("*.tmp"))
    meta = json.loads((tmp_path / "performance_meta.json").read_text(encoding="utf-8"))
    assert meta["clean_shutdown"] is False


@pytest.mark.parametrize("elapsed, expected_timeouts", [(0.249, 0), (0.250, 1)])
def test_source_starvation_uses_exact_monitor_hop_and_latches_once(
    tmp_path, elapsed, expected_timeouts
):
    frame = _decoder()._decode_frame(_stationary_frame_bytes(1)[0])

    class StarvingSource:
        def __init__(self):
            stalled = [None] if expected_timeouts == 0 else [None, None, None]
            self.items = iter([(0, frame, True), *stalled, (1, frame, True), _END])
            self.start_count = 0
            self.stop_count = 0
            self.queue_overflow_count = 0
            self.frame_validity = [True, True]

        def start(self):
            self.start_count += 1

        def get_frame(self, timeout_s):
            del timeout_s
            return next(self.items)

        def stop(self):
            self.stop_count += 1

    clock_values = iter([0.0, elapsed, elapsed + 0.1])
    source = StarvingSource()
    runtime = _runtime(
        tmp_path,
        source,
        _DeterministicAnalysis(),
        monotonic_fn=lambda: next(clock_values),
    )

    _finish(runtime)

    events_path = tmp_path / "controller_events.csv"
    rows = []
    if events_path.is_file():
        with events_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    timeouts = [
        row for row in rows
        if row["kind"] == "data_gap" and row["reason"] == "source_frame_timeout"
    ]
    assert len(timeouts) == expected_timeouts
    assert source.start_count == source.stop_count == 1
    assert runtime.latest_snapshot.frame_index == 1


def test_starvation_after_accepted_values_holds_red_and_resume_requires_60_quiet_frames(tmp_path):
    gates = {stop: threading.Event() for stop in (260, 460, 660)}
    clock = {"value": 0.0}

    class AcceptedThenStarvedSource(_DecodedSource):
        def __init__(self):
            super().__init__(
                _stationary_frame_bytes(720), tmp_path / "adc_stream.bin", gates=gates
            )
            self.stalls = iter([0.249, 0.250, 0.500])
            self.stall_complete = False

        def get_frame(self, timeout_s):
            if self._cursor == 660 and not self.stall_complete:
                assert gates[660].wait(timeout=3.0)
                try:
                    clock["value"] = next(self.stalls)
                    return None
                except StopIteration:
                    self.stall_complete = True
            return super().get_frame(timeout_s)

    source = AcceptedThenStarvedSource()
    analysis = _DeterministicAnalysis(gates)
    runtime = _runtime(
        tmp_path,
        source,
        analysis,
        monotonic_fn=lambda: clock["value"],
    )

    _finish(runtime)

    snapshot = runtime.latest_snapshot
    assert snapshot.frame_index == 719
    assert snapshot.epoch == 1
    assert snapshot.reason == "fresh_epoch_started"
    assert (snapshot.hr.value_bpm, snapshot.hr.state, snapshot.hr.color) == (70.0, "held", "red")
    assert (snapshot.br.value_bpm, snapshot.br.state, snapshot.br.color) == (12.0, "held", "red")
    with (tmp_path / "controller_events.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    timeouts = [
        row for row in rows
        if row["kind"] == "data_gap" and row["reason"] == "source_frame_timeout"
    ]
    assert len(timeouts) == 1
    assert any(row["kind"] == "epoch_started" and row["frame_index"] == "719" for row in rows)
    assert not any(job.epoch == 1 for job in analysis.jobs)


def test_live_display_mapper_retains_channels_independently_and_never_formats_zero_br():
    held = DisplaySnapshot(
        frame_index=700,
        epoch=1,
        status="recovery",
        reason="physical_movement",
        hr=DisplayValue(72.0, "held", "red"),
        br=DisplayValue(0.0, "missing", "neutral"),
        breathing_status="",
        locked_bin=1,
        selection_revision=2,
    )
    held_fields = live_demo._live_motion_display_fields(held)
    assert held_fields == {
        "hr_text": "72.0 bpm",
        "hr_color": "red",
        "br_text": "--",
        "br_color": "black",
        "status_text": "recovery: physical_movement",
    }

    quiet = replace(
        held,
        hr=DisplayValue(72.0, "held", "red"),
        br=DisplayValue(np.nan, "held", "red"),
        breathing_status="No breathing motion detected",
    )
    quiet_fields = live_demo._live_motion_display_fields(quiet)
    assert quiet_fields["hr_text"] == "72.0 bpm"
    assert quiet_fields["hr_color"] == quiet_fields["br_color"] == "red"
    assert quiet_fields["br_text"] == "No breathing motion detected"


def test_cleanup_attempts_every_resource_and_preserves_first_failure():
    calls = []

    class Runtime:
        def stop(self):
            calls.append("runtime.stop")
            raise RuntimeError("runtime stop first")

    class CsvHandle:
        def flush(self):
            calls.append("csv.flush")
            raise OSError("csv flush second")

        def close(self):
            calls.append("csv.close")

    class Device:
        def __init__(self, name, fail_stop=False):
            self.name = name
            self.fail_stop = fail_stop

        def stop(self):
            calls.append(f"{self.name}.stop")
            if self.fail_stop:
                raise RuntimeError(f"{self.name} stop later")

        def close(self):
            calls.append(f"{self.name}.close")

    errors = live_demo._cleanup_live_motion_resources(
        runtime=Runtime(),
        frame_source=pytest.fail,
        csv_handle=CsvHandle(),
        iwr=Device("iwr"),
        dca=Device("dca", fail_stop=True),
    )

    assert calls == [
        "runtime.stop",
        "csv.flush",
        "csv.close",
        "iwr.stop",
        "iwr.close",
        "dca.stop",
        "dca.close",
    ]
    assert [str(error) for error in errors] == [
        "runtime stop first",
        "csv flush second",
        "dca stop later",
    ]
    assert isinstance(errors[0], RuntimeError)


def test_rss_telemetry_reports_current_working_set_only_where_supported():
    value = runtime_module._rss_bytes()
    if hasattr(ctypes, "windll"):
        assert type(value) is int
        assert value > 0
    else:
        assert value is None


def test_production_sampleswap_decoder_uses_exact_sdk_word_order():
    expected = np.asarray(
        [1 + 5j, 2 + 6j, -3 + 7j, -4 - 8j, 9 - 1j, 10 - 2j, 11 - 3j, 12 - 4j],
        dtype=np.complex64,
    ).reshape(1, 1, 8)
    encoded = _sample_swap_bytes(expected)
    decoded = _decoder()._decode_frame(encoded)
    assert encoded == np.asarray(
        [5, 6, 1, 2, 7, -8, -3, -4, -1, -2, 9, 10, -3, -4, 11, 12],
        dtype="<i2",
    ).tobytes()
    np.testing.assert_array_equal(decoded, expected)


def _write_launcher_cfg(tmp_path: Path, *, enabled: bool) -> Path:
    source = Path(live_demo.__file__).with_name("live_demo_calibrated_config.yaml")
    cfg = yaml.safe_load(source.read_text(encoding="utf-8"))
    cfg["session"]["startup_delay_s"] = 10 if enabled else 0
    cfg["paths"]["manifest"] = str(tmp_path / "absent_manifest.csv")
    cfg["paths"]["results_dir"] = str(tmp_path / "must_not_exist")
    cfg["development_motion"] = {
        "enabled": enabled,
        "unavailable_reason": "physical calibration pending" if not enabled else "",
        "extended_breathing_enabled": False,
        "preliminary_stages_s": [10, 20],
        "ordinary_window_s": 30,
        "ordinary_hop_s": 3,
        "monitor_window_s": 1,
        "monitor_hop_s": 0.25,
        "stillness_confirmation_s": 3,
        "extended_breathing_window_s": 60,
        "extended_breathing_band_hz": [0.05, 0.50],
        "calibration": {"record_path": "missing.json", "record_sha256": "d" * 64},
    }
    path = tmp_path / "launcher.yaml"
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return path


def test_launcher_rejects_manual_bin_before_countdown_output_or_hardware(monkeypatch, tmp_path):
    cfg_path = _write_launcher_cfg(tmp_path, enabled=True)
    effects = []

    monkeypatch.setattr(live_demo, "_git_info", lambda: {"git_commit": "test", "git_dirty": False})
    monkeypatch.setattr(live_demo.time, "sleep", lambda _seconds: effects.append("sleep"))
    monkeypatch.setattr(live_demo, "_select_backend", lambda _cfg: effects.append("backend"))
    monkeypatch.setattr(
        live_demo.sys,
        "argv",
        ["live_demo.py", "--config", str(cfg_path), "--locked-bin", "1", "--headless"],
    )

    with pytest.raises(SystemExit, match="--locked-bin is incompatible"):
        live_demo.main()

    assert effects == []
    assert not (tmp_path / "must_not_exist").exists()


@pytest.mark.parametrize("raw_setting", [None, False, 1])
def test_launcher_requires_exact_raw_recording_before_any_side_effect(
    monkeypatch, tmp_path, raw_setting
):
    cfg_path = _write_launcher_cfg(tmp_path, enabled=True)
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    if raw_setting is None:
        cfg["capture"].pop("record_raw_stream", None)
    else:
        cfg["capture"]["record_raw_stream"] = raw_setting
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    effects = []
    monkeypatch.setattr(live_demo, "_git_info", lambda: {"git_commit": "test", "git_dirty": False})
    monkeypatch.setattr(live_demo.time, "sleep", lambda _seconds: effects.append("sleep"))
    monkeypatch.setattr(live_demo, "_select_backend", lambda _cfg: effects.append("backend"))
    monkeypatch.setattr(
        live_demo.sys,
        "argv",
        ["live_demo.py", "--config", str(cfg_path), "--headless"],
    )

    with pytest.raises(SystemExit, match="capture.record_raw_stream=true"):
        live_demo.main()

    assert effects == []
    assert not (tmp_path / "must_not_exist").exists()


def test_disabled_feature_preserves_legacy_dispatch_without_calibration_io(monkeypatch, tmp_path, capsys):
    cfg_path = _write_launcher_cfg(tmp_path, enabled=False)
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg["capture"]["record_raw_stream"] = False
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    reached_backend = RuntimeError("legacy backend reached")

    monkeypatch.setattr(live_demo, "_git_info", lambda: {"git_commit": "test", "git_dirty": False})
    monkeypatch.setattr(live_demo, "_select_backend", lambda _cfg: (_ for _ in ()).throw(reached_backend))
    monkeypatch.setattr(
        live_demo.sys,
        "argv",
        ["live_demo.py", "--config", str(cfg_path), "--headless"],
    )

    with pytest.raises(RuntimeError, match="legacy backend reached"):
        live_demo.main()

    assert "physical calibration pending" in capsys.readouterr().out
    assert not (tmp_path / "must_not_exist").exists()


def _analysis_job(*, selection_mode="select", requested_bin=None) -> AnalysisJob:
    return AnalysisJob(
        job_id="job-production",
        epoch=1,
        selection_revision=0,
        stage="preview_10" if selection_mode == "select" else "preview_20",
        frame_start=60,
        frame_stop=260 if selection_mode == "select" else 460,
        requested_bin=requested_bin,
        selection_mode=selection_mode,
        raw_frames=np.zeros((200 if selection_mode == "select" else 400, 1, 1, 8), dtype=np.complex64),
    )


def _production_dsp() -> dict[str, object]:
    return {
        "hr_valid": True,
        "br_valid": True,
        "hr_raw": 72.0,
        "br_bpm": 12.0,
        "phase_raw": np.zeros(200),
        "phase_clean": np.zeros(200),
        "f_r_hz": 0.2,
        "fft_r": {
            "freqs_hz": np.asarray([0.2]),
            "spectrum": np.asarray([1.0]),
            "fft_selected_bin": 1,
        },
        "ha_r": {"ha_selected_bin": 1},
        "hr_result": {
            "freqs_hz": np.asarray([1.2]),
            "spectrum": np.asarray([2.0]),
            "accepted_candidate_rank": 0,
        },
    }


def test_production_analysis_uses_selector_once_and_preserves_selector_evidence(monkeypatch):
    calls = []
    dsp = _production_dsp()
    selector_evidence = {"fallback_used": False, "selection_reason": "eligible_dsp_pass"}

    def selector(frames, bins, cfg, fs):
        calls.append((frames.shape, tuple(bins), cfg, fs))
        return 2, dsp, selector_evidence

    monkeypatch.setattr(runtime_module, "run_warmup_selection", selector)
    execute = production_analysis_function(_cfg(), _movement_only_settings())
    output = execute(_analysis_job())

    assert calls == [((200, 1, 1, 8), (1, 2), _cfg(), 20.0)]
    assert output.result.status == "completed"
    assert output.result.actual_bin == 2
    assert output.result.selection_decision == SelectionDecision(
        2, selector_evidence, True, True, False, "eligible_dsp_pass"
    )
    assert output.evidence["exceptional_evidence"] is False
    assert output.evidence["corrected_range_m"] == pytest.approx(0.2)
    assert output.selector_elapsed_s is not None


def test_production_analysis_all_failed_selector_keeps_diagnostic_fallback_non_numeric(monkeypatch):
    evidence = {"fallback_used": True, "selection_reason": "all_dsp_failed"}
    monkeypatch.setattr(
        runtime_module,
        "run_warmup_selection",
        lambda frames, bins, cfg, fs: (2, None, evidence),
    )
    output = production_analysis_function(_cfg(), _movement_only_settings())(_analysis_job())

    assert output.result.status == "completed"
    assert output.result.dsp is None
    assert output.result.selection_decision == SelectionDecision(
        2, evidence, True, False, True, "all_dsp_failed"
    )
    assert output.evidence["exceptional_evidence"] is True
    assert output.evidence["phase_raw"].size == 0
    assert output.evidence["rejection_reasons"].tolist() == ["all_dsp_failed"]


def test_production_analysis_selector_exception_is_explicit_failed_attempt(monkeypatch):
    def explode(*_args):
        raise ValueError("synthetic selector error")

    monkeypatch.setattr(runtime_module, "run_warmup_selection", explode)
    output = production_analysis_function(_cfg(), _movement_only_settings())(_analysis_job())

    assert output.result.status == "failed"
    assert output.result.actual_bin is None
    assert output.result.selection_decision is None
    assert output.result.error == "ValueError: synthetic selector error"
    assert np.isnan(output.evidence["corrected_range_m"])
    assert output.evidence["rejection_reasons"].tolist() == ["ValueError: synthetic selector error"]


def test_production_analysis_reuse_calls_dsp_at_the_single_requested_bin(monkeypatch):
    calls = []
    monkeypatch.setattr(
        runtime_module,
        "run_warmup_selection",
        lambda *_args: pytest.fail("selector must not run for provisional-bin reuse"),
    )

    def dsp(frames, locked_bin, fs, cfg):
        calls.append((frames.shape, locked_bin, fs, cfg))
        value = _production_dsp()
        value["phase_raw"] = np.zeros(400)
        value["phase_clean"] = np.zeros(400)
        return value

    monkeypatch.setattr(runtime_module, "run_window_dsp", dsp)
    output = production_analysis_function(_cfg(), _movement_only_settings())(
        _analysis_job(selection_mode="reuse_provisional", requested_bin=2)
    )

    assert calls == [((400, 1, 1, 8), 2, 20.0, _cfg())]
    assert output.result.actual_bin == 2
    assert output.result.selection_decision is None
    assert output.result.status == "completed"


def _ordinary_dsp_600(*, respiration_hz: float | None = 0.2) -> dict[str, object]:
    dsp = _production_dsp()
    dsp["phase_raw"] = np.zeros(600, dtype=np.float64)
    dsp["phase_clean"] = np.zeros(600, dtype=np.float64)
    if respiration_hz is None:
        dsp.pop("f_r_hz", None)
    else:
        dsp["f_r_hz"] = respiration_hz
    return dsp


@pytest.mark.parametrize(
    ("rate_bpm", "respiration_hz", "expected_rate", "expected_veto"),
    [
        (3.0, 3.0 / 60.0, 3.0, "extended_breathing_below_9_bpm"),
        (12.0, 12.0 / 60.0, 12.0, ""),
        (30.0, 30.0 / 60.0, 30.0, ""),
    ],
)
def test_extended_production_analysis_separates_600_frame_dsp_from_1200_frame_cache(
    monkeypatch, rate_bpm, respiration_hz, expected_rate, expected_veto
):
    job = _extended_job(_breathing_tone(rate_bpm))
    ordinary = _ordinary_dsp_600(respiration_hz=respiration_hz)
    original = copy.deepcopy(ordinary)
    calls = []

    def dsp(frames, locked_bin, fs, cfg):
        calls.append((frames.shape, locked_bin, fs, cfg))
        return ordinary

    monkeypatch.setattr(runtime_module, "run_window_dsp", dsp)
    output = production_analysis_function(_cfg(), _breathing_settings())(job)

    assert calls == [((600, 1, 1, 8), 1, 20.0, _cfg())]
    assert output.result.status == "completed"
    assert output.result.breathing.state == "positive"
    assert output.result.breathing.value_bpm == pytest.approx(expected_rate, abs=0.02)
    assert output.result.dsp["hr_veto_reason"] == expected_veto
    assert output.evidence["extended_br_state"] == "positive"
    assert output.evidence["extended_br_bpm"] == pytest.approx(expected_rate, abs=0.02)
    assert output.evidence["extended_analysis_computed"] is True
    assert output.evidence["hr_window_start"] == 660
    assert output.evidence["br_window_start"] == 60
    assert output.evidence["extended_phase"].shape == (1200,)
    np.testing.assert_allclose(
        output.evidence["extended_phase"],
        reconstruct_phase(job.range_cache_snapshot, 1),
        rtol=0.0,
        atol=0.0,
    )
    # Extended BR is a post-DSP decision and must not mutate or rerun the
    # ordinary estimator's respiration input.
    assert ordinary.keys() == original.keys()
    for key in ordinary:
        if isinstance(ordinary[key], np.ndarray):
            np.testing.assert_array_equal(ordinary[key], original[key])
        else:
            assert ordinary[key] == original[key]


@pytest.mark.parametrize(
    ("ordinary_outcome", "ordinary_reason"),
    [
        ("none", "ordinary_dsp_returned_no_result"),
        ("raise", "RuntimeError: synthetic ordinary failure"),
    ],
)
def test_extended_quiet_assessment_survives_ordinary_dsp_failure(
    monkeypatch, ordinary_outcome, ordinary_reason
):
    calls = []

    def dsp(frames, locked_bin, fs, cfg):
        calls.append((frames.shape, locked_bin, fs, cfg))
        if ordinary_outcome == "raise":
            raise RuntimeError("synthetic ordinary failure")
        return None

    monkeypatch.setattr(runtime_module, "run_window_dsp", dsp)
    output = production_analysis_function(_cfg(), _breathing_settings())(
        _extended_job(np.zeros(1200, dtype=np.float64))
    )

    assert calls == [((600, 1, 1, 8), 1, 20.0, _cfg())]
    assert output.result.status == "completed"
    assert output.result.breathing.state == "quiet"
    assert np.isnan(output.result.breathing.value_bpm)
    assert output.result.dsp["hr_veto_reason"] == "quiet_breathing"
    assert output.evidence["exceptional_evidence"] is True
    assert output.evidence["ordinary_exception_reason"] == ordinary_reason
    assert output.evidence["extended_exception_reason"] == ""
    assert output.evidence["extended_analysis_computed"] is True
    assert output.evidence["extended_assessment_state"] == "quiet"
    assert np.isnan(output.evidence["extended_assessment_value_bpm"])
    assert output.evidence["extended_phase"].shape == (1200,)
    assert output.evidence["extended_respiratory_projection"].shape == (1200,)


def test_positive_extended_br_publishes_when_ahet_input_missing_but_vetoes_hr(monkeypatch):
    ordinary = _ordinary_dsp_600(respiration_hz=None)
    monkeypatch.setattr(runtime_module, "run_window_dsp", lambda *_args: ordinary)

    output = production_analysis_function(_cfg(), _breathing_settings())(
        _extended_job(_breathing_tone(12.0))
    )

    assert output.result.breathing.state == "positive"
    assert output.result.breathing.value_bpm == pytest.approx(12.0, abs=0.02)
    assert output.result.dsp["hr_valid"] is True
    assert output.result.dsp["hr_veto_reason"] == "missing_ahet_respiration_input"
    assert np.isnan(output.evidence["ahet_respiration_input_hz"])
    assert output.evidence["extended_br_bpm"] == pytest.approx(12.0, abs=0.02)
    assert "f_r_hz" not in ordinary


def test_unresolved_extended_activity_adds_no_veto_or_numeric_breathing(monkeypatch):
    ordinary = _ordinary_dsp_600(respiration_hz=0.2)
    ordinary["hr_no_eca"] = 91.0
    monkeypatch.setattr(runtime_module, "run_window_dsp", lambda *_args: ordinary)

    output = production_analysis_function(_cfg(), _breathing_settings())(
        _extended_job(_breathing_tone(2.0))
    )

    assert output.result.breathing.state == "unresolved"
    assert np.isnan(output.result.breathing.value_bpm)
    assert output.result.dsp["hr_veto_reason"] == ""
    assert output.result.dsp["hr_valid"] is True
    assert output.result.dsp["hr_no_eca"] == 91.0
    assert output.evidence["extended_br_state"] == "unresolved"
    assert np.isnan(output.evidence["extended_br_bpm"])
    assert output.result.dsp["f_r_hz"] == pytest.approx(0.2)


def test_extended_failure_preserves_ordinary_evidence_without_fabricating_state(monkeypatch):
    ordinary = _ordinary_dsp_600(respiration_hz=0.2)
    monkeypatch.setattr(runtime_module, "run_window_dsp", lambda *_args: ordinary)
    monkeypatch.setattr(
        runtime_module,
        "reconstruct_phase",
        lambda *_args: (_ for _ in ()).throw(ValueError("synthetic cache failure")),
    )

    output = production_analysis_function(_cfg(), _breathing_settings())(
        _extended_job(_breathing_tone(12.0))
    )

    assert output.result.status == "failed"
    assert output.result.breathing is None
    assert output.result.error == "ValueError: synthetic cache failure"
    assert output.evidence["extended_analysis_computed"] is False
    assert output.evidence["extended_exception_reason"] == "ValueError: synthetic cache failure"
    assert output.evidence["ordinary_exception_reason"] == ""
    assert output.evidence["extended_assessment_state"] == "unresolved"
    assert np.isnan(output.evidence["extended_br_bpm"])
    assert output.evidence["hr_veto_reason"] == "extended_analysis_failed"
    np.testing.assert_array_equal(output.evidence["phase_raw"], ordinary["phase_raw"])
    np.testing.assert_array_equal(output.evidence["heart_spectrum"], ordinary["hr_result"]["spectrum"])
    assert output.evidence["extended_phase"].size == 0


def test_assessment_exception_preserves_reconstructed_phase_and_no_derived_decision(
    monkeypatch, tmp_path
):
    ordinary = _ordinary_dsp_600(respiration_hz=0.2)
    monkeypatch.setattr(runtime_module, "run_window_dsp", lambda *_args: ordinary)

    def fail_assessment(*_args, **_kwargs):
        raise RuntimeError("synthetic assessment failure")

    monkeypatch.setattr(breathing_module, "assess_breathing", fail_assessment)
    job = _extended_job(_breathing_tone(12.0))
    output = production_analysis_function(_cfg(), _breathing_settings())(job)

    assert output.result.status == "failed"
    assert output.result.error == "RuntimeError: synthetic assessment failure"
    assert output.evidence["extended_phase_reconstructed"] is True
    assert output.evidence["extended_phase_validated"] is True
    assert output.evidence["extended_assessment_computed"] is False
    assert output.evidence["extended_coupling_computed"] is False
    assert output.evidence["extended_analysis_computed"] is False
    assert output.evidence["extended_failure_component"] == "breathing_assessment"
    assert output.evidence["extended_exception_reason"] == output.result.error
    np.testing.assert_array_equal(
        output.evidence["extended_phase"],
        reconstruct_phase(job.range_cache_snapshot, 1),
    )
    assert output.evidence["extended_respiratory_projection"].size == 0
    assert output.evidence["extended_assessment_state"] == "unresolved"
    assert np.isnan(output.evidence["extended_br_bpm"])
    assert output.evidence["hr_veto_reason"] == "extended_analysis_failed"

    snapshot = _extended_snapshot(reason=output.result.error)
    writer = AttemptEvidenceWriter(tmp_path, "a" * 64, "b" * 64, "c" * 64)
    _write_indexed_final_selection(writer)
    writer.write_attempt(
        AttemptDisposition(
            output.result, "failed", output.result.error,
            selection_revision=2, snapshot=snapshot,
        ),
        snapshot,
        output.evidence,
    )
    _write_breathing_metadata(tmp_path)
    assert verify_development_evidence(tmp_path) == []


def test_hr_coupling_exception_preserves_complete_assessment_but_publishes_nothing(
    monkeypatch, tmp_path
):
    ordinary = _ordinary_dsp_600(respiration_hz=0.2)
    monkeypatch.setattr(runtime_module, "run_window_dsp", lambda *_args: ordinary)

    def fail_coupling(*_args, **_kwargs):
        raise RuntimeError("synthetic coupling failure")

    monkeypatch.setattr(breathing_module, "hr_veto_decision", fail_coupling)
    job = _extended_job(_breathing_tone(12.0))
    expected = breathing_module.assess_breathing(
        reconstruct_phase(job.range_cache_snapshot, 1),
        20.0,
        _breathing_thresholds(),
        eligible=True,
    )
    output = production_analysis_function(_cfg(), _breathing_settings())(job)

    assert output.result.status == "failed"
    assert output.result.error == "RuntimeError: synthetic coupling failure"
    assert output.evidence["extended_phase_reconstructed"] is True
    assert output.evidence["extended_phase_validated"] is True
    assert output.evidence["extended_assessment_computed"] is True
    assert output.evidence["extended_coupling_computed"] is False
    assert output.evidence["extended_analysis_computed"] is False
    assert output.evidence["extended_failure_component"] == "hr_coupling"
    assert output.evidence["extended_exception_reason"] == output.result.error
    assert output.evidence["extended_assessment_state"] == expected.state == "positive"
    assert output.evidence["extended_assessment_value_bpm"] == pytest.approx(
        expected.value_bpm
    )
    for name, value in expected.evidence.items():
        actual = np.asarray(output.evidence[f"extended_{name}"])
        reference = np.asarray(value)
        if actual.dtype.kind in "fc":
            np.testing.assert_allclose(actual, reference, rtol=1e-12, atol=1e-12)
        else:
            np.testing.assert_array_equal(actual, reference)
    assert output.evidence["extended_br_state"] == "unresolved"
    assert np.isnan(output.evidence["extended_br_bpm"])
    assert output.evidence["ahet_respiration_input_hz"] == pytest.approx(0.2)
    assert output.evidence["ahet_respiration_input_bpm"] == pytest.approx(12.0)
    assert np.isnan(output.evidence["extended_ahet_difference_bpm"])
    assert output.evidence["hr_veto_reason"] == "extended_analysis_failed"

    snapshot = _extended_snapshot(reason=output.result.error)
    writer = AttemptEvidenceWriter(tmp_path, "a" * 64, "b" * 64, "c" * 64)
    _write_indexed_final_selection(writer)
    writer.write_attempt(
        AttemptDisposition(
            output.result, "failed", output.result.error,
            selection_revision=2, snapshot=snapshot,
        ),
        snapshot,
        output.evidence,
    )
    _write_breathing_metadata(tmp_path)
    assert verify_development_evidence(tmp_path) == []


def test_wrong_length_reconstructed_phase_is_preserved_as_validation_failure(
    monkeypatch, tmp_path
):
    ordinary = _ordinary_dsp_600(respiration_hz=0.2)
    partial_phase = np.linspace(0.0, 1.0, 1199, dtype=np.float64)
    monkeypatch.setattr(runtime_module, "run_window_dsp", lambda *_args: ordinary)
    monkeypatch.setattr(
        runtime_module, "reconstruct_phase", lambda *_args: partial_phase.copy()
    )

    output = production_analysis_function(_cfg(), _breathing_settings())(
        _extended_job(_breathing_tone(12.0))
    )

    assert output.result.status == "failed"
    assert "1200 finite frames" in output.result.error
    assert output.evidence["extended_phase_reconstructed"] is True
    assert output.evidence["extended_phase_validated"] is False
    assert output.evidence["extended_assessment_computed"] is False
    assert output.evidence["extended_coupling_computed"] is False
    assert output.evidence["extended_failure_component"] == "phase_validation"
    np.testing.assert_array_equal(output.evidence["extended_phase"], partial_phase)
    assert output.evidence["extended_respiratory_projection"].size == 0

    snapshot = _extended_snapshot(reason=output.result.error)
    writer = AttemptEvidenceWriter(tmp_path, "a" * 64, "b" * 64, "c" * 64)
    _write_indexed_final_selection(writer)
    writer.write_attempt(
        AttemptDisposition(
            output.result, "failed", output.result.error,
            selection_revision=2, snapshot=snapshot,
        ),
        snapshot,
        output.evidence,
    )
    _write_breathing_metadata(tmp_path)
    assert verify_development_evidence(tmp_path) == []

    _rewrite_only_attempt_and_rehash(
        tmp_path,
        lambda payload: payload.__setitem__(
            "extended_assessment_computed", np.asarray(True, dtype=np.bool_)
        ),
    )
    assert verify_development_evidence(tmp_path)


def test_actual_decoder_cache_publishes_quiet_assessment_and_holds_prior_values_red(
    monkeypatch, tmp_path
):
    stops = (260, 460, *range(660, 1261, 60))
    gates = {stop: threading.Event() for stop in stops}
    source = _DecodedSource(
        _stationary_frame_bytes(1260), tmp_path / "adc_stream.bin", gates=gates
    )
    settings = _breathing_settings()
    ordinary_analysis = _DeterministicAnalysis(gates)
    production = production_analysis_function(_cfg(), settings)
    jobs = []

    monkeypatch.setattr(runtime_module, "run_window_dsp", lambda *_args: None)

    def analyze(job):
        jobs.append(job)
        if job.stage != "extended_60":
            return ordinary_analysis(job)
        output = production(job)
        gates[job.frame_stop].set()
        return output

    runtime = _runtime(
        tmp_path, source, analyze, settings=settings, cfg=_cfg()
    )
    _finish(runtime)

    extended_jobs = [job for job in jobs if job.stage == "extended_60"]
    assert len(extended_jobs) == 1
    extended = extended_jobs[0]
    assert (extended.frame_start, extended.frame_stop) == (60, 1260)
    assert extended.raw_frames.shape == (600, 1, 1, 8)
    assert extended.range_cache_snapshot.samples.shape == (1200, 1, 1, 2)
    assert extended.requested_bin == runtime.latest_snapshot.locked_bin == 1

    snapshot = runtime.latest_snapshot
    assert snapshot.hr.value_bpm == pytest.approx(70.0)
    assert snapshot.hr.state == "held"
    assert snapshot.hr.color == "red"
    assert snapshot.br.value_bpm == pytest.approx(12.0)
    assert snapshot.br.state == "held"
    assert snapshot.br.color == "red"
    assert snapshot.breathing_status == "No breathing motion detected"
    assert snapshot.reason == "quiet_breathing"
    assert snapshot.epoch == 0

    with (tmp_path / "analysis_attempts.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    extended_rows = [row for row in rows if row["stage"] == "extended_60"]
    assert len(extended_rows) == 1
    row = extended_rows[0]
    assert row["disposition"] == "published"
    assert row["fresh_hr_bpm"] == row["fresh_br_bpm"] == ""
    assert row["held_hr_bpm"] == "70"
    assert row["held_br_bpm"] == "12"
    assert row["quiet_assessment_accepted"] == "1"
    with np.load(tmp_path / row["npz_file"], allow_pickle=False) as payload:
        assert payload["extended_analysis_computed"].item() is True
        assert payload["ordinary_exception_reason"].item() == "ordinary_dsp_returned_no_result"
        assert payload["extended_assessment_state"].item() == "quiet"
        assert np.isnan(payload["fresh_br_bpm"].item())
        assert payload["extended_phase"].shape == (1200,)

    _write_breathing_metadata(tmp_path)
    assert verify_development_evidence(tmp_path) == []

    # The raw mirror remains one continuous acquisition through the quiet branch.
    assert source.start_count == source.stop_count == 1
    assert (tmp_path / "adc_stream.bin").read_bytes() == b"".join(
        _stationary_frame_bytes(1260)
    )


def test_movement_only_runtime_keeps_600_frame_rolling_contract_after_60_seconds(
    tmp_path
):
    stops = (260, 460, *range(660, 1261, 60))
    gates = {stop: threading.Event() for stop in stops}
    source = _DecodedSource(
        _stationary_frame_bytes(1260), tmp_path / "adc_stream.bin", gates=gates
    )
    analysis = _DeterministicAnalysis(gates)
    runtime = _runtime(
        tmp_path,
        source,
        analysis,
        settings=_movement_only_settings(),
        cfg=_cfg(),
    )

    _finish(runtime)

    assert not any(job.stage == "extended_60" for job in analysis.jobs)
    rolling = [job for job in analysis.jobs if job.stage == "rolling"]
    assert rolling
    assert rolling[-1].frame_stop == 1260
    assert all(job.frame_stop - job.frame_start == 600 for job in rolling)
    assert all(job.raw_frames.shape == (600, 1, 1, 8) for job in rolling)
    assert all(job.range_cache_snapshot is None for job in rolling)
    assert verify_development_evidence(tmp_path) == []

    with (tmp_path / "analysis_attempts.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    last_path = tmp_path / rows[-1]["npz_file"]
    with np.load(last_path, allow_pickle=False) as payload:
        assert not (EXTENDED_ANALYSIS_FIELDS & set(payload.files))


def test_physical_event_at_1200_frame_boundary_cancels_extended_assessment(
    tmp_path
):
    stops = (260, 460, *range(660, 1201, 60))
    gates = {stop: threading.Event() for stop in stops}
    stationary = _stationary_frame_bytes(1255)
    moving = [
        _impulse_frame_bytes(16 + 4j if index % 2 == 0 else -16 - 4j)
        for index in range(5)
    ]
    source = _DecodedSource(
        stationary + moving, tmp_path / "adc_stream.bin", gates=gates
    )
    analysis = _DeterministicAnalysis(gates)
    runtime = _runtime(
        tmp_path, source, analysis, settings=_breathing_settings(), cfg=_cfg()
    )

    _finish(runtime)

    assert not any(job.stage == "extended_60" for job in analysis.jobs)
    with (tmp_path / "controller_events.csv").open(newline="", encoding="utf-8") as handle:
        events = list(csv.DictReader(handle))
    boundary_events = [
        row for row in events
        if row["kind"] == "movement" and row["reason"] == "physical_movement"
    ]
    assert len(boundary_events) == 1
    assert boundary_events[0]["frame_index"] == "1259"
    assert runtime.latest_snapshot.epoch == 1
    assert runtime.latest_snapshot.hr.state == "held"
    assert runtime.latest_snapshot.br.state == "held"
    assert source.start_count == source.stop_count == 1


def _extended_snapshot(*, reason: str) -> DisplaySnapshot:
    return DisplaySnapshot(
        frame_index=1259,
        epoch=0,
        status="active",
        reason=reason,
        hr=DisplayValue(70.0, "held", "red"),
        br=DisplayValue(12.0, "held", "red"),
        breathing_status=(
            "No breathing motion detected" if reason == "quiet_breathing" else ""
        ),
        locked_bin=1,
        selection_revision=2,
    )


def _write_breathing_metadata(root: Path) -> None:
    calibration_path = root / "calibration.snapshot.json"
    calibration_path.write_text(
        json.dumps({"thresholds": {"breathing": _breathing_thresholds()}}),
        encoding="utf-8",
    )
    (root / "run_metadata.json").write_text(
        json.dumps({"calibration_snapshot": calibration_path.name}),
        encoding="utf-8",
    )


def _write_indexed_final_selection(writer: AttemptEvidenceWriter) -> None:
    job = AnalysisJob(
        job_id="job-final-selection",
        epoch=0,
        selection_revision=1,
        stage="ordinary_30",
        frame_start=60,
        frame_stop=660,
        requested_bin=None,
        selection_mode="select",
        raw_frames=np.zeros((600, 1, 1, 8), dtype=np.complex64),
    )
    selection_evidence = {
        "selected_bin": 1,
        "fallback_used": False,
        "selection_reason": "eligible_dsp_pass",
        "candidates": [{"bin": 1, "dsp_passed": True}],
    }
    result = AnalysisResult(
        job_id=job.job_id,
        epoch=0,
        selection_revision=1,
        stage="ordinary_30",
        frame_start=60,
        frame_stop=660,
        actual_bin=1,
        executed=True,
        status="completed",
        dsp={"hr_valid": True, "br_valid": True, "hr_raw": 70.0, "br_bpm": 12.0},
        selection_decision=SelectionDecision(
            1, selection_evidence, True, True, False, "eligible_dsp_pass"
        ),
    )
    snapshot = DisplaySnapshot(
        frame_index=659,
        epoch=0,
        status="active",
        reason="estimate_accepted",
        hr=DisplayValue(70.0, "fresh", "green"),
        br=DisplayValue(12.0, "fresh", "green"),
        breathing_status="",
        locked_bin=1,
        selection_revision=2,
    )
    writer.write_event(ControllerEvent(
        "final_bin_selected", "eligible_dsp_pass", 659, 0,
        job.job_id, 1, 2, 660,
    ))
    writer.write_attempt(
        AttemptDisposition(
            result,
            "published",
            "estimate_accepted",
            hr_accepted=True,
            br_accepted=True,
            selection_applied=True,
            selection_revision=2,
            snapshot=snapshot,
        ),
        snapshot,
        _evidence(job),
    )


def _rewrite_only_attempt_and_rehash(root: Path, mutate) -> None:
    index_path = root / "analysis_attempts.csv"
    with index_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames, rows = reader.fieldnames, list(reader)
    assert fieldnames is not None
    targets = [row for row in rows if row["stage"] == "extended_60"]
    assert len(targets) == 1
    target = targets[0]
    payload_path = root / target["npz_file"]
    with np.load(payload_path, allow_pickle=False) as archive:
        payload = {name: np.array(archive[name], copy=True) for name in archive.files}
    mutate(payload)
    with payload_path.open("wb") as handle:
        np.savez(handle, **payload)
    target["npz_sha256"] = hashlib.sha256(payload_path.read_bytes()).hexdigest()
    with index_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_authentic_quiet_evidence(monkeypatch, root: Path) -> None:
    monkeypatch.setattr(runtime_module, "run_window_dsp", lambda *_args: None)
    output = production_analysis_function(_cfg(), _breathing_settings())(
        _extended_job(np.zeros(1200, dtype=np.float64))
    )
    snapshot = _extended_snapshot(reason="quiet_breathing")
    writer = AttemptEvidenceWriter(root, "a" * 64, "b" * 64, "c" * 64)
    _write_indexed_final_selection(writer)
    writer.write_attempt(
        AttemptDisposition(
            output.result,
            "published",
            "quiet_breathing",
            quiet_assessment_accepted=True,
            selection_revision=2,
            snapshot=snapshot,
        ),
        snapshot,
        output.evidence,
    )
    _write_breathing_metadata(root)
    assert verify_development_evidence(root) == []


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (
            lambda payload: payload["extended_respiratory_projection"].__setitem__(
                0, payload["extended_respiratory_projection"][0] + 0.25
            ),
            "extended_numerical_mismatch",
        ),
        (
            lambda payload: payload.__setitem__("hr_veto_reason", np.asarray("")),
            "extended_decision_mismatch",
        ),
        (
            lambda payload: payload.pop("extended_phase"),
            "npz_missing_fields",
        ),
        (
            lambda payload: payload.__setitem__(
                "extended_threshold_quiet_resp_rms_max", np.asarray(0.25)
            ),
            "extended_numerical_mismatch",
        ),
        (
            lambda payload: payload.__setitem__(
                "ahet_respiration_input_hz", np.asarray(0.2)
            ),
            "ahet_respiration_input_mismatch",
        ),
    ],
    ids=["projection", "hr-veto", "missing-phase", "threshold", "ahet-input"],
)
def test_extended_evidence_verifier_recomputes_after_hash_consistent_tampering(
    monkeypatch, tmp_path, mutate, expected_code
):
    monkeypatch.setattr(runtime_module, "run_window_dsp", lambda *_args: None)
    output = production_analysis_function(_cfg(), _breathing_settings())(
        _extended_job(np.zeros(1200, dtype=np.float64))
    )
    snapshot = _extended_snapshot(reason="quiet_breathing")
    attempt = AttemptDisposition(
        output.result,
        "published",
        "quiet_breathing",
        quiet_assessment_accepted=True,
        selection_revision=2,
        snapshot=snapshot,
    )
    writer = AttemptEvidenceWriter(
        tmp_path, "a" * 64, "b" * 64, "c" * 64
    )
    _write_indexed_final_selection(writer)
    record = writer.write_attempt(attempt, snapshot, output.evidence)
    _write_breathing_metadata(tmp_path)

    assert record.npz_path.is_file()
    assert verify_development_evidence(tmp_path) == []

    _rewrite_only_attempt_and_rehash(tmp_path, mutate)
    assert expected_code in {
        issue.code for issue in verify_development_evidence(tmp_path)
    }


def test_authentic_extended_failure_evidence_verifies_and_forged_computed_flag_fails(
    monkeypatch, tmp_path
):
    ordinary = _ordinary_dsp_600(respiration_hz=0.2)
    monkeypatch.setattr(runtime_module, "run_window_dsp", lambda *_args: ordinary)
    monkeypatch.setattr(
        runtime_module,
        "reconstruct_phase",
        lambda *_args: (_ for _ in ()).throw(ValueError("synthetic cache failure")),
    )
    output = production_analysis_function(_cfg(), _breathing_settings())(
        _extended_job(_breathing_tone(12.0))
    )
    snapshot = _extended_snapshot(reason="ValueError: synthetic cache failure")
    attempt = AttemptDisposition(
        output.result,
        "failed",
        output.result.error,
        selection_revision=2,
        snapshot=snapshot,
    )
    writer = AttemptEvidenceWriter(
        tmp_path, "a" * 64, "b" * 64, "c" * 64
    )
    _write_indexed_final_selection(writer)
    writer.write_attempt(attempt, snapshot, output.evidence)
    _write_breathing_metadata(tmp_path)

    assert verify_development_evidence(tmp_path) == []

    def forge_computed(payload):
        payload["extended_analysis_computed"] = np.asarray(True, dtype=np.bool_)
        payload["extended_assessment_state"] = np.asarray("quiet")

    _rewrite_only_attempt_and_rehash(tmp_path, forge_computed)
    codes = {issue.code for issue in verify_development_evidence(tmp_path)}
    assert {
        "inconsistent_extended_component_flags",
        "extended_failure_component_mismatch",
        "invalid_failed_extended_assessment",
    } & codes


def test_quiet_acceptance_cannot_be_forged_to_an_invalid_disposition(monkeypatch, tmp_path):
    _write_authentic_quiet_evidence(monkeypatch, tmp_path)
    index_path = tmp_path / "analysis_attempts.csv"
    with index_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames, rows = reader.fieldnames, list(reader)
    extended_row = next(row for row in rows if row["stage"] == "extended_60")
    payload_path = tmp_path / extended_row["npz_file"]
    with np.load(payload_path, allow_pickle=False) as archive:
        payload = {name: np.array(archive[name], copy=True) for name in archive.files}
    payload["quiet_assessment_accepted"] = np.asarray(False, dtype=np.bool_)
    payload["disposition"] = np.asarray("invalid")
    with payload_path.open("wb") as handle:
        np.savez(handle, **payload)
    extended_row["quiet_assessment_accepted"] = "0"
    extended_row["disposition"] = "invalid"
    extended_row["npz_sha256"] = hashlib.sha256(payload_path.read_bytes()).hexdigest()
    with index_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    assert "quiet_assessment_not_published" in {
        issue.code for issue in verify_development_evidence(tmp_path)
    }


@pytest.mark.parametrize("event_mutation", ["delete", "wrong_bin", "wrong_revision"])
def test_extended_attempt_requires_exact_indexed_final_selection_event(
    monkeypatch, tmp_path, event_mutation
):
    _write_authentic_quiet_evidence(monkeypatch, tmp_path)
    events_path = tmp_path / "controller_events.csv"
    with events_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames, rows = reader.fieldnames, list(reader)
    target = next(row for row in rows if row["kind"] == "final_bin_selected")
    if event_mutation == "delete":
        rows.remove(target)
    elif event_mutation == "wrong_bin":
        target["selected_bin"] = "2"
    else:
        target["selection_revision"] = "3"
    with events_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    codes = {issue.code for issue in verify_development_evidence(tmp_path)}
    assert {
        "selection_event_cardinality",
        "selection_event_mismatch",
        "final_selection_event_cardinality",
    } & codes


def test_nonselection_ordinary_none_has_explicit_failure_without_selector_recovery(monkeypatch):
    job = AnalysisJob(
        job_id="job-ordinary-none",
        epoch=3,
        selection_revision=4,
        stage="rolling",
        frame_start=120,
        frame_stop=720,
        requested_bin=1,
        selection_mode="committed",
        raw_frames=np.zeros((600, 1, 1, 8), dtype=np.complex64),
    )
    monkeypatch.setattr(runtime_module, "run_window_dsp", lambda *_args: None)

    output = production_analysis_function(_cfg(), _breathing_settings())(job)

    assert output.result.status == "completed"
    assert output.result.dsp is None
    assert output.result.selection_decision is None
    assert output.result.actual_bin == 1
    assert output.evidence["rejection_reasons"].tolist() == [
        "ordinary_dsp_returned_no_result"
    ]
    assert output.evidence["exceptional_evidence"] is True
