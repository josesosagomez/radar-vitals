# Handoff — radar-vitals

> **Read this + `CLAUDE.md` and you can resume the project.** State **as of 2026-07-28**.
> Rewritten, not appended (CLAUDE.md §10). For *what happened and why*, read
> `HISTORY.md`. Every claim below was re-verified against the repo at the time of writing.
>
> **Scope pivoted earlier this project.** The user does not have time to finish the M4
> manifest/M0 Zenodo-freeze path. **M4 Stage 1's cross-review is still OPEN and still has 5
> items unbuilt — it is deprioritized, not closed or abandoned.**
>
> **The offline scoring script is now BUILT, TESTED, and RUN FOR REAL (§3.1).**
> `src/comparator.py` + `scripts/score_offline.py` implement `plans/offline_scoring_script.md`
> revision 7 (the closed, cross-reviewed build authority). 64 new tests pass (18 + 46); full suite
> **1788 passed, 1 skipped** (was 1724 before this session, zero regressions). The plan's exact
> Verification §3 invocation has been run for real on all 3 Masimo captures — see
> `results/score_offline/20260728T154834Z/` for the first actual `guard_v1_only` incremental-
> coverage numbers (small-n, exploratory, `comparator_status: exploratory_non_frozen` — not a
> promotion verdict). **The active work for a new chat is now capturing more subjects (§3.4) and/or
> M8/M9 (§3.3)** — the guard_v1 promotion decision itself stays open pending more data. The
> bin-drift diagnostic's review is separately CLOSED (§3.2) and the 5-bin relock tracker go/no-go
> remains DEFERRED (Option C).

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

