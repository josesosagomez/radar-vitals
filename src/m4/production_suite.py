"""Production estimator wrapped as a neutral window suite (M8 Step 1b plan section 4.1).

Presents the unchanged production DSP through the `WindowEstimatorSuite` contract so the
runner can dispatch it identically to the Ahmed arms. This module adds **no** science: the
arm's native payload is whatever `run_window_dsp` returns, unmodified, and the suite is
required to be full-native-payload equivalent to a direct call.

Two things live here rather than in `src/m4/estimator_suite.py`, which stays purely
contractual and estimator-neutral: this wrapper (which necessarily imports the production
pipeline) and the sole outcome-classifier adapter.

The classifier label is stored in `SuiteWindowResult.arm_outcomes`, deliberately *outside*
the native payload, so the payload stays byte-equivalent to a direct call.
"""
from __future__ import annotations

import copy
from typing import Mapping

from src.m4.estimator_suite import (
    EstimatorArmSpec,
    SuiteWindowResult,
    canonical_plain,
    validate_arm_specs,
)
from src.m4.outcome import classify_window_outcome
from src.window_pipeline import run_config_hash, run_window_dsp

__all__ = [
    "PRODUCTION_ARM_ID",
    "PRODUCTION_ESTIMATOR_ID",
    "EcaBindriftOutcomeClassifier",
    "ProductionEstimatorSuite",
    "STRICT_GATE_MODE",
]

PRODUCTION_ESTIMATOR_ID = "eca_ahet_v1"
PRODUCTION_ARM_ID = "production_eca_ahet_v1"
OUTCOME_CLASSIFIER_ID = "eca_bindrift_outcome_v1"
STRICT_GATE_MODE = "strict_v1"


class EcaBindriftOutcomeClassifier:
    """The one adapter around `src/m4/outcome.py::classify_window_outcome`.

    Mirrors the existing call in `scripts/score_offline.py` exactly, including its
    `None -> NaN` mapping of `f_r_hz`: the classifier distinguishes "the gate never ran"
    from "the gate ran and rejected everything", and a missing `f_r_hz` is the former.
    """

    classifier_id = OUTCOME_CLASSIFIER_ID

    def __call__(self, native_result: Mapping[str, object]) -> str:
        hr_result = native_result["hr_result"]
        f_r_hz = native_result["f_r_hz"]
        f_r_for_outcome = float("nan") if f_r_hz is None else float(f_r_hz)
        return classify_window_outcome(
            hr_result["accepted_candidate_rank"],
            hr_result["candidate_rejection_code"],
            f_r_for_outcome,
        )


class ProductionEstimatorSuite:
    """One-arm suite wrapping `run_window_dsp` without altering it.

    The configuration is bound at construction: it is deep-copied into canonical plain
    values and hashed before use, so mutating the caller's dict afterwards cannot change
    hashes or results. Runtime callers cannot pass or substitute a config.
    """

    suite_id = "production_eca_ahet_suite_v1"

    def __init__(
        self,
        config: Mapping[str, object],
        outcome_classifier: EcaBindriftOutcomeClassifier | None = None,
    ) -> None:
        if not isinstance(config, Mapping):
            raise TypeError("config must be a mapping")
        plain = canonical_plain(config)
        # Retain a private deep copy; every call materializes a fresh mutable dict from
        # it, so neither the caller nor the production code can reach this state.
        self._plain_config = copy.deepcopy(plain)
        self.suite_config_hash = run_config_hash(self._plain_config)

        gate_mode = str(
            (self._plain_config.get("heart") or {}).get("ahet_gate_mode", "")
        )
        self._is_strict = gate_mode == STRICT_GATE_MODE
        classifier = outcome_classifier or EcaBindriftOutcomeClassifier()
        if classifier.classifier_id != OUTCOME_CLASSIFIER_ID:
            raise ValueError(
                f"unexpected classifier id {classifier.classifier_id!r}; "
                f"the production arm declares {OUTCOME_CLASSIFIER_ID!r}"
            )
        self._classifier = classifier

        self.arm_specs = (
            EstimatorArmSpec(
                arm_id=PRODUCTION_ARM_ID,
                estimator_id=PRODUCTION_ESTIMATOR_ID,
                run_config_hash=self.suite_config_hash,
                harmonic_count=None,
                suppression_profile=None,
                # Only declared when the gate mode actually produces classifiable
                # evidence; outside strict_v1 the classifier's preconditions do not hold.
                outcome_classifier_id=OUTCOME_CLASSIFIER_ID if self._is_strict else None,
            ),
        )
        validate_arm_specs(self.arm_specs)
        self.outcome_classifiers = (
            {PRODUCTION_ARM_ID: self._classifier} if self._is_strict else {}
        )

    @property
    def config_snapshot(self) -> dict:
        """A fresh mutable copy. Mutating it cannot affect the suite."""
        return copy.deepcopy(self._plain_config)

    def __call__(self, frames, locked_bin: int, fs: float) -> SuiteWindowResult:
        native = run_window_dsp(frames, locked_bin, fs, self.config_snapshot)
        outcomes: dict[str, str] = {}
        if self._is_strict:
            outcomes[PRODUCTION_ARM_ID] = self._classifier(native)
        return SuiteWindowResult(
            shared_evidence={
                "suite_id": self.suite_id,
                "suite_config_hash": self.suite_config_hash,
                "fs_hz": float(fs),
                "locked_bin": int(locked_bin),
                "ahet_gate_mode": STRICT_GATE_MODE if self._is_strict else "non_strict",
            },
            arm_native_results={PRODUCTION_ARM_ID: native},
            arm_outcomes=outcomes,
        )
