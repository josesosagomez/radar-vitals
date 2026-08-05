# M9 Kotte implementation-plan review — disposition record

Nine review passes, all 2026-08-05, verified against the paper, its extraction, and the existing
code. The first eight passes are dispositioned below. **The ninth-pass comments remain
outstanding.** Applied comments are erased per the review protocol; this file records what was
verified, what was applied, where a rebuttal was later overturned, and the current open findings.

## First pass

All seven "must fix" items, the Fig. 5 blocking question, the arm-config, P5, RX-gain/wavelength
comments, the build order, and all five nitpicks were **confirmed correct and applied**.
Key verifications: the identity `||w^H Y||^2/M = w^H R w = 1^T H^-1 1` was re-derived and holds
exactly (unloaded); per-RX mean removal puts the all-ones vector in the sample covariance's null
space exactly (extraction :809-816); ±10 Hz at 20 Hz sampling are the same steering vector; the
fast-time inconsistency is verbatim at :1336-1338 (128 samples at 42.66 MHz ≈ 3 µs inside a
300 µs pulse; Fig. 4, not Fig. 5, is the range/DOA figure); segment pooling decorrelates the two
components, so the paper's coherent rank-one Cases 1–4 analysis does not apply verbatim to it.

Two first-pass comments were initially retained with partial rebuttals; both are now closed:

- **File-count reduction** — closed by convergence. The second pass conceded the independent
  oracle script and the `figures/` CLI (the two items the rebuttal defended); its further merges
  (suite+config into `kotte_core.py`, the ablation as a controls-runner subcommand, the manifest
  builder inlined into the bundle CLI) were applied. Final layout: 11 Python files + 1 config.
- **Scoring import** — the rebuttal's bands/CSV-writer parts stood and were applied, but its core
  (importing `_score`/`window_references` from `scripts/m8_ahmed_score.py`) was **overturned by
  the second pass and is withdrawn**: that script's imports (:49-59) drag the Masimo parser,
  `diagnose_bin_drift`, `simulate_bin_policy`, and the M8 sweep into the M9 scorer's module
  graph, and the proposed "identity-check the imported functions" test was tautological. The
  applied design: local copies in `m9_kotte_score.py` + a golden-fixture test that imports M8's
  functions at test time only and asserts both scorers produce identical columns and values on a
  shared synthetic fixture. The rebuttal's one surviving claim — that
  `src/m4/estimator_scoring.py` alone cannot reproduce the five row families and hit-band
  columns — remains true and is why the M8-shaped table stays local; that module IS now used for
  the paired production-vs-arm comparison it was built for.

## Second pass

All items **confirmed correct and applied**:

- **Mean-objective CPI integration is not the paper's estimator** — verified: the relative tone
  phase advances `2π(f1−f2)·N_c·T_PRI` per CPI, so surface averaging averages away the `β1β2*`
  cross-term and suppresses the coherent cancellation behavior. Primary is now the per-CPI paper
  estimator with a named `median_of_cpi_estimates` reporting step; per-CPI pairs + dispersion
  persisted; mean-surface integration demoted to a separately named variant of ours; the
  multi-CPI oracle discrimination table (single-CPI vs cpi_median vs mean_surface vs pooled on
  the cancellation configuration) added to the gate.
- **Scoring import** — applied as above (local copies + golden-fixture equality test).
- **Paired "identical cells" comparison** — applied: `src/m4/estimator_scoring.ScoredRow` +
  `paired_partitions` on the reference-admitted ∩ both-emitted universe is now a second scorer
  output and the sole input to the Stage-B rule.
- **No post-hoc "some arm" GO** — applied: the Stage-B rule binds to the primary arm alone,
  declared in the plan before any real data; other arms are exploratory and cannot support a
  method-performance conclusion.
- **File count (14, not 11) + further merges** — applied (layout above).
- **Provenance reuse boundary** — verified: `build_source_manifest()`/`scoped_paths()` read M8's
  module globals with no override parameter (`ahmed_provenance.py:134-149,210-216`). M9 reuses
  only generic git/env helpers and carries its own explicit manifest list inline in the bundle
  CLI.
