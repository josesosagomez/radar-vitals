# M4 — Offline evaluation harness (HR + BR): implementation plan

> **M0 SUPERSESSION NOTICE — 2026-08-09.** This 2026-07-26 plan transcribes the former
> two-arm/two-role analysis contract. `notes/analysis_prespec.md` and `notes/protocol.md` now
> govern. The current scorer/manifest is **not compliant with the M0 recovery contract** until a
> later, separately reviewed implementation milestone adds: the `recovery` arm; immutable
> `development` / `representation_validation` / `final_evaluation` roles; the recovery settle
> exemption; Stage-1/Stage-2 recovery fields and exact constant-baseline formulas; final-only
> scoring and label-state guards; the source-window and scoring-ledger keys; and retirement of the
> prospective low-warmup-confidence retry. This notice authorizes no code, capture, scoring, or M1+
> work.
>
> **Status: REVISION 6, under cross-model review** (`plans/m4_plan_cross_review.md`).
> Revision 6 repairs the §4 manifest table: revision 5 inserted the executable contract mid-table,
> which terminated it and orphaned the Timebase / Integrity / Provenance / Disposition rows. The
> manifest is now one contiguous six-group table and the contract lives in **§4.1** (M4R-15).
> Revision 5 deleted the retracted Monte-Carlo **value itself** from §2.3 — restating it inside a
> retraction note still published an unsupported number (M4R-13) — and made the design-field
> validation **executable**: `distance_m` finite and `0.8 ≤ d ≤ 1.4` inclusive, `posture == seated`,
> with equality boundaries pinned in stage 1 (M4R-15).
> Revision 4 added the frozen evidence-floor/precision **consequences** (M4R-12), removed the
> untraceable claim from the binding comparators (M4R-13), put **Stage 0 into the done-when**
> (M4R-14), and added distance + posture to the manifest (M4R-15).
> Prior status line, retained for history:
> Revision 1 drew 9 Blocking findings; revision 2 resolved M4R-01/03/08/09 and drew four PARTIALs
> plus **M4R-10 (Blocking)** and **M4R-11**. **All 11 findings agreed, none disputed.**
> Revision 3 adds: the §5.1 shared-callable refactor (the equality test in revision 2 was circular),
> the complete §6.4 equations including the previously-undefined `SSB`, the §6.5 intra-category
> priority, testable §6.6 definitions, the rebuilt §7.1 fixture 2, and §2.4's usable-sample rule.
> Written 2026-07-26. Both M4 gates (M3, linalg review) are cleared. **No M4 code exists yet.**

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

*(New, per M4R-09; evidence corrected per M4R-13.)* Both comparators gate on `p90 − p10` and the LoA
CI uses endpoint percentiles, but no frozen text names the quantile method. With integer-valued
samples over 24–30, `p10`/`p90` usually fall *between* order statistics, so the rule alone can decide
the verdict.

**Self-contained example** — a 30-sample window of `3 × 71`, `23 × 72`, `4 × 77` bpm:
`linear` 5.100, `lower` 6.000, `midpoint` 5.500 → **exclude**; `higher` 5.000, `nearest` 5.000 →
**admit**. Same window, opposite admissibility. Recorded in full in `notes/comparator_prespec.md`
§2.2.

*(An earlier Monte-Carlo frequency claim — asserting how often the method alone decides the verdict —
appeared in revision 2 and briefly in the binding comparators. **Removed, value and all**: it traced
to no committed script and depended on an unstated assumed PR distribution, so it was neither
regenerable nor auditable, which CLAUDE.md §3.1 forbids outright in documents bound for the public M0
deposit. Restating the figure even inside a retraction note would keep publishing an unsupported
number, so it is not repeated here. The deterministic example above establishes the ambiguity, and
the `linear` decision never rested on a frequency.)*

**Resolution: `method="linear"`** — NumPy's default, and what the existing design-evidence scripts
already used, so it minimises retro-inconsistency. Adopted as an **explicit pre-deposit
clarification**, same class as the M3R-40 half-open harmonisation. **Named explicitly and passed
explicitly at every call site** — never left to a library default — in stationarity gates,
sensitivity tables and bootstrap CI endpoints. A boundary fixture on which ≥ 2 standard methods
differ is mandatory (§6.2).

### 2.4 The usable HR reference sample — **finite PR ∧ finite PI ∧ PI ≥ 0.5** (user, 2026-07-26)

