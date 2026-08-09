"""Fail-closed scientific dependency manifest for the Ahmed experiment.

The manifest is the transitive closure of the small, explicitly reviewed set of Ahmed
entry points plus the configs, plans, registries, and tests that define and validate the
experiment.  Local Python imports are followed recursively.  This matters because naming
only the top-level runner is not enough: a change in phase extraction, decoding, or a
serializer can change the scientific result just as surely as a change in the estimator.

The copyrighted Ahmed PDF is recorded separately as an external reference.  Its bytes are
hashed, but its unavoidable untracked state cannot make an otherwise reproducible run
promotion-ineligible.
"""
from __future__ import annotations

import ast
import base64
import binascii
import contextlib
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import importlib.util
import io
import json
import os
from pathlib import Path
import platform
from pathlib import PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Mapping, Sequence
from xml.etree import ElementTree

import numpy as np

from src.m4.bundle import sha256_bytes, sha256_path

__all__ = [
    "SourceManifest",
    "build_source_manifest",
    "conda_explicit",
    "environment_attestation",
    "build_test_attestation",
    "git_status_paths",
    "git_text",
    "load_source_manifest",
    "reference_paths",
    "scoped_paths",
    "verify_source_manifest",
    "validate_test_attestation",
    "verify_exact_authorization_transition",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_GIT_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")

# Executable entry points whose local imports form the scientific source closure.  Each
# domain named in the correction plan is represented: decoder/geometry, extraction,
# windowing, lock selection, both estimators, classifier, runner/scorer, reference parser,
# serializers, synthetic gate, and their thin command entry points.
_SCIENTIFIC_ENTRY_POINTS = (
    "src/radar_io.py",
    "src/respiration.py",
    "src/m4/window_grid.py",
    "src/warmup_select.py",
    "src/window_pipeline.py",
    "src/m4/outcome.py",
    "src/m4/production_suite.py",
    "src/m4/estimator_suite.py",
    "src/m4/estimator_runner.py",
    "src/m4/estimator_scoring.py",
    "src/m4/capture_registry.py",
    "src/m4/bundle.py",
    "src/m4/manifest.py",
    "src/masimo.py",
    "src/comparator.py",
    "src/m8/ahmed_fig8.py",
    "src/m8/ahmed_transfer.py",
    "src/m8/ahmed_synthetic.py",
    "src/m8/ahmed_gate.py",
    "src/m8/ahmed_provenance.py",
    "figures/reproduce_ahmed_fig8.py",
    "scripts/m8_step1b_gate_prediction.py",
    "scripts/m8_ahmed_transfer.py",
    "scripts/m8_ahmed_all_bins.py",
    "scripts/m8_ahmed_score.py",
    "scripts/score_production.py",
)

# Non-Python inputs that fix the experimental identity.  Unlike the previous manifest,
# these are required: a typo or absent named dependency is an error, not a reason to
# silently shorten the manifest.
_REQUIRED_ARTIFACTS = (
    "AGENTS.md",
    "CLAUDE.md",
    ".gitattributes",
    "environment.yml",
    "tests/conftest.py",
    "scripts/live_demo_config.yaml",
    "experiments/m8_ahmed_fig8/config.yaml",
    "experiments/m8_ahmed_fig8/layer_a_profiles.yaml",
    "experiments/m8_ahmed_transfer/capture_registry.yaml",
    "experiments/m8_ahmed_transfer/layer_b_profiles.yaml",
    "plans/m8_step1a_ahmed_reproduction.md",
    "plans/m8_step1b_ahmed_transfer.md",
    "plans/m8_step1b_ahmed_transfer_addendum_a.md",
    "plans/m8_ahmed_correction_plan.md",
    "tests/test_radar_io_layout.py",
    "tests/test_radar_io_split.py",
    "tests/test_respiration.py",
    "tests/test_respiration_m2_fix.py",
    "tests/test_m4_window_grid.py",
    "tests/test_live_demo_warmup_helpers.py",
    "tests/test_window_pipeline_adapter.py",
    "tests/test_m4_outcome.py",
    "tests/test_m4_estimator_suites.py",
    "tests/test_m4_estimator_runner.py",
    "tests/test_m4_evidence_serialization.py",
    "tests/test_m4_paired_runner.py",
    "tests/test_m4_preflight_strict.py",
    "tests/test_m4_registry_and_scoring.py",
    "tests/test_m4_bundle.py",
    "tests/test_m4_manifest.py",
    "tests/test_masimo.py",
    "tests/test_comparator.py",
    "tests/test_paired_metrics.py",
    "tests/test_diagnose_bin_drift.py",
    "tests/test_diagnose_bin_sweep.py",
    "tests/test_score_offline.py",
    "tests/test_m8_ahmed_fig8.py",
    "tests/test_m8_ahmed_transfer.py",
    "tests/test_m8_ahmed_gate.py",
    "tests/test_m8_ahmed_provenance.py",
    "tests/test_m8_ahmed_synthetic.py",
    "tests/test_m8_ahmed_all_bins.py",
    "tests/test_m8_ahmed_score.py",
    "tests/test_m8_ahmed_score_independent.py",
    "tests/test_m1_production_scoring.py",
)

_ATTESTED_TEST_FILES = (
    "tests/test_m8_ahmed_fig8.py",
    "tests/test_m8_ahmed_transfer.py",
    "tests/test_m8_ahmed_gate.py",
    "tests/test_m8_ahmed_synthetic.py",
    "tests/test_m8_ahmed_provenance.py",
    "tests/test_m8_ahmed_all_bins.py",
    "tests/test_m8_ahmed_score.py",
    "tests/test_m8_ahmed_score_independent.py",
    "tests/test_m1_production_scoring.py",
    "tests/test_m4_estimator_runner.py",
    "tests/test_m4_estimator_suites.py",
    "tests/test_m4_evidence_serialization.py",
    "tests/test_m4_paired_runner.py",
    "tests/test_m4_preflight_strict.py",
    "tests/test_m4_registry_and_scoring.py",
    "tests/test_m4_bundle.py",
    "tests/test_m4_manifest.py",
    "tests/test_m4_window_grid.py",
    "tests/test_m4_outcome.py",
    "tests/test_masimo.py",
    "tests/test_comparator.py",
    # Decoder/capture geometry, phase extraction, and the production estimator adapter.
    # These are declared scientific entry points, so the gate must actually execute the
    # tests that validate them and not merely hash their source.
    "tests/test_radar_io_layout.py",
    "tests/test_radar_io_split.py",
    "tests/test_respiration.py",
    "tests/test_respiration_m2_fix.py",
    "tests/test_window_pipeline_adapter.py",
    # ``src/warmup_select.py`` produces ``current_production_rerun_lock``, one of the two
    # canonical lock estimands, and its numeric bin is shared by all seven arms in every
    # cell.  ``tests/test_window_pipeline_adapter.py`` only checks identity and wiring, so
    # without this file the energy ranking, the eligibility threshold, and the settle-skip
    # window would be hashed but never executed by the gate.
    "tests/test_live_demo_warmup_helpers.py",
)
_ATTESTATION_CONFIG_INPUTS = (
    # Pytest loads this file implicitly.  It controls the only permitted skip classes,
    # so its exact bytes are a scientific attestation input even though it must not be
    # passed to pytest as a test module.
    "tests/conftest.py",
    "experiments/m8_ahmed_fig8/config.yaml",
    "experiments/m8_ahmed_fig8/layer_a_profiles.yaml",
    "experiments/m8_ahmed_transfer/capture_registry.yaml",
    "experiments/m8_ahmed_transfer/layer_b_profiles.yaml",
    "scripts/live_demo_config.yaml",
    "plans/m8_ahmed_correction_plan.md",
    "plans/m8_step1b_ahmed_transfer.md",
    "plans/m8_step1b_ahmed_transfer_addendum_a.md",
)

_NAMED_REFERENCE = (
    "literature/ref_papers/discovering_the_unseen_radar_vitals/"
    "Discovering_the_Unseen_Radar-Based_Estimation_of_Heartbeat_Breathing_Rate_and_"
    "Underlying_Muscle_Expansion_Without_Probes.pdf",
)


def git_text(*args: str, cwd: Path | None = None) -> str:
    """Run Git and return trimmed stdout; callers use failures as provenance."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd or REPO_ROOT),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        return f"<unavailable: {type(exc).__name__}: {exc}>"


def git_status_paths(cwd: Path | None = None) -> set[str]:
    """Return literal repo-relative dirty and untracked paths, failing closed."""
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "-z"],
            cwd=str(cwd or REPO_ROOT),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"cannot determine git status: {exc}") from exc

    paths: set[str] = set()
    fields = [field for field in completed.stdout.split("\0") if field]
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < 4:
            raise RuntimeError(f"malformed git status entry {entry!r}")
        status, path = entry[:2], entry[3:]
        paths.add(path.replace("\\", "/"))
        if "R" in status or "C" in status:
            if index >= len(fields):
                raise RuntimeError("git rename/copy status omitted its source path")
            paths.add(fields[index].replace("\\", "/"))
            index += 1
    return paths


def _tracked_paths(root: Path) -> set[str]:
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=str(root),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"cannot determine tracked files: {exc}") from exc
    return {
        field.replace("\\", "/")
        for field in completed.stdout.split("\0")
        if field
    }


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(f"dependency {path} lies outside repository root {root}") from exc


def _module_name(relative: str) -> str:
    path = Path(relative)
    parts = list(path.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _python_module_index(root: Path) -> dict[str, str]:
    """Map importable local module names to repo-relative Python paths."""
    index: dict[str, str] = {}
    for directory in ("src", "scripts", "figures", "tests"):
        base = root / directory
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            relative = _relative(path.resolve(), root)
            index[_module_name(relative)] = relative
            # Several established CLI scripts put ``scripts/`` on sys.path and import
            # sibling modules by basename (for example ``import score_offline``).  Those
            # are real executable dependencies even though they are not package-qualified.
            if directory in {"scripts", "figures", "tests"}:
                index.setdefault(path.stem, relative)
    return index


def _package_initializers(module: str, module_index: Mapping[str, str]) -> set[str]:
    paths: set[str] = set()
    pieces = module.split(".")
    for count in range(1, len(pieces)):
        package = ".".join(pieces[:count])
        path = module_index.get(package)
        if path and path.endswith("/__init__.py"):
            paths.add(path)
    return paths


def _local_imports(relative: str, root: Path, module_index: Mapping[str, str]) -> set[str]:
    """Resolve direct local imports in one Python dependency."""
    path = root / relative
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
    except (OSError, SyntaxError, UnicodeError) as exc:
        raise RuntimeError(f"cannot inspect scientific dependency {relative}: {exc}") from exc

    current_module = _module_name(relative)
    current_package = (
        current_module
        if relative.endswith("/__init__.py")
        else current_module.rpartition(".")[0]
    )
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            relative_name = "." * node.level + (node.module or "")
            try:
                base_module = importlib.util.resolve_name(relative_name, current_package)
            except (ImportError, ValueError) as exc:
                raise RuntimeError(f"cannot resolve import {relative_name!r} in {relative}") from exc
        else:
            base_module = node.module or ""
        if base_module:
            imported_modules.add(base_module)
        # `from . import masimo` and `from src import masimo` import a submodule.  Trying
        # each alias is harmless for normal attributes and closes this otherwise easy gap.
        for alias in node.names:
            if alias.name != "*" and base_module:
                imported_modules.add(f"{base_module}.{alias.name}")

    dependencies: set[str] = set()
    for module in imported_modules:
        candidate = module
        while candidate:
            path_for_module = module_index.get(candidate)
            if path_for_module:
                dependencies.add(path_for_module)
                dependencies.update(_package_initializers(candidate, module_index))
                break
            candidate = candidate.rpartition(".")[0]
    return dependencies


def _canonical_root(root: Path) -> bool:
    return (root / "plans" / "m8_ahmed_correction_plan.md").is_file()


def _dependency_relatives(
    root: Path,
    entry_points: Sequence[str] | None = None,
    required_artifacts: Sequence[str] | None = None,
) -> tuple[str, ...]:
    if entry_points is None:
        if _canonical_root(root):
            entry_points = _SCIENTIFIC_ENTRY_POINTS
        else:
            # Small throwaway repositories remain useful for provenance-state tests.
            entry_points = tuple(
                _relative(path.resolve(), root)
                for directory in ("src", "scripts", "figures", "tests")
                if (root / directory).is_dir()
                for path in (root / directory).rglob("*.py")
            )
    if required_artifacts is None:
        required_artifacts = _REQUIRED_ARTIFACTS if _canonical_root(root) else (
            ("CLAUDE.md",) if (root / "CLAUDE.md").is_file() else ()
        )

    required = set(entry_points) | set(required_artifacts)
    missing = sorted(relative for relative in required if not (root / relative).is_file())
    if missing:
        raise FileNotFoundError(
            "required Ahmed scientific dependencies are missing: " + ", ".join(missing)
        )

    module_index = _python_module_index(root)
    closure = set(required)
    pending = [relative for relative in entry_points if relative.endswith(".py")]
    inspected: set[str] = set()
    while pending:
        relative = pending.pop()
        if relative in inspected:
            continue
        inspected.add(relative)
        for dependency in _local_imports(relative, root, module_index):
            if dependency not in closure:
                closure.add(dependency)
                pending.append(dependency)
    return tuple(sorted(closure))


def scoped_paths(
    root: Path | None = None,
    *,
    entry_points: Sequence[str] | None = None,
    required_artifacts: Sequence[str] | None = None,
) -> list[Path]:
    """Return the exact transitive scientific dependency closure."""
    root = Path(root or REPO_ROOT).resolve()
    return [
        (root / relative).resolve()
        for relative in _dependency_relatives(root, entry_points, required_artifacts)
    ]


def reference_paths(root: Path | None = None) -> list[Path]:
    """Return available non-gating external references."""
    root = Path(root or REPO_ROOT).resolve()
    return [
        (root / relative).resolve()
        for relative in _NAMED_REFERENCE
        if (root / relative).is_file()
    ]


@dataclass(frozen=True)
class SourceManifest:
    entries: tuple[dict, ...]
    reference_entries: tuple[dict, ...]
    manifest_sha256: str
    git_commit: str
    git_branch: str
    scoped_dirty: tuple[str, ...]
    scoped_untracked: tuple[str, ...]
    promotion_eligible: bool

    @classmethod
    def from_dict(cls, document: Mapping[str, object]) -> "SourceManifest":
        """Parse a persisted manifest with a strict schema and no ignored fields."""
        expected_keys = {
            "schema_version", "scope", "entries", "reference_entries",
            "manifest_sha256", "git_commit", "git_branch", "scoped_dirty",
            "scoped_untracked", "promotion_eligible", "authorization_note",
            "reference_note",
        }
        if set(document) != expected_keys:
            raise ValueError(
                f"source manifest keys differ: missing={sorted(expected_keys - set(document))}, "
                f"extra={sorted(set(document) - expected_keys)}"
            )
        if type(document["schema_version"]) is not int or document["schema_version"] != 3:
            raise ValueError("unsupported source manifest schema")
        if document["scope"] != "transitive_scientific_dependency_closure_v1":
            raise ValueError("unsupported source manifest scope")
        if type(document["promotion_eligible"]) is not bool:
            raise ValueError("promotion_eligible must be an exact Boolean")

        manifest_sha256 = document["manifest_sha256"]
        if type(manifest_sha256) is not str or not _SHA256_PATTERN.fullmatch(
            manifest_sha256
        ):
            raise ValueError("manifest_sha256 must be a lowercase SHA-256 string")
        git_commit = document["git_commit"]
        if type(git_commit) is not str or not (
            git_commit == "<no-commit>" or _GIT_COMMIT_PATTERN.fullmatch(git_commit)
        ):
            raise ValueError("git_commit must identify the source commit")
        if type(document["git_branch"]) is not str:
            raise ValueError("git_branch must be a string")
        for note_name in ("authorization_note", "reference_note"):
            if type(document[note_name]) is not str:
                raise ValueError(f"{note_name} must be a string")

        raw_entries = document["entries"]
        raw_references = document["reference_entries"]
        if not isinstance(raw_entries, list) or not isinstance(raw_references, list):
            raise ValueError("manifest entries and reference_entries must be lists")
        entries = tuple(dict(entry) for entry in raw_entries if isinstance(entry, Mapping))
        references = tuple(
            dict(entry) for entry in raw_references if isinstance(entry, Mapping)
        )
        if len(entries) != len(raw_entries) or len(references) != len(raw_references):
            raise ValueError("every manifest entry must be an object")
        source_keys = {
            "path", "size_bytes", "sha256", "source_commit", "tracked",
            "dirty", "untracked", "status",
        }
        reference_keys = source_keys | {"gates_promotion"}

        def validate_common(entry: Mapping[str, object], expected: set[str]) -> None:
            if set(entry) != expected:
                raise ValueError(
                    f"manifest entry keys differ: missing={sorted(expected - set(entry))}, "
                    f"extra={sorted(set(entry) - expected)}"
                )
            path = entry["path"]
            if type(path) is not str or not path or "\\" in path:
                raise ValueError("manifest path must be a non-empty POSIX string")
            parsed_path = PurePosixPath(path)
            if parsed_path.is_absolute() or ".." in parsed_path.parts:
                raise ValueError("manifest path must be repository-relative")
            size_bytes = entry["size_bytes"]
            if type(size_bytes) is not int or size_bytes < 0:
                raise ValueError("size_bytes must be a non-negative exact integer")
            digest = entry["sha256"]
            if type(digest) is not str or not _SHA256_PATTERN.fullmatch(digest):
                raise ValueError("entry sha256 must be a lowercase SHA-256 string")
            source_commit = entry["source_commit"]
            if source_commit is not None and (
                type(source_commit) is not str
                or not _GIT_COMMIT_PATTERN.fullmatch(source_commit)
            ):
                raise ValueError("entry source_commit must be a Git commit string or null")
            for name in ("tracked", "dirty", "untracked"):
                if type(entry[name]) is not bool:
                    raise ValueError(f"entry {name} must be an exact Boolean")

        for entry in entries:
            validate_common(entry, source_keys)
            state = (entry["tracked"], entry["dirty"], entry["untracked"])
            valid_state = {
                "clean": (True, False, False),
                "dirty": (True, True, False),
                "untracked": (False, False, True),
            }
            if type(entry["status"]) is not str or valid_state.get(entry["status"]) != state:
                raise ValueError("source entry status is inconsistent with Git-state flags")

        for entry in references:
            validate_common(entry, reference_keys)
            if entry["status"] != "external_reference":
                raise ValueError("reference status must be external_reference")
            if type(entry["gates_promotion"]) is not bool or entry["gates_promotion"]:
                raise ValueError("external references must not gate promotion")
            if entry["untracked"] is entry["tracked"]:
                raise ValueError("reference tracked/untracked flags are inconsistent")
            if entry["dirty"] and not entry["tracked"]:
                raise ValueError("an untracked external reference cannot be dirty")

        paths = [entry["path"] for entry in entries]
        reference_paths_ = [entry["path"] for entry in references]
        if len(set(paths)) != len(paths) or len(set(reference_paths_)) != len(reference_paths_):
            raise ValueError("source manifest contains duplicate paths")
        if paths != sorted(paths) or reference_paths_ != sorted(reference_paths_):
            raise ValueError("source manifest paths must be sorted")
        def string_tuple(name: str) -> tuple[str, ...]:
            value = document[name]
            if not isinstance(value, list) or not all(type(item) is str for item in value):
                raise ValueError(f"{name} must be a list of strings")
            items = tuple(value)
            if list(items) != sorted(set(items)):
                raise ValueError(f"{name} must be sorted and unique")
            return items

        scoped_dirty = string_tuple("scoped_dirty")
        scoped_untracked = string_tuple("scoped_untracked")
        expected_dirty = tuple(
            entry["path"] for entry in entries if entry["status"] == "dirty"
        )
        expected_untracked = tuple(
            entry["path"] for entry in entries if entry["status"] == "untracked"
        )
        if scoped_dirty != expected_dirty or scoped_untracked != expected_untracked:
            raise ValueError(
                "scientific dependency content or Git status changed: "
                "persisted status lists do not match entries"
            )
        expected_eligible = (
            git_commit != "<no-commit>" and not expected_dirty and not expected_untracked
        )
        if document["promotion_eligible"] != expected_eligible:
            raise ValueError("source promotion eligibility mismatch")
        # A persisted manifest must be internally consistent on its own, before anything
        # is rebuilt from the working tree.  ``load_source_manifest`` is public and M5
        # step 6 reopens bundles with it, so self-verification cannot be left to the
        # callers that happen to also run ``verify_source_manifest``.
        if manifest_sha256 != _manifest_digest(entries, references):
            raise ValueError(
                "source manifest digest mismatch: manifest_sha256 disagrees with its own "
                "entries, so the recorded content or Git status changed after writing"
            )

        return cls(
            entries=entries,
            reference_entries=references,
            manifest_sha256=manifest_sha256,
            git_commit=git_commit,
            git_branch=document["git_branch"],
            scoped_dirty=scoped_dirty,
            scoped_untracked=scoped_untracked,
            promotion_eligible=document["promotion_eligible"],
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": 3,
            "scope": "transitive_scientific_dependency_closure_v1",
            "entries": list(self.entries),
            "reference_entries": list(self.reference_entries),
            "manifest_sha256": self.manifest_sha256,
            "git_commit": self.git_commit,
            "git_branch": self.git_branch,
            "scoped_dirty": list(self.scoped_dirty),
            "scoped_untracked": list(self.scoped_untracked),
            "promotion_eligible": self.promotion_eligible,
            "authorization_note": (
                "stage authorization and continuation files are separately hash-bound by "
                "the consuming stage; their governing plans are in this closure"
            ),
            "reference_note": (
                "external references are hash-identified but do not gate promotion"
            ),
        }


def _manifest_digest(entries: Sequence[Mapping[str, object]], references: Sequence[Mapping[str, object]]) -> str:
    spine = "\n".join(
        f"{entry['path']}\t{entry['sha256']}"
        for entry in (*entries, *references)
    ).encode("utf-8")
    return sha256_bytes(spine)


def build_source_manifest(
    root: Path | None = None,
    *,
    entry_points: Sequence[str] | None = None,
    required_artifacts: Sequence[str] | None = None,
) -> SourceManifest:
    """Build the exact manifest and record per-file Git provenance."""
    root = Path(root or REPO_ROOT).resolve()
    paths = scoped_paths(
        root, entry_points=entry_points, required_artifacts=required_artifacts
    )
    tracked = _tracked_paths(root)
    changed = git_status_paths(root)
    source_commit = git_text("rev-parse", "HEAD", cwd=root)
    has_source_commit = not source_commit.startswith("<unavailable:")
    if not has_source_commit:
        # A just-initialized throwaway repository is valid draft provenance, but can
        # never promote.  Once any tracked scientific file exists, a real execution
        # requires a concrete HEAD commit.
        source_commit = "<no-commit>"

    entries: list[dict] = []
    dirty: list[str] = []
    untracked: list[str] = []
    for path in paths:
        relative = _relative(path, root)
        is_tracked = relative in tracked
        is_dirty = is_tracked and relative in changed
        status = "untracked" if not is_tracked else "dirty" if is_dirty else "clean"
        entries.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_path(path),
                "source_commit": source_commit if is_tracked and has_source_commit else None,
                "tracked": is_tracked,
                "dirty": is_dirty,
                "untracked": not is_tracked,
                "status": status,
            }
        )
        if status == "dirty":
            dirty.append(relative)
        elif status == "untracked":
            untracked.append(relative)

    references: list[dict] = []
    for path in reference_paths(root):
        relative = _relative(path, root)
        is_tracked = relative in tracked
        references.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_path(path),
                "source_commit": source_commit if is_tracked and has_source_commit else None,
                "tracked": is_tracked,
                "dirty": bool(is_tracked and relative in changed),
                "untracked": not is_tracked,
                "status": "external_reference",
                "gates_promotion": False,
            }
        )

    entries.sort(key=lambda entry: entry["path"])
    references.sort(key=lambda entry: entry["path"])
    return SourceManifest(
        entries=tuple(entries),
        reference_entries=tuple(references),
        manifest_sha256=_manifest_digest(entries, references),
        git_commit=source_commit,
        git_branch=git_text("branch", "--show-current", cwd=root),
        scoped_dirty=tuple(sorted(dirty)),
        scoped_untracked=tuple(sorted(untracked)),
        promotion_eligible=has_source_commit and not dirty and not untracked,
    )


_POST_GATE_APPROVAL_DIRECTORIES = (
    PurePosixPath("experiments/m8_ahmed_transfer/authorizations"),
    PurePosixPath("experiments/m8_ahmed_transfer/continuations"),
)


def _require_nonempty_git_blob(root: Path, commit: str, path: str) -> None:
    """Require an approval path to exist as a nonempty regular Git blob at a commit."""
    object_spec = f"{commit}:{path}"
    try:
        object_type = subprocess.run(
            ["git", "cat-file", "-t", object_spec],
            cwd=str(root),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        size_text = subprocess.run(
            ["git", "cat-file", "-s", object_spec],
            cwd=str(root),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        size_bytes = int(size_text)
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise ValueError(
            "source commit mismatch: approval YAML must exist as a nonempty blob; "
            f"commit={commit}; path={path}"
        ) from exc
    if object_type != "blob" or size_bytes <= 0:
        raise ValueError(
            "source commit mismatch: approval YAML must exist as a nonempty blob; "
            f"commit={commit}; path={path}"
        )


def _approval_only_commit_paths(
    root: Path,
    gate_commit: str,
    current_commit: str,
) -> tuple[str, ...]:
    """Return paths only when every post-gate commit contains approval YAMLs alone."""
    try:
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", gate_commit, current_commit],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise ValueError("cannot verify the post-gate approval-only commit") from exc
    if ancestor.returncode != 0:
        raise ValueError(
            "source commit mismatch: gate commit is not an ancestor of current HEAD"
        )
    try:
        commits_result = subprocess.run(
            ["git", "rev-list", "--reverse", f"{gate_commit}..{current_commit}"],
            cwd=str(root),
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("cannot verify the post-gate approval-only commit") from exc

    commits = tuple(commit for commit in commits_result.stdout.splitlines() if commit)
    if not commits:
        raise ValueError("source commit mismatch: HEAD changed without an approval commit")

    approval_paths: list[str] = []
    expected_parent = gate_commit
    for commit in commits:
        try:
            parents_result = subprocess.run(
                ["git", "rev-list", "--parents", "-n", "1", commit],
                cwd=str(root),
                check=True,
                capture_output=True,
                text=True,
            )
            commit_and_parents = parents_result.stdout.split()
            if len(commit_and_parents) != 2 or commit_and_parents[1] != expected_parent:
                raise ValueError(
                    "source commit mismatch: post-gate history is not a linear "
                    "approval-only sequence"
                )
            changed_result = subprocess.run(
                [
                    "git",
                    "diff-tree",
                    "--no-commit-id",
                    "--name-only",
                    "--no-renames",
                    "-r",
                    "-z",
                    expected_parent,
                    commit,
                ],
                cwd=str(root),
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ValueError("cannot verify the post-gate approval-only commit") from exc

        commit_paths = tuple(
            path.replace("\\", "/")
            for path in changed_result.stdout.split("\0")
            if path
        )
        disallowed = [
            path
            for path in commit_paths
            if not (
                PurePosixPath(path).suffix == ".yaml"
                and PurePosixPath(path).parent in _POST_GATE_APPROVAL_DIRECTORIES
            )
        ]
        if not commit_paths or disallowed:
            details = (
                f"disallowed paths={sorted(disallowed)}"
                if disallowed
                else "commit changed no approval YAML"
            )
            raise ValueError(
                "source commit mismatch: post-gate commit is not approval-only; "
                f"commit={commit}; {details}"
            )
        for path in commit_paths:
            _require_nonempty_git_blob(root, commit, path)
        approval_paths.extend(commit_paths)
        expected_parent = commit

    if expected_parent != current_commit:
        raise ValueError(
            "source commit mismatch: post-gate history does not end at current HEAD"
        )
    return tuple(approval_paths)


def _entries_without_source_commit(
    entries: Sequence[Mapping[str, object]],
) -> tuple[dict[str, object], ...]:
    """Remove only the commit provenance field; retain bytes and every status flag."""
    return tuple(
        {key: value for key, value in entry.items() if key != "source_commit"}
        for entry in entries
    )


def verify_exact_authorization_transition(
    gate_source_manifest: SourceManifest | Mapping[str, object],
    authorization_path: Path,
    root: Path | None = None,
    *,
    entry_points: Sequence[str] | None = None,
    required_artifacts: Sequence[str] | None = None,
) -> dict[str, object]:
    """Prove one clean direct gate-to-authorization commit transition.

    The general source verifier supports the plan's broader approval-only history for
    continuation workflows.  Canonical production scoring is narrower: its current HEAD
    must be the direct child of the gate source commit, and that one commit must change
    exactly the authorization YAML passed to the real-data preflight.
    """
    if isinstance(gate_source_manifest, Mapping):
        gate_source_manifest = SourceManifest.from_dict(gate_source_manifest)
    if not isinstance(gate_source_manifest, SourceManifest):
        raise TypeError("gate_source_manifest must be a SourceManifest or mapping")

    repository = Path(root or REPO_ROOT).resolve()
    authorization = Path(authorization_path).resolve()
    try:
        relative_authorization = authorization.relative_to(repository).as_posix()
    except ValueError as exc:
        raise ValueError("authorization path is outside the canonical repository") from exc

    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "-z"],
            cwd=str(repository),
            check=True,
            capture_output=True,
            text=True,
        )
        current_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repository),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError("cannot verify the canonical authorization Git transition") from exc
    if status.stdout:
        raise ValueError("canonical authorization transition requires a clean whole Git tree")
    if not _GIT_COMMIT_PATTERN.fullmatch(current_head):
        raise ValueError("canonical authorization transition has a malformed current HEAD")

    verify_source_manifest(
        gate_source_manifest,
        repository,
        entry_points=entry_points,
        required_artifacts=required_artifacts,
        require_promotion_eligible=True,
    )
    if current_head == gate_source_manifest.git_commit:
        raise ValueError(
            "canonical authorization was reused without a post-gate authorization commit"
        )
    changed_paths = _approval_only_commit_paths(
        repository, gate_source_manifest.git_commit, current_head
    )
    if changed_paths != (relative_authorization,):
        raise ValueError(
            "canonical post-gate transition must change exactly the authorization passed; "
            f"expected={(relative_authorization,)}, observed={changed_paths}"
        )

    try:
        committed_blob = subprocess.run(
            ["git", "rev-parse", f"{current_head}:{relative_authorization}"],
            cwd=str(repository),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        filtered_working_blob = subprocess.run(
            [
                "git",
                "hash-object",
                f"--path={relative_authorization}",
                str(authorization),
            ],
            cwd=str(repository),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        working_bytes = authorization.read_bytes()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ValueError(
            "cannot read the committed canonical authorization bytes"
        ) from exc
    if not working_bytes or committed_blob != filtered_working_blob:
        raise ValueError(
            "canonical authorization content differs from the exact current-HEAD Git blob"
        )

    return {
        "relationship": "direct_single_authorization_commit",
        "gate_source_commit": gate_source_manifest.git_commit,
        "authorization_commit": current_head,
        "authorization_path": relative_authorization,
        "authorization_sha256": sha256_path(authorization),
        "authorization_git_blob": committed_blob,
        "changed_paths": [relative_authorization],
        "whole_tree_clean": True,
    }


def verify_source_manifest(
    manifest: SourceManifest | Mapping[str, object],
    root: Path | None = None,
    *,
    entry_points: Sequence[str] | None = None,
    required_artifacts: Sequence[str] | None = None,
    require_promotion_eligible: bool = False,
) -> None:
    """Rebuild and compare an Ahmed source manifest, rejecting every mismatch."""
    if isinstance(manifest, Mapping):
        manifest = SourceManifest.from_dict(manifest)
    if not isinstance(manifest, SourceManifest):
        raise TypeError("manifest must be a SourceManifest or manifest mapping")
    observed = build_source_manifest(
        root, entry_points=entry_points, required_artifacts=required_artifacts
    )
    expected_paths = {entry["path"] for entry in observed.entries}
    supplied_paths = {entry.get("path") for entry in manifest.entries}
    missing = sorted(expected_paths - supplied_paths)
    extra = sorted(path for path in supplied_paths - expected_paths if isinstance(path, str))
    if missing or extra:
        raise ValueError(f"scientific dependency closure mismatch: missing={missing}, extra={extra}")
    commit_changed = manifest.git_commit != observed.git_commit
    if commit_changed:
        entries_match = _entries_without_source_commit(
            manifest.entries
        ) == _entries_without_source_commit(observed.entries)
        references_match = _entries_without_source_commit(
            manifest.reference_entries
        ) == _entries_without_source_commit(observed.reference_entries)
    else:
        entries_match = manifest.entries == observed.entries
        references_match = manifest.reference_entries == observed.reference_entries
    if not entries_match:
        raise ValueError("scientific dependency content or Git status changed")
    if not references_match:
        raise ValueError("external reference content or status changed")
    if manifest.manifest_sha256 != observed.manifest_sha256:
        raise ValueError("source manifest digest mismatch")
    if commit_changed:
        if not manifest.promotion_eligible or not observed.promotion_eligible:
            raise ValueError(
                "source commit mismatch: approval-only exception requires clean, "
                "promotion-eligible gate and current manifests"
            )
        _approval_only_commit_paths(
            Path(root or REPO_ROOT).resolve(),
            manifest.git_commit,
            observed.git_commit,
        )
    if manifest.git_branch != observed.git_branch:
        raise ValueError("source branch mismatch")
    if manifest.scoped_dirty != observed.scoped_dirty or manifest.scoped_untracked != observed.scoped_untracked:
        raise ValueError("source promotion status mismatch")
    if manifest.promotion_eligible != observed.promotion_eligible:
        raise ValueError("source promotion eligibility mismatch")
    if require_promotion_eligible and not manifest.promotion_eligible:
        raise ValueError("source manifest is not promotion-eligible")


def load_source_manifest(path: Path) -> SourceManifest:
    """Load a strict UTF-8 JSON manifest for prerequisite verification."""
    def reject_nonfinite(token: str) -> None:
        raise ValueError(f"non-finite JSON token {token}")

    try:
        document = json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_constant=reject_nonfinite,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"cannot load source manifest {path}: {exc}") from exc
    if not isinstance(document, Mapping):
        raise ValueError("source manifest root must be an object")
    return SourceManifest.from_dict(document)


# The one environment variable that decides which attested nodes actually execute.
# ``tests/conftest.py`` skips every node marked ``real_data`` unless this is exactly "1".
# The builder pins it instead of inheriting the operator's shell so that "which tests
# ran" is a recorded property of the attestation rather than of the calling terminal.
# It is pinned to "0" because the synthetic gate must not depend on frozen real capture
# files; those are exercised by the plan's later real-data stages (plan section 5, M5).
_REAL_DATA_ENV_NAME = "M8_RUN_REAL_DATA_TESTS"
_REAL_DATA_ENV_VALUE = "0"

# Exactly two classes of skip are scientifically permitted, and both are declared by a
# pytest marker on the node itself (registered in tests/conftest.py):
#
#   real_data                  - reads a frozen real capture/reference file, which the
#                                synthetic gate deliberately does not touch;
#   optional_artifact_or_mode  - asserts against an OPTIONAL input: an artifact under the
#                                gitignored ``results/`` tree, or an optional config mode.
#
# Marker declaration is the whole point.  Several attested nodes inspect artifacts that
# exist on an operator's machine and not on a clean clone, so a permitted-skip set derived
# from a count, or from what happens to be on disk, would make the attestation - and hence
# the gate - a property of one filesystem.  With markers, a clean clone honestly records
# more declared skips than a machine that has run the optional sweep, and both remain valid
# and reproducible.  Any other skip still refuses to produce a gate.
_DECLARED_SKIP_MARKERS = ("real_data", "optional_artifact_or_mode")

_ATTESTATION_COMMAND_PURPOSES = (
    "collect",
    "collect_declared_real_data",
    "collect_declared_optional_artifact_or_mode",
    "execute",
)
# pytest exits 5 ("no tests were collected") when a marker expression selects nothing.
# For a declared-skip collection that is a legitimate answer meaning the attested set
# declares no node of that class at all, in which case no skip of that class is permitted.
_ATTESTATION_COMMAND_EXIT_STATUSES = ((0,), (0, 5), (0, 5), (0,))

# The executed command writes a machine-readable outcome report so the skipped node
# identities are OBSERVED rather than inferred.  Its path is a temporary directory, so this
# one argv element cannot be part of the frozen command identity.
_JUNIT_XML_OPTION_PREFIX = "--junitxml="
_JUNIT_XML_PATH_PLACEHOLDER = "<temporary-run-report>"
# Passing reports are currently below 200 KiB.  Eight MiB leaves ample room for the
# complete 1,327-node xUnit document while preventing an unbounded read from a malformed
# or substituted runner.
_MAX_JUNIT_XML_BYTES = 8 * 1024 * 1024
_CAPTURED_STDIO_MODE = "captured_text"
_INHERITED_STDIO_MODE = "inherited_not_captured"


def _attestation_commands(
    python_executable: str, junit_xml_path: str
) -> tuple[tuple[str, ...], ...]:
    base = (python_executable, "-X", "utf8", "-m", "pytest", *_ATTESTED_TEST_FILES)
    declared_skip_collections = tuple(
        (*base, "-m", marker, "--collect-only", "-q") for marker in _DECLARED_SKIP_MARKERS
    )
    return (
        (*base, "--collect-only", "-q"),
        *declared_skip_collections,
        (*base, "-q", f"{_JUNIT_XML_OPTION_PREFIX}{junit_xml_path}"),
    )


def _argv_with_report_path_normalised(argv: Sequence[str]) -> tuple[str, ...]:
    """Argv with the run-specific junit XML path replaced by a fixed placeholder.

    Everything else - interpreter, flags, and the ordered attested file list - is compared
    literally, so a change to which tests run is still rejected.  Only the report path is
    normalised, because it legitimately differs between operators and between runs.
    """
    return tuple(
        _JUNIT_XML_OPTION_PREFIX + _JUNIT_XML_PATH_PLACEHOLDER
        if item.startswith(_JUNIT_XML_OPTION_PREFIX)
        else item
        for item in argv
    )


def _collected_node_ids(stdout: str) -> list[str]:
    """Ordered pytest node IDs from one ``--collect-only -q`` stdout."""
    return [
        line.strip()
        for line in stdout.splitlines()
        if "::" in line and not line.lstrip().startswith(("<", "="))
    ]


def _pytest_node_id_from_junit_testcase(testcase: ElementTree.Element) -> str:
    """Reconstruct one exact pytest node ID from pytest's xUnit2 address fields."""
    module_prefix_by_file = {
        path: path.removesuffix(".py").replace("/", ".") for path in _ATTESTED_TEST_FILES
    }
    classname = testcase.get("classname") or ""
    test_name = testcase.get("name") or ""
    matching_files = [
        path
        for path, prefix in module_prefix_by_file.items()
        if classname == prefix or classname.startswith(f"{prefix}.")
    ]
    if not matching_files:
        raise ValueError(
            f"the executed run reported {classname!r}, which is not one of "
            "the attested test files"
        )
    # Longest prefix wins so a nested module can never be attributed to a shorter one.
    file_path = max(matching_files, key=lambda path: len(module_prefix_by_file[path]))
    enclosing_classes = classname[len(module_prefix_by_file[file_path]):].strip(".")
    node_parts = [file_path]
    if enclosing_classes:
        node_parts.extend(enclosing_classes.split("."))
    node_parts.append(test_name)
    return "::".join(node_parts)


def _pytest_node_ids_from_junit_root(root: ElementTree.Element) -> list[str]:
    """Every executed pytest node ID in exact xUnit2 document order."""
    return [
        _pytest_node_id_from_junit_testcase(testcase)
        for testcase in root.iter("testcase")
    ]


def _skipped_node_ids_from_junit_root(root: ElementTree.Element) -> list[str]:
    """Observed skipped pytest node IDs from an already validated xUnit2 tree.

    Taking the skip identities from a separate ``-m <marker>`` collection would name the
    DECLARED nodes, not the nodes that actually skipped.  A compensating pair - one declared
    node that executes plus one undeclared node that skips - reconciles numerically while
    naming the wrong node as unexercised coverage, so the identities must be observed.

    pytest's junit XML (``junit_family=xunit2``, the default) does not record the node ID
    directly.  It records ``classname`` as the node ID's file path with ``/`` replaced by
    ``.`` and the ``.py`` suffix stripped, followed by any enclosing class names, and
    ``name`` as the final component including any parametrisation.  The file path is
    therefore recovered by matching the frozen attested file list, which also means a skip
    reported outside that list is rejected instead of being silently renamed.  The caller
    must first enforce the closed grammar in ``_junit_evidence_from_bytes``; this helper
    only performs node-ID reconstruction after that validation boundary.
    """
    skipped_node_ids: list[str] = []
    for testcase in root.iter("testcase"):
        if testcase.find("skipped") is None:
            continue
        skipped_node_ids.append(_pytest_node_id_from_junit_testcase(testcase))
    return skipped_node_ids


def _skipped_node_ids_from_junit_xml(report_path: Path) -> list[str]:
    _evidence, skipped_node_ids = _read_junit_evidence(report_path)
    return skipped_node_ids


def _execute_junit_path(purpose: str, argv: Sequence[str]) -> Path | None:
    """Return the sole execute-report path, identified from role and argv structure."""
    report_options = [
        item.removeprefix(_JUNIT_XML_OPTION_PREFIX)
        for item in argv
        if item.startswith(_JUNIT_XML_OPTION_PREFIX)
    ]
    if purpose != "execute":
        if report_options:
            raise ValueError("only the execute attestation command may request JUnit XML")
        return None
    if "--collect-only" in argv or len(report_options) != 1 or not report_options[0]:
        raise ValueError(
            "execute attestation command must contain exactly one JUnit XML argument"
        )
    return Path(report_options[0])


def _run_attestation_command(
    purpose: str,
    argv: Sequence[str],
    *,
    repository: Path,
    command_environment: Mapping[str, str],
    command_runner=subprocess.run,
) -> tuple[subprocess.CompletedProcess, str, str | None, str | None]:
    """Run one attestation command with the stdio mode required by its role.

    Collection output is a parsed input, so it remains captured and hash-bound.  The
    execute command is identified structurally by its sole JUnit argument and inherits
    the parent handles for Windows native-library compatibility.  Its exact bounded JUnit
    bytes, rather than unavailable console text, are validated separately.
    """
    execute_report_path = _execute_junit_path(purpose, argv)
    if execute_report_path is not None:
        completed = command_runner(
            list(argv), cwd=str(repository), env=dict(command_environment)
        )
        return completed, _INHERITED_STDIO_MODE, None, None

    completed = command_runner(
        list(argv),
        cwd=str(repository),
        env=dict(command_environment),
        capture_output=True,
        text=True,
    )
    if type(completed.stdout) is not str or type(completed.stderr) is not str:
        raise RuntimeError("captured test-attestation output is not decoded text")
    return (
        completed,
        _CAPTURED_STDIO_MODE,
        sha256_bytes(completed.stdout.encode("utf-8")),
        sha256_bytes(completed.stderr.encode("utf-8")),
    )


def _nonnegative_junit_count(suite: ElementTree.Element, name: str) -> int:
    value = suite.get(name)
    if value is None or re.fullmatch(r"[0-9]+", value) is None:
        raise ValueError(f"JUnit {name} count is malformed")
    return int(value)


def _require_junit_attributes(
    element: ElementTree.Element,
    expected: set[str],
    *,
    context: str,
) -> None:
    if set(element.attrib) != expected:
        raise ValueError(f"JUnit {context} attributes are malformed")


def _require_structural_junit_text(
    element: ElementTree.Element,
    *,
    context: str,
    allow_content: bool = False,
) -> None:
    if not allow_content and element.text is not None and not element.text.isspace():
        raise ValueError(f"JUnit {context} contains unexpected text")
    if element.tail is not None and not element.tail.isspace():
        raise ValueError(f"JUnit {context} contains unexpected tail text")


def _nonnegative_junit_decimal(value: str | None, *, context: str) -> Decimal:
    # Pinned pytest 8.4.2 emits both suite and testcase durations with ``:.3f``.
    if value is None or re.fullmatch(r"[0-9]+\.[0-9]{3}", value) is None:
        raise ValueError(f"JUnit {context} is malformed")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"JUnit {context} is malformed") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ValueError(f"JUnit {context} is malformed")
    return parsed


