# Stage 1B, round 3 (draft 7) — a multi-hop-lag motion statistic

**Status: DRAFT 7 — CLEARED FOR CONTROL-SCAFFOLDING.** Draft 6's §7(g) rewrite was reviewed
and returned **READY FOR CONTROL-SCAFFOLDING**
(`notes/cross_review_stage1b_lag_statistic_review_6_findings.md`): the three-stage filter,
breakpoint population, and sentinel definition are all confirmed correct. Two small
clarifications from that review are folded into this draft — "infeasible" is not empirically
reachable under valid inputs (the sentinel always trivially clears stage 1), so it's
relabeled a defensive `invalid_preconditions` check rather than a third scientific outcome,
and M0's harmonic-positive cases must be role-tagged (supported / deliberate-invalid /
optional-stress) before execution, never reclassified after seeing a failure. **This closes
the plan-review cycle for this design** (6 rounds: round 1 NOT CLEARED, rounds 2/3/4/5 READY
WITH FIXES on successively narrower issues, round 6 READY FOR CONTROL-SCAFFOLDING).
Signal-generation/scoring scaffolds (M0/M0b/null/RSA) AND threshold-selection code may now
both be implemented. Numeric bars (§7c) still must be frozen before any real lag-10 SCORE
distribution, threshold fit, or endpoint evaluation is run.

**Supersedes nothing — extends round 2.** `scripts/stage1b_exploratory_motion.py` (round 2)
is preserved as-is.

---

## 1. The problem this fixes (unchanged — not in question since round 2's review)

Round 2's causal one-step statistic has no leverage on any capture in hand. A lag spanning
the observed ~30-36 s ramp duration is the fix; `L=10` hops (30 s), frozen as a design
constant.

---

## 2. The redesigned statistic — frozen-`k` contract (confirmed sound; wording fix only)

**Confirmed RESOLVED by round-4 review**: `k_hat` uses candidate and `f_r` values only at
the exact inference hops `h0..h0+4`, clipped via `admissible_ks()` evaluated at `h0+4` (last
inference hop) — no value at the origin `t-L`, the endpoint `t`, or any intervening scoring
hop can alter it. The harmonic model's later use of `f_r(t-L)`/`f_r(t)` is required endpoint
data for the frozen prediction itself, not leakage into `k_hat` — round 2's own review
confirmed this distinction is correctly drawn.

```
harmonic model:  f_hat(t) = f_c(t-L) + k_hat * ( f_r(t) - f_r(t-L) )
cardiac  model:  f_hat(t) = f_c(t-L)
SCORE(t)       = | f_c(t) - f_hat_harmonic(t) |  -  | f_c(t) - f_hat_cardiac(t) |
```

**The full attempt spans `h0` through `h0+15` inclusive — 16 total hop observations, not 11
(fixing round-4 findings 2 and 8; independently re-verified: `len(range(h0, h0+16)) == 16`,
`5` inference hops + `11` scoring-span hops, zero overlap between the two).** Breakdown:
- **Inference**: exact hops `h0..h0+4` — 5 observations.
- **Origin**: `t-L = h0+5`.
- **Scoring span**: exact hops `h0+5..h0+15` inclusive — 11 observations, spanning 10 hop
  intervals (`t - (t-L) = 10`).
- **Full attempt (zero-gap-checked span)**: `h0..h0+15` — **16 observations total**. Every
  one of the 16 must be present; a missing hop anywhere in this full span (not just the
  11-hop scoring portion) invalidates the attempt and restarts it (§3b).

`L=10` remains frozen from the Masimo-measured ramp durations, not re-derived per capture.

---

## 3. The leverage precondition — perturbation instability made exact (resolves round-4 finding 3)

Unchanged: frozen, pre-origin `k_hat` only; raw `|Δf_r|` distribution reported pre-gate;
coverage by session/`k_hat`/class; binding `<30%` stop rule. **Confirmed correct by round-4
review**: 7 independent `±1`-bin perturbations (5 inference `f_r` values + 2 scoring
endpoints) give `2^7 = 128` combinations, re-freezing `k_hat` under each — consistent with
§2's exact-hop contract.

