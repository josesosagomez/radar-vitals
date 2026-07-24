# Journal Paper — planning and content source

> **What this file is.** Everything needed to write and place a journal paper from this project:
> an honest submission-readiness gate, a target-journal comparison, the positioning and novelty
> argument, a section-by-section outline with word budgets, the figure and table plan, the
> reviewer objections you will actually receive, and a verified reference list.
>
> **Companion file:** [THIRD_CHAPTER.md](THIRD_CHAPTER.md) holds the full technical detail
> (theory, algorithm specification, protocol, failure analysis). This file does not repeat it —
> it decides what goes *in the paper*, in what order, and how it is framed. Status tags
> (`[VERIFIED] / [PRELIMINARY] / [RETIRED] / [PENDING]`) mean the same thing in both files.
>
> Synchronised with the repository **2026-07-24**, branch `vital_signs_v9c`, HEAD `b6f5b73`.
>
> **Corrections applied 2026-07-24** after a cross-model review of `plans/implementation_plan.md`
> (see `HISTORY.md`, same date): the test count, the respiration-collapse count (4/4 → 3/3), the
> Bland–Altman treatment (pooled-independent is invalid for a repeated-measures design), the
> per-distance claim (downgraded to descriptive — the protocol does not stratify distance), the
> pre-registration's defensible strength (§10), and the session size (recordings are now 10 min).
>
> **This is a living document.** The project is ongoing. The readiness gate in §1, the claim
> rankings in §3.2, and the results in §4 are all expected to change as data arrives — that is
> what they are for. Two rules keep it trustworthy: **promote a claim only by re-measuring it**
> (never by editing its status tag), and **never delete a superseded result** — retire it with
> the reason. An inventory of the work completed to date is in
> [THIRD_CHAPTER.md §17](THIRD_CHAPTER.md).

---

## 1. Submission-readiness gate — read this before writing anything

**The paper is not submittable today, and the reason is data, not writing.**

| Requirement for any credible venue | Status |
|---|---|
| Multi-subject dataset | **MISSING** — n = 1 subject, 4 sessions |
| Agreement vs reference (MAE/RMSE/Bland–Altman) | **MISSING** — pilot numbers only, from a single subject |
| Comparison against ≥1 baseline | **MISSING** — never run |
| Coverage at a defensible level | **WEAK** — 10–46% of windows produce an estimate |
| Ethics approval / informed consent for human subjects | **OBTAINED** (confirmed 2026-07-23) — record the reference number for the Methods section (§10) |
| Working system, verified | **DONE** [VERIFIED — 796 passed, 1 xfailed] |
| Pre-registered evaluation methodology | **DONE** [VERIFIED — a genuine differentiator] |
| Reproducible pipeline, seeds, hashes | **DONE** |

**Minimum viable path to submission**, in order:

1. Run a **live hardware smoke test** — the live capture path has not been exercised since
   2026-07-14, and the study cannot afford to discover a broken chain on subject 1.
2. Run the **10-subject × 2-session study**. Ethics approval is in hand; nothing algorithmic
   blocks it.
3. Take **one collision-provoking capture** (4·f_r ≈ HR) to give the central limitation direct
   evidence.
4. Run **baselines** — TI's on-chip vital-signs output plus one reimplemented published pipeline.
5. Improve or explicitly characterise **coverage**.
6. Close the **cross-model DSP review** of the linalg-free path.

Realistically, steps 2–4 are the difference between a rejected manuscript and a competitive one.

**A caution about the framing you may be tempted into.** With MAE around 0.2–0.5 bpm on pilot
data [PRELIMINARY], it is tempting to lead with "sub-bpm accuracy." Do not. Those numbers come
from one subject, on ~10–20% of windows, from sessions that informed the method's design, and
with a single scorable window in one session. A reviewer who notices any of that — and a good
one will — will distrust the whole paper. Our own project history contains exactly this lesson:
a "MAE 0.16 bpm" result was reported internally and had to be withdrawn once the comparator was
pre-registered (§4.1). **Lead with the methodology, report accuracy with coverage attached.**

---

## 2. Target journal comparison

You said high-impact and undecided. Here is the honest landscape for *this* paper.

