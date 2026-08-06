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
from src.m4.estimator_suite import canonical_plain

__all__ = [
    "AllMaskedError",
    "CaponContext",
    "CollapsedSurface",
    "CovarianceFailure",
    "KotteArm",
    "KotteJointDopplerConfig",
    "SurfaceResult",
    "alias_collapse",
    "argmax_masked",
    "beta_surface",
    "canonical_hash",
    "capon_power_at",
    "joint_beta_at",
    "joint_capon_surface",
    "make_grid_hz",
    "numerical_rank",
    "pair_margin_db",
    "remove_per_rx_mean",
    "sample_covariance",
    "signed_band_grid_hz",
    "spatial_steering",
    "temporal_steering",
]

ESTIMATOR_FORMS = ("cpi_medoid", "mean_surface", "pooled")


class CovarianceFailure(Exception):
    """Covariance cannot support the requested inverse (rank 0, rank-deficient
    unloaded, nonfinite entries, or non-positive loaded spectrum)."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class AllMaskedError(Exception):
    """Every grid cell was masked; selection is impossible (``dsp_failed``)."""


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
    # c1[i] = a1_i^H R^-1 Y a_theta* / n_r  (and c2 likewise).
    x1 = ctx.solve(a1)
    x2 = ctx.solve(a2)
    c1 = (x1.conj().T @ y) @ a_theta.conj() / n_r
    c2 = (x2.conj().T @ y) @ a_theta.conj() / n_r

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
    ctx: CaponContext, f1_hz: float, f2_hz: float, *, n_c: int, t_pri_s: float
) -> float:
    """Direct loop-form ``Re(1^T H^-1 1)`` at a single pair (the vectorized == loop
    reference; also the per-cell path for oracle checks)."""
    a_pair = temporal_steering(np.array([f1_hz, f2_hz]), n_c, t_pri_s)
    x = ctx.solve(a_pair)
    h = a_pair.conj().T @ x
    ones = np.ones(2, dtype=np.complex128)
    return float((ones @ np.linalg.solve(h, ones)).real)


def joint_beta_at(
    ctx: CaponContext,
    y_t: np.ndarray,
    f1_hz: float,
    f2_hz: float,
    *,
    theta0_rad: float,
    t_pri_s: float,
) -> complex:
    """Direct loop-form eq (25)+(26): ``w = R^-1 A2 H^-1 1``, ``beta_hat = w^H Y a*(t)/n_r``."""
    y = np.asarray(y_t)
    n_c, n_r = y.shape
    a_pair = temporal_steering(np.array([f1_hz, f2_hz]), n_c, t_pri_s)
    x = ctx.solve(a_pair)
    h = a_pair.conj().T @ x
    w = x @ np.linalg.solve(h, np.ones(2, dtype=np.complex128))
    a_theta = spatial_steering(theta0_rad, n_r)
    return complex((w.conj() @ y) @ a_theta.conj() / n_r)


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

    The representative of each collapsed cell is the maximizing sign combination in
    the deterministic preference order (+,+), (+,-), (-,+), (-,-); ties resolve to the
    earliest in that order.
    """

    objective: np.ndarray
    abs_f1_hz: np.ndarray
    abs_f2_hz: np.ndarray
    sign_f1: np.ndarray
    sign_f2: np.ndarray


_SIGN_ORDER = ((1, 1), (1, -1), (-1, 1), (-1, -1))


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
    mean_removal_scope: str = "retained_support"
    chirp_aggregation: str = "coherent_mean"
    evidence_schema_version: int = 1

    #: The only stage_a keys this config may consume. Anything else in the section
    #: (captures, candidate_bins, window_frames, primary_arm_id, ...) is runner input
    #: and MUST NOT influence estimator identity.
    DSP_KEYS = (
        "breath_band_hz",
        "heart_band_hz",
        "grid_step_bpm",
        "condition_mask_threshold",
        "rank_rtol",
        "arms",
        "min_valid_cpi_fraction",
        "mean_removal_scope",
        "chirp_aggregation",
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
        if self.chirp_aggregation != "coherent_mean":
            raise ValueError(
                f"chirp_aggregation must be 'coherent_mean', got "
                f"{self.chirp_aggregation!r}"
            )
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
            mean_removal_scope=str(stage_a["mean_removal_scope"]),
            chirp_aggregation=str(stage_a["chirp_aggregation"]),
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
