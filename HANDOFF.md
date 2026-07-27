# Handoff — radar-vitals

> **Read this + `CLAUDE.md` and you can resume the project.** State **as of 2026-07-28**.
> Rewritten, not appended (CLAUDE.md §10). For *what happened and why*, read
> `HISTORY.md`. Every claim below was re-verified against the repo at the time of writing.
>
> **Scope pivoted this session.** The user does not have time to finish the M4
> manifest/M0 Zenodo-freeze path. **M4 Stage 1's cross-review is still OPEN and still has 5
> items unbuilt — it is deprioritized, not closed or abandoned.** The active work is now:
> (1) implement + compare the two theoretical reference papers (M8 Ahmed, M9 Kotte), (2) raise
> HR coverage. See §3.

---

## 1. Project snapshot

Estimate **heart rate and — co-equally — breathing rate** from a 77 GHz FMCW radar
(TI IWR1642BOOST + DCA1000EVM) for a subject seated 0.8–1.4 m from the sensor, chest facing the
radar. Ground truth is a Masimo MightySat pulse oximeter (1 Hz CSV: `Beats / min` = PR for heart,
`Breaths / min` = RR for respiration). The DSP extracts chest-wall phase from an auto-locked
range bin, cancels respiration harmonics (ECA) and verifies the cardiac peak via a second-harmonic
check (AHET). Output: journal paper + thesis chapter.

**Given the time constraint, the paper's likely shape has changed:** less pre-registered
confirmatory study, more (a) simulation-based reproduction/adaptation/transfer of the two
reference papers (both are simulation-only in their own right — meeting them on their own
ground needs no new data) and (b) a single-subject-plus-a-few-more feasibility result on
coverage, rather than the original 10-subject M0-frozen design. Not yet formally re-scoped in
`plans/implementation_plan.md` — that document still describes the original full design and
should be read with this pivot in mind, not treated as current without cross-checking §3 below.

**Method rationale, literature and algorithm spec: `notes/approach.md`.**
**Whole-project milestone plan (pre-pivot, needs a re-read against §3): `plans/implementation_plan.md`.**

---

## 2. Current state

**Branch `vital_signs_v9c`, HEAD `81c1a9f`, being pushed as part of this update —
`origin/vital_signs_v9c` should match after this session's push completes.**
Suite **1695 passed, 1 skipped, 0 failed** (`conda run -n radar-vitals python -m pytest tests/ -q`,
2026-07-28) — 1616 baseline + 79 (the bin-drift diagnostic's own tests, grown across three
post-implementation review rounds: 34 shipped initially, +8 in round 4, +31 in round 5, +14 in
round 6 [BDR-14 R2, BDR-19 R2, BDR-20…23] — see HISTORY.md 2026-07-28 entries for the full
account). v9 UI work remains parked in `git stash@{0}`; relocking/display-holdover stay reverted
(HISTORY.md 2026-07-09).

**This session's work is committed** (one commit, `81c1a9f`, on top of the previous session's
`8379a87`: the round-6 review-driven fix — code + tests + plan doc + cross-review debate log, all
together — plus this HISTORY/HANDOFF update as a second commit). `results/` (gitignored, as
always) gained one new run directory this session, `results/diagnose/bin_drift/20260727T215319Z/`
— not tracked, not part of these commits.

### M4 (deprioritized, not touched this session — state unchanged from before the pivot)

