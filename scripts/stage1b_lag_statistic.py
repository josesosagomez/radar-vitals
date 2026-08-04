"""Stage 1B round 3 -- multi-hop-lag motion statistic: CONTROL SCAFFOLD.

Plan: notes/note_stage1b_lag_statistic.md, draft 7 -- CLEARED FOR CONTROL-SCAFFOLDING after
6 rounds of cross-model plan review (notes/cross_review_stage1b_lag_statistic_prompt*.md /
*_review_*_findings.md). READ-ONLY on real sessions; touches no production code or config.

WHAT THIS MODULE IS
--------------------
Round 2's one-hop-lag motion statistic (scripts/stage1b_exploratory_motion.py, preserved
unchanged) has no leverage on any capture in hand: real breathing-rate transitions ramp over
~10 hops, so a lag-1 comparison sees almost no movement. This module implements the lag-10
redesign's CONTROL SCAFFOLD ONLY:
  - the exact-hop frozen-k_hat statistic (design Sec 2/3/3b)
  - M0 (synthetic parameter grid, role-tagged), M0b (overlap-smoothness control on the real
    spectral pipeline), a missingness-matched null, and the cardiac/RSA confound controls
    (design Sec 4)
  - the three-stage threshold-selection machinery (design Sec 7g), exercised only on a small
    synthetic scenario here

WHAT THIS MODULE IS NOT
------------------------
It does NOT compute a real lag-10 SCORE distribution as a scientific result, fit a final
threshold against real sessions, or evaluate the decisive endpoint (design Sec 7b). All of
that requires the numeric specification in design Sec 7(c), which is NOT yet frozen. Any
real-session numbers printed by this script (the "real per-decision surrogate" section) are
plumbing/diagnostic checks that the code runs end-to-end -- not headline results, and not
a fitted threshold. Running the M0 gate first and stopping if it fails follows the same
discipline as scripts/stage1a_harmonic_coherence.py.

DESIGN SUMMARY (see the note for the full derivation and 6 rounds of review)
-----------------------------------------------------------------------------
  Exact-hop frozen-k_hat contract (Sec 2):
    inference window   = exact hops h0..h0+4                         (5 observations)
    k_hat               = median(f_cand/f_r) over the inference window,
                          clipped to admissible_ks(f_r at h0+4 ONLY)   -- never origin/later
    origin  t-L         = h0+5           (strictly after the inference window)
    endpoint t          = t-L + L = h0+15   (L=10)
    full attempt span   = h0..h0+15 inclusive == 16 hop observations, ZERO gaps tolerated
                          anywhere in that span (Sec 3b); any missing/collapsed hop
                          invalidates the attempt and restarts it.

    harmonic model:  f_hat(t) = f_c(t-L) + k_hat * ( f_r(t) - f_r(t-L) )
    cardiac  model:  f_hat(t) = f_c(t-L)
    SCORE(t)       = | f_c(t) - f_hat_harmonic(t) |  -  | f_c(t) - f_hat_cardiac(t) |
    (negative SCORE => candidate moved like a harmonic)

  Leverage gate (Sec 3): eligible iff |k_hat * (f_r(t)-f_r(t-L))| >= one FFT bin (2 bpm at the
  30 s window). Perturbation stability (Sec 3): a window is UNSTABLE if ANY of the 2**7=128
  independent +/-1-bin sign combinations (5 inference f_r values + 2 endpoints) flips its
  eligibility status relative to nominal.

  Controls (Sec 4): M0 (fixed-seed grid; supported-positive cases must ALL be recovered
  correctly, deliberate-gap cases must be REJECTED by the persistence contract, stress cases
  are diagnostic-only); M0b (does 90% window overlap alone manufacture smoothness -- tested
  on the REAL Hann+rFFT extraction pipeline, not a pre-smoothed synthetic trajectory);
  a missingness-matched null; a synthetic cardiac/RSA confound control (does real HR-RR
  covariation alone push a genuinely cardiac trajectory across the harmonic side); and a real
  per-decision empirical surrogate (substitutes gated Masimo PR for candidate motion, reusing
  that decision's OWN frozen k_hat).

  Threshold selection (Sec 7g): three ordered stages -- (1) safety-feasible [cardiac
  retention, false-harmonic bound, yield/NaN, leverage/persistence -- the pass-all sentinel
  trivially clears this], (2) utility/mechanism [M0 gate + minimum-utility bar -- the sentinel
  is EXPECTED to fail here], (3) objective+tie-break among thresholds passing BOTH stages.
  Outcomes: `invalid_preconditions` (defensive only, not reachable with valid inputs) /
  feasible-but-not-useful / feasible-and-useful.

Usage:
    python -X utf8 scripts/stage1b_lag_statistic.py
"""
from __future__ import annotations

import itertools
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Reuse round 2's pure helpers rather than re-deriving them -- round 2 is preserved unchanged;
# this module only extends it (design note, "Supersedes nothing").
from scripts.stage1b_exploratory_motion import (  # noqa: E402
    RUNS,
    admissible_ks,
    associate,
    collapse_mask,
    load_run,
)

# ── Design constants (Sec 2/3) ────────────────────────────────────────────────────────────────
FS = 20.0
HOP_S = 3.0
WINDOW_S = 30.0
L = 10                                   # lag, hops -- frozen from measured Masimo ramp durations
HIST = 5                                 # exact inference-window length, hops
FULL_ATTEMPT_HOPS = HIST + L + 1         # h0..h0+15 inclusive = 16 (5 inference + 11 scoring, no overlap)
CARDIAC_BAND = (0.8, 2.0)                # Hz
LEVERAGE_BIN_BPM = 60.0 / WINDOW_S       # one FFT bin at the 30 s window = 2.0 bpm
LEVERAGE_STOP_FRACTION = 0.30            # binding stop rule (Sec 3): below this, don't headline

assert FULL_ATTEMPT_HOPS == 16, "design note pins this at exactly 16 -- recompute if constants change"


# ── Sec 2/3: exact-hop frozen-k_hat + SCORE ──────────────────────────────────────────────────