**Fix — the aggregate instability metric had two non-equivalent readings; the review
identified the correct one.** "Worst-case fraction of windows that flip eligibility" means:
**for each window, mark it unstable if ANY of its 128 perturbation combinations flips its
eligibility status; report the fraction of windows so marked.** (The rejected alternative —
picking, per sign-pattern, the fraction of windows that pattern flips, then taking the max
over patterns — answers a different, less conservative question and is NOT used.) This is
the true per-window worst-case robustness check, matching "per candidate scoring attempt."
Optionally, also report the distribution of how many of the 128 combinations flip each
unstable window, as a secondary diagnostic (not required for the pass/fail bar).

### 3b. Trajectory persistence (policy unchanged; span corrected to match §2's fix)

Zero gaps tolerated anywhere across the **full 16-observation attempt** (`h0..h0+15` — not
just the 11-hop scoring span; the review caught this same undercount in §3b's prose,
mirroring §2's). Any missing or collapse/reset hop, anywhere in the full 16, invalidates the
attempt; restart at the next hop after the violation. Implementation must validate the
literal hop-number sequence, not merely trust that round 2's association code retained a
track ID across a gap (it can, via its own `MAX_GAP=2` allowance — this design's scoring
contract independently re-checks regardless).

**M0 must assert all 16 exact hop numbers** — both the 5 inference hops AND the 11 scoring
hops — not the 11-hop scoring span alone (round-4 finding 2's fix: asserting only 11 would
leave a gap inside the inference history unvalidated).

---

## 4. Controls (unchanged — round-4 review: RESOLVED, no further changes required)

M0 (fixed-seed grid, now asserting all 16 hops per case), M0b (real spectral pipeline), null
(missingness-matched), and both cardiac/RSA control arms — (i) the restored known-truth
synthetic control, (ii) the real per-decision empirical surrogate reusing each accepted
decision's frozen `k_hat` — are confirmed correctly specified. No further choice of surrogate
`k_hat` is left to implementation.

---

## 5. What's unchanged from round 1/2 (reuse, don't re-derive)

Unchanged from draft 4.

---

## 6. Data budget (unchanged)

All 3 existing sessions remain exploratory-only. A primary claim requires a new capture,
taken after every contract in §§2-4 and §7(c)-(h) is frozen.

---

## 7. Pass / fail and claim discipline

### (a) Diagnostic mechanism check — unchanged, diagnostic only, never decisive

### (b) The decisive endpoint — unchanged: severe-false-accept reduction vs. baseline

### (c) Numeric specification (2026-07-15 — first real derivation; NOT yet cross-model
reviewed, see "Status" note below before treating as frozen)

**Baseline data this section is derived from.** All 3 exploratory sessions were re-processed
end-to-end through current HEAD (`dfe7fb5`) — bin-lock fix + the Stage-0 `src/vitals.py`
rewrite — because the sessions' original captures predate BOTH (`natural`/`paced16` were
captured at `403c245`, `sweep` at `ab6264c`; neither includes the bin-lock fix or the Stage-0
DSP changes). Fresh replays: `results/live_demo/20260715_164124_replay_unknown` (natural, bin
23 — matches the pre-fix pick, confirming HANDOFF's "unaffected" claim held for the BIN but not
for the DSP version), `20260715_164018_replay_unknown` (paced16, bin 20→26), `20260715_164132_
replay_unknown` (sweep, bin 21→26). Masimo alignment used each session's ORIGINAL live capture's
real `elapsed_s` joined by `frame_idx` to the fresh replay's DSP output (the fresh replay's own
`elapsed_s` is broken under `--replay-fast` — the wall-clock landmine, HANDOFF §6); one extra
hop per fresh replay beyond the original capture's real-time cutoff was dropped for lack of a
ground-truth timestamp. Scored under `notes/comparator_prespec.md` exactly (PI gate, ≥80%
coverage, 5 bpm stationarity gate, median PR). Result:

| session | AHET-accepted | excluded (Masimo non-stationary) | scorable | MAE | severe (>5bpm) | leverage-eligible / clean 16-hop attempts |
|---|---|---|---|---|---|---|
| natural | 5/50 | 4 | 1 | 0.19 bpm | **0** | 1/7 (14.3%) |
| paced16 | 23/50 | 2 | 21 | 0.50 bpm | **0** | 0/28 (**0.0%**) |
| sweep | 30/150 | 11 | 19 | 0.53 bpm | **0** | 36/72 (50.0%) |

