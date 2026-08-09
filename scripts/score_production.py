#!/usr/bin/env python
"""Canonical M1 scoring for the unchanged production ECA+AHET estimator.

The command consumes one immutable M4 radar parent and routes it through
``src.m4.estimator_scoring.run_score_stage``.  It adds no estimator path and never edits
or selects rows manually.  Canonical execution fails before reference access unless the
whole repository tree is clean and the committed source manifest, authorization, radar
parent, raw hashes, configuration hashes and reference hashes all verify.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Callable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.m4.bundle import new_run_id, read_manifest  # noqa: E402
from src.m4.capture_registry import load_registry  # noqa: E402
from src.m4.estimator_runner import (  # noqa: E402
    verify_gate_bundle,
    verify_repository_authorization,
)
from src.m4.estimator_scoring import (  # noqa: E402
    PRODUCTION_ALL_WINDOWS_UNIVERSE,
    PRODUCTION_PERSISTED_LOCK_UNIVERSE,
    run_score_stage,
)
from src.m8.ahmed_provenance import (  # noqa: E402
    load_source_manifest,
    verify_source_manifest,
)

DEFAULT_GATE_ROOT = REPO_ROOT / "results" / "m8_ahmed_transfer" / "synthetic"
DEFAULT_RADAR_ROOT = REPO_ROOT / "results" / "m8_ahmed_transfer" / "radar"
DEFAULT_OUT = REPO_ROOT / "results" / "production_eca_ahet" / "scored"
DEFAULT_AUTHORIZATION = (
    REPO_ROOT
    / "experiments"
    / "m8_ahmed_transfer"
    / "authorizations"
    / "real_evaluation_20260808.yaml"
)


def require_clean_tree_commit(
    root: Path = REPO_ROOT,
    *,
    command_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> str:
    """Return HEAD only when the entire Git tree is clean; otherwise fail closed."""
    status = command_runner(
        ["git", "status", "--porcelain", "--untracked-files=all", "-z"],
        cwd=str(root),
        check=True,
        capture_output=True,
        text=True,
    )
    if status.stdout:
        paths = []
        for entry in status.stdout.split("\0"):
            if entry:
                paths.append(entry[3:] if len(entry) >= 4 else entry)
        raise RuntimeError(
            "canonical production scoring requires a clean Git tree; dirty paths: "
            + ", ".join(paths[:10])
        )
    head = command_runner(
        ["git", "rev-parse", "HEAD"],
        cwd=str(root),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if len(head) != 40 or any(char not in "0123456789abcdef" for char in head):
        raise RuntimeError("cannot resolve a canonical lowercase 40-character Git commit")
    return head


def _latest_bundle(stage_root: Path, expected_stage: str) -> Path:
    pointer = stage_root / "LATEST.json"
    document = json.loads(pointer.read_text(encoding="utf-8"))
    if document.get("stage") != expected_stage or type(document.get("run_id")) is not str:
        raise ValueError(f"malformed {expected_stage} LATEST pointer: {pointer}")
    bundle = stage_root / document["run_id"]
    _manifest, digest = read_manifest(bundle)
    if digest != document.get("manifest_sha256"):
        raise ValueError(f"{expected_stage} LATEST pointer digest mismatch")
    return bundle


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gate", type=Path)
    parser.add_argument("--authorization", type=Path, default=DEFAULT_AUTHORIZATION)
    parser.add_argument("--radar-parent", type=Path)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    # This is deliberately first.  A dirty development tree must not produce a
    # reference-derived artifact, even if all immutable parents happen to be valid.
    git_commit = require_clean_tree_commit()
    gate = args.gate or _latest_bundle(DEFAULT_GATE_ROOT, "synthetic")
    radar_parent = args.radar_parent or _latest_bundle(DEFAULT_RADAR_ROOT, "radar")
    verify_gate_bundle(gate)
    source = load_source_manifest(gate / "source_manifest.json")
    verify_source_manifest(source, require_promotion_eligible=True)
    if source.git_commit != git_commit:
        raise RuntimeError(
            f"source manifest commit {source.git_commit} does not equal clean HEAD {git_commit}"
        )

    registry = load_registry()
    run_id = new_run_id(source.manifest_sha256)
    bundle = run_score_stage(
        gate_dir=gate,
        authorization_path=args.authorization,
        source_manifest_sha256=source.manifest_sha256,
        radar_dir=radar_parent,
        reference=registry.reference_scope(),
        run_id=run_id,
        out_root=args.out,
        authorization_validator=verify_repository_authorization,
        require_scientific_gate=True,
        require_production_provenance=True,
    )

    summary = json.loads(
        (bundle.root / "production_summary.json").read_text(encoding="utf-8")
    )["production_audit"]
    full = summary["universes"][PRODUCTION_ALL_WINDOWS_UNIVERSE]["micro"]
    persisted = summary["universes"][PRODUCTION_PERSISTED_LOCK_UNIVERSE]["micro"]
    print(f"run_id                  : {bundle.run_id}")
    print(f"manifest_sha256         : {bundle.manifest_sha256}")
    print(f"clean_git_commit        : {git_commit}")
    print(
        "all-window radar coverage: "
        f"{full['n_radar_valid']}/{full['n_source']} = {full['radar_coverage']:.6f}"
    )
    print(
        "joint given reference    : "
        f"{full['n_joint']}/{full['n_reference_admitted']} = "
        f"{full['joint_given_reference']:.6f}"
    )
    print(
        "k>=1 radar coverage      : "
        f"{persisted['n_radar_valid']}/{persisted['n_source']} = "
        f"{persisted['radar_coverage']:.6f}"
    )
    print("k=0 remains in the ledger and is reported separately.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
