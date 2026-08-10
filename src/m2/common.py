"""Small deterministic file and JSON helpers shared by the M2 contracts."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping


SHA256_HEX_LENGTH = 64


class ContractError(ValueError):
    """Input or artifact state violates a frozen M2 engineering contract."""


def canonical_json_bytes(value: Any, *, trailing_newline: bool = True) -> bytes:
    """Return the one canonical JSON representation used by M2 hash identities."""
    try:
        text = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ContractError(f"value cannot be represented as canonical JSON: {exc}") from exc
    return (text + ("\n" if trailing_newline else "")).encode("utf-8")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_sha256(value: object, field_name: str) -> str:
    if (
        type(value) is not str
        or len(value) != SHA256_HEX_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ContractError(
            f"{field_name} must be a lowercase 64-character SHA-256 digest"
        )
    return value


def require_bool(mapping: Mapping[str, object], key: str) -> bool:
    value = mapping.get(key)
    if type(value) is not bool:
        raise ContractError(f"{key} must be a JSON Boolean, got {value!r}")
    return value


def require_int(
    mapping: Mapping[str, object], key: str, *, minimum: int | None = None
) -> int:
    value = mapping.get(key)
    if type(value) is not int:
        raise ContractError(f"{key} must be an exact integer, got {value!r}")
    if minimum is not None and value < minimum:
        raise ContractError(f"{key} must be >= {minimum}, got {value}")
    return value


def require_number(
    mapping: Mapping[str, object], key: str, *, minimum: float | None = None
) -> float:
    value = mapping.get(key)
    if type(value) not in (int, float):
        raise ContractError(f"{key} must be a JSON number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ContractError(f"{key} must be finite")
    if minimum is not None and number < minimum:
        raise ContractError(f"{key} must be >= {minimum}, got {number}")
    return number


def require_nonempty_string(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if type(value) is not str or not value.strip():
        raise ContractError(f"{key} must be a non-empty string, got {value!r}")
    return value


def read_json_object(path: str | Path) -> dict[str, Any]:
    file_path = Path(path)
    try:
        value = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"{file_path}: cannot read JSON object ({exc})") from exc
    if type(value) is not dict:
        raise ContractError(f"{file_path}: expected a JSON object")
    return value


def write_new_bytes(path: str | Path, content: bytes) -> Path:
    """Create one immutable artifact; an existing target is never overwritten."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with file_path.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError as exc:
        raise ContractError(f"refusing to overwrite immutable artifact {file_path}") from exc
    return file_path


def write_new_json(path: str | Path, value: Any) -> Path:
    return write_new_bytes(path, canonical_json_bytes(value))


def resolve_bound_path(root: str | Path, relative_path: object, field_name: str) -> Path:
    if type(relative_path) is not str or not relative_path.strip():
        raise ContractError(f"{field_name} must be a non-empty relative path")
    root_path = Path(root).resolve()
    candidate = (root_path / relative_path).resolve()
    if candidate != root_path and root_path not in candidate.parents:
        raise ContractError(f"{field_name} resolves outside artifact root {root_path}")
    return candidate
