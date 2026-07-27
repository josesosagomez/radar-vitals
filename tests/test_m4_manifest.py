"""M4 Stage 1 — manifest schema, validation and disposition recomputation.

Done-when (plan §7 row 1): scoring mode rejects every missing required field with a named
error; M4 recomputes the disposition disposition from the primitive fields and **fails loudly
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
    PACKET_LOSS_FLAG_RATIO,
    DISTANCE_MAX_M,
    DISTANCE_MIN_M,
    MANIFEST_SCHEMA_VERSION,
    MAX_CLOCK_OFFSET_S,
    SessionDisposition,
    Arm,
    DataRole,
    ManifestError,
    Mode,
    RecordKind,
    RetryStatus,
    _REQUIRED_PRE_CAPTURE_FIELDS,
    load_manifest,
    parse_session,
    recompute_disposition,
    require_scoring_mode,
)

_REQUIRED_KEYS = [
    "record_kind", "session_id", "subject_id", "arm", "data_role", "disposition",
    "distance_m", "posture",
    "frame0_epoch", "clock_offset_start_s", "clock_offset_end_s",
    "raw_path", "raw_sha256", "truncation_bytes",
    "packets_received", "packets_dropped", "n_frames", "n_invalid_frames",
    "frame_validity_map_path", "frame_validity_map_sha256",
    "capture_config_path", "capture_config_sha256",
    "capture_git_commit", "masimo_path", "masimo_sha256",
    "intended_duration_s", "actual_duration_s", "early_stop", "retry_status",
    "settle_pr_spread_bpm", "settle_pr_drift_bpm",
    "settle_evidence_path", "settle_evidence_sha256",
]


def admissible(**over) -> dict:
    """A session that M4 recomputes as ADMITTED. Every exclusion test perturbs one field."""
    base = {
        "record_kind": "captured_session",
        "session_id": "S01_natural",
        "subject_id": "S01",
        "arm": "natural",
        "data_role": "evaluation",
        "disposition": "admitted",
        "distance_m": 1.0,
        "posture": "seated",
        "frame0_epoch": 1785000000.25,
        "clock_offset_start_s": 0.2,
        "clock_offset_end_s": -0.3,
        "raw_path": "data/raw/S01_natural.bin",
        "raw_sha256": "a" * 64,
        "truncation_bytes": 0,
        "packets_received": 100000,
        "packets_dropped": 0,
        "n_frames": 12000,
        "n_invalid_frames": 0,
        "frame_validity_map_path": "data/raw/S01_natural.validity.npy",
        "frame_validity_map_sha256": "b" * 64,
        "capture_config_path": "experiments/study/capture_config.yaml",
        "capture_config_sha256": "c" * 64,
        "capture_git_commit": "0123456789abcdef",
        "masimo_path": "data/raw/S01_natural.csv",
        "masimo_sha256": "d" * 64,
        "intended_duration_s": 600.0,
        "actual_duration_s": 600.0,
        "early_stop": False,
        "retry_status": "original",
        # Settle evidence comfortably inside both limbs (5 bpm spread, 3 bpm drift).
        "settle_pr_spread_bpm": 2.0,
        "settle_pr_drift_bpm": 1.0,
        "settle_evidence_path": "data/raw/S01_natural.settle.csv",
        "settle_evidence_sha256": "e" * 64,
    }
    base.update(over)
    return base


def pre_capture(**over) -> dict:
    """A logged pre-capture attempt (S12R-12): the settle gate failed, so no capture exists."""
    base = {
        "record_kind": "pre_capture_attempt",
        "session_id": "S01_natural_attempt1",
        "subject_id": "S01",
        "arm": "natural",
        "data_role": "evaluation",
        "disposition": "excluded",
        "distance_m": 1.0,
        "posture": "seated",
        "attempt_utc": 1785000000.0,
        "clock_offset_start_s": 0.2,
        "settle_pr_spread_bpm": 9.0,      # fails limb 1 (> 5 bpm)
        "settle_pr_drift_bpm": 1.0,
        "settle_evidence_path": "data/raw/S01_attempt1.settle.csv",
        "settle_evidence_sha256": "f" * 64,
    }
    base.update(over)
    return base


def paced(rate: int = 12, **over) -> dict:
    """An admissible PACED session. The paced arm additionally binds the commanded rate and
    the §4 commanded-rate schedule, so it needs its own fixture."""
    base = admissible(
        arm="paced",
        commanded_rate_bpm=rate,
        commanded_rate_schedule=[{"commanded_rate_bpm": rate, "start_s": 0.0}],
    )
    base.update(over)
    return base


def manifest_doc(*sessions: dict) -> dict:
    """A manifest **document** — the versioned envelope around the session array (§4)."""
    return {"manifest_schema_version": MANIFEST_SCHEMA_VERSION, "sessions": list(sessions)}


# ── The happy path, so the negatives below mean something ─────────────────────

def test_a_complete_admissible_session_parses_in_scoring_mode():
    m = parse_session(admissible(), Mode.SCORING, raw_digest_ok=True)
    assert m.mode is Mode.SCORING and m.is_scorable
    assert m.session_id == "S01_natural"
    assert m.arm is Arm.NATURAL
    assert m.data_role is DataRole.EVALUATION
    assert m.disposition is SessionDisposition.ADMITTED
    assert m.disposition_reasons == ()
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
        parse_session(fields, Mode.SCORING, raw_digest_ok=True)
    assert missing in str(exc.value), "the error must name the missing field"


@pytest.mark.parametrize("nulled", _REQUIRED_KEYS)
def test_a_present_but_null_required_field_is_also_rejected(nulled):
    """`"distance_m": null` is missing, not supplied."""
    fields = admissible(**{nulled: None})
    with pytest.raises(ManifestError):
        parse_session(fields, Mode.SCORING, raw_digest_ok=True)


#: Plan §4's manifest table, transcribed field by field from the *document* rather than from
#: the implementation (S12R-09). The previous version of this test compared
#: `_REQUIRED_SCORING_FIELDS` against `_REQUIRED_KEYS` — a hand-copy of the same list — and
#: asserted only that six group *labels* occurred, so a field omitted from both lists passed.
#: Two copies of one incomplete list agreeing is not coverage.
_SECTION_4_CONTRACT: dict[str, tuple[str, ...]] = {
    # "subject ID, arm (natural / paced), commanded paced rate (12/15/18), data role (§3.1),
    #  study disposition disposition"
    "Identity": ("subject_id", "arm", "data_role", "disposition"),
    # "`distance_m` and `posture` (M4R-15)"
    "Design": ("distance_m", "posture"),
    # "`frame0_epoch` …, start **and** end PC<->phone clock offsets"
    "Timebase": ("frame0_epoch", "clock_offset_start_s", "clock_offset_end_s"),
    # "raw checksum, truncation bytes, packet-loss statistics, per-frame validity / zero-fill
    #  map" — each artifact bound "by path + SHA-256" per §4's opening sentence.
    "Integrity": ("raw_path", "raw_sha256", "truncation_bytes",
                  "packets_received", "packets_dropped", "n_frames", "n_invalid_frames",
                  "frame_validity_map_path", "frame_validity_map_sha256"),
    # "capture config, capture-time git commit, Masimo CSV path + hash, commanded-rate
    #  schedule" — the config is an artifact, so it is bound by path + SHA-256 too.
    "Provenance": ("capture_config_path", "capture_config_sha256", "capture_git_commit",
                   "masimo_path", "masimo_sha256"),
    # "intended duration vs early stop, retry / replacement status and reason (§6)"
    "Disposition": ("intended_duration_s", "actual_duration_s", "early_stop", "retry_status"),
}


@pytest.mark.parametrize(
    "group, field",
    [(g, f) for g, fields in _SECTION_4_CONTRACT.items() for f in fields],
)
def test_every_section_4_field_is_required_in_scoring_mode(group, field):
    """Each §4 field, individually: omitting it must fail by name in scoring mode.

    Parametrised over the transcribed contract, so a field the *implementation* forgets is a
    failure here rather than an invisible gap.
    """
    from src.m4.manifest import _REQUIRED_SCORING_FIELDS

    assert (field, group) in _REQUIRED_SCORING_FIELDS, (
        f"§4 {group} field {field!r} is absent from the schema's required list"
    )
    fields = admissible()
    del fields[field]
    with pytest.raises(ManifestError) as exc:
        parse_session(fields, Mode.SCORING, raw_digest_ok=True)
    assert field in str(exc.value)


#: Required fields whose authority is **not** §4's table. Each must name its source, because
#: "a rule that is reasonable but unsourced is a finding" — the invariant this review opened
#: with. Anything required by the schema and absent from both this map and `_SECTION_4_CONTRACT`
#: is an invented requirement.
_NON_SECTION_4_AUTHORITY: dict[str, str] = {
    "session_id": "the record key itself, not a §4 table row",
    "record_kind": "S12R-12: discriminates the captured-session and pre-capture-attempt contracts",
    "settle_pr_spread_bpm": "§6 item 3 + notes/protocol.md SETTLE CRITERION limb 1 (S12R-04)",
    "settle_pr_drift_bpm": "§6 item 3 + notes/protocol.md SETTLE CRITERION limb 2 (S12R-04)",
    "settle_evidence_path": "S12R-04 R2: the criterion must be derived from auditable evidence",
    "settle_evidence_sha256": "S12R-04 R2: auditable evidence is bound by path + SHA-256",
}


def test_the_schema_requires_nothing_beyond_a_named_authority():
    """The other direction: an invented required field is also a defect — it would reject a
    manifest the binding documents say is complete."""
    from src.m4.manifest import _REQUIRED_SCORING_FIELDS

    contract = {f for fields in _SECTION_4_CONTRACT.values() for f in fields}
    extra = {k for k, _ in _REQUIRED_SCORING_FIELDS} - contract - set(_NON_SECTION_4_AUTHORITY)
    assert extra == set(), f"required fields with no named authority: {sorted(extra)}"


def test_every_pre_capture_required_field_has_a_named_authority():
    from src.m4.manifest import _REQUIRED_PRE_CAPTURE_FIELDS

    contract = {f for fields in _SECTION_4_CONTRACT.values() for f in fields}
    extra = {k for k, _ in _REQUIRED_PRE_CAPTURE_FIELDS} - contract - set(_NON_SECTION_4_AUTHORITY)
    # `attempt_utc` is the one field unique to this record kind.
    assert extra == {"attempt_utc"}, f"unexpected pre-capture requirements: {sorted(extra)}"


def test_conditionally_required_section_4_fields_are_enforced_when_applicable():
    """`commanded paced rate` and `retry / replacement … reason` are §4 fields that are
    required only in the state that gives them meaning, so they are not in the unconditional
    list and need their own coverage."""
    with pytest.raises(ManifestError, match="commanded_rate_bpm is missing"):
        parse_session(admissible(arm="paced"), Mode.SCORING, raw_digest_ok=True)
    with pytest.raises(ManifestError, match="retry_reason"):
        parse_session(admissible(retry_status="retry"), Mode.SCORING, raw_digest_ok=True)


# ── §4.1 design-field contract, tested AT the equality boundaries ─────────────

@pytest.mark.parametrize("d", [0.8, 1.4, 1.0, 0.80000001, 1.39999999])
def test_distance_inside_the_inclusive_range_is_accepted(d):
    assert parse_session(admissible(distance_m=d), Mode.SCORING, raw_digest_ok=True).distance_m == pytest.approx(d)


def test_distance_boundaries_are_inclusive_at_both_ends():
    """§4.1: '0.8 <= distance_m <= 1.4. Inclusive at both ends.' Stage 1 pins the equality
    boundaries: 0.8 and 1.4 accepted, 0.79 and 1.41 rejected."""
    assert parse_session(admissible(distance_m=DISTANCE_MIN_M), Mode.SCORING, raw_digest_ok=True).distance_m == 0.8
    assert parse_session(admissible(distance_m=DISTANCE_MAX_M), Mode.SCORING, raw_digest_ok=True).distance_m == 1.4
    for outside in (0.79, 1.41):
        with pytest.raises(ManifestError, match="outside the protocol range"):
            parse_session(admissible(distance_m=outside), Mode.SCORING, raw_digest_ok=True)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_distance_is_rejected(bad):
    with pytest.raises(ManifestError, match="not finite"):
        parse_session(admissible(distance_m=bad), Mode.SCORING, raw_digest_ok=True)


def test_distance_in_centimetres_is_rejected_not_silently_converted():
    """The legacy `run_metadata.json` field is `distance_cm`. 100 cm is a plausible-looking
    value that must NOT be read as 100 m — conversion is explicit, never implicit (§4.1)."""
    with pytest.raises(ManifestError, match="outside the protocol range"):
        parse_session(admissible(distance_m=100), Mode.SCORING, raw_digest_ok=True)


@pytest.mark.parametrize("bad", ["1.0 m", "1.0", True, False, None, ["1.0"]])
def test_non_numeric_distance_is_rejected(bad):
    """S12R-10 R2 added `True` and the clean numeric string `"1.0"`: `float(True)` is **1.0**,
    which sits inside the protocol range, so a boolean parsed as a valid 1.0 m distance."""
    with pytest.raises(ManifestError, match="distance_m"):
        parse_session(admissible(distance_m=bad), Mode.SCORING, raw_digest_ok=True)


@pytest.mark.parametrize("bad", ["1785000000.25", True, "not-an-epoch", [1785000000.25]])
def test_frame0_epoch_must_be_a_number_not_a_string(bad):
    """S12R-10 R2. `float("1785000000.25")` succeeded, so a string origin parsed as valid —
    in the single field every window boundary and reference span is measured from."""
    with pytest.raises(ManifestError, match="frame0_epoch"):
        parse_session(admissible(frame0_epoch=bad), Mode.SCORING, raw_digest_ok=True)


@pytest.mark.parametrize("bad", ["standing", "supine", "Seated", "seated ", "", None])
def test_posture_must_equal_the_canonical_seated(bad):
    """The estimand fixes posture; a differing session is not a member of this design."""
    with pytest.raises(ManifestError):
        parse_session(admissible(posture=bad), Mode.SCORING, raw_digest_ok=True)
    assert CANONICAL_POSTURE == "seated"


# ── Controlled vocabularies ───────────────────────────────────────────────────

@pytest.mark.parametrize(
    "key, bad",
    [("arm", "sitting"), ("data_role", "confirmatory"), ("retry_status", "redone"),
     ("disposition", "maybe")],
)
def test_unknown_enum_values_are_rejected_and_the_error_lists_the_allowed_set(key, bad):
    with pytest.raises(ManifestError) as exc:
        parse_session(admissible(**{key: bad}), Mode.SCORING, raw_digest_ok=True)
    assert key in str(exc.value) and "is not one of" in str(exc.value)


def test_paced_arm_requires_a_commanded_rate_from_the_frozen_rotation():
    for rate in (12, 15, 18):
        assert parse_session(paced(rate), Mode.SCORING, raw_digest_ok=True).commanded_rate_bpm == rate
    with pytest.raises(ManifestError, match="commanded_rate_bpm is missing"):
        parse_session(admissible(arm="paced"), Mode.SCORING, raw_digest_ok=True)
    with pytest.raises(ManifestError, match="not one of"):
        parse_session(paced(16), Mode.SCORING, raw_digest_ok=True)


@pytest.mark.parametrize("bad", [12.9, 12.0, "12", True, None])
def test_commanded_rate_must_be_an_exact_int(bad):
    """S12R-10: `int(12.9)` silently became 12 — a producer bug promoted into a valid frozen
    rate, which would then mis-file the session in the M3R-31 rate allocation (4/3/3)."""
    with pytest.raises(ManifestError, match="commanded_rate_bpm"):
        parse_session(paced(12, commanded_rate_bpm=bad), Mode.SCORING, raw_digest_ok=True)


def test_natural_arm_must_not_carry_a_commanded_rate():
    with pytest.raises(ManifestError, match="has no commanded rate"):
        parse_session(admissible(arm="natural", commanded_rate_bpm=12), Mode.SCORING, raw_digest_ok=True)


def test_frame0_epoch_must_be_finite():
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ManifestError, match="frame0_epoch"):
            parse_session(admissible(frame0_epoch=bad), Mode.SCORING, raw_digest_ok=True)


def test_frame0_epoch_may_be_fractional():
    """It is a synchronised clock reading, not an integer second (§7 requires the
    fractional case to work end to end)."""
    m = parse_session(admissible(frame0_epoch=1785000000.9375), Mode.SCORING, raw_digest_ok=True)
    assert m.frame0_epoch == pytest.approx(1785000000.9375)


# ── SessionDisposition recomputation: one NAMED negative test per rule (M4R-04) ────────

def test_recompute_admits_a_clean_session():
    verdict, reasons, flags = recompute_disposition(admissible(), "S01", raw_digest_ok=True)
    assert verdict is SessionDisposition.ADMITTED and reasons == () and flags == ()


@pytest.mark.parametrize("key", ["clock_offset_start_s", "clock_offset_end_s"])
def test_rule_clock_offset_exceeds_one_second_at_either_end(key):
    """§6: NTP-synced, max +/-1 s, re-checked at session end. Tested AT the boundary:
    exactly 1.0 s is admissible, a hair beyond is not."""
    assert recompute_disposition(admissible(**{key: 1.0}), "S", raw_digest_ok=True)[0] is SessionDisposition.ADMITTED
    assert recompute_disposition(admissible(**{key: -1.0}), "S", raw_digest_ok=True)[0] is SessionDisposition.ADMITTED
    for beyond in (1.0001, -1.0001, 3.0):
        verdict, reasons, _ = recompute_disposition(admissible(**{key: beyond}), "S", raw_digest_ok=True)
        assert verdict is SessionDisposition.EXCLUDED
        assert f"{key}_exceeds_{MAX_CLOCK_OFFSET_S:g}s" in reasons


@pytest.mark.parametrize("key", ["clock_offset_start_s", "clock_offset_end_s"])
def test_a_missing_or_non_finite_clock_offset_is_an_ERROR_not_an_exclusion(key):
    """S12R-10. A missing or NaN offset is a malformed manifest, not a §6 disposition.
    Recording it as an exclusion *reason* would put a fabricated cause into the study-level
    reason counts §6 requires ('counts and reasons at every level')."""
    for bad in (None, float("nan"), float("inf"), "0.2"):
        with pytest.raises(ManifestError, match=key):
            recompute_disposition(admissible(**{key: bad}), "S", raw_digest_ok=True)


def test_rule_stored_checksum_failed():
    """§6 item 4: 'a stored file checksum fails' -> corrupt, not admitted.

    The fact is now DERIVED from the bytes on disk (S12R-03 R2) rather than read from a
    manifest field, so it arrives as a keyword. `verify_bound_files` is what produces it."""
    verdict, reasons, _ = recompute_disposition(admissible(), "S", raw_digest_ok=False)
    assert verdict is SessionDisposition.EXCLUDED and "stored_checksum_failed" in reasons


@pytest.mark.parametrize("bad", [None, "false", "true", 0, 1, "", "no"])
def test_the_raw_digest_fact_must_be_a_derived_bool(bad):
    """`checksum_ok` is **gone from the schema** (S12R-03 R2): retaining it, even
    cross-checked, would have kept two independently editable declarations of one fact, and a
    mismatch would still need someone to decide which controls the frozen disposition.

    What remains is the derived keyword, and it is exact-typed for the same reason the old
    field was — `bool("false")` is **True**, which is how a FAILED checksum was once
    ADMITTED. There is deliberately no default: a caller that has not verified the file
    cannot obtain an 'admitted' verdict by omission."""
    with pytest.raises(ManifestError, match="raw_digest_ok"):
        recompute_disposition(admissible(), "S", raw_digest_ok=bad)


def test_the_manifest_can_no_longer_declare_its_own_checksum_result():
    """The double-source defect, pinned shut: a manifest that still carries `checksum_ok`
    must not be able to influence the disposition."""
    from src.m4.manifest import _REQUIRED_SCORING_FIELDS

    assert "checksum_ok" not in {k for k, _ in _REQUIRED_SCORING_FIELDS}
    verdict, _, _ = recompute_disposition(
        admissible(checksum_ok=False), "S", raw_digest_ok=True
    )
    assert verdict is SessionDisposition.ADMITTED, (
        "a stray checksum_ok field must be inert — only the derived digest decides"
    )


def test_a_trailing_partial_window_is_RETAINED_not_excluded():
    """§6 item 4, verbatim: a session that reached its intended duration 'with all complete
    windows plus an incomplete trailing partial window is RETAINED - that tail window is
    simply unscored'. The first draft excluded on ANY truncation, which would have silently
    discarded admissible sessions."""
    # 600 s intended => 20 expected windows. 12010 frames stored = 20 complete + a 10-frame
    # tail; the truncation removed only part of that tail.
    verdict, reasons, flags = recompute_disposition(
        admissible(truncation_bytes=4096, n_frames=12010), "S", raw_digest_ok=True
    )
    assert verdict is SessionDisposition.ADMITTED, reasons
    assert "raw_truncated_trailing" in flags, "truncation must still be REPORTED"


def test_a_completed_run_whose_LAST_window_is_short_is_still_RETAINED():
    """S12R-01, the exact case the removed predicate got wrong. 600 s intended => 20 windows;
    11,999 stored frames means the final window holds 599 of 600 frames. §6 item 4 retains
    it — "all complete windows plus an incomplete trailing partial window is RETAINED" — but
    `floor(11999/600)=19 < floor(600*20/600)=20` made the old rule call it corrupt.

    This is a **regression test for an over-exclusion**: a wrong answer here silently drops
    an admissible session out of a pre-registered analysis, which looks like caution."""
    verdict, reasons, flags = recompute_disposition(
        admissible(truncation_bytes=4096, n_frames=11999), "S", raw_digest_ok=True
    )
    assert verdict is SessionDisposition.ADMITTED, reasons
    assert "raw_truncated_trailing" in flags


def test_the_item_4_truncation_limb_is_UNIMPLEMENTED_and_escalated():
    """S12R-01 is an open escalation, not a resolved rule, and this test pins that state so
    the gap cannot be forgotten or quietly re-filled.

    §6 item 4 excludes a truncation that "cuts into a mid-recording window", naming
    `mirror_truncated_bytes` as the mechanism — but that field is
    `file_size % bytes_per_frame` (`scripts/live_demo.py` `LiveFrameSource._loop`), a
    sub-frame remainder strictly smaller than one frame. It can never locate a mid-file cut.
    Until the conflict is resolved at the M0 freeze, **no truncation value excludes**; every
    one is flagged and retained. Delete this test only together with that resolution."""
    for n_frames, truncation in ((12000, 1), (11999, 4096), (600, 65535), (0, 12)):
        verdict, reasons, flags = recompute_disposition(
            admissible(truncation_bytes=truncation, n_frames=n_frames,
                       actual_duration_s=600.0), "S", raw_digest_ok=True
        )
        assert verdict is SessionDisposition.ADMITTED, (n_frames, truncation, reasons)
        assert "raw_truncated_trailing" in flags
    assert not any(
        "truncat" in r
        for r in recompute_disposition(admissible(truncation_bytes=4096), "S", raw_digest_ok=True)[1]
    ), "no exclusion reason may mention truncation while the limb is escalated"


@pytest.mark.parametrize("bad", [None, -1, 4096.0, "4096", True])
def test_truncation_bytes_must_be_a_non_negative_exact_int(bad):
    with pytest.raises(ManifestError, match="truncation_bytes"):
        recompute_disposition(admissible(truncation_bytes=bad), "S", raw_digest_ok=True)


def test_packet_loss_FLAGS_the_session_and_never_excludes_it():
    """§6 item 4, verbatim: packet loss above the frozen tolerance 'flags the session
    (reported) but does not by itself exclude it; the per-frame validity map decides which
    windows are radar-NaN'. The first draft excluded on ANY packet loss."""
    # 4 % - under tolerance: no flag, no exclusion.
    verdict, reasons, flags = recompute_disposition(
        admissible(packets_received=100000, packets_dropped=4000), "S", raw_digest_ok=True
    )
    assert verdict is SessionDisposition.ADMITTED and flags == ()
    # 6 % - over tolerance: FLAGGED, still admitted.
    verdict, reasons, flags = recompute_disposition(
        admissible(packets_received=100000, packets_dropped=6000, n_invalid_frames=120), "S", raw_digest_ok=True
    )
    assert verdict is SessionDisposition.ADMITTED, reasons
    assert any("packet_loss_above" in f for f in flags)