| Venue | IF (recent) | Scope fit | What they demand | Verdict |
|---|---|---|---|---|
| **IEEE J. Biomedical and Health Informatics (JBHI)** | ~8.2 | **Excellent** — sensing methods validated against clinical references | Multi-subject validation, proper agreement statistics, some clinical framing | **Primary target.** Best ratio of impact to achievability |
| **IEEE Trans. Biomedical Engineering (TBME)** | ~4.4 | **Excellent** — methodological rigour in physiological measurement | Strong methodological novelty; a good home for the comparator argument | **Strong second.** Values the methodology contribution most |
| **npj Digital Medicine** | ~15.1 | **Weak for now** — expects clinical/health-outcome framing, larger and more diverse cohorts | Clinical study design, diverse cohort, health-outcome relevance | Highest impact, but 10 healthy seated subjects will not clear the bar. Revisit only with a clinical population |
| **IEEE Sensors Journal** | ~4.5 | **Good** — sensor systems and signal processing | Solid system paper; lighter validation demands | **Safe fallback.** Fast, respectable, lower impact |
| **IEEE Trans. Microwave Theory & Techniques (T-MTT)** | ~5.2 | **Moderate** — the home of [R5], [R6], [R7], [R15] | RF/microwave novelty | Weak fit: our novelty is DSP and methodology, not RF hardware |
| **Sensors (MDPI)** | ~3.4 | Broad | Fast turnaround | Only if speed matters more than prestige |
| **IEEE J. Electromagnetics, RF and Microwaves in Medicine and Biology (J-ERM)** | ~3 | **Good topical fit** — RF for medicine | Smaller, specialised readership | Reasonable niche fallback |

**Recommendation: target JBHI.** It rewards exactly what this project has (a real system, a
clinical reference, honest agreement statistics) and what makes it distinctive (the comparator
pre-registration). TBME is the better home if you decide the *methodology* is the headline
rather than the sensor. Keep npj Digital Medicine as a later ambition contingent on a clinical
cohort, not as a target for this manuscript.

**Practical notes.** Verify current impact factors at submission time — the values above are
recent but move annually, and the sources disagreed slightly. Check each journal's page limits
and open-access/APC terms before writing; JBHI and TBME have specific formatting and length
expectations that shape §5's word budget.

---

## 3. Positioning and novelty

### 3.1 The strongest available claim

> **The reported accuracy of radar heart-rate systems is not comparable across papers, because
> the comparator that maps a windowed radar estimate onto an instantaneous reference is an
> unreported free parameter — and it can move the headline number by more than the differences
> between the methods being compared.**

We can support this with a direct demonstration: **the same radar output, the same reference,
the same windows, scored two defensible ways, gives MAE 0.16 bpm or 2.72 bpm — a 17× difference**
[PRELIMINARY, §4.1]. That is a result about the field's reporting practice, and it is the most
transferable thing this project has produced. It also does not require 10 subjects to be true,
although a multi-subject demonstration makes it far harder to dismiss.

### 3.2 Supporting claims, ranked by evidential strength

1. **[VERIFIED] Range-bin mislock is a silent, catastrophic failure mode** — a bin ~28 dB below
   the chest produced a confident, entirely spurious ~65 bpm reading that survived harmonic
   verification. Fixed by energy-eligibility partitioning; validated by a committed script
   against all raw streams. Nobody in the surveyed literature reports this failure mode.
2. **[VERIFIED] Harmonic verification cannot, in principle, reject a respiratory harmonic** — a
   respiratory harmonic has genuine second-harmonic structure. This is a structural limitation of
   the AHET family [R1], established here by a documented chain of four failed attempts to
   engineer around it.
3. **[VERIFIED, negative] The coincidence limit (4·f_r ≈ HR) is an identifiability problem**, not
   a resolution or tuning problem — longer windows provably do not help.
4. **[PRELIMINARY] Accuracy–coverage trade-off**: the bin-lock fix *reduced* apparent coverage
   from 36.4% to 19.9% while removing the errors. Coverage-blind accuracy reporting is selection
   bias, and we can demonstrate the magnitude.
5. **[PENDING] Agreement with a clinical reference across subjects** — the conventional
   contribution, and the one currently missing. **Note the scope carefully: across *subjects*, not
   across *distances*.** The protocol deliberately leaves the exact distance free within
   0.8–1.4 m (the warmup auto-locks), so there are no replicated distance strata and a
   per-distance comparison would be confounded with subject. Distance is reported as descriptive
   metadata; claiming per-distance agreement would be claiming a comparison the design cannot
   support.

### 3.3 What is *not* novel — do not claim it

