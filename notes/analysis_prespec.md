# Analysis pre-specification — mmWave vital-signs study

> **Status: DRAFT for the M0 deposit, prepared 2026-07-25.** This freezes the analysis decisions
> that the study cannot be re-run to fix (`plans/implementation_plan.md` §M0). It is **binding once
> deposited**; changes after freeze go through the amendment mechanism (§4). Companion frozen
> documents in the same deposit: `notes/comparator_prespec.md` (HR scoring),
> `notes/comparator_prespec_br.md` (BR scoring — **drafted 2026-07-25 (M3), awaiting cross-review**),
> `notes/protocol.md` (capture protocol), `notes/capture_inventory.md`.
>
> **Open items that MUST close before freeze** are marked **[OPEN]**. A deposit with any [OPEN]
> item is not frozen.

---

## 1. Agreement model

Radar–reference differences `d = (radar − reference)` are analysed **per vital sign** (HR, BR) with
a **subject-clustered limits-of-agreement model**. The design is **10 subjects × 2 sessions, and
the two sessions are the two fixed conditions** (session 1 = natural, session 2 = paced), so
**"session" and "arm/condition" coincide** — there is exactly one session per arm per subject.
Windows within a session are the within-subject replicates; they are **not** independent pairs.

**Estimand and model (arm-specific, PRIMARY).** For arm `a ∈ {natural, paced}` and difference
`d_ik` (subject `i`, window `k`):

  `d_ik = μ_a + b_i + e_ik`, with `b_i ~ N(0, σ²_b)` (subject random intercept),
  `e_ik ~ N(0, σ²_w)` (within-subject-arm window residual),

fitted by a linear mixed / variance-components model (Carstensen, Simpson & Gurrin 2008).
**Arm-specific limits of agreement:** `LoA_a = μ_a ± 1.96·√(σ²_b + σ²_w)`. Bias `μ_a` and LoA are
reported **separately for each arm**, because natural and paced are non-exchangeable measurement
regimes, not exchangeable random sessions.

**No session-within-subject variance component.** With exactly one session per arm per subject, a
between-session-within-subject term is **not identifiable** — it is confounded with the arm mean.
The earlier draft's `σ²_s` is therefore dropped; the model has **two** levels only: subject
(`σ²_b`) and within-subject-arm window residual (`σ²_w`).

**No single combined LoA (M3R-27).** Because arm and paced-rate are fixed effects, a pooled
"marginal" LoA would surround a covariate-specific mean whose value depends on arbitrary
centering/weights — not one well-defined estimand — and §3.2 already forbids a combined headline.
So **only arm-conditional** (and, within paced, **commanded-rate-conditional**) limits are reported;
there is **no** single natural+paced LoA.

**Variance components — closed-form unbalanced one-way ANOVA (M3R-23).** For arm `a`, let its
**contributing subjects** be the `S_a` subjects with ≥ 1 evaluable window, subject `s` having `n_s`
windows and `N_a = Σ_s n_s`. With `d̄_s` = subject `s` mean difference and `d̄ = mean_s(d̄_s)`
(**subject-weighted**, matching §3.2):
- `SSW = Σ_s Σ_k (d_sk − d̄_s)²`, `MSW = SSW/(N_a − S_a)`, so **`σ²_w = MSW`**;
- `SSB = Σ_s n_s (d̄_s − d̄_grand)²` with `d̄_grand = (Σ_s Σ_k d_sk)/N_a`,
  `MSB = SSB/(S_a − 1)`, and the **effective group-size coefficient**
  `n0 = (N_a − Σ_s n_s²/N_a)/(S_a − 1)`, so **`σ²_b = max( (MSB − MSW)/n0 , 0 )`** (truncated).
- **Bias** `μ_a = d̄` (subject-weighted); **`LoA_a = μ_a ± 1.96·√(σ²_b + σ²_w)`.**

This is closed-form (no convergence failure); a REML fit is only an optional cross-check. It handles
**unequal `n_s`** via the `n0` coefficient — *this* replaces the earlier "mixed model handles unequal
counts" wording, which no longer applies now that the primary is method-of-moments.

