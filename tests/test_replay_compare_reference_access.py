"""Replay-only checks for the guarded cross-worktree reference boundary.

The committed registry remains authoritative in the active source checkout.  The
optional data root may supply bytes for one registered development capture only;
it must never turn into a general reference override or a prospective-data path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.m2.common import ContractError, sha256_bytes
import src.reference_access as reference_access


CSV_BYTES = (
    b"Session,Index,Timestamp,Date,Time,O2 Saturation,Beats / min,"
    b"Perfusion Index,Pleth Variability,Breaths / min\n"
    b"0,1,1000,1/1/25,12:00:00 PM,98,73,1.5,15,15\n"
)


def _write_registry(authority_root: Path, entries: list[dict[str, str]]) -> Path:
    registry_dir = authority_root / "reference_registry"
    registry_dir.mkdir(parents=True)
    path = registry_dir / "development_references_v1.json"
    path.write_text(
        json.dumps(
            {"schema": "development_reference_registry_v1", "entries": entries}
        ),
        encoding="utf-8",
    )
    return path


def _entry(capture_id: str, *, relative: str | None = None, digest: str | None = None):
    return {
        "capture_id": capture_id,
        "subject_id": "A",
        "repository_relative_path": relative
        or f"results/live_demo/{capture_id}/reference.csv",
        "sha256": digest or sha256_bytes(CSV_BYTES),
    }


def _install_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entries: list[dict[str, str]],
) -> tuple[Path, Path]:
    authority = tmp_path / "active_source"
    authority.mkdir()
    data_root = tmp_path / "development_data"
    data_root.mkdir()
    registry = _write_registry(authority, entries)
    monkeypatch.setattr(reference_access, "REPO_ROOT", authority)
    monkeypatch.setattr(reference_access, "DEVELOPMENT_REGISTRY_PATH", registry)
    return authority, data_root


def _write_reference(data_root: Path, capture_id: str, content: bytes = CSV_BYTES) -> Path:
    capture = data_root / "results" / "live_demo" / capture_id
    capture.mkdir(parents=True)
    (capture / "reference.csv").write_bytes(content)
    return capture


def test_external_root_changes_only_registered_data_resolution(tmp_path, monkeypatch):
    authority, data_root = _install_authority(
        tmp_path,
        monkeypatch,
        [_entry("selected"), _entry("registered_but_absent")],
    )
    capture = _write_reference(data_root, "selected")

    loaded = reference_access.load_reference(
        capture, development_data_root=data_root
    )

    assert loaded.source.capture_id == "selected"
    assert loaded.source.resolved_path == str((capture / "reference.csv").resolve())
    assert loaded.frame["pr_bpm"].tolist() == [73.0]
    assert not (authority / "results").exists()


def test_external_root_reference_hash_mismatch_fails_closed(tmp_path, monkeypatch):
    _, data_root = _install_authority(tmp_path, monkeypatch, [_entry("selected")])
    capture = _write_reference(data_root, "selected", CSV_BYTES + b"\n")

    with pytest.raises(ContractError, match="hash mismatch"):
        reference_access.load_reference(capture, development_data_root=data_root)


@pytest.mark.parametrize(
    "relative",
    [
        "../outside/reference.csv",
        "results/live_demo/../../outside/reference.csv",
        "data/raw/prospective/P001/reference.csv",
        "results/live_demo/P001_natural/reference.csv",
    ],
)
def test_external_root_rejects_registry_traversal_and_protected_components_before_read(
    tmp_path, monkeypatch, relative
):
    capture_id = "P001_natural" if "P001_natural" in relative else "selected"
    _, data_root = _install_authority(
        tmp_path, monkeypatch, [_entry(capture_id, relative=relative)]
    )
    capture = data_root / "results" / "live_demo" / capture_id
    capture.mkdir(parents=True)

    def forbidden_read_bytes(_self):
        raise AssertionError("reference bytes opened before path rejection")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)
    with pytest.raises(ContractError):
        reference_access.load_reference(capture, development_data_root=data_root)


def test_external_root_rejects_symlink_escape_before_read(tmp_path, monkeypatch):
    _, data_root = _install_authority(tmp_path, monkeypatch, [_entry("selected")])
    capture = data_root / "results" / "live_demo" / "selected"
    capture.mkdir(parents=True)
    outside = tmp_path / "outside.csv"
    outside.write_bytes(CSV_BYTES)
    link = capture / "reference.csv"
    try:
        os.symlink(outside, link)
    except OSError as exc:  # Windows may deny symlink creation without Developer Mode.
        pytest.skip(f"symlinks unavailable in this test environment: {exc}")

    opened = False
    original_read_bytes = Path.read_bytes

    def counted_read_bytes(self):
        nonlocal opened
        if self == outside:
            opened = True
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)
    with pytest.raises(ContractError, match="escapes"):
        reference_access.load_reference(capture, development_data_root=data_root)
    assert opened is False


def test_external_root_rejects_internal_same_name_capture_symlink_before_read(
    tmp_path, monkeypatch
):
    _, data_root = _install_authority(tmp_path, monkeypatch, [_entry("selected")])
    alternate = data_root / "alternate" / "selected"
    alternate.mkdir(parents=True)
    (alternate / "reference.csv").write_bytes(CSV_BYTES)
    registered_parent = data_root / "results" / "live_demo"
    registered_parent.mkdir(parents=True)
    linked_capture = registered_parent / "selected"
    try:
        os.symlink(alternate, linked_capture, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable in this test environment: {exc}")

    def forbidden_read_bytes(_self):
        raise AssertionError("reference bytes opened through internal capture symlink")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)
    with pytest.raises(ContractError, match="identity|symlink|binding"):
        reference_access.load_reference(
            linked_capture, development_data_root=data_root
        )


def test_external_root_rejects_outside_alias_to_registered_capture_before_read(
    tmp_path, monkeypatch
):
    _, data_root = _install_authority(tmp_path, monkeypatch, [_entry("selected")])
    registered = _write_reference(data_root, "selected")
    alias_parent = tmp_path / "alias"
    alias_parent.mkdir()
    alias = alias_parent / "selected"
    try:
        os.symlink(registered, alias, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable in this test environment: {exc}")

    def forbidden_read_bytes(_self):
        raise AssertionError("reference bytes opened through outside-root alias")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)
    with pytest.raises(ContractError, match="identity|root|binding"):
        reference_access.load_reference(alias, development_data_root=data_root)


def test_external_root_rejects_lexical_capture_traversal_even_if_it_resolves_in_place(
    tmp_path, monkeypatch
):
    _, data_root = _install_authority(tmp_path, monkeypatch, [_entry("selected")])
    capture = _write_reference(data_root, "selected")
    traversed = capture / ".." / "selected"

    def forbidden_read_bytes(_self):
        raise AssertionError("reference bytes opened after lexical traversal")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)
    with pytest.raises(ContractError, match="identity|traversal|binding"):
        reference_access.load_reference(traversed, development_data_root=data_root)


def test_external_root_is_incompatible_with_prospective_authorization(
    tmp_path, monkeypatch
):
    _, data_root = _install_authority(tmp_path, monkeypatch, [_entry("selected")])

    with pytest.raises(ContractError, match="incompatible with prospective"):
        reference_access.load_reference(
            data_root,
            authorization=object(),
            development_data_root=data_root,
        )


def test_external_root_must_match_requested_registered_capture(tmp_path, monkeypatch):
    _, data_root = _install_authority(tmp_path, monkeypatch, [_entry("selected")])
    wrong = _write_reference(data_root, "unregistered")

    with pytest.raises(ContractError, match="not uniquely authorized"):
        reference_access.load_reference(wrong, development_data_root=data_root)
