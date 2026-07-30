"""Tests for raw-mirror finalisation in scripts/live_demo.py (LiveFrameSource).

Regression cover for the defect found on 2026-07-30: frame-alignment and hashing
used to run inside the receive thread's `finally`, while `stop()` joined that thread
with a 3 s timeout. Hashing a multi-GB mirror does not finish in 3 s, so the join
expired and metadata was written with `mirror_sha256 = None`. It failed silently and
ONLY on large captures — 6 of the 8 canonical captures (every one >= 1.26 GB) lost
their raw hash, and a 10-min study session at 1.57 GB would have lost all 20.

The structural test below (`test_hashing_is_not_done_in_the_receive_thread`) is the
one that actually prevents recurrence: a behavioural test on small fixtures passes
under the old design too, because small files hash fast enough.
"""
from __future__ import annotations

import hashlib
import importlib.util
import inspect
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

_spec = importlib.util.spec_from_file_location("live_demo_mod", REPO_ROOT / "scripts" / "live_demo.py")
live_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(live_demo)
LiveFrameSource = live_demo.LiveFrameSource

PROFILE = {"num_chirps_per_frame": 2, "num_rx": 2, "num_adc_samples": 4, "iq_swap": True}
BYTES_PER_FRAME = 2 * 2 * 4 * 4  # 64


def _source(tmp_path: Path, name: str = "adc_stream.bin") -> tuple[LiveFrameSource, Path]:
    p = tmp_path / name
    src = LiveFrameSource(
        PROFILE, sock_dat=object(), raw_mirror_path=p,
    )
    return src, p


# ---------------------------------------------------------------------------
# The structural guarantee
# ---------------------------------------------------------------------------

def test_hashing_is_not_done_in_the_receive_thread():
    """_loop must not hash or truncate — that is what the join timeout could cut short.

    Asserted on source text because the failure mode is *where* the work happens, and
    a small-file behavioural test cannot distinguish the two designs.
    """
    loop_src = inspect.getsource(LiveFrameSource._loop)
    assert "_sha256_file" not in loop_src, (
        "_loop hashes the mirror again — this is the 2026-07-30 join-timeout defect; "
        "hashing belongs in finalize_mirror, called from the main thread"
    )
    assert "truncate" not in loop_src, (
        "_loop truncates the mirror again — move it to finalize_mirror, or a capture "
        "whose join times out is left un-frame-aligned"
    )
    final_src = inspect.getsource(LiveFrameSource.finalize_mirror)
    assert "_sha256_file" in final_src and "truncate" in final_src


def test_stop_join_timeout_is_not_a_hashing_budget():
    """stop() may keep its short join, but only because finalisation left the thread."""
    stop_src = inspect.getsource(LiveFrameSource.stop)
    assert "join" in stop_src
    assert "_sha256_file" not in stop_src


# ---------------------------------------------------------------------------
# Behaviour
# ---------------------------------------------------------------------------

def test_finalize_hashes_and_frame_aligns(tmp_path: Path):
    src, p = _source(tmp_path)
    payload = b"\x01\x02" * (BYTES_PER_FRAME // 2) * 3      # 3 whole frames
    p.write_bytes(payload + b"\xff" * 10)                   # + a partial frame
    src._writer_done.set()

    src.finalize_mirror()

    assert src.mirror_truncated_bytes == 10
    assert p.stat().st_size == len(payload)
    assert src.mirror_sha256 == hashlib.sha256(payload).hexdigest()
    assert src.mirror_finalize_error is None


def test_hash_is_of_the_truncated_bytes(tmp_path: Path):
    """The recorded hash must describe the file that persists, not the pre-trim bytes."""
    src, p = _source(tmp_path)
    payload = b"\xab" * BYTES_PER_FRAME
    p.write_bytes(payload + b"\x00" * 7)
    src._writer_done.set()
    src.finalize_mirror()
    assert src.mirror_sha256 == hashlib.sha256(p.read_bytes()).hexdigest()


def test_frame_aligned_input_is_not_truncated(tmp_path: Path):
    src, p = _source(tmp_path)
    payload = b"\x07" * (BYTES_PER_FRAME * 2)
    p.write_bytes(payload)
    src._writer_done.set()
    src.finalize_mirror()
    assert src.mirror_truncated_bytes == 0
    assert p.read_bytes() == payload


def test_refuses_to_hash_while_writer_may_still_be_writing(tmp_path: Path):
    """Never hash a file the receive thread might still append to."""
    src, p = _source(tmp_path)
    p.write_bytes(b"\x01" * BYTES_PER_FRAME)
    # _writer_done deliberately NOT set.
    src.finalize_mirror(timeout_s=0.1)
    assert src.mirror_sha256 is None
    assert "did not finish" in src.mirror_finalize_error


def test_waits_for_a_slow_writer_rather_than_giving_up(tmp_path: Path):
    """The old bug in miniature: finalisation must wait, not race a timeout."""
    src, p = _source(tmp_path)
    payload = b"\x02" * BYTES_PER_FRAME

    def _late_writer():
        p.write_bytes(payload)
        src._writer_done.set()

    t = threading.Timer(0.3, _late_writer)
    t.start()
    try:
        src.finalize_mirror(timeout_s=10.0)
    finally:
        t.cancel()

    assert src.mirror_sha256 == hashlib.sha256(payload).hexdigest()
    assert src.mirror_finalize_error is None


def test_missing_hash_always_carries_a_reason(tmp_path: Path):
    """A silent None is what made the original defect invisible."""
    src, p = _source(tmp_path)
    src._writer_done.set()          # file never created
    src.finalize_mirror()
    assert src.mirror_sha256 is None
    assert src.mirror_finalize_error and "does not exist" in src.mirror_finalize_error


def test_empty_mirror_is_reported_not_hashed(tmp_path: Path):
    src, p = _source(tmp_path)
    p.write_bytes(b"")
    src._writer_done.set()
    src.finalize_mirror()
    assert src.mirror_sha256 is None
    assert "empty" in src.mirror_finalize_error


def test_finalize_is_idempotent(tmp_path: Path):
    src, p = _source(tmp_path)
    p.write_bytes(b"\x03" * BYTES_PER_FRAME)
    src._writer_done.set()
    src.finalize_mirror()
    first = src.mirror_sha256
    p.write_bytes(b"\x04" * BYTES_PER_FRAME)   # tamper afterwards
    src.finalize_mirror()
    assert src.mirror_sha256 == first, "second call must not re-hash"


def test_finalize_never_raises(tmp_path: Path, monkeypatch):
    """A finalisation failure must not destroy the run that produced the capture."""
    src, p = _source(tmp_path)
    p.write_bytes(b"\x05" * BYTES_PER_FRAME)
    src._writer_done.set()
    monkeypatch.setattr(live_demo, "_sha256_file", lambda _p: (_ for _ in ()).throw(OSError("disk gone")))
    src.finalize_mirror()
    assert src.mirror_sha256 is None
    assert "OSError" in src.mirror_finalize_error


def test_no_mirror_path_is_a_noop(tmp_path: Path):
    src = LiveFrameSource(PROFILE, sock_dat=object(), raw_mirror_path=None)
    src.finalize_mirror(timeout_s=0.01)
    assert src.mirror_sha256 is None
    assert src.mirror_finalize_error is None
