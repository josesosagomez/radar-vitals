"""Repeated-measures limits of agreement for the prospective study.

The primary estimator is the closed-form unbalanced one-way ANOVA specified in
``notes/analysis_prespec.md`` section 1.  Windows are replicates within a subject;
they are never treated as independent subjects.  This module performs no file I/O
and deliberately requires the subject, session, arm, and window identities on every
row so callers cannot silently fall back to pooled-window agreement.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from typing import Any

import numpy as np
from scipy.optimize import minimize_scalar


BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_SEED = 20_260_725
BOOTSTRAP_MAX_FAILURE_FRACTION = 0.05
ALLOWED_ARMS = frozenset({"natural", "paced", "recovery"})


class AgreementContractError(ValueError):
    """Raised when agreement rows do not satisfy the specified analysis contract."""


def _finite_number(value: object, field: str) -> float:
    if isinstance(value, (bool, np.bool_)):
        raise AgreementContractError(f"{field} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AgreementContractError(f"{field} must be a finite number") from exc
    if not np.isfinite(number):
        raise AgreementContractError(f"{field} must be a finite number")
    return number


def _nonempty_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgreementContractError(f"{field} must be a non-empty string")
    return value


def _linear_slope(x: np.ndarray, y: np.ndarray) -> float | None:
    if x.size < 2:
        return None
    centered = x - np.mean(x)
    denominator = float(np.dot(centered, centered))
    if denominator == 0.0:
        return None
    return float(np.dot(centered, y - np.mean(y)) / denominator)


def _components(groups: Sequence[np.ndarray]) -> dict[str, float]:
    """Return the exact specified ANOVA components for estimable groups."""
    counts = np.asarray([values.size for values in groups], dtype=np.float64)
    subject_means = np.asarray([np.mean(values) for values in groups], dtype=np.float64)
    n_subjects = len(groups)
    n_windows = int(np.sum(counts))
    bias = float(np.mean(subject_means))
    ssw = float(sum(np.sum((values - np.mean(values)) ** 2) for values in groups))
    msw = ssw / (n_windows - n_subjects)
    grand_mean = float(sum(np.sum(values) for values in groups) / n_windows)
    ssb = float(np.sum(counts * (subject_means - grand_mean) ** 2))
    msb = ssb / (n_subjects - 1)
    n0 = float((n_windows - np.sum(counts ** 2) / n_windows) / (n_subjects - 1))
    variance_within = float(msw)
    variance_between_untruncated = float((msb - msw) / n0)
    variance_between = max(variance_between_untruncated, 0.0)
    width = float(1.96 * np.sqrt(variance_between + variance_within))
    return {
        "bias_bpm": bias,
        "ssw": ssw,
        "msw": float(msw),
        "ssb": ssb,
        "msb": float(msb),
        "n0": n0,
        "variance_within_bpm2": variance_within,
        "variance_between_untruncated_bpm2": variance_between_untruncated,
        "variance_between_bpm2": float(variance_between),
        "variance_between_truncated": bool(variance_between_untruncated < 0.0),
        "loa_width_bpm": width,
        "loa_low_bpm": bias - width,
        "loa_high_bpm": bias + width,
    }


def _failure_fraction_allows_ci(failed: int, total: int) -> bool:
    """The specified boundary is inclusive: exactly 5% failed still permits a CI."""
    return failed / total <= BOOTSTRAP_MAX_FAILURE_FRACTION


def _regression_fit(
    groups: Sequence[tuple[np.ndarray, np.ndarray]],
    *,
    center_bpm: float,
) -> dict[str, Any]:
    """REML random-intercept regression via a deterministic 1-D variance-ratio profile.

    Each group contains pair means and differences for one subject cluster.  For
    ``lambda = variance_between / variance_residual``, the correlation-scale block is
    ``W_s = I + lambda J`` and its inverse/determinant are analytic.  Profiling out
    beta and residual variance leaves one bounded scalar optimization.
    """
    n_windows = sum(y.size for _, y in groups)
    n_fixed = 2
    unavailable = {
        "fit_available": False,
        "fit_failure_reason": None,
        "fit_method": "random_intercept_reml_profile_v1",
        "mean_center_bpm": float(center_bpm),
        "intercept_at_center_bpm": None,
        "difference_vs_mean_slope": None,
        "variance_between_bpm2": None,
        "variance_residual_bpm2": None,
        "population_loa_width_bpm": None,
    }
    if len(groups) < 2:
        unavailable["fit_failure_reason"] = "insufficient_subjects_or_residual_df"
        return unavailable
    if not any(group_y.size >= 2 for _, group_y in groups):
        unavailable["fit_failure_reason"] = "no_within_subject_replication"
        return unavailable
    if n_windows <= n_fixed:
        unavailable["fit_failure_reason"] = "insufficient_subjects_or_residual_df"
        return unavailable
    x = np.concatenate([values[0] for values in groups])
    y = np.concatenate([values[1] for values in groups])
    if np.ptp(x) <= np.finfo(np.float64).eps * max(1.0, float(np.max(np.abs(x)))):
        unavailable["fit_failure_reason"] = "pair_mean_has_no_variation"
        return unavailable
    design = np.column_stack([np.ones(n_windows), x - center_bpm])

    def evaluate(lambda_ratio: float) -> tuple[float, np.ndarray, float] | None:
        xt_winv_x = np.zeros((n_fixed, n_fixed), dtype=np.float64)
        xt_winv_y = np.zeros(n_fixed, dtype=np.float64)
        logdet_w = 0.0
        offset = 0
        for group_x, group_y in groups:
            n_group = group_y.size
            group_design = design[offset:offset + n_group]
            shrink = lambda_ratio / (1.0 + n_group * lambda_ratio)
            winv_design = group_design - shrink * np.sum(group_design, axis=0)
            winv_y = group_y - shrink * np.sum(group_y)
            xt_winv_x += group_design.T @ winv_design
            xt_winv_y += group_design.T @ winv_y
            logdet_w += float(np.log1p(n_group * lambda_ratio))
            offset += n_group
        sign, logdet_fixed = np.linalg.slogdet(xt_winv_x)
        if sign <= 0.0:
            return None
        try:
            beta = np.linalg.solve(xt_winv_x, xt_winv_y)
        except np.linalg.LinAlgError:
            return None
        residual = y - design @ beta
        quadratic = 0.0
        offset = 0
        for _, group_y in groups:
            n_group = group_y.size
            group_residual = residual[offset:offset + n_group]
            shrink = lambda_ratio / (1.0 + n_group * lambda_ratio)
            quadratic += float(
                np.dot(group_residual, group_residual)
                - shrink * np.sum(group_residual) ** 2
            )
            offset += n_group
        residual_df = n_windows - n_fixed
        if not np.isfinite(quadratic) or quadratic <= np.finfo(np.float64).tiny:
            return None
        variance_residual = quadratic / residual_df
        objective = (
            residual_df * np.log(variance_residual) + logdet_w + logdet_fixed
        )
        return float(objective), beta, float(variance_residual)

    boundary = evaluate(0.0)
    if boundary is None:
        unavailable["fit_failure_reason"] = "singular_or_zero_residual_fit"
        return unavailable

    def objective(log_lambda: float) -> float:
        evaluated = evaluate(float(np.exp(log_lambda)))
        return float("inf") if evaluated is None else evaluated[0]

    optimized = minimize_scalar(
        objective,
        method="bounded",
        bounds=(-20.0, 20.0),
        options={"xatol": 1e-10, "maxiter": 500},
    )
    candidates: list[tuple[float, float, np.ndarray, float]] = [
        (boundary[0], 0.0, boundary[1], boundary[2])
    ]
    if optimized.success and np.isfinite(optimized.fun):
        ratio = float(np.exp(optimized.x))
        interior = evaluate(ratio)
        if interior is not None:
            candidates.append((interior[0], ratio, interior[1], interior[2]))
    best_objective, ratio, beta, variance_residual = min(candidates, key=lambda item: item[0])
    if not np.isfinite(best_objective):
        unavailable["fit_failure_reason"] = "reml_profile_failed"
        return unavailable
    variance_between = float(ratio * variance_residual)
    width = float(1.96 * np.sqrt(variance_between + variance_residual))
    return {
        "fit_available": True,
        "fit_failure_reason": None,
        "fit_method": "random_intercept_reml_profile_v1",
        "optimizer": "bounded_scalar_log_variance_ratio_with_zero_boundary",
        "optimizer_log_ratio_bounds": [-20.0, 20.0],
        "optimizer_xatol": 1e-10,
        "optimizer_maxiter": 500,
        "mean_center_bpm": float(center_bpm),
        "intercept_at_center_bpm": float(beta[0]),
        "difference_vs_mean_slope": float(beta[1]),
        "variance_between_bpm2": variance_between,
        "variance_residual_bpm2": float(variance_residual),
        "population_loa_width_bpm": width,
    }


def _regression_limits(fit: Mapping[str, Any], at_mean_bpm: float) -> dict[str, float]:
    fitted_bias = float(
        fit["intercept_at_center_bpm"]
        + fit["difference_vs_mean_slope"] * (at_mean_bpm - fit["mean_center_bpm"])
    )
    width = float(fit["population_loa_width_bpm"])
    return {
        "pair_mean_bpm": float(at_mean_bpm),
        "fitted_bias_bpm": fitted_bias,
        "loa_low_bpm": fitted_bias - width,
        "loa_high_bpm": fitted_bias + width,
    }


def arm_loa(
    rows: Sequence[Mapping[str, Any]],
    *,
    allowed_subject_ids: Collection[str],
    arm: str | None = None,
    bootstrap_replicates: int = BOOTSTRAP_REPLICATES,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Compute one arm's subject-clustered repeated-measures limits of agreement.

    Required row fields are ``arm``, ``subject_id``, ``session_id``,
    ``window_index``, ``radar_bpm``, and ``reference_bpm``.  ``allowed_subject_ids``
    must come from the authoritative cohort mapping; unknown identities are rejected.
    The returned result is JSON-safe. Unavailable population quantities are ``None``.
    """
    if not isinstance(bootstrap_replicates, int) or isinstance(bootstrap_replicates, bool):
        raise AgreementContractError("bootstrap_replicates must be a positive integer")
    if bootstrap_replicates <= 0:
        raise AgreementContractError("bootstrap_replicates must be a positive integer")
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise AgreementContractError("seed must be a nonnegative integer")
    if not rows:
        raise AgreementContractError("agreement rows must not be empty")

    if not isinstance(allowed_subject_ids, Collection) or isinstance(allowed_subject_ids, str):
        raise AgreementContractError("allowed_subject_ids must be a non-empty collection")
    allowed_subjects = set(allowed_subject_ids)
    if not allowed_subjects or any(
        not isinstance(value, str) or not value.strip() or value == "unknown"
        for value in allowed_subjects
    ):
        raise AgreementContractError("allowed_subject_ids contains an invalid identity")
    expected_arm = _nonempty_text(arm, "arm") if arm is not None else None
    if expected_arm is not None and expected_arm not in ALLOWED_ARMS:
        raise AgreementContractError(f"arm must be one of {sorted(ALLOWED_ARMS)}")
    parsed: list[dict[str, Any]] = []
    identities: set[tuple[str, str, int]] = set()
    observed_arms: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise AgreementContractError("every agreement row must be a mapping")
        row_arm = _nonempty_text(row.get("arm"), "arm")
        if row_arm not in ALLOWED_ARMS:
            raise AgreementContractError(f"arm must be one of {sorted(ALLOWED_ARMS)}")
        observed_arms.add(row_arm)
        subject_id = _nonempty_text(row.get("subject_id"), "subject_id")
        if subject_id not in allowed_subjects:
            raise AgreementContractError(f"subject_id is not in allowed_subject_ids: {subject_id!r}")
        session_id = _nonempty_text(row.get("session_id"), "session_id")
        window_index = row.get("window_index")
        if type(window_index) is not int or window_index < 0:
            raise AgreementContractError("window_index must be a nonnegative integer")
        identity = (subject_id, session_id, window_index)
        if identity in identities:
            raise AgreementContractError(f"duplicate agreement row identity: {identity!r}")
        identities.add(identity)
        radar = _finite_number(row.get("radar_bpm"), "radar_bpm")
        reference = _finite_number(row.get("reference_bpm"), "reference_bpm")
        parsed.append(
            {
                "arm": row_arm,
                "subject_id": subject_id,
                "session_id": session_id,
                "window_index": window_index,
                "radar_bpm": radar,
                "reference_bpm": reference,
                "difference_bpm": radar - reference,
                "pair_mean_bpm": (radar + reference) / 2.0,
            }
        )
    if len(observed_arms) != 1:
        raise AgreementContractError("arm_loa requires rows from exactly one arm")
    observed_arm = next(iter(observed_arms))
    if expected_arm is not None and observed_arm != expected_arm:
        raise AgreementContractError(
            f"row arm {observed_arm!r} does not match requested arm {expected_arm!r}"
        )

    by_subject: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in parsed:
        by_subject[row["subject_id"]].append(row)
    for subject, subject_rows in by_subject.items():
        sessions = {row["session_id"] for row in subject_rows}
        if len(sessions) != 1:
            raise AgreementContractError(
                f"subject {subject!r} has multiple sessions in arm {observed_arm!r}"
            )
    subject_ids = sorted(by_subject)
    groups = [
        np.asarray([row["difference_bpm"] for row in by_subject[subject]], dtype=np.float64)
        for subject in subject_ids
    ]
    regression_groups = [
        (
            np.asarray(
                [row["pair_mean_bpm"] for row in by_subject[subject]], dtype=np.float64
            ),
            np.asarray(
                [row["difference_bpm"] for row in by_subject[subject]], dtype=np.float64
            ),
        )
        for subject in subject_ids
    ]
    n_subjects = len(groups)
    n_windows = len(parsed)
    point_estimable = n_subjects >= 2 and n_windows > n_subjects
    differences = np.asarray([row["difference_bpm"] for row in parsed], dtype=np.float64)
    descriptive_bias = float(np.mean(differences))
    descriptive_sd = float(np.std(differences, ddof=1)) if n_windows >= 2 else None
    pair_means = np.asarray([row["pair_mean_bpm"] for row in parsed], dtype=np.float64)
    regression_center = float(np.mean(pair_means))
    regression_fit = _regression_fit(regression_groups, center_bpm=regression_center)
    evaluation_means = [
        float(value)
        for value in np.quantile(
            pair_means, [0.05, 0.25, 0.5, 0.75, 0.95], method="linear"
        )
    ]
    regression_sensitivity: dict[str, Any] = {
        **regression_fit,
        "evaluation_percentiles": [5, 25, 50, 75, 95],
        "limits_by_pair_mean": (
            [_regression_limits(regression_fit, value) for value in evaluation_means]
            if regression_fit["fit_available"] else []
        ),
        "bootstrap_replicates_requested": bootstrap_replicates,
        "bootstrap_replicates_valid": 0,
        "bootstrap_replicates_failed": bootstrap_replicates,
        "bootstrap_failure_fraction": 1.0,
        "bootstrap_ci_available": False,
        "bootstrap_failure_reason": "primary_not_estimable",
        "bootstrap_seed": seed,
        "bootstrap_percentile_method": "linear",
    }

    result: dict[str, Any] = {
        "schema_version": 1,
        "method": "unbalanced_one_way_anova_subject_clustered_loa_v1",
        "arm": observed_arm,
        "n_subjects": n_subjects,
        "n_windows": n_windows,
        "subject_window_counts": {
            subject: len(by_subject[subject]) for subject in subject_ids
        },
        "point_estimable": point_estimable,
        "report_population_loa": False,
        "descriptive_only": True,
        "limitation_reason": None,
        "descriptive_pooled_window_bias_bpm": descriptive_bias,
        "descriptive_pooled_window_sd_bpm": descriptive_sd,
        "bias_bpm": None,
        "ssw": None,
        "msw": None,
        "ssb": None,
        "msb": None,
        "n0": None,
        "variance_within_bpm2": None,
        "variance_between_untruncated_bpm2": None,
        "variance_between_bpm2": None,
        "variance_between_truncated": None,
        "loa_width_bpm": None,
        "loa_low_bpm": None,
        "loa_high_bpm": None,
        "loa_low_ci95_bpm": None,
        "loa_high_ci95_bpm": None,
        "bootstrap_replicates_requested": bootstrap_replicates,
        "bootstrap_replicates_valid": 0,
        "bootstrap_replicates_failed": bootstrap_replicates,
        "bootstrap_failure_fraction": 1.0,
        "bootstrap_duplicate_cluster_replicates": 0,
        "bootstrap_seed": seed,
        "bootstrap_percentile_method": "linear",
        "window_rows": parsed,
    }
    diagnostics = _diagnostics(parsed, by_subject)
    diagnostics["regression_loa_sensitivity"] = regression_sensitivity
    if not point_estimable:
        conditions = []
        if n_subjects < 2:
            conditions.append("fewer_than_two_subjects")
        if n_windows <= n_subjects:
            conditions.append("no_within_subject_replication")
        result["limitation_reason"] = "+".join(conditions)
        result["diagnostics"] = diagnostics
        return result

    result.update(_components(groups))
    rng = np.random.default_rng(seed)
    lower: list[float] = []
    upper: list[float] = []
    failures = 0
    regression_bootstrap: list[dict[str, Any]] = []
    regression_failures = 0
    duplicate_cluster_replicates = 0
    for _ in range(bootstrap_replicates):
        sampled = rng.integers(0, n_subjects, size=n_subjects)
        # The spec rejects resamples containing fewer than two *original* subjects.
        if np.unique(sampled).size < 2:
            failures += 1
            continue
        sampled_groups = [groups[int(index)] for index in sampled]
        if np.unique(sampled).size < sampled.size:
            duplicate_cluster_replicates += 1
        if not any(values.size >= 2 for values in sampled_groups):
            failures += 1
            continue
        boot = _components(sampled_groups)
        lower.append(boot["loa_low_bpm"])
        upper.append(boot["loa_high_bpm"])
        if regression_fit["fit_available"]:
            sampled_regression_groups = [regression_groups[int(index)] for index in sampled]
            regression_boot = _regression_fit(
                sampled_regression_groups, center_bpm=regression_center
            )
            if regression_boot["fit_available"]:
                regression_bootstrap.append(regression_boot)
            else:
                regression_failures += 1

    valid = bootstrap_replicates - failures
    failure_fraction = failures / bootstrap_replicates
    result.update(
        {
            "bootstrap_replicates_valid": valid,
            "bootstrap_replicates_failed": failures,
            "bootstrap_failure_fraction": failure_fraction,
            "bootstrap_duplicate_cluster_replicates": duplicate_cluster_replicates,
            "diagnostics": diagnostics,
        }
    )
    if regression_fit["fit_available"]:
        regression_total_failures = failures + regression_failures
        regression_failure_fraction = regression_total_failures / bootstrap_replicates
        regression_sensitivity.update(
            {
                "bootstrap_replicates_valid": len(regression_bootstrap),
                "bootstrap_replicates_failed": regression_total_failures,
                "bootstrap_failure_fraction": regression_failure_fraction,
                "bootstrap_failure_reason": None,
            }
        )
        if regression_bootstrap and _failure_fraction_allows_ci(
            regression_total_failures, bootstrap_replicates
        ):
            low_by_mean: list[list[float]] = [[] for _ in evaluation_means]
            high_by_mean: list[list[float]] = [[] for _ in evaluation_means]
            for fit in regression_bootstrap:
                for index, value in enumerate(evaluation_means):
                    limits = _regression_limits(fit, value)
                    low_by_mean[index].append(limits["loa_low_bpm"])
                    high_by_mean[index].append(limits["loa_high_bpm"])
            for row, low_values, high_values in zip(
                regression_sensitivity["limits_by_pair_mean"],
                low_by_mean,
                high_by_mean,
                strict=True,
            ):
                row["loa_low_ci95_bpm"] = [
                    float(np.percentile(low_values, 2.5, method="linear")),
                    float(np.percentile(low_values, 97.5, method="linear")),
                ]
                row["loa_high_ci95_bpm"] = [
                    float(np.percentile(high_values, 2.5, method="linear")),
                    float(np.percentile(high_values, 97.5, method="linear")),
                ]
            regression_sensitivity["bootstrap_ci_available"] = True
        else:
            regression_sensitivity["bootstrap_failure_reason"] = (
                "bootstrap_failure_fraction_above_0.05"
            )
    else:
        regression_sensitivity["bootstrap_failure_reason"] = regression_fit[
            "fit_failure_reason"
        ]
    if not _failure_fraction_allows_ci(failures, bootstrap_replicates):
        result["limitation_reason"] = "bootstrap_failure_fraction_above_0.05"
        return result

    result.update(
        {
            "report_population_loa": True,
            "descriptive_only": False,
            "loa_low_ci95_bpm": [
                float(np.percentile(lower, 2.5, method="linear")),
                float(np.percentile(lower, 97.5, method="linear")),
            ],
            "loa_high_ci95_bpm": [
                float(np.percentile(upper, 2.5, method="linear")),
                float(np.percentile(upper, 97.5, method="linear")),
            ],
        }
    )
    return result


