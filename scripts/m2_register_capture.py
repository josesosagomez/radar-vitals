"""Append a sealed radar receipt to the immutable M2 cohort registry history."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.m2.cohort_registry import register_captured_session  # noqa: E402
from src.m2.common import ContractError, sha256_file  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a sealed M2 radar receipt and append its captured state/hash "
            "to a new no-overwrite cohort-registry revision."
        )
    )
    parser.add_argument("--registry", required=True, type=Path, help="Latest registry JSON")
    parser.add_argument("--receipt", required=True, type=Path, help="Sealed radar receipt JSON")
    parser.add_argument(
        "--output-registry", required=True, type=Path, help="New registry revision JSON"
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        written = register_captured_session(
            args.registry, args.output_registry, args.receipt
        )
    except ContractError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "registry_path": str(written.resolve()),
                "registry_sha256": sha256_file(written),
                "receipt_sha256": sha256_file(args.receipt),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
