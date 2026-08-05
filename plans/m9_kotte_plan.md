# M9 — Kotte joint-Doppler: first real-data evaluation — plan

> Status: revised after nine review passes (all 2026-08-05, `plans/m9_comments_plan.md`). Every
> comment in every pass was verified against the paper and the code before acting; all verified
> items are applied. One fifth-pass nitpick was rebutted against the paper's page render. Four
> items were decided by the user (2026-08-05): the reference-alignment amendment, the
> subject-weighted Stage-B rule and its exact natural-only formula, and the headline rewording.
> Elaborates `plans/implementation_plan.md:468-509` (M9).

## Context

The paper's headline — **reworded by user decision 2026-08-05 (pass 9)** — is *first real-data
**evaluation** of two simulation-only published methods under one common comparator, with
coverage reported* ("validation" is reserved for exact-origin data; `JOURNAL_PAPER.md` /
`THIRD_CHAPTER.md` are reworded at session close). M8 (Ahmed) is done: negative at every bin,
mechanism identified. M9 is the other half: **Kotte, Ahmed, Alouini, Al-Naffouri, "Joint
Estimation of Single Target's High Amplitude Difference Doppler Frequencies in FMCW Radar",
IEEE T-RS vol. 2, 2024, DOI 10.1109/TRS.2024.3352189**. Zero M9 code exists (verified).
Equation extraction:
`literature/ref_papers/joint_estimation_high_amplitude_doppler/joint_estimation_high_amplitude_doppler.md`
(§III :622-1310, Algorithm 1 :1251-1303, §IV :1316-1541).

Ladder (`plans/implementation_plan.md:468-509`): **control 1** (paper-faithful 20-RX
reproduction, synthetic) → **control 2** (4-RX ablation, declared before the run) → **Stage A**
(joint f_breath/f_heart on range bins, NO DOA, 8 captures) → **Stage B** (DOA — go/no-go note
only).

**User decisions 2026-08-05:**
1. Step 1a reproduces **Figs 5 + 7 + 8** with FFT + MUSIC; Fig 9 Monte Carlo and Yule-AR are
   declared descopes.
2. **Reference-alignment authorization:** `notes/analysis_prespec.md` is amended
   **prospectively, before any M9 scoring run** (§6 cross-reviewed), extending the identical
   approximate-origin `exploratory_non_frozen` treatment to M9 (the existing exception at
   :559-565 names M8 only — verified; the 8 captures have no persisted `frame0_epoch`). Every
   M9 scored row, table, metric, and decision carries the approximate-origin +
   no-promotion/no-final-agreement-claim taint; the captures stay exploratory, never headline
   evidence. The amendment also fixes the "registered in" vocabulary leftover in that
   paragraph. **The scorer enforces this** (preflight below); radar-only work does not wait on
   it.
3. **Stage-B aggregation: subject-weighted, natural-only, pooled within subject** (exact
   formula in Stage A below).
4. **Headline wording:** "first real-data **evaluation**" (drop "validation").

**Review decisions (nine passes, verified, applied)** — the load-bearing deltas:
- Range/DOA OUT of M9; direct `Y_t` in all controls (fast-time setup internally inconsistent,
  :1336-1338; Fig 4 not reproduced).
- Gate primary objective = **Algorithm 1's literal selection line**; eq-26 surface (θ known) is
  the analysis-consistency check; the **truth table** in Step 1a governs verdicts.
- Stage-A primary = closest Kotte-form 4-RX adaptation per CPI, **2D-medoid**-reported; pooled
  and mean-surface are labelled variants of ours; **three real-data arms**, two gate-only
  diagnostics with a declared promotion rule.
- ONE Stage-A objective: **`loaded_capon_power` = `1ᵀH_loaded⁻¹1` = `wᴴR_loaded w`**.
- Signed grid; alias collapse with a deterministic representative; one defined diagnostic
  **`pair_margin_db`**.
- Noise/channel: signal + noise first, per-RX gain/phase on the **whole column**; configured
  transfer SNR = **`snr_z_dynamic_db`** on the mean-removed dynamic component.
- **`gate_status == "passed"` required for publication** and verified by sweep and scorer.
- The transfer bundle **links both controls**; both control subcommands and every
  evidence-producing run **require a clean tree** (pass 9): dirty-tree runs are permitted only
  as explicitly labelled non-gating smoke whose outputs can never gate, score, or decide.
- **Gate manifest = runtime sources + external config inputs**; tests and this plan are
  non-gating declaration hashes. **Config freshness is section-scoped** (pass 9): control runs
  persist the whole-config hash for reproduction AND the resolved `controls`-section hash; the
  transfer command requires the **current `controls` section hash** to match each accepted
  control run (the oracle later edits `transfer.gate_criteria` in the same file, which must not
  stale the controls).
- **One owner for the production comparator + lock map** (pass 9): a single command,
  `scripts/m9_production_comparator.py`, produces from one clean commit/config both the
  production all-bins table and the **sole** `current_production_rerun_lock` map artifact
  (`results/m9/production_comparator/<stamp>/`, with an output-hash map). The M9 sweep does
  **not** derive locks (it evaluates every bin and does not need them); the scorer requires the
  comparator's run ID, commit, table hash, and lock-map hash, verified against its output-hash
  map. (The prior production run `20260804T131040Z` records `git_tree_clean: false`,
  `config_hash: null` — verified — and is ineligible.)
