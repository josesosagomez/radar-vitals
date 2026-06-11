"""Phase-based vital-signs DSP: range profile -> chest bin -> phase -> rate.

Design goal (CLAUDE.md s.5): every heart-rate estimate leaves EVIDENCE. The functions
return intermediate signals (chosen bin, unwrapped phase, band spectrum, picked peak) so a
wrong reading (e.g. radar 54 vs Masimo 80) can be diagnosed: wrong range bin? respiration
harmonic? spurious peak? — not just observed.

The pure-signal functions (`bandpass_filter`, `estimate_rate_from_phase`) have no hardware
dependency and are covered by tests/test_vitals_synthetic.py.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
from scipy.signal import butter, filtfilt

# Physiological bands (Hz). Heart 0.8-2.0 Hz = 48-120 bpm; respiration 0.1-0.5 Hz = 6-30 bpm.
HEART_BAND_HZ = (0.8, 2.0)
RESP_BAND_HZ = (0.1, 0.5)


@dataclass
class VitalsParams:
    fs_hz: float                     # slow-time sample rate = radar frame rate
    gate_min_m: float = 1.3          # subject distance gate
    gate_max_m: float = 1.6
    heart_band_hz: tuple = HEART_BAND_HZ
    resp_band_hz: tuple = RESP_BAND_HZ


def bandpass_filter(x: np.ndarray, fs: float, lo: float, hi: float, order: int = 4) -> np.ndarray:
    """Zero-phase Butterworth bandpass."""
    nyq = 0.5 * fs
    b, a = butter(order, [lo / nyq, hi / nyq], btype="band")
    return filtfilt(b, a, x)


def estimate_rate_from_phase(phase: np.ndarray, fs: float, band: tuple) -> dict:
    """Estimate a rate (bpm) from a slow-time phase signal via band-limited FFT peak.

    Returns a dict with the rate AND the spectrum/peak so the choice is inspectable.
    """
    x = np.asarray(phase, dtype=float)
    x = x - x.mean()
    x = bandpass_filter(x, fs, band[0], band[1])

    n = len(x)
    win = np.hanning(n)
    spectrum = np.abs(np.fft.rfft(x * win))
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)

    mask = (freqs >= band[0]) & (freqs <= band[1])
    if not mask.any():
        raise ValueError("No FFT bins in band; window too short for this frequency band.")
    band_freqs = freqs[mask]
    band_spec = spectrum[mask]
    peak_idx = int(np.argmax(band_spec))
    peak_hz = float(band_freqs[peak_idx])

    return {
        "rate_bpm": peak_hz * 60.0,
        "peak_hz": peak_hz,
        "freqs_hz": freqs,
        "spectrum": spectrum,
        "band": band,
        "filtered": x,
    }


def select_range_bin(profiles: np.ndarray, range_axis: np.ndarray, gate: tuple) -> dict:
    """Pick the chest range bin within the distance gate.

    profiles: (frames, chirps, rx, bins) complex range profiles.
    Strategy: reduce to per-bin energy, restrict to [gate_min, gate_max] m, pick the max.
    Returns the bin index plus the per-bin energy so a bad pick is visible.
    """
    energy = np.mean(np.abs(profiles) ** 2, axis=(0, 1, 2))  # -> (bins,)
    gate_mask = (range_axis >= gate[0]) & (range_axis <= gate[1])
    if not gate_mask.any():
        raise ValueError("Distance gate falls outside the range axis; check range resolution.")
    gated = np.where(gate_mask, energy, -np.inf)
    bin_idx = int(np.argmax(gated))
    return {"bin_idx": bin_idx, "range_m": float(range_axis[bin_idx]), "energy": energy}


def phase_at_bin(profiles: np.ndarray, bin_idx: int) -> np.ndarray:
    """Unwrapped slow-time phase at one range bin (one value per frame).

    Coherently averages chirps within a frame and combines RX, then takes the phase
    across frames (slow time). Returns the unwrapped phase signal.
    """
    bin_series = profiles[:, :, :, bin_idx]          # (frames, chirps, rx)
    per_frame = bin_series.mean(axis=(1, 2))         # (frames,) complex
    return np.unwrap(np.angle(per_frame))


def remove_impulse_noise(phase: np.ndarray, thresh: float = 1.5) -> np.ndarray:
    """Clip large frame-to-frame phase jumps (body-motion spikes) before spectral estimation."""
    d = np.diff(phase, prepend=phase[0])
    d = np.clip(d, -thresh, thresh)
    return np.cumsum(d)


def run_pipeline_locked(
    cube: np.ndarray,
    range_axis: np.ndarray,
    params: VitalsParams,
    window_frames: int,
    hop_frames: int,
) -> list[dict]:
    """Phase-locked variant: selects the range bin ONCE for the entire cube.

    Avoids bin-hopping and phase discontinuities that occur when select_range_bin
    is called independently on each window slice.

    Steps:
      1. Select the range bin from the full cube.
      2. Extract and denoise the full slow-time phase (all frames).
      3. Run per-window spectral estimation on the continuous, clean phase.

    Each returned dict has the same keys as run_pipeline() plus:
      - "bin_locked": True
      - "chosen_bin" / "chosen_range_m": the single locked bin (same for every window)
      - "window_start_frame" / "window_end_frame": frame indices into the cube
    """
    sel = select_range_bin(cube, range_axis, (params.gate_min_m, params.gate_max_m))
    locked_bin = sel["bin_idx"]
    locked_range_m = sel["range_m"]

    phase = phase_at_bin(cube, locked_bin)
    phase_clean = remove_impulse_noise(phase)

    n = len(phase_clean)
    results: list[dict] = []
    for s in range(0, n - window_frames + 1, hop_frames):
        e = s + window_frames
        win_phase = phase_clean[s:e]
        heart = estimate_rate_from_phase(win_phase, params.fs_hz, params.heart_band_hz)
        resp = estimate_rate_from_phase(win_phase, params.fs_hz, params.resp_band_hz)
        results.append({
            "hr_bpm": heart["rate_bpm"],
            "rr_bpm": resp["rate_bpm"],
            "chosen_bin": locked_bin,
            "chosen_range_m": locked_range_m,
            "bin_locked": True,
            "bin_energy": sel["energy"],
            "phase_unwrapped": phase[s:e],
            "phase_clean": win_phase,
            "heart_spectrum": heart["spectrum"],
            "heart_freqs_hz": heart["freqs_hz"],
            "heart_peak_hz": heart["peak_hz"],
            "window_start_frame": s,
            "window_end_frame": e,
        })
    return results


def run_pipeline(cube: np.ndarray, range_axis: np.ndarray, params: VitalsParams) -> dict:
    """Full offline estimate from a radar cube. Returns HR, RR, and all intermediates.

    `cube` is the complex range profile (frames, chirps, rx, bins) from radar_io.range_profile.
    """
    sel = select_range_bin(cube, range_axis, (params.gate_min_m, params.gate_max_m))
    phase = phase_at_bin(cube, sel["bin_idx"])
    phase_clean = remove_impulse_noise(phase)

    heart = estimate_rate_from_phase(phase_clean, params.fs_hz, params.heart_band_hz)
    resp = estimate_rate_from_phase(phase_clean, params.fs_hz, params.resp_band_hz)

    return {
        "hr_bpm": heart["rate_bpm"],
        "rr_bpm": resp["rate_bpm"],
        # ---- evidence for debugging a wrong reading ----
        "chosen_bin": sel["bin_idx"],
        "chosen_range_m": sel["range_m"],
        "bin_energy": sel["energy"],
        "phase_unwrapped": phase,
        "phase_clean": phase_clean,
        "heart_spectrum": heart["spectrum"],
        "heart_freqs_hz": heart["freqs_hz"],
        "heart_peak_hz": heart["peak_hz"],
    }
