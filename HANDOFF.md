# Handoff — radar-vitals

> Read this and `CLAUDE.md` before resuming. State verified on 2026-07-29.
> `HISTORY.md` contains the chronological detail. This file is rewritten, not appended.

## 1. Project snapshot

This project estimates heart rate and breathing rate from a TI IWR1642BOOST +
DCA1000EVM 77 GHz FMCW radar. Masimo MightySat CSV ground truth uses the
`Beats / min` PR column aligned by integer Unix-epoch `Timestamp`; breathing uses
`Breaths / min` where present. The intended outputs are a journal paper and thesis chapter.

The current direction combines:

1. simulation-based reproduction/adaptation of the reference algorithms; and
2. exploratory real-capture feasibility evidence.

The original M0/M4 frozen multi-subject design remains documented but is deprioritized.
The method rationale and literature synthesis are in `notes/approach.md`; the older whole-project
roadmap is `plans/implementation_plan.md`.

## 2. Repository state

- Branch: `vital_signs_v9c`
- HEAD before the current uncommitted M8 work:
  `ba2f3d40238cf8fd238e5d43505a413bad9e2ffa`
- Current M8 changes are intentionally uncommitted.
- Tracked files modified in this work:
  - `HISTORY.md`
  - `HANDOFF.md`
  - `notes/approach.md`
- New M8 paths:
  - `plans/m8_step1a_ahmed_reproduction.md`
  - `src/m8/__init__.py`
  - `src/m8/ahmed_fig8.py`
  - `experiments/m8_ahmed_fig8/config.yaml`
  - `figures/reproduce_ahmed_fig8.py`
  - `tests/test_m8_ahmed_fig8.py`

No production DSP was modified. In particular, M8 Step 1a does not change
`src/respiration.py`, `run_window_dsp`, or `scripts/score_offline.py`.

Use the pinned environment:

```powershell
& 'C:\ProgramData\anaconda3\condabin\conda.bat' run -n radar-vitals <command>
```

On this Windows host, give pytest a workspace-local `--basetemp=.pytest_tmp\<name>` because the
default temporary directory may be unwritable.

## 3. Active task: M8 Step 1a

### 3.1 What is implemented

The approved build authority is `plans/m8_step1a_ahmed_reproduction.md`.

`src/m8/ahmed_fig8.py` is an isolated implementation of:

- Ahmed et al. equation (14) as two independent single-TX/RX cosine returns;
- the paper-derived PRF and fixed five-breath acquisition;
- deterministic real AWGN;
- unwindowed, undetrended positive-frequency FFT magnitude;
- the paper's spectral convention with peaks at \(2f_b\) and \(2f_h\);
- \(H=3\) and \(H=5\) fixed-harmonic accumulation;
- exact finite-tie, empty-domain, exclusion, and Nyquist behavior;
- the three contradictory source interpretations:
  - `figure_visible_unsuppressed`
  - `eq26_multiples_suppressed`
  - `prose_low_or_equal_suppressed`
- a native adapter dictionary accepted by `as_window_estimate`.

The adapter compatibility is only a record-boundary check. The simulation is not a
`WindowEstimator`, and `score_offline.py` is still hardwired to `run_window_dsp`; scorer
dispatch belongs to later adaptation work.

`figures/reproduce_ahmed_fig8.py` is the evidence CLI. It writes a collision-safe result
directory with:

- resolved configuration;
- strict metrics and provenance JSON;
- non-object-dtype NPZ evidence for all primary profiles and ten one-factor audits;
- PNG and PDF figures; and
- a `running`/`complete`/`failed` status manifest.

Canonical promotion is versioned and requires all of the following:

1. a clean `git status --porcelain`;
2. all required plan/config/code/test paths tracked; and
3. the exact approved default v1 contract.

Dirty exploratory runs cannot overwrite or populate
`figures/generated/m8_ahmed_fig8/`. That directory is currently absent by design.

### 3.2 Scientific outcome

The latest final evidence is:

`results/m8_ahmed_fig8/20260729T005107.440812Z_8e08f5ab0120/`

Run status: `complete`.

Scientific status: `not_reproduced_under_declared_assumptions`.

Primary `figure_visible_unsuppressed` estimates:

| Curve | Selected rate | Target | Outcome |
|---|---:|---:|---|
| Breathing, \(H=3\) | 20.0072 bpm | 20 bpm | within native resolution |
| Breathing, \(H=5\) | 20.0072 bpm | 20 bpm | within native resolution |
| Heart, \(H=3\) | 40.0144 bpm | 80 bpm | subharmonic; failed |
| Heart, \(H=5\) | 20.0072 bpm | 80 bpm | subharmonic; failed |

None of the ten predeclared one-factor audits selected 80 bpm. Do not tune parameters to convert
this into a success.

The negative result is mathematically coherent: with \(\theta_0=0\), each return contains DC and
even harmonics, and the unsuppressed heart candidate sweep is dominated by the breathing comb and
its subharmonics. It supports only this narrow claim:

> The visible unsuppressed-curve interpretation does not reproduce Ahmed et al.'s reported heart
> maximum under the declared assumptions.

It does not prove that the authors' unpublished implementation fails. Equation (26) separately
suppresses the breathing row and its multiples; because
\(2f_h=4(2f_b)\), it also removes the heart target and is reported as
`inconclusive_by_definition`. The source is internally inconsistent.

