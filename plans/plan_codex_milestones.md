## 1. Executive verdict

**MAJOR SIGNAL-REPRESENTATION CHANGE LIKELY NEEDED.**

The current `eca_ahet_v1` implementation is not scientifically validated as a heart-rate estimator. Its principal respiratory-harmonic cancellation mode is knowingly ineffective in the cardiac band, and AHET can falsely “verify” a respiratory harmonic as cardiac.

The reported low conditional MAE is arithmetically correct for nine scored windows, but the published 30.08% coverage is not: honest all-window coverage is **14/128 = 10.94%**, or **11/120 = 9.17%** after excluding the warm-up window. The available data also cannot demonstrate heart-rate tracking because reference HR varies too little.

This was a read-only audit. I made no changes; the worktree was clean at `409ec055dab82db2ffec9c926b9f66674212f414`.

## 2. Exact method scope

The audited method is the user’s own pipeline:

- Core estimator: `src/window_pipeline.py::run_window_dsp`, estimator ID `eca_ahet_v1`.
- Production wrapper: [production_suite.py](<C:/Users/josemsosag/Desktop/vitals_radar_3/src/m4/production_suite.py>), ID `production_eca_ahet_v1`.
- Live path: [live_demo.py](<C:/Users/josemsosag/Desktop/vitals_radar_3/scripts/live_demo.py>) with [live_demo_config.yaml](<C:/Users/josemsosag/Desktop/vitals_radar_3/scripts/live_demo_config.yaml:95>).
- Offline path: [score_offline.py](<C:/Users/josemsosag/Desktop/vitals_radar_3/scripts/score_offline.py>) using fixed non-overlapping 30-second windows.

Excluded from conclusions about the active method:

- Ahmed/M8 implementations under `src/m8` and `scripts/m8_*`.
- Kotte/M9 implementations under `src/m9` and `scripts/m9_*`.
- Historical Step 6 temporal tracking.
- Superseded `src/vitals.py::run_pipeline*` and mean-comparator experiments.

Where the latest M8 artifact contains several methods, I considered only rows labeled `production_eca_ahet`.

## 3. Pipeline reconstruction

The active signal path is:

1. **DCA1000 decoding.** Little-endian `int16`, with the configured two-lane four-word IQ layout and `iq_swap=true`, producing complex data shaped approximately `(frames, 32 chirps, 4 RX, 256 ADC samples)`.

2. **Range processing.** Hann window across fast time, followed by the ADC/range FFT.

3. **Session-level range-bin lock.** Bins corresponding to the configured 0.8–1.4 m gate are tested on the first 600 frames. A radar-only score combines estimator validity, respiration confidence and settled energy. One bin is then locked for the session.

4. **Scalar phase construction.** At the selected bin, each channel contributes
   \(z_n z^*_{n-1}\). These complex differences are averaged across all chirps and receivers; their angle is cumulatively integrated into one phase series.

5. **Impulse suppression.** Phase increments are clipped to ±1.5 rad and reintegrated.

6. **Respiration estimate.** Detrending, Hann FFT, harmonic analysis and STFT stability generate respiration frequency and confidence. Invalid respiration causes HR abstention.

7. **Broad heart-band filtering.** A zero-phase Butterworth response is applied in the FFT domain over roughly 0.8–4 Hz so fundamental and second harmonic are available.

8. **First ECA pass.** Active mode `skip_forbidden_harmonics_v1` avoids projecting respiratory harmonics lying inside the cardiac band. The remaining projected low-order terms are largely below the preceding heart filter. Consequently, the in-band spectrum is effectively unchanged. This limitation is documented directly in [vitals.py](<C:/Users/josemsosag/Desktop/vitals_radar_3/src/vitals.py:463>).

9. **Candidate and AHET gating.** Up to three magnitude-ranked peaks are considered, subject to an effective 0.95 Hz/57 bpm lower floor. Each candidate undergoes another ECA attempt and a second-harmonic check. The first candidate passing all ratio, prominence and floor rules wins. The estimate averages the fundamental with half the detected second-harmonic frequency.

