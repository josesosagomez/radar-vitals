"""Opt-in configuration and preflight side-effect boundary tests."""
from __future__ import annotations

import builtins
import copy
import hashlib
import json
from pathlib import Path

import pytest

from src.live_motion.config import parse_live_motion_settings, validate_live_motion_preflight


ROOT = Path(__file__).resolve().parents[1]


def _cfg(*, enabled=True, extended=True):
    return {
        "session": {"frame_rate_hz": 20.0, "locked_bin": None},
        "profile": {"num_adc_samples": 64, "range_resolution_m": 0.05},
        "protocol": {"subject_distance_m": [0.8, 1.4]},
        "phase": {"method": "delta_before_mean", "clutter_removal": "none"},
        "bin_selection": {"candidate_bins": [16, 17]},
        "development_motion": {
            "enabled": enabled,
            "extended_breathing_enabled": extended,
            "unavailable_reason": "physical calibration pending",
            "preliminary_stages_s": [10, 20],
            "ordinary_window_s": 30,
            "ordinary_hop_s": 3,
            "monitor_window_s": 1,
            "monitor_hop_s": 0.25,
            "stillness_confirmation_s": 3,
            "extended_breathing_window_s": 60,
            "extended_breathing_band_hz": [0.05, 0.50],
            "calibration": {"record_path": "pending.json", "record_sha256": "a" * 64},
        },
    }


def _record():
    return {
        "thresholds": {
            "presence": {"empty_max_db": -20.0, "occupied_min_db": -10.0},
            "movement": {
                "presence": {"enabled": False, "entry": None, "exit": None},
                "phase_activity": {"enabled": True, "entry": 1.0, "exit": 0.2},
                "range_profile_change": {"enabled": True, "entry": 0.8, "exit": 0.1},
            },
        }
    }


def test_absent_feature_block_preserves_legacy_dispatch():
    assert validate_live_motion_preflight(
        {"session": {}}, mode="replay", replay_requested=True, prospective=True,
        cli_locked_bin=2, manifest_locked_bin=3,
    ) is None


def test_disabled_valid_block_needs_no_calibration_and_retains_exact_schedule():
    settings = validate_live_motion_preflight(
        _cfg(enabled=False, extended=False), mode="live", replay_requested=False,
        prospective=False, cli_locked_bin=None, manifest_locked_bin=None,
    )
    assert settings.enabled is False
    assert settings.preview_frames == (200, 400)
    assert settings.ordinary_window_frames == 600
    assert settings.ordinary_hop_frames == 60
    assert settings.monitor_window_frames == 20
    assert settings.monitor_hop_frames == 5
    assert settings.stillness_frames == 60
    assert settings.extended_breathing_window_frames == 1200


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda c: c["development_motion"].update(preliminary_stages_s=[10, 30]),
         "preliminary_stages_s"),
        (lambda c: c["development_motion"].update(ordinary_window_s=29), "ordinary_window_s"),
        (lambda c: c["development_motion"].update(monitor_hop_s=0.5), "monitor_hop_s"),
        (lambda c: c["development_motion"].update(stillness_confirmation_s=2),
         "stillness_confirmation_s"),
        (lambda c: c["development_motion"].update(extended_breathing_band_hz=[0.1, 0.5]),
         "extended_breathing_band_hz"),
        (lambda c: c["session"].update(frame_rate_hz=19.0), "frame_rate_hz"),
        (lambda c: c["phase"].update(method="mean_phasor"), "delta_before_mean"),
        (lambda c: c["phase"].update(clutter_removal="slow_time_mean"), "clutter_removal"),
    ],
)
def test_semantic_drift_is_rejected_even_while_feature_disabled(mutation, message):
    cfg = _cfg(enabled=False, extended=False)
    mutation(cfg)
    with pytest.raises(ValueError, match=message):
        validate_live_motion_preflight(
            cfg, mode="live", replay_requested=False, prospective=False,
            cli_locked_bin=None, manifest_locked_bin=None,
        )


def test_extended_breathing_requires_movement_guard():
    with pytest.raises(ValueError, match="requires"):
        parse_live_motion_settings(_cfg(enabled=False, extended=True))


@pytest.mark.parametrize("source", ["replay", "prospective", "cli", "manifest", "yaml"])
def test_enabled_feature_rejects_incompatible_mode_or_manual_lock_before_calibration(
    monkeypatch, source
):
    cfg = _cfg()
    kwargs = dict(
        mode="live", replay_requested=False, prospective=False,
        cli_locked_bin=None, manifest_locked_bin=None,
    )
    if source == "replay":
        kwargs.update(mode="replay", replay_requested=True)
    elif source == "prospective":
        kwargs["prospective"] = True
    elif source == "cli":
        kwargs["cli_locked_bin"] = 20
    elif source == "manifest":
        kwargs["manifest_locked_bin"] = 21
    else:
        cfg["session"]["locked_bin"] = 22

    original_import = builtins.__import__

    def reject_calibration_import(name, *args, **kwargs):
        if name == "calibration" or name.endswith(".calibration"):
            pytest.fail("incompatibility must be rejected before calibration import/I/O")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_calibration_import)
    with pytest.raises(ValueError, match="incompatible|locked_bin"):
        validate_live_motion_preflight(cfg, **kwargs)


def test_enabled_parser_requires_accepted_record_and_valid_strict_thresholds():
    cfg = _cfg()
    with pytest.raises(ValueError, match="physical-calibration-required"):
        parse_live_motion_settings(cfg)
    settings = parse_live_motion_settings(cfg, calibration_record=_record())
    assert settings.presence_thresholds.empty_max_db == -20.0
    assert settings.presence_thresholds.occupied_min_db == -10.0
    assert settings.movement_thresholds.phase_activity.entry == 1.0

    bad = copy.deepcopy(_record())
    bad["thresholds"]["movement"]["phase_activity"]["exit"] = 1.0
    with pytest.raises(ValueError, match="exit < entry"):
        parse_live_motion_settings(cfg, calibration_record=bad)


def test_protected_shared_modules_and_baseline_configs_match_checkpoint_hashes():
    manifest = json.loads(
        (ROOT / "config/live_motion_checkpoint_2026-10-07.json").read_text(encoding="utf-8")
    )
    expected_paths = {
        "src/respiration.py",
        "src/vitals.py",
        "src/window_pipeline.py",
        "src/warmup_select.py",
        "scripts/live_demo_config.yaml",
        "scripts/live_demo_calibrated_config.yaml",
    }
    assert set(manifest["frozen_sha256"]) == expected_paths
    for relative, expected in manifest["frozen_sha256"].items():
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        assert actual == expected, f"protected checkpoint file changed: {relative}"
