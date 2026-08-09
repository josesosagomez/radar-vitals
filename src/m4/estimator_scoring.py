"""Canonical M8 scoring of one immutable same-run paired radar artifact.

This module is deliberately downstream-only.  It never imports a decoder or estimator and
it is handed only :class:`~src.m4.capture_registry.ReferenceScope`, which contains no ADC
mapping.  The public ``run_score_stage`` entry point validates the frozen gate,
authorization, and complete radar parent before constructing, hashing, or opening a Masimo
path.

The comparative universe is ``k >= 1``.  ``k == 0`` is retained in a separately labelled
in-sample diagnostic and is never blended into comparative metrics.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import subprocess
from typing import Callable, Iterable, Literal, Mapping, Sequence

import numpy as np
import yaml

from src.comparator import br_reference, hr_reference
from src.masimo import load_masimo
from src.m4.bundle import (
    BundleWriter,
    StageBundle,
    read_manifest,
    sha256_bytes,
    sha256_path,
    strict_json_bytes,
    verify_bundle,
)
from src.m4.capture_registry import DEFAULT_REGISTRY, ReferenceScope, load_registry
from src.m4.estimator_runner import (
    CANONICAL_ARM_IDS,
    LOCK_ESTIMANDS,
    Authorization,
    PreflightError,
    verify_preflight,
)
from src.m4.evidence_serialization import deserialize_native_tree
from src.m4.production_suite import PRODUCTION_ARM_ID
from src.m8.ahmed_provenance import (
    SourceManifest,
    build_source_manifest,
    load_source_manifest,
    verify_source_manifest,
)

__all__ = [
    "COMPARATIVE_UNIVERSE",
    "DIAGNOSTIC_UNIVERSE",
    "MetricSummary",
    "ScoreContractError",
    "ScoredRow",
    "coverage_and_metrics",
    "execute_score",
    "paired_partitions",
    "percentiles",
    "production_audit_summary",
    "persist_score_artifacts",
    "run_score_stage",
    "validate_radar_parent",
    "validate_radar_rows",
    "verify_frozen_registry_agreement",
]

Vital = Literal["hr", "br"]
DIAGNOSTIC_UNIVERSE = "full_k0_diagnostic"
COMPARATIVE_UNIVERSE = "evaluation_k_ge_1"
EVALUATION_STATUS = "exploratory_non_frozen"
TIME_ORIGIN_ID = "start_wall_utc_approximate_v1"
CLAIM_STATUS = "not_eligible_for_promotion_or_final_agreement_claims"
DATA_ROLE = "development_apparent_single_subject"
#: Documented uncertainty of the approximate ``start_wall_utc`` frame-zero origin, in
#: seconds (plan section 3, "approximate ``start_wall_utc`` origins retain their
#: documented 5-15 s uncertainty").  Declared, never measured or optimised from data.
#: The magnitude matters: 5-15 s is 17-50% of a 30 s window, so a reader of the scored
#: artifacts must be able to see how far the radar/reference alignment may be off.
ORIGIN_UNCERTAINTY_SECONDS_RANGE = (5, 15)

_SUMMARY_MACRO_METRICS = (
    "n_source",
    "n_reference_admitted",
    "n_radar_valid",
    "n_joint",
    "reference_coverage",
    "radar_coverage",
    "joint_coverage",
    "joint_given_reference",
    "mae",
    "rmse",
    "bias",
    "error_percentiles.p50",
    "error_percentiles.p75",
    "error_percentiles.p90",
    "error_percentiles.p95",
)
_PAIRWISE_MACRO_METRICS = (
    "n_reference_admitted",
    "partitions.both",
    "partitions.production_only",
    "partitions.ahmed_only",
    "partitions.neither",
    "n_intersection",
    *tuple(
        f"{prefix}.{metric}"
        for prefix in (
            "production_metrics_on_intersection",
            "ahmed_metrics_on_intersection",
        )
        for metric in _SUMMARY_MACRO_METRICS
    ),
    *tuple(
        f"descriptive_difference_ahmed_minus_production.{metric}"
        for metric in ("mae", "rmse", "bias", "p50", "p75", "p90", "p95")
    ),
)

# The next two tables restate ``windows``, ``recorded_lock`` and ``rerun_lock`` from
# ``experiments/m8_ahmed_transfer/capture_registry.yaml``.  They are kept as literals so
# that importing the scorer never reads a file, and
# :func:`verify_frozen_registry_agreement` re-derives them from the registry before the
# ``validate_radar_rows`` gate uses them.  See that function for why.
EXPECTED_CAPTURE_WINDOWS = {
    "m1": 6,
    "m2": 6,
    "sweep": 16,
    "m3": 20,
    "m4": 20,
    "m5": 20,
    "m6": 20,
    "m7": 20,
}
EXPECTED_LOCKS = {
    "m1": (23, 27),
    "m2": (20, 26),
    "sweep": (21, 26),
    "m3": (26, 26),
    "m4": (25, 25),
    "m5": (25, 25),
    "m6": (24, 24),
    "m7": (32, 32),
}
EXPECTED_RADAR_PAYLOADS = {
    "rows.json",
    "selector_evidence.json",
    "shared_evidence.npz",
    "ahmed_evidence.npz",
    "production_native_index.json",
    "production_evidence.npz",
    "resolved_config.yaml",
    "provenance.json",
}

# M1 evaluates the deployed production lock.  The recorded historical lock remains in
# the M4 ledger and paired M8 artifacts, but it is not the lock selected by the current
# production code.  Keeping this identity explicit prevents an apparently innocuous
# aggregation from silently mixing the two estimands.
PRODUCTION_LOCK_ESTIMAND_ID = "current_production_rerun_lock"
PRODUCTION_ALL_WINDOWS_UNIVERSE = "all_complete_windows_k_ge_0"
PRODUCTION_PERSISTED_LOCK_UNIVERSE = "persisted_lock_windows_k_ge_1"
PRODUCTION_K0_UNIVERSE = "lock_selection_in_sample_k0"


class ScoreContractError(RuntimeError):
    """Malformed parent, join, reference, or scoring state."""


@dataclass(frozen=True)
class ScoredRow:
    """One estimator/vital row after one common reference has been resolved."""

    capture_id: str
    lock_estimand_id: str
    k: int
    arm_id: str
    radar_value: float | None
    radar_valid: bool
    reference_value: float | None
    reference_admitted: bool
    reference_reason: str = ""
    radar_reason: str = "invalid"
    vital: Vital = "hr"
    window_universe: str = COMPARATIVE_UNIVERSE
    protocol: str = ""
    protocol_stratum: str = ""

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.capture_id, self.lock_estimand_id, self.k)

    @property
    def disposition(self) -> str:
        """Mutually exclusive disposition with reference precedence."""
        if not self.reference_admitted:
            if not self.reference_reason:
                raise ScoreContractError("excluded reference row has no reason")
            return f"reference_{self.reference_reason}"
        if not self.radar_valid:
            if not self.radar_reason or self.radar_reason == "ok":
                raise ScoreContractError("invalid radar row has no exact validity reason")
            return f"radar_{self.radar_reason}"
        if self.radar_value is None or not math.isfinite(float(self.radar_value)):
            raise ScoreContractError("valid radar row has no finite value")
        if self.reference_value is None or not math.isfinite(float(self.reference_value)):
            raise ScoreContractError("admitted reference row has no finite value")
        return "joint"


def percentiles(
    values: Sequence[float], points: Sequence[int] = (50, 75, 90, 95)
) -> dict[str, float | None]:
    """Absolute-error percentiles with the approved explicit linear method."""
    if not len(values):
        return {f"p{point}": None for point in points}
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1 or np.any(~np.isfinite(array)):
        raise ScoreContractError("percentile input must be a finite one-dimensional sequence")
    return {
        f"p{point}": float(np.percentile(array, point, method="linear"))
        for point in points
    }


@dataclass(frozen=True)
class MetricSummary:
    n_source: int
    n_reference_admitted: int
    n_radar_valid: int
    n_joint: int
    reference_coverage: float | None
    radar_coverage: float | None
    joint_coverage: float | None
    joint_given_reference: float | None
    mae: float | None
    rmse: float | None
    bias: float | None
    error_percentiles: Mapping[str, float | None]
    exclusions: Mapping[str, int]
    reference_exclusions: Mapping[str, int]
    radar_exclusions: Mapping[str, int]

    def to_dict(self) -> dict:
        return {
            "n_source": self.n_source,
            "n_reference_admitted": self.n_reference_admitted,
            "n_radar_valid": self.n_radar_valid,
            "n_joint": self.n_joint,
            "reference_coverage": self.reference_coverage,
            "radar_coverage": self.radar_coverage,
            "joint_coverage": self.joint_coverage,
            "joint_given_reference": self.joint_given_reference,
            "mae": self.mae,
            "rmse": self.rmse,
            "bias": self.bias,
            "error_percentiles": dict(self.error_percentiles),
            "exclusions": dict(self.exclusions),
            "reference_exclusions": dict(self.reference_exclusions),
            "radar_exclusions": dict(self.radar_exclusions),
        }


def _fraction(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _radar_marginal_reason(row: ScoredRow) -> str | None:
    """Radar-side exclusion reason for one row, independent of the reference.

    The mutually exclusive ``exclusions`` histogram applies reference precedence, so a
    row the reference rejected never exposes its radar-side state there.  The radar
    marginal is a measured radar-coverage quantity and must therefore account for every
    source row whether or not the reference admitted it.  Returns ``None`` when the
    radar delivered a usable finite estimate for this row.
    """
    if not row.radar_valid:
        if not row.radar_reason or row.radar_reason == "ok":
            raise ScoreContractError("invalid radar row has no exact marginal reason")
        return row.radar_reason
    # A row that claims radar validity but carries no finite estimate is a malformed
    # scientific state, not a coverage category.  It is never silently downgraded to an
    # exclusion or coerced into a value, and the reference disposition cannot excuse it.
    if row.radar_value is None or not math.isfinite(float(row.radar_value)):
        raise ScoreContractError("valid radar row has no finite value")
    return None


def coverage_and_metrics(rows: Iterable[ScoredRow]) -> MetricSummary:
    """Measured coverage and joint-only errors for one exact scientific grouping."""
    rows = list(rows)
    exclusions: dict[str, int] = {}
    reference_exclusions: dict[str, int] = {}
    radar_exclusions: dict[str, int] = {}
    signed_errors: list[float] = []
    n_reference_admitted = 0
    n_radar_valid = 0

    for row in rows:
        disposition = row.disposition
        exclusions[disposition] = exclusions.get(disposition, 0) + 1
        if not row.reference_admitted:
            reference_exclusions[row.reference_reason] = (
                reference_exclusions.get(row.reference_reason, 0) + 1
            )
        radar_reason = _radar_marginal_reason(row)
        if radar_reason is not None:
            radar_exclusions[radar_reason] = radar_exclusions.get(radar_reason, 0) + 1
        n_reference_admitted += int(row.reference_admitted)
        n_radar_valid += int(radar_reason is None)
        if disposition == "joint":
            signed_errors.append(float(row.radar_value) - float(row.reference_value))

    # Coverage is measured, never asserted "100% by construction": the mutually exclusive
    # disposition histogram and both marginals must each account for every source row.
    #
    # The three guards below are invariant assertions, not the substantive protection.
    # A well-formed ``ScoredRow`` contributes exactly one unit to each total and to each
    # marginal, so none of them can be tripped by any valid input; they exist to catch
    # future refactoring drift in this loop.  What actually guarantees the radar marginal
    # is honest is that ``_radar_marginal_reason`` is the single predicate from which both
    # ``n_radar_valid`` and ``radar_exclusions`` are derived, so a row cannot be counted
    # as covered by one and excluded by the other.
    n_source = len(rows)
    if sum(exclusions.values()) != n_source:
        raise ScoreContractError("scoring dispositions do not reconcile to source rows")
    if n_source - n_reference_admitted != sum(reference_exclusions.values()):
        raise ScoreContractError(
            "reference exclusion marginal does not reconcile to n_source - n_reference_admitted"
        )
    if n_source - n_radar_valid != sum(radar_exclusions.values()):
        raise ScoreContractError(
            "radar exclusion marginal does not reconcile to n_source - n_radar_valid"
        )
    if signed_errors:
        differences = np.asarray(signed_errors, dtype=np.float64)
        absolute = np.abs(differences)
        mae = float(np.mean(absolute))
        rmse = float(np.sqrt(np.mean(differences**2)))
        bias = float(np.mean(differences))
    else:
        absolute = np.empty(0, dtype=np.float64)
        mae = rmse = bias = None
    n_joint = exclusions.get("joint", 0)
    return MetricSummary(
        n_source=n_source,
        n_reference_admitted=n_reference_admitted,
        n_radar_valid=n_radar_valid,
        n_joint=n_joint,
        reference_coverage=_fraction(n_reference_admitted, n_source),
        radar_coverage=_fraction(n_radar_valid, n_source),
        joint_coverage=_fraction(n_joint, n_source),
        joint_given_reference=_fraction(n_joint, n_reference_admitted),
        mae=mae,
        rmse=rmse,
        bias=bias,
        error_percentiles=percentiles(absolute.tolist()),
        exclusions=dict(sorted(exclusions.items())),
        reference_exclusions=dict(sorted(reference_exclusions.items())),
        radar_exclusions=dict(sorted(radar_exclusions.items())),
    )


def _capture_macro_summary(
    per_capture: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Equal-capture coverage with zero-output captures retained.

    Accuracy is intentionally absent.  A capture with no joint rows has undefined
    accuracy but has a perfectly well-defined zero coverage, and dropping that capture
    is the survivor-bias defect M1 retires.
    """
    coverage_fields = (
        "reference_coverage",
        "radar_coverage",
        "joint_coverage",
        "joint_given_reference",
    )
    metric_means: dict[str, float | None] = {}
    contributing_counts: dict[str, int] = {}
    for field in coverage_fields:
        values = [
            float(record["metrics"][field])
            for record in per_capture
            if record["metrics"][field] is not None
        ]
        metric_means[field] = float(np.mean(values)) if values else None
        contributing_counts[field] = len(values)
    return {
        "metric_means": metric_means,
        "contributing_capture_count_by_metric": contributing_counts,
        "coverage_capture_count": len(per_capture),
        "zero_radar_output_capture_ids": sorted(
            str(record["capture_id"])
            for record in per_capture
            if record["metrics"]["n_radar_valid"] == 0
        ),
        "zero_joint_output_capture_ids": sorted(
            str(record["capture_id"])
            for record in per_capture
            if record["metrics"]["n_joint"] == 0
        ),
        "zero_output_policy": (
            "coverage is defined as zero and retained; undefined accuracy never removes "
            "a capture from a coverage mean"
        ),
    }


