"""Contract tests for the two concrete window suites (M8 Step 1b plan sections 4.1, 6.3).

The load-bearing checks are:

* the production suite's native payload is **fully equivalent to a direct
  `run_window_dsp` call** — `WindowEstimate` is explicitly *not* the equality oracle here,
  because it normalizes and would hide a payload difference;
* mutating the caller's config dict after construction cannot change hashes or results;
* the Ahmed suite extracts phase exactly once and every arm references that one signal;
* no production outcome classifier is ever applied to an Ahmed arm.

All fixtures are synthetic. Nothing here opens a real capture or Masimo file.
"""
from __future__ import annotations

import copy
from pathlib import Path
import sys

import numpy as np
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.m4.estimator_suite import SuiteWindowResult, validate_returned_arms  # noqa: E402
from src.m4.production_suite import (  # noqa: E402
    OUTCOME_CLASSIFIER_ID,
    PRODUCTION_ARM_ID,
    PRODUCTION_ESTIMATOR_ID,
    EcaBindriftOutcomeClassifier,
    ProductionEstimatorSuite,
)
from src.m8.ahmed_transfer import (  # noqa: E402
    ESTIMATOR_ID,
    PHASE_EXTRACTION_METHOD,
    REAL_REPRESENTATIVE_DOMAIN,
    AhmedPhaseConfig,
    AhmedPhaseEstimatorSuite,
    arm_id_for,
)
from src.window_pipeline import run_window_dsp  # noqa: E402

FS = 20.0
N_FRAMES = 600
LOCKED_BIN = 25


