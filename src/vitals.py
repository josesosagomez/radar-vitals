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
from scipy.signal import find_peaks

# Physiological bands (Hz). Heart 0.8-2.0 Hz = 48-120 bpm; respiration 0.1-0.5 Hz = 6-30 bpm.
HEART_BAND_HZ = (0.8, 2.0)
RESP_BAND_HZ = (0.1, 0.5)
AHET_MAX_CANDIDATES = 3
INTERMEDIATE_SCHEMA_VERSION = 1
# Minimum frequency above the cardiac band's lower edge accepted as a cardiac
# candidate.  The Butterworth rolloff at 0.8 Hz provides insufficient
# suppression of 2×f_r (0–0.2 Hz below the cutoff), leaving a spurious
# left-edge peak in the post-ECA cardiac band spectrum.  Candidates below
# band_lo + MIN_CARDIAC_BAND_MARGIN_HZ are treated as filter-edge artifacts.
# Assumption: HR > 57 bpm for seated/standing adults in this study.
MIN_CARDIAC_BAND_MARGIN_HZ: float = 0.15   # 0.8 + 0.15 = 0.95 Hz = 57 bpm
# Tolerance for the INCLUSIVE band-ceiling test in ECA harmonic selection. The cardiac
# spectrum mask uses `freqs <= band_hi`, so ECA must too, or the two disagree exactly at
# the edge (e.g. f_r=0.2 Hz: 10 x 0.2 = 2.0 Hz). Guards float round-off only.
_CEIL_EPS: float = 1e-9


@dataclass
class VitalsParams:
    fs_hz: float                     # slow-time sample rate = radar frame rate
    gate_min_m: float = 1.3          # subject distance gate
    gate_max_m: float = 1.6
    heart_band_hz: tuple = HEART_BAND_HZ
    resp_band_hz: tuple = RESP_BAND_HZ


def bandpass_filter(x: np.ndarray, fs: float, lo: float, hi: float, order: int = 4) -> np.ndarray:
    """Zero-phase FFT-domain bandpass.

    The live-demo environment has shown hard Windows failures inside the
    LAPACK solve used by scipy.signal filtfilt initial-condition helpers.
    Frequency-domain masking keeps the filter deterministic and avoids that
    non-catchable runtime path.
    """
    del order  # Kept for backward-compatible call sites.
    x_arr = np.asarray(x, dtype=float)
    freqs = np.fft.rfftfreq(x_arr.size, d=1.0 / fs)
    spec = np.fft.rfft(x_arr - np.mean(x_arr))
    mask = (freqs >= lo) & (freqs <= hi)
    return np.fft.irfft(spec * mask, n=x_arr.size)


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


def parabolic_interpolate_peak(
    spectrum: np.ndarray,
    peak_idx: int,
    freq_resolution_hz: float,
) -> float:
    """Refine a spectral peak location using parabolic interpolation.

    Parameters
    ----------
    spectrum : 1-D array of non-negative magnitudes (linear, not dB)
    peak_idx : integer index of the peak bin (argmax in search band)
    freq_resolution_hz : Hz per bin

    Returns
    -------
    Interpolated peak frequency in Hz. Falls back to raw bin centre
    on edge cases (boundary bin, flat top, |delta| > 1).
    """
    if peak_idx <= 0 or peak_idx >= len(spectrum) - 1:
        return peak_idx * freq_resolution_hz
    alpha = float(spectrum[peak_idx - 1])
    beta  = float(spectrum[peak_idx])
    gamma = float(spectrum[peak_idx + 1])
    denom = alpha - 2.0 * beta + gamma
    if denom == 0.0:
        return peak_idx * freq_resolution_hz
    delta = 0.5 * (alpha - gamma) / denom
    if abs(delta) > 1.0:
        return peak_idx * freq_resolution_hz
    return (peak_idx + delta) * freq_resolution_hz


def derive_k_max_eff(f_r: float, band_hi: float, k_max_cap: int) -> int:
    """Highest harmonic order whose k*f_r still lies at or below the band ceiling.

    Bounded by k_max_cap: an unbounded derivation is unsafe because the physiological
    f_r gate floor (0.15 Hz) would admit floor(2.0/0.15) = 13 harmonics, so a bad f_r
    estimate could silently expand the ECA subspace (plan S5.7).

    The ceiling is INCLUSIVE, matching the cardiac spectrum mask (freqs <= band_hi).
    """
    if f_r <= 0:
        return 0
    k_ceiling = int(np.floor(band_hi / f_r + _CEIL_EPS))
    return max(0, min(int(k_max_cap), k_ceiling))


def cardiac_skip_ks(
    f_r: float,
    cardiac_candidate_hz: float | None,
    k_max: int,
    cardiac_guard_hz: float,
) -> frozenset:
    """Harmonics to SPARE: those landing within cardiac_guard_hz of the cardiac candidate.

    This is the whole protection rule for `guard_cardiac_candidate_v1` (plan S5.1). Only a
    harmonic that lands *on* the cardiac peak must be spared; every other in-band harmonic
    should be cancelled.
    """
    if cardiac_candidate_hz is None:
        return frozenset()
    return frozenset(
        k for k in range(1, k_max + 1)
        if abs(k * f_r - cardiac_candidate_hz) <= cardiac_guard_hz
    )


def eca_harmonic_ks(
    f_r: float,
    k_max: int,
    band_hi: float = 2.0,
    cardiac_candidate_hz: float | None = None,
    skip_ks: frozenset = frozenset(),
    cardiac_guard_hz: float = 0.15,
    hard_floor_k: int = 4,
) -> list[int]:
    """Which harmonic orders ECA actually projects out. Single source of truth.

    Used by both eca_project() (to build the subspace) and estimate_rate_from_phase()
    (to report `eca_skipped_harmonics` / `n_eca_projected`), so the artifact can never
    disagree with what the algorithm did.

    A harmonic k is projected out when all of:
      - k * f_r <= band_hi        (inclusive ceiling — matches the cardiac spectrum mask)
      - k not in skip_ks          (caller-computed protection set)
      - k <= hard_floor_k         (unconditional, `legacy` only)
        OR |k*f_r - cardiac_candidate_hz| > cardiac_guard_hz

    Set hard_floor_k=0 to disable the unconditional floor, making skip_ks / the guard the
    only protection mechanism (`guard_cardiac_candidate_v1`).
    """
    ks: list[int] = []
    for k in range(1, k_max + 1):
        freq = k * f_r
        if freq > band_hi + _CEIL_EPS:      # inclusive ceiling
            break
        if k in skip_ks:
            continue
        if k <= hard_floor_k:               # legacy hard floor (known 60 bpm failure)
            ks.append(k)
        elif (cardiac_candidate_hz is None
              or abs(freq - cardiac_candidate_hz) > cardiac_guard_hz):
            ks.append(k)
    return ks


