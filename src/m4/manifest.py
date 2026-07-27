"""M4 Stage 1 — the session manifest: schema, validation, disposition recomputation.

Implements `plans/m4_offline_harness.md` §4 and §4.1. The manifest binds, per session, the
identity/estimand fields, the design fields, the timebase, integrity, provenance and
disposition that M4 needs before a capture can be scored — because a run folder plus two
fields cannot form the frozen estimand sets (M4R-04: the inspected replay metadata has
`session_id="unknown"`, `posture=None`, `distance=None`, and nothing binds a run to a Masimo
file, subject, arm or data role).

Two rules shape everything here:

**Scoring mode fails loudly on any missing required field.** Not "warns", not "defaults" —
a missing field means the frozen estimand set cannot be formed, and guessing one is how a
session that was never part of the design ends up inside a pre-registered result.

**M4 recomputes the session disposition from the primitive fields and refuses to proceed
if it disagrees with the operator's verdict** (M4R-04). An operator verdict alone is an
opinion; recomputation makes it checkable. Disagreement is an error, never a silent
override in either direction — M4 does not "know better" and does not defer.

Development mode exists for the 4 existing captures (plan §2.2) and is built so its output
cannot be mistaken for scoring output: it carries `mode: DEVELOPMENT`, and
`require_scoring_mode` refuses to let a development manifest reach a scoring path.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np

# ── Controlled vocabularies ───────────────────────────────────────────────────


class Mode(str, Enum):
    """SCORING produces frozen-comparator numbers; DEVELOPMENT never can."""

    SCORING = "SCORING"
    DEVELOPMENT = "DEVELOPMENT"


class Arm(str, Enum):
    NATURAL = "natural"
    PACED = "paced"


class DataRole(str, Enum):
    """The binding data-roles table, `notes/analysis_prespec.md` §3.1."""

    DEVELOPMENT = "development"    # the 4 existing captures: exploratory, apparent/in-sample
    ENGINEERING = "engineering"    # M1 smoke test: never scored, never evaluation
    PILOT = "pilot"                # M5: post-freeze exploratory
    EVALUATION = "evaluation"      # M6: the confirmatory evidence base, never tuning
    COLLISION = "collision"        # M7: role is method-specific (see §3.1)


class RecordKind(str, Enum):
    """What kind of thing a manifest row describes (S12R-12).

    §6 items 3 and 5 require settle aborts and clock-resync restarts to be **logged**, but
    both are detected *before* recording starts, so they have no frame-0 epoch, no raw file,
    no validity map and no reference. The first schema required all of those unconditionally,
    so such an attempt could only be logged by fabricating provenance (CLAUDE.md §4 forbids
    it) — "logged always" and "requires artifacts that cannot exist" cannot both hold.

    The fix is a **discriminated** record, not a union of optional fields: making every
    capture field optional would trade one contradiction for a large space of loadable-but-
    invalid rows, and that space is where a fabricated record would eventually live. Each
    kind therefore has its own **exact** required-field set, and capture-only fields must be
    **absent** from a pre-capture attempt rather than merely omitted.
    """

    CAPTURED_SESSION = "captured_session"
    PRE_CAPTURE_ATTEMPT = "pre_capture_attempt"


class SessionDisposition(str, Enum):
    """The single §6 session-level partition key (S12R-06).

    One enum, not a binary `admission` plus a side-channel: a separate `reference_status`
    field would create combinatorial states and force the Stage 5 ledger to reconstruct §6's
    partition from two columns, while overloading "admission" with a third meaning would make
    one word mean two things.

    `NO_AGREEMENT` is §6 item 6 — "a **wholly missing Masimo file** is a separately-logged
    no-agreement session (radar-only, descriptive at most)". Such a session keeps its full
    radar/timebase/integrity binding and is structurally barred from agreement scoring; it is
    **not** an exclusion, and its windows are not an exclusion count.

    **It is derived, never declared** (S12R-06 R2, S12R-07 R3). Omitting the Masimo fields
    while writing `NO_AGREEMENT` would let the operator supply both the fact and the verdict —
    the `checksum_ok` defect again. See `_derive_reference_disposition`.
    """

    ADMITTED = "admitted"
    EXCLUDED = "excluded"
    NO_AGREEMENT = "no_agreement"


class RetryStatus(str, Enum):
    ORIGINAL = "original"
    RETRY = "retry"                # this session replaced an earlier attempt
    SUPERSEDED = "superseded"      # this session was replaced by a later attempt


#: `notes/protocol.md`: the subject "must be within 0.8-1.4 m". Inclusive at both ends
#: (plan §4.1); Stage 1 pins the equality boundaries.
DISTANCE_MIN_M = 0.8
DISTANCE_MAX_M = 1.4

#: The estimand fixes posture. A session with any other value is not a member of this design.
CANONICAL_POSTURE = "seated"

#: `notes/analysis_prespec.md` §6 item 5: NTP-synced, max +/-1 s, re-checked at session end.
MAX_CLOCK_OFFSET_S = 1.0

#: §6 item 4, frozen tolerance: `n_dropped / n_received > 5 %` **flags** the session
#: (reported) but "does not by itself exclude it" — the per-frame validity map decides which
#: WINDOWS are radar-NaN. Exceeding this never changes the session disposition.
PACKET_LOSS_FLAG_RATIO = 0.05

#: Commanded paced rates, frozen by the M3R-31 rotation (12 -> 15 -> 18).
PACED_RATES_BPM = (12, 15, 18)

#: `notes/protocol.md` SETTLE CRITERION — "mandatory, every arm, no exceptions", transcribed
#: verbatim (S12R-04). Capture must not start until **BOTH** hold, measured on the live Masimo:
#:
#:   1. **PR spread <= 5 bpm** (max - min) over a **continuous 60 s**; and
#:   2. **no monotonic drift** — PR in the last 20 s differs from the first 20 s by **<= 3 bpm**.
#:
#: Both limbs are `<=`, so **5.0 and 3.0 exactly are PASSES** — the comparison below is `>`,
#: and the equality boundary is tested on both sides. A bare operator-supplied
#: `settle_criterion_met` boolean would have been the `checksum_ok` defect again (S12R-04 R2):
#: the criterion is numerical, so M4 derives it from the measured primitives.
SETTLE_MAX_PR_SPREAD_BPM = 5.0
SETTLE_MAX_PR_DRIFT_BPM = 3.0
SETTLE_WINDOW_S = 60.0

#: Plan §4 calls for a **versioned** manifest (S12R-09). The version is a property of the
#: manifest document, not of a session, so `load_manifest` enforces it. Bump only with a
#: migration: this is the root provenance record for every session.
#: **Version 2** (S12R-06/12): `admission` became the three-valued `disposition`, `record_kind`
#: discriminates captured sessions from pre-capture attempts, and `checksum_ok` was removed in
#: favour of derived verification. A v1 document cannot be read as v2 — the disposition
#: partition changed meaning — so the reader refuses it rather than guessing.
MANIFEST_SCHEMA_VERSION = 2

#: `notes/analysis_prespec.md` §3.1 decides which data roles may ever produce a
#: frozen-comparator number (S12R-11). Everything absent from this set is barred from
#: SCORING mode at parse time — mode alone was not a control, because mode is supplied
#: out-of-band by the caller and a fully-populated `development` manifest passed as SCORING.
#:
#: * `evaluation` — "the confirmatory evidence base" (§3.1).
#: * `collision`  — "role is method-specific"; confirmatory *only* for an estimator not fit,
#:   tuned or selected on M7, so it is admissible here and the per-method exclusion is the
#:   pooling table's job (§3.2), not the schema's.
#:
#: Barred: `development` ("never confirmatory/headline"), `engineering` ("never scored, never
#: evaluation"), `pilot` ("excluded from confirmatory metrics").
_SCORING_ALLOWED_ROLES = frozenset({DataRole.EVALUATION, DataRole.COLLISION})

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ManifestError(ValueError):
    """A manifest that cannot be used as given. Always names the session and the field."""


# ── Strict primitive validation (S12R-10) ─────────────────────────────────────
#
# Presence-only checking plus `bool()`/`int()` coercion let malformed manifests become
# admission *decisions*: `checksum_ok="false"` was admitted because `bool("false")` is True,
# and `commanded_rate_bpm=12.9` silently became 12. Types are therefore checked **exactly**,
# by `type(...) is`, never `isinstance` — the same rule Stage 0 arrived at for the estimator
# adapter (S0R-02, S0R-18), and for the same reason: `isinstance` admits `np.bool_`, and
# `bool` is a subclass of `int`, so a boolean would satisfy an integer counter.
#
# A malformed primitive is a **`ManifestError`**, never an exclusion reason. Exclusion
# reasons are reported study-wide (§6: "counts and reasons at every level"), so a schema
# defect logged as a §6 disposition would put a fabricated cause into a published table.


def _exact_bool(fields: dict, key: str, session_id: str) -> bool:
    v = fields.get(key)
    if type(v) is not bool:
        raise ManifestError(
            f"session {session_id!r}: {key}={v!r} must be a JSON boolean (true/false), got "
            f"{type(v).__name__}. The string \"false\" is truthy and would have been read as "
            "a pass; nothing here coerces."
        )
    return v


def _exact_int(fields: dict, key: str, session_id: str, *, minimum: int | None = None) -> int:
    v = fields.get(key)
    if type(v) is not int:   # `type(True) is bool`, so booleans are rejected here too
        raise ManifestError(
            f"session {session_id!r}: {key}={v!r} must be an integer, got "
            f"{type(v).__name__}. Counts are never rounded or truncated — a fractional count "
            "means the producer is wrong, and silently flooring it hides that."
        )
    if minimum is not None and v < minimum:
        raise ManifestError(
            f"session {session_id!r}: {key}={v!r} must be >= {minimum}. A negative count is "
            "not a capture disposition, it is a malformed manifest."
        )
    return v


def _finite_number(
    fields: dict, key: str, session_id: str, *, minimum: float | None = None
) -> float:
    v = fields.get(key)
    if type(v) not in (int, float):
        raise ManifestError(
            f"session {session_id!r}: {key}={v!r} must be a number, got {type(v).__name__}."
        )
    f = float(v)
    if not math.isfinite(f):
        raise ManifestError(f"session {session_id!r}: {key}={v!r} must be finite.")
    if minimum is not None and f < minimum:
        raise ManifestError(f"session {session_id!r}: {key}={v!r} must be >= {minimum}.")
    return f


def _non_empty_str(fields: dict, key: str, session_id: str) -> str:
    v = fields.get(key)
    if type(v) is not str or not v.strip():
        raise ManifestError(
            f"session {session_id!r}: {key}={v!r} must be a non-empty string."
        )
    return v


def _sha256(fields: dict, key: str, session_id: str) -> str:
    v = _non_empty_str(fields, key, session_id)
    if not _SHA256_RE.match(v):
        raise ManifestError(
            f"session {session_id!r}: {key}={v!r} is not a SHA-256 digest "
            "(64 lowercase hex characters). Provenance that cannot be checked is not "
            "provenance (CLAUDE.md §3.1)."
        )
    return v


# ── Schema ────────────────────────────────────────────────────────────────────

#: Required in SCORING mode, by §4 group. Development mode may omit these — that is the
#: whole reason it exists (the 4 captures have no `frame0_epoch`, distance or posture).
_REQUIRED_SCORING_FIELDS: tuple[tuple[str, str], ...] = (
    ("record_kind", "Identity"),
    ("session_id", "Identity"),
    ("subject_id", "Identity"),
    ("arm", "Identity"),
    ("data_role", "Identity"),
    ("disposition", "Identity"),
    ("distance_m", "Design"),
    ("posture", "Design"),
    ("frame0_epoch", "Timebase"),
    ("clock_offset_start_s", "Timebase"),
    ("clock_offset_end_s", "Timebase"),
    ("raw_path", "Integrity"),
    ("raw_sha256", "Integrity"),
    ("truncation_bytes", "Integrity"),
    # `checksum_ok` is GONE (S12R-03 R2). It was an operator-supplied boolean, so the operator
    # supplied both the verdict and the fact that made M4's "objective recomputation" agree
    # with it — the exact double-source M4R-04 exists to remove. Retaining it and
    # cross-checking would have kept two independently editable declarations of one fact, and
    # a mismatch would still need someone to decide which one controls the frozen disposition.
    # The digest is now DERIVED by `verify_bound_files` from the bytes on disk.
    ("packets_received", "Integrity"),
    ("packets_dropped", "Integrity"),
    ("n_frames", "Integrity"),
    ("n_invalid_frames", "Integrity"),
    ("frame_validity_map_path", "Integrity"),
    ("frame_validity_map_sha256", "Integrity"),
    # §4's opening sentence binds every artifact "by path + SHA-256". The capture config had
    # only a hash, so nothing said *which file* the hash was of (S12R-09).
    ("capture_config_path", "Provenance"),
    ("capture_config_sha256", "Provenance"),
    ("capture_git_commit", "Provenance"),
    ("masimo_path", "Provenance"),
    ("masimo_sha256", "Provenance"),
    ("intended_duration_s", "Disposition"),
    ("actual_duration_s", "Disposition"),
    ("early_stop", "Disposition"),
    ("retry_status", "Disposition"),
    # Authority is §6 item 3 + `notes/protocol.md` SETTLE CRITERION, not §4's table (S12R-04).
    ("settle_pr_spread_bpm", "Settle"),
    ("settle_pr_drift_bpm", "Settle"),
    ("settle_evidence_path", "Settle"),
    ("settle_evidence_sha256", "Settle"),
)

#: A **pre-capture attempt** (S12R-12): a settle abort (§6 item 3) or a clock-resync restart
#: (§6 item 5), both detected *before* recording begins. It has identity, design, a timestamp
#: and the objective gate evidence — and nothing else, because nothing else exists yet.
_REQUIRED_PRE_CAPTURE_FIELDS: tuple[tuple[str, str], ...] = (
    ("record_kind", "Identity"),
    ("session_id", "Identity"),
    ("subject_id", "Identity"),
    ("arm", "Identity"),
    ("data_role", "Identity"),
    ("disposition", "Identity"),
    ("distance_m", "Design"),
    ("posture", "Design"),
    ("attempt_utc", "Timebase"),
    ("clock_offset_start_s", "Timebase"),
    ("settle_pr_spread_bpm", "Settle"),
    ("settle_pr_drift_bpm", "Settle"),
    ("settle_evidence_path", "Settle"),
    ("settle_evidence_sha256", "Settle"),
)

#: Capture-only fields, which a pre-capture attempt must **not** carry. Absence is enforced,
#: not merely permitted: the whole point of discriminating the record kinds is that "every
#: field optional on one class" would trade one contradiction for a space of loadable-but-
#: invalid rows, and that space is where a fabricated-provenance record would live
#: (CLAUDE.md §4). A value here means either the capture did happen — in which case this is
#: the wrong record kind — or someone invented one.
_FORBIDDEN_ON_PRE_CAPTURE: tuple[str, ...] = (
    "frame0_epoch", "clock_offset_end_s",
    "raw_path", "raw_sha256", "truncation_bytes",
    "packets_received", "packets_dropped", "n_frames", "n_invalid_frames",
    "frame_validity_map_path", "frame_validity_map_sha256",
    "capture_config_path", "capture_config_sha256", "capture_git_commit",
    "masimo_path", "masimo_sha256",
    "intended_duration_s", "actual_duration_s", "early_stop",
)


@dataclass(frozen=True)
class SessionManifest:
    """One validated session. Construct via `load_manifest` / `parse_session`."""

    mode: Mode
    session_id: str

    #: Which contract this row was validated against (S12R-12).
    record_kind: RecordKind = RecordKind.CAPTURED_SESSION

    # Identity / estimands
    subject_id: str | None = None
    arm: Arm | None = None
    commanded_rate_bpm: int | None = None
    data_role: DataRole | None = None
    disposition: SessionDisposition | None = None
    disposition_reasons: tuple[str, ...] = ()
    #: Reported, never excluding (§6 item 4) — e.g. packet loss above the 5 % tolerance.
    flags: tuple[str, ...] = ()

    # Design / descriptive
    distance_m: float | None = None
    posture: str | None = None

    # Timebase
    frame0_epoch: float | None = None
    clock_offset_start_s: float | None = None
    clock_offset_end_s: float | None = None
    #: Pre-capture attempts only: when the attempt was made. There is no frame 0 to date it by.
    attempt_utc: float | None = None

    # Settle evidence (§6 item 3 / `notes/protocol.md`)
    settle_pr_spread_bpm: float | None = None
    settle_pr_drift_bpm: float | None = None
    settle_evidence_path: str | None = None
    settle_evidence_sha256: str | None = None

    # Integrity
    raw_path: str | None = None
    raw_sha256: str | None = None
    truncation_bytes: int | None = None
    #: DERIVED by `verify_bound_files`, never declared in the manifest (S12R-03 R2).
    raw_digest_ok: bool | None = None
    packets_received: int | None = None
    packets_dropped: int | None = None
    n_frames: int | None = None
    n_invalid_frames: int | None = None
    frame_validity_map_path: str | None = None
    frame_validity_map_sha256: str | None = None

    # Provenance
    capture_config_path: str | None = None
    capture_config_sha256: str | None = None
    capture_git_commit: str | None = None
    masimo_path: str | None = None
    masimo_sha256: str | None = None
    commanded_rate_schedule: tuple[dict, ...] = ()

    # Disposition
    intended_duration_s: float | None = None
    actual_duration_s: float | None = None
    early_stop: bool | None = None
    retry_status: RetryStatus | None = None
    retry_reason: str | None = None

    raw: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def is_scorable(self) -> bool:
        """Only a SCORING-mode manifest **in a scoring-eligible data role** may produce a
        frozen-comparator number.

        Mode alone was not a control (S12R-11): mode is supplied by the caller, so a fully
        populated `data_role="development"` session passed as `Mode.SCORING` reported
        `is_scorable=True`. `parse_session` now rejects that combination outright, but this
        property is the second lock — `SessionManifest` is directly constructible, and
        §3.1's "never scored" is too load-bearing to rest on one check.
        """
        return self.mode is Mode.SCORING and self.data_role in _SCORING_ALLOWED_ROLES


# ── Settle criterion (S12R-04) ────────────────────────────────────────────────


def derive_settle_result(fields: dict, session_id: str) -> tuple[bool, tuple[str, ...]]:
    """Derive the SETTLE CRITERION pass/fail from the measured primitives.

    `notes/protocol.md` states both limbs numerically, so M4 recomputes them rather than
    accepting a declared verdict — a `settle_criterion_met` boolean would let the operator
    supply both the fact and the disposition it justifies, which is exactly the defect
    S12R-03 removed from `checksum_ok`.

    Returns `(met, failure_reasons)`. Both thresholds are **inclusive** (`<=` in the source),
    so the comparisons here are strict `>` and the boundary values pass.
    """
    spread = _finite_number(fields, "settle_pr_spread_bpm", session_id, minimum=0.0)
    drift = _finite_number(fields, "settle_pr_drift_bpm", session_id, minimum=0.0)

    reasons: list[str] = []
    if spread > SETTLE_MAX_PR_SPREAD_BPM:
        reasons.append(f"settle_pr_spread_exceeds_{SETTLE_MAX_PR_SPREAD_BPM:g}bpm")
    if drift > SETTLE_MAX_PR_DRIFT_BPM:
        reasons.append(f"settle_pr_drift_exceeds_{SETTLE_MAX_PR_DRIFT_BPM:g}bpm")
    return not reasons, tuple(reasons)


# ── Disposition recomputation (M4R-04) ────────────────────────────────────────


def recompute_disposition(
    fields: dict, session_id: str, *, raw_digest_ok: bool
) -> tuple[SessionDisposition, tuple[str, ...], tuple[str, ...]]:
    """Derive `(verdict, exclusion_reasons, flags)` from the primitive fields alone.

    **The predicates are transcribed from `notes/analysis_prespec.md` §6, which is FROZEN.**
    Plan §7 row 1 lists the *causes* to check; §6 defines what each one decides. Where they
    could be read differently, §6 wins. Three of these were wrong in the first draft and are
    called out below, because the wrong versions were all *more* aggressive — they would have
    silently discarded admissible sessions, which is an unlogged degree of freedom.

    Exclusion rules (session **not admitted**):

    * **§6 item 3 — protocol abort.** A run "deliberately halted before its intended 10-min
      end". The M3R-37 discriminator is one question: *did the run reach its intended
      duration?* If not, item 3, not admitted. A run that DID reach its intended end but
      whose stored file has an incomplete trailing window is item 4 and is **retained**.
    * **§6 item 4 — corrupt raw.** Not admitted iff a stored checksum fails. The truncation
      limb is **deliberately unimplemented** — see the S12R-01 note below.
    * **§6 item 5 — epoch-sync failure.** |offset| > 1 s at either end.
    * **§6 item 7 / plan §4** — a session superseded by its one permitted re-run: its windows
      "do not enter the coverage denominator".

    Flags (reported, **never** excluding):

    * **§6 item 4 — packet loss** above `n_dropped / n_received > 5 %`: "flags the session
      (reported) but does not by itself exclude it; the per-frame validity map decides which
      windows are radar-NaN".
    * **§6 item 4 — a trailing truncation fragment**, which §6 explicitly **retains**.

    **S12R-01 — the item-4 truncation limb is OPEN and escalated, not silently decided.**
    §6 item 4 excludes a session "truncated so that a **non-final** window is incomplete
    (`mirror_truncated_bytes` cuts into a mid-recording window)". But the only producer of
    that primitive sets it to `file_size % bytes_per_frame` and truncates those bytes from
    the end (`scripts/live_demo.py` `LiveFrameSource._loop`): it is a **sub-frame remainder**,
    strictly less than one frame, so it can never locate a mid-file cut and can never
    represent a lost window. The frozen text names a mechanism that cannot detect the
    condition the text defines.

    The first implementation bridged that gap by inferring a mid-file cut from
    `floor(n_frames/600) < floor(intended*20/600)` — which excluded exactly the case §6
    orders **retained** (a completed run whose stored file ends in a partial window). That
    predicate is removed. Nothing replaces it pending the user's resolution of the conflict
    at the M0 freeze, so a truncation is **flagged and retained** here. If the capture path
    later persists positional integrity evidence, the limb becomes implementable; until then
    an exclusion on this ground would rest on a primitive that cannot support it.

    **Not checked here, and NOT YET CHECKED ANYWHERE** (S12R-03, S12R-07 — open): that
    `raw_sha256` matches the bytes on disk, and that the validity map's length and
    invalid-count match its file. Both need file access and this function is pure, but plan
    §7 row 1 names checksum *and* "validity-map consistency" in the **Stage 1** done-when, so
    deferring them to Stage 3 was a done-when violation rather than a scoping choice. Until
    an I/O-backed verification exists, `checksum_ok` is an operator-supplied boolean and the
    integrity limb of this recomputation is **not** objective.
    """
    reasons: list[str] = []
    flags: list[str] = []

    # Strict primitives first: a malformed value is a ManifestError, never an exclusion
    # reason (S12R-10). Everything below can therefore assume exact types.
    offsets = {
        key: _finite_number(fields, key, session_id)
        for key in ("clock_offset_start_s", "clock_offset_end_s")
    }
    intended = _finite_number(fields, "intended_duration_s", session_id, minimum=0.0)
    actual = _finite_number(fields, "actual_duration_s", session_id, minimum=0.0)
    early_stop = _exact_bool(fields, "early_stop", session_id)
    truncation = _exact_int(fields, "truncation_bytes", session_id, minimum=0)
    received = _exact_int(fields, "packets_received", session_id, minimum=0)
    dropped = _exact_int(fields, "packets_dropped", session_id, minimum=0)
    n_frames = _exact_int(fields, "n_frames", session_id, minimum=0)
    n_invalid = _exact_int(fields, "n_invalid_frames", session_id, minimum=0)

    # Internally impossible combinations are malformed manifests, not §6 dispositions.
    # `n_invalid_frames` counts a subset of `n_frames`, so exceeding it really is impossible.
    if n_invalid > n_frames:
        raise ManifestError(
            f"session {session_id!r}: n_invalid_frames={n_invalid} exceeds n_frames="
            f"{n_frames}. Impossible, so the manifest is wrong; this is not a capture "
            "disposition and must not be counted as one."
        )

    # S12R-13: `packets_dropped > packets_received` is NOT impossible and must never raise.
    # The counters are independent, not a partition: `LiveFrameSource._loop` increments
    # `n_received` by **one per arriving packet** while `n_dropped` grows by the **size of
    # each sequence gap** (`seq - last_seq - 1`, plus any leading gap). Severe loss therefore
    # legitimately yields e.g. 10 received / 90 dropped, and §6 item 4 freezes
    # `n_dropped / n_received > 5 %` as flag-only with **no upper bound**. Rejecting a ratio
    # above 1.0 would make exactly the worst-loss sessions unloadable — silently removing the
    # hardest sessions and inflating coverage, which is the failure mode this stage's
    # invariants call out by name. Each counter is still individually non-negative (above).

    # ── §6 item 5: epoch-sync failure ────────────────────────────────────────
    for key, offset in offsets.items():
        if abs(offset) > MAX_CLOCK_OFFSET_S:
            reasons.append(f"{key}_exceeds_{MAX_CLOCK_OFFSET_S:g}s")

    # ── §6 item 3: protocol abort — did the run reach its intended duration? ─
    #
    # S12R-02: `early_stop=True` with `actual >= intended` is self-contradictory, and the
    # first draft made that contradiction an *invented* exclusion reason
    # (`early_stop_contradicts_durations`), which §6 does not name. Worse, it was invisible:
    # an operator who also wrote `admission: excluded` got agreement, so the contradictory
    # manifest parsed clean. It is a consistency failure and raises, independent of the
    # operator's verdict. The frozen M3R-37 discriminator is the duration question alone.
    reached_intended = actual >= intended
    if early_stop and reached_intended:
        raise ManifestError(
            f"session {session_id!r}: early_stop=True but actual_duration_s={actual!r} >= "
            f"intended_duration_s={intended!r}. A run cannot be both halted early and "
            "complete. §6 item 3's discriminator (M3R-37) is the duration question alone, "
            "so this contradiction has no frozen disposition — fix the manifest."
        )
    if not reached_intended:
        reasons.append("protocol_abort_did_not_reach_intended_duration")

    # §6 item 3's OTHER limb, absent until S12R-04: "the settle criterion is **not met**, or
    # the protocol run is deliberately halted…". Both are item-3 protocol aborts. A recorded
    # session whose settle evidence fails should not exist — the gate is pre-capture — so if
    # one does, it is an abort that was recorded anyway, and §6 makes it not admitted.
    settle_met, settle_reasons = derive_settle_result(fields, session_id)
    if not settle_met:
        reasons.extend(settle_reasons)

    # ── §6 item 4: corrupt raw — checksum limb only (truncation limb: see docstring) ──
    #
    # `raw_digest_ok` is DERIVED from the bytes on disk by `verify_bound_files`, never read
    # from the manifest (S12R-03 R2). It is a required keyword with no default: a caller that
    # has not verified the file cannot accidentally get an "admitted" verdict by omission.
    if type(raw_digest_ok) is not bool:
        raise ManifestError(
            f"session {session_id!r}: raw_digest_ok must be a derived bool, got "
            f"{raw_digest_ok!r}. It comes from hashing the bound raw file — if you are "
            "calling this directly, verification has not run."
        )
    if not raw_digest_ok:
        reasons.append("stored_checksum_failed")
    if truncation > 0:
        flags.append("raw_truncated_trailing")

    # ── §6 item 4: packet loss FLAGS, never excludes ─────────────────────────
    if received > 0 and dropped / received > PACKET_LOSS_FLAG_RATIO:
        flags.append(f"packet_loss_above_{PACKET_LOSS_FLAG_RATIO:.0%}")

    # ── §6 item 7 / plan §4: superseded attempt ──────────────────────────────
    if fields.get("retry_status") == RetryStatus.SUPERSEDED.value:
        reasons.append("superseded_by_retry")

    verdict = SessionDisposition.EXCLUDED if reasons else SessionDisposition.ADMITTED
    return verdict, tuple(reasons), tuple(flags)


# ── Parsing / validation ──────────────────────────────────────────────────────


def _require(fields: dict, key: str, session_id: str, group: str) -> Any:
    if key not in fields or fields[key] is None:
        raise ManifestError(
            f"session {session_id!r}: required {group} field {key!r} is missing. "
            "Scoring mode cannot form the frozen estimand sets without it "
            "(plans/m4_offline_harness.md §4)."
        )
    return fields[key]


def _as_enum(value, enum_cls, key: str, session_id: str):
    try:
        return enum_cls(value)
    except ValueError:
        allowed = ", ".join(repr(m.value) for m in enum_cls)
        raise ManifestError(
            f"session {session_id!r}: {key}={value!r} is not one of [{allowed}]."
        ) from None


def _validate_distance(value, session_id: str) -> float:
    """§4.1: finite, metres, 0.8 <= d <= 1.4 **inclusive**.

    S12R-10 R2: this used `float(value)`, which accepted `True` as **1.0 m** — inside the
    protocol range — and `"1.0"` as a number. §4.1 names the canonical form as a numeric
    float in metres, so the type is checked exactly before the bounds are applied.
    """
    if type(value) not in (int, float):
        raise ManifestError(
            f"session {session_id!r}: distance_m={value!r} is not a number "
            f"(got {type(value).__name__}). It is metres, as a JSON number — the legacy "
            "run_metadata field is distance_cm, and conversion is explicit, never implicit."
        )
    d = float(value)
    if not math.isfinite(d):
        raise ManifestError(f"session {session_id!r}: distance_m={d!r} is not finite.")
    if not (DISTANCE_MIN_M <= d <= DISTANCE_MAX_M):
        raise ManifestError(
            f"session {session_id!r}: distance_m={d!r} m is outside the protocol range "
            f"[{DISTANCE_MIN_M}, {DISTANCE_MAX_M}] m, inclusive (notes/protocol.md)."
        )
    return d


def _validate_posture(value, session_id: str) -> str:
    if value != CANONICAL_POSTURE:
        raise ManifestError(
            f"session {session_id!r}: posture={value!r} but the estimand fixes "
            f"{CANONICAL_POSTURE!r}. A differing session is not a member of this design."
        )
    return value


def recompute_pre_capture_disposition(
    fields: dict, session_id: str
) -> tuple[SessionDisposition, tuple[str, ...]]:
    """Derive a pre-capture attempt's disposition from its gate evidence alone (S12R-12).

    A pre-capture attempt exists precisely *because* a mandatory pre-recording gate failed:
    `notes/protocol.md` step 3 (settle) or step 3a (clock sync, "resync and restart before
    recording"). Both map to a §6 session-level not-admitted disposition — item 3 and item 5
    respectively.

    The capture-limb rules (duration, checksum, packet loss, truncation, retry) cannot run
    here: their fields are forbidden on this record kind because the artifacts do not exist.

    **An attempt where both gates pass raises**, rather than being admitted. If both had
    passed, recording would have started and this would be a captured session — so such a
    record is self-contradictory, and inventing a disposition for it would put a cause that
    never happened into the study's reason counts.
    """
    reasons: list[str] = []

    settle_met, settle_reasons = derive_settle_result(fields, session_id)
    if not settle_met:
        reasons.extend(settle_reasons)

    offset = _finite_number(fields, "clock_offset_start_s", session_id)
    if abs(offset) > MAX_CLOCK_OFFSET_S:
        reasons.append(f"clock_offset_start_s_exceeds_{MAX_CLOCK_OFFSET_S:g}s")

    if not reasons:
        raise ManifestError(
            f"session {session_id!r}: record_kind is 'pre_capture_attempt' but both "
            "pre-recording gates pass — settle criterion met and the clock offset within "
            f"+/-{MAX_CLOCK_OFFSET_S:g} s. Recording would have started, so this is a "
            "captured session, not an attempt. §6 has no disposition for an attempt that "
            "did not fail."
        )
    return SessionDisposition.EXCLUDED, tuple(reasons)


def _validate_rate_schedule(
    fields: dict, arm: Arm | None, session_id: str, mode: Mode
) -> tuple[dict, ...]:
    """Validate `commanded_rate_schedule` (plan §4, Provenance row; S12R-09).

    **The entry shape below is DEFINED HERE, not transcribed.** Plan §4 requires the manifest
    to bind a "commanded-rate schedule" but no binding document states its fields, so this is
    the one rule in Stage 1 that does not trace to a frozen source. It is written to be the
    weakest shape that still makes the field checkable, and it is called out rather than
    buried so the review can reject or replace it — inventing predicates quietly is what went
    wrong in the first draft.

    **A SCORING session must carry exactly one entry, at `start_s = 0`, matching the scalar
    `commanded_rate_bpm`** (S12R-09 R2). The first version allowed a stepped schedule here on
    the grounds that `notes/protocol.md`'s sweep runs 12 → 15 → 18 → **21** — but that
    document calls the stepped capture "a *method development* capture, **not a study
    session**", and §3.2 makes the paced commanded rate **between-subject**. Admitting a
    stepped schedule into scoring mode therefore imported a development protocol into the
    frozen study estimand. A multi-entry schedule now loads only in DEVELOPMENT mode, where
    the sweep belongs and where its rates need not be members of the M3R-31 rotation.
    """
    raw = fields.get("commanded_rate_schedule")

    if arm is not Arm.PACED:
        if raw:
            raise ManifestError(
                f"session {session_id!r}: arm is {arm.value if arm else None!r} but "
                f"commanded_rate_schedule={raw!r} is set. Only the paced arm has one."
            )
        return ()

    if raw is None:
        if mode is not Mode.SCORING:
            return ()
        raise ManifestError(
            f"session {session_id!r}: arm is 'paced' but commanded_rate_schedule is missing. "
            "Plan §4 binds it under Provenance; without it the commanded rate that was "
            "actually played cannot be reconstructed from the manifest."
        )
    if type(raw) is not list or not raw:
        raise ManifestError(
            f"session {session_id!r}: commanded_rate_schedule={raw!r} must be a non-empty "
            "array of entries."
        )

    last_start: float | None = None
    for i, entry in enumerate(raw):
        if type(entry) is not dict:
            raise ManifestError(
                f"session {session_id!r}: commanded_rate_schedule[{i}]={entry!r} must be an "
                "object."
            )
        where = f"{session_id}.commanded_rate_schedule[{i}]"
        _exact_int(entry, "commanded_rate_bpm", where, minimum=1)
        start = _finite_number(entry, "start_s", where, minimum=0.0)
        if last_start is not None and start <= last_start:
            raise ManifestError(
                f"session {session_id!r}: commanded_rate_schedule[{i}].start_s={start!r} "
                f"does not increase on the previous entry ({last_start!r}). The schedule is "
                "an ordered timeline."
            )
        last_start = start

    if float(raw[0]["start_s"]) != 0.0:
        raise ManifestError(
            f"session {session_id!r}: commanded_rate_schedule[0].start_s must be 0 — the "
            "schedule is relative to the start of the recording."
        )

    # S12R-09 R2: a study paced session holds ONE commanded rate for the whole recording.
    # `notes/protocol.md` classes the stepped 12->15->18->21 sweep as method development,
    # "not a study session", and §3.2 makes the paced rate between-subject.
    if mode is Mode.SCORING and len(raw) != 1:
        raise ManifestError(
            f"session {session_id!r}: a SCORING paced session must carry exactly one "
            f"commanded_rate_schedule entry, got {len(raw)}. A stepped schedule is the "
            "diagnostic sweep, which notes/protocol.md calls a method-development capture "
            "and not a study session — load it in DEVELOPMENT mode."
        )

    # A single-rate paced session must agree with its own scalar field; a stepped schedule
    # (development only, per the check above) has no single scalar to agree with.
    rate = fields.get("commanded_rate_bpm")
    if len(raw) == 1 and rate is not None and raw[0]["commanded_rate_bpm"] != rate:
        raise ManifestError(
            f"session {session_id!r}: commanded_rate_bpm={rate!r} but the single-entry "
            f"schedule declares {raw[0]['commanded_rate_bpm']!r}."
        )
    return tuple(raw)


def parse_session(
    fields: dict, mode: Mode, *, raw_digest_ok: bool | None = None
) -> SessionManifest:
    """Validate one session's fields and return the manifest record.

    In SCORING mode every §4 required field must be present and every §4.1 rule must hold,
    and the recomputed disposition must agree with the operator's. In DEVELOPMENT mode the
    fields that the 4 existing captures genuinely lack may be absent — but any field that
    *is* present is still validated, so development mode is a smaller contract, not a
    laxer one.
    """
    if not isinstance(fields, dict):
        raise ManifestError(f"each session entry must be a dict, got {type(fields).__name__}")

    session_id = fields.get("session_id")
    if not session_id or not isinstance(session_id, str):
        raise ManifestError(
            f"every session needs a non-empty string session_id; got {session_id!r}"
        )

    # ── S12R-12: which contract applies? ─────────────────────────────────────
    #
    # Absent, this is a captured session: every existing capture is one, and the pre-capture
    # attempt kind is new with this schema version. Scoring mode requires it explicitly (it
    # is in the required list) so a study manifest never relies on that default.
    kind = (_as_enum(fields["record_kind"], RecordKind, "record_kind", session_id)
            if ("record_kind" in fields and fields["record_kind"] is not None)
            else RecordKind.CAPTURED_SESSION)

    if kind is RecordKind.PRE_CAPTURE_ATTEMPT:
        for key, group in _REQUIRED_PRE_CAPTURE_FIELDS:
            _require(fields, key, session_id, group)
        present_but_forbidden = [k for k in _FORBIDDEN_ON_PRE_CAPTURE if fields.get(k) is not None]
        if present_but_forbidden:
            raise ManifestError(
                f"session {session_id!r}: record_kind is 'pre_capture_attempt', but these "
                f"capture-only fields are present: {', '.join(sorted(present_but_forbidden))}. "
                "The gate that produced this record fires before recording starts, so these "
                "artifacts cannot exist. Either the capture did happen — in which case this "
                "is a captured_session — or the values were invented (CLAUDE.md §4)."
            )
    elif mode is Mode.SCORING:
        for key, group in _REQUIRED_SCORING_FIELDS:
            _require(fields, key, session_id, group)

    def present(key):
        return key in fields and fields[key] is not None

    arm = _as_enum(fields["arm"], Arm, "arm", session_id) if present("arm") else None
    role = (_as_enum(fields["data_role"], DataRole, "data_role", session_id)
            if present("data_role") else None)
    retry = (_as_enum(fields["retry_status"], RetryStatus, "retry_status", session_id)
             if present("retry_status") else None)

    # The commanded rate is required by, and only meaningful for, the paced arm.
    rate = fields.get("commanded_rate_bpm")
    if arm is Arm.PACED and kind is RecordKind.PRE_CAPTURE_ATTEMPT:
        # An attempt that never recorded has no schedule to bind; the rate may be declared
        # (the arm was planned) but is not required, because nothing was played.
        if rate is not None:
            rate = _exact_int(fields, "commanded_rate_bpm", session_id, minimum=1)
    elif arm is Arm.PACED:
        if rate is None:
            raise ManifestError(
                f"session {session_id!r}: arm is 'paced' but commanded_rate_bpm is missing; "
                f"it must be one of {PACED_RATES_BPM}."
            )
        # Exact int (S12R-10): `int(12.9)` silently became 12, turning a producer bug into a
        # valid frozen rate and mis-filing the session in the M3R-31 rate allocation.
        rate = _exact_int(fields, "commanded_rate_bpm", session_id, minimum=1)
        # S12R-14: the (12, 15, 18) rotation is the **study** allocation (§1, M3R-31) and
        # binds SCORING only. Enforcing it in development mode made the existing paced-16
        # capture (`massimo2`, `notes/capture_inventory.md`) unloadable in the very mode
        # plan §2.2/§4.1 created for the four existing captures — so M4 could not have run
        # on one of the three reference-bearing captures at all.
        if mode is Mode.SCORING and rate not in PACED_RATES_BPM:
            raise ManifestError(
                f"session {session_id!r}: commanded_rate_bpm={rate!r} is not one of "
                f"{PACED_RATES_BPM} (the frozen M3R-31 rotation). Historical development "
                "rates such as the existing 16 bpm capture load in DEVELOPMENT mode, which "
                "is exploratory / apparent / in-sample and never a study estimand."
            )
    elif arm is Arm.NATURAL and rate is not None:
        raise ManifestError(
            f"session {session_id!r}: arm is 'natural' but commanded_rate_bpm={rate!r} is "
            "set. A natural session has no commanded rate."
        )

    schedule = (
        () if kind is RecordKind.PRE_CAPTURE_ATTEMPT
        else _validate_rate_schedule(fields, arm, session_id, mode)
    )

    distance = _validate_distance(fields["distance_m"], session_id) if present("distance_m") else None
    posture = _validate_posture(fields["posture"], session_id) if present("posture") else None

    # S12R-10 R2: `float(frame0)` accepted the STRING "1785000000.25" as a valid origin,
    # hiding a producer defect in the one field every window boundary is measured from.
    # §7 binds it to a synchronised UTC *measurement*, so it must arrive as a JSON number.
    frame0 = None
    if present("frame0_epoch"):
        frame0 = _finite_number(fields, "frame0_epoch", session_id)

    # ── §3.1 role/mode matrix (S12R-11) ──────────────────────────────────────
    #
    # Mode is supplied out-of-band so a manifest cannot promote itself; the cost is that
    # nothing stopped a caller from handing a `development` or `engineering` session to
    # SCORING. §3.1 makes engineering "never scored, never evaluation", development "never
    # confirmatory/headline" and the pilot "excluded from confirmatory metrics", so the
    # binding is between the *role* and the mode, not the mode alone.
    if mode is Mode.SCORING and role is not None and role not in _SCORING_ALLOWED_ROLES:
        allowed = ", ".join(sorted(r.value for r in _SCORING_ALLOWED_ROLES))
        raise ManifestError(
            f"session {session_id!r}: data_role={role.value!r} may never produce a "
            f"frozen-comparator number (notes/analysis_prespec.md §3.1); scoring mode "
            f"accepts only [{allowed}]. Load it in DEVELOPMENT mode, where its output is "
            "labelled exploratory / apparent / in-sample."
        )

    # ── Provenance strings, strictly typed — per record kind (S12R-12) ───────
    if kind is RecordKind.PRE_CAPTURE_ATTEMPT:
        # The capture bindings are forbidden here; the settle evidence is what this record
        # exists to carry, so it is bound by path + SHA-256 in **both** modes.
        _non_empty_str(fields, "subject_id", session_id)
        _non_empty_str(fields, "settle_evidence_path", session_id)
        _sha256(fields, "settle_evidence_sha256", session_id)
        _finite_number(fields, "attempt_utc", session_id)
    elif mode is Mode.SCORING:
        for key in ("raw_path", "frame_validity_map_path", "capture_config_path",
                    "masimo_path", "capture_git_commit", "subject_id",
                    "settle_evidence_path"):
            _non_empty_str(fields, key, session_id)
        for key in ("raw_sha256", "frame_validity_map_sha256", "capture_config_sha256",
                    "masimo_sha256", "settle_evidence_sha256"):
            _sha256(fields, key, session_id)

    # Plan §4 Disposition binds retry/replacement status **and reason** (S12R-09). A reason
    # is only meaningful once the status is not `original`, so it is conditionally required
    # rather than always required.
    if retry is not None and retry is not RetryStatus.ORIGINAL:
        _non_empty_str(fields, "retry_reason", session_id)
    elif retry is RetryStatus.ORIGINAL and fields.get("retry_reason") is not None:
        raise ManifestError(
            f"session {session_id!r}: retry_status is 'original' but retry_reason="
            f"{fields['retry_reason']!r} is set. An original attempt replaced nothing."
        )

    disposition = (_as_enum(fields["disposition"], SessionDisposition, "disposition", session_id)
                   if present("disposition") else None)
    reasons: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    if kind is RecordKind.PRE_CAPTURE_ATTEMPT:
        # Always recomputed, in both modes: this record kind's entire required field set is
        # the gate evidence, so there is never a mode in which it cannot be derived.
        recomputed, reasons = recompute_pre_capture_disposition(fields, session_id)
        if recomputed is not disposition:
            raise ManifestError(
                f"session {session_id!r}: operator recorded disposition="
                f"{disposition.value if disposition else None!r} but M4 recomputes "
                f"{recomputed.value!r} from the gate evidence (reasons: "
                f"{', '.join(reasons)}). A logged pre-capture attempt is not admitted."
            )
    elif mode is Mode.SCORING:
        if raw_digest_ok is None:
            raise ManifestError(
                f"session {session_id!r}: scoring-mode parsing needs the DERIVED raw-digest "
                "result, which comes from hashing the bound raw file. Use `load_manifest`, "
                "which verifies every binding before returning a session — verification is "
                "not an optional caller convention (S12R-07 R2)."
            )
        recomputed, reasons, flags = recompute_disposition(
            fields, session_id, raw_digest_ok=raw_digest_ok
        )
        if recomputed is not disposition:
            raise ManifestError(
                f"session {session_id!r}: operator recorded disposition="
                f"{disposition.value!r} but "
                f"M4 recomputes {recomputed.value!r} from the primitive fields"
                + (f" (reasons: {', '.join(reasons)})" if reasons else "")
                + ". M4 neither overrides the operator nor defers to them — the disagreement "
                "itself is the defect and must be resolved in the manifest (M4R-04)."
            )

    return SessionManifest(
        mode=mode,
        session_id=session_id,
        record_kind=kind,
        attempt_utc=fields.get("attempt_utc"),
        settle_pr_spread_bpm=fields.get("settle_pr_spread_bpm"),
        settle_pr_drift_bpm=fields.get("settle_pr_drift_bpm"),
        settle_evidence_path=fields.get("settle_evidence_path"),
        settle_evidence_sha256=fields.get("settle_evidence_sha256"),
        subject_id=fields.get("subject_id"),
        arm=arm,
        commanded_rate_bpm=rate,
        data_role=role,
        disposition=disposition,
        disposition_reasons=reasons,
        flags=flags,
        distance_m=distance,
        posture=posture,
        frame0_epoch=frame0,
        clock_offset_start_s=fields.get("clock_offset_start_s"),
        clock_offset_end_s=fields.get("clock_offset_end_s"),
        raw_path=fields.get("raw_path"),
        raw_sha256=fields.get("raw_sha256"),
        truncation_bytes=fields.get("truncation_bytes"),
        raw_digest_ok=raw_digest_ok,
        packets_received=fields.get("packets_received"),
        packets_dropped=fields.get("packets_dropped"),
        n_frames=fields.get("n_frames"),
        n_invalid_frames=fields.get("n_invalid_frames"),
        frame_validity_map_path=fields.get("frame_validity_map_path"),
        frame_validity_map_sha256=fields.get("frame_validity_map_sha256"),
        capture_config_path=fields.get("capture_config_path"),
        capture_config_sha256=fields.get("capture_config_sha256"),
        capture_git_commit=fields.get("capture_git_commit"),
        masimo_path=fields.get("masimo_path"),
        masimo_sha256=fields.get("masimo_sha256"),
        commanded_rate_schedule=schedule,
        intended_duration_s=fields.get("intended_duration_s"),
        actual_duration_s=fields.get("actual_duration_s"),
        early_stop=fields.get("early_stop"),
        retry_status=retry,
        retry_reason=fields.get("retry_reason"),
        raw=fields,
    )


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_bound_files(fields: dict, session_id: str, root: Path) -> bool:
    """Hash every bound artifact and return the DERIVED raw-digest result (S12R-03, S12R-07).

    **The disposition split is the load-bearing part**, confirmed by Codex in S12R-07 R3 and
    implemented exactly as stated there:

    * **raw file present and readable, digest != `raw_sha256`** → this is the §6 item-4
      "stored file checksum fails" *capture disposition*. It is **returned**, not raised, so
      the caller feeds it into `recompute_disposition` and the session becomes EXCLUDED with
      a §6 reason that belongs in the study's counts.
    * **config / validity-map / reference digest mismatch** → a **provenance failure**.
      `ManifestError`, never a §6 reason: no binding authority assigns these a session
      disposition, so counting one as an exclusion would put a cause that never happened
      into a published table (S12R-10's principle, applied to the I/O layer).
    * **a bound file missing at scoring time** → also a provenance failure. A reference that
      was previously bound by path + digest and is gone has been **LOST, not never-acquired**;
      turning that into §6 item 6 `NO_AGREEMENT` would let the filesystem supply both the
      fact and the verdict. `NO_AGREEMENT` derives only from bound acquisition evidence.

    Paths resolve against one documented `root` so a manifest cannot reach outside the study
    tree or depend on the process working directory. **Hash before reading** for content: the
    validity map is only interpreted once its digest matches.
    """
    def resolve(key: str) -> Path:
        rel = fields.get(key)
        if type(rel) is not str or not rel.strip():
            raise ManifestError(
                f"session {session_id!r}: binding {key!r}={rel!r} must be a non-empty "
                "string before it can be verified."
            )
        p = (root / rel).resolve()
        if root.resolve() not in p.parents and p != root.resolve():
            raise ManifestError(
                f"session {session_id!r}: {key}={rel!r} resolves outside the manifest root "
                f"{root}. Every bound artifact lives under one documented root."
            )
        return p

    def require_digest(key_path: str, key_hash: str, what: str) -> bool:
        _sha256(fields, key_hash, session_id)
        p = resolve(key_path)
        if not p.is_file():
            raise ManifestError(
                f"session {session_id!r}: {what} bound at {key_path}={fields[key_path]!r} "
                f"does not exist under {root}. It was bound by path + SHA-256, so it was "
                "acquired and is now LOST — that is a provenance failure, not a capture "
                "disposition, and never a no-agreement session (S12R-07 R3)."
            )
        return _sha256_file(p) == fields[key_hash]

    # Raw: a mismatch is a §6 item-4 DISPOSITION, returned to the caller.
    raw_digest_ok = require_digest("raw_path", "raw_sha256", "the raw capture")

    # Everything else: a mismatch is a PROVENANCE FAILURE.
    for key_path, key_hash, what in (
        ("capture_config_path", "capture_config_sha256", "the capture config"),
        ("masimo_path", "masimo_sha256", "the Masimo reference"),
        ("settle_evidence_path", "settle_evidence_sha256", "the settle evidence"),
        ("frame_validity_map_path", "frame_validity_map_sha256", "the frame validity map"),
    ):
        if not require_digest(key_path, key_hash, what):
            raise ManifestError(
                f"session {session_id!r}: {what} at {fields[key_path]!r} does not match its "
                f"bound {key_hash}. No binding authority gives this a §6 session "
                "disposition, so it is a provenance failure and not an exclusion reason — "
                "counting it as one would report a cause that never happened."
            )

    _verify_validity_map(fields, session_id, resolve("frame_validity_map_path"))
    return raw_digest_ok


def _verify_validity_map(fields: dict, session_id: str, path: Path) -> None:
    """Plan §7's per-frame validity map: exactly one entry per frame, counts agreeing.

    Only reached once the file's digest matches (hash before read). **Convention, defined
    here and not transcribed:** the map is a 1-D boolean array in which `True` marks a
    **valid** frame, so `n_invalid_frames` is the count of `False`. §7 requires "a per-frame
    validity / zero-fill map" without fixing its dtype or polarity — flagged for review
    rather than buried.
    """
    try:
        arr = np.load(path, allow_pickle=False)
    except Exception as exc:                                  # noqa: BLE001 - reported as-is
        raise ManifestError(
            f"session {session_id!r}: the frame validity map at {path} could not be read as "
            f"a NumPy array ({type(exc).__name__}: {exc})."
        ) from None

    if arr.dtype != np.bool_ or arr.ndim != 1:
        raise ManifestError(
            f"session {session_id!r}: the frame validity map must be a 1-D boolean array, "
            f"got dtype={arr.dtype} ndim={arr.ndim}."
        )
    n_frames = fields["n_frames"]
    if arr.size != n_frames:
        raise ManifestError(
            f"session {session_id!r}: the frame validity map has {arr.size} entries but "
            f"n_frames={n_frames}. Plan §7 requires exactly one entry per frame — without "
            "that, the map cannot say WHICH windows are affected, which is the whole reason "
            "it is bound rather than the aggregate n_dropped."
        )
    n_invalid = int((~arr).sum())
    if n_invalid != fields["n_invalid_frames"]:
        raise ManifestError(
            f"session {session_id!r}: the frame validity map marks {n_invalid} invalid "
            f"frames but n_invalid_frames={fields['n_invalid_frames']}."
        )


def load_manifest(
    path: str | Path, mode: Mode, *, root: str | Path | None = None
) -> list[SessionManifest]:
    """Load and validate a manifest JSON file: `{"sessions": [ … ]}`.

    `mode` is supplied by the caller, not read from the file, so a manifest cannot promote
    itself into scoring mode.

    The **schema version is a property of the document**, not of a session (plan §4: "a
    versioned manifest binds … for each session"), so it is enforced here. `parse_session`
    validates one session's fields and is deliberately reachable without it.
    """
    p = Path(path)
    doc = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or "sessions" not in doc:
        raise ManifestError(
            f"{p}: manifest must be a JSON object with a 'sessions' array."
        )

    # S12R-09: plan §4 calls for a versioned manifest and there was neither a field nor a
    # check. Without one, a schema migration silently reinterprets every existing session.
    version = doc.get("manifest_schema_version")
    if version is None:
        raise ManifestError(
            f"{p}: manifest_schema_version is missing. Plan §4 requires a versioned "
            f"manifest; this reader implements version {MANIFEST_SCHEMA_VERSION}."
        )
    if type(version) is not int or version != MANIFEST_SCHEMA_VERSION:
        raise ManifestError(
            f"{p}: manifest_schema_version={version!r}, but this reader implements "
            f"version {MANIFEST_SCHEMA_VERSION}. Refusing to guess how to read it."
        )

    sessions = doc["sessions"]
    if not isinstance(sessions, list):
        raise ManifestError(f"{p}: 'sessions' must be an array, got {type(sessions).__name__}")

    # Verification is part of the load path, not a helper a future caller might forget
    # (S12R-07 R2). Paths resolve against `root`, which defaults to the manifest's own
    # directory so the document and the artifacts it binds travel together.
    base = Path(root).resolve() if root is not None else p.parent.resolve()

    parsed = []
    for s in sessions:
        digest_ok = None
        if mode is Mode.SCORING and isinstance(s, dict) and s.get("record_kind") != (
            RecordKind.PRE_CAPTURE_ATTEMPT.value
        ):
            # A pre-capture attempt binds no capture artifacts; its settle evidence is
            # verified inside `parse_session`'s own contract.
            digest_ok = verify_bound_files(s, s.get("session_id", "<unnamed>"), base)
        parsed.append(parse_session(s, mode, raw_digest_ok=digest_ok))

    seen: set[str] = set()
    for s in parsed:
        if s.session_id in seen:
            raise ManifestError(f"{p}: duplicate session_id {s.session_id!r}")
        seen.add(s.session_id)
    return parsed


def require_scoring_mode(sessions: list[SessionManifest], what: str) -> None:
    """Refuse to let a non-scorable session reach a scoring path (§4).

    The separation has to be enforced somewhere executable; a naming convention is not a
    control. Call this at the entry of anything that emits a frozen-comparator number.

    "Non-scorable" is mode **and** data role (S12R-11): a development-role session is barred
    even if someone hands it in as `Mode.SCORING`.
    """
    offenders = [
        f"{s.session_id!r} (mode={s.mode.value}, "
        f"data_role={s.data_role.value if s.data_role else None})"
        for s in sessions
        if not s.is_scorable
    ]
    if offenders:
        raise ManifestError(
            f"{what} requires a SCORING-mode session in a scoring-eligible data role, but "
            f"these are not: {', '.join(offenders)}. Development, engineering and pilot "
            "output is exploratory / apparent / in-sample and can never be a "
            "frozen-comparator number (notes/analysis_prespec.md §3.1; "
            "plans/m4_offline_harness.md §2.2, §4)."
        )
