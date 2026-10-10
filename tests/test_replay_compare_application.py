"""End-to-end baseline application composition and reference isolation."""

from __future__ import annotations

import csv
import hashlib
import json
import queue
import shutil
import threading
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from src.reference_access import LoadedReference, ReferenceSource
from src.replay_compare.application import create_replay_application
from src.replay_compare.clock import PlaybackClock
from src.replay_compare.engine import baseline_analysis_function
from src.replay_compare.evidence import verify_replay_artifacts
from src.replay_compare.reference import PassiveReferenceTimeline
from src.replay_compare.session import (
    LEGACY_VALIDITY_ID,
    SOURCE_KIND,
    SUPPORTED_CAPTURE_ID,
    ReplaySessionSpec,
)
from src.replay_compare.source import ProductionFrameDecoder, SourceIntegrity
from src.replay_compare.verification import verify_replay_with_source
from src.vitals import remove_impulse_noise


CONFIG = yaml.safe_load(
    (Path(__file__).resolve().parents[1] / "scripts" / "live_demo_calibrated_config.yaml")
    .read_text(encoding="utf-8")
)
CONFIG["profile"].update(
    {
        "num_adc_samples": 8,
        "num_rx": 1,
        "num_chirps_per_frame": 1,
        "range_resolution_m": 0.1,
        "range_bias_m": 0.0,
        "iq_swap": True,
    }
)
CONFIG["phase"]["clutter_removal"] = "none"
CONFIG["protocol"]["subject_distance_m"] = [0.1, 0.1]
CONFIG["bin_selection"]["candidate_bins"] = [1]


def _canonical_bytes(value) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _spec(tmp_path: Path) -> ReplaySessionSpec:
    config_bytes = _canonical_bytes(CONFIG)
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
        original_config_sha256=hashlib.sha256(config_bytes).hexdigest(),
        effective_config_sha256=hashlib.sha256(config_bytes).hexdigest(),
        _original_config_json=config_bytes,
        _effective_config_json=config_bytes,
        frame_count=601,
        frame_rate_hz=20.0,
        frame_bytes=32,
        num_adc_samples=8,
        num_rx=1,
        num_chirps_per_frame=1,
        iq_swap=True,
        range_resolution_m=0.1,
        range_bias_m=0.0,
        anchor_utc_us=1_000_000,
        time_origin_id="start_wall_utc_fallback_v1",
        time_origin_approximate=True,
        validity_provenance=LEGACY_VALIDITY_ID,
        validity_assumed=True,
        source_kind=SOURCE_KIND,
        _metadata_json=b"{}",
    )


def _frames() -> np.ndarray:
    count = 601
    time_s = np.arange(count, dtype=np.float64) / 20.0
    rng = np.random.default_rng(123)
    phase = (
        3.00 * np.sin(2 * np.pi * 0.25 * time_s)
        + 1.50 * np.sin(2 * np.pi * 0.50 * time_s)
        + 1.00 * np.sin(2 * np.pi * 0.75 * time_s)
        + 0.75 * np.sin(2 * np.pi * 1.00 * time_s)
        + 0.30 * np.sin(2 * np.pi * (71.0 / 60.0) * time_s)
        + 0.15 * np.sin(2 * np.pi * (142.0 / 60.0) * time_s)
        + 0.03 * rng.standard_normal(count)
    )
    carrier = np.exp(2j * np.pi * np.arange(8, dtype=np.float64) / 8.0)
    return (
        np.exp(1j * phase)[:, None, None, None]
        * carrier[None, None, None, :]
    ).astype(np.complex64)


class _ArraySource:
    initial_sha256 = "a" * 64
    opened_file_identity = {"size": 601 * 32, "device": 1, "inode": 2}

    def __init__(
        self,
        frames: np.ndarray,
        *,
        wait_before_index: int | None = None,
        wait_event: threading.Event | None = None,
        source_sha256: str = "a" * 64,
    ):
        self.frames = frames
        self.frame_count = int(frames.shape[0])
        self.cursor = 0
        self.closed = False
        self.wait_before_index = wait_before_index
        self.wait_event = wait_event
        self.initial_sha256 = source_sha256
        self.integrity = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.closed = True
        self.integrity = SourceIntegrity(
            initial_sha256=self.initial_sha256,
            final_sha256=self.initial_sha256,
            streamed_sha256=self.initial_sha256,
            complete=(self.cursor == self.frame_count),
            frames_read=self.cursor,
        )

    def read_next(self):
        if self.cursor >= self.frame_count:
            return None
        index = self.cursor
        if index == self.wait_before_index:
            if self.wait_event is None or not self.wait_event.wait(timeout=3.0):
                raise TimeoutError("test analysis did not finish before source release")
        self.cursor += 1
        return index, np.array(self.frames[index], copy=True)


