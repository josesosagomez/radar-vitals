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

# Thesis Chapter 3 — Contactless Heart-Rate Estimation with a 77 GHz FMCW Radar

> **What this file is.** A single source for writing the thesis chapter: the science, the
> justification for every design choice with a traceable citation, the implementation detail,
> the results (clearly separated into *citable* and *not yet citable*), and the negative
> results. It is a **writing input, not the chapter itself** — prose here is deliberately
> compressed and note-like.
>
> **Honesty contract (CLAUDE.md §4).** Every empirical number below carries a status tag:
> - **[VERIFIED]** — computed by a committed script from data that still exists; regenerable.
> - **[PRELIMINARY]** — real and regenerable, but n=4 subjects, exploratory (used to design the
>   method), or otherwise not defensible as a headline result.
> - **[RETIRED]** — computed on data deleted 2026-07-09; **must never be reported as a result.**
> - **[PENDING]** — not yet measured. A placeholder, not a promise.
>
> M8 Ahmed results synchronised with the repository: **2026-08-08**, branch
> `vital_signs_ahmed_v11`; authoritative report `reports/m8_ahmed_correction_final_report.md`.
> Sources: `notes/approach.md`, `notes/comparator_prespec.md`, `notes/protocol.md`,
> `notes/note_stage1b_lag_statistic.md`, `HISTORY.md`, and the code itself.
>
> **Corrections applied 2026-07-24** after a cross-model review of `plans/implementation_plan.md`
> (see `HISTORY.md`, same date). Four claims in this file were wrong and are fixed: the test count
> ("797 passing" → 796 passed + 1 xfailed), the respiration-collapse count ("4-for-4" → 3-for-3,
> since only three captures have a reference), the Bland–Altman treatment (pooled-independent is
> invalid for a repeated-measures design — §7.5), and the Kotte et al. characterisation (§14 — the
> paper uses 20 RX, not a 4-RX SIMO setup). Recording duration also rose 5 → 10 min (§3.3).
>
> **This is a living document.** The project is ongoing; sections will be added, replaced and
> re-tagged as work lands. Two rules keep it trustworthy as it grows:
> 1. **Promote by re-measuring, never by editing the tag.** A `[PENDING]` becomes `[VERIFIED]`
>    only when a committed script produced the number from data that still exists. A
>    `[PRELIMINARY]` becomes `[VERIFIED]` only when the sample supports it — more subjects, or
>    held-out data the method's design never saw.
> 2. **Never silently delete a result.** Superseded numbers move to `[RETIRED]` with the reason,
>    per CLAUDE.md §4. §11 (negative results) only grows.
>
> A **complete inventory of the work done to date** — code, tooling, tests, data assets, review
> rounds — is in **§17**, kept separate from the chapter argument so it can be updated
> mechanically.

---

## 0. Chapter status board — what can and cannot be written today

| Chapter section | Can be written now? | Blocker |
|---|---|---|
| Motivation, background, theory | **Yes** | — |
| Literature review | **Yes** | — |
| Method / algorithm specification | **Yes** | — |
| System implementation | **Yes** | — |
| Evaluation methodology (comparator) | **Yes** | — |
| Failure-mode analysis | **Yes** — this is a genuine strength | — |
| Ahmed HA real-data comparison | **Yes, exploratory** | Complete two-lock × seven-arm M8 evaluation; approximate timing prevents final agreement claims |
| Headline agreement results | **No** | 10-subject study not started; n=4 subjects exist |
| Bland–Altman (subject-clustered), per-subject breakdown | **No** | Same; and the repeated-measures model is not implemented yet (§7.5) |
| Published-method comparison | **Partial** | Ahmed HA and Kotte joint-Doppler are complete; TI on-chip comparison remains |
| Conclusions | **Partially** | Method conclusions yes; performance conclusions no |

**The single largest gap is data.** The algorithmic and methodological work is mature; the
empirical evidence base is one subject across four sessions. Everything in §11 is
writing-ready; §10 is not.

---

## 1. Research question and contribution claims

**Research question.** Can heart rate be estimated in real time from a 76–81 GHz FMCW radar,
for a subject seated 0.8–1.4 m from the sensor with the radar facing the chest, accurately
enough to track a clinical pulse-oximeter reference?

**Claimed contributions**, ordered by how well the evidence currently supports them:

1. **A real-data evaluation of two published, simulation-only methods** — Ahmed's harmonic
   accumulation [R1] and Kotte's joint high-amplitude-difference Doppler [R21] — scored against a
   clinical reference on identical windows under one common comparator, with coverage reported.
   Ahmed accumulates harmonic evidence to suppress respiratory interference, whereas Kotte
   jointly estimates two high-amplitude-difference Dopplers to separate or mask dominant lines;
   these are distinct mechanisms. **This is the headline contribution.** [PARTIAL — M8 AHMED AND M9 KOTTE COMPLETE; multi-subject validation remains]
2. **An explicit comparator specification for radar-vs-oximeter agreement**
   (`notes/comparator_prespec.md`), stated in full and applied identically to every estimator
   compared. We show that the *same* radar output scored against the *same* reference yields
   **MAE 0.16 bpm or 2.72 bpm — a 17× difference — purely from the choice of comparator**
   [PRELIMINARY, §10.1]. This is a methodological result about how the field reports agreement.
   **It is a transparency contribution, not a timing one:** the specification is not
   pre-registered and no claim about when it was written may be made (see the banner at the top
   of this file).
3. **A warmup range-bin selection failure mode and its fix.** Energy-based eligibility
   partitioning prevents the warmup from locking onto a low-energy "skirt" bin that produces a
   confident but entirely spurious heart rate [VERIFIED, §9].
4. **A characterisation of the respiratory-harmonic coincidence limit** (4·f_r ≈ HR) as an
   *identifiability* problem rather than a resolution or tuning problem, with a documented
   chain of four failed attempts to engineer around it [VERIFIED as negative results, §12].
5. **An implementation and evaluation of ECA + AHET** [Tang 2025] on hardware and at a window
   length different from the original paper (30 s at 20 Hz vs 20 s at 100 Hz).
6. **Agreement with a clinical reference across subjects** [PENDING — this is the contribution
   the thesis examiner will look for, and it does not exist yet]. **Across subjects, not
   across distances**: the protocol leaves the exact distance free within 0.8–1.4 m, so distance
   is descriptive metadata rather than a designed factor (§10.5).

> **Framing advice.** Do not write the chapter as "we built an accurate HR sensor" — the data
> does not support that yet. Write it as "we built the system, evaluated two published
> simulation-only remedies and tested them on real data, and in doing so found
> that the reported-accuracy literature also has a comparator problem and a bin-selection
> problem." That chapter is defensible today. Upgrade to the former once the 10-subject study
> lands.

---

## 2. Background and theory

### 2.1 Why radar for vital signs

Contact sensors (ECG electrodes, oximeter clips, chest straps) are unsuitable for long-term or
unsupervised monitoring: skin irritation, patient compliance, and — for neonates and burn
patients — direct contraindication. Radar is contactless, works through clothing and bedding,
is insensitive to illumination, and unlike a camera does not capture identifiable imagery.
Doppler radar vital-signs sensing dates to the 1970s; see [R7] for the canonical review of the
field and [R11] for a clinical deployment in a neonatal intensive care unit.

### 2.2 FMCW ranging

An FMCW radar transmits a chirp whose instantaneous frequency ramps linearly by slope *S* over
the sweep. Mixing the received echo with the transmitted chirp produces a beat tone at

$$f_b = \frac{2 S R}{c}$$

for a target at range *R*. A fast-time FFT over the ADC samples of one chirp therefore maps
directly to range. Range resolution is set by the **swept bandwidth actually sampled**:

$$\Delta R = \frac{c}{2 B_{\text{eff}}}$$

For our configuration (§3.2): ADC sampling time = 256 samples ÷ 5209 ksps = 49.1 µs, so
B_eff = 70.006 MHz/µs × 49.1 µs ≈ **3.44 GHz**, giving **ΔR ≈ 4.36 cm/bin** [VERIFIED — matches
`range_resolution_m: 0.0436` in the production config and the value written into every capture's
metadata]. A chest at 0.8–1.4 m therefore falls in **range bins ≈ 18–32**.

### 2.3 Phase as a displacement sensor

The key mechanism. Range resolution (4.36 cm) is four orders of magnitude coarser than cardiac
chest-wall displacement (0.2–0.5 mm), so the *magnitude* of the range profile carries no
cardiac information. The *phase* of the complex range bin does:

$$\phi(t) = \frac{4\pi\, x(t)}{\lambda} + \phi_0$$

At 77 GHz, λ ≈ 3.9 mm, so **1 mm of chest displacement produces ≈ 3.2 rad of phase change** —
easily measurable. This is why the entire pipeline is phase-based, and why range-bin selection
matters so much: the phase of the *wrong* bin is a physically meaningless quantity that
nevertheless produces a confident-looking spectrum (§9).

Two classical results underpin this and should be cited:
- **Range correlation** [R5]: in a coherent radar the phase noise of the local oscillator
  correlates between the transmitted and received paths, and largely cancels for short ranges —
  which is what makes µm-scale displacement sensing possible at all with practical oscillators.
- **Arctangent demodulation with DC-offset compensation** [R6]: extracting φ(t) = arctan(Q/I)
  linearises the displacement-to-phase relation and avoids the null-point problem that afflicts
  single-channel amplitude demodulation. Our implementation uses `np.angle` on the complex range
  bin, which is the arctangent demodulation of [R6]; DC offset is handled by static-clutter
  removal (slow-time mean subtraction per bin) rather than by explicit offset estimation.