**Estimability conditions and their prospective disposition.** The population LoA for arm `a` is
estimable **iff `S_a ≥ 2`** (needs a between-subject contrast) **and `N_a > S_a`** (at least one
subject with ≥ 2 windows, so `MSW` has ≥ 1 df). **If either fails, that arm reports
descriptive-only** — observed bias and observed SD of the pooled differences, **no population LoA
and no CI** — declared as a limitation. This is a prospective, count-based rule (agreement-blind).

**CI on the LoA — frozen recipe.** Primary = **subject-level nonparametric cluster bootstrap over
the `S_a` contributing subjects** (not a hard-coded 10): resample `S_a` subjects with replacement (a
subject drawn twice enters as two distinct clusters with all its windows), recompute the **same
closed-form components**, **B = 10 000** replicates, fixed **seed = 20260725**, **two-sided 95 %**
percentile interval (2.5 / 97.5). A replicate whose resample **fails the estimability conditions
above** (e.g. < 2 distinct subjects drawn, or no subject with ≥ 2 windows) is **recorded and
excluded** from the percentile; **if > 5 % of replicates fail, the arm reports descriptive-only** (no
population-LoA CI). The small-sample (`S_a ≤ 10`) limitation is stated. Sensitivity = **MOVER
(Zou 2013), arm-specific, two-sided 95 %**, changing-true-value — a sensitivity, never a rescue.
**Executed by the committed, hash-pinned M4 harness**, which logs package versions, seed, config,
git commit, and input SHA-256 at run (CLAUDE.md §3.1).

**Handling of design features.** Unequal window counts per subject are handled by the unbalanced
one-way ANOVA via the effective group-size coefficient `n0` above (no per-subject pre-averaging).
The pooled `bias ± 1.96·SD` with
`se_loa = sqrt(3·SD²/n)` in `scripts/plot_bland_altman.py` is **descriptive-only** and never quoted
as a limit of agreement.

**Assumption diagnostics — MANDATORY, not conditional (M3R-18).** The constant-`μ ± 1.96·SD` LoA is
the **primary, pre-registered** estimand and is **never** switched post-hoc. For **every arm**,
three items are **always computed and reported** (no "if breached" trigger): (i) proportional-bias /
heteroscedasticity — the slope of difference-vs-mean and residual-spread-vs-magnitude; (ii)
difference-tail normality — residual skew and a QQ summary; (iii) a **serial-correlation summary =
the residual lag-1 autocorrelation** within session (the whole-subject cluster bootstrap already
propagates within-subject dependence into the CI). A **single, fully-specified descriptive
sensitivity** is **always** reported alongside: a **subject-clustered regression-based LoA** —
the difference regressed on the mean with a **subject random intercept** (mixed model, same
clustering as the primary), limits `= fitted bias(mean) ± 1.96·(residual SD)`, labelled descriptive.
**Scope of this sensitivity, stated honestly:** it addresses **proportional bias only**; it uses a
**constant residual SD**, so it does **not** correct heteroscedasticity — a heteroscedastic constant
LoA therefore **remains a declared limitation**, not something this sensitivity removes. Choosing a
transform *as the new primary* after M6 is **forbidden**.

- **References (verified 2026-07-25):**
  - Bland & Altman, *Lancet* 1986; **1**(8476):307–310 — original limits of agreement.
  - Bland & Altman, *J. Biopharm. Stat.* 2007; **17**(4):571–582 — agreement with multiple
    observations per individual (DOI 10.1080/10543400701329422).
  - Carstensen, Simpson & Gurrin, *Int. J. Biostat.* 2008; **4**(1):Art. 16 — mixed-model /
    variance-components agreement with replicate measurements (DOI 10.2202/1557-4679.1107).
  - Zou, *Stat. Methods Med. Res.* 2013; **22**(6):630–642 — MOVER CI for the LoA with multiple
    observations per individual (DOI 10.1177/0962280211402548).
- Requires math/claims cross-review (CLAUDE.md §6) before freeze.

## 2. Evidence floor and precision target

The minimum number of **evaluable** (radar-accepted **and** comparator-admissible,
non-overlapping 30 s) windows the study is powered to report.

