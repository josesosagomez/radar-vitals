"""Breathing rate extraction DSP for Step 5.

Three estimators are provided:
  A. FFT peak + parabolic refinement (baseline / fallback)
  B. Harmonic accumulation (primary)
  C. STFT subwindow stability check (confidence / QC)

And a fusion function that combines them into a final estimate.

Phase extraction note
---------------------
`extract_chest_phase` expects a raw ADC cube slice (N, chirps, rx, adc_samples).
It applies a Hann window over ADC samples before the range FFT, then extracts the
complex value at `locked_bin` and aggregates across chirps/RX using one of two methods:

  delta_before_mean (default):
    Per-channel frame-to-frame conjugate-product delta, averaged over chirps/RX, then
    integrated. Robust when RX channels have fixed static phase offsets that would
    partially cancel the mean phasor.

  mean_phasor:
    Mean complex phasor across chirps/RX per frame, then unwrap angle.  Simpler but
    can fail when RX phase offsets are spread.

Harmonic accumulation adaptation
---------------------------------
The "Discovering the Unseen" paper models a pulse radar where the demodulated output
has power at 2*f_b, 4*f_b, ... (even harmonics of breathing).  Step 5 works with the
unwrapped chest *phase* signal, which is proportional to chest displacement and has
power at f_b, 2*f_b, 3*f_b ... (all harmonics).  Candidate frequencies are searched
inside `band_hz`; harmonic evidence is collected across the full spectrum up to Nyquist
(or `harmonic_max_hz`), NOT restricted to the respiration band.
"""
from __future__ import annotations

import numpy as np
from scipy.fft import fft as sp_fft, rfft as sp_rfft, rfftfreq as sp_rfftfreq


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _detrend(x: np.ndarray, kind: str) -> np.ndarray:
    """Linear or constant detrend without LAPACK (avoids scipy/numpy lstsq crashes on Windows).

    Linear fit solved analytically using centred time variable — only dot products needed.
    """
    if kind == "constant":
        return x - x.mean()
    # Linear: fit y = a*t + b. Solve analytically in centred coordinates.
    n      = len(x)
    t_mean = (n - 1) / 2.0
    t_c    = np.arange(n, dtype=np.float64) - t_mean  # centred time
    ss_tt  = np.dot(t_c, t_c)
    a      = np.dot(t_c, x) / ss_tt if ss_tt > 0 else 0.0
    b      = x.mean() - a * t_mean
    return x - (a * (np.arange(n, dtype=np.float64)) + b)


# ---------------------------------------------------------------------------
# Internal helper — duplicated from src/vitals.py to avoid circular import
# ---------------------------------------------------------------------------

def _is_local_max(spec: np.ndarray, idx: int) -> bool:
    """M2 peak-validity predicate (plans/m2_respiration_fix.md §2.1, plateau policy).

    Strict `>` against the lower-index neighbour, `>=` against the upper: the structure
    being rejected is a monotone non-increasing decay from below the respiration band
    (drift-leakage tail), so strictness is required on the low side; upper-side equality
    is the half-bin-split signature of a genuine line, not leakage. Array-edge bins have
    no complete neighbourhood and never qualify.
    """
    if idx <= 0 or idx >= len(spec) - 1:
        return False
    return bool(spec[idx] > spec[idx - 1] and spec[idx] >= spec[idx + 1])


def _parabolic_peak(spectrum: np.ndarray, peak_idx: int, freq_res_hz: float) -> float:
    """Sub-bin peak refinement via parabolic interpolation.

    Returns refined frequency in Hz.  Falls back to raw bin centre on edge cases.
    """
    if peak_idx <= 0 or peak_idx >= len(spectrum) - 1:
        return peak_idx * freq_res_hz
    alpha = float(spectrum[peak_idx - 1])
    beta  = float(spectrum[peak_idx])
    gamma = float(spectrum[peak_idx + 1])
    denom = alpha - 2.0 * beta + gamma
    if denom == 0.0:
        return peak_idx * freq_res_hz
    delta = 0.5 * (alpha - gamma) / denom
    if abs(delta) > 1.0:
        return peak_idx * freq_res_hz
    return (peak_idx + delta) * freq_res_hz


