# Handoff — radar-vitals

> **Read this + `CLAUDE.md` and you can resume the project.** State **as of 2026-07-27**.
> Rewritten, not appended (CLAUDE.md §10). For *what happened and why*, read `HISTORY.md`.
> Every claim below was re-verified against the repo at the time of writing.
>
> **The active job is BUILDING M4.** Stage 0 is done and its review is closed. **Stages 1 + 2 are
> built and their cross-review is OPEN** (`plans/m4_stage12_review.md`) — **Stage 3 does not begin
> until it closes** (plan §7). No user decision is outstanding.
>
> Read `plans/m4_offline_harness.md` (revision 6, the build authority) before touching M4.

---

## 1. Project snapshot

Estimate **heart rate and — co-equally — breathing rate** from a 77 GHz FMCW radar
(TI IWR1642BOOST + DCA1000EVM) for a subject seated 0.8–1.4 m from the sensor, chest facing the
radar. Ground truth is a Masimo MightySat pulse oximeter (1 Hz CSV: `Beats / min` = PR for heart,
`Breaths / min` = RR for respiration). HR agreement is scored under a **pre-registered comparator**
(`notes/comparator_prespec.md`); the BR comparator is `notes/comparator_prespec_br.md`. Both are
**cross-review COMPLETE and ready for the M0 freeze — but NOT frozen** (the freeze is the user's
irreversible Zenodo act). The DSP extracts chest-wall phase from an auto-locked range bin, cancels
respiration harmonics (ECA) and verifies the cardiac peak via a second-harmonic check (AHET).
Output: journal paper + thesis chapter.

**Method rationale, literature and algorithm spec: `notes/approach.md`.**
**Whole-project milestone plan: `plans/implementation_plan.md`** — the scope authority for every
milestone.

---

## 2. Current state

**Branch `vital_signs_v9c`.** Suite **1240 passed, 0 failed, 0 xfailed**
(`conda run -n radar-vitals python -m pytest tests/ -q`, 2026-07-27). v9 UI work remains parked in
`git stash@{0}`; relocking/display-holdover stay reverted.

### Where M4 is

| Stage | State |
|---|---|
| **0** — shared-callable refactor | **DONE**, cross-review **CLOSED** (`plans/m4_stage0_refactor_review.md`) |
| **1** — manifest schema + validation | **BUILT** (`src/m4/manifest.py`), review **OPEN** |
| **2** — window grid | **BUILT** (`src/m4/window_grid.py`), review **OPEN** |
| **3–8** | **not started** — 3 gated on the Stages 1+2 review closing |

Nothing else in `src/m4/` exists: no reprocessing, no reference aggregation, no ledger, no
statistics, no outputs, and no `scripts/run_m4_harness.py`. **No paper-grade number exists yet.**

**Stage 0** moved the window DSP into `src/window_pipeline.py:run_window_dsp` and the warmup policy
into `src/warmup_select.py:run_warmup_selection`; `scripts/live_demo.py` imports both. Its review ran
12 Codex passes / 10 response rounds, 20 findings + 3 follow-ups, 11 Blocking, all resolved. **Not one
finding was in the moved DSP** — all 11 Blocking were in the new adapter code. The extraction is proven
behaviour-identical to pre-refactor `d3cfb92` by **22 bitwise-identical comparisons** (three Masimo
captures × four windows each, plus all three warmup failure branches); locked bins 27/26/26 unchanged.

### Both earlier M4 gates were already cleared

1. **M3** (BR comparator + analysis pre-spec) — COMPLETE 2026-07-26, 48 findings, all resolved.
2. **Linalg-free DSP review** — COMPLETE 2026-07-26, 7 findings (LFR-01…07), Codex signed off.

### What is built and trustworthy
Raw ADC I/O with `iq_swap` handling · range FFT · static clutter removal · phase extraction and
unwrap · warmup range-bin lock with the energy-eligibility fix · restored Butterworth band-pass ·
ECA + AHET heart-rate core · breathing-rate DSP with the M2 peak-validity fix · Masimo parser ·
quality mask · live demo with capture, replay, raw mirroring and diagnostics · the shared
window-DSP/warmup callables and the estimator adapter · M4 Stages 1–2. All under test.