| Stage | State |
|---|---|
| **0** — shared-callable refactor | **DONE**, review **CLOSED** (`plans/m4_stage0_refactor_review.md`) |
| **1** — manifest schema + validation | **BUILT**, review **OPEN, 5 items unbuilt** (S12R-22, S12R-24, S12R-05 R3, S12R-12 R3, S12R-21's artifact binding) |
| **2** — window grid | **BUILT** (`src/m4/window_grid.py`), review **OPEN** |
| **3–8, M0 freeze** | **not started / on hold** — no active work planned; resume only if scope allows |

`src/m4/` contains only `__init__.py`, `manifest.py`, `window_grid.py`. No paper-grade number
exists from M4.

---

## 3. Active task / next steps

### 3.1 Bin-drift diagnostic: check the review loop FIRST, before anything else

**This is almost certainly the immediate next action for a new chat.** The Codex cross-review
(`plans/bin_drift_diagnostic_cross_review.md`) is an active, ongoing loop — it has already
reopened three times *after* implementation (rounds 4, 5, and 6) and found real code bugs each
time, not just wording issues. Do not assume the loop is closed without checking:

1. Read `plans/bin_drift_diagnostic_cross_review.md`'s `COMMENTS OF CODEX` section (near the
   top). **As of this writing it reads `(round 6 processed — see DEBATE COMMENTS. Awaiting Codex
   round 7.)`** — round 6's 6 findings (`BDR-14 R2`, `BDR-19 R2` reopened; `BDR-20`...`BDR-23`
   new) were verified against the shipped code, all confirmed real, fixed, tested, committed
   (`81c1a9f`), and re-run on all 4 real captures — see HISTORY.md 2026-07-28 for the full
   account. **If Codex has posted round 7 findings by the time you read this, `COMMENTS OF CODEX`
   will show them instead of the placeholder above — process them before anything else in
   §3.2–3.4.** If it still shows the placeholder, round 7 has not landed yet; check again before
   assuming the loop is closed.