def _validate_junit_properties(
    properties: ElementTree.Element,
    *,
    context: str,
) -> None:
    _require_junit_attributes(properties, set(), context=f"{context} properties")
    _require_structural_junit_text(properties, context=f"{context} properties")
    property_nodes = list(properties)
    if not property_nodes:
        raise ValueError(f"JUnit {context} properties are empty")
    for property_node in property_nodes:
        if property_node.tag != "property":
            raise ValueError(f"JUnit {context} properties contain an unknown child")
        _require_junit_attributes(
            property_node, {"name", "value"}, context=f"{context} property"
        )
        if list(property_node):
            raise ValueError(f"JUnit {context} property contains child elements")
        _require_structural_junit_text(
            property_node, context=f"{context} property"
        )


def _validate_junit_testcase(testcase: ElementTree.Element) -> str:
    """Validate pytest 8 xUnit2 testcase grammar and return its stable identity."""
    _require_junit_attributes(
        testcase, {"classname", "name", "time"}, context="testcase"
    )
    _require_structural_junit_text(testcase, context="testcase")
    if not testcase.get("classname") or not testcase.get("name"):
        raise ValueError("JUnit testcase identity is incomplete")
    _nonnegative_junit_decimal(testcase.get("time"), context="testcase duration")

    seen_properties = False
    seen_outcome = False
    seen_output = False
    outcome_tags = {"skipped", "failure", "error"}
    for index, child in enumerate(testcase):
        if child.tag == "properties":
            if index != 0 or seen_properties or seen_outcome or seen_output:
                raise ValueError("JUnit testcase properties cardinality/order is malformed")
            seen_properties = True
            _validate_junit_properties(child, context="testcase")
        elif child.tag in outcome_tags:
            if seen_outcome or seen_output:
                raise ValueError("JUnit testcase outcome cardinality/order is malformed")
            seen_outcome = True
            if child.tag in {"failure", "error"}:
                _require_junit_attributes(
                    child, {"message"}, context=f"{child.tag} outcome"
                )
            else:
                if set(child.attrib) not in ({"message"}, {"type", "message"}):
                    raise ValueError("JUnit skipped attributes are malformed")
                if "type" in child.attrib and child.get("type") not in {
                    "pytest.skip", "pytest.xfail"
                }:
                    raise ValueError("JUnit skipped type is malformed")
            if list(child):
                raise ValueError("JUnit testcase outcome contains child elements")
            _require_structural_junit_text(
                child, context=f"{child.tag} outcome", allow_content=True
            )
        elif child.tag in {"system-out", "system-err"}:
            seen_output = True
            _require_junit_attributes(child, set(), context=child.tag)
            if list(child):
                raise ValueError(f"JUnit {child.tag} contains child elements")
            _require_structural_junit_text(
                child, context=child.tag, allow_content=True
            )
        else:
            raise ValueError("JUnit testcase contains an unknown child")
    return f"{testcase.get('classname')}::{testcase.get('name')}"


