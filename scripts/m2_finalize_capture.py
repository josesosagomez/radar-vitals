"""Finalize one sealed M2 radar receipt after reference export and end-clock measurement."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.m2.capture_artifacts import finalize_capture


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--finalization-sidecar", type=Path, required=True)
    parser.add_argument("--cohort-registry", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--reference", type=Path, default=None)
    arguments = parser.parse_args()
    metadata = yaml.safe_load(arguments.finalization_sidecar.read_text(encoding="utf-8"))
    manifest_path = finalize_capture(
        arguments.run_dir,
        finalization_metadata=metadata,
        cohort_registry_path=arguments.cohort_registry,
        destination_dir=arguments.destination,
        reference_path=arguments.reference,
    )
    print(json.dumps({"status": "finalized", "manifest": str(manifest_path)}, indent=2))


if __name__ == "__main__":
    main()
