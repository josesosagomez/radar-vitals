"""M8 Step 1a tests for the isolated Ahmed Fig. 8(c)-(d) reproduction."""
from __future__ import annotations

from dataclasses import replace
import importlib.util
import json
import math
from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.special import jv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.m8.ahmed_fig8 import (  # noqa: E402
    SUPPRESSION_PROFILES,
    AhmedConfig,
    accumulate_harmonics,
    amplitude_ratio_variant,
    build_adapter_result,
    compute_ha_scores,
    config_hash,
    derive_variant_seed,
    run_profile,
    simulate_eq14,
    _select_scores,
)
from src.window_pipeline import as_window_estimate  # noqa: E402


def _load_figure_script():
    path = ROOT / "figures" / "reproduce_ahmed_fig8.py"
    spec = importlib.util.spec_from_file_location("reproduce_ahmed_fig8", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def figure_script():
    return _load_figure_script()


@pytest.fixture(scope="module")
def primary_config() -> AhmedConfig:
    return AhmedConfig()


@pytest.fixture(scope="module")
def primary_signal(primary_config: AhmedConfig):
    return simulate_eq14(primary_config, np.random.default_rng(primary_config.seed))


def test_primary_derived_parameters_are_locked(primary_config: AhmedConfig):
    expected_wavelength = 3.0e8 / 6.7e9
    expected_bandwidth = (
        2.0
        * (80.0 / 60.0)
        * (4.0 * math.pi * 0.010 / expected_wavelength)
    )
    expected_prf = 5.0 * expected_bandwidth
    expected_sample_count_float = expected_prf * 15.0
    assert primary_config.wavelength_m == pytest.approx(expected_wavelength)
    assert primary_config.heart_modulation_bandwidth_hz == pytest.approx(
        expected_bandwidth
    )
    assert primary_config.prf_hz == pytest.approx(expected_prf)
    assert primary_config.requested_duration_s == pytest.approx(15.0)
    assert primary_config.sample_count_float == pytest.approx(
        expected_sample_count_float
    )
    assert primary_config.sample_count == 561
    assert primary_config.effective_duration_s == pytest.approx(
        561 / primary_config.prf_hz
    )
    assert primary_config.last_sample_time_s == pytest.approx(
        560 / primary_config.prf_hz
    )


def test_equation14_is_sum_of_independent_returns(primary_config: AhmedConfig):
    signal = simulate_eq14(primary_config, np.random.default_rng(42))
    assert signal.t_s[0] == 0.0
    assert signal.clean_heart[0] == pytest.approx(1.0)
    assert signal.clean_breath[0] == pytest.approx(1.0)
    assert signal.clean_combined[0] == pytest.approx(2.0)
    assert np.array_equal(
        signal.clean_combined, signal.clean_heart + signal.clean_breath
    )
    index = 137
    t = signal.t_s[index]
    expected_heart = math.cos(
        primary_config.heart_phase_index
        * math.sin(2 * math.pi * primary_config.heart_hz * t)
    )
    expected_breath = math.cos(
        primary_config.breath_phase_index
        * math.sin(2 * math.pi * primary_config.breath_hz * t)
    )
    assert signal.clean_heart[index] == pytest.approx(expected_heart)
    assert signal.clean_breath[index] == pytest.approx(expected_breath)
    summed_phase_wrong_model = math.cos(
        primary_config.heart_phase_index
        * math.sin(2 * math.pi * primary_config.heart_hz * t)
        + primary_config.breath_phase_index
        * math.sin(2 * math.pi * primary_config.breath_hz * t)
    )
    assert signal.clean_combined[index] != pytest.approx(summed_phase_wrong_model)


@pytest.mark.parametrize(
    ("component", "phase_index"),
    (("clean_heart", "heart_phase_index"), ("clean_breath", "breath_phase_index")),
)
def test_equation14_harmonic_family_matches_bessel_oracle(
    primary_config: AhmedConfig,
    component: str,
    phase_index: str,
):
    # Choose a coherent grid: PRF/f_h=32, N=4096, so both f_h and f_b
    # complete an integer number of cycles. This makes the FFT coefficients an
    # independent Jacobi-Anger oracle rather than another copy of Eq. (14).
    coherent_prf = 32.0 * primary_config.heart_hz
    multiplier = coherent_prf / primary_config.heart_modulation_bandwidth_hz
    sample_count = 4096
    duration_breaths = (
        sample_count * primary_config.breath_hz / coherent_prf
    )
    base = replace(
        primary_config,
        prf_bandwidth_multiplier=multiplier,
        duration_breaths=duration_breaths,
        n_fft=sample_count,
        snr_db=300.0,
    )
    beta = float(getattr(base, phase_index))
    fundamental_bin = (
        128 if component == "clean_heart" else 32
    )

    even_signal = simulate_eq14(base, np.random.default_rng(1))
    even_spectrum = np.fft.rfft(getattr(even_signal, component)) / sample_count
    even_amplitude_1 = 2.0 * abs(even_spectrum[fundamental_bin])
    even_amplitude_2 = 2.0 * abs(even_spectrum[2 * fundamental_bin])
    assert even_amplitude_1 < 1e-12
    assert even_amplitude_2 == pytest.approx(abs(2.0 * jv(2, beta)), abs=1e-12)

    odd_config = replace(base, theta0_rad=math.pi / 2.0)
    odd_signal = simulate_eq14(odd_config, np.random.default_rng(1))
    odd_spectrum = np.fft.rfft(getattr(odd_signal, component)) / sample_count
    odd_amplitude_1 = 2.0 * abs(odd_spectrum[fundamental_bin])
    odd_amplitude_2 = 2.0 * abs(odd_spectrum[2 * fundamental_bin])
    assert odd_amplitude_1 == pytest.approx(abs(2.0 * jv(1, beta)), abs=1e-12)
    assert odd_amplitude_2 < 1e-12


def test_signal_spacing_noise_contract_and_readonly_arrays(
    primary_config: AhmedConfig,
    primary_signal,
):
    assert len(primary_signal.t_s) == primary_config.sample_count
    assert np.diff(primary_signal.t_s) == pytest.approx(1.0 / primary_config.prf_hz)
    expected_power = float(np.mean(primary_signal.clean_combined**2))
    expected_sigma = math.sqrt(expected_power / 10.0)
    assert primary_signal.reference_power == pytest.approx(expected_power)
    assert primary_signal.noise_sigma == pytest.approx(expected_sigma)
    assert np.array_equal(
        primary_signal.samples, primary_signal.clean_combined + primary_signal.noise
    )
    assert np.isrealobj(primary_signal.noise)
    assert primary_signal.realized_snr_db == pytest.approx(
        10
        * math.log10(
            expected_power / float(np.mean(np.square(primary_signal.noise)))
        )
    )
    for array in (
        primary_signal.t_s,
        primary_signal.clean_heart,
        primary_signal.clean_breath,
        primary_signal.clean_combined,
        primary_signal.standard_normal,
        primary_signal.noise,
        primary_signal.samples,
    ):
        assert not array.flags.writeable


def test_seed_changes_only_noise(primary_config: AhmedConfig):
    a = simulate_eq14(primary_config, np.random.default_rng(1))
    b = simulate_eq14(primary_config, np.random.default_rng(2))
    assert np.array_equal(a.clean_combined, b.clean_combined)
    assert not np.array_equal(a.noise, b.noise)
    assert not np.array_equal(a.samples, b.samples)
    assert a.signal_hash != b.signal_hash


def test_reused_standard_normal_is_order_independent(primary_config: AhmedConfig):
    z = np.random.default_rng(42).standard_normal(primary_config.sample_count)
    configs = [
        replace(primary_config, theta0_rad=math.pi / 4),
        amplitude_ratio_variant(primary_config, 0.5),
        replace(primary_config, snr_power_mode="centered"),
    ]
    forward = [
        simulate_eq14(cfg, np.random.default_rng(999), standard_normal=z)
        for cfg in configs
    ]
    reverse = {
        config_hash(cfg): simulate_eq14(
            cfg, np.random.default_rng(123), standard_normal=z
        )
        for cfg in reversed(configs)
    }
    for cfg, result in zip(configs, forward, strict=True):
        assert np.array_equal(result.samples, reverse[config_hash(cfg)].samples)


def test_centered_snr_power_uses_centered_clean_signal(primary_config: AhmedConfig):
    config = replace(primary_config, snr_power_mode="centered")
    signal = simulate_eq14(config, np.random.default_rng(42))
    assert signal.reference_power == pytest.approx(
        np.mean(np.square(signal.clean_combined - np.mean(signal.clean_combined)))
    )


def test_amplitude_ratio_preserves_total_squared_scale(primary_config: AhmedConfig):
    for ratio in (0.5, 2.0):
        variant = amplitude_ratio_variant(primary_config, ratio)
        assert variant.heart_amplitude / variant.breath_amplitude == pytest.approx(ratio)
        assert variant.heart_amplitude**2 + variant.breath_amplitude**2 == pytest.approx(2.0)


def test_accumulator_matches_hand_computable_matrix_oracle():
    spectrum = np.arange(20, dtype=float)
    candidates = np.array([1, 2, 3], dtype=np.int64)
    bins, scores = accumulate_harmonics(spectrum, candidates, 3)
    expected_bins = np.array([[1, 2, 3], [2, 4, 6], [3, 6, 9]])
    matrix = np.zeros((3, spectrum.size), dtype=float)
    for row, row_bins in enumerate(expected_bins):
        matrix[row, row_bins] = 1.0 / 3.0
    expected_scores = matrix @ spectrum
    assert np.array_equal(bins, expected_bins)
    assert scores == pytest.approx(expected_scores)
    _, eta_scores = accumulate_harmonics(
        spectrum, candidates, 3, "matrix_eta"
    )
    assert np.array_equal(eta_scores, np.sum(spectrum[expected_bins], axis=1) / 4)


def test_randomized_accumulator_matches_literal_loop():
    rng = np.random.default_rng(20240729)
    for _ in range(100):
        size = int(rng.integers(20, 100))
        harmonics = int(rng.integers(1, 7))
        max_candidate = (size - 1) // harmonics
        candidates = np.arange(1, max_candidate + 1, dtype=np.int64)
        spectrum = rng.random(size)
        _, scores = accumulate_harmonics(spectrum, candidates, harmonics)
        oracle = np.array(
            [
                sum(float(spectrum[h * candidate]) for h in range(1, harmonics + 1))
                / harmonics
                for candidate in candidates
            ]
        )
        assert np.array_equal(scores, oracle)


def test_positive_spectrum_scaling_preserves_argmax():
    spectrum = np.array([0.0, 2.0, 9.0, 1.0, 5.0, 3.0, 4.0])
    candidates = np.array([1, 2], dtype=np.int64)
    _, original = accumulate_harmonics(spectrum, candidates, 3)
    _, scaled = accumulate_harmonics(7.5 * spectrum, candidates, 3)
    assert np.argmax(original) == np.argmax(scaled)
    assert scaled == pytest.approx(7.5 * original)


def test_tie_policy_selects_lowest_frequency_and_all_nan_is_invalid():
    scores = np.array([4.0, 7.0, 7.0])
    bins = np.array([5, 8, 11])
    selected_index, selected_bin, runner_bin, runner_score, unique = _select_scores(
        scores, bins
    )
    assert selected_index == 1
    assert selected_bin == 8
    assert runner_bin == 11
    assert runner_score == 7.0
    assert unique is False
    assert _select_scores(np.full(3, np.nan), bins) == (
        None,
        None,
        None,
        None,
        False,
    )


def test_accumulator_rejects_invalid_domains():
    with pytest.raises(ValueError, match="finite one-dimensional"):
        accumulate_harmonics(np.array([0.0, np.nan]), np.array([1]), 1)
    with pytest.raises(ValueError, match="exclude DC"):
        accumulate_harmonics(np.ones(5), np.array([0]), 1)
    with pytest.raises(ValueError, match="outside"):
        accumulate_harmonics(np.ones(5), np.array([3]), 2)
    with pytest.raises(ValueError, match="positive integer"):
        accumulate_harmonics(np.ones(5), np.array([1]), 0)


def test_harmonic_support_rejects_20_hz_prf_for_h5_heart():
    base = AhmedConfig()
    multiplier = 20.0 / base.heart_modulation_bandwidth_hz
    config = replace(base, prf_bandwidth_multiplier=multiplier)
    assert config.prf_hz == pytest.approx(20.0)
    with pytest.raises(ValueError, match="Nyquist"):
        config.validate_harmonic_support(5, "heart")


def test_config_rejects_too_few_samples_and_invalid_vital(primary_config: AhmedConfig):
    with pytest.raises(ValueError, match="sample_count must be at least 2"):
        replace(primary_config, duration_breaths=1e-6)
    with pytest.raises(ValueError, match="unsupported vital"):
        primary_config.validate_harmonic_support(3, "pulse")  # type: ignore[arg-type]


def test_signal_cannot_be_scored_under_a_different_same_length_config(
    primary_config: AhmedConfig, primary_signal
):
    different = replace(primary_config, theta0_rad=math.pi / 4)
    assert different.sample_count == primary_config.sample_count
    with pytest.raises(ValueError, match="signal/config mismatch"):
        compute_ha_scores(
            primary_signal,
            different,
            3,
            "figure_visible_unsuppressed",
            vital="breath",
        )


def test_primary_profiles_keep_source_disagreement_visible(
    primary_config: AhmedConfig, primary_signal
):
    results = {
        profile: run_profile(primary_signal, primary_config, 3, profile)
        for profile in SUPPRESSION_PROFILES
    }
    breath_bins = {pair[0].selected_bin for pair in results.values()}
    assert len(breath_bins) == 1
    breath_bin = next(iter(breath_bins))
    assert breath_bin is not None
    unsuppressed = results["figure_visible_unsuppressed"][1]
    equation = results["eq26_multiples_suppressed"][1]
    prose = results["prose_low_or_equal_suppressed"][1]
    assert np.all(unsuppressed.eligible)
    assert np.all(equation.candidate_bins[equation.eligible] % breath_bin != 0)
    assert np.all(prose.candidate_bins[prose.eligible] > breath_bin)
    assert np.array_equal(
        unsuppressed.scores_before_exclusion,
        equation.scores_before_exclusion,
    )
    assert np.array_equal(
        unsuppressed.scores_before_exclusion,
        prose.scores_before_exclusion,
    )
    assert equation.status == "inconclusive_by_definition"
    assert equation.rejection_reason == "suppression_profile_excludes_expected_target"


def test_primary_result_honestly_records_negative_heart_reproduction(
    primary_config: AhmedConfig, primary_signal
):
    breath3, heart3 = run_profile(
        primary_signal, primary_config, 3, "figure_visible_unsuppressed"
    )
    breath5, heart5 = run_profile(
        primary_signal, primary_config, 5, "figure_visible_unsuppressed"
    )
    assert breath3.selected_bpm == pytest.approx(20.007, abs=0.01)
    assert breath5.selected_bpm == pytest.approx(20.007, abs=0.01)
    assert heart3.selected_bpm == pytest.approx(40.014, abs=0.02)
    assert heart5.selected_bpm == pytest.approx(20.007, abs=0.02)
    assert heart3.spectral_error_hz > primary_config.native_resolution_hz
    assert heart5.spectral_error_hz > primary_config.native_resolution_hz


def test_result_arrays_are_readonly(primary_config: AhmedConfig, primary_signal):
    breath, _ = run_profile(
        primary_signal, primary_config, 3, "figure_visible_unsuppressed"
    )
    for array in (
        breath.frequencies_hz,
        breath.candidate_bins,
        breath.harmonic_bins,
        breath.spectrum_magnitude,
        breath.scores,
        breath.eligible,
    ):
        assert not array.flags.writeable


def test_all_excluded_domain_is_invalid(primary_config: AhmedConfig, primary_signal):
    result = compute_ha_scores(
        primary_signal,
        primary_config,
        3,
        "eq26_multiples_suppressed",
        vital="heart",
        breath_bin=1,
    )
    assert not result.valid
    assert result.status == "invalid"
    assert result.rejection_reason == "no_finite_eligible_scores"
    assert result.selected_bin is None
    assert not np.any(result.eligible)


def test_adapter_record_round_trips_through_window_estimate(
    primary_config: AhmedConfig, primary_signal
):
    breath, heart = run_profile(
        primary_signal, primary_config, 3, "figure_visible_unsuppressed"
    )
    digest = config_hash(primary_config)
    native = build_adapter_result(breath, heart, {"config_hash": digest})
    assert type(native["hr_valid"]) is bool
    assert type(native["br_valid"]) is bool
    assert native["f_r_hz"] == pytest.approx(native["br_bpm"] / 60)
    normalized = as_window_estimate(
        native,
        estimator_id="ahmed_fig8_sim_v1",
        run_config_hash=digest,
    )
    assert normalized.estimator_id == "ahmed_fig8_sim_v1"
    assert normalized.run_config_hash == digest
    assert normalized.hr_bpm == pytest.approx(native["hr_raw"])
    assert normalized.br_bpm == pytest.approx(native["br_bpm"])
    assert normalized.f_r_hz == pytest.approx(native["f_r_hz"])
    assert normalized.raw is native
    assert not native["raw"]["heart_scores"].flags.writeable


def test_adapter_rejects_mismatched_results(primary_config: AhmedConfig, primary_signal):
    breath3, heart3 = run_profile(
        primary_signal, primary_config, 3, "figure_visible_unsuppressed"
    )
    _, heart5 = run_profile(
        primary_signal, primary_config, 5, "figure_visible_unsuppressed"
    )
    digest = config_hash(primary_config)
    with pytest.raises(ValueError, match="different harmonic counts"):
        build_adapter_result(breath3, heart5, {"config_hash": digest})
    with pytest.raises(ValueError, match="does not match"):
        build_adapter_result(breath3, heart3, {"config_hash": "wrong"})
    other_signal = simulate_eq14(primary_config, np.random.default_rng(99))
    other_breath, _ = run_profile(
        other_signal, primary_config, 3, "figure_visible_unsuppressed"
    )
    with pytest.raises(ValueError, match="different signal realizations"):
        build_adapter_result(other_breath, heart3, {"config_hash": digest})


def test_config_validation_rejects_bad_values_and_unknown_keys(
    primary_config: AhmedConfig, figure_script, tmp_path: Path
):
    with pytest.raises(ValueError, match="finite and positive"):
        replace(primary_config, fc_hz=0.0)
    with pytest.raises(ValueError, match="smaller than sample_count"):
        replace(primary_config, n_fft=128)
    document = figure_script.load_experiment_config(figure_script.DEFAULT_CONFIG)
    document["primary"]["unknown"] = 1
    path = tmp_path / "bad.yaml"
    path.write_text(
        figure_script.yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="unknown keys"):
        figure_script.load_experiment_config(path)
    document = figure_script.load_experiment_config(figure_script.DEFAULT_CONFIG)
    document["harmonics"] = [3]
    path.write_text(
        figure_script.yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="exactly \\[3, 5\\]"):
        figure_script.load_experiment_config(path)
    document = figure_script.load_experiment_config(figure_script.DEFAULT_CONFIG)
    document["audit"]["n_fft"] = [4096]
    path.write_text(
        figure_script.yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="audit.n_fft exactly"):
        figure_script.load_experiment_config(path)


def test_audit_variants_are_one_factor_conceptually(figure_script):
    document = figure_script.load_experiment_config(figure_script.DEFAULT_CONFIG)
    primary = AhmedConfig(**document["primary"])
    variants = figure_script._variant_configs(primary, document["audit"])
    assert len(variants) == 10
    assert len({variant_id for variant_id, _, _ in variants}) == len(variants)
    for variant_id, variant, field in variants:
        differences = {
            key
            for key, value in primary.to_dict().items()
            if variant.to_dict()[key] != value
        }
        if field == "amplitude_ratio_h_to_b":
            assert differences == {"heart_amplitude", "breath_amplitude"}
        else:
            assert differences == {field}
        assert variant_id


def test_canonical_contract_requires_exact_default_document(figure_script, tmp_path: Path):
    document = figure_script.load_experiment_config(figure_script.DEFAULT_CONFIG)
    assert figure_script._canonical_contract_matches(
        figure_script.DEFAULT_CONFIG, document
    ) == (True, [])
    external = tmp_path / "external.yaml"
    external.write_text(
        figure_script.DEFAULT_CONFIG.read_text(encoding="utf-8"), encoding="utf-8"
    )
    matches, reasons = figure_script._canonical_contract_matches(external, document)
    assert not matches
    assert reasons == ["config_path_is_not_default"]
    changed = json.loads(json.dumps(document))
    changed["primary"]["theta0_rad"] = math.pi / 4
    matches, reasons = figure_script._canonical_contract_matches(
        figure_script.DEFAULT_CONFIG, changed
    )
    assert not matches
    assert "primary_config_differs_from_approved_contract" in reasons


def test_derived_variant_seed_is_stable_and_separated():
    assert derive_variant_seed(42, "a") == derive_variant_seed(42, "a")
    assert derive_variant_seed(42, "a") != derive_variant_seed(42, "b")
    assert derive_variant_seed(42, "a") != derive_variant_seed(43, "a")


def _required_relpaths(figure_script, config_path: Path) -> list[str]:
    """Repo-relative posix paths of the files `_provenance` requires to be tracked."""
    root = figure_script.REPO_ROOT
    candidates = [
        figure_script.PLAN_PATH,
        figure_script.IMPLEMENTATION_PATH,
        Path(figure_script.__file__).resolve(),
        figure_script.TEST_PATH,
        config_path,
    ]
    relative = []
    for path in candidates:
        try:
            relative.append(path.resolve().relative_to(root).as_posix())
        except ValueError:
            continue  # outside the repo; intentionally untrackable
    return relative


def _pin_git_provenance(monkeypatch, figure_script, *, tracked, porcelain: str):
    """Replace `_git_text` so promotion depends on injected state, not the real worktree.

    The promotion decision is a function of `git ls-files` and `git status --porcelain`.
    Reading those from the ambient worktree made canonical-promotion assertions flip with
    whatever happened to be uncommitted, so every provenance test pins them explicitly.
    """

    def fake_git_text(*args: str) -> str:
        if args == ("ls-files",):
            return "\n".join(tracked)
        if args == ("status", "--porcelain"):
            return porcelain
        if args == ("rev-parse", "HEAD"):
            return "0" * 40
        if args == ("branch", "--show-current"):
            return "pinned-test-branch"
        raise AssertionError(f"unexpected git invocation: {args!r}")

    monkeypatch.setattr(figure_script, "_git_text", fake_git_text)


def test_cli_execute_writes_strict_complete_artifacts(
    figure_script, tmp_path: Path, monkeypatch
):
    output = tmp_path / "results"
    canonical = tmp_path / "canonical"
    # Pin an ineligible tree so the promotion assertions below are deterministic.
    _pin_git_provenance(
        monkeypatch,
        figure_script,
        tracked=_required_relpaths(figure_script, figure_script.DEFAULT_CONFIG),
        porcelain=" M notes/scratch.md",
    )
    run_dir = figure_script.execute(
        figure_script.DEFAULT_CONFIG, output, canonical
    )
    required = {
        "resolved_config.yaml",
        "metrics.json",
        "provenance.json",
        "run_status.json",
        "ahmed_fig8cd_behavioral.png",
        "ahmed_fig8cd_behavioral.pdf",
    }
    assert required <= {path.name for path in run_dir.iterdir()}
    status = json.loads((run_dir / "run_status.json").read_text(encoding="utf-8"))
    metrics = json.loads(
        (run_dir / "metrics.json").read_text(encoding="utf-8"),
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"nonfinite {value}")
        ),
    )
    assert status["status"] == "complete"
    assert status["canonical_promoted"] is False
    assert status["canonical_bundle"] is None
    assert status["acceptance_status"] == "not_reproduced_under_declared_assumptions"
    assert metrics["acceptance"]["passed"] is False
    assert metrics["acceptance"]["status"] == status["acceptance_status"]
    npz_paths = sorted(run_dir.glob("*.npz"))
    assert len(npz_paths) == 13
    for path in npz_paths:
        with np.load(path, allow_pickle=False) as evidence:
            assert evidence.files
            assert all(evidence[key].dtype != object for key in evidence.files)
    provenance = json.loads(
        (run_dir / "provenance.json").read_text(encoding="utf-8")
    )
    hashes = provenance["hashes"]
    fixed_hash_paths = {
        "source_config_sha256": figure_script.DEFAULT_CONFIG,
        "resolved_config_sha256": run_dir / "resolved_config.yaml",
        "plan_sha256": figure_script.PLAN_PATH,
        "implementation_module_sha256": figure_script.IMPLEMENTATION_PATH,
        "runner_script_sha256": Path(figure_script.__file__),
        "test_file_sha256": figure_script.TEST_PATH,
        "paper_pdf_sha256": figure_script.PAPER_PATH,
        "metrics_sha256": run_dir / "metrics.json",
        "figure_png_sha256": run_dir / "ahmed_fig8cd_behavioral.png",
        "figure_pdf_sha256": run_dir / "ahmed_fig8cd_behavioral.pdf",
    }
    for key, path in fixed_hash_paths.items():
        assert hashes[key] == figure_script._sha256(path)
    declared_evidence_hashes = sorted(
        value
        for key, value in hashes.items()
        if key.startswith("evidence_")
    )
    actual_evidence_hashes = sorted(
        figure_script._sha256(path) for path in npz_paths
    )
    assert declared_evidence_hashes == actual_evidence_hashes
    assert not canonical.exists()


