# M9 — Kotte Joint Doppler Heart-Rate Estimation

> **Canonical M9 plan.** Consolidated and reviewed 2026-08-08 from the former
> `m9_comments_plan`, `m9_step1a_snr_finding`, `m9_step1b_oracle_finding`, and the
> previous version of this file. Those three companion documents are superseded.
> The original Kotte PDF is the scientific authority. Existing M9 Python and config
> are partial work and are not automatically plan-conformant.

## 1. Objective

Establish, reproducibly, whether the two-frequency estimator published by Kotte et al.
can first be reproduced on its stated synthetic signal model and then evaluated on this
project's FMCW chest-radar captures as a clearly labelled adaptation.

M9 is a research implementation, not production software. Scientific correctness,
faithfulness to the paper, readable Python, complete evidence, and an honest negative
result take priority over Masimo agreement or estimator coverage.

## 2. Scope

### Included

- A direct-`Y_t` synthetic reproduction of the paper's Section III-C joint Doppler
  estimator, including Algorithm 1's selection objective and Eq. (26) as a diagnostic.
- A fixed 20-RX to 4-RX ablation that separates the paper geometry from this hardware.
- A synthetic chest-displacement transfer study that exposes the two-line model's limits.
- A simple adapter from the preserved raw FMCW cube to the matrix required by Kotte.
- Radar-only estimates on a fixed capture cohort, followed by separate reference scoring.
- Coverage, failure causes, agreement metrics, and enough evidence to explain each estimate.

### Excluded

- Changes to Ahmed/M8 or any Ahmed artifact, result, or implementation.
- Changes to the project's own estimator or range-selection algorithm.
- Production architecture, services, concurrency, caching, deployment, or elaborate bundles.
- A four-line or multi-line extension of Kotte; that would be a new method.
- Kotte Fig. 9 Monte Carlo and the Yule-AR comparator.
- A claim that Kotte et al. validated heart rate on humans; the paper uses simulations only.
- DOA or localization claims from this project unless separately authorized after M9.
- Choosing any estimator setting, bin, or arm from Masimo performance.

## 3. Primary source

Primary authority:

V. V. Kotte, S. Ahmed, M.-S. Alouini, and T. Y. Al-Naffouri, "Joint Estimation
of Single Target's High Amplitude Difference Doppler Frequencies in FMCW Radar,"
*IEEE Transactions on Radar Systems*, vol. 2, 2024, DOI 10.1109/TRS.2024.3352189.

Repository PDF:
`literature/ref_papers/joint_estimation_high_amplitude_doppler/Joint_Estimation_of_Single_Targets_High_Amplitude_Difference_Doppler_Frequencies_in_FMCW_Radar.pdf`.

The PDF wins over both Markdown conversions. Equations, conjugates, transposes, matrix
dimensions, Algorithm 1, and figure/table facts must be checked against the PDF or its
page renders. The long OCR-style Markdown conversion is materially corrupt around Eqs.
(23)-(26) and Algorithm 1 and must not be used as an implementation source.

## 4. Paper method

### 4.1 Signal and range contract

The paper uses one TX, `n_R` RX in a half-wavelength ULA, `N_s` fast-time samples per
chirp, and `N_c` chirps separated by `T_PRI`. Indices are:

- `n = 0 ... N_s-1`: fast-time sample;
- `i = 0 ... N_c-1`: slow-time chirp;
- `m = 1 ... n_R`: RX channel;
- `k`: range-FFT bin; `kappa`: selected target bin.

After the range FFT, the selected-bin matrix is

```text
Y(kappa) in C^(n_R x N_c)
         = a(theta_0) [beta_1 a(f_d1)^T + beta_2 a(f_d2)^T] + V(kappa),
```

with RX rows and chirp columns. Section III-A, Eqs. (12)-(14), finds `kappa` from
fast-time range-FFT peaks. The later Doppler mathematics requires a valid selected bin;
it does not algebraically depend on how that bin was selected.

### 4.2 DOA stage

The DOA covariance and Doppler covariance are different and must never be conflated:

```text
DOA:      R = Y Y^H / N_c       in C^(n_R x n_R)
Doppler:  R_t = Y_t Y_t^H / n_R in C^(N_c x N_c)
```

Eq. (18) uses the spatial steering vector and the Capon weight

```text
w_theta = R^-1 a(theta) / [a(theta)^H R^-1 a(theta)].
```

The paper sweeps `theta` and includes DOA in Algorithm 1. It also describes subtracting
each RX row's temporal mean to remove stationary targets, but this operation makes the
temporal all-ones vector a null vector and conflicts with the paper's subsequent unloaded
inverse. This inconsistency is retained as a limitation, not silently repaired.

### 4.3 Joint Doppler stage: Eqs. (23)-(26)

Transpose the selected-bin matrix:

```text
Y_t(kappa) = Y(kappa)^T in C^(N_c x n_R)
           = [a(f_d1) a(f_d2)] [beta_1 beta_2]^T a(theta_0)^T + V_t.
```

For every swept pair `(f_1, f_2)`, define

```text
A = [a(f_1) a(f_2)] in C^(N_c x 2)
H = A^H R_t^-1 A    in C^(2 x 2).
```