def production_audit_summary(
    rows: Iterable[ScoredRow],
    *,
    expected_capture_windows: Mapping[str, int] = EXPECTED_CAPTURE_WINDOWS,
) -> dict[str, object]:
    """Reconstruct M1 production denominators from exact M4 scoring-row identities.

    Input may contain every M4 arm, lock and vital.  The function selects exactly one HR
    row for each ``(capture_id, k)`` under the current production lock and fails closed on
    a duplicate, missing or extra key.  The full ``k >= 0`` result remains primary for
    honest all-window coverage; ``k == 0`` and the persisted-lock ``k >= 1`` subset are
    also reported separately without removing ``k == 0`` from the ledger.
    """
    selected: dict[tuple[str, int], ScoredRow] = {}
    for row in rows:
        if (
            row.arm_id != PRODUCTION_ARM_ID
            or row.lock_estimand_id != PRODUCTION_LOCK_ESTIMAND_ID
            or row.vital != "hr"
        ):
            continue
        key = (row.capture_id, row.k)
        if key in selected:
            raise ScoreContractError(f"duplicate production audit key {key}")
        selected[key] = row

    expected_keys = {
        (capture_id, k)
        for capture_id, window_count in expected_capture_windows.items()
        for k in range(window_count)
    }
    if set(selected) != expected_keys:
        difference = sorted(set(selected) ^ expected_keys)
        raise ScoreContractError(
            "production audit rows are incomplete or contain extras: "
            f"{difference[:5]}"
        )

    ordered_rows = [selected[key] for key in sorted(selected)]
    for row in ordered_rows:
        expected_universe = DIAGNOSTIC_UNIVERSE if row.k == 0 else COMPARATIVE_UNIVERSE
        if row.window_universe != expected_universe:
            raise ScoreContractError(
                f"production audit key {(row.capture_id, row.k)} has "
                f"window_universe={row.window_universe!r}, expected {expected_universe!r}"
            )

    universe_rows = {
        PRODUCTION_ALL_WINDOWS_UNIVERSE: ordered_rows,
        PRODUCTION_PERSISTED_LOCK_UNIVERSE: [row for row in ordered_rows if row.k >= 1],
        PRODUCTION_K0_UNIVERSE: [row for row in ordered_rows if row.k == 0],
    }
    universes: dict[str, object] = {}
    for universe_name, subset in universe_rows.items():
        per_capture = []
        for capture_id in sorted(expected_capture_windows):
            capture_rows = [row for row in subset if row.capture_id == capture_id]
            per_capture.append(
                {
                    "capture_id": capture_id,
                    "metrics": coverage_and_metrics(capture_rows).to_dict(),
                }
            )
        micro = coverage_and_metrics(subset).to_dict()
        reconstructed_source = sum(
            int(record["metrics"]["n_source"]) for record in per_capture
        )
        reconstructed_reference = sum(
            int(record["metrics"]["n_reference_admitted"]) for record in per_capture
        )
        reconstructed_radar = sum(
            int(record["metrics"]["n_radar_valid"]) for record in per_capture
        )
        reconstructed_joint = sum(
            int(record["metrics"]["n_joint"]) for record in per_capture
        )
        reconstructed = {
            "n_source": reconstructed_source,
            "n_reference_admitted": reconstructed_reference,
            "n_radar_valid": reconstructed_radar,
            "n_joint": reconstructed_joint,
        }
        if any(micro[name] != value for name, value in reconstructed.items()):
            raise ScoreContractError(
                f"{universe_name}: per-capture denominators do not reconstruct micro totals"
            )
        universes[universe_name] = {
            "includes_k0": universe_name != PRODUCTION_PERSISTED_LOCK_UNIVERSE,
            "comparative_eligible": universe_name == PRODUCTION_PERSISTED_LOCK_UNIVERSE,
            "micro": micro,
            "capture_macro": _capture_macro_summary(per_capture),
            "per_capture": per_capture,
            "denominator_reconstruction": {
                "sum_of_per_capture_counts": reconstructed,
                "reconciles_to_micro": True,
            },
        }

    all_count = int(universes[PRODUCTION_ALL_WINDOWS_UNIVERSE]["micro"]["n_source"])
    persisted_count = int(
        universes[PRODUCTION_PERSISTED_LOCK_UNIVERSE]["micro"]["n_source"]
    )
    k0_count = int(universes[PRODUCTION_K0_UNIVERSE]["micro"]["n_source"])
    if all_count != persisted_count + k0_count:
        raise ScoreContractError("production k>=0 denominator does not partition into k0 + k>=1")

    return {
        "schema_version": 1,
        "estimator_id": "eca_ahet_v1",
        "arm_id": PRODUCTION_ARM_ID,
        "vital": "hr",
        "lock_estimand_id": PRODUCTION_LOCK_ESTIMAND_ID,
        "source_key": ["capture_id", "k"],
        "capture_window_counts": {
            capture_id: int(count)
            for capture_id, count in sorted(expected_capture_windows.items())
        },
        "universe_partition": {
            "all_complete_windows": all_count,
            "lock_selection_in_sample_k0": k0_count,
            "persisted_lock_windows_k_ge_1": persisted_count,
            "identity": "all_complete_windows = k0 + k_ge_1",
            "reconciles": True,
        },
        "universes": universes,
        "legacy_coverage_status": (
            "retired: the 30.08% survivor-biased production coverage is not a valid "
            "M4 scoring output"
        ),
    }


def _unique_by_key(rows: Sequence[ScoredRow], label: str) -> dict[tuple[str, str, int], ScoredRow]:
    result: dict[tuple[str, str, int], ScoredRow] = {}
    for row in rows:
        if row.key in result:
            raise ScoreContractError(f"duplicate {label} paired key {row.key}")
        result[row.key] = row
    return result