- **The capture cohort is runner input, not estimator config** (pass 9): `KotteJointDopplerConfig`
  is DSP-only; the YAML capture manifest is parsed by the sweep/scorer runners and hashed as a
  separate `input_manifest_hash`, excluded from `suite_config_hash` and per-arm
  `run_config_hash` — the identical algorithm never changes identity because a capture or
  filename changed. Runners open **only** the listed paths; "reject unlisted" means rejecting a
  CLI request outside the manifest, not scanning `results/live_demo/`.
- The scorer is **radar-free**, **enforces the amendment** (preflight: committed amendment
  marker/version present in a clean-tree `notes/analysis_prespec.md`, file recorded + hashed;
  refusal otherwise — tested), and persists per capture the numeric epoch used,
  `origin_source="start_wall_utc"`, the timezone-normalized value, and
  `origin_is_approximate=true`. Reference fields remain the integer `Timestamp` and
  `Beats / min` (CLAUDE.md §9).
- **Paired wrapper hardened** (dup-key and mismatched-reference rejection — the helper's dict
  build silently overwrites and mixes reference sources, verified); paired universe =
  `(capture_id, lock_estimand_id, k)`, **k ≥ 1**.
- **Numerical rank**: `rank = #{λ_i > rank_rtol·λ_max}`; finite `λ_max ≤ 0` → rank 0 →
  covariance failure path; nonfinite rejected; scale-change test.
- **FFT/MUSIC comparators pinned as declared reproduction assumptions** (pass 9; the paper is
  qualitative): specification in Step 1a.
- Dependency claims scoped (DSP/core numpy-only; scorer uses pandas/PyYAML/Masimo parser;
  oracle uses `scipy.special.jv`). Live-demo raw mirrors are referenced **in place** as
  noncanonical regression fixtures — hashed, never edited, never implied promoted into
  `data/raw/`.

## The method, pinned to the paper

- Eq (23): `Y_t(κ) ∈ C^(N_c×n_R)` — rows = slow-time samples, columns = RX channels. Steering
  `a(f)_n = exp(j2πf·n·T_PRI)`.
- Eq (24): `min_w wᴴR_t w  s.t.  wᴴ[a(f1) a(f2)] = [1 1]`. Eq (25): `w = R_t⁻¹A₂H⁻¹[1;1]`,
  `H = A₂ᴴR_t⁻¹A₂`.
- **Algorithm 1's selection line** (:1267-1303): `f̂_d = argmax wᴴE{Y_tY_tᴴ}w` — the gate's
  primary objective (sample covariance, unloaded, 20 RX).
- Eq (26): `β̂` estimates the **joint** amplitude β = β1+β2; Cases 1–4: `β̂ ≈ β1+β2` iff both
  swept frequencies are true — analysis, not the selection line.
- **Identities (review-verified):** `wᴴR w = 1ᵀH⁻¹1`; unloaded additionally
  `‖wᴴY‖²/M = 1ᵀH⁻¹1`; loaded: `1ᵀH_loaded⁻¹1 = wᴴR_sample w + δ̄‖w‖²`. Corollaries:
  (i) `R → cR` argmax-invariant under trace-relative loading (divisor via direct unit test);
  (ii) exact invariance under whole-column per-RX diagonal unitaries; (iii) under stated
  assumptions, whole-column gain imbalance is in expectation a pure scalar (finite-sample
  sensitivity = the **algebraic non-invariance counterexample** unit test).
- **The method's own failure mode:** β2 ≈ −β1 collapses the true peak — P5 + control-1 audit;
  the multi-CPI discrimination table pins how the estimator forms differ.
- §III-B (:809-816): per-row mean removal is in the paper, but exact per-channel removal makes
  the sample covariance **exactly singular** (`R_t·1 = 0`).
- §IV: 24 GHz, 1 TX / **20 RX** λ/2 ULA, **T_PRI = 50 ms**, **N_c = 16**, SNR 0 dB (two-line
  signal-to-noise power ratio in `Y_t`). Fig 8: target **3 m**/−30° (caption and prose agree —
  verified against the page-09 render), (2, 1) Hz, β2 ∈ {β1, β1/2, β1/10}. Fig 7 row 2:
  (1.5, 1) Hz, Δ=0.5 < 1/CPI=1.25 Hz. Disambiguation prior licenses **ordering**, not sign.

## Established mechanics (verified 2026-08-05)

- **Hardware match:** `scripts/live_demo_config.yaml` — 77.0 GHz start, 70.006 MHz/µs, ADC
  start 5 µs, 256 samples at 5.209 Msps → **effective carrier ≈ 79.07 GHz, λ_eff ≈ 3.79 mm**
  (config-derived; hash + resolved values in the gate bundle). Frame period 50 ms = the paper's
  T_PRI; 32 chirps/frame; 4 RX. Slow time = frame axis; chirps coherently averaged per
  (frame, RX); RX kept separate.