The paper solves

```text
min_w  w^H R_t w    subject to w^H A = [1 1]

w = R_t^-1 A H^-1 [1 1]^T.                         (25)
```

Eq. (26) estimates the joint coefficient, not two separate amplitudes:

```text
beta_hat = w^H Y_t a(theta_hat)* / [a(theta_hat)^H a(theta_hat)].
```

Cases 1-4 show `beta_hat ~= beta_1 + beta_2` when both trial frequencies are correct and
approximately zero when one or both are wrong. The paper explicitly states that
`beta_1 = -beta_2` cancels the joint coefficient and makes the method inapplicable.

### 4.4 Pair-selection objective

Algorithm 1, not the surrounding case discussion, defines the primary frequency surface:

```text
(f_1_hat, f_2_hat) = argmax_(f_1,f_2) w^H R_t w.
```

For the unloaded full-rank equations,

```text
w^H R_t w = 1^H H^-1 1.
```

The Eq. (26) `|beta_hat|^2` surface is persisted in direct synthetic controls as an
independent consistency diagnostic. It is not substituted for Algorithm 1.

The columns of `A` are interchangeable, so `(f_1, f_2)` and `(f_2, f_1)` are symmetric.
The paper uses prior knowledge that heart frequency is higher than breathing frequency to
label the outputs, but it does not resolve sign and does not test human vital signs.

### 4.5 Source ambiguities that implementation must not hide

- Eqs. (7) and the simulation use physical Hz and `T_PRI`; the printed temporal steering
  vector omits `T_PRI`. M9 uses `exp(j 2 pi f i T_PRI)` and labels this a physical-units
  interpretation of the source.
- Algorithm 1 writes a scalar `f_d` argmax although the weight contains two swept
  frequencies. The equations and Fig. 5 require a two-dimensional sweep.
- Eqs. (27)-(28) contain inconsistent angle subscripts.
- The paper does not specify a frequency-grid step, equality rule, conditioning rule,
  diagonal loading, or pseudoinverse.
- The fast-time parameters reported in Section IV do not fully specify a consistent
  reproduction of the complete range/DOA simulation.

## 5. Paper assumptions vs project conditions

| Paper assumption or parameter | Project condition | Classification | Consequence |
|---|---|---|---|
| One TX | TX0 only (`tx_channel_en=1`) | match | No TX multiplexing is introduced. |
| 20 RX, half-wavelength ULA in simulation | 4 RX, RX0-RX3 ordering preserved; M9 spatial calibration not established | adaptation | Direct paper control uses 20 RX. Four RX is an ablation. No DOA claim. |
| One uniformly spaced slow-time chirp every 50 ms | 32 chirps about 64 us apart in a roughly 2 ms burst, frames every 50 ms | adaptation | Canonical sample is fixed chirp-loop 0 from each frame. |
| `N_c=16` | 16 consecutive frames = 0.8 s | declared primary adaptation | Paper-comparison CPI retained; poor resolution/performance is acceptable. |
| `Y_t` is `N_c x n_R` | Raw per-chirp, per-RX complex data survives | compatible | The required matrix can be formed without collapsing RX. |
| Unloaded invertible `R_t` | With 4 RX and `N_c=16`, rank is at most 4 | structural mismatch | Project path requires declared loading; it is not literal Kotte. |
| Two additive complex Doppler lines | Periodic chest displacement produces conjugate sidebands and a Bessel comb | model mismatch | Synthetic transfer must expose this; no multi-line repair inside M9. |
| Full range + DOA + Doppler pipeline | Single seated target; established project range selection; no need for localization claim | controlled adaptation | M9 evaluates Section III-C at an external radar-only bin and omits DOA. |
| Simulation SNR = 0 dB | SNR reference point is ambiguous | unresolved source detail | Literal post-range 0 dB and a separate FFT-gain sensitivity are both retained. |
| Generic moving-target simulations | Human chest motion and Masimo reference | unsupported transfer | Real results are an evaluation, not inherited validation. |

## 6. Actual data contract

### 6.1 Stored acquisition

The repository decoder and HDF5 writer define:

| Item | Contract |
|---|---|
| Raw/HDF5 cube | `(N_frames, 32 chirps, 4 RX, 256 ADC samples)` |
| Stored dtype | `complex64` |
| TX | one, TX0 |
| RX order | RX0, RX1, RX2, RX3; no additional reorder after I/Q decoding |
| Frame rate | nominal 20 Hz; frame period 50 ms |
| Chirp timing | nominal 7 us idle + 57 us ramp = about 64 us per loop |
| ADC | 256 samples at 5.209 Msps |
| Range FFT | unnormalized Hann-windowed complex FFT over the final ADC axis |
| Range resolution | 0.0436 m/bin in the active capture config |

`steps/step_2/save_time_domain_cubes.py` would store `/cube` with the same shape and
dtype, but `data/processed/time_domain_cubes/` is currently absent. M9 therefore uses the
raw `adc_stream.bin` decoder contract; HDF5 is optional and must be numerically identical.

### 6.2 Canonical project `Y_t`

For a selected range bin and a 30 s window:

1. Compute the unnormalized Hann range FFT per frame, chirp, and RX.
2. Select **chirp-loop index 0** from every frame and retain all four RX values.
3. Cast the selected-bin matrix to `complex128` for DSP.
4. The result is `Z in C^(600 x 4)`, sampled nominally every 50 ms.
5. Retain frames `0:592`, subtract each RX column's mean over that retained support,
   then split into 37 CPIs of shape `(16, 4)`.
6. Each CPI is the project-adapted `Y_t`; `T_slow = 0.05 s` and steering is
   `exp(j 2 pi f i 0.05)`.

This fixed-loop mapping is the closest uniform analogue to the paper. Within-frame chirps
span only about 2 ms and cannot resolve 0.10-2.00 Hz over `N_c=16`.

The current `src/m9/kotte_core.py::extract_rx_slow_time` coherently averages all 32
chirps and is **not plan-conformant**. A 32-chirp coherent mean may remain only as a named
sensitivity after a radar-only test demonstrates inter-chirp phase consistency. It may
never replace fixed-loop 0 because of Masimo performance. The production phase extractor's
`delta_before_mean` behavior is not evidence that raw coherent averaging is safe.

### 6.3 Timing and availability constraints

- The raw data preserves per-chirp complex samples and separate RX channels, satisfying the
  data needed by Eqs. (17) and (23).
- The repository has no per-frame timestamp vector. Nominal frame timing, capture metadata,
  file-size divisibility, and any packet/drop metadata available at run time must be saved.
- If frame continuity, fixed-loop indexing, finite complex data, or required metadata cannot
  be established, the adapter fails closed before any Kotte estimate is reported.

## 7. Prior M9 findings and four-file disposition

These are evidence and constraints, not instructions to tune future real-data results.

### 7.1 Findings that survive

- **DECISION — comparison target:** M9 evaluates the Kotte joint Doppler stage. Reusing
  project range selection and omitting DOA preserves the joint-frequency core but is not the
  complete three-stage Kotte pipeline.
- **DECISION — primary paper objective:** Algorithm 1's `w^H R_t w` surface is primary;
  Eq. (26) is a direct-synthetic diagnostic.
- **CONSTRAINT — rank:** with `Y_t in C^(16 x 4)`, the sample `R_t` has rank at most four.
  Unloaded inversion is impossible on project geometry.
- **CONSTRAINT — aggregation:** surface averaging and pooled snapshots change the coherent
  two-line model. The canonical window report is the 2-D L1 medoid of per-CPI pairs, with
  lowest-CPI-index tie-break. Pooled/mean-surface outputs are not canonical Kotte results.
- **CONSTRAINT — mean removal:** exact per-CPI mean removal makes `R_t` singular. Project
  mean removal over the retained 592-frame support is a declared adaptation.
- **NEGATIVE RESULT — literal SNR:** at literal post-range/`Y_t` 0 dB, the prior Fig. 8
  control selected the wrong ridge in 3/3 amplitude cases. The exact-covariance example had
  truth value 4.31 and an inter-truth ridge value 5.18.
- **DIAGNOSTIC — alternate SNR:** adding `10 log10(128) = 21.07 dB` as a hypothesized
  range-FFT gain reproduced the declared Fig. 5/7/8 behaviors at the recorded seeds. This is
  an interpretive sensitivity, not a settled paper fact or real-data rule.
- **NEGATIVE RESULT — vital-sign model order:** a real sinusoidal displacement produces at
  least `+/-f_b` and `+/-f_h`, not two cisoids. Prior synthetic work recovered 0/9 conjugate-
  pair cases at `N_c=16` and `N_c=32` across 10, 30, and 60 dB. Increasing SNR did not fix it.
- **DIAGNOSTIC ONLY — longer CPI:** recovery near `N_c=64` was observed for one oracle model,
  with biased BR and less than 0.02 dB margin. It is configuration-specific and cannot select
  the canonical real-data CPI or become an active arm.
- **LIMITATION — cancellation:** the paper's own `beta_1=-beta_2` case fails and must remain
  an expected negative control.

### 7.2 Existing implementation and evidence provenance

Partial work already exists and must be audited rather than rebuilt blindly:

| Item | Recorded provenance | Status under this plan |
|---|---|---|
| Groundwork/config/approach | `b39888f` | Historical input; config needs plan-conformance edits before any new run. |
| Core and controls | `3c0efc7` | Reuse after equation/data-contract audit. |
| SNR option-B change | `2fec07e` | Alternate-SNR sensitivity only; not the canonical paper fact. |
| Aggregation code | `f2e74c5` | Medoid/retained-support logic survives; coherent-mean adapter does not. |
| Oracle | `9078ed8` | Diagnostic-only; never a canonical selector. |
| Official Fig. 8 control | `20260806T154753.144349Z_6bde60353be2` | Historical evidence under the alternate-SNR interpretation. |
| Fig. 5/7/audits | `20260806T154841.509470Z_6bde60353be2` | Historical evidence; literal-0 dB null remains explicit. |
| 20-RX to 4-RX ablation | `20260806T155022.295892Z_6bde60353be2` | 24/24 rows passed under declared loading; does not remove the adaptation label. |
| Chest oracle output | `results/m9/step1b/oracle/20260806T160339.308982Z/` | Diagnostic-only and gitignored; provenance retained here. |