def paired_partitions(
    production_rows: Sequence[ScoredRow], ahmed_rows: Sequence[ScoredRow]
) -> dict:
    """Production/Ahmed validity partitions on identical reference-admitted keys."""
    production = _unique_by_key(production_rows, "production")
    ahmed = _unique_by_key(ahmed_rows, "Ahmed")
    if set(production) != set(ahmed):
        missing = sorted(set(production) ^ set(ahmed))
        raise ScoreContractError(f"production/Ahmed paired keys differ: {missing[:5]}")

    admitted: list[tuple[str, str, int]] = []
    partitions = {"both": [], "production_only": [], "ahmed_only": [], "neither": []}
    for key in sorted(production):
        prod = production[key]
        arm = ahmed[key]
        reference_identity = (
            prod.reference_admitted,
            prod.reference_reason,
            prod.reference_value,
            prod.vital,
            prod.window_universe,
        )
        arm_reference_identity = (
            arm.reference_admitted,
            arm.reference_reason,
            arm.reference_value,
            arm.vital,
            arm.window_universe,
        )
        if reference_identity != arm_reference_identity:
            raise ScoreContractError(f"paired rows use different reference identity at {key}")
        if not prod.reference_admitted:
            continue
        admitted.append(key)
        production_ok = prod.disposition == "joint"
        ahmed_ok = arm.disposition == "joint"
        name = (
            "both"
            if production_ok and ahmed_ok
            else "production_only"
            if production_ok
            else "ahmed_only"
            if ahmed_ok
            else "neither"
        )
        partitions[name].append(key)

    counts = {name: len(keys) for name, keys in partitions.items()}
    if sum(counts.values()) != len(admitted):
        raise ScoreContractError("validity partitions do not reconcile to admitted references")
    both = partitions["both"]
    prod_metrics = coverage_and_metrics([production[key] for key in both])
    ahmed_metrics = coverage_and_metrics([ahmed[key] for key in both])

    differences: dict[str, float | None] = {}
    for field in ("mae", "rmse", "bias"):
        prod_value = getattr(prod_metrics, field)
        arm_value = getattr(ahmed_metrics, field)
        differences[field] = (
            None if prod_value is None or arm_value is None else arm_value - prod_value
        )
    for point in ("p50", "p75", "p90", "p95"):
        prod_value = prod_metrics.error_percentiles[point]
        arm_value = ahmed_metrics.error_percentiles[point]
        differences[point] = (
            None if prod_value is None or arm_value is None else arm_value - prod_value
        )

    return {
        "n_reference_admitted": len(admitted),
        "partitions": counts,
        "n_intersection": len(both),
        "production_metrics_on_intersection": prod_metrics.to_dict(),
        "ahmed_metrics_on_intersection": ahmed_metrics.to_dict(),
        "descriptive_difference_ahmed_minus_production": differences,
        "comparison_note": (
            "descriptive only; both estimators are conditional on a production-selected "
            "range lock, so this does not validate Ahmed range selection"
        ),
    }


def _strict_json(path: Path, label: str) -> dict:
    try:
        value = json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON token {token}")
            ),
        )
    except Exception as exc:
        raise ScoreContractError(f"malformed {label}: {path}") from exc
    if not isinstance(value, dict):
        raise ScoreContractError(f"{label} must be a JSON mapping")
    return value


def _load_npz(path: Path) -> dict[str, np.ndarray]:
    try:
        with np.load(path, allow_pickle=False) as loaded:
            arrays = {key: np.array(loaded[key], copy=True) for key in loaded.files}
    except Exception as exc:
        raise ScoreContractError(f"malformed non-object NPZ payload: {path}") from exc
    if any(array.dtype == object for array in arrays.values()):
        raise ScoreContractError(f"object dtype is forbidden in {path}")
    return arrays


def _exact_bool(row: Mapping[str, object], key: str) -> bool:
    value = row.get(key)
    if type(value) is not bool:
        raise ScoreContractError(f"row field {key!r} must be an exact bool")
    return value


def _finite_or_none(row: Mapping[str, object], key: str, valid: bool) -> float | None:
    value = row.get(key)
    if valid:
        if type(value) not in (int, float) or not math.isfinite(float(value)):
            raise ScoreContractError(f"valid row field {key!r} must be finite")
        return float(value)
    if value is not None:
        raise ScoreContractError(f"invalid row field {key!r} must be null")
    return None


def verify_frozen_registry_agreement(registry_path: Path | None = None) -> None:
    """Fail closed when the scorer's frozen tables drift from the capture registry.

    ``EXPECTED_CAPTURE_WINDOWS`` and ``EXPECTED_LOCKS`` duplicate sixteen numbers that
    the registry already declares.  Deriving them at import time would make importing
    this downstream-only module read a file, so they are literals and this check ties
    them back to their source.  A deliberate registry correction then fails the scoring
    gate loudly instead of silently letting ``validate_radar_rows`` accept rows whose
    locks or window counts no longer match the registry the run was produced from.
    """
    captures = load_registry(registry_path).radar_scope().captures
    registry_windows = {
        capture_id: capture.windows for capture_id, capture in captures.items()
    }
    # Tuple order follows LOCK_ESTIMANDS:
    # (recorded_lock_as_captured, current_production_rerun_lock).
    registry_locks = {
        capture_id: (capture.recorded_lock, capture.rerun_lock)
        for capture_id, capture in captures.items()
    }
    if registry_windows != EXPECTED_CAPTURE_WINDOWS:
        raise ScoreContractError(
            f"frozen window counts {EXPECTED_CAPTURE_WINDOWS} disagree with the capture "
            f"registry {registry_windows}"
        )
    if registry_locks != EXPECTED_LOCKS:
        raise ScoreContractError(
            f"frozen locks {EXPECTED_LOCKS} disagree with the capture registry "
            f"{registry_locks}"
        )