def _strict_junit_root(xml_bytes: bytes) -> ElementTree.Element:
    """Parse only the closed pytest 8.4 xUnit2 grammar used by this gate.

    The pinned pytest emits an optional UTF-8 XML declaration, ``testsuites`` with
    one ``pytest`` suite, optional suite/testcase properties, testcase outcomes, and
    optional captured ``system-out``/``system-err`` text.  It emits no DTD, entities,
    namespaces, comments, CDATA, or processing instructions.  Those forms are rejected
    before ElementTree receives the bytes, so entity expansion cannot begin.
    """
    try:
        xml_text = xml_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("executed test JUnit XML is not strict UTF-8") from exc

    if xml_text.startswith("\ufeff"):
        raise ValueError("executed test JUnit XML UTF-8 BOM is forbidden")

    body = xml_text
    if body.startswith("<?xml"):
        declaration_end = body.find("?>")
        if declaration_end < 0:
            raise ValueError("executed test JUnit XML declaration is malformed")
        declaration = body[: declaration_end + 2]
        if declaration not in {
            '<?xml version="1.0" encoding="utf-8"?>',
            "<?xml version='1.0' encoding='utf-8'?>",
        }:
            raise ValueError("executed test JUnit XML declaration is unsupported")
        body = body[declaration_end + 2 :]
    if "<!" in body:
        raise ValueError("executed test JUnit XML declarations are forbidden")
    if "<?" in body:
        raise ValueError("executed test JUnit XML processing instructions are forbidden")
    if re.search(r"<[^>]*\sxmlns(?::[A-Za-z_][\w.-]*)?\s*=", body):
        raise ValueError("executed test JUnit XML namespaces are forbidden")

    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        raise ValueError("executed test JUnit XML is malformed") from exc
    if root.tag != "testsuites":
        raise ValueError("executed test JUnit XML root is not testsuites")
    _require_junit_attributes(root, {"name"}, context="testsuites")
    if root.get("name") != "pytest tests":
        raise ValueError("JUnit testsuites identity is malformed")
    _require_structural_junit_text(root, context="testsuites")

    suites = list(root)
    if len(suites) != 1 or suites[0].tag != "testsuite":
        raise ValueError("executed test JUnit XML is not one xUnit2 testsuite")
    suite = suites[0]
    _require_junit_attributes(
        suite,
        {
            "name", "errors", "failures", "skipped", "tests", "time",
            "timestamp", "hostname",
        },
        context="testsuite",
    )
    if suite.get("name") != "pytest":
        raise ValueError("JUnit testsuite identity is malformed")
    _require_structural_junit_text(suite, context="testsuite")

    seen_suite_properties = False
    testcase_identities: set[str] = set()
    for index, child in enumerate(suite):
        if child.tag == "properties":
            if index != 0 or seen_suite_properties:
                raise ValueError("JUnit testsuite properties cardinality/order is malformed")
            seen_suite_properties = True
            _validate_junit_properties(child, context="testsuite")
        elif child.tag == "testcase":
            identity = _validate_junit_testcase(child)
            if identity in testcase_identities:
                raise ValueError("JUnit testcase identity is duplicated")
            testcase_identities.add(identity)
        else:
            raise ValueError("JUnit testsuite contains an unknown child")
    return root


