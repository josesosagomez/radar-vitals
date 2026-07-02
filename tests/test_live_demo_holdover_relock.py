"""Tests for live-demo display holdover + nearby-bin relock helpers.

Covers Goals 1-2 of notes/relocking_bin_plan.md:
- _DisplayHoldoverState real/held/blank behavior, boundary, sparse-history,
  source-age limit, HR/BR independence, resets.
- _RelockController arm/disarm state machine (holdover expiry supplied
  externally, never re-derived).
- _derive_relock_candidate_bins clipping rules.
- _relock_allowed pinned-source exemption.
- _run_bin_selection_scan context labels vs the warmup wrapper.
"""
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
    _DisplayHoldoverState,
    _RelockController,
    _derive_relock_candidate_bins,
    _relock_allowed,
    _run_bin_selection_scan,
    _run_warmup_selection,
)


# ── _DisplayHoldoverState ─────────────────────────────────────────────────────

def _holdover(**kw) -> _DisplayHoldoverState:
    defaults = dict(holdover_n=3, holdover_hops=1, source_max_hops=5, enabled=True)
    defaults.update(kw)
    return _DisplayHoldoverState(**defaults)


def test_holdover_real_held_blank_cycle():
    h = _holdover()
    assert h.update("hr", 72.0, True, 0) == (72.0, "real")
    assert h.update("hr", 74.0, True, 1) == (74.0, "real")
    # First invalid hop: held average of the two valid readings.
    value, state = h.update("hr", np.nan, False, 2)
    assert state == "held"
    assert value == 73.0
    # Second consecutive invalid hop: holdover expired -> blank.
    value, state = h.update("hr", np.nan, False, 3)
    assert state == "blank"
    assert np.isnan(value)
    # Recovery: a new valid reading displays real again.
    assert h.update("hr", 76.0, True, 4) == (76.0, "real")


def test_holdover_boundary_first_invalid_held_second_blank():
    # Explicit boundary check with default holdover_hops=1 (hop_s=3.0,
    # holdover_s=3.0): exactly one missed hop is held, the next is blank.
    h = _holdover(holdover_hops=1)
    h.update("br", 15.0, True, 10)
    assert h.update("br", np.nan, False, 11)[1] == "held"
    assert h.update("br", np.nan, False, 12)[1] == "blank"


def test_holdover_hr_br_independent():
    h = _holdover()
    h.update("hr", 70.0, True, 0)
    h.update("br", 14.0, True, 0)
    hr_value, hr_state = h.update("hr", np.nan, False, 1)
    br_value, br_state = h.update("br", 16.0, True, 1)
    assert hr_state == "held" and hr_value == 70.0
    assert br_state == "real" and br_value == 16.0


def test_holdover_sparse_history_averages_what_exists():
    h = _holdover()
    h.update("hr", 80.0, True, 0)
    value, state = h.update("hr", np.nan, False, 1)
    assert state == "held"
    assert value == 80.0


def test_holdover_never_valid_is_blank():
    h = _holdover()
    value, state = h.update("hr", np.nan, False, 0)
    assert state == "blank"
    assert np.isnan(value)


def test_holdover_source_age_limit_excludes_stale_readings():
    h = _holdover(holdover_hops=1, source_max_hops=5)
    h.update("hr", 60.0, True, 0)   # stale by hop 9 (age 9 > 5)
    h.update("hr", 62.0, True, 1)   # stale by hop 9 (age 8 > 5)
    h.update("hr", 90.0, True, 8)   # age 1 at hop 9 -> only eligible source
    value, state = h.update("hr", np.nan, False, 9)
    assert state == "held"
    assert value == 90.0


def test_holdover_disabled_goes_straight_to_blank():
    h = _holdover(enabled=False)
    h.update("hr", 72.0, True, 0)
    value, state = h.update("hr", np.nan, False, 1)
    assert state == "blank"
    assert np.isnan(value)


def test_holdover_valid_flag_with_nonfinite_value_is_not_real():
    # A "valid" flag with a NaN value must not enter the buffer as a reading.
    h = _holdover()
    value, state = h.update("hr", np.nan, True, 0)
    assert state == "blank"
    assert np.isnan(value)


