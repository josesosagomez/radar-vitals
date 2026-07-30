"""Capture registry with enforced radar/reference separation (plan sections 3.3, 4.2).

The Masimo reference is the thing the radar arms are ultimately compared against, so the
single most valuable leakage control is structural: **the radar stage is never handed a
Masimo path at all**. That is enforced here by type, not by convention — `RadarScope`
exposes no accessor that can reach the reference section, and `ReferenceScope` exposes no
accessor that can reach a raw ADC path.

A comment saying "don't read Masimo here" is not a control; a scope object that has no
method to do so is. Tests additionally assert that no Masimo path string is reachable from
a `RadarScope` instance.

Every hash in the YAML is transcribed from the approved plan rather than measured, because
plan section 8 step 3 forbids opening a real capture during implementation. `verify_*`
resolves and checks them at execution time, and any mismatch is fatal.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml

__all__ = [
    "CaptureRegistry",
    "RadarCapture",
    "RadarScope",
    "ReferenceScope",
    "load_registry",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = REPO_ROOT / "experiments" / "m8_ahmed_transfer" / "capture_registry.yaml"

MASIMO_KEYS = ("masimo_sha256",)


@dataclass(frozen=True)
class RadarCapture:
    capture_id: str
    directory: str
    frames: int
    windows: int
    tail_frames: int
    recorded_lock: int
    rerun_lock: int
    capture_config_sha256: str
    adc_stream_sha256: str
    metadata_sha256: str
    warmup_sha256: str

    def adc_path(self, root: Path) -> Path:
        return Path(root) / self.directory / "adc_stream.bin"

    def metadata_path(self, root: Path) -> Path:
        return Path(root) / self.directory / "run_metadata.json"


@dataclass(frozen=True)
class RadarScope:
    """Everything the radar stage may see. Contains no reference information."""

    root: Path
    captures: Mapping[str, RadarCapture]
    geometry: Mapping[str, object]
    window_grid: Mapping[str, int]

    def capture(self, capture_id: str) -> RadarCapture:
        if capture_id not in self.captures:
            raise KeyError(f"unknown capture {capture_id!r}")
        return self.captures[capture_id]

    def total_windows(self) -> int:
        return sum(c.windows for c in self.captures.values())

    def evaluation_windows(self) -> int:
        """k>=1 only: k=0 selected both locks and is in-sample (plan section 3.2)."""
        return self.total_windows() - len(self.captures)


@dataclass(frozen=True)
class ReferenceScope:
    """Everything the scoring stage may see. Contains no raw ADC path."""

    root: Path
    masimo_sha256: Mapping[str, str]
    protocol: Mapping[str, str]
    strata: Mapping[str, tuple[str, ...]]

    def digest(self, capture_id: str) -> str:
        if capture_id not in self.masimo_sha256:
            raise KeyError(f"unknown capture {capture_id!r}")
        return self.masimo_sha256[capture_id]

    def stratum_of(self, capture_id: str) -> str:
        for name, members in self.strata.items():
            if capture_id in members:
                return name
        raise KeyError(f"capture {capture_id!r} is in no protocol stratum")


@dataclass(frozen=True)
class CaptureRegistry:
    """Holds both sections but hands out only one at a time."""

    registry_id: str
    root: Path
    _radar: RadarScope
    _reference: ReferenceScope

    def radar_scope(self) -> RadarScope:
        return self._radar

    def reference_scope(self) -> ReferenceScope:
        return self._reference

    @property
    def capture_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._radar.captures))


def _require(mapping: Mapping, key: str, context: str):
    if key not in mapping:
        raise ValueError(f"{context}: missing required key {key!r}")
    return mapping[key]


def load_registry(path: Path | None = None, *, root: Path | None = None) -> CaptureRegistry:
    """Parse and validate the registry. Structural problems are fatal, never warnings."""
    path = Path(path or DEFAULT_REGISTRY)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise ValueError(f"{path}: registry must be a mapping")

    resolved_root = Path(root) if root is not None else REPO_ROOT / _require(
        document, "root", str(path)
    )
    radar_raw = _require(document, "radar", str(path))
    reference_raw = _require(document, "reference", str(path))
    grid = _require(document, "window_grid", str(path))

    if set(radar_raw) != set(reference_raw):
        raise ValueError(
            f"{path}: radar and reference cover different captures: "
            f"{sorted(set(radar_raw) ^ set(reference_raw))}"
        )

    captures: dict[str, RadarCapture] = {}
    for capture_id, entry in radar_raw.items():
        # A stray reference key inside the radar section would defeat the separation.
        leaked = [k for k in entry if k in MASIMO_KEYS or "masimo" in k.lower()]
        if leaked:
            raise ValueError(
                f"{path}: radar entry {capture_id!r} contains reference keys {leaked}; "
                "the radar stage must never be able to reach a Masimo path"
            )
        captures[capture_id] = RadarCapture(
            capture_id=capture_id,
            directory=str(_require(entry, "directory", capture_id)),
            frames=int(_require(entry, "frames", capture_id)),
            windows=int(_require(entry, "windows", capture_id)),
            tail_frames=int(_require(entry, "tail_frames", capture_id)),
            recorded_lock=int(_require(entry, "recorded_lock", capture_id)),
            rerun_lock=int(_require(entry, "rerun_lock", capture_id)),
            capture_config_sha256=str(_require(entry, "capture_config_sha256", capture_id)),
            adc_stream_sha256=str(_require(entry, "adc_stream_sha256", capture_id)),
            metadata_sha256=str(_require(entry, "metadata_sha256", capture_id)),
            warmup_sha256=str(_require(entry, "warmup_sha256", capture_id)),
        )

    frames_per_window = int(_require(grid, "frames_per_window", "window_grid"))
    for capture in captures.values():
        expected_windows, expected_tail = divmod(capture.frames, frames_per_window)
        if expected_windows != capture.windows or expected_tail != capture.tail_frames:
            raise ValueError(
                f"{capture.capture_id}: {capture.frames} frames give "
                f"{expected_windows} windows / {expected_tail} tail, but the registry "
                f"declares {capture.windows} / {capture.tail_frames}"
            )

    declared_total = int(_require(grid, "total_windows", "window_grid"))
    actual_total = sum(c.windows for c in captures.values())
    if declared_total != actual_total:
        raise ValueError(
            f"{path}: window_grid.total_windows={declared_total} but the per-capture "
            f"windows sum to {actual_total}"
        )
    declared_eval = int(_require(grid, "evaluation_k_ge_1_windows", "window_grid"))
    if declared_eval != actual_total - len(captures):
        raise ValueError(
            f"{path}: evaluation_k_ge_1_windows={declared_eval} but dropping k=0 from "
            f"{len(captures)} captures leaves {actual_total - len(captures)}"
        )

    strata = {
        name: tuple(members)
        for name, members in (document.get("strata") or {}).items()
    }
    covered = {c for members in strata.values() for c in members}
    if covered != set(captures):
        raise ValueError(
            f"{path}: strata cover {sorted(covered)}, captures are {sorted(captures)}"
        )

    return CaptureRegistry(
        registry_id=str(_require(document, "registry_id", str(path))),
        root=resolved_root,
        _radar=RadarScope(
            root=resolved_root,
            captures=captures,
            geometry=dict(_require(document, "geometry", str(path))),
            window_grid={k: int(v) for k, v in grid.items()},
        ),
        _reference=ReferenceScope(
            root=resolved_root,
            masimo_sha256={
                cid: str(_require(entry, "masimo_sha256", cid))
                for cid, entry in reference_raw.items()
            },
            protocol=dict(document.get("protocol") or {}),
            strata=strata,
        ),
    )