def test_packet_loss_flag_boundary_is_strictly_greater_than_five_percent():
    at = recompute_disposition(
        admissible(packets_received=100000, packets_dropped=5000), "S", raw_digest_ok=True
    )[2]
    over = recompute_disposition(
        admissible(packets_received=100000, packets_dropped=5001), "S", raw_digest_ok=True
    )[2]
    assert at == (), "exactly 5 % is not 'above' the tolerance"
    assert any("packet_loss_above" in f for f in over)
    assert PACKET_LOSS_FLAG_RATIO == 0.05


@pytest.mark.parametrize(
    "over",
    [{"packets_received": None}, {"packets_dropped": None}, {"packets_dropped": -1},
     {"packets_received": -1}, {"packets_dropped": 1.5}, {"packets_received": "100000"},
     {"packets_dropped": True}],
)
def test_malformed_packet_counts_are_an_ERROR_not_an_exclusion(over):
    """S12R-10. `packets_dropped=-1` previously became the invented exclusion reason
    `packet_counts_negative` and — because an operator who also wrote `disposition: excluded`
    got agreement — parsed clean. §6 names no such gate."""
    with pytest.raises(ManifestError, match="packets_"):
        recompute_disposition(admissible(**over), "S", raw_digest_ok=True)


@pytest.mark.parametrize(
    "received, dropped", [(10, 90), (100, 101), (1, 1000), (10, 10)],
)
def test_packets_dropped_MAY_exceed_packets_received(received, dropped):
    """S12R-13 — a defect introduced by the S12R-10 fix, caught on review.

    The counters are **not a partition**: `LiveFrameSource._loop` increments `n_received` by
    one per arriving packet, but `n_dropped` by the **size of each sequence gap**. Severe
    loss legitimately gives 10 received / 90 dropped. §6 item 4 freezes
    `n_dropped / n_received > 5 %` as flag-only with **no upper bound**, so these must load,
    flag, and stay ADMITTED.

    Rejecting them would have made precisely the worst-loss sessions unloadable — dropping
    the hardest data and inflating coverage, which is the exact failure mode §6 item 4's
    flag-don't-exclude rule exists to prevent.
    """
    verdict, reasons, flags = recompute_disposition(
        admissible(packets_received=received, packets_dropped=dropped), "S", raw_digest_ok=True
    )
    assert verdict is SessionDisposition.ADMITTED, reasons
    assert any("packet_loss_above" in f for f in flags)