### Data
**4 real captures, ~2.52 GB raw, all with saved `adc_stream.bin`** in `results/live_demo/`.
**Only 3 carry a Masimo reference**: `20260713_170323_..._live_test1` (**no Masimo**),
`20260713_172042_..._massimo1` (natural), `20260713_182002_..._massimo2` (paced 16),
`20260714_180523_..._sweep` (stepped 12→15→18→21 bpm, 486 s). **All from one subject, and all
exploratory** — they informed the method's design, so they can never be confirmatory evidence.
`data/raw/` is **empty**; `data/processed/` holds only a tracked `.gitkeep`; **`experiments/` and
`figures/` do not exist**; `data/manifest.local.csv` is header-only (0 sessions).

Replay artefact dirs (not sessions): post-filter-fix (2026-07-26, current generation) at
`20260726_173434` (natural) / `_173653` (paced16) / `_173914` (sweep). Pre-filter-fix but post-M2
(2026-07-25) at `20260725_212615/_212829/_213047/_213305/_213635`. Older pre-M2 at
`20260715_164018/_164124/_164132` and `20260714_224000/_224244`.

### There are no citable results

The old pilot MAEs (0.19 / 0.50 / 0.53 bpm) are **retired**, not merely stale: no committed script
produced them, they used a nearest-hop rule §7 later froze differently, and they predate the
band-pass fix. **Do not quote them or resurrect them.** M4 will produce the first traceable numbers.

### Known broken / open
- **Coverage 17–55 %** — AHET conservatism is the bottleneck (improved by the filter fix, not solved).
- **Bland–Altman is not implemented correctly for this study.** `scripts/plot_bland_altman.py` pools
  every window as an independent pair (no subject/session term). **Reuse for plotting only.**
- **Candidate ranking** — magnitude order ≠ credibility order. Confirmed, n=1, no plan written.
- **Stage 1B lag-10 veto** — designed and verified on synthetics but **blocked**: zero baseline severe
  accepts exist post-bin-fix, so the veto has nothing to veto. Unblocked only by M7.
- **ECA v1** (`skip_forbidden_harmonics_v1`, the live mode) removes **0.00 dB** in the cardiac band at
  low breathing rates — production harmonic cancellation is effectively inert.
- **`guard_cardiac_candidate_v1`** implemented and unit-tested but **not promoted** — blocked on
  `experiments/exp_eca_modes`, and `experiments/` does not exist.
- **The "34 % of hops" paced-16 decoy figure is un-remeasured** since the filter fix.
- **`elapsed_s` is wall-clock** in replay NPZs under `--replay-fast`.
- **Genuine ≈6 bpm breathing at the edge bin is permanently `resp_valid=False`** — a deliberate,
  declared coverage sacrifice mandated by the M2 invariant.

---

## 3. Active task / next steps

### 3.0 Close the Stages 1+2 review — **this gates everything**

`plans/m4_stage12_review.md` is OPEN. Codex writes findings as `S12R-NN` into `COMMENTS OF CODEX`;
process them into `DEBATE COMMENTS` with a **verified** response (reproduce before agreeing), apply
agreed fixes, commit, and poll. The loop ends at `NO MORE COMMENTS` with every item resolved.

**One question is deliberately unanswered and waiting for the reviewer** (not for the user):
`notes/analysis_prespec.md` §6 item 6 makes a **wholly missing Masimo file** a separately-logged
**no-agreement** session (radar-only, descriptive at most), but the Stage 1 schema requires
`masimo_path`/`masimo_sha256` in scoring mode, so such a session cannot be loaded at all. Stage 1
defect, or correctly deferred to Stage 4/5? **Do not invent a third disposition** — inventing
predicates is exactly what went wrong in Stage 1 (§4).

### 3.1 Then Stage 3, then Stage 4

Grouping already decided with the user: **1+2 together** (done), **3 alone**, **4 alone**.

- **Stage 3 — raw-ADC exact-grid reprocessing** (plan §6.1). The highest-risk stage: it is where M4
  meets the DSP, and its done-when *is* the equality test Stage 0 existed to make meaningful —
  *"M4's per-window DSP output equals a direct shared-DSP call on the same saved 600-frame slice at
  full precision"*, plus a forced DSP failure yielding a **recorded** radar-NaN (never a missing
  window), plus `k = 0` reproducing the warmup semantics. Golden fixture 2 (§7.1 — synthetic frame
  stream with frame-ID sentinels and a **call-recording estimator** that fails if any overlapping or
  greedy slice is requested) belongs here.
