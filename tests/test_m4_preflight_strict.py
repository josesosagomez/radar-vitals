"""Official-path M3 preflight checks that stay entirely within temporary files.

The strict test attestation is exercised here through the same
``verify_gate_bundle`` entry point the official preflight uses.  Attestation
documents come from the ``valid_test_attestation`` fixture, which drives the real
builder with a fake command runner — no pytest process is ever spawned.
"""
from __future__ import annotations

import copy
from dataclasses import replace
import json
import subprocess
from pathlib import Path
from typing import Mapping

import numpy as np
import pytest
import yaml

from src.m4.bundle import BundleWriter, sha256_bytes, sha256_path, strict_json_bytes
from src.m4.estimator_runner import (
    Authorization,
    CANONICAL_ARM_IDS,
    LOCK_ESTIMANDS,
    REAL_STAGES,
    PreflightError,
    verify_gate_bundle,
    verify_preflight,
    verify_repository_authorization,
)
from src.m8.ahmed_provenance import build_source_manifest, verify_source_manifest
from src.m8.ahmed_transfer import (
    APPROVED_ARM_IDS,
    FREQUENCY_MAPPING_ID,
    LAYER_B_NORMALIZATION,
    PHASE_EXTRACTION_METHOD,
    RATE_MAPPING_ID,
    SCORE_FORMULA_ID,
    SCORE_FUNCTIONAL,
    SUPPORT_RULE_ID,
)


SOURCE_MANIFEST_SHA256 = "1" * 64
GATE_PREDICTION_IDS = (
    "P1_P4_selection_fs37.4199_collision_domain_from_fb_h3",
    "P1_P4_selection_fs37.4199_collision_domain_from_fb_h5",
    "P1_P4_selection_fs37.4199_real_representative_domain_h3",
    "P1_P4_selection_fs37.4199_real_representative_domain_h5",
    "P1_P4_selection_fs20_collision_domain_from_fb_h3",
    "P2_subharmonic_degeneracy_h3",
    "P3_collision_ratio_h3",
    "P3_non_divisor_carries_no_signal_h3",
    "P1_P4_selection_fs20_collision_domain_from_fb_h5",
    "P2_subharmonic_degeneracy_h5",
    "P3_collision_ratio_h5",
    "P3_non_divisor_carries_no_signal_h5",
    "P1_P4_selection_fs20_real_representative_domain_h3",
    "P1_P4_selection_fs20_real_representative_domain_h5",
)


def _source_manifest_document(source_manifest) -> dict:
    """The gate's persisted source manifest, reduced to what the validator binds.

    ``validate_test_attestation`` reads only ``manifest_sha256`` and the per-entry
    ``path``/``sha256`` pairs out of this document, so the fixture records exactly
    those and nothing that could accidentally stand in for a real manifest.
    """
    return {
        "manifest_sha256": source_manifest.manifest_sha256,
        "entries": [
            {"path": entry["path"], "sha256": entry["sha256"]}
            for entry in source_manifest.entries
        ],
    }


def _source_manifest_with_synthetic_authority(source_manifest):
    """Add the one authority input absent from the minimal attestation fixture."""
    path = "plans/m8_step1a_ahmed_reproduction.md"
    if any(entry["path"] == path for entry in source_manifest.entries):
        return source_manifest
    authority_entry = {
        "path": path,
        "size_bytes": 1,
        "sha256": "2" * 64,
        "source_commit": "a" * 40,
        "tracked": True,
        "dirty": False,
        "untracked": False,
        "status": "clean",
    }
    return replace(
        source_manifest,
        entries=(*source_manifest.entries, authority_entry),
    )