The analysis-spec amendment, SNR interpretation, and oracle mathematical finding still
need the other model family's cross-review before they support a thesis claim. Their pending
review does not block plan readiness; it blocks treating those claims as settled evidence.

### 7.3 Disposition of the former four documents

| Former document | Useful content retained here | Removed or superseded |
|---|---|---|
| Previous `plans/m9_kotte_plan.md` | Paper equations, controls, data/rank facts, medoid, evidence requirements, provenance | Overbuilt bundle/gate/runner architecture, MAE go/no-go, stale status, duplicated rationale |
| `plans/m9_comments_plan.md` | Verified mathematical corrections and review decisions | Nine-pass narrative, obsolete alternatives, stale "zero code" statements |
| `plans/m9_step1a_snr_finding.md` | Literal-vs-FFT-gain ambiguity, bound/ridge result, run provenance | Option B as a hard algorithm rule; comparator tolerance as a Kotte requirement |
| `plans/m9_step1b_oracle_finding.md` | Sideband model mismatch, negative results, `N_c=64` limitation, four-line future direction | A/B/C checkpoint and any oracle-driven real-data arm selection |

## 8. Scientific decisions frozen before further implementation

These decisions may not change after Masimo or Kotte real-data errors are inspected:

| Decision | Frozen value or rule | Rationale |
|---|---|---|
| Canonical slow-time sample | chirp-loop 0 at the selected bin from each 50 ms frame, four RX retained | Closest uniform mapping to the paper; no intra-frame phase-averaging assumption |
| CPI | `N_c=16` consecutive frames, 0.8 s | Paper-comparison value; oracle cannot promote `N_c=64` |
| Window | frozen consecutive, non-overlapping 600-frame/30 s grid | Existing project comparator contract |
| Within-window support | first 592 frames; 37 complete CPIs; tail dropped before mean removal | Deterministic complete-CPI rule |
| Mean removal | subtract RX-column mean over retained 592-frame support | Declared project adaptation; not paper literal |
| Primary loading | trace-relative `delta=1e-2` | Required for 4-RX rank deficiency; declared before real results |
| Loading sensitivity | trace-relative `delta=1e-4`, separately labelled | Numerical sensitivity only; cannot replace primary |
| Primary aggregation | 2-D L1 medoid of 37 CPI pairs; lowest CPI index breaks ties | Reports an actually selected pair without incoherent surface pooling |
| Validity | every CPI must be numerically valid; otherwise the window is invalid | Fail closed; no survivor rule selected after outcomes |
| Search bands | breath `+/-[0.10,0.50]` Hz; heart `+/-[0.80,2.00]` Hz | Existing project physiological domains, not derived from M9/Masimo performance |
| Grid | 0.5 bpm (`1/120` Hz) | Existing declared M9 grid; fine sampling does not imply physical resolution |
| Canonical sign/pair | collapse signed aliases by `(abs(f_b), abs(f_h))`; keep raw signed pair; lexicographic signed tie-break | Deterministic handling of conjugate/swap symmetry |
| Range source | one `current_production_rerun_lock` per capture, generated before Kotte by the unchanged radar-only warmup selector | Uniform current rule; no Kotte/Masimo bin selection |
| Recorded historical lock | separate diagnostic estimand only; never pooled with rerun lock | Three pre-M2 recorded locks are documented as buggy/mislocked |
| Capture cohort | the eight captures listed in the M9 config, with subject/protocol roles from `notes/capture_inventory.md` | Fixed exploratory cohort |
| Real-data success threshold | none | Accuracy is a result, not an implementation gate |

Before any new M9 run, `experiments/m9_kotte/config.yaml` must be aligned with this table,
reviewed, and committed. In particular, its current `chirp_aggregation: coherent_mean`,
oracle gate, and MAE Stage-B decision are not authoritative under this plan.

## 9. Kotte-to-project adaptation boundary

### Literal Kotte claim permitted

Only the direct synthetic, full-rank, unloaded Section III-C control may be called a
paper-faithful implementation of the joint Doppler equations. It uses `N_c=16`, `n_R=20`,
the paper covariance divisors, signed two-cisoid inputs, Algorithm 1's primary surface,
and Eq. (26) with known angle as a diagnostic. It does not reproduce the full range/DOA
pipeline and must be described accordingly.

### Project adaptation claim permitted

The real-data method is **Kotte's joint Doppler estimator adapted to this project**:

- range is supplied by the unchanged project warmup selector;
- DOA is omitted because the frequency-selection surface is angle-free and the study makes
  no localization claim;
- slow time is fixed chirp-loop 0 across frames;
- the 4-RX covariance is trace-loaded;
- retained-support mean removal and multi-CPI medoid reporting are project operations;
- breath/heart bands and absolute-frequency labeling are project priors.

Skipping Kotte's range and DOA stages means the output must never be called a literal
implementation of the complete Kotte pipeline.

## 10. Implementation milestones

No Python, tests, config, or experiment is implemented by this planning turn.

### M9.1 — Paper-equation and existing-code conformance audit