### 2.4 The chest-wall signal model, and why heart rate is hard

The chest displacement is a sum of two quasi-periodic components plus their harmonics:

$$d(t) = \sum_{k=1}^{K_r} \alpha_k \sin(2\pi k f_r t + \theta_k) + \sum_{l=1}^{K_h} \beta_l \sin(2\pi l f_h t + \psi_l)$$

with respiration f_r ≈ 0.1–0.5 Hz at **1–12 mm** amplitude and heartbeat f_h ≈ 0.8–2.0 Hz at
**0.2–0.5 mm**. Respiration is therefore **20–60× larger**, and because the phase response is
non-linear in displacement, its *harmonics* extend well into the cardiac band.

Worked example at f_r = 15 bpm (0.25 Hz): harmonics land at 30, 45, **60**, **75** bpm — the 4th
and 5th sit squarely inside the 48–120 bpm cardiac band. Intermodulation products
(f_h ± k·f_r) fill it further. When two components fall within one FFT bin they merge, and the
stronger — respiration — wins any argmax-based estimator.

**This single fact drives the entire method.** Everything in §6 exists to solve it.

---

## 3. System and experimental setup

### 3.1 Hardware

| Item | Detail |
|---|---|
| Radar | Texas Instruments **IWR1642BOOST**, 76–81 GHz FMCW, 4 RX / 2 TX (TX0 only enabled) [R9] |
| Raw capture | **DCA1000EVM** LVDS capture card, 2-lane, streaming raw ADC over Ethernet [R10] |
| Reference | **Masimo MightySat** fingertip pulse oximeter, SET® signal-extraction technology, exported at 1 Hz [R12] |
| Geometry | Radar at chest height, horizontal, facing the chest; subject seated 0.8–1.4 m away |

Only **one TX and the phase of a single range bin** are used. The 4 RX channels are captured but
the current DSP does not beamform — a deliberate simplification, and an acknowledged limitation
(§13) since multi-bin/MIMO combination is one of the two known escape routes from the
coincidence limit (§12.1).

### 3.2 Chirp configuration [VERIFIED — `scripts/live_demo_config.yaml`]

| Parameter | Value |
|---|---|
| Start frequency | 77.0 GHz |
| Frequency slope | 70.006 MHz/µs |
| Idle / ADC start / ramp end | 7.0 / 5.0 / 57.0 µs |
| ADC samples per chirp | 256 |
| Sampling rate | 5209 ksps |
| RX gain | 30 dB |
| Chirps (loops) per frame | 32 |
| Frame period | **50 ms → 20 Hz slow-time sampling** |
| Effective sampled bandwidth | ≈ 3.44 GHz |
| Range resolution | **0.0436 m** |

**Why 20 Hz and not faster.** The slow-time sample rate need only satisfy Nyquist for the
cardiac band plus its verified 2nd harmonic (2 × 2.0 Hz = 4 Hz), so 20 Hz gives 5× margin. Our
primary reference [R1] uses 100 Hz; we deliberately run 5× slower to keep the raw data rate
tractable for long captures over the DCA1000 link. **This is a difference from the reference
implementation and must be declared** — it is one reason our numbers cannot be compared to
theirs directly.

### 3.3 Capture protocol [`notes/protocol.md`]

Seated, back straight, both hands resting on the legs, facing the radar; chest 0.8–1.4 m from
the radar face; **10-minute recordings** (~30 s warmup + ~9.5 min usable, ~19 independent
non-overlapping 30 s windows — raised from 5 min on 2026-07-24 because 5 min yielded too few
evaluable windows at 10–46% coverage; the ethics approval permits up to 10 min and that ceiling
is hard); **10 subjects × 3 sessions** (natural, paced, and a post-exertion recovery arm added
2026-08-03 under an approved ethics amendment) on different days, each session re-set up (so
setup variability is inside, not outside, the reported variance).

**Settle criterion (added 2026-07-14 after observing settling transients corrupt early
windows).** Before the radar starts: on the live Masimo, PR spread ≤ 5 bpm over a continuous
60 s **and** no monotonic drift (last 20 s within 3 bpm of first 20 s). Not met within 5 minutes
→ abort and re-seat. **Known incomplete:** even with this in place, the sweep capture showed
elevated PR variability for its first ~2.5 minutes — the criterion may need to hold *after*
warmup, not only before recording starts. Report this honestly as a protocol limitation.

**Paced-breathing arm: 12 / 15 / 18 bpm, plus a stepped 12→15→18→21 bpm sweep at 120 s dwell.**
The 18 bpm arm is *deliberately inside the known failure zone* (4 × 18 = 72 bpm, inside the
resting-HR band). This is a designed probe of the §12.1 limit, not an oversight, and its
windows **must be reported separately and never pooled into headline metrics** — pooling would
import a known failure mode into the top-line MAE.

---

## 4. Literature review

### 4.1 Foundational work (cite for background, §2)

- **[R5] Droitcour et al. (2004)** — range correlation and I/Q performance in single-chip
  Doppler radar. The enabling result for µm-scale displacement sensing.
- **[R6] Park, Boric-Lubecke & Lubecke (2007)** — arctangent demodulation with DC-offset
  compensation; reports HR standard deviation < 1 bpm and establishes phase demodulation as
  superior to channel selection.
- **[R7] Li, Lubecke, Boric-Lubecke & Lin (2013)** — the standard review of Doppler radar for
  noncontact healthcare monitoring.
- **[R8] Alizadeh et al. (2019)** — mm-wave FMCW vital signs at 77 GHz; a direct methodological
  ancestor of our setup.
- **[R11] Beltrão et al. (2022)** — radar breathing monitoring of premature infants in a real
  NICU; the strongest clinical-deployment citation available and a good motivation anchor.

### 4.2 The modern harmonic-interference literature (the design space)

All six were surveyed in `notes/approach.md` §5 and each is included in the reference list with
its arXiv ID. Summarised by *strategy*:

| Ref | Strategy | Reported result | Why not chosen |
|---|---|---|---|
| **[R1] Tang et al. 2025** — ECA + AHET | Cancel respiration harmonics in time domain, then verify the cardiac peak via its 2nd harmonic | RMSE 14.41 → 6.37 (ECA) → **1.20 bpm** (AHET) | **CHOSEN** — see §5 |
| **[R2] Hsieh et al. 2024** — Harmonic MUSIC | Super-resolution subspace method; jointly model respiration and cardiac harmonic sources | 88th pct error < 5 bpm, 4 subjects | Assumes a multi-antenna correlation matrix; single-bin phase has lower rank than assumed |
| **[R3] Iwata et al. 2024** — \|d²s/dt²\| + VME | Move up in frequency: 2nd derivative acts as a high-pass, extract the **2nd cardiac harmonic** via variational mode extraction | IBI RMSE −23%, CC +0.20 | Needs 60 s windows; targets IBI/HRV rather than per-window rate |
| **[R4] Shimomura et al. 2025** — NLHS | Sum localised spectral autocorrelations across harmonic orders; cardiac harmonics align, respiratory ones don't | RMSE −20%, CC +0.20 | 60 s windows |
| **[R13] Gu et al. 2025** — NRBO-VMD | Auto-tune VMD parameters by minimising sample entropy | RMSE 5.21 bpm, 18 subjects | Measured from the **back at 20 cm** — different geometry |
| **[R14] Zhang et al. 2023** — Pi-ViMo | Time-domain template matching with physiological models (RC respiration, Van der Pol heart) | 11.9% error stationary | ~4.3 s compute per 15 s window — not real time |

**Gap this work addresses.** Two published simulation methods — Ahmed's harmonic accumulation
[R1] and Kotte's joint high-amplitude-difference Doppler [R21] — are evaluated on these real
radar captures. They address interference differently: Ahmed accumulates harmonic evidence,
whereas Kotte jointly estimates two dominant Doppler lines. Neither paper's simulation result
should be read as human-data validation.
Separately, every entry reports accuracy without stating how the radar window is compared to the
reference, and — with the exception of [R2] — without reporting *coverage* (what fraction of
windows produced an estimate at all). §7 shows both choices can move a headline number by more
than the differences between the methods in this table.

---

## 5. Method decision and its justification

**Chosen: ECA + AHET [R1].** The rationale, each point traceable:

1. **It targets our documented failure mode directly.** [R1] explicitly identifies the 4th
   respiratory harmonic and the f_h − f_r intermodulation term merging within the frequency
   resolution — precisely the mechanism in §2.4 and §12.1.
2. **It works at our window length and frame rate.** [R3] and [R4] require 60 s windows; we need
   a live readout, and a 60 s window makes the warmup unusable.
3. **ECA is a linear projection** — invertible, information-preserving, and diagnosable. We can
   log exactly how much power was removed at each harmonic and show it in the chapter.
4. **Implementable in NumPy/SciPy** with no new dependencies, unlike the MUSIC and VMD families.
5. **AHET is the principled form of an idea we had already tried and failed with.** We first
   implemented a fixed 0.08 Hz "exclude peaks near respiratory harmonics" proximity rule; it
   over-triggered whenever the true cardiac peak sat near a harmonic [RETIRED — recorded in
   `notes/approach.md` §4.1]. A fixed frequency tolerance is the wrong abstraction; a
   *structural* test (does this peak have the 2nd-harmonic structure a real heartbeat has?) is
   the right one. **This is a good chapter narrative beat** — it motivates AHET from our own
   failure rather than by appeal to authority.

