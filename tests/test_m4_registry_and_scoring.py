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
    ScoreContractError,
    ScoredRow,
    coverage_and_metrics,
    paired_partitions,
    percentiles,
)
from src.m4.estimator_runner import (  # noqa: E402
    CANONICAL_ARM_IDS,
    LOCK_ESTIMANDS,
    _validate_capture_inputs,
)
from src.m4.production_suite import PRODUCTION_ARM_ID  # noqa: E402


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


def test_reference_registry_has_the_frozen_explicit_csv_names(registry):
    reference = registry.reference_scope()
    assert Path(reference.masimo_csv["m1"]).name == "demo_massimo1.csv"
    assert Path(reference.masimo_csv["m2"]).name == "demo_massimo2.csv"
    assert Path(reference.masimo_csv["sweep"]).name == "demo_sweep.csv"
    for capture_id in ("m3", "m4", "m5", "m6", "m7"):
        assert Path(reference.masimo_csv[capture_id]).name == f"demo_massimo{capture_id[1:]}.csv"


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


def test_registry_rejects_overlapping_protocol_strata(tmp_path):
    document = yaml.safe_load(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    document["strata"]["paced"].append("m1")
    path = tmp_path / "overlap.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ValueError, match="more than one protocol stratum"):
        load_registry(path)


def test_registry_rejects_guessed_or_missing_protocol_identity(tmp_path):
    document = yaml.safe_load(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    document["protocol"]["sweep"] = "paced_15_bpm"
    path = tmp_path / "guessed-protocol.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ValueError, match="protocol identities"):
        load_registry(path)


@pytest.mark.parametrize(
    "section, malformed_value",
    [
        ("radar", ["m1", "m2"]),
        ("reference", ["m1", "m2"]),
        ("window_grid", [600, 128]),
        ("geometry", ["fs_hz", "iq_swap"]),
    ],
)
def test_a_section_with_the_wrong_yaml_shape_is_a_value_error(
    tmp_path, section, malformed_value
):
    """A structurally malformed registry must fail as a ValueError, not an AttributeError.

    Each of these sections is later iterated with ``.items()`` or converted with ``dict()``.
    YAML parses a list where a mapping was meant without complaint, and the scorer's gate
    (``validate_radar_rows``) only translates ValueError/OSError/yaml.YAMLError into
    ``ScoreContractError`` — so an AttributeError or TypeError here would escape the gate
    entirely.  ``dict(["ab", "cd"])`` is the sharpest case: it succeeds and silently yields
    the wrong geometry.
    """
    document = yaml.safe_load(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    document[section] = malformed_value
    path = tmp_path / f"malformed-{section}.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ValueError, match=f"{section}: expected a mapping"):
        load_registry(path)


@pytest.mark.parametrize("section", ["radar", "reference"])
def test_a_capture_entry_with_the_wrong_yaml_shape_is_a_value_error(tmp_path, section):
    """One malformed capture entry inside an otherwise well-formed section."""
    document = yaml.safe_load(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    document[section]["m1"] = "data/raw/m1"
    path = tmp_path / f"malformed-{section}-entry.yaml"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ValueError, match=f"{section} entry 'm1': expected a mapping"):
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


@pytest.mark.real_data
def test_registry_preflight_matches_all_frozen_radar_inputs(registry):
    """Authorized integration check for the eight exact radar capture identities.

    The default suite skips this node before its body runs.  M5 may opt in only by
    setting ``M8_RUN_REAL_DATA_TESTS=1`` after the source/test scope is frozen.
    """
    radar = registry.radar_scope()
    validated_windows = 0
    for capture_id in registry.capture_ids:
        capture = radar.capture(capture_id)
        metadata, recorded_warmup, chirp, adc_path = _validate_capture_inputs(
            radar, capture
        )
        assert metadata["config"]
        assert recorded_warmup
        assert chirp.iq_swap is radar.geometry["iq_swap"]
        assert adc_path == capture.adc_path(radar.root)
        validated_windows += capture.windows
    assert validated_windows == 128


# ── Metrics, against hand-computed values ────────────────────────────────────

def _row(
    k,
    radar,
    reference,
    *,
    radar_valid=True,
    admitted=True,
    reason="",
    arm="a",
    radar_reason="invalid",
):
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
        radar_reason="ok" if radar_valid else radar_reason,
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
    assert summary.exclusions["reference_insufficient_usable"] == 1
    assert summary.exclusions.get("radar_invalid", 0) == 0


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
        "reference_insufficient_usable": 1,
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


# ── Separate radar and reference exclusion marginals ─────────────────────────
#
# The `exclusions` histogram is mutually exclusive with *reference precedence*: a
# window the Masimo reference rejected is recorded only as a reference exclusion,
# so its radar-side state disappears from that histogram.  Reporting measured radar
# coverage therefore needs a second, non-exclusive marginal that accounts for every
# source row.  Every count below is worked out by hand from the fixture, never read
# back from the implementation.


def _mixed_marginal_fixture():
    """Six hand-counted rows spanning all four (reference, radar) combinations.

    | k | reference          | radar                | disposition                     |
    |---|--------------------|----------------------|---------------------------------|
    | 1 | admitted, 70 bpm   | valid, 72 bpm        | joint                           |
    | 2 | admitted, 70 bpm   | invalid, low_snr     | radar_low_snr                   |
    | 3 | nonstationary      | valid, 71 bpm        | reference_nonstationary         |
    | 4 | insufficient_usable| invalid, no_peak     | reference_insufficient_usable   |
    | 5 | nonstationary      | invalid, low_snr     | reference_nonstationary         |
    | 6 | admitted, 70 bpm   | valid, 66 bpm        | joint                           |
    """
    return [
        _row(1, 72.0, 70.0),
        _row(2, None, 70.0, radar_valid=False, radar_reason="low_snr"),
        _row(3, 71.0, None, admitted=False, reason="nonstationary"),
        _row(
            4, None, None,
            radar_valid=False, radar_reason="no_peak",
            admitted=False, reason="insufficient_usable",
        ),
        _row(
            5, None, None,
            radar_valid=False, radar_reason="low_snr",
            admitted=False, reason="nonstationary",
        ),
        _row(6, 66.0, 70.0),
    ]


def test_radar_only_exclusion_appears_in_the_radar_marginal_alone():
    """Reference admitted the window; only the radar failed."""
    rows = [_row(1, None, 70.0, radar_valid=False, radar_reason="low_snr")]

    summary = coverage_and_metrics(rows)

    assert summary.exclusions == {"radar_low_snr": 1}
    assert summary.reference_exclusions == {}
    assert summary.radar_exclusions == {"low_snr": 1}
    assert summary.n_reference_admitted == 1
    assert summary.n_radar_valid == 0


def test_reference_only_exclusion_leaves_the_radar_marginal_empty():
    """The radar produced a usable finite estimate; the reference was unusable."""
    rows = [_row(1, 71.0, None, admitted=False, reason="nonstationary")]

    summary = coverage_and_metrics(rows)

    assert summary.exclusions == {"reference_nonstationary": 1}
    assert summary.reference_exclusions == {"nonstationary": 1}
    assert summary.radar_exclusions == {}
    assert summary.n_reference_admitted == 0
    assert summary.n_radar_valid == 1
    assert summary.radar_coverage == pytest.approx(1.0)


def test_a_jointly_excluded_row_is_counted_once_but_in_both_marginals():
    """The whole point of the marginals: reference precedence must not hide the radar."""
    rows = [
        _row(
            1, None, None,
            radar_valid=False, radar_reason="no_peak",
            admitted=False, reason="insufficient_usable",
        )
    ]

    summary = coverage_and_metrics(rows)

    # Exactly one entry in the mutually exclusive histogram, chosen by reference precedence.
    assert summary.exclusions == {"reference_insufficient_usable": 1}
    assert sum(summary.exclusions.values()) == 1
    # ...yet the row is visible in both marginals.
    assert summary.reference_exclusions == {"insufficient_usable": 1}
    assert summary.radar_exclusions == {"no_peak": 1}
    assert summary.n_reference_admitted == 0
    assert summary.n_radar_valid == 0


def test_a_joint_row_is_excluded_by_neither_marginal():
    rows = [_row(1, 72.0, 70.0)]

    summary = coverage_and_metrics(rows)

    assert summary.exclusions == {"joint": 1}
    assert summary.reference_exclusions == {}
    assert summary.radar_exclusions == {}
    assert summary.n_reference_admitted == 1
    assert summary.n_radar_valid == 1
    assert summary.n_joint == 1


def test_mixed_fixture_marginals_match_the_hand_count():
    summary = coverage_and_metrics(_mixed_marginal_fixture())

    assert summary.n_source == 6
    assert summary.n_reference_admitted == 3       # k = 1, 2, 6
    assert summary.n_radar_valid == 3              # k = 1, 3, 6
    assert summary.n_joint == 2                    # k = 1, 6

    assert summary.exclusions == {
        "joint": 2,
        "radar_low_snr": 1,
        "reference_insufficient_usable": 1,
        "reference_nonstationary": 2,
    }
    assert summary.reference_exclusions == {"insufficient_usable": 1, "nonstationary": 2}
    assert summary.radar_exclusions == {"low_snr": 2, "no_peak": 1}


def test_the_mutually_exclusive_histogram_understates_radar_failures():
    """`low_snr` happened twice, but reference precedence shows it once in `exclusions`."""
    summary = coverage_and_metrics(_mixed_marginal_fixture())

    assert summary.exclusions["radar_low_snr"] == 1
    assert summary.radar_exclusions["low_snr"] == 2
    assert summary.radar_coverage == pytest.approx(3 / 6)


def test_both_marginals_reconcile_to_the_source_row_count():
    summary = coverage_and_metrics(_mixed_marginal_fixture())

    assert summary.n_source - summary.n_reference_admitted == sum(
        summary.reference_exclusions.values()
    )
    assert summary.n_source - summary.n_radar_valid == sum(
        summary.radar_exclusions.values()
    )
    assert sum(summary.exclusions.values()) == summary.n_source


def test_reconciliation_holds_across_every_legal_reference_radar_state():
    """Exhaustive 3x3 cross product of legal reference and radar states.

    The production reconciliation guards cannot be tripped from plain `ScoredRow`
    inputs (each row contributes exactly one unit to either the admitted/valid count
    or to its marginal), so the meaningful check is that the invariant *holds*
    everywhere rather than that a contrived failure aborts.
    """
    reference_states = [
        ("ok", True, 70.0),
        ("nonstationary", False, None),
        ("insufficient_usable", False, None),
    ]
    radar_states = [
        ("ok", True, 72.0),
        ("low_snr", False, None),
        ("no_peak", False, None),
    ]
    rows = [
        _row(
            index,
            radar_value,
            reference_value,
            radar_valid=radar_valid,
            radar_reason=radar_reason,
            admitted=admitted,
            reason=reference_reason,
        )
        for index, ((reference_reason, admitted, reference_value),
                    (radar_reason, radar_valid, radar_value)) in enumerate(
            [(ref, rad) for ref in reference_states for rad in radar_states]
        )
    ]

    summary = coverage_and_metrics(rows)

    assert summary.n_source == 9
    assert summary.n_reference_admitted == 3        # one admitted reference x 3 radar states
    assert summary.n_radar_valid == 3               # one valid radar x 3 reference states
    assert summary.n_joint == 1                     # only the admitted/valid corner
    assert summary.exclusions == {
        "joint": 1,
        "radar_low_snr": 1,
        "radar_no_peak": 1,
        "reference_insufficient_usable": 3,
        "reference_nonstationary": 3,
    }
    assert summary.reference_exclusions == {"insufficient_usable": 3, "nonstationary": 3}
    assert summary.radar_exclusions == {"low_snr": 3, "no_peak": 3}
    assert summary.n_source - summary.n_reference_admitted == sum(
        summary.reference_exclusions.values()
    )
    assert summary.n_source - summary.n_radar_valid == sum(
        summary.radar_exclusions.values()
    )


def test_marginals_survive_serialization_into_the_metric_summary_document():
    document = coverage_and_metrics(_mixed_marginal_fixture()).to_dict()

    assert document["reference_exclusions"] == {
        "insufficient_usable": 1, "nonstationary": 2
    }
    assert document["radar_exclusions"] == {"low_snr": 2, "no_peak": 1}
    # Sorted keys keep the persisted summary byte-stable across runs.
    assert list(document["radar_exclusions"]) == sorted(document["radar_exclusions"])
    assert list(document["reference_exclusions"]) == sorted(
        document["reference_exclusions"]
    )


def test_paired_intersection_metrics_expose_empty_marginals():
    """On a both-valid intersection every row is joint, so neither marginal fires."""
    production = [_row(1, 70.0, 70.0, arm="prod"), _row(2, 60.0, 70.0, arm="prod")]
    arm = [_row(1, 74.0, 70.0, arm="ahmed"), _row(2, 63.0, 70.0, arm="ahmed")]

    result = paired_partitions(production, arm)

    for side in ("production_metrics_on_intersection", "ahmed_metrics_on_intersection"):
        assert result[side]["reference_exclusions"] == {}
        assert result[side]["radar_exclusions"] == {}


@pytest.mark.parametrize("bad_value", [None, float("nan"), float("inf")])
def test_radar_valid_without_a_finite_value_is_fatal_even_when_the_reference_excluded(
    bad_value,
):
    """A malformed radar row must not be masked by reference precedence.

    Before the marginals existed, `disposition` returned `reference_*` and returned
    early, so this contradictory state was silently absorbed into a reference
    exclusion instead of aborting the run.
    """
    row = _row(1, bad_value, None, admitted=False, reason="nonstationary")

    # The mutually exclusive disposition alone still absorbs the row silently, which
    # is exactly why the radar marginal has to check it independently.
    assert row.disposition == "reference_nonstationary"

    with pytest.raises(ScoreContractError, match="valid radar row has no finite value"):
        coverage_and_metrics([row])


@pytest.mark.parametrize("bad_value", [None, float("nan"), float("inf")])
def test_radar_valid_without_a_finite_value_is_fatal_when_the_reference_admitted(
    bad_value,
):
    rows = [_row(1, bad_value, 70.0)]

    with pytest.raises(ScoreContractError, match="valid radar row has no finite value"):
        coverage_and_metrics(rows)


def test_an_invalid_radar_row_must_carry_an_exact_marginal_reason():
    """`ok` or an empty reason on an invalid row is a contract error, not a category."""
    for radar_reason in ("", "ok"):
        rows = [
            _row(
                1, None, None,
                radar_valid=False, radar_reason=radar_reason,
                admitted=False, reason="nonstationary",
            )
        ]
        with pytest.raises(ScoreContractError, match="exact"):
            coverage_and_metrics(rows)


# ── The exact canonical identities, transcribed from the approved plan ───────
#
# Everything else in the M4 suite refers to `LOCK_ESTIMANDS` / `CANONICAL_ARM_IDS`
# symbolically, so a silent rename or reordering of the arm identities would not
# be caught anywhere.  These literals are the plan section 3 tables.

PLAN_LOCK_ESTIMAND_IDS = (
    "recorded_lock_as_captured",
    "current_production_rerun_lock",
)
PLAN_ARM_IDS = (
    "production_eca_ahet_v1",
    "ahmed_phase_h3_figure_visible_unsuppressed",
    "ahmed_phase_h3_eq26_multiples_suppressed",
    "ahmed_phase_h3_prose_low_or_equal_suppressed",
    "ahmed_phase_h5_figure_visible_unsuppressed",
    "ahmed_phase_h5_eq26_multiples_suppressed",
    "ahmed_phase_h5_prose_low_or_equal_suppressed",
)


def test_the_two_lock_identities_are_exactly_the_plan_section_3_pair():
    assert LOCK_ESTIMANDS == PLAN_LOCK_ESTIMAND_IDS
    assert len(set(LOCK_ESTIMANDS)) == 2


def test_the_seven_arm_identities_are_exactly_the_plan_section_3_table():
    assert CANONICAL_ARM_IDS == PLAN_ARM_IDS
    assert len(set(CANONICAL_ARM_IDS)) == 7
    assert CANONICAL_ARM_IDS[0] == PRODUCTION_ARM_ID
    # One production arm plus three suppression identities at each of H=3 and H=5.
    assert sum(arm.startswith("ahmed_phase_h3_") for arm in CANONICAL_ARM_IDS) == 3
    assert sum(arm.startswith("ahmed_phase_h5_") for arm in CANONICAL_ARM_IDS) == 3
    # Candidate-dependent / matrix_eta Layer A profiles have no Layer B arm.
    assert not any("matrix_eta" in arm or "candidate_row" in arm for arm in CANONICAL_ARM_IDS)


# ── Exact paired-key joins on (capture_id, lock_estimand_id, k) ──────────────


def _paired_row(capture, lock_id, k, arm, radar, reference, *, admitted=True, reason=""):
    return ScoredRow(
        capture_id=capture,
        lock_estimand_id=lock_id,
        k=k,
        arm_id=arm,
        radar_value=radar,
        radar_valid=radar is not None,
        radar_reason="ok" if radar is not None else "no_peak",
        reference_value=reference,
        reference_admitted=admitted,
        reference_reason=reason,
    )


def test_the_paired_join_key_is_capture_lock_and_k():
    """The two lock estimands are separate rows even at the same capture and k.

    If the join collapsed the lock estimand, these four rows would look like two
    duplicate keys and the comparison would abort instead of scoring both locks.
    """
    production = [
        _paired_row("m1", LOCK_ESTIMANDS[0], 1, "prod", 70.0, 70.0),
        _paired_row("m1", LOCK_ESTIMANDS[1], 1, "prod", 60.0, 70.0),
    ]
    arm = [
        _paired_row("m1", LOCK_ESTIMANDS[0], 1, "ahmed", 72.0, 70.0),
        _paired_row("m1", LOCK_ESTIMANDS[1], 1, "ahmed", 72.0, 70.0),
    ]

    result = paired_partitions(production, arm)

    assert result["n_reference_admitted"] == 2
    assert result["partitions"]["both"] == 2
    # production errors 0 and -10 -> MAE 5, bias -5; Ahmed +2 twice -> MAE 2.
    assert result["production_metrics_on_intersection"]["mae"] == pytest.approx(5.0)
    assert result["production_metrics_on_intersection"]["bias"] == pytest.approx(-5.0)
    assert result["ahmed_metrics_on_intersection"]["mae"] == pytest.approx(2.0)


@pytest.mark.parametrize("side", ["production", "Ahmed"])
def test_a_duplicate_paired_key_aborts_instead_of_joining_many_to_many(side):
    production = [_paired_row("m1", LOCK_ESTIMANDS[0], 1, "prod", 70.0, 70.0)]
    arm = [_paired_row("m1", LOCK_ESTIMANDS[0], 1, "ahmed", 72.0, 70.0)]
    if side == "production":
        production = production + [_paired_row("m1", LOCK_ESTIMANDS[0], 1, "prod", 71.0, 70.0)]
    else:
        arm = arm + [_paired_row("m1", LOCK_ESTIMANDS[0], 1, "ahmed", 73.0, 70.0)]

    with pytest.raises(ScoreContractError, match=f"duplicate {side} paired key"):
        paired_partitions(production, arm)


@pytest.mark.parametrize(
    "production_keys,arm_keys",
    [
        ((1, 2), (1, 3)),   # one key missing on each side
        ((1, 2), (1,)),     # Ahmed arm is short a window
        ((1,), (1, 2)),     # production is short a window
    ],
)
def test_a_missing_paired_key_aborts_instead_of_shrinking_the_denominator(
    production_keys, arm_keys
):
    production = [
        _paired_row("m1", LOCK_ESTIMANDS[0], k, "prod", 70.0, 70.0) for k in production_keys
    ]
    arm = [_paired_row("m1", LOCK_ESTIMANDS[0], k, "ahmed", 72.0, 70.0) for k in arm_keys]

    with pytest.raises(ScoreContractError, match="paired keys differ"):
        paired_partitions(production, arm)


@pytest.mark.parametrize(
    "reference_value,admitted,reason",
    [(80.0, True, ""), (70.0, False, "nonstationary")],
)
def test_paired_rows_on_one_key_must_share_one_reference_identity(
    reference_value, admitted, reason
):
    """Both arms are scored against the same window of the same reference file."""
    production = [_paired_row("m1", LOCK_ESTIMANDS[0], 1, "prod", 70.0, 70.0)]
    arm = [
        _paired_row(
            "m1", LOCK_ESTIMANDS[0], 1, "ahmed", 72.0, reference_value,
            admitted=admitted, reason=reason,
        )
    ]

    with pytest.raises(ScoreContractError, match="different reference identity"):
        paired_partitions(production, arm)
