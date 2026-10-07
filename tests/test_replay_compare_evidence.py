"""Replay artifact atomicity, reconstruction, and tamper-resistance tests."""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import yaml

from src.live_motion.runtime import ExecutedAnalysis, _exception_evidence
from src.live_motion.scheduler import AnalysisJob, AnalysisResult, SelectionDecision
from src.range_coordinates import bin_range_m
from src.replay_compare.engine import (
    BaselineController,
    BaselineDisposition,
    ReplayControllerEvent,
    baseline_analysis_function,
)
from src.replay_compare.evidence import (
    REPLAY_EVIDENCE_SCHEMA,
    ReplayEvidenceWriter,
    verify_replay_artifacts,
)
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


def _executed(
    *,
    job_id="job-0",
    hr=70.0,
    br=12.0,
    hr_valid=True,
    br_valid=True,
    selection_decision=None,
    actual_bin=1,
    executed=True,
    status="completed",
):
    phase_raw = np.zeros(600, dtype=np.float64)
    phase_clean = remove_impulse_noise(phase_raw, 1.5)
    result = AnalysisResult(
        job_id=job_id,
        epoch=0,
        selection_revision=0,
        stage="ordinary_30",
        frame_start=0,
        frame_stop=600,
        actual_bin=actual_bin,
        executed=executed,
        status=status,
        dsp={
            "hr_valid": hr_valid,
            "br_valid": br_valid,
            "hr_raw": hr,
            "br_bpm": br,
            "hr_result": {
                "accepted_candidate_rank": 0,
                "ahet_verified": True,
            },
        },
        selection_decision=selection_decision,
    )
    evidence = {
        "phase_raw": phase_raw,
        "phase_clean": phase_clean,
        "heart_freqs_hz": np.asarray([1.0, 1.2]),
        "heart_spectrum": np.asarray([2.0, 3.0]),
        "resp_freqs_hz": np.asarray([0.2, 0.3]),
        "resp_spectrum": np.asarray([4.0, 1.0]),
        "selected_peak_bins": np.asarray([1, 1, 0], dtype=np.int64),
        "respiration_inputs_hz": np.asarray([0.2]),
        "activity_resp_projection": np.asarray([], dtype=np.float64),
        "activity_subband_projection": np.asarray([], dtype=np.float64),
        "coherence_scores": np.asarray([], dtype=np.float64),
        "thresholds": np.asarray([], dtype=np.float64),
        "rejection_reasons": np.asarray([], dtype="<U1"),
        "hr_window_start": np.asarray(0, dtype=np.int64),
        "br_window_start": np.asarray(0, dtype=np.int64),
        "exceptional_evidence": np.asarray(True, dtype=np.bool_),
        "corrected_range_m": np.asarray(bin_range_m(actual_bin, CONFIG)),
    }
    return ExecutedAnalysis(result, evidence, 0.01, 0.002)


def _authentic_executed(
    *,
    job_id: str = "job-authentic",
    frame_start: int = 0,
    selection_mode: str = "committed",
    selection_revision: int = 1,
    heart_bpm: float = 71.0,
) -> ExecutedAnalysis:
    fs = 20.0
    count = 600
    time_s = np.arange(count, dtype=np.float64) / fs
    rng = np.random.default_rng(123)
    respiration_hz = 0.25
    heart_hz = heart_bpm / 60.0
    phase = (
        3.00 * np.sin(2 * np.pi * respiration_hz * time_s)
        + 1.50 * np.sin(2 * np.pi * 2 * respiration_hz * time_s)
        + 1.00 * np.sin(2 * np.pi * 3 * respiration_hz * time_s)
        + 0.75 * np.sin(2 * np.pi * 4 * respiration_hz * time_s)
        + 0.30 * np.sin(2 * np.pi * heart_hz * time_s)
        + 0.15 * np.sin(2 * np.pi * 2 * heart_hz * time_s)
        + 0.03 * rng.standard_normal(count)
    )
    fast_index = np.arange(8, dtype=np.float64)
    carrier = np.exp(2j * np.pi * fast_index / 8.0)
    raw_frames = (
        np.exp(1j * phase)[:, None, None, None]
        * carrier[None, None, None, :]
    ).astype(np.complex64)
    job = AnalysisJob(
        job_id=job_id,
        epoch=0,
        selection_revision=selection_revision,
        stage="ordinary_30" if selection_mode == "select" else "rolling",
        frame_start=frame_start,
        frame_stop=frame_start + count,
        requested_bin=None if selection_mode == "select" else 1,
        selection_mode=selection_mode,
        raw_frames=raw_frames,
    )
    return baseline_analysis_function(CONFIG)(job)


def _authentic_disposition(executed: ExecutedAnalysis) -> BaselineDisposition:
    dsp = executed.result.dsp
    assert dsp is not None
    hr_result = dsp.get("hr_result") or {}
    hr_accepted = bool(
        dsp.get("hr_valid") is True
        and hr_result.get("ahet_verified") is True
        and type(hr_result.get("accepted_candidate_rank")) is int
        and hr_result["accepted_candidate_rank"] >= 0
        and np.isfinite(dsp.get("hr_raw"))
        and float(dsp["hr_raw"]) > 0
    )
    br_accepted = bool(
        dsp.get("br_valid") is True
        and np.isfinite(dsp.get("br_bpm"))
        and float(dsp["br_bpm"]) > 0
    )
    return BaselineDisposition(
        generation=1,
        application_boundary=600,
        selection_revision=1,
        disposition="published" if hr_accepted or br_accepted else "rejected",
        reason="accepted" if hr_accepted or br_accepted else "both_channels_rejected",
        hr_accepted=hr_accepted,
        br_accepted=br_accepted,
        quiet_accepted=False,
        display_hr_bpm=float(dsp["hr_raw"]) if hr_accepted else None,
        display_hr_state="accepted" if hr_accepted else "missing",
        display_br_bpm=float(dsp["br_bpm"]) if br_accepted else None,
        display_br_state="accepted" if br_accepted else "missing",
        request_epoch=executed.result.epoch,
        request_frame_start=executed.result.frame_start,
        request_frame_stop=executed.result.frame_stop,
        requested_bin=executed.result.actual_bin,
        selection_mode="committed",
        requested_selection_revision=executed.result.selection_revision,
    )


