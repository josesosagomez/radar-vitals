"""Kotte joint-Doppler DSP core (M9, plans/m9_kotte_plan.md).

Implements the estimator of Kotte, Ahmed, Alouini, Al-Naffouri, "Joint Estimation of
Single Target's High Amplitude Difference Doppler Frequencies in FMCW Radar" (IEEE T-RS
vol. 2, 2024, DOI 10.1109/TRS.2024.3352189), eqs (23)-(26) and Algorithm 1's selection
line, on the complex slow-time x RX matrix at one range bin.

Everything here is DSP-only: no capture, subject, or reference knowledge may enter this
module or any hash it computes. The capture manifest is runner input, hashed separately
(plan, pass 9).

Conventions (pinned by the plan and its review passes):

* ``Y_t`` has rows = slow-time samples (chirp/frame index at ``t_pri_s``), columns = RX.
* Temporal steering ``a(f)_n = exp(j 2 pi f n T_PRI)``; spatial steering for a
  half-wavelength ULA ``a(theta)_m = exp(j pi sin(theta) (m-1))``.
* Sample covariance ``R = Y Y^H / n_snapshots`` where the snapshots are the RX columns
  (the divisor is pinned by a direct unit test).
* Loading is trace-relative: ``R_loaded = R + delta * (trace(R).real / n) * I``. The
  selection objective ``1^T H^-1 1`` is argmax-invariant under ``R -> cR`` with this rule.
* Numerical rank: ``rank = #{lambda_i > rank_rtol * lambda_max}``; a finite
  ``lambda_max <= 0`` is rank 0 (covariance failure path); nonfinite eigenvalues are
  rejected. The identical rule applies everywhere (controls, ablation, gate, stage A).
* The unloaded inverse is never formed on a rank-deficient covariance: the unloaded path
  refuses unless the numerical rank equals the dimension.
* Grid cells where the 2x2 constraint Gram matrix ``H`` has reciprocal condition number
  below ``condition_mask_threshold`` are masked (NaN); an all-masked surface is a DSP
  failure, never a silent argmax over garbage.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from hashlib import sha256
from typing import Mapping, Sequence

import numpy as np

from src.m4.bundle import strict_json_bytes
from src.m4.estimator_suite import (
    EstimatorArmSpec,
    SuiteWindowResult,
    canonical_plain,
    freeze_array,
    validate_arm_specs,
    validate_returned_arms,
)

__all__ = [
    "AllMaskedError",
    "CPI_CAUSE_CODES",
    "CaponContext",
    "CollapsedSurface",
    "ConstraintMatrixFailure",
    "CovarianceFailure",
    "KotteArm",
    "KotteEstimatorSuite",
    "KotteJointDopplerConfig",
    "PairSelection",
    "SurfaceResult",
    "alias_collapse",
    "argmax_masked",
    "beta_surface",
    "canonical_hash",
    "capon_power_at",
    "estimate_window",
    "extract_rx_slow_time",
    "joint_beta_at",
    "joint_weight_at",
    "joint_capon_surface",
    "make_grid_hz",
    "medoid_of_cpi_estimates",
    "numerical_rank",
    "pair_margin_db",
    "prepare_cpis",
    "remove_per_rx_mean",
    "sample_covariance",
    "signed_band_grid_hz",
    "spatial_steering",
    "temporal_steering",
    "validate_project_frame_continuity",
]

ESTIMATOR_FORMS = ("cpi_medoid",)

#: Integer codebook for per-CPI validity causes. Persisted in the evidence NPZ, which
#: loads with allow_pickle=False, so causes must be integers rather than strings.
CPI_CAUSE_CODES = {
    "valid": 0,
    "covariance_nonfinite": 1,
    "rank_zero": 2,
    "rank_deficient_unloaded": 3,
    "nonpositive_loaded_spectrum": 4,
    "all_masked": 5,
}
_CAUSE_FROM_REASON = {
    "nonfinite_covariance": CPI_CAUSE_CODES["covariance_nonfinite"],
    "nonfinite_eigenvalues": CPI_CAUSE_CODES["covariance_nonfinite"],
    "rank_zero": CPI_CAUSE_CODES["rank_zero"],
    "rank_deficient_unloaded": CPI_CAUSE_CODES["rank_deficient_unloaded"],
    "nonpositive_loaded_spectrum": CPI_CAUSE_CODES["nonpositive_loaded_spectrum"],
}


class CovarianceFailure(Exception):
    """Covariance cannot support the requested inverse (rank 0, rank-deficient
    unloaded, nonfinite entries, or non-positive loaded spectrum)."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class AllMaskedError(Exception):
    """Every grid cell was masked; selection is impossible (``dsp_failed``)."""


class ConstraintMatrixFailure(Exception):
    """The two-frequency Eq. (25) constraint matrix is not admissible.

    Equal frequencies make the two columns of ``A`` identical.  Near-equal pairs are
    rejected under the same declared reciprocal-condition threshold used by the 2-D
    surfaces; M9 never perturbs a frequency or substitutes a pseudoinverse.
    """


# ---------------------------------------------------------------------------------------
# Hashing (shared canonical form; identical bytes on every platform)
# ---------------------------------------------------------------------------------------

def canonical_hash(payload: Mapping[str, object]) -> str:
    """SHA-256 of the canonical strict-JSON encoding of ``payload``."""
    return sha256(strict_json_bytes(canonical_plain(payload))).hexdigest()


# ---------------------------------------------------------------------------------------
# Grids and steering
# ---------------------------------------------------------------------------------------

def make_grid_hz(start_hz: float, stop_hz: float, step_hz: float) -> np.ndarray:
    """Half-open uniform grid ``[start, stop)`` — ``stop`` itself is never a point."""
    if not (step_hz > 0.0) or not np.isfinite(step_hz):
        raise ValueError(f"step_hz must be positive and finite, got {step_hz}")
    if not stop_hz > start_hz:
        raise ValueError(f"empty grid: start {start_hz}, stop {stop_hz}")
    span = (stop_hz - start_hz) / step_hz
    n = int(round(span))
    if not np.isclose(span, n, rtol=0.0, atol=1e-9):
        n = int(np.ceil(span - 1e-12))
    grid = start_hz + step_hz * np.arange(n, dtype=np.float64)
    if grid.size == 0 or grid[-1] >= stop_hz - 1e-12:
        grid = grid[grid < stop_hz - 1e-12]
    if grid.size == 0:
        raise ValueError("grid collapsed to zero points")
    return grid


