# Handoff — radar-vitals

> **Read this + `CLAUDE.md` and you can resume the project.** State **as of 2026-07-24**.
> Rewritten, not appended (CLAUDE.md §10). For *what happened and why*, read `HISTORY.md`.
> Every claim below was re-verified against the repo at the time of writing.
>
> **The next chat's job is to plan M0** — see §3. Read `plans/implementation_plan.md` first.

---

## 1. Project snapshot

Estimate **heart rate and — co-equally — breathing rate** from a 77 GHz FMCW radar
(TI IWR1642BOOST + DCA1000EVM) for a subject seated 0.8–1.4 m from the sensor, chest facing the
radar. Ground truth is a Masimo MightySat pulse oximeter (1 Hz CSV: `Beats / min` = PR for heart,
`Breaths / min` = RR for respiration). HR agreement is scored under a **pre-registered comparator**
(`notes/comparator_prespec.md`) — binding on every HR number in the paper. **BR has no comparator
spec yet; writing one (M3) is a blocking task and the only thing M0 waits on.** The DSP extracts
chest-wall phase from an auto-locked range bin, cancels respiration harmonics (ECA) and verifies
the cardiac peak via a second-harmonic check (AHET). Output: journal paper + thesis chapter.

**Method rationale, literature and algorithm spec: `notes/approach.md`.**
**Whole-project milestone plan: `plans/implementation_plan.md`** — read this before starting work.
It survived a full cross-model review on 2026-07-24 (19 comments, 3 rounds) and is the scope
authority for every milestone.

---

## 2. Current state

**Branch `vital_signs_v9c`, HEAD `b6f5b73`.** Test suite **796 passed, 1 xfailed, 0 failed**
(`conda run -n radar-vitals python -m pytest tests/ -q`, verified 2026-07-24). That is **797
outcomes, not 797 passing tests** — the xfail is a known design hole in the ECA/AHET decoy case.
v9 UI work remains parked in `git stash@{0}`; relocking/display-holdover stay reverted.

**Uncommitted working tree:** `M HANDOFF.md` · `M HISTORY.md` · `D figures/.gitkeep` ·
`?? JOURNAL_PAPER.md` · `?? THIRD_CHAPTER.md` · `?? plans/`. Note `notes/` is **gitignored**, so
recent edits to `notes/protocol.md` are on disk with **no version history** — see §5.

**Ethics approval is IN HAND and its scope is now settled** (user-confirmed 2026-07-24): it covers
**M7's subject-specific collision manoeuvre** and permits **recordings up to 10 minutes**. Recorded
on the user's authority — the approval document is not in this repo. **Still to record: the
approval reference number and issuing board** (needed for the Methods section, and for M0).

### What is built and trustworthy
Raw ADC I/O with `iq_swap` handling · range FFT · static clutter removal · phase extraction and
unwrap · **warmup range-bin lock with the energy-eligibility fix** (committed `863600e` + `dfe7fb5`,
3× cross-reviewed) · ECA + AHET heart-rate core · breathing-rate DSP · Masimo parser · quality mask ·
live demo with capture, replay, raw mirroring and diagnostics · the HR comparator spec. All under
test.

### Data
**4 real captures, ~2.52 GB raw, all with saved `adc_stream.bin`** in `results/live_demo/`.
**Only 3 carry a Masimo reference** — this distinction matters and was previously stated wrong:
`20260713_170323_..._live_test1` (**no Masimo**), `20260713_172042_..._massimo1` (natural),
`20260713_182002_..._massimo2` (paced 16), `20260714_180523_..._sweep` (stepped 12→15→18→21 bpm,
486 s). **All from one subject, and all exploratory** — they informed the method's design, so they
can never be confirmatory evidence.
Five `*_replay_unknown` folders are **reprocessing artefacts, not sessions**.
`data/raw/`, `data/processed/`, `experiments/` and `figures/` are **empty or absent**;
`data/manifest.local.csv` is header-only (0 sessions).

### Best current results — PRELIMINARY, not citable
Scored strictly under `notes/comparator_prespec.md` at the corrected bin:

| session | AHET-accepted | scorable | MAE | severe (>5 bpm) |
|---|---|---|---|---|
| natural | 5/50 | 1 | 0.19 bpm | 0 |
| paced16 | 23/50 | 21 | 0.50 bpm | 0 |
| sweep | 30/150 | 19 | 0.53 bpm | 0 |