def test_rule_protocol_abort_did_not_reach_intended_duration():
    """§6 item 3 / M3R-37: the discriminator is one question - did the run reach its
    intended duration?"""
    verdict, reasons, _ = recompute_disposition(
        admissible(intended_duration_s=600.0, actual_duration_s=412.0, early_stop=True), "S", raw_digest_ok=True
    )
    assert verdict is SessionDisposition.EXCLUDED
    assert "protocol_abort_did_not_reach_intended_duration" in reasons
    # Reaching or exceeding the intended duration is admissible.
    assert recompute_disposition(admissible(actual_duration_s=600.0), "S", raw_digest_ok=True)[0] is SessionDisposition.ADMITTED
    assert recompute_disposition(admissible(actual_duration_s=601.0), "S", raw_digest_ok=True)[0] is SessionDisposition.ADMITTED


def test_early_stop_contradicting_the_durations_RAISES_and_is_not_an_exclusion_reason():
    """S12R-02. `early_stop=True` with `actual >= intended` is self-contradictory, but §6
    names no such gate: the M3R-37 discriminator is the duration question **alone**. The
    first draft invented `early_stop_contradicts_durations` as an exclusion *reason*, which
    was doubly wrong — unsourced, and invisible whenever the operator also wrote
    `disposition: excluded`, because then the two agreed and the manifest parsed clean."""
    with pytest.raises(ManifestError, match="cannot be both halted early and complete"):
        recompute_disposition(admissible(early_stop=True, actual_duration_s=600.0), "S", raw_digest_ok=True)
    # It must not be reachable through the operator-agreement path either.
    with pytest.raises(ManifestError, match="cannot be both halted early and complete"):
        parse_session(
            admissible(early_stop=True, actual_duration_s=600.0, disposition="excluded"),
            Mode.SCORING, raw_digest_ok=True,
        )
    # An early stop that genuinely fell short is still the ordinary item-3 exclusion.
    verdict, reasons, _ = recompute_disposition(
        admissible(early_stop=True, actual_duration_s=412.0), "S", raw_digest_ok=True
    )
    assert verdict is SessionDisposition.EXCLUDED
    assert "protocol_abort_did_not_reach_intended_duration" in reasons