10. **Validity and evidence.** `hr_valid` is equivalent to AHET verification. Offline scoring compares it with median Masimo `Beats / min` over the half-open epoch window, provided at least 24 usable samples, PI ≥0.5 and PR p90–p10 ≤5 bpm.

## 4. Scientific assumption audit

| Assumption | Assessment |
|---|---|
| One range bin contains a stable dominant chest scatterer | Plausible in constrained seated recordings, but not established from the stored evidence |
| A scalar average across 32 chirps × 4 RX preserves cardiac displacement | Under-validated; multipath, receiver cancellation and spatially varying phase can destroy the small cardiac component |
| Static RX phase offsets corrupt the delta phase | Not a concern: conjugate differencing cancels static phase offsets |
| Phase clipping adequately handles movement | Unsupported; it limits increments but is not a validated motion detector or quality gate |
| Respiratory interference is well modeled by stationary sinusoids at exact integer harmonics | Too restrictive for frequency drift, amplitude modulation, chest-shape changes and nonstationary motion |
| A strong second harmonic establishes cardiac origin | False in general; a respiratory harmonic can supply both the candidate and its apparent second harmonic |
| A single 30-second stationary estimate demonstrates HR tracking | False; it demonstrates at most window-level frequency agreement |
| Abstention makes the accepted estimates trustworthy | Only if the verification rule is identifiable and risk–coverage is measured without selection bias; neither condition currently holds |

The most consequential assumption is the early collapse from a complex multichannel observation to one scalar phase series.

## 5. Signal-model adequacy

The scalar phase representation is physically defensible only when one coherent scatterer dominates all included channels. In the present implementation, complex differences are averaged before their angle is taken. That creates an amplitude-weighted circular average: high-amplitude channels dominate, while incompatible channel phases can cancel.

Information discarded before HR estimation includes:

- Receiver-specific coherence and phase.
- Chirp-to-chirp consistency.
- Adjacent-bin spatial structure.
- Range stability and migration.
- Evidence that a component is common across channels versus localized multipath.

AHET also has a structural identifiability failure. If a candidate lies at \(f_c=kf_r\), its test near \(2f_c\) searches near \(2kf_r\). A respiratory harmonic at \(2kf_r\) therefore satisfies the same test as a cardiac second harmonic. The repository’s synthetic tests explicitly preserve this ambiguity rather than resolving it.

This does not prove that a multichannel method will work. It establishes that the current scalar method cannot reliably distinguish cardiac motion from a sufficiently rich respiratory harmonic comb.

## 6. Respiratory-harmonic rejection audit

The active ECA mode is the central blocker.

- Harmonics inside the cardiac band are deliberately skipped to avoid erasing coincident heart energy.
- The harmonics that remain eligible for projection are generally below the preceding cardiac filter.
- Thus the first-pass and pre-ECA spectra are essentially identical.

Recorded regression evidence covers **1,001 in-band respiratory-harmonic cases**:

- Median removal: **0.000 dB**.
- Worst observed removal: **0.018 dB**.
- Cases exceeding 1 dB removal: **0/1,001**.

That is not merely weak cancellation; it is a cancellation mode that is functionally inert for its stated in-band purpose.

The rejected alternatives reveal the underlying contradiction:

- Projecting higher harmonics can erase a real heart component when HR coincides with \(kf_r\).
- Skipping those harmonics leaves the respiratory comb untouched.
- Increasing `kmax` does not solve source identifiability.
- Errors in estimated respiration frequency grow as \(k\Delta f_r\).

For a 30-second stationary sinusoid, the normalized correlation after frequency mismatch is approximately \(|\mathrm{sinc}(\pi\Delta fT)|\). A 0.01 Hz mismatch leaves about 26% residual energy; 0.02 Hz leaves about 75%. Drift and modulation are worse than this stationary calculation.

Respiration-invalid windows correctly abstain. That is a sound failure behavior, but it contributes heavily to the low coverage.