def _run_with_provenance(
    figure_script, tmp_path: Path, monkeypatch, *, tracked, porcelain, config_path=None
):
    config_path = config_path or figure_script.DEFAULT_CONFIG
    _pin_git_provenance(
        monkeypatch, figure_script, tracked=tracked, porcelain=porcelain
    )
    canonical = tmp_path / "canonical"
    run_dir = figure_script.execute(config_path, tmp_path / "results", canonical)
    provenance = json.loads((run_dir / "provenance.json").read_text(encoding="utf-8"))
    status = json.loads((run_dir / "run_status.json").read_text(encoding="utf-8"))
    return run_dir, provenance, status, canonical


def test_provenance_clean_tracked_tree_promotes_canonical(
    figure_script, tmp_path: Path, monkeypatch
):
    _, provenance, status, canonical = _run_with_provenance(
        figure_script,
        tmp_path,
        monkeypatch,
        tracked=_required_relpaths(figure_script, figure_script.DEFAULT_CONFIG),
        porcelain="",
    )
    assert provenance["git"]["clean_and_required_tracked"] is True
    assert all(provenance["git"]["required_files_tracked"].values())
    promotion = provenance["canonical_promotion"]
    assert promotion["eligible"] is True
    assert promotion["contract_matches"] is True
    assert promotion["ineligibility_reasons"] == []
    assert status["canonical_promoted"] is True
    assert status["canonical_bundle"] is not None
    assert canonical.exists()