**Vital-sign scope (M3R-28).** Option A below is the **HR (primary-endpoint) evidence floor** — it
was derived from the HR AHET-acceptance yield and the HR comparator
(`plans/m0_b1_evidence_floor_memo.md`). **BR is a secondary, exploratory endpoint (§3) and has NO
confirmatory evidence floor**: BR agreement is reported **descriptively wherever BR-evaluable
windows exist, always with its own coverage**, using a BR-evaluable count (radar BR estimate present
**and** `comparator_prespec_br.md`-admissible) that is computed and reported **separately** from the
HR count. The ≤ 5 bpm precision target and the miss rule apply to the **HR** primary endpoint only;
no unspecified joint HR+BR window set is ever used.

### 2a. Option A — as the user chose it (FROZEN, verbatim from `plans/m0_b1_evidence_floor_memo.md` §3)
| item | value |
|---|---|
| per-session floor | ≥ 1 evaluable window |
| per-subject floor | ≥ 4 evaluable windows (across the 2 sessions) |
| precision target | limits-of-agreement CI half-width ≤ 5 bpm (§1 model) |
| **miss rule** | if a session yields < 1 evaluable window, **drop that subject's natural arm to descriptive; report paced only** |

This is exactly the user's 2026-07-24/25 selection — no more.

**Operational definition of "CI half-width ≤ 5 bpm"** (the LoA CIs from §1 can be asymmetric):
the target is met iff **the maximum of the four distances** from each LoA point estimate (upper and
lower) to each of its two CI endpoints is ≤ 5 bpm — evaluated **per arm** on the primary
(bootstrap) CI. The **consequence of missing it** is frozen in §2b.

### 2b. Extensions — FROZEN (user decision 2026-07-25, adopting the cross-review recommendation)
The following complete the HR evidence floor and are now binding:
- **Study-wide floor:** the study-wide confirmatory HR agreement claim requires **≥ 8 of the 10
  subjects** to meet the per-subject floor. If fewer than 8 do, the **study-wide claim weakens to
  descriptive** (bias and observed spread; no population LoA), reported as a limitation.
- **Symmetric zero-window handling:** the §2a miss rule is **symmetric** — **either** arm (natural
  *or* paced) that yields **< 1 evaluable window** has **that arm** reported descriptive-only; the
  subject's other arm still contributes to its arm-specific confirmatory analysis.