*(New, per M4R-11.)* The HR comparator said "PI-gated PR samples" and "≥ 80 % … surviving the PI
gate", which did not settle whether a row with acceptable PI but **non-finite `pr_bpm`** counts
toward the 24. Reachable: `src/masimo.py` parses with `pd.to_numeric(..., errors="coerce")`.

**Resolved:** a row is usable **iff** `pr_bpm` finite **∧** `pi` finite **∧** `pi ≥ 0.5`. **One set
for median, stationarity quantiles and coverage** — exactly one denominator. Finite-PR and
PI-qualified counts are reported **separately** per window, so "missing data" and "low perfusion" stay
distinguishable in the ledger. Makes HR symmetric with BR's explicit finite-RRp counting.

**Effect on existing data: none** — all three CSVs have zero non-finite PR/PI/RR and zero PI < 0.5
(n = 247 / 272 / 574). Recorded as a pre-deposit clarification in `notes/comparator_prespec.md` §2.1.

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
| **Design / descriptive** | **`distance_m`** and **`posture`** (M4R-15), under the executable contract in §4.1. Distance is a required descriptive breakdown from M5 onward; posture is fixed *seated* by the estimand, so the manifest must let M4 **verify** design membership rather than assume it. Distance metadata accompanies every applicable per-subject result. **No post-hoc distance strata and no inferential per-distance claim.** Their absence is why the 4 exploratory captures cannot support distance reporting (`posture=None`, `distance_cm=None`). |
| **Timebase** | `frame0_epoch` (synchronised PC UTC at receipt of frame 0), start **and** end PC↔phone clock offsets (§6: NTP-synced, max ±1 s, re-checked at session end; offset > ±1 s ⇒ resync and restart; **no offset may be chosen by optimising radar–reference agreement**) |
| **Integrity** | raw checksum, truncation bytes, packet-loss statistics, **per-frame validity / zero-fill map** |
| **Provenance** | capture config, capture-time git commit, Masimo CSV path + hash, commanded-rate schedule |
| **Disposition** | intended duration vs early stop, retry / replacement status and reason (§6) |

### 4.1 Executable validation contract for the design fields

*(M4R-15 — "scoring mode validates allowed values and ranges" is not a contract; two implementations
could accept different sessions while both claiming to follow this plan.)*

| field | canonical form | scoring-mode rule |
|---|---|---|
| **`distance_m`** | float, **metres** — one canonical name and unit, matching `protocol.subject_distance_m` in `scripts/live_demo_config.yaml` | must be **finite** and **`0.8 ≤ distance_m ≤ 1.4`** (`notes/protocol.md`: "must be within 0.8–1.4 m"). **Inclusive at both ends.** Reject NaN/inf/missing. The legacy `run_metadata.json` field is `distance_cm` — conversion is explicit and lossless, never implicit |
| **`posture`** | the canonical string **`seated`** | must equal `seated`; any other value or a missing field is rejected in scoring mode, because the estimand fixes posture and a differing session is not a member of this design |

Stage 1 pins the **equality boundaries** (0.8 and 1.4 accepted; 0.79 and 1.41 rejected) and the
non-finite/missing cases.

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
src/window_pipeline.py NEW (STAGE 0 REFACTOR — see below). The one window-level composition
                       callable, extracted from live_demo.py's private `_run_dsp`.
src/warmup_select.py   NEW (STAGE 0 REFACTOR). The one warmup/bin-selection callable, extracted
                       from live_demo.py's private `_run_warmup_selection`.
