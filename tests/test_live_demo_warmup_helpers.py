"""Goal 1 tests for live-demo warmup bin helper contracts."""
from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pytest

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
    assert "energy_eligibility_min_settled_db:" in block
    assert "settle_skip_s:" in block
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
    # Bin 3 at -3.5 dB rel max: hr_valid bonus must survive the settled-energy veto.
    cube = _tone_cube({1: 3.0, 2: 6.0, 3: 4.0})
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
    # Both bins within the veto gate (-1.9 dB) so the hr bonus applies to both
    # and only the energy-rank score term separates them.
    cube = _tone_cube({1: 4.0, 3: 5.0})
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


def test_hr_bonus_vetoed_for_low_settled_energy_bin():
    """A lone hr_valid pass at a bin far below the strongest candidate's energy
    must not outvote the dominant reflector (20260714 sweep / massimo2 mislocks)."""
    cfg = _base_cfg()
    cfg["profile"]["range_resolution_m"] = 1.0
    cfg["protocol"]["subject_distance_m"] = [1.0, 3.0]
    # Bins must be well separated: the Hann range window leaks ~-6 dB into
    # ADJACENT bins, which would lift a weak neighbour past the veto gate.
    cube = _tone_cube({4: 10.0, 12: 0.1})   # bin 12 at ~-40 dB rel bin 4

    dsp_by_bin = {
        4: _dsp(br_confidence="high", br_valid=True, br_bpm=13.0),
        12: _dsp(hr_valid=True, hr_raw=66.0, br_confidence="high", br_valid=True),
    }

    selected_bin, winning_dsp, evidence = _run_warmup_selection(
        cube, [4, 12], cfg, fs=20.0,
        dsp_fn=lambda _c, b, _f, _g: dsp_by_bin[b],
    )

    assert selected_bin == 4
    assert winning_dsp is dsp_by_bin[4]
    assert evidence["selected_confidence"] == "medium"  # hr bonus vetoed -> not "high"
    by_bin = {c["bin"]: c for c in evidence["candidates"]}
    assert by_bin[12]["hr_bonus_vetoed"] is True
    assert by_bin[12]["settled_energy_db"] < -12.0
    assert by_bin[4]["hr_bonus_vetoed"] is False
    assert by_bin[4]["settled_energy_db"] == 0.0


def test_hr_bonus_veto_uses_settled_not_full_window_energy():
    """A strong transient confined to the settle-skip period must not lift a skirt
    bin past the veto gate, even when it makes that bin energy-rank 1 overall."""
    n_adc, n_frames = 32, 200          # fs=20 -> settle_skip=100 frames
    samples = np.arange(n_adc, dtype=np.float32)
    tone4 = np.exp(1j * 2 * np.pi * 4 * samples / n_adc).astype(np.complex64)
    tone12 = np.exp(1j * 2 * np.pi * 12 * samples / n_adc).astype(np.complex64)
    cube = np.zeros((n_frames, 2, 2, n_adc), dtype=np.complex64)
    cube += 5.0 * tone4                # bin 4: steady chest-like return
    cube[:100] += 50.0 * tone12        # bin 12: settling transient only
    cube[100:] += 0.01 * tone12

    cfg = _base_cfg()
    cfg["profile"]["range_resolution_m"] = 1.0
    cfg["protocol"]["subject_distance_m"] = [1.0, 3.0]

    dsp_by_bin = {
        4: _dsp(br_confidence="high", br_valid=True, br_bpm=12.0),
        12: _dsp(hr_valid=True, hr_raw=110.0),   # bogus pass rides the transient
    }

    selected_bin, _, evidence = _run_warmup_selection(
        cube, [4, 12], cfg, fs=20.0,
        dsp_fn=lambda _c, b, _f, _g: dsp_by_bin[b],
    )

    assert selected_bin == 4
    by_bin = {c["bin"]: c for c in evidence["candidates"]}
    assert by_bin[12]["energy_rank"] == 1             # transient dominates full window
    assert by_bin[12]["settled_energy_db"] < -12.0    # but not the settled window
    assert by_bin[12]["hr_bonus_vetoed"] is True


