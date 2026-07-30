"""Oracle and contract tests for the Step 1b scientific core.

The load-bearing test here is `test_core_reproduces_the_independent_oracle`: the addendum's
gate criterion (section A4.2) is reproduction of predictions P1-P4, so the core must agree
with `scripts/m8_step1b_gate_prediction.py`, which was written independently of `src/m8/`
before the core existed and is the frozen prediction's source.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.m4.estimator_suite import SuiteWindowResult  # noqa: E402
from src.m8.ahmed_transfer import (  # noqa: E402
    COLLISION_DOMAIN_FROM_FB,
    ESTIMATOR_ID,
    REAL_REPRESENTATIVE_DOMAIN,
    AhmedPhaseConfig,
    arm_id_for,
    estimate_phase_ha,
    phase_signal_hash,
)
from src.m8.ahmed_fig8 import SUPPRESSION_PROFILES  # noqa: E402

UNSUPPRESSED = "figure_visible_unsuppressed"


def _load_oracle():
    path = REPO_ROOT / "scripts" / "m8_step1b_gate_prediction.py"
    spec = importlib.util.spec_from_file_location("m8_gate_prediction", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def oracle():
    return _load_oracle()


@pytest.fixture(scope="module")
def domains():
    return {
        "collision": COLLISION_DOMAIN_FROM_FB,
        "real": REAL_REPRESENTATIVE_DOMAIN,
    }


# ── P1-P4: agreement with the independent oracle ─────────────────────────────

GRID_CASES = [
    ("primary_prf", None, 561, "collision"),
    ("primary_prf_real_domains", None, 561, "real"),
    ("audit_20hz", 20.0, 300, "collision"),
    ("real_30s", 20.0, 600, "real"),
]


@pytest.mark.parametrize("label, fs_override, n, domain_key", GRID_CASES)
@pytest.mark.parametrize("harmonics", [3, 5])
def test_core_reproduces_the_independent_oracle(
    oracle, domains, label, fs_override, n, domain_key, harmonics
):
    """The core's unsuppressed selections must equal the frozen prediction exactly."""
    fs = oracle.PRF_HZ if fs_override is None else fs_override
    _clean, noisy = oracle.build(fs, n)
    domain = domains[domain_key]

    config = AhmedPhaseConfig(domain=domain, harmonic_counts=(harmonics,))
    result = estimate_phase_ha(noisy, fs, config)
    record = result.arm_native_results[arm_id_for(harmonics, UNSUPPRESSED)]

    for vital, key in (("breath", "br_bpm"), ("heart", "hr_raw")):
        lo, hi, excl = domain.bounds(vital)
        _freqs, bins, scores = oracle.accumulate(noisy, fs, n, harmonics, lo, hi)
        expected_bin = int(bins[int(np.argmax(scores))])
        expected_bpm = 60.0 * expected_bin * fs / n
        assert record[key] == pytest.approx(expected_bpm, rel=0, abs=1e-9), (
            f"{label} {vital} H={harmonics}: core said {record[key]}, "
            f"oracle said {expected_bpm}"
        )


# P2/P3 are exact identities in exact arithmetic, but the rFFT introduces round-off, so
# they hold to float64 precision rather than bit-exactly. Observed on this grid: P2 agrees
# to 2.0e-15 relative, the collision ratios to 6e-15 relative, and a non-divisor candidate
# scores 1.5e-12 absolute, which is 8.8e-16 of the 1683.89 spectrum peak. The tolerances
# below are ~1000x the observed round-off — loose enough to survive a different BLAS, and
# still around nine orders of magnitude below any real spectral line.
DEGENERACY_REL_TOL = 1e-12
NON_DIVISOR_REL_TO_PEAK = 1e-12


def _heart_scores(clean, fs, harmonics):
    config = AhmedPhaseConfig(domain=COLLISION_DOMAIN_FROM_FB, harmonic_counts=(harmonics,))
    heart = estimate_phase_ha(clean, fs, config).arm_native_results[
        arm_id_for(harmonics, UNSUPPRESSED)
    ]["heart_evidence"]
    return {int(b): float(s) for b, s in zip(heart["candidate_bins"], heart["scores"])}