- **Reference-alignment authorization:** the amendment (user decision 2) is a **blocking
  prerequisite for scoring**, enforced by the scorer's preflight. Radar-only stages are
  independent of it.
- **The rank landmine:** at n_R=4, rank(R_t) ≤ 4 < N_c=16. Primary response: RX-only snapshots
  + trace-relative loading per CPI. Pooling = full rank but a different (incoherent) model —
  ours. **Rank rule:** `rank = #{λ_i > rank_rtol·λ_max}` (`rank_rtol` in config); finite
  `λ_max ≤ 0` → rank 0 → covariance failure path; nonfinite rejected; eigenvalues + rank
  persisted; identical rule everywhere; scale-change test.
- **CPI tail + detrend order (pinned):** `extract 600 → retain first n_cpis·N_c (592 at
  N_c=16) → subtract the per-RX mean over the retained support → split into CPIs`
  (`mean_removal_scope: "retained_support"`); a test asserts the discarded tail cannot affect
  any result; `frame_start_used`/`frame_end_used` (half-open) persisted.
- **Partial-CPI semantics:** `min_valid_cpi_fraction` validated **exactly 1.0**; any invalid
  CPI → `dsp_failed`/`invalid_cpi`; per-CPI validity/cause arrays (integer codebook,
  `allow_pickle=False`) + failing CPI index + partial diagnostics persisted.
- **Transfer physics:** `exp(j(4π/λ)d(t))`; breathing index ≈6.6 rad → **two-sided Bessel
  comb** through the heart band. Transfer question: f_h or a breathing harmonic? Band-edge
  saturation instrumented.
- **Suite contract** (`src/m4/estimator_suite.py:90-163`): optional M8 fields None;
  `__call__(frames, locked_bin, fs)`; `validate_returned_arms`; `validate_arm_specs` rejects
  duplicate `(estimator_id, run_config_hash)` → per-arm hashes.
- **Front end:** `extract_rx_slow_time` mirrors `extract_chest_phase`
  (`src/respiration.py:104-168`), averaging only chirps → `Z ∈ C^(N_frames×4)`.
- **Numerics:** `solve` on the loaded matrix only; `rcond(H)` mask below
  `condition_mask_threshold`; masked fraction persisted; all-masked → `dsp_failed`; control
  truth pairs stay admissible (tested); paper-control grid **half-open** `[−10, 10)` Hz.
- **Bands:** in the config (0.10–0.50 / 0.80–2.00 Hz); tests pin equality with M8's domain and
  `live_demo_config.yaml`.
- **Dependencies (scoped):** DSP/core numerics numpy-only; oracle `scipy.special.jv`;
  sweep/scorer pandas + PyYAML + the existing Masimo parser.
- **Capture cohort = runner input:** `stage_a.captures` YAML table — exact directory name,
  subject ID (A–D per `notes/capture_inventory.md`), Masimo CSV filename, **protocol role**
  (natural / paced / stepped), data role. Parsed by the runners (never by the suite); resolved
  list hashed as `input_manifest_hash` (outside all estimator hashes); runners open only listed
  paths; missing/duplicate entries and out-of-manifest CLI requests rejected. The live-demo raw
  mirrors are referenced in place as noncanonical fixtures — hashed, never edited, not promoted.
- **Clean-tree rule (evidence vs smoke):** official control verdicts, the transfer gate, the
  full sweep, the production-comparator run, and the scorer **all refuse a dirty tree**.
  Dirty-tree execution is allowed only via an explicit `--smoke` mode whose outputs are written
  under `*_smoke/`, labelled non-gating, and unusable as evidence, parent, comparator, or
  decision input.
- **Provenance (CLAUDE.md §3.1):**
  - *Controls:* clean tree required; run_meta = resolved config + **whole-file hash AND
    `controls`-section hash**, git commit, hashes of the short runtime list
    (`paper_control.py`, controls CLI, `kotte_core.py`, config, `environment.yml`), PDF hash,
    **`case_id → (Y_t hash, seed)` map** (per-case RNG seeded from root seed + stable case id;
    reorder test), output hashes.
  - *Gate bundle:* gating manifest = runtime sources + external config inputs
    (`src/m9/kotte_core.py`, `src/m9/kotte_gate.py`, `scripts/m9_step1b_gate_prediction.py`,
    `scripts/m9_kotte_transfer.py`, `src/m4/bundle.py`, `src/m8/ahmed_provenance.py`,
    `experiments/m9_kotte/config.yaml`, `scripts/live_demo_config.yaml`, `environment.yml`);
    tests + this plan as non-gating declaration hashes; live-demo-config hash + resolved
    hardware values persisted. **Publication requires complete + eligible +
    `gate_status="passed"`.** **Control parent links:** both control run dirs verified
    (manifests + current `controls`-section hash match) and their IDs/verdicts/hashes
    persisted; control 1 must be `behaviorally_reproduced`, control 2 complete.
  - *Production comparator (`scripts/m9_production_comparator.py`):* clean tree required; one
    commit/config produces the production all-bins table AND the sole lock-map artifact;
    run_meta = run ID, commit, config hashes, per-capture input hashes, output-hash map.
  - *Sweep:* clean tree required; startup parent validation (verifier;
    complete/eligible/**passed**/stage; gating-manifest hash comparison). run_meta: parent
    digest, git commit + state, own env attestation + conda-explicit hash, resolved `stage_a`
    hash + `input_manifest_hash`, per-capture `adc_stream.bin` + `run_metadata.json` hashes,
    output hashes. **No lock derivation, no Masimo.**
  - *Scorer:* clean tree required; radar-free; **amendment preflight** (marker/version in a
    committed `notes/analysis_prespec.md`, hashed; refusal otherwise); repeats parent
    verification incl. `gate_status`; asserts digest agreement with the sweep run; requires
    the comparator run's ID/commit/table hash/lock-map hash verified against its output-hash
    map; records git commit + state + own env attestation, hashes of the sweep `run_meta.json`
    itself, both window tables, the lock map, every Masimo CSV + capture metadata file, the
    `stage_b_decision` section, its outputs; persists per-capture origin fields (epoch used,
    `origin_source`, tz-normalized value, `origin_is_approximate=true`).