## 7. Numerical and implementation audit

Numerically strong parts include:

- DCA layout and frame validation.
- Zero-phase filter construction.
- Modified Gram–Schmidt behavior in the ECA primitives.
- Half-open window boundaries.
- NaN/validity propagation.
- Deterministic fixed-window operation.

Confirmed defects or fragilities:

- `refine_freq_hz` lacks the interpolation-shift bound used by the respiration refinement helper. **25/247** finite candidate refinements left their nominal search interval. None changed a currently accepted output, so this is latent rather than causal.
- The configured heart band begins at 0.8 Hz/48 bpm, but candidate construction imposes an undocumented 0.95 Hz/57 bpm floor.
- The AHET numerator is measured around the second harmonic, while its comparison floor is the median amplitude in the lower 0.8–2 Hz band. This cross-band absolute heuristic lacks physical calibration.
- `candidate_min_prominence=3` is an absolute FFT-magnitude threshold, making it dependent on sample count and signal scaling.
- A 30-second window gives a raw FFT grid of 0.0333 Hz, or 2 bpm. Parabolic refinement is reasonable for an isolated stationary tone but cannot separate overlapping or drifting sources.
- String-valued evidence arrays use object dtype, so normal `np.load(..., allow_pickle=False)` cannot load the complete archive.
- Live elapsed-time, HR, BR and intermediate collections grow without a configured bound; checkpoints rewrite the accumulated intermediate list.

Sampling at 20 Hz is not the limiting factor.

## 8. Evaluation-methodology audit

The legacy scorer contains a confirmed coverage bug in [m8_ahmed_score.py](<C:/Users/josemsosag/Desktop/vitals_radar_3/scripts/m8_ahmed_score.py:220>): it removes captures with undefined MAE before averaging capture coverage. Captures with zero HR outputs disappear from the denominator.

Honest current-method results are:

| Quantity | Result |
|---|---:|
| Emitted / all complete windows | 14/128 = **10.94%** |
| Macro mean of per-capture all-window coverage | **19.43%** |
| Emitted and reference-admitted / reference-admitted | 9/67 = **13.43%** |
| All-window coverage excluding `k=0` | 11/120 = **9.17%** |
| Macro coverage excluding `k=0` | **16.97%** |
| Conditional MAE on nine scored windows | **2.77 bpm** |
| Conditional RMSE | **5.28 bpm** |
| Conditional bias | **−2.39 bpm** |
| Within ±5 bpm | **7/9** |

The reported 30.08% headline should be retired.

The newer [estimator_scoring.py](<C:/Users/josemsosag/Desktop/vitals_radar_3/src/m4/estimator_scoring.py:274>) already retains source rows, reconciles radar/reference/joint denominators, includes zero-output captures and separates `k=0`. It should be the sole canonical evaluator rather than creating a second repaired-but-divergent scoring path.

The nine-window MAE is numerically correct but selection-conditioned: two false accepts account for the severe tail, while most windows are abstained.

## 9. Ground-truth leakage audit

I found **no direct Masimo leakage into the active estimator**:

- Range-bin warm-up uses radar-only energy, respiration confidence and radar HR validity.
- The production estimator does not open the Masimo data.
- Radar-only bin sweeps do not use PR.
- Runtime candidate selection and AHET are reference-independent.

Important distinctions:

- Scoring `k=0` is in-sample radar-side selection because the same warm-up interval influences the locked bin. It is look-ahead, but not ground-truth leakage.
- Reference-driven best-bin ceilings and constant-session-median comparisons are diagnostic oracles. They are acceptable only when clearly labeled nondeployable.
- Repeated analysis of all eight captures and absence of an untouched cohort create evaluation-overfitting risk, even without runtime leakage.
- Representation or threshold selection using final-holdout separability would itself consume the holdout. Development, validation and final evaluation must be separated by subject.

## 10. Intermediate-signal and provenance audit

The older clean artifacts under:

[results/score_offline/20260730T204448Z](<C:/Users/josemsosag/Desktop/vitals_radar_3/results/score_offline/20260730T204448Z>)

contain strong window-level evidence: raw/clean phase, respiration spectra, harmonic analysis, STFT evidence, baseline/pre/first/final HR spectra, candidates, rejection codes, ratios, per-attempt spectra and ECA bases. These are sufficient to reconstruct the representative failures below.

Missing or weakly represented evidence includes:

- Selected complex IQ by receiver/chirp.
- Local range profiles and adjacent-bin behavior.
- Motion-clip mask and clip count.
- Explicit zero-amplitude/channel-validity masks.
- A pickle-free typed schema for string fields.

The latest headline scorer is clean, hashes its input CSV and configuration, and records seed 42. However, its source bin-sweep artifact was produced from a **dirty upstream tree**, and the uncommitted diff was not preserved. The bin sweep stores scalar rows rather than complete per-window signal evidence. Therefore the latest headline bundle is not paper-grade reproducible despite its downstream clean scorer.

Future production evidence should follow the manifest contract in [manifest.py](<C:/Users/josemsosag/Desktop/vitals_radar_3/src/m4/manifest.py:352>) and store a bounded, typed intermediate schema for every estimate and abstention.

## 11. Result robustness

Current per-capture results are:

| Capture | Subject/condition | Lock bin | Windows | Emitted | Ref-admitted | Scored | Conditional MAE |
|---|---|---:|---:|---:|---:|---:|---:|
| massimo1 | A, natural | 27 | 6 | 2 | 5 | 1 | 0.42 |
| massimo2 | A, paced 16 | 26 | 6 | 5 | 5 | 4 | 0.47 |
| sweep | B, paced steps | 26 | 16 | 3 | 8 | 1 | 0.45 |
| massimo3 | B, natural | 26 | 20 | 0 | 11 | 0 | — |
| massimo4 | C, natural | 25 | 20 | 1 | 13 | 1 | 12.55 |
| massimo5 | C, natural | 25 | 20 | 0 | 9 | 0 | — |
| massimo6 | D, natural | 24 | 20 | 2 | 12 | 2 | 4.79 |
| massimo7 | D, natural | 32 | 20 | 1 | 4 | 0 | — |

Limitations:

- Only four subjects and eight sessions.
- All existing capture origins are approximate; likely 5–15 seconds of uncertainty. A 12-second error is 40% of a scoring window.
- Posture and tape-measured distance are not stored in run metadata.
- PR p10–p90 spans only about 2.6–5.2 bpm within sessions.
- A constant session-median PR diagnostic achieves approximately **1.06 bpm MAE and 100% within ±5 bpm** on admitted windows without using radar.
- A decoy-controlled signal-presence analysis found significant HR evidence in only **2/8** sessions.
- Current data demonstrate respiration, but not HR dynamics or tracking.
- Re-running the current DSP changes the first three warm-up locks from recorded bins 23/20/21 to 27/26/26, because warm-up scoring depends on the full evolving estimator.

Alternate bins sometimes emit more often, but many are low-energy range skirts, and AHET acceptance does not imply correctness. Bin choice alone is not the dominant fix.

## 12. Representative-window traces

| Window | Outcome | First informative divergence |
|---|---|---|
| massimo6 `k=2` | HR 86.01 vs PR 86; correct | Both ~86 and ~74 bpm candidates pass AHET; magnitude ordering happens to select the correct one |
| massimo6 `k=1` | HR 74.44 vs PR 84; error −9.56 | BR 18.79 bpm gives \(4f_r≈75.16\) bpm. The respiratory line dominates; the near-84 candidate is rejected by AHET |
| massimo4 `k=5` | HR 73.45 vs PR 86; error −12.55 | BR 18.36 bpm gives \(4f_r≈73.44\) bpm. The true 86 bpm component is not even retained as a candidate |
| massimo3 `k=17` | Correct abstention | Respiration is invalid; HR gating is not run |
| massimo6 `k=15` | Correct abstention | \(4f_r≈83.60\) is near PR 84, but all AHET ratios fail; the system does not fabricate a value |