All 17 excluded windows were checked individually: every one is a real Masimo-reference
instability (spread 5.1–26.0 bpm) at a settling transient or the sweep's rate-transition
boundaries — legitimate stationarity-gate exclusions, not radar error hidden by the gate.

**Headline finding: zero baseline severe accepts, on all 3 sessions, at the corrected bin.**
This blocks item (i) below outright and is very likely explained by workstream B (the bin-lock
fix) having already removed the dominant real-world mechanism producing severe false accepts in
this dataset — see the "Scope note" at the end of this subsection.

---

**(i) Objective bar — fraction of baseline severe accepts that must convert to NaN vs. be
retained as correct: BLOCKED, not derivable.** There is no baseline severe-accept population on
current data (0/41 scorable accepts across all 3 sessions) to define a conversion target
against. This is an empty-population problem, not a small-sample one — no number derived from
it would mean anything. Also blocks the tied §7(g)-stage-2 sub-item, the **declared
minimum harmful-example-rejection rate** (same empty population). **Both are DEFERRED to the
primary capture** — see the scope note below on what that capture needs to do differently.

**(ii) Safety bar — retention rate for baseline non-severe/genuine-cardiac accepts (must not
regress): retention = 100% (0/41 real, comparator-prespec-scorable AHET-accepted decisions may
be converted to NaN).** Derivation: this is not a chosen tolerance — it is §7(g) stage 1's own
definition of "safety-feasible" ("does not increase false rejections") restated as a number
against the only real population available (natural n=1, paced16 n=21, sweep n=19; pooled
n=41). Any candidate threshold that vetoes even one of these 41 fails stage 1 outright.
**Power caveat (report honestly, don't oversell)**: with n=41 and 0 required false vetoes, the
rule-of-three gives a 95% CI upper bound on the TRUE false-veto rate of only ≈3/41 ≈ 7.3% —
passing this bar is necessary, not a tight guarantee; the primary capture's larger n will
sharpen it. `natural`'s own n=1 is too small to check anything on its own; the pooled n=41 is
the operative population.

**(iii) Coverage guard — minimum retained yield / maximum additional NaN rate: yield loss = 0%,
retained yield = 100% of current AHET-accepted count, on the 3 existing sessions.** This is a
mechanical corollary of (i)+(ii), not an independent choice: with 0 known real severe accepts to
justify vetoing anything, and (ii) requiring all 41 known non-severe accepts retained, the only
safety-feasible behavior on current data IS a no-op. This bound only becomes a real
(non-trivial) test once the primary capture supplies real severe accepts to trade off
against retained yield.

**(iv) Evaluability floor — minimum leverage coverage and minimum exact-track coverage.**
- *Leverage coverage floor*: already defined in code, not a new number —
  `LEVERAGE_STOP_FRACTION = 0.30` (`scripts/stage1b_lag_statistic.py:103`). Measured against
  fresh data: natural 14.3% **FAIL**, paced16 0.0% **FAIL**, sweep 50.0% **PASS**. Two of three
  sessions fall below the pre-existing 30% floor — an honest evaluability finding, not
  something to re-derive.
- *Exact-track/persistence coverage floor* (minimum independent attempts before ANY directional
  claim, including "no effect", is meaningful): derived from a one-sided sign test against a
  p=0.5 null — n=5 independent, all-agreeing observations are the minimum to clear p<0.05
  (0.5^5 = 0.03125 < 0.05; 0.5^4 = 0.0625 does not). **Minimum 5 independent, non-overlapping-
  origin (`h0`) clean 16-hop attempts per session (or pooled, if per-session power is
  insufficient) before any claim beyond "descriptive" is reported.** Raw (overlapping-origin)
  attempt counts measured: natural 7, paced16 28, sweep 72 — all exceed 5, but attempts starting
  at consecutive hops overlap almost entirely (share up to 15 of 16 hops) and are NOT
  independent in the sign-test sense. **Not yet computed**: the actual non-overlapping-origin
  count per session — a concrete follow-up before this floor can be checked, not a number to
  guess at here.

**(v) Control pass bars.**
- *M0*: no new number — restates the already-coded zero-tolerance bar: 100% of "supported"
  cases classified harmonic (48/48) AND 100% of "invalid_gap" cases rejected by the persistence
  contract (6/6). No partial credit either direction.
- *M0b (overlap-smoothness artifact bound)*: `|frac_negative(overlap_3s) −
  frac_negative(nonoverlap_30s)|` ≤ **0.20** at either lag. Derivation: 2× binomial standard
  error at n≈24 (the smaller, non-overlapping scheme's per-series window count — a 720 s
  synthetic session / 30 s hop ≈ 24 windows), `2×sqrt(0.5×0.5/24) ≈ 0.204`. A gap beyond this is
  attributable to more than sampling noise and should be treated as a real overlap-smoothness
  artifact requiring mitigation before trusting production-cadence (3 s hop) SCORE values.
- *Null control (missingness-matched) false-harmonic bound*: re-measured on FRESH (current-
  HEAD) session data (the committed scaffold's own `run_null_control()` defaults to the stale
  `sweep` session and would otherwise silently use pre-fix data) —
  `frac_negative_and_eligible`: natural 0/7, paced16 0/28, sweep 5/72; **pooled 5/107 ≈ 4.7%**.
  Bound: **any real threshold's estimated false-harmonic rate on the null must not exceed
  4.7% + 2×SEM(n=107) ≈ 8.9%**, `SEM = sqrt(0.047×0.953/107) ≈ 0.021`. A threshold that vetoes
  phase-randomized (by-construction non-harmonic) tracks meaningfully above the measured noise
  floor is unsafe.
- *Cardiac/RSA confound control bound*: measured **0%** `frac_negative_and_eligible` across all
  36 tested (rsa_gain ∈ {0, 0.1, 0.3, 0.6}) × (drift ∈ {0, 0.05, 0.15} bpm/hop) × (3 seeds)
  combinations spanning physiologically plausible RSA coupling. Bound: safety requires this stay
  **exactly 0%** across the tested grid — this is a known-truth cardiac control, so any nonzero
  false-harmonic classification here fails stage 1 outright, zero tolerance (not a
  noise-floor judgment call like the null control above, because ground truth here is certain).

**(vi) Primary-capture block-resampling parameters and minimum independent blocks.**
Same sign-test logic as (iv): **minimum 5 independent, non-overlapping 16-hop (48 s) blocks
containing at least one baseline severe accept**, before the primary capture can support
any directional claim about severe-accept conversion. **Capture-design implication, not just a
statistics parameter**: baseline severe-accept rate is apparently very low-to-zero even in a
486 s stepped-rate session post-fix (`sweep` produced 0 severe accepts despite being the most
provocative capture taken so far) — a repeat of the existing stepped-rate protocol is not
guaranteed to produce ANY severe accepts to test against. The primary capture should be
explicitly designed around a scenario plausible to still trigger one post-bin-fix (e.g. the
`k×f_r ≈ HR` collision the stepped-rate protocol was originally built around, HANDOFF §4),
not assumed to reproduce one by default.

**Scope note — what the zero-baseline-severe-accepts finding means for this design.** The
bin-lock fix (workstream B, `863600e`/`dfe7fb5`) plausibly already removed the dominant
real-world mechanism Stage 1B was built to catch: on the corrected pipeline, AHET's own
conservatism at the correct bin appears to leave nothing severe behind on these 3
short/single-subject sessions. This does not mean the mechanism (respiratory-harmonic false
accepts) is impossible in general — only that it isn't currently observed. Items (i) and the
tied utility sub-item stay open until the primary capture (vi) supplies a real target
population; everything else above (ii–v) is usable now.

**Status: not yet cross-model reviewed.** Draft 7's design (§§2–7g structure) went through 6
rounds of review before being cleared for control-scaffolding; this numeric content is new as
of 2026-07-15 and has not been through that process. Per CLAUDE.md §6, treat these bars as a
proposal, not frozen, until reviewed — do not fit a real threshold or run the decisive endpoint
against them yet.

### (d) Cardiac/RSA confound — unchanged (round-4: RESOLVED, both arms implementable)

### (e) Latency — unchanged, confirmed correct by round-4 review (45 s from first considered
hop, ~75 s from acquisition, optimistic floor); scoring span is 10 intervals/11 observations,
full attempt is 16 (§2)

