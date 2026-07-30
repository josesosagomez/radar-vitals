"""Unit test for 2-lane LVDS 4-word-packet I/Q lane ordering in read_adc_bin.

Guards against the lane-ordering regression caught in code review: the old decoder
produced complex_data = I0 + j*I1 (treating both lanes as I), losing all Q information.
The correct layout per TI SWRA581B and rawDataReader.m (dp_reshape2LaneLVDS, lines
607-609) is [I_n, I_{n+1}, Q_n, Q_{n+1}] per 4-word packet, so:
    complex_data[0] = words[0] + j*words[2]  = I_n  + j*Q_n
    complex_data[1] = words[1] + j*words[3]  = I_{n+1} + j*Q_{n+1}
This test will fail if the lane ordering ever reverts to I0+jI1.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.radar_io import ChirpConfig, read_adc_bin  # noqa: E402


def test_iq_lane_decode_4word_packet(tmp_path: Path) -> None:
    """Verify that each 4-word packet [I0, I1, Q0, Q1] decodes to [I0+jQ0, I1+jQ1].

    A synthetic .bin filled entirely with the packet [100, 200, 300, 400] must produce
    a cube where every even-indexed sample == 100+300j and every odd-indexed sample ==
    200+400j.
    """
    cfg = ChirpConfig(
        num_adc_samples=256,
        num_rx=4,
        num_tx=2,
        num_chirps_per_frame=32,
        num_frames=1,
        frame_rate_hz=20.0,
        range_resolution_m=1.0,
    )

    # Total complex samples for one frame.
    total_complex = cfg.num_chirps_per_frame * cfg.num_rx * cfg.num_adc_samples  # 32768
    # Each 4-word packet produces 2 complex samples, so we need total_complex/2 packets.
    num_packets = total_complex // 2  # 16384

    # Build raw int16 array: repeat [100, 200, 300, 400] for every packet.
    packet = np.array([100, 200, 300, 400], dtype="<i2")
    raw = np.tile(packet, num_packets)  # shape (65536,) = total_complex * 2 words

    bin_file = tmp_path / "test.bin"
    raw.tofile(bin_file)

    cube = read_adc_bin(bin_file, cfg)

    assert cube.shape == (1, 32, 4, 256), f"unexpected shape: {cube.shape}"

    # Even ADC-sample indices come from words[*,0]+j*words[*,2] = 100+300j.
    # Odd ADC-sample indices come from words[*,1]+j*words[*,3] = 200+400j.
    assert cube[0, 0, 0, 0] == 100 + 300j, f"sample 0 mismatch: {cube[0,0,0,0]}"
    assert cube[0, 0, 0, 1] == 200 + 400j, f"sample 1 mismatch: {cube[0,0,0,1]}"

    even_samples = cube[:, :, :, 0::2]
    odd_samples  = cube[:, :, :, 1::2]
    assert np.all(even_samples == 100 + 300j), "not all even samples are 100+300j"
    assert np.all(odd_samples  == 200 + 400j), "not all odd samples are 200+400j"


def test_iq_lane_decode_4word_packet_sample_swap(tmp_path: Path) -> None:
    """Same packet, SampleSwap=1 (iq_swap=True): [Q0, Q1, I0, I1] -> [I0+jQ0, I1+jQ1].

    This is the branch every Python (scripts/capture.py) capture uses, and therefore
    the branch all 8 recorded captures were decoded with -- yet only the iq_swap=False
    default was covered until now.  The packet [100, 200, 300, 400] must decode with
    real and imaginary parts exchanged relative to the SampleSwap=0 case above.
    """
    cfg = ChirpConfig(
        num_adc_samples=256,
        num_rx=4,
        num_tx=2,
        num_chirps_per_frame=32,
        num_frames=1,
        frame_rate_hz=20.0,
        range_resolution_m=1.0,
        iq_swap=True,
    )

    total_complex = cfg.num_chirps_per_frame * cfg.num_rx * cfg.num_adc_samples
    packet = np.array([100, 200, 300, 400], dtype="<i2")
    raw = np.tile(packet, total_complex // 2)

    bin_file = tmp_path / "test_swapped.bin"
    raw.tofile(bin_file)

    cube = read_adc_bin(bin_file, cfg)

    assert cube.shape == (1, 32, 4, 256), f"unexpected shape: {cube.shape}"

    # Lane 1 (words 0-1) carries Q, lane 2 (words 2-3) carries I.
    assert np.all(cube[:, :, :, 0::2] == 300 + 100j), "even samples are not 300+100j"
    assert np.all(cube[:, :, :, 1::2] == 400 + 200j), "odd samples are not 400+200j"


def test_iq_swap_mirrors_range_bin(tmp_path: Path) -> None:
    """Decoding with the wrong convention mirrors range: a tone at bin k lands at N-k.

    This is the physical consequence the module docstring warns about, and the
    property the empirical gate-vs-mirror check on the recorded captures relies on.
    A synthetic on-grid beat tone at bin K is written in SampleSwap=1 order; reading
    it with iq_swap=True must peak at K, and with iq_swap=False at N-K.
    """
    N = 256
    K = 23  # ~1.0 m at the project's 0.0436 m/bin -- inside the protocol gate
    cfg = ChirpConfig(
        num_adc_samples=N,
        num_rx=4,
        num_tx=2,
        num_chirps_per_frame=32,
        num_frames=1,
        frame_rate_hz=20.0,
        range_resolution_m=0.0436,
        iq_swap=True,
    )

    total_complex = cfg.num_chirps_per_frame * cfg.num_rx * N
    tone = 1000.0 * np.exp(2j * np.pi * K * np.arange(N) / N)
    i_flat = np.tile(np.round(tone.real), total_complex // N).astype("<i2")
    q_flat = np.tile(np.round(tone.imag), total_complex // N).astype("<i2")

    # SampleSwap=1 packet order: [Q_n, Q_{n+1}, I_n, I_{n+1}]
    raw = np.empty(total_complex * 2, dtype="<i2")
    raw[0::4] = q_flat[0::2]
    raw[1::4] = q_flat[1::2]
    raw[2::4] = i_flat[0::2]
    raw[3::4] = i_flat[1::2]

    bin_file = tmp_path / "tone.bin"
    raw.tofile(bin_file)

    correct = read_adc_bin(bin_file, cfg)
    peak_correct = int(np.abs(np.fft.fft(correct[0, 0, 0, :])).argmax())
    assert peak_correct == K, f"iq_swap=True peaked at bin {peak_correct}, expected {K}"

    wrong_cfg = replace(cfg, iq_swap=False)
    wrong = read_adc_bin(bin_file, wrong_cfg)
    peak_wrong = int(np.abs(np.fft.fft(wrong[0, 0, 0, :])).argmax())
    assert peak_wrong == N - K, f"iq_swap=False peaked at bin {peak_wrong}, expected {N - K}"
