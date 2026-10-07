#!/usr/bin/env python3
"""Evaluate saved live-motion performance telemetry without opening raw ADC data.

This owner-run command assesses software responsiveness from a completed run.  A
fake source can exercise the software gates, but cannot satisfy physical/live
acceptance.  Memory growth is reported numerically and remains unresolved unless
the owner explicitly records review with ``--memory-accepted``; no empirical
plateau tolerance is invented here.
"""
from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from pathlib import Path
from typing import Any, Mapping

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.m2.common import read_json_object, require_sha256, sha256_file, write_new_json


CSV_FIELDS = (
    "kind",
    "monotonic_s",
    "frame_index",
    "epoch",
    "job_id",
    "stage",
    "controller_lag_s",
    "ui_snapshot_age_s",
    "analysis_elapsed_s",
    "selector_elapsed_s",
    "source_queue_overflow_count",
    "source_queue_depth",
    "rss_bytes",
    "analysis_status",
)
META_SCHEMA = "live_motion_performance_v1"
MAX_CONTROLLER_UI_LAG_S = 0.25
GIT_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


class BenchmarkError(ValueError):
    """Saved telemetry is missing, malformed, or incompatible."""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate a completed live-motion run's saved performance telemetry."
    )
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path,
                        help="new canonical JSON report path")
    parser.add_argument("--machine-label", required=True,
                        help="operator-provided identity for the representative computer")
    parser.add_argument(
        "--memory-accepted",
        action="store_true",
        help="record that the owner reviewed the reported RSS series and accepts its plateau",
    )
    return parser.parse_args()


def _finite_float(value: str, field: str, *, required: bool) -> float | None:
    if value == "":
        if required:
            raise BenchmarkError(f"missing telemetry field {field}")
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise BenchmarkError(f"{field} must be a finite number") from exc
    if not math.isfinite(result):
        raise BenchmarkError(f"{field} must be a finite number")
    return result


def _exact_int(
    value: str,
    field: str,
    *,
    required: bool,
    minimum: int = 0,
) -> int | None:
    if value == "":
        if required:
            raise BenchmarkError(f"missing telemetry field {field}")
        return None
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BenchmarkError(f"{field} must be an integer >= {minimum}") from exc
    if str(result) != value.strip() or result < minimum:
        raise BenchmarkError(f"{field} must be an integer >= {minimum}")
    return result


def _load_rows(
    path: Path, *, require_controller_lag: bool = True
) -> list[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != CSV_FIELDS:
                raise BenchmarkError(
                    f"performance.csv fields differ: expected {CSV_FIELDS}, got {reader.fieldnames}"
                )
            source_rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise BenchmarkError(f"cannot read performance telemetry: {exc}") from exc
    if not source_rows:
        raise BenchmarkError("performance telemetry has no rows")

    rows: list[dict[str, Any]] = []
    previous_time = -math.inf
    previous_overflow: int | None = None
    for index, row in enumerate(source_rows):
        prefix = f"performance.csv row {index + 2}"
        kind = row["kind"]
        if kind not in ("controller", "ui", "analysis"):
            raise BenchmarkError(f"{prefix}: unknown kind {kind!r}")
        time_s = _finite_float(row["monotonic_s"], f"{prefix}.monotonic_s", required=True)
        assert time_s is not None
        if time_s < previous_time:
            raise BenchmarkError("performance telemetry monotonic_s must not decrease")
        previous_time = time_s
        parsed = dict(row)
        parsed["monotonic_s"] = time_s
        parsed["frame_index"] = _exact_int(
            row["frame_index"],
            f"{prefix}.frame_index",
            required=kind in {"controller", "analysis"},
            # The first UI snapshot may precede the first acquired frame.
            minimum=-1 if kind == "ui" else 0,
        )
        parsed["epoch"] = _exact_int(
            row["epoch"], f"{prefix}.epoch", required=False
        )
        for field in (
            "controller_lag_s", "ui_snapshot_age_s", "analysis_elapsed_s",
            "selector_elapsed_s",
        ):
            parsed[field] = _finite_float(row[field], f"{prefix}.{field}", required=False)
            if parsed[field] is not None and parsed[field] < 0:
                raise BenchmarkError(f"{prefix}.{field} must be nonnegative")
        parsed["source_queue_overflow_count"] = _exact_int(
            row["source_queue_overflow_count"],
            f"{prefix}.source_queue_overflow_count",
            required=kind == "controller",
        )
        parsed["source_queue_depth"] = _exact_int(
            row["source_queue_depth"],
            f"{prefix}.source_queue_depth",
            required=kind == "controller",
        )
        parsed["rss_bytes"] = _exact_int(
            row["rss_bytes"], f"{prefix}.rss_bytes", required=False
        )
        if kind == "controller":
            overflow = int(parsed["source_queue_overflow_count"])
            if previous_overflow is not None and overflow < previous_overflow:
                raise BenchmarkError("source_queue_overflow_count must be cumulative/monotonic")
            previous_overflow = overflow
            if require_controller_lag and parsed["controller_lag_s"] is None:
                raise BenchmarkError(f"{prefix}.controller_lag_s is required")
        elif kind == "ui" and parsed["ui_snapshot_age_s"] is None:
            raise BenchmarkError(f"{prefix}.ui_snapshot_age_s is required")
        elif kind == "analysis":
            if parsed["analysis_elapsed_s"] is None:
                raise BenchmarkError(f"{prefix}.analysis_elapsed_s is required")
            if not row["job_id"].strip() or not row["stage"].strip():
                raise BenchmarkError(f"{prefix}.job_id and stage are required")
            if row["stage"] not in {
                "preview_10", "preview_20", "ordinary_30", "extended_60", "rolling"
            }:
                raise BenchmarkError(f"{prefix}.stage is unknown")
            if row["analysis_status"] not in {"completed", "failed", "interrupted"}:
                raise BenchmarkError(f"{prefix}.analysis_status is invalid")
        elif row["analysis_status"]:
            raise BenchmarkError(f"{prefix}.analysis_status is only valid for analysis rows")
        rows.append(parsed)
    return rows


def _percentile(values: list[float], percentile: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=np.float64), percentile))


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "median": None, "p95": None, "max": None}
    return {
        "count": len(values),
        "min": min(values),
        "median": _percentile(values, 50),
        "p95": _percentile(values, 95),
        "max": max(values),
    }


