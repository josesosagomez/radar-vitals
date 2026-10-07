"""Atomic evidence, typed serialization, and adversarial verification tests."""
from __future__ import annotations

import csv
from dataclasses import replace

import numpy as np
import pytest

from src.live_motion.controller import (
    AttemptDisposition,
    ControllerEvent,
    DisplaySnapshot,
    DisplayValue,
)
from src.live_motion.evidence import (
    EXTENDED_ANALYSIS_FIELDS,
    REQUIRED_ANALYSIS_FIELDS,
    AttemptEvidenceWriter,
    sha256_file,
    verify_development_evidence,
)
from src.live_motion.runtime import _exception_evidence
from src.live_motion.scheduler import AnalysisJob, AnalysisResult, SelectionDecision


HASHES = ("1" * 64, "2" * 64, "3" * 64)


def _snapshot(*, revision=4, hr_state="held", br_state="fresh"):
    return DisplaySnapshot(
        frame_index=659,
        epoch=2,
        status="active",
        reason="estimate_accepted",
        hr=DisplayValue(71.0, hr_state, "red" if hr_state == "held" else "green"),
        br=DisplayValue(15.0, br_state, "green"),
        breathing_status="",
        locked_bin=24,
        selection_revision=revision,
    )


def _result(job_id="job-1", *, stage="ordinary_30", revision=4, executed=True,
            status="completed", decision=None):
    frame_stop = 1260 if stage == "extended_60" else 660
    return AnalysisResult(
        job_id=job_id,
        epoch=2,
        selection_revision=revision,
        stage=stage,
        frame_start=60,
        frame_stop=frame_stop,
        actual_bin=24,
        executed=executed,
        status=status,
        dsp={"hr_raw": 73.0, "br_bpm": 16.0, "hr_valid": True, "br_valid": True}
        if status == "completed" else None,
        selection_decision=decision,
        error="worker_failed" if status != "completed" else "",
    )


def _evidence(*, extended=False):
    values = {
        "phase_raw": np.linspace(0, 1, 600, dtype=np.float64),
        "phase_clean": np.linspace(0, 0.5, 600, dtype=np.float64),
        "heart_freqs_hz": np.array([0.8, 1.2]),
        "heart_spectrum": np.array([2.0, 7.0]),
        "resp_freqs_hz": np.array([0.2, 0.25]),
        "resp_spectrum": np.array([8.0, 3.0]),
        "selected_peak_bins": np.array([12, 72], dtype=np.int32),
        "respiration_inputs_hz": np.array([0.2, 0.2]),
        "activity_resp_projection": np.array([], dtype=np.float64),
        "activity_subband_projection": np.array([], dtype=np.float64),
        "coherence_scores": np.array([], dtype=np.float64),
        "thresholds": np.array([3.0, 4.0]),
        "rejection_reasons": np.array(["passed"]),
        "corrected_range_m": 24 * 0.0436 - 0.0784329,
        "hr_window_start": 60,
        "br_window_start": 60,
        "exceptional_evidence": False,
    }
    assert REQUIRED_ANALYSIS_FIELDS.issubset(values)
    if extended:
        for name in EXTENDED_ANALYSIS_FIELDS:
            values[name] = np.array([], dtype=np.float64)
        values["extended_phase"] = np.linspace(0, 1, 1200)
        values["extended_fft_freqs_hz"] = np.linspace(0, 10, 601)
        values["extended_fft_spectrum"] = np.ones(601)
        values["extended_selected_peak_bins"] = np.array([3], dtype=np.int32)
        values["respiratory_projection"] = np.zeros(1200)
        values["subband_projection"] = np.zeros(1200)
        values["respiratory_block_rms"] = np.zeros(6)
        values["subband_block_rms"] = np.zeros(6)
        values["persistence_half_scores"] = np.array([0.9, 0.8])
        values["persistence_half_energy"] = np.ones(2)
        values["persistence_basis_norms"] = np.ones((2, 2))
        values["persistence_coefficients"] = np.ones((2, 2))
        values["persistence_bases"] = np.ones((2, 2, 600))
        values["persistence_centered_halves"] = np.ones((2, 600))
        values["extended_threshold_values"] = np.ones(7)
        values["positive_rejections"] = np.array(["passed"])
        values["quiet_rejections"] = np.array(["periodic_rate_accepted"])
        values["hr_window_start"] = 660
        values["br_window_start"] = 60
    return values


