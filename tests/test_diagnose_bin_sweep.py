"""Tests for `scripts/diagnose_bin_sweep.py`.

The sweep's conclusions are only as good as three things: that its frame-range decoder
agrees with `src/radar_io.read_adc_bin`, that it notices when window 0 stops reproducing
a capture's recorded warmup evidence, and that its verdict logic distinguishes "no bin
works" from "a different bin works better". Each is covered here, because a silent bug in
any of them would produce a confident wrong answer about where the coverage loss lives.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import diagnose_bin_sweep as dbs  # noqa: E402
from src.radar_io import ChirpConfig, read_adc_bin  # noqa: E402


def _cfg(iq_swap: bool = True, adc: int = 8, rx: int = 2, chirps: int = 4) -> ChirpConfig:
    return ChirpConfig(
        num_adc_samples=adc, num_rx=rx, num_tx=1, num_chirps_per_frame=chirps,
        num_frames=0, frame_rate_hz=20.0, range_resolution_m=0.0436, iq_swap=iq_swap,
    )


# ── frame-range decoding ────────────────────────────────────────────────────


@pytest.mark.parametrize("iq_swap", [False, True])
def test_decode_frame_range_matches_read_adc_bin(tmp_path, iq_swap):
    """Every frame slice must equal the same slice of a whole-file decode."""
    cfg = _cfg(iq_swap=iq_swap)
    n_frames = 6
    rng = np.random.default_rng(7)
    payload = rng.integers(-2048, 2048, size=n_frames * dbs.words_per_frame(cfg),
                           dtype=np.int16)
    path = tmp_path / "x.bin"
    path.write_bytes(payload.astype("<i2").tobytes())

    whole = read_adc_bin(path, cfg)
    raw = np.memmap(path, dtype="<i2", mode="r")

    for start in range(n_frames):
        for count in range(1, n_frames - start + 1):
            got = dbs.decode_frame_range(raw, cfg, start, count)
            assert got.shape == (count, cfg.num_chirps_per_frame, cfg.num_rx,
                                 cfg.num_adc_samples)
            assert np.array_equal(got, whole[start:start + count])


def test_decode_frame_range_rejects_out_of_range(tmp_path):
    cfg = _cfg()
    path = tmp_path / "x.bin"
    path.write_bytes(np.zeros(2 * dbs.words_per_frame(cfg), dtype="<i2").tobytes())
    raw = np.memmap(path, dtype="<i2", mode="r")
    with pytest.raises(ValueError, match="int16 words but the stream holds"):
        dbs.decode_frame_range(raw, cfg, 1, 5)


def test_words_per_frame_rejects_unpacketised_geometry():
    """A frame that does not divide into 4-word LVDS packets would mis-pair I/Q."""
    with pytest.raises(ValueError, match="4-word LVDS packet"):
        dbs.words_per_frame(_cfg(adc=1, rx=1, chirps=1))


def test_selfcheck_passes_for_the_production_geometry(tmp_path):
    result = dbs.verify_frame_range_decode(tmp_path, _cfg(adc=256, rx=4, chirps=32))
    assert result["passed"] is True
    assert {c["iq_swap"] for c in result["cases"]} == {False, True}


# ── window-0 reproduction check ─────────────────────────────────────────────


def _sweep_with(rows: list[dict]) -> dict:
    return {"capture_id": "c", "rows": rows}


def _row(bin_id: int, **kw) -> dict:
    row = {
        "k": 0, "bin": bin_id, "hr_valid": False, "rej_reason": "ratio_db_low",
        "br_bpm": 18.0, "br_confidence": "high",
    }
    row.update(kw)
    return row


def _warmup(bin_id: int, **kw) -> dict:
    cand = {
        "bin": bin_id, "hr_valid": False, "rej_reason": "ratio_db_low",
        "br_bpm": 18.0, "br_confidence": "high",
    }
    cand.update(kw)
    return {"candidates": [cand]}


def test_window0_reproduction_detects_agreement():
    check = dbs.verify_window0_reproduces_warmup(_sweep_with([_row(26)]), _warmup(26))
    assert check == {"checked": True, "bins_compared": 1, "reproduces": True,
                     "mismatches": [], "n_mismatches": 0}


def test_window0_reproduction_flags_a_moved_hr_verdict():
    """The failure that matters: offline now verifies HR where the live run did not."""
    check = dbs.verify_window0_reproduces_warmup(
        _sweep_with([_row(26, hr_valid=True, rej_reason="passed")]), _warmup(26)
    )
    assert check["reproduces"] is False
    assert {m["field"] for m in check["mismatches"]} == {"hr_valid", "rej_reason"}


def test_window0_reproduction_ignores_windows_after_zero():
    check = dbs.verify_window0_reproduces_warmup(
        _sweep_with([_row(26), _row(26, k=1, hr_valid=True)]), _warmup(26)
    )
    assert check["bins_compared"] == 1
    assert check["reproduces"] is True


def test_window0_reproduction_reports_when_unavailable():
    check = dbs.verify_window0_reproduces_warmup(_sweep_with([_row(26)]), None)
    assert check["checked"] is False


def test_window0_reproduction_tolerates_float_noise_but_not_real_drift():
    assert dbs.verify_window0_reproduces_warmup(
        _sweep_with([_row(26, br_bpm=18.0 + 1e-12)]), _warmup(26)
    )["reproduces"] is True
    assert dbs.verify_window0_reproduces_warmup(
        _sweep_with([_row(26, br_bpm=18.5)]), _warmup(26)
    )["reproduces"] is False


# ── aggregation and verdicts ────────────────────────────────────────────────


def _cell(bin_id: int, k: int, hr_valid: bool, hr_bpm=None, rel_db=-2.0) -> dict:
    return {
        "capture_id": "c", "k": k, "bin": bin_id, "dsp_failed": False,
        "hr_valid": hr_valid, "hr_bpm": hr_bpm, "br_valid": True, "br_bpm": 18.0,
        "rel_db_in_window": rel_db, "rej_reason": "passed" if hr_valid else "ratio_db_low",
        "ahet_ratio_db_best": 2.0 if hr_valid else -2.0,
    }


def _sweep(rows: list[dict], bins: list[int], locked: int | None) -> dict:
    return {
        "capture_id": "c", "candidate_bins": bins, "locked_bin": locked,
        "range_resolution_m": 0.0436, "n_windows_swept": 2, "rows": rows,
    }


def test_summarise_counts_yield_per_bin():
    sweep = _sweep(
        [_cell(20, 0, True, 80.0), _cell(20, 1, False),
         _cell(21, 0, False), _cell(21, 1, False)],
        [20, 21], locked=21,
    )
    per_bin = {r["bin"]: r for r in dbs.summarise(sweep)}
    assert per_bin[20]["n_hr_valid"] == 1 and per_bin[20]["hr_yield"] == 0.5
    assert per_bin[20]["hr_windows_k"] == [0]
    assert per_bin[21]["hr_yield"] == 0.0 and per_bin[21]["is_locked_bin"] is True
    assert per_bin[21]["rej_reason_histogram"] == {"ratio_db_low": 2}
    assert per_bin[21]["ahet_ratio_db_median"] == -2.0


def test_verdict_no_bin_works_is_distinguished_from_a_better_bin():
    """The two answers imply different work, so they must never collapse together."""
    dead = _sweep([_cell(20, 0, False), _cell(21, 0, False)], [20, 21], locked=21)
    assert dbs.capture_verdict(dead, dbs.summarise(dead))["verdict"] == (
        "no_verifiable_hr_at_any_in_gate_bin"
    )

    better = _sweep([_cell(20, 0, True, 80.0), _cell(21, 0, False)], [20, 21], locked=21)
    v = dbs.capture_verdict(better, dbs.summarise(better))
    assert v["verdict"] == "another_in_gate_bin_yields_more"
    assert v["best_bin"] == 20 and v["locked_bin_hr_yield"] == 0.0


def test_verdict_when_the_lock_is_already_the_maximum():
    sweep = _sweep([_cell(20, 0, False), _cell(21, 0, True, 80.0)], [20, 21], locked=21)
    v = dbs.capture_verdict(sweep, dbs.summarise(sweep))
    assert v["verdict"] == "locked_bin_is_at_the_in_gate_maximum"
    assert v["pooled_rejection_histogram"] == {"ratio_db_low": 1}


def test_continuity_mad_needs_two_values():
    assert dbs._mad_successive_diff([80.0]) is None
    assert dbs._mad_successive_diff([80.0, 84.0, 88.0]) == 4.0


def test_csv_is_written_with_lf_endings(tmp_path):
    """HANDOFF section 9: line endings are pinned to LF and it is load-bearing."""
    path = tmp_path / "out.csv"
    dbs.write_csv(path, [{"a": 1, "b": True, "c": None, "d": {"x": 1}}], ["a", "b", "c", "d"])
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert raw.decode().splitlines()[1] == '1,1,,"{""x"":1}"'
