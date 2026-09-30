"""Closure checks for the committed pre-fix refinement-incidence artifact."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import subprocess

import pytest

from src.m4.bundle import sha256_path
from src.m4.capture_registry import DEFAULT_REGISTRY, load_registry


REPO_ROOT = Path(__file__).parents[1]
ARTIFACT = REPO_ROOT / "reports" / "peak_refinement_diagnostic_2026-09-29.json"
CONFIG = REPO_ROOT / "scripts" / "live_demo_config.yaml"
DIAGNOSTIC_COMMIT = "4280e34d691537d4465fd3d0a0d50b954692bff1"


def _load_artifact() -> dict:
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def _sha256_at_diagnostic_commit(path: Path) -> str:
    """SHA-256 of `path` as committed at DIAGNOSTIC_COMMIT, i.e. the bytes the diagnostic read.

    Comparing against the working-tree file instead would freeze that file forever: any later
    edit, even a comment, would fail this historical-artifact check.
    """
    blob = subprocess.run(
        ["git", "show", f"{DIAGNOSTIC_COMMIT}:{path.relative_to(REPO_ROOT).as_posix()}"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    ).stdout
    return hashlib.sha256(blob).hexdigest()


def test_refinement_artifact_is_radar_only_and_bound_to_clean_sources():
    artifact = _load_artifact()
    assert artifact["schema"] == "peak_refinement_dual_calculation_v2"
    assert artifact["git_commit"] == DIAGNOSTIC_COMMIT
    assert artifact["git_dirty"] is False
    assert artifact["reference_data_accessed"] is False
    assert artifact["returned_estimator_behavior"] == "legacy_unchanged"
    assert artifact["legacy_estimator_id"] == "eca_ahet_v1"
    assert artifact["corrected_estimator_id_reserved"] == "eca_ahet_safe_refine_v2"
    assert artifact["registry_path"] == str(DEFAULT_REGISTRY.relative_to(REPO_ROOT))
    assert artifact["registry_sha256"] == sha256_path(DEFAULT_REGISTRY)
    assert artifact["config_path"] == str(CONFIG.relative_to(REPO_ROOT))
    assert artifact["config_sha256"] == _sha256_at_diagnostic_commit(CONFIG)

    radar = load_registry(DEFAULT_REGISTRY, root=Path("unused")).radar_scope()
    actual = {row["capture_id"]: row for row in artifact["captures"]}
    assert set(actual) == set(radar.captures)
    for capture_id, expected in radar.captures.items():
        row = actual[capture_id]
        assert row["subject_reference_accessed"] is False
        assert row["directory_identity"] == expected.directory
        assert row["raw_adc_sha256"] == expected.adc_stream_sha256
        assert row["metadata_sha256"] == expected.metadata_sha256
        assert row["warmup_sha256"] == expected.warmup_sha256
        assert row["capture_config_sha256"] == expected.capture_config_sha256
        assert row["recorded_lock"] == expected.recorded_lock
        assert row["windows"] == expected.windows


def test_refinement_artifact_summary_recomputes_from_call_rows():
    artifact = _load_artifact()
    calls = artifact["calls"]
    captures = artifact["captures"]
    potential = [
        abs(float(row["potential_final_bpm_difference"]))
        for row in calls
        if row["accepted_candidate"]
        and row["callsite"] == "candidate_second_pass"
        and row["potential_final_bpm_difference"] is not None
    ]
    recomputed = {
        "capture_count": len(captures),
        "window_count": sum(int(row["windows"]) for row in captures),
        "refinement_call_count": len(calls),
        "safe_reason_counts": dict(Counter(row["safe_reason"] for row in calls)),
        "safe_fallback_count": sum(not row["safe_applied"] for row in calls),
        "legacy_delta_out_of_bounds_count": sum(
            row["legacy_delta_out_of_bounds"] for row in calls
        ),
        "legacy_refined_out_of_band_count": sum(
            row["legacy_refined_out_of_band"] for row in calls
        ),
        "accepted_window_count_with_counterfactual": len(potential),
        "max_abs_potential_final_bpm_difference": max(potential),
    }
    assert artifact["summary"] == recomputed
    assert recomputed == {
        "capture_count": 8,
        "window_count": 128,
        "refinement_call_count": 756,
        "safe_reason_counts": {
            "applied": 717,
            "not_strict_local_maximum": 31,
            "refined_frequency_out_of_band": 8,
        },
        "safe_fallback_count": 39,
        "legacy_delta_out_of_bounds_count": 31,
        "legacy_refined_out_of_band_count": 30,
        "accepted_window_count_with_counterfactual": 11,
        "max_abs_potential_final_bpm_difference": pytest.approx(26.479367556414346),
    }

    for row in calls:
        lo, hi = row["allowed_band_hz"]
        assert math.isfinite(lo) and math.isfinite(hi) and lo <= hi
        assert row["legacy_delta_out_of_bounds"] is (
            math.isfinite(row["legacy_delta_bins"])
            and abs(row["legacy_delta_bins"]) > 0.5
        )
        assert row["legacy_refined_out_of_band"] is (
            not math.isfinite(row["legacy_refined_hz"])
            or row["legacy_refined_hz"] < lo
            or row["legacy_refined_hz"] > hi
        )


def test_refinement_artifact_records_worst_accepted_case():
    artifact = _load_artifact()
    worst = max(
        (
            row for row in artifact["calls"]
            if row["potential_final_bpm_difference"] is not None
        ),
        key=lambda row: abs(row["potential_final_bpm_difference"]),
    )
    assert (worst["capture_id"], worst["k"], worst["candidate_rank"]) == (
        "sweep", 4, 2
    )
    assert worst["potential_final_bpm_difference"] == pytest.approx(26.479367556414346)
    matching_second = next(
        row for row in artifact["calls"]
        if row["capture_id"] == "sweep" and row["k"] == 4
        and row["candidate_rank"] == 2 and row["callsite"] == "second_harmonic"
    )
    assert matching_second["legacy_delta_bins"] == pytest.approx(-52.95873511282871)
    assert matching_second["safe_reason"] == "not_strict_local_maximum"
    assert matching_second["safe_delta_bins"] == 0.0
