"""Headless-safe replay dashboard formatting and native smoke tests."""

from __future__ import annotations

import builtins
import importlib
import math
import sys
import threading
from dataclasses import FrozenInstanceError, replace

import pytest


def test_ui_module_import_is_lazy_and_headless_safe(monkeypatch):
    sys.modules.pop("src.replay_compare.ui", None)
    original_import = builtins.__import__

    def reject_gui_import(name, *args, **kwargs):
        if name == "PySide6" or name.startswith("PySide6.") or name == "pyqtgraph":
            raise AssertionError(f"eager GUI import: {name}")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_gui_import)
    module = importlib.import_module("src.replay_compare.ui")

    assert module.SUPPORTED_SPEEDS == (0.5, 1.0, 2.0, 4.0)
    assert module.format_channel(None, "missing").value_text == "--"


@pytest.mark.parametrize("speed", [0.5, 1, 1.0, 2, 4.0])
def test_supported_speed_validation_is_exact(speed):
    from src.replay_compare.ui import validate_speed

    assert validate_speed(speed) == float(speed)


@pytest.mark.parametrize("speed", [True, 0, 1.000001, 3, math.nan, math.inf, "2"])
def test_unsupported_or_coercive_speed_values_are_rejected(speed):
    from src.replay_compare.ui import validate_speed

    with pytest.raises(ValueError, match="speed"):
        validate_speed(speed)


def test_channel_presentations_are_immutable_and_never_show_numeric_zero_br():
    from src.replay_compare.ui import format_channel

    accepted = format_channel(72.04, "accepted", channel="hr")
    held = format_channel(71.0, "held", channel="hr")
    missing = format_channel(None, "missing", channel="br")
    quiet = format_channel(0.0, "missing", channel="br", quiet_state="accepted")

    assert (accepted.value_text, accepted.state_text) == ("72.0", "Accepted")
    assert held.value_text == "71.0" and held.state_text == "Held"
    assert missing.value_text == "--"
    assert quiet.value_text == "--"
    assert quiet.state_text == "No breathing motion detected"
    with pytest.raises(FrozenInstanceError):
        accepted.value_text = "forged"


def test_plot_series_has_nan_gaps_and_discards_all_future_samples():
    from src.replay_compare.ui import TraceSample, visible_plot_series

    samples = (
        TraceSample(0, 70.0, "accepted", 80.0),
        TraceSample(1, 71.0, "held", 81.0),
        TraceSample(2, 72.0, "accepted", 82.0),
        TraceSample(3, None, "missing", None),
    )
    series = visible_plot_series(samples, 2, channel="hr")

    assert series.frame_indices == (0, 1)
    assert series.radar_bpm[0] == pytest.approx(70.0)
    assert math.isnan(series.radar_bpm[1])
    assert series.reference_bpm == pytest.approx((80.0, 81.0))
    with pytest.raises(FrozenInstanceError):
        series.frame_indices = ()


def test_plot_series_rejects_ambiguous_order_and_invalid_state():
    from src.replay_compare.ui import TraceSample, visible_plot_series

    with pytest.raises(ValueError, match="strictly increasing"):
        visible_plot_series(
            (
                TraceSample(1, 70.0, "accepted", 80.0),
                TraceSample(1, 71.0, "accepted", 81.0),
            ),
            2,
            channel="hr",
        )
    with pytest.raises(ValueError, match="display state"):
        visible_plot_series(
            (TraceSample(0, 70.0, "invented", 80.0),), 1, channel="hr"
        )


