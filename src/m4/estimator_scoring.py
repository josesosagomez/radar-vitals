"""Estimator-neutral scoring and metrics (plan section 3.5).

This module computes agreement metrics from **already-persisted** radar rows. It never
decodes ADC, never invokes an estimator, and is handed only the registry's reference scope,
so it structurally cannot reach a raw capture path.

Reporting rules that are easy to get wrong and are enforced here:

* dispositions are mutually exclusive with **reference precedence first** — a row excluded
  by the reference is never also counted as a radar exclusion;
* every error metric uses `joint` rows only;
* the four production-versus-arm validity partitions must sum exactly to
  `n_reference_admitted`, which is asserted rather than assumed;
* percentiles use `method="linear"` explicitly, so a NumPy default change cannot silently
  move a published number;
* an empty intersection yields `n=0` and `null` metrics — not a crash, and not a
  silently-dropped comparison.

Nothing here pools across protocol strata. Base plan section 3.5 forbids an all-capture
error headline for single-subject development data, so the caller supplies one stratum at
a time.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, Sequence

import numpy as np

__all__ = [
    "ROW_DISPOSITIONS",
    "MetricSummary",
    "ScoredRow",
    "coverage_and_metrics",
    "paired_partitions",
    "percentiles",
]

#: Mutually exclusive per-row disposition, in precedence order.
ROW_DISPOSITIONS = (
    "reference_insufficient",
    "reference_nonstationary",
    "radar_invalid",
    "joint",
)

Vital = Literal["hr", "br"]


@dataclass(frozen=True)
class ScoredRow:
    """One (capture, lock estimand, k, arm) row after reference resolution."""

    capture_id: str
    lock_estimand_id: str
    k: int
    arm_id: str
    radar_value: float | None
    radar_valid: bool
    reference_value: float | None
    reference_admitted: bool
    reference_reason: str = ""

    @property
    def disposition(self) -> str:
        """Reference precedence first: a reference exclusion outranks a radar one."""
        if not self.reference_admitted:
            if self.reference_reason.startswith("insufficient"):
                return "reference_insufficient"
            return "reference_nonstationary"
        if not self.radar_valid or self.radar_value is None:
            return "radar_invalid"
        return "joint"


def percentiles(values: Sequence[float], points: Sequence[int] = (50, 75, 90, 95)) -> dict:
    """Absolute-error percentiles with an explicit method, never the NumPy default."""
    if not len(values):
        return {f"p{p}": None for p in points}
    array = np.asarray(values, dtype=np.float64)
    return {
        f"p{p}": float(np.percentile(array, p, method="linear")) for p in points
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
        }


def _fraction(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def coverage_and_metrics(rows: Iterable[ScoredRow]) -> MetricSummary:
    """Coverage, error metrics, and exclusion counts for one grouping.

    `bias` is `radar - reference`, stated explicitly so its sign is never ambiguous.
    """
    rows = list(rows)
    exclusions: dict[str, int] = {name: 0 for name in ROW_DISPOSITIONS}
    errors: list[float] = []
    signed: list[float] = []

    for row in rows:
        exclusions[row.disposition] += 1
        if row.disposition == "joint":
            difference = float(row.radar_value) - float(row.reference_value)
            signed.append(difference)
            errors.append(abs(difference))

    n_source = len(rows)
    n_reference = sum(1 for r in rows if r.reference_admitted)
    n_radar = sum(1 for r in rows if r.radar_valid and r.radar_value is not None)
    n_joint = exclusions["joint"]

    if errors:
        absolute = np.asarray(errors, dtype=np.float64)
        difference = np.asarray(signed, dtype=np.float64)
        mae = float(absolute.mean())
        rmse = float(np.sqrt(np.mean(difference**2)))
        bias = float(difference.mean())
    else:
        mae = rmse = bias = None

    return MetricSummary(
        n_source=n_source,
        n_reference_admitted=n_reference,
        n_radar_valid=n_radar,
        n_joint=n_joint,
        reference_coverage=_fraction(n_reference, n_source),
        radar_coverage=_fraction(n_radar, n_source),
        joint_coverage=_fraction(n_joint, n_source),
        joint_given_reference=_fraction(n_joint, n_reference),
        mae=mae,
        rmse=rmse,
        bias=bias,
        error_percentiles=percentiles(errors),
        exclusions=exclusions,
    )


def paired_partitions(
    production_rows: Sequence[ScoredRow], arm_rows: Sequence[ScoredRow]
) -> dict:
    """Validity partitions for production versus one Ahmed arm.

    Computed on the **common reference-admitted** universe, so the four partitions sum
    exactly to `n_reference_admitted`. That identity is asserted, not assumed: a silent
    mismatch would mean rows were dropped somewhere and every downstream count is wrong.

    Reports each estimator's metrics on the `both` intersection and the descriptive
    difference `arm - production`. No p-value, no winner, no ranking.
    """
    by_key_production = {(r.capture_id, r.lock_estimand_id, r.k): r for r in production_rows}
    by_key_arm = {(r.capture_id, r.lock_estimand_id, r.k): r for r in arm_rows}
    common = sorted(set(by_key_production) & set(by_key_arm))

    admitted = [k for k in common if by_key_production[k].reference_admitted]
    partitions = {"both": [], "production_only": [], "ahmed_only": [], "neither": []}
    for key in admitted:
        production_ok = by_key_production[key].disposition == "joint"
        arm_ok = by_key_arm[key].disposition == "joint"
        if production_ok and arm_ok:
            partitions["both"].append(key)
        elif production_ok:
            partitions["production_only"].append(key)
        elif arm_ok:
            partitions["ahmed_only"].append(key)
        else:
            partitions["neither"].append(key)

    counts = {name: len(keys) for name, keys in partitions.items()}
    total = sum(counts.values())
    if total != len(admitted):
        raise ValueError(
            f"validity partitions sum to {total} but the common reference-admitted "
            f"universe has {len(admitted)} rows; rows were lost"
        )

    both = partitions["both"]
    production_metrics = coverage_and_metrics([by_key_production[k] for k in both])
    arm_metrics = coverage_and_metrics([by_key_arm[k] for k in both])

    def difference(field: str) -> float | None:
        a = getattr(arm_metrics, field)
        p = getattr(production_metrics, field)
        return None if a is None or p is None else a - p

    return {
        "n_reference_admitted": len(admitted),
        "partitions": counts,
        "n_intersection": len(both),
        "production_metrics_on_intersection": production_metrics.to_dict(),
        "ahmed_metrics_on_intersection": arm_metrics.to_dict(),
        "descriptive_difference_ahmed_minus_production": {
            "mae": difference("mae"),
            "rmse": difference("rmse"),
            "bias": difference("bias"),
        },
        "comparison_note": (
            "descriptive only; both estimators are conditional on a production-selected "
            "range lock, so this does not validate Ahmed range selection"
        ),
    }
