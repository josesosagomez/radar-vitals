# Approach — mmWave Heart-Rate Estimation

> Fill this in BEFORE writing pipeline code (research-first). Use the arXiv MCP to find
> references; have ChatGPT review the plan for what a reviewer would attack.
> Keep it honest: mark `[CITATION NEEDED]` rather than guessing.

---

## 1. Problem
Estimate heart rate from a 76-81 GHz FMCW radar (IWR1642), subject seated/standing at
1.3-1.6 m, radar facing the chest. Reference: Masimo MightySat pulse rate.

---

## 2. Physical principle
Heartbeat and respiration cause sub-millimetre chest displacement. FMCW radar resolves
the chest into a range bin; the displacement shows up as a slow-time phase change at that
bin. Heart rate is recovered from the phase signal's spectral content in the cardiac band.
- Range resolution dR = c / (2 * B_sweep). Confirmed: 0.0436 m/bin at our chirp config.
- Slow-time sample rate = frame rate. Confirmed: ~20 Hz frames (3000 frames / ~150 s).
- Chest at 1.3-1.6 m falls in bins 25-34 (gate [1.1-1.5 m]). Locked bin: 29 (1.264 m).

---

## 3. Candidate pipeline — baseline (exp001, implemented and verified)

Standard phase-based chain. Each stage verified in isolation:

1. Parse raw ADC (IWR1642 2-lane LVDS, Complex1x, 4-word packets).
2. Range FFT (fast time) → complex range profile per frame.
3. Static clutter removal (subtract slow-time mean per bin).
4. Range-bin locking: select bin once from full post-trim cube (max phase variance in
   gate), hold fixed for all windows. Eliminates bin-hopping artefacts.
5. Phase extraction (arctan I/Q) + phase unwrapping on the full continuous slow-time.
6. Phase differencing / impulse-noise removal.
7. Bandpass filter → cardiac band (0.8-2.0 Hz = 48-120 bpm).
8. Sliding 20 s FFT window (step 10 s) → argmax peak in cardiac band → bpm.

**exp001 result (locked bin 29):** MAE 6.21 bpm, RMSE 8.80 bpm, bias −4.51 bpm,
21 windows. Evidence: `results/exp001_offline_baseline/20260610_223845/`.

---

## 4. Known pitfalls

- **Wrong range-bin pick** — now fixed by bin locking.
- **Respiration harmonics in cardiac band** — OUR CURRENT BLOCKER. Detailed below.
- Spurious/noise peaks when SNR is low.
- Large body motion corrupting a window.
- Posture change shifting the chest bin mid-capture.

### The harmonic interference problem (root cause of exp001 failure)

The chest-wall displacement signal is the *sum* of respiration and heartbeat harmonics:

    d(t) = Σ α_k sin(2π k f_r t) + Σ β_l sin(2π l f_h t)
            k=1..K_r                  l=1..K_h

Because respiration (f_r ~0.1-0.5 Hz, amplitude 1-12 mm) is much larger than heartbeat
(f_h ~0.8-2.0 Hz, amplitude 0.2-0.5 mm), its harmonics appear throughout the spectrum.

In our capture: f_r ≈ 15 bpm = 0.25 Hz. The harmonics fall at:
  - 2nd: 30 bpm = 0.50 Hz  (below cardiac band, benign)
  - 3rd: 45 bpm = 0.75 Hz  (below cardiac band, benign)
  - 4th: 60 bpm = 1.00 Hz  ← INSIDE cardiac band, competed with true HR ~71 bpm
  - 5th: 75 bpm = 1.25 Hz  ← also in band

Additionally, *intermodulation* products (f_h ± k·f_r, k·f_r ± l·f_h) fill the cardiac
band. With our 20 s window the frequency resolution is 3 bpm — peaks separated by <3 bpm
merge into one, and the stronger respiration harmonic wins the argmax.

**Failed attempt:** fixed 0.08 Hz proximity threshold to exclude peaks near respiratory
harmonics. Over-triggered when true cardiac peak was near a harmonic. MAE 9.55 bpm.
Root cause: a fixed frequency tolerance is the wrong abstraction for this problem.

---

## 5. Literature — exp002 survey (2024-2025, arXiv MCP, 2026-06-11)