ECA + AHET is [R1]'s method, not ours. We implement it at a different frame rate (20 Hz vs
100 Hz) and window (30 s vs 20 s), on different hardware. Claiming the algorithm as a
contribution invites an easy rejection. **Our contributions are the failure analysis, the
evaluation methodology, and the bin-selection fix.**

### 3.4 Two possible papers

| | **Paper A — "How to evaluate"** | **Paper B — "System + validation"** |
|---|---|---|
| Headline | Comparator pre-registration; coverage-aware reporting; two silent failure modes | A 77 GHz seated HR system validated against a clinical reference across 10 subjects |
| Venue | TBME, JBHI | JBHI, IEEE Sensors J. |
| Needs 10 subjects? | Strongly preferred, not strictly required | **Yes, absolutely** |
| Risk | "Methodology paper without enough data" | Crowded field; needs competitive numbers |
| Current readiness | ~70% | ~25% |

**Recommendation: write Paper A, with the 10-subject study as its evidence base.** It plays to
what the project genuinely has, it is far more defensible, and it is more citable — a paper that
changes how a field reports its numbers outlives a paper that reports slightly better numbers.

---

## 4. Key results to feature

### 4.1 The comparator demonstration [PRELIMINARY — the paper's centrepiece]

Same five radar hops, same Masimo reference:

| Comparator | MAE | Severe errors (>5 bpm) |
|---|---|---|
| Instantaneous PR at the window's end epoch | **0.16 bpm** | 0 |
| PI-gated mean over the same 30 s window | **2.72 bpm** | 1 |

**Root cause:** the reference was genuinely non-stationary (75 → 94 → 65 bpm over ~25 s at
Perfusion Index 7–10, so physiologically real), and the radar's first accepted window straddled
the excursion. **The argument:** a 30 s FFT peak estimates a *dominant frequency*, which is not
the arithmetic mean of instantaneous rates. When the reference moves inside the window there is
no single true value, and no comparator is defensible — so the fix is to detect and exclude that
case, not to pick a cleverer average.

The pre-registered specification (median PI-gated PR, ≥80% sample coverage, and a stationarity
gate at 5 bpm derived from the 2 bpm FFT bin width — 2.5 bins, not a tuned value) follows from
that argument. Report exclusion sensitivity at 3 / 5 / 8 bpm so the choice is auditable.

### 4.2 Pilot agreement [PRELIMINARY — report with every caveat attached]

| Session | AHET-accepted | Excluded (non-stationary reference) | Scorable | MAE | Severe |
|---|---|---|---|---|---|
| natural | 5 / 50 | 4 | 1 | 0.19 bpm | 0 |
| paced-16 | 23 / 50 | 2 | 21 | 0.50 bpm | 0 |
| sweep | 30 / 150 | 11 | 19 | 0.53 bpm | 0 |

Mandatory accompanying statements: **n = 1 subject**; these sessions **informed the method's
design** (exploratory, not held-out); `natural` rests on **one** scorable window; coverage is
10–46%. All 17 excluded windows were individually checked and every one is a real reference
instability (spread 5.1–26.0 bpm) — pre-empt the "what did your exclusion gate hide?" question,
because it is the first thing a reviewer will ask about a gate.

### 4.3 The mislock case study [VERIFIED — the most persuasive single figure]

A ~63–67 bpm peak, stable over t = 70–169 s, that passed harmonic verification and was
attributable to nothing in the DSP — because the fault was upstream: the warmup had locked a bin
**28.1 dB** below the chest. On one window (t = 147 s): mislocked bin **64.4 bpm**, correct bin
**81.8 bpm**, reference **81.0 bpm**.

**The defect was in the selector's scoring.** The weak bin was the only candidate whose HR DSP
returned a result, earning a score bonus that put it at **1265 vs 295** for the true chest bin —
the selector rewarded "produced an answer" without checking there was enough signal to support
one. The fix gates that bonus on energy within −12 dB of the strongest candidate, measured
**after a 5 s settle skip**. The skip is necessary, not cosmetic: over the full warmup the bad
bin reads −11.0 dB (inside the gate); only after the skip does its true −28.1 dB appear. Result:
bonus vetoed, score 1265 → 265, correct bin wins. Validated by
`scripts/validate_warmup_selection.py` against all four sessions' raw streams; figure:
`figures/fig_range_bin_mislock.py`.

