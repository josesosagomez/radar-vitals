> # ⚠ PRE-REGISTRATION CLAIMS IN THIS FILE ARE VOID — 2026-08-03
>
> **M0 (pre-registration freeze and deposit) was removed from the project by user decision.**
> No deposit exists, no DOI exists, and **nothing in this study is pre-registered.**
>
> Every occurrence below of "pre-registered", "pre-specified", "frozen before data",
> "deposited", "registered <date>" or "confirmatory" **about this study's own results** is
> obsolete and must not reach the manuscript. The comparator specifications still exist and are
> still applied — they are **internal engineering specs** (`notes/comparator_prespec*.md`,
> `notes/analysis_prespec.md`), not registrations, and must be described that way.
>
> **This affects the planned headline.** "Comparator pre-registration" was listed here as the
> paper's primary novelty; that claim is no longer available. The strongest remaining candidate
> is the **real-data evaluation of two simulation-only published methods** (M8 Ahmed,
> M9 Kotte) under one common comparator with coverage reported. **M8 Ahmed and M9 Kotte are
> complete; multi-subject validation and the remaining baseline comparison are still pending.**
>
> See `plans/implementation_plan.md` "Track 0", `HANDOFF.md` §4, `HISTORY.md` 2026-08-03.

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
> M8 Ahmed results synchronised with the repository **2026-08-08**, branch
> `vital_signs_ahmed_v11`; authoritative report `reports/m8_ahmed_correction_final_report.md`.
>
> **Corrections applied 2026-07-24** after a cross-model review of `plans/implementation_plan.md`
> (see `HISTORY.md`, same date): the test count, the respiration-collapse count (4/4 → 3/3), the
> Bland–Altman treatment (pooled-independent is invalid for a repeated-measures design), the
> per-distance claim (downgraded to descriptive — the protocol does not stratify distance), the
> the comparator specification's status (§10 — see the 2026-08-03 banner), and the session size
> (recordings are now 10 min).
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
| Multi-subject dataset | **WEAK** — n = 4 subjects, 8 sessions (corrected 2026-08-03; previously recorded as n = 1) |
| Agreement vs reference (MAE/RMSE/Bland–Altman) | **MISSING** — pilot numbers only, n = 4 subjects |
| Comparison against ≥1 published method | **PARTIAL** — Ahmed HA and Kotte joint-Doppler complete; TI on-chip comparison remains |
| Coverage at a defensible level | **WEAK** — 10–46% of windows produce an estimate |
| Ethics approval / informed consent for human subjects | **OBTAINED** (confirmed 2026-07-23) — record the reference number for the Methods section (§10) |
| Working system, verified | **DONE** [VERIFIED — 796 passed, 1 xfailed] |
| Explicit, auditable evaluation methodology | **DONE** — specified and applied consistently. *Not* pre-registered (M0 removed 2026-08-03), so it is a transparency contribution, not a timing claim |
| **Real-data evaluation of two simulation-only published methods** | **PARTIAL** — M8 Ahmed HA and M9 Kotte complete; multi-subject validation remains |
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
from four subjects, on ~10–20% of windows, from sessions that informed the method's design, and
with a single scorable window in one session. A reviewer who notices any of that — and a good
one will — will distrust the whole paper. Our own project history contains exactly this lesson:
a "MAE 0.16 bpm" result was reported internally and had to be withdrawn once the comparator was
written down explicitly (§4.1). **Lead with the method comparison, report accuracy with coverage
attached.**

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
clinical reference, honest agreement statistics) and what makes it distinctive (a real-data
evaluation of two published simulation-only methods, scored under one common comparator with
coverage reported). TBME is the better home if you decide the *methodology* is the headline
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
| Headline | Real-data evaluation of two simulation-only published methods under one common comparator; coverage-aware reporting; two silent failure modes | A 77 GHz seated HR system validated against a clinical reference across 10 subjects |
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

The specification we adopt (median PI-gated PR, ≥80% sample coverage, and a stationarity gate at
5 bpm derived from the 2 bpm FFT bin width — 2.5 bins, not a tuned value) follows from that
argument. It is stated in full and applied uniformly to every estimator compared, which is what
makes the comparison meaningful; it is **not** a pre-registration, and must not be described as
one. Report exclusion sensitivity at 3 / 5 / 8 bpm so the choice is auditable.

### 4.2 Pilot agreement [PRELIMINARY — report with every caveat attached]