**Why not citable:** n=1 subject; exploratory, not held-out; `natural` rests on a single scorable
window; coverage is 10–46%. **Accuracy is not the problem — coverage is.** Always report both.

### Known broken / open
- **Respiration collapse** — `f_r` pins to the 0.1 Hz (6 bpm) search floor while `resp_valid` stays
  `True`. **3-for-3 on the Masimo-referenced captures** (`massimo1` 12 hops, `massimo2` 5,
  `sweep` 10); **absent** from the unreferenced `live_test1`. Measured from each run's
  `live_estimates.csv`. **Blocker, because BR is a goal.**
- **Coverage 10–46%** — AHET conservatism at the correct bin is the bottleneck.
- **Bland–Altman is not implemented correctly for this study.** `scripts/plot_bland_altman.py`
  pools every window as an independent pair (`se_loa = sqrt(3·SD²/n)`, no subject/session term).
  For 10 subjects × 2 sessions it gives falsely narrow limits, and it was never valid for the n=1
  pilot either. **Reuse for plotting only.**
- **Candidate ranking** — magnitude order ≠ credibility order. Confirmed, n=1, no plan written.
- **Stage 1B lag-10 veto** — designed, scaffolded, verified on synthetics, but **blocked**: zero
  baseline severe accepts exist post-bin-fix, so the veto has nothing to veto. Unblocked only by M7.
- **ECA v1** (`skip_forbidden_harmonics_v1`, the live mode) removes **0.00 dB** in the cardiac band
  at low breathing rates — production harmonic cancellation is effectively inert.
- **`guard_cardiac_candidate_v1`** implemented and unit-tested but **not promoted** — blocked on
  `experiments/exp_eca_modes`, and `experiments/` does not exist.
- **`elapsed_s` is wall-clock** in replay NPZs under `--replay-fast`.
- **Cross-model review of the linalg-free DSP path** (FFT masking replacing `filtfilt`; modified
  Gram–Schmidt replacing `np.linalg.qr`) has **not** been done. It is now a **prerequisite of M4**,
  not housekeeping — M4 produces every paper-grade number and executes that path.

---

## 3. Active task / next steps — **plan M0**

**M0 is the pre-registration freeze and deposit. It is a hard gate: no study capture (M5 onward)
may happen before its DOI exists.** Its full specification is `plans/implementation_plan.md` §M0;
this section is only what a planning chat needs to get moving.

**M0 depends on M3 alone** (the BR comparator must exist to be frozen with it). M3 depends on
nothing and can start immediately.

### What the deposit must contain
1. `notes/comparator_prespec.md` (HR, exists) + the **new BR comparator** (M3, to be written) +
   `notes/protocol.md`.
2. **Four analysis decisions**, none of which the study can be re-run to fix:
   - **Agreement model** — subject-clustered repeated-measures Bland–Altman; name the clustering
     level, variance components, CI method, and handling of unequal window counts per subject.
   - **Evidence floor and precision target** — **STILL OPEN, see below.**
   - **Method-comparison discipline** — ECA+AHET is the pre-declared **primary** estimator and HR
     MAE-with-coverage the primary endpoint; HA (M8), joint-Doppler (M9) and the baselines (M10)
     are **secondary and exploratory**. Fix tuning-vs-evaluation data and multiplicity handling.
   - **Amendment mechanism** — how a post-deposit change is versioned, justified, cross-reviewed
     and re-deposited.
3. **An enumeration of every capture existing at freeze time**, labelled exploratory.
4. The **ethics approval reference number and issuing board**.

### The one decision blocking M0
**The evidence floor** — the minimum number of *evaluable* (radar-accepted **and**
comparator-admissible, non-overlapping) windows per session and per subject, plus the agreement
precision the study claims, plus a **prospective** rule for what happens if the floor is missed.
Deferred by the user on 2026-07-24. **It cannot slip past M0: a floor chosen after seeing the pilot
yield is not a floor.**

The arithmetic that makes it urgent, at the measured 10–46% coverage:

| recording | windows/session | per subject (2 sessions) | after Masimo gates (−12–20%) |
|---|---|---|---|
| 5 min (former) | ~9 | 1.8 – 8.3 | ~1.5 – 7 |
| **10 min (current)** | **~19** | **3.8 – 17.5** | **~3 – 14** |