### 5.1 Adaptive ECA + AHET (primary reference for exp002)
Tang et al., "Adaptive Extensive Cancellation Algorithm and Harmonic Enhanced Heart Rate
Estimation based on MMWave Radar," arXiv:2503.07062, Zhejiang/ASU/Wuhan, 2025.

**Strategy:** cancellation-before-estimation.

**Step 1 — Adaptive ECA (Extensive Cancellation Algorithm):**
- Use ANLS (Adaptive Non-Linear Least Squares) to estimate f_r and reconstruct the
  respiration signal plus its first K_b harmonics (typically K_b = 3; for our case with
  f_r = 0.25 Hz, K_b = 4 to cover the 4th harmonic at 1.0 Hz = 60 bpm).
- Build subspace matrix X from the harmonic Vandermonde structure.
- Project phase signal θ onto the complement: θ_ECA = P·θ where P = I - X(X^T X)^{-1} X^T.
- Result: respiration and its harmonics are removed from the phase signal in the time domain.

**Step 2 — Adaptive Harmonic Enhanced Trace (AHET):**
- Search for the largest FFT peak f_h1 in cardiac band [0.7, 2.0] Hz.
- Validate: look for the 2nd HR harmonic f_h* in [2·f_h1 - V_e, 4.0] Hz (V_e = 0.1 Hz).
- Credibility: |2·f_h1 - f_h*| ≤ V_e → accepted. Else: try 2nd-largest peak f_h2, re-check.
- Fallback: if both fail, use history (mean of first 5 stable estimates ± V_a = 0.1 Hz).

**Results (77 GHz, 100 Hz frame, 20 s CPI):**
- Raw: RMSE 14.41 bpm → after ECA: 6.37 bpm → after AHET: 1.20 bpm.
- ANLS reconstruction: 5 s sliding window, 1 s step, for segmental f_r estimation.

**Key note for us:** Paper explicitly states the 4th harmonic of f_r (62.4 bpm with
f_r = 15.6 bpm) and intermodulation term f_1 = f_h - f_r (61 bpm with f_h = 76.6 bpm)
merge within the 3 bpm frequency resolution and produce a competing peak. This is exactly
our failure mode.

### 5.2 Harmonic MUSIC (HMUSIC)
Hsieh et al., "Harmonic MUSIC Method for mmWave Radar-based Vital Sign Estimation,"
arXiv:2408.01951, National Taipei University of Technology, 2024.

**Strategy:** super-resolution subspace method that jointly estimates f_r and f_h by
modelling both as harmonic sources. The HMUSIC pseudo-spectrum minimises projection onto
the noise subspace of the slow-time covariance matrix.
- Models phase as L harmonics of respiration + L harmonics of heartbeat simultaneously.
- Avoids explicit harmonic cancellation; resolves both sources jointly.
- 88th percentile HR error < 5 bpm (4 subjects, 12.8 s window, 60 GHz, cluttered room).
- Requires knowing L and number of sources; heavier computation.

### 5.3 Second-derivative + VME (working at higher harmonics)
Iwata et al., "Accurate Radar-Based Heartbeat Measurement Using Higher Harmonic
Components," arXiv:2407.07380, Kyoto/Nagoya, 2024.

**Strategy:** shift the problem up in frequency where respiratory interference is weaker.
- Compute |d²s/dt²|: absolute value of 2nd derivative of the complex radar signal s(t).
  This acts as a spectral high-pass that emphasises cardiac harmonics (2nd, 3rd) while
  suppressing respiratory components.
- The 2nd cardiac harmonic (2·f_h ≈ 2-3.4 Hz) lies above the worst respiratory harmonics.
- Apply VME (Variational Mode Extraction) to extract the 2nd harmonic mode; divide by 2.
- Advantage: bypasses phase unwrapping. Disadvantage: needs long windows (60 s).
- RMSE of IBI decreased 23%, CC improved 0.20 vs conventional phase methods.

### 5.4 Nonlinear Harmonic Spectrum (NLHS)
Shimomura et al., "A Nonlinear Spectral Approach for Radar-Based Heartbeat Estimation
via Autocorrelation of Higher Harmonics," arXiv:2507.20664, Kyoto, 2025.

**Strategy:** compute localised spectral autocorrelations summed across harmonic orders.
- Gaussian-smooth d(t), then compute 2nd derivative d''(t).
- Define NLHS(f) = Σ_n c(n·f, f) where c(f0, Δf) is a localised autocorrelation of the
  Fourier transform around frequency f0 with lag Δf.
