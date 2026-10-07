"""Frame-driven recovery, display, publication, and regression tests."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from src.live_motion.cache import RangeBinCache
from src.live_motion.config import (
    FeatureThreshold,
    LiveMotionSettings,
    MovementThresholds,
    PresenceThresholds,
)
from src.live_motion.controller import RecoveryController
from src.live_motion.features import FrameObservation, MonitorFeatures
from src.live_motion.scheduler import AnalysisResult, SelectionDecision
from src.live_motion.scheduler import BoundedAnalysisScheduler


def _settings():
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
        extended_breathing_band_hz=(0.05, 0.5),
        candidate_bins=(1, 2),
        presence_thresholds=PresenceThresholds(-10.0, 0.0),
        movement_thresholds=MovementThresholds(
            presence=FeatureThreshold(False, None, None),
            phase_activity=FeatureThreshold(True, 1.0, 0.2),
            range_profile_change=FeatureThreshold(True, 0.8, 0.2),
        ),
        calibration_record={"accepted": True},
        calibration_record_path=None,
        calibration_record_sha256="a" * 64,
    )


def _controller():
    settings = _settings()
    cache = RangeBinCache(1200, 1, 1, settings.candidate_bins)
    return RecoveryController(settings, cache)


def _monitor(start, stop, *, presence=10.0, phase=0.0, profile=0.0, valid=True,
             reason=""):
    return MonitorFeatures(start, stop, valid, presence, phase, profile, reason)


def _frame(index, *, monitor=None, valid=True, reason=""):
    return FrameObservation(
        frame_index=index,
        valid=valid,
        raw_frame=np.full((1, 1, 8), index + 1j, dtype=np.complex64),
        gate_bins=np.array([1, 2], dtype=np.int32),
        gate_samples=np.array([[[1 + 0j, 2 + 0j]]], dtype=np.complex64),
        monitor=monitor,
        invalid_reason=reason,
    )


def _settle(controller):
    updates = []
    for index in range(60):
        monitor = _monitor(index - 19, index + 1) if index >= 19 and (index - 19) % 5 == 0 else None
        updates.append(controller.handle_frame(_frame(index, monitor=monitor)))
    return updates


def _advance(controller, first, stop):
    jobs = []
    for index in range(first, stop):
        update = controller.handle_frame(_frame(index))
        jobs.extend(update.jobs)
    return jobs


def _result(job, *, hr=72.0, br=12.0, hr_valid=True, br_valid=True,
            decision=None, breathing=None, status="completed", error=""):
    return AnalysisResult(
        job_id=job.job_id,
        epoch=job.epoch,
        selection_revision=job.selection_revision,
        stage=job.stage,
        frame_start=job.frame_start,
        frame_stop=job.frame_stop,
        actual_bin=job.requested_bin if job.requested_bin is not None else 1,
        executed=True,
        status=status,
        dsp={
            "hr_valid": hr_valid,
            "br_valid": br_valid,
            "hr_raw": hr,
            "br_bpm": br,
        } if status == "completed" else None,
        breathing=breathing,
        selection_decision=decision,
        error=error,
    )


def _selection(bin_index=1, *, ok=True, dsp=True, fallback=False, reason="passed"):
    return SelectionDecision(bin_index if ok else None, {}, ok, dsp, fallback, reason)


def _complete_to_committed(controller, *, provisional=1, final=1, ordinary_hr=80.0):
    _settle(controller)
    preview = _advance(controller, 60, 260)[0]
    controller.handle_result(_result(preview, decision=_selection(provisional)))
    preview_20 = _advance(controller, 260, 460)[0]
    controller.handle_result(_result(preview_20, hr_valid=False, br_valid=False))
    ordinary = _advance(controller, 460, 660)[0]
    update = controller.handle_result(_result(
        ordinary, hr=ordinary_hr, br=15.0, decision=_selection(final)
    ))
    return update


def test_first_quiet_block_is_retroactively_credited_and_epoch_starts_after_exactly_60_frames():
    controller = _controller()
    updates = _settle(controller)
    assert updates[18].snapshot.reason == "waiting_for_confirmed_target_and_stillness"
    assert updates[19].snapshot.reason == "stillness_confirmation_20_of_60"
    assert updates[54].snapshot.reason == "stillness_confirmation_55_of_60"
    assert updates[59].snapshot.reason == "fresh_epoch_started"
    assert [event.kind for event in updates[59].events] == ["epoch_started"]
    assert updates[59].jobs == ()
    # Settling data is not silently reused as analysis data.
    assert controller.cache.snapshot().frame_indices.size == 0


def test_stage_jobs_use_exact_fresh_half_open_bounds_one_frame_before_on_and_after():
    controller = _controller()
    _settle(controller)
    jobs = _advance(controller, 60, 1261)
    by_stage = {}
    for job in jobs:
        by_stage.setdefault(job.stage, []).append(job)

    assert [(j.frame_start, j.frame_stop) for j in by_stage["preview_10"]] == [(60, 260)]
    assert [(j.frame_start, j.frame_stop) for j in by_stage["preview_20"]] == [(60, 460)]
    assert [(j.frame_start, j.frame_stop) for j in by_stage["ordinary_30"]] == [(60, 660)]
    assert [(j.frame_start, j.frame_stop) for j in by_stage["extended_60"]] == [(60, 1260)]
    assert by_stage["preview_10"][0].raw_frames.shape[0] == 200
    assert by_stage["preview_20"][0].raw_frames.shape[0] == 400
    assert by_stage["ordinary_30"][0].raw_frames.shape[0] == 600
    assert by_stage["extended_60"][0].range_cache_snapshot.samples.shape[0] == 1200
    assert all(job.frame_stop not in {259, 261, 459, 461, 659, 661, 1259, 1261}
               for job in jobs if job.stage != "rolling")


@pytest.mark.parametrize("event", ["invalid", "gap", "target", "movement"])
@pytest.mark.parametrize("fresh_frames", [200, 400, 600, 1200])
def test_disruption_at_exact_stage_boundary_cancels_that_estimate(event, fresh_frames):
    controller = _controller()
    _settle(controller)
    _advance(controller, 60, 60 + fresh_frames - 1)
    index = 60 + fresh_frames - 1
    if event == "invalid":
        update = controller.handle_frame(_frame(index, valid=False, reason="packet_gap"))
    elif event == "gap":
        update = controller.handle_frame(_frame(index + 1))
    elif event == "target":
        update = controller.handle_frame(_frame(index, monitor=_monitor(
            index - 19, index + 1, presence=-10.0
        )))
    else:
        update = controller.handle_frame(_frame(index, monitor=_monitor(
            index - 19, index + 1, phase=1.0
        )))
    assert update.jobs == ()
    assert update.reset_buffers is True
    assert update.snapshot.status in {"settling", "moving"}


def test_previews_require_existing_dsp_validity_and_two_cycles_and_are_amber():
    controller = _controller()
    _settle(controller)
    preview = _advance(controller, 60, 260)[0]

    below_floor = controller.handle_result(_result(
        preview, br=11.999, decision=_selection()
    ))
    assert below_floor.attempts[0].disposition == "invalid"
    assert below_floor.snapshot.hr.state == below_floor.snapshot.br.state == "missing"

    controller = _controller()
    _settle(controller)
    preview = _advance(controller, 60, 260)[0]
    accepted = controller.handle_result(_result(
        preview, br=12.0, decision=_selection()
    ))
    assert accepted.snapshot.hr.state == accepted.snapshot.br.state == "preliminary"
    assert accepted.snapshot.hr.color == accepted.snapshot.br.color == "amber"

    controller = _controller()
    _settle(controller)
    preview = _advance(controller, 60, 260)[0]
    invalid_dsp = controller.handle_result(_result(
        preview, br=20.0, hr_valid=False, br_valid=False, decision=_selection()
    ))
    assert invalid_dsp.snapshot.hr.state == invalid_dsp.snapshot.br.state == "missing"


def test_hr_and_br_replace_independently_and_preview_never_contaminates_ordinary_median():
    controller = _controller()
    _settle(controller)
    preview = _advance(controller, 60, 260)[0]
    controller.handle_result(_result(preview, hr=120, br=12, decision=_selection()))
    preview_20 = _advance(controller, 260, 460)[0]
    controller.handle_result(_result(preview_20, hr_valid=False, br_valid=False))
    ordinary = _advance(controller, 460, 660)
    ordinary_job = next(job for job in ordinary if job.stage == "ordinary_30")
    update = controller.handle_result(_result(
        ordinary_job, hr=80, br=15, decision=_selection(1)
    ))
    assert update.snapshot.hr.value_bpm == 80  # preview 120 was never smoothed
    assert update.snapshot.hr.state == update.snapshot.br.state == "fresh"

    rolling = _advance(controller, 660, 720)[0]
    hr_only = controller.handle_result(_result(
        rolling, hr=100, br=99, hr_valid=True, br_valid=False
    ))
    assert hr_only.snapshot.hr.value_bpm == 90  # median of fresh 80 and 100 only
    assert hr_only.snapshot.br.value_bpm == 15
    assert hr_only.snapshot.br.state == "held"
    assert hr_only.snapshot.br.color == "red"


def test_quiet_assessment_never_displays_zero_or_triggers_relocking():
    controller = _controller()
    _settle(controller)
    preview = _advance(controller, 60, 260)[0]
    controller.handle_result(_result(preview, decision=_selection()))
    preview_20 = _advance(controller, 260, 460)[0]
    controller.handle_result(_result(preview_20, hr_valid=False, br_valid=False))
    ordinary = _advance(controller, 460, 660)[0]
    controller.handle_result(_result(
        ordinary, hr_valid=False, br_valid=False, decision=_selection(1)
    ))
    jobs = _advance(controller, 660, 1260)
    extended = next(job for job in jobs if job.stage == "extended_60")
    for rolling in (job for job in jobs if job.stage == "rolling"):
        controller.handle_result(_result(rolling, hr_valid=False, br_valid=False))
    before = controller.snapshot()
    quiet = SimpleNamespace(state="quiet", value_bpm=float("nan"))
    update = controller.handle_result(_result(
        extended, hr=90, br=0.0, breathing=quiet
    ))
    assert update.snapshot.epoch == before.epoch
    assert update.snapshot.locked_bin == before.locked_bin
    assert update.snapshot.br.value_bpm == 12.0
    assert update.snapshot.br.state == "held"
    assert update.snapshot.breathing_status == "No breathing motion detected"
    assert update.snapshot.hr.state == "held"
    assert update.attempts[0].disposition == "published"
    assert update.attempts[0].quiet_assessment_accepted is True
    assert update.attempts[0].br_accepted is False


@pytest.mark.parametrize(
    ("extended_br", "ahet_hz", "veto_reason", "expected_hr", "hr_state"),
    [
        (3.0, 0.05, "extended_breathing_below_9_bpm", 80.0, "held"),
        (12.0, 0.2, "", 85.0, "fresh"),
        (12.0, 0.1, "extended_ahet_respiration_disagreement", 80.0, "held"),
        (12.0, None, "missing_ahet_respiration_input", 80.0, "held"),
    ],
)
def test_extended_br_and_hr_veto_publish_atomically_in_one_snapshot(
    extended_br, ahet_hz, veto_reason, expected_hr, hr_state
):
    controller = _controller()
    _complete_to_committed(controller, ordinary_hr=80.0)
    jobs = _advance(controller, 660, 1260)
    extended = next(job for job in jobs if job.stage == "extended_60")
    for rolling in (job for job in jobs if job.stage == "rolling"):
        controller.handle_result(_result(rolling, hr_valid=False, br_valid=False))

    breathing = SimpleNamespace(state="positive", value_bpm=extended_br)
    result = _result(extended, hr=90.0, br=15.0, breathing=breathing)
    dsp = dict(result.dsp)
    dsp["hr_veto_reason"] = veto_reason
    if ahet_hz is not None:
        dsp["f_r_hz"] = ahet_hz
    update = controller.handle_result(replace(result, dsp=dsp))

    assert len(update.attempts) == 1
    attempt = update.attempts[0]
    assert attempt.disposition == "published"
    assert attempt.br_accepted is True
    assert attempt.hr_accepted is (veto_reason == "")
    assert update.snapshot == attempt.snapshot
    assert update.snapshot.br.value_bpm == extended_br
    assert update.snapshot.br.state == "fresh"
    assert update.snapshot.hr.value_bpm == expected_hr
    assert update.snapshot.hr.state == hr_state
    assert update.snapshot.hr.color == ("green" if hr_state == "fresh" else "red")
    assert update.snapshot.reason == (veto_reason or "estimate_accepted")


def test_invalid_hr_never_promotes_diagnostic_no_eca_value_during_unresolved_assessment():
    controller = _controller()
    _complete_to_committed(controller, ordinary_hr=80.0)
    jobs = _advance(controller, 660, 1260)
    extended = next(job for job in jobs if job.stage == "extended_60")
    for rolling in (job for job in jobs if job.stage == "rolling"):
        controller.handle_result(_result(rolling, hr_valid=False, br_valid=False))

    breathing = SimpleNamespace(state="unresolved", value_bpm=float("nan"))
    result = _result(
        extended, hr=91.0, br=0.0, hr_valid=False, br_valid=False,
        breathing=breathing,
    )
    dsp = dict(result.dsp)
    dsp["hr_no_eca"] = 123.0
    update = controller.handle_result(replace(result, dsp=dsp))

    assert update.attempts[0].disposition == "invalid"
    assert update.attempts[0].hr_accepted is False
    assert update.snapshot.hr.value_bpm == 80.0
    assert update.snapshot.hr.state == "held"
    assert update.snapshot.hr.color == "red"
    assert update.snapshot.hr.value_bpm != dsp["hr_no_eca"]
    assert update.snapshot.breathing_status == "Breathing activity unresolved"


def test_unresolved_extended_activity_keeps_ordinary_ahet_hr_decision():
    controller = _controller()
    _complete_to_committed(controller, ordinary_hr=80.0)
    jobs = _advance(controller, 660, 1260)
    extended = next(job for job in jobs if job.stage == "extended_60")
    for rolling in (job for job in jobs if job.stage == "rolling"):
        controller.handle_result(_result(rolling, hr_valid=False, br_valid=False))

    breathing = SimpleNamespace(state="unresolved", value_bpm=float("nan"))
    result = _result(
        extended, hr=100.0, br=0.0, hr_valid=True, br_valid=False,
        breathing=breathing,
    )
    dsp = dict(result.dsp)
    dsp["f_r_hz"] = 0.2
    dsp["hr_veto_reason"] = ""
    update = controller.handle_result(replace(result, dsp=dsp))

    assert update.attempts[0].disposition == "published"
    assert update.attempts[0].hr_accepted is True
    assert update.attempts[0].br_accepted is False
    assert update.snapshot.hr.value_bpm == 90.0
    assert update.snapshot.hr.state == "fresh"
    assert update.snapshot.br.value_bpm == 15.0
    assert update.snapshot.br.state == "held"
    assert update.snapshot.breathing_status == "Breathing activity unresolved"


def test_movement_holds_prior_values_red_clears_buffers_and_repeats_recovery():
    controller = _controller()
    _settle(controller)
    preview = _advance(controller, 60, 260)[0]
    controller.handle_result(_result(preview, hr=72, br=12, decision=_selection()))
    moved = controller.handle_frame(_frame(260, monitor=_monitor(241, 261, phase=1.0)))
    assert moved.snapshot.status == "moving"
    assert moved.snapshot.hr.state == moved.snapshot.br.state == "held"
    assert moved.snapshot.hr.color == moved.snapshot.br.color == "red"
    assert moved.reset_buffers
    assert controller.cache.snapshot().frame_indices.size == 0

    # The reset feature window first becomes eligible after 20 post-event frames.
    updates = []
    for index in range(261, 321):
        monitor = _monitor(index - 19, index + 1) if index in range(280, 321, 5) else None
        updates.append(controller.handle_frame(_frame(index, monitor=monitor)))
    starts = [(update.snapshot.frame_index, event) for update in updates for event in update.events
              if event.kind == "epoch_started"]
    assert len(starts) == 1
    assert starts[0][0] == 320


def test_stale_expired_and_superseded_results_never_publish():
    controller = _controller()
    _settle(controller)
    preview = _advance(controller, 60, 260)[0]
    controller.handle_frame(_frame(260, monitor=_monitor(241, 261, phase=1.0)))
    stale = controller.handle_result(_result(preview, decision=_selection()))
    assert stale.attempts[0].disposition == "stale_epoch"
    assert stale.snapshot.hr.state == "missing"

    controller = _controller()
    _settle(controller)
    preview = _advance(controller, 60, 260)[0]
    _advance(controller, 260, 321)  # more than one 60-frame hop after preview stop
    expired = controller.handle_result(_result(preview, decision=_selection()))
    assert expired.attempts[0].disposition == "expired"
    assert expired.snapshot.hr.state == "missing"
    # A valid current selector decision may still unblock dependent CONTROL work;
    # expiry applies to its HR/BR publication and smoothing.
    assert expired.snapshot.locked_bin == 1
    assert expired.snapshot.selection_revision == 1

    controller = _controller()
    _settle(controller)
    preview = _advance(controller, 60, 260)[0]
    forged = replace(_result(preview, decision=_selection()), frame_stop=261)
    rejected = controller.handle_result(forged)
    assert rejected.attempts[0].disposition == "superseded"
    assert rejected.snapshot.hr.state == "missing"

    controller = _controller()
    _settle(controller)
    preview = _advance(controller, 60, 260)[0]
    first = controller.handle_result(_result(preview, decision=_selection()))
    assert first.attempts[0].disposition == "published"
    duplicate = controller.handle_result(_result(preview, decision=_selection()))
    assert duplicate.attempts[0].disposition == "superseded"


def test_obsolete_selection_revision_cannot_mutate_control_state_or_publish():
    controller = _controller()
    _settle(controller)
    preview = _advance(controller, 60, 260)[0]
    accepted = controller.handle_result(_result(preview, decision=_selection(1)))
    assert accepted.snapshot.selection_revision == 1
    assert accepted.snapshot.locked_bin == 1

    preview_20 = _advance(controller, 260, 460)[0]
    obsolete = replace(_result(preview_20), selection_revision=0)
    update = controller.handle_result(obsolete)
    assert update.attempts[0].disposition == "superseded"
    assert update.snapshot.selection_revision == 1
    assert update.snapshot.locked_bin == 1


def test_selector_failure_is_explicit_and_returns_to_target_unconfirmed_settling():
    controller = _controller()
    _settle(controller)
    preview = _advance(controller, 60, 260)[0]
    failure = controller.handle_result(_result(
        preview,
        hr_valid=False,
        br_valid=False,
        decision=_selection(ok=False, reason="all_dsp_failed"),
    ))
    assert failure.reset_buffers
    assert failure.attempts[0].disposition == "failed"
    assert failure.snapshot.status == "settling"
    assert failure.snapshot.reason == "all_dsp_failed"


def test_scheduler_resolved_dispatch_identity_binds_revision_bin_and_actual_bin():
    controller = _controller()
    _settle(controller)
    preview_10 = _advance(controller, 60, 260)[0]
    preview_20 = _advance(controller, 260, 460)[0]
    scheduler = BoundedAnalysisScheduler()
    scheduler.submit(preview_10)
    scheduler.submit(preview_20)
    active = scheduler.claim_next()
    selection_result = _result(active, decision=_selection(1))
    update = controller.handle_result(selection_result)
    scheduler.complete(selection_result)
    resolution = update.dependency_resolutions[0]
    scheduler.resolve_dependency(
        resolution.dependency_job_id,
        selected_bin=resolution.selected_bin,
        selection_revision=resolution.selection_revision,
    )
    resolved = scheduler.claim_next()
    assert resolved.requested_bin == 1
    assert resolved.selection_revision == 1
    controller.register_dispatched_job(resolved)
    with pytest.raises(ValueError, match="identity|bounds"):
        controller.register_dispatched_job(replace(resolved, frame_stop=461))

    wrong_bin = replace(_result(resolved), actual_bin=2)
    rejected = controller.handle_result(wrong_bin)
    assert rejected.attempts[0].disposition == "superseded"
    assert rejected.attempts[0].reason in {
        "actual_bin_mismatch", "job_identity_or_bounds_mismatch"
    }
    assert rejected.snapshot.br.state == "missing"
    assert np.isnan(rejected.snapshot.br.value_bpm)

    # A terminal corrupt completion is evidence, not a permanent head-of-line poison.
    ordinary = _advance(controller, 460, 660)[0]
    progressed = controller.handle_result(_result(
        ordinary, hr_valid=False, br_valid=False, decision=_selection(1)
    ))
    assert len(progressed.attempts) == 1
    assert all(event.kind != "result_buffered" for event in progressed.events)


def test_out_of_order_results_release_in_order_with_distinct_immutable_snapshots():
    controller = _controller()
    _complete_to_committed(controller, ordinary_hr=80.0)
    first = _advance(controller, 660, 720)[0]
    second = _advance(controller, 720, 780)[0]

    buffered = controller.handle_result(_result(second, hr=60.0, br=15.0))
    assert buffered.attempts == ()
    assert [event.kind for event in buffered.events] == ["result_buffered"]
    assert buffered.snapshot.hr.value_bpm == 80.0

    released = controller.handle_result(_result(first, hr=100.0, br=15.0))
    assert [attempt.result.job_id for attempt in released.attempts] == [
        first.job_id, second.job_id
    ]
    assert [attempt.snapshot.hr.value_bpm for attempt in released.attempts] == [90.0, 80.0]
    assert released.attempts[0].snapshot is not released.attempts[1].snapshot
    assert released.snapshot.hr.value_bpm == 80.0


def test_cancelled_buffered_job_is_removed_and_late_completion_is_superseded():
    controller = _controller()
    _complete_to_committed(controller)
    first = _advance(controller, 660, 720)[0]
    second = _advance(controller, 720, 780)[0]
    controller.handle_result(_result(second, hr=60.0, br=15.0))

    cancelled = controller.cancel_job(second.job_id, "rolling_coalesced")
    assert [(e.kind, e.job_id, e.reason) for e in cancelled.events] == [
        ("job_cancelled", second.job_id, "rolling_coalesced")
    ]
    completed = controller.handle_result(_result(first, hr=90.0, br=15.0))
    assert len(completed.attempts) == 1
    late = controller.handle_result(_result(second, hr=60.0, br=15.0))
    assert late.attempts[0].disposition == "superseded"


def test_invalid_frame_resets_partial_stillness_and_hysteresis_band_does_not_clear_motion():
    controller = _controller()
    for index in range(20):
        monitor = _monitor(0, 20) if index == 19 else None
        controller.handle_frame(_frame(index, monitor=monitor))
    reset = controller.handle_frame(_frame(20, valid=False, reason="packet_gap"))
    assert reset.reset_buffers
    assert reset.snapshot.reason == "packet_gap"

    starts = []
    for index in range(21, 81):
        monitor = _monitor(index - 19, index + 1) if index in range(40, 81, 5) else None
        update = controller.handle_frame(_frame(index, monitor=monitor))
        starts.extend(e for e in update.events if e.kind == "epoch_started")
    assert len(starts) == 1
    assert controller.snapshot().frame_index == 80

    movement = controller.handle_frame(_frame(81, monitor=_monitor(62, 82, phase=1.0)))
    assert movement.snapshot.status == "moving"
    in_hysteresis = controller.handle_frame(_frame(
        82, monitor=_monitor(63, 83, phase=0.5)
    ))
    assert in_hysteresis.snapshot.status == "moving"
    assert in_hysteresis.snapshot.reason == "physical_movement"


def test_final_bin_change_clears_preview_state_and_fresh_median_before_replacement():
    controller = _controller()
    _settle(controller)
    preview = _advance(controller, 60, 260)[0]
    controller.handle_result(_result(preview, hr=120.0, br=12.0, decision=_selection(1)))
    preview_20 = _advance(controller, 260, 460)[0]
    controller.handle_result(_result(preview_20, hr_valid=False, br_valid=False))
    ordinary = _advance(controller, 460, 660)[0]
    changed = controller.handle_result(replace(
        _result(ordinary, hr_valid=False, br_valid=False, decision=_selection(2)),
        actual_bin=2,
    ))
    assert [event.kind for event in changed.events] == [
        "final_bin_changed", "final_bin_selected"
    ]
    selection_event = changed.events[-1]
    assert selection_event.selected_bin == 2
    assert selection_event.selection_revision == 2
    assert selection_event.signal_frame_stop == 660
    assert changed.snapshot.locked_bin == 2
    assert changed.snapshot.hr.value_bpm == 120.0
    assert changed.snapshot.hr.state == "held"
    assert changed.snapshot.hr.color == "red"

    rolling = _advance(controller, 660, 720)[0]
    fresh = controller.handle_result(_result(rolling, hr=80.0, br=15.0))
    assert fresh.snapshot.hr.value_bpm == 80.0


def test_fallback_from_all_dsp_failed_selection_never_promotes_numeric_preview():
    controller = _controller()
    _settle(controller)
    preview = _advance(controller, 60, 260)[0]
    fallback = _selection(
        1, ok=True, dsp=False, fallback=True, reason="all_candidate_dsp_failed"
    )
    update = controller.handle_result(_result(preview, hr=88.0, br=18.0, decision=fallback))
    assert update.snapshot.locked_bin == 1  # diagnostic/control fallback is recorded
    assert update.snapshot.hr.state == update.snapshot.br.state == "missing"
    assert update.attempts[0].disposition == "invalid"


def test_select_stage_missing_decision_fails_explicitly_and_resets():
    controller = _controller()
    _settle(controller)
    preview = _advance(controller, 60, 260)[0]
    update = controller.handle_result(_result(preview, decision=None))
    assert update.reset_buffers
    assert update.attempts[0].disposition == "failed"
    assert update.attempts[0].reason == "selector_result_missing"
    assert [event.kind for event in update.events] == ["selection_failed"]
    assert update.snapshot.status == "settling"


def test_nonselect_stage_with_unexpected_selection_decision_is_superseded():
    controller = _controller()
    _settle(controller)
    preview = _advance(controller, 60, 260)[0]
    controller.handle_result(_result(preview, decision=_selection(1)))
    preview_20 = _advance(controller, 260, 460)[0]
    update = controller.handle_result(_result(preview_20, decision=_selection(1)))
    assert update.attempts[0].disposition == "superseded"
    assert update.attempts[0].reason == "job_identity_or_bounds_mismatch"
    assert update.snapshot.selection_revision == 1
    ordinary = _advance(controller, 460, 660)[0]
    progressed = controller.handle_result(_result(
        ordinary, hr_valid=False, br_valid=False, decision=_selection(1)
    ))
    assert len(progressed.attempts) == 1
    assert all(event.kind != "result_buffered" for event in progressed.events)


def test_selected_bin_outside_candidate_gate_is_rejected_before_control_mutation():
    controller = _controller()
    _settle(controller)
    preview = _advance(controller, 60, 260)[0]
    outside = replace(_result(preview, decision=_selection(99)), actual_bin=99)
    update = controller.handle_result(outside)
    assert update.attempts[0].disposition in {"failed", "superseded"}
    assert update.snapshot.locked_bin is None
    assert update.snapshot.selection_revision == 0


def test_current_analysis_failure_holds_both_prior_values_red_without_epoch_reset():
    controller = _controller()
    accepted = _complete_to_committed(controller, ordinary_hr=80.0)
    before_epoch = accepted.snapshot.epoch
    rolling = _advance(controller, 660, 720)[0]
    failed = controller.handle_result(_result(
        rolling, status="failed", error="worker_failed"
    ))
    assert failed.attempts[0].disposition == "failed"
    assert failed.snapshot.epoch == before_epoch
    assert failed.reset_buffers is False
    assert failed.snapshot.reason == "worker_failed"
    assert failed.snapshot.hr.value_bpm == 80.0
    assert failed.snapshot.br.value_bpm == 15.0
    assert failed.snapshot.hr.state == failed.snapshot.br.state == "held"
    assert failed.snapshot.hr.color == failed.snapshot.br.color == "red"