def _writer(tmp_path, injector=None):
    return AttemptEvidenceWriter(tmp_path, *HASHES, failure_injector=injector)


def _codes(root):
    return {issue.code for issue in verify_development_evidence(root)}


def _rewrite_payload_and_accept_new_hash(root, mutate):
    index_path = root / "analysis_attempts.csv"
    with index_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields, rows = reader.fieldnames, list(reader)
    payload_path = root / rows[0]["npz_file"]
    with np.load(payload_path, allow_pickle=False) as archive:
        payload = {name: np.array(archive[name], copy=True) for name in archive.files}
    mutate(payload)
    with payload_path.open("wb") as handle:
        np.savez(handle, **payload)
    rows[0]["npz_sha256"] = sha256_file(payload_path)
    with index_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_writer_closes_hashes_and_indexes_npz_before_csv(tmp_path):
    checkpoints = []

    def inspect(point):
        checkpoints.append(point)
        csv_path = tmp_path / "analysis_attempts.csv"
        if point == "after_npz_close":
            assert not csv_path.exists()
            assert len(list((tmp_path / "analysis_attempts").glob("*.tmp"))) == 1
        elif point == "after_npz_replace":
            assert not csv_path.exists()
            assert len(list((tmp_path / "analysis_attempts").glob("*.npz"))) == 1
        elif point == "before_csv_append":
            assert not csv_path.exists()

    result = _result()
    attempt = AttemptDisposition(
        result, "published", "estimate_accepted", hr_accepted=True, br_accepted=True
    )
    record = _writer(tmp_path, inspect).write_attempt(attempt, _snapshot(), _evidence())

    assert checkpoints == ["before_npz_write", "after_npz_close", "after_npz_replace",
                           "before_csv_append"]
    assert record.npz_sha256 == sha256_file(record.npz_path)
    assert verify_development_evidence(tmp_path) == []
    with np.load(record.npz_path, allow_pickle=False) as payload:
        assert REQUIRED_ANALYSIS_FIELDS.issubset(payload.files)
        assert payload["phase_raw"].dtype == np.float64
        assert payload["actual_bin"].item() == 24
        assert payload["fresh_hr_bpm"].item() == 73.0
        assert payload["fresh_br_bpm"].item() == 16.0
        assert payload["held_hr_bpm"].item() == 71.0
        assert np.isnan(payload["held_br_bpm"].item())


@pytest.mark.parametrize("disposition", ["invalid", "failed", "expired", "superseded"])
def test_every_executed_nonpublished_attempt_keeps_evidence_and_invalid_fresh_fields(
    tmp_path, disposition
):
    result = _result(job_id=f"job-{disposition}", status=(
        "failed" if disposition == "failed" else "completed"
    ))
    record = _writer(tmp_path).write_attempt(
        AttemptDisposition(result, disposition, disposition), _snapshot(), _evidence()
    )
    with np.load(record.npz_path, allow_pickle=False) as payload:
        assert payload["executed"].item() is True
        assert np.isnan(payload["fresh_hr_bpm"].item())
        assert np.isnan(payload["fresh_br_bpm"].item())
        np.testing.assert_array_equal(payload["phase_raw"], _evidence()["phase_raw"])


def test_cancelled_before_execution_is_an_event_without_fabricated_signal_npz(tmp_path):
    writer = _writer(tmp_path)
    writer.write_event(ControllerEvent(
        "job_cancelled", "movement", 400, 2, "never-executed"
    ))
    assert not writer.csv_path.exists()
    assert list(writer.attempt_dir.glob("*.npz")) == []
    rows = list(csv.DictReader(writer.events_path.open(newline="", encoding="utf-8")))
    assert [(row["kind"], row["job_id"], row["reason"]) for row in rows] == [
        ("job_cancelled", "never-executed", "movement")
    ]