def test_provenance_dirty_tree_blocks_promotion(
    figure_script, tmp_path: Path, monkeypatch
):
    _, provenance, status, canonical = _run_with_provenance(
        figure_script,
        tmp_path,
        monkeypatch,
        tracked=_required_relpaths(figure_script, figure_script.DEFAULT_CONFIG),
        porcelain=" M src/m8/ahmed_fig8.py",
    )
    assert provenance["git"]["clean_and_required_tracked"] is False
    promotion = provenance["canonical_promotion"]
    assert promotion["eligible"] is False
    assert "git_tree_or_required_tracking_not_clean" in promotion["ineligibility_reasons"]
    assert promotion["destination"] is None
    assert status["canonical_promoted"] is False
    assert status["canonical_bundle"] is None
    assert not canonical.exists()


def test_provenance_untracked_required_file_blocks_promotion(
    figure_script, tmp_path: Path, monkeypatch
):
    # Clean tree, but the implementation module itself is not tracked.
    tracked = [
        path
        for path in _required_relpaths(figure_script, figure_script.DEFAULT_CONFIG)
        if not path.endswith("src/m8/ahmed_fig8.py")
    ]
    _, provenance, status, canonical = _run_with_provenance(
        figure_script, tmp_path, monkeypatch, tracked=tracked, porcelain=""
    )
    tracking = provenance["git"]["required_files_tracked"]
    assert tracking["src/m8/ahmed_fig8.py"] is False
    assert provenance["git"]["status_porcelain"] == ""
    assert provenance["git"]["clean_and_required_tracked"] is False
    promotion = provenance["canonical_promotion"]
    assert promotion["eligible"] is False
    assert "git_tree_or_required_tracking_not_clean" in promotion["ineligibility_reasons"]
    assert status["canonical_promoted"] is False
    assert not canonical.exists()


