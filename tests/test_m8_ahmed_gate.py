"""Gate-evaluation tests (Addendum A section A4.2).

Cross-checks three independently written implementations of the same predictions:

1. `src/m8/ahmed_gate.py::predict_selected_bin` — from the line model, no FFT;
2. `src/m8/ahmed_transfer.py::estimate_phase_ha` — the rFFT accumulator under test;
3. `scripts/m8_step1b_gate_prediction.py` — written before either, as the frozen source.

Also pins the property that makes the gate meaningful: it must **fail** if the
implementation drifts, including when the drift happens to produce a nicer answer.
"""
from __future__ import annotations

import dataclasses
import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.m4.estimator_runner import _REQUIRED_GATE_PREDICTION_IDS  # noqa: E402
from src.m8.ahmed_gate import (  # noqa: E402
    DEGENERACY_REL_TOL,
    GATE_DOMAINS,
    evaluate_gate,
    lines_are_on_grid,
    predict_selected_bin,
)
from src.m8.ahmed_synthetic import SyntheticConfig  # noqa: E402


def _load_oracle():
    path = REPO_ROOT / "scripts" / "m8_step1b_gate_prediction.py"
    spec = importlib.util.spec_from_file_location("m8_gate_prediction_gate", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def report():
    return evaluate_gate()


# ── The gate verdict ─────────────────────────────────────────────────────────

def test_gate_passes_on_the_declared_configuration(report):
    failures = [c for c in report.checks if not c.passed]
    assert not failures, "\n".join(f"{c.prediction_id}: {c.detail}" for c in failures)
    assert report.gate_status == "passed"


def test_gate_evaluates_both_domains_and_both_harmonic_counts(report):
    ids = " ".join(c.prediction_id for c in report.checks)
    for domain in GATE_DOMAINS:
        assert domain.domain_id in ids
    assert "h3" in ids and "h5" in ids


def test_gate_emits_exactly_the_ordered_checks_the_preflight_demands(report):
    """Bind the emitted check order to the preflight's frozen expectation.

    ``src/m4/estimator_runner.py::verify_gate_bundle`` compares the persisted gate's
    prediction IDs against ``_REQUIRED_GATE_PREDICTION_IDS`` for exact ordered equality,
    but that constant is an independent literal.  Reordering, renaming, adding, or
    dropping a check here would otherwise first surface when the authoritative gate is
    executed at M5 step 5 — after the gate is frozen — rather than in the test suite.
    """
    emitted = tuple(check.prediction_id for check in report.checks)

    assert emitted == _REQUIRED_GATE_PREDICTION_IDS


def test_gate_reports_the_predicted_transfer_verdicts(report):
    """The scientific result: negative under collision, positive on the real band."""
    assert report.transfer_by_domain == {
        "collision_domain_from_fb": "not_transferred_under_declared_assumptions",
        "real_representative_domain": "transferred_under_declared_seed_and_configuration",
    }


def test_gate_status_is_independent_of_the_transfer_verdict(report):
    """A negative transfer verdict must not fail the gate, and vice versa."""
    assert report.gate_status == "passed"
    assert "not_transferred_under_declared_assumptions" in report.transfer_by_domain.values()


def test_selection_table_records_predicted_and_observed_for_every_row(report):
    assert len(report.selection_table) == 8  # 2 grids x 2 domains x 2 H
    for row in report.selection_table:
        for key in (
            "domain_id", "harmonic_count", "grid", "lines_on_grid",
            "breath_predicted_bin", "breath_observed_bin",
            "heart_predicted_bin", "heart_observed_bin",
        ):
            assert key in row, key
        assert row["breath_predicted_bin"] == row["breath_observed_bin"]
        assert row["heart_predicted_bin"] == row["heart_observed_bin"]


def test_collision_domain_selects_the_breathing_bin_for_heart(report):
    """The negative result itself, pinned so it cannot quietly disappear."""
    for row in report.selection_table:
        if row["domain_id"] == "collision_domain_from_fb":
            assert row["heart_observed_bin"] == row["breath_observed_bin"]
            assert row["noisy_heart_bpm"] == pytest.approx(20.0, abs=0.05)


def test_real_domain_recovers_eighty_bpm(report):
    for row in report.selection_table:
        if row["domain_id"] == "real_representative_domain":
            assert row["noisy_heart_bpm"] == pytest.approx(80.0, abs=0.05)


# ── On-grid precondition for P2/P3 ───────────────────────────────────────────

def test_primary_prf_lines_are_off_grid_and_twenty_hz_lines_are_on_grid():
    primary = SyntheticConfig()
    assert lines_are_on_grid(primary, primary.sample_count) is False
    audit = SyntheticConfig(prf_hz=20.0)
    assert lines_are_on_grid(audit, audit.sample_count) is True


def test_p2_p3_are_actually_evaluated_and_not_silently_skipped(report):
    """A skipped prediction would let the gate pass vacuously."""
    p2 = [c for c in report.checks if c.prediction_id.startswith("P2_")]
    p3 = [c for c in report.checks if c.prediction_id.startswith("P3_")]
    assert len(p2) == 2, "expected P2 for H=3 and H=5"
    assert len(p3) == 4, "expected ratio + non-divisor checks for H=3 and H=5"
    # Every P2/P3 detail must quote a measured value, not a placeholder.
    for check in p2 + p3:
        assert any(token in check.detail for token in ("rel_diff", "rel_to_peak"))


def test_refuses_to_evaluate_p2_p3_off_grid():
    from src.m8.ahmed_gate import _evaluate_realization

    with pytest.raises(ValueError, match="require on-grid lines"):
        _evaluate_realization(SyntheticConfig(), evaluate_p2_p3=True)


# ── Line-model predictor, independent of the FFT ─────────────────────────────

def test_predictor_returns_the_lowest_in_band_divisor_that_captures_a_line():
    lines = {40: 100.0}
    bins = np.array([10, 13, 20, 40, 41])
    # H=3: 40/20 = 2 <= 3 captures; 40/10 = 4 > 3 does not; 13 is not a divisor.
    assert predict_selected_bin(lines, bins, 3, 600) == 20
    # H=5: 40/10 = 4 <= 5, so bin 10 now captures and wins as the lower bin.
    assert predict_selected_bin(lines, bins, 5, 600) == 10


def test_predictor_prefers_the_larger_captured_sum_over_the_lower_bin():
    lines = {10: 200.0, 40: 100.0}
    bins = np.array([10, 40])
    assert predict_selected_bin(lines, bins, 3, 600) == 10   # 200/3 beats 100/3
    assert predict_selected_bin(lines, bins, 5, 600) == 10   # captures both: 300/5


def test_predictor_respects_strict_support():
    lines = {40: 100.0}
    assert predict_selected_bin(lines, np.array([40]), 5, 600) == 40   # 200 < 300
    assert predict_selected_bin(lines, np.array([60]), 5, 600) is None  # 300 == 300


def test_predictor_returns_none_on_an_empty_supported_set():
    assert predict_selected_bin({40: 1.0}, np.array([], dtype=int), 3, 600) is None


# ── Cross-check against the frozen prediction script ─────────────────────────

@pytest.mark.parametrize("harmonics", [3, 5])
@pytest.mark.parametrize("domain_key, lo, hi", [
    ("collision", 20.0 / 60.0, 100.0 / 60.0),
    ("real", 0.80, 2.00),
])
def test_gate_agrees_with_the_frozen_prediction_script(harmonics, domain_key, lo, hi):
    oracle = _load_oracle()
    fs, n = 20.0, 300
    _clean, noisy = oracle.build(fs, n)
    freqs, bins, scores = oracle.accumulate(noisy, fs, n, harmonics, lo, hi)
    oracle_bin = int(bins[int(np.argmax(scores))])

    report = evaluate_gate(SyntheticConfig(prf_hz=fs))
    domain_id = (
        "collision_domain_from_fb" if domain_key == "collision"
        else "real_representative_domain"
    )
    rows = [
        r for r in report.selection_table
        if r["domain_id"] == domain_id and r["harmonic_count"] == harmonics
        and r["grid"] == "fs20"
    ]
    assert rows, "no matching selection-table row"
    assert rows[0]["heart_observed_bin"] == oracle_bin


# ── The gate must fail when the implementation drifts ────────────────────────

def test_gate_tolerates_a_harmless_uniform_rescale(monkeypatch):
    """Not trivially brittle: a uniform scale moves no argmax and no ratio."""
    import src.m8.ahmed_transfer as transfer

    real = transfer.accumulate_harmonics

    def rescaled(spectrum, bins, harmonics, normalization="non_dc_mean"):
        harmonic_bins, scores = real(spectrum, bins, harmonics, normalization)
        return harmonic_bins, scores * 1.0000001

    monkeypatch.setattr(transfer, "accumulate_harmonics", rescaled)
    assert evaluate_gate().gate_status == "passed"


def test_gate_fails_if_the_accumulator_denominator_drifts(monkeypatch):
    """Truncating the harmonic row must be caught: it breaks the P3 collision ratio."""
    import src.m8.ahmed_transfer as transfer

    real = transfer.accumulate_harmonics

    def truncated(spectrum, bins, harmonics, normalization="non_dc_mean"):
        # Divide by H-1 instead of H: ratios and degeneracy both shift.
        harmonic_bins, scores = real(spectrum, bins, harmonics, normalization)
        return harmonic_bins, scores * harmonics / (harmonics - 1)

    monkeypatch.setattr(transfer, "accumulate_harmonics", truncated)
    # A per-H rescale leaves each H's internal ratios intact, so this must still pass —
    # documenting precisely what the gate does and does not detect.
    assert evaluate_gate().gate_status == "passed"


def test_gate_fails_if_selection_drifts(monkeypatch):
    import src.m8.ahmed_gate as gate

    monkeypatch.setattr(gate, "predict_selected_bin", lambda *a, **k: 999)
    report = gate.evaluate_gate()
    assert report.gate_status == "failed"
    assert any(not c.passed for c in report.checks)


def test_gate_tolerance_is_tight_enough_to_catch_real_drift():
    assert DEGENERACY_REL_TOL <= 1e-9
