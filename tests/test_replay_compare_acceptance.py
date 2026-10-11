"""Pure validation tests for the native replay acceptance driver."""

from __future__ import annotations

import copy
import hashlib
import math
from pathlib import Path

import pytest

from scripts.run_replay_compare_acceptance import (
    BoundedScreenshotCaptures,
    finish_screenshot_captures_after_loop,
    memory_observation,
    process_performance_errors,
)


SPANS = (
    "parent_copy_elapsed_s",
    "parent_send_elapsed_s",
    "parent_wait_receive_elapsed_s",
    "child_materialization_elapsed_s",
    "child_callback_elapsed_s",
    "process_round_trip_elapsed_s",
)


def _analysis_row(job_id: str, sequence: int) -> dict:
    row = {
        "kind": "analysis",
        "job_id": job_id,
        "process_sequence": sequence,
        **{name: 0.01 for name in SPANS},
        "analysis_elapsed_s": 0.05,
        "process_control_bytes": 65_536,
        "process_result_bytes": 16_777_216,
        "fatal_backend_error": None,
    }
    row["process_round_trip_elapsed_s"] = 0.04
    return row


def test_process_performance_accepts_complete_ordered_rows_at_inclusive_limits():
    performance = [
        {"kind": "frame", "boundary": 1},
        _analysis_row("attempt-a", 1),
        _analysis_row("attempt-b", 2),
    ]

    assert process_performance_errors(
        performance, ["attempt-a", "attempt-b"]
    ) == []


@pytest.mark.parametrize(
    ("performance", "attempt_ids", "message"),
    [
        ([], ["attempt-a"], "does not match every durable attempt"),
        ([], [], "production analysis process telemetry is missing"),
        (
            [_analysis_row("attempt-a", 1)],
            ["attempt-a", "attempt-a"],
            "does not match every durable attempt",
        ),
        (
            [_analysis_row("attempt-b", 1), _analysis_row("attempt-a", 2)],
            ["attempt-a", "attempt-b"],
            "does not match every durable attempt",
        ),
        (
            [_analysis_row("attempt-a", 1), _analysis_row("attempt-a", 2)],
            ["attempt-a", "attempt-b"],
            "does not match every durable attempt",
        ),
    ],
)
def test_process_performance_rejects_missing_duplicate_or_reordered_binding(
    performance, attempt_ids, message
):
    assert any(
        message in error
        for error in process_performance_errors(performance, attempt_ids)
    )


@pytest.mark.parametrize("sequence", [None, True, 1.0, 0, 2, "1"])
def test_process_performance_rejects_missing_coercive_or_discontinuous_sequence(
    sequence,
):
    row = _analysis_row("attempt-a", 1)
    row["process_sequence"] = sequence

    assert "analysis process sequence is missing or discontinuous" in (
        process_performance_errors([row], ["attempt-a"])
    )


@pytest.mark.parametrize("span", SPANS)
@pytest.mark.parametrize(
    "value",
    [None, True, "0.1", -0.01, math.nan, math.inf, -math.inf],
)
def test_process_performance_rejects_missing_nonfinite_or_negative_span(
    span, value
):
    row = _analysis_row("attempt-a", 1)
    row[span] = value

    assert f"analysis process span {span} is missing or invalid" in (
        process_performance_errors([row], ["attempt-a"])
    )


@pytest.mark.parametrize(
    "value", [None, True, "0.1", -0.01, math.nan, math.inf, -math.inf]
)
def test_process_performance_rejects_invalid_total_analysis_elapsed(value):
    row = _analysis_row("attempt-a", 1)
    row["analysis_elapsed_s"] = value

    assert "analysis process span analysis_elapsed_s is missing or invalid" in (
        process_performance_errors([row], ["attempt-a"])
    )


def test_process_performance_rejects_impossible_nested_elapsed_spans():
    analysis_too_short = _analysis_row("attempt-a", 1)
    analysis_too_short["analysis_elapsed_s"] = 0.03
    round_trip_too_short = _analysis_row("attempt-a", 1)
    round_trip_too_short["process_round_trip_elapsed_s"] = 0.02

    assert "analysis process durations have impossible nesting" in (
        process_performance_errors([analysis_too_short], ["attempt-a"])
    )
    assert "analysis process durations have impossible nesting" in (
        process_performance_errors([round_trip_too_short], ["attempt-a"])
    )