def test_provenance_external_config_blocks_promotion_on_otherwise_clean_tree(
    figure_script, tmp_path: Path, monkeypatch
):
    """Complements `test_external_config_path_runs_and_is_reported_untracked`.

    That test runs against the real worktree, so on a dirty tree it cannot tell whether
    promotion was blocked by the external config or merely by ambient dirtiness. Pinning
    a clean tree here isolates the external config as the sole cause.
    """
    external = tmp_path / "external_config.yaml"
    external.write_bytes(figure_script.DEFAULT_CONFIG.read_bytes())
    _, provenance, status, canonical = _run_with_provenance(
        figure_script,
        tmp_path,
        monkeypatch,
        # Git tracks the repo's real files; it never tracks the temporary config.
        tracked=_required_relpaths(figure_script, figure_script.DEFAULT_CONFIG),
        porcelain="",
        config_path=external,
    )
    tracking = provenance["git"]["required_files_tracked"]
    # `_provenance` keys a required file by its repo-relative posix path when it lies
    # inside the repo and by its absolute path otherwise. Workspace-local `--basetemp`
    # can put tmp_path inside the repo, so derive the key rather than assuming either.
    resolved = external.resolve()
    try:
        key = resolved.relative_to(figure_script.REPO_ROOT).as_posix()
    except ValueError:
        key = str(resolved)
    assert tracking[key] is False
    assert provenance["git"]["clean_and_required_tracked"] is False
    promotion = provenance["canonical_promotion"]
    assert promotion["eligible"] is False
    assert promotion["contract_matches"] is False
    assert "config_path_is_not_default" in promotion["ineligibility_reasons"]
    assert "git_tree_or_required_tracking_not_clean" in promotion["ineligibility_reasons"]
    assert status["canonical_promoted"] is False
    assert not canonical.exists()