def test_hr_bonus_veto_threshold_comes_from_config():
    cfg = _base_cfg()
    cfg["profile"]["range_resolution_m"] = 1.0
    cfg["protocol"]["subject_distance_m"] = [1.0, 3.0]
    cfg["bin_selection"]["energy_eligibility_min_settled_db"] = -100.0
    cube = _tone_cube({4: 10.0, 12: 0.1})

    dsp_by_bin = {
        4: _dsp(br_confidence="high", br_valid=True, br_bpm=13.0),
        12: _dsp(hr_valid=True, hr_raw=66.0, br_confidence="high", br_valid=True),
    }

    selected_bin, _, evidence = _run_warmup_selection(
        cube, [4, 12], cfg, fs=20.0,
        dsp_fn=lambda _c, b, _f, _g: dsp_by_bin[b],
    )

    assert selected_bin == 12   # ~-40 dB bin clears a -100 dB gate
    assert evidence["selected_confidence"] == "high"
    by_bin = {c["bin"]: c for c in evidence["candidates"]}
    assert by_bin[12]["hr_bonus_vetoed"] is False


def test_ineligible_bin_cannot_outvote_eligible_bin_on_breathing_evidence():
    """Cross-model review F1 (REJECT): an energy-ineligible bin must never win via
    BR score alone, even without any hr_valid pass involved. Reproduces the
    reviewer's ~295-vs-~145 scenario: bin 12 (ineligible, br=high) previously beat
    bin 4 (eligible, br=medium) purely on breathing-evidence score. Fails on the
    pre-partition code (yesterday's hr_bonus-only veto), passes under the
    eligible/ineligible partition."""
    cfg = _base_cfg()
    cfg["profile"]["range_resolution_m"] = 1.0
    cfg["protocol"]["subject_distance_m"] = [1.0, 3.0]
    cube = _tone_cube({4: 10.0, 12: 0.1})   # bin 12 at ~-40 dB rel bin 4

    dsp_by_bin = {
        4: _dsp(br_confidence="medium", br_valid=True, br_bpm=14.0),   # eligible
        12: _dsp(br_confidence="high", br_valid=True, br_bpm=13.0),    # ineligible
    }

    selected_bin, winning_dsp, evidence = _run_warmup_selection(
        cube, [4, 12], cfg, fs=20.0,
        dsp_fn=lambda _c, b, _f, _g: dsp_by_bin[b],
    )

    assert selected_bin == 4
    assert winning_dsp is dsp_by_bin[4]
    by_bin = {c["bin"]: c for c in evidence["candidates"]}
    assert by_bin[12]["energy_eligible"] is False
    assert by_bin[4]["energy_eligible"] is True


def test_ineligible_bin_wins_only_when_no_eligible_dsp_succeeds(capsys):
    """Mixed fallback: the eligible bin's DSP call raises, only an ineligible
    bin's DSP succeeds. The ineligible bin may win here (nothing better exists),
    but it must be clearly marked as a fallback, not presented as trustworthy.

    Also pins the wording fix from cross-model review round 2 (new finding):
    the ineligible-candidate stderr warning fires BEFORE the eligibility-partition
    fallback decision, so in exactly this scenario it must not claim the bin was
    "excluded from selection" when that same bin goes on to win as the fallback."""
    cfg = _base_cfg()
    cfg["profile"]["range_resolution_m"] = 1.0
    cfg["protocol"]["subject_distance_m"] = [1.0, 3.0]
    cube = _tone_cube({4: 10.0, 12: 0.1})

    def fake_dsp(_cube, b, _fs, _cfg):
        if b == 4:
            raise RuntimeError("DSP blew up on the eligible bin")
        return _dsp(hr_valid=True, hr_raw=66.0, br_confidence="high", br_valid=True)

    selected_bin, winning_dsp, evidence = _run_warmup_selection(
        cube, [4, 12], cfg, fs=20.0, dsp_fn=fake_dsp
    )

    assert selected_bin == 12
    assert winning_dsp is not None
    assert evidence["selected_confidence"] == "low"
    assert "_no_energy_eligible_dsp_success" in evidence["selection_reason"]
    assert evidence["fallback_used"] is True
    assert evidence["eligible_dsp_success_count"] == 0

    captured = capsys.readouterr()
    assert "excluded from the primary energy-eligible pool" in captured.err
    assert "excluded from selection" not in captured.err