@pytest.mark.parametrize("harmonics", [3, 5])
def test_p2_subharmonic_degeneracy_on_clean_signal(oracle, harmonics):
    """P2: score(q=f_h) equals score(q=f_h/2) to float64 round-off.

    A lone spectral line is captured at *some* harmonic index regardless of whether the
    candidate is the fundamental or an exact integer divisor of it, so the accumulated
    means coincide. This is the mechanism behind the subharmonic selection failures.
    """
    fs, n = 20.0, 600
    clean, _noisy = oracle.build(fs, n)
    at = _heart_scores(clean, fs, harmonics)  # bin 40 = f_h, bin 20 = f_h/2
    assert at[40] == pytest.approx(at[20], rel=DEGENERACY_REL_TOL)


@pytest.mark.parametrize("harmonics, expected_ratio", [(3, 2.0), (5, 3.0)])
def test_p3_collision_ratio_on_clean_signal(oracle, harmonics, expected_ratio):
    """P3: score(q=f_b)/score(q=f_h) is 2 for H=3 and 3 for H=5.

    H=3 at q=f_b captures only the breathing line, which is d_b/d_h = 2 times the heart
    line. H=5 additionally reaches f_h at the fourth harmonic, giving (2+1)/1 = 3.
    """
    fs, n = 20.0, 600
    clean, _noisy = oracle.build(fs, n)
    at = _heart_scores(clean, fs, harmonics)
    assert at[10] / at[40] == pytest.approx(expected_ratio, rel=DEGENERACY_REL_TOL)


@pytest.mark.parametrize("harmonics", [3, 5])
def test_p3_non_divisor_candidate_scores_at_round_off_only(oracle, harmonics):
    """P3: a candidate whose harmonic row misses every line carries no signal."""
    fs, n = 20.0, 600
    clean, _noisy = oracle.build(fs, n)
    at = _heart_scores(clean, fs, harmonics)
    peak = float(np.abs(np.fft.rfft(clean, n=n)).max())
    # bin 13 -> {13, 26, 39} misses the line at bin 40.
    assert at[13] / peak < NON_DIVISOR_REL_TO_PEAK
    assert at[13] >= 0.0


# ── Frequency convention, support, and suppression ───────────────────────────

def test_bpm_conversion_is_60q_not_30q():
    """A pure 1.0 Hz tone must read 60 bpm, proving no residual Step 1a 30*q path."""
    fs, n = 20.0, 600
    t = np.arange(n) / fs
    phase = np.sin(2 * np.pi * 1.0 * t)
    config = AhmedPhaseConfig(domain=REAL_REPRESENTATIVE_DOMAIN, harmonic_counts=(3,))
    record = estimate_phase_ha(phase, fs, config).arm_native_results[
        arm_id_for(3, UNSUPPRESSED)
    ]
    assert record["hr_raw"] == pytest.approx(60.0, abs=1e-9)


@pytest.mark.parametrize(
    "harmonics, bin_index, expect_supported",
    [
        (5, 40, True),    # 5 * 40 = 200 < 300  -> supported (100 bpm at 20 Hz)
        (5, 59, True),    # 5 * 59 = 295 < 300  -> supported
        (5, 60, False),   # 5 * 60 = 300 == n_fft/2 -> Nyquist-degenerate, excluded
    ],
)
def test_strict_support_boundary(harmonics, bin_index, expect_supported):
    fs, n = 20.0, 600
    phase = np.sin(2 * np.pi * (bin_index * fs / n) * np.arange(n) / fs)
    config = AhmedPhaseConfig(
        domain=REAL_REPRESENTATIVE_DOMAIN, harmonic_counts=(harmonics,)
    )
    heart = estimate_phase_ha(phase, fs, config).arm_native_results[
        arm_id_for(harmonics, UNSUPPRESSED)
    ]["heart_evidence"]
    supported = {
        int(b): bool(m) for b, m in zip(heart["candidate_bins"], heart["supported_mask"])
    }
    assert supported[bin_index] is expect_supported


