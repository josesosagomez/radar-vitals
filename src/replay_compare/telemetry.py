"""Replay-only serialization for process telemetry reads."""

from __future__ import annotations

import threading

from src.live_motion.runtime import _rss_bytes as _native_rss_bytes


_rss_lock = threading.Lock()


def replay_rss_bytes() -> int | None:
    """Read process RSS while excluding concurrent replay telemetry calls.

    The native getter caches a platform-specific ``ctypes`` signature.  Replay
    workers and acceptance monitoring can call it from different threads, so
    all replay callers share this lock around the unchanged production getter.
    """

    with _rss_lock:
        return _native_rss_bytes()