### (f) Intervention point — unchanged: post-AHET veto only, retain-or-NaN

### (g) SCORE threshold selection — three-stage filter, corrected (resolves round-5 findings 3, 4, 5, 6)

**Bug found and fixed**: draft 5 grouped the M0 harmonic-positive gate together with the
safety constraints as one "hard feasibility" bucket. But the pass-all sentinel (vetoes
nothing) can never correctly classify an M0 harmonic-positive case as harmonic — it classifies
NOTHING as harmonic. So bucketing M0 with feasibility meant the sentinel was, by construction,
always "infeasible," which defeats the entire reason for adding it: to represent a safe,
feasible no-op distinct from "no threshold exists at all." The three-way outcome was
internally inconsistent.

**Fix — three explicit, ordered stages, evaluated in this order:**

1. **Safety-feasible filter** (the sentinel must be able to pass this — and, by not vetoing
   anything, trivially does not increase false rejections, does not exceed the false-harmonic
   bound, does not reduce yield or raise the NaN rate, and leverage/persistence accounting is
   about window eligibility, not the threshold value): cardiac retention (§7c), the
   surrogate-cardiac false-harmonic bound (§4/§7d), retained-yield/additional-NaN bounds
   (§7c), and valid leverage/persistence accounting (§3/§3b).