def _write_gate(
    tmp_path: Path,
    *,
    include_scientific_payloads: bool,
    test_attestation: Mapping[str, object] | None = None,
    source_manifest_document: Mapping[str, object] | None = None,
    omit_payloads: tuple[str, ...] = (),
    manifest_attestation_sha256: str | None = None,
    provenance_attestation_sha256: str | None = None,
    environment_conda_sha256: str | None = None,
    provenance_conda_sha256: str | None = None,
    provenance_environment_sha256: str | None = None,
    authority_overrides: Mapping[str, object] | None = None,
    source_manifest_sha256: str = SOURCE_MANIFEST_SHA256,
) -> Path:
    writer = BundleWriter(
        stage_root=tmp_path / "synthetic",
        stage="synthetic",
        run_id="strict-gate",
    )
    writer.add_json("draft.json", {"purpose": "portable preflight test"})
    attestation_sha256 = (
        None
        if test_attestation is None
        else sha256_bytes(strict_json_bytes(test_attestation))
    )
    explicit_environment_lock = "# portable test\n@EXPLICIT\nhttps://example.invalid/x.conda\n"
    conda_lock_sha256 = sha256_bytes(explicit_environment_lock.encode("utf-8"))
    environment_document = {
        "environment": "test",
        "conda_explicit_sha256": environment_conda_sha256 or conda_lock_sha256,
    }
    environment_sha256 = sha256_bytes(strict_json_bytes(environment_document))
    if include_scientific_payloads:
        authority = {
            "lock_ids": list(LOCK_ESTIMANDS),
            "production_arm_id": CANONICAL_ARM_IDS[0],
            "ahmed_arm_ids": list(APPROVED_ARM_IDS),
            "harmonic_counts": [3, 5],
            "frequency_mapping_id": FREQUENCY_MAPPING_ID,
            "rate_mapping_id": RATE_MAPPING_ID,
            "normalization": LAYER_B_NORMALIZATION,
            "phase_extraction_method": PHASE_EXTRACTION_METHOD,
        }
        authority.update(dict(authority_overrides or {}))
        payloads: dict[str, object] = {
            "gate.json": {
                "gate_status": "passed",
                "checks": [
                    {"prediction_id": prediction_id, "passed": True}
                    for prediction_id in GATE_PREDICTION_IDS
                ],
                "authority": authority,
            },
            "source_manifest.json": dict(
                source_manifest_document or {"manifest_sha256": SOURCE_MANIFEST_SHA256}
            ),
            "test_attestation.json": test_attestation,
            "environment_attestation.json": environment_document,
            "metrics.json": {"metric": 1.0},
        }
        for name, payload in payloads.items():
            if name in omit_payloads or payload is None:
                continue
            writer.add_json(name, payload)
        if "conda_explicit.txt" not in omit_payloads:
            writer.add_text("conda_explicit.txt", explicit_environment_lock)
        if "resolved_config.yaml" not in omit_payloads:
            writer.add_text("resolved_config.yaml", "{}\n")
        if "evidence.npz" not in omit_payloads:
            writer.add_npz("evidence.npz", {"evidence": np.array([1.0])})
    provenance: dict[str, object] = {"source_manifest_sha256": source_manifest_sha256}
    if include_scientific_payloads:
        provenance["conda_explicit_sha256"] = (
            provenance_conda_sha256 or conda_lock_sha256
        )
        provenance["environment_attestation_sha256"] = (
            provenance_environment_sha256 or environment_sha256
        )
    extra_manifest: dict[str, object] = {"gate_status": "passed"}
    if attestation_sha256 is not None:
        provenance["test_attestation_sha256"] = (
            provenance_attestation_sha256 or attestation_sha256
        )
        extra_manifest["test_attestation_sha256"] = (
            manifest_attestation_sha256 or attestation_sha256
        )
    bundle = writer.finalize(
        status="complete",
        provenance=provenance,
        promotion_eligible=True,
        extra_manifest=extra_manifest,
    )
    return bundle.root


def _authorization() -> Authorization:
    return Authorization(
        authorization_id="real_evaluation_v1",
        gate_manifest_sha256="2" * 64,
        source_manifest_sha256=SOURCE_MANIFEST_SHA256,
        allowed_stages=REAL_STAGES,
        capture_ids=("m1",),
        lock_estimands=LOCK_ESTIMANDS,
        arm_ids=CANONICAL_ARM_IDS,
        approved_by="portable test",
        approved_on="2026-08-08",
    )


def _run_git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


def test_official_gate_requires_complete_scientific_payload_set(tmp_path: Path) -> None:
    gate_dir = _write_gate(tmp_path, include_scientific_payloads=False)

    with pytest.raises(PreflightError, match="scientific payloads"):
        verify_gate_bundle(gate_dir, require_scientific_payloads=True)


def test_complete_official_gate_payload_set_is_accepted(
    tmp_path: Path, valid_test_attestation, fake_source_manifest
) -> None:
    gate_dir = _write_gate(
        tmp_path,
        include_scientific_payloads=True,
        test_attestation=valid_test_attestation,
        source_manifest_document=_source_manifest_document(fake_source_manifest),
        authority_overrides={
            "score_functional": SCORE_FUNCTIONAL,
            "score_formula_id": SCORE_FORMULA_ID,
            "support_rule": SUPPORT_RULE_ID,
        },
    )

    manifest = verify_gate_bundle(
        gate_dir,
        expected_source_manifest_sha256=SOURCE_MANIFEST_SHA256,
        require_scientific_payloads=True,
    )

    assert manifest["gate_status"] == "passed"