- **Stage 4 — reference aggregation + gates**, on **raw-format** Masimo CSVs, not clean DataFrames.
  Touches the Masimo parser path, so it takes a **mandatory** CLAUDE.md §6 review. Every gate tested
  **at equality** (PI 0.5, coverage 24/30, 5.0 / 2.0 bpm); `linear` named explicitly. Golden
  fixture 1 (§7.1) belongs here.

**M4's radar input is the original capture folder's `adc_stream.bin` — NEVER `live_estimates.csv`**
(M4R-01, CLAUDE.md §4). The replay folders contain no ADC and their `start_wall_utc` is 13 days off;
they are evidence for the `600k + 599` row mapping only.

### 3.2 After M4
- **M0** — pre-registration freeze + Zenodo deposit (hard gate before any study capture). Waits on M4
  existing (the MOVER *candidate* sensitivity and the M3R-29 primary-CI harness).
- **M1** — live hardware smoke test (unscored; run the **full 10 min**).
- **M8 step 1a/1b** — HA reproduction control, then adaptation transfer.

---

## 4. Decisions that matter (do not silently reverse)

### Working method — earned expensively this session
- **Read the FROZEN source, not just the plan.** `notes/analysis_prespec.md` outranks
  `plans/m4_offline_harness.md`, and the plan says so. Stage 1 was written from the plan's list of
  admission *causes*; §6 defines what each cause *decides*, and **three of ~9 predicates
  contradicted it** — all three more aggressive than frozen, i.e. silently discarding admissible
  sessions while looking like caution.
- **Mutation-check every rule before opening a review.** A negative test that still passes with the
  rule removed is worse than no test. Four tests were found vacuous during Stage 0, twice while
  fixing a vacuity finding. **But mutation testing proves only that each rule has *a* test depending
  on it — not that the test asserts the *right* semantics.** All 22 Stage 1 mutants passed against
  the three wrong rules.
- **Fixing the cited instances is not fixing the finding.** Three Stage 0 findings needed a round 2
  because the property behind them survived. What worked was **deleting speculative surface**.
- **A first `NO MORE COMMENTS` is provisional.** Codex signed off and reopened twice on Stage 0.

### M4 contracts now in code
- **Stage 0 precedes all M4 work** (M4R-10). **Never re-add a private DSP or warmup copy to
  `scripts/live_demo.py`** — `tests/test_window_pipeline_adapter.py` fails if anyone does.
- **`run_config_hash` takes an exact `dict` root** and accepts, by exact type, `None, bool, int,
  float, str, list, tuple, dict`, dispatched on `type(obj)` with **no `isinstance` in any encoding
  path**. NumPy and `pathlib` support were both tried and **removed after review** (S0R-07…12): six
  Blocking findings, every one a different way for a value to carry state the encoder could not see.
  **Do not "helpfully" re-add either.** It is an **exact-run provenance key only** — never an
  estimator-equivalence or grouping key.
- **A `WindowEstimate` with a true validity flag always carries a finite rate**, and validity flags
  must be an **exact `bool`** — `"false"`, `0`/`1` and `np.bool_` all raise (S0R-02, S0R-18).
  Equality is NaN-aware; the record is unhashable by design. **It is not the Stage 3 equality
  oracle** — that comparison uses the native DSP payload.
- **Admission predicates come from `notes/analysis_prespec.md` §6, verbatim in effect:** packet loss
  >5 % **flags**, never excludes (the validity map decides which *windows* are radar-NaN);
  a trailing-fragment truncation is **retained**; the item-3/item-4 discriminator is the single
  M3R-37 question, *did the run reach its intended duration?*

### Frozen analysis decisions
- **The quantile method is `linear`** (user, 2026-07-26, M4R-09) — written into both comparators and
  `notes/analysis_prespec.md` §1. **Pass it explicitly at every call site**; never rely on a library default.
- **A usable HR reference sample is `pr_bpm` finite ∧ `pi` finite ∧ `pi ≥ 0.5`** (user, M4R-11) —
  **one set** for the median, the stationarity quantiles and the coverage count.
