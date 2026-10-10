"""Exact application-boundary display coverage and lease regressions."""

from __future__ import annotations

import pytest

from src.replay_compare.coverage import (
    CoverageEvent,
    CoverageLedger,
    CoverageSnapshot,
    reconstruct_coverage,
)


def _advance(ledger: CoverageLedger, stop_boundary: int) -> None:
    while ledger.processed_boundary < stop_boundary:
        ledger.advance_frame()


def test_exact_800_frame_application_lease_regression():
    ledger = CoverageLedger(hop_frames=60)
    _advance(ledger, 665)
    ledger.apply(
        CoverageEvent(
            665,
            "accepted ordinary",
            attempt_id="a0",
            hr_state="accepted",
            br_state="accepted",
        )
    )
    _advance(ledger, 725)
    ledger.apply(
        CoverageEvent(
            725,
            "HR-only renewal",
            attempt_id="a1",
            hr_state="accepted",
            br_state="held",
        )
    )
    _advance(ledger, 750)
    ledger.clip(reason="physical_movement")
    _advance(ledger, 800)

    snapshot = ledger.snapshot
    assert snapshot.denominator_frames == 800
    assert snapshot.hr_accepted_frames == 85
    assert snapshot.br_numeric_frames == 60
    assert snapshot.both_frames == 60
    assert CoverageSnapshot.percentage(85, 800) == pytest.approx(10.625)
    assert CoverageSnapshot.percentage(60, 800) == pytest.approx(7.5)


def test_publication_starts_at_application_boundary_without_window_backfill():
    events = [CoverageEvent(600, "published", "a0", "accepted", "accepted")]

    snapshot = reconstruct_coverage(events, total_frames=665, hop_frames=60)

    assert snapshot.denominator_frames == 665
    assert snapshot.hr_accepted_frames == 60
    assert snapshot.br_numeric_frames == 60
    assert snapshot.both_frames == 60


def test_lease_expires_after_exactly_one_hop_into_held_state():
    ledger = CoverageLedger(hop_frames=60)
    ledger.apply(CoverageEvent(0, "published", "a0", "accepted", "accepted"))
    _advance(ledger, 61)

    assert ledger.snapshot.hr_accepted_frames == 60
    assert ledger.snapshot.br_numeric_frames == 60
    intervals = ledger.finalized_intervals()
    assert any(
        row.channel == "hr"
        and row.start == 0
        and row.stop == 60
        and row.state == "accepted"
        and row.attempt_id == "a0"
        for row in intervals
    )
    assert any(
        row.channel == "hr"
        and row.start == 60
        and row.stop == 61
        and row.state == "held"
        and row.reason == "display_lease_expired"
        for row in intervals
    )


def test_expiry_state_is_observable_at_exact_boundary_and_exact_eof():
    ledger = CoverageLedger(hop_frames=60)
    ledger.apply(CoverageEvent(0, "published", "a0", "accepted", "accepted"))
    _advance(ledger, 59)
    assert ledger.snapshot.hr_state == "accepted"
    assert ledger.snapshot.br_state == "accepted"

    # Advancing the final covered frame lands exactly at the half-open end.
    # A paused worker or EOF at this boundary must already present held state;
    # it cannot wait for a nonexistent sixty-first frame.
    ledger.advance_frame()
    assert ledger.processed_boundary == 60
    assert ledger.snapshot.hr_accepted_frames == 60
    assert ledger.snapshot.br_numeric_frames == 60
    assert ledger.snapshot.hr_state == "held"
    assert ledger.snapshot.br_state == "held"
    intervals = ledger.finalized_intervals()
    assert any(
        row.channel == "hr"
        and row.start == 0
        and row.stop == 60
        and row.state == "accepted"
        for row in intervals
    )


def test_renewal_exactly_at_expiry_is_contiguous_but_one_frame_late_has_gap():
    exact = CoverageLedger(hop_frames=60)
    exact.apply(CoverageEvent(0, "first", "a0", hr_state="accepted"))
    _advance(exact, 60)
    exact.apply(CoverageEvent(60, "renew", "a1", hr_state="accepted"))
    _advance(exact, 120)
    assert exact.snapshot.hr_accepted_frames == 120

    late = CoverageLedger(hop_frames=60)
    late.apply(CoverageEvent(0, "first", "a0", hr_state="accepted"))
    _advance(late, 61)
    late.apply(CoverageEvent(61, "late renew", "a1", hr_state="accepted"))
    _advance(late, 121)
    assert late.snapshot.hr_accepted_frames == 120
    assert any(
        row.channel == "hr"
        and row.start == 60
        and row.stop == 61
        and row.state == "held"
        for row in late.finalized_intervals()
    )


