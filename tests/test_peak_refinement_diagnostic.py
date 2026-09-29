"""Behavior-preserving peak-refinement diagnostic tests."""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest

from scripts import diagnose_peak_refinement
from scripts.diagnose_peak_refinement import safe_refine_frequency
from src.window_pipeline import SAFE_REFINEMENT_ESTIMATOR_ID


def test_safe_refinement_applies_only_to_strict_concave_local_peak():
    result = safe_refine_frequency(
        np.array([0.0, 2.0, 5.0, 3.0, 0.0]),
        np.arange(5, dtype=float) * 0.1,
        2,
        (0.1, 0.3),
    )
    assert result.applied is True
    assert result.reason == "applied"
    assert abs(result.delta_bins) <= 0.5
    assert 0.1 <= result.refined_hz <= 0.3


def test_safe_refinement_falls_back_for_nonpeak_plateau_and_nonfinite():
    freqs = np.arange(5, dtype=float) * 0.1
    cases = (
        (np.array([0.0, 2.0, 2.0, 1.0, 0.0]), "not_strict_local_maximum"),
        (np.array([0.0, 1.0, np.nan, 0.0, 0.0]), "nonfinite_magnitude"),
    )
    for spectrum, reason in cases:
        result = safe_refine_frequency(spectrum, freqs, 2, (0.1, 0.3))
        assert result.applied is False
        assert result.reason == reason
        assert result.refined_hz == freqs[2]
        assert result.delta_bins == 0.0


def test_safe_refinement_rejects_boundary_nonuniform_and_out_of_band():
    spectrum = np.array([1.0, 5.0, 1.0, 0.0])
    assert safe_refine_frequency(spectrum, np.arange(4) * 0.1, 0, (0.0, 0.3)).reason == "boundary_peak"
    assert safe_refine_frequency(spectrum, np.array([0.0, 0.1, 0.21, 0.3]), 1, (0.0, 0.3)).reason == "nonuniform_frequency_grid"
    assert safe_refine_frequency(spectrum, np.arange(4) * 0.1, 1, (0.2, 0.3)).reason == "refined_frequency_out_of_band"


def test_safe_refinement_rejects_malformed_nonfinite_and_inverted_bands():
    spectrum = np.array([0.0, 1.0, 4.0, 1.0, 0.0])
    freqs = np.arange(5, dtype=float) * 0.1
    invalid_bands = ((0.1,), (0.1, 0.3, 0.4), (np.nan, 0.3), (0.1, np.inf), (0.3, 0.1))
    for band in invalid_bands:
        result = safe_refine_frequency(spectrum, freqs, 2, band)
        assert result.applied is False
        assert result.reason == "invalid_band"
        assert result.refined_hz == freqs[2]
        assert result.delta_bins == 0.0


def test_corrected_estimator_identity_is_frozen_before_use():
    assert SAFE_REFINEMENT_ESTIMATOR_ID == "eca_ahet_safe_refine_v2"


def test_diagnostic_source_has_no_reference_access_or_masimo_parser():
    path = Path(__file__).parents[1] / "scripts" / "diagnose_peak_refinement.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert not any("reference_access" in name or "masimo" in name for name in imported)


def test_historical_diagnostic_fails_closed_after_estimator_transition(tmp_path):
    output = tmp_path / "must_not_exist.json"
    with pytest.raises(RuntimeError, match="clean commit 4280e34"):
        diagnose_peak_refinement.run_diagnostic(
            capture_root=tmp_path,
            output=output,
            config_path=diagnose_peak_refinement.DEFAULT_CONFIG,
        )
    assert not output.exists()
