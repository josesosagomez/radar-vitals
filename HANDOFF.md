# Handoff — radar-vitals

> **Read this + `CLAUDE.md` and you can resume the project.** State **as of 2026-07-27**.
> Rewritten, not appended (CLAUDE.md §10). For *what happened and why*, read `HISTORY.md`.
> Every claim below was re-verified against the repo at the time of writing.
>
> **The active job is BUILDING M4 Stage 1.** Its cross-review (`plans/m4_stage12_review.md`)
> is **OPEN** after six rounds and 25 findings. **Four findings + one artifact binding are
> unbuilt** — that is your first task. **Stage 3 does not begin until the review closes.**
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

**You are not working on the DSP or on the reference papers.** M4 Stage 1 is the *session
manifest*: schema, validation, and the objective disposition recomputation that decides whether a
recorded session is admitted, excluded, or has no reference — with every input file bound by
path + SHA-256. No equations. The two reference papers are M8/M9, two milestones away.

**Method rationale, literature and algorithm spec: `notes/approach.md`.**
**Whole-project milestone plan: `plans/implementation_plan.md`** — the scope authority.

---

## 2. Current state

**Branch `vital_signs_v9c`.** Suite **1616 passed, 1 skipped, 0 failed**
(`conda run -n radar-vitals python -m pytest tests/ -q`, 2026-07-27); **508 passed, 1 skipped**
in the two targeted Stage 1/2 files. The single skip is a parametrised protocol-identity case
whose fixture already matches on `posture`, so there is nothing to differ. v9 UI work remains
parked in `git stash@{0}`; relocking/display-holdover stay reverted.

**Measure both figures by running the commands. Never derive one by arithmetic** — that was
S12R-15, a number in a review document that traced to nothing.

### Where M4 is

| Stage | State |
|---|---|
| **0** — shared-callable refactor | **DONE**, review **CLOSED** (`plans/m4_stage0_refactor_review.md`) |
| **1** — manifest schema + validation | **BUILT**, review **OPEN with 5 items unbuilt** |
| **2** — window grid | **BUILT** (`src/m4/window_grid.py`), review **OPEN** |
| **3–8** | **not started** — 3 gated on the review closing |

`src/m4/` contains only `__init__.py`, `manifest.py`, `window_grid.py`. There is no
`reprocess.py`, no reference aggregation, no ledger, no statistics, no outputs and no
`scripts/run_m4_harness.py`. **No paper-grade number exists yet.**

### The review: 25 findings, six rounds, none rejected

Codex has raised **S12R-01…25**. **Every behavioural claim was reproduced before being agreed
with, and not one finding was rejected.** The recurring failure mode is worth internalising
before you write anything: **I repeatedly built a rule and then failed to enforce it.** The
clearest case is S12R-16 — the entire `SessionDisposition` partition was built over three
rounds, and then *nothing gated on it*, so excluded sessions passed every scoring guard.

Schema is at **version 2**. What exists now in `src/m4/manifest.py`:

- `SessionDisposition` — `ADMITTED` / `EXCLUDED` / `NO_AGREEMENT`, one §6 partition key.
- `RecordKind` — `captured_session` / `pre_capture_attempt`, with **exact** per-kind field sets;
  capture-only fields are *forbidden* on an attempt, rejected by **presence**.
- Verification is a **capability**, not a boolean: `_VerifiedBindings` is identity-checked
  against a module-private `_VERIFY_TOKEN` that only `verify_bound_files` can produce, and
  `SessionManifest.bindings_verified` is derived from it.
- One eligibility base gates every scorability question: SCORING ∧ captured ∧ ADMITTED ∧ verified.
- `NO_AGREEMENT` is **derived** from a parsed, versioned acquisition record — never declared.
- `retry_status` is a **checked summary of the replacement links**; the graph is walked in both
  directions, self-links rejected, cycles detected, "same subject, same protocol" enforced.
- `scorable_for(MethodProvenance)` — M7 collision scorability is method-dependent, and the
  leakage check applies to **every** role.

---

## 3. Active task / next steps

### 3.0 Finish the five unbuilt items — **this gates Stage 3**

All five are reproduced, agreed, and specified in `plans/m4_stage12_review.md`. In dependency
order, the sensible grouping is:

