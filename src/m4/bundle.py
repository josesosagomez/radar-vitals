"""Immutable, parent-linked stage bundles (M8 Step 1b plan section 5.2).

Every stage writes a new directory through a same-filesystem staging path and then renames
it atomically, so a reader never sees a half-written bundle and a finalized directory is
never reopened.

Identity rules that matter downstream:

* the **manifest's own bytes** are the stage identity — parents and children reference each
  other by `sha256(manifest.json)`. The manifest lists every payload with its size and
  digest but excludes itself, so the structure is acyclic. There is no self-referential
  `bundle.json` (Step 1a had one; it cannot be hashed without a fixed point);
* `LATEST.json` lives *outside* the run directory, so publishing a pointer never mutates an
  immutable bundle;
* only a `complete` bundle whose scoped sources were committed and clean may publish
  `LATEST.json`. A dirty or untracked scoped source makes the run useful draft evidence but
  **not** promotion-eligible, which is stricter than merely being complete.

JSON is strict throughout: UTF-8, sorted keys, and `allow_nan=False`, so a NaN or Infinity
raises rather than silently producing invalid JSON that a strict reader would reject.
Absent numeric values must be passed as `None`, never as NaN.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil
from typing import Any, Literal, Mapping

import numpy as np

__all__ = [
    "BundleWriter",
    "StageBundle",
    "new_run_id",
    "publish_latest",
    "read_manifest",
    "sha256_bytes",
    "sha256_path",
    "strict_json_bytes",
    "verify_bundle",
]

BundleStatus = Literal["running", "complete", "failed", "incomplete_abandoned"]
MANIFEST_NAME = "manifest.json"
LATEST_NAME = "LATEST.json"
SCHEMA_VERSION = 1


def sha256_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    digest = sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_safe(value: Any) -> Any:
    """Convert NumPy scalars/arrays to plain values; leave non-finite floats to fail."""
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, np.ndarray):
        return [_json_safe(v) for v in value.tolist()]
    if isinstance(value, np.generic):
        return _json_safe(value.item())
    if isinstance(value, (bool, int, str)) or value is None:
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Path):
        return value.as_posix()
    raise TypeError(f"cannot serialize {type(value).__name__} to strict JSON")


def strict_json_bytes(payload: Mapping[str, Any]) -> bytes:
    """UTF-8, sorted keys, no NaN/Infinity. Raises on a non-finite float."""
    text = json.dumps(
        _json_safe(payload),
        allow_nan=False,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return (text + "\n").encode("utf-8")


def new_run_id(short_hash: str) -> str:
    """Timestamped, collision-resistant, and sortable — matches the Step 1a convention."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{stamp}_{short_hash[:12]}"


@dataclass(frozen=True)
class StageBundle:
    stage: str
    run_id: str
    root: Path
    status: BundleStatus
    manifest_sha256: str
    promotion_eligible: bool