@pytest.mark.parametrize(
    "over",
    [{"intended_duration_s": None}, {"actual_duration_s": None},
     {"actual_duration_s": float("nan")}, {"intended_duration_s": "600"},
     {"actual_duration_s": -1.0}, {"early_stop": None}, {"early_stop": "true"},
     {"early_stop": 0}],
)
def test_malformed_duration_or_early_stop_fields_are_an_ERROR(over):
    with pytest.raises(ManifestError):
        recompute_disposition(admissible(**over), "S", raw_digest_ok=True)


def test_rule_superseded_by_retry():
    verdict, reasons, _ = recompute_disposition(admissible(retry_status="superseded"), "S", raw_digest_ok=True)
    assert verdict is SessionDisposition.EXCLUDED and "superseded_by_retry" in reasons
    # A retry that IS the kept session stays admissible.
    assert recompute_disposition(admissible(retry_status="retry"), "S", raw_digest_ok=True)[0] is SessionDisposition.ADMITTED


def test_invalid_frames_exceeding_the_total_RAISES_and_is_not_an_exclusion_reason():
    """S12R-10. Impossible counts mean the producer is broken; §6 has no disposition for
    "the manifest is internally contradictory", and inventing one would report a cause that
    never happened in the study's exclusion table."""
    with pytest.raises(ManifestError, match="exceeds n_frames"):
        recompute_disposition(admissible(n_frames=100, n_invalid_frames=101), "S", raw_digest_ok=True)


