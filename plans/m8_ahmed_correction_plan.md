# M8 Ahmed HA Correction Plan

**Status:** READY WITH MINOR CHANGES — APPROVED FOR IMPLEMENTATION  
**Scope:** Ahmed et al. Section III-C Fourier Series-Based Harmonic Accumulation only  
**Document type:** Correction and re-validation plan; no implementation or result regeneration is contained in this document

The incorporated minor reviewer changes are part of the approved plan. Implementation must follow this document without selecting any Ahmed interpretation or parameter from real-data performance.

## 1. M0 owner decisions recorded

Milestone M0 is scientifically authorized as follows.

- Canonical real-data design: restore the accepted two-lock/seven-arm paired comparison.
- Lock identities:
  - `recorded_lock_as_captured`
  - `current_production_rerun_lock`
- Arms:
  - one production arm;
  - six accepted fixed-H Ahmed phase arms.
- The 14-bin sweep is an optional, separately identified radar-side diagnostic only.
- Any reference-selected bin remains a nondeployable oracle/ceiling analysis.
- The Layer A profile register is approved:
  - `matrix_eta` is equation-literal normalization;
  - `non_dc_mean` is a reconstruction convention;
  - fixed-H and candidate-dependent rows remain separate;
  - magnitude accumulation is an explicit interpretation;
  - all three suppression interpretations remain separate.
- Profiles cannot be selected from corrected Masimo error.
- Historical bundles remain immutable and noncanonical.

No owner decision remains open.

## 2. Resolved blockers

| Previous blocker | Status | Resolution |
|---|---|---|
| Real-evaluation authority was unresolved. | RESOLVED | The accepted two-lock/seven-arm comparison is restored as canonical. |
| Exact lock identities were not fixed. | RESOLVED | `recorded_lock_as_captured` and `current_production_rerun_lock` are recovered directly from the accepted Step 1b plan. |
| Exact arm identities were not fixed. | RESOLVED | The production arm and six Ahmed arm IDs are recovered from the accepted plan/current suite definitions. |
| The later 14-bin sweep could replace the accepted experiment. | RESOLVED | It is optional, separately authorized, run only after canonical scoring, and cannot feed canonical metrics. |
| Layer A normalization identity was unresolved. | RESOLVED | `matrix_eta` is equation-literal; `non_dc_mean` is reconstruction-only. |
| Fixed-H and candidate-dependent rows could be silently conflated. | RESOLVED | They have distinct profile identities. Candidate-dependent rows remain Layer A audits with no Layer B arm mapping. |
| Suppression ambiguity could permit outcome-based selection. | RESOLVED | The three accepted suppression identities remain separate and equally reported. |
| The authoritative gate was generated before all real runner/scorer code existed. | RESOLVED IN PLAN | The authoritative gate moves to M5, after M1-M4 implementation, tests, and review are complete. |
| Old synthetic evidence was overstated. | RESOLVED | Existing Figure 8 and transfer bundles are historical partial controls, not canonical gate/reproduction evidence. |

## 3. Exact canonical experiment identity

### Lock identities

1. `recorded_lock_as_captured`

   The capture's recorded lock, hash-bound to the raw radar file, capture metadata, and recorded warmup evidence.

2. `current_production_rerun_lock`

   Recompute `production_warmup_selector_v1` once per capture on `k=0` using the accepted production configuration. Share that selected bin across all seven arms.

   The selector's estimator payload must not be reused as the production result. All seven estimates on `k=0` are recomputed on the common paired input.

The lock IDs remain distinct even when they select the same numeric bin.

### Seven paired arms

| Arm | Exact ID | Estimator identity |
|---:|---|---|
| 1 | `production_eca_ahet_v1` | `eca_ahet_v1` |
| 2 | `ahmed_phase_h3_figure_visible_unsuppressed` | `ahmed_fixed_h_phase_v1` |
| 3 | `ahmed_phase_h3_eq26_multiples_suppressed` | `ahmed_fixed_h_phase_v1` |
| 4 | `ahmed_phase_h3_prose_low_or_equal_suppressed` | `ahmed_fixed_h_phase_v1` |
| 5 | `ahmed_phase_h5_figure_visible_unsuppressed` | `ahmed_fixed_h_phase_v1` |
| 6 | `ahmed_phase_h5_eq26_multiples_suppressed` | `ahmed_fixed_h_phase_v1` |
| 7 | `ahmed_phase_h5_prose_low_or_equal_suppressed` | `ahmed_fixed_h_phase_v1` |

