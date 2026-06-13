"""Phase-based vital-signs DSP: range profile -> chest bin -> phase -> rate.

Design goal (CLAUDE.md s.5): every heart-rate estimate leaves EVIDENCE. The functions
return intermediate signals (chosen bin, unwrapped phase, band spectrum, picked peak) so a
wrong reading (e.g. radar 54 vs Masimo 80) can be diagnosed: wrong range bin? respiration
harmonic? spurious peak? — not just observed.

The pure-signal functions (`bandpass_filter`, `estimate_rate_from_phase`) have no hardware
dependency and are covered by tests/test_vitals_synthetic.py.

exp002 additions (arXiv:2503.07062):
  - eca_project(): QR-based respiration subspace cancellation
  - refine_freq_hz(): parabolic interpolation beyond FFT bin resolution
  - estimate_rate_from_phase() extended with f_r_hz kwarg for ECA + AHET path
  - run_pipeline_locked() wired to estimate f_r per window and apply ECA + AHET
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
from scipy.signal import butter, filtfilt, find_peaks

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


def refine_freq_hz(spectrum: np.ndarray, freqs: np.ndarray, peak_idx: int) -> float:
    """Parabolic interpolation to refine a spectral peak beyond FFT bin resolution.

    Returns refined frequency in Hz. Falls back to bin centre if peak is at edge.
    (OpenAI cross-review finding #2 — arXiv:2503.07062)
    """
    if peak_idx <= 0 or peak_idx >= len(spectrum) - 1:
        return float(freqs[peak_idx])
    alpha = np.abs(spectrum[peak_idx - 1])
    beta  = np.abs(spectrum[peak_idx])
    gamma = np.abs(spectrum[peak_idx + 1])
    denom = alpha - 2 * beta + gamma
    if denom == 0:
        return float(freqs[peak_idx])
    shift = 0.5 * (alpha - gamma) / denom      # shift in bins
    bin_width = float(freqs[1] - freqs[0])
    return float(freqs[peak_idx]) + shift * bin_width


def eca_project(
    theta: np.ndarray,
    f_r: float,
    fs: float,
    k_max: int = 6,
    cardiac_candidate_hz: float | None = None,
) -> np.ndarray:
    """Remove respiratory harmonics from phase signal using QR projection.

    Adaptive K_b: include harmonic k if:
      - k * f_r < 2.0 Hz (stays below cardiac band ceiling), AND
      - cardiac_candidate_hz is None OR abs(k * f_r - cardiac_candidate_hz) > 0.15 Hz
        (do not suppress a harmonic too close to the cardiac candidate)
    Hard floor: always include k = 1..4 regardless of proximity to cardiac candidate.
    Uses QR decomposition — not explicit matrix inverse — for numerical stability.
    (OpenAI cross-review finding #1 — arXiv:2503.07062)
    """
    N = len(theta)
    t = np.arange(N) / fs
    cols: list[np.ndarray] = []
    for k in range(1, k_max + 1):
        freq = k * f_r
        if freq >= 2.0:
            break
        if k <= 4:                              # hard floor — covers known 60 bpm failure
            cols += [np.sin(2 * np.pi * freq * t), np.cos(2 * np.pi * freq * t)]
        elif (cardiac_candidate_hz is None
              or abs(freq - cardiac_candidate_hz) > 0.15):
            cols += [np.sin(2 * np.pi * freq * t), np.cos(2 * np.pi * freq * t)]
    if not cols:
        return theta.copy()
    X = np.column_stack(cols)
    Q, _ = np.linalg.qr(X, mode="reduced")     # stable — no explicit inverse
    return theta - Q @ (Q.T @ theta)


def estimate_rate_from_phase(
    phase: np.ndarray,
    fs: float,
    band: tuple,
    f_r_hz: float | None = None,
) -> dict:
    """Estimate a rate (bpm) from a slow-time phase signal via band-limited FFT peak.

    When f_r_hz is None: original bandpass + argmax path (backward compat).
    When f_r_hz is provided: ECA respiration cancellation + AHET second-harmonic
    consistency check (arXiv:2503.07062, OpenAI cross-review findings #1–#4).

    Physiological outlier gate: if f_r_hz is provided but outside [0.15, 0.60] Hz
    (9–36 bpm), falls back to no-ECA path with f_r_outlier=True in the returned dict.
    This prevents an implausible respiration estimate from corrupting ECA subspace.

    Returns a dict with rate AND the spectrum/peak so the choice is inspectable.
    All paths return: ahet_verified, harmonic_suspect, f_r_hz_used, eca_applied,
      phase_eca, f_r_outlier.
    """
    # Physiological gate: an implausible f_r collapses ECA to garbage; skip it.
    _GATE_LO_HZ = 0.15   # 9 bpm  — below this is not real respiration
    _GATE_HI_HZ = 0.60   # 36 bpm — above this is not real respiration
    f_r_is_outlier = (f_r_hz is not None) and not (_GATE_LO_HZ <= f_r_hz <= _GATE_HI_HZ)

    x = np.asarray(phase, dtype=float)
    x = x - x.mean()

    # ------------------------------------------------------------------ #
    # No-ECA path: f_r_hz is None OR physiological outlier gate fired     #
    # ------------------------------------------------------------------ #
    if f_r_hz is None or f_r_is_outlier:
        x_filt = bandpass_filter(x, fs, band[0], band[1])
        n = len(x_filt)
        win = np.hanning(n)
        spectrum = np.abs(np.fft.rfft(x_filt * win))
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
            "filtered": x_filt,
            "ahet_verified": False,
            "harmonic_suspect": False,
            "f_r_hz_used": f_r_hz,    # preserve original value (may be None or outlier)
            "f_r_outlier": f_r_is_outlier,
            "eca_applied": False,
            "phase_eca": None,
        }

    # ------------------------------------------------------------------ #
    # ECA + AHET path (f_r_hz is valid and within physiological gate)     #
    # ------------------------------------------------------------------ #
    # Widen the bandpass to preserve 2nd cardiac harmonic (up to 2×band_hi).
    # This lets AHET check [2×f_h ± 0.1 Hz] without hitting the filter rolloff.
    bp_hi = min(2.0 * band[1], fs * 0.45)
    x_bp = bandpass_filter(x, fs, band[0], bp_hi)
    n = len(x_bp)
    hann = np.hanning(n)

    def _spec(sig: np.ndarray):
        return np.abs(np.fft.rfft(sig * hann)), np.fft.rfftfreq(n, d=1.0 / fs)

    # First-pass ECA: no cardiac candidate guard yet
    x_eca1 = eca_project(x_bp, f_r_hz, fs, cardiac_candidate_hz=None)
    spec1, freqs = _spec(x_eca1)

    cardiac_mask = (freqs >= band[0]) & (freqs <= band[1])
    if not cardiac_mask.any():
        raise ValueError("No FFT bins in cardiac band.")

    band_mask_idx = np.where(cardiac_mask)[0]
    band_spec1 = spec1[band_mask_idx]

    # Top-3 candidates by prominence within cardiac band
    noise_floor = float(np.median(band_spec1))
    peaks_local, _ = find_peaks(band_spec1, prominence=max(noise_floor * 0.1, 1e-12))
    if len(peaks_local) == 0:
        peaks_local = np.array([int(np.argmax(band_spec1))])
    sorted_by_mag = peaks_local[np.argsort(band_spec1[peaks_local])[::-1]][:3]
    candidates_global = band_mask_idx[sorted_by_mag]

    # Try each candidate with AHET consistency check
    for cand_global in candidates_global:
        cand_hz = refine_freq_hz(spec1, freqs, int(cand_global))

        # Second-pass ECA: guard against suppressing a harmonic near the cardiac candidate
        # (OpenAI cross-review finding #4)
        x_eca2 = eca_project(x_bp, f_r_hz, fs, cardiac_candidate_hz=cand_hz)
        spec2, _ = _spec(x_eca2)

        # AHET: local 2nd harmonic search [2×cand_hz ± 0.1 Hz]
        # Local window, not global to 4.0 Hz (OpenAI cross-review finding #3)
        lo2 = 2.0 * cand_hz - 0.1
        hi2 = 2.0 * cand_hz + 0.1
        mask2 = (freqs >= lo2) & (freqs <= hi2)
        if not mask2.any():
            continue

        region2 = spec2[mask2]
        peak2_local = int(np.argmax(region2))
        peak2_global = int(np.where(mask2)[0][peak2_local])

        # Noise floor from the cardiac band of the second-pass ECA spectrum
        noise2 = float(np.median(spec2[cardiac_mask]))
        if float(region2[peak2_local]) > noise2:
            # Refine both fundamental and 2nd harmonic, then blend
            # (OpenAI cross-review finding: use 2nd harmonic as refinement, not just gate)
            f_h_ref = refine_freq_hz(spec2, freqs, int(cand_global))
            f_h2_ref = refine_freq_hz(spec2, freqs, peak2_global)
            f_final = 0.5 * f_h_ref + 0.5 * (f_h2_ref / 2.0)
            return {
                "rate_bpm": f_final * 60.0,
                "peak_hz": f_final,
                "freqs_hz": freqs,
                "spectrum": spec2,
                "band": band,
                "filtered": x_eca2,
                "ahet_verified": True,
                "harmonic_suspect": False,
                "f_r_hz_used": f_r_hz,
                "f_r_outlier": False,
                "eca_applied": True,
                "phase_eca": x_eca2,
                "ahet_second_harmonic_hz": f_h2_ref,
            }

    # All candidates failed AHET — do NOT fabricate a value (CLAUDE.md §4)
    return {
        "rate_bpm": float("nan"),
        "peak_hz": float("nan"),
        "freqs_hz": freqs,
        "spectrum": spec1,
        "band": band,
        "filtered": x_eca1,
        "ahet_verified": False,
        "harmonic_suspect": True,
        "f_r_hz_used": f_r_hz,
        "f_r_outlier": False,
        "eca_applied": True,
        "phase_eca": x_eca1,
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
    locked_bin: int | None = None,
) -> list[dict]:
    """Phase-locked variant: selects the range bin ONCE for the entire cube.

    Avoids bin-hopping and phase discontinuities that occur when select_range_bin
    is called independently on each window slice.

    locked_bin: if provided, skip energy-based bin selection and use this bin index
      directly. Decouples bin choice from the trim window so different trim lengths
      give comparable results. If None, existing energy-based selection is used.

    Per-window processing (exp002 ECA + AHET, arXiv:2503.07062):
      1. Estimate and refine f_r from the window phase (parabolic interpolation).
      2. Pass f_r directly to estimate_rate_from_phase(); the physiological outlier gate
         inside that function falls back to no-ECA if f_r is outside [0.15, 0.60] Hz.
      3. Record f_r_hz_used and f_r_outlier from the returned dict for diagnostics.

    Each returned dict includes all intermediate signals for diagnosis (CLAUDE.md §5.4).
    Result keys:
      f_r_hz_used  — f_r fed to estimate_rate_from_phase() (raw, unsmoothed)
      f_r_raw_hz   — alias for f_r_hz_used (kept for CSV backward compat)
      f_r_outlier  — True if physiological gate fired (ECA was skipped this window)
    """
    if locked_bin is not None:
        if locked_bin < 0 or locked_bin >= range_axis.shape[0]:
            raise ValueError(
                f"locked_bin={locked_bin} is out of range [0, {range_axis.shape[0]})."
            )
        locked_range_m = float(range_axis[locked_bin])
        print(
            f"Bin locked by config: bin {locked_bin} ({locked_range_m:.3f} m)"
            f" -- skipping energy-based selection"
        )
        bin_energy = None
    else:
        sel = select_range_bin(cube, range_axis, (params.gate_min_m, params.gate_max_m))
        locked_bin = sel["bin_idx"]
        locked_range_m = sel["range_m"]
        bin_energy = sel["energy"]

    phase = phase_at_bin(cube, locked_bin)
    phase_clean = remove_impulse_noise(phase)

    n = len(phase_clean)
    results: list[dict] = []
    for win_idx, s in enumerate(range(0, n - window_frames + 1, hop_frames)):
        e = s + window_frames
        win_phase = phase_clean[s:e]

        # Respiration rate + parabolic refinement beyond FFT bin resolution
        resp = estimate_rate_from_phase(win_phase, params.fs_hz, params.resp_band_hz)
        resp_peak_global = int(np.argmin(np.abs(resp["freqs_hz"] - resp["peak_hz"])))
        f_r_hz = refine_freq_hz(resp["spectrum"], resp["freqs_hz"], resp_peak_global)

        # Cardiac rate: ECA + AHET (gate inside estimate_rate_from_phase handles outliers)
        heart = estimate_rate_from_phase(
            win_phase, params.fs_hz, params.heart_band_hz, f_r_hz=f_r_hz
        )

        if heart.get("f_r_outlier"):
            print(
                f"Window {win_idx}: f_r={f_r_hz * 60:.1f} bpm outside physiological gate"
                f" -- ECA skipped, fell back to bandpass+argmax"
            )

        results.append({
            "hr_bpm": heart["rate_bpm"],
            "rr_bpm": resp["rate_bpm"],
            "chosen_bin": locked_bin,
            "chosen_range_m": locked_range_m,
            "bin_locked": True,
            "bin_energy": bin_energy,
            "phase_unwrapped": phase[s:e],
            "phase_clean": win_phase,
            "heart_spectrum": heart["spectrum"],
            "heart_freqs_hz": heart["freqs_hz"],
            "heart_peak_hz": heart["peak_hz"],
            "window_start_frame": s,
            "window_end_frame": e,
            # ECA + AHET diagnostics (CLAUDE.md §5.4)
            "ahet_verified":   heart.get("ahet_verified", False),
            "harmonic_suspect": heart.get("harmonic_suspect", False),
            "f_r_hz_used":     float(f_r_hz),
            "f_r_raw_hz":      float(f_r_hz),   # alias — kept for CSV compat
            "f_r_outlier":     bool(heart.get("f_r_outlier", False)),
            "eca_applied":     heart.get("eca_applied", True),
            "phase_eca":       heart.get("phase_eca"),
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