def test_process_performance_nesting_allows_only_documented_float_tolerance():
    row = _analysis_row("attempt-a", 1)
    parent_sum = sum(row[name] for name in SPANS[:3])
    row["process_round_trip_elapsed_s"] = parent_sum - 0.5e-9
    row["analysis_elapsed_s"] = row["process_round_trip_elapsed_s"] - 0.5e-9

    assert process_performance_errors([row], ["attempt-a"]) == []


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("process_control_bytes", 65_536),
        ("process_result_bytes", 16_777_216),
    ],
)
@pytest.mark.parametrize("value_kind", ["one", "limit"])
def test_process_performance_byte_bounds_are_positive_and_inclusive(
    field, limit, value_kind
):
    row = _analysis_row("attempt-a", 1)
    row[field] = 1 if value_kind == "one" else limit

    assert process_performance_errors([row], ["attempt-a"]) == []


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("process_control_bytes", 65_536),
        ("process_result_bytes", 16_777_216),
    ],
)
@pytest.mark.parametrize("value", [None, True, 1.0, 0, -1])
def test_process_performance_rejects_missing_coercive_or_nonpositive_byte_count(
    field, limit, value
):
    del limit
    row = _analysis_row("attempt-a", 1)
    row[field] = value

    assert f"analysis process byte count {field} is outside its inclusive bound" in (
        process_performance_errors([row], ["attempt-a"])
    )


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("process_control_bytes", 65_536),
        ("process_result_bytes", 16_777_216),
    ],
)
def test_process_performance_rejects_byte_count_above_inclusive_limit(field, limit):
    row = _analysis_row("attempt-a", 1)
    row[field] = limit + 1

    assert f"analysis process byte count {field} is outside its inclusive bound" in (
        process_performance_errors([row], ["attempt-a"])
    )


@pytest.mark.parametrize("value", ["missing", False, 0, "", "child exited"])
def test_process_performance_requires_exact_none_fatal_backend_error(value):
    row = _analysis_row("attempt-a", 1)
    if value == "missing":
        row.pop("fatal_backend_error")
    else:
        row["fatal_backend_error"] = value

    assert "analysis fatal-error field is missing or reports a failure" in (
        process_performance_errors([row], ["attempt-a"])
    )


def test_process_performance_validation_does_not_mutate_artifact_rows():
    rows = [_analysis_row("attempt-a", 1)]
    before = copy.deepcopy(rows)

    process_performance_errors(rows, ["attempt-a"])

    assert rows == before


def test_memory_observation_filters_generation_and_reports_diagnostic_slope():
    samples = [
        {
            "generation": 1,
            "boundary": 9_999,
            "monotonic_s": -1.0,
            "child_current_rss_bytes": 999,
        },
        {
            "generation": 2,
            "boundary": 1_799,
            "monotonic_s": -0.5,
            "child_current_rss_bytes": 888,
        },
        {
            "generation": 2,
            "boundary": 1_800,
            "monotonic_s": -0.25,
            "child_current_rss_bytes": None,
        },
        {
            "generation": 2,
            "boundary": 1_800,
            "monotonic_s": -0.1,
            "child_current_rss_bytes": 123.0,
        },
    ]
    samples.extend(
        {
            "generation": 2,
            "boundary": 1_800 + 10 * index,
            "monotonic_s": float(index),
            "child_current_rss_bytes": 100 + 10 * index,
        }
        for index in range(8)
    )

    observation = memory_observation(
        samples, "child_current_rss_bytes", generation=2
    )

    assert observation["peak_bytes"] == 999
    assert observation["steady"] == {
        "first_boundary": 1_800,
        "last_boundary": 1_870,
        "early_quarter_median_bytes": 105.0,
        "late_quarter_median_bytes": 165.0,
        "late_minus_early_bytes": 60.0,
        "late_quarter_endpoint_bytes_per_s": 10.0,
        "interpretation": (
            "Observed memory; inspect alongside bounded ownership tests."
        ),
    }


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class _FakeImage:
    def __init__(
        self,
        width=40,
        height=24,
        *,
        size_in_bytes=3840,
        null=False,
        save_result=True,
        events=None,
        name="image",
    ):
        self._width = width
        self._height = height
        self._size_in_bytes = size_in_bytes
        self._null = null
        self._save_result = save_result
        self._events = [] if events is None else events
        self._name = name

    def isNull(self):
        return self._null

    def width(self):
        return self._width

    def height(self):
        return self._height

    def sizeInBytes(self):
        return self._size_in_bytes

    def devicePixelRatio(self):
        return 2.5

    def format(self):
        return 7

    def save(self, path, image_format):
        self._events.append(("save", self._name, Path(path).name, image_format))
        if not self._save_result:
            return False
        Path(path).write_bytes(PNG_SIGNATURE + self._name.encode("ascii"))
        return True