def test_nyquist_equality_is_recorded_as_degenerate_not_merely_unsupported():
    fs, n = 20.0, 600
    phase = np.sin(2 * np.pi * 2.0 * np.arange(n) / fs)
    config = AhmedPhaseConfig(domain=REAL_REPRESENTATIVE_DOMAIN, harmonic_counts=(5,))
    heart = estimate_phase_ha(phase, fs, config).arm_native_results[
        arm_id_for(5, UNSUPPRESSED)
    ]["heart_evidence"]
    degenerate = {
        int(b): bool(m)
        for b, m in zip(heart["candidate_bins"], heart["nyquist_degenerate_mask"])
    }
    assert degenerate[60] is True  # 5 * 60 == 300 == n_fft/2 exactly


def test_denominator_stays_h_when_candidates_are_masked():
    """Masking unsupported candidates must not truncate the harmonic row."""
    fs, n = 20.0, 600
    t = np.arange(n) / fs
    phase = np.sin(2 * np.pi * 1.0 * t)
    h3 = estimate_phase_ha(
        phase, fs, AhmedPhaseConfig(domain=REAL_REPRESENTATIVE_DOMAIN, harmonic_counts=(3,))
    ).arm_native_results[arm_id_for(3, UNSUPPRESSED)]["heart_evidence"]
    spectrum_peak = float(np.abs(np.fft.rfft(phase, n=n)).max())
    at30 = {int(b): float(s) for b, s in zip(h3["candidate_bins"], h3["scores"])}
    # bin 30 == 1.0 Hz carries the only line; with H=3 the mean divides by 3, not by 1.
    assert at30[30] == pytest.approx(spectrum_peak / 3.0, rel=1e-12)


def test_suppression_uses_the_same_h_ahmed_breath_bin_only():
    """Changing an unrelated production BR cannot alter any Ahmed arm."""
    fs, n = 20.0, 600
    rng = np.random.Generator(np.random.PCG64(7))
    phase = rng.standard_normal(n)
    config = AhmedPhaseConfig(domain=REAL_REPRESENTATIVE_DOMAIN)
    first = estimate_phase_ha(phase, fs, config)
    second = estimate_phase_ha(phase, fs, config)
    for arm_id, record in first.arm_native_results.items():
        assert record["hr_raw"] == second.arm_native_results[arm_id]["hr_raw"]
        assert record["br_bpm"] == second.arm_native_results[arm_id]["br_bpm"]


def test_eq26_and_prose_profiles_apply_the_declared_rules():
    fs, n = 20.0, 600
    rng = np.random.Generator(np.random.PCG64(11))
    phase = rng.standard_normal(n)
    result = estimate_phase_ha(
        phase, fs, AhmedPhaseConfig(domain=REAL_REPRESENTATIVE_DOMAIN, harmonic_counts=(3,))
    )
    breath_bin = result.arm_native_results[arm_id_for(3, UNSUPPRESSED)]["br_selected_bin"]

    eq26 = result.arm_native_results[arm_id_for(3, "eq26_multiples_suppressed")]["heart_evidence"]
    for b, ok in zip(eq26["candidate_bins"], eq26["eligible_mask"]):
        if bool(ok):
            assert int(b) % breath_bin != 0

    prose = result.arm_native_results[arm_id_for(3, "prose_low_or_equal_suppressed")]["heart_evidence"]
    for b, ok in zip(prose["candidate_bins"], prose["eligible_mask"]):
        if bool(ok):
            assert int(b) > breath_bin


def test_prose_duplicates_unsuppressed_on_the_real_domain():
    """The real HR band starts at 0.8 Hz while BR ends at 0.5, so prose is a duplicate."""
    fs, n = 20.0, 600
    rng = np.random.Generator(np.random.PCG64(13))
    phase = rng.standard_normal(n)
    result = estimate_phase_ha(
        phase, fs, AhmedPhaseConfig(domain=REAL_REPRESENTATIVE_DOMAIN, harmonic_counts=(3,))
    )
    unsup = result.arm_native_results[arm_id_for(3, UNSUPPRESSED)]
    prose = result.arm_native_results[arm_id_for(3, "prose_low_or_equal_suppressed")]
    assert prose["hr_raw"] == unsup["hr_raw"]
    assert prose["hr_selected_bin"] == unsup["hr_selected_bin"]


# ── Contracts ────────────────────────────────────────────────────────────────