- **Retired machinery, do not rebuild:** the authorization YAML + `_SCOPED_TREES` chain.
- **Vocabulary ban** (CLAUDE.md §4) and **HANDOFF §2.2** bind throughout; all scored outputs
  carry the approximate-origin taint.

## File layout — new `src/m9/`, sibling scripts, M8 files NOT edited (13 Python files + 1 config)

| File | Role |
|---|---|
| `src/m9/kotte_core.py` | DSP maths + extraction + suite (DSP-only config; no capture knowledge): extraction, steering, covariances, loaded `solve`, rank rule, masked surfaces, alias collapse + `pair_margin_db`, medoid, `joint_beta_at` (controls only), `KotteArm`/`KotteJointDopplerConfig`, `KotteEstimatorSuite` |
| `src/m9/paper_control.py` | controls 1+2: direct `Y_t`, **pinned FFT + MUSIC comparators**, verdict truth table + comparator matrix, 20→4 RX ablation (fixed endpoints), clean-tree enforcement, control run_meta |
| `src/m9/kotte_gate.py` | Step-1b generator (`snr_z_dynamic_db`, whole-column convention, phase pairs, per-case seeds) + P1–P6 gate via `transfer.gate_criteria` + transfer verdicts + robustness report |
| `scripts/m9_step1b_gate_prediction.py` | **independent oracle, before `kotte_gate.py`** — direct eq 25/26 + pinned Bessel model (`scipy.special.jv`) |
| `scripts/m9_kotte_transfer.py` | `synthetic` → control-linked, gate-status-checked bundle → `LATEST.json` |
| `scripts/m9_production_comparator.py` | **the single owner of the production comparator + lock map** — clean commit, one config, output-hash map → `results/m9/production_comparator/<stamp>/` |
| `scripts/m9_kotte_all_bins.py` | Stage-A sweep — capture-manifest runner input, evidence NPZ, full provenance, parent verification; no locks, no Masimo |
| `scripts/m9_kotte_score.py` | scorer — amendment preflight, local scoring functions, hardened paired wrapper, natural-only subject-weighted `stage_b_decision.json`, lineage binding, taint + origin stamping |
| `figures/reproduce_kotte_controls.py` | controls CLI (CLAUDE.md §3.4), `paper` / `ablation`, `--smoke` for dirty-tree scratch |
| `experiments/m9_kotte/config.yaml` | the one M9 config — `controls` (Table-I, comparator assumptions, descopes, audits, endpoints), `transfer` (scenarios, noise + `snr_tolerance_db`, phase pairs, `gate_criteria`, `robustness_seeds`, `diagnostic_arms`), `stage_a` (bands, grid, thresholds, `rank_rtol`, 3 arms, tail policy, schema version, **capture manifest**), `stage_b_decision` (natural-only subject-weighted rule + floors + rationale) |
| `notes/approach.md` §"Kotte" | research note (signal model, selection line, identities, rank rule, Bessel analysis, aggregation contract, ambiguity list) |
| this file | the M9 authority doc; frozen P1–P6 predictions + gate criteria appended at the commit checkpoint |
| `tests/test_m9_kotte_core.py`, `…_gate.py`, `…_all_bins.py`, `…_score.py` | targeted tests (scorer tests in their own file — module boundary) |

Plus the **prospective amendment to `notes/analysis_prespec.md`** (§6 cross-reviewed, committed
before any scoring).

Results: `results/m9/{step1b, production_comparator, kotte_all_bins, kotte_score}/`,
`results/m9_kotte_controls/<run_id>/` (gitignored; `*_smoke/` for scratch).

## Step 1a — paper-faithful reproduction (20 RX, N_c = 16, direct Y_t)

`Y_t` per eq (23) per target (θ0 known); range/DOA descoped; mean removal OFF; whole-column
convention; clean tree required for official verdicts (`--smoke` for scratch).

**Objectives and the verdict truth table** — primary = Algorithm 1's selection `1ᵀH⁻¹1`;
secondary = eq-26 `|β̂|²` (known θ0); each independently vs truth (one grid step, symmetric swap
allowed):