def test_settle_skip_exceeding_window_falls_back_to_full_window(capsys):
    n_adc, n_frames = 32, 80   # fs=20 -> default settle_skip_s=5.0 -> 100 frames, > 80
    samples = np.arange(n_adc, dtype=np.float32)
    tone = np.exp(1j * 2 * np.pi * 4 * samples / n_adc).astype(np.complex64)
    cube = np.zeros((n_frames, 2, 2, n_adc), dtype=np.complex64)
    cube += 5.0 * tone

    cfg = _base_cfg()
    cfg["profile"]["range_resolution_m"] = 1.0
    cfg["protocol"]["subject_distance_m"] = [1.0, 3.0]

    _, _, evidence = _run_warmup_selection(
        cube, [4], cfg, fs=20.0, dsp_fn=lambda *_a: _dsp(),
    )

    assert evidence["settle_skip_frames_applied"] == 0
    assert evidence["settle_skip_fallback_full_window"] is True
    assert "FULL window" in capsys.readouterr().err


def test_negative_settle_skip_s_raises():
    cfg = _base_cfg()
    cfg["bin_selection"]["settle_skip_s"] = -1.0
    cube = _tone_cube({4: 10.0})

    with pytest.raises(ValueError, match="settle_skip_s"):
        _run_warmup_selection(cube, [4], cfg, fs=20.0, dsp_fn=lambda *_a: _dsp())


def test_nan_settle_skip_s_raises():
    cfg = _base_cfg()
    cfg["bin_selection"]["settle_skip_s"] = float("nan")
    cube = _tone_cube({4: 10.0})

    with pytest.raises(ValueError, match="settle_skip_s"):
        _run_warmup_selection(cube, [4], cfg, fs=20.0, dsp_fn=lambda *_a: _dsp())


def test_positive_energy_eligibility_threshold_raises():
    cfg = _base_cfg()
    cfg["bin_selection"]["energy_eligibility_min_settled_db"] = 5.0
    cube = _tone_cube({4: 10.0})

    with pytest.raises(ValueError, match="energy_eligibility_min_settled_db"):
        _run_warmup_selection(cube, [4], cfg, fs=20.0, dsp_fn=lambda *_a: _dsp())


def test_infinite_energy_eligibility_threshold_raises():
    cfg = _base_cfg()
    cfg["bin_selection"]["energy_eligibility_min_settled_db"] = float("inf")
    cube = _tone_cube({4: 10.0})

    with pytest.raises(ValueError, match="energy_eligibility_min_settled_db"):
        _run_warmup_selection(cube, [4], cfg, fs=20.0, dsp_fn=lambda *_a: _dsp())


def test_energy_eligibility_threshold_boundary(monkeypatch):
    """-12.0 dB exactly is eligible (>=); a hair below is not. Uses monkeypatched
    energies (not real FFT output) to pin the boundary exactly, avoiding float
    fuzz from division/log10 on synthetic tone amplitudes."""
    fixed_energies = {
        100: 1.0,
        101: 10 ** (-12.0 / 10.0),   # exactly at threshold -> eligible
        102: 10 ** (-12.5 / 10.0),   # just below threshold -> ineligible
    }

    def fake_energy_by_bin(_cube, candidate_bins):
        return {b: fixed_energies[b] for b in candidate_bins}

    monkeypatch.setattr("scripts.live_demo._range_energy_by_bin", fake_energy_by_bin)

    cfg = _base_cfg()
    cfg["profile"]["range_resolution_m"] = 1.0
    cfg["protocol"]["subject_distance_m"] = [1.0, 3.0]
    cube = np.zeros((10, 1, 1, 4), dtype=np.complex64)  # content unused by the fake

    _, _, evidence = _run_warmup_selection(
        cube, [100, 101, 102], cfg, fs=20.0, dsp_fn=lambda *_a: _dsp(),
    )

    by_bin = {c["bin"]: c for c in evidence["candidates"]}
    assert by_bin[101]["energy_eligible"] is True
    assert by_bin[102]["energy_eligible"] is False
    assert by_bin[101]["settled_energy_db"] == pytest.approx(-12.0, abs=0.05)
    assert by_bin[102]["settled_energy_db"] == pytest.approx(-12.5, abs=0.05)


