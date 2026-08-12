"""Pin the acquisition admission thresholds, their rendered messages, and M2/M4 agreement.

Milestone A named these numbers as module constants so operator tooling can import them instead
of re-typing them. That makes them editable from one place, which is the point - and also means a
typo there widens or narrows admission with the rest of the suite still green. Nothing else in
`tests/` asserts an inclusive/exclusive boundary of `validate_acquisition_metadata`, so these
tests are what make the constants safe to edit.

The M4 agreement test exists because these are NOT the only definitions of these numbers:
`src/m4/manifest.py` independently defines the same protocol thresholds for the offline scoring
admissibility gate, and a one-sided protocol edit would let acquisition and scoring disagree
silently.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "m2"))

from builders import acquisition_metadata  # noqa: E402
from src.m2.acquisition_metadata import (  # noqa: E402
    DISTANCE_MAX_M,
    DISTANCE_MIN_M,
    EXERTION_STOP_PR_MAX_BPM,
    EXERTION_STOP_PR_MIN_BPM,
    HARMONIC_MARGIN_TOLERANCE_BPM,
    MAX_SCENE_NOTES_CHARS,
    METRONOME_BEATS_PER_BREATH,
    MIN_BR_STABILITY_S,
    MIN_PACED_SETTLE_S,
    MIN_SETTLE_S,
    RESPIRATION_HARMONIC_ORDER,
    SETTLE_DRIFT_MAX_BPM,
    SETTLE_EVIDENCE_WINDOW_S,
    SETTLE_SPREAD_MAX_BPM,
    validate_acquisition_metadata,
)
from src.m2.common import ContractError  # noqa: E402


def _with(arm: str, **overrides: object) -> dict:
    metadata = acquisition_metadata(arm)
    metadata.update(overrides)
    return metadata


# ── Inclusive boundaries are ADMITTED, not merely "same error" ────────────────

@pytest.mark.parametrize(
    ("arm", "field", "value"),
    [
        ("natural", "distance_m", DISTANCE_MIN_M),
        ("natural", "distance_m", DISTANCE_MAX_M),
        ("natural", "settle_duration_s", MIN_SETTLE_S),
        ("natural", "settle_evidence_window_s", SETTLE_EVIDENCE_WINDOW_S),
        ("natural", "settle_pr_spread_bpm", SETTLE_SPREAD_MAX_BPM),
        ("natural", "settle_pr_drift_bpm", SETTLE_DRIFT_MAX_BPM),
        ("paced", "paced_settle_duration_s", MIN_PACED_SETTLE_S),
        ("paced", "br_stability_duration_s", MIN_BR_STABILITY_S),
        ("recovery", "exertion_stop_pr_bpm", EXERTION_STOP_PR_MIN_BPM),
        ("recovery", "exertion_stop_pr_bpm", EXERTION_STOP_PR_MAX_BPM),
    ],
)
def test_inclusive_threshold_boundaries_are_admitted(arm, field, value):
    validate_acquisition_metadata(_with(arm, **{field: value}))


def test_scene_notes_at_the_exact_character_limit_are_admitted():
    validate_acquisition_metadata(
        _with("natural", scene_non_health_notes="a" * MAX_SCENE_NOTES_CHARS)
    )


# ── Just outside each boundary is REJECTED ───────────────────────────────────

@pytest.mark.parametrize(
    ("arm", "field", "value"),
    [
        ("natural", "distance_m", 0.79),
        ("natural", "distance_m", 1.41),
        ("natural", "settle_duration_s", 119.9),
        # 59.9 is a legacy pin: it was the admitted boundary while MIN_SETTLE_S was 60.0, before
        # protocol limb 3 raised the floor to 120 s on 2026-08-12. Kept so a revert is visible.
        ("natural", "settle_duration_s", 59.9),
        ("natural", "settle_evidence_window_s", 60.1),
        ("natural", "settle_evidence_window_s", 59.9),
        ("natural", "settle_pr_spread_bpm", 5.1),
        ("natural", "settle_pr_drift_bpm", 3.1),
        ("paced", "paced_settle_duration_s", 119.9),
        ("paced", "br_stability_duration_s", 59.9),
        ("recovery", "exertion_stop_pr_bpm", 99.9),
        ("recovery", "exertion_stop_pr_bpm", 120.1),
    ],
)
def test_values_just_outside_each_threshold_are_rejected(arm, field, value):
    with pytest.raises(ContractError):
        validate_acquisition_metadata(_with(arm, **{field: value}))


def test_scene_notes_one_character_over_the_limit_are_rejected():
    with pytest.raises(ContractError):
        validate_acquisition_metadata(
            _with("natural", scene_non_health_notes="a" * (MAX_SCENE_NOTES_CHARS + 1))
        )


# ── Operator-facing message text ─────────────────────────────────────────────
# These messages are built from the constants above, so a change to a constant changes operator
# guidance. Pinned so the text cannot drift silently, and so a future formatting "cleanup" of the
# mixed repr/:g specs fails loudly instead of quietly rewording what the operator reads.

@pytest.mark.parametrize(
    ("arm", "field", "value", "message"),
    [
        (
            "natural",
            "distance_m",
            1.41,
            "distance_m must be in [0.8, 1.4] metres, inclusive",
        ),
        (
            "natural",
            "settle_evidence_window_s",
            60.1,
            "settle_evidence_window_s must be exactly 60.0 seconds",
        ),
        (
            "natural",
            "settle_pr_spread_bpm",
            5.1,
            "natural/paced settle evidence does not pass <=5/<=3 bpm gates",
        ),
        (
            "recovery",
            "exertion_stop_pr_bpm",
            120.1,
            "exertion_stop_pr_bpm must be in [100, 120] bpm",
        ),
    ],
)
def test_threshold_messages_render_exactly(arm, field, value, message):
    with pytest.raises(ContractError) as excinfo:
        validate_acquisition_metadata(_with(arm, **{field: value}))
    assert str(excinfo.value) == message


def test_settle_duration_rejection_names_the_120_s_floor():
    """Pin the settle-floor message, which comes from require_number rather than this module.

    Two reasons this needs its own test. The parametrized rejection above uses a bare `raises`, so
    it would pass if the rejection came from an unrelated cause. And this message changed when the
    floor moved from 60 to 120, which is exactly the drift the message pinning exists to catch.
    """
    with pytest.raises(ContractError, match=r"settle_duration_s must be >= 120\.0, got 119\.9"):
        validate_acquisition_metadata(_with("natural", settle_duration_s=119.9))


def test_scene_notes_message_renders_exactly():
    with pytest.raises(ContractError) as excinfo:
        validate_acquisition_metadata(
            _with("natural", scene_non_health_notes="a" * (MAX_SCENE_NOTES_CHARS + 1))
        )
    assert str(excinfo.value) == "scene_non_health_notes must be at most 500 characters"


# ── Derived formulas the operator tool must reproduce ────────────────────────

def test_metronome_rate_is_the_named_multiple_of_the_commanded_rate():
    metadata = acquisition_metadata("paced")
    commanded = metadata["commanded_rate_bpm"]
    validate_acquisition_metadata(
        _with("paced", metronome_rate_bpm=METRONOME_BEATS_PER_BREATH * commanded)
    )
    with pytest.raises(ContractError):
        validate_acquisition_metadata(
            _with("paced", metronome_rate_bpm=METRONOME_BEATS_PER_BREATH * commanded + 1)
        )


def test_harmonic_collision_margin_uses_the_named_harmonic_order_and_tolerance():
    metadata = acquisition_metadata("paced")
    commanded = metadata["commanded_rate_bpm"]
    resting = metadata["resting_pr_bpm"]
    exact = abs(resting - RESPIRATION_HARMONIC_ORDER * commanded)
    validate_acquisition_metadata(_with("paced", harmonic_collision_margin_bpm=exact))
    with pytest.raises(ContractError):
        validate_acquisition_metadata(
            _with(
                "paced",
                harmonic_collision_margin_bpm=exact + 10 * HARMONIC_MARGIN_TOLERANCE_BPM,
            )
        )


# ── M2 and M4 must not drift apart ──────────────────────────────────────────

def test_m2_and_m4_both_source_every_shared_protocol_fact_from_src_protocol():
    """Guard against a local literal creeping back into either module.

    Since D-OWN-8 these are aliases of `src/protocol.py`, so equality between M2 and M4 is
    automatic and asserting it alone would be vacuous. What can still go wrong is someone
    re-introducing a literal in one module - which is exactly the pre-D-OWN-8 state, where M4 is the
    offline scoring path feeding the paper's agreement metrics and a one-sided protocol edit would
    have let acquisition admit a session that scoring excluded, with nothing failing. So this
    asserts each module's name still resolves to the shared definition. Note the spellings still
    differ between the two modules; only the values are unified.
    """
    from src import protocol
    from src.m2 import acquisition_metadata as m2
    from src.m4 import manifest as m4

    assert (m2.DISTANCE_MIN_M, m2.DISTANCE_MAX_M) == (protocol.DISTANCE_MIN_M, protocol.DISTANCE_MAX_M)
    assert (m4.DISTANCE_MIN_M, m4.DISTANCE_MAX_M) == (protocol.DISTANCE_MIN_M, protocol.DISTANCE_MAX_M)

    assert m2.SETTLE_SPREAD_MAX_BPM == protocol.SETTLE_SPREAD_MAX_BPM
    assert m4.SETTLE_MAX_PR_SPREAD_BPM == protocol.SETTLE_SPREAD_MAX_BPM
    assert m2.SETTLE_DRIFT_MAX_BPM == protocol.SETTLE_DRIFT_MAX_BPM
    assert m4.SETTLE_MAX_PR_DRIFT_BPM == protocol.SETTLE_DRIFT_MAX_BPM

    assert m2.MAX_CLOCK_OFFSET_S == protocol.MAX_CLOCK_OFFSET_S
    assert m4.MAX_CLOCK_OFFSET_S == protocol.MAX_CLOCK_OFFSET_S

    assert tuple(m2.PACED_RATES_BPM) == tuple(protocol.PACED_RATES_BPM)
    assert tuple(m4.PACED_RATES_BPM) == tuple(protocol.PACED_RATES_BPM)

    # Settle timing: M2 enforces both, M4 documents them without gating on them.
    assert m2.MIN_SETTLE_S == protocol.MIN_SETTLE_S
    assert m2.SETTLE_EVIDENCE_WINDOW_S == protocol.SETTLE_EVIDENCE_WINDOW_S


def test_the_three_frame_rate_definitions_agree_although_deliberately_unshared():
    """`FRAME_RATE_HZ` is duplicated on purpose, so a test must hold the copies together.

    M2's is a capture parameter the operator attests to having configured; `src/m4/window_grid.py`
    marks its copy FROZEN by `notes/analysis_prespec.md` §7, where a change is an amendment to a
    frozen analysis decision rather than a config edit. Merging them would couple an acquisition
    attestation to a frozen analysis decision, so they stay separate - but a divergence would be a
    *silent* scientific error, windows holding 20 s of data while labelled 30 s, which is why this
    asserts they agree.
    """
    from src.m2 import acquisition_metadata as m2_acq
    from src.m2 import time_sensitivity as m2_time
    from src.m4 import window_grid as m4_grid

    assert m2_acq.FRAME_RATE_HZ == m2_time.FRAME_RATE_HZ == m4_grid.FRAME_RATE_HZ
    assert m2_time.FRAMES_PER_WINDOW == m4_grid.FRAMES_PER_WINDOW


def test_a_coincidentally_equal_threshold_is_not_folded_into_the_shared_module():
    """`comparator.py`'s HR stationarity limit is also 5.0 and must stay independent.

    It is a within-window HR quantity, unrelated to the settle PR spread. If a future protocol
    amendment moves the settle limb, this must not follow it. Equal numbers are not the same fact.
    """
    import inspect

    from src import comparator, protocol

    assert comparator._HR_STATIONARITY_MAX_BPM == 5.0
    assert protocol.SETTLE_SPREAD_MAX_BPM == 5.0

    # The shared module must not have grown an HR-stationarity constant, and comparator must not
    # have started sourcing its limit from it. Both are checkable; equality of the values is not.
    assert not any("STATIONARITY" in name for name in vars(protocol)), (
        "src/protocol.py holds physical protocol facts; HR stationarity is an analysis quantity"
    )
    assert "protocol" not in inspect.getsource(comparator).split("_HR_STATIONARITY_MAX_BPM")[0][-200:]


def test_min_settle_is_never_below_the_evidence_window():
    """Nothing validates settle_duration_s >= settle_evidence_window_s.

    Until such a cross-check exists, MIN_SETTLE_S dropping below SETTLE_EVIDENCE_WINDOW_S would
    admit a sidecar declaring a 60 s continuous evidence window inside a shorter total settle - a
    criterion it cannot have demonstrated. This guards the constant, not the input.
    """
    assert MIN_SETTLE_S >= SETTLE_EVIDENCE_WINDOW_S
