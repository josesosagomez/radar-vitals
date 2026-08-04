"""Tests for the true frame-0 capture origin (2026-08-04).

`start_wall_utc` is written before the DCA1000 and IWR1642 are configured, so it precedes
frame 0 by seconds — a 5-15 s error against a 30 s window grid, i.e. up to a third of a
window. These tests pin the replacement: a first-packet timestamp corrected for leading
zero-fill, and a scorer that prefers it while still accepting the eight pre-2026-08-04
captures that do not have it.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import live_demo  # noqa: E402
import score_offline as so  # noqa: E402

PROFILE = {
    "num_chirps_per_frame": 32, "num_rx": 4, "num_adc_samples": 256, "iq_swap": True,
}
BYTES_PER_FRAME = 32 * 4 * 256 * 4


def _source() -> "live_demo.LiveFrameSource":
    return live_demo.LiveFrameSource(PROFILE, net_cfg={"host_ip": "127.0.0.1", "data_port": 0})


# ── LiveFrameSource.frame0_epoch_utc ────────────────────────────────────────


def test_no_packet_means_no_origin():
    """A run that received nothing must report None, not a fabricated timestamp."""
    assert _source().frame0_epoch_utc(20.0) is None


def test_origin_is_the_first_packet_when_no_leading_loss():
    src = _source()
    src.t_first_packet_utc = 1_800_000_000.0
    src.leading_zero_filled_bytes = 0
    assert src.frame0_epoch_utc(20.0) == pytest.approx(1_800_000_000.0)


def test_origin_is_corrected_backwards_by_leading_zero_fill():
    """Joining the stream late means frame 0's data began BEFORE the first packet seen."""
    src = _source()
    src.t_first_packet_utc = 1_800_000_000.0
    src.leading_zero_filled_bytes = 2 * BYTES_PER_FRAME      # two whole frames missed
    # two frames at 20 Hz = 0.1 s earlier
    assert src.frame0_epoch_utc(20.0) == pytest.approx(1_800_000_000.0 - 0.1)


def test_leading_correction_scales_with_frame_rate():
    src = _source()
    src.t_first_packet_utc = 1_000.0
    src.leading_zero_filled_bytes = BYTES_PER_FRAME
    assert src.frame0_epoch_utc(20.0) == pytest.approx(1_000.0 - 0.05)
    assert src.frame0_epoch_utc(10.0) == pytest.approx(1_000.0 - 0.10)


def test_partial_frame_of_leading_loss_is_a_fraction_of_a_frame():
    src = _source()
    src.t_first_packet_utc = 500.0
    src.leading_zero_filled_bytes = BYTES_PER_FRAME // 4
    assert src.frame0_epoch_utc(20.0) == pytest.approx(500.0 - 0.0125)


def test_mid_stream_gaps_do_not_move_the_origin():
    """Only LEADING loss shifts frame 0; a gap at minute 5 does not."""
    src = _source()
    src.t_first_packet_utc = 1_800_000_000.0
    src.zero_filled_bytes = 40 * BYTES_PER_FRAME    # large mid-stream loss
    src.leading_zero_filled_bytes = 0
    assert src.frame0_epoch_utc(20.0) == pytest.approx(1_800_000_000.0)


# ── score_offline.resolve_frame0_epoch ──────────────────────────────────────


def test_scorer_prefers_the_true_origin():
    meta = {
        "start_wall_utc": "2026-08-04T10:00:00+00:00",
        "frame0_epoch_utc": 1_800_000_012.5,
        "frame0_epoch_source": "first_packet_receipt_minus_leading_zero_fill",
    }
    epoch, source, approx, caveat = so.resolve_frame0_epoch(meta)
    assert epoch == pytest.approx(1_800_000_012.5)
    assert source == "first_packet_receipt_minus_leading_zero_fill"
    assert approx is False
    assert caveat == ""


def test_scorer_falls_back_for_pre_2026_08_04_captures():
    """The eight existing captures have no such field and must still score."""
    wall = "2026-07-28T19:49:02.711071+00:00"
    epoch, source, approx, caveat = so.resolve_frame0_epoch({"start_wall_utc": wall})
    assert epoch == pytest.approx(datetime.fromisoformat(wall).timestamp())
    assert source == "start_wall_utc_approximate"
    assert approx is True
    assert "approximate" in caveat


def test_explicit_null_origin_falls_back_rather_than_crashing():
    """live_demo writes the key as null on a run that received no packets."""
    wall = "2026-08-04T10:00:00+00:00"
    _epoch, source, approx, _c = so.resolve_frame0_epoch(
        {"start_wall_utc": wall, "frame0_epoch_utc": None, "frame0_epoch_source": None}
    )
    assert source == "start_wall_utc_approximate" and approx is True


def test_the_error_this_fixes_is_large_enough_to_matter():
    """Regression guard on the motivation, not just the mechanism.

    A 12 s origin error against 600-frame windows at 20 Hz misassigns 240 frames — 40% of a
    window — so reference spans pair with the wrong radar data. This test exists so nobody
    later decides the fallback is 'good enough' without seeing the size of the effect.
    """
    wall = datetime(2026, 8, 4, 10, 0, 0, tzinfo=timezone.utc)
    approx_epoch, _s, _a, _c = so.resolve_frame0_epoch({"start_wall_utc": wall.isoformat()})
    true_epoch, _s2, _a2, _c2 = so.resolve_frame0_epoch({
        "start_wall_utc": wall.isoformat(),
        "frame0_epoch_utc": approx_epoch + 12.0,
        "frame0_epoch_source": "first_packet_receipt_minus_leading_zero_fill",
    })
    frames_misassigned = (true_epoch - approx_epoch) * 20.0
    assert frames_misassigned == pytest.approx(240.0)
    assert frames_misassigned / 600.0 > 0.35
