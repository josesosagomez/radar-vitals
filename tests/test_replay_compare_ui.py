"""Headless-safe replay dashboard formatting and native smoke tests."""

from __future__ import annotations

import builtins
import importlib
import math
import sys
import threading
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace

import numpy as np
import pytest


def _display_trace(
    boundary,
    *,
    generation=7,
    hr_bpm=70.0,
    hr_state="accepted",
    br_bpm=12.0,
    br_state="accepted",
    reference_pr_bpm=80.0,
    reference_rr_bpm=15.0,
    reference_available=True,
):
    from src.replay_compare.view_model import DisplayTraceSample

    return DisplayTraceSample(
        boundary=boundary,
        generation=generation,
        radar_hr_bpm=hr_bpm,
        hr_state=hr_state,
        radar_br_bpm=br_bpm,
        br_state=br_state,
        reference_pr_bpm=reference_pr_bpm,
        reference_rr_bpm=reference_rr_bpm,
        reference_available=reference_available,
    )


def _playback_snapshot(
    boundary,
    *,
    generation=7,
    total_frames=1205,
    state="running",
    mode="baseline",
    trace_samples=None,
):
    from src.replay_compare.coverage import CoverageSnapshot
    from src.replay_compare.view_model import DisplayChannel, PlaybackSnapshot

    if trace_samples is None:
        trace_samples = tuple(
            _display_trace(index, generation=generation)
            for index in range(1, boundary + 1)
        )
    return PlaybackSnapshot(
        generation=generation,
        processed_boundary=boundary,
        total_frames=total_frames,
        playback_state=state,
        speed=1.0,
        mode=mode,
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
        coverage=CoverageSnapshot(
            boundary,
            boundary,
            boundary,
            boundary,
            0,
            0,
            0,
            0,
            hr_state="accepted",
            br_state="accepted",
            quiet_state="missing",
        ),
        warning_messages=(),
        trace_samples=tuple(trace_samples),
    )