**Objective.** Establish that the existing direct-`Y_t` core implements Eqs. (23)-(26) and
Algorithm 1 as printed before relying on prior controls.

**Rationale.** Existing code predates this consolidated review and contains a nonconformant
real-data adapter; the mathematical core may still be reusable.

**In scope.** `src/m9/kotte_core.py`, `src/m9/paper_control.py`,
`figures/reproduce_kotte_controls.py`, the `controls` config, and focused tests.

**Non-goals.** Raw radar, range FFT, Masimo, DOA, real-data accuracy.

**Required interfaces and shapes.** Direct complex `Y_t` of `(16,20)` for literal controls
and `(16,4)` for the declared ablation; physical-Hz steering with `T_PRI=0.05 s`.

**Algorithm.** Verify covariance divisors, constraints, solve order, Algorithm 1 surface,
Eq. (26), swap symmetry, signed grid, and cancellation.

**Numerical risks.** Rank deficiency, singular/near-singular `H`, source SNR ambiguity,
complex conjugation/transposition errors.

**Tests.** Constraint residuals, loop-versus-vectorized equality, direct identity
`w^H R w = 1^H H^-1 1`, pair-swap invariance, signed Fig. 5 cases, equal-frequency refusal,
`beta_1=-beta_2` expected failure, and literal/alternate SNR separation.

**Validation command.** The later implementer must provide one documented control command
using the `radar-vitals` environment and record its exact invocation and hashes.

**Acceptance.** All mathematical tests pass, PDF facts are cited in test comments, direct
controls persist both surfaces, and the literal-0 dB result remains separate from the
alternate-SNR sensitivity. A mismatch is a valid finding but blocks M9.2 until explained.

### M9.2 — Project adapter and synthetic transfer

**Objective.** Prove that the project can form the required `Y_t` without reference leakage
and characterize the two-line model on chest-like signals.

**Rationale.** This separates acquisition/rank limitations from physiological model mismatch.

**In scope.** Fixed-loop-0 raw-cube adapter, retained-support detrend, CPI split, trace loading,
medoid reporting, 4-RX direct-cisoid ablation, and conjugate/Bessel synthetic displacement.

**Non-goals.** Masimo, real capture accuracy, multi-line repair, oracle-selected parameters.

**Expected files.** Amend the existing `src/m9/kotte_core.py` and M9 config; keep a small
synthetic runner using the existing controls/oracle code where scientifically valid. Do not
introduce bundle or service layers.

**Data shapes/units.** Synthetic cube `(frames,32,4,256)` `complex64`; adapter output
`(frames,4)` `complex128`; CPI `(16,4)`; `f` in Hz and `60f` in bpm.

**Algorithm.** Apply the adapter contract in Section 6, compute the regularized project
surface per CPI, canonicalize each signed pair, and report the 2-D medoid.

**Numerical risks.** Cross-frame phase discontinuity, chirp-loop misindexing, rank four,
loading dominance, flat objectives, signed aliases, breathing harmonics, cancellation.

**Tests.** Exact fixed-loop extraction, RX ordering, no chirp averaging on primary,
cross-frame phase-ramp recovery, tail immutability, mean-removal scope, expected rank, loading
formula, invalid-CPI failure, medoid determinism, conjugate sideband generation, and evidence
round trip without pickle.

**Acceptance.** A known synthetic physical-Hz phase ramp is recovered from fixed-loop 0;
frame continuity checks pass; the 4-RX two-cisoid control is measured; chest-model results
match an independent diagnostic within declared numerical tolerance. Chest-model failure does
not block a documented real-data evaluation and must not be tuned away.

**Stop conditions.** Stop before real data if fixed-loop indexing, nominal timing, complex
phase continuity, or matrix shapes cannot be established, or if the code does not fail closed
on singular/nonfinite inputs.

### M9.3 — Canonical radar-only evaluation

**Objective.** Produce one canonical Kotte-adaptation estimate and one declared loading
sensitivity for every complete window in the fixed cohort, without opening Masimo.

**Rationale.** Radar decisions must be complete before reference performance is visible.

**In scope.** Current-production lock-map regeneration using unchanged warmup code, eight
manifest captures, fixed non-overlapping windows, primary and loading-sensitivity arms, evidence
and provenance.

**Non-goals.** Kotte-selected bins, all-bin winner search, DOA, parameter changes, scoring.

**Expected files.** One readable radar runner (for example `scripts/m9_kotte_run.py`) calling
the audited core; the existing config is the only experiment config.

**Algorithm.** Hash raw/config/code, generate the radar-only lock map, decode each window once,
form fixed-loop `Z`, run both frozen loading arms, and write tidy estimates plus per-window
evidence.

**Numerical risks.** Old mislocks, packet/truncation defects, phase discontinuity, NaN/Inf,
memory pressure, all-masked surfaces.

**Tests.** Manifest uniqueness, unchanged warmup-selector call, no reference imports or file
opens, raw hash binding, exact window counts, one decode per window, deterministic rerun,
failure rows, and evidence completeness.

**Acceptance.** Every complete window has an emitted estimate or explicit failure for each arm;
no Masimo path is opened; every row binds to capture/bin/window/config/code/input hashes; recorded
and rerun locks remain separate. Poor or zero coverage is a valid result.