The six Ahmed arms use:

- fixed H=3 or H=5;
- `non_dc_mean`;
- rFFT magnitude accumulation;
- one of the three accepted suppression identities;
- Layer B \(q=f\), rate \(=60q\);
- strict \(Hq<f_\mathrm{Nyquist}\).

`non_dc_mean` is part of this accepted FMCW reconstruction identity. It is not represented as uniquely equation-literal Ahmed.

Because the real HR domain begins at 0.8 Hz and the BR domain ends at 0.5 Hz, the prose and unsuppressed arms are expected to coincide on current real inputs. The prose arm nevertheless remains separately emitted and is labeled `dependent_duplicate_by_disjoint_domains`; it is not independent corroboration.

### Pairing contract

For every canonical cell, production and all six Ahmed arms receive:

- the same decoded radar cube;
- the same capture;
- the same 600-frame window;
- the same lock identity and numeric selected bin;
- the same source/configuration state;
- the same run identity.

### Window universe

Use `src/m4/window_grid.py` exactly:

- contiguous, non-overlapping 600-frame/30 s windows;
- start at `k=0`;
- discard incomplete tails;
- do not reinterpret the live 3 s display hop as an offline window hop.

Expected full-run cardinalities:

- eight captures;
- 128 source windows;
- two lock identities;
- 256 shared `(capture, lock, k)` rows;
- 1,792 estimator rows: \(128\times2\times7\);
- 1,536 Ahmed evidence rows;
- 256 production evidence rows.

### Comparative universe

`evaluation_k_ge_1` is the only comparative accuracy universe:

- 120 source windows;
- 240 shared lock/window rows;
- 1,680 estimator rows.

`k=0` is retained as:

- `lock_selection_in_sample`;
- `full_k0_diagnostic`.

It cannot enter canonical MAE, RMSE, hit-rate, or production-versus-Ahmed comparisons.

### Masimo

Masimo is inaccessible to radar estimation.

During scoring only:

- `Beats / min` is HR truth;
- alignment uses integer Unix `Timestamp`;
- approximate `start_wall_utc` origins retain their documented 5-15 s uncertainty;
- affected scores remain `exploratory_non_frozen` and ineligible for final agreement or promotion claims.

Masimo cannot choose a lock, profile, H, suppression rule, frequency band, or diagnostic bin.

### Optional 14-bin diagnostic

The 14-bin experiment is a separate radar-side sensitivity surface:

- separate authorization;
- separate experimental ID;
- separate run ID and manifest;
- executed only after canonical paired scoring completes;
- cannot block, alter, replace, or reinterpret the canonical result;
- cannot supply canonical metrics.

Any reference-selected best-bin result is labeled:

`NONDEPLOYABLE ORACLE / CEILING ANALYSIS`

## 4. Final Layer A profile register

For all profiles, the exact magnitude-score interpretation is:

\[
A(q)=\frac{1}{\eta(q)}
\sum_{h=1}^{H(q)} |S(hq)|
\]

This is a sum of spectral magnitudes. It is not:

\[
\left|\sum_h S(hq)\right|.
\]

For fixed H:

- `matrix_eta`: \(\eta=H+1\), including the matrix's DC entry before DC subtraction;
- `non_dc_mean`: \(\eta=H\).

For candidate-dependent rows:

\[
\eta(q)=1+\text{number of positive supported multiples of }q.
\]

The H versus H+1 selection-equivalence claim applies only within one fixed H. It does not justify cross-H amplitude comparisons.

In the table, `{H}` expands immutably to `h3` and `h5`.

