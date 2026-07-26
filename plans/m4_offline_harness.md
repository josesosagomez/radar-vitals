# M4 — Offline evaluation harness (HR + BR): implementation plan

> **Status: REVISION 2, under cross-model review** (`plans/m4_plan_cross_review.md`).
> Revision 1 drew **9 Blocking findings (M4R-01…09), all agreed, none disputed** — including two
> architecture errors. This revision rewrites §§2–8 against them. Written 2026-07-26.
> Both M4 gates (M3, linalg review) are cleared. **No M4 code exists yet.**

---

## 1. What M4 is

One tracked entry point that **reprocesses a capture's raw `adc_stream.bin` offline** and turns it
into paper-grade agreement numbers for **both** vitals, under **both** frozen comparators, on the
frozen non-overlapping 30 s grid — logging config, seed, package versions, scoring commit and every
input hash alongside every result (CLAUDE.md §3.1).

M4 is the acceptance harness for every later milestone: M0's CI machinery, M5's pilot, M6's study,
and the M8/M9/M10 method work all report through it.

---

## 2. Decisions that set this plan's shape

### 2.1 The regression anchor — **Option A** (user, 2026-07-26)

The pilot MAEs **0.19 / 0.50 / 0.53 bpm** are **retired, not reproduced**: no committed script
produced them, they used a nearest-hop rule §7 later froze differently, and they predate the
band-pass fix (2026-06-30 `1847d7f` → 2026-07-26). The scorer is validated instead against
**independently specified golden fixtures** (§6.2). Option B was declined — it would have to guess an
undocumented rule *and* resurrect the retired filter, and a reconstruction tuned until it emits 0.19
proves nothing.

### 2.2 The existing captures are **development/demonstration only** — and **M2 #5 stays OPEN**

*(Revised per M4R-03. Revision 1 claimed a "descriptive" run under the frozen comparator closed M2
done-when #5. It does not.)*

`notes/analysis_prespec.md` §7: the 4 existing captures lack a persisted `frame0_epoch`; alignment
reconstructed from `start_wall_utc` is APPROXIMATE and is "used **only** for reference-characterization
design evidence, **never** for a frozen scoring number". **An agreement value computed under an
unknown startup offset is not the frozen-comparator outcome merely because it is labelled
descriptive.**

Two distinct alignment defects, not one:
- **Original captures:** `start_wall_utc` is written before DCA/IWR configuration
  (`live_demo.py:1213` vs `1300+`), so the offset is capture-startup latency — approximate.
- **Replay folders:** their `start_wall_utc` is the *replay launch* (`2026-07-26T14:36:54`) versus the
  capture's `2026-07-13T15:20:03` — **13 days out**. Not an approximation at all.

**Therefore:**
- M4's run on the existing captures is **development/demonstration only**, labelled **exploratory and
  apparent/in-sample**. Never "under the frozen comparator".
- **M2 done-when #5 remains OPEN** (user decision, 2026-07-26; Codex's reviewer recommendation
  concurred). It is **not** superseded — "superseded" would imply the obligation was discharged, and
  it was not. It closes when a capture carrying a persisted `frame0_epoch` exists and is scored.
- M4's own done-when is **implementation-scoped** (§9) and does not depend on closing M2 #5.

### 2.3 Percentile convention — **`linear`** (user, 2026-07-26)

*(New, per M4R-09.)* Both comparators gate on `p90 − p10` and the LoA CI uses endpoint percentiles,
but no frozen text names the quantile method. Measured over the admissible regime (n ∈ [24, 30]
integer-valued PR samples, 4000 trials, 9 NumPy methods):

| gate | windows where the method alone decides the verdict |
|---|---|
| **HR, 5.0 bpm** | **1993 / 4000 (≈ 50 %)** |
| BR, 2.0 bpm | 8 / 4000 |

Example (n = 28): `higher`/`nearest` → 5.000 (**admit**); `linear` 5.300, `midpoint` 5.500,
`hazen` 5.700, `median_unbiased` 5.833, `lower`/`averaged_inverted_cdf` 6.000, `weibull` 6.100
(**exclude**). Integer PR over ~28 samples puts p90/p10 between order statistics almost every time.

