# Handoff — resume here

> Read this and `CLAUDE.md` before doing anything. **State verified 2026-08-08.**
> `HISTORY.md` is the append-only log; this file is the always-current summary. Where they
> disagree, this file is wrong and should be fixed.

**M8 Ahmed documentation update (2026-08-08):** canonical two-lock × seven-arm execution and
scoring are complete and documented in `reports/m8_ahmed_correction_final_report.md`. Figure 8
remains not reproduced under declared assumptions; synthetic controls passed. Real metrics are
approximate-origin, protocol-stratified, k>=1 descriptive rows only; do not pool locks or rank
arms. Optional all-bin diagnostic was not authorized and was not run.

## 1. Project snapshot

Estimate heart rate and breathing rate from a TI IWR1642BOOST + DCA1000 FMCW radar while a subject
sits 0.8–1.4 m away, and quantify agreement against a Masimo MightySat fingertip pulse oximeter.
HR truth is the Masimo `Beats / min`; BR reference is `Breaths / min`; both align on the integer
Unix-epoch `Timestamp`. Output: a journal paper plus a thesis chapter.

**Production method:** per 30 s window at a warmup-locked range bin — phase extraction
(`delta_before_mean`) → impulse-noise clip → for **BR** a fused FFT/HA/STFT estimate over
0.10–0.50 Hz; for **HR**, ECA (project out respiration harmonics) → argmax in 0.8–2.0 Hz → AHET
second-harmonic verification, which returns **NaN rather than a guess** when it fails.

Two things the one-liner hides, both of which have caused real mistakes:

- **There is no static clutter removal.** `phase.clutter_removal` defaults to `"none"` and the
  call is an identity at that setting. It was measured and rejected — see §6.
- **Warmup is not a filter that picks a bin.** It runs the *entire* downstream chain once per
  candidate bin (14 of them) and scores the results. So any DSP change can move the bin lock.

## 2. Where the project is

**Infrastructure is strong; the science is thin, and what exists is exploratory.**

Active branch **`vital_signs_ahmed_v11`**. M8's scientific source was frozen at `3058fe1`, the
runner repair at `df51a95`, and the replacement real-evaluation authorization at `e182288`.
The canonical M5 execution and documentation are complete; verify the current tip and tree with
`git log -1 --oneline` and `git status --short` before starting new work. The final report is
`reports/m8_ahmed_correction_final_report.md`.

| Track | Milestone | Status |
|---|---|---|
| C | **M8 — Ahmed harmonic accumulation** | **CORRECTED AND VALIDATED 2026-08-08** — Figure 8 not reproduced; canonical two-lock/seven-arm evaluation complete; claim-ineligible exploratory scoring in final report |
| C | **M9 — Kotte joint-Doppler** | **Steps 0-3 DONE; controls PASSED; step-4 checkpoint BLOCKED on a user decision** (the oracle finding) — §4.1 |
| C | M10 baselines | not started |
| A | M1 live smoke test | not run |
| A | M2 respiration fix | landed; only done-when #5 open |
| A | M3 BR comparator spec | closed, 48/48 findings |
| A | M4 offline harness | built (`scripts/score_offline.py`), has run for real |
| B | M5 pilot / M6 study | not started; no longer blocked by anything |
| 0 | ~~M0 governance milestone~~ | **REMOVED 2026-08-03 by user decision** — §3 |
| D | M12 paper / figures / chapter | not started; headline reworded — §3 |

### 2.1 The dataset — 8 captures, FOUR subjects

Corrected 2026-08-04; the record previously said one subject, which was wrong. Authoritative
map in `notes/capture_inventory.md` — **subject identity is not machine-recorded anywhere** and
cannot be recovered from the artifacts.

| Subject | Captures |
|---|---|
| A | `massimo1`, `massimo2` |
| B | `massimo3`, `sweep` |
| C | `massimo4`, `massimo5` |
| D | `massimo6`, `massimo7` |