- When Δf = f_h, the autocorrelations at n·f_h all peak simultaneously → sharp NLHS peak.
  Respiratory harmonics do not produce the same structure.
- Reduces RMSE by 20%, CC by 0.20 vs best conventional baseline (60 s windows, 60 GHz).
- Naturally exploits higher harmonics (N = 6-15) without needing to isolate them.

### 5.5 NRBO-VMD (mode decomposition)
Gu et al., "Improved VMD Based Remote Heartbeat Estimation Utilizing 60GHz mmWave Radar,"
arXiv:2502.11042, UESTC, 2025.

**Strategy:** auto-tune VMD parameters (K, α) using Newton-Raphson-based optimizer
(NRBO) minimising sample entropy, then select IMFs in 0.5-2 Hz and reconstruct CMS.
- RMSE 5.208 bpm, 94% accuracy (18 subjects, 60 s windows).
- Measured from back at 20 cm — different geometry to our chest-facing 1.3 m setup.
- Less directly applicable; included for completeness.

### 5.6 Pi-ViMo (template matching)
Zhang et al., "Pi-ViMo: Physiology-inspired Robust Vital Sign Monitoring using mmWave
Radars," arXiv:2303.13816, McMaster/Huawei, 2023.

**Strategy:** time-domain template matching with physiological models (RC-circuit
respiration + Van der Pol heartbeat oscillator). Jointly fits templates to chest-wall
displacement, bypassing frequency-domain harmonic confusion entirely.
- HR error 11.9% stationary, 13.6% with micro-RBMs.
- Computation: 4.3 s per 15 s window on i7 → not real-time. Heavy optimisation.
- 15 s windows (shorter than our 20 s).
- Interesting as a theoretical ceiling; not our chosen approach for exp002.

---

## 6. Decision — exp002 approach

**Chosen: ECA + harmonic consistency check (§5.1 simplified)**

Rationale:
- Directly addresses our documented failure mode (4th harmonic / intermodulation).
- Works on 20 s windows with 20 Hz frame rate (no long-window requirement).
- Implementable in scipy/numpy without new dependencies.
- ANLS-based f_r estimation is robust (respiration is the dominant spectral component).
- ECA is a linear projection — invertible, no information destroyed, diagnosable.
- The AHET consistency check (2×f_h verification) is the principled version of the
  "harmonic proximity" idea that failed with a fixed threshold.

**Approach not chosen and why:**
- HMUSIC: requires MIMO/multi-antenna correlation matrix; single-bin phase signals have
  lower rank than assumed. More complex to implement correctly.
- |d²s/dt²| + VME: designed for 60 s windows and IBI/HRV estimation. Our 20 s sliding
  window would lose too much resolution. Good future direction for HRV work.
- NLHS: also uses 60 s windows; designed for IBI not HR rate. Same concern.
- Pi-ViMo: computationally impractical for real-time; 4.3 s per window.

---

## 7. exp002 algorithm plan (to be cross-reviewed before implementation)

### 7.1 Phase 1 — ECA (respiration subspace cancellation)

Input: unwrapped phase vector θ[n] of length N (one 20 s window, N = 400 at 20 Hz).

1. **Estimate f_r:** run FFT on θ[n], find argmax in [0.1, 0.5 Hz] (respiration band).
   Use a shorter 5 s sub-window for ANLS-style estimation as per §5.1 (5 s @ 20 Hz =
   100 samples — enough for 3 cycles of 15 bpm respiration).

2. **Build harmonic Vandermonde subspace X** (N × K_b):
     X[:,k] = sin(2π·(k+1)·f_r·t_n)  and  cos(2π·(k+1)·f_r·t_n)
   for k = 0..K_b-1. Use K_b = 4 (covers up to 4th harmonic = 60 bpm for our f_r).
   Include both sin and cos columns so the phase offset of each harmonic is free.

3. **Project:** θ_ECA = θ - X·(X^T X)^{-1}·X^T·θ
   This orthogonally projects θ away from the respiration subspace.

4. **Verify:** FFT of θ_ECA should show the respiration peak and its harmonics gone.
   Log the cancelled power at each harmonic as a diagnostic.

### 7.2 Phase 2 — Cardiac peak search + AHET consistency

Input: θ_ECA.

5. **Spectral estimate:** windowed FFT of θ_ECA (same Hann window as baseline).

