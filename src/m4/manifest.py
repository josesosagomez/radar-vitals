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

#: `notes/analysis_prespec.md` §6: NTP-synced, max +/-1 s, re-checked at session end.
MAX_CLOCK_OFFSET_S = 1.0

#: Commanded paced rates, frozen by the M3R-31 rotation (12 -> 15 -> 18).
PACED_RATES_BPM = (12, 15, 18)


class ManifestError(ValueError):
    """A manifest that cannot be used as given. Always names the session and the field."""


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
    ("packet_loss_frames", "Integrity"),
    ("n_frames", "Integrity"),
    ("n_invalid_frames", "Integrity"),
    ("frame_validity_map_path", "Integrity"),
    ("frame_validity_map_sha256", "Integrity"),
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
    packet_loss_frames: int | None = None
    n_frames: int | None = None
    n_invalid_frames: int | None = None
    frame_validity_map_path: str | None = None
    frame_validity_map_sha256: str | None = None

    # Provenance
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
        """Only a SCORING-mode manifest may produce a frozen-comparator number."""
        return self.mode is Mode.SCORING


# ── Admission recomputation (M4R-04) ──────────────────────────────────────────


def recompute_admission(fields: dict, session_id: str) -> tuple[Admission, tuple[str, ...]]:
    """Derive the admission disposition from the primitive fields alone.

    One named rule per exclusion cause, so a disagreement points at *which* rule fired
    rather than at the verdict as a whole. The rules are the ones plan §7 row 1 enumerates:
    clock offset at both ends, checksum/truncation, intended-duration vs abort, packet loss,
    retry/replacement, validity-map consistency.

    Deliberately **not** checked here: that `raw_sha256` matches the bytes on disk, and that
    the validity map's length and invalid-count match its file. Both need the files, which
    Stage 3 opens; this function is pure and works from the manifest alone. What it *can*
    check from the manifest — the internal consistency of the declared counts — it does.
    """
    reasons: list[str] = []

    for key in ("clock_offset_start_s", "clock_offset_end_s"):
        offset = fields.get(key)
        if offset is None or not math.isfinite(float(offset)):
            reasons.append(f"{key}_missing_or_non_finite")
        elif abs(float(offset)) > MAX_CLOCK_OFFSET_S:
            reasons.append(f"{key}_exceeds_{MAX_CLOCK_OFFSET_S:g}s")

    truncation = fields.get("truncation_bytes")
    if truncation is None:
        reasons.append("truncation_bytes_missing")
    elif int(truncation) > 0:
        reasons.append("raw_truncated")

    packet_loss = fields.get("packet_loss_frames")
    if packet_loss is None:
        reasons.append("packet_loss_frames_missing")
    elif int(packet_loss) > 0:
        reasons.append("packet_loss_detected")

    if bool(fields.get("early_stop", False)):
        reasons.append("early_stop")
    intended, actual = fields.get("intended_duration_s"), fields.get("actual_duration_s")
    if intended is not None and actual is not None and float(actual) < float(intended):
        reasons.append("actual_duration_below_intended")

    retry = fields.get("retry_status")
    if retry == RetryStatus.SUPERSEDED.value:
        reasons.append("superseded_by_retry")

    n_frames, n_invalid = fields.get("n_frames"), fields.get("n_invalid_frames")
    if n_frames is not None and n_invalid is not None:
        if int(n_invalid) < 0 or int(n_frames) < 0:
            reasons.append("frame_counts_negative")
        elif int(n_invalid) > int(n_frames):
            reasons.append("invalid_frames_exceed_total")
        elif packet_loss is not None and int(packet_loss) > 0 and int(n_invalid) == 0:
            # Packets were lost but no frame is marked invalid: the map cannot be describing
            # the same capture, and a window containing a dropped frame must be radar-NaN.
            reasons.append("validity_map_inconsistent_with_packet_loss")

    verdict = Admission.EXCLUDED if reasons else Admission.ADMITTED
    return verdict, tuple(reasons)


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
    """§4.1: finite, metres, 0.8 <= d <= 1.4 **inclusive**."""
    try:
        d = float(value)
    except (TypeError, ValueError):
        raise ManifestError(
            f"session {session_id!r}: distance_m={value!r} is not a number. It is metres "
            "(the legacy run_metadata field is distance_cm — convert explicitly, never "
            "implicitly)."
        ) from None
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
        if int(rate) not in PACED_RATES_BPM:
            raise ManifestError(
                f"session {session_id!r}: commanded_rate_bpm={rate!r} is not one of "
                f"{PACED_RATES_BPM} (the frozen M3R-31 rotation)."
            )
        rate = int(rate)
    elif arm is Arm.NATURAL and rate is not None:
        raise ManifestError(
            f"session {session_id!r}: arm is 'natural' but commanded_rate_bpm={rate!r} is "
            "set. A natural session has no commanded rate."
        )

    distance = _validate_distance(fields["distance_m"], session_id) if present("distance_m") else None
    posture = _validate_posture(fields["posture"], session_id) if present("posture") else None

    frame0 = fields.get("frame0_epoch")
    if frame0 is not None:
        frame0 = float(frame0)
        if not math.isfinite(frame0):
            raise ManifestError(
                f"session {session_id!r}: frame0_epoch={frame0!r} is not finite. It is the "
                "synchronised UTC time at receipt of frame 0 — never start_wall_utc."
            )

    admission = (_as_enum(fields["admission"], Admission, "admission", session_id)
                 if present("admission") else None)
    reasons: tuple[str, ...] = ()
    if mode is Mode.SCORING:
        recomputed, reasons = recompute_admission(fields, session_id)
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
        distance_m=distance,
        posture=posture,
        frame0_epoch=frame0,
        clock_offset_start_s=fields.get("clock_offset_start_s"),
        clock_offset_end_s=fields.get("clock_offset_end_s"),
        raw_path=fields.get("raw_path"),
        raw_sha256=fields.get("raw_sha256"),
        truncation_bytes=fields.get("truncation_bytes"),
        packet_loss_frames=fields.get("packet_loss_frames"),
        n_frames=fields.get("n_frames"),
        n_invalid_frames=fields.get("n_invalid_frames"),
        frame_validity_map_path=fields.get("frame_validity_map_path"),
        frame_validity_map_sha256=fields.get("frame_validity_map_sha256"),
        capture_config_sha256=fields.get("capture_config_sha256"),
        capture_git_commit=fields.get("capture_git_commit"),
        masimo_path=fields.get("masimo_path"),
        masimo_sha256=fields.get("masimo_sha256"),
        commanded_rate_schedule=tuple(fields.get("commanded_rate_schedule", ()) or ()),
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
    """
    p = Path(path)
    doc = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or "sessions" not in doc:
        raise ManifestError(
            f"{p}: manifest must be a JSON object with a 'sessions' array."
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
    """Refuse to let development-mode sessions reach a scoring path (§4).

    The separation has to be enforced somewhere executable; a naming convention is not a
    control. Call this at the entry of anything that emits a frozen-comparator number.
    """
    offenders = [s.session_id for s in sessions if not s.is_scorable]
    if offenders:
        raise ManifestError(
            f"{what} requires SCORING mode, but these sessions are DEVELOPMENT: "
            f"{', '.join(repr(o) for o in offenders)}. Development output is exploratory / "
            "apparent / in-sample and can never be a frozen-comparator number "
            "(plans/m4_offline_harness.md §2.2, §4)."
        )