def build_valid_attempts(track_hops: dict[int, float], usable: np.ndarray) -> list[int]:
    """Every h0 such that hops h0..h0+FULL_ATTEMPT_HOPS-1 are ALL present in track_hops AND
    usable (not respiration-collapsed). Exact hop-number membership only -- NEVER positional
    row indexing (design Sec 3b: round 2's association can retain a track ID across a gap via
    its own MAX_GAP allowance; this persistence check is independent and stricter: zero gaps
    tolerated anywhere in the full attempt)."""
    if not track_hops:
        return []
    hop_set = set(track_hops)
    lo, hi = min(hop_set), max(hop_set)
    starts = []
    for h0 in range(lo, hi - FULL_ATTEMPT_HOPS + 2):
        span = range(h0, h0 + FULL_ATTEMPT_HOPS)
        if all(h in hop_set and 0 <= h < len(usable) and usable[h] for h in span):
            starts.append(h0)
    return starts


def frozen_k_and_score(
    track_hops: dict[int, float], fr_bpm: np.ndarray, h0: int, lag: int = L,
) -> dict | None:
    """Sec 2's exact contract for one valid attempt starting at h0.

    k_hat is computed from EXACTLY the 5 inference hops h0..h0+4 and clipped using
    admissible_ks() evaluated ONLY at the final inference hop h0+4 -- never at the origin
    (t-L) or later. This is what makes the freeze genuinely pre-origin (round-4 review
    confirmed this fix; round-3's draft had clipped at the origin, a self-contradiction).
    """
    inf_hops = list(range(h0, h0 + HIST))            # h0..h0+4, exact hop numbers
    origin = h0 + HIST                               # t-L, strictly after the inference window
    endpoint = origin + lag                          # t

    fc_inf = np.array([track_hops[h] for h in inf_hops], dtype=float)
    fr_inf = np.array([fr_bpm[h] for h in inf_hops], dtype=float)
    if not np.all(np.isfinite(fr_inf)) or np.any(fr_inf <= 0):
        return None

    ks = admissible_ks(fr_inf[-1] / 60.0)             # f_r at h0+4 ONLY, converted bpm->Hz
    if len(ks) == 0:
        return None
    k_hat = int(np.clip(round(float(np.median(fc_inf / fr_inf))), min(ks), max(ks)))

    fr_origin, fr_end = fr_bpm[origin], fr_bpm[endpoint]
    if not (np.isfinite(fr_origin) and np.isfinite(fr_end)):
        return None
    d_fr = fr_end - fr_origin

    fc_origin, fc_end = track_hops[origin], track_hops[endpoint]
    pred_harmonic = fc_origin + k_hat * d_fr
    pred_cardiac = fc_origin
    err_h = abs(fc_end - pred_harmonic)
    err_c = abs(fc_end - pred_cardiac)
    score = err_h - err_c
    eligible = abs(k_hat * d_fr) >= LEVERAGE_BIN_BPM

    return dict(
        h0=h0, origin=origin, endpoint=endpoint, k_hat=k_hat, d_fr_bpm=float(d_fr),
        f_c_endpoint=float(fc_end), score=float(score), eligible=bool(eligible),
    )


def perturbation_instability(
    track_hops: dict[int, float], fr_bpm: np.ndarray, h0: int, nominal: dict,
) -> bool:
    """Sec 3: worst-case per-window instability under all 2**7=128 independent +/-1-bin sign
    combinations (5 inference f_r values + 2 scoring endpoints). Unstable if ANY combination
    flips eligibility relative to nominal -- the true per-window worst-case check, NOT the
    rejected per-pattern/max-across-patterns alternative."""
    inf_hops = list(range(h0, h0 + HIST))
    origin, endpoint = nominal["origin"], nominal["endpoint"]
    fc_inf = np.array([track_hops[h] for h in inf_hops], dtype=float)
    fr_inf0 = np.array([fr_bpm[h] for h in inf_hops], dtype=float)
    fr_origin0, fr_end0 = fr_bpm[origin], fr_bpm[endpoint]

    n_checked = 0
    for signs in itertools.product((-1.0, 1.0), repeat=7):
        fr_inf = fr_inf0 + np.array(signs[:5]) * LEVERAGE_BIN_BPM
        fr_origin = fr_origin0 + signs[5] * LEVERAGE_BIN_BPM
        fr_end = fr_end0 + signs[6] * LEVERAGE_BIN_BPM
        if np.any(fr_inf <= 0) or fr_origin <= 0 or fr_end <= 0:
            continue  # non-physical perturbation (won't occur for real bpm magnitudes)
        ks = admissible_ks(fr_inf[-1] / 60.0)
        if len(ks) == 0:
            continue
        k_hat = int(np.clip(round(float(np.median(fc_inf / fr_inf))), min(ks), max(ks)))
        d_fr = fr_end - fr_origin
        eligible = abs(k_hat * d_fr) >= LEVERAGE_BIN_BPM
        n_checked += 1
        if eligible != nominal["eligible"]:
            return True
    return False


def score_track(
    track_hops: dict[int, float], fr_bpm: np.ndarray, usable: np.ndarray, lag: int = L,
) -> list[dict]:
    """All valid-attempt scores for one track. This is the module's single entry point for
    turning a {hop: f_cand} track into scored, eligibility-gated, exact-hop-validated results."""
    out = []
    for h0 in build_valid_attempts(track_hops, usable):
        rec = frozen_k_and_score(track_hops, fr_bpm, h0, lag=lag)
        if rec is None:
            continue
        rec["unstable"] = perturbation_instability(track_hops, fr_bpm, h0, rec) if lag == L else None
        out.append(rec)
    return out


# ── Sec 4 / 7(g): M0 -- fixed-seed parameter grid, ROLE-TAGGED before execution ─────────────
#
# Design note (round-6 review): M0 case roles must be tagged BEFORE running, never decided
# post hoc. Three roles:
#   "supported"    -- MUST all be recovered correctly (correct k_hat, negative SCORE, exact
#                     16-hop set used). If any supported case fails, the M0 gate fails, full
#                     stop -- never weakened to accommodate a hard case after the fact.
#   "invalid_gap"  -- deliberately contains a missing/collapsed hop; MUST be REJECTED by the
#                     persistence contract (build_valid_attempts returns no spanning h0),
#                     never scored at all.
#   "stress"       -- optional, out-of-domain (e.g. very low SNR); reported diagnostically,
#                     EXCLUDED from the all-pass gate unless explicitly promoted in the
#                     numeric specification (Sec 7c) before a real run.