- **No automatic whole-subject exclusion.** A subject below the **≥ 4** per-subject floor is **not**
  removed: its **eligible arm data remain in the arm-specific analysis**, flagged **below-floor**
  with counts reported. The **≥ 8/10 study-wide rule** (above) — not deletion of the subject — is
  what governs whether the study-wide confirmatory claim survives. (This keeps a below-floor
  subject's otherwise-usable data in the analysis rather than discarding it on a count.)
- **Precision-miss consequence:** if the ≤ 5 bpm CI-half-width target (§2a) is not met for an arm,
  that arm's **headline weakens to descriptive** (bias and observed spread; no population LoA),
  stated as a limitation — never renegotiated.

### 2c. Reconciliation with §6 (no outcome-based exclusion)
The floor thresholds **evaluable-window count only** — never the agreement value — and is applied
**identically to every subject/session regardless of how well radar and reference agree**. A
session with 0 evaluable windows contributes nothing because it *has* nothing to contribute (it is
empty), not because unfavourable results were deleted. The safeguards: the rule is **prospective,
count-based, and symmetric across arms** (§2b) — no session or arm is moved to "descriptive" on the
basis of its bias/error; **no whole subject is ever excluded** (its eligible arm data stay in, §2b);
and **every descriptive-only or below-floor session/subject/arm is reported with its count and
reason** (§6).

**No add-sessions lever** (Option A narrows the claim rather than recruiting more), so the
"`24IBEC051` permits > 2 sessions/subject?" question (A5(a)) is **moot**.

**Motivation.** The pilot measured **0** evaluable non-overlapping windows in the natural arm
(`plans/m0_b1_evidence_floor_memo.md` §1); Option A makes a contribution-free natural session a
declared, prospective outcome, not a post-hoc rescue.

## 3. Method-comparison discipline (with normative pooling table)

- **Primary estimator:** ECA+AHET, as committed at the freeze commit.
- **Primary endpoint:** HR MAE **reported with coverage**, under `notes/comparator_prespec.md`.
- **Secondary, exploratory:** BR agreement; Harmonic Accumulation (M8); joint-Doppler (M9);
  baselines (M10). Reported descriptively (CIs labelled exploratory, no winner-crowning test).
  Promotion of any secondary to primary requires a documented decision **and an amendment**.
- **Fourth estimator arm — a REFERENCE-BLIND ADAPTATION of Alizadeh et al., "Remote Monitoring of
  Human Vital Signs Using mm-Wave FMCW Radar," *IEEE Access* 2019; 7:54958–54968
  (DOI 10.1109/ACCESS.2019.2912956).** The implementation **reproduces Alizadeh's published
  phase-based FMCW signal chain (§III, pp. 54961–54962) exactly** — its range-FFT, DC-offset /
  constellation compensation, phase extraction and unwrapping, and the second-FFT
  vibration/range-spectrum estimation with Gaussian peak interpolation (**no** harmonic
  cancellation) — **except the two steps that consult the reference**, which are replaced so the
  arm never touches the study reference:
  - Alizadeh selects the range bin whose rate estimate is **closest to the reference sensor**
    (pp. 54961–54962) — a reference leak. **Replaced** with this project's outcome-independent
    warmup bin-lock (the same bin ECA+AHET uses), which consults no reference.
  - Alizadeh removes unspecified outliers. **Replaced** with **no reference-based outlier removal**
    (any post-hoc pruning against the reference would leak it).

  **This is an *adaptation*, not a faithful reproduction, and is labelled so.** The exact retained
  stages are taken from the paper at implementation, not paraphrased authoritatively here; only the
  two replacements above are normative for outcome-independence. Chosen on outcome-independent
  criteria (same 77 GHz FMCW modality; peer-reviewed; a standard pipeline lacking ECA+AHET's
  harmonic handling, so the contrast is informative). **No performance on any of this project's
  data entered the choice or the adaptation.** Any post-freeze change requires a dated amendment
  (§4). **TI on-chip output (M10a) is a separate engineering baseline, not one of the four
  estimator arms.**

### 3.1 Data-roles table (binding)
| data | role |
|---|---|
| 4 existing captures | **development/tuning AND exploratory evaluation only; never confirmatory/headline.** Exploratory agreement (e.g. M4's HR reproduction, M8/M9/M10 offline arms) is permitted and labelled exploratory; performance on a capture a method was tuned on is additionally labelled **apparent / in-sample**. |
| M1 smoke test | **engineering-only** — never scored, never evaluation (CLAUDE.md §4). |
| M5 pilot | post-freeze exploratory — may change the rules; excluded from confirmatory metrics |
| M6 | evaluation only — never tuning; the confirmatory evidence base |
| **M7 collision capture** | **role is method-specific.** Confirmatory **only** for an estimator that was *not* fit, tuned, or selected using M7. For any method whose parameters/thresholds are fit or chosen using M7 — e.g. the Stage 1B lag-10 veto (M11c) and M8's collision tuning — M7 is **method-development/exploratory** and is **excluded from that method's confirmatory/headline metrics**. A capture cannot both fit and confirm the same method. |

### 3.2 Pooling table (binding)
- **M5 pilot never enters** confirmatory or headline metrics.
- **18 bpm paced arm — HR only.** For **HR**, the 18 bpm arm is **always reported separately and
  never pooled** into headline metrics (it sits inside the 4·f_r ≈ HR collision zone —
  `notes/protocol.md`). **For BR this exclusion does NOT apply** (the collision is an HR-cancellation
  mechanism, not a BR one): the 18 bpm paced session **is included in the BR paced summary**
  (`notes/comparator_prespec_br.md` §2.5). The two vitals use different pooling here by design.
- **Headline MAE/RMSE/coverage weighting — exact equations (per arm `a`, over its `n_s` evaluable
  windows for subject `s`, `S` subjects):**
  - per-subject: `MAE_s = mean_k |d_sk|`;  `MSE_s = mean_k d_sk²`;  `cov_s = n_s / N_s`
    (evaluable ÷ total windows in that session).
  - **subject-weighted headline (each subject equal, regardless of `n_s`):**
    `MAE = mean_s(MAE_s)`;  **`RMSE = sqrt( mean_s(MSE_s) )`** (the root of the mean of subject
    MSEs — **not** `mean_s(RMSE_s)`, which differs);  `coverage = mean_s(cov_s)`.
- **Arms are reported separately** (natural, paced) — these are the primary summaries. A **combined
  natural+paced headline is NOT reported** as a single pooled number, because after the HR-only
  18 bpm exclusion (§3.2 above) subjects contribute unequal arm sets, so a pooled figure is not one
  comparable estimand. If a combined view is shown it is **arm-equal-weighted and labelled
  descriptive**, never a headline LoA.
- Unequal evaluable-window counts feed the variance-components model (§1) directly.

## 4. Amendment mechanism

An amendment is: a dated changelog entry (what changed, why); cross-model review where CLAUDE.md §6
applies; a **new Zenodo version DOI** under the concept DOI; applied **prospectively only** (never
retrofitted to already-collected data). An un-re-deposited change is void — the frozen version
governs. An amendment changing what a participant does clears ethics (`24IBEC051`) first.

## 5. Endpoint definitions

- **HR MAE / RMSE:** over evaluable non-overlapping windows, in bpm, radar vs Masimo `Beats/min`
  (PR), per `notes/comparator_prespec.md`.
- **Coverage:** evaluable windows ÷ total non-overlapping windows in the session, reported
  **alongside every accuracy number, always**.
- **Severe error (HR):** **|radar − Masimo PR| > 5.0 bpm** (5.0 exactly is *not* severe; strict
  inequality), reported as a count and as a fraction of evaluable windows.
- **BR MAE / RMSE:** over evaluable non-overlapping windows, radar vs Masimo `Breaths/min` (RRp),
  per `notes/comparator_prespec_br.md`; for paced sessions also vs the commanded metronome rate
  (report both). Coverage reported alongside, always.
- **BR error tail counts (no "severe" label).** BR has a different range and reference accuracy
  than HR, and no source supplies an a-priori 5 bpm clinical threshold for BR, so the HR "severe"
  label is **not** carried over. Instead report **descriptive tail counts at pre-declared BR
  thresholds |radar − RR_ref| > 2, > 3, > 5 bpm** (strict), as counts and fractions of evaluable
  windows, against **RRp** (the measured reference); for paced sessions additionally against the
  **commanded target** as a concordance figure (not "truth" — `notes/comparator_prespec_br.md`
  §2.5). Natural-session tails rest on RRp alone and are read as exploratory.

## 6. Exclusion / disposition hierarchy

Every failure class below has an **objective, pre-outcome disposition** — decidable without looking
at any agreement value. **Counts and reasons are reported at every level, always** (window,
session, subject). No session, arm, or subject may be moved or removed on the basis of its
bias/error.

**Window level (within an admitted session):**
1. **Reference unavailable / inadmissible** — window fails a comparator gate
   (`notes/comparator_prespec.md` §2.2 for HR, `notes/comparator_prespec_br.md` §2.2 for BR):
   excluded, counted.
2. **Radar NaN** — no estimate produced: **counted as a coverage failure** (it lowers coverage; it
   is *not* a licence to drop the session).

**Session level (decided before scoring, from capture/reference integrity — never from agreement):**
3. **Protocol abort** — settle criterion not met, or recording stopped mid-session
   (`notes/protocol.md`): session not admitted; logged.
4. **Unusable / corrupt raw capture (objective test).** From `run_metadata.json`: a session is
   **corrupt and not admitted** iff the raw mirror is truncated so that a **non-final** window is
   incomplete (`mirror_truncated_bytes` cuts into a mid-recording window) **or** a stored file
   checksum fails. A session with all complete windows plus an **incomplete trailing partial
   window is RETAINED** — that tail window is simply unscored (§7). **Packet loss** above a frozen
   tolerance (`n_dropped / n_received > 5 %`) **flags** the session (reported) but does not by
   itself exclude it; the per-frame validity map (§7) decides which windows are radar-NaN.
5. **Epoch-sync failure (objective, agreement-blind).** Before each session the PC and Masimo-phone
   clocks are synchronised to a common NTP source and **both offsets logged**; **max tolerated
   PC↔phone offset ±1 s**, re-checked at session end for drift. Offset > ±1 s → **resync and
   restart before recording**. **No offset may be chosen or adjusted by optimising radar–reference
   agreement** (the §4-forbidden tuning). `frame0_epoch` (§7) uses the synchronised PC UTC clock.
   Procedure lives in `notes/protocol.md`.
6. **Incomplete / missing reference (M3R-21).** **No whole-session reference floor is defined** —
   neither comparator defines one. Partial reference is handled **entirely by the per-window
   comparator gates** (a window with no admissible reference is excluded and counted; §2.2 of each
   comparator) **plus the evidence floor (§2).** A **wholly missing Masimo file** is a
   separately-logged **no-agreement** session (radar-only, descriptive at most).
7. **Poor warmup bin-lock (outcome-blind).** **Frozen trigger:** re-run iff
   `warmup_bin_selection.json` reports **`selected_confidence == "low"`**.
   (`live_demo.py:_run_warmup_selection` sets "low" when no energy-eligible candidate yields a
   valid, confident DSP winner — i.e. **all** candidates energy-ineligible, **or** an
   energy-eligible candidate wins but lacks the required HR/BR validity/confidence.) Warmup
   selection **runs the DSP internally**, so it is **reference/agreement-blind and pre-*display*,
   not pre-*estimate***; it never sees the Masimo. **Retry accounting (M3R-20):** because the
   trigger reads radar signal availability, retries must not silently lift coverage — **at most one
   re-run per session**; the discarded attempt is logged with its `selected_confidence` but its
   windows **do not enter the coverage denominator** (it was never a scored session); the number of
   sessions requiring a retry, and the number still "low" after retry, are **reported at study
   level**. **If the retry is also "low", the session is captured and scored anyway** (no further
   retries) — a low-confidence session is **never silently dropped**, so coverage cannot be
   inflated by discarding hard sessions.

**Replacement policy (binding):** a session not admitted for reasons 3–5 **may be re-captured**
(same subject, same protocol) **only before any of that subject's data is scored**; both the
discarded and the replacement session are **logged with reason**. Reason 6 is a no-agreement
session (not re-captured); reason 7 is the single warmup retry above. No replacement once scoring
has begun. Missing/replaced sessions are counted against the evidence floor (§2).

**Subject level:** a subject falling below the per-subject floor (§2) is handled by the frozen
evidence-floor rule, **not** by discretionary exclusion.

## 7. Window selection and required breakdowns

- **Exact non-overlapping 30 s window grid (FROZEN).** At the 20 Hz frame rate, 30 s = **600
  frames**. Windows are the consecutive, non-overlapping, **half-open frame intervals**
  `[k·600, (k+1)·600)` for `k = 0, 1, 2, …`, indexed by **frame number** (not wall-clock — the
  `--replay-fast` `elapsed_s` is unusable; HANDOFF §5). **The first window `k = 0` (`[0, 30) s`) IS
  scored**, and its mechanism is stated exactly: `scripts/live_demo.py` fills the first 600-frame
  ring buffer, runs warmup bin-selection **on that buffer**, then applies the result back to the
  same buffer via `dsp_override` to emit the `k = 0` estimate (`live_demo.py:1564–1598`). So the
  selected bin is not "active in real time" during `[0, 30)`; it is chosen from the `k = 0` buffer
  and applied to it. Every window `k ≥ 0` is scored **iff it is a complete 600 frames**.
- **Frame-0 epoch origin — FROZEN rule, with a forward requirement.** The reference window for
  frame window `k` is the **half-open integer-second epoch interval** `[E(k·600), E((k+1)·600))`,
  where `E(i) = frame0_epoch + i/20` (20 Hz) and a Masimo sample at integer `epoch_utc = e` belongs
  to the window iff `E(k·600) ≤ e < E((k+1)·600)` — the same alignment rule as the HR comparator
  (`notes/comparator_prespec.md` §2.1; align on integer `epoch_utc`). **`frame0_epoch` is bound to a
  single named event: the synchronised-clock UTC time recorded at receipt/assembly of frame index
  0** — **not** `run_metadata.json`'s `start_wall_utc`, which is written *before* DCA/IWR
  configuration and capture startup (`live_demo.py:1213` vs `1300`+) and is therefore **not** a
  frame-0 timestamp. **Forward requirement (M1/M4):** the study capture path must (a) persist that
  frame-0 UTC timestamp, and (b) persist a **per-frame validity / zero-fill map** — not merely the
  aggregate `n_dropped` / `zero_filled_bytes`, which cannot identify *which* windows are affected. A
  window containing any dropped or zero-filled frame is **flagged from that map and its estimate
  treated as radar-NaN** (§6 window level).
- **The 4 existing exploratory captures lack a persisted `frame0_epoch`.** Any window alignment
  reconstructed for them from `start_wall_utc` is **APPROXIMATE** (the offset is the capture-startup
  latency, not sub-second) and is used **only for reference-characterization design evidence**
  (`notes/comparator_prespec_br.md` §1), never for a frozen scoring number.
  - **Selection is boundary-aligned, not greedy:** each window is scored by the single estimate
    whose 600-frame analysis window is exactly `[k·600, (k+1)·600)`. Greedy selection of accepted
    hops is forbidden (it would maximise accepted windows and is estimator-dependent).
  - **Yield arithmetic under this grid:** a 180 s session → 6 windows (`k=0..5`); a 480 s session →
    16; a **600 s (10-min) session → exactly 20 windows** (`k=0..19`, including the `k=0`
    warmup-fill window). `notes/protocol.md` has been corrected to this exact count (it previously
    said "~19"). The A2 counts in `plans/m0_b1_evidence_floor_memo.md` (natural 6, paced16 6,
    sweep 16) are exactly this grid.
- **Required breakdowns:** per-subject HR (and BR, per M3) MAE/RMSE **and coverage** are required
  outputs, not options.
- **Distance is descriptive, not inferential:** reported as error against the measured continuous
  distance. **No post-hoc distance strata and no per-distance agreement claim** unless separately
  pre-specified by amendment (`plans/implementation_plan.md` §M4). NB: distance was not recorded in
  the 4 existing exploratory captures — this breakdown applies from M5 onward.

## 8. Deliberately not pre-specified (open degrees of freedom)

The deposit freezes **only the enumerated rules**. These are intentionally left open; resolving any
of them uses only design/tuning data (§3.1) and, where noted, an amendment:
- Coverage-improvement work (M11a) and any resulting estimator changes — amendment required to
  make one the primary.
- Secondary-method internals and tuning (HA, joint-Doppler). **The *identity* of the
  published-pipeline baseline is NOT open — see §3.**
- Sensitivity analyses beyond those named in §1 and `notes/comparator_prespec.md` §2.3.
- Figure and presentation choices.

---

### Open-items checklist (all must close before the deposit is frozen)
- [x] §1 statistical citations verified 2026-07-25 (BA 1986/2007, Carstensen 2008, Zou 2013)
- [x] §1 agreement model corrected (2-level per-arm; bootstrap primary; MOVER arm-specific) after
      cross-review M3R-01/M3R-02 — still requires the math/claims sign-off below
- [x] §2a Option A core frozen (2026-07-25) — per-session ≥1, per-subject ≥4, ≤5 bpm CI, natural-drop miss rule
- [x] §2b extensions FROZEN (user decision 2026-07-25): study-wide ≥8/10, symmetric zero-window
      handling, **no** whole-subject exclusion, precision-miss → descriptive
- [x] §3 published-pipeline baseline — Alizadeh 2019 as a declared **reference-blind adaptation** (M3R-04)
- [x] §5 BR endpoints imported from M3's comparator; §3.2 18 bpm pooling made HR-only (M3R-08)
- [x] §6 exclusion/disposition hierarchy enumerated (M3R-10)
- [x] §7 non-overlapping-window grid frozen exactly (frame-index, first window included) (M3R-09)
- [ ] math/claims cross-model review of §1 passed, and the M3 BR-comparator cross-review closed