*Mechanism caveat:* that the first 5 s inflate weak bins is measured; **why** is not. Plausibly
the static-clutter removal (a per-bin slow-time mean, poorly estimated from few frames, leaking
residual static energy into low-signal bins) — reasoning, not a result. Do not publish it as one.

**State the scope honestly:** the −12 dB / 5 s thresholds are an empirical, scene-scoped prior
from 4 sessions / 1 subject, in a scene where a single seated subject dominates the range gate.
Untested with competing reflectors. Overclaiming here is unnecessary — the *failure mode* is the
contribution, and it is general even where the threshold is not.

### 4.4 Negative results worth publishing

Fixed-threshold harmonic proximity (over-triggers); adaptive k_max (too blunt); ECA v1 measured
at **0.00 dB removed** in the cardiac band at low breathing rates — i.e. **the production
harmonic cancellation is inert in the regime we mostly operate in**; ECA v2 rejected because
cancellation degrades as k·Δf_r; harmonic coherence (Stage 1A) failed — the high-order
respiratory evidence is not a coherent single-sinusoid line.

Most venues will not let you spend much space here, but two or three of these earn their place
because they explain *why* the method looks the way it does. Publishing the inert-ECA measurement
in particular is an unusual piece of honesty that strengthens rather than weakens the paper.

---

## 5. Manuscript outline

Assumes a ~9,000-word JBHI/TBME-style paper; compress for IEEE Sensors J.

| Section | Content | Words | Source |
|---|---|---|---|
| **Title** | §6 | — | — |
| **Abstract** | Problem → gap (comparator + silent failures) → what we did → key numbers with coverage → implication | 200 | — |
| **I. Introduction** | Contactless monitoring motivation [R7, R11]; harmonic interference problem; the two gaps (unreported comparator, unreported coverage); contribution list | 1000 | CH §1 |
| **II. Related work** | Table of the six harmonic-interference approaches; foundations [R5–R8]; explicit statement that none pre-register a comparator or report coverage | 1200 | CH §4 |
| **III. Signal model and system** | FMCW ranging, phase-displacement relation (3.2 rad/mm at 77 GHz), the harmonic sum model, hardware and chirp table, protocol | 1500 | CH §2, §3 |
| **IV. Method** | ECA + AHET spec with equations; deviations from [R1] declared; **warmup bin selection with the energy-eligibility rule**; diagnostics commitment | 1800 | CH §5, §6, §9 |
| **V. Evaluation methodology** | The comparator problem; the pre-registered spec; why 5 bpm is derived from FFT resolution; non-overlapping windows; coverage-with-accuracy rule; Bland–Altman [R16] | 1300 | CH §7 |
| **VI. Results** | Comparator demonstration; per-subject agreement + Bland–Altman [PENDING]; coverage; mislock case study; baselines [PENDING]; 18 bpm arm reported separately | 1800 | CH §10 |
| **VII. Discussion** | Coincidence identifiability limit; why harmonic verification cannot reject a respiratory harmonic; accuracy–coverage trade-off; what this implies for reading the literature | 1200 | CH §12 |
| **VIII. Limitations** | Explicit, unhedged (§8 below) | 500 | CH §12.6 |
| **IX. Conclusion** | 300 | — | — |

**Two structural notes.** First, Section V is unusually long for a sensing paper — that is
deliberate, because it carries the main claim; do not let a co-author compress it into Section VI.
Second, put the contribution list at the end of Section I as an explicit bulleted list; reviewers
of methodology papers look for it there.

---

## 6. Title and abstract drafts

**Title options** (all avoid claiming accuracy we cannot yet defend):

1. *"How You Compare Matters: Pre-Registered Evaluation of Contactless Heart-Rate Estimation with
   a 77 GHz FMCW Radar"* — leads with the methodology claim.
2. *"Coverage, Comparators, and Silent Failures in mmWave Radar Heart-Rate Estimation"* — leads
   with the three findings; strong for TBME.
3. *"Contactless Heart-Rate Estimation with a 77 GHz FMCW Radar: A Pre-Registered Agreement Study
   Against Pulse Oximetry"* — conventional, best for JBHI, safest.

**Abstract skeleton** (fill bracketed values from the 10-subject study):