def test_scientific_outputs_identical_across_provenance_states(
    figure_script, tmp_path: Path, monkeypatch
):
    """Provenance state may gate promotion, but must never change the science."""
    tracked = _required_relpaths(figure_script, figure_script.DEFAULT_CONFIG)

    def science(run_dir: Path, provenance: dict) -> tuple:
        metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
        # Everything in metrics.json except the per-run identifier is science.
        stable = {key: value for key, value in metrics.items() if key != "run_id"}
        evidence = {
            key: value
            for key, value in provenance["hashes"].items()
            if key.startswith("evidence_")
        }
        return stable, evidence

    clean_dir, clean_prov, clean_status, _ = _run_with_provenance(
        figure_script, tmp_path / "clean", monkeypatch, tracked=tracked, porcelain=""
    )
    dirty_dir, dirty_prov, dirty_status, _ = _run_with_provenance(
        figure_script,
        tmp_path / "dirty",
        monkeypatch,
        tracked=tracked,
        porcelain=" M README.md",
    )

    assert science(clean_dir, clean_prov) == science(dirty_dir, dirty_prov)
    # ...while promotion eligibility differs, proving the states really were distinct.
    assert clean_status["canonical_promoted"] is True
    assert dirty_status["canonical_promoted"] is False
    assert clean_status["acceptance_status"] == dirty_status["acceptance_status"]