# ---------------------------------------------------------------------------
# Phase extraction
# ---------------------------------------------------------------------------

def extract_chest_phase(
    cube_slice: np.ndarray,
    locked_bin: int,
    method: str = "delta_before_mean",
) -> np.ndarray:
    """Extract accumulated chest phase from a raw ADC cube slice.

    Applies a Hann window over ADC samples before the range FFT, selects
    `locked_bin`, then aggregates across chirps and RX channels.

    Parameters
    ----------
    cube_slice : (N, chirps, rx, adc_samples) complex64
    locked_bin : range FFT bin index for the chest.
    method     : "delta_before_mean" or "mean_phasor".

    Returns
    -------
    phase : float64 (N,)
        Accumulated phase signal (suitable for spectral analysis after detrend).
    """
    if method not in ("delta_before_mean", "mean_phasor"):
        raise ValueError(
            f"method must be 'delta_before_mean' or 'mean_phasor', got {method!r}"
        )
    N, _, _, n_adc = cube_slice.shape
    n_bins = n_adc  # complex FFT: n_bins == n_adc

    if not (0 <= int(locked_bin) < n_bins):
        raise ValueError(
            f"locked_bin={locked_bin} out of range [0, {n_bins - 1}]"
        )

    hann_win = np.hanning(n_adc).astype(np.float32)
    # (N, chirps, rx, n_adc) * (n_adc,) — trailing-dim broadcast
    windowed  = cube_slice * hann_win
    range_fft = sp_fft(windowed, axis=3)             # (N, chirps, rx, n_adc)
    bin_vals  = range_fft[:, :, :, int(locked_bin)]  # (N, chirps, rx)

    if method == "mean_phasor":
        phasors = bin_vals.mean(axis=(1, 2))  # (N,) complex
        return np.unwrap(np.angle(phasors)).astype(np.float64)

    # delta_before_mean
    phase_delta = np.zeros(N, dtype=np.float64)
    if N > 1:
        dp = bin_vals[1:] * np.conj(bin_vals[:-1])       # (N-1, chirps, rx)
        phase_delta[1:] = np.angle(dp.mean(axis=(1, 2))).astype(np.float64)
    return np.cumsum(phase_delta)


# ---------------------------------------------------------------------------
# Method A: FFT peak baseline
# ---------------------------------------------------------------------------