def _junit_evidence_from_bytes(
    xml_bytes: bytes,
) -> tuple[dict[str, object], list[str]]:
    if not xml_bytes:
        raise ValueError("executed test JUnit XML is empty")
    if len(xml_bytes) > _MAX_JUNIT_XML_BYTES:
        raise ValueError(
            f"executed test JUnit XML exceeds {_MAX_JUNIT_XML_BYTES} bytes"
        )
    root = _strict_junit_root(xml_bytes)
    suites = list(root)
    suite = suites[0]
    tests_count = _nonnegative_junit_count(suite, "tests")
    errors_count = _nonnegative_junit_count(suite, "errors")
    failures_count = _nonnegative_junit_count(suite, "failures")
    skipped_count = _nonnegative_junit_count(suite, "skipped")
    suite_time_seconds = suite.get("time")
    _nonnegative_junit_decimal(suite_time_seconds, context="suite duration")
    suite_timestamp = suite.get("timestamp")
    try:
        parsed_timestamp = datetime.fromisoformat(suite_timestamp or "")
    except ValueError as exc:
        raise ValueError("JUnit suite timestamp is malformed") from exc
    if parsed_timestamp.tzinfo is None:
        raise ValueError("JUnit suite timestamp must be timezone-aware")

    testcases = list(suite.findall("testcase"))
    if len(testcases) != tests_count:
        raise ValueError("JUnit testcase cardinality differs from its suite count")
    outcome_element_by_count = {
        "errors": "error",
        "failures": "failure",
        "skipped": "skipped",
    }
    observed_counts = {name: 0 for name in outcome_element_by_count}
    for testcase in testcases:
        outcomes = [
            name
            for name, element_name in outcome_element_by_count.items()
            if testcase.find(element_name) is not None
        ]
        if len(outcomes) > 1:
            raise ValueError("JUnit testcase has multiple terminal outcomes")
        if outcomes:
            observed_counts[outcomes[0]] += 1
    if observed_counts != {
        "errors": errors_count,
        "failures": failures_count,
        "skipped": skipped_count,
    }:
        raise ValueError("JUnit suite counts disagree with testcase outcomes")
    passed_count = tests_count - errors_count - failures_count - skipped_count
    if passed_count < 0:
        raise ValueError("JUnit suite counts exceed its testcase total")

    ordered_pytest_node_ids = _pytest_node_ids_from_junit_root(root)
    evidence = {
        "schema_id": "pytest_xunit2_single_suite_v2",
        "xml_base64": base64.b64encode(xml_bytes).decode("ascii"),
        "xml_sha256": sha256_bytes(xml_bytes),
        "size_bytes": len(xml_bytes),
        "tests_count": tests_count,
        "passed_count": passed_count,
        "skipped_count": skipped_count,
        "failures_count": failures_count,
        "errors_count": errors_count,
        "suite_time_seconds": suite_time_seconds,
        "suite_timestamp": suite_timestamp,
        "ordered_pytest_node_ids": ordered_pytest_node_ids,
    }
    return evidence, _skipped_node_ids_from_junit_root(root)