Going to 10 minutes roughly doubled the evidence and **that lever is now spent** — it sits at the
approval ceiling. If the floor is still missed, the only remaining responses are **more sessions**
or **a weaker claim**. Carried with this decision: whether to attack coverage (**M11a**) *before*
freezing, so an improved estimator can be the pre-registered primary rather than a post-hoc
footnote.

### Immediate actions, in order
1. **M1** — live hardware smoke test. Unscored, no dependencies, highest risk-reduction per minute;
   does **not** wait on M0. Run the **full 10 min**, so it doubles as the first check that the bin
   lock survives a long session.
2. **M2** — respiration-collapse root cause and fix. Offline, on the three Masimo-referenced
   captures. **A negative result closes it** — if the HA adaptation is simply invalid, that is the
   finding; do not tune until BR agrees.
3. **M3** — the BR comparator pre-spec. **Runs in parallel with M2, not after it.**
4. **M8 step 1a/1b** — the HA reproduction control, then the adaptation transfer test.
5. **M0** — freeze and deposit, before M5.

Also start early: the **linalg-free DSP cross-model review** (gates M4, hence M5/M8/M9/M10).

---

## 4. Decisions that matter (do not silently reverse)

- **Breathing rate is a co-equal goal**, not a by-product (2026-07-24).
- **Recordings are 10 minutes** (2026-07-24, decided *before* the deposit so no amendment is
  needed). **The approval ceiling is 10 min — do not exceed it.** Do not shorten either: 5 min
  yielded too few evaluable windows.
- **The pre-registration claim is "frozen before the confirmatory data", NOT "before any data
  existed."** The stronger version is unavailable — four captures already exist and already shaped
  the method. Overstating it in the paper repeats the error that forced the "MAE 0.16 bpm"
  withdrawal.
- **Three capture classes, and the distinction is load-bearing:** *pre-freeze exploratory* (the 4
  existing captures + M1's smoke test), *post-freeze exploratory* (**M5**, the pilot — it may change
  the rules, so it is excluded from confirmatory metrics), *confirmatory* (**M6** onward).
- **The BR comparator is designed from the reference alone.** Using radar agreement to sanity-check
  reference eligibility is the mirror image of the tuning CLAUDE.md §4 forbids.
- **BR reference = Masimo `rr_bpm`, cross-checked against the paced metronome rate.** Masimo RRp is
  pleth-derived, smoothed and laggy — a weaker gold standard than PR. Say so.
- **Both `literature/ref_papers/` methods are an OFFLINE COMPARISON ARM**, not production
  replacements. No promotion without a separate documented decision + cross-model review.
- **Paper 2 (Kotte) needs a paper-faithful control first.** See §6 — the "4-RX SIMO" premise was
  false.
- **Sequencing is fix-blockers → pilot → study → method work.**
- **Window is 30 s; do not shorten** (sets ~2 bpm HR resolution and bin-lock quality).
- **Statistics use non-overlapping 30 s windows only.** The 3 s hop shares 27/30 s of data.
- **Every HR agreement number goes through `notes/comparator_prespec.md`.** No exceptions.
- **Report coverage alongside accuracy, always.**
- **The live demo's readouts are NOT paper-grade** — metrics come from offline reprocessing of
  `adc_stream.bin`.
- **The bin-lock's −12 dB / 5 s thresholds are an empirical, scene-scoped prior** (4 sessions,
  1 subject). Re-validate on the study; untested with competing reflectors.
- **The 18 bpm paced arm is deliberately inside the failure zone** (4×18 = 72 bpm). Report it
  separately; never pool it into headline metrics.
- **Distance is descriptive, not inferential.** The protocol deliberately does not pin it, so a
  per-distance *comparison* is confounded with subject. Report it; do not claim agreement across it.
- **Do not implement the candidate-ranking fix ad hoc** — plan + cross-model review first.
- **Never promote a status tag in `THIRD_CHAPTER.md` by editing it** — re-measure, or leave it.

---

## 5. Gotchas / landmines

- **Run Python via `conda run -n radar-vitals`.** Conda is at
  `C:\ProgramData\anaconda3\condabin\conda.bat`, not on PATH. Invoking the env's `python.exe` by
  absolute path skips activation; matplotlib then can't find its render DLLs and `savefig()`
  hard-kills the interpreter with exit 127 and no traceback.
