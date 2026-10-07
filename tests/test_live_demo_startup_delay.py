"""Development YAML countdown and isolation from study timing; no hardware."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

import scripts.live_demo as demo

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("cfg", [{}, {"session": {}}, {"session": {"window_s": 30}}])
def test_missing_delay_keeps_legacy_countdown(cfg):
    assert demo._startup_delay_s(cfg, None) == 30


@pytest.mark.parametrize("delay", [0, 1, 5, 10, 30, 60])
def test_development_delay_from_yaml(delay):
    assert demo._startup_delay_s({"session": {"startup_delay_s": delay}}, None) == delay


@pytest.mark.parametrize("delay", [-1, True, False, None, "5", 1.5, 30.0,
                                   float("nan"), float("inf")])
def test_invalid_development_delay(delay):
    with pytest.raises(ValueError, match="startup_delay_s"):
        demo._startup_delay_s({"session": {"startup_delay_s": delay}}, None)


@pytest.mark.parametrize("arm,expected", [("natural", 30), ("paced", 30), ("recovery", 0)])
@pytest.mark.parametrize("override", [0, 5, -1, True, None, "bad"])
def test_study_timing_ignores_development_option(arm, expected, override):
    assert demo._startup_delay_s(
        {"session": {"startup_delay_s": override}}, {"arm": arm}
    ) == expected


def _prepare_launch(tmp_path, monkeypatch, delay):
    cfg = yaml.safe_load((ROOT / "scripts/live_demo_config.yaml").read_text())
    cfg["session"]["startup_delay_s"] = delay
    output = tmp_path / "runs"
    cfg["paths"]["results_dir"] = str(output)
    path = tmp_path / "demo.yaml"
    path.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(sys, "argv", ["live_demo.py", "--config", str(path)])
    monkeypatch.setattr(demo, "_git_info", lambda: {})
    sleeps = []
    monkeypatch.setattr(demo.time, "sleep", sleeps.append)

    def stop_at_backend(_cfg):
        raise RuntimeError("backend reached")

    def forbid_hardware(*_args, **_kwargs):
        pytest.fail("hardware source must not be constructed")

    monkeypatch.setattr(demo, "_select_backend", stop_at_backend)
    monkeypatch.setattr(demo, "LiveFrameSource", forbid_hardware)
    return sleeps, output


@pytest.mark.parametrize("delay", [0, 2, 10, 30])
def test_main_uses_yaml_countdown_before_backend(tmp_path, monkeypatch, delay):
    sleeps, output = _prepare_launch(tmp_path, monkeypatch, delay)
    with pytest.raises(RuntimeError, match="backend reached"):
        demo.main()
    assert sleeps == [1] * delay
    assert not output.exists()


@pytest.mark.parametrize("delay", [-1, True, None, "5", 1.5])
def test_main_rejects_invalid_delay_before_side_effects(tmp_path, monkeypatch, delay):
    sleeps, output = _prepare_launch(tmp_path, monkeypatch, delay)
    with pytest.raises(SystemExit, match="startup_delay_s"):
        demo.main()
    assert sleeps == []
    assert not output.exists()