def validate_radar_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    expected_capture_windows: Mapping[str, int] = EXPECTED_CAPTURE_WINDOWS,
) -> dict[str, object]:
    """Reject malformed, duplicate, missing, or unpaired canonical radar rows."""
    # The registry cross-check reads a YAML file, so it can fail for reasons other than
    # a scientific disagreement: a missing, renamed, or malformed registry.  Callers of
    # the scorer catch ``ScoreContractError``, so those failures are re-raised in that
    # declared type rather than surfacing as an unrelated OSError/ValueError.
    try:
        verify_frozen_registry_agreement()
    except ScoreContractError:
        raise
    except FileNotFoundError as exc:
        raise ScoreContractError(
            f"the capture registry the frozen scoring tables are checked against is "
            f"missing or renamed: {exc}"
        ) from exc
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ScoreContractError(
            f"the capture registry the frozen scoring tables are checked against could "
            f"not be read or parsed: {exc}"
        ) from exc
    expected_keys = {
        (capture_id, lock_id, k, arm_id)
        for capture_id, windows in expected_capture_windows.items()
        for lock_id in LOCK_ESTIMANDS
        for k in range(windows)
        for arm_id in CANONICAL_ARM_IDS
    }
    seen: dict[tuple[str, str, int, str], Mapping[str, object]] = {}
    shared_identity: dict[tuple[str, str, int], tuple[object, ...]] = {}
    source_cell_identity: dict[tuple[str, int], tuple[object, ...]] = {}
    run_identity: tuple[str, str, str] | None = None
    capture_cube_hash: dict[str, str] = {}
    capture_config_hash: dict[str, str] = {}
    suite_config_hash: dict[str, str] = {}
    arm_config_hash: dict[str, str] = {}

    for row in rows:
        if not isinstance(row, Mapping):
            raise ScoreContractError("radar row must be a mapping")
        required = {
            "schema_version", "run_id", "run_hash", "source_hash", "capture_id",
            "lock_estimand_id", "locked_bin", "k", "frame_start", "frame_stop",
            "epoch_start", "epoch_stop", "time_origin_id", "origin_is_approximate",
            "evaluation_status", "window_set", "window_origin_role", "suite_id",
            "suite_config_hash", "arm_id", "estimator_id", "arm_config_hash",
            "cube_hash", "window_cube_hash", "shared_signal_hash", "capture_config_hash",
            "hr_valid", "hr_raw", "br_valid", "br_bpm", "validity_reason",
            "hr_validity_reason", "br_validity_reason", "outcome",
        }
        missing = sorted(required - set(row))
        if missing:
            raise ScoreContractError(f"radar row is missing required fields {missing}")
        if type(row["schema_version"]) is not int or row["schema_version"] != 2:
            raise ScoreContractError("radar rows must use schema_version=2")
        capture_id = row["capture_id"]
        lock_id = row["lock_estimand_id"]
        arm_id = row["arm_id"]
        k = row["k"]
        if type(capture_id) is not str or capture_id not in expected_capture_windows:
            raise ScoreContractError(f"unknown capture_id {capture_id!r}")
        if type(lock_id) is not str or lock_id not in LOCK_ESTIMANDS:
            raise ScoreContractError(f"unknown lock_estimand_id {lock_id!r}")
        if type(arm_id) is not str or arm_id not in CANONICAL_ARM_IDS:
            raise ScoreContractError(f"unknown arm_id {arm_id!r}")
        if type(k) is not int or not 0 <= k < expected_capture_windows[capture_id]:
            raise ScoreContractError(f"invalid k={k!r} for {capture_id}")
        key = (capture_id, lock_id, k, arm_id)
        if key in seen:
            raise ScoreContractError(f"duplicate estimator row key {key}")
        seen[key] = row

        expected_locked_bin = EXPECTED_LOCKS[capture_id][LOCK_ESTIMANDS.index(lock_id)]
        if type(row["locked_bin"]) is not int or row["locked_bin"] != expected_locked_bin:
            raise ScoreContractError(f"{key}: numeric lock does not match frozen registry")
        if row["frame_start"] != k * 600 or row["frame_stop"] != (k + 1) * 600:
            raise ScoreContractError(f"{key}: frame span is not the frozen 600-frame grid")
        epoch_start = row["epoch_start"]
        epoch_stop = row["epoch_stop"]
        if type(epoch_start) is not float or type(epoch_stop) is not float:
            raise ScoreContractError(f"{key}: epoch bounds must be floats")
        if not math.isfinite(epoch_start) or not math.isfinite(epoch_stop):
            raise ScoreContractError(f"{key}: epoch bounds must be finite")
        if not math.isclose(epoch_stop - epoch_start, 30.0, rel_tol=0.0, abs_tol=1e-9):
            raise ScoreContractError(f"{key}: epoch span is not 30 seconds")
        expected_universe = DIAGNOSTIC_UNIVERSE if k == 0 else COMPARATIVE_UNIVERSE
        expected_role = "lock_selection_in_sample" if k == 0 else "evaluation"
        if row["window_set"] != expected_universe or row["window_origin_role"] != expected_role:
            raise ScoreContractError(f"{key}: k/window-universe labels disagree")
        if (
            row["time_origin_id"] != TIME_ORIGIN_ID
            or row["origin_is_approximate"] is not True
            or row["evaluation_status"] != EVALUATION_STATUS
        ):
            raise ScoreContractError(f"{key}: timing/evaluation labels are not frozen")

        hr_valid = _exact_bool(row, "hr_valid")
        br_valid = _exact_bool(row, "br_valid")
        _finite_or_none(row, "hr_raw", hr_valid)
        _finite_or_none(row, "br_bpm", br_valid)
        for valid, reason_key in (
            (hr_valid, "hr_validity_reason"),
            (br_valid, "br_validity_reason"),
        ):
            reason = row[reason_key]
            if type(reason) is not str or not reason:
                raise ScoreContractError(f"{key}: {reason_key} must be non-empty")
            if (reason == "ok") != valid:
                raise ScoreContractError(f"{key}: {reason_key} disagrees with validity")
        if row["validity_reason"] != row["hr_validity_reason"]:
            raise ScoreContractError(f"{key}: legacy validity_reason must equal HR reason")

        identity = (row["run_id"], row["run_hash"], row["source_hash"])
        if type(identity[0]) is not str or not identity[0]:
            raise ScoreContractError(f"{key}: malformed run/source identity")
        for digest_name, digest in (("run_hash", identity[1]), ("source_hash", identity[2])):
            if type(digest) is not str or len(digest) != 64 or any(
                char not in "0123456789abcdef" for char in digest
            ):
                raise ScoreContractError(f"{key}: {digest_name} is not a lowercase SHA-256")
        if run_identity is None:
            run_identity = identity
        elif identity != run_identity:
            raise ScoreContractError(f"{key}: mixed run/source identity")
        for hash_key in (
            "cube_hash", "window_cube_hash", "shared_signal_hash",
            "capture_config_hash", "suite_config_hash", "arm_config_hash",
        ):
            digest = row[hash_key]
            if type(digest) is not str or len(digest) != 64 or any(
                char not in "0123456789abcdef" for char in digest
            ):
                raise ScoreContractError(f"{key}: {hash_key} is not a lowercase SHA-256")
        previous_cube = capture_cube_hash.setdefault(capture_id, row["cube_hash"])
        if previous_cube != row["cube_hash"]:
            raise ScoreContractError(f"{capture_id}: cube hash changes within one run")
        previous_capture_config = capture_config_hash.setdefault(
            capture_id, row["capture_config_hash"]
        )
        if previous_capture_config != row["capture_config_hash"]:
            raise ScoreContractError(
                f"{capture_id}: capture configuration hash changes within one run"
            )
        previous_suite_config = suite_config_hash.setdefault(
            str(row["suite_id"]), row["suite_config_hash"]
        )
        if previous_suite_config != row["suite_config_hash"]:
            raise ScoreContractError(
                f"{row['suite_id']}: suite configuration hash changes within one run"
            )
        previous_arm_hash = arm_config_hash.setdefault(arm_id, row["arm_config_hash"])
        if previous_arm_hash != row["arm_config_hash"]:
            raise ScoreContractError(f"{arm_id}: arm configuration changes within one run")

        common = (
            row["locked_bin"], row["frame_start"], row["frame_stop"], epoch_start,
            epoch_stop, row["cube_hash"], row["window_cube_hash"],
            row["shared_signal_hash"], row["capture_config_hash"], *identity,
        )
        shared_key = (capture_id, lock_id, k)
        previous = shared_identity.setdefault(shared_key, common)
        if previous != common:
            raise ScoreContractError(f"{shared_key}: seven arms are not same-run/same-signal")
        source_common = (
            row["frame_start"], row["frame_stop"], epoch_start, epoch_stop,
            row["cube_hash"], row["window_cube_hash"], row["capture_config_hash"],
            *identity,
        )
        source_key = (capture_id, k)
        previous_source = source_cell_identity.setdefault(source_key, source_common)
        if previous_source != source_common:
            raise ScoreContractError(
                f"{source_key}: lock estimands do not share the same source cube/window/span"
            )

        expected_suite = (
            "production_eca_ahet_suite_v1"
            if arm_id == PRODUCTION_ARM_ID
            else "ahmed_fixed_h_phase_suite_v1"
        )
        expected_estimator = (
            "eca_ahet_v1" if arm_id == PRODUCTION_ARM_ID else "ahmed_fixed_h_phase_v1"
        )
        if row["suite_id"] != expected_suite or row["estimator_id"] != expected_estimator:
            raise ScoreContractError(f"{key}: suite/estimator identity is not canonical")

    if set(seen) != expected_keys:
        difference = sorted(set(seen) ^ expected_keys)
        raise ScoreContractError(
            f"radar Cartesian keys are incomplete or contain extras: {difference[:5]}"
        )
    if run_identity is None:
        raise ScoreContractError("radar artifact contains no rows")
    return {
        "run_id": run_identity[0],
        "run_hash": run_identity[1],
        "source_hash": run_identity[2],
        "source_span_count": sum(expected_capture_windows.values()),
        "shared_row_count": len(shared_identity),
        "estimator_row_count": len(seen),
    }


def _validate_evidence_keys(radar_dir: Path, expected_rows: Sequence[Mapping[str, object]]) -> None:
    expected_shared = {
        (row["capture_id"], row["lock_estimand_id"], row["k"])
        for row in expected_rows
    }
    expected_ahmed = {
        (row["capture_id"], row["lock_estimand_id"], row["k"], row["arm_id"])
        for row in expected_rows
        if row["arm_id"] != PRODUCTION_ARM_ID
    }
    shared = _load_npz(radar_dir / "shared_evidence.npz")
    ahmed = _load_npz(radar_dir / "ahmed_evidence.npz")
    for arrays, required in (
        (shared, ("capture_id", "lock_estimand_id", "k")),
        (ahmed, ("capture_id", "lock_estimand_id", "k", "arm_id")),
    ):
        if any(key not in arrays for key in required):
            raise ScoreContractError(f"evidence NPZ is missing key columns {required}")
        lengths = {len(arrays[key]) for key in required}
        if len(lengths) != 1:
            raise ScoreContractError("evidence NPZ key columns have different lengths")
        row_count = next(iter(lengths))
        if any(array.ndim == 0 or array.shape[0] != row_count for array in arrays.values()):
            raise ScoreContractError("evidence NPZ arrays do not share one row dimension")
    shared_keys = {
        (str(c), str(lock), int(k))
        for c, lock, k in zip(
            shared["capture_id"], shared["lock_estimand_id"], shared["k"]
        )
    }
    ahmed_keys = {
        (str(c), str(lock), int(k), str(arm))
        for c, lock, k, arm in zip(
            ahmed["capture_id"], ahmed["lock_estimand_id"], ahmed["k"], ahmed["arm_id"]
        )
    }
    if len(shared_keys) != len(shared["k"]) or shared_keys != expected_shared:
        raise ScoreContractError("shared evidence keys are duplicate, missing, or extra")
    if len(ahmed_keys) != len(ahmed["k"]) or ahmed_keys != expected_ahmed:
        raise ScoreContractError("Ahmed evidence keys are duplicate, missing, or extra")

    rows_by_shared = {
        (row["capture_id"], row["lock_estimand_id"], row["k"]): row
        for row in expected_rows
    }
    for index, key in enumerate(
        zip(shared["capture_id"], shared["lock_estimand_id"], shared["k"])
    ):
        normalized = (str(key[0]), str(key[1]), int(key[2]))
        row = rows_by_shared[normalized]
        for array_name, row_name in (
            ("cube_hash", "cube_hash"), ("window_cube_hash", "window_cube_hash"),
            ("signal_hash", "shared_signal_hash"), ("config_hash", "capture_config_hash"),
            ("source_hash", "source_hash"), ("run_hash", "run_hash"),
        ):
            if str(shared[array_name][index]) != row[row_name]:
                raise ScoreContractError(f"shared evidence {array_name} disagrees with rows")

    rows_by_estimator = {
        (row["capture_id"], row["lock_estimand_id"], row["k"], row["arm_id"]): row
        for row in expected_rows
    }
    for index, key in enumerate(
        zip(ahmed["capture_id"], ahmed["lock_estimand_id"], ahmed["k"], ahmed["arm_id"])
    ):
        normalized = (str(key[0]), str(key[1]), int(key[2]), str(key[3]))
        row = rows_by_estimator[normalized]
        for array_name, row_name in (
            ("cube_hash", "cube_hash"), ("signal_hash", "shared_signal_hash"),
            ("config_hash", "arm_config_hash"), ("source_hash", "source_hash"),
            ("run_hash", "run_hash"),
        ):
            if str(ahmed[array_name][index]) != row[row_name]:
                raise ScoreContractError(f"Ahmed evidence {array_name} disagrees with rows")

    native_index = _strict_json(
        radar_dir / "production_native_index.json", "production native index"
    )
    native_arrays = _load_npz(radar_dir / "production_evidence.npz")
    native = deserialize_native_tree(native_index, native_arrays)
    if type(native) is not list or len(native) != len(expected_shared):
        raise ScoreContractError("production evidence cardinality is not one per shared cell")
    native_keys = set()
    for record in native:
        if not isinstance(record, Mapping):
            raise ScoreContractError("production native evidence row must be a mapping")
        key = (record.get("capture_id"), record.get("lock_estimand_id"), record.get("k"))
        if key in native_keys:
            raise ScoreContractError(f"duplicate production evidence key {key}")
        native_keys.add(key)
        row = rows_by_estimator[(*key, PRODUCTION_ARM_ID)]
        for native_name, row_name in (
            ("cube_hash", "cube_hash"), ("signal_hash", "shared_signal_hash"),
            ("config_hash", "arm_config_hash"), ("source_hash", "source_hash"),
            ("run_hash", "run_hash"),
        ):
            if record.get(native_name) != row[row_name]:
                raise ScoreContractError(
                    f"production evidence {native_name} disagrees with rows"
                )
    if native_keys != expected_shared:
        raise ScoreContractError("production evidence keys differ from shared rows")