**Stop conditions.** Stop the affected capture on input-hash, frame-count, timing, or decoder
failure. Do not choose another bin or setting to rescue it.

### M9.4 — Exploratory scoring and report

**Objective.** Compare immutable radar-only outputs with the reference under the existing
comparator rules without feeding reference information back into M9.

**Rationale.** Accuracy and coverage are scientific outcomes, not implementation gates.

**In scope.** Radar-free scoring, HR and BR coverage/error, per-capture/per-subject/per-protocol
summaries, failure census, Bland-Altman outputs where evaluable, and a concise report.

**Non-goals.** Tuning, arm promotion, a GO/NO-GO MAE threshold, or a final HR agreement claim
from the low-dynamic-range cohort.

**Expected files.** One readable scorer (for example `scripts/m9_kotte_score.py`) and a generated
report/artifact directory. Reuse existing comparator modules; do not edit M8.

**Reference contract.** Integer Unix `Timestamp`; `Beats / min` is HR truth; `Breaths / min`
is BR reference; existing PI/stationarity gates apply. The eight old captures use approximate
origin and every scored artifact is `exploratory_non_frozen`, ineligible for promotion or final
agreement claims.

**Tests.** Radar-free import/file audit, exact half-open reference windows, duplicate-key and
reference-identity rejection, missing-reference handling, metric fixtures, taint propagation,
and unchanged radar artifacts.

**Acceptance.** Coverage and failure causes accompany every error table; HR is reported
descriptively beside `constant_session_median`; protocols and lock estimands are not pooled;
no absolute MAE is required for scientific correctness.

## 11. Synthetic validation

### 11.1 Literal direct-`Y_t` controls

- Use the paper's `N_c=16`, `n_R=20`, `T_PRI=50 ms`, signed frequencies, and the Fig. 5,
  Fig. 7 row 2, and Fig. 8 cases that can be reconstructed from the PDF.
- Use `R_t = Y_t Y_t^H / n_R`; no loading and no mean removal in the literal primary.
- Persist Algorithm 1 and Eq. (26) surfaces separately.
- Permit pair swap when checking truth.
- Reproduce the cancellation case as an expected failure.
- Record seeds, generated `Y_t` hashes, config, code, PDF hash, and exact assumptions.

The paper does not fully specify the grid, covariance realization, or SNR reference domain.
Those are declared assumptions, never silently described as source facts.

### 11.2 Four-RX and project controls

- Run a fixed 20-RX/4-RX direct-cisoid comparison on identical realizations.
- Use the exact project loading formula and label the result an adaptation.
- Validate fixed-loop extraction on a synthetic raw cube with known phase evolution.
- Exercise small displacement, deep modulation, harmonic collision, and phase-cancellation
  scenarios without converting their oracle truth into real-data settings.
- Treat robustness seeds as a stability description, not a tuning loop.

### 11.3 Interpretation of a negative synthetic result

A failed chest-transfer case does not mean the Python is wrong if the direct two-cisoid control
passes. It may demonstrate that the published two-line model does not represent phase-modulated
chest displacement. That negative result is preserved and the canonical real evaluation may
proceed only with the limitation stated in advance.

## 12. Real-data evaluation

### 12.1 Capture and window universe

- The eight captures and subject/protocol mapping are fixed in the M9 config and
  `notes/capture_inventory.md`.
- Use complete, consecutive, non-overlapping 30 s windows `[k*600,(k+1)*600)`.
- Retain `k=0` as `lock_selection_in_sample`; only `k>=1` supports comparative accuracy.
- Run the primary and loading sensitivity exactly as frozen in Section 8.

### 12.2 Range selection

Before Kotte runs, invoke the unchanged current production warmup selector once per capture,
using radar only. Persist `current_production_rerun_lock`, the selector's code/config/input
hashes, and its evidence. Do not let a Kotte surface, Masimo value, all-bin error, or historical
recorded lock choose the canonical bin.

The recorded `warmup_auto` lock is a separate diagnostic estimand because pre-M2 captures
contain documented mislocks. It is never pooled with the rerun-lock result.

### 12.3 Heart/breath interpretation

The frame-axis estimator frequency is a temporal complex-modulation frequency in Hz; report
`60*abs(f)` in bpm. It is not a direct radial-velocity estimate requiring a further wavelength
conversion. Periodic displacement produces sidebands at physiological frequencies and their
combinations, so:

- the breath-band coordinate is labelled a breathing candidate;
- the heart-band coordinate is labelled a heart candidate;
- higher-frequency = heart and lower-frequency = breath is only an operational prior within
  the two disjoint bands;
- sidebands, harmonics, motion, or collisions can invalidate that interpretation.

### 12.4 Valid and invalid outcomes

Never force an estimate. Nonfinite data, rank-zero covariance, nonpositive loaded spectrum,
invalid CPI, all-masked pair surface, or failed structural checks produce an explicit invalid
row and evidence. Coverage is reported jointly with accuracy.

### 12.5 Reference scoring and metrics

Scoring occurs only after radar outputs are immutable. Report per capture, subject, protocol,
and lock estimand:

- algorithmic validity and joint radar/reference coverage;
- MAE and RMSE in bpm on identical paired cells;
- Bland-Altman bias and limits of agreement where the paired count supports them;
- failure-reason counts and pair-margin summaries;
- HR descriptive comparison with `constant_session_median` because the existing captures
  cannot falsify an HR claim.

No metric threshold decides whether Kotte was implemented correctly. Do not rank arms, pool
protocols/locks, or turn approximate-origin scores into validation claims.

## 13. Oracle policy

Oracle information is diagnostic and nondeployable.

It may:

- check equations and synthetic generators;
- identify model-order mismatch and expected failure mechanisms;
- provide a labelled upper bound or sensitivity result.

It may not select or promote the canonical CPI, chirp aggregation, loading, bin, band, grid,
target, arm, acceptance rule, or score. In particular, the observed `N_c=64` recovery is not
an active arm and cannot replace the paper-comparison `N_c=16` primary. No oracle column enters
the real estimator or canonical performance table.

## 14. SNR finding disposition

The paper defines

```text
SNR = 10 log10[(|beta_1|^2 + |beta_2|^2) / sigma^2]
```

but does not state unambiguously whether `sigma^2` is before or after the range FFT.

Canonical interpretation of the prior investigation:

1. **Primary historical fact:** literal post-range/`Y_t` 0 dB did not reproduce Fig. 8
   under the declared sample-covariance implementation.
2. **Interpretive sensitivity:** adding `10 log10(N_s)` with `N_s=128` gave 21.07 dB and
   reproduced the selected figure behaviors at recorded seeds.
3. **Not an algorithm rule:** the 21.07 dB interpretation does not set real-data noise,
   loading, validity, or performance thresholds.
4. **Claim status:** the SNR interpretation remains unsettled until independently cross-reviewed;
   both arms and their assumptions must be reported.

The earlier FFT/MUSIC weak-peak tolerance was a declared control comparator rule, not part of
Kotte's estimator, and is not carried into real-data selection.

## 15. Numerical stability policy

### Literal synthetic controls

- Use complex floating-point arrays and reject nonfinite inputs.
- Use the paper sample divisors.
- Evaluate ordinary full-rank equations with linear solves rather than explicit inverses.
- Do not load, pool, or use a pseudoinverse silently.
- Exclude `f_1=f_2`; mask near-equal cells under a declared `rcond(H)` rule.
- Persist masks and fail if no admissible pair remains.

### Project 4-RX adaptation

Let

```text
R_t = Y_t Y_t^H / 4
delta_bar = delta * Re(trace(R_t)) / N_c
R_delta = R_t + delta_bar I
H_delta = A^H R_delta^-1 A.
```

The regularized project surface is exactly

```text
J_delta(f_1,f_2) = 1^H H_delta^-1 1
                   = w_delta^H R_delta w_delta.
```

It is **not** the literal `w_delta^H R_t w_delta`; it includes the loading penalty
`delta_bar ||w_delta||^2`. Name it `regularized_kotte_power` in outputs.

Also:

- numerical rank is `count(lambda_i > rank_rtol * lambda_max)`;
- finite `lambda_max <= 0` is rank zero and invalid;
- nonfinite eigenvalues/covariances are invalid;
- persist unloaded eigenvalues/rank, `delta`, `delta_bar`, `rcond(H_delta)`, and masks;
- canonicalize signed aliases before computing the runner-up margin;
- use deterministic lexicographic tie handling;
- no setting changes in response to Masimo error.

## 16. Tests required during implementation

| Category | Objective evidence |
|---|---|
| Paper mathematics | shapes; covariance divisors; steering units; Eq. (25) constraints; Algorithm 1 identity; Eq. (26); conjugation/transpose; pair swap; cancellation |
| Synthetic controls | Fig. 5/7/8 declared cases; literal/alternate SNR separation; fixed seeds and hashes; equal/near-frequency handling |
| 4-RX numerics | rank bound; loading formula; loaded-objective identity; scale behavior; phase/gain sensitivities; fail-closed branches |
| Adapter | `(frames,32,4,256)` to fixed-loop `(frames,4)`; chirp index; RX order; dtype; Hann FFT; timing; known phase ramp; tail and mean scope |
| Aggregation | 37 CPIs; all-valid rule; 2-D medoid membership and tie-break; signed alias collapse; margin definition |
| Radar runner | fixed manifest; current-production lock generation; no Masimo access; deterministic window counts; input/output hashes; evidence completeness |
| Scoring | radar-free; integer `Timestamp`; correct PR/RR fields; half-open spans; comparator gates; duplicate/reference mismatch refusal; approximate-origin taint |
| Regression | no diff to M8/Ahmed or the project's estimator; frozen window/comparator behavior unchanged |

Plots may supplement but never replace objective assertions.

## 17. Acceptance criteria

### M9.1

- All direct-`Y_t` equations, dimensions, constraints, and objective identities pass.
- PDF-derived controls are reproducible with declared assumptions.
- Literal-0 dB and alternate-SNR results remain separately labelled.

### M9.2

