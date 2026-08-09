"""Independent, fixture-only verification of the M9.4 exploratory scorer."""
from __future__ import annotations

import ast
import csv
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from scripts import m9_kotte_score as scoring


ARMS = {
    "kotte_cpi_medoid_nc16_dl1em2": 1.0e-2,
    "kotte_cpi_medoid_nc16_dl1em4": 1.0e-4,
}


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _reference_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = list(scoring.REQUIRED_REFERENCE_COLUMNS)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        for epoch in range(90):
            if epoch < 30:
                pr, rr = 70.0, 10.0
            elif epoch < 60:
                pr, rr = 80.0, 15.0
            else:
                pr, rr = 90.0, 20.0
            writer.writerow([0, epoch + 1, epoch, "1/1/70", "12:00:00 AM", 98, pr, 1.0, 5, rr])


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _fixture(tmp_path: Path, *, duplicate_row: bool = False) -> tuple[Path, Path, Path]:
    radar = tmp_path / "radar" / "fixture_radar_only_unscored"
    capture = tmp_path / "capture"
    capture.mkdir(parents=True)
    metadata = {"start_wall_utc": "1970-01-01T00:00:00+00:00"}
    _write_json(capture / "run_metadata.json", metadata)
    reference = tmp_path / "reference.csv"
    _reference_csv(reference)

    lock_path = radar / "locks" / "c1.json"
    _write_json(lock_path, {"capture_id": "c1", "selected_bin": 5})
    manifest = []
    rows = []
    estimates = {
        "kotte_cpi_medoid_nc16_dl1em2": {
            0: (999.0, 999.0), 1: (82.0, 14.0), 2: (86.0, 22.0)
        },
        "kotte_cpi_medoid_nc16_dl1em4": {
            0: (1.0, 1.0), 1: (80.0, 15.0), 2: (90.0, 20.0)
        },
    }
    for k in range(3):
        shared = radar / "evidence" / "c1" / f"k{k:04d}_shared_z.npz"
        shared.parent.mkdir(parents=True, exist_ok=True)
        shared.write_bytes(f"shared-{k}".encode())
        shared_rel = shared.relative_to(radar).as_posix()
        manifest.append({"artifact": shared_rel, "role": "shared_z", "sha256": _hash(shared)})
        for arm_id, loading in ARMS.items():
            arm_path = radar / "evidence" / "c1" / f"k{k:04d}_{arm_id}.npz"
            arm_path.write_bytes(f"arm-{k}-{arm_id}".encode())
            arm_rel = arm_path.relative_to(radar).as_posix()
            manifest.append({"artifact": arm_rel, "role": "arm", "sha256": _hash(arm_path)})
            heart, breath = estimates[arm_id][k]
            rows.append(
                {
                    "capture_id": "c1", "subject": "S", "protocol_role": "natural",
                    "data_role": "exploratory", "window_index": k,
                    "window_role": "lock_selection_in_sample" if k == 0 else "comparative",
                    "frame_start": k * 600, "frame_end": (k + 1) * 600,
                    "arm_id": arm_id, "loading_delta": loading, "valid": True,
                    "heart_bpm": heart, "breath_bpm": breath, "failure_reason": "",
                    "pair_margin_db": 0.1 + 0.01 * k,
                    "lock_estimand_id": "current_production_rerun_lock",
                    "locked_bin": 5, "lock_binding_status": "available",
                    "lock_evidence": "locks/c1.json", "lock_evidence_sha256": _hash(lock_path),
                    "shared_z_artifact": shared_rel,
                    "shared_z_artifact_sha256": _hash(shared),
                    "evidence_artifact": arm_rel, "evidence_sha256": _hash(arm_path),
                }
            )
    if duplicate_row:
        rows.append(deepcopy(rows[0]))
    _write_json(radar / "estimates.json", rows)
    (radar / "estimates.csv").write_text("fixture\n", encoding="utf-8")
    _write_json(radar / "evidence_manifest.json", manifest)
    _write_json(radar / "capture_failures.json", [])
    _write_json(radar / "lock_map.json", [{"capture_id": "c1", "bin": 5}])
    _write_json(
        radar / "run_meta.json",
        {
            "git_commit": "fixture-commit",
            "capture_inputs": [
                {"capture_id": "c1", "metadata_sha256": _hash(capture / "run_metadata.json")}
            ],
        },
    )
    _write_json(
        radar / "radar_runner_config.json",
        {
            "radar_evaluation": {
                "captures": [
                    {"capture_id": "c1", "directory": "capture", "subject": "S",
                     "protocol_role": "natural", "data_role": "exploratory"}
                ]
            },
            "stage_a": {"arms": [{"arm_id": key, "loading_delta": value} for key, value in ARMS.items()]},
        },
    )
    artifacts = {
        "capture_failures": "capture_failures.json",
        "estimates_csv": "estimates.csv",
        "estimates_json": "estimates.json",
        "evidence_manifest": "evidence_manifest.json",
        "lock_map": "lock_map.json",
        "run_meta": "run_meta.json",
    }
    _write_json(
        radar / "radar_only_handoff.json",
        {
            "role": "immutable_radar_only_handoff",
            "run_id": radar.name,
            "estimator_settings_mutable": False,
            "artifacts": {key: _hash(radar / relative) for key, relative in artifacts.items()},
        },
    )

    config = {
        "scoring": {
            "estimator_settings_mutable_after_scoring": False,
            "outcome_threshold": "none",
            "timestamp_column": "Timestamp",
            "heart_reference_column": "Beats / min",
            "breath_reference_column": "Breaths / min",
            "window_interval": "half_open",
            "comparative_windows_start_k": 1,
            "reuse_existing_pi_and_stationarity_gates": True,
            "metrics": ["coverage", "mae_bpm", "rmse_bpm", "bias_bpm", "bland_altman_limits"],
            "summaries": ["capture", "subject", "protocol", "lock_estimand"],
            "constant_session_median_hr_comparator": True,
            "approximate_origin_label": "exploratory_non_frozen",
            "promotion_eligible": False,
            "capture_references": [{"capture_id": "c1", "csv": "reference.csv"}],
        }
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return radar, config_path, reference


def _execute(tmp_path: Path, *, duplicate_row: bool = False,
             reference_loader=scoring.load_reference_strict) -> scoring.ScoreResult:
    radar, config, _ = _fixture(tmp_path, duplicate_row=duplicate_row)
    return scoring.execute_score(
        radar_input=radar, config_path=config, output_root=tmp_path / "outputs",
        repo_root=tmp_path, official=False, run_id="fixture_score",
        created_utc="2030-01-02T03:04:05+00:00", reference_loader=reference_loader,
    )


def test_import_firewall_contains_no_radar_estimator_or_decoder() -> None:
    source = (Path(__file__).parents[1] / "scripts" / "m9_kotte_score.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    forbidden = ("radar_io", "warmup_select", "window_pipeline", "kotte_core", "m9_kotte_run")
    assert not [name for name in imports if any(token in name for token in forbidden)]
    assert "adc_stream.bin" not in source


def test_half_open_alignment_and_k0_exclusion(tmp_path: Path) -> None:
    result = _execute(tmp_path)
    rows = [
        row for row in result.scored_rows
        if row["method"] == "kotte_cpi_medoid_nc16_dl1em2" and row["vital"] == "hr"
    ]
    assert [row["reference_value_bpm"] for row in rows] == [70.0, 80.0, 90.0]
    assert rows[0]["epoch_end"] == rows[1]["epoch_start"] == 30.0
    assert rows[0]["metric_eligible"] is False
    assert rows[0]["joint_scored"] is False
    assert rows[0]["taint_reason"] == "lock_selection_in_sample"
    assert all(row["window_universe"] == "evaluation_k_ge_1" for row in rows[1:])


def test_hand_derived_metrics_bias_rmse_and_bland_altman(tmp_path: Path) -> None:
    result = _execute(tmp_path)
    summary = next(
        row for row in result.summaries
        if row["stratum_level"] == "capture" and row["method"].endswith("dl1em2")
        and row["vital"] == "hr"
    )
    assert summary["n_windows"] == 2
    assert summary["n_joint_scored"] == 2
    assert summary["algorithmic_coverage"] == 1.0
    assert summary["joint_coverage"] == 1.0
    assert summary["mae_bpm"] == 3.0
    assert summary["rmse_bpm"] == pytest.approx(np.sqrt(10.0))
    assert summary["bias_bpm"] == -1.0
    sd = np.std(np.asarray([2.0, -4.0]), ddof=1)
    assert summary["bland_altman_loa_low_bpm"] == pytest.approx(-1.0 - 1.96 * sd)
    assert summary["bland_altman_loa_high_bpm"] == pytest.approx(-1.0 + 1.96 * sd)


def test_metric_summary_nan_coverage_and_single_pair_loa() -> None:
    one = scoring.metric_summary([2.0, np.nan], n_total=3, n_radar_valid=2,
                                 n_reference_admitted=1)
    assert one["n_joint_scored"] == 1
    assert one["mae_bpm"] == 2.0
    assert one["algorithmic_coverage"] == pytest.approx(2 / 3)
    assert one["bland_altman_loa_low_bpm"] is None
    empty = scoring.metric_summary([], n_total=2, n_radar_valid=0, n_reference_admitted=1)
    assert empty["joint_coverage"] == 0.0
    assert empty["mae_bpm"] is None
    with pytest.raises(scoring.ScoreContractError, match="coverage counts"):
        scoring.metric_summary([], n_total=1, n_radar_valid=2, n_reference_admitted=1)
    with pytest.raises(scoring.ScoreContractError, match="joint scored count"):
        scoring.metric_summary([1.0, 2.0], n_total=2, n_radar_valid=1,
                               n_reference_admitted=2)


def test_constant_session_median_is_descriptive_beside_both_arms(tmp_path: Path) -> None:
    result = _execute(tmp_path)
    methods = {row["method"] for row in result.summaries}
    assert set(ARMS) <= methods
    assert "constant_session_median" in methods
    baseline = next(
        row for row in result.summaries
        if row["stratum_level"] == "capture" and row["method"] == "constant_session_median"
    )
    assert baseline["mae_bpm"] == 5.0
    assert baseline["bias_bpm"] == 0.0
    assert all(row["promotion_eligible"] is False for row in result.summaries)
    assert not any("rank" in key or "best" in key for row in result.summaries for key in row)


@pytest.mark.parametrize("mutation", ["float_timestamp", "null_timestamp", "wrong_hr"])
def test_reference_contract_rejects_malformed_identity(
    tmp_path: Path, mutation: str
) -> None:
    path = tmp_path / "reference.csv"
    _reference_csv(path)
    frame = pd.read_csv(path)
    if mutation == "float_timestamp":
        frame["Timestamp"] = frame["Timestamp"].astype(float) + 0.5
    elif mutation == "null_timestamp":
        frame.loc[1, "Timestamp"] = np.nan
    else:
        frame = frame.rename(columns={"Beats / min": "O2 Saturation duplicate"})
    frame.to_csv(path, index=False)
    expected = "integer Timestamp" if mutation != "wrong_hr" else "exact columns"
    with pytest.raises(scoring.ScoreContractError, match=expected):
        scoring.load_reference_strict(path)


def test_conflicting_raw_timestamp_is_normalized_and_persisted(tmp_path: Path) -> None:
    radar, config, reference = _fixture(tmp_path)
    radar_digest_before = scoring.snapshot_directory(radar).digest_sha256
    raw = pd.read_csv(reference)
    raw["Session"] = raw["Session"].astype(object)
    raw.loc[0, "Session"], raw.loc[0, "Index"] = "first-session", 1000
    raw.loc[1, "Session"], raw.loc[1, "Index"] = "second-session", 1001
    raw.loc[0, ["Beats / min", "Perfusion Index", "Breaths / min"]] = [70.0, 1.0, 10.0]
    raw.loc[1, "Timestamp"] = raw.loc[0, "Timestamp"]
    raw.loc[1, ["Beats / min", "Perfusion Index", "Breaths / min"]] = [71.0, 1.3, 11.0]
    raw.to_csv(reference, index=False)

    normalized = scoring.load_reference_strict(reference)
    merged = normalized[normalized["epoch_utc"] == int(raw.loc[0, "Timestamp"])].iloc[0]
    assert merged["pr_bpm"] == 70.5
    assert merged["pi"] == 1.2
    assert merged["rr_bpm"] == 10.5
    assert merged["session"] == "first-session"
    assert merged["index"] == 1000
    assert normalized.attrs["n_raw_rows"] == 90
    assert normalized.attrs["n_unique_epochs"] == 89
    assert normalized.attrs["n_duplicates_merged"] == 1
    assert normalized.attrs["n_missing_seconds"] == 1

    result = scoring.execute_score(
        radar_input=radar, config_path=config, output_root=tmp_path / "out",
        repo_root=tmp_path, official=False, run_id="raw_duplicate",
        created_utc="2030-01-02T03:04:05+00:00",
    )
    evidence = json.loads(
        (result.output_dir / "reference_duplicate_diagnostics.json").read_text(
            encoding="utf-8"
        )
    )["c1"]
    assert evidence["capture_id"] == "c1"
    assert evidence["reference_path"] == str(reference.resolve())
    assert evidence["reference_sha256"] == _hash(reference)
    assert evidence["n_raw_rows"] == 90
    assert evidence["n_unique_epochs"] == 89
    assert evidence["n_duplicates_merged"] == 1
    assert evidence["n_missing_seconds"] == 1
    run_meta = json.loads((result.output_dir / "run_meta.json").read_text(encoding="utf-8"))
    assert run_meta["reference_duplicate_diagnostics"]["c1"] == evidence
    handoff = json.loads(
        (result.output_dir / "scoring_handoff.json").read_text(encoding="utf-8")
    )
    assert handoff["reference_hashes"]["c1"] == _hash(reference)
    assert handoff["output_hashes"]["reference_duplicate_diagnostics.json"] == _hash(
        result.output_dir / "reference_duplicate_diagnostics.json"
    )
    assert handoff["output_hashes"]["run_meta.json"] == _hash(
        result.output_dir / "run_meta.json"
    )
    assert scoring.snapshot_directory(radar).digest_sha256 == radar_digest_before


@pytest.mark.parametrize(
    "diagnostic_key",
    ["n_raw_rows", "n_unique_epochs", "n_duplicates_merged", "n_missing_seconds"],
)
def test_inconsistent_parser_diagnostics_are_rejected(
    tmp_path: Path, diagnostic_key: str
) -> None:
    radar, config, _ = _fixture(tmp_path)

    def inconsistent_loader(path: Path) -> pd.DataFrame:
        normalized = scoring.load_reference_strict(path)
        normalized.attrs[diagnostic_key] += 1
        return normalized

    with pytest.raises(scoring.ScoreContractError, match="inconsistent"):
        scoring.execute_score(
            radar_input=radar, config_path=config, output_root=tmp_path / "out",
            repo_root=tmp_path, official=False, run_id="inconsistent_diagnostics",
            created_utc="2030-01-02T03:04:05+00:00",
            reference_loader=inconsistent_loader,
        )
    assert not (tmp_path / "out").exists()


def test_post_parser_duplicate_epoch_is_rejected(tmp_path: Path) -> None:
    radar, config, _ = _fixture(tmp_path)

    def duplicate_after_parser(path: Path) -> pd.DataFrame:
        normalized = scoring.load_reference_strict(path)
        return pd.concat([normalized, normalized.iloc[[0]]], ignore_index=True)

    with pytest.raises(scoring.ScoreContractError, match="duplicate epoch_utc"):
        scoring.execute_score(
            radar_input=radar, config_path=config, output_root=tmp_path / "out",
            repo_root=tmp_path, official=False, run_id="post_parser_duplicate",
            created_utc="2030-01-02T03:04:05+00:00",
            reference_loader=duplicate_after_parser,
        )
    assert not (tmp_path / "out").exists()


def test_duplicate_radar_key_rejected(tmp_path: Path) -> None:
    radar, config, _ = _fixture(tmp_path, duplicate_row=True)
    with pytest.raises(scoring.ScoreContractError, match="duplicate radar estimate key"):
        scoring.execute_score(
            radar_input=radar, config_path=config, output_root=tmp_path / "out",
            repo_root=tmp_path, official=False, run_id="duplicate",
            created_utc="2030-01-02T03:04:05+00:00",
        )


def test_missing_and_duplicate_reference_identity_rejected(tmp_path: Path) -> None:
    radar, config, reference = _fixture(tmp_path)
    reference.unlink()
    with pytest.raises(scoring.ScoreContractError, match="missing reference"):
        scoring.execute_score(
            radar_input=radar, config_path=config, output_root=tmp_path / "out",
            repo_root=tmp_path, official=False, run_id="missing",
            created_utc="2030-01-02T03:04:05+00:00",
        )
    _reference_csv(reference)
    document = yaml.safe_load(config.read_text(encoding="utf-8"))
    document["scoring"]["capture_references"].append(
        {"capture_id": "c1", "csv": "reference.csv"}
    )
    config.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    with pytest.raises(scoring.ScoreContractError, match="duplicate reference identity"):
        scoring.execute_score(
            radar_input=radar, config_path=config, output_root=tmp_path / "out2",
            repo_root=tmp_path, official=False, run_id="duplicate_ref",
            created_utc="2030-01-02T03:04:05+00:00",
        )


def test_radar_hash_mismatch_rejected(tmp_path: Path) -> None:
    radar, config, _ = _fixture(tmp_path)
    (radar / "estimates.csv").write_text("changed\n", encoding="utf-8")
    with pytest.raises(scoring.ScoreContractError, match="handoff hash mismatch"):
        scoring.execute_score(
            radar_input=radar, config_path=config, output_root=tmp_path / "out",
            repo_root=tmp_path, official=False, run_id="changed",
            created_utc="2030-01-02T03:04:05+00:00",
        )


def test_nonmapping_radar_handoff_rejected_fail_closed(tmp_path: Path) -> None:
    radar, config, _ = _fixture(tmp_path)
    _write_json(radar / "radar_only_handoff.json", ["not", "a", "mapping"])
    with pytest.raises(scoring.ScoreContractError, match="handoff must be a mapping"):
        scoring.execute_score(
            radar_input=radar, config_path=config, output_root=tmp_path / "out",
            repo_root=tmp_path, official=False, run_id="bad_handoff",
            created_utc="2030-01-02T03:04:05+00:00",
        )


def test_radar_mutation_during_reference_load_is_detected(tmp_path: Path) -> None:
    radar, config, _ = _fixture(tmp_path)
    target = radar / "radar_runner_config.json"

    def mutating_loader(path: Path):
        frame = scoring.load_reference_strict(path)
        target.write_text(target.read_text(encoding="utf-8") + " ", encoding="utf-8")
        return frame

    with pytest.raises(scoring.ScoreContractError, match="changed during scoring"):
        scoring.execute_score(
            radar_input=radar, config_path=config, output_root=tmp_path / "out",
            repo_root=tmp_path, official=False, run_id="mutation",
            created_utc="2030-01-02T03:04:05+00:00", reference_loader=mutating_loader,
        )
    assert not (tmp_path / "out").exists()


def test_output_provenance_taint_and_pickle_free_evidence(tmp_path: Path) -> None:
    result = _execute(tmp_path)
    run_meta = json.loads((result.output_dir / "run_meta.json").read_text(encoding="utf-8"))
    assert run_meta["radar_input_unchanged"] is True
    assert run_meta["classification"] == "exploratory_non_frozen"
    assert run_meta["promotion_eligible"] is False
    assert run_meta["outcome_threshold"] is None
    assert set(run_meta["reference_hashes"]) == {"c1"}
    assert set(run_meta["source_hashes"]) == set(run_meta["source_identities"])
    assert all(
        identity["sha256"] == run_meta["source_hashes"][name]
        and identity["size_bytes"] > 0
        and identity["resolved_path"]
        for name, identity in run_meta["source_identities"].items()
    )
    assert run_meta["actual_invocation"]
    with np.load(result.output_dir / "paired_evidence.npz", allow_pickle=False) as evidence:
        assert str(evidence["classification"]) == "exploratory_non_frozen"
        assert evidence["promotion_eligible"].item() is False
        assert evidence["error_bpm"].dtype == np.float64
    snapshot = json.loads(
        (result.output_dir / "radar_input_snapshot.json").read_text(encoding="utf-8")
    )
    assert snapshot["before_digest_sha256"] == snapshot["after_digest_sha256"]


def test_failure_census_and_pair_margin_diagnostics_are_separate_by_arm(
    tmp_path: Path,
) -> None:
    result = _execute(tmp_path)
    failures = json.loads(
        (result.output_dir / "failure_census.json").read_text(encoding="utf-8")
    )
    margins = json.loads(
        (result.output_dir / "pair_margin_diagnostics.json").read_text(encoding="utf-8")
    )
    assert {row["arm_id"] for row in failures} == set(ARMS)
    assert all(row["failure_reason"] == "valid" and row["n_windows"] == 2 for row in failures)
    assert {row["arm_id"] for row in margins} == set(ARMS)
    assert all(row["n_finite"] == 2 and row["median_db"] == pytest.approx(0.115) for row in margins)


def test_deterministic_fixture_outputs(tmp_path: Path) -> None:
    first = _execute(tmp_path / "first")
    second = _execute(tmp_path / "second")
    for relative in (
        "scored_rows.json", "summaries.json", "failure_census.json",
        "pair_margin_diagnostics.json", "paired_evidence.npz",
    ):
        assert _hash(first.output_dir / relative) == _hash(second.output_dir / relative)


def test_aggregate_never_pools_protocols_locks_or_arms() -> None:
    rows = []
    for protocol in ("natural", "paced"):
        for lock in ("lock_a", "lock_b"):
            for arm in ARMS:
                rows.append(
                    {
                        "metric_eligible": True, "capture_id": protocol + lock,
                        "subject": "same_subject", "protocol": protocol,
                        "lock_estimand_id": lock, "method": arm, "vital": "hr",
                        "loading_delta": ARMS[arm], "error_bpm": 1.0,
                        "joint_scored": True, "radar_valid": True,
                        "reference_admitted": True,
                    }
                )
    summaries = scoring.aggregate_summaries(rows)
    subject = [row for row in summaries if row["stratum_level"] == "subject"]
    assert len(subject) == 2 * 2 * 2  # protocol x lock x arm remain distinct
    assert all(row["n_windows"] == 1 for row in subject)


def test_cli_has_no_path_or_threshold_overrides(monkeypatch) -> None:
    monkeypatch.setattr(
        scoring, "execute_score", lambda **_kwargs: pytest.fail("override reached scoring")
    )
    for forbidden in ("--radar-input", "--config", "--output-root", "--threshold"):
        with pytest.raises(SystemExit):
            scoring.main(["--execute-scoring", forbidden, "x"])


def test_official_dirty_guard_refuses_before_radar_or_reference_access(monkeypatch) -> None:
    monkeypatch.setattr(
        scoring, "_official_dirty_paths", lambda _root: ["?? scripts/m9_kotte_score.py"]
    )
    monkeypatch.setattr(
        scoring,
        "snapshot_directory",
        lambda _root: pytest.fail("dirty scorer reached immutable radar input"),
    )
    with pytest.raises(scoring.ScoreContractError, match="must be committed"):
        scoring.execute_score()


def test_executed_scorer_source_is_bound_before_and_after_scoring(
    tmp_path: Path, monkeypatch
) -> None:
    """A mid-run source change must not be bound only to the post-change bytes.

    The radar input, reference, metadata, and config already use before/after identity
    checks.  The executed scorer needs the same two-sided provenance contract: a single
    end-of-run hash cannot establish which source bytes the imported code executed.
    """
    scorer_path = Path(scoring.__file__).resolve()
    original_hash = scoring.sha256_file
    scorer_hash_calls: list[str] = []

    def counting_hash(path: Path) -> str:
        if path.resolve() == scorer_path:
            scorer_hash_calls.append(original_hash(path))
        return original_hash(path)

    monkeypatch.setattr(scoring, "sha256_file", counting_hash)
    _execute(tmp_path)
    assert len(scorer_hash_calls) >= 2, (
        "the scorer source must be hashed before reference access and re-hashed after "
        "scoring so a source race fails closed"
    )
    assert len(set(scorer_hash_calls)) == 1


def test_source_snapshot_completes_before_radar_and_reference_access(
    tmp_path: Path, monkeypatch
) -> None:
    source_snapshot_complete = False
    events: list[str] = []
    original_source_snapshot = scoring.snapshot_load_bearing_sources
    original_radar_snapshot = scoring.snapshot_directory

    def source_snapshot(paths):
        nonlocal source_snapshot_complete
        result = original_source_snapshot(paths)
        source_snapshot_complete = True
        events.append("source")
        return result

    def radar_snapshot(path):
        assert source_snapshot_complete
        events.append("radar")
        return original_radar_snapshot(path)

    def reference_loader(path: Path):
        assert source_snapshot_complete
        events.append("reference")
        return scoring.load_reference_strict(path)

    monkeypatch.setattr(scoring, "snapshot_load_bearing_sources", source_snapshot)
    monkeypatch.setattr(scoring, "snapshot_directory", radar_snapshot)
    _execute(tmp_path, reference_loader=reference_loader)
    assert events[0] == "source"
    assert events.index("source") < events.index("radar") < events.index("reference")
    assert events[-1] == "source"


def test_source_snapshot_rejects_missing_directory_symlink_and_in_hash_change(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")
    with pytest.raises(scoring.ScoreContractError, match="unavailable or unhashable"):
        scoring.snapshot_load_bearing_sources({"missing": tmp_path / "missing.py"})
    with pytest.raises(scoring.ScoreContractError, match="not a regular file"):
        scoring.snapshot_load_bearing_sources({"directory": tmp_path})

    original_is_symlink = Path.is_symlink

    def synthetic_symlink(path: Path) -> bool:
        return path.resolve() == source.resolve() or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", synthetic_symlink)
    with pytest.raises(scoring.ScoreContractError, match="is a symlink"):
        scoring.snapshot_load_bearing_sources({"symlink": source})
    monkeypatch.setattr(Path, "is_symlink", original_is_symlink)

    original_hash = scoring.sha256_file

    def mutating_hash(path: Path) -> str:
        result = original_hash(path)
        path.write_text("value = 123456\n", encoding="utf-8")
        return result

    monkeypatch.setattr(scoring, "sha256_file", mutating_hash)
    with pytest.raises(scoring.ScoreContractError, match="changed while hashing"):
        scoring.snapshot_load_bearing_sources({"changing": source})


def test_source_identity_change_between_snapshots_fails_before_output(
    tmp_path: Path, monkeypatch
) -> None:
    radar, config, _ = _fixture(tmp_path)
    original_snapshot = scoring.snapshot_load_bearing_sources
    snapshot_calls = 0

    def changed_second_snapshot(paths):
        nonlocal snapshot_calls
        snapshot_calls += 1
        result = original_snapshot(paths)
        if snapshot_calls == 2:
            result = deepcopy(result)
            result["scorer"]["sha256"] = "0" * 64
        return result

    monkeypatch.setattr(scoring, "snapshot_load_bearing_sources", changed_second_snapshot)
    with pytest.raises(scoring.ScoreContractError, match="source identity changed"):
        scoring.execute_score(
            radar_input=radar,
            config_path=config,
            output_root=tmp_path / "outputs",
            repo_root=tmp_path,
            official=False,
            run_id="source_race",
            created_utc="2030-01-02T03:04:05+00:00",
        )
    assert snapshot_calls == 2
    assert not (tmp_path / "outputs").exists()


def test_source_disappearance_at_second_snapshot_fails_before_output(
    tmp_path: Path, monkeypatch
) -> None:
    radar, config, _ = _fixture(tmp_path)
    original_snapshot = scoring.snapshot_load_bearing_sources
    snapshot_calls = 0

    def unavailable_second_snapshot(paths):
        nonlocal snapshot_calls
        snapshot_calls += 1
        if snapshot_calls == 2:
            raise scoring.ScoreContractError("load-bearing source is unavailable: scorer")
        return original_snapshot(paths)

    monkeypatch.setattr(
        scoring, "snapshot_load_bearing_sources", unavailable_second_snapshot
    )
    with pytest.raises(scoring.ScoreContractError, match="source is unavailable"):
        scoring.execute_score(
            radar_input=radar,
            config_path=config,
            output_root=tmp_path / "outputs",
            repo_root=tmp_path,
            official=False,
            run_id="source_disappeared",
            created_utc="2030-01-02T03:04:05+00:00",
        )
    assert snapshot_calls == 2
    assert not (tmp_path / "outputs").exists()


@pytest.mark.parametrize(
    ("target", "reason"),
    [
        ("reference", "reference changed during scoring"),
        ("metadata", "capture metadata changed during scoring"),
        ("config", "scoring config changed during scoring"),
    ],
)
def test_synthetic_scoring_input_mutations_still_fail_before_output(
    tmp_path: Path, target: str, reason: str
) -> None:
    radar, config, reference = _fixture(tmp_path)

    def mutating_loader(path: Path):
        frame = scoring.load_reference_strict(path)
        mutation_path = {
            "reference": reference,
            "metadata": tmp_path / "capture" / "run_metadata.json",
            "config": config,
        }[target]
        mutation_path.write_text(
            mutation_path.read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )
        return frame

    with pytest.raises(scoring.ScoreContractError, match=reason):
        scoring.execute_score(
            radar_input=radar,
            config_path=config,
            output_root=tmp_path / "outputs",
            repo_root=tmp_path,
            official=False,
            run_id=f"mutated_{target}",
            created_utc="2030-01-02T03:04:05+00:00",
            reference_loader=mutating_loader,
        )
    assert not (tmp_path / "outputs").exists()
