# Handoff — radar-vitals

> **Read this + `CLAUDE.md` and you can resume the project.** This file describes the state
> **as of 2026-07-15**. It is rewritten, not appended (CLAUDE.md §10). For *what happened
> and why*, read `HISTORY.md`.

---

## 1. Project snapshot

Estimate heart rate from a 76-81 GHz FMCW radar (TI IWR1642BOOST + DCA1000EVM) while the
subject sits 0.8-1.4 m in front of the sensor, chest facing the radar. Ground truth is a
Masimo MightySat pulse oximeter (1 Hz CSV, `Beats / min` = PR), scored under a
**pre-registered comparator** (`notes/comparator_prespec.md` — median PI-gated PR over the
same 30 s window the radar used, with a stationarity gate; binding on every agreement number
in the paper). The DSP extracts chest-wall phase from a locked range bin, cancels respiration
harmonics (ECA) and verifies the cardiac peak via a second-harmonic check (AHET). Target
output: journal paper + thesis chapter.

**Method rationale, literature and the algorithm spec live in `notes/approach.md`.**

---

## 2. Current state

**Branch: `vital_signs_v9b`.** v8 algorithm (fixed bin, no relocking, no display holdover)
plus the v9 UI redesign. Relocking/holdover deliberately reverted — do not reintroduce
without asking. v9 UI work sits in `git stash@{0}` (`vital_signs_v9`), untouched.

**Data:** the 2026-07-09 reset wiped everything under `data/raw/`, `data/processed/`,
`results/` (the offline Step1-6 pipeline) and `figures/`; `data/manifest.local.csv` is still
just its header row (0 sessions logged there). Separately, **4 real live-demo captures now
exist** under `results/live_demo/` (this path was never touched by the reset):
`20260713_170323_live_demo_live_test1` (no Masimo), `20260713_172042_live_demo_massimo1`,
`20260713_182002_live_demo_massimo2`, and `20260714_180523_live_demo_sweep` (a deliberate
12→15→18→21 bpm paced-breathing staircase, 486 s). Two more folders,
`20260714_224000_replay_unknown` / `20260714_224244_replay_unknown`, are **not real
sessions** — they're replay-verification artifacts from bin-lock testing, kept as evidence
for a HISTORY claim, not subject data.

**Method validated against Masimo (2026-07-13), but the headline number is WITHDRAWN.**
"MAE 0.16 bpm" compared a 30 s radar estimate to a single instantaneous Masimo sample; under
the pre-registered window-mean comparator the same hops give MAE 2.72 bpm with 1 severe
error. **No agreement number may be cited without going through `notes/comparator_prespec.md`.**

**Test suite: 796 passed, 1 xfailed, 0 failed** (`conda run -n radar-vitals python -m pytest
tests/ -q`, verified 2026-07-15). One thing worth flagging: an earlier HISTORY entry
(2026-07-14) reported **33 failing Step-4 quality-mask tests** with an explicit "deliberately
not fixed this session." **Verified: they are not failing now** — `tests/test_quality_mask.py`
+ `tests/test_quality_mask_new.py` both pass (209 tests) and the full suite is green. **No
HISTORY entry documents when/how this was fixed** — a real gap in the record. If the Step-4
gating stage matters for upcoming work, don't just trust the green count; skim
`tests/test_quality_mask.py` to confirm it exercises the refactored `_process_session()` API.

**Two independent workstreams. (B) is fully closed and committed. (A) has one thread newly
advanced to a verified control scaffold; the rest is unchanged from before.**

### (A) The AHET candidate-selection / ECA investigation

This chases *why AHET sometimes verifies the wrong candidate*. Status of each mechanism tried
(`notes/plan_eca_forbidden_zone.md` PART IV has the full staged plan):
- **ECA v1 (`skip_forbidden_harmonics_v1`, still the LIVE production mode)** is known-inert
  in the cardiac band at low breathing rates (0.00 dB removed) — the config's own comment
  says so (`scripts/live_demo_config.yaml`).