> Contactless heart-rate estimation from millimetre-wave radar is limited by respiratory
> harmonics that fall inside the cardiac band. Reported accuracies across the literature are
> difficult to compare, because the mapping from a windowed radar estimate to an instantaneous
> physiological reference is an unreported degree of freedom. We implement an extensive
> cancellation and harmonic-verification pipeline on a 77 GHz FMCW radar for seated subjects at
> 0.8–1.4 m, and evaluate it against a fingertip pulse oximeter under a comparator specification
> pre-registered before data collection. We show that the same radar output, scored against the
> same reference by two defensible comparators, differs by [17×] in mean absolute error —
> exceeding the reported differences between competing methods. We further identify two silent
> failure modes: a range-bin selection error that produces a confident but entirely spurious
> heart rate [28 dB below the true chest return], and a structural limitation whereby
> second-harmonic verification cannot reject a respiratory harmonic. Across [N] subjects, the
> system achieves [MAE] bpm on [coverage]% of windows [with Bland–Altman limits of agreement of
> ±X bpm]. We argue that agreement claims in this field require a pre-registered comparator and
> must report coverage alongside accuracy.

---

## 7. Figures and tables [all PENDING generation]

Each must come from a committed script in `figures/` — no hand-edited figures.

**Main text (target 7–8):**
1. System block diagram — hardware, signal chain, live/offline separation.
2. Signal model — respiration and cardiac spectra with harmonics annotated, cardiac band shaded,
   the 4·f_r collision marked.
3. **The mislock figure** — correct bin vs skirt bin, phase spectra side by side, spurious peak
   annotated. *Most persuasive single figure in the paper; the data already exists.*
4. ECA before/after spectra with cancelled harmonics annotated.
5. **The comparator figure** — same hops scored two ways; the 17× gap made visual.
6. **Bland–Altman** — bias and 95% limits of agreement, **subject-clustered (repeated
   measurements)**. `scripts/plot_bland_altman.py` may be reused for *plotting only*: its
   statistics pool every window as an independent pair (`se_loa = sqrt(3·SD²/n)`, no subject or
   session term), which for 10 subjects × 2 sessions × ~19 windows yields falsely narrow limits.
   The model must be pre-specified in the deposit — see `plans/implementation_plan.md` M0/M4.
7. Radar HR vs Masimo PR time series, rejected windows shaded.
8. Coverage vs accuracy trade-off across gate settings.

**Tables:** (T1) related-work comparison; (T2) chirp/hardware configuration; (T3) per-subject
agreement with coverage; (T4) stationarity-gate exclusion sensitivity at 3/5/8 bpm.

**Supplementary:** AHET verification illustration; the 18 bpm coincidence arm; per-session
diagnostics.

---

## 8. Limitations to state explicitly

Reviewers penalise concealment far more than limitation. State all of these plainly:

- Single posture (seated), single geometry (frontal chest), narrow distance range (0.8–1.4 m).
- Healthy adult volunteers; no clinical population, no arrhythmia, no demographic diversity
  analysis.
- Single-antenna, single-range-bin processing; 4 RX captured but unused for beamforming.
- 20 Hz frame rate, 5× below the primary reference's 100 Hz.
- **Pulse oximeter PR is not ECG-derived HR.** PR and R-R-derived HR differ physiologically; the
  reference has its own error, which bounds any agreement claim from below.
- **Coverage is 10–46%** on pilot data. Report it; do not bury it.
- The **respiration-collapse bug** (f_r pins to the 6 bpm search floor while the validity flag
  stays true, observed on **3/3 Masimo-referenced captures**, and absent from the one unreferenced
  capture) is unresolved — **do not report breathing rate in this paper** unless it is fixed first.
- The −12 dB eligibility threshold is scene-scoped and untested with competing reflectors.
- The independent cross-model review of the linalg-free DSP path is not complete.
- **[RETIRED] numbers from data deleted 2026-07-09 must never appear** — including exp001
  (MAE 6.21 bpm) and exp002 (MAE 5.29 bpm). The *mechanism* evidence from those experiments may
  be cited qualitatively; the numbers may not.

---

## 9. Anticipated reviewer objections