2. **Utility/mechanism filter**, applied only to thresholds that passed stage 1: correctly
   classifies every required M0 harmonic-positive case as harmonic, AND clears the
   declared minimum harmful-example-rejection rate (§7c). The pass-all sentinel is
   expected to fail this stage (it vetoes nothing, so it cannot flag M0's positives) — that
   is the intended, correct behavior, not a bug: failing stage 2 while passing stage 1 is
   exactly what "feasible but not useful" means.

   **M0 case roles must be tagged BEFORE execution (round-6 review), not decided post hoc**:
   M0 (§4) is a parameter grid, and not every case in it plays the same role in this gate.
   - **Supported harmonic positives**: every one MUST be classified harmonic for the
     all-or-nothing gate to pass. Never weakened after the fact to accommodate a case that
     happens to fail (e.g. a hard low-SNR grid point) — if a supported case fails, the gate
     fails, full stop.
   - **Deliberate invalid/gap cases** (§3b's zero-gap violations, deliberately included in
     M0): must be REJECTED by the trajectory/persistence contract itself (never scored at
     all), not merely "classified" one way or the other by the harmonic/cardiac models.
   - **Optional stress/out-of-domain cases**: reported diagnostically, explicitly EXCLUDED
     from the all-pass gate — unless a case is deliberately promoted into the supported
     domain in the numeric specification (§7c) before any real run.
3. **Objective + tie-break**, applied only to thresholds that passed BOTH stage 1 and stage
   2: maximize the count of baseline severe accepts (real, exploratory) correctly vetoed to
   NaN; break ties by preferring the smallest SCORE value among tied thresholds (most
   conservative — fewest vetoes under `SCORE <= threshold`, protecting cardiac retention).
   Ties are on the OBJECTIVE, a discrete count, so ties are common even though raw SCORE
   values are continuous; among score-tied thresholds achieving the same count, the smallest
   numeric value is already a full, deterministic order — **no further tie-break is needed or
   used** (draft 5's "then break by numerically smallest raw SCORE" was a redundant no-op on
   top of an already-fully-deterministic rule; removed).

**Two scientific outcomes under valid inputs, plus one non-scientific defensive check
(round-6 review: with valid inputs, "infeasible" is NOT empirically reachable — the sentinel
vetoes nothing, so it necessarily introduces no false rejection, no false-harmonic
classification, no yield loss, no NaN increase, and cannot alter leverage/persistence
eligibility; it always clears stage 1). Keeping a distinct "sentinel failed stage 1" check is
still worthwhile, but only as an internal-invariant/precondition check, never reported as a
scientific "no safe threshold" finding**:

1. **`invalid_preconditions` (defensive only, not a scientific outcome)**: covers two
   distinct non-threshold failure modes that must be reported separately from both outcomes
   below — (a) the sentinel itself somehow fails stage 1 (an internal-invariant violation,
   should not occur with valid inputs — keep as a defensive assertion in implementation, not
   a possible real result); (b) the score population needed to even compute
   `global_min_score` is empty or entirely non-finite (a no-evaluable-data condition, not a
   safety judgment about any threshold).
2. **Feasible but not useful**: at least one threshold (possibly only the sentinel) passes
   stage 1, but nothing passes stage 2 → reported explicitly as feasible-but-not-useful, NOT
   deployed. A safe no-op exists; a useful discriminator does not.
3. **Feasible and useful**: something passes both stages → selected by stage 3's objective and
   tie-break, deployed, threshold locked.

Also, separately: if leverage/exact-track coverage falls below its required floor (§3/§3b),
that session is unscorable at the threshold-independent precondition stage — this is not
evidence the sentinel or any threshold is unsafe, and must not be conflated with outcome 2.

**Candidate threshold set (fix — must include every population any constraint or the
objective depends on, not just real accepted decisions)**: the finite search set is the union
of SCORE breakpoints from **every population used by the objective or by any constraint in
stages 1-3** — real otherwise-eligible ACCEPTED AHET decisions (the objective's own
population), M0's synthetic harmonic-positive/negative cases (stage 2's gate), the synthetic
cardiac/RSA control's scores and the real per-decision cardiac-surrogate scores (stage 1's
false-harmonic bound), and any null-control scores used by a constraint — **plus the pass-all
sentinel**. Omitting any one of these could skip over the exact SCORE value where that
population's own constraint changes from pass to fail. Candidate-level windows AHET already
rejected remain excluded from this set unless they belong to an explicitly synthetic control
population (none currently do).

