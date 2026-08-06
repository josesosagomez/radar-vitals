# Analysis pre-specification — mmWave vital-signs study

> **Status: INTERNAL ENGINEERING SPEC — 2026-08-03.** **M0 was removed from the project**, so this
> is no longer a deposit draft and **nothing here is pre-registered.** It remains binding *as
> engineering*: `src/m4/window_grid.py` cites §7 as its authority and hard-errors against it, and
> the decisions below are still the ones the study cannot be re-run to fix. Changes are made by
> dated `HISTORY.md` entry plus CLAUDE.md §6 cross-review where it applies; §4's deposit/DOI
> machinery is dead. **The prospective-only rule in §4 survives and still matters** — it is what
> stops a threshold being chosen after seeing the data it will be judged on.
>
> Companion specs, same status: `notes/comparator_prespec.md` (HR scoring),
> `notes/comparator_prespec_br.md` (BR scoring — **M3, cross-reviewed 2026-07-25/26; all findings
> resolved**), `notes/protocol.md` (capture protocol), `notes/capture_inventory.md`.
>
> **Cross-review COMPLETE:** all M3 findings (M3R-01…48) are **resolved** across 17 rounds (see
> `plans/m3_prespec_cross_review.md`). There is no freeze and no deposit — M0 was removed.
>
> **Change log, kept so the spec is self-documenting:**
> **2026-07-27 — §6 item 7, location-only.** The parenthetical naming the code that sets
> `selected_confidence == "low"` was updated from `live_demo.py:_run_warmup_selection` to
> `src/warmup_select.py:run_warmup_selection`: the M4 Stage 0 refactor (`4b64eb8`) moved that
> function, verified behaviour-identical. **No rule, trigger, threshold or definition changed** —
> the frozen trigger still reads `selected_confidence` from `warmup_bin_selection.json`, and that
> field is written identically. Raised as S0R-06 in `plans/m4_stage0_refactor_review.md`; user
> decision 2026-07-27.
>
> **2026-07-30 — M8 existing-data clarification, pre-freeze.** The existing exploratory inventory
> is now eight captures, not four. All eight are development/apparent data (four subjects — corrected
> 2026-08-03; previously recorded as one) and remain
> ineligible for primary/headline use. The reviewed M8 Step 1b plan permits explicitly
> `exploratory_non_frozen` reference scoring from the approximate `start_wall_utc` origin only if
> every row/manifest/table/plot states that timing limitation and its ineligibility for promotion or
> final agreement claims. It retains `k=0` as lock-selection-in-sample diagnostics and uses only
> `k>=1` for comparative accuracy. This is a method-specific descriptive clarification, **not** a
> relaxation of the prospective `frame0_epoch` requirement. (It was originally positioned as coming
> "before M0 deposit"; no deposit ever existed — M0 was removed 2026-08-03 — and the clarification
> stands on its own terms.) It was included in the
> five-discipline review of `plans/m8_step1b_ahmed_transfer.md`.
>
> **2026-08-03 — PENDING, NOT YET APPLIED: a third arm.** *(An internal-consistency debt, not a
> blocker on anything — M0 is gone. It still decides what M6 can claim.)* **Trigger, set by the
> user 2026-08-04: this edit must land BEFORE ANY M5 PILOT SESSION.** It does not block the
> three new BR captures (subjects E/F/G), which are not M5.* IBEC approved an amendment to
> `24IBEC051` adding a **seated HR-recovery arm** (`notes/protocol.md`, "HR dynamic-range arm").
> The study design is now **10 subjects × 3 sessions**, and arm becomes
> `a ∈ {natural, paced, recovery}`. **§1 below still says 2 sessions and 2 arms and has NOT been
> edited** — the change needs CLAUDE.md §6 cross-model review first (the completed M3 review
> covered the 2-arm design). **This banner exists so the contradiction is visible rather than
> silent; `notes/protocol.md` is the newer document and governs what is captured.**
>
> What the edit must cover when it is made:
> - **§1 estimand:** extend the arm set; add a third arm-conditional LoA. The two-level model
>   survives intact — with one session per arm per subject still true at 3 arms, the
>   "no session-within-subject variance component" argument is unchanged.
> - **§1 non-exchangeability:** recovery is a third non-exchangeable regime, so it gets its own
>   `μ_a` and LoA and is **never pooled** with natural or paced (M3R-27 already forbids a
>   combined headline).
> - **§2b per-subject floor** reads "≥ 4 evaluable windows (**across the 2 sessions**)" — the
>   denominator changes at 3 sessions and the number must be re-decided, not silently rescaled.
> - **§2b miss rule** names only natural and paced ("drop that subject's **natural** arm to
>   descriptive; report **paced** only"). It has no branch for a recovery arm that misses.
> - **§2b "No add-sessions lever"** and its conclusion that the *"`24IBEC051` permits > 2
>   sessions/subject?"* question (A5(a)) is **moot** are now contradicted on their face. The
>   distinction the edit must draw explicitly: the recovery arm is **not** an add-sessions lever
>   in the §2b sense — it does not add sessions to raise evidence *yield*, it adds an arm to make
>   the HR claim *falsifiable*. Option A still narrows rather than recruits. Say so, or a
>   reviewer will read a plain contradiction.
> - **§2a/§2b evidence floor — the real risk.** The recovery arm is *deliberately* non-stationary,
>   and HR admissibility requires within-window PR spread ≤ 5 bpm
>   (`src/comparator.py:_HR_STATIONARITY_MAX_BPM`). Early-recovery windows will legitimately fail
>   it. **The floor may not be achievable in this arm**, and whether it is arm-specific must be
>   decided *before* that arm is captured — not after seeing its yield. An honesty rule now, not a
>   governance one.
> - **Order:** sessions run natural → paced → recovery, fixed, no counterbalancing.

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

**No single combined LoA (M3R-27).** Because arm is a fixed effect, a pooled "marginal" LoA would
surround a covariate-specific mean whose value depends on arbitrary centering/weights — not one
well-defined estimand — and §3.2 already forbids a combined headline. So **only arm-conditional**
limits are reported; there is **no** single natural+paced LoA.

**Paced commanded-rate estimand — FROZEN (M3R-31).** Per `notes/protocol.md`, each subject is paced
at a **single steady commanded rate** (12, 15, *or* 18 bpm), so commanded rate is a **between-subject**
attribute, **not** a within-subject factor. The **only inferential paced estimand is the arm-level
paced LoA** `μ_paced ± 1.96·√(σ²_b + σ²_w)` from the arm model `d_ik = μ_paced + b_i + e_ik`.
**This is explicitly a MARGINAL LoA over the study's commanded-rate allocation — a design-weighted
mixture across the 12/15/18 subgroups (M3R-31 r2)**, whose bias `μ_paced` and between-subject variance
`σ²_b` **depend on the realized allocation weights**. It is **not** a "rate effect absorbed by `b_i`":
a zero-mean intercept cannot absorb a systematic between-group shift — instead, between-group rate
differences enter `σ²_b` as genuine between-subject spread and any rate mean-shift enters `μ_paced` as
the mixture mean. **No separate commanded-rate fixed-effect term, rate-specific variance rule, or
per-rate CI is added or reported.** **The allocation is fixed prospectively and reported, never an
analyst choice:** the 10 subjects are assigned rates by the protocol's **enrolment-order rotation**
(12 → 15 → 18 repeating), which for `N = 10` yields **counts 4 / 3 / 3** — rate 12 takes the extra
(tenth) subject — fixed **before any data is collected or scored**. Every paced LoA is reported
**with its realized per-rate subject counts** (the mixture weights), so the weighting is transparent.
*(This enrolment-order start is a study-design default reconciled into `notes/protocol.md`; the user
may re-fix the allocation, but it must be frozen pre-collection, never chosen post-hoc.)* **Which
subjects enter the paced estimand differs by vital sign, by design (§3.2):** the **HR** paced LoA is
the mixture over **12 and 15 bpm subjects only** (7 subjects, counts 4/3; the 18 bpm subjects sit in
the 4·f_r≈HR collision zone and are reported **separately / descriptively**, never pooled — §3.2); the
**BR** paced LoA is the mixture over **all paced subjects (12/15/18; 10 subjects, counts 4/3/3)** (the
collision is an HR-cancellation mechanism, not a BR one — `notes/comparator_prespec_br.md` §2.5).
**Commanded-rate-stratified summaries (per 12/15/18) are DESCRIPTIVE only** — per-rate bias and
observed SD, **no population LoA and no CI** — because 3–4 subjects per rate cannot support a
between-subject population LoA. There is no post-data discretion over pooling or rate adjustment:
this rule is frozen here.

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

**CI on the LoA — frozen recipe (M3R-29, user decision 2026-07-26: Option A).** Primary =
**subject-level nonparametric cluster bootstrap over the `S_a` contributing subjects** (not a
hard-coded 10). It is primary because it is **fully specified and executable today** and
**estimand-matched by construction**: each replicate recomputes the **exact frozen closed-form
components above**, so the CI targets the identical LoA estimand and subject-weighting as the point
estimate — with no external formula to transcribe and no not-yet-existing implementation to freeze.
**Recipe:** resample `S_a` subjects with replacement (a subject drawn twice enters as two distinct
clusters with all its windows); on each resample recompute `μ_a`, `σ²_w = MSW`,
`σ²_b = max((MSB − MSW)/n0, 0)` and `LoA_a = μ_a ± 1.96·√(σ²_b + σ²_w)`; **B = 10 000** replicates;
fixed **seed = 20260725**; **two-sided 95 %** percentile interval (2.5 / 97.5), taken per endpoint,
with the percentile computed by the **`linear`** method (**clarification, user decision
2026-07-26, M4 plan review M4R-09** — the same convention now named in `notes/comparator_prespec.md`
§2.2 and `notes/comparator_prespec_br.md` §2.2 for the `p90 − p10` stationarity gates; it must be
passed explicitly at every call site, never left to a library default, so the CI endpoints and the
admissibility gates cannot diverge on a numerical convention). A
replicate whose resample **fails the estimability conditions above** (< 2 distinct subjects, or no
subject with ≥ 2 windows) is **recorded and excluded**; if **> 5 % of replicates fail**, the arm
reports **descriptive-only** (no population-LoA CI). The primary is subject to the **same estimability
conditions** (`S_a ≥ 2`, `N_a > S_a`).

**Declared limitation of the primary — it is ANTI-CONSERVATIVE for the precision gate (stated
plainly, M3R-45).** A percentile cluster bootstrap over `S_a ≤ 10` clusters has **no coverage
guarantee** and is known to **under-cover** at this sample size, so the reported CIs may be
**optimistically narrow**. For the ≤ 5 bpm CI-half-width precision gate (§2a/§2b) this is
**anti-conservative, not conservative**: an over-narrow CI can turn a *true* half-width **above** 5 bpm
into an *observed* half-width **≤ 5**, letting an arm **falsely retain its primary headline** when
a better-calibrated interval would have sent it to descriptive-only. The gate can therefore be
**passed too easily** at small `S_a`. This is a **declared limitation**, reported with every LoA
alongside the `S_a ≤ 10` caveat; it is **not** self-correcting and is **not** claimed to be.
**ACCEPTED (user decision 2026-07-26, M3R-45):** the user **knowingly accepts this anti-conservative
precision gate** as the cost of the Option A executable-now choice — a gate-pass at small `S_a` is
therefore **not** a guarantee of adequate precision, and is read together with the reported CI and the
`S_a ≤ 10` caveat. Any future conservative adjustment (e.g. a small-sample CI widening, or treating a
gate-pass at `S_a ≤ 10` as provisional) would be a **prospective amendment**, never a post-hoc
reinterpretation of the gate's direction.

**Sensitivity CIs (reported alongside; never a gate on the primary):**
- **MOVER (Zou 2013), arm-specific, changing-true-value** — a **pre-named candidate sensitivity**
  (M3R-46). *(Zou's MOVER is coverage-validated in Zou's own simulations, but **not** yet validated
  for **this** estimator/recipe — so it is a candidate, not "the validated procedure", until proven.)*
  It becomes a **reported, validated sensitivity only if ALL three conditions hold**: the M4 harness
  **implements** it, a **statistician math-reviews** that implementation, **and** it **passes a named,
  tolerance-pinned published benchmark** (Zou 2013's worked example or an equivalent) — implementation
  alone is **not** the validation gate — with its committed code incorporated **by reference at the
  freeze commit** (Option B layering, M3R-29). Until all three are met MOVER is **not** reported and
  **no** claim is made that it is validated for this estimator; when reported it is a sensitivity, and
  if it and the primary diverge materially at `S_a ≤ 10` that divergence is itself reported.
- The **subject-clustered regression-based LoA** in the diagnostics paragraph below (M3R-30) is the
  other pre-named descriptive sensitivity.

**Executed by the committed, hash-pinned M4 harness**, which logs package versions, seed, config, git
commit, and input SHA-256 at run (CLAUDE.md §3.1).

**Handling of design features.** Unequal window counts per subject are handled by the unbalanced
one-way ANOVA via the effective group-size coefficient `n0` above (no per-subject pre-averaging).
The pooled `bias ± 1.96·SD` with
`se_loa = sqrt(3·SD²/n)` in `scripts/plot_bland_altman.py` is **descriptive-only** and never quoted
as a limit of agreement.

**Assumption diagnostics — MANDATORY, not conditional (M3R-18).** The constant-`μ ± 1.96·SD` LoA is
the **primary, frozen** estimand and is **never** switched post-hoc. For **every arm**,
three items are **always computed and reported** (no "if breached" trigger): (i) proportional-bias /
heteroscedasticity — the slope of difference-vs-mean and residual-spread-vs-magnitude; (ii)
difference-tail normality — residual skew and a QQ summary; (iii) a **serial-correlation summary =
the residual lag-1 autocorrelation** within session (**M3R-29/45/47** — two separate things:
**(a) the POINT LoA and its ANOVA variance components** (`σ²_w = MSW`, `σ²_b`, and the width
`1.96·√(σ²_b + σ²_w)` from the model `d_ik = μ_a + b_i + e_ik`) **assume conditionally independent,
homoscedastic within-subject residuals.** Positive **serial correlation** changes the expectation of
the within-subject mean square and can therefore **bias `σ²_w`, `σ²_b`, and the point LoA width
itself** (Bland–Altman 2007) — a **declared limitation**, not something the model corrects.
**(b) The primary cluster bootstrap** resamples **whole subjects**, so it preserves each subject's
observed within-session sequence when estimating the **sampling distribution** of the LoA (its
interval does not *additionally* assume residual independence), **but it does NOT repair a biased or
misspecified point estimator**, and its own validity still needs independent subjects and
large-cluster asymptotics that `S_a ≤ 10` does not guarantee (the anti-conservative limitation above).
**So cluster resampling does NOT make the primary LoA robust to serial correlation.** The residual
**lag-1 autocorrelation is a mandatory diagnostic and a reported limitation**; the homoscedasticity
assumption is separately probed by the regression sensitivity below). A **single, fully-specified descriptive
sensitivity** is **always** reported alongside: a **subject-clustered regression-based LoA** —
the difference regressed on the mean with a **subject random intercept** (mixed model, same
clustering as the primary), **population** limits `= fitted bias(mean) ± 1.96·√(σ²_b,reg + σ²_e,reg)`,
where `σ²_b,reg` is the fitted between-subject random-intercept variance and `σ²_e,reg` the residual
variance **from that same mixed model** (M3R-30). Both components are included — a
residual-only `± 1.96·(residual SD)` interval would be a subject-*conditional* band, not a population
LoA, and would be systematically too narrow whenever between-subject heterogeneity is nonzero; this
sensitivity is a population LoA comparable to the primary, labelled descriptive. Its uncertainty is
obtained by the same whole-subject cluster bootstrap that provides the §1 sensitivity CI (or, where
that is unavailable, reported as a point sensitivity without a CI). **Scope of this sensitivity,
stated honestly:** it addresses **proportional bias only**; it uses a **constant residual SD**
`σ²_e,reg`, so it does **not** correct heteroscedasticity — a heteroscedastic constant LoA therefore
**remains a declared limitation**, not something this sensitivity removes. Choosing a
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

> **Status after M0's removal — BINDING AS ENGINEERING (user decision 2026-08-04).** The floor
> was written when it was also governance for a deposit gate. **The deposit is gone; the floor
> is not.** It keeps its force: a below-floor result **narrows the claim and is logged**, it is
> not quietly ignored. What it never was, and must never be described as, is a *timing* claim —
> nothing here is pre-registered. Its value is that it is written out in full and applied
> identically to every estimator compared, and that it is fixed **before** the yield it judges
> is seen (the §4 prospective-only rule, which also survived M0). A floor chosen after seeing
> the pilot yield is not a floor — that reasoning never depended on a deposit.

The minimum number of **evaluable** (radar-accepted **and** comparator-admissible,
non-overlapping 30 s) windows the study is powered to report.

**Vital-sign scope (M3R-28).** Option A below is the **HR (primary-endpoint) evidence floor** — it
was derived from the HR AHET-acceptance yield and the HR comparator
(`plans/m0_b1_evidence_floor_memo.md`). **BR is a secondary, exploratory endpoint (§3) and carries
no evidence floor for primary/headline claims**: BR agreement is reported **descriptively wherever BR-evaluable
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
lower) to each of its two CI endpoints is ≤ 5 bpm — evaluated **per arm** on the **primary
(cluster-bootstrap) CI** (§1, M3R-29 Option A). The **consequence of missing it** is frozen in §2b.

### 2b. Extensions — FROZEN (user decision 2026-07-25, adopting the cross-review recommendation)
The following complete the HR evidence floor and are now binding:
- **Study-wide floor:** the study-wide primary HR agreement claim requires **≥ 8 of the 10
  subjects** to meet the per-subject floor. If fewer than 8 do, the **study-wide claim weakens to
  descriptive** (bias and observed spread; no population LoA), reported as a limitation.
- **Symmetric zero-window handling:** the §2a miss rule is **symmetric** — **either** arm (natural
  *or* paced) that yields **< 1 evaluable window** has **that arm** reported descriptive-only; the
  subject's other arm still contributes to its arm-specific primary analysis.
- **No automatic whole-subject exclusion.** A subject below the **≥ 4** per-subject floor is **not**
  removed: its **eligible arm data remain in the arm-specific analysis**, flagged **below-floor**
  with counts reported. The **≥ 8/10 study-wide rule** (above) — not deletion of the subject — is
  what governs whether the study-wide primary claim survives. (This keeps a below-floor
  subject's otherwise-usable data in the analysis rather than discarding it on a count.)
- **Precision-miss consequence:** if the ≤ 5 bpm CI-half-width target (§2a) is not met for an arm,
  that arm's **headline weakens to descriptive** (bias and observed spread; no population LoA),
  stated as a limitation — never renegotiated.
- **Denominator of the ≥ 8/10 floor, and its relation to the arm-specific LoAs (M3R-42 clarification
  — no frozen threshold changed).** The ≥ 8/10 is a **study-wide, per-subject coverage**
  prerequisite: a subject **qualifies** if it meets the per-subject floor (≥ 4 **evaluable** windows
  across its 2 sessions), where *evaluable* is fixed **before** any pooling rule (radar-accepted +
  comparator-admissible; §2 head). The §3.2 HR **18 bpm exclusion is a *pooling* rule applied *after***
  evaluability, so an 18 bpm subject's paced windows still count toward **its own** per-subject floor
  and toward the ≥ 8/10 study-wide count. **The ≥ 8/10 is NOT a per-arm requirement.** Each
  arm-specific LoA is reported over its **own** contributing subjects `S_a` (§1); consistent with the
  no-double-standard rule, a subject **barred from an arm's estimand does not count toward that arm's
  `S_a`** — the subjects paced at 18 bpm are **excluded from the paced-HR arm's `S_a`** (so that arm's
  `S_a ≤ 7` under the 4/3/3 allocation), while they still contribute to the study-wide ≥ 8/10, to the
  **natural** arm, to the descriptive per-rate 18 bpm summary, and to the BR paced pool (they are
  **not** barred from *all* estimands). **Design consequence — CONFIRMED (user decision 2026-07-26):**
  because the 18 bpm collision arm is run **deliberately** (`notes/protocol.md`), the **paced-HR
  arm-specific LoA rests on ≤ 7 subjects by design** and cannot by itself reach an 8-subject bar; the
  ≥ 8/10 governs the study's **overall** subject coverage (the natural arm can reach 10), **not** the
  paced-HR arm count. The user confirmed this study-wide (not per-arm) reading is the intended meaning
  of the frozen §2b, and that the paced-HR headline resting on ≤ 7 subjects is acceptable by design —
  the 4/3/3 allocation and the paced-HR claim are unchanged.

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
  cancellation) — **except two steps, which are replaced to keep the arm reference-blind and
  reproducible** (the two are replaced for **different** reasons — one is a reference leak, the other
  is unreproducible — M3R-36):
  - Alizadeh selects the range bin whose rate estimate is **closest to the reference sensor**
    (pp. 54961–54962) — a **reference leak**. **Replaced** with this project's outcome-independent
    warmup bin-lock (the same bin ECA+AHET uses), which consults no reference.
  - Alizadeh removes outliers by a criterion that is **left unspecified in the paper** (attributed to
    measurement noise, with **no** reproducible rule, and **not** stated to consult the reference).
    Because that step is **not reproducible** — not because it is a reference leak — it is
    **replaced with no outlier removal** at all (and any reference-based pruning is separately
    forbidden as it would leak the reference). We do **not** claim the paper made outlier removal
    reference-dependent.

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
| 8 existing captures | **development/tuning AND exploratory evaluation only; never primary/headline.** Exploratory agreement (e.g. M4's HR reproduction, M8/M9/M10 offline arms) is permitted and labelled exploratory; performance on a capture a method was tuned on is additionally labelled **apparent / in-sample**. For M8, approximate-origin reference scoring is additionally labelled `exploratory_non_frozen` and is ineligible for promotion/final agreement claims. |
| M1 smoke test | **engineering-only** — never scored, never evaluation (CLAUDE.md §4). |
| M5 pilot | post-freeze exploratory — may change the rules; excluded from primary metrics |
| M6 | evaluation only — never tuning; the primary evidence base |
| **M7 collision capture** | **role is method-specific.** **Primary only** for an estimator that was *not* fit, tuned, or selected using M7. For any method whose parameters/thresholds are fit or chosen using M7 — e.g. the Stage 1B lag-10 veto (M11c) and M8's collision tuning — M7 is **method-development/exploratory** and is **excluded from that method's primary/headline metrics**. A capture cannot both fit and confirm the same method. |

### 3.2 Pooling table (binding)
- **M5 pilot never enters** primary or headline metrics.
- **18 bpm paced arm — HR only.** For **HR**, the 18 bpm arm is **always reported separately and
  never pooled** into headline metrics (it sits inside the 4·f_r ≈ HR collision zone —
  `notes/protocol.md`). **For BR this exclusion does NOT apply** (the collision is an HR-cancellation
  mechanism, not a BR one): the 18 bpm paced session **is included in the BR paced summary**
  (`notes/comparator_prespec_br.md` §2.5). The two vitals use different pooling here by design.
- **Paced commanded rate is between-subject; only the arm-level paced LoA is inferential (M3R-31).**
  Per the frozen §1 paced estimand: the **HR** paced LoA pools **12/15 bpm** subjects only (18 bpm
  separate/descriptive, as above); the **BR** paced LoA pools **12/15/18**. **Per-rate (12/15/18)
  breakdowns are descriptive-only** (bias + observed SD, no population LoA/CI) — ~3–4 subjects per
  rate cannot support a between-subject LoA. No separate rate fixed-effect term is fitted (§1).
- **Two explicit subject sets (M3R-32).** Because zero-window arms are an **expected** outcome under
  the §2b evidence-floor rules, the accuracy and coverage denominators are **defined over different
  subject sets** and must not be conflated:
  - **Accuracy set** = the `S_a` **contributing subjects with ≥ 1 evaluable window** (the same `S_a`
    as §1). `MAE_s`, `MSE_s` are computed only over these subjects (they are undefined for a subject
    with `n_s = 0`), and the **contributing count `S_a` is reported alongside** every accuracy number.
  - **Coverage set** = **every admitted subject/session in the arm**, including any with `n_s = 0`,
    for which `cov_s = 0/N_s = 0` is **defined and retained** (dropping it would inflate coverage —
    exactly the hardest, zero-output subjects must stay in the denominator).
- **Headline MAE/RMSE/coverage weighting — exact equations (per arm `a`, subject `s` with `n_s`
  evaluable of `N_s` total windows):**
  - per-subject: `MAE_s = mean_k |d_sk|`;  `MSE_s = mean_k d_sk²` (both over subjects with `n_s ≥ 1`);
    `cov_s = n_s / N_s` (evaluable ÷ total windows in that session; defined for `n_s ≥ 0`).
  - **subject-weighted headline (each subject equal, regardless of `n_s`):**
    `MAE = mean_{s∈S_a}(MAE_s)`;  **`RMSE = sqrt( mean_{s∈S_a}(MSE_s) )`** (the root of the mean of
    subject MSEs — **not** `mean_s(RMSE_s)`, which differs), both over the **accuracy set**;
    `coverage = mean_s(cov_s)` over the **coverage set** (all admitted subjects, `n_s = 0` included).
- **Arms are reported separately** (natural, paced) — these are the primary summaries. A **combined
  natural+paced headline is NOT reported** as a single pooled number, because after the HR-only
  18 bpm exclusion (§3.2 above) subjects contribute unequal arm sets, so a pooled figure is not one
  comparable estimand. If a combined view is shown it is **arm-equal-weighted and labelled
  descriptive**, never a headline LoA.
- Unequal evaluable-window counts feed the variance-components model (§1) directly.

## 4. Amendment mechanism

An amendment is: a dated changelog entry (what changed, why); cross-model review where CLAUDE.md §6
applies; a dated `HISTORY.md` entry; applied **prospectively only** (never retrofitted to
already-collected data). An unrecorded change is void — the version in this file governs.
*(The original wording required "a new Zenodo version DOI under the concept DOI" and called an
un-re-deposited change void. No Zenodo record ever existed — M0 was removed 2026-08-03 — so the
recording mechanism is `HISTORY.md`. **The prospective-only requirement is untouched and is the
part that ever mattered.**)* An amendment changing what a participant does clears ethics (`24IBEC051`) first.

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
3. **Protocol abort (intentional early termination)** — the settle criterion is not met, or the
   protocol run is **deliberately halted before its intended 10-min end** (operator stop, subject
   withdrawal, equipment intervention; `notes/protocol.md`): session **not admitted**; logged. **The
   discriminator vs item 4 (M3R-37):** item 3 covers a run that **did not reach its intended
   duration**; item 4 covers a run that **did** reach its intended end but whose *stored file* has an
   incomplete trailing window. An intentional early stop is item 3 (not admitted); a
   completed-length run with only a trailing file/transport fragment is item 4 (retained). A run
   cannot be both, because "reached its intended duration" is either true or false.
4. **Unusable / corrupt raw capture (objective test).** From `run_metadata.json`: a session is
   **corrupt and not admitted** iff the raw mirror is truncated so that a **non-final** window is
   incomplete (`mirror_truncated_bytes` cuts into a mid-recording window) **or** a stored file
   checksum fails. A session that **reached its intended duration** (not an item-3 early stop) with all complete
   windows plus an **incomplete trailing partial window is RETAINED** — that tail window is simply
   unscored (§7). **Packet loss** above a frozen
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
   (`src/warmup_select.py:run_warmup_selection` sets "low" when no energy-eligible candidate yields a
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
  **M8 interpretation (pre-freeze clarification, 2026-07-30):** retain and report `k=0`, but label
  it `lock_selection_in_sample`; only the persisted `k>=1` subset may support comparative accuracy
  because both M8 lock estimands were selected by production logic using `k=0`.
- **Frame-0 epoch origin — FROZEN rule, with a forward requirement.** The reference window for
  frame window `k` is the **half-open integer-second epoch interval** `[E(k·600), E((k+1)·600))`,
  where `E(i) = frame0_epoch + i/20` (20 Hz) and a Masimo sample at integer `epoch_utc = e` belongs
  to the window iff `E(k·600) ≤ e < E((k+1)·600)`. This aligns on the integer `epoch_utc` column as
  the HR comparator does (`notes/comparator_prespec.md` §2.1).
  > **RESOLVED — HR comparator harmonised (user decision 2026-07-26, M3R-40).** The HR comparator
  > previously wrote this span as the **closed** `[t − 30 s, t]`, which for integer-second samples
  > differs from this half-open grid by one endpoint (a closed span can double-count the boundary
  > second across adjacent windows). The user authorised **harmonising the HR comparator to the
  > half-open `[t − 30 s, t)`** — a clarification made before anything was scored under the closed
  > form (and no deposit or DOI ever existed), applied in
  > `notes/comparator_prespec.md` §2.1. **HR and BR now use the identical half-open endpoint rule**,
  > this frame-index grid being binding for both.
  **`frame0_epoch` is bound to a
  single named event: the synchronised-clock UTC time recorded at receipt/assembly of frame index
  0** — **not** `run_metadata.json`'s `start_wall_utc`, which is written *before* DCA/IWR
  configuration and capture startup (`live_demo.py:1213` vs `1300`+) and is therefore **not** a
  frame-0 timestamp. **Forward requirement (M1/M4):** the study capture path must (a) persist that
  frame-0 UTC timestamp, and (b) persist a **per-frame validity / zero-fill map** — not merely the
  aggregate `n_dropped` / `zero_filled_bytes`, which cannot identify *which* windows are affected. A
  window containing any dropped or zero-filled frame is **flagged from that map and its estimate
  treated as radar-NaN** (§6 window level).
- **The 8 existing exploratory captures lack a persisted `frame0_epoch`.** Any window alignment
  reconstructed for them from `start_wall_utc` is **APPROXIMATE** (the offset is the capture-startup
  latency, not sub-second). It is never a frozen scoring number. M8 may use it only for the
  explicitly `exploratory_non_frozen`, apparent/descriptive analysis declared in
  `plans/m8_step1b_ahmed_transfer.md`; every output must carry the approximate-origin and
  no-promotion/final-claim taint. Other uses remain limited to reference-characterization design
  evidence (`notes/comparator_prespec_br.md` §1).

  > **Amendment M9-1 (2026-08-06, prospective — committed before any M9 scoring run).**
  > `m9_approx_origin_amendment_version: 1`
  > `m9_amendment_cross_review: pending`
  >
  > The identical `exploratory_non_frozen` treatment above is extended to **M9 (Kotte
  > joint-Doppler, `plans/m9_kotte_plan.md`)**, per the user decision of 2026-08-05: M9 may
  > score the 8 existing captures against `start_wall_utc`-reconstructed window origins only
  > as apparent/descriptive analysis. Every M9 scored row, table, metric, and decision —
  > including `stage_b_decision.json` — must persist per capture the numeric epoch used,
  > `origin_source="start_wall_utc"`, the timezone-normalized value, and
  > `origin_is_approximate=true`, and must carry the approximate-origin and
  > no-promotion/no-final-agreement-claim taint. The captures stay exploratory and are never
  > headline evidence; validation-grade claims require exact-origin data (`frame0_epoch`,
  > available from the E/F/G captures onward). `scripts/m9_kotte_score.py` refuses to run
  > unless this amendment is present in a committed, clean-tree copy of this file **and**
  > the cross-review line above reads `completed` (CLAUDE.md §6 review by the other model
  > family; flip the line only when that review has passed, recording reviewer and date).
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
  specified by a documented amendment (`plans/implementation_plan.md` §M4). NB: distance was not recorded in
  the 8 existing exploratory captures — this breakdown applies from M5 onward.

## 8. Deliberately left open (open degrees of freedom)

This document fixes **only the enumerated rules**. These are intentionally left open; resolving any
of them uses only design/tuning data (§3.1) and, where noted, an amendment:
- Coverage-improvement work (M11a) and any resulting estimator changes — amendment required to
  make one the primary.
- Secondary-method internals and tuning (HA, joint-Doppler). **The *identity* of the
  published-pipeline baseline is NOT open — see §3.**
- Sensitivity analyses beyond those named in §1 and `notes/comparator_prespec.md` §2.3.
- Figure and presentation choices.

---

### Open-items checklist

> **Re-scoped 2026-08-04.** These previously "all had to close before the deposit is frozen".
> There is no deposit — M0 was removed 2026-08-03. They remain a real checklist of engineering
> debt; what they gate is stated per item, or nothing.
- [x] §1 statistical citations verified 2026-07-25 (BA 1986/2007, Carstensen 2008, Zou 2013)
- [x] §1 agreement model corrected (2-level per-arm; **cluster-bootstrap primary CI** — M3R-29
      Option A, user 2026-07-26 — with its **anti-conservative-precision-gate** limitation declared
      and **accepted** (M3R-45, user 2026-07-26); **MOVER a pre-named *candidate* sensitivity**, validated only after
      implementation + statistician review + benchmark (M3R-46); regression-LoA sensitivity uses both
      variance components after M3R-30) — still requires the math/claims sign-off below
- [x] §2a Option A core frozen (2026-07-25) — per-session ≥1, per-subject ≥4, ≤5 bpm CI, natural-drop miss rule
- [x] §2b extensions FROZEN (user decision 2026-07-25): study-wide ≥8/10, symmetric zero-window
      handling, **no** whole-subject exclusion, precision-miss → descriptive
- [x] §3 published-pipeline baseline — Alizadeh 2019 as a declared **reference-blind adaptation** (M3R-04)
- [x] §5 BR endpoints imported from M3's comparator; §3.2 18 bpm pooling made HR-only (M3R-08)
- [x] §6 exclusion/disposition hierarchy enumerated (M3R-10)
- [x] §7 non-overlapping-window grid frozen exactly (frame-index, first window included) (M3R-09)
- [x] math/claims cross-model review of §1 **passed** and all M3 findings (M3R-01…48) **resolved**
      across 17 rounds (Codex, 2026-07-25/26; `plans/m3_prespec_cross_review.md`) — **cross-review
      COMPLETE**; this pre-spec is ready for the M0 freeze.