| Objection | Response |
|---|---|
| *"Only N subjects, all healthy adults, seated."* | Concede in Limitations; scope the claims to that population explicitly in the abstract. Do not over-generalise |
| *"Your exclusion gate could hide radar errors."* | Every excluded window was individually inspected; all are genuine reference instabilities (spread 5.1–26.0 bpm). Report the sensitivity table (T4) |
| *"MAE < 1 bpm is implausible for radar."* | **This is the dangerous one.** It is on 10–46% of windows, non-overlapping, on a small sample. Present coverage in the same sentence, every time. Never quote MAE alone |
| *"ECA + AHET is [R1]'s method — what is new?"* | The comparator methodology, the bin-selection failure and fix, and the structural analysis of why harmonic verification cannot reject a respiratory harmonic. Say so in the contribution list |
| *"Why not compare with an ECG?"* | Concede — an ECG reference would be stronger. Justify the oximeter (clinical device, PR + a perfusion-quality signal enabling the reference-quality gate) and list ECG as future work |
| *"Why 30 s? It is long for a real-time claim."* | Frequency resolution: Δf = 1/T gives ~2 bpm at 30 s, ~3 bpm at 20 s. Shortening coarsens accuracy and degrades the warmup bin lock |
| *"Coverage is low."* | Concede directly. Argue that an honest abstention is preferable to a confident wrong reading, and that AHET conservatism at the correct bin is a characterised, quantified trade — this is a finding, not an excuse |
| *"Statistical independence of your windows?"* | Non-overlapping 30 s windows only; the 3 s hop is display cadence and is never used for statistics |

---

## 10. Pre-submission logistics

- **Ethics / IRB — OBTAINED** (confirmed by the author, 2026-07-23). Two follow-ups remain:
  **record the approval reference number and issuing board** — journals require both in the
  Methods section and an editor will ask — and **confirm the approval's scope covers what you
  intend to do with the data**, specifically any public release of recordings under a data
  availability statement (below). Approval to *collect* does not always include approval to
  *share*, and that distinction is easier to resolve now than at revision.
- **Data availability statement.** Increasingly mandatory. Decide now whether raw `.bin` captures
  (**~1.55 GB per 10-minute session, ~31 GB for the 20-session study**) can be shared, and where.
  At that size, releasing processed phase signals plus code is the realistic option if raw sharing
  is impractical. Note the ethics follow-up above: approval to *collect* is not automatically
  approval to *share*.
- **Code availability.** The repository is a genuine strength — tests, pinned environment, seeds,
  input hashing, pre-registered specifications. Plan a cleaned public release; it is an easy
  credibility win in review.
- **Pre-registration — and state its strength precisely.** `notes/comparator_prespec.md` was
  registered internally before the relevant captures. Deposit it publicly (OSF or similar)
  **before** the pilot, so the claim is externally verifiable rather than self-asserted. This
  materially strengthens the paper's central argument.

  > **The defensible claim is "frozen before the confirmatory data", NOT "before any data
  > existed."** Four captures already exist and they **informed the method's design** (see
  > [THIRD_CHAPTER.md §10.1](THIRD_CHAPTER.md) — exploratory, not held-out), so no deposit made now
  > can predate the data that shaped the method. What the deposit *can* do — and what carries the
  > argument — is predate every capture the agreement numbers are computed on. The deposit should
  > therefore **enumerate every capture in existence at freeze time** and label it exploratory.
  > Overstating this is the same class of error as the withdrawn "MAE 0.16 bpm" (§4.1), and a
  > reviewer who checks the dates will catch it.
- **Preprint.** arXiv (eess.SP) is standard in this field and compatible with IEEE policy.
- **Author contributions, funding, conflicts** — collect early.

---

## 11. References

Verified citations; `[CITATION NEEDED]` items must be completed from the actual documents. Full
annotated versions with the role each plays in the argument are in
[THIRD_CHAPTER.md §14](THIRD_CHAPTER.md).

**Method and comparison**
- **[R1]** Tang et al., "Adaptive Extensive Cancellation Algorithm and Harmonic Enhanced Heart
  Rate Estimation based on MMWave Radar," arXiv:2503.07062, 2025. *(Primary method)*
- **[R2]** Hsieh et al., "Harmonic MUSIC Method for mmWave Radar-based Vital Sign Estimation,"
  arXiv:2408.01951, 2024.
- **[R3]** Iwata et al., "Accurate Radar-Based Heartbeat Measurement Using Higher Harmonic
  Components," arXiv:2407.07380, 2024.
- **[R4]** Shimomura et al., "A Nonlinear Spectral Approach for Radar-Based Heartbeat Estimation
  via Autocorrelation of Higher Harmonics," arXiv:2507.20664, 2025.
- **[R13]** Gu et al., "Improved VMD Based Remote Heartbeat Estimation Utilizing 60GHz mmWave
  Radar," arXiv:2502.11042, 2025.