| Session | AHET-accepted | Excluded (non-stationary reference) | Scorable | MAE | Severe |
|---|---|---|---|---|---|
| natural | 5 / 50 | 4 | 1 | 0.19 bpm | 0 |
| paced-16 | 23 / 50 | 2 | 21 | 0.50 bpm | 0 |
| sweep | 30 / 150 | 11 | 19 | 0.53 bpm | 0 |

Mandatory accompanying statements: **n = 4 subjects**; these sessions **informed the method's
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
from 4 sessions, in a scene where a single seated subject dominates the range gate.
Untested with competing reflectors. Overclaiming here is unnecessary — the *failure mode* is the
contribution, and it is general even where the threshold is not.

### 4.4 Ahmed harmonic accumulation: real-data transfer result [EXPLORATORY]

Ahmed et al.'s Section III-C method was evaluated as six declared profiles—H=3/H=5 crossed
with visible-unsuppressed, Eq. 26 multiples-suppressed, and prose low-or-equal-suppressed
interpretations—alongside the production estimator on the same cubes, windows, locks, and run
identity. The canonical experiment comprised eight captures, two locks, seven arms, 1,792
estimator rows, and 3,584 scored HR/BR rows. k=0 was excluded from comparative metrics, and
natural, paced, and unknown protocol strata were not pooled.

The table reports the range across all six Ahmed profiles, not a selected winner:

| Vital / protocol | Recorded-lock MAE / RMSE (bpm) | Current-lock MAE / RMSE (bpm) | Joint coverage |
|---|---:|---:|---:|
| HR / natural | 4.55–7.35 / 6.51–9.03 | 4.25–9.05 / 7.39–10.93 | 1.000 |
| HR / paced | 23.46–25.46 / 24.52–26.34 | 17.77–18.85 / 22.66–23.74 | 0.650 |
| HR / unknown | 31.65–33.57 / 32.77–34.42 | 31.65–33.57 / 32.77–34.42 | 0.505 |
| BR / natural | 9.00–11.40 / 10.23–11.55 | 11.40–11.80 / 11.45–11.86 | 1.000 |
| BR / paced | 8.85–9.35 / 9.52–9.86 | 8.95–9.35 / 9.65–9.85 | 1.000 |
| BR / unknown | 10.30–11.16 / 10.69–11.34 | 10.30–11.16 / 10.69–11.34 | 0.842 |

HR bias was predominantly negative: −2.15 to −7.35 bpm in natural data, −17.00 to −25.46 bpm
in paced data, and −31.65 to −33.57 bpm in the unknown-protocol captures. Thus, the Ahmed code
ran and returned radar-valid outputs, but the declared FMCW phase adaptation did **not** provide
accurate HR or BR estimates on these recordings. Joint coverage is measured after reference
admission and must not be confused with estimator validity.

The successor Figure 8 control also failed honestly: for an 80 bpm heart target, the
visible-unsuppressed H=3 result was 40.0144 bpm and H=5 was 20.0072 bpm; both recovered the
20 bpm breath target as 20.0072 bpm. No profile or assumption was selected using Masimo error.

**Claim boundary:** these are descriptive development-data results with an approximate capture
origin (5–15 s uncertainty), not final agreement or population evidence. They test the project's
FMCW unwrapped-phase adaptation (`q=f`, `60q bpm`), not Ahmed's original pulse-radar mapping
(`q=2f`, `30q bpm`). Exact per-profile values and provenance are in
`reports/m8_ahmed_correction_final_report.md`.

### 4.5 Kotte joint Doppler: controlled transfer to FMCW chest data [VERIFIED IMPLEMENTATION; EXPLORATORY AGREEMENT]

Kotte et al.'s published method jointly searches two Doppler frequencies using the transposed
selected-bin matrix `Y_t = Y(kappa)^T`. For each pair, the implementation forms
`A=[a(f1) a(f2)]`, `R_t=Y_tY_t^H/n_R`, `H=A^H R_t^{-1}A`, and the constrained weight
`w=R_t^{-1}AH^{-1}[1,1]^T` (Eq. 25). Algorithm 1's primary objective is
`J=w^H R_t w=1^H H^{-1}1`; the Eq. 26 joint-coefficient surface is retained only as a
direct-synthetic diagnostic. The real-data path omits DOA and Eq. 26 and selects with the
regularized Algorithm 1 surface. Pair columns are interchangeable, equal/ill-conditioned pairs
are masked, and physical frequency is converted to `60|f|` bpm. The signed domains are
`±0.10–0.50 Hz` (breathing) and `±0.80–2.00 Hz` (heart), sampled at `1/120 Hz` (0.5 bpm).
The printed steering vector omits `T_PRI`; the implementation includes `T_PRI=0.05 s` as the
declared physical-Hz interpretation. Within disjoint bands the lower candidate is labelled
breathing and the higher candidate heart; Masimo is never used for this assignment.

