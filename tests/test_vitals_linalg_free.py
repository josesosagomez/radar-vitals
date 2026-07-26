"""Regression guards for the LAPACK-free numerical replacements in src/vitals.py.

Cross-model review 2026-07-26 (`plans/m4_linalg_free_dsp_review.md`), finding **LFR-04**.

Why this file exists
--------------------
Both Codex and Claude Code verified numerically that single-pass modified Gram-Schmidt
(replacing `np.linalg.qr`) is sound across the pipeline's admissible domain:

    max |MGS - QR| signal difference   5.8e-15 .. 1.1e-14
    max basis orthogonality defect     1.0e-15 .. 1.3e-15
    min post/pre column-norm ratio     0.9926 .. 0.9955   (no near-degenerate columns)

The defect LFR-04 identifies is **not** the projector — it is that the evidence lived only in
two throwaway review probes. Under CLAUDE.md §3.1 a claim that cannot be regenerated does not
count, and nothing stopped a later change of window length, sample rate, harmonic cap or
breathing-rate range from silently invalidating it. These tests make the invariants durable.

`np.linalg.qr` is deliberately NOT used here. The reference projector is built by direct
construction, so the production path and the test path are both LAPACK-free (the live-demo
Windows environment hard-crashes inside the LAPACK call — see `src/vitals.py:eca_project`).

Domain covered: N in {400, 600} (the 20 s and 30 s production windows at fs = 20 Hz),
f_r = 0.15 .. 0.60 Hz (9 .. 36 bpm, spanning the respiration band and both boundaries),
k_max in {6, 10} (production default and the reporting cap).

Run: pytest tests/test_vitals_linalg_free.py -v
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import vitals  # noqa: E402

FS = 20.0
BAND_HI = 2.0

# Production window lengths at fs = 20 Hz: 400 = 20 s, 600 = 30 s (the frozen analysis window).
LENGTHS = (400, 600)
# Respiration band edges and interior. 0.15 and 0.60 bracket RESP_BAND_HZ = (0.1, 0.5) with
# margin, so a future band widening is still covered.
F_R_GRID = (0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60)
K_MAXES = (6, 10)

# Stated tolerances. Observed worst case is ~1e-14; these leave ~4 orders of headroom so the
# tests fail on a real regression, not on floating-point weather.
ORTHONORMAL_TOL = 1e-10   # |Q^T Q - I|_max over the retained basis
ORTHOGONAL_TOL = 1e-10    # |<col, clean>| / (|col| |clean|) for every selected column
IDEMPOTENT_TOL = 1e-10    # |P(P theta) - P(theta)| / |P(theta)|
DEFLATION_TOL = 1e-9      # |theta-form - deflation-form| / |theta-form|


def _theta(n: int, f_r: float, seed: int = 0) -> np.ndarray:
    """Deterministic chest-phase surrogate: respiratory harmonics + cardiac + seeded noise.

    Seeded per CLAUDE.md §3.3 — same input, same output, on every machine.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(n) / FS
    sig = np.zeros(n)
    for k, amp in enumerate((3.0, 1.5, 1.0, 0.75, 0.5, 0.3), start=1):
        sig += amp * np.sin(2 * np.pi * k * f_r * t + 0.1 * k)
    sig += 0.30 * np.sin(2 * np.pi * (71.0 / 60.0) * t)      # cardiac fundamental
    sig += 0.15 * np.sin(2 * np.pi * 2 * (71.0 / 60.0) * t)  # cardiac 2nd harmonic
    sig += 0.03 * rng.standard_normal(n)
    return sig


def _selected_columns(n: int, f_r: float, k_max: int) -> list[np.ndarray]:
    """The sin/cos basis columns eca_project() builds, in the same order it builds them.

    Mirrors src/vitals.py:eca_project so the tests exercise the real selection rule rather
    than a hand-picked harmonic set.
    """
    t = np.arange(n) / FS
    ks = vitals.eca_harmonic_ks(f_r, k_max, band_hi=BAND_HI)
    cols: list[np.ndarray] = []
    for k in ks:
        cols.append(np.sin(2 * np.pi * k * f_r * t))
        cols.append(np.cos(2 * np.pi * k * f_r * t))
    return cols