@pytest.mark.parametrize(
    "binding, expected_message",
    [
        ("environment_to_conda", "environment attestation does not bind"),
        ("provenance_to_conda", "provenance does not bind conda"),
        ("provenance_to_environment", "provenance does not bind environment"),
    ],
)
def test_official_gate_rejects_tampered_environment_cross_bindings(
    tmp_path: Path,
    valid_test_attestation,
    fake_source_manifest,
    binding: str,
    expected_message: str,
) -> None:
    wrong_digest = "0" * 64
    gate_dir = _write_gate(
        tmp_path,
        include_scientific_payloads=True,
        test_attestation=valid_test_attestation,
        source_manifest_document=_source_manifest_document(fake_source_manifest),
        environment_conda_sha256=(
            wrong_digest if binding == "environment_to_conda" else None
        ),
        provenance_conda_sha256=(
            wrong_digest if binding == "provenance_to_conda" else None
        ),
        provenance_environment_sha256=(
            wrong_digest if binding == "provenance_to_environment" else None
        ),
    )

    with pytest.raises(PreflightError, match=expected_message):
        verify_gate_bundle(
            gate_dir,
            expected_source_manifest_sha256=SOURCE_MANIFEST_SHA256,
            require_scientific_payloads=True,
        )


def test_official_synthetic_runner_builds_a_strictly_valid_gate_nonrecursively(
    tmp_path: Path,
    monkeypatch,
    valid_test_attestation,
    fake_source_manifest,
) -> None:
    """Exercise official bundle construction without recursively spawning pytest."""
    import scripts.m8_ahmed_transfer as cli

    source_manifest = _source_manifest_with_synthetic_authority(fake_source_manifest)
    explicit_environment_lock = (
        "# platform: win-64\n"
        "@EXPLICIT\n"
        "https://conda.anaconda.org/conda-forge/win-64/numpy-test.conda\n"
    )
    monkeypatch.setattr(cli, "build_source_manifest", lambda: source_manifest)
    monkeypatch.setattr(cli, "verify_source_manifest", lambda _source: None)
    monkeypatch.setattr(cli, "conda_explicit", lambda: explicit_environment_lock)

    attested_sources = []

    def _prebuilt_attestation_builder(source):
        attested_sources.append(source)
        return valid_test_attestation

    output_root = tmp_path / "official-synthetic"
    exit_status = cli.run_synthetic(
        output_root,
        publish=False,
        test_attestation_builder=_prebuilt_attestation_builder,
    )

    run_directories = [path for path in output_root.iterdir() if path.is_dir()]
    assert exit_status == 0
    assert attested_sources == [source_manifest]
    assert len(run_directories) == 1
    gate_dir = run_directories[0]
    manifest = verify_gate_bundle(
        gate_dir,
        expected_source_manifest_sha256=source_manifest.manifest_sha256,
        require_scientific_payloads=True,
    )
    assert manifest["gate_status"] == "passed"
    assert (gate_dir / "conda_explicit.txt").read_text(
        encoding="utf-8"
    ) == explicit_environment_lock
    provenance = json.loads((gate_dir / "provenance.json").read_text(encoding="utf-8"))
    environment = json.loads(
        (gate_dir / "environment_attestation.json").read_text(encoding="utf-8")
    )
    conda_lock_sha256 = sha256_bytes((gate_dir / "conda_explicit.txt").read_bytes())
    environment_sha256 = manifest["payloads"]["environment_attestation.json"]["sha256"]
    # Step 1b section 4.3 requires the environment document to identify the lock it
    # describes, and the official provenance to bind both payload identities explicitly.
    assert environment["conda_explicit_sha256"] == conda_lock_sha256
    assert provenance["conda_explicit_sha256"] == conda_lock_sha256
    assert provenance["environment_attestation_sha256"] == environment_sha256


def test_official_synthetic_runner_persists_nothing_when_conda_lock_fails(
    tmp_path: Path,
    monkeypatch,
    valid_test_attestation,
    fake_source_manifest,
) -> None:
    import scripts.m8_ahmed_transfer as cli

    source_manifest = _source_manifest_with_synthetic_authority(fake_source_manifest)
    monkeypatch.setattr(cli, "build_source_manifest", lambda: source_manifest)
    monkeypatch.setattr(cli, "verify_source_manifest", lambda _source: None)

    def _lock_failure():
        raise RuntimeError("cannot produce authoritative Conda lockfile")

    monkeypatch.setattr(cli, "conda_explicit", _lock_failure)
    output_root = tmp_path / "failed-official-synthetic"

    with pytest.raises(RuntimeError, match="authoritative Conda lockfile"):
        cli.run_synthetic(
            output_root,
            publish=False,
            test_attestation_builder=lambda _source: valid_test_attestation,
        )

    assert not output_root.exists()