For the literal surface, the selected pair is the deterministic lexicographic tie-broken argmax
over admissible grid cells. The real-data path instead maximizes the loaded
`regularized_kotte_power` surface over the same admissibility rules.

The paper's 20-RX DOA/range simulation is not reproduced on this hardware. The project adapter
reuses the unchanged warmup range bin, omits DOA, and processes each complex64 cube
`(600,32,4,256)` by fixed chirp-loop-0 extraction to `Z (600,4)` complex128. After first-592
support retention and per-RX mean removal, 37 `(16,4)` CPIs are evaluated at 0.05-s slow-time
spacing. Because four RX channels make the temporal covariance rank-deficient, both declared
loaded arms (`delta=1e-2` primary project arm, `1e-4` loading sensitivity) are reported
separately using the explicit regularized objective `1^H H_delta^{-1}1`.

Direct-cisoid and phase-ramp controls passed; chest-displacement transfer failure (0/24 chest
cases and 0/10 robustness cases) is a nondeployable diagnostic consistent with two-line model
limitations. The canonical run
covered eight captures, 128 windows and 256 arm rows; all radar rows and CPIs were algorithmically
valid. `k=0` was excluded from comparison. Reference admission was 66/120 HR and 105/120 BR
cells; 30 duplicate reference seconds were normalized by the existing parser. All scoring is
`exploratory_non_frozen` and promotion-ineligible.

| Vital / protocol | `delta` | Scored / total | Coverage | MAE / RMSE (bpm) | Bias (bpm) |
|---|---:|---:|---:|---:|---:|
| HR / natural | 1e-2 | 53 / 100 | 0.53 | 29.434 / 30.608 | −29.434 |
| HR / natural | 1e-4 | 53 / 100 | 0.53 | 29.368 / 30.911 | −29.368 |
| HR / paced | 1e-2 | 5 / 5 | 1.00 | 19.100 / 20.059 | −19.100 |
| HR / paced | 1e-4 | 5 / 5 | 1.00 | 10.700 / 12.106 | −10.500 |
| HR / stepped | 1e-2 | 8 / 15 | 0.53 | 27.438 / 27.854 | −27.438 |
| HR / stepped | 1e-4 | 8 / 15 | 0.53 | 26.938 / 27.299 | −26.938 |
| BR / natural | 1e-2 | 85 / 100 | 0.85 | 6.406 / 7.547 | +2.688 |
| BR / natural | 1e-4 | 85 / 100 | 0.85 | 5.447 / 6.632 | −3.188 |
| BR / paced | 1e-2 | 5 / 5 | 1.00 | 10.700 / 11.039 | +10.700 |
| BR / paced | 1e-4 | 5 / 5 | 1.00 | 8.200 / 9.482 | +8.200 |
| BR / stepped | 1e-2 | 15 / 15 | 1.00 | 11.800 / 12.172 | +11.800 |
| BR / stepped | 1e-4 | 15 / 15 | 1.00 | 11.300 / 11.860 | +11.300 |

M9.1 literal post-range/`Y_t` 0-dB R1/R2/R3 controls were
`not_reproduced_under_declared_assumptions`. The separate 21.0721-dB FFT-gain interpretation
reproduced selected behavior but remains unsettled and nonprimary. These implementation-only
controls have `thesis_evidence_eligible=false` and are outcome-decision-ineligible; they are not
thesis outcome evidence or real-data rules. HR remains descriptive beside `constant_session_median` because timing is approximate and the
sessions have little within-session HR dynamic range. Pair margins were tiny (minimum about
`1.8e-5 dB`). The result is a negative transfer finding for this fixed-range, 4-RX adaptation,
not evidence against Kotte's original 20-RX simulation method.

### 4.6 Negative results worth publishing

Fixed-threshold harmonic proximity (over-triggers); adaptive k_max (too blunt); ECA v1 measured
at **0.00 dB removed** in the cardiac band at low breathing rates — i.e. **the production
harmonic cancellation is inert in the regime we mostly operate in**; ECA v2 rejected because
cancellation degrades as k·Δf_r; harmonic coherence (Stage 1A) failed — the high-order
respiratory evidence is not a coherent single-sinusoid line.

