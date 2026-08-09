"""M1 regressions for canonical production coverage and denominator accounting."""
from __future__ import annotations

from dataclasses import replace
import json
import subprocess

import pandas as pd
import pytest

import src.m4.estimator_scoring as scoring
from src.m4.bundle import sha256_path
from src.m4.capture_registry import DEFAULT_REGISTRY, load_registry
from src.m4.estimator_scoring import (
    COMPARATIVE_UNIVERSE,
    DIAGNOSTIC_UNIVERSE,
    EXPECTED_CAPTURE_WINDOWS,
    PRODUCTION_ALL_WINDOWS_UNIVERSE,
    PRODUCTION_K0_UNIVERSE,
    PRODUCTION_PERSISTED_LOCK_UNIVERSE,
    ScoreContractError,
    ScoredRow,
    _derive_production_input_identity,
    _require_clean_repository_checkout,
    _verify_current_source_checkout,
    production_audit_summary,
    run_score_stage,
)
from src.m8.ahmed_provenance import SourceManifest, build_source_manifest
from src.m4.production_suite import PRODUCTION_ARM_ID
from scripts.score_production import require_clean_tree_commit
from scripts.m8_ahmed_score import pool_score_rows, write_and_display_pooled


REFERENCE_ADMITTED_K = {
    "m1": {1, 2, 3, 4, 5},
    "m2": {1, 2, 3, 4, 5},
    "sweep": {1, 3, 5, 6, 7, 8, 9, 10},
    "m3": set(range(11)),
    "m4": set(range(1, 14)),
    "m5": set(range(1, 10)),
    "m6": set(range(1, 13)),
    "m7": set(range(1, 5)),
}
RADAR_VALID_K = {
    "m1": {0, 2},
    "m2": {0, 1, 3, 4, 5},
    "sweep": {1, 2, 4},
    "m3": set(),
    "m4": {5},
    "m5": set(),
    "m6": {1, 2},
    "m7": {0},
}


def _audit_rows() -> list[ScoredRow]:
    rows: list[ScoredRow] = []
    for capture_id, window_count in EXPECTED_CAPTURE_WINDOWS.items():
        for k in range(window_count):
            radar_valid = k in RADAR_VALID_K[capture_id]
            reference_admitted = k in REFERENCE_ADMITTED_K[capture_id]
            rows.append(
                ScoredRow(
                    capture_id=capture_id,
                    lock_estimand_id="current_production_rerun_lock",
                    k=k,
                    arm_id=PRODUCTION_ARM_ID,
                    radar_value=70.0 + k / 100.0 if radar_valid else None,
                    radar_valid=radar_valid,
                    radar_reason="ok" if radar_valid else "ratio_db_low",
                    reference_value=70.0 if reference_admitted else None,
                    reference_admitted=reference_admitted,
                    reference_reason="ok" if reference_admitted else "nonstationary",
                    vital="hr",
                    window_universe=(
                        DIAGNOSTIC_UNIVERSE if k == 0 else COMPARATIVE_UNIVERSE
                    ),
                )
            )
    return rows


def test_audit_reproduces_the_hand_counted_full_denominators():
    summary = production_audit_summary(_audit_rows())
    full = summary["universes"][PRODUCTION_ALL_WINDOWS_UNIVERSE]

    assert full["micro"]["n_source"] == 128
    assert full["micro"]["n_radar_valid"] == 14
    assert full["micro"]["radar_coverage"] == pytest.approx(14 / 128)
    assert full["micro"]["n_reference_admitted"] == 67
    assert full["micro"]["n_joint"] == 9
    assert full["micro"]["joint_given_reference"] == pytest.approx(9 / 67)
    assert full["denominator_reconstruction"]["reconciles_to_micro"] is True