src/vitals.py, src/respiration.py  REUSE — the primitives, called only via the two callables above.
src/compare.py         SUPERSEDED, not imported. Implements the old PI-gated-*mean* comparator.
scripts/plot_bland_altman.py  PLOTTING ONLY. Its statistics are invalid for repeated measures.
```

**Determinism:** no RNG except the cluster bootstrap at its frozen seed. Same manifest + config +
seed → byte-identical output.

### 5.1 Stage 0 — the shared-callable refactor (M4R-10), a prerequisite of everything else

Revision 2 claimed M4 and the live path "use the same DSP". They would not have. The window-level
composition (`_run_dsp`) and warmup policy (`_run_warmup_selection`) are **private functions in
`scripts/live_demo.py`** — `tests/test_live_demo_warmup_helpers.py:22` imports the latter from there.
Reusing only `src/vitals.py` and `src/respiration.py` still forces M4 to duplicate the config wiring,
respiration fusion/validity semantics, ECA inputs, fallback handling and warmup policy.

**The consequence that condemns revision 2's design:** its "direct shared-DSP equality" test would
have compared M4 against *whichever duplicate the test author chose*. Two copies that silently drift
apart **both pass**. The assertion was load-bearing and hollow simultaneously.

**Before any M4 code:**
1. Extract `_run_dsp`'s orchestration into a **pure `src/window_pipeline.py` callable**; extract
   `_run_warmup_selection` into **`src/warmup_select.py`**. `scripts/live_demo.py` and M4 both
   **import** them — neither reimplements.
2. M4 owns **only** raw slicing, validity dispositions and evidence persistence *around* that call.
3. Define a **normalised estimator adapter / result record carrying estimator ID + config hash**, so
   M8/M9/M10 estimators enter the same grid and scoring path without copying the comparator.
   Revision 2 hard-wired the harness to ECA+AHET, contradicting §1's "every later milestone reports
   through it".
4. The stage-3 equality test calls **that one production callable** — not a duplicate.

**The extraction is a behaviour-preserving refactor of reviewed DSP code and takes its own
CLAUDE.md §6 correctness review**, with the existing 1056-test suite as the regression guard.

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

Per **arm**, over subjects `s` with `n_s` evaluable windows, `N_a = Σ_s n_s`, `S_a` contributing
subjects (≥ 1 evaluable window). **Every equation is restated verbatim rather than pointed at** —
M4R-02 was a transcription error at exactly this boundary, so a source pointer is not sufficient.

**Two different means appear here and they are not interchangeable** (M4R-02 — revision 2 omitted
`SSB` entirely, which would have led an implementer to reuse the subject-weighted mean and silently
change `σ²_b` on every unequal-`n_s` arm):

- `d̄_s` = subject `s` mean difference
- **`d̄_grand = (Σ_s Σ_k d_sk)/N_a`** — **window-weighted**, used **only** inside `SSB`
- **`d̄ = mean_s(d̄_s)`** — **subject-weighted**, and this is the reported **bias**

Variance components:
- `SSW = Σ_s Σ_k (d_sk − d̄_s)²`; `MSW = SSW/(N_a − S_a)`; **`σ²_w = MSW`**
- **`SSB = Σ_s n_s (d̄_s − d̄_grand)²`**; `MSB = SSB/(S_a − 1)`
- **`n0 = (N_a − Σ_s n_s²/N_a)/(S_a − 1)`**
- **`σ²_b = max((MSB − MSW)/n0, 0)`** (truncated)
- **bias `μ_a = d̄` (subject-weighted)**; **`LoA_a = μ_a ± 1.96·√(σ²_b + σ²_w)`**

Headline accuracy and coverage (`analysis_prespec.md` §3.2, verbatim):
- per-subject `MAE_s = mean_k |d_sk|`; `MSE_s = mean_k d_sk²` (both over subjects with `n_s ≥ 1`)
- **`MAE = mean_{s∈S_a}(MAE_s)`**; **`RMSE = √( mean_{s∈S_a}(MSE_s) )`** — the root of the *mean of
  per-subject MSEs*, not a pooled RMSE
- **`coverage = mean_s(n_s/N_s)` over ALL admitted subjects, including those with `n_s = 0`** — the
  accuracy set and the coverage set are **different subject sets**; `S_a` is reported alongside
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
retained in a **separate diagnostic cross-tab**, never in the primary ledger.

**Deterministic priority WITHIN `reference_failures`** (M4R-05 — the top-level identity fixes the
three-way split but not which subcategory a multi-gate failure lands in; without a fixed order a
window can move between ledger rows depending on implementation order while `final n` stays put):

| vital | priority, first match wins |
|---|---|
| **HR** | 1. missing-finite-PR coverage → 2. PI-induced insufficiency → 3. non-stationarity |
| **BR** | 1. availability → 2. non-stationarity |

**BR has one `excluded_by_availability` category, not availability plus coverage** (revision 2
duplicated its single gate). **HR** reports the underlying finite-PR and PI-qualified counts
alongside the categorical verdict, per §2.4. Windows failing **two and three gates simultaneously**
are tested explicitly.

### 6.6 Required outputs (M4R-06 — gates are not the whole comparator)
- HR stationarity sensitivity at **3 / 5 / 8** bpm; **BR at 2 / 3 / 5** bpm.
- **HR severe-error counts > 5 bpm**; **BR error tails > 2 / > 3 / > 5 bpm** vs RRp and, when paced,
  vs the commanded target.
- **HR evidence-floor rules, stated testably** (M4R-06 — names without definitions are not testable):
  **per-session ≥ 1** evaluable window, **per-subject ≥ 4** evaluable windows across the 2 sessions,
  **study-wide ≥ 8/10** subjects meeting the per-subject floor. **No automatic whole-subject
  exclusion** — a subject below the floor is reported with counts, never deleted; the ≥ 8/10 rule,
  not deletion, carries the consequence. The ≥ 8/10 is **study-wide, not per-arm**.
- **LoA-CI precision disposition:** half-width **≤ 5 bpm**, using the frozen operational definition
  for **asymmetric** CIs, evaluated as the **four-distance** test. Reported without deleting subjects.
- **The frozen CONSEQUENCES, not just the threshold booleans** (M4R-12 — emitting a boolean leaves
  the headline disposition to later analyst judgement, which is exactly what a pre-registration
  exists to prevent). M4 emits the resulting claim status directly:

  | condition | consequence M4 must emit |
  |---|---|
  | an arm has **zero evaluable windows** | that arm → **descriptive-only**; the other arm **remains eligible** (symmetric zero-window handling) |
  | **< 8/10** qualifying subjects | the **study-wide confirmatory HR claim weakens to descriptive** |
  | four-distance **precision miss** for an arm | that arm → **descriptive**, **no population LoA** |
  | **every** primary LoA/CI | carries the accepted **`S_a ≤ 10` anti-conservative under-coverage caveat** as a mandatory label field (M3R-45) |
  | regression-LoA sensitivity | uncertainty from the **same whole-subject bootstrap**, or explicitly **point-only** when that bootstrap is unavailable |

  **No subject data are ever deleted** — these rules change the *claim status*, not the input set.
  Stage 7 tests **each transition**, including the mandatory caveat/label fields.
- Per-arm LoA diagnostics: **proportional-bias / heteroscedasticity, residual skew / QQ,
  within-session lag-1 autocorrelation**, and the **subject-clustered regression-LoA descriptive
  sensitivity** computed as
  **`fitted_bias(mean) ± 1.96·√(σ²_b,reg + σ²_e,reg)`** — **both** components from the **same
  subject-random-intercept model**. A stage-7 fixture **must fail if only the residual SD is used**:
  that is the already-reviewed **M3R-30 defect**, and it produces plausible-looking output.
  (Not optional; only MOVER is out of scope.)
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
| 0 | **Shared-callable refactor** (§5.1) — extract `window_pipeline` + `warmup_select` into `src/`; define the estimator adapter. | `live_demo.py` imports both; the 1056-test suite passes unchanged; the diff has passed its own CLAUDE.md §6 review. **Nothing else starts before this.** |
| 1 | **Manifest schema + validation** (§4) | Scoring mode rejects every missing required field with a named error. **M4 recomputes the objective admission disposition** from the primitive fields — ±1 s clock offset at both ends, checksum/truncation, intended-duration vs abort, packet-loss flag, retry/replacement, validity-map consistency — and **fails loudly if it disagrees with the operator-supplied verdict**, with a named negative test per rule (M4R-04). Development mode is separately labelled and cannot emit scoring output. |
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
2. **Synthetic frame stream with frame-ID sentinels + an injected, call-recording estimator.**
   The estimator **records every slice it is asked for** and the fixture asserts the calls occur
   **exactly** on `[0,600)`, `[600,1200)`, … — **failing if any overlapping or greedy slice is
   requested**. A forced estimator failure and an incomplete tail are folded into this same fixture.
   *(Replaces revision 2's "neighbouring 3 s hop distractors", which was a leftover from the
   discarded CSV architecture — M4R-07. Under the raw pipeline the estimator is only ever called on
   exact slices, so there are no hop rows to distract with: that fixture would have tested nothing
   while appearing to test greedy selection.)*
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
5. **Record `distance_m` (metres, 0.8–1.4 inclusive) and `posture` (`seated`)** (M4R-15) — required
   descriptive metadata from M5 onward, validated against the §4 contract, and not reliably
   reconstructable after the session.

**This should land before M1's smoke test**, so M1 validates the capture path M5/M6 will rely on.

---

## 9. Done-when (implementation-scoped)

1. **Stages 0–7** complete, each unit-tested, with the three golden fixtures passing against
   hand-written expected JSON. *(Stage 0 is included explicitly — M4R-14: revision 3 said "Stages
   1–7", which read literally would let M4 pass its acceptance checklist without the refactor that
   prevents live/offline DSP divergence.)*
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
