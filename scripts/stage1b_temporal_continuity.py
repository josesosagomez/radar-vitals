"""Stage 1B — Temporal continuity as a respiratory-harmonic discriminant.

Plan: notes/plan_eca_forbidden_zone.md, PART IV, Stage 1B (v2 contract).
READ-ONLY: touches no production code and no config. Consumes only existing live-run artifacts.

HYPOTHESIS
----------
A respiratory-harmonic candidate TRACKS f_r across hops; a cardiac candidate does not.
This is a claim about how a candidate MOVES. It is indifferent to whether high-order harmonics
are coherent, to f_r precision, and to whether ECA works at all.

=============================================================================================
PRE-SPECIFICATION — written and committed BEFORE looking at any result (review comment 1B.4).
Nothing below this block was tuned after seeing an outcome.
=============================================================================================

S1 (PRIMARY statistic) — harmonic-tracking residual, in bpm:

        S1(t) = median over the trailing window of  | f_cand(u) - k_hat * f_r(u) |

    where k_hat is ONE integer inferred per TRAJECTORY (not per hop), from the trailing history.
    Small S1  -> the candidate moves with f_r  -> harmonic.
    Large S1  -> it does not                   -> cardiac.

S2 (SECONDARY) — coupling slope: regress d(f_cand) on d(f_r) over the trailing window.
    Harmonic -> slope ~ k_hat.  Cardiac -> slope ~ 0.

THRESHOLD RULE: chosen on the DEVELOPMENT run as the value maximising Youden's J
    (sensitivity + specificity - 1) over cardiac_only vs harmonic_only, then applied
    UNCHANGED to the VALIDATION run. Both directions (A->B and B->A) are reported.

CAUSALITY: trailing history only. Hop t uses hops <= t. No Viterbi, no future windows.
    (The existing scripts/diagnose_step6_candidate_tracks.py uses viterbi_track(), a max-score
    DP over the FULL sequence — a legitimate offline oracle, but NOT a real-time discriminator.)

FIXED PARAMETERS (pre-specified, not swept):
    TOL_BPM        = 3.0    label tolerance
    HIST           = 5      trailing hops required (warm-up cost; counted against yield)
    MAX_JUMP_BPM   = 6.0    trajectory association max jump per hop
    MAX_GAP        = 2      trajectory association max gap in hops
    K_RANGE        = 1..20  harmonic orders considered for labelling
    COLLAPSE_EPS   = 0.005 Hz, COLLAPSE_N = 2 consecutive hops   (band-floor lock)

LABELS (four — review comment 1B.1). A candidate can be near BOTH the Masimo PR and some k*f_r;
that IS the collision case and must not be hidden by a "cardiac wins" rule:
    cardiac_only | harmonic_only | ambiguous_both | neither
AUC is computed on cardiac_only vs harmonic_only ONLY. ambiguous_both / neither are EXCLUDED
from AUC and reported separately. Ambiguous collisions are NaN outcomes, never successes.

COLLAPSE EXCLUSION is computed DIRECTLY from f_r (band-floor lock), NOT from resp_valid —
resp_valid has a known false-confidence bug (it stayed 1 while f_r was pinned to the 0.1 Hz
floor, 3 occurrences in 3 runs), so using it would be circular.

REPORTING: per session. Windows overlap 27/30 s at a 3 s hop, so candidate rows are NOT
independent; raw counts overstate the effective sample size. No pooled ROC. Block resampling
if any uncertainty is quoted.

With two same-subject sessions this is FEASIBILITY EVIDENCE, NOT VALIDATION.
=============================================================================================

Usage:
    python -X utf8 scripts/stage1b_temporal_continuity.py
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

# ── Pre-specified constants (do not tune) ──────────────────────────────────────────────────
TOL_BPM      = 3.0
HIST         = 5
MAX_JUMP_BPM = 6.0
MAX_GAP      = 2
K_RANGE      = range(1, 21)
COLLAPSE_EPS = 0.005     # Hz from the respiration search-band edge
COLLAPSE_N   = 2         # consecutive hops to call it a lock
RESP_BAND    = (0.1, 0.5)

RUNS = {
    "natural": REPO_ROOT / "results/live_demo/20260713_172042_live_demo_massimo1",
    "paced16": REPO_ROOT / "results/live_demo/20260713_182002_live_demo_massimo2",
}


# ── Loading ────────────────────────────────────────────────────────────────────────────────
def load_run(run_dir: Path) -> pd.DataFrame:
    meta = json.loads((run_dir / "run_metadata.json").read_text(encoding="utf-8"))
    t0 = datetime.fromisoformat(meta["start_wall_utc"]).timestamp()
    csv = next(p for p in run_dir.glob("*.csv") if "massimo" in p.name.lower())
    m = masimo.load_masimo(csv).set_index("epoch_utc")
    d = pd.read_csv(run_dir / "live_estimates.csv")
    z = np.load(run_dir / "live_intermediates.npz", allow_pickle=True)

    d["epoch"] = (t0 + d["elapsed_s"]).round().astype(int)
    d["masimo_pr"] = d.epoch.map(m.pr_bpm)
    d["f_r_hz"] = z["f_r_hz"]
    cand = z["candidate_refined_hz"] * 60.0          # (n_hops, 3) bpm
    for i in range(cand.shape[1]):
        d[f"cand{i}"] = cand[:, i]
    return d


# ── Collapse detection — direct band-floor lock, NOT resp_valid ────────────────────────────
def collapse_mask(f_r: np.ndarray) -> np.ndarray:
    """True where f_r is locked to a respiration search-band edge for >= COLLAPSE_N hops."""
    at_edge = (np.abs(f_r - RESP_BAND[0]) <= COLLAPSE_EPS) | \
              (np.abs(f_r - RESP_BAND[1]) <= COLLAPSE_EPS) | \
              ~np.isfinite(f_r)
    out = np.zeros(len(f_r), dtype=bool)
    i = 0
    while i < len(f_r):
        if at_edge[i]:
            j = i
            while j < len(f_r) and at_edge[j]:
                j += 1
            if (j - i) >= COLLAPSE_N:
                out[i:j] = True
            i = j
        else:
            i += 1
    return out


def collapse_spans(mask: np.ndarray, t: np.ndarray) -> list[tuple[float, float, int]]:
    spans, i = [], 0
    while i < len(mask):
        if mask[i]:
            j = i
            while j < len(mask) and mask[j]:
                j += 1
            spans.append((float(t[i]), float(t[j - 1]), j - i))
            i = j
        else:
            i += 1
    return spans


# ── Labelling — FOUR labels ────────────────────────────────────────────────────────────────
def label_candidate(cand_bpm: float, pr_bpm: float, f_r_hz: float) -> str:
    if not np.isfinite(cand_bpm):
        return "invalid"
    near_cardiac = np.isfinite(pr_bpm) and abs(cand_bpm - pr_bpm) <= TOL_BPM
    fr_bpm = f_r_hz * 60.0
    near_harm = any(abs(cand_bpm - k * fr_bpm) <= TOL_BPM for k in K_RANGE)
    if near_cardiac and near_harm:
        return "ambiguous_both"
    if near_cardiac:
        return "cardiac_only"
    if near_harm:
        return "harmonic_only"
    return "neither"


# ── Trajectory association (CAUSAL, forward-only greedy) ───────────────────────────────────
def associate(d: pd.DataFrame, usable: np.ndarray) -> pd.DataFrame:
    """Link candidates into trajectories. Rank is NOT identity (review 1B.2)."""
    rows = []
    tracks: dict[int, dict] = {}       # track_id -> {last_hop, last_f}
    next_id = 0
    for i in range(len(d)):
        if not usable[i]:
            tracks.clear()             # reset after a gap/collapse
            continue
        for r in range(3):
            f = d[f"cand{r}"].iloc[i]
            if not np.isfinite(f):
                continue
            best, best_d = None, np.inf
            for tid, tr in tracks.items():
                gap = i - tr["last_hop"]
                if gap < 1 or gap > MAX_GAP:
                    continue
                dist = abs(f - tr["last_f"])
                if dist <= MAX_JUMP_BPM * gap and dist < best_d:
                    best, best_d = tid, dist
            if best is None:
                best = next_id
                next_id += 1
            tracks[best] = {"last_hop": i, "last_f": f}
            rows.append({"hop": i, "rank": r, "track": best, "f_cand": f})
    return pd.DataFrame(rows)


# ── Statistics S1 / S2 — causal, trailing history only ─────────────────────────────────────
def compute_stats(tr: pd.DataFrame, d: pd.DataFrame) -> pd.DataFrame:
    out = []
    fr_bpm_all = d["f_r_hz"].values * 60.0
    for tid, g in tr.groupby("track"):
        g = g.sort_values("hop")
        hops = g["hop"].values
        fc = g["f_cand"].values
        for n in range(len(g)):
            if n + 1 < HIST:                      # warm-up: not enough trailing history
                continue
            sl = slice(n + 1 - HIST, n + 1)       # trailing window, causal
            h_w, f_w = hops[sl], fc[sl]
            fr_w = fr_bpm_all[h_w]
            if not np.all(np.isfinite(fr_w)) or np.any(fr_w <= 0):
                continue
            # ONE k per trajectory, inferred from the trailing history (review 1B.2)
            k_hat = int(np.clip(round(float(np.median(f_w / fr_w))), 1, max(K_RANGE)))
            s1 = float(np.median(np.abs(f_w - k_hat * fr_w)))
            # S2 — coupling slope of d(f_cand) on d(f_r)
            dfc, dfr = np.diff(f_w), np.diff(fr_w)
            s2 = float(np.polyfit(dfr, dfc, 1)[0]) if np.ptp(dfr) > 1e-9 else np.nan
            out.append({
                "hop": int(hops[n]), "track": int(tid), "rank": int(g["rank"].values[n]),
                "f_cand": float(fc[n]), "k_hat": k_hat, "S1": s1, "S2": s2,
            })
    return pd.DataFrame(out)


# ── Threshold selection (Youden's J) — fit on DEV only ──────────────────────────────────────
def youden_threshold(s: np.ndarray, is_cardiac: np.ndarray) -> float:
    best_t, best_j = np.nan, -np.inf
    for t in np.unique(np.round(s, 3)):
        pred_card = s > t                      # large S1 => cardiac
        tp = np.sum(pred_card & is_cardiac)
        fn = np.sum(~pred_card & is_cardiac)
        tn = np.sum(~pred_card & ~is_cardiac)
        fp = np.sum(pred_card & ~is_cardiac)
        sens = tp / max(tp + fn, 1)
        spec = tn / max(tn + fp, 1)
        j = sens + spec - 1
        if j > best_j:
            best_t, best_j = float(t), j
    return best_t


def auc(s: np.ndarray, is_cardiac: np.ndarray) -> float:
    pos, neg = s[is_cardiac], s[~is_cardiac]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    # Mann-Whitney U / rank AUC
    allv = np.concatenate([pos, neg])
    ranks = pd.Series(allv).rank().values
    r_pos = ranks[: len(pos)].sum()
    return float((r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def build(name: str, run_dir: Path) -> dict:
    d = load_run(run_dir)
    fr = d["f_r_hz"].values
    coll = collapse_mask(fr)
    usable = ~coll & np.isfinite(fr)
    tr = associate(d, usable)
    st = compute_stats(tr, d)
    if len(st):
        st["masimo_pr"] = d["masimo_pr"].values[st["hop"].values]
        st["f_r_hz"] = fr[st["hop"].values]
        st["label"] = [
            label_candidate(r.f_cand, r.masimo_pr, r.f_r_hz) for r in st.itertuples()
        ]
    return {
        "name": name, "d": d, "collapse": coll, "usable": usable, "stats": st,
        "spans": collapse_spans(coll, d["elapsed_s"].values),
        "n_hops": len(d),
    }


# ── Reporting ──────────────────────────────────────────────────────────────────────────────
def main() -> None:
    S = {k: build(k, v) for k, v in RUNS.items()}
    SEP = "=" * 94

    print(SEP)
    print("STAGE 1B — temporal continuity as a respiratory-harmonic discriminant")
    print("  Statistic + threshold rule were PRE-SPECIFIED in the module docstring before any run.")
    print("  Causal (trailing history only). Two same-subject sessions => FEASIBILITY, not validation.")
    print(SEP)

    # 1 — collapse exclusion, computed from f_r directly (never from resp_valid)
    print("\n1. COLLAPSE EXCLUSION  (direct band-floor lock; resp_valid NOT used — it has a known")
    print("   false-confidence bug and using it would be circular)")
    for k, s in S.items():
        n_ex = int(s["collapse"].sum())
        print(f"   {k:<8} hops={s['n_hops']:<3} excluded={n_ex:<3} "
              f"({100*n_ex/s['n_hops']:.0f}%)  spans(t_start,t_end,n): {s['spans']}")

    # 2 — four-label census
    print("\n2. LABEL CENSUS  (four labels; ambiguous_both = the collision case, NOT hidden)")
    hdr = f"   {'session':<8} {'cardiac_only':>13} {'harmonic_only':>14} {'ambiguous_both':>15} {'neither':>8}"
    print(hdr)
    for k, s in S.items():
        st = s["stats"]
        if not len(st):
            print(f"   {k:<8}  (no scored candidates)")
            continue
        c = st["label"].value_counts()
        print(f"   {k:<8} {c.get('cardiac_only',0):>13} {c.get('harmonic_only',0):>14} "
              f"{c.get('ambiguous_both',0):>15} {c.get('neither',0):>8}")
    print("   NOTE: AUC uses cardiac_only vs harmonic_only ONLY. ambiguous_both/neither are")
    print("         excluded from AUC and reported here; ambiguous collisions are NaN outcomes.")

    # 3 — separation, per session (windows overlap 27/30 s => NOT independent; no pooled ROC)
    print("\n3. SEPARATION, PER SESSION  (no pooled ROC — 30 s windows at a 3 s hop share 27/30 s)")
    print(f"   {'session':<8} {'n_card':>7} {'n_harm':>7} {'AUC(S1)':>8} {'S1 card med':>12} {'S1 harm med':>12}")
    for k, s in S.items():
        st = s["stats"]
        if not len(st):
            continue
        sub = st[st.label.isin(["cardiac_only", "harmonic_only"])]
        if not len(sub):
            print(f"   {k:<8}  (no separable candidates)")
            continue
        isc = (sub.label == "cardiac_only").values
        a = auc(sub.S1.values, isc)
        print(f"   {k:<8} {int(isc.sum()):>7} {int((~isc).sum()):>7} {a:>8.3f} "
              f"{sub.S1[isc].median():>12.2f} {sub.S1[~isc].median():>12.2f}")

    # 4 — HELD-OUT, both directions. Threshold fit on DEV, applied UNCHANGED to VAL.
    print("\n4. HELD-OUT EVALUATION  (threshold fit on DEV via Youden's J, applied UNCHANGED to VAL)")
    print(f"   {'dev -> val':<22} {'thr(S1)':>8} {'val AUC':>8} {'val sens':>9} {'val spec':>9} {'decoys rejected':>16}")
    results = {}
    names = list(S)
    for dev, val in [(names[0], names[1]), (names[1], names[0])]:
        sd = S[dev]["stats"]
        sv = S[val]["stats"]
        if not len(sd) or not len(sv):
            continue
        d_sub = sd[sd.label.isin(["cardiac_only", "harmonic_only"])]
        v_sub = sv[sv.label.isin(["cardiac_only", "harmonic_only"])]
        if not len(d_sub) or not len(v_sub):
            continue
        thr = youden_threshold(d_sub.S1.values, (d_sub.label == "cardiac_only").values)
        isc = (v_sub.label == "cardiac_only").values
        pred_card = v_sub.S1.values > thr
        sens = np.sum(pred_card & isc) / max(isc.sum(), 1)
        spec = np.sum(~pred_card & ~isc) / max((~isc).sum(), 1)
        a = auc(v_sub.S1.values, isc)
        # PASS CRITERION 1: every harmonic_only candidate must be rejected (predicted non-cardiac)
        harm = v_sub[v_sub.label == "harmonic_only"]
        rejected = int(np.sum(harm.S1.values <= thr))
        results[(dev, val)] = dict(thr=thr, auc=a, sens=sens, spec=spec,
                                   rejected=rejected, n_harm=len(harm))
        print(f"   {dev+' -> '+val:<22} {thr:>8.2f} {a:>8.3f} {sens:>9.2f} {spec:>9.2f} "
              f"{f'{rejected}/{len(harm)}':>16}")

    # 5 — the decisive check: the known confident decoys
    print("\n5. KNOWN DECOYS  (rank-0 candidates that are harmonic_only — the ones v1 confidently")
    print("   reported as HR). The discriminant MUST reject these.")
    for (dev, val), r in results.items():
        sv = S[val]["stats"]
        decoys = sv[(sv.label == "harmonic_only") & (sv["rank"] == 0)]
        if not len(decoys):
            print(f"   {val:<8} (no rank-0 harmonic candidates)")
            continue
        rej = int(np.sum(decoys.S1.values <= r["thr"]))
        flag = "ALL REJECTED" if rej == len(decoys) else f"*** {len(decoys)-rej} LEAKED ***"
        print(f"   dev={dev:<8} val={val:<8} rank-0 decoys={len(decoys):<3} "
              f"rejected={rej}/{len(decoys)}  {flag}")

    # 6 — yield / warm-up cost (a discriminator needing history has a real cost; make it visible)
    print("\n6. YIELD AND WARM-UP COST  (HIST=%d trailing hops required)" % HIST)
    for k, s in S.items():
        st = s["stats"]
        scored_hops = st["hop"].nunique() if len(st) else 0
        print(f"   {k:<8} hops={s['n_hops']:<3} usable(after collapse)={int(s['usable'].sum()):<3} "
              f"hops with a scored candidate={scored_hops:<3} "
              f"({100*scored_hops/max(s['n_hops'],1):.0f}% of all hops)")

    print("\n" + SEP)
    print("PASS CRITERIA (pre-specified):")
    print("  1. BOTH held-out directions reject every known confident decoy   -> see section 5")
    print("  2. No severe Masimo errors introduced                            -> see section 4 (sens)")
    print("  3. Yield / warm-up / latency reported                            -> see section 6")
    print("  4. Stable under f_r perturbation                                 -> section 7")
    print("  5. Ambiguous collisions reported as NaN, never as successes      -> section 2")
    print(SEP)

    # 7 — f_r perturbation: labels use k*f_r, so f_r error mislabels candidates
    print("\n7. LABEL STABILITY UNDER f_r PERTURBATION (+/- 1 FFT bin = +/- 0.0333 Hz)")
    for k, s in S.items():
        st = s["stats"]
        if not len(st):
            continue
        base = st["label"].values
        for pert in (+1 / 30.0, -1 / 30.0):
            lab = [
                label_candidate(r.f_cand, r.masimo_pr, r.f_r_hz + pert)
                for r in st.itertuples()
            ]
            same = float(np.mean(np.array(lab) == base))
            print(f"   {k:<8} f_r {pert*60:+.1f} bpm -> labels unchanged on {100*same:.0f}% of candidates")


if __name__ == "__main__":
    main()