- **Multi-line `python -c` under `conda run` silently produces no output in this shell.** Write a
  scratch `.py` file and run that instead.
- **`notes/` is gitignored** (`.gitignore:17`, `notes*/`), so `notes/protocol.md` — which is a
  **pre-registration input** — has no version history. The 2026-07-24 `HISTORY.md` entry is
  currently the only dated record that the recording duration changed from 5 to 10 min.
  **Consider tracking `notes/protocol.md` before M0 deposits it.**
- **The 10-minute duration is enforced by operator discipline only.**
  `scripts/live_demo_config.yaml` has `max_live_duration_s: null` (run until Ctrl-C).
- **A longer session stresses the bin lock.** The warmup locks **one** range bin for the whole
  recording, so a posture shift at minute 8 corrupts the tail with no obvious symptom — the same
  silent-failure class as the 2026-07-14 mislock. **Check the lock at minute 9–10** with
  `scripts/diagnose_live_run.py`.
- **`elapsed_s` in replay NPZs is WALL-CLOCK**, ~0 under `--replay-fast`. Use
  `frame_idx / frame_rate_hz` for any replay timebase. Still unfixed in `live_demo.py`.
- **`fallback_hr_bpm` is a LIAR** — a naive argmax, not the verified estimate. Use `hr_bpm_raw` /
  `candidate_refined_hz[0]`.
- **The live display holds the last valid HR** — it can look like steady success while most hops
  are rejected. Always run `scripts/diagnose_live_run.py` before believing a live run.
- **M7's rate must NOT be computed from resting HR.** Pacing moves HR (this subject: ~65 → 72 bpm;
  80–88 in the sweep), which is why the sweep's real collision landed on the **21 bpm** step, not
  the designed 18. Set the rate from HR measured *while paced*.
- **A synthetic test asserting "candidate tracks harmonic order k" must first confirm k is
  admissible at that f_r** (`k*f_r` must land in 0.8–2.0 Hz). Choose `f_r` per `k`.
- **Masimo: `Beats / min` (PR) for heart, `Breaths / min` (RR) for respiration — never SpO2/PI/
  PVi for rate.** Align on the integer `Timestamp` column, never the `Date`/`Time` strings.
- **I/Q ordering depends on capture source**: SDK/Python captures need `iq_swap=True`; mmWave
  Studio captures `iq_swap=False`.
- **Respiratory-harmonic coincidence** (`notes/approach.md` §4.2): when `k·f_r ≈ HR`, ECA can
  cancel the cardiac signal with the harmonic. Identifiability problem — longer windows do not help.
- **`experiments/` and `figures/` do not exist.** `figures/` was deleted on 2026-07-24 along with
  three generated figure scripts; none were committed and none are recoverable from git. The
  numbers they encoded survive in `HISTORY.md` and `THIRD_CHAPTER.md` §9. Restore with
  `git checkout figures/.gitkeep` if rebuilding.
- **Only 6 `.md` files are tracked**: `AGENTS.md`, `CLAUDE.md`, `HANDOFF.md`, `HISTORY.md`,
  `config/set_DCA1000.md`, `notes/approach.md`. The rest of `notes/` and all of `results/` remain
  gitignored **by design**. The writing files and `plans/` are untracked but *not* ignored.

---

## 6. The two reference papers (both simulation-only — a live opportunity)

`literature/ref_papers/`, both IEEE Trans. Radar Systems vol. 2 (2024), both KAUST:

- **Paper 1 — Ahmed et al., "Discovering the Unseen"** (DOI 10.1109/TRS.2024.3412915). Its
  **Harmonic Accumulation is already the primary BR estimator** in `src/respiration.py`, but
  **adapted**: the paper is a **pulse radar, single TX / single RX** (its Fig. 1), built on **2f_h
  and 2f_b and their harmonics** — even-harmonic structure is intrinsic to its demodulated model —
  whereas ours is all-harmonic phase. **Never validated against Masimo.** Its Fig. 8(c)–(d) claims
  correct estimation *at* the 4·f_r = HR collision (f_h = 80/60 Hz, f_b = 20/60 Hz, f_c = 6.7 GHz,
  d_h = 10 mm, d_b = 20 mm, SNR 10 dB) — the project's central unsolved problem. **M8 must
  reproduce the paper on its own terms FIRST (step 1a), then run the adaptation (step 1b)**;
  otherwise a failure cannot be told apart from our own bug, and a success reproduces nothing.