def test_official_gate_rejects_a_bundle_with_no_test_attestation(
    tmp_path: Path, fake_source_manifest
) -> None:
    """A gate with no attestation payload cannot parent a real stage."""
    gate_dir = _write_gate(
        tmp_path,
        include_scientific_payloads=True,
        test_attestation=None,
        source_manifest_document=_source_manifest_document(fake_source_manifest),
    )

    with pytest.raises(PreflightError, match="test_attestation.json"):
        verify_gate_bundle(
            gate_dir,
            expected_source_manifest_sha256=SOURCE_MANIFEST_SHA256,
            require_scientific_payloads=True,
        )


@pytest.mark.parametrize("status", ["failed", None, True])
def test_official_gate_rejects_failed_or_malformed_test_attestation(
    tmp_path: Path, status: object, valid_test_attestation, fake_source_manifest
) -> None:
    attestation = {**copy.deepcopy(valid_test_attestation), "status": status}
    gate_dir = _write_gate(
        tmp_path,
        include_scientific_payloads=True,
        test_attestation=attestation,
        source_manifest_document=_source_manifest_document(fake_source_manifest),
    )

    with pytest.raises(PreflightError, match="test attestation|tests|passed"):
        verify_gate_bundle(
            gate_dir,
            expected_source_manifest_sha256=SOURCE_MANIFEST_SHA256,
            require_scientific_payloads=True,
        )


def test_official_gate_rejects_a_stale_attestation_after_a_source_edit(
    tmp_path: Path, valid_test_attestation, fake_source_manifest
) -> None:
    """The gate's own source manifest moved on; the recorded test run predates it.

    This is the case that stops a gate being blessed by a test run that happened
    before a scientific source file was edited.
    """
    edited_path = "tests/test_m4_paired_runner.py"
    document = _source_manifest_document(fake_source_manifest)
    for entry in document["entries"]:
        if entry["path"] == edited_path:
            entry["sha256"] = "0" * 64
    gate_dir = _write_gate(
        tmp_path,
        include_scientific_payloads=True,
        test_attestation=valid_test_attestation,
        source_manifest_document=document,
    )

    with pytest.raises(PreflightError, match=f"input hash mismatch for {edited_path}"):
        verify_gate_bundle(
            gate_dir,
            expected_source_manifest_sha256=SOURCE_MANIFEST_SHA256,
            require_scientific_payloads=True,
        )


@pytest.mark.parametrize(
    "mutation, expected_message",
    [
        ("flipped_status", "status/schema is not passed"),
        ("nonzero_failed_count", "failed or skipped"),
        ("nonzero_skipped_count", "failed or skipped"),
        ("passed_count_disagrees", "node/count reconciliation"),
        ("altered_attested_file_set", "ordered command identity mismatch"),
        ("duplicated_node_id", "node IDs are malformed"),
        ("corrupted_input_hash", "input hash mismatch"),
    ],
)
def test_official_gate_rejects_each_tampered_attestation_field(
    tmp_path: Path,
    valid_test_attestation,
    fake_source_manifest,
    mutation: str,
    expected_message: str,
) -> None:
    attestation = copy.deepcopy(valid_test_attestation)
    if mutation == "flipped_status":
        attestation["status"] = "failed"
    elif mutation == "nonzero_failed_count":
        attestation["failed_count"] = 1
    elif mutation == "nonzero_skipped_count":
        attestation["skipped_count"] = 1
    elif mutation == "passed_count_disagrees":
        attestation["passed_count"] -= 1
    elif mutation == "altered_attested_file_set":
        dropped = "tests/test_m4_registry_and_scoring.py"
        for command in attestation["ordered_commands"]:
            command["argv"] = [item for item in command["argv"] if item != dropped]
    elif mutation == "duplicated_node_id":
        attestation["ordered_pytest_node_ids"][-1] = attestation[
            "ordered_pytest_node_ids"
        ][0]
    elif mutation == "corrupted_input_hash":
        attestation["input_sha256"]["tests/test_m4_estimator_runner.py"] = "0" * 64
    else:  # pragma: no cover - parametrization is exhaustive
        raise AssertionError(mutation)

    gate_dir = _write_gate(
        tmp_path,
        include_scientific_payloads=True,
        test_attestation=attestation,
        source_manifest_document=_source_manifest_document(fake_source_manifest),
    )

    with pytest.raises(PreflightError, match=expected_message):
        verify_gate_bundle(
            gate_dir,
            expected_source_manifest_sha256=SOURCE_MANIFEST_SHA256,
            require_scientific_payloads=True,
        )