2. **Process a new round** using
   `plans/bin_drift_diagnostic_claude_review_loop_prompt.md` as the procedure — this is a
   **post-implementation** loop: findings can be real bugs in `scripts/diagnose_bin_drift.py`, not
   just plan text, and processing one means verify against the actual running code/real output →
   apply the code fix → add a test that would have caught it → run
   `tests/test_diagnose_bin_drift.py` then the full suite → once the round's batch is done,
   **commit** (the diagnostic's own clean-tree gate requires this) → **re-run on all 4 real
   captures** (paths in §6 below) → spot-check the changed `summary.json` fields against real
   output → append `HISTORY.md` + rewrite `HANDOFF.md` (new `run_id`, new commit hash) → commit
   and push. This exact cycle has now happened three times (rounds 4, 5, 6) — follow the same
   pattern, don't improvise a lighter one.
3. **If `COMMENTS OF CODEX` says `NO MORE COMMENTS`** with no open items in `DEBATE COMMENTS`,
   the review is closed. Do **not** treat that as a cue to build something new — implementation is
   already done. The remaining task becomes the human decision in the paragraph below, which is
   not part of the review loop.

A range-bin drift diagnostic was designed, cross-reviewed with Codex (6 real rounds — the review
reopened **three times** after implementation: round 4 found the decided centroid-drift grid was
never wired up; round 5 found the diagnostic's stated core measurement — per-bin energy at two
time scales — had been approximated (mode/mean of 1 s blocks) rather than computed directly at
the 600-frame window scale, plus a "trailing 10 s" arithmetic bug, decode geometry validated
against the wrong metadata file, and motion energy computed for the whole capture instead of per
window; round 6 found the round-5 window-scale energy fix still discarded the profile before
persisting it, replay-to-capture matching bound only raw bytes (not the estimator generation),
the frame_idx validator still accepted a misanchored/truncated grid, `baseline_rank_of_locked_bin`
was sourced from the wrong profile, per-session summaries lacked their own provenance, and the
centroid-drift support span was hardcoded outside the config. All fixed and re-verified against
real data; `plans/bin_drift_diagnostic_cross_review.md`), implemented
(`scripts/diagnose_bin_drift.py`), and **run on all 4 real captures four times** as each round's
fixes changed what was actually being measured (or, in round 6's case, what was persisted and
provenanced).

**Current evidence: `results/diagnose/bin_drift/20260727T215319Z/`** — all three earlier runs
(`20260727T192643Z`, `20260727T195535Z`, `20260727T210936Z`) are superseded (kept on disk, do not
cite any of them; round 6 changed persistence/validation/provenance, not the underlying
measurement — every top-level statistic printed to console (`baseline_argmax_bin`, `locked_bin`,
`episode_count_at_grid`, `centroid_drift_at_grid`) is numerically identical between the round-5
and round-6 runs, confirmed by direct comparison. Cite the round-6 run: it is the only one whose
`summary.json` carries the round-6 provenance/rank fields, and the only one produced under the
now-enforced `approved_replays` generation binding). It measures whether the in-gate radar energy
profile drifts away from its settled warmup baseline over a session, and whether that's
associated with `gate_not_run` outcomes —
**deliberately an evidence summary, not an automatic verdict** (both open design choices from
review — sensitivity-grid framing and the `live_test1`-has-no-matched-replay scope question —
were resolved by the user as Option A on both: purely exploratory, no single threshold;
`live_test1` gets baseline-only evidence, no replay generated).

**What the evidence shows (read the summaries yourself before deciding):** all four sessions
show frequent short (<2 s) argmax flicker between the baseline bin and its neighbours; almost
none of that survives as a ≥5 s sustained episode (0 of 4 sessions except massimo2's 2); none
reach ≥10 s in any session; centroid drift (trailing-10s vs. first-post-calibration-10s median,
now using the corrected exact-10-block selection) meets the 0.3-bin grid point in massimo1 and
sweep, the 0.5-bin point in massimo1 only, and no session reaches 1.0 bin. **The
`duration_grid_by_outcome` report (new in round 5) shows ≥2 s off-baseline excursions are common
across *every* outcome class in massimo1** (`other_rejected` 26/26 windows, `gate_not_run`
11/13, `covered` 2/2), **but none reach 5 s in any class** — drift does not cleanly separate
`covered` from `gate_not_run`, which cuts against drift being *the* explanation for that
session's coverage loss. The new heatmap (`drift_overview.png`) makes this visually clear: the
chest energy visibly spans bins ~22–26 throughout, and the outcome strip below it shows
covered/gate_not_run/other_rejected windows interspersed rather than temporally clustered. This
leans toward "no sustained postural drift in these 4 single-subject sessions, and where drift
exists it doesn't cleanly track DSP outcome" but is n=1-subject evidence — **the next step is a
human decision, not more code**: read
`results/diagnose/bin_drift/20260727T215319Z/*/summary.json` and `drift_overview.png`, then
decide go/no-go on the 5-bin relock tracker.

### 3.2 Coverage: the mechanism is identified, the fix is not yet verified correct

- **Production ECA (`skip_forbidden_harmonics_v1`) is measured, not just documented, as inert**:
  0.000 dB median attenuation across 1001 in-band respiratory harmonics (3 sessions,
  post-filter-fix replays).
- **`guard_cardiac_candidate_v1` genuinely cancels** (−2.7 to −6.0 dB) **and raises coverage**
  (17.6→21.6%, 54.9→70.6%, 23.2→27.8%, bin pinned so `eca_mode` was the only variable) — proven
  in `experiments/exp_eca_modes/config_guard_v1.yaml`, **not promoted to
  `scripts/live_demo_config.yaml`**. The coverage gain was selected on mechanism + coverage
  only, never on Masimo agreement (CLAUDE.md §4) — **whether the extra covered windows are
  actually *correct* is unverified**, because there is no offline scoring script yet.
- **The real coverage bottleneck is AHET's `ratio_db_low` gate** (35–67% of attempted candidate
  slots; median shortfall 4.2–6.6 dB below the 1.0 dB requirement) — **not a threshold problem**
  (fixing ECA barely moved these counts) and **not fixable by relaxing the threshold** without
  disabling the gate. HA-as-an-HR-estimator (M8 plan step 3, additive across harmonics rather
  than pass/fail on one) is the most plausible structural fix, not yet tried.
- **massimo1's other major loss** (52% of its dead windows, `gate_not_run`) **is upstream of
  AHET and untouched by either fix** — `f_r_hz` invalid, cause not yet investigated.

**Next action:** build the minimal offline scoring script (replaces M4 Stages 3–8 for this
narrower scope — read `adc_stream.bin` → 30 s windows → shared DSP → align to Masimo on the
integer `Timestamp` → MAE/RMSE/coverage). Needed to (a) verify `guard_cardiac_candidate_v1`'s
coverage gain is correct before promoting it, and (b) score every M8/M9 comparison.

### 3.3 M8 / M9 (the other half of the pivot, not started)

- **M8 step 1a**: implement Ahmed et al.'s own signal model faithfully (single TX/RX, harmonics
  indexed on 2f_h/2f_b) and reproduce Fig. 8(c)–(d) on its own terms — **before** step 1b's
  adaptation to this project's all-harmonic phase formulation (`src/respiration.py:ha_estimate_rr`
  is already the adapted form; the faithful paper model does not exist yet). No hardware, no new
  data — pure simulation.