| primary | secondary | verdict |
|---|---|---|
| miss | miss | `not_reproduced_under_declared_assumptions` |
| miss | hit | `not_reproduced_under_declared_assumptions` |
| hit | miss | `ambiguous_reproduction` (cannot pass; cannot be upgraded) |
| hit | hit | evaluate remaining checks; differing cells recorded as diagnostic |

**Comparators — pinned as declared reproduction assumptions (the paper is qualitative):**
- **FFT:** mean per-RX periodogram — each `Y_t` column's periodogram (no extra window beyond
  the raw data; mean removal off), zero-padded to a declared `n_fft_comparator`, incoherently
  averaged over the 20 RX; amplitude-normalized to its maximum (declared).
- **MUSIC:** covariance `Y_tY_tᴴ/n_R`, **primary model order p = 2** (the p=4 audit varies
  this), no loading, evaluated on the same frequency grid, pseudospectrum
  `1/(aᴴE_nE_nᴴa)` normalized to its maximum (declared).
- **One peak rule for every row of R1/R3:** the declared weak-peak rule (top-2 merged peak
  within ±0.25 Hz of the weak truth; merge radius per config).

- **R1 (gating): Fig 8 amplitude ladder** — 3 m/−30°, SNR 0 dB, (2, 1) Hz:

  | ratio | FFT | MUSIC | proposed |
  |---|---|---|---|
  | β2 = β1 | detects both | detects both | peak within one grid step |
  | β2 = β1/2 | detects both | fails | peak within one grid step |
  | β2 = β1/10 | fails | fails | peak within one grid step |

- **R2: Fig 5 surfaces** — (−1,−2), (−1,4), (1,2.5) Hz targets, per-target argmax within
  tolerance.
- **R3: Fig 7 row 2** — (1.5, 1) Hz: proposed resolves both; FFT and MUSIC fail. Row 1 free.
- **Descopes in config:** Fig 9 MC; Yule-AR; range/DOA.

Primary config values: T_PRI=50e-3, N_c=16, n_R=20, Table I, `Y_t`-domain AWGN per the paper's
two-line power ratio, per-case deterministic seeds, grid **[−10, 10)** Hz step 0.05 + rcond
mask. **Audits (one field; cannot upgrade):** ensemble covariance (Cases-1–4 oracle); grid
0.02/0.10; mean removal on + tiny loading; MUSIC p=4; merge radius 0.15/0.40; Fig 5 at 10 dB;
**cancellation** β2 = −β1 (predicted failure).

## Control 2 — the 4-RX ablation (paper geometry, synthetic; `ablation` subcommand)

Clean tree required; declared in the committed script:
- **Fixed endpoints:** 20 RX and one fixed nested 4-RX subset (first four elements), same
  generated realization; δ ∈ {1e-2, 1e-4}, both run; no curve claim.
- Rank per the declared rule (predicted exactly 4 — noise supplies column rank); unloaded
  inverse never formed.
- Whole-column phase offsets cancel exactly; gain imbalance is not an ablation prediction.
- Slow-time = chirp index; mean removal off; pooling impossible here (recorded).
- Pass/fail: peak error ≤ 1 grid step per Fig 8 ratio / Fig 5 target at both endpoints.
- **Role fixed in advance:** cannot relabel arms; equal-amplitude failure ⇒ primary carried as
  **expected-negative**; completion (not outcome) blocks the transfer bundle.

## Step 1b — transfer gate (synthetic vitals cube, production geometry, λ_eff from config)

**Generator** (`KotteSyntheticConfig`: fs=20, n_frames=600, 32 chirps, 4 RX, bin 7, carrier
≈79.07 GHz from config, per-RX clutter, per-RX gain+phase tables, explicit `(φ_b, φ_h)` pairs,
per-case seeds). Signal + noise first; channel table on the whole column; signal-only
propagation-phase variant = named audit, statistical invariance.

**`snr_z_dynamic_db`:** clean moving-target component and injected noise retained separately,
both through the same front end → `Z_sig`, `Z_noise`; configured SNR on the dynamic component
(`Z − mean_time(Z)` over the declared 592-frame support, same projection both):
`10log10(mean|Z_sig_dyn|²/mean|Z_noise_dyn|²)` over frames × RX. ADC variance back-solved;
`snr_z_raw_db` persisted separately; per-arm post-tail realized SNRs (592/576); declared vs
realized within `snr_tolerance_db` (formula unit-tested).

**`transfer.gate_criteria`** = numeric config table (per item: quantity, rule, tolerance,
single declared seed; all-rows-pass), filled from the oracle and committed at the checkpoint.
Deterministic gate; `robustness_seeds` multi-seed report non-gating.

Scenarios (each at `(0,0)`, `(0,π/2)`, `(0,π)`, `(1.0,2.5)` rad; the oracle identifies the
cancellation-critical configuration):
- **S1 two-rate small-modulation regime** (±f_b AND ±f_h sidebands — not two cisoids):
  A_b=0.05 mm, A_h=0.025 mm; f_b=0.30, f_h=1.35 Hz.