ECA's underlying estimator lineage is the **ANLS framework of Beltrão et al.** [R15], cited by
[R1]; our implementation substitutes a simpler FFT-argmax-plus-parabolic-interpolation estimator
for f_r (§6.1) — **a declared deviation from [R1]**.

---

## 6. Algorithm specification

Pipeline, each stage independently verified (`tests/`, 796 passed + 1 xfailed [VERIFIED 2026-07-24]):

1. Parse raw ADC (2-lane LVDS, Complex1x, 4-word packets). **I/Q ordering depends on capture
   source**: SDK/Python captures need `iq_swap=True`, mmWave Studio captures `iq_swap=False`.
2. Range FFT (fast time) → complex range profile per frame.
3. Static clutter removal — subtract the slow-time mean per bin (also serves as the DC-offset
   compensation of [R6]).
4. **Warmup range-bin selection and lock** (§9).
5. Phase extraction (`arctan` on I/Q) and unwrapping along slow time.
6. Phase differencing + impulse-noise clipping (`impulse_clip_rad: 1.5`).
7. **ECA** → cardiac band → **AHET** (§6.1–6.2).
8. Windowing: **30 s window**, 3 s hop for the live display.

### 6.1 Phase 1 — ECA (respiration subspace cancellation)

Input: unwrapped phase θ[n], N = 600 (30 s × 20 Hz).

1. **Estimate f_r**: FFT of θ[n], argmax in [0.10, 0.50] Hz, refined by parabolic interpolation.
2. **Build the harmonic Vandermonde subspace** X (N × 2K_b), columns
   sin(2π(k+1)f_r t_n) and cos(2π(k+1)f_r t_n) for k = 0…K_b−1. Both sine and cosine so each
   harmonic's phase offset is a free parameter.
3. **Project onto the orthogonal complement**:
   $$\theta_{\text{ECA}} = \theta - X(X^\top X)^{-1}X^\top\theta$$
   computed by **modified Gram–Schmidt**, not an explicit inverse (numerical stability).
4. **Log the cancelled power at each harmonic** as a mandatory diagnostic.

**K_b selection.** Production mode is `skip_forbidden_harmonics_v1`: include harmonic k unless
k·f_r falls inside the cardiac band. The reason is §12.2 — the previous mode unconditionally
projected out k = 1…4, which *erased the cardiac signal itself* whenever 4·f_r landed on the
heart rate.

### 6.2 Phase 2 — cardiac peak search and AHET verification

5. Hann-windowed rFFT of θ_ECA.
6. Candidate peak: argmax in [0.8, 2.0] Hz → f_h1.
7. **AHET second-harmonic consistency check**: search locally in [2·f_h1 ± V_e], V_e = 0.1 Hz.
   If |2·f_h1 − f_h*| ≤ V_e → accept f_h1. Else try the 2nd-largest candidate and re-check.
   **If both fail, return NaN.** Never substitute a default — that was the original failure mode.
8. Output HR = 60·f_h bpm plus a per-window confidence flag.

Additional production gates [VERIFIED — `live_demo_config.yaml`]: `candidate_min_prominence: 3.0`,
`candidate_min_peak_to_floor_db: 2.0` (4.0 for low candidates below 1.20 Hz),
`candidate_min_second_harmonic_ratio_db: 1.0`, `k_max: 6` with an adaptive per-window
`k_max_eff = min(10, ⌊band_hi / f_r⌋)`.

### 6.3 Mandatory diagnostics (a methodological commitment, worth a paragraph)

Per window the pipeline dumps: estimated f_r, power cancelled at each harmonic, the full θ_ECA
spectrum, f_h1, f_h*, the credibility flag, and the final HR. **This is what makes a wrong
reading diagnosable rather than merely observable** — the reason the failure analyses in §12
were possible at all. Implemented as `live_intermediates.npz` + `live_estimates.csv`; inspected
by `scripts/diagnose_live_run.py`.

### 6.4 Known limitations of the AHET criterion [VERIFIED as properties of the criterion]

Four caveats that **must appear in the chapter** — they are not dataset artefacts:

1. **The pass threshold is soft.** The rule is `second_peak_magnitude / comparison_floor > 1.0`
   against the median cardiac-band magnitude; observed ratios cluster just above 1.0, so the
   criterion is weakly discriminative.
2. **Passing does not guarantee accuracy.** It confirms 2nd-harmonic *structure* exists, not
   that the fundamental is cardiac — **a respiratory harmonic also has genuine 2nd-harmonic
   structure**. This is the root cause behind §12.3.
3. **Longer windows mechanically inflate pass rates.** The ±0.1 Hz search region spans ~5/5/7
   FFT bins at 20/25/30 s. Under an idealised null the chance the max of *m* bins exceeds the
   band median is 1 − 0.5^m ≈ 0.97–0.99. **AHET pass rates must never be compared across window
   lengths** without this caveat.
4. **No validated rejection population yet** [PENDING].

---

## 7. Evaluation methodology — the comparator problem

**This section is a contribution, not boilerplate. Give it real space in the chapter.**

### 7.1 The problem

The radar produces one estimate per **30 s window** — an FFT peak, i.e. a *dominant frequency*.
The Masimo produces an **instantaneous PR once per second**. These are not the same kind of
quantity, and the bridge between them is a free parameter that the literature leaves unstated.
On the same five radar hops, same reference [PRELIMINARY]:

| Comparator | MAE | Severe errors (>5 bpm) |
|---|---|---|
| Instantaneous PR at the window's end epoch | **0.16 bpm** | 0 |
| PI-gated **mean** over the same 30 s window | **2.72 bpm** | 1 |

**A 17× difference in the headline number, from the comparator alone.** The first version was
reported internally as a validated result. It was not one, and it is **formally withdrawn**.

**Root cause.** The subject's PR was not stationary early in that run — 75 → 94 → 65 bpm over
~25 s, with Perfusion Index 7–10 throughout, so the excursion was *physiologically real*, not a
sensor artefact. The radar's first accepted window straddled it.

**The deeper point, and the chapter's argument:** when the reference moves inside the window,
**there is no single true HR for that window**, and no comparator is defensible. The fix is not
a cleverer average — it is to *detect that case and exclude it*.

### 7.2 The comparator specification [`notes/comparator_prespec.md`]

For a radar window ending at *t*, spanning [t − 30 s, t]:

**Reference value:** `PR_ref` = **median** of PI-gated Masimo PR samples inside the window.
Median for robustness to single-sample glitches; under the stationarity gate the median and mean
coincide anyway — *which is exactly the point*: once the window is stationary, the comparator
choice stops mattering. Alignment uses the integer `Timestamp` (Unix epoch, UTC) column only,
never the `Date`/`Time` strings.

**Window admissibility — all three gates, or the window is excluded:**

| Gate | Rule | Rationale |
|---|---|---|
| **Perfusion Index** | drop samples with PI < 0.5 | low PI ⇒ the reference itself is untrustworthy; never tune the radar to chase it |
| **Coverage** | require ≥ 80% of the 30 expected samples to survive the PI gate | a window scored on a handful of samples is not scored |
| **Stationarity** | exclude if (p90 − p10) of in-window PI-gated PR > **5.0 bpm** | §7.3 |

### 7.3 Why 5 bpm — derived, not tuned

**Chosen from frequency resolution, not from radar error.** The FFT bin at a 30 s window is
2.0 bpm. If the reference moves more than about one bin inside the window, the target is not a
single number. 5.0 bpm = **2.5 bins**. Measured within-window PR spread on existing captures is
**median 2.8–3.0 bpm** — that is ordinary HRV/RSA, it is real, and it does *not* make the
dominant frequency ill-defined; a 1-bin (2 bpm) gate would reject 60–70% of windows for normal
physiology. At 5 bpm, 12% of windows are excluded (natural) and 20% (paced-16), and in the
natural run the excluded windows are *exactly* the settling transient.

**Sensitivity must be reported** (exclusion fraction at 3 / 5 / 8 bpm) so the choice is auditable
rather than asserted.

### 7.4 Statistical independence — non-overlapping windows only

The live display hops every 3 s; adjacent estimates therefore share **27 of 30 s** of data and
are **not independent**. All agreement statistics (MAE, RMSE, Bland–Altman) are computed on
**non-overlapping 30 s windows**. Treating hopped estimates as independent would fake tight
Bland–Altman limits of agreement — a specific, checkable error to warn about in the chapter.

### 7.5 Metrics

MAE (bpm), RMSE (bpm), and **Bland–Altman** bias with 95% limits of agreement [R16] — the
standard for method-comparison studies, and explicitly *not* a correlation coefficient, which
[R16] shows is misleading for agreement. **Coverage (% of windows producing an estimate) is
reported alongside accuracy, always** — accuracy computed only on surviving windows is selection
bias, and given our coverage (§10.2) this is not a hypothetical concern.

**Bland–Altman must account for repeated measurements** [CRITICAL — the current implementation
does not]. The study design is 10 subjects × 2 sessions × ~19 windows: those window-level
differences are **nested within subject** and are not independent pairs. The only implementation in
the repo, `scripts/plot_bland_altman.py`, pools every window as if independent
(`bias ± 1.96·SD`, `se_loa = sqrt(3·SD²/n)` with `n` = pooled window count, no subject or session
term), which will produce **falsely narrow limits of agreement and confidence intervals**. This is
wrong for the study, and it was never right for the n=1 pilot either — windows from one subject do
not become independent by being non-overlapping, and a single subject carries no between-subject
variance, so population limits of agreement are unidentifiable from it. **Treat every number that
script has produced as descriptive only.** The chapter must use a subject-clustered
repeated-measures model specified in `notes/analysis_prespec.md` §1
[PENDING implementation — see `plans/implementation_plan.md` M4].

