"""Unit tests for ECA + AHET harmonic rejection (exp002).

Tests verify each new DSP stage in isolation — the "verify before chaining" rule
from CLAUDE.md §5.3. Reference: arXiv:2503.07062 (Tang et al., 2025).

Signal model used throughout:
  fs=20 Hz, N=400 (20 s window), f_r=0.25 Hz (15 bpm), f_h=71/60 Hz (~71 bpm).
  Respiratory harmonics up to 4th order with decreasing amplitude simulate the
  real-capture failure mode: 4th harmonic (60 bpm) dominates the cardiac band
  before ECA and would win a naive argmax.

Run: pytest tests/test_eca_ahet.py -v
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import vitals  # noqa: E402

FS = 20.0
N  = 400                # 20 s window at 20 Hz
F_R = 0.25              # 15 bpm respiration
F_H = 71.0 / 60.0       # ~1.1833 Hz = 71 bpm cardiac


def _make_harmonic_signal(seed: int = 0) -> np.ndarray:
    """Chest phase with 4 respiratory harmonics + cardiac fundamental + 2nd harmonic.

    Amplitudes chosen so the 4th respiratory harmonic (1.0 Hz = 60 bpm) exceeds
    the cardiac fundamental (1.183 Hz = 71 bpm) in the raw spectrum — the exact
    failure mode that defeated exp001.
    """
    rng = np.random.default_rng(seed)
    t = np.arange(N) / FS
    sig = (
        3.00 * np.sin(2 * np.pi * 1 * F_R * t) +   # resp fundamental
        1.50 * np.sin(2 * np.pi * 2 * F_R * t) +   # resp 2nd harmonic
        1.00 * np.sin(2 * np.pi * 3 * F_R * t) +   # resp 3rd harmonic
        0.75 * np.sin(2 * np.pi * 4 * F_R * t) +   # resp 4th harmonic — 60 bpm
        0.30 * np.sin(2 * np.pi * 1 * F_H * t) +   # cardiac fundamental
        0.15 * np.sin(2 * np.pi * 2 * F_H * t) +   # cardiac 2nd harmonic
        0.03 * rng.standard_normal(N)
    )
    return sig


def _run_single_window(phase: np.ndarray) -> dict:
    """Run one synthetic phase window through the locked-bin production path."""
    cube = np.exp(1j * phase)[:, None, None, None]
    params = vitals.VitalsParams(
        fs_hz=FS,
        gate_min_m=1.3,
        gate_max_m=1.6,
    )
    results = vitals.run_pipeline_locked(
        cube,
        np.array([1.4]),
        params,
        window_frames=N,
        hop_frames=N,
        locked_bin=0,
    )
    assert len(results) == 1
    return results[0]


def test_eca_removes_respiratory_harmonic():
    """ECA projection removes the 4th respiratory harmonic from the cardiac band.

    Before ECA: 4th harmonic at 1.0 Hz (60 bpm, amplitude 0.75) dominates over
    the cardiac at 1.183 Hz (71 bpm, amplitude 0.30).
    After ECA: cardiac should be the dominant peak in [0.8, 2.0] Hz.
    """
    sig = _make_harmonic_signal()

    # Verify that before ECA the 4th harmonic (1.0 Hz) wins the argmax in cardiac band
    raw_spec = np.abs(np.fft.rfft(sig * np.hanning(N)))
    freqs = np.fft.rfftfreq(N, d=1.0 / FS)
    cardiac_mask = (freqs >= 0.8) & (freqs <= 2.0)
    raw_peak_hz = freqs[cardiac_mask][np.argmax(raw_spec[cardiac_mask])]
    assert abs(raw_peak_hz - 1.0) < 0.1, (
        f"Pre-condition failed: expected 4th harmonic (~1.0 Hz) to dominate raw, "
        f"got {raw_peak_hz:.3f} Hz"
    )

    # After ECA the cardiac (1.183 Hz) should dominate
    sig_eca = vitals.eca_project(sig, F_R, FS)
    eca_spec = np.abs(np.fft.rfft(sig_eca * np.hanning(N)))
    eca_peak_hz = freqs[cardiac_mask][np.argmax(eca_spec[cardiac_mask])]
    assert abs(eca_peak_hz - F_H) < 0.1, (
        f"After ECA expected cardiac peak near {F_H:.3f} Hz, got {eca_peak_hz:.3f} Hz"
    )


def test_ahet_refinement_improves_accuracy():
    """ECA + AHET recovers HR within 1.5 bpm and sets ahet_verified=True.

    The 3 bpm FFT bin width (20 s @ 20 Hz) limits argmax precision; parabolic
    interpolation + 2nd-harmonic blending should tighten the estimate to <1.5 bpm.
    """
    sig = _make_harmonic_signal()
    out = vitals.estimate_rate_from_phase(sig, FS, vitals.HEART_BAND_HZ, f_r_hz=F_R)

    assert out["ahet_verified"] is True, (
        f"Expected AHET to verify the cardiac peak; got ahet_verified={out['ahet_verified']}, "
        f"harmonic_suspect={out['harmonic_suspect']}"
    )
    assert not np.isnan(out["rate_bpm"]), "rate_bpm should not be NaN when AHET verifies"
    assert abs(out["rate_bpm"] - 71.0) <= 1.5, (
        f"Expected HR within 1.5 bpm of 71; got {out['rate_bpm']:.2f} bpm"
    )


def test_nan_on_no_credible_candidate():
    """Signal with only respiration harmonics (no cardiac) returns NaN + harmonic_suspect=True.

    After ECA removes the respiratory subspace, the cardiac band contains only
    noise. No candidate can pass the AHET 2nd-harmonic check, so the pipeline
    returns NaN rather than fabricating a value (CLAUDE.md §4).
    """
    t = np.arange(N) / FS
    # Purely sinusoidal respiratory harmonics, no cardiac, no noise.
    # After ECA the cardiac band is numerically zero — no AHET check can pass.
    sig = (
        3.00 * np.sin(2 * np.pi * 1 * F_R * t) +
        1.50 * np.sin(2 * np.pi * 2 * F_R * t) +
        1.00 * np.sin(2 * np.pi * 3 * F_R * t) +
        0.75 * np.sin(2 * np.pi * 4 * F_R * t)
    )
    out = vitals.estimate_rate_from_phase(sig, FS, vitals.HEART_BAND_HZ, f_r_hz=F_R)

    assert np.isnan(out["rate_bpm"]), (
        f"Expected NaN for a respiration-only signal; got {out['rate_bpm']:.1f} bpm"
    )
    assert out["harmonic_suspect"] is True
    assert out["ahet_verified"] is False


def test_successful_ahet_window_records_consistent_intermediates():
    out = _run_single_window(_make_harmonic_signal())

    required_fields = {
        "phase_unwrapped",
        "phase_clean",
        "phase_eca",
        "resp_freqs_hz",
        "resp_spectrum",
        "resp_peak_raw_index",
        "resp_peak_raw_hz",
        "resp_peak_refined_hz",
        "heart_freqs_hz",
        "heart_spectrum_first_pass",
        "heart_spectrum",
        "heart_spectrum_stage",
        "heart_peak_hz",
        "accepted_candidate_rank",
        "accepted_candidate_initial_hz",
        "accepted_candidate_refined_hz",
        "accepted_second_harmonic_refined_hz",
        "candidate_attempted",
        "candidate_peak_bin_index",
        "candidate_initial_hz",
        "candidate_refined_hz",
        "candidate_peak_magnitude",
        "candidate_prominence",
        "candidate_argmax_fallback",
        "second_peak_bin_hz",
        "second_peak_refined_hz",
        "second_peak_magnitude",
        "comparison_floor",
        "peak_to_floor_ratio",
        "peak_to_floor_ratio_db",
        "region_available",
        "candidate_passed",
        "ahet_attempt_spectrum",
        "schema_version",
        "window_index",
        "start_frame",
        "end_frame",
        "window_frames",
        "hop_frames",
        "frame_rate_hz",
        "range_bin",
        "range_m",
        "eca_applied",
        "f_r_outlier",
        "ahet_verified",
    }
    assert required_fields <= out.keys()
    assert "start_epoch" not in out
    assert "end_epoch" not in out

    rank = out["accepted_candidate_rank"]
    assert out["heart_spectrum_stage"] == 2
    assert out["ahet_verified"] is True
    assert 0 <= rank < vitals.AHET_MAX_CANDIDATES
    assert out["candidate_passed"].shape == (vitals.AHET_MAX_CANDIDATES,)
    assert out["ahet_attempt_spectrum"].shape == (
        vitals.AHET_MAX_CANDIDATES,
        len(out["heart_freqs_hz"]),
    )
    assert out["candidate_passed"].sum() == 1
    assert bool(out["candidate_passed"][rank])
    assert out["accepted_candidate_initial_hz"] == pytest.approx(
        out["candidate_initial_hz"][rank]
    )
    assert out["accepted_candidate_refined_hz"] == pytest.approx(
        out["candidate_refined_hz"][rank]
    )
    assert out["accepted_second_harmonic_refined_hz"] == pytest.approx(
        out["second_peak_refined_hz"][rank]
    )
    np.testing.assert_allclose(
        out["heart_spectrum"], out["ahet_attempt_spectrum"][rank]
    )

    assert out["schema_version"] == vitals.INTERMEDIATE_SCHEMA_VERSION
    assert out["window_index"] == 0
    assert out["start_frame"] == 0
    assert out["end_frame"] == N
    assert out["window_frames"] == N
    assert out["hop_frames"] == N
    assert out["frame_rate_hz"] == FS
    assert out["range_bin"] == 0
    assert out["range_m"] == pytest.approx(1.4)


def test_failed_ahet_window_uses_first_pass_stage():
    t = np.arange(N) / FS
    phase = (
        3.00 * np.sin(2 * np.pi * 1 * F_R * t)
        + 1.50 * np.sin(2 * np.pi * 2 * F_R * t)
        + 1.00 * np.sin(2 * np.pi * 3 * F_R * t)
        + 0.75 * np.sin(2 * np.pi * 4 * F_R * t)
    )
    out = _run_single_window(phase)

    assert out["accepted_candidate_rank"] == -1
    assert out["heart_spectrum_stage"] == 1
    assert out["ahet_verified"] is False
    assert not out["candidate_passed"].any()
    assert np.isnan(out["accepted_candidate_initial_hz"])
    assert np.isnan(out["accepted_candidate_refined_hz"])
    assert np.isnan(out["accepted_second_harmonic_refined_hz"])
    np.testing.assert_allclose(
        out["heart_spectrum"], out["heart_spectrum_first_pass"]
    )


def test_respiratory_outlier_window_uses_no_eca_stage():
    t = np.arange(N) / FS
    phase = (
        3.0 * np.sin(2 * np.pi * 0.1 * t)
        + 0.4 * np.sin(2 * np.pi * 1.2 * t)
    )
    out = _run_single_window(phase)

    assert out["f_r_outlier"] is True
    assert out["eca_applied"] is False
    assert out["heart_spectrum_stage"] == 0
    assert out["accepted_candidate_rank"] == -1
    assert np.isnan(out["phase_eca"]).all()
    assert np.isnan(out["heart_spectrum_first_pass"]).all()
    assert not out["candidate_attempted"].any()
    assert not out["candidate_passed"].any()


def test_argmax_fallback_flag_when_find_peaks_returns_nothing(monkeypatch):
    monkeypatch.setattr(
        vitals,
        "find_peaks",
        lambda *args, **kwargs: (np.array([], dtype=int), {}),
    )

    out = _run_single_window(_make_harmonic_signal())

    assert bool(out["candidate_attempted"][0])
    assert bool(out["candidate_argmax_fallback"][0])
    assert not out["candidate_argmax_fallback"][1:].any()
    assert np.isnan(out["candidate_prominence"][0])


# ─────────────────────────────────────────────────────────────────────────────
# guard_cardiac_candidate_v1 — the ECA forbidden-zone fix.
# Plan: notes/plan_eca_forbidden_zone.md §8.3, §8.4.
#
# The bug: skip_forbidden_harmonics_v1 skipped EVERY harmonic landing inside the
# cardiac band — at ordinary breathing rates that is all of them — so ECA cancelled
# nothing in the band it exists to clean (measured on real data: 0.00 dB removed).
# ─────────────────────────────────────────────────────────────────────────────

FS_G = 20.0
N_G = 600                      # 30 s window, matching the live/paper config
BAND_G = (0.8, 2.0)

# Gate settings copied from scripts/live_demo_config.yaml so these tests exercise the
# real AHET gates, not wide-open defaults.
GATES_G = dict(
    candidate_min_second_harmonic_ratio_db=1.0,
    candidate_min_prominence=3.0,
    candidate_min_peak_to_floor_db=2.0,
    low_candidate_min_peak_to_floor_db=4.0,
)


def _phase_g(f_r, f_h, resp_amp=1.0, heart_amp=0.30, k_list=(1, 2, 3, 4, 5, 6, 7),
             seed=0, n=N_G, fs=FS_G):
    """Respiratory comb + cardiac fundamental AND its 2nd harmonic.

    The cardiac 2nd harmonic is what AHET verifies against — a pure cardiac sine is
    unverifiable by construction and would be an unfair signal (see _make_harmonic_signal).
    """
    rng = np.random.default_rng(seed)
    t = np.arange(n) / fs
    x = np.zeros(n)
    for k in k_list:
        x += (resp_amp / k) * np.sin(2 * np.pi * k * f_r * t + 0.3 * k)
    x += heart_amp * np.sin(2 * np.pi * f_h * t + 0.7)
    x += 0.5 * heart_amp * np.sin(2 * np.pi * 2 * f_h * t + 0.2)   # cardiac 2nd harmonic
    x += 0.01 * rng.standard_normal(n)
    return x


def _inband_power(spec, freqs, band=BAND_G):
    m = (freqs >= band[0]) & (freqs <= band[1])
    return float(np.sum(spec[m] ** 2))


def _run_g(phase, f_r, mode, guard=0.10, k_max=6, k_max_cap=10):
    return vitals.estimate_rate_from_phase(
        phase, FS_G, BAND_G, f_r_hz=f_r, k_max=k_max, eca_mode=mode,
        ahet_gate_mode="strict_v1", eca_cardiac_guard_hz=guard, k_max_cap=k_max_cap,
        **GATES_G,
    )


def test_regression_old_mode_cancels_nothing_in_band():
    """The bug, pinned: skip_forbidden_harmonics_v1 removes ~0 dB inside the cardiac band."""
    f_r, f_h = 0.30, 65 / 60
    out = _run_g(_phase_g(f_r, f_h), f_r, "skip_forbidden_harmonics_v1")
    pre = _inband_power(out["spectrum_pre_eca"], out["freqs_hz"])
    post = _inband_power(out["spectrum_first_pass"], out["freqs_hz"])
    removed_db = 10 * np.log10(post / pre)
    assert abs(removed_db) < 0.01, f"expected ~0 dB removed, got {removed_db:+.2f} dB"


def test_new_mode_cancels_noncolliding_harmonics_in_band():
    """§8.3 — the regression the bug caused: in-band harmonics must actually be removed."""
    f_r, f_h = 0.30, 65 / 60          # |4·f_r − f_h| = 0.117 Hz — no collision
    out = _run_g(_phase_g(f_r, f_h), f_r, "guard_cardiac_candidate_v1")
    pre = _inband_power(out["spectrum_pre_eca"], out["freqs_hz"])
    post = _inband_power(out["spectrum_first_pass"], out["freqs_hz"])
    removed_db = 10 * np.log10(post / pre)

    # ── What this test asserts, and why it changed on 2026-07-26 ──────────────────────────
    # Cross-review LFR-06. The original assertion was `removed_db < -3.0` on TOTAL in-band
    # power. After the LFR-01 band-pass fix that ratio became -2.87 dB, and my first response
    # was to relax the threshold and add an absolute guard `post < 5.146e3`, justified as
    # pinning "residual contamination".
    #
    # Codex was right that this is the wrong physical quantity, and the guard was actively
    # unsafe. Total in-band power is dominated by the two things ECA must PRESERVE, not
    # remove — measured on this exact signal:
    #
    #     bin              pre      post     change
    #     k=4  1.200 Hz  37.392   37.391    -0.00 dB   <- deliberately SPARED (guard)
    #     cardiac 1.067 Hz 37.577  37.576   -0.00 dB   <- must survive
    #     k=3  0.900 Hz  40.240    2.359   -24.64 dB   <- projected out
    #     k=5  1.500 Hz  29.983    2.074   -23.20 dB   <- projected out
    #     k=6  1.800 Hz  25.063    1.256   -26.00 dB   <- projected out
    #
    # So a LOWER total would also be produced by erasing the cardiac peak — the exact
    # over-cancellation regression this test exists to catch would have made it pass more
    # easily. The absolute guard is therefore removed and replaced by targeted per-bin
    # assertions on the quantities that actually define correct cancellation.
    # ──────────────────────────────────────────────────────────────────────────────────────
    freqs = out["freqs_hz"]
    pre_spec, post_spec = out["spectrum_pre_eca"], out["spectrum_first_pass"]
    skipped = set((np.flatnonzero(out["eca_skipped_harmonics"]) + 1).tolist())

    def _atten_db(hz):
        b = int(np.argmin(np.abs(freqs - hz)))
        return 20 * np.log10(max(post_spec[b], 1e-30) / max(pre_spec[b], 1e-30))

    # (a) every SELECTED in-band respiratory harmonic is materially cancelled.
    # Margin 15 dB against an observed 23-26 dB: comfortably inside the real behaviour, far
    # outside the ~0 dB the pre-fix `skip_forbidden_harmonics_v1` bug produced.
    projected = [k for k in range(1, out["k_max_eff"] + 1)
                 if BAND_G[0] <= k * f_r <= BAND_G[1] and k not in skipped]
    assert projected, "no in-band harmonic was projected — the test signal is not exercising ECA"
    for k in projected:
        a = _atten_db(k * f_r)
        assert a < -15.0, (
            f"harmonic k={k} at {k * f_r:.3f} Hz was only attenuated {a:+.2f} dB "
            f"(expected < -15 dB); ECA is not cancelling what it selected"
        )

    # (b) the cardiac peak is PRESERVED. This is the assertion the old total-power guard
    # could not make, and the one that fails first on an over-cancellation regression.
    cardiac_change = _atten_db(f_h)
    assert cardiac_change > -1.0, (
        f"cardiac tone at {f_h:.4f} Hz lost {cardiac_change:+.2f} dB — ECA is eating the "
        f"signal it exists to expose"
    )
    # ...and so is any deliberately spared harmonic.
    for k in sorted(skipped):
        if BAND_G[0] <= k * f_r <= BAND_G[1]:
            a = _atten_db(k * f_r)
            assert a > -1.0, f"spared harmonic k={k} was attenuated {a:+.2f} dB despite the guard"

    # (c) coarse secondary check only — retained for continuity with the original test, and
    # explicitly NOT interpreted as "residual contamination" (see above).
    assert removed_db < -2.0, f"expected material in-band suppression, got {removed_db:+.2f} dB"
    assert out["n_eca_projected"] >= 3, "non-colliding in-band harmonics must be projected out"


def test_new_mode_spares_the_colliding_harmonic_and_keeps_the_heart():
    """§8.3 — a harmonic landing ON the cardiac tone must be spared, not cancelled."""
    f_r = 0.271
    f_h = 4 * f_r                      # exact collision at 1.084 Hz (65 bpm)
    out = _run_g(_phase_g(f_r, f_h), f_r, "guard_cardiac_candidate_v1")
    skipped = np.flatnonzero(out["eca_skipped_harmonics"]) + 1
    assert 4 in skipped, f"k=4 collides with the heart and must be spared; skipped={skipped}"
    freqs = out["freqs_hz"]
    b = int(np.argmin(np.abs(freqs - f_h)))
    assert out["spectrum_first_pass"][b] > 0.5 * out["spectrum_pre_eca"][b], (
        "cardiac tone was erased by ECA despite the guard"
    )


def test_k_max_eff_spans_the_band_at_low_breathing_rates():
    """§5.7 — the 7th harmonic is in-band at 16 bpm and must not be beyond reach."""
    f_r = 16 / 60.0                    # 7·f_r = 1.867 Hz, inside [0.8, 2.0]
    out = _run_g(_phase_g(f_r, 72 / 60), f_r, "guard_cardiac_candidate_v1")
    assert out["k_max_eff"] >= 7, f"k_max_eff={out['k_max_eff']} cannot reach the in-band 7th"


def test_k_max_eff_is_bounded_by_the_cap():
    """§5.7 — a bad/low f_r must not silently expand the ECA subspace."""
    out = _run_g(_phase_g(0.16, 70 / 60), 0.16, "guard_cardiac_candidate_v1", k_max_cap=10)
    assert out["k_max_eff"] == 10      # floor(2.0/0.16) = 12, capped to 10


def test_skipped_harmonics_artifact_has_fixed_length():
    """§8.1b — shape must not vary with k_max_eff, or NPZ stacking breaks/truncates."""
    outs = [
        _run_g(_phase_g(f_r, 70 / 60), f_r, "guard_cardiac_candidate_v1", k_max_cap=10)
        for f_r in (0.20, 0.267, 0.30, 0.40)
    ]
    assert {len(o["eca_skipped_harmonics"]) for o in outs} == {10}
    assert len({o["k_max_eff"] for o in outs}) > 1, "k_max_eff should vary across these f_r"


def test_derive_k_max_eff_ceiling_is_inclusive():
    """Second review, finding 1 — ECA's ceiling must match the spectrum mask (<= band_hi)."""
    assert vitals.derive_k_max_eff(0.2, 2.0, 20) == 10      # 10 x 0.2 == 2.0 exactly
    assert vitals.derive_k_max_eff(0.3, 2.0, 20) == 6       # 7 x 0.3 = 2.1 > 2.0
    assert vitals.derive_k_max_eff(16 / 60, 2.0, 20) == 7


