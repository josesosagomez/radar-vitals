"""Narrow schema-version dispatcher preserving historical v2 semantics."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .manifest_v3 import ManifestError, Mode, load_manifest_v3

# Prospective cohort identities (P001-P015) as subject IDs or session-ID prefixes.
_PROSPECTIVE_IDENTITY = re.compile(r"^P\d{3}(_|$)", re.IGNORECASE)


def _reject_prospective_v2_sessions(document: dict) -> None:
    """v2 has no settle or protocol-compliance contract, so P-subjects must use v3."""
    sessions = document.get("sessions")
    if not isinstance(sessions, list):
        return  # the v2 loader reports the malformed document itself
    for session in sessions:
        if not isinstance(session, dict):
            continue
        for key in ("subject_id", "session_id"):
            value = session.get(key)
            if isinstance(value, str) and _PROSPECTIVE_IDENTITY.match(value):
                raise ManifestError(
                    f"{key}={value!r}: prospective sessions must use manifest v3; "
                    "v2 carries no settle or protocol-compliance contract"
                )


def load_manifest(path: str | Path, mode: Mode | str, *, root: str | Path | None = None):
    manifest_path = Path(path)
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"cannot read manifest {manifest_path}: {exc}") from exc
    if type(document) is not dict:
        raise ManifestError("manifest must be a JSON object")
    version = document.get("manifest_schema_version")
    if type(version) is not int:
        raise ManifestError("manifest_schema_version must be an exact integer")
    if version == 3:
        return load_manifest_v3(manifest_path, mode, root=root)
    if version == 2:
        _reject_prospective_v2_sessions(document)
        from src.m4.manifest import Mode as V2Mode
        from src.m4.manifest import load_manifest as load_manifest_v2

        try:
            v2_mode = V2Mode(mode.value if isinstance(mode, Mode) else mode)
        except ValueError:
            raise ManifestError(f"invalid v2 mode {mode!r}") from None
        return load_manifest_v2(manifest_path, v2_mode, root=root)
    raise ManifestError(
        f"manifest_schema_version={version!r} is unsupported; only exact v2/v3 dispatch exists"
    )