---

## 8. Reproducibility apparatus

Worth a short chapter section; it is unusual to have this and examiners reward it.

- Raw inputs are **read-only**; all processing writes to `data/processed/` via tracked scripts.
- Every result traces to **script + config + seed (42) + the input file's SHA-256**. Capture
  metadata records the hash, chirp configuration, frame count, and byte counts at capture time.
- No magic numbers in code — chirp parameters, distance gates, frame rate and filter bands live
  in `config.yaml` files.
- Figures are generated by scripts, never hand-edited.
- **Negative results are recorded, not deleted** (`HISTORY.md`, ~5,800 lines, append-only).
- **Cross-model review**: any diff touching range-FFT, phase extraction, filtering, peak-picking
  or the reference parser gets an independent correctness pass from a different model family.
  The Stage 1B design went through **six rounds**, each catching a real defect (§12.5).

**Live vs offline separation.** The live demo's readouts use an online median smoother over
recent AHET-verified estimates and are **a sanity check, not a result**. All paper-grade metrics
come from re-processing the run's saved raw `adc_stream.bin` offline.

---

## 9. Contribution — warmup range-bin selection and the mislock failure

### 9.1 The problem

The chest occupies one of ~14 candidate range bins in the 0.8–1.4 m gate. The DSP locks one bin
at warmup and holds it for the session. **A wrong lock is catastrophic but invisible**: the phase
of a wrong bin still produces a spectrum, still has peaks, and can still pass AHET.

### 9.2 The observed failure [VERIFIED]

In the stepped-rate sweep session, a strong non-harmonic peak at ~63–67 bpm appeared from
t = 70–169 s, initially attributed to a DSP/ECA/AHET fault ("Pattern A") and investigated as
such for some time. Root cause: **the warmup had locked a skirt bin 28.1 dB below the actual
chest bin.** Not an algorithm bug at all — a selection bug upstream of it.

Quantified on the same window (t = 147 s, `figures/fig_range_bin_mislock.py`): the mislocked
bin 21 reported **64.4 bpm** and *passed* AHET second-harmonic verification, while the correct
bin 26 reported **81.8 bpm** against a comparator reference of **81.0 bpm**. A 16.6 bpm error,
indistinguishable at the output from a correct reading.

### 9.3 The fix — energy-eligibility partitioning [VERIFIED, committed `863600e` + `dfe7fb5`]

**The underlying defect was in the scoring, not the DSP.** Bin 21 was the only candidate whose
heart-rate DSP produced a valid-looking result, which earned it a large "HR-valid" score bonus:
**1265 points versus 295 for the true chest bin.** The selector was rewarding *"this bin
produced an answer"* without asking whether the bin carried enough signal for that answer to
mean anything.

The fix has two parts, and both are load-bearing:

1. **Energy eligibility gate.** A candidate's HR-valid bonus only counts if its settled energy
   is within **−12 dB** of the strongest candidate. Ineligible bins can still win, but only if
   no eligible candidate's DSP succeeds (`fallback_used`).
2. **Measure that energy after a 5 s settle skip** (100 frames at 20 Hz). This is *not* a delay
   in the output — warmup length is unchanged; it selects *which frames* the energy measurement
   uses.

**The skip is what makes the gate work.** Measured over the whole warmup, bin 21 reads
**−11.0 dB** — inside the −12 dB gate, so the gate alone would have passed it. Measured after
the first 5 s it reads **−28.1 dB** and is correctly ruled ineligible. Verified end-to-end on
the sweep session: with the fix, bin 21's bonus is vetoed (`hr_bonus_vetoed: true`, score
1265 → 265) and bin 26 wins on an unchanged score of 295.

**Mechanism caveat — flag this as unverified.** *That* the first 5 s inflate weak bins, and that
skipping them corrects the selection, are both measured. *Why* is not. The plausible cause is the
static-clutter removal: it subtracts a per-bin slow-time mean, which is poorly estimated from few
frames, so residual static energy leaks in and disproportionately inflates bins with little real
signal. **That is reasoning, not a measurement** — do not state it as a finding.

**Honest scoping — say this explicitly in the chapter.** The −12 dB / 5 s thresholds are an
**empirical, scene-scoped prior** validated on 4 sessions from 1 subject, under a scene where a
single seated subject is the dominant in-gate reflector. They are **not** a general physical law
and are **untested with competing reflectors** (a chair, a second person, a moving object) inside
the gate. Re-validate against the 10-subject study.

Validation: `scripts/validate_warmup_selection.py` reruns the real selection logic against all
four sessions' raw streams and confirms the picks [VERIFIED, committed and regenerable].

### 9.4 Consequence — an honest trade

At the *correct* bin, only **19.9%** of windows produce an AHET-verified HR, versus 36.4% before
the fix — but most of those pre-fix estimates were *wrong* [PRELIMINARY]. The fix trades
apparent coverage for correctness. **This is the honest way to present it**, and it sets up the
coverage discussion in §10.2. One session (`massimo1`) still locks a mediocre bin because no
`hr_valid` pass exists anywhere in its warmup — not fixable by this design.

---

## 10. Results

> **Read the status tags.** Nothing in §10 is a defensible headline result yet.

### 10.1 Agreement, at the corrected bin, under the stated comparator [PRELIMINARY]

All three Masimo-referenced sessions re-processed end-to-end at current HEAD and scored strictly
under `notes/comparator_prespec.md`:

| Session | AHET-accepted | Excluded (reference non-stationary) | Scorable | MAE | Severe (>5 bpm) |
|---|---|---|---|---|---|
| natural | 5 / 50 | 4 | 1 | 0.19 bpm | **0** |
| paced-16 | 23 / 50 | 2 | 21 | 0.50 bpm | **0** |
| sweep | 30 / 150 | 11 | 19 | 0.53 bpm | **0** |

All 17 excluded windows were checked individually: **every one is a real reference instability**
(in-window spread 5.1–26.0 bpm) at a settling transient or a paced-rate transition — legitimate
stationarity-gate exclusions, not radar error being hidden by the gate. *(Say this in the
chapter. The obvious reviewer question about an exclusion gate is "what did it hide?", and we
checked.)*

**Why these are not citable as a result:**
- **n = 4 subjects** across the eight sessions (corrected 2026-08-03; previously recorded as one — see `notes/capture_inventory.md` "Subject map").
- These sessions **informed the design** of the harmonic-veto work, so they are exploratory, not
  held-out validation.
- `natural`'s MAE rests on a **single** scorable window.
- Coverage is 10–20%, so the accuracy figure describes a small, self-selected minority of windows.

**The correct framing for these numbers:** "when the pipeline accepts a window, it is accurate;
it accepts few windows." Both halves must be reported together.

### 10.2 Coverage — the actual bottleneck [PRELIMINARY]

10% (natural), 46% (paced-16), 20% (sweep) of windows yield an AHET-verified estimate. **AHET's
conservatism at the correct bin is now the limiting factor**, not accuracy. This is the honest
headline of the current state of the system and should be stated as such.

### 10.3 Ahmed harmonic accumulation on real FMCW data [VERIFIED IMPLEMENTATION; EXPLORATORY AGREEMENT]

Ahmed et al.'s Section III-C harmonic-accumulation method was corrected and evaluated as a
paired comparison on the same decoded cubes, windows, locks, and run identity as the production
estimator. The canonical design contains the production arm and six Ahmed profiles: H=3/H=5
crossed with figure-visible unsuppressed, Eq. 26 multiples-suppressed, and prose
low-or-equal-suppressed interpretations. The original pulse-radar identity is `q=2f` and
physiological rate `30q bpm`; the project's declared FMCW unwrapped-phase adaptation is `q=f`
and `60q bpm`. Each score is `sum_h |S(hq)| / eta` with strict `Hq < f_Nyquist` support.

The canonical run covered eight captures, two lock estimands, and seven paired arms, producing
128 source spans, 256 shared cells, 1,792 estimator rows, and 3,584 unique scored HR/BR rows.
Comparative metrics exclude k=0 and keep natural, paced, and unknown protocol strata separate.
The following ranges span all six Ahmed profiles; they are **not** a post-hoc best-arm result.

**Heart-rate agreement against Masimo `Beats / min`:**

| Protocol stratum | Lock | Joint coverage | MAE range (bpm) | RMSE range (bpm) | Bias range (bpm) |
|---|---|---:|---:|---:|---:|
| Natural | recorded as captured | 1.000 | 4.55–7.35 | 6.51–9.03 | −7.35 to −4.15 |
| Natural | current-production rerun | 1.000 | 4.25–9.05 | 7.39–10.93 | −7.35 to −2.15 |
| Paced | recorded as captured | 0.650 | 23.46–25.46 | 24.52–26.34 | −25.46 to −23.46 |
| Paced | current-production rerun | 0.650 | 17.77–18.85 | 22.66–23.74 | −17.92 to −17.00 |
| Unknown | either lock | 0.505 | 31.65–33.57 | 32.77–34.42 | −33.57 to −31.65 |

**Breathing-rate agreement against Masimo `Breaths / min`:**