- **M9**: `n_R=4` cannot invert `R_t` as the paper's eq. (25) requires (`rank(R_t) ≤ 4`,
  singular) — a cheap, credible negative result once the 1×20-RX/`N_c=16` faithful reproduction
  control exists first (do that control before touching the 4-RX ablation).

### 3.4 Capture more subjects
Agreed: 3–5 additional subjects, still to be executed (none captured this session). No protocol
change from `notes/protocol.md`.

---

## 4. Decisions that matter (do not silently reverse)

### This session
- **M4/M0 deprioritized, not decided against** — the 5 unbuilt Stage-1 review items and the two
  frozen escalations (S12R-01, S12R-18) are exactly as HISTORY.md's last M4 entry left them.
  Resume from there if scope allows later; do not silently drop or "clean up" the open review.
- **BDR-04 (bin-drift): Option A, purely exploratory** — the diagnostic reports a frozen
  sensitivity grid (duration `{2,5,10}s` × centroid `{0.3,0.5,1.0} bin`) and **never** produces a
  single automatic drift/no-drift threshold. Do not add one later without a fresh user decision.
- **BDR-07 (bin-drift): Option A** — `live_test1` gets baseline-only evidence;
  `correlation_not_available` for its outcome table; **no replay of it was generated**, because
  the three existing comparison replays were made at commit `5537df5` with an unrecoverable
  dirty diff, and HEAD has moved well past that commit since (now `81c1a9f`) — re-running
  `scripts/live_demo.py` now would **not** reproduce a matched generation. **This is also now
  enforced in code, not just documented**: `scripts/diagnose_bin_drift_config.yaml`'s
  `approved_replays` pins each of the three real captures' raw SHA-256 to its one approved
  replay's `run_metadata.json` SHA-256 (BDR-20, HISTORY.md 2026-07-28 round-6 entry) — a
  regenerated replay with a different generation hash will be rejected by
  `match_replays_to_captures` until `approved_replays` is updated to the new hash.