- **Alias canonicalization before margins** — applied: candidates collapsed by `(|f1|,|f2|)`
  before runner-up/margin; raw signed peaks kept in evidence.
- **Condition mask fully specified** — applied: `rcond(H) = λ_min/λ_max`, masked when below the
  declared threshold, masked fraction persisted, all-masked → `dsp_failed`
  (`all_pairs_masked`), truth-pairs-admissible test.
- **Explicit `(φ_b, φ_h)` pairs** — applied: `(0,0)`, `(0,π/2)`, `(0,π)`, `(1.0, 2.5)` rad, plus
  the oracle-identified cancellation-critical configuration (line phases are `kφ_b+mφ_h`, so
  relative phase alone underdetermines the comb).
- **Control 2 cannot relabel the primary** — applied: label fixed in advance;
  expected-negative marking only; Stage A runs regardless.
- **Corrected build order** — applied (core+R1 → ablation → aggregation contract → oracle+gate →
  one synthetic Stage-A cell incl. paired scoring → bundle plumbing → full sweep).
- **Nitpicks** — applied: test-count phrase removed; "ungameable" replaced with "independently
  implemented consistency check"; plan status line updated.

## Third pass

All items **confirmed correct and applied**:

- **Algorithm 1's selection criterion is the gate's primary objective** — verified: the
  algorithm's final line (:1267-1303) selects `(f_d1, f_d2)` by maximizing `w^H E{Y_t Y_t^H} w`;
  eqs (28)-(32) explain the joint-amplitude behavior but do not replace the selection line.
  Applied: `1^T H^-1 1` primary for R1-R3; the eq-26 surface (theta known) must agree within one
  grid step; disagreement is a named `ambiguous_reproduction` outcome that cannot pass and cannot
  be upgraded.
- **DOA-dependent amplitude diagnostics removed from Stage A** — verified: without theta,
  `beta_joint_abs` reduces to the square root of the objective (the identity), and the
  two-amplitude LS is a 2-vector per snapshot with no defined scalar. Applied: both removed from
  the Stage-A native/CSV schema; `joint_beta_at` is controls-only; post-hoc amplitude analysis
  runs off the persisted Z.
- **Coordinate-wise medians replaced by a 2D medoid** — verified: per-coordinate medians can
  report a pair no CPI selected and leave every window-level diagnostic sourceless. Applied:
  `medoid_of_cpi_estimates` (L1 in bpm over alias-canonicalized per-CPI pairs, tie-break lowest
  CPI index); window diagnostics are the medoid CPI's; per-CPI pairs/margins/ranks/eigenvalues
  persisted; `cpi_iqr_br_bpm` / `cpi_iqr_hr_bpm` split; every CSV scalar a named aggregate.
- **Transfer generator noise model declared** — verified and important: without an independent
  noise term the four RX columns are one temporal signal times per-RX gains, so rank(R_t) = 1,
  not the predicted 4. Applied: circular complex AWGN independent across (frame, chirp, RX,
  ADC), declared `snr_db`, gains multiply signal only, realized post-extraction SNR persisted;
  P2 gains a noise-off rank-1 audit making the dependence explicit.
- **Stage-A contract moved into the experiment config** — applied:
  `experiments/m9_kotte/config.yaml` with `controls` / `transfer` / `stage_a` sections; the sweep
  loads the resolved `stage_a` section and records its hash (CLAUDE.md section 2).
- **Execution-order contradiction fixed** — applied: the frozen bundle + `LATEST.json` now
  complete step 4; the one-cell end-to-end smoke through the sweep is step 5, after the bundle
  exists.
- **Paired scorer output wrapped and pre-validated** — verified: `paired_partitions` exposes
  `ahmed_only` / `ahmed_metrics_on_intersection` / `descriptive_difference_ahmed_minus_production`
  and silently intersects key sets. Applied: estimator-neutral rename before persistence, and the
  scorer asserts production keys == Kotte keys == the expected production-lock universe before
  the call.
