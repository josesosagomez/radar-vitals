"""Deterministic telemetry evaluation; never a real-machine benchmark."""
import csv
import json

import pytest

from scripts import benchmark_live_motion as benchmark


def _telemetry(tmp_path, *, extended=True):
    commit = "a" * 40
    hashes = dict(config_sha256="b"*64, source_sha256="c"*64,
                  calibration_sha256="d"*64)
    metadata = dict(completion_status="completed", prospective_study_mode=False,
                    live_motion_enabled=True, extended_breathing_enabled=extended,
                    source_id="live:synthetic_fixture", source_kind="synthetic",
                    mode="live", git_commit=commit,
                    config=dict(development_motion=dict(
                        extended_breathing_enabled=extended,
                        extended_breathing_window_s=60)), **hashes)
    perf = dict(schema_version=benchmark.META_SCHEMA, clean_shutdown=True,
                nominal_frame_rate_hz=20, ordinary_hop_s=3,
                frame_arrival_clock_available=True,
                source_id=metadata["source_id"], source_kind="synthetic",
                checkout_commit=commit, extended_breathing_enabled=extended,
                run_start_utc="2026-10-07T00:00:00Z",
                run_stop_utc="2026-10-07T00:01:10Z", **hashes)
    rows = []
    stages = {199: "preview_10", 399: "preview_20", 599: "ordinary_30",
              1199: "extended_60", 1259: "rolling", 1319: "rolling", 1379: "rolling"}
    if not extended:
        stages.pop(1199)
    for index in range(1401):
        base = {field: "" for field in benchmark.CSV_FIELDS}
        base.update(monotonic_s=str(index / 20), frame_index=str(index), epoch="1")
        controller = dict(base, kind="controller", controller_lag_s="0.01",
                          source_queue_overflow_count="0", source_queue_depth="0")
        if index % 20 == 0:
            controller["rss_bytes"] = str(100_000_000)
        rows.append(controller)
        if index % 20 == 0:
            rows.append(dict(base, kind="ui", ui_snapshot_age_s="0.02"))
        if index in stages:
            stage = stages[index]
            rows.append(dict(base, kind="analysis", stage=stage,
                             job_id=f"fixture-{index}", analysis_elapsed_s="0.1",
                             selector_elapsed_s="0.05" if stage in ("preview_10", "ordinary_30") else "",
                             analysis_status="completed"))
    _write(tmp_path, metadata, perf, rows)
    return metadata, perf, rows


