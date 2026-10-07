"""Deterministic baseline-controller, scheduling, and admission tests."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from src.live_motion.runtime import ExecutedAnalysis
from src.live_motion.scheduler import (
    AnalysisResult,
    BoundedAnalysisScheduler,
    SelectionDecision,
)
from src.replay_compare.engine import BaselineController


FRAME = np.asarray([[[1.0 + 2.0j, 3.0 + 4.0j]]], dtype=np.complex64)


def _feed(controller: BaselineController, count: int, *, frame=FRAME):
    updates = []
    for _ in range(count):
        updates.append(
            controller.handle_frame(controller.processed_boundary, frame, valid=True)
        )
    return updates


def _claim(controller: BaselineController):
    job = controller.claim_pending_job()
    assert job is not None
    controller.register_dispatched_job(job)
    return job


def _executed(
    job,
    *,
    hr=70.0,
    br=12.0,
    hr_valid=True,
    br_valid=True,
    accepted_rank=0,
    ahet_verified=True,
    status="completed",
    actual_bin=None,
    decision=None,
    error="",
):
    if actual_bin is None:
        actual_bin = job.requested_bin
    dsp = None
    if status == "completed":
        dsp = {
            "hr_valid": hr_valid,
            "br_valid": br_valid,
            "hr_raw": hr,
            "br_bpm": br,
            "hr_result": {
                "accepted_candidate_rank": accepted_rank,
                "ahet_verified": ahet_verified,
            },
            "rej_reason": "synthetic_rejection",
            "fallback_hr_bpm": 99.0,
        }
    result = AnalysisResult(
        job_id=job.job_id,
        epoch=job.epoch,
        selection_revision=job.selection_revision,
        stage=job.stage,
        frame_start=job.frame_start,
        frame_stop=job.frame_stop,
        actual_bin=actual_bin,
        executed=True,
        status=status,
        dsp=dsp,
        selection_decision=decision,
        error=error,
    )
    return ExecutedAnalysis(
        result=result,
        evidence={},
        analysis_elapsed_s=0.01,
        selector_elapsed_s=0.002 if job.selection_mode == "select" else None,
    )


def _selection(
    selected_bin=3,
    *,
    selector_succeeded=True,
    dsp_succeeded=True,
    fallback_used=False,
    reason="eligible_dsp_pass",
):
    return SelectionDecision(
        selected_bin=selected_bin,
        evidence={"reason": reason},
        selector_succeeded=selector_succeeded,
        dsp_succeeded=dsp_succeeded,
        fallback_used=fallback_used,
        reason=reason,
    )


def _selected_controller() -> BaselineController:
    controller = BaselineController({}, generation=7)
    _feed(controller, 600)
    job = _claim(controller)
    update = controller.handle_executed(
        _executed(job, actual_bin=3, decision=_selection())
    )
    assert update.disposition is not None and update.disposition.hr_accepted
    return controller


def test_exact_600_frame_window_and_60_frame_hop_contract():
    controller = BaselineController({}, generation=4)
    _feed(controller, 599)
    assert controller.pending_job is None

    update = _feed(controller, 1)[0]
    assert controller.pending_job is not None
    job = controller.pending_job
    assert (job.frame_start, job.frame_stop) == (0, 600)
    assert job.stage == "ordinary_30"
    assert job.selection_mode == "select"
    assert job.raw_frames.shape == (600, 1, 1, 2)
    assert job.raw_frames.flags.writeable is False
    assert [event.kind for event in update.events] == ["analysis_requested"]

    _feed(controller, 59)
    assert controller.processed_boundary == 659
    assert controller.pending_job.job_id == job.job_id
    update = _feed(controller, 1)[0]
    assert controller.pending_job.frame_stop == 660
    assert update.cancelled_job_ids == (job.job_id,)
    assert [event.kind for event in update.events] == [
        "cancelled_before_execution",
        "analysis_requested",
    ]
    assert update.events[0].replacement_job_id == controller.pending_job.job_id


def test_baseline_controller_reuses_the_production_bounded_scheduler():
    controller = BaselineController({}, generation=4)
    assert isinstance(controller._scheduler, BoundedAnalysisScheduler)


def test_successful_selection_reuses_selection_window_dsp_and_fixes_bin():
    controller = BaselineController({}, generation=1)
    _feed(controller, 600)
    job = _claim(controller)
    update = controller.handle_executed(
        _executed(job, actual_bin=4, decision=_selection(4))
    )

    assert controller.selected_bin == 4
    assert controller.selection_revision == 1
    assert update.disposition.hr_accepted is True
    assert update.disposition.br_accepted is True
    assert update.snapshot.hr.value_bpm == pytest.approx(70.0)
    assert update.snapshot.br.value_bpm == pytest.approx(12.0)
    assert update.disposition.application_boundary == 600

    _feed(controller, 60)
    fixed = controller.pending_job
    assert fixed.selection_mode == "committed"
    assert fixed.stage == "rolling"
    assert fixed.requested_bin == 4
    assert fixed.selection_revision == 1
    assert (fixed.frame_start, fixed.frame_stop) == (60, 660)


def test_all_dsp_failed_completed_selector_is_persistable_but_never_locks_or_publishes():
    controller = BaselineController({}, generation=1)
    _feed(controller, 600)
    job = _claim(controller)
    decision = _selection(
        selected_bin=9,
        dsp_succeeded=False,
        fallback_used=True,
        reason="all_dsp_failed",
    )

    update = controller.handle_executed(
        _executed(job, actual_bin=9, decision=decision)
    )

    assert update.disposition is not None
    assert update.disposition.disposition == "rejected"
    assert update.disposition.hr_accepted is False
    assert update.disposition.br_accepted is False
    assert controller.selected_bin is None
    assert controller.selection_revision == 0
    assert update.snapshot.hr.value_bpm is None
    assert update.snapshot.br.value_bpm is None

    _feed(controller, 59)
    assert controller.pending_job is None
    _feed(controller, 1)
    retry = controller.pending_job
    assert retry.selection_mode == "select"
    assert (retry.frame_start, retry.frame_stop) == (60, 660)


def test_energy_fallback_may_lock_bin_but_cannot_promote_selection_window_values():
    controller = BaselineController({}, generation=1)
    _feed(controller, 600)
    job = _claim(controller)
    decision = _selection(
        selected_bin=5,
        dsp_succeeded=True,
        fallback_used=True,
        reason="no_energy_eligible_dsp",
    )

    update = controller.handle_executed(
        _executed(job, actual_bin=5, decision=decision, hr=88.0, br=18.0)
    )

    assert controller.selected_bin == 5
    assert controller.selection_revision == 1
    assert update.disposition.reason == "selection_energy_fallback_not_presentation_eligible"
    assert update.disposition.hr_accepted is False
    assert update.disposition.br_accepted is False
    assert update.snapshot.hr.value_bpm is None
    assert update.snapshot.br.value_bpm is None


def test_selector_actual_bin_mismatch_refuses_lock_and_publication():
    controller = BaselineController(
        {"bin_selection": {"candidate_bins": [3]}}, generation=1
    )
    _feed(controller, 600)
    job = _claim(controller)
    update = controller.handle_executed(
        _executed(job, actual_bin=4, decision=_selection(3))
    )

    assert update.disposition.reason == "selector_actual_bin_mismatch"
    assert update.disposition.hr_accepted is False
    assert update.disposition.br_accepted is False
    assert controller.selected_bin is None


def test_selector_bin_outside_candidate_gate_refuses_lock_and_publication():
    controller = BaselineController(
        {"bin_selection": {"candidate_bins": [3]}}, generation=1
    )
    _feed(controller, 600)
    job = _claim(controller)
    update = controller.handle_executed(
        _executed(job, actual_bin=4, decision=_selection(4))
    )

    assert update.disposition.reason == "selector_bin_outside_candidate_gate"
    assert update.disposition.hr_accepted is False
    assert update.disposition.br_accepted is False
    assert controller.selected_bin is None


def test_successful_selection_cancels_queued_retry_without_false_held_gap():
    controller = BaselineController({}, generation=1)
    _feed(controller, 600)
    first = _claim(controller)
    _feed(controller, 60)
    retry = controller.pending_job
    assert retry is not None and retry.selection_mode == "select"

    update = controller.handle_executed(
        _executed(first, actual_bin=3, decision=_selection(3))
    )

    assert update.cancelled_job_ids == (retry.job_id,)
    assert [event.kind for event in update.events] == [
        "cancelled_before_execution",
        "result_applied",
    ]
    assert all(event.hr_state == "accepted" for event in update.events)
    assert all(event.br_state == "accepted" for event in update.events)
    assert update.snapshot.hr.state == "accepted"
    assert update.snapshot.br.state == "accepted"


def test_busy_selector_keeps_active_job_and_only_newest_bounded_retry():
    controller = BaselineController({}, generation=1)
    _feed(controller, 600)
    active = _claim(controller)

    first_retry_update = _feed(controller, 60)[-1]
    first_retry = controller.pending_job
    assert first_retry is not None
    assert first_retry.frame_stop == 660
    assert first_retry.selection_mode == "select"
    assert controller.active_job is active
    assert first_retry_update.cancelled_job_ids == ()

    replacement_update = _feed(controller, 60)[-1]
    newest_retry = controller.pending_job
    assert newest_retry is not None
    assert newest_retry.frame_stop == 720
    assert newest_retry.selection_mode == "select"
    assert newest_retry.job_id != first_retry.job_id
    assert controller.active_job is active
    assert replacement_update.cancelled_job_ids == (first_retry.job_id,)
    assert [event.kind for event in replacement_update.events] == [
        "cancelled_before_execution",
        "analysis_requested",
    ]
    assert replacement_update.events[0].replacement_job_id == newest_retry.job_id


@pytest.mark.parametrize("status", ["failed", "interrupted"])
def test_selector_exception_or_interruption_cannot_emit_numbers_and_retries(status):
    controller = BaselineController({}, generation=1)
    _feed(controller, 600)
    job = _claim(controller)
    update = controller.handle_executed(
        _executed(job, status=status, error="synthetic selector failure")
    )

    assert update.disposition.reason == "synthetic selector failure"
    assert controller.selected_bin is None
    assert update.snapshot.hr.state == "missing"
    assert update.snapshot.br.state == "missing"
    _feed(controller, 60)
    assert controller.pending_job.selection_mode == "select"


def test_valid_only_hr_median_and_independent_channel_holding():
    controller = _selected_controller()
    _feed(controller, 60)
    first = _claim(controller)
    update = controller.handle_executed(
        _executed(first, hr=999.0, hr_valid=False, br=13.0, br_valid=True)
    )
    assert update.snapshot.hr.value_bpm == pytest.approx(70.0)
    assert update.snapshot.hr.state == "held"
    assert update.snapshot.br.value_bpm == pytest.approx(13.0)
    assert update.snapshot.br.state == "accepted"

    _feed(controller, 60)
    second = _claim(controller)
    update = controller.handle_executed(
        _executed(second, hr=72.0, hr_valid=True, br=0.0, br_valid=True)
    )
    assert update.snapshot.hr.value_bpm == pytest.approx(71.0)
    assert update.snapshot.hr.state == "accepted"
    assert update.snapshot.br.value_bpm == pytest.approx(13.0)
    assert update.snapshot.br.state == "held"
    assert update.disposition.hr_accepted is True
    assert update.disposition.br_accepted is False


@pytest.mark.parametrize("rank", [None, -1, True])
def test_unverified_or_no_eca_hr_never_enters_accepted_median(rank):
    controller = _selected_controller()
    _feed(controller, 60)
    job = _claim(controller)
    update = controller.handle_executed(
        _executed(job, hr=99.0, hr_valid=True, accepted_rank=rank, br_valid=False)
    )

    assert update.disposition.hr_accepted is False
    assert update.snapshot.hr.value_bpm == pytest.approx(70.0)
    assert update.snapshot.hr.state == "held"


def test_hr_marked_valid_without_explicit_ahet_verification_is_never_accepted():
    controller = _selected_controller()
    _feed(controller, 60)
    job = _claim(controller)
    update = controller.handle_executed(
        _executed(
            job,
            hr=99.0,
            hr_valid=True,
            accepted_rank=0,
            ahet_verified=False,
            br_valid=False,
        )
    )

    assert update.disposition.hr_accepted is False
    assert update.snapshot.hr.value_bpm == pytest.approx(70.0)
    assert update.snapshot.hr.state == "held"


@pytest.mark.parametrize("lag", [60, 61])
def test_publication_age_accepts_exactly_one_hop_and_rejects_one_frame_beyond(lag):
    controller = BaselineController({}, generation=1)
    _feed(controller, 600)
    job = _claim(controller)
    _feed(controller, lag)
    update = controller.handle_executed(
        _executed(job, actual_bin=3, decision=_selection(3))
    )

    if lag == 60:
        assert update.disposition.hr_accepted is True
        assert update.disposition.br_accepted is True
    else:
        assert update.disposition.disposition == "expired"
        assert update.disposition.reason == "publication_age_exceeded_one_hop"
        assert controller.selected_bin is None


def test_busy_controller_bounds_one_active_and_replaces_one_pending_window():
    controller = BaselineController({}, generation=1)
    _feed(controller, 600)
    active = _claim(controller)
    assert controller.claim_pending_job() is None
    updates = _feed(controller, 180)

    assert controller.active_job.job_id == active.job_id
    assert controller.pending_job.frame_stop == 780
    coalesced = [
        job_id
        for update in updates
        for job_id in update.cancelled_job_ids
    ]
    assert len(coalesced) == 2
    assert len(set(coalesced)) == 2


def test_dispatched_identity_check_rejects_unclaimed_or_replaced_job():
    controller = BaselineController({}, generation=1)
    _feed(controller, 600)
    pending = controller.pending_job
    with pytest.raises(ValueError, match="claimed baseline job"):
        controller.register_dispatched_job(pending)
    active = _claim(controller)
    with pytest.raises(ValueError, match="claimed baseline job"):
        controller.register_dispatched_job(replace(active, job_id="forged"))


def test_job_owns_immutable_frame_copy():
    controller = BaselineController({}, generation=1)
    mutable = FRAME.copy()
    _feed(controller, 600, frame=mutable)
    job = controller.pending_job
    before = job.raw_frames.copy()
    mutable[...] = 999 + 999j

    np.testing.assert_array_equal(job.raw_frames, before)
    assert job.raw_frames.flags.writeable is False
    with pytest.raises(ValueError):
        job.raw_frames[0, 0, 0, 0] = 0


def test_invalid_frame_resets_selection_history_and_requires_new_contiguous_window():
    controller = _selected_controller()
    update = controller.handle_frame(
        controller.processed_boundary, np.empty((1, 1, 2)), valid=False
    )

    assert controller.epoch == 1
    assert controller.selected_bin is None
    assert controller.selection_revision == 0
    assert update.snapshot.hr.value_bpm == pytest.approx(70.0)
    assert update.snapshot.hr.state == "held"
    assert update.snapshot.br.value_bpm == pytest.approx(12.0)
    assert update.snapshot.br.state == "held"
    assert update.events[0].kind == "data_integrity_reset"
    _feed(controller, 599)
    assert controller.pending_job is None
    _feed(controller, 1)
    assert controller.pending_job.frame_start == 601


def test_old_generation_result_cannot_mutate_restarted_controller():
    old = BaselineController({}, generation=1)
    _feed(old, 600)
    old_job = _claim(old)
    old_result = _executed(old_job, actual_bin=3, decision=_selection(3))

    restarted = BaselineController({}, generation=2)
    _feed(restarted, 600)
    _claim(restarted)
    with pytest.raises(ValueError, match="active baseline job"):
        restarted.handle_executed(old_result)
    assert restarted.snapshot.generation == 2
    assert restarted.selected_bin is None


def test_result_after_finish_is_evidenced_expired_without_publication():
    controller = BaselineController({}, generation=1)
    _feed(controller, 600)
    job = _claim(controller)
    controller.finish()
    update = controller.handle_executed(
        _executed(job, actual_bin=3, decision=_selection(3))
    )

    assert update.disposition.disposition == "expired"
    assert update.disposition.reason == "playback_denominator_frozen"
    assert update.disposition.hr_accepted is False
    assert update.disposition.br_accepted is False


def test_paused_controller_does_not_claim_new_work():
    controller = BaselineController({}, generation=1)
    _feed(controller, 600)
    controller.set_playback_state("paused")
    assert controller.claim_pending_job() is None
    assert controller.pending_job is not None
    controller.set_playback_state("running")
    assert _claim(controller) is not None