- **Paper 2 — Kotte et al.** (DOI 10.1109/TRS.2024.3352189). **The "1 TX / 4 RX = the SIMO setup
  the paper assumes" claim was FALSE and is retracted.** Its §IV simulates **one TX and 20 RX** at
  24 GHz, 50 ms PRI, `N_c = 16`. Eq. (23) defines `Y_t ∈ C^(N_c×n_R)` and eq. (25) requires
  `R_t⁻¹` where `R_t = E{Y_t Y_tᴴ}` is **N_c × N_c** — the **RX channels supply the snapshots**, so
  at `n_R = 4`, `rank(R_t) ≤ 4` and `R_t` is **singular**. The slow-time mapping is also unresolved
  (its 16 chirps at 50 ms = a 0.8 s CPI; our vital-signs slow time is 600 frames over 30 s). Its
  Fig. 5 validates on generic moving-target Dopplers, **not HR/BR** — though its *motivation*
  (lungs masking the heart in one range bin) genuinely is ours. **M9 requires a paper-faithful
  1×20 / `N_c`=16 reproduction, then a registered 4-RX ablation** specifying covariance estimation,
  regularisation, rank checks and the timebase, **before any existing capture is touched.**

---

## 7. Pointers

| What | Where |
|---|---|
| Project rules (read first) | `CLAUDE.md` |
| **Whole-project milestone plan** | `plans/implementation_plan.md` (untracked) |
| Thesis chapter source | `THIRD_CHAPTER.md` (untracked) |
| Journal paper planning | `JOURNAL_PAPER.md` (untracked) |
| Method, literature, algorithm spec | `notes/approach.md` (tracked) |
| **Pre-registered HR comparator (binding)** | `notes/comparator_prespec.md` |
| Capture protocol / SOP (**10-min recordings**) | `notes/protocol.md` (gitignored — see §5) |
| ECA staged plan (v1/v2 dead) | `notes/plan_eca_forbidden_zone.md` |
| Stage 1B lag-10 design (draft 7) | `notes/note_stage1b_lag_statistic.md` |
| Candidate-ranking bug writeup (no plan yet) | `notes/note_candidate_ranking.md` |
| Reference papers (simulation-only) | `literature/ref_papers/` |
| Live/replay demo **and capture tool** | `scripts/live_demo.py` |
| Live demo config | `scripts/live_demo_config.yaml` |
| Standalone headless capture | `steps/step_1/capture.py` |
| Warmup bin-lock + eligibility logic | `scripts/live_demo.py:_run_warmup_selection` |
| Bin-lock validation (tracked, regenerable) | `scripts/validate_warmup_selection.py` |
| Run post-mortem ("why was HR blank?") | `scripts/diagnose_live_run.py` |
| Core DSP (ECA + AHET) | `src/vitals.py` |
| Breathing-rate DSP (incl. Harmonic Accumulation) | `src/respiration.py` |
| Radar I/O (4-RX cube, `iq_swap`) | `src/radar_io.py` |
| Masimo parser (PR, RR, PI) | `src/masimo.py` |
| Agreement metrics | `src/compare.py` |
| Bland–Altman — **plotting only**, statistics invalid for repeated measures | `scripts/plot_bland_altman.py` |
| Evidence run (Masimo, 180 s) | `results/live_demo/20260713_172042_live_demo_massimo1/` |
| Stepped-rate sweep (Masimo, 486 s) | `results/live_demo/20260714_180523_live_demo_sweep/` |
| Post-fix re-scoring of all 3 sessions | `results/live_demo/20260715_164018` (paced16), `_164124` (natural), `_164132` (sweep) `_replay_unknown/` |
| Full project history | `HISTORY.md` |

### Run artifacts written per live run
`results/live_demo/<ts>_<mode>_<session>/` contains `run_metadata.json` (config, git commit,
packet stats), `warmup_bin_selection.json` (per-candidate bin-lock evidence incl.
`settled_energy_db` / `energy_eligible` / `hr_bonus_vetoed`), `live_estimates.csv` (per-hop HR/BR
+ rejection reasons), `live_intermediates.npz` (phase + spectra + AHET candidates, checkpointed
every 60 s — `elapsed_s` inside is wall-clock, see §5) and `adc_stream.bin` (raw mirror).