All exploratory; all informed the method's design. Four subjects is still small and
non-randomly sampled. **None carries a persisted `frame0_epoch`** — any reference alignment is
approximate (from `start_wall_utc`), which is why M9 scoring needs the §4.1 amendment.

### 2.2 What the data can and cannot support (measured 2026-07-31)

`scripts/diagnose_signal_presence.py`, evidence `results/diagnose/signal_presence/20260731T155946Z/`.
Everything there is a **feasibility ceiling, not a result** — the audit used the reference to decide
where to look and switched every gate off. Never quote it as accuracy or coverage, and **derive no
threshold, band edge or bin choice from it**.

- **The reference is not the weak link.** 100% of Masimo rows have PI ≥ 0.5 in all 8 captures,
  physiological PR/RR medians, full temporal overlap.
- **BR is extractable.** True respiration frequency beats random decoys in 7 of 8 captures;
  locked-bin BR SNR +8.2 to +17.3 dB in 7 of 8. **Tracking demonstrated in `sweep`** (permutation
  `p=.001`, Spearman +0.56 locked / +0.90 best bin) — the only capture whose protocol deliberately
  varies BR.
- **HR is neither demonstrated nor demonstrable here.** No tracking evidence anywhere
  (permutation `p=.30–1.00`); at the locked bin the heart-band argmax is **worse than a constant
  predictor in all 8**. And structurally: within-session PR spread is **2.6–5.2 bpm, narrower than
  the ±5 bpm tolerance**, so a predictor emitting the session median scores 83–100%. **These
  sessions cannot distinguish a working HR estimator from a stub returning 85 bpm.**

**Consequence: do not write an HR acceptance criterion against these captures.** It would be
unfalsifiable. BR work can proceed. This binds M9: HR is reported descriptively next to
`constant_session_median`, never gated.

### 2.3 Where the HR coverage bottleneck actually is (measured 2026-07-31)

HR coverage is 12% pooled. `scripts/diagnose_bin_sweep.py` scored every candidate bin on every
window (840 cells over the three 0%-coverage captures):

- **It is NOT bin selection.** Energy-eligible (plausibly-chest) bins yield **0.8%**; the only bins
  with any yield sit 20–34 dB down, exactly the ones the eligibility gate distrusts.
- **The binding constraint is AHET's second-harmonic check** — median ratio **−2.23 dB** against a
  **+1.0 dB** gate; only 14.1% of eligible cells reach it. The harmonic mostly isn't there.
- **27.6% of cells never attempt AHET at all** (`spectrum_stage == 0`, the
  `f_r_hz is None or f_r_is_outlier` no-ECA path, `src/vitals.py:523`).
- Warmup *is* locking onto the subject — locked-bin BR validity 16–17/20 — confirmed independently
  by breathing.

## 3. M0 removed — the standing vocabulary rule, and the headline

**The M0 governance milestone was REMOVED 2026-08-03 by user decision**, along with its hard gate
on study captures; what it would have contained is recorded in `HISTORY.md` 2026-08-03.
M5/M6/M7 are no longer blocked by governance.

**What this project claims about its own specs is a TRANSPARENCY claim, never a TIMING claim:**
the comparator and analysis specifications are written down in full and applied identically to
every estimator compared. Nothing asserts that any of them predates the data, and all agreement
results are exploratory/descriptive. **Never reintroduce** the banned strings — "pre-registered",
"pre-specified", "frozen before data", "registered \<date\>", "deposited", "predeclared" or
"confirmatory" — about this study's own specs or results, in any file, manuscript, title or talk.
Full rule: `HISTORY.md` 2026-08-03 and CLAUDE.md §4. Say "written down in the committed script and
applied identically to every estimator compared". **The purge is not complete.** The last known
leftover of the original strings ("registered in" inside `notes/analysis_prespec.md`'s
approximate-origin paragraph) was fixed by Amendment M9-1 (`b39888f`, 2026-08-06), but
"predeclared" was added to the banned list only afterwards and has never been swept: verified
2026-08-08 that it still describes this study's own specs at `notes/approach.md:318` and `:578`
(that file carries no VOID banner), at `notes/analysis_prespec.md:451` (under that file's own
banner), and at 12 sites across six `plans/` documents. `HISTORY.md` uses it too and is
append-only, so those stay. `notes/` and `plans/` were deliberately left alone — the owner scoped
the 2026-08-08 pass to this file. **If you sweep again, classify every match by hand — `grep -v`
filters hid real hits three times on 2026-08-04.**

