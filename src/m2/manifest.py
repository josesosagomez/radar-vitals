"""Narrow schema-version dispatcher preserving historical v2 semantics."""
from __future__ import annotations

import json
from pathlib import Path

from .manifest_v3 import ManifestError, Mode, load_manifest_v3


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