def _empty_eca_diagnostics(diag_len: int) -> dict:
    """Zeroed diagnostics with the pinned shapes (used on every early-return path)."""
    return {
        "selected_ks":     np.zeros(diag_len, dtype=bool),
        "retained_ks":     np.zeros(diag_len, dtype=bool),
        "cols_retained":   np.zeros((diag_len, 2), dtype=bool),
        "n_cols_selected": 0,
        "n_cols_retained": 0,
        "n_cols_dropped":  0,
    }


def eca_project(
    theta: np.ndarray,
    f_r: float,
    fs: float,
    k_max: int = 6,
    cardiac_candidate_hz: float | None = None,
    skip_ks: frozenset = frozenset(),
    band_hi: float = 2.0,
    cardiac_guard_hz: float = 0.15,
    hard_floor_k: int = 4,
    return_diagnostics: bool = False,
    diag_len: int | None = None,
):
    """Remove respiratory harmonics from phase signal using QR projection.

    Which harmonics are projected is decided by eca_harmonic_ks() — see there.
    `cardiac_guard_hz`, `band_hi` and `hard_floor_k` are explicit parameters (previously
    hardcoded 0.15 / 2.0 / 4) so that one rule governs protection and the reported
    skip set cannot drift from the projected set (plan S5.4).

    Uses modified Gram-Schmidt — no explicit inverse or LAPACK dependency — for
    numerical stability in the live-demo environment.
    (OpenAI cross-review finding #1 — arXiv:2503.07062)

    Basis diagnostics (cross-review 2026-07-14, comment 20.6)
    --------------------------------------------------------
    `eca_harmonic_ks()` reports which harmonic ORDERS were *selected*. It cannot report which
    sine/cosine COLUMNS actually survived the modified-Gram-Schmidt norm guard below — a
    near-degenerate column is silently dropped, so a run could claim full coverage while the
    projection used fewer columns.

    `return_diagnostics=True` returns `(clean, diag)` instead of `clean`, where diag has PINNED
    shapes (never varying with k_max_eff, so artifacts cannot truncate — plan S8.1b):

        selected_ks     bool (diag_len,)     orders eca_harmonic_ks() chose
        retained_ks     bool (diag_len,)     orders with >= 1 surviving column
        cols_retained   bool (diag_len, 2)   per-order [sin, cos] survival
        n_cols_selected int
        n_cols_retained int
        n_cols_dropped  int

    `diag_len` defaults to `k_max`; callers pass their reporting cap so the shape is stable.

    **The returned signal is bit-identical whether or not diagnostics are requested** — this is
    enforced by a regression test (test_diagnostics_do_not_change_the_projected_signal).
    """
    dl = int(diag_len) if diag_len is not None else int(k_max)
    # Cross-review P2 (2026-07-14): diag_len < k_max would silently truncate the diagnostic
    # vectors, producing evidence that looks complete but omits the highest harmonics — the
    # exact class of failure these diagnostics exist to detect. Fail loudly instead.
    if dl < int(k_max):
        raise ValueError(
            f"eca_project: diag_len={dl} < k_max={k_max} would truncate the diagnostics "
            f"for harmonics {dl + 1}..{k_max}. Pass diag_len >= k_max (normally the "
            f"reporting cap, max(k_max, k_max_cap))."
        )
    N = len(theta)
    t = np.arange(N) / fs
    ks = eca_harmonic_ks(
        f_r, k_max, band_hi=band_hi, cardiac_candidate_hz=cardiac_candidate_hz,
        skip_ks=skip_ks, cardiac_guard_hz=cardiac_guard_hz, hard_floor_k=hard_floor_k,
    )
    diag = _empty_eca_diagnostics(dl)
    for k in ks:
        if 1 <= k <= dl:
            diag["selected_ks"][k - 1] = True
    diag["n_cols_selected"] = 2 * len(ks)

    cols: list[tuple[int, int, np.ndarray]] = []   # (k, 0=sin|1=cos, column)
    for k in ks:
        freq = k * f_r
        cols.append((k, 0, np.sin(2 * np.pi * freq * t)))
        cols.append((k, 1, np.cos(2 * np.pi * freq * t)))
    if not cols:
        return (theta.copy(), diag) if return_diagnostics else theta.copy()

    basis: list[np.ndarray] = []
    for k, which, col in cols:
        v = np.asarray(col, dtype=float).copy()
        for q in basis:
            v -= q * float(np.sum(q * v))
        norm = float(np.sqrt(np.sum(v * v)))
        if norm > 1e-12:
            basis.append(v / norm)
            if 1 <= k <= dl:
                diag["cols_retained"][k - 1, which] = True
                diag["retained_ks"][k - 1] = True
    diag["n_cols_retained"] = len(basis)
    diag["n_cols_dropped"] = diag["n_cols_selected"] - len(basis)

    if not basis:
        return (theta.copy(), diag) if return_diagnostics else theta.copy()
    clean = np.asarray(theta, dtype=float).copy()
    for q in basis:
        clean -= q * float(np.sum(q * theta))
    return (clean, diag) if return_diagnostics else clean


def _check_low_candidate_competitor(
    cand_global: int,
    cand_hz: float,
    spec1: np.ndarray,
    freqs: np.ndarray,
    low_candidate_hz: float,
    high_candidate_preference_hz: float,
    high_competitor_min_mag_ratio: float,
    band: tuple,
) -> bool:
    """Return True if a high-frequency competitor outweighs a low-frequency candidate.

    Fires when cand_hz < low_candidate_hz AND there is any bin in spec1 at
    freq in [high_candidate_preference_hz, band[1]] whose magnitude is >=
    high_competitor_min_mag_ratio * spec1[cand_global].  The upper bound is
    band[1] so second-harmonic energy (spec1 extends to 2×band[1]) is never
    mistaken for a competing cardiac fundamental.
    """
    if cand_hz >= low_candidate_hz:
        return False
    hi_mask = (freqs >= high_candidate_preference_hz) & (freqs <= band[1])
    if not hi_mask.any():
        return False
    cand_mag = float(spec1[cand_global])
    if cand_mag == 0.0:
        return False
    max_hi_mag = float(spec1[hi_mask].max())
    return max_hi_mag >= high_competitor_min_mag_ratio * cand_mag