def _write(directory, metadata, perf, rows):
    (directory/"run_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (directory/"performance_meta.json").write_text(json.dumps(perf), encoding="utf-8")
    with (directory/"performance.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=benchmark.CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _evaluate(directory, accepted=True):
    return benchmark.evaluate_run(directory, machine_label="synthetic test fixture",
                                  memory_accepted=accepted)


def test_synthetic_telemetry_can_pass_software_but_never_hardware(tmp_path):
    _telemetry(tmp_path)
    result = _evaluate(tmp_path)
    assert result["status"] == "passed"
    assert result["source_provenance"] == "fake_or_replay"
    assert result["physical_acceptance_status"] == "not_assessed_nonhardware_source"
    assert result["gates"]["steady_analysis"]["count"] == 3
    assert result["gates"]["selector_latency"]["count"] == 2
    assert result["inputs"]["performance_csv_sha256"]


def test_memory_requires_explicit_owner_review(tmp_path):
    metadata, perf, rows = _telemetry(tmp_path)
    for row in rows:
        if row["rss_bytes"]:
            row["rss_bytes"] = str(100_000_000 + int(row["frame_index"]) * 1000)
    _write(tmp_path, metadata, perf, rows)
    result = _evaluate(tmp_path, accepted=False)
    assert result["status"] == "unresolved_or_failed"
    assert result["memory"]["status"] == "requires_owner_review"
    assert result["memory"]["post_warmup_slope_bytes_per_s"] > 0
    assert result["memory"]["automatic_plateau_tolerance_bytes"] is None


@pytest.mark.parametrize("field,kind,value,gate", [
    ("controller_lag_s", "controller", "0.251", "controller_lag"),
    ("ui_snapshot_age_s", "ui", "0.251", "ui_snapshot_age"),
    ("analysis_elapsed_s", "rolling", "3.001", "steady_analysis"),
    ("source_queue_overflow_count", "controller", "1", "source_queue_overflow"),
])
def test_observable_responsiveness_failures(tmp_path, field, kind, value, gate):
    metadata, perf, rows = _telemetry(tmp_path)
    for row in rows:
        if row["kind"] == kind or row["stage"] == kind:
            row[field] = value
    _write(tmp_path, metadata, perf, rows)
    result = _evaluate(tmp_path)
    assert result["status"] == "unresolved_or_failed"
    assert result["gates"][gate]["status"] == "failed"


def test_previews_cannot_substitute_for_steady_rolling_analysis(tmp_path):
    metadata, perf, rows = _telemetry(tmp_path)
    rows = [row for row in rows if row["stage"] != "rolling"]
    _write(tmp_path, metadata, perf, rows)
    result = _evaluate(tmp_path)
    assert result["gates"]["steady_analysis"]["status"] == "failed_missing_telemetry"
    assert result["status"] == "unresolved_or_failed"


def test_short_run_cannot_pass_even_with_owner_memory_attestation(tmp_path):
    metadata, perf, rows = _telemetry(tmp_path)
    rows = [row for row in rows if int(row["frame_index"]) <= 400]
    _write(tmp_path, metadata, perf, rows)
    result = _evaluate(tmp_path)
    assert result["status"] == "unresolved_or_failed"


def test_exact_latency_limits_and_motion_only_stage_contract(tmp_path):
    metadata, perf, rows = _telemetry(tmp_path, extended=False)
    for row in rows:
        if row["kind"] == "controller":
            row["controller_lag_s"] = "0.25"
        elif row["kind"] == "ui":
            row["ui_snapshot_age_s"] = "0.25"
        elif row["stage"] == "rolling":
            row["analysis_elapsed_s"] = "3"
    _write(tmp_path, metadata, perf, rows)
    result = _evaluate(tmp_path)
    assert result["status"] == "passed"
    assert not result["gates"]["analysis_stage_coverage"]["extended_breathing_enabled"]


def test_failed_rolling_jobs_do_not_satisfy_stage_coverage(tmp_path):
    metadata, perf, rows = _telemetry(tmp_path)
    for row in rows:
        if row["stage"] == "rolling":
            row["analysis_status"] = "failed"
    _write(tmp_path, metadata, perf, rows)
    result = _evaluate(tmp_path)
    assert result["gates"]["steady_analysis"]["status"] == "failed_missing_telemetry"
    assert result["gates"]["analysis_stage_coverage"]["status"] == "failed_missing_telemetry"


def test_short_rss_coverage_cannot_be_owner_attested_as_complete(tmp_path):
    metadata, perf, rows = _telemetry(tmp_path)
    for row in rows:
        if int(row["frame_index"]) >= 200:
            row["rss_bytes"] = ""
    _write(tmp_path, metadata, perf, rows)
    result = _evaluate(tmp_path)
    assert result["status"] == "unresolved_or_failed"
    assert result["memory"]["status"] != "passed_owner_attested"


def test_missing_arrival_clock_is_explicit_failure(tmp_path):
    metadata, perf, rows = _telemetry(tmp_path)
    perf["frame_arrival_clock_available"] = False
    for row in rows:
        row["controller_lag_s"] = ""
    _write(tmp_path, metadata, perf, rows)
    result = _evaluate(tmp_path)
    assert result["gates"]["frame_arrival_clock"]["status"] == "failed_missing_telemetry"
    assert result["status"] == "unresolved_or_failed"


@pytest.mark.parametrize("change", ["hash", "commit", "decreasing_time", "nan_lag",
                                    "dirty_shutdown", "prospective"])
def test_incompatible_or_corrupt_telemetry_fails(tmp_path, change):
    metadata, perf, rows = _telemetry(tmp_path)
    if change == "hash":
        metadata["source_sha256"] = "e"*64
    elif change == "commit":
        metadata["git_commit"] = "f"*40
    elif change == "decreasing_time":
        rows[-1]["monotonic_s"] = "0"
    elif change == "nan_lag":
        rows[0]["controller_lag_s"] = "nan"
    elif change == "dirty_shutdown":
        perf["clean_shutdown"] = False
    elif change == "prospective":
        metadata["prospective_study_mode"] = True
    _write(tmp_path, metadata, perf, rows)
    with pytest.raises(benchmark.BenchmarkError):
        _evaluate(tmp_path)