1. **S12R-05 R3 + S12R-12 R3 + S12R-22 — make evidence conditional on the stage reached.**
   - `selected_confidence` is unconditionally required, but `warmup_bin_selection.json` only
     exists after the first complete 600-frame buffer. A subject withdrawal before then is a
     captured §6 item-3 abort that can currently be logged **only by fabricating** a confidence
     value. Represent "warmup not reached" objectively (not as `low`), and apply the item-7
     iff/retry rules only when a verdict exists.
   - A `pre_capture_attempt` always requires **both** settle evidence and
     `clock_offset_start_s`, but `notes/protocol.md` step 3 (settle) precedes step 3a (clock
     sync) — so a settle-failed attempt legitimately has no clock measurement. Discriminate the
     failed stage; keep the both-gates-pass contradiction.
   - A paced `pre_capture_attempt` may omit `commanded_rate_bpm` entirely and accepts
     off-rotation values (17 parsed fine). The rate is a **prospectively assigned** design
     attribute; require it and enforce 12/15/18 in SCORING mode.
2. **S12R-24 — bind the validity-map encoding.** Polarity is currently a private convention
   (`True` = valid) with no producer contract. When exactly half the frames are invalid, a
   producer using the opposite convention passes every count check while identifying the
   **opposite frames** — Stage 3 would then NaN the wrong windows silently. Pin format, dtype,
   dimensionality, polarity and schema version in the manifest so it is declared, not assumed.
3. **S12R-21's artifact binding.** `MethodProvenance` is a caller-authored in-memory dataclass,
   so omitting a session ID is enough to "prove" eligibility. Bind and validate a canonical
   provenance record at the scoring boundary.

Then write the round-7 debate entry and poll Codex. **The loop ends only at
`NO MORE COMMENTS`.**

### 3.1 Then Stage 3, then Stage 4

Grouping already decided with the user: **1+2 together** (done), **3 alone**, **4 alone**.

- **Stage 3 — raw-ADC exact-grid reprocessing** (plan §6.1). The highest-risk stage: its
  done-when *is* the equality test Stage 0 existed to make meaningful — *"M4's per-window DSP
  output equals a direct shared-DSP call on the same saved 600-frame slice at full precision"* —
  plus a forced DSP failure yielding a **recorded** radar-NaN (never a missing window), plus
  `k = 0` reproducing the warmup semantics. Golden fixture 2 (§7.1) belongs here.
- **Stage 4 — reference aggregation + gates**, on **raw-format** Masimo CSVs. Touches the Masimo
  parser path, so it takes a **mandatory** CLAUDE.md §6 review. Every gate tested **at equality**
  (PI 0.5, coverage 24/30, 5.0 / 2.0 bpm); `linear` named explicitly. Golden fixture 1 here.

**M4's radar input is the original capture folder's `adc_stream.bin` — NEVER `live_estimates.csv`**
(M4R-01, CLAUDE.md §4).

### 3.2 After M4
- **M0** — pre-registration freeze + Zenodo deposit. **Two escalations resolve here** (§4).
- **M1** — live hardware smoke test (unscored; run the **full 10 min**).
- **M8 step 1a/1b** — HA reproduction control, then adaptation transfer.

---

## 4. Decisions that matter (do not silently reverse)

### Two escalations wait on the M0 freeze — neither may be decided in code
- **S12R-01 — the §6 item-4 truncation limb is UNIMPLEMENTED.** §6 names `mirror_truncated_bytes`
  as the mechanism for detecting a mid-recording cut, but `LiveFrameSource` sets it to
  `file_size % bytes_per_frame` — a sub-frame remainder that can never locate one.
  `test_the_item_4_truncation_limb_is_UNIMPLEMENTED_and_escalated` pins the state; delete it only
  together with the resolution.
- **S12R-18 — settle evidence is declared, not derived.** The loader hashes
  `settle_evidence_path` and never reads it. Deriving the criterion needs a rule for how the
  first and last 20 s reduce to two comparable PR values, which `notes/protocol.md` does not
  state — choosing it in code would add pre-registration content. **User decision 2026-07-27:
  defer, on the basis that no study capture exists yet.**

### Working method — earned expensively over six rounds
- **Read the FROZEN source for what it DECIDES, not for the detail you went looking for.** Two
  of this project's worst errors were exactly this, including a test that asserted the **opposite**
  of `notes/protocol.md` because the file was read for rates and not for the sentence classifying
  the capture as method development.
- **Building a rule is not enforcing it.** S12R-16 is the canonical case. When you add a
  disposition, a flag or a partition, immediately ask what *reads* it.
- **Fix the class, not the cited instances.** S12R-10 R2 existed only because round 1 fixed the
  four proven examples and left two other fields on permissive `float()`.