def estimate_rate_from_phase(
    phase: np.ndarray,
    fs: float,
    band: tuple,
    f_r_hz: float | None = None,
    k_max: int = 6,
    ahet_deviation_hz: float = 0.1,
    eca_mode: str = "legacy",
    ahet_gate_mode: str = "legacy",
    eca_forbidden_guard_hz: float = 0.0,
    eca_cardiac_guard_hz: float = 0.10,
    k_max_cap: int = 10,
    candidate_min_second_harmonic_ratio_db: float = 1.0,
    candidate_min_prominence: float = 3.0,
    low_candidate_hz: float = 1.20,
    high_candidate_preference_hz: float = 1.25,
    high_competitor_min_mag_ratio: float = 0.80,
    candidate_min_peak_to_floor_db: float = 0.0,
    low_candidate_min_peak_to_floor_db: float = 0.0,
) -> dict:
    """Estimate a rate (bpm) from a slow-time phase signal via band-limited FFT peak.

    When f_r_hz is None: original bandpass + argmax path (backward compat).
    When f_r_hz is provided: ECA respiration cancellation + AHET second-harmonic
    consistency check (arXiv:2503.07062, OpenAI cross-review findings #1-#4).

    eca_mode:
      "legacy"                      -- existing behavior (hard floor k=1..4 always projected).
                                       BROKEN: erases the cardiac peak when 4*f_r ~= HR.
      "skip_forbidden_harmonics_v1" -- skip any k where k*f_r falls in
                                       [band_lo - guard, band_hi + guard].
                                       BROKEN: at ordinary f_r that is EVERY in-band
                                       harmonic, so ECA cancels nothing in the cardiac
                                       band (measured: 0.00 dB removed).
      "guard_cardiac_candidate_v1"  -- IMPLEMENTED, NOT PROMOTED. No config selects this mode.
                                       It FAILS its own acceptance test: when a respiratory
                                       harmonic outranks the heart pre-ECA (34% of hops on the
                                       paced-16 capture) the guard spares the DECOY, which AHET
                                       then confidently reports. See notes/plan_eca_forbidden_zone.md
                                       PART II. Kept only as a comparison arm.
                                       Spare only harmonics landing within
                                       eca_cardiac_guard_hz of the cardiac candidate;
                                       cancel every other harmonic up to the band ceiling.
                                       k_max is derived per window as
                                       min(k_max_cap, floor(band_hi / f_r)) because a fixed
                                       k_max=6 does not span the cardiac band at breathing
                                       rates <= 16 bpm (the 7th harmonic lands in-band and
                                       was being picked as the heartbeat).
                                       See notes/plan_eca_forbidden_zone.md.

    ahet_gate_mode:
      "legacy"    -- return early on first passing candidate (existing behavior)
      "strict_v1" -- evaluate all candidates; sequential rejection codes 1-4;
                     select first surviving candidate by magnitude order

    Physiological outlier gate: if f_r_hz is provided but outside [0.15, 0.60] Hz,
    falls back to no-ECA path with f_r_outlier=True.

    New return fields (all modes):
      candidate_rejection_code -- int array (AHET_MAX_CANDIDATES,), -1=gate_not_run
      all_candidates_rejected  -- bool, True only in strict_v1 when all rejected
      eca_skipped_harmonics    -- bool array, FIXED length (k_max_cap,), True for each k
                                  that was SPARED (in-band but deliberately not projected).
                                  Fixed length even though k_max_eff varies per window, so
                                  that stacking into an NPZ cannot break or silently
                                  truncate k > k_max (plan S8.1b).
      k_max_eff                -- int, harmonics considered this window
      n_eca_projected          -- int, harmonics actually projected out this window
    """
    # Physiological gate: an implausible f_r collapses ECA to garbage; skip it.
    _GATE_LO_HZ = 0.15   # 9 bpm  — below this is not real respiration
    _GATE_HI_HZ = 0.60   # 36 bpm — above this is not real respiration
    f_r_is_outlier = (f_r_hz is not None) and not (_GATE_LO_HZ <= f_r_hz <= _GATE_HI_HZ)

    x = np.asarray(phase, dtype=float)
    x = x - x.mean()

    _empty_rej_codes = np.full(AHET_MAX_CANDIDATES, -1, dtype=int)
    # Fixed reporting length (plan S8.1b): k_max_eff varies per window, but the artifact
    # shape must not, or NPZ stacking breaks / silently hides k > k_max.
    _skip_len = max(int(k_max_cap), int(k_max))
    _empty_eca_skip  = np.zeros(_skip_len, dtype=bool)

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
        freq_res_hz = float(freqs[1] - freqs[0]) if len(freqs) > 1 else 1.0
        peak_hz = float(band_freqs[0]) + parabolic_interpolate_peak(
            band_spec, peak_idx, freq_res_hz
        )
        n_fft = len(spectrum)
        return {
            "rate_bpm": peak_hz * 60.0,
            "peak_hz": peak_hz,
            "freqs_hz": freqs,
            "spectrum": spectrum,
            "spectrum_pre_eca": spectrum,
            "spectrum_first_pass": np.full(n_fft, np.nan, dtype=float),
            "spectrum_stage": 0,
            "band": band,
            "filtered": x_filt,
            "ahet_verified": False,
            "harmonic_suspect": False,
            "f_r_hz_used": f_r_hz,    # preserve original value (may be None or outlier)
            "f_r_outlier": f_r_is_outlier,
            "eca_applied": False,
            "phase_eca": np.full(n, np.nan, dtype=float),
            "accepted_candidate_rank": -1,
            "accepted_candidate_initial_hz": float("nan"),
            "accepted_candidate_refined_hz": float("nan"),
            "accepted_second_harmonic_refined_hz": float("nan"),
            "candidate_attempted": np.zeros(AHET_MAX_CANDIDATES, dtype=bool),
            "candidate_peak_bin_index": np.full(AHET_MAX_CANDIDATES, -1, dtype=int),
            "candidate_initial_hz": np.full(AHET_MAX_CANDIDATES, np.nan, dtype=float),
            "candidate_refined_hz": np.full(AHET_MAX_CANDIDATES, np.nan, dtype=float),
            "candidate_peak_magnitude": np.full(AHET_MAX_CANDIDATES, np.nan, dtype=float),
            "candidate_prominence": np.full(AHET_MAX_CANDIDATES, np.nan, dtype=float),
            "candidate_argmax_fallback": np.zeros(AHET_MAX_CANDIDATES, dtype=bool),
            "second_peak_bin_hz": np.full(AHET_MAX_CANDIDATES, np.nan, dtype=float),
            "second_peak_refined_hz": np.full(AHET_MAX_CANDIDATES, np.nan, dtype=float),
            "second_peak_magnitude": np.full(AHET_MAX_CANDIDATES, np.nan, dtype=float),
            "comparison_floor": np.full(AHET_MAX_CANDIDATES, np.nan, dtype=float),
            "peak_to_floor_ratio": np.full(AHET_MAX_CANDIDATES, np.nan, dtype=float),
            "peak_to_floor_ratio_db": np.full(AHET_MAX_CANDIDATES, np.nan, dtype=float),
            "region_available": np.zeros(AHET_MAX_CANDIDATES, dtype=bool),
            "candidate_passed": np.zeros(AHET_MAX_CANDIDATES, dtype=bool),
            "ahet_attempt_spectrum": np.full(
                (AHET_MAX_CANDIDATES, n_fft), np.nan, dtype=float
            ),
            "candidate_rejection_code": _empty_rej_codes.copy(),
            "all_candidates_rejected": False,
            "eca_skipped_harmonics": _empty_eca_skip.copy(),
            "k_max_eff": 0,
            "n_eca_projected": 0,
            "eca_retained_ks": np.zeros(_skip_len, dtype=bool),
            "eca_cols_retained": np.zeros((_skip_len, 2), dtype=bool),
            "n_eca_cols_selected": 0,
            "n_eca_cols_retained": 0,
            "n_eca_cols_dropped": 0,
            "candidate_eca_skipped": np.zeros((AHET_MAX_CANDIDATES, _skip_len), dtype=bool),
            "candidate_eca_retained_ks": np.zeros((AHET_MAX_CANDIDATES, _skip_len), dtype=bool),
            "candidate_n_eca_cols_retained": np.zeros(AHET_MAX_CANDIDATES, dtype=int),
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

    # Frequency axis and cardiac mask are signal-independent (depend only on n, fs).
    spec_bp, freqs = _spec(x_bp)
    spectrum_pre_eca = spec_bp          # true pre-ECA snapshot; evidence-only
    cardiac_mask = (freqs >= band[0]) & (freqs <= band[1])
    if not cardiac_mask.any():
        raise ValueError("No FFT bins in cardiac band.")

    # Fix A — provisional cardiac guard.
    # When k×f_r coincides with the cardiac frequency, first-pass ECA with no
    # guard removes the cardiac signal from spec1, so candidate selection never
    # finds the true cardiac peak.  Guard the dominant bandpassed peak above the
    # minimum margin so the cardiac signal survives into spec1.
    #
    # ORDERING (plan S5.2): this MUST come before the skip set is computed —
    # guard_cardiac_candidate_v1 derives its skip set from prov_cand_hz.
    cardiac_zone = cardiac_mask & (freqs >= band[0] + MIN_CARDIAC_BAND_MARGIN_HZ)
    prov_cand_hz = (float(freqs[cardiac_zone][np.argmax(spec_bp[cardiac_zone])])
                    if cardiac_zone.any() else None)

    # ── ECA harmonic-selection parameters, per mode ─────────────────────────────
    eca_skipped_harmonics = np.zeros(_skip_len, dtype=bool)
    if eca_mode == "guard_cardiac_candidate_v1":
        # Derive k_max per window: a fixed k_max=6 does not span the cardiac band at
        # f_r <= 0.267 Hz (16 bpm), leaving the 7th harmonic in-band and uncancellable.
        k_max_eff   = derive_k_max_eff(f_r_hz, band[1], k_max_cap)
        guard_hz    = eca_cardiac_guard_hz
        hard_floor  = 0                       # the guard is the ONLY protection
    elif eca_mode == "skip_forbidden_harmonics_v1":
        k_max_eff   = int(k_max)
        guard_hz    = 0.15                    # legacy k>=5 guard, retained for this arm
        hard_floor  = 4
    else:                                     # "legacy"
        k_max_eff   = int(k_max)
        guard_hz    = 0.15
        hard_floor  = 4

    def _skip_set_for(cand_hz: float | None) -> frozenset:
        """Skip set for one ECA pass, centred on the candidate that pass is testing.

        Per-pass (plan S5.3): the second pass must guard around the candidate it is
        actually evaluating, not around the provisional peak.
        """
        if eca_mode == "guard_cardiac_candidate_v1":
            return cardiac_skip_ks(f_r_hz, cand_hz, k_max_eff, eca_cardiac_guard_hz)
        if eca_mode == "skip_forbidden_harmonics_v1":
            fz_lo = band[0] - eca_forbidden_guard_hz
            fz_hi = band[1] + eca_forbidden_guard_hz
            return frozenset(
                k for k in range(1, k_max_eff + 1) if fz_lo <= k * f_r_hz <= fz_hi
            )
        return frozenset()

    def _eca(sig: np.ndarray, cand_hz: float | None) -> tuple[np.ndarray, frozenset, dict]:
        sk = _skip_set_for(cand_hz)
        out, diag = eca_project(
            sig, f_r_hz, fs, k_max=k_max_eff, cardiac_candidate_hz=cand_hz,
            skip_ks=sk, band_hi=band[1], cardiac_guard_hz=guard_hz,
            hard_floor_k=hard_floor, return_diagnostics=True, diag_len=_skip_len,
        )
        return out, sk, diag

    def _skip_vec(sk: frozenset) -> np.ndarray:
        """Fixed-length bool vector of SPARED orders (in-band but deliberately not projected)."""
        v = np.zeros(_skip_len, dtype=bool)
        for k in sk:
            if 1 <= k <= _skip_len:
                v[k - 1] = True
        return v

    # ── First pass: guard around the PROVISIONAL candidate ──────────────────────
    x_eca1, skip_ks_set, eca_diag1 = _eca(x_bp, prov_cand_hz)
    spec1, _ = _spec(x_eca1)

    # Candidate-wise ECA metadata (cross-review 2026-07-14, comment 12.4). The whole v1 failure
    # was rank-0 vs rank-1 being judged on DIFFERENT spectra, so a reviewer must be able to
    # reconstruct which projection each candidate was evaluated under.
    candidate_eca_skipped = np.zeros((AHET_MAX_CANDIDATES, _skip_len), dtype=bool)
    candidate_eca_retained_ks = np.zeros((AHET_MAX_CANDIDATES, _skip_len), dtype=bool)
    candidate_n_cols_retained = np.zeros(AHET_MAX_CANDIDATES, dtype=int)

    # Report what ECA actually did, from the same selector it used (no drift possible).
    projected_ks = eca_harmonic_ks(
        f_r_hz, k_max_eff, band_hi=band[1], cardiac_candidate_hz=prov_cand_hz,
        skip_ks=skip_ks_set, cardiac_guard_hz=guard_hz, hard_floor_k=hard_floor,
    )
    n_eca_projected = len(projected_ks)
    # "Skipped" = in-band (within k_max_eff) but deliberately NOT projected.
    for k in range(1, k_max_eff + 1):
        if k * f_r_hz > band[1] + _CEIL_EPS:
            break
        if k not in projected_ks and k <= _skip_len:
            eca_skipped_harmonics[k - 1] = True

    band_mask_idx = np.where(cardiac_mask)[0]
    band_spec1 = spec1[band_mask_idx]

    # Top-3 candidates by prominence within cardiac band
    noise_floor = float(np.median(band_spec1))
    peaks_local, peak_properties = find_peaks(
        band_spec1, prominence=max(noise_floor * 0.1, 1e-12)
    )
    used_argmax_fallback = len(peaks_local) == 0
    if used_argmax_fallback:
        peaks_local = np.array([int(np.argmax(band_spec1))])
        peak_prominences = np.array([np.nan], dtype=float)
    else:
        peak_prominences = np.asarray(peak_properties["prominences"], dtype=float)
    sort_order = np.argsort(band_spec1[peaks_local])[::-1][:AHET_MAX_CANDIDATES]
    sorted_by_mag = peaks_local[sort_order]
    sorted_prominences = peak_prominences[sort_order]
    candidates_global = band_mask_idx[sorted_by_mag]

    # Fix B — minimum margin filter.
    # Reject candidates within MIN_CARDIAC_BAND_MARGIN_HZ of the band's lower
    # edge; these are filter-edge leakage artifacts, not cardiac peaks.  If all
    # candidates are filtered, the for-loop below does not execute and the
    # all-candidates-failed path returns NaN (no fabricated estimate).
    valid = freqs[candidates_global] >= band[0] + MIN_CARDIAC_BAND_MARGIN_HZ
    candidates_global = candidates_global[valid]
    sorted_prominences = sorted_prominences[valid]

    candidate_attempted       = np.zeros(AHET_MAX_CANDIDATES, dtype=bool)
    candidate_peak_bin_index  = np.full(AHET_MAX_CANDIDATES, -1, dtype=int)
    candidate_initial_hz      = np.full(AHET_MAX_CANDIDATES, np.nan, dtype=float)
    candidate_refined_hz      = np.full(AHET_MAX_CANDIDATES, np.nan, dtype=float)
    candidate_peak_magnitude  = np.full(AHET_MAX_CANDIDATES, np.nan, dtype=float)
    candidate_prominence      = np.full(AHET_MAX_CANDIDATES, np.nan, dtype=float)
    candidate_argmax_fallback = np.zeros(AHET_MAX_CANDIDATES, dtype=bool)
    second_peak_bin_hz        = np.full(AHET_MAX_CANDIDATES, np.nan, dtype=float)
    second_peak_refined_hz    = np.full(AHET_MAX_CANDIDATES, np.nan, dtype=float)
    second_peak_magnitude     = np.full(AHET_MAX_CANDIDATES, np.nan, dtype=float)
    comparison_floor          = np.full(AHET_MAX_CANDIDATES, np.nan, dtype=float)
    peak_to_floor_ratio       = np.full(AHET_MAX_CANDIDATES, np.nan, dtype=float)
    peak_to_floor_ratio_db    = np.full(AHET_MAX_CANDIDATES, np.nan, dtype=float)
    region_available          = np.zeros(AHET_MAX_CANDIDATES, dtype=bool)
    candidate_passed          = np.zeros(AHET_MAX_CANDIDATES, dtype=bool)
    candidate_rejection_code  = np.full(AHET_MAX_CANDIDATES, -1, dtype=int)
    ahet_attempt_spectrum     = np.full(
        (AHET_MAX_CANDIDATES, len(freqs)), np.nan, dtype=float
    )

    # Cache second-pass ECA results for the accept step in strict_v1
    _cand_eca: dict = {}  # rank -> (spec2, x_eca2, f_h_ref, f_h2_ref)

    def _common_fields() -> dict:
        """Shared fields included in every ECA+AHET return."""
        return {
            "freqs_hz": freqs,
            "band": band,
            "f_r_hz_used": f_r_hz,
            "f_r_outlier": False,
            "eca_applied": True,
            "candidate_attempted": candidate_attempted,
            "candidate_peak_bin_index": candidate_peak_bin_index,
            "candidate_initial_hz": candidate_initial_hz,
            "candidate_refined_hz": candidate_refined_hz,
            "candidate_peak_magnitude": candidate_peak_magnitude,
            "candidate_prominence": candidate_prominence,
            "candidate_argmax_fallback": candidate_argmax_fallback,
            "second_peak_bin_hz": second_peak_bin_hz,
            "second_peak_refined_hz": second_peak_refined_hz,
            "second_peak_magnitude": second_peak_magnitude,
            "comparison_floor": comparison_floor,
            "peak_to_floor_ratio": peak_to_floor_ratio,
            "peak_to_floor_ratio_db": peak_to_floor_ratio_db,
            "region_available": region_available,
            "candidate_passed": candidate_passed,
            "ahet_attempt_spectrum": ahet_attempt_spectrum,
            "candidate_rejection_code": candidate_rejection_code,
            "eca_skipped_harmonics": eca_skipped_harmonics,
            "k_max_eff": int(k_max_eff),
            "n_eca_projected": int(n_eca_projected),
            # Basis diagnostics — what ECA ACTUALLY projected, not what it selected (20.6)
            "eca_retained_ks": eca_diag1["retained_ks"],
            "eca_cols_retained": eca_diag1["cols_retained"],
            "n_eca_cols_selected": int(eca_diag1["n_cols_selected"]),
            "n_eca_cols_retained": int(eca_diag1["n_cols_retained"]),
            "n_eca_cols_dropped": int(eca_diag1["n_cols_dropped"]),
            # Candidate-wise ECA metadata (12.4)
            "candidate_eca_skipped": candidate_eca_skipped,
            "candidate_eca_retained_ks": candidate_eca_retained_ks,
            "candidate_n_eca_cols_retained": candidate_n_cols_retained,
            "spectrum_pre_eca": spectrum_pre_eca,
        }

    def _build_accepted(rank: int) -> dict:
        spec2, x_eca2, f_h_ref, f_h2_ref = _cand_eca[rank]
        f_final = 0.5 * f_h_ref + 0.5 * (f_h2_ref / 2.0)
        return {
            "rate_bpm": f_final * 60.0,
            "peak_hz": f_final,
            "spectrum": spec2,
            "spectrum_first_pass": spec1,
            "spectrum_stage": 2,
            "filtered": x_eca2,
            "ahet_verified": True,
            "harmonic_suspect": False,
            "phase_eca": x_eca2,
            "ahet_second_harmonic_hz": f_h2_ref,
            "accepted_candidate_rank": rank,
            "accepted_candidate_initial_hz": float(candidate_initial_hz[rank]),
            "accepted_candidate_refined_hz": f_h_ref,
            "accepted_second_harmonic_refined_hz": f_h2_ref,
            "all_candidates_rejected": False,
            **_common_fields(),
        }

    # Try each candidate with AHET consistency check
    for candidate_rank, (cand_global, prominence) in enumerate(
        zip(candidates_global, sorted_prominences)
    ):
        cand_global = int(cand_global)
        cand_hz = refine_freq_hz(spec1, freqs, cand_global)
        candidate_attempted[candidate_rank] = True
        candidate_peak_bin_index[candidate_rank] = cand_global
        candidate_initial_hz[candidate_rank] = cand_hz
        candidate_peak_magnitude[candidate_rank] = float(spec1[cand_global])
        candidate_prominence[candidate_rank] = float(prominence)
        candidate_argmax_fallback[candidate_rank] = used_argmax_fallback

        # Second-pass ECA: guard against suppressing a harmonic near the cardiac candidate
        # (OpenAI cross-review finding #4).
        # The skip set is RECOMPUTED for the candidate actually being tested (plan S5.3):
        # reusing the first-pass set would judge a rank-2 candidate with a guard parked on
        # the provisional peak.
        x_eca2, sk2, diag2 = _eca(x_bp, cand_hz)
        candidate_eca_skipped[candidate_rank] = _skip_vec(sk2)
        candidate_eca_retained_ks[candidate_rank] = diag2["retained_ks"]
        candidate_n_cols_retained[candidate_rank] = diag2["n_cols_retained"]
        spec2, _ = _spec(x_eca2)
        ahet_attempt_spectrum[candidate_rank] = spec2
        candidate_refined_hz[candidate_rank] = refine_freq_hz(spec2, freqs, cand_global)

        # AHET: local 2nd harmonic search [2×cand_hz ± ahet_deviation_hz]
        # Local window, not global to 4.0 Hz (OpenAI cross-review finding #3)
        lo2 = 2.0 * cand_hz - ahet_deviation_hz
        hi2 = 2.0 * cand_hz + ahet_deviation_hz
        mask2 = (freqs >= lo2) & (freqs <= hi2)
        if not mask2.any():
            if ahet_gate_mode == "strict_v1":
                candidate_rejection_code[candidate_rank] = 1  # no_second_harmonic_region
            continue
        region_available[candidate_rank] = True

        region2 = spec2[mask2]
        peak2_local = int(np.argmax(region2))
        peak2_global = int(np.where(mask2)[0][peak2_local])
        peak2_magnitude = float(region2[peak2_local])
        second_peak_bin_hz[candidate_rank] = float(freqs[peak2_global])
        f_h2_ref = refine_freq_hz(spec2, freqs, peak2_global)
        second_peak_refined_hz[candidate_rank] = f_h2_ref
        second_peak_magnitude[candidate_rank] = peak2_magnitude

        # Noise floor from the cardiac band of the second-pass ECA spectrum
        noise2 = float(np.median(spec2[cardiac_mask]))
        comparison_floor[candidate_rank] = noise2
        if noise2 == 0.0:
            ratio = float("inf") if peak2_magnitude > 0.0 else float("nan")
        else:
            ratio = peak2_magnitude / noise2
        peak_to_floor_ratio[candidate_rank] = ratio
        if np.isnan(ratio):
            ratio_db = float("nan")
        elif ratio == 0.0:
            ratio_db = float("-inf")
        else:
            ratio_db = 20.0 * np.log10(ratio)
        peak_to_floor_ratio_db[candidate_rank] = ratio_db

        f_h_ref = candidate_refined_hz[candidate_rank]
        _cand_eca[candidate_rank] = (spec2, x_eca2, f_h_ref, f_h2_ref)

        if ahet_gate_mode == "strict_v1":
            # Sequential rejection checks (codes 2-4, 6-7)
            if ratio_db < candidate_min_second_harmonic_ratio_db:
                candidate_rejection_code[candidate_rank] = 2  # ratio_db_low
                continue
            prom = float(prominence)
            if np.isnan(prom) or prom < candidate_min_prominence:
                candidate_rejection_code[candidate_rank] = 3  # prominence_low (NaN = argmax fallback)
                continue
            if _check_low_candidate_competitor(
                cand_global, cand_hz, spec1, freqs,
                low_candidate_hz, high_candidate_preference_hz,
                high_competitor_min_mag_ratio, band,
            ):
                candidate_rejection_code[candidate_rank] = 4  # low_candidate_competitor
                continue
            # Step 6.2 floor gates — applied after the harmonic-existence check so
            # code 6 means "harmonic found but absolute SNR still too weak"
            if ratio_db < candidate_min_peak_to_floor_db:
                candidate_rejection_code[candidate_rank] = 6  # peak_to_floor_db_low
                continue
            if cand_hz < low_candidate_hz and ratio_db < low_candidate_min_peak_to_floor_db:
                candidate_rejection_code[candidate_rank] = 7  # low_candidate_floor_db_low
                continue
            candidate_rejection_code[candidate_rank] = 0  # passed
            candidate_passed[candidate_rank] = True
            # Do not return early — evaluate all candidates first
        else:
            # Legacy mode: return early on first passing candidate
            passed = peak2_magnitude > noise2
            candidate_passed[candidate_rank] = passed
            if passed:
                # Refine both fundamental and 2nd harmonic, then blend
                # (OpenAI cross-review finding: use 2nd harmonic as refinement, not just gate)
                f_final = 0.5 * f_h_ref + 0.5 * (f_h2_ref / 2.0)
                return {
                    "rate_bpm": f_final * 60.0,
                    "peak_hz": f_final,
                    "spectrum": spec2,
                    "spectrum_first_pass": spec1,
                    "spectrum_stage": 2,
                    "filtered": x_eca2,
                    "ahet_verified": True,
                    "harmonic_suspect": False,
                    "phase_eca": x_eca2,
                    "ahet_second_harmonic_hz": f_h2_ref,
                    "accepted_candidate_rank": candidate_rank,
                    "accepted_candidate_initial_hz": cand_hz,
                    "accepted_candidate_refined_hz": f_h_ref,
                    "accepted_second_harmonic_refined_hz": f_h2_ref,
                    "all_candidates_rejected": False,
                    **_common_fields(),
                }

    # ------------------------------------------------------------------ #
    # Post-loop: strict_v1 select first passing candidate OR all-failed   #
    # ------------------------------------------------------------------ #
    if ahet_gate_mode == "strict_v1":
        # Slots that were never entered get code 5 (not_attempted), not -1.
        # -1 is reserved for legacy / gate-not-run.
        not_attempted = ~candidate_attempted
        candidate_rejection_code[not_attempted] = 5
        passed_ranks = [r for r in range(AHET_MAX_CANDIDATES) if candidate_passed[r]]
        if passed_ranks:
            return _build_accepted(passed_ranks[0])
        # All candidates rejected by strict_v1 gate — separate state from harmonic_suspect
        return {
            "rate_bpm": float("nan"),
            "peak_hz": float("nan"),
            "spectrum": spec1,
            "spectrum_first_pass": spec1,
            "spectrum_stage": 1,
            "filtered": x_eca1,
            "ahet_verified": False,
            "harmonic_suspect": False,
            "phase_eca": x_eca1,
            "accepted_candidate_rank": -1,
            "accepted_candidate_initial_hz": float("nan"),
            "accepted_candidate_refined_hz": float("nan"),
            "accepted_second_harmonic_refined_hz": float("nan"),
            "all_candidates_rejected": True,
            **_common_fields(),
        }

    # Legacy: all candidates failed AHET — do NOT fabricate a value (CLAUDE.md §4)
    return {
        "rate_bpm": float("nan"),
        "peak_hz": float("nan"),
        "freqs_hz": freqs,
        "spectrum": spec1,
        "spectrum_pre_eca": spectrum_pre_eca,
        "spectrum_first_pass": spec1,
        "spectrum_stage": 1,
        "band": band,
        "filtered": x_eca1,
        "ahet_verified": False,
        "harmonic_suspect": True,
        "f_r_hz_used": f_r_hz,
        "f_r_outlier": False,
        "eca_applied": True,
        "phase_eca": x_eca1,
        "accepted_candidate_rank": -1,
        "accepted_candidate_initial_hz": float("nan"),
        "accepted_candidate_refined_hz": float("nan"),
        "accepted_second_harmonic_refined_hz": float("nan"),
        "candidate_attempted": candidate_attempted,
        "candidate_peak_bin_index": candidate_peak_bin_index,
        "candidate_initial_hz": candidate_initial_hz,
        "candidate_refined_hz": candidate_refined_hz,
        "candidate_peak_magnitude": candidate_peak_magnitude,
        "candidate_prominence": candidate_prominence,
        "candidate_argmax_fallback": candidate_argmax_fallback,
        "second_peak_bin_hz": second_peak_bin_hz,
        "second_peak_refined_hz": second_peak_refined_hz,
        "second_peak_magnitude": second_peak_magnitude,
        "comparison_floor": comparison_floor,
        "peak_to_floor_ratio": peak_to_floor_ratio,
        "peak_to_floor_ratio_db": peak_to_floor_ratio_db,
        "region_available": region_available,
        "candidate_passed": candidate_passed,
        "ahet_attempt_spectrum": ahet_attempt_spectrum,
        "candidate_rejection_code": candidate_rejection_code,
        "all_candidates_rejected": False,
        "eca_skipped_harmonics": eca_skipped_harmonics,
    }


def select_range_bin(profiles: np.ndarray, range_axis: np.ndarray, gate: tuple) -> dict:
    """Pick the chest range bin within the distance gate.

    profiles: (frames, chirps, rx, bins) complex range profiles.
    Strategy: compute mean squared magnitude (energy) per range bin, averaged over
    frames, chirps and RX.  The gate bin with the highest energy is the dominant
    chest reflector.

    Prints the top-5 gate bins by energy so the selection is auditable.
    Returns bin_idx, range_m, and energy (full-spectrum array, shape=(bins,)).
    """
    gate_mask = (range_axis >= gate[0]) & (range_axis <= gate[1])
    if not gate_mask.any():
        raise ValueError("Distance gate falls outside the range axis; check range resolution.")
    gate_bins = np.where(gate_mask)[0]

    energy      = np.mean(np.abs(profiles) ** 2, axis=(0, 1, 2))  # (bins,)
    gate_energy = energy[gate_bins]

    # Top-5 by energy — printed so a surprising selection can be diagnosed
    top5_local = np.argsort(gate_energy)[::-1][:5]
    print("Top-5 range bins by energy:")
    for local_idx in top5_local:
        b = int(gate_bins[local_idx])
        print(f"  bin {b:3d}  {float(range_axis[b]):.3f} m  energy={float(gate_energy[local_idx]):.4f}")

    best_local = int(np.argmax(gate_energy))
    bin_idx    = int(gate_bins[best_local])
    range_m    = float(range_axis[bin_idx])
    print(f"Selected bin (max energy): bin {bin_idx} ({range_m:.3f} m)")

    return {
        "bin_idx": bin_idx,
        "range_m": range_m,
        "energy":  energy,
    }


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
    use_eca: bool = True,
    k_max: int = 6,
    ahet_deviation_hz: float = 0.1,
) -> list[dict]:
    """Phase-locked variant: selects the range bin ONCE for the entire cube.

    Avoids bin-hopping and phase discontinuities that occur when select_range_bin
    is called independently on each window slice.

    locked_bin: if provided, skip energy-based bin selection and use this bin index
      directly. Decouples bin choice from the trim window so different trim lengths
      give comparable results. If None, existing energy-based selection is used.

    use_eca: if True (default), estimate f_r per window and apply ECA + AHET
      (exp002 path, arXiv:2503.07062). If False, use bandpass+argmax only — the
      exp001 baseline path, preserved as the no-harmonic-rejection reference.

    Per-window processing when use_eca=True (exp002 ECA + AHET, arXiv:2503.07062):
      1. Estimate and refine f_r from the window phase (parabolic interpolation).
      2. Pass f_r directly to estimate_rate_from_phase(); the physiological outlier gate
         inside that function falls back to no-ECA if f_r is outside [0.15, 0.60] Hz.
      3. Record f_r_hz_used and f_r_outlier from the returned dict for diagnostics.

    Each returned dict includes all intermediate signals for diagnosis (CLAUDE.md §5.4).
    Selected result keys:
      f_r_hz_used  — f_r fed to estimate_rate_from_phase() (raw, unsmoothed; NaN if no-ECA)
      f_r_raw_hz   — alias for f_r_hz_used (kept for CSV backward compat)
      f_r_outlier  — True if physiological gate fired (ECA was skipped this window)
      heart_spectrum_stage — 0 no-ECA, 1 failed AHET first pass, 2 accepted second pass
      start_frame/end_frame — end-exclusive frame bounds within the supplied cube
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

        # Respiration rate (always estimated for RR output)
        resp = estimate_rate_from_phase(win_phase, params.fs_hz, params.resp_band_hz)
        resp_peak_raw_index = int(
            np.argmin(np.abs(resp["freqs_hz"] - resp["peak_hz"]))
        )
        resp_peak_raw_hz = float(resp["freqs_hz"][resp_peak_raw_index])
        resp_peak_refined_hz = refine_freq_hz(
            resp["spectrum"], resp["freqs_hz"], resp_peak_raw_index
        )

        if use_eca:
            # ECA + AHET path: refine f_r, then pass to estimate_rate_from_phase
            f_r_hz = resp_peak_refined_hz
            heart = estimate_rate_from_phase(
                win_phase, params.fs_hz, params.heart_band_hz, f_r_hz=f_r_hz,
                k_max=k_max, ahet_deviation_hz=ahet_deviation_hz,
            )
            if heart.get("f_r_outlier"):
                print(
                    f"Window {win_idx}: f_r={f_r_hz * 60:.1f} bpm outside physiological gate"
                    f" -- ECA skipped, fell back to bandpass+argmax"
                )
        else:
            # Baseline path: bandpass + argmax, no ECA (exp001 reference)
            f_r_hz = float("nan")
            heart = estimate_rate_from_phase(win_phase, params.fs_hz, params.heart_band_hz)

        results.append({
            "hr_bpm": heart["rate_bpm"],
            "rr_bpm": resp["rate_bpm"],
            "chosen_bin": locked_bin,
            "chosen_range_m": locked_range_m,
            "bin_locked": True,
            "bin_energy": bin_energy,
            # Phase evidence
            "phase_unwrapped": phase[s:e],
            "phase_clean": win_phase,
            "phase_eca": heart["phase_eca"],
            # Respiration evidence
            "resp_freqs_hz": resp["freqs_hz"],
            "resp_spectrum": resp["spectrum"],
            "resp_peak_raw_index": resp_peak_raw_index,
            "resp_peak_raw_hz": resp_peak_raw_hz,
            "resp_peak_refined_hz": resp_peak_refined_hz,
            # Heart-spectrum evidence
            "heart_freqs_hz": heart["freqs_hz"],
            "heart_spectrum_first_pass": heart["spectrum_first_pass"],
            "heart_spectrum": heart["spectrum"],
            "heart_spectrum_stage": heart["spectrum_stage"],
            "heart_peak_hz": heart["peak_hz"],
            # Accepted AHET candidate
            "accepted_candidate_rank": heart["accepted_candidate_rank"],
            "accepted_candidate_initial_hz": heart[
                "accepted_candidate_initial_hz"
            ],
            "accepted_candidate_refined_hz": heart[
                "accepted_candidate_refined_hz"
            ],
            "accepted_second_harmonic_refined_hz": heart[
                "accepted_second_harmonic_refined_hz"
            ],
            # Fixed-width AHET attempt evidence
            "candidate_attempted": heart["candidate_attempted"],
            "candidate_peak_bin_index": heart["candidate_peak_bin_index"],
            "candidate_initial_hz": heart["candidate_initial_hz"],
            "candidate_refined_hz": heart["candidate_refined_hz"],
            "candidate_peak_magnitude": heart["candidate_peak_magnitude"],
            "candidate_prominence": heart["candidate_prominence"],
            "candidate_argmax_fallback": heart["candidate_argmax_fallback"],
            "second_peak_bin_hz": heart["second_peak_bin_hz"],
            "second_peak_refined_hz": heart["second_peak_refined_hz"],
            "second_peak_magnitude": heart["second_peak_magnitude"],
            "comparison_floor": heart["comparison_floor"],
            "peak_to_floor_ratio": heart["peak_to_floor_ratio"],
            "peak_to_floor_ratio_db": heart["peak_to_floor_ratio_db"],
            "region_available": heart["region_available"],
            "candidate_passed": heart["candidate_passed"],
            "ahet_attempt_spectrum": heart["ahet_attempt_spectrum"],
            # Alignment metadata
            "schema_version": INTERMEDIATE_SCHEMA_VERSION,
            "window_index": win_idx,
            "start_frame": s,
            "end_frame": e,
            "window_frames": window_frames,
            "hop_frames": hop_frames,
            "frame_rate_hz": float(params.fs_hz),
            "range_bin": locked_bin,
            "range_m": locked_range_m,
            "eca_applied": bool(heart["eca_applied"]),
            "f_r_outlier": bool(heart["f_r_outlier"]),
            "ahet_verified": bool(heart["ahet_verified"]),
            # Legacy aliases retained for current experiment runners.
            "window_start_frame": s,
            "window_end_frame": e,
            "harmonic_suspect": heart.get("harmonic_suspect", False),
            "f_r_hz_used": float(f_r_hz),
            "f_r_raw_hz": float(f_r_hz),
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
