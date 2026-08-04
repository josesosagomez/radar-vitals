# Approach — mmWave Heart-Rate Estimation

> **How to read this file.** It is split into three parts:
> - **Part A — Method (current).** Physics, literature, the chosen algorithm, and the
>   pitfalls that still govern the design. This is live: keep it current.
> - **Part B — Historical results (PRE-RESTART, NOT REPRODUCIBLE).** Empirical numbers
>   from the datasets deleted on 2026-07-09. Quarantined, never cited as results.
> - **Part C — Open questions for the new dataset.**
>
> Keep it honest: mark `[CITATION NEEDED]` rather than guessing.

---
---

# PART A — METHOD (CURRENT)

## 1. Problem
Estimate heart rate from a 76-81 GHz FMCW radar (IWR1642), subject seated (hands on
legs, back straight, facing the radar) at 0.8-1.4 m, radar facing the chest.
Reference: Masimo MightySat pulse rate.

---

## 2. Physical principle
Heartbeat and respiration cause sub-millimetre chest displacement. FMCW radar resolves
the chest into a range bin; the displacement shows up as a slow-time phase change at that
bin. Heart rate is recovered from the phase signal's spectral content in the cardiac band.
- Range resolution dR = c / (2 * B_sweep). Confirmed: 0.0436 m/bin at our chirp config.
- Slow-time sample rate = frame rate. Confirmed: ~20 Hz frames.
- Chest at 0.8-1.4 m falls in bins ~19-32 (warmup search gate [0.8, 1.4 m]). The bin is
  auto-selected at warmup per session, not pinned.

---

## 3. Pipeline (as currently implemented)

Standard phase-based chain. Each stage verified in isolation:

1. Parse raw ADC (IWR1642 2-lane LVDS, Complex1x, 4-word packets). SDK captures use
   `iq_swap=True` (SampleSwap=1); mmWave Studio captures use `iq_swap=False`.
2. Range FFT (fast time) → complex range profile per frame. **There is no shared
   "range FFT stage."** Two functions each compute their own Hann + `scipy.fft.fft`
   over the ADC axis: `src/warmup_select.py::range_energy_by_bin` (per-bin power, for
   the energy prior) and `src/respiration.py::extract_chest_phase` (the one whose bin
   value becomes phase). The numbering here is logical order, not a data-flow diagram —
   nothing consumes a range profile produced by an earlier step.
3. ~~Static clutter removal (subtract slow-time mean per bin).~~ **NOT IMPLEMENTED —
   corrected 2026-07-30.** This step was listed here, and copied from here into
   `HANDOFF.md`'s method summary, but no such stage exists in the production path.
   `src/respiration.py::extract_chest_phase` goes Hann window → range FFT → select the
   locked bin → `delta_before_mean` → cumsum, with nothing in between; there is no
   slow-time mean subtraction, MTI filter or clutter subtraction anywhere in `src/`.
   It is disabled on-chip too (`steps/step_1/capture.py` sends `clutterRemoval -1 0`
   and `calibDcRangeSig -1 0`).
   **Do not conflate this with `delta_before_mean`.** That cancels static *per-channel
   phase offsets* (which is all its docstring claims); it does **not** cancel additive
   static clutter in the range bin, which compresses the phase excursion and introduces
   harmonic distortion — a concern here specifically because HR rests on AHET
   second-harmonic verification and ECA on respiration harmonics.
   **Open decision:** justify the omission with a citation, or implement it — see
   `HISTORY.md` 2026-07-30. Relevant evidence: the 2026-07-28 captures contain static
   reflectors stronger than the subject (`notes/protocol.md`, "Scene behind the subject").