**"Primary" is the project's word for the M6/M7 data role** (renamed away from the banned
"confirmatory" label, 2026-08-04, 41 sites). Watch the collision: `analysis_prespec.md` uses
"primary" in three senses (endpoint, CI recipe, data role) — always attach a disambiguating noun.

**Four decisions from 2026-08-04 that outlive the vocabulary work — do not unknowingly reverse:**

1. **The evidence floor is BINDING AS ENGINEERING** (`analysis_prespec.md` §2a/§2b): per-session
   ≥ 1, per-subject ≥ 4, study-wide ≥ 8/10, LoA CI half-width ≤ 5 bpm, natural-drop miss rule.
2. **`notes/comparator_prespec_br.md` is BINDING**; `src/comparator.py:br_reference` implements it.
3. **`notes/ethics_amendment_hr_recovery.md` is a RECORD AS SUBMITTED — do not edit the body.**
4. **The 2-vs-3-session contradiction** between `protocol.md` and `analysis_prespec.md` must be
   resolved **before any M5 pilot session** (needs §6 cross-review). It does not block E/F/G.

**Survived M0's removal because they were never governance:** synthetic-control-first,
prospective-only changes, the pilot's exclusion from M6 metrics, CLAUDE.md §6 cross-review. The
spec files under `notes/` continue as internal engineering specs — `src/m4/window_grid.py`
hard-errors against `analysis_prespec.md` §7. Do not delete them.

**The paper's headline — REWORDED by user decision 2026-08-05:** *first real-data
**evaluation** of two simulation-only published methods — Ahmed harmonic accumulation [R1] and
Kotte joint high-amplitude-difference Doppler [R2] — under one common comparator, with coverage
reported.* "Validation" was dropped because the authorized scoring on the existing captures is
approximate-origin, non-promotable, and barred from final agreement claims; "validation" is
reserved for future exact-origin data. `JOURNAL_PAPER.md` / `THIRD_CHAPTER.md` still carry the
old wording — **rewording them is a pending task at M9 session close.** The headline is IN
PROGRESS; the old (pre-2026-08-03) headline cannot be reinstated.

## 4. ACTIVE WORK

### 4.1 M9 Kotte — the active task. Plan complete; implement it.

**The single authority is `plans/m9_kotte_plan.md`.** Read it in full before writing any code —
it is the product of nine verified cross-review passes (dispositions in
`plans/m9_comments_plan.md`; every comment was checked against the paper extraction, its page
renders, or the repo code before being applied; one was rebutted with evidence). Do not
re-derive design decisions the review already settled — the disposition file records what was
overturned and why.

**What M9 is:** first real-data evaluation of Kotte et al., "Joint Estimation of Single
Target's High Amplitude Difference Doppler Frequencies in FMCW Radar" (IEEE T-RS vol. 2, 2024,
DOI 10.1109/TRS.2024.3352189) — a Capon-like joint two-frequency estimator on the complex
slow-time × RX matrix at one range bin (not on extracted phase; M9 is the harness's first
non-phase consumer). Ladder: control 1 (paper-faithful 20-RX reproduction, direct `Y_t`,
synthetic) → control 2 (4-RX ablation, fixed endpoints) → transfer gate (synthetic vitals cube,
P1–P6 written down in the plan, frozen bundle) → radar-only all-bins sweep (3 arms) → production
comparator regeneration → scoring + Stage-B decision. Stage B (DOA) is out of scope beyond a
go/no-go note.

