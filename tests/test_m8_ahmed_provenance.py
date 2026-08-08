"""M2 authority and transitive scientific-provenance controls.

All mutation cases use throwaway repositories.  No capture or reference-data path is
opened by these tests.

The test-attestation section at the end never lets ``build_test_attestation`` reach a
real ``subprocess.run``: its attested file set includes this file, so a real run would
spawn pytest inside pytest.  Every case injects ``FakePytestRunner`` from conftest.
"""
from __future__ import annotations

import ast
import copy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from conftest import FakePytestRunner, fake_input_digest  # noqa: E402
from src.m8.ahmed_provenance import (  # noqa: E402
    SourceManifest,
    build_source_manifest,
    build_test_attestation,
    conda_explicit,
    load_source_manifest,
    scoped_paths,
    validate_test_attestation,
    verify_source_manifest,
)

# The gate-critical M3/M4 runner, serialization, preflight and scoring tests, plus the
# re-audited respiration test and the numeric lock-selector test, written out literally.
# These paths are the scientific expectation: if any of them leaves the manifest closure or
# the attested command, a synthetic gate could be blessed without the tests that actually
# validate the paired runner, the evidence serializer, the strict preflight, the scorer, or
# the bin the seven arms all share.  Deriving this list from `_REQUIRED_ARTIFACTS` /
# `_ATTESTED_TEST_FILES` would make it tautological.
GATE_CRITICAL_TEST_PATHS = (
    "tests/test_m4_estimator_runner.py",
    "tests/test_m4_estimator_suites.py",
    "tests/test_m4_evidence_serialization.py",
    "tests/test_m4_paired_runner.py",
    "tests/test_m4_preflight_strict.py",
    "tests/test_m4_registry_and_scoring.py",
    "tests/test_m4_manifest.py",
    "tests/test_m8_ahmed_score.py",
    "tests/test_m8_ahmed_score_independent.py",
    "tests/test_respiration.py",
    # `src/warmup_select.py` produces `current_production_rerun_lock`, whose numeric bin is
    # shared by all seven arms in every cell.  This is the only test file that checks the
    # selector's numeric behaviour — the range-FFT energy ranking, the eligibility
    # threshold, and the settle-skip window — as opposed to its identity and wiring.
    "tests/test_live_demo_warmup_helpers.py",
)

# Pytest imports this control file implicitly rather than collecting it as a test module.
# Its bytes still govern which attested skips are possible and therefore must be bound.
ATTESTATION_CONTROL_INPUT_PATHS = ("tests/conftest.py",)

#: The nodes whose `optional_artifact_or_mode` skip the attestation may record, written out
#: literally per file.  All six assert against sweep/scoring artifacts under the gitignored
#: `results/` tree, so whether they execute is a property of the operator's machine.
#: Declaring them by marker is what makes a clean clone able to produce a valid attestation;
#: inferring "one skip is fine" from a count could not.
DECLARED_OPTIONAL_ARTIFACT_NODES = {
    "tests/test_m8_ahmed_all_bins.py": (
        "test_sweep_declares_masimo_unopened_in_its_metadata",
        "test_row_count_is_windows_times_bins_times_arms",
        "test_all_bins_of_a_window_share_one_decoded_frame_span",
        "test_all_arms_of_a_cell_share_one_extracted_phase",
        "test_ceiling_is_never_worse_than_the_production_lock",
        "test_scoring_run_records_the_hr_interpretation_limit",
    ),
}

#: The nodes that may carry `real_data`, written out literally per file.  This marker is far
#: sharper than the optional-artifact one: `tests/conftest.py` force-skips every `real_data`
#: node unless `M8_RUN_REAL_DATA_TESTS=1`, and the attestation builder pins that variable to
#: "0".  One added decorator therefore removes a test from the gate's EXECUTED coverage on
#: every machine, permanently, while the attestation still records it as "declared".  Only
#: the frozen eight-capture preflight is authorized to be opt-in that way.
DECLARED_REAL_DATA_NODES = {
    "tests/test_m4_registry_and_scoring.py": (
        "test_registry_preflight_matches_all_frozen_radar_inputs",
    ),
}

#: Marker -> permitted nodes, in the order the attestation collects the classes.
DECLARED_SKIP_NODES_BY_MARKER = {
    "real_data": DECLARED_REAL_DATA_NODES,
    "optional_artifact_or_mode": DECLARED_OPTIONAL_ARTIFACT_NODES,
}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _small_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "main.py").write_text("from src.dependency import VALUE\n", encoding="utf-8")
    (repo / "src" / "dependency.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "src" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "CLAUDE.md").write_text("test rules\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "initial")
    return repo


def test_canonical_closure_contains_every_correction_plan_dependency_class():
    relative = {path.relative_to(REPO_ROOT).as_posix() for path in scoped_paths()}
    required = {
        "src/radar_io.py",                         # decoder / capture geometry
        "src/respiration.py",                      # phase extraction
        "src/m4/window_grid.py",                   # window grid
        "src/warmup_select.py",                    # lock selector
        "src/window_pipeline.py",                  # production estimator
        "src/m4/outcome.py",                       # production classifier
        "src/m8/ahmed_fig8.py",                    # Ahmed accumulation core
        "src/m8/ahmed_transfer.py",                # Layer B core
        "src/m4/estimator_runner.py",              # runner
        "src/m4/estimator_scoring.py",             # scorer
        "src/masimo.py",                           # reference parser
        "src/comparator.py",                       # frozen comparators
        "src/m4/bundle.py",                        # serializers / bundle
        "experiments/m8_ahmed_transfer/capture_registry.yaml",
        "experiments/m8_ahmed_fig8/layer_a_profiles.yaml",
        "experiments/m8_ahmed_transfer/layer_b_profiles.yaml",
        "plans/m8_ahmed_correction_plan.md",
        "plans/m8_step1b_ahmed_transfer.md",
        "plans/m8_step1b_ahmed_transfer_addendum_a.md",
        "scripts/m8_ahmed_transfer.py",
        "scripts/m8_ahmed_score.py",
        "tests/test_m8_ahmed_transfer.py",
        "tests/test_m8_ahmed_gate.py",
        "tests/test_m8_ahmed_provenance.py",
    }
    assert not required - relative


def test_canonical_closure_contains_every_gate_critical_m3_m4_and_respiration_test():
    """The manifest closure must cover the tests that validate the gate itself.

    Naming only the M1/M2-era paths leaves the M3 paired runner, the evidence
    serializer, the strict preflight, and the M4 scorer outside the frozen source
    identity: those files could then be edited (or deleted) without invalidating an
    authoritative gate.  `tests/test_respiration.py` is included because phase
    extraction is a scientific dependency of every Ahmed arm.
    """
    closure = {path.relative_to(REPO_ROOT).as_posix() for path in scoped_paths()}
    missing = sorted(set(GATE_CRITICAL_TEST_PATHS) - closure)
    assert missing == [], f"gate-critical tests absent from the source closure: {missing}"


def test_built_manifest_hash_binds_every_gate_critical_test_file():
    """Presence in the closure is not enough; each file must carry a content hash."""
    manifest = build_source_manifest()
    hashed = {entry["path"]: entry["sha256"] for entry in manifest.entries}
    for path in GATE_CRITICAL_TEST_PATHS:
        assert path in hashed, f"{path} is not hash-bound by the source manifest"
        assert len(hashed[path]) == 64


def test_every_manifest_entry_has_hash_commit_and_explicit_git_status():
    manifest = build_source_manifest()
    assert manifest.entries
    for entry in manifest.entries:
        assert set(entry) == {
            "path",
            "size_bytes",
            "sha256",
            "source_commit",
            "tracked",
            "dirty",
            "untracked",
            "status",
        }
        assert not Path(entry["path"]).is_absolute()
        assert len(entry["sha256"]) == 64
        assert entry["status"] in {"clean", "dirty", "untracked"}
        assert type(entry["tracked"]) is bool
        assert type(entry["dirty"]) is bool
        assert type(entry["untracked"]) is bool
        assert entry["untracked"] is (not entry["tracked"])
        assert entry["dirty"] is (entry["status"] == "dirty")
        assert entry["untracked"] is (entry["status"] == "untracked")
        if entry["tracked"]:
            assert entry["source_commit"] == manifest.git_commit
        else:
            assert entry["source_commit"] is None


def test_clean_manifest_is_deterministic_and_verifies(tmp_path):
    repo = _small_repo(tmp_path)
    first = build_source_manifest(repo)
    second = build_source_manifest(repo)
    assert first == second
    paths = [entry["path"] for entry in first.entries]
    assert paths == sorted(paths)
    assert first.manifest_sha256 == second.manifest_sha256
    assert first.promotion_eligible is True
    verify_source_manifest(first, repo, require_promotion_eligible=True)