@pytest.mark.parametrize(
    "over",
    [{"n_invalid_frames": -1}, {"n_frames": -1}, {"n_frames": None},
     {"n_invalid_frames": 12.0}, {"n_frames": "12000"}],
)
def test_malformed_frame_counts_are_an_ERROR(over):
    with pytest.raises(ManifestError, match="n_(invalid_)?frames"):
        recompute_disposition(admissible(**over), "S", raw_digest_ok=True)


def test_multiple_failing_rules_are_all_reported_not_just_the_first():
    """A manifest with three defects must name three, or fixing one reveals the next."""
    verdict, reasons, _ = recompute_disposition(
        admissible(actual_duration_s=1.0, clock_offset_end_s=9.0), "S", raw_digest_ok=False
    )
    assert verdict is SessionDisposition.EXCLUDED
    assert {"stored_checksum_failed", "protocol_abort_did_not_reach_intended_duration",
            "clock_offset_end_s_exceeds_1s"} <= set(reasons)


# ── Operator vs recomputed disagreement (the M4R-04 headline) ─────────────────

def test_operator_admitted_but_M4_recomputes_excluded_raises():
    with pytest.raises(ManifestError) as exc:
        parse_session(admissible(disposition="admitted"), Mode.SCORING, raw_digest_ok=False)
    msg = str(exc.value)
    assert "recomputes" in msg and "stored_checksum_failed" in msg


def test_operator_excluded_but_M4_recomputes_admitted_also_raises():
    """The check is symmetric on purpose. An operator excluding a session M4 sees as clean
    is just as much a disagreement — silently accepting it would let a session be dropped
    for an unrecorded reason, which is an unlogged degree of freedom."""
    with pytest.raises(ManifestError) as exc:
        parse_session(admissible(disposition="excluded"), Mode.SCORING, raw_digest_ok=True)
    assert "recomputes" in str(exc.value)


def test_agreement_on_excluded_is_accepted_and_keeps_the_reasons():
    m = parse_session(
        admissible(disposition="excluded", actual_duration_s=10.0, early_stop=True),
        Mode.SCORING, raw_digest_ok=True,
    )
    assert m.disposition is SessionDisposition.EXCLUDED
    assert "protocol_abort_did_not_reach_intended_duration" in m.disposition_reasons


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
    require_scoring_mode([parse_session(admissible(), Mode.SCORING, raw_digest_ok=True)], "HR agreement")


# ── §3.1 role/mode matrix (S12R-11) ───────────────────────────────────────────

@pytest.mark.parametrize("role", ["development", "engineering", "pilot"])
def test_a_never_scored_data_role_cannot_be_parsed_in_scoring_mode(role):
    """§3.1 makes engineering "never scored, never evaluation", development "never
    confirmatory/headline" and the pilot "excluded from confirmatory metrics".

    Mode alone was not a control: mode is supplied out-of-band by the caller, so a fully
    populated `data_role="development"` manifest parsed as SCORING, reported
    `is_scorable=True`, and `require_scoring_mode` waved it through. The bypass had to be
    closed at the role, not the mode.
    """
    with pytest.raises(ManifestError, match="may never produce a"):
        parse_session(admissible(data_role=role), Mode.SCORING, raw_digest_ok=True)


@pytest.mark.parametrize("role", ["evaluation", "collision"])
def test_a_scoring_eligible_data_role_is_accepted(role):
    """`evaluation` is "the confirmatory evidence base"; M7 `collision` is confirmatory for
    any estimator not fit on it, so the schema admits it and §3.2's pooling table carries
    the per-method exclusion."""
    m = parse_session(admissible(data_role=role), Mode.SCORING, raw_digest_ok=True)
    assert m.is_scorable


@pytest.mark.parametrize("role", ["development", "engineering", "pilot"])
def test_a_never_scored_role_is_not_scorable_even_if_constructed_directly(role):
    """Second lock: `SessionManifest` is directly constructible, so `is_scorable` must not
    trust that `parse_session` was the only way in."""
    from src.m4.manifest import SessionManifest

    m = SessionManifest(mode=Mode.SCORING, session_id="x", data_role=DataRole(role))
    assert not m.is_scorable
    with pytest.raises(ManifestError, match=role):
        require_scoring_mode([m], "HR agreement")


def test_a_never_scored_role_loads_fine_in_development_mode():
    """The bar is on *scoring*, not on loading: M4 is meant to run on the 4 development
    captures and label the output exploratory / apparent / in-sample."""
    m = parse_session({"session_id": "d1", "data_role": "development"}, Mode.DEVELOPMENT)
    assert m.data_role is DataRole.DEVELOPMENT and not m.is_scorable


# ── §4 commanded-rate schedule (S12R-09) ──────────────────────────────────────

def test_a_paced_session_requires_its_commanded_rate_schedule():
    fields = paced(15)
    del fields["commanded_rate_schedule"]
    with pytest.raises(ManifestError, match="commanded_rate_schedule is missing"):
        parse_session(fields, Mode.SCORING, raw_digest_ok=True)


def test_a_natural_session_must_not_carry_a_schedule():
    with pytest.raises(ManifestError, match="Only the paced arm"):
        parse_session(
            admissible(commanded_rate_schedule=[{"commanded_rate_bpm": 12, "start_s": 0.0}]),
            Mode.SCORING, raw_digest_ok=True,
        )


@pytest.mark.parametrize(
    "schedule, match",
    [
        ([], "non-empty"),
        ("12bpm", "non-empty"),
        ([["commanded_rate_bpm", 12]], "must be an object"),
        ([12], "must be an object"),
        (["12bpm"], "must be an object"),
        ([{"commanded_rate_bpm": 12, "start_s": 0.0}, None], "must be an object"),
        ([{"commanded_rate_bpm": 12}], "start_s"),
        ([{"start_s": 0.0}], "commanded_rate_bpm"),
        ([{"commanded_rate_bpm": 12.5, "start_s": 0.0}], "commanded_rate_bpm"),
        ([{"commanded_rate_bpm": 12, "start_s": 30.0}], "start_s must be 0"),
        ([{"commanded_rate_bpm": 12, "start_s": 0.0},
          {"commanded_rate_bpm": 15, "start_s": 0.0}], "does not increase"),
    ],
)
def test_a_malformed_rate_schedule_is_rejected(schedule, match):
    with pytest.raises(ManifestError, match=match):
        parse_session(paced(12, commanded_rate_schedule=schedule), Mode.SCORING, raw_digest_ok=True)