def test_cli_runs_never_overwrite(figure_script, tmp_path: Path):
    first = figure_script.execute(
        figure_script.DEFAULT_CONFIG, tmp_path / "out", tmp_path / "canonical"
    )
    second = figure_script.execute(
        figure_script.DEFAULT_CONFIG, tmp_path / "out", tmp_path / "canonical"
    )
    assert first != second
    assert first.exists() and second.exists()
    first_metrics = json.loads((first / "metrics.json").read_text(encoding="utf-8"))
    second_metrics = json.loads((second / "metrics.json").read_text(encoding="utf-8"))
    first_metrics.pop("run_id")
    second_metrics.pop("run_id")
    assert first_metrics == second_metrics
    first_npz = first / "primary__figure_visible_unsuppressed.npz"
    second_npz = second / "primary__figure_visible_unsuppressed.npz"
    with np.load(first_npz, allow_pickle=False) as a, np.load(
        second_npz, allow_pickle=False
    ) as b:
        assert a.files == b.files
        for key in a.files:
            if np.issubdtype(a[key].dtype, np.inexact):
                assert np.array_equal(a[key], b[key], equal_nan=True)
            else:
                assert np.array_equal(a[key], b[key])


def test_external_config_path_runs_and_is_reported_untracked(
    figure_script, tmp_path: Path
):
    external = tmp_path / "external.yaml"
    external.write_text(
        figure_script.DEFAULT_CONFIG.read_text(encoding="utf-8"), encoding="utf-8"
    )
    run = figure_script.execute(
        external, tmp_path / "out", tmp_path / "canonical"
    )
    provenance = json.loads((run / "provenance.json").read_text(encoding="utf-8"))
    try:
        external_key = external.resolve().relative_to(figure_script.REPO_ROOT).as_posix()
    except ValueError:
        external_key = str(external.resolve())
    assert provenance["git"]["required_files_tracked"][external_key] is False
    assert provenance["canonical_promotion"]["eligible"] is False
    assert (
        "config_path_is_not_default"
        in provenance["canonical_promotion"]["ineligibility_reasons"]
    )


