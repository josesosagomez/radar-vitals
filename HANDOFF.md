# Handoff — M8 Step 1b planning

> Read this and `CLAUDE.md` before doing anything. State verified on 2026-07-29.
> `HISTORY.md` contains the chronological record. This file is rewritten, not appended.

## 1. Immediate task for the new chat

Plan **M8 Step 1b: transfer Ahmed harmonic accumulation to this project's phase model, then to
the saved real FMCW captures**.

There is an important scope definition to preserve. `plans/implementation_plan.md` already defines
Step 1b as the synthetic all-harmonic phase-model transfer control; real-capture evaluation follows
that gate. The new plan should cover both stages in that order, not skip directly from the
equation-(14) simulator to performance testing.

Do not implement Step 1b yet. The next chat should:

1. inspect the paper, Step 1a evidence, all relevant Python interfaces, and all eight capture
   schemas;
2. write the first draft to `plans/m8_step1b_ahmed_transfer.md`;
3. have independent architecture, correctness/math, Python, testing, and adversarial reviewers
   verify the draft against the repository; and
4. revise the plan and wait for explicit user approval before changing estimator or scorer code.

The user has clarified the intended eventual real-data evaluation set: `demo_massimo1` through
`demo_massimo7` plus `demo_sweep`.

## 2. Critical boundary: what Step 1a did and did not do

Step 1a is complete and canonicalized. It implemented Ahmed et al.'s equation-(14) synthetic
single-TX/RX model and attempted a behavioral reproduction of Fig. 8(c)-(d).

It did **not**:

- decode `adc_stream.bin`;
- consume radar frame cubes;
- implement the `(frames, locked_bin, fs, cfg)` `WindowEstimator` protocol;
- run on any `demo_massimo*` or `demo_sweep` window;
- modify `scripts/score_offline.py` to dispatch a foreign estimator; or
- compare an Ahmed estimate against Masimo.

`build_adapter_result(...)` passing through `as_window_estimate(...)` proves only that a synthetic
native dictionary can be normalized into a `WindowEstimate`. It is not real-data readiness.

Therefore the current evidence supports neither “Ahmed improves the real captures” nor “Ahmed
cannot improve them.”

### Step 1a scientific result

Canonical bundle:

`figures/generated/m8_ahmed_fig8/20260729T075443.145998Z_8e08f5ab0120/`

Canonical source commit:

`d1f44829ae0b272b61f2151ef1d2087491b16e26`

Status: `not_reproduced_under_declared_assumptions`.

| Synthetic curve | Selected rate | Target |
|---|---:|---:|
| Breathing, \(H=3\) | 20.0072 bpm | 20 bpm |
| Breathing, \(H=5\) | 20.0072 bpm | 20 bpm |
| Heart, \(H=3\) | 40.0144 bpm | 80 bpm |
| Heart, \(H=5\) | 20.0072 bpm | 80 bpm |

No predeclared Step 1a audit selected 80 bpm. Equation-(26)-literal suppression excludes the
collision target \(2f_h=4(2f_b)\) and is `inconclusive_by_definition`. Do not tune Step 1b to
reverse the Step 1a result, and do not describe Step 1a as a real-capture failure.

## 3. Repository state

- Branch: `vital_signs_ahmed_v10`
- HEAD: `d1f44829ae0b272b61f2151ef1d2087491b16e26`
- Current uncommitted work:
  - modified `HANDOFF.md`
  - modified `HISTORY.md`
  - modified `notes/approach.md`
  - modified `plans/implementation_plan.md`
  - untracked canonical bundle under `figures/generated/m8_ahmed_fig8/`

The user should commit this documentation and canonical bundle when satisfied. Do not discard or
overwrite it.

Use:

```powershell
& 'C:\ProgramData\anaconda3\condabin\conda.bat' run -n radar-vitals <command>
```

Use a workspace-local pytest base such as `--basetemp=.pytest_tmp\<name>` on Windows.

## 4. Saved real-capture inventory

All eight directories exist under `results/live_demo/`. Every one contains:

- `adc_stream.bin`;
- one Masimo CSV;
- `live_estimates.csv`;
- `live_intermediates.npz`;
- `run_metadata.json`; and
- `warmup_bin_selection.json`.

All metadata records 20 Hz frames, 30 s windows, and 3 s hops.

| Capture directory | Nominal duration | Recorded lock | Masimo rows |
|---|---:|---:|---:|
| `20260713_172042_live_demo_massimo1` | 180 s | 23 | 247 |
| `20260713_182002_live_demo_massimo2` | 180 s | 20 | 272 |
| `20260714_180523_live_demo_sweep` | 480 s | 21 | 574 |
| `20260728_224902_live_demo_massimo3` | 600 s | 26 | 613 |
| `20260728_230903_live_demo_massimo4` | 600 s | 25 | 611 |
| `20260728_232415_live_demo_massimo5` | 600 s | 25 | 608 |
| `20260729_002158_live_demo_massimo6` | 600 s | 24 | 613 |
| `20260729_004815_live_demo_massimo7` | 600 s | 32 | 612 |

Important inventory cautions:

- The `massimo3` metadata `session_id` is `massimo_3`, unlike its directory/CSV naming. Resolve
  captures by validated paths or an explicit registry, not by assuming a uniform session string.
- Only Massimo 1-2 persist `live_raw_mirror_hash`; the other six metadata files contain `null`.
  Compute and persist SHA-256 directly from every `adc_stream.bin`.
- The captures were made under different commits/config generations, and their recorded warmup
  locks are not automatically a fair common lock policy.
- Earlier work documented lock concerns in the old Massimo 1, Massimo 2, and sweep sessions.
  Step 1b must choose and justify a fixed per-capture lock source shared by every compared
  estimator, or report pinned and rerun-lock estimands separately.

## 5. Decisions the Step 1b plan must settle before implementation

### 5.0 Synthetic transfer gate

Before decoding real captures, implement and validate the master roadmap's Step 1b control:

- replace Ahmed's even-harmonic equation-(14) return with the all-harmonic phase formulation used
  by this project;
- keep the collision scenario and all other defensible Step 1a parameters fixed where possible;
- derive whether candidate frequency and bpm conversion now use \(f\) rather than \(2f\);
- state exactly which Step 1a accumulator/suppression behavior is reused and which part is an
  adaptation;
- define full-curve and rate acceptance criteria before running it; and
- record a negative transfer as a valid result rather than tuning the model.

The plan must make the gate explicit: no real-capture Ahmed performance run until the synthetic
all-harmonic result is complete, validated, and reported. A negative synthetic transfer need not
automatically forbid descriptive real-data work, but continuing would require a named rationale
and user approval rather than silently treating the control as passed.

### 5.1 Paper-to-FMCW signal mapping

Ahmed's equation (14) is a real slow-time return at one fixed fast-time sample. This repository
has complex FMCW range-bin data after range processing. The following are scientifically different:

- one quadrature or the real part of the coherent locked-bin return;
- the complex locked-bin return with a newly defined magnitude score;
- magnitude/envelope;
- unwrapped phase or displacement-derived phase.

The first is closest in form to equation (14) but depends on arbitrary complex phase. Magnitude can
remove the sign/modulation structure. Phase produces peaks at \(f\), not automatically the paper's
\(2f\) convention. The plan must derive and name the mapping from the signal model; it must not try
several mappings and select whichever scores best against Masimo.

If more than one source-defensible mapping must be retained, preregister them as independently
reported variants. Do not let one upgrade another's result.

The plan must also decide, before seeing Ahmed-vs-Masimo scores:

- chirp aggregation;
- RX aggregation or channel selection;
- static-clutter/DC handling;
- whether detrending/windowing is forbidden for the paper-faithful transfer arm;
- the exact range FFT and locked-bin extraction path; and
- how the one-dimensional slow-time signal is preserved as evidence.

### 5.2 The 20 Hz Nyquist blocker

Every saved capture has 20 Hz frame rate and 10 Hz Nyquist.

For Ahmed's spectral candidate \(q\), \(H=5\) at the 100 bpm heart limit requires:

\[
5 \times 2(100/60)=16.67\ \mathrm{Hz},
\]

which is not observable. Even \(H=3\) reaches exactly 10 Hz at 100 bpm.

Resampling cannot create the missing harmonics. The plan must explicitly choose among defensible
policies such as:

- make \(H=3\) the only full-range real-capture transfer arm and keep \(H=5\) as
  unsupported/descriptive;
- restrict \(H=5\)'s candidate domain to the truly supported rate range and label the resulting
  estimand; or
- define a separately named partial-harmonic accumulator that is acknowledged as an adaptation,
  not Ahmed's fixed-\(H\) method.

Do not silently truncate, wrap, interpolate, or change the denominator.

### 5.3 Window grid and acquisition duration

For a fair estimator comparison, the production and Ahmed arms should normally use the identical
frozen 30 s / 600-frame windows and 3 s hops already built by `src/m4/window_grid.py`.

If a paper-duration 15 s audit is wanted, keep it secondary and do not compare its metrics directly
with the 30 s production arm without a separately defined common window grid.

The plan must define behavior for:

- warmup frames;
- trailing incomplete windows;
- frame-zero epoch;
- dropped/truncated bytes;
- nonfinite signals; and
- windows whose requested harmonic support exceeds Nyquist.

### 5.4 Suppression and collision interpretations

Retain the Step 1a source disagreement:

1. `figure_visible_unsuppressed`
2. `eq26_multiples_suppressed`
3. `prose_low_or_equal_suppressed`

Report each independently. Do not select a suppression rule by lowest real-data error.

The plan must define whether the breathing bin used for heart suppression comes from the Ahmed
breathing arm or another estimator. Using production BR would couple the new method to the
baseline and must be explicit.

### 5.5 Validity and outputs

The paper's accumulator naturally returns an argmax, while the production method has quality and
AHET rejection gates. A fair plan must separate:

- raw finite estimates available on every eligible window;
- method validity/rejection defined without Masimo tuning; and
- downstream scoring coverage.

Every window must dump at least:

- extracted slow-time signal and its exact definition;
- sample rate and timestamps;
- frequency grid and spectrum;
- candidate and harmonic-bin matrices;
- pre-exclusion scores;
- suppression/eligibility mask;
- selected and runner-up bins/scores;
- HR/BR values and validity;
- rejection reason;
- estimator/config/signal hashes; and
- range-lock provenance.

### 5.6 Scorer architecture

Relevant interfaces:

- `src/window_pipeline.py::WindowEstimator`
- `src/window_pipeline.py::as_window_estimate`
- `scripts/score_offline.py`
- `src/comparator.py`
- `src/m4/window_grid.py`

`scripts/score_offline.py` currently calls `run_window_dsp(...)` directly around line 1073 and
passes the production `ESTIMATOR_ID`. Step 1b must either make this path estimator-pluggable or add
a narrowly scoped estimator-neutral runner without copying the comparator/window/reference logic.

The plan should prefer one shared window decode and reference partition, then run multiple named
estimators on the same frames and lock. It must prevent:

- duplicated decoding/comparator logic;
- estimator-specific window omissions;
- mutable raw evidence aliasing;
- production-only outcome classifiers being applied to Ahmed records; and
- foreign estimator IDs being overwritten with `eca_ahet_v1`.

### 5.7 Ground truth and metrics

Ground truth rules are unchanged:

- HR: Masimo `Beats / min` aligned through integer Unix-epoch `Timestamp`;
- BR: Masimo `Breaths / min`;
- use the frozen comparator admissibility/stationarity rules;
- do not tune radar outputs to a poor Masimo segment.

At minimum compare production ECA+AHET and each preregistered Ahmed arm on identical admissible
windows using:

- MAE, RMSE, bias, and percentile errors;
- reference, radar, and joint coverage;
- HR and BR separately;
- per-capture and pooled results;
- paired intersection metrics; and
- incremental-coverage partitions where meaningful.

The sweep has no persisted true transition timestamps for its commanded breathing schedule.
Do not invent metronome target alignment; radar-vs-Masimo BR can still be scored using the Masimo
column.

### 5.8 Development versus evaluation

These are existing, single-subject development captures. `notes/analysis_prespec.md` classifies
existing captures as development/tuning and exploratory evaluation, not confirmatory evidence.

Before producing Ahmed results, the plan must decide whether:

- all eight are development, making every result apparent/in-sample; or
- a predeclared subset is withheld from all mapping/threshold decisions and used as an exploratory
  holdout.

No capture may both choose an adaptation and validate that adaptation. Do not report pooled windows
as independent subjects.

## 6. Required planning/review deliverable

Create `plans/m8_step1b_ahmed_transfer.md`. It should include:

- scope and explicit non-goals;
- source equations and unresolved ambiguities;
- the synthetic all-harmonic transfer gate and its acceptance criteria;
- exact estimator interface and data flow;
- eight-capture registry with direct raw/config/reference hashes;
- preregistered signal mappings and harmonic-support policy;
- lock, window, validity, and suppression rules;
- scorer changes, if any;
- failure/status/artifact schemas;
- unit, oracle, property, integration, regression, and visual validation;
- acceptance criteria that allow an honest negative result;
- “Assumptions requiring confirmation”;
- “Risks of tuning and data leakage”; and
- “Step 1a components reused unchanged.”

Then run five independent read-only reviews:

1. architecture;
2. mathematical correctness and edge cases;
3. Python implementation;
4. testing and validation; and
5. adversarial pre-mortem.

Reviewers must inspect the plan, Ahmed source evidence, and relevant code. Merge their findings,
resolve disagreements with repository evidence, revise the plan, and stop for user approval.

## 7. Verification state to preserve

Step 1a focused suite:

```powershell
& 'C:\ProgramData\anaconda3\condabin\conda.bat' run -n radar-vitals `
  python -m pytest tests/test_m8_ahmed_fig8.py tests/test_window_pipeline_adapter.py `
  -q --basetemp=.pytest_tmp\m8_target_final
```

Result: **85 passed**.

Broader verified suite: **1816 passed, 2 skipped, 4 known environment-dependent scorer cases
deselected**. See `HISTORY.md` for the exact command and explanation.

No Step 1b test or real-capture Ahmed result exists yet.

## 8. Pointers

| Purpose | Path |
|---|---|
| Project rules | `CLAUDE.md` |
| Chronological record | `HISTORY.md` |
| Method/literature rationale | `notes/approach.md` §5.7 |
| Evaluation-role rules | `notes/analysis_prespec.md` |
| Immutable Step 1a plan | `plans/m8_step1a_ahmed_reproduction.md` |
| Canonical Step 1a bundle | `figures/generated/m8_ahmed_fig8/20260729T075443.145998Z_8e08f5ab0120/` |
| Step 1a simulation | `src/m8/ahmed_fig8.py` |
| Estimator protocol/adapter | `src/window_pipeline.py` |
| Current offline scorer | `scripts/score_offline.py` |
| Frozen comparator | `src/comparator.py` |
| Frozen window grid | `src/m4/window_grid.py` |
| Eight raw captures | `results/live_demo/` |
| New plan to create | `plans/m8_step1b_ahmed_transfer.md` |