def test_a_single_entry_schedule_must_agree_with_the_scalar_rate():
    with pytest.raises(ManifestError, match="schedule declares"):
        parse_session(
            paced(12, commanded_rate_schedule=[{"commanded_rate_bpm": 15, "start_s": 0.0}]),
            Mode.SCORING, raw_digest_ok=True,
        )


_SWEEP = [
    {"commanded_rate_bpm": 12, "start_s": 0.0},
    {"commanded_rate_bpm": 15, "start_s": 120.0},
    {"commanded_rate_bpm": 18, "start_s": 240.0},
    {"commanded_rate_bpm": 21, "start_s": 360.0},
]


def test_the_stepped_diagnostic_sweep_CANNOT_be_a_scoring_study_session():
    """S12R-09 R2. The previous version of this test asserted the opposite and was wrong.

    `notes/protocol.md` heads the stepped 12->15->18->21 capture "Diagnostic arm" and states
    it "is a *method development* capture, **not a study session**"; §3.2 makes the paced
    commanded rate **between-subject**. Allowing a multi-entry schedule in scoring mode
    imported a development protocol into the frozen study estimand.
    """
    with pytest.raises(ManifestError, match="exactly one"):
        parse_session(paced(12, commanded_rate_schedule=_SWEEP), Mode.SCORING, raw_digest_ok=True)


def test_the_existing_paced16_capture_loads_in_development_mode():
    """S12R-14. `notes/capture_inventory.md` records `massimo2` as **paced 16 bpm**, and it is
    one of the three reference-bearing existing captures. The (12, 15, 18) rotation is the
    **study** allocation (§1, M3R-31) and must bind SCORING only — enforcing it everywhere
    made the capture unloadable in the mode plan §2.2/§4.1 created for exactly these four
    captures, so M4's development run could never have included it.

    The pre-existing development sweep test did not catch this because its scalar rate is 12;
    it exercised the schedule entries, never the scalar's membership check.
    """
    m = parse_session(
        {"session_id": "20260713_182002_massimo2", "arm": "paced", "commanded_rate_bpm": 16,
         "commanded_rate_schedule": [{"commanded_rate_bpm": 16, "start_s": 0.0}]},
        Mode.DEVELOPMENT,
    )
    assert m.commanded_rate_bpm == 16
    assert not m.is_scorable, "loadable for development is not scorable"


def test_paced16_is_still_rejected_in_scoring_mode():
    """The other half: relaxing development must not open the study estimand."""
    with pytest.raises(ManifestError, match="not one of"):
        parse_session(paced(16), Mode.SCORING, raw_digest_ok=True)


@pytest.mark.parametrize("bad", [0, -1, -16])
def test_development_mode_still_requires_a_positive_integer_rate(bad):
    """Relaxed to the *rotation*, not to validation: a development rate is still an exact
    positive integer."""
    with pytest.raises(ManifestError, match="commanded_rate_bpm"):
        parse_session(
            {"session_id": "d", "arm": "paced", "commanded_rate_bpm": bad}, Mode.DEVELOPMENT
        )


def test_development_mode_still_enforces_scalar_schedule_consistency():
    with pytest.raises(ManifestError, match="schedule declares"):
        parse_session(
            {"session_id": "d", "arm": "paced", "commanded_rate_bpm": 16,
             "commanded_rate_schedule": [{"commanded_rate_bpm": 12, "start_s": 0.0}]},
            Mode.DEVELOPMENT,
        )


def test_the_stepped_sweep_still_loads_in_development_mode():
    """It is a real capture and must remain representable — just never as study evidence.
    Its 21 bpm step is outside the M3R-31 rotation, which is fine here and only here."""
    m = parse_session(
        {"session_id": "sweep", "arm": "paced", "commanded_rate_bpm": 12,
         "commanded_rate_schedule": _SWEEP},
        Mode.DEVELOPMENT,
    )
    assert len(m.commanded_rate_schedule) == 4 and not m.is_scorable


# ── Provenance binding: path + SHA-256 (S12R-09) ──────────────────────────────

@pytest.mark.parametrize(
    "key",
    ["raw_sha256", "frame_validity_map_sha256", "capture_config_sha256", "masimo_sha256",
     "settle_evidence_sha256"],
)
@pytest.mark.parametrize("bad", ["", "abc", "A" * 64, "g" * 64, "a" * 63, "a" * 65, 12345])
def test_every_bound_hash_must_look_like_a_sha256(key, bad):
    """§4 binds each artifact "by path + SHA-256". A hash that cannot be a SHA-256 cannot
    check anything, and CLAUDE.md §3.1 requires every result to trace to hashed input."""
    with pytest.raises(ManifestError, match=key):
        parse_session(admissible(**{key: bad}), Mode.SCORING, raw_digest_ok=True)


@pytest.mark.parametrize(
    "key", ["raw_path", "frame_validity_map_path", "capture_config_path", "masimo_path",
            "capture_git_commit", "subject_id", "settle_evidence_path"],
)
@pytest.mark.parametrize("bad", ["", "   ", 17, ["a"]])
def test_every_bound_path_must_be_a_non_empty_string(key, bad):
    with pytest.raises(ManifestError, match=key):
        parse_session(admissible(**{key: bad}), Mode.SCORING, raw_digest_ok=True)


def test_the_capture_config_is_bound_by_path_as_well_as_hash():
    """§4's opening sentence binds every artifact "by path + SHA-256". The capture config
    carried only a hash, so nothing recorded *which file* the hash was of."""
    fields = admissible()
    del fields["capture_config_path"]
    with pytest.raises(ManifestError, match="capture_config_path"):
        parse_session(fields, Mode.SCORING, raw_digest_ok=True)


# ── retry / replacement reason (S12R-09, partial — see S12R-05) ───────────────

@pytest.mark.parametrize("status", ["retry", "superseded"])
def test_a_non_original_attempt_requires_its_reason(status):
    """Plan §4 Disposition binds retry/replacement status **and reason**; §6 item 7 requires
    the discarded attempt to be "logged with reason"."""
    fields = admissible(retry_status=status)
    if status == "superseded":
        fields["disposition"] = "excluded"
    with pytest.raises(ManifestError, match="retry_reason"):
        parse_session(fields, Mode.SCORING, raw_digest_ok=True)


def test_an_original_attempt_must_not_carry_a_retry_reason():
    with pytest.raises(ManifestError, match="replaced nothing"):
        parse_session(admissible(retry_reason="subject moved"), Mode.SCORING, raw_digest_ok=True)


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
    base = materialise(tmp_path)
    sessions = _load(tmp_path, base, dict(base, session_id="S02"))
    assert [s.session_id for s in sessions] == ["S01_natural", "S02"]


def test_load_manifest_rejects_a_duplicate_session_id(tmp_path):
    base = materialise(tmp_path)
    with pytest.raises(ManifestError, match="duplicate session_id"):
        _load(tmp_path, base, dict(base))