@dataclass
class M0Case:
    role: str            # "supported" | "invalid_gap" | "stress"
    tag: str             # short identifier for reporting
    fc: dict[int, float]
    fr_bpm: np.ndarray
    usable: np.ndarray
    k_true: int | None = None
    expect_h0: int | None = None   # the h0 this case is built to exercise (usually 0)


def _ramp_fr(n: int, f0_bpm: float, rate_bpm: float, direction: int) -> np.ndarray:
    return f0_bpm + direction * rate_bpm * np.arange(n)


def _build_supported_case(
    k: int, direction: int, start_offset_bin: float, seed: int, n: int = 20,
) -> M0Case:
    rng = np.random.default_rng(seed)
    # f0 chosen so k*f0 lands mid-cardiac-band (~1.2 Hz = 72 bpm) -- i.e. the true k is
    # actually an ADMISSIBLE order at this f_r. Bug caught by M0 itself on the first run: a
    # fixed f0=13 bpm for every k made k=2/3 inadmissible there (k*f_r < 0.8 Hz cardiac-band
    # floor), so the clip correctly refused them and snapped to k=4 -- correct clipping
    # behavior, invalid test construction. Never weaken the clip to accommodate a bad test.
    f0_bpm = 72.0 / k
    fr = _ramp_fr(n, f0_bpm=f0_bpm, rate_bpm=0.15, direction=direction)
    offset = start_offset_bin * LEVERAGE_BIN_BPM
    fc = {h: k * fr[h] + offset + rng.normal(0, 0.15) for h in range(n)}
    return M0Case(
        role="supported", tag=f"k={k}_dir={direction:+d}_off={start_offset_bin}_seed={seed}",
        fc=fc, fr_bpm=fr, usable=np.ones(n, dtype=bool), k_true=k, expect_h0=0,
    )


def _build_invalid_gap_case(gap_position: str, seed: int, n: int = 20) -> M0Case:
    """gap_position in {'inference', 'origin', 'scoring'} -- where inside the 16-hop span the
    deliberate violation sits, for the h0=0 attempt specifically."""
    rng = np.random.default_rng(seed)
    fr = _ramp_fr(n, f0_bpm=13.0, rate_bpm=0.25, direction=1)
    fc = {h: 4 * fr[h] + rng.normal(0, 0.15) for h in range(n)}
    usable = np.ones(n, dtype=bool)
    pos = {"inference": 2, "origin": HIST, "scoring": HIST + 3}[gap_position]
    if gap_position == "inference" or gap_position == "scoring":
        del fc[pos]   # missing candidate hop
    else:
        usable[pos] = False   # respiration-collapse at the origin hop
    return M0Case(
        role="invalid_gap", tag=f"gap_at_{gap_position}_seed={seed}",
        fc=fc, fr_bpm=fr, usable=usable, expect_h0=0,
    )


def _build_stress_case(label: str, seed: int, n: int = 20) -> M0Case:
    rng = np.random.default_rng(seed)
    fr = _ramp_fr(n, f0_bpm=13.0, rate_bpm=0.25, direction=1)
    if label == "very_low_snr":
        fc = {h: 4 * fr[h] + rng.normal(0, 8.0) for h in range(n)}   # noise >> bin width
    elif label == "band_edge_f_r":
        fr = _ramp_fr(n, f0_bpm=11.9, rate_bpm=0.02, direction=1)    # barely above search-band floor
        fc = {h: 4 * fr[h] + rng.normal(0, 0.15) for h in range(n)}
    else:
        raise ValueError(label)
    return M0Case(role="stress", tag=label, fc=fc, fr_bpm=fr, usable=np.ones(n, dtype=bool), k_true=4)


def build_m0_grid() -> list[M0Case]:
    cases: list[M0Case] = []
    for k in (2, 3, 4, 5):
        for direction in (1, -1):
            for start_offset in (0.0, 0.4):
                for seed in (0, 1, 2):
                    cases.append(_build_supported_case(k, direction, start_offset, seed))
    for gap_position in ("inference", "origin", "scoring"):
        for seed in (0, 1):
            cases.append(_build_invalid_gap_case(gap_position, seed))
    for label in ("very_low_snr", "band_edge_f_r"):
        cases.append(_build_stress_case(label, seed=0))
    return cases