def test_interrupted_extended_attempt_requires_complete_typed_empty_schema(tmp_path):
    writer = _writer(tmp_path)
    selector_evidence = {
        "selected_bin": 24,
        "fallback_used": False,
        "selection_reason": "eligible_dsp_pass",
        "candidates": [{"bin": 24, "dsp_passed": True}],
    }
    prior = _result(
        job_id="final-selection",
        stage="ordinary_30",
        revision=3,
        decision=SelectionDecision(
            24, selector_evidence, True, True, False, "eligible_dsp_pass"
        ),
    )
    writer.write_event(ControllerEvent(
        "final_bin_selected", "eligible_dsp_pass", 659, 2,
        "final-selection", 24, 4, 660,
    ))
    writer.write_attempt(
        AttemptDisposition(
            prior,
            "published",
            "estimate_accepted",
            hr_accepted=True,
            br_accepted=True,
            selection_applied=True,
            selection_revision=4,
            snapshot=_snapshot(),
        ),
        _snapshot(),
        _evidence(),
    )
    result = _result(job_id="extended-interrupted", stage="extended_60", status="interrupted")
    job = AnalysisJob(
        job_id=result.job_id,
        epoch=result.epoch,
        selection_revision=result.selection_revision,
        stage="extended_60",
        frame_start=60,
        frame_stop=1260,
        requested_bin=24,
        selection_mode="committed",
        raw_frames=np.zeros((600, 1, 1, 8), dtype=np.complex64),
    )
    evidence = _exception_evidence(
        job,
        {"profile": {"range_resolution_m": 0.0436, "range_bias_m": 0.0784329}},
        "worker_shutdown",
        24,
    )
    record = writer.write_attempt(
        AttemptDisposition(result, "failed", "worker_shutdown"), _snapshot(), evidence
    )
    with np.load(record.npz_path, allow_pickle=False) as payload:
        assert EXTENDED_ANALYSIS_FIELDS.issubset(payload.files)
        assert payload["exceptional_evidence"].item() is True
    assert verify_development_evidence(tmp_path) == []


def test_writer_rejects_silent_required_field_omission_collision_and_object_dtype(tmp_path):
    result = _result()
    attempt = AttemptDisposition(result, "published", "passed")
    missing = _evidence()
    missing.pop("phase_raw")
    with pytest.raises(ValueError, match="phase_raw"):
        _writer(tmp_path).write_attempt(attempt, _snapshot(), missing)

    collision = _evidence()
    collision["job_id"] = "forged"
    with pytest.raises(ValueError, match="collides"):
        _writer(tmp_path).write_attempt(attempt, _snapshot(), collision)

    objects = _evidence()
    objects["thresholds"] = [{"not": "primitive"}]
    with pytest.raises(TypeError, match="object dtype"):
        _writer(tmp_path).write_attempt(attempt, _snapshot(), objects)


@pytest.mark.parametrize("failure_point", ["after_npz_close", "after_npz_replace",
                                             "before_csv_append"])
def test_interrupted_write_never_creates_a_partial_index_row(tmp_path, failure_point):
    def fail(point):
        if point == failure_point:
            raise RuntimeError("injected crash")

    with pytest.raises(RuntimeError, match="injected"):
        _writer(tmp_path, fail).write_attempt(
            AttemptDisposition(_result(), "published", "passed"), _snapshot(), _evidence()
        )
    assert not (tmp_path / "analysis_attempts.csv").exists()
    assert verify_development_evidence(tmp_path)


