"""Tests for the restructured live-demo artifact verifier.

Covers the relocking_bin_plan.md verifier requirements: warmup-auto runs,
pinned manual/manifest runs without warmup JSON, relock lock-bin sequences,
and rejection of CSV locked-bin changes not backed by an accepted event.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.verify_live_demo_artifacts import verify_run  # noqa: E402

_CSV_FIELDS = ["elapsed_s", "frame_idx", "locked_bin", "hr_bpm_raw", "br_bpm"]


def _write_run(
    run_dir: Path,
    *,
    source: str = "warmup_auto",
    csv_bins: list[int],
    final_bin: int | None = None,
    warmup_bin: int | None = None,
    relock_enabled: bool = False,
    events: list[dict] | None = None,
    write_warmup_json: bool | None = None,
) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    events = events or []
    accepted = [e for e in events if e.get("switched")]
    if final_bin is None:
        final_bin = csv_bins[-1]
    if warmup_bin is None and source == "warmup_auto":
        warmup_bin = csv_bins[0]
    if write_warmup_json is None:
        write_warmup_json = source == "warmup_auto"

    warmup_path = run_dir / "warmup_bin_selection.json"
    meta = {
        "mode": "replay",
        "completion_status": "completed",
        "locked_bin_source": source,
        "locked_bin": final_bin,
        "warmup_selected_bin": warmup_bin,
        "warmup_selection_confidence": "medium" if warmup_bin is not None else None,
        "warmup_selection_path": str(warmup_path) if write_warmup_json else None,
        "range_resolution_m": 0.0436,
        "relock_enabled": relock_enabled,
        "n_relock_scans": len(events),
        "n_relock_switches": len(accepted),
        "relock_events_path": str(run_dir / "relock_events.json") if events else None,
        "config": {
            "bin_selection": {"candidate_bins": None},
            "protocol": {"subject_distance_m": [1.0, 1.4]},
        },
    }
    (run_dir / "run_metadata.json").write_text(json.dumps(meta, indent=2))

    if write_warmup_json:
        warmup = {
            "selected_bin": warmup_bin,
            "selected_confidence": "medium",
            "candidates": [
                {"bin": warmup_bin, "failed": False, "hr_raw": None, "br_bpm": 16.0}
            ],
        }
        warmup_path.write_text(json.dumps(warmup, indent=2))

    if events:
        (run_dir / "relock_events.json").write_text(json.dumps(events, indent=2))

    with (run_dir / "live_estimates.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for i, b in enumerate(csv_bins):
            writer.writerow({
                "elapsed_s": f"{30 + 3 * i:.2f}",
                "frame_idx": 599 + 60 * i,
                "locked_bin": b,
                "hr_bpm_raw": "",
                "br_bpm": "16.00" if i == 0 else "15.50",
            })

    np.savez(str(run_dir / "live_intermediates.npz"), x=np.array([1]))
    return run_dir


def _event(old_bin: int, new_bin: int, switched: bool = True) -> dict:
    return {
        "elapsed_s": 45.0,
        "dsp_hop_idx": 5,
        "old_locked_bin": old_bin,
        "candidate_bins": list(range(old_bin - 2, old_bin + 3)),
        "selected_bin": new_bin,
        "selected_confidence": "medium",
        "switched": switched,
        "reason": "test",
        "t_relock_scan_ms": 100.0,
    }


def test_warmup_auto_single_bin_passes(tmp_path, capsys):
    run = _write_run(tmp_path / "run", csv_bins=[25, 25, 25])
    assert verify_run(run, expect_mode="replay") == 0
    assert "FAIL" not in capsys.readouterr().out


def test_pinned_manual_without_warmup_json_passes(tmp_path, capsys):
    run = _write_run(
        tmp_path / "run", source="manual", csv_bins=[27, 27], relock_enabled=False
    )
    assert verify_run(run, expect_mode="replay") == 0
    assert "FAIL" not in capsys.readouterr().out


def test_pinned_manifest_without_warmup_json_passes(tmp_path):
    run = _write_run(
        tmp_path / "run", source="manifest", csv_bins=[24, 24], relock_enabled=False
    )
    assert verify_run(run, expect_mode="replay") == 0


def test_relock_switch_sequence_accepted(tmp_path, capsys):
    run = _write_run(
        tmp_path / "run",
        csv_bins=[25, 25, 27, 27],
        final_bin=27,
        warmup_bin=25,
        relock_enabled=True,
        events=[_event(25, 27)],
    )
    assert verify_run(run, expect_mode="replay") == 0
    assert "FAIL" not in capsys.readouterr().out


def test_unexplained_csv_bin_change_rejected(tmp_path, capsys):
    # CSV changes bin but there is no relock event backing it.
    run = _write_run(
        tmp_path / "run",
        csv_bins=[25, 25, 27, 27],
        final_bin=27,
        warmup_bin=25,
        relock_enabled=True,
        events=[],
    )
    assert verify_run(run, expect_mode="replay") == 1
    assert "csv_bins_explained_by_relock_events: FAIL" in capsys.readouterr().out


def test_non_accepted_scan_event_does_not_permit_bin_change(tmp_path):
    # A scan happened but was not accepted; the CSV must not change bins.
    run = _write_run(
        tmp_path / "run",
        csv_bins=[25, 25, 27],
        final_bin=27,
        warmup_bin=25,
        relock_enabled=True,
        events=[_event(25, 27, switched=False)],
    )
    assert verify_run(run, expect_mode="replay") == 1


def test_pinned_relock_disabled_rejects_multiple_bins(tmp_path, capsys):
    run = _write_run(
        tmp_path / "run",
        source="manual",
        csv_bins=[25, 27],
        final_bin=27,
        relock_enabled=False,
    )
    assert verify_run(run, expect_mode="replay") == 1
    assert "pinned_single_locked_bin: FAIL" in capsys.readouterr().out


def test_final_metadata_bin_must_match_accepted_sequence(tmp_path, capsys):
    # Accepted switch to 27 but metadata still claims 25.
    run = _write_run(
        tmp_path / "run",
        csv_bins=[25, 25, 27],
        final_bin=25,
        warmup_bin=25,
        relock_enabled=True,
        events=[_event(25, 27)],
    )
    assert verify_run(run, expect_mode="replay") == 1
    assert "final_locked_bin_matches_sequence: FAIL" in capsys.readouterr().out


def test_broken_event_chain_rejected(tmp_path, capsys):
    # Accepted event claims old bin 26, but the run started at 25.
    run = _write_run(
        tmp_path / "run",
        csv_bins=[25, 27],
        final_bin=27,
        warmup_bin=25,
        relock_enabled=True,
        events=[_event(26, 27)],
    )
    assert verify_run(run, expect_mode="replay") == 1
    assert "relock_event_chain_ordered: FAIL" in capsys.readouterr().out


def test_event_count_mismatch_rejected(tmp_path, capsys):
    run = _write_run(
        tmp_path / "run",
        csv_bins=[25, 27],
        final_bin=27,
        warmup_bin=25,
        relock_enabled=True,
        events=[_event(25, 27)],
    )
    # Corrupt the metadata counters after writing.
    meta_path = run / "run_metadata.json"
    meta = json.loads(meta_path.read_text())
    meta["n_relock_scans"] = 0
    meta_path.write_text(json.dumps(meta))
    assert verify_run(run, expect_mode="replay") == 1
    assert "relock_event_counts_consistent: FAIL" in capsys.readouterr().out
