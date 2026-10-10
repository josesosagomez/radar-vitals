"""Production application lifecycle for calibration-gated motion replay.

The advanced orchestrator owns radar state and authentic motion attempt evidence.
The common application composer observes only durable replay events and immutable
snapshots, so passive reference values and Qt polling cannot affect radar science.
"""

from __future__ import annotations

import csv
import json
import math
import os
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from src.live_motion.evidence import AttemptEvidenceWriter

from .advanced import (
    AdvancedReplayAuthorization,
    AdvancedReplayOrchestrator,
    AdvancedReplayUnavailable,
    authorize_advanced_replay,
)
from .application import (
    AdvancedReplayCompositionSink,
    ReplaySnapshotComposer,
    scientific_source_status,
)
from .clock import SUPPORTED_SPEEDS, PlaybackClock
from .reference import PassiveReferenceTimeline
from .source import ProductionFrameDecoder, SequentialAdcSource
from .view_model import PlaybackSnapshot


@dataclass
class _AdvancedRunState:
    generation: int
    pass_dir: Path
    runtime: AdvancedReplayOrchestrator
    source: SequentialAdcSource
    motion_writer: AttemptEvidenceWriter
    sink: AdvancedReplayCompositionSink
    composer: ReplaySnapshotComposer
    authorization: AdvancedReplayAuthorization
    monitor: threading.Thread | None = None
    finalized: bool = False
    finalize_error: BaseException | None = None


class _RestartCancelled(RuntimeError):
    """Internal control-flow marker for a stop that wins during reauthorization."""