| Protocol stratum | Lock | Joint coverage | MAE range (bpm) | RMSE range (bpm) | Bias range (bpm) |
|---|---|---:|---:|---:|---:|
| Natural | recorded as captured | 1.000 | 9.00–11.40 | 10.23–11.55 | −11.40 to −9.00 |
| Natural | current-production rerun | 1.000 | 11.40–11.80 | 11.45–11.86 | −11.80 to −11.40 |
| Paced | recorded as captured | 1.000 | 8.85–9.35 | 9.52–9.86 | −9.35 to −8.85 |
| Paced | current-production rerun | 1.000 | 8.95–9.35 | 9.65–9.85 | −9.35 to −8.95 |
| Unknown | either lock | 0.842 | 10.30–11.16 | 10.69–11.34 | −11.16 to −10.16 |

Ahmed produced a radar-valid estimate in every comparative cell, but measured joint coverage is
lower where the reference stationarity/availability rules exclude windows. The predominantly
negative bias shows that the FMCW adaptation generally underestimated both rates. It therefore
ran successfully as an algorithm but **did not transfer as an accurate estimator on these
recordings**.

The Figure 8 successor independently failed its declared 80 bpm heart target: the
visible-unsuppressed H=3 curve selected 40.0144 bpm and H=5 selected 20.0072 bpm, although both
recovered the 20 bpm breath target as 20.0072 bpm. This outcome was retained honestly; no
assumption was changed to force reproduction.

**Claim boundary:** timestamps use an approximate capture origin with 5–15 s uncertainty; the
scored bundle is single-development-dataset, exploratory, and explicitly ineligible for final
agreement or population claims. These findings evaluate the **FMCW phase adaptation**, not
Ahmed's original pulse-radar hardware/mapping. Exact profile-by-profile values, manifests, and
evidence identities are in `reports/m8_ahmed_correction_final_report.md` and
`results/m8_ahmed_transfer/scored/20260808T191921.669826Z_bc3ccf4635c5/`.

### 10.4 Kotte joint Doppler on real FMCW data [VERIFIED IMPLEMENTATION; EXPLORATORY AGREEMENT]

Kotte et al. propose a three-stage pipeline: (i) select a target range bin and estimate a
Capon direction of arrival (DOA), (ii) form a two-frequency joint Doppler weight, and (iii)
choose the frequency pair by the joint objective. Their paper is a 20-RX, 24-GHz simulation;
it does not validate human vital signs. The implementation here is therefore a controlled
adaptation, not a reproduction of the complete paper pipeline.

The direct mathematical control follows the paper's orientation. At the selected range bin,
`Y(kappa)` has RX rows and slow-time columns and is transposed to
`Y_t = Y(kappa)^T` (slow time × RX). For each trial pair,

```text
A = [a(f1) a(f2)]                         (N_c × 2)
R_t = Y_t Y_t^H / n_R                    (N_c × N_c)
H = A^H R_t^{-1} A                       (2 × 2)
w = R_t^{-1} A H^{-1} [1 1]^T            (Eq. 25)
beta_hat = w^H Y_t a(theta_hat)* /
           (a(theta_hat)^H a(theta_hat)) (Eq. 26; direct-synthetic diagnostic only)
```

The primary pair-selection surface is Algorithm 1's
`J(f1,f2) = w^H R_t w = 1^H H^{-1}1`; the Eq. (26) coefficient surface is retained as a
separate diagnostic for direct synthetic audits only. The real-data path omits DOA and Eq. (26)
and selects pairs only with the regularized Algorithm 1 surface. The two columns of `A` are
interchangeable, so signed pair aliases are collapsed deterministically before reporting the
lower and higher frequencies. Equality and ill-conditioned pairs are masked. Frequency is
physical Hz, using
`a(f)[i] = exp(j 2 pi f i T_PRI)` with `T_PRI = 0.05 s`; the reported physiological candidates
are `60 |f|` bpm. The signed search domains are `±0.10–0.50 Hz` for breathing and
`±0.80–2.00 Hz` for heart, sampled at `1/120 Hz` (0.5 bpm). The printed paper steering vector
omits `T_PRI`; including it is the declared physical-Hz interpretation used here. The
operational labels are lower frequency = breathing candidate and higher frequency = heart
candidate. This is a prior within disjoint bands, not a Masimo-selected label;
harmonics, sidebands, signs, and motion can invalidate it.

For the literal surface, the selected pair is the deterministic lexicographic tie-broken argmax
over admissible grid cells. The real-data path instead maximizes the loaded
`regularized_kotte_power` surface over the same admissibility rules.

The real-data adapter deliberately reuses the established warmup range bin and omits the DOA
stage; no DOA claim is made. Each decoded window is the exact complex64 FMCW cube
`(600, 32, 4, 256)` (frames, chirp loops, RX, fast-time samples). The adapter performs the
unchanged Hann range FFT, selects chirp-loop 0 across frames, and emits selected-bin
`Z (600, 4)` complex128. It retains the first 592 frames after support validation, removes the
per-RX retained-support mean, and splits the result into 37 non-overlapping `(16, 4)` CPIs.
The 4-RX covariance has rank at most four, so the project adaptation uses the declared
trace-relative loading, `R_delta = R_t + delta Re(trace(R_t))/N_c I`, with two separate arms:
`delta=1e-2` is the primary project arm and `delta=1e-4` is the loading sensitivity. The
project surface is explicitly the loaded objective
`1^H H_delta^{-1}1`, named `regularized_kotte_power`; it is not silently reported as the
unloaded paper power.

Synthetic controls established fixed-loop extraction and physical-Hz phase-ramp recovery,
passed the direct 4-RX two-cisoid control within the grid, and reproduced the expected
high-amplitude-difference/cancellation behavior. The chest-displacement/Bessel transfer study
treats its failure (0/24 chest cases and 0/10 robustness cases) as a nondeployable diagnostic
consistent with two-line model limitations. It is not a reason to add a four-line estimator,
tune the grid, or consult Masimo. Literal 0-dB paper controls and the separate 21.0721-dB
FFT-gain sensitivity remain separately labelled; neither sets a real-data threshold. In M9.1,
the literal post-range/`Y_t` 0-dB R1/R2/R3 controls were `not_reproduced_under_declared_assumptions`.
The 21.0721-dB FFT-gain interpretation reproduced selected behavior but remains unsettled and
nonprimary. Both are implementation-only (`thesis_evidence_eligible=false`, outcome-decision-
ineligible) and are not thesis outcome evidence or a real-data rule.

The canonical radar-only run used eight captures and fixed half-open 30-s windows. It emitted
256 rows (two loading arms × 128 windows), all algorithmically valid, with 100% valid CPI
coverage and complete evidence. The first window in each capture (`k=0`) is labelled
`lock_selection_in_sample` and excluded from comparative metrics; scoring uses `k >= 1` only.
Reference scoring then admitted 66/120 HR cells and 105/120 BR cells after the existing
timestamp, perfusion-index, and stationarity rules. The 30 duplicate-normalized reference
seconds were recorded in the scoring artifact; they were not radar tuning.

**Protocol-stratified exploratory summaries (current-production rerun lock):**

| Vital / protocol | Loading arm | Scored / total | Joint coverage | MAE (bpm) | RMSE (bpm) | Bias (bpm) | Bland–Altman LoA (bpm) |
|---|---:|---:|---:|---:|---:|---:|---:|
| HR / natural | `1e-2` | 53 / 100 | 0.53 | 29.434 | 30.608 | −29.434 | [−46.047, −12.821] |
| HR / natural | `1e-4` | 53 / 100 | 0.53 | 29.368 | 30.911 | −29.368 | [−48.450, −10.286] |
| HR / paced | `1e-2` | 5 / 5 | 1.00 | 19.100 | 20.059 | −19.100 | [−32.526, −5.674] |
| HR / paced | `1e-4` | 5 / 5 | 1.00 | 10.700 | 12.106 | −10.500 | [−23.703, 2.703] |
| HR / stepped | `1e-2` | 8 / 15 | 0.53 | 27.438 | 27.854 | −27.438 | [−37.492, −17.383] |
| HR / stepped | `1e-4` | 8 / 15 | 0.53 | 26.938 | 27.299 | −26.938 | [−36.212, −17.663] |
| BR / natural | `1e-2` | 85 / 100 | 0.85 | 6.406 | 7.547 | +2.688 | [−11.216, 16.593] |
| BR / natural | `1e-4` | 85 / 100 | 0.85 | 5.447 | 6.632 | −3.188 | [−14.655, 8.278] |
| BR / paced | `1e-2` | 5 / 5 | 1.00 | 10.700 | 11.039 | +10.700 | [4.755, 16.645] |
| BR / paced | `1e-4` | 5 / 5 | 1.00 | 8.200 | 9.482 | +8.200 | [−2.231, 18.631] |
| BR / stepped | `1e-2` | 15 / 15 | 1.00 | 11.800 | 12.172 | +11.800 | [5.738, 17.862] |
| BR / stepped | `1e-4` | 15 / 15 | 1.00 | 11.300 | 11.860 | +11.300 | [3.996, 18.604] |

The constant-session-median comparator is reported beside HR because these approximate-origin
captures have low within-session HR dynamic range; it is descriptive, not a radar result.
Minimum pair margins were approximately `1.8e-5 dB`, so the selected pairs are weakly
separated. All outputs are exploratory and ineligible for promotion or a final agreement
claim. The conservative conclusion is that this fixed-range, 4-RX, loaded adaptation of Kotte
underestimated HR and produced substantial BR error on these captures. It does not establish
that the published simulation method fails in its original 20-RX setting.