def test_load_manifest_rejects_a_malformed_document(tmp_path):
    with pytest.raises(ManifestError, match="'sessions' array"):
        load_manifest(_write(tmp_path, {"rows": []}), Mode.SCORING)
    doc = manifest_doc()
    with pytest.raises(ManifestError, match="must be an array"):
        load_manifest(_write(tmp_path, {**doc, "sessions": {}}), Mode.SCORING)
    with pytest.raises(ManifestError, match="must be a dict"):
        load_manifest(_write(tmp_path, {**doc, "sessions": ["not-a-dict"]}), Mode.SCORING)


# ── §4: the manifest is VERSIONED (S12R-09) ───────────────────────────────────

def test_load_manifest_requires_a_schema_version(tmp_path):
    """Plan §4 calls for a versioned manifest and there was neither a field nor a check.
    Without one, a later schema change silently reinterprets every already-written session
    — the root provenance record for the whole study."""
    with pytest.raises(ManifestError, match="manifest_schema_version is missing"):
        load_manifest(
            _write(tmp_path, {"sessions": [materialise(tmp_path)]}),
            Mode.SCORING, root=tmp_path,
        )


@pytest.mark.parametrize("bad", [0, 1, 3, 99, "2", 2.0, True, None])
def test_load_manifest_refuses_an_unknown_schema_version(tmp_path, bad):
    """`1` is in the list deliberately: v1 used the binary `admission` field, so reading a v1
    document as v2 would silently reinterpret the disposition partition (S12R-06)."""
    doc = {"manifest_schema_version": bad, "sessions": [admissible()]}
    with pytest.raises(ManifestError, match="manifest_schema_version"):
        load_manifest(_write(tmp_path, doc), Mode.SCORING)


def test_session_id_must_be_a_non_empty_string(tmp_path):
    for bad in ("", None, 17):
        with pytest.raises(ManifestError, match="session_id"):
            parse_session({"session_id": bad}, Mode.DEVELOPMENT)


# ── S12R-04: the SETTLE CRITERION, derived not declared ───────────────────────

def test_settle_thresholds_pass_AT_their_equality_boundaries():
    """`notes/protocol.md`: "PR spread <= 5 bpm ... differs ... by <= 3 bpm". Both limbs are
    inclusive, so 5.0 and 3.0 exactly are PASSES. Tested on both sides of each boundary."""
    m = parse_session(
        admissible(settle_pr_spread_bpm=5.0, settle_pr_drift_bpm=3.0),
        Mode.SCORING, raw_digest_ok=True,
    )
    assert m.disposition is SessionDisposition.ADMITTED


@pytest.mark.parametrize(
    "over, reason",
    [
        ({"settle_pr_spread_bpm": 5.0001}, "settle_pr_spread_exceeds_5bpm"),
        ({"settle_pr_spread_bpm": 9.0}, "settle_pr_spread_exceeds_5bpm"),
        ({"settle_pr_drift_bpm": 3.0001}, "settle_pr_drift_exceeds_3bpm"),
        ({"settle_pr_drift_bpm": 7.5}, "settle_pr_drift_exceeds_3bpm"),
    ],
)
def test_a_failed_settle_criterion_is_an_item_3_protocol_abort(over, reason):
    """§6 item 3's other limb: "the settle criterion is **not met**, or the protocol run is
    deliberately halted". Only the second was implemented before S12R-04."""
    verdict, reasons, _ = recompute_disposition(admissible(**over), "S", raw_digest_ok=True)
    assert verdict is SessionDisposition.EXCLUDED
    assert reason in reasons


def test_both_settle_limbs_are_reported_when_both_fail():
    _, reasons, _ = recompute_disposition(
        admissible(settle_pr_spread_bpm=9.0, settle_pr_drift_bpm=7.5), "S", raw_digest_ok=True
    )
    assert {"settle_pr_spread_exceeds_5bpm", "settle_pr_drift_exceeds_3bpm"} <= set(reasons)


@pytest.mark.parametrize(
    "over",
    [{"settle_pr_spread_bpm": "2.0"}, {"settle_pr_drift_bpm": True},
     {"settle_pr_spread_bpm": -1.0}, {"settle_pr_drift_bpm": float("nan")}],
)
def test_malformed_settle_evidence_is_an_ERROR_not_a_disposition(over):
    with pytest.raises(ManifestError, match="settle_pr_"):
        recompute_disposition(admissible(**over), "S", raw_digest_ok=True)


# ── S12R-12: discriminated record kinds ───────────────────────────────────────

def test_a_pre_capture_attempt_loads_and_is_excluded_with_its_derived_reason():
    """§6 items 3 and 5 require these to be logged, but they happen before recording, so they
    have no capture artifacts. The old schema could log one only by fabricating provenance."""
    m = parse_session(pre_capture(), Mode.SCORING, raw_digest_ok=True)
    assert m.record_kind is RecordKind.PRE_CAPTURE_ATTEMPT
    assert m.disposition is SessionDisposition.EXCLUDED
    assert "settle_pr_spread_exceeds_5bpm" in m.disposition_reasons
    assert m.frame0_epoch is None and m.raw_path is None


def test_a_clock_resync_attempt_is_an_item_5_disposition():
    """§6 item 5: offset > +/-1 s -> "resync and restart before recording"."""
    m = parse_session(
        pre_capture(settle_pr_spread_bpm=2.0, clock_offset_start_s=3.0), Mode.SCORING
    )
    assert m.disposition is SessionDisposition.EXCLUDED
    assert "clock_offset_start_s_exceeds_1s" in m.disposition_reasons


@pytest.mark.parametrize(
    "field, value",
    [("raw_path", "data/raw/x.bin"), ("frame0_epoch", 1785000000.0), ("n_frames", 12000),
     ("masimo_path", "data/raw/x.csv"), ("intended_duration_s", 600.0),
     ("early_stop", True), ("clock_offset_end_s", 0.1), ("capture_git_commit", "abc123")],
)
def test_a_pre_capture_attempt_must_not_carry_capture_only_fields(field, value):
    """Absence is ENFORCED, not merely permitted. Making every capture field optional on one
    class would trade the old contradiction for a space of loadable-but-invalid rows — which
    is where a fabricated-provenance record would live (CLAUDE.md §4)."""
    with pytest.raises(ManifestError, match="capture-only fields are present"):
        parse_session(pre_capture(**{field: value}), Mode.SCORING, raw_digest_ok=True)


def test_an_attempt_where_BOTH_gates_pass_is_a_contradiction():
    """If both gates passed, recording would have started and this would be a captured
    session. §6 has no disposition for an attempt that did not fail."""
    with pytest.raises(ManifestError, match="both\\s+pre-recording gates pass"):
        parse_session(
            pre_capture(settle_pr_spread_bpm=2.0, settle_pr_drift_bpm=1.0,
                        clock_offset_start_s=0.2),
            Mode.SCORING, raw_digest_ok=True,
        )


def test_an_attempt_cannot_be_recorded_as_admitted():
    with pytest.raises(ManifestError, match="recomputes"):
        parse_session(pre_capture(disposition="admitted"), Mode.SCORING, raw_digest_ok=True)


@pytest.mark.parametrize("missing", [k for k, _ in _REQUIRED_PRE_CAPTURE_FIELDS])
def test_a_pre_capture_attempt_rejects_every_missing_required_field(missing):
    fields = pre_capture()
    del fields[missing]
    with pytest.raises(ManifestError) as exc:
        parse_session(fields, Mode.SCORING, raw_digest_ok=True)
    assert missing in str(exc.value)


