# Handoff — resume here

> Read this and `CLAUDE.md` before doing anything. **State verified 2026-08-09.**
> `HISTORY.md` is append-only; this file is the current resume point.

## 1. Project snapshot

This thesis/research project estimates heart rate and breathing rate from a TI IWR1642BOOST +
DCA1000 FMCW radar for a seated subject at roughly 0.8–1.4 m. The Masimo MightySat reference uses
integer Unix `Timestamp`; `Beats / min` is HR truth and `Breaths / min` is the BR reference. The
existing eight captures have approximate time origin and low within-session HR dynamic range, so
their agreement outputs are exploratory and cannot support a final HR agreement claim.

The production estimator, Ahmed M8 evaluation, and Kotte M9 evaluation are separate. Do not change
Ahmed or the project's estimator while working on Kotte evidence.

## 2. Current state

Active branch: **`vital_signs_kotte_v12`**. Current verified tip: **`46cec82`**. The load-bearing
M9 commits are:

- `3ac060e` — reviewed Kotte core/config/radar path and tests;
- `99c22d0` — reviewed radar-free scorer and tests;
- `22ed3b6` — established Masimo duplicate normalization before post-parser duplicate refusal;
- `46cec82` — reviewed paper controls and synthetic-transfer runner.

| Track | Status |
|---|---|
| M8 Ahmed | corrected exploratory evaluation complete; final report at `reports/m8_ahmed_correction_final_report.md` |
| M9 Kotte | **implemented and validated 2026-08-09**; all M9.1–M9.4 test and code-review gates passed |
| M10 baselines | not started; requires its own approved plan |
| New captures E/F/G | still awaited; they are the untouched BR bin-rule holdout and need exact-origin metadata |

The working tree still contains unrelated/user documentation changes and deleted superseded M9
planning files. Inspect `git status --short` and preserve them. Do not reset or stage them as a
bundle. `tmp/` is untracked and requires ownership inspection before cleanup.

## 3. Active task / next steps

M9 implementation is complete. The next safe actions are:

1. If explicitly authorized, integrate the accepted M9 result into `THIRD_CHAPTER.md` and
   `JOURNAL_PAPER.md` using the thesis-safe wording in §5 below.
2. Do not rerun or tune M9 from Masimo. Any estimator-setting change requires a new reviewed plan.
3. Start M10 only from an approved M10 authority; do not infer it from M9 history.
4. When E/F/G captures arrive, follow `notes/protocol.md` and the existing one-touch BR bin-rule
   scoring instructions; do not reuse the eight exploratory captures as an untouched holdout.

## 4. Decisions that must not drift

- The only M9 authority is `plans/m9_kotte_plan.md`.
- Literal Kotte evidence is the direct full-rank two-line control. The project path is a disclosed
  adaptation: external radar-only range lock, no DOA claim, four RX, fixed chirp-loop 0 across
  50 ms frames, retained-support mean removal, trace loading, band restriction, 37 `N_c=16` CPIs,
  and lowest-index L1 medoid aggregation.
- Slow time is frame-to-frame fixed chirp-loop 0, not the 32-chirp coherent mean and not the
  existing frame-rate vital-sign phase signal.
- The two real-data loading arms (`delta=1e-2`, `delta=1e-4`) remain separate. Never rank, select,
  pool, or promote them from their Masimo scores.
- Literal post-range/`Y_t` 0 dB is the primary paper-control interpretation. The +21.0721 dB
  FFT-gain result is a separately labelled sensitivity, not a real-data setting.
- `N_c=64`, oracle information, recorded historical locks, all-bin selection, and the former MAE
  GO/NO-GO architecture are not active M9 arms.
- Canonical range locks are `current_production_rerun_lock` from unchanged warmup code. Recorded
  locks remain diagnostic-only.
- Scoring uses unchanged `src.masimo.load_masimo` clock-glitch normalization, then requires unique
  integer parsed timestamps. All 30 merged seconds are persisted as evidence.
- Comparative metrics use only `k>=1`; `k=0` is labelled `lock_selection_in_sample` and diagnostic.
- All eight-capture scoring outputs are `exploratory_non_frozen`, promotion-ineligible, and
  claim-ineligible. Poor results must not feed back into estimator choices.

## 5. Verified M9 evidence and interpretation

### Paper and synthetic evidence

- M9.1 implementation-validation controls:
  `results/m9_kotte_controls/20260808T214759.951535Z_b3b3bb4e128b_implementation_validation_non_thesis`.
- Literal 0 dB Figs. 5/7/8 controls are negative; the 21.0721 dB sensitivity reproduces the
  selected behaviors. The loaded four-RX literal ablation is also negative.