Most venues will not let you spend much space here, but two or three of these earn their place
because they explain *why* the method looks the way it does. Publishing the inert-ECA measurement
in particular is an unusual piece of honesty that strengthens rather than weakens the paper.
The corrected Ahmed FMCW transfer is also a publishable negative result: extensive independent
controls passed, yet errors remained large and strongly low-biased, so poor agreement was not
hidden by tuning or a post-hoc profile choice.

---

## 5. Manuscript outline

Assumes a ~9,000-word JBHI/TBME-style paper; compress for IEEE Sensors J.

| Section | Content | Words | Source |
|---|---|---|---|
| **Title** | §6 | — | — |
| **Abstract** | Problem → gap (two published methods never tested on real data; comparator + silent failures) → what we did → key numbers with coverage → implication | 200 | — |
| **I. Introduction** | Contactless monitoring motivation [R7, R11]; harmonic interference problem; the gaps (two published simulation-only methods; unreported comparators; unreported coverage); contribution list | 1000 | CH §1 |
| **II. Related work** | Table of the six harmonic-interference approaches; foundations [R5–R8]; original Ahmed and Kotte evidence was simulation-only; declare the project's Ahmed FMCW adaptation and its pulse-radar claim boundary | 1200 | CH §4, §10.3 |
| **III. Signal model and system** | FMCW ranging, phase-displacement relation (3.2 rad/mm at 77 GHz), the harmonic sum model, hardware and chirp table, protocol | 1500 | CH §2, §3 |
| **IV. Method** | ECA + AHET spec with equations; deviations from [R1] declared; **warmup bin selection with the energy-eligibility rule**; diagnostics commitment | 1800 | CH §5, §6, §9 |
| **V. Evaluation methodology** | The comparator problem; the explicit comparator specification applied uniformly to every estimator; why 5 bpm is derived from FFT resolution; non-overlapping windows; coverage-with-accuracy rule; Bland–Altman [R16] | 1300 | CH §7 |
| **VI. Results** | Comparator demonstration; Ahmed two-lock/six-profile real-data result; per-subject agreement + Bland–Altman [PENDING]; coverage; mislock case study; remaining baselines [PENDING] | 1800 | CH §10 |
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

1. *"Do They Work on Real Radar? Empirical Evaluation of Two Simulation-Only Methods for
   mmWave Vital-Sign Estimation"* — leads with the headline claim.
2. *"Coverage, Comparators, and Silent Failures in mmWave Radar Heart-Rate Estimation"* — leads
   with the findings; strong for TBME.
3. *"Contactless Heart-Rate and Breathing-Rate Estimation with a 77 GHz FMCW Radar: An Agreement
   Study Against Pulse Oximetry"* — conventional, best for JBHI, safest.

> **Do not reintroduce "pre-registered" into any title.** M0 was removed 2026-08-03; the claim is
> unavailable. See the banner at the top of this file.

**Abstract skeleton** (fill bracketed values from the 10-subject study):

> Contactless heart-rate estimation from millimetre-wave radar is limited by respiratory
> harmonics that fall inside the cardiac band. Reported accuracies across the literature are
> difficult to compare, and two published simulation methods — Ahmed's harmonic accumulation
> [R1] and Kotte's joint high-amplitude-difference Doppler [R21] — were originally evaluated
> **only in simulation**.
> We evaluate harmonic accumulation and joint Doppler on real FMCW radar data, alongside an extensive-cancellation and
> harmonic-verification pipeline, on a 77 GHz FMCW radar for seated subjects at 0.8–1.4 m,
> validated against a fingertip pulse oximeter. All estimators are scored on identical
> non-overlapping windows under a single explicitly stated comparator, with coverage reported
> alongside accuracy. [Headline finding: which methods transfer to real data and which do not.]
> We further show that the mapping from a windowed radar estimate to an instantaneous
> physiological reference is itself an unreported degree of freedom: the same radar output,
> scored against the same reference by two defensible comparators, differs by [17×] in mean
> absolute error — exceeding the reported differences between competing methods. We identify two
> silent failure modes: a range-bin selection error that produces a confident but entirely
> spurious heart rate [28 dB below the true chest return], and a structural limitation whereby
> second-harmonic verification cannot reject a respiratory harmonic. Across [N] subjects the
> system achieves [MAE] bpm on [coverage]% of windows [with Bland–Altman limits of agreement of
> ±X bpm]. We argue that simulation-only results should not be cited as solutions until tested on
> real data, and that agreement claims in this field must state their comparator and report
> coverage alongside accuracy.

