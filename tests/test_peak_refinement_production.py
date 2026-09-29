"""Production bounded-refinement behavior and evidence contract."""
from __future__ import annotations

import numpy as np
import yaml

from scripts import score_offline
from src import vitals
from src.m4.production_suite import (
    PRODUCTION_ARM_ID,
    PRODUCTION_ESTIMATOR_ID,
    ProductionEstimatorSuite,
)
from src.window_pipeline import ESTIMATOR_ID, as_window_estimate


def test_safe_refinement_applies_to_strict_peak_and_records_stable_reason():
    result = vitals.refine_peak_hz_safe(
        np.array([0.0, 2.0, 5.0, 3.0, 0.0]),
        np.arange(5, dtype=float) * 0.1,
        2,
        (0.1, 0.3),
    )
    assert result.applied is True
    assert result.reason == "applied"
    assert result.reason_code == vitals.REFINEMENT_REASON_CODES["applied"]
    assert abs(result.delta_bins) <= 0.5
    assert 0.1 <= result.refined_hz <= 0.3


def test_safe_refinement_falls_back_for_nonpeak_and_invalid_band():
    freqs = np.arange(5, dtype=float) * 0.1
    nonpeak = vitals.refine_peak_hz_safe(
        np.array([0.0, 2.0, 1.0, 3.0, 0.0]), freqs, 2, (0.1, 0.3)
    )
    assert nonpeak.refined_hz == freqs[2]
    assert nonpeak.delta_bins == 0.0
    assert nonpeak.reason == "not_strict_local_maximum"

    for band in ((0.1,), (np.nan, 0.3), (0.3, 0.1)):
        invalid = vitals.refine_peak_hz_safe(
            np.array([0.0, 2.0, 5.0, 3.0, 0.0]), freqs, 2, band
        )
        assert invalid.refined_hz == freqs[2]
        assert invalid.reason == "invalid_band"


def test_eca_ahet_uses_safe_path_and_persists_every_refinement(monkeypatch):
    fs = 20.0
    n = 600
    f_r = 0.25
    t = np.arange(n) / fs
    phase = (
        3.0 * np.sin(2 * np.pi * f_r * t)
        + 0.7 * np.sin(2 * np.pi * 4 * f_r * t)
        + 0.4 * np.sin(2 * np.pi * 1.183 * t)
        + 0.2 * np.sin(2 * np.pi * 2.366 * t)
    )

    def legacy_must_not_run(*_args, **_kwargs):
        raise AssertionError("production ECA+AHET called legacy refine_freq_hz")

    monkeypatch.setattr(vitals, "refine_freq_hz", legacy_must_not_run)
    out = vitals.estimate_rate_from_phase(
        phase, fs, vitals.HEART_BAND_HZ, f_r_hz=f_r
    )
    for field in (
        "candidate_initial_refinement_delta_bins",
        "candidate_initial_refinement_reason_code",
        "candidate_refinement_delta_bins",
        "candidate_refinement_reason_code",
        "second_peak_refinement_delta_bins",
        "second_peak_refinement_reason_code",
    ):
        assert out[field].shape == (vitals.AHET_MAX_CANDIDATES,)

    attempted = out["candidate_attempted"]
    assert np.all(out["candidate_initial_refinement_reason_code"][attempted] >= 0)
    assert np.all(out["candidate_refinement_reason_code"][attempted] >= 0)
    available = out["region_available"]
    assert np.all(out["second_peak_refinement_reason_code"][available] >= 0)
    if out["ahet_verified"]:
        rank = out["accepted_candidate_rank"]
        assert out["accepted_candidate_refinement_reason_code"] == int(
            out["candidate_refinement_reason_code"][rank]
        )
        assert out["accepted_second_harmonic_refinement_reason_code"] == int(
            out["second_peak_refinement_reason_code"][rank]
        )


def test_not_attempted_sentinel_is_explicit_for_no_eca_and_rejected_outputs():
    fs = 20.0
    n = 600
    t = np.arange(n) / fs
    no_eca = vitals.estimate_rate_from_phase(
        np.sin(2 * np.pi * 1.2 * t), fs, vitals.HEART_BAND_HZ
    )
    sentinel = vitals.REFINEMENT_NOT_ATTEMPTED_CODE
    for field in (
        "candidate_initial_refinement_reason_code",
        "candidate_refinement_reason_code",
        "second_peak_refinement_reason_code",
    ):
        assert np.all(no_eca[field] == sentinel)
    assert no_eca["accepted_candidate_refinement_reason_code"] == sentinel
    assert no_eca["accepted_second_harmonic_refinement_reason_code"] == sentinel
    assert np.isnan(no_eca["accepted_candidate_refinement_delta_bins"])
    assert np.isnan(no_eca["accepted_second_harmonic_refinement_delta_bins"])

    f_r = 0.25
    rejected = vitals.estimate_rate_from_phase(
        sum(
            amplitude * np.sin(2 * np.pi * harmonic * f_r * t)
            for harmonic, amplitude in ((1, 3.0), (2, 1.5), (3, 1.0), (4, 0.75))
        ),
        fs,
        vitals.HEART_BAND_HZ,
        f_r_hz=f_r,
    )
    assert rejected["ahet_verified"] is False
    assert rejected["accepted_candidate_refinement_reason_code"] == sentinel
    assert rejected["accepted_second_harmonic_refinement_reason_code"] == sentinel
    assert np.isnan(rejected["accepted_candidate_refinement_delta_bins"])
    assert np.isnan(rejected["accepted_second_harmonic_refinement_delta_bins"])
    attempted = rejected["candidate_attempted"]
    assert np.all(rejected["candidate_initial_refinement_reason_code"][attempted] >= 0)
    assert np.all(rejected["candidate_refinement_reason_code"][attempted] >= 0)
    assert np.all(rejected["candidate_initial_refinement_reason_code"][~attempted] == sentinel)
    assert np.all(rejected["candidate_refinement_reason_code"][~attempted] == sentinel)
    available = rejected["region_available"]
    assert np.all(rejected["second_peak_refinement_reason_code"][available] >= 0)
    assert np.all(rejected["second_peak_refinement_reason_code"][~available] == sentinel)


def test_direct_offline_and_m4_paths_share_safe_estimator_identity():
    direct = as_window_estimate(
        {"hr_valid": False, "br_valid": False}, run_config_hash="h"
    )
    config = yaml.safe_load(
        (score_offline.REPO_ROOT / "scripts" / "live_demo_config.yaml").read_text(
            encoding="utf-8"
        )
    )
    m4_spec = ProductionEstimatorSuite(config).arm_specs[0]
    assert direct.estimator_id == "eca_ahet_safe_refine_v2"
    assert ESTIMATOR_ID == direct.estimator_id
    assert score_offline.ESTIMATOR_ID == direct.estimator_id
    assert PRODUCTION_ESTIMATOR_ID == direct.estimator_id
    assert m4_spec.estimator_id == direct.estimator_id
    profile = yaml.safe_load(
        (
            score_offline.REPO_ROOT
            / "experiments"
            / "m8_ahmed_transfer"
            / "layer_b_profiles.yaml"
        ).read_text(encoding="utf-8")
    )
    assert profile["production_arm"] == PRODUCTION_ARM_ID