4. **Range-bin selection (warmup).** Not a post-FFT filter — it runs **the entire
   downstream chain, once per candidate bin**, and picks a winner
   (`src/warmup_select.py::run_warmup_selection`):
   a. `range_energy_by_bin` over every candidate → mean power per bin (FFT #1);
   b. the same again on the *settled* sub-window, after skipping `settle_skip_s`
      (default 5 s), so a settling transient cannot inflate a skirt bin (FFT #2);
   c. **for each candidate bin, call `run_window_dsp`** — steps 5–7 below in full:
      phase extraction, BR fusion, ECA, AHET;
   d. score each: `hr_valid` **+1000** (granted only if the bin's settled energy is
      within `energy_eligibility_min_settled_db`, default −12 dB, of the strongest
      candidate), BR confidence high/medium/low **+250/+100/−100**, `br_valid` **+50**,
      minus `5 ×` energy rank;
   e. **lock the winner for the rest of the session.** No manual or manifest pin in the
      current protocol. Evidence dumped to `warmup_bin_selection.json`.

   For the 0.8–1.4 m gate at 0.0436 m/bin that is 14 candidates, so warmup transforms
   its window ~16 times and runs the full HR/BR chain 14 times before the first reported
   estimate. It also means **any change to the downstream DSP can move the bin lock** —
   observed 2026-07-30/31 for both the M2 respiration fix and the clutter-removal A/B.
5. **Phase extraction.** `src/respiration.py::extract_chest_phase`, two methods —
   **corrected 2026-07-31; this entry previously described only the non-default one.**
   Production config (`scripts/live_demo_config.yaml`) sets `phase.method:
   delta_before_mean`:
   - `delta_before_mean` (**default**): conjugate product between consecutive frames,
     `v[n]·conj(v[n−1])`, averaged over (chirp, rx), take the angle, then `cumsum`.
     Differencing is *inside* this step, and there is **no `np.unwrap` call** — the
     per-frame increment is already wrapped into (−π, π] by `angle`.
   - `mean_phasor`: average the phasor over (chirp, rx), then `angle` + `np.unwrap`.
     This is the "arctan I/Q + unwrapping" chain — it is **not** what production runs.
6. Impulse-noise removal on the extracted phase (`src/vitals.py::remove_impulse_noise`,
   `phase.impulse_clip_rad`), applied in `run_window_dsp` after step 5.
7. Respiration estimate → ECA harmonic cancellation → cardiac band (0.8-2.0 Hz =
   48-120 bpm) → AHET second-harmonic verification (see §7).
8. **Windowing:** 30 s window. The live demo hops every 3 s (display cadence); the
   offline pipeline is what produces paper metrics.

**Live vs offline (CLAUDE.md §4).** The live demo's readouts use an online median
smoother over recent AHET-verified estimates and are a **sanity check, not a result**.
Paper metrics are computed offline by re-processing the run's saved raw `adc_stream.bin`.

**Window length sets HR resolution.** Δf = 1/T, so a 30 s window gives ~0.033 Hz ≈
**2 bpm** quantisation; 20 s gives ~3 bpm. Do not shorten the window to reduce warmup
latency — it coarsens accuracy and degrades the warmup bin lock.

**Statistical independence.** Consecutive 3 s-hop estimates share 27/30 s of data and are
**not independent**. Agreement statistics (MAE/RMSE/Bland-Altman) must be computed on
**non-overlapping 30 s windows**, or explicitly correct for the autocorrelation.

---

## 4. Known pitfalls

- **Wrong range-bin pick** — mitigated by warmup bin selection + scoring.
- **Respiration harmonics in the cardiac band** — the central problem. §4.1, §4.2.
- Spurious/noise peaks when SNR is low.
- Large body motion corrupting a window.
- Posture change shifting the chest bin mid-capture (protocol: subject stays still).

### 4.1 The harmonic interference problem

The chest-wall displacement signal is the *sum* of respiration and heartbeat harmonics:

    d(t) = Σ α_k sin(2π k f_r t) + Σ β_l sin(2π l f_h t)
            k=1..K_r                  l=1..K_h

Because respiration (f_r ~0.1-0.5 Hz, amplitude 1-12 mm) is much larger than heartbeat
(f_h ~0.8-2.0 Hz, amplitude 0.2-0.5 mm), its harmonics appear throughout the spectrum.

Worked example at f_r = 15 bpm = 0.25 Hz:
  - 2nd: 30 bpm = 0.50 Hz  (below cardiac band, benign)
  - 3rd: 45 bpm = 0.75 Hz  (below cardiac band, benign)
  - 4th: 60 bpm = 1.00 Hz  ← INSIDE cardiac band
  - 5th: 75 bpm = 1.25 Hz  ← also in band

Additionally, *intermodulation* products (f_h ± k·f_r, k·f_r ± l·f_h) fill the cardiac
band. Peaks separated by less than the frequency resolution merge into one, and the
stronger respiration harmonic wins the argmax.

**Failed approach (recorded, do not repeat):** a fixed 0.08 Hz proximity threshold to
exclude peaks near respiratory harmonics. It over-triggered whenever the true cardiac
peak sat near a harmonic. A fixed frequency tolerance is the wrong abstraction; the
principled version is the AHET second-harmonic consistency check (§7.2).

### 4.2 CRITICAL — respiratory-harmonic coincidence (drives the capture protocol)

**Coincidence condition:** when the 4th respiratory harmonic falls near the true cardiac
fundamental, i.e. **4 × f_r ≈ HR**. Example: f_r = 20 bpm = 0.33 Hz → 4th harmonic at
80 bpm, while true HR ≈ 80 bpm.

**Why ECA cannot suppress it:** ECA builds a harmonic subspace from the estimated f_r and
projects the phase signal onto its complement. If the 4th harmonic sits at the same
frequency as the cardiac fundamental, the projection removes **both** — the cancellation
is correct for respiration but destructive for the cardiac component. This is a physics
limitation of single-range-bin phase extraction, not an algorithm bug. It cannot be fixed
by a longer window (it is an SNR/identifiability problem, not a resolution problem).

**Observed behaviour under coincidence:** AHET correctly abstains (NaN) when no
second-harmonic evidence survives; when it does pass, the accepted candidate may be the
respiratory harmonic itself (a respiratory harmonic has genuine second-harmonic
structure, so AHET cannot distinguish it). Result: **high NaN rate plus large negative
bias**. See Part B for the measured magnitude on the retired data.

**Protocol remediation (this is why the capture protocol constrains breathing):** ensure
**|HR − k × f_r| > 10 bpm for k = 1..5** at capture time. Typical resting HR is 60-80 bpm,
so the danger zone is **f_r ≈ 15-20 bpm** (4×f_r = 60-80 bpm). Breathing at a steady
**13-16 bpm** puts 4×f_r at 52-64 bpm, below a typical resting HR.

**How wide is the danger zone, really?** The collision is governed by frequency
resolution, not by a fixed bpm rule. At a 30 s window (~2 bpm/cell) the genuinely
destructive zone is roughly **|HR − 4·f_r| ≲ 2-5 bpm**. The "> 10 bpm" guard above is a
deliberately conservative safety margin, not the physical width.

**Is it unfixable?** It is unfixable *for this method* — a single-range-bin spectral
estimator cannot separate two components at the same frequency (an identifiability
problem, not a tuning problem). It is **not** a limit of the physics. Two escape routes
exist and are **not implemented**:
- **Work from the 2nd cardiac harmonic** (§5.3, §5.4). If 4·f_r = f_h then the collision
  repeats at 8·f_r = 2·f_h, but respiratory harmonic amplitude decays steeply with order,
  so the 8th is far weaker than the 4th — the 2nd cardiac harmonic survives where the
  fundamental does not.
- **Multi-range-bin / Doppler coherent combination** — different bins weight respiration
  and heartbeat differently, changing the ratio.

> **DECISION (2026-07-09):** keep the paced arm at **12 / 15 / 18 bpm** as agreed. At
> 18 bpm, 4×f_r = 72 bpm sits inside the resting-HR band, i.e. **deliberately inside this
> failure zone**; 15 bpm (4×f_r = 60) is borderline for a low-resting-HR subject. This is
> accepted as an **empirical question to settle on the new data** — the cap3 evidence
> (Part B.3) came from an earlier pipeline configuration, before the adaptive K_b guard,
> the peak-to-floor gates and warmup bin selection.
>
> Two conditions on this decision:
> 1. **Note that pacing makes this failure mode worse, by construction.** Natural
>    breathing lets f_r wander, so 4·f_r drifts across the HR and the coincidence is only
>    intermittent. Paced breathing *pins* f_r, so for a subject whose HR sits near 4·f_r
>    the collision is **sustained for the entire recording**. The 18 bpm arm is therefore
>    the worst case, not an average one.
> 2. **Report the 18 bpm arm separately.** Its windows must never be pooled into the
>    headline agreement metrics (that would import a known failure mode into the top-line
>    MAE). Reported on its own it is a legitimate contribution: a characterisation of
>    where the method breaks.
>
> Revisit once the new data is in: if the failure reproduces, report it as a documented
> limitation; if it does not, record why (which of the new guards saved it).

**Detection heuristic:** before committing to a recording, check Masimo PR and the
radar-estimated f_r. If |HR − 4 × f_r| < 10 bpm, the capture is at high risk.

---

## 5. Literature survey (arXiv, 2024-2025)

### 5.1 Adaptive ECA + AHET (our primary reference — the method we implement)
Tang et al., "Adaptive Extensive Cancellation Algorithm and Harmonic Enhanced Heart Rate
Estimation based on MMWave Radar," arXiv:2503.07062, Zhejiang/ASU/Wuhan, 2025.

**Strategy:** cancellation-before-estimation.

**Step 1 — Adaptive ECA (Extensive Cancellation Algorithm):**
- Use ANLS (Adaptive Non-Linear Least Squares) to estimate f_r and reconstruct the
  respiration signal plus its first K_b harmonics (typically K_b = 3; for f_r = 0.25 Hz
  we use K_b = 4 to cover the 4th harmonic at 1.0 Hz = 60 bpm).
- Build subspace matrix X from the harmonic Vandermonde structure.
- Project phase signal θ onto the complement: θ_ECA = P·θ where P = I - X(X^T X)^{-1} X^T.
- Result: respiration and its harmonics are removed from the phase signal in time domain.

**Step 2 — Adaptive Harmonic Enhanced Trace (AHET):**
- Search for the largest FFT peak f_h1 in the cardiac band [0.7, 2.0] Hz.
- Validate: look for the 2nd HR harmonic f_h* in [2·f_h1 - V_e, 4.0] Hz (V_e = 0.1 Hz).
- Credibility: |2·f_h1 - f_h*| ≤ V_e → accepted. Else: try 2nd-largest peak f_h2, re-check.
- Fallback: if both fail, use history (mean of first 5 stable estimates ± V_a = 0.1 Hz).

**Reported results (77 GHz, 100 Hz frame, 20 s CPI):**
- Raw: RMSE 14.41 bpm → after ECA: 6.37 bpm → after AHET: 1.20 bpm.

**Key note for us:** the paper explicitly states that the 4th harmonic of f_r and the
intermodulation term f_h − f_r merge within the frequency resolution and produce a
competing peak — exactly the failure mode in §4.2.

### 5.2 Harmonic MUSIC (HMUSIC)
Hsieh et al., "Harmonic MUSIC Method for mmWave Radar-based Vital Sign Estimation,"
arXiv:2408.01951, National Taipei University of Technology, 2024.

**Strategy:** super-resolution subspace method that jointly estimates f_r and f_h by
modelling both as harmonic sources. The HMUSIC pseudo-spectrum minimises projection onto
the noise subspace of the slow-time covariance matrix.
- Models phase as L harmonics of respiration + L harmonics of heartbeat simultaneously.
- Avoids explicit harmonic cancellation; resolves both sources jointly.
- 88th percentile HR error < 5 bpm (4 subjects, 12.8 s window, 60 GHz, cluttered room).
- Requires knowing L and the number of sources; heavier computation.

### 5.3 Second-derivative + VME (working at higher harmonics)
Iwata et al., "Accurate Radar-Based Heartbeat Measurement Using Higher Harmonic
Components," arXiv:2407.07380, Kyoto/Nagoya, 2024.

**Strategy:** shift the problem up in frequency where respiratory interference is weaker.
- Compute |d²s/dt²| of the complex radar signal: acts as a spectral high-pass that
  emphasises cardiac harmonics while suppressing respiratory components.
- The 2nd cardiac harmonic (2·f_h ≈ 2-3.4 Hz) lies above the worst respiratory harmonics.
- Apply VME (Variational Mode Extraction) to extract the 2nd harmonic mode; divide by 2.
- Advantage: bypasses phase unwrapping. Disadvantage: needs long windows (60 s).
- RMSE of IBI decreased 23%, CC improved 0.20 vs conventional phase methods.

### 5.4 Nonlinear Harmonic Spectrum (NLHS)
Shimomura et al., "A Nonlinear Spectral Approach for Radar-Based Heartbeat Estimation
via Autocorrelation of Higher Harmonics," arXiv:2507.20664, Kyoto, 2025.

**Strategy:** localised spectral autocorrelations summed across harmonic orders.
- Gaussian-smooth d(t), then compute 2nd derivative d''(t).
- NLHS(f) = Σ_n c(n·f, f), where c(f0, Δf) is a localised autocorrelation of the Fourier
  transform around f0 with lag Δf. When Δf = f_h, autocorrelations at n·f_h peak
  simultaneously → sharp NLHS peak. Respiratory harmonics lack this structure.
- Reduces RMSE by 20%, CC by 0.20 vs the best conventional baseline (60 s, 60 GHz).

### 5.5 NRBO-VMD (mode decomposition)
Gu et al., "Improved VMD Based Remote Heartbeat Estimation Utilizing 60GHz mmWave Radar,"
arXiv:2502.11042, UESTC, 2025.

**Strategy:** auto-tune VMD parameters (K, α) with a Newton-Raphson-based optimizer
minimising sample entropy, then select IMFs in 0.5-2 Hz and reconstruct.
- RMSE 5.208 bpm, 94% accuracy (18 subjects, 60 s windows).
- Measured from the back at 20 cm — different geometry to our chest-facing 0.8-1.4 m
  setup. Less directly applicable; included for completeness.

### 5.6 Pi-ViMo (template matching)
Zhang et al., "Pi-ViMo: Physiology-inspired Robust Vital Sign Monitoring using mmWave
Radars," arXiv:2303.13816, McMaster/Huawei, 2023.

**Strategy:** time-domain template matching with physiological models (RC-circuit
respiration + Van der Pol heartbeat oscillator), bypassing frequency-domain harmonic
confusion entirely.
- HR error 11.9% stationary, 13.6% with micro-RBMs.
- ~4.3 s of computation per 15 s window on an i7 → not real-time.
- Interesting as a theoretical ceiling; not our chosen approach.

### 5.7 Ahmed et al. HA - M8 Step 1a reproduction control

Ahmed et al., "Discovering the Unseen" (DOI 10.1109/TRS.2024.3412915),
uses a single-TX/RX pulse-radar slow-time model whose demodulated spectrum is
built on `2*f_b`, `2*f_h`, and their harmonics. This is not the same model as
the production all-harmonic phase estimator in `src/respiration.py`, so the
paper control is isolated in `src/m8/ahmed_fig8.py`; it does not import or
modify production DSP.

The paper is internally inconsistent at the Fig. 8(c)-(d) collision:
equation (26)'s prose says to zero the breathing bin and its multiples, nearby
prose says to discard frequencies at or below breathing, while Fig. 8(d)
visibly retains the disputed bins. M8 therefore reports three named profiles
(`eq26_multiples_suppressed`, `prose_low_or_equal_suppressed`, and
`figure_visible_unsuppressed`) rather than asserting unpublished author intent.

**Fixed-assumption Step 1a outcome (2026-07-29): negative.** With the approved
equal-amplitude, `theta0=0`, raw-power 10 dB SNR, five-breath, `N_f=4096`,
seed-42 reconstruction, breathing is selected at about 20.007 bpm for both
`H=3` and `H=5`, but the figure-visible heart sweep selects a subharmonic:
about 40.014 bpm for `H=3` and 20.007 bpm for `H=5`, not the expected 80 bpm.
None of the predeclared one-factor audit variants selects 80 bpm. The literal
equation-(26) profile also excludes the collision target by definition. The
status is therefore `not_reproduced_under_declared_assumptions`; this is not a
parameter-tuning prompt and must not be relabelled as a reproduction success.
The lightweight evidence is generated by `figures/reproduce_ahmed_fig8.py` from
`experiments/m8_ahmed_fig8/config.yaml`; the build authority and caveats are
in `plans/m8_step1a_ahmed_reproduction.md`. The clean-commit canonical bundle is
`figures/generated/m8_ahmed_fig8/20260729T075443.145998Z_8e08f5ab0120/`.

**Step 1a is not a real-capture evaluation.** It proves that the isolated
simulation, score curves, ambiguity profiles, evidence schema, and
`as_window_estimate` record boundary exist. It does not make the simulator a
`WindowEstimator`, does not define how Ahmed's fixed-fast-time pulse-radar
sample maps to this project's complex FMCW range-bin data, and does not make
`scripts/score_offline.py` estimator-pluggable. Consequently, no Ahmed estimate
has yet been produced for `demo_massimo1` through `demo_massimo7` or
`demo_sweep`, and the Step 1a negative result cannot be used to claim that the
method does or does not improve real-capture HR/BR.

**Step 1b plan reviewed; implementation awaits approval (2026-07-30).**
`plans/m8_step1b_ahmed_transfer.md` is the decision-complete build authority
(SHA-256
`9294cb0589b9f0d8f50cdfa0ea893862b1f8ac7f26eb6fee31ee57d622da33ac`).
Independent architecture, mathematical, Python, testing, and adversarial
reviewers all passed those exact bytes. No Step 1b code or real-capture result
exists yet.

The review corrected the roadmap's loose “all-harmonic phase model” wording.
For the declared coherent phasor with sinusoidal breathing and heartbeat
displacements, delta/unwrapped phase contains the two displacement fundamentals;
the phasor's Bessel/mixed harmonics do not survive ideal phase extraction.
The primary synthetic adaptation is therefore honestly named
`phase_fundamentals_only_transfer_v1`. “All-harmonic” describes the fixed-\(H\)
accumulator's \(q,2q,\ldots,Hq\) candidate convention, not fabricated signal
content. Candidate frequency is \(q=f\), rate is \(60q\) bpm, and full support
requires strict \(Hq<f_s/2\). Consequently, \(H=5\) at 100 bpm requires only
8.33 Hz and is supported at the captures' 20 Hz frame rate; the 120 bpm endpoint
is Nyquist-degenerate and excluded. The earlier \(16.67\) Hz blocker applied to
Step 1a's \(q=2f\) paper model and is not transferred to Step 1b.

The approved-order boundary remains synthetic first, real data second. All
scientific/runner/scorer code and portable tests are implemented before the
synthetic gate, but no real path is touched until a clean,
`promotion_eligible=true` gate and one comprehensive pre-data authorization
exist. Any negative transfer is recorded rather than tuned; descriptive real
continuation would additionally require a gate-bound user rationale. Real
outputs use the unchanged `delta_before_mean` phase mapping, six preregistered
\(H\)/suppression arms, separate recorded/rerun lock estimands, the exact
non-overlapping 30 s grid, and Masimo-independent radar artifacts. `k=0` is
retained as lock-selection-in-sample diagnostics; only `k>=1` supports
comparative accuracy. All eight captures remain single-subject
development/apparent evidence with approximate timing, protocol-stratified
descriptive summaries, and no production-promotion claim.

---

## 6. Method decision — ECA + AHET

**Chosen: ECA + harmonic consistency check (§5.1).**

Rationale:
- Directly addresses our documented failure mode (4th harmonic / intermodulation).
- Works at our window length and 20 Hz frame rate (no 60 s-window requirement).
- Implementable in scipy/numpy without new dependencies.
- ECA is a linear projection — invertible, no information destroyed, diagnosable.
- The AHET consistency check (2×f_h verification) is the principled version of the
  "harmonic proximity" idea that failed with a fixed threshold (§4.1).

**Not chosen, and why:**
- **HMUSIC:** assumes a multi-antenna/MIMO correlation matrix; single-bin phase signals
  have lower rank than assumed. Harder to implement correctly.
- **|d²s/dt²| + VME** and **NLHS:** designed for 60 s windows and IBI/HRV, not per-window
  HR rate. Good future direction for HRV work.
- **Pi-ViMo:** computationally impractical for real time.

---

## 7. Algorithm specification (ECA + AHET)

### 7.1 Phase 1 — ECA (respiration subspace cancellation)

Input: unwrapped phase vector θ[n] for one window (N = 600 at 30 s × 20 Hz).

1. **Estimate f_r:** FFT of θ[n], argmax in [0.1, 0.5 Hz] (respiration band). Refine
   beyond the raw FFT bin with parabolic interpolation.
2. **Build harmonic Vandermonde subspace X** (N × 2K_b):
     X[:,k] = sin(2π·(k+1)·f_r·t_n) and cos(2π·(k+1)·f_r·t_n), k = 0..K_b-1.
   Both sin and cos so each harmonic's phase offset is free.
3. **Project:** θ_ECA = θ − X·(X^T X)^{-1}·X^T·θ, computed via a **QR / modified
   Gram-Schmidt projection, not an explicit matrix inverse** (numerical stability; the
   current implementation is linalg-free — see the note below).
4. **Verify:** the FFT of θ_ECA should show the respiration peak and harmonics gone. Log
   the cancelled power at each harmonic as a diagnostic.

**K_b selection (adaptive).** Two modes exist in `eca_project`; the live one is
`skip_forbidden_harmonics_v1` (set in both `scripts/live_demo_config.yaml` and
`steps/step_6/config.yaml`):

- `legacy` — include harmonic k if k·f_r < 2.0 Hz, **but with a hard floor that always
  projects out k = 1..4** regardless of where they land. The 0.15 Hz "don't suppress near
  the cardiac candidate" guard applies **only to k ≥ 5**, so it does *not* protect against
  the common 4·f_r collision. This is the B.5 root cause. Retained only for comparison.
- `skip_forbidden_harmonics_v1` **(live)** — additionally skip any k whose k·f_r falls
  inside the cardiac band (± `eca_forbidden_guard_hz`), including k ≤ 4.

Skipping a colliding harmonic prevents ECA from erasing the cardiac peak, but it leaves
respiratory energy inside the cardiac band competing with the true HR. The coincidence is
therefore still an identifiability problem (§4.2) — the mode changes the failure from
silent cancellation to detectable contamination; it does not recover the signal when the
two genuinely coincide. See B.5.

### 7.2 Phase 2 — Cardiac peak search + AHET consistency

Input: θ_ECA.

5. **Spectral estimate:** windowed FFT of θ_ECA (Hann).
6. **Candidate peak:** argmax in the cardiac band [0.8, 2.0 Hz] → f_h1.
7. **2nd-harmonic consistency check (AHET):**
   - Search for a peak f_h* **locally** in [2·f_h1 ± V_e], V_e = 0.1 Hz (local, *not* a
     global search out to 4.0 Hz).
   - If |2·f_h1 − f_h*| ≤ V_e → credible, return f_h1.
   - Else: try the 2nd-largest cardiac-band peak f_h2 and re-check.
   - If both fail: flag low-confidence, return NaN (excluded from MAE/RMSE).
     **Do NOT substitute a default value** — that was the original failure mode.
8. **Output:** HR = f_h × 60 bpm, plus a per-window confidence flag.

### 7.3 Mandatory diagnostic outputs (CLAUDE.md §5 rule 4)

Per window, dump: estimated f_r; power cancelled at each harmonic; the full θ_ECA
spectrum; f_h1, f_h*, credibility flag; final HR. This is what makes a wrong reading
diagnosable rather than merely observable. Implemented as `live_intermediates.npz` +
`live_estimates.csv`, inspected by `scripts/diagnose_live_run.py`.

### 7.4 Where the parameters live

Live/replay: `scripts/live_demo_config.yaml`. Offline: `steps/step_6/config*.yaml`.
Key parameters: harmonics to cancel (K_b), AHET deviation tolerance V_e, history
stability V_a, respiration band for f_r, and the candidate gates
(`candidate_min_peak_to_floor_db`, `candidate_min_prominence`, …).

### 7.5 AHET criterion — known limitations (honest caveats, still open)

These were established on the retired data but are **properties of the criterion**, not of
that dataset, and remain open until re-validated:

- **The pass threshold is soft.** The pass rule is
  `second_peak_magnitude / comparison_floor > 1.0`, where the floor is the median
  cardiac-band magnitude. Observed ratios clustered just above 1.0, so the criterion is
  weakly discriminative. Any threshold change must be validated on independent data.
- **A passing ratio does not guarantee HR accuracy.** The ratio confirms second-harmonic
  *structure* exists; it does not confirm the fundamental is cardiac rather than a
  respiratory harmonic (which also has genuine second-harmonic structure — §4.2).
- **Longer windows mechanically inflate pass rates.** The ±0.1 Hz search region around
  2×f_h spans ~5/5/7 FFT bins at 20/25/30 s windows. Under an idealised null, the chance
  the max of m bins exceeds the band median is 1 − 0.5^m ≈ 0.97-0.99. So **AHET pass rates
  must not be compared across window lengths** without this caveat.
- **No validated rejection population yet.** Any pass/fail-rate claim needs a dataset
  containing genuine rejections.

**Planned validation (deferred to the new dataset):** Monte-Carlo with four synthetic
conditions per window length (genuine_harmonic, missing_harmonic, noise_only,
resp_competitor), a development/held-out seed split, adequacy rule = Wilson 95% CI upper
bound on false-pass rate < 0.10, and predeclared alternative criteria (`local_prominence`,
`peak_local_median`) compared by ROC. Use production rFFT sizes (not zero-padded).
**Any criterion selected on synthetic data must be confirmed on an independent real
capture before it is used to interpret results.**

### 7.6 Implementation note — linalg-free DSP path (needs cross-model review)

`src/vitals.py`'s `bandpass_filter` is zero-phase FFT-domain masking (was
`scipy.signal.filtfilt`) and the ECA projection is modified Gram-Schmidt (was
`np.linalg.qr`). These were rewritten to fix a crash, and they sit on the shared Step-6
path. A same-model reproducibility spot check passed, but the **CLAUDE.md §6 independent
cross-model DSP review has not been done** — required before this path is paper-grade.

---

## 8. Evaluation plan
- Metric: HR error vs Masimo PR — MAE (bpm), RMSE (bpm), Bland-Altman limits of agreement.
- Computed on **non-overlapping 30 s windows** (§3), reporting **coverage (% of windows
  with a reported HR) alongside accuracy** — never accuracy on surviving windows alone.
- Conditions: seated (hands on legs, back straight, facing radar), chest 0.8-1.4 m;
  **10 subjects × 3 sessions (natural + paced + recovery)**. Posture fixed; distance varies
  within the warmup search range.
- **HR agreement is claimed only on sessions carrying HR dynamic range.** Measured 2026-07-31
  (`scripts/diagnose_signal_presence.py`): across all 8 pilot captures the within-session PR
  spread was narrower than the agreement tolerance, and a constant predictor was correct on
  **100%** of admissible windows in every one — such sessions cannot falsify an HR claim.
  Session 3 (seated post-exertion recovery, ethics-approved 2026-08-03) supplies the range;
  see `notes/protocol.md`. BR is unaffected — the stepped-sweep capture already demonstrates
  BR tracking.
- Quality gate: exclude/flag Masimo segments with low Perfusion Index. Never tune the
  radar algorithm to chase a low-PI reference segment.
- Baselines: TI on-chip vital-signs output; a published phase-based pipeline.

---

## 9. References

- [x] Tang et al. 2025, ECA+AHET: arXiv:2503.07062
- [x] Hsieh et al. 2024, HMUSIC: arXiv:2408.01951
- [x] Iwata et al. 2024, |d²s/dt²|+VME: arXiv:2407.07380
- [x] Shimomura et al. 2025, NLHS: arXiv:2507.20664
- [x] Gu et al. 2025, NRBO-VMD: arXiv:2502.11042
- [x] Zhang et al. 2023, Pi-ViMo: arXiv:2303.13816
- [ ] TI application note: vital signs with mmWave sensors `[CITATION NEEDED]`
- [ ] TI raw ADC data capture / DCA1000 data-format app note `[CITATION NEEDED]`
- [ ] Beltrão et al. 2023, ANLS framework (cited in 2503.07062): IEEE TMTT vol.71 no.4

---
---

# PART B — HISTORICAL RESULTS (PRE-RESTART — NOT REPRODUCIBLE, NOT CITABLE)

> **All numbers below were computed on raw data that was deleted on 2026-07-09** (see
> HISTORY.md, "Hard reset of all datasets"). The inputs, the `results/` run folders and
> the `experiments/` configs they reference **no longer exist**, so per CLAUDE.md
> reproducibility rule 1 ("if it can't be regenerated, it does not go in the paper")
> **none of these may be reported as results or used as a baseline.** They are retained
> only as the narrative of how the method was arrived at, and as the *qualitative*
> evidence for the §4.2 failure mode.

### B.1 exp001 — baseline (argmax, no harmonic rejection)
Fixed bin 29; 20 s window / 10 s step; plain argmax in the cardiac band.
MAE 6.21 bpm, RMSE 8.80 bpm, bias −4.51 bpm over 21 windows.
*Superseded:* the bin is now auto-locked at warmup, the window is 30 s, and argmax alone
is not the method. Evidence folder deleted.

### B.2 exp002 — ECA + AHET
Best: MAE 5.29 bpm, RMSE 7.03 bpm, bias −2.27 bpm (20/21 windows, 1 NaN), fixed bin 29.
With an EMA outlier gate: MAE 6.98 bpm, RMSE 8.80 bpm, bias −5.15 bpm (2 NaN) — the EMA
added lag and was not retained.
Stopping note at the time: further tuning on that single capture was fitting to one
20-second event; a second capture was required to assess generalisation.
*Superseded:* different bin strategy, window, and tracker. Evidence folder deleted.

### B.3 cap3 / exp004 — evidence for the §4.2 coincidence failure
The qualitative finding here **still stands and drives the capture protocol**, even though
the numbers are not reproducible:
- f_r held at 19-20 bpm; true HR (Masimo) 78-84 bpm → 4 × 20 = 80 bpm ≈ HR → coincidence
  throughout the recording.
- NaN rate 17/37 = 46% at 20 s windows; 9/37 = 24% at 25 s.
- Bias on the finite windows: −12 to −14 bpm at every window length.
- Longer windows did **not** resolve it, confirming it is an SNR/identifiability problem
  rather than a frequency-resolution one.

### B.4 Offline hop/window sweep (retired)
A hop-1 grid sweep over the retired sessions selected a 30 s window over 20 s, and
established that the 30 s window needs a stricter fundamental floor (min_fund_db 4.0 vs
2.0) to suppress spurious peaks at the finer 2 bpm/bin resolution; with that floor, a
looser max_jump (6.0) is safe. **This coupling is why the current configs look as they
do.** Full numbers in HISTORY.md (2026-07-09 entry); output folders deleted.

### B.5 ECA unconditional-harmonic-removal root cause (found, then fixed)
The mechanism behind the §4.2 coincidence failure, diagnosed on the exp004 captures. The
numbers are not reproducible (data deleted) but **the mechanism is a property of the code,
not of the dataset**, so it is recorded here.

*Root cause:* `eca_project()` removed harmonics k = 1..k_max from the phase spectrum with a
**hard floor that always projected out k = 1..4**, irrespective of where those harmonics
landed. When k·f_r fell inside the cardiac band, ECA therefore erased the cardiac signal
along with the respiratory harmonic. This is why a longer window never helped: the signal
was being cancelled, not under-resolved. Observed at f_r = 17-19 bpm (4·f_r = 68-76 bpm,
squarely in the resting-HR band). Failure was much worse when cardiac SNR was low, because
a strong cardiac return can survive as a residual whereas a weak one cannot.

*Rejected alternative — adaptive k_max.* The first proposed fix was to lower k_max until the
highest suppressed harmonic fell below the cardiac floor:
`k_max = floor((0.833 Hz − 0.083 Hz margin) / f_r)`, clamped to [1, 6]. It was **not adopted**:
it is a blunt instrument — it drops *every* harmonic above the cut, including ones that are
harmless and worth cancelling, and for f_r ≈ 17-19 bpm it must fall to k_max ≤ 3 to spare
4·f_r, which leaves substantial respiratory energy uncancelled. Superseded by the targeted
skip below, which removes only the specific colliding k.

*Fix (live):* `eca_mode: skip_forbidden_harmonics_v1` — skip any k whose k·f_r falls inside
the cardiac band (± `eca_forbidden_guard_hz`). Set in **both** `scripts/live_demo_config.yaml`
and `steps/step_6/config.yaml`; implemented via `skip_ks` in `src/vitals.py:eca_project`.

*Residual caveat — this is a trade, not a cure.* Skipping the harmonic stops ECA erasing
the cardiac peak, but it leaves respiratory energy sitting **inside** the cardiac band,
where it competes with the true HR. Which one wins depends on cardiac SNR at that
frequency. So the coincidence remains an identifiability problem (§4.2) and the 18 bpm
paced arm remains a genuine probe of it — the fix changes the failure from *silent
cancellation* to *contamination*, which is at least detectable.

---
---

# PART C — OPEN QUESTIONS FOR THE NEW DATASET

1. **Does the §4.2 coincidence failure still reproduce?** The paced arm deliberately
   includes 18 bpm (4×f_r = 72 bpm, inside the resting-HR band) to measure this on the
   new pipeline. Record each subject's resting HR so the per-session |HR − 4·f_r| margin
   is known. Report the 18 bpm arm separately; never pool it into the headline metrics.
2. **Coverage/yield.** The retired data showed a valid-HR fraction swinging from ~0% to
   ~85% per session, dominated by AHET gate rejections. Establishing a *reliable* yield is
   the precondition for any credible agreement claim — accuracy measured only on surviving
   windows is selection bias (§8).
3. **Validate the AHET criterion (§7.5)** on data containing a genuine rejection
   population.
4. **Independent cross-model DSP review** of the linalg-free `src/vitals.py` path (§7.6).
5. **Baselines:** produce head-to-head numbers vs TI on-chip output and a published
   phase-based pipeline.