- **A guard no test can fail on is not a rule.** Five have been deleted on that ground.
- **Mutation testing misses in both directions, demonstrated twice:** it cannot catch a rule
  **never written** nor one that **should never have been written**. Run it; never treat a clean
  sweep as evidence the semantics are right.
- **A first `NO MORE COMMENTS` is provisional.** Codex signed off and reopened twice on Stage 0.

### M4 contracts now in code
- **Stage 0 precedes all M4 work** (M4R-10). **Never re-add a private DSP or warmup copy to
  `scripts/live_demo.py`** — `tests/test_window_pipeline_adapter.py` fails if anyone does.
- **Exact types everywhere, via `type(x) is`, never `isinstance`.** `bool` subclasses `int` and
  `isinstance` admits `np.bool_`; `bool("false")` is `True`, which is how a FAILED checksum was
  once ADMITTED.
- **A malformed primitive is a `ManifestError`, never a §6 reason.** Exclusion reasons are
  reported study-wide, so a schema defect logged as a disposition puts a fabricated cause in a
  published table.
- **Never reintroduce an operator-declared integrity fact.** `checksum_ok` was removed, not
  cross-checked: two independently editable declarations of one fact leave someone deciding which
  controls the frozen disposition.
- **Verification is a capability, not a boolean**, and any derived flag must be derived from it —
  a plain `bindings_verified: bool` field was settable by a direct constructor call.
- **`run_config_hash` takes an exact `dict` root**, dispatches on `type(obj)`, and has **no
  `isinstance` in any encoding path**. NumPy and `pathlib` support were tried and **removed after
  review** (S0R-07…12). It is an **exact-run provenance key only**.
- **The M4 scoring grid is frozen at 20 Hz / 600 frames** and the API rejects anything else.
  Validate arguments, then compute from the **validated** values.

### Frozen analysis decisions
- **The quantile method is `linear`** (user, M4R-09). **Pass it explicitly at every call site.**
- **A usable HR reference sample is `pr_bpm` finite ∧ `pi` finite ∧ `pi ≥ 0.5`** (user, M4R-11) —
  **one set** for the median, the stationarity quantiles and the coverage count.
- **No frozen scoring number can come from the 4 existing captures**: they lack a persisted
  `frame0_epoch`, so alignment is APPROXIMATE by construction. **`start_wall_utc` is NOT frame-0.**
- **M2 done-when #5 stays OPEN** — closes only when a capture carrying a persisted `frame0_epoch`
  is scored.
- **M4's regression anchor is Option A** (user): the pilot MAEs are **retired, not reproduced**.
- **Never put a number in a document bound for the M0 deposit unless it traces to a committed
  script** (M4R-13) — **and measure it, never derive it** (S12R-15).
- **Window is 30 s; do not shorten.** Non-overlapping only, and `k = 0` **is** scored.
- **The `(12, 15, 18)` rotation is the STUDY allocation and binds SCORING only** (S12R-14).
  Development mode must load the real captures, including `massimo2`'s **16 bpm**.
- **The stepped 12→15→18→21 sweep is method development, not a study session**
  (`notes/protocol.md`). A multi-entry rate schedule loads in DEVELOPMENT mode only.
- **§3.1 makes M6 evaluation data "never tuning."** A method declaring itself fit on an
  evaluation session is a **design violation**, not permission to confirm on it.
- **Agreement CI: the whole-subject cluster bootstrap is PRIMARY** (M3R-29 Option A). Its
  `S_a ≤ 10` under-coverage is declared and is **ANTI-conservative** for the ≤5 bpm gate
  (M3R-45, user accepted). **MOVER is a pre-named *candidate* sensitivity** only.
- **The paced-arm LoA is a marginal design-weighted mixture over a FROZEN rate allocation**
  (M3R-31): rotation 12→15→18 ⇒ counts **4/3/3**. **HR paced pools 12/15 only**; **BR pools
  12/15/18**.
- **BR reference = Masimo `rr_bpm`**, cross-checked against the paced metronome. RRp being
  smoothed and laggy is an **inference**, not a measured device fact (M3R-38).
- **Three capture classes:** *pre-freeze exploratory* (the 4 existing + M1), *post-freeze
  exploratory* (**M5**), *confirmatory* (**M6** onward).

### DSP decisions
- **The band-pass is a zero-phase order-4 Butterworth with an odd-reflected edge policy**
  (LFR-01/02). **Never reintroduce a rectangular/brick-wall mask.**