def _decision(*, fallback=False, dsp_succeeded=True):
    return SelectionDecision(
        selected_bin=1,
        evidence={"fallback_used": fallback},
        selector_succeeded=True,
        dsp_succeeded=dsp_succeeded,
        fallback_used=fallback,
        reason="fallback" if fallback else "eligible_dsp_pass",
    )


def _all_dsp_failed_selector(job: AnalysisJob) -> ExecutedAnalysis:
    decision = SelectionDecision(
        selected_bin=1,
        evidence={
            "fallback_used": True,
            "failure_reason": "all_dsp_failed_energy_fallback",
        },
        selector_succeeded=True,
        dsp_succeeded=False,
        fallback_used=True,
        reason="all_dsp_failed_energy_fallback",
    )
    result = AnalysisResult(
        job_id=job.job_id,
        epoch=job.epoch,
        selection_revision=job.selection_revision,
        stage=job.stage,
        frame_start=job.frame_start,
        frame_stop=job.frame_stop,
        actual_bin=1,
        executed=True,
        status="completed",
        dsp=None,
        selection_decision=decision,
    )
    return ExecutedAnalysis(
        result=result,
        evidence=_exception_evidence(
            job, CONFIG, "all_dsp_failed_energy_fallback", 1
        ),
        analysis_elapsed_s=0.01,
        selector_elapsed_s=0.002,
    )


def _disposition(
    *,
    job_accepted=True,
    display_hr=70.0,
    display_br=12.0,
    reason="accepted",
):
    return BaselineDisposition(
        generation=1,
        application_boundary=600,
        selection_revision=1,
        disposition="published" if job_accepted else "rejected",
        reason=reason,
        hr_accepted=job_accepted,
        br_accepted=job_accepted,
        quiet_accepted=False,
        display_hr_bpm=display_hr if job_accepted else None,
        display_hr_state="accepted" if job_accepted else "missing",
        display_br_bpm=display_br if job_accepted else None,
        display_br_state="accepted" if job_accepted else "missing",
    )


def _writer(tmp_path: Path) -> ReplayEvidenceWriter:
    return ReplayEvidenceWriter(
        tmp_path / "pass_1",
        {
            "generation": 1,
            "mode": "baseline",
            "source_kind": "recorded_development_replay",
            "effective_config": CONFIG,
        },
    )


def _finalize(writer: ReplayEvidenceWriter) -> Path:
    return writer.finalize(
        summary={
            "completion": "complete",
            "denominator_frames": 600,
            "hr_accepted_frames": 0,
            "br_numeric_frames": 0,
            "both_frames": 0,
        },
        display_intervals=[],
        reference_audit=[],
    )


def _write_requested_attempt(
    writer: ReplayEvidenceWriter,
    executed: ExecutedAnalysis,
    disposition: BaselineDisposition,
):
    result = executed.result
    applied_selected_bin = (
        result.actual_bin
        if disposition.selection_mode == "committed"
        or disposition.selection_revision
        == disposition.requested_selection_revision + 1
        else None
    )
    writer.write_controller_event(
        ReplayControllerEvent(
            kind="analysis_requested",
            reason="ordinary_boundary",
            boundary=result.frame_stop,
            epoch=disposition.request_epoch,
            generation=disposition.generation,
            job_id=result.job_id,
            selected_bin=disposition.requested_bin,
            selection_revision=disposition.requested_selection_revision,
            hr_state="missing",
            br_state="missing",
            frame_start=disposition.request_frame_start,
            frame_stop=disposition.request_frame_stop,
            requested_bin=disposition.requested_bin,
            selection_mode=disposition.selection_mode,
            requested_selection_revision=disposition.requested_selection_revision,
        )
    )
    writer.write_controller_event(
        ReplayControllerEvent(
            kind="result_applied",
            reason=disposition.reason,
            boundary=disposition.application_boundary,
            epoch=disposition.application_epoch,
            generation=disposition.generation,
            job_id=result.job_id,
            selected_bin=applied_selected_bin,
            selection_revision=disposition.selection_revision,
            hr_state=disposition.display_hr_state,
            br_state=disposition.display_br_state,
            disposition=disposition.disposition,
            hr_accepted=disposition.hr_accepted,
            br_accepted=disposition.br_accepted,
        )
    )
    return writer.write_attempt(executed, disposition)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rewrite_attempt_and_rehash_pass(
    pass_dir: Path, mutate, *, attempt_index: int = 0
) -> None:
    index_path = pass_dir / "analysis_attempts.csv"
    with index_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        rows = list(reader)
    assert fieldnames is not None and len(rows) > attempt_index
    row = rows[attempt_index]
    npz_path = pass_dir / row["npz_file"]
    with np.load(npz_path, allow_pickle=False) as archive:
        payload = {name: np.array(archive[name], copy=True) for name in archive.files}
    mutate(payload)
    with npz_path.open("wb") as handle:
        np.savez(handle, **payload)
    row["npz_sha256"] = _digest(npz_path)
    with index_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    manifest_path = pass_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][row["npz_file"]] = _digest(npz_path)
    manifest["files"]["analysis_attempts.csv"] = _digest(index_path)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _rehash_manifest_file(pass_dir: Path, relative: str) -> None:
    manifest_path = pass_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][relative] = _digest(pass_dir / relative)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _rewrite_index_and_rehash(pass_dir: Path, mutate) -> None:
    index_path = pass_dir / "analysis_attempts.csv"
    with index_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        rows = list(reader)
    assert fieldnames is not None
    mutate(rows)
    with index_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    _rehash_manifest_file(pass_dir, "analysis_attempts.csv")


def _rewrite_events_and_rehash(pass_dir: Path, mutate) -> None:
    events_path = pass_dir / "controller_events.jsonl"
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    mutate(events)
    events_path.write_text(
        "".join(
            json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n"
            for event in events
        ),
        encoding="utf-8",
        newline="\n",
    )
    _rehash_manifest_file(pass_dir, "controller_events.jsonl")


def test_authentic_executed_attempt_has_primitive_evidence_and_verifies(tmp_path):
    writer = _writer(tmp_path)
    executed = _authentic_executed()
    record = _write_requested_attempt(
        writer, executed, _authentic_disposition(executed)
    )
    _finalize(writer)

    assert record.index == 0
    with np.load(record.npz_path, allow_pickle=False) as payload:
        assert all(not payload[name].dtype.hasobject for name in payload.files)
        assert payload["phase_raw"].shape == payload["phase_clean"].shape
        assert payload["schema"].item() == REPLAY_EVIDENCE_SCHEMA
    assert verify_replay_artifacts(writer.pass_dir) == ()


