"""Goal 1 tests for live-demo warmup bin helper contracts."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np

try:
    import yaml as _yaml  # noqa: F401
except ModuleNotFoundError:
    sys.modules["yaml"] = types.ModuleType("yaml")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.live_demo import (  # noqa: E402
    _derive_candidate_bins,
    _range_energy_by_bin,
    _resolve_locked_bin,
    _run_warmup_selection,
)


def _base_cfg() -> dict:
    return {
        "protocol": {"subject_distance_m": [1.0, 1.4]},
        "profile": {"range_resolution_m": 0.0436, "num_adc_samples": 256},
        "bin_selection": {"enabled": True, "candidate_bins": None},
    }


def test_live_demo_config_has_minimal_bin_selection_section():
    cfg_path = Path(__file__).resolve().parents[1] / "scripts" / "live_demo_config.yaml"
    text = cfg_path.read_text(encoding="utf-8")
    start = text.index("bin_selection:")
    rest = text[start:]
    end_candidates = [
        idx for idx in (rest.find("\n" + name) for name in ("display:", "capture:", "profile:"))
        if idx != -1
    ]
    block = rest[: min(end_candidates)]

    assert "bin_selection:" in block
    assert "enabled: true" in block
    assert "candidate_bins: null" in block
    block_lines = [line.strip() for line in block.splitlines()]
    assert not any(line.startswith("distance_m:") for line in block_lines)
    assert not any(line.startswith("top_n_reported:") for line in block_lines)


def test_derive_candidate_bins_from_protocol_distance_and_resolution():
    assert _derive_candidate_bins(_base_cfg()) == list(range(23, 33))


def test_derive_candidate_bins_uses_explicit_override():
    cfg = _base_cfg()
    cfg["bin_selection"]["candidate_bins"] = ["24", 27, 31]

    assert _derive_candidate_bins(cfg) == [24, 27, 31]


def test_derive_candidate_bins_clamps_to_adc_bounds():
    cfg = {
        "protocol": {"subject_distance_m": [-1.0, 999.0]},
        "profile": {"range_resolution_m": 0.5, "num_adc_samples": 4},
        "bin_selection": {"candidate_bins": None},
    }

    assert _derive_candidate_bins(cfg) == [0, 1, 2, 3]


def test_range_energy_by_bin_uses_range_fft_energy_ranking():
    n_adc = 32
    samples = np.arange(n_adc, dtype=np.float32)
    cube = np.zeros((3, 2, 2, n_adc), dtype=np.complex64)
    cube += 0.5 * np.exp(1j * 2 * np.pi * 5 * samples / n_adc).astype(np.complex64)
    cube += 3.0 * np.exp(1j * 2 * np.pi * 9 * samples / n_adc).astype(np.complex64)

    energy = _range_energy_by_bin(cube, [5, 9, 12])

    assert set(energy) == {5, 9, 12}
    assert energy[9] > energy[5] > energy[12]


def test_resolve_locked_bin_prefers_manual_then_manifest():
    assert _resolve_locked_bin(25, 27, True) == (25, "manual", False)
    assert _resolve_locked_bin(None, 27, True) == (27, "manifest", False)


def test_resolve_locked_bin_returns_warmup_pending_when_enabled():
    assert _resolve_locked_bin(None, None, True) == (None, "warmup_auto", True)


def test_resolve_locked_bin_is_pure_error_sentinel_when_disabled():
    assert _resolve_locked_bin(None, None, False) == (None, None, False)


def _tone_cube(bin_amplitudes: dict[int, float], n_adc: int = 32) -> np.ndarray:
    samples = np.arange(n_adc, dtype=np.float32)
    cube = np.zeros((4, 2, 2, n_adc), dtype=np.complex64)
    for bin_idx, amplitude in bin_amplitudes.items():
        tone = amplitude * np.exp(1j * 2 * np.pi * bin_idx * samples / n_adc)
        cube += tone.astype(np.complex64)
    return cube


def _dsp(
    *,
    hr_valid: bool = False,
    br_confidence: str = "low",
    br_valid: bool = False,
    hr_raw: float = np.nan,
    br_bpm: float = np.nan,
    fallback_hr_bpm: float = 72.0,
    f_r_hz: float | None = None,
) -> dict:
    return {
        "hr_valid": hr_valid,
        "hr_raw": hr_raw,
        "fallback_hr_bpm": fallback_hr_bpm,
        "br_bpm": br_bpm,
        "br_confidence": br_confidence,
        "br_valid": br_valid,
        "f_r_hz": f_r_hz,
        "spectrum_stage": 2 if hr_valid else 1,
        "rej_reason": "passed" if hr_valid else "ratio_db_low",
        "n_eca_skipped": 2,
        "hr_result": {"accepted_candidate_rank": 0 if hr_valid else -1},
    }


def test_run_warmup_selection_scans_all_bins_and_selects_hr_valid_winner():
    cfg = _base_cfg()
    cfg["profile"]["range_resolution_m"] = 1.0
    cfg["protocol"]["subject_distance_m"] = [1.0, 3.0]
    cube = _tone_cube({1: 3.0, 2: 6.0, 3: 1.0})
    calls: list[int] = []

    dsp_by_bin = {
        1: _dsp(br_confidence="high", br_valid=True, br_bpm=12.0),
        2: _dsp(br_confidence="medium", br_valid=True, br_bpm=16.0),
        3: _dsp(
            hr_valid=True,
            hr_raw=81.0,
            br_confidence="low",
            br_valid=False,
            br_bpm=np.nan,
        ),
    }

    def fake_dsp(_cube, locked_bin, _fs, _cfg):
        calls.append(locked_bin)
        return dsp_by_bin[locked_bin]

    selected_bin, winning_dsp, evidence = _run_warmup_selection(
        cube, [1, 2, 3], cfg, fs=20.0, dsp_fn=fake_dsp
    )

    assert calls == [1, 2, 3]
    assert selected_bin == 3
    assert winning_dsp is dsp_by_bin[3]
    assert evidence["selected_bin"] == 3
    assert evidence["selected_confidence"] == "low"
    winner = next(c for c in evidence["candidates"] if c["bin"] == 3)
    assert winner["hr_valid"] is True
    assert winner["hr_raw"] == 81.0


def test_run_warmup_selection_tie_breaks_by_energy_rank():
    cfg = _base_cfg()
    cfg["profile"]["range_resolution_m"] = 1.0
    cfg["protocol"]["subject_distance_m"] = [1.0, 3.0]
    cube = _tone_cube({1: 1.0, 3: 5.0})
    common = _dsp(
        hr_valid=True,
        hr_raw=70.0,
        br_confidence="high",
        br_valid=True,
        br_bpm=15.0,
        f_r_hz=0.25,
    )

    def fake_dsp(_cube, locked_bin, _fs, _cfg):
        return dict(common, hr_raw=70.0 + locked_bin)

    selected_bin, winning_dsp, evidence = _run_warmup_selection(
        cube, [1, 3], cfg, fs=20.0, dsp_fn=fake_dsp
    )

    assert selected_bin == 3
    assert winning_dsp is not None
    assert winning_dsp["hr_raw"] == 73.0
    ranks = {c["bin"]: c["energy_rank"] for c in evidence["candidates"]}
    assert ranks[3] == 1
    assert ranks[1] == 2
    assert evidence["selected_confidence"] == "high"


def test_run_warmup_selection_continues_after_partial_dsp_failures():
    cfg = _base_cfg()
    cfg["profile"]["range_resolution_m"] = 1.0
    cfg["protocol"]["subject_distance_m"] = [1.0, 3.0]
    cube = _tone_cube({1: 8.0, 2: 2.0, 3: 4.0})

    def fake_dsp(_cube, locked_bin, _fs, _cfg):
        if locked_bin != 2:
            raise RuntimeError(f"bad bin {locked_bin}")
        return _dsp(br_confidence="low", br_valid=False, fallback_hr_bpm=68.0)

    selected_bin, winning_dsp, evidence = _run_warmup_selection(
        cube, [1, 2, 3], cfg, fs=20.0, dsp_fn=fake_dsp
    )

    assert selected_bin == 2
    assert winning_dsp is not None
    assert evidence["selected_confidence"] == "low"
    failed = {c["bin"]: c for c in evidence["candidates"] if c["failed"]}
    assert set(failed) == {1, 3}
    assert "bad bin 1" in failed[1]["error"]
    survivor = next(c for c in evidence["candidates"] if c["bin"] == 2)
    assert survivor["score"] < 0
    assert survivor["fallback_hr_bpm"] == 68.0


def test_run_warmup_selection_all_fail_falls_back_to_highest_energy_bin():
    cfg = _base_cfg()
    cfg["profile"]["range_resolution_m"] = 1.0
    cfg["protocol"]["subject_distance_m"] = [1.0, 3.0]
    cube = _tone_cube({1: 1.0, 2: 9.0, 3: 4.0})

    def fake_dsp(_cube, locked_bin, _fs, _cfg):
        raise ValueError(f"no dsp for {locked_bin}")

    selected_bin, winning_dsp, evidence = _run_warmup_selection(
        cube, [1, 2, 3], cfg, fs=20.0, dsp_fn=fake_dsp
    )

    assert selected_bin == 2
    assert winning_dsp is None
    assert evidence["selected_confidence"] == "low"
    assert evidence["selection_reason"] == "all_dsp_failed_energy_fallback"
    assert all(c["failed"] for c in evidence["candidates"])
    assert {c["bin"]: c["energy_rank"] for c in evidence["candidates"]}[2] == 1