| Profile ID | Row-support interpretation | Normalization | Score functional | Suppression interpretation | Paper support | Layer B arm mapping |
|---|---|---|---|---|---|---|
| `a_fixed_h{H}_matrix_eta_magnitude_unsuppressed_v1` | Fixed H=3/5 | `matrix_eta`, \(\eta=H+1\) | Magnitude sum | Figure-visible unsuppressed | PAPER AMBIGUITY INTERPRETATION: fixed H from Fig. 8 prose; eta from Eq. 23; unsuppressed from visible figure | Layer A selection-equivalence audit only; corresponding accepted unsuppressed arm |
| `a_fixed_h{H}_matrix_eta_magnitude_eq26_v1` | Fixed H=3/5 | `matrix_eta` | Magnitude sum | Eq. 26 breathing bin and multiples | PAPER AMBIGUITY INTERPRETATION combining fixed-H prose with direct Eq. 26 suppression | Layer A audit only; corresponding accepted Eq. 26 arm |
| `a_fixed_h{H}_matrix_eta_magnitude_prose_v1` | Fixed H=3/5 | `matrix_eta` | Magnitude sum | Candidate \(\le i_b\) exclusion | PAPER AMBIGUITY INTERPRETATION combining fixed-H prose with subsequent suppression prose | Layer A audit only; corresponding accepted prose arm |
| `a_fixed_h{H}_non_dc_mean_magnitude_unsuppressed_v1` | Fixed H=3/5 | `non_dc_mean`, \(\eta=H\) | Magnitude sum | Figure-visible unsuppressed | RECONSTRUCTION CONVENTION with source-supported fixed H and visible suppression behavior | `ahmed_phase_h{H}_figure_visible_unsuppressed` |
| `a_fixed_h{H}_non_dc_mean_magnitude_eq26_v1` | Fixed H=3/5 | `non_dc_mean` | Magnitude sum | Eq. 26 breathing multiples | RECONSTRUCTION CONVENTION plus DIRECT PAPER SUPPORT for suppression | `ahmed_phase_h{H}_eq26_multiples_suppressed` |
| `a_fixed_h{H}_non_dc_mean_magnitude_prose_v1` | Fixed H=3/5 | `non_dc_mean` | Magnitude sum | Candidate \(\le i_b\) exclusion | RECONSTRUCTION CONVENTION plus DIRECT PAPER SUPPORT for prose rule | `ahmed_phase_h{H}_prose_low_or_equal_suppressed` |
| `a_candidate_row_matrix_eta_magnitude_unsuppressed_v1` | All positive supported multiples for each candidate | Candidate-dependent `matrix_eta` | Magnitude sum | Unsuppressed | DIRECT PAPER SUPPORT / mathematical implication of displayed matrices; figure behavior supplies unsuppressed identity | None—Layer A ambiguity audit only |
| `a_candidate_row_matrix_eta_magnitude_eq26_v1` | All positive supported multiples | Candidate-dependent `matrix_eta` | Magnitude sum | Eq. 26 multiples | DIRECT PAPER SUPPORT from displayed matrices and Eq. 26 | None—Layer A ambiguity audit only |
| `a_candidate_row_matrix_eta_magnitude_prose_v1` | All positive supported multiples | Candidate-dependent `matrix_eta` | Magnitude sum | Candidate \(\le i_b\) exclusion | DIRECT PAPER SUPPORT from displayed matrices and suppression prose, but their combination is not uniquely specified | None—Layer A ambiguity audit only |

The magnitude functional remains a reconstruction convention because the PDF types complex \(S_p\) without defining an ordering for `argmax`; the figures' use of \(|S|\) makes this defensible but not uniquely specified.

Candidate-dependent profiles have no Layer B mapping. This prevents a paper-matrix audit from silently creating additional FMCW arms.

## 5. Revised M0-M5 sequence

### M0 — Authority and profile identity

Status: resolved and approved.

Record:

- canonical two-lock/seven-arm experiment;
- exact lock and arm IDs above;
- window/cardinality contract;
- approved profile register;
- optional diagnostic separation;
- approximate-origin/no-promotion status;
- exact command interface below.

No implementation or result execution belongs to M0.

### M1 — Layer A mathematics and Figure 8 successor

Implement later:

- literal/reference accumulation;
- full score-array comparison;
- fixed-H and candidate-dependent profiles;
- `matrix_eta` and `non_dc_mean`;
- magnitude-sum interpretation;
- all suppression masks;
- Figure 8 successor with status-derived title and byte-stable provenance.

Independent mutations must fail for:

- wrong H/H+1 divisor;
- wrong DC treatment;
- magnitude-of-complex-sum;
- truncated harmonic rows;
- factor-of-two drift;
- Nyquist equality;
- changed suppression mask.

The denominator mutation must fail on full score arrays even if its winning bin is unchanged.

No official successor bundle is generated until M5.

### M2 — Layer B interface and proportionate provenance

Preserve \(q=f\), rate \(=60q\), and independently validate phase-frequency mapping.