class _ResultSignalQueue(queue.Queue):
    def __init__(self, queued: threading.Event):
        super().__init__(maxsize=1)
        self.queued = queued

    def put(self, item, *args, **kwargs):
        super().put(item, *args, **kwargs)
        self.queued.set()


class _RecordingAnalysis:
    def __init__(self):
        self._production = baseline_analysis_function(CONFIG)
        self.requests = []
        self.results = []
        self.completed = threading.Event()

    def __call__(self, job):
        self.requests.append(
            (
                job.job_id,
                job.epoch,
                job.selection_revision,
                job.stage,
                job.frame_start,
                job.frame_stop,
                job.requested_bin,
                job.selection_mode,
                hashlib.sha256(job.raw_frames.tobytes()).hexdigest(),
            )
        )
        executed = self._production(job)
        self.results.append(executed)
        self.completed.set()
        return executed


def _timeline(rows) -> PassiveReferenceTimeline:
    frame = pd.DataFrame(rows, columns=["epoch_utc", "pr_bpm", "pi", "rr_bpm"])
    loaded = LoadedReference(
        frame=frame,
        source=ReferenceSource(
            source_kind="development_registry",
            capture_id="synthetic",
            subject_id="A",
            resolved_path="synthetic.csv",
            sha256="d" * 64,
        ),
    )
    return PassiveReferenceTimeline(
        loaded, anchor_utc_us=1_000_000, frame_rate_hz=20.0
    )


def _fast_clock(frame_rate_hz: float, speed: float) -> PlaybackClock:
    return PlaybackClock(
        frame_rate_hz,
        speed,
        monotonic=lambda: 0.0,
        wait=lambda _seconds: None,
    )


def _run_application(tmp_path, label, reference):
    frames = _frames()
    analyzer = _RecordingAnalysis()
    sources = []
    result_queued = threading.Event()

    def source_factory():
        source = _ArraySource(
            frames, wait_before_index=600, wait_event=result_queued
        )
        sources.append(source)
        return source

    manager = create_replay_application(
        _spec(tmp_path),
        output_root=tmp_path / label,
        reference_timeline=reference,
        frame_limit=601,
        initial_speed=4.0,
        source_factory=source_factory,
        analysis_function=analyzer,
        clock_factory=_fast_clock,
        shutdown_timeout_s=2.0,
    )
    manager.current_run._results = _ResultSignalQueue(result_queued)
    manager.start()
    assert manager.current_run._ended.wait(timeout=5.0)
    manager.stop(timeout_s=3.0)
    assert manager.failure is None
    assert manager.clean_shutdown is True
    assert sources and sources[0].closed is True
    return manager, analyzer