- **`filtfilt` and `np.linalg.qr` must never enter the live/M4 path or the tests** — the Windows
  LAPACK crash is non-catchable. `butter`/`freqz` are safe.
- **Never use total in-band power as an ECA cancellation metric** (LFR-06).
- **Breathing rate is a co-equal goal**, not a by-product.
- **The M2 validity invariant: a selection on the first in-band FFT bin can never be
  `resp_valid=True`** — enforced by bin identity inside `fuse_estimates`.
- **The 7 unresolved edge windows are "spectrally unresolved", never classified by
  STFT/reference agreement** (that would be tuning to the reference, CLAUDE.md §4).

### Protocol / admin
- **Recordings are 10 minutes.** Approval ceiling is 10 min — do not exceed, do not shorten.
- **Ethics approval `24IBEC051`, IBEC KAUST.** Recorded in `notes/protocol.md`.
- **Masimo intended-use disposition (M3R-34):** covered for attended 10-min healthy-subject
  sessions with a **verified battery**. Cite the manual as **`LAB-10168A`**.
- **Both `literature/ref_papers/` methods are an OFFLINE COMPARISON ARM**, not production
  replacements.
- **Never promote a status tag in `THIRD_CHAPTER.md` by editing it** — re-measure, or leave it.

---

## 5. Gotchas / landmines

- **Run Python via `conda run -n radar-vitals`.** Conda is at
  `C:\ProgramData\anaconda3\condabin\conda.bat`, **not on PATH**. Invoking the env's `python.exe`
  by absolute path skips activation; matplotlib then can't find its render DLLs and `savefig()`
  hard-kills the interpreter with exit 127 and no traceback.
- **Multi-line `python -c` and stdin heredocs under `conda run` silently produce no output.**
  Write a scratch `.py` file and run that.
- **PowerShell `-replace` is CASE-INSENSITIVE by default** and will mangle string literals in a
  bulk rename. Use `-creplace`, or better, do bulk edits with a scratch Python script.
- **PowerShell has no heredocs**, and a here-string written with LF will not match a CRLF file.
  Use `-F <file>` for multi-line commit messages.
- **A mutation harness must (a) VERIFY its restore and (b) impose a per-run timeout.** It has
  damaged the working tree **twice** this project: once when `write_text` raised
  `OSError [Errno 22]` in a `finally` block, and once when a mutant made the suite hang and the
  run was killed. Treat a hang as a *caught* mutant. After any harness run, check for stray
  `if False:` and confirm the suite count.
- **`bandpass_filter` RAISES on short input** (`n < ceil(fs/lo)`) and on `lo ≥ hi`, `lo ≤ 0`,
  `hi ≥ Nyquist`. Production 400/600 windows are unaffected.
- **Two replay generations exist and are NOT interchangeable.** 2026-07-26 dirs are
  post-filter-fix; 2026-07-25 dirs are pre-filter-fix. Any comparison must state which.
- **Pre-M2 replay NPZs (before 2026-07-25) have no `resp_valid` field** — infer validity from
  `f_r_hz` finiteness.
- **Warmup bin selection consumes BR/HR, so estimator changes can move the lock.** The filter fix
  did **not** move it (27/26/26 unchanged), but the M2 fix previously moved natural 23 → 27.
- **`_save_intermediates` silently drops any record key that fails to stack** (`except: pass`).
- **The 10-minute duration is enforced by operator discipline only.**
  `scripts/live_demo_config.yaml` has `max_live_duration_s: null`.
- **`elapsed_s` in replay NPZs is WALL-CLOCK**, ~0 under `--replay-fast`. Use
  `frame_idx / frame_rate_hz`.
- **`fallback_hr_bpm` is a LIAR** — a naive argmax, not the verified estimate. Use `hr_bpm_raw` /
  `candidate_refined_hz[0]`.
- **The live display holds the last valid HR** — always run `scripts/diagnose_live_run.py` before
  believing a live run.
- **`scripts/validate_warmup_selection.py`'s `SESSIONS` expectation table is STALE** (it expects
  bin 23 for `massimo1`; the current lock is 27).
- **`packets_dropped` MAY exceed `packets_received`** — they are not a partition. `n_received`
  counts arriving packets; `n_dropped` counts sequence-gap **sizes**. Rejecting that ratio makes
  the worst-loss sessions unloadable and inflates coverage (S12R-13).