**Resolution: `method="linear"`** — NumPy's default, and what the existing design-evidence scripts
already used, so it minimises retro-inconsistency. Adopted as an **explicit pre-deposit
clarification**, same class as the M3R-40 half-open harmonisation. **Named explicitly and passed
explicitly at every call site** — never left to a library default — in stationarity gates,
sensitivity tables and bootstrap CI endpoints. A boundary fixture on which ≥ 2 standard methods
differ is mandatory (§6.2).

---

## 3. Inputs — raw ADC, bound by a manifest

*(Rewritten per M4R-01 and M4R-04. Revision 1 made `live_estimates.csv` the radar input, which
violates CLAUDE.md §4.)*

**The radar input is the original capture folder's `adc_stream.bin`.** CLAUDE.md §4: *"No HR/BR value
shown by `scripts/live_demo.py` (or its `live_estimates.csv`) is paper-grade… Paper metrics are
computed offline by re-processing the run's saved raw `adc_stream.bin`."* `implementation_plan.md`
§M4: the entry point *"reprocesses `adc_stream.bin` offline"*.

`live_estimates.csv` is a **non-authoritative diagnostic cross-check only**. It rounds HR/BR to 2 dp,
omits a row entirely when a DSP call raises, and cannot distinguish a missing estimate from a corrupt
artifact. The 2026-07-26 replay folders contain **no `adc_stream.bin`** and are reframed as **evidence
for the `600k + 599` row mapping only** (independently verified by both models: `ReplayFrameSource`
emits zero-based indices, the first 600-frame buffer emits at 599, stored runs contain exactly
6 / 6 / 16 rows `599, 1199, …`).

| use | never use | why |
|---|---|---|
| `adc_stream.bin` from the original capture | any replay folder's outputs | CLAUDE.md §4; replay folders have no ADC and a 13-day-wrong origin |
| manifest `frame0_epoch` | `start_wall_utc` | written pre-configuration; not a frame-0 timestamp |
| Masimo integer `Timestamp`/`epoch_utc` | `Date`/`Time` strings | CLAUDE.md §9 |
| `Beats / min` (PR), `Breaths / min` (RRp) | SpO2 / PI / PVi as a rate | CLAUDE.md §9 |

---

## 4. The M4 study/session manifest (versioned, validated before implementation)

*(New, per M4R-04. A run folder plus two fields cannot form the frozen estimand sets: the inspected
replay metadata has `session_id="unknown"`, `posture=None`, `distance=None`, and nothing binds a run
to a Masimo file, subject, arm or data role.)*

A versioned manifest binds, **by path + SHA-256**, for each session:

| group | fields |
|---|---|
| **Identity / estimands** | subject ID, arm (natural / paced), commanded paced rate (12/15/18), data role (§3.1), study admission disposition |
| **Timebase** | `frame0_epoch` (synchronised PC UTC at receipt of frame 0), start **and** end PC↔phone clock offsets (§6: NTP-synced, max ±1 s, re-checked at session end; offset > ±1 s ⇒ resync and restart; **no offset may be chosen by optimising radar–reference agreement**) |
| **Integrity** | raw checksum, truncation bytes, packet-loss statistics, **per-frame validity / zero-fill map** |
| **Provenance** | capture config, capture-time git commit, Masimo CSV path + hash, commanded-rate schedule |
| **Disposition** | intended duration vs early stop, retry / replacement status and reason (§6) |

**Frozen-scoring mode fails loudly on any missing required field.** A separate **development mode**
exists for the 4 existing captures and is made **impossible to mistake for scoring output** (distinct
output directory, `mode: DEVELOPMENT` in every artifact, and the words *exploratory / apparent /
in-sample* on every emitted table).

---

## 5. Architecture

```
src/m4/manifest.py     NEW. Manifest schema, validation, hashing. Fails loudly in scoring mode.
src/m4/reprocess.py    NEW. Raw-ADC exact-grid reprocessing (§6.1):
                         decode adc_stream.bin -> complete 600-frame slices
                         warmup/bin-lock semantics applied to k=0
                         one shared full-precision DSP call per slice
                         DSP failure / invalid / zero-filled slice -> RECORDED radar-NaN disposition
                         persists per-window intermediates
src/m4/scoring.py      NEW. Pure, no I/O. Subject/arm structures (NOT generic pair pools):
                         window_grid, reference windows, HR/BR gates, disposition hierarchy,
                         subject-weighted MAE/RMSE/bias/coverage, ANOVA LoA, cluster bootstrap
scripts/run_m4_harness.py  NEW. Entry point: manifest -> results/<experiment>/<timestamp>/
src/masimo.py          REUSE unchanged.
src/vitals.py, src/respiration.py  REUSE — the SAME DSP the live path calls. M4 must not fork it.
src/compare.py         SUPERSEDED, not imported. Implements the old PI-gated-*mean* comparator.
scripts/plot_bland_altman.py  PLOTTING ONLY. Its statistics are invalid for repeated measures.
```

