# Handoff — radar-vitals

> **Read this + `CLAUDE.md` and you can resume the project.** State **as of 2026-07-26**.
> Rewritten, not appended (CLAUDE.md §10). For *what happened and why*, read `HISTORY.md`.
> Every claim below was re-verified against the repo at the time of writing.
>
> **M4 is fully unblocked: both gates cleared (M3, linalg review) AND its build plan is written and
> cross-reviewed to closure.** No user decision is outstanding.
>
> **The active job is to BUILD M4 — and the first step is the §5.1 Stage 0 refactor, NOT the
> harness.** Read `plans/m4_offline_harness.md` (revision 6, the build authority) before anything
> else, then §3 below. Stage 0 extracts `_run_dsp` and `_run_warmup_selection` out of
> `scripts/live_demo.py` into `src/`; it gates every other stage and takes its own CLAUDE.md §6
> review. Skipping it makes the harness's central correctness test meaningless (M4R-10).

---

## 1. Project snapshot

Estimate **heart rate and — co-equally — breathing rate** from a 77 GHz FMCW radar
(TI IWR1642BOOST + DCA1000EVM) for a subject seated 0.8–1.4 m from the sensor, chest facing the
radar. Ground truth is a Masimo MightySat pulse oximeter (1 Hz CSV: `Beats / min` = PR for heart,
`Breaths / min` = RR for respiration). HR agreement is scored under a **pre-registered comparator**
(`notes/comparator_prespec.md`); the BR comparator is `notes/comparator_prespec_br.md`. Both declare
themselves **cross-review COMPLETE, ready for the M0 freeze — but NOT frozen** (the freeze is the
user's irreversible Zenodo act). The DSP extracts chest-wall phase from an auto-locked range bin,
cancels respiration harmonics (ECA) and verifies the cardiac peak via a second-harmonic check (AHET).
Output: journal paper + thesis chapter.

**Method rationale, literature and algorithm spec: `notes/approach.md`.**
**Whole-project milestone plan: `plans/implementation_plan.md`** — read before starting work. It
survived a full cross-model review on 2026-07-24 and is the scope authority for every milestone.

---

## 2. Current state

**Branch `vital_signs_v9c`.** Test suite **1056 passed, 0 failed, 0 xfailed**
(`conda run -n radar-vitals python -m pytest tests/ -q`, 2026-07-26; independently re-run by Codex).
There is **no longer an xfail** — the ECA/AHET decoy case now passes (see §2 "partly closed" below).
v9 UI work remains parked in `git stash@{0}`; relocking/display-holdover stay reverted.

### Both M4 gates are cleared

1. **M3 (BR comparator + analysis pre-spec)** — cross-review COMPLETE 2026-07-26, 48 findings
   (M3R-01…48) over 19 rounds, all resolved (`plans/m3_prespec_cross_review.md`).
2. **Linalg-free DSP review** — cross-review COMPLETE 2026-07-26, **7 findings (LFR-01…07) over 5
   rounds, all resolved, no escalation**; Codex signed off
   (`plans/m4_linalg_free_dsp_review.md`, status header + resolution table at the top of
   `DEBATE COMMENTS`).

**M4's output may now be trusted** once M4 exists. The M0 deposit remains the user's act.

### The band-pass was silently wrong for four weeks — now fixed

Between **2026-06-30 (`1847d7f`)** and 2026-07-26, `bandpass_filter` was an FFT **brick-wall mask**,
introduced as a "transparent substitution" for `filtfilt(butter(4,…))` to dodge a Windows LAPACK
crash. It was not transparent: it changed the estimator, and **every number this project has quoted
was produced under it**. Because the mask was applied to an un-windowed, non-periodic segment, a
respiratory harmonic just below `lo` leaked a tail across the cutoff which the rectangle **kept while
discarding the main lobe** — at f_r = 0.36 Hz (21.6 bpm breathing, inside the `sweep` range) the
artifact **outranked the true cardiac peak** (ratio 1.570 vs 0.126 restored).

`bandpass_filter` is now odd-reflect (`n−1`/side) → `rfft` → `|H_butter4(f)|²` → `irfft` → centre
crop: a faithful LAPACK-free `filtfilt(butter(4), padtype='odd')`. Cutoff gains measure
0.500001/0.500008 (the mask gave 1.0). `order` is live again and pinned at both call sites.

**Measured impact (`scripts/compare_filter_fix_impact.py`, committed and deterministic):**

| capture | warmup bin | HR coverage, all hops | HR, frozen §7 grid | BR |
|---|---|---|---|---|
| natural | 27 → 27 | 9/51 → 9/51 (17.6%) | 2/6 → 2/6 | bitwise identical |
| paced16 | 26 → 26 | 23/51 → **28/51** (45.1→54.9%) | 3/6 → **5/6** | bitwise identical |
| sweep | 26 → 26 | 30/151 → **35/151** (19.9→23.2%) | 3/16 → 3/16 | bitwise identical |

**No bin moved. No window lost. HR max |Δ| = 0.00088 bpm. BR bitwise identical** (because
`src/respiration.py` never calls `bandpass_filter`). M2 floor-pin invariant holds (0
floor-pinned-and-valid) on all three. Codex independently confirmed **0/486** cardiac peak-bin changes
vs the former `filtfilt`. The effect lands on **coverage, not accuracy** — AHET only verifies windows
whose peak is unambiguous, which a response change cannot move. All 10 newly-accepted windows were
checked: each lies inside both the previously-accepted and Masimo PR ranges (±5 bpm), and each had
been rejected on a **floor/ratio** gate, never a wrong-frequency one.

### What is built and trustworthy
Raw ADC I/O with `iq_swap` handling · range FFT · static clutter removal · phase extraction and
unwrap · warmup range-bin lock with the energy-eligibility fix · **restored Butterworth band-pass
(cross-reviewed)** · ECA + AHET heart-rate core · breathing-rate DSP with the M2 peak-validity fix ·
Masimo parser · quality mask · live demo with capture, replay, raw mirroring and diagnostics · both
comparator specs. All under test.

### A known bug partly closed
The strict-xfail AHET decoy test now **passes** (brick wall gave 2/6 correct across seeds 0–5; the
restored filter gives 6/6) because the hole's mechanism — "the decoy survives ECA at full strength" —
was the mask admitting the leak. The marker is removed and the historical reason preserved verbatim
in `tests/test_eca_ahet.py`. **But its headline claim "34% of hops on the paced-16 capture" is
REAL-DATA and has NOT been re-measured** — it is annotated CLOSED ON SYNTHETICS ONLY.
`guard_cardiac_candidate_v1` remains un-promoted in every config.

### Data
**4 real captures, ~2.52 GB raw, all with saved `adc_stream.bin`** in `results/live_demo/`.
**Only 3 carry a Masimo reference**: `20260713_170323_..._live_test1` (**no Masimo**),
`20260713_172042_..._massimo1` (natural), `20260713_182002_..._massimo2` (paced 16),
`20260714_180523_..._sweep` (stepped 12→15→18→21 bpm, 486 s). **All from one subject, and all
exploratory** — they informed the method's design, so they can never be confirmatory evidence.
`data/raw/` is empty; `data/processed/` holds only a tracked `.gitkeep`; `experiments/` and
`figures/` **do not exist**; `data/manifest.local.csv` is header-only (0 sessions).

Replay artefact dirs (not sessions): **post-filter-fix (2026-07-26, the current generation)** at
`20260726_173434` (natural) / `_173653` (paced16) / `_173914` (sweep). Pre-filter-fix but post-M2
(2026-07-25) at `20260725_212615` (live_test1) / `_212829` (natural@27) / `_213047` (paced16) /
`_213305` (sweep) / `_213635` (natural@23). Older pre-M2 evidence at `20260715_164018/_164124/_164132`.

### Best current results — PRELIMINARY, not citable, and now STALE IN PROVENANCE

| session | AHET-accepted | scorable | MAE | severe (>5 bpm) |
|---|---|---|---|---|
| natural | 5/50 | 1 | 0.19 bpm | 0 |
| paced16 | 23/50 | 21 | 0.50 bpm | 0 |
| sweep | 30/150 | 19 | 0.53 bpm | 0 |

**Do not quote these.** Three independent reasons: (1) n=1 subject, exploratory, not held-out;
`natural` rests on a single scorable window. (2) They were computed **under the retired brick-wall
filter** and at the older natural bin (23), and have **not been recomputed** since. (3) They trace to
no committed script. HR values at already-accepted windows moved ≤0.001 bpm, so the accuracy figures
would likely be similar — but the **scorable population changed** (coverage rose), so the MAEs are
not simply carried forward. **M4 recomputes them.** Report coverage alongside accuracy, always:
accuracy is not the problem, coverage is.

### Known broken / open
- **Coverage 17–55%** — AHET conservatism is the bottleneck. (Improved by the filter fix, not solved.)
- **Bland–Altman is not implemented correctly for this study.** `scripts/plot_bland_altman.py` pools
  every window as an independent pair (`se_loa = sqrt(3·SD²/n)`, no subject/session term). For
  10 subjects × 2 sessions it gives falsely narrow limits, and it was never valid for the n=1 pilot.
  **Reuse for plotting only.**
- **Candidate ranking** — magnitude order ≠ credibility order. Confirmed, n=1, no plan written.
- **Stage 1B lag-10 veto** — designed, scaffolded, verified on synthetics, but **blocked**: zero
  baseline severe accepts exist post-bin-fix, so the veto has nothing to veto. Unblocked only by M7.
- **ECA v1** (`skip_forbidden_harmonics_v1`, the live mode) removes **0.00 dB** in the cardiac band at
  low breathing rates — production harmonic cancellation is effectively inert.
- **`guard_cardiac_candidate_v1`** implemented and unit-tested but **not promoted** — blocked on
  `experiments/exp_eca_modes`, and `experiments/` does not exist. Its synthetic decoy test now passing
  is **not** grounds to promote it.
- **The "34% of hops" paced-16 decoy figure is un-remeasured** since the filter fix.
- **`elapsed_s` is wall-clock** in replay NPZs under `--replay-fast`.
- **Genuine ≈6 bpm breathing at the edge bin is permanently `resp_valid=False`** — a deliberate,
  declared coverage sacrifice mandated by the M2 invariant.

---

## 3. Active task / next steps — **BUILD M4 (offline evaluation harness)**

Both gates are cleared. M4 is now the critical path: it produces every paper-grade number, and M0,
M5, M8, M9 and M10 all wait on it.

### 3.0 The plan is written AND cross-reviewed. **Implementation may begin.**

**`plans/m4_offline_harness.md` revision 6** is the build authority. Its cross-model review is
**COMPLETE** (2026-07-26): **15 findings (M4R-01…15) over 8 rounds, 13 Blocking, all resolved, none
disputed**; Codex signed off with `NO MORE COMMENTS` (`plans/m4_plan_cross_review.md` — status header
+ resolution table at the top of `DEBATE COMMENTS`).

**Start at §5.1 Stage 0, not at the harness.** Stage 0 is the shared-callable refactor and it **gates
every other stage**; it takes its own CLAUDE.md §6 correctness review. Reason: `_run_dsp` and
`_run_warmup_selection` are **private functions in `scripts/live_demo.py`**, so without extraction M4
must duplicate them — and the harness's central equality test would then compare M4 against a
duplicate rather than against the production path (M4R-10).

The loop produced **two user decisions** (M2 #5 stays open; `linear` percentile) and **two
pre-deposit clarifications now written into the binding specs** (`linear`; the usable-HR-sample
rule) — see §4.

**The A/B regression-anchor decision is CLOSED: Option A** (user, 2026-07-26). The
0.19/0.50/0.53 anchor is **retired** — no committed script produced it, it used a nearest-hop rule
§7 later froze differently, and it predates the filter fix. The scorer is instead validated on
**synthetic windows with hand-computable answers**, and frozen-§7 outputs become canonical.

**A scope constraint found while writing the plan, which the reviewer must rule on.**
`notes/analysis_prespec.md` §7 states the 4 existing captures **lack a persisted `frame0_epoch`**, so
any alignment reconstructed from `start_wall_utc` is APPROXIMATE and is "used only for
reference-characterization design evidence, **never for a frozen scoring number**." So M4's run on
these captures is a **validation/demonstration output, explicitly non-scoring** — a stronger limit
than "n=1 is descriptive", and one that no amount of scorer correctness removes. This is in tension
with `plans/implementation_plan.md` §M4's done-when, which asks for numbers from exactly these
captures; the proposed resolution is in the plan's §2.2.

### 3.1 The build

Build incrementally — **scoring core + unit tests before any capture is scored** (CLAUDE.md §5.3);
staged table in the plan's §6. Design already grounded:

- **Input:** the replay `live_estimates.csv` of the **2026-07-26** post-fix runs (§2).
- **Grid:** the frozen §7 non-overlapping grid — the hop where `frame_idx = 600·k + 599`.
- **Fields:** `hr_bpm_raw` / `br_bpm`. **Never** `hr_bpm_smooth` (online median, not paper-grade) and
  **never** `fallback_hr_bpm` (a naive argmax — it lies).
- **Validity:** `ahet_verified` / `resp_valid` false → radar NaN.
- **Comparators:** apply **both** frozen specs — HR (PI-gate, median, ≥80% coverage, stationarity
  >5 bpm) and BR (≥24 availability, median, stationarity >2 bpm, half-open). Windows are half-open
  `[t − 30 s, t)` for both vitals.
- **Pilot output is descriptive-only** — n=1 fails §1 estimability, so **no cluster-bootstrap LoA**.
- **Alignment is APPROXIMATE** on these captures (no persisted `frame0_epoch`). Declare it.

**Then:** run on the 3 Masimo captures → recompute HR under the frozen §7 grid → emit **BR for the
first time**, closing **M2 done-when #5**.

### 3.2 After M4
- **M0** — pre-registration freeze + Zenodo deposit (hard gate before any study capture). The
  evidence-floor decision is frozen (Option A, §2b). M0 also waits on M4 existing (the MOVER
  *candidate* sensitivity and the M3R-29 primary-CI harness).
- **M1** — live hardware smoke test (unscored; run the **full 10 min**).
- **M8 step 1a/1b** — HA reproduction control, then adaptation transfer.

---

## 4. Decisions that matter (do not silently reverse)

- **The quantile method is `linear`** (user, 2026-07-26, M4R-09) — a **pre-deposit clarification now
  written into `notes/comparator_prespec.md` §2.2, `notes/comparator_prespec_br.md` §2.2 and
  `notes/analysis_prespec.md` §1**. Neither comparator named one, and with integer PR over 24–30
  samples the interpolation rule alone can flip a `p90 − p10` verdict (self-contained worked example
  in the HR comparator). **Pass it explicitly at every call site** — never rely on a library default.
- **A usable HR reference sample is `pr_bpm` finite ∧ `pi` finite ∧ `pi ≥ 0.5`** (user, 2026-07-26,
  M4R-11) — **one set** for the median, the stationarity quantiles and the coverage count, so there is
  exactly one denominator. Written into `notes/comparator_prespec.md` §2.1. Makes HR symmetric with
  BR's explicit finite-RRp counting.
- **Stage 0 (the shared-callable refactor) precedes all M4 work** (M4R-10). `_run_dsp` and
  `_run_warmup_selection` must move out of `scripts/live_demo.py` into `src/`, imported by both the
  live path and M4. Without it, M4's equality test compares M4 to a duplicate of itself.
- **Never put a number in a document bound for the M0 deposit unless it traces to a committed script**
  (M4R-13, CLAUDE.md §3.1). A Monte-Carlo frequency was inserted into both comparators during this
  loop and retracted: it reported an unstated simulation parameter, not the data. Self-contained
  worked examples — data stated in full, inline — are the safe alternative.
- **M4's regression anchor is Option A** (user, 2026-07-26): the pilot MAEs 0.19/0.50/0.53 are
  **retired, not reproduced**. M4 is validated on synthetic windows with hand-computable answers, and
  frozen-§7 outputs are canonical. Reconstructing the old nearest-hop rule was **declined** — it would
  have to guess an undocumented rule *and* resurrect the retired brick-wall filter to be a true
  bridge, and a reconstruction tuned until it emits 0.19 proves nothing.
- **No frozen scoring number can come from the 4 existing captures** (`notes/analysis_prespec.md` §7):
  they lack a persisted `frame0_epoch`, so alignment is APPROXIMATE by construction. M4's output on
  them is descriptive and must be labelled non-scoring. **`start_wall_utc` is NOT frame-0** — it is
  written before DCA/IWR configuration, so the offset is capture-startup latency.
- **The band-pass is a zero-phase order-4 Butterworth with an odd-reflected edge policy**
  (2026-07-26, cross-reviewed, LFR-01/02). **Never reintroduce a rectangular/brick-wall mask** — it
  lets a 2·f_r artifact outrank the true cardiac peak at 20–22 bpm breathing. The pad is `n−1` per
  side, deliberately longer than filtfilt's 27, because FFT-domain filtering wraps globally.
- **`filtfilt` and `np.linalg.qr` must never enter the live/M4 path or the tests** — the Windows
  LAPACK crash is non-catchable. `butter`/`freqz` are safe (algebraic/polynomial only). Tests pin the
  response analytically instead of differencing against `filtfilt`.
- **Never use total in-band power as an ECA cancellation metric** (LFR-06). It is dominated by the
  cardiac peak and the deliberately spared harmonic, so it moves the *wrong way* — a lower total is
  also what erasing the cardiac signal produces. Assert per-harmonic attenuation + cardiac preservation.
- **Breathing rate is a co-equal goal**, not a by-product.
- **The M2 validity invariant: a selection on the first in-band FFT bin can never be
  `resp_valid=True`** — enforced by bin identity inside `fuse_estimates`, not frequency arithmetic.
- **The 7 unresolved edge windows are "spectrally unresolved", never classified by STFT/reference
  agreement** (that would be tuning to the reference, CLAUDE.md §4).
- **Recordings are 10 minutes.** Approval ceiling is 10 min — do not exceed, do not shorten.
- **The pre-registration claim is "frozen before the confirmatory data", NOT "before any data
  existed."** Overstating it repeats the error that forced the "MAE 0.16 bpm" withdrawal.
- **Three capture classes:** *pre-freeze exploratory* (the 4 existing + M1), *post-freeze exploratory*
  (**M5**), *confirmatory* (**M6** onward).
- **The BR comparator is designed from the reference alone.** Using radar agreement to sanity-check
  reference eligibility is the mirror image of the tuning CLAUDE.md §4 forbids.
- **BR reference = Masimo `rr_bpm`, cross-checked against the paced metronome rate.** Masimo RRp is
  pleth-derived; it *appears* smoothed and laggy — an **inference**, not a measured device fact
  (M3R-38). Report it as a weaker, non-gold-standard reference than PR.
- **Agreement CI: the whole-subject cluster bootstrap is PRIMARY** (M3R-29 Option A). Its `S_a ≤ 10`
  under-coverage is a declared limitation and is **ANTI-conservative** for the ≤5 bpm precision gate
  (M3R-45, user accepted). **MOVER (Zou 2013) is a pre-named *candidate* sensitivity** — validated only
  after M4 implements + a statistician math-reviews it (M3R-46). The constant `μ ± 1.96·SD` LoA point
  estimate is primary and never switched post-hoc.
- **The paced-arm LoA is a marginal design-weighted mixture over a FROZEN rate allocation** (M3R-31):
  rotation 12→15→18 ⇒ counts **4/3/3**. **HR paced pools 12/15 only**; **BR paced pools 12/15/18**.
  Per-rate breakdowns are descriptive-only.
- **Masimo intended-use disposition (M3R-34):** covered for attended 10-min healthy-subject sessions
  with a **verified battery**. Cite the manual as **`LAB-10168A`**.
- **Ethics approval `24IBEC051`, IBEC KAUST** — covers collection and publication, 10-min recordings,
  M7's collision manoeuvre. Recorded in `notes/protocol.md`.
- **Both `literature/ref_papers/` methods are an OFFLINE COMPARISON ARM**, not production replacements.
- **Sequencing is fix-blockers → pilot → study → method work.**
- **Window is 30 s; do not shorten.** **Statistics use non-overlapping 30 s windows only.**
- **Every HR agreement number goes through `notes/comparator_prespec.md`.** No exceptions.
- **The live demo's readouts are NOT paper-grade** — metrics come from offline reprocessing.
- **The 18 bpm paced arm is deliberately inside the failure zone** (4×18 = 72 bpm). Report separately.
- **Distance is descriptive, not inferential.**
- **Do not implement the candidate-ranking fix ad hoc** — plan + cross-model review first.
- **Never promote a status tag in `THIRD_CHAPTER.md` by editing it** — re-measure, or leave it.

---

## 5. Gotchas / landmines

- **Run Python via `conda run -n radar-vitals`.** Conda is at
  `C:\ProgramData\anaconda3\condabin\conda.bat`, **not on PATH** (bare `conda` fails). Invoking the
  env's `python.exe` by absolute path skips activation; matplotlib then can't find its render DLLs and
  `savefig()` hard-kills the interpreter with exit 127 and no traceback.
- **Multi-line `python -c` under `conda run` silently produces no output in this shell.** Write a
  scratch `.py` file and run that instead.
- **`bandpass_filter` now RAISES on short input** (`n < ceil(fs/lo)`: 25 samples at 0.8 Hz, 200 at
  0.1 Hz) and on `lo ≥ hi`, `lo ≤ 0`, `hi ≥ Nyquist`. Production 400/600 windows are unaffected, but a
  diagnostic script feeding it a short slice will now fail loudly rather than return unfiltered data.
- **Two replay generations exist and they are NOT interchangeable.** 2026-07-26 dirs are post-filter-fix;
  2026-07-25 dirs are pre-filter-fix. Any comparison must state which. `scripts/compare_filter_fix_impact.py`
  pairs them automatically by source capture and prefers a matching locked bin.
- **Pre-M2 replay NPZs (before 2026-07-25) have no `resp_valid` field** — infer validity from `f_r_hz`
  finiteness. Post-fix NPZs carry the full M2 evidence schema.
- **Warmup bin selection consumes BR/HR, so estimator changes can move the lock.** The filter fix did
  **not** move it (27/26/26 unchanged), but the M2 fix previously moved natural 23 → 27. Any future DSP
  change must re-check warmup decisions, not just per-window outputs.
- **`_save_intermediates` silently drops any record key that fails to stack** (`except: pass`) — the
  schema test in `tests/test_respiration_m2_fix.py` guards the M2 fields; extend it when adding fields.
- **The 10-minute duration is enforced by operator discipline only.** `scripts/live_demo_config.yaml`
  has `max_live_duration_s: null` (run until Ctrl-C).
- **A longer session stresses the bin lock.** The warmup locks **one** range bin for the whole
  recording — check the lock at minute 9–10 with `scripts/diagnose_live_run.py`.
- **`elapsed_s` in replay NPZs is WALL-CLOCK**, ~0 under `--replay-fast`. Use `frame_idx / frame_rate_hz`.
- **`fallback_hr_bpm` is a LIAR** — a naive argmax, not the verified estimate. Use `hr_bpm_raw` /
  `candidate_refined_hz[0]`.
- **The live display holds the last valid HR** — always run `scripts/diagnose_live_run.py` before
  believing a live run.
- **M7's rate must NOT be computed from resting HR** — set it from HR measured *while paced*.
- **A synthetic test asserting "candidate tracks harmonic order k" must first confirm k is admissible
  at that f_r** (`k*f_r` must land in 0.8–2.0 Hz).
- **Masimo: `Beats / min` (PR) for heart, `Breaths / min` (RR) for respiration — never SpO2/PI/PVi.**
  Align on the integer `Timestamp` column, never the `Date`/`Time` strings.
- **I/Q ordering depends on capture source**: SDK/Python captures need `iq_swap=True`; mmWave Studio
  captures `iq_swap=False`.
- **Respiratory-harmonic coincidence** (`notes/approach.md` §4.2): when `k·f_r ≈ HR`, ECA can cancel
  the cardiac signal with the harmonic. Identifiability problem — longer windows do not help.
- **`experiments/` and `figures/` do not exist, and cannot be restored from git** — the only tracked
  placeholders are `data/processed/.gitkeep` and `results/.gitkeep`, so `git checkout figures/.gitkeep`
  fails. Create them with `mkdir` when a milestone needs them. (An earlier HANDOFF gave that broken
  restore command; verified and corrected 2026-07-26.)

---

## 6. The two reference papers (both simulation-only — a live opportunity)

`literature/ref_papers/`, both IEEE Trans. Radar Systems vol. 2 (2024), both KAUST:

- **Paper 1 — Ahmed et al., "Discovering the Unseen"** (DOI 10.1109/TRS.2024.3412915). Its Harmonic
  Accumulation is already the primary BR estimator in `src/respiration.py`, but **adapted**: the paper
  is a **pulse radar, single TX / single RX**, built on **2f_h and 2f_b and their harmonics** — even-
  harmonic structure is intrinsic to its demodulated model — whereas ours is all-harmonic phase.
  **Never validated against Masimo.** Its Fig. 8(c)–(d) claims correct estimation *at* the 4·f_r = HR
  collision — the project's central unsolved problem. **M8 must reproduce the paper on its own terms
  FIRST (step 1a), then run the adaptation (step 1b)**; otherwise a failure cannot be told apart from
  our own bug, and a success reproduces nothing.
- **Paper 2 — Kotte et al.** (DOI 10.1109/TRS.2024.3352189). **The "1 TX / 4 RX = the SIMO setup the
  paper assumes" claim was FALSE and is retracted.** Its §IV simulates **one TX and 20 RX** at 24 GHz,
  50 ms PRI, `N_c = 16`. Eq. (23) defines `Y_t ∈ C^(N_c×n_R)` and eq. (25) requires `R_t⁻¹` where
  `R_t = E{Y_t Y_tᴴ}` is **N_c × N_c** — the **RX channels supply the snapshots**, so at `n_R = 4`,
  `rank(R_t) ≤ 4` and `R_t` is **singular**. Its Fig. 5 validates on generic moving-target Dopplers,
  **not HR/BR**. **M9 requires a paper-faithful 1×20 / `N_c`=16 reproduction, then a registered 4-RX
  ablation before any existing capture is touched.**

---

## 7. Pointers

| What | Where |
|---|---|
| Project rules (read first) | `CLAUDE.md` |
| **Whole-project milestone plan** | `plans/implementation_plan.md` |
| **M4 build plan — rev 6, cross-review COMPLETE, ACTIVE build authority** | `plans/m4_offline_harness.md` |
| **M4 plan review record — COMPLETE (M4R-01…15, 8 rounds)** | `plans/m4_plan_cross_review.md` |
| M4 plan review — Codex-side prompt (loop closed) | `plans/m4_plan_codex_review_prompt.md` |
| Thesis chapter source | `THIRD_CHAPTER.md` |
| Journal paper planning | `JOURNAL_PAPER.md` |
| Method, literature, algorithm spec | `notes/approach.md` |
| **Pre-registered HR comparator (binding)** | `notes/comparator_prespec.md` |
| **BR comparator (M3 complete; ready for M0 freeze, not frozen)** | `notes/comparator_prespec_br.md` |
| Analysis pre-spec (agreement model §1, scoring grid §7) | `notes/analysis_prespec.md` |
| **Linalg-free DSP review — COMPLETE, gate cleared (LFR-01…07)** | `plans/m4_linalg_free_dsp_review.md` |
| **Filter-fix impact measurement (regenerates §2's table)** | `scripts/compare_filter_fix_impact.py` |
| **Linalg-free regression tests (MGS + filter response + edges)** | `tests/test_vitals_linalg_free.py` |
| M3 cross-review record (complete) | `plans/m3_prespec_cross_review.md` |
| M2 fix plan + cross-review record | `plans/m2_respiration_fix.md`, `plans/m2_fix_cross_review.md` |
| M2 floor-pin diagnostic | `scripts/diagnose_respiration_collapse.py` |
| M2 fix tests (25, precondition-asserting) | `tests/test_respiration_m2_fix.py` |
| M0 pre-registration draft + evidence-floor memo | `plans/m0_preregistration.md`, `plans/m0_b1_evidence_floor_memo.md` |
| Capture inventory (hashes) | `notes/capture_inventory.md` |
| Capture protocol / SOP (10-min; ethics `24IBEC051`) | `notes/protocol.md` |
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
| **Core DSP (Butterworth band-pass + ECA + AHET)** | `src/vitals.py` |
| Breathing-rate DSP (HA + M2 peak-validity fix) | `src/respiration.py` |
| Radar I/O (4-RX cube, `iq_swap`) | `src/radar_io.py` |
| Masimo parser (PR, RR, PI) | `src/masimo.py` |
| Agreement metrics — **OLD mean-based comparator, M4 must replace** | `src/compare.py` |
| Bland–Altman — **plotting only**, statistics invalid for repeated measures | `scripts/plot_bland_altman.py` |
| **Post-filter-fix replays (2026-07-26 — the current generation)** | `results/live_demo/20260726_173434` (natural), `_173653` (paced16), `_173914` (sweep) `_replay_unknown/` |
| Pre-filter-fix, post-M2 replays (2026-07-25) | `results/live_demo/20260725_212615/_212829/_213047/_213305/_213635` `_replay_unknown/` |
| Full project history | `HISTORY.md` |

### Run artifacts written per live run
`results/live_demo/<ts>_<mode>_<session>/` contains `run_metadata.json` (config, git commit, packet
stats), `warmup_bin_selection.json` (per-candidate bin-lock evidence), `live_estimates.csv` (per-hop
HR/BR + rejection reasons), `live_intermediates.npz` (phase + spectra + AHET candidates + M2
respiration decision evidence, checkpointed every 60 s — `elapsed_s` inside is wall-clock, see §5) and
`adc_stream.bin` (raw mirror).