def validate_radar_parent(
    radar_dir: Path,
    *,
    expected_gate_manifest_sha256: str,
    expected_source_manifest_sha256: str,
    expected_authorization_id: str,
    expected_authorization_sha256: str,
) -> tuple[dict, str, list[dict]]:
    """Validate the exact complete M3 parent without touching any reference path."""
    radar_dir = Path(radar_dir)
    try:
        manifest = verify_bundle(radar_dir)
        _manifest, manifest_sha256 = read_manifest(radar_dir)
    except Exception as exc:
        raise ScoreContractError("radar parent bundle failed hash/schema verification") from exc
    if manifest.get("stage") != "radar" or manifest.get("status") != "complete":
        raise ScoreContractError("score parent must be one completed radar stage")
    if manifest.get("promotion_eligible") is not True:
        raise ScoreContractError("radar parent is not promotion-eligible")
    if set(manifest.get("payloads", {})) != EXPECTED_RADAR_PAYLOADS:
        raise ScoreContractError("radar parent payload allowlist is incomplete or substituted")
    if (manifest.get("parents") or {}).get("synthetic") != expected_gate_manifest_sha256:
        raise ScoreContractError("radar parent has the wrong synthetic ancestor")
    exact_manifest_values = {
        "source_span_count": 128,
        "shared_row_count": 256,
        "estimator_row_count": 1792,
        "ahmed_evidence_count": 1536,
        "production_evidence_count": 256,
        "score_status": "disabled_until_m4",
    }
    for key, expected in exact_manifest_values.items():
        if manifest.get(key) != expected:
            raise ScoreContractError(f"radar parent {key}={manifest.get(key)!r}, expected {expected!r}")

    provenance = _strict_json(radar_dir / "provenance.json", "radar provenance")
    if provenance.get("source_manifest_sha256") != expected_source_manifest_sha256:
        raise ScoreContractError("radar parent source identity differs from frozen source")
    if provenance.get("authorization_id") != expected_authorization_id:
        raise ScoreContractError("radar parent authorization ID differs from current chain")
    if provenance.get("authorization_sha256") != expected_authorization_sha256:
        raise ScoreContractError("radar parent authorization hash differs from current chain")

    resolved = yaml.safe_load((radar_dir / "resolved_config.yaml").read_text(encoding="utf-8"))
    if not isinstance(resolved, Mapping):
        raise ScoreContractError("radar resolved_config.yaml must be a mapping")
    if (
        tuple(resolved.get("capture_ids", ())) != tuple(sorted(EXPECTED_CAPTURE_WINDOWS))
        or tuple(resolved.get("lock_estimands", ())) != LOCK_ESTIMANDS
        or tuple(resolved.get("arm_ids", ())) != CANONICAL_ARM_IDS
        or resolved.get("score_status") != "disabled_until_m4"
    ):
        raise ScoreContractError("radar resolved configuration is not the canonical scope")

    rows_document = _strict_json(radar_dir / "rows.json", "radar rows")
    if set(rows_document) != {"schema_version", "rows"} or rows_document["schema_version"] != 2:
        raise ScoreContractError("radar rows.json has the wrong schema")
    rows = rows_document["rows"]
    if type(rows) is not list:
        raise ScoreContractError("radar rows must be a list")
    identity = validate_radar_rows(rows)
    if identity["source_hash"] != expected_source_manifest_sha256:
        raise ScoreContractError("radar rows source hash differs from frozen source")
    _validate_evidence_keys(radar_dir, rows)
    return manifest, manifest_sha256, rows


def _reference_reason(vital: Vital, result: Mapping[str, object]) -> str:
    if result.get("admitted") is True:
        return "ok"
    if vital == "hr":
        return "insufficient_usable" if result.get("coverage_ok") is False else "nonstationary"
    return "insufficient_available" if result.get("availability_ok") is False else "nonstationary"


def _json_finite(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_finite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_finite(item) for item in value]
    if isinstance(value, np.generic):
        return _json_finite(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _metric_flat(summary: Mapping[str, object]) -> dict[str, float]:
    values: dict[str, float] = {}
    for key in (
        "n_source", "n_reference_admitted", "n_radar_valid", "n_joint",
        "reference_coverage", "radar_coverage", "joint_coverage",
        "joint_given_reference", "mae", "rmse", "bias",
    ):
        value = summary.get(key)
        if value is not None:
            values[key] = float(value)
    for key, value in (summary.get("error_percentiles") or {}).items():
        if value is not None:
            values[f"error_percentiles.{key}"] = float(value)
    for marginal in ("exclusions", "reference_exclusions", "radar_exclusions"):
        for key, value in (summary.get(marginal) or {}).items():
            values[f"{marginal}.{key}"] = float(value)
    return values


def _capture_macro(per_capture_summaries: Sequence[Mapping[str, object]]) -> dict:
    flattened_summaries = [_metric_flat(summary) for summary in per_capture_summaries]
    marginal_count_names = {
        name
        for summary in flattened_summaries
        for name in summary
        if name.split(".", maxsplit=1)[0]
        in {"exclusions", "reference_exclusions", "radar_exclusions"}
    }
    gathered: dict[str, list[float]] = {}
    for summary in flattened_summaries:
        for name, value in summary.items():
            gathered.setdefault(name, []).append(value)
    # A reason absent from a capture means zero exclusions, not an undefined
    # quantity.  Include those zeroes so equal-capture macro means remain literal.
    for name in marginal_count_names:
        gathered[name] = [summary.get(name, 0.0) for summary in flattened_summaries]
    metric_names = sorted(set(gathered) | set(_SUMMARY_MACRO_METRICS))
    return {
        "metric_means": {
            name: float(np.mean(gathered[name])) if gathered.get(name) else None
            for name in metric_names
        },
        "contributing_capture_count_by_metric": {
            name: len(gathered.get(name, ())) for name in metric_names
        },
    }


def _flatten_numeric_metrics(
    value: Mapping[str, object], prefix: str = ""
) -> dict[str, float]:
    """Flatten defined numeric leaves for an equal-capture macro summary."""
    flattened: dict[str, float] = {}
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, Mapping):
            flattened.update(_flatten_numeric_metrics(item, name))
        elif type(item) in (int, float) and math.isfinite(float(item)):
            flattened[name] = float(item)
    return flattened


def _pairwise_capture_macro(per_capture_results: Sequence[Mapping[str, object]]) -> dict:
    """Arithmetic mean of each defined per-capture pairwise metric.

    Counts are kept separately for every metric because an empty both-valid
    intersection makes error metrics undefined for that capture.
    """
    flattened_results = [_flatten_numeric_metrics(result) for result in per_capture_results]
    marginal_count_names = {
        name
        for result in flattened_results
        for name in result
        if any(
            component in {"exclusions", "reference_exclusions", "radar_exclusions"}
            for component in name.split(".")
        )
    }
    gathered: dict[str, list[float]] = {}
    for result in flattened_results:
        for name, value in result.items():
            gathered.setdefault(name, []).append(value)
    for name in marginal_count_names:
        gathered[name] = [result.get(name, 0.0) for result in flattened_results]
    metric_names = sorted(set(gathered) | set(_PAIRWISE_MACRO_METRICS))
    return {
        "metric_means": {
            name: float(np.mean(gathered[name])) if gathered.get(name) else None
            for name in metric_names
        },
        "contributing_capture_count_by_metric": {
            name: len(gathered.get(name, ())) for name in metric_names
        },
    }


@dataclass(frozen=True)
class ScoreResult:
    scored_rows: tuple[dict, ...]
    metrics: Mapping[str, object]
    partitions: Mapping[str, object]
    production_audit: Mapping[str, object]
    reference_files: Mapping[str, Mapping[str, str]]


def _require_clean_repository_checkout(root: Path) -> str:
    """Return actual HEAD only when the whole checkout is clean.

    This function deliberately invokes Git itself.  A Boolean supplied by a caller is
    not evidence of repository state.
    """
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "-z"],
            cwd=str(root),
            check=True,
            capture_output=True,
            text=True,
        )
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ScoreContractError("cannot verify canonical production Git checkout") from exc
    if status.stdout:
        raise ScoreContractError(
            "canonical production scoring requires an actually clean whole Git tree"
        )
    if len(head) != 40 or any(character not in "0123456789abcdef" for character in head):
        raise ScoreContractError("canonical production Git HEAD is malformed")
    return head


