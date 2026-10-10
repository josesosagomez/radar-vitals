"""Native replay dashboard with lazy Qt imports.

The controller remains the sole owner of recording progress, coverage, and
publication state.  This module polls immutable :class:`PlaybackSnapshot`
values on the Qt main thread and sends control requests through a small
duck-typed application adapter.  Importing the module never imports Qt or
pyqtgraph, which keeps headless verification usable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol, runtime_checkable


SUPPORTED_SPEEDS = (0.5, 1.0, 2.0, 4.0)
FRAME_RATE_HZ = 20.0

RADAR_TEAL = "#1ab3a6"
REFERENCE_PURPLE = "#9b72cf"
ACCEPTED_GREEN = "#43c77b"
PRELIMINARY_AMBER = "#e5a84b"
HELD_RED = "#e26767"
MISSING_GREY = "#88919d"

_VALID_CHANNELS = {"hr", "br"}
_VALID_STATES = {"accepted", "preliminary", "held", "missing", "unresolved"}
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

    if not recording_name:
        raise ValueError("recording_name is required")
    if frame_rate_hz != FRAME_RATE_HZ:
        raise ValueError("the replay dashboard requires the recorded 20 Hz frame rate")
    if type(poll_interval_ms) is not int or not 1 <= poll_interval_ms <= 250:
        raise ValueError("poll_interval_ms must be an exact integer in [1, 250]")
    dashboard_class = _load_dashboard_type()
    return dashboard_class(
        adapter=adapter,
        recording_name=recording_name,
        frame_rate_hz=frame_rate_hz,
        poll_interval_ms=poll_interval_ms,
        parent=parent,
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

    QtCore, QtGui, QtWidgets, pg = _load_gui_dependencies()
    del QtCore, QtGui, pg
    application = QtWidgets.QApplication.instance()
    owns_application = application is None
    if application is None:
        application = QtWidgets.QApplication([])
    window = create_dashboard(
        adapter,
        recording_name=recording_name,
        frame_rate_hz=frame_rate_hz,
        poll_interval_ms=poll_interval_ms,
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
        ):
            super().__init__(parent)
            self.adapter = adapter
            self.recording_name = recording_name
            self.frame_rate_hz = frame_rate_hz
            self._snapshot: Any = None
            self._plot_generation: int | None = None
            self._last_trace_frame = -1
            self._hr_samples: list[TraceSample] = []
            self._br_samples: list[TraceSample] = []
            self._control_message = ""
            self._restart_pending_generation: int | None = None

            self.setObjectName("replayComparisonDashboard")
            self.setWindowTitle("Recorded radar / Masimo comparison")
            self.resize(1380, 900)
            self._build_ui()
            self.poll_timer = QtCore.QTimer(self)
            self.poll_timer.setInterval(poll_interval_ms)
            self.poll_timer.timeout.connect(self.poll_once)
            self.poll_timer.start()
            self.poll_once()

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
            self.hr_radar_curve = self.hr_plot.plot(
                pen=pg.mkPen(RADAR_TEAL, width=2), name="Radar accepted"
            )
            self.hr_reference_curve = self.hr_plot.plot(
                pen=pg.mkPen(REFERENCE_PURPLE, width=2), name="Masimo PR"
            )
            self.br_radar_curve = self.br_plot.plot(
                pen=pg.mkPen(RADAR_TEAL, width=2), name="Radar accepted"
            )
            self.br_reference_curve = self.br_plot.plot(
                pen=pg.mkPen(REFERENCE_PURPLE, width=2), name="Masimo RRp"
            )
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
            self.progress_bar = QtWidgets.QProgressBar()
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

        @QtCore.Slot()
        def poll_once(self) -> None:
            snapshot = self.adapter.latest_snapshot
            _require_immutable_snapshot(snapshot)
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
            self._update_plots(snapshot)

        def _update_header(self, snapshot: Any) -> None:
            state_text = _state_label(snapshot.playback_state)
            self.mode_label.setText(f"Mode: {snapshot.mode}")
            self.state_label.setText(f"State: {state_text} | {snapshot.speed:g}x")
            elapsed_s = snapshot.processed_boundary / self.frame_rate_hz
            total_s = snapshot.total_frames / self.frame_rate_hz
            self.time_label.setText(
                f"{_format_duration(elapsed_s)} / {_format_duration(total_s)}"
            )
            alignment_warning = (
                "Approximate reference alignment - exact radar frame-zero UTC "
                "was not recorded."
            )
            warnings = tuple(str(item) for item in snapshot.warning_messages if str(item))
            if alignment_warning not in warnings:
                warnings = (alignment_warning, *warnings)
            self.warning_label.setText("\n".join(f"Warning: {item}" for item in warnings))
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
            value_label.setText(presentation.value_text)
            status_label.setText(presentation.state_text)
            value_label.setStyleSheet(f"QLabel {{ color: {presentation.color}; }}")
            status_label.setStyleSheet(f"QLabel {{ color: {presentation.color}; }}")

        def _update_summaries(self, snapshot: Any) -> None:
            self.hr_summary_label.setText(
                _format_reference_summary("HR", snapshot.hr_reference_summary)
            )
            self.br_summary_label.setText(
                _format_reference_summary("BR", snapshot.br_reference_summary)
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
                self.coverage_labels[key].setText(
                    _format_coverage(numerator, denominator, self.frame_rate_hz)
                )

        def _update_status(self, snapshot: Any) -> None:
            selected_range = getattr(snapshot, "selected_range_m", None)
            range_text = (
                "--" if _positive_finite(selected_range) is None else f"{selected_range:.3f} m"
            )
            bin_text = "--" if snapshot.selected_bin is None else str(snapshot.selected_bin)
            self.status_values["stage"].setText(str(snapshot.accumulation_stage))
            self.status_values["bin"].setText(f"{bin_text} / {range_text}")
            self.status_values["recovery"].setText(str(snapshot.recovery_reason or "--"))
            self.status_values["age"].setText(
                _format_frames(snapshot.measurement_window_age_frames, self.frame_rate_hz)
            )
            self.status_values["lag"].setText(
                _format_frames(snapshot.analysis_lag_frames, self.frame_rate_hz)
            )
            self.status_values["revision"].setText(str(snapshot.selection_revision))

        def _update_controls(self, snapshot: Any) -> None:
            state = snapshot.playback_state
            terminal = state in {"complete", "completed", "stopped", "failed"}
            restart_pending = self._restart_pending_generation is not None
            stopping = bool(getattr(self.adapter, "stopping", False))
            if state == "paused":
                self.pause_button.setText("Resume")
                self.pause_button.setEnabled(True)
            elif state == "pausing":
                self.pause_button.setText("Pausing...")
                self.pause_button.setEnabled(False)
            else:
                self.pause_button.setText("Pause")
                self.pause_button.setEnabled(not terminal)
            if restart_pending or stopping:
                self.pause_button.setText(
                    "Stopping..." if stopping else "Restarting..."
                )
                self.pause_button.setEnabled(False)
            self.stop_button.setEnabled(not terminal)
            if restart_pending:
                # The visible snapshot remains the preserved completed pass
                # while the next generation is authorized and constructed.
                # Stop must remain available during that transition.
                self.stop_button.setEnabled(True)
            if stopping:
                self.stop_button.setEnabled(False)
            self.restart_button.setEnabled(
                state != "pausing" and not restart_pending and not stopping
            )

            speed = validate_speed(snapshot.speed)
            speed_index = self.speed_selector.findData(speed)
            self.speed_selector.blockSignals(True)
            if speed_index >= 0:
                self.speed_selector.setCurrentIndex(speed_index)
            self.speed_selector.blockSignals(False)
            self.speed_selector.setEnabled(
                not terminal and not restart_pending and not stopping
            )

            total = max(0, int(snapshot.total_frames))
            boundary = min(max(0, int(snapshot.processed_boundary)), total)
            self.progress_bar.setRange(0, max(1, total))
            self.progress_bar.setValue(boundary)
            suffix = ""
            if getattr(self.adapter, "failure", None) is not None:
                suffix = f"Replay failure: {self.adapter.failure}"
            self.control_status_label.setText(suffix or self._control_message)

        def _update_plots(self, snapshot: Any) -> None:
            if self._plot_generation != snapshot.generation:
                self._plot_generation = snapshot.generation
                self._last_trace_frame = -1
                self._hr_samples.clear()
                self._br_samples.clear()
            trace_samples = getattr(snapshot, "trace_samples", ())
            if trace_samples:
                self._append_controller_trace(snapshot, trace_samples)
            else:
                # Compatibility path for an initial/preparing snapshot. Running
                # application snapshots provide controller-owned trace_samples so
                # a short held interval cannot be skipped between GUI polls.
                frame_index = snapshot.processed_boundary - 1
                if frame_index > self._last_trace_frame:
                    reference = snapshot.reference
                    self._hr_samples.append(TraceSample(
                        frame_index=frame_index,
                        radar_value_bpm=snapshot.hr.value_bpm,
                        radar_state=snapshot.hr.state,
                        reference_value_bpm=None if reference is None else reference.pr_bpm,
                    ))
                    self._br_samples.append(TraceSample(
                        frame_index=frame_index,
                        radar_value_bpm=snapshot.br.value_bpm,
                        radar_state=snapshot.br.state,
                        reference_value_bpm=None if reference is None else reference.rr_bpm,
                    ))
                    self._last_trace_frame = frame_index

            hr = visible_plot_series(
                self._hr_samples, snapshot.processed_boundary, channel="hr"
            )
            br = visible_plot_series(
                self._br_samples, snapshot.processed_boundary, channel="br"
            )
            hr_x = [frame / self.frame_rate_hz for frame in hr.frame_indices]
            br_x = [frame / self.frame_rate_hz for frame in br.frame_indices]
            self.hr_radar_curve.setData(hr_x, hr.radar_bpm, connect="finite")
            self.hr_reference_curve.setData(hr_x, hr.reference_bpm, connect="finite")
            self.br_radar_curve.setData(br_x, br.radar_bpm, connect="finite")
            self.br_reference_curve.setData(br_x, br.reference_bpm, connect="finite")
            elapsed_s = snapshot.processed_boundary / self.frame_rate_hz
            visible_stop = max(30.0, elapsed_s)
            self.hr_plot.setXRange(0.0, visible_stop, padding=0.0)
            self.br_plot.setXRange(0.0, visible_stop, padding=0.0)

        def _append_controller_trace(
            self, snapshot: Any, trace_samples: Iterable[Any]
        ) -> None:
            previous_boundary = -1
            for sample in trace_samples:
                if sample.generation != snapshot.generation:
                    raise ValueError("plot trace generation disagrees with snapshot")
                boundary = sample.boundary
                if type(boundary) is not int or boundary < 1:
                    raise ValueError("plot trace boundary must be a positive exact integer")
                if boundary <= previous_boundary:
                    raise ValueError("controller plot trace boundaries must increase strictly")
                previous_boundary = boundary
                if boundary > snapshot.processed_boundary:
                    raise ValueError("controller plot trace contains future data")
                frame_index = boundary - 1
                if frame_index <= self._last_trace_frame:
                    continue
                reference_available = bool(sample.reference_available)
                self._hr_samples.append(TraceSample(
                    frame_index=frame_index,
                    radar_value_bpm=sample.radar_hr_bpm,
                    radar_state=sample.hr_state,
                    reference_value_bpm=(
                        sample.reference_pr_bpm if reference_available else None
                    ),
                ))
                self._br_samples.append(TraceSample(
                    frame_index=frame_index,
                    radar_value_bpm=sample.radar_br_bpm,
                    radar_state=sample.br_state,
                    reference_value_bpm=(
                        sample.reference_rr_bpm if reference_available else None
                    ),
                ))
                self._last_trace_frame = frame_index

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
                    self.pause_button.setText("Pausing...")
                    self.pause_button.setEnabled(False)
                    self._control_message = (
                        "Pause requested; waiting for controller acknowledgment"
                    )
            self.control_status_label.setText(self._control_message)

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
                    self.restart_button.setEnabled(False)
                    self._control_message = (
                        "Restart requested; waiting for clean shutdown and a new generation"
                    )
                else:
                    self._control_message = (
                        "Restart refused: prior replay worker did not shut down cleanly"
                    )
            self.control_status_label.setText(self._control_message)

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
            self.control_status_label.setText(self._control_message)

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
            self.control_status_label.setText(self._control_message)

        def save_screenshot(self, path: str | Path) -> Path:
            """Save pixels from this real widget for acceptance evidence."""

            destination = Path(path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            pixmap = self.grab()
            if pixmap.isNull() or not pixmap.save(str(destination), "PNG"):
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
        "warning_messages",
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
    trace_samples = getattr(snapshot, "trace_samples", ())
    if type(trace_samples) is not tuple:
        raise TypeError("snapshot trace_samples must be an immutable tuple")
    for sample in trace_samples:
        sample_parameters = getattr(type(sample), "__dataclass_params__", None)
        if not is_dataclass(sample) or sample_parameters is None or not sample_parameters.frozen:
            raise TypeError("snapshot trace samples must be frozen dataclasses")
    validate_speed(snapshot.speed)


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