def _clock():
    values = iter(index / 10.0 for index in range(100))
    return lambda: next(values)


@pytest.mark.parametrize(
    "filenames,expected_error",
    [
        (
            {"during": "../escape.png", "final": "dashboard_final.png"},
            ValueError,
        ),
        (
            {"during": "same.png", "final": "same.png"},
            ValueError,
        ),
    ],
)
def test_screenshot_destination_preflight_rejects_escape_or_duplicate(
    tmp_path, monkeypatch, filenames, expected_error
):
    monkeypatch.setattr(BoundedScreenshotCaptures, "_slot_filenames", filenames)
    with pytest.raises(expected_error):
        BoundedScreenshotCaptures(tmp_path.resolve())


def test_screenshot_preflight_requires_already_resolved_output_root(tmp_path):
    unresolved = tmp_path / ".." / tmp_path.name
    assert unresolved != unresolved.resolve()
    with pytest.raises(ValueError, match="already be resolved"):
        BoundedScreenshotCaptures(unresolved)


def test_screenshot_destination_and_admission_fail_before_any_gui_work(tmp_path):
    captures = BoundedScreenshotCaptures(tmp_path.resolve(), clock=_clock())
    gui_calls = []

    def ui_assertion():
        gui_calls.append("thread")

    def capture_image():
        gui_calls.append("capture")
        return _FakeImage()

    with pytest.raises(ValueError, match="unknown screenshot slot"):
        captures.capture("unknown", capture_image, assert_ui_thread=ui_assertion)
    assert gui_calls == []

    during_path = tmp_path / "dashboard_during.png"
    during_path.write_bytes(b"preexisting")
    with pytest.raises(FileExistsError):
        captures.capture("during", capture_image, assert_ui_thread=ui_assertion)
    assert gui_calls == []
    during_path.unlink()

    captures.capture("during", capture_image, assert_ui_thread=ui_assertion)
    assert gui_calls == ["thread", "capture"]
    gui_calls.clear()
    with pytest.raises(RuntimeError, match="already admitted"):
        captures.capture("during", capture_image, assert_ui_thread=ui_assertion)
    assert gui_calls == []
    assert not during_path.exists()
    assert captures.records()[0]["completion_status"] == "not_started"
    assert "png_sha256" not in captures.records()[0]


def test_screenshot_capture_is_bounded_and_completion_hashes_actual_png_bytes(
    tmp_path,
):
    events = []
    captures = BoundedScreenshotCaptures(tmp_path.resolve(), clock=_clock())
    during = _FakeImage(size_in_bytes=100, events=events, name="during")
    final = _FakeImage(size_in_bytes=200, events=events, name="final")
    thread_checks = []

    for slot, image in (("during", during), ("final", final)):
        captures.capture(
            slot,
            lambda image=image: image,
            assert_ui_thread=lambda: thread_checks.append(("capture", slot)),
        )

    assert captures.retained_bytes == 300
    assert captures.retained_bytes_peak == 300
    assert all(not Path(row["destination"]).exists() for row in captures.records())
    assert all(row["completion_status"] == "not_started" for row in captures.records())

    def load_image(path):
        events.append(("load", Path(path).name))
        return _FakeImage(width=40, height=24)

    completed, errors = captures.encode_and_verify(
        load_image,
        assert_ui_thread=lambda: events.append(("ui", "encode")),
    )

    assert errors == []
    assert completed == [
        str((tmp_path / "dashboard_during.png").resolve()),
        str((tmp_path / "dashboard_final.png").resolve()),
    ]
    assert captures.retained_bytes == 0
    assert captures.retained_bytes_peak == 300
    records = captures.records()
    for record in records:
        png_bytes = Path(record["destination"]).read_bytes()
        assert png_bytes.startswith(PNG_SIGNATURE)
        assert record["completion_status"] == "complete"
        assert record["png_bytes"] == len(png_bytes)
        assert record["png_sha256"] == hashlib.sha256(png_bytes).hexdigest()
        assert (record["reopened_width"], record["reopened_height"]) == (40, 24)

    before = list(events)
    assert captures.encode_and_verify(
        lambda _path: pytest.fail("idempotent completion must not reload"),
        assert_ui_thread=lambda: pytest.fail("idempotent completion must not touch Qt"),
    ) == (completed, [])
    assert events == before