@pytest.fixture(scope="module")
def production_config() -> dict:
    text = (REPO_ROOT / "scripts" / "live_demo_config.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


@pytest.fixture(scope="module")
def cube() -> np.ndarray:
    """A deterministic synthetic ADC cube with a breathing-like phase at the locked bin."""
    rng = np.random.Generator(np.random.PCG64(2026))
    n_chirps, n_rx, n_adc = 2, 2, 64
    t = np.arange(N_FRAMES) / FS
    phase = 3.0 * np.sin(2 * np.pi * 0.25 * t) + 0.6 * np.sin(2 * np.pi * 1.2 * t)
    tone = np.exp(1j * 2 * np.pi * LOCKED_BIN * np.arange(n_adc) / n_adc)
    carrier = np.exp(1j * phase)[:, None, None, None] * tone[None, None, None, :]
    noise = 0.01 * (
        rng.standard_normal(carrier.shape) + 1j * rng.standard_normal(carrier.shape)
    )
    return (carrier + noise).astype(np.complex64)


# ── Production suite ─────────────────────────────────────────────────────────

def test_production_native_payload_equals_a_direct_call(production_config, cube):
    """Full nested payload equality, arrays compared with equal_nan=True."""
    suite = ProductionEstimatorSuite(production_config)
    via_suite = suite(cube, LOCKED_BIN, FS).arm_native_results[PRODUCTION_ARM_ID]
    direct = run_window_dsp(cube, LOCKED_BIN, FS, copy.deepcopy(production_config))

    def assert_equal(a, b, path="root"):
        assert type(a) is type(b), f"{path}: {type(a)} vs {type(b)}"
        if isinstance(a, dict):
            assert a.keys() == b.keys(), f"{path}: key mismatch"
            for key in a:
                assert_equal(a[key], b[key], f"{path}.{key}")
        elif isinstance(a, (list, tuple)):
            assert len(a) == len(b), f"{path}: length mismatch"
            for i, (x, y) in enumerate(zip(a, b)):
                assert_equal(x, y, f"{path}[{i}]")
        elif isinstance(a, np.ndarray):
            assert a.dtype == b.dtype, f"{path}: dtype"
            if np.issubdtype(a.dtype, np.inexact):
                assert np.array_equal(a, b, equal_nan=True), f"{path}: values"
            else:
                assert np.array_equal(a, b), f"{path}: values"
        elif isinstance(a, float):
            assert (np.isnan(a) and np.isnan(b)) or a == b, f"{path}: {a} vs {b}"
        else:
            assert a == b, f"{path}: {a!r} vs {b!r}"

    assert_equal(via_suite, direct)


def test_production_suite_declares_exactly_one_arm(production_config):
    suite = ProductionEstimatorSuite(production_config)
    assert len(suite.arm_specs) == 1
    spec = suite.arm_specs[0]
    assert spec.arm_id == PRODUCTION_ARM_ID
    assert spec.estimator_id == PRODUCTION_ESTIMATOR_ID
    assert spec.harmonic_count is None
    assert spec.suppression_profile is None


def test_mutating_the_caller_config_after_construction_changes_nothing(
    production_config, cube
):
    mutable = copy.deepcopy(production_config)
    suite = ProductionEstimatorSuite(mutable)
    before_hash = suite.suite_config_hash
    before = suite(cube, LOCKED_BIN, FS).arm_native_results[PRODUCTION_ARM_ID]

    mutable["heart"]["k_max"] = 99
    mutable["respiration"]["band_hz"] = [0.01, 9.0]

    assert suite.suite_config_hash == before_hash
    after = suite(cube, LOCKED_BIN, FS).arm_native_results[PRODUCTION_ARM_ID]
    assert after["hr_raw"] == before["hr_raw"] or (
        np.isnan(after["hr_raw"]) and np.isnan(before["hr_raw"])
    )
    assert after["br_bpm"] == before["br_bpm"] or (
        np.isnan(after["br_bpm"]) and np.isnan(before["br_bpm"])
    )


def test_config_snapshot_is_a_fresh_copy(production_config):
    suite = ProductionEstimatorSuite(production_config)
    snapshot = suite.config_snapshot
    snapshot["heart"]["k_max"] = 12345
    assert suite.config_snapshot["heart"]["k_max"] != 12345


def test_outcome_classifier_is_declared_and_keyed_by_arm_id(production_config, cube):
    suite = ProductionEstimatorSuite(production_config)
    if suite.arm_specs[0].outcome_classifier_id is None:
        pytest.skip("live config is not in strict_v1 gate mode")
    assert set(suite.outcome_classifiers) == {PRODUCTION_ARM_ID}
    assert suite.outcome_classifiers[PRODUCTION_ARM_ID].classifier_id == OUTCOME_CLASSIFIER_ID
    result = suite(cube, LOCKED_BIN, FS)
    assert set(result.arm_outcomes) == {PRODUCTION_ARM_ID}
    assert result.arm_outcomes[PRODUCTION_ARM_ID] in {
        "covered", "gate_not_run", "other_rejected"
    }


def test_outcome_label_is_stored_outside_the_native_payload(production_config, cube):
    suite = ProductionEstimatorSuite(production_config)
    result = suite(cube, LOCKED_BIN, FS)
    native = result.arm_native_results[PRODUCTION_ARM_ID]
    assert "outcome_class" not in native
    assert "eca_bindrift_outcome_v1" not in native


def test_classifier_maps_missing_f_r_to_nan_like_score_offline():
    classifier = EcaBindriftOutcomeClassifier()
    native = {
        "f_r_hz": None,
        "hr_result": {
            "accepted_candidate_rank": -1,
            "candidate_rejection_code": np.array([-1, -1, -1]),
        },
    }
    assert classifier(native) == "gate_not_run"


def test_rejects_a_classifier_with_the_wrong_id(production_config):
    class Wrong:
        classifier_id = "some_other_v1"

        def __call__(self, native_result):  # pragma: no cover - never invoked
            return "covered"

    with pytest.raises(ValueError, match="unexpected classifier id"):
        ProductionEstimatorSuite(production_config, outcome_classifier=Wrong())


# ── Ahmed suite ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def ahmed_suite() -> AhmedPhaseEstimatorSuite:
    return AhmedPhaseEstimatorSuite(AhmedPhaseConfig(domain=REAL_REPRESENTATIVE_DOMAIN))


def test_ahmed_suite_declares_the_six_arms(ahmed_suite):
    assert len(ahmed_suite.arm_specs) == 6
    assert {spec.arm_id for spec in ahmed_suite.arm_specs} == {
        arm_id_for(h, p)
        for h in (3, 5)
        for p in (
            "figure_visible_unsuppressed",
            "eq26_multiples_suppressed",
            "prose_low_or_equal_suppressed",
        )
    }
    assert {spec.estimator_id for spec in ahmed_suite.arm_specs} == {ESTIMATOR_ID}


def test_ahmed_suite_returns_exactly_its_declared_arms(ahmed_suite, cube):
    result = ahmed_suite(cube, LOCKED_BIN, FS)
    validate_returned_arms(ahmed_suite.arm_specs, result)
    assert isinstance(result, SuiteWindowResult)


def test_ahmed_arms_share_one_extracted_phase(ahmed_suite, cube):
    result = ahmed_suite(cube, LOCKED_BIN, FS)
    hashes = {r["shared_signal_hash"] for r in result.arm_native_results.values()}
    assert len(hashes) == 1
    assert hashes.pop() == result.shared_evidence["shared_signal_hash"]


def test_ahmed_suite_extracts_phase_exactly_once(ahmed_suite, cube, monkeypatch):
    import src.m8.ahmed_transfer as module

    calls = {"n": 0}
    real = module.extract_chest_phase

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(module, "extract_chest_phase", counting)
    ahmed_suite(cube, LOCKED_BIN, FS)
    assert calls["n"] == 1


def test_ahmed_suite_uses_delta_before_mean_only(ahmed_suite, cube, monkeypatch):
    import src.m8.ahmed_transfer as module

    seen: list[str] = []
    real = module.extract_chest_phase

    def recording(cube_slice, locked_bin, method):
        seen.append(method)
        return real(cube_slice, locked_bin, method)

    monkeypatch.setattr(module, "extract_chest_phase", recording)
    ahmed_suite(cube, LOCKED_BIN, FS)
    assert seen == [PHASE_EXTRACTION_METHOD] == ["delta_before_mean"]


def test_ahmed_suite_declares_no_outcome_classifiers(ahmed_suite, cube):
    assert ahmed_suite.outcome_classifiers == {}
    for spec in ahmed_suite.arm_specs:
        assert spec.outcome_classifier_id is None
    assert ahmed_suite(cube, LOCKED_BIN, FS).arm_outcomes == {}


def test_ahmed_arm_hashes_are_unique_and_match_the_native_records(ahmed_suite, cube):
    by_arm = {spec.arm_id: spec.run_config_hash for spec in ahmed_suite.arm_specs}
    assert len(set(by_arm.values())) == 6
    result = ahmed_suite(cube, LOCKED_BIN, FS)
    for arm_id, record in result.arm_native_results.items():
        assert record["run_config_hash"] == by_arm[arm_id]
        assert record["arm_id"] == arm_id


def test_ahmed_suite_rejects_a_non_config_argument():
    with pytest.raises(TypeError, match="AhmedPhaseConfig"):
        AhmedPhaseEstimatorSuite({"domain": "real"})


def test_both_suites_are_deterministic(production_config, ahmed_suite, cube):
    prod = ProductionEstimatorSuite(production_config)
    a1 = ahmed_suite(cube, LOCKED_BIN, FS)
    a2 = ahmed_suite(cube, LOCKED_BIN, FS)
    assert a1.shared_evidence["shared_signal_hash"] == a2.shared_evidence["shared_signal_hash"]
    p1 = prod(cube, LOCKED_BIN, FS).arm_native_results[PRODUCTION_ARM_ID]
    p2 = prod(cube, LOCKED_BIN, FS).arm_native_results[PRODUCTION_ARM_ID]
    assert np.array_equal(p1["phase_clean"], p2["phase_clean"], equal_nan=True)