class AdvancedReplayApplicationManager:
    """Own motion replay passes behind the dashboard's nonblocking protocol."""

    def __init__(
        self,
        authorization: AdvancedReplayAuthorization,
        *,
        output_root: str | Path,
        reference_timeline: PassiveReferenceTimeline | None,
        frame_limit: int,
        initial_speed: float,
        initial_generation: int,
        shutdown_timeout_s: float,
    ):
        valid_frame_limit = (
            type(frame_limit) is int
            and 1 <= frame_limit <= authorization.session.frame_count
        )
        if not valid_frame_limit:
            raise ValueError("frame_limit lies outside the authorized recording")
        if type(initial_generation) is not int or initial_generation <= 0:
            raise ValueError("initial_generation must be a positive exact integer")
        if (
            isinstance(initial_speed, bool)
            or not isinstance(initial_speed, (int, float))
            or not math.isfinite(float(initial_speed))
            or float(initial_speed) not in SUPPORTED_SPEEDS
        ):
            raise ValueError("initial_speed must be one of 0.5, 1, 2, or 4")
        if not math.isfinite(shutdown_timeout_s) or shutdown_timeout_s <= 0:
            raise ValueError("shutdown_timeout_s must be finite and positive")

        # The manager itself is also a public construction surface.  Re-run the
        # physical gate before creating even the output root, then do it again
        # for each pass immediately before pass artifacts and the ADC source.
        refreshed = _refresh_authorization(authorization)
        self.authorization = refreshed
        self.spec = refreshed.session
        self.output_root = Path(output_root)
        self.reference_timeline = reference_timeline
        self.frame_limit = frame_limit
        self.initial_speed = float(initial_speed)
        self.shutdown_timeout_s = float(shutdown_timeout_s)

        self._lock = threading.Lock()
        self._transition: threading.Thread | None = None
        self._stop_thread: threading.Thread | None = None
        self._stopping = False
        self._transition_error: BaseException | None = None
        self._run = self._build_run(initial_generation, self.initial_speed)

    @property
    def latest_snapshot(self) -> PlaybackSnapshot:
        with self._lock:
            run = self._run
        return run.composer.latest_snapshot

    @property
    def generation(self) -> int:
        with self._lock:
            return self._run.generation

    @property
    def pass_dir(self) -> Path:
        with self._lock:
            return self._run.pass_dir

    @property
    def current_run(self) -> AdvancedReplayOrchestrator:
        with self._lock:
            return self._run.runtime

    @property
    def ended(self) -> bool:
        with self._lock:
            transition_active = (
                self._transition is not None and self._transition.is_alive()
            )
            return not transition_active and self._run.runtime.ended

    @property
    def clean_shutdown(self) -> bool:
        with self._lock:
            run = self._run
            transition_active = (
                self._transition is not None and self._transition.is_alive()
            )
            return (
                not transition_active
                and run.finalized
                and run.runtime.clean_shutdown
                and run.finalize_error is None
                and self._transition_error is None
            )

    @property
    def restart_in_progress(self) -> bool:
        with self._lock:
            return self._transition is not None and self._transition.is_alive()

    @property
    def stopping(self) -> bool:
        with self._lock:
            return self._stopping

    @property
    def failure(self) -> BaseException | None:
        with self._lock:
            run = self._run
            return self._transition_error or run.runtime.failure or run.finalize_error

    def _record_lifecycle_failure_locked(self, exc: BaseException) -> None:
        """Keep the first lifecycle failure as the causal diagnostic."""

        if self._transition_error is None:
            self._transition_error = exc

    def start(self) -> None:
        with self._lock:
            run = self._run
        run.runtime.start()
        monitor = threading.Thread(
            target=self._monitor_run,
            args=(run,),
            name=f"ReplayMotionFinalize-g{run.generation}",
            daemon=True,
        )
        run.monitor = monitor
        monitor.start()

    def request_pause(self) -> bool:
        with self._lock:
            if not self._controls_available_locked():
                return False
            self._run.runtime.request_pause()
            return True

    def resume(self) -> bool:
        with self._lock:
            if not self._controls_available_locked():
                return False
            self._run.runtime.resume()
            return True

    def set_speed(self, speed: float) -> bool:
        with self._lock:
            if not self._controls_available_locked():
                return False
            self._run.runtime.set_speed(speed)
            return True

    def _controls_available_locked(self) -> bool:
        """Check control routing while holding the generation-transition lock."""

        return bool(
            not self._stopping
            and not (
                self._transition is not None and self._transition.is_alive()
            )
            and not self._run.runtime.ended
        )

    def restart(self) -> bool:
        """Request a new pass only after the current pass finalizes cleanly."""

        with self._lock:
            if self._stopping:
                return False
            if self._transition is not None and self._transition.is_alive():
                return False
            previous = self._run
            self._transition_error = None
            transition = threading.Thread(
                target=self._restart_worker,
                args=(previous,),
                name="ReplayMotionRestart",
                daemon=True,
            )
            self._transition = transition
            transition.start()
        return True

    def request_stop(self) -> None:
        """Request a bounded stop without blocking the Qt main thread."""

        with self._lock:
            if self._stop_thread is not None and self._stop_thread.is_alive():
                return
            self._stopping = True
            stop_thread = threading.Thread(
                target=self._stop_worker,
                name="ReplayMotionStop",
                daemon=True,
            )
            self._stop_thread = stop_thread
            stop_thread.start()

    def stop(self, timeout_s: float | None = None) -> None:
        with self._lock:
            self._stopping = True
            run = self._run
            transition = self._transition
        run.runtime.stop(timeout_s=timeout_s)
        wait_s = self.shutdown_timeout_s + 1.0 if timeout_s is None else float(timeout_s)
        if transition is not None and transition is not threading.current_thread():
            transition.join(timeout=max(0.0, wait_s))
            if transition.is_alive():
                with self._lock:
                    self._record_lifecycle_failure_locked(TimeoutError(
                        "advanced replay restart did not stop within the bounded timeout"
                    ))
        with self._lock:
            latest = self._run
        if latest is not run:
            latest.runtime.stop(timeout_s=timeout_s)
        if run.monitor is not None:
            run.monitor.join(timeout=max(0.0, wait_s))
            if run.monitor.is_alive():
                with self._lock:
                    self._record_lifecycle_failure_locked(TimeoutError(
                        "advanced replay finalization did not finish within the bounded timeout"
                    ))
        if latest is not run and latest.monitor is not None:
            latest.monitor.join(timeout=max(0.0, wait_s))
            if latest.monitor.is_alive():
                with self._lock:
                    self._record_lifecycle_failure_locked(TimeoutError(
                        "advanced replay finalization did not finish within the bounded timeout"
                    ))

    def _restart_worker(self, previous: _AdvancedRunState) -> None:
        next_run: _AdvancedRunState | None = None
        try:
            previous.runtime.stop(timeout_s=self.shutdown_timeout_s + 1.0)
            if previous.monitor is not None:
                previous.monitor.join(timeout=self.shutdown_timeout_s + 1.0)
            if (
                not previous.runtime.clean_shutdown
                or not previous.finalized
                or previous.finalize_error is not None
            ):
                with self._lock:
                    self._record_lifecycle_failure_locked(RuntimeError(
                        "prior advanced replay pass did not finalize cleanly"
                    ))
                return
            with self._lock:
                if self._stopping or self._run is not previous:
                    return
            next_run = self._build_run(
                previous.generation + 1,
                self.initial_speed,
                cancel_if_stopping=True,
            )
            with self._lock:
                if self._stopping or self._run is not previous:
                    next_run.sink.write_playback_event({
                        "kind": "restart_cancelled_before_start",
                        "boundary": 0,
                        "generation": next_run.generation,
                    })
                    # This pass never opened and verified its registered ADC.
                    # Preserve the cancellation stream as an explicitly
                    # incomplete pass; a source-less manifest would look like a
                    # finalized scientific replay to downstream tooling.
                    self._record_lifecycle_failure_locked(RuntimeError(
                        "restart was cancelled after creating an incomplete pass"
                    ))
                    return
                next_run.runtime.start()
                self._run = next_run
            monitor = threading.Thread(
                target=self._monitor_run,
                args=(next_run,),
                name=f"ReplayMotionFinalize-g{next_run.generation}",
                daemon=True,
            )
            next_run.monitor = monitor
            monitor.start()
        except _RestartCancelled:
            return
        except BaseException as exc:
            if next_run is not None:
                try:
                    next_run.runtime.stop(timeout_s=self.shutdown_timeout_s + 1.0)
                except BaseException:
                    pass
            with self._lock:
                self._record_lifecycle_failure_locked(exc)

    def _stop_worker(self) -> None:
        try:
            with self._lock:
                run = self._run
                transition = self._transition
            run.runtime.stop(timeout_s=self.shutdown_timeout_s + 1.0)
            if transition is not None and transition is not threading.current_thread():
                transition.join(timeout=self.shutdown_timeout_s + 1.0)
                if transition.is_alive():
                    with self._lock:
                        self._record_lifecycle_failure_locked(TimeoutError(
                            "advanced replay restart did not stop within the bounded timeout"
                        ))
                    return
            with self._lock:
                latest = self._run
            if latest is not run:
                latest.runtime.stop(timeout_s=self.shutdown_timeout_s + 1.0)
            states = (run,) if latest is run else (run, latest)
            for state in states:
                monitor = state.monitor
                if monitor is None:
                    continue
                monitor.join(timeout=self.shutdown_timeout_s + 1.0)
                if monitor.is_alive():
                    with self._lock:
                        self._record_lifecycle_failure_locked(TimeoutError(
                            "advanced replay finalization did not finish within the bounded timeout"
                        ))
                    return
        except BaseException as exc:
            with self._lock:
                self._record_lifecycle_failure_locked(exc)

    def _monitor_run(self, run: _AdvancedRunState) -> None:
        while not run.runtime.ended:
            time.sleep(0.02)
        run.runtime.stop(timeout_s=self.shutdown_timeout_s + 1.0)
        try:
            integrity = run.source.integrity
            run.sink.finalize(
                source_integrity=None if integrity is None else asdict(integrity)
            )
            run.finalized = True
        except BaseException as exc:
            run.finalize_error = exc

    def _build_run(
        self,
        generation: int,
        speed: float,
        *,
        cancel_if_stopping: bool = False,
    ) -> _AdvancedRunState:
        authorization = _refresh_authorization(self.authorization)
        if cancel_if_stopping:
            with self._lock:
                if self._stopping:
                    raise _RestartCancelled(
                        "stop requested while restart authorization was pending"
                    )
        spec = authorization.session
        self.output_root.mkdir(parents=True, exist_ok=True)
        pass_dir = self.output_root / f"pass_{generation:04d}"
        pass_dir.mkdir(exist_ok=False)
        calibration_snapshot = pass_dir / "development_calibration_snapshot.json"
        _write_exact_bytes(
            calibration_snapshot,
            authorization.calibration_record_bytes,
        )
        _write_run_metadata(
            pass_dir / "run_metadata.json",
            authorization,
            generation=generation,
            frame_limit=self.frame_limit,
            reference_timeline=self.reference_timeline,
            calibration_snapshot=calibration_snapshot.name,
        )

        config = authorization.config
        source = SequentialAdcSource(
            spec.adc_path,
            ProductionFrameDecoder(config["profile"]),
            expected_sha256=spec.registered_adc_sha256,
            expected_frame_count=spec.frame_count,
        )
        motion_writer = AttemptEvidenceWriter(
            pass_dir,
            authorization.config_sha256,
            spec.registered_adc_sha256,
            authorization.calibration_sha256,
        )
        _initialize_native_motion_csvs(motion_writer)
        sink = AdvancedReplayCompositionSink(pass_dir)
        runtime = AdvancedReplayOrchestrator.from_authorization(
            authorization,
            source=source,
            clock=PlaybackClock(self.spec.frame_rate_hz, speed=speed),
            frame_limit=self.frame_limit,
            evidence_writer=motion_writer,
            event_sink=sink,
            generation=generation,
            shutdown_timeout_s=self.shutdown_timeout_s,
            snapshot_observer=sink.observe_snapshot,
        )
        composer = ReplaySnapshotComposer.for_advanced(
            spec,
            initial_snapshot=runtime.latest_snapshot,
            frame_limit=self.frame_limit,
            reference_timeline=self.reference_timeline,
        )
        sink.attach_composer(composer)
        return _AdvancedRunState(
            generation,
            pass_dir,
            runtime,
            source,
            motion_writer,
            sink,
            composer,
            authorization,
        )


