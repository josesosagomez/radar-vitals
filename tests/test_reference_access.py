"""The reference boundary rejects sealed paths before opening reference bytes."""

from __future__ import annotations

import ast
import copy
import dataclasses
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "m2"))

from builders import bind_sealed_receipt  # noqa: E402
from src.m2.cohort_registry import DEFAULT_REGISTRY_PATH, load_registry
from src.m2.common import ContractError, canonical_json_bytes, sha256_bytes, sha256_file
from src.m2.label_firewall import ReferenceOperation, transition_label_access_atomically
import src.reference_access as reference_access


CSV_BYTES = (
    b"Session,Index,Timestamp,Date,Time,O2 Saturation,Beats / min,"
    b"Perfusion Index,Pleth Variability,Breaths / min\n"
    b"0,1,1000,1/1/25,12:00:00 PM,98,73,1.5,15,15\n"
    b"0,2,1001,1/1/25,12:00:01 PM,98,74,1.6,15,16\n"
)


def _registered_capture(tmp_path: Path, monkeypatch) -> Path:
    capture = tmp_path / "results" / "live_demo" / "development_capture"
    capture.mkdir(parents=True)
    reference = capture / "reference.csv"
    reference.write_bytes(CSV_BYTES)
    registry_dir = tmp_path / "reference_registry"
    registry_dir.mkdir()
    registry = registry_dir / "development_references_v1.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "development_reference_registry_v1",
                "entries": [
                    {
                        "capture_id": capture.name,
                        "subject_id": "A",
                        "repository_relative_path": (
                            "results/live_demo/development_capture/reference.csv"
                        ),
                        "sha256": sha256_bytes(CSV_BYTES),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(reference_access, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(reference_access, "DEVELOPMENT_REGISTRY_PATH", registry)
    return capture


def test_registered_development_reference_is_hash_checked_and_parsed(
    tmp_path, monkeypatch
):
    capture = _registered_capture(tmp_path, monkeypatch)
    loaded = reference_access.load_reference(capture)
    assert loaded.source.capture_id == capture.name
    assert loaded.source.subject_id == "A"
    assert loaded.source.sha256 == sha256_bytes(CSV_BYTES)
    assert loaded.frame["epoch_utc"].tolist() == [1000, 1001]
    assert loaded.frame["pr_bpm"].tolist() == [73.0, 74.0]


def test_hash_mismatch_fails_instead_of_parsing(tmp_path, monkeypatch):
    capture = _registered_capture(tmp_path, monkeypatch)
    (capture / "reference.csv").write_bytes(CSV_BYTES + b"\n")
    with pytest.raises(ContractError, match="hash mismatch"):
        reference_access.load_reference(capture)


def test_reference_override_must_equal_registered_file(tmp_path, monkeypatch):
    capture = _registered_capture(tmp_path, monkeypatch)
    other = capture / "other.csv"
    other.write_bytes(CSV_BYTES)
    with pytest.raises(ContractError, match="override differs"):
        reference_access.load_reference(capture, reference_override=other)


def test_p001_path_fails_before_reference_bytes_are_opened(tmp_path, monkeypatch):
    capture = tmp_path / "P001_natural"
    capture.mkdir()
    (capture / "P001_natural_reference.csv").write_bytes(CSV_BYTES)

    def forbidden_read_bytes(self):
        raise AssertionError("reference bytes were opened before the firewall rejected the path")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)
    with pytest.raises(ContractError, match="P001-P015"):
        reference_access.load_reference(capture)


@pytest.mark.parametrize(
    "relative",
    [
        "data/raw/prospective/P001_natural",
        "m2_capture_work/P001_natural",
    ],
)
def test_protected_tree_fails_without_authorization(tmp_path, relative):
    capture = tmp_path / relative
    capture.mkdir(parents=True)
    with pytest.raises(ContractError, match="authorization"):
        reference_access.load_reference(capture)


def _write_registry_fixture(path: Path, document: dict) -> Path:
    content = canonical_json_bytes(document)
    path.write_bytes(content)
    path.with_suffix(path.suffix + ".sha256").write_text(
        sha256_bytes(content) + "\n", encoding="ascii"
    )
    return path


def test_authorized_prospective_reference_is_read_once_and_parsed_in_memory(
    tmp_path, monkeypatch
):
    document = copy.deepcopy(load_registry(DEFAULT_REGISTRY_PATH))
    bind_sealed_receipt(document, tmp_path, "P001_natural")
    registry1 = _write_registry_fixture(tmp_path / "registry_v001.json", document)
    reference = tmp_path / "P001_natural_reference.csv"
    reference.write_bytes(CSV_BYTES)
    digest = sha256_file(reference)

    registry2 = tmp_path / "registry_v002.json"
    stage1_audit = tmp_path / "stage1_audit.json"
    transition_label_access_atomically(
        registry_path=registry1,
        next_registry_path=registry2,
        audit_path=stage1_audit,
        subject_id="P001",
        next_state="stage1_reference_only",
        operation=ReferenceOperation.REFERENCE_TIME_SENSITIVITY,
        utc="2030-01-01T00:00:00Z",
        capture_git_commit="abc123",
        capture_git_dirty=False,
        config_sha256="b" * 64,
        scorer_sha256="c" * 64,
        session_id="P001_natural",
        arm="natural",
        reference_path=reference,
        reference_sha256=digest,
    )
    registry3 = tmp_path / "registry_v003.json"
    scoring_audit = tmp_path / "scoring_audit.json"
    authorization = transition_label_access_atomically(
        registry_path=registry2,
        next_registry_path=registry3,
        audit_path=scoring_audit,
        subject_id="P001",
        next_state="validation_opened",
        operation=ReferenceOperation.VALIDATION_SCORING,
        utc="2030-01-01T00:00:01Z",
        capture_git_commit="abc123",
        capture_git_dirty=False,
        config_sha256="b" * 64,
        scorer_sha256="c" * 64,
        session_id="P001_natural",
        arm="natural",
        reference_path=reference,
        reference_sha256=digest,
        radar_receipt_path=tmp_path / "P001_natural_sealed_radar_receipt.json",
        previous_audit_path=stage1_audit,
    )

    original_open = Path.open
    payload_binary_opens = 0

    def counted_open(self, mode="r", *args, **kwargs):
        nonlocal payload_binary_opens
        if self.resolve() == reference.resolve() and "b" in mode:
            payload_binary_opens += 1
        return original_open(self, mode, *args, **kwargs)

    parsed_payloads: list[bytes] = []
    original_parse = reference_access.load_masimo_bytes

    def counted_parse(content, *, source_name):
        parsed_payloads.append(content)
        return original_parse(content, source_name=source_name)

    monkeypatch.setattr(Path, "open", counted_open)
    monkeypatch.setattr(reference_access, "load_masimo_bytes", counted_parse)
    loaded = reference_access.load_reference(
        tmp_path,
        authorization,
        reference_override=reference,
        registry_path=registry3,
        operation=ReferenceOperation.VALIDATION_SCORING,
    )
    assert payload_binary_opens == 1
    assert parsed_payloads == [CSV_BYTES]
    assert loaded.source.sha256 == digest
    assert loaded.frame["pr_bpm"].tolist() == [73.0, 74.0]

    for invalid in (
        dataclasses.replace(authorization, subject_id="P002"),
        dataclasses.replace(authorization, session_id="P001_paced"),
        dataclasses.replace(authorization, arm="paced"),
        dataclasses.replace(authorization, reference_path=str(tmp_path / "other.csv")),
        dataclasses.replace(authorization, reference_sha256="0" * 64),
    ):
        before = payload_binary_opens
        with pytest.raises(ContractError):
            reference_access.load_reference(
                tmp_path,
                invalid,
                reference_override=reference,
                registry_path=registry3,
                operation=ReferenceOperation.VALIDATION_SCORING,
            )
        assert payload_binary_opens == before


def test_audited_entry_points_have_no_csv_discovery_or_direct_masimo_open():
    audited = (
        "score_offline.py",
        "diagnose_signal_presence.py",
        "br_bin_rule.py",
        "simulate_bin_policy.py",
        "m8_ahmed_score.py",
        "br_bin_preflight.py",
        "stage1b_temporal_continuity.py",
        "stage1b_exploratory_motion.py",
        "derive_br_comparator_evidence.py",
    )
    failures: list[str] = []
    for name in audited:
        path = reference_access.REPO_ROOT / "scripts" / name
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        if "src.reference_access" not in source:
            failures.append(f"{name}: does not import the guarded boundary")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "load_masimo":
                    failures.append(f"{name}:{node.lineno}: direct load_masimo")
                if node.func.attr in {"glob", "rglob"} and node.args:
                    pattern = node.args[0]
                    if isinstance(pattern, ast.Constant) and pattern.value in {"*.csv", "**/*.csv"}:
                        failures.append(f"{name}:{node.lineno}: reference CSV discovery")
    assert failures == []


MASIMO_PARSERS = {"load_masimo", "load_masimo_bytes"}
# Modules allowed to call a Masimo parser directly, and why.
PARSER_ALLOWLIST = {
    "src/masimo.py": "defines the parsers",
    "src/reference_access.py": "the guarded boundary itself",
    # Its default non-official loader exists only for synthetic fixtures; the command line
    # always runs officially, which uses load_registered_reference_strict.
    "scripts/m9_kotte_score.py": "fixture-only strict loader",
}
REFERENCE_NAME = re.compile(r"masimo|reference", re.IGNORECASE)


def _tracked_production_modules() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--", "src/*.py", "scripts/*.py", "steps/*.py", "figures/*.py"],
        cwd=reference_access.REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def _called_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    return func.id if isinstance(func, ast.Name) else ""


def _reference_read_violations(relative_path: str, source: str) -> list[str]:
    """Direct Masimo parsing, CSV discovery, or read_csv of a reference-named path."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        where = f"{relative_path}:{getattr(node, 'lineno', '?')}"
        if isinstance(node, ast.ImportFrom) and any(a.name in MASIMO_PARSERS for a in node.names):
            if relative_path not in PARSER_ALLOWLIST:
                found.append(f"{where}: imports a Masimo parser")
        if not isinstance(node, ast.Call):
            continue
        name = _called_name(node)
        if name in MASIMO_PARSERS and relative_path not in PARSER_ALLOWLIST:
            found.append(f"{where}: calls {name}")
        if name in {"glob", "rglob"} and node.args:
            pattern = node.args[0]
            if isinstance(pattern, ast.Constant) and str(pattern.value).lower().endswith(".csv"):
                found.append(f"{where}: CSV discovery {pattern.value!r}")
        if name == "read_csv" and node.args and relative_path not in PARSER_ALLOWLIST:
            argument = ast.get_source_segment(source, node.args[0]) or ""
            if REFERENCE_NAME.search(argument):
                found.append(f"{where}: read_csv({argument})")
    return found


def test_no_production_module_reads_a_reference_outside_the_boundary():
    failures: list[str] = []
    for relative_path in _tracked_production_modules():
        source = (reference_access.REPO_ROOT / relative_path).read_text(encoding="utf-8")
        failures.extend(_reference_read_violations(relative_path, source))
    assert failures == []


@pytest.mark.parametrize(
    "snippet",
    [
        "from src.masimo import load_masimo",
        "import src.masimo as m\nm.load_masimo_bytes(b'', source_name='x')",
        "from pathlib import Path\nPath('.').rglob('*_masimo.csv')",
        "import pandas as pd\npd.read_csv(capture / 'reference.csv')",
    ],
)
def test_reference_read_audit_catches_each_bypass_pattern(snippet):
    assert _reference_read_violations("scripts/new_tool.py", snippet)


def _link_directory(link: Path, target: Path) -> None:
    """Create a directory symlink, or a Windows junction when symlinks need privileges."""
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except OSError:
        pass
    try:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    except (ImportError, AttributeError, OSError):
        pytest.skip("this platform can create neither a directory symlink nor a junction")


def test_registered_capture_linked_into_a_protected_tree_fails_before_reading(
    tmp_path, monkeypatch
):
    """A development capture that is really a link into the sealed tree must be refused."""
    # Same folder name as the capture, so only the protected-path check can refuse it.
    sealed = tmp_path / "data" / "raw" / "prospective" / "development_capture"
    sealed.mkdir(parents=True)
    (sealed / "reference.csv").write_bytes(CSV_BYTES)
    live_demo = tmp_path / "results" / "live_demo"
    live_demo.mkdir(parents=True)
    capture = live_demo / "development_capture"
    _link_directory(capture, sealed)
    registry_dir = tmp_path / "reference_registry"
    registry_dir.mkdir()
    registry = registry_dir / "development_references_v1.json"
    registry.write_text(
        json.dumps(
            {
                "schema": "development_reference_registry_v1",
                "entries": [
                    {
                        "capture_id": capture.name,
                        "subject_id": "A",
                        "repository_relative_path": (
                            "results/live_demo/development_capture/reference.csv"
                        ),
                        "sha256": sha256_bytes(CSV_BYTES),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(reference_access, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(reference_access, "DEVELOPMENT_REGISTRY_PATH", registry)

    def forbidden_read_bytes(self):
        raise AssertionError("reference bytes were opened through a protected link")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read_bytes)
    with pytest.raises(ContractError, match="prospective/sealed"):
        reference_access.load_reference(capture)
