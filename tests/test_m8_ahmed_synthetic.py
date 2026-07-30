"""Correctness oracles for the Step 1b synthetic generator (base plan section 2.2).

Implementation correctness and scientific transfer are separate questions; only the first
is tested here. Nothing in this file asserts that the method recovers the right rate.

The declared primary realization's constants are pinned because base plan section 2.2
quotes them and the gate binds them: any drift in the generator, the RNG, or the
extraction path must fail loudly rather than silently produce a different experiment.
"""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.m8.ahmed_synthetic import (  # noqa: E402
    MODEL_ID,
    SyntheticConfig,
    build_phasor,
    embed_production_geometry_cube,
    embed_single_channel_cube,
    extract_phase,
    generate,
)

PRIMARY = SyntheticConfig()
AUDIT_20HZ = SyntheticConfig(prf_hz=20.0)


# ── Declared primary realization, pinned to base plan section 2.2 ────────────

def test_sample_count_rule_round():
    assert PRIMARY.sample_count == 561      # 15 s * 37.41985916275843, rounded
    assert AUDIT_20HZ.sample_count == 300   # 15 s * 20 Hz


def test_declared_noise_power_and_realized_snr():
    _clean, _cp, _np_, diag = build_phasor(PRIMARY)
    assert diag["noise_power"] == pytest.approx(0.0959419233, abs=5e-11)
    assert diag["realized_snr_db"] == pytest.approx(10.1799158, abs=5e-8)


def test_measured_noise_power_matches_the_declared_equation_exactly():
    """Correctness is algebraic agreement, not closeness of the draw to 10 dB."""
    _clean, _cp, _np_, diag = build_phasor(PRIMARY)
    assert diag["noise_power"] == pytest.approx(
        diag["expected_noise_power"], rel=1e-15, abs=0
    )


def test_declared_branch_margin():
    _clean, _cp, _np_, diag = build_phasor(PRIMARY)
    assert diag["max_clean_increment_rad"] == pytest.approx(0.94111946, abs=5e-9)
    assert diag["min_branch_margin_rad"] == pytest.approx(2.20047319, abs=5e-9)


def test_beta_ratio_is_exactly_the_displacement_ratio():
    assert PRIMARY.beta_breath / PRIMARY.beta_heart == pytest.approx(2.0, rel=1e-15)


def test_seed_determinism():
    a = build_phasor(PRIMARY)[3]
    b = build_phasor(PRIMARY)[3]
    assert a["draw_hash"] == b["draw_hash"]
    assert build_phasor(SyntheticConfig(seed=43))[3]["draw_hash"] != a["draw_hash"]


def test_real_and_imaginary_draws_are_independent():
    n = PRIMARY.sample_count
    rng = np.random.Generator(np.random.PCG64(PRIMARY.seed))
    u, v = rng.standard_normal(n), rng.standard_normal(n)
    assert not np.allclose(u, v)
    assert abs(float(np.corrcoef(u, v)[0, 1])) < 0.15


# ── Extraction-path oracles ──────────────────────────────────────────────────

def test_clean_direct_phase_identity_when_no_branch_is_crossed():
    """With every clean increment inside (-pi, pi), extraction equals the unwrapped angle."""
    clean_phase, clean_phasor, _noisy, diag = build_phasor(PRIMARY)
    assert diag["max_clean_increment_rad"] < np.pi
    extracted = extract_phase(embed_single_channel_cube(clean_phasor))
    expected = np.unwrap(np.angle(clean_phasor))
    expected = expected - expected[0]
    assert np.allclose(extracted, expected, atol=1e-4)
    assert np.allclose(extracted, clean_phase - clean_phase[0], atol=1e-4)


def test_production_geometry_matches_the_single_channel_extraction():
    """32 chirps x 4 RX with per-channel gains and static offsets must agree.

    The conjugate product cancels each static offset and the mean of positive squared
    gains is a positive real scale, so the phase is unchanged.
    """
    _clean, _cp, noisy, _diag = build_phasor(PRIMARY)
    single = extract_phase(embed_single_channel_cube(noisy))
    multi = extract_phase(embed_production_geometry_cube(noisy))
    assert multi.shape == single.shape
    assert np.allclose(multi, single, atol=1e-3)


def test_cube_is_complex64_like_a_decoded_production_cube():
    _clean, _cp, noisy, _diag = build_phasor(PRIMARY)
    assert embed_single_channel_cube(noisy).dtype == np.complex64
    assert embed_production_geometry_cube(noisy).dtype == np.complex64
    assert embed_production_geometry_cube(noisy).shape == (noisy.size, 32, 4, 64)


def test_model_relative_oracle_holds_and_records_no_slip_for_the_primary():
    result = generate(PRIMARY)
    assert result.max_oracle_residual_rad < 1e-3
    assert result.max_reference_increment_rad < np.pi
    assert result.n_cycle_slips == 0
    assert np.all(result.cycle_offsets == 0)


def test_primary_reference_increment_is_the_declared_value():
    result = generate(PRIMARY)
    assert result.max_reference_increment_rad == pytest.approx(1.64805, abs=5e-5)


def test_twenty_hz_audit_is_a_separate_realization_not_the_primary():
    primary, audit = generate(PRIMARY), generate(AUDIT_20HZ)
    assert audit.config_hash != primary.config_hash
    assert audit.extracted_phase.size == 300
    assert primary.extracted_phase.size == 561


def test_evidence_arrays_are_immutable():
    result = generate(PRIMARY)
    for name in ("clean_phase", "extracted_phase", "reference_phase", "noisy_phasor"):
        assert not getattr(result, name).flags.writeable, name


def test_hashes_are_stable_and_content_sensitive():
    a, b = generate(PRIMARY), generate(PRIMARY)
    assert (a.draw_hash, a.clean_phasor_hash, a.cube_hash) == (
        b.draw_hash, b.clean_phasor_hash, b.cube_hash
    )
    other = generate(SyntheticConfig(seed=7))
    assert other.draw_hash != a.draw_hash
    assert other.cube_hash != a.cube_hash


def test_config_identity_names_the_conservative_model():
    assert MODEL_ID == "phase_fundamentals_only_transfer_v1"
    assert PRIMARY.to_dict()["model_id"] == MODEL_ID
    assert PRIMARY.to_dict()["noise_model"] == "circular_complex_pre_extraction"


def test_config_hash_changes_with_every_declared_field():
    base = PRIMARY.config_hash()
    for field, value in [
        ("prf_hz", 20.0),
        ("breath_hz", 0.25),
        ("heart_hz", 1.5),
        ("breath_displacement_m", 0.019),
        ("heart_displacement_m", 0.011),
        ("snr_db", 12.0),
        ("seed", 43),
        ("breaths", 6),
    ]:
        import dataclasses

        assert dataclasses.replace(PRIMARY, **{field: value}).config_hash() != base, field