def _mgs_basis(cols: list[np.ndarray]) -> list[np.ndarray]:
    """Reference modified-Gram-Schmidt orthonormalisation (no LAPACK, no QR)."""
    basis: list[np.ndarray] = []
    for col in cols:
        v = np.asarray(col, dtype=float).copy()
        for q in basis:
            v -= q * float(np.sum(q * v))
        norm = float(np.sqrt(np.sum(v * v)))
        if norm > 1e-12:
            basis.append(v / norm)
    return basis


_DOMAIN = [
    pytest.param(n, f_r, k_max, id=f"N{n}-fr{f_r:.2f}-k{k_max}")
    for n in LENGTHS
    for f_r in F_R_GRID
    for k_max in K_MAXES
]


# ─────────────────────────────────────────────────────────────────────────────
# LFR-04 (a) — the retained basis is orthonormal to a stated tolerance
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("n,f_r,k_max", _DOMAIN)
def test_mgs_basis_is_orthonormal(n, f_r, k_max):
    """Single-pass MGS must not lose orthogonality anywhere in the admissible domain.

    This is the B6 risk from the review brief: without re-orthogonalisation, MGS degrades on
    an ill-conditioned basis. If a future change admits near-degenerate harmonic columns
    (closely spaced orders, a harmonic near Nyquist, a much shorter window), this fails.
    """
    cols = _selected_columns(n, f_r, k_max)
    if not cols:
        pytest.skip(f"no harmonics selected at f_r={f_r} (k*f_r all above band_hi)")
    basis = _mgs_basis(cols)
    assert basis, "MGS retained no columns for a non-empty selection"
    Q = np.column_stack(basis)
    gram_defect = np.max(np.abs(Q.T @ Q - np.eye(Q.shape[1])))
    assert gram_defect < ORTHONORMAL_TOL, (
        f"MGS basis lost orthogonality: |Q^T Q - I|_max = {gram_defect:.3e} "
        f"(tol {ORTHONORMAL_TOL:.0e}) at N={n}, f_r={f_r}, k_max={k_max}. "
        f"A second orthogonalisation pass is now warranted."
    )