**Determinism:** no RNG except the cluster bootstrap at its frozen seed. Same manifest + config +
seed → byte-identical output.

---

## 6. The rules, restated from the frozen specs

Transcribed, not invented. Any disagreement with the source is a bug in this plan and the source wins.

### 6.1 Window grid (`analysis_prespec.md` §7, FROZEN)
- 30 s = **600 frames** at 20 Hz; half-open frame intervals `[k·600, (k+1)·600)`.
- **`k = 0` IS scored** — warmup bin-selection runs on the `k=0` buffer and is applied back to it.
  M4 reproduces this directly from raw rather than inheriting it from a CSV; the all-DSP-failed `k=0`
  case becomes a **recorded radar-NaN disposition**, not a missing row.
- A window is scored **iff** it is a complete 600 frames. Incomplete tail dropped.
- Reference span for window `k` is `[E(k·600), E((k+1)·600))`, `E(i) = frame0_epoch + i/20`; a sample
  at integer epoch `e` belongs iff `E(k·600) ≤ e < E((k+1)·600)`. **HR and BR share this half-open
  rule** (M3R-40). `frame0_epoch` may be **fractional** — endpoint inclusion must be tested for it.
- Yields: 180 s → 6; 480 s → 16; 600 s → exactly 20.
- **Boundary-aligned, never greedy** — greedy selection of accepted hops is forbidden.

### 6.2 HR comparator (`comparator_prespec.md`)
Reference = **median of PI-gated PR** (`PI ≥ 0.5`). Gates, all required: PI; **coverage ≥ 80 %** of
30 expected samples surviving the PI gate; **stationarity** `p90 − p10 > 5.0 bpm` ⇒ exclude.

### 6.3 BR comparator (`comparator_prespec_br.md`)
Reference = **median of finite RRp**. Gates, all required: **availability ≥ 24 finite `rr_bpm`
samples** (one gate, not two — revision 1 duplicated it); **stationarity** `p90 − p10 > 2.0 bpm` ⇒
exclude. **PI is NOT a BR gate** — reported as a per-window flag / sensitivity covariate only.
**Paced:** report agreement against **both** RRp *and* the commanded metronome, clearly labelled;
the metronome is **target-concordance, not truth**. **Natural:** RRp only; the words are "compared
with", never "validated against".

### 6.4 Statistics — the frozen variance-components model (`analysis_prespec.md` §1)
*(Rewritten per M4R-02. Revision 1 said "μ ± 1.96·SD point estimate is primary" — that HANDOFF
sentence is about not switching to MOVER post-hoc, and is not the estimator.)*

Per **arm**, over subjects `s` with `n_s` windows, `N_a = Σ n_s`, `S_a` subjects:
- `MSW = SSW/(N_a − S_a)` where `SSW = Σ_s Σ_k (d_sk − d̄_s)²`; **`σ²_w = MSW`**
- `MSB = SSB/(S_a − 1)`; **`n0 = (N_a − Σ_s n_s²/N_a)/(S_a − 1)`**;
  **`σ²_b = max((MSB − MSW)/n0, 0)`** (truncated)
- **bias `μ_a = d̄` subject-weighted**; **`LoA_a = μ_a ± 1.96·√(σ²_b + σ²_w)`**
- **Estimable iff `S_a ≥ 2` AND `N_a > S_a`.** If either fails, the arm reports **descriptive-only:
  observed bias and observed SD, with the population LoA *and* its CI both suppressed.** *(At one
  subject this suppresses the point estimate too — revision 1 wrongly kept it.)*
- **CI:** whole-subject nonparametric cluster bootstrap; resample `S_a` subjects with replacement
  (a subject drawn twice enters as two distinct clusters with all its windows); recompute the **exact**
  estimator each replicate; **B = 10 000**; **seed = 20260725**; two-sided **95 %** percentile per
  endpoint (`method="linear"`, §2.3); non-estimable replicates excluded; **> 5 % failures ⇒ demote
  the arm to descriptive-only**.