**Branch `vital_signs_v9c`** — last commit still `e55b207` at the time of writing (verified via
`git log -1`); **nothing from this session has been committed yet.** `git status --short` shows:
- Modified (tracked): `HANDOFF.md`, `HISTORY.md` (this rewrite/append).
- Untracked, uncommitted: `plans/offline_scoring_script.md`,
  `plans/offline_scoring_script_codex_review_prompt.md`,
  `plans/offline_scoring_script_cross_review.md` (from the prior session's plan review),
  **`src/comparator.py`, `scripts/score_offline.py`, `tests/test_comparator.py`,
  `tests/test_score_offline.py`** (this session's build).

**This session's full arc:** implemented `plans/offline_scoring_script.md` revision 7 exactly —
`src/comparator.py` (`hr_reference`/`br_reference`/`br_metronome_concordance`) and
`scripts/score_offline.py` (the full scoring loop) — wrote 64 new tests (18 comparator + 46
score_offline), and ran the plan's own Verification §3 invocation for real on all 3 Masimo
captures. **Full suite: 1788 passed, 1 skipped** (`conda run -n radar-vitals python -m pytest
tests/ -q`) — up from 1724 before this session, zero regressions, verified directly (not carried
forward). See HISTORY.md's 2026-07-28 "Offline scoring script: implemented, tested, and run for
real" entry for the full account, including the one bug the first real run surfaced (evidence.npz
assumed every window's `run_window_dsp` dict had an identical key set; `ahet_second_harmonic_hz`
is only present when a candidate is accepted — fixed by keying on the union of keys across
windows, not window 0's alone) and a reproducibility gap in `--out` handling found by running the
plan's own Verification §3 command twice (fixed to always append a `run_id` timestamp
subdirectory, mirroring `scripts/diagnose_bin_drift.py`).

**§3.1 below is fully rewritten from the prior "implement this" framing to the current "it's
built, tested, and run — here's what the first real numbers say and what's next" state.**

### M4 (deprioritized, not touched this session — state unchanged from before the pivot)

| Stage | State |
|---|---|
| **0** — shared-callable refactor | **DONE**, review **CLOSED** (`plans/m4_stage0_refactor_review.md`) |
| **1** — manifest schema + validation | **BUILT**, review **OPEN, 5 items unbuilt** (S12R-22, S12R-24, S12R-05 R3, S12R-12 R3, S12R-21's artifact binding) |
| **2** — window grid | **BUILT** (`src/m4/window_grid.py`), review **OPEN** |
| **3–8, M0 freeze** | **not started / on hold** — no active work planned; resume only if scope allows |

`src/m4/` contains only `__init__.py`, `manifest.py`, `window_grid.py`. No paper-grade number
exists from M4. Note: `src/m4/window_grid.py` is directly reused (imported, not reimplemented) by
the new offline scoring plan's `build_window_grid` calls — the two are not independent tracks.

---

## 3. Active task / next steps

### 3.1 Coverage: offline scoring script — BUILT, TESTED, RUN FOR REAL

**`plans/offline_scoring_script.md` revision 7 is fully implemented.** `src/comparator.py`
(`hr_reference`/`br_reference`/`br_metronome_concordance`, every primary gate threshold an
internal module constant per OSR-10 — verified: neither function accepts a threshold kwarg) and
`scripts/score_offline.py` (the full CLI + scoring loop, all flags named in the plan implemented
and exercised) exist, pass 64 new tests, and have been run for real on all 3 Masimo captures with
the plan's exact Verification §3 invocation. Nothing here is speculative — every claim below was
re-run or re-read against the actual repo state while writing this.

**What exists now:**
1. `src/comparator.py` — `tests/test_comparator.py`, 18 tests, all passing, including the spec's
   own worked stationarity example and both HR (5.0 bpm)/BR (2.0 bpm) strict boundaries at exactly
   the threshold vs. threshold+0.000001.
2. `scripts/score_offline.py` — `tests/test_score_offline.py`, 46 tests, all passing, including the
   plan's own real-fixture negative cases (massimo1's `20260727_182319_replay_unknown` for the
   OSR-03 R3 same-hash-different-`eca_mode` rejection; the original massimo1 directory for the
   OSR-03 round-4 wrong-lock-value rejection) and a structural slice-equivalence check
   (`run_window_dsp` on an ndarray slice vs. a `collections.deque` of the same frames is
   bit-identical).
3. **Full suite: 1788 passed, 1 skipped** (`conda run -n radar-vitals python -m pytest tests/ -q`),
   up from 1724 before this session — the +64 are exactly this session's new tests, zero
   regressions, re-verified directly.
4. **The plan's Verification §3 invocation has been run for real** (2026-07-28) on all 3 Masimo
   captures, both configs (`production`/`guard_v1`), both estimands, `--isolate-fields
   heart.eca_mode`, both `--reproduction-baseline-*` checks passing (each pinned lock source
   confirmed to really be the 2026-07-26 replay generation at locks **27/26/26**, not the original
   captures' own 23/20/21). Output: `results/score_offline/20260728T154834Z/` (gitignored —
   `results*/` — not committed; re-runnable from the exact command in §6 below).

**Why this exists and what it answers, unchanged from before:**
- **Production ECA (`skip_forbidden_harmonics_v1`) is measured, not just documented, as inert**:
  0.000 dB median attenuation across 1001 in-band respiratory harmonics (3 sessions,
  post-filter-fix replays).
- **`guard_cardiac_candidate_v1` genuinely cancels** (−2.7 to −6.0 dB) **and raises coverage**
  (17.6→21.6%, 54.9→70.6%, 23.2→27.8%, bin pinned so `eca_mode` was the only variable) — proven
  in `experiments/exp_eca_modes/config_guard_v1.yaml`, **not promoted to
  `scripts/live_demo_config.yaml`**. The coverage gain was selected on mechanism + coverage
  only, never on Masimo agreement (CLAUDE.md §4).
- **The real coverage bottleneck is AHET's `ratio_db_low` gate** (35–67% of attempted candidate
  slots; median shortfall 4.2–6.6 dB below the 1.0 dB requirement) — **not a threshold problem**
  and **not fixable by relaxing the threshold** without disabling the gate. HA-as-an-HR-estimator
  (M8 plan step 3) is the most plausible structural fix, not yet tried.
- **massimo1's other major loss** (52% of its dead windows, `gate_not_run`) is upstream of AHET
  and untouched by either fix — `f_r_hz` invalid, cause not yet investigated.

**The first real `guard_v1_only` incremental-coverage numbers (Step 11 — the direct answer to "is
the extra covered evidence also correct," not `paired_metrics`'s intersection, which structurally
excludes it):**
- Pinned estimand, sweep capture: 1 window in `guard_v1_only` (of 8 reference-admissible windows)
  — MAE/RMSE 0.446 bpm, bias −0.446 bpm.
- Rerun-warmup estimand, massimo1: 2 windows in `guard_v1_only` — MAE 0.567 bpm, RMSE 0.618 bpm.
- Rerun-warmup estimand, massimo1: 1 window in `production_only` — MAE 0.418 bpm.
- massimo2 (both estimands): `guard_v1_only`/`production_only` both empty; 4 windows `both_pass`
  agree to within ~0.0002 bpm between configs.
- Full detail (all 3 captures × both estimands × all 4 buckets):
  `results/score_offline/20260728T154834Z/<capture>/comparison_<estimand>.json`.

