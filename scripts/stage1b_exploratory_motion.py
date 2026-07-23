"""Stage 1B — EXPLORATORY redesign. Motion statistic, leakage-free, Masimo-endpoint.

Plan: notes/plan_eca_forbidden_zone.md PART IV Stage 1B. Round 1 lives in
scripts/stage1b_temporal_continuity.py and is PRESERVED as the failed pre-specified experiment.

=============================================================================================
THIS IS EXPLORATORY, NOT CONFIRMATORY.
I have already seen round-1 outcomes on these same two sessions. Nothing here can be reported
as clean held-out evidence. Its ONLY purpose is to decide what to FREEZE and then test on
untouched captures. Any number below is a design aid, not a result.
=============================================================================================

WHY ROUND 1 WAS INVALID (all three verified, 2026-07-14)

  1. TARGET LEAKAGE — fatal. The label was "harmonic <=> min_k |f_c - k*f_r| <= 3 bpm", and the
     statistic S1 was the trailing median of that SAME distance. Classifying by S1 was close to
     classifying by the label definition. AUC 0.90-0.94 was partly tautological, and holding out
     a session did not remove it: both sessions shared the label construction.

  2. MY K_RANGE DIAGNOSIS WAS WRONG. I blamed orders 11..20. But those lie far above the cardiac
     band and essentially never matched a candidate, so removing them changes coverage by ~nothing.
     The in-band orders alone already cover 33% of the band at 18 bpm and 58% at 12 bpm, and a
     2 bpm f_r error still moves a VALID in-band harmonic by 6-20 bpm. The proposed fix would
     NOT have worked.

  3. THE "DECOY" CHECK DID NOT MEASURE WHAT IT CLAIMED. It counted rank-0 harmonic CANDIDATES —
     which AHET had already REJECTED. Measured: v1 accepted only 5 hops (natural) and 3 (paced16),
     with max |error| 0.22 and 2.64 bpm and ZERO severe false accepts. There are essentially NO
     "known confident decoys" in the real runs; the decoys were in the SYNTHETIC test.

WHAT CHANGES HERE

  MOTION STATISTIC (breaks the leakage). Score how a candidate MOVES, not where it SITS.
  Causal one-step prediction, k inferred from PRIOR samples only and held fixed per trajectory:

      harmonic model:  f_hat(t) = f_c(t-1) + k * ( f_r(t) - f_r(t-1) )
      cardiac  model:  f_hat(t) = f_c(t-1)
      SCORE(t) = |err_harmonic| - |err_cardiac|        (negative => moves LIKE a harmonic)

  This is a statement about motion. It is NOT the label's distance, so the label cannot leak in.

  DYNAMIC ADMISSIBLE ORDERS, per hop:  k_min = ceil(band_lo / f_r), k_max = floor(band_hi / f_r)

  PRIMARY ENDPOINT = WINDOW-LEVEL BEHAVIOUR vs PI-GATED MASIMO PR:
      severe false accepts (|err| > 5 bpm), MAE, yield, latency, NaN rate.
  Harmonic labels are DIAGNOSTICS ONLY — never ground truth, never the endpoint.

  MASIMO handled properly: PI-gated, averaged over the SAME trailing 30 s window that produced
  the radar spectrum (round 1 sampled a single PR value at the rounded hop epoch, ungated).

LEVERAGE PRE-CHECK: the motion statistic needs f_r to MOVE. If f_r is static, the harmonic and
cardiac models coincide and the score is ~0 by construction. We measure that leverage FIRST and
report it — if leverage is absent, the experiment is inconclusive BY CONSTRUCTION and says so.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from src import masimo  # noqa: E402

WINDOW_S     = 30.0      # radar analysis window — Masimo is averaged over the SAME span
PI_GATE      = 0.5       # CLAUDE.md: below this the Masimo PR is not trustworthy
TOL_BPM      = 3.0
SEVERE_BPM   = 5.0
HIST         = 5
MAX_JUMP_BPM = 6.0
MAX_GAP      = 2
CARDIAC_BAND = (0.8, 2.0)
RESP_BAND    = (0.1, 0.5)
COLLAPSE_EPS = 0.005
COLLAPSE_N   = 2

RUNS = {
    "natural": REPO_ROOT / "results/live_demo/20260713_172042_live_demo_massimo1",
    "paced16": REPO_ROOT / "results/live_demo/20260713_182002_live_demo_massimo2",
    # Stepped 12->15->18->21 bpm sweep (2026-07-14) — the first capture with real f_r MOVEMENT,
    # which is the precondition the leverage pre-check found missing on the two runs above.
    "sweep": REPO_ROOT / "results/live_demo/20260714_180523_live_demo_sweep",
}

# This subject's HR ran ~80-88 bpm during the sweep (higher than the ~65-72 bpm seen on 07-13,
# plausibly from the effort of actively pacing 4 rate changes). 4*21bpm=84 lands on the OBSERVED
# HR here — the real collision in this capture is near the END of the sweep (21 bpm step), not
# the middle (18 bpm) as originally designed around the older, lower resting HR. See HISTORY.md
# 2026-07-14 "Captured the stepped-rate sweep" for the full derivation.


def admissible_ks(f_r_hz: float) -> range:
    """Per-hop orders whose k*f_r can actually LAND in the cardiac band."""
    if not np.isfinite(f_r_hz) or f_r_hz <= 0:
        return range(0, 0)
    k_lo = int(np.ceil(CARDIAC_BAND[0] / f_r_hz - 1e-9))
    k_hi = int(np.floor(CARDIAC_BAND[1] / f_r_hz + 1e-9))
    return range(max(k_lo, 1), max(k_hi, 0) + 1)


STATIONARITY_GATE_BPM = 5.0    # notes/comparator_prespec.md S2.2/S2.3 — p90-p10 within the window
MIN_COVERAGE_FRAC = 0.80       # notes/comparator_prespec.md S2.2


def load_run(run_dir: Path) -> pd.DataFrame:
    meta = json.loads((run_dir / "run_metadata.json").read_text(encoding="utf-8"))
    t0 = datetime.fromisoformat(meta["start_wall_utc"]).timestamp()
    # The Masimo export filename is user-chosen (demo_massimo1.csv, demo_sweep.csv, ...) — match
    # by ELIMINATION (the only other CSV in a run folder is live_estimates.csv), not by a
    # "massimo" substring, which silently failed to find e.g. demo_sweep.csv.
    csv = next(p for p in run_dir.glob("*.csv") if p.name != "live_estimates.csv")
    m = masimo.load_masimo(csv)
    d = pd.read_csv(run_dir / "live_estimates.csv")
    z = np.load(run_dir / "live_intermediates.npz", allow_pickle=True)
    d["f_r_hz"] = z["f_r_hz"]
    cand = z["candidate_refined_hz"] * 60.0
    for i in range(cand.shape[1]):
        d[f"cand{i}"] = cand[:, i]

    # Reference per notes/comparator_prespec.md, BINDING on every agreement number:
    #   - MEDIAN (not mean) of PI-gated PR over the trailing 30 s window;
    #   - coverage gate: >= 80% of expected samples surviving the PI gate;
    #   - stationarity gate: exclude if p90-p10 of in-window PR > 5.0 bpm (non-stationary
    #     reference -> no single "true HR" for that window -> masimo_pr = NaN, not averaged over).
    pr, pi_med, n_ok, spread, stationary = [], [], [], [], []
    for _, r in d.iterrows():
        e1 = t0 + r["elapsed_s"]
        e0 = e1 - WINDOW_S
        w = m[(m.epoch_utc >= e0) & (m.epoch_utc <= e1)]
        good = w[w.pi >= PI_GATE]
        n_ok.append(len(good))
        pi_med.append(w.pi.median() if len(w) else np.nan)
        enough = len(good) >= MIN_COVERAGE_FRAC * WINDOW_S
        sp = (np.percentile(good.pr_bpm, 90) - np.percentile(good.pr_bpm, 10)) if len(good) >= 5 else np.nan
        spread.append(sp)
        is_stationary = enough and np.isfinite(sp) and sp <= STATIONARITY_GATE_BPM
        stationary.append(bool(is_stationary))
        pr.append(good.pr_bpm.median() if is_stationary else np.nan)
    d["masimo_pr"] = pr
    d["masimo_pi"] = pi_med
    d["masimo_n"] = n_ok
    d["masimo_spread"] = spread
    d["masimo_stationary"] = stationary
    return d


def collapse_mask(f_r: np.ndarray) -> np.ndarray:
    at_edge = (np.abs(f_r - RESP_BAND[0]) <= COLLAPSE_EPS) | \
              (np.abs(f_r - RESP_BAND[1]) <= COLLAPSE_EPS) | ~np.isfinite(f_r)
    out = np.zeros(len(f_r), dtype=bool)
    i = 0
    while i < len(f_r):
        if at_edge[i]:
            j = i
            while j < len(f_r) and at_edge[j]:
                j += 1
            if j - i >= COLLAPSE_N:
                out[i:j] = True
            i = j
        else:
            i += 1
    return out


def associate(d: pd.DataFrame, usable: np.ndarray) -> pd.DataFrame:
    rows, tracks, nid = [], {}, 0
    for i in range(len(d)):
        if not usable[i]:
            tracks.clear()
            continue
        for r in range(3):
            f = d[f"cand{r}"].iloc[i]
            if not np.isfinite(f):
                continue
            best, bd = None, np.inf
            for tid, tr in tracks.items():
                gap = i - tr["hop"]
                if 1 <= gap <= MAX_GAP:
                    dist = abs(f - tr["f"])
                    if dist <= MAX_JUMP_BPM * gap and dist < bd:
                        best, bd = tid, dist
            if best is None:
                best, nid = nid, nid + 1
            tracks[best] = {"hop": i, "f": f}
            rows.append({"hop": i, "rank": r, "track": best, "f_cand": f})
    return pd.DataFrame(rows)


def motion_scores(tr: pd.DataFrame, d: pd.DataFrame) -> pd.DataFrame:
    """Causal one-step prediction. k inferred from PRIOR samples only, fixed per trajectory."""
    out = []
    fr_bpm = d["f_r_hz"].values * 60.0
    for tid, g in tr.groupby("track"):
        g = g.sort_values("hop")
        hops, fc = g["hop"].values, g["f_cand"].values
        for n in range(1, len(g)):
            if n < HIST:                       # need trailing history to infer k causally
                continue
            prior = slice(max(0, n - HIST), n)  # STRICTLY prior samples
            hp, fp = hops[prior], fc[prior]
            frp = fr_bpm[hp]
            if not np.all(np.isfinite(frp)) or np.any(frp <= 0):
                continue
            ks = admissible_ks(d["f_r_hz"].values[hops[n]])
            if len(ks) == 0:
                continue
            k_hat = int(np.clip(round(float(np.median(fp / frp))), min(ks), max(ks)))

            t, tprev = hops[n], hops[n - 1]
            dfr = fr_bpm[t] - fr_bpm[tprev]
            if not np.isfinite(dfr):
                continue
            pred_h = fc[n - 1] + k_hat * dfr    # harmonic model: moves WITH f_r
            pred_c = fc[n - 1]                  # cardiac model: does not
            err_h = abs(fc[n] - pred_h)
            err_c = abs(fc[n] - pred_c)
            out.append({
                "hop": int(t), "track": int(tid), "rank": int(g["rank"].values[n]),
                "f_cand": float(fc[n]), "k_hat": k_hat, "d_fr_bpm": float(dfr),
                "err_h": float(err_h), "err_c": float(err_c),
                "SCORE": float(err_h - err_c),   # negative => moves like a harmonic
            })
    return pd.DataFrame(out)


def diag_label(f_cand, pr, f_r_hz):
    """DIAGNOSTIC ONLY — never the endpoint (round 1's mistake)."""
    if not np.isfinite(f_cand):
        return "invalid"
    near_c = np.isfinite(pr) and abs(f_cand - pr) <= TOL_BPM
    ks = admissible_ks(f_r_hz)
    fr_b = f_r_hz * 60.0
    near_h = any(abs(f_cand - k * fr_b) <= TOL_BPM for k in ks)
    if near_c and near_h:
        return "ambiguous_both"
    return "cardiac_only" if near_c else ("harmonic_only" if near_h else "neither")


def main() -> None:
    SEP = "=" * 96
    print(SEP)
    print("STAGE 1B — EXPLORATORY redesign (motion statistic; leakage-free; Masimo endpoint)")
    print("*** EXPLORATORY, NOT CONFIRMATORY — round-1 outcomes on this data are already seen. ***")
    print("*** Purpose: decide what to FREEZE, then test on untouched captures.               ***")
    print(SEP)

    S = {}
    for name, rd in RUNS.items():
        d = load_run(rd)
        fr = d["f_r_hz"].values
        coll = collapse_mask(fr)
        usable = ~coll & np.isfinite(fr)
        tr = associate(d, usable)
        sc = motion_scores(tr, d)
        if len(sc):
            sc["masimo_pr"] = d["masimo_pr"].values[sc["hop"].values]
            sc["f_r_hz"] = fr[sc["hop"].values]
            sc["label"] = [diag_label(r.f_cand, r.masimo_pr, r.f_r_hz) for r in sc.itertuples()]
            sc["err_vs_masimo"] = (sc.f_cand - sc.masimo_pr).abs()
        S[name] = dict(d=d, coll=coll, usable=usable, sc=sc)

    # ── 0. LEVERAGE PRE-CHECK — the statistic is powerless if f_r does not move ──
    print("\n0. LEVERAGE PRE-CHECK — does f_r actually MOVE? (if not, the two models coincide and")
    print("   the score is ~0 BY CONSTRUCTION, and the experiment is inconclusive by design)")
    print(f"   {'session':<9} {'|d f_r| median':>15} {'|d f_r| p90':>12} {'hops |d f_r|<0.5bpm':>21}")
    for k, s in S.items():
        sc = s["sc"]
        if not len(sc):
            continue
        a = sc.d_fr_bpm.abs()
        print(f"   {k:<9} {a.median():>13.2f} bpm {a.quantile(0.9):>10.2f} bpm "
              f"{f'{int((a < 0.5).sum())}/{len(a)}':>21}")
    print("   Interpretation: leverage per hop = |k * d f_r|. With k~4 a 0.5 bpm f_r move gives")
    print("   only ~2 bpm of predicted candidate motion — around one FFT bin. Thin.")

    # ── 1. PRIMARY ENDPOINT — window-level behaviour vs PI-gated Masimo ──
    print("\n1. PRIMARY ENDPOINT — what v1 ACTUALLY DID (PI-gated Masimo PR over the same 30 s window)")
    print(f"   {'session':<9} {'hops':>5} {'AHET-accepted':>14} {'MAE':>8} {'severe(>5bpm)':>14} {'NaN rate':>9}")
    for k, s in S.items():
        d = s["d"]
        acc = d[(d.hr_valid == 1) & d.masimo_pr.notna()]
        err = (acc.hr_bpm_raw - acc.masimo_pr).abs()
        sev = int((err > SEVERE_BPM).sum())
        print(f"   {k:<9} {len(d):>5} {len(acc):>14} {err.mean():>7.2f}  {sev:>14} "
              f"{1 - len(acc)/len(d):>8.0%}")
    print("   NOTE: this table is COMPARATOR-DEPENDENT and that is itself a finding — see below.")
    print("   Against an INSTANTANEOUS PR at the hop epoch, the same hops give MAE 0.16 (natural)")
    print("   and ZERO severe errors. Against a 30 s WINDOW MEAN they give MAE 2.72 and 1 severe.")
    print("   Neither comparator is obviously right when the reference is NON-STATIONARY inside")
    print("   the window — and in the natural run it is: PR ran 75 -> 94 -> 65 bpm over the first")
    print("   25 s (PI 7-10, so the excursion is REAL, not an artefact). An FFT peak over 30 s")
    print("   estimates the DOMINANT frequency, which is not the arithmetic mean of instantaneous")
    print("   rates. The comparator must be pre-specified, and settling windows excluded.")

    # ── 2. Does the motion score separate cardiac from harmonic? (DIAGNOSTIC labels) ──
    print("\n2. MOTION SCORE vs DIAGNOSTIC LABELS  (labels are diagnostics, NOT the endpoint)")
    print(f"   {'session':<9} {'n_card':>7} {'n_harm':>7} {'SCORE card med':>15} {'SCORE harm med':>15} {'separation':>11}")
    for k, s in S.items():
        sc = s["sc"]
        if not len(sc):
            continue
        c = sc[sc.label == "cardiac_only"]
        h = sc[sc.label == "harmonic_only"]
        if not len(c) or not len(h):
            print(f"   {k:<9} {len(c):>7} {len(h):>7}   (insufficient)")
            continue
        sep = c.SCORE.median() - h.SCORE.median()
        print(f"   {k:<9} {len(c):>7} {len(h):>7} {c.SCORE.median():>15.2f} "
              f"{h.SCORE.median():>15.2f} {sep:>11.2f}")
    print("   Expect: harmonics move WITH f_r => SCORE < 0.  Cardiac => SCORE ~ 0 or > 0.")

    # ── 3. Label census (diagnostic) ──
    print("\n3. LABEL CENSUS (diagnostic only; dynamic admissible orders k_min..k_max per hop)")
    for k, s in S.items():
        sc = s["sc"]
        if len(sc):
            print(f"   {k:<9} {dict(sc.label.value_counts())}")


if __name__ == "__main__":
    main()