def fft_estimate_rr(
    phase: np.ndarray,
    fs: float,
    band_hz: tuple[float, float],
    detrend_type: str = "linear",
) -> dict:
    """Estimate breathing rate via windowed FFT peak + parabolic refinement.

    Parameters
    ----------
    phase       : 1-D phase signal (frames).
    fs          : sample rate (Hz) — radar frame rate.
    band_hz     : (lo, hi) respiration search band in Hz.
    detrend_type: "linear" (default) or "constant".

    Returns
    -------
    dict with keys:
        fft_rr_bpm, fft_peak_hz, fft_peak_snr_db,
        freqs_hz (ndarray), spectrum (ndarray).
    NaN values are returned when the band contains no FFT bins.
    """
    _nan = float("nan")
    x = _detrend(np.asarray(phase, dtype=np.float64), detrend_type)

    n     = len(x)
    win   = np.hanning(n)
    spec  = np.abs(sp_rfft(x * win))
    freqs = sp_rfftfreq(n, d=1.0 / fs)

    mask = (freqs >= band_hz[0]) & (freqs <= band_hz[1])
    if not mask.any():
        return {
            "fft_rr_bpm": _nan, "fft_peak_hz": _nan, "fft_peak_snr_db": _nan,
            "fft_peak_bin": -1,
            "fft_band_argmax_bin": -1, "fft_band_argmax_is_local_max": False,
            "fft_selected_bin": -1, "fft_selected_is_edge_bin": False,
            "freqs_hz": freqs, "spectrum": spec,
        }

    band_spec    = spec[mask]
    band_freqs   = freqs[mask]
    band_indices = np.where(mask)[0]
    noise_floor  = float(np.median(band_spec))

    # M2 §2.1: the reported peak must be a genuine local maximum of the FULL spectrum
    # (the band-edge bin is compared to the bin just below the band). If the in-band
    # argmax fails, fall back to the strongest in-band bin that passes; if none passes,
    # there is no valid respiration peak this window.
    argmax_local  = int(np.argmax(band_spec))
    argmax_global = int(band_indices[argmax_local])
    argmax_is_lm  = _is_local_max(spec, argmax_global)

    if argmax_is_lm:
        sel_local = argmax_local
    else:
        sel_local = -1
        lm_order = np.argsort(band_spec)[::-1]
        for li in lm_order:
            if _is_local_max(spec, int(band_indices[li])):
                sel_local = int(li)
                break

    if sel_local < 0:
        return {
            "fft_rr_bpm": _nan, "fft_peak_hz": _nan, "fft_peak_snr_db": _nan,
            "fft_peak_bin": -1,
            "fft_band_argmax_bin": argmax_global,
            "fft_band_argmax_is_local_max": False,
            "fft_selected_bin": -1, "fft_selected_is_edge_bin": False,
            "freqs_hz": freqs, "spectrum": spec,
        }

    sel_global = int(band_indices[sel_local])
    # SNR is recomputed for the actually selected bin (M2 §2.1: on fallback the peak
    # fields must describe the selected bin, not the rejected argmax).
    peak_mag = float(band_spec[sel_local])
    snr_db   = (20.0 * np.log10(peak_mag / max(noise_floor, 1e-12))
                if peak_mag > 0 else _nan)

    freq_res = float(freqs[1] - freqs[0]) if len(freqs) > 1 else 1.0
    # Parabolic interpolation works on the full local band_spec array.
    # band_freqs[0] is the starting frequency, so the refined hz is:
    peak_hz = float(band_freqs[0]) + _parabolic_peak(band_spec, sel_local, freq_res)

    return {
        "fft_rr_bpm":      peak_hz * 60.0,
        "fft_peak_hz":     peak_hz,
        "fft_peak_snr_db": snr_db,
        "fft_peak_bin":    sel_global,  # index into freqs_hz / spectrum
        # M2 evidence: the original argmax verdict is kept distinct from the selected
        # (possibly fallback) bin, so a fallback is reconstructable from persisted fields.
        "fft_band_argmax_bin":          argmax_global,
        "fft_band_argmax_is_local_max": argmax_is_lm,
        "fft_selected_bin":             sel_global,
        # Band-edge status is a separate fact from local-max validity (M2 §2.3): the
        # fusion veto keys on the selected bin being the FIRST in-band FFT bin.
        "fft_selected_is_edge_bin":     bool(sel_global == int(band_indices[0])),
        "freqs_hz":        freqs,
        "spectrum":        spec,
    }


# ---------------------------------------------------------------------------
# Method B: Harmonic accumulation (primary estimator)
# ---------------------------------------------------------------------------