**Read this as a first real, honest data point — not a result.** Every bucket above has **n≤2**,
one subject, and every number is stamped `comparator_status: exploratory_non_frozen` (§4 below,
`start_wall_utc`-approximate origin, OSR-01). It is nowhere near enough to conclude the coverage
gain is also an accuracy gain — only that what little data exists so far does not contradict it.
**Do not cite these numbers as a promotion decision for `guard_cardiac_candidate_v1`.** The honest
next step is more subjects (§3.4), not a verdict on n≤2.

**Re-running the script:** see §6 for the exact command (identical to the plan's Verification §3,
pinned lock sources at the 2026-07-26 replay dirs, both `--reproduction-baseline-*` flags set).

### 3.2 Bin-drift diagnostic: review CLOSED, tracker decision DEFERRED (Option C, 2026-07-28)

**The Codex cross-review is CLOSED** (round 11, 2026-07-28): `plans/bin_drift_diagnostic_cross_review.md`'s
`COMMENTS OF CODEX` reads the literal string `NO MORE COMMENTS`, independently re-verified (every
`DEBATE COMMENTS` item is `RESOLVED` or `applied` with no open escalation) rather than taken on
Codex's word alone. `plans/bin_drift_diagnostic.md`'s header is marked `REVIEW CLOSED`. The loop
ran 10 real rounds (BDR-01 through BDR-25 R2), reopened seven times after implementation, and
twice found a real bug in a *previous round's own fix* on the same function
(`classify_window_outcome`) — full round-by-round account in HISTORY.md 2026-07-28 entries; no
need to re-read it unless the diagnostic is touched again.

**The go/no-go decision on building the 5-bin relock tracker (locked±2, radar-only scoring,
hysteresis, window-boundary switching) is DEFERRED — the user's explicit choice, 2026-07-28
(Option C of three discussed: go now / defer / no-go).** Coverage (§3.1) takes priority; revisit
this decision later if time allows, ideally after the §3.4 subjects are captured — this
diagnostic's evidence is n=1 subject who moved very little, and a subject who moves more during a
session would be a more informative test than deciding on this data alone. **Do not build the
tracker without revisiting this decision first** — the reverted 2026-07-02 implementation still
sits, working, in `git stash@{0}` (~370 lines in `scripts/live_demo.py` + ~560 lines of tests; not
a from-scratch build, but stale enough now — predates M4, this diagnostic, and ECA/AHET changes —
that reviving it means a port + re-validation, not a clean `git stash pop`).

**Evidence, if you do revisit:** `results/diagnose/bin_drift/20260728T004453Z/*/summary.json` and
`drift_overview.png` (all earlier run directories are superseded — the round-7 run is additionally
wrong, not just stale, due to the BDR-02 R3 bug fixed in round 8; do not cite it). Headline: all 4
sessions show frequent short (<2s) argmax flicker but almost no sustained (≥5s) drift (0 of 4
sessions except massimo2's 2 episodes; none reach ≥10s); centroid drift crosses 0.3 bin in
massimo1/sweep, 0.5 bin only in massimo1, never 1.0 bin. In massimo1, ≥2s excursions occur across
*every* outcome class (`covered` 2/2, `gate_not_run` 17/19, `other_rejected` 20/20) but none reach
5s in any class — drift does not cleanly separate good windows from bad ones, which is the main
reason the decision leans toward eventual no-go rather than go, independent of the n=1 caveat.

To re-run the diagnostic once new subjects are captured, see §6 for the exact invocation.

### 3.3 M8 / M9 (the other half of the pivot, not started)

- **M8 step 1a**: implement Ahmed et al.'s own signal model faithfully (single TX/RX, harmonics
  indexed on 2f_h/2f_b) and reproduce Fig. 8(c)–(d) on its own terms — **before** step 1b's
  adaptation to this project's all-harmonic phase formulation (`src/respiration.py:ha_estimate_rr`
  is already the adapted form; the faithful paper model does not exist yet). No hardware, no new
  data — pure simulation. **`scripts/score_offline.py` now exists (§3.1)** — its reuse surface
  (`as_window_estimate`/`WindowEstimate`) is ready for a non-AHET estimator to enter the same
  scoring path; wiring a new estimator through it is unstarted work, not a blocked one.
- **M9**: `n_R=4` cannot invert `R_t` as the paper's eq. (25) requires (`rank(R_t) ≤ 4`,
  singular) — a cheap, credible negative result once the 1×20-RX/`N_c=16` faithful reproduction
  control exists first (do that control before touching the 4-RX ablation).

### 3.4 Capture more subjects
Agreed: 3–5 additional subjects, still to be executed (none captured this session). No protocol
change from `notes/protocol.md`.

---

## 4. Decisions that matter (do not silently reverse)