def test_canonical_promotion_is_versioned_and_complete(
    figure_script, tmp_path: Path
):
    sources = {}
    for name in ("metrics.json", "provenance.json", "figure.png"):
        source = tmp_path / f"source-{name}"
        source.write_text(name, encoding="utf-8")
        sources[source] = name
    canonical = tmp_path / "canonical"
    destination = figure_script._promote_canonical(
        canonical, "run-123", sources, {"example_sha256": "abc"}
    )
    assert destination == canonical / "run-123"
    assert {path.name for path in destination.iterdir()} == {
        "metrics.json",
        "provenance.json",
        "figure.png",
        "bundle.json",
    }
    latest = json.loads((canonical / "LATEST.json").read_text(encoding="utf-8"))
    assert latest["run_id"] == "run-123"
    assert latest["bundle"] == "run-123"
    with pytest.raises(FileExistsError):
        figure_script._promote_canonical(
            canonical, "run-123", sources, {"example_sha256": "abc"}
        )


def test_cli_failure_leaves_diagnostic_manifest(
    figure_script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def fail_render(*_args, **_kwargs):
        raise RuntimeError("forced render failure")

    monkeypatch.setattr(figure_script, "_render_figure", fail_render)
    output = tmp_path / "failed"
    with pytest.raises(RuntimeError, match="forced render failure"):
        figure_script.execute(
            figure_script.DEFAULT_CONFIG, output, tmp_path / "canonical"
        )
    runs = list(output.iterdir())
    assert len(runs) == 1
    status = json.loads((runs[0] / "run_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["stage"] == "rendering"
    assert status["exception_type"] == "RuntimeError"
    assert status["message"] == "forced render failure"


def test_isolated_module_does_not_import_production_respiration():
    source = (ROOT / "src" / "m8" / "ahmed_fig8.py").read_text(encoding="utf-8")
    assert "src.respiration" not in source
    assert "run_window_dsp" not in source
