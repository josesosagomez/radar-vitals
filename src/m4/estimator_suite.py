"""Estimator-neutral window-suite contracts (M8 Step 1b plan section 4.1).

A *suite* evaluates one window once and returns several named *arms* — the production
estimator and the six Ahmed phase arms are both suites. Keeping the contract neutral is
what lets the runner treat them identically and lets the shared extracted phase be
computed once per window and referenced by every arm.

This module defines only the contracts and their validation. Concrete suites live with
their estimators (`src/m8/ahmed_transfer.py` for the Ahmed arms).

Two invariants matter for provenance and are enforced here rather than by convention:

* **Evidence arrays are immutable.** Every array stored in a result is copied,
  made C-contiguous, and marked read-only, so a later stage cannot mutate evidence that
  has already been hashed.
* **Configuration is bound at construction.** A suite copies its config into canonical
  plain values and hashes it before freezing, so mutating the caller's original dict
  afterwards cannot change hashes or results.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, runtime_checkable

import numpy as np

__all__ = [
    "EstimatorArmSpec",
    "OutcomeClassifier",
    "SuiteWindowResult",
    "WindowEstimatorSuite",
    "canonical_plain",
    "freeze_array",
    "validate_evidence_mapping",
]

#: The only value types permitted in shared evidence. Deliberately narrow: NPZ must load
#: with allow_pickle=False and contain no object dtype (plan section 5.2).
_SCALAR_EVIDENCE = (bool, int, float, str)


def freeze_array(values: np.ndarray) -> np.ndarray:
    """Return an immutable, C-contiguous, non-object copy of `values`."""
    array = np.ascontiguousarray(values)
    if array.dtype == object:
        raise TypeError("evidence arrays must not have object dtype")
    frozen = array.copy()
    frozen.setflags(write=False)
    return frozen


def validate_evidence_mapping(evidence: Mapping[str, object], *, label: str) -> None:
    """Fail closed on any evidence value outside the declared EvidenceValue union."""
    for key, value in evidence.items():
        if not isinstance(key, str):
            raise TypeError(f"{label}: evidence keys must be str, got {type(key).__name__}")
        if value is None or isinstance(value, _SCALAR_EVIDENCE):
            continue
        if isinstance(value, np.ndarray):
            if value.dtype == object:
                raise TypeError(f"{label}: evidence {key!r} has object dtype")
            if value.flags.writeable:
                raise ValueError(
                    f"{label}: evidence {key!r} is writeable; use freeze_array()"
                )
            continue
        raise TypeError(
            f"{label}: evidence {key!r} has unsupported type {type(value).__name__}; "
            "permitted: ndarray, None, bool, int, float, str"
        )


def canonical_plain(value: object) -> object:
    """Recursively convert a config into plain, canonically ordered Python values.

    NumPy scalars become their Python equivalents and mappings are key-sorted, so the
    resulting structure hashes identically regardless of how the caller built it.
    """
    if isinstance(value, Mapping):
        return {str(k): canonical_plain(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [canonical_plain(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    raise TypeError(f"config contains non-canonicalizable {type(value).__name__}")


@dataclass(frozen=True)
class EstimatorArmSpec:
    """Static identity of one arm. Never derived from a window's observed values."""

    arm_id: str
    estimator_id: str
    run_config_hash: str
    harmonic_count: int | None = None
    suppression_profile: str | None = None
    outcome_classifier_id: str | None = None

    def __post_init__(self) -> None:
        if not self.arm_id or any(c in self.arm_id for c in r'/\:*?"<>| '):
            raise ValueError(f"arm_id {self.arm_id!r} must be non-empty and path-safe")
        if not self.estimator_id:
            raise ValueError("estimator_id must be non-empty")
        if not self.run_config_hash:
            raise ValueError("run_config_hash must be non-empty")
        if self.harmonic_count is not None and (
            type(self.harmonic_count) is not int or self.harmonic_count <= 0
        ):
            raise ValueError("harmonic_count must be a positive exact int or None")


@dataclass(frozen=True)
class SuiteWindowResult:
    """One window's shared evidence plus every arm's native payload.

    Shared arrays are stored once here — never duplicated into each arm — and each arm
    refers to them by `shared_signal_hash`.

    `arm_outcomes` holds classifier labels *outside* the native payloads, so a native
    payload stays byte-equivalent to what a direct estimator call would produce (plan
    section 4.1). Only arms whose spec declares a classifier may appear here.
    """

    shared_evidence: Mapping[str, object]
    arm_native_results: Mapping[str, dict]
    arm_outcomes: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_evidence_mapping(self.shared_evidence, label="shared_evidence")
        if not self.arm_native_results:
            raise ValueError("arm_native_results must not be empty")
        for arm_id, native in self.arm_native_results.items():
            if not isinstance(native, dict):
                raise TypeError(f"arm {arm_id!r} native result must be a dict")
        unknown = set(self.arm_outcomes) - set(self.arm_native_results)
        if unknown:
            raise ValueError(f"arm_outcomes references unknown arms {sorted(unknown)}")
        for arm_id, label in self.arm_outcomes.items():
            if not isinstance(label, str):
                raise TypeError(f"outcome for arm {arm_id!r} must be str")


@runtime_checkable
class OutcomeClassifier(Protocol):
    """Maps one arm's native payload to a scalar outcome label."""

    classifier_id: str

    def __call__(self, native_result: Mapping[str, object]) -> str: ...


@runtime_checkable
class WindowEstimatorSuite(Protocol):
    """Evaluates one window and returns exactly its declared arm set."""

    suite_id: str
    suite_config_hash: str
    arm_specs: tuple[EstimatorArmSpec, ...]
    outcome_classifiers: Mapping[str, OutcomeClassifier]

    def __call__(self, frames, locked_bin: int, fs: float) -> SuiteWindowResult: ...


def validate_arm_specs(arm_specs: tuple[EstimatorArmSpec, ...]) -> None:
    """Arm ids unique, (estimator_id, run_config_hash) unique."""
    if not arm_specs:
        raise ValueError("a suite must declare at least one arm")
    arm_ids = [spec.arm_id for spec in arm_specs]
    if len(set(arm_ids)) != len(arm_ids):
        raise ValueError(f"duplicate arm_id in {arm_ids}")
    identities = [(spec.estimator_id, spec.run_config_hash) for spec in arm_specs]
    if len(set(identities)) != len(identities):
        raise ValueError(f"duplicate (estimator_id, run_config_hash) in {identities}")


def validate_returned_arms(
    arm_specs: tuple[EstimatorArmSpec, ...], result: SuiteWindowResult
) -> None:
    """Missing, duplicate, or unexpected arms are fatal (plan section 4.1)."""
    expected = {spec.arm_id for spec in arm_specs}
    returned = set(result.arm_native_results)
    if returned != expected:
        raise ValueError(
            f"suite returned arms {sorted(returned)}, expected exactly {sorted(expected)}"
        )
