"""Exercise the real recorded dashboard and save reproducible observations.

This driver uses only the registered development recording and ordinary replay
application factory. It has no hardware, estimator, bin or reference overrides.
Run artifact verification separately after each invocation.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import multiprocessing
import statistics
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def memory_observation(samples: list[dict], field: str, generation: int) -> dict:
    """Describe simultaneous observations without inventing a RAM threshold."""
    measured = [row for row in samples if type(row.get(field)) is int]
    result = {"peak_bytes": max((row[field] for row in measured), default=None)}
    stable = [row for row in measured
              if row["generation"] == generation and row["boundary"] >= 1800]
    if len(stable) >= 8:
        quarter = max(1, len(stable) // 4)
        early = statistics.median(row[field] for row in stable[:quarter])
        late_rows = stable[-quarter:]
        late = statistics.median(row[field] for row in late_rows)
        elapsed = late_rows[-1]["monotonic_s"] - late_rows[0]["monotonic_s"]
        result["steady"] = {
            "first_boundary": stable[0]["boundary"],
            "last_boundary": stable[-1]["boundary"],
            "early_quarter_median_bytes": early,
            "late_quarter_median_bytes": late,
            "late_minus_early_bytes": late - early,
            "late_quarter_endpoint_bytes_per_s": (
                (late_rows[-1][field] - late_rows[0][field]) / elapsed
                if elapsed > 0 else None
            ),
            "interpretation": "Observed memory; inspect alongside bounded ownership tests.",
        }
    return result


def process_performance_errors(performance: list[dict], attempt_ids: list[str]) -> list[str]:
    """Require complete, bounded transport telemetry for this production driver.

    Independent artifact verification checks the scientific evidence. This
    separate native gate also binds each process timing row to its durable
    executed attempt; injected thread backends are not used by this driver.
    """
    errors = []
    rows = [row for row in performance if row.get("kind") == "analysis"]
    job_ids = [row.get("job_id") for row in rows]
    if len(set(attempt_ids)) != len(attempt_ids) or job_ids != attempt_ids:
        errors.append("process telemetry does not match every durable attempt exactly once in order")
    if not rows:
        errors.append("production analysis process telemetry is missing")
    spans = (
        "analysis_elapsed_s",
        "parent_copy_elapsed_s", "parent_send_elapsed_s",
        "parent_wait_receive_elapsed_s", "child_materialization_elapsed_s",
        "child_callback_elapsed_s", "process_round_trip_elapsed_s",
    )
    for sequence, row in enumerate(rows, start=1):
        if type(row.get("process_sequence")) is not int or row["process_sequence"] != sequence:
            errors.append("analysis process sequence is missing or discontinuous")
        for name in spans:
            value = row.get(name)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                errors.append(f"analysis process span {name} is missing or invalid")
        for name, limit in (("process_control_bytes", 65_536),
                            ("process_result_bytes", 16_777_216)):
            value = row.get(name)
            if type(value) is not int or not 0 < value <= limit:
                errors.append(f"analysis process byte count {name} is outside its inclusive bound")
        valid_spans = all(type(row.get(name)) in (int, float)
                          and math.isfinite(row[name]) and row[name] >= 0 for name in spans)
        if valid_spans:
            round_trip = row["process_round_trip_elapsed_s"]
            parent_total = sum(row[name] for name in (
                "parent_copy_elapsed_s", "parent_send_elapsed_s", "parent_wait_receive_elapsed_s"
            ))
            if row["analysis_elapsed_s"] + 1e-9 < round_trip or round_trip + 1e-9 < parent_total:
                errors.append("analysis process durations have impossible nesting")
        if "fatal_backend_error" not in row or row["fatal_backend_error"] is not None:
            errors.append("analysis fatal-error field is missing or reports a failure")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development-data-root", type=Path, required=True)
    parser.add_argument("--scenario", choices=("full", "smoke", "controls"), required=True)
    parser.add_argument("--speed", type=float, choices=(0.5, 1.0, 2.0, 4.0), default=1.0)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    if args.scenario == "full" and args.speed != 1.0:
        parser.error("the complete baseline acceptance scenario requires speed 1")
    source_status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all", "--",
         "src", "scripts", "experiments", "reference_registry", "environment.yml"],
        cwd=ROOT, text=True,
    )
    if source_status.strip():
        parser.error("commit the reviewed replay sources/configuration before acceptance")

    from PySide6 import QtCore, QtWidgets

    from src.replay_compare.telemetry import replay_rss_bytes
    from src.replay_compare.application import create_replay_application
    from src.replay_compare.reference import PassiveReferenceTimeline
    from src.replay_compare.session import SUPPORTED_CAPTURE_ID, resolve_replay_session
    from src.replay_compare.ui import create_dashboard

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    approved_output = ROOT / "results" / "replay_compare"
    if approved_output.resolve() != approved_output:
        parser.error("the fixed replay output directory must not redirect through a link")
    output = args.output_root or approved_output / run_id
    output = output.resolve()
    if not output.is_relative_to(approved_output):
        parser.error("acceptance outputs must stay under this checkout's results/replay_compare")
    if output.exists():
        parser.error("acceptance output directory must be new")

    spec = resolve_replay_session(
        SUPPORTED_CAPTURE_ID, development_data_root=args.development_data_root
    )
    reference = PassiveReferenceTimeline.load(
        spec.capture_dir,
        development_data_root=args.development_data_root,
        anchor_utc_us=spec.anchor_utc_us,
        frame_rate_hz=spec.frame_rate_hz,
    )
    limit = spec.frame_count if args.scenario == "full" else 800
    manager = create_replay_application(
        spec,
        output_root=output,
        reference_timeline=reference,
        frame_limit=limit,
        initial_speed=args.speed,
    )
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = create_dashboard(manager, recording_name=spec.capture_id)
    window.show()

    started = time.monotonic()
    observations: dict = {
        "schema": "replay_compare_native_acceptance_v1",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "command": [sys.executable, *sys.argv],
        "scenario": args.scenario,
        "initial_speed": args.speed,
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scientific_source_status": source_status,
        "source_tree": subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True
        ).strip(),
        "capture_id": spec.capture_id,
        "registered_adc_sha256": spec.registered_adc_sha256,
        "effective_config_sha256": spec.effective_config_sha256,
        "frame_limit": limit,
        "commands": [],
        "samples": [],
        "screenshots": [],
        "errors": [],
        "limitations": [
            "Recorded current-software playback, not hardware or clinical acceptance.",
            "Qt timer intervals measure event-loop responsiveness, not physiological accuracy.",
            "Summed parent/child working sets can count shared pages twice; samples are sequential within one tick.",
        ],
    }
    ui_timing_path = output / "ui_performance.jsonl"
    ui_timing = ui_timing_path.open("x", encoding="utf-8", newline="\n")
    observations["ui_performance_path"] = str(ui_timing_path)
    previous_tick: float | None = None
    tick_intervals: list[float] = []
    ui_callback_times: list[float] = []
    ui_snapshot_ages: list[float] = []
    last_sample = -1.0
    finalizing_since: float | None = None
    pause_request: float | None = None
    paused_at: float | None = None
    paused_snapshot = None
    resumed = False
    restart_requested = False
    restart_request_wall: float | None = None
    restarted = False
    speed_two = False
    speed_four = False
    speed_two_request: float | None = None
    speed_four_request: float | None = None
    stop_requested = False
    during_saved = False
    exception_hook = sys.excepthook

    def record_error(message: str) -> None:
        nonlocal stop_requested
        observations["errors"].append(message)
        if not stop_requested:
            manager.request_stop()
            stop_requested = True

    def qt_exception(exc_type, exc, traceback) -> None:
        record_error(f"Qt callback: {exc_type.__name__}: {exc}")
        exception_hook(exc_type, exc, traceback)

    sys.excepthook = qt_exception

    def timing_context(snapshot) -> dict:
        return {
            "generation": snapshot.generation,
            "boundary": snapshot.processed_boundary,
            "state": snapshot.playback_state,
            "accumulation_stage": snapshot.accumulation_stage,
            "published_monotonic_s": snapshot.published_monotonic_s,
            "hr_attempt_id": snapshot.hr.attempt_id,
            "br_attempt_id": snapshot.br.attempt_id,
        }

    def timed_ui_poll() -> None:
        begin = time.monotonic()
        displayed = window._snapshot
        try:
            window.poll_once()
            displayed = window._snapshot
            if displayed.processed_boundary > 0 and displayed.playback_state == "running":
                published = displayed.published_monotonic_s
                if published is None:
                    raise RuntimeError("running snapshot lacks its controller publication timestamp")
                ui_snapshot_ages.append(time.monotonic() - published)
        except Exception as exc:
            record_error(f"Dashboard poll: {type(exc).__name__}: {exc}")
        finally:
            end = time.monotonic()
            ui_callback_times.append(end - begin)
            ui_timing.write(json.dumps({
                "kind": "ui_poll", "begin_monotonic_s": begin,
                "end_monotonic_s": end, "elapsed_s": end - begin,
                **timing_context(displayed),
            }, allow_nan=False) + "\n")

    window.poll_timer.timeout.disconnect()
    window.poll_timer.timeout.connect(timed_ui_poll)

    def command(name: str, callback) -> None:
        begin = time.monotonic()
        callback()
        observations["commands"].append({
            "kind": name,
            "monotonic_s": begin,
            "callback_elapsed_s": time.monotonic() - begin,
            "generation": manager.generation,
            "boundary": manager.latest_snapshot.processed_boundary,
        })

    def tick() -> None:
        nonlocal previous_tick, last_sample, finalizing_since
        nonlocal pause_request, paused_at, paused_snapshot, resumed
        nonlocal restart_requested, restart_request_wall, restarted, speed_two, speed_four, during_saved
        nonlocal speed_two_request, speed_four_request
        now = time.monotonic()
        interval_begin = previous_tick
        if interval_begin is not None:
            tick_intervals.append(now - interval_begin)
        previous_tick = now
        try:
            snapshot = manager.latest_snapshot
            if interval_begin is not None:
                ui_timing.write(json.dumps({
                    "kind": "qt_heartbeat", "begin_monotonic_s": interval_begin,
                    "end_monotonic_s": now, "elapsed_s": now - interval_begin,
                    **timing_context(snapshot),
                }, allow_nan=False) + "\n")
            if now - last_sample >= 1.0:
                memory_begin = time.monotonic()
                parent_rss = replay_rss_bytes()
                child = manager.current_run.analysis_process_observation()
                child_rss = child["current_rss_bytes"]
                # Only direct children owned by this driver with the production
                # backend name are counted; this does not enumerate system PIDs.
                owned_children = [process.pid for process in multiprocessing.active_children()
                                  if process.name == "ReplayBaselineDSP"]
                observations["samples"].append({
                    "monotonic_s": now,
                    "generation": snapshot.generation,
                    "boundary": snapshot.processed_boundary,
                    "state": snapshot.playback_state,
                    "speed": snapshot.speed,
                    "rss_bytes": parent_rss,
                    "analysis_process": child,
                    "owned_analysis_process_count": len(owned_children),
                    "owned_analysis_process_pids": owned_children,
                    "combined_current_rss_bytes": (
                        parent_rss + child_rss
                        if type(parent_rss) is int and type(child_rss) is int else None
                    ),
                    "memory_sample_begin_monotonic_s": memory_begin,
                    "memory_sample_end_monotonic_s": time.monotonic(),
                    "coverage": asdict(snapshot.coverage),
                })
                last_sample = now
            if not during_saved and snapshot.processed_boundary >= min(6000, limit - 1):
                path = window.save_screenshot(output / "dashboard_during.png")
                observations["screenshots"].append(str(path))
                during_saved = True
            if args.scenario == "controls":
                if pause_request is None and snapshot.generation == 1 and snapshot.processed_boundary >= 620:
                    pause_request = now
                    command("pause", window.request_pause_or_resume)
                elif pause_request is not None and paused_at is None and snapshot.playback_state == "paused":
                    paused_at = now
                    paused_snapshot = snapshot
                    observations["pause_ack_elapsed_s"] = now - pause_request
                elif paused_at is not None and not resumed:
                    if snapshot != paused_snapshot:
                        raise AssertionError("controller snapshot changed after pause acknowledgment")
                    if now - paused_at >= 0.5:
                        command("resume", window.request_pause_or_resume)
                        resumed = True
                elif resumed and not restart_requested and snapshot.processed_boundary >= 680:
                    observations["preserved_first_pass"] = str(manager.pass_dir)
                    command("restart", window.request_restart)
                    restart_requested = True
                    restart_request_wall = now
                elif restart_requested and snapshot.generation == 2:
                    restarted = True
                    if speed_two_request is not None and not speed_two and snapshot.speed == 2.0:
                        speed_two = True
                        observations["speed_2_ack_elapsed_s"] = now - speed_two_request
                    if speed_four_request is not None and not speed_four and snapshot.speed == 4.0:
                        speed_four = True
                        observations["speed_4_ack_elapsed_s"] = now - speed_four_request
                    if speed_two_request is None and snapshot.processed_boundary >= 100:
                        speed_two_request = now
                        command("speed_2", lambda: window.set_speed(2.0))
                    elif speed_two and speed_four_request is None and snapshot.processed_boundary >= 200:
                        speed_four_request = now
                        command("speed_4", lambda: window.set_speed(4.0))
            if manager.failure is not None:
                record_error(f"Replay failure: {manager.failure}")
            if manager.ended:
                if finalizing_since is None:
                    finalizing_since = now
                restart_pending = restart_requested and snapshot.generation < 2
                if restart_pending and manager.failure is None:
                    if restart_request_wall is not None and now - restart_request_wall > 12.0:
                        record_error("restart failed to establish generation 2 within 12 seconds")
                        application.quit()
                elif manager.clean_shutdown or manager.failure is not None:
                    application.quit()
                elif now - finalizing_since > 12.0:
                    record_error("artifact finalization or clean shutdown exceeded 12 seconds")
                    application.quit()
            else:
                finalizing_since = None
            pass_budget = 2 if args.scenario == "controls" else 1
            maximum = pass_budget * limit / spec.frame_rate_hz / args.speed + 120.0
            if now - started > maximum:
                record_error("acceptance scenario timed out")
                application.quit()
        except Exception as exc:
            record_error(f"Acceptance control: {type(exc).__name__}: {exc}")
            application.quit()

    timer = QtCore.QTimer()
    timer.setInterval(50)
    timer.timeout.connect(tick)
    manager.start()
    timer.start()
    print(f"Native {args.scenario} replay started: {output}", flush=True)
    try:
        application.exec()
        manager.stop(timeout_s=6.0)
        timed_ui_poll()
        final = manager.latest_snapshot
        screenshot = window.save_screenshot(output / "dashboard_final.png")
        observations["screenshots"].append(str(screenshot))
        observations.update({
            "wall_elapsed_s": time.monotonic() - started,
            "final_pass_dir": str(manager.pass_dir),
            "final_generation": final.generation,
            "processed_frames": final.processed_boundary,
            "final_state": final.playback_state,
            "coverage": asdict(final.coverage),
            "clean_shutdown": manager.clean_shutdown,
            "ui_callback_max_s": max(ui_callback_times, default=0.0),
            "ui_callback_over_0_25s": sum(value > 0.25 for value in ui_callback_times),
            "ui_snapshot_age_max_s": max(ui_snapshot_ages, default=0.0),
            "qt_heartbeat_max_s": max(tick_intervals, default=0.0),
            "qt_heartbeat_over_0_25s": sum(value > 0.25 for value in tick_intervals),
            "source_frames_read": manager.current_run.source.frames_read,
            "final_analysis_process": manager.current_run.analysis_process_observation(),
        })
        performance = [
            json.loads(line)
            for line in (manager.pass_dir / "performance.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        with (manager.pass_dir / "analysis_attempts.csv").open(encoding="utf-8", newline="") as handle:
            attempt_ids = [row["job_id"] for row in csv.DictReader(handle)]
        observations["process_telemetry_errors"] = process_performance_errors(performance, attempt_ids)
        observations["errors"].extend(observations["process_telemetry_errors"])
        frame_rows = [row for row in performance if row.get("kind") == "frame"]
        work_times = [float(row["controller_work_elapsed_s"]) for row in frame_rows]
        selector_times = [
            float(row["selector_elapsed_s"])
            for row in performance
            if row.get("selector_elapsed_s") is not None
        ]
        observations["controller_work_max_s"] = max(work_times, default=None)
        observations["selector_elapsed_s"] = selector_times
        observations["frame_telemetry_rows"] = len(frame_rows)
        rss_samples = [
            row for row in observations["samples"]
            if isinstance(row.get("rss_bytes"), int)
        ]
        observations["rss_peak_bytes"] = max(
            (row["rss_bytes"] for row in rss_samples), default=None
        )
        observations["combined_rss_observation"] = memory_observation(
            observations["samples"], "combined_current_rss_bytes", final.generation
        )
        observations["child_current_rss_observation"] = memory_observation(
            [{**row, "child_current_rss_bytes": row["analysis_process"]["current_rss_bytes"]}
             for row in observations["samples"]], "child_current_rss_bytes", final.generation
        )
        child_ids: dict[int, set[int]] = {}
        for row in observations["samples"]:
            pid = row["analysis_process"]["pid"]
            if type(pid) is int:
                child_ids.setdefault(row["generation"], set()).add(pid)
        observations["observed_analysis_pids_by_generation"] = {
            str(generation): sorted(pids) for generation, pids in child_ids.items()
        }
        if any(len(pids) > 1 for pids in child_ids.values()):
            observations["errors"].append("more than one analysis child was observed in one generation")
        observations["owned_analysis_process_count_max"] = max(
            (row["owned_analysis_process_count"] for row in observations["samples"]), default=0
        )
        if observations["owned_analysis_process_count_max"] > 1:
            observations["errors"].append("overlapping owned analysis children were observed")
        if args.scenario == "controls" and (
            len(child_ids.get(1, set())) != 1 or len(child_ids.get(2, set())) != 1
            or child_ids[1] == child_ids[2]
        ):
            observations["errors"].append("restart did not establish an observed fresh analysis PID")
        # Report measured growth without inventing an absolute RAM threshold.
        # Initial ring accumulation and the first selector are excluded from
        # the steady-playback comparison, but remain in the overall peak.
        stable = [
            row for row in rss_samples
            if row["generation"] == final.generation and row["boundary"] >= 1800
        ]
        if len(stable) >= 8:
            quarter = max(1, len(stable) // 4)
            early = statistics.median(row["rss_bytes"] for row in stable[:quarter])
            late_rows = stable[-quarter:]
            late = statistics.median(row["rss_bytes"] for row in late_rows)
            elapsed = late_rows[-1]["monotonic_s"] - late_rows[0]["monotonic_s"]
            observations["rss_steady_observation"] = {
                "first_boundary": stable[0]["boundary"],
                "last_boundary": stable[-1]["boundary"],
                "early_quarter_median_bytes": early,
                "late_quarter_median_bytes": late,
                "late_minus_early_bytes": late - early,
                "late_quarter_endpoint_bytes_per_s": (
                    (late_rows[-1]["rss_bytes"] - late_rows[0]["rss_bytes"]) / elapsed
                    if elapsed > 0 else None
                ),
                "interpretation": "Observed memory; inspect alongside bounded-ring and queue tests.",
            }
        playback = [
            json.loads(line)
            for line in (manager.pass_dir / "playback_events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        prepared = [row for row in playback if row.get("kind") == "source_prepared"]
        if prepared and frame_rows:
            playback_wall = float(frame_rows[-1]["monotonic_s"]) - float(prepared[0]["monotonic_s"])
            observations["playback_wall_elapsed_s"] = playback_wall
            observations["observed_frames_per_wall_second"] = (
                int(frame_rows[-1]["processed_boundary"]) / playback_wall
            )
        if final.processed_boundary != limit or not manager.clean_shutdown:
            observations["errors"].append("scenario did not finish every authorized frame with clean shutdown")
        child_final = observations["final_analysis_process"]
        if child_final["alive"]:
            observations["errors"].append("owned analysis child remains alive after shutdown")
        if child_final["exitcode"] != 0:
            observations["errors"].append("owned analysis child did not exit successfully")
        if not any(type(row["analysis_process"]["current_rss_bytes"]) is int
                   for row in observations["samples"]):
            observations["errors"].append("owned analysis child memory observations are missing")
        if manager.current_run.source.frames_read != limit or len(frame_rows) != limit:
            observations["errors"].append("source/frame telemetry does not account for every processed frame")
        if args.scenario == "controls" and not all((resumed, restarted, speed_two, speed_four)):
            observations["errors"].append("not all requested controls were acknowledged")
        if args.scenario == "controls":
            changed = [row.get("speed") for row in playback if row.get("kind") == "speed_changed"]
            if 2.0 not in changed or 4.0 not in changed:
                observations["errors"].append("durable playback events do not acknowledge both speed changes")
            for name in ("speed_2_ack_elapsed_s", "speed_4_ack_elapsed_s"):
                if observations.get(name, float("inf")) > 0.25:
                    observations["errors"].append(f"{name} exceeded the 0.25-second target")
        if args.scenario == "controls" and observations.get("pause_ack_elapsed_s", float("inf")) > 0.25:
            observations["errors"].append("pause acknowledgment exceeded the 0.25-second target")
        if any(row["callback_elapsed_s"] > 0.25 for row in observations["commands"]):
            observations["errors"].append("a control callback exceeded the 0.25-second target")
        if args.scenario == "full":
            if not work_times or not ui_snapshot_ages:
                observations["errors"].append("required controller/UI responsiveness telemetry is missing")
            elif max(work_times) > 0.25 or max(ui_snapshot_ages) > 0.25:
                observations["errors"].append("controller/UI snapshot lag exceeded the 0.25-second target")
            if not tick_intervals or max(tick_intervals) > 0.25:
                observations["errors"].append("Qt heartbeat exceeded the 0.25-second responsiveness target")
            if not ui_callback_times or max(ui_callback_times) > 0.25:
                observations["errors"].append("UI poll callback exceeded the 0.25-second responsiveness target")
    except Exception as exc:
        observations["errors"].append(f"Acceptance shutdown: {type(exc).__name__}: {exc}")
    finally:
        timer.stop()
        ui_timing.close()
        sys.excepthook = exception_hook
        observations["finished_utc"] = datetime.now(timezone.utc).isoformat()
        path = output / "acceptance_observations.json"
        path.write_text(json.dumps(observations, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        window.close()
    print(json.dumps({"observations": str(path), "errors": observations["errors"]}), flush=True)
    return int(bool(observations["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