In both false accepts, the pre-ECA and first-ECA spectra are effectively identical. The first decisive failure is already present in the scalar spectrum: a respiratory multiple dominates or excludes the actual cardiac candidate. AHET then certifies the wrong source.

## 13. Test-suite audit

The focused deterministic suite completed with:

- **497 passed**
- **4 skipped**
- **13.30 seconds**

It covered decoder behavior, phase/respiration processing, ECA/AHET, warm-up, production adapter, window grid, comparator, evidence and scoring. It ran under NumPy 2.1.3, SciPy 1.15.3 and pandas 2.2.3 rather than the nominal pinned NumPy 1.26 environment. The complete repository suite was not rerun.

Strong coverage:

- ADC layouts and split-file decoding.
- Boundary and comparator behavior.
- Filter/ECA numerical primitives.
- Evidence shapes and deterministic validity.
- Synthetic tests acknowledging inert ECA and AHET ambiguity.

Important missing regressions:

- An end-to-end respiration-only high-harmonic comb using the active ECA mode.
- The real massimo4 `k=5` and massimo6 `k=1` false accepts.
- Unequal RX phase/amplitude, multipath and adjacent-bin simulations from raw cubes.
- Motion, clipping and clip-mask validation.
- Empty, short, NaN and Inf input through the full chain.
- A fixture catching survivor-biased macro coverage.
- Pickle-free evidence loading.
- Frame-validity and packet-loss handling for old captures.

Passing tests therefore support implementation determinism, not scientific validity.

## 14. Literature audit

The literature supports phase-based FMCW vital-sign measurement under stable-scatterer conditions, not the current verification rule:

- Phase displacement and FMCW vital-sign fundamentals: [Alizadeh et al., 2019](https://doi.org/10.1109/ACCESS.2019.2912956).
- Explicit respiratory third/fourth-harmonic interference with heartbeat: [Lv et al., 2021](https://doi.org/10.3390/s21082732).
- Spatial coherence and adjacent-bin/channel information: [Choi et al., 2021](https://doi.org/10.3390/app11104514) and [Mehrjouseresht et al., 2024](https://doi.org/10.3390/s24082448).
- IQ/DC, motion and harmonic vulnerabilities: [Li and Lin, 2009](https://doi.org/10.1109/TMTT.2008.2007139).
- Single- and multi-tone frequency-estimation limits: [Rife and Boorstyn, 1974](https://doi.org/10.1109/TIT.1974.1055282) and [Rife and Boorstyn, 1976](https://doi.org/10.1002/j.1538-7305.1976.tb02941.x).
- Proper selective-prediction risk–coverage analysis: [Geifman and El-Yaniv, 2017](https://proceedings.neurips.cc/paper/2017/file/4a8423d5e91fda00bb7e46540e2b0cf1-Paper.pdf).
- Agreement analysis: [Bland and Altman, 1986](https://pubmed.ncbi.nlm.nih.gov/2868172/).
- The cited Tang ECA/AHET preprint: [arXiv:2503.07062](https://arxiv.org/abs/2503.07062). It does not validate this project’s altered skip-forbidden-harmonics rule.

The literature motivates a controlled spatial/multichannel feasibility study. It does not guarantee that a more complex representation will recover HR with the present hardware and geometry.

## 15. Failed prior ideas that should not be repeated unchanged

The project history already rejects or undermines:

- Fixed ±0.08 Hz respiratory exclusion.
- Extending windows to 45 seconds.
- Respiration-frequency EMA with α=0.3.
- Larger or adaptive `kmax`.
- Legacy ECA that cancels coincident cardiac energy.
- Active skip mode that preserves that energy but cancels almost nothing.
- Candidate-guard ECA based on a potentially contaminated initial argmax.
- Extending the ECA ceiling to 4 Hz.
- Relaxing AHET ratio thresholds by 4–6 dB.
- Reranking candidates by raw AHET ratios.
- One-hop temporal continuity.
- Relocking, bin hopping or selecting deep low-energy range skirts.
- Static clutter removal without new evidence.
- Warm-up shifts and adjacent-bin corroboration in their previously tested form.

The brick-wall spectral-leakage defect was already fixed; it should not remain the default explanation for current failures.

## 16. Prioritized issue list

| Priority | Class | Finding | Consequence |
|---|---|---|---|
| BLOCKER | Scientific/design | Active ECA is inert in the cardiac band | The claimed respiratory-harmonic rejection is not occurring |
| BLOCKER | Scientific/design | AHET is source-nonidentifiable | Respiratory harmonics can be falsely certified as cardiac |
| HIGH | Evaluation code | Legacy coverage drops zero-output captures | 30.08% headline is invalid |
| HIGH | Data/evaluation | Narrow HR dynamics and no untouched cohort | No supported tracking or generalization claim |
| HIGH | Provenance | Latest source artifact came from a dirty tree and lacks full evidence | Latest headline is not paper-grade reproducible |
| HIGH | Timing | Old frame-zero origins are approximate | Radar/reference windows may be materially misaligned |
| HIGH | Research process | Recovery-arm analysis contract is still unresolved | New capture would precede a stable estimand |
| MEDIUM | Representation | Early scalar aggregation removes spatial diagnostics | Likely information bottleneck; requires a falsifiable study |
| MEDIUM | Interface | Hidden 57 bpm candidate floor contradicts configured 48 bpm band | Undocumented operating range |
| MEDIUM | Motion | Clipping has no stored mask or validated quality rule | Motion failures are opaque |
| MEDIUM | Evidence | Object-dtype strings require pickle | Portability and safe-loading defect |
| MEDIUM | Live reliability | Unbounded history/intermediate accumulation | Long-run memory and checkpoint-growth risk |
| LOW/MEDIUM | Numerical | Candidate frequency refinement can leave its search interval | Latent boundary defect |
| LOW | Documentation | Stale subject count and Step 6 identity claims | Confusing method provenance |

## 17. Correctness versus complexity

Several small code repairs are clearly warranted eventually: canonical coverage accounting, hidden-band consistency, bounded interpolation, typed evidence, motion masks and live-memory limits.

Those repairs will not solve the primary HR problem.

The false accepts originate in the observed scalar spectrum and in an unidentifiable verification rule. More thresholds, more ECA harmonics or a larger candidate search would add complexity without addressing source identity.

The proportionate next step is therefore not an elaborate new estimator. It is a small, predeclared representation-identifiability experiment. If receiver and adjacent-bin structure cannot separate cardiac evidence from respiratory multiples on independent subjects, the appropriate conclusion may be a hardware/geometry limit rather than another DSP layer.

## 18. Sequential research milestone plan

The independent plan review’s verdict was **NOT READY** until the following gates are incorporated.

**M0 — Close the analysis contract before capture**

- Reconcile the three-arm recovery estimand, recovery evidence floor, missing-window disposition, data roles and approval-reference metadata in [analysis_prespec.md](<C:/Users/josemsosag/Desktop/vitals_radar_3/notes/analysis_prespec.md:40>) and [protocol.md](<C:/Users/josemsosag/Desktop/vitals_radar_3/notes/protocol.md:429>).
- Cross-review the amendment.
- Decide subject counts through an explicit evidence-floor/sample-size analysis.
- Define hash-bound, subject-disjoint development, representation-validation and final-evaluation cohorts.
- If the available prospective subjects cannot support separate validation and final cohorts, stop and revise scope. Do not split correlated sessions from one subject across roles.

**M1 — Repair evaluation and provenance without changing the estimator**

- Route `production_eca_ahet_v1` through the existing M4 scoring contract.
- Retire the legacy 30.08% summary.
- Add fixed regressions for 14/128 all-window coverage, 9/67 joint-given-reference coverage, zero-output captures and separate `k=0`.
- Require clean-tree commit, raw/config/code hashes, exact denominator reconstruction and bounded full intermediates.
- Acceptance: a fresh run reproduces the current estimates and the corrected metrics without manual row handling.

**M2 — Engineering preflight, then independent acquisition**

- First validate a scoring-mode manifest before involving subjects.
- Require exact frame origin, start/end PC–phone offsets within ±1 second, packet counts, frame-validity map, raw/config hashes and capture commit.
- Record posture and tape-measured distance through the acquisition tool.
- Include packet-loss rules and time-offset sensitivity.
- Acquire meaningful HR dynamics, such as controlled recovery, rather than stationary sessions alone.
- Freeze subject-level cohort hashes before any validation/final labels are inspected.

**M3 — Falsifiable representation-identifiability gate**

Initially test deterministic representations only:

- Current scalar accumulated phase `(N,)`.
- Channel-normalized locked-bin phase increments by RX `(N−1, 4)`.
- The same representation for a fixed three-bin neighborhood `(N−1, 3, 4)`.

Before implementation, freeze dtype, units, normalization, zero-amplitude/NaN rules, bin offsets, chirp timing and memory ceiling.

Primary validation statistic:

- For each reference-admitted window, compute the power contrast between the PR-centered spectral neighborhood and the strongest noncoincident \(k f_r,\;k=3…6\) neighborhood.
- Compare candidates with the scalar baseline using within-subject paired medians.
- Require at least a **3 dB** improvement in the subject-level median contrast, with a subject-cluster bootstrap 95% lower bound above zero and multiplicity control across the fixed candidates.
- Include shifted-PR and respiration-only negative controls.
- Use validation labels only; final-evaluation labels remain closed.

If no candidate passes, stop estimator development and report the representation/hardware limitation.

**M4 — One minimal estimator, only if M3 passes**

- Preserve `eca_ahet_v1` unchanged as baseline.
- Implement one transparent estimator through the existing [window_pipeline.py](<C:/Users/josemsosag/Desktop/vitals_radar_3/src/window_pipeline.py:197>) callable contract.
- Give it a unique estimator/config hash and deterministic bounded evidence schema.
- Freeze the operating threshold using development data.
- Validate once using subject-disjoint validation data.
- Promotion requires no more than a two-percentage-point loss in radar or joint coverage, a subject-clustered upper confidence bound below zero for paired MAE difference at matched coverage, and improvement—not deterioration—in severe-error rate. Risk–coverage curves and the two known \(4f_r\) false accepts are mandatory regressions.
- Failure retains the old estimator; gates are not relaxed.

**M5 — Final untouched evaluation**

- Open final labels once, for scoring only.
- No repairs, threshold changes or representation choices may flow back into this evaluation.
- Report all-window, radar, reference and joint coverage; subject-weighted MAE/RMSE/bias; severe-error rate; subject-clustered Bland–Altman limits; arm-specific results; dynamic-range adequacy; uncertainty; and zero-output captures.
- Require complete artifact validation and independent cross-model review.

Dependency: **M0 → M1 → M2 → M3 → M4 → M5**. M3 and M4 are genuine stop gates, not automatic steps.

## 19. Final verdict

The implementation contains several repairable evaluator, provenance and interface defects, but those are not the main cause of poor HR performance.

The dominant problem is scientific: the pipeline collapses the radar cube too early, the active ECA does not remove the relevant respiratory harmonics, and AHET cannot establish whether a harmonic is cardiac or respiratory. Current evidence shows excellent-looking accepted estimates alongside severe, mechanistically predictable false accepts and approximately 9–11% honest all-window coverage.

Accordingly:

**MAJOR SIGNAL-REPRESENTATION CHANGE LIKELY NEEDED.**

Do not tune the current gates against these eight sessions or present the existing conditional MAE as validated tracking performance. First repair evaluation/provenance, acquire exact-time dynamic data under a frozen subject-level protocol, and run the representation-identifiability gate.