- **The bin-drift diagnostic requires a clean tree** to produce citable evidence
  (`scripts/diagnose_bin_drift_config.yaml: provenance.require_clean_tree`) — a dirty-tree run is
  permitted (`--allow-dirty`) but is stamped `reproducible: false` in its own `summary.json` and
  must not be cited. The current `20260727T215319Z` run was from a clean tree (`reproducible: true`
  in every session's summary) — committed at `81c1a9f` before the run, per the diagnostic's own
  gate.
- **`PeakWorkingSetSize` is a process-lifetime high-water mark, and round 5 found the round-4
  memory figure was sampled at the wrong point** — before the (then whole-capture) motion-energy
  FFT ran, so it silently excluded that computation's own cost. The diagnostic now samples both
  post-decode and end-of-session; neither is a cleanly isolated per-session peak when multiple
  sessions run in one process (see the existing `mem_peak_working_set_*` gotcha below) — read
  both, don't treat either as the single authoritative number.
- **Sweep-capture memory is ~7 GB peak working set, not ~2.5 GB** — the smaller figure is the
  decoded cube's size, not the decode peak. Measured twice (a one-off scratch script: 6.96 GB;
  the committed diagnostic's own logger on a real run: 7.05 GB) — treat ~7 GB as the real
  precondition for any future script that decodes the sweep capture whole.

### Frozen earlier (still true, unchanged)
- **The quantile method is `linear`** (user, M4R-09). **Pass it explicitly at every call site.**
- **No frozen scoring number can come from the 4 existing captures**: they lack a persisted
  `frame0_epoch`. **`start_wall_utc` is NOT frame-0.**
- **M4's regression anchor is Option A** (user): the pilot MAEs are **retired, not reproduced**.
- **Window is 30 s; do not shorten.** Non-overlapping only for M4 scoring; `k = 0` **is** scored.
- **The `(12, 15, 18)` rotation is the STUDY allocation and binds SCORING only** (S12R-14).
  Development mode must load the real captures, including `massimo2`'s **16 bpm**.
- **Agreement CI: the whole-subject cluster bootstrap is PRIMARY** (M3R-29 Option A).
- **BR reference = Masimo `rr_bpm`**, cross-checked against the paced metronome.
- **Three capture classes:** *pre-freeze exploratory* (the 4 existing + M1), *post-freeze
  exploratory* (**M5**), *confirmatory* (**M6** onward) — this taxonomy still stands even though
  the M0 freeze itself is on hold.

### DSP decisions (unchanged)
- **The band-pass is a zero-phase order-4 Butterworth with an odd-reflected edge policy**
  (LFR-01/02). **Never reintroduce a rectangular/brick-wall mask.**
- **`filtfilt` and `np.linalg.qr` must never enter the live/M4 path or the tests** — the Windows
  LAPACK crash is non-catchable. `butter`/`freqz` are safe.
- **Never use total in-band power as an ECA cancellation metric** (LFR-06) — measure at the
  specific harmonic bins, as this session's ECA-inertness measurement did.
- **Breathing rate is a co-equal goal**, not a by-product.
- **The M2 validity invariant: a selection on the first in-band FFT bin can never be
  `resp_valid=True`.**

### Protocol / admin (unchanged)
- **Recordings are 10 minutes.** Ethics approval `24IBEC051`, IBEC KAUST.
- **Masimo intended-use disposition (M3R-34):** attended 10-min healthy-subject sessions, a
  verified battery. Cite the manual as `LAB-10168A`.
- **Both `literature/ref_papers/` methods are an OFFLINE COMPARISON ARM**, not production
  replacements (M8/M9, §3.3).

---

## 5. Gotchas / landmines

- **Run Python via `conda run -n radar-vitals`.** Conda is at
  `C:\ProgramData\anaconda3\condabin\conda.bat`, **not on PATH**. Invoking the env's `python.exe`
  by absolute path skips activation; matplotlib then can't find its render DLLs and `savefig()`
  hard-kills the interpreter with exit 127 and no traceback.
- **Multi-line `python -c` and stdin heredocs under `conda run` silently produce no output.**
  Write a scratch `.py` file and run that — hit repeatedly again this session.
- **`psutil` is NOT installed in the `radar-vitals` env.** Peak-memory measurement uses raw
  `ctypes` + `GetProcessMemoryInfo` (Windows-only; see `scripts/diagnose_bin_drift.py:get_peak_working_set_bytes`)
  instead. `GetProcessMemoryInfo`/`GetCurrentProcess` need explicit `argtypes`/`restype` set via
  `ctypes.wintypes` or the call silently fails (`GetProcessMemoryInfo failed` with no detail) —
  the default `ctypes` return-type assumption truncates the `HANDLE` on 64-bit.
- **`PeakWorkingSetSize` is a whole-process high-water mark, not per-call.** If a script measures
  it before/after each of several sequential operations in one process (as
  `diagnose_bin_drift.py` does across its 4 sessions), only the operation that sets a *new*
  process-wide high is cleanly isolated by the delta — earlier operations' deltas are polluted by
  whatever peak the process already reached. Not a bug, just what the counter means; a truly
  isolated per-operation measurement needs a fresh subprocess per operation.
- **Two replay generations exist and are NOT interchangeable**, and this bit twice this session.
  2026-07-26 dirs are post-filter-fix; 2026-07-25 dirs are pre-filter-fix. Beyond that: replays
  also carry a `git_commit` + `git_dirty` stamp in `run_metadata.json`, and a matching *nominal*
  generation label is not sufficient — the three 2026-07-26 comparison replays were made at commit
  `5537df5` with an uncommitted, unrecovered diff; re-running the same replay command later at a
  different commit does **not** reproduce that generation even though both would be called
  "2026-07-26-generation." Check `git_commit`, not just the directory date, before treating two
  replays as comparable.
- **Warmup bin selection consumes BR/HR, so estimator changes can move the lock** — confirmed
  live again this session: a free-warmup replay of massimo1 under `guard_cardiac_candidate_v1`
  locked bin 25, not the production lock 27.
- **`fallback_hr_bpm` is a LIAR** — a naive argmax, not the verified estimate. Use `hr_bpm_raw` /
  `candidate_refined_hz[0]`.
- **`packets_dropped` MAY exceed `packets_received`** — not a partition (S12R-13).
- **Masimo: `Beats / min` (PR) for heart, `Breaths / min` (RR) for respiration — never
  SpO2/PI/PVi.** Align on the integer `Timestamp` column, never `Date`/`Time` strings.
- **I/Q ordering depends on capture source**: SDK/Python captures need `iq_swap=True`; mmWave
  Studio captures `iq_swap=False`. All 4 existing captures are `iq_swap=True`.
- **`config.profile` has no `frame_rate_hz` key** — it's at `config.session.frame_rate_hz` in
  `run_metadata.json`'s recorded config snapshot (cross-checked against
  `1000/config.hw_frame.period_ms`). Got this wrong once this session before checking directly.
