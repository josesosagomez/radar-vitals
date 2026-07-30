"""Radar-only stage runner: preflight, dispatch, and Cartesian completeness.

Plan section 4.2. Two properties matter more than anything else here.

**Preflight happens before any capture path is touched.** Not before decoding — before the
first `stat` or `open`. Checking authorization after opening a file would already have
leaked the thing the authorization exists to control, and would let an operator learn
something about the data before deciding whether they were allowed to. `run_radar_stage`
therefore validates the gate, the authorization, and the parent manifest first, and the
tests assert that a rejected preflight performs **zero** filesystem access on any capture
path.

**Every eligible Cartesian tuple must produce exactly one row.** A missing row is a fatal
incomplete run, not a quietly smaller denominator; a duplicate row would double-count. The
ledger asserts this rather than trusting the loop.

This module is handed only the registry's radar scope, so it structurally cannot reach a
Masimo path — see `src/m4/capture_registry.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import yaml

from src.m4.bundle import read_manifest, sha256_path, verify_bundle
from src.m4.capture_registry import RadarScope

__all__ = [
    "Authorization",
    "CartesianLedger",
    "PreflightError",
    "load_authorization",
    "verify_gate_bundle",
    "verify_preflight",
]

#: Lock estimands are reported separately and never pooled, even when numerically equal.
LOCK_ESTIMANDS = ("recorded_lock_as_captured", "current_production_rerun_lock")

REAL_STAGES = ("real-smoke", "real-radar", "score")


class PreflightError(RuntimeError):
    """Raised before any capture access when a precondition is not satisfied."""


@dataclass(frozen=True)
class Authorization:
    """One frozen, comprehensive real-evaluation authorization (plan section 5.1).

    Deliberately covers the whole `real-smoke -> real-radar -> score` chain at once. If
    each stage were authorized separately, an operator could look at the smoke output and
    then decide whether to continue — outcome-adaptive stopping. Abandoning the chain must
    be an explicit, recorded act instead.
    """

    authorization_id: str
    gate_manifest_sha256: str
    source_manifest_sha256: str
    allowed_stages: tuple[str, ...]
    capture_ids: tuple[str, ...]
    lock_estimands: tuple[str, ...]
    arm_ids: tuple[str, ...]
    approved_by: str
    approved_on: str
    continuation_rationale_sha256: str | None = None

    def permits(self, stage: str) -> bool:
        return stage in self.allowed_stages


def load_authorization(path: Path) -> Authorization:
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise PreflightError(f"{path}: authorization must be a mapping")
    missing = [
        key
        for key in (
            "authorization_id",
            "gate_manifest_sha256",
            "source_manifest_sha256",
            "allowed_stages",
            "capture_ids",
            "lock_estimands",
            "arm_ids",
            "approved_by",
            "approved_on",
        )
        if key not in document
    ]
    if missing:
        raise PreflightError(f"{path}: authorization missing {missing}")
    return Authorization(
        authorization_id=str(document["authorization_id"]),
        gate_manifest_sha256=str(document["gate_manifest_sha256"]),
        source_manifest_sha256=str(document["source_manifest_sha256"]),
        allowed_stages=tuple(document["allowed_stages"]),
        capture_ids=tuple(document["capture_ids"]),
        lock_estimands=tuple(document["lock_estimands"]),
        arm_ids=tuple(document["arm_ids"]),
        approved_by=str(document["approved_by"]),
        approved_on=str(document["approved_on"]),
        continuation_rationale_sha256=(
            None
            if document.get("continuation_rationale_sha256") is None
            else str(document["continuation_rationale_sha256"])
        ),
    )


def verify_gate_bundle(gate_dir: Path) -> dict:
    """A gate may parent a real stage only if complete, passed, and promotion-eligible.

    `status == "complete"` is not sufficient: a complete bundle built from dirty or
    untracked scoped sources is useful draft evidence but is not reproducible, so it can
    never parent real data (plan section 5.1).
    """
    gate_dir = Path(gate_dir)
    if not (gate_dir / "manifest.json").is_file():
        raise PreflightError(f"no gate manifest at {gate_dir}")
    manifest = verify_bundle(gate_dir)
    if manifest.get("stage") != "synthetic":
        raise PreflightError(f"{gate_dir} is stage {manifest.get('stage')!r}, not synthetic")
    if manifest.get("status") != "complete":
        raise PreflightError(f"gate status is {manifest.get('status')!r}, not complete")
    if manifest.get("gate_status") != "passed":
        raise PreflightError(f"gate verdict is {manifest.get('gate_status')!r}, not passed")
    if not manifest.get("promotion_eligible"):
        raise PreflightError(
            "gate is not promotion-eligible: its scoped sources were dirty or untracked, "
            "so it is draft evidence and cannot parent a real-data stage"
        )
    return manifest


@dataclass(frozen=True)
class PreflightResult:
    gate_manifest: Mapping[str, object]
    gate_manifest_sha256: str
    authorization: Authorization
    parent_manifest_sha256: str | None


def verify_preflight(
    *,
    stage: str,
    gate_dir: Path,
    authorization_path: Path,
    source_manifest_sha256: str,
    parent_dir: Path | None = None,
) -> PreflightResult:
    """Validate everything required before the first capture path is touched.

    Ordering is the point. Every check here reads only bundle and authorization files,
    never a capture.
    """
    if stage not in REAL_STAGES:
        raise PreflightError(f"unknown real stage {stage!r}; expected one of {REAL_STAGES}")

    manifest = verify_gate_bundle(gate_dir)
    _manifest, gate_digest = read_manifest(Path(gate_dir))

    authorization = load_authorization(authorization_path)
    if not authorization.permits(stage):
        raise PreflightError(
            f"authorization {authorization.authorization_id!r} does not permit {stage!r}; "
            f"it allows {list(authorization.allowed_stages)}"
        )
    if authorization.gate_manifest_sha256 != gate_digest:
        raise PreflightError(
            "authorization is bound to a different gate: expected "
            f"{authorization.gate_manifest_sha256}, found {gate_digest}"
        )
    if authorization.source_manifest_sha256 != source_manifest_sha256:
        raise PreflightError(
            "scoped sources changed since the authorization was frozen: expected "
            f"{authorization.source_manifest_sha256}, found {source_manifest_sha256}"
        )

    parent_digest = None
    if parent_dir is not None:
        parent_manifest = verify_bundle(Path(parent_dir))
        if parent_manifest.get("status") != "complete":
            raise PreflightError(
                f"parent stage {parent_manifest.get('stage')!r} is "
                f"{parent_manifest.get('status')!r}, not complete"
            )
        _pm, parent_digest = read_manifest(Path(parent_dir))

    return PreflightResult(
        gate_manifest=manifest,
        gate_manifest_sha256=gate_digest,
        authorization=authorization,
        parent_manifest_sha256=parent_digest,
    )


@dataclass
class CartesianLedger:
    """Tracks that every eligible (capture, lock, k, arm) tuple yields exactly one row."""

    expected: set[tuple[str, str, int, str]] = field(default_factory=set)
    seen: dict[tuple[str, str, int, str], int] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        radar: RadarScope,
        capture_ids: Sequence[str],
        lock_estimands: Sequence[str],
        arm_ids: Sequence[str],
    ) -> "CartesianLedger":
        expected = {
            (capture_id, lock, k, arm)
            for capture_id in capture_ids
            for lock in lock_estimands
            for k in range(radar.capture(capture_id).windows)
            for arm in arm_ids
        }
        return cls(expected=expected)

    def record(self, capture_id: str, lock: str, k: int, arm_id: str) -> None:
        key = (capture_id, lock, k, arm_id)
        if key not in self.expected:
            raise ValueError(f"unexpected row {key}: not in the authorized Cartesian scope")
        self.seen[key] = self.seen.get(key, 0) + 1

    def assert_complete(self) -> int:
        duplicates = sorted(k for k, n in self.seen.items() if n > 1)
        if duplicates:
            raise ValueError(f"duplicate rows emitted: {duplicates[:5]} ({len(duplicates)})")
        missing = sorted(self.expected - set(self.seen))
        if missing:
            raise ValueError(
                f"incomplete run: {len(missing)} rows missing, e.g. {missing[:5]}. "
                "A missing row is a fatal incompleteness, never a smaller denominator."
            )
        return len(self.expected)

    @property
    def expected_count(self) -> int:
        return len(self.expected)