def _read_junit_evidence(report_path: Path) -> tuple[dict[str, object], list[str]]:
    try:
        with Path(report_path).open("rb") as handle:
            xml_bytes = handle.read(_MAX_JUNIT_XML_BYTES + 1)
    except OSError as exc:
        raise RuntimeError(
            f"cannot read the executed run's outcome report {report_path}: {exc}"
        ) from exc
    try:
        return _junit_evidence_from_bytes(xml_bytes)
    except ValueError as exc:
        raise RuntimeError(
            f"executed run outcome report {report_path} is invalid: {exc}"
        ) from exc


def _attestation_input_hashes(source_manifest: SourceManifest) -> dict[str, str]:
    entries = {entry["path"]: entry["sha256"] for entry in source_manifest.entries}
    expected = (*_ATTESTED_TEST_FILES, *_ATTESTATION_CONFIG_INPUTS)
    missing = [path for path in expected if path not in entries]
    if missing:
        raise ValueError(f"source manifest omitted test-attestation inputs: {missing}")
    return {path: str(entries[path]) for path in expected}


def build_test_attestation(
    source_manifest: SourceManifest,
    *,
    root: Path | None = None,
    command_runner=subprocess.run,
    temporary_parent: Path | None = None,
) -> dict:
    """Execute and freeze the complete focused scientific test attestation.

    Four ordered commands are issued over exactly the same attested file set:

    1. ``--collect-only`` so every ordered pytest node ID is known explicitly;
    2. ``-m real_data --collect-only`` — declared skip class 1;
    3. ``-m optional_artifact_or_mode --collect-only`` — declared skip class 2;
    4. execution, writing a junit XML report so the skipped node identities are observed.

    A failure, error, xfail/xpass, undeclared skip, collection mismatch, or unexpected
    command status fails before a synthetic bundle exists.  A skip is permitted only if the
    node belongs to one of the declared classes described at ``_DECLARED_SKIP_MARKERS``;
    the declared node IDs and the observed skipped node IDs are both recorded, so a reader
    can see exactly which scientific coverage the synthetic gate did not have.
    """
    if not isinstance(source_manifest, SourceManifest):
        raise TypeError("source_manifest must be a SourceManifest")
    repository = Path(root or REPO_ROOT).resolve()
    started = datetime.now(timezone.utc).isoformat()
    # The interpreter still needs the ambient PATH/SYSTEMROOT to start, so the parent
    # environment is copied and only the governing variable is pinned and recorded.
    command_environment = dict(os.environ)
    command_environment[_REAL_DATA_ENV_NAME] = _REAL_DATA_ENV_VALUE
    completed_commands: list[dict] = []
    captured_outputs: list[subprocess.CompletedProcess] = []

    # The outcome report is a transient intermediate of this check, not an artifact of the
    # experiment, so it is written to a temporary directory and never into the repository
    # or ``results/``.
    # ``temporary_parent`` is operational plumbing for restricted or unusual platforms.
    # It never enters the persisted scientific identity; the report's exact bounded bytes
    # and summary are embedded and hash-bound in the attestation before it is deleted.
    with tempfile.TemporaryDirectory(
        prefix="m8_test_attestation_",
        dir=None if temporary_parent is None else str(Path(temporary_parent).resolve()),
    ) as report_directory:
        junit_xml_path = Path(report_directory) / "attested_pytest_report.xml"
        commands = _attestation_commands(sys.executable, str(junit_xml_path))
        execute_command_count = 0
        for purpose, argv, allowed_statuses in zip(
            _ATTESTATION_COMMAND_PURPOSES, commands, _ATTESTATION_COMMAND_EXIT_STATUSES
        ):
            try:
                execute_report_path = _execute_junit_path(purpose, argv)
            except ValueError as exc:
                raise RuntimeError("test attestation command structure is invalid") from exc
            if (
                execute_report_path is not None
                and execute_report_path.resolve() != junit_xml_path.resolve()
            ):
                raise RuntimeError("execute command JUnit path differs from the allocated report")
            if execute_report_path is not None:
                execute_command_count += 1
            try:
                (
                    completed,
                    stdio_capture_mode,
                    stdout_sha256,
                    stderr_sha256,
                ) = _run_attestation_command(
                    purpose,
                    argv,
                    repository=repository,
                    command_environment=command_environment,
                    command_runner=command_runner,
                )
            except ValueError as exc:
                raise RuntimeError("test attestation command structure is invalid") from exc
            if execute_report_path is None:
                captured_outputs.append(completed)
            completed_commands.append(
                {
                    "purpose": purpose,
                    "argv": list(argv),
                    "working_directory": str(repository),
                    "exit_status": int(completed.returncode),
                    "stdio_capture_mode": stdio_capture_mode,
                    "stdout_sha256": stdout_sha256,
                    "stderr_sha256": stderr_sha256,
                }
            )
            if completed.returncode not in allowed_statuses:
                output_tail = ""
                if execute_report_path is None:
                    output_tail = f": {completed.stdout[-2000:]}{completed.stderr[-2000:]}"
                raise RuntimeError(
                    f"test attestation {purpose} command failed with exit "
                    f"{completed.returncode}{output_tail}"
                )
        if execute_command_count != 1:
            raise RuntimeError("test attestation must execute exactly one inherited JUnit command")
        if len(captured_outputs) != len(_ATTESTATION_COMMAND_PURPOSES) - 1:
            raise RuntimeError("test attestation collection command cardinality differs")

        node_ids = _collected_node_ids(captured_outputs[0].stdout)
        if not node_ids or len(node_ids) != len(set(node_ids)):
            raise RuntimeError("test collection did not emit unique ordered pytest node IDs")

        # One collection per declared class, so membership is read from the markers in the
        # committed test source rather than being inferred from the executed run.
        declared_skip_node_ids: dict[str, list[str]] = {}
        for marker, collection in zip(_DECLARED_SKIP_MARKERS, captured_outputs[1:]):
            declared = _collected_node_ids(collection.stdout)
            if len(set(declared)) != len(declared) or not set(declared) <= set(node_ids):
                raise RuntimeError(
                    f"declared {marker} nodes are duplicated or absent from the attested "
                    "collection"
                )
            declared_skip_node_ids[marker] = declared
        permitted_skip_node_ids = set().union(*declared_skip_node_ids.values())

        execute_junit, observed_skipped_node_ids = _read_junit_evidence(junit_xml_path)
        if execute_junit["ordered_pytest_node_ids"] != node_ids:
            raise RuntimeError(
                "executed JUnit node order/identity differs from collected pytest nodes"
            )

    if len(set(observed_skipped_node_ids)) != len(observed_skipped_node_ids):
        raise RuntimeError("the executed run reported the same skipped node more than once")
    undeclared_skips = sorted(set(observed_skipped_node_ids) - permitted_skip_node_ids)
    if undeclared_skips:
        raise RuntimeError(
            "test execution skipped nodes that are not declared by any of "
            f"{list(_DECLARED_SKIP_MARKERS)}, so scientific coverage was lost without "
            f"being recorded: {undeclared_skips}"
        )

    passed_count = int(execute_junit["passed_count"])
    skipped_count = int(execute_junit["skipped_count"])
    failed_count = int(execute_junit["failures_count"]) + int(
        execute_junit["errors_count"]
    )
    if (
        passed_count + skipped_count != len(node_ids)
        or skipped_count != len(observed_skipped_node_ids)
        or failed_count != 0
    ):
        raise RuntimeError(
            "test execution did not pass every collected node apart from declared "
            f"skips: collected={len(node_ids)}, passed={passed_count}, "
            f"skipped={skipped_count}, observed_skips={len(observed_skipped_node_ids)}, "
            f"declared_skips={len(permitted_skip_node_ids)}, failed={failed_count}"
        )

    versions = {"numpy": np.__version__}
    for module_name in ("scipy", "pytest"):
        module = __import__(module_name)
        versions[module_name] = str(module.__version__)
    attestation = {
        "schema_version": 3,
        "status": "passed",
        "source_manifest_sha256": source_manifest.manifest_sha256,
        "started_utc": started,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "ordered_commands": completed_commands,
        "execute_junit": execute_junit,
        "ordered_pytest_node_ids": node_ids,
        "declared_skip_pytest_node_ids": declared_skip_node_ids,
        "skipped_pytest_node_ids": observed_skipped_node_ids,
        "m8_run_real_data_tests_env_value": _REAL_DATA_ENV_VALUE,
        "collected_count": len(node_ids),
        "passed_count": passed_count,
        "failed_count": 0,
        "skipped_count": skipped_count,
        "environment": {
            "python_version": sys.version,
            "python_executable": sys.executable,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "byte_order": sys.byteorder,
            "versions": versions,
        },
        "input_sha256": _attestation_input_hashes(source_manifest),
    }
    validate_test_attestation(attestation, source_manifest)
    return attestation