- **Respiratory-harmonic coincidence** (`notes/approach.md` §4.2): when `k·f_r ≈ HR`, ECA can
  cancel the cardiac signal with the harmonic.
- **`experiments/` now exists** (`experiments/exp_eca_modes/`, created this session) —
  the earlier "does not exist and cannot be restored from git" note is stale for `experiments/`;
  `figures/` still does not exist.

---

## 6. Data

**4 real captures, ~2.52 GB raw, all with saved `adc_stream.bin`** in `results/live_demo/`.
**Only 3 carry a Masimo reference**: `..._live_test1` (no Masimo, 120 s), `..._massimo1`
(natural, 180 s), `..._massimo2` (paced 16 bpm, 180 s), `..._sweep` (stepped 12→15→18→21 bpm,
480 s). **All from one subject, all exploratory.**

**Exact paths for re-running the bin-drift diagnostic** (§3.1) — 4 captures, 3 matched-generation
replays (`live_test1` has none, by design, BDR-07 Option A):
```
--captures results/live_demo/20260713_170323_live_demo_live_test1
           results/live_demo/20260713_172042_live_demo_massimo1
           results/live_demo/20260713_182002_live_demo_massimo2
           results/live_demo/20260714_180523_live_demo_sweep
--replays  results/live_demo/20260726_173434_replay_unknown
           results/live_demo/20260726_173653_replay_unknown
           results/live_demo/20260726_173914_replay_unknown
```
Full invocation: `conda run -n radar-vitals python -X utf8 scripts/diagnose_bin_drift.py --config
scripts/live_demo_config.yaml --diagnostic-config scripts/diagnose_bin_drift_config.yaml
--captures <4 paths above> --replays <3 paths above> --out results/diagnose/bin_drift`. Requires
a clean committed tree (§4) and ~7 GB available memory for the sweep session (§4).

`data/raw/` is **empty**;
`data/manifest.local.csv` is header-only (0 sessions); `figures/` does not exist.
**3–5 more subjects agreed, not yet captured (§3.4).**

**There are no citable results.** The old pilot MAEs (0.19/0.50/0.53 bpm) are **retired** — do
not quote or resurrect them. Coverage's mechanism is now understood (§3.2) but no MAE/RMSE
number exists for any ECA mode — the offline scoring script that would produce one does not
exist yet.

### Known broken / open (DSP side)
- **Coverage 17–55% in production; 21–71% with `guard_cardiac_candidate_v1`** (unpromoted, §3.2)
  — the dominant bottleneck is AHET's `ratio_db_low` gate, not ECA, and not fixable by threshold
  tuning (§3.2).