# ─────────────────────────────────────────────────────────────────────────────
# LFR-04 (b) — the projected output is orthogonal to every retained column
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("n,f_r,k_max", _DOMAIN)
def test_projection_output_is_orthogonal_to_every_selected_column(n, f_r, k_max):
    """The defining property of ECA: no projected harmonic survives in the cleaned signal.

    Asserted against the ORIGINAL sin/cos columns, not the orthonormalised ones — the
    orthogonal complement of span{q} is the orthogonal complement of span{cols}, so this
    tests eca_project()'s actual output rather than a reconstruction of its internals.
    """
    cols = _selected_columns(n, f_r, k_max)
    if not cols:
        pytest.skip(f"no harmonics selected at f_r={f_r}")
    theta = _theta(n, f_r)
    clean = vitals.eca_project(theta, f_r, FS, k_max=k_max, band_hi=BAND_HI)
    clean_norm = float(np.linalg.norm(clean))
    assert clean_norm > 0, "projection annihilated the whole signal"
    for i, col in enumerate(cols):
        cos_angle = abs(float(np.dot(col, clean))) / (float(np.linalg.norm(col)) * clean_norm)
        assert cos_angle < ORTHOGONAL_TOL, (
            f"column {i} (order {i // 2 + 1}, {'sin' if i % 2 == 0 else 'cos'}) survives the "
            f"projection: |cos angle| = {cos_angle:.3e} (tol {ORTHOGONAL_TOL:.0e}) "
            f"at N={n}, f_r={f_r}, k_max={k_max}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# LFR-04 (c) — projection is idempotent
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("n,f_r,k_max", _DOMAIN)
def test_projection_is_idempotent(n, f_r, k_max):
    """P(P(theta)) == P(theta). A projector that is not idempotent is not a projector;
    re-running ECA on already-cleaned phase would keep removing signal."""
    if not _selected_columns(n, f_r, k_max):
        pytest.skip(f"no harmonics selected at f_r={f_r}")
    theta = _theta(n, f_r)
    once = vitals.eca_project(theta, f_r, FS, k_max=k_max, band_hi=BAND_HI)
    twice = vitals.eca_project(once, f_r, FS, k_max=k_max, band_hi=BAND_HI)
    denom = float(np.linalg.norm(once)) or 1.0
    rel = float(np.linalg.norm(twice - once)) / denom
    assert rel < IDEMPOTENT_TOL, (
        f"projection is not idempotent: relative change {rel:.3e} (tol {IDEMPOTENT_TOL:.0e}) "
        f"at N={n}, f_r={f_r}, k_max={k_max}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# LFR-04 (d) — no columns drop anywhere in the admissible domain
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("n,f_r,k_max", _DOMAIN)
def test_no_basis_columns_are_dropped_in_the_admissible_domain(n, f_r, k_max):
    """The 1e-12 norm guard must never fire on a legitimate harmonic set.

    If it does, `n_eca_projected` overstates the cancellation actually performed — the exact
    silent-coverage failure the 2026-07-14 review (comment 20.6) added the diagnostics to catch.
    A drop here means the guard has become load-bearing and its absolute 1e-12 scale (question
    B8) needs revisiting as a relative threshold.
    """
    cols = _selected_columns(n, f_r, k_max)
    if not cols:
        pytest.skip(f"no harmonics selected at f_r={f_r}")
    theta = _theta(n, f_r)
    _, diag = vitals.eca_project(
        theta, f_r, FS, k_max=k_max, band_hi=BAND_HI,
        return_diagnostics=True, diag_len=max(k_max, 10),
    )
    assert diag["n_cols_selected"] == len(cols)
    assert diag["n_cols_dropped"] == 0, (
        f"{diag['n_cols_dropped']} of {diag['n_cols_selected']} basis columns dropped at "
        f"N={n}, f_r={f_r}, k_max={k_max} — the norm guard fired on a legitimate harmonic set"
    )
    assert diag["n_cols_retained"] == len(cols)


# ─────────────────────────────────────────────────────────────────────────────
# LFR-04 (e) — projecting from the original theta agrees with sequential deflation
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("n,f_r,k_max", _DOMAIN)
def test_theta_form_agrees_with_sequential_deflation(n, f_r, k_max):
    """Review question B7.

    eca_project() computes `clean -= q * (q . theta)` against the ORIGINAL theta. The
    alternative, sequential deflation `clean -= q * (q . clean)`, is identical only when
    {q} is exactly orthonormal. Their agreement is therefore a direct, end-to-end measure of
    whether the orthonormality assumed by the chosen form actually holds in production.

    This is the test that fails first if MGS conditioning ever degrades — before any peak
    moves and before any validity flag flips.
    """
    cols = _selected_columns(n, f_r, k_max)
    if not cols:
        pytest.skip(f"no harmonics selected at f_r={f_r}")
    theta = _theta(n, f_r)
    theta_form = vitals.eca_project(theta, f_r, FS, k_max=k_max, band_hi=BAND_HI)

    deflation_form = theta.astype(float).copy()
    for q in _mgs_basis(cols):
        deflation_form = deflation_form - q * float(np.sum(q * deflation_form))

    denom = float(np.linalg.norm(theta_form)) or 1.0
    rel = float(np.linalg.norm(theta_form - deflation_form)) / denom
    assert rel < DEFLATION_TOL, (
        f"theta-form and sequential-deflation projections diverge: relative difference "
        f"{rel:.3e} (tol {DEFLATION_TOL:.0e}) at N={n}, f_r={f_r}, k_max={k_max}. "
        f"MGS orthogonality is no longer good enough for the theta-form to be valid."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Determinism (CLAUDE.md §3.1) — no RNG, no platform-dependent LAPACK
# ─────────────────────────────────────────────────────────────────────────────
def test_projection_is_bitwise_deterministic():
    """Same input, same output, byte for byte — repeated calls must not drift."""
    theta = _theta(600, 0.30)
    first = vitals.eca_project(theta, 0.30, FS, k_max=6, band_hi=BAND_HI)
    for _ in range(3):
        assert np.array_equal(
            first, vitals.eca_project(theta, 0.30, FS, k_max=6, band_hi=BAND_HI)
        ), "eca_project is not bitwise deterministic across repeated calls"


# ═════════════════════════════════════════════════════════════════════════════
# LFR-01 / LFR-02 — bandpass_filter response and edge policy
#
# `scipy.signal.filtfilt` is deliberately NOT imported here. It is the exact call that
# hard-crashes the live-demo Windows environment (`lfilter_zi` -> `np.linalg.solve`), and
# tests/test_step6_heart_rate.py already mocks it out for that reason. The response is
# therefore pinned analytically rather than by differencing against filtfilt.
# ═════════════════════════════════════════════════════════════════════════════

CARDIAC_BAND = (0.8, 2.0)
BP_HI = 4.0    # the ECA path's widened ceiling: min(2 * band_hi, 0.45 * fs)


def _gain_at(freq_hz: float, n: int = 600, lo: float = 0.8, hi: float = BP_HI,
             order: int = 4) -> float:
    """Zero-phase power gain |H(f)|^2 the filter applies at a single frequency.

    Measured through the public function on a pure tone. The amplitude ratio *is* the power
    gain here — the filter applies the forward-backward response |H|^2 — so it is NOT
    squared again. Reference points: 0.5 at each cutoff, ~1.0 in the passband interior.
    """
    t = np.arange(n) / FS
    tone = np.sin(2 * np.pi * freq_hz * t)
    out = vitals.bandpass_filter(tone, FS, lo, hi, order=order)
    interior = slice(n // 4, 3 * n // 4)      # ignore any edge transient
    return float(np.max(np.abs(out[interior])) / np.max(np.abs(tone[interior])))


# ── A5: length / parity ──────────────────────────────────────────────────────
@pytest.mark.parametrize("n", [64, 65, 127, 128, 399, 400, 599, 600, 601, 1200])
def test_output_length_always_equals_input_length(n):
    """Review question A5. The odd-reflect/crop path must be exact for even and odd n."""
    rng = np.random.default_rng(1)
    x = rng.standard_normal(n)
    out = vitals.bandpass_filter(x, FS, 0.8, BP_HI, order=4)
    assert out.shape == (n,), f"length changed: {n} -> {out.shape}"
    assert np.all(np.isfinite(out)), "filter produced non-finite samples"


# ── LFR-05: too-short input must fail loudly, never return unfiltered data ────
def test_too_short_input_raises_instead_of_returning_unfiltered_data():
    """Cross-review LFR-05.

    An earlier version returned the demeaned input unchanged for n < 4, and produced
    plausible-looking output for every n below one period of `lo`. The caller could not tell
    the difference between "band-passed" and "untouched" — a silent failure (CLAUDE.md §4).

    Boundary is derived, not tuned: n >= fs/lo, one full period of the lowest passed
    frequency. At fs=20 that is 25 samples for lo=0.8 Hz and 200 for lo=0.1 Hz.
    """
    min_n = int(np.ceil(FS / 0.8))          # 25
    assert min_n == 25

    for n in (0, 1, 2, 3, 4, 8, 24):        # smallest rejected lengths, incl. empty
        with pytest.raises(ValueError, match="too short"):
            vitals.bandpass_filter(np.zeros(n), FS, 0.8, BP_HI, order=4)

    # smallest ACCEPTED length works and is genuinely filtered
    out = vitals.bandpass_filter(np.arange(min_n, dtype=float), FS, 0.8, BP_HI, order=4)
    assert out.shape == (min_n,) and np.all(np.isfinite(out))

    # the respiration band has a longer minimum, and it is enforced independently
    with pytest.raises(ValueError, match="too short"):
        vitals.bandpass_filter(np.zeros(100), FS, 0.1, 0.5, order=4)
    assert vitals.bandpass_filter(np.zeros(200), FS, 0.1, 0.5, order=4).shape == (200,)

    # production window lengths must be entirely unaffected
    for n in (400, 600):
        assert vitals.bandpass_filter(np.zeros(n), FS, 0.8, BP_HI, order=4).shape == (n,)


def test_invalid_band_arguments_raise():
    """A nonsensical band must fail loudly rather than produce an empty or aliased response."""
    with pytest.raises(ValueError):
        vitals.bandpass_filter(np.zeros(600), FS, 2.0, 0.8, order=4)      # lo > hi
    with pytest.raises(ValueError):
        vitals.bandpass_filter(np.zeros(600), FS, 0.0, 2.0, order=4)      # lo == 0
    with pytest.raises(ValueError):
        vitals.bandpass_filter(np.zeros(600), FS, 0.8, FS / 2, order=4)   # hi at Nyquist


# ── A3: DC handling ──────────────────────────────────────────────────────────
def test_dc_is_removed_regardless_of_offset():
    """Review question A3. A large constant offset must not survive, and must not alter
    the AC result — the mean is removed before the transform and the band starts above DC."""
    t = np.arange(600) / FS
    tone = np.sin(2 * np.pi * 1.2 * t)
    base = vitals.bandpass_filter(tone, FS, 0.8, BP_HI, order=4)
    offset = vitals.bandpass_filter(tone + 1000.0, FS, 0.8, BP_HI, order=4)
    # DC is suppressed relative to the passband tone. The raw mean of the *cropped centre*
    # is not identically zero even though the extension's DC bin is annihilated — cropping a
    # zero-mean signal does not preserve a zero mean — so the invariant is asserted on the
    # spectrum, where it actually lives. Observed ~2.1e-3.
    spec = np.abs(np.fft.rfft(base))
    assert spec[0] / spec.max() < 0.01, "DC survived the filter"
    # A 1000x offset must change nothing at all. Observed max difference 4.7e-14.
    assert np.allclose(base, offset, atol=1e-9), "a constant offset changed the AC output"


# ── LFR-01: the response is Butterworth, not a rectangle ─────────────────────
def test_band_edge_gain_is_the_forward_backward_butterworth_half_power():
    """A zero-phase order-4 Butterworth has |H|^2 = 0.5 at each -3 dB cutoff.

    The brick-wall mask this replaced had gain 1.0 at both edges. That is the single
    cheapest discriminator between the two responses, so it is pinned on both edges.
    """
    assert _gain_at(0.8) == pytest.approx(0.5, abs=0.05), "lower band edge is not -3 dB"
    assert _gain_at(BP_HI) == pytest.approx(0.5, abs=0.05), "upper band edge is not -3 dB"


def test_transition_band_rolls_off_monotonically_below_the_cutoff():
    """A rectangle has no transition band: gain jumps 0 -> 1 at `lo`. A Butterworth
    decreases smoothly. Monotonicity below the cutoff is what suppresses the 2*f_r leak."""
    freqs = [0.80, 0.75, 0.70, 0.65, 0.60, 0.55, 0.50]
    gains = [_gain_at(f) for f in freqs]
    for lower, upper, gl, gu in zip(freqs[1:], freqs[:-1], gains[1:], gains[:-1]):
        assert gl < gu, (
            f"gain did not decrease from {upper} Hz ({gu:.4f}) to {lower} Hz ({gl:.4f}) — "
            f"the transition band is missing or non-monotone"
        )
    assert gains[-1] < 0.02, f"0.50 Hz leaks {gains[-1]:.4f} of its power into the cardiac band"


def test_passband_interior_gain_is_near_unity():
    """Inside the band the filter must be nearly transparent, or it re-weights the
    spectrum that argmax-in-band ranks."""
    for f in (1.0, 1.2, 1.5, 2.0, 3.0):
        assert _gain_at(f) == pytest.approx(1.0, abs=0.12), f"passband gain wrong at {f} Hz"


# ── LFR-01: the regression that would have caught the brick wall ─────────────
@pytest.mark.parametrize("f_r,max_ratio", [(0.30, 0.10), (0.34, 0.35), (0.36, 0.50)])
def test_respiratory_harmonic_below_the_band_does_not_outrank_the_cardiac_peak(f_r, max_ratio):
    """**The LFR-01 regression test.**

    A respiration harmonic at 2*f_r sitting just below the 0.8 Hz cutoff must not produce a
    spectral artifact in the guarded [0.8, 0.95) Hz margin that rivals the true cardiac peak.

    Under the brick-wall mask this ratio was 0.977 at f_r = 0.34 and **1.570** at f_r = 0.36 —
    above 1.0, meaning the artifact outranked the real peak and argmax-in-band selected it.
    f_r = 0.36 Hz is 21.6 bpm breathing, which the `sweep` capture steps through by design,
    so this was a live failure mode and not a corner case. With the Butterworth response
    restored the ratios are ~0.001 / 0.019 / 0.126.

    Thresholds are set roughly 3-4x above the restored values and far below the brick-wall
    values, so the test bites on a regression without tracking floating-point weather.
    """
    n = 600
    t = np.arange(n) / FS
    rng = np.random.default_rng(0)
    x = (20.0 * np.sin(2 * np.pi * 2 * f_r * t)      # dominant respiration harmonic
         + 1.0 * np.sin(2 * np.pi * 1.20 * t)        # weak true cardiac tone, 72 bpm
         + 0.01 * rng.standard_normal(n))

    filtered = vitals.bandpass_filter(x, FS, CARDIAC_BAND[0], BP_HI, order=4)
    spec = np.abs(np.fft.rfft(filtered * np.hanning(n)))
    freqs = np.fft.rfftfreq(n, d=1.0 / FS)

    margin_peak = float(spec[(freqs >= 0.8) & (freqs < 0.95)].max())
    true_peak = float(spec[(freqs >= 1.15) & (freqs < 1.25)].max())
    ratio = margin_peak / true_peak

    assert ratio < max_ratio, (
        f"2*f_r = {2 * f_r:.2f} Hz leaks an artifact {ratio:.3f}x the true cardiac peak "
        f"(limit {max_ratio}); at ratio >= 1.0 argmax-in-band would select the artifact"
    )


def test_cardiac_peak_is_recovered_through_the_filter():
    """End-to-end: the filter must not move the frequency it passes."""
    n = 600
    t = np.arange(n) / FS
    for true_hz in (1.0, 1.2, 1.5):
        x = np.sin(2 * np.pi * true_hz * t) + 0.05 * np.sin(2 * np.pi * 0.3 * t)
        filtered = vitals.bandpass_filter(x, FS, CARDIAC_BAND[0], BP_HI, order=4)
        spec = np.abs(np.fft.rfft(filtered * np.hanning(n)))
        freqs = np.fft.rfftfreq(n, d=1.0 / FS)
        m = (freqs >= CARDIAC_BAND[0]) & (freqs <= CARDIAC_BAND[1])
        peak_hz = float(freqs[m][np.argmax(spec[m])])
        assert abs(peak_hz - true_hz) < 0.05, (
            f"peak moved: true {true_hz:.2f} Hz, recovered {peak_hz:.2f} Hz"
        )


# ── LFR-02: the edge policy is not circular ──────────────────────────────────
def test_edge_policy_is_not_circular():
    """A window whose endpoints differ strongly is the case periodic extension corrupts.

    `rfft`/`irfft` filter the periodic extension, so a large end-to-end step injects
    broadband energy at both edges — energy that reaches `eca_project` *before* the Hann
    analysis taper and so cannot be tapered away. Odd reflection removes the step by
    construction. Here a ramp (out of band, should vanish) rides on an in-band tone; the
    recovered tone is checked at the edges, where circular wrap does its damage.
    """
    n = 600
    t = np.arange(n) / FS
    tone = np.sin(2 * np.pi * 1.21 * t)               # non-periodic in the window
    x = tone + 6.0 * (t / t[-1])                      # strong end-to-end step

    filtered = vitals.bandpass_filter(x, FS, CARDIAC_BAND[0], BP_HI, order=4)
    scale = float(np.max(np.abs(filtered[n // 4:3 * n // 4])))
    edge = np.max(np.abs(filtered[:40])) / scale
    assert edge < 1.2, (      # observed 0.9996 — no edge transient at all
        f"edge amplitude is {edge:.2f}x the interior — the ramp's wrap discontinuity is "
        f"leaking into the filtered signal, i.e. the extension is behaving circularly"
    )


# ── LFR-02 closure: end-to-end, through ECA-before-Hann, asserting VALIDITY ──
#
# Codex round 3: `test_edge_policy_is_not_circular` checks only the public filter, only the
# first 40 samples, only the cardiac band — it never reaches `eca_project` /
# `estimate_rate_from_phase`, which is where the original WANTED aimed. These tests close that
# gap: a strong endpoint trend is the case a circular extension corrupts, ECA sees the
# band-passed signal BEFORE the Hann analysis taper, so the taper cannot repair it. Adding the
# trend must not change the validity flag or move the recovered rate.

_GATES = dict(
    candidate_min_second_harmonic_ratio_db=1.0,
    candidate_min_prominence=3.0,
    candidate_min_peak_to_floor_db=2.0,
    low_candidate_min_peak_to_floor_db=4.0,
)


def _vitals_phase(n: int, f_r: float, f_h: float, seed: int = 0) -> np.ndarray:
    """Respiratory comb + cardiac fundamental + cardiac 2nd harmonic (AHET-verifiable)."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / FS
    x = np.zeros(n)
    for k in (1, 2, 3, 4, 5, 6):
        x += (1.0 / k) * np.sin(2 * np.pi * k * f_r * t + 0.3 * k)
    x += 0.30 * np.sin(2 * np.pi * f_h * t + 0.7)
    x += 0.15 * np.sin(2 * np.pi * 2 * f_h * t + 0.2)
    x += 0.01 * rng.standard_normal(n)
    return x


def _estimate(phase: np.ndarray, f_r: float) -> dict:
    return vitals.estimate_rate_from_phase(
        phase, FS, CARDIAC_BAND, f_r_hz=f_r, k_max=6,
        eca_mode="guard_cardiac_candidate_v1", ahet_gate_mode="strict_v1",
        eca_cardiac_guard_hz=0.10, k_max_cap=10, **_GATES,
    )


@pytest.mark.parametrize("n", [400, 600])
@pytest.mark.parametrize("trend", ["rising", "falling", "edge_step_start", "edge_step_end"])
def test_endpoint_trend_does_not_change_eca_ahet_validity_or_rate(n, trend):
    """A strong endpoint slope/step must not alter the estimate through the full ECA/AHET path.

    Both endpoints are exercised — ramps create the end-to-end mismatch a circular extension
    wraps into a discontinuity, and the edge steps perturb only the outer 5% of samples at one
    end or the other.

    NOT tested here: a step in the MIDDLE of the window. That was in the first draft of this
    test and it flipped `ahet_verified` True->False at N=400 — but it does so **identically
    under the old brick-wall filter**, so it is not an edge-policy effect. A 20x broadband
    transient mid-record is a genuine artifact and refusing to verify is the correct
    conservative response, not a filter defect. Keeping it here would have made this test
    assert something it cannot attribute to the edge policy.
    """
    f_r, f_h = 0.30, 1.60
    base = _vitals_phase(n, f_r, f_h)
    t = np.arange(n) / FS
    idx = np.arange(n)
    edge = max(1, n // 20)                 # outer 5%
    amp = 6.0
    add = {
        "rising": amp * (t / t[-1]),
        "falling": -amp * (t / t[-1]),
        "edge_step_start": np.where(idx < edge, amp, 0.0),
        "edge_step_end": np.where(idx >= n - edge, amp, 0.0),
    }[trend]

    clean = _estimate(base, f_r)
    trended = _estimate(base + add, f_r)

    # LFR-07: assert BOTH are verified, rather than merely that they agree. An earlier version
    # asserted `trended == clean` and guarded the rate/rank checks behind `if clean[...]`, so a
    # regression that made both paths invalid would have passed vacuously and skipped every
    # substantive assertion. This fixture is verified at N=400 and N=600 by construction —
    # invalid/invalid is not an acceptable alternative outcome here.
    assert bool(clean["ahet_verified"]), (
        f"fixture regression: the CLEAN signal is no longer AHET-verified at N={n} — this test "
        f"can no longer detect what it exists to detect"
    )
    assert bool(trended["ahet_verified"]), (
        f"a {trend} endpoint trend flipped ahet_verified True -> False at N={n} — edge "
        f"contamination is reaching the ECA fit"
    )

    d = abs(float(trended["rate_bpm"]) - float(clean["rate_bpm"]))
    assert d < 0.5, (
        f"a {trend} endpoint trend moved the verified rate by {d:.3f} bpm at N={n} "
        f"({clean['rate_bpm']:.3f} -> {trended['rate_bpm']:.3f})"
    )
    assert int(trended["accepted_candidate_rank"]) == int(clean["accepted_candidate_rank"]), (
        f"a {trend} endpoint trend changed the accepted candidate rank at N={n} "
        f"({clean['accepted_candidate_rank']} -> {trended['accepted_candidate_rank']})"
    )


@pytest.mark.parametrize("n", [400, 600])
def test_known_respiration_frequency_survives_an_endpoint_trend(n):
    """The respiration band is where the pad policy still differs most from filtfilt
    (6/486 raw peak bins on stored windows), so pin a known rate there explicitly."""
    f_r_true = 0.25                       # 15 bpm, a bin-resolvable interior rate
    t = np.arange(n) / FS
    tone = np.sin(2 * np.pi * f_r_true * t + 0.4)
    trend = 6.0 * (t / t[-1])

    for label, sig in (("clean", tone), ("trended", tone + trend)):
        out = vitals.bandpass_filter(sig, FS, 0.1, 0.5, order=4)
        spec = np.abs(np.fft.rfft(out * np.hanning(n)))
        freqs = np.fft.rfftfreq(n, d=1.0 / FS)
        m = (freqs >= 0.1) & (freqs <= 0.5)
        peak = float(freqs[m][np.argmax(spec[m])])
        assert abs(peak - f_r_true) < (FS / n) * 1.5, (
            f"{label}: respiration peak {peak:.4f} Hz is more than 1.5 bins from the true "
            f"{f_r_true} Hz at N={n}"
        )


def test_filtering_is_deterministic_and_response_cache_is_immutable():
    """Determinism (CLAUDE.md §3.1), plus: the cached response table must not be mutable
    through a returned reference, or one caller could corrupt every later window."""
    rng = np.random.default_rng(7)
    x = rng.standard_normal(600)
    first = vitals.bandpass_filter(x, FS, 0.8, BP_HI, order=4)
    for _ in range(3):
        assert np.array_equal(first, vitals.bandpass_filter(x, FS, 0.8, BP_HI, order=4))
    resp = vitals._zero_phase_butter_response(1198, FS, 0.8, BP_HI, 4)
    with pytest.raises(ValueError):
        resp[0] = 123.0


# ── LFR-03: `order` is a live parameter again ────────────────────────────────
def test_order_is_honoured_not_silently_ignored():
    """LFR-03. Before this fix `bandpass_filter` did `del order`, so callers were told they
    controlled roll-off when they did not. Higher order must give sharper rejection."""
    g2 = _gain_at(0.6, order=2)
    g4 = _gain_at(0.6, order=4)
    g6 = _gain_at(0.6, order=6)
    assert g6 < g4 < g2, (
        f"stopband rejection did not sharpen with order: "
        f"order2={g2:.5f}, order4={g4:.5f}, order6={g6:.5f} — `order` is being ignored"
    )
