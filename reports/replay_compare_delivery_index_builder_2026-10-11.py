"""Static-only delivery index, read from retained primary-run evidence."""
import hashlib
import json
import subprocess
import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = ROOT / "results/test_tmp"
SOURCE = "522c4dfc0457036942b89a6bf2fd336c4262b337"
BASE = "dcda615d1a2984848882ed7da6641860331577b1"
SUFFIXES = {
    "full_1": "2f3374fb38fc4b02b71592cd2cbd8837",
    "smoke_1": "c9f561ee6ceb498dac073495ab8f1097",
    "smoke_0p5": "607417d2275041b68b961abf89b94235",
    "smoke_2": "3213fb47f95545f08e4ba70d3eb4070f",
    "smoke_4": "57802d722e354e46bff89d2676c57eb6",
    "controls_1": "f0b56324fad4433f86a82df71c6f84fe",
}
VERIFIERS = {
    "smoke_1": ["1fc56ba5889d486eb7bf8be7ff291207"],
    "smoke_0p5": ["160097501472456b9a0b2f91b39331b6"],
    "smoke_2": ["dc942eabfece4ce788794b0db9dad90d"],
    "smoke_4": ["f51d96809703410181be7df688fc9b72"],
    "controls_1": ["cd08154577b440ffaba2e789df78be74", "1aed70d97600403ca2255d3ae9797825"],
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path):
    return path.resolve().relative_to(ROOT).as_posix()


def record(path):
    assert path.is_file(), path
    return {"path": relative(path), "sha256": digest(path), "bytes": path.stat().st_size}