- **`guard_cardiac_candidate_v1`** is implemented and unit-tested but **NOT promoted** —
  waiting on `experiments/exp_eca_modes` (which doesn't exist yet — `experiments/` is empty).
- **ECA v2 (extend the cancellation ceiling to 4 Hz) is REJECTED**, **Stage 1 (comb-fit f_r
  estimator) was killed before being built**, and **Stage 1A (harmonic coherence) FAILS on
  both original captures** — the whole "cancel high-order harmonic evidence" mechanism is
  dead for this design family. Licensed conclusion: on these sessions, a stationary
  single-sinusoid model of the high-order respiratory harmonics is unsupported. **Not
  licensed**: dead on physics permanently. **Stage 1A itself still owes its cross-model
  review** (CLAUDE.md §6) — treat the FAIL as provisional until reviewed.
- **Stage 1B (temporal continuity — a respiratory harmonic tracks f_r, a heartbeat doesn't):
  the leverage problem is SOLVED IN DESIGN, not yet in data.** Round 2's one-hop statistic
  had no leverage on any capture (breathing-rate transitions ramp over ~10 hops; a 1-hop
  delta sees almost no movement). A lag-10 redesign was fully specified across **6 rounds of
  cross-model plan review** (round 1 NOT CLEARED → rounds 2-5 progressively-fixed READY WITH
  FIXES → round 6 READY FOR CONTROL-SCAFFOLDING) and its **control scaffold is implemented
  and verified**: `scripts/stage1b_lag_statistic.py` (untracked — see §3). Full design in
  `notes/note_stage1b_lag_statistic.md` (draft 7, current). **Not yet done**: the design's
  own §7(c) numeric pre-registration (blocking any real evaluation) and a new confirmatory
  capture (all 3 existing sessions are now exploratory-only for this specific design — see
  §3 for exactly why and what's next).
- **Candidate-ranking ("magnitude order ≠ credibility order", `notes/note_candidate_ranking.md`)
  is a confirmed, real bug, but small (n=1 clean example so far).** **Needs its own plan +
  cross-model review before any code** (CLAUDE.md §6) — do not implement ad hoc. No plan
  written yet.
- **The respiration-collapse bug** (f_r pins to the 0.1 Hz / 6 bpm search-band floor while
  `resp_valid` stays `True`) is **logged 4-for-4 across every Masimo-referenced capture**.
  Independent of everything above. Still open, unfixed, no owner.

### (B) The warmup range-bin lock — DONE, committed

Root-caused, fixed, cross-model reviewed three times (two design rounds + one post-commit
wording nit), and **committed**: `863600e` (the fix) and `dfe7fb5` (a wording-only follow-up
a re-review of the committed code caught). Explains something workstream (A) had flagged but
couldn't explain: the sweep session's "Pattern A" (a strong non-harmonic ~63-67 bpm peak,
t=70-169s) was the warmup bin lock choosing a skirt bin ~28 dB below the actual chest, not a
DSP/ECA/AHET problem. `scripts/validate_warmup_selection.py` (committed) reruns the real
selection logic against all 4 sessions' raw streams and confirms the picks. **Nothing left
to do here except the follow-on items in §3.**

---

## 3. Active task / next steps

**The concrete next action is the Stage 1B lag-10 design's numeric pre-registration.**
`notes/note_stage1b_lag_statistic.md` §7(c) lists exactly what needs a number, with a
derivation, before any real data can be touched:
- fraction of baseline severe accepts that must convert to NaN vs. be retained as correct;
- retention rate for baseline non-severe/genuine-cardiac accepts (must not regress);
- minimum retained yield / maximum additional NaN rate;
- minimum leverage coverage (§3 of the note) and minimum exact-track coverage (§3b);
- numeric pass bars for M0, M0b/null, and the cardiac/RSA surrogate control (§4);
- contiguous-block resampling parameters and the minimum number of independent blocks needed
  for a confirmatory claim.

**Do this the same way `notes/comparator_prespec.md`'s 5 bpm stationarity gate was built** —
derive each number from something independent (FFT resolution, existing baseline error
rates already in `HISTORY.md`, etc.), not picked to look reasonable. Once frozen:
1. Run `scripts/stage1b_lag_statistic.py`'s machinery against real lag-10 SCORE distributions
   on the 3 exploratory sessions (`natural`, `paced16`, `sweep`) — this is now legitimate
   *exploratory development*, not confirmatory (§6 of the note: all 3 sessions contributed to
   designing the statistic).
2. Fit the three-stage threshold per §7(g) on that exploratory data, then **freeze it before
   looking at anything else**.
3. Plan and take a **new confirmatory capture** — required for any claim stronger than
   "promising in development"; none of the existing captures can serve as clean validation.
4. **Decide on committing `scripts/stage1b_lag_statistic.py`.** It's untracked, fully
   verified (M0 PASS, threshold-selector self-test PASS, no crashes anywhere), but sits
   alongside 3 other untracked stage1 scripts (`stage1a_harmonic_coherence.py`,
   `stage1b_exploratory_motion.py`, `stage1b_temporal_continuity.py`) that predate it and
   haven't been triaged either — commit decision for all 4 is still open.

**What `scripts/stage1b_lag_statistic.py` does and doesn't do, if picking this up cold:** it
is pure offline research code — no connection to `live_demo.py` or live capture, ever. What
exists now is CONTROL SCAFFOLDING ONLY: synthetic self-tests proving the math is correct
(M0/M0b/null/cardiac-RSA controls, a hand-verified three-stage threshold selector), plus one
diagnostic pass over the 3 real sessions that only proves the plumbing runs, not a result. It
explicitly refuses to compute a real lag-10 SCORE distribution as a scientific finding, fit a
threshold against real data, or evaluate the decisive endpoint — all gated on the
pre-registration above. Read the module docstring before extending it.

**Other open research threads (not urgent-ordered, pick based on interest):**
1. **Bin-lock follow-ons** (workstream B): at the *correct* bin, only 19.9% of windows
   produce an AHET-verified HR (vs 36.4% pre-fix, mostly wrong) — AHET's conservatism at the
   correct bin is now the coverage bottleneck. `massimo1` still locks a mediocre bin because
   no `hr_valid` pass exists anywhere in its warmup — not fixable by this design.
2. **Candidate-ranking fix** (workstream A) — write a plan, get it cross-reviewed, then
   implement ranking by credibility instead of raw magnitude.
   `notes/note_candidate_ranking.md` has the existing writeup; no plan yet.
3. **Stage 1A cross-model review** — procedural debt; the FAIL verdict that retires the
   ECA-ceiling-extension family is still provisional.
4. **Respiration-collapse bug** — `resp_valid` stays `True` while f_r is pinned to the 6 bpm
   floor. 4-for-4 across every capture. No owner yet.
5. **10-subject study** (`notes/protocol.md`) — blocked on nothing algorithmic, but
   revalidate the bin-lock's -12 dB / 5 s thresholds (n=4 sessions / 1 subject so far) as
   real subject data comes in.

---

## 4. The capture protocol (updated 2026-07-14 — settle criterion + stepped-rate arm added)

Seated, back straight, both hands on the legs, facing the radar. Radar at chest height,
horizontal, chest 0.8-1.4 m away. Warmup auto-locks the bin (no manual/manifest pin).

**Hard settle criterion, every arm, before the radar starts:** on the live Masimo, PR spread
≤5 bpm over a continuous 60 s AND no monotonic drift (last 20 s within 3 bpm of the first
20 s). Not met within 5 minutes → abort and re-seat. (Even with this in place, the sweep
capture still showed elevated PR variability for the first ~2.5 minutes — the criterion may
need to also hold *after* warmup completes, not just before recording starts.)

**Stepped/paced-rate diagnostic arm:** 12→15→18→21 bpm, 120 s dwell each. Gives f_r movement
and puts a `4*f_r`-vs-HR collision somewhere in the sweep without guessing the subject's
resting HR in advance. **This same arm is also the natural template for Stage 1B's needed
confirmatory capture (§3)** — a new session using this protocol, not yet taken, un-seen by
any part of the lag-10 design.

Full SOP, equipment checklist, paced-breathing method: `notes/protocol.md`.
Log every session in `HISTORY.md` (subject, distance, time of day, duration, observed PR).

---

## 5. Decisions that matter (do not silently reverse these)

- **Relocking and display holdover were reverted.** Fixed-bin tracking is deliberate. v9 UI
  work is parked in `git stash@{0}`.
- **The live demo's readouts are NOT paper-grade** (CLAUDE.md §4). Paper metrics come from
  re-processing the run's saved `adc_stream.bin` offline — never `live_estimates.csv`.
- **Window is 30 s; do not shorten it.** Sets HR frequency resolution (~2 bpm at 30 s) and
  warmup bin-lock quality.
- **Statistics must use non-overlapping 30 s windows.** The 3 s hop shares 27/30 s of data
  between adjacent estimates — not independent.
- **Report coverage alongside accuracy**, always.
- **Every agreement number goes through `notes/comparator_prespec.md`.** No exceptions.
- **The bin-lock's energy-eligibility threshold (-12 dB / 5 s settle-skip) is an empirical,
  scene-scoped prior**, not a general physical law. Don't broaden its claimed scope without
  re-validating.
- **Do not implement the candidate-ranking fix ad hoc.** Needs a plan + cross-model review
  first, same as everything else touching phase/filtering/peak-picking (CLAUDE.md §6).
- **Stage 1B's lag-10 design has its own frozen contracts — don't silently vary them.**
  `L=10` hops, the exact-hop (not row-count) frozen-`k_hat` contract, zero gaps tolerated
  anywhere in the 16-hop attempt, and post-AHET-veto-only as the intervention point (it can
  only retain-or-NaN an accepted decision, never promote a different candidate) were each
  the subject of a specific cross-model review finding. Changing any of them means re-opening
  that finding, not just editing code.
- **All 3 existing sessions are "exploratory, not confirmatory" for the Stage 1B lag-10
  design specifically** — they directly informed its design (the ramp-duration measurement
  that chose `L=10`, the leverage-failure diagnosis). Do not describe a result on them as
  held-out validation.

---

## 6. Gotchas / landmines

- **`HISTORY.md`, `HANDOFF.md`, and almost all of `notes/` are NOT version-controlled.**
  `.gitignore` has a blanket `*.md` rule plus `notes*/` and `results*/`. Only `CLAUDE.md`,
  `AGENTS.md`, and `notes/approach.md` are tracked (`git ls-files | grep '\.md$'` to
  re-check). Confirmed intentional by the user (2026-07-15) — these files are local-only by
  design, not an oversight to fix.
- **A synthetic test asserting "this candidate tracks harmonic order k" must first confirm k
  is actually admissible at the chosen f_r** (`admissible_ks(f_r_hz)` in
  `scripts/stage1b_exploratory_motion.py` / `scripts/stage1b_lag_statistic.py` — requires
  `k*f_r` to land in the 0.8-2.0 Hz cardiac band). Building a "k=2 harmonic" test case at a
  typical resting breathing rate (~13 bpm) silently fails: 2*13bpm doesn't land in the
  cardiac band, so the code correctly clips to a different k, and the test looks like a code
  bug when it's a test-construction bug. Caught once already (2026-07-15, M0's first run);
  choose `f_r` per `k` (e.g. `f0_bpm = target_hz*60/k`), don't fix `f_r` and vary `k`.
- **`fallback_hr_bpm` is a LIAR.** A naive argmax, not the verified estimate. Use
  `hr_bpm_raw` / `candidate_refined_hz[0]`.
- **The live display holds the last valid HR** — it can look like a steady success while
  most hops are being rejected. Always run `scripts/diagnose_live_run.py` before believing a
  live run.
- **Respiratory-harmonic coincidence** (`notes/approach.md` §4.2): when `k * f_r ≈ HR`, ECA
  can cancel the cardiac signal along with the harmonic — an identifiability problem.
- **Run Python via `conda run -n radar-vitals`.** Conda lives at
  `C:\ProgramData\anaconda3\condabin\conda.bat` / `Scripts\conda.exe`, not on PATH. Invoking
  the env's `python.exe` by absolute path skips activation; matplotlib then can't find its
  render DLLs and any `savefig()` hard-kills the interpreter with exit 127 and no traceback.
- **I/Q ordering depends on the capture source.** SDK/Python captures need `iq_swap=True`;
  mmWave Studio captures need `iq_swap=False`.
- **Masimo reference is `Beats / min` (PR) only** — never SpO2/PI/PVi. Align on the integer
  `Timestamp` column, never the `Date`/`Time` strings.
- **NPZ replay artifacts: `elapsed_s` is WALL-CLOCK, not signal time.** Under
  `--replay-fast` it reads ~0 for every hop. Use `frame_idx / frame_rate_hz` for any
  replay-based timebase instead. Not yet fixed in `live_demo.py`/its NPZ schema.
- **`experiments/` is completely empty.** Blocks `experiments/exp_eca_modes` (needed to
  promote `guard_cardiac_candidate_v1`) and offline diagnostics that auto-discover
  `results/exp_eca_all/`.
- **`plot_bland_altman.py` holds the only Bland-Altman implementation.** `src/compare.py`
  does not do Bland-Altman despite its docstring.

---

## 7. Pointers

| What | Where |
|---|---|
| Project rules (read first) | `CLAUDE.md` |
| Method, literature, algorithm spec | `notes/approach.md` |
| ECA fix plan (staged; v1/v2 dead, Stage 0 done) | `notes/plan_eca_forbidden_zone.md` (gitignored) |
| Pre-registered agreement comparator (binding) | `notes/comparator_prespec.md` (gitignored) |
| Candidate-ranking bug writeup (no plan yet) | `notes/note_candidate_ranking.md` (gitignored) |
| Capture protocol / SOP | `notes/protocol.md` (gitignored) |
| **Stage 1B lag-10 design (draft 7, current; §7c is the next action)** | `notes/note_stage1b_lag_statistic.md` (gitignored) |
| Bin-lock cross-review prompts + findings | `notes/cross_review_warmup_veto_prompt*.md` (gitignored) |
| Evidence run (Masimo, 180 s) | `results/live_demo/20260713_172042_live_demo_massimo1/` |
| Stepped-rate sweep (Masimo, 486 s) | `results/live_demo/20260714_180523_live_demo_sweep/` |
| Live/replay demo **and capture tool** | `scripts/live_demo.py` |
| Live demo config | `scripts/live_demo_config.yaml` |
| Warmup bin-lock fix + eligibility-partition logic (committed) | `scripts/live_demo.py:_run_warmup_selection` |
| Tracked, regenerable bin-lock validation (committed) | `scripts/validate_warmup_selection.py` |
| Run post-mortem ("why was HR blank?") | `scripts/diagnose_live_run.py` |
| Live demo artifact checker | `scripts/verify_live_demo_artifacts.py` |
| Stage 1A (harmonic coherence, awaiting review) | `scripts/stage1a_harmonic_coherence.py` (untracked) |
| Stage 1B round 1 (failed, preserved) / round 2 (exploratory) | `scripts/stage1b_temporal_continuity.py`, `scripts/stage1b_exploratory_motion.py` (untracked) |
| **Stage 1B round 3 (lag-10, control scaffold verified, §7c blocks real use)** | `scripts/stage1b_lag_statistic.py` (untracked) |
| Candidate-track diagnostic (non-causal — offline only) | `scripts/diagnose_step6_candidate_tracks.py` |
| Core DSP (ECA + AHET) | `src/vitals.py` |
| Breathing-rate DSP | `src/respiration.py` |
| Radar I/O (incl. `iq_swap`) | `src/radar_io.py` |
| Masimo parser | `src/masimo.py` |
| Agreement metrics (MAE/RMSE/bias, overlay plot) | `src/compare.py` |
| Bland-Altman (bias, LoA, CIs) — the ONLY implementation | `scripts/plot_bland_altman.py` |
| Quality mask (Step 4 — gates windows for offline metrics) | `steps/step_4/add_quality_mask.py` |
| Radar hardware control | `steps/step_1/capture.py` |
| Session manifest (currently empty) | `data/manifest.local.csv` |
| Live run artifacts | `results/live_demo/<run_id>/` |
| Full project history | `HISTORY.md` (gitignored) |

### Run artifacts written per live run
`results/live_demo/<ts>_<mode>_<session>/` contains `run_metadata.json` (config, git commit,
packet stats), `warmup_bin_selection.json` (per-candidate bin-lock evidence, including
`energy_eligible`/`fallback_used`/settle-skip fields), `live_estimates.csv` (per-hop HR/BR +
rejection reasons), `live_intermediates.npz` (phase + spectra + AHET candidates, checkpointed
every 60 s — note `elapsed_s` inside it is wall-clock, see §6), and `adc_stream.bin` (the raw
mirror). `diagnose_live_run.py` reads all of these and prints a ranked verdict.