- **Masimo: `Beats / min` (PR) for heart, `Breaths / min` (RR) for respiration — never
  SpO2/PI/PVi.** Align on the integer `Timestamp` column, never the `Date`/`Time` strings.
- **I/Q ordering depends on capture source**: SDK/Python captures need `iq_swap=True`; mmWave
  Studio captures `iq_swap=False`.
- **M7's rate must NOT be computed from resting HR** — set it from HR measured *while paced*.
- **Respiratory-harmonic coincidence** (`notes/approach.md` §4.2): when `k·f_r ≈ HR`, ECA can
  cancel the cardiac signal with the harmonic. Identifiability problem — longer windows do not help.
- **`experiments/` and `figures/` do not exist and cannot be restored from git** — the only
  tracked placeholders are `data/processed/.gitkeep` and `results/.gitkeep`.

---

## 6. Data

**4 real captures, ~2.52 GB raw, all with saved `adc_stream.bin`** in `results/live_demo/`
(verified: 4 directories contain one). **Only 3 carry a Masimo reference**:
`20260713_170323_..._live_test1` (**no Masimo**, 120 s), `20260713_172042_..._massimo1`
(natural, 180 s), `20260713_182002_..._massimo2` (**paced 16 bpm**, 180 s),
`20260714_180523_..._sweep` (stepped 12→15→18→21 bpm, 480 s). **All from one subject, and all
exploratory** — they informed the method's design, so they can never be confirmatory evidence.
`data/raw/` is **empty**; `data/processed/` holds only a tracked `.gitkeep`; **`experiments/` and
`figures/` do not exist**; `data/manifest.local.csv` is header-only (0 sessions).

**There are no citable results.** The old pilot MAEs (0.19 / 0.50 / 0.53 bpm) are **retired**:
no committed script produced them, they used a nearest-hop rule §7 later froze differently, and
they predate the band-pass fix. **Do not quote or resurrect them.**

### Known broken / open (DSP side)
- **Coverage 17–55 %** — AHET conservatism is the bottleneck.
- **Bland–Altman is not implemented correctly for this study.** `scripts/plot_bland_altman.py`
  pools every window as an independent pair (no subject/session term). **Plotting only.**
- **Candidate ranking** — magnitude order ≠ credibility order. Confirmed, n=1, no plan written.
- **Stage 1B lag-10 veto** — verified on synthetics but **blocked**: zero baseline severe accepts
  exist post-bin-fix. Unblocked only by M7.
- **ECA v1** (`skip_forbidden_harmonics_v1`, the live mode) removes **0.00 dB** in the cardiac
  band at low breathing rates — production harmonic cancellation is effectively inert.
- **`guard_cardiac_candidate_v1`** implemented and unit-tested but **not promoted** — blocked on
  `experiments/exp_eca_modes`, and `experiments/` does not exist.
- **Genuine ≈6 bpm breathing at the edge bin is permanently `resp_valid=False`** — a deliberate,
  declared coverage sacrifice mandated by the M2 invariant.

---

## 7. The two reference papers (both simulation-only — a live opportunity)

`literature/ref_papers/`, both IEEE Trans. Radar Systems vol. 2 (2024), both KAUST. **Not the
current work — these are M8/M9.**

- **Paper 1 — Ahmed et al., "Discovering the Unseen"** (DOI 10.1109/TRS.2024.3412915). Its
  Harmonic Accumulation is already the primary BR estimator in `src/respiration.py`, but
  **adapted**: the paper is a **pulse radar, single TX / single RX**, built on **2f_h and 2f_b and
  their harmonics**, whereas ours is all-harmonic phase. **Never validated against Masimo.** Its
  Fig. 8(c)–(d) claims correct estimation *at* the 4·f_r = HR collision — the project's central
  unsolved problem. **M8 must reproduce the paper on its own terms FIRST (step 1a), then run the
  adaptation (step 1b)**; otherwise a failure cannot be told apart from our own bug.
- **Paper 2 — Kotte et al.** (DOI 10.1109/TRS.2024.3352189). **The "1 TX / 4 RX = the SIMO setup
  the paper assumes" claim was FALSE and is retracted.** Its §IV simulates **one TX and 20 RX** at
  24 GHz, 50 ms PRI, `N_c = 16`. Eq. (23) defines `Y_t ∈ C^(N_c×n_R)` and eq. (25) requires
  `R_t⁻¹` where `R_t = E{Y_t Y_tᴴ}` is **N_c × N_c** — the **RX channels supply the snapshots**,
  so at `n_R = 4`, `rank(R_t) ≤ 4` and `R_t` is **singular**. Its Fig. 5 validates on generic
  moving-target Dopplers, **not HR/BR**. **M9 requires a paper-faithful 1×20 / `N_c`=16
  reproduction, then a registered 4-RX ablation before any existing capture is touched.**