def test_holdover_reset_metric_and_reset_all():
    h = _holdover()
    h.update("hr", 72.0, True, 0)
    h.update("br", 15.0, True, 0)
    h.reset_metric("hr")
    assert h.update("hr", np.nan, False, 1)[1] == "blank"
    assert h.update("br", np.nan, False, 1)[1] == "held"
    h.reset_all()
    assert h.update("br", np.nan, False, 2)[1] == "blank"


# ── _RelockController ─────────────────────────────────────────────────────────

def test_relock_arms_only_after_consecutive_valid_hops():
    c = _RelockController(arm_hops=2)
    # One valid hop then blank-expired invalid: not armed yet -> no scan.
    assert c.update(True, False, False, False, 0) == (False, [])
    assert c.update(False, False, True, False, 1) == (False, [])
    # Two consecutive valid hops arm HR; blank-expired invalid triggers.
    c.update(True, False, False, False, 2)
    c.update(True, False, False, False, 3)
    should, triggers = c.update(False, False, True, False, 4)
    assert should is True
    assert triggers == ["hr"]


def test_relock_isolated_blip_resets_arm_streak():
    c = _RelockController(arm_hops=2)
    # valid, invalid(held), valid: streak reset by the blip, so still unarmed.
    c.update(True, False, False, False, 0)
    c.update(False, False, False, False, 1)   # held, not expired
    c.update(True, False, False, False, 2)
    should, _ = c.update(False, False, True, False, 3)
    assert should is False


def test_relock_armed_flag_survives_held_hop_then_triggers_on_expiry():
    c = _RelockController(arm_hops=2)
    c.update(True, False, False, False, 0)
    c.update(True, False, False, False, 1)     # armed
    # Held-but-invalid hop: streak resets, armed stays set, no trigger yet.
    should, _ = c.update(False, False, False, False, 2)
    assert should is False
    # Blank/expired hop: armed metric triggers exactly one scan.
    should, triggers = c.update(False, False, True, False, 3)
    assert should is True and triggers == ["hr"]


def test_relock_no_repeated_scans_during_dead_stretch():
    c = _RelockController(arm_hops=1)
    c.update(True, False, False, False, 0)     # armed
    should, _ = c.update(False, False, True, False, 1)
    assert should is True
    c.disarm_all()                             # caller disarms after the scan attempt
    # Long dead stretch: never re-armed, never re-triggers.
    for hop in range(2, 12):
        assert c.update(False, False, True, False, hop) == (False, [])


def test_relock_rearm_after_new_stability_streak():
    c = _RelockController(arm_hops=2)
    c.update(True, False, False, False, 0)
    c.update(True, False, False, False, 1)
    assert c.update(False, False, True, False, 2)[0] is True
    c.disarm_all()
    # Fresh stability streak re-arms and allows a later scan.
    c.update(True, False, False, False, 3)
    c.update(True, False, False, False, 4)
    assert c.update(False, False, True, False, 5)[0] is True


def test_relock_br_triggers_independently_and_both_reported():
    c = _RelockController(arm_hops=1)
    c.update(True, True, False, False, 0)      # both armed
    should, triggers = c.update(False, False, True, True, 1)
    assert should is True
    assert triggers == ["hr", "br"]


def test_relock_startup_blank_never_triggers():
    c = _RelockController(arm_hops=1)
    for hop in range(5):
        assert c.update(False, False, True, True, hop) == (False, [])


def test_relock_expiry_comes_only_from_caller_flags():
    # Armed metric invalid forever, but caller never reports blank/expired:
    # the controller must not trigger from its own internal counting.
    c = _RelockController(arm_hops=1)
    c.update(True, False, False, False, 0)     # armed
    for hop in range(1, 10):
        assert c.update(False, False, False, False, hop) == (False, [])


def test_relock_reset_clears_armed_state():
    c = _RelockController(arm_hops=1)
    c.update(True, False, False, False, 0)     # armed
    c.reset()
    assert c.update(False, False, True, False, 1) == (False, [])


# ── _derive_relock_candidate_bins ─────────────────────────────────────────────

def _relock_cfg(candidate_bins=None) -> dict:
    return {
        "protocol": {"subject_distance_m": [1.0, 1.4]},
        "profile": {"range_resolution_m": 0.0436, "num_adc_samples": 256},
        "bin_selection": {"enabled": True, "candidate_bins": candidate_bins},
    }


