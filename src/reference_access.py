"""Single guarded entry point for Masimo reference discovery and loading."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from .m2.common import ContractError, require_sha256, sha256_bytes
from .m2.label_firewall import (
    ReferenceOperation,
    ScoringAuthorization,
    guarded_reference_bytes,
)
from .masimo import load_masimo_bytes

if TYPE_CHECKING:
    from collections.abc import Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
DEVELOPMENT_REGISTRY_PATH = (
    REPO_ROOT / "reference_registry" / "development_references_v1.json"
)
_PROSPECTIVE_COMPONENT = re.compile(r"(?:^|_)P\d{3}(?:_|$)", re.IGNORECASE)


@dataclass(frozen=True)
class ReferenceSource:
    """Immutable identity attached to parsed reference samples."""

    source_kind: str
    capture_id: str
    subject_id: str
    resolved_path: str
    sha256: str


@dataclass(frozen=True)
class LoadedReference:
    frame: pd.DataFrame
    source: ReferenceSource


def _reject_protected_development_path(path: Path) -> None:
    parts = tuple(part.lower() for part in path.parts)
    joined = "/".join(parts)
    if "data/raw/prospective" in joined or "m2_capture_work" in parts:
        raise ContractError("prospective/sealed reference paths require scoring authorization")
    if any(_PROSPECTIVE_COMPONENT.search(part) for part in path.parts):
        raise ContractError("P001-P015 paths require scoring authorization")


def _load_development_registry() -> tuple[dict[str, object], ...]:
    try:
        document = json.loads(DEVELOPMENT_REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(
            f"cannot read committed development reference registry: {exc}"
        ) from exc
    if type(document) is not dict or document.get("schema") != "development_reference_registry_v1":
        raise ContractError("development reference registry has the wrong schema")
    entries = document.get("entries")
    if type(entries) is not list or not entries:
        raise ContractError("development reference registry entries must be a non-empty array")
    normalized: list[dict[str, object]] = []
    seen: set[str] = set()
    for entry in entries:
        if type(entry) is not dict or set(entry) != {
            "capture_id",
            "subject_id",
            "repository_relative_path",
            "sha256",
        }:
            raise ContractError("development reference registry entry has invalid fields")
        capture_id = entry.get("capture_id")
        subject_id = entry.get("subject_id")
        relative = entry.get("repository_relative_path")
        if not all(type(value) is str and value for value in (capture_id, subject_id, relative)):
            raise ContractError("development registry identities and paths must be strings")
        if capture_id in seen:
            raise ContractError(f"duplicate development capture identity {capture_id!r}")
        seen.add(capture_id)
        require_sha256(entry.get("sha256"), "sha256")
        normalized.append(dict(entry))
    return tuple(normalized)


def _resolve_registered_path(entry: "Mapping[str, object]") -> Path:
    relative = Path(str(entry["repository_relative_path"]))
    if relative.is_absolute() or ".." in relative.parts:
        raise ContractError("development reference registry path must be repository-relative")
    if relative.parts[:2] != ("results", "live_demo"):
        raise ContractError("development references must be registered under results/live_demo")
    try:
        resolved_root = REPO_ROOT.resolve(strict=True)
        resolved = (REPO_ROOT / relative).resolve(strict=True)
    except OSError as exc:
        raise ContractError(f"registered development reference is unavailable: {relative}") from exc
    if not resolved.is_relative_to(resolved_root):
        raise ContractError("registered development reference escapes the repository root")
    if resolved.parent.name != entry["capture_id"] or not resolved.is_file():
        raise ContractError("development registry capture identity/path binding is invalid")
    _reject_protected_development_path(resolved)
    return resolved


def list_development_capture_dirs() -> tuple[Path, ...]:
    """Return only committed, hash-bound development capture directories."""
    return tuple(_resolve_registered_path(entry).parent for entry in _load_development_registry())


def load_reference(
    capture_dir: str | Path,
    authorization: ScoringAuthorization | None = None,
    *,
    reference_override: str | Path | None = None,
    registry_path: str | Path | None = None,
    subject_id: str | None = None,
    operation: ReferenceOperation | str | None = None,
) -> LoadedReference:
    """Load a registered development reference or a capability-bound prospective one.

    Without an authorization, the capture directory must exactly match the committed
    development registry.  Prospective reads never discover files: callers must provide
    the exact capability-bound path and the existing firewall performs the first byte read.
    """
    requested = Path(capture_dir)
    if authorization is None:
        # This lexical rejection occurs before the registry or any CSV is opened.
        _reject_protected_development_path(requested)
        if reference_override is not None:
            _reject_protected_development_path(Path(reference_override))
        try:
            requested_dir = requested.resolve(strict=True)
        except OSError as exc:
            raise ContractError(f"capture directory is unavailable: {requested}") from exc
        if not requested_dir.is_dir():
            raise ContractError("reference access requires a capture directory")
        matches: list[tuple[dict[str, object], Path]] = []
        for entry in _load_development_registry():
            registered = _resolve_registered_path(entry)
            if registered.parent == requested_dir:
                matches.append((entry, registered))
        if len(matches) != 1:
            raise ContractError(
                "capture directory is not uniquely authorized by the committed development registry"
            )
        entry, reference_path = matches[0]
        if reference_override is not None:
            try:
                override = Path(reference_override).resolve(strict=True)
            except OSError as exc:
                raise ContractError("reference override is unavailable") from exc
            if override != reference_path:
                raise ContractError("reference override differs from the committed registry")
        content = reference_path.read_bytes()
        expected_hash = str(entry["sha256"])
        actual_hash = sha256_bytes(content)
        if actual_hash != expected_hash:
            raise ContractError(
                f"development reference hash mismatch: expected {expected_hash}, got {actual_hash}"
            )
        frame = load_masimo_bytes(content, source_name=reference_path.name)
        return LoadedReference(
            frame=frame,
            source=ReferenceSource(
                source_kind="development_registry",
                capture_id=str(entry["capture_id"]),
                subject_id=str(entry["subject_id"]),
                resolved_path=str(reference_path),
                sha256=actual_hash,
            ),
        )

    if reference_override is None or registry_path is None or operation is None:
        raise ContractError(
            "prospective reference access requires exact path, registry, operation, and capability"
        )
    authorized_subject = subject_id or authorization.subject_id
    content = guarded_reference_bytes(
        registry_path,
        authorized_subject,
        operation,
        reference_override,
        capability=authorization,
    )
    reference_path = Path(reference_override).resolve()
    actual_hash = sha256_bytes(content)
    frame = load_masimo_bytes(content, source_name=reference_path.name)
    return LoadedReference(
        frame=frame,
        source=ReferenceSource(
            source_kind="prospective_capability",
            capture_id=authorization.session_id,
            subject_id=authorized_subject,
            resolved_path=str(reference_path),
            sha256=actual_hash,
        ),
    )