def main():
    # Supply the final full verifier GUID after it has actually completed.
    import sys
    assert len(sys.argv) == 2, "full verifier GUID required"
    assert re.fullmatch(r"[0-9a-f]{32}", sys.argv[1])
    VERIFIERS["full_1"] = [sys.argv[1]]
    changed = subprocess.check_output(["git", "diff", "--name-only", BASE, SOURCE],
        cwd=ROOT, text=True).splitlines()
    software = {p: digest(ROOT / p) for p in changed
        if p.startswith(("src/", "scripts/", "tests/"))}
    assert len(software) == 36
    for path, expected in software.items():
        committed = subprocess.check_output(["git", "show", SOURCE+":"+path], cwd=ROOT)
        assert hashlib.sha256(committed).hexdigest() == expected, path
    protected = json.loads((ROOT / "reports/replay_compare_protected_sources_2026-10-07.json").read_text())["frozen_sha256"]
    assert len(protected) == 20
    for p, expected in protected.items():
        assert digest(ROOT / p) == expected, p
    runs = {}
    for name, suffix in SUFFIXES.items():
        tag = "replay_native_final_"+name+"_20261011_"+suffix
        directory = ROOT / "results/replay_compare" / tag
        observation_path = directory / "acceptance_observations.json"
        observation = json.loads(observation_path.read_text())
        assert observation["source_commit"] == SOURCE and observation["scientific_source_status"] == ""
        assert observation["driver_sha256"] == software["scripts/run_replay_compare_acceptance.py"]
        assert observation["clean_shutdown"] and not observation["errors"]
        assert observation["final_state"] == "complete"
        assert observation["processed_frames"] == (12005 if name == "full_1" else 800)
        assert observation["source_frames_read"] == observation["processed_frames"]
        assert observation["frame_telemetry_rows"] == observation["processed_frames"]
        assert not observation["process_telemetry_errors"]
        assert observation["ui_callback_over_0_25s"] == observation["qt_heartbeat_over_0_25s"] == 0
        assert observation["owned_analysis_process_count_max"] == 1
        assert observation["final_analysis_process"]["alive"] is False
        assert observation["final_analysis_process"]["exitcode"] == 0
        for key in ("controller_work_max_s", "ui_callback_max_s", "ui_snapshot_age_max_s", "qt_heartbeat_max_s"):
            assert observation[key] <= .25, (name, key, observation[key])
        native_exit = TMP / (tag+".exit.txt")
        assert native_exit.read_text().strip() == "0", native_exit
        verification = []
        passes = sorted(directory.glob("pass_*"))
        assert len(passes) == len(VERIFIERS[name])
        for pass_dir, verifier_id in zip(passes, VERIFIERS[name]):
            verifier_tag = "replay_native_final_verify_1_20261011_"+verifier_id
            console = TMP / (verifier_tag+".console.txt")
            status = TMP / (verifier_tag+".exit.txt")
            assert status.read_text().strip() == "0"
            output = console.read_text()
            parsed, _ = json.JSONDecoder().raw_decode(output[output.index("{"):])
            assert parsed["accepted"] and parsed["source_binding_complete"]
            assert parsed["internal_issues"] == [] and parsed["source_issues"] == []
            metadata = json.loads((pass_dir / "run_metadata.json").read_text())
            assert metadata["source_commit"] == SOURCE and metadata["randomness"] == "none"
            manifest = json.loads((pass_dir / "manifest.json").read_text())
            assert manifest["schema"] == "replay_compare_evidence_v1" and manifest["files"]
            members = {}
            for filename, expected in manifest["files"].items():
                member = (pass_dir / filename).resolve(strict=True)
                assert member.is_relative_to(pass_dir.resolve()), filename
                assert digest(member) == expected, member
                members[filename] = record(member)
            verification.append({"pass": relative(pass_dir), "stdout": record(console),
                "exit": record(status), "result": parsed,
                "manifest": record(pass_dir / "manifest.json"),
                "rechecked_members": members,
                "metadata": record(pass_dir / "run_metadata.json"),
                "summary": record(pass_dir / "summary.json")})
        screenshots = []
        captures = observation["screenshot_captures"]
        assert len(captures) == 2 and {c["slot"] for c in captures} == {"during", "final"}
        assert len({c["destination"] for c in captures}) == 2
        for capture in captures:
            path = Path(capture["destination"])
            assert path.resolve().is_relative_to(directory.resolve())
            assert capture["completion_status"] == "complete" and not capture["errors"]
            assert capture["width"] > 0 and capture["height"] > 0 and capture["png_bytes"] > 0
            assert path.stat().st_size == capture["png_bytes"]
            assert digest(path) == capture["png_sha256"]
            screenshots.append(record(path))
        runs[name] = {"observations": record(observation_path),
            "native_stdout": record(TMP / (tag+".console.txt")), "native_exit": record(native_exit),
            "verifications": verification, "screenshots": screenshots,
            "coverage": observation["coverage"], "processed_frames": observation["processed_frames"],
            "memory": {**{k: observation[k] for k in ("rss_peak_bytes", "combined_rss_observation", "child_current_rss_observation", "retained_screenshot_bytes_peak")},
                "rss_steady_observation": observation.get("rss_steady_observation")},
            "timing_max_s": {k: observation[k] for k in ("controller_work_max_s", "ui_callback_max_s", "ui_snapshot_age_max_s", "qt_heartbeat_max_s")}}
    full_metadata = json.loads((ROOT / runs["full_1"]["verifications"][0]["pass"] / "run_metadata.json").read_text())
    failed = ROOT / "results/replay_compare/replay_native_final_full_1_20261011_5abaa70e2325495197b943944b446cb1/acceptance_observations.json"
    refusal = ROOT / "results/replay_compare/replay_native_final_refusal_1_20261011_44762d9a681a4df78af6d4cc3ebedcb5/20261011T082322_228204Z/startup_refusal.json"
    refused = json.loads(refusal.read_text())
    assert refused["schema"] == "replay_compare_startup_refusal_v1" and refused["mode"] == "motion"
    assert refused["adc_content_was_not_opened"] is True and refused["hardware_was_not_accessed"] is True
    assert refused["capture_id"] == full_metadata["capture_id"]
    assert refused["effective_config_sha256"] == full_metadata["effective_config_sha256"]
    assert refused["registered_adc_expected_sha256"] == full_metadata["registered_adc_sha256"]
    refusal_tag = "replay_native_final_refusal_1_20261011_44762d9a681a4df78af6d4cc3ebedcb5"
    refusal_console = TMP / (refusal_tag+".console.txt")
    refusal_status = TMP / (refusal_tag+".exit.txt")
    assert refusal_status.read_text().strip() == "2"
    refusal_text = refusal_console.read_text()
    refusal_output, _ = json.JSONDecoder().raw_decode(refusal_text[refusal_text.index("{"):])
    assert refusal_output["started"] is False and refusal_output["reason"] == refused["reason"]
    assert Path(refusal_output["diagnostic"]).resolve() == refusal.resolve()
    test_tags = ("focus_20261011_65332374cb2f4698a65a79176753c6da", "full_20261011_49b229f4ff80404298e97cca3bc797fc")
    # Test output basenames are located explicitly by the retained runner tags.
    tests = [record(p) for p in sorted(TMP.glob("replay_qt_display_*.xml")) if any(t in p.name for t in test_tags)]
    assert len(tests) == 2
    for evidence in tests:
        path = ROOT / evidence["path"]
        suites = list(ET.parse(path).getroot().iter("testsuite"))
        assert suites and all(int(s.get("errors", "0")) == int(s.get("failures", "0")) == 0 for s in suites)
        evidence["junit_totals"] = {key: sum(int(s.get(key, "0")) for s in suites)
            for key in ("tests", "failures", "errors", "skipped")}
        evidence["passed"] = evidence["junit_totals"]["tests"] - evidence["junit_totals"]["skipped"]
        assert evidence["passed"] == (536 if "focus" in path.name else 4167)
        console = path.with_suffix(".console.txt")
        status = path.with_suffix(".exit.txt")
        assert status.read_text().strip() == "0"
        evidence["stdout"] = record(console)
        evidence["exit"] = record(status)
    result = {"schema": "replay_delivery_index_v1", "source_commit": SOURCE,
        "delivery_generator": record(Path(__file__)),
        "software_sha256": software, "protected_sha256": protected,
        "capture": {k: full_metadata[k] for k in ("capture_id", "registered_adc_sha256", "metadata_sha256", "warmup_sha256", "original_config_sha256", "effective_config_sha256", "random_seed", "randomness", "anchor_utc_us", "time_origin_approximate", "validity_provenance")},
        "reference_sha256": full_metadata["reference_source"]["sha256"],
        "tests": tests, "runs": runs, "earlier_same_commit_timing_failure": record(failed),
        "advanced_refusal": {"diagnostic": record(refusal), "stdout": record(refusal_console), "exit": record(refusal_status), "result": refusal_output},
        "limitations": ["Recorded development playback only; no physiological accuracy claim.",
            "An earlier identical-source native full run failed responsiveness; cause remains unestablished.",
            "Storage is capacity bounded; no universal RSS plateau or zero-growth claim.",
            "Advanced operation requires compatible accepted physical calibration; no hardware was operated."]}
    destination = ROOT / "reports/replay_compare_delivery_index_2026-10-11.json"
    with destination.open("w", encoding="utf-8", newline="\n") as output:
        output.write(json.dumps(result, indent=2, allow_nan=False)+"\n")
    print(json.dumps({"path": str(destination), "sha256": digest(destination), "runs": len(runs), "software_files": len(software)}))


if __name__ == "__main__":
    main()
