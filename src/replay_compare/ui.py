"""Native replay dashboard with lazy Qt imports.

The controller remains the sole owner of recording progress, coverage, and
publication state.  This module polls immutable :class:`PlaybackSnapshot`
values on the Qt main thread and sends control requests through a small
duck-typed application adapter.  Importing the module never imports Qt or
pyqtgraph, which keeps headless verification usable.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, is_dataclass
from numbers import Real
from pathlib import Path
from typing import Any, Iterable, Protocol, runtime_checkable

import numpy as np


SUPPORTED_SPEEDS = (0.5, 1.0, 2.0, 4.0)
FRAME_RATE_HZ = 20.0
PLOT_CHUNK_FRAMES = 600
PLOT_REDRAW_INTERVAL_S = 0.1

RADAR_TEAL = "#1ab3a6"
REFERENCE_PURPLE = "#9b72cf"
ACCEPTED_GREEN = "#43c77b"
PRELIMINARY_AMBER = "#e5a84b"
HELD_RED = "#e26767"
MISSING_GREY = "#88919d"

_VALID_CHANNELS = {"hr", "br"}
_VALID_STATES = {"accepted", "preliminary", "held", "missing", "unresolved"}
_VALID_PLAYBACK_STATES = {
    "preparing", "running", "pausing", "paused", "complete", "completed",
    "stopped", "failed",
}
_VALID_MODES = {"baseline", "motion"}
_dashboard_type: type[Any] | None = None


class DashboardDependencyError(RuntimeError):
    """Raised when a native dashboard is requested without its GUI packages."""


@runtime_checkable
class ReplayApplicationAdapter(Protocol):
    """Control surface consumed by the dashboard.

    ``latest_snapshot`` must return a frozen dataclass (normally
    ``view_model.PlaybackSnapshot``).  Methods may enqueue commands; the GUI
    never reaches into the replay controller or analysis worker.
    """

    @property
    def latest_snapshot(self) -> Any: ...

    @property
    def restart_in_progress(self) -> bool: ...

    @property
    def stopping(self) -> bool: ...

    def request_pause(self) -> bool | None: ...

    def resume(self) -> bool | None: ...

    def set_speed(self, speed: float) -> bool | None: ...

    def restart(self) -> bool: ...

    def stop(self, timeout_s: float | None = None) -> None: ...


@dataclass(frozen=True)
class ChannelPresentation:
    """Pure, testable rendering decision for one rate card."""

    value_text: str
    state_text: str
    color: str


@dataclass(frozen=True)
class TraceSample:
    """One immutable, already-observed plot sample in recording-frame time."""

    frame_index: int
    radar_value_bpm: float | None
    radar_state: str
    reference_value_bpm: float | None


@dataclass(frozen=True)
class PlotSeries:
    """Plot-ready prefix. Missing and unaccepted values are represented by NaN."""

    frame_indices: tuple[int, ...]
    radar_bpm: tuple[float, ...]
    reference_bpm: tuple[float, ...]


@dataclass(frozen=True)
class _PlotChunk:
    """Four curves backed by one bounded recording-frame slice.

    ``start`` and ``stop`` describe the maximum half-open slice used by this
    chunk.  Chunks after the first begin one point before their primary
    600-frame interval so adjacent finite lines meet without interpolation.
    """

    start: int
    stop: int
    hr_radar: Any
    hr_reference: Any
    br_radar: Any
    br_reference: Any


@dataclass(frozen=True)
class _ValidatedPlotUpdate:
    """Snapshot-derived plot values that are safe to commit without validation."""

    starts_new_generation: bool
    capacity: int
    trace_prefix: tuple[Any, ...]
    trace_identity: tuple[int, ...]
    tail_start: int
    x_s: tuple[float, ...]
    radar_hr_bpm: tuple[float, ...]
    reference_pr_bpm: tuple[float, ...]
    radar_br_bpm: tuple[float, ...]
    reference_rr_bpm: tuple[float, ...]


def format_channel(
    value_bpm: float | None,
    state: str,
    *,
    channel: str = "hr",
    quiet_state: str = "missing",
) -> ChannelPresentation:
    """Format a radar rate without promoting zero or a held value.

    Quiet breathing is categorical.  Even if an upstream object accidentally
    carries ``0.0``, the BR card renders ``--`` rather than a numeric zero.
    """

    if channel not in _VALID_CHANNELS:
        raise ValueError("channel must be 'hr' or 'br'")
    if state not in _VALID_STATES:
        raise ValueError(f"unknown display state {state!r}")
    if channel == "br" and quiet_state == "accepted":
        return ChannelPresentation("--", "No breathing motion detected", ACCEPTED_GREEN)

    value = _positive_finite(value_bpm)
    if value is None:
        if state == "unresolved":
            return ChannelPresentation("--", "Breathing unresolved", HELD_RED)
        labels = {
            "accepted": "Missing accepted value",
            "preliminary": "Preliminary",
            "held": "Held",
            "missing": "Missing",
        }
        colors = {
            "accepted": HELD_RED,
            "preliminary": PRELIMINARY_AMBER,
            "held": HELD_RED,
            "missing": MISSING_GREY,
        }
        return ChannelPresentation("--", labels[state], colors[state])

    if state == "accepted":
        return ChannelPresentation(f"{value:.1f}", "Accepted", ACCEPTED_GREEN)
    if state == "preliminary":
        return ChannelPresentation(f"{value:.1f}", "Preliminary", PRELIMINARY_AMBER)
    if state in {"held", "unresolved"}:
        label = "Held" if state == "held" else "Held - breathing unresolved"
        return ChannelPresentation(f"{value:.1f}", label, HELD_RED)
    return ChannelPresentation("--", "Missing", MISSING_GREY)


def visible_plot_series(
    samples: Iterable[TraceSample],
    processed_boundary: int,
    *,
    channel: str,
) -> PlotSeries:
    """Return only the observed recording prefix with explicit NaN gaps.

    A processed boundary of ``N`` authorizes frame indices ``[0, N)``.  Future
    samples are discarded even if supplied by a faulty caller.  Radar points
    are visible only while accepted; held/preliminary/missing samples make a
    line break.  Reference points are passive positive recorded values.
    """

    if channel not in _VALID_CHANNELS:
        raise ValueError("channel must be 'hr' or 'br'")
    if type(processed_boundary) is not int or processed_boundary < 0:
        raise ValueError("processed_boundary must be a nonnegative exact integer")

    frames: list[int] = []
    radar: list[float] = []
    reference: list[float] = []
    previous_frame = -1
    for sample in samples:
        if type(sample.frame_index) is not int or sample.frame_index < 0:
            raise ValueError("plot frame indices must be nonnegative exact integers")
        if sample.frame_index <= previous_frame:
            raise ValueError("plot frame indices must be strictly increasing")
        previous_frame = sample.frame_index
        if sample.radar_state not in _VALID_STATES:
            raise ValueError(f"unknown display state {sample.radar_state!r}")
        if sample.frame_index >= processed_boundary:
            continue
        frames.append(sample.frame_index)
        radar_value = _positive_finite(sample.radar_value_bpm)
        reference_value = _positive_finite(sample.reference_value_bpm)
        radar.append(
            radar_value
            if sample.radar_state == "accepted" and radar_value is not None
            else math.nan
        )
        reference.append(reference_value if reference_value is not None else math.nan)
    return PlotSeries(tuple(frames), tuple(radar), tuple(reference))


def validate_speed(speed: float) -> float:
    """Return one supported playback speed, rejecting coercive near matches."""

    if isinstance(speed, bool) or not isinstance(speed, (int, float)):
        raise ValueError("speed must be one of 0.5, 1, 2, or 4")
    numeric = float(speed)
    if not math.isfinite(numeric) or numeric not in SUPPORTED_SPEEDS:
        raise ValueError("speed must be one of 0.5, 1, 2, or 4")
    return numeric


def create_dashboard(
    adapter: ReplayApplicationAdapter,
    *,
    recording_name: str,
    frame_rate_hz: float = FRAME_RATE_HZ,
    poll_interval_ms: int = 50,
    parent: Any = None,
) -> Any:
    """Create and return the native ``QMainWindow`` without starting an event loop."""

    _validate_dashboard_arguments(recording_name, frame_rate_hz, poll_interval_ms)
    initial_snapshot, initial_plot_update = _validate_initial_snapshot(
        adapter, frame_rate_hz=frame_rate_hz
    )
    dashboard_class = _load_dashboard_type()
    return dashboard_class(
        adapter=adapter,
        recording_name=recording_name,
        frame_rate_hz=frame_rate_hz,
        poll_interval_ms=poll_interval_ms,
        parent=parent,
        initial_snapshot=initial_snapshot,
        initial_plot_update=initial_plot_update,
    )


def run_dashboard(
    adapter: ReplayApplicationAdapter,
    *,
    recording_name: str,
    frame_rate_hz: float = FRAME_RATE_HZ,
    poll_interval_ms: int = 50,
) -> int:
    """Create the dashboard and run an owned Qt event loop.

    When a caller supplies an existing ``QApplication``, that caller retains
    lifecycle ownership and must make a bounded ``adapter.stop()`` call after
    its event loop finishes.
    """

    _validate_dashboard_arguments(recording_name, frame_rate_hz, poll_interval_ms)
    initial_snapshot, initial_plot_update = _validate_initial_snapshot(
        adapter, frame_rate_hz=frame_rate_hz
    )
    QtCore, QtGui, QtWidgets, pg = _load_gui_dependencies()
    del QtCore, QtGui, pg
    application = QtWidgets.QApplication.instance()
    owns_application = application is None
    if application is None:
        application = QtWidgets.QApplication([])
    dashboard_class = _load_dashboard_type()
    window = dashboard_class(
        adapter=adapter,
        recording_name=recording_name,
        frame_rate_hz=frame_rate_hz,
        poll_interval_ms=poll_interval_ms,
        parent=None,
        initial_snapshot=initial_snapshot,
        initial_plot_update=initial_plot_update,
    )
    window.show()
    if not owns_application:
        return 0
    try:
        exit_code = int(application.exec())
    finally:
        # closeEvent stays nonblocking while Qt is painting.  Once the owned
        # event loop ends, wait for source verification, worker shutdown, and
        # pass finalization before allowing the process to exit.
        adapter.stop(timeout_s=None)
    failure = getattr(adapter, "failure", None)
    if failure is not None:
        raise RuntimeError(f"replay shutdown failed: {failure}") from failure
    if getattr(adapter, "clean_shutdown", False) is not True:
        raise RuntimeError("replay did not complete a clean finalized shutdown")
    return exit_code


def _validate_dashboard_arguments(
    recording_name: str, frame_rate_hz: float, poll_interval_ms: int
) -> None:
    """Validate public dashboard arguments without loading GUI dependencies."""

    if not recording_name:
        raise ValueError("recording_name is required")
    if frame_rate_hz != FRAME_RATE_HZ:
        raise ValueError("the replay dashboard requires the recorded 20 Hz frame rate")
    if type(poll_interval_ms) is not int or not 1 <= poll_interval_ms <= 250:
        raise ValueError("poll_interval_ms must be an exact integer in [1, 250]")


def _validate_initial_snapshot(
    adapter: ReplayApplicationAdapter, *, frame_rate_hz: float
) -> tuple[Any, _ValidatedPlotUpdate]:
    """Read and fully validate the exact snapshot used for initial rendering."""

    initial_snapshot = adapter.latest_snapshot
    initial_plot_update = _validate_snapshot_update(
        initial_snapshot,
        frame_rate_hz=frame_rate_hz,
        prior_generation=None,
        prior_capacity=0,
        retained_prefix=(),
        retained_identity=(),
    )
    return initial_snapshot, initial_plot_update


def _load_gui_dependencies() -> tuple[Any, Any, Any, Any]:
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
        import pyqtgraph as pg
    except (ImportError, ModuleNotFoundError, OSError) as exc:
        raise DashboardDependencyError(
            "The native replay dashboard requires pinned PySide6 and pyqtgraph. "
            "Headless replay and artifact verification remain available."
        ) from exc
    return QtCore, QtGui, QtWidgets, pg


def _load_dashboard_type() -> type[Any]:
    global _dashboard_type
    if _dashboard_type is not None:
        return _dashboard_type

    QtCore, QtGui, QtWidgets, pg = _load_gui_dependencies()

    class PassiveProgressBar(QtWidgets.QWidget):
        """Read-only progress presentation with deferred, coalesced painting.

        The logical range and value follow QProgressBar semantics, including
        its reset sentinel and special 0/0 indeterminate range. Setters only
        request a future paint; this widget never starts an animation or calls
        ``repaint()`` synchronously.
        """

        _INT_MIN = -(2**31)
        valueChanged = QtCore.Signal(int)

        def __init__(self, parent: Any = None):
            super().__init__(parent)
            self._minimum = 0
            self._maximum = 100
            self._value = -1
            self._text_visible = True
            self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
            self.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Fixed,
            )
            self.setMinimumSize(40, 24)
            self.setMaximumHeight(24)
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
            self.setAccessibleName("Recording progress")
            self._refresh_accessible_description()

        def minimum(self) -> int:
            return self._minimum

        def maximum(self) -> int:
            return self._maximum

        def value(self) -> int:
            return self._value

        def isTextVisible(self) -> bool:  # noqa: N802 - Qt-compatible API
            return self._text_visible

        def setTextVisible(self, visible: bool) -> None:  # noqa: N802
            visible = bool(visible)
            if visible == self._text_visible:
                return
            self._text_visible = visible
            self.update(self.rect())

        def reset(self) -> None:
            reset_value = (
                self._INT_MIN
                if self._minimum == self._INT_MIN
                else self._minimum - 1
            )
            self._value = reset_value
            self._refresh_accessible_description()
            self.update(self.rect())

        def setRange(self, minimum: int, maximum: int) -> None:  # noqa: N802
            minimum = int(minimum)
            maximum = max(minimum, int(maximum))
            if (minimum, maximum) == (self._minimum, self._maximum):
                return
            self._minimum = minimum
            self._maximum = maximum
            if self._value < minimum - 1 or self._value > maximum:
                self._value = (
                    self._INT_MIN if minimum == self._INT_MIN else minimum - 1
                )
            self._refresh_accessible_description()
            self.update(self.rect())

        def setMinimum(self, minimum: int) -> None:  # noqa: N802
            minimum = int(minimum)
            self.setRange(minimum, max(self._maximum, minimum))

        def setMaximum(self, maximum: int) -> None:  # noqa: N802
            maximum = int(maximum)
            self.setRange(min(self._minimum, maximum), maximum)

        def setValue(self, value: int) -> None:  # noqa: N802
            value = int(value)
            if self._value == value:
                return
            outside_range = value < self._minimum or value > self._maximum
            if outside_range and (self._minimum, self._maximum) != (0, 0):
                return
            self._value = value
            self.valueChanged.emit(value)
            self._refresh_accessible_description()
            self.update(self.rect())

        def text(self) -> str:
            if (
                (self._minimum, self._maximum) == (0, 0)
                or self._value < self._minimum
                or (
                    self._minimum == self._INT_MIN
                    and self._value == self._INT_MIN
                )
            ):
                return ""
            total_steps = self._maximum - self._minimum
            if total_steps == 0:
                return "100%"
            percentage = int(
                (self._value - self._minimum) * 100.0 / total_steps
            )
            return f"{percentage}%"

        def flush_pending_update(self) -> None:
            """Ensure the current logical state has a queued paint request."""

            self.update(self.rect())

        def sizeHint(self) -> Any:  # noqa: N802 - Qt virtual API
            return QtCore.QSize(100, 24)

        def minimumSizeHint(self) -> Any:  # noqa: N802 - Qt virtual API
            return QtCore.QSize(40, 24)

        def paintEvent(self, event: Any) -> None:  # noqa: N802 - Qt virtual API
            painter = QtGui.QPainter(self)
            background = QtGui.QColor("#141922")
            border = QtGui.QColor("#46536a")
            text_color = QtGui.QColor("#e8edf4")
            fill_color = QtGui.QColor(RADAR_TEAL)
            try:
                # WA_OpaquePaintEvent is valid because every exposed pixel is
                # filled before the current border, fill, and text are drawn.
                painter.fillRect(event.rect(), background)
                bounds = QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
                painter.setPen(QtGui.QPen(border, 1.0))
                painter.setBrush(background)
                painter.drawRoundedRect(bounds, 3.0, 3.0)

                if self._maximum > self._minimum and self._value >= self._minimum:
                    fraction = (self._value - self._minimum) / (
                        self._maximum - self._minimum
                    )
                    if fraction > 0.0:
                        inner = bounds.adjusted(1.0, 1.0, -1.0, -1.0)
                        inner.setWidth(inner.width() * min(1.0, fraction))
                        painter.setPen(QtCore.Qt.PenStyle.NoPen)
                        painter.setBrush(fill_color)
                        painter.drawRoundedRect(inner, 2.0, 2.0)

                label = self.text()
                if self._text_visible and label:
                    painter.setPen(text_color)
                    painter.drawText(
                        self.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, label
                    )
            finally:
                painter.end()

        def _refresh_accessible_description(self) -> None:
            label = self.text()
            if label:
                description = (
                    f"Playback progress {label}; value {self._value} of "
                    f"{self._maximum}."
                )
            elif (self._minimum, self._maximum) == (0, 0):
                description = "Playback progress is indeterminate."
            else:
                description = "Playback progress has not started."
            if self.accessibleDescription() != description:
                self.setAccessibleDescription(description)

    class ReplayDashboard(QtWidgets.QMainWindow):
        """Read-only rendering of controller-owned immutable replay snapshots."""

        def __init__(
            self,
            *,
            adapter: ReplayApplicationAdapter,
            recording_name: str,
            frame_rate_hz: float,
            poll_interval_ms: int,
            parent: Any,
            initial_snapshot: Any,
            initial_plot_update: _ValidatedPlotUpdate,
        ):
            super().__init__(parent)
            self.adapter = adapter
            self.recording_name = recording_name
            self.frame_rate_hz = frame_rate_hz
            self._snapshot: Any = None
            self._plot_generation: int | None = None
            self._plot_capacity = 0
            self._validated_trace_prefix: tuple[Any, ...] = ()
            self._validated_trace_identity: tuple[int, ...] = ()
            self._plot_x_s = np.empty(0, dtype=np.float64)
            self._radar_hr_bpm = np.empty(0, dtype=np.float64)
            self._reference_pr_bpm = np.empty(0, dtype=np.float64)
            self._radar_br_bpm = np.empty(0, dtype=np.float64)
            self._reference_rr_bpm = np.empty(0, dtype=np.float64)
            self._plot_chunks: list[_PlotChunk] = []
            self._dirty_plot_chunks: set[int] = set()
            self._last_plot_redraw_monotonic_s: float | None = None
            self._plot_x_ranges: dict[int, tuple[float, float]] = {}
            self.hr_radar_curve: Any = None
            self.hr_reference_curve: Any = None
            self.br_radar_curve: Any = None
            self.br_reference_curve: Any = None
            self._control_message = ""
            self._restart_pending_generation: int | None = None

            self.setObjectName("replayComparisonDashboard")
            self.setWindowTitle("Recorded radar / Masimo comparison")
            self.resize(1380, 900)
            self._build_ui()
            self._apply_validated_snapshot(initial_snapshot, initial_plot_update)
            self.poll_timer = QtCore.QTimer(self)
            self.poll_timer.setInterval(poll_interval_ms)
            self.poll_timer.timeout.connect(self.poll_once)
            self.poll_timer.start()

        def _build_ui(self) -> None:
            central = QtWidgets.QWidget(self)
            self.setCentralWidget(central)
            root = QtWidgets.QVBoxLayout(central)
            root.setContentsMargins(16, 14, 16, 12)
            root.setSpacing(10)

            header = QtWidgets.QHBoxLayout()
            self.title_label = QtWidgets.QLabel(self.recording_name)
            self.title_label.setObjectName("recordingName")
            title_font = self.title_label.font()
            title_font.setPointSize(16)
            title_font.setBold(True)
            self.title_label.setFont(title_font)
            self.mode_label = QtWidgets.QLabel("Mode: --")
            self.state_label = QtWidgets.QLabel("State: --")
            self.time_label = QtWidgets.QLabel("00:00.0 / 00:00.0")
            header.addWidget(self.title_label, 1)
            header.addWidget(self.mode_label)
            header.addWidget(self.state_label)
            header.addWidget(self.time_label)
            root.addLayout(header)

            self.warning_label = QtWidgets.QLabel("Waiting for replay status...")
            self.warning_label.setObjectName("persistentWarnings")
            self.warning_label.setWordWrap(True)
            self.warning_label.setStyleSheet(
                "QLabel { background: #382f1f; color: #ffd98a; "
                "padding: 7px; border-radius: 4px; }"
            )
            root.addWidget(self.warning_label)

            cards = QtWidgets.QHBoxLayout()
            self.rate_cards: dict[str, tuple[Any, Any]] = {}
            for key, title in (
                ("radar_hr", "Radar HR"),
                ("reference_hr", "Masimo PR"),
                ("radar_br", "Radar BR"),
                ("reference_br", "Masimo RRp"),
            ):
                card, value_label, status_label = self._rate_card(title)
                self.rate_cards[key] = (value_label, status_label)
                cards.addWidget(card)
            root.addLayout(cards)

            summaries = QtWidgets.QHBoxLayout()
            self.hr_summary_label = QtWidgets.QLabel("HR window summary - descriptive: --")
            self.br_summary_label = QtWidgets.QLabel("BR window summary - descriptive: --")
            self.hr_summary_label.setWordWrap(True)
            self.br_summary_label.setWordWrap(True)
            summaries.addWidget(self.hr_summary_label)
            summaries.addWidget(self.br_summary_label)
            root.addLayout(summaries)

            plots = QtWidgets.QHBoxLayout()
            self.hr_plot = self._comparison_plot("Heart rate", "bpm")
            self.br_plot = self._comparison_plot("Breathing rate", "breaths/min")
            plots.addWidget(self.hr_plot)
            plots.addWidget(self.br_plot)
            root.addLayout(plots, 1)

            lower = QtWidgets.QHBoxLayout()
            coverage_box = QtWidgets.QGroupBox("Display and reference availability")
            coverage_layout = QtWidgets.QFormLayout(coverage_box)
            self.coverage_labels: dict[str, Any] = {}
            for key, title in (
                ("hr", "Radar HR accepted"),
                ("br", "Radar numeric BR accepted"),
                ("both", "Both radar values accepted"),
                ("quiet", "Quiet status accepted"),
                ("pr", "Masimo PR present"),
                ("pr_quality", "Masimo PR PI-qualified"),
                ("rr", "Masimo RRp present"),
            ):
                label = QtWidgets.QLabel("--")
                self.coverage_labels[key] = label
                coverage_layout.addRow(title, label)
            lower.addWidget(coverage_box, 2)

            status_box = QtWidgets.QGroupBox("Processing status")
            status_layout = QtWidgets.QFormLayout(status_box)
            self.status_values: dict[str, Any] = {}
            for key, title in (
                ("stage", "Accumulation"),
                ("bin", "Selected bin / range"),
                ("recovery", "Recovery / hold reason"),
                ("age", "Measurement-window age"),
                ("lag", "Analysis lag"),
                ("revision", "Selection revision"),
            ):
                label = QtWidgets.QLabel("--")
                label.setWordWrap(True)
                self.status_values[key] = label
                status_layout.addRow(title, label)
            lower.addWidget(status_box, 2)
            root.addLayout(lower)

            controls = QtWidgets.QHBoxLayout()
            self.pause_button = QtWidgets.QPushButton("Pause")
            self.pause_button.setObjectName("pauseResumeButton")
            self.pause_button.clicked.connect(self.request_pause_or_resume)
            self.restart_button = QtWidgets.QPushButton("Restart")
            self.restart_button.setObjectName("restartButton")
            self.restart_button.clicked.connect(self.request_restart)
            self.stop_button = QtWidgets.QPushButton("Stop")
            self.stop_button.setObjectName("stopButton")
            self.stop_button.clicked.connect(self.request_stop)
            self.speed_selector = QtWidgets.QComboBox()
            self.speed_selector.setObjectName("speedSelector")
            for speed in SUPPORTED_SPEEDS:
                self.speed_selector.addItem(f"{speed:g}x", speed)
            self.speed_selector.currentIndexChanged.connect(self._speed_changed)
            self.progress_bar = PassiveProgressBar()
            self.progress_bar.setObjectName("recordingProgress")
            self.progress_bar.setTextVisible(True)
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)
            controls.addWidget(self.pause_button)
            controls.addWidget(self.restart_button)
            controls.addWidget(self.stop_button)
            controls.addWidget(QtWidgets.QLabel("Playback speed"))
            controls.addWidget(self.speed_selector)
            controls.addWidget(self.progress_bar, 1)
            root.addLayout(controls)

            self.control_status_label = QtWidgets.QLabel("")
            self.control_status_label.setObjectName("controlStatus")
            root.addWidget(self.control_status_label)
            footer = QtWidgets.QLabel(
                "Development replay of recorded data - not for clinical use or "
                "paper-grade validation. Coverage is availability, not accuracy."
            )
            footer.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            footer.setStyleSheet("QLabel { color: #8f99a8; padding-top: 3px; }")
            root.addWidget(footer)

            self.setStyleSheet(
                "QMainWindow, QWidget { background: #141922; color: #e8edf4; }"
                "QGroupBox { border: 1px solid #384253; border-radius: 5px; "
                "margin-top: 8px; padding-top: 8px; }"
                "QGroupBox::title { subcontrol-origin: margin; left: 8px; }"
                "QPushButton, QComboBox { background: #283242; padding: 6px 10px; "
                "border: 1px solid #46536a; border-radius: 4px; }"
                "QPushButton:disabled { color: #667083; }"
                "QProgressBar { border: 1px solid #46536a; border-radius: 3px; "
                "text-align: center; }"
                f"QProgressBar::chunk {{ background: {RADAR_TEAL}; }}"
            )

        def _rate_card(self, title: str) -> tuple[Any, Any, Any]:
            frame = QtWidgets.QFrame()
            frame.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
            layout = QtWidgets.QVBoxLayout(frame)
            title_label = QtWidgets.QLabel(title)
            value_label = QtWidgets.QLabel("--")
            value_font = value_label.font()
            value_font.setPointSize(23)
            value_font.setBold(True)
            value_label.setFont(value_font)
            status_label = QtWidgets.QLabel("Missing")
            status_label.setWordWrap(True)
            layout.addWidget(title_label)
            layout.addWidget(value_label)
            layout.addWidget(status_label)
            return frame, value_label, status_label

        def _comparison_plot(self, title: str, units: str) -> Any:
            # pyqtgraph 0.13.x can ask a title LabelItem for its size hint
            # before LabelItem.__init__ has finished when ``title=`` is passed
            # through PlotWidget construction.  Creating the widget first keeps
            # Qt virtual callbacks outside that partially initialized window.
            plot = pg.PlotWidget()
            plot.setTitle(title)
            plot.setLabel("left", units)
            plot.setLabel("bottom", "Recording time", units="s")
            plot.showGrid(x=True, y=True, alpha=0.18)
            plot.addLegend()
            plot.setBackground("#11161e")
            return plot

        @staticmethod
        def _set_text(widget: Any, text: str) -> None:
            if widget.text() != text:
                widget.setText(text)

        @staticmethod
        def _set_style(widget: Any, style_sheet: str) -> None:
            if widget.styleSheet() != style_sheet:
                widget.setStyleSheet(style_sheet)

        @staticmethod
        def _set_enabled(widget: Any, enabled: bool) -> None:
            if widget.isEnabled() != enabled:
                widget.setEnabled(enabled)

        def _set_plot_x_range(
            self, plot: Any, minimum_s: float, maximum_s: float
        ) -> None:
            requested = (minimum_s, maximum_s)
            key = id(plot)
            if self._plot_x_ranges.get(key) == requested:
                return
            plot.setXRange(minimum_s, maximum_s, padding=0.0)
            self._plot_x_ranges[key] = requested

        @QtCore.Slot()
        def poll_once(self) -> None:
            snapshot = self.adapter.latest_snapshot
            plot_update = self._validate_plot_update(snapshot)

            self._apply_validated_snapshot(snapshot, plot_update)

        def _apply_validated_snapshot(
            self, snapshot: Any, plot_update: _ValidatedPlotUpdate
        ) -> None:
            """Commit and render one snapshot after complete pure validation."""

            # All snapshot and trace validation completes before any dashboard
            # state, labels, buffers, or curves are changed.
            self._commit_plot_update(snapshot, plot_update)
            if (
                self._restart_pending_generation is not None
                and snapshot.generation > self._restart_pending_generation
            ):
                self._control_message = (
                    f"Restart complete in preserved pass generation {snapshot.generation}"
                )
                self._restart_pending_generation = None
            elif self._restart_pending_generation is not None:
                restart_active = bool(
                    getattr(self.adapter, "restart_in_progress", False)
                )
                if getattr(self.adapter, "failure", None) is not None and not restart_active:
                    self._control_message = (
                        f"Restart failed: {self.adapter.failure}"
                    )
                    self._restart_pending_generation = None
                elif bool(getattr(self.adapter, "stopping", False)) and not restart_active:
                    self._control_message = "Restart cancelled by stop request"
                    self._restart_pending_generation = None
            self._snapshot = snapshot
            self._update_header(snapshot)
            self._update_cards(snapshot)
            self._update_summaries(snapshot)
            self._update_coverage(snapshot)
            self._update_status(snapshot)
            self._update_controls(snapshot)
            force_plot_flush = snapshot.playback_state in {
                "pausing", "paused", "complete", "completed", "stopped", "failed"
            }
            self._flush_plots(force=force_plot_flush)
            if force_plot_flush:
                self.progress_bar.flush_pending_update()

        def _update_header(self, snapshot: Any) -> None:
            state_text = _state_label(snapshot.playback_state)
            self._set_text(self.mode_label, f"Mode: {snapshot.mode}")
            self._set_text(
                self.state_label, f"State: {state_text} | {snapshot.speed:g}x"
            )
            elapsed_s = snapshot.processed_boundary / self.frame_rate_hz
            total_s = snapshot.total_frames / self.frame_rate_hz
            self._set_text(
                self.time_label,
                f"{_format_duration(elapsed_s)} / {_format_duration(total_s)}"
            )
            alignment_warning = (
                "Approximate reference alignment - exact radar frame-zero UTC "
                "was not recorded."
            )
            warnings = tuple(str(item) for item in snapshot.warning_messages if str(item))
            if alignment_warning not in warnings:
                warnings = (alignment_warning, *warnings)
            self._set_text(
                self.warning_label,
                "\n".join(f"Warning: {item}" for item in warnings),
            )
            self.warning_label.setVisible(bool(warnings))

        def _update_cards(self, snapshot: Any) -> None:
            hr = format_channel(snapshot.hr.value_bpm, snapshot.hr.state, channel="hr")
            br = format_channel(
                snapshot.br.value_bpm,
                snapshot.br.state,
                channel="br",
                quiet_state=snapshot.quiet_state,
            )
            self._set_card("radar_hr", hr)
            self._set_card("radar_br", br)

            reference = snapshot.reference
            if reference is None:
                pr = ChannelPresentation("--", "Missing", MISSING_GREY)
                rr = ChannelPresentation("--", "Missing", MISSING_GREY)
            else:
                pr_value = _positive_finite(reference.pr_bpm)
                if pr_value is None:
                    pr = ChannelPresentation("--", "Missing", MISSING_GREY)
                elif reference.pr_qualified:
                    pr = ChannelPresentation(
                        f"{pr_value:.1f}",
                        f"PI-qualified | Unix {reference.timestamp_s}",
                        REFERENCE_PURPLE,
                    )
                else:
                    pi_text = "missing" if reference.pi is None else f"{reference.pi:.2f}"
                    pr = ChannelPresentation(
                        f"{pr_value:.1f}",
                        f"Low PR quality (PI {pi_text}) | Unix {reference.timestamp_s}",
                        HELD_RED,
                    )
                rr_value = _positive_finite(reference.rr_bpm)
                rr = (
                    ChannelPresentation(
                        f"{rr_value:.1f}",
                        f"Recorded sample | Unix {reference.timestamp_s}",
                        REFERENCE_PURPLE,
                    )
                    if rr_value is not None
                    else ChannelPresentation("--", "Missing", MISSING_GREY)
                )
            self._set_card("reference_hr", pr)
            self._set_card("reference_br", rr)

        def _set_card(self, key: str, presentation: ChannelPresentation) -> None:
            value_label, status_label = self.rate_cards[key]
            style_sheet = f"QLabel {{ color: {presentation.color}; }}"
            self._set_text(value_label, presentation.value_text)
            self._set_text(status_label, presentation.state_text)
            self._set_style(value_label, style_sheet)
            self._set_style(status_label, style_sheet)

        def _update_summaries(self, snapshot: Any) -> None:
            self._set_text(
                self.hr_summary_label,
                _format_reference_summary("HR", snapshot.hr_reference_summary),
            )
            self._set_text(
                self.br_summary_label,
                _format_reference_summary("BR", snapshot.br_reference_summary),
            )

        def _update_coverage(self, snapshot: Any) -> None:
            coverage = snapshot.coverage
            denominator = coverage.denominator_frames
            numerators = {
                "hr": coverage.hr_accepted_frames,
                "br": coverage.br_numeric_frames,
                "both": coverage.both_frames,
                "quiet": coverage.quiet_frames,
                "pr": coverage.reference_pr_frames,
                "pr_quality": coverage.reference_pr_qualified_frames,
                "rr": coverage.reference_rr_frames,
            }
            for key, numerator in numerators.items():
                self._set_text(
                    self.coverage_labels[key],
                    _format_coverage(numerator, denominator, self.frame_rate_hz),
                )

        def _update_status(self, snapshot: Any) -> None:
            selected_range = getattr(snapshot, "selected_range_m", None)
            range_text = (
                "--" if _positive_finite(selected_range) is None else f"{selected_range:.3f} m"
            )
            bin_text = "--" if snapshot.selected_bin is None else str(snapshot.selected_bin)
            self._set_text(
                self.status_values["stage"], str(snapshot.accumulation_stage)
            )
            self._set_text(
                self.status_values["bin"], f"{bin_text} / {range_text}"
            )
            self._set_text(
                self.status_values["recovery"], str(snapshot.recovery_reason or "--")
            )
            self._set_text(
                self.status_values["age"],
                _format_frames(snapshot.measurement_window_age_frames, self.frame_rate_hz),
            )
            self._set_text(
                self.status_values["lag"],
                _format_frames(snapshot.analysis_lag_frames, self.frame_rate_hz),
            )
            self._set_text(
                self.status_values["revision"], str(snapshot.selection_revision)
            )

        def _update_controls(self, snapshot: Any) -> None:
            state = snapshot.playback_state
            terminal = state in {"complete", "completed", "stopped", "failed"}
            restart_pending = self._restart_pending_generation is not None
            stopping = bool(getattr(self.adapter, "stopping", False))
            if state == "paused":
                pause_text = "Resume"
                pause_enabled = True
            elif state == "pausing":
                pause_text = "Pausing..."
                pause_enabled = False
            else:
                pause_text = "Pause"
                pause_enabled = not terminal
            if restart_pending or stopping:
                pause_text = "Stopping..." if stopping else "Restarting..."
                pause_enabled = False
            self._set_text(self.pause_button, pause_text)
            self._set_enabled(self.pause_button, pause_enabled)

            # The preserved completed snapshot stays visible while a restart
            # constructs the next generation, so Stop remains available.
            stop_enabled = (not terminal or restart_pending) and not stopping
            self._set_enabled(self.stop_button, stop_enabled)
            self._set_enabled(
                self.restart_button,
                state != "pausing" and not restart_pending and not stopping
            )

            speed = validate_speed(snapshot.speed)
            speed_index = self.speed_selector.findData(speed)
            if speed_index >= 0 and self.speed_selector.currentIndex() != speed_index:
                prior_blocked = self.speed_selector.blockSignals(True)
                try:
                    self.speed_selector.setCurrentIndex(speed_index)
                finally:
                    self.speed_selector.blockSignals(prior_blocked)
            self._set_enabled(
                self.speed_selector,
                not terminal and not restart_pending and not stopping
            )

            total = max(0, int(snapshot.total_frames))
            boundary = min(max(0, int(snapshot.processed_boundary)), total)
            self.progress_bar.setRange(0, max(1, total))
            self.progress_bar.setValue(boundary)
            suffix = ""
            if getattr(self.adapter, "failure", None) is not None:
                suffix = f"Replay failure: {self.adapter.failure}"
            self._set_text(
                self.control_status_label, suffix or self._control_message
            )

        def _validate_plot_update(self, snapshot: Any) -> _ValidatedPlotUpdate:
            return _validate_snapshot_update(
                snapshot,
                frame_rate_hz=self.frame_rate_hz,
                prior_generation=self._plot_generation,
                prior_capacity=self._plot_capacity,
                retained_prefix=self._validated_trace_prefix,
                retained_identity=self._validated_trace_identity,
            )

        def _commit_plot_update(
            self, snapshot: Any, update: _ValidatedPlotUpdate
        ) -> None:
            if update.starts_new_generation:
                self._reset_plot_generation(snapshot.generation, update.capacity)

            tail_stop = update.tail_start + len(update.x_s)
            if tail_stop > update.capacity:
                # This is implied by the snapshot scalar validation.  Keep the
                # local guard so future refactors cannot overrun fixed buffers.
                raise RuntimeError("validated plot tail exceeds buffer capacity")
            if tail_stop > update.tail_start:
                plot_slice = slice(update.tail_start, tail_stop)
                self._plot_x_s[plot_slice] = update.x_s
                self._radar_hr_bpm[plot_slice] = update.radar_hr_bpm
                self._reference_pr_bpm[plot_slice] = update.reference_pr_bpm
                self._radar_br_bpm[plot_slice] = update.radar_br_bpm
                self._reference_rr_bpm[plot_slice] = update.reference_rr_bpm
                first_chunk = update.tail_start // PLOT_CHUNK_FRAMES
                final_chunk = (tail_stop - 1) // PLOT_CHUNK_FRAMES
                self._dirty_plot_chunks.update(range(first_chunk, final_chunk + 1))
            self._validated_trace_prefix = update.trace_prefix
            self._validated_trace_identity = update.trace_identity

        def _reset_plot_generation(self, generation: int, capacity: int) -> None:
            for chunk in self._plot_chunks:
                self.hr_plot.removeItem(chunk.hr_radar)
                self.hr_plot.removeItem(chunk.hr_reference)
                self.br_plot.removeItem(chunk.br_radar)
                self.br_plot.removeItem(chunk.br_reference)

            self._plot_generation = generation
            self._plot_capacity = capacity
            self._validated_trace_prefix = ()
            self._validated_trace_identity = ()
            # NaN initialization makes unwritten/future positions explicit and
            # prevents a restarted generation from inheriting visible history.
            self._plot_x_s = np.full(capacity, np.nan, dtype=np.float64)
            self._radar_hr_bpm = np.full(capacity, np.nan, dtype=np.float64)
            self._reference_pr_bpm = np.full(capacity, np.nan, dtype=np.float64)
            self._radar_br_bpm = np.full(capacity, np.nan, dtype=np.float64)
            self._reference_rr_bpm = np.full(capacity, np.nan, dtype=np.float64)
            self._plot_chunks = []
            self._dirty_plot_chunks = set()
            self._last_plot_redraw_monotonic_s = None
            self.hr_radar_curve = None
            self.hr_reference_curve = None
            self.br_radar_curve = None
            self.br_reference_curve = None
            if capacity:
                self._ensure_plot_chunk(0)

        def _ensure_plot_chunk(self, chunk_index: int) -> _PlotChunk:
            while len(self._plot_chunks) <= chunk_index:
                index = len(self._plot_chunks)
                start = max(0, index * PLOT_CHUNK_FRAMES - 1)
                stop = min(self._plot_capacity, (index + 1) * PLOT_CHUNK_FRAMES)
                curve_name = index == 0
                hr_radar = self.hr_plot.plot(
                    pen=pg.mkPen(RADAR_TEAL, width=2),
                    name="Radar accepted" if curve_name else None,
                )
                hr_reference = self.hr_plot.plot(
                    pen=pg.mkPen(REFERENCE_PURPLE, width=2),
                    name="Masimo PR" if curve_name else None,
                )
                br_radar = self.br_plot.plot(
                    pen=pg.mkPen(RADAR_TEAL, width=2),
                    name="Radar accepted" if curve_name else None,
                )
                br_reference = self.br_plot.plot(
                    pen=pg.mkPen(REFERENCE_PURPLE, width=2),
                    name="Masimo RRp" if curve_name else None,
                )
                for curve in (hr_radar, hr_reference, br_radar, br_reference):
                    curve.setDownsampling(ds=1, auto=False)
                    curve.setClipToView(False)
                self._plot_chunks.append(_PlotChunk(
                    start=start,
                    stop=stop,
                    hr_radar=hr_radar,
                    hr_reference=hr_reference,
                    br_radar=br_radar,
                    br_reference=br_reference,
                ))

            first = self._plot_chunks[0]
            self.hr_radar_curve = first.hr_radar
            self.hr_reference_curve = first.hr_reference
            self.br_radar_curve = first.br_radar
            self.br_reference_curve = first.br_reference
            return self._plot_chunks[chunk_index]

        def _flush_plots(self, *, force: bool) -> None:
            if not self._dirty_plot_chunks:
                return
            now = time.monotonic()
            if (
                not force
                and self._last_plot_redraw_monotonic_s is not None
                and now - self._last_plot_redraw_monotonic_s
                < PLOT_REDRAW_INTERVAL_S
            ):
                return

            observed_stop = len(self._validated_trace_prefix)
            for chunk_index in sorted(self._dirty_plot_chunks):
                chunk = self._ensure_plot_chunk(chunk_index)
                stop = min(chunk.stop, observed_stop)
                plot_slice = slice(chunk.start, stop)
                x_s = self._plot_x_s[plot_slice]
                chunk.hr_radar.setData(
                    x_s, self._radar_hr_bpm[plot_slice], connect="finite"
                )
                chunk.hr_reference.setData(
                    x_s, self._reference_pr_bpm[plot_slice], connect="finite"
                )
                chunk.br_radar.setData(
                    x_s, self._radar_br_bpm[plot_slice], connect="finite"
                )
                chunk.br_reference.setData(
                    x_s, self._reference_rr_bpm[plot_slice], connect="finite"
                )

            self._dirty_plot_chunks.clear()
            self._last_plot_redraw_monotonic_s = now
            elapsed_s = observed_stop / self.frame_rate_hz
            visible_stop = max(30.0, elapsed_s)
            self._set_plot_x_range(self.hr_plot, 0.0, visible_stop)
            self._set_plot_x_range(self.br_plot, 0.0, visible_stop)

        @QtCore.Slot()
        def request_pause_or_resume(self) -> None:
            if self._snapshot is None:
                return
            state = self._snapshot.playback_state
            if state == "paused":
                accepted = self.adapter.resume()
                self._control_message = (
                    "Resume refused during a replay lifecycle transition"
                    if accepted is False
                    else "Resume requested"
                )
            elif state not in {"pausing", "complete", "completed", "stopped", "failed"}:
                accepted = self.adapter.request_pause()
                if accepted is False:
                    self._control_message = (
                        "Pause refused during a replay lifecycle transition"
                    )
                else:
                    self._set_text(self.pause_button, "Pausing...")
                    self._set_enabled(self.pause_button, False)
                    self._control_message = (
                        "Pause requested; waiting for controller acknowledgment"
                    )
            self._set_text(self.control_status_label, self._control_message)

        @QtCore.Slot()
        def request_restart(self) -> None:
            try:
                restarted = bool(self.adapter.restart())
            except Exception as exc:
                restarted = False
                self._control_message = f"Restart refused: {exc}"
            else:
                if restarted:
                    self._restart_pending_generation = self._snapshot.generation
                    self._set_enabled(self.restart_button, False)
                    self._control_message = (
                        "Restart requested; waiting for clean shutdown and a new generation"
                    )
                else:
                    self._control_message = (
                        "Restart refused: prior replay worker did not shut down cleanly"
                    )
            self._set_text(self.control_status_label, self._control_message)

        @QtCore.Slot()
        def request_stop(self) -> None:
            # Runtime adapters may provide a nonblocking request_stop operation.
            # The fallback issues the stop request with a zero join timeout so the
            # Qt main thread keeps servicing paint and polling events.
            request_stop = getattr(self.adapter, "request_stop", None)
            if callable(request_stop):
                request_stop()
            else:
                self.adapter.stop(timeout_s=0.0)
            self._control_message = "Stop requested"
            self._set_text(self.control_status_label, self._control_message)

        @QtCore.Slot(int)
        def _speed_changed(self, index: int) -> None:
            if index < 0:
                return
            speed = self.speed_selector.itemData(index)
            self.set_speed(speed)

        def set_speed(self, speed: float) -> None:
            """Request one of the four supported speeds through the adapter."""

            speed = validate_speed(speed)
            accepted = self.adapter.set_speed(speed)
            self._control_message = (
                "Speed change refused during a replay lifecycle transition"
                if accepted is False
                else f"Speed change requested: {speed:g}x"
            )
            self._set_text(self.control_status_label, self._control_message)

        def capture_screenshot_image(self) -> Any:
            """Return a detached QImage of the current real dashboard pixels."""

            application = QtWidgets.QApplication.instance()
            if application is None:
                raise RuntimeError("dashboard screenshot requires a QApplication")
            current_thread = QtCore.QThread.currentThread()
            application_thread = application.thread()
            if (
                current_thread != application_thread
                or self.thread() != application_thread
            ):
                raise RuntimeError(
                    "dashboard screenshot capture must run on the Qt application thread"
                )
            self._flush_plots(force=True)
            self.progress_bar.flush_pending_update()
            pixmap = self.grab()
            if pixmap.isNull():
                raise RuntimeError("dashboard screenshot grab returned a null pixmap")
            image = pixmap.toImage().copy()
            if image.isNull():
                raise RuntimeError("dashboard screenshot copy returned a null image")
            return image

        def save_screenshot(self, path: str | Path) -> Path:
            """Synchronously save a detached dashboard image as PNG."""

            destination = Path(path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            image = self.capture_screenshot_image()
            if not image.save(str(destination), "PNG"):
                raise RuntimeError(f"could not save dashboard screenshot to {destination}")
            return destination

        def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt API name
            self.poll_timer.stop()
            try:
                request_stop = getattr(self.adapter, "request_stop", None)
                if callable(request_stop):
                    request_stop()
                else:
                    self.adapter.stop(timeout_s=0.0)
            finally:
                event.accept()

    _dashboard_type = ReplayDashboard
    return ReplayDashboard


def _validate_snapshot_update(
    snapshot: Any,
    *,
    frame_rate_hz: float,
    prior_generation: int | None,
    prior_capacity: int,
    retained_prefix: tuple[Any, ...],
    retained_identity: tuple[int, ...],
) -> _ValidatedPlotUpdate:
    """Fully validate one snapshot without changing dashboard or Qt state."""

    _require_immutable_snapshot(snapshot)
    trace_samples = snapshot.trace_samples
    boundary = snapshot.processed_boundary
    if len(trace_samples) != boundary:
        raise ValueError("snapshot trace length must equal its processed boundary")

    starts_new_generation = prior_generation != snapshot.generation
    if starts_new_generation:
        if prior_generation is not None and snapshot.generation <= prior_generation:
            raise ValueError("plot generation must increase on restart")
        tail_start = 0
    else:
        if snapshot.total_frames != prior_capacity:
            raise ValueError("snapshot total_frames changed within a generation")
        prefix_length = len(retained_prefix)
        if len(trace_samples) < prefix_length:
            raise ValueError("snapshot trace truncated its validated prefix")
        # Compare cached integer identities without invoking a sample's equality
        # method. Retaining the accepted tuple prevents Python from reusing ids.
        candidate_identity = tuple(map(id, trace_samples[:prefix_length]))
        if candidate_identity != retained_identity:
            raise ValueError("snapshot altered its validated trace prefix")
        tail_start = prefix_length

    x_s: list[float] = []
    radar_hr_bpm: list[float] = []
    reference_pr_bpm: list[float] = []
    radar_br_bpm: list[float] = []
    reference_rr_bpm: list[float] = []
    for frame_index, sample in enumerate(
        trace_samples[tail_start:], start=tail_start
    ):
        values = _validate_trace_sample(
            sample,
            expected_boundary=frame_index + 1,
            generation=snapshot.generation,
        )
        x_s.append(frame_index / frame_rate_hz)
        radar_hr_bpm.append(values[0])
        reference_pr_bpm.append(values[1])
        radar_br_bpm.append(values[2])
        reference_rr_bpm.append(values[3])

    if trace_samples:
        if trace_samples[0].boundary != 1:
            raise ValueError("snapshot trace must begin at boundary 1")
        if trace_samples[-1].boundary != boundary:
            raise ValueError("snapshot trace endpoint must equal processed boundary")

    return _ValidatedPlotUpdate(
        starts_new_generation=starts_new_generation,
        capacity=snapshot.total_frames,
        trace_prefix=trace_samples,
        trace_identity=tuple(map(id, trace_samples)),
        tail_start=tail_start,
        x_s=tuple(x_s),
        radar_hr_bpm=tuple(radar_hr_bpm),
        reference_pr_bpm=tuple(reference_pr_bpm),
        radar_br_bpm=tuple(radar_br_bpm),
        reference_rr_bpm=tuple(reference_rr_bpm),
    )


def _require_immutable_snapshot(snapshot: Any) -> None:
    if snapshot is None:
        raise TypeError("adapter.latest_snapshot returned None")
    parameters = getattr(type(snapshot), "__dataclass_params__", None)
    if not is_dataclass(snapshot) or parameters is None or not parameters.frozen:
        raise TypeError("adapter.latest_snapshot must be a frozen dataclass")
    required = (
        "generation", "processed_boundary", "total_frames", "playback_state",
        "speed", "mode", "hr", "br", "quiet_state", "selected_bin",
        "selection_revision", "accumulation_stage", "recovery_reason",
        "measurement_window_age_frames", "analysis_lag_frames", "reference",
        "hr_reference_summary", "br_reference_summary", "coverage",
        "warning_messages", "trace_samples",
    )
    missing = [name for name in required if not hasattr(snapshot, name)]
    if missing:
        raise TypeError(f"immutable playback snapshot is missing: {', '.join(missing)}")
    if type(snapshot.processed_boundary) is not int or snapshot.processed_boundary < 0:
        raise ValueError("snapshot processed_boundary must be nonnegative")
    if type(snapshot.total_frames) is not int or snapshot.total_frames < 0:
        raise ValueError("snapshot total_frames must be nonnegative")
    if snapshot.processed_boundary > snapshot.total_frames:
        raise ValueError("snapshot processed_boundary exceeds total_frames")
    if type(snapshot.generation) is not int or snapshot.generation <= 0:
        raise ValueError("snapshot generation must be a positive exact integer")
    if (
        type(snapshot.playback_state) is not str
        or snapshot.playback_state not in _VALID_PLAYBACK_STATES
    ):
        raise ValueError("snapshot playback_state is invalid")
    if snapshot.playback_state == "preparing" and snapshot.processed_boundary != 0:
        raise ValueError("preparing snapshots are allowed only at boundary zero")
    if type(snapshot.mode) is not str or snapshot.mode not in _VALID_MODES:
        raise ValueError("snapshot mode is invalid")
    if snapshot.quiet_state not in _VALID_STATES:
        raise ValueError("snapshot quiet_state is invalid")
    if snapshot.selected_bin is not None and (
        type(snapshot.selected_bin) is not int or snapshot.selected_bin < 0
    ):
        raise ValueError("snapshot selected_bin must be nonnegative when present")
    _require_nonnegative_integer(snapshot.selection_revision, "selection_revision")
    _require_optional_nonnegative_integer(
        snapshot.measurement_window_age_frames, "measurement_window_age_frames"
    )
    _require_optional_nonnegative_integer(
        snapshot.analysis_lag_frames, "analysis_lag_frames"
    )
    if not isinstance(snapshot.accumulation_stage, str):
        raise TypeError("snapshot accumulation_stage must be a string")
    if not isinstance(snapshot.recovery_reason, str):
        raise TypeError("snapshot recovery_reason must be a string")
    selected_range_m = getattr(snapshot, "selected_range_m", None)
    if selected_range_m is not None:
        _require_positive_finite(selected_range_m, "selected_range_m")
    if type(snapshot.warning_messages) is not tuple or any(
        not isinstance(message, str) for message in snapshot.warning_messages
    ):
        raise TypeError("snapshot warning_messages must be an immutable string tuple")
    if type(snapshot.trace_samples) is not tuple:
        raise TypeError("snapshot trace_samples must be an immutable tuple")
    validate_speed(snapshot.speed)
    _validate_display_channel(snapshot.hr, "HR")
    _validate_display_channel(snapshot.br, "BR")
    _validate_coverage(snapshot.coverage, snapshot.processed_boundary)
    if snapshot.hr.state != snapshot.coverage.hr_state:
        raise ValueError("snapshot HR state disagrees with coverage state")
    if snapshot.br.state != snapshot.coverage.br_state:
        raise ValueError("snapshot BR state disagrees with coverage state")
    if snapshot.quiet_state != snapshot.coverage.quiet_state:
        raise ValueError("snapshot quiet state disagrees with coverage state")
    _validate_reference(snapshot.reference)
    _validate_reference_summary(snapshot.hr_reference_summary, "hr")
    _validate_reference_summary(snapshot.br_reference_summary, "br")


def _require_frozen_dataclass(
    value: Any, label: str, required_fields: tuple[str, ...]
) -> None:
    parameters = getattr(type(value), "__dataclass_params__", None)
    if not is_dataclass(value) or parameters is None or not parameters.frozen:
        raise TypeError(f"snapshot {label} must be a frozen dataclass")
    missing = [name for name in required_fields if not hasattr(value, name)]
    if missing:
        raise TypeError(
            f"snapshot {label} is missing fields: {', '.join(missing)}"
        )


def _validate_display_channel(channel: Any, label: str) -> None:
    _require_frozen_dataclass(
        channel, label, ("value_bpm", "state", "reason", "attempt_id")
    )
    if channel.state not in _VALID_STATES:
        raise ValueError(f"snapshot {label} channel state is invalid")
    value = _strict_nonnegative_optional(channel.value_bpm, f"{label} value")
    if channel.state == "accepted" and (value is None or value <= 0.0):
        raise ValueError(
            f"snapshot accepted {label} channel requires a positive finite value"
        )
    if channel.state == "missing" and value is not None:
        raise ValueError(f"snapshot missing {label} channel cannot carry a value")
    if not isinstance(channel.reason, str):
        raise TypeError(f"snapshot {label} channel reason must be a string")
    if channel.attempt_id is not None and not isinstance(channel.attempt_id, str):
        raise TypeError(f"snapshot {label} attempt_id must be a string when present")


def _validate_coverage(coverage: Any, processed_boundary: int) -> None:
    count_fields = (
        "denominator_frames",
        "hr_accepted_frames",
        "br_numeric_frames",
        "both_frames",
        "quiet_frames",
        "reference_pr_frames",
        "reference_pr_qualified_frames",
        "reference_rr_frames",
    )
    _require_frozen_dataclass(
        coverage, "coverage", (*count_fields, "hr_state", "br_state", "quiet_state")
    )
    for field in count_fields:
        _require_nonnegative_integer(getattr(coverage, field), f"coverage {field}")
    if coverage.denominator_frames != processed_boundary:
        raise ValueError("coverage denominator must equal processed boundary")
    for field in count_fields[1:]:
        if getattr(coverage, field) > coverage.denominator_frames:
            raise ValueError(f"coverage {field} exceeds its denominator")
    if coverage.reference_pr_qualified_frames > coverage.reference_pr_frames:
        raise ValueError("PI-qualified reference coverage exceeds PR coverage")
    if coverage.both_frames > min(
        coverage.hr_accepted_frames, coverage.br_numeric_frames
    ):
        raise ValueError("simultaneous coverage exceeds an individual channel")
    for field in ("hr_state", "br_state", "quiet_state"):
        if getattr(coverage, field) not in _VALID_STATES:
            raise ValueError(f"coverage {field} is invalid")


def _validate_reference(reference: Any) -> None:
    if reference is None:
        return
    _require_frozen_dataclass(
        reference,
        "reference",
        (
            "timestamp_s", "pr_bpm", "pi", "rr_bpm", "pr_present",
            "pr_qualified", "rr_present",
        ),
    )
    if reference.timestamp_s is not None and type(reference.timestamp_s) is not int:
        raise TypeError("reference timestamp_s must be an exact integer when present")
    pr_bpm = _strict_positive_optional(reference.pr_bpm, "reference PR")
    rr_bpm = _strict_positive_optional(reference.rr_bpm, "reference RR")
    pi = _strict_nonnegative_optional(reference.pi, "reference PI")
    for field in ("pr_present", "pr_qualified", "rr_present"):
        if type(getattr(reference, field)) is not bool:
            raise TypeError(f"reference {field} must be an exact boolean")
    if reference.pr_present != (pr_bpm is not None):
        raise ValueError("reference pr_present disagrees with PR value")
    if reference.rr_present != (rr_bpm is not None):
        raise ValueError("reference rr_present disagrees with RR value")
    if reference.pr_qualified and (pr_bpm is None or pi is None):
        raise ValueError("PI-qualified reference requires finite PR and PI")
    if reference.timestamp_s is None and (
        pr_bpm is not None or rr_bpm is not None or pi is not None
    ):
        raise ValueError("reference values require a source timestamp")


def _validate_reference_summary(summary: Any, expected_channel: str) -> None:
    if summary is None:
        return
    count_fields = (
        "expected_timestamp_count", "observed_count", "finite_count",
        "pr_qualified_count",
    )
    _require_frozen_dataclass(
        summary,
        f"{expected_channel} reference summary",
        (
            "channel", "frame_start", "frame_stop", "duration_s",
            *count_fields, "median_bpm",
        ),
    )
    if summary.channel != expected_channel:
        raise ValueError("reference summary channel disagrees with its display channel")
    _require_nonnegative_integer(summary.frame_start, "summary frame_start")
    _require_nonnegative_integer(summary.frame_stop, "summary frame_stop")
    if summary.frame_stop <= summary.frame_start:
        raise ValueError("reference summary frame bounds must be positive")
    _require_positive_finite(summary.duration_s, "summary duration_s")
    for field in count_fields:
        _require_nonnegative_integer(getattr(summary, field), f"summary {field}")
    if summary.observed_count > summary.expected_timestamp_count:
        raise ValueError("summary observed count exceeds expected count")
    if summary.finite_count > summary.observed_count:
        raise ValueError("summary finite count exceeds observed count")
    if summary.pr_qualified_count > summary.finite_count:
        raise ValueError("summary PI-qualified count exceeds finite count")
    _strict_positive_optional(summary.median_bpm, "summary median")


def _require_nonnegative_integer(value: Any, label: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"snapshot {label} must be a nonnegative exact integer")


def _require_optional_nonnegative_integer(value: Any, label: str) -> None:
    if value is not None:
        _require_nonnegative_integer(value, label)


def _validate_trace_sample(
    sample: Any,
    *,
    expected_boundary: int,
    generation: int,
) -> tuple[float, float, float, float]:
    """Validate one newly observed controller sample and return plot values."""

    parameters = getattr(type(sample), "__dataclass_params__", None)
    if not is_dataclass(sample) or parameters is None or not parameters.frozen:
        raise TypeError("snapshot trace samples must be frozen dataclasses")
    if type(sample.generation) is not int or sample.generation != generation:
        raise ValueError("plot trace generation disagrees with snapshot")
    if type(sample.boundary) is not int or sample.boundary != expected_boundary:
        raise ValueError("controller plot trace boundaries must be contiguous")
    if sample.hr_state not in _VALID_STATES or sample.br_state not in _VALID_STATES:
        raise ValueError("plot trace contains an unknown display state")
    if type(sample.reference_available) is not bool:
        raise TypeError("plot trace reference_available must be an exact boolean")

    radar_hr = _strict_positive_optional(sample.radar_hr_bpm, "radar HR")
    radar_br = _strict_positive_optional(sample.radar_br_bpm, "radar BR")
    reference_pr = _strict_positive_optional(sample.reference_pr_bpm, "reference PR")
    reference_rr = _strict_positive_optional(sample.reference_rr_bpm, "reference RR")
    if sample.hr_state == "accepted" and radar_hr is None:
        raise ValueError("accepted HR trace state requires a positive finite value")
    if sample.br_state == "accepted" and radar_br is None:
        raise ValueError("accepted BR trace state requires a positive finite value")
    if sample.hr_state == "missing" and radar_hr is not None:
        raise ValueError("missing HR trace state cannot carry a numeric value")
    if sample.br_state == "missing" and radar_br is not None:
        raise ValueError("missing BR trace state cannot carry a numeric value")
    if not sample.reference_available and (
        reference_pr is not None or reference_rr is not None
    ):
        raise ValueError("unavailable reference trace cannot carry numeric values")
    if sample.reference_available and reference_pr is None and reference_rr is None:
        raise ValueError("available reference trace requires PR or RR")

    return (
        radar_hr if sample.hr_state == "accepted" and radar_hr is not None else math.nan,
        reference_pr if sample.reference_available and reference_pr is not None else math.nan,
        radar_br if sample.br_state == "accepted" and radar_br is not None else math.nan,
        reference_rr if sample.reference_available and reference_rr is not None else math.nan,
    )


def _strict_positive_optional(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be positive and finite when present")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{label} must be positive and finite when present")
    return number


def _strict_nonnegative_optional(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be nonnegative and finite when present")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{label} must be nonnegative and finite when present")
    return number


def _require_positive_finite(value: Any, label: str) -> float:
    number = _strict_positive_optional(value, label)
    if number is None:
        raise ValueError(f"{label} must be positive and finite")
    return number


def _positive_finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _format_duration(seconds: float) -> str:
    minutes, remaining = divmod(max(0.0, float(seconds)), 60.0)
    return f"{int(minutes):02d}:{remaining:04.1f}"


def _format_frames(frames: int | None, frame_rate_hz: float) -> str:
    if frames is None:
        return "--"
    return f"{frames} frames ({frames / frame_rate_hz:.2f} s)"


def _format_coverage(numerator: int, denominator: int, frame_rate_hz: float) -> str:
    if denominator == 0:
        return "--"
    percentage = 100.0 * numerator / denominator
    return (
        f"{numerator / frame_rate_hz:.2f} / {denominator / frame_rate_hz:.2f} s "
        f"({percentage:.2f}%)"
    )


def _format_reference_summary(label: str, summary: Any) -> str:
    if summary is None:
        return f"{label} window summary - descriptive: --"
    median = "--" if _positive_finite(summary.median_bpm) is None else f"{summary.median_bpm:.1f}"
    quality = (
        f", PI-qualified {summary.pr_qualified_count}"
        if summary.channel == "hr"
        else ""
    )
    return (
        f"{label} window summary - descriptive: {median} | "
        f"{summary.duration_s:.1f} s, expected {summary.expected_timestamp_count}, "
        f"observed {summary.observed_count}, finite {summary.finite_count}{quality}"
    )


def _state_label(state: str) -> str:
    return {
        "running": "Running",
        "pausing": "Pausing...",
        "paused": "Paused",
        "complete": "Complete",
        "completed": "Complete",
        "stopped": "Stopped - partial",
        "failed": "Failed",
    }.get(state, str(state).replace("_", " ").title())


__all__ = [
    "ChannelPresentation",
    "DashboardDependencyError",
    "PlotSeries",
    "ReplayApplicationAdapter",
    "SUPPORTED_SPEEDS",
    "TraceSample",
    "create_dashboard",
    "format_channel",
    "run_dashboard",
    "validate_speed",
    "visible_plot_series",
]
