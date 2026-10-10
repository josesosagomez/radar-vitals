"""Verify replay artifacts against the registered ADC and Masimo inputs."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--development-data-root", type=Path, required=True)
    args = parser.parse_args(argv)

    from src.replay_compare.reference import PassiveReferenceTimeline
    from src.replay_compare.session import (
        SUPPORTED_CAPTURE_ID,
        resolve_replay_session,
    )
    from src.replay_compare.verification import verify_replay_with_source
    from src.replay_compare.source import ReplaySourceError

    try:
        metadata = json.loads(
            (args.run_dir / "run_metadata.json").read_text(encoding="utf-8")
        )
        mode = metadata.get("mode", "baseline")
        if mode not in {"baseline", "motion"}:
            raise ValueError(f"unsupported replay mode {mode!r}")
        spec = resolve_replay_session(
            SUPPORTED_CAPTURE_ID,
            development_data_root=args.development_data_root,
            mode=mode,
            allow_legacy_validity_assumption=(mode == "motion"),
        )
        reference = PassiveReferenceTimeline.load(
            spec.capture_dir,
            development_data_root=args.development_data_root,
            anchor_utc_us=spec.anchor_utc_us,
            frame_rate_hz=spec.frame_rate_hz,
        )
        report = verify_replay_with_source(
            args.run_dir,
            spec,
            reference_timeline=reference,
        )
    except (
        OSError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        ReplaySourceError,
    ) as exc:
        print(json.dumps({
            "accepted": False,
            "source_binding_complete": False,
            "issues": [{
                "code": "verification_inputs_unavailable",
                "details": f"{type(exc).__name__}: {exc}",
            }],
        }, indent=2, sort_keys=True))
        return 2

    result = {
        "accepted": report.accepted,
        "mode": report.mode,
        "source_binding_complete": report.source_binding_complete,
        "internal_issues": [asdict(issue) for issue in report.internal_issues],
        "source_issues": [asdict(issue) for issue in report.source_issues],
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if report.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
