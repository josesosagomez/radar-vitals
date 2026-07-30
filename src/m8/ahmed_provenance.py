"""Scoped source and environment attestation for Step 1b (plan section 4.3).

The synthetic gate binds a hash of every file that could change its result. Any change to a
scoped source, config, test, or plan invalidates the gate and requires rerunning it, so the
scope must be *wide enough to be honest* and *narrow enough to be stable*.

`promotion_eligible` is stricter than "the run completed": it additionally requires every
scoped file to be tracked and clean at gate time. A dirty or untracked scoped file yields a
useful draft bundle that can never parent a real-data stage.

Authorization and continuation YAMLs are deliberately **outside** this scope — they are
hashed by the stage that consumes them. Including them here would make the gate's own hash
depend on decisions taken after the gate ran.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import platform
import subprocess
import sys
from typing import Iterable, Sequence

import numpy as np

from src.m4.bundle import sha256_bytes, sha256_path

__all__ = [
    "SourceManifest",
    "build_source_manifest",
    "environment_attestation",
    "git_status_paths",
    "git_text",
    "reference_paths",
    "scoped_paths",
]

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Individually named gating files. Missing entries are recorded, never silently skipped.
_NAMED_SCOPED = (
    "CLAUDE.md",
    "environment.yml",
    "pytest.ini",
    "setup.cfg",
    "pyproject.toml",
    "conftest.py",
    "tests/conftest.py",
    "scripts/live_demo_config.yaml",
    "experiments/m8_ahmed_fig8/config.yaml",
    "plans/m8_step1b_ahmed_transfer.md",
    "plans/m8_step1b_ahmed_transfer_addendum_a.md",
)

#: Hashed for provenance but **not** gating. Base plan section 4.3 scopes the Ahmed PDF
#: into the source manifest, while section 5.1's promotion rule speaks of "plan/source/
#: config/test" files. The PDF is none of those: it is a copyrighted external reference,
#: deliberately gitignored via `literature*/`, and it cannot be committed. Requiring it to
#: be tracked would deadlock the gate permanently.
#:
#: The distinction is real. An uncommitted *source* file means the code that ran is not
#: recoverable; an uncommitted *reference* is still fully identified by its hash and cannot
#: change what the code does. So the hash is recorded and pinned, and tracking status is
#: reported, but neither blocks promotion.
_NAMED_REFERENCE = (
    "literature/ref_papers/discovering_the_unseen_radar_vitals/"
    "Discovering_the_Unseen_Radar-Based_Estimation_of_Heartbeat_Breathing_Rate_and_"
    "Underlying_Muscle_Expansion_Without_Probes.pdf",
)

#: Glob trees included whole, tracked or not — an untracked source still changes results.
_SCOPED_TREES = ("src/**/*.py", "scripts/**/*.py", "tests/**/*.py")


def git_text(*args: str, cwd: Path | None = None) -> str:
    """Run git and return trimmed stdout.

    Do **not** use this for `status --porcelain`: the leading status column contains
    significant spaces that `.strip()` destroys. Use `git_status_paths` instead.
    """
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
    """Repo-relative paths git reports as changed or untracked.

    Uses `-z` so paths are literal and NUL-separated — no shell quoting to unpick — and
    `--untracked-files=all` so a new directory is not collapsed to a single `dir/` entry
    that would hide the files inside it.

    This must fail *closed*. An earlier version reused `git_text`, whose `.strip()` removed
    porcelain's leading status space; the subsequent `line[3:]` slice then truncated every
    path by two characters, so no file ever matched and a dirty tree reported as
    promotion-eligible.
    """
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
    fields = [f for f in completed.stdout.split("\0") if f]
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        paths.add(path)
        if "R" in status or "C" in status:
            # Rename/copy entries carry the original path as the next NUL field.
            if index < len(fields):
                paths.add(fields[index])
                index += 1
    return paths


def scoped_paths(root: Path | None = None) -> list[Path]:
    """Gating files: everything whose content can change the gate's result."""
    # Resolve the root as well as each path: on Windows a temp or symlinked root differs
    # from its resolved form, `relative_to` then fails, and entries silently fall back to
    # absolute machine-specific paths that would make the manifest unreproducible.
    root = Path(root or REPO_ROOT).resolve()
    found: set[Path] = set()
    for relative in _NAMED_SCOPED:
        candidate = root / relative
        if candidate.is_file():
            found.add(candidate.resolve())
    for pattern in _SCOPED_TREES:
        for candidate in root.glob(pattern):
            if candidate.is_file():
                found.add(candidate.resolve())
    return sorted(found)