- **S2 deep modulation + decoy** (headline): A_b=2.0 mm (index ≈6.6), A_h=0.3 mm; harmonics at
  0.9/1.2/1.5/1.8 Hz; f_h=1.35 Hz between k=4 and k=5.
- **S3 collision:** f_h = 4·f_b = 1.20 Hz — unresolvable by construction, recorded.

**Oracle first, commit checkpoint:** oracle → append predictions + cancellation-critical
phases + filled `gate_criteria` to this plan and the config → **commit** → implement/run the
gate. **Bessel model pinned** (tail-mass truncation; coincident lines coherently combined;
alias folding; `scipy.special.jv`). The oracle also produces the **multi-CPI discrimination
table**.

**Predictions (gate = P1–P6 under the criteria table):**
- **P1 clutter/DC:** removal off + clutter → breath argmax at the 0.10 Hz floor (`band_edge`);
  on → S1 recovers f_b.
- **P2 rank:** noise on → per-CPI rank exactly 4; **noise-off audit → rank 1**; unloaded
  raises; loaded proceeds.
- **P3 two-rate recovery:** S1, primary arm → within one grid step at every phase pair (or the
  oracle's predicted exceptions).
- **P4 deep-modulation decoy:** S2 — implementation matches the oracle per arm (incl.
  diagnostic arms); S3 degenerate case recorded.
- **P5 phase cancellation (predicted failure):** single-CPI collapse/decoy takeover; medoid /
  mean-surface / pooled partial immunity — the discrimination table reproduced.
- **P6 invariances:** whole-column phase offsets → exact surface invariance; pooled full rank.

Bundle: gate.json (`gate_status`), metrics, gating manifest + declaration hashes, control
parent links, env attestation, resolved config, evidence.npz, robustness report. Publication
only on complete + eligible + **passed** → `LATEST.json`.

## Stage A — `KotteEstimatorSuite` + all-bins sweep (only after both controls)

DSP values from `stage_a` (suite-facing, DSP-only); the capture manifest is runner input
(`input_manifest_hash`); the sweep records both hashes:

```python
@dataclass(frozen=True)
class KotteArm:
    arm_id: str
    n_c: int
    estimator_form: str       # "cpi_medoid" | "mean_surface" | "pooled"
    loading_delta: float
    # run_config_hash = hash(shared resolved DSP settings + this arm's fields)

@dataclass(frozen=True)
class KotteJointDopplerConfig:      # DSP-ONLY — no captures, subjects, or reference files
    breath_band_hz: tuple[float, float]
    heart_band_hz: tuple[float, float]
    grid_step_bpm: float
    condition_mask_threshold: float
    rank_rtol: float
    arms: tuple[KotteArm, ...]            # EXACTLY the three real-data arms
    min_valid_cpi_fraction: float         # MUST be 1.0 — validated
    mean_removal_scope: str = "retained_support"
    chirp_aggregation: str = "coherent_mean"
    evidence_schema_version: int = 1
```

**Grid — signed:** f1 ∈ ±[0.10, 0.50] Hz, f2 ∈ ±[0.80, 2.00] Hz at 0.5 bpm; |f̂| reported in
bpm; alias collapse by `(|f1|, |f2|)` with the deterministic tie-break; **`pair_margin_db`**.

**Objective: `loaded_capon_power`.** No amplitude diagnostics in Stage A.

**Estimator forms:** `cpi_medoid` (per-CPI Kotte-form; `medoid_of_cpi_estimates`, L1 in bpm,
tie-break lowest CPI index; window diagnostics = the medoid CPI's; all CPIs valid);
`mean_surface` *(gate-only; raw surface mean over the fixed CPI set under the common mask)*;
`pooled` (one covariance; δ retained for uniformity — not required for invertibility, still
perturbs the objective).

**Arms — `stage_a.arms`:** `kotte_cpi_medoid_nc16_dl1em2` (**primary; Stage-B binds to this
arm alone**), `kotte_cpi_medoid_nc16_dl1em4`, `kotte_pooled_nc16_dl1em2`.
**`transfer.diagnostic_arms` (gate only):** `kotte_meansurf_nc16_dl1em2`,
`kotte_cpi_medoid_nc32_dl1em2`. Promotion: pre-results — committed config amendment; post-results
— a separately labelled follow-up run. Non-primary arms are exploratory.

**Native dict per arm:** `br_valid`/`hr_valid`, `br_bpm`, `hr_raw`, `selection_method`,
`rej_reason`, `f_r_hz`, `shared_signal_hash`; evidence dicts (`selected_hz`, `pair_margin_db`,
`masked_fraction_mean`); scalars (`rank_rt`, `n_snapshots`, `n_cpis_expected`, `n_cpis_valid`,
`cpi_iqr_br_bpm`, `cpi_iqr_hr_bpm`, `loading_delta`, `frame_start_used`, `frame_end_used`,
`band_edge_low/high`). Missing concepts are absent keys.

**Evidence NPZ** (per-form schemas; `allow_pickle=False`): all forms — Z, selected pair, raw
signed top-3, masked fractions, frames used, failure reason; `cpi_medoid`/`mean_surface` —
per-CPI pairs + margins + eigenvalues + ranks + validity/cause arrays + failing CPI index
(+ medoid index / common mask + averaged profiles); `pooled` — pooled eigenvalues + rank +
profiles. One complete record per form tested.

**Sweep** — M8 skeleton + manifest-listed paths only + parent verification (incl.
`gate_status`) + provenance + Kotte `ROW_COLUMNS` + evidence writer + run_meta notes (band
comparability; ordering prior; Kotte coverage note; approximate-origin taint). No locks, no
Masimo.

**Production comparator** (`scripts/m9_production_comparator.py`) — the single owner: from one
clean commit/config, the production all-bins table AND the sole
`current_production_rerun_lock` map, with an output-hash map. Prerequisite for scoring.

**Scorer** — radar-free; **amendment preflight** (refuse without the committed amendment;
record + hash the spec; persist per-capture origin fields); three outputs:
1. **The M8-shaped table** (five row families; production at the comparator's lock map;
   `recorded_lock_as_captured` never pooled with it). Local scoring functions; golden-fixture
   equality vs M8's.
2. **The paired comparison** — per arm and vital on `(capture_id, lock_estimand_id, k)`,
  **k ≥ 1**, reference-admitted ∩ both-emitted. Hardened: one row per key per side
  (duplicate-key rejection tested); per-pair identity of reference bounds/value/admission/
  reason (mismatch rejection tested); explicit `reference_reason` construction
  (availability → `insufficient_*`, else spread → `nonstationary_*`); estimator-neutral field
  names; per-subject, per-capture, and **per-protocol-role** paired counts +
  `n_intersection_subjects` persisted; 592-vs-600 support difference recorded.
3. **`stage_b_decision.json`** (beside the scorer outputs; sweep metadata never reopened) —
   **user-decided formula (pass 9): natural-only, pooled within subject, subjects averaged
   equally.** Windows from captures whose manifest protocol role is `natural` form each
   subject's pooled paired BR MAE; subjects are averaged equally; GO iff
   `MAE_kotte_primary ≤ MAE_production + 1.0 bpm` under that aggregation. Paced/stepped
   captures are scored and reported **descriptively only** — excluded from the gate.
   **Evaluability floors:** ≥ 3 subjects each with ≥ 5 natural paired windows AND ≥ 30 natural
   paired windows total (a 30-windows-one-subject case is NO-GO — tested; unequal capture
   lengths tested); a **sensitivity variant** with low-contribution subjects (< 5 windows)
   dropped is persisted alongside. Zero/insufficient → automatic NO-GO. Constants + rationale
   in `stage_b_decision` (hash recorded); the artifact carries the approximate-origin taint.
   BR only — §2.2 forbids an HR-based rule.

**Cost (estimate — to be replaced by the measured one-cell benchmark in the run log):**
≈ 75 distinct surfaces per cell → ≈ 8–20 min locally + decode. No IBEX.

## Test plan (4 files; the full suite must pass — exact counts live in the run log)

- `test_m9_kotte_core.py`: divisor direct test; Cases-1–4 oracle; identities + corollaries
  (gain = algebraic non-invariance counterexample; exact whole-column phase invariance); rank
  rule incl. zero-scale branch + scale-change test; verdict truth table; the full R1 comparator
  matrix incl. the **pinned FFT/MUSIC assumptions**; vectorized == loop; rcond mask; half-open
  grid; alias collapse + tie-break; `pair_margin_db`; medoid determinism; partial-CPI semantics
  + cause arrays + fraction validation; **tail/detrend order** (tail-immutability); mean
  removal → rank ≤ N_c−1 + unloaded refusal; per-arm hash uniqueness; audits cannot upgrade;
  **DSP config contains no capture knowledge** (adding a manifest entry changes no estimator
  hash).
- `test_m9_kotte_gate.py`: generator (noise model; whole-column convention; `snr_z_dynamic_db`
  formula + tolerance; `snr_z_raw_db` distinct; per-arm post-tail SNRs; pinned Bessel model;
  clutter DC; chirp-average exactness; per-case seed derivation — reorder changes no hash);
  P1–P6 via `gate_criteria`; oracle cross-check; multi-CPI discrimination; drift tests
  (transposed/conjugated covariance, absolute loading, dropped constraint column, perturbed
  steering); gate/transfer separation; publish refusal on dirty tree AND
  `gate_status != "passed"`; control-parent enforcement (verdict + completeness + **current
  `controls`-section hash match**; control dirty-tree refusal); gating-manifest completeness;
  stale-parent rejection.
- `test_m9_kotte_all_bins.py`: sweep never reads the reference; **manifest enforcement**
  (listed paths only; out-of-manifest CLI request rejected; duplicates rejected;
  `input_manifest_hash` recorded, excluded from estimator hashes); every cell swept (rows =
  Σ win×14×3; frames-used; shared `shared_signal_hash`); run_meta completeness; **no lock
  derivation and no ADC reopening downstream of the sweep**; sweep clean-tree refusal (official
  mode) + `--smoke` outputs segregated and labelled; parent refusal on failed gate_status; one
  evidence record per form; end-to-end smoke on a synthetic capture.
- `test_m9_kotte_score.py`: **amendment preflight** (missing/uncommitted amendment → refusal;
  spec hashed; origin fields persisted per capture); comparator lineage (run ID/commit/table/
  lock-map hashes verified against the output-hash map; the dirty `20260804T131040Z` shape is
  rejected); golden-fixture scorer equality + constant baseline reads no radar column; paired
  hardening (dup-key; mismatched-reference; key triple; k=0 boundary; `reference_reason` both
  classes × both vitals); **natural-only subject-weighted Stage-B** (protocol-role filtering;
  pooled-within-subject formula on unequal capture lengths; floors incl.
  30-windows-one-subject → NO-GO; sensitivity variant persisted); `stage_b_decision.json`
  beside scorer outputs, sweep metadata byte-identical; scorer radar-freedom + clean-tree
  refusal; taint stamping on every scored artifact.

## Execution order and gates

0. **Decisions + groundwork:** commit the **analysis-spec amendment** (§6 cross-review) and the
   config (capture manifest with protocol roles; `stage_b_decision`); `notes/approach.md`
   Kotte section; headline rewording noted for session close. Cross-model review of this plan:
   **nine passes done 2026-08-05**, applied.
1. **Build → test → commit → run control 1:** implement `kotte_core.py` + the R1 path + core
   tests; a dirty-tree `--smoke` R1 run is permitted as scratch while building; **commit; then
   run the official R1 verdict** (clean tree). **Gate: `behaviorally_reproduced` per the truth
   table + comparator matrix.** NO-GO/`ambiguous_reproduction` → recorded; no real data ever
   touched. Then R2/R3 + audits (same commit-then-run discipline).
2. **4-RX ablation** (fixed endpoints; clean tree) → documented outcome; cannot relabel arms.
3. **Aggregation contract** (forms + medoid + partial-CPI semantics) against the multi-CPI
   oracle table.
4. **Oracle → commit checkpoint → transfer gate → frozen bundle** (controls verified via
   current section hash; `gate_status` enforced) → `LATEST.json`.
5. **Build sweep + scorer + comparator command; one synthetic Stage-A cell through the sweep**
   (`--smoke` while building; outside the gating manifest — no staling).
6. **Radar-only sweep** on the manifest captures (clean tree; parent verified; no Masimo).
7. **Production comparator run** (`m9_production_comparator.py`, clean commit) — the sole
   lock-map + production-table owner.
8. **Scoring + Stage-B decision** (clean tree; amendment preflight; full lineage binding;
   natural-only subject-weighted rule). **§6 cross-review of the full diff** before results are
   treated as final.
9. **Session close:** HISTORY.md append + HANDOFF.md rewrite + headline rewording in
   `JOURNAL_PAPER.md`/`THIRD_CHAPTER.md`.

## Risks, ranked

1. **May not transfer to 4 RX at all** — established on synthetics so a real-data null is
   attributable to the method.
2. **Deep-modulation decoy** — P4 + profiles/top-k/band-edge/evidence identify the mechanism.
3. **Per-CPI noise** — `cpi_iqr_*_bpm` + the pooled arm make it visible.
4. **Loading dominance** — δ-sensitivity arm + ablation endpoints bound it.
5. **Phase-dependent cancellation** — explicit predicted failure (P5 + control-1 audit).
6. **Natural-only Stage-B thinness** — restricting the gate to natural captures shrinks the
   paired universe; the floors make an unevaluable gate an automatic NO-GO rather than a weak
   GO, and the descriptive paced/stepped tables preserve the information.
7. **Aperture vs breath band** — N_c=32 gate-only arm + oracle surfaces bound it.
8. **Variant attribution** — pooled/mean-surface are ours; primary = "closest Kotte-form 4-RX
   adaptation"; paper-faithful claims confined to control 1.
9. **Approximate-origin ceiling** — every M9 agreement number is exploratory and
   non-promotable on these captures; evaluation-grade wording only ("first real-data
   evaluation"); exact-origin data (E/F/G onward) needed for validation-grade claims.
10. **M8 entanglement** — no-touch layout; golden-fixture-guarded local copies.

## Verification

- Full suite passes via `& 'C:\ProgramData\anaconda3\condabin\conda.bat' run -n radar-vitals
  python -m pytest -q` (exact counts in the run log). Never the env's `python.exe` by absolute
  path.
- Controls: official runs on a clean tree → verdict JSON + run_meta (runtime-list hashes,
  whole-file + `controls`-section hashes, per-case seed map, output hashes).
- Step 1b: `m9_kotte_transfer.py synthetic` (control dirs verified) → publication only on
  `gate_status="passed"`; oracle cross-check; `snr_z_dynamic_db` within tolerance.
- Stage A: smoke cell → radar-only sweep (manifest-listed paths only; run_meta complete) →
  comparator run (clean commit; output-hash map) → scorer (amendment preflight; lineage bound;
  taint + origin stamped; `stage_b_decision.json` natural-only subject-weighted; sweep metadata
  untouched).
- No `src/m8/` or `scripts/m8_*` file is modified (git diff check).
