"""Validate M2 prospective artifacts or create the isolated synthetic dry run."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.m2.preflight import build_synthetic_dry_run, validate_preflight


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--manifest", type=Path)
    group.add_argument("--dry-run", type=Path, metavar="OUTPUT_DIR")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--reference-sensitivity", type=Path, default=None)
    arguments = parser.parse_args()
    if arguments.dry_run is not None:
        report = build_synthetic_dry_run(arguments.dry_run)
    else:
        report = validate_preflight(
            arguments.manifest,
            root=arguments.root,
            reference_sensitivity_path=arguments.reference_sensitivity,
        )
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