def create_advanced_replay_application(
    authorization: AdvancedReplayAuthorization,
    *,
    output_root: str | Path,
    reference_timeline: PassiveReferenceTimeline | None = None,
    frame_limit: int | None = None,
    initial_speed: float = 1.0,
    initial_generation: int = 1,
    shutdown_timeout_s: float = 5.0,
) -> AdvancedReplayApplicationManager:
    """Create a production motion application after rechecking physical gates.

    No dependency-injection surface is exposed here.  Synthetic research tests
    construct :class:`AdvancedReplayOrchestrator` directly and cannot unlock the
    CLI or this production factory.
    """

    if not isinstance(authorization, AdvancedReplayAuthorization):
        raise TypeError("authorization must be an AdvancedReplayAuthorization")
    limit = authorization.session.frame_count if frame_limit is None else frame_limit
    return AdvancedReplayApplicationManager(
        authorization,
        output_root=output_root,
        reference_timeline=reference_timeline,
        frame_limit=limit,
        initial_speed=initial_speed,
        initial_generation=initial_generation,
        shutdown_timeout_s=shutdown_timeout_s,
    )


def _write_run_metadata(
    path: Path,
    authorization: AdvancedReplayAuthorization,
    *,
    generation: int,
    frame_limit: int,
    reference_timeline: PassiveReferenceTimeline | None,
    calibration_snapshot: str,
) -> None:
    spec = authorization.session
    metadata: dict[str, Any] = {
        "schema": "replay_compare_motion_outer_v1",
        "capture_id": spec.capture_id,
        "capture_key": spec.capture_key,
        "source_kind": spec.source_kind,
        "registered_adc_sha256": spec.registered_adc_sha256,
        "metadata_sha256": spec.metadata_sha256,
        "warmup_sha256": spec.warmup_sha256,
        "original_config_sha256": spec.original_config_sha256,
        "effective_config_sha256": spec.effective_config_sha256,
        "effective_config": spec.effective_config,
        "motion_config_sha256": authorization.config_sha256,
        "motion_config": authorization.config,
        "calibration_record_path": str(authorization.calibration_record_path),
        "calibration_sha256": authorization.calibration_sha256,
        "calibration_record": authorization.calibration_record,
        "calibration_snapshot": calibration_snapshot,
        "generation": generation,
        "mode": "motion",
        "frame_limit": frame_limit,
        "frame_rate_hz": spec.frame_rate_hz,
        "validity_provenance": authorization.validity_provenance,
        "validity_assumed": spec.validity_assumed,
        "reference_is_passive": True,
        "reference_source": (
            None
            if reference_timeline is None
            else asdict(reference_timeline.source)
        ),
        "source_commit": _source_commit(),
        "scientific_source_status": scientific_source_status(),
        "random_seed": None,
        "randomness": "none",
        "anchor_utc_us": spec.anchor_utc_us,
        "time_origin_id": spec.time_origin_id,
        "time_origin_approximate": spec.time_origin_approximate,
        "geometry": {
            "num_adc_samples": spec.num_adc_samples,
            "num_rx": spec.num_rx,
            "num_chirps_per_frame": spec.num_chirps_per_frame,
            "frame_bytes": spec.frame_bytes,
            "iq_swap": spec.iq_swap,
            "range_resolution_m": spec.range_resolution_m,
            "range_bias_m": spec.range_bias_m,
        },
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(metadata, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(temporary, path)


def _refresh_authorization(
    authorization: AdvancedReplayAuthorization,
) -> AdvancedReplayAuthorization:
    """Re-run every physical/session gate and retain an identical snapshot."""

    refreshed = authorize_advanced_replay(
        authorization.session,
        authorization.config,
        allow_legacy_validity_assumption=True,
    )
    identity = (
        refreshed.session == authorization.session
        and refreshed.config_sha256 == authorization.config_sha256
        and refreshed.calibration_sha256 == authorization.calibration_sha256
        and refreshed.calibration_record_path == authorization.calibration_record_path
        and refreshed.calibration_record_bytes == authorization.calibration_record_bytes
        and refreshed.validity_provenance == authorization.validity_provenance
        and refreshed.source_kind == authorization.source_kind
    )
    if not identity:
        raise AdvancedReplayUnavailable(
            "advanced replay authorization changed after physical preflight"
        )
    return refreshed


def _write_exact_bytes(path: Path, content: bytes) -> None:
    """Persist one immutable, hash-authorized input snapshot without rewriting."""

    with path.open("xb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _initialize_native_motion_csvs(writer: AttemptEvidenceWriter) -> None:
    """Create verifier-readable native indexes even when no job executes."""

    controller_fields = (
        "kind", "reason", "frame_index", "epoch", "job_id",
        "selected_bin", "selection_revision", "signal_frame_stop",
    )
    for path, fields in (
        (writer.csv_path, writer.CSV_FIELDS),
        (writer.events_path, controller_fields),
    ):
        with path.open("x", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=fields, lineterminator="\n").writeheader()
            handle.flush()
            os.fsync(handle.fileno())


def _source_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("cannot identify replay software commit") from exc
    commit = completed.stdout.strip().lower()
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise RuntimeError("git returned an invalid replay software commit")
    return commit


__all__ = [
    "AdvancedReplayApplicationManager",
    "create_advanced_replay_application",
]