- **No frozen scoring number can come from the 4 existing captures** (§7): they lack a persisted
  `frame0_epoch`, so alignment is APPROXIMATE by construction. M4's output on them is
  development-mode, labelled exploratory / apparent / in-sample. **`start_wall_utc` is NOT frame-0.**
- **M2 done-when #5 stays OPEN** — it is *not* superseded, and closes only when a capture carrying a
  persisted `frame0_epoch` is scored.
- **M4's regression anchor is Option A** (user, 2026-07-26): the pilot MAEs are **retired, not
  reproduced**. M4 is validated on synthetic windows with hand-computable answers.
- **Never put a number in a document bound for the M0 deposit unless it traces to a committed
  script** (M4R-13). Self-contained worked examples are the safe alternative.
- **Window is 30 s; do not shorten.** Statistics use **non-overlapping** 30 s windows only, and
  `k = 0` **is** scored.
- **Every HR agreement number goes through `notes/comparator_prespec.md`.** No exceptions.
- **Agreement CI: the whole-subject cluster bootstrap is PRIMARY** (M3R-29 Option A). Its `S_a ≤ 10`
  under-coverage is declared and is **ANTI-conservative** for the ≤5 bpm gate (M3R-45, user
  accepted). **MOVER is a pre-named *candidate* sensitivity** only. The constant `μ ± 1.96·SD` LoA
  point estimate is primary and never switched post-hoc.
- **The paced-arm LoA is a marginal design-weighted mixture over a FROZEN rate allocation** (M3R-31):
  rotation 12→15→18 ⇒ counts **4/3/3**. **HR paced pools 12/15 only**; **BR paced pools 12/15/18**.
- **BR reference = Masimo `rr_bpm`, cross-checked against the paced metronome.** RRp is pleth-derived
  and *appears* smoothed and laggy — an **inference**, not a measured device fact (M3R-38). Report it
  as a weaker, non-gold-standard reference than PR. **The BR comparator is designed from the
  reference alone.**
- **The pre-registration claim is "frozen before the confirmatory data", NOT "before any data
  existed."** Overstating it repeats the error that forced the "MAE 0.16 bpm" withdrawal.
- **Three capture classes:** *pre-freeze exploratory* (the 4 existing + M1), *post-freeze exploratory*
  (**M5**), *confirmatory* (**M6** onward).

### DSP decisions
- **The band-pass is a zero-phase order-4 Butterworth with an odd-reflected edge policy**
  (LFR-01/02). **Never reintroduce a rectangular/brick-wall mask** — it lets a 2·f_r artifact
  outrank the true cardiac peak at 20–22 bpm breathing.
- **`filtfilt` and `np.linalg.qr` must never enter the live/M4 path or the tests** — the Windows
  LAPACK crash is non-catchable. `butter`/`freqz` are safe.
- **Never use total in-band power as an ECA cancellation metric** (LFR-06) — it moves the *wrong way*.
- **Breathing rate is a co-equal goal**, not a by-product.
- **The M2 validity invariant: a selection on the first in-band FFT bin can never be
  `resp_valid=True`** — enforced by bin identity inside `fuse_estimates`.
- **The 7 unresolved edge windows are "spectrally unresolved", never classified by STFT/reference
  agreement** (that would be tuning to the reference, CLAUDE.md §4).
- **Do not implement the candidate-ranking fix ad hoc** — plan + cross-model review first.

### Protocol / admin
- **Recordings are 10 minutes.** Approval ceiling is 10 min — do not exceed, do not shorten.
- **Ethics approval `24IBEC051`, IBEC KAUST** — covers collection and publication, 10-min
  recordings, M7's collision manoeuvre. Recorded in `notes/protocol.md`.
- **Masimo intended-use disposition (M3R-34):** covered for attended 10-min healthy-subject sessions
  with a **verified battery**. Cite the manual as **`LAB-10168A`**.
- **Both `literature/ref_papers/` methods are an OFFLINE COMPARISON ARM**, not production replacements.
- **Sequencing is fix-blockers → pilot → study → method work.**
- **Never promote a status tag in `THIRD_CHAPTER.md` by editing it** — re-measure, or leave it.

---

## 5. Gotchas / landmines