def test_record_kind_defaults_to_captured_session_when_absent():
    """Every existing capture is one, and the attempt kind is new with schema v2. Scoring mode
    still requires it explicitly, so a study manifest never leans on this default."""
    m = parse_session({"session_id": "d1", "arm": "natural"}, Mode.DEVELOPMENT)
    assert m.record_kind is RecordKind.CAPTURED_SESSION


def test_an_unknown_record_kind_is_rejected():
    with pytest.raises(ManifestError, match="record_kind"):
        parse_session(admissible(record_kind="aborted"), Mode.SCORING, raw_digest_ok=True)


# ── S12R-03 / S12R-07: verification happens INSIDE the scoring load path ──────

def materialise(tmp_path, fields=None, *, n_frames=12000, n_invalid=0):
    """Write every bound artifact and rewrite `fields` with the real digests.

    Verification is only meaningful against real bytes, so these tests build a genuine
    little study tree rather than asserting on a mocked hasher.
    """
    import hashlib

    import numpy as np

    fields = dict(fields or admissible())
    (tmp_path / "data" / "raw").mkdir(parents=True, exist_ok=True)
    (tmp_path / "experiments" / "study").mkdir(parents=True, exist_ok=True)

    valid = np.ones(n_frames, dtype=bool)
    valid[:n_invalid] = False
    np.save(tmp_path / fields["frame_validity_map_path"], valid)

    (tmp_path / fields["raw_path"]).write_bytes(b"\x01\x02" * 64)
    (tmp_path / fields["capture_config_path"]).write_text("window_s: 30\n", encoding="utf-8")
    (tmp_path / fields["masimo_path"]).write_text("Timestamp,Beats / min\n1,72\n", encoding="utf-8")
    (tmp_path / fields["settle_evidence_path"]).write_text("t,pr\n0,72\n", encoding="utf-8")

    for path_key, hash_key in (
        ("raw_path", "raw_sha256"),
        ("capture_config_path", "capture_config_sha256"),
        ("masimo_path", "masimo_sha256"),
        ("settle_evidence_path", "settle_evidence_sha256"),
        ("frame_validity_map_path", "frame_validity_map_sha256"),
    ):
        fields[hash_key] = hashlib.sha256(
            (tmp_path / fields[path_key]).read_bytes()
        ).hexdigest()

    fields["n_frames"] = n_frames
    fields["n_invalid_frames"] = n_invalid
    return fields


def _load(tmp_path, *fields, mode=Mode.SCORING):
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(manifest_doc(*fields)), encoding="utf-8")
    return load_manifest(p, mode, root=tmp_path)


def test_a_fully_verified_manifest_loads(tmp_path):
    (s,) = _load(tmp_path, materialise(tmp_path))
    assert s.disposition is SessionDisposition.ADMITTED
    assert s.raw_digest_ok is True


def test_a_raw_digest_mismatch_is_the_frozen_item_4_EXCLUSION(tmp_path):
    """S12R-07 R3, first row of the split: the raw file is present and readable but its
    digest differs, which is §6 item 4's "a stored file checksum fails" — a **capture
    disposition** that belongs in the study's exclusion counts, not an error."""
    fields = materialise(tmp_path)
    fields["raw_sha256"] = "0" * 64
    fields["disposition"] = "excluded"
    (s,) = _load(tmp_path, fields)
    assert s.disposition is SessionDisposition.EXCLUDED
    assert "stored_checksum_failed" in s.disposition_reasons
    assert s.raw_digest_ok is False


@pytest.mark.parametrize(
    "hash_key",
    ["capture_config_sha256", "masimo_sha256", "settle_evidence_sha256",
     "frame_validity_map_sha256"],
)
def test_a_non_raw_digest_mismatch_is_a_PROVENANCE_FAILURE(tmp_path, hash_key):
    """Second row of the split: no binding authority gives these a §6 disposition, so they
    raise instead of becoming an exclusion reason. Counting one would report a cause that
    never happened (S12R-10's principle, at the I/O layer)."""
    fields = materialise(tmp_path)
    fields[hash_key] = "0" * 64
    with pytest.raises(ManifestError, match="provenance failure"):
        _load(tmp_path, fields)


@pytest.mark.parametrize(
    "path_key",
    ["raw_path", "capture_config_path", "masimo_path", "settle_evidence_path",
     "frame_validity_map_path"],
)
def test_a_bound_file_missing_at_scoring_time_is_LOST_not_absent(tmp_path, path_key):
    """Fourth row: a file bound by path + digest that is gone was **acquired and lost**.
    Deriving §6 item 6 NO_AGREEMENT from it would let the filesystem supply both the fact and
    the verdict — the `checksum_ok` defect, one level out."""
    fields = materialise(tmp_path)
    (tmp_path / fields[path_key]).unlink()
    with pytest.raises(ManifestError, match="LOST"):
        _load(tmp_path, fields)


def test_verification_is_not_optional_for_a_direct_parse(tmp_path):
    """S12R-07 R2: a helper future callers may omit would leave the done-when violation
    exactly where it was."""
    with pytest.raises(ManifestError, match="load_manifest"):
        parse_session(admissible(), Mode.SCORING)


def test_a_bound_path_cannot_escape_the_manifest_root(tmp_path):
    fields = materialise(tmp_path)
    fields["raw_path"] = "../outside.bin"
    with pytest.raises(ManifestError, match="outside the manifest root"):
        _load(tmp_path, fields)


# ── The validity map is checked for shape and counts, not just hashed ────────

def test_the_validity_map_must_have_one_entry_per_frame(tmp_path):
    """Plan §7 names "validity-map consistency" in the **Stage 1** done-when; deferring it to
    Stage 3 was a done-when violation, not a scoping choice (S12R-07)."""
    fields = materialise(tmp_path, n_frames=1200)
    fields["n_frames"] = 1800
    with pytest.raises(ManifestError, match="one entry per frame"):
        _load(tmp_path, fields)


def test_the_validity_map_invalid_count_must_match_the_declared_count(tmp_path):
    fields = materialise(tmp_path, n_frames=1200, n_invalid=7)
    fields["n_invalid_frames"] = 3
    with pytest.raises(ManifestError, match="marks 7 invalid"):
        _load(tmp_path, fields)


def test_a_declared_invalid_count_that_matches_the_map_is_accepted(tmp_path):
    fields = materialise(tmp_path, n_frames=1200, n_invalid=7)
    (s,) = _load(tmp_path, fields)
    assert s.n_invalid_frames == 7


@pytest.mark.parametrize("arr_kind", ["float", "2d"])
def test_the_validity_map_must_be_a_1d_boolean_array(tmp_path, arr_kind):
    import hashlib

    import numpy as np

    fields = materialise(tmp_path, n_frames=1200)
    arr = (np.ones(1200, dtype=float) if arr_kind == "float"
           else np.ones((600, 2), dtype=bool))
    np.save(tmp_path / fields["frame_validity_map_path"], arr)
    fields["frame_validity_map_sha256"] = hashlib.sha256(
        (tmp_path / fields["frame_validity_map_path"]).read_bytes()
    ).hexdigest()
    with pytest.raises(ManifestError, match="1-D boolean array"):
        _load(tmp_path, fields)