def test_audit_separates_k0_without_removing_it_from_the_ledger():
    summary = production_audit_summary(_audit_rows())
    partition = summary["universe_partition"]
    persisted = summary["universes"][PRODUCTION_PERSISTED_LOCK_UNIVERSE]
    k0 = summary["universes"][PRODUCTION_K0_UNIVERSE]

    assert partition == {
        "all_complete_windows": 128,
        "lock_selection_in_sample_k0": 8,
        "persisted_lock_windows_k_ge_1": 120,
        "identity": "all_complete_windows = k0 + k_ge_1",
        "reconciles": True,
    }
    assert persisted["micro"]["n_radar_valid"] == 11
    assert persisted["micro"]["radar_coverage"] == pytest.approx(11 / 120)
    assert k0["micro"]["n_radar_valid"] == 3
    assert persisted["includes_k0"] is False
    assert k0["includes_k0"] is True


def test_capture_macro_keeps_zero_output_captures():
    summary = production_audit_summary(_audit_rows())
    full_macro = summary["universes"][PRODUCTION_ALL_WINDOWS_UNIVERSE][
        "capture_macro"
    ]
    persisted_macro = summary["universes"][PRODUCTION_PERSISTED_LOCK_UNIVERSE][
        "capture_macro"
    ]

    assert full_macro["coverage_capture_count"] == 8
    assert full_macro["contributing_capture_count_by_metric"]["radar_coverage"] == 8
    assert full_macro["zero_radar_output_capture_ids"] == ["m3", "m5"]
    assert full_macro["metric_means"]["radar_coverage"] == pytest.approx(
        0.19427083333333334
    )
    assert persisted_macro["zero_radar_output_capture_ids"] == ["m3", "m5", "m7"]
    assert persisted_macro["metric_means"]["radar_coverage"] == pytest.approx(
        0.16973684210526316
    )


def test_missing_duplicate_and_wrong_universe_rows_fail_closed():
    rows = _audit_rows()
    with pytest.raises(ScoreContractError, match="incomplete"):
        production_audit_summary(rows[:-1])
    with pytest.raises(ScoreContractError, match="duplicate"):
        production_audit_summary([*rows, rows[0]])

    wrong = replace(rows[1], window_universe=DIAGNOSTIC_UNIVERSE)
    with pytest.raises(ScoreContractError, match="window_universe"):
        production_audit_summary([rows[0], wrong, *rows[2:]])


def test_summary_does_not_modify_radar_estimates_or_validity():
    rows = _audit_rows()
    before = [
        (row.capture_id, row.k, row.radar_valid, row.radar_value, row.radar_reason)
        for row in rows
    ]

    production_audit_summary(rows)

    after = [
        (row.capture_id, row.k, row.radar_valid, row.radar_value, row.radar_reason)
        for row in rows
    ]
    assert after == before


def test_canonical_cli_clean_tree_gate_fails_before_returning_a_commit(tmp_path):
    responses = iter(
        [
            subprocess.CompletedProcess(
                args=[], returncode=0, stdout=" M tests/scientific_test.py\0", stderr=""
            )
        ]
    )

    with pytest.raises(RuntimeError, match="clean Git tree"):
        require_clean_tree_commit(
            tmp_path, command_runner=lambda *args, **kwargs: next(responses)
        )


def test_canonical_cli_clean_tree_gate_returns_exact_head(tmp_path):
    commit = "a" * 40
    responses = iter(
        [
            subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
            subprocess.CompletedProcess(args=[], returncode=0, stdout=commit + "\n", stderr=""),
        ]
    )

    observed = require_clean_tree_commit(
        tmp_path, command_runner=lambda *args, **kwargs: next(responses)
    )

    assert observed == commit


def _git(repo, *arguments):
    return subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def clean_scientific_git_repo(tmp_path):
    repo = tmp_path / "repository"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "m1-test@example.invalid")
    _git(repo, "config", "user.name", "M1 Test")
    (repo / "src").mkdir()
    scientific_file = repo / "src" / "scientific.py"
    scientific_file.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "src/scientific.py")
    _git(repo, "commit", "-m", "scientific fixture")
    return repo, scientific_file


def test_actual_git_subprocess_proves_clean_head_and_rejects_dirty_tree(
    clean_scientific_git_repo,
):
    repo, scientific_file = clean_scientific_git_repo
    expected_head = _git(repo, "rev-parse", "HEAD")

    assert _require_clean_repository_checkout(repo) == expected_head

    scientific_file.write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(ScoreContractError, match="actually clean whole Git tree"):
        _require_clean_repository_checkout(repo)