def ha_estimate_rr(
    phase: np.ndarray,
    fs: float,
    band_hz: tuple[float, float],
    max_harmonics: int = 3,
    harmonic_max_hz: float | None = None,
    detrend_type: str = "linear",
) -> dict:
    """Estimate breathing rate via harmonic accumulation.

    Candidate frequencies are FFT bin centres within `band_hz`.
    For each candidate f, harmonic evidence is collected at f, 2f, 3f, ...
    up to min(harmonic_max_hz, Nyquist) — NOT restricted to the respiration band.

    The fundamental must be a genuine spectral line — a local maximum of the full
    spectrum under the M2 plateau policy (`_is_local_max`) — for a candidate to be
    selected. This guards against false wins driven purely by harmonic-power
    coincidence (a sub-harmonic candidate inheriting a strong line at k*f), which
    the previous `fund_power > band noise floor` guard was too weak to stop
    (plans/m2_respiration_fix.md §1 Class B, §2.2).

    Parameters
    ----------
    phase            : 1-D phase signal.
    fs               : sample rate in Hz.
    band_hz          : (lo, hi) candidate search band in Hz.
    max_harmonics    : maximum number of harmonic orders to accumulate.
    harmonic_max_hz  : upper frequency limit for harmonic evidence. None = Nyquist.
    detrend_type     : "linear" or "constant".

    Returns
    -------
    Scalar CSV fields:
        ha_rr_bpm, ha_peak_hz, ha_score, ha_harmonics_used.
    NPZ array fields (returned as ndarrays):
        ha_candidate_freqs_hz, ha_candidate_scores,
        ha_harmonic_freqs_hz   (shape: n_candidates × max_harmonics, NaN-padded),
        ha_harmonic_power      (same shape).
    Also: freqs_hz, spectrum (for diagnostics).
    """
    _nan = float("nan")
    x = _detrend(np.asarray(phase, dtype=np.float64), detrend_type)

    n     = len(x)
    win   = np.hanning(n)
    spec  = np.abs(sp_rfft(x * win))
    freqs = sp_rfftfreq(n, d=1.0 / fs)
    freq_res = float(freqs[1] - freqs[0]) if len(freqs) > 1 else 1.0

    nyquist      = fs / 2.0
    max_harm_hz  = harmonic_max_hz if harmonic_max_hz is not None else nyquist

    cand_mask  = (freqs >= band_hz[0]) & (freqs <= band_hz[1])
    cand_freqs = freqs[cand_mask]
    noise_floor = float(np.median(spec[cand_mask])) if cand_mask.any() else 1e-12

    if len(cand_freqs) == 0:
        empty = np.array([], dtype=np.float64)
        return {
            "ha_rr_bpm": _nan, "ha_peak_hz": _nan,
            "ha_score": _nan, "ha_harmonics_used": 0,
            "ha_candidate_freqs_hz": empty, "ha_candidate_scores": empty,
            "ha_fund_is_local_max": np.array([], dtype=bool),
            "ha_selected_bin": -1, "ha_selected_is_edge_bin": False,
            "ha_harmonic_freqs_hz": np.empty((0, max_harmonics), dtype=np.float64),
            "ha_harmonic_power":    np.empty((0, max_harmonics), dtype=np.float64),
            "freqs_hz": freqs, "spectrum": spec,
        }

    n_cands = len(cand_freqs)
    cand_scores       = np.full(n_cands, _nan, dtype=np.float64)
    fund_lm_flags     = np.zeros(n_cands, dtype=bool)
    harm_freqs_matrix = np.full((n_cands, max_harmonics), _nan, dtype=np.float64)
    harm_power_matrix = np.full((n_cands, max_harmonics), _nan, dtype=np.float64)
    first_band_bin    = int(np.where(cand_mask)[0][0])  # first in-band FFT bin (edge)

    best_score  = -np.inf
    best_idx    = -1

    # Weight the fundamental 2× relative to harmonics so that a candidate whose
    # fundamental coincides with a genuine spectral peak is preferred over a
    # sub-harmonic candidate that merely picks up the true fundamental as its 2nd harmonic.
    _FUND_WEIGHT = 2.0

    for ci, f in enumerate(cand_freqs):
        score    = 0.0
        w_total  = 0.0
        for k in range(1, max_harmonics + 1):
            hf = k * f
            if hf > max_harm_hz:
                break
            weight  = _FUND_WEIGHT if k == 1 else 1.0
            bin_idx = int(np.argmin(np.abs(freqs - hf)))
            power   = float(spec[bin_idx])
            score  += weight * power
            w_total += weight
            harm_freqs_matrix[ci, k - 1] = hf
            harm_power_matrix[ci, k - 1] = power

        if w_total == 0.0:
            continue
        score /= w_total  # weighted average

        # M2 §2.2: the fundamental must be a genuine spectral line (local max under
        # the plateau policy), not merely above the band noise floor — a candidate
        # cannot win purely on harmonic-power coincidence.
        fund_idx = int(np.argmin(np.abs(freqs - f)))
        fund_lm_flags[ci] = _is_local_max(spec, fund_idx)
        if not fund_lm_flags[ci]:
            score *= 0.0   # zero-weight: non-line fundamentals excluded

        cand_scores[ci] = score
        if score > best_score:
            best_score = score
            best_idx   = ci

    if best_idx < 0 or best_score <= 0:
        return {
            "ha_rr_bpm": _nan, "ha_peak_hz": _nan,
            "ha_score": _nan, "ha_harmonics_used": 0,
            "ha_candidate_freqs_hz": cand_freqs,
            "ha_candidate_scores":   cand_scores,
            "ha_fund_is_local_max":  fund_lm_flags,
            "ha_selected_bin": -1, "ha_selected_is_edge_bin": False,
            "ha_harmonic_freqs_hz":  harm_freqs_matrix,
            "ha_harmonic_power":     harm_power_matrix,
            "freqs_hz": freqs, "spectrum": spec,
        }

    best_f_raw = float(cand_freqs[best_idx])
    # Parabolic refinement within the candidate band.
    # Anchor to cand_freqs[0] (the first actual FFT bin at or above band_hz[0]),
    # NOT band_hz[0] itself — the two differ by up to freq_res/2 when the band
    # edge doesn't land on an FFT bin centre.
    band_spec  = spec[cand_mask]
    peak_local = int(np.argmin(np.abs(cand_freqs - best_f_raw)))
    best_f_refined = float(cand_freqs[0]) + _parabolic_peak(band_spec, peak_local, freq_res)

    # Count valid harmonics for the winner
    n_harm_used = int(np.sum(~np.isnan(harm_freqs_matrix[best_idx])))

    # M2 evidence: raw bin identity of the winning fundamental; the edge flag is what
    # the fusion band-edge veto keys on (§2.3), independent of parabolic refinement.
    sel_bin = int(np.argmin(np.abs(freqs - best_f_raw)))

    return {
        "ha_rr_bpm":           best_f_refined * 60.0,
        "ha_peak_hz":          best_f_refined,
        "ha_score":            float(best_score / max(noise_floor, 1e-12)),  # normalised
        "ha_harmonics_used":   n_harm_used,
        "ha_candidate_freqs_hz": cand_freqs,
        "ha_candidate_scores":   cand_scores,
        "ha_fund_is_local_max":  fund_lm_flags,
        "ha_selected_bin":       sel_bin,
        "ha_selected_is_edge_bin": bool(sel_bin == first_band_bin),
        "ha_harmonic_freqs_hz":  harm_freqs_matrix,
        "ha_harmonic_power":     harm_power_matrix,
        "freqs_hz": freqs, "spectrum": spec,
    }