# ─────────────────────────────────────────────────────────────────────────────
# XFAIL REMOVED 2026-07-26 — cross-review LFR-01/LFR-02 closed this synthetic hole.
#
# This test was `xfail(strict=True)` with the note "so it flips to XPASS the moment it is
# fixed". It flipped. The historical reason is preserved verbatim:
#
#   "KNOWN DESIGN HOLE in guard_cardiac_candidate_v1 — plan S7.1 materialised. prov_cand_hz
#    is the argmax of the CONTAMINATED pre-ECA spectrum, so when a respiratory harmonic
#    outranks the heart (34% of hops on the paced-16 capture), the guard SPARES the decoy.
#    The decoy survives ECA at full strength, becomes rank-0, and strict_v1 returns the first
#    passing candidate — the true heart sits at rank 1 with p2f=33 dB and is never reached.
#    `legacy` gets this right precisely because it unconditionally cancels k<=4. This mode is
#    NOT promoted in any config. The extended-ceiling repair (v2) was REJECTED: it needs the
#    2k*f_r line cancelled, but a f_r error of 1/10 of an FFT bin destroys that, and on real
#    captures the high-k harmonics are not coherent lines at all.
#    See notes/plan_eca_forbidden_zone.md PART IV."
#
# WHY IT NOW PASSES. The hole's mechanism was "the decoy survives ECA at full strength".
# Under the brick-wall band-pass it did, because the mask admitted the respiratory harmonic's
# out-of-band leak unattenuated, letting the decoy outrank the heart in the pre-ECA spectrum
# that prov_cand_hz is drawn from. With the Butterworth response restored the decoy no longer
# wins that ranking. Measured across seeds 0-5 on this exact signal:
#
#     brick wall   : 2 true HR, 3 decoy (~71 bpm), 1 other wrong HR   -> 4/6 hard failures
#     butterworth  : 6 true HR (96.00 bpm)                            -> 0/6 hard failures
#
# WHAT THIS DOES **NOT** ESTABLISH. The quoted "34% of hops on the paced-16 capture" is a
# REAL-DATA claim, and it has not been re-measured — the replays have not been reprocessed
# under the new filter at the time of writing. Six synthetic seeds are not four captures.
# Treat the design hole as CLOSED ON SYNTHETICS ONLY until the paced-16 replay is re-run and
# the hop percentage re-measured. `guard_cardiac_candidate_v1` remains un-promoted in every
# config, and this test passing is not grounds to promote it (HANDOFF: promotion is blocked
# on `experiments/exp_eca_modes`, which does not exist).
# ─────────────────────────────────────────────────────────────────────────────
def test_does_not_confidently_report_a_respiratory_harmonic_as_hr():
    """§8.4 — the plan's BIGGEST risk (§7.1), attacked directly.

    The dominant pre-ECA in-band peak is a respiratory harmonic (the provisional candidate
    is therefore WRONG, and the first-pass guard protects the decoy). The true cardiac peak
    is weaker but present, with a 2nd harmonic.

    Required: strict AHET must not confidently return the decoy. Returning the true HR is
    the good outcome; returning NaN is acceptable. Reporting the harmonic is a hard failure.

    PASSES since 2026-07-26 (returns the true 96 bpm), as a consequence of the LFR-01/LFR-02
    band-pass fix rather than of any change to the ECA/AHET logic. See the block above for
    what that does and does not establish.
    """
    f_r = 0.30                          # 4·f_r = 1.20 Hz = 72 bpm — the decoy
    f_h = 1.60                          # 96 bpm — true heart, far from every k·f_r
    phase = _phase_g(f_r, f_h, resp_amp=1.0, heart_amp=0.08,   # heart much weaker than decoy
                     k_list=(1, 2, 3, 4, 5, 6), seed=3)
    out = _run_g(phase, f_r, "guard_cardiac_candidate_v1")

    # Cross-review 2026-07-14 (comment 3): asserting only "not the decoy" lets this XPASS for
    # the WRONG reason — a change that confidently reports some other wrong HR (85, 110 bpm)
    # would have satisfied it. When AHET verifies, the answer must be the TRUE HR. NaN is the
    # only other acceptable outcome.
    true_bpm = f_h * 60.0               # 96 bpm
    if bool(out["ahet_verified"]):
        assert abs(out["rate_bpm"] - true_bpm) <= 3.0, (
            f"verified a wrong HR: reported {out['rate_bpm']:.1f} bpm, true {true_bpm:.1f} bpm "
            f"(decoy = {4 * f_r * 60.0:.1f} bpm)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Stage 0 — review debt (cross-model review 2026-07-14, PART IV §23).
# These tests are independent of which ECA design eventually wins.
# ─────────────────────────────────────────────────────────────────────────────

def test_diagnostics_do_not_change_the_projected_signal():
    """Comment 8: enabling diagnostics must leave the projected signal NUMERICALLY unchanged.

    A diagnostic that perturbs the thing it measures is worse than no diagnostic.
    """
    f_r = 0.30
    x = _phase_g(f_r, 65 / 60)
    plain = vitals.eca_project(x, f_r, FS_G, k_max=6, band_hi=BAND_G[1])
    with_diag, diag = vitals.eca_project(
        x, f_r, FS_G, k_max=6, band_hi=BAND_G[1],
        return_diagnostics=True, diag_len=10,
    )
    assert np.array_equal(plain, with_diag), "diagnostics altered the projected signal"
    # ...and the pinned shapes hold regardless of k_max
    assert diag["selected_ks"].shape == (10,)
    assert diag["retained_ks"].shape == (10,)
    assert diag["cols_retained"].shape == (10, 2)


def test_basis_diagnostics_count_actual_columns_not_selected_orders():
    """Comment 20.6: n_eca_projected counts SELECTED orders; it cannot see columns that the
    Gram-Schmidt norm guard silently dropped. The diagnostics must report the real basis."""
    f_r = 0.30
    _, diag = vitals.eca_project(
        _phase_g(f_r, 65 / 60), f_r, FS_G, k_max=6, band_hi=BAND_G[1],
        return_diagnostics=True, diag_len=10,
    )
    n_sel_orders = int(diag["selected_ks"].sum())
    assert diag["n_cols_selected"] == 2 * n_sel_orders          # sin + cos per order
    assert diag["n_cols_retained"] == int(diag["cols_retained"].sum())
    assert (diag["n_cols_retained"] + diag["n_cols_dropped"]) == diag["n_cols_selected"]
    # every retained order must have at least one retained column, and vice versa
    assert np.array_equal(diag["retained_ks"], diag["cols_retained"].any(axis=1))


def test_candidate_wise_eca_metadata_is_recorded():
    """Comment 12.4: the v1 failure was rank-0 vs rank-1 being judged on DIFFERENT spectra.
    A reviewer must be able to reconstruct which projection each candidate was evaluated under."""
    f_r = 0.30
    out = _run_g(_phase_g(f_r, 65 / 60), f_r, "guard_cardiac_candidate_v1", k_max_cap=10)
    assert out["candidate_eca_skipped"].shape == (vitals.AHET_MAX_CANDIDATES, 10)
    assert out["candidate_eca_retained_ks"].shape == (vitals.AHET_MAX_CANDIDATES, 10)
    assert out["candidate_n_eca_cols_retained"].shape == (vitals.AHET_MAX_CANDIDATES,)
    attempted = out["candidate_attempted"]
    if attempted.any():
        # any attempted candidate must have had a real basis projected for it
        assert (out["candidate_n_eca_cols_retained"][attempted] > 0).all()


@pytest.mark.parametrize("br_bpm, expected_k", [(12, 20), (16, 15), (20, 12)])
def test_derive_k_max_eff_exact_ceiling_unrounded(br_bpm, expected_k):
    """Comment 20.2: use UNROUNDED f_r. 12/16/20 bpm all hit a 4.0 Hz ceiling EXACTLY.

    My v2 table said 16 bpm -> 14 only because I rounded f_r to 0.267 first;
    floor(4.0 / (16/60)) = 15. This is exactly the boundary error _CEIL_EPS exists to prevent.
    """
    f_r = br_bpm / 60.0                      # unrounded
    assert vitals.derive_k_max_eff(f_r, 4.0, 30) == expected_k
    assert abs(expected_k * f_r - 4.0) < 1e-9, "this case is supposed to hit the ceiling exactly"


def test_ahet_cannot_reject_a_respiratory_harmonic_on_2nd_harmonic_evidence_alone():
    """Committed, seeded replacement for the scratchpad probe (comment 20.1).

    THE STRUCTURAL FINDING: a decoy at k*f_r has its "2nd harmonic" at 2k*f_r — which is
    ITSELF a respiratory harmonic. So AHET's second-harmonic gate cannot, on its own,
    distinguish a respiratory harmonic from a heartbeat while 2k*f_r remains in the spectrum.

    Real breathing is not sinusoidal, so the comb extends past 2 Hz — model k=1..14.
    """
    f_r, f_h = 0.30, 1.60                    # decoy = 4*f_r = 1.20 Hz; heart = 96 bpm
    x = _phase_g(f_r, f_h, heart_amp=0.08, k_list=tuple(range(1, 15)), seed=3)
    x_bp = vitals.bandpass_filter(x - x.mean(), FS_G, BAND_G[0], 4.0)
    spec = np.abs(np.fft.rfft(x_bp * np.hanning(len(x_bp))))
    freqs = np.fft.rfftfreq(len(x_bp), 1 / FS_G)

    decoy_2nd = 2 * (4 * f_r)                # 2.40 Hz — where AHET looks for the decoy's 2nd
    resp_8th = 8 * f_r                       # 2.40 Hz — an actual respiratory harmonic
    assert abs(decoy_2nd - resp_8th) < 1e-9, "by construction these are the same frequency"

    mag_fake = spec[int(np.argmin(np.abs(freqs - decoy_2nd)))]
    mag_true = spec[int(np.argmin(np.abs(freqs - 2 * f_h)))]
    assert mag_fake > mag_true, (
        "AHET's 'evidence' for the DECOY should be stronger than for the true heart: "
        f"fake={mag_fake:.2f} at {decoy_2nd:.2f} Hz, true={mag_true:.2f} at {2*f_h:.2f} Hz"
    )
