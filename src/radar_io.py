"""IWR1642 + DCA1000 raw ADC reader.

Two I/Q conventions exist depending on how the IWR1642 was configured:

SampleSwap=0  (mmWave Studio default, iqSwapSel=0):
    4-word packet = [I_n, I_{n+1}, Q_n, Q_{n+1}]
    Lane 1 (words 0-1) carries I; lane 2 (words 2-3) carries Q.
    Use ChirpConfig(iq_swap=False)  ← default, all exp001-exp010 Studio captures.

SampleSwap=1  (SDK adcbufCfg SampleSwap=1, required for LVDS output in demo firmware):
    4-word packet = [Q_n, Q_{n+1}, I_n, I_{n+1}]
    Lane 1 (words 0-1) carries Q; lane 2 (words 2-3) carries I.
    Use ChirpConfig(iq_swap=True)  ← all Python (scripts/capture.py) captures.
    Effect if decoded wrong: positive and negative range frequencies are swapped
    (true target at bin k appears at bin N-k in the FFT output).

Channel interleaving: with chInterleave=1 (non-interleaved), all NSample complex
samples for RX0 come before RX1, RX2, RX3 within each chirp.  The flat sample stream
after I/Q de-interleaving reshapes directly to
(frames, chirps_per_frame, num_rx, num_adc_samples) with no further reordering.

Note on near-DC energy: static leakage and close-range clutter commonly produce a
strong DC/near-DC component in the range profile.  This is a scene-content artefact,
not a format or decoding issue.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Union
import numpy as np


@dataclass
class ChirpConfig:
    """Capture geometry — fill from your mmWave Studio profile/frame config."""
    num_adc_samples: int       # ADC samples per chirp (fast time)
    num_rx: int                # active RX antennas (IWR1642: up to 4)
    num_tx: int                # active TX (IWR1642: up to 2)
    num_chirps_per_frame: int  # loops per frame (per TX)
    num_frames: int            # total frames captured
    frame_rate_hz: float       # = slow-time / phase sample rate (target ~20 Hz)
    range_resolution_m: float  # c / (2 * sweep_bandwidth); compute from your profile
    iq_swap: bool = False      # True for SDK SampleSwap=1 (Python captures); see module docstring
    # NOTE: enforce Low Power ADC mode + IF bandwidth <= 5 MHz in the profile (TI xWR1642).


def parse_logfile(logfile_path: str | Path, utc_offset_hours: int = 3) -> dict:
    """Parse a DCA1000 LogFile CSV and return capture timestamps in UTC.

    Expects lines of the form:
        Capture start time - Tue Jun 09 11:59:55 2026
        Capture end time   - Tue Jun 09 12:02:27 2026
        Duration(sec) - 152
    where the timestamps are in local PC time. utc_offset_hours is subtracted to
    convert to UTC (e.g. 3 for Saudi Arabia AST = UTC+3).

    Returns dict with keys: start_epoch_utc (int), end_epoch_utc (int), duration_s (int).
    Raises ValueError if any of the three expected rows are absent.
    """
    logfile_path = Path(logfile_path)
    text = logfile_path.read_text(encoding="utf-8", errors="replace")

    _TIME_FMT = "%a %b %d %H:%M:%S %Y"
    start_str = end_str = duration_str = None

    for line in text.splitlines():
        if "Capture start time" in line:
            start_str = line.split(" - ", 1)[1].strip()
        elif "Capture end time" in line:
            end_str = line.split(" - ", 1)[1].strip()
        elif "Duration(sec)" in line:
            duration_str = line.split(" - ", 1)[1].strip()

    missing = [k for k, v in [("Capture start time", start_str),
                               ("Capture end time", end_str),
                               ("Duration(sec)", duration_str)] if v is None]
    if missing:
        raise ValueError(f"parse_logfile: rows not found in {logfile_path}: {missing}")

    tz_delta = timedelta(hours=utc_offset_hours)
    start_utc = (datetime.strptime(start_str, _TIME_FMT) - tz_delta).replace(tzinfo=timezone.utc)
    end_utc = (datetime.strptime(end_str, _TIME_FMT) - tz_delta).replace(tzinfo=timezone.utc)

    return {
        "start_epoch_utc": int(start_utc.timestamp()),
        "end_epoch_utc": int(end_utc.timestamp()),
        "duration_s": int(duration_str),
    }


def infer_num_frames(bin_path: str | Path, cfg: ChirpConfig) -> int:
    """Infer the number of captured frames from the .bin file size.

    4 bytes per complex16 sample (2 bytes I + 2 bytes Q).
    Prints inferred vs configured counts so discrepancies are immediately visible.
    """
    bin_path = Path(bin_path)
    file_size = bin_path.stat().st_size
    bytes_per_frame = cfg.num_chirps_per_frame * cfg.num_rx * cfg.num_adc_samples * 4
    remainder = file_size % bytes_per_frame
    if remainder != 0:
        raise ValueError(
            f"infer_num_frames: {bin_path.name} size {file_size} B is not divisible "
            f"by bytes_per_frame {bytes_per_frame} (remainder {remainder} B). "
            f"Check num_chirps_per_frame={cfg.num_chirps_per_frame}, "
            f"num_rx={cfg.num_rx}, num_adc_samples={cfg.num_adc_samples}."
        )
    num_frames = file_size // bytes_per_frame
    print(
        f"infer_num_frames: {file_size} B / {bytes_per_frame} B per frame "
        f"-> {num_frames} frames  (config: {cfg.num_frames})"
    )
    return num_frames


def read_adc_bin(
    path: Union[str, Path, List[Union[str, Path]]],
    cfg: ChirpConfig,
    trim_frames: int = 0,
) -> np.ndarray:
    """Read a DCA1000 raw .bin into a complex radar cube.

    4-word-packet de-interleaving per rawDataReader.m lines 607-609.

    path: a single file path, or a list of paths to concatenate in order.
      When mmWave Studio splits a recording across multiple files (e.g. at the
      1 GB boundary), pass the parts as a list.  The split need NOT fall on a
      frame boundary — raw bytes are concatenated before decoding so mid-frame
      splits are handled correctly.

    trim_frames: accepted for backward compatibility but no longer used.
      Range-bin selection is handled by Step 3 (select_chest_bin.py).

    Output: (num_frames, num_chirps_per_frame, num_rx, num_adc_samples), complex64.
    """
    bytes_per_frame = cfg.num_chirps_per_frame * cfg.num_rx * cfg.num_adc_samples * 4

    if isinstance(path, list):
        # ------------------------------------------------------------------ #
        # Multi-file path: validate all files exist, concatenate raw bytes,  #
        # then decode the combined stream.  Raw-byte concatenation handles    #
        # splits that do not fall on a frame boundary (e.g. mmWave Studio    #
        # 1 GB limit splitting mid-frame).                                    #
        # ------------------------------------------------------------------ #
        paths = [Path(p) for p in path]
        for p in paths:
            if not p.exists():
                raise FileNotFoundError(
                    f"read_adc_bin: file not found: {p}"
                )

        # Validate combined size before allocating.
        file_sizes = [p.stat().st_size for p in paths]
        total_bytes = sum(file_sizes)
        remainder = total_bytes % bytes_per_frame
        if remainder != 0:
            raise ValueError(
                f"Combined size of {len(paths)} files is {total_bytes} bytes, "
                f"not a multiple of {bytes_per_frame} bytes/frame "
                f"(remainder {remainder} bytes)."
            )
        num_frames = total_bytes // bytes_per_frame
        if num_frames == 0:
            raise ValueError("Combined files contain no complete frames.")

        for p, sz in zip(paths, file_sizes):
            print(
                f"  {p.name}: {sz} B "
                f"({sz / bytes_per_frame:.6f} raw frames)"
            )
        print(
            f"Config num_frames: {cfg.num_frames} | "
            f"Inferred from {len(paths)} files: {num_frames}"
        )

        # Concatenate raw int16 words into a single contiguous array.
        total_words = total_bytes // 2   # int16 = 2 bytes
        raw = np.empty(total_words, dtype="<i2")
        offset = 0
        for p in paths:
            mm = np.memmap(p, dtype="<i2", mode="r")
            n = len(mm)
            raw[offset: offset + n] = mm
            offset += n

    else:
        # ------------------------------------------------------------------ #
        # Single-file path — original behaviour, unchanged.                  #
        # ------------------------------------------------------------------ #
        bin_path = Path(path)

        file_size = bin_path.stat().st_size
        inferred = file_size // bytes_per_frame
        remainder = file_size % bytes_per_frame
        if remainder != 0:
            raise ValueError(
                f"{bin_path.name}: file size {file_size} bytes is not a multiple of "
                f"{bytes_per_frame} bytes/frame (remainder {remainder} bytes). "
                f"The capture may be truncated. Got {inferred} complete frames."
            )
        if inferred == 0:
            raise ValueError(f"{bin_path.name}: file is empty or smaller than one frame.")
        num_frames = inferred
        print(f"Config num_frames: {cfg.num_frames} | Inferred from file: {num_frames}")

        raw = np.memmap(bin_path, dtype="<i2", mode="r")

    # 2-lane LVDS 4-word-packet de-interleaving (shared for single and multi-file).
    # Word layout depends on adcbufCfg SampleSwap — see module docstring.
    words = raw.reshape(-1, 4)
    complex_data = np.empty(raw.size // 2, dtype=np.complex64)
    if cfg.iq_swap:
        # SampleSwap=1: packet = [Q_n, Q_{n+1}, I_n, I_{n+1}]
        complex_data[0::2] = words[:, 2].astype(np.float32) + 1j * words[:, 0].astype(np.float32)
        complex_data[1::2] = words[:, 3].astype(np.float32) + 1j * words[:, 1].astype(np.float32)
    else:
        # SampleSwap=0 (Studio default): packet = [I_n, I_{n+1}, Q_n, Q_{n+1}]
        # Ref: rawDataReader.m lines 607-609 (dp_reshape2LaneLVDS).
        complex_data[0::2] = words[:, 0].astype(np.float32) + 1j * words[:, 2].astype(np.float32)
        complex_data[1::2] = words[:, 1].astype(np.float32) + 1j * words[:, 3].astype(np.float32)

    cube = complex_data.reshape(
        num_frames, cfg.num_chirps_per_frame, cfg.num_rx, cfg.num_adc_samples
    )

    return cube


def range_profile(cube: np.ndarray, window_fn=np.hanning) -> np.ndarray:
    """Range FFT along fast time (last axis) -> complex range profile.

    Input:  (frames, chirps, rx, samples) complex
    Output: (frames, chirps, rx, samples) complex (range bins along last axis)
    """
    n = cube.shape[-1]
    win = window_fn(n).astype(cube.dtype)
    return np.fft.fft(cube * win, axis=-1)


def range_axis_m(num_samples: int, range_resolution_m: float) -> np.ndarray:
    """Range (metres) for each range bin index."""
    return np.arange(num_samples) * range_resolution_m