def test_screenshot_encode_failure_is_retained_and_releases_both_images(tmp_path):
    captures = BoundedScreenshotCaptures(tmp_path.resolve(), clock=_clock())
    captures.capture(
        "during",
        lambda: _FakeImage(size_in_bytes=100, save_result=False, name="during"),
        assert_ui_thread=lambda: None,
    )
    captures.capture(
        "final",
        lambda: _FakeImage(size_in_bytes=200, name="final"),
        assert_ui_thread=lambda: None,
    )

    completed, errors = captures.encode_and_verify(
        lambda _path: _FakeImage(), assert_ui_thread=lambda: None
    )

    assert completed == [str((tmp_path / "dashboard_final.png").resolve())]
    assert any("PNG encoding returned false" in error for error in errors)
    assert captures.retained_bytes == 0
    records = captures.records()
    assert records[0]["completion_status"] == "failed"
    assert records[0]["errors"]
    assert records[1]["completion_status"] == "complete"
    assert not (tmp_path / "dashboard_during.png").exists()


def test_screenshot_capture_failure_is_recorded_without_claim_or_retention(tmp_path):
    captures = BoundedScreenshotCaptures(tmp_path.resolve(), clock=_clock())
    with pytest.raises(RuntimeError, match="null image"):
        captures.capture(
            "during",
            lambda: _FakeImage(null=True),
            assert_ui_thread=lambda: None,
        )

    record = captures.records()[0]
    assert record["capture_status"] == "failed"
    assert record["completion_status"] == "not_started"
    assert record["errors"]
    assert captures.retained_bytes == 0
    assert not Path(record["destination"]).exists()


def test_post_loop_screenshot_finalization_has_strict_order_and_releases_images(
    tmp_path,
):
    events = []
    captures = BoundedScreenshotCaptures(tmp_path.resolve(), clock=_clock())
    captures.capture(
        "during",
        lambda: _FakeImage(size_in_bytes=100, events=events, name="during"),
        assert_ui_thread=lambda: events.append("capture-during-thread"),
    )
    events.clear()

    def callback(name, result=None):
        def invoke():
            events.append(name)
            return result

        return invoke

    def load_image(path):
        events.append(f"load-{Path(path).stem}")
        return _FakeImage()

    completed, errors = finish_screenshot_captures_after_loop(
        captures,
        stop_acceptance_timer=callback("stop-acceptance"),
        stop_poll_timer=callback("stop-poll"),
        shutdown_replay=callback("shutdown"),
        final_poll=callback("final-poll"),
        capture_final_image=callback(
            "capture-final", _FakeImage(size_in_bytes=200, events=events, name="final")
        ),
        image_loader=load_image,
        assert_ui_thread=callback("ui-thread"),
    )

    assert errors == []
    assert completed == [
        str((tmp_path / "dashboard_during.png").resolve()),
        str((tmp_path / "dashboard_final.png").resolve()),
    ]
    assert events[:6] == [
        "stop-acceptance",
        "stop-poll",
        "shutdown",
        "final-poll",
        "ui-thread",
        "capture-final",
    ]
    first_save = next(index for index, event in enumerate(events) if isinstance(event, tuple))
    assert first_save >= 7
    assert captures.retained_bytes == 0


def test_post_loop_timer_failure_still_stops_both_and_releases_during_image(tmp_path):
    events = []
    captures = BoundedScreenshotCaptures(tmp_path.resolve(), clock=_clock())
    captures.capture(
        "during",
        lambda: _FakeImage(events=events, name="during"),
        assert_ui_thread=lambda: None,
    )
    events.clear()

    def fail_first_stop():
        events.append("stop-acceptance")
        raise RuntimeError("timer stuck")

    completed, errors = finish_screenshot_captures_after_loop(
        captures,
        stop_acceptance_timer=fail_first_stop,
        stop_poll_timer=lambda: events.append("stop-poll"),
        shutdown_replay=lambda: events.append("shutdown"),
        final_poll=lambda: pytest.fail("final poll must not run after timer failure"),
        capture_final_image=lambda: pytest.fail("final capture must not run"),
        image_loader=lambda _path: _FakeImage(),
        assert_ui_thread=lambda: None,
    )

    assert events[:3] == ["stop-acceptance", "stop-poll", "shutdown"]
    assert completed == [str((tmp_path / "dashboard_during.png").resolve())]
    assert any("acceptance timer stop" in error for error in errors)
    assert any("final" in error and "not captured" in error for error in errors)
    assert captures.retained_bytes == 0