- Fixed-loop-0 extraction and physical-Hz steering recover known synthetic phase ramps.
- The 4-RX loaded adaptation is deterministic, evidenced, and explicitly nonliteral.
- Chest-model behavior is recorded without parameter changes, whether positive or negative.

### M9.3

- Every fixed capture/window/arm yields an estimate or an explicit failure.
- No reference is opened and no Kotte/Masimo-selected bin is used.
- Every estimate traces to code, config, seed where applicable, raw hash, lock evidence,
  and intermediate arrays.

### M9.4

- Radar outputs are unchanged by scoring.
- Coverage and failures accompany error metrics.
- Approximate-origin and low-HR-dynamic-range limitations are explicit.
- No MAE target is required. Poor performance is a valid M9 result.

## 18. Known limitations and resolved special questions

| Question | Canonical answer |
|---|---|
| Q1. One slow-time sample? | Selected-bin complex values from chirp-loop 0 in one frame, retaining four RX; nominal interval 50 ms. |
| Q2. Within frame or across frames? | Across frames. Within-frame `N_c=16` spans about 1 ms and cannot resolve 0.10-2.00 Hz. Coherent chirp mean is sensitivity-only. |
| Q3. Doppler-to-vital conversion? | The frame-axis result is temporal modulation frequency in Hz; report `60*abs(f)` bpm. It is not guaranteed to be a unique physiological or radial-velocity component. |
| Q4. Higher = heart, lower = breath? | Operational only within disjoint declared bands; invalidated by signs, harmonics, sidebands, collision, or motion. |
| Q5. Is 4 RX enough for DOA? | Four RX can form a spatial covariance but offers less aperture than 20 RX and lacks M9 calibration evidence. M9 makes no DOA claim. |
| Q6. May project range selection be reused? | Yes for a controlled joint-Doppler adaptation. It does not reproduce Kotte's complete range stage. |
| Q7. May angle be fixed? | Known angle is permitted only for direct-synthetic Eq. (26) diagnostics. Real frequency selection omits DOA and Eq. (26), with disclosure. |
| Q8. What is the 2-D objective? | Literal: maximize `1^H H^-1 1 = w^H R_t w`. Project: maximize `1^H H_delta^-1 1 = w_delta^H R_delta w_delta`, explicitly regularized. |
| Q9. Symmetric solutions? | Search signed breath x heart domains, retain raw signs, collapse by absolute band coordinates, deterministic lexicographic tie-break. |
| Q10. `f_1=f_2` or close? | Equality excluded; ill-conditioned `H` masked by declared threshold; all masked means invalid. Project bands are disjoint but controls still test this. |
| Q11. Search ranges? | Existing project domains `+/-0.10-0.50` and `+/-0.80-2.00` Hz, fixed independently of Masimo; 0.5 bpm grid is sampling, not resolution. |
| Q12. Minimum persisted evidence? | Raw/config/code/PDF hashes; lock source/bin; cube shape/dtype/timing; frame/CPI spans; selected `Z`; covariance eigenvalues/rank/loading; grids/masks/surfaces; raw and canonical pair; margin; validity cause; seed/input/output hashes. |

Additional limitations:

- The paper's temporal steering units and SNR domain are ambiguous.
- The paper does not validate human heart-rate estimation.
- Project `R_t` is structurally rank deficient without loading.
- Retained-support mean removal, fixed-loop framing, loading, band restriction, and medoid
  reporting are project adaptations.
- The two-line model is misspecified for general phase-modulated chest displacement.
- The eight old captures have approximate time origin and insufficient HR dynamic range for a
  final HR agreement claim.
- The project has not established calibrated 4-RX DOA performance.

## 19. Implementation handoff

A later implementation agent is authorized to execute this plan in milestone order, beginning
with M9.1 and stopping after each milestone for verification. Before any new evidence run it
must:

1. re-read `CLAUDE.md` and this file;
2. inspect the current M9 diff and preserve useful existing code;
3. align `experiments/m9_kotte/config.yaml` with Section 8;
4. replace the canonical coherent-mean adapter with fixed chirp-loop 0;
5. keep coherent averaging only as a named radar-only sensitivity after its structural test;
6. avoid M8 and the project's estimator files;
7. use a simple core, adapter, runner, scorer, one config, and inspectable evidence;
8. run the required cross-model reviews before treating mathematical or empirical claims as
   settled thesis evidence.

The implementer is not authorized to tune from Masimo, add the oracle `N_c=64` arm, create a
four-line estimator, or resurrect the retired bundle/gate/MAE-decision architecture without a
new scientific plan and user authorization.

## 20. Plan-review verdict

**READY WITH MINOR CHANGES — minor changes incorporated.**

Independent review checked the original PDF, all four former M9 documents, actual raw-cube
interfaces, current M9 code/config, and the consolidated draft. It required:

1. fixed chirp-loop 0 across frames as canonical slow time;
2. separate DOA and Doppler covariance divisors;
3. an exact name/formula for the regularized project objective;
4. explicit retained-support mean removal as an adaptation;
5. a uniform current-production rerun lock map rather than known-buggy recorded locks;
6. complete timing, dtype, and phase-continuity stop conditions.

All six changes are present above. No planning blocker remains. Implementation remains gated by
the milestone-specific acceptance and stop conditions in this file.