def test_current_source_manifest_is_rebuilt_and_source_drift_fails_closed(
    clean_scientific_git_repo, tmp_path
):
    repo, scientific_file = clean_scientific_git_repo
    entry_points = ("src/scientific.py",)
    source = build_source_manifest(
        repo, entry_points=entry_points, required_artifacts=()
    )
    gate_dir = tmp_path / "gate"
    gate_dir.mkdir()
    (gate_dir / "source_manifest.json").write_text(
        json.dumps(source.to_dict()), encoding="utf-8"
    )
    head = _git(repo, "rev-parse", "HEAD")

    rebuilt = _verify_current_source_checkout(
        gate_dir=gate_dir,
        source_manifest_sha256=source.manifest_sha256,
        repository_root=repo,
        git_head=head,
        entry_points=entry_points,
        required_artifacts=(),
    )
    assert rebuilt.manifest_sha256 == source.manifest_sha256
    assert rebuilt.git_commit == head

    with pytest.raises(ScoreContractError, match="actual clean Git HEAD"):
        _verify_current_source_checkout(
            gate_dir=gate_dir,
            source_manifest_sha256=source.manifest_sha256,
            repository_root=repo,
            git_head="f" * 40,
            entry_points=entry_points,
            required_artifacts=(),
        )

    scientific_file.write_text("VALUE = 2\n", encoding="utf-8")
    with pytest.raises(ScoreContractError, match="differs from the verified gate"):
        _verify_current_source_checkout(
            gate_dir=gate_dir,
            source_manifest_sha256=source.manifest_sha256,
            repository_root=repo,
            git_head=head,
            entry_points=entry_points,
            required_artifacts=(),
        )


def test_caller_clean_flag_lie_is_rejected_before_preflight(tmp_path):
    fabricated_identity = {
        "git_commit": "f" * 40,
        "git_tree_clean": True,
        "source_manifest_sha256": "s" * 64,
        "raw_adc_sha256_by_capture": {},
        "capture_config_sha256_by_capture": {},
    }

    with pytest.raises(ScoreContractError, match="caller-supplied.*forbidden"):
        run_score_stage(
            gate_dir=tmp_path / "missing-gate",
            authorization_path=tmp_path / "missing-authorization",
            source_manifest_sha256="s" * 64,
            radar_dir=tmp_path / "missing-radar",
            reference=object(),
            run_id="must-not-run",
            require_scientific_gate=True,
            production_input_identity=fabricated_identity,
            require_production_provenance=True,
        )


def test_no_constructible_token_or_module_level_canonical_writer_remains():
    assert not hasattr(scoring, "_VerifiedProductionInputIdentity")
    assert not hasattr(scoring, "_persist_score_artifacts")


@pytest.mark.parametrize("parent_mutation", ["mismatch", "missing", "extra"])
def test_internal_identity_derivation_rejects_inexact_radar_config_map(parent_mutation):
    radar_scope = load_registry(DEFAULT_REGISTRY).radar_scope()
    source = SourceManifest(
        entries=(
            {
                "path": "experiments/m8_ahmed_transfer/capture_registry.yaml",
                "sha256": sha256_path(DEFAULT_REGISTRY),
            },
        ),
        reference_entries=(),
        manifest_sha256="3" * 64,
        git_commit="4" * 40,
        git_branch="test",
        scoped_dirty=(),
        scoped_untracked=(),
        promotion_eligible=True,
    )
    radar_rows = [
        {
            "capture_id": capture_id,
            "capture_config_hash": capture.capture_config_sha256,
        }
        for capture_id, capture in sorted(radar_scope.captures.items())
    ]
    identity = _derive_production_input_identity(
        source,
        repository_root=DEFAULT_REGISTRY.parents[2],
        radar_rows=radar_rows,
    )
    assert identity["raw_adc_sha256_by_capture"] == {
        capture_id: capture.adc_stream_sha256
        for capture_id, capture in sorted(radar_scope.captures.items())
    }

    if parent_mutation == "mismatch":
        radar_rows[0]["capture_config_hash"] = "f" * 64
    elif parent_mutation == "missing":
        radar_rows.pop()
    else:
        radar_rows.append(
            {"capture_id": "extra", "capture_config_hash": "f" * 64}
        )
    with pytest.raises(ScoreContractError, match="configuration"):
        _derive_production_input_identity(
            source,
            repository_root=DEFAULT_REGISTRY.parents[2],
            radar_rows=radar_rows,
        )