@pytest.mark.parametrize("unbound", ["manifest", "provenance"])
def test_official_gate_requires_the_attestation_to_be_hash_bound(
    tmp_path: Path, valid_test_attestation, fake_source_manifest, unbound: str
) -> None:
    """A valid-looking attestation still fails unless both records hash it exactly."""
    wrong_digest = "0" * 64
    gate_dir = _write_gate(
        tmp_path,
        include_scientific_payloads=True,
        test_attestation=valid_test_attestation,
        source_manifest_document=_source_manifest_document(fake_source_manifest),
        manifest_attestation_sha256=wrong_digest if unbound == "manifest" else None,
        provenance_attestation_sha256=wrong_digest if unbound == "provenance" else None,
    )

    with pytest.raises(PreflightError, match="not hash-bound by manifest/provenance"):
        verify_gate_bundle(
            gate_dir,
            expected_source_manifest_sha256=SOURCE_MANIFEST_SHA256,
            require_scientific_payloads=True,
        )


@pytest.mark.parametrize(
    "field, wrong_value",
    [
        ("frequency_mapping_id", "pulse_radar_q_equals_2f"),
        ("rate_mapping_id", "rate_equals_30q_bpm"),
        ("normalization", "matrix_eta"),
        ("phase_extraction_method", "mean_phasor"),
        ("score_functional", "magnitude_of_complex_sum"),
        ("score_formula_id", "sum_h_complex_s_hq_divided_by_h"),
        ("support_rule", "h_q_less_than_or_equal_to_nyquist"),
    ],
)
def test_official_gate_rejects_wrong_layer_b_scientific_authority(
    tmp_path: Path,
    field: str,
    wrong_value: str,
    valid_test_attestation,
    fake_source_manifest,
) -> None:
    gate_dir = _write_gate(
        tmp_path,
        include_scientific_payloads=True,
        test_attestation=valid_test_attestation,
        source_manifest_document=_source_manifest_document(fake_source_manifest),
        authority_overrides={field: wrong_value},
    )

    with pytest.raises(PreflightError, match="authority|mapping|normalization|phase"):
        verify_gate_bundle(
            gate_dir,
            expected_source_manifest_sha256=SOURCE_MANIFEST_SHA256,
            require_scientific_payloads=True,
        )


