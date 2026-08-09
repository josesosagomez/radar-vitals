"""Portable M4 scorer contracts using hand-computed reference fixtures only."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.m4.capture_registry import DEFAULT_REGISTRY, ReferenceScope  # noqa: E402
from src.m4.bundle import read_manifest, verify_bundle  # noqa: E402
from src.m4.estimator_runner import (  # noqa: E402
    Authorization,
    CANONICAL_ARM_IDS,
    LOCK_ESTIMANDS,
)
from src.m4.estimator_scoring import (  # noqa: E402
    CLAIM_STATUS,
    COMPARATIVE_UNIVERSE,
    DIAGNOSTIC_UNIVERSE,
    ScoreContractError,
    execute_score,
    persist_score_artifacts,
    run_score_stage,
    validate_radar_rows,
    verify_frozen_registry_agreement,
)


CAPTURES = ("m1", "m2", "m3", "m4", "m5", "m6", "m7", "sweep")


def _reference_scope(tmp_path: Path) -> ReferenceScope:
    protocol = {
        "m1": "natural",
        "m2": "paced_16_bpm",
        "sweep": "paced_schedule_target_unavailable",
        **{capture: "unknown_protocol_development" for capture in ("m3", "m4", "m5", "m6", "m7")},
    }
    return ReferenceScope(
        root=tmp_path,
        masimo_csv={capture: f"{capture}/reference.csv" for capture in CAPTURES},
        masimo_sha256={capture: "a" * 64 for capture in CAPTURES},
        protocol=protocol,
        strata={
            "natural": ("m1",),
            "paced": ("m2", "sweep"),
            "unknown": ("m3", "m4", "m5", "m6", "m7"),
        },
    )


def _masimo_frame() -> pd.DataFrame:
    # Epoch 30 belongs only to k=1: the comparators use an exact half-open [start, stop) join.
    epoch = np.arange(600, dtype=np.int64)
    frame = pd.DataFrame(
        {
            "epoch_utc": epoch,
            "pr_bpm": np.where(epoch == 30, 100.0, 70.0),
            "pi": np.full(600, 1.0),
            "rr_bpm": np.full(600, 15.0),
        }
    )
    return frame


def _minimal_radar_rows() -> list[dict]:
    rows: list[dict] = []
    windows_by_capture = {
        "m1": 6, "m2": 6, "sweep": 16,
        "m3": 20, "m4": 20, "m5": 20, "m6": 20, "m7": 20,
    }
    for capture_id in CAPTURES:
        for lock_id in LOCK_ESTIMANDS:
            for k in range(windows_by_capture[capture_id]):
                for arm_index, arm_id in enumerate(CANONICAL_ARM_IDS):
                    rows.append({
                        "run_id": "same-run",
                        "run_hash": "r" * 64,
                        "source_hash": "s" * 64,
                        "capture_id": capture_id,
                        "lock_estimand_id": lock_id,
                        "locked_bin": 23,
                        "k": k,
                        "frame_start": 600 * k,
                        "frame_stop": 600 * (k + 1),
                        "epoch_start": float(30 * k),
                        "epoch_stop": float(30 * (k + 1)),
                        "arm_id": arm_id,
                        "hr_valid": True,
                        "hr_raw": 70.0 if arm_index == 0 else 72.0,
                        "hr_validity_reason": "ok",
                        "br_valid": True,
                        "br_bpm": 15.0,
                        "br_validity_reason": "ok",
                        "window_origin_role": "lock_selection_in_sample" if k == 0 else "evaluation",
                    })
    return rows


def test_execute_score_keeps_k0_out_of_the_comparative_metric(tmp_path):
    opened: list[Path] = []

    def loader(path: Path):
        opened.append(path)
        return _masimo_frame()

    result = execute_score(
        radar_rows=_minimal_radar_rows(),
        reference=_reference_scope(tmp_path),
        file_hash=lambda path: "a" * 64,
        masimo_loader=loader,
    )
    assert len(opened) == 8
    assert all(path.name == "reference.csv" for path in opened)
    for vital in ("hr", "br"):
        assert sum(
            row["vital"] == vital and row["window_universe"] == DIAGNOSTIC_UNIVERSE
            for row in result.scored_rows
        ) == 112
        assert sum(
            row["vital"] == vital and row["window_universe"] == COMPARATIVE_UNIVERSE
            for row in result.scored_rows
        ) == 1680

    production = next(
        row for row in result.metrics["per_capture"]
        if row["capture_id"] == "m1"
        and row["lock_estimand_id"] == LOCK_ESTIMANDS[0]
        and row["window_universe"] == COMPARATIVE_UNIVERSE
        and row["arm_id"] == CANONICAL_ARM_IDS[0]
        and row["vital"] == "hr"
    )
    diagnostic = next(
        row for row in result.metrics["per_capture"]
        if row["capture_id"] == "m1"
        and row["lock_estimand_id"] == LOCK_ESTIMANDS[0]
        and row["window_universe"] == DIAGNOSTIC_UNIVERSE
        and row["arm_id"] == CANONICAL_ARM_IDS[0]
        and row["vital"] == "hr"
    )
    assert production["comparative_eligible"] is True
    assert production["metrics"]["n_source"] == 5
    assert production["metrics"]["mae"] == pytest.approx(0.0)
    assert diagnostic["comparative_eligible"] is False
    assert diagnostic["metrics"]["n_source"] == 1
    # The 100 bpm sample at the exact right boundary (epoch 30) is excluded from k0.
    assert diagnostic["metrics"]["mae"] == pytest.approx(0.0)


def test_execute_score_reports_hand_computed_ahmed_difference_and_labels(tmp_path):
    result = execute_score(
        radar_rows=_minimal_radar_rows(),
        reference=_reference_scope(tmp_path),
        file_hash=lambda path: "a" * 64,
        masimo_loader=lambda path: _masimo_frame(),
    )
    comparison = next(
        row for row in result.partitions["production_vs_ahmed"]
        if row["capture_id"] == "m1"
        and row["window_universe"] == COMPARATIVE_UNIVERSE
        and row["vital"] == "hr"
        and row["ahmed_arm_id"] == CANONICAL_ARM_IDS[1]
    )
    assert comparison["partitions"] == {
        "both": 5,
        "production_only": 0,
        "ahmed_only": 0,
        "neither": 0,
    }
    assert comparison["production_metrics_on_intersection"]["mae"] == 0.0
    assert comparison["ahmed_metrics_on_intersection"]["mae"] == 2.0
    assert comparison["descriptive_difference_ahmed_minus_production"]["mae"] == 2.0
    assert result.metrics["claim_status"] == CLAIM_STATUS
    assert {row["protocol_stratum"] for row in result.metrics["protocol_summaries"]} == {
        "natural", "paced", "unknown"
    }
    assert "all" not in {row["protocol_stratum"] for row in result.metrics["protocol_summaries"]}
    flat = repr(result.metrics).lower() + repr(result.partitions).lower()
    for forbidden in ("best_arm", "winner", "p_value", "metronome"):
        assert forbidden not in flat


def test_reference_hash_mismatch_stops_before_parser_open(tmp_path):
    opened = {"count": 0}

    def forbidden_loader(path):
        opened["count"] += 1
        raise AssertionError("parser must not open a hash-mismatched reference")

    with pytest.raises(ScoreContractError, match="Masimo SHA-256"):
        execute_score(
            radar_rows=_minimal_radar_rows(),
            reference=_reference_scope(tmp_path),
            file_hash=lambda path: "0" * 64,
            masimo_loader=forbidden_loader,
        )
    assert opened["count"] == 0


def test_score_preflight_rejection_occurs_before_any_reference_path_access(tmp_path):
    class BombReference:
        def csv_path(self, capture_id):
            raise AssertionError("reference path was accessed before preflight")

    with pytest.raises(Exception, match="gate|manifest"):
        run_score_stage(
            gate_dir=tmp_path / "missing-gate",
            authorization_path=tmp_path / "missing-authorization.yaml",
            source_manifest_sha256="s" * 64,
            radar_dir=tmp_path / "missing-radar",
            reference=BombReference(),
            run_id="must-not-run",
        )


def test_scored_bundle_is_immutable_and_claim_ineligible(tmp_path):
    result = execute_score(
        radar_rows=_minimal_radar_rows(),
        reference=_reference_scope(tmp_path),
        file_hash=lambda path: "a" * 64,
        masimo_loader=lambda path: _masimo_frame(),
    )
    authorization = Authorization(
        authorization_id="portable",
        gate_manifest_sha256="g" * 64,
        source_manifest_sha256="s" * 64,
        allowed_stages=("real-smoke", "real-radar", "score"),
        capture_ids=CAPTURES,
        lock_estimands=LOCK_ESTIMANDS,
        arm_ids=CANONICAL_ARM_IDS,
        approved_by="test",
        approved_on="2026-08-08",
    )
    bundle = persist_score_artifacts(
        result,
        out_root=tmp_path / "scored",
        run_id="portable-score",
        source_manifest_sha256="s" * 64,
        authorization=authorization,
        authorization_sha256="a" * 64,
        gate_manifest_sha256="g" * 64,
        radar_manifest_sha256="r" * 64,
    )
    manifest = verify_bundle(bundle.root)
    _document, manifest_hash = read_manifest(bundle.root)
    assert manifest_hash == bundle.manifest_sha256
    assert manifest["promotion_eligible"] is False
    assert manifest["claim_status"] == CLAIM_STATUS
    assert manifest["source_chain_verified"] is True
    assert manifest["scored_row_count"] == 3584
    assert manifest["production_provenance_status"] == (
        "legacy_api_no_m1_clean_tree_attestation"
    )
    assert set(manifest["reference_sha256_by_capture"]) == set(CAPTURES)
    for name in (
        "rows.json",
        "metrics.json",
        "partitions.json",
        "production_summary.json",
        "provenance.json",
    ):
        document = json.loads((bundle.root / name).read_text(encoding="utf-8"))
        assert document["claim_status"] == CLAIM_STATUS
    production = json.loads(
        (bundle.root / "production_summary.json").read_text(encoding="utf-8")
    )["production_audit"]
    assert production["universe_partition"]["all_complete_windows"] == 128
    assert production["canonical_provenance_status"] == (
        "legacy_api_no_m1_clean_tree_attestation"
    )
    resolved = yaml.safe_load((bundle.root / "resolved_config.yaml").read_text(encoding="utf-8"))
    assert resolved["claim_status"] == CLAIM_STATUS


def test_direct_persistence_rejects_unverified_production_digest_maps(tmp_path):
    """Regression: syntactically valid caller hashes must never become canonical."""
    result = execute_score(
        radar_rows=_minimal_radar_rows(),
        reference=_reference_scope(tmp_path),
        file_hash=lambda path: "a" * 64,
        masimo_loader=lambda path: _masimo_frame(),
    )
    authorization = Authorization(
        authorization_id="portable",
        gate_manifest_sha256="g" * 64,
        source_manifest_sha256="s" * 64,
        allowed_stages=("real-smoke", "real-radar", "score"),
        capture_ids=CAPTURES,
        lock_estimands=LOCK_ESTIMANDS,
        arm_ids=CANONICAL_ARM_IDS,
        approved_by="test",
        approved_on="2026-08-08",
    )
    fabricated_identity = {
        "git_commit": "f" * 40,
        "git_tree_clean": True,
        "source_manifest_sha256": "s" * 64,
        "raw_adc_sha256_by_capture": {
            capture_id: "1" * 64 for capture_id in CAPTURES
        },
        "capture_config_sha256_by_capture": {
            capture_id: "2" * 64 for capture_id in CAPTURES
        },
    }

    with pytest.raises(ScoreContractError, match="unverified production input identity"):
        persist_score_artifacts(
            result,
            out_root=tmp_path / "scored",
            run_id="must-not-persist",
            source_manifest_sha256="s" * 64,
            authorization=authorization,
            authorization_sha256="a" * 64,
            gate_manifest_sha256="g" * 64,
            radar_manifest_sha256="r" * 64,
            production_input_identity=fabricated_identity,
        )

    assert not (tmp_path / "scored").exists()


def _canonical_row(capture_id: str, lock_id: str, arm_id: str) -> dict:
    locked_bin = 23 if lock_id == LOCK_ESTIMANDS[0] else 27
    return {
        "schema_version": 2,
        "run_id": "run",
        "run_hash": "1" * 64,
        "source_hash": "2" * 64,
        "capture_id": capture_id,
        "lock_estimand_id": lock_id,
        "locked_bin": locked_bin,
        "k": 0,
        "frame_start": 0,
        "frame_stop": 600,
        "epoch_start": 1000.0,
        "epoch_stop": 1030.0,
        "time_origin_id": "start_wall_utc_approximate_v1",
        "origin_is_approximate": True,
        "evaluation_status": "exploratory_non_frozen",
        "window_set": "full_k0_diagnostic",
        "window_origin_role": "lock_selection_in_sample",
        "suite_id": (
            "production_eca_ahet_suite_v1"
            if arm_id == CANONICAL_ARM_IDS[0]
            else "ahmed_fixed_h_phase_suite_v1"
        ),
        "suite_config_hash": "3" * 64,
        "arm_id": arm_id,
        "estimator_id": (
            "eca_ahet_v1"
            if arm_id == CANONICAL_ARM_IDS[0]
            else "ahmed_fixed_h_phase_v1"
        ),
        "arm_config_hash": ("4" if arm_id == CANONICAL_ARM_IDS[0] else "5") * 64,
        "cube_hash": "6" * 64,
        "window_cube_hash": "7" * 64,
        "shared_signal_hash": ("9" if lock_id == LOCK_ESTIMANDS[0] else "a") * 64,
        "capture_config_hash": "b" * 64,
        "hr_valid": True,
        "hr_raw": 70.0,
        "br_valid": True,
        "br_bpm": 15.0,
        "validity_reason": "ok",
        "hr_validity_reason": "ok",
        "br_validity_reason": "ok",
        "outcome": None,
    }


def _one_window_canonical_rows() -> list[dict]:
    return [
        _canonical_row("m1", lock_id, arm_id)
        for lock_id in LOCK_ESTIMANDS
        for arm_id in CANONICAL_ARM_IDS
    ]


def test_radar_row_validator_rejects_duplicate_and_same_run_mismatch():
    rows = _one_window_canonical_rows()
    identity = validate_radar_rows(rows, expected_capture_windows={"m1": 1})
    assert identity["estimator_row_count"] == 14
    with pytest.raises(ScoreContractError, match="duplicate"):
        validate_radar_rows([*rows, rows[0]], expected_capture_windows={"m1": 1})

    mismatched = [dict(row) for row in rows]
    mismatched[-1]["run_hash"] = "f" * 64
    with pytest.raises(ScoreContractError, match="mixed run/source"):
        validate_radar_rows(mismatched, expected_capture_windows={"m1": 1})


def test_radar_row_validator_reconciles_vital_specific_invalidity_reason():
    rows = [dict(row) for row in _one_window_canonical_rows()]
    rows[0]["br_valid"] = False
    rows[0]["br_bpm"] = None
    rows[0]["br_validity_reason"] = "resp_confidence_low"
    validate_radar_rows(rows, expected_capture_windows={"m1": 1})
    rows[0]["br_validity_reason"] = "ok"
    with pytest.raises(ScoreContractError, match="disagrees with validity"):
        validate_radar_rows(rows, expected_capture_windows={"m1": 1})


def test_scored_artifacts_carry_the_documented_numeric_origin_uncertainty(tmp_path):
    """A 5-15 s origin offset is 17-50% of a 30 s window, so its size must be visible.

    Plan section 3 documents the magnitude; without it a reader of metrics.json sees only
    the qualitative ``origin_is_approximate`` flag and cannot judge the HR alignment risk.
    """
    result = execute_score(
        radar_rows=_minimal_radar_rows(),
        reference=_reference_scope(tmp_path),
        file_hash=lambda path: "a" * 64,
        masimo_loader=lambda path: _masimo_frame(),
    )

    for artifact in (result.metrics, result.partitions):
        assert artifact["origin_uncertainty_seconds_range"] == [5, 15]

    authorization = Authorization(
        authorization_id="portable",
        gate_manifest_sha256="g" * 64,
        source_manifest_sha256="s" * 64,
        allowed_stages=("real-smoke", "real-radar", "score"),
        capture_ids=CAPTURES,
        lock_estimands=LOCK_ESTIMANDS,
        arm_ids=CANONICAL_ARM_IDS,
        approved_by="test",
        approved_on="2026-08-08",
    )
    bundle = persist_score_artifacts(
        result,
        out_root=tmp_path / "scored",
        run_id="origin-uncertainty",
        source_manifest_sha256="s" * 64,
        authorization=authorization,
        authorization_sha256="a" * 64,
        gate_manifest_sha256="g" * 64,
        radar_manifest_sha256="r" * 64,
    )
    for name in ("rows.json", "metrics.json", "partitions.json", "production_summary.json"):
        document = json.loads((bundle.root / name).read_text(encoding="utf-8"))
        assert document["origin_uncertainty_seconds_range"] == [5, 15]


def test_frozen_locks_and_window_counts_must_agree_with_the_capture_registry(tmp_path):
    """The scorer's literals are duplicated numbers; drift must fail, not pass quietly."""
    verify_frozen_registry_agreement()

    document = yaml.safe_load(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
    document["radar"]["m1"]["recorded_lock"] += 1
    drifted = tmp_path / "capture_registry.yaml"
    drifted.write_text(yaml.safe_dump(document, sort_keys=True), encoding="utf-8")

    with pytest.raises(ScoreContractError, match="frozen locks"):
        verify_frozen_registry_agreement(drifted)


@pytest.mark.parametrize(
    "registry_state, expected_message",
    [
        ("absent", "missing or renamed"),
        ("malformed", "could not be read or parsed"),
    ],
)
def test_an_unreadable_registry_surfaces_as_the_scorer_declared_error_type(
    tmp_path, monkeypatch, registry_state, expected_message
):
    """``validate_radar_rows`` must not leak an OSError/YAMLError past its own type.

    It now cross-checks the frozen locks and window counts against the registry file, so
    a missing, renamed, or malformed registry is an I/O failure inside a scoring
    contract check.  Callers guard the scorer with ``ScoreContractError``; anything else
    escapes that guard and looks like an unrelated crash.
    """
    if registry_state == "absent":
        registry_path = tmp_path / "renamed_away" / "capture_registry.yaml"
    else:
        registry_path = tmp_path / "capture_registry.yaml"
        registry_path.write_text("radar: [unclosed\n", encoding="utf-8")
    monkeypatch.setattr(
        "src.m4.capture_registry.DEFAULT_REGISTRY", registry_path
    )

    with pytest.raises(ScoreContractError, match=expected_message):
        validate_radar_rows([])


def test_execute_score_rejects_arms_that_disagree_on_the_scored_window(tmp_path):
    """One reference window per cell is reused by all seven arms, so it must be shared.

    ``execute_score`` is exported and callable without ``validate_radar_rows``, so it
    must not silently score six arms against a seventh arm's window.
    """
    rows = [dict(row) for row in _minimal_radar_rows()]
    disagreeing = next(
        row for row in rows
        if row["capture_id"] == "m1"
        and row["k"] == 1
        and row["arm_id"] == CANONICAL_ARM_IDS[3]
    )
    disagreeing["epoch_start"] += 7.0
    disagreeing["epoch_stop"] += 7.0

    with pytest.raises(ScoreContractError, match="disagree on the scored window"):
        execute_score(
            radar_rows=rows,
            reference=_reference_scope(tmp_path),
            file_hash=lambda path: "a" * 64,
            masimo_loader=lambda path: _masimo_frame(),
        )