- **Stage-B rule made exact** — applied: non-inferiority
  `MAE_kotte_primary <= MAE_production + 1.0 bpm` on the paired BR universe; evaluable only with
  >= 30 paired windows across >= 4 captures; zero or insufficient paired cells is an automatic
  NO-GO.
- **Covariance-divisor drift test removed** — verified: `R -> cR` with trace-relative loading
  leaves `w` and every argmax unchanged, so a wrong divisor cannot flip a behavioral gate.
  Applied: direct divisor unit test; drift tests are now behavior-changing (transposed/conjugated
  covariance, absolute instead of trace-relative loading, dropped constraint column, perturbed
  steering).
- **CPI tail policy declared** — applied: each arm uses the window's first `n_cpis * N_c` frames,
  mean removal over exactly those frames, `frame_start_used`/`frame_end_used` persisted; the
  N_c=32 arm is described as having different temporal support.
- **Bessel oracle computation pinned** — applied: truncation by omitted-coefficient-mass
  tolerance, coherent combination of coincident lines (the S3 commensurate case), folding through
  the 20 Hz alias, `scipy.special.jv`; the "numpy only" claim is now scoped to suite/sweep/scorer.
- **Primary-arm attribution corrected** — applied: "closest Kotte-form 4-RX adaptation, applied
  per CPI"; the paper-faithful claim is confined to control 1.
- **Inline manifest enumerated** — applied: the exact 14-file gating list is in the plan, with a
  test that dirtying any listed file makes the bundle promotion-ineligible.
- **Nitpicks** — applied: cost recomputed with shared per-CPI surfaces (~93 distinct surface
  evaluations per cell, ~10-25 min locally); plan status and this record updated.

## Fourth pass

All items **confirmed correct and applied**:

- **Noise ordering broke P6's exact invariance** — verified: the exact-invariance proof covers
  `Y -> YD` applied to the whole RX column; with noise injected after the channel tables, a
  signal-only phase change moves the signal-noise cross terms in `YY^H` on a finite realization.
  Applied: signal + noise generated first, per-RX gain/phase applied to the whole column; a
  signal-only propagation-phase variant is a named audit checked statistically; the same
  convention is stated for control 2, the oracle, and P6. (Amends pass 3's "gains multiply
  signal only".)
- **SNR domain mismatch** — verified: Hann coherent gain, Hann noise gain, and 32-chirp
  averaging change signal and noise power differently, and a realized finite-noise SNR
  fluctuates. Applied: SNR declared in the extracted `Y_t` domain (matching the paper's
  definition), ADC variance back-solved; `snr_yt_declared_db` / `snr_post_expected_db` /
  `snr_post_realized_db` persisted with a declared tolerance; P3-P6 declared to use noise-free
  oracle expectations with SNR-sized tolerances.
- **Partial-CPI failure semantics** — applied: `min_valid_cpi_fraction: 1.0` in config (any
  invalid CPI -> `dsp_failed` with `rej_reason="invalid_cpi"`); `n_cpis_expected` /
  `n_cpis_valid` persisted; `mean_surface` averages the raw signed-grid surfaces over one fixed
  CPI set under the common admissible mask (intersection), then alias-collapses.
- **Evidence schema per estimator form** — applied: `evidence_schema_version` + per-form
  required keys (`cpi_medoid` has the per-CPI sequence + medoid index; `mean_surface` the common
  mask + averaged-surface profiles; `pooled` the single covariance); missing concepts are absent
  keys, never zeroes or fake indices; one complete record per form is tested.
- **`LATEST.json` pointer insufficient** — applied: sweep startup runs the bundle verifier,
  requires status/eligibility/stage, and compares current enumerated-file hashes against the
  parent manifest; any post-freeze edit (including from the smoke step) invalidates the bundle
  and the synthetic gate is rerun and republished before the full sweep; stale-parent rejection
  is tested with a clean, valid old bundle plus one changed source.
- **Input-data hashes** — applied per CLAUDE.md section 3.1: sweep run_meta hashes each
  capture's `adc_stream.bin` + `run_metadata.json`; the scorer hashes both window tables, every
  Masimo CSV and capture metadata file it reads; emitted CSVs and evidence NPZs are hashed;
  `shared_signal_hash` is recorded as diagnostic, not a substitute.
- **Stage-B constants into config** — applied: `stage_b_decision` section (inequality direction,
  1.0 bpm margin, >=30 paired windows, >=4 captures, vital, arm id, insufficient-data outcome)
  with a recorded rationale; the scorer evaluates it and records its hash.
- **Both objectives must hit truth** — verified: two peaks can agree within one grid step while
  the second is two steps from truth. Applied: each objective is independently checked against
  the true pair for R1-R3; mutual disagreement is diagnostic evidence only.
- **Controls run metadata** — applied: each control run emits `run_meta.json` (resolved config +
  hash, seed, git state, PDF hash, `Y_t` hash, output hashes).
- **`mean_surface` averaging declared** — applied: raw arithmetic mean (power-weighted incoherent
  integration, deliberate), oracle models the same.
- **Fewer real-data arms** — applied: three arms on real data (primary, loading sensitivity,
  pooled); `mean_surface` and `N_c=32` are gate-only synthetic diagnostics, promotable only by a
  documented decision.
- **Commit checkpoint after oracle predictions** — applied: oracle run -> predictions +
  cancellation-critical phase appended to plan and config -> commit -> then gate implementation
  and run.
- **Nitpicks** — applied: `frame_end_used` declared half-open (matches `src/m4/window_grid.py`);
  `objective_norm_diag` removed (no consumer; redundant with the objective up to the loading
  penalty); plan status and this record updated.

## Fifth pass

All "must fix" and "worth considering" items and the first nitpick **confirmed correct and
applied**; the second nitpick is **rebutted** (kept at the end with its rebuttal).

- **Control-1 verdict rule contradicted itself** — verified (the prose contained both "either
  misses -> not reproduced" and "primary hits + secondary misses -> ambiguous"). Applied: a
  complete four-row truth table in the plan — primary miss (either secondary outcome) ->
  `not_reproduced_under_declared_assumptions`; primary hit + secondary miss ->
  `ambiguous_reproduction`; both hit -> evaluate remaining checks, cell disagreement recorded as
  diagnostic.
- **Transfer SNR not operationally defined** — verified: S2's Bessel comb with clutter has no
  unique `(beta1, beta2)` numerator, so the paper's two-line ratio cannot be applied verbatim.
  Applied: the generator retains the clean moving-target component (post-channel, excluding
  clutter and noise) and the injected noise separately, pushes both through the same extraction
  front end, and defines `snr_zt_db = 10 log10(mean|Z_sig|^2 / mean|Z_noise|^2)` (mean over
  frames x RX); ADC variance back-solved from that target; declared vs realized asserted within
  the numeric `snr_tolerance_db` config field; formula unit-tested directly.
- **"SNR-sized tolerances" was not an acceptance rule** — applied: `transfer.gate_criteria` is a
  numeric config table (per gate item: compared quantity, rule, tolerance, single declared
  seed, all-rows-pass aggregation), filled from the oracle's numbers and committed at the
  checkpoint before the gate is implemented; multi-seed robustness is a non-gating report.
- **P6's mandatory gain-induced shift did not follow from the generator** — verified: with the
  whole-column convention, `E[R] = (sum_r |d_r|^2 / M)(s s^H + sigma^2 I)` — a pure scalar of
  the balanced case, argmax-invariant in expectation under trace-relative loading; a noise-free
  oracle cannot predict a required nonzero shift. Applied: gain sensitivity moved to a
  deterministic core unit test on a fixed non-collinear `Y`; P6 gates only the exact
  whole-column phase invariance and the pooled rank.
- **Arm config could not express the two scopes; duplicate run_config_hash** — verified:
  `validate_arm_specs` rejects duplicate `(estimator_id, run_config_hash)` pairs. Applied:
  `stage_a.arms` holds exactly the three real-data arms, `transfer.diagnostic_arms` the two
  gate-only arms (no runner-side filter); per-arm `run_config_hash` = hash(shared resolved
  settings + that arm's settings); the whole-section hash recorded separately in run_meta.
- **Pooled arm's loading contradiction** — applied: delta=1e-2 trace-relative loading retained
  for uniformity, description corrected to "no longer required for invertibility; retained, and
  it still perturbs the objective" — the arm id states what actually ran.
- **Freeze/build order impossible; scorer escaped the frozen set** — verified: a
  promotion-eligible bundle at step 4 could not enumerate sweep/scorer/tests that are first
  built in step 5. Applied (the reviewer's simpler option): a **gate-scoped manifest** — only
  the files that can affect P1-P6 (core, gate, oracle, bundle CLI, config, plan,
  environment.yml, the core and gate test files); sweep and scorer are outside it, hash their
  own source + config in run metadata, both verify the parent bundle, and the scorer asserts it
  names the same parent digest as the sweep run it scores.
- **Partial-CPI behavior not advertised beyond what is defined** — applied:
  `min_valid_cpi_fraction` is validated to be exactly 1.0 for M9; survivor semantics deferred
  until a real need appears.
- **Gate-only-arm promotion loophole closed** — applied: before any Stage-A output is opened, a
  diagnostic arm may join the sweep only via a committed config amendment; after results are
  seen, promotion means a separately labelled follow-up run with a new config/run ID — never a
  retroactive arm in this comparison.
- **"Identical cells" overstated** — applied: the paired universe is the shared key universe
  (reference-admitted, both-emitted `(capture, k)` cells at the production lock); the
  592-versus-600 temporal-support difference is stated in the paired output and the Stage-B
  rationale.
- **Control hashes case-addressable** — applied: control run_meta stores a
  `case_id -> (Y_t hash, seed)` map across targets, ratios, phase cases, and audits.
- **Nitpick (SNR wording)** — applied: "two-line signal-to-noise power ratio in `Y_t`".

- **Nitpick (Fig. 8 range: "prose says 3 m while its caption says 5 m")** — **REBUTTED.** The
  claim was checked against the paper itself: the page-09 render
  (`..._figures/page_renders/page-09.png`) shows Fig. 8's caption reading "for a target placed
  at a distance of 3m and at an angle -30 deg", and the adjacent prose also says 3 m; the
  LaTeX extraction (:1453-1455, :1468-1470) agrees. No 5 m appears in the caption, prose, or
  extraction for Fig. 8. There is no internal inconsistency to record; R1 keeps 3 m with no
  conflict note. (Range does not enter the direct-`Y_t` control either way, as the comment
  itself notes.)

## Sixth pass

All items **confirmed correct and applied**:

- **Transfer SNR counted energy the estimator removes** — verified: at small modulation index
  the extracted echo is dominated by the DC/J0 line, which Stage-A mean removal deletes.
  Applied: the configured SNR is `snr_z_dynamic_db`, defined on the mean-removed dynamic
  component over the declared 592-frame support with the same projection applied to the noise;
  the pre-detrend ratio persists separately as `snr_z_raw_db`; per-arm post-tail realized SNRs
  persisted for the 592- and 576-frame supports.
- **Controls not linked into the parent chain** — applied: the `synthetic` command takes and
  hashes the control-1/control-2 run directories, refuses unless control 1's verdict is
  `behaviorally_reproduced` and control 2 is complete (outcome non-blocking, completion
  blocking), and persists their run IDs/verdicts/hashes in the bundle provenance.
- **Gating manifest under- and over-scoped** — applied: the gating list is runtime sources +
  external config inputs (now including `scripts/live_demo_config.yaml`, `src/m4/bundle.py`,
  `src/m8/ahmed_provenance.py`); tests and the plan are recorded as non-gating declaration
  hashes; the live-demo-config hash and resolved hardware values are persisted in the bundle.
- **Sweep/scorer provenance identified only entry points** — applied: each run records git
  commit, tree state, its own environment attestation + conda-explicit hash, plus config,
  input, and output hashes. No dependency scanner.
- **Scorer did not bind upstream lineage** — applied: the scorer hashes the sweep's
  `run_meta.json` itself and the production run's metadata/manifest + run ID, and verifies
  tables against upstream output-hash maps where they exist.
- **Stage-B output location ambiguous** — applied: a separate `stage_b_decision.json` beside the
  scorer outputs, hashed from the scorer's run metadata; the sweep's metadata is never reopened
  (byte-identity tested).
- **R1 matrix missing the equal-amplitude row** — applied: all three rows pinned (equal: FFT
  pass + MUSIC pass; half: FFT pass + MUSIC fail; tenth: both fail) under one declared peak
  rule.
- **`invalid_cpi` hid the diagnosis** — applied: per-CPI validity and cause arrays (fixed
  integer codebook, `allow_pickle=False`-loadable) + failing CPI index + partial diagnostics up
  to it; the top-level summary reason remains.
- **Objective named precisely** — applied: `loaded_capon_power` = `1^T H_loaded^-1 1` =
  `w^H R_loaded w`, covariance stated; differs from Algorithm 1's sample-covariance power by the
  loading penalty.
- **Unused confidence fields** — applied: `margin`/`hr_margin`/`n_candidates` deleted; one
  defined diagnostic `pair_margin_db` (post-alias-collapse peak vs the best canonical pair >= 1
  bpm away, in dB) kept and tested — decoy competition is the headline mechanism read-out.
- **Robustness seeds declared** — applied: fixed `robustness_seeds` list in config; per-seed
  case hashes persisted; non-gating.
- **Gain corollary scoped** — applied: assumptions stated (shared temporal signal up to RX
  phase, iid equal-variance noise, clutter removed); the fixed-`Y` test renamed "algebraic
  non-invariance counterexample".
- **Nitpicks** — applied: `snr_z_dynamic_db` naming; the paired key is the actual triple
  `(capture_id, lock_estimand_id, k)`.

## Seventh pass

All items **confirmed correct and applied**:

- **A failed gate could be published and accepted as parent** — verified:
  `BundleWriter.publish_latest()` checks only completion + promotion eligibility. Applied:
  `gate_status` lives in the transfer manifest; the M9 CLI refuses publication unless it is
  `passed`; sweep and scorer verify that exact value; tested with a complete,
  promotion-eligible, hash-valid but failed gate (rejected, not published).
- **Scorer could not be radar-free while deriving the lock** — verified: M8's scorer reopens
  `adc_stream.bin` via `current_code_lock` and reruns the warm-up. Applied: the per-capture
  `current_production_rerun_lock` bin map is derived on the radar side (in the sweep run) and
  persisted as a hashed artifact; the scorer consumes and verifies the map and never opens ADC
  (tested); the `recorded_lock_as_captured` estimand is never pooled with it.
- **`reference_reason` construction** — verified: `ScoredRow.disposition` dispatches on the
  `insufficient` prefix. Applied: explicit mapping with availability precedence
  (coverage/availability failure -> `insufficient_*`; else spread/stationarity ->
  `nonstationary_*`), persisted per row, both exclusion classes tested for HR and BR.
- **Four-capture criterion not auditable** — applied: the wrapper computes and persists
  `n_intersection_captures` + per-capture paired counts from the exact key set; the GO rule
  consumes them; a 30-windows-one-capture case is required to produce NO-GO (tested).
- **Window-zero inconsistency** — applied as a declared rule with a reason: the paired/Stage-B
  universe is k >= 1 per `notes/analysis_prespec.md` section 7 (`k=0` is
  `lock_selection_in_sample`); k=0 rows are excluded and counted separately (boundary-tested);
  the M8-shaped table keeps M8's own convention, pinned by the golden fixture.
- **Numerical rank undefined** — applied: `rank = #{lambda_i > rank_rtol * lambda_max}` with
  `rank_rtol` in config; eigenvalues + resulting rank persisted; the same rule in P2, the
  ablation, and tests; scale-change test added.
- **Control-2 curve points** — applied: fixed endpoints only (20 RX and one fixed nested 4-RX
  subset, same generated realization); no shape claim.
- **S1 naming** — applied: "two-rate small-modulation regime"; the oracle/gate recognize both
  signs.
- **Alias-collapse signed representative** — applied: deterministic tie-break (lexicographic
  signed-grid order), persisted convention.

## Eighth pass

All items **confirmed correct and applied**; the two governance items were put to the user and
decided (2026-08-05):

- **Reference scoring not authorized for M9** — verified: `notes/analysis_prespec.md:559-565`
  scopes the approximate-origin `exploratory_non_frozen` exception to M8 by name; the 8 captures
  have no persisted `frame0_epoch`. **User decision: amend the spec** — a prospective amendment
  (CLAUDE.md section 6 cross-reviewed, committed before any M9 scoring run) extends the identical
  treatment to M9; every scored row/table/metric/decision carries the approximate-origin +
  no-promotion/no-final-claim taint; the captures stay exploratory, never headline. The
  amendment also fixes the "registered in" vocabulary leftover in that paragraph. The radar-only
  parts of M9 do not depend on it; scoring is blocked until it is committed.
- **Stage-B MAE had no aggregation unit** — **user decision: subject-weighted** — per-subject
  paired BR MAE, subjects averaged equally (the spec's headline convention); evaluability floor
  restated in subjects: >= 30 paired windows AND >= 3 of 4 subjects represented; per-subject
  contributions and `n_intersection_subjects` persisted (per-capture counts kept as evidence).
- **Production comparator neither current nor reproducible** — verified:
  `results/diagnose/bin_sweep/20260804T131040Z/run_meta.json` records `git_tree_clean: false`
  and `config_hash: null`. Applied: a named comparator run regenerated from a clean commit —
  production estimates AND the `current_production_rerun_lock` map under one code/config
  version, with an output-hash map — as a hard prerequisite immediately before scoring, not an
  optional "where present" check.
- **Paired wrapper permitted silent row corruption** — verified: `paired_partitions()` builds
  dicts (duplicate keys silently overwrite) and takes admission from the production row while
  each method's error uses its own reference fields. Applied: pre-call assertions — exactly one
  production and one arm row per key, and per-pair identity of reference window bounds, value,
  admitted flag, and reason; duplicate-key and mismatched-reference rejection tests added.
- **Control 1 could produce the blocking verdict from a dirty tree** — applied: both control
  subcommands refuse a dirty tree and hash their short explicit runtime source/config list; the
  transfer command verifies that control manifest before accepting the verdict. No dependency
  scanner.
- **Capture cohort not config data** — applied: a plain YAML capture manifest (exact directory
  name, subject ID, Masimo CSV filename, data role) in the one config; sweep and scorer consume
  the same list, reject missing/duplicate/unlisted inputs, and hash the resolved list. No
  suffix globs or first-match CSV picks.
- **Case-addressable seeds** — applied: each case's RNG seeded deterministically from the root
  seed + stable case id; a reorder test asserts no `Y_t` hash changes.
- **Tail/detrend order pinned** — applied: `extract 600 -> retain first n_cpis*n_c -> subtract
  the mean over the retained support -> split into CPIs`; config field renamed
  `mean_removal_scope: "retained_support"`; a test asserts the discarded tail cannot affect any
  result.
- **Rank rule zero-scale branch** — applied: finite `lambda_max <= 0` -> rank 0 -> the existing
  covariance failure path; nonfinite eigenvalues rejected; one rule everywhere.
- **Dependency claim corrected** — applied: numpy-only is scoped to the DSP/core numerics; the
  sweep/scorer's actual dependencies (pandas, PyYAML, the existing Masimo parser) are listed;
  nothing reimplemented around them.
- **Build order adjusted** — applied: decisions + amendment + capture manifest first; core +
  one direct-`Y_t` control path + one synthetic Stage-A cell early; then remaining
  controls/gate; radar-only sweep; comparator regeneration; scoring + Stage-B last, only under
  the committed amendment.
- **Nitpick** — applied: the hard-coded "2155 passed / 5 skipped" baseline removed again; the
  plan requires the full suite to pass and leaves exact counts to the run log.

## Ninth pass

All items **confirmed correct and applied**; two were put to the user and decided (2026-08-05):

- **Two owners for the lock map; comparator step unowned** — verified: the pass-8 revision had
  the sweep emit a lock map while the regenerated comparator would emit another, with no named
  script or output path. Applied (the reviewer's simplest contract): one command,
  `scripts/m9_production_comparator.py`, produces from one clean commit/config both the
  production all-bins table and the sole `current_production_rerun_lock` artifact
  (`results/m9/production_comparator/<stamp>/`, output-hash map); lock derivation removed from
  the M9 sweep (it evaluates every bin and does not need locks); the scorer requires the
  comparator's run ID, commit, table hash, and lock-map hash.
- **FFT/MUSIC comparators underdefined for blocking verdicts** — applied as declared
  reproduction assumptions in the config: FFT = mean per-RX periodogram (no extra window, mean
  removal off, declared zero-padded length, max-normalized); MUSIC = `Y_t Y_t^H/n_R`
  covariance, primary model order p=2 (the p=4 audit now explicitly varies a stated primary),
  no loading, same frequency grid, max-normalized pseudospectrum; one weak-peak rule for every
  R1/R3 row.
- **Capture cohort contaminated arm identity** — verified: `captures` inside
  `KotteJointDopplerConfig` would change estimator hashes when a filename changed and give the
  suite knowledge it must never have. Applied: DSP-only config; the manifest is runner input
  hashed separately as `input_manifest_hash`, excluded from `suite_config_hash` and per-arm
  `run_config_hash`; tested (adding a manifest entry changes no estimator hash).
- **Clean-tree rule vs build order; dirty official runs** — verified: step 1 built files then
  immediately ran a control that refuses dirty trees, while the sweep/scorer merely recorded
  tree state. Applied: smoke-vs-evidence distinction — `--smoke` dirty-tree runs write to
  segregated `*_smoke/` outputs that can never gate, score, or decide; official control
  verdicts run after build->test->commit; the full sweep, comparator run, and scorer all
  refuse a dirty tree.
- **Scorer did not enforce or record the amendment** — applied: a scorer preflight requires the
  committed amendment marker/version in a clean-tree `notes/analysis_prespec.md` (hashed),
  refusing otherwise (missing-amendment refusal test); per capture the numeric epoch used,
  `origin_source="start_wall_utc"`, timezone-normalized value, and
  `origin_is_approximate=true` are persisted; integer `Timestamp` + `Beats / min` restated.
- **"Subject-weighted" underdefined on this cohort** — **user decision: natural-only, pooled
  within subject** — windows from manifest-role `natural` captures pooled per subject into one
  paired BR MAE, subjects averaged equally; paced/stepped captures scored descriptively only;
  floors: >= 3 subjects each with >= 5 natural paired windows AND >= 30 total; a sensitivity
  variant with low-contribution subjects dropped is persisted; protocol role added to the
  contribution table; unequal-capture-length and 30-windows-one-subject tests added.
- **Control-manifest config freshness vs the oracle edit** — verified: the oracle later writes
  `transfer.gate_criteria` into the same YAML, so a whole-file hash check would spuriously
  stale valid controls. Applied: whole-file hash persisted for reproduction AND a resolved
  `controls`-section hash; the transfer command requires the current section hash to match
  each accepted control run.
- **Headline wording** — **user decision: "first real-data evaluation"** (drop "validation";
  reserved for exact-origin data). Applied in the plan; `JOURNAL_PAPER.md`/`THIRD_CHAPTER.md`
  reworded at session close.
- **Per-subject contribution floor** — applied inside the decided formula (>= 5 natural paired
  windows per counted subject + the persisted sensitivity variant).
- **Scorer tests in their own file** — applied: `tests/test_m9_kotte_score.py` (13 Python
  files total).
- **Raw mirrors referenced in place** — applied: noncanonical regression fixtures; hashed,
  never edited, never implied promoted into `data/raw/`.
- **Nitpicks** — applied: runners open only manifest-listed paths (rejection = out-of-manifest
  CLI request, not directory scanning); the 8-20 min cost is labelled an estimate to be
  replaced by the measured one-cell benchmark in the run log.