def _diagnostics(
    parsed: Sequence[Mapping[str, Any]],
    by_subject: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, Any]:
    """Mandatory transparent diagnostics without changing the specified primary estimator."""
    differences = np.asarray([row["difference_bpm"] for row in parsed], dtype=np.float64)
    pair_means = np.asarray([row["pair_mean_bpm"] for row in parsed], dtype=np.float64)
    subject_means = {
        subject: float(np.mean([row["difference_bpm"] for row in rows]))
        for subject, rows in by_subject.items()
    }
    residuals = np.asarray(
        [row["difference_bpm"] - subject_means[row["subject_id"]] for row in parsed],
        dtype=np.float64,
    )
    centered = residuals - np.mean(residuals)
    scale = float(np.sqrt(np.mean(centered ** 2)))
    skew = float(np.mean((centered / scale) ** 3)) if scale > 0.0 else None
    qq_probabilities = [0.05, 0.25, 0.5, 0.75, 0.95]
    qq_normal_scores = [-1.644853626951, -0.674489750196, 0.0, 0.674489750196, 1.644853626951]

    lag_left: list[float] = []
    lag_right: list[float] = []
    sessions: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in parsed:
        sessions[(row["subject_id"], row["session_id"])].append(row)
    for session_rows in sessions.values():
        ordered = sorted(session_rows, key=lambda row: row["window_index"])
        values = [row["difference_bpm"] - subject_means[row["subject_id"]] for row in ordered]
        lag_left.extend(values[:-1])
        lag_right.extend(values[1:])
    lag1 = None
    if len(lag_left) >= 2 and np.std(lag_left) > 0.0 and np.std(lag_right) > 0.0:
        lag1 = float(np.corrcoef(lag_left, lag_right)[0, 1])

    return {
        "difference_vs_pair_mean_slope": _linear_slope(pair_means, differences),
        "absolute_residual_vs_abs_pair_mean_slope": _linear_slope(
            np.abs(pair_means), np.abs(residuals)
        ),
        "subject_centered_residual_skew": skew,
        "residual_qq_summary": [
            {
                "probability": probability,
                "normal_score": score,
                "observed_residual_quantile_bpm": float(
                    np.quantile(residuals, probability, method="linear")
                ),
            }
            for probability, score in zip(qq_probabilities, qq_normal_scores, strict=True)
        ],
        "within_session_residual_lag1_autocorrelation": lag1,
        "within_session_lag1_pair_count": len(lag_left),
        "serial_correlation_limitation": (
            "The point estimator assumes conditionally independent within-subject residuals; "
            "cluster bootstrapping preserves observed sequences but does not repair bias in the "
            "point variance components."
        ),
        "small_cluster_bootstrap_limitation": (
            "With at most ten subjects, percentile cluster-bootstrap intervals may under-cover "
            "and can be anti-conservative for the precision gate."
        ),
    }