**The five user decisions (2026-08-05) — recorded, do not re-ask:**
1. Step 1a reproduces Figs 5+7+8 with FFT+MUSIC; Fig 9 Monte Carlo and Yule-AR are descoped.
2. `notes/analysis_prespec.md` gets a **prospective amendment** (with §6 cross-review, committed
   before any M9 scoring run) extending the approximate-origin `exploratory_non_frozen`
   treatment to M9 — the existing exception (:559-565) names M8 only. All scored M9 outputs
   carry the no-promotion/no-final-claim taint. Radar-only work does not wait on it; the scorer
   refuses to run without it.
3. Stage-B MAE is **subject-weighted**.
4. Exact formula: **natural-only captures, pooled within subject, subjects averaged equally**;
   floors ≥3 subjects with ≥5 natural paired windows each and ≥30 total; paced/stepped captures
   scored descriptively only; sensitivity variant with low-contribution subjects dropped.
5. Headline reworded to "first real-data **evaluation**" (§3).

**Execution-order status: steps 0-3 DONE, step 4 half done and blocked.**

| Step | State |
|---|---|
| 0 groundwork | **done** `b39888f` — config, `notes/approach.md` §5.8, analysis-spec Amendment M9-1 |
| 1 core + controls build | **done** `3c0efc7`; SNR amendment `2fec07e` |
| 1 official R1 verdict | **done — `behaviorally_reproduced`. THE MILESTONE GATE IS PASSED.** |
| 1 R2 / R3 / audits | **done** — R2 3/3 exact, R3 resolves Δ=0.5 Hz, audits recorded |
| 2 4-RX ablation | **done — 24/24 rows pass**, rank 4 at 4 RX, phase invariance 3.9e-12 |
| 3 aggregation contract | **done** `f2e74c5` — forms, medoid, partial-CPI, suite, 20 tests |
| 4 oracle | **done** `9078ed8` — and it found a blocker (below) |
| 4 checkpoint → gate → bundle | **BLOCKED on a user decision** |
| 5-8 sweep / comparator / scorer | not started |

**Control evidence lives in gitignored `results/`, so the run IDs are recorded here:**
R1 `20260806T154753.144349Z_6bde60353be2`; R2/R3/audits
`20260806T154841.509470Z_6bde60353be2`; ablation `20260806T155022.295892Z_6bde60353be2`
— all clean-tree at commit `2fec07e`, `smoke: false`. Oracle:
`results/m9/step1b/oracle/20260806T160339.308982Z/`. Anything under `*_smoke/` is
non-gating scratch and can never be evidence.

**BLOCKED ON THE USER: choose option A, B, or C in
`plans/m9_step1b_oracle_finding.md`.** The oracle (which the plan requires to run
*before* `kotte_gate.py` exists) found that a real sinusoidal chest displacement produces
**conjugate sideband pairs** — `J_{-1} = −J_{+1}`, so `±f_b` and `±f_h` are all present
even in the small-modulation limit. The signal carries **≥ 4 lines where Kotte's
estimator constrains 2**: a model-order **misspecification**, not a noise problem.
Isolated against a two-cisoid control at identical aperture/SNR/gains — two cisoids
recover 9/9 at N_c=16/30 dB; **conjugate pairs recover 0/9 at N_c=16 AND N_c=32 at 10,
30 and 60 dB.** Fifty extra dB fixes nothing; only aperture does, near N_c=64 (3.2 s),
and even there BR is biased and the margin is < 0.02 dB. This predicts **P3 fails at the
primary arm** and quantifies the plan's risk #7. Recommendation: **B** — add a declared
`N_c=64` arm (config amendment + §6 cross-review) *before* freezing, keeping N_c=16 as
primary, so the gate tests the regime where the method can work and the paper gains a
two-point aperture curve instead of a bare null. `transfer.gate_criteria` stays `null`
until the checkpoint happens.

**After that decision, in order:** fill `gate_criteria` from the oracle → commit the
checkpoint → build `src/m9/kotte_gate.py` + `scripts/m9_kotte_transfer.py` +
`tests/test_m9_kotte_gate.py` → transfer gate → bundle (publication requires
`gate_status == "passed"`) → steps 5-8 (sweep, production comparator, scorer, Stage-B).