def reference_paths(root: Path | None = None) -> list[Path]:
    """Non-gating external references, hashed for provenance only."""
    # Resolve the root as well as each path: on Windows a temp or symlinked root differs
    # from its resolved form, `relative_to` then fails, and entries silently fall back to
    # absolute machine-specific paths that would make the manifest unreproducible.
    root = Path(root or REPO_ROOT).resolve()
    return sorted(
        (root / relative).resolve()
        for relative in _NAMED_REFERENCE
        if (root / relative).is_file()
    )


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

    def to_dict(self) -> dict:
        return {
            "schema_version": 2,
            "entries": list(self.entries),
            "reference_entries": list(self.reference_entries),
            "manifest_sha256": self.manifest_sha256,
            "git_commit": self.git_commit,
            "git_branch": self.git_branch,
            "scoped_dirty": list(self.scoped_dirty),
            "scoped_untracked": list(self.scoped_untracked),
            "promotion_eligible": self.promotion_eligible,
            "scope_note": (
                "authorization and continuation YAMLs are intentionally outside this "
                "scope; they are hashed by the stage that consumes them"
            ),
            "reference_note": (
                "reference_entries are hashed for provenance but do not gate promotion: "
                "they are external documents that cannot change what the code does, and "
                "the Ahmed PDF is copyrighted and gitignored so it can never be tracked"
            ),
        }


def _relative(path: Path, root: Path) -> str:
    """Repo-relative posix path. A path outside the root is a scoping bug, not a fallback."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError(
            f"scoped path {path} lies outside root {root}; an absolute path in the "
            "source manifest would be machine-specific and unreproducible"
        ) from exc


def build_source_manifest(root: Path | None = None) -> SourceManifest:
    """Hash every scoped file and decide promotion eligibility from Git state."""
    # Resolve the root as well as each path: on Windows a temp or symlinked root differs
    # from its resolved form, `relative_to` then fails, and entries silently fall back to
    # absolute machine-specific paths that would make the manifest unreproducible.
    root = Path(root or REPO_ROOT).resolve()
    paths = scoped_paths(root)

    tracked = set(git_text("ls-files", cwd=root).splitlines())
    changed = git_status_paths(cwd=root)

    entries: list[dict] = []
    dirty: list[str] = []
    untracked: list[str] = []
    for path in paths:
        relative = _relative(path, root)
        is_tracked = relative in tracked
        is_changed = relative in changed
        entries.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_path(path),
                "tracked": is_tracked,
                "dirty": is_changed,
            }
        )
        if not is_tracked:
            untracked.append(relative)
        elif is_changed:
            dirty.append(relative)

    references: list[dict] = []
    for path in reference_paths(root):
        relative = _relative(path, root)
        references.append(
            {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_path(path),
                "tracked": relative in tracked,
                "gates_promotion": False,
            }
        )

    # The manifest's own digest covers the ordered (path, sha256) pairs of gating files and
    # references alike — a different paper must change the hash — but promotion depends
    # only on the gating set. Stable against incidental fields like size and ordering.
    spine = "\n".join(
        f"{e['path']}\t{e['sha256']}" for e in (*entries, *references)
    ).encode("utf-8")
    return SourceManifest(
        entries=tuple(entries),
        reference_entries=tuple(references),
        manifest_sha256=sha256_bytes(spine),
        git_commit=git_text("rev-parse", "HEAD", cwd=root),
        git_branch=git_text("branch", "--show-current", cwd=root),
        scoped_dirty=tuple(sorted(dirty)),
        scoped_untracked=tuple(sorted(untracked)),
        promotion_eligible=not dirty and not untracked,
    )


def environment_attestation() -> dict:
    """Interpreter, platform, byte order, and BLAS identity."""
    import io
    import contextlib

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
        except Exception:  # pragma: no cover
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


def conda_explicit(env_name: str = "radar-vitals") -> str:
    """`conda list --explicit` for the active environment, or a recorded failure."""
    try:
        completed = subprocess.run(
            ["conda", "list", "--explicit", "-n", env_name],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        return f"<unavailable: {type(exc).__name__}: {exc}>\n"