@pytest.mark.parametrize(
    "approval_path",
    [
        "experiments/m8_ahmed_transfer/authorizations/approved.yaml",
        "experiments/m8_ahmed_transfer/continuations/approved.yaml",
    ],
)
def test_approval_only_commit_preserves_a_clean_byte_identical_gate_manifest(
    tmp_path, approval_path
):
    repo = _small_repo(tmp_path)
    gate_manifest = build_source_manifest(repo)
    approval = repo / approval_path
    approval.parent.mkdir(parents=True)
    approval.write_text("approval_id: fixture\n", encoding="utf-8")
    _git(repo, "add", approval_path)
    _git(repo, "commit", "-q", "-m", "freeze post-gate approval")

    current = build_source_manifest(repo)
    assert current.git_commit != gate_manifest.git_commit
    assert current.manifest_sha256 == gate_manifest.manifest_sha256
    assert current.promotion_eligible is True
    verify_source_manifest(
        gate_manifest,
        repo,
        require_promotion_eligible=True,
    )


def test_multiple_linear_approval_only_commits_preserve_the_gate_manifest(tmp_path):
    repo = _small_repo(tmp_path)
    gate_manifest = build_source_manifest(repo)
    for directory, name in (
        ("continuations", "continued.yaml"),
        ("authorizations", "authorized.yaml"),
    ):
        relative_path = f"experiments/m8_ahmed_transfer/{directory}/{name}"
        document = repo / relative_path
        document.parent.mkdir(parents=True, exist_ok=True)
        document.write_text(f"approval_id: {name}\n", encoding="utf-8")
        _git(repo, "add", relative_path)
        _git(repo, "commit", "-q", "-m", f"freeze {name}")

    verify_source_manifest(gate_manifest, repo, require_promotion_eligible=True)


@pytest.mark.parametrize("terminal_change", ["empty", "deleted"])
def test_approval_yaml_must_be_a_nonempty_blob_in_every_commit(
    tmp_path, terminal_change
):
    repo = _small_repo(tmp_path)
    gate_manifest = build_source_manifest(repo)
    relative_path = "experiments/m8_ahmed_transfer/authorizations/approved.yaml"
    approval = repo / relative_path
    approval.parent.mkdir(parents=True)
    approval.write_text("approval_id: fixture\n", encoding="utf-8")
    _git(repo, "add", relative_path)
    _git(repo, "commit", "-q", "-m", "freeze post-gate approval")

    if terminal_change == "empty":
        approval.write_bytes(b"")
    else:
        approval.unlink()
    _git(repo, "add", relative_path)
    _git(repo, "commit", "-q", "-m", f"make approval {terminal_change}")

    with pytest.raises(ValueError, match="nonempty blob.*approved.yaml"):
        verify_source_manifest(gate_manifest, repo, require_promotion_eligible=True)


def test_merge_history_cannot_use_the_approval_only_exception(tmp_path):
    repo = _small_repo(tmp_path)
    gate_manifest = build_source_manifest(repo)
    original_branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    _git(repo, "checkout", "-q", "-b", "approval-side")
    authorization_path = (
        "experiments/m8_ahmed_transfer/authorizations/authorized.yaml"
    )
    authorization = repo / authorization_path
    authorization.parent.mkdir(parents=True)
    authorization.write_text("approval_id: authorized\n", encoding="utf-8")
    _git(repo, "add", authorization_path)
    _git(repo, "commit", "-q", "-m", "freeze authorization")

    _git(repo, "checkout", "-q", original_branch)
    continuation_path = (
        "experiments/m8_ahmed_transfer/continuations/continued.yaml"
    )
    continuation = repo / continuation_path
    continuation.parent.mkdir(parents=True)
    continuation.write_text("approval_id: continued\n", encoding="utf-8")
    _git(repo, "add", continuation_path)
    _git(repo, "commit", "-q", "-m", "freeze continuation")
    _git(repo, "merge", "-q", "--no-ff", "approval-side", "-m", "merge approvals")

    with pytest.raises(ValueError, match="not a linear approval-only sequence"):
        verify_source_manifest(gate_manifest, repo, require_promotion_eligible=True)