def test_preliminary_held_missing_and_unresolved_never_count_as_numeric_coverage():
    ledger = CoverageLedger(hop_frames=3)
    for boundary, state in enumerate(
        ("preliminary", "held", "missing", "unresolved")
    ):
        assert ledger.processed_boundary == boundary
        ledger.apply(
            CoverageEvent(
                boundary,
                state,
                f"a{boundary}",
                hr_state=state,
                br_state=state,
            )
        )
        ledger.advance_frame()

    assert ledger.snapshot.hr_accepted_frames == 0
    assert ledger.snapshot.br_numeric_frames == 0
    assert ledger.snapshot.both_frames == 0


def test_quiet_status_has_separate_lease_and_never_becomes_numeric_zero_br():
    ledger = CoverageLedger(hop_frames=4)
    ledger.apply(
        CoverageEvent(
            0,
            "quiet assessment",
            "quiet0",
            br_state="held",
            quiet_state="accepted",
        )
    )
    _advance(ledger, 5)

    assert ledger.snapshot.quiet_frames == 4
    assert ledger.snapshot.br_numeric_frames == 0
    assert ledger.snapshot.both_frames == 0


def test_physical_clip_ends_only_requested_active_leases_at_boundary():
    ledger = CoverageLedger(hop_frames=60)
    ledger.apply(CoverageEvent(0, "accepted", "a0", "accepted", "accepted"))
    _advance(ledger, 10)
    ledger.clip(reason="invalid_frame", channels=("hr",))
    _advance(ledger, 20)

    assert ledger.snapshot.hr_accepted_frames == 10
    assert ledger.snapshot.br_numeric_frames == 20
    assert ledger.snapshot.both_frames == 10


def test_partial_run_freezes_denominator_and_clips_leases_at_stop():
    ledger = CoverageLedger(hop_frames=60)
    _advance(ledger, 17)
    ledger.apply(CoverageEvent(17, "accepted", "a0", hr_state="accepted"))
    _advance(ledger, 23)

    assert ledger.snapshot.denominator_frames == 23
    assert ledger.snapshot.hr_accepted_frames == 6
    assert max(row.stop for row in ledger.finalized_intervals()) == 23


def test_reference_availability_uses_same_frame_denominator_but_is_independent():
    ledger = CoverageLedger(hop_frames=2)
    ledger.apply(CoverageEvent(0, "radar", "a0", hr_state="accepted"))
    ledger.advance_frame(
        reference_pr_present=True,
        reference_pr_qualified=False,
        reference_rr_present=True,
    )
    ledger.advance_frame(
        reference_pr_present=True,
        reference_pr_qualified=True,
        reference_rr_present=False,
    )
    ledger.advance_frame()

    snapshot = ledger.snapshot
    assert snapshot.denominator_frames == 3
    assert snapshot.hr_accepted_frames == 2
    assert snapshot.reference_pr_frames == 2
    assert snapshot.reference_pr_qualified_frames == 1
    assert snapshot.reference_rr_frames == 1


def test_pi_qualified_reference_requires_positive_present_pr():
    ledger = CoverageLedger()
    with pytest.raises(ValueError, match="implies positive finite PR"):
        ledger.advance_frame(reference_pr_qualified=True)


def test_coverage_event_must_apply_at_exact_current_boundary():
    ledger = CoverageLedger()
    with pytest.raises(ValueError, match="current processed boundary"):
        ledger.apply(CoverageEvent(1, "future", hr_state="accepted"))


def test_reconstruction_rejects_event_beyond_elapsed_run():
    with pytest.raises(ValueError, match="outside|beyond|total"):
        reconstruct_coverage(
            [CoverageEvent(11, "impossible future", hr_state="accepted")],
            total_frames=10,
        )


@pytest.mark.parametrize("speed", [0.5, 1.0, 2.0, 4.0])
def test_identical_controller_event_trace_has_identical_coverage_at_every_speed(speed):
    # Speed is deliberately absent from the ledger.  Persisting the same controlled
    # frame/result trace must yield the same result regardless of pacing.
    events = [
        CoverageEvent(10, f"published at {speed}x", "a0", "accepted", "accepted"),
        CoverageEvent(70, f"HR renewed at {speed}x", "a1", "accepted", "held"),
        CoverageEvent(75, f"movement at {speed}x", hr_state="held"),
    ]
    snapshot = reconstruct_coverage(events, total_frames=100)
    assert (
        snapshot.hr_accepted_frames,
        snapshot.br_numeric_frames,
        snapshot.both_frames,
    ) == (65, 60, 60)


def test_zero_frame_coverage_percentage_is_unavailable():
    assert CoverageSnapshot.percentage(0, 0) is None