def _validate_execute_junit_document(
    document: object,
) -> tuple[dict[str, object], list[str]]:
    expected_keys = {
        "schema_id",
        "xml_base64",
        "xml_sha256",
        "size_bytes",
        "tests_count",
        "passed_count",
        "skipped_count",
        "failures_count",
        "errors_count",
        "suite_time_seconds",
        "suite_timestamp",
        "ordered_pytest_node_ids",
    }
    if not isinstance(document, Mapping) or set(document) != expected_keys:
        raise ValueError("test attestation execute JUnit schema is malformed")
    encoded = document["xml_base64"]
    if type(encoded) is not str:
        raise ValueError("test attestation execute JUnit bytes are malformed")
    maximum_encoded_length = 4 * ((_MAX_JUNIT_XML_BYTES + 2) // 3)
    if len(encoded) > maximum_encoded_length:
        raise ValueError("test attestation execute JUnit exceeds its bounded size")
    try:
        xml_bytes = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise ValueError("test attestation execute JUnit bytes are malformed") from exc
    if len(xml_bytes) > _MAX_JUNIT_XML_BYTES:
        raise ValueError("test attestation execute JUnit exceeds its bounded size")
    if document["size_bytes"] != len(xml_bytes):
        raise ValueError("test attestation execute JUnit byte count differs")
    if document["xml_sha256"] != sha256_bytes(xml_bytes):
        raise ValueError("test attestation execute JUnit hash differs")
    parsed, skipped_node_ids = _junit_evidence_from_bytes(xml_bytes)
    if dict(document) != parsed:
        raise ValueError("test attestation execute JUnit summary differs from exact XML")
    return parsed, skipped_node_ids


def validate_test_attestation(
    document: Mapping[str, object],
    source_manifest: SourceManifest | Mapping[str, object],
) -> None:
    """Strictly validate a persisted focused-test attestation and its source binding."""
    v2_expected_keys = {
        "schema_version", "status", "source_manifest_sha256", "started_utc",
        "completed_utc", "ordered_commands", "ordered_pytest_node_ids",
        "declared_skip_pytest_node_ids", "skipped_pytest_node_ids",
        "m8_run_real_data_tests_env_value",
        "collected_count", "passed_count", "failed_count", "skipped_count",
        "environment", "input_sha256",
    }
    if not isinstance(document, Mapping):
        raise ValueError("test attestation has a malformed top-level schema")
    schema_version = document.get("schema_version")
    if schema_version == 2:
        if set(document) != v2_expected_keys:
            raise ValueError("test attestation has a malformed top-level schema")
    elif schema_version == 3:
        if set(document) != v2_expected_keys | {"execute_junit"}:
            raise ValueError("test attestation has a malformed top-level schema")
    else:
        raise ValueError("test attestation status/schema is not passed/supported")
    # Schema 2 is the immutable all-captured historical contract.  Schema 3 retains its
    # scientific identity and adds exact JUnit authority for inherited final execution.
    if document["status"] != "passed":
        if schema_version == 2:
            raise ValueError("test attestation status/schema is not passed/v2")
        raise ValueError("test attestation status/schema is not passed/v3")
    if isinstance(source_manifest, SourceManifest):
        source_sha = source_manifest.manifest_sha256
        source_entries = {entry["path"]: entry["sha256"] for entry in source_manifest.entries}
    elif isinstance(source_manifest, Mapping):
        source_sha = source_manifest.get("manifest_sha256")
        entries = source_manifest.get("entries")
        if type(entries) is not list:
            raise ValueError("source manifest entries are required for test attestation")
        source_entries = {
            entry.get("path"): entry.get("sha256")
            for entry in entries
            if isinstance(entry, Mapping)
        }
    else:
        raise TypeError("source_manifest must be a SourceManifest or mapping")
    if document["source_manifest_sha256"] != source_sha:
        raise ValueError("test attestation source-manifest identity mismatch")

    for timestamp_key in ("started_utc", "completed_utc"):
        value = document[timestamp_key]
        if type(value) is not str:
            raise ValueError(f"test attestation {timestamp_key} must be an ISO timestamp")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"test attestation {timestamp_key} is malformed") from exc
        if parsed.tzinfo is None:
            raise ValueError(f"test attestation {timestamp_key} must be timezone-aware")

    environment = document["environment"]
    environment_keys = {
        "python_version", "python_executable", "platform", "machine", "byte_order", "versions"
    }
    if not isinstance(environment, Mapping) or set(environment) != environment_keys:
        raise ValueError("test attestation environment schema is malformed")
    if environment["byte_order"] not in ("little", "big"):
        raise ValueError("test attestation byte order is malformed")
    if any(type(environment[key]) is not str or not environment[key] for key in (
        "python_version", "python_executable", "platform", "machine"
    )):
        raise ValueError("test attestation platform identity is incomplete")
    versions = environment["versions"]
    if not isinstance(versions, Mapping) or set(versions) != {"numpy", "scipy", "pytest"}:
        raise ValueError("test attestation version identity is incomplete")
    if any(type(value) is not str or not value for value in versions.values()):
        raise ValueError("test attestation versions are malformed")

    commands = document["ordered_commands"]
    # The recorded junit XML path is run-specific, so both sides are compared with that one
    # element normalised to a placeholder; every other element must match literally.
    expected_argv = _attestation_commands(
        str(environment["python_executable"]), _JUNIT_XML_PATH_PLACEHOLDER
    )
    if type(commands) is not list or len(commands) != len(_ATTESTATION_COMMAND_PURPOSES):
        raise ValueError(
            f"test attestation must contain {len(_ATTESTATION_COMMAND_PURPOSES)} "
            "ordered commands"
        )
    inherited_execute_count = 0
    for command, purpose, argv, allowed_statuses in zip(
        commands,
        _ATTESTATION_COMMAND_PURPOSES,
        expected_argv,
        _ATTESTATION_COMMAND_EXIT_STATUSES,
    ):
        v2_command_keys = {
            "purpose", "argv", "exit_status", "stdout_sha256", "stderr_sha256"
        }
        expected_command_keys = (
            v2_command_keys
            if schema_version == 2
            else v2_command_keys | {"stdio_capture_mode", "working_directory"}
        )
        if not isinstance(command, Mapping) or set(command) != expected_command_keys:
            raise ValueError("test attestation command schema is malformed")
        recorded_argv = command["argv"]
        if type(recorded_argv) is not list or any(
            type(item) is not str for item in recorded_argv
        ):
            raise ValueError("test attestation command argv is malformed")
        if command["purpose"] != purpose or _argv_with_report_path_normalised(
            recorded_argv
        ) != _argv_with_report_path_normalised(argv):
            raise ValueError("test attestation ordered command identity mismatch")
        if type(command["exit_status"]) is not int or command["exit_status"] not in allowed_statuses:
            raise ValueError("test attestation command did not pass")
        if schema_version == 2:
            for key in ("stdout_sha256", "stderr_sha256"):
                digest = command[key]
                if type(digest) is not str or _SHA256_PATTERN.fullmatch(digest) is None:
                    raise ValueError("test attestation output hash is malformed")
        else:
            working_directory = command["working_directory"]
            if (
                type(working_directory) is not str
                or not working_directory
                or not Path(working_directory).is_absolute()
            ):
                raise ValueError("test attestation command working directory is malformed")
            try:
                execute_report_path = _execute_junit_path(purpose, recorded_argv)
            except ValueError as exc:
                raise ValueError("test attestation execute command structure is malformed") from exc
            if execute_report_path is None:
                if command["stdio_capture_mode"] != _CAPTURED_STDIO_MODE:
                    raise ValueError("test attestation collection stdio mode is malformed")
                for key in ("stdout_sha256", "stderr_sha256"):
                    digest = command[key]
                    if type(digest) is not str or _SHA256_PATTERN.fullmatch(digest) is None:
                        raise ValueError("test attestation captured output hash is malformed")
            else:
                inherited_execute_count += 1
                if command["stdio_capture_mode"] != _INHERITED_STDIO_MODE:
                    raise ValueError("test attestation execute stdio mode is malformed")
                if command["stdout_sha256"] is not None or command["stderr_sha256"] is not None:
                    raise ValueError(
                        "test attestation inherited execute output hashes must be null"
                    )
    if schema_version == 3:
        working_directories = {
            command["working_directory"] for command in commands
        }
        if len(working_directories) != 1:
            raise ValueError("test attestation command working directory identity mismatch")
    junit_skipped_node_ids = None
    execute_junit = None
    if schema_version == 3:
        if inherited_execute_count != 1:
            raise ValueError("test attestation must contain one inherited execute command")
        execute_junit, junit_skipped_node_ids = _validate_execute_junit_document(
            document["execute_junit"]
        )

    if document["m8_run_real_data_tests_env_value"] != _REAL_DATA_ENV_VALUE:
        raise ValueError(
            "test attestation did not pin the governing real-data environment variable"
        )
    node_ids = document["ordered_pytest_node_ids"]
    if type(node_ids) is not list or not node_ids or any(
        type(node) is not str or "::" not in node for node in node_ids
    ) or len(node_ids) != len(set(node_ids)):
        raise ValueError("test attestation ordered pytest node IDs are malformed")
    if (
        schema_version == 3
        and execute_junit is not None
        and execute_junit["ordered_pytest_node_ids"] != node_ids
    ):
        raise ValueError(
            "test attestation collected node order/identity differs from exact execute JUnit"
        )
    # The declared classes are the permitted-skip set.  They must be present for exactly
    # the frozen markers, so a class cannot be dropped to make an undeclared skip look
    # declared, and a class cannot be invented to widen what is permitted.
    declared_by_class = document["declared_skip_pytest_node_ids"]
    if not isinstance(declared_by_class, Mapping) or set(declared_by_class) != set(
        _DECLARED_SKIP_MARKERS
    ):
        raise ValueError(
            "test attestation must declare exactly the frozen skip classes "
            f"{list(_DECLARED_SKIP_MARKERS)}"
        )
    permitted_skip_node_ids: set[str] = set()
    for marker in _DECLARED_SKIP_MARKERS:
        declared = declared_by_class[marker]
        if type(declared) is not list or any(
            type(node) is not str or "::" not in node for node in declared
        ) or len(declared) != len(set(declared)):
            raise ValueError(f"test attestation declared {marker} node IDs are malformed")
        if not set(declared) <= set(node_ids):
            raise ValueError(
                f"test attestation declared {marker} nodes are outside the attested "
                "collection"
            )
        permitted_skip_node_ids.update(declared)

    skipped_node_ids = document["skipped_pytest_node_ids"]
    if type(skipped_node_ids) is not list or any(
        type(node) is not str or "::" not in node for node in skipped_node_ids
    ) or len(skipped_node_ids) != len(set(skipped_node_ids)):
        raise ValueError("test attestation skipped pytest node IDs are malformed")
    if not set(skipped_node_ids) <= set(node_ids):
        raise ValueError("test attestation skipped nodes are outside the attested collection")
    if schema_version == 3 and skipped_node_ids != junit_skipped_node_ids:
        raise ValueError(
            "test attestation skipped node IDs differ from exact execute JUnit"
        )
    # The observed skips must each be declared.  A declared node that executed is fine and
    # expected: on a machine that holds the optional artifacts, fewer nodes skip.
    undeclared_skips = sorted(set(skipped_node_ids) - permitted_skip_node_ids)
    if undeclared_skips:
        raise ValueError(
            f"test attestation records skipped nodes that no declared class covers: "
            f"{undeclared_skips}"
        )
    for key in ("collected_count", "passed_count", "failed_count", "skipped_count"):
        if type(document[key]) is not int or document[key] < 0:
            raise ValueError(f"test attestation {key} is malformed")
    # Order matters: the skip accounting is checked before the passed/skipped total so a
    # tampered skip count reports the skip problem rather than a generic count mismatch.
    if document["collected_count"] != len(node_ids):
        raise ValueError("test attestation node/count reconciliation failed")
    if document["failed_count"] != 0:
        raise ValueError(
            "test attestation contains failed or skipped nodes that are not declared skips"
        )
    if document["skipped_count"] != len(skipped_node_ids):
        raise ValueError(
            "test attestation failed or skipped counts do not reconcile with the "
            "recorded skipped node IDs"
        )
    if document["passed_count"] + document["skipped_count"] != len(node_ids):
        raise ValueError("test attestation node/count reconciliation failed")
    if schema_version == 3:
        assert execute_junit is not None
        if (
            document["collected_count"] != execute_junit["tests_count"]
            or document["passed_count"] != execute_junit["passed_count"]
            or document["skipped_count"] != execute_junit["skipped_count"]
            or document["failed_count"]
            != execute_junit["failures_count"] + execute_junit["errors_count"]
        ):
            raise ValueError("test attestation counts differ from exact execute JUnit")

    hashes = document["input_sha256"]
    expected_paths = (*_ATTESTED_TEST_FILES, *_ATTESTATION_CONFIG_INPUTS)
    if not isinstance(hashes, Mapping) or set(hashes) != set(expected_paths):
        raise ValueError("test attestation input-hash closure is incomplete")
    for path in expected_paths:
        if hashes[path] != source_entries.get(path):
            raise ValueError(f"test attestation input hash mismatch for {path}")