def test_cancelled_before_execution_is_event_only_and_empty_attempt_set_verifies(
    tmp_path
):
    writer = _writer(tmp_path)
    writer.write_controller_event(
        ReplayControllerEvent(
            kind="analysis_requested",
            job_id="never-ran",
            reason="ordinary_boundary",
            boundary=600,
            generation=1,
            epoch=0,
            selection_revision=0,
            requested_selection_revision=0,
            frame_start=0,
            frame_stop=600,
            requested_bin=None,
            selection_mode="select",
            hr_state="missing",
            br_state="missing",
        )
    )
    writer.write_controller_event(
        ReplayControllerEvent(
            kind="cancelled_before_execution",
            job_id="never-ran",
            reason="playback_finished",
            boundary=600,
            generation=1,
            epoch=0,
        )
    )
    _finalize(writer)

    with writer.index_path.open(newline="", encoding="utf-8") as handle:
        assert list(csv.DictReader(handle)) == []
    assert list(writer.attempt_dir.iterdir()) == []
    assert verify_replay_artifacts(writer.pass_dir) == ()


def test_writer_refuses_cancelled_result_as_attempt(tmp_path):
    writer = _writer(tmp_path)
    executed = _executed(executed=False, status="interrupted")
    with pytest.raises(ValueError, match="event evidence only"):
        writer.write_attempt(executed, _disposition(job_accepted=False))