def test_unrelated_post_gate_commit_is_not_the_approved_commit_exception(tmp_path):
    repo = _small_repo(tmp_path)
    gate_manifest = build_source_manifest(repo)
    (repo / "README.md").write_text("unrelated documentation\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "unrelated post-gate commit")

    current = build_source_manifest(repo)
    assert current.manifest_sha256 == gate_manifest.manifest_sha256
    assert current.promotion_eligible is True
    with pytest.raises(ValueError, match="not approval-only.*README.md"):
        verify_source_manifest(gate_manifest, repo, require_promotion_eligible=True)


def test_intervening_unrelated_commit_before_approval_still_fails(tmp_path):
    repo = _small_repo(tmp_path)
    gate_manifest = build_source_manifest(repo)
    (repo / "README.md").write_text("intervening documentation\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "intervening unrelated commit")

    approval_path = "experiments/m8_ahmed_transfer/authorizations/approved.yaml"
    approval = repo / approval_path
    approval.parent.mkdir(parents=True)
    approval.write_text("approval_id: fixture\n", encoding="utf-8")
    _git(repo, "add", approval_path)
    _git(repo, "commit", "-q", "-m", "freeze post-gate approval")

    with pytest.raises(ValueError, match="not approval-only.*README.md"):
        verify_source_manifest(gate_manifest, repo, require_promotion_eligible=True)


def test_reverted_intervening_unrelated_commit_before_approval_still_fails(tmp_path):
    """An unrelated commit is not erased from history by restoring the gate tree."""
    repo = _small_repo(tmp_path)
    gate_manifest = build_source_manifest(repo)
    readme = repo / "README.md"
    readme.write_text("intervening documentation\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "intervening unrelated commit")
    readme.unlink()
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "revert unrelated content")

    approval_path = "experiments/m8_ahmed_transfer/authorizations/approved.yaml"
    approval = repo / approval_path
    approval.parent.mkdir(parents=True)
    approval.write_text("approval_id: fixture\n", encoding="utf-8")
    _git(repo, "add", approval_path)
    _git(repo, "commit", "-q", "-m", "freeze post-gate approval")

    current = build_source_manifest(repo)
    assert current.manifest_sha256 == gate_manifest.manifest_sha256
    with pytest.raises(ValueError, match="not approval-only.*README.md"):
        verify_source_manifest(gate_manifest, repo, require_promotion_eligible=True)


def test_non_ancestor_current_head_cannot_use_the_approval_exception(tmp_path):
    repo = _small_repo(tmp_path)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "gate checkpoint")
    gate_manifest = build_source_manifest(repo)

    _git(repo, "checkout", "-q", "--detach", "HEAD^")
    approval_path = "experiments/m8_ahmed_transfer/authorizations/approved.yaml"
    approval = repo / approval_path
    approval.parent.mkdir(parents=True)
    approval.write_text("approval_id: fixture\n", encoding="utf-8")
    _git(repo, "add", approval_path)
    _git(repo, "commit", "-q", "-m", "sibling approval commit")

    current = build_source_manifest(repo)
    assert current.manifest_sha256 == gate_manifest.manifest_sha256
    with pytest.raises(ValueError, match="gate commit is not an ancestor"):
        verify_source_manifest(gate_manifest, repo, require_promotion_eligible=True)


@pytest.mark.parametrize(
    "disallowed_path",
    [
        "experiments/m8_ahmed_transfer/approved.yaml",
        "experiments/m8_ahmed_transfer/authorizations/nested/approved.yaml",
        "experiments/m8_ahmed_transfer/authorizations/approved.yml",
        "experiments/m8_ahmed_transfer/continuations/approved.YAML",
    ],
)
def test_approval_exception_path_scope_is_exact(tmp_path, disallowed_path):
    repo = _small_repo(tmp_path)
    gate_manifest = build_source_manifest(repo)
    disallowed = repo / disallowed_path
    disallowed.parent.mkdir(parents=True)
    disallowed.write_text("approval_id: fixture\n", encoding="utf-8")
    _git(repo, "add", disallowed_path)
    _git(repo, "commit", "-q", "-m", "wrong approval path")

    with pytest.raises(ValueError, match="not approval-only"):
        verify_source_manifest(gate_manifest, repo, require_promotion_eligible=True)


def test_approval_commit_cannot_include_any_other_file(tmp_path):
    repo = _small_repo(tmp_path)
    gate_manifest = build_source_manifest(repo)
    approval_path = "experiments/m8_ahmed_transfer/authorizations/approved.yaml"
    approval = repo / approval_path
    approval.parent.mkdir(parents=True)
    approval.write_text("approval_id: fixture\n", encoding="utf-8")
    (repo / "README.md").write_text("bundled unrelated file\n", encoding="utf-8")
    _git(repo, "add", approval_path, "README.md")
    _git(repo, "commit", "-q", "-m", "mixed approval commit")

    with pytest.raises(ValueError, match="not approval-only.*README.md"):
        verify_source_manifest(gate_manifest, repo, require_promotion_eligible=True)


def test_deleted_authorization_yaml_in_post_gate_history_fails(tmp_path):
    """Every intervening commit must add/modify usable approval evidence, not delete it."""
    repo = _small_repo(tmp_path)
    gate_manifest = build_source_manifest(repo)
    approval_path = "experiments/m8_ahmed_transfer/authorizations/approved.yaml"
    approval = repo / approval_path
    approval.parent.mkdir(parents=True)
    approval.write_text("approval_id: fixture\n", encoding="utf-8")
    _git(repo, "add", approval_path)
    _git(repo, "commit", "-q", "-m", "freeze post-gate approval")
    approval.unlink()
    _git(repo, "add", approval_path)
    _git(repo, "commit", "-q", "-m", "delete post-gate approval")

    with pytest.raises(ValueError, match="not approval-only|delet|empty|zero"):
        verify_source_manifest(gate_manifest, repo, require_promotion_eligible=True)


def test_transient_zero_byte_authorization_yaml_in_post_gate_history_fails(tmp_path):
    """A later valid edit cannot sanitize an earlier zero-byte authorization commit."""
    repo = _small_repo(tmp_path)
    gate_manifest = build_source_manifest(repo)
    approval_path = "experiments/m8_ahmed_transfer/authorizations/approved.yaml"
    approval = repo / approval_path
    approval.parent.mkdir(parents=True)
    approval.write_bytes(b"")
    _git(repo, "add", approval_path)
    _git(repo, "commit", "-q", "-m", "commit empty authorization")
    approval.write_text("approval_id: fixture\n", encoding="utf-8")
    _git(repo, "add", approval_path)
    _git(repo, "commit", "-q", "-m", "populate authorization")

    with pytest.raises(ValueError, match="not approval-only|empty|zero"):
        verify_source_manifest(gate_manifest, repo, require_promotion_eligible=True)


def test_multiple_linear_approval_only_commits_are_accepted(tmp_path):
    repo = _small_repo(tmp_path)
    gate_manifest = build_source_manifest(repo)
    continuation_path = "experiments/m8_ahmed_transfer/continuations/continued.yaml"
    continuation = repo / continuation_path
    continuation.parent.mkdir(parents=True)
    continuation.write_text("continuation_id: fixture\n", encoding="utf-8")
    _git(repo, "add", continuation_path)
    _git(repo, "commit", "-q", "-m", "freeze continuation")
    authorization_path = "experiments/m8_ahmed_transfer/authorizations/approved.yaml"
    authorization = repo / authorization_path
    authorization.parent.mkdir(parents=True)
    authorization.write_text("approval_id: fixture\n", encoding="utf-8")
    _git(repo, "add", authorization_path)
    _git(repo, "commit", "-q", "-m", "freeze authorization")

    current = build_source_manifest(repo)
    assert current.manifest_sha256 == gate_manifest.manifest_sha256
    verify_source_manifest(gate_manifest, repo, require_promotion_eligible=True)


def test_merge_in_post_gate_approval_history_is_rejected(tmp_path):
    repo = _small_repo(tmp_path)
    gate_manifest = build_source_manifest(repo)
    gate_branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _git(repo, "branch", "approval-side")

    authorization_path = "experiments/m8_ahmed_transfer/authorizations/approved.yaml"
    authorization = repo / authorization_path
    authorization.parent.mkdir(parents=True)
    authorization.write_text("approval_id: fixture\n", encoding="utf-8")
    _git(repo, "add", authorization_path)
    _git(repo, "commit", "-q", "-m", "freeze authorization")

    _git(repo, "checkout", "-q", "approval-side")
    continuation_path = "experiments/m8_ahmed_transfer/continuations/continued.yaml"
    continuation = repo / continuation_path
    continuation.parent.mkdir(parents=True)
    continuation.write_text("continuation_id: fixture\n", encoding="utf-8")
    _git(repo, "add", continuation_path)
    _git(repo, "commit", "-q", "-m", "freeze continuation")
    _git(repo, "checkout", "-q", gate_branch)
    _git(repo, "merge", "-q", "--no-ff", "approval-side", "-m", "merge approvals")

    with pytest.raises(ValueError, match="not a linear approval-only sequence"):
        verify_source_manifest(gate_manifest, repo, require_promotion_eligible=True)


@pytest.mark.parametrize("closure_change", ["omitted", "extra"])
def test_approval_exception_does_not_hide_closure_mismatch(tmp_path, closure_change):
    repo = _small_repo(tmp_path)
    gate_manifest = build_source_manifest(repo)
    approval_path = "experiments/m8_ahmed_transfer/continuations/approved.yaml"
    approval = repo / approval_path
    approval.parent.mkdir(parents=True)
    approval.write_text("approval_id: fixture\n", encoding="utf-8")
    _git(repo, "add", approval_path)
    _git(repo, "commit", "-q", "-m", "freeze post-gate continuation")

    if closure_change == "omitted":
        changed_entries = tuple(
            entry
            for entry in gate_manifest.entries
            if entry["path"] != "src/dependency.py"
        )
    else:
        extra_entry = {
            **gate_manifest.entries[-1],
            "path": "src/not_in_scientific_closure.py",
        }
        changed_entries = (*gate_manifest.entries, extra_entry)
    malformed_gate = replace(gate_manifest, entries=changed_entries)

    with pytest.raises(ValueError, match="scientific dependency closure mismatch"):
        verify_source_manifest(malformed_gate, repo, require_promotion_eligible=True)


def test_scoped_source_commit_after_gate_still_fails(tmp_path):
    repo = _small_repo(tmp_path)
    gate_manifest = build_source_manifest(repo)
    (repo / "src" / "dependency.py").write_text(
        "VALUE = 999  # changed science\n", encoding="utf-8"
    )
    _git(repo, "add", "src/dependency.py")
    _git(repo, "commit", "-q", "-m", "change scoped science")

    with pytest.raises(ValueError, match="not approval-only|content or Git status changed"):
        verify_source_manifest(gate_manifest, repo, require_promotion_eligible=True)


@pytest.mark.parametrize("working_tree_change", ["dirty", "untracked"])
def test_dirty_or_untracked_scoped_change_after_approval_commit_fails(
    tmp_path, working_tree_change
):
    repo = _small_repo(tmp_path)
    gate_manifest = build_source_manifest(repo)
    approval_path = "experiments/m8_ahmed_transfer/authorizations/approved.yaml"
    approval = repo / approval_path
    approval.parent.mkdir(parents=True)
    approval.write_text("approval_id: fixture\n", encoding="utf-8")
    _git(repo, "add", approval_path)
    _git(repo, "commit", "-q", "-m", "freeze post-gate approval")

    if working_tree_change == "dirty":
        (repo / "src" / "dependency.py").write_text(
            "VALUE = 2  # dirty\n", encoding="utf-8"
        )
    else:
        (repo / "src" / "new_science.py").write_text("FACTOR = 60\n", encoding="utf-8")
        (repo / "src" / "main.py").write_text(
            "from src.dependency import VALUE\nfrom src.new_science import FACTOR\n",
            encoding="utf-8",
        )

    expected_error = (
        "content or Git status changed"
        if working_tree_change == "dirty"
        else "scientific dependency closure mismatch"
    )
    with pytest.raises(ValueError, match=expected_error):
        verify_source_manifest(gate_manifest, repo, require_promotion_eligible=True)


def test_branch_change_is_not_hidden_by_the_approval_commit_exception(tmp_path):
    repo = _small_repo(tmp_path)
    gate_manifest = build_source_manifest(repo)
    approval_path = "experiments/m8_ahmed_transfer/authorizations/approved.yaml"
    approval = repo / approval_path
    approval.parent.mkdir(parents=True)
    approval.write_text("approval_id: fixture\n", encoding="utf-8")
    _git(repo, "add", approval_path)
    _git(repo, "commit", "-q", "-m", "freeze post-gate approval")
    _git(repo, "branch", "-m", "different-branch")

    with pytest.raises(ValueError, match="source branch mismatch"):
        verify_source_manifest(gate_manifest, repo, require_promotion_eligible=True)


def test_ast_closure_resolves_absolute_relative_and_from_package_aliases(tmp_path):
    repo = _small_repo(tmp_path)
    (repo / "src" / "package").mkdir()
    (repo / "src" / "package" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "absolute_dep.py").write_text("ABS = 1\n", encoding="utf-8")
    (repo / "src" / "relative_dep.py").write_text("REL = 2\n", encoding="utf-8")
    (repo / "src" / "package_alias_dep.py").write_text("ALIAS = 3\n", encoding="utf-8")
    (repo / "src" / "package" / "helper.py").write_text("HELP = 4\n", encoding="utf-8")
    (repo / "src" / "orphan.py").write_text("NOT_IMPORTED = 5\n", encoding="utf-8")
    (repo / "src" / "main.py").write_text(
        "import src.absolute_dep as absolute\n"
        "from . import relative_dep as relative\n"
        "from src import package_alias_dep as renamed\n"
        "from .package import helper as package_helper\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "import forms")

    manifest = build_source_manifest(
        repo,
        entry_points=("src/main.py",),
        required_artifacts=("CLAUDE.md",),
    )
    paths = {entry["path"] for entry in manifest.entries}
    assert {
        "src/main.py",
        "src/__init__.py",
        "src/absolute_dep.py",
        "src/relative_dep.py",
        "src/package_alias_dep.py",
        "src/package/__init__.py",
        "src/package/helper.py",
    } <= paths
    assert "src/orphan.py" not in paths


def test_untracked_external_pdf_hash_does_not_gate_promotion(tmp_path):
    repo = _small_repo(tmp_path)
    pdf = (
        repo
        / "literature"
        / "ref_papers"
        / "discovering_the_unseen_radar_vitals"
        / (
            "Discovering_the_Unseen_Radar-Based_Estimation_of_Heartbeat_Breathing_Rate_and_"
            "Underlying_Muscle_Expansion_Without_Probes.pdf"
        )
    )
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"external paper fixture")

    manifest = build_source_manifest(repo)
    assert manifest.promotion_eligible is True
    assert len(manifest.reference_entries) == 1
    reference = manifest.reference_entries[0]
    assert reference["path"] == pdf.relative_to(repo).as_posix()
    assert reference["tracked"] is False
    assert reference["gates_promotion"] is False
    verify_source_manifest(manifest, repo, require_promotion_eligible=True)


@pytest.mark.parametrize(
    "malformation",
    [
        "extra_entry_key",
        "invalid_entry_hash",
        "non_boolean_entry_status",
        "non_string_manifest_hash",
    ],
)
def test_persisted_manifest_rejects_malformed_strict_schema(tmp_path, malformation):
    document = build_source_manifest(_small_repo(tmp_path)).to_dict()
    if malformation == "extra_entry_key":
        document["entries"][0]["ignored"] = "must not be accepted"
    elif malformation == "invalid_entry_hash":
        document["entries"][0]["sha256"] = "not-a-sha256"
    elif malformation == "non_boolean_entry_status":
        document["entries"][0]["tracked"] = 1
    elif malformation == "non_string_manifest_hash":
        document["manifest_sha256"] = 123
    else:  # pragma: no cover - parametrization is exhaustive
        raise AssertionError(malformation)

    with pytest.raises(ValueError):
        SourceManifest.from_dict(document)


def test_dirty_and_imported_untracked_dependencies_are_ineligible(tmp_path):
    repo = _small_repo(tmp_path)
    (repo / "src" / "dependency.py").write_text("VALUE = 2  # dirty\n", encoding="utf-8")
    dirty = build_source_manifest(repo)
    assert dirty.promotion_eligible is False
    assert "src/dependency.py" in dirty.scoped_dirty
    # The flag alone is not the contract: the verifier must refuse the manifest.
    with pytest.raises(ValueError, match="promotion-eligible"):
        verify_source_manifest(dirty, repo, require_promotion_eligible=True)

    (repo / "src" / "new_science.py").write_text("FACTOR = 60\n", encoding="utf-8")
    (repo / "src" / "main.py").write_text(
        "from src.dependency import VALUE\nfrom src.new_science import FACTOR\n",
        encoding="utf-8",
    )
    untracked = build_source_manifest(repo)
    paths = {entry["path"] for entry in untracked.entries}
    assert "src/new_science.py" in paths
    assert "src/new_science.py" in untracked.scoped_untracked
    with pytest.raises(ValueError, match="promotion-eligible"):
        verify_source_manifest(untracked, repo, require_promotion_eligible=True)


def test_omitting_an_imported_scientific_dependency_fails(tmp_path):
    repo = _small_repo(tmp_path)
    manifest = build_source_manifest(
        repo,
        entry_points=("src/main.py",),
        required_artifacts=("CLAUDE.md",),
    )
    omitted = replace(
        manifest,
        entries=tuple(
            entry for entry in manifest.entries if entry["path"] != "src/dependency.py"
        ),
    )
    with pytest.raises(ValueError, match="closure mismatch.*src/dependency.py"):
        verify_source_manifest(
            omitted,
            repo,
            entry_points=("src/main.py",),
            required_artifacts=("CLAUDE.md",),
        )


def test_tampering_after_manifest_creation_fails(tmp_path):
    repo = _small_repo(tmp_path)
    manifest = build_source_manifest(repo)
    (repo / "src" / "dependency.py").write_text("VALUE = 999  # tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="content or Git status changed"):
        verify_source_manifest(manifest, repo)


def test_content_change_is_detected_by_hash_not_merely_by_dirty_status(tmp_path):
    """Committing the edit restores a clean tree; the content hash must still change.

    Without this, the tamper test above could be passing only because the edited file
    became dirty, leaving content-hash detection unproven.
    """
    repo = _small_repo(tmp_path)
    original = build_source_manifest(repo)
    (repo / "src" / "dependency.py").write_text("VALUE = 999  # tampered\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "commit the tampered dependency")

    rebuilt = build_source_manifest(repo)
    assert rebuilt.scoped_dirty == () and rebuilt.scoped_untracked == ()
    assert rebuilt.promotion_eligible is True

    before = {entry["path"]: entry["sha256"] for entry in original.entries}
    after = {entry["path"]: entry["sha256"] for entry in rebuilt.entries}
    assert before["src/dependency.py"] != after["src/dependency.py"]
    assert original.manifest_sha256 != rebuilt.manifest_sha256
    with pytest.raises(ValueError, match="content or Git status changed"):
        verify_source_manifest(original, repo)


def test_persisted_ineligible_manifest_cannot_flip_eligibility_true(tmp_path):
    repo = _small_repo(tmp_path)
    (repo / "src" / "dependency.py").write_text(
        "VALUE = 2  # dirty\n", encoding="utf-8"
    )
    manifest = build_source_manifest(repo)
    assert manifest.promotion_eligible is False

    tampered = manifest.to_dict()
    tampered["promotion_eligible"] = True
    with pytest.raises(ValueError, match="promotion eligibility mismatch"):
        verify_source_manifest(
            tampered, repo, require_promotion_eligible=True
        )


def test_persisted_branch_tampering_fails(tmp_path):
    repo = _small_repo(tmp_path)
    manifest = build_source_manifest(repo)
    tampered = manifest.to_dict()
    tampered["git_branch"] = "tampered-branch"
    with pytest.raises(ValueError, match="source branch mismatch"):
        verify_source_manifest(tampered, repo)


@pytest.mark.parametrize(
    "field, expected_message",
    [
        ("manifest_sha256", "source manifest digest mismatch"),
        ("git_commit", "source commit mismatch"),
        ("entry_sha256", "content or Git status changed"),
        ("entry_status", "content or Git status changed"),
    ],
)
def test_hash_commit_and_status_tampering_fail_closed(tmp_path, field, expected_message):
    repo = _small_repo(tmp_path)
    tampered = build_source_manifest(repo).to_dict()
    if field == "manifest_sha256":
        tampered[field] = "0" * 64
    elif field == "git_commit":
        tampered[field] = "0" * 40
    elif field == "entry_sha256":
        tampered["entries"][0]["sha256"] = "0" * 64
    elif field == "entry_status":
        tampered["entries"][0]["status"] = "dirty"
        tampered["entries"][0]["dirty"] = True
    else:  # pragma: no cover - parametrization is exhaustive
        raise AssertionError(field)
    with pytest.raises(ValueError, match=expected_message):
        verify_source_manifest(tampered, repo)


def test_persisted_manifest_verifies_its_own_digest_without_the_working_tree(tmp_path):
    """Loading alone must reject a manifest inconsistent with its own entry list.

    ``load_source_manifest`` is a public entry point, and the M5 step that reopens
    bundles may use it without immediately rebuilding from the working tree.
    """
    repo = _small_repo(tmp_path)
    document = build_source_manifest(repo).to_dict()
    document["entries"][0]["sha256"] = "0" * 64
    path = tmp_path / "source_manifest.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="source manifest digest mismatch"):
        SourceManifest.from_dict(document)
    with pytest.raises(ValueError, match="source manifest digest mismatch"):
        load_source_manifest(path)


def test_missing_named_dependency_fails_instead_of_being_skipped(tmp_path):
    repo = _small_repo(tmp_path)
    with pytest.raises(FileNotFoundError, match="missing.yaml"):
        build_source_manifest(
            repo,
            entry_points=("src/main.py",),
            required_artifacts=("missing.yaml",),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Strict focused-test attestation.
#
# The attestation is what stops a synthetic gate being blessed by a test run that
# is red, partly skipped, or that predates a source edit.  Every case below injects
# a fake command runner; the real ``subprocess.run`` is never reached, because this
# very file is part of the attested set.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def forbid_nested_pytest(monkeypatch):
    """Hard stop: spawning pytest from inside a test body fails instead of recursing.

    ``platform.platform()`` legitimately shells out on Windows, so only a pytest
    invocation is blocked here; everything else is passed through untouched.
    """
    real_popen = subprocess.Popen

    def _guarded(args, *rest, **kwargs):
        flattened = " ".join(args) if isinstance(args, (list, tuple)) else str(args)
        if "pytest" in flattened:
            raise AssertionError(
                "a real pytest process was spawned; build_test_attestation must "
                "always be given a fake command_runner"
            )
        return real_popen(args, *rest, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", _guarded)


def _attested_files_in(argv: list[str]) -> list[str]:
    """Recover the attested file list from one frozen pytest command line."""
    start = argv.index("pytest") + 1
    return [item for item in argv[start:] if not item.startswith("-")]


def test_builder_emits_a_valid_attestation_bound_to_its_source_manifest(
    fake_source_manifest, attested_node_ids, tmp_path, forbid_nested_pytest
):
    runner = FakePytestRunner(attested_node_ids)

    attestation = build_test_attestation(
        fake_source_manifest,
        root=tmp_path,
        command_runner=runner,
        temporary_parent=tmp_path,
    )

    # Exactly four commands were issued, in the repository root, and nothing else ran.
    assert [call["cwd"] for call in runner.calls] == [str(tmp_path)] * 4
    assert "--collect-only" in runner.calls[0]["argv"]
    assert "--collect-only" in runner.calls[1]["argv"]
    assert "real_data" in runner.calls[1]["argv"]
    assert "--collect-only" in runner.calls[2]["argv"]
    assert "optional_artifact_or_mode" in runner.calls[2]["argv"]
    assert "--collect-only" not in runner.calls[3]["argv"]
    # The executed command must ask for the machine-readable report the skip identities are
    # read from, and it must land outside the repository.
    report_options = [
        item for item in runner.calls[3]["argv"] if item.startswith("--junitxml=")
    ]
    assert len(report_options) == 1
    report_path = Path(report_options[0].removeprefix("--junitxml="))
    assert tmp_path in report_path.parents
    assert REPO_ROOT not in report_path.parents

    assert attestation["schema_version"] == 2
    assert attestation["status"] == "passed"
    assert attestation["source_manifest_sha256"] == fake_source_manifest.manifest_sha256
    assert attestation["collected_count"] == len(attested_node_ids)
    assert attestation["passed_count"] == len(attested_node_ids)
    assert attestation["failed_count"] == 0
    assert attestation["skipped_count"] == 0
    assert attestation["skipped_pytest_node_ids"] == []
    assert attestation["declared_skip_pytest_node_ids"] == {
        "real_data": [],
        "optional_artifact_or_mode": [],
    }
    assert attestation["ordered_pytest_node_ids"] == attested_node_ids
    assert [command["purpose"] for command in attestation["ordered_commands"]] == [
        "collect",
        "collect_declared_real_data",
        "collect_declared_optional_artifact_or_mode",
        "execute",
    ]
    # A declared-class collection legitimately exits 5 when no node carries that marker.
    assert [command["exit_status"] for command in attestation["ordered_commands"]] == [
        0,
        5,
        5,
        0,
    ]

    # The frozen command must actually run the gate-critical tests, and every one of
    # them must be hash-bound in the attestation's input closure.
    for command in attestation["ordered_commands"]:
        attested = _attested_files_in(command["argv"])
        assert not set(GATE_CRITICAL_TEST_PATHS) - set(attested)
    assert not set(GATE_CRITICAL_TEST_PATHS) - set(attestation["input_sha256"])
    assert not set(ATTESTATION_CONTROL_INPUT_PATHS) - set(
        attestation["input_sha256"]
    )
    for command in attestation["ordered_commands"]:
        assert not set(ATTESTATION_CONTROL_INPUT_PATHS) & set(command["argv"])
    for path, digest in attestation["input_sha256"].items():
        assert digest == fake_input_digest(path)

    validate_test_attestation(attestation, fake_source_manifest)


def test_the_report_path_is_the_only_variable_part_of_the_frozen_commands(
    fake_source_manifest, valid_test_attestation
):
    """The junit XML lands in a temporary directory, so its path cannot be frozen.

    It is normalised away before the commands are compared.  Nothing else may be: the argv
    is what pins which tests the gate actually ran.
    """
    document = copy.deepcopy(valid_test_attestation)
    execute_command = document["ordered_commands"][-1]
    execute_command["argv"] = [
        "--junitxml=/a/different/temporary/directory/report.xml"
        if item.startswith("--junitxml=")
        else item
        for item in execute_command["argv"]
    ]

    validate_test_attestation(document, fake_source_manifest)

    execute_command["argv"] = [item for item in execute_command["argv"] if item != "-q"]
    with pytest.raises(ValueError, match="ordered command identity mismatch"):
        validate_test_attestation(document, fake_source_manifest)


def test_builder_output_validates_against_the_manifest_as_a_plain_mapping(
    fake_source_manifest, valid_test_attestation
):
    """The gate persists JSON, so the validator must accept a mapping manifest too."""
    document = {
        "manifest_sha256": fake_source_manifest.manifest_sha256,
        "entries": [dict(entry) for entry in fake_source_manifest.entries],
    }

    validate_test_attestation(valid_test_attestation, document)


# ── Stale attestations ───────────────────────────────────────────────────────

def test_attestation_is_stale_when_the_source_manifest_digest_moves_on(
    fake_source_manifest, valid_test_attestation
):
    """A source edit rolls the manifest digest; the old attestation must not carry over."""
    edited = replace(fake_source_manifest, manifest_sha256="9" * 64)

    with pytest.raises(ValueError, match="source-manifest identity mismatch"):
        validate_test_attestation(valid_test_attestation, edited)


def test_attestation_is_stale_when_an_attested_test_file_changed(
    fake_source_manifest, valid_test_attestation
):
    """The scientifically important case: the tests ran before the source was edited.

    Only the per-file hash moves here — the manifest digest is left alone — so the
    per-path ``input_sha256`` binding is the sole thing that can catch it.
    """
    edited_path = "tests/test_m4_paired_runner.py"
    entries = tuple(
        {**entry, "sha256": "0" * 64} if entry["path"] == edited_path else entry
        for entry in fake_source_manifest.entries
    )
    edited = replace(fake_source_manifest, entries=entries)

    with pytest.raises(ValueError, match=f"input hash mismatch for {edited_path}"):
        validate_test_attestation(valid_test_attestation, edited)


# ── Field-level tampering ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "mutation, expected_message",
    [
        ("flipped_status", "status/schema is not passed"),
        ("nonzero_failed_count", "failed or skipped"),
        ("nonzero_skipped_count", "failed or skipped"),
        ("passed_count_below_collected_nodes", "node/count reconciliation"),
        ("collected_count_above_node_ids", "node/count reconciliation"),
        ("altered_attested_file_set", "ordered command identity mismatch"),
        ("duplicated_node_id", "node IDs are malformed"),
        ("corrupted_input_hash", "input hash mismatch"),
        ("dropped_input_hash", "input-hash closure is incomplete"),
        ("nonzero_command_exit_status", "command did not pass"),
        ("timezone_naive_timestamp", "timezone-aware"),
        ("extra_top_level_field", "malformed top-level schema"),
    ],
)
def test_tampered_attestation_fields_each_fail_closed(
    fake_source_manifest, valid_test_attestation, mutation, expected_message
):
    document = copy.deepcopy(valid_test_attestation)
    if mutation == "flipped_status":
        document["status"] = "failed"
    elif mutation == "nonzero_failed_count":
        document["failed_count"] = 1
    elif mutation == "nonzero_skipped_count":
        document["skipped_count"] = 1
    elif mutation == "passed_count_below_collected_nodes":
        document["passed_count"] -= 1
    elif mutation == "collected_count_above_node_ids":
        document["collected_count"] += 1
    elif mutation == "altered_attested_file_set":
        dropped = "tests/test_m4_paired_runner.py"
        for command in document["ordered_commands"]:
            command["argv"] = [item for item in command["argv"] if item != dropped]
    elif mutation == "duplicated_node_id":
        document["ordered_pytest_node_ids"][-1] = document["ordered_pytest_node_ids"][0]
    elif mutation == "corrupted_input_hash":
        document["input_sha256"]["tests/test_m4_preflight_strict.py"] = "0" * 64
    elif mutation == "dropped_input_hash":
        del document["input_sha256"]["tests/test_m4_preflight_strict.py"]
    elif mutation == "nonzero_command_exit_status":
        document["ordered_commands"][1]["exit_status"] = 1
    elif mutation == "timezone_naive_timestamp":
        document["completed_utc"] = document["completed_utc"].replace("+00:00", "")
    elif mutation == "extra_top_level_field":
        document["operator_note"] = "looks fine to me"
    else:  # pragma: no cover - parametrization is exhaustive
        raise AssertionError(mutation)

    with pytest.raises(ValueError, match=expected_message):
        validate_test_attestation(document, fake_source_manifest)


# ── The builder refuses to attest anything but a fully green run ─────────────

def test_builder_refuses_when_the_execution_command_fails(
    fake_source_manifest, attested_node_ids, tmp_path, forbid_nested_pytest
):
    runner = FakePytestRunner(attested_node_ids, execute_returncode=1)

    with pytest.raises(RuntimeError, match="execute command failed"):
        build_test_attestation(
            fake_source_manifest, root=tmp_path, command_runner=runner
        )


def test_builder_refuses_and_stops_when_collection_fails(
    fake_source_manifest, attested_node_ids, tmp_path, forbid_nested_pytest
):
    runner = FakePytestRunner(attested_node_ids, collect_returncode=2)

    with pytest.raises(RuntimeError, match="collect command failed"):
        build_test_attestation(
            fake_source_manifest, root=tmp_path, command_runner=runner
        )
    # A failed collection must abort before the execution command is even issued.
    assert len(runner.calls) == 1


def test_builder_refuses_a_run_with_failures(
    fake_source_manifest, attested_node_ids, tmp_path, forbid_nested_pytest
):
    passed = len(attested_node_ids) - 2
    runner = FakePytestRunner(
        attested_node_ids, execute_stdout=f"{passed} passed, 2 failed in 1.00s\n"
    )

    with pytest.raises(RuntimeError, match="did not pass every collected node"):
        build_test_attestation(
            fake_source_manifest, root=tmp_path, command_runner=runner
        )


def test_builder_refuses_an_undeclared_skip_even_when_the_rest_passed(
    fake_source_manifest, attested_node_ids, tmp_path, forbid_nested_pytest
):
    """An undeclared skip silently removes scientific coverage; it cannot produce a gate.

    Nothing in this attested set carries a declared skip marker, so the permitted-skip set
    is empty and the one reported skip is unaccounted for.  The summary is ``N-1 passed,
    1 skipped`` out of N collected, which reconciles arithmetically: the run is rejected by
    the undeclared-skip rule alone, not by a count mismatch.
    """
    undeclared_skip = attested_node_ids[-1]
    runner = FakePytestRunner(
        attested_node_ids,
        skipped_node_ids=[undeclared_skip],
        execute_stdout=f"{len(attested_node_ids) - 1} passed, 1 skipped in 1.00s\n",
    )

    with pytest.raises(RuntimeError, match="not declared by any of") as raised:
        build_test_attestation(
            fake_source_manifest, root=tmp_path, command_runner=runner
        )
    assert undeclared_skip in str(raised.value)


# ── Declared skips are the only permitted exception ──────────────────────────

def test_the_declared_skip_classes_are_exactly_the_two_frozen_marker_classes():
    """Two marker classes, and one collection command each, written out literally.

    `real_data` covers frozen real capture/reference inputs the synthetic gate must not
    touch; `optional_artifact_or_mode` covers nodes whose subject is an optional artifact
    under the gitignored `results/` tree, or an optional configuration mode.  Every other
    skip must refuse to produce a gate, so widening this set is a scientific change and
    has to be a deliberate edit here as well.
    """
    from src.m8.ahmed_provenance import (  # noqa: WPS436 - frozen contract under test
        _ATTESTATION_COMMAND_PURPOSES,
        _DECLARED_SKIP_MARKERS,
    )

    assert _DECLARED_SKIP_MARKERS == ("real_data", "optional_artifact_or_mode")
    # Every declared class must be collected explicitly, between the full collection and
    # the executed run.
    assert _ATTESTATION_COMMAND_PURPOSES == (
        "collect",
        "collect_declared_real_data",
        "collect_declared_optional_artifact_or_mode",
        "execute",
    )


def _nodes_carrying_marker(path: str, marker: str) -> tuple[str, ...]:
    """Names of every definition in one committed test file decorated with `marker`.

    `ast.walk` rather than `tree.body`, so a method nested inside a test class is seen;
    both function kinds, so an `async def` test cannot hide a marker; and `ClassDef` too,
    because a marker on a test class silently marks every method it contains.  A class name
    appearing here will not match the function names in the literal dicts, which is the
    intent: marking a whole class must be an explicit, reviewable edit as well.
    """
    tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"), filename=path)
    decorator_text = f"pytest.mark.{marker}"
    definition_types = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    return tuple(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, definition_types)
        and any(
            ast.unparse(decorator) == decorator_text for decorator in node.decorator_list
        )
    )


def _module_level_pytestmark_text(path: str) -> str | None:
    """The source of a module-level ``pytestmark = ...`` in one test file, or None."""
    tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"), filename=path)
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "pytestmark":
                return ast.unparse(node)
    return None


def test_the_declared_skip_markers_appear_on_exactly_the_declared_nodes(attestation_inputs):
    """Both directions of the permitted-skip set, checked statically on every attested file.

    The missing direction is the clean-clone guarantee: a node that skips where an optional
    `results/` artifact is absent must carry its marker, or the builder sees an undeclared
    skip and refuses to emit any gate.

    The extra direction is its dual, and is the sharper of the two.  Adding
    `@pytest.mark.real_data` to any attested node removes it from the gate's executed
    coverage on EVERY machine — `tests/conftest.py` force-skips the marker and the builder
    pins `M8_RUN_REAL_DATA_TESTS=0` — while the attestation still records it as declared and
    still emits a gate.  Widening the permitted-skip set must therefore cost an explicit
    edit to the literal dicts above, which a reviewer sees.
    """
    attested_test_files, _config_inputs = attestation_inputs

    for marker, declared_by_file in DECLARED_SKIP_NODES_BY_MARKER.items():
        for path in declared_by_file:
            assert path in attested_test_files, (
                f"{path} declares a {marker} skip but is not attested, so declaring it is moot"
            )

    observed: dict[str, dict[str, tuple[str, ...]]] = {
        marker: {} for marker in DECLARED_SKIP_NODES_BY_MARKER
    }
    for path in attested_test_files:
        for marker in DECLARED_SKIP_NODES_BY_MARKER:
            marked_nodes = _nodes_carrying_marker(path, marker)
            if marked_nodes:
                observed[marker][path] = marked_nodes

    assert observed == DECLARED_SKIP_NODES_BY_MARKER

    # A module-level `pytestmark` applies the marker to every node in the file, so it is the
    # same widening by a route no decorator walk can see.
    for path in attested_test_files:
        assert _module_level_pytestmark_text(path) is None, (
            f"{path} sets a module-level pytestmark, which would mark every node in the "
            "file; declared skips must be per-node so they can be listed literally above"
        )


def test_the_outcome_report_parser_reconstructs_pytest_node_ids(tmp_path):
    """pytest's junit XML names a module and a test, not a node ID; the mapping must be exact.

    All three shapes an attested file can emit are covered: a plain function, a parametrised
    function, and a method inside a test class.  A wrong reconstruction here would make the
    observed skip set disjoint from the declared set and reject every honest run.
    """
    from src.m8.ahmed_provenance import (  # noqa: WPS436 - parser under test
        _skipped_node_ids_from_junit_xml,
    )

    report = tmp_path / "report.xml"
    report.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites><testsuite name="pytest" tests="4" skipped="3">'
        '<testcase classname="tests.test_m4_bundle" name="test_passes" time="0.01" />'
        '<testcase classname="tests.test_m4_bundle" name="test_plain" time="0.01">'
        '<skipped type="pytest.skip" message="no artifact" /></testcase>'
        '<testcase classname="tests.test_m4_bundle" name="test_parametrised[a-1]" time="0.01">'
        '<skipped type="pytest.skip" message="no artifact" /></testcase>'
        '<testcase classname="tests.test_m4_bundle.TestGroup" name="test_method" time="0.01">'
        '<skipped type="pytest.skip" message="no artifact" /></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )

    assert _skipped_node_ids_from_junit_xml(report) == [
        "tests/test_m4_bundle.py::test_plain",
        "tests/test_m4_bundle.py::test_parametrised[a-1]",
        "tests/test_m4_bundle.py::TestGroup::test_method",
    ]


def test_the_outcome_report_parser_rejects_a_skip_outside_the_attested_files(tmp_path):
    """A skip the attested file list cannot explain must abort, not be renamed into it."""
    from src.m8.ahmed_provenance import (  # noqa: WPS436 - parser under test
        _skipped_node_ids_from_junit_xml,
    )

    report = tmp_path / "report.xml"
    report.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites><testsuite name="pytest" tests="1" skipped="1">'
        '<testcase classname="tests.test_somewhere_unattested" name="test_x" time="0.01">'
        '<skipped type="pytest.skip" message="?" /></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="not one of the attested test files"):
        _skipped_node_ids_from_junit_xml(report)


def test_builder_accepts_and_records_declared_real_data_skips(
    fake_source_manifest,
    attested_node_ids,
    declared_real_data_node_ids,
    tmp_path,
    forbid_nested_pytest,
):
    """The frozen real-capture checks are opt-in, so they skip; the gate must say so.

    Recording the identities — not merely a count — is what lets a reader of the
    attestation see exactly which scientific coverage the synthetic gate did not have.
    """
    runner = FakePytestRunner(
        attested_node_ids, real_data_node_ids=declared_real_data_node_ids
    )

    attestation = build_test_attestation(
        fake_source_manifest, root=tmp_path, command_runner=runner
    )

    assert attestation["skipped_pytest_node_ids"] == declared_real_data_node_ids
    assert attestation["declared_skip_pytest_node_ids"] == {
        "real_data": declared_real_data_node_ids,
        "optional_artifact_or_mode": [],
    }
    assert attestation["skipped_count"] == len(declared_real_data_node_ids)
    assert attestation["passed_count"] == len(attested_node_ids) - len(
        declared_real_data_node_ids
    )
    assert attestation["failed_count"] == 0
    # Exact reconciliation: every collected node either passed or is a declared skip.
    assert (
        attestation["passed_count"] + attestation["skipped_count"]
        == attestation["collected_count"]
        == len(attestation["ordered_pytest_node_ids"])
    )
    validate_test_attestation(attestation, fake_source_manifest)


def test_builder_accepts_a_clean_clone_where_every_optional_input_is_absent(
    fake_source_manifest,
    attested_node_ids,
    declared_real_data_node_ids,
    declared_optional_artifact_node_ids,
    tmp_path,
    forbid_nested_pytest,
):
    """The reproducibility case: a machine with no `results/` tree must still attest.

    Both declared classes skip in full here, which is what a clean clone produces.  The
    attestation must be valid and must name every unexercised node, so a reader can see
    that the optional-artifact coverage was absent rather than silently assumed.
    """
    absent_optional_inputs = [
        *declared_real_data_node_ids,
        *declared_optional_artifact_node_ids,
    ]
    runner = FakePytestRunner(
        attested_node_ids,
        real_data_node_ids=declared_real_data_node_ids,
        optional_artifact_node_ids=declared_optional_artifact_node_ids,
    )

    attestation = build_test_attestation(
        fake_source_manifest, root=tmp_path, command_runner=runner
    )

    assert attestation["skipped_pytest_node_ids"] == absent_optional_inputs
    assert attestation["skipped_count"] == len(absent_optional_inputs)
    assert attestation["passed_count"] == len(attested_node_ids) - len(
        absent_optional_inputs
    )
    validate_test_attestation(attestation, fake_source_manifest)


def test_builder_accepts_a_machine_where_a_declared_optional_node_executed(
    fake_source_manifest,
    attested_node_ids,
    declared_optional_artifact_node_ids,
    tmp_path,
    forbid_nested_pytest,
):
    """The other side of the same coin: a declared node that ran is not a problem.

    An operator who has run the optional sweep executes that node, so fewer nodes skip than
    are declared.  The attestation must record what was OBSERVED — one skip, named — rather
    than assuming every declared node skipped.
    """
    executed, skipped = declared_optional_artifact_node_ids
    runner = FakePytestRunner(
        attested_node_ids,
        optional_artifact_node_ids=declared_optional_artifact_node_ids,
        skipped_node_ids=[skipped],
    )

    attestation = build_test_attestation(
        fake_source_manifest, root=tmp_path, command_runner=runner
    )

    assert attestation["skipped_pytest_node_ids"] == [skipped]
    assert attestation["skipped_count"] == 1
    assert executed not in attestation["skipped_pytest_node_ids"]
    assert attestation["declared_skip_pytest_node_ids"][
        "optional_artifact_or_mode"
    ] == declared_optional_artifact_node_ids
    validate_test_attestation(attestation, fake_source_manifest)


def test_builder_refuses_a_compensating_declared_and_undeclared_skip_pair(
    fake_source_manifest,
    attested_node_ids,
    declared_optional_artifact_node_ids,
    tmp_path,
    forbid_nested_pytest,
):
    """The reason the skip identities must be observed, not inferred.

    Two declared nodes exist; one of them executed and one undeclared node skipped instead.
    Every count reconciles — two declared, one observed skip, N-1 passed — so an attestation
    that inferred the skip identities from the marker collection would name the wrong node
    as unexercised coverage and still pass.  Reading the executed run's own report catches
    it.
    """
    undeclared_skip = attested_node_ids[-1]
    assert undeclared_skip not in declared_optional_artifact_node_ids
    runner = FakePytestRunner(
        attested_node_ids,
        optional_artifact_node_ids=declared_optional_artifact_node_ids,
        skipped_node_ids=[undeclared_skip],
    )

    with pytest.raises(RuntimeError, match="not declared by any of") as raised:
        build_test_attestation(
            fake_source_manifest, root=tmp_path, command_runner=runner
        )
    assert undeclared_skip in str(raised.value)


def test_builder_refuses_more_skips_than_the_declared_nodes(
    fake_source_manifest,
    attested_node_ids,
    declared_real_data_node_ids,
    tmp_path,
    forbid_nested_pytest,
):
    """One declared real-data node cannot license a second, undeclared skip."""
    undeclared_skip = attested_node_ids[-1]
    runner = FakePytestRunner(
        attested_node_ids,
        real_data_node_ids=declared_real_data_node_ids,
        skipped_node_ids=[*declared_real_data_node_ids, undeclared_skip],
        execute_stdout=f"{len(attested_node_ids) - 2} passed, 2 skipped in 1.00s\n",
    )

    with pytest.raises(RuntimeError, match="not declared by any of") as raised:
        build_test_attestation(
            fake_source_manifest, root=tmp_path, command_runner=runner
        )
    assert undeclared_skip in str(raised.value)


def test_builder_refuses_a_skip_count_that_disagrees_with_the_executed_report(
    fake_source_manifest,
    attested_node_ids,
    declared_real_data_node_ids,
    tmp_path,
    forbid_nested_pytest,
):
    """The summary line and the machine-readable report must agree on how many skipped."""
    runner = FakePytestRunner(
        attested_node_ids,
        real_data_node_ids=declared_real_data_node_ids,
        skipped_node_ids=declared_real_data_node_ids,
        execute_stdout=f"{len(attested_node_ids) - 2} passed, 2 skipped in 1.00s\n",
    )

    with pytest.raises(RuntimeError, match="did not pass every collected node") as raised:
        build_test_attestation(
            fake_source_manifest, root=tmp_path, command_runner=runner
        )
    assert "skipped=2" in str(raised.value)
    assert "observed_skips=1" in str(raised.value)


def test_builder_refuses_a_real_data_node_outside_the_attested_collection(
    fake_source_manifest, attested_node_ids, tmp_path, forbid_nested_pytest
):
    """The permitted-skip set must be a subset of what the first command collected."""
    runner = FakePytestRunner(
        attested_node_ids,
        real_data_node_ids=["tests/test_somewhere_else.py::test_real_capture"],
    )

    with pytest.raises(RuntimeError, match="absent from the attested collection"):
        build_test_attestation(
            fake_source_manifest, root=tmp_path, command_runner=runner
        )


def test_builder_pins_the_governing_real_data_environment_variable(
    fake_source_manifest, attested_node_ids, tmp_path, forbid_nested_pytest
):
    """Which tests execute must be a property of the attestation, not of the shell.

    Every command receives the same explicit environment, and the recorded value is the
    one the commands actually ran with.
    """
    runner = FakePytestRunner(attested_node_ids)

    attestation = build_test_attestation(
        fake_source_manifest, root=tmp_path, command_runner=runner
    )

    recorded = attestation["m8_run_real_data_tests_env_value"]
    assert recorded == "0"
    assert all(
        call["env"]["M8_RUN_REAL_DATA_TESTS"] == recorded for call in runner.calls
    )


@pytest.mark.parametrize(
    "mutation, expected_message",
    [
        ("skipped_node_outside_collection", "outside the attested collection"),
        ("skipped_list_disagrees_with_count", "failed or skipped"),
        ("unpinned_real_data_environment", "did not pin the governing real-data"),
        ("emptied_declared_class", "no declared class covers"),
        ("dropped_declared_class", "exactly the frozen skip classes"),
        ("invented_declared_class", "exactly the frozen skip classes"),
        ("declared_node_outside_collection", "outside the attested collection"),
    ],
)
def test_tampered_skip_declaration_fails_closed(
    fake_source_manifest,
    attested_node_ids,
    declared_real_data_node_ids,
    tmp_path,
    forbid_nested_pytest,
    mutation,
    expected_message,
):
    document = build_test_attestation(
        fake_source_manifest,
        root=tmp_path,
        command_runner=FakePytestRunner(
            attested_node_ids, real_data_node_ids=declared_real_data_node_ids
        ),
    )
    if mutation == "skipped_node_outside_collection":
        document["skipped_pytest_node_ids"] = ["tests/other.py::test_not_collected"]
    elif mutation == "skipped_list_disagrees_with_count":
        document["skipped_pytest_node_ids"] = []
    elif mutation == "unpinned_real_data_environment":
        document["m8_run_real_data_tests_env_value"] = "1"
    elif mutation == "emptied_declared_class":
        # The recorded skip then has no declaring class, which is exactly the state the
        # marker mechanism exists to reject.
        document["declared_skip_pytest_node_ids"]["real_data"] = []
    elif mutation == "dropped_declared_class":
        del document["declared_skip_pytest_node_ids"]["optional_artifact_or_mode"]
    elif mutation == "invented_declared_class":
        document["declared_skip_pytest_node_ids"]["operator_convenience"] = []
    elif mutation == "declared_node_outside_collection":
        document["declared_skip_pytest_node_ids"]["real_data"] = [
            "tests/other.py::test_not_collected"
        ]
    else:  # pragma: no cover - parametrization is exhaustive
        raise AssertionError(mutation)

    with pytest.raises(ValueError, match=expected_message):
        validate_test_attestation(document, fake_source_manifest)


def test_builder_refuses_a_run_reporting_errors(
    fake_source_manifest, attested_node_ids, tmp_path, forbid_nested_pytest
):
    """A teardown error is reported alongside a full ``N passed``, so only the
    forbidden-outcome rule can reject this run.

    That is what makes the case sharp: ``passed=N`` plus ``skipped=0`` already reconciles
    with the N collected nodes, so the rejection is attributable to the reported error and
    not to a count mismatch.  The message is checked for that attribution.
    """
    runner = FakePytestRunner(
        attested_node_ids,
        execute_stdout=f"{len(attested_node_ids)} passed, 1 error in 1.00s\n",
    )

    with pytest.raises(RuntimeError, match="did not pass every collected node") as raised:
        build_test_attestation(
            fake_source_manifest, root=tmp_path, command_runner=runner
        )
    message = str(raised.value)
    assert f"collected={len(attested_node_ids)}" in message
    assert f"passed={len(attested_node_ids)}" in message
    assert "skipped=0" in message
    assert "other=[('1', 'error')]" in message


def test_builder_refuses_an_empty_or_duplicated_collection(
    fake_source_manifest, attested_node_ids, tmp_path, forbid_nested_pytest
):
    empty = FakePytestRunner([], collect_stdout="no tests ran\n", execute_stdout="\n")
    with pytest.raises(RuntimeError, match="unique ordered pytest node IDs"):
        build_test_attestation(fake_source_manifest, root=tmp_path, command_runner=empty)

    duplicated = FakePytestRunner([attested_node_ids[0], attested_node_ids[0]])
    with pytest.raises(RuntimeError, match="unique ordered pytest node IDs"):
        build_test_attestation(
            fake_source_manifest, root=tmp_path, command_runner=duplicated
        )


def test_builder_refuses_a_source_manifest_missing_an_attestation_input(
    fake_source_manifest, attested_node_ids, tmp_path, forbid_nested_pytest
):
    """The attestation cannot be hash-bound to inputs the manifest never recorded."""
    omitted = "plans/m8_ahmed_correction_plan.md"
    trimmed = replace(
        fake_source_manifest,
        entries=tuple(
            entry for entry in fake_source_manifest.entries if entry["path"] != omitted
        ),
    )

    with pytest.raises(ValueError, match="omitted test-attestation inputs"):
        build_test_attestation(
            trimmed,
            root=tmp_path,
            command_runner=FakePytestRunner(attested_node_ids),
        )


def test_builder_rejects_a_non_manifest_argument(tmp_path, forbid_nested_pytest):
    with pytest.raises(TypeError, match="must be a SourceManifest"):
        build_test_attestation(
            {"manifest_sha256": "1" * 64},
            root=tmp_path,
            command_runner=FakePytestRunner([]),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Authoritative Conda environment lock.
# ─────────────────────────────────────────────────────────────────────────────


def _valid_conda_explicit_text() -> str:
    return (
        "# platform: win-64\n"
        "@EXPLICIT\n"
        "https://conda.anaconda.org/conda-forge/win-64/numpy-2.0.0-test.conda\n"
    )


def test_conda_explicit_uses_configured_executable_and_exact_environment(
    tmp_path, monkeypatch
):
    configured_executable = tmp_path / "configured-conda.exe"
    configured_executable.write_bytes(b"test executable identity only")
    monkeypatch.setenv("CONDA_EXE", str(configured_executable))
    calls = []

    def _runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(
            argv,
            returncode=0,
            stdout=_valid_conda_explicit_text(),
            stderr="",
        )

    output = conda_explicit("research-environment", command_runner=_runner)

    assert output == _valid_conda_explicit_text()
    assert calls == [
        (
            [
                str(configured_executable),
                "list",
                "--explicit",
                "-n",
                "research-environment",
            ],
            {"check": True, "capture_output": True, "text": True},
        )
    ]


def test_conda_explicit_fails_closed_when_configured_executable_is_missing(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("CONDA_EXE", str(tmp_path / "missing-conda.exe"))

    with pytest.raises(RuntimeError, match="CONDA_EXE does not name a file"):
        conda_explicit(command_runner=lambda *_args, **_kwargs: None)


def test_conda_explicit_uses_path_fallback_when_no_executable_is_configured(
    tmp_path, monkeypatch
):
    discovered_executable = tmp_path / "path-conda.exe"
    discovered_executable.write_bytes(b"test executable identity only")
    monkeypatch.delenv("CONDA_EXE", raising=False)
    monkeypatch.setattr(
        "src.m8.ahmed_provenance.shutil.which",
        lambda command: str(discovered_executable) if command == "conda" else None,
    )
    observed_argv = []

    def _runner(argv, **_kwargs):
        observed_argv.append(argv)
        return subprocess.CompletedProcess(
            argv, returncode=0, stdout=_valid_conda_explicit_text(), stderr=""
        )

    conda_explicit(command_runner=_runner)

    assert observed_argv[0][0] == str(discovered_executable)


def test_conda_explicit_fails_closed_when_command_fails(tmp_path, monkeypatch):
    configured_executable = tmp_path / "configured-conda.exe"
    configured_executable.write_bytes(b"test executable identity only")
    monkeypatch.setenv("CONDA_EXE", str(configured_executable))

    def _failing_runner(argv, **_kwargs):
        raise subprocess.CalledProcessError(returncode=2, cmd=argv)

    with pytest.raises(RuntimeError, match="cannot produce authoritative Conda lockfile"):
        conda_explicit(command_runner=_failing_runner)


def test_conda_explicit_rejects_a_nonzero_completed_process(tmp_path, monkeypatch):
    """The injected runner contract must fail closed even if it ignores ``check=True``.

    ``subprocess.run`` raises for this case, but a wrapper or test double can legally
    return a ``CompletedProcess``.  The scientific contract is the exit status, not the
    runner implementation detail.
    """
    configured_executable = tmp_path / "configured-conda.exe"
    configured_executable.write_bytes(b"test executable identity only")
    monkeypatch.setenv("CONDA_EXE", str(configured_executable))

    def _nonzero_runner(argv, **_kwargs):
        return subprocess.CompletedProcess(
            argv,
            returncode=9,
            stdout=_valid_conda_explicit_text(),
            stderr="environment export failed",
        )

    with pytest.raises(RuntimeError, match="cannot produce authoritative Conda lockfile"):
        conda_explicit(command_runner=_nonzero_runner)


@pytest.mark.parametrize(
    "output",
    [
        "",
        "# no explicit marker\nhttps://example.invalid/package.conda\n",
        "@EXPLICIT\n",
        "<unavailable: FileNotFoundError>\n@EXPLICIT\nhttps://example.invalid/x.conda\n",
    ],
)
def test_conda_explicit_rejects_non_lockfile_output(tmp_path, monkeypatch, output):
    configured_executable = tmp_path / "configured-conda.exe"
    configured_executable.write_bytes(b"test executable identity only")
    monkeypatch.setenv("CONDA_EXE", str(configured_executable))

    def _runner(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, returncode=0, stdout=output, stderr="")

    with pytest.raises(RuntimeError, match="conda list --explicit"):
        conda_explicit(command_runner=_runner)