def signed_band_grid_hz(band_hz: Sequence[float], step_hz: float) -> np.ndarray:
    """Signed band grid: ``-[hi..lo]`` then ``+[lo..hi]``, both endpoints included."""
    lo, hi = float(band_hz[0]), float(band_hz[1])
    if not (0.0 < lo < hi):
        raise ValueError(f"band must satisfy 0 < lo < hi, got {band_hz}")
    n = int(round((hi - lo) / step_hz)) + 1
    if not np.isclose(lo + (n - 1) * step_hz, hi, rtol=0.0, atol=1e-9):
        raise ValueError(f"band {band_hz} is not an integer number of {step_hz} Hz steps")
    positive = lo + step_hz * np.arange(n, dtype=np.float64)
    return np.concatenate([-positive[::-1], positive])


def temporal_steering(freqs_hz: np.ndarray, n_c: int, t_pri_s: float) -> np.ndarray:
    """Slow-time steering matrix, shape ``(n_c, F)``: ``exp(j 2 pi f n T_PRI)``."""
    freqs = np.atleast_1d(np.asarray(freqs_hz, dtype=np.float64))
    if n_c <= 0:
        raise ValueError(f"n_c must be positive, got {n_c}")
    if not (np.isfinite(t_pri_s) and t_pri_s > 0.0):
        raise ValueError(f"t_pri_s must be positive and finite, got {t_pri_s}")
    if not np.all(np.isfinite(freqs)):
        raise ValueError("frequencies must be finite physical-Hz values")
    n = np.arange(n_c, dtype=np.float64)[:, None]
    return np.exp(2j * np.pi * n * (freqs[None, :] * t_pri_s))


def spatial_steering(theta_rad: float, n_r: int) -> np.ndarray:
    """Half-wavelength ULA steering vector, shape ``(n_r,)``."""
    m = np.arange(n_r, dtype=np.float64)
    return np.exp(1j * np.pi * np.sin(theta_rad) * m)


# ---------------------------------------------------------------------------------------
# Covariance, rank rule, and the (possibly loaded) solver
# ---------------------------------------------------------------------------------------

def sample_covariance(y_t: np.ndarray) -> np.ndarray:
    """``R = Y Y^H / n_snapshots`` with snapshots = columns (RX channels)."""
    y = np.asarray(y_t)
    if y.ndim != 2:
        raise ValueError(f"y_t must be 2-D (n_c, n_r), got shape {y.shape}")
    n_snapshots = y.shape[1]
    if n_snapshots == 0:
        raise ValueError("y_t has zero snapshot columns")
    return (y @ y.conj().T) / n_snapshots


def remove_per_rx_mean(z: np.ndarray) -> np.ndarray:
    """Subtract each RX column's slow-time mean (paper section III-B; makes the sample
    covariance exactly singular: ``R . 1 = 0``)."""
    z = np.asarray(z)
    return z - z.mean(axis=0, keepdims=True)


def numerical_rank(eigvals: np.ndarray, rank_rtol: float) -> int:
    """The pinned rank rule. ``eigvals`` in any order; must be real and finite."""
    values = np.asarray(eigvals, dtype=np.float64)
    if values.size == 0:
        raise ValueError("empty eigenvalue array")
    if not np.all(np.isfinite(values)):
        raise CovarianceFailure("nonfinite_eigenvalues", "nonfinite eigenvalues rejected")
    lam_max = float(values.max())
    if lam_max <= 0.0:
        return 0
    return int(np.count_nonzero(values > rank_rtol * lam_max))


@dataclass(frozen=True)
class CaponContext:
    """Eigendecomposition-backed solver for ``R_eff = R + delta_bar I``.

    ``eigvals`` are the eigenvalues of the *unloaded* covariance, descending.
    ``delta_bar`` is the resolved absolute loading (0.0 for the unloaded path).
    """

    eigvals: np.ndarray
    eigvecs: np.ndarray
    rank: int
    delta_bar: float
    n: int

    @classmethod
    def from_covariance(
        cls,
        covariance: np.ndarray,
        *,
        loading_delta: float | None,
        rank_rtol: float,
    ) -> "CaponContext":
        r = np.asarray(covariance)
        if r.ndim != 2 or r.shape[0] != r.shape[1]:
            raise ValueError(f"covariance must be square, got shape {r.shape}")
        if not np.all(np.isfinite(r)):
            raise CovarianceFailure("nonfinite_covariance", "nonfinite covariance entries")
        eigvals, eigvecs = np.linalg.eigh(r)
        eigvals = eigvals[::-1].copy()
        eigvecs = eigvecs[:, ::-1].copy()
        rank = numerical_rank(eigvals, rank_rtol)
        n = r.shape[0]
        if rank == 0:
            raise CovarianceFailure(
                "rank_zero", "lambda_max <= 0: covariance carries no energy"
            )
        if loading_delta is None:
            if rank < n:
                raise CovarianceFailure(
                    "rank_deficient_unloaded",
                    f"rank {rank} < dimension {n}: the unloaded inverse is never "
                    "formed on a rank-deficient covariance",
                )
            delta_bar = 0.0
        else:
            if not (loading_delta > 0.0):
                raise ValueError(f"loading_delta must be positive, got {loading_delta}")
            delta_bar = float(loading_delta) * float(np.trace(r).real) / n
            if not np.all(eigvals + delta_bar > 0.0):
                raise CovarianceFailure(
                    "nonpositive_loaded_spectrum",
                    "loaded spectrum not strictly positive",
                )
        return cls(
            eigvals=eigvals, eigvecs=eigvecs, rank=rank, delta_bar=delta_bar, n=n
        )

    @property
    def effective_eigvals(self) -> np.ndarray:
        return self.eigvals + self.delta_bar

    def solve(self, columns: np.ndarray) -> np.ndarray:
        """``R_eff^{-1} @ columns`` via the eigendecomposition."""
        projected = self.eigvecs.conj().T @ columns
        return self.eigvecs @ (projected / self.effective_eigvals[:, None])


# ---------------------------------------------------------------------------------------
# Surfaces
# ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SurfaceResult:
    """The masked selection surface over a (f1, f2) grid pair.

    ``objective`` is ``Re(1^T H^-1 1)`` (the loaded Capon power when the context is
    loaded; Algorithm 1's literal selection objective when unloaded), NaN at masked
    cells. ``rcond`` is the 2x2 reciprocal condition number of ``H`` per cell.
    """

    objective: np.ndarray
    rcond: np.ndarray
    masked: np.ndarray
    masked_fraction: float
    f1_grid_hz: np.ndarray
    f2_grid_hz: np.ndarray