Canonical artifacts: radar-only
`results/m9_kotte_radar/20260809T001318.369165Z_d01cbc2d8404_radar_only_unscored/` and scoring
`results/m9_kotte_score/20260809T014500.317959Z_adf4434de20b_exploratory_non_frozen/`.

### 10.5 [PENDING] Everything else

- Agreement across 10 subjects; **per-subject** breakdown.
- Bland–Altman bias and 95% limits of agreement on the full study, under a **subject-clustered
  repeated-measures** model (§7.5).
- **Distance as descriptive metadata only.** `notes/protocol.md` deliberately does not pin the
  distance — it is recorded, and the warmup auto-locks anywhere in 0.8–1.4 m — so there are no
  predefined, replicated distance strata and a per-distance *comparison* would be confounded with
  subject. Report error against measured distance descriptively; **do not claim per-distance
  agreement.** (CLAUDE.md §1 asks to *report* per distance, which this satisfies.)
- Baseline comparison vs TI's on-chip vital-signs output and a published phase-based pipeline.
- The 18 bpm paced arm reported separately as a failure-mode characterisation.
- Sensitivity of exclusions to the stationarity gate at 3 / 5 / 8 bpm.

### 10.6 [RETIRED] Pre-restart numbers — never cite

Computed on raw data deleted 2026-07-09; inputs and run folders no longer exist, so per the
reproducibility rule they **cannot appear in the thesis as results**: exp001 baseline (MAE 6.21,
RMSE 8.80, bias −4.51 bpm); exp002 ECA+AHET (MAE 5.29, RMSE 7.03, bias −2.27 bpm). Retained in
`notes/approach.md` Part B **only** as narrative of how the method was arrived at. The
*qualitative* finding from cap3/exp004 — that a sustained 4·f_r ≈ HR coincidence produces 24–46%
NaN rates and −12 to −14 bpm bias, and that longer windows do not fix it — still stands as
mechanism evidence and may be cited **as such**, without the numbers.

---

## 11. Negative results and dead ends

**This is the most examiner-valuable section in the chapter, and it is fully writing-ready.**
Present these as a systematic elimination, not a list of mistakes.

### 11.1 Fixed-threshold harmonic proximity rule — FAILED
A fixed 0.08 Hz "exclude peaks near respiratory harmonics" test. Over-triggered whenever the
true cardiac peak sat near a harmonic. **Lesson: a fixed frequency tolerance is the wrong
abstraction**; the structural (2nd-harmonic) test is right. Directly motivates AHET.

### 11.2 Adaptive k_max — REJECTED by analysis
Lower k_max until the highest cancelled harmonic falls below the cardiac floor. Rejected: it is
a blunt instrument that drops *every* harmonic above the cut, including harmless ones worth
cancelling; at f_r ≈ 17–19 bpm it must fall to k_max ≤ 3, leaving substantial respiratory energy
uncancelled. Superseded by the targeted per-k skip.

### 11.3 ECA v1 is inert in the cardiac band — MEASURED
The production mode `skip_forbidden_harmonics_v1` removes **0.00 dB** in the cardiac band at low
breathing rates. The config's own comment says so. **The live system's harmonic cancellation is,
in the regime we mostly operate in, doing nothing** — an uncomfortable but important disclosure.

### 11.4 ECA v2 (raise the cancellation ceiling to 4 Hz) — REJECTED
Cancellation quality degrades as k·Δf_r: any error in the f_r estimate is multiplied by the
harmonic order, so high-order cancellation is unreliable by construction.

### 11.5 Stage 1A (harmonic coherence) — FAILED on both captures
Tests whether the high-order respiratory harmonic evidence is a coherent, cancellable
single-sinusoid line at all. It is not. **Licensed conclusion:** on these sessions, a stationary
single-sinusoid model of the high-order respiratory harmonics is unsupported. **Not licensed:**
that this is dead on physics permanently. *Procedural caveat: this verdict still owes its
independent cross-model review — treat as provisional in the chapter.*

### 11.6 Stage 1B round 1 (temporal continuity) — FAILED its own stated acceptance criteria
Cause diagnosed as a labelling error in the experiment design, not a property of the signal.