def test_empty_candidate_bins_raises():
    cube = _tone_cube({4: 10.0})
    cfg = _base_cfg()

    with pytest.raises(ValueError, match="candidate_bins"):
        _run_warmup_selection(cube, [], cfg, fs=20.0, dsp_fn=lambda *_a: _dsp())


def test_dominant_non_chest_reflector_is_surfaced_not_silently_dropped(capsys):
    """Accepted F2 limitation: a strong non-chest reflector can outrank the true
    chest bin. The requirement is that this is never silent -- the chest's
    hr_valid pass stays visible in the evidence and a stderr warning fires."""
    cfg = _base_cfg()
    cfg["profile"]["range_resolution_m"] = 1.0
    cfg["protocol"]["subject_distance_m"] = [1.0, 3.0]
    # bin 4: dominant static reflector, no vitals at all (e.g. a wall/chair).
    # bin 12: the true chest, much weaker in this contrived scene, but with a
    # genuine hr_valid pass.
    cube = _tone_cube({4: 10.0, 12: 0.1})

    dsp_by_bin = {
        4: _dsp(),  # no HR, no BR evidence -- purely a static reflector
        12: _dsp(hr_valid=True, hr_raw=66.0, br_confidence="high", br_valid=True),
    }

    selected_bin, winning_dsp, evidence = _run_warmup_selection(
        cube, [4, 12], cfg, fs=20.0,
        dsp_fn=lambda _c, b, _f, _g: dsp_by_bin[b],
    )

    assert selected_bin == 4
    assert evidence["selected_confidence"] == "low"
    by_bin = {c["bin"]: c for c in evidence["candidates"]}
    assert by_bin[12]["hr_valid"] is True
    assert by_bin[12]["energy_eligible"] is False

    captured = capsys.readouterr()
    assert "energy-ineligible" in captured.err
    assert "bin 12" in captured.err


def test_energy_eligible_recorded_for_dsp_failed_candidates():
    cfg = _base_cfg()
    cfg["profile"]["range_resolution_m"] = 1.0
    cfg["protocol"]["subject_distance_m"] = [1.0, 3.0]
    cube = _tone_cube({4: 10.0, 12: 0.1})

    def fake_dsp(_cube, b, _fs, _cfg):
        if b == 12:
            raise RuntimeError("boom")
        return _dsp(br_confidence="high", br_valid=True)

    _, _, evidence = _run_warmup_selection(
        cube, [4, 12], cfg, fs=20.0, dsp_fn=fake_dsp
    )

    by_bin = {c["bin"]: c for c in evidence["candidates"]}
    assert by_bin[12]["failed"] is True
    assert "energy_eligible" in by_bin[12]
    assert by_bin[12]["energy_eligible"] is False
    assert by_bin[4]["energy_eligible"] is True


def test_fallback_used_true_for_all_zero_energy_and_all_dsp_failed():
    """Cross-model review #2 finding: an all-zero cube (every bin energy-ineligible)
    combined with every DSP call raising must still set fallback_used=True. Before
    this fix the field stayed False here, contradicting its own invariant that an
    ineligible/untrustworthy winner is always flagged as a fallback -- even though
    selection_reason and confidence were already correct in this branch."""
    cfg = _base_cfg()
    cfg["profile"]["range_resolution_m"] = 1.0
    cfg["protocol"]["subject_distance_m"] = [1.0, 3.0]
    cube = np.zeros((10, 2, 2, 8), dtype=np.complex64)

    def fake_dsp(_cube, _b, _fs, _cfg):
        raise RuntimeError("boom")

    _, winning_dsp, evidence = _run_warmup_selection(
        cube, [1, 2, 3], cfg, fs=20.0, dsp_fn=fake_dsp
    )

    assert winning_dsp is None
    assert evidence["selection_reason"] == "all_dsp_failed_energy_fallback"
    assert evidence["selected_confidence"] == "low"
    assert evidence["all_candidates_energy_ineligible"] is True
    assert evidence["eligible_dsp_success_count"] == 0
    assert evidence["fallback_used"] is True


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