class _SnapshotAdapter:
    failure = None
    stopping = False

    def __init__(self, snapshot):
        self.latest_snapshot = snapshot
        self.calls = []

    def request_pause(self):
        self.calls.append(("pause",))
        return True

    def resume(self):
        self.calls.append(("resume",))
        return True

    def set_speed(self, speed):
        self.calls.append(("speed", speed))
        return True

    def restart(self):
        self.calls.append(("restart",))
        return True

    def request_stop(self):
        self.calls.append(("stop",))


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
        DisplayTraceSample,
        PlaybackSnapshot,
    )

    trace_samples = tuple(
        DisplayTraceSample(
            boundary=boundary,
            generation=1,
            radar_hr_bpm=72.0,
            hr_state="accepted",
            radar_br_bpm=None,
            br_state="missing",
            reference_pr_bpm=None,
            reference_rr_bpm=None,
            reference_available=False,
        )
        for boundary in range(1, 61)
    )

    snapshot = PlaybackSnapshot(
        generation=1,
        processed_boundary=60,
        total_frames=800,
        playback_state="running",
        speed=1.0,
        mode="baseline",
        hr=DisplayChannel(72.0, "accepted", "accepted", "a0"),
        br=DisplayChannel(None, "missing", "quiet", None),
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
        coverage=CoverageSnapshot(
            60,
            60,
            0,
            0,
            60,
            0,
            0,
            0,
            hr_state="accepted",
            br_state="missing",
            quiet_state="accepted",
        ),
        warning_messages=("Approximate reference alignment",),
        trace_samples=trace_samples,
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
        adapter.latest_snapshot = replace(
            snapshot,
            generation=2,
            trace_samples=tuple(
                replace(sample, generation=2)
                for sample in snapshot.trace_samples
            ),
        )
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


def test_dashboard_preserves_one_frame_held_gap_between_sparse_polls(
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
        coverage=CoverageSnapshot(
            1,
            1,
            1,
            1,
            0,
            1,
            1,
            1,
            hr_state="accepted",
            br_state="accepted",
            quiet_state="missing",
        ),
        warning_messages=(),
        trace_samples=(trace(1, 70.0, "accepted"),),
    )
    second = replace(
        first,
        processed_boundary=3,
        hr=DisplayChannel(72.0, "accepted", "accepted", "a1"),
        br=DisplayChannel(12.0, "accepted", "accepted", "a1"),
        coverage=CoverageSnapshot(
            3,
            2,
            2,
            2,
            0,
            3,
            3,
            3,
            hr_state="accepted",
            br_state="accepted",
            quiet_state="missing",
        ),
        trace_samples=(
            first.trace_samples[0],
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
        window.save_screenshot(tmp_path / "sparse-gap.png")
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


def test_incremental_dashboard_rejects_prefix_forgery_before_any_ui_mutation(
    monkeypatch,
):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    pytest.importorskip("pyqtgraph")

    from src.replay_compare.reference import ReferenceValue
    from src.replay_compare.ui import create_dashboard

    valid = _playback_snapshot(3, total_frames=10)
    adapter = _SnapshotAdapter(valid)
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = create_dashboard(adapter, recording_name="prefix validation")
    try:
        window.poll_once()
        original_prefix = window._validated_trace_prefix
        original_identity = window._validated_trace_identity
        original_snapshot = window._snapshot
        original_arrays = tuple(
            np.array(getattr(window, name), copy=True)
            for name in (
                "_plot_x_s",
                "_radar_hr_bpm",
                "_reference_pr_bpm",
                "_radar_br_bpm",
                "_reference_rr_bpm",
            )
        )
        original_dirty = set(window._dirty_plot_chunks)
        original_redraw = window._last_plot_redraw_monotonic_s
        original_labels = (
            window.time_label.text(),
            window.state_label.text(),
            window.status_values["revision"].text(),
        )
        original_curves = tuple(
            tuple(np.array(part, copy=True) for part in curve.getData())
            for curve in (
                window.hr_radar_curve,
                window.hr_reference_curve,
                window.br_radar_curve,
                window.br_reference_curve,
            )
        )

        samples = valid.trace_samples
        altered_first = replace(samples[0], radar_hr_bpm=999.0)
        reconstructed_first = replace(samples[0])
        mutable_tail = SimpleNamespace(**samples[-1].__dict__)
        valid_reference = ReferenceValue(1, 80.0, 1.0, 15.0, True, True, True)
        invalid_snapshots = (
            replace(valid, trace_samples=(altered_first, *samples[1:])),
            replace(valid, trace_samples=(reconstructed_first, *samples[1:])),
            replace(valid, trace_samples=samples[:-1]),
            replace(valid, processed_boundary=2, trace_samples=samples[1:]),
            replace(
                valid,
                trace_samples=(samples[0], samples[1], replace(samples[2], boundary=4)),
            ),
            replace(valid, trace_samples=(*samples, _display_trace(4))),
            replace(valid, trace_samples=(samples[0], samples[2], samples[1])),
            replace(valid, trace_samples=(samples[0], samples[1], mutable_tail)),
            replace(
                valid,
                trace_samples=(samples[0], samples[1], replace(samples[2], generation=8)),
            ),
            replace(valid, hr=replace(valid.hr, state="forged")),
            replace(
                valid,
                coverage=replace(valid.coverage, denominator_frames=2),
            ),
            replace(valid, selection_revision=True),
            replace(valid, generation=7.0),
            replace(valid, processed_boundary=True),
            replace(valid, total_frames=10.0),
            replace(valid, playback_state="invented"),
            replace(valid, speed="1.0"),
            replace(valid, mode="invented"),
            replace(valid, hr=replace(valid.hr, value_bpm="70.0")),
            replace(valid, quiet_state="invented"),
            replace(valid, selected_bin=3.0),
            replace(valid, measurement_window_age_frames=0.0),
            replace(
                valid,
                coverage=replace(valid.coverage, hr_accepted_frames=3.0),
            ),
            replace(valid, warning_messages=[]),
            replace(
                valid,
                reference=replace(valid_reference, pr_bpm="80.0"),
            ),
            replace(
                valid,
                trace_samples=(
                    samples[0],
                    samples[1],
                    replace(samples[2], radar_hr_bpm="70.0"),
                ),
            ),
            replace(
                valid,
                trace_samples=(
                    samples[0],
                    samples[1],
                    replace(samples[2], reference_available=1),
                ),
            ),
        )

        invalid_labels = (
            "altered prior value",
            "reconstructed equal-value prior sample",
            "truncated trace",
            "shifted trace with shorter boundary",
            "wrong tail endpoint",
            "future sample",
            "out-of-order prior samples",
            "foreign mutable prior sample",
            "wrong trace generation",
            "invalid channel state",
            "inconsistent coverage denominator",
            "boolean selection revision",
            "floating generation",
            "boolean processed boundary",
            "floating total frames",
            "unknown playback state",
            "string speed",
            "unknown mode",
            "string channel value",
            "unknown quiet state",
            "floating selected bin",
            "floating window age",
            "floating coverage count",
            "mutable warnings",
            "string reference value",
            "string trace value",
            "integer reference availability",
        )
        assert len(invalid_labels) == len(invalid_snapshots)

        for label, forged in zip(invalid_labels, invalid_snapshots, strict=True):
            adapter.latest_snapshot = forged
            try:
                window.poll_once()
            except (TypeError, ValueError):
                pass
            else:
                pytest.fail(f"invalid snapshot did not raise: {label}")
            assert window._validated_trace_prefix is original_prefix
            assert window._validated_trace_identity is original_identity
            assert window._snapshot is original_snapshot
            assert set(window._dirty_plot_chunks) == original_dirty
            assert window._last_plot_redraw_monotonic_s == original_redraw
            assert (
                window.time_label.text(),
                window.state_label.text(),
                window.status_values["revision"].text(),
            ) == original_labels
            for name, expected in zip(
                (
                    "_plot_x_s",
                    "_radar_hr_bpm",
                    "_reference_pr_bpm",
                    "_radar_br_bpm",
                    "_reference_rr_bpm",
                ),
                original_arrays,
                strict=True,
            ):
                np.testing.assert_array_equal(getattr(window, name), expected)
            for curve, expected in zip(
                (
                    window.hr_radar_curve,
                    window.hr_reference_curve,
                    window.br_radar_curve,
                    window.br_reference_curve,
                ),
                original_curves,
                strict=True,
            ):
                actual = curve.getData()
                np.testing.assert_array_equal(actual[0], expected[0])
                np.testing.assert_array_equal(actual[1], expected[1])
    finally:
        window.close()
        application.processEvents()


@pytest.mark.parametrize(
    ("state", "boundary", "samples", "accepted"),
    [
        ("preparing", 0, (), True),
        ("preparing", 1, (), False),
        ("preparing", 0, (_display_trace(1),), False),
    ],
)
def test_trace_free_compatibility_is_limited_to_zero_boundary_preparing_snapshots(
    monkeypatch, state, boundary, samples, accepted
):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    pytest.importorskip("pyqtgraph")

    from src.replay_compare.ui import create_dashboard

    snapshot = _playback_snapshot(
        boundary,
        total_frames=10,
        state=state,
        trace_samples=samples,
    )
    adapter = _SnapshotAdapter(snapshot)
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    if accepted:
        window = create_dashboard(adapter, recording_name="preparing compatibility")
        try:
            window.poll_once()
            assert window._validated_trace_prefix == ()
            assert window._validated_trace_identity == ()
        finally:
            window.close()
            application.processEvents()
    else:
        with pytest.raises((TypeError, ValueError)):
            create_dashboard(adapter, recording_name="preparing compatibility")
        application.processEvents()


def _logical_chunk_curve(window, field):
    x_parts = []
    y_parts = []
    for index, chunk in enumerate(window._plot_chunks):
        x_values, y_values = getattr(chunk, field).getData()
        if index:
            x_values = x_values[1:]
            y_values = y_values[1:]
        x_parts.append(np.asarray(x_values))
        y_parts.append(np.asarray(y_values))
    return np.concatenate(x_parts), np.concatenate(y_parts)


@pytest.mark.parametrize("mode", ["baseline", "motion"])
def test_incremental_chunks_equal_lossless_golden_series_after_sparse_poll(
    monkeypatch, mode
):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    pytest.importorskip("pyqtgraph")

    from src.replay_compare.ui import TraceSample, create_dashboard, visible_plot_series

    traces = []
    for boundary in range(1, 1206):
        hr_state = "held" if boundary % 5 == 0 else "accepted"
        br_state = "missing" if boundary % 7 == 0 else "accepted"
        reference_available = boundary % 4 != 0
        traces.append(
            _display_trace(
                boundary,
                hr_bpm=70.0 + boundary / 1000.0,
                hr_state=hr_state,
                br_bpm=None if br_state == "missing" else 12.0 + boundary / 2000.0,
                br_state=br_state,
                reference_pr_bpm=(80.0 + boundary / 1000.0) if reference_available else None,
                reference_rr_bpm=(15.0 + boundary / 2000.0) if reference_available else None,
                reference_available=reference_available,
            )
        )
    traces = tuple(traces)
    first = _playback_snapshot(
        5,
        total_frames=1205,
        mode=mode,
        trace_samples=traces[:5],
    )
    final = _playback_snapshot(
        1205,
        total_frames=1205,
        state="completed",
        mode=mode,
        trace_samples=traces,
    )
    adapter = _SnapshotAdapter(first)
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = create_dashboard(adapter, recording_name=f"{mode} sparse prefix")
    try:
        window.poll_once()
        adapter.latest_snapshot = final
        window.poll_once()

        assert window._validated_trace_prefix is traces
        assert [(chunk.start, chunk.stop) for chunk in window._plot_chunks] == [
            (0, 600),
            (599, 1200),
            (1199, 1205),
        ]
        assert window._dirty_plot_chunks == set()

        golden_hr = visible_plot_series(
            tuple(
                TraceSample(
                    sample.boundary - 1,
                    sample.radar_hr_bpm,
                    sample.hr_state,
                    sample.reference_pr_bpm,
                )
                for sample in traces
            ),
            1205,
            channel="hr",
        )
        golden_br = visible_plot_series(
            tuple(
                TraceSample(
                    sample.boundary - 1,
                    sample.radar_br_bpm,
                    sample.br_state,
                    sample.reference_rr_bpm,
                )
                for sample in traces
            ),
            1205,
            channel="br",
        )
        expected_x = np.asarray(golden_hr.frame_indices, dtype=float) / 20.0
        np.testing.assert_array_equal(window._plot_x_s, expected_x)
        np.testing.assert_array_equal(
            window._radar_hr_bpm, np.asarray(golden_hr.radar_bpm)
        )
        np.testing.assert_array_equal(
            window._reference_pr_bpm, np.asarray(golden_hr.reference_bpm)
        )
        np.testing.assert_array_equal(
            window._radar_br_bpm, np.asarray(golden_br.radar_bpm)
        )
        np.testing.assert_array_equal(
            window._reference_rr_bpm, np.asarray(golden_br.reference_bpm)
        )

        for field, expected in (
            ("hr_radar", golden_hr.radar_bpm),
            ("hr_reference", golden_hr.reference_bpm),
            ("br_radar", golden_br.radar_bpm),
            ("br_reference", golden_br.reference_bpm),
        ):
            actual_x, actual_y = _logical_chunk_curve(window, field)
            np.testing.assert_array_equal(actual_x, expected_x)
            np.testing.assert_array_equal(actual_y, np.asarray(expected))
    finally:
        window.close()
        application.processEvents()


def test_plot_restart_resets_prefix_buffers_and_removes_old_chunk_curves(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    pytest.importorskip("pyqtgraph")

    from src.replay_compare.ui import create_dashboard

    old = _playback_snapshot(605, total_frames=605, state="completed")
    new_traces = (
        _display_trace(1, generation=8, hr_bpm=88.0),
        _display_trace(2, generation=8, hr_bpm=89.0),
    )
    new = _playback_snapshot(
        2,
        generation=8,
        total_frames=10,
        state="paused",
        trace_samples=new_traces,
    )
    adapter = _SnapshotAdapter(old)
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = create_dashboard(adapter, recording_name="restart buffers")
    try:
        window.poll_once()
        old_curves = tuple(
            curve
            for chunk in window._plot_chunks
            for curve in (
                chunk.hr_radar,
                chunk.hr_reference,
                chunk.br_radar,
                chunk.br_reference,
            )
        )
        assert len(old_curves) == 8

        adapter.latest_snapshot = new
        window.poll_once()
        assert window._plot_generation == 8
        assert window._plot_capacity == 10
        assert window._validated_trace_prefix is new_traces
        assert len(window._plot_chunks) == 1
        assert all(
            curve not in old_curves
            for chunk in window._plot_chunks
            for curve in (
                chunk.hr_radar,
                chunk.hr_reference,
                chunk.br_radar,
                chunk.br_reference,
            )
        )
        np.testing.assert_array_equal(window._radar_hr_bpm[:2], [88.0, 89.0])
        assert np.isnan(window._radar_hr_bpm[2:]).all()
        assert np.isnan(window._plot_x_s[2:]).all()
        assert not any(curve in window.hr_plot.listDataItems() for curve in old_curves)
        assert not any(curve in window.br_plot.listDataItems() for curve in old_curves)
    finally:
        window.close()
        application.processEvents()


def test_plot_redraw_cadence_and_pause_eof_screenshot_force_pending_flushes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    pytest.importorskip("pyqtgraph")

    import src.replay_compare.ui as ui

    now = [10.0]
    monkeypatch.setattr(ui.time, "monotonic", lambda: now[0])
    traces = tuple(_display_trace(index) for index in range(1, 5))
    adapter = _SnapshotAdapter(
        _playback_snapshot(1, total_frames=10, trace_samples=traces[:1])
    )
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = ui.create_dashboard(adapter, recording_name="forced flush")
    try:
        window.show()
        application.processEvents()
        assert len(window.hr_radar_curve.getData()[0]) == 1

        now[0] = 10.05
        adapter.latest_snapshot = _playback_snapshot(
            2, total_frames=10, trace_samples=traces[:2]
        )
        window.poll_once()
        assert window._dirty_plot_chunks == {0}
        assert len(window.hr_radar_curve.getData()[0]) == 1

        destination = window.save_screenshot(tmp_path / "forced.png")
        assert destination.is_file() and destination.stat().st_size > 0
        assert window._dirty_plot_chunks == set()
        assert len(window.hr_radar_curve.getData()[0]) == 2

        now[0] = 10.06
        adapter.latest_snapshot = _playback_snapshot(
            3, total_frames=10, trace_samples=traces[:3]
        )
        window.poll_once()
        assert window._dirty_plot_chunks == {0}
        adapter.latest_snapshot = _playback_snapshot(
            3,
            total_frames=10,
            state="paused",
            trace_samples=traces[:3],
        )
        window.poll_once()
        assert window._dirty_plot_chunks == set()
        assert len(window.hr_radar_curve.getData()[0]) == 3

        now[0] = 10.07
        adapter.latest_snapshot = _playback_snapshot(
            4, total_frames=10, trace_samples=traces
        )
        window.poll_once()
        assert window._dirty_plot_chunks == {0}
        adapter.latest_snapshot = _playback_snapshot(
            4,
            total_frames=10,
            state="completed",
            trace_samples=traces,
        )
        window.poll_once()
        assert window._dirty_plot_chunks == set()
        assert len(window.hr_radar_curve.getData()[0]) == 4
    finally:
        window.close()
        application.processEvents()


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
