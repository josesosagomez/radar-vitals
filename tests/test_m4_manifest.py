"""M4 Stage 1 — manifest schema, validation and admission recomputation.

Done-when (plan §7 row 1): scoring mode rejects every missing required field with a named
error; M4 recomputes the admission disposition from the primitive fields and **fails loudly
if it disagrees with the operator-supplied verdict, with a named negative test per rule**
(M4R-04); development mode is separately labelled and cannot emit scoring output.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.m4.manifest import (  # noqa: E402
    CANONICAL_POSTURE,
    DISTANCE_MAX_M,
    DISTANCE_MIN_M,
    MAX_CLOCK_OFFSET_S,
    Admission,
    Arm,
    DataRole,
    ManifestError,
    Mode,
    RetryStatus,
    load_manifest,
    parse_session,
    recompute_admission,
    require_scoring_mode,
)

_REQUIRED_KEYS = [
    "session_id", "subject_id", "arm", "data_role", "admission",
    "distance_m", "posture",
    "frame0_epoch", "clock_offset_start_s", "clock_offset_end_s",
    "raw_path", "raw_sha256", "truncation_bytes", "packet_loss_frames",
    "n_frames", "n_invalid_frames",
    "frame_validity_map_path", "frame_validity_map_sha256",
    "capture_config_sha256", "capture_git_commit", "masimo_path", "masimo_sha256",
    "intended_duration_s", "actual_duration_s", "early_stop", "retry_status",
]


def admissible(**over) -> dict:
    """A session that M4 recomputes as ADMITTED. Every exclusion test perturbs one field."""
    base = {
        "session_id": "S01_natural",
        "subject_id": "S01",
        "arm": "natural",
        "data_role": "evaluation",
        "admission": "admitted",
        "distance_m": 1.0,
        "posture": "seated",
        "frame0_epoch": 1785000000.25,
        "clock_offset_start_s": 0.2,
        "clock_offset_end_s": -0.3,
        "raw_path": "data/raw/S01_natural.bin",
        "raw_sha256": "a" * 64,
        "truncation_bytes": 0,
        "packet_loss_frames": 0,
        "n_frames": 12000,
        "n_invalid_frames": 0,
        "frame_validity_map_path": "data/raw/S01_natural.validity.npy",
        "frame_validity_map_sha256": "b" * 64,
        "capture_config_sha256": "c" * 64,
        "capture_git_commit": "0123456789abcdef",
        "masimo_path": "data/raw/S01_natural.csv",
        "masimo_sha256": "d" * 64,
        "intended_duration_s": 600.0,
        "actual_duration_s": 600.0,
        "early_stop": False,
        "retry_status": "original",
    }
    base.update(over)
    return base


# ── The happy path, so the negatives below mean something ─────────────────────

def test_a_complete_admissible_session_parses_in_scoring_mode():
    m = parse_session(admissible(), Mode.SCORING)
    assert m.mode is Mode.SCORING and m.is_scorable
    assert m.session_id == "S01_natural"
    assert m.arm is Arm.NATURAL
    assert m.data_role is DataRole.EVALUATION
    assert m.admission is Admission.ADMITTED
    assert m.admission_reasons == ()
    assert m.retry_status is RetryStatus.ORIGINAL
    assert m.frame0_epoch == pytest.approx(1785000000.25)


# ── Required fields: a named error for EVERY one ──────────────────────────────

@pytest.mark.parametrize("missing", _REQUIRED_KEYS)
def test_scoring_mode_rejects_every_missing_required_field(missing):
    """Plan §7 row 1: 'rejects every missing required field with a named error'. The
    parametrisation is over the field list itself, so a field added to the schema without a
    test is impossible."""
    fields = admissible()
    del fields[missing]
    with pytest.raises(ManifestError) as exc:
        parse_session(fields, Mode.SCORING)
    assert missing in str(exc.value), "the error must name the missing field"


@pytest.mark.parametrize("nulled", _REQUIRED_KEYS)
def test_a_present_but_null_required_field_is_also_rejected(nulled):
    """`"distance_m": null` is missing, not supplied."""
    fields = admissible(**{nulled: None})
    with pytest.raises(ManifestError):
        parse_session(fields, Mode.SCORING)


def test_required_field_list_covers_every_group_in_section_4():
    """Guards against a group being dropped from the schema wholesale."""
    from src.m4.manifest import _REQUIRED_SCORING_FIELDS

    groups = {g for _, g in _REQUIRED_SCORING_FIELDS}
    assert groups == {"Identity", "Design", "Timebase", "Integrity", "Provenance", "Disposition"}
    assert {k for k, _ in _REQUIRED_SCORING_FIELDS} == set(_REQUIRED_KEYS)


# ── §4.1 design-field contract, tested AT the equality boundaries ─────────────

@pytest.mark.parametrize("d", [0.8, 1.4, 1.0, 0.80000001, 1.39999999])
def test_distance_inside_the_inclusive_range_is_accepted(d):
    assert parse_session(admissible(distance_m=d), Mode.SCORING).distance_m == pytest.approx(d)


def test_distance_boundaries_are_inclusive_at_both_ends():
    """§4.1: '0.8 <= distance_m <= 1.4. Inclusive at both ends.' Stage 1 pins the equality
    boundaries: 0.8 and 1.4 accepted, 0.79 and 1.41 rejected."""
    assert parse_session(admissible(distance_m=DISTANCE_MIN_M), Mode.SCORING).distance_m == 0.8
    assert parse_session(admissible(distance_m=DISTANCE_MAX_M), Mode.SCORING).distance_m == 1.4
    for outside in (0.79, 1.41):
        with pytest.raises(ManifestError, match="outside the protocol range"):
            parse_session(admissible(distance_m=outside), Mode.SCORING)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_distance_is_rejected(bad):
    with pytest.raises(ManifestError, match="not finite"):
        parse_session(admissible(distance_m=bad), Mode.SCORING)


def test_distance_in_centimetres_is_rejected_not_silently_converted():
    """The legacy `run_metadata.json` field is `distance_cm`. 100 cm is a plausible-looking
    value that must NOT be read as 100 m — conversion is explicit, never implicit (§4.1)."""
    with pytest.raises(ManifestError, match="outside the protocol range"):
        parse_session(admissible(distance_m=100), Mode.SCORING)


def test_non_numeric_distance_is_rejected():
    with pytest.raises(ManifestError, match="not a number"):
        parse_session(admissible(distance_m="1.0 m"), Mode.SCORING)


@pytest.mark.parametrize("bad", ["standing", "supine", "Seated", "seated ", "", None])
def test_posture_must_equal_the_canonical_seated(bad):
    """The estimand fixes posture; a differing session is not a member of this design."""
    with pytest.raises(ManifestError):
        parse_session(admissible(posture=bad), Mode.SCORING)
    assert CANONICAL_POSTURE == "seated"


# ── Controlled vocabularies ───────────────────────────────────────────────────

@pytest.mark.parametrize(
    "key, bad",
    [("arm", "sitting"), ("data_role", "confirmatory"), ("retry_status", "redone"),
     ("admission", "maybe")],
)
def test_unknown_enum_values_are_rejected_and_the_error_lists_the_allowed_set(key, bad):
    with pytest.raises(ManifestError) as exc:
        parse_session(admissible(**{key: bad}), Mode.SCORING)
    assert key in str(exc.value) and "is not one of" in str(exc.value)


def test_paced_arm_requires_a_commanded_rate_from_the_frozen_rotation():
    for rate in (12, 15, 18):
        m = parse_session(
            admissible(arm="paced", commanded_rate_bpm=rate), Mode.SCORING
        )
        assert m.commanded_rate_bpm == rate
    with pytest.raises(ManifestError, match="commanded_rate_bpm is missing"):
        parse_session(admissible(arm="paced"), Mode.SCORING)
    with pytest.raises(ManifestError, match="not one of"):
        parse_session(admissible(arm="paced", commanded_rate_bpm=16), Mode.SCORING)


def test_natural_arm_must_not_carry_a_commanded_rate():
    with pytest.raises(ManifestError, match="has no commanded rate"):
        parse_session(admissible(arm="natural", commanded_rate_bpm=12), Mode.SCORING)


def test_frame0_epoch_must_be_finite():
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ManifestError, match="frame0_epoch"):
            parse_session(admissible(frame0_epoch=bad), Mode.SCORING)


def test_frame0_epoch_may_be_fractional():
    """It is a synchronised clock reading, not an integer second (§7 requires the
    fractional case to work end to end)."""
    m = parse_session(admissible(frame0_epoch=1785000000.9375), Mode.SCORING)
    assert m.frame0_epoch == pytest.approx(1785000000.9375)


# ── Admission recomputation: one NAMED negative test per rule (M4R-04) ────────

def test_recompute_admits_a_clean_session():
    verdict, reasons = recompute_admission(admissible(), "S01")
    assert verdict is Admission.ADMITTED and reasons == ()


@pytest.mark.parametrize("key", ["clock_offset_start_s", "clock_offset_end_s"])
def test_rule_clock_offset_exceeds_one_second_at_either_end(key):
    """§6: NTP-synced, max +/-1 s, re-checked at session end. Tested AT the boundary:
    exactly 1.0 s is admissible, a hair beyond is not."""
    assert recompute_admission(admissible(**{key: 1.0}), "S")[0] is Admission.ADMITTED
    assert recompute_admission(admissible(**{key: -1.0}), "S")[0] is Admission.ADMITTED
    for beyond in (1.0001, -1.0001, 3.0):
        verdict, reasons = recompute_admission(admissible(**{key: beyond}), "S")
        assert verdict is Admission.EXCLUDED
        assert f"{key}_exceeds_{MAX_CLOCK_OFFSET_S:g}s" in reasons


@pytest.mark.parametrize("key", ["clock_offset_start_s", "clock_offset_end_s"])
def test_rule_clock_offset_missing_or_non_finite(key):
    for bad in (None, float("nan"), float("inf")):
        verdict, reasons = recompute_admission(admissible(**{key: bad}), "S")
        assert verdict is Admission.EXCLUDED
        assert f"{key}_missing_or_non_finite" in reasons


def test_rule_raw_truncated():
    verdict, reasons = recompute_admission(admissible(truncation_bytes=4096), "S")
    assert verdict is Admission.EXCLUDED and "raw_truncated" in reasons


def test_rule_truncation_bytes_missing():
    verdict, reasons = recompute_admission(admissible(truncation_bytes=None), "S")
    assert verdict is Admission.EXCLUDED and "truncation_bytes_missing" in reasons


def test_rule_packet_loss_detected():
    verdict, reasons = recompute_admission(
        admissible(packet_loss_frames=3, n_invalid_frames=3), "S"
    )
    assert verdict is Admission.EXCLUDED and "packet_loss_detected" in reasons


def test_rule_packet_loss_frames_missing():
    verdict, reasons = recompute_admission(admissible(packet_loss_frames=None), "S")
    assert verdict is Admission.EXCLUDED and "packet_loss_frames_missing" in reasons


def test_rule_early_stop():
    verdict, reasons = recompute_admission(admissible(early_stop=True), "S")
    assert verdict is Admission.EXCLUDED and "early_stop" in reasons


def test_rule_actual_duration_below_intended():
    verdict, reasons = recompute_admission(
        admissible(intended_duration_s=600.0, actual_duration_s=412.0), "S"
    )
    assert verdict is Admission.EXCLUDED and "actual_duration_below_intended" in reasons
    # Running slightly long is not an exclusion cause.
    assert recompute_admission(
        admissible(actual_duration_s=601.0), "S"
    )[0] is Admission.ADMITTED


def test_rule_superseded_by_retry():
    verdict, reasons = recompute_admission(admissible(retry_status="superseded"), "S")
    assert verdict is Admission.EXCLUDED and "superseded_by_retry" in reasons
    # A retry that IS the kept session stays admissible.
    assert recompute_admission(admissible(retry_status="retry"), "S")[0] is Admission.ADMITTED


def test_rule_invalid_frames_exceed_total():
    verdict, reasons = recompute_admission(
        admissible(n_frames=100, n_invalid_frames=101), "S"
    )
    assert verdict is Admission.EXCLUDED and "invalid_frames_exceed_total" in reasons


def test_rule_frame_counts_negative():
    verdict, reasons = recompute_admission(admissible(n_invalid_frames=-1), "S")
    assert verdict is Admission.EXCLUDED and "frame_counts_negative" in reasons


def test_rule_validity_map_inconsistent_with_packet_loss():
    """Packets were lost but no frame is marked invalid: the map cannot be describing the
    same capture, and a window containing a dropped frame must become radar-NaN."""
    verdict, reasons = recompute_admission(
        admissible(packet_loss_frames=5, n_invalid_frames=0), "S"
    )
    assert verdict is Admission.EXCLUDED
    assert "validity_map_inconsistent_with_packet_loss" in reasons


def test_multiple_failing_rules_are_all_reported_not_just_the_first():
    """A manifest with three defects must name three, or fixing one reveals the next."""
    verdict, reasons = recompute_admission(
        admissible(truncation_bytes=1, early_stop=True, clock_offset_end_s=9.0), "S"
    )
    assert verdict is Admission.EXCLUDED
    assert {"raw_truncated", "early_stop", "clock_offset_end_s_exceeds_1s"} <= set(reasons)


# ── Operator vs recomputed disagreement (the M4R-04 headline) ─────────────────

def test_operator_admitted_but_M4_recomputes_excluded_raises():
    with pytest.raises(ManifestError) as exc:
        parse_session(admissible(admission="admitted", truncation_bytes=8), Mode.SCORING)
    msg = str(exc.value)
    assert "recomputes" in msg and "raw_truncated" in msg


def test_operator_excluded_but_M4_recomputes_admitted_also_raises():
    """The check is symmetric on purpose. An operator excluding a session M4 sees as clean
    is just as much a disagreement — silently accepting it would let a session be dropped
    for an unrecorded reason, which is an unlogged degree of freedom."""
    with pytest.raises(ManifestError) as exc:
        parse_session(admissible(admission="excluded"), Mode.SCORING)
    assert "recomputes" in str(exc.value)


def test_agreement_on_excluded_is_accepted_and_keeps_the_reasons():
    m = parse_session(
        admissible(admission="excluded", early_stop=True), Mode.SCORING
    )
    assert m.admission is Admission.EXCLUDED
    assert "early_stop" in m.admission_reasons


# ── Development mode (§2.2 / §4) ──────────────────────────────────────────────

def test_development_mode_accepts_the_fields_the_existing_captures_genuinely_lack():
    """The 4 existing captures have no `frame0_epoch`, distance or posture. Development
    mode exists for exactly them."""
    m = parse_session(
        {"session_id": "20260713_172042_massimo1", "arm": "natural"}, Mode.DEVELOPMENT
    )
    assert m.mode is Mode.DEVELOPMENT
    assert m.frame0_epoch is None and m.distance_m is None and m.posture is None
    assert not m.is_scorable


def test_development_mode_is_a_smaller_contract_not_a_laxer_one():
    """A field that IS present is still validated: development mode drops requirements, it
    does not stop checking."""
    with pytest.raises(ManifestError, match="outside the protocol range"):
        parse_session({"session_id": "x", "distance_m": 4.2}, Mode.DEVELOPMENT)
    with pytest.raises(ManifestError, match="posture"):
        parse_session({"session_id": "x", "posture": "standing"}, Mode.DEVELOPMENT)
    with pytest.raises(ManifestError, match="is not one of"):
        parse_session({"session_id": "x", "arm": "sitting"}, Mode.DEVELOPMENT)


def test_development_sessions_cannot_reach_a_scoring_path():
    dev = [parse_session({"session_id": "d1"}, Mode.DEVELOPMENT)]
    with pytest.raises(ManifestError) as exc:
        require_scoring_mode(dev, "HR agreement")
    assert "d1" in str(exc.value) and "DEVELOPMENT" in str(exc.value)


def test_require_scoring_mode_passes_for_scoring_sessions():
    require_scoring_mode([parse_session(admissible(), Mode.SCORING)], "HR agreement")


def test_a_manifest_cannot_promote_itself_to_scoring_mode():
    """Mode is supplied by the caller, never read from the file — otherwise a manifest
    could declare itself scorable."""
    import inspect

    assert "mode" in inspect.signature(load_manifest).parameters
    m = parse_session({"session_id": "x", "mode": "SCORING"}, Mode.DEVELOPMENT)
    assert m.mode is Mode.DEVELOPMENT


# ── File loading ──────────────────────────────────────────────────────────────

def _write(tmp_path, doc) -> Path:
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def test_load_manifest_reads_and_validates_every_session(tmp_path):
    p = _write(tmp_path, {"sessions": [admissible(), admissible(session_id="S02")]})
    sessions = load_manifest(p, Mode.SCORING)
    assert [s.session_id for s in sessions] == ["S01_natural", "S02"]


def test_load_manifest_rejects_a_duplicate_session_id(tmp_path):
    p = _write(tmp_path, {"sessions": [admissible(), admissible()]})
    with pytest.raises(ManifestError, match="duplicate session_id"):
        load_manifest(p, Mode.SCORING)


def test_load_manifest_rejects_a_malformed_document(tmp_path):
    with pytest.raises(ManifestError, match="'sessions' array"):
        load_manifest(_write(tmp_path, {"rows": []}), Mode.SCORING)
    with pytest.raises(ManifestError, match="must be an array"):
        load_manifest(_write(tmp_path, {"sessions": {}}), Mode.SCORING)
    with pytest.raises(ManifestError, match="must be a dict"):
        load_manifest(_write(tmp_path, {"sessions": ["not-a-dict"]}), Mode.SCORING)


def test_session_id_must_be_a_non_empty_string(tmp_path):
    for bad in ("", None, 17):
        with pytest.raises(ManifestError, match="session_id"):
            parse_session({"session_id": bad}, Mode.DEVELOPMENT)
