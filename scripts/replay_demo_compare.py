"""Run the registered massimo3 recording through the replay dashboard."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_CONFIG = ROOT / "scripts" / "replay_demo_compare_config.yaml"
SUPPORTED_SPEEDS = (0.5, 1.0, 2.0, 4.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--capture")
    parser.add_argument("--development-data-root", type=Path, required=True)
    parser.add_argument("--mode", choices=("baseline", "motion"))
    parser.add_argument("--motion-config", type=Path)
    parser.add_argument("--allow-legacy-validity-assumption", action="store_true")
    parser.add_argument("--speed", type=float, choices=SUPPORTED_SPEEDS)
    parser.add_argument("--duration-s", type=float)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args(argv)

    config = _load_config(args.config)
    capture = args.capture or str(config["capture"])
    mode = args.mode or str(config["mode"])
    speed = float(config["speed"] if args.speed is None else args.speed)
    if speed not in SUPPORTED_SPEEDS:
        parser.error(f"speed must be one of {SUPPORTED_SPEEDS}")
    if mode == "baseline" and args.allow_legacy_validity_assumption:
        parser.error("the legacy validity assumption is accepted only in motion mode")
    if mode == "baseline" and args.motion_config is not None:
        parser.error("--motion-config is accepted only in motion mode")
    if mode == "motion" and args.motion_config is None:
        parser.error("motion mode requires --motion-config")
    _require_clean_scientific_source()

    from src.replay_compare.application import create_replay_application
    from src.replay_compare.reference import PassiveReferenceTimeline
    from src.replay_compare.session import duration_frame_limit, resolve_replay_session
    from src.replay_compare.ui import run_dashboard

    spec = resolve_replay_session(
        capture,
        development_data_root=args.development_data_root,
        mode=mode,
        allow_legacy_validity_assumption=args.allow_legacy_validity_assumption,
    )
    limit = duration_frame_limit(
        args.duration_s,
        total_frames=spec.frame_count,
        frame_rate_hz=spec.frame_rate_hz,
    )
    output_parent = (
        args.output_root
        if args.output_root is not None
        else ROOT / str(config["output_root"])
    )
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    output_root = Path(output_parent).resolve() / run_id
    canonical_root = ROOT.resolve()
    lexical_results = ROOT / "results"
    canonical_results = canonical_root / "results"
    if lexical_results.resolve() != canonical_results:
        parser.error("this checkout's results directory is redirected")
    lexical_approved_output = lexical_results / "replay_compare"
    approved_output = canonical_results / "replay_compare"
    if (
        lexical_approved_output.exists()
        and lexical_approved_output.resolve() != approved_output
    ):
        parser.error("the fixed replay output directory is redirected")
    if not output_root.is_relative_to(approved_output):
        parser.error("replay outputs must stay under this checkout's results/replay_compare")

    if mode == "motion":
        from src.replay_compare.advanced import (
            AdvancedReplayUnavailable,
            authorize_advanced_replay,
        )
        from src.replay_compare.advanced_application import (
            create_advanced_replay_application,
        )

        motion_cfg = _load_yaml_object(args.motion_config, "motion configuration")
        try:
            authorization = authorize_advanced_replay(
                spec,
                motion_cfg,
                allow_legacy_validity_assumption=args.allow_legacy_validity_assumption,
            )
        except AdvancedReplayUnavailable as exc:
            output_root.mkdir(parents=True, exist_ok=False)
            refusal = {
                "schema": "replay_compare_startup_refusal_v1",
                "mode": "motion",
                "capture_id": spec.capture_id,
                "registered_adc_expected_sha256": spec.registered_adc_sha256,
                "effective_config_sha256": spec.effective_config_sha256,
                "reason": str(exc),
                "adc_content_was_not_opened": True,
                "hardware_was_not_accessed": True,
            }
            refusal_path = output_root / "startup_refusal.json"
            refusal_path.write_text(
                json.dumps(refusal, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            print(json.dumps({
                "started": False,
                "diagnostic": str(refusal_path),
                "reason": str(exc),
            }, sort_keys=True), file=sys.stderr)
            return 2
    reference = PassiveReferenceTimeline.load(
        spec.capture_dir,
        development_data_root=args.development_data_root,
        anchor_utc_us=spec.anchor_utc_us,
        frame_rate_hz=spec.frame_rate_hz,
        min_pi=0.5,
    )
    if mode == "motion":
        manager = create_advanced_replay_application(
            authorization,
            output_root=output_root,
            reference_timeline=reference,
            frame_limit=limit,
            initial_speed=speed,
        )
    else:
        manager = create_replay_application(
            spec,
            output_root=output_root,
            reference_timeline=reference,
            frame_limit=limit,
            initial_speed=speed,
        )

    manager.start()
    if args.headless:
        while not manager.ended:
            time.sleep(0.05)
        manager.stop()
        result = {
            "pass_dir": str(manager.pass_dir),
            "clean_shutdown": manager.clean_shutdown,
            "failure": None if manager.failure is None else str(manager.failure),
        }
        print(json.dumps(result, sort_keys=True))
        return 0 if manager.clean_shutdown and manager.failure is None else 1
    exit_code = run_dashboard(
        manager,
        recording_name=spec.capture_id,
        frame_rate_hz=spec.frame_rate_hz,
        poll_interval_ms=int(config["poll_interval_ms"]),
    )
    manager.stop()
    if manager.failure is not None:
        print(f"replay failed: {manager.failure}", file=sys.stderr)
        return 1
    return exit_code


def _load_config(path: Path) -> dict[str, Any]:
    config = _load_yaml_object(path, "replay configuration")
    expected = {
        "schema", "capture", "mode", "speed", "output_root",
        "reference_min_pi", "poll_interval_ms",
    }
    if set(config) != expected:
        raise ValueError(
            "replay configuration fields differ from the exact supported schema"
        )
    if config["schema"] != "replay_demo_compare_config_v1":
        raise ValueError("unsupported replay configuration schema")
    if config["mode"] not in {"baseline", "motion"}:
        raise ValueError("configured mode must be baseline or motion")
    if type(config["capture"]) is not str or not config["capture"].strip():
        raise ValueError("capture must be a nonempty string")
    configured_speed = config["speed"]
    if (
        isinstance(configured_speed, bool)
        or not isinstance(configured_speed, (int, float))
        or float(configured_speed) not in SUPPORTED_SPEEDS
    ):
        raise ValueError(f"configured speed must be one of {SUPPORTED_SPEEDS}")
    if type(config["output_root"]) is not str or not config["output_root"].strip():
        raise ValueError("output_root must be a nonempty path string")
    poll = config["poll_interval_ms"]
    if type(poll) is not int or not 1 <= poll <= 250:
        raise ValueError("poll_interval_ms must be an integer in [1, 250]")
    minimum_pi = config["reference_min_pi"]
    if (
        isinstance(minimum_pi, bool)
        or not isinstance(minimum_pi, (int, float))
        or float(minimum_pi) != 0.5
    ):
        raise ValueError("reference_min_pi is fixed at exactly 0.5")
    return config


def _load_yaml_object(path: Path | None, label: str) -> dict[str, Any]:
    if path is None:
        raise ValueError(f"{label} path is required")
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ValueError(f"{label} must contain a YAML mapping")
    return value


def _require_clean_scientific_source() -> None:
    completed = subprocess.run(
        [
            "git", "status", "--porcelain", "--untracked-files=all", "--",
            "src", "scripts", "experiments", "reference_registry",
            "environment.yml",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    if completed.stdout.strip():
        raise RuntimeError(
            "commit the reviewed replay scientific source before operator playback"
        )


if __name__ == "__main__":
    raise SystemExit(main())