---

## 8. Pointers

| What | Where |
|---|---|
| Project rules (read first) | `CLAUDE.md` |
| **Whole-project milestone plan** | `plans/implementation_plan.md` |
| **M4 build plan — rev 6, the ACTIVE build authority** | `plans/m4_offline_harness.md` |
| **Stages 1+2 review — OPEN, 5 items unbuilt, gates Stage 3** | `plans/m4_stage12_review.md` |
| Codex-side review prompt (verification-pass revision) | `plans/m4_stage12_codex_review_prompt.md` |
| Stage 0 code review — COMPLETE (S0R-01…20) | `plans/m4_stage0_refactor_review.md` |
| M4 plan review record — COMPLETE (M4R-01…15) | `plans/m4_plan_cross_review.md` |
| **M4 Stage 1 — manifest, verification, disposition, retry graph** | `src/m4/manifest.py` |
| **M4 Stage 2 — frozen window grid** | `src/m4/window_grid.py` |
| M4 Stage 1/2 tests | `tests/test_m4_manifest.py`, `tests/test_m4_window_grid.py` |
| **Core DSP (Butterworth band-pass + ECA + AHET)** | `src/vitals.py` |
| Breathing-rate DSP (HA + M2 peak-validity fix) | `src/respiration.py` |
| **Window-level DSP composition (shared by live + M4)** | `src/window_pipeline.py:run_window_dsp` |
| **Warmup bin-lock + eligibility logic (shared)** | `src/warmup_select.py:run_warmup_selection` |
| Estimator adapter + `run_config_hash` tests | `tests/test_window_pipeline_adapter.py` |
| Radar I/O (4-RX cube, `iq_swap`) | `src/radar_io.py` |
| Masimo parser (PR, RR, PI) | `src/masimo.py` |
| Agreement metrics — **OLD mean-based comparator, M4 must replace** | `src/compare.py` |
| Bland–Altman — **plotting only**, statistics invalid for repeated measures | `scripts/plot_bland_altman.py` |
| Method, literature, algorithm spec | `notes/approach.md` |
| **Pre-registered HR comparator (binding)** | `notes/comparator_prespec.md` |
| **BR comparator (binding)** | `notes/comparator_prespec_br.md` |
| **Analysis pre-spec — FROZEN-in-effect; §6 dispositions, §7 window grid** | `notes/analysis_prespec.md` |
| Linalg-free DSP review — COMPLETE (LFR-01…07) | `plans/m4_linalg_free_dsp_review.md` |
| M3 / M2 cross-review records | `plans/m3_prespec_cross_review.md`, `plans/m2_fix_cross_review.md` |
| M0 pre-registration draft + evidence-floor memo | `plans/m0_preregistration.md`, `plans/m0_b1_evidence_floor_memo.md` |
| **Capture inventory (hashes, arms, rates)** | `notes/capture_inventory.md` |
| Capture protocol / SOP (10-min; settle criterion; ethics `24IBEC051`) | `notes/protocol.md` |
| Live/replay demo **and capture tool** | `scripts/live_demo.py` |
| Standalone headless capture | `steps/step_1/capture.py` |
| Run post-mortem ("why was HR blank?") | `scripts/diagnose_live_run.py` |
| Bin-lock validation (**expectation table is stale**) | `scripts/validate_warmup_selection.py` |
| Thesis chapter / paper planning | `THIRD_CHAPTER.md`, `JOURNAL_PAPER.md` |
| Reference papers (simulation-only, M8/M9) | `literature/ref_papers/` |
| Full project history | `HISTORY.md` |

### Run artifacts written per live run
`results/live_demo/<ts>_<mode>_<session>/` contains `run_metadata.json` (config, git commit, packet
stats), `warmup_bin_selection.json` (per-candidate bin-lock evidence), `live_estimates.csv` (per-hop
HR/BR + rejection reasons — **diagnostic only, never a scoring input**), `live_intermediates.npz`
(phase + spectra + AHET candidates + M2 respiration evidence; `elapsed_s` inside is wall-clock) and
`adc_stream.bin` (raw mirror — **this is M4's input**).