def _verify_current_source_checkout(
    *,
    gate_dir: Path,
    source_manifest_sha256: str,
    repository_root: Path,
    git_head: str,
    entry_points: Sequence[str] | None = None,
    required_artifacts: Sequence[str] | None = None,
) -> SourceManifest:
    """Rebuild the checkout manifest and require exact gate/source/HEAD identity."""
    try:
        gate_source = load_source_manifest(Path(gate_dir) / "source_manifest.json")
        verify_source_manifest(
            gate_source,
            root=repository_root,
            entry_points=entry_points,
            required_artifacts=required_artifacts,
            require_promotion_eligible=True,
        )
        current_source = build_source_manifest(
            repository_root,
            entry_points=entry_points,
            required_artifacts=required_artifacts,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise ScoreContractError(
            "current scientific source checkout differs from the verified gate"
        ) from exc
    if (
        gate_source.manifest_sha256 != source_manifest_sha256
        or current_source.manifest_sha256 != source_manifest_sha256
    ):
        raise ScoreContractError(
            "rebuilt scientific source identity differs from the scoring chain"
        )
    if gate_source.git_commit != git_head or current_source.git_commit != git_head:
        raise ScoreContractError(
            "verified source manifest commit does not equal actual clean Git HEAD"
        )
    if not current_source.promotion_eligible:
        raise ScoreContractError("rebuilt scientific source manifest is not promotion-eligible")
    return current_source


def _derive_production_input_identity(
    source_manifest: SourceManifest,
    *,
    repository_root: Path,
    radar_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Derive raw/config identity internally from verified source and radar parents.

    The source manifest binds the exact registry file.  That
    registry supplies the raw-ADC and capture-configuration digests.  The independently
    validated radar parent supplies a second configuration binding.  No caller digest
    claim, ADC file, or reference file participates here.
    """
    registry_relative_path = "experiments/m8_ahmed_transfer/capture_registry.yaml"
    registry_entries = [
        entry
        for entry in source_manifest.entries
        if entry.get("path") == registry_relative_path
    ]
    if len(registry_entries) != 1:
        raise ScoreContractError(
            "gate source manifest must bind exactly one canonical capture registry"
        )
    registry_path = Path(repository_root) / registry_relative_path
    if registry_path.resolve() != DEFAULT_REGISTRY.resolve():
        raise ScoreContractError("canonical scoring repository has an unexpected registry path")
    if registry_entries[0].get("sha256") != sha256_path(registry_path):
        raise ScoreContractError(
            "current capture registry differs from the verified gate source manifest"
        )

    try:
        radar_scope = load_registry(registry_path).radar_scope()
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ScoreContractError("canonical capture registry is malformed") from exc
    expected_capture_ids = set(EXPECTED_CAPTURE_WINDOWS)
    if set(radar_scope.captures) != expected_capture_ids:
        raise ScoreContractError("canonical capture registry does not bind the exact capture set")

    authoritative_raw_digests = {
        capture_id: radar_scope.captures[capture_id].adc_stream_sha256
        for capture_id in sorted(expected_capture_ids)
    }
    authoritative_config_digests = {
        capture_id: radar_scope.captures[capture_id].capture_config_sha256
        for capture_id in sorted(expected_capture_ids)
    }
    parent_config_digests: dict[str, str] = {}
    for row in radar_rows:
        capture_id = str(row["capture_id"])
        digest = str(row["capture_config_hash"])
        previous = parent_config_digests.setdefault(capture_id, digest)
        if previous != digest:
            raise ScoreContractError(
                f"{capture_id}: radar-parent capture configuration hash is inconsistent"
            )
    if set(parent_config_digests) != expected_capture_ids:
        raise ScoreContractError(
            "radar parent configuration identity does not bind the exact capture set"
        )
    if parent_config_digests != authoritative_config_digests:
        raise ScoreContractError(
            "radar-parent configuration hashes differ from the source-manifest-bound registry"
        )
    return {
        "git_commit": source_manifest.git_commit,
        "git_tree_clean": True,
        "source_manifest_sha256": source_manifest.manifest_sha256,
        "raw_adc_sha256_by_capture": authoritative_raw_digests,
        "capture_config_sha256_by_capture": authoritative_config_digests,
    }


def execute_score(
    *,
    radar_rows: Sequence[Mapping[str, object]],
    reference: ReferenceScope,
    file_hash: Callable[[Path], str] = sha256_path,
    masimo_loader: Callable[[Path], object] = load_masimo,
) -> ScoreResult:
    """Hash/load Masimo and score rows after caller-completed parent preflight."""
    captures = sorted({str(row["capture_id"]) for row in radar_rows})
    if tuple(captures) != tuple(sorted(EXPECTED_CAPTURE_WINDOWS)):
        raise ScoreContractError("radar rows do not contain the exact eight reference captures")

    frames: dict[str, object] = {}
    reference_files: dict[str, dict[str, str]] = {}
    for capture_id in captures:
        path = reference.csv_path(capture_id)
        actual_hash = file_hash(path)
        expected_hash = reference.digest(capture_id)
        if actual_hash != expected_hash:
            raise ScoreContractError(
                f"{capture_id}: Masimo SHA-256 {actual_hash} != registry {expected_hash}"
            )
        frame = masimo_loader(path)
        if "epoch_utc" not in frame or not np.issubdtype(frame["epoch_utc"].dtype, np.integer):
            raise ScoreContractError(f"{capture_id}: parser did not preserve integer Timestamp")
        epochs = frame["epoch_utc"].to_numpy(dtype=np.int64)
        if len(np.unique(epochs)) != len(epochs):
            raise ScoreContractError(f"{capture_id}: parser left duplicate Timestamp rows")
        frames[capture_id] = frame
        reference_files[capture_id] = {
            "path": reference.masimo_csv[capture_id],
            "sha256": actual_hash,
        }

    # One reference window is resolved per (capture, lock, k) cell and reused for all
    # seven arms, so the first row's epoch span is authoritative for the whole cell.
    # ``run_score_stage`` guarantees the arms agree via ``validate_radar_rows``, but
    # ``execute_score`` is exported and called directly, so it checks the invariant it
    # relies on rather than scoring six arms against a seventh arm's window.
    common_rows: dict[tuple[str, str, int], Mapping[str, object]] = {}
    for row in radar_rows:
        key = (str(row["capture_id"]), str(row["lock_estimand_id"]), int(row["k"]))
        cell_row = common_rows.setdefault(key, row)
        if (row["epoch_start"], row["epoch_stop"]) != (
            cell_row["epoch_start"],
            cell_row["epoch_stop"],
        ):
            raise ScoreContractError(
                f"{key}: paired arms disagree on the scored window, "
                f"[{cell_row['epoch_start']}, {cell_row['epoch_stop']}) for "
                f"{cell_row['arm_id']!r} versus [{row['epoch_start']}, "
                f"{row['epoch_stop']}) for {row['arm_id']!r}"
            )
    references: dict[tuple[str, str, int, Vital], dict] = {}
    for key, row in sorted(common_rows.items()):
        capture_id, _lock, _k = key
        epoch_start = float(row["epoch_start"])
        epoch_stop = float(row["epoch_stop"])
        hr = hr_reference(frames[capture_id], epoch_start, epoch_stop)
        br = br_reference(frames[capture_id], epoch_start, epoch_stop)
        references[(*key, "hr")] = {
            "value": float(hr["median_pr_bpm"]) if hr["admitted"] else None,
            "admitted": bool(hr["admitted"]),
            "reason": _reference_reason("hr", hr),
            "diagnostics": _json_finite(hr),
        }
        references[(*key, "br")] = {
            "value": float(br["median_rr_bpm"]) if br["admitted"] else None,
            "admitted": bool(br["admitted"]),
            "reason": _reference_reason("br", br),
            "diagnostics": _json_finite(br),
        }

    scored_objects: list[ScoredRow] = []
    scored_payload: list[dict] = []
    for row in radar_rows:
        capture_id = str(row["capture_id"])
        lock_id = str(row["lock_estimand_id"])
        k = int(row["k"])
        universe = DIAGNOSTIC_UNIVERSE if k == 0 else COMPARATIVE_UNIVERSE
        for vital, valid_key, value_key, reason_key in (
            ("hr", "hr_valid", "hr_raw", "hr_validity_reason"),
            ("br", "br_valid", "br_bpm", "br_validity_reason"),
        ):
            if type(row[valid_key]) is not bool:
                raise ScoreContractError(f"{valid_key} must be an exact bool")
            reference_result = references[(capture_id, lock_id, k, vital)]
            scored = ScoredRow(
                capture_id=capture_id,
                lock_estimand_id=lock_id,
                k=k,
                arm_id=str(row["arm_id"]),
                radar_value=None if row[value_key] is None else float(row[value_key]),
                radar_valid=bool(row[valid_key]),
                radar_reason=str(row[reason_key]),
                reference_value=reference_result["value"],
                reference_admitted=reference_result["admitted"],
                reference_reason=reference_result["reason"],
                vital=vital,
                window_universe=universe,
                protocol=reference.protocol[capture_id],
                protocol_stratum=reference.stratum_of(capture_id),
            )
            scored_objects.append(scored)
            error = (
                float(scored.radar_value - scored.reference_value)
                if scored.disposition == "joint"
                else None
            )
            scored_payload.append(
                {
                    "schema_version": 1,
                    "run_id": row["run_id"],
                    "run_hash": row["run_hash"],
                    "source_hash": row["source_hash"],
                    "capture_id": capture_id,
                    "lock_estimand_id": lock_id,
                    "locked_bin": row["locked_bin"],
                    "k": k,
                    "frame_start": row["frame_start"],
                    "frame_stop": row["frame_stop"],
                    "epoch_start": row["epoch_start"],
                    "epoch_stop": row["epoch_stop"],
                    "arm_id": row["arm_id"],
                    "vital": vital,
                    "window_universe": universe,
                    "window_origin_role": row["window_origin_role"],
                    "protocol": reference.protocol[capture_id],
                    "protocol_stratum": reference.stratum_of(capture_id),
                    "data_role": DATA_ROLE,
                    "radar_valid": scored.radar_valid,
                    "radar_validity_reason": scored.radar_reason,
                    "radar_value_bpm": scored.radar_value,
                    "reference_admitted": scored.reference_admitted,
                    "reference_reason": scored.reference_reason,
                    "reference_value_bpm": scored.reference_value,
                    "reference_diagnostics": reference_result["diagnostics"],
                    "disposition": scored.disposition,
                    "error_radar_minus_reference_bpm": error,
                    "absolute_error_bpm": None if error is None else abs(error),
                    "evaluation_status": EVALUATION_STATUS,
                    "time_origin_id": TIME_ORIGIN_ID,
                    "origin_is_approximate": True,
                    "claim_status": CLAIM_STATUS,
                }
            )

    metric_groups: list[dict] = []
    per_capture_lookup: dict[tuple[str, str, str, str, str], dict] = {}
    for capture_id in captures:
        for lock_id in LOCK_ESTIMANDS:
            for universe in (DIAGNOSTIC_UNIVERSE, COMPARATIVE_UNIVERSE):
                for arm_id in CANONICAL_ARM_IDS:
                    for vital in ("hr", "br"):
                        group = [
                            row for row in scored_objects
                            if row.capture_id == capture_id
                            and row.lock_estimand_id == lock_id
                            and row.window_universe == universe
                            and row.arm_id == arm_id
                            and row.vital == vital
                        ]
                        summary = coverage_and_metrics(group).to_dict()
                        record = {
                            "summary_kind": "per_capture_primary",
                            "capture_id": capture_id,
                            "protocol": reference.protocol[capture_id],
                            "protocol_stratum": reference.stratum_of(capture_id),
                            "lock_estimand_id": lock_id,
                            "window_universe": universe,
                            "comparative_eligible": universe == COMPARATIVE_UNIVERSE,
                            "arm_id": arm_id,
                            "vital": vital,
                            "metrics": summary,
                        }
                        metric_groups.append(record)
                        per_capture_lookup[(capture_id, lock_id, universe, arm_id, vital)] = summary

    protocol_summaries: list[dict] = []
    for stratum in sorted(reference.strata):
        member_captures = tuple(reference.strata[stratum])
        for lock_id in LOCK_ESTIMANDS:
            for universe in (DIAGNOSTIC_UNIVERSE, COMPARATIVE_UNIVERSE):
                for arm_id in CANONICAL_ARM_IDS:
                    for vital in ("hr", "br"):
                        micro_rows = [
                            row for row in scored_objects
                            if row.capture_id in member_captures
                            and row.lock_estimand_id == lock_id
                            and row.window_universe == universe
                            and row.arm_id == arm_id
                            and row.vital == vital
                        ]
                        base = {
                            "protocol_stratum": stratum,
                            "capture_ids": list(member_captures),
                            "lock_estimand_id": lock_id,
                            "window_universe": universe,
                            "comparative_eligible": universe == COMPARATIVE_UNIVERSE,
                            "arm_id": arm_id,
                            "vital": vital,
                            "single_subject_descriptive_only": True,
                        }
                        protocol_summaries.append(
                            {
                                **base,
                                "summary_kind": "protocol_pooled_window_micro",
                                "metrics": coverage_and_metrics(micro_rows).to_dict(),
                            }
                        )
                        capture_metrics = [
                            per_capture_lookup[(capture_id, lock_id, universe, arm_id, vital)]
                            for capture_id in member_captures
                        ]
                        protocol_summaries.append(
                            {
                                **base,
                                "summary_kind": "protocol_capture_macro",
                                **_capture_macro(capture_metrics),
                            }
                        )

    partition_groups: list[dict] = []
    for capture_id in captures:
        for lock_id in LOCK_ESTIMANDS:
            for universe in (DIAGNOSTIC_UNIVERSE, COMPARATIVE_UNIVERSE):
                for vital in ("hr", "br"):
                    production_rows = [
                        row for row in scored_objects
                        if row.capture_id == capture_id
                        and row.lock_estimand_id == lock_id
                        and row.window_universe == universe
                        and row.vital == vital
                        and row.arm_id == PRODUCTION_ARM_ID
                    ]
                    for arm_id in CANONICAL_ARM_IDS[1:]:
                        ahmed_rows = [
                            row for row in scored_objects
                            if row.capture_id == capture_id
                            and row.lock_estimand_id == lock_id
                            and row.window_universe == universe
                            and row.vital == vital
                            and row.arm_id == arm_id
                        ]
                        partition_groups.append(
                            {
                                "summary_kind": "per_capture_primary",
                                "capture_id": capture_id,
                                "protocol": reference.protocol[capture_id],
                                "protocol_stratum": reference.stratum_of(capture_id),
                                "lock_estimand_id": lock_id,
                                "window_universe": universe,
                                "comparative_eligible": universe == COMPARATIVE_UNIVERSE,
                                "vital": vital,
                                "production_arm_id": PRODUCTION_ARM_ID,
                                "ahmed_arm_id": arm_id,
                                **paired_partitions(production_rows, ahmed_rows),
                            }
                        )

    pairwise_by_capture = {
        (
            row["capture_id"], row["lock_estimand_id"], row["window_universe"],
            row["vital"], row["ahmed_arm_id"],
        ): row
        for row in partition_groups
    }
    protocol_partition_summaries: list[dict] = []
    for stratum in sorted(reference.strata):
        member_captures = tuple(reference.strata[stratum])
        for lock_id in LOCK_ESTIMANDS:
            for universe in (DIAGNOSTIC_UNIVERSE, COMPARATIVE_UNIVERSE):
                for vital in ("hr", "br"):
                    production_rows = [
                        row for row in scored_objects
                        if row.capture_id in member_captures
                        and row.lock_estimand_id == lock_id
                        and row.window_universe == universe
                        and row.vital == vital
                        and row.arm_id == PRODUCTION_ARM_ID
                    ]
                    for arm_id in CANONICAL_ARM_IDS[1:]:
                        ahmed_rows = [
                            row for row in scored_objects
                            if row.capture_id in member_captures
                            and row.lock_estimand_id == lock_id
                            and row.window_universe == universe
                            and row.vital == vital
                            and row.arm_id == arm_id
                        ]
                        base = {
                            "protocol_stratum": stratum,
                            "capture_ids": list(member_captures),
                            "lock_estimand_id": lock_id,
                            "window_universe": universe,
                            "comparative_eligible": universe == COMPARATIVE_UNIVERSE,
                            "vital": vital,
                            "production_arm_id": PRODUCTION_ARM_ID,
                            "ahmed_arm_id": arm_id,
                            "single_subject_descriptive_only": True,
                        }
                        protocol_partition_summaries.append(
                            {
                                **base,
                                "summary_kind": "protocol_pooled_window_micro",
                                **paired_partitions(production_rows, ahmed_rows),
                            }
                        )
                        per_capture_results = [
                            pairwise_by_capture[
                                (capture_id, lock_id, universe, vital, arm_id)
                            ]
                            for capture_id in member_captures
                        ]
                        protocol_partition_summaries.append(
                            {
                                **base,
                                "summary_kind": "protocol_capture_macro",
                                **_pairwise_capture_macro(per_capture_results),
                            }
                        )

    common_labels = {
        "evaluation_status": EVALUATION_STATUS,
        "time_origin_id": TIME_ORIGIN_ID,
        "origin_is_approximate": True,
        "origin_uncertainty_seconds_range": list(ORIGIN_UNCERTAINTY_SECONDS_RANGE),
        "claim_status": CLAIM_STATUS,
        "data_role": DATA_ROLE,
        "population_limitation": "single_subject_development_captures_no_population_claim",
        "comparative_universe": COMPARATIVE_UNIVERSE,
        "k0_status": "in_sample_diagnostic_only",
        "no_all_protocol_pooling": True,
        "no_arm_ranking": True,
    }
    expected_scored_counts = {
        ("hr", DIAGNOSTIC_UNIVERSE): 112,
        ("br", DIAGNOSTIC_UNIVERSE): 112,
        ("hr", COMPARATIVE_UNIVERSE): 1680,
        ("br", COMPARATIVE_UNIVERSE): 1680,
    }
    observed_scored_counts = {
        key: sum(
            row["vital"] == key[0] and row["window_universe"] == key[1]
            for row in scored_payload
        )
        for key in expected_scored_counts
    }
    if observed_scored_counts != expected_scored_counts:
        raise ScoreContractError(
            f"scored universe cardinalities {observed_scored_counts} do not match "
            f"the canonical contract {expected_scored_counts}"
        )
    common_labels["cardinalities"] = {
        "full_k0_shared_cells": 16,
        "full_k0_estimator_rows_per_vital": 112,
        "evaluation_k_ge_1_shared_cells": 240,
        "evaluation_k_ge_1_estimator_rows_per_vital": 1680,
        "total_scored_rows_both_vitals": 3584,
    }
    metrics = {
        "schema_version": 1,
        **common_labels,
        "per_capture": metric_groups,
        "protocol_summaries": protocol_summaries,
    }
    partitions = {
        "schema_version": 1,
        **common_labels,
        "production_vs_ahmed": partition_groups,
        "protocol_summaries": protocol_partition_summaries,
    }
    production_audit = production_audit_summary(scored_objects)
    production_estimate_rows = [
        {
            "capture_id": row["capture_id"],
            "k": row["k"],
            "radar_valid": row["radar_valid"],
            "radar_validity_reason": row["radar_validity_reason"],
            "radar_value_bpm": row["radar_value_bpm"],
        }
        for row in scored_payload
        if row["arm_id"] == PRODUCTION_ARM_ID
        and row["lock_estimand_id"] == PRODUCTION_LOCK_ESTIMAND_ID
        and row["vital"] == "hr"
    ]
    production_estimate_rows.sort(key=lambda row: (row["capture_id"], row["k"]))
    production_audit = {
        **production_audit,
        "radar_estimate_identity_sha256": sha256_bytes(
            strict_json_bytes({"rows": production_estimate_rows})
        ),
        "radar_estimate_identity_row_count": len(production_estimate_rows),
        "radar_estimate_identity_fields": [
            "capture_id",
            "k",
            "radar_valid",
            "radar_validity_reason",
            "radar_value_bpm",
        ],
    }
    return ScoreResult(
        scored_rows=tuple(scored_payload),
        metrics=metrics,
        partitions=partitions,
        production_audit=production_audit,
        reference_files=reference_files,
    )


def persist_score_artifacts(
    result: ScoreResult,
    *,
    out_root: Path,
    run_id: str,
    source_manifest_sha256: str,
    authorization: Authorization,
    authorization_sha256: str,
    gate_manifest_sha256: str,
    radar_manifest_sha256: str,
    production_input_identity: Mapping[str, object] | None = None,
) -> StageBundle:
    """Write a legacy/non-canonical immutable, parent-linked scored bundle.

    Canonical identity is derived internally by :func:`run_score_stage` from the actual
    checkout, gate-bound registry and radar parent.  Accepting an ordinary digest mapping
    here would let a direct API caller self-attest fabricated hashes.
    """
    if production_input_identity is not None:
        raise ScoreContractError(
            "unverified production input identity cannot be persisted; "
            "use run_score_stage for canonical scoring"
        )
    reference_identity_sha256 = sha256_bytes(strict_json_bytes(result.reference_files))
    common = {
        "schema_version": 1,
        "evaluation_status": EVALUATION_STATUS,
        "time_origin_id": TIME_ORIGIN_ID,
        "origin_is_approximate": True,
        "origin_uncertainty_seconds_range": list(ORIGIN_UNCERTAINTY_SECONDS_RANGE),
        "claim_status": CLAIM_STATUS,
        "data_role": DATA_ROLE,
        "population_limitation": "single_subject_development_captures_no_population_claim",
        "source_manifest_sha256": source_manifest_sha256,
        "authorization_id": authorization.authorization_id,
        "authorization_sha256": authorization_sha256,
        "radar_parent_manifest_sha256": radar_manifest_sha256,
        "reference_identity_sha256": reference_identity_sha256,
        "reference_files": dict(result.reference_files),
    }
    production_audit = {
        **dict(result.production_audit),
        "canonical_provenance_status": "legacy_api_no_m1_clean_tree_attestation",
        "input_identity": None,
    }
    writer = BundleWriter(stage_root=out_root, stage="scored", run_id=run_id)
    writer.add_json("rows.json", {**common, "rows": list(result.scored_rows)})
    writer.add_json("metrics.json", {**common, **dict(result.metrics)})
    writer.add_json("partitions.json", {**common, **dict(result.partitions)})
    writer.add_json(
        "production_summary.json",
        {**common, "production_audit": production_audit},
    )
    writer.add_text(
        "resolved_config.yaml",
        yaml.safe_dump(
            {
                **common,
                "experiment_id": "m8_canonical_two_lock_seven_arm_score_v1",
                "lock_estimands": list(LOCK_ESTIMANDS),
                "arm_ids": list(CANONICAL_ARM_IDS),
                "window_universes": [DIAGNOSTIC_UNIVERSE, COMPARATIVE_UNIVERSE],
                "comparative_universe": COMPARATIVE_UNIVERSE,
                "score_status": "canonical_scoring_complete",
            },
            sort_keys=True,
        ),
    )
    return writer.finalize(
        status="complete",
        # The source/authorization chain is verified, but approximate timing makes the
        # reference-derived result scientifically ineligible for promotion/final agreement.
        promotion_eligible=False,
        provenance=common,
        parents={"synthetic": gate_manifest_sha256, "radar": radar_manifest_sha256},
        extra_manifest={
            "evaluation_status": EVALUATION_STATUS,
            "time_origin_id": TIME_ORIGIN_ID,
            "origin_is_approximate": True,
            "origin_uncertainty_seconds_range": list(ORIGIN_UNCERTAINTY_SECONDS_RANGE),
            "claim_status": CLAIM_STATUS,
            "source_chain_verified": True,
            "data_role": DATA_ROLE,
            "source_manifest_sha256": source_manifest_sha256,
            "authorization_sha256": authorization_sha256,
            "radar_parent_manifest_sha256": radar_manifest_sha256,
            "reference_identity_sha256": reference_identity_sha256,
            "reference_sha256_by_capture": {
                capture_id: record["sha256"]
                for capture_id, record in sorted(result.reference_files.items())
            },
            "production_provenance_status": production_audit[
                "canonical_provenance_status"
            ],
            "scored_row_count": len(result.scored_rows),
            "score_status": "canonical_scoring_complete",
        },
    )


def run_score_stage(
    *,
    gate_dir: Path,
    authorization_path: Path,
    source_manifest_sha256: str,
    radar_dir: Path,
    reference: ReferenceScope,
    run_id: str,
    out_root: Path | None = None,
    authorization_validator: Callable[[Path, Authorization], None] | None = None,
    require_scientific_gate: bool = False,
    file_hash: Callable[[Path], str] = sha256_path,
    masimo_loader: Callable[[Path], object] = load_masimo,
    production_input_identity: Mapping[str, object] | None = None,
    require_production_provenance: bool = False,
) -> ScoreResult | StageBundle:
    """Fail-closed official scoring entry point; reference access is last in preflight."""
    if production_input_identity is not None:
        raise ScoreContractError(
            "caller-supplied production identity is forbidden; canonical scoring "
            "derives repository/raw/config identity internally"
        )
    if require_production_provenance and not require_scientific_gate:
        raise ScoreContractError(
            "canonical production scoring requires the complete scientific gate"
        )

    # Canonical execution proves the real process checkout before trusting any parent.
    # Compute the root from this file each time so a caller cannot nominate an unrelated
    # clean repository as its authority.
    repository_root = Path(__file__).resolve().parents[2]
    canonical_git_head = None
    if require_production_provenance:
        canonical_git_head = _require_clean_repository_checkout(repository_root)

    preflight = verify_preflight(
        stage="score",
        gate_dir=gate_dir,
        authorization_path=authorization_path,
        source_manifest_sha256=source_manifest_sha256,
        parent_dir=radar_dir,
        authorization_validator=authorization_validator,
        require_scientific_gate=require_scientific_gate,
    )
    authorization = preflight.authorization
    if authorization.lock_estimands != LOCK_ESTIMANDS:
        raise PreflightError("score authorization does not bind both canonical locks")
    if authorization.arm_ids != CANONICAL_ARM_IDS:
        raise PreflightError("score authorization does not bind all seven canonical arms")
    if tuple(sorted(authorization.capture_ids)) != tuple(sorted(EXPECTED_CAPTURE_WINDOWS)):
        raise PreflightError("score authorization does not bind all eight captures")
    authorization_sha256 = sha256_path(authorization_path)
    _manifest, radar_manifest_sha256, radar_rows = validate_radar_parent(
        radar_dir,
        expected_gate_manifest_sha256=preflight.gate_manifest_sha256,
        expected_source_manifest_sha256=source_manifest_sha256,
        expected_authorization_id=authorization.authorization_id,
        expected_authorization_sha256=authorization_sha256,
    )

    canonical_input_identity = None
    if require_production_provenance:
        assert canonical_git_head is not None
        current_source = _verify_current_source_checkout(
            gate_dir=gate_dir,
            source_manifest_sha256=source_manifest_sha256,
            repository_root=repository_root,
            git_head=canonical_git_head,
        )
        canonical_input_identity = _derive_production_input_identity(
            current_source,
            repository_root=repository_root,
            radar_rows=radar_rows,
        )

    # This is intentionally the first point at which a reference path may be built,
    # hashed, or opened. Every gate/authorization/parent/schema check above is complete.
    result = execute_score(
        radar_rows=radar_rows,
        reference=reference,
        file_hash=file_hash,
        masimo_loader=masimo_loader,
    )
    if out_root is None:
        return result
    if not require_production_provenance:
        return persist_score_artifacts(
            result,
            out_root=out_root,
            run_id=run_id,
            source_manifest_sha256=source_manifest_sha256,
            authorization=authorization,
            authorization_sha256=authorization_sha256,
            gate_manifest_sha256=preflight.gate_manifest_sha256,
            radar_manifest_sha256=radar_manifest_sha256,
        )

    # Canonical authority is lexical: there is no module-level token or canonical writer
    # for a direct caller to construct or invoke.  This branch is reachable only after
    # the real checkout, rebuilt source manifest, registry and radar parent all agree.
    assert canonical_input_identity is not None
    reference_identity_sha256 = sha256_bytes(strict_json_bytes(result.reference_files))
    common = {
        "schema_version": 1,
        "evaluation_status": EVALUATION_STATUS,
        "time_origin_id": TIME_ORIGIN_ID,
        "origin_is_approximate": True,
        "origin_uncertainty_seconds_range": list(ORIGIN_UNCERTAINTY_SECONDS_RANGE),
        "claim_status": CLAIM_STATUS,
        "data_role": DATA_ROLE,
        "population_limitation": "single_subject_development_captures_no_population_claim",
        "source_manifest_sha256": source_manifest_sha256,
        "authorization_id": authorization.authorization_id,
        "authorization_sha256": authorization_sha256,
        "radar_parent_manifest_sha256": radar_manifest_sha256,
        "reference_identity_sha256": reference_identity_sha256,
        "reference_files": dict(result.reference_files),
    }
    production_audit = {
        **dict(result.production_audit),
        "canonical_provenance_status": "complete_clean_tree_hash_bound",
        "input_identity": canonical_input_identity,
    }
    writer = BundleWriter(stage_root=out_root, stage="scored", run_id=run_id)
    writer.add_json("rows.json", {**common, "rows": list(result.scored_rows)})
    writer.add_json("metrics.json", {**common, **dict(result.metrics)})
    writer.add_json("partitions.json", {**common, **dict(result.partitions)})
    writer.add_json(
        "production_summary.json",
        {**common, "production_audit": production_audit},
    )
    writer.add_text(
        "resolved_config.yaml",
        yaml.safe_dump(
            {
                **common,
                "experiment_id": "m8_canonical_two_lock_seven_arm_score_v1",
                "lock_estimands": list(LOCK_ESTIMANDS),
                "arm_ids": list(CANONICAL_ARM_IDS),
                "window_universes": [DIAGNOSTIC_UNIVERSE, COMPARATIVE_UNIVERSE],
                "comparative_universe": COMPARATIVE_UNIVERSE,
                "score_status": "canonical_scoring_complete",
            },
            sort_keys=True,
        ),
    )
    return writer.finalize(
        status="complete",
        promotion_eligible=False,
        provenance=common,
        parents={
            "synthetic": preflight.gate_manifest_sha256,
            "radar": radar_manifest_sha256,
        },
        extra_manifest={
            "evaluation_status": EVALUATION_STATUS,
            "time_origin_id": TIME_ORIGIN_ID,
            "origin_is_approximate": True,
            "origin_uncertainty_seconds_range": list(ORIGIN_UNCERTAINTY_SECONDS_RANGE),
            "claim_status": CLAIM_STATUS,
            "source_chain_verified": True,
            "data_role": DATA_ROLE,
            "source_manifest_sha256": source_manifest_sha256,
            "authorization_sha256": authorization_sha256,
            "radar_parent_manifest_sha256": radar_manifest_sha256,
            "reference_identity_sha256": reference_identity_sha256,
            "reference_sha256_by_capture": {
                capture_id: record["sha256"]
                for capture_id, record in sorted(result.reference_files.items())
            },
            "production_provenance_status": "complete_clean_tree_hash_bound",
            "scored_row_count": len(result.scored_rows),
            "score_status": "canonical_scoring_complete",
        },
    )