- **Bland–Altman is not implemented correctly for this study.**
  `scripts/plot_bland_altman.py` pools every window as an independent pair. **Plotting only.**
- **Stage 1B lag-10 veto** — verified on synthetics but blocked: zero baseline severe accepts
  exist post-bin-fix.
- **Genuine ≈6 bpm breathing at the edge bin is permanently `resp_valid=False`** — a deliberate,
  declared coverage sacrifice mandated by the M2 invariant.

---

## 7. The two reference papers (both simulation-only — a live opportunity, now the primary focus)

`literature/ref_papers/`, both IEEE Trans. Radar Systems vol. 2 (2024), both KAUST.

- **Paper 1 — Ahmed et al., "Discovering the Unseen"** (DOI 10.1109/TRS.2024.3412915). Harmonic
  Accumulation is already the primary BR estimator in `src/respiration.py`, but **adapted**
  (all-harmonic phase, not the paper's pulse-radar single-TX/RX 2f_h/2f_b model). **Never
  validated against Masimo.** **M8 step 1a must reproduce the paper on its own terms first**,
  before 1b's adaptation (§3.3) — this is unstarted.
- **Paper 2 — Kotte et al.** (DOI 10.1109/TRS.2024.3352189). Simulates 1 TX / 20 RX, `N_c = 16`;
  our board is 1 TX / 4 RX. `rank(R_t) ≤ 4` at `n_R = 4` makes `R_t⁻¹` (eq. 25) undefined —
  **M9 requires the faithful 1×20/`N_c=16` reproduction control first**, then a registered 4-RX
  ablation whose most likely outcome is a documented negative result (§3.3).

---

## 8. Pointers

| What | Where |
|---|---|
| Project rules (read first) | `CLAUDE.md` |
| Whole-project milestone plan (pre-pivot — read against §3) | `plans/implementation_plan.md` |
| **Bin-drift diagnostic — plan, review (round 6 processed, awaiting Codex round 7), run output** | `plans/bin_drift_diagnostic.md`, `plans/bin_drift_diagnostic_cross_review.md`, `results/diagnose/bin_drift/20260727T215319Z/` (current — `20260727T192643Z`, `20260727T195535Z`, `20260727T210936Z` are all superseded, kept but not citable) |
| **Bin-drift review-loop procedure (start here to resume the loop, §3.1)** | `plans/bin_drift_diagnostic_claude_review_loop_prompt.md` (your side), `plans/bin_drift_diagnostic_codex_review_prompt.md` (Codex's side — for reference / re-sending fresh, not something you run) |
| Bin-drift diagnostic code + tests | `scripts/diagnose_bin_drift.py`, `scripts/diagnose_bin_drift_config.yaml`, `tests/test_diagnose_bin_drift.py` |
| **Reusable cross-review prompt templates** (Codex + Claude loop sides) | `plans/codex_review_prompt_template.md`, `plans/claude_review_loop_prompt_template.md` |
| ECA-mode coverage experiment (unpromoted) | `experiments/exp_eca_modes/config_guard_v1.yaml` |
| M4 Stage 1/2 — manifest, window grid (built, review OPEN, deprioritized) | `src/m4/manifest.py`, `src/m4/window_grid.py` |
| M4 build plan / Stage 1+2 review record | `plans/m4_offline_harness.md`, `plans/m4_stage12_review.md` |
| Core DSP (Butterworth band-pass + ECA + AHET) | `src/vitals.py` |
| Breathing-rate DSP (HA + M2 peak-validity fix) | `src/respiration.py` |
| Window-level DSP composition (shared by live + M4) | `src/window_pipeline.py:run_window_dsp` |
| Warmup bin-lock + eligibility logic (shared) | `src/warmup_select.py:run_warmup_selection` |
| Radar I/O (4-RX cube, `iq_swap`) | `src/radar_io.py` |
| Masimo parser (PR, RR, PI) | `src/masimo.py` |
| Method, literature, algorithm spec | `notes/approach.md` |
| Pre-registered HR/BR comparators (binding, frozen-in-effect, not frozen-for-real) | `notes/comparator_prespec.md`, `notes/comparator_prespec_br.md` |
| Capture inventory (hashes, arms, rates) | `notes/capture_inventory.md` |
| Capture protocol / SOP | `notes/protocol.md` |
| Live/replay demo and capture tool | `scripts/live_demo.py` |
| Run post-mortem ("why was HR blank?") | `scripts/diagnose_live_run.py` |
| Thesis chapter / paper planning | `THIRD_CHAPTER.md`, `JOURNAL_PAPER.md` |
| Reference papers (simulation-only, M8/M9 — now the active focus) | `literature/ref_papers/` |
| Full project history | `HISTORY.md` |

### Run artifacts written per live run
`results/live_demo/<ts>_<mode>_<session>/` contains `run_metadata.json` (config incl.
`git_commit`/`git_dirty`, packet stats), `warmup_bin_selection.json` (per-candidate bin-lock
evidence), `live_estimates.csv` (diagnostic only, never a scoring input),
`live_intermediates.npz` (phase + spectra + AHET candidates + M2 respiration evidence) and
`adc_stream.bin` (raw mirror — this is every diagnostic's and M4's radar input).

### Run artifacts written per bin-drift diagnostic run
`results/diagnose/bin_drift/<run_id>/<session>/` contains:
- `bin_energy_blocks.csv` — per-block, full per-bin energy matrix (raw + baseline-relative dB)
- `window_audit.csv` — per-window, `window_argmax_bin`/`window_centroid` computed directly on
  each window's own 600-frame slice (not derived from blocks, since round 5)
- `motion_energy_windows.npz` — real `(n_windows, n_bins)` matrix, empty for `live_test1`
- `window_energy_windows.npz` — **new in round 6 (BDR-14 R2)** — the ordinary (non-motion)
  per-window per-bin energy profile `align_windows` computes to derive `window_argmax_bin`/
  `window_centroid`, persisted as its own `(n_windows, n_bins)` matrix (`window_indices`, `bins`,
  `matrix`, `matrix_rel_baseline_db`) rather than discarded — every saved
  `window_argmax_bin`/`window_centroid` is exactly recomputable from this file, verified against
  the real 2026-07-28 round-6 run
- `summary.json` — **each session's own copy of the run manifest, new in round 6 (BDR-22)**:
  `run_id`, `git_commit`, `diagnostic_config_path`/`_sha256`, `live_demo_config_path`/`_sha256`
  (previously only `run_summary.json` at the parent level had these). Plus: baseline profile +
  `occupancy`, warmup-recompute check, episodes, `episode_count_at_grid` (session-level,
  outcome-blind) + `centroid_drift_at_grid` (session-level, support now config-bound via
  `centroid.summary_span_s`, round 6 BDR-23) + `duration_grid_by_outcome` (per-window,
  per-outcome-class, round 5), `outcome_stratified_report` (full-exposure + transitional, with
  normalized fraction), `offset_phase_report` (all 10 hop-offset phases), provenance
  (`capture_run_metadata_sha256` / `replay_run_metadata_sha256` as two distinct hashes),
  `baseline_rank_of_locked_bin` (round 6 BDR-21: now sourced from the settled baseline profile,
  not the full-buffer warmup-JSON rank) alongside `full_buffer_warmup_rank_of_locked_bin` (the
  full-buffer rank, kept under its own name), `reproducible` flag, and three memory fields
  (`mem_available_preflight`, `mem_peak_working_set_after_decode`,
  `mem_peak_working_set_after_session`)
- `drift_overview.png` — a real heatmap (dB rel. per-block max) with baseline/argmax/centroid
  overlay and a window-outcome strip (since round 5; was a 2-line plot before)

`<run_id>/run_summary.json` aggregates all sessions in that run. As of round 6, a replay is only
accepted for a capture if its `run_metadata.json` SHA-256 matches the entry registered for that
capture's raw hash in `scripts/diagnose_bin_drift_config.yaml`'s `approved_replays` (BDR-20) — see
the gotcha in §5 above before regenerating any replay.