def test_legacy_m8_macro_cannot_drop_a_zero_output_capture():
    scores = pd.DataFrame(
        [
            {
                "capture_id": "with_output",
                "vital": "hr",
                "method": "production_eca_ahet",
                "condition": "production_lock",
                "coverage": 0.5,
                "n_scored": 2,
                "mae_bpm": 1.0,
                "hit_5bpm": 1.0,
                "hit_3bpm": None,
            },
            {
                "capture_id": "zero_output",
                "vital": "hr",
                "method": "production_eca_ahet",
                "condition": "production_lock",
                "coverage": 0.0,
                "n_scored": 0,
                "mae_bpm": None,
                "hit_5bpm": None,
                "hit_3bpm": None,
            },
        ]
    )

    pooled = pool_score_rows(scores)

    assert len(pooled) == 1
    assert pooled[0]["mean_coverage"] == pytest.approx(0.25)
    assert pooled[0]["coverage_capture_count"] == 2
    assert pooled[0]["accuracy_capture_count"] == 1
    assert pooled[0]["production_headline_eligible"] is False


def test_legacy_m8_pool_retains_an_all_zero_output_estimator():
    scores = pd.DataFrame(
        [
            {
                "capture_id": capture_id,
                "vital": "hr",
                "method": "production_eca_ahet",
                "condition": "production_lock",
                "coverage": 0.0,
                "n_scored": 0,
                "mae_bpm": None,
                "hit_5bpm": None,
                "hit_3bpm": None,
            }
            for capture_id in ("zero_a", "zero_b")
        ]
    )

    pooled = pool_score_rows(scores)

    assert len(pooled) == 1
    assert pooled[0]["mean_coverage"] == 0.0
    assert pooled[0]["coverage_capture_count"] == 2
    assert pooled[0]["accuracy_capture_count"] == 0
    assert pooled[0]["n_scored"] == 0
    assert pooled[0]["mae_bpm"] is None
    assert pooled[0]["hit_5bpm"] is None


@pytest.mark.parametrize("include_defined_method", [False, True])
def test_legacy_pooled_display_and_csv_handle_undefined_accuracy(
    tmp_path, capsys, include_defined_method
):
    rows = [
        {
            "capture_id": capture_id,
            "vital": "hr",
            "method": "all_zero_method",
            "condition": "production_lock",
            "coverage": 0.0,
            "n_scored": 0,
            "mae_bpm": None,
            "hit_5bpm": None,
            "hit_3bpm": None,
        }
        for capture_id in ("zero_a", "zero_b")
    ]
    if include_defined_method:
        rows.append(
            {
                "capture_id": "defined",
                "vital": "hr",
                "method": "defined_method",
                "condition": "production_lock",
                "coverage": 0.5,
                "n_scored": 2,
                "mae_bpm": 1.25,
                "hit_5bpm": 0.5,
                "hit_3bpm": None,
            }
        )

    pooled = pool_score_rows(pd.DataFrame(rows))
    write_and_display_pooled(pooled, tmp_path)

    display = capsys.readouterr().out
    assert "all_zero_method" in display
    assert "n/a" in display
    if include_defined_method:
        assert "defined_method" in display
        assert "  1.250" in display

    persisted = pd.read_csv(tmp_path / "pooled.csv")
    zero_row = persisted.loc[persisted["method"] == "all_zero_method"].iloc[0]
    assert zero_row["mean_coverage"] == 0.0
    assert zero_row["accuracy_capture_count"] == 0
    assert zero_row["n_scored"] == 0
    assert pd.isna(zero_row["mae_bpm"])
    assert len(persisted) == (2 if include_defined_method else 1)
