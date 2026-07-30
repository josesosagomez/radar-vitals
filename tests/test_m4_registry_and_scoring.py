"""Registry isolation and neutral-metric tests (plan sections 3.3, 3.5, 4.2).

Every metric below is checked against a hand-computed value, not against the code's own
output. All fixtures are synthetic; nothing here opens a real capture or Masimo file.
"""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.m4.capture_registry import (  # noqa: E402
    DEFAULT_REGISTRY,
    RadarScope,
    ReferenceScope,
    load_registry,
)
from src.m4.estimator_scoring import (  # noqa: E402
    ScoredRow,
    coverage_and_metrics,
    paired_partitions,
    percentiles,
)


@pytest.fixture(scope="module")
def registry():
    return load_registry()


# ── Registry contents match the approved plan ────────────────────────────────

def test_registry_declares_the_eight_planned_captures(registry):
    assert registry.capture_ids == ("m1", "m2", "m3", "m4", "m5", "m6", "m7", "sweep")


def test_window_arithmetic_reconciles_to_the_pinned_counts(registry):
    radar = registry.radar_scope()
    assert radar.total_windows() == 128
    assert radar.evaluation_windows() == 120
    # 2 lock estimands x 128 windows = 256 shared rows; x 7 arms = 1792 estimator rows.
    assert radar.total_windows() * 2 == 256
    assert radar.total_windows() * 2 * 7 == 1792
    assert radar.total_windows() * 2 * 6 == 1536  # Ahmed evidence rows
    assert radar.evaluation_windows() * 2 == 240
    assert radar.evaluation_windows() * 2 * 7 == 1680


@pytest.mark.parametrize(
    "capture_id, frames, windows, tail, recorded, rerun",
    [
        ("m1", 3610, 6, 10, 23, 27),
        ("m2", 3611, 6, 11, 20, 26),
        ("sweep", 9611, 16, 11, 21, 26),
        ("m3", 12005, 20, 5, 26, 26),
        ("m4", 12002, 20, 2, 25, 25),
        ("m5", 12005, 20, 5, 25, 25),
        ("m6", 12003, 20, 3, 24, 24),
        ("m7", 12020, 20, 20, 32, 32),
    ],
)
def test_per_capture_values_match_the_plan(registry, capture_id, frames, windows, tail, recorded, rerun):
    capture = registry.radar_scope().capture(capture_id)
    assert (capture.frames, capture.windows, capture.tail_frames) == (frames, windows, tail)
    assert (capture.recorded_lock, capture.rerun_lock) == (recorded, rerun)


def test_every_declared_hash_is_a_full_sha256(registry):
    for capture in registry.radar_scope().captures.values():
        for digest in (
            capture.adc_stream_sha256,
            capture.metadata_sha256,
            capture.warmup_sha256,
            capture.capture_config_sha256,
        ):
            assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


def test_protocol_strata_match_the_plan(registry):
    reference = registry.reference_scope()
    assert reference.stratum_of("m1") == "natural"
    assert reference.stratum_of("m2") == "paced"
    assert reference.stratum_of("sweep") == "paced"
    for capture_id in ("m3", "m4", "m5", "m6", "m7"):
        assert reference.stratum_of(capture_id) == "unknown"


def test_sweep_target_is_recorded_as_unavailable_not_guessed(registry):
    assert registry.reference_scope().protocol["sweep"] == "paced_schedule_target_unavailable"


# ── Structural radar / reference isolation ───────────────────────────────────

def test_radar_scope_exposes_no_masimo_information(registry):
    radar = registry.radar_scope()
    assert isinstance(radar, RadarScope)
    text = repr(radar).lower()
    assert "masimo" not in text
    for attribute in dir(radar):
        assert "masimo" not in attribute.lower()
        assert "reference" not in attribute.lower()


def test_reference_scope_exposes_no_raw_adc_information(registry):
    reference = registry.reference_scope()
    assert isinstance(reference, ReferenceScope)
    text = repr(reference).lower()
    assert "adc_stream" not in text
    for attribute in dir(reference):
        assert "adc" not in attribute.lower()