- **[R14]** Zhang et al., "Pi-ViMo: Physiology-inspired Robust Vital Sign Monitoring using mmWave
  Radars," arXiv:2303.13816, 2023.
- **[R15]** Beltrão, G., et al., "Adaptive Nonlinear Least Squares Framework for Contactless Vital
  Sign Monitoring," *IEEE Trans. Microwave Theory Tech.*, vol. 71, no. 4, 2023.

**Foundations**
- **[R5]** Droitcour, A.D., et al., "Range correlation and I/Q performance benefits in single-chip
  silicon Doppler radars for non-contact cardiopulmonary monitoring," *IEEE Trans. Microwave
  Theory Tech.*, vol. 52, no. 3, pp. 838–848, 2004.
- **[R6]** Park, B.-K., Boric-Lubecke, O., Lubecke, V.M., "Arctangent Demodulation with DC Offset
  Compensation in Quadrature Doppler Radar Receiver Systems," *IEEE Trans. Microwave Theory
  Tech.*, vol. 55, no. 5, pp. 1073–1079, 2007.
- **[R7]** Li, C., Lubecke, V.M., Boric-Lubecke, O., Lin, J., "A Review on Recent Advances in
  Doppler Radar Sensors for Noncontact Healthcare Monitoring," *IEEE Trans. Microwave Theory
  Tech.*, vol. 61, no. 5, pp. 2046–2060, 2013.
- **[R8]** Alizadeh, M., Shaker, G., De Almeida, J.C.M., et al., "Remote Monitoring of Human Vital
  Signs Using mm-Wave FMCW Radar," *IEEE Access*, vol. 7, pp. 54958–54968, 2019.
- **[R11]** Beltrão, G., Stutz, R., Hornberger, F., Martins, W.A., et al., "Contactless
  radar-based breathing monitoring of premature infants in the neonatal intensive care unit,"
  *Scientific Reports*, vol. 12, 2022. *(Clinical-deployment motivation)*

**Statistics and standards**
- **[R16]** Bland, J.M., Altman, D.G., "Statistical Methods for Assessing Agreement between Two
  Methods of Clinical Measurement," *The Lancet*, vol. 327, no. 8476, pp. 307–310, 1986.
- **[R17]** ANSI/AAMI EC13, Cardiac Monitors, Heart Rate Meters, and Alarms. `[CITATION NEEDED —
  obtain the standard; quote the exact accuracy clause and edition. The commonly-cited "±10% or
  ±5 bpm, whichever is greater" could NOT be verified from open sources — do not cite it from
  memory.]`
- **[R18]** ISO 80601-2-61, pulse oximeter equipment. `[CITATION NEEDED — edition and pulse-rate
  accuracy clause.]`

**Hardware and reference device**
- **[R9]** Texas Instruments, *IWR1642BOOST Evaluation Module User's Guide*. `[CITATION NEEDED —
  literature number; PDF in literature/IWR1642/.]`
- **[R10]** Texas Instruments, *DCA1000EVM Data Capture Card User's Guide*, **SPRUIJ4**. `[Verify
  revision.]`
- **[R19]** Texas Instruments, mmWave SDK User's Guide. `[CITATION NEEDED — literature number.]`
- **[R20]** Texas Instruments, vital-signs measurement with mmWave sensors application note.
  `[CITATION NEEDED — no document number could be verified; required as the on-chip baseline
  citation.]`
- **[R12]** Masimo Corporation, *MightySat Fingertip Pulse Oximeter* operator's manual.
  `[CITATION NEEDED — model, revision, and the stated pulse-rate accuracy specification. Cite the
  device documentation, not marketing claims.]`

---

## 12. Immediate next actions

1. **Run a live hardware smoke test** before subject 1 — the live capture path has not been
   exercised since 2026-07-14. This is now the first blocker.
2. **Publicly deposit the comparator pre-registration** before the study — it converts the
   paper's main claim from self-asserted to verifiable. Time-sensitive: it only counts as a
   pre-registration if it is deposited *before* the data it governs is collected.
3. **Run the 10-subject study**, including the collision-provoking arm. Ethics approval is in
   hand; log the reference number for the Methods section.
4. **Choose Paper A or Paper B** (§3.4) once the data is in and you can see how the numbers land.
5. **Run the baselines** — the single most common reason a paper like this gets rejected.
6. **Complete every `[CITATION NEEDED]`** in §11.
