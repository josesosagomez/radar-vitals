"""Deterministic queue, identity, ownership, and shutdown tests."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import numpy as np
import pytest

from src.live_motion.scheduler import AnalysisJob, AnalysisResult, BoundedAnalysisScheduler


def _job(
    job_id: str,
    stage="rolling",
    *,
    epoch=3,
    revision=2,
    start=0,
    stop=600,
    dependency=None,
):
    return AnalysisJob(
        job_id=job_id,
        epoch=epoch,
        selection_revision=revision,
        stage=stage,
        frame_start=start,
        frame_stop=stop,
        requested_bin=24,
        selection_mode="committed" if stage in {"rolling", "extended_60"} else "select",
        raw_frames=np.full((stop - start, 1, 1, 1), int(job_id[-1]), dtype=np.complex64),
        depends_on=dependency,
    )


def _result(job: AnalysisJob, **changes):
    fields = dict(
        job_id=job.job_id,
        epoch=job.epoch,
        selection_revision=job.selection_revision,
        stage=job.stage,
        frame_start=job.frame_start,
        frame_stop=job.frame_stop,
        actual_bin=job.requested_bin,
        executed=True,
        status="completed",
        dsp={"hr_valid": False, "br_valid": False},
    )
    fields.update(changes)
    return AnalysisResult(**fields)


def test_job_owns_a_read_only_copy_of_analysis_samples():
    source = np.arange(12, dtype=np.float32).reshape(3, 1, 1, 4)
    job = AnalysisJob("j", 0, 0, "preview_10", 0, 3, None, "select", source)
    source[...] = -1
    np.testing.assert_array_equal(job.raw_frames.ravel(), np.arange(12))
    assert not job.raw_frames.flags.writeable
    with pytest.raises(ValueError):
        job.raw_frames.flat[0] = 9
    with pytest.raises(FrozenInstanceError):
        job.frame_stop = 4


def test_four_mandatory_slots_are_fifo_and_fifth_is_refused():
    scheduler = BoundedAnalysisScheduler()
    stages = ["preview_10", "preview_20", "ordinary_30", "extended_60"]
    jobs = [_job(str(i), stage, start=0, stop=200 * (i + 1))
            for i, stage in enumerate(stages)]
    for job in jobs:
        assert scheduler.submit(job) == ()
    assert scheduler.pending_counts == (4, 0)
    with pytest.raises(RuntimeError, match="bound"):
        scheduler.submit(_job("5", "ordinary_30"))
    for expected in jobs:
        claimed = scheduler.claim_next()
        assert claimed == expected
        scheduler.complete(_result(claimed))
    assert scheduler.claim_next() is None


def test_rolling_slot_coalesces_only_pending_work_and_logs_replacement():
    scheduler = BoundedAnalysisScheduler()
    old = _job("rolling1")
    new = _job("rolling2", start=60, stop=660)
    scheduler.submit(old)
    events = scheduler.submit(new)
    assert scheduler.pending_counts == (0, 1)
    assert [(e.kind, e.job_id, e.replacement_job_id, e.reason) for e in events] == [
        ("rolling_coalesced", "rolling1", "rolling2", "newer_rolling_request")
    ]
    assert scheduler.claim_next() == new


def test_mandatory_work_precedes_rolling_and_dependency_preserves_stage_order():
    scheduler = BoundedAnalysisScheduler()
    blocked = _job("preview2", "preview_20", start=0, stop=400, dependency="preview1")
    rolling = _job("rolling3")
    scheduler.submit(blocked)
    scheduler.submit(rolling)
    # A blocked mandatory head cannot be bypassed by ordinary rolling work.
    assert scheduler.claim_next() is None
    assert scheduler.resolve_dependency(
        "preview1", selected_bin=27, selection_revision=1
    ) == ("preview2",)
    resolved = scheduler.claim_next()
    assert resolved.job_id == "preview2"
    assert resolved.requested_bin == 27
    assert resolved.selection_revision == 1
    assert resolved.depends_on is None
    scheduler.complete(_result(resolved))
    assert scheduler.claim_next() == rolling


@pytest.mark.parametrize(
    "field,value",
    [
        ("epoch", 99),
        ("selection_revision", 99),
        ("stage", "extended_60"),
        ("frame_start", 1),
        ("frame_stop", 601),
    ],
)
def test_completion_rejects_wrong_identity_or_requested_bounds(field, value):
    scheduler = BoundedAnalysisScheduler()
    job = _job("rolling4")
    scheduler.submit(job)
    scheduler.claim_next()
    with pytest.raises(ValueError, match="identity|bounds"):
        scheduler.complete(replace(_result(job), **{field: value}))
    assert scheduler.active == job


def test_epoch_cancellation_removes_pending_and_invalidates_active_without_fabricating_results():
    scheduler = BoundedAnalysisScheduler()
    active = _job("active0", "ordinary_30", epoch=5)
    pending = _job("pending0", "extended_60", epoch=5)
    other = _job("other0", "preview_10", epoch=6, stop=200)
    scheduler.submit(active)
    scheduler.claim_next()
    scheduler.submit(pending)
    scheduler.submit(other)

    events = scheduler.cancel_epoch(5, "movement")

    assert {(e.kind, e.job_id, e.reason) for e in events} == {
        ("active_invalidated", "active0", "movement"),
        ("cancelled_before_execution", "pending0", "movement"),
    }
    assert scheduler.active == active
    scheduler.complete(_result(active))
    assert scheduler.claim_next() == other


def test_close_cancels_pending_and_refuses_new_work_but_does_not_mutate_active():
    scheduler = BoundedAnalysisScheduler()
    active = _job("active1", "ordinary_30")
    waiting = _job("waiting1", "extended_60")
    rolling = _job("rolling5")
    scheduler.submit(active)
    scheduler.claim_next()
    scheduler.submit(waiting)
    scheduler.submit(rolling)
    events = scheduler.close("operator_shutdown")
    assert {e.job_id for e in events} == {"waiting1", "rolling5"}
    assert all(e.kind == "cancelled_before_execution" for e in events)
    assert scheduler.active == active
    with pytest.raises(RuntimeError, match="closed"):
        scheduler.submit(_job("late9"))


def test_job_validation_rejects_unknown_stages_modes_and_empty_bounds():
    with pytest.raises(ValueError, match="stage"):
        AnalysisJob("x", 0, 0, "unknown", 0, 1, None, "select")
    with pytest.raises(ValueError, match="mode"):
        AnalysisJob("x", 0, 0, "preview_10", 0, 1, None, "unknown")
    with pytest.raises(ValueError, match="bounds"):
        AnalysisJob("x", 0, 0, "preview_10", 1, 1, None, "select")
