"""Immutable stage-bundle and provenance tests (M8 Step 1b plan sections 4.3, 5.2).

Everything here uses temporary directories and a throwaway Git repository. Nothing touches
`results/`, `data/raw/`, or any real capture.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.m4.bundle import (  # noqa: E402
    BundleWriter,
    new_run_id,
    publish_latest,
    read_manifest,
    sha256_bytes,
    strict_json_bytes,
    verify_bundle,
)
from src.m8.ahmed_provenance import build_source_manifest, scoped_paths  # noqa: E402

PROVENANCE = {"schema_version": 1, "note": "test"}


def _writer(tmp_path: Path, stage: str = "synthetic") -> BundleWriter:
    return BundleWriter(stage_root=tmp_path / stage, stage=stage, run_id=new_run_id("a" * 12))


# ── Strict JSON ──────────────────────────────────────────────────────────────

def test_strict_json_is_sorted_utf8_and_newline_terminated():
    data = strict_json_bytes({"b": 1, "a": "ü"})
    assert data.endswith(b"\n")
    assert data.decode("utf-8").index('"a"') < data.decode("utf-8").index('"b"')


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_strict_json_refuses_non_finite_numbers(value):
    with pytest.raises(ValueError):
        strict_json_bytes({"x": value})


def test_strict_json_accepts_none_for_absent_numerics():
    assert json.loads(strict_json_bytes({"x": None}))["x"] is None


def test_strict_json_converts_numpy_scalars_and_arrays():
    payload = {"a": np.int64(3), "b": np.float64(1.5), "c": np.array([1, 2])}
    loaded = json.loads(strict_json_bytes(payload))
    assert loaded == {"a": 3, "b": 1.5, "c": [1, 2]}


# ── Bundle writing and immutability ──────────────────────────────────────────

def test_finalize_writes_payloads_manifest_and_provenance(tmp_path):
    writer = _writer(tmp_path)
    writer.add_json("gate.json", {"gate_status": "passed"})
    writer.add_npz("evidence.npz", {"x": np.arange(4)})
    bundle = writer.finalize(
        status="complete", provenance=PROVENANCE, promotion_eligible=True
    )
    assert bundle.root.is_dir()
    names = {p.name for p in bundle.root.iterdir()}
    assert names == {"gate.json", "evidence.npz", "provenance.json", "manifest.json"}
    manifest = verify_bundle(bundle.root)
    assert manifest["status"] == "complete"
    assert manifest["stage"] == "synthetic"


def test_manifest_excludes_itself_so_the_structure_is_acyclic(tmp_path):
    writer = _writer(tmp_path)
    writer.add_json("gate.json", {"a": 1})
    bundle = writer.finalize(
        status="complete", provenance=PROVENANCE, promotion_eligible=True
    )
    manifest, digest = read_manifest(bundle.root)
    assert "manifest.json" not in manifest["payloads"]
    assert digest == bundle.manifest_sha256


def test_manifest_digest_is_the_stage_identity(tmp_path):
    writer = _writer(tmp_path)
    writer.add_json("gate.json", {"a": 1})
    bundle = writer.finalize(
        status="complete", provenance=PROVENANCE, promotion_eligible=True
    )
    raw = (bundle.root / "manifest.json").read_bytes()
    assert sha256_bytes(raw) == bundle.manifest_sha256


def test_no_self_referential_bundle_json(tmp_path):
    writer = _writer(tmp_path)
    writer.add_json("gate.json", {"a": 1})
    bundle = writer.finalize(
        status="complete", provenance=PROVENANCE, promotion_eligible=True
    )
    assert not (bundle.root / "bundle.json").exists()


def test_a_finalized_bundle_cannot_be_written_again(tmp_path):
    writer = _writer(tmp_path)
    writer.add_json("gate.json", {"a": 1})
    writer.finalize(status="complete", provenance=PROVENANCE, promotion_eligible=True)
    with pytest.raises(RuntimeError, match="already finalized"):
        writer.add_json("more.json", {"b": 2})
    with pytest.raises(RuntimeError, match="already finalized"):
        writer.finalize(status="complete", provenance=PROVENANCE, promotion_eligible=True)


def test_staging_is_removed_when_finalize_fails(tmp_path):
    writer = _writer(tmp_path)
    writer.add_json("gate.json", {"a": 1})
    with pytest.raises(ValueError):
        # A non-finite float in provenance fails after staging is created.
        writer.finalize(
            status="complete",
            provenance={"x": float("nan")},
            promotion_eligible=True,
        )
    assert not writer.staging.exists()
    assert not writer.destination.exists()


def test_refuses_an_empty_bundle_and_a_running_status(tmp_path):
    with pytest.raises(ValueError, match="empty bundle"):
        _writer(tmp_path).finalize(
            status="complete", provenance=PROVENANCE, promotion_eligible=True
        )
    writer = _writer(tmp_path)
    writer.add_json("a.json", {"a": 1})
    with pytest.raises(ValueError, match="terminal status"):
        writer.finalize(status="running", provenance=PROVENANCE, promotion_eligible=True)


@pytest.mark.parametrize("name", ["manifest.json", "LATEST.json", "provenance.json"])
def test_reserved_payload_names_are_rejected(tmp_path, name):
    writer = _writer(tmp_path)
    if name == "provenance.json":
        writer.add_json(name, {"a": 1})  # accepted here, rejected at finalize
        with pytest.raises(ValueError, match="written by finalize"):
            writer.finalize(
                status="complete", provenance=PROVENANCE, promotion_eligible=True
            )
    else:
        with pytest.raises(ValueError):
            writer.add_json(name, {"a": 1})


def test_payload_names_cannot_escape_the_bundle(tmp_path):
    writer = _writer(tmp_path)
    with pytest.raises(ValueError, match="path separator"):
        writer.add_json("../escape.json", {"a": 1})


def test_verify_detects_tampering(tmp_path):
    writer = _writer(tmp_path)
    writer.add_json("gate.json", {"a": 1})
    bundle = writer.finalize(
        status="complete", provenance=PROVENANCE, promotion_eligible=True
    )
    (bundle.root / "gate.json").write_bytes(b'{"a": 2}\n')
    with pytest.raises(ValueError, match="sha256"):
        verify_bundle(bundle.root)


def test_verify_detects_an_extra_file(tmp_path):
    writer = _writer(tmp_path)
    writer.add_json("gate.json", {"a": 1})
    bundle = writer.finalize(
        status="complete", provenance=PROVENANCE, promotion_eligible=True
    )
    (bundle.root / "sneaky.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="payload set mismatch"):
        verify_bundle(bundle.root)


def test_npz_rejects_object_dtype(tmp_path):
    writer = _writer(tmp_path)
    with pytest.raises(TypeError, match="object dtype"):
        writer.add_npz("evidence.npz", {"x": np.array([{"a": 1}], dtype=object)})


def test_npz_loads_without_pickle(tmp_path):
    writer = _writer(tmp_path)
    writer.add_npz("evidence.npz", {"x": np.arange(3), "y": np.array([1.5, 2.5])})
    bundle = writer.finalize(
        status="complete", provenance=PROVENANCE, promotion_eligible=True
    )
    with np.load(bundle.root / "evidence.npz", allow_pickle=False) as data:
        assert set(data.files) == {"x", "y"}
        assert all(data[k].dtype != object for k in data.files)


# ── LATEST publication is fail-closed ────────────────────────────────────────

def test_latest_lives_outside_the_run_directory(tmp_path):
    writer = _writer(tmp_path)
    writer.add_json("gate.json", {"a": 1})
    bundle = writer.finalize(
        status="complete", provenance=PROVENANCE, promotion_eligible=True
    )
    latest = publish_latest(tmp_path / "synthetic", bundle)
    assert latest.parent == bundle.root.parent
    assert not (bundle.root / "LATEST.json").exists()
    assert json.loads(latest.read_text())["manifest_sha256"] == bundle.manifest_sha256


def test_a_promotion_ineligible_bundle_cannot_publish_latest(tmp_path):
    writer = _writer(tmp_path)
    writer.add_json("gate.json", {"a": 1})
    bundle = writer.finalize(
        status="complete", provenance=PROVENANCE, promotion_eligible=False
    )
    with pytest.raises(ValueError, match="promotion-ineligible"):
        publish_latest(tmp_path / "synthetic", bundle)


@pytest.mark.parametrize("status", ["failed", "incomplete_abandoned"])
def test_a_non_complete_bundle_cannot_publish_latest(tmp_path, status):
    writer = _writer(tmp_path)
    writer.add_json("gate.json", {"a": 1})
    bundle = writer.finalize(
        status=status, provenance=PROVENANCE, promotion_eligible=True
    )
    with pytest.raises(ValueError, match="refusing to publish"):
        publish_latest(tmp_path / "synthetic", bundle)


# ── Source manifest scope and promotion eligibility ──────────────────────────

def test_scope_covers_the_step_1b_modules_and_their_tests():
    relative = {p.relative_to(REPO_ROOT).as_posix() for p in scoped_paths()}
    for expected in (
        "src/m8/ahmed_transfer.py",
        "src/m8/ahmed_synthetic.py",
        "src/m8/ahmed_gate.py",
        "src/m4/estimator_suite.py",
        "src/m4/production_suite.py",
        "src/m4/outcome.py",
        "src/m4/bundle.py",
        "tests/test_m8_ahmed_gate.py",
        "scripts/m8_step1b_gate_prediction.py",
        "CLAUDE.md",
        "plans/m8_step1b_ahmed_transfer.md",
        "plans/m8_step1b_ahmed_transfer_addendum_a.md",
    ):
        assert expected in relative, expected


def test_authorization_yamls_are_outside_the_gating_scope():
    """They are consumed after the gate; including them would create a cycle."""
    relative = {p.relative_to(REPO_ROOT).as_posix() for p in scoped_paths()}
    assert not any("authorizations/" in r or "continuations/" in r for r in relative)


def test_the_copyrighted_pdf_is_hashed_but_does_not_gate_promotion():
    manifest = build_source_manifest()
    references = {e["path"]: e for e in manifest.reference_entries}
    assert any(r.endswith(".pdf") for r in references), "the Ahmed PDF must be hashed"
    for entry in references.values():
        assert entry["gates_promotion"] is False
        assert len(entry["sha256"]) == 64
    # It is gitignored, so it must never appear in the promotion-blocking lists.
    assert not any(p.endswith(".pdf") for p in manifest.scoped_untracked)


def test_git_status_parsing_preserves_full_paths(tmp_path):
    """Regression: a `.strip()` on porcelain output truncated every path by two chars.

    Porcelain's status column contains significant leading spaces (` M path`). Stripping
    them and then slicing `[3:]` produced `th` instead of `path`, so no file ever matched
    and a dirty tree reported as promotion-eligible — failing open on the one check that
    must fail closed.
    """
    from src.m8.ahmed_provenance import git_status_paths

    repo = tmp_path / "statusrepo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "a.py").write_text("a = 1\n", encoding="utf-8")
    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", *a], cwd=repo, check=True, capture_output=True
    )
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    run("add", "-A")
    run("commit", "-q", "-m", "init")

    (repo / "pkg" / "a.py").write_text("a = 1  # changed\n", encoding="utf-8")
    (repo / "pkg" / "b.py").write_text("b = 2\n", encoding="utf-8")

    paths = git_status_paths(cwd=repo)
    assert "pkg/a.py" in paths, f"modified file lost from {paths}"
    # --untracked-files=all: a new file in an existing dir must not collapse to 'pkg/'.
    assert "pkg/b.py" in paths, f"untracked file collapsed in {paths}"


def test_promotion_eligibility_tracks_git_state(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src" / "m8").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src" / "m8" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "CLAUDE.md").write_text("rules\n", encoding="utf-8")
    run = lambda *a: subprocess.run(  # noqa: E731
        ["git", *a], cwd=repo, check=True, capture_output=True
    )
    run("init", "-q")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")

    untracked = build_source_manifest(repo)
    assert untracked.promotion_eligible is False
    assert "src/m8/mod.py" in untracked.scoped_untracked

    run("add", "-A")
    run("commit", "-q", "-m", "init")
    clean = build_source_manifest(repo)
    assert clean.promotion_eligible is True
    assert clean.scoped_dirty == () and clean.scoped_untracked == ()

    # Different *length*, not just different bytes: git's index caches on (size, mtime),
    # so a same-size rewrite inside the same second can be missed entirely.
    (repo / "src" / "m8" / "mod.py").write_text("x = 2  # modified\n", encoding="utf-8")
    dirty = build_source_manifest(repo)
    assert dirty.promotion_eligible is False
    assert "src/m8/mod.py" in dirty.scoped_dirty
    assert dirty.manifest_sha256 != clean.manifest_sha256