### This session — the offline scoring script's 6 cross-review user decisions
All recorded in `plans/offline_scoring_script.md`'s "User decisions" sections; do not silently
revisit any of these when implementing — they were each escalated by Codex specifically because
they are judgment calls, not engineering defaults.
- **Approximate origin (OSR-01):** the script runs on `start_wall_utc` (no capture has a true
  `frame0_epoch`), but **every artifact is stamped `origin_is_approximate`/
  `comparator_status: exploratory_non_frozen`** and the result is reported as **provisional
  evidence**, never a final/frozen ruling — "actual answer" language is explicitly banned from
  the plan's own output design.
- **Both estimands, always (OSR-04):** pinned-bin (isolates `eca_mode` as the only variable) and
  rerun-warmup (what production would actually select post-promotion) are **both** computed and
  reported separately, **never pooled** — `--estimands both` is enforced (not just default)
  whenever `--isolate-fields` is used.
- **BR scored "in full" really means in full (OSR-16):** the §2.5 paced-metronome cross-check and
  §2.6 natural/paced separate reporting are implemented now, not deferred, even though the guard_v1
  HR decision doesn't strictly need them.
- **`comparator_prespec.md` §2.4 vs §2.1 (OSR-06 R2):** a genuine internal tension in the frozen
  spec (§2.4 lists a separate "excluded-by-PI" category; the later §2.1 clarification folds PI
  into one usable-set denominator) — resolved as **§2.1 supersedes §2.4's separate bucket**;
  `n_pi_qualified`/`n_finite_pr` stay per-window diagnostics only.
- **Sweep gets no metronome cross-check, ever, in this build (OSR-16 R2):** verified directly that
  no file anywhere persists sweep's actual step-transition timestamps — only
  `notes/protocol.md`'s nominal 120 s dwell schedule. Rather than fabricate a schedule and risk
  mistiming transition-adjacent windows, sweep is scored BR RRp-only,
  `br_session_type="paced"` (never "natural"), `br_metronome_status=
  "unavailable_missing_transition_timestamps"`.
- **Explicit expected-lock check, not weaker framing (OSR-03, round 4):** after two directory-
  swap loopholes were found and closed, a third (the *original* massimo1/massimo2 capture
  directories, same raw hash + same `eca_mode` as themselves, but the wrong lock value) was
  closed by adding `--reproduction-baseline-lock` rather than by stripping "reproducing the
  measured methodology" framing from the plan.

### Frozen/decided earlier (still true, unchanged)
- **5-bin relock tracker: DEFERRED (Option C), not decided against** — user's explicit call,
  2026-07-28, made with the full bin-drift diagnostic evidence in hand (§3.2). Coverage (§3.1)
  is the priority; revisit this later, ideally after the §3.4 subjects are captured. Do not
  build the tracker, and do not silently convert this into a "no" — it is genuinely undecided,
  parked pending more time or more evidence.
- **M4/M0 deprioritized, not decided against** — the 5 unbuilt Stage-1 review items and the two
  frozen escalations (S12R-01, S12R-18) are exactly as HISTORY.md's last M4 entry left them.
  Resume from there if scope allows later; do not silently drop or "clean up" the open review.
- **BDR-04 (bin-drift): Option A, purely exploratory** — the diagnostic reports a frozen
  sensitivity grid (duration `{2,5,10}s` × centroid `{0.3,0.5,1.0} bin`) and **never** produces a
  single automatic drift/no-drift threshold. Do not add one later without a fresh user decision.
- **BDR-07 (bin-drift): Option A** — `live_test1` gets baseline-only evidence;
  `correlation_not_available` for its outcome table; **no replay of it was generated**, because
  the three existing comparison replays were made at commit `5537df5` with an unrecoverable
  dirty diff, and HEAD has moved well past that commit since — re-running
  `scripts/live_demo.py` now would **not** reproduce a matched generation. **This is also now
  enforced in code, not just documented**: `scripts/diagnose_bin_drift_config.yaml`'s
  `approved_replays` pins each of the three real captures' raw SHA-256 to its one approved
  replay's `run_metadata.json` SHA-256 (BDR-20) — a regenerated replay with a different
  generation hash will be rejected by `match_replays_to_captures` until `approved_replays` is
  updated to the new hash.
- **The bin-drift diagnostic requires a clean tree** to produce citable evidence
  (`scripts/diagnose_bin_drift_config.yaml: provenance.require_clean_tree`) — a dirty-tree run is
  permitted (`--allow-dirty`) but is stamped `reproducible: false` in its own `summary.json` and
  must not be cited. The offline scoring plan (§3.1) reuses this exact pattern, not a new one.