@dataclass
class BundleWriter:
    """Accumulates payloads in a staging directory, then finalizes atomically."""

    stage_root: Path
    stage: str
    run_id: str
    _payloads: dict[str, bytes] = field(default_factory=dict, init=False)
    _finalized: bool = field(default=False, init=False)

    @property
    def staging(self) -> Path:
        return self.stage_root / f".staging-{self.run_id}"

    @property
    def destination(self) -> Path:
        return self.stage_root / self.run_id

    def _guard(self, name: str) -> None:
        if self._finalized:
            raise RuntimeError("bundle already finalized; bundles are immutable")
        if name == MANIFEST_NAME:
            raise ValueError("the manifest is written by finalize(), not added")
        if name == LATEST_NAME:
            raise ValueError("LATEST.json lives outside the run directory")
        if name in self._payloads:
            raise ValueError(f"payload {name!r} already added")
        if Path(name).name != name:
            raise ValueError(f"payload name {name!r} must not contain a path separator")

    def add_json(self, name: str, payload: Mapping[str, Any]) -> None:
        self._guard(name)
        self._payloads[name] = strict_json_bytes(payload)

    def add_text(self, name: str, text: str) -> None:
        self._guard(name)
        self._payloads[name] = text.encode("utf-8")

    def add_npz(self, name: str, arrays: Mapping[str, np.ndarray]) -> None:
        """Store arrays with no object dtype, loadable with allow_pickle=False."""
        self._guard(name)
        import io

        for key, array in arrays.items():
            values = np.asarray(array)
            if values.dtype == object:
                raise TypeError(f"{name}:{key} has object dtype; use an explicit codebook")
        buffer = io.BytesIO()
        np.savez(buffer, **{k: np.asarray(v) for k, v in arrays.items()})
        self._payloads[name] = buffer.getvalue()

    def add_file(self, name: str, source: Path) -> None:
        self._guard(name)
        self._payloads[name] = Path(source).read_bytes()

    def finalize(
        self,
        *,
        status: BundleStatus,
        provenance: Mapping[str, Any],
        promotion_eligible: bool,
        parents: Mapping[str, str] | None = None,
        extra_manifest: Mapping[str, Any] | None = None,
    ) -> StageBundle:
        """Write every payload plus the manifest, then rename into place atomically."""
        if self._finalized:
            raise RuntimeError("bundle already finalized")
        if status == "running":
            raise ValueError("finalize() requires a terminal status")
        if not self._payloads:
            raise ValueError("refusing to finalize an empty bundle")

        self.stage_root.mkdir(parents=True, exist_ok=True)
        if self.destination.exists():
            raise FileExistsError(f"run directory already exists: {self.destination}")
        if self.staging.exists():
            shutil.rmtree(self.staging)

        try:
            self.staging.mkdir(parents=True)
            if "provenance.json" in self._payloads:
                raise ValueError(
                    "provenance.json is written by finalize(); pass it as `provenance`"
                )
            for name, data in sorted(self._payloads.items()):
                (self.staging / name).write_bytes(data)
            (self.staging / "provenance.json").write_bytes(strict_json_bytes(provenance))
            payload_names = sorted(set(self._payloads) | {"provenance.json"})
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "stage": self.stage,
                "run_id": self.run_id,
                "status": status,
                "promotion_eligible": bool(promotion_eligible),
                "parents": dict(parents or {}),
                "payloads": {
                    name: {
                        "size_bytes": (self.staging / name).stat().st_size,
                        "sha256": sha256_path(self.staging / name),
                    }
                    for name in payload_names
                },
                **dict(extra_manifest or {}),
            }
            manifest_bytes = strict_json_bytes(manifest)
            (self.staging / MANIFEST_NAME).write_bytes(manifest_bytes)
            self.staging.replace(self.destination)
        except BaseException:
            if self.staging.exists():
                shutil.rmtree(self.staging, ignore_errors=True)
            raise

        self._finalized = True
        return StageBundle(
            stage=self.stage,
            run_id=self.run_id,
            root=self.destination,
            status=status,
            manifest_sha256=sha256_bytes(manifest_bytes),
            promotion_eligible=bool(promotion_eligible),
        )


def read_manifest(run_dir: Path) -> tuple[dict, str]:
    """Return (manifest, sha256 of its exact bytes)."""
    data = (Path(run_dir) / MANIFEST_NAME).read_bytes()
    return json.loads(data.decode("utf-8")), sha256_bytes(data)


def verify_bundle(run_dir: Path) -> dict:
    """Re-hash every declared payload and reject extras. Raises on any mismatch."""
    run_dir = Path(run_dir)
    manifest, _digest = read_manifest(run_dir)
    declared = manifest["payloads"]
    on_disk = {p.name for p in run_dir.iterdir() if p.is_file()} - {MANIFEST_NAME}
    if on_disk != set(declared):
        raise ValueError(
            f"payload set mismatch in {run_dir}: on disk {sorted(on_disk)}, "
            f"declared {sorted(declared)}"
        )
    for name, expected in declared.items():
        path = run_dir / name
        actual = sha256_path(path)
        if actual != expected["sha256"]:
            raise ValueError(f"{name}: sha256 {actual} != declared {expected['sha256']}")
        if path.stat().st_size != expected["size_bytes"]:
            raise ValueError(f"{name}: size mismatch")
    return manifest


def publish_latest(stage_root: Path, bundle: StageBundle) -> Path:
    """Point LATEST.json at a complete, promotion-eligible bundle. Never mutates it."""
    if bundle.status != "complete":
        raise ValueError(f"refusing to publish LATEST for status {bundle.status!r}")
    if not bundle.promotion_eligible:
        raise ValueError(
            "refusing to publish LATEST for a promotion-ineligible bundle; "
            "dirty or untracked scoped sources make this draft evidence only"
        )
    latest = Path(stage_root) / LATEST_NAME
    payload = {
        "run_id": bundle.run_id,
        "stage": bundle.stage,
        "manifest_sha256": bundle.manifest_sha256,
    }
    temporary = latest.with_suffix(".json.tmp")
    temporary.write_bytes(strict_json_bytes(payload))
    temporary.replace(latest)
    return latest
