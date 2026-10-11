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
_rss_lock = threading.Lock()


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