- **Pooling (§3.2, binding):** MAE/RMSE/coverage are **arm-specific and subject-weighted** (each
  subject equal regardless of `n_s`); accuracy and coverage have **distinct subject sets**;
  **HR excludes the 18 bpm subjects from the inferential paced arm, BR includes them**; per-rate
  breakdowns are descriptive-only; **no combined natural+paced headline.**

### 6.5 Disposition hierarchy — one partition (M4R-05)
Revision 1 said radar-NaN "is not an exclusion" while requiring the ledger to sum. Both cannot hold.

**Primary, mutually exclusive, per `analysis_prespec.md` §6 (reference failure precedes radar-NaN):**

```
total_complete_windows = reference_failures
                       + reference_admissible_radar_nan
                       + evaluable
```
Reference gates are applied **first**; radar-NaN is the disposition **only** for a
reference-admissible window. Overlapping views (e.g. inadmissible-reference **and** radar-NaN) are
retained in a **separate diagnostic cross-tab**, never in the primary ledger. **BR:** one
`excluded_by_availability`. **HR:** a deterministic split between missing-finite-PR coverage and
PI-induced insufficiency, with underlying sample counts also reported. Every cross-product tested.

### 6.6 Required outputs (M4R-06 — gates are not the whole comparator)
- HR stationarity sensitivity at **3 / 5 / 8** bpm; **BR at 2 / 3 / 5** bpm.
- **HR severe-error counts > 5 bpm**; **BR error tails > 2 / > 3 / > 5 bpm** vs RRp and, when paced,
  vs the commanded target.
- HR **evidence-floor** and **LoA-CI precision** dispositions — reported **without deleting otherwise
  usable subjects**.
- Per-arm LoA diagnostics: **proportional-bias / heteroscedasticity, residual skew / QQ,
  within-session lag-1 autocorrelation**, and the **subject-clustered regression-LoA descriptive
  sensitivity** (not optional; only MOVER is out of scope).
- Coverage alongside accuracy, always; per-session and subject-weighted coverage from the **frozen
  denominator** (§3.2).
- Data-role labels (§3.1) enforced on every table.

### 6.7 Provenance (M4R-08)
The reproducibility boundary is the **M4 scoring tree**, not the capture's commit. Confirmed
`git_dirty = true` on both the replay (`5537df51`) and original-capture (`403c245f`) metadata, so a
bare commit + dirty flag identifies nothing.

- **Frozen-scoring mode requires a clean committed M4 worktree** (or an exactly reconstructable
  source bundle). Dirty runs are labelled non-scoring.
- **Scoring commit recorded separately from source-capture commit.**
- Hash and list **every** consumed file: ADC, Masimo CSV, session/study manifest, capture config,
  scoring config, metadata, validity map, target schedule.
