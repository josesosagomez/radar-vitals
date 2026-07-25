# Handoff — radar-vitals

> **Read this + `CLAUDE.md` and you can resume the project.** State **as of 2026-07-25**.
> Rewritten, not appended (CLAUDE.md §10). For *what happened and why*, read `HISTORY.md`.
> Every claim below was re-verified against the repo at the time of writing.
>
> **The next chat's job is to cross-review and freeze the BR comparator (M3)** — see §3.
> Read `plans/implementation_plan.md` first.

---

## 1. Project snapshot

Estimate **heart rate and — co-equally — breathing rate** from a 77 GHz FMCW radar
(TI IWR1642BOOST + DCA1000EVM) for a subject seated 0.8–1.4 m from the sensor, chest facing the
radar. Ground truth is a Masimo MightySat pulse oximeter (1 Hz CSV: `Beats / min` = PR for heart,
`Breaths / min` = RR for respiration). HR agreement is scored under a **pre-registered comparator**
(`notes/comparator_prespec.md`) — binding on every HR number in the paper. **The BR comparator
exists** (`notes/comparator_prespec_br.md`) and its **M3 cross-review is 9 rounds deep with all 28
findings (M3R-01…28) resolved** — see `plans/m3_prespec_cross_review.md` `DEBATE COMMENTS` for the
authoritative state. It is **not yet formally closed** (awaiting Codex's `NO MORE COMMENTS`) and
**not frozen** — closing it is the only thing M0 waits on. The DSP extracts chest-wall phase from an
auto-locked range bin, cancels respiration harmonics (ECA) and verifies the cardiac peak via a
second-harmonic check (AHET). Output: journal paper + thesis chapter.

**Method rationale, literature and algorithm spec: `notes/approach.md`.**
**Whole-project milestone plan: `plans/implementation_plan.md`** — read this before starting work.
It survived a full cross-model review on 2026-07-24 (19 comments, 3 rounds) and is the scope
authority for every milestone.

---

## 2. Current state

**Branch `vital_signs_v9c`.** Test suite **821 passed, 1 xfailed, 0 failed**
(`conda run -n radar-vitals python -m pytest tests/ -q`, verified 2026-07-25). The xfail is a known
design hole in the ECA/AHET decoy case. v9 UI work remains parked in `git stash@{0}`;
relocking/display-holdover stay reverted. The working tree is committed as of this rewrite
(M0/M3 prep docs + the M2 fix, both pushed).

**Ethics approval is IN HAND and now fully recorded** (user-confirmed 2026-07-25): approval
**`24IBEC051`**, issuing board **IBEC, KAUST**, covers collection **and** publication, permits
recordings up to **10 minutes** and M7's collision manoeuvre. Recorded in `notes/protocol.md`
(tracked); the approval document itself is not in this repo.

### What is built and trustworthy
Raw ADC I/O with `iq_swap` handling · range FFT · static clutter removal · phase extraction and
unwrap · **warmup range-bin lock with the energy-eligibility fix** (committed `863600e` + `dfe7fb5`,
3× cross-reviewed) · ECA + AHET heart-rate core · **breathing-rate DSP with the M2 peak-validity
fix** (2026-07-25, cross-reviewed 7 findings / 2 rounds / 0 escalations —
`plans/m2_respiration_fix.md` + `plans/m2_fix_cross_review.md`) · Masimo parser · quality mask ·
live demo with capture, replay, raw mirroring and diagnostics · the HR comparator spec. All under
test.

### M2 — respiration floor-pin: FIXED and verified (was the top blocker)
The estimator silently reported **6.0 bpm (`band_hz[0]`) with `resp_valid=True`** — 29 windows
across natural (13) and sweep (16), 0 on paced-16. Root cause (verified from the checkpointed
NPZs; regenerate with `scripts/diagnose_respiration_collapse.py`, which records NPZ SHA-256s):
two observed classes — **both-branch edge selection** (23 windows, 16 with a monotone
leakage-decay signature) and **HA-only edge selection** (6 windows, 4 supported by 3rd-harmonic
inheritance) — blessed as valid by two fusion flaws (floor agreement read as confirmation; STFT
stability of a *different* rate blessing HA's value). 7/29 pinned windows are **spectrally
unresolved** (the edge bin is a strict local max) and are suppressed only by the band-edge veto.
Fix in `src/respiration.py`: local-max plateau predicate (FFT argmax + HA fundamentals),
**bin-identity band-edge veto** in fusion, STFT median-match on **all** STFT-dependent branches;
14 new NPZ evidence fields; 25 new tests (`tests/test_respiration_m2_fix.py`).
**Verified on all 4 reprocessed captures (2026-07-25): 0 floor-pinned-and-valid windows; paced-16
bitwise unchanged; HR bitwise identical at fixed bins (max |ΔHR| = 0.000 bpm) — the predeclared
HR stop was not triggered.** Formerly pinned windows now read 13.3–22.2 bpm or NaN (the 7
unresolved, via `resp_edge_veto`).
**Still open on M2: done-when #5** — BR scored under the frozen comparator. Deferred until M3
freezes (scoring against the unfrozen draft would produce numbers the frozen version could void).

### Data
**4 real captures, ~2.52 GB raw, all with saved `adc_stream.bin`** in `results/live_demo/`.
**Only 3 carry a Masimo reference**:
`20260713_170323_..._live_test1` (**no Masimo**), `20260713_172042_..._massimo1` (natural),
`20260713_182002_..._massimo2` (paced 16), `20260714_180523_..._sweep` (stepped 12→15→18→21 bpm,
486 s). **All from one subject, and all exploratory** — they informed the method's design, so they
can never be confirmatory evidence.
Replay artefact dirs (not sessions): pre-fix evidence at `20260715_164018` (paced16) / `_164124`
(natural, bin 23) / `_164132` (sweep); **post-fix (2026-07-25)** at `20260725_212615` (live_test1) /
`_212829` (natural, warmup→bin 27) / `_213047` (paced16) / `_213305` (sweep) / `_213635`
(natural forced to bin 23 for the per-window pre/post comparison).
`data/raw/`, `data/processed/`, `experiments/` and `figures/` are **empty or absent**;
`data/manifest.local.csv` is header-only (0 sessions).

### Best current results — PRELIMINARY, not citable
HR, scored strictly under `notes/comparator_prespec.md` at the corrected bin — **unchanged by the
M2 fix** (HR outputs verified bitwise identical at fixed bins):

| session | AHET-accepted | scorable | MAE | severe (>5 bpm) |
|---|---|---|---|---|
| natural | 5/50 | 1 | 0.19 bpm | 0 |
| paced16 | 23/50 | 21 | 0.50 bpm | 0 |
| sweep | 30/150 | 19 | 0.53 bpm | 0 |

**Why not citable:** n=1 subject; exploratory, not held-out; `natural` rests on a single scorable
window; coverage is 10–46%. **Accuracy is not the problem — coverage is.** Always report both.
BR is unpinned but **unscored** — waits on the M3 freeze.

### New finding — warmup bin selection moved (improvement; protocol decision open)
Warmup scoring consumes BR/HR, and pre-fix it was being fed floor-pins (the old natural warmup
record shows bins 24–29 all `br=6.0`/valid). Post-fix, natural's warmup locks **bin 27 at HIGH
confidence with a warmup-verified HR of 64.5 bpm** (was bin 23, medium, no valid HR). At bin 27:
HR-valid 9/51 vs 5/51 at bin 23. **Whether the live protocol adopts this is a user decision** —
nothing was changed in the warmup code.

### Known broken / open
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
  Gram–Schmidt replacing `np.linalg.qr`) has **not** been done. It is a **prerequisite of M4** —
  M4 produces every paper-grade number and executes that path.
- **Genuine ≈6 bpm breathing at the edge bin is permanently `resp_valid=False`** — a deliberate,
  declared coverage sacrifice mandated by the M2 invariant (implementation_plan M2 done-when #2).

---

## 3. Active task / next steps — **CLOSE the M3 cross-review (not start it)**

**M3 blocks the most:** the M0 deposit needs the frozen BR comparator, and M2's last done-when
item (#5) is BR scored under it.

1. **M3 close-out** — the review of `notes/comparator_prespec_br.md` + `notes/analysis_prespec.md`
   already ran (2026-07-25, 9 rounds, 28/28 findings resolved incl. the user's §2b evidence-floor
   decision in round 8). Remaining: Codex's final confirmation + `NO MORE COMMENTS`, inbox tidy-up,
   status-header updates. **Resume prompt: `plans/m3_claude_review_loop_prompt.md`** (Codex side:
   `plans/m3_codex_review_prompt.md`). Note the comparator's own header still says "awaiting
   cross-review" — stale; the coordination file's debate log is authoritative.
2. **Score reprocessed BR under the frozen comparator** (closes M2). The post-fix NPZs are ready;
   the reference-only evidence script is `scripts/derive_br_comparator_evidence.py`.
3. **M0** — the pre-registration freeze and deposit (hard gate before any study capture). Its one
   blocking decision is the **evidence floor** (deferred by the user 2026-07-24 — see
   `plans/m0_b1_evidence_floor_memo.md`; a floor chosen after seeing the pilot yield is not a
   floor). Draft deposit material: `plans/m0_preregistration.md`. Ethics number/board are now
   recorded (§2).
4. **M1** — live hardware smoke test (unscored, no dependencies, highest risk-reduction per
   minute; run the **full 10 min** so it doubles as the first long-session bin-lock check).
   User decision folded in: whether natural-style sessions adopt warmup bin 27 (§2).
5. **M8 step 1a/1b** — the HA reproduction control, then the adaptation transfer test.

Also start early: the **linalg-free DSP cross-model review** (gates M4, hence M5/M8/M9/M10).

---

## 4. Decisions that matter (do not silently reverse)

- **Breathing rate is a co-equal goal**, not a by-product (2026-07-24).
- **The M2 validity invariant (2026-07-25): a selection on the first in-band FFT bin can never be
  `resp_valid=True`** — enforced by bin identity inside the shared `fuse_estimates`, not by
  frequency arithmetic (which breaks on non-bin-aligned edges).
- **The 7 unresolved edge windows are "spectrally unresolved", never classified by STFT/reference
  agreement** (that would be tuning to the reference, CLAUDE.md §4).
- **STFT stability may support a fusion branch only if its median matches the selected value**
  (`stft_match_bpm` = 6.0, one 10 s-subwindow FFT bin; `stft_min_valid_fraction` = 0.5) — new keys
  with resolution-derived defaults; **no pre-existing threshold changed** (2026-07-25,
  cross-reviewed).
- **Step-5's offline `edge_lock_margin_bpm` post-fusion gate is kept unchanged on both edges**;
  only the first-bin veto is guaranteed identical live/offline — expected divergence at the
  offline-only margins is asserted in tests, not papered over.
- **Recordings are 10 minutes** (2026-07-24, decided *before* the deposit). **The approval
  ceiling is 10 min — do not exceed it.** Do not shorten either.
- **The pre-registration claim is "frozen before the confirmatory data", NOT "before any data
  existed."** Overstating it repeats the error that forced the "MAE 0.16 bpm" withdrawal.
- **Three capture classes, and the distinction is load-bearing:** *pre-freeze exploratory* (the 4
  existing captures + M1's smoke test), *post-freeze exploratory* (**M5**, the pilot), and
  *confirmatory* (**M6** onward).
- **The BR comparator is designed from the reference alone.** Using radar agreement to sanity-check
  reference eligibility is the mirror image of the tuning CLAUDE.md §4 forbids.
- **BR reference = Masimo `rr_bpm`, cross-checked against the paced metronome rate.** Masimo RRp is
  pleth-derived, smoothed and laggy — a weaker reference than PR. Say so.
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
- **Distance is descriptive, not inferential.**
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
- **Pre-fix replay NPZs (before 2026-07-25) have no `resp_valid` field** — infer validity from
  `f_r_hz` finiteness (the live path only sets it for valid respiration). Post-fix NPZs carry the
  full M2 evidence schema (`resp_valid`, `resp_edge_veto` + reason, `resp_fusion_branch`,
  selected/argmax bins, per-candidate HA arrays, STFT std/valid-fraction).
- **Warmup bin selection consumes BR/HR, so estimator changes can move the lock.** The M2 fix
  moved natural's auto-lock from bin 23 to bin 27 (see §2). Any future DSP change must re-check
  warmup decisions, not just per-window outputs.
- **`_save_intermediates` silently drops any record key that fails to stack** (`except: pass`) —
  the schema test in `tests/test_respiration_m2_fix.py` guards the M2 fields; extend it when
  adding fields.
- **Tracked vs untracked:** `notes/protocol.md`, `notes/comparator_prespec.md`, `notes/approach.md`,
  `plans/implementation_plan.md`, the writing files and the M0/M2/M3 plan + comparator docs are
  **tracked** (committed 2026-07-25). `results/` and `data/` stay gitignored by design.
- **The 10-minute duration is enforced by operator discipline only.**
  `scripts/live_demo_config.yaml` has `max_live_duration_s: null` (run until Ctrl-C).
- **A longer session stresses the bin lock.** The warmup locks **one** range bin for the whole
  recording — check the lock at minute 9–10 with `scripts/diagnose_live_run.py`.
- **`elapsed_s` in replay NPZs is WALL-CLOCK**, ~0 under `--replay-fast`. Use
  `frame_idx / frame_rate_hz` for any replay timebase. Still unfixed in `live_demo.py`.
- **`fallback_hr_bpm` is a LIAR** — a naive argmax, not the verified estimate. Use `hr_bpm_raw` /
  `candidate_refined_hz[0]`.
- **The live display holds the last valid HR** — always run `scripts/diagnose_live_run.py` before
  believing a live run.
- **M7's rate must NOT be computed from resting HR** — set it from HR measured *while paced*.
- **A synthetic test asserting "candidate tracks harmonic order k" must first confirm k is
  admissible at that f_r** (`k*f_r` must land in 0.8–2.0 Hz).
- **Masimo: `Beats / min` (PR) for heart, `Breaths / min` (RR) for respiration — never SpO2/PI/
  PVi for rate.** Align on the integer `Timestamp` column, never the `Date`/`Time` strings.
- **I/Q ordering depends on capture source**: SDK/Python captures need `iq_swap=True`; mmWave
  Studio captures `iq_swap=False`.
- **Respiratory-harmonic coincidence** (`notes/approach.md` §4.2): when `k·f_r ≈ HR`, ECA can
  cancel the cardiac signal with the harmonic. Identifiability problem — longer windows do not help.
- **`experiments/` and `figures/` do not exist.** Restore with `git checkout figures/.gitkeep` if
  rebuilding.

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
  Fig. 5 validates on generic moving-target Dopplers, **not HR/BR**. **M9 requires a
  paper-faithful 1×20 / `N_c`=16 reproduction, then a registered 4-RX ablation before any existing
  capture is touched.**

---

## 7. Pointers

| What | Where |
|---|---|
| Project rules (read first) | `CLAUDE.md` |
| **Whole-project milestone plan** | `plans/implementation_plan.md` |
| Thesis chapter source | `THIRD_CHAPTER.md` |
| Journal paper planning | `JOURNAL_PAPER.md` |
| Method, literature, algorithm spec | `notes/approach.md` |
| **Pre-registered HR comparator (binding)** | `notes/comparator_prespec.md` |
| **BR comparator (M3 — review at final gate, 28/28 resolved, not frozen)** | `notes/comparator_prespec_br.md` |
| M3 close-out prompt (Claude side) | `plans/m3_claude_review_loop_prompt.md` |
| BR-comparator design evidence (reference-only) | `scripts/derive_br_comparator_evidence.py` |
| Frozen analysis pre-spec (scoring grid §7) | `notes/analysis_prespec.md` |
| Capture inventory (hashes) | `notes/capture_inventory.md` |
| Capture protocol / SOP (10-min; ethics `24IBEC051`, IBEC KAUST) | `notes/protocol.md` |
| **M2 fix plan (as reviewed)** | `plans/m2_respiration_fix.md` |
| **M2 cross-review record (closed 2026-07-25)** | `plans/m2_fix_cross_review.md` |
| M2 floor-pin diagnostic (regenerates root-cause counts) | `scripts/diagnose_respiration_collapse.py` |
| M2 fix tests (25, precondition-asserting) | `tests/test_respiration_m2_fix.py` |
| M0 pre-registration draft + evidence-floor memo | `plans/m0_preregistration.md`, `plans/m0_b1_evidence_floor_memo.md` |
| M3 review prompts | `plans/m3_codex_review_prompt.md`, `plans/m3_prespec_cross_review.md` |
| ECA staged plan (v1/v2 dead) | `notes/plan_eca_forbidden_zone.md` |
| Stage 1B lag-10 design (draft 7) | `notes/note_stage1b_lag_statistic.md` |
| Candidate-ranking bug writeup (no plan yet) | `notes/note_candidate_ranking.md` |
| Reference papers (simulation-only) | `literature/ref_papers/` |
| Live/replay demo **and capture tool** | `scripts/live_demo.py` |
| Live demo config (incl. new `stft_match_bpm` keys) | `scripts/live_demo_config.yaml` |
| Standalone headless capture | `steps/step_1/capture.py` |
| Warmup bin-lock + eligibility logic | `scripts/live_demo.py:_run_warmup_selection` |
| Bin-lock validation (tracked, regenerable) | `scripts/validate_warmup_selection.py` |
| Run post-mortem ("why was HR blank?") | `scripts/diagnose_live_run.py` |
| Core DSP (ECA + AHET) | `src/vitals.py` |
| Breathing-rate DSP (HA + M2 peak-validity fix) | `src/respiration.py` |
| Radar I/O (4-RX cube, `iq_swap`) | `src/radar_io.py` |
| Masimo parser (PR, RR, PI) | `src/masimo.py` |
| Agreement metrics | `src/compare.py` |
| Bland–Altman — **plotting only**, statistics invalid for repeated measures | `scripts/plot_bland_altman.py` |
| Evidence run (Masimo, 180 s) | `results/live_demo/20260713_172042_live_demo_massimo1/` |
| Stepped-rate sweep (Masimo, 486 s) | `results/live_demo/20260714_180523_live_demo_sweep/` |
| Pre-fix replay evidence (bin-corrected) | `results/live_demo/20260715_164018` (paced16), `_164124` (natural), `_164132` (sweep) `_replay_unknown/` |
| **Post-fix replays (2026-07-25, full M2 evidence schema)** | `results/live_demo/20260725_212615` (live_test1), `_212829` (natural@27), `_213047` (paced16), `_213305` (sweep), `_213635` (natural@23) `_replay_unknown/` |
| Full project history | `HISTORY.md` |

### Run artifacts written per live run
`results/live_demo/<ts>_<mode>_<session>/` contains `run_metadata.json` (config, git commit,
packet stats), `warmup_bin_selection.json` (per-candidate bin-lock evidence incl.
`settled_energy_db` / `energy_eligible` / `hr_bonus_vetoed`), `live_estimates.csv` (per-hop HR/BR
+ rejection reasons), `live_intermediates.npz` (phase + spectra + AHET candidates + M2 respiration
decision evidence, checkpointed every 60 s — `elapsed_s` inside is wall-clock, see §5) and
`adc_stream.bin` (raw mirror).