Use simple readable manifests and small validation helpers. The scientific dependency closure must include:

- decoder and capture geometry;
- phase extraction;
- window grid;
- warmup selector;
- production estimator and outcome classifier;
- Ahmed core;
- runner and scorer;
- Masimo parser and comparison functions;
- serializers;
- configurations, registries, profile register, plans, and authorization;
- relevant tests.

Record path, SHA-256, commit, and dirty/untracked status. An omitted imported scientific dependency must fail a dedicated test.

This does not require a new framework, plugin architecture, service, database, or generalized workflow engine.

No authoritative gate is generated until M5.

### M3 — Canonical paired runner and evidence

Implement exactly two locks x seven arms.

Required evidence for every estimate:

- lock identity and numeric selected bin;
- phase;
- frequency grid and spectrum;
- candidate bins;
- harmonic-bin matrix;
- support and Nyquist masks;
- suppression and eligibility masks;
- pre/post-suppression score arrays;
- selected and runner-up bins and scores;
- validity and reason;
- shared cube, signal, configuration, source, and run hashes.

Round-trip serialization must preserve dtype, shape, and values without object arrays.

Acceptance cardinalities:

- 256 shared rows;
- 1,792 estimator rows;
- 240 shared and 1,680 estimator rows in the derived `k>=1` view;
- no missing or duplicate keys.

`production_warmup_selector_v1` runs once per capture on `k=0`; its payload is never reused as an estimator result.

Expected estimator invalidity is recorded with evidence. Unexpected decode, configuration, schema, serialization, or DSP contract errors abort the run.

### M4 — Scoring and coverage

Consume only the same-run paired M3 artifact.

Enforce:

- `k>=1` comparative universe;
- `k=0` diagnostic only;
- protocol-stratified reporting;
- approximate-origin and no-promotion labels;
- exact paired-key joins;
- duplicate/missing-key rejection;
- measured coverage;
- validity-reason reconciliation;
- no best-arm headline;
- no external production artifact.

Masimo is accessed only here.

### M5 — Review, authoritative execution, and reporting

Official order:

1. Focused tests.
2. Independent code/scientific review.
3. Final scoped source manifest and authorization.
4. Figure 8 successor.
5. Authoritative synthetic gate.
6. Reopen and verify all bundle hashes.
7. `real-smoke`: m1/k=0, two locks, seven arms.
8. Verify smoke structure and parent.
9. Full eight-capture canonical paired radar run.
10. Canonical scoring.
11. Verify cardinalities, evidence, manifests, and labels.
12. Only afterward, optionally run the separately authorized 14-bin diagnostic.
13. Update documentation/thesis evidence only after validation.

Failure of the optional diagnostic cannot invalidate or modify the already completed canonical comparison.

Any scientific executable, configuration, profile, registry, or test change after the authoritative gate invalidates that gate.

### Exact planned command interface

These command strings are fixed for the later implementation:

```powershell
conda run -n radar-vitals python -X utf8 -m pytest tests/test_m8_ahmed_fig8.py tests/test_m8_ahmed_transfer.py tests/test_m8_ahmed_gate.py tests/test_m8_ahmed_all_bins.py tests/test_m8_ahmed_score.py -q
```

```powershell
conda run -n radar-vitals python -X utf8 figures/reproduce_ahmed_fig8.py
conda run -n radar-vitals python -X utf8 scripts/m8_ahmed_transfer.py synthetic
conda run -n radar-vitals python -X utf8 scripts/m8_ahmed_transfer.py real-smoke
conda run -n radar-vitals python -X utf8 scripts/m8_ahmed_transfer.py real-radar
conda run -n radar-vitals python -X utf8 scripts/m8_ahmed_transfer.py score
```

Optional diagnostic, only after canonical scoring:

```powershell
conda run -n radar-vitals python -X utf8 scripts/m8_ahmed_all_bins.py --all
```

The real-stage subcommands currently refuse execution. The later implementation must make these accepted command identities functional without changing their scientific scope.

## 6. Defect coverage matrix

