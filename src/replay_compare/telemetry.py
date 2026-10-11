"""Replay-local process telemetry with stable Windows ``ctypes`` bindings."""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Any


class _ProcessMemoryCounters(ctypes.Structure):
    """Windows PROCESS_MEMORY_COUNTERS with a stable module-level identity."""

    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


_ProcessMemoryCountersPointer = ctypes.POINTER(_ProcessMemoryCounters)
_UNINITIALIZED = object()
_windows_memory_api: tuple[Any, Any] | None | object = _UNINITIALIZED
_external_windows_memory_api: tuple[Any, Any, Any] | None | object = _UNINITIALIZED
_rss_lock = threading.Lock()

_PROCESS_QUERY_INFORMATION = 0x0400
_PROCESS_VM_READ = 0x0010


def _initialize_windows_memory_api() -> tuple[Any, Any] | None:
    """Bind the Windows API once and cache the process pseudo-handle."""

    try:
        loader = getattr(ctypes, "windll", None)
        if loader is None:
            return None
        get_current_process = loader.kernel32.GetCurrentProcess
        get_current_process.argtypes = []
        get_current_process.restype = wintypes.HANDLE
        get_process_memory = loader.psapi.GetProcessMemoryInfo
        get_process_memory.argtypes = [
            wintypes.HANDLE,
            _ProcessMemoryCountersPointer,
            wintypes.DWORD,
        ]
        get_process_memory.restype = wintypes.BOOL
        return get_process_memory, get_current_process()
    except (AttributeError, ImportError, OSError, TypeError, ValueError):
        return None


def _native_rss_bytes() -> int | None:
    """Return the current working-set size through the cached Windows API."""

    global _windows_memory_api
    if _windows_memory_api is _UNINITIALIZED:
        _windows_memory_api = _initialize_windows_memory_api()
    if _windows_memory_api is None:
        return None
    if not isinstance(_windows_memory_api, tuple):
        return None
    get_process_memory, process = _windows_memory_api
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    try:
        ok = get_process_memory(process, ctypes.byref(counters), counters.cb)
    except (AttributeError, ctypes.ArgumentError, OSError, TypeError, ValueError):
        return None
    return int(counters.WorkingSetSize) if ok else None


def replay_rss_bytes() -> int | None:
    """Return replay process RSS while serializing lazy binding and reads."""

    with _rss_lock:
        return _native_rss_bytes()


def _initialize_external_windows_memory_api() -> tuple[Any, Any, Any] | None:
    """Bind the Windows functions needed to observe one owned child process."""

    try:
        loader = getattr(ctypes, "windll", None)
        if loader is None:
            return None
        open_process = loader.kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        close_handle = loader.kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        get_process_memory = loader.psapi.GetProcessMemoryInfo
        get_process_memory.argtypes = [
            wintypes.HANDLE,
            _ProcessMemoryCountersPointer,
            wintypes.DWORD,
        ]
        get_process_memory.restype = wintypes.BOOL
        return open_process, get_process_memory, close_handle
    except (AttributeError, ImportError, OSError, TypeError, ValueError):
        return None


def replay_process_memory_bytes(
    process_id: int,
) -> tuple[int | None, int | None]:
    """Return current and lifetime-peak working-set bytes for an owned PID.

    The caller is responsible for ensuring that ``process_id`` belongs to the
    replay child.  A fresh Windows handle is opened for the observation and is
    always closed before returning.  Unavailable or exited processes yield
    ``(None, None)``; peak bytes are never substituted for current RSS.
    """

    if type(process_id) is not int or process_id <= 0:
        raise ValueError("process_id must be a positive exact integer")

    global _external_windows_memory_api
    with _rss_lock:
        if _external_windows_memory_api is _UNINITIALIZED:
            _external_windows_memory_api = _initialize_external_windows_memory_api()
        if _external_windows_memory_api is None:
            return None, None
        if not isinstance(_external_windows_memory_api, tuple):
            return None, None
        open_process, get_process_memory, close_handle = _external_windows_memory_api
        process = None
        try:
            access = _PROCESS_QUERY_INFORMATION | _PROCESS_VM_READ
            process = open_process(access, False, process_id)
            if not process:
                return None, None
            counters = _ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            ok = get_process_memory(
                process, ctypes.byref(counters), counters.cb
            )
            if not ok:
                return None, None
            return int(counters.WorkingSetSize), int(counters.PeakWorkingSetSize)
        except (AttributeError, ctypes.ArgumentError, OSError, TypeError, ValueError):
            return None, None
        finally:
            if process:
                try:
                    close_handle(process)
                except (AttributeError, ctypes.ArgumentError, OSError, TypeError, ValueError):
                    pass