### 11.7 Stage 1B round 2 (one-hop motion statistic) — NO LEVERAGE
Real breathing-rate transitions ramp over ~10 hops (measured: 31 s, 30 s, 36 s across the
sweep's three transitions), so a one-hop delta sees almost no movement. Redesigned as a lag-10
statistic (§12.5).

### 11.8 Six rounds of design review, three of which caught defects in my own drafts
Draft 1 asserted a "frozen k̂" property the code did not have; draft 3 stated an optimisation
objective **backwards** (it would have tuned the veto to reject *more* true cardiac signal);
draft 5 conflated feasibility with utility, breaking the pass-all control's entire purpose.
**None were caught by re-reading; all three required either direct code verification or an
independent reviewer.** Worth a methodological paragraph on verification discipline.

### 11.9 Ahmed HA FMCW transfer — IMPLEMENTED CORRECTLY, DID NOT TRANSFER ACCURATELY

The corrected Ahmed implementation passed paper-derived harmonic-bin, normalization, strict
Nyquist, suppression-mask, factor-of-two, full-score-array, evidence-reconstruction, pairing,
and scoring controls. Its failure on real data is therefore retained as a scientific result,
not repaired by selecting a profile after observing Masimo error. Across the approved profiles,
HR MAE rose from 4.25–9.05 bpm in the small natural stratum to 17.77–25.46 bpm in paced data and
31.65–33.57 bpm in the unknown-protocol captures; bias was generally strongly negative. BR MAE
was 8.85–11.80 bpm. This result concerns the declared FMCW phase adaptation and cannot be
generalised to the original pulse-radar representation.

---

## 12. Open problems and limitations

### 12.1 The respiratory-harmonic coincidence limit — the central limitation

When 4·f_r ≈ HR (e.g. f_r = 20 bpm → 80 bpm, and resting HR ≈ 80 bpm), ECA's projection removes
**both** the respiratory harmonic and the cardiac fundamental — the cancellation is correct for
respiration and destructive for the heartbeat. Even with the collision harmonic skipped, the
respiratory energy remains *inside* the cardiac band competing with the true HR.

**Frame this precisely:** it is an **identifiability** problem, not a resolution problem. A
single-bin spectral estimator cannot separate two components at the same frequency, and longer
windows provably do not help (confirmed empirically on the retired data — bias was −12 to
−14 bpm at *every* window length). It is a limit **of this method**, not of the physics.

**Danger-zone width.** Governed by frequency resolution: at a 30 s window (~2 bpm/bin) the
genuinely destructive zone is roughly |HR − 4·f_r| ≲ 2–5 bpm. The protocol's "> 10 bpm" guard is
a deliberately conservative safety margin, **not** the physical width — state this, or a
reviewer will read the 10 bpm as a physical claim.

**Two escape routes, neither implemented** [PENDING]:
1. **Work from the 2nd cardiac harmonic** [R3, R4]. If 4·f_r = f_h the collision repeats at
   8·f_r = 2·f_h, but respiratory harmonic amplitude decays steeply with order, so the 8th is far
   weaker than the 4th — the 2nd cardiac harmonic survives where the fundamental does not.
2. **Multi-range-bin / MIMO coherent combination.** Different bins weight respiration and
   heartbeat differently, changing the ratio. We capture 4 RX and use none of them for this.

**Note that pacing makes this worse by construction:** natural breathing lets f_r wander so the
collision is intermittent; paced breathing *pins* f_r, so for a subject whose HR sits near 4·f_r
the collision is sustained for the entire recording. The 18 bpm arm is the **worst case**, not an
average one.

### 12.2 AHET cannot reject a respiratory harmonic — the root cause found by review
A respiratory harmonic **has genuine 2nd-harmonic structure**. AHET's verification therefore
cannot distinguish it from a heartbeat in principle. This is why the whole "cancel the harmonic
evidence" family (§11.4–11.5) was pursued and why it failed: the discriminant has to come from
somewhere other than harmonic structure.

### 12.3 Candidate ranking — "magnitude order ≠ credibility order" [OPEN, confirmed, n=1]
AHET evaluates candidates in descending magnitude order, but magnitude order is not credibility
order, so it can verify the wrong candidate. Confirmed as a real bug with one clean example. No
fix planned yet; deliberately not implemented ad hoc.

### 12.4 Respiration collapse [RESOLVED IN THE CURRENT PIPELINE; HISTORICAL FAILURE MODE]
The estimated f_r pins to the 0.1 Hz (6 bpm) search-band floor while the validity flag
`resp_valid` remains `True`. Observed on **all three Masimo-referenced captures** — `massimo1`
(12 hops), `massimo2` (5), `sweep` (10) — and **absent** from the unreferenced `live_test1`
(minimum BR 16.0 bpm) [VERIFIED 2026-07-24, measured from each run's `live_estimates.csv`].
Unowned, unfixed. If breathing rate is reported anywhere in the thesis, this must be disclosed.

> *Correction:* this was previously written as "4-for-4 on every Masimo capture." There are only
> **three** Masimo-referenced captures out of four raw captures, so that count was impossible.
> `HISTORY.md` (2026-07-14) carries the same error and is corrected by a later dated entry rather
> than edited.

**Current disposition:** the accepted production path now rejects band-edge collapse and applies
confidence/consistency gates. This historical mechanism remains important context, but it is no
longer an open implementation blocker. Ahmed's independently poor BR agreement in §10.3 is a
method-transfer result, not evidence that this production defect remains active.

### 12.5 Stage 1B lag-10 temporal continuity [DESIGNED, SCAFFOLDED, BLOCKED ON DATA]
The current line of attack: a respiratory harmonic *tracks* f_r over time; a heartbeat does not.
Uses a fixed lag L = 10 hops (~30 s), chosen from measured ramp durations and frozen as a design
constant. Six rounds of cross-model review; control scaffold implemented and verified (48/48
synthetic cases, 6/6 gap cases, hand-verified threshold selector).

**Blocked, and worth stating plainly:** re-processing all three sessions at the corrected bin
found **zero baseline severe errors** — the bin-lock fix (§9) appears to have already removed the
dominant real-world mechanism producing severe false accepts in this dataset. **The veto
currently has nothing to veto.** Its objective criterion is not derivable from an empty
population and is deferred to a new capture designed to *provoke* a collision.

### 12.6 Other declared limitations
- **Only four subjects** in all current data, not randomly sampled; no recorded demographic, BMI, or chest-morphology diversity.
- **Seated posture only**; no lying, standing, or motion arms.
- **Single-antenna, single-bin** processing.
- **20 Hz frame rate** — 5× below the primary reference's 100 Hz.
- **Masimo PR as ground truth** is itself an estimate, not an ECG-derived R-R interval; PR and
  ECG-derived HR differ physiologically (pulse transit, ectopy). This is a real assumption to
  flag, and an ECG reference would be the stronger design [CITATION NEEDED — Masimo MightySat
  PR accuracy specification from the device IFU/operator's manual, needed to bound the reference's
  own error].
- **Cross-model DSP review of the linalg-free path** (FFT-domain zero-phase masking replacing
  `filtfilt`; modified Gram–Schmidt replacing `np.linalg.qr`) has **not** been done. Required
  before this path is paper-grade.

### 12.7 Assumptions to declare explicitly (CLAUDE.md §4)
Far-field; a single dominant reflector in the range gate; stationarity of HR within a 30 s
window; Masimo PR as truth; chest displacement dominating other body motion; the subject
remaining still enough that the chest does not change range bin mid-capture.

---

## 13. Suggested chapter structure

| § | Content | Source here | Words (est.) |
|---|---|---|---|
| 3.1 | Introduction and research question | §1 | 800 |
| 3.2 | Background: FMCW ranging, phase sensing, signal model | §2 | 2000 |
| 3.3 | Related work | §4 | 2000 |
| 3.4 | System design and capture protocol | §3 | 1500 |
| 3.5 | Method: ECA + AHET, with justification | §5, §6 | 2500 |
| 3.6 | Range-bin selection and the mislock failure | §9 | 1500 |
| 3.7 | Evaluation methodology and the comparator problem | §7 | 2000 |
| 3.8 | Results | §10 | 2000 |
| 3.9 | Failure modes and negative results | §11, §12 | 2500 |
| 3.10 | Limitations and future work | §12.6, §12.1 | 1200 |
| 3.11 | Conclusion | — | 600 |

≈ 18,600 words — a full thesis chapter. §3.6, §3.7 and §3.9 are the parts a reader will not find
elsewhere in the literature; weight them accordingly.

---

## 14. References

**Verified full citations.** Items marked `[CITATION NEEDED]` must be completed before
submission — do not invent them.

**Primary method**
- **[R1]** Tang et al., "Adaptive Extensive Cancellation Algorithm and Harmonic Enhanced Heart
  Rate Estimation based on MMWave Radar," arXiv:2503.07062, 2025.
- **[R15]** Beltrão, G., et al., "Adaptive Nonlinear Least Squares Framework for Contactless
  Vital Sign Monitoring," *IEEE Trans. Microwave Theory and Techniques*, vol. 71, no. 4, 2023.
- **[R21]** Kotte, V.V., Ahmed, S., Alouini, M.-S., and Al-Naffouri, T.Y., "Joint Estimation of
  Single Target's High Amplitude Difference Doppler Frequencies in FMCW Radar," *IEEE
  Transactions on Radar Systems*, vol. 2, 2024, DOI: 10.1109/TRS.2024.3352189.

**Comparison methods**
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

**Foundations**
- **[R5]** Droitcour, A.D., Boric-Lubecke, O., Lubecke, V.M., Lin, J., Kovacs, G.T.A., "Range
  correlation and I/Q performance benefits in single-chip silicon Doppler radars for non-contact
  cardiopulmonary monitoring," *IEEE Trans. Microwave Theory and Techniques*, vol. 52, no. 3,
  pp. 838–848, 2004.
- **[R6]** Park, B.-K., Boric-Lubecke, O., Lubecke, V.M., "Arctangent Demodulation with DC Offset
  Compensation in Quadrature Doppler Radar Receiver Systems," *IEEE Trans. Microwave Theory and
  Techniques*, vol. 55, no. 5, pp. 1073–1079, 2007.
- **[R7]** Li, C., Lubecke, V.M., Boric-Lubecke, O., Lin, J., "A Review on Recent Advances in
  Doppler Radar Sensors for Noncontact Healthcare Monitoring," *IEEE Trans. Microwave Theory and
  Techniques*, vol. 61, no. 5, pp. 2046–2060, 2013.
- **[R8]** Alizadeh, M., Shaker, G., De Almeida, J.C.M., et al., "Remote Monitoring of Human
  Vital Signs Using mm-Wave FMCW Radar," *IEEE Access*, vol. 7, pp. 54958–54968, 2019.
- **[R11]** Beltrão, G., Stutz, R., Hornberger, F., Martins, W.A., et al., "Contactless
  radar-based breathing monitoring of premature infants in the neonatal intensive care unit,"
  *Scientific Reports*, vol. 12, 2022.

**Statistics and standards**
- **[R16]** Bland, J.M., Altman, D.G., "Statistical Methods for Assessing Agreement between Two
  Methods of Clinical Measurement," *The Lancet*, vol. 327, no. 8476, pp. 307–310, 1986.
- **[R17]** ANSI/AAMI EC13 — Cardiac Monitors, Heart Rate Meters, and Alarms. *Use for the
  clinical-acceptability threshold.* `[CITATION NEEDED — obtain the standard and quote the exact
  heart-rate accuracy clause and edition; the commonly-cited "±10% or ±5 bpm, whichever is
  greater" was NOT verifiable from open sources.]`
- **[R18]** ISO 80601-2-61 — Particular requirements for basic safety and essential performance
  of pulse oximeter equipment. `[CITATION NEEDED — confirm edition and the pulse-rate accuracy
  clause.]`

**Hardware and reference device**
- **[R9]** Texas Instruments, *IWR1642BOOST Evaluation Module User's Guide*.
  `[CITATION NEEDED — literature number and revision; PDF is in literature/IWR1642/.]`
- **[R10]** Texas Instruments, *DCA1000EVM Data Capture Card User's Guide*, **SPRUIJ4**.
  `[Verify revision letter against literature/IWR1642/DCA1000EVM_User_Guide.pdf.]`
- **[R19]** Texas Instruments, mmWave SDK User's Guide. `[CITATION NEEDED — literature number.]`
- **[R20]** Texas Instruments application note on vital-signs measurement with mmWave sensors.
  `[CITATION NEEDED — could not verify a document number; needed as the on-chip baseline citation.]`
- **[R12]** Masimo Corporation, *MightySat Fingertip Pulse Oximeter* — operator's manual /
  instructions for use. `[CITATION NEEDED — model number, document revision, and the stated pulse-rate
  accuracy specification. Masimo SET® is validated in >100 clinical studies per the manufacturer;
  cite the device documentation for the numeric specification, not marketing material.]`

**Also in `literature/ref_papers/`** (surveyed, not yet placed in the argument), both IEEE Trans.
Radar Systems vol. 2 (2024), both simulation-only:

- **Ahmed et al., "Discovering the Unseen"** (DOI 10.1109/TRS.2024.3412915). Its **Harmonic
  Accumulation is already the primary BR estimator** in `src/respiration.py`, but **adapted**: the
  paper's model is a **pulse radar with a single TX and single RX** (its Fig. 1) built on **2f_h
  and 2f_b and their harmonics** — the even-harmonic structure is intrinsic to its demodulated
  model — whereas ours is all-harmonic phase (f_b, 2f_b, 3f_b…). Its Fig. 8(c)–(d) claims correct
  estimation *at* the 4·f_r = HR collision (f_h = 80/60 Hz, f_b = 20/60 Hz, f_c = 6.7 GHz,
  d_h = 10 mm, d_b = 20 mm, SNR 10 dB) — the project's central unsolved problem. The corrected
  Figure 8 successor did **not** reproduce the declared 80 bpm heart target, and the paired
  FMCW real-data adaptation produced HR MAE ranges of 4.25–9.05 bpm (natural), 17.77–25.46 bpm
  (paced), and 31.65–33.57 bpm (unknown protocol), with predominantly negative bias
  [EXPLORATORY; §10.3]. This is not a test of the original pulse-radar hardware regime.
- **Kotte et al., "Joint Estimation of High-Amplitude Difference Doppler"**
  (DOI 10.1109/TRS.2024.3352189). **Correction to an earlier characterisation:** this paper is
  *not* run on a 4-RX SIMO setup like ours. Its §IV simulates **one TX and 20 RX** at 24 GHz,
  50 ms PRI, `N_c = 16`. Its eq. (23) defines `Y_t ∈ C^(N_c×n_R)` and eq. (25) requires `R_t⁻¹`
  where `R_t = E{Y_t Y_tᴴ}` is **N_c × N_c** — i.e. **the RX channels supply the snapshots**. With
  `n_R = 4`, `rank(R_t) ≤ 4` and `R_t` is singular, so the method does not transfer to this
  hardware without regularisation and an explicit rank treatment. Its Fig. 5 validation is on
  generic moving-target Dopplers (−1/−2, −1/4, 1/2.5 Hz), **not HR/BR**. Its *motivation* (lungs
  masking the heart within one range bin) does match ours. [VERIFIED 2026-07-24 against the paper.]

---

## 15. Figures and tables

Per the reproducibility rules, each must come from a committed script in `figures/`.

1. System block diagram — hardware, signal chain, live/offline split.
2. Signal-model illustration — respiration and cardiac spectra with harmonics annotated,
   cardiac band shaded (can be synthetic; label it as such).
3. **Ahmed Figure 8 successor [GENERATED]** — canonical output under
   `figures/generated/m8_ahmed_fig8/`, titled with the honest non-reproduction status.
4. **Ahmed real-data table [DATA READY]** — six profiles × two locks × three protocol strata,
   always reporting coverage with MAE/RMSE/bias and never ranking profiles post hoc.
5. **Range-profile heatmap with the locked bin marked** — from `steps/range_plot/`.
6. **The mislock figure** — correct bin vs skirt bin, phase spectra side by side, showing the
   spurious 63–67 bpm peak. High value; the evidence exists.
7. ECA before/after spectra with cancelled harmonics annotated.
8. AHET verification illustration — f_h1, the 2·f_h1 search region, accept and reject cases.
9. **Comparator comparison figure** — the same hops scored both ways, showing the 17× gap.
10. **Bland–Altman** — `scripts/plot_bland_altman.py` is the only implementation.
11. Radar HR vs Masimo PR time series with rejected windows shaded.
12. Coverage vs accuracy trade-off across gate settings.
13. Coincidence failure figure — the 18 bpm arm, |HR − 4·f_r| against error.

---

## 16. Path to a complete chapter

1. **Run the 10-subject study** (`notes/protocol.md`). Nothing algorithmic blocks it. This is the
   critical path for §3.8 and half of §3.10.
2. **Take one capture designed to provoke a 4·f_r ≈ HR collision** — needed to unblock §12.5 and
   to give §12.1 direct rather than retired evidence.
3. **Address coverage** (§10.2). Currently 10–46%; at that level a coverage-adjusted accuracy
   claim is weak regardless of MAE.
4. **Run the baselines** — TI on-chip output and one published pipeline. A thesis chapter without
   a baseline comparison is exposed.
5. **Close the cross-model DSP review** of the linalg-free path (§12.6).
6. **Fill every `[CITATION NEEDED]`** in §14 from the actual documents.

---

## 17. Appendix — inventory of work completed to date

> **Purpose.** A factual record of what exists, as of **2026-07-23** (branch `vital_signs_v9c`,
> HEAD `b6f5b73`). All counts measured directly from the repository on that date, not estimated.
> Update this section mechanically as the project grows; it is deliberately separate from the
> chapter argument above.

### 17.1 Codebase

**Core DSP library — `src/`, 2,612 lines**

| Module | Lines | Role |
|---|---|---|
| `vitals.py` | 1,030 | ECA projection + AHET verification — the core heart-rate DSP |
| `respiration.py` | 413 | Breathing-rate estimation (FFT + STFT agreement confidence) |
| `compare.py` | 329 | Agreement metrics (MAE/RMSE/bias), overlay plots |
| `intermediates.py` | 301 | Diagnostic-artefact schema and I/O |
| `radar_io.py` | 203 | Raw ADC parsing, LVDS layout, `iq_swap` handling |
| `windowing.py` | 203 | Window/hop segmentation |
| `masimo.py` | 132 | Reference-device CSV parser (epoch-timestamp alignment) |

**Offline pipeline — `steps/`, 6,420 lines.** Step 1 capture (644), Step 2 time-domain cubes
(257), Step 3 chest-bin selection (735), Step 4 quality mask (1,376), Step 5 breathing rate
(733), Step 6 heart rate (1,473) + temporal tracker (520), range-plot visualisation (682).

**Tooling and analysis — `scripts/`, 23 files, 9,704 lines.** The largest are `live_demo.py`
(1,449 — live capture, display, replay, and raw mirroring), `diagnose_step6_hr.py` (1,003),
`diagnose_coverage_gaps.py` (912), `stage1b_lag_statistic.py` (754), `diagnose_live_run.py`
(684), `diagnose_step6_candidate_tracks.py` (583). Eleven `diag_*.py` single-purpose
investigation scripts (2,943 lines combined) remain from specific diagnoses.

**Total: ≈ 18,700 lines** of production and tooling code, excluding tests.

### 17.2 Test suite — 797 test outcomes across 21 files: 796 passed, 1 xfailed

[VERIFIED 2026-07-24: `796 passed, 1 xfailed, 0 failed`.] **An xfail is not a pass** — the one
expected failure is the known design hole in the ECA/AHET decoy case, not a green test. Do not
write "797 passing tests." Distribution shows where
verification effort actually went:

| Test file | Tests | Target |
|---|---|---|
| `test_quality_mask.py` | 155 | Step 4 window gating |
| `test_step6_heart_rate.py` | 138 | Heart-rate extraction |
| `test_diagnose_step6_hr.py` | 105 | HR diagnostic tooling |
| `test_diagnose_coverage_gaps.py` | 99 | Coverage-gap analysis |
| `test_quality_mask_new.py` | 54 | Refactored quality-mask API |
| `test_diagnose_step6_candidate_tracks.py` | 53 | Candidate-track diagnostics |
| `test_respiration.py` | 34 | Breathing-rate DSP |
| `test_live_demo_warmup_helpers.py` | 27 | **Warmup bin-lock helpers (§9)** |
| `test_eca_ahet.py` | 22 | **ECA + AHET core (§6)** |
| Remaining 12 files | 110 | I/O, windowing, Masimo parsing, metrics, synthetic phase |

The synthetic-phase test (`test_vitals_synthetic.py`) is the canonical example of the
verify-each-stage-in-isolation discipline: a phase signal with known f_r and f_h is constructed,
and the pipeline must recover the planted heart rate.

### 17.3 Data assets

**Four real captures, ≈ 2.52 GB of raw ADC, all with saved raw streams enabling full offline
reprocessing** (`results/live_demo/`):

| Session | Raw bytes | ≈ duration | Reference | Role |
|---|---|---|---|---|
| `20260713_170323_..._live_test1` | 316,538,880 | ~121 s | none | First live smoke test |
| `20260713_172042_..._massimo1` | 473,169,920 | ~180 s | Masimo | Natural breathing |
| `20260713_182002_..._massimo2` | 473,300,992 | ~181 s | Masimo | Paced 16 bpm |
| `20260714_180523_..._sweep` | 1,259,732,992 | ~481 s | Masimo | Stepped 12→15→18→21 bpm |

*(Durations derived from byte count at 131,072 bytes/frame × 20 Hz = 2.62 MB/s; `HISTORY.md`
records the sweep as 486 s of wall-clock session, the difference being pre-recording framing.)*

Five further `replay_unknown` folders are **reprocessing artefacts, not sessions** — two from
bin-lock verification (2026-07-14) and three from the 2026-07-15 re-scoring of all sessions at
current HEAD. `data/raw/` and `data/processed/` are empty by design after the 2026-07-09 reset.

**Per-run artefacts** (what makes §11's failure analyses possible): `run_metadata.json` (config,
git commit, packet statistics), `warmup_bin_selection.json` (per-candidate bin-lock evidence),
`live_estimates.csv` (per-hop HR/BR with rejection reasons), `live_intermediates.npz` (phase,
spectra, AHET candidates, checkpointed every 60 s), and `adc_stream.bin` (the raw mirror).

### 17.4 Process record

- **`HISTORY.md`: 100 dated entries, ~5,800 lines** [measured 2026-07-24], append-only since
  2026-06-09. Contains the
  full record of what worked, what failed, and what was retired — the source for §11.
- **Cross-model design review** (CLAUDE.md §6) applied to every change touching phase extraction,
  filtering or peak-picking. The Stage 1B design alone went through **six rounds**; the warmup
  bin-lock fix through **three**.
- **Explicit written specifications** for the comparator and the analysis, applied uniformly to
  every estimator compared: `notes/comparator_prespec.md`, `notes/comparator_prespec_br.md`,
  `notes/analysis_prespec.md`, and `notes/note_stage1b_lag_statistic.md` (draft 7). **These are
  engineering specifications, not registrations** — no deposit exists and no claim is made about
  when they were written relative to the data.
- **Hardware capture paths**, both exercised: `live_demo.py` live mode (live display + raw
  mirror) and `steps/step_1/capture.py` standalone (headless, writes `data/raw/` + metadata with
  SHA-256 + manifest row).

### 17.5 Honest summary of the balance of work

The project is **heavily weighted toward infrastructure, verification and failure analysis**, and
**light on subject data**. Roughly 18,700 lines of code and 797 test outcomes support four captures from
four subjects. That ratio is not a criticism — the failure analyses in §11 and the
methodology in §7 are only possible because the diagnostic infrastructure exists, and they are
the chapter's most distinctive material. But it does identify the critical path precisely:
**every remaining gap in §0's status board is closed by collecting data, not by writing code.**
