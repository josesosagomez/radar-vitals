"""Deterministic serialization tests for process-wide replay RSS sampling."""

from __future__ import annotations

import threading

import pytest

from src.replay_compare import telemetry


def test_replay_rss_bytes_serializes_native_getter_across_threads(monkeypatch):
    state_lock = threading.Lock()
    first_entered = threading.Event()
    second_attempting = threading.Event()
    overlapping_raw_call = threading.Event()
    active = 0
    max_active = 0
    call_count = 0

    def raw_getter():
        nonlocal active, max_active, call_count
        with state_lock:
            call_count += 1
            call_number = call_count
            active += 1
            max_active = max(max_active, active)
            if active > 1:
                overlapping_raw_call.set()
        try:
            if call_number == 1:
                first_entered.set()
                assert second_attempting.wait(timeout=2.0)
                # Without wrapper serialization the second raw call enters and
                # signals overlap while this first call is deliberately held.
                overlapping_raw_call.wait(timeout=0.5)
            return 100 + call_number
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(telemetry, "_native_rss_bytes", raw_getter)
    results = {}

    def sample(label, *, announce=False):
        if announce:
            second_attempting.set()
        results[label] = telemetry.replay_rss_bytes()

    first = threading.Thread(target=sample, args=("first",))
    second = threading.Thread(
        target=sample, args=("second",), kwargs={"announce": True}
    )
    first.start()
    assert first_entered.wait(timeout=2.0)
    second.start()
    first.join(timeout=3.0)
    second.join(timeout=3.0)

    assert first.is_alive() is False
    assert second.is_alive() is False
    assert results == {"first": 101, "second": 102}
    assert max_active == 1
    assert overlapping_raw_call.is_set() is False


def test_replay_rss_bytes_preserves_none_and_releases_lock_after_exception(
    monkeypatch,
):
    monkeypatch.setattr(telemetry, "_native_rss_bytes", lambda: None)
    assert telemetry.replay_rss_bytes() is None

    calls = 0

    def fail_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic RSS failure")
        return 12345

    monkeypatch.setattr(telemetry, "_native_rss_bytes", fail_once)
    with pytest.raises(RuntimeError, match="synthetic RSS failure"):
        telemetry.replay_rss_bytes()
    assert telemetry.replay_rss_bytes() == 12345