- **`PeakWorkingSetSize` is a process-lifetime high-water mark** — the diagnostic samples both
  post-decode and end-of-session; neither is a cleanly isolated per-session peak when multiple
  sessions run in one process — read both, don't treat either as the single authoritative number.
- **Sweep-capture memory is ~7 GB peak working set, not ~2.5 GB** — the smaller figure is the
  decoded cube's size, not the decode peak. Treat ~7 GB as the real precondition for any script
  that decodes the sweep capture whole (relevant to §3.1's implementation).

### Frozen earlier still (unchanged)
- **The quantile method is `linear`** (user, M4R-09). **Pass it explicitly at every call site** —
  the offline scoring plan's `hr_reference`/`br_reference` do this by design (OSR-10 made the
  thresholds themselves non-configurable specifically to protect this).
- **No frozen scoring number can come from the 4 existing captures**: they lack a persisted
  `frame0_epoch`. **`start_wall_utc` is NOT frame-0.** (This is exactly what §3.1's
  "provisional evidence" framing exists to respect.)
- **M4's regression anchor is Option A** (user): the pilot MAEs are **retired, not reproduced**.
- **Window is 30 s; do not shorten.** Non-overlapping only for scoring; `k = 0` **is** scored.
- **The `(12, 15, 18)` rotation is the STUDY allocation and binds SCORING only** (S12R-14).
  Development mode must load the real captures, including `massimo2`'s **16 bpm**.
- **Agreement CI: the whole-subject cluster bootstrap is PRIMARY** (M3R-29 Option A) — out of
  scope for §3.1's narrower single-capture build, relevant again only if M0/multi-subject work
  resumes.
- **BR reference = Masimo `rr_bpm`**, cross-checked against the paced metronome (now precisely
  specified per §3.1's `--paced-schedule`/`--paced-target-unavailable` design).
- **Three capture classes:** *pre-freeze exploratory* (the 4 existing + M1), *post-freeze
  exploratory* (**M5**), *confirmatory* (**M6** onward) — this taxonomy still stands even though
  the M0 freeze itself is on hold.

### DSP decisions (unchanged)
- **The band-pass is a zero-phase order-4 Butterworth with an odd-reflected edge policy**
  (LFR-01/02). **Never reintroduce a rectangular/brick-wall mask.**
- **`filtfilt` and `np.linalg.qr` must never enter the live/M4 path or the tests** — the Windows
  LAPACK crash is non-catchable. `butter`/`freqz` are safe.
- **Never use total in-band power as an ECA cancellation metric** (LFR-06) — measure at the
  specific harmonic bins.
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
  Write a scratch `.py` file and run that.
- **`psutil` is NOT installed in the `radar-vitals` env.** Peak-memory measurement uses raw
  `ctypes` + `GetProcessMemoryInfo` (Windows-only; see `scripts/diagnose_bin_drift.py:get_peak_working_set_bytes`)
  instead. `GetProcessMemoryInfo`/`GetCurrentProcess` need explicit `argtypes`/`restype` set via
  `ctypes.wintypes` or the call silently fails — the default `ctypes` return-type assumption
  truncates the `HANDLE` on 64-bit.
- **`PeakWorkingSetSize` is a whole-process high-water mark, not per-call.** A truly isolated
  per-operation measurement needs a fresh subprocess per operation.
- **Two replay generations exist and are NOT interchangeable.** 2026-07-26 dirs are
  post-filter-fix; 2026-07-25 dirs are pre-filter-fix. Replays also carry a `git_commit` +
  `git_dirty` stamp in `run_metadata.json`, and a matching *nominal* generation label is not
  sufficient — check `git_commit`, not just the directory date, before treating two replays as
  comparable. **This bit the offline-scoring plan too**: the original massimo1/massimo2 capture
  directories (locks 23/20) and the 2026-07-26 replay generation (locks 27/26) both exist, are
  both `eca_mode=skip_forbidden_harmonics_v1`, and are easy to conflate — the plan's
  `--reproduction-baseline-lock` check exists specifically because this distinction is easy to
  lose (§4).
- **Warmup bin selection consumes BR/HR, so estimator changes can move the lock** — a free-warmup
  replay of massimo1 under `guard_cardiac_candidate_v1` locked bin 25, not the production lock 27.
  This is why §3.1's plan computes both a pinned and a rerun-warmup estimand rather than just one.
- **`fallback_hr_bpm` is a LIAR** — a naive argmax, not the verified estimate. Use `hr_bpm_raw` /
  `candidate_refined_hz[0]`, or (in the new scoring script) the `as_window_estimate`-normalized
  `hr_bpm`, never the raw `dsp` dict's fields directly.
- **`run_window_dsp`'s native `br_bpm` can be finite even when `br_valid` is False** — a
  diagnostic value, not a scorable one. `scripts/score_offline.py` routes everything through
  `as_window_estimate` specifically to avoid this leaking into a scored column (OSR-12).
- **`run_window_dsp`'s `hr_result` dict does NOT carry an identical key set on every window** —
  e.g. `ahet_second_harmonic_hz` is only present when a candidate is actually accepted; an
  ordinary `gate_not_run`/all-rejected window's `hr_result` lacks it entirely. Discovered by
  actually running `scripts/score_offline.py` for real (it crashed with `KeyError:
  'hr_result__ahet_second_harmonic_hz'` on the first attempt) — `build_evidence_arrays`/
  `_flatten_dsp_leaves` now key on the UNION of keys across all windows and fill a
  type-appropriate sentinel (NaN/False/"") for a window missing a given key. Relevant to anyone
  touching evidence-dumping code for `run_window_dsp`'s output again.
- **`scripts/score_offline.py --out <dir>` is a ROOT, not the final output directory** — a UTC
  `run_id` timestamp subdirectory is always appended beneath it (refusing to reuse an existing
  run directory), mirroring `scripts/diagnose_bin_drift.py` exactly. Passing `--out
  results/score_offline` twice therefore never silently overwrites a prior run's artifacts.
- **`packets_dropped` MAY exceed `packets_received`** — not a partition (S12R-13).
- **Masimo: `Beats / min` (PR) for heart, `Breaths / min` (RR) for respiration — never
  SpO2/PI/PVi.** Align on the integer `Timestamp` column, never `Date`/`Time` strings.
- **I/Q ordering depends on capture source**: SDK/Python captures need `iq_swap=True`; mmWave
  Studio captures `iq_swap=False`. All 4 existing captures are `iq_swap=True`.
- **`config.profile` has no `frame_rate_hz` key** — it's at `config.session.frame_rate_hz` in
  `run_metadata.json`'s recorded config snapshot (cross-checked against
  `1000/config.hw_frame.period_ms`).