### 3.3 Final evidence and provenance

Latest key hashes are in the run's `provenance.json`. At the final run:

- signal realization:
  `1977bd5bfcf846030834c7a429a1589cda840251127796f4b1d8ca72180d6fe0`
- Ahmed PDF:
  `2d13bca3fdfcbf249a500622dac0be9ad37e35a6aa880c440ff0c12eb4f8689f`
- source configuration:
  `47ae39d87966adbb0beb496371a78a3e1f828c116cfc965b2f940207b18180a8`
- resolved configuration:
  `2133320d26b58943e8da2f7b5ba42cfb673be030817095d023e21f453078f99c`
- plan:
  `3cfe18129708735ac31b1f56408a48934b9e2f57676f1f644b084e13c3700478`

The run correctly did not promote a canonical bundle:
`git_tree_or_required_tracking_not_clean`. After the reviewed files are committed, rerun the
default command from a clean tree to create the citable versioned bundle:

```powershell
& 'C:\ProgramData\anaconda3\condabin\conda.bat' run -n radar-vitals `
  python figures/reproduce_ahmed_fig8.py
```

Do not copy the current dirty run into the canonical directory by hand.

### 3.4 Verification

Focused verification:

```powershell
& 'C:\ProgramData\anaconda3\condabin\conda.bat' run -n radar-vitals `
  python -m pytest tests/test_m8_ahmed_fig8.py tests/test_window_pipeline_adapter.py `
  -q --basetemp=.pytest_tmp\m8_target_final
```

Result: **85 passed**.

Broader regression suite, excluding four known environment-dependent scorer cases:

```powershell
& 'C:\ProgramData\anaconda3\condabin\conda.bat' run -n radar-vitals `
  python -m pytest tests -q --basetemp=.pytest_tmp\m8_full_final2 `
  -k "not test_resolve_pinned_lock_directory_correct and not test_resolve_pinned_lock_rejects_unrelated_raw_hash and not test_resolve_pinned_lock_isolate_active_requires_baseline_eca_mode and not test_end_to_end_real_capture_rerun_estimand"
```

Result: **1816 passed, 2 skipped, 4 deselected**.

The four raw full-suite failures are not M8 regressions:

- three `tests/test_score_offline.py` tests depend on ignored local replay directories that are
  absent in this checkout;
- one legacy end-to-end scorer assertion expects the production clean-tree helper to report dirty
  under `--allow-dirty`, but that helper uses `git diff --quiet HEAD --` and ignores untracked
  files. M8 deliberately uses the stricter porcelain/tracking gate instead of modifying production
  code in this step.

The final PDF was rendered with Poppler and inspected at 150 DPI. Panel order, axes, target
markers, legends, units, and labels are legible and unclipped.

Two independent post-build reviewers approved the final implementation:

- architecture: approve, no remaining blocker or major finding;
- mathematical/correctness/testing: approve after independent PRF derivation, coherent-grid
  Jacobi-Anger/Bessel tests, and provenance-hash recomputation.

## 4. Other project state worth preserving

The offline scorer and comparator from the prior milestone are implemented and were run on the
three Masimo captures. Their latest detailed state and exploratory small-\(n\) guard-v1 results are
in the preceding 2026-07-28 entry in `HISTORY.md` and
`results/score_offline/20260728T154834Z/`. Do not infer a guard-v1 promotion from those \(n\le2\)
incremental-coverage buckets.

M4 Stage 1 review remains open and deprioritized; do not call it closed. The 5-bin relock tracker
go/no-go also remains deferred.

## 5. Next actions

1. Review the M8 diff and commit the plan, configuration, source, CLI, tests, approach note,
   `HISTORY.md`, and this handoff.
2. From that clean commit, rerun the exact default M8 CLI and verify that versioned canonical
   promotion succeeds and `LATEST.json` points at the new immutable bundle.
3. Review the negative scientific evidence before choosing between:
   - requesting clarification or code/data from Ahmed et al.; or
   - registering another source-grounded interpretation in a new reviewed plan.
4. Do not start M8 Step 1b, production adaptation, or `score_offline.py` integration until the
   Step 1a evidence is explicitly approved.
5. Continue collecting more subjects before any guard-v1 promotion decision.

## 6. Pointers

| Purpose | Path |
|---|---|
| Agent/project rules | `CLAUDE.md` |
| Chronological record | `HISTORY.md` |
| M8 build authority | `plans/m8_step1a_ahmed_reproduction.md` |
| M8 simulation/HA code | `src/m8/ahmed_fig8.py` |
| M8 resolved input contract | `experiments/m8_ahmed_fig8/config.yaml` |
| M8 evidence CLI | `figures/reproduce_ahmed_fig8.py` |
| M8 test suite | `tests/test_m8_ahmed_fig8.py` |
| Latest complete M8 run | `results/m8_ahmed_fig8/20260729T005107.440812Z_8e08f5ab0120/` |
| Scientific metrics | latest run's `metrics.json` |
| Reproducibility manifest | latest run's `provenance.json` |
| Figure PDF | latest run's `ahmed_fig8cd_behavioral.pdf` |
| Method/literature rationale | `notes/approach.md` §5.7 |
| Offline scorer plan | `plans/offline_scoring_script.md` |
