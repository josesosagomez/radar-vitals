"""M4 Stage 1 — the session manifest: schema, validation, admission recomputation.

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

**M4 recomputes the admission disposition from the primitive fields and refuses to proceed
if it disagrees with the operator's verdict** (M4R-04). An operator verdict alone is an
opinion; recomputation makes it checkable. Disagreement is an error, never a silent
override in either direction — M4 does not "know better" and does not defer.

Development mode exists for the 4 existing captures (plan §2.2) and is built so its output
cannot be mistaken for scoring output: it carries `mode: DEVELOPMENT`, and
`require_scoring_mode` refuses to let a development manifest reach a scoring path.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

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


class Admission(str, Enum):
    ADMITTED = "admitted"
    EXCLUDED = "excluded"


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
#: WINDOWS are radar-NaN. Exceeding this is not an admission failure.
PACKET_LOSS_FLAG_RATIO = 0.05

#: Commanded paced rates, frozen by the M3R-31 rotation (12 -> 15 -> 18).
PACED_RATES_BPM = (12, 15, 18)

#: Plan §4 calls for a **versioned** manifest (S12R-09). The version is a property of the
#: manifest document, not of a session, so `load_manifest` enforces it. Bump only with a
#: migration: this is the root provenance record for every session.
MANIFEST_SCHEMA_VERSION = 1

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
    ("session_id", "Identity"),
    ("subject_id", "Identity"),
    ("arm", "Identity"),
    ("data_role", "Identity"),
    ("admission", "Identity"),
    ("distance_m", "Design"),
    ("posture", "Design"),
    ("frame0_epoch", "Timebase"),
    ("clock_offset_start_s", "Timebase"),
    ("clock_offset_end_s", "Timebase"),
    ("raw_path", "Integrity"),
    ("raw_sha256", "Integrity"),
    ("truncation_bytes", "Integrity"),
    ("checksum_ok", "Integrity"),
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
)


@dataclass(frozen=True)
class SessionManifest:
    """One validated session. Construct via `load_manifest` / `parse_session`."""

    mode: Mode
    session_id: str

    # Identity / estimands
    subject_id: str | None = None
    arm: Arm | None = None
    commanded_rate_bpm: int | None = None
    data_role: DataRole | None = None
    admission: Admission | None = None
    admission_reasons: tuple[str, ...] = ()
    #: Reported, never excluding (§6 item 4) — e.g. packet loss above the 5 % tolerance.
    flags: tuple[str, ...] = ()

    # Design / descriptive
    distance_m: float | None = None
    posture: str | None = None

    # Timebase
    frame0_epoch: float | None = None
    clock_offset_start_s: float | None = None
    clock_offset_end_s: float | None = None

    # Integrity
    raw_path: str | None = None
    raw_sha256: str | None = None
    truncation_bytes: int | None = None
    checksum_ok: bool | None = None
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


# ── Admission recomputation (M4R-04) ──────────────────────────────────────────


def recompute_admission(
    fields: dict, session_id: str
) -> tuple[Admission, tuple[str, ...], tuple[str, ...]]:
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
    checksum_ok = _exact_bool(fields, "checksum_ok", session_id)
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

    # ── §6 item 4: corrupt raw — checksum limb only (truncation limb: see docstring) ──
    if not checksum_ok:
        reasons.append("stored_checksum_failed")
    if truncation > 0:
        flags.append("raw_truncated_trailing")

    # ── §6 item 4: packet loss FLAGS, never excludes ─────────────────────────
    if received > 0 and dropped / received > PACKET_LOSS_FLAG_RATIO:
        flags.append(f"packet_loss_above_{PACKET_LOSS_FLAG_RATIO:.0%}")

    # ── §6 item 7 / plan §4: superseded attempt ──────────────────────────────
    if fields.get("retry_status") == RetryStatus.SUPERSEDED.value:
        reasons.append("superseded_by_retry")

    verdict = Admission.EXCLUDED if reasons else Admission.ADMITTED
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


def parse_session(fields: dict, mode: Mode) -> SessionManifest:
    """Validate one session's fields and return the manifest record.

    In SCORING mode every §4 required field must be present and every §4.1 rule must hold,
    and the recomputed admission must agree with the operator's. In DEVELOPMENT mode the
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

    if mode is Mode.SCORING:
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
    if arm is Arm.PACED:
        if rate is None:
            raise ManifestError(
                f"session {session_id!r}: arm is 'paced' but commanded_rate_bpm is missing; "
                f"it must be one of {PACED_RATES_BPM}."
            )
        # Exact int (S12R-10): `int(12.9)` silently became 12, turning a producer bug into a
        # valid frozen rate and mis-filing the session in the M3R-31 rate allocation.
        rate = _exact_int(fields, "commanded_rate_bpm", session_id)
        if rate not in PACED_RATES_BPM:
            raise ManifestError(
                f"session {session_id!r}: commanded_rate_bpm={rate!r} is not one of "
                f"{PACED_RATES_BPM} (the frozen M3R-31 rotation)."
            )
    elif arm is Arm.NATURAL and rate is not None:
        raise ManifestError(
            f"session {session_id!r}: arm is 'natural' but commanded_rate_bpm={rate!r} is "
            "set. A natural session has no commanded rate."
        )

    schedule = _validate_rate_schedule(fields, arm, session_id, mode)

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

    # ── Provenance and disposition strings, strictly typed in scoring mode ───
    if mode is Mode.SCORING:
        for key in ("raw_path", "frame_validity_map_path", "capture_config_path",
                    "masimo_path", "capture_git_commit", "subject_id"):
            _non_empty_str(fields, key, session_id)
        for key in ("raw_sha256", "frame_validity_map_sha256", "capture_config_sha256",
                    "masimo_sha256"):
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

    admission = (_as_enum(fields["admission"], Admission, "admission", session_id)
                 if present("admission") else None)
    reasons: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    if mode is Mode.SCORING:
        recomputed, reasons, flags = recompute_admission(fields, session_id)
        if recomputed is not admission:
            raise ManifestError(
                f"session {session_id!r}: operator recorded admission={admission.value!r} but "
                f"M4 recomputes {recomputed.value!r} from the primitive fields"
                + (f" (reasons: {', '.join(reasons)})" if reasons else "")
                + ". M4 neither overrides the operator nor defers to them — the disagreement "
                "itself is the defect and must be resolved in the manifest (M4R-04)."
            )

    return SessionManifest(
        mode=mode,
        session_id=session_id,
        subject_id=fields.get("subject_id"),
        arm=arm,
        commanded_rate_bpm=rate,
        data_role=role,
        admission=admission,
        admission_reasons=reasons,
        flags=flags,
        distance_m=distance,
        posture=posture,
        frame0_epoch=frame0,
        clock_offset_start_s=fields.get("clock_offset_start_s"),
        clock_offset_end_s=fields.get("clock_offset_end_s"),
        raw_path=fields.get("raw_path"),
        raw_sha256=fields.get("raw_sha256"),
        truncation_bytes=fields.get("truncation_bytes"),
        checksum_ok=fields.get("checksum_ok"),
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


def load_manifest(path: str | Path, mode: Mode) -> list[SessionManifest]:
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

    parsed = [parse_session(s, mode) for s in sessions]

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