def test_a_masimo_key_inside_the_radar_section_is_fatal(tmp_path):
    document = yaml.safe_load(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    document["radar"]["m1"]["masimo_sha256"] = "0" * 64
    path = tmp_path / "leaky.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ValueError, match="never be able to reach a Masimo path"):
        load_registry(path)


def test_registry_rejects_mismatched_capture_sets(tmp_path):
    document = yaml.safe_load(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    del document["reference"]["m7"]
    path = tmp_path / "partial.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ValueError, match="different captures"):
        load_registry(path)


def test_registry_rejects_inconsistent_window_arithmetic(tmp_path):
    document = yaml.safe_load(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    document["radar"]["m1"]["windows"] = 7
    path = tmp_path / "badwindows.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ValueError, match="windows"):
        load_registry(path)


def test_registry_rejects_a_wrong_total(tmp_path):
    document = yaml.safe_load(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    document["window_grid"]["total_windows"] = 999
    path = tmp_path / "badtotal.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ValueError, match="total_windows"):
        load_registry(path)


def test_registry_loading_opens_no_capture_file(registry, monkeypatch):
    """Loading must be metadata-only; it must not stat or open a raw capture."""
    import builtins

    real_open = builtins.open

    def guarded(file, *args, **kwargs):
        if "adc_stream" in str(file) or "masimo" in str(file).lower():
            raise AssertionError(f"registry load touched capture data: {file}")
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded)
    load_registry()


# ── Metrics, against hand-computed values ────────────────────────────────────

def _row(k, radar, reference, *, radar_valid=True, admitted=True, reason="", arm="a"):
    return ScoredRow(
        capture_id="m1",
        lock_estimand_id="recorded_lock_as_captured",
        k=k,
        arm_id=arm,
        radar_value=radar,
        radar_valid=radar_valid,
        reference_value=reference,
        reference_admitted=admitted,
        reference_reason=reason,
    )


def test_metrics_match_a_hand_computation():
    # errors: +2, -4, +6  ->  MAE = 4, RMSE = sqrt((4+16+36)/3) = sqrt(56/3), bias = 4/3
    rows = [_row(1, 72.0, 70.0), _row(2, 66.0, 70.0), _row(3, 76.0, 70.0)]
    summary = coverage_and_metrics(rows)
    assert summary.n_source == 3
    assert summary.n_joint == 3
    assert summary.mae == pytest.approx(4.0)
    assert summary.rmse == pytest.approx(np.sqrt(56.0 / 3.0))
    assert summary.bias == pytest.approx(4.0 / 3.0)


def test_bias_sign_is_radar_minus_reference():
    assert coverage_and_metrics([_row(1, 80.0, 70.0)]).bias == pytest.approx(10.0)
    assert coverage_and_metrics([_row(1, 60.0, 70.0)]).bias == pytest.approx(-10.0)


def test_reference_precedence_outranks_radar_invalidity():
    """A row excluded by the reference is never also counted as a radar exclusion."""
    row = _row(1, None, None, radar_valid=False, admitted=False, reason="insufficient_usable")
    summary = coverage_and_metrics([row])
    assert summary.exclusions["reference_insufficient"] == 1
    assert summary.exclusions["radar_invalid"] == 0


def test_dispositions_are_mutually_exclusive_and_total():
    rows = [
        _row(1, 72.0, 70.0),
        _row(2, None, 70.0, radar_valid=False),
        _row(3, 71.0, None, admitted=False, reason="insufficient_usable"),
        _row(4, 71.0, None, admitted=False, reason="nonstationary"),
    ]
    summary = coverage_and_metrics(rows)
    assert sum(summary.exclusions.values()) == len(rows)
    assert summary.exclusions == {
        "joint": 1,
        "radar_invalid": 1,
        "reference_insufficient": 1,
        "reference_nonstationary": 1,
    }


def test_metrics_use_joint_rows_only():
    rows = [_row(1, 72.0, 70.0), _row(2, 999.0, 70.0, admitted=False, reason="nonstationary")]
    assert coverage_and_metrics(rows).mae == pytest.approx(2.0)


def test_coverage_fractions_and_conditional_denominator():
    rows = [
        _row(1, 72.0, 70.0),
        _row(2, None, 70.0, radar_valid=False),
        _row(3, 71.0, None, admitted=False, reason="nonstationary"),
        _row(4, 71.0, None, admitted=False, reason="nonstationary"),
    ]
    summary = coverage_and_metrics(rows)
    assert summary.reference_coverage == pytest.approx(2 / 4)
    assert summary.joint_coverage == pytest.approx(1 / 4)
    assert summary.joint_given_reference == pytest.approx(1 / 2)


def test_empty_input_yields_nulls_not_a_crash():
    summary = coverage_and_metrics([])
    assert summary.n_source == 0
    assert summary.mae is None and summary.rmse is None and summary.bias is None
    assert summary.reference_coverage is None
    assert summary.joint_given_reference is None
    assert all(v is None for v in summary.error_percentiles.values())


def test_percentiles_use_the_linear_method_explicitly():
    values = [1.0, 2.0, 3.0, 4.0]
    result = percentiles(values)
    assert result["p50"] == pytest.approx(np.percentile(values, 50, method="linear"))
    assert result["p50"] == pytest.approx(2.5)


# ── Paired partitions ────────────────────────────────────────────────────────

def test_partitions_sum_to_the_reference_admitted_universe():
    production = [
        _row(1, 70.0, 70.0, arm="prod"),
        _row(2, None, 70.0, radar_valid=False, arm="prod"),
        _row(3, 70.0, 70.0, arm="prod"),
        _row(4, 70.0, None, admitted=False, reason="nonstationary", arm="prod"),
    ]
    arm = [
        _row(1, 72.0, 70.0, arm="ahmed"),
        _row(2, 72.0, 70.0, arm="ahmed"),
        _row(3, None, 70.0, radar_valid=False, arm="ahmed"),
        _row(4, 72.0, None, admitted=False, reason="nonstationary", arm="ahmed"),
    ]
    result = paired_partitions(production, arm)
    assert result["n_reference_admitted"] == 3
    assert result["partitions"] == {
        "both": 1, "production_only": 1, "ahmed_only": 1, "neither": 0
    }
    assert sum(result["partitions"].values()) == result["n_reference_admitted"]


def test_paired_metrics_are_computed_on_the_intersection_only():
    production = [_row(1, 70.0, 70.0, arm="prod"), _row(2, 60.0, 70.0, arm="prod")]
    arm = [_row(1, 74.0, 70.0, arm="ahmed"), _row(2, None, 70.0, radar_valid=False, arm="ahmed")]
    result = paired_partitions(production, arm)
    assert result["n_intersection"] == 1
    assert result["production_metrics_on_intersection"]["mae"] == pytest.approx(0.0)
    assert result["ahmed_metrics_on_intersection"]["mae"] == pytest.approx(4.0)
    assert result["descriptive_difference_ahmed_minus_production"]["mae"] == pytest.approx(4.0)


def test_empty_intersection_yields_null_metrics_not_an_abort():
    production = [_row(1, 70.0, 70.0, arm="prod")]
    arm = [_row(1, None, 70.0, radar_valid=False, arm="ahmed")]
    result = paired_partitions(production, arm)
    assert result["n_intersection"] == 0
    assert result["ahmed_metrics_on_intersection"]["mae"] is None
    assert result["descriptive_difference_ahmed_minus_production"]["mae"] is None


def test_no_winner_or_p_value_is_emitted():
    production = [_row(1, 70.0, 70.0, arm="prod")]
    arm = [_row(1, 74.0, 70.0, arm="ahmed")]
    result = paired_partitions(production, arm)
    flat = repr(result).lower()
    for forbidden in ("p_value", "pvalue", "winner", "best_arm", "rank"):
        assert forbidden not in flat
