"""IWR1642 + DCA1000 raw ADC reader.

Byte format: 4-word-packet, per TI rawDataReader.m (dp_reshape2LaneLVDS, lines 607-609)
and SWRA581 §3.3.  For 2-lane LVDS (laneEn=0x3) each 4-word (8-byte) packet contains:

    [I_n, I_{n+1}, Q_n, Q_{n+1}]

Lane 1 (words 0-1) carries I; lane 2 (words 2-3) carries Q.

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
    bin_path: str | Path, cfg: ChirpConfig, trim_frames: int = 0
) -> np.ndarray:
    """Read a DCA1000 raw .bin into a complex radar cube.

    4-word-packet de-interleaving per rawDataReader.m lines 607-609.

    trim_frames: leading frames to skip in the range-profile sanity check (the
      walk-in/settle period).  Range-FFT energy is checked on frames
      [trim_frames : trim_frames+100] (clamped to file length).
      Must satisfy 0 <= trim_frames < num_frames.

    Output: (num_frames, num_chirps_per_frame, num_rx, num_adc_samples), complex64.
    """
    bin_path = Path(bin_path)

    # Validate file geometry before any memory allocation.
    file_size = bin_path.stat().st_size
    bytes_per_frame = cfg.num_chirps_per_frame * cfg.num_rx * cfg.num_adc_samples * 4
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

    # Validate trim_frames before using it as a slice index.
    if not (0 <= trim_frames < num_frames):
        raise ValueError(
            f"trim_frames={trim_frames} is out of range [0, {num_frames}). "
            f"Check trim_start_s and frame_rate_hz in config."
        )

    # memmap instead of fromfile: OS pages the 393 MB file on demand so peak RSS
    # stays low while the decode runs.  Confirmed little-endian int16 (SWRA581).
    raw = np.memmap(bin_path, dtype="<i2", mode="r")

    # 2-lane LVDS 4-word-packet de-interleaving.
    # Each 4-word packet: [I_n, I_{n+1}, Q_n, Q_{n+1}].
    # Lane 1 (words 0-1) = I samples; lane 2 (words 2-3) = Q samples.
    # Ref: rawDataReader.m lines 607-609 (dp_reshape2LaneLVDS).
    words = raw.reshape(-1, 4)
    complex_data = np.empty(raw.size // 2, dtype=np.complex64)
    complex_data[0::2] = words[:, 0].astype(np.float32) + 1j * words[:, 2].astype(np.float32)
    complex_data[1::2] = words[:, 1].astype(np.float32) + 1j * words[:, 3].astype(np.float32)

    cube = complex_data.reshape(
        num_frames, cfg.num_chirps_per_frame, cfg.num_rx, cfg.num_adc_samples
    )

    # --- range-profile sanity check (informational only — does not fail the parse) ---
    # Raw ADC samples are beat-frequency time values; range bins exist only after FFT.
    # Skip trim_frames (walk-in period); clamp end so the slice is always nonempty.
    print(f"cube.shape = {cube.shape}")
    check_start = trim_frames
    check_end   = min(trim_frames + 100, num_frames)   # clamp to file length
    print(f"Sanity check frames: [{check_start}, {check_end})")
    rp_check = np.fft.fft(cube[check_start:check_end], axis=-1)
    energy_full = np.mean(np.abs(rp_check) ** 2, axis=(0, 1, 2))  # (num_adc_samples,)
    n_pos = cfg.num_adc_samples // 2
    energy = energy_full[:n_pos]   # positive-frequency half (alias-free)

    # Top-5 energy bins (excluding DC bin 0)
    top5_idx = np.argsort(energy[1:])[::-1][:5] + 1
    print("Top-5 range bins by energy (excl DC):")
    for b in top5_idx:
        print(f"  bin {int(b):3d}  {b * cfg.range_resolution_m:.3f} m  "
              f"energy={energy[b]:.3e}")

    print(f"energy at range bins 25-35: "
          f"{np.array2string(energy[25:36], precision=2, suppress_small=True)}")

    # Report the overall peak and the in-gate peak; warn if they diverge or if
    # the gate peak implies range_resolution_m is significantly miscalibrated.
    # A stronger peak outside [gate_lo, gate_hi] is normal (static leakage, far
    # walls) and does not indicate a parsing problem.
    gate_lo, gate_hi = 20, 45
    overall_peak_bin   = int(np.argmax(energy[1:])) + 1
    overall_peak_range = overall_peak_bin * cfg.range_resolution_m
    gate_peak_bin      = int(np.argmax(energy[gate_lo:gate_hi + 1])) + gate_lo
    gate_peak_range    = gate_peak_bin * cfg.range_resolution_m

    print(f"Peak energy bin (excl DC): {overall_peak_bin} = {overall_peak_range:.3f} m")
    print(f"Peak in gate [{gate_lo}-{gate_hi}]:   {gate_peak_bin} = {gate_peak_range:.3f} m")

    if overall_peak_bin < gate_lo or overall_peak_bin > gate_hi:
        print(
            f"NOTE: strongest bin ({overall_peak_bin}, {overall_peak_range:.3f} m) is "
            f"outside gate [{gate_lo}-{gate_hi}] — likely static leakage or an off-gate "
            f"reflector.  Not a decoding error; check range_resolution_m "
            f"({cfg.range_resolution_m} m/bin) if the subject peak is not visible."
        )

    if abs(gate_peak_range - 1.4) > 0.2:
        print(
            f"WARNING: gate peak at {gate_peak_range:.3f} m differs from nominal 1.4 m "
            f"by {abs(gate_peak_range - 1.4):.3f} m (> 0.2 m threshold). "
            f"Recalculate range_resolution_m from actual chirp slope and sample rate."
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