- **Run Python via `conda run -n radar-vitals`.** Conda is at
  `C:\ProgramData\anaconda3\condabin\conda.bat`, **not on PATH**. Invoking the env's `python.exe` by
  absolute path skips activation; matplotlib then can't find its render DLLs and `savefig()`
  hard-kills the interpreter with exit 127 and no traceback.
- **Multi-line `python -c` and stdin heredocs under `conda run` silently produce no output.** Write a
  scratch `.py` file and run that. (This bit twice this session.)
- **`@'…'@` is PowerShell here-string syntax and is NOT valid in the Bash tool** — it silently puts a
  literal `@` in your commit subject. Use `-F <file>` for multi-line commit messages.
- **`bandpass_filter` RAISES on short input** (`n < ceil(fs/lo)`: 25 samples at 0.8 Hz, 200 at
  0.1 Hz) and on `lo ≥ hi`, `lo ≤ 0`, `hi ≥ Nyquist`. Production 400/600 windows are unaffected.
- **Two replay generations exist and are NOT interchangeable.** 2026-07-26 dirs are post-filter-fix;
  2026-07-25 dirs are pre-filter-fix. Any comparison must state which.
- **Pre-M2 replay NPZs (before 2026-07-25) have no `resp_valid` field** — infer validity from `f_r_hz`
  finiteness.
- **Warmup bin selection consumes BR/HR, so estimator changes can move the lock.** The filter fix did
  **not** move it (27/26/26 unchanged), but the M2 fix previously moved natural 23 → 27. Any future
  DSP change must re-check warmup decisions, not just per-window outputs.
- **`_save_intermediates` silently drops any record key that fails to stack** (`except: pass`).
- **The 10-minute duration is enforced by operator discipline only.** `scripts/live_demo_config.yaml`
  has `max_live_duration_s: null` (run until Ctrl-C).
- **A longer session stresses the bin lock** — check it at minute 9–10 with
  `scripts/diagnose_live_run.py`.
- **`elapsed_s` in replay NPZs is WALL-CLOCK**, ~0 under `--replay-fast`. Use `frame_idx / frame_rate_hz`.
- **`fallback_hr_bpm` is a LIAR** — a naive argmax, not the verified estimate. Use `hr_bpm_raw` /
  `candidate_refined_hz[0]`.
- **The live display holds the last valid HR** — always run `scripts/diagnose_live_run.py` before
  believing a live run.
- **`scripts/validate_warmup_selection.py`'s `SESSIONS` expectation table is STALE** (pre-M2: it
  expects bin 23 for `massimo1`, but the current lock is 27). Predates the Stage 0 work.
- **M7's rate must NOT be computed from resting HR** — set it from HR measured *while paced*.
- **A synthetic test asserting "candidate tracks harmonic order k" must first confirm k is admissible
  at that f_r** (`k*f_r` must land in 0.8–2.0 Hz).
- **Masimo: `Beats / min` (PR) for heart, `Breaths / min` (RR) for respiration — never SpO2/PI/PVi.**
  Align on the integer `Timestamp` column, never the `Date`/`Time` strings.
- **I/Q ordering depends on capture source**: SDK/Python captures need `iq_swap=True`; mmWave Studio
  captures `iq_swap=False`.
- **Respiratory-harmonic coincidence** (`notes/approach.md` §4.2): when `k·f_r ≈ HR`, ECA can cancel
  the cardiac signal with the harmonic. Identifiability problem — longer windows do not help.
- **`experiments/` and `figures/` do not exist and cannot be restored from git** — the only tracked
  placeholders are `data/processed/.gitkeep` and `results/.gitkeep`. Create them with `mkdir`.

---

## 6. The two reference papers (both simulation-only — a live opportunity)

`literature/ref_papers/`, both IEEE Trans. Radar Systems vol. 2 (2024), both KAUST:

- **Paper 1 — Ahmed et al., "Discovering the Unseen"** (DOI 10.1109/TRS.2024.3412915). Its Harmonic
  Accumulation is already the primary BR estimator in `src/respiration.py`, but **adapted**: the paper
  is a **pulse radar, single TX / single RX**, built on **2f_h and 2f_b and their harmonics**, whereas
  ours is all-harmonic phase. **Never validated against Masimo.** Its Fig. 8(c)–(d) claims correct
  estimation *at* the 4·f_r = HR collision — the project's central unsolved problem. **M8 must
  reproduce the paper on its own terms FIRST (step 1a), then run the adaptation (step 1b)**;
  otherwise a failure cannot be told apart from our own bug, and a success reproduces nothing.
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
| **M4 build plan — rev 6, the ACTIVE build authority** | `plans/m4_offline_harness.md` |
| **Stages 1+2 review — OPEN, gates Stage 3** | `plans/m4_stage12_review.md` |
| Stages 1+2 review — Codex-side prompt | `plans/m4_stage12_codex_review_prompt.md` |
| Stage 0 code review — COMPLETE (S0R-01…20) | `plans/m4_stage0_refactor_review.md` |
| M4 plan review record — COMPLETE (M4R-01…15) | `plans/m4_plan_cross_review.md` |
| **M4 Stage 1 — manifest schema + admission recomputation** | `src/m4/manifest.py` |
| **M4 Stage 2 — frozen window grid** | `src/m4/window_grid.py` |
| M4 Stage 1/2 tests | `tests/test_m4_manifest.py`, `tests/test_m4_window_grid.py` |
| **Core DSP (Butterworth band-pass + ECA + AHET)** | `src/vitals.py` |
| Breathing-rate DSP (HA + M2 peak-validity fix) | `src/respiration.py` |
| **Window-level DSP composition (shared by live + M4)** | `src/window_pipeline.py:run_window_dsp` |
| **Warmup bin-lock + eligibility logic (shared)** | `src/warmup_select.py:run_warmup_selection` |
| Estimator adapter + `run_config_hash` tests | `tests/test_window_pipeline_adapter.py` |
| Tracked config fixture (provenance regression guard) | `tests/fixtures/sample_run_config.json` |
| Radar I/O (4-RX cube, `iq_swap`) | `src/radar_io.py` |
| Masimo parser (PR, RR, PI) | `src/masimo.py` |
| Agreement metrics — **OLD mean-based comparator, M4 must replace** | `src/compare.py` |
| Bland–Altman — **plotting only**, statistics invalid for repeated measures | `scripts/plot_bland_altman.py` |
| Method, literature, algorithm spec | `notes/approach.md` |
| **Pre-registered HR comparator (binding)** | `notes/comparator_prespec.md` |
| **BR comparator (binding)** | `notes/comparator_prespec_br.md` |
| **Analysis pre-spec — FROZEN-in-effect; §6 exclusions, §7 window grid** | `notes/analysis_prespec.md` |
| Linalg-free DSP review — COMPLETE (LFR-01…07) | `plans/m4_linalg_free_dsp_review.md` |
| Filter-fix impact measurement | `scripts/compare_filter_fix_impact.py` |
| M3 / M2 cross-review records | `plans/m3_prespec_cross_review.md`, `plans/m2_fix_cross_review.md` |
| M0 pre-registration draft + evidence-floor memo | `plans/m0_preregistration.md`, `plans/m0_b1_evidence_floor_memo.md` |
| Capture inventory (hashes) | `notes/capture_inventory.md` |
| Capture protocol / SOP (10-min; ethics `24IBEC051`) | `notes/protocol.md` |
| Live/replay demo **and capture tool** | `scripts/live_demo.py` |
| Standalone headless capture | `steps/step_1/capture.py` |
| Run post-mortem ("why was HR blank?") | `scripts/diagnose_live_run.py` |
| Bin-lock validation (**expectation table is stale**) | `scripts/validate_warmup_selection.py` |
| Thesis chapter / paper planning | `THIRD_CHAPTER.md`, `JOURNAL_PAPER.md` |
| Reference papers (simulation-only) | `literature/ref_papers/` |
| Full project history | `HISTORY.md` |

### Run artifacts written per live run
`results/live_demo/<ts>_<mode>_<session>/` contains `run_metadata.json` (config, git commit, packet
stats), `warmup_bin_selection.json` (per-candidate bin-lock evidence), `live_estimates.csv` (per-hop
HR/BR + rejection reasons — **diagnostic only, never a scoring input**), `live_intermediates.npz`
(phase + spectra + AHET candidates + M2 respiration evidence; `elapsed_s` inside is wall-clock) and
`adc_stream.bin` (raw mirror — **this is M4's input**).