def _indexed_payload(pass_dir: Path):
    with (pass_dir / "analysis_attempts.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    with np.load(pass_dir / rows[0]["npz_file"], allow_pickle=False) as archive:
        payload = {name: np.array(archive[name], copy=True) for name in archive.files}
    return rows[0], payload


def _encode_sampleswap_true(frames: np.ndarray) -> bytes:
    payload = bytearray()
    for frame in frames:
        samples = np.asarray(frame).reshape(-1)
        real = np.rint(samples.real * 1000.0).astype("<i2")
        imag = np.rint(samples.imag * 1000.0).astype("<i2")
        words = np.empty((samples.size // 2, 4), dtype="<i2")
        words[:, 0] = imag[0::2]
        words[:, 1] = imag[1::2]
        words[:, 2] = real[0::2]
        words[:, 3] = real[1::2]
        payload.extend(words.tobytes())
    return bytes(payload)


def _registered_spec(tmp_path):
    adc_bytes = _encode_sampleswap_true(_frames())
    adc_path = tmp_path / "registered" / "adc_stream.bin"
    adc_path.parent.mkdir(parents=True, exist_ok=True)
    adc_path.write_bytes(adc_bytes)
    return replace(
        _spec(tmp_path),
        capture_dir=adc_path.parent,
        adc_path=adc_path,
        registered_adc_sha256=hashlib.sha256(adc_bytes).hexdigest(),
    )


def _run_registered_source_application(tmp_path, reference=None):
    spec = _registered_spec(tmp_path)
    if reference is None:
        reference = _timeline([])
    adc_bytes = spec.adc_path.read_bytes()
    decoder = ProductionFrameDecoder(CONFIG["profile"])
    decoded_frames = np.stack(
        [
            decoder.decode(adc_bytes[offset: offset + spec.frame_bytes])
            for offset in range(0, len(adc_bytes), spec.frame_bytes)
        ]
    )
    analyzer = _RecordingAnalysis()
    result_queued = threading.Event()

    def source_factory():
        source = _ArraySource(
            decoded_frames,
            wait_before_index=600,
            wait_event=result_queued,
            source_sha256=spec.registered_adc_sha256,
        )
        stat = spec.adc_path.stat()
        source.opened_file_identity = {
            name: int(getattr(stat, name))
            for name in ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        }
        return source

    manager = create_replay_application(
        spec,
        output_root=tmp_path / "source-bound-run",
        reference_timeline=reference,
        frame_limit=601,
        initial_speed=4.0,
        source_factory=source_factory,
        analysis_function=analyzer,
        clock_factory=_fast_clock,
        shutdown_timeout_s=2.0,
    )
    manager.current_run._results = _ResultSignalQueue(result_queued)
    manager.start()
    assert manager.current_run._ended.wait(timeout=5.0)
    manager.stop(timeout_s=3.0)
    assert manager.failure is None
    assert manager.clean_shutdown is True
    return manager, spec, reference


def _rehash_forged_attempt(pass_dir: Path, mutate) -> None:
    index_path = pass_dir / "analysis_attempts.csv"
    with index_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        rows = list(reader)
    assert fieldnames is not None and len(rows) == 1
    attempt_path = pass_dir / rows[0]["npz_file"]
    with np.load(attempt_path, allow_pickle=False) as archive:
        payload = {name: np.array(archive[name], copy=True) for name in archive.files}
    mutate(payload)
    with attempt_path.open("wb") as handle:
        np.savez(handle, **payload)
    rows[0]["npz_sha256"] = hashlib.sha256(attempt_path.read_bytes()).hexdigest()
    with index_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    manifest_path = pass_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][rows[0]["npz_file"]] = rows[0]["npz_sha256"]
    manifest["files"]["analysis_attempts.csv"] = hashlib.sha256(
        index_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _rehash_json_artifact(pass_dir: Path, name: str) -> None:
    artifact_path = pass_dir / name
    manifest_path = pass_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][name] = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _assert_baseline_pass_is_accepted(manager, spec, reference) -> None:
    report = verify_replay_with_source(
        manager.pass_dir, spec, reference_timeline=reference
    )
    assert report.internal_issues == ()
    assert report.source_issues == ()
    assert report.source_binding_complete is True
    assert report.accepted is True


def test_reference_changes_cannot_change_radar_execution_or_coverage(tmp_path):
    dense = _timeline(
        [(second, 60.0 + second, 2.0, 10.0 + second) for second in range(1, 32)]
    )
    sparse_low_quality = _timeline(
        [
            (1, 240.0, 0.01, 70.0),
            (9, 0.0, 9.0, 0.0),
            (29, 35.0, np.nan, 4.0),
        ]
    )

    dense_app, dense_analysis = _run_application(tmp_path, "dense", dense)
    sparse_app, sparse_analysis = _run_application(
        tmp_path, "sparse", sparse_low_quality
    )

    assert dense_analysis.requests == sparse_analysis.requests
    assert len(dense_analysis.results) == len(sparse_analysis.results) == 1
    dense_result = dense_analysis.results[0].result
    sparse_result = sparse_analysis.results[0].result
    assert (
        dense_result.job_id,
        dense_result.epoch,
        dense_result.selection_revision,
        dense_result.stage,
        dense_result.frame_start,
        dense_result.frame_stop,
        dense_result.actual_bin,
        dense_result.executed,
        dense_result.status,
        dense_result.error,
    ) == (
        sparse_result.job_id,
        sparse_result.epoch,
        sparse_result.selection_revision,
        sparse_result.stage,
        sparse_result.frame_start,
        sparse_result.frame_stop,
        sparse_result.actual_bin,
        sparse_result.executed,
        sparse_result.status,
        sparse_result.error,
    )
    assert dense_result.dsp is not None and sparse_result.dsp is not None
    for field in ("hr_valid", "br_valid", "hr_raw", "br_bpm", "rej_reason"):
        assert dense_result.dsp.get(field) == sparse_result.dsp.get(field)
    assert dense_result.selection_decision is not None
    assert sparse_result.selection_decision is not None
    assert (
        dense_result.selection_decision.selected_bin,
        dense_result.selection_decision.selector_succeeded,
        dense_result.selection_decision.dsp_succeeded,
        dense_result.selection_decision.fallback_used,
        dense_result.selection_decision.reason,
    ) == (
        sparse_result.selection_decision.selected_bin,
        sparse_result.selection_decision.selector_succeeded,
        sparse_result.selection_decision.dsp_succeeded,
        sparse_result.selection_decision.fallback_used,
        sparse_result.selection_decision.reason,
    )

    dense_row, dense_payload = _indexed_payload(dense_app.pass_dir)
    sparse_row, sparse_payload = _indexed_payload(sparse_app.pass_dir)
    assert {
        key: value for key, value in dense_row.items() if key != "npz_sha256"
    } == {
        key: value for key, value in sparse_row.items() if key != "npz_sha256"
    }
    assert dense_payload.keys() == sparse_payload.keys()
    for name in dense_payload:
        if name == "selection_decision_json":
            dense_decision = json.loads(dense_payload[name].item())
            sparse_decision = json.loads(sparse_payload[name].item())
            # Selector runtime is performance telemetry. It may vary across two
            # executions, while every scientific decision field must be equal.
            dense_decision.get("evidence", {}).pop("t_warmup_scan_ms", None)
            sparse_decision.get("evidence", {}).pop("t_warmup_scan_ms", None)
            assert dense_decision == sparse_decision
            continue
        np.testing.assert_equal(dense_payload[name], sparse_payload[name])

    dense_snapshot = dense_app.latest_snapshot
    sparse_snapshot = sparse_app.latest_snapshot
    assert (
        dense_snapshot.selected_bin,
        dense_snapshot.selection_revision,
        dense_snapshot.hr,
        dense_snapshot.br,
    ) == (
        sparse_snapshot.selected_bin,
        sparse_snapshot.selection_revision,
        sparse_snapshot.hr,
        sparse_snapshot.br,
    )
    assert (
        dense_snapshot.coverage.denominator_frames,
        dense_snapshot.coverage.hr_accepted_frames,
        dense_snapshot.coverage.br_numeric_frames,
        dense_snapshot.coverage.both_frames,
        dense_snapshot.coverage.hr_state,
        dense_snapshot.coverage.br_state,
    ) == (
        sparse_snapshot.coverage.denominator_frames,
        sparse_snapshot.coverage.hr_accepted_frames,
        sparse_snapshot.coverage.br_numeric_frames,
        sparse_snapshot.coverage.both_frames,
        sparse_snapshot.coverage.hr_state,
        sparse_snapshot.coverage.br_state,
    )
    assert (
        dense_snapshot.coverage.reference_pr_frames,
        dense_snapshot.coverage.reference_pr_qualified_frames,
        dense_snapshot.coverage.reference_rr_frames,
    ) != (
        sparse_snapshot.coverage.reference_pr_frames,
        sparse_snapshot.coverage.reference_pr_qualified_frames,
        sparse_snapshot.coverage.reference_rr_frames,
    )
    dense_audit = json.loads(
        (dense_app.pass_dir / "reference_audit.json").read_text(encoding="utf-8")
    )
    sparse_audit = json.loads(
        (sparse_app.pass_dir / "reference_audit.json").read_text(encoding="utf-8")
    )
    assert dense_audit and sparse_audit
    assert dense_audit != sparse_audit
    assert verify_replay_artifacts(dense_app.pass_dir) == ()
    assert verify_replay_artifacts(sparse_app.pass_dir) == ()


def test_application_rejects_invalid_limits_before_source_or_output_pass(tmp_path):
    source_calls = []

    with pytest.raises(ValueError, match="frame_limit"):
        create_replay_application(
            _spec(tmp_path),
            output_root=tmp_path / "invalid",
            frame_limit=602,
            source_factory=lambda: source_calls.append(True),
            analysis_function=lambda job: job,
            clock_factory=_fast_clock,
        )

    assert source_calls == []
    assert not (tmp_path / "invalid" / "pass_0001").exists()


@pytest.mark.parametrize(
    ("frame_limit", "error_type"),
    [(True, TypeError), (1.5, TypeError), (0, ValueError), (602, ValueError)],
)
def test_application_rejects_nonexact_frame_limits_before_output(
    tmp_path, frame_limit, error_type
):
    output = tmp_path / f"invalid-frame-limit-{frame_limit!s}"
    source_calls = []

    with pytest.raises(error_type, match="frame_limit"):
        create_replay_application(
            _spec(tmp_path),
            output_root=output,
            frame_limit=frame_limit,
            source_factory=lambda: source_calls.append(True),
            analysis_function=lambda job: job,
            clock_factory=_fast_clock,
        )

    assert source_calls == []
    assert output.exists() is False


@pytest.mark.parametrize("generation", [True, -1, 1.5])
def test_application_rejects_nonexact_generation_before_output(
    tmp_path, generation
):
    output = tmp_path / f"invalid-generation-{generation!s}"
    source_calls = []

    with pytest.raises(ValueError, match="initial_generation"):
        create_replay_application(
            _spec(tmp_path),
            output_root=output,
            frame_limit=1,
            initial_generation=generation,
            source_factory=lambda: source_calls.append(True),
            analysis_function=lambda job: job,
            clock_factory=_fast_clock,
        )

    assert source_calls == []
    assert output.exists() is False


def test_snapshot_read_between_event_persistence_and_runtime_publication_is_coherent(
    tmp_path, request
):
    frames = _frames()
    analyzer = _RecordingAnalysis()
    manager = create_replay_application(
        _spec(tmp_path),
        output_root=tmp_path / "event-barrier",
        frame_limit=601,
        initial_speed=4.0,
        source_factory=lambda: _ArraySource(frames),
        analysis_function=analyzer,
        clock_factory=_fast_clock,
        shutdown_timeout_s=2.0,
    )
    request_persisted = threading.Event()
    release = threading.Event()
    original_write = manager.current_run.evidence.write_controller_event

    def write_with_barrier(event):
        original_write(event)
        if getattr(event, "kind", None) == "analysis_requested":
            request_persisted.set()
            if not release.wait(timeout=3.0):
                raise TimeoutError("test did not release controller event write")

    manager.current_run.evidence.write_controller_event = write_with_barrier

    def cleanup():
        release.set()
        manager.stop(timeout_s=1.0)

    request.addfinalizer(cleanup)
    manager.start()
    assert request_persisted.wait(timeout=3.0)

    # The event observer is already at boundary 600 while ReplayRuntime has not
    # published its matching frame update. The application getter must return
    # one cached immutable state instead of mixing those two ownership domains.
    during = manager.latest_snapshot
    assert during.processed_boundary == 599
    assert during.coverage.denominator_frames == 599
    assert during.selected_bin is None
    assert during.hr.state == "missing"
    assert during.br.state == "missing"

    release.set()
    assert manager.current_run._ended.wait(timeout=5.0)
    manager.stop(timeout_s=3.0)
    assert manager.failure is None
    assert manager.clean_shutdown is True


def test_attempt_persistence_failure_cannot_create_phantom_display_lease(
    tmp_path, request
):
    frames = _frames()
    manager = create_replay_application(
        _spec(tmp_path),
        output_root=tmp_path / "attempt-failure",
        frame_limit=601,
        initial_speed=4.0,
        source_factory=lambda: _ArraySource(frames),
        analysis_function=baseline_analysis_function(CONFIG),
        clock_factory=_fast_clock,
        shutdown_timeout_s=2.0,
    )
    attempt_write_entered = threading.Event()
    release = threading.Event()

    def fail_attempt_write(*_args, **_kwargs):
        attempt_write_entered.set()
        if not release.wait(timeout=3.0):
            raise TimeoutError("test did not release attempt evidence write")
        raise OSError("injected application attempt persistence failure")

    manager.current_run.evidence.writer.write_attempt = fail_attempt_write

    def cleanup():
        release.set()
        manager.stop(timeout_s=1.0)

    request.addfinalizer(cleanup)
    manager.start()
    assert attempt_write_entered.wait(timeout=5.0)

    during = manager.latest_snapshot
    assert during.hr.state == "missing"
    assert during.br.state == "missing"
    assert during.hr.value_bpm is None
    assert during.br.value_bpm is None
    assert during.coverage.hr_accepted_frames == 0
    assert during.coverage.br_numeric_frames == 0

    release.set()
    assert manager.current_run._ended.wait(timeout=5.0)
    manager.stop(timeout_s=3.0)
    assert isinstance(manager.failure, OSError)
    after = manager.latest_snapshot
    assert after.hr.state in {"missing", "held"}
    assert after.br.state in {"missing", "held"}
    assert after.hr.value_bpm is None
    assert after.br.value_bpm is None
    assert after.coverage.hr_accepted_frames == 0
    assert after.coverage.br_numeric_frames == 0


def test_final_verifier_binds_saved_phase_to_registered_adc(tmp_path):
    manager, spec, reference = _run_registered_source_application(tmp_path)

    report = verify_replay_with_source(
        manager.pass_dir, spec, reference_timeline=reference
    )

    assert report.internal_issues == ()
    assert report.source_issues == ()
    assert report.source_binding_complete is True
    assert report.accepted is True


def test_late_valid_selector_at_eof_is_expired_without_revision_commit(tmp_path):
    spec = _registered_spec(tmp_path)
    reference = _timeline([])
    denominator_frozen = threading.Event()
    production = baseline_analysis_function(CONFIG)

    def finish_after_freeze(job):
        if not denominator_frozen.wait(timeout=3.0):
            raise TimeoutError("denominator did not freeze before selector return")
        return production(job)

    manager = create_replay_application(
        spec,
        output_root=tmp_path / "late-selector-run",
        reference_timeline=reference,
        frame_limit=601,
        initial_speed=4.0,
        analysis_function=finish_after_freeze,
        clock_factory=_fast_clock,
        shutdown_timeout_s=2.0,
    )
    original_playback_write = manager.current_run.evidence.write_playback_event

    def observe_freeze(event):
        original_playback_write(event)
        if event.get("kind") == "denominator_frozen":
            denominator_frozen.set()

    manager.current_run.evidence.write_playback_event = observe_freeze
    manager.start()
    assert manager.current_run._ended.wait(timeout=5.0)
    manager.stop(timeout_s=3.0)

    assert manager.failure is None
    assert manager.clean_shutdown is True
    snapshot = manager.latest_snapshot
    assert snapshot.selected_bin is None
    assert snapshot.selection_revision == 0
    assert snapshot.hr.value_bpm is None
    assert snapshot.br.value_bpm is None
    with (manager.pass_dir / "analysis_attempts.csv").open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["disposition"] == "expired"
    assert rows[0]["reason"] == "playback_denominator_frozen"
    report = verify_replay_with_source(
        manager.pass_dir, spec, reference_timeline=reference
    )
    assert report.internal_issues == ()
    assert report.source_issues == ()
    assert report.accepted is True


def test_final_verifier_rejects_source_independent_phase_forgery(tmp_path):
    manager, spec, reference = _run_registered_source_application(tmp_path)
    _assert_baseline_pass_is_accepted(manager, spec, reference)
    forged = tmp_path / "forged-pass"
    shutil.copytree(manager.pass_dir, forged)

    def shift_phase(payload):
        payload["phase_raw"] = payload["phase_raw"] + np.float64(1.25)
        payload["phase_clean"] = remove_impulse_noise(
            payload["phase_raw"], float(CONFIG["phase"]["impulse_clip_rad"])
        )

    _rehash_forged_attempt(forged, shift_phase)
    # The forged phase is internally self-consistent after a constant offset,
    # and the attacker has recomputed all outer hashes. The registered ADC must
    # still be the independent authority.
    assert verify_replay_artifacts(forged) == ()

    report = verify_replay_with_source(
        forged, spec, reference_timeline=reference
    )

    assert report.accepted is False
    assert report.source_binding_complete is False
    assert any(issue.code == "source_phase_mismatch" for issue in report.source_issues)


def test_final_verifier_reports_missing_authorized_source_as_incomplete(tmp_path):
    manager, spec, reference = _run_registered_source_application(tmp_path)

    absent_authorization = verify_replay_with_source(
        manager.pass_dir, None, reference_timeline=reference
    )
    missing_file = verify_replay_with_source(
        manager.pass_dir,
        replace(spec, adc_path=tmp_path / "missing-adc.bin"),
        reference_timeline=reference,
    )

    assert absent_authorization.accepted is False
    assert absent_authorization.source_binding_complete is False
    assert any(
        issue.code == "source_inputs_unavailable"
        for issue in absent_authorization.source_issues
    )
    assert missing_file.accepted is False
    assert missing_file.source_binding_complete is False
    assert any(
        issue.code == "source_inputs_unavailable"
        for issue in missing_file.source_issues
    )


def test_final_verifier_rejects_resigned_attempt_path_escape(tmp_path):
    manager, spec, reference = _run_registered_source_application(tmp_path)
    _assert_baseline_pass_is_accepted(manager, spec, reference)
    forged = tmp_path / "forged-attempt-path"
    shutil.copytree(manager.pass_dir, forged)
    index_path = forged / "analysis_attempts.csv"
    with index_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        rows = list(reader)
    assert fieldnames is not None and len(rows) == 1
    original_relative = rows[0]["npz_file"]
    escaped_path = forged.parent / "escaped_attempt.npz"
    shutil.copy2(forged / original_relative, escaped_path)
    rows[0]["npz_file"] = "../escaped_attempt.npz"
    rows[0]["npz_sha256"] = hashlib.sha256(escaped_path.read_bytes()).hexdigest()
    with index_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    manifest_path = forged / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["files"][original_relative]
    manifest["files"]["../escaped_attempt.npz"] = rows[0]["npz_sha256"]
    manifest["files"]["analysis_attempts.csv"] = hashlib.sha256(
        index_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    report = verify_replay_with_source(
        forged, spec, reference_timeline=reference
    )

    assert report.accepted is False
    assert report.source_binding_complete is False
    assert any(
        issue.code == "artifact_containment_preflight"
        for issue in report.internal_issues
    )
    assert any(
        issue.code == "source_binding_not_attempted"
        for issue in report.source_issues
    )


@pytest.mark.parametrize(
    "mutate_audit",
    [
        lambda audit: audit[0]["hr"].__setitem__(
            "observed_count", audit[0]["hr"]["observed_count"] + 1
        ),
        lambda audit: audit[0]["reference_source"].__setitem__(
            "sha256", "0" * 64
        ),
    ],
    ids=("window-count", "reference-identity"),
)
def test_final_verifier_recomputes_passive_reference_audit(
    tmp_path, mutate_audit
):
    reference = _timeline(
        [(second, 70.0 + second, 3.0, 12.0 + second) for second in range(1, 32)]
    )
    manager, spec, reference = _run_registered_source_application(
        tmp_path, reference
    )
    audit_path = manager.pass_dir / "reference_audit.json"
    assert json.loads(audit_path.read_text(encoding="utf-8"))

    complete = verify_replay_with_source(
        manager.pass_dir, spec, reference_timeline=reference
    )
    unavailable = verify_replay_with_source(manager.pass_dir, spec)

    assert complete.accepted is True
    assert any(
        issue.code == "reference_inputs_unavailable"
        for issue in unavailable.internal_issues
    )
    assert unavailable.accepted is False

    forged = tmp_path / "forged-reference-audit"
    shutil.copytree(manager.pass_dir, forged)
    forged_audit_path = forged / "reference_audit.json"
    forged_audit = json.loads(forged_audit_path.read_text(encoding="utf-8"))
    mutate_audit(forged_audit)
    forged_audit_path.write_text(
        json.dumps(forged_audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    manifest_path = forged / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["reference_audit.json"] = hashlib.sha256(
        forged_audit_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    report = verify_replay_with_source(
        forged, spec, reference_timeline=reference
    )
    assert report.source_binding_complete is True
    assert report.accepted is False
    assert any(
        issue.code == "reference_audit_mismatch"
        for issue in report.internal_issues
    )


def test_reference_display_audit_covers_every_processed_frame_without_future_data(
    tmp_path,
):
    reference = _timeline(
        [(second, 70.0 + second, 3.0, 12.0 + second) for second in range(1, 32)]
    )
    manager, _specification, reference = _run_registered_source_application(
        tmp_path, reference
    )
    audit = json.loads(
        (manager.pass_dir / "reference_display_audit.json").read_text(
            encoding="utf-8"
        )
    )

    assert audit["schema"] == "replay_reference_display_audit_v1"
    assert audit["reference_source"] == asdict(reference.source)
    frames = audit["frames"]
    assert [row["frame_index"] for row in frames] == list(range(601))
    assert [row["display_boundary"] for row in frames] == list(range(1, 602))
    assert frames[-1]["display_boundary"] == manager.latest_snapshot.processed_boundary
    expected_fields = {
        "frame_index",
        "display_boundary",
        "timestamp_s",
        "pr_bpm",
        "pi",
        "rr_bpm",
        "pr_present",
        "pr_qualified",
        "rr_present",
    }
    for row in frames:
        assert set(row) == expected_fields
        expected = asdict(reference.lookup_frame(row["frame_index"]))
        assert {
            key: value
            for key, value in row.items()
            if key not in {"frame_index", "display_boundary"}
        } == expected


@pytest.mark.parametrize(
    "tamper",
    [
        lambda audit: audit["frames"][20].__setitem__("pr_bpm", 999.0),
        lambda audit: audit["frames"][20].__setitem__("timestamp_s", 999),
        lambda audit: audit["frames"][20].__setitem__("pr_qualified", False),
        lambda audit: audit["reference_source"].__setitem__("sha256", "0" * 64),
    ],
    ids=("value", "timestamp", "quality", "source"),
)
def test_final_verifier_rejects_reference_display_audit_tampering(
    tmp_path, tamper
):
    reference = _timeline(
        [(second, 70.0 + second, 3.0, 12.0 + second) for second in range(1, 32)]
    )
    manager, spec, reference = _run_registered_source_application(
        tmp_path, reference
    )
    _assert_baseline_pass_is_accepted(manager, spec, reference)
    forged = tmp_path / "forged-reference-display-audit"
    shutil.copytree(manager.pass_dir, forged)
    path = forged / "reference_display_audit.json"
    audit = json.loads(path.read_text(encoding="utf-8"))
    tamper(audit)
    path.write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _rehash_json_artifact(forged, "reference_display_audit.json")

    report = verify_replay_with_source(
        forged, spec, reference_timeline=reference
    )

    assert report.accepted is False
    assert any(
        issue.code == "reference_display_audit_mismatch"
        for issue in report.internal_issues
    )


@pytest.mark.parametrize(
    "mutate_integrity",
    [
        lambda summary: summary.pop("source_integrity"),
        lambda summary: summary["source_integrity"].__setitem__("complete", False),
        lambda summary: summary["source_integrity"].__setitem__(
            "final_sha256", "0" * 64
        ),
        lambda summary: summary["source_integrity"].__setitem__("frames_read", 600),
    ],
    ids=("missing", "incomplete", "hash", "frame-count"),
)
def test_final_verifier_rejects_missing_or_forged_source_integrity(
    tmp_path, mutate_integrity
):
    manager, spec, reference = _run_registered_source_application(tmp_path)
    _assert_baseline_pass_is_accepted(manager, spec, reference)
    forged = tmp_path / "forged-source-integrity"
    shutil.copytree(manager.pass_dir, forged)
    summary_path = forged / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    mutate_integrity(summary)
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    _rehash_json_artifact(forged, "summary.json")

    report = verify_replay_with_source(
        forged, spec, reference_timeline=reference
    )

    assert report.accepted is False
    assert report.source_binding_complete is False
    assert any(
        issue.code == "source_integrity_mismatch"
        for issue in report.source_issues
    )


def test_stop_winning_during_baseline_restart_leaves_new_pass_incomplete(
    tmp_path, request
):
    frames = _frames()
    sources = []
    second_source_entered = threading.Event()
    release_second_source = threading.Event()

    def source_factory():
        if sources:
            second_source_entered.set()
            if not release_second_source.wait(timeout=3.0):
                raise TimeoutError("test did not release second source construction")
        source = _ArraySource(frames)
        sources.append(source)
        return source

    manager = create_replay_application(
        _spec(tmp_path),
        output_root=tmp_path / "stop-during-baseline-restart",
        reference_timeline=_timeline([]),
        frame_limit=1,
        initial_speed=4.0,
        source_factory=source_factory,
        analysis_function=_RecordingAnalysis(),
        clock_factory=_fast_clock,
        shutdown_timeout_s=2.0,
    )

    def cleanup():
        release_second_source.set()
        manager.stop(timeout_s=1.0)

    request.addfinalizer(cleanup)
    manager.start()
    assert manager.current_run._ended.wait(timeout=3.0)
    monitor = manager._run.monitor
    assert monitor is not None
    monitor.join(timeout=5.0)
    assert manager.clean_shutdown is True

    first_pass = manager.pass_dir
    assert manager.restart() is True
    assert second_source_entered.wait(timeout=3.0)
    previous = manager.current_run
    calls = []
    previous.request_pause = lambda: calls.append("pause")
    previous.resume = lambda: calls.append("resume")
    previous.set_speed = lambda value: calls.append(value)
    assert manager.request_pause() is False
    assert manager.resume() is False
    assert manager.set_speed(2.0) is False
    assert calls == []
    manager.request_stop()
    release_second_source.set()
    transition = manager._transition
    stop_thread = manager._stop_thread
    assert transition is not None and stop_thread is not None
    transition.join(timeout=5.0)
    stop_thread.join(timeout=5.0)

    assert transition.is_alive() is False
    assert stop_thread.is_alive() is False
    assert manager.generation == 1
    assert manager.pass_dir == first_pass
    assert isinstance(manager.failure, RuntimeError)
    assert "unopened pass" in str(manager.failure)
    assert manager.clean_shutdown is False
    assert len(sources) == 2
    assert sources[1].cursor == 0
    assert sources[1].integrity is None
    incomplete = manager.output_root / "pass_0002"
    assert incomplete.is_dir()
    assert (incomplete / "summary.json").exists() is False
    assert (incomplete / "manifest.json").exists() is False


def test_stuck_baseline_restart_transition_times_out_without_new_worker(
    tmp_path, request
):
    manager = create_replay_application(
        _spec(tmp_path),
        output_root=tmp_path / "stuck-restart",
        reference_timeline=_timeline([]),
        frame_limit=1,
        initial_speed=4.0,
        source_factory=lambda: _ArraySource(_frames()),
        analysis_function=_RecordingAnalysis(),
        clock_factory=_fast_clock,
        shutdown_timeout_s=1.0,
    )
    manager.start()
    assert manager.current_run._ended.wait(timeout=3.0)
    monitor = manager._run.monitor
    assert monitor is not None
    monitor.join(timeout=5.0)
    assert monitor.is_alive() is False
    assert manager.clean_shutdown is True
    original_build = manager._build_run
    built = {}
    build_complete = threading.Event()
    release_install = threading.Event()

    def stuck_build(*args, **kwargs):
        run = original_build(*args, **kwargs)
        built["run"] = run
        build_complete.set()
        if not release_install.wait(timeout=3.0):
            raise TimeoutError("test did not release stuck baseline restart")
        return run

    manager._build_run = stuck_build

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
    assert isinstance(manager.failure, RuntimeError)
    assert "shutdown timeout" in str(manager.failure)
    assert manager.clean_shutdown is False
    first_failure = manager.failure
    assert manager.generation == 1
    next_run = built["run"]
    assert next_run.runtime._worker is None
    assert next_run.runtime._controller is None

    release_install.set()
    transition.join(timeout=5.0)
    assert transition.is_alive() is False
    assert manager.failure is first_failure