- **Respiratory-harmonic coincidence** (`notes/approach.md` §4.2): when `k·f_r ≈ HR`, ECA can
  cancel the cardiac signal with the harmonic.
- **`experiments/` exists** (`experiments/exp_eca_modes/`); `figures/` still does not exist.
- **Each of the 3 Masimo capture directories contains TWO CSVs** (`live_estimates.csv` — radar
  output — and `demo_massimo*.csv`/`demo_sweep.csv` — the actual Masimo export) — a naive
  `glob("*.csv")` for Masimo auto-discovery will pick up both; §3.1's plan requires
  header-based discovery via `load_masimo`'s own column check, excluding `live_estimates.csv`
  by name.
- **`np.isfinite(None)` raises `TypeError`**, verified directly in this env — relevant if
  wiring `run_window_dsp`'s `f_r_hz=None` into `scripts/diagnose_bin_drift.py:classify_window_outcome`
  (used by §3.1's outcome-breakdown reporting); map `None → float("nan")` first.

---

## 6. Data

**4 real captures, ~2.52 GB raw, all with saved `adc_stream.bin`** in `results/live_demo/`.
**Only 3 carry a Masimo reference**: `..._live_test1` (no Masimo, 120 s), `..._massimo1`
(natural, 180 s), `..._massimo2` (paced 16 bpm, 180 s), `..._sweep` (stepped 12→15→18→21 bpm,
480 s). **All from one subject, all exploratory.**

**Locks, by generation — do not conflate (§4/§5):**
| capture | original live directory lock | 2026-07-26 replay-generation lock (the measured baseline) |
|---|---|---|
| massimo1 | 23 | **27** |
| massimo2 | 20 | **26** |
| sweep | 21 | **26** |

**Exact paths for re-running the bin-drift diagnostic** (§3.2) — 4 captures, 3 matched-generation
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

**Exact invocation for the offline scoring script (§3.1) — already run once, 2026-07-28, output at
`results/score_offline/20260728T154834Z/`; re-runnable verbatim (a fresh run gets its own new
`run_id`, never overwrites the old one — §5):**
```
conda run -n radar-vitals python scripts/score_offline.py \
  --captures results/live_demo/20260713_172042_live_demo_massimo1 \
             results/live_demo/20260713_182002_live_demo_massimo2 \
             results/live_demo/20260714_180523_live_demo_sweep \
  --configs production=scripts/live_demo_config.yaml \
            guard_v1=experiments/exp_eca_modes/config_guard_v1.yaml \
  --pinned-lock-source \
     20260713_172042_live_demo_massimo1=results/live_demo/20260726_173434_replay_unknown \
     20260713_182002_live_demo_massimo2=results/live_demo/20260726_173653_replay_unknown \
     20260714_180523_live_demo_sweep=results/live_demo/20260726_173914_replay_unknown \
  --estimands both \
  --isolate-fields heart.eca_mode \
  --reproduction-baseline-eca-mode \
     20260713_172042_live_demo_massimo1=skip_forbidden_harmonics_v1 \
     20260713_182002_live_demo_massimo2=skip_forbidden_harmonics_v1 \
     20260714_180523_live_demo_sweep=skip_forbidden_harmonics_v1 \
  --reproduction-baseline-lock \
     20260713_172042_live_demo_massimo1=27 \
     20260713_182002_live_demo_massimo2=26 \
     20260714_180523_live_demo_sweep=26 \
  --session-type \
     20260713_172042_live_demo_massimo1=natural \
     20260713_182002_live_demo_massimo2=paced \
     20260714_180523_live_demo_sweep=paced \
  --paced-schedule 20260713_182002_live_demo_massimo2=16.0 \
  --paced-target-unavailable \
     20260714_180523_live_demo_sweep=unavailable_missing_transition_timestamps \
  --allow-dirty --out results/score_offline
```
`--allow-dirty` was needed because the tree had uncommitted `HANDOFF.md`/`HISTORY.md` changes at
run time — the run's own `summary.json`/`run_summary.json` are stamped `reproducible: false`
accordingly; a run from a clean commit would omit `--allow-dirty` and get `reproducible: true`.

`data/raw/` is **empty**;
`data/manifest.local.csv` is header-only (0 sessions); `figures/` does not exist.
**3–5 more subjects agreed, not yet captured (§3.4) — now the priority (§3.1's guard_v1 buckets
are real but n≤2, too small to decide anything on their own).**

**There are still no *frozen* results.** The old pilot MAEs (0.19/0.50/0.53 bpm) remain
**retired** — do not quote or resurrect them. The first real, honest MAE/RMSE numbers now exist
(§3.1) but are exploratory, single-subject, n≤2 per bucket, and stamped
`comparator_status: exploratory_non_frozen` — provisional evidence toward the guard_v1 promotion
call, not a citable paper result.

### Known broken / open (DSP side)
- **Coverage 17–55% in production; 21–71% with `guard_cardiac_candidate_v1`** (unpromoted, §3.1)
  — the dominant bottleneck is AHET's `ratio_db_low` gate, not ECA, and not fixable by threshold
  tuning.
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
| **Offline scoring script — plan (REVIEW CLOSED, revision 7, build authority), cross-review (CLOSED round 7, `NO MORE COMMENTS`)** | `plans/offline_scoring_script.md`, `plans/offline_scoring_script_cross_review.md` |
| Offline scoring script — Codex review prompt (reference only; the loop already closed) | `plans/offline_scoring_script_codex_review_prompt.md` |
| **Offline scoring script — IMPLEMENTATION (built, tested, run for real, §3.1)** | `src/comparator.py`, `scripts/score_offline.py` |
| Offline scoring script — tests (64 total, all passing) | `tests/test_comparator.py`, `tests/test_score_offline.py` |
| Offline scoring script — first real run output (2026-07-28, exploratory, gitignored) | `results/score_offline/20260728T154834Z/` |
| **Bin-drift diagnostic — plan, review (CLOSED round 11, `NO MORE COMMENTS`), run output** | `plans/bin_drift_diagnostic.md`, `plans/bin_drift_diagnostic_cross_review.md`, `results/diagnose/bin_drift/20260728T004453Z/` (current and final — earlier run dirs are superseded, kept but not citable) |
| **Reusable cross-review prompt templates** (Codex + Claude loop sides) | `plans/codex_review_prompt_template.md`, `plans/claude_review_loop_prompt_template.md` |
| ECA-mode coverage experiment (unpromoted) | `experiments/exp_eca_modes/config_guard_v1.yaml` |
| M4 Stage 1/2 — manifest, window grid (built, review OPEN, deprioritized; window grid reused by §3.1) | `src/m4/manifest.py`, `src/m4/window_grid.py` |
| M4 build plan / Stage 1+2 review record | `plans/m4_offline_harness.md`, `plans/m4_stage12_review.md` |
| Core DSP (Butterworth band-pass + ECA + AHET) | `src/vitals.py` |
| Breathing-rate DSP (HA + M2 peak-validity fix) | `src/respiration.py` |
| Window-level DSP composition (shared by live + M4 + §3.1) | `src/window_pipeline.py:run_window_dsp`, `as_window_estimate`, `WindowEstimate` |
| Warmup bin-lock + eligibility logic (shared, reused by §3.1's rerun-warmup estimand) | `src/warmup_select.py:run_warmup_selection` |
| Radar I/O (4-RX cube, `iq_swap`) | `src/radar_io.py` |
| Masimo parser (PR, RR, PI) — reused by §3.1, not modified by it | `src/masimo.py` |
| Old mean-based comparator (SUPERSEDED, kept not deleted) + still-reused `paired_metrics`/`coverage_table` | `src/compare.py` |
| Bin-drift diagnostic code + tests (`classify_window_outcome` reused by §3.1) | `scripts/diagnose_bin_drift.py`, `scripts/diagnose_bin_drift_config.yaml`, `tests/test_diagnose_bin_drift.py` |
| Method, literature, algorithm spec | `notes/approach.md` |
| Pre-registered HR/BR comparators (binding, frozen-in-effect — implemented by §3.1's `src/comparator.py`) | `notes/comparator_prespec.md`, `notes/comparator_prespec_br.md` |
| Capture inventory (hashes, arms, rates) | `notes/capture_inventory.md` |
| Capture protocol / SOP (sweep's nominal step schedule lives here) | `notes/protocol.md` |
| Live/replay demo and capture tool | `scripts/live_demo.py` |
| Run post-mortem ("why was HR blank?") | `scripts/diagnose_live_run.py` |
| Thesis chapter / paper planning | `THIRD_CHAPTER.md`, `JOURNAL_PAPER.md` |
| Reference papers (simulation-only, M8/M9 — now the active focus) | `literature/ref_papers/` |
| Full project history | `HISTORY.md` |

### Run artifacts written per live run
`results/live_demo/<ts>_<mode>_<session>/` contains `run_metadata.json` (config incl.
`git_commit`/`git_dirty`, packet stats, `locked_bin`, `replay_file_hashes`/`live_raw_mirror_hash`),
`warmup_bin_selection.json` (per-candidate bin-lock evidence), `live_estimates.csv` (diagnostic
only, never a scoring input), `live_intermediates.npz` (phase + spectra + AHET candidates + M2
respiration evidence) and `adc_stream.bin` (raw mirror — this is every diagnostic's and the
offline scoring script's radar input). **Each of the 3 Masimo capture directories also has its own
`demo_*.csv`** — the actual Masimo export, distinct from `live_estimates.csv` (§5).

### Run artifacts written per bin-drift diagnostic run
`results/diagnose/bin_drift/<run_id>/<session>/` contains `bin_energy_blocks.csv`,
`window_audit.csv` (per-window `window_argmax_bin`/`window_centroid`/`accepted_candidate_rank`),
`motion_energy_windows.npz`, `window_energy_windows.npz`, `summary.json` (run manifest, baseline
profile, episodes, centroid drift, provenance, `reproducible` flag, memory fields), and
`drift_overview.png`. `<run_id>/run_summary.json` aggregates all sessions in that run. A replay is
only accepted for a capture if its `run_metadata.json` SHA-256 matches the entry registered for
that capture's raw hash in `scripts/diagnose_bin_drift_config.yaml`'s `approved_replays` (BDR-20).

### Run artifacts written per offline scoring run (§3.1, IMPLEMENTED)
`results/score_offline/<run_id>/<capture_label>/<config_label>/<estimand>/` (where `<run_id>` is
a UTC timestamp subdirectory always appended beneath whatever `--out` names — §5) containing
`window_scores.csv` (per-window, self-describing — carries `frame0_epoch`/`origin_source`/
`origin_caveat`/`comparator_status` on every row, not only in the summary), `evidence.npz` (the
full native `run_window_dsp` payload per window, flattened+stacked across the union of keys any
window carries — phase, spectra, candidate arrays — kept separate from the scored columns per
OSR-12), and `summary.json` (the reference/radar/joint cross-tab, MAE/RMSE/bias, full `sys.argv` +
normalized parsed-arguments, and provenance hashes/identities per the plan's Step 13). One level
up, `results/score_offline/<run_id>/<capture_label>/comparison_<estimand>.json` holds the
incremental-coverage partition (Step 11) and the supplementary `paired_metrics` report across
configs — this filename is `scripts/score_offline.py`'s own choice, since the plan pins the
per-triple output root exactly but leaves the cross-config comparison's path unspecified. A
run-level `results/score_offline/<run_id>/run_summary.json` aggregates every capture's result.
