"""Stage 1A — Is a respiratory line at the ECA "evidence" orders cancellable at all?

Plan: notes/plan_eca_forbidden_zone.md, PART IV, Stage 1A (v2 contract, section 1A.0-1A.6).
READ-ONLY: touches no production code and no config. Consumes only existing live-run artifacts.

BACKGROUND
----------
A decoy sits at k*f_r inside the cardiac band [0.8, 2.0] Hz. AHET looks for its "2nd harmonic"
at 2*(k*f_r) = (2k)*f_r -- the EVIDENCE frequency. Stage 1A asks: is that evidence line even a
coherent, cancellable single-sinusoid, or is it noise/motion with no stable phase?

TARGET ORDERS -- computed PER HOP from that hop's f_r (never a fixed range; a fixed "k=8..15"
range was tried in round 1 and was wrong twice: it included irrelevant odd orders and missed
orders that appear at low breathing rates).

METRICS (all specified in the plan; M0 runs first as a control)
  M0  Validate M1-M3 on a SYNTHETIC STATIONARY COMB before trusting them on real data. A metric
      that cannot detect coherence where coherence exists by construction is broken.
  M1  Presence: is there a detectable peak within +/-1 bin of the evidence frequency at all?
  M2  Held-out cancellation: fit a frequency on sub-window A (first half of the hop), project
      THAT frequency (with a fresh least-squares fit) out of held-out sub-window B, measure the
      dB change in B's mainlobe energy. Compared against TWO nulls (off-harmonic frequency;
      phase-randomised surrogate) so a positive result cannot be an artefact of spending 2
      degrees of freedom on 300 samples of noise.
  M3  Coherence via phase tracking: narrowband-demodulate at the evidence frequency, remove the
      BEST LINEAR SLOPE (a constant frequency offset is an f_r error, NOT incoherence, and must
      not be scored as one), reject amplitude-gated-out sub-windows, report residual phase std.
      Validated on 3 synthetic controls: stationary, frequency-offset (must be ABSORBED, i.e.
      read as coherent), and random-walk-drifting (must NOT be absorbed, i.e. read as
      incoherent) -- if M3 cannot tell these apart, it is not trustworthy on real data.

PASS/FAIL (plan section 1A.5, exact)
  PASS: held-out C_dB <= -15 dB, beating the null by >= 10 dB, on >= 70% of eligible hops,
        at the per-hop evidence orders, in BOTH runs.
  FAIL: anything less.

CLAIM DISCIPLINE (plan section 1A.6 -- narrowed after round-1 overclaiming)
  A FAIL licenses only: "on these sessions, a STATIONARY SINGLE-SINUSOID model of the high-order
  respiratory harmonics is unsupported, so THIS cancellation mechanism cannot work." It does NOT
  license "dead permanently, on physics" -- time-varying / broadened / harmonic-subspace bases
  remain open, different hypotheses, out of scope here.

Usage:
    python -X utf8 scripts/stage1a_harmonic_coherence.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import butter, filtfilt

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FS = 20.0
WINDOW_S = 30.0
BAND = (0.8, 2.0)                # cardiac band, Hz
BIN_HZ = 1.0 / WINDOW_S           # full-window FFT bin = 0.0333 Hz = 2 bpm
SEARCH_HALF_BINS = 1.5
MAINLOBE_HALF_BINS = 2
PASS_CDB = -15.0
PASS_NULL_MARGIN_DB = 10.0
PASS_FRACTION = 0.70

RUNS = {
    "natural": REPO_ROOT / "results/live_demo/20260713_172042_live_demo_massimo1",
    "paced16": REPO_ROOT / "results/live_demo/20260713_182002_live_demo_massimo2",
}


# ── Per-hop target orders (1A.0) ─────────────────────────────────────────────────────────────
def evidence_orders(f_r_hz: float) -> list[tuple[int, float]]:
    """(k, evidence_freq_hz=2k*f_r) for every k whose DECOY k*f_r lands inside the cardiac band."""
    if not np.isfinite(f_r_hz) or f_r_hz <= 0:
        return []
    k_lo = int(np.ceil(BAND[0] / f_r_hz - 1e-9))
    k_hi = int(np.floor(BAND[1] / f_r_hz + 1e-9))
    return [(k, 2 * k * f_r_hz) for k in range(max(k_lo, 1), max(k_hi, 0) + 1)]


# ── Core spectral / projection primitives ────────────────────────────────────────────────────
def spec(x: np.ndarray, fs: float = FS):
    n = len(x)
    return np.abs(np.fft.rfft(x * np.hanning(n))), np.fft.rfftfreq(n, 1 / fs)


def mainlobe_energy(x: np.ndarray, f0: float, half_bins: float = MAINLOBE_HALF_BINS) -> float:
    s, f = spec(x)
    m = (f >= f0 - half_bins * BIN_HZ) & (f <= f0 + half_bins * BIN_HZ)
    return float(np.sum(s[m] ** 2)) if m.any() else 0.0


def project_out_freq(x: np.ndarray, f: float, fs: float = FS) -> np.ndarray:
    """Least-squares project [sin(2*pi*f*t), cos(2*pi*f*t)] out of x (local fit on x's own samples)."""
    n = len(x)
    t = np.arange(n) / fs
    basis = np.stack([np.sin(2 * np.pi * f * t), np.cos(2 * np.pi * f * t)], axis=1)
    coef, *_ = np.linalg.lstsq(basis, x, rcond=None)
    return x - basis @ coef


def held_out_cancellation(x: np.ndarray, f0: float) -> tuple[float, float]:
    """M2 core. Fit best frequency on sub-window A; project it (freshly fit) out of held-out B.

    Returns (C_dB, chosen_frequency). C_dB = 10*log10(E_after/E_before) in B's mainlobe.
    """
    n = len(x)
    half = n // 2
    a, b = x[:half], x[half:]
    grid = np.linspace(f0 - SEARCH_HALF_BINS * BIN_HZ, f0 + SEARCH_HALF_BINS * BIN_HZ, 31)
    e0_a = mainlobe_energy(a, f0)
    best_f, best_reduction = f0, -np.inf
    for f in grid:
        resid = project_out_freq(a, f)
        reduction = e0_a - mainlobe_energy(resid, f0)
        if reduction > best_reduction:
            best_reduction, best_f = reduction, f
    e0_b = mainlobe_energy(b, f0)
    e1_b = mainlobe_energy(project_out_freq(b, best_f), f0)
    c_db = 10 * np.log10(max(e1_b, 1e-15) / max(e0_b, 1e-15))
    return c_db, best_f


def null_off_harmonic(x: np.ndarray, f0: float, f_r_hz: float) -> float:
    """Null 1: same procedure at a frequency maximally OFF the harmonic comb grid."""
    return held_out_cancellation(x, f0 + 0.5 * f_r_hz)[0]


def null_phase_randomized(x: np.ndarray, f0: float, rng: np.random.Generator) -> float:
    """Null 2: destroy deterministic phase structure (keep the magnitude spectrum), retest."""
    n = len(x)
    xr_spec = np.fft.rfft(x)
    mag = np.abs(xr_spec)
    ph = rng.uniform(0, 2 * np.pi, size=mag.shape)
    ph[0] = 0.0
    xr = np.fft.irfft(mag * np.exp(1j * ph), n=n)
    return held_out_cancellation(xr, f0)[0]


def presence(x: np.ndarray, f0: float, prom_factor: float = 2.0) -> bool:
    """M1: a local max within +/-1 bin of f0, above prom_factor times the surrounding floor."""
    s, f = spec(x)
    near = (f >= f0 - BIN_HZ) & (f <= f0 + BIN_HZ)
    band = (f >= f0 - 6 * BIN_HZ) & (f <= f0 + 6 * BIN_HZ)
    mainlobe = (f >= f0 - MAINLOBE_HALF_BINS * BIN_HZ) & (f <= f0 + MAINLOBE_HALF_BINS * BIN_HZ)
    outside = band & ~mainlobe
    if not near.any():
        return False
    floor = float(np.median(s[outside])) if outside.any() else float(np.median(s))
    return bool(s[near].max() > prom_factor * max(floor, 1e-12))


def coherence_residuals(x: np.ndarray, f0: float, bandwidth_hz: float = 0.1,
                        n_subwindows: int = 3, amp_floor_frac: float = 0.2,
                        fs: float = FS) -> list[float]:
    """M3: narrowband-demodulate at f0, remove the best LINEAR slope per sub-window, report
    residual phase std for amplitude-admissible sub-windows. Empty list = fully amplitude-gated.

    Demodulation is DOWN-MIX + zero-phase LOW-PASS (scipy filtfilt), not an FFT-domain brick-wall
    mask. A brick-wall mask at a fixed absolute frequency is well-behaved only when f0 sits exactly
    on an FFT bin; for any other frequency (the general case — real f_r rarely lands on a bin) it
    introduces spectral-leakage phase distortion that masquerades as incoherence. Down-mixing f0 to
    DC first and then low-passing avoids this: a genuine small frequency OFFSET becomes a slow,
    smooth phasor rotation near DC, which a linear fit still absorbs cleanly.
    """
    n = len(x)
    t = np.arange(n) / fs
    y = x * np.exp(-1j * 2 * np.pi * f0 * t)      # down-mix f0 -> DC
    wn = min(bandwidth_hz / (fs / 2), 0.99)
    b, a_coef = butter(4, wn, btype="low")
    narrow = filtfilt(b, a_coef, y.real) + 1j * filtfilt(b, a_coef, y.imag)
    amp = np.abs(narrow)
    phase = np.unwrap(np.angle(narrow))
    idx_groups = np.array_split(np.arange(n), n_subwindows)
    amp_floor = amp_floor_frac * (np.median(amp) if np.median(amp) > 0 else 1.0)
    out = []
    for ii in idx_groups:
        if len(ii) < 3 or amp[ii].mean() < amp_floor:
            continue
        t = ii / fs
        b, a_ = np.polyfit(t, phase[ii], 1)      # best LINEAR slope
        resid = phase[ii] - (a_ + b * t)
        out.append(float(np.std(resid)))
    return out


# ── Synthetic signals — for M0 control and M3 validation ────────────────────────────────────
def synth(kind: str, f0: float, fs: float = FS, dur: float = WINDOW_S, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(fs * dur)
    t = np.arange(n) / fs
    if kind == "stationary":
        ph, amp = 2 * np.pi * f0 * t, np.ones(n)
    elif kind == "freq_offset":       # constant freq error -> a pure LINEAR phase ramp
        ph, amp = 2 * np.pi * (f0 * 1.01) * t, np.ones(n)
    elif kind == "drifting":
        # A frequency STEP mid-window (f0 -> f0*1.03 at t=dur/2): genuinely NONLINEAR phase,
        # unlike freq_offset's constant slope. Deterministic and easy to verify (unlike a random
        # walk, which a bug can silently damp to near-nothing — as round 1 of this script did).
        half = n // 2
        ph = np.empty(n)
        ph[:half] = 2 * np.pi * f0 * t[:half]
        ph[half:] = ph[half - 1] + 2 * np.pi * (f0 * 1.03) * (t[half:] - t[half - 1])
        amp = np.ones(n)
    elif kind == "amplitude_modulated":
        ph = 2 * np.pi * f0 * t
        amp = 0.5 + 0.5 * np.abs(np.sin(2 * np.pi * (1 / 12.0) * t))
    else:
        raise ValueError(kind)
    return amp * np.sin(ph) + 0.01 * rng.standard_normal(n)


# ── Loading real runs ─────────────────────────────────────────────────────────────────────────
def load_run(run_dir: Path) -> pd.DataFrame:
    z = np.load(run_dir / "live_intermediates.npz", allow_pickle=True)
    d = pd.DataFrame({
        "elapsed_s": z["elapsed_s"],
        "f_r_hz": z["f_r_hz"],
    })
    d["phase"] = list(z["phase_clean"])       # (n_hops, 600) — the signal ECA operates on
    return d


# ── M0 — validate the metrics on a control BEFORE trusting them on real data ────────────────
def run_m0() -> bool:
    print("=" * 96)
    print("M0 — CONTROL: validate M1/M2/M3 on a SYNTHETIC STATIONARY COMB (coherent by construction)")
    print("=" * 96)
    f_r = 0.30
    # BUG FIXED (2026-07-14): must cover every evidence order actually tested below (k=3..6 ->
    # evidence freq 2k = 6,8,10,12 harmonics of f_r). A comb over k=1..4 only reaches 1.2 Hz and
    # tests 1.8-3.6 Hz against pure noise, producing a false "control fails" verdict — that WAS
    # the round-1 bug, caught before being reported.
    x = sum(synth("stationary", k * f_r, seed=k) for k in range(1, 15))
    ok = True
    for k in range(3, 7):
        f0 = 2 * k * f_r
        pres = presence(x, f0)
        cdb, _ = held_out_cancellation(x, f0)
        res = coherence_residuals(x, f0)
        res_ok = bool(res) and np.mean(res) < 0.3
        print(f"  k={k}  f0={f0:.2f} Hz  M1(presence)={pres}  M2(C_dB)={cdb:+6.1f}  "
              f"M3(mean resid rad)={np.mean(res) if res else float('nan'):.3f}  "
              f"{'OK' if (pres and cdb <= PASS_CDB and res_ok) else 'WEAK'}")
        ok &= pres and (cdb <= PASS_CDB) and res_ok
    print(f"\n  M0 CONTROL: {'PASS — metrics detect coherence where it exists' if ok else 'FAIL — DO NOT TRUST M1-M3 ON REAL DATA'}")
    return ok


def run_m3_validation() -> None:
    print("\n" + "=" * 96)
    print("M3 VALIDATION — must tell freq-offset (coherent) apart from drifting (incoherent)")
    print("=" * 96)
    f0 = 1.2
    for kind in ("stationary", "freq_offset", "drifting", "amplitude_modulated"):
        x = synth(kind, f0, seed=1)
        res = coherence_residuals(x, f0, n_subwindows=3)
        mean_r = np.mean(res) if res else float("nan")
        n_kept = len(res)
        print(f"  {kind:<20} mean residual = {mean_r:.3f} rad   sub-windows kept = {n_kept}/3")
    print("  EXPECT: stationary & freq_offset LOW (both coherent — a freq error is absorbed by")
    print("  the linear-slope removal); drifting HIGH (genuinely nonlinear phase); AM LOW on the")
    print("  sub-windows it keeps (amplitude gating removes the null, not phase incoherence).")


# ── Main per-hop evaluation on real data ─────────────────────────────────────────────────────
def evaluate_run(name: str, run_dir: Path, rng: np.random.Generator) -> pd.DataFrame:
    d = load_run(run_dir)
    rows = []
    for _, r in d.iterrows():
        for k, f0 in evidence_orders(r.f_r_hz):
            x = np.asarray(r.phase, dtype=float)
            x = x - x.mean()
            pres = presence(x, f0)
            if not pres:
                rows.append(dict(session=name, elapsed_s=r.elapsed_s, k=k, f0=f0,
                                 eligible=False, cdb=np.nan, null_off=np.nan, null_rand=np.nan))
                continue
            cdb, _ = held_out_cancellation(x, f0)
            n_off = null_off_harmonic(x, f0, r.f_r_hz)
            n_rand = null_phase_randomized(x, f0, rng)
            rows.append(dict(session=name, elapsed_s=r.elapsed_s, k=k, f0=f0,
                             eligible=True, cdb=cdb, null_off=n_off, null_rand=n_rand))
    return pd.DataFrame(rows)


def main() -> None:
    m0_ok = run_m0()
    run_m3_validation()
    if not m0_ok:
        print("\n*** M0 control failed — stopping. Fix the metrics before running on real data. ***")
        return

    rng = np.random.default_rng(42)
    print("\n" + "=" * 96)
    print("REAL DATA — per-hop evidence-order cancellation (held-out, vs. both nulls)")
    print(f"PASS bar: C_dB <= {PASS_CDB} dB, beating the null by >= {PASS_NULL_MARGIN_DB} dB, "
          f"on >= {PASS_FRACTION:.0%} of eligible hops, in BOTH runs.")
    print("=" * 96)

    all_dfs = []
    for name, rd in RUNS.items():
        df = evaluate_run(name, rd, rng)
        all_dfs.append(df)
        elig = df[df.eligible]
        print(f"\n{name}:  target(k, evidence-order-hits) = {len(df)}   "
              f"eligible (M1 presence) = {len(elig)}/{len(df)}")
        if len(elig):
            null_level = elig[["null_off", "null_rand"]].mean(axis=1)
            beats_null = (elig.cdb <= null_level - PASS_NULL_MARGIN_DB)
            passes = (elig.cdb <= PASS_CDB) & beats_null
            print(f"   median C_dB (held-out)     = {elig.cdb.median():+.1f} dB")
            print(f"   median null (off-harmonic) = {elig.null_off.median():+.1f} dB")
            print(f"   median null (phase-random) = {elig.null_rand.median():+.1f} dB")
            print(f"   hops passing BOTH bars     = {int(passes.sum())}/{len(elig)} "
                  f"({100*passes.sum()/len(elig):.0f}%)")
            elig = elig.assign(passes=passes)
        else:
            print("   (no eligible hops — every evidence order failed the M1 presence check)")

    print("\n" + "=" * 96)
    print("VERDICT (plan section 1A.5 / 1A.6 — exact criteria)")
    print("=" * 96)
    overall_pass = True
    for name, df in zip(RUNS, all_dfs):
        elig = df[df.eligible]
        if not len(elig):
            print(f"  {name}: FAIL (no eligible hops)")
            overall_pass = False
            continue
        null_level = elig[["null_off", "null_rand"]].mean(axis=1)
        frac = float(((elig.cdb <= PASS_CDB) & (elig.cdb <= null_level - PASS_NULL_MARGIN_DB)).mean())
        p = frac >= PASS_FRACTION
        overall_pass &= p
        print(f"  {name}: {'PASS' if p else 'FAIL'}  ({100*frac:.0f}% of eligible hops met the bar, "
              f"need >= {100*PASS_FRACTION:.0f}%)")

    print()
    if overall_pass:
        print("  OVERALL: PASS. A stationary single-sinusoid model at the evidence orders is")
        print("  SUPPORTED on these two sessions. This would revive the high-k cancellation family")
        print("  (subject to Stage 2's frequency-source question) — reconsider v2.")
    else:
        print("  OVERALL: FAIL.")
        print("  LICENSED CLAIM (narrow, per plan 1A.6): on these two sessions, a STATIONARY")
        print("  SINGLE-SINUSOID model of the high-order respiratory harmonics is UNSUPPORTED, so")
        print("  THIS cancellation mechanism cannot work.")
        print("  NOT LICENSED: 'dead permanently, on physics'. Time-varying / broadened / harmonic-")
        print("  subspace bases remain OPEN, DIFFERENT hypotheses — out of scope here.")


if __name__ == "__main__":
    main()
