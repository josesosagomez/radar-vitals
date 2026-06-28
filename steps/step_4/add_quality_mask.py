#!/usr/bin/env python3
"""Add a per-frame quality mask to existing time-domain cube HDF5 files.

Loads each session from data/processed/time_domain_cubes/<session_id>.h5,
drops the first trim_frames (accommodation period), then runs hard- and
soft-failure checks on each remaining frame. Results are written back into
the same file.

HDF5 additions
--------------
/quality_mask                      bool (N_analysis,)    True = healthy frame
/quality_metrics/max_abs_adc       float32 (N_analysis,) max |ADC sample| per frame
/quality_metrics/frame_energy      float32 (N_analysis,) mean |frame|² per frame
/quality_metrics/clip_flagged      bool    (N_analysis,)
/quality_metrics/dead_flagged      bool    (N_analysis,)
/quality_metrics/rx_energy_ratio   float32 (N_analysis,) max/min RX energy per frame
                                                          (written when rx_imbalance
                                                           block present in config)
/quality_metrics/rx_imbalance_flagged bool (N_analysis,) (same condition)

Attributes added to /quality_mask:
  trim_frames, clip_threshold, dead_frame_fraction,
  rx_imbalance_enabled, rx_imbalance_ratio  (when rx_imbalance block present),
  git_commit, created_at

Adding a new soft-failure criterion
------------------------------------
1. Add an entry under soft_failures in steps/step_4/config.yaml with
   at minimum an `enabled` key.
2. Implement _flag_<name>(cube, ...) -> tuple[np.ndarray[bool], np.ndarray]
   returning (flagged, metric) both shape (N,).
3. In _compute_quality_mask(), read the config block, call the function,
   store results in the dict, and OR into `flagged` only when enabled.
4. In _process_session(), write the metric and flagged arrays to
   /quality_metrics/ and add the config values as attrs on /quality_mask.
5. Extend _run_stats() and _write_log() to report counts.

Run from repo root:
    python -X utf8 steps/step_4/add_quality_mask.py --session exp003
    python -X utf8 steps/step_4/add_quality_mask.py --all
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import yaml
from itertools import combinations as _combinations
from scipy import ndimage as _ndimage
from scipy.fft import fft as _fft, rfft as _rfft

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Failure checks
# Each function receives the analysis cube (N_analysis, chirps, rx, samples)
# as complex64 and returns (flagged, metric) both shape (N_analysis,).
# flagged[i] = True means frame i FAILS that check (i.e. is unhealthy).
# ---------------------------------------------------------------------------

def _flag_clipping(cube: np.ndarray, threshold: float) -> tuple[np.ndarray, np.ndarray]:
    """Flag frames where any ADC sample reaches the saturation rail.

    Views the complex64 cube as float32 (interleaved real/imag) so that
    both I and Q channels are checked against the threshold.

    Returns (flagged, max_abs_adc) both shape (N,).
    """
    # View as float32: shape (N, chirps, rx, samples*2)
    flat = cube.view(np.float32).reshape(cube.shape[0], -1)
    max_abs = np.max(np.abs(flat), axis=1).astype(np.float32)
    return max_abs >= threshold, max_abs


def _flag_dead(cube: np.ndarray, fraction: float) -> tuple[np.ndarray, np.ndarray]:
    """Flag frames whose energy is below a fraction of the session median.

    Energy = mean(|sample|²) over all chirps, rx, and ADC samples per frame.

    Returns (flagged, frame_energy) both shape (N,).
    """
    # |complex|² = real² + imag² — np.abs does this correctly for complex64
    energy = np.mean(
        np.abs(cube.reshape(cube.shape[0], -1)) ** 2,
        axis=1,
    ).astype(np.float32)
    median_e = float(np.median(energy))
    return energy < fraction * median_e, energy


def _flag_rx_imbalance(cube: np.ndarray, ratio: float) -> tuple[np.ndarray, np.ndarray]:
    """Flag frames where RX channel energy spread exceeds the given ratio.

    Computes mean(|sample|²) over all chirps and ADC samples independently
    for each RX channel, then flags frames where
    max(channel_energy) / min(channel_energy) > ratio.

    A fully dead channel (zero energy) is treated as infinite ratio and
    always flagged regardless of the threshold.

    Returns (flagged, rx_energy_ratio) both shape (N,), ratio as float32.
    """
    # mean |sample|² over chirps (axis=1) and ADC samples (axis=3) → (N, rx)
    energy_per_rx = np.mean(np.abs(cube) ** 2, axis=(1, 3))
    max_e = energy_per_rx.max(axis=1)
    min_e = energy_per_rx.min(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        rx_ratio = np.where(min_e > 0, max_e / min_e, np.inf).astype(np.float32)
    return rx_ratio > ratio, rx_ratio


def _subject_bin_energy(
    cube: np.ndarray,
    locked_bin: int,
    *,
    chunk_frames: int | None = None,
) -> np.ndarray:
    """Compute per-frame mean |FFT[locked_bin]|² averaged over chirps and RX.

    Shared by motion_spike, subject_bin_dropout (fallback), and any future
    subject-bin energy checks.  Validates locked_bin and chunk_frames.

    Parameters
    ----------
    cube         : (N, chirps, rx, samples) complex64 or float32
    locked_bin   : range-FFT bin index.
                   Valid range: [0, samples-1] for complex; [0, samples//2] for real.
    chunk_frames : frames per FFT chunk. None = all at once.

    Returns
    -------
    energy : float32 (N,)
    """
    if not isinstance(locked_bin, (int, np.integer)):
        raise ValueError(f"locked_bin must be an integer, got {type(locked_bin)}")
    if chunk_frames is not None and chunk_frames <= 0:
        raise ValueError(f"chunk_frames must be > 0, got {chunk_frames}")

    n_frames, _, _, n_samples = cube.shape
    is_complex = np.iscomplexobj(cube)
    n_bins     = n_samples if is_complex else (n_samples // 2 + 1)

    if not (0 <= int(locked_bin) < n_bins):
        raise ValueError(
            f"locked_bin={locked_bin} is out of range for FFT output size {n_bins} "
            f"({'complex' if is_complex else 'real'} input, {n_samples} samples)"
        )

    fft_fn = _fft if is_complex else _rfft
    energy = np.empty(n_frames, dtype=np.float32)
    step   = chunk_frames if chunk_frames is not None else n_frames

    for start in range(0, n_frames, step):
        stop      = min(start + step, n_frames)
        chunk     = cube[start:stop]
        fft_chunk = fft_fn(chunk, axis=3)
        bin_col   = fft_chunk[:, :, :, int(locked_bin)]
        energy[start:stop] = np.mean(np.abs(bin_col) ** 2, axis=(1, 2)).astype(np.float32)

    return energy.astype(np.float32, copy=False)


def _flag_motion_spike(
    cube: np.ndarray,
    locked_bin: int,
    factor: float,
    half_window: int,
    *,
    chunk_frames: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Flag frames where subject-bin energy exceeds factor × local rolling median.

    Delegates FFT work to _subject_bin_energy; applies rolling-median spike
    detection on the resulting energy series.

    Uses scipy.fft.fft for complex64 input (standard FMCW IQ data) and
    scipy.fft.rfft for real-valued input.

    Parameters
    ----------
    cube         : (N, chirps, rx, samples) complex64 or float32
    locked_bin   : range-FFT bin index corresponding to the subject.
                   Valid range: [0, samples-1] for complex; [0, samples//2] for real.
    factor       : spike threshold — must be > 1.
    half_window  : half-width of the rolling median window (full size = 2*half_window+1).
                   Must be >= 0.
    chunk_frames : frames per FFT chunk. None = all at once.

    Returns
    -------
    flagged            : bool (N,)    — True = spike detected
    subject_bin_energy : float32 (N,) — mean |FFT[locked_bin]|² over chirps and RX
    """
    if factor <= 1:
        raise ValueError(f"factor must be > 1, got {factor}")
    if half_window < 0:
        raise ValueError(f"half_window must be >= 0, got {half_window}")

    # locked_bin and chunk_frames validated inside _subject_bin_energy
    subject_bin_energy = _subject_bin_energy(cube, locked_bin, chunk_frames=chunk_frames)

    rolling_med = _ndimage.median_filter(
        subject_bin_energy,
        size=2 * half_window + 1,
        mode="reflect",
    )

    # Safe threshold: eps prevents flagging every frame if rolling_med collapses to 0
    eps     = np.finfo(np.float32).tiny
    flagged = subject_bin_energy > factor * np.maximum(rolling_med, eps)

    return flagged.astype(bool, copy=False), subject_bin_energy


def _flag_subject_bin_dropout(
    energy: np.ndarray,
    dropout_factor: float,
    half_window: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Flag frames where subject-bin energy drops below dropout_factor × rolling median.

    Inverse of the motion-spike detector: a dropout means the subject signal
    at the locked range bin is abnormally weak — the subject may have shifted
    out of the bin, a radar null may have formed, or a glitch suppressed the
    return.

    Only flags when the rolling median itself is meaningful (> eps): an all-zero
    or all-near-zero session does not produce false dropout flags.

    Parameters
    ----------
    energy         : float32 (N,) — pre-computed subject-bin energy series.
                     Typically from _subject_bin_energy or reused from motion_spike.
    dropout_factor : flag when energy < dropout_factor × rolling_median.
                     Must be in (0, 1).
    half_window    : half-width of rolling median window (full = 2*half_window+1).
                     Must be >= 0.

    Returns
    -------
    flagged : bool (N,)    — True = dropout detected
    energy  : float32 (N,) — input energy, guaranteed float32
    """
    if not (0.0 < dropout_factor < 1.0):
        raise ValueError(
            f"dropout_factor must be in (0, 1), got {dropout_factor}"
        )
    if half_window < 0:
        raise ValueError(f"half_window must be >= 0, got {half_window}")

    energy = np.asarray(energy, dtype=np.float32)

    rolling_med = _ndimage.median_filter(
        energy,
        size=2 * half_window + 1,
        mode="reflect",
    )

    # Only flag when the baseline is meaningful (> eps).
    # This prevents false flags when the entire session is near-zero.
    eps            = np.finfo(np.float32).tiny
    baseline_valid = rolling_med > eps
    flagged        = baseline_valid & (energy < dropout_factor * rolling_med)

    return flagged.astype(bool, copy=False), energy.astype(np.float32, copy=False)


def _flag_subject_bin_snr(
    cube: np.ndarray,
    locked_bin: int,
    snr_threshold: float,
    guard_half_width: int,
    *,
    chunk_frames: int | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Flag frames where subject-bin SNR falls below the threshold.

    SNR is defined as:
        subject_bin_energy / median(background_bin_energies)

    where subject_bin_energy = mean(|FFT[locked_bin]|²) over chirps and RX,
    and background_bin_energies are the per-bin mean energies for all bins
    outside the guard band [locked_bin - guard_half_width,
    locked_bin + guard_half_width].

    Median (not mean) is used for the background to stay robust against DC
    leakage (bins 0–1 are always elevated) and incidental reflectors.

    Uses scipy.fft.fft for complex64 input, scipy.fft.rfft for real input.

    Parameters
    ----------
    cube             : (N, chirps, rx, samples) complex64 or float32
    locked_bin       : subject range-bin index. Valid range: [0, samples-1]
                       for complex; [0, samples//2] for real.
    snr_threshold    : flag when SNR < this value. Must be > 0.
    guard_half_width : bins to exclude on each side of locked_bin when
                       computing the background median. Must be >= 0.
    chunk_frames     : frames per FFT chunk. None = all at once.

    Returns
    -------
    flagged          : bool (N,)     — True = SNR below threshold
    subject_bin_snr  : float32 (N,) — per-frame SNR values
    """
    if not isinstance(locked_bin, (int, np.integer)):
        raise ValueError(f"locked_bin must be an integer, got {type(locked_bin)}")
    if snr_threshold <= 0:
        raise ValueError(f"snr_threshold must be > 0, got {snr_threshold}")
    if guard_half_width < 0:
        raise ValueError(f"guard_half_width must be >= 0, got {guard_half_width}")
    if chunk_frames is not None and chunk_frames <= 0:
        raise ValueError(f"chunk_frames must be > 0, got {chunk_frames}")

    n_frames, _, _, n_samples = cube.shape
    is_complex = np.iscomplexobj(cube)
    n_bins = n_samples if is_complex else (n_samples // 2 + 1)

    if not (0 <= int(locked_bin) < n_bins):
        raise ValueError(
            f"locked_bin={locked_bin} is out of range for FFT output size {n_bins} "
            f"({'complex' if is_complex else 'real'} input, {n_samples} samples)"
        )

    fft_fn = _fft if is_complex else _rfft

    # Background mask: all bins except the guard band around locked_bin
    lo = max(0, int(locked_bin) - guard_half_width)
    hi = min(n_bins - 1, int(locked_bin) + guard_half_width)
    background_mask = np.ones(n_bins, dtype=bool)
    background_mask[lo : hi + 1] = False
    if not background_mask.any():
        # Guard band covers the entire FFT — fall back to all bins except locked_bin
        background_mask[:] = True
        background_mask[int(locked_bin)] = False

    eps = np.finfo(np.float32).tiny

    # Accumulate per-frame (subject_energy, background_median) chunk-by-chunk
    subject_energy   = np.empty(n_frames, dtype=np.float32)
    background_meds  = np.empty(n_frames, dtype=np.float32)

    step = chunk_frames if chunk_frames is not None else n_frames
    for start in range(0, n_frames, step):
        stop   = min(start + step, n_frames)
        chunk  = cube[start:stop]                          # (c, chirps, rx, samples)
        fft_c  = fft_fn(chunk, axis=3)                    # (c, chirps, rx, n_bins)
        # Mean |FFT|² over chirps and RX for every bin → (c, n_bins)
        bin_e  = np.mean(np.abs(fft_c) ** 2, axis=(1, 2)).astype(np.float32)
        subject_energy[start:stop]  = bin_e[:, int(locked_bin)]
        background_meds[start:stop] = np.median(bin_e[:, background_mask], axis=1)

    snr     = (subject_energy / np.maximum(background_meds, eps)).astype(np.float32)
    flagged = snr < snr_threshold

    return flagged.astype(bool, copy=False), snr


def _subject_bin_complex(
    cube: np.ndarray,
    locked_bin: int,
    *,
    chunk_frames: int | None = None,
) -> np.ndarray:
    """Compute per-frame mean complex FFT value at locked_bin, averaged over chirps/RX.

    Returns the complex phasor series used by the phase-jump detector:
        z[i] = mean(FFT[i, :, :, locked_bin], axis=(chirps, rx))

    Uses scipy.fft.fft for complex64 input and scipy.fft.rfft for real input.
    This matches the phase signal only if downstream phase extraction uses the
    same mean-over-chirps-and-RX construction. If the downstream pipeline uses
    a different aggregation (e.g. a specific chirp, beamforming weights), this
    helper must be adapted to match it.

    Parameters
    ----------
    cube         : (N, chirps, rx, samples) complex64 or float32
    locked_bin   : range-FFT bin index.
                   Valid range: [0, samples-1] for complex; [0, samples//2] for real.
    chunk_frames : frames per FFT chunk. None = all at once.

    Returns
    -------
    z : complex64 (N,)
    """
    if not isinstance(locked_bin, (int, np.integer)):
        raise ValueError(f"locked_bin must be an integer, got {type(locked_bin)}")
    if chunk_frames is not None and chunk_frames <= 0:
        raise ValueError(f"chunk_frames must be > 0, got {chunk_frames}")

    n_frames, _, _, n_samples = cube.shape
    is_complex = np.iscomplexobj(cube)
    n_bins     = n_samples if is_complex else (n_samples // 2 + 1)

    if not (0 <= int(locked_bin) < n_bins):
        raise ValueError(
            f"locked_bin={locked_bin} is out of range for FFT output size {n_bins} "
            f"({'complex' if is_complex else 'real'} input, {n_samples} samples)"
        )

    fft_fn = _fft if is_complex else _rfft
    z      = np.empty(n_frames, dtype=np.complex64)
    step   = chunk_frames if chunk_frames is not None else n_frames

    for start in range(0, n_frames, step):
        stop      = min(start + step, n_frames)
        chunk     = cube[start:stop]
        fft_chunk = fft_fn(chunk, axis=3)
        bin_col   = fft_chunk[:, :, :, int(locked_bin)]        # (c, chirps, rx)
        z[start:stop] = np.mean(bin_col, axis=(1, 2)).astype(np.complex64)

    return z.astype(np.complex64, copy=False)


def _subject_bin_phase_delta(
    cube: np.ndarray,
    locked_bin: int,
    *,
    method: str = "mean_phasor",
    chunk_frames: int | None = None,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Compute per-frame phase delta at locked_bin using the chosen method.

    Parameters
    ----------
    cube         : (N, chirps, rx, samples) complex64 or float32
    locked_bin   : range-FFT bin index (same validation as _subject_bin_complex)
    method       : "mean_phasor" — average phasors first, then compute conjugate-product
                                   delta. Returns complex_series.
                   "delta_before_mean" — compute per-chirp/RX conjugate-product deltas
                                         first, then average. More stable when static
                                         phase offsets across RX channels partially cancel
                                         the mean phasor.
    chunk_frames : frames per FFT chunk. None = all at once.
                   For "delta_before_mean", chunk boundaries are handled correctly:
                   frame start of each chunk compares against the last frame of the
                   previous chunk, never resetting to zero mid-session.

    Returns
    -------
    phase_delta     : float32 (N,) — signed wrapped frame-to-frame phase difference (rad).
                      phase_delta[0] = 0 always (no predecessor for the first frame).
    complex_series  : complex64 (N,) when method="mean_phasor", else None.
    """
    if method not in ("mean_phasor", "delta_before_mean"):
        raise ValueError(
            f"phase_jump.method must be 'mean_phasor' or 'delta_before_mean', "
            f"got {method!r}"
        )
    if not isinstance(locked_bin, (int, np.integer)):
        raise ValueError(f"locked_bin must be an integer, got {type(locked_bin)}")
    if chunk_frames is not None and chunk_frames <= 0:
        raise ValueError(f"chunk_frames must be > 0, got {chunk_frames}")

    n_frames, _, _, n_samples = cube.shape
    is_complex = np.iscomplexobj(cube)
    n_bins     = n_samples if is_complex else (n_samples // 2 + 1)

    if not (0 <= int(locked_bin) < n_bins):
        raise ValueError(
            f"locked_bin={locked_bin} is out of range for FFT output size {n_bins} "
            f"({'complex' if is_complex else 'real'} input, {n_samples} samples)"
        )

    fft_fn = _fft if is_complex else _rfft

    if method == "mean_phasor":
        z = _subject_bin_complex(cube, locked_bin, chunk_frames=chunk_frames)
        phase_delta = np.zeros(n_frames, dtype=np.float32)
        if n_frames > 1:
            phase_delta[1:] = np.angle(z[1:] * np.conj(z[:-1])).astype(np.float32)
        return phase_delta, z

    # method == "delta_before_mean"
    # For each frame i: compute bin_i * conj(bin_{i-1}) per (chirp, rx), then average.
    # Chunk boundary: the first frame of each non-first chunk uses the last FFT bin
    # values of the previous chunk as its predecessor — stored in prev_last_bin.
    phase_delta: np.ndarray = np.zeros(n_frames, dtype=np.float32)
    step = chunk_frames if chunk_frames is not None else n_frames
    prev_last_bin: np.ndarray | None = None  # shape (chirps, rx), complex

    for start in range(0, n_frames, step):
        stop      = min(start + step, n_frames)
        chunk     = cube[start:stop]
        fft_chunk = fft_fn(chunk, axis=3)
        bin_chunk = fft_chunk[:, :, :, int(locked_bin)]  # (c_len, chirps, rx)
        c_len     = stop - start
        chunk_delta = np.zeros(c_len, dtype=np.float32)

        if prev_last_bin is not None:
            # Build predecessor array: prev_last_bin for frame 0, bin_chunk[k-1] for k>0
            extended_prev = np.concatenate(
                [prev_last_bin[np.newaxis], bin_chunk[:-1]], axis=0
            )  # (c_len, chirps, rx)
            dp = bin_chunk * np.conj(extended_prev)
            chunk_delta[:] = np.angle(
                np.mean(dp, axis=(1, 2))
            ).astype(np.float32)
        else:
            # First chunk: frame 0 has no predecessor → delta stays 0
            if c_len > 1:
                dp = bin_chunk[1:] * np.conj(bin_chunk[:-1])
                chunk_delta[1:] = np.angle(
                    np.mean(dp, axis=(1, 2))
                ).astype(np.float32)

        phase_delta[start:stop] = chunk_delta
        prev_last_bin = bin_chunk[-1].copy()  # (chirps, rx)

    return phase_delta, None


def _flag_phase_jump_from_delta(
    phase_delta: np.ndarray,
    threshold_rad: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Flag frames where the absolute wrapped phase delta exceeds threshold_rad.

    Parameters
    ----------
    phase_delta   : float32 (N,) — signed wrapped frame-to-frame phase difference.
                    phase_delta[0] must be 0 (no predecessor for frame 0).
    threshold_rad : flag when |phase_delta[i]| > threshold. Must be > 0.

    Returns
    -------
    phase_jump_flagged : bool (N,)    — True = rotation exceeds threshold (strict >)
    phase_diff         : float32 (N,) — abs(phase_delta), non-negative
    phase_delta        : float32 (N,) — input coerced to float32
    """
    if threshold_rad <= 0:
        raise ValueError(f"threshold_rad must be > 0, got {threshold_rad}")
    phase_delta = np.asarray(phase_delta, dtype=np.float32)
    phase_diff  = np.abs(phase_delta).astype(np.float32, copy=False)
    phase_jump_flagged = phase_diff > threshold_rad
    return (
        phase_jump_flagged.astype(bool, copy=False),
        phase_diff.astype(np.float32, copy=False),
        phase_delta,
    )


def _flag_phase_jump(
    complex_series: np.ndarray,
    threshold_rad: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Flag frames where the wrapped frame-to-frame phasor rotation exceeds threshold_rad.

    Uses the conjugate-product method:
        phase_delta[i] = angle(z[i] * conj(z[i-1]))

    This is numerically stable and correctly handles the ±π wrap boundary (a
    true 0.1 rad step near +π appears as 0.1 rad, not as ~6.2 rad).

    phase_delta[0] and phase_diff[0] are always 0 — the first frame has no
    preceding frame to compare against.

    Parameters
    ----------
    complex_series : complex64 (N,) — per-frame mean phasor at locked bin.
                     From _subject_bin_complex(). Near-zero or zero phasors
                     do not crash (np.angle(0) = 0).
    threshold_rad  : flag when |phase_delta[i]| > threshold. Must be > 0.

    Returns
    -------
    phase_jump_flagged : bool (N,)    — True = rotation exceeds threshold
    phase_diff         : float32 (N,) — absolute phase difference per frame (rad)
    phase_delta        : float32 (N,) — signed wrapped phase difference per frame (rad)
    """
    z = np.asarray(complex_series, dtype=np.complex64)
    n = len(z)
    phase_delta = np.zeros(n, dtype=np.float32)
    if n > 1:
        phase_delta[1:] = np.angle(z[1:] * np.conj(z[:-1])).astype(np.float32)
    return _flag_phase_jump_from_delta(phase_delta, threshold_rad)


def _phase_diff_stats(phase_diff: np.ndarray) -> dict[str, float] | None:
    """Compute summary statistics for a phase_diff array, using finite values only.

    Returns None if no finite values exist (e.g. all-NaN input).
    """
    arr    = np.asarray(phase_diff, dtype=np.float32)
    finite = arr[np.isfinite(arr)]
    if len(finite) == 0:
        return None
    return {
        "min":  float(np.min(finite)),
        "p50":  float(np.percentile(finite, 50)),
        "p90":  float(np.percentile(finite, 90)),
        "p95":  float(np.percentile(finite, 95)),
        "p99":  float(np.percentile(finite, 99)),
        "max":  float(np.max(finite)),
        "mean": float(np.mean(finite)),
    }


def _soft_failure_overlap_counts(results: dict) -> dict[str, int]:
    """Compute pairwise and multi-way overlap counts between soft-failure flags.

    Considers the four soft-failure arrays when present in results:
        motion_spike_flagged, snr_flagged, subject_bin_dropout_flagged, phase_jump_flagged

    Returns {} when fewer than two arrays are present.

    Keys in the returned dict:
        <name>           — total frames flagged by this detector
        <name>_only      — frames flagged by this detector and no other
        <a>_and_<b>      — pairwise intersection (and higher-order combinations)
        soft_failure_union — union of all present flags
    """
    _candidates = [
        ("motion",     "motion_spike_flagged"),
        ("snr",        "snr_flagged"),
        ("dropout",    "subject_bin_dropout_flagged"),
        ("phase_jump", "phase_jump_flagged"),
    ]
    present = [(name, results[key]) for name, key in _candidates if key in results]

    if len(present) < 2:
        return {}

    counts: dict[str, int] = {}

    # Individual totals
    for name, arr in present:
        counts[name] = int(arr.sum())

    # "Only" counts: flagged by this one and no other
    for i, (name, arr) in enumerate(present):
        only = arr.copy()
        for j, (_, other) in enumerate(present):
            if j != i:
                only = only & ~other
        counts[f"{name}_only"] = int(only.sum())

    # All r-way combinations for r >= 2 (pairwise + higher-order)
    for r in range(2, len(present) + 1):
        for combo in _combinations(present, r):
            key  = "_and_".join(c[0] for c in combo)
            mask = combo[0][1].copy()
            for _, arr in combo[1:]:
                mask = mask & arr
            counts[key] = int(mask.sum())

    # Union of all flags
    union = present[0][1].copy()
    for _, arr in present[1:]:
        union = union | arr
    counts["soft_failure_union"] = int(union.sum())

    return counts


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

_CONFIDENCE_ORDER = {"high": 2, "medium": 1, "low": 0}
_STEP3_REQUIRED   = [
    "locked_bin", "locked_range_m",
    "chest_bin_confidence", "chest_bin_review_required",
]


def _compute_quality_mask(
    cube: np.ndarray,
    cfg: dict,
    locked_bin: int | None = None,
    chunk_frames: int | None = None,
) -> dict:
    """Run all enabled checks and return results dict.

    cube        : complex64, shape (N_analysis, chirps, rx, adc_samples)
    locked_bin  : subject range-bin index from manifest (Step 3 output).
    chunk_frames: frames per FFT chunk for all checks. None = all at once.

    Always-present keys in the returned dict:
        quality_mask   : bool (N,) — True = healthy
        max_abs_adc    : float32 (N,)
        frame_energy   : float32 (N,)
        clip_flagged   : bool (N,)
        dead_flagged   : bool (N,)

    Present when soft_failures.rx_imbalance block exists in cfg:
        rx_energy_ratio       : float32 (N,)
        rx_imbalance_flagged  : bool (N,)

    Present when soft_failures.motion_spike block exists in cfg AND check ran:
        subject_bin_energy    : float32 (N,)
        motion_spike_flagged  : bool (N,)

    Extra informational keys when motion_spike block exists:
        motion_spike_skipped     : bool
        motion_spike_skip_reason : str | None
        motion_spike_locked_bin  : int | None  — effective bin used (or None if skipped)

    Present when soft_failures.subject_bin_snr block exists in cfg AND check ran:
        subject_bin_snr       : float32 (N,)
        snr_flagged           : bool (N,)

    Extra informational keys when subject_bin_snr block exists:
        snr_skipped           : bool
        snr_skip_reason       : str | None
        snr_locked_bin        : int | None

    Present when soft_failures.subject_bin_dropout block exists in cfg AND check ran:
        subject_bin_dropout_flagged : bool (N,)
        (subject_bin_energy is reused or written once — never duplicated)

    Extra informational keys when subject_bin_dropout block exists:
        dropout_skipped       : bool
        dropout_skip_reason   : str | None
        dropout_locked_bin    : int | None   — only set when fallback energy was computed
        dropout_energy_source : str          — "reused_subject_bin_energy" | "computed_fallback"

    Present when soft_failures.phase_jump block exists in cfg AND check ran:
        phase_diff             : float32 (N,) — abs frame-to-frame phasor rotation (rad)
        phase_delta            : float32 (N,) — signed frame-to-frame phasor rotation (rad)
        phase_jump_flagged     : bool (N,)

    Extra informational keys when phase_jump block exists:
        phase_jump_skipped     : bool
        phase_jump_skip_reason : str | None
        phase_jump_locked_bin  : int | None
    """
    hf = cfg["hard_failures"]

    clip_flagged, max_abs_adc  = _flag_clipping(cube, float(hf["clip_threshold"]))
    dead_flagged, frame_energy = _flag_dead(cube, float(hf["dead_frame_fraction"]))

    flagged = clip_flagged | dead_flagged

    results: dict = {
        "max_abs_adc":  max_abs_adc,
        "frame_energy": frame_energy,
        "clip_flagged": clip_flagged,
        "dead_flagged": dead_flagged,
    }

    n_samples  = cube.shape[3]
    is_complex = np.iscomplexobj(cube)
    n_bins     = n_samples if is_complex else (n_samples // 2 + 1)

    def _resolve_bin() -> tuple[int | None, bool, str | None]:
        """Validate locked_bin against FFT output size. Returns (eff, skipped, reason)."""
        if locked_bin is None:
            return None, True, "missing locked_bin (not in manifest)"
        eff = int(locked_bin)
        if not (0 <= eff < n_bins):
            return None, True, (
                f"manifest locked_bin={eff} out of FFT range [0, {n_bins - 1}]"
            )
        return eff, False, None

    # -- RX channel imbalance (no locked_bin needed) --------------------------
    rx_cfg = cfg.get("soft_failures", {}).get("rx_imbalance", {})
    if rx_cfg:
        rx_flagged, rx_ratio = _flag_rx_imbalance(
            cube, float(rx_cfg["rx_imbalance_ratio"])
        )
        results["rx_imbalance_flagged"] = rx_flagged
        results["rx_energy_ratio"]      = rx_ratio
        if rx_cfg.get("enabled", False):
            flagged |= rx_flagged

    # -- Motion spike (subject-bin energy burst detector) ---------------------
    ms_cfg = cfg.get("soft_failures", {}).get("motion_spike", {})
    if ms_cfg:
        eff_bin, skipped, skip_reason = _resolve_bin()
        if skipped:
            print(f"  WARNING: motion_spike check skipped — {skip_reason}")
        results["motion_spike_skipped"]     = skipped
        results["motion_spike_skip_reason"] = skip_reason
        results["motion_spike_locked_bin"]  = eff_bin
        if not skipped:
            ms_flagged, bin_energy = _flag_motion_spike(
                cube,
                locked_bin   = eff_bin,
                factor       = float(ms_cfg["motion_spike_factor"]),
                half_window  = int(ms_cfg["motion_window_frames"]),
                chunk_frames = chunk_frames,
            )
            results["subject_bin_energy"]   = bin_energy
            results["motion_spike_flagged"] = ms_flagged
            if ms_cfg.get("enabled", False):
                flagged |= ms_flagged

    # -- Subject-bin SNR ------------------------------------------------------
    snr_cfg = cfg.get("soft_failures", {}).get("subject_bin_snr", {})
    if snr_cfg:
        eff_bin, skipped, skip_reason = _resolve_bin()
        if skipped:
            print(f"  WARNING: subject_bin_snr check skipped — {skip_reason}")
        results["snr_skipped"]     = skipped
        results["snr_skip_reason"] = skip_reason
        results["snr_locked_bin"]  = eff_bin
        if not skipped:
            snr_flagged, snr_vals = _flag_subject_bin_snr(
                cube,
                locked_bin       = eff_bin,
                snr_threshold    = float(snr_cfg["snr_threshold"]),
                guard_half_width = int(snr_cfg["guard_half_width"]),
                chunk_frames     = chunk_frames,
            )
            results["subject_bin_snr"] = snr_vals
            results["snr_flagged"]     = snr_flagged
            if snr_cfg.get("enabled", False):
                flagged |= snr_flagged

    # -- Subject-bin energy dropout -------------------------------------------
    do_cfg = cfg.get("soft_failures", {}).get("subject_bin_dropout", {})
    if do_cfg:
        skipped       = False
        skip_reason   = None
        energy_source = None
        do_locked_bin = None

        if "subject_bin_energy" in results:
            energy_source = "reused_subject_bin_energy"
        else:
            eff_bin, skipped, skip_reason = _resolve_bin()
            if skipped:
                if skip_reason and "missing" in skip_reason:
                    skip_reason += " and no reusable subject_bin_energy"
                print(f"  WARNING: subject_bin_dropout check skipped — {skip_reason}")
            else:
                energy = _subject_bin_energy(cube, eff_bin, chunk_frames=chunk_frames)
                results["subject_bin_energy"] = energy
                do_locked_bin  = eff_bin
                energy_source  = "computed_fallback"

        results["dropout_skipped"]       = skipped
        results["dropout_skip_reason"]   = skip_reason
        results["dropout_energy_source"] = energy_source
        results["dropout_locked_bin"]    = do_locked_bin

        if not skipped:
            dropout_flagged, _ = _flag_subject_bin_dropout(
                results["subject_bin_energy"],
                dropout_factor = float(do_cfg["dropout_factor"]),
                half_window    = int(do_cfg["dropout_window_frames"]),
            )
            results["subject_bin_dropout_flagged"] = dropout_flagged
            if do_cfg.get("enabled", False):
                flagged |= dropout_flagged

    # -- Phase jump detector --------------------------------------------------
    pj_cfg = cfg.get("soft_failures", {}).get("phase_jump", {})
    if pj_cfg:
        method = pj_cfg.get("method", "mean_phasor")
        if method not in ("mean_phasor", "delta_before_mean"):
            raise ValueError(
                f"phase_jump.method must be 'mean_phasor' or 'delta_before_mean', "
                f"got {method!r}"
            )
        eff_bin, skipped, skip_reason = _resolve_bin()
        if skipped:
            print(f"  WARNING: phase_jump check skipped — {skip_reason}")
        results["phase_jump_skipped"]     = skipped
        results["phase_jump_skip_reason"] = skip_reason
        results["phase_jump_locked_bin"]  = eff_bin
        results["phase_jump_method"]      = method
        if not skipped:
            phase_delta_arr, _ = _subject_bin_phase_delta(
                cube, eff_bin,
                method=method,
                chunk_frames=chunk_frames,
            )
            pj_flagged, phase_diff, phase_delta_arr = _flag_phase_jump_from_delta(
                phase_delta_arr, threshold_rad=float(pj_cfg["threshold_rad"])
            )
            results["phase_diff"]         = phase_diff
            results["phase_delta"]        = phase_delta_arr
            results["phase_jump_flagged"] = pj_flagged
            results["phase_diff_stats"]   = _phase_diff_stats(phase_diff)
            if pj_cfg.get("enabled", False):
                flagged |= pj_flagged

    results["quality_mask"] = ~flagged
    return results


def _run_stats(results: dict, trim_frames: int, frame_rate_hz: float) -> dict:
    """Compute summary statistics for logging."""
    mask   = results["quality_mask"]
    n      = len(mask)
    n_bad  = int((~mask).sum())
    n_clip = int(results["clip_flagged"].sum())
    n_dead = int(results["dead_flagged"].sum())
    n_rx = int(results["rx_imbalance_flagged"].sum()) \
           if "rx_imbalance_flagged" in results else None
    # motion_spike: None when block absent or check was skipped
    n_ms  = int(results["motion_spike_flagged"].sum()) \
            if "motion_spike_flagged" in results else None
    n_snr     = int(results["snr_flagged"].sum()) \
                if "snr_flagged" in results else None
    n_dropout   = int(results["subject_bin_dropout_flagged"].sum()) \
                  if "subject_bin_dropout_flagged" in results else None
    n_phase_jump = int(results["phase_jump_flagged"].sum()) \
                   if "phase_jump_flagged" in results else None

    def _longest_run(arr: np.ndarray) -> int:
        if not arr.any():
            return 0
        maxrun = cur = 0
        for v in arr:
            cur = cur + 1 if v else 0
            maxrun = max(maxrun, cur)
        return maxrun

    longest_bad  = _longest_run(~mask)
    longest_good = _longest_run(mask)

    return {
        "n_analysis":            n,
        "trim_frames":           trim_frames,
        "trim_s":                trim_frames / frame_rate_hz,
        "analysis_s":            n / frame_rate_hz,
        "n_flagged":             n_bad,
        "pct_flagged":           100.0 * n_bad / n if n else 0.0,
        "n_clip":                n_clip,
        "n_dead":                n_dead,
        "n_rx_imbalance":        n_rx,
        "n_motion_spike":        n_ms,
        "n_snr":                 n_snr,
        "n_dropout":             n_dropout,
        "n_phase_jump":          n_phase_jump,
        "longest_bad_run":       longest_bad,
        "longest_good_run":      longest_good,
        "phase_diff_stats":      results.get("phase_diff_stats"),
        "soft_failure_overlaps": _soft_failure_overlap_counts(results),
    }


def _write_diagnostic_log(
    session_id: str,
    stats: dict,
    cfg: dict,
    commit: str,
    step3_meta: dict,
    *,
    no_write: bool,
    source_bin_files: str,
) -> None:
    """Write results/<session_id>/step_4/step_4.log (overwritten each run)."""
    log_dir = REPO_ROOT / "results" / session_id / "step_4"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "step_4.log"

    hf  = cfg["hard_failures"]
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    SEP = "=" * 72

    lines = [
        SEP,
        f"Step 4 diagnostic log -- {session_id}",
        f"Generated    : {now}",
        f"Git commit   : {commit}",
        SEP,
        "",
        "PROVENANCE",
        f"  source_bin_files     : {source_bin_files}",
        f"  HDF5 write           : {'skipped (--no-write)' if no_write else 'written'}",
        "",
        "STEP 3 INTAKE",
        f"  locked_bin           : {step3_meta.get('locked_bin')}",
        f"  locked_range_m       : {step3_meta.get('locked_range_m')}",
        f"  chest_bin_confidence : {step3_meta.get('confidence')}",
        f"  review_required      : {step3_meta.get('review_required')}",
        f"  step3_run_id         : {step3_meta.get('run_id')}",
        "",
        "ANALYSIS WINDOW",
        f"  trim_frames          : {stats['trim_frames']}  ({stats['trim_s']:.1f} s)",
        f"  analysis_frames      : {stats['n_analysis']}  ({stats['analysis_s']:.1f} s)",
        "",
        "HARD FAILURES",
        f"  Clipping  (threshold={hf['clip_threshold']})        : {stats['n_clip']} frames",
        f"  Dead      (fraction={hf['dead_frame_fraction']}) : {stats['n_dead']} frames",
    ]

    soft_lines = []
    rx_cfg = cfg.get("soft_failures", {}).get("rx_imbalance", {})
    if rx_cfg and stats["n_rx_imbalance"] is not None:
        enabled_str = "enabled" if rx_cfg.get("enabled", False) else "disabled"
        soft_lines.append(
            f"  RX imbalance (ratio={rx_cfg['rx_imbalance_ratio']}, {enabled_str}) : "
            f"{stats['n_rx_imbalance']} frames"
        )

    ms_cfg = cfg.get("soft_failures", {}).get("motion_spike", {})
    if ms_cfg:
        enabled_str = "enabled" if ms_cfg.get("enabled", False) else "disabled"
        factor      = ms_cfg["motion_spike_factor"]
        hw          = ms_cfg["motion_window_frames"]
        eff_bin     = stats.get("motion_spike_locked_bin")
        if stats.get("motion_spike_skipped", True):
            reason = stats.get("motion_spike_skip_reason", "unknown")
            soft_lines.append(
                f"  Motion spike ({enabled_str}, factor={factor}, "
                f"half_window={hw}): skipped — {reason}"
            )
        else:
            soft_lines.append(
                f"  Motion spike ({enabled_str}, factor={factor}, "
                f"half_window={hw}, locked_bin={eff_bin}): "
                f"{stats['n_motion_spike']} frames"
            )

    snr_cfg = cfg.get("soft_failures", {}).get("subject_bin_snr", {})
    if snr_cfg:
        enabled_str = "enabled" if snr_cfg.get("enabled", False) else "disabled"
        threshold   = snr_cfg["snr_threshold"]
        ghw         = snr_cfg["guard_half_width"]
        eff_bin     = stats.get("snr_locked_bin")
        if stats.get("snr_skipped", True):
            reason = stats.get("snr_skip_reason", "unknown")
            soft_lines.append(
                f"  Subject-bin SNR ({enabled_str}, threshold={threshold}, "
                f"guard={ghw}): skipped — {reason}"
            )
        else:
            soft_lines.append(
                f"  Subject-bin SNR ({enabled_str}, threshold={threshold}, "
                f"guard={ghw}, locked_bin={eff_bin}): "
                f"{stats['n_snr']} frames"
            )

    do_cfg = cfg.get("soft_failures", {}).get("subject_bin_dropout", {})
    if do_cfg:
        enabled_str   = "enabled" if do_cfg.get("enabled", False) else "disabled"
        factor        = do_cfg["dropout_factor"]
        hw            = do_cfg["dropout_window_frames"]
        energy_source = stats.get("dropout_energy_source")
        do_locked_bin = stats.get("dropout_locked_bin")
        if stats.get("dropout_skipped", True):
            reason = stats.get("dropout_skip_reason", "unknown")
            soft_lines.append(
                f"  Subject-bin dropout ({enabled_str}, factor={factor}, "
                f"half_window={hw}): skipped — {reason}"
            )
        else:
            src_note = f", energy={energy_source}" if energy_source else ""
            bin_note = f", locked_bin={do_locked_bin}" if do_locked_bin is not None else ""
            soft_lines.append(
                f"  Subject-bin dropout ({enabled_str}, factor={factor}, "
                f"half_window={hw}{src_note}{bin_note}): "
                f"{stats['n_dropout']} frames"
            )

    pj_cfg = cfg.get("soft_failures", {}).get("phase_jump", {})
    if pj_cfg:
        enabled_str = "enabled" if pj_cfg.get("enabled", False) else "disabled"
        thr         = pj_cfg["threshold_rad"]
        method      = pj_cfg.get("method", "mean_phasor")
        pj_bin      = stats.get("phase_jump_locked_bin")
        if stats.get("phase_jump_skipped", True):
            reason = stats.get("phase_jump_skip_reason", "unknown")
            soft_lines.append(
                f"  Phase jump ({enabled_str}, threshold={thr} rad, "
                f"method={method}): skipped — {reason}"
            )
        else:
            soft_lines.append(
                f"  Phase jump ({enabled_str}, threshold={thr} rad, "
                f"method={method}, locked_bin={pj_bin}): "
                f"{stats['n_phase_jump']} frames"
            )
            pd_stats = stats.get("phase_diff_stats")
            if pd_stats:
                soft_lines += [
                    "  Phase-diff stats:",
                    f"    p50 / p90 / p95 / p99 / max : "
                    f"{pd_stats['p50']:.2f} / {pd_stats['p90']:.2f} / "
                    f"{pd_stats['p95']:.2f} / {pd_stats['p99']:.2f} / "
                    f"{pd_stats['max']:.2f} rad",
                ]

    overlaps = stats.get("soft_failure_overlaps", {})
    if overlaps:
        _pretty = {
            "motion": "Motion", "snr": "SNR",
            "dropout": "Dropout", "phase_jump": "Phase jump",
        }
        n_flags = sum(
            1 for k in ("motion", "snr", "dropout", "phase_jump")
            if k in overlaps
        )

        def _olabel(key: str) -> str:
            if key == "soft_failure_union":
                return "Soft-failure union"
            parts = key.split("_and_")
            if len(parts) == 1:
                if key.endswith("_only"):
                    base = key[:-5]
                    return _pretty.get(base, base.title()) + " only"
                return _pretty.get(key, key.title())
            if len(parts) == n_flags:
                return "All soft failures"
            return " + ".join(_pretty.get(p, p.title()) for p in parts)

        overlap_lines = ["  Soft-failure overlaps:"]
        for k in [x for x in overlaps if x.endswith("_only")]:
            overlap_lines.append(f"    {_olabel(k):<28}: {overlaps[k]} frames")
        for k in [x for x in overlaps if "_and_" in x and x.count("_and_") == 1]:
            overlap_lines.append(f"    {_olabel(k):<28}: {overlaps[k]} frames")
        for k in [x for x in overlaps if "_and_" in x and x.count("_and_") > 1]:
            overlap_lines.append(f"    {_olabel(k):<28}: {overlaps[k]} frames")
        if "soft_failure_union" in overlaps:
            overlap_lines.append(
                f"    {'Soft-failure union':<28}: {overlaps['soft_failure_union']} frames"
            )
        soft_lines += overlap_lines

    if soft_lines:
        lines += ["", "SOFT FAILURES"] + soft_lines

    lines += [
        "",
        "SUMMARY",
        f"  Total flagged    : {stats['n_flagged']} / {stats['n_analysis']} "
        f"({stats['pct_flagged']:.2f}%)",
        f"  Longest bad run  : {stats['longest_bad_run']} frames",
        f"  Longest good run : {stats['longest_good_run']} frames",
        "",
        SEP,
    ]

    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Diagnostic log:  {log_path}")


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def _process_session(
    session_id: str,
    row: "pd.Series",
    cfg: dict,
    commit: str,
    cubes_dir: Path,
    chunk_frames: int | None,
    step3_meta: dict,
    *,
    no_write: bool = False,
    overwrite: bool = False,
) -> dict:
    """Process one session. Returns stats dict."""
    h5_path = cubes_dir / f"{session_id}.h5"
    if not h5_path.exists():
        raise FileNotFoundError(f"{h5_path} not found — run Step 2 first")

    trim_frames      = int(cfg["analysis"]["trim_frames"])
    locked_bin       = step3_meta["locked_bin"]
    source_bin_files = "unknown"

    mode = "r" if no_write else "a"
    with h5py.File(h5_path, mode) as f:
        frame_rate_hz    = float(f.attrs.get("frame_rate_hz", 20.0))
        total_frames     = int(f.attrs["num_frames"])
        source_bin_files = str(f.attrs.get("source_bin_files", "unknown"))

        if not no_write:
            has_qm  = "quality_mask"    in f
            has_qmt = "quality_metrics" in f
            if (has_qm or has_qmt) and not overwrite:
                raise FileExistsError(
                    f"{h5_path.name} already has /quality_mask or /quality_metrics. "
                    "Re-run with --overwrite to replace, or use --no-write for a dry run."
                )

        n_analysis = total_frames - trim_frames
        if n_analysis <= 0:
            raise ValueError(
                f"trim_frames={trim_frames} >= total_frames={total_frames} for {session_id}"
            )

        print(f"  Total frames : {total_frames}  |  analysis : {n_analysis} "
              f"(frames {trim_frames}–{total_frames - 1})")
        print(f"  locked_bin   : {locked_bin}  "
              f"(confidence: {step3_meta['confidence']}  "
              f"review_required: {step3_meta['review_required']})")

        cube = f["cube"][trim_frames:].astype(np.complex64)

        results = _compute_quality_mask(
            cube, cfg, locked_bin=locked_bin, chunk_frames=chunk_frames
        )
        stats = _run_stats(results, trim_frames, frame_rate_hz)

        # Carry per-check skip state and metadata into stats for logging
        for check_key, prefix in [
            ("motion_spike",        "motion_spike"),
            ("subject_bin_snr",     "snr"),
            ("subject_bin_dropout", "dropout"),
            ("phase_jump",          "phase_jump"),
        ]:
            if cfg.get("soft_failures", {}).get(check_key):
                for suffix in ("_skipped", "_skip_reason", "_locked_bin"):
                    k = f"{prefix}{suffix}"
                    if k in results:
                        stats[k] = results[k]
                if check_key == "subject_bin_dropout" and "dropout_energy_source" in results:
                    stats["dropout_energy_source"] = results["dropout_energy_source"]
                if check_key == "phase_jump" and "phase_jump_method" in results:
                    stats["phase_jump_method"] = results["phase_jump_method"]

        # Console summary
        print(f"  Clipping flagged : {stats['n_clip']}")
        print(f"  Dead flagged     : {stats['n_dead']}")
        if stats["n_rx_imbalance"] is not None:
            rx_cfg = cfg.get("soft_failures", {}).get("rx_imbalance", {})
            enabled_str = "enabled" if rx_cfg.get("enabled", False) else "disabled"
            print(f"  RX imbalance ({enabled_str}) : {stats['n_rx_imbalance']}")
        ms_cfg = cfg.get("soft_failures", {}).get("motion_spike", {})
        if ms_cfg:
            ms_enabled = "enabled" if ms_cfg.get("enabled", False) else "disabled"
            if stats.get("motion_spike_skipped", True):
                print(f"  Motion spike ({ms_enabled}) : skipped — "
                      f"{stats.get('motion_spike_skip_reason', 'unknown')}")
            else:
                print(f"  Motion spike ({ms_enabled}) : {stats['n_motion_spike']}")
        snr_cfg = cfg.get("soft_failures", {}).get("subject_bin_snr", {})
        if snr_cfg:
            snr_enabled = "enabled" if snr_cfg.get("enabled", False) else "disabled"
            if stats.get("snr_skipped", True):
                print(f"  Subject-bin SNR ({snr_enabled}) : skipped — "
                      f"{stats.get('snr_skip_reason', 'unknown')}")
            else:
                print(f"  Subject-bin SNR ({snr_enabled}) : {stats['n_snr']}")
        do_cfg = cfg.get("soft_failures", {}).get("subject_bin_dropout", {})
        if do_cfg:
            do_enabled = "enabled" if do_cfg.get("enabled", False) else "disabled"
            if stats.get("dropout_skipped", True):
                print(f"  Subject-bin dropout ({do_enabled}) : skipped — "
                      f"{stats.get('dropout_skip_reason', 'unknown')}")
            else:
                src = stats.get("dropout_energy_source", "?")
                print(f"  Subject-bin dropout ({do_enabled}, {src}) : {stats['n_dropout']}")
        pj_cfg = cfg.get("soft_failures", {}).get("phase_jump", {})
        if pj_cfg:
            pj_enabled = "enabled" if pj_cfg.get("enabled", False) else "disabled"
            if stats.get("phase_jump_skipped", True):
                print(f"  Phase jump ({pj_enabled}) : skipped — "
                      f"{stats.get('phase_jump_skip_reason', 'unknown')}")
            else:
                print(f"  Phase jump ({pj_enabled}) : {stats['n_phase_jump']}")
        print(f"  Total flagged    : {stats['n_flagged']} / {n_analysis} "
              f"({stats['pct_flagged']:.2f}%)")
        print(f"  Longest bad run  : {stats['longest_bad_run']} frames")

        if no_write:
            print("  HDF5 write skipped (--no-write)")
        else:
            hf      = cfg["hard_failures"]
            now_str = (datetime.datetime.now(datetime.timezone.utc)
                       .strftime("%Y-%m-%dT%H:%M:%SZ"))

            for ds_name in ("quality_mask", "quality_metrics"):
                if ds_name in f:
                    del f[ds_name]

            qm = f.create_dataset("quality_mask", data=results["quality_mask"])

            # Provenance
            qm.attrs["git_commit"]            = commit
            qm.attrs["created_at"]            = now_str
            qm.attrs["locked_bin"]            = int(locked_bin) if locked_bin is not None else -1
            qm.attrs["step3_confidence"]      = step3_meta["confidence"]
            qm.attrs["step3_run_id"]          = step3_meta["run_id"]
            qm.attrs["step3_review_required"] = step3_meta["review_required"]
            qm.attrs["source_bin_files"]      = source_bin_files
            qm.attrs["seed"]                  = int(cfg.get("seed", 42))
            qm.attrs["trim_frames"]           = trim_frames
            qm.attrs["chunk_frames"]          = chunk_frames if chunk_frames is not None else -1

            # Hard failure config
            qm.attrs["clip_threshold"]      = float(hf["clip_threshold"])
            qm.attrs["dead_frame_fraction"] = float(hf["dead_frame_fraction"])

            # Soft failure config attrs
            rx_cfg = cfg.get("soft_failures", {}).get("rx_imbalance", {})
            if rx_cfg:
                qm.attrs["rx_imbalance_enabled"] = bool(rx_cfg.get("enabled", False))
                qm.attrs["rx_imbalance_ratio"]   = float(rx_cfg["rx_imbalance_ratio"])
            ms_cfg = cfg.get("soft_failures", {}).get("motion_spike", {})
            if ms_cfg:
                qm.attrs["motion_spike_present"]     = True
                qm.attrs["motion_spike_enabled"]     = bool(ms_cfg.get("enabled", False))
                qm.attrs["motion_spike_factor"]      = float(ms_cfg["motion_spike_factor"])
                qm.attrs["motion_spike_half_window"] = int(ms_cfg["motion_window_frames"])
                qm.attrs["motion_spike_skipped"]     = bool(results.get("motion_spike_skipped", True))
                eff_bin = results.get("motion_spike_locked_bin")
                if eff_bin is not None:
                    qm.attrs["motion_spike_locked_bin"] = int(eff_bin)
                skip_reason = results.get("motion_spike_skip_reason")
                if skip_reason:
                    qm.attrs["motion_spike_skip_reason"] = str(skip_reason)
            snr_cfg = cfg.get("soft_failures", {}).get("subject_bin_snr", {})
            if snr_cfg:
                qm.attrs["subject_bin_snr_present"]          = True
                qm.attrs["subject_bin_snr_enabled"]          = bool(snr_cfg.get("enabled", False))
                qm.attrs["subject_bin_snr_threshold"]        = float(snr_cfg["snr_threshold"])
                qm.attrs["subject_bin_snr_guard_half_width"] = int(snr_cfg["guard_half_width"])
                qm.attrs["subject_bin_snr_skipped"]          = bool(results.get("snr_skipped", True))
                snr_bin = results.get("snr_locked_bin")
                if snr_bin is not None:
                    qm.attrs["subject_bin_snr_locked_bin"] = int(snr_bin)
                snr_skip_reason = results.get("snr_skip_reason")
                if snr_skip_reason:
                    qm.attrs["subject_bin_snr_skip_reason"] = str(snr_skip_reason)
            do_cfg = cfg.get("soft_failures", {}).get("subject_bin_dropout", {})
            if do_cfg:
                qm.attrs["subject_bin_dropout_present"]     = True
                qm.attrs["subject_bin_dropout_enabled"]     = bool(do_cfg.get("enabled", False))
                qm.attrs["subject_bin_dropout_factor"]      = float(do_cfg["dropout_factor"])
                qm.attrs["subject_bin_dropout_half_window"] = int(do_cfg["dropout_window_frames"])
                qm.attrs["subject_bin_dropout_skipped"]     = bool(results.get("dropout_skipped", True))
                energy_source = results.get("dropout_energy_source")
                if energy_source:
                    qm.attrs["subject_bin_dropout_energy_source"] = str(energy_source)
                do_locked_bin = results.get("dropout_locked_bin")
                if do_locked_bin is not None:
                    qm.attrs["subject_bin_dropout_locked_bin"] = int(do_locked_bin)
                do_skip_reason = results.get("dropout_skip_reason")
                if do_skip_reason:
                    qm.attrs["subject_bin_dropout_skip_reason"] = str(do_skip_reason)
            pj_cfg = cfg.get("soft_failures", {}).get("phase_jump", {})
            if pj_cfg:
                qm.attrs["phase_jump_present"]       = True
                qm.attrs["phase_jump_enabled"]       = bool(pj_cfg.get("enabled", False))
                qm.attrs["phase_jump_threshold_rad"] = float(pj_cfg["threshold_rad"])
                qm.attrs["phase_jump_skipped"]       = bool(results.get("phase_jump_skipped", True))
                qm.attrs["phase_jump_method"]        = str(results.get("phase_jump_method", "mean_phasor"))
                pj_bin = results.get("phase_jump_locked_bin")
                if pj_bin is not None:
                    qm.attrs["phase_jump_locked_bin"] = int(pj_bin)
                pj_skip_reason = results.get("phase_jump_skip_reason")
                if pj_skip_reason:
                    qm.attrs["phase_jump_skip_reason"] = str(pj_skip_reason)
                pd_stats = results.get("phase_diff_stats")
                if pd_stats:
                    for stat_key in ("min", "p50", "p90", "p95", "p99", "max", "mean"):
                        qm.attrs[f"phase_jump_phase_diff_{stat_key}"] = float(pd_stats[stat_key])

            qm.attrs["description"] = (
                "Boolean mask for frames [trim_frames:]. "
                "True = healthy. Indexing: quality_mask[i] corresponds to cube[trim_frames + i]."
            )

            grp = f.create_group("quality_metrics")
            grp.attrs["description"] = "Per-frame scalar metrics computed over frames [trim_frames:]."
            grp.create_dataset("max_abs_adc",  data=results["max_abs_adc"])
            grp.create_dataset("frame_energy", data=results["frame_energy"])
            grp.create_dataset("clip_flagged", data=results["clip_flagged"])
            grp.create_dataset("dead_flagged", data=results["dead_flagged"])
            if "rx_energy_ratio" in results:
                grp.create_dataset("rx_energy_ratio",      data=results["rx_energy_ratio"])
                grp.create_dataset("rx_imbalance_flagged", data=results["rx_imbalance_flagged"])
            if "subject_bin_energy" in results:
                grp.create_dataset("subject_bin_energy", data=results["subject_bin_energy"])
            if "motion_spike_flagged" in results:
                grp.create_dataset("motion_spike_flagged", data=results["motion_spike_flagged"])
            if "subject_bin_dropout_flagged" in results:
                grp.create_dataset(
                    "subject_bin_dropout_flagged",
                    data=results["subject_bin_dropout_flagged"],
                )
            if "subject_bin_snr" in results:
                grp.create_dataset("subject_bin_snr", data=results["subject_bin_snr"])
                grp.create_dataset("snr_flagged",     data=results["snr_flagged"])
            if "phase_diff" in results:
                grp.create_dataset("phase_diff",         data=results["phase_diff"])
                grp.create_dataset("phase_delta",        data=results["phase_delta"])
                grp.create_dataset("phase_jump_flagged", data=results["phase_jump_flagged"])

            print(f"  quality_mask written -> {h5_path.name}")

    try:
        _write_diagnostic_log(
            session_id, stats, cfg, commit, step3_meta,
            no_write=no_write, source_bin_files=source_bin_files,
        )
    except Exception as exc:
        print(f"  WARNING: diagnostic log failed: {exc}")

    stats["session_id"] = session_id
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Step 4: Add per-frame quality mask to time-domain HDF5 cubes"
    )
    parser.add_argument(
        "--config", default="steps/step_4/config.yaml",
        help="Path to config.yaml (default: steps/step_4/config.yaml)"
    )
    parser.add_argument("--session", metavar="EXPID",
                        help="Single session ID (e.g. exp003)")
    parser.add_argument("--all", action="store_true",
                        help="Process all non-excluded sessions in manifest")
    parser.add_argument("--no-write", action="store_true",
                        help="Compute quality mask but do not modify HDF5 files")
    parser.add_argument("--overwrite", action="store_true",
                        help="Replace existing /quality_mask and /quality_metrics")
    parser.add_argument("--allow-review-required", action="store_true",
                        help="Process sessions flagged chest_bin_review_required=True")
    args = parser.parse_args()

    if not args.session and not args.all:
        parser.print_help()
        sys.exit(0)

    cfg_path = REPO_ROOT / args.config
    if not cfg_path.exists():
        sys.exit(f"ERROR: config not found: {cfg_path}")
    cfg_text = cfg_path.read_text(encoding="utf-8")
    cfg      = yaml.safe_load(cfg_text)

    np.random.seed(int(cfg.get("seed", 42)))

    paths         = cfg["paths"]
    cubes_dir     = REPO_ROOT / paths["cubes_dir"]
    manifest_path = REPO_ROOT / paths["manifest"]

    manifest = pd.read_csv(manifest_path, keep_default_na=False)
    commit   = _git_commit()

    analysis_cfg  = cfg["analysis"]
    chunk_frames_v = analysis_cfg.get("chunk_frames")
    chunk_frames   = int(chunk_frames_v) if chunk_frames_v is not None else None

    input_policy  = cfg.get("input_policy", {})
    require_step3 = bool(input_policy.get("require_step3_fields", True))
    min_conf      = input_policy.get("min_chest_bin_confidence", "medium")
    allow_review  = (
        bool(input_policy.get("allow_review_required", False)) or args.allow_review_required
    )
    skip_excluded         = not bool(input_policy.get("process_excluded_sessions", False))
    skip_missing_h5_in_all = bool(input_policy.get("skip_missing_h5_in_all", True))

    session_ids = [args.session] if args.session else manifest["session_id"].tolist()

    SEP     = "=" * 60
    failed  = []
    skipped = []
    all_stats: list[dict] = []

    for sid in session_ids:
        print(f"\n{SEP}\n  {sid}\n{SEP}")
        rows = manifest[manifest["session_id"] == sid]
        if rows.empty:
            print(f"  ERROR: '{sid}' not found in manifest.")
            failed.append(sid)
            continue
        row = rows.iloc[0]

        # Exclusion check
        exclusion = str(row.get("exclusion_reason", "")).strip()
        if exclusion and skip_excluded:
            if args.all:
                print(f"  SKIPPED (excluded): {exclusion}")
                skipped.append(sid)
                continue
            else:
                print(f"  WARNING: session excluded in manifest: {exclusion}")

        # HDF5 existence check
        h5_path = cubes_dir / f"{sid}.h5"
        if not h5_path.exists():
            if args.all and skip_missing_h5_in_all:
                print(f"  SKIPPED (no HDF5): {h5_path.name} — run Step 2 first")
                skipped.append(sid)
                continue
            else:
                print(f"  ERROR: HDF5 not found: {h5_path} — run Step 2 first")
                failed.append(sid)
                continue

        # Step 3 intake validation
        if require_step3:
            missing = [f for f in _STEP3_REQUIRED if str(row.get(f, "")).strip() == ""]
            if missing:
                msg = f"missing Step 3 fields: {missing} — run Step 3 first"
                if args.all:
                    print(f"  SKIPPED ({msg})")
                    skipped.append(sid)
                    continue
                else:
                    print(f"  ERROR: {msg}")
                    failed.append(sid)
                    continue

        # Confidence check
        confidence = str(row.get("chest_bin_confidence", "")).strip()
        if confidence and _CONFIDENCE_ORDER.get(confidence, -1) < _CONFIDENCE_ORDER.get(min_conf, 1):
            msg = (
                f"chest_bin_confidence={confidence!r} < required={min_conf!r} "
                f"(re-run Step 3 or lower input_policy.min_chest_bin_confidence)"
            )
            if args.all:
                print(f"  SKIPPED ({msg})")
                skipped.append(sid)
                continue
            else:
                print(f"  ERROR: {msg}")
                failed.append(sid)
                continue

        # Review-required check
        review_required = str(row.get("chest_bin_review_required", "")).strip().lower()
        if review_required == "true" and not allow_review:
            msg = "chest_bin_review_required=True — inspect Step 3 output before continuing"
            if args.all:
                print(f"  SKIPPED ({msg}) — use --allow-review-required to override")
                skipped.append(sid)
                continue
            else:
                print(f"  ERROR: {msg} — use --allow-review-required to override")
                failed.append(sid)
                continue

        # Parse locked_bin
        raw_lb = str(row.get("locked_bin", "")).strip()
        if not raw_lb:
            print(f"  ERROR: locked_bin is blank for '{sid}'")
            failed.append(sid)
            continue
        try:
            locked_bin = int(float(raw_lb))
        except (ValueError, TypeError):
            print(f"  ERROR: cannot parse locked_bin={raw_lb!r} for '{sid}'")
            failed.append(sid)
            continue

        step3_meta = {
            "locked_bin":     locked_bin,
            "locked_range_m": str(row.get("locked_range_m", "")),
            "confidence":     confidence,
            "run_id":         str(row.get("chest_bin_run_id", "")),
            "review_required": review_required,
        }

        try:
            stats = _process_session(
                sid, row, cfg, commit, cubes_dir, chunk_frames, step3_meta,
                no_write=args.no_write,
                overwrite=args.overwrite,
            )
            all_stats.append(stats)
            # Write config snapshot per session alongside step_4.log
            sess_dir = REPO_ROOT / "results" / sid / "step_4"
            sess_dir.mkdir(parents=True, exist_ok=True)
            (sess_dir / "config_used.yaml").write_text(cfg_text, encoding="utf-8")
        except Exception as exc:
            print(f"  ERROR: {exc}")
            failed.append(sid)
            continue

    processed = len(session_ids) - len(failed) - len(skipped)
    print(f"\n{SEP}")
    print(f"  Done: {processed}/{len(session_ids)} processed  "
          f"skipped={len(skipped)}  failed={len(failed)}")
    if failed:
        print(f"  Failed:  {failed}")
    if skipped:
        print(f"  Skipped: {skipped}")
    print(SEP)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
