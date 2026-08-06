"""Core tests for src/m9/kotte_core.py (M9 step 1, plans/m9_kotte_plan.md test plan).

Covers the DSP maths only — the aggregation contract (medoid, partial-CPI semantics,
tail/detrend order) and the suite arrive with plan step 3 and extend this file.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import yaml

from src.m9.kotte_core import (
    CPI_CAUSE_CODES,
    AllMaskedError,
    CaponContext,
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
    rng = np.random.default_rng(0)
    y = rng.standard_normal((N_C, 7)) + 1j * rng.standard_normal((N_C, 7))
    expected = y @ y.conj().T / 7
    np.testing.assert_allclose(sample_covariance(y), expected, rtol=0, atol=0)


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
    y, _ = _two_tone(1.0, 0.5, 2.0, 1.0, 0.3, 20, 1.0, seed=5)
    r = sample_covariance(y)
    ctx = _ctx(r)
    w, _h = _direct_w(ctx.solve, 3.3, -1.7)
    lhs = float((w.conj() @ r @ w).real)
    rhs = capon_power_at(ctx, 3.3, -1.7, n_c=N_C, t_pri_s=T_PRI)
    np.testing.assert_allclose(lhs, rhs, rtol=1e-9)


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


def test_all_masked_raises():
    with pytest.raises(AllMaskedError):
        argmax_masked(np.full((3, 3), np.nan))


def test_vectorized_objective_equals_loop():
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


def test_alias_collapse_tie_break_prefers_positive_quadrant():
    f1 = signed_band_grid_hz((0.10, 0.30), 0.10)
    f2 = signed_band_grid_hz((0.80, 1.00), 0.10)
    surface = np.zeros((f1.size, f2.size))
    # Same value in all four sign quadrants of (0.2, 0.9): (+,+) must win.
    for s1 in (1, -1):
        for s2 in (1, -1):
            i = int(np.argmin(np.abs(f1 - s1 * 0.2)))
            j = int(np.argmin(np.abs(f2 - s2 * 0.9)))
            surface[i, j] = 5.0
    collapsed = alias_collapse(surface, f1, f2)
    ci = int(np.argmin(np.abs(collapsed.abs_f1_hz - 0.2)))
    cj = int(np.argmin(np.abs(collapsed.abs_f2_hz - 0.9)))
    assert collapsed.sign_f1[ci, cj] == 1
    assert collapsed.sign_f2[ci, cj] == 1


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
        "kotte_pooled_nc16_dl1em2",
    ]
    assert config.min_valid_cpi_fraction == 1.0


def test_config_rejects_partial_cpi_fraction():
    stage_a = _stage_a_dict()
    stage_a["min_valid_cpi_fraction"] = 0.9
    with pytest.raises(ValueError, match="exactly 1.0"):
        KotteJointDopplerConfig.from_stage_a(stage_a)


def test_arm_rejects_unknown_form_and_bad_delta():
    with pytest.raises(ValueError, match="estimator_form"):
        KotteArm("x", 16, "argmax", 1e-2)
    with pytest.raises(ValueError, match="loading_delta"):
        KotteArm("x", 16, "pooled", 0.0)


def test_per_arm_hash_uniqueness_and_stability():
    config = KotteJointDopplerConfig.from_stage_a(_stage_a_dict())
    hashes = [config.run_config_hash(arm) for arm in config.arms]
    assert len(set(hashes)) == len(hashes)
    config2 = KotteJointDopplerConfig.from_stage_a(_stage_a_dict())
    assert [config2.run_config_hash(a) for a in config2.arms] == hashes
    assert config.suite_config_hash() == config2.suite_config_hash()


def test_dsp_config_contains_no_capture_knowledge():
    # Adding a capture manifest entry (runner input) must change NO estimator hash.
    stage_a = _stage_a_dict()
    baseline = KotteJointDopplerConfig.from_stage_a(stage_a)
    baseline_hashes = {
        "suite": baseline.suite_config_hash(),
        "arms": [baseline.run_config_hash(a) for a in baseline.arms],
    }
    stage_a["captures"] = list(stage_a["captures"]) + [
        {
            "capture_id": "massimo99",
            "directory": "results/live_demo/does_not_exist",
            "subject": "Z",
            "masimo_csv": "demo_massimo99.csv",
            "protocol_role": "natural",
            "data_role": "exploratory",
        }
    ]
    stage_a["candidate_bins"] = list(stage_a["candidate_bins"]) + [33]
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
    n_chirps: int = 4,
    n_rx: int = 4,
    n_adc: int = 32,
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
    range_tone = np.exp(2j * np.pi * target_bin * n / n_adc) / np.hanning(n_adc).clip(1e-6)
    cube = np.empty((n_frames, n_chirps, n_rx, n_adc), dtype=np.complex128)
    for rx in range(n_rx):
        gain = 1.0 + 0.05 * rx
        cube[:, :, rx, :] = (
            gain * envelope[:, None, None] * range_tone[None, None, :]
        )
    cube += noise_scale * (
        rng.standard_normal(cube.shape) + 1j * rng.standard_normal(cube.shape)
    )
    return cube


def _stage_a_config() -> KotteJointDopplerConfig:
    return KotteJointDopplerConfig.from_stage_a(_stage_a_dict())


def test_extract_rx_slow_time_averages_only_chirps_and_keeps_rx():
    cube = _synthetic_cube(n_frames=40, noise_scale=0.0)
    z = extract_rx_slow_time(cube, locked_bin=7)
    assert z.shape == (40, 4)
    assert z.dtype == np.complex128
    # Exactly the coherent chirp mean of the per-chirp range-FFT bin.
    hann = np.hanning(cube.shape[3]).astype(np.float32)
    expected = np.fft.fft(cube * hann, axis=3)[:, :, :, 7].mean(axis=1)
    np.testing.assert_allclose(z, expected, rtol=1e-12, atol=1e-12)


def test_extract_rejects_out_of_range_bin():
    cube = _synthetic_cube(n_frames=8)
    with pytest.raises(ValueError, match="locked_bin"):
        extract_rx_slow_time(cube, locked_bin=999)


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
    z = np.zeros((600, 1), dtype=np.complex128)
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
    z = extract_rx_slow_time(_synthetic_cube(), locked_bin=7)
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


def test_estimate_window_missing_concepts_are_absent_keys():
    config = _stage_a_config()
    pooled = next(a for a in config.arms if a.estimator_form == "pooled")
    z = extract_rx_slow_time(_synthetic_cube(), locked_bin=7)
    native, _ = estimate_window(z, config=config, arm=pooled, fs_hz=FS_HZ)
    # The pooled form has no per-CPI medoid, so the key is ABSENT rather than a sentinel.
    assert "medoid_cpi_index" not in native
    assert "cpi_iqr_br_bpm" not in native
    assert native["n_snapshots"] == 37 * 4


def test_pooled_form_reaches_full_rank_where_a_single_cpi_cannot():
    config = _stage_a_config()
    pooled = next(a for a in config.arms if a.estimator_form == "pooled")
    z = extract_rx_slow_time(_synthetic_cube(), locked_bin=7)
    native, _ = estimate_window(z, config=config, arm=pooled, fs_hz=FS_HZ)
    # 4 RX snapshots per CPI cannot span 16 dimensions; pooled snapshots can.
    assert native["rank_rt"] == 16
    assert native["br_valid"]


def test_single_cpi_rank_is_four_at_four_rx():
    config = _stage_a_config()
    arm = next(a for a in config.arms if a.estimator_form == "cpi_medoid")
    z = extract_rx_slow_time(_synthetic_cube(), locked_bin=7)
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
    z = np.array(extract_rx_slow_time(_synthetic_cube(), locked_bin=7))
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
    z = np.zeros((592, 4), dtype=np.complex128)
    alternating = np.where(np.arange(592) % 2 == 0, 1.0, -1.0)
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
    z = np.array(extract_rx_slow_time(_synthetic_cube(), locked_bin=7))
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
        z = extract_rx_slow_time(_synthetic_cube(n_frames=160), locked_bin=7)
        _native, evidence = estimate_window(z, config=config, arm=arm, fs_hz=FS_HZ)
        for key, value in evidence.items():
            if isinstance(value, np.ndarray):
                assert value.dtype != object, key
                assert not value.flags.writeable, key


def test_evidence_npz_round_trips_without_pickle(tmp_path):
    config = _stage_a_config()
    arm = config.arms[0]
    z = extract_rx_slow_time(_synthetic_cube(n_frames=160), locked_bin=7)
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
# Control layer (src/m9/paper_control.py) — verdicts, seeds, generator, comparators
# =======================================================================================

from src.m9.paper_control import (  # noqa: E402
    VERDICT_AMBIGUOUS,
    VERDICT_NOT_REPRODUCED,
    VERDICT_REPRODUCED,
    case_seed,
    classify_case,
    combine_verdicts,
    effective_snr_db,
    evaluate_comparators,
    generate_case_yt,
    load_experiment_config,
    merged_peak_frequencies,
    require_clean_tree,
    run_ablation,
    run_audits,
    run_r1,
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


# --- The 2026-08-06 option-B amendment (plans/m9_step1a_snr_finding.md) ---------------


def test_effective_snr_db_folds_range_fft_gain():
    controls = {
        "snr_db": 0.0,
        "snr_reference": "fast_time_with_range_fft_gain",
        "n_s_fast_time": 128,
    }
    np.testing.assert_allclose(
        effective_snr_db(controls), 10.0 * np.log10(128.0), rtol=1e-12
    )
    # The gain is additive on top of whatever base SNR is declared.
    np.testing.assert_allclose(
        effective_snr_db({**controls, "snr_db": -3.0}),
        -3.0 + 10.0 * np.log10(128.0),
        rtol=1e-12,
    )


def test_effective_snr_db_literal_reference_is_identity():
    controls = {"snr_db": 0.0, "snr_reference": "yt_domain_literal"}
    assert effective_snr_db(controls) == 0.0


def test_effective_snr_db_fails_closed_on_missing_or_unknown_reference():
    with pytest.raises(ValueError, match="unknown snr_reference"):
        effective_snr_db({"snr_db": 0.0})
    with pytest.raises(ValueError, match="unknown snr_reference"):
        effective_snr_db({"snr_db": 0.0, "snr_reference": "guessed"})
    with pytest.raises(ValueError, match="n_s_fast_time"):
        effective_snr_db(
            {
                "snr_db": 0.0,
                "snr_reference": "fast_time_with_range_fft_gain",
                "n_s_fast_time": 1,
            }
        )


def test_committed_config_declares_the_amended_assumptions():
    controls = load_experiment_config().controls
    assert controls["snr_reference"] == "fast_time_with_range_fft_gain"
    assert controls["n_s_fast_time"] == 128, "the paper's own N_s (§IV)"
    np.testing.assert_allclose(effective_snr_db(controls), 21.0721, atol=1e-4)
    rule = controls["comparators"]["weak_peak_rule"]
    # Half mainlobe width 1/(2 * n_c * t_pri) = 0.625 Hz.
    expected = 1.0 / (2.0 * int(controls["n_c"]) * float(controls["t_pri_s"]))
    np.testing.assert_allclose(float(rule["weak_tolerance_hz"]), expected, rtol=1e-12)
    assert controls["audits"]["snr_reference_literal"] is True


def test_literal_snr_audit_is_recorded_and_carries_no_verdict():
    config = load_experiment_config()
    audits = run_audits(config)
    literal = audits["snr_reference_literal"]
    assert literal["snr_db_effective"] == 0.0
    np.testing.assert_allclose(literal["primary_snr_db_effective"], 21.0721, atol=1e-4)
    assert "verdict" not in literal
    assert len(literal["cases"]) == 3
    # The measured fact this audit exists to preserve: the literal reading misses.
    assert not any(case["primary_hit"] for case in literal["cases"])


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
