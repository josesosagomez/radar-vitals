"""Tests for the shared capture acceptance gate (src/capture_integrity.py).

The gate's whole value is rejecting captures that nothing downstream would notice,
so every check here is exercised in BOTH directions: a clean synthetic capture
must be accepted, and a capture corrupted in exactly one way must be rejected for
exactly that reason.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.capture_integrity import (  # noqa: E402
    CLIP_THRESHOLD,
    CaptureGeometry,
    evaluate_capture,
    format_report,
    gate_bins_from_distance,
)
from src.warmup_select import derive_candidate_bins  # noqa: E402

N_ADC = 256
N_RX = 4
N_CHIRPS = 32
RES_M = 0.0436
SUBJECT_BIN = 23           # ~1.0 m, inside the 0.8-1.4 m gate
DISTANCE_RANGE = [0.8, 1.4]

GEOM = CaptureGeometry(
    num_adc_samples=N_ADC, num_rx=N_RX, num_chirps_per_frame=N_CHIRPS,
    range_resolution_m=RES_M, iq_swap=True,
)
GATE = gate_bins_from_distance(DISTANCE_RANGE, RES_M, N_ADC)


def _write_capture(
    path: Path, n_frames: int = 3, target_bin: int = SUBJECT_BIN,
    amplitude: float = 1000.0, iq_swap_written: bool = True,
    trailing_junk_bytes: int = 0,
) -> Path:
    """Synthesise a .bin holding a single on-grid target at `target_bin`.

    `iq_swap_written` selects the packet order actually laid down, which is how a
    convention mismatch is simulated: write SampleSwap=0 order, read it as
    SampleSwap=1, and the target must appear mirrored.
    """
    total_complex = n_frames * N_CHIRPS * N_RX * N_ADC
    tone = amplitude * np.exp(2j * np.pi * target_bin * np.arange(N_ADC) / N_ADC)
    i_flat = np.tile(np.round(tone.real), total_complex // N_ADC).astype("<i2")
    q_flat = np.tile(np.round(tone.imag), total_complex // N_ADC).astype("<i2")

    raw = np.empty(total_complex * 2, dtype="<i2")
    if iq_swap_written:
        raw[0::4], raw[1::4] = q_flat[0::2], q_flat[1::2]
        raw[2::4], raw[3::4] = i_flat[0::2], i_flat[1::2]
    else:
        raw[0::4], raw[1::4] = i_flat[0::2], i_flat[1::2]
        raw[2::4], raw[3::4] = q_flat[0::2], q_flat[1::2]

    with path.open("wb") as fh:
        fh.write(raw.tobytes())
        if trailing_junk_bytes:
            fh.write(b"\x00" * trailing_junk_bytes)
    return path


def test_gate_matches_production_candidate_bins():
    """gate_bins_from_distance must agree with the bins warmup actually searches.

    If these drift apart, the gate would validate a range the selector never looks
    at, and a mirrored capture could pass while selection failed for another reason.
    """
    cfg = {
        "protocol": {"subject_distance_m": DISTANCE_RANGE},
        "profile": {"range_resolution_m": RES_M, "num_adc_samples": N_ADC},
    }
    produced = derive_candidate_bins(cfg)
    assert GATE == (min(produced), max(produced))


def test_clean_capture_is_accepted(tmp_path: Path):
    rec = evaluate_capture(
        _write_capture(tmp_path / "clean.bin"), GEOM, GATE,
        n_dropped=0, zero_filled_bytes=0, mirror_truncated_bytes=0,
    )
    assert rec["passed"], rec["checks"]
    assert all(c["passed"] for c in rec["checks"].values())
    assert rec["checks"]["iq_convention"]["gate_over_mirror_db"] > 10.0


def test_mirrored_iq_convention_is_rejected(tmp_path: Path):
    """The failure the whole check exists for: subject decoded to ~10 m."""
    path = _write_capture(tmp_path / "mirrored.bin", iq_swap_written=False)
    rec = evaluate_capture(path, GEOM, GATE, n_dropped=0, zero_filled_bytes=0)

    assert not rec["passed"]
    chk = rec["checks"]["iq_convention"]
    assert not chk["passed"]
    # Sign flips: energy sits in the mirror band instead of the gate.
    assert chk["gate_over_mirror_db"] < 0
    assert chk["alternative_convention_db"] == pytest.approx(-chk["gate_over_mirror_db"], abs=0.1)
    # Every other check still passes -- the rejection is specific, not incidental.
    assert rec["checks"]["frame_alignment"]["passed"]
    assert rec["checks"]["saturation"]["passed"]


def test_frame_misalignment_is_rejected(tmp_path: Path):
    path = _write_capture(tmp_path / "short.bin", trailing_junk_bytes=384)
    rec = evaluate_capture(path, GEOM, GATE, n_dropped=0, zero_filled_bytes=0)
    assert not rec["passed"]
    assert not rec["checks"]["frame_alignment"]["passed"]
    assert rec["checks"]["frame_alignment"]["remainder_bytes"] == 384


@pytest.mark.parametrize("dropped,zero_filled", [(1, 0), (0, 1456), (7, 10192)])
def test_packet_loss_is_rejected(tmp_path: Path, dropped: int, zero_filled: int):
    rec = evaluate_capture(
        _write_capture(tmp_path / f"loss_{dropped}_{zero_filled}.bin"), GEOM, GATE,
        n_dropped=dropped, zero_filled_bytes=zero_filled,
    )
    assert not rec["passed"]
    assert not rec["checks"]["packet_loss"]["passed"]


def test_packet_loss_absent_is_skipped_not_passed(tmp_path: Path):
    """A replay has no UDP stats. That must read as 'skipped', never as evidence."""
    rec = evaluate_capture(_write_capture(tmp_path / "replay.bin"), GEOM, GATE)
    chk = rec["checks"]["packet_loss"]
    assert chk["skipped"] is True
    assert chk["passed"] is True
    assert "reason" in chk
    assert rec["passed"]


def test_saturation_is_rejected(tmp_path: Path):
    path = _write_capture(tmp_path / "clipped.bin", amplitude=32767.0)
    rec = evaluate_capture(path, GEOM, GATE, n_dropped=0, zero_filled_bytes=0)
    assert not rec["passed"]
    sat = rec["checks"]["saturation"]
    assert not sat["passed"]
    assert sat["peak_abs_sample"] >= CLIP_THRESHOLD
    assert sat["n_samples_at_or_above_threshold"] > 0


def test_mirror_trim_of_a_whole_frame_is_rejected(tmp_path: Path):
    """Sub-frame trim is the deliberate frame-alignment behaviour; a whole frame is not."""
    path = _write_capture(tmp_path / "trim.bin")
    ok = evaluate_capture(path, GEOM, GATE, n_dropped=0, zero_filled_bytes=0,
                          mirror_truncated_bytes=GEOM.bytes_per_frame - 1)
    bad = evaluate_capture(path, GEOM, GATE, n_dropped=0, zero_filled_bytes=0,
                           mirror_truncated_bytes=GEOM.bytes_per_frame)
    assert ok["checks"]["mirror_trim"]["passed"]
    assert not bad["checks"]["mirror_trim"]["passed"]


def test_scene_margin_is_reported_but_never_gates(tmp_path: Path):
    """A stronger out-of-gate reflector must be surfaced without failing the capture.

    This is the 2026-07-28 scene: subject present and correctly decoded, but a
    static reflector behind them returns more. No defensible threshold exists, so
    the gate reports and does not judge.
    """
    total_complex = 3 * N_CHIRPS * N_RX * N_ADC
    subject = 1000.0 * np.exp(2j * np.pi * SUBJECT_BIN * np.arange(N_ADC) / N_ADC)
    intruder = 4000.0 * np.exp(2j * np.pi * 48 * np.arange(N_ADC) / N_ADC)  # ~2.09 m
    combined = subject + intruder
    i_flat = np.tile(np.round(combined.real), total_complex // N_ADC).astype("<i2")
    q_flat = np.tile(np.round(combined.imag), total_complex // N_ADC).astype("<i2")
    raw = np.empty(total_complex * 2, dtype="<i2")
    raw[0::4], raw[1::4] = q_flat[0::2], q_flat[1::2]
    raw[2::4], raw[3::4] = i_flat[0::2], i_flat[1::2]
    path = tmp_path / "scene.bin"
    path.write_bytes(raw.tobytes())

    rec = evaluate_capture(path, GEOM, GATE, n_dropped=0, zero_filled_bytes=0)

    assert rec["passed"], "scene margin must not gate"
    d = rec["diagnostics"]
    assert d["strongest_reflector_bin"] == 48
    assert d["strongest_reflector_in_gate"] is False
    assert d["gate_peak_bin"] == SUBJECT_BIN
    assert d["gate_peak_vs_strongest_db"] < 0
    # ...and it must be visible to a human reading the report.
    report = format_report(rec)
    assert "OUTSIDE the subject gate" in report
    assert "ACCEPTED" in report


def test_format_report_marks_a_rejection(tmp_path: Path):
    path = _write_capture(tmp_path / "mirrored2.bin", iq_swap_written=False)
    rec = evaluate_capture(path, GEOM, GATE, n_dropped=0, zero_filled_bytes=0)
    report = format_report(rec)
    assert "REJECTED" in report
    assert "iq_convention" in report


def test_empty_file_fails_closed(tmp_path: Path):
    path = tmp_path / "empty.bin"
    path.write_bytes(b"")
    rec = evaluate_capture(path, GEOM, GATE)
    assert not rec["passed"]
    assert not rec["checks"]["iq_convention"]["passed"]
    assert not rec["checks"]["saturation"]["passed"]