def test_native_dashboard_consumes_frozen_snapshot_and_routes_controls(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    callback_errors = []
    monkeypatch.setattr(
        sys,
        "excepthook",
        lambda exc_type, value, traceback: callback_errors.append(
            (exc_type, value, traceback)
        ),
    )
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    pytest.importorskip("pyqtgraph")

    from src.replay_compare.coverage import CoverageSnapshot
    from src.replay_compare.ui import SUPPORTED_SPEEDS, create_dashboard
    from src.replay_compare.view_model import (
        DisplayChannel,
        PlaybackSnapshot,
    )

    snapshot = PlaybackSnapshot(
        generation=1,
        processed_boundary=60,
        total_frames=800,
        playback_state="running",
        speed=1.0,
        mode="baseline",
        hr=DisplayChannel(72.0, "accepted", "accepted", "a0"),
        br=DisplayChannel(0.0, "missing", "quiet", None),
        quiet_state="accepted",
        selected_bin=3,
        selection_revision=1,
        accumulation_stage="ordinary_tracking",
        recovery_reason="",
        measurement_window_age_frames=0,
        analysis_lag_frames=0,
        reference=None,
        hr_reference_summary=None,
        br_reference_summary=None,
        coverage=CoverageSnapshot(60, 60, 0, 0, 60, 0, 0, 0),
        warning_messages=("Approximate reference alignment",),
    )

    class Adapter:
        failure = None

        def __init__(self):
            self.latest_snapshot = snapshot
            self.calls = []
            self.accept_controls = True
            self.stopping = False

        def request_pause(self):
            self.calls.append(("pause",))
            return self.accept_controls

        def resume(self):
            self.calls.append(("resume",))
            return self.accept_controls

        def set_speed(self, speed):
            self.calls.append(("speed", speed))
            return self.accept_controls

        def restart(self):
            self.calls.append(("restart",))
            return True

        def request_stop(self):
            self.calls.append(("stop",))

        def stop(self, timeout_s=None):
            self.calls.append(("stop_fallback", timeout_s))

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    adapter = Adapter()
    window = create_dashboard(adapter, recording_name="synthetic replay")
    try:
        window.show()
        application.processEvents()
        window.poll_once()
        assert tuple(
            window.speed_selector.itemData(index)
            for index in range(window.speed_selector.count())
        ) == SUPPORTED_SPEEDS
        assert window.rate_cards["radar_hr"][0].text() == "72.0"
        assert window.rate_cards["radar_br"][0].text() == "--"
        assert "No breathing motion" in window.rate_cards["radar_br"][1].text()
        window.request_pause_or_resume()
        window.set_speed(2.0)
        window.request_restart()
        assert ("pause",) in adapter.calls
        assert ("speed", 2.0) in adapter.calls
        assert ("restart",) in adapter.calls
        assert "waiting for clean shutdown" in window.control_status_label.text()
        assert window.restart_button.isEnabled() is False
        window.poll_once()
        assert "waiting for clean shutdown" in window.control_status_label.text()
        assert window.restart_button.isEnabled() is False
        assert window.pause_button.isEnabled() is False
        assert window.speed_selector.isEnabled() is False
        assert window.stop_button.isEnabled() is True
        adapter.accept_controls = False
        window.request_pause_or_resume()
        assert "Pause refused" in window.control_status_label.text()
        window.set_speed(4.0)
        assert "Speed change refused" in window.control_status_label.text()
        adapter.accept_controls = True
        adapter.latest_snapshot = replace(snapshot, generation=2)
        window.poll_once()
        assert "Restart complete" in window.control_status_label.text()
        assert window.restart_button.isEnabled() is True
        adapter.stopping = True
        adapter.accept_controls = False
        window.poll_once()
        assert window.pause_button.isEnabled() is False
        assert window.speed_selector.isEnabled() is False
        assert window.restart_button.isEnabled() is False
        assert window.stop_button.isEnabled() is False
        window.request_pause_or_resume()
        assert "Pause refused" in window.control_status_label.text()
        destination = window.save_screenshot(tmp_path / "dashboard.png")
        assert destination.is_file() and destination.stat().st_size > 0
    finally:
        window.close()
        application.processEvents()
    assert ("stop",) in adapter.calls
    assert callback_errors == [], [str(value) for _, value, _ in callback_errors]


def test_dashboard_preserves_one_frame_held_gap_between_sparse_polls(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    callback_errors = []
    monkeypatch.setattr(
        sys,
        "excepthook",
        lambda exc_type, value, traceback: callback_errors.append(
            (exc_type, value, traceback)
        ),
    )
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    pytest.importorskip("pyqtgraph")

    from src.replay_compare.coverage import CoverageSnapshot
    from src.replay_compare.ui import create_dashboard
    from src.replay_compare.view_model import (
        DisplayChannel,
        DisplayTraceSample,
        PlaybackSnapshot,
    )

    def trace(boundary, value, state):
        return DisplayTraceSample(
            boundary=boundary,
            generation=7,
            radar_hr_bpm=value,
            hr_state=state,
            radar_br_bpm=12.0 if state == "accepted" else None,
            br_state=state,
            reference_pr_bpm=80.0 + boundary,
            reference_rr_bpm=15.0,
            reference_available=True,
        )

    first = PlaybackSnapshot(
        generation=7,
        processed_boundary=1,
        total_frames=10,
        playback_state="running",
        speed=1.0,
        mode="baseline",
        hr=DisplayChannel(70.0, "accepted", "accepted", "a0"),
        br=DisplayChannel(12.0, "accepted", "accepted", "a0"),
        quiet_state="missing",
        selected_bin=3,
        selection_revision=1,
        accumulation_stage="ordinary_tracking",
        recovery_reason="",
        measurement_window_age_frames=0,
        analysis_lag_frames=0,
        reference=None,
        hr_reference_summary=None,
        br_reference_summary=None,
        coverage=CoverageSnapshot(1, 1, 1, 1, 0, 1, 1, 1),
        warning_messages=(),
        trace_samples=(trace(1, 70.0, "accepted"),),
    )
    second = replace(
        first,
        processed_boundary=3,
        hr=DisplayChannel(72.0, "accepted", "accepted", "a1"),
        br=DisplayChannel(12.0, "accepted", "accepted", "a1"),
        coverage=CoverageSnapshot(3, 2, 2, 2, 0, 3, 3, 3),
        trace_samples=(
            trace(1, 70.0, "accepted"),
            trace(2, 71.0, "held"),
            trace(3, 72.0, "accepted"),
        ),
    )

    class Adapter:
        failure = None

        def __init__(self):
            self.latest_snapshot = first

        def request_pause(self):
            pass

        def resume(self):
            pass

        def set_speed(self, _speed):
            pass

        def restart(self):
            return False

        def request_stop(self):
            pass

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    adapter = Adapter()
    window = create_dashboard(adapter, recording_name="gap test")
    try:
        window.poll_once()
        adapter.latest_snapshot = second
        window.poll_once()
        hr_x, hr_y = window.hr_radar_curve.getData()
        br_x, br_y = window.br_radar_curve.getData()
        assert hr_x.tolist() == pytest.approx([0.0, 0.05, 0.10])
        assert br_x.tolist() == pytest.approx([0.0, 0.05, 0.10])
        assert hr_y[0] == pytest.approx(70.0)
        assert math.isnan(hr_y[1])
        assert hr_y[2] == pytest.approx(72.0)
        assert br_y[0] == pytest.approx(12.0)
        assert math.isnan(br_y[1])
        assert br_y[2] == pytest.approx(12.0)
    finally:
        window.close()
        application.processEvents()
    assert callback_errors == [], [str(value) for _, value, _ in callback_errors]


def test_run_dashboard_owned_loop_waits_for_clean_finalized_shutdown(monkeypatch):
    import src.replay_compare.ui as ui

    stop_entered = threading.Event()
    finalizer_release = threading.Event()

    class Adapter:
        failure = None
        clean_shutdown = False

        def __init__(self):
            self.finalizer = threading.Thread(target=self._finalize, daemon=True)
            self.finalizer.start()

        def _finalize(self):
            assert finalizer_release.wait(timeout=3.0)
            self.clean_shutdown = True

        def stop(self, timeout_s=None):
            assert timeout_s is None
            stop_entered.set()
            self.finalizer.join(timeout=3.0)
            assert self.finalizer.is_alive() is False

    class FakeApplication:
        @staticmethod
        def instance():
            return None

        def __init__(self, _args):
            pass

        def exec(self):
            return 7

    class FakeQtWidgets:
        QApplication = FakeApplication

    class FakeWindow:
        def show(self):
            pass

    monkeypatch.setattr(
        ui,
        "_load_gui_dependencies",
        lambda: (object(), object(), FakeQtWidgets, object()),
    )
    monkeypatch.setattr(ui, "create_dashboard", lambda *_args, **_kwargs: FakeWindow())

    adapter = Adapter()
    outcome = []

    def invoke():
        outcome.append(ui.run_dashboard(adapter, recording_name="owned loop"))

    caller = threading.Thread(target=invoke, daemon=True)
    caller.start()
    assert stop_entered.wait(timeout=2.0)
    assert caller.is_alive() is True
    assert adapter.clean_shutdown is False

    finalizer_release.set()
    caller.join(timeout=2.0)
    assert caller.is_alive() is False
    assert adapter.finalizer.is_alive() is False
    assert adapter.clean_shutdown is True
    assert outcome == [7]