| Defect ID | Milestone | Planned correction | Independent validation | Result requiring rerun |
|---|---|---|---|---|
| D1 | M2, M5 | Complete transitive source manifest; reject dirty/untracked/omitted dependencies; gate after final code | Temporary-repository mutation and omitted-import tests | Synthetic gate, canonical real run, score |
| D2 | M0, M2, M3 | Restore exact accepted authority; verify immutable parent before data access; execute two locks x seven arms | Pointer/status/scope/authorization tests before mocked capture access | Canonical real comparison |
| D3 | M4 | `k>=1` only; k0 diagnostic; protocol strata; origin/no-promotion labels | Hand-calculated mixed-k/protocol/origin scorer fixture | All comparative metrics |
| D4 | M3 | Persist complete estimate evidence; abort unexpected failures | Evidence reconstruction, dtype/shape/hash round trip, fault injection | Canonical real run; optional diagnostic if retained |
| D5 | M3, M4 | Run production and Ahmed on identical cube/source/run and reject external comparator | Same-run key/hash oracle and deliberate mismatch tests | Production-versus-Ahmed results |
| D6 | M1, M5 | Successor-only Figure 8 bundle; correct title/status; reopen and verify hashes | Byte mutation and status/title tests | Figure 8 successor |
| D7 | M0, M1 | Immutable profile register; exact magnitude formula; separate normalization/row/suppression identities | Literal full-array reference and ambiguity-profile controls | Figure 8 and synthetic controls; Layer B arms rerun under declared identity |
| D8 | M4 | Compute observed coverage and reconcile invalidity reasons | Hand-counted valid/invalid fixture | Coverage summaries |

D1-D8 are all covered.

## 7. Remaining scientific ambiguities

These ambiguities remain but do not block implementation.

| Ambiguity | Non-post-hoc handling |
|---|---|
| Fixed H versus candidate-dependent rows | Separate immutable Layer A identities; candidate-dependent profiles cannot enter the six Layer B arms. |
| \(H+1\) versus H normalization | `matrix_eta` and `non_dc_mean` are separately labeled; selection equivalence is tested only within fixed H. |
| Complex-spectrum `argmax` | Magnitude-sum functional is explicitly fixed and tested; no alternative is selected from results. |
| Eq. 26 versus prose suppression | Separate profiles are emitted with equal status; no best suppression interpretation. |
| Figure-visible unsuppressed behavior | Retained as its own reconstruction profile, not used to invalidate the two textual suppression profiles. |
| Unknown Figure 8 parameters | Declared assumptions remain fixed; failure is reported rather than tuned away. |
| Prose and unsuppressed real-domain duplication | Both arms remain for source completeness, but the dependency label prevents counting them as independent corroboration. |
| Approximate frame-zero origin | Retained as an evaluation limitation with exploratory/no-promotion labels; no time-shift optimization. |

## 8. Plan-review verdict

**READY WITH MINOR CHANGES — APPROVED FOR IMPLEMENTATION**

The reviewer found no remaining scientific or owner-decision blocker. The following minor changes were required and are incorporated into this approved document:

1. Defined magnitude accumulation exactly as \(\sum_h|S(hq)|/\eta\), not \(|\sum_hS(hq)|/\eta\).
2. Defined exact fixed-H and candidate-dependent eta values and limited selection-equivalence claims to within-H comparisons.
3. Defined the manifest as the transitive scientific dependency closure.
4. Made the minimum evidence payload explicit.
5. Added exact lock-use and cardinality acceptance checks.
6. Strengthened mutation tests beyond selected-bin outcomes.
7. Moved the optional 14-bin diagnostic after canonical scoring.
8. Fixed the intended CLI command interface before implementation.

These refinements require no new owner decision and do not change Ahmed HA.

## 9. Implementation handoff

For the future `python_expert`:

> Implement only the approved M1-M4 Ahmed HA corrections. Preserve the recovered two-lock/seven-arm canonical identity, exact window universe, Layer A/Layer B frequency mappings, H=3/5, strict harmonic support, and the approved profile register. Add readable literal/reference mathematical tests, complete non-object evidence, a simple transitive source manifest, fail-closed scientific errors, same-cube paired production/Ahmed execution, and compliant `k>=1` stratified scoring. Do not create additional real-data arms for `matrix_eta` or candidate-dependent rows; those are Layer A audits. Do not access Masimo during radar processing, select any interpretation from error, run official experiments, overwrite historical artifacts, or expand the work into a general infrastructure framework.

Implementation must begin in a separate task. This plan document does not itself authorize running the corrected real-data experiment before M1-M4 implementation, review, and M5 prerequisites are complete.