def test_writer_preserves_executed_arrays_without_fabricating_missing_lineage(tmp_path):
    writer = _writer(tmp_path)
    executed = _authentic_executed()
    record = writer.write_attempt(executed, _authentic_disposition(executed))
    _finalize(writer)

    with np.load(record.npz_path, allow_pickle=False) as payload:
        assert np.array_equal(payload["phase_raw"], executed.evidence["phase_raw"])
        assert np.array_equal(payload["phase_clean"], executed.evidence["phase_clean"])
    with writer.index_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["job_id"] for row in rows] == [executed.result.job_id]
    events = [
        json.loads(line)
        for line in writer.events_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert [event["kind"] for event in events] == ["attempt_terminal"]
    issues = verify_replay_artifacts(writer.pass_dir)
    assert any("request" in (issue.code + issue.details).lower() for issue in issues)
    assert any("application" in (issue.code + issue.details).lower() for issue in issues)


def test_writer_refuses_missing_intermediate_component(tmp_path):
    writer = _writer(tmp_path)
    executed = _executed()
    executed.evidence.pop("phase_clean")
    with pytest.raises(ValueError, match="omitted fields"):
        writer.write_attempt(executed, _disposition())


def test_completed_all_dsp_failed_selector_is_honestly_verifiable(tmp_path):
    writer = _writer(tmp_path)
    job = AnalysisJob(
        job_id="selector-all-dsp-failed",
        epoch=0,
        selection_revision=0,
        stage="ordinary_30",
        frame_start=0,
        frame_stop=600,
        requested_bin=None,
        selection_mode="select",
        raw_frames=np.zeros((600, 1, 1, 8), dtype=np.complex64),
    )
    executed = _all_dsp_failed_selector(job)
    disposition = BaselineDisposition(
        generation=1,
        application_boundary=600,
        selection_revision=0,
        disposition="rejected",
        reason="selector_no_verified_dsp",
        hr_accepted=False,
        br_accepted=False,
        quiet_accepted=False,
        display_hr_bpm=None,
        display_hr_state="missing",
        display_br_bpm=None,
        display_br_state="missing",
        request_epoch=0,
        request_frame_start=0,
        request_frame_stop=600,
        requested_bin=None,
        selection_mode="select",
        requested_selection_revision=0,
    )
    _write_requested_attempt(writer, executed, disposition)
    _finalize(writer)

    assert verify_replay_artifacts(writer.pass_dir) == ()

    forged = tmp_path / "forged_revision"
    shutil.copytree(writer.pass_dir, forged)
    _rewrite_attempt_and_rehash_pass(
        forged,
        lambda payload: payload.__setitem__(
            "applied_selection_revision", np.asarray(1, dtype=np.int64)
        ),
    )
    _rewrite_index_and_rehash(
        forged,
        lambda rows: rows[0].__setitem__("applied_selection_revision", "1"),
    )

    def forge_application_revision(events):
        applied = next(item for item in events if item["kind"] == "result_applied")
        applied["selection_revision"] = 1

    _rewrite_events_and_rehash(forged, forge_application_revision)
    issues = verify_replay_artifacts(forged)
    assert any("revision" in (issue.code + issue.details).lower() for issue in issues)


def test_completed_exceptional_selector_without_exact_failure_decision_is_rejected(
    tmp_path,
):
    writer = _writer(tmp_path)
    job = AnalysisJob(
        job_id="selector-resigned",
        epoch=0,
        selection_revision=0,
        stage="ordinary_30",
        frame_start=0,
        frame_stop=600,
        requested_bin=None,
        selection_mode="select",
        raw_frames=np.zeros((600, 1, 1, 8), dtype=np.complex64),
    )
    executed = _all_dsp_failed_selector(job)
    disposition = BaselineDisposition(
        generation=1,
        application_boundary=600,
        selection_revision=0,
        disposition="rejected",
        reason="selector_no_verified_dsp",
        hr_accepted=False,
        br_accepted=False,
        quiet_accepted=False,
        display_hr_bpm=None,
        display_hr_state="missing",
        display_br_bpm=None,
        display_br_state="missing",
        request_epoch=0,
        request_frame_start=0,
        request_frame_stop=600,
        requested_bin=None,
        selection_mode="select",
        requested_selection_revision=0,
    )
    _write_requested_attempt(writer, executed, disposition)
    _finalize(writer)
    _rewrite_attempt_and_rehash_pass(
        writer.pass_dir,
        lambda payload: payload.__setitem__(
            "selection_decision_json", np.asarray("null")
        ),
    )

    issues = verify_replay_artifacts(writer.pass_dir)
    assert any("selector" in (issue.code + issue.details).lower() for issue in issues)


@pytest.mark.parametrize(
    ("field", "forged"),
    [("selected_bin", None), ("reason", "invented_failure")],
)
def test_completed_exceptional_selector_requires_exact_all_dsp_failed_decision(
    tmp_path, field, forged
):
    writer = _writer(tmp_path)
    job = AnalysisJob(
        job_id="selector-resigned-decision",
        epoch=0,
        selection_revision=0,
        stage="ordinary_30",
        frame_start=0,
        frame_stop=600,
        requested_bin=None,
        selection_mode="select",
        raw_frames=np.zeros((600, 1, 1, 8), dtype=np.complex64),
    )
    executed = _all_dsp_failed_selector(job)
    disposition = BaselineDisposition(
        generation=1,
        application_boundary=600,
        selection_revision=0,
        disposition="rejected",
        reason="selector_no_verified_dsp",
        hr_accepted=False,
        br_accepted=False,
        quiet_accepted=False,
        display_hr_bpm=None,
        display_hr_state="missing",
        display_br_bpm=None,
        display_br_state="missing",
        request_epoch=0,
        request_frame_start=0,
        request_frame_stop=600,
        requested_bin=None,
        selection_mode="select",
        requested_selection_revision=0,
    )
    _write_requested_attempt(writer, executed, disposition)
    _finalize(writer)

    def forge_decision(payload):
        decision = json.loads(str(payload["selection_decision_json"].item()))
        decision[field] = forged
        payload["selection_decision_json"] = np.asarray(
            json.dumps(decision, sort_keys=True, separators=(",", ":"))
        )

    _rewrite_attempt_and_rehash_pass(writer.pass_dir, forge_decision)
    issues = verify_replay_artifacts(writer.pass_dir)
    assert any("all-dsp-failed" in issue.details.lower() for issue in issues)


def test_successful_selector_requires_exactly_one_revision_increment(tmp_path):
    writer = _writer(tmp_path)
    executed = _authentic_executed(
        job_id="selector-success",
        selection_mode="select",
        selection_revision=0,
    )
    decision = executed.result.selection_decision
    assert decision is not None
    assert decision.selector_succeeded is True
    assert decision.dsp_succeeded is True
    assert decision.fallback_used is False
    assert executed.result.actual_bin == decision.selected_bin
    base = _authentic_disposition(executed)
    disposition = replace(
        base,
        selection_revision=1,
        request_epoch=0,
        request_frame_start=0,
        request_frame_stop=600,
        requested_bin=None,
        selection_mode="select",
        requested_selection_revision=0,
    )
    _write_requested_attempt(writer, executed, disposition)
    _finalize(writer)
    assert verify_replay_artifacts(writer.pass_dir) == ()

    _rewrite_attempt_and_rehash_pass(
        writer.pass_dir,
        lambda payload: payload.__setitem__(
            "applied_selection_revision", np.asarray(0, dtype=np.int64)
        ),
    )
    _rewrite_index_and_rehash(
        writer.pass_dir,
        lambda rows: rows[0].__setitem__("applied_selection_revision", "0"),
    )

    def remove_increment(events):
        event = next(item for item in events if item["kind"] == "result_applied")
        event["selection_revision"] = 0

    _rewrite_events_and_rehash(writer.pass_dir, remove_increment)
    issues = verify_replay_artifacts(writer.pass_dir)
    assert any("revision" in (issue.code + issue.details).lower() for issue in issues)


def test_energy_fallback_may_commit_revision_but_cannot_publish_window_values(
    tmp_path,
):
    writer = _writer(tmp_path)
    successful = _authentic_executed(
        job_id="selector-energy-fallback",
        selection_mode="select",
        selection_revision=0,
    )
    fallback = SelectionDecision(
        selected_bin=successful.result.actual_bin,
        evidence={"fallback_used": True},
        selector_succeeded=True,
        dsp_succeeded=True,
        fallback_used=True,
        reason="no_energy_eligible_dsp",
    )
    executed = ExecutedAnalysis(
        result=replace(successful.result, selection_decision=fallback),
        evidence=successful.evidence,
        analysis_elapsed_s=successful.analysis_elapsed_s,
        selector_elapsed_s=successful.selector_elapsed_s,
    )
    base = _authentic_disposition(executed)
    disposition = replace(
        base,
        selection_revision=1,
        disposition="rejected",
        reason="selection_energy_fallback_not_presentation_eligible",
        hr_accepted=False,
        br_accepted=False,
        display_hr_bpm=None,
        display_hr_state="missing",
        display_br_bpm=None,
        display_br_state="missing",
        request_epoch=0,
        request_frame_start=0,
        request_frame_stop=600,
        requested_bin=None,
        selection_mode="select",
        requested_selection_revision=0,
    )
    _write_requested_attempt(writer, executed, disposition)
    _finalize(writer)

    assert verify_replay_artifacts(writer.pass_dir) == ()


def test_busy_selector_coalescing_has_one_terminal_outcome_per_request(tmp_path):
    writer = _writer(tmp_path / "base")
    controller = BaselineController(CONFIG, generation=1)
    frame = np.ones((1, 1, 8), dtype=np.complex64)

    def advance(count):
        last = None
        for _ in range(count):
            last = controller.handle_frame(
                controller.processed_boundary, frame, valid=True
            )
            for event in last.events:
                writer.write_controller_event(event)
        return last

    advance(600)
    first = controller.claim_pending_job()
    assert first is not None
    controller.register_dispatched_job(first)
    advance(60)
    replaced = controller.pending_job
    assert replaced is not None
    replacement_update = advance(60)
    newest = controller.pending_job
    assert newest is not None and newest.job_id != replaced.job_id
    assert replaced.job_id in replacement_update.cancelled_job_ids

    first_executed = _all_dsp_failed_selector(first)
    first_update = controller.handle_executed(first_executed)
    for event in first_update.events:
        writer.write_controller_event(event)
    assert first_update.disposition is not None
    writer.write_attempt(first_executed, first_update.disposition)

    claimed = controller.claim_pending_job()
    assert claimed is not None and claimed.job_id == newest.job_id
    controller.register_dispatched_job(claimed)
    newest_executed = _all_dsp_failed_selector(claimed)
    newest_update = controller.handle_executed(newest_executed)
    for event in newest_update.events:
        writer.write_controller_event(event)
    assert newest_update.disposition is not None
    writer.write_attempt(newest_executed, newest_update.disposition)
    _finalize(writer)

    assert verify_replay_artifacts(writer.pass_dir) == ()

    unknown = tmp_path / "unknown_cancellation"
    shutil.copytree(writer.pass_dir, unknown)

    def use_unknown_cancellation(events):
        event = next(
            item
            for item in events
            if item["kind"] == "cancelled_before_execution"
            and item.get("replacement_job_id") is not None
        )
        event["kind"] = "cancelled"

    _rewrite_events_and_rehash(unknown, use_unknown_cancellation)
    assert verify_replay_artifacts(unknown)

    duplicate = tmp_path / "duplicate_cancellation"
    shutil.copytree(writer.pass_dir, duplicate)

    def duplicate_cancellation(events):
        event = next(
            item
            for item in events
            if item["kind"] == "cancelled_before_execution"
            and item.get("replacement_job_id") is not None
        )
        events.append(dict(event))

    _rewrite_events_and_rehash(duplicate, duplicate_cancellation)
    issues = verify_replay_artifacts(duplicate)
    assert any("cardinality" in (issue.code + issue.details).lower() for issue in issues)


def test_stale_committed_result_after_integrity_reset_is_honestly_verifiable(
    tmp_path,
):
    writer = _writer(tmp_path / "base")
    controller = BaselineController(CONFIG, generation=1)
    production = baseline_analysis_function(CONFIG)
    count = 660
    time_s = np.arange(count, dtype=np.float64) / 20.0
    phase = (
        3.0 * np.sin(2 * np.pi * 0.25 * time_s)
        + 1.5 * np.sin(2 * np.pi * 0.50 * time_s)
        + 1.0 * np.sin(2 * np.pi * 0.75 * time_s)
        + 0.3 * np.sin(2 * np.pi * (71.0 / 60.0) * time_s)
    )
    carrier = np.exp(2j * np.pi * np.arange(8, dtype=np.float64) / 8.0)
    frames = (
        np.exp(1j * phase)[:, None, None, None]
        * carrier[None, None, None, :]
    ).astype(np.complex64)

    def advance(frame):
        update = controller.handle_frame(
            controller.processed_boundary, frame, valid=True
        )
        for event in update.events:
            writer.write_controller_event(event)

    for frame in frames[:600]:
        advance(frame)
    selector_job = controller.claim_pending_job()
    assert selector_job is not None
    controller.register_dispatched_job(selector_job)
    selector_executed = production(selector_job)
    selector_update = controller.handle_executed(selector_executed)
    for event in selector_update.events:
        writer.write_controller_event(event)
    assert selector_update.disposition is not None
    assert controller.selected_bin is not None
    writer.write_attempt(selector_executed, selector_update.disposition)

    for frame in frames[600:660]:
        advance(frame)
    stale_job = controller.claim_pending_job()
    assert stale_job is not None and stale_job.selection_mode == "committed"
    controller.register_dispatched_job(stale_job)
    stale_executed = production(stale_job)

    reset = controller.handle_frame(
        controller.processed_boundary,
        np.zeros((1, 1, 8), dtype=np.complex64),
        valid=False,
    )
    for event in reset.events:
        writer.write_controller_event(event)
    assert controller.epoch == 1
    assert controller.selection_revision == 0
    assert controller.selected_bin is None

    stale_update = controller.handle_executed(stale_executed)
    for event in stale_update.events:
        writer.write_controller_event(event)
    assert stale_update.disposition is not None
    assert stale_update.disposition.reason == "stale_epoch"
    assert stale_update.disposition.hr_accepted is False
    assert stale_update.disposition.br_accepted is False
    writer.write_attempt(stale_executed, stale_update.disposition)
    _finalize(writer)
    assert verify_replay_artifacts(writer.pass_dir) == ()

    missing_reset = tmp_path / "missing_reset"
    shutil.copytree(writer.pass_dir, missing_reset)
    _rewrite_events_and_rehash(
        missing_reset,
        lambda events: events.__setitem__(
            slice(None),
            [event for event in events if event["kind"] != "data_integrity_reset"],
        ),
    )
    assert verify_replay_artifacts(missing_reset)

    forged = tmp_path / "stale_published"
    shutil.copytree(writer.pass_dir, forged)
    first_hr = float(selector_executed.result.dsp["hr_raw"])
    stale_hr = float(stale_executed.result.dsp["hr_raw"])
    stale_br = float(stale_executed.result.dsp["br_bpm"])
    display_hr = float(np.median([first_hr, stale_hr]))

    def publish_stale(payload):
        payload["disposition"] = np.asarray("published")
        payload["hr_accepted"] = np.asarray(True, dtype=np.bool_)
        payload["br_accepted"] = np.asarray(True, dtype=np.bool_)
        payload["display_hr_bpm"] = np.asarray(display_hr, dtype=np.float64)
        payload["display_hr_state"] = np.asarray("accepted")
        payload["display_br_bpm"] = np.asarray(stale_br, dtype=np.float64)
        payload["display_br_state"] = np.asarray("accepted")

    _rewrite_attempt_and_rehash_pass(forged, publish_stale, attempt_index=1)

    def publish_stale_index(rows):
        row = rows[1]
        row["disposition"] = "published"
        row["hr_accepted"] = "1"
        row["br_accepted"] = "1"
        row["display_hr_bpm"] = str(display_hr)
        row["display_hr_state"] = "accepted"
        row["display_br_bpm"] = str(stale_br)
        row["display_br_state"] = "accepted"

    _rewrite_index_and_rehash(forged, publish_stale_index)

    def publish_stale_event(events):
        event = next(
            item
            for item in events
            if item["kind"] == "result_applied"
            and item["job_id"] == stale_job.job_id
        )
        event["disposition"] = "published"
        event["hr_accepted"] = True
        event["br_accepted"] = True
        event["hr_state"] = "accepted"
        event["br_state"] = "accepted"

    _rewrite_events_and_rehash(forged, publish_stale_event)
    issues = verify_replay_artifacts(forged)
    assert any("admission" in (issue.code + issue.details).lower() for issue in issues)


def test_hash_consistent_phase_tamper_is_recomputed_and_rejected(tmp_path):
    writer = _writer(tmp_path)
    executed = _authentic_executed()
    _write_requested_attempt(writer, executed, _authentic_disposition(executed))
    _finalize(writer)
    _rewrite_attempt_and_rehash_pass(
        writer.pass_dir,
        lambda payload: payload["phase_clean"].__setitem__(1, 99.0),
    )

    issues = verify_replay_artifacts(writer.pass_dir)
    assert any("phase cleaning" in issue.details for issue in issues)


def test_hash_consistent_display_median_tamper_is_recomputed_and_rejected(tmp_path):
    writer = _writer(tmp_path)
    executed = _authentic_executed()
    disposition = _authentic_disposition(executed)
    assert disposition.hr_accepted is True
    _write_requested_attempt(writer, executed, disposition)
    _finalize(writer)
    _rewrite_attempt_and_rehash_pass(
        writer.pass_dir,
        lambda payload: payload.__setitem__(
            "display_hr_bpm", np.asarray(71.0, dtype=np.float64)
        ),
    )

    issues = verify_replay_artifacts(writer.pass_dir)
    assert any("median reconstruction" in issue.details for issue in issues)


@pytest.mark.parametrize(
    ("state", "display"),
    [("missing", None), ("held", 999.0)],
)
def test_verifier_reconstructs_exact_eligible_admission_and_initial_state(
    tmp_path, state, display
):
    writer = _writer(tmp_path)
    executed = _authentic_executed()
    disposition = _authentic_disposition(executed)
    assert disposition.hr_accepted is True
    resigned = replace(
        disposition,
        hr_accepted=False,
        display_hr_state=state,
        display_hr_bpm=display,
    )
    _write_requested_attempt(writer, executed, resigned)
    _finalize(writer)

    issues = verify_replay_artifacts(writer.pass_dir)
    assert any(
        token in (issue.code + issue.details).lower()
        for issue in issues
        for token in ("admission", "eligible", "display", "state")
    )


def test_committed_attempt_rejects_non_null_selector_decision(tmp_path):
    writer = _writer(tmp_path)
    executed = _authentic_executed()
    _write_requested_attempt(writer, executed, _authentic_disposition(executed))
    _finalize(writer)
    decision = {
        "selected_bin": 1,
        "evidence": {},
        "selector_succeeded": True,
        "dsp_succeeded": True,
        "fallback_used": False,
        "reason": "eligible_dsp_pass",
    }
    _rewrite_attempt_and_rehash_pass(
        writer.pass_dir,
        lambda payload: payload.__setitem__(
            "selection_decision_json",
            np.asarray(json.dumps(decision, sort_keys=True, separators=(",", ":"))),
        ),
    )

    issues = verify_replay_artifacts(writer.pass_dir)
    assert any("committed" in (issue.code + issue.details).lower() for issue in issues)


def test_committed_requested_bin_must_equal_actual_bin_after_consistent_rehash(
    tmp_path,
):
    writer = _writer(tmp_path)
    executed = _authentic_executed()
    _write_requested_attempt(writer, executed, _authentic_disposition(executed))
    _finalize(writer)
    _rewrite_attempt_and_rehash_pass(
        writer.pass_dir,
        lambda payload: payload.__setitem__(
            "requested_bin", np.asarray(7, dtype=np.int64)
        ),
    )
    _rewrite_index_and_rehash(
        writer.pass_dir, lambda rows: rows[0].__setitem__("requested_bin", "7")
    )

    def forge_request(events):
        request = next(item for item in events if item["kind"] == "analysis_requested")
        request["requested_bin"] = 7
        request["selected_bin"] = 7

    _rewrite_events_and_rehash(writer.pass_dir, forge_request)

    issues = verify_replay_artifacts(writer.pass_dir)
    assert any("bin" in (issue.code + issue.details).lower() for issue in issues)


def test_selector_selected_bin_outside_candidate_gate_is_rejected_after_rehash(
    tmp_path,
):
    writer = _writer(tmp_path)
    executed = _authentic_executed(
        job_id="selector-outside-gate",
        selection_mode="select",
        selection_revision=0,
    )
    disposition = replace(
        _authentic_disposition(executed),
        selection_revision=1,
        requested_bin=None,
        selection_mode="select",
        requested_selection_revision=0,
    )
    _write_requested_attempt(writer, executed, disposition)
    _finalize(writer)

    def forge_attempt(payload):
        decision = json.loads(str(payload["selection_decision_json"].item()))
        decision["selected_bin"] = 7
        payload["selection_decision_json"] = np.asarray(
            json.dumps(decision, sort_keys=True, separators=(",", ":"))
        )
        payload["actual_bin"] = np.asarray(7, dtype=np.int64)
        payload["corrected_range_m"] = np.asarray(bin_range_m(7, CONFIG))

    _rewrite_attempt_and_rehash_pass(writer.pass_dir, forge_attempt)
    _rewrite_index_and_rehash(
        writer.pass_dir, lambda rows: rows[0].__setitem__("actual_bin", "7")
    )

    def forge_application(events):
        applied = next(item for item in events if item["kind"] == "result_applied")
        applied["selected_bin"] = 7

    _rewrite_events_and_rehash(writer.pass_dir, forge_application)
    issues = verify_replay_artifacts(writer.pass_dir)
    assert any("candidate" in (issue.code + issue.details).lower() for issue in issues)


def test_returned_identity_claim_must_describe_an_actual_mismatch(tmp_path):
    writer = _writer(tmp_path)
    executed = _authentic_executed()
    _write_requested_attempt(writer, executed, _authentic_disposition(executed))
    _finalize(writer)

    def add_false_mismatch_claim(payload):
        payload.update(
            {
                "result_identity_mismatch": np.asarray(True, dtype=np.bool_),
                "result_identity_mismatch_fields": np.asarray(["job_id"], dtype="U"),
                "returned_job_id": np.asarray(executed.result.job_id),
                "returned_epoch": np.asarray(executed.result.epoch, dtype=np.int64),
                "returned_selection_revision": np.asarray(
                    executed.result.selection_revision, dtype=np.int64
                ),
                "returned_stage": np.asarray(executed.result.stage),
                "returned_frame_start": np.asarray(
                    executed.result.frame_start, dtype=np.int64
                ),
                "returned_frame_stop": np.asarray(
                    executed.result.frame_stop, dtype=np.int64
                ),
                "returned_actual_bin": np.asarray(
                    executed.result.actual_bin, dtype=np.int64
                ),
            }
        )

    _rewrite_attempt_and_rehash_pass(writer.pass_dir, add_false_mismatch_claim)
    issues = verify_replay_artifacts(writer.pass_dir)
    assert any("mismatch" in (issue.code + issue.details).lower() for issue in issues)


@pytest.mark.parametrize("application_boundary", [599, 661])
def test_normal_publication_must_apply_from_window_end_through_one_hop(
    tmp_path, application_boundary
):
    writer = _writer(tmp_path)
    executed = _authentic_executed()
    disposition = replace(
        _authentic_disposition(executed),
        application_boundary=application_boundary,
    )
    _write_requested_attempt(writer, executed, disposition)
    _finalize(writer)

    issues = verify_replay_artifacts(writer.pass_dir)
    assert any(
        token in (issue.code + issue.details).lower()
        for issue in issues
        for token in ("boundary", "age", "backfill")
    )


def test_expired_late_result_is_allowed_only_without_publication(tmp_path):
    writer = _writer(tmp_path)
    executed = _authentic_executed()
    expired = replace(
        _authentic_disposition(executed),
        application_boundary=700,
        disposition="expired",
        reason="publication_age_exceeded_one_hop",
        hr_accepted=False,
        br_accepted=False,
        display_hr_bpm=None,
        display_hr_state="missing",
        display_br_bpm=None,
        display_br_state="missing",
    )
    _write_requested_attempt(writer, executed, expired)
    _finalize(writer)
    assert verify_replay_artifacts(writer.pass_dir) == ()

    forged = tmp_path / "forged_publish"
    shutil.copytree(writer.pass_dir, forged)

    def publish_late(payload):
        payload["hr_accepted"] = np.asarray(True, dtype=np.bool_)
        payload["display_hr_bpm"] = np.asarray(
            float(executed.result.dsp["hr_raw"]), dtype=np.float64
        )
        payload["display_hr_state"] = np.asarray("accepted")

    _rewrite_attempt_and_rehash_pass(forged, publish_late)

    def publish_late_index(rows):
        rows[0]["hr_accepted"] = "1"
        rows[0]["display_hr_bpm"] = str(float(executed.result.dsp["hr_raw"]))
        rows[0]["display_hr_state"] = "accepted"

    _rewrite_index_and_rehash(forged, publish_late_index)

    def publish_late_event(events):
        applied = next(item for item in events if item["kind"] == "result_applied")
        applied["hr_accepted"] = True
        applied["hr_state"] = "accepted"

    _rewrite_events_and_rehash(forged, publish_late_event)
    issues = verify_replay_artifacts(forged)
    assert any(
        token in (issue.code + issue.details).lower()
        for issue in issues
        for token in ("expired", "publication", "age")
    )


@pytest.mark.parametrize(
    "selection_payload",
    [
        {
            "selected_bin": 1,
            "evidence": {"fallback_used": True},
            "selector_succeeded": True,
            "dsp_succeeded": True,
            "fallback_used": True,
            "reason": "no_energy_eligible_dsp",
        },
        {
            "selected_bin": 1,
            "evidence": {"fallback_used": True},
            "selector_succeeded": True,
            "dsp_succeeded": False,
            "fallback_used": True,
            "reason": "all_dsp_failed",
        },
    ],
)
def test_verifier_recomputes_selector_refusal_even_after_consistent_rehash(
    tmp_path, selection_payload
):
    writer = _writer(tmp_path)
    executed = _authentic_executed()
    disposition = _authentic_disposition(executed)
    assert disposition.hr_accepted or disposition.br_accepted
    _write_requested_attempt(writer, executed, disposition)
    _finalize(writer)
    _rewrite_attempt_and_rehash_pass(
        writer.pass_dir,
        lambda payload: payload.__setitem__(
            "selection_decision_json",
            np.asarray(
                json.dumps(selection_payload, sort_keys=True, separators=(",", ":"))
            ),
        ),
    )

    issues = verify_replay_artifacts(writer.pass_dir)
    assert any(
        "selection" in issue.details.lower() or "fallback" in issue.details.lower()
        for issue in issues
    )


@pytest.mark.parametrize(
    "forged_flag",
    [
        np.asarray(1, dtype=np.int64),
        np.asarray("false"),
        np.asarray([False], dtype=np.bool_),
    ],
    ids=["integer", "string", "nonscalar"],
)
def test_exceptional_evidence_flag_requires_scalar_boolean_dtype(
    tmp_path, forged_flag
):
    writer = _writer(tmp_path)
    executed = _authentic_executed()
    _write_requested_attempt(writer, executed, _authentic_disposition(executed))
    _finalize(writer)
    _rewrite_attempt_and_rehash_pass(
        writer.pass_dir,
        lambda payload: payload.__setitem__("exceptional_evidence", forged_flag),
    )

    issues = verify_replay_artifacts(writer.pass_dir)
    assert any("exception" in (issue.code + issue.details).lower() for issue in issues)


def test_exceptional_record_cannot_bypass_with_fake_nonempty_scientific_arrays(
    tmp_path,
):
    writer = _writer(tmp_path)
    executed = _executed()
    disposition = replace(
        _disposition(),
        request_epoch=executed.result.epoch,
        request_frame_start=executed.result.frame_start,
        request_frame_stop=executed.result.frame_stop,
        requested_bin=executed.result.actual_bin,
        selection_mode="committed",
        requested_selection_revision=executed.result.selection_revision,
    )
    _write_requested_attempt(writer, executed, disposition)
    _finalize(writer)

    issues = verify_replay_artifacts(writer.pass_dir)
    assert any("exception" in (issue.code + issue.details).lower() for issue in issues)


def test_verifier_rejects_every_csv_npz_lineage_mismatch(tmp_path):
    writer = _writer(tmp_path / "base")
    executed = _authentic_executed()
    _write_requested_attempt(writer, executed, _authentic_disposition(executed))
    _finalize(writer)
    corruptions = {
        "generation": "99",
        "epoch": "9",
        "requested_selection_revision": "7",
        "applied_selection_revision": "8",
        "stage": "unexpected-stage",
        "frame_start": "1",
        "frame_stop": "599",
        "requested_bin": "7",
        "selection_mode": "select",
        "actual_bin": "7",
        "execution_status": "failed",
        "disposition": "expired",
        "application_boundary": "601",
        "application_epoch": "9",
        "hr_accepted": "0",
        "br_accepted": "0",
    }
    for field, forged in corruptions.items():
        case = tmp_path / f"csv_{field}"
        shutil.copytree(writer.pass_dir, case)
        _rewrite_index_and_rehash(
            case, lambda rows, f=field, v=forged: rows[0].__setitem__(f, v)
        )
        issues = verify_replay_artifacts(case)
        assert issues, f"verifier accepted forged CSV field {field!r}"


def test_verifier_rejects_request_and_terminal_event_lineage_mismatches(tmp_path):
    writer = _writer(tmp_path / "base")
    executed = _authentic_executed()
    _write_requested_attempt(writer, executed, _authentic_disposition(executed))
    _finalize(writer)
    corruptions = (
        ("analysis_requested", "frame_stop", 599),
        ("analysis_requested", "selection_mode", "select"),
        ("result_applied", "epoch", 9),
        ("result_applied", "generation", 9),
        ("result_applied", "selection_revision", 9),
        ("result_applied", "selected_bin", 7),
        ("result_applied", "hr_state", "held"),
        ("result_applied", "br_state", "missing"),
        ("result_applied", "disposition", "expired"),
        ("result_applied", "hr_accepted", False),
        ("result_applied", "br_accepted", False),
        ("result_applied", "boundary", 599),
        ("result_applied", "boundary", 661),
        ("attempt_terminal", "boundary", 601),
        ("attempt_terminal", "execution_status", "failed"),
        ("attempt_terminal", "disposition", "expired"),
    )
    for kind, field, forged in corruptions:
        case = tmp_path / f"event_{kind}_{field}_{forged}"
        shutil.copytree(writer.pass_dir, case)

        def mutate(events, *, expected_kind=kind, name=field, value=forged):
            event = next(item for item in events if item["kind"] == expected_kind)
            event[name] = value

        _rewrite_events_and_rehash(case, mutate)
        issues = verify_replay_artifacts(case)
        assert issues, f"verifier accepted forged {kind}.{field}"

    duplicate_case = tmp_path / "event_duplicate_request"
    shutil.copytree(writer.pass_dir, duplicate_case)

    def duplicate_request(events):
        request = next(item for item in events if item["kind"] == "analysis_requested")
        events.insert(1, dict(request))

    _rewrite_events_and_rehash(duplicate_case, duplicate_request)
    issues = verify_replay_artifacts(duplicate_case)
    assert any("duplicate" in (issue.code + issue.details).lower() for issue in issues)

    for label, mutate in (
        (
            "missing_request_epoch",
            lambda events: next(
                item for item in events if item["kind"] == "analysis_requested"
            ).pop("epoch"),
        ),
        (
            "unexpected_application_field",
            lambda events: next(
                item for item in events if item["kind"] == "result_applied"
            ).__setitem__("invented", 1),
        ),
    ):
        case = tmp_path / label
        shutil.copytree(writer.pass_dir, case)
        _rewrite_events_and_rehash(case, mutate)
        issues = verify_replay_artifacts(case)
        assert any("event" in (issue.code + issue.details).lower() for issue in issues)


def test_verifier_rejects_orphan_request_after_consistent_attempt_removal(tmp_path):
    writer = _writer(tmp_path)
    executed = _authentic_executed()
    record = _write_requested_attempt(
        writer, executed, _authentic_disposition(executed)
    )
    _finalize(writer)

    _rewrite_index_and_rehash(writer.pass_dir, lambda rows: rows.clear())
    _rewrite_events_and_rehash(
        writer.pass_dir,
        lambda events: events.__setitem__(
            slice(None),
            [event for event in events if event["kind"] != "attempt_terminal"],
        ),
    )
    relative = record.npz_path.relative_to(writer.pass_dir).as_posix()
    record.npz_path.unlink()
    manifest_path = writer.pass_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].pop(relative)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    issues = verify_replay_artifacts(writer.pass_dir)
    assert any("request" in (issue.code + issue.details).lower() for issue in issues)