**Three §6 cross-reviews are outstanding** and all need the other model family:
1. analysis-spec **Amendment M9-1** (`m9_amendment_cross_review: pending`) — **the scorer
   refuses to run until this reads `completed`**, so it blocks step 8;
2. the **step-1a SNR amendment** (`m9_snr_amendment_cross_review: pending`) — the
   controls already ran against it; a verdict is not settled paper evidence until this
   passes;
3. the **oracle finding** (`m9_oracle_finding_cross_review: pending`).

**Non-negotiables while implementing (details and rationale in the plan):**
- **No M8 file is edited** — the frozen M8 gate bundle and its source-text-pinned tests must
  stay valid. M9 is `src/m9/` + sibling scripts (13 Python files + 1 config).
- **Evidence runs require a clean tree**; dirty-tree execution only via `--smoke` into
  segregated `*_smoke/` outputs that can never gate, score, or decide.
- **`gate_status == "passed"` is required to publish the transfer bundle** and is verified by
  the sweep and scorer (`publish_latest` alone would accept a failed gate — verified).
- **The sweep opens no Masimo file and derives no locks.** The production comparator + the sole
  `current_production_rerun_lock` map come from one command
  (`scripts/m9_production_comparator.py`) on a clean commit — the old
  `results/diagnose/bin_sweep/20260804T131040Z` run is dirty-tree (verified) and ineligible.
- **The scorer is radar-free**, refuses to run without the committed amendment, and stamps
  every scored artifact with the approximate-origin taint.
- **HR is never gated** (§2.2); Stage-B is BR-only, natural-only, subject-weighted, primary arm
  only.

### 4.2 Waiting on the user: three new captures (E, F, G)

The user is collecting three new captures from three new people. They are the **only untouched
BR bin-selection holdout** (`massimo4`–`massimo7` are spent — §7) and the first data able to
discharge M2 done-when #5. Requirements (`notes/protocol.md`): natural breathing (not the
recovery arm), Masimo with the step 3a clock sync, scene behind the chair recorded, settle
criterion enforced. With the current build they carry a true `frame0_epoch` (§4.4).

### 4.3 The BR bin rule — FROZEN; score it once when E/F/G land

**The learned-feature arm is a closed NO-GO** (2026-08-04): out-of-fold it lost to a
zero-parameter rule (2.384 vs 2.291 MAE), the winning sign vector was unstable across all 4
LOSO folds, and a permutation null showed the 834-vector search reaches 2.665 on shuffled
errors for free. Do not re-open by adding features — the constraint is n=4 subjects. Full
numbers: `HISTORY.md` 2026-08-04; artifacts `results/diagnose/br_bin_preflight/20260804T192908Z/`.

**What won: `medoid_consensus_always_emit` v1, frozen 2026-08-04** — among `br_valid` bins,
report the one whose `br_bpm` is closest to the window median over valid bins; tie-break higher
energy; always emit. Zero fitted parameters. Pooled over the 105 admissible `k>=1` windows:
coverage 1.00 / MAE 2.291 / hit±3 0.733, vs production 0.867 / 2.679 / 0.670; paired per-subject
delta +0.473 bpm (SE 0.266), better in 3 of 4 subjects. Frozen artifact:
`results/diagnose/br_bin_rule/20260804T204634Z_freeze/frozen_rule.json` (SHA-256
`cda0b352…174f81af`); predictions with 95% PIs and the success criteria (paired delta > 0;
failure if ≤ 0 or worse in ≥2 of 3 subjects; **no absolute MAE threshold**) are recorded at the
freeze. Label-origin sensitivity clean (offset grid {0, +7.5, +15} s moves rule MAE ≤ 0.046).

**One-touch scoring when the captures exist** (needs `diagnose_bin_sweep` +
`diagnose_signal_presence` runs on them first):