# ---------------------------------------------------------------------------
# Method C: STFT subwindow stability check
# ---------------------------------------------------------------------------

def stft_stability(
    phase: np.ndarray,
    fs: float,
    band_hz: tuple[float, float],
    subwindow_s: float = 10.0,
    overlap: float = 0.5,
    detrend_type: str = "linear",
) -> dict:
    """Estimate RR per subwindow and report stability statistics.

    This is a confidence check, not the primary estimator.

    Parameters
    ----------
    phase        : 1-D phase signal.
    fs           : sample rate in Hz.
    band_hz      : (lo, hi) respiration search band.
    subwindow_s  : subwindow duration in seconds.
    overlap      : fractional overlap between subwindows [0, 1).
    detrend_type : passed to fft_estimate_rr.

    Returns
    -------
    stft_rr_bpm       : median RR across valid subwindows (NaN if none).
    stft_rr_std_bpm   : std of RR across valid subwindows (NaN if < 2).
    stft_valid_fraction : fraction of subwindows with a finite RR estimate.
    """
    _nan = float("nan")
    n               = len(phase)
    sub_frames      = max(1, int(round(subwindow_s * fs)))
    hop_frames      = max(1, int(round(sub_frames * (1.0 - overlap))))
    n_subwindows    = max(0, (n - sub_frames) // hop_frames + 1)

    estimates: list[float] = []
    start = 0
    while start + sub_frames <= n:
        sub = phase[start : start + sub_frames]
        r   = fft_estimate_rr(sub, fs, band_hz, detrend_type=detrend_type)
        if np.isfinite(r["fft_rr_bpm"]):
            estimates.append(float(r["fft_rr_bpm"]))
        start += hop_frames

    valid_frac = len(estimates) / n_subwindows if n_subwindows > 0 else 0.0

    if len(estimates) == 0:
        return {"stft_rr_bpm": _nan, "stft_rr_std_bpm": _nan, "stft_valid_fraction": valid_frac}
    if len(estimates) == 1:
        return {"stft_rr_bpm": estimates[0], "stft_rr_std_bpm": _nan, "stft_valid_fraction": valid_frac}

    arr = np.array(estimates, dtype=np.float64)
    return {
        "stft_rr_bpm":       float(np.median(arr)),
        "stft_rr_std_bpm":   float(np.std(arr, ddof=1)),
        "stft_valid_fraction": valid_frac,
    }


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------

def fuse_estimates(
    fft_result:  dict,
    ha_result:   dict,
    stft_result: dict,
    resp_cfg:    dict,
) -> dict:
    """Combine FFT, HA, and STFT results into a final RR estimate.

    Parameters
    ----------
    fft_result  : output of fft_estimate_rr.
    ha_result   : output of ha_estimate_rr.
    stft_result : output of stft_stability.
    resp_cfg    : respiration section of the Step 5 config dict.

    Returns
    -------
    radar_rr_bpm    : final estimate (NaN for low confidence unless emit_low_confidence).
    resp_peak_hz    : selected fundamental frequency in Hz (= radar_rr_bpm / 60).
    resp_confidence : "high" | "medium" | "low".
    resp_valid      : True for medium or high, False for low.
    resp_edge_veto  : True when the M2 band-edge veto fired (selection was the first
                      in-band FFT bin — never valid, implementation_plan M2 done-when #2).
    resp_edge_veto_reason : "band_edge_bin" | "".
    resp_fusion_branch    : which decision branch fired (pre-veto):
                      "high" | "medium_agree" | "medium_ha_stft" | "fft_fallback" | "low".

    Decision rules (plans/m2_respiration_fix.md §2.3):
      high   : FFT/HA agree within fft_ha_agree_bpm_high, HA strong, STFT std ≤
               stft_std_high_bpm AND the STFT median supports the selected value
      medium : FFT/HA agree within fft_ha_agree_bpm_medium (no STFT evidence used), OR
               (HA strong AND STFT stable AND STFT median supports the selected value), OR
               (HA failed, FFT clean AND STFT stable AND supports the FFT value)
      low    : otherwise
      Every branch that relies on STFT stability also requires the STFT median to be
      finite, to cover >= stft_min_valid_fraction of subwindows, and to match the
      selected value within stft_match_bpm — temporal stability of a *different* rate
      is not corroboration of the selected rate.
      Finally: a selection whose raw bin is the first in-band FFT bin is vetoed
      (resp_valid=False, confidence low), by bin identity, whatever branch fired.
    """
    _nan         = float("nan")
    agree_hi     = float(resp_cfg.get("fft_ha_agree_bpm_high",   2.0))
    agree_md     = float(resp_cfg.get("fft_ha_agree_bpm_medium", 4.0))
    std_hi       = float(resp_cfg.get("stft_std_high_bpm",       2.0))
    std_md       = float(resp_cfg.get("stft_std_medium_bpm",     4.0))
    fft_snr_gate = float(resp_cfg.get("fft_fallback_snr_db",     6.0))
    # M2 §2.3 STFT-consistency keys (new; derivations in plans/m2_respiration_fix.md):
    # stft_match_bpm = one STFT-subwindow FFT bin (60 / stft_subwindow_s at the 10 s
    # default) — the coarsest quantum either compared quantity can be trusted to;
    # stft_min_valid_fraction — a median over fewer than half the subwindows is not a
    # stability measurement.
    stft_match   = float(resp_cfg.get("stft_match_bpm",          6.0))
    stft_min_vf  = float(resp_cfg.get("stft_min_valid_fraction", 0.5))
    emit_low     = bool(resp_cfg.get("emit_low_confidence", False))

    fft_rr   = fft_result.get("fft_rr_bpm",      _nan)
    fft_snr  = fft_result.get("fft_peak_snr_db", _nan)
    ha_rr    = ha_result.get("ha_rr_bpm",         _nan)
    ha_score = ha_result.get("ha_score",           _nan)
    stft_rr  = stft_result.get("stft_rr_bpm",     _nan)
    stft_std = stft_result.get("stft_rr_std_bpm", _nan)
    stft_vf  = stft_result.get("stft_valid_fraction", 0.0)

    fft_valid  = np.isfinite(fft_rr)
    ha_valid   = np.isfinite(ha_rr)
    ha_strong  = ha_valid and np.isfinite(ha_score) and ha_score > 0.0
    fft_clean  = fft_valid and np.isfinite(fft_snr) and fft_snr >= fft_snr_gate

    agree_bpm = abs(fft_rr - ha_rr) if (fft_valid and ha_valid) else _nan

    stft_stable_hi = np.isfinite(stft_std) and stft_std <= std_hi
    stft_stable_md = np.isfinite(stft_std) and stft_std <= std_md

    def _stft_supports(sel_hz: float) -> bool:
        return (
            np.isfinite(stft_rr)
            and np.isfinite(sel_hz)
            and float(stft_vf) >= stft_min_vf
            and abs(sel_hz * 60.0 - stft_rr) <= stft_match
        )

    ha_hz  = ha_result.get("ha_peak_hz",   _nan)
    fft_hz = fft_result.get("fft_peak_hz", _nan)

    if (np.isfinite(agree_bpm) and agree_bpm <= agree_hi
            and ha_strong and stft_stable_hi and _stft_supports(ha_hz)):
        confidence, selected_hz, branch, source = "high", ha_hz, "high", "ha"
    elif np.isfinite(agree_bpm) and agree_bpm <= agree_md:
        # Agreement-only medium: uses no STFT evidence. agree_bpm finite implies both
        # estimators are valid, so HA is the selected source (as before the M2 fix).
        confidence, selected_hz, branch, source = "medium", ha_hz, "medium_agree", "ha"
    elif ha_strong and stft_stable_md and _stft_supports(ha_hz):
        confidence, selected_hz, branch, source = "medium", ha_hz, "medium_ha_stft", "ha"
    elif fft_clean and stft_stable_md and _stft_supports(fft_hz):
        # HA failed but FFT found a clean, temporally stable peak — use as fallback.
        confidence, selected_hz, branch, source = "medium", fft_hz, "fft_fallback", "fft"
    else:
        confidence, selected_hz, branch, source = "low", _nan, "low", ""

    # M2 §2.3 — band-edge veto by BIN IDENTITY: a selection whose raw bin is the first
    # in-band FFT bin can never be resp_valid=True, whatever branch fired. Robust to
    # non-bin-aligned band edges, unlike refined-frequency arithmetic.
    if source == "ha":
        edge_veto = bool(ha_result.get("ha_selected_is_edge_bin", False))
    elif source == "fft":
        edge_veto = bool(fft_result.get("fft_selected_is_edge_bin", False))
    else:
        edge_veto = False
    if edge_veto:
        confidence = "low"

    resp_valid = bool(confidence in ("high", "medium") and np.isfinite(selected_hz))
    if not resp_valid and not emit_low:
        selected_hz = _nan

    radar_rr_bpm = selected_hz * 60.0 if np.isfinite(selected_hz) else _nan

    return {
        "radar_rr_bpm":   radar_rr_bpm,
        "resp_peak_hz":   selected_hz,
        "resp_confidence": confidence,
        "resp_valid":     resp_valid,
        "resp_edge_veto": edge_veto,
        "resp_edge_veto_reason": "band_edge_bin" if edge_veto else "",
        "resp_fusion_branch": branch,
    }
