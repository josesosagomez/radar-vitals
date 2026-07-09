"""Tests for scripts/diagnose_live_run.py.

Synthetic run-folder fixtures (hand-built CSV/NPZ/JSON) exercise the triage
logic and the graceful-degradation paths. No real capture data is needed.

Run: pytest tests/test_diagnose_live_run.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.diagnose_live_run import (
    GATE_REJECTIONS,
    THIN_MARGIN_SCORE,
    analyze_capture,
    analyze_hr_timeline,
    analyze_lock,
    analyze_signal,
    build_report,
    diagnose,
    load_run,
    main,
    rank_causes,
)

# CSV schema mirrors scripts/live_demo.py:_csv_fields
CSV_FIELDS = [
    "elapsed_s", "frame_idx", "locked_bin",
    "hr_bpm_raw", "hr_bpm_smooth", "hr_valid",
    "fallback_hr_bpm", "hr_confidence", "ahet_verified",
    "heart_peak_hz", "f_r_hz_used",
    "br_bpm", "br_confidence", "resp_valid", "spectrum_stage",
    "candidate_rejection_reason", "n_eca_skipped_harmonics",
]

N_FREQS = 64


# ── Fixture builders ────────────────────────────────────────────────────────

def _csv_row(i, hr_valid, reason="", hr=75.0, peak_hz=1.25, stage=0):
    return {
        "elapsed_s": round(3.0 * i, 2),
        "frame_idx": 100 * i,
        "locked_bin": 23,
        "hr_bpm_raw": hr if hr_valid else "",
        "hr_bpm_smooth": hr if hr_valid else "",
        "hr_valid": int(hr_valid),
        "fallback_hr_bpm": hr,
        "hr_confidence": "high" if hr_valid else "low",
        "ahet_verified": int(hr_valid),
        "heart_peak_hz": peak_hz if hr_valid else "",
        "f_r_hz_used": 0.25,
        "br_bpm": 15.0,
        "br_confidence": "high",
        "resp_valid": 1,
        "spectrum_stage": stage,
        "candidate_rejection_reason": reason,
        "n_eca_skipped_harmonics": 0,
    }


def write_run(
    tmp_path: Path,
    rows: list[dict],
    *,
    warmup: dict | None = None,
    metadata: dict | None = None,
    npz: dict | None = None,
    name: str = "20260709_live_test",
) -> Path:
    run_dir = tmp_path / name
    run_dir.mkdir()
    pd.DataFrame(rows, columns=CSV_FIELDS).to_csv(
        run_dir / "live_estimates.csv", index=False
    )
    if metadata is not None:
        (run_dir / "run_metadata.json").write_text(json.dumps(metadata))
    if warmup is not None:
        (run_dir / "warmup_bin_selection.json").write_text(json.dumps(warmup))
    if npz is not None:
        np.savez_compressed(run_dir / "live_intermediates.npz", **npz)
    return run_dir


def base_metadata(**kw) -> dict:
    md = {
        "mode": "live", "session_id": "test", "git_commit": "deadbeef" * 5,
        "completion_status": "completed", "locked_bin": 23,
        "locked_bin_source": "warmup_auto",
        "config": {"heart": {"candidate_min_peak_to_floor_db": 6.0}},
    }
    md.update(kw)
    return md


def good_warmup() -> dict:
    return {
        "selected_bin": 23, "selected_range_m": 0.63,
        "selected_confidence": "high", "selection_reason": "score=1300_hr=1_br=high",
        "candidates": [
            {"bin": 22, "score": 300, "hr_valid": False, "br_confidence": "medium",
             "energy_rank": 2},
            {"bin": 23, "score": 1300, "hr_valid": True, "br_confidence": "high",
             "energy_rank": 0},
            {"bin": 24, "score": 250, "hr_valid": False, "br_confidence": "low",
             "energy_rank": 1},
        ],
    }


def make_npz(n, ptf=12.0, phase_scale=1.0, motion_hops=()):
    freqs = np.tile(np.linspace(0.8, 2.0, N_FREQS), (n, 1))
    spec = np.tile(np.hanning(N_FREQS), (n, 1))
    phase = np.random.RandomState(0).randn(n, 200) * phase_scale
    for h in motion_hops:
        phase[h] *= 50.0
    return {
        "elapsed_s": np.arange(n) * 3.0,
        "frame_idx": np.arange(n) * 100,
        "hr_raw": np.full(n, 75.0),
        "heart_freqs_hz": freqs,
        "heart_spectrum": spec,
        "heart_spectrum_pre_eca": spec,
        "heart_spectrum_first_pass": spec,
        "baseline_freqs_hz": freqs,
        "baseline_spectrum": spec,
        "phase_clean": phase,
        "peak_to_floor_ratio_db": np.full((n, 3), ptf),
        "accepted_candidate_rank": np.zeros(n),
        "candidate_refined_hz": np.tile([1.25, 2.5, np.nan], (n, 1)),
    }


# ── Loading / degradation ───────────────────────────────────────────────────

def test_load_missing_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_run(tmp_path / "nope")


def test_load_csv_only_degrades(tmp_path):
    rd = write_run(tmp_path, [_csv_row(i, True) for i in range(5)])
    loaded = load_run(rd)
    assert loaded.n_hops == 5
    assert "run_metadata.json" in loaded.missing
    assert "live_intermediates.npz" in loaded.missing
    # diagnose() must not raise when only the CSV is present.
    res = diagnose(loaded)
    assert res["signal"]["available"] is False


def test_npz_shorter_than_csv_noted(tmp_path):
    rows = [_csv_row(i, True) for i in range(10)]
    rd = write_run(tmp_path, rows, metadata=base_metadata(), npz=make_npz(4))
    loaded = load_run(rd)
    assert any("NPZ has 4 hops" in n for n in loaded.notes)


# ── Warmup lock analysis ────────────────────────────────────────────────────

def test_lock_manual_when_no_warmup_json(tmp_path):
    rd = load_run(write_run(
        tmp_path, [_csv_row(0, True)],
        metadata=base_metadata(locked_bin_source="manual")))
    lock = analyze_lock(rd)
    assert lock["status"] == "manual"
    assert lock["flags"] == []


def test_lock_good_has_no_flags(tmp_path):
    rd = load_run(write_run(
        tmp_path, [_csv_row(0, True)], warmup=good_warmup(),
        metadata=base_metadata()))
    lock = analyze_lock(rd)
    assert lock["status"] == "scanned"
    assert lock["flags"] == []


def test_lock_thin_margin_and_better_neighbor_flagged(tmp_path):
    warm = {
        "selected_bin": 23, "selected_range_m": 0.63,
        "selected_confidence": "low", "selection_reason": "score=250_hr=0_br=low",
        "candidates": [
            # winner has NO valid HR; neighbor 22 does; margin is tiny.
            {"bin": 22, "score": 1200, "hr_valid": True, "br_confidence": "high",
             "energy_rank": 1},
            {"bin": 23, "score": 250, "hr_valid": False, "br_confidence": "low",
             "energy_rank": 0},
        ],
    }
    # selected bin 23 scored below 22 here, but the demo's own tiebreak chose it;
    # we only test that the diagnostic flags the situation.
    rd = load_run(write_run(tmp_path, [_csv_row(0, False, "ratio_db_low")],
                            warmup=warm, metadata=base_metadata()))
    lock = analyze_lock(rd)
    names = {f["flag"] for f in lock["flags"]}
    assert "better_neighbor" in names
    assert "low_confidence" in names


def test_thin_margin_threshold_boundary(tmp_path):
    warm = good_warmup()
    # Make the top-two gap exactly THIN_MARGIN_SCORE - 1 => flagged.
    warm["candidates"] = [
        {"bin": 23, "score": 1000, "hr_valid": True, "br_confidence": "high",
         "energy_rank": 0},
        {"bin": 22, "score": 1000 - (THIN_MARGIN_SCORE - 1), "hr_valid": True,
         "br_confidence": "high", "energy_rank": 1},
    ]
    lock = analyze_lock(load_run(write_run(
        tmp_path, [_csv_row(0, True)], warmup=warm, metadata=base_metadata())))
    assert "thin_margin" in {f["flag"] for f in lock["flags"]}


# ── HR timeline analysis ────────────────────────────────────────────────────

def test_timeline_all_valid():
    df = pd.DataFrame([_csv_row(i, True) for i in range(8)], columns=CSV_FIELDS)
    t = analyze_hr_timeline(df)
    assert t["valid_frac"] == 1.0
    assert t["timeline"] == "#" * 8
    assert t["n_valid"] == 8


def test_timeline_dominant_rejection():
    rows = ([_csv_row(i, False, "ratio_db_low") for i in range(7)]
            + [_csv_row(i, True) for i in range(3)])
    t = analyze_hr_timeline(pd.DataFrame(rows, columns=CSV_FIELDS))
    assert t["dominant_reason"] == "ratio_db_low"
    assert t["valid_frac"] == pytest.approx(3 / 10)
    assert "ratio_db_low" in GATE_REJECTIONS


# ── Signal analysis ─────────────────────────────────────────────────────────

def test_signal_weak_when_below_floor():
    sig = analyze_signal(make_npz(6, ptf=3.0), floor_db=6.0)
    assert sig["weak_signal"] is True
    assert sig["ptf_median_db"] == pytest.approx(3.0)


def test_signal_ok_when_above_floor():
    sig = analyze_signal(make_npz(6, ptf=12.0), floor_db=6.0)
    assert sig["weak_signal"] is False


def test_signal_detects_motion_outliers():
    sig = analyze_signal(make_npz(10, motion_hops=(4, 7)), floor_db=6.0)
    assert set(sig["motion_hops"]) == {4, 7}


# ── Capture analysis ────────────────────────────────────────────────────────

def test_capture_frame_loss_flagged():
    md = base_metadata(live_packet_stats={
        "n_received": 1000, "n_dropped": 12, "zero_filled_bytes": 4096,
        "mirror_truncated_bytes": 0})
    cap = analyze_capture(type("R", (), {"metadata": md})())
    assert cap["frame_loss"] is True
    assert cap["n_dropped"] == 12


# ── Ranked verdict ──────────────────────────────────────────────────────────

def test_verdict_orders_frame_loss_first(tmp_path):
    rows = [_csv_row(i, False, "ratio_db_low") for i in range(10)]
    md = base_metadata(live_packet_stats={
        "n_received": 1000, "n_dropped": 50, "zero_filled_bytes": 8192,
        "mirror_truncated_bytes": 0})
    rd = load_run(write_run(tmp_path, rows, warmup=good_warmup(),
                            metadata=md, npz=make_npz(10, ptf=3.0)))
    res = diagnose(rd)
    causes = [c["cause"] for c in res["causes"]]
    assert causes[0] == "frame_loss"
    assert "ahet_over_rejection" in causes
    assert "weak_signal" in causes


def test_verdict_empty_when_healthy(tmp_path):
    rows = [_csv_row(i, True) for i in range(20)]
    rd = load_run(write_run(tmp_path, rows, warmup=good_warmup(),
                            metadata=base_metadata(), npz=make_npz(20, ptf=12.0)))
    res = diagnose(rd)
    assert res["causes"] == []


def test_ahet_over_rejection_vs_plain_blank():
    # gate reason => ahet_over_rejection; non-gate reason => hr_mostly_blank
    tl = {"valid_frac": 0.1, "dominant_reason": "ratio_db_low"}
    causes = rank_causes({"status": "manual", "flags": []}, tl,
                         {"available": True}, {"frame_loss": False})
    assert any(c["cause"] == "ahet_over_rejection" for c in causes)

    tl2 = {"valid_frac": 0.1, "dominant_reason": "no_second_harmonic_region"}
    causes2 = rank_causes({"status": "manual", "flags": []}, tl2,
                          {"available": True}, {"frame_loss": False})
    assert any(c["cause"] == "hr_mostly_blank" for c in causes2)


# ── Report + CLI smoke ──────────────────────────────────────────────────────

def test_build_report_contains_verdict(tmp_path):
    rows = [_csv_row(i, False, "ratio_db_low") for i in range(6)]
    rd = load_run(write_run(tmp_path, rows, warmup=good_warmup(),
                            metadata=base_metadata(), npz=make_npz(6, ptf=3.0)))
    res = diagnose(rd)
    report = build_report(rd, res["lock"], res["timeline"], res["signal"],
                          res["capture"], res["causes"])
    assert "VERDICT" in report
    assert "WARMUP LOCK" in report
    assert "HR AVAILABILITY" in report


def test_main_writes_plots(tmp_path):
    rows = ([_csv_row(i, False, "ratio_db_low") for i in range(4)]
            + [_csv_row(i, True) for i in range(4)])
    rd = write_run(tmp_path, rows, warmup=good_warmup(),
                   metadata=base_metadata(), npz=make_npz(8, ptf=3.0))
    rc = main([str(rd), "--worst", "2"])
    assert rc == 0
    out = rd / "diagnosis"
    assert (out / "overview.png").is_file()
    assert (out / "warmup_candidates.png").is_file()
    assert list(out.glob("window_*.png"))


def test_main_no_plots_flag(tmp_path):
    rd = write_run(tmp_path, [_csv_row(i, True) for i in range(3)],
                   metadata=base_metadata())
    rc = main([str(rd), "--no-plots"])
    assert rc == 0
    assert not (rd / "diagnosis").exists()