def environment_attestation() -> dict:
    """Interpreter, platform, byte order, and BLAS identity."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            np.show_config()
        except Exception as exc:  # pragma: no cover - platform dependent
            buffer.write(f"<unavailable: {exc}>")
    versions = {"numpy": np.__version__}
    for module_name in ("scipy", "pytest", "yaml", "matplotlib"):
        try:
            versions[module_name] = __import__(module_name).__version__
        except Exception:  # pragma: no cover - environment dependent
            versions[module_name] = "<not-installed>"
    return {
        "schema_version": 1,
        "python_version": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "byte_order": sys.byteorder,
        "versions": versions,
        "numpy_show_config": buffer.getvalue(),
    }


def _conda_executable() -> str:
    """Resolve Conda from its configured executable, with a PATH fallback."""
    configured = os.environ.get("CONDA_EXE", "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        if not configured_path.is_file():
            raise RuntimeError(f"CONDA_EXE does not name a file: {configured_path}")
        return str(configured_path)

    discovered = shutil.which("conda")
    if discovered:
        return discovered
    raise RuntimeError(
        "cannot locate Conda: CONDA_EXE is unset and 'conda' is not on PATH"
    )


def _validate_conda_explicit(text: str) -> None:
    """Require the characteristic non-empty output of ``conda list --explicit``."""
    if type(text) is not str or not text.strip():
        raise RuntimeError("conda list --explicit produced empty output")
    lines = [line.strip() for line in text.splitlines()]
    if lines.count("@EXPLICIT") != 1:
        raise RuntimeError("conda list --explicit output has no unique @EXPLICIT marker")
    marker_index = lines.index("@EXPLICIT")
    package_records = [
        line for line in lines[marker_index + 1 :] if line and not line.startswith("#")
    ]
    if not package_records:
        raise RuntimeError("conda list --explicit output contains no package records")
    if any(line.startswith("<unavailable") for line in lines):
        raise RuntimeError("conda list --explicit output contains an unavailable placeholder")


def conda_explicit(
    env_name: str = "radar-vitals",
    *,
    command_runner=subprocess.run,
) -> str:
    """Return a validated explicit Conda lockfile, failing closed on any error."""
    executable = _conda_executable()
    try:
        completed = command_runner(
            [executable, "list", "--explicit", "-n", env_name],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            f"cannot produce authoritative Conda lockfile for {env_name!r}"
        ) from exc
    # ``subprocess.run(check=True)`` raises here, but injected runners and wrappers may
    # ignore that keyword.  The scientific contract is the observed process status.
    if type(completed.returncode) is not int or completed.returncode != 0:
        raise RuntimeError(
            f"cannot produce authoritative Conda lockfile for {env_name!r}: "
            f"process exit status {completed.returncode!r}"
        )
    output = completed.stdout
    _validate_conda_explicit(output)
    return output