```
python -X utf8 scripts/br_bin_rule.py --mode test \
  --frozen-rule results/diagnose/br_bin_rule/20260804T204634Z_freeze/frozen_rule.json \
  --i-have-frozen-the-rule --sweep-run <dir> --presence-run <dir> \
  --subject-map <suffix>=E <suffix>=F <suffix>=G
```

### 4.4 Already landed — no action needed, but know it

**Frame 0's true capture epoch is now recorded** for future captures. `LiveFrameSource` stamps
`t_first_packet_utc`, corrects for leading zero-fill; `run_metadata.json` gains
`frame0_epoch_utc`/`frame0_epoch_source`; `score_offline.py::resolve_frame0_epoch()` prefers it
and falls back to `start_wall_utc` for the 8 old captures, which cannot be retrofitted
(hence the M9 amendment). 10 tests in `tests/test_frame0_epoch.py`.

### 4.5 Also available (not active)

- **M1 live smoke test** — hours, no dependencies, also discharges M2 #5 if it carries a synced
  Masimo.
- **M11a coverage** — aimed at §2.3's finding (AHET verification, the no-ECA path). §6
  cross-review applies; §2.2 forbids validating an HR criterion on the existing captures.
- **M10 baselines** — not started.

## 5. M8 Ahmed — corrected canonical evaluation complete

Authority is `plans/m8_ahmed_correction_plan.md`, with the accepted Step 1a/Step 1b plans and
addendum beneath it. The implementation preserves Layer A pulse-radar `q=2f`, `30q bpm` and the
project's Layer B FMCW unwrapped-phase `q=f`, `60q bpm` mapping.

The replacement promotion-eligible synthetic gate is
`results/m8_ahmed_transfer/synthetic/20260808T183335.468320Z_779928f3a61c` (manifest
`3f0467d5…83d6`). The canonical paired radar bundle is
`results/m8_ahmed_transfer/radar/20260808T183600.392760Z_bc3ccf4635c5` (manifest
`51b68e93…5532`): eight captures × two locks × seven arms, with 128 source spans, 256 shared
cells, 1,792 estimator rows, 1,536 Ahmed evidence rows, and 256 production evidence rows.

Canonical scoring is
`results/m8_ahmed_transfer/scored/20260808T191921.669826Z_bc3ccf4635c5` (manifest
`96b81120…835`), with 3,584 unique HR/BR rows. Comparative metrics use `k>=1`; k=0 is diagnostic
only. Natural, paced, and unknown protocol strata and the two lock estimands remain separate.
The complete exact tables are in `reports/m8_ahmed_correction_final_report.md`.

The Figure 8 successor is honestly `not_reproduced_under_declared_assumptions`. Real scoring is
`exploratory_non_frozen`, uses an approximate 5–15 s time-origin uncertainty, and is
`not_eligible_for_promotion_or_final_agreement_claims`. Do not rank arms, pool protocols/locks,
or describe Ahmed's 100% algorithmic validity as measured 100% joint coverage. The optional
14-bin diagnostic was not authorized and was not run. Earlier sweep/scorer bundles remain
historical-only and invalid as canonical evidence.

## 6. Settled questions — do not re-open without new data

- **Static clutter removal** — implemented (`src/clutter.py`), **OFF**, measured not to help
  (warmup free 13%→**7%**, lock moved in 4 of 8 captures). Default path bit-identical, pinned
  by test.
- **The 5-bin relock tracker** — simulated 2026-08-04; `P3_relock` fired 0–1 times across all
  38 combinations — inert. **Do not port the relock half from `git stash@{0}`.** The
  neighbourhood-read half (`P2`) was not adopted either (halved coverage on the holdout).
- **Warmup bin selection is not the HR coverage bottleneck** (§2.3). It still matters for BR
  accuracy — the per-window oracle ceiling is MAE 1.10 vs production 2.68; about a third of
  that headroom is reachable with zero fitted parameters (§4.3); the rest is not identifiable
  at n=4.