**Pass-all sentinel, defined precisely (fix)**: strictly below the **global minimum SCORE
across the complete search/control population above** (not just real accepted decisions) —
concretely, `pass_all = nextafter(global_min_score, -infinity)`, where `global_min_score` is
computed over that full population after non-finite SCORE values are rejected and their count
reported. This guarantees the sentinel vetoes nothing, regardless of which population would
otherwise have held the minimum.

No epsilon/tolerance is needed anywhere in this section: the objective is an exact discrete
count, and the candidate threshold set is a finite, exactly-deduplicated collection of
observed breakpoints — there is no continuous search and no floating-point "near-tie" to
resolve.

Selected once, on the 3 exploratory sessions only (§6), locked before the primary
capture, never re-fit against it.

### (h) In-sample threshold-fit vs. primary validation (unchanged — round-4 finding 9:
RESOLVED, correctly labeled)

### (i) Claim boundaries — unchanged (round-4 finding 10: confirmed no prior resolution reopened)

---

## Next

1. **Implement the full control scaffold** — DONE (`scripts/stage1b_lag_statistic.py`,
   verified 2026-07-15: M0 48/48 supported + 6/6 gap cases, threshold-selector self-test PASS).
2. Safeguards from round-6 review — preserved as coded (see script docstring); unchanged.
3. **§7(c) numeric specification — PARTIALLY DONE (2026-07-15)**. Items (ii)-(vi) filled in
   with real derivations from fresh current-HEAD baseline data. Item (i) (the objective bar) and
   its tied §7(g)-stage-2 utility sub-item are BLOCKED — zero baseline severe accepts exist on
   current data — and DEFERRED to the primary capture. **Still open before anything in §7c
   is frozen**: (a) cross-model review of this new numeric content (CLAUDE.md §6 — the §§2-7g
   *structure* was reviewed 6 rounds, this numeric content was not); (b) computing the actual
   non-overlapping-origin independent-attempt count per session for item (iv)'s exact-track
   floor (currently only the overlapping raw count is measured).
4. **Plan the new primary capture** (§6) — now more urgent than "eventually": item (i)
   cannot be unblocked without one, and it should be designed to plausibly still trigger a
   severe accept post-bin-fix (e.g. the `k×f_r ≈ HR` collision), not assume a repeat of the
   existing stepped-rate protocol will produce one.
5. Do **not** compute a real lag-10 SCORE distribution, fit a threshold, or run the decisive
   endpoint evaluation until 3(a) (cross-model review) is done, in addition to 3's numeric work.