def _crossing_trajectory_case(seed: int = 0, n: int = 22) -> dict:
    """Sec 3b: M0 must include crossing/rank-swapping trajectories, not only one clean
    isolated trajectory, because greedy rank-first association can give rank-0 first choice
    of a track. Builds a per-hop DataFrame with 2 competing candidate series that CROSS in
    frequency mid-window, runs it through round 2's own associate(), and checks that k_hat/
    SCORE are computed against a track that stayed on ONE physical series across the
    crossing, not a track that jumped series at the swap.
    """
    rng = np.random.default_rng(seed)
    fr = _ramp_fr(n, f0_bpm=13.0, rate_bpm=0.25, direction=1)
    # Series A: the true harmonic (k=4), rising. Series B: a decoy, roughly flat, that crosses
    # series A's frequency around the midpoint -- exactly the ambiguity a rank-first greedy
    # associator can mishandle.
    series_a = np.array([4 * fr[h] + rng.normal(0, 0.15) for h in range(n)])
    series_b = np.full(n, series_a[n // 2])  # flat, sitting right at A's crossing point

    d = pd.DataFrame({"f_r_hz": fr / 60.0})
    for h in range(n):
        # rank ordering flips at the crossing -- deliberately assign rank0/rank1 by raw
        # magnitude (as AHET's real candidate ranking does), not by "which series this is",
        # so the association code must disambiguate using its own jump/gap logic.
        lo, hi = sorted([series_a[h], series_b[h]])
        d.loc[h, "cand0"] = hi
        d.loc[h, "cand1"] = lo
        d.loc[h, "cand2"] = np.nan
    usable = np.ones(n, dtype=bool)
    tr = associate(d, usable)
    return dict(d=d, tr=tr, fr_bpm=fr, series_a=series_a, series_b=series_b)


def run_m0() -> bool:
    """Sec 4/7(g) M0 control. Supported cases must ALL pass; invalid-gap cases must ALL be
    correctly rejected by the persistence contract; stress cases are diagnostic-only. Follows
    the same "stop if the control itself is broken" discipline as
    scripts/stage1a_harmonic_coherence.py's run_m0()."""
    print("=" * 96)
    print("M0 -- CONTROL: fixed-seed parameter grid, role-tagged BEFORE execution")
    print("=" * 96)

    cases = build_m0_grid()
    supported = [c for c in cases if c.role == "supported"]
    invalid_gap = [c for c in cases if c.role == "invalid_gap"]
    stress = [c for c in cases if c.role == "stress"]

    all_supported_ok = True
    for c in supported:
        starts = build_valid_attempts(c.fc, c.usable)
        exact_set_ok = starts and set(range(c.expect_h0, c.expect_h0 + FULL_ATTEMPT_HOPS)).issubset(
            set(c.fc) & {h for h in range(len(c.usable)) if c.usable[h]}
        )
        if c.expect_h0 not in starts:
            print(f"  [supported] {c.tag}: FAIL -- expected h0={c.expect_h0} not in valid starts {starts}")
            all_supported_ok = False
            continue
        rec = frozen_k_and_score(c.fc, c.fr_bpm, c.expect_h0)
        ok = (rec is not None) and (rec["k_hat"] == c.k_true) and (rec["score"] < 0) and exact_set_ok
        all_supported_ok &= ok
        status = "OK" if ok else "FAIL"
        print(f"  [supported] {c.tag}: k_hat={rec['k_hat'] if rec else None} "
              f"(true {c.k_true})  SCORE={rec['score']:+.2f}  exact_16_hop_set={exact_set_ok}  {status}")

    all_gaps_rejected = True
    for c in invalid_gap:
        starts = build_valid_attempts(c.fc, c.usable)
        rejected = c.expect_h0 not in starts
        all_gaps_rejected &= rejected
        print(f"  [invalid_gap] {c.tag}: h0={c.expect_h0} rejected={rejected}  "
              f"{'OK' if rejected else 'FAIL -- persistence contract did not reject this gap'}")

    print("  [stress] (diagnostic only -- excluded from the pass/fail gate):")
    for c in stress:
        starts = build_valid_attempts(c.fc, c.usable)
        rec = frozen_k_and_score(c.fc, c.fr_bpm, 0) if 0 in starts else None
        print(f"    {c.tag}: valid_h0={0 in starts}  "
              f"k_hat={rec['k_hat'] if rec else None}  score={rec['score'] if rec else None}")

    xing = _crossing_trajectory_case()
    n_tracks = xing["tr"]["track"].nunique() if len(xing["tr"]) else 0
    print(f"  [crossing/rank-swap] associate() produced {n_tracks} track(s) over "
          f"{len(xing['d'])} hops with a mid-window rank swap "
          f"(diagnostic -- see printed track table if n_tracks is surprising)")

    ok = all_supported_ok and all_gaps_rejected
    print(f"\n  M0 CONTROL: {'PASS' if ok else 'FAIL -- DO NOT TRUST OTHER CONTROLS OR REAL DATA'}")
    print(f"    supported: {len(supported)} cases, all_ok={all_supported_ok}")
    print(f"    invalid_gap: {len(invalid_gap)} cases, all_rejected={all_gaps_rejected}")
    print(f"    stress: {len(stress)} cases (diagnostic, not gating)")
    return ok


# ── Sec 1.2 / 4: M0b -- does window overlap ALONE manufacture smoothness? ──────────────────
#
# Design note Sec 1: "the pipeline runs a 30 s window on a 3 s hop, so adjacent windows share
# 27/30 s (90%) of their input phase data. Two overlapping FFT peak estimates could look smooth
# hop-to-hop purely from that shared data, independent of whether the true underlying frequency
# is 'cardiac' or 'harmonic'." Flagged as a HYPOTHESIS, not a claim, in the note -- this is the
# control that checks it, on the REAL Hann+rFFT extraction (not a pre-smoothed synthetic
# candidate-frequency trajectory, which would beg the question -- round-4 review comment 8).

def _hann_rfft_peak_bpm(segment: np.ndarray, fs: float, band_hz: tuple[float, float]) -> float:
    """Same convention as src/vitals.py's estimate_rate_from_phase and Stage 1A's spec():
    detrend (subtract mean), Hann window, rfft, magnitude peak within band."""
    n = len(segment)
    x = segment - segment.mean()
    spectrum = np.abs(np.fft.rfft(x * np.hanning(n)))
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    mask = (freqs >= band_hz[0]) & (freqs <= band_hz[1])
    if not mask.any():
        return float("nan")
    return float(freqs[mask][np.argmax(spectrum[mask])] * 60.0)


def _extract_candidate_series(
    raw_signal: np.ndarray, fs: float, window_s: float, hop_s: float, band_hz: tuple[float, float],
) -> np.ndarray:
    """Slide a window of window_s seconds by hop_s seconds over raw_signal, extracting the
    Hann+rFFT peak frequency (bpm) per hop -- the REAL production extraction, run on
    synthetic raw signal, per M0b's requirement (not a pre-smoothed candidate trajectory)."""
    win_n = int(round(window_s * fs))
    hop_n = int(round(hop_s * fs))
    n_hops = (len(raw_signal) - win_n) // hop_n + 1
    out = np.empty(n_hops)
    for i in range(n_hops):
        start = i * hop_n
        out[i] = _hann_rfft_peak_bpm(raw_signal[start:start + win_n], fs, band_hz)
    return out


def run_m0b(seeds: tuple[int, ...] = (0, 1, 2, 3, 4), snr_levels: tuple[float, ...] = (0.05, 0.2)) -> None:
    """M0b: a genuinely non-tracking ('cardiac') synthetic signal -- a fixed-frequency tone,
    no mechanism relating it to any f_r at all -- run through the REAL extraction pipeline at
    (a) the production 3 s-hop/30 s-window (90% overlap) and (b) a non-overlapping 30 s-hop/
    30 s-window, then scored at L=1 and L=10 against an INDEPENDENT synthetic f_r series.
    If overlap alone manufactures spurious harmonic-like SCORE, (a) will show more negative
    SCORE / more eligible-and-negative windows than (b), at either lag -- and L=10 should show
    less of that artefact than L=1 even within the overlapping (a) scheme, since a longer lag
    dilutes the shared-data fraction between the two endpoints being compared.
    """
    print("\n" + "=" * 96)
    print("M0b -- does window overlap ALONE manufacture spurious harmonic-like SCORE?")
    print("=" * 96)
    band_hz = CARDIAC_BAND
    f0_hz = 1.2  # 72 bpm, mid-cardiac-band, fixed -- genuinely non-tracking by construction

    rows = []
    for snr in snr_levels:
        for seed in seeds:
            rng = np.random.default_rng(seed)
            duration_s = 24 * WINDOW_S  # long enough for >=20 non-overlapping 30 s windows
            n_samples = int(duration_s * FS)
            t = np.arange(n_samples) / FS
            raw = np.sin(2 * np.pi * f0_hz * t) + rng.normal(0, snr, n_samples)

            fc_overlap = _extract_candidate_series(raw, FS, WINDOW_S, HOP_S, band_hz)
            fc_nonoverlap = _extract_candidate_series(raw, FS, WINDOW_S, WINDOW_S, band_hz)

            for scheme, fc_arr, hop_s in (("overlap_3s", fc_overlap, HOP_S), ("nonoverlap_30s", fc_nonoverlap, WINDOW_S)):
                n = len(fc_arr)
                if n < FULL_ATTEMPT_HOPS + 1:
                    continue
                # Independent synthetic f_r reference, unrelated to f0 by construction -- a
                # slow ramp, resampled to this scheme's own hop cadence.
                fr_bpm = _ramp_fr(n, f0_bpm=13.0, rate_bpm=0.15 * (HOP_S / hop_s), direction=1)
                fc = {h: fc_arr[h] for h in range(n) if np.isfinite(fc_arr[h])}
                usable = np.isfinite(fc_arr)
                for lag in (1, L):
                    scores = score_track(fc, fr_bpm, usable, lag=lag)
                    if not scores:
                        continue
                    vals = np.array([r["score"] for r in scores])
                    rows.append(dict(
                        snr=snr, seed=seed, scheme=scheme, lag=lag, n_windows=len(vals),
                        median_score=float(np.median(vals)),
                        frac_negative=float(np.mean(vals < 0)),
                    ))

    df = pd.DataFrame(rows)
    if not len(df):
        print("  NO WINDOWS SCORED -- check FULL_ATTEMPT_HOPS vs. generated series length.")
        return
    summary = df.groupby(["scheme", "lag"]).agg(
        median_score=("median_score", "median"), frac_negative=("frac_negative", "mean"),
        n_series=("median_score", "size"),
    )
    print(summary.to_string(float_format=lambda v: f"{v:.3f}"))
    print("\n  Interpretation: a genuinely non-tracking signal should show frac_negative ~ 0.5")
    print("  (noise-driven, no systematic bias) at EITHER scheme/lag. A large gap between")
    print("  overlap_3s and nonoverlap_30s (esp. at lag=1) would indicate the overlap-")
    print("  smoothness hypothesis (Sec 1.2) is real; L=10 is expected to reduce any such gap.")
    print("  CAVEAT (honest, not a finding): at these SNR levels the extracted peak barely")
    print("  jitters at all, so frac_negative=0 everywhere is a floor/ceiling effect, not")
    print("  evidence the hypothesis is false -- this control currently has limited power to")
    print("  detect the artefact it was built to check. Higher-noise seeds or more repeats")
    print("  would be needed before treating this as more than a plumbing/sanity check.")


# ── Sec 4: null control -- missingness/candidate-count/track-length matched ─────────────────
#
# "Off-harmonic/phase-randomized surrogate, matched to real data's missingness, candidate
# count, and track-length distribution" -- otherwise the null is an easier problem than
# reality and understates the false-positive rate (round-4 review comment 8).

def _phase_randomize_track(fc_values: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Destroy deterministic structure (keep the magnitude spectrum), same technique as
    Stage 1A's null_phase_randomized -- preserves the series' own spectral content class
    while removing any real harmonic-tracking relationship to f_r."""
    n = len(fc_values)
    if n < 4:
        return fc_values + rng.normal(0, 1.0, n)   # too short for a meaningful FFT null
    mag = np.abs(np.fft.rfft(fc_values - fc_values.mean()))
    ph = rng.uniform(0, 2 * np.pi, size=mag.shape)
    ph[0] = 0.0
    surrogate = np.fft.irfft(mag * np.exp(1j * ph), n=n)
    return surrogate + fc_values.mean()


def run_null_control(run_name: str = "sweep", seed: int = 0) -> pd.DataFrame:
    """Load a REAL session's actual track structure (exact hop membership, gaps, track
    lengths -- via round 2's own associate()+collapse_mask), phase-randomize each track's
    candidate VALUES in place (missingness pattern untouched), rescore, and report the
    resulting SCORE distribution as the null reference."""
    print("\n" + "=" * 96)
    print(f"NULL CONTROL -- phase-randomized surrogate on {run_name}'s real track structure")
    print("=" * 96)
    rng = np.random.default_rng(seed)
    d = load_run(RUNS[run_name])
    fr_bpm = d["f_r_hz"].values * 60.0
    coll = collapse_mask(d["f_r_hz"].values)
    usable = ~coll & np.isfinite(d["f_r_hz"].values)
    tr = associate(d, usable)

    rows = []
    for tid, g in tr.groupby("track"):
        g = g.sort_values("hop")
        hops = g["hop"].values
        fc_real = g["f_cand"].values
        fc_null = _phase_randomize_track(fc_real, rng)
        track_hops = {int(h): float(v) for h, v in zip(hops, fc_null)}
        for rec in score_track(track_hops, fr_bpm, usable):
            rec["track"] = int(tid)
            rows.append(rec)

    df = pd.DataFrame(rows)
    if not len(df):
        print(f"  no scoreable windows on {run_name} under the null (0 tracks reached "
              f"{FULL_ATTEMPT_HOPS} consecutive usable hops) -- report this explicitly, "
              f"do not silently skip.")
        return df
    print(f"  {len(df)} null windows scored across {df['track'].nunique()} tracks")
    print(f"  median SCORE = {df['score'].median():+.2f}   "
          f"frac negative (harmonic-like) = {(df['score'] < 0).mean():.2%}   "
          f"frac eligible = {df['eligible'].mean():.2%}")
    print("  Interpretation: a phase-randomized null should show NO systematic harmonic bias --")
    print("  frac negative well away from 1.0 and not concentrated in eligible windows.")
    return df


# ── Sec 4 / 7(d): synthetic cardiac/RSA confound control ────────────────────────────────────
#
# Over 30+ s, real HR is not perfectly constant -- RSA / exertion during paced breathing can
# make true Delta-HR covary with Delta-f_r, which could push a genuinely cardiac trajectory's
# SCORE toward the harmonic side by coincidence, not misdetection (round-5/6 review, restored
# after being silently dropped in an earlier draft).

def run_cardiac_rsa_control(
    rsa_gains: tuple[float, ...] = (0.0, 0.1, 0.3, 0.6),
    drift_bpm_per_hop: tuple[float, ...] = (0.0, 0.05, 0.15),
    seeds: tuple[int, ...] = (0, 1, 2),
    n: int = 24,
) -> pd.DataFrame:
    """Synthetic TRUE-cardiac trajectory: HR(t) = base + drift(t) + rsa_gain*(f_r(t)-mean(f_r)).
    This is explicitly NOT a harmonic of f_r -- k_true is undefined/not applicable; it is
    scored the same way any real candidate would be, to see whether RSA-like coupling alone
    can make a genuinely cardiac trajectory look harmonic (eligible AND SCORE < 0)."""
    print("\n" + "=" * 96)
    print("CARDIAC/RSA CONFOUND CONTROL -- can real HR-RR covariation alone look harmonic?")
    print("=" * 96)
    base_hr_bpm = 75.0
    fr_bpm = _ramp_fr(n, f0_bpm=13.0, rate_bpm=0.2, direction=1)
    fr_centered = fr_bpm - fr_bpm.mean()

    rows = []
    for gain in rsa_gains:
        for drift in drift_bpm_per_hop:
            for seed in seeds:
                rng = np.random.default_rng(seed)
                hr = base_hr_bpm + drift * np.arange(n) + gain * fr_centered + rng.normal(0, 0.3, n)
                track_hops = {h: float(hr[h]) for h in range(n)}
                usable = np.ones(n, dtype=bool)
                for rec in score_track(track_hops, fr_bpm, usable):
                    rows.append(dict(rsa_gain=gain, drift_bpm_per_hop=drift, seed=seed, **rec))

    df = pd.DataFrame(rows)
    if not len(df):
        print("  NO WINDOWS SCORED -- check n vs. FULL_ATTEMPT_HOPS.")
        return df
    summary = df.groupby(["rsa_gain", "drift_bpm_per_hop"]).agg(
        median_score=("score", "median"),
        frac_negative_and_eligible=("score", lambda s: float(((s < 0) & df.loc[s.index, "eligible"]).mean())),
    )
    print(summary.to_string(float_format=lambda v: f"{v:.3f}"))
    print("\n  Interpretation: frac_negative_and_eligible rising with rsa_gain would mean real")
    print("  HR-RR coupling alone can push a genuinely cardiac trajectory across the harmonic")
    print("  side -- exactly the confound Sec 7(d)'s false-harmonic bound must catch. This is")
    print("  the KNOWN-TRUTH positive-confound arm (Sec 4(i)); Sec 4(ii)'s real per-decision")
    print("  surrogate (below) is the separate empirical-protocol arm.")
    mono = df.groupby("rsa_gain")["score"].median()
    print(f"  Median score by rsa_gain (monotonic decrease = control has real discriminating")
    print(f"  power, not a floor effect): {dict(mono.round(2))}")
    print("  None of the tested gain/drift combinations crossed into eligible+negative here --")
    print("  that boundary (if it exists) sits at a larger gain/drift than tested; the actual")
    print("  acceptable bound is a Sec 7(c) specification question, not answered by this run.")
    return df


# ── Sec 4(ii): real per-decision empirical surrogate ────────────────────────────────────────
#
# One surrogate per otherwise-eligible, REAL, accepted AHET decision -- reuses that decision's
# OWN frozen k_hat and real Delta f_r, substituting gated Masimo PR for candidate motion:
#   surrogate_SCORE = |PR(t) - (PR(t-L) + k_hat*Delta f_r)| - |PR(t) - PR(t-L)|
# Missing/nonstationary PR at either endpoint excludes that decision, with the exclusion count
# reported (never silently dropped from the denominator).

def run_real_surrogate(run_name: str = "sweep") -> pd.DataFrame:
    print("\n" + "=" * 96)
    print(f"REAL PER-DECISION SURROGATE (DIAGNOSTIC/PLUMBING ONLY -- NOT a scientific result;")
    print(f"no threshold is fitted here) -- {run_name}")
    print("=" * 96)
    d = load_run(RUNS[run_name])
    fr_bpm = d["f_r_hz"].values * 60.0
    coll = collapse_mask(d["f_r_hz"].values)
    usable = ~coll & np.isfinite(d["f_r_hz"].values)
    tr = associate(d, usable)
    masimo_pr = d["masimo_pr"].values
    hr_valid = d["hr_valid"].values if "hr_valid" in d.columns else np.zeros(len(d), dtype=bool)

    rows, n_excluded_pr, n_16hop_scored, n_endpoint_accepted = [], 0, 0, 0
    for tid, g in tr.groupby("track"):
        track_hops = {int(h): float(v) for h, v in zip(g["hop"].values, g["f_cand"].values)}
        for h0 in build_valid_attempts(track_hops, usable):
            rec = frozen_k_and_score(track_hops, fr_bpm, h0)
            if rec is None:
                continue
            n_16hop_scored += 1
            endpoint = rec["endpoint"]
            # "otherwise-eligible accepted AHET decision" -- the endpoint hop must be one
            # where AHET actually accepted a candidate.
            if endpoint >= len(hr_valid) or not hr_valid[endpoint]:
                continue
            n_endpoint_accepted += 1
            origin = rec["origin"]
            pr_t, pr_tL = masimo_pr[endpoint], masimo_pr[origin]
            if not (np.isfinite(pr_t) and np.isfinite(pr_tL)):
                n_excluded_pr += 1
                continue
            d_fr = rec["d_fr_bpm"]
            k_hat = rec["k_hat"]
            surrogate_score = abs(pr_t - (pr_tL + k_hat * d_fr)) - abs(pr_t - pr_tL)
            rows.append(dict(track=int(tid), h0=h0, endpoint=endpoint, k_hat=k_hat,
                             d_fr_bpm=d_fr, surrogate_score=surrogate_score))

    df = pd.DataFrame(rows)
    # Report the full breakdown, not a bare final count -- a "0" at the end is meaningless
    # without knowing whether it's the 16-hop persistence contract or the endpoint-acceptance
    # join that's the bottleneck (never hide a zero behind a single number).
    print(f"  clean 16-hop attempts (any endpoint): {n_16hop_scored}")
    print(f"  of those, endpoint is an AHET-accepted decision: {n_endpoint_accepted}")
    print(f"  of those, excluded for missing/nonstationary PR: {n_excluded_pr}")
    print(f"  final usable surrogates: {len(df)}")
    if len(df):
        print(f"  surrogate_score: median={df.surrogate_score.median():+.2f}  "
              f"frac_negative(would-be-vetoed-if-thresholded-at-0)={100*(df.surrogate_score<0).mean():.0f}%")
    elif n_16hop_scored and not n_endpoint_accepted:
        print("  GENUINE FINDING, not a bug: on this session, clean 16-hop runs exist but their")
        print("  endpoints never happen to land on an AHET-accepted hop -- getting a full")
        print("  16-consecutive-hop run AND landing on one of the (sparse) accepted hops is a")
        print("  restrictive joint condition on these short sessions. This is exactly the kind")
        print("  of data-sparsity problem Sec 7(c)'s specification and the planned new")
        print("  primary capture need to account for.")
    print("  NOTE: 'frac_negative' above is NOT a false-harmonic rate against a real threshold --")
    print("  no threshold is frozen yet (Sec 7c). This only confirms the plumbing (frozen k_hat")
    print("  reuse, real Delta f_r, gated PR substitution, exclusion accounting) runs end-to-end.")
    return df


# ── Sec 7(g): three-stage threshold selection machinery ─────────────────────────────────────
#
# GENERIC, reusable logic -- exercised below only against a small hand-computed synthetic
# scenario (verifying the code matches the design exactly), NEVER against real per-session
# SCORE populations to pick a "final" threshold. That requires Sec 7(c)'s numeric
# specification, which is not yet frozen. `SCORE <= threshold` => classified harmonic => veto.

def build_candidate_thresholds(*populations: np.ndarray) -> tuple[np.ndarray, int, float | None]:
    """Union of SCORE breakpoints from every population used by the objective or any
    constraint, plus a pass-all sentinel strictly below the GLOBAL minimum across ALL of them
    (round-5 review: a sentinel scoped to only one population is not a true pass-all sentinel
    for the full procedure). Non-finite values are rejected and their count reported, never
    silently included. Returns (candidates_incl_sentinel, n_nonfinite_rejected, sentinel_or_None).
    """
    finite_vals, n_nonfinite = [], 0
    for pop in populations:
        pop = np.asarray(pop, dtype=float)
        finite = pop[np.isfinite(pop)]
        n_nonfinite += len(pop) - len(finite)
        finite_vals.append(finite)
    all_finite = np.concatenate(finite_vals) if finite_vals else np.array([])
    breakpoints = np.unique(all_finite)
    if len(breakpoints) == 0:
        return breakpoints, n_nonfinite, None
    global_min = float(breakpoints.min())
    sentinel = float(np.nextafter(global_min, -np.inf))
    candidates = np.concatenate([[sentinel], breakpoints])
    return candidates, n_nonfinite, sentinel


def select_threshold(candidates, sentinel, safety_ok, utility_ok, objective) -> dict:
    """Sec 7(g)'s three ordered stages, applied in order -- stage 2 is never evaluated for a
    threshold that failed stage 1; stage 3 compares only thresholds passing both.

    safety_ok(threshold)  -> bool   (cardiac retention, false-harmonic bound, yield/NaN,
                                     leverage/persistence -- Stage 1)
    utility_ok(threshold) -> bool   (M0 all-pass gate + minimum-utility bar -- Stage 2)
    objective(threshold)  -> int    (count of baseline severe accepts vetoed -- Stage 3)

    Returns dict with `outcome` in {"invalid_preconditions", "feasible_not_useful",
    "feasible_and_useful"} (round-6 review: "infeasible" is NOT a 3rd scientific outcome --
    with valid inputs the sentinel always trivially clears stage 1; a sentinel failing stage 1
    is a defensive invariant violation, not a real result) plus the chosen threshold, if any.
    """
    if len(candidates) == 0 or sentinel is None:
        return dict(outcome="invalid_preconditions", reason="no_evaluable_data", threshold=None)

    if not safety_ok(sentinel):
        # Defensive only (round-6 review) -- should not occur with valid inputs. Reported
        # distinctly from the two real outcomes, never as a scientific "no safe threshold".
        return dict(outcome="invalid_preconditions", reason="sentinel_failed_stage1_safety",
                     threshold=None)

    stage1_pass = [c for c in candidates if safety_ok(c)]
    stage2_pass = [c for c in stage1_pass if utility_ok(c)]
    if not stage2_pass:
        return dict(outcome="feasible_not_useful", threshold=None,
                     stage1_pass_count=len(stage1_pass), stage2_pass_count=0)

    scored = [(objective(c), c) for c in stage2_pass]
    best_obj = max(o for o, _ in scored)
    tied = sorted(c for o, c in scored if o == best_obj)
    chosen = tied[0]   # smallest SCORE among objective-tied thresholds -- most conservative
    return dict(
        outcome="feasible_and_useful", threshold=chosen, objective_value=best_obj,
        stage1_pass_count=len(stage1_pass), stage2_pass_count=len(stage2_pass), n_tied=len(tied),
    )


def _self_test_threshold_selection() -> bool:
    """Small, hand-computed synthetic scenario -- NOT real session data -- verifying the
    three-stage machinery matches the design exactly, including that the sentinel passes
    stage 1 (trivially, by construction) and is EXPECTED to fail stage 2 (it vetoes nothing,
    so it cannot flag M0's harmonic positives). Worked by hand in the implementation notes;
    re-derived here as executable assertions, not trusted from memory."""
    baseline_good = np.array([4.0, 6.0, 8.0])          # must NEVER be newly rejected
    baseline_severe = np.array([-5.0, -2.0, 1.0, 3.0])  # objective: maximize count vetoed
    m0_positive = np.array([-10.0, -8.0, -9.0])         # ALL must be vetoed (utility gate)
    cardiac_rsa = np.array([2.0, 5.0, 7.0])             # false-harmonic bound (safety)

    candidates, n_nonfinite, sentinel = build_candidate_thresholds(
        baseline_good, baseline_severe, m0_positive, cardiac_rsa,
    )
    assert n_nonfinite == 0
    assert sentinel < baseline_good.min() and sentinel < m0_positive.min()

    def safety_ok(thr):
        return not np.any(baseline_good <= thr) and not np.any(cardiac_rsa <= thr)

    def utility_ok(thr, min_severe_caught=2):
        return bool(np.all(m0_positive <= thr)) and int(np.sum(baseline_severe <= thr)) >= min_severe_caught

    def objective(thr):
        return int(np.sum(baseline_severe <= thr))

    # By hand: safety needs thr < 2 (tighter of thr<4, thr<2); utility needs thr>=-2 (tighter
    # of thr>=-8 for m0, thr>=-2 for >=2 severe caught). Feasible band [-2, 2) among the
    # breakpoints {-10,-9,-8,-5,-2,1,2,3,4,5,6,7,8} is exactly {-2, 1}. Objective(1)=3 beats
    # objective(-2)=2, so the expected pick is threshold=1.0, objective_value=3.
    result = select_threshold(candidates, sentinel, safety_ok, utility_ok, objective)
    assert result["outcome"] == "feasible_and_useful", result
    assert result["threshold"] == 1.0, result
    assert result["objective_value"] == 3, result

    # Sentinel must independently verify: passes stage 1, fails stage 2.
    assert safety_ok(sentinel) is True
    assert utility_ok(sentinel) is False

    # feasible_not_useful case: an M0 case placed ABOVE the safety ceiling makes the utility
    # gate unsatisfiable by ANY safety-passing threshold.
    m0_impossible = np.array([-10.0, -8.0, 100.0])   # 100.0 can never be <= a safety-passing thr
    candidates2, _, sentinel2 = build_candidate_thresholds(
        baseline_good, baseline_severe, m0_impossible, cardiac_rsa,
    )

    def utility_ok2(thr):
        return bool(np.all(m0_impossible <= thr)) and int(np.sum(baseline_severe <= thr)) >= 2

    result2 = select_threshold(candidates2, sentinel2, safety_ok, utility_ok2, objective)
    assert result2["outcome"] == "feasible_not_useful", result2
    assert safety_ok(sentinel2) is True  # sentinel still trivially clears stage 1

    # invalid_preconditions case: no evaluable data at all.
    candidates3, _, sentinel3 = build_candidate_thresholds(np.array([]), np.array([np.nan]))
    result3 = select_threshold(candidates3, sentinel3, safety_ok, utility_ok, objective)
    assert result3["outcome"] == "invalid_preconditions", result3

    print("THRESHOLD-SELECTION SELF-TEST: PASS")
    print(f"  feasible_and_useful case -> {result}")
    print(f"  feasible_not_useful case -> {result2}")
    print(f"  invalid_preconditions case -> {result3}")
    return True


def main() -> None:
    SEP = "=" * 96
    print(SEP)
    print("STAGE 1B ROUND 3 -- multi-hop-lag motion statistic: CONTROL SCAFFOLD")
    print("*** This run exercises CONTROLS ONLY. It does not compute a real lag-10 SCORE   ***")
    print("*** distribution as a scientific result, fit a final threshold against real     ***")
    print("*** sessions, or evaluate the decisive endpoint. That requires Sec 7(c)'s       ***")
    print("*** numeric specification, which is not yet frozen (notes/note_stage1b_lag_    ***")
    print("*** statistic.md). Any real-session numbers below are plumbing checks only.     ***")
    print(SEP)

    m0_ok = run_m0()
    if not m0_ok:
        print("\n*** M0 control failed -- stopping. Fix the frozen-k/persistence contract before")
        print("*** trusting M0b, the null, the cardiac/RSA controls, or the threshold selector. ***")
        return

    run_m0b()
    run_null_control("sweep")
    run_cardiac_rsa_control()
    _self_test_threshold_selection()

    print("\n" + SEP)
    print("REAL-DATA PLUMBING CHECK (Sec 4(ii)) -- diagnostic only, all 3 exploratory sessions")
    print(SEP)
    for run_name in RUNS:
        run_real_surrogate(run_name)

    print("\n" + SEP)
    print("CONTROL SCAFFOLD COMPLETE.")
    print("Next (per notes/note_stage1b_lag_statistic.md): freeze Sec 7(c)'s numeric bars with")
    print("real derivations, then -- and only then -- compute a real lag-10 SCORE distribution,")
    print("fit the threshold on the 3 exploratory sessions, and plan the new primary capture.")
    print(SEP)


if __name__ == "__main__":
    main()