- **A learned bin rule does not beat the zero-parameter medoid** — measured 2026-08-04 (§4.3).
  Re-open only with more subjects, via the same go/no-go script.
- **Respiration collapse** — fixed via `resp_edge_veto` + STFT-consistency gates. Do not
  re-open. Accepted cost: a genuine ~6 bpm breather on the edge bin is permanently invalid.
  Only done-when #5 remains open (needs a capture with non-approximate alignment).
- **M8/Ahmed on real data** — corrected canonical comparison is complete (§5). Do not re-run,
  pool, rank, or promote it without new authority and a scientifically stated reason.

## 7. Gotchas that will bite you

- **The BR holdout is partly spent.** `massimo4`–`massimo7` were scored under two operating
  points of the same policy family; no longer a clean holdout for any bin-policy question. The
  §4.2 plan (test on E/F/G) resolves this.
- **The 3 pre-M2-fix captures do not reproduce their recorded warmup evidence.** `massimo1`,
  `massimo2`, `sweep`: their `run_metadata.json` `locked_bin` came from buggy code; massimo2's
  and sweep's live locks (20, 21) are documented mislocks. **Re-derive locks with current code**
  (current: massimo1 27, massimo2 26, sweep 26). In M9 this is institutionalized: the lock map
  comes only from `scripts/m9_production_comparator.py`.
- **`is_locked_bin` in the old sweep CSV is the PRE-M2-FIX recorded lock** — banned as feature
  and baseline (`src/br_features.py:BANNED_COLUMNS`).
- **Any DSP change can move the bin lock.** Run `scripts/validate_warmup_selection.py` after
  any change.
- **The old production bin-sweep run `20260804T131040Z` is dirty-tree** (`git_tree_clean:
  false`, no config hash — verified 2026-08-05). Usable as a feature-study input as before, but
  **ineligible as the M9 scoring comparator** — M9 regenerates its own.
- **`src/m4/estimator_scoring.paired_partitions` silently overwrites duplicate keys** and takes
  admission from the production row while erroring from each row's own reference. Never call it
  without the M9 wrapper's pre-assertions (one row per key per side; per-pair reference
  identity).
- **`BundleWriter.publish_latest` does NOT check `gate_status`** — it would publish a completed,
  promotion-eligible, *failed* gate. Any new bundle CLI must enforce `gate_status == "passed"`
  itself (the M9 plan does).
- **The BR admissibility gate drops 15 of 120 `k>=1` windows, all by stationarity** — not
  uniform across captures (massimo7 −5 … massimo1/2/sweep −0); always report it.
- **`oracle SNR` and AHET's `ratio_db` are not comparable** — different spectra and floors.
- **Line endings are pinned to LF and it is load-bearing.** Any script hashing a text payload
  must write binary or `newline="\n"`.
- **Canonical Step 1a bundle: 2 payload hashes are CRLF-era.** Documented erratum. **Do not
  "fix" the digests** — that falsifies a provenance record; explicitly rejected.
- **Never parse `git status --porcelain` from a stripped string** — use
  `src/m8/ahmed_provenance.py::git_status_paths`. Pin git state in tests asserting on
  promotion/cleanliness (`_pin_git_provenance`). Git's index caches on `(size, mtime)` — change
  fixture lengths.
- **Session types are not machine-recorded.** `notes/capture_inventory.md`: massimo1 natural,
  massimo2 **paced 16**, sweep **stepped 12→15→18→21**; massimo3–7 user-declared natural
  (verify against the Masimo RRp channel — massimo2 reads a flat 16.0). This feeds M9's capture
  manifest **protocol roles**, which the natural-only Stage-B rule depends on.
  `score_offline.py` requires explicit `--session-type` (OSR-19).
- **Nothing checks a new capture** for packet loss, frame misalignment, ADC clipping or
  mirrored I/Q — that tooling was removed 2026-07-31 on user instruction. `live_demo.py` writes
  `live_raw_mirror_hash: null`, so `score_offline.py`'s directory-form `--pinned-lock-source`
  refuses future live captures; use the integer form (`kind="manual"`).