- M9.2 diagnostic transfer:
  `results/m9_kotte_synthetic_transfer/20260808T225156.641305Z_d01cbc2d8404_diagnostic_nondeployable`.
  Direct two-cisoid controls pass both loading arms. Chest transfer is 0/24 and robustness is 0/10.
  This is preserved negative evidence of the two-line model's transfer limitation.

### Immutable radar-only evidence

Canonical handoff:
`results/m9_kotte_radar/20260809T001318.369165Z_d01cbc2d8404_radar_only_unscored`.

- 8 captures, 128 complete 600-frame windows, 256 arm rows;
- 256/256 algorithmically valid; 9,472/9,472 CPIs valid, rank 4;
- 128 shared-`Z` plus 256 arm NPZ artifacts, all pickle-free;
- selected-bin `Z` shape `(600,4)` complex128; decoded cube identity
  `(600,32,4,256)` complex64; CPI shape `(37,16,4)`;
- evidence surfaces `(37,98,290)` on the signed physical-Hz grids;
- radar artifact is immutable: 400 files, digest
  `adf4434de20b9c88f34a730dc9dae1cf61c77933f4a6024ab28e9c11e8f1e9b1`.

### Exploratory scoring evidence

Canonical scoring artifact:
`results/m9_kotte_score/20260809T014500.317959Z_adf4434de20b_exploratory_non_frozen`.

- 632 scored rows, 100 summaries, 408 paired evidence rows;
- all 240 comparative radar arm/windows are algorithmically valid;
- unique admitted comparative reference cells: HR 66/120, BR 105/120;
- both radar arms have 100% algorithmic coverage; joint coverage follows reference admission;
- 30 duplicate Masimo seconds were normalized by the unchanged parser and fully audited;
- pair margins are extremely small (overall minimum about `1.8e-5` dB), indicating weak
  pair-selection separation despite declared numerical validity.

Protocol MAE in bpm:

| Protocol | HR `delta=1e-2` | HR `delta=1e-4` | HR session median (descriptive) | BR `delta=1e-2` | BR `delta=1e-4` |
|---|---:|---:|---:|---:|---:|
| natural | 29.434 | 29.368 | 0.981 | 6.406 | 5.447 |
| paced | 19.100 | 10.700 | 0.800 | 10.700 | 8.200 |
| stepped | 27.438 | 26.938 | 1.375 | 11.800 | 11.300 |

Thesis-safe conclusion: **our fixed-range, four-RX, frame-axis, loaded adaptation of Kotte et al.
substantially underestimates HR and has substantial BR error on these approximate-origin IWR1642
captures.** This is trustworthy negative project-transfer evidence. It does **not** show that
Kotte et al.'s published method generally fails for human vital-sign estimation.

## 6. Known limitations

- Kotte's source simulations use a two-line point-target model; chest displacement produces
  conjugate sidebands and a higher-order Bessel comb.
- The project has four RX, so the literal unloaded covariance is rank-deficient at `N_c=16`;
  trace loading is an explicit adaptation.
- Range is supplied by the unchanged project warmup selector; the paper range/DOA pipeline is not
  implemented and no DOA conclusion is supported.
- The old captures have approximate origin and narrow HR range. The session-median comparison is
  descriptive and uses reference information; it is not a deployable baseline.
- Very small pair margins show weak objective separation even where the declared validity rules
  pass.
- M9.1 paper-control artifacts are implementation-gate evidence, not thesis outcome evidence;
  M9.2 transfer is diagnostic/nondeployable.

## 7. Pointers

| File or artifact | Purpose |
|---|---|
| `plans/m9_kotte_plan.md` | sole approved M9 implementation authority |
| `src/m9/kotte_core.py` | equations, search, adapter, loading, CPI and medoid implementation |
| `src/m9/paper_control.py` | literal/alternate paper controls and evidence |
| `figures/reproduce_kotte_controls.py` | reproducible paper-control artifacts/figures |
| `scripts/m9_kotte_synthetic_transfer.py` | diagnostic two-line/chest transfer runner |
| `scripts/m9_kotte_run.py` | canonical radar-only runner |
| `scripts/m9_kotte_score.py` | radar-free exploratory scorer |
| `experiments/m9_kotte/config.yaml` | sole active M9 experiment configuration |
| `tests/test_m9_kotte_core.py` | equation, control, adapter and synthetic contracts |
| `tests/test_m9_kotte_run.py` | radar runner, lock, evidence and firewall contracts |
| `tests/test_m9_kotte_score.py` | scorer, alignment, metric, taint and immutability contracts |
| M9.3 radar artifact above | immutable unscored estimator handoff |
| M9.4 scoring artifact above | accepted exploratory metrics and report |