6. **Candidate peak:** argmax in cardiac band [0.8, 2.0 Hz] → f_h1.

7. **2nd harmonic consistency check (AHET):**
   - Search for peak f_h_star in [2·f_h1 - V_e, 4.0] Hz, V_e = 0.1 Hz.
   - If |2·f_h1 - f_h_star| ≤ V_e → credible, return f_h1.
   - Else: try 2nd-largest peak f_h2 in cardiac band, re-run step 7.
   - If both fail: flag as low-confidence. Return NaN (exclude from MAE/RMSE).
     Do NOT substitute 60 bpm as a default — that was the failure mode.

8. **Output:** HR = f_h (Hz) × 60 (bpm), plus a confidence flag per window.

### 7.3 Diagnostic outputs (mandatory per CLAUDE.md §5 rule 4)

Per window, dump:
- f_r estimated
- Power cancelled at each harmonic (k=1..4)
- θ_ECA spectrum (the full array, not just peak)
- f_h1 (raw candidate), f_h_star (2nd harmonic location), credibility flag
- Final HR estimate

This lets us diagnose any window where radar ≠ Masimo without just observing the error.

### 7.4 Config additions (experiments/exp002_harmonic_rejection/config.yaml)

New parameters vs exp001:
  eca_harmonics: 4          # K_b — harmonics of f_r to cancel
  eca_deviation_hz: 0.1     # V_e — credibility tolerance for 2nd harmonic check
  eca_history_hz: 0.1       # V_a — stability threshold for HR track history
  respiration_band_hz: [0.1, 0.5]  # for f_r estimation

### 7.5 Evaluation

Metric unchanged from exp001: MAE, RMSE, Bland-Altman vs Masimo PR.
Report additionally: % of windows flagged as low-confidence (NaN).
Compare: ECA+AHET vs exp001 baseline on the same capture file.

### 7.6 Cross-review status

APPROVED — OpenAI cross-review completed. Four implementation improvements mandated:
1. Use QR projection, not explicit matrix inverse
2. Refine f_r beyond raw FFT bin using parabolic interpolation
3. AHET second-harmonic search must be LOCAL [2*f_h ± 0.1 Hz], not global to 4.0 Hz
4. K_b adaptive: include harmonic k only if k*f_r < 2.0 Hz AND not within 0.15 Hz
   of the cardiac candidate (guards against suppressing true HR near a harmonic)
   Hard floor: always include k=1..4 for this capture (4th harmonic = known failure)
Full review recorded in SESSION.md.

#### AHET second-harmonic criterion — known limitations (as of 2026-06-14)

**Pass rule:** `second_peak_magnitude > comparison_floor`, where `comparison_floor` is
the median cardiac-band magnitude on the candidate-specific second-pass ECA spectrum.
The ratio is `second_peak_magnitude / comparison_floor`; a ratio > 1.0 is a pass.

**Ratio distribution observed in exp002 (`20260614_161352`):**
- min 1.035, median 1.233, max 2.385 (0.30, 1.82, 7.55 dB).
- 16/20 AHET-verified windows fall in the [1.0, 1.5] range — the threshold is very soft.
- A hypothetical threshold of 1.5 would retain only 4/20 estimates; any threshold
  tuning requires independent capture data and cannot be derived from exp002 alone.

**HR accuracy is not guaranteed by a passing ratio:**
- 5 of the 20 verified windows have absolute HR error ≥ 10 bpm, with ratios spanning
  1.060–1.233. The ratio confirms second-harmonic structure is present but does not
  confirm the fundamental is the cardiac peak rather than a respiratory harmonic.

**Window-length-dependent search-region bin count:**
- The ±0.1 Hz search region around 2×f_h contains approximately 5, 5, and 7 FFT bins
  at window lengths of 20, 25, and 30 s respectively (at a representative 1.2 Hz
  candidate with production rFFT sizes 400/500/600, not zero-padded).
- Under an idealised null (equal independent noise), the probability that the maximum
  of m bins exceeds the cardiac-band median is 1 − 0.5^m ≈ 0.969 / 0.969 / 0.992.
- This means longer windows mechanically inflate AHET pass rates independent of true
  second-harmonic evidence. exp004 pass-rate comparisons across window lengths must
  carry the explicit caveat: "AHET pass rates are indicative only; criterion not
  independently validated; window-length-dependent search-region bin counts
  (~5/5/7 at 20/25/30 s) may inflate pass rates at longer windows."