- **6 of 8 captures have no raw hash in their metadata.** massimo1/2/sweep have 2026-07-25
  hashes in `notes/capture_inventory.md`; massimo3–7's are in `HISTORY.md` 2026-07-31 only. M9
  hashes every `adc_stream.bin` per run; the live mirrors are referenced **in place** as
  noncanonical fixtures — never edited, never promoted into `data/raw/`.
- `results/live_demo/` holds exactly the 8 canonical captures; massimo7 has duplicate/missing
  Masimo seconds — use the parser's integer-`Timestamp` dedup.
- Do not mutate `data/raw/`, `results/live_demo/`, live estimates, metadata, warmup evidence or
  Masimo CSVs.
- **Environment:** plain `conda` is not on PATH. Use
  `& 'C:\ProgramData\anaconda3\condabin\conda.bat' run -n radar-vitals python …`, and never call
  the env's `python.exe` by absolute path (it crashes matplotlib `savefig`).

## 8. Pointers

| Purpose | Path |
|---|---|
| Project rules | `CLAUDE.md` |
| Append-only log | `HISTORY.md` |
| Milestone roadmap | `plans/implementation_plan.md` |
| **M9 plan — THE authority for the active task** | `plans/m9_kotte_plan.md` |
| **M9 review dispositions (9 passes, 1 rebuttal, 5 user decisions)** | `plans/m9_comments_plan.md` |
| **M9 oracle finding — THE BLOCKING decision memo** | `plans/m9_step1b_oracle_finding.md` |
| M9 step-1a SNR finding (resolved: option B applied) | `plans/m9_step1a_snr_finding.md` |
| M9 built code (steps 0-4) | `experiments/m9_kotte/config.yaml`, `src/m9/`, `figures/reproduce_kotte_controls.py`, `scripts/m9_step1b_gate_prediction.py`, `tests/test_m9_kotte_core.py` |
| **Kotte paper extraction + page renders** | `literature/ref_papers/joint_estimation_high_amplitude_doppler/` |
| Method rationale, ECA+AHET spec | `notes/approach.md` (Kotte: §5.8, written 2026-08-06) |
| Analysis spec (window grid §7, evidence floor §2a/§2b, approx-origin rule :559-565) | `notes/analysis_prespec.md` |
| HR / BR comparator specs | `notes/comparator_prespec.md`, `notes/comparator_prespec_br.md` |
| Capture protocol, ethics, scene requirement | `notes/protocol.md` |
| **Capture inventory + SUBJECT MAP + protocol roles** | `notes/capture_inventory.md` |
| Approved IBEC amendment (recovery arm) — record as submitted | `notes/ethics_amendment_hr_recovery.md` |
| **M8 correction authority and final report** | `plans/m8_ahmed_correction_plan.md`, `reports/m8_ahmed_correction_final_report.md` |
| M8 suite/gate/provenance modules (completed; do not change without invalidating its evidence chain) | `src/m8/`, `src/m4/estimator_runner.py`, `src/m4/estimator_scoring.py`, `src/m4/evidence_serialization.py` |
| M8 canonical runner/scorer | `scripts/m8_ahmed_transfer.py` (`real-smoke`, `real-radar`, `score`) |
| Production DSP | `src/respiration.py`, `src/vitals.py`, `src/window_pipeline.py` |
| Warmup selection + regression check | `src/warmup_select.py`; `scripts/validate_warmup_selection.py` |
| Offline scorer / comparators | `scripts/score_offline.py`, `src/comparator.py` |
| Per-bin sweep / signal presence / bin policy diagnostics | `scripts/diagnose_bin_sweep.py`, `scripts/diagnose_signal_presence.py`, `scripts/simulate_bin_policy.py` |
| FROZEN BR bin rule + one-touch test | `scripts/br_bin_rule.py` → `results/diagnose/br_bin_rule/20260804T204634Z_freeze/` |
| Frozen window grid | `src/m4/window_grid.py` |
| The 8 captures | `results/live_demo/` |
