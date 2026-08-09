"""Core tests for src/m9/kotte_core.py (M9 step 1, plans/m9_kotte_plan.md test plan).

Covers the DSP maths only — the aggregation contract (medoid, partial-CPI semantics,
tail/detrend order) and the suite arrive with plan step 3 and extend this file.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import numpy as np
import pytest
from scipy.special import jv
import yaml

from src.m9.kotte_core import (
    CPI_CAUSE_CODES,
    AllMaskedError,
    CaponContext,
    ConstraintMatrixFailure,
    CovarianceFailure,
    KotteArm,
    KotteEstimatorSuite,
    KotteJointDopplerConfig,
    alias_collapse,
    argmax_masked,
    beta_surface,
    capon_power_at,
    estimate_window,
    extract_rx_slow_time,
    joint_beta_at,
    joint_capon_surface,
    joint_weight_at,
    make_grid_hz,
    medoid_of_cpi_estimates,
    numerical_rank,
    pair_margin_db,
    prepare_cpis,
    remove_per_rx_mean,
    sample_covariance,
    signed_band_grid_hz,
    spatial_steering,
    temporal_steering,
    validate_project_frame_continuity,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "experiments" / "m9_kotte" / "config.yaml"

T_PRI = 0.05
N_C = 16
RANK_RTOL = 1e-9
MASK = 1e-8


def _steer(f: float) -> np.ndarray:
    return temporal_steering(np.array([f]), N_C, T_PRI)[:, 0]


def _two_tone(beta1, beta2, f1, f2, theta0_rad, n_r, sigma2, seed):
    rng = np.random.default_rng(seed)
    s = beta1 * _steer(f1) + beta2 * _steer(f2)
    noise = np.sqrt(sigma2 / 2.0) * (
        rng.standard_normal((N_C, n_r)) + 1j * rng.standard_normal((N_C, n_r))
    )
    return np.outer(s, spatial_steering(theta0_rad, n_r)) + noise, s


def _ctx(r, delta=None):
    return CaponContext.from_covariance(r, loading_delta=delta, rank_rtol=RANK_RTOL)


# ---------------------------------------------------------------------------------------
# Covariance divisor and rank rule
# ---------------------------------------------------------------------------------------


def test_sample_covariance_divisor_is_snapshot_count():
    # Kotte Algorithm 1, PDF printed p. 117: R_t = Y_t Y_t^H / N_R.
    rng = np.random.default_rng(0)
    y = rng.standard_normal((N_C, 7)) + 1j * rng.standard_normal((N_C, 7))
    expected = y @ y.conj().T / 7
    np.testing.assert_allclose(sample_covariance(y), expected, rtol=0, atol=0)


def test_temporal_steering_uses_physical_hz_and_pri_with_expected_shapes():
    # Kotte Eq. (7), PDF printed p. 113, carries i*T_PRI. The printed steering
    # shorthand below Eq. (16), p. 115, omits T_PRI; M9 declares physical Hz.
    frequencies_hz = np.array([-2.0, 1.5])
    steering = temporal_steering(frequencies_hz, N_C, T_PRI)
    expected = np.empty((N_C, 2), dtype=np.complex128)
    for chirp_index in range(N_C):
        for frequency_index, frequency_hz in enumerate(frequencies_hz):
            expected[chirp_index, frequency_index] = np.exp(
                2j * np.pi * frequency_hz * chirp_index * T_PRI
            )
    assert steering.shape == (16, 2)
    np.testing.assert_allclose(steering, expected, rtol=0.0, atol=3e-15)
    assert spatial_steering(-0.3, 20).shape == (20,)


def test_eq23_uses_ordinary_transpose_not_hermitian_transpose():
    # Kotte Eq. (23), PDF printed p. 116: Y_t(kappa)=Y^T(kappa), so the paper's
    # (n_R,N_c) matrix becomes (N_c,n_R) without conjugating complex samples.
    receive_index = np.arange(20, dtype=np.float64)[:, None]
    slow_time_index = np.arange(16, dtype=np.float64)[None, :]
    y = (receive_index + 2.0 * slow_time_index) + 1j * (
        3.0 * receive_index - slow_time_index - 0.5
    )
    y_t = y.T
    assert y.shape == (20, 16)
    assert y_t.shape == (16, 20)
    for chirp_index in (0, 7, 15):
        for rx_index in (0, 9, 19):
            assert y_t[chirp_index, rx_index] == y[rx_index, chirp_index]
    assert not np.array_equal(y_t, y.conj().T)


def test_numerical_rank_basic_and_scale_invariance():
    eigvals = np.array([1.0, 0.5, 1e-12, 0.0])
    assert numerical_rank(eigvals, RANK_RTOL) == 2
    assert numerical_rank(eigvals * 1e7, RANK_RTOL) == 2
    assert numerical_rank(eigvals * 1e-7, RANK_RTOL) == 2


def test_numerical_rank_zero_scale_branch():
    assert numerical_rank(np.array([0.0, -1e-3]), RANK_RTOL) == 0
    assert numerical_rank(np.array([-1.0, -2.0]), RANK_RTOL) == 0


def test_numerical_rank_rejects_nonfinite():
    with pytest.raises(CovarianceFailure) as excinfo:
        numerical_rank(np.array([1.0, np.nan]), RANK_RTOL)
    assert excinfo.value.reason == "nonfinite_eigenvalues"
    with pytest.raises(CovarianceFailure):
        numerical_rank(np.array([np.inf, 1.0]), RANK_RTOL)


def test_context_rank_zero_covariance_fails():
    with pytest.raises(CovarianceFailure) as excinfo:
        _ctx(np.zeros((4, 4), dtype=complex))
    assert excinfo.value.reason == "rank_zero"


def test_context_rejects_nonfinite_covariance():
    r = np.eye(4, dtype=complex)
    r[0, 0] = np.nan
    with pytest.raises(CovarianceFailure) as excinfo:
        _ctx(r)
    assert excinfo.value.reason == "nonfinite_covariance"


def test_mean_removal_drops_rank_and_unloaded_refuses():
    y, _ = _two_tone(1.0, 0.5, 2.0, 1.0, 0.0, 20, 1.0, seed=3)
    z = remove_per_rx_mean(y)
    r = sample_covariance(z)
    # R . 1 = 0 exactly up to float error -> numerical rank <= N_C - 1.
    ones = np.ones(N_C) / np.sqrt(N_C)
    assert np.linalg.norm(r @ ones) < 1e-10 * np.linalg.norm(r)
    eigvals = np.linalg.eigvalsh(r)
    assert numerical_rank(eigvals, RANK_RTOL) <= N_C - 1
    with pytest.raises(CovarianceFailure) as excinfo:
        _ctx(r)
    assert excinfo.value.reason == "rank_deficient_unloaded"
    # The loaded path proceeds on the same covariance.
    ctx = _ctx(r, delta=1e-2)
    assert ctx.delta_bar > 0.0


# ---------------------------------------------------------------------------------------
# Verified identities and corollaries
# ---------------------------------------------------------------------------------------


def _direct_w(r_inv_apply, f1, f2):
    a_pair = temporal_steering(np.array([f1, f2]), N_C, T_PRI)
    x = r_inv_apply(a_pair)
    h = a_pair.conj().T @ x
    return x @ np.linalg.solve(h, np.ones(2, dtype=complex)), h


def test_identity_whrw_equals_ones_hinv_ones():
    # Algorithm 1, PDF p. 117, maximizes w^H R_t w. Substituting Eq. (25),
    # PDF p. 116, gives the independently evaluated 1^H H^-1 1 identity.
    y, _ = _two_tone(1.0, 0.5, 2.0, 1.0, 0.3, 20, 1.0, seed=5)
    r = sample_covariance(y)
    ctx = _ctx(r)
    w, _h = _direct_w(ctx.solve, 3.3, -1.7)
    lhs = float((w.conj() @ r @ w).real)
    rhs = capon_power_at(ctx, 3.3, -1.7, n_c=N_C, t_pri_s=T_PRI)
    np.testing.assert_allclose(lhs, rhs, rtol=1e-9)


def test_eq25_weight_satisfies_hermitian_two_frequency_constraint():
    # Eqs. (24)-(25), PDF p. 116: A=(N_c,2), H=A^H R_t^-1 A=(2,2),
    # w=(N_c,), and the printed constraint is w^H A=[1,1].
    y, _ = _two_tone(1.0, 0.4j, 2.0, -1.0, -0.2, 20, 1.3, seed=105)
    ctx = _ctx(sample_covariance(y))
    weight, constraint_gram = joint_weight_at(
        ctx, 2.0, -1.0, n_c=N_C, t_pri_s=T_PRI,
        condition_mask_threshold=MASK,
    )
    frequency_constraint_matrix = np.column_stack((_steer(2.0), _steer(-1.0)))
    assert weight.shape == (16,)
    assert constraint_gram.shape == (2, 2)
    residual = weight.conj() @ frequency_constraint_matrix - np.ones(2)
    assert np.linalg.norm(residual) < 1e-10


def test_eq26_matches_independent_hand_derived_expression_and_requires_conjugation():
    # Eq. (26), PDF p. 117: beta_hat=w^H Y_t a(theta_hat)* /(a^H a).
    # This oracle deliberately derives w with raw NumPy solves, not a production helper.
    rng = np.random.default_rng(106)
    matrix = rng.standard_normal((N_C, N_C)) + 1j * rng.standard_normal((N_C, N_C))
    covariance = matrix @ matrix.conj().T + 0.5 * np.eye(N_C)
    y_t = rng.standard_normal((N_C, 20)) + 1j * rng.standard_normal((N_C, 20))
    theta_rad = 0.37
    frequency_constraint_matrix = np.column_stack((_steer(2.0), _steer(-1.0)))
    covariance_solutions = np.linalg.solve(covariance, frequency_constraint_matrix)
    constraint_gram = frequency_constraint_matrix.conj().T @ covariance_solutions
    expected_weight = covariance_solutions @ np.linalg.solve(
        constraint_gram, np.ones(2, dtype=np.complex128)
    )
    spatial = np.exp(
        1j * np.pi * np.sin(theta_rad) * np.arange(20, dtype=np.float64)
    )
    expected = (
        expected_weight.conj() @ y_t @ spatial.conj() / (spatial.conj() @ spatial)
    )
    observed = joint_beta_at(
        _ctx(covariance), y_t, 2.0, -1.0, theta0_rad=theta_rad, t_pri_s=T_PRI,
        condition_mask_threshold=MASK,
    )
    np.testing.assert_allclose(observed, expected, rtol=1e-11, atol=1e-11)
    wrong_without_spatial_conjugation = (
        expected_weight.conj() @ y_t @ spatial / (spatial.conj() @ spatial)
    )
    assert not np.isclose(observed, wrong_without_spatial_conjugation)


def test_identity_unloaded_projection_power():
    # Unloaded: ||w^H Y||^2 / M == 1^T H^-1 1 with R the sample covariance of Y.
    y, _ = _two_tone(1.0, 0.5, 2.0, 1.0, 0.3, 20, 1.0, seed=6)
    r = sample_covariance(y)
    ctx = _ctx(r)
    w, _ = _direct_w(ctx.solve, 2.0, 1.0)
    lhs = float(np.sum(np.abs(w.conj() @ y) ** 2) / y.shape[1])
    rhs = capon_power_at(ctx, 2.0, 1.0, n_c=N_C, t_pri_s=T_PRI)
    np.testing.assert_allclose(lhs, rhs, rtol=1e-9)


def test_identity_loaded_decomposition():
    # Loaded: 1^T H_loaded^-1 1 == w^H R_sample w + delta_bar ||w||^2 (w from R_loaded).
    y, _ = _two_tone(1.0, 0.5, 2.0, 1.0, 0.3, 4, 1.0, seed=7)
    r = sample_covariance(y)
    ctx = _ctx(r, delta=1e-2)
    w, _ = _direct_w(ctx.solve, 2.0, 1.0)
    lhs = capon_power_at(ctx, 2.0, 1.0, n_c=N_C, t_pri_s=T_PRI)
    rhs = float((w.conj() @ r @ w).real + ctx.delta_bar * np.vdot(w, w).real)
    np.testing.assert_allclose(lhs, rhs, rtol=1e-9)


def test_corollary_scale_invariance_of_argmax_under_trace_relative_loading():
    y, _ = _two_tone(1.0, 0.5, 2.0, 1.0, 0.3, 4, 1.0, seed=8)
    r = sample_covariance(y)
    grid = make_grid_hz(-5.0, 5.0, 0.25)
    surf_1 = joint_capon_surface(
        _ctx(r, delta=1e-2), grid, grid, n_c=N_C, t_pri_s=T_PRI,
        condition_mask_threshold=MASK,
    )
    surf_c = joint_capon_surface(
        _ctx(1e6 * r, delta=1e-2), grid, grid, n_c=N_C, t_pri_s=T_PRI,
        condition_mask_threshold=MASK,
    )
    assert argmax_masked(surf_1.objective) == argmax_masked(surf_c.objective)
    ratio = surf_c.objective / surf_1.objective
    finite = np.isfinite(ratio)
    np.testing.assert_allclose(ratio[finite], 1e6, rtol=1e-6)


def test_corollary_whole_column_phase_offsets_exactly_invariant():
    y, _ = _two_tone(1.0, 0.5, 2.0, 1.0, 0.3, 4, 1.0, seed=9)
    phases = np.exp(1j * np.array([0.3, -1.1, 2.2, 0.7]))
    y_shifted = y * phases[None, :]
    r_a = sample_covariance(y)
    r_b = sample_covariance(y_shifted)
    grid = make_grid_hz(-5.0, 5.0, 0.5)
    surf_a = joint_capon_surface(
        _ctx(r_a, delta=1e-2), grid, grid, n_c=N_C, t_pri_s=T_PRI,
        condition_mask_threshold=MASK,
    )
    surf_b = joint_capon_surface(
        _ctx(r_b, delta=1e-2), grid, grid, n_c=N_C, t_pri_s=T_PRI,
        condition_mask_threshold=MASK,
    )
    finite = np.isfinite(surf_a.objective)
    np.testing.assert_allclose(
        surf_b.objective[finite], surf_a.objective[finite], rtol=1e-10
    )


def test_gain_imbalance_algebraic_non_invariance_counterexample():
    # Whole-column real gains change the finite-sample surface: document with a
    # concrete counterexample rather than assert a false invariance.
    y, _ = _two_tone(1.0, 0.5, 2.0, 1.0, 0.3, 4, 1.0, seed=10)
    gains = np.array([1.0, 0.5, 2.0, 1.5])
    r_a = sample_covariance(y)
    r_b = sample_covariance(y * gains[None, :])
    ctx_a = _ctx(r_a, delta=1e-2)
    ctx_b = _ctx(r_b, delta=1e-2)
    v_a = capon_power_at(ctx_a, 2.0, 1.0, n_c=N_C, t_pri_s=T_PRI)
    v_b = capon_power_at(ctx_b, 2.0, 1.0, n_c=N_C, t_pri_s=T_PRI)
    trace_ratio = float(np.trace(r_b).real / np.trace(r_a).real)
    assert not np.isclose(v_b / v_a, trace_ratio, rtol=1e-3), (
        "expected finite-sample non-invariance under per-RX gain imbalance"
    )


# ---------------------------------------------------------------------------------------
# Cases 1-4 oracle (ensemble covariance, eq 26)
# ---------------------------------------------------------------------------------------


def test_cases_1_to_4_oracle_on_ensemble_covariance():
    beta1 = (1.0 + 1.0j) / np.sqrt(2.0)
    beta2 = 0.5 * beta1
    f1, f2 = 2.0, 1.0
    theta0 = np.deg2rad(-30.0)
    n_r = 20
    sigma2 = abs(beta1) ** 2 + abs(beta2) ** 2
    s = beta1 * _steer(f1) + beta2 * _steer(f2)
    r_ens = np.outer(s, s.conj()) + sigma2 * np.eye(N_C)
    y_clean = np.outer(s, spatial_steering(theta0, n_r))
    ctx = _ctx(r_ens)

    def beta_at(g1, g2):
        return joint_beta_at(
            ctx, y_clean, g1, g2, theta0_rad=theta0, t_pri_s=T_PRI
        )

    joint = beta1 + beta2
    # Case 1: both true -> exactly beta1 + beta2 (clean Y, constraints force it).
    np.testing.assert_allclose(beta_at(f1, f2), joint, rtol=1e-9)
    # Cases 2-4: a wrong, well-separated frequency collapses |beta_hat| (paper's
    # epsilon terms; measured 0.011-0.167 of |joint| at this 0 dB ensemble — the
    # largest is Case 2 with f1 true, exactly the widest-lobe case the paper's own
    # analysis predicts). 0.25 documents "collapsed" (> 12 dB) without overclaiming.
    for g1, g2 in ((f1, 4.0), (5.5, f2), (4.0, -3.0)):
        assert abs(beta_at(g1, g2)) < 0.25 * abs(joint), (g1, g2, beta_at(g1, g2))


def test_eq26_cancellation_beta1_equals_negative_beta2_is_expected_failure():
    # Kotte p. 117, text below Eq. (29): beta1=-beta2 cancels beta_hat and makes
    # the proposed joint-coefficient method inapplicable even at the true pair.
    beta1 = 1.0 + 0.5j
    beta2 = -beta1
    f1_hz, f2_hz = 2.0, 1.0
    theta_rad = -0.3
    temporal_signal = beta1 * _steer(f1_hz) + beta2 * _steer(f2_hz)
    clean_y_t = np.outer(temporal_signal, spatial_steering(theta_rad, 20))
    # A full-rank identity covariance is an independent admissible analysis context;
    # Eq. (25)'s constraints alone force w^H Y_t a* / (a^H a)=beta1+beta2=0.
    beta_hat = joint_beta_at(
        _ctx(np.eye(N_C, dtype=np.complex128)), clean_y_t, f1_hz, f2_hz,
        theta0_rad=theta_rad, t_pri_s=T_PRI,
    )
    np.testing.assert_allclose(beta_hat, 0.0, atol=1e-12)
    assert abs(beta1) > 0.0 and abs(beta2) > 0.0


# ---------------------------------------------------------------------------------------
# Grids, mask, vectorized == loop
# ---------------------------------------------------------------------------------------


def test_make_grid_half_open():
    grid = make_grid_hz(-10.0, 10.0, 0.05)
    assert grid.size == 400
    assert grid[0] == -10.0
    assert np.isclose(grid[-1], 9.95)
    assert grid.max() < 10.0


def test_signed_band_grid_endpoints_and_symmetry():
    grid = signed_band_grid_hz((0.10, 0.50), 0.5 / 60.0)
    assert grid.size == 98
    assert np.isclose(grid[0], -0.50) and np.isclose(grid[-1], 0.50)
    assert np.isclose(np.abs(grid).min(), 0.10)
    np.testing.assert_allclose(np.sort(grid[grid > 0]), np.sort(-grid[grid < 0]))


def test_rcond_mask_diagonal_masked_truth_admissible():
    # Eq. (25), PDF p. 116: f1=f2 duplicates A's columns and makes H singular.
    y, _ = _two_tone(1.0, 1.0, 2.0, 1.0, -0.5, 20, 2.0, seed=11)
    ctx = _ctx(sample_covariance(y))
    grid = make_grid_hz(-10.0, 10.0, 0.05)
    surf = joint_capon_surface(
        ctx, grid, grid, n_c=N_C, t_pri_s=T_PRI, condition_mask_threshold=MASK
    )
    diag = np.arange(grid.size)
    assert surf.masked[diag, diag].all(), "exact f1 == f2 cells must be masked"
    i1 = int(np.argmin(np.abs(grid - 2.0)))
    i2 = int(np.argmin(np.abs(grid - 1.0)))
    assert not surf.masked[i1, i2], "the control truth pair (2, 1) must stay admissible"
    # R3's Fig-7-row-2 spacing (1.5, 1.0) must also stay admissible.
    i3 = int(np.argmin(np.abs(grid - 1.5)))
    assert not surf.masked[i3, i2]
    assert 0.0 < surf.masked_fraction < 0.05
    assert np.isnan(surf.objective[surf.masked]).all()


def test_equal_frequency_refused_and_near_equal_pair_masked():
    y, _ = _two_tone(1.0, 0.5, 2.0, 1.0, 0.2, 20, 1.0, seed=111)
    ctx = _ctx(sample_covariance(y))
    with pytest.raises(ConstraintMatrixFailure, match="f1 == f2"):
        joint_weight_at(ctx, 1.0, 1.0, n_c=N_C, t_pri_s=T_PRI)
    with pytest.raises(ConstraintMatrixFailure, match="reciprocal-condition"):
        joint_weight_at(
            ctx, 1.0, 1.0 + 1e-8, n_c=N_C, t_pri_s=T_PRI,
            condition_mask_threshold=MASK,
        )
    grid = np.array([1.0, 1.0 + 1e-8, 2.0])
    surface = joint_capon_surface(
        ctx, grid, grid, n_c=N_C, t_pri_s=T_PRI,
        condition_mask_threshold=MASK,
    )
    assert surface.masked[0, 0]
    assert surface.masked[0, 1]
    assert not surface.masked[0, 2]


def test_all_masked_raises():
    with pytest.raises(AllMaskedError):
        argmax_masked(np.full((3, 3), np.nan))


def test_vectorized_objective_equals_loop():
    # Algorithm 1 and Eq. (25), PDF pp. 116-117: vectorized H algebra must equal
    # the literal per-pair solve, not a second call to the vectorized implementation.
    y, _ = _two_tone(1.0, 0.3, 2.0, 1.0, 0.7, 20, 1.5, seed=12)
    ctx = _ctx(sample_covariance(y))
    grid = make_grid_hz(-4.0, 4.0, 0.4)
    surf = joint_capon_surface(
        ctx, grid, grid, n_c=N_C, t_pri_s=T_PRI, condition_mask_threshold=MASK
    )
    rng = np.random.default_rng(0)
    for _ in range(25):
        i, j = rng.integers(0, grid.size, size=2)
        if surf.masked[i, j]:
            continue
        direct = capon_power_at(ctx, grid[i], grid[j], n_c=N_C, t_pri_s=T_PRI)
        np.testing.assert_allclose(surf.objective[i, j], direct, rtol=1e-8)


def test_vectorized_beta_surface_equals_loop():
    # Eq. (26), PDF p. 117: vectorized joint beta equals the explicit Eq. (25)+(26)
    # solve at independently sampled cells.
    theta0 = 0.4
    y, _ = _two_tone(1.0, 0.3, 2.0, 1.0, theta0, 20, 1.5, seed=13)
    ctx = _ctx(sample_covariance(y))
    grid = make_grid_hz(-4.0, 4.0, 0.4)
    bsurf = beta_surface(
        ctx, y, grid, grid, theta0_rad=theta0, t_pri_s=T_PRI,
        condition_mask_threshold=MASK,
    )
    rng = np.random.default_rng(1)
    for _ in range(25):
        i, j = rng.integers(0, grid.size, size=2)
        if np.isnan(bsurf[i, j]):
            continue
        direct = joint_beta_at(
            ctx, y, grid[i], grid[j], theta0_rad=theta0, t_pri_s=T_PRI
        )
        np.testing.assert_allclose(bsurf[i, j], direct, rtol=1e-8)


def test_pair_swap_invariance_for_algorithm1_and_eq26():
    # Fig. 5 discussion, PDF pp. 118-119: the two solutions are symmetric and
    # interchangeable because swapping columns of A must not change either surface.
    theta0 = -0.4
    y, _ = _two_tone(1.0 + 0.2j, 0.2 - 0.1j, -2.0, 1.0, theta0, 20, 1.0, seed=113)
    ctx = _ctx(sample_covariance(y))
    power_12 = capon_power_at(ctx, -2.0, 1.0, n_c=N_C, t_pri_s=T_PRI)
    power_21 = capon_power_at(ctx, 1.0, -2.0, n_c=N_C, t_pri_s=T_PRI)
    beta_12 = joint_beta_at(
        ctx, y, -2.0, 1.0, theta0_rad=theta0, t_pri_s=T_PRI
    )
    beta_21 = joint_beta_at(
        ctx, y, 1.0, -2.0, theta0_rad=theta0, t_pri_s=T_PRI
    )
    np.testing.assert_allclose(power_12, power_21, rtol=1e-11)
    np.testing.assert_allclose(beta_12, beta_21, rtol=1e-11)


# ---------------------------------------------------------------------------------------
# Alias collapse, tie-break, pair margin
# ---------------------------------------------------------------------------------------


def test_alias_collapse_takes_max_and_records_signs():
    f1 = signed_band_grid_hz((0.10, 0.30), 0.10)   # -0.3..-0.1, 0.1..0.3
    f2 = signed_band_grid_hz((0.80, 1.00), 0.10)
    surface = np.zeros((f1.size, f2.size))
    i_neg = int(np.argmin(np.abs(f1 - (-0.2))))
    j_pos = int(np.argmin(np.abs(f2 - 0.9)))
    surface[i_neg, j_pos] = 7.0
    collapsed = alias_collapse(surface, f1, f2)
    ci = int(np.argmin(np.abs(collapsed.abs_f1_hz - 0.2)))
    cj = int(np.argmin(np.abs(collapsed.abs_f2_hz - 0.9)))
    assert collapsed.objective[ci, cj] == 7.0
    assert collapsed.sign_f1[ci, cj] == -1
    assert collapsed.sign_f2[ci, cj] == 1


def test_alias_collapse_tie_break_is_ascending_lexicographic_signed_pair():
    f1 = signed_band_grid_hz((0.10, 0.30), 0.10)
    f2 = signed_band_grid_hz((0.80, 1.00), 0.10)
    surface = np.zeros((f1.size, f2.size))
    # Same value in all four aliases: (-0.2,-0.9) is lexicographically first.
    for s1 in (1, -1):
        for s2 in (1, -1):
            i = int(np.argmin(np.abs(f1 - s1 * 0.2)))
            j = int(np.argmin(np.abs(f2 - s2 * 0.9)))
            surface[i, j] = 5.0
    collapsed = alias_collapse(surface, f1, f2)
    ci = int(np.argmin(np.abs(collapsed.abs_f1_hz - 0.2)))
    cj = int(np.argmin(np.abs(collapsed.abs_f2_hz - 0.9)))
    assert collapsed.sign_f1[ci, cj] == -1
    assert collapsed.sign_f2[ci, cj] == -1


def test_alias_collapse_all_masked_cell_is_nan_with_zero_sign():
    f1 = signed_band_grid_hz((0.10, 0.20), 0.10)
    f2 = signed_band_grid_hz((0.80, 0.90), 0.10)
    surface = np.full((f1.size, f2.size), np.nan)
    surface[0, 0] = 1.0  # (-0.2, -0.9) only
    collapsed = alias_collapse(surface, f1, f2)
    ci = int(np.argmin(np.abs(collapsed.abs_f1_hz - 0.1)))
    cj = int(np.argmin(np.abs(collapsed.abs_f2_hz - 0.8)))
    assert np.isnan(collapsed.objective[ci, cj])
    assert collapsed.sign_f1[ci, cj] == 0


def test_pair_margin_db_excludes_neighborhoods():
    surface = np.ones((9, 9))
    surface[4, 4] = 100.0
    surface[4, 5] = 90.0        # inside the exclusion ball -> not a runner-up
    surface[0, 0] = 10.0        # the true runner-up
    margin = pair_margin_db(surface, [(4, 4)], exclusion_steps=1)
    np.testing.assert_allclose(margin, 10.0)
    # Excluding the swap image as a second center also works.
    surface2 = np.ones((9, 9))
    surface2[2, 6] = 100.0
    surface2[6, 2] = 99.0       # swap image
    surface2[0, 0] = 1.0
    margin2 = pair_margin_db(surface2, [(2, 6), (6, 2)], exclusion_steps=1)
    np.testing.assert_allclose(margin2, 20.0)


def test_pair_margin_db_no_runner_up_is_nan():
    surface = np.full((3, 3), np.nan)
    surface[1, 1] = 5.0
    assert np.isnan(pair_margin_db(surface, [(1, 1)], exclusion_steps=1))


def test_pair_margin_is_computed_after_signed_alias_collapse():
    f1 = signed_band_grid_hz((0.10, 0.50), 0.10)
    f2 = signed_band_grid_hz((0.80, 1.20), 0.10)
    surface = np.ones((f1.size, f2.size))
    # Two raw aliases of the same canonical pair must collapse to one candidate.
    surface[np.argmin(abs(f1 + 0.3)), np.argmin(abs(f2 + 1.0))] = 100.0
    surface[np.argmin(abs(f1 - 0.3)), np.argmin(abs(f2 - 1.0))] = 99.0
    surface[np.argmin(abs(f1 + 0.1)), np.argmin(abs(f2 + 0.8))] = 10.0
    collapsed = alias_collapse(surface, f1, f2)
    selected = argmax_masked(collapsed.objective)
    np.testing.assert_allclose(
        pair_margin_db(collapsed.objective, [selected], exclusion_steps=1), 10.0
    )


# ---------------------------------------------------------------------------------------
# Config dataclasses, hashes, capture-knowledge firewall
# ---------------------------------------------------------------------------------------


def _stage_a_dict():
    with open(CONFIG_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle)["stage_a"]


def test_config_builds_from_committed_yaml_stage_a():
    config = KotteJointDopplerConfig.from_stage_a(_stage_a_dict())
    assert config.breath_band_hz == (0.10, 0.50)
    assert config.heart_band_hz == (0.80, 2.00)
    assert [arm.arm_id for arm in config.arms] == [
        "kotte_cpi_medoid_nc16_dl1em2",
        "kotte_cpi_medoid_nc16_dl1em4",
    ]
    assert config.min_valid_cpi_fraction == 1.0
    assert config.slow_time_selection == "fixed_chirp_loop_0"


def test_active_m9_config_excludes_retired_architecture():
    with open(CONFIG_PATH, encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    assert "transfer" not in config
    assert "stage_b_decision" not in config
    diagnostics = config["synthetic_transfer_diagnostics"]
    assert "diagnostic_arms" not in diagnostics
    assert "gate_criteria" not in diagnostics
    assert diagnostics["parameter_promotion_allowed"] is False
    stage_a = config["stage_a"]
    assert stage_a["window_frames"] == 600
    assert stage_a["retained_frames"] == 592
    assert stage_a["cpi_frames"] == 16
    assert stage_a["cpis_per_window"] == 37
    assert stage_a["slow_time_selection"] == "fixed_chirp_loop_0"
    assert stage_a["window_report"] == "two_dimensional_l1_medoid"
    assert {arm["estimator_form"] for arm in stage_a["arms"]} == {"cpi_medoid"}
    assert {arm["n_c"] for arm in stage_a["arms"]} == {16}
    assert {arm["loading_delta"] for arm in stage_a["arms"]} == {1.0e-2, 1.0e-4}
    assert len(stage_a["arms"]) == 2
    assert stage_a["primary_arm_id"] == "kotte_cpi_medoid_nc16_dl1em2"
    assert stage_a["loading_sensitivity_arm_id"] == "kotte_cpi_medoid_nc16_dl1em4"
    assert config["radar_evaluation"]["range_selection"]["source"] == (
        "current_production_rerun_lock"
    )
    assert config["radar_evaluation"]["range_selection"][
        "kotte_all_bin_selection_allowed"
    ] is False
    assert len(config["radar_evaluation"]["captures"]) == 8
    assert config["scoring"]["outcome_threshold"] == "none"
    assert config["scoring"]["estimator_settings_mutable_after_scoring"] is False
    assert config["scoring"]["promotion_eligible"] is False
    active_yaml = yaml.safe_dump(config, sort_keys=True).lower()
    for retired_token in ("coherent_mean", "mean_surface", "nc32", "go_no_go"):
        assert retired_token not in active_yaml


def test_config_rejects_partial_cpi_fraction():
    stage_a = _stage_a_dict()
    stage_a["min_valid_cpi_fraction"] = 0.9
    with pytest.raises(ValueError, match="exactly 1.0"):
        KotteJointDopplerConfig.from_stage_a(stage_a)


def test_arm_rejects_unknown_form_and_bad_delta():
    with pytest.raises(ValueError, match="estimator_form"):
        KotteArm("x", 16, "argmax", 1e-2)
    with pytest.raises(ValueError, match="estimator_form"):
        KotteArm("x", 16, "pooled", 1e-2)
    with pytest.raises(ValueError, match="loading_delta"):
        KotteArm("x", 16, "cpi_medoid", 0.0)


def test_per_arm_hash_uniqueness_and_stability():
    config = KotteJointDopplerConfig.from_stage_a(_stage_a_dict())
    hashes = [config.run_config_hash(arm) for arm in config.arms]
    assert len(set(hashes)) == len(hashes)
    config2 = KotteJointDopplerConfig.from_stage_a(_stage_a_dict())
    assert [config2.run_config_hash(a) for a in config2.arms] == hashes
    assert config.suite_config_hash() == config2.suite_config_hash()


def test_dsp_config_contains_no_capture_knowledge():
    # Adding unrelated runner input must change NO estimator hash.
    stage_a = _stage_a_dict()
    baseline = KotteJointDopplerConfig.from_stage_a(stage_a)
    baseline_hashes = {
        "suite": baseline.suite_config_hash(),
        "arms": [baseline.run_config_hash(a) for a in baseline.arms],
    }
    stage_a["unrelated_runner_input"] = {"capture_id": "must_not_enter_hash"}
    modified = KotteJointDopplerConfig.from_stage_a(stage_a)
    assert modified.suite_config_hash() == baseline_hashes["suite"]
    assert [modified.run_config_hash(a) for a in modified.arms] == baseline_hashes["arms"]


def test_bands_pinned_to_m8_domain_and_live_demo_config():
    with open(REPO_ROOT / "scripts" / "live_demo_config.yaml", encoding="utf-8") as fh:
        live = yaml.safe_load(fh)
    config = KotteJointDopplerConfig.from_stage_a(_stage_a_dict())
    assert list(config.breath_band_hz) == [float(v) for v in live["respiration"]["band_hz"]]
    assert list(config.heart_band_hz) == [float(v) for v in live["heart"]["band_hz"]]


# =======================================================================================
# Aggregation contract (plan step 3): extraction, tail/detrend order, forms, suite
# =======================================================================================

FS_HZ = 20.0


def _synthetic_cube(
    n_frames: int = 600,
    *,
    f_breath_hz: float = 0.30,
    f_heart_hz: float = 1.35,
    breath_amp_m: float = 5.0e-5,
    heart_amp_m: float = 2.5e-5,
    target_bin: int = 7,
    n_chirps: int = 32,
    n_rx: int = 4,
    n_adc: int = 256,
    seed: int = 4,
    noise_scale: float = 0.01,
) -> np.ndarray:
    """A small chest-like cube: a range-bin tone modulated by two displacement lines."""
    rng = np.random.default_rng(seed)
    t = np.arange(n_frames) / FS_HZ
    lam = 3.79e-3
    displacement = breath_amp_m * np.sin(2 * np.pi * f_breath_hz * t) + heart_amp_m * (
        np.sin(2 * np.pi * f_heart_hz * t)
    )
    phase = (4.0 * np.pi / lam) * displacement
    envelope = np.exp(1j * phase)  # (n_frames,)
    n = np.arange(n_adc)
    range_tone = np.exp(2j * np.pi * target_bin * n / n_adc)
    loop_zero = np.empty((n_frames, n_rx, n_adc), dtype=np.complex64)
    for rx in range(n_rx):
        gain = 1.0 + 0.05 * rx
        loop_zero[:, rx, :] = gain * envelope[:, None] * range_tone[None, :]
    loop_zero += noise_scale * (
        rng.standard_normal(loop_zero.shape) + 1j * rng.standard_normal(loop_zero.shape)
    )
    # A read-only broadcast is sufficient except in the explicit no-averaging test.
    return np.broadcast_to(loop_zero[:, None, :, :], (n_frames, n_chirps, n_rx, n_adc))


def _synthetic_z(seed: int = 4) -> np.ndarray:
    """Four-RX two-cisoid window with independent noise (rank four per CPI)."""
    rng = np.random.default_rng(seed)
    t = np.arange(600, dtype=np.float64) / FS_HZ
    dynamic = np.exp(2j * np.pi * 0.30 * t) + 0.35 * np.exp(2j * np.pi * 1.35 * t)
    gains = np.array([1.0, 0.9j, -0.8, -0.7j])
    noise = 0.08 * (
        rng.standard_normal((600, 4)) + 1j * rng.standard_normal((600, 4))
    )
    return np.asarray(dynamic[:, None] * gains[None, :] + noise, dtype=np.complex128)


def _stage_a_config() -> KotteJointDopplerConfig:
    return KotteJointDopplerConfig.from_stage_a(_stage_a_dict())


def test_extract_rx_slow_time_selects_only_loop_zero_and_keeps_rx_order():
    cube = np.array(_synthetic_cube(n_frames=8, noise_scale=0.0), copy=True)
    cube[:, 1:, :, :] = 1000.0 + 2000.0j
    z = extract_rx_slow_time(cube, locked_bin=7)
    assert z.shape == (8, 4)
    assert z.dtype == np.complex128
    hann = np.hanning(256)
    expected = np.fft.fft(cube[:, 0, :, :] * hann, axis=-1)[:, :, 7]
    np.testing.assert_allclose(z, expected, rtol=1e-12, atol=1e-12)


@pytest.mark.parametrize("locked_bin", [-1, 256, 7.0, True])
def test_extract_rejects_out_of_range_or_noninteger_bin(locked_bin):
    cube = _synthetic_cube(n_frames=8)
    with pytest.raises(ValueError, match="locked_bin"):
        extract_rx_slow_time(cube, locked_bin=locked_bin)


@pytest.mark.parametrize(
    "shape",
    [
        (8, 31, 4, 256),
        (8, 32, 3, 256),
        (8, 32, 4, 255),
        (8, 32, 4),
    ],
)
def test_extract_rejects_wrong_chirp_rx_adc_or_dimensional_contract(shape):
    cube = np.zeros(shape, dtype=np.complex64)
    with pytest.raises(ValueError, match="shape|4-D"):
        extract_rx_slow_time(cube, locked_bin=7)


def test_extract_rejects_noncomplex_nonfinite_and_nonzero_chirp_loop():
    real_cube = np.zeros((2, 32, 4, 256), dtype=np.float32)
    with pytest.raises(TypeError, match="complex"):
        extract_rx_slow_time(real_cube, locked_bin=7)
    complex_cube = real_cube.astype(np.complex64)
    complex_cube[0, 0, 0, 0] = np.nan
    with pytest.raises(ValueError, match="NaN or Inf"):
        extract_rx_slow_time(complex_cube, locked_bin=7)
    with pytest.raises(ValueError, match="chirp_loop_index=0"):
        extract_rx_slow_time(np.zeros_like(complex_cube), locked_bin=7, chirp_loop_index=1)


def test_fixed_loop_phase_ramp_is_recovered_in_physical_hz():
    n_frames = 40
    expected_hz = 0.30
    t = np.arange(n_frames) / FS_HZ
    n = np.arange(256)
    tone = np.exp(2j * np.pi * 7 * n / 256)
    cube = np.zeros((n_frames, 32, 4, 256), dtype=np.complex64)
    gains = np.array([1.0, 0.8j, -0.7, -0.6j])
    cube[:, 0, :, :] = (
        np.exp(2j * np.pi * expected_hz * t)[:, None, None]
        * gains[None, :, None]
        * tone[None, None, :]
    )
    # Other chirps carry a different ramp; their presence must not influence Z.
    cube[:, 1:, :, :] = (
        np.exp(2j * np.pi * 1.75 * t)[:, None, None, None] * tone[None, None, None, :]
    )
    z = extract_rx_slow_time(cube, locked_bin=7)
    observed_hz = np.angle(np.mean(z[1:, 0] * z[:-1, 0].conj())) * FS_HZ / (2 * np.pi)
    np.testing.assert_allclose(observed_hz, expected_hz, atol=2e-8)


def test_structural_frame_continuity_checks_are_exact_and_fail_closed():
    metadata = {
        "frame_count": 600,
        "num_chirps_per_frame": 32,
        "num_rx": 4,
        "num_adc_samples": 256,
        "file_size_divisible": True,
        "packet_drop_count": 0,
    }
    validate_project_frame_continuity(
        np.arange(600, dtype=np.int64), frame_period_s=0.05, metadata=metadata
    )
    broken_order = np.arange(600, dtype=np.int64)
    broken_order[100] = 99
    with pytest.raises(ValueError, match="ordered and contiguous"):
        validate_project_frame_continuity(
            broken_order, frame_period_s=0.05, metadata=metadata
        )
    with pytest.raises(ValueError, match="packet_drop_count"):
        validate_project_frame_continuity(
            np.arange(600),
            frame_period_s=0.05,
            metadata={**metadata, "packet_drop_count": 1},
        )


@pytest.mark.parametrize(
    ("frame_indices", "frame_period_s", "metadata_update", "match"),
    [
        (np.arange(600, dtype=np.float64), 0.05, {}, "integer vector"),
        (np.arange(599, dtype=np.int64), 0.05, {}, "length 600"),
        (np.arange(600, dtype=np.int64), 0.0501, {}, "frame period"),
        (np.arange(600, dtype=np.int64), 0.05, {"frame_count": 599}, "frame_count"),
        (
            np.arange(600, dtype=np.int64),
            0.05,
            {"num_chirps_per_frame": 31},
            "num_chirps_per_frame",
        ),
        (np.arange(600, dtype=np.int64), 0.05, {"num_rx": 3}, "num_rx"),
        (
            np.arange(600, dtype=np.int64),
            0.05,
            {"num_adc_samples": 255},
            "num_adc_samples",
        ),
        (
            np.arange(600, dtype=np.int64),
            0.05,
            {"file_size_divisible": False},
            "file_size_divisible",
        ),
    ],
)
def test_structural_continuity_refuses_each_unfrozen_acquisition_fact(
    frame_indices, frame_period_s, metadata_update, match
):
    """Plan M9.2: unavailable or mismatched acquisition structure fails closed."""
    metadata = {
        "frame_count": 600,
        "num_chirps_per_frame": 32,
        "num_rx": 4,
        "num_adc_samples": 256,
        "file_size_divisible": True,
        "packet_drop_count": 0,
    }
    metadata.update(metadata_update)
    with pytest.raises(ValueError, match=match):
        validate_project_frame_continuity(
            frame_indices, frame_period_s=frame_period_s, metadata=metadata
        )


def test_prepare_cpis_retains_592_of_600_at_nc16():
    z = np.arange(600 * 4, dtype=np.complex128).reshape(600, 4)
    cpis, start, end = prepare_cpis(z, 16)
    assert (start, end) == (0, 592)
    assert cpis.shape == (37, 16, 4)


def test_tail_is_immutable_discarded_frames_cannot_affect_any_result():
    """The pinned order (retain -> detrend -> split) makes the tail provably inert."""
    z = np.asarray(
        np.random.default_rng(0).standard_normal((600, 4))
        + 1j * np.random.default_rng(1).standard_normal((600, 4))
    )
    perturbed = z.copy()
    perturbed[592:] += 1e6 + 1e6j  # obliterate the discarded tail
    a, _, _ = prepare_cpis(z, 16)
    b, _, _ = prepare_cpis(perturbed, 16)
    np.testing.assert_array_equal(a, b)


def test_detrend_is_over_the_retained_support_not_the_full_window():
    z = np.zeros((600, 4), dtype=np.complex128)
    z[592:] = 100.0  # only the discarded tail carries the offset
    cpis, _, _ = prepare_cpis(z, 16)
    # If the mean had been taken over all 600 frames, every retained sample would be
    # shifted by -100*8/600; over the retained support the retained data is all zeros.
    np.testing.assert_allclose(cpis, 0.0, atol=1e-12)


def test_medoid_picks_a_member_and_breaks_ties_to_lowest_index():
    estimates = np.array([[18.0, 80.0], [18.5, 81.0], [40.0, 120.0]])
    idx = medoid_of_cpi_estimates(estimates)
    assert idx in (0, 1)
    # A member of the set, never an invented pair.
    assert tuple(estimates[idx]) in {tuple(row) for row in estimates}
    # Exact tie between two identical candidates -> the lower index wins.
    tied = np.array([[10.0, 60.0], [10.0, 60.0], [90.0, 200.0]])
    assert medoid_of_cpi_estimates(tied) == 0


def test_medoid_is_deterministic_across_calls():
    rng = np.random.default_rng(3)
    estimates = rng.normal(size=(37, 2)) * 10 + np.array([18.0, 80.0])
    first = medoid_of_cpi_estimates(estimates)
    for _ in range(5):
        assert medoid_of_cpi_estimates(estimates) == first


def test_estimate_window_cpi_medoid_reports_medoid_cpi_diagnostics():
    config = _stage_a_config()
    arm = next(a for a in config.arms if a.estimator_form == "cpi_medoid")
    z = _synthetic_z()
    native, evidence = estimate_window(z, config=config, arm=arm, fs_hz=FS_HZ)
    assert native["br_valid"] and native["hr_valid"]
    assert native["n_cpis_expected"] == 37 and native["n_cpis_valid"] == 37
    assert (native["frame_start_used"], native["frame_end_used"]) == (0, 592)
    idx = native["medoid_cpi_index"]
    # The window's reported pair IS the medoid CPI's pair, not an average.
    np.testing.assert_allclose(native["br_bpm"], evidence["cpi_estimates_bpm"][idx, 0])
    np.testing.assert_allclose(native["hr_raw"], evidence["cpi_estimates_bpm"][idx, 1])
    np.testing.assert_allclose(
        native["pair_margin_db"], evidence["cpi_pair_margin_db"][idx]
    )
    # Estimates land inside the configured bands.
    assert 6.0 <= native["br_bpm"] <= 30.0
    assert 48.0 <= native["hr_raw"] <= 120.0
    assert native["objective_name"] == "regularized_kotte_power"
    assert evidence["cpi_regularized_kotte_power"].shape[:1] == (37,)
    assert evidence["cpi_constraint_rcond"].shape == evidence[
        "cpi_regularized_kotte_power"
    ].shape
    assert evidence["cpi_constraint_mask"].shape == evidence[
        "cpi_regularized_kotte_power"
    ].shape
    np.testing.assert_allclose(
        native["selected_raw_signed_hz"], evidence["cpi_raw_signed_pairs_hz"][idx]
    )


def test_cpi_loading_is_exact_trace_relative_formula_and_rank_at_most_four():
    config = _stage_a_config()
    arm = config.arms[0]
    z = _synthetic_z()
    _native, evidence = estimate_window(z, config=config, arm=arm, fs_hz=FS_HZ)
    cpis, _, _ = prepare_cpis(z)
    expected_delta_bar = np.empty(37)
    for cpi_index, y_t in enumerate(cpis):
        covariance = y_t @ y_t.conj().T / 4.0
        expected_delta_bar[cpi_index] = (
            arm.loading_delta * np.trace(covariance).real / 16.0
        )
    np.testing.assert_allclose(evidence["cpi_delta_bar"], expected_delta_bar, rtol=2e-13)
    assert np.all(evidence["cpi_ranks"] <= 4)


def test_project_loaded_surface_matches_independent_literal_matrix_oracle():
    """M9.2 loaded adaptation of Kotte Algorithm 1, PDF printed pp. 116-117.

    This derives R, R_delta, H_delta and Eq. (25)'s weight with raw NumPy.  In
    particular, the persisted ``regularized_kotte_power`` must be w^H R_delta w,
    not the literal-paper unloaded quadratic w^H R w.
    """
    config = _stage_a_config()
    arm = config.arms[0]
    z = _synthetic_z(seed=41)
    _native, evidence = estimate_window(z, config=config, arm=arm, fs_hz=FS_HZ)

    retained = z[:592]
    first_cpi = (retained - retained.mean(axis=0, keepdims=True))[:16]
    covariance_unloaded = first_cpi @ first_cpi.conj().T / 4.0
    expected_eigvals = np.linalg.eigvalsh(covariance_unloaded)[::-1]
    expected_delta_bar = arm.loading_delta * np.trace(covariance_unloaded).real / 16.0
    covariance_loaded = covariance_unloaded + expected_delta_bar * np.eye(16)

    f1_hz, f2_hz = -0.30, 1.35
    slow_time_s = np.arange(16, dtype=np.float64) * 0.05
    frequency_constraint = np.column_stack(
        (
            np.exp(2j * np.pi * f1_hz * slow_time_s),
            np.exp(2j * np.pi * f2_hz * slow_time_s),
        )
    )
    covariance_solutions = np.linalg.solve(covariance_loaded, frequency_constraint)
    h_delta = frequency_constraint.conj().T @ covariance_solutions
    ones = np.ones(2, dtype=np.complex128)
    h_solution = np.linalg.solve(h_delta, ones)
    expected_power = float((ones.conj() @ h_solution).real)
    weight = covariance_solutions @ h_solution
    loaded_quadratic = float((weight.conj() @ covariance_loaded @ weight).real)
    unloaded_quadratic = float((weight.conj() @ covariance_unloaded @ weight).real)

    i = int(np.argmin(np.abs(evidence["f1_grid_hz"] - f1_hz)))
    j = int(np.argmin(np.abs(evidence["f2_grid_hz"] - f2_hz)))
    assert not evidence["cpi_constraint_mask"][0, i, j]
    np.testing.assert_allclose(
        evidence["cpi_eigvals"][0], expected_eigvals, rtol=2e-11, atol=3e-15
    )
    np.testing.assert_allclose(evidence["cpi_delta_bar"][0], expected_delta_bar, rtol=2e-13)
    np.testing.assert_allclose(
        evidence["cpi_constraint_rcond"][0, i, j],
        1.0 / np.linalg.cond(h_delta),
        rtol=2e-10,
    )
    np.testing.assert_allclose(
        evidence["cpi_regularized_kotte_power"][0, i, j],
        expected_power,
        rtol=2e-9,
    )
    np.testing.assert_allclose(expected_power, loaded_quadratic, rtol=2e-9)
    assert not np.isclose(expected_power, unloaded_quadratic, rtol=1e-5)


def test_project_signed_grids_are_exact_half_bpm_domains():
    config = _stage_a_config()
    _native, evidence = estimate_window(
        _synthetic_z(), config=config, arm=config.arms[0], fs_hz=FS_HZ
    )
    positive_breath = np.linspace(0.10, 0.50, 49)
    positive_heart = np.linspace(0.80, 2.00, 145)
    expected_breath = np.concatenate((-positive_breath[::-1], positive_breath))
    expected_heart = np.concatenate((-positive_heart[::-1], positive_heart))
    np.testing.assert_allclose(evidence["f1_grid_hz"], expected_breath, atol=2e-15)
    np.testing.assert_allclose(evidence["f2_grid_hz"], expected_heart, atol=2e-15)
    np.testing.assert_allclose(np.diff(positive_breath), 1.0 / 120.0, atol=2e-16)
    np.testing.assert_allclose(np.diff(positive_heart), 1.0 / 120.0, atol=2e-16)


def test_estimate_window_rejects_wrong_frame_rate_and_short_window():
    config = _stage_a_config()
    arm = config.arms[0]
    with pytest.raises(ValueError, match="frame rate"):
        estimate_window(_synthetic_z(), config=config, arm=arm, fs_hz=19.0)
    with pytest.raises(ValueError, match="project window shape"):
        estimate_window(_synthetic_z()[:592], config=config, arm=arm, fs_hz=FS_HZ)


@pytest.mark.parametrize("retired_form", ["pooled", "mean_surface"])
def test_estimate_window_refuses_retired_estimator_forms(retired_form):
    config = _stage_a_config()
    with pytest.raises(ValueError, match="estimator_form"):
        KotteArm("retired_form_test_only", 16, retired_form, 1e-2)


def test_estimate_window_refuses_retired_nc32_arm():
    stage_a = _stage_a_dict()
    stage_a["arms"] = [
        {
            "arm_id": "retired_nc32_test_only",
            "n_c": 32,
            "estimator_form": "cpi_medoid",
            "loading_delta": 1e-2,
        }
    ]
    config = KotteJointDopplerConfig.from_stage_a(stage_a)
    with pytest.raises(ValueError, match="16-frame"):
        estimate_window(
            _synthetic_z(), config=config, arm=config.arms[0], fs_hz=FS_HZ
        )


def test_single_cpi_rank_is_four_at_four_rx():
    config = _stage_a_config()
    arm = next(a for a in config.arms if a.estimator_form == "cpi_medoid")
    z = _synthetic_z()
    native, evidence = estimate_window(z, config=config, arm=arm, fs_hz=FS_HZ)
    assert native["rank_rt"] == 4
    assert set(np.unique(evidence["cpi_ranks"])) == {4}


def test_zeroed_block_becomes_a_rank_one_dc_cpi_and_stays_valid():
    """Detrending runs AFTER retention, so a zeroed block is not a zero CPI.

    Subtracting the per-RX mean turns an all-zero block into a constant (DC) block,
    which carries energy and has rank 1 — valid under the pinned rule. Pinned because
    the naive expectation ("zeroing frames kills the CPI") is wrong and would otherwise
    be re-derived incorrectly later.
    """
    config = _stage_a_config()
    arm = next(a for a in config.arms if a.estimator_form == "cpi_medoid")
    z = _synthetic_z()
    z[16:32, :] = 0.0
    native, evidence = estimate_window(z, config=config, arm=arm, fs_hz=FS_HZ)
    assert native["br_valid"] is True
    assert evidence["cpi_ranks"][1] == 1
    assert evidence["cpi_valid"].all()


def test_any_invalid_cpi_fails_the_whole_window():
    """min_valid_cpi_fraction is exactly 1.0 — the window fails closed."""
    config = _stage_a_config()
    arm = next(a for a in config.arms if a.estimator_form == "cpi_medoid")
    # One CPI is exactly the retained-support mean, so it is identically zero after
    # detrending -> lambda_max == 0 -> rank 0. The other CPIs alternate about that
    # same mean, so they keep energy and stay valid.
    z = np.zeros((600, 4), dtype=np.complex128)
    alternating = np.where(np.arange(600) % 2 == 0, 1.0, -1.0)
    z[:, :] = alternating[:, None]
    z[16:32, :] = 0.0  # this block equals the (zero) global mean exactly
    native, evidence = estimate_window(z, config=config, arm=arm, fs_hz=FS_HZ)
    assert native["br_valid"] is False and native["hr_valid"] is False
    assert native["rej_reason"] == "invalid_cpi"
    assert native["failing_cpi_index"] == 1
    assert native["failing_cpi_cause_code"] == CPI_CAUSE_CODES["rank_zero"]
    assert evidence["cpi_valid"][1] == False  # noqa: E712
    assert evidence["cpi_valid"].sum() == 36
    assert "br_bpm" not in native


def test_a_single_nonfinite_sample_fails_the_whole_window():
    """One NaN contaminates every CPI, because the detrend mean spans the window.

    Pinned deliberately: the failure is total, not localized to the CPI containing the
    NaN, so `failing_cpi_index` is 0 and is NOT a pointer to the corrupt sample. The
    outcome is fail-closed, which is what matters; anyone diagnosing a nonfinite window
    must look at the raw Z, not at the reported index.
    """
    config = _stage_a_config()
    arm = next(a for a in config.arms if a.estimator_form == "cpi_medoid")
    z = _synthetic_z()
    z[40, 2] = np.nan  # lands in CPI index 2, but poisons RX column 2 for all CPIs
    native, evidence = estimate_window(z, config=config, arm=arm, fs_hz=FS_HZ)
    assert native["br_valid"] is False and native["hr_valid"] is False
    assert native["rej_reason"] == "invalid_cpi"
    assert native["failing_cpi_index"] == 0
    assert native["failing_cpi_cause_code"] == CPI_CAUSE_CODES["covariance_nonfinite"]
    assert not evidence["cpi_valid"].any()
    assert set(np.unique(evidence["cpi_cause_codes"])) == {
        CPI_CAUSE_CODES["covariance_nonfinite"]
    }


def test_evidence_arrays_are_npz_safe_and_immutable():
    config = _stage_a_config()
    for arm in config.arms:
        z = _synthetic_z()
        _native, evidence = estimate_window(z, config=config, arm=arm, fs_hz=FS_HZ)
        for key, value in evidence.items():
            if isinstance(value, np.ndarray):
                assert value.dtype != object, key
                assert not value.flags.writeable, key


def test_evidence_npz_round_trips_without_pickle(tmp_path):
    config = _stage_a_config()
    arm = config.arms[0]
    z = _synthetic_z()
    _native, evidence = estimate_window(z, config=config, arm=arm, fs_hz=FS_HZ)
    arrays = {k: v for k, v in evidence.items() if isinstance(v, np.ndarray)}
    target = tmp_path / "evidence.npz"
    np.savez(target, **arrays)
    with np.load(target, allow_pickle=False) as loaded:
        assert set(loaded.files) == set(arrays)


def test_suite_returns_exactly_its_declared_arms_with_one_shared_signal():
    config = _stage_a_config()
    suite = KotteEstimatorSuite(config)
    cube = _synthetic_cube()
    result = suite(cube, locked_bin=7, fs=FS_HZ)
    assert set(result.arm_native_results) == {arm.arm_id for arm in config.arms}
    hashes = {n["shared_signal_hash"] for n in result.arm_native_results.values()}
    assert len(hashes) == 1, "Z is extracted once and shared by every arm"
    assert result.shared_evidence["shared_signal_hash"] == hashes.pop()
    assert result.shared_evidence["n_rx"] == 4
    assert result.arm_outcomes == {}


def test_suite_arm_specs_carry_unique_hashes_and_no_m8_fields():
    config = _stage_a_config()
    suite = KotteEstimatorSuite(config)
    assert len({s.run_config_hash for s in suite.arm_specs}) == len(suite.arm_specs)
    for spec in suite.arm_specs:
        assert spec.harmonic_count is None
        assert spec.suppression_profile is None
        assert spec.outcome_classifier_id is None


def test_suite_satisfies_the_neutral_protocol():
    from src.m4.estimator_suite import WindowEstimatorSuite

    assert isinstance(KotteEstimatorSuite(_stage_a_config()), WindowEstimatorSuite)


def test_suite_is_deterministic():
    config = _stage_a_config()
    cube = _synthetic_cube()
    first = KotteEstimatorSuite(config)(cube, locked_bin=7, fs=FS_HZ)
    second = KotteEstimatorSuite(config)(cube, locked_bin=7, fs=FS_HZ)
    for arm_id, native in first.arm_native_results.items():
        assert native == second.arm_native_results[arm_id]


# =======================================================================================
# M9.2 chest-displacement transfer diagnostics
# =======================================================================================

from scripts.m9_kotte_synthetic_transfer import (  # noqa: E402
    bessel_reconstruction,
    chest_displacement_phasor,
    compact_evidence,
    write_shared_z_artifact,
)


def test_bessel_diagnostic_matches_direct_chest_phasor_and_has_signed_sidebands():
    common = dict(
        n_frames=600,
        fs_hz=20.0,
        wavelength_m=3.79e-3,
        breath_amplitude_m=2.0e-3,
        heart_amplitude_m=3.0e-4,
        f_breath_hz=0.30,
        f_heart_hz=1.20,
        phi_breath_rad=1.0,
        phi_heart_rad=2.5,
    )
    # Independent literal chest generator: x(t) followed by exp(j 4 pi x/lambda).
    time_s = np.arange(common["n_frames"], dtype=np.float64) / common["fs_hz"]
    displacement_m = common["breath_amplitude_m"] * np.sin(
        2.0 * np.pi * common["f_breath_hz"] * time_s + common["phi_breath_rad"]
    ) + common["heart_amplitude_m"] * np.sin(
        2.0 * np.pi * common["f_heart_hz"] * time_s + common["phi_heart_rad"]
    )
    expected_direct = np.exp(
        1j * 4.0 * np.pi * displacement_m / common["wavelength_m"]
    )
    direct = chest_displacement_phasor(**common)
    np.testing.assert_allclose(direct, expected_direct, rtol=0, atol=2e-15)
    analytic = bessel_reconstruction(**common, expansion_order=32)
    relative_rms = np.sqrt(np.mean(np.abs(direct - analytic) ** 2))
    relative_rms /= np.sqrt(np.mean(np.abs(direct) ** 2))
    assert relative_rms < 1e-12

    # Independent first-order Jacobi-Anger check: J_-1(m)=-J_1(m), so the clean
    # complex phasor contains both conjugate-frequency sidebands with opposite signs.
    modulation = 4.0 * np.pi * common["breath_amplitude_m"] / common["wavelength_m"]
    np.testing.assert_allclose(jv(-1, modulation), -jv(1, modulation), rtol=0, atol=1e-15)


def test_transfer_matrix_includes_cancellation_and_cannot_promote_parameters():
    with CONFIG_PATH.open(encoding="utf-8") as handle:
        diagnostics = yaml.safe_load(handle)["synthetic_transfer_diagnostics"]
    generator = diagnostics["generator"]
    assert diagnostics["role"] == "diagnostic_nondeployable"
    assert diagnostics["parameter_promotion_allowed"] is False
    assert generator["phase_pair_labels"][2] == "anti_phase_cancellation_diagnostic"
    np.testing.assert_allclose(generator["phase_pairs_rad"][2], [0.0, np.pi], atol=0)
    assert {case["scenario_id"] for case in diagnostics["scenarios"]} == {
        "s1_two_rate_small_modulation",
        "s2_deep_modulation_decoy",
        "s3_harmonic_collision",
    }


def test_shared_z_and_arm_evidence_reconstruct_loaded_objective(tmp_path):
    config = _stage_a_config()
    arm = config.arms[0]
    z = _synthetic_z(seed=104)
    case_id = "s1_two_rate_small_modulation_phase0_test"
    seed = 104
    shared_reference = write_shared_z_artifact(
        tmp_path, case_id=case_id, seed=seed, z=z
    )
    native, evidence = estimate_window(z, config=config, arm=arm, fs_hz=FS_HZ)
    medoid_index = int(native["medoid_cpi_index"])
    arrays = compact_evidence(
        evidence, medoid_index, shared_z_reference=shared_reference
    )
    arm_path = tmp_path / "arm_evidence.npz"
    np.savez(arm_path, **arrays)

    shared_path = tmp_path / shared_reference["input_z_artifact"]
    shared_file_hash = sha256(shared_path.read_bytes()).hexdigest()
    assert shared_file_hash == shared_reference["input_z_artifact_sha256"]
    with np.load(shared_path, allow_pickle=False) as shared:
        assert all(shared[key].dtype != object for key in shared.files)
        assert shared["case_id"].item() == case_id
        assert int(shared["seed"].item()) == seed
        assert tuple(shared["shape"]) == (600, 4)
        assert shared["dtype"].item() == "complex128"
        shared_z = np.asarray(shared["z"])
        observed_input_hash = sha256(
            np.ascontiguousarray(shared_z).tobytes()
        ).hexdigest()
        assert observed_input_hash == shared["input_z_sha256"].item()
        assert observed_input_hash == shared_reference["input_z_sha256"]

    with np.load(arm_path, allow_pickle=False) as loaded:
        assert all(loaded[key].dtype != object for key in loaded.files)
        assert loaded["input_z_artifact"].item() == shared_path.name
        assert loaded["input_z_artifact_sha256"].item() == shared_file_hash
        assert loaded["input_z_sha256"].item() == observed_input_hash
        assert int(loaded["medoid_index"].item()) == medoid_index

        # Independent M9 project equations (plan §15): R=Y_tY_t^H/4,
        # delta_bar=delta*Re(trace(R))/16, then 1^H H_delta^-1 1.
        retained = shared_z[:592]
        detrended = retained - retained.mean(axis=0, keepdims=True)
        y_t = detrended.reshape(37, 16, 4)[medoid_index]
        covariance = y_t @ y_t.conj().T / 4.0
        loading_delta = float(loaded["loading_delta"].item())
        delta_bar = loading_delta * np.trace(covariance).real / 16.0
        np.testing.assert_allclose(
            delta_bar, loaded["cpi_delta_bar"][medoid_index], rtol=2e-13
        )
        loaded_covariance = covariance + delta_bar * np.eye(16)
        raw_pair_hz = loaded["cpi_raw_signed_pairs_hz"][medoid_index]
        time_index = np.arange(16, dtype=np.float64)
        constraint = np.column_stack(
            [
                np.exp(2j * np.pi * frequency_hz * time_index * 0.05)
                for frequency_hz in raw_pair_hz
            ]
        )
        covariance_solutions = np.linalg.solve(loaded_covariance, constraint)
        gram = constraint.conj().T @ covariance_solutions
        ones = np.ones(2, dtype=np.complex128)
        expected_objective = float((ones.conj() @ np.linalg.solve(gram, ones)).real)
        breath_index = int(np.argmin(abs(loaded["f1_grid_hz"] - raw_pair_hz[0])))
        heart_index = int(np.argmin(abs(loaded["f2_grid_hz"] - raw_pair_hz[1])))
        observed_objective = loaded["medoid_regularized_kotte_power"][
            breath_index, heart_index
        ]
        np.testing.assert_allclose(
            observed_objective, expected_objective, rtol=2e-9, atol=1e-12
        )


# =======================================================================================
# Control layer (src/m9/paper_control.py) — verdicts, seeds, generator, comparators
# =======================================================================================

from src.m9.paper_control import (  # noqa: E402
    VERDICT_AMBIGUOUS,
    VERDICT_NOT_REPRODUCED,
    VERDICT_REPRODUCED,
    alternate_range_fft_gain_snr_db,
    assemble_run_meta,
    case_seed,
    classify_case,
    combine_verdicts,
    effective_snr_db,
    evaluate_comparators,
    evaluate_proposed,
    generate_case_yt,
    load_experiment_config,
    merged_peak_frequencies,
    require_clean_tree,
    run_ablation,
    run_audits,
    run_r1,
    run_r2,
    run_r3,
    write_json,
)
import src.m9.paper_control as paper_control_module  # noqa: E402


def test_verdict_truth_table():
    assert classify_case(False, False) == VERDICT_NOT_REPRODUCED
    assert classify_case(False, True) == VERDICT_NOT_REPRODUCED
    assert classify_case(True, False) == VERDICT_AMBIGUOUS
    assert classify_case(True, True) == VERDICT_REPRODUCED


def test_combine_verdicts_precedence_and_no_upgrade():
    assert combine_verdicts([VERDICT_REPRODUCED, VERDICT_NOT_REPRODUCED]) == (
        VERDICT_NOT_REPRODUCED
    )
    assert combine_verdicts([VERDICT_REPRODUCED, VERDICT_AMBIGUOUS]) == VERDICT_AMBIGUOUS
    assert combine_verdicts([VERDICT_REPRODUCED] * 3) == VERDICT_REPRODUCED
    # Ambiguous cannot be upgraded by any number of passing cases.
    assert combine_verdicts([VERDICT_AMBIGUOUS] + [VERDICT_REPRODUCED] * 10) == (
        VERDICT_AMBIGUOUS
    )


def test_case_seed_is_deterministic_distinct_and_order_free():
    a1 = case_seed(20260806, "r1_fig8:ratio=1.0")
    b1 = case_seed(20260806, "r1_fig8:ratio=0.5")
    a2 = case_seed(20260806, "r1_fig8:ratio=1.0")
    assert a1 == a2
    assert a1 != b1
    assert case_seed(1, "x") != case_seed(2, "x")


def test_generator_two_line_power_ratio_and_reproducibility():
    beta1 = (1.0 + 1.0j) / np.sqrt(2.0)
    beta2 = 0.1 * beta1
    y1, sigma2 = generate_case_yt(
        beta1=beta1, beta2=beta2, f1_hz=2.0, f2_hz=1.0, theta0_deg=-30.0,
        n_c=N_C, n_r=400, t_pri_s=T_PRI, snr_db=7.0, seed=99,
    )
    expected_sigma2 = (abs(beta1) ** 2 + abs(beta2) ** 2) / (10.0 ** 0.7)
    np.testing.assert_allclose(sigma2, expected_sigma2, rtol=1e-12)
    # Empirical noise power over 16*400 elements matches sigma^2 (statistical).
    s = beta1 * _steer(2.0) + beta2 * _steer(1.0)
    clean = np.outer(s, spatial_steering(np.deg2rad(-30.0), 400))
    noise_power = float(np.mean(np.abs(y1 - clean) ** 2))
    np.testing.assert_allclose(noise_power, sigma2, rtol=0.05)
    y2, _ = generate_case_yt(
        beta1=beta1, beta2=beta2, f1_hz=2.0, f2_hz=1.0, theta0_deg=-30.0,
        n_c=N_C, n_r=400, t_pri_s=T_PRI, snr_db=7.0, seed=99,
    )
    np.testing.assert_array_equal(y1, y2)


def test_merged_peak_rule_merges_and_ranks():
    freqs = np.linspace(-5.0, 5.0, 1001)
    spectrum = np.zeros_like(freqs)

    def bump(center, height, width=0.05):
        spectrum[np.abs(freqs - center) < width] = height

    bump(1.0, 1.0)
    bump(1.1, 0.8)   # inside merge radius of the 1.0 peak -> merged away
    bump(-3.0, 0.5)
    bump(4.0, 0.2)
    peaks = merged_peak_frequencies(freqs, spectrum, merge_radius_hz=0.25, top_k=2)
    assert len(peaks) == 2
    assert abs(peaks[0] - 1.0) < 0.06
    assert abs(peaks[1] - (-3.0)) < 0.06


def test_run_r1_structure_and_determinism():
    config = load_experiment_config()
    first = run_r1(config)
    second = run_r1(config)
    first.pop("_case_map")
    second.pop("_case_map")
    assert first == second
    assert first["verdict"] in (
        VERDICT_NOT_REPRODUCED, VERDICT_AMBIGUOUS, VERDICT_REPRODUCED
    )
    assert len(first["cases"]) == 3
    assert len(first["comparator_cells"]) == 3
    for cell in first["comparator_cells"]:
        assert set(cell["expected"]) == {"ratio", "fft_weak", "music_weak", "proposed"}
        assert isinstance(cell["matches"], bool)


def test_audits_cannot_upgrade_and_carry_no_verdict():
    config = load_experiment_config()
    audits = run_audits(config)
    assert "verdict" not in audits
    assert "cannot upgrade" in audits["note"]
    assert "ensemble_covariance" in audits
    assert "comparator_rules" in audits
    # PDF printed p. 117, below Eq. (29): beta1=-beta2 makes the joint
    # coefficient cancel and the method inapplicable.  The audit must preserve
    # that case as a measured expected failure, never as a passing control.
    cancellation = audits["cancellation_predicted_failure"]
    assert cancellation["role"] == "expected_paper_limitation"
    assert len(cancellation["cases"]) == 1
    assert cancellation["cases"][0]["case_id"] == "audit:cancellation:ratio=-1.0"
    assert not cancellation["cases"][0]["primary_hit"]
    assert not cancellation["cases"][0]["secondary_hit"]


def test_cancellation_evidence_round_trips_without_pickle(tmp_path):
    config = load_experiment_config()
    audits = run_audits(config)
    payload = audits["_case_map"]["audit:cancellation:ratio=-1.0"]
    assert payload["case_role"] == "expected_paper_limitation"
    evidence = payload["evidence"]
    required = {
        "y_t", "covariance_slow_time", "algorithm1_power",
        "algorithm1_invalid_mask", "constraint_rcond", "eq26_beta_power",
        "eq26_invalid_mask", "f1_grid_hz", "f2_grid_hz",
    }
    assert set(evidence) >= required
    result = payload["result"]
    artifact = tmp_path / "cancellation_expected_paper_limitation.npz"
    np.savez(
        artifact,
        **{name: np.asarray(evidence[name]) for name in required},
        seed=np.asarray(result.seed, dtype=np.uint32),
        y_t_sha256=np.asarray(result.yt_sha256),
        snr_assumption_id=np.asarray("literal_post_range_yt"),
        case_role=np.asarray(payload["case_role"]),
    )
    with np.load(artifact, allow_pickle=False) as restored:
        assert set(restored.files) >= required | {
            "seed", "y_t_sha256", "snr_assumption_id", "case_role"
        }
        assert str(restored["case_role"]) == "expected_paper_limitation"
        np.testing.assert_array_equal(restored["y_t"], evidence["y_t"])


# --- Canonical plan §14: literal primary and alternate range-FFT sensitivity ----------


def test_alternate_snr_adds_range_fft_gain_without_becoming_primary():
    controls = {
        "snr_db": 0.0,
        "snr_reference": "yt_domain_literal",
        "n_s_fast_time": 128,
    }
    assert effective_snr_db(controls) == 0.0
    np.testing.assert_allclose(
        alternate_range_fft_gain_snr_db(controls), 10.0 * np.log10(128.0), rtol=1e-12
    )
    np.testing.assert_allclose(
        alternate_range_fft_gain_snr_db({**controls, "snr_db": -3.0}),
        -3.0 + 10.0 * np.log10(128.0),
        rtol=1e-12,
    )


def test_effective_snr_db_literal_reference_is_identity():
    controls = {"snr_db": 0.0, "snr_reference": "yt_domain_literal"}
    assert effective_snr_db(controls) == 0.0


def test_effective_snr_db_fails_closed_on_missing_or_unknown_reference():
    with pytest.raises(ValueError, match="primary snr_reference"):
        effective_snr_db({"snr_db": 0.0})
    with pytest.raises(ValueError, match="primary snr_reference"):
        effective_snr_db({"snr_db": 0.0, "snr_reference": "guessed"})
    with pytest.raises(ValueError, match="primary snr_reference"):
        effective_snr_db(
            {"snr_db": 0.0, "snr_reference": "fast_time_with_range_fft_gain"}
        )
    with pytest.raises(ValueError, match="n_s_fast_time"):
        alternate_range_fft_gain_snr_db(
            {
                "snr_db": 0.0,
                "snr_reference": "yt_domain_literal",
                "n_s_fast_time": 1,
            }
        )


def test_committed_config_declares_literal_primary_and_separate_alternate():
    controls = load_experiment_config().controls
    assert controls["snr_reference"] == "yt_domain_literal"
    assert controls["n_s_fast_time"] == 128, "the paper's own N_s (§IV)"
    assert effective_snr_db(controls) == 0.0
    np.testing.assert_allclose(
        alternate_range_fft_gain_snr_db(controls), 21.0721, atol=1e-4
    )
    rule = controls["comparators"]["weak_peak_rule"]
    # Half mainlobe width 1/(2 * n_c * t_pri) = 0.625 Hz.
    expected = 1.0 / (2.0 * int(controls["n_c"]) * float(controls["t_pri_s"]))
    np.testing.assert_allclose(float(rule["weak_tolerance_hz"]), expected, rtol=1e-12)
    assert controls["audits"]["alternate_range_fft_gain_sensitivity"] is True


def test_committed_direct_controls_match_pdf_declared_conditions():
    # PDF printed pp. 118-120: N_c=16, N_R=20, T_PRI=50 ms; Table I supplies
    # the signed Fig. 5 pairs; Fig. 7 row 2 uses (1.5, 1) Hz with beta2=beta1/2;
    # Fig. 8 uses (2, 1) Hz at the explicitly printed SNR=0 dB.
    controls = load_experiment_config().controls
    assert (controls["n_c"], controls["n_r"], controls["t_pri_s"]) == (16, 20, 0.05)
    assert controls["snr_db"] == 0.0
    assert controls["r1_fig8"]["f_strong_hz"] == 2.0
    assert controls["r1_fig8"]["f_weak_hz"] == 1.0
    assert controls["r1_fig8"]["ratios"] == [1.0, 0.5, 0.1]
    assert [row["f_pair_hz"] for row in controls["r2_fig5"]["targets"]] == [
        [-1.0, -2.0],
        [-1.0, 4.0],
        [1.0, 2.5],
    ]
    assert controls["r3_fig7_row2"]["f_strong_hz"] == 1.5
    assert controls["r3_fig7_row2"]["f_weak_hz"] == 1.0
    assert controls["r3_fig7_row2"]["beta2_ratio"] == 0.5


def test_literal_primary_and_alternate_snr_results_are_separately_labelled():
    config = load_experiment_config()
    literal_sections = {
        "fig8": run_r1(config),
        "fig5": run_r2(config),
        "fig7_row2": run_r3(config),
    }
    audits = run_audits(config)
    alternate = audits["alternate_range_fft_gain_sensitivity"]
    for section_name, literal in literal_sections.items():
        assert literal["snr_db"] == 0.0, section_name
        assert literal["snr_assumption_id"] == "literal_post_range_yt", section_name
        assert literal["snr_assumption_role"] == "primary_literal_interpretation", section_name
        sensitivity = alternate["sections"][section_name]
        assert sensitivity["snr_assumption_id"] == "alternate_range_fft_gain"
        assert sensitivity["snr_assumption_role"] == "interpretive_sensitivity_nonprimary"
        assert sensitivity["snr_db"] > literal["snr_db"]
        literal_hashes = {case["yt_sha256"] for case in literal["cases"]}
        sensitivity_hashes = {case["yt_sha256"] for case in sensitivity["cases"]}
        assert literal_hashes.isdisjoint(sensitivity_hashes), section_name
    assert not any(case["primary_hit"] for case in literal_sections["fig8"]["cases"])
    np.testing.assert_allclose(alternate["effective_yt_snr_db"], 21.0721, atol=1e-4)
    assert alternate["role"] == "DIAGNOSTIC_NONPRIMARY_INTERPRETIVE_SENSITIVITY"
    assert "not_a_paper_fact" in alternate["sections"]["fig8"]["snr_assumption_status"]


def test_signed_fig5_cases_recover_under_recorded_alternate_assumption():
    # Fig. 5 and Table I, PDF p. 118: (-1,-2), (-1,4), and (1,2.5) Hz.
    config = load_experiment_config()
    alternate_snr_db = alternate_range_fft_gain_snr_db(config.controls)
    result = run_r2(config, snr_override_db=alternate_snr_db)
    assert len(result["cases"]) == 3
    for case in result["cases"]:
        assert case["primary_hit"], case


def test_direct_control_evidence_contains_both_surfaces_masks_seed_and_hash():
    config = load_experiment_config()
    result = run_r1(config)
    case_id, payload = next(iter(result["_case_map"].items()))
    evidence = payload["evidence"]
    case_result = payload["result"]
    assert case_result.seed == case_seed(config.controls["root_seed"], case_id)
    assert set(evidence) >= {
        "y_t", "covariance_slow_time", "algorithm1_power", "eq26_beta_power",
        "algorithm1_invalid_mask", "eq26_invalid_mask", "constraint_rcond",
        "f1_grid_hz", "f2_grid_hz",
    }
    assert evidence["y_t"].shape == (16, 20)
    assert np.issubdtype(evidence["y_t"].dtype, np.complexfloating)
    assert evidence["covariance_slow_time"].shape == (16, 16)
    # Algorithm 1, PDF printed p. 117: the persisted covariance must be bound
    # to this exact direct Y_t realization and use the N_R divisor.
    expected_covariance = evidence["y_t"] @ evidence["y_t"].conj().T / 20
    np.testing.assert_allclose(
        evidence["covariance_slow_time"], expected_covariance, rtol=0.0, atol=0.0
    )
    expected_hash = sha256(np.ascontiguousarray(evidence["y_t"]).tobytes()).hexdigest()
    assert case_result.yt_sha256 == expected_hash
    assert evidence["algorithm1_power"].shape == evidence["eq26_beta_power"].shape
    assert evidence["algorithm1_power"].shape == (400, 400)
    assert evidence["constraint_rcond"].shape == (400, 400)
    assert evidence["algorithm1_invalid_mask"].dtype == np.bool_
    assert evidence["eq26_invalid_mask"].dtype == np.bool_
    np.testing.assert_array_equal(
        evidence["algorithm1_invalid_mask"], ~np.isfinite(evidence["algorithm1_power"])
    )
    np.testing.assert_array_equal(
        evidence["eq26_invalid_mask"], ~np.isfinite(evidence["eq26_beta_power"])
    )
    assert evidence["f1_grid_hz"].shape == (400,)
    assert evidence["f1_grid_hz"][0] == -10.0
    assert evidence["f1_grid_hz"][-1] > 0.0
    np.testing.assert_array_equal(evidence["f1_grid_hz"], evidence["f2_grid_hz"])


def test_direct_control_zero_energy_returns_clear_invalid_reason():
    config = load_experiment_config()
    result, evidence = evaluate_proposed(
        np.zeros((16, 20), dtype=np.complex128),
        case_id="zero_energy",
        seed=0,
        truth_pair_hz=(2.0, 1.0),
        theta0_deg=-30.0,
        controls=config.controls,
        grid_hz=np.array([-2.0, -1.0, 1.0, 2.0]),
    )
    assert result.primary_selected_hz is None
    assert result.secondary_selected_hz is None
    assert result.dsp_failure == "rank_zero"
    assert evidence["y_t"].shape == (16, 20)


def test_r1_verdict_comes_from_truth_table_not_the_comparator_matrix():
    config = load_experiment_config()
    result = run_r1(config)
    result.pop("_case_map")
    assert result["verdict_source"] == "truth_table_over_proposed_primary_and_secondary"
    assert result["comparator_role"] == "diagnostic_corroboration_not_gating"
    # Every enumerated mismatch is a real expected-vs-observed disagreement.
    for entry in result["comparator_mismatches"]:
        cell = next(
            c for c in result["comparator_cells"]
            if c["observed"]["ratio"] == entry["ratio"]
        )
        assert cell["expected"][entry["quantity"]] == entry["expected"]
        assert cell["observed"][entry["quantity"]] == entry["observed"]
    assert result["comparator_matrix_matches"] == (not result["comparator_mismatches"])
    # The verdict is a pure function of the per-case truth-table classifications.
    assert result["verdict"] == combine_verdicts(result["case_verdicts"])


def test_clean_tree_refusal_official_vs_smoke(monkeypatch):
    monkeypatch.setattr(
        paper_control_module, "git_status_paths", lambda cwd=None: {"src/m9/x.py"}
    )
    with pytest.raises(RuntimeError, match="clean git tree"):
        require_clean_tree(smoke=False)
    assert require_clean_tree(smoke=True) == ["src/m9/x.py"]
    assert require_clean_tree(implementation_validation=True) == ["src/m9/x.py"]
    with pytest.raises(ValueError, match="mutually exclusive"):
        require_clean_tree(smoke=True, implementation_validation=True)


def test_implementation_validation_metadata_is_gate_only(monkeypatch):
    monkeypatch.setattr(
        paper_control_module,
        "git_text",
        lambda *args, **kwargs: "test-git-value",
    )
    monkeypatch.setattr(
        paper_control_module,
        "environment_attestation",
        lambda: {"schema_version": 1, "test": True},
    )
    exact_invocation = (
        "& 'C:\\ProgramData\\anaconda3\\condabin\\conda.bat' run -n radar-vitals "
        "python -X utf8 figures/reproduce_kotte_controls.py paper "
        "--sections r1,r2,r3,audits --implementation-validation"
    )
    metadata = assemble_run_meta(
        load_experiment_config(),
        subcommand="paper",
        run_classification="implementation_validation_non_thesis",
        exact_invocation=exact_invocation,
        dirty_paths=["src/m9/kotte_core.py"],
        case_seed_map={},
        output_hashes={},
    )
    assert metadata["run_classification"] == "implementation_validation_non_thesis"
    assert metadata["exact_invocation"] == exact_invocation
    assert metadata["implementation_gate_eligible"] is True
    assert metadata["thesis_evidence_eligible"] is False
    assert metadata["empirical_outcome_decision_eligible"] is False
    assert metadata["git_tree_clean"] is False
    with pytest.raises(RuntimeError, match="official_clean_tree"):
        assemble_run_meta(
            load_experiment_config(),
            subcommand="paper",
            run_classification="official_clean_tree",
            exact_invocation=exact_invocation,
            dirty_paths=["src/m9/kotte_core.py"],
            case_seed_map={},
            output_hashes={},
        )


def test_write_json_is_lf_only(tmp_path):
    target = tmp_path / "out.json"
    write_json(target, {"a": 1, "b": [1.5, None]})
    data = target.read_bytes()
    assert b"\r" not in data
    assert data.endswith(b"\n")


def test_ablation_rank_predictions_and_structure():
    config = load_experiment_config()
    result = run_ablation(config)
    assert len(result["rows"]) == 6 * 2 * 2  # 6 cases x 2 endpoints x 2 deltas
    for row in result["rows"]:
        if row["n_r"] == 4:
            assert row["rank"] == 4, "noise must supply exactly column rank at 4 RX"
        else:
            assert row["rank"] == N_C
    assert result["phase_invariance_max_rel_dev"] < 1e-9
    assert len(result["per_case_pass"]) == 6
    assert result["snr_db"] == 0.0
    assert result["snr_assumption_id"] == "literal_post_range_yt"
    assert result["method_role"] == "declared_loaded_4rx_adaptation_not_literal_kotte"
    assert any("pooling is impossible" in note for note in result["notes"])
    assert isinstance(result["all_cases_pass"], bool)


def test_evaluate_comparators_reports_rule_parameters():
    config = load_experiment_config()
    controls = config.controls
    beta1 = (1.0 + 1.0j) / np.sqrt(2.0)
    y, _ = generate_case_yt(
        beta1=beta1, beta2=beta1, f1_hz=2.0, f2_hz=1.0, theta0_deg=-30.0,
        n_c=N_C, n_r=20, t_pri_s=T_PRI, snr_db=0.0, seed=1,
    )
    from src.m9.kotte_core import make_grid_hz as _mg

    grid = _mg(-10.0, 10.0, 0.05)
    out = evaluate_comparators(y, controls=controls, grid_hz=grid, f_weak_hz=1.0)
    assert set(out) >= {
        "fft_weak_detected", "music_weak_detected", "fft_top_peaks_hz",
        "music_top_peaks_hz", "merge_radius_hz", "weak_tolerance_hz",
    }
    assert out["merge_radius_hz"] == 0.25
    assert out["music_model_order"] == 2