def _linear_slope(times: np.ndarray, values: np.ndarray) -> float | None:
    if times.size < 2 or float(times[-1] - times[0]) <= 0:
        return None
    centered_time = times - float(np.mean(times))
    denominator = float(np.dot(centered_time, centered_time))
    if denominator <= 0 or not math.isfinite(denominator):
        return None
    centered_values = values - float(np.mean(values))
    return float(np.dot(centered_time, centered_values) / denominator)


def _memory_report(
    rows: list[Mapping[str, Any]],
    accepted: bool,
    *,
    required_duration_s: float = 60.0,
) -> dict[str, object]:
    samples = [
        (float(row["monotonic_s"]), int(row["rss_bytes"]))
        for row in rows if row["rss_bytes"] is not None
    ]
    if len(samples) < 6:
        return {
            "status": "failed_missing_telemetry",
            "owner_accepted": bool(accepted),
            "sample_count": len(samples),
            "required_duration_s": required_duration_s,
            "reason": "at least six RSS samples are required for segment reporting",
        }
    times = np.asarray([sample[0] for sample in samples], dtype=np.float64)
    rss = np.asarray([sample[1] for sample in samples], dtype=np.float64)
    relative_time = times - times[0]
    if float(relative_time[-1]) < required_duration_s:
        return {
            "status": "failed_missing_telemetry",
            "owner_accepted": bool(accepted),
            "sample_count": len(samples),
            "duration_s": float(relative_time[-1]),
            "required_duration_s": required_duration_s,
            "reason": "RSS telemetry does not span the complete assessment window",
        }
    warmup_count = max(2, len(samples) // 5)
    first_segment = rss[:warmup_count]
    last_segment = rss[-warmup_count:]
    steady_times = relative_time[warmup_count:]
    steady_rss = rss[warmup_count:]
    report = {
        "status": "passed_owner_attested" if accepted else "requires_owner_review",
        "owner_accepted": bool(accepted),
        "sample_count": len(samples),
        "duration_s": float(relative_time[-1]),
        "required_duration_s": required_duration_s,
        "first_rss_bytes": int(rss[0]),
        "last_rss_bytes": int(rss[-1]),
        "first_to_last_delta_bytes": int(rss[-1] - rss[0]),
        "all_samples_slope_bytes_per_s": _linear_slope(relative_time, rss),
        "post_warmup_slope_bytes_per_s": _linear_slope(steady_times, steady_rss),
        "warmup_segment": {
            "sample_count": warmup_count,
            "first_rss_bytes": int(first_segment[0]),
            "last_rss_bytes": int(first_segment[-1]),
            "median_rss_bytes": float(np.median(first_segment)),
        },
        "final_segment": {
            "sample_count": warmup_count,
            "first_rss_bytes": int(last_segment[0]),
            "last_rss_bytes": int(last_segment[-1]),
            "median_rss_bytes": float(np.median(last_segment)),
        },
        "automatic_plateau_tolerance_bytes": None,
        "reason": (
            "owner explicitly accepted the reported RSS behavior"
            if accepted
            else "no calibrated memory-growth tolerance exists; owner review is required"
        ),
    }
    return report


def _source_provenance(source_kind: str, run_metadata: Mapping[str, object]) -> str:
    if source_kind == "live_dca1000" and run_metadata.get("mode") == "live":
        return "real_live_hardware"
    if source_kind in {"fake", "synthetic", "replay"}:
        return "fake_or_replay"
    return "unresolved_source_identity"


def evaluate_run(
    run_dir: Path,
    *,
    machine_label: str,
    memory_accepted: bool,
) -> dict[str, object]:
    if type(machine_label) is not str or not machine_label.strip():
        raise BenchmarkError("machine_label must be a non-empty operator-provided string")
    run_dir = run_dir.resolve()
    metadata_path = run_dir / "run_metadata.json"
    performance_path = run_dir / "performance.csv"
    performance_meta_path = run_dir / "performance_meta.json"
    for path in (metadata_path, performance_path, performance_meta_path):
        if not path.is_file():
            raise BenchmarkError(f"required benchmark artifact is missing: {path.name}")
    run_metadata = read_json_object(metadata_path)
    performance_meta = read_json_object(performance_meta_path)
    if run_metadata.get("completion_status") != "completed":
        raise BenchmarkError("run_metadata completion_status is not completed")
    if run_metadata.get("prospective_study_mode") is not False:
        raise BenchmarkError("benchmark accepts development runs only")
    if run_metadata.get("live_motion_enabled") is not True:
        raise BenchmarkError("run_metadata does not identify an enabled live-motion run")
    if performance_meta.get("schema_version") != META_SCHEMA:
        raise BenchmarkError(f"performance_meta schema_version must be {META_SCHEMA!r}")
    if performance_meta.get("clean_shutdown") is not True:
        raise BenchmarkError("performance telemetry does not record a clean shutdown")
    arrival_clock_available = performance_meta.get("frame_arrival_clock_available")
    if type(arrival_clock_available) is not bool:
        raise BenchmarkError(
            "performance_meta frame_arrival_clock_available must be Boolean"
        )
    nominal_rate = performance_meta.get("nominal_frame_rate_hz")
    ordinary_hop = performance_meta.get("ordinary_hop_s")
    if type(nominal_rate) not in (int, float) or float(nominal_rate) != 20.0:
        raise BenchmarkError("performance_meta nominal_frame_rate_hz must be 20")
    if type(ordinary_hop) not in (int, float) or float(ordinary_hop) != 3.0:
        raise BenchmarkError("performance_meta ordinary_hop_s must be 3")
    for name in ("run_start_utc", "run_stop_utc", "source_id", "checkout_commit"):
        if type(performance_meta.get(name)) is not str or not str(performance_meta[name]).strip():
            raise BenchmarkError(f"performance_meta {name} is missing")
    if GIT_COMMIT_RE.fullmatch(str(performance_meta["checkout_commit"])) is None:
        raise BenchmarkError("performance_meta checkout_commit must be a full Git object ID")
    for name in ("config_sha256", "source_sha256", "calibration_sha256"):
        try:
            expected = require_sha256(performance_meta.get(name), f"performance_meta.{name}")
            actual = require_sha256(run_metadata.get(name), f"run_metadata.{name}")
        except ValueError as exc:
            raise BenchmarkError(str(exc)) from exc
        if actual != expected:
            raise BenchmarkError(f"run/performance metadata {name} mismatch")
    source_id = str(performance_meta["source_id"])
    if run_metadata.get("source_id") != source_id:
        raise BenchmarkError("run/performance metadata source_id mismatch")
    source_kind = performance_meta.get("source_kind")
    if type(source_kind) is not str or not source_kind:
        raise BenchmarkError("performance_meta source_kind is missing")
    if run_metadata.get("source_kind") != source_kind:
        raise BenchmarkError("run/performance metadata source_kind mismatch")
    if run_metadata.get("git_commit") != performance_meta["checkout_commit"]:
        raise BenchmarkError("run/performance metadata checkout commit mismatch")

    effective_config = run_metadata.get("config")
    development = (
        effective_config.get("development_motion")
        if isinstance(effective_config, Mapping) else None
    )
    if not isinstance(development, Mapping):
        raise BenchmarkError("run_metadata effective development_motion config is missing")
    extended_enabled = development.get("extended_breathing_enabled")
    if type(extended_enabled) is not bool:
        raise BenchmarkError("extended_breathing_enabled must be Boolean")
    extended_window_s = development.get("extended_breathing_window_s")
    if type(extended_window_s) not in (int, float) or float(extended_window_s) != 60.0:
        raise BenchmarkError("extended_breathing_window_s must be 60 for this evaluator")

    rows = _load_rows(
        performance_path,
        require_controller_lag=arrival_clock_available,
    )
    controller_rows = [row for row in rows if row["kind"] == "controller"]
    ui_rows = [row for row in rows if row["kind"] == "ui"]
    analysis_rows = [row for row in rows if row["kind"] == "analysis"]
    if len(controller_rows) < 2:
        raise BenchmarkError("at least two controller rows are required for throughput")
    if not ui_rows or not analysis_rows:
        raise BenchmarkError("UI and analysis telemetry rows are required")

    frame_indices = [int(row["frame_index"]) for row in controller_rows]
    if any(right <= left for left, right in zip(frame_indices, frame_indices[1:])):
        raise BenchmarkError("controller frame indices must increase strictly")
    frame_delta = frame_indices[-1] - frame_indices[0]
    time_delta = float(controller_rows[-1]["monotonic_s"] - controller_rows[0]["monotonic_s"])
    if frame_delta <= 0 or time_delta <= 0:
        raise BenchmarkError("controller telemetry has no positive frame/time span")
    observed_rate = frame_delta / time_delta
    nominal_rate = float(nominal_rate)
    contiguous = frame_indices == list(range(frame_indices[0], frame_indices[-1] + 1))
    overflow_max = max(int(row["source_queue_overflow_count"]) for row in controller_rows)
    queue_depth_max = max(int(row["source_queue_depth"]) for row in controller_rows)

    controller_lag = [
        float(row["controller_lag_s"])
        for row in controller_rows
        if row["controller_lag_s"] is not None
    ]
    ui_age = [float(row["ui_snapshot_age_s"]) for row in ui_rows]
    selector_elapsed = [
        float(row["selector_elapsed_s"])
        for row in analysis_rows if row["selector_elapsed_s"] is not None
    ]
    completed_analysis = [
        row for row in analysis_rows if row["analysis_status"] == "completed"
    ]
    completed_ordinary = [
        row for row in completed_analysis if row["stage"] == "ordinary_30"
    ]
    completed_extended = [
        row for row in completed_analysis if row["stage"] == "extended_60"
    ]
    full_window_frames = int(round(float(extended_window_s) * nominal_rate))
    completed_rolling_after_window = [
        row for row in completed_analysis
        if row["stage"] == "rolling"
        and int(row["frame_index"]) >= frame_indices[0] + full_window_frames - 1
    ]
    steady_elapsed = [
        float(row["analysis_elapsed_s"])
        for row in completed_rolling_after_window
    ]

    required_rolling_updates = 2
    stage_coverage_passed = (
        len(frame_indices) >= full_window_frames
        and bool(completed_ordinary)
        and len(completed_rolling_after_window) >= required_rolling_updates
        and (not extended_enabled or bool(completed_extended))
    )

    gates = {
        "frame_arrival_clock": {
            "status": "passed" if arrival_clock_available else "failed_missing_telemetry",
            "available": arrival_clock_available,
            "interpretation": (
                "controller lag is measured from source enqueue to completed controller work"
                if arrival_clock_available
                else "source did not expose per-frame arrival timestamps"
            ),
        },
        "throughput_20_hz": {
            "status": "passed" if observed_rate >= nominal_rate and contiguous else "failed",
            "threshold_hz": nominal_rate,
            "observed_hz": observed_rate,
            "contiguous_frame_indices": contiguous,
            "frame_start": frame_indices[0],
            "frame_stop_inclusive": frame_indices[-1],
            "frame_count": len(frame_indices),
            "wall_span_s": time_delta,
            "nominal_covered_duration_s": len(frame_indices) / nominal_rate,
        },
        "analysis_stage_coverage": {
            "status": "passed" if stage_coverage_passed else "failed_missing_telemetry",
            "required_full_window_frames": full_window_frames,
            "required_full_window_s": float(extended_window_s),
            "extended_breathing_enabled": extended_enabled,
            "completed_ordinary_count": len(completed_ordinary),
            "completed_extended_count": len(completed_extended),
            "completed_rolling_after_full_window_count": len(
                completed_rolling_after_window
            ),
            "required_rolling_after_full_window_count": required_rolling_updates,
        },
        "source_queue_overflow": {
            "status": "passed" if overflow_max == 0 else "failed",
            "maximum_cumulative_overflow_count": overflow_max,
            "maximum_queue_depth": queue_depth_max,
        },
        "controller_lag": {
            "status": (
                "failed_missing_telemetry" if not controller_lag
                else "passed" if max(controller_lag) <= MAX_CONTROLLER_UI_LAG_S
                else "failed"
            ),
            "threshold_s": MAX_CONTROLLER_UI_LAG_S,
            **_distribution(controller_lag),
        },
        "ui_snapshot_age": {
            "status": "passed" if max(ui_age) <= MAX_CONTROLLER_UI_LAG_S else "failed",
            "threshold_s": MAX_CONTROLLER_UI_LAG_S,
            **_distribution(ui_age),
        },
        "steady_analysis": {
            "status": (
                "failed_missing_telemetry" if not steady_elapsed
                else "passed" if max(steady_elapsed) <= float(ordinary_hop) else "failed"
            ),
            "threshold_s": float(ordinary_hop),
            **_distribution(steady_elapsed),
        },
        "selector_latency": {
            "status": "reported" if selector_elapsed else "failed_missing_telemetry",
            "acceptance_threshold_s": None,
            **_distribution(selector_elapsed),
        },
    }
    memory = _memory_report(
        rows,
        memory_accepted,
        required_duration_s=float(extended_window_s),
    )
    source_provenance = _source_provenance(source_kind, run_metadata)
    required_gate_statuses = [
        value["status"] for name, value in gates.items() if name != "selector_latency"
    ]
    software_passed = all(status == "passed" for status in required_gate_statuses)
    selector_present = gates["selector_latency"]["status"] == "reported"
    memory_passed = memory["status"] == "passed_owner_attested"
    benchmark_passed = software_passed and selector_present and memory_passed
    physical_status = (
        "passed" if benchmark_passed and source_provenance == "real_live_hardware"
        else "failed" if source_provenance == "real_live_hardware"
        else "not_assessed_nonhardware_source"
    )

    return {
        "schema": "live_motion_benchmark_report_v1",
        "status": "passed" if benchmark_passed else "unresolved_or_failed",
        "physical_acceptance_status": physical_status,
        "source_provenance": source_provenance,
        "source_id": source_id,
        "source_kind": source_kind,
        "machine_label": machine_label.strip(),
        "memory_owner_attestation": (
            "owner reviewed RSS series and accepts no continuing growth"
            if memory_accepted else None
        ),
        "inputs": {
            "run_dir": str(run_dir),
            "run_metadata_sha256": sha256_file(metadata_path),
            "performance_csv_sha256": sha256_file(performance_path),
            "performance_meta_sha256": sha256_file(performance_meta_path),
            "config_sha256": performance_meta["config_sha256"],
            "source_sha256": performance_meta["source_sha256"],
            "calibration_sha256": performance_meta["calibration_sha256"],
            "checkout_commit": performance_meta["checkout_commit"],
            "evaluator_source_sha256": sha256_file(Path(__file__)),
        },
        "run_timing": {
            "run_start_utc": performance_meta["run_start_utc"],
            "run_stop_utc": performance_meta["run_stop_utc"],
            "nominal_frame_rate_hz": nominal_rate,
            "ordinary_hop_s": float(ordinary_hop),
        },
        "gates": gates,
        "memory": memory,
        "interpretation": (
            "software telemetry only; this source cannot establish representative hardware acceptance"
            if source_provenance != "real_live_hardware"
            else "completed real-live source telemetry; clinical or signal-accuracy validation is separate"
        ),
    }


def main() -> int:
    args = _parse_args()
    try:
        report = evaluate_run(
            args.run_dir,
            machine_label=args.machine_label,
            memory_accepted=args.memory_accepted,
        )
        output = args.output.resolve()
        if output.exists():
            raise BenchmarkError(f"refusing to overwrite benchmark report {output}")
        write_new_json(output, report)
    except (BenchmarkError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Benchmark report: {output}")
    print(f"SHA-256: {sha256_file(output)}")
    print(f"Status: {report['status']}; physical: {report['physical_acceptance_status']}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