def test_repository_authorization_must_be_committed_and_clean(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    authorization_path = (
        repository
        / "experiments"
        / "m8_ahmed_transfer"
        / "authorizations"
        / "approved.yaml"
    )
    authorization_path.parent.mkdir(parents=True)
    authorization_path.write_text(
        yaml.safe_dump({"authorization_id": "real_evaluation_v1"}),
        encoding="utf-8",
    )
    _run_git(repository, "init")
    _run_git(repository, "config", "user.name", "M3 portable test")
    _run_git(repository, "config", "user.email", "m3-test@example.invalid")
    _run_git(repository, "add", authorization_path.relative_to(repository).as_posix())
    _run_git(repository, "commit", "-m", "add frozen authorization")

    verify_repository_authorization(
        authorization_path,
        _authorization(),
        repository_root=repository,
    )

    authorization_path.write_text(
        yaml.safe_dump({"authorization_id": "changed-after-commit"}),
        encoding="utf-8",
    )
    with pytest.raises(PreflightError, match="dirty or untracked"):
        verify_repository_authorization(
            authorization_path,
            _authorization(),
            repository_root=repository,
        )


def test_real_shape_preflight_accepts_direct_authorization_only_commit(
    tmp_path: Path,
) -> None:
    """Exercise the public source verifier and real preflight in one temporary repo."""
    repository = tmp_path / "repository"
    (repository / "src").mkdir(parents=True)
    (repository / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repository / "src" / "dependency.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repository / "src" / "main.py").write_text(
        "from src.dependency import VALUE\n", encoding="utf-8"
    )
    (repository / "CLAUDE.md").write_text("portable rules\n", encoding="utf-8")
    _run_git(repository, "init", "-q")
    _run_git(repository, "config", "user.name", "M3 portable test")
    _run_git(repository, "config", "user.email", "m3-test@example.invalid")
    _run_git(repository, "add", "-A")
    _run_git(repository, "commit", "-q", "-m", "promotion-eligible gate source")
    gate_source = build_source_manifest(repository)

    gate_dir = _write_gate(
        tmp_path / "gate-artifacts",
        include_scientific_payloads=False,
        source_manifest_sha256=gate_source.manifest_sha256,
    )
    gate_digest = sha256_path(gate_dir / "manifest.json")
    authorization_path = (
        repository
        / "experiments"
        / "m8_ahmed_transfer"
        / "authorizations"
        / "approved.yaml"
    )
    authorization_path.parent.mkdir(parents=True)
    authorization_path.write_text(
        yaml.safe_dump(
            {
                "authorization_id": "real_evaluation_v1",
                "gate_manifest_sha256": gate_digest,
                "source_manifest_sha256": gate_source.manifest_sha256,
                "allowed_stages": list(REAL_STAGES),
                "capture_ids": ["m1"],
                "lock_estimands": list(LOCK_ESTIMANDS),
                "arm_ids": list(CANONICAL_ARM_IDS),
                "approved_by": "portable test",
                "approved_on": "2026-08-08",
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _run_git(
        repository,
        "add",
        authorization_path.relative_to(repository).as_posix(),
    )
    _run_git(repository, "commit", "-q", "-m", "freeze real evaluation authorization")

    current = build_source_manifest(repository)
    assert current.manifest_sha256 == gate_source.manifest_sha256
    assert current.git_branch == gate_source.git_branch
    verify_source_manifest(
        gate_source,
        repository,
        require_promotion_eligible=True,
    )
    result = verify_preflight(
        stage="real-smoke",
        gate_dir=gate_dir,
        authorization_path=authorization_path,
        source_manifest_sha256=gate_source.manifest_sha256,
        authorization_validator=lambda path, authorization: verify_repository_authorization(
            path,
            authorization,
            repository_root=repository,
        ),
    )

    assert result.gate_manifest_sha256 == gate_digest
    assert result.authorization.authorization_id == "real_evaluation_v1"


def test_repository_authorization_rejects_wrong_path(tmp_path: Path) -> None:
    wrong_path = tmp_path / "authorization.yaml"
    wrong_path.write_text("authorization_id: real_evaluation_v1\n", encoding="utf-8")

    with pytest.raises(PreflightError, match="approved repository authorization"):
        verify_repository_authorization(
            wrong_path,
            _authorization(),
            repository_root=tmp_path,
        )


def test_repository_authorization_rejects_untracked_file_at_the_right_path(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    authorization_path = (
        repository
        / "experiments"
        / "m8_ahmed_transfer"
        / "authorizations"
        / "approved.yaml"
    )
    authorization_path.parent.mkdir(parents=True)
    authorization_path.write_text("authorization_id: real_evaluation_v1\n", encoding="utf-8")
    _run_git(repository, "init")

    with pytest.raises(PreflightError, match="not committed"):
        verify_repository_authorization(
            authorization_path,
            _authorization(),
            repository_root=repository,
        )


def test_repository_authorization_rejects_more_than_one_candidate(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    authorization_root = (
        repository / "experiments" / "m8_ahmed_transfer" / "authorizations"
    )
    authorization_root.mkdir(parents=True)
    approved = authorization_root / "approved.yaml"
    second = authorization_root / "second.yml"
    approved.write_text("authorization_id: approved\n", encoding="utf-8")
    second.write_text("authorization_id: second\n", encoding="utf-8")
    _run_git(repository, "init")
    _run_git(repository, "config", "user.name", "M3 portable test")
    _run_git(repository, "config", "user.email", "m3-test@example.invalid")
    _run_git(repository, "add", "experiments/m8_ahmed_transfer/authorizations")
    _run_git(repository, "commit", "-m", "add two authorizations")

    with pytest.raises(PreflightError, match="exactly one"):
        verify_repository_authorization(
            approved,
            _authorization(),
            repository_root=repository,
        )


def test_production_paths_do_not_freeze_an_authorization_id_literal() -> None:
    repository = Path(__file__).resolve().parents[1]
    for path in (
        repository / "src" / "m4" / "estimator_runner.py",
        repository / "scripts" / "m8_ahmed_transfer.py",
    ):
        assert "real_evaluation_v1" not in path.read_text(encoding="utf-8")