def test_relock_candidates_center_of_protocol():
    assert _derive_relock_candidate_bins(25, _relock_cfg(), 2) == [23, 24, 25, 26, 27]


def test_relock_candidates_clip_to_protocol_edges():
    # Protocol range 1.0-1.4 m at 0.0436 m/bin -> bins 23-32.
    assert _derive_relock_candidate_bins(23, _relock_cfg(), 2) == [23, 24, 25]
    assert _derive_relock_candidate_bins(32, _relock_cfg(), 2) == [30, 31, 32]


def test_relock_candidates_explicit_config_skips_protocol_clip():
    cfg = _relock_cfg(candidate_bins=[10, 40])
    cfg["profile"]["num_adc_samples"] = 4
    # Only ADC bounds apply when candidate_bins is explicitly configured.
    assert _derive_relock_candidate_bins(1, cfg, 2) == [0, 1, 2, 3]


def test_relock_candidates_always_include_locked_bin():
    # Manual bin outside the protocol range: neighbours are protocol-clipped
    # away but the current locked bin itself must remain scannable.
    assert _derive_relock_candidate_bins(40, _relock_cfg(), 2) == [40]


# ── _relock_allowed ───────────────────────────────────────────────────────────

def _allowed_cfg(relock_enabled=True, pinned_enabled=False) -> dict:
    return {
        "bin_selection": {
            "relock_enabled": relock_enabled,
            "relock_pinned_sources_enabled": pinned_enabled,
        }
    }


def test_relock_allowed_for_warmup_auto():
    assert _relock_allowed("warmup_auto", _allowed_cfg()) is True


def test_relock_disabled_for_manual_and_manifest_by_default():
    assert _relock_allowed("manual", _allowed_cfg()) is False
    assert _relock_allowed("manifest", _allowed_cfg()) is False


def test_relock_pinned_override_permits_relock():
    assert _relock_allowed("manual", _allowed_cfg(pinned_enabled=True)) is True
    assert _relock_allowed("manifest", _allowed_cfg(pinned_enabled=True)) is True


def test_relock_master_switch_off_blocks_everything():
    assert _relock_allowed("warmup_auto", _allowed_cfg(relock_enabled=False)) is False


# ── Selector context labels ───────────────────────────────────────────────────

def _scan_cfg() -> dict:
    return {
        "protocol": {"subject_distance_m": [1.0, 3.0]},
        "profile": {"range_resolution_m": 1.0, "num_adc_samples": 256},
        "bin_selection": {"enabled": True, "candidate_bins": None},
    }


def _tone_cube(bin_amplitudes: dict[int, float], n_adc: int = 32) -> np.ndarray:
    samples = np.arange(n_adc, dtype=np.float32)
    cube = np.zeros((4, 2, 2, n_adc), dtype=np.complex64)
    for bin_idx, amplitude in bin_amplitudes.items():
        tone = amplitude * np.exp(1j * 2 * np.pi * bin_idx * samples / n_adc)
        cube += tone.astype(np.complex64)
    return cube


def test_relock_context_messages_do_not_say_warmup(capsys):
    def fail_dsp(_cube, locked_bin, _fs, _cfg):
        raise ValueError(f"no dsp for {locked_bin}")

    _, _, evidence = _run_bin_selection_scan(
        _tone_cube({1: 1.0, 2: 9.0}), [1, 2], _scan_cfg(), fs=20.0,
        dsp_fn=fail_dsp,
        context_label="relock", artifact_hint="relock_events.json",
    )
    err = capsys.readouterr().err
    assert "relock DSP failed" in err
    assert "relock_events.json" in err
    assert "warmup" not in err
    assert "t_scan_ms" in evidence


def test_warmup_wrapper_keeps_warmup_labels_and_time_key(capsys):
    def fail_dsp(_cube, locked_bin, _fs, _cfg):
        raise ValueError(f"no dsp for {locked_bin}")

    _, _, evidence = _run_warmup_selection(
        _tone_cube({1: 1.0, 2: 9.0}), [1, 2], _scan_cfg(), fs=20.0,
        dsp_fn=fail_dsp,
    )
    err = capsys.readouterr().err
    assert "warmup DSP failed" in err
    assert "warmup_bin_selection.json" in err
    assert "t_warmup_scan_ms" in evidence
    assert "t_scan_ms" not in evidence