def test_verifier_rejects_unindexed_npz_even_when_manifest_hash_is_valid(tmp_path):
    writer = _writer(tmp_path)
    executed = _authentic_executed()
    record = _write_requested_attempt(
        writer, executed, _authentic_disposition(executed)
    )
    _finalize(writer)
    extra = writer.attempt_dir / "attempt_999999_unindexed.npz"
    shutil.copyfile(record.npz_path, extra)
    manifest_path = writer.pass_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    relative = extra.relative_to(writer.pass_dir).as_posix()
    manifest["files"][relative] = _digest(extra)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    issues = verify_replay_artifacts(writer.pass_dir)
    assert any(
        "unindexed" in issue.details.lower() or "cardinality" in issue.details.lower()
        for issue in issues
    )


def test_verifier_rejects_interrupted_temporary_attempt_file(tmp_path):
    writer = _writer(tmp_path)
    executed = _authentic_executed()
    _write_requested_attempt(writer, executed, _authentic_disposition(executed))
    _finalize(writer)
    temporary = writer.attempt_dir / "attempt_000001_interrupted.npz.tmp"
    temporary.write_bytes(b"partial")
    manifest_path = writer.pass_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    relative = temporary.relative_to(writer.pass_dir).as_posix()
    manifest["files"][relative] = _digest(temporary)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )

    issues = verify_replay_artifacts(writer.pass_dir)
    assert any(
        "temporary" in issue.details.lower() or "interrupted" in issue.details.lower()
        for issue in issues
    )


def test_manifest_detects_ordinary_byte_tamper(tmp_path):
    writer = _writer(tmp_path)
    executed = _authentic_executed()
    _write_requested_attempt(writer, executed, _authentic_disposition(executed))
    _finalize(writer)
    summary = writer.pass_dir / "summary.json"
    summary.write_bytes(summary.read_bytes() + b" ")

    issues = verify_replay_artifacts(writer.pass_dir)
    assert any(issue.code == "file_hash" and "summary.json" in issue.details for issue in issues)


def test_manifest_schema_failure_is_reported(tmp_path):
    writer = _writer(tmp_path)
    executed = _authentic_executed()
    _write_requested_attempt(writer, executed, _authentic_disposition(executed))
    manifest_path = _finalize(writer)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema"] = "unknown"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8", newline="\n")

    issues = verify_replay_artifacts(writer.pass_dir)
    assert any(issue.code == "manifest_schema" for issue in issues)