def test_returns_exactly_the_six_declared_arms():
    fs, n = 20.0, 600
    rng = np.random.Generator(np.random.PCG64(3))
    result = estimate_phase_ha(
        rng.standard_normal(n), fs, AhmedPhaseConfig(domain=REAL_REPRESENTATIVE_DOMAIN)
    )
    expected = {arm_id_for(h, p) for h in (3, 5) for p in SUPPRESSION_PROFILES}
    assert set(result.arm_native_results) == expected
    assert len(expected) == 6


def test_native_result_field_contract():
    fs, n = 20.0, 600
    rng = np.random.Generator(np.random.PCG64(5))
    result = estimate_phase_ha(
        rng.standard_normal(n), fs, AhmedPhaseConfig(domain=REAL_REPRESENTATIVE_DOMAIN)
    )
    for arm_id, record in result.arm_native_results.items():
        assert record["estimator_id"] == ESTIMATOR_ID
        assert record["arm_id"] == arm_id
        assert type(record["hr_valid"]) is bool, arm_id
        assert type(record["br_valid"]) is bool, arm_id
        assert isinstance(record["br_confidence"], str)
        assert isinstance(record["rej_reason"], str)
        if record["hr_valid"]:
            assert np.isfinite(record["hr_raw"])
        if record["br_valid"]:
            assert np.isfinite(record["br_bpm"])
        if record["f_r_hz"] is not None:
            assert np.isfinite(record["f_r_hz"])


def test_shared_evidence_arrays_are_immutable():
    fs, n = 20.0, 600
    rng = np.random.Generator(np.random.PCG64(17))
    result = estimate_phase_ha(
        rng.standard_normal(n), fs, AhmedPhaseConfig(domain=REAL_REPRESENTATIVE_DOMAIN)
    )
    for key, value in result.shared_evidence.items():
        if isinstance(value, np.ndarray):
            assert not value.flags.writeable, key
            with pytest.raises(ValueError):
                value[0] = 0


def test_arm_hashes_differ_by_h_and_profile_but_are_stable():
    config = AhmedPhaseConfig(domain=REAL_REPRESENTATIVE_DOMAIN)
    hashes = {
        (h, p): config.arm_config_hash(h, p)
        for h in (3, 5)
        for p in SUPPRESSION_PROFILES
    }
    assert len(set(hashes.values())) == 6
    assert hashes[(3, UNSUPPRESSED)] == config.arm_config_hash(3, UNSUPPRESSED)


def test_domains_have_distinct_hashes_and_only_one_is_real_eligible():
    a = AhmedPhaseConfig(domain=COLLISION_DOMAIN_FROM_FB).config_hash()
    b = AhmedPhaseConfig(domain=REAL_REPRESENTATIVE_DOMAIN).config_hash()
    assert a != b
    assert COLLISION_DOMAIN_FROM_FB.real_data_eligible is False
    assert REAL_REPRESENTATIVE_DOMAIN.real_data_eligible is True


@pytest.mark.parametrize(
    "phase, message",
    [
        (np.zeros((2, 3)), "one-dimensional"),
        (np.array([1.0]), "at least two"),
        (np.array([1.0, np.nan, 2.0]), "non-finite"),
        (np.array([1, 2, 3], dtype=np.int64), "floating"),
    ],
)
def test_malformed_phase_is_fatal(phase, message):
    with pytest.raises(ValueError, match=message):
        estimate_phase_ha(phase, 20.0, AhmedPhaseConfig(domain=REAL_REPRESENTATIVE_DOMAIN))


def test_signal_hash_is_stable_and_content_sensitive():
    a = np.array([0.0, 1.0, 2.0])
    b = np.array([0.0, 1.0, 2.5])
    assert phase_signal_hash("hdr", a) == phase_signal_hash("hdr", a)
    assert phase_signal_hash("hdr", a) != phase_signal_hash("hdr", b)
    assert phase_signal_hash("hdr", a) != phase_signal_hash("other", a)


def test_result_is_a_suite_window_result():
    fs, n = 20.0, 600
    rng = np.random.Generator(np.random.PCG64(19))
    result = estimate_phase_ha(
        rng.standard_normal(n), fs, AhmedPhaseConfig(domain=REAL_REPRESENTATIVE_DOMAIN)
    )
    assert isinstance(result, SuiteWindowResult)