**No rejection population in exp002:**
- exp002 produced 20 AHET passes, 0 AHET-rejection failures, and 1 respiratory
  fallback (no AHET attempted). There is no failed-window population from which to
  estimate rejection performance; the criterion cannot be validated or tuned on
  this capture alone.

**Planned evaluation (deferred until second capture):**
- Monte Carlo framework with four synthetic conditions per window length (20/25/30 s):
  genuine_harmonic (cardiac fundamental + second harmonic present),
  missing_harmonic (fundamental only, no second harmonic),
  noise_only (no cardiac signal),
  resp_competitor (respiratory harmonic inside cardiac band, separated from true HR).
- Development/held-out seed split: criterion and threshold selected on development
  seeds, evaluated once on held-out seeds.
- Adequacy rule: Wilson 95% CI upper bound on false-pass rate < 0.10.
- Predeclared alternative criteria to compare against the current floor ratio:
  `local_prominence` (second-harmonic peak prominence relative to local background)
  and `peak_local_median` (second-harmonic peak divided by local-neighbourhood median,
  with defined guard bins and edge handling).
- ROC curves for criteria with continuous scores; operating-point plots for binary criteria.
- Production rFFT sizes (400/500/600, not zero-padded) must be used throughout to
  match the real search-bin counts.

**Required condition for any criterion change:**
Any new threshold or alternative criterion selected on synthetic/development data must
be confirmed on an independent real capture before it is used to interpret exp004
pass-rate differences.

---

## 8. Evaluation plan
- Metric: HR error vs Masimo PR — MAE (bpm), RMSE (bpm), Bland-Altman limits.
- Conditions: sit vs stand; 1.3 vs 1.6 m; multiple subjects.
- Quality gate: exclude/flag Masimo segments with low Perfusion Index.

---

## 9. References gathered

- [x] Tang et al. 2025, ECA+AHET: arXiv:2503.07062
- [x] Hsieh et al. 2024, HMUSIC: arXiv:2408.01951
- [x] Iwata et al. 2024, |d²s/dt²|+VME: arXiv:2407.07380
- [x] Shimomura et al. 2025, NLHS: arXiv:2507.20664
- [x] Gu et al. 2025, NRBO-VMD: arXiv:2502.11042
- [x] Zhang et al. 2023, Pi-ViMo: arXiv:2303.13816
- [ ] TI application note: vital signs with mmWave sensors `[CITATION NEEDED]`
- [ ] TI raw ADC data capture / DCA1000 data-format app note `[CITATION NEEDED]`
- [ ] Beltrão et al. 2023, ANLS framework (cited in [2503.07062]): IEEE TMT&T vol.71 no.4

---

## 10. Chosen plan for exp002

Implement ECA + AHET as described in §7 above.
Cross-review the algorithm plan with OpenAI (per CLAUDE.md §6) before writing code.
Evaluate on the same `data/raw/` capture as exp001 to isolate algorithm improvement.

---

## exp002 Final Assessment

Best result: MAE 5.29 bpm, RMSE 7.03 bpm, bias -2.27 bpm (20/21 windows, 1 NaN)
  Config: 30 s trim, bin 29 pinned, ECA+AHET, no EMA
  Run: results/exp002_harmonic_rejection/20260611_170029/

Conservative result: MAE 6.98 bpm, RMSE 8.80 bpm, bias -5.15 bpm (19/21 windows, 2 NaN)
  Config: 30 s trim, bin 29 pinned, ECA+AHET + EMA outlier gate
  Notes: EMA adds lag; window 10 gated correctly but underlying signal is a genuine
  respiratory-dominance event (~60 bpm harmonic with second-harmonic AHET support).
  This event is a physics limitation of single-range-bin phase extraction, not an
  algorithm failure.

Known failure mode documented:
  Windows 9-12 (09:01:10-09:01:35): radar returns ~60 bpm, Masimo shows ~71 bpm.
  Cause: respiratory motion temporarily dominates phase signal; 4th harmonic at
  ~60 bpm passes AHET verification because genuine second harmonic (~120 bpm) is
  present. Cannot be resolved by spectral methods without multi-bin or Doppler
  separation. Candidate mitigation: multi-range-bin coherent combination (exp004).

Stopping criteria met: further tuning on this capture is fitting to a single
20-second event. Second capture required to assess generalisation.
