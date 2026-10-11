"""Deterministic serialization tests for process-wide replay RSS sampling."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from src.replay_compare import telemetry


class _FakeFunction:
    """Callable Windows function stand-in with assignable ctypes metadata."""

    def __init__(self, implementation):
        self._implementation = implementation
        self.calls = []
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        self.calls.append(args)
        return self._implementation(*args)


def test_windows_rss_binding_is_cached_and_reports_current_working_set(
    monkeypatch,
):
    process_handle = 0xCAFE
    current_process = _FakeFunction(lambda: process_handle)

    def fill_counters(handle, counters_pointer, size):
        assert handle == process_handle
        assert size == telemetry.ctypes.sizeof(telemetry._ProcessMemoryCounters)
        counters = telemetry.ctypes.cast(
            counters_pointer,
            telemetry._ProcessMemoryCountersPointer,
        ).contents
        assert counters.cb == size
        counters.PeakWorkingSetSize = 987_654_321
        counters.WorkingSetSize = 123_456_789
        return 1

    process_memory = _FakeFunction(fill_counters)
    fake_loader = SimpleNamespace(
        kernel32=SimpleNamespace(GetCurrentProcess=current_process),
        psapi=SimpleNamespace(GetProcessMemoryInfo=process_memory),
    )
    structure_type = telemetry._ProcessMemoryCounters
    pointer_type = telemetry._ProcessMemoryCountersPointer

    monkeypatch.setattr(telemetry.ctypes, "windll", fake_loader, raising=False)
    monkeypatch.setattr(
        telemetry,
        "_windows_memory_api",
        telemetry._UNINITIALIZED,
    )

    assert telemetry.replay_rss_bytes() == 123_456_789
    assert telemetry.replay_rss_bytes() == 123_456_789

    assert len(current_process.calls) == 1
    assert len(process_memory.calls) == 2
    assert current_process.argtypes == []
    assert current_process.restype is telemetry.wintypes.HANDLE
    assert process_memory.argtypes == [
        telemetry.wintypes.HANDLE,
        pointer_type,
        telemetry.wintypes.DWORD,
    ]
    assert process_memory.restype is telemetry.wintypes.BOOL
    assert telemetry._ProcessMemoryCounters is structure_type
    assert telemetry._ProcessMemoryCountersPointer is pointer_type
    assert pointer_type is telemetry.ctypes.POINTER(structure_type)


def test_windows_rss_unavailability_is_cached(monkeypatch):
    initialization_calls = 0

    def unavailable():
        nonlocal initialization_calls
        initialization_calls += 1
        return None

    monkeypatch.setattr(telemetry, "_initialize_windows_memory_api", unavailable)
    monkeypatch.setattr(
        telemetry,
        "_windows_memory_api",
        telemetry._UNINITIALIZED,
    )

    assert telemetry.replay_rss_bytes() is None
    assert telemetry.replay_rss_bytes() is None
    assert initialization_calls == 1
    assert telemetry._windows_memory_api is None


def test_windows_rss_api_failure_returns_none(monkeypatch):
    calls = 0

    def fail_read(_process, _counters, _size):
        nonlocal calls
        calls += 1
        return 0

    monkeypatch.setattr(
        telemetry,
        "_windows_memory_api",
        (fail_read, 0xCAFE),
    )

    assert telemetry.replay_rss_bytes() is None
    assert telemetry.replay_rss_bytes() is None
    assert calls == 2


def test_owned_child_memory_binding_reports_current_and_peak_and_closes_handle(
    monkeypatch,
):
    process_handle = 0xBEEF
    open_process = _FakeFunction(
        lambda access, inherit, process_id: (
            process_handle
            if (
                access
                == telemetry._PROCESS_QUERY_INFORMATION | telemetry._PROCESS_VM_READ
                and inherit is False
                and process_id == 4321
            )
            else 0
        )
    )

    def fill_counters(handle, counters_pointer, size):
        assert handle == process_handle
        assert size == telemetry.ctypes.sizeof(telemetry._ProcessMemoryCounters)
        counters = telemetry.ctypes.cast(
            counters_pointer,
            telemetry._ProcessMemoryCountersPointer,
        ).contents
        assert counters.cb == size
        counters.WorkingSetSize = 123_456
        counters.PeakWorkingSetSize = 987_654
        return 1

    process_memory = _FakeFunction(fill_counters)
    close_handle = _FakeFunction(lambda handle: int(handle == process_handle))
    fake_loader = SimpleNamespace(
        kernel32=SimpleNamespace(
            OpenProcess=open_process,
            CloseHandle=close_handle,
        ),
        psapi=SimpleNamespace(GetProcessMemoryInfo=process_memory),
    )
    structure_type = telemetry._ProcessMemoryCounters
    pointer_type = telemetry._ProcessMemoryCountersPointer

    monkeypatch.setattr(telemetry.ctypes, "windll", fake_loader, raising=False)
    monkeypatch.setattr(
        telemetry,
        "_external_windows_memory_api",
        telemetry._UNINITIALIZED,
    )

    assert telemetry.replay_process_memory_bytes(4321) == (123_456, 987_654)
    assert telemetry.replay_process_memory_bytes(4321) == (123_456, 987_654)

    assert len(open_process.calls) == 2
    assert len(process_memory.calls) == 2
    assert close_handle.calls == [(process_handle,), (process_handle,)]
    assert open_process.argtypes == [
        telemetry.wintypes.DWORD,
        telemetry.wintypes.BOOL,
        telemetry.wintypes.DWORD,
    ]
    assert open_process.restype is telemetry.wintypes.HANDLE
    assert process_memory.argtypes == [
        telemetry.wintypes.HANDLE,
        pointer_type,
        telemetry.wintypes.DWORD,
    ]
    assert process_memory.restype is telemetry.wintypes.BOOL
    assert close_handle.argtypes == [telemetry.wintypes.HANDLE]
    assert close_handle.restype is telemetry.wintypes.BOOL
    assert telemetry._ProcessMemoryCounters is structure_type
    assert telemetry._ProcessMemoryCountersPointer is pointer_type


@pytest.mark.parametrize("process_id", [True, False, 0, -1, 1.0, "1"])
def test_owned_child_memory_rejects_coercive_or_nonpositive_pid(process_id):
    with pytest.raises(ValueError, match="positive exact integer"):
        telemetry.replay_process_memory_bytes(process_id)


def test_owned_child_memory_failure_still_closes_handle(monkeypatch):
    close_calls = []

    def fail_read(_handle, _counters, _size):
        raise OSError("synthetic process-exit race")

    monkeypatch.setattr(
        telemetry,
        "_external_windows_memory_api",
        (
            lambda _access, _inherit, _pid: 0xBEEF,
            fail_read,
            lambda handle: close_calls.append(handle),
        ),
    )

    assert telemetry.replay_process_memory_bytes(4321) == (None, None)
    assert close_calls == [0xBEEF]


def test_owned_child_memory_unavailability_is_cached(monkeypatch):
    initialization_calls = 0

    def unavailable():
        nonlocal initialization_calls
        initialization_calls += 1
        return None

    monkeypatch.setattr(
        telemetry,
        "_initialize_external_windows_memory_api",
        unavailable,
    )
    monkeypatch.setattr(
        telemetry,
        "_external_windows_memory_api",
        telemetry._UNINITIALIZED,
    )

    assert telemetry.replay_process_memory_bytes(4321) == (None, None)
    assert telemetry.replay_process_memory_bytes(4321) == (None, None)
    assert initialization_calls == 1


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