def test_verifier_rejects_npz_tamper_or_unindexed_extra_attempt(tmp_path):
    record = _writer(tmp_path).write_attempt(
        AttemptDisposition(_result(), "published", "passed"), _snapshot(), _evidence()
    )
    with record.npz_path.open("ab") as handle:
        handle.write(b"tamper")
    assert "npz_hash_mismatch" in _codes(tmp_path)

    other = tmp_path / "other"
    record = _writer(other).write_attempt(
        AttemptDisposition(_result(), "published", "passed"), _snapshot(), _evidence()
    )
    np.savez(other / "analysis_attempts" / "unindexed.npz", x=np.array([1]))
    assert "attempt_npz_cardinality" in _codes(other)


@pytest.mark.parametrize(
    "field,new_value",
    [
        ("epoch", "9"),
        ("requested_selection_revision", "9"),
        ("selection_revision", "9"),
        ("stage", "preview_10"),
        ("frame_start", "61"),
        ("frame_stop", "661"),
        ("actual_bin", "25"),
        ("config_sha256", "9" * 64),
        ("source_sha256", "9" * 64),
        ("calibration_sha256", "9" * 64),
    ],
)
def test_verifier_rejects_csv_npz_identity_or_hash_field_mismatch(tmp_path, field, new_value):
    _writer(tmp_path).write_attempt(
        AttemptDisposition(_result(), "published", "passed"), _snapshot(), _evidence()
    )
    path = tmp_path / "analysis_attempts.csv"
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields, rows = reader.fieldnames, list(reader)
    rows[0][field] = new_value
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    assert verify_development_evidence(tmp_path), field


def test_verifier_rejects_unexplained_selection_revision_jump(tmp_path):
    writer = _writer(tmp_path)
    writer.write_attempt(
        AttemptDisposition(_result(job_id="first", revision=0), "published", "passed"),
        _snapshot(revision=0), _evidence(),
    )
    decision = SelectionDecision(24, {}, True, True, False, "passed")
    writer.write_attempt(
        AttemptDisposition(
            _result(job_id="jump", revision=0, decision=decision), "published", "passed"
        ),
        _snapshot(revision=3), _evidence(),
    )
    assert "unexplained_selection_revision" in _codes(tmp_path)


def test_hr_and_br_window_starts_remain_separate_in_index_and_npz(tmp_path):
    evidence = _evidence(extended=True)
    record = _writer(tmp_path).write_attempt(
        AttemptDisposition(
            _result(stage="extended_60"), "published", "passed",
            hr_accepted=True, br_accepted=True,
        ),
        _snapshot(), evidence,
    )
    with np.load(record.npz_path, allow_pickle=False) as payload:
        assert payload["hr_window_start"].item() == 660
        assert payload["br_window_start"].item() == 60
    row = next(csv.DictReader((tmp_path / "analysis_attempts.csv").open(
        newline="", encoding="utf-8"
    )))
    assert row["hr_window_start"] == "660"
    assert row["br_window_start"] == "60"


def test_verifier_rejects_rolling_sixty_second_attempt_with_all_extended_fields_removed(
    tmp_path,
):
    result = replace(
        _result(job_id="rolling-60", stage="rolling"), frame_start=60, frame_stop=1260
    )
    evidence = _evidence(extended=True)
    _writer(tmp_path).write_attempt(
        AttemptDisposition(result, "published", "passed"), _snapshot(), evidence
    )

    def remove_extended(payload):
        for name in EXTENDED_ANALYSIS_FIELDS:
            payload.pop(name)

    _rewrite_payload_and_accept_new_hash(tmp_path, remove_extended)
    assert verify_development_evidence(tmp_path), (
        "a 1200-frame rolling BR window cannot silently become an ordinary-only attempt"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("evidence_version", np.asarray(99, dtype=np.int64)),
        ("executed", np.asarray(False, dtype=np.bool_)),
    ],
)
def test_verifier_rejects_rehashed_version_or_execution_disposition_tamper(
    tmp_path, field, value
):
    _writer(tmp_path).write_attempt(
        AttemptDisposition(_result(), "published", "passed"), _snapshot(), _evidence()
    )
    _rewrite_payload_and_accept_new_hash(tmp_path, lambda payload: payload.__setitem__(field, value))
    assert verify_development_evidence(tmp_path), field