- Log package/environment versions and the frozen bootstrap seed.
- **Never reject an older raw capture on its capture-time commit** — reprocessing old raw ADC with
  the current scorer is M4's purpose. Reject only incompatible/missing schemas or unreproducible
  scoring code. *(This overturns revision 1's proposal to refuse pre-filter-fix folders.)*

---

## 7. Build order — core and tests before any capture is scored (CLAUDE.md §5.3)

| # | Stage | Done when |
|---|---|---|
| 1 | **Manifest schema + validation** (§4) | Scoring mode rejects every missing required field with a named error; development mode is separately labelled and cannot emit scoring output. |
| 2 | **Window grid.** `[k·600,(k+1)·600)`, fractional-`frame0_epoch` endpoint inclusion, incomplete-tail rejection. | Yields 6/6/16/20 by hand; both boundary seconds land in exactly one window under a fractional origin. |
| 3 | **Raw-ADC exact-grid reprocessing** (§6.1). | M4's per-window DSP output **equals a direct shared-DSP call on the same saved 600-frame slice at full precision**; a forced DSP failure yields a *recorded* radar-NaN, not a missing window; `k=0` reproduces the warmup semantics. |
| 4 | **Reference aggregation + gates**, on **raw-format** Masimo CSVs (not clean DataFrames). | Every gate fires exactly at its boundary (PI 0.5, coverage 24/30, 5.0 / 2.0 bpm) tested **at equality**; `linear` named explicitly; the ≥2-method boundary fixture pins it. |
| 5 | **Disposition ledger** (§6.5). | The partition identity holds on every cross-product, including inadmissible-reference **and** radar-NaN. |
| 6 | **Subject/arm statistics** (§6.4). | Hand-computed `MSW`, `MSB`, `n0`, both variance components and LoA on an **unequal-window multi-subject** fixture; subject-weighted ≠ pooled demonstrated; `S_a<2` or `N_a≤S_a` emits descriptive-only with **both** LoA and CI suppressed; bootstrap honours B/seed and the >5 % demotion rule. |
| 7 | **Required outputs** (§6.6) + provenance (§6.7). | Every named output present in the schema; scoring mode refuses a dirty worktree; every consumed file hashed. |
| 8 | **End-to-end smoke on the 3 captures.** | Runs in **development mode**, labelled exploratory / apparent / in-sample. **Not** a numeric oracle, **not** under the frozen comparator, does **not** close M2 #5. |

### 7.1 Golden fixtures — the load-bearing validation (M4R-07)

Revision 1's hand-computable DataFrames were necessary but not sufficient: a bug shared between
fixture and implementation, or living **outside** the pure functions, passes them. Named failure
modes: Masimo schema/column handling, duplicate-epoch merging, finite-value counting,
fractional-origin endpoint inclusion, exact-grid selection, run↔reference binding, subject weighting,
arm pooling, disposition precedence.

**Three checked-in golden fixtures, with expected-result JSON written BY HAND — never generated by
scorer code** (this constraint is what defeats the shared-bug problem):

1. **Raw-format Masimo CSV** with a duplicate epoch, a missing second, NaN PR/RRp, PI **below and
   exactly at** 0.5, skewed values, and samples exactly on **both** boundaries under a **fractional**
   `frame0_epoch`.
2. **Exact-grid estimator stub** whose 600-frame windows carry sentinel outputs while **neighbouring
   3 s hop positions carry deliberate distractors**, plus a failed estimate and an incomplete tail.
3. **Unequal-count multi-subject natural/paced fixture** with a zero-evaluable subject, an 18 bpm
   subject and cross-failure windows, with hand-derived subject-weighted metrics, ledger, ANOVA LoA
   and pooling answers.

The three real captures are an **end-to-end smoke test only, never the numeric oracle.**

---

## 8. Forward requirement for scorable captures (M1 / M5)

No capture can yield a frozen number until the **full §4 manifest** can be populated — not merely
`frame0_epoch` and a validity map (revision 1 understated this). Minimum new capture-path work:

1. **Persist `frame0_epoch`** — synchronised PC UTC at receipt of frame 0. Not `start_wall_utc`.
2. **Persist a per-frame validity / zero-fill map** — aggregate `n_dropped` cannot identify *which*
   windows are affected. A window containing any dropped/zero-filled frame ⇒ radar-NaN.
3. **Log start and end PC↔phone clock offsets** (NTP, ±1 s, re-check for drift).
4. **Record subject, arm, commanded rate, data role, admission/retry disposition** at capture time.

**This should land before M1's smoke test**, so M1 validates the capture path M5/M6 will rely on.

---

## 9. Done-when (implementation-scoped)

1. Stages 1–7 complete, each unit-tested, with the three golden fixtures passing against hand-written
   expected JSON.
2. Raw-reprocessing equality against a direct shared-DSP call demonstrated at full precision.
3. An end-to-end **development-mode** run on the 3 Masimo captures, emitting HR and BR labelled
   exploratory / apparent / in-sample.
4. Scoring mode demonstrably **refuses** an incomplete manifest and a dirty worktree.

**Explicitly NOT in the done-when:** reproducing 0.19/0.50/0.53 (§2.1), and closing **M2 done-when
#5**, which stays **open** until a capture with a persisted `frame0_epoch` exists (§2.2).

---

## 10. Out of scope

- MOVER CI (M3R-46 — needs statistician review). The **regression-LoA sensitivity is in scope**.
- Per-distance agreement claims (not recorded in these captures; forbidden without amendment).
- Promoting `guard_cardiac_candidate_v1` (blocked on `experiments/exp_eca_modes`).
- Re-measuring the "34 % of hops" paced-16 decoy figure — still open, not M4.
- Fixing `elapsed_s` in `live_demo.py`. M4 is **immune** by using `frame_idx` throughout.