---

## 7. Figures and tables

Each must come from a committed script in `figures/` — no hand-edited figures.

**Main text (target 7–8):**
1. System block diagram — hardware, signal chain, live/offline separation.
2. Signal model — respiration and cardiac spectra with harmonics annotated, cardiac band shaded,
   the 4·f_r collision marked.
3. **Ahmed Figure 8 successor [GENERATED]** — retain its explicit “not reproduced under declared
   assumptions” title; source and canonical outputs are under `figures/generated/m8_ahmed_fig8/`.
4. **The mislock figure** — correct bin vs skirt bin, phase spectra side by side, spurious peak
   annotated. *Most persuasive single figure in the paper; the data already exists.*
5. ECA before/after spectra with cancelled harmonics annotated.
6. **The comparator figure** — same hops scored two ways; the 17× gap made visual.
7. **Bland–Altman** — bias and 95% limits of agreement, **subject-clustered (repeated
   measurements)**. `scripts/plot_bland_altman.py` may be reused for *plotting only*: its
   statistics pool every window as an independent pair (`se_loa = sqrt(3·SD²/n)`, no subject or
   session term), which for 10 subjects × 2 sessions × ~19 windows yields falsely narrow limits.
   The model is specified in `notes/analysis_prespec.md` §1 (an internal engineering spec, not a
   registration) and must be stated in the Methods.
8. Radar HR vs Masimo PR time series, rejected windows shaded.
9. Coverage vs accuracy trade-off across gate settings.

**Tables:** (T1) related-work comparison; (T2) chirp/hardware configuration; (T3) per-subject
agreement with coverage; (T4) Ahmed profile × lock × protocol accuracy/coverage; (T5)
stationarity-gate exclusion sensitivity at 3/5/8 bpm.

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
- The historical **respiration-collapse bug** was corrected with the accepted band-edge veto and
  confidence/consistency gates. Ahmed BR remains separately poor (MAE 8.85–11.80 bpm) and must
  retain the exploratory timing/reference limitations from §4.4.
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
  input hashing, and explicit written specifications for the comparator and the analysis. Plan a
  cleaned public release; it is an easy credibility win in review.
- **Publish the comparator and analysis specifications alongside the code** —
  `notes/comparator_prespec.md`, `notes/comparator_prespec_br.md`, `notes/analysis_prespec.md`.
  Describe them as **specifications applied uniformly to every estimator compared**, which is what
  makes the method comparison interpretable. That is a genuine and checkable contribution.

  > **Never describe them as pre-registered, and make no timing claim about them.** M0 was removed
  > on 2026-08-03: nothing was deposited, no DOI exists, and the captures that exist informed the
  > method's design. Claiming or implying otherwise is the same class of error as the withdrawn
  > "MAE 0.16 bpm" (§4.1), and a reviewer who checks will catch it. The honest and still-strong
  > framing is: *one comparator, stated in full, applied identically to every method compared,
  > with coverage reported.*
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
- **[R21]** Kotte, V.V., Ahmed, S., Alouini, M.-S., and Al-Naffouri, T.Y., "Joint Estimation of
  Single Target's High Amplitude Difference Doppler Frequencies in FMCW Radar," *IEEE
  Transactions on Radar Systems*, vol. 2, 2024, DOI: 10.1109/TRS.2024.3352189.

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
2. **Preserve the completed M9 Kotte radar-only and exploratory scoring artifacts.** Together
   with M8 Ahmed they form the paper's published-method comparison. The existing captures can
   support exploratory **BR error/coverage characterization** and **HR coverage/feasibility**, but **not HR tracking** — a constant
   predictor scores 100% on every admissible window (`HANDOFF.md` §2.1). State that limit in the
   paper rather than letting a reviewer find it.
3. **Run the 10-subject study**, including the collision-provoking arm and the recovery arm.
   Ethics approval is in hand (`24IBEC051` + the 2026-08-03 exertion amendment); log the
   reference numbers for the Methods section.
4. **Choose Paper A or Paper B** (§3.4) once the data is in and you can see how the numbers land.
5. **Run the baselines** — the single most common reason a paper like this gets rejected.
6. **Complete every `[CITATION NEEDED]`** in §11.