def _gram_blocks(
    ctx: CaponContext,
    f1_grid_hz: np.ndarray,
    f2_grid_hz: np.ndarray,
    n_c: int,
    t_pri_s: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """``g1[i] = a1_i^H R^-1 a1_i``, ``g2[j]``, and ``cross[i, j] = a1_i^H R^-1 a2_j``."""
    a1 = temporal_steering(f1_grid_hz, n_c, t_pri_s)
    a2 = temporal_steering(f2_grid_hz, n_c, t_pri_s)
    x1 = ctx.solve(a1)
    x2 = ctx.solve(a2)
    g1 = np.einsum("ni,ni->i", a1.conj(), x1).real
    g2 = np.einsum("nj,nj->j", a2.conj(), x2).real
    cross = a1.conj().T @ x2
    return a1, a2, g1, g2, cross


def joint_capon_surface(
    ctx: CaponContext,
    f1_grid_hz: np.ndarray,
    f2_grid_hz: np.ndarray,
    *,
    n_c: int,
    t_pri_s: float,
    condition_mask_threshold: float,
) -> SurfaceResult:
    """Vectorized ``Re(1^T H^-1 1)`` over every (f1, f2) pair, rcond-masked."""
    _, _, g1, g2, cross = _gram_blocks(ctx, f1_grid_hz, f2_grid_hz, n_c, t_pri_s)
    g1c = g1[:, None]
    g2c = g2[None, :]
    abs_cross_sq = np.abs(cross) ** 2
    det = g1c * g2c - abs_cross_sq
    numerator = g1c + g2c - 2.0 * cross.real

    mean = 0.5 * (g1c + g2c)
    spread = np.sqrt(0.25 * (g1c - g2c) ** 2 + abs_cross_sq)
    lam_max = mean + spread
    lam_min = mean - spread
    with np.errstate(divide="ignore", invalid="ignore"):
        rcond = np.where(lam_max > 0.0, lam_min / lam_max, 0.0)
        objective = numerator / det

    masked = ~(rcond > condition_mask_threshold) | ~np.isfinite(objective)
    objective = np.where(masked, np.nan, objective)
    return SurfaceResult(
        objective=objective,
        rcond=rcond,
        masked=masked,
        masked_fraction=float(masked.mean()),
        f1_grid_hz=np.asarray(f1_grid_hz, dtype=np.float64),
        f2_grid_hz=np.asarray(f2_grid_hz, dtype=np.float64),
    )


def beta_surface(
    ctx: CaponContext,
    y_t: np.ndarray,
    f1_grid_hz: np.ndarray,
    f2_grid_hz: np.ndarray,
    *,
    theta0_rad: float,
    t_pri_s: float,
    condition_mask_threshold: float,
) -> np.ndarray:
    """Vectorized eq-26 joint amplitude ``beta_hat`` over every (f1, f2) pair.

    Controls-only analysis surface (theta0 known). Masked cells are NaN.
    """
    y = np.asarray(y_t)
    n_c, n_r = y.shape
    a1, a2, g1, g2, cross = _gram_blocks(ctx, f1_grid_hz, f2_grid_hz, n_c, t_pri_s)
    a_theta = spatial_steering(theta0_rad, n_r)
    spatial_norm = np.vdot(a_theta, a_theta)
    # Eq. (26), PDF p. 117: c1[i] = a1_i^H R^-1 Y_t a(theta)* /
    # (a(theta)^H a(theta)); c2 is analogous.  The ordinary transpose in
    # Y_t=Y^T (Eq. 23) must not be confused with these Hermitian products.
    x1 = ctx.solve(a1)
    x2 = ctx.solve(a2)
    c1 = (x1.conj().T @ y) @ a_theta.conj() / spatial_norm
    c2 = (x2.conj().T @ y) @ a_theta.conj() / spatial_norm

    g1c = g1[:, None]
    g2c = g2[None, :]
    abs_cross_sq = np.abs(cross) ** 2
    det = g1c * g2c - abs_cross_sq
    mean = 0.5 * (g1c + g2c)
    spread = np.sqrt(0.25 * (g1c - g2c) ** 2 + abs_cross_sq)
    with np.errstate(divide="ignore", invalid="ignore"):
        rcond = np.where(mean + spread > 0.0, (mean - spread) / (mean + spread), 0.0)
        beta = ((g2c - cross.conj()) * c1[:, None] + (g1c - cross) * c2[None, :]) / det
    masked = ~(rcond > condition_mask_threshold) | ~np.isfinite(beta)
    return np.where(masked, np.nan + 0j, beta)


def capon_power_at(
    ctx: CaponContext,
    f1_hz: float,
    f2_hz: float,
    *,
    n_c: int,
    t_pri_s: float,
    condition_mask_threshold: float = 1.0e-8,
) -> float:
    """Direct loop-form ``Re(1^T H^-1 1)`` at a single pair (the vectorized == loop
    reference; also the per-cell path for oracle checks)."""
    _weight, h = joint_weight_at(
        ctx,
        f1_hz,
        f2_hz,
        n_c=n_c,
        t_pri_s=t_pri_s,
        condition_mask_threshold=condition_mask_threshold,
    )
    ones = np.ones(2, dtype=np.complex128)
    return float((ones @ np.linalg.solve(h, ones)).real)


def joint_weight_at(
    ctx: CaponContext,
    f1_hz: float,
    f2_hz: float,
    *,
    n_c: int,
    t_pri_s: float,
    condition_mask_threshold: float = 1.0e-8,
) -> tuple[np.ndarray, np.ndarray]:
    """Eq. (25) weight and its ``H`` matrix for one physical-Hz pair.

    Shapes follow PDF p. 116 (printed page): ``A`` is ``(N_c, 2)``,
    ``R_t^-1 A`` is ``(N_c, 2)``, ``H=A^H R_t^-1 A`` is ``(2, 2)``, and
    ``w`` is ``(N_c,)``.  The resulting constraint is ``w^H A = [1, 1]``.
    """
    if f1_hz == f2_hz:
        raise ConstraintMatrixFailure("Eq. (25) is undefined for f1 == f2")
    if not (0.0 <= condition_mask_threshold < 1.0):
        raise ValueError("condition_mask_threshold must satisfy 0 <= threshold < 1")

    frequency_constraint_matrix = temporal_steering(
        np.array([f1_hz, f2_hz]), n_c, t_pri_s
    )
    covariance_solutions = ctx.solve(frequency_constraint_matrix)
    constraint_gram = frequency_constraint_matrix.conj().T @ covariance_solutions
    reciprocal_condition = 1.0 / float(np.linalg.cond(constraint_gram))
    if not (
        np.isfinite(reciprocal_condition)
        and reciprocal_condition > condition_mask_threshold
    ):
        raise ConstraintMatrixFailure(
            "Eq. (25) H is singular or below the declared reciprocal-condition threshold"
        )

    constraint_target = np.ones(2, dtype=np.complex128)
    constraint_coefficients = np.linalg.solve(constraint_gram, constraint_target)
    weight = covariance_solutions @ constraint_coefficients
    return weight, constraint_gram


def joint_beta_at(
    ctx: CaponContext,
    y_t: np.ndarray,
    f1_hz: float,
    f2_hz: float,
    *,
    theta0_rad: float,
    t_pri_s: float,
    condition_mask_threshold: float = 1.0e-8,
) -> complex:
    """Direct Eqs. (25)-(26), including the PDF's spatial conjugation.

    Eq. (26), PDF p. 117, is the *joint* coefficient
    ``w^H Y_t a(theta_hat)* / (a^H a)`` rather than two amplitudes.
    """
    y = np.asarray(y_t)
    n_c, n_r = y.shape
    w, _constraint_gram = joint_weight_at(
        ctx,
        f1_hz,
        f2_hz,
        n_c=n_c,
        t_pri_s=t_pri_s,
        condition_mask_threshold=condition_mask_threshold,
    )
    a_theta = spatial_steering(theta0_rad, n_r)
    spatial_norm = np.vdot(a_theta, a_theta)
    return complex((w.conj() @ y) @ a_theta.conj() / spatial_norm)


# ---------------------------------------------------------------------------------------
# Selection: argmax, alias collapse, margin
# ---------------------------------------------------------------------------------------

def argmax_masked(surface: np.ndarray) -> tuple[int, int]:
    """First (row-major) index of the maximum over finite cells; all-masked raises."""
    values = np.asarray(surface, dtype=np.float64)
    finite = np.isfinite(values)
    if not finite.any():
        raise AllMaskedError("every grid cell is masked")
    filled = np.where(finite, values, -np.inf)
    flat = int(np.argmax(filled))
    return tuple(int(v) for v in np.unravel_index(flat, values.shape))  # type: ignore[return-value]


@dataclass(frozen=True)
class CollapsedSurface:
    """Alias collapse of a signed-grid surface onto (|f1|, |f2|).

    The representative of each collapsed cell is the maximizing raw signed pair. Exact
    ties use ascending lexicographic order on ``(f_b, f_h)`` as required by the M9 plan.
    """

    objective: np.ndarray
    abs_f1_hz: np.ndarray
    abs_f2_hz: np.ndarray
    sign_f1: np.ndarray
    sign_f2: np.ndarray


# For fixed positive magnitudes, ascending lexicographic raw signed pairs are
# (-f_b,-f_h), (-f_b,+f_h), (+f_b,-f_h), (+f_b,+f_h).
_SIGN_ORDER = ((-1, -1), (-1, 1), (1, -1), (1, 1))


def _signed_axis_maps(grid: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For a symmetric signed grid, return (abs values asc, +index map, -index map)."""
    grid = np.asarray(grid, dtype=np.float64)
    positive = np.sort(grid[grid > 0.0])
    negative = np.sort(-grid[grid < 0.0])
    if positive.size == 0 or not np.allclose(positive, negative, rtol=0.0, atol=1e-12):
        raise ValueError("alias collapse requires a sign-symmetric grid")
    order = np.argsort(grid, kind="stable")
    sorted_vals = grid[order]
    pos_map = np.empty(positive.size, dtype=np.int64)
    neg_map = np.empty(positive.size, dtype=np.int64)
    for k, value in enumerate(positive):
        pos_map[k] = order[np.searchsorted(sorted_vals, value)]
        neg_map[k] = order[np.searchsorted(sorted_vals, -value)]
    return positive, pos_map, neg_map


def alias_collapse(
    surface: np.ndarray, f1_grid_hz: np.ndarray, f2_grid_hz: np.ndarray
) -> CollapsedSurface:
    """Collapse a signed (f1, f2) surface to (|f1|, |f2|) by the deterministic rule."""
    values = np.asarray(surface, dtype=np.float64)
    abs_f1, pos1, neg1 = _signed_axis_maps(f1_grid_hz)
    abs_f2, pos2, neg2 = _signed_axis_maps(f2_grid_hz)
    maps = {1: (pos1, pos2), -1: (neg1, neg2)}
    quadrants = []
    for s1, s2 in _SIGN_ORDER:
        rows = maps[s1][0]
        cols = maps[s2][1]
        quadrants.append(values[np.ix_(rows, cols)])
    stack = np.stack(quadrants, axis=0)
    finite = np.isfinite(stack)
    filled = np.where(finite, stack, -np.inf)
    winner = np.argmax(filled, axis=0)  # first max in preference order
    collapsed = np.take_along_axis(stack, winner[None, ...], axis=0)[0]
    all_masked = ~finite.any(axis=0)
    collapsed = np.where(all_masked, np.nan, collapsed)
    signs = np.array(_SIGN_ORDER, dtype=np.int8)
    sign_f1 = signs[winner, 0]
    sign_f2 = signs[winner, 1]
    sign_f1 = np.where(all_masked, np.int8(0), sign_f1)
    sign_f2 = np.where(all_masked, np.int8(0), sign_f2)
    return CollapsedSurface(
        objective=collapsed,
        abs_f1_hz=abs_f1,
        abs_f2_hz=abs_f2,
        sign_f1=sign_f1,
        sign_f2=sign_f2,
    )


def pair_margin_db(
    surface: np.ndarray,
    centers: Sequence[tuple[int, int]],
    *,
    exclusion_steps: int = 1,
) -> float:
    """10*log10(selected peak / best cell outside the exclusion neighborhoods).

    ``centers[0]`` is the selected cell; every center gets a Chebyshev ball of
    ``exclusion_steps`` excluded (pass the swap image as a second center on square
    grids). Returns NaN when no finite runner-up remains or a power is non-positive.
    """
    values = np.asarray(surface, dtype=np.float64)
    if not centers:
        raise ValueError("centers must name at least the selected cell")
    peak = values[centers[0]]
    if not np.isfinite(peak) or peak <= 0.0:
        return float("nan")
    eligible = np.isfinite(values)
    for ci, cj in centers:
        i_lo, i_hi = max(ci - exclusion_steps, 0), ci + exclusion_steps + 1
        j_lo, j_hi = max(cj - exclusion_steps, 0), cj + exclusion_steps + 1
        eligible[i_lo:i_hi, j_lo:j_hi] = False
    if not eligible.any():
        return float("nan")
    runner_up = float(values[eligible].max())
    if runner_up <= 0.0:
        return float("nan")
    return float(10.0 * np.log10(peak / runner_up))


# ---------------------------------------------------------------------------------------
# DSP-only configuration (no capture knowledge — enforced by construction and by test)
# ---------------------------------------------------------------------------------------

# ---------------------------------------------------------------------------------------
# Front end: raw cube -> Z (slow time x RX) at one range bin
# ---------------------------------------------------------------------------------------

def validate_project_frame_continuity(
    frame_indices: np.ndarray,
    *,
    frame_period_s: float,
    metadata: Mapping[str, object],
) -> None:
    """Fail closed unless the structural 600-frame acquisition contract is established.

    The repository has no per-frame timestamp vector, so this validation deliberately
    asserts only facts available without inventing a phase-jump threshold: exact decoded
    order/count, nominal timing, decode geometry, file-size divisibility, and zero packet
    drops when that optional acquisition diagnostic is present.
    """
    indices = np.asarray(frame_indices)
    if indices.shape != (600,) or not np.issubdtype(indices.dtype, np.integer):
        raise ValueError("frame_indices must be an integer vector of length 600")
    if not np.array_equal(indices, np.arange(600, dtype=indices.dtype)):
        raise ValueError("frame_indices must be exactly ordered and contiguous 0:600")
    if not np.isclose(float(frame_period_s), 0.05, rtol=0.0, atol=1e-12):
        raise ValueError("nominal frame period must be exactly 0.05 s")
    required = {
        "frame_count": 600,
        "num_chirps_per_frame": 32,
        "num_rx": 4,
        "num_adc_samples": 256,
        "file_size_divisible": True,
    }
    for key, expected in required.items():
        if key not in metadata or metadata[key] != expected:
            raise ValueError(f"required frame metadata {key!r} must equal {expected!r}")
    if "packet_drop_count" in metadata and metadata["packet_drop_count"] != 0:
        raise ValueError("nonzero packet_drop_count breaks structural frame continuity")

def extract_rx_slow_time(
    cube_slice: np.ndarray,
    locked_bin: int,
    *,
    chirp_loop_index: int = 0,
) -> np.ndarray:
    """Form project ``Z`` from fixed chirp-loop 0 at one locked range bin.

    The project adaptation uses frame-to-frame slow time, not Kotte's chirp-to-chirp
    paper simulation. The adapter therefore applies the unnormalised Hann range FFT and
    takes ``range_fft[:, 0, RX0:RX4, locked_bin]``. It never averages chirps. RX ordering
    is preserved and no reference signal enters this operation.

    Parameters
    ----------
    cube_slice : (N_frames, 32, 4, 256) complex
    locked_bin : range FFT bin index.

    Returns
    -------
    Z : complex128 (N_frames, n_rx)
    """
    cube = np.asarray(cube_slice)
    if cube.ndim != 4:
        raise ValueError(f"cube_slice must be 4-D, got shape {cube.shape}")
    if cube.shape[1:] != (32, 4, 256):
        raise ValueError(
            "cube_slice must have shape (frames, 32 chirps, 4 RX, 256 ADC), "
            f"got {cube.shape}"
        )
    if not np.issubdtype(cube.dtype, np.complexfloating):
        raise TypeError(f"cube_slice must have complex dtype, got {cube.dtype}")
    if not np.all(np.isfinite(cube)):
        raise ValueError("cube_slice contains NaN or Inf")
    if type(chirp_loop_index) is not int or chirp_loop_index != 0:
        raise ValueError(
            "M9 uses chirp_loop_index=0 exactly; chirp averaging or another loop is not "
            "an authorised adaptation"
        )
    if type(locked_bin) is not int or not (0 <= locked_bin < 256):
        raise ValueError(f"locked_bin={locked_bin!r} out of range [0, 255]")

    hann = np.hanning(256)
    # Slice the frozen loop before the FFT. This is mathematically identical to taking
    # loop 0 after a separable last-axis FFT and avoids materialising unused chirp FFTs.
    loop_zero = cube[:, 0, :, :]
    range_fft = np.fft.fft(loop_zero * hann[None, None, :], axis=-1)
    return np.asarray(range_fft[:, :, locked_bin], dtype=np.complex128)


def prepare_cpis(
    z: np.ndarray,
    n_c: int = 16,
    *,
    window_frames: int = 600,
    retained_frames: int = 592,
) -> tuple[np.ndarray, int, int]:
    """Retain -> detrend -> split, in the pinned order.

    ``extract N frames -> retain the first n_cpis*n_c -> subtract the per-RX mean over
    the RETAINED support -> split into CPIs``. The discarded tail provably cannot affect
    any result because it is dropped before the mean is taken.

    Returns ``(cpis, frame_start_used, frame_end_used)`` with ``cpis`` shaped
    ``(n_cpis, n_c, n_rx)`` and a half-open ``[start, end)`` frame span.
    """
    z = np.asarray(z)
    if z.ndim != 2 or z.shape != (window_frames, 4):
        raise ValueError(
            f"z must have the project window shape ({window_frames}, 4), got {z.shape}"
        )
    if not np.issubdtype(z.dtype, np.complexfloating):
        raise TypeError(f"z must have complex dtype, got {z.dtype}")
    if n_c != 16 or window_frames != 600 or retained_frames != 592:
        raise ValueError(
            "M9.2 requires 600 frames, first 592 retained, and 16-frame CPIs exactly"
        )
    if retained_frames % n_c != 0:
        raise ValueError("retained_frames must divide exactly into CPIs")
    retained = z[:retained_frames]
    detrended = retained - retained.mean(axis=0, keepdims=True)
    n_cpis = retained_frames // n_c
    return detrended.reshape(n_cpis, n_c, 4), 0, retained_frames


# ---------------------------------------------------------------------------------------
# Aggregation across CPIs
# ---------------------------------------------------------------------------------------

def medoid_of_cpi_estimates(estimates_bpm: np.ndarray) -> int:
    """Index of the 2-D L1 medoid; ties resolve to the lowest CPI index.

    ``estimates_bpm`` is ``(n_cpis, 2)`` holding ``(br_bpm, hr_bpm)`` per CPI. The medoid
    minimizes the summed L1 distance to every other CPI estimate — a member of the set,
    so the reported pair is always one the estimator actually produced (unlike a
    coordinate-wise median, which can invent a pair no CPI selected).
    """
    values = np.asarray(estimates_bpm, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != 2:
        raise ValueError(f"estimates must be (n_cpis, 2), got {values.shape}")
    if not np.all(np.isfinite(values)):
        raise ValueError("medoid requires finite estimates")
    distances = np.abs(values[:, None, :] - values[None, :, :]).sum(axis=(1, 2))
    return int(np.argmin(distances))  # argmin returns the FIRST minimum => lowest index


def _band_grids(config: "KotteJointDopplerConfig") -> tuple[np.ndarray, np.ndarray]:
    step_hz = float(config.grid_step_bpm) / 60.0
    return (
        signed_band_grid_hz(config.breath_band_hz, step_hz),
        signed_band_grid_hz(config.heart_band_hz, step_hz),
    )


def _cpi_surface(
    y_t: np.ndarray,
    *,
    config: "KotteJointDopplerConfig",
    loading_delta: float,
    f1_grid: np.ndarray,
    f2_grid: np.ndarray,
    t_pri_s: float,
) -> tuple[SurfaceResult | None, np.ndarray, int, float, int]:
    """One CPI's regularized Kotte surface and persisted numerical diagnostics."""
    covariance = sample_covariance(y_t)
    if not np.all(np.isfinite(covariance)):
        return (
            None,
            np.full(y_t.shape[0], np.nan),
            0,
            float("nan"),
            CPI_CAUSE_CODES["covariance_nonfinite"],
        )
    unloaded_eigvals = np.linalg.eigvalsh(covariance)[::-1]
    try:
        unloaded_rank = numerical_rank(unloaded_eigvals, config.rank_rtol)
    except CovarianceFailure:
        return (
            None,
            unloaded_eigvals,
            0,
            float("nan"),
            CPI_CAUSE_CODES["covariance_nonfinite"],
        )
    try:
        ctx = CaponContext.from_covariance(
            covariance, loading_delta=loading_delta, rank_rtol=config.rank_rtol
        )
    except CovarianceFailure as exc:
        return (
            None,
            unloaded_eigvals,
            unloaded_rank,
            float("nan"),
            _CAUSE_FROM_REASON.get(exc.reason, 1),
        )
    surface = joint_capon_surface(
        ctx,
        f1_grid,
        f2_grid,
        n_c=y_t.shape[0],
        t_pri_s=t_pri_s,
        condition_mask_threshold=config.condition_mask_threshold,
    )
    cause = (
        CPI_CAUSE_CODES["all_masked"]
        if surface.masked.all()
        else CPI_CAUSE_CODES["valid"]
    )
    return surface, ctx.eigvals, ctx.rank, ctx.delta_bar, cause


@dataclass(frozen=True)
class PairSelection:
    """One canonical band-labelled pair and its retained raw signed representative."""

    breath_hz: float
    heart_hz: float
    raw_breath_hz: float
    raw_heart_hz: float
    margin_db: float
    breath_index: int
    heart_index: int


def _select_from_surface(
    surface: np.ndarray, f1_grid: np.ndarray, f2_grid: np.ndarray
) -> PairSelection:
    """Canonicalize signed aliases before computing the runner-up margin."""
    collapsed = alias_collapse(surface, f1_grid, f2_grid)
    i, j = argmax_masked(collapsed.objective)
    margin = pair_margin_db(collapsed.objective, [(i, j)], exclusion_steps=1)
    breath_hz = float(collapsed.abs_f1_hz[i])
    heart_hz = float(collapsed.abs_f2_hz[j])
    return PairSelection(
        breath_hz=breath_hz,
        heart_hz=heart_hz,
        raw_breath_hz=float(collapsed.sign_f1[i, j]) * breath_hz,
        raw_heart_hz=float(collapsed.sign_f2[i, j]) * heart_hz,
        margin_db=float(margin),
        breath_index=i,
        heart_index=j,
    )


def estimate_window(
    z: np.ndarray,
    *,
    config: "KotteJointDopplerConfig",
    arm: "KotteArm",
    fs_hz: float,
) -> tuple[dict, dict]:
    """Evaluate one window for one arm. Returns ``(native_result, evidence_arrays)``.

    Missing concepts are ABSENT keys, never sentinel values (plan section "Native dict
    per arm"). A window fails closed: any invalid CPI yields ``dsp_failed`` because
    ``min_valid_cpi_fraction`` is exactly 1.0.
    """
    if not np.isclose(float(fs_hz), 1.0 / config.frame_period_s, rtol=0.0, atol=1e-12):
        raise ValueError(
            f"M9 frame rate must be {1.0 / config.frame_period_s} Hz, got {fs_hz}"
        )
    if arm not in config.arms:
        raise ValueError(f"arm {arm.arm_id!r} is not declared by this M9 configuration")
    if arm.estimator_form != "cpi_medoid" or arm.n_c != config.cpi_frames:
        raise ValueError("M9.2 authorizes only the 16-frame per-CPI medoid estimator")
    t_pri_s = config.frame_period_s
    f1_grid, f2_grid = _band_grids(config)
    cpis, frame_start, frame_end = prepare_cpis(
        z,
        arm.n_c,
        window_frames=config.window_frames,
        retained_frames=config.retained_frames,
    )
    n_cpis = cpis.shape[0]
    n_rx = cpis.shape[2]
    if n_cpis != config.cpis_per_window:
        raise ValueError(
            f"expected {config.cpis_per_window} CPIs, formed {n_cpis}"
        )

    base: dict[str, object] = {
        "arm_id": arm.arm_id,
        "estimator_form": arm.estimator_form,
        "loading_delta": float(arm.loading_delta),
        "n_cpis_expected": int(n_cpis),
        "frame_start_used": int(frame_start),
        "frame_end_used": int(frame_end),
        "band_edge_low": float(config.breath_band_hz[0]),
        "band_edge_high": float(config.heart_band_hz[1]),
        "selection_method": "regularized_kotte_power_cpi_medoid",
        "objective_name": "regularized_kotte_power",
        "frequency_units": "Hz",
        "slow_time_period_s": float(t_pri_s),
    }

    surfaces: list[np.ndarray] = []
    rconds: list[np.ndarray] = []
    masks: list[np.ndarray] = []
    eig_rows: list[np.ndarray] = []
    ranks: list[int] = []
    delta_bars: list[float] = []
    causes: list[int] = []
    masked_fractions: list[float] = []
    for k in range(n_cpis):
        surface, eigvals, rank, delta_bar, cause = _cpi_surface(
            cpis[k], config=config, loading_delta=arm.loading_delta,
            f1_grid=f1_grid, f2_grid=f2_grid, t_pri_s=t_pri_s,
        )
        eig_rows.append(np.asarray(eigvals, dtype=np.float64))
        ranks.append(int(rank))
        delta_bars.append(float(delta_bar))
        causes.append(int(cause))
        if surface is None:
            surfaces.append(np.full((f1_grid.size, f2_grid.size), np.nan))
            rconds.append(np.zeros((f1_grid.size, f2_grid.size), dtype=np.float64))
            masks.append(np.ones((f1_grid.size, f2_grid.size), dtype=bool))
            masked_fractions.append(1.0)
        else:
            surfaces.append(surface.objective)
            rconds.append(surface.rcond)
            masks.append(surface.masked)
            masked_fractions.append(float(surface.masked_fraction))

    cause_array = np.asarray(causes, dtype=np.int16)
    validity = cause_array == CPI_CAUSE_CODES["valid"]
    n_valid = int(validity.sum())
    evidence = {
        "z": freeze_array(z),
        "loading_delta": freeze_array(np.asarray(arm.loading_delta, dtype=np.float64)),
        "objective_name": freeze_array(np.asarray("regularized_kotte_power")),
        "cpi_eigvals": freeze_array(np.stack(eig_rows)),
        "cpi_ranks": freeze_array(np.asarray(ranks, dtype=np.int32)),
        "cpi_delta_bar": freeze_array(np.asarray(delta_bars, dtype=np.float64)),
        "cpi_valid": freeze_array(validity),
        "cpi_cause_codes": freeze_array(cause_array),
        "cpi_masked_fraction": freeze_array(np.asarray(masked_fractions)),
        "f1_grid_hz": freeze_array(f1_grid),
        "f2_grid_hz": freeze_array(f2_grid),
        "cpi_regularized_kotte_power": freeze_array(np.stack(surfaces)),
        "cpi_constraint_rcond": freeze_array(np.stack(rconds)),
        "cpi_constraint_mask": freeze_array(np.stack(masks)),
    }
    base.update(
        n_cpis_valid=n_valid,
        rank_rt=int(np.max(ranks)) if ranks else 0,
        n_snapshots=int(n_rx),
        masked_fraction_mean=float(np.mean(masked_fractions)),
    )

    if n_valid < n_cpis:  # min_valid_cpi_fraction is exactly 1.0 -> fail closed
        first_bad = int(np.argmin(validity))
        base.update(
            br_valid=False, hr_valid=False, rej_reason="invalid_cpi",
            failing_cpi_index=first_bad,
            failing_cpi_cause_code=int(cause_array[first_bad]),
        )
        return base, evidence

    # Per-CPI estimates are canonicalized before the fixed 2-D L1 medoid.
    per_cpi = np.empty((n_cpis, 2), dtype=np.float64)
    per_cpi_raw_signed = np.empty((n_cpis, 2), dtype=np.float64)
    per_cpi_margin = np.empty(n_cpis, dtype=np.float64)
    for k in range(n_cpis):
        selected = _select_from_surface(surfaces[k], f1_grid, f2_grid)
        per_cpi[k] = (selected.breath_hz * 60.0, selected.heart_hz * 60.0)
        per_cpi_raw_signed[k] = (selected.raw_breath_hz, selected.raw_heart_hz)
        per_cpi_margin[k] = selected.margin_db
    medoid_index = medoid_of_cpi_estimates(per_cpi)
    evidence["cpi_estimates_bpm"] = freeze_array(per_cpi)
    evidence["cpi_raw_signed_pairs_hz"] = freeze_array(per_cpi_raw_signed)
    evidence["cpi_pair_margin_db"] = freeze_array(per_cpi_margin)
    evidence["medoid_index"] = int(medoid_index)
    base.update(
        br_valid=True,
        hr_valid=True,
        br_bpm=float(per_cpi[medoid_index, 0]),
        hr_raw=float(per_cpi[medoid_index, 1]),
        # Window diagnostics are the MEDOID CPI's, not an average over CPIs.
        pair_margin_db=float(per_cpi_margin[medoid_index]),
        medoid_cpi_index=int(medoid_index),
        selected_raw_signed_hz=[
            float(per_cpi_raw_signed[medoid_index, 0]),
            float(per_cpi_raw_signed[medoid_index, 1]),
        ],
        cpi_iqr_br_bpm=float(np.subtract(*np.percentile(per_cpi[:, 0], [75, 25]))),
        cpi_iqr_hr_bpm=float(np.subtract(*np.percentile(per_cpi[:, 1], [75, 25]))),
        selected_hz=[
            float(per_cpi[medoid_index, 0]) / 60.0,
            float(per_cpi[medoid_index, 1]) / 60.0,
        ],
    )
    return base, evidence


@dataclass(frozen=True)
class KotteArm:
    arm_id: str
    n_c: int
    estimator_form: str
    loading_delta: float

    def __post_init__(self) -> None:
        if not self.arm_id or any(c in self.arm_id for c in r'/\:*?"<>| '):
            raise ValueError(f"arm_id {self.arm_id!r} must be non-empty and path-safe")
        if type(self.n_c) is not int or self.n_c < 2:
            raise ValueError(f"n_c must be an int >= 2, got {self.n_c!r}")
        if self.estimator_form not in ESTIMATOR_FORMS:
            raise ValueError(
                f"estimator_form {self.estimator_form!r} not in {ESTIMATOR_FORMS}"
            )
        if not (float(self.loading_delta) > 0.0):
            raise ValueError(f"loading_delta must be positive, got {self.loading_delta}")

    def to_plain(self) -> dict:
        return {
            "arm_id": self.arm_id,
            "n_c": self.n_c,
            "estimator_form": self.estimator_form,
            "loading_delta": float(self.loading_delta),
        }


@dataclass(frozen=True)
class KotteJointDopplerConfig:
    """DSP-ONLY — contains no captures, subjects, bins, windows, or reference files."""

    breath_band_hz: tuple[float, float]
    heart_band_hz: tuple[float, float]
    grid_step_bpm: float
    condition_mask_threshold: float
    rank_rtol: float
    arms: tuple[KotteArm, ...]
    min_valid_cpi_fraction: float
    frame_period_s: float
    window_frames: int
    retained_frames: int
    cpi_frames: int
    cpis_per_window: int
    tail_policy: str
    mean_removal_scope: str = "retained_support"
    slow_time_selection: str = "fixed_chirp_loop_0"
    evidence_schema_version: int = 1

    #: The only stage_a keys this config may consume. Anything else in the section
    #: (captures, candidate_bins, primary_arm_id, ...) is runner input
    #: and MUST NOT influence estimator identity.
    DSP_KEYS = (
        "breath_band_hz",
        "heart_band_hz",
        "grid_step_bpm",
        "condition_mask_threshold",
        "rank_rtol",
        "arms",
        "min_valid_cpi_fraction",
        "frame_period_s",
        "window_frames",
        "retained_frames",
        "cpi_frames",
        "cpis_per_window",
        "tail_policy",
        "mean_removal_scope",
        "slow_time_selection",
        "evidence_schema_version",
    )

    def __post_init__(self) -> None:
        lo_b, hi_b = self.breath_band_hz
        lo_h, hi_h = self.heart_band_hz
        if not (0.0 < lo_b < hi_b <= lo_h < hi_h):
            raise ValueError(
                f"bands must satisfy 0 < breath < heart, got {self.breath_band_hz} "
                f"and {self.heart_band_hz}"
            )
        if self.min_valid_cpi_fraction != 1.0:
            raise ValueError(
                "min_valid_cpi_fraction must be exactly 1.0 (plan: any invalid CPI "
                f"fails the window), got {self.min_valid_cpi_fraction!r}"
            )
        if self.mean_removal_scope != "retained_support":
            raise ValueError(
                f"mean_removal_scope must be 'retained_support', got "
                f"{self.mean_removal_scope!r}"
            )
        if self.slow_time_selection != "fixed_chirp_loop_0":
            raise ValueError(
                "slow_time_selection must be 'fixed_chirp_loop_0', got "
                f"{self.slow_time_selection!r}"
            )
        if not np.isclose(self.frame_period_s, 0.05, rtol=0.0, atol=1e-12):
            raise ValueError(f"frame_period_s must be exactly 0.05 s, got {self.frame_period_s}")
        if (
            self.window_frames,
            self.retained_frames,
            self.cpi_frames,
            self.cpis_per_window,
        ) != (600, 592, 16, 37):
            raise ValueError(
                "M9.2 window contract must be 600 frames -> first 592 -> 37 CPIs of 16"
            )
        if self.tail_policy != "drop_frames_592_to_599_before_mean_removal":
            raise ValueError(f"unexpected tail_policy {self.tail_policy!r}")
        if not self.arms:
            raise ValueError("at least one arm is required")
        arm_ids = [arm.arm_id for arm in self.arms]
        if len(set(arm_ids)) != len(arm_ids):
            raise ValueError(f"duplicate arm_id in {arm_ids}")
        if not (self.grid_step_bpm > 0.0):
            raise ValueError(f"grid_step_bpm must be positive, got {self.grid_step_bpm}")

    @classmethod
    def from_stage_a(cls, stage_a: Mapping[str, object]) -> "KotteJointDopplerConfig":
        """Build from the config file's ``stage_a`` section, consuming ONLY DSP keys."""
        arms = tuple(
            KotteArm(
                arm_id=str(entry["arm_id"]),
                n_c=int(entry["n_c"]),
                estimator_form=str(entry["estimator_form"]),
                loading_delta=float(entry["loading_delta"]),
            )
            for entry in stage_a["arms"]  # type: ignore[index]
        )
        return cls(
            breath_band_hz=tuple(float(v) for v in stage_a["breath_band_hz"]),  # type: ignore[arg-type]
            heart_band_hz=tuple(float(v) for v in stage_a["heart_band_hz"]),  # type: ignore[arg-type]
            grid_step_bpm=float(stage_a["grid_step_bpm"]),  # type: ignore[arg-type]
            condition_mask_threshold=float(stage_a["condition_mask_threshold"]),  # type: ignore[arg-type]
            rank_rtol=float(stage_a["rank_rtol"]),  # type: ignore[arg-type]
            arms=arms,
            min_valid_cpi_fraction=float(stage_a["min_valid_cpi_fraction"]),  # type: ignore[arg-type]
            frame_period_s=float(stage_a["frame_period_s"]),  # type: ignore[arg-type]
            window_frames=int(stage_a["window_frames"]),  # type: ignore[arg-type]
            retained_frames=int(stage_a["retained_frames"]),  # type: ignore[arg-type]
            cpi_frames=int(stage_a["cpi_frames"]),  # type: ignore[arg-type]
            cpis_per_window=int(stage_a["cpis_per_window"]),  # type: ignore[arg-type]
            tail_policy=str(stage_a["tail_policy"]),
            mean_removal_scope=str(stage_a["mean_removal_scope"]),
            slow_time_selection=str(stage_a["slow_time_selection"]),
            evidence_schema_version=int(stage_a["evidence_schema_version"]),  # type: ignore[arg-type]
        )

    def shared_dsp_plain(self) -> dict:
        """Every shared DSP field (everything except the arm list)."""
        payload = {}
        for field_def in fields(self):
            if field_def.name == "arms":
                continue
            payload[field_def.name] = canonical_plain(getattr(self, field_def.name))
        return payload

    def run_config_hash(self, arm: KotteArm) -> str:
        """Identity of one arm = shared resolved DSP settings + this arm's fields."""
        if arm not in self.arms:
            raise ValueError(f"arm {arm.arm_id!r} is not part of this config")
        return canonical_hash({"shared": self.shared_dsp_plain(), "arm": arm.to_plain()})

    def suite_config_hash(self) -> str:
        return canonical_hash(
            {
                "shared": self.shared_dsp_plain(),
                "arms": [arm.to_plain() for arm in self.arms],
            }
        )


# ---------------------------------------------------------------------------------------
# The suite: every declared arm evaluated once per window
# ---------------------------------------------------------------------------------------

KOTTE_ESTIMATOR_ID = "kotte_joint_doppler_v1"


class KotteEstimatorSuite:
    """Evaluates one window and returns exactly its declared arms.

    Satisfies `src.m4.estimator_suite.WindowEstimatorSuite`. The extracted ``Z`` is
    computed **once** per window and shared by every arm through
    ``shared_signal_hash``; the optional M8-specific spec fields stay ``None``.
    """

    suite_id = "kotte_joint_doppler_suite_v1"

    def __init__(self, config: KotteJointDopplerConfig) -> None:
        if not isinstance(config, KotteJointDopplerConfig):
            raise TypeError("config must be a KotteJointDopplerConfig")
        self._config = config
        self.suite_config_hash = config.suite_config_hash()
        self.arm_specs = tuple(
            EstimatorArmSpec(
                arm_id=arm.arm_id,
                estimator_id=KOTTE_ESTIMATOR_ID,
                run_config_hash=config.run_config_hash(arm),
                harmonic_count=None,
                suppression_profile=None,
                outcome_classifier_id=None,
            )
            for arm in config.arms
        )
        validate_arm_specs(self.arm_specs)
        self.outcome_classifiers: dict = {}

    @property
    def config(self) -> KotteJointDopplerConfig:
        return self._config

    def __call__(self, frames, locked_bin: int, fs: float) -> SuiteWindowResult:
        z = extract_rx_slow_time(frames, locked_bin)
        frozen_z = freeze_array(z)
        signal_hash = canonical_hash(
            {
                "z_sha256": sha256(np.ascontiguousarray(z).tobytes()).hexdigest(),
                "locked_bin": int(locked_bin),
                "fs_hz": float(fs),
            }
        )
        natives: dict[str, dict] = {}
        for arm in self._config.arms:
            native, _evidence = estimate_window(
                frozen_z, config=self._config, arm=arm, fs_hz=float(fs)
            )
            native["shared_signal_hash"] = signal_hash
            natives[arm.arm_id] = native
        result = SuiteWindowResult(
            shared_evidence={
                "suite_id": self.suite_id,
                "suite_config_hash": self.suite_config_hash,
                "fs_hz": float(fs),
                "locked_bin": int(locked_bin),
                "shared_signal_hash": signal_hash,
                "n_frames": int(z.shape[0]),
                "n_rx": int(z.shape[1]),
            },
            arm_native_results=natives,
        )
        validate_returned_arms(self.arm_specs, result)
        return result

    def window_evidence(self, frames, locked_bin: int, fs: float) -> dict[str, dict]:
        """Per-arm evidence arrays for the NPZ writer (kept out of the native payloads)."""
        z = freeze_array(extract_rx_slow_time(frames, locked_bin))
        return {
            arm.arm_id: estimate_window(
                z, config=self._config, arm=arm, fs_hz=float(fs)
            )[1]
            for arm in self._config.arms
        }
