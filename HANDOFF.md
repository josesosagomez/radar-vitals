# Handoff — resume here

> Read this and `CLAUDE.md` before doing anything. **State verified 2026-08-04 (evening).**
> `HISTORY.md` is the append-only log; this file is the always-current summary. Where they
> disagree, this file is wrong and should be fixed.

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

Active branch **`vital_signs_ahmed_v10`**, HEAD `a9a2416`. Suite **2140 passed, 5 skipped**
(verified 2026-08-04). The 5 skips are honest absences (4 OSR-03 tests need replay artifacts that
no longer exist), not passes.

| Track | Milestone | Status |
|---|---|---|
| C | **M8 — Ahmed harmonic accumulation** | **CURRENT FOCUS.** Step 1a done and negative; Step 1b built through the synthetic gate, **never opened a real capture** — §5 |
| C | **M9 — Kotte joint-Doppler** | **not started; zero code in the repo** |
| C | M10 baselines | not started |
| A | M1 live smoke test | not run |
| A | M2 respiration fix | landed; only done-when #5 open — §4 |
| A | M3 BR comparator spec | closed, 48/48 findings |
| A | M4 offline harness | built (`scripts/score_offline.py`), has run for real |
| B | M5 pilot / M6 study | not started; **no longer blocked by anything** |
| 0 | ~~M0 pre-registration~~ | **REMOVED 2026-08-03 by user decision** — §3 |
| D | M12 paper / figures / chapter | not started |

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
non-randomly sampled.

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
unfalsifiable. BR work can proceed.

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

## 3. M0 removed — and the standing rule that follows

**M0 (pre-registration freeze and deposit) was REMOVED 2026-08-03 by user decision**, along with
its hard gate on study captures. M5/M6/M7 are no longer blocked by governance.

**Nothing in this project is pre-registered.** Agreement results are exploratory/descriptive,
never confirmatory. **Never reintroduce** "pre-registered", "pre-specified", "frozen before data",
"registered \<date\>", "deposited" or "confirmatory" about this study's own specs or results, in
any file, manuscript, title or talk. Full rule: `HISTORY.md` 2026-08-03 ("Pre-registration language
purged") and CLAUDE.md §4. It governs over residual `M0` mentions in the append-only review logs
under `plans/`.

**Purge status, re-swept and largely closed 2026-08-04. One population remains:**

- **`JOURNAL_PAPER.md` / `THIRD_CHAPTER.md` — CLEAN.** Every match is a prohibition or part of the
  void banner. The manuscript purge held; it was code and specs that had been missed.
- **`src/` / `scripts/` / `tests/` — CLEAN.** Zero affirmative uses of any banned term.
- **"confirmatory" — GONE repo-wide.** Renamed to **`primary`** on 2026-08-04 (user decision)
  across spec and code in one change, 41 sites: `analysis_prespec.md` (11),
  `note_stage1b_lag_statistic.md` (12), `src/m4/manifest.py` (6), `tests/test_m4_manifest.py` (5),
  and 7 elsewhere. **`primary` is now the project's word for the M6/M7 data role** — "the primary
  evidence base", "never primary/headline", "excluded from primary metrics". The only surviving
  occurrences of "confirmatory" are the prohibitions themselves.
  > **Watch the collision when you write.** `analysis_prespec.md` uses "primary" in **three**
  > senses now: the primary *endpoint* (HR, vs secondary BR), the primary *CI recipe/estimator*
  > (vs the sensitivity analyses), and this data role. Always attach a disambiguating noun —
  > "primary evidence base", "primary/headline metrics", "primary capture" — never a bare
  > "primary". §2's BR sentence had to be rewritten rather than renamed for exactly this reason.
- **`notes/` — CLEAN as of 2026-08-04.** The deposit / pre-specification prose was re-derived
  against the post-M0 world rather than renamed. Detail: `HISTORY.md` 2026-08-04 ("Deposit prose
  re-derived").
- **The purge is complete.** Every surviving match repo-wide is a prohibition, the append-only
  record (`HISTORY.md`, `plans/`), or the preserved IBEC submission record. **If you sweep again,
  classify every match by hand — `grep -v` filters hid real hits three times today.**

**Four decisions taken 2026-08-04 that outlive the vocabulary work — do not unknowingly reverse:**

1. **The evidence floor is BINDING AS ENGINEERING.** Option A (`analysis_prespec.md` §2a/§2b) kept
   its force when M0 died: per-session ≥ 1, per-subject ≥ 4, study-wide ≥ 8/10, LoA CI half-width
   ≤ 5 bpm, natural-drop miss rule. **A below-floor result narrows the claim and is logged.**
2. **`notes/comparator_prespec_br.md` is BINDING**, no longer "READY FOR THE M0 FREEZE — NOT yet
   frozen". `src/comparator.py:br_reference` implements it; the 2026-08-04 BR pre-flight used its
   §2.2 `admitted` as the primary labelling gate.
3. **`notes/ethics_amendment_hr_recovery.md` is a RECORD AS SUBMITTED.** Its "pre-specified"
   wording is what went to IBEC. **Do not edit the body** — that would falsify a submission record.
   A header says so; the file's diff is 10 insertions, 0 deletions.
4. **The 2-vs-3-session contradiction between `protocol.md` and `analysis_prespec.md` must be
   resolved BEFORE ANY M5 PILOT SESSION** (was "before M0 is deposited"). It does **not** block the
   three new BR captures. It needs CLAUDE.md §6 cross-review — the completed M3 review covered only
   the 2-arm design — and touches the §1 estimand, the §2b per-subject floor denominator and the
   §2b miss rule.

**Corrected 2026-08-04, and it mattered:** `notes/protocol.md` still asserted *"no study capture may
be taken until the pre-registration is deposited"* — a hard gate on a deposit that never existed,
while the user is already collecting E/F/G. **There is no governance gate before study capture.**
What governs capture is ethics `24IBEC051`, the settle criterion, the clock-sync step and the
protocol. Separately, `notes/comparator_prespec.md` had opened with "**PRE-REGISTERED 2026-07-14,
before the next capture**" while `analysis_prespec.md` named it a companion spec carrying the void
banner; the two contradicted each other. Banner replaced, retraction recorded in place.

**Say this instead, because it is true and still strong:** the comparator and analysis specs are
written down in full and applied *identically to every estimator compared*. A **transparency**
contribution, not a **timing** one.

**Survived the removal because they were never governance:** synthetic-control-first,
prospective-only changes, the pilot's exclusion from M6 metrics, CLAUDE.md §6 cross-review.
`notes/analysis_prespec.md`, `comparator_prespec*.md`, `protocol.md` and `capture_inventory.md`
continue as **internal engineering specs** — `src/m4/window_grid.py` cites analysis_prespec §7 as
its authority and hard-errors against it. Do not delete them.

**The paper's headline (user-agreed 2026-08-03):** *first real-data validation of two
simulation-only published methods — Ahmed harmonic accumulation [R1] and Kotte joint
high-amplitude-difference Doppler [R2] — under one common comparator, with coverage reported.*
**It is IN PROGRESS and the old headline cannot be reinstated.** If the methods work is abandoned,
the paper has no headline.

## 4. ACTIVE WORK — what to do next

### 4.1 Waiting on the user: three new captures

The user is collecting **three new captures from three new people** (subjects E, F, G). The agreed
plan for the BR bin-selection study:

- **Discovery/training: all 8 existing captures (subjects A–D)** — done, see §4.2.
- **Test: the 3 new captures, touched once**, with the rule already frozen. **This is the only
  untouched BR bin-selection holdout that exists**; `massimo4`–`massimo7` are spent (§7).

**Requirements for those captures to be usable** (from `notes/protocol.md`):
natural breathing — **not** the recovery arm, whose elevated changing BR is a different regime;
Masimo with the step 3a clock sync; **record the scene behind the chair** (this drifted unrecorded
between the 2026-07-13/14 and 2026-07-28 eras — the later captures have static reflectors
*stronger than the subject*); settle criterion enforced.

Taken with the current build they will also carry a true capture origin (§4.3), making them the
first data able to **discharge M2 done-when #5**.

### 4.2 The BR feature study — BUILT, RUN, and a NO-GO. A zero-parameter rule won instead.

**The learned-feature arm is stopped.** It was built, run and measured on 2026-08-04, and it lost
to a rule with no fitted parameters. Do not resume it by adding features — the binding constraint
is **n=4 subjects**, not the feature set. Full numbers: `HISTORY.md` 2026-08-04
("BR bin-selection pre-flight"). Artifacts: `results/diagnose/br_bin_preflight/20260804T192908Z/`.

**What won — and it is genuinely better than production, on both axes at once.** Pooled over the
105 admissible `k>=1` windows:

| rule | dof | coverage | MAE bpm | hit±3 |
|---|---|---|---|---|
| random valid bin (null) | 0 | 1.00 | 3.370 | 0.520 |
| `P0_static_lock` re-derived — **production today** | 0 | 0.867 | 2.679 | 0.670 |
| **medoid consensus + always-emit** | **0** | **1.00** | **2.291** | **0.733** |
| oracle (uses the reference) | ∞ | 1.00 | 1.103 | 0.914 |

The rule: **among `br_valid` bins, report the one whose `br_bpm` is closest to the window median
over valid bins; tie-break by higher energy; always emit.** Paired per-subject delta against
production **+0.473 bpm, SE 0.266, better in 3 of 4 subjects**. Its appeal is not the MAE — it is
that with zero fitted parameters there is **no selection optimism**, so the measured number is an
estimate rather than an upper bound on one.

**Why the feature search failed:**
- **Out-of-fold it was WORSE than the medoid** — 2.384 vs 2.291 (needed ≥ 0.5 bpm *better*).
- **Not identifiable:** 3 distinct winning sign vectors across 4 leave-one-subject-out folds; fold
  test MAE ranged 0.763–3.186.
- **The permutation null explains the in-sample number.** Searching 834 vectors on *shuffled*
  errors reaches best-in-sample MAE 2.665 on average, against a 3.370 null. Real signal exists
  (observed in-sample 2.056, p=0.000) but does not survive cross-validation.
- Bonus: "always bin 24" beats the searched rule (2.269 in-sample vs 2.384 out-of-fold).

**Do not re-derive these lessons the hard way:**
- **Rank correlation is not selection skill.** `temporal_dev` has the *highest* within-window
  Spearman against error (+0.389) and a clearly *worse* argmax rule (MAE 2.799) than
  `dev_consensus` (ρ +0.351, MAE 2.291). Gate on argmax MAE, never on ρ.
- `HANDOFF` previously listed **"settled energy dB" as an available feature. It is not** —
  `warmup_settled_energy_db` is empty on all 1792 rows of the sweep.
- **Branch-agreement columns (fft/ha/stft) were never built and are now optional.**
  `run_window_dsp` does already return `fft_r`/`ha_r`/`stft_r` (`src/window_pipeline.py:188-190`),
  so it is columns-only in `diagnose_bin_sweep.py` plus a ~7 min re-run — but `br_confidence`
  already discretises branch agreement and was the weakest live feature in the run.

### 4.2a The rule is FROZEN — the only thing left is to score it once

**`medoid_consensus_always_emit` v1, frozen 2026-08-04.** Artifact
`results/diagnose/br_bin_rule/20260804T204634Z_freeze/frozen_rule.json`, SHA-256
`cda0b352…174f81af`. Spec is declarative in `src/br_bin_search.py:BR_BIN_RULE_V1`.

**Predictions recorded at the freeze**, t-based at n=4 subjects — deliberately wide, and the
delta interval spans zero:

| quantity | mean | 95 % PI |
|---|---|---|
| MAE, one new subject | 2.017 | [0.00, 5.66] |
| MAE, mean of three new subjects | 2.017 | [0.00, 4.51] |
| paired delta (P0 − rule), mean of three | 0.473 | [−0.82, +1.77] |

**Success criteria as frozen:** PRIMARY — mean paired delta `MAE(P0) − MAE(rule)` over the new
subjects > 0. FAILURE — mean delta ≤ 0, **or** the rule worse than P0 in ≥ 2 of 3 subjects.
**No absolute MAE threshold** (oracle MAE spans 0.26–1.35 across A–D, so an absolute bar measures
recruitment, not the rule). Coverage is a **structural guarantee**, not a criterion — the rule
emits whenever any bin is valid, P0 only when the locked bin is, so P0's emitting set is a subset;
a violation is a bug and is asserted by test.

**Label-origin sensitivity: clean, and this was the real worry.** Fixed grid {0, +7.5, +15} s run
once after the freeze (`…20260804T204716Z_offset-scan/`): rule ordering identical at all three,
rule MAE moves ≤ 0.046 bpm, paired delta ≤ 0.022. **The unfixable approximate frame-0 origin of the
eight training captures does not threaten this result.**

**To score it — one touch, no re-tuning:**

```
python -X utf8 scripts/br_bin_rule.py --mode test \
  --frozen-rule results/diagnose/br_bin_rule/20260804T204634Z_freeze/frozen_rule.json \
  --i-have-frozen-the-rule --sweep-run <dir> --presence-run <dir> \
  --subject-map <suffix>=E <suffix>=F <suffix>=G
```

The new captures need a `diagnose_bin_sweep` and a `diagnose_signal_presence` run first.
`--mode test` refuses to run without all three flags and the script contains **no search
machinery at all** — the guard `massimo4`–`massimo7` lacked until after they were spent.

### 4.3 Already landed — no action needed, but know it

**Frame 0's true capture epoch is now recorded.** `start_wall_utc` was written before the DCA1000
and IWR1642 are configured, so it preceded frame 0 by 5–15 s — **40% of a 30 s window** at 12 s.
`LiveFrameSource` now stamps `t_first_packet_utc` and corrects backwards for *leading* zero-fill;
`run_metadata.json` gains `frame0_epoch_utc` / `frame0_epoch_source`; `score_offline.py`'s
`resolve_frame0_epoch()` prefers it and falls back to `start_wall_utc` for the 8 old captures,
which cannot be retrofitted. 10 tests in `tests/test_frame0_epoch.py`.

### 4.4 Also available

- **M8 real-data arm** (§5) — the headline. Independent of the BR bin work.
- **M9 Kotte** — zero code. Plan → cross-review → synthetic controls → real data.
- **M1 live smoke test** — hours, no dependencies, also discharges M2 #5 if it carries a synced
  Masimo.
- **M11a coverage**, aimed at what §2.3 found: AHET verification and the no-ECA path. Touches
  heart-band peak-picking → CLAUDE.md §6 cross-review applies, and §2.2 forbids validating an HR
  criterion on the existing captures.

## 5. M8 Step 1b — the in-flight piece of the headline

Authority is the **pair** of files; the addendum wins on conflict.

| File | SHA-256 | Status |
|---|---|---|
| `plans/m8_step1b_ahmed_transfer.md` | `9294cb05…da33ac` | five-discipline PASS on these exact bytes; **unmodified — verified 2026-08-04** |
| `plans/m8_step1b_ahmed_transfer_addendum_a.md` | `b8625f6e…d02fed0` | user-approved 2026-07-30; re-review waived |

Built and tested — **no real capture or Masimo file has ever been opened by this work.** Modules:
`src/m4/outcome.py`, `estimator_suite.py`, `production_suite.py`, `bundle.py`,
`capture_registry.py`, `estimator_scoring.py`, `estimator_runner.py`; `src/m8/ahmed_transfer.py`,
`ahmed_synthetic.py`, `ahmed_gate.py`, `ahmed_provenance.py`; `scripts/m8_ahmed_transfer.py`.

**Gate passes in-process, 14/14.** Verdicts exactly as predeclared:
`collision_domain_from_fb` → `not_transferred_under_declared_assumptions` (~20 bpm, the breathing
bin); `real_representative_domain` → `transferred_under_declared_seed_and_configuration` (80.04 bpm).

**Remaining:** (1) synthetic capture fixture + the runner's decode/dispatch loop + the strict
production serializer; (2) `test_attestation.json` with enumerated node IDs; (3) freeze the gate
bundle. **No canonical gate bundle exists yet.**

**Open question worth raising with the user before building:** Step 1b carries heavy apparatus —
frozen gate bundles, pre-data authorization YAMLs, provenance hashing scoped over `src/**`,
`scripts/**`, `tests/**`. That was designed under the pre-registration discipline, and **M0 is
gone**. It is not M0 and did not die with it, but it costs real time. Worth a decision before
building to its requirements.

**Sequencing:** `_SCOPED_TREES` covers those three trees, so **any** change under them invalidates
a frozen bundle. Freeze only when you intend to stop touching them.

**When scoring on real data, state the §2.2 limit up front:** these captures support BR agreement
and HR coverage/feasibility, **not HR tracking**.

## 6. Settled questions — do not re-open without new data

- **Static clutter removal** — implemented (`src/clutter.py`), **OFF**, and measured not to help.
  Pinned bin 12%→12%; warmup free 13%→**7%**, lock moved in 4 of 8 captures, worst case massimo2
  80%→0%. The default path is bit-identical, pinned by
  `tests/test_clutter.py::test_default_path_is_bit_identical`.
- **The 5-bin relock tracker** — simulated 2026-08-04 (`scripts/simulate_bin_policy.py`).
  **`P3_relock` fired 0–1 times across all 38 combinations** — inert, reproducing the bin-drift
  finding of frequent short flicker but no sustained drift. **Do not port the relock half from
  `git stash@{0}`.** The neighbourhood-read half (`P2`) does all the work and was not adopted
  either: on the holdout it beat production on accuracy (MAE 2.60 vs 3.41) but cost half the
  coverage (46% vs 89%), and regressed `massimo5` on every axis.
- **Warmup bin selection is not the HR coverage bottleneck** (§2.3). It still matters for **BR
  accuracy** — over the 105 admissible `k>=1` windows the per-window oracle ceiling is MAE 1.10
  against production's 2.68, so a correct per-window bin more than halves BR error. About a third
  of that headroom is reachable with no fitted parameters (§4.2); the rest is not reachable by any
  rule this dataset can identify.
- **A learned multi-feature bin-selection rule does not beat a zero-parameter consensus rule** —
  measured 2026-08-04 (`scripts/br_bin_preflight.py`). Out-of-fold 2.384 vs medoid 2.291; winning
  sign vector unstable across all 4 leave-one-subject-out folds; a permutation null shows searching
  834 vectors on shuffled errors reaches 2.665 for free against a 3.370 null. **Do not re-open by
  adding features** — the constraint is four subjects. Re-open only with more subjects, and re-run
  the same script, which is the go/no-go.
- **Respiration collapse** — fixed in `src/respiration.py` via band-edge veto by bin identity
  (`resp_edge_veto`) plus STFT-consistency gates. **Do not re-open the fix.** Accepted cost: a
  genuine ~6 bpm breather on the edge bin is permanently invalid. Only **done-when #5** is open —
  a BR score under the M3 comparator, blocked on a capture with non-approximate alignment (§4.1).

## 7. Gotchas that will bite you

- **The BR holdout is partly spent.** `massimo4`–`massimo7` were scored under **two** operating
  points of the same policy family (tol=2, then tol=5 at the user's request). They are no longer a
  clean holdout for *any* bin-policy question. The §4.1 plan resolves this by moving them into
  training and testing on the 3 new captures.
- **The 3 pre-M2-fix captures do not reproduce their recorded warmup evidence.** `massimo1`,
  `massimo2`, `sweep` (2026-07-13/14) mismatch with the signature `br_bpm = 6.0` — the collapse
  floor. **Their `run_metadata.json` `locked_bin` was chosen by buggy code**; massimo2's and
  sweep's live locks (20, 21) are documented mislocks. **Re-derive the lock with current code**
  (`run_warmup_selection` on window 0) rather than trusting the metadata. Current code gives
  massimo1 27, massimo2 26, sweep 26.
- **Any DSP change can move the bin lock**, because warmup scores candidates by running the full
  chain. Run `scripts/validate_warmup_selection.py` after any change — fastest signal that
  selection moved.
- **The BR admissibility gate drops 15 of 120 `k>=1` windows, and every exclusion is by
  stationarity, none by availability** — massimo7 −5, massimo3/4/5 −3 each, massimo6 −1, and zero
  from massimo1/massimo2/sweep. Measured 2026-08-04; not uniform, so always report it.
- **`is_locked_bin` in the sweep CSV is the PRE-M2-FIX recorded lock**, and it looks like the right
  column. Banned as feature and as baseline in `src/br_features.py:BANNED_COLUMNS`. Re-derive with
  `run_warmup_selection` (`scripts/simulate_bin_policy.py:current_code_lock`) — it moves three of
  eight locks: massimo1 23→27, massimo2 20→26, sweep 21→26.
- **`oracle SNR` and AHET's `ratio_db` are not comparable** — different spectra (no-ECA vs post-ECA
  second pass) and different floors.
- **Line endings are pinned to LF and it is load-bearing.** Any script hashing a text payload must
  write binary or `newline="\n"`.
- **Canonical Step 1a bundle: 2 payload hashes are CRLF-era.** Documented erratum in `HISTORY.md`.
  **Do not "fix" the digests** — that is falsifying a provenance record and was explicitly rejected.
- **Never parse `git status --porcelain` from a stripped string** — its status column has
  significant leading spaces. Use `src/m8/ahmed_provenance.py::git_status_paths`.
- **Pin git state in tests asserting on promotion, `reproducible` or cleanliness** (`_pin_git_provenance`).
- Git's index caches on `(size, mtime)`; a same-length rewrite in the same second is not seen as
  modified. Change the length in fixtures.
- **Session types are not machine-recorded.** `notes/capture_inventory.md` covers massimo1
  (natural), massimo2 (**paced 16 bpm**), sweep (stepped 12→15→18→21); massimo3–7 the user declared
  natural. Verify against the Masimo RRp channel — massimo2 reads a flat 16.0, massimo3–7 wander
  3–10 bpm. `score_offline.py` requires an explicit `--session-type` (OSR-19) and forbids inferring it.
- **Nothing checks a new capture** for packet loss, frame misalignment, ADC clipping or a mirrored
  I/Q convention — that tooling was removed 2026-07-31 on user instruction. `live_demo.py` writes
  `live_raw_mirror_hash: null`, so `score_offline.py`'s directory-form `--pinned-lock-source` will
  refuse every future live capture; use the integer form (tagged `kind="manual"`).
- **6 of 8 captures have no raw hash in their metadata.** massimo1/2/sweep have independent
  2026-07-25 hashes in `notes/capture_inventory.md`; massimo3–7's are in `HISTORY.md` 2026-07-31 only.
- `results/live_demo/` holds exactly the 8 canonical captures. massimo7 has duplicate/missing
  Masimo seconds — use the parser's integer-`Timestamp` dedup, never hardcoded row counts.
- Do not mutate `data/raw/`, `results/live_demo/`, live estimates, metadata, warmup evidence or
  Masimo CSVs.
- **Environment:** plain `conda` is not on PATH. Use
  `& 'C:\ProgramData\anaconda3\condabin\conda.bat' run -n radar-vitals python …`, and never call the
  env's `python.exe` by absolute path (it crashes matplotlib `savefig`).

## 8. Pointers

| Purpose | Path |
|---|---|
| Project rules | `CLAUDE.md` |
| Append-only log | `HISTORY.md` |
| Milestone roadmap | `plans/implementation_plan.md` |
| Method rationale, ECA+AHET spec | `notes/approach.md` |
| Analysis spec (window grid §7, evidence floor §2a/§2b) | `notes/analysis_prespec.md` |
| HR / BR comparator specs | `notes/comparator_prespec.md`, `notes/comparator_prespec_br.md` |
| Capture protocol, ethics, recovery arm, scene requirement | `notes/protocol.md` |
| **Capture inventory + SUBJECT MAP** | `notes/capture_inventory.md` |
| Approved IBEC amendment (recovery arm) | `notes/ethics_amendment_hr_recovery.md` |
| **M8 Step 1b authority (both required)** | `plans/m8_step1b_ahmed_transfer.md` + `…_addendum_a.md` |
| Production DSP | `src/respiration.py`, `src/vitals.py`, `src/window_pipeline.py` |
| Warmup selection | `src/warmup_select.py`; knobs in `scripts/live_demo_config.yaml` (`bin_selection`) |
| Warmup regression check — run after any DSP change | `scripts/validate_warmup_selection.py` |
| Offline scorer / comparators | `scripts/score_offline.py`, `src/comparator.py` |
| Per-bin sweep (feature source) | `scripts/diagnose_bin_sweep.py` → `results/diagnose/bin_sweep/20260804T131040Z/` |
| Signal-presence audit (feature source) | `scripts/diagnose_signal_presence.py` → `results/diagnose/signal_presence/20260731T155946Z/` |
| Bin-policy simulator | `scripts/simulate_bin_policy.py` → `results/diagnose/bin_policy/` |
| **FROZEN BR bin rule + one-touch test** | `scripts/br_bin_rule.py` → `results/diagnose/br_bin_rule/20260804T204634Z_freeze/frozen_rule.json` |
| **BR bin rules: feature table + guards** | `src/br_features.py` |
| **BR bin rules: search, LOSO, permutation null** | `src/br_bin_search.py` |
| **BR bin-selection pre-flight / go-no-go** | `scripts/br_bin_preflight.py` → `results/diagnose/br_bin_preflight/20260804T192908Z/` |
| Static clutter removal (off by default) | `src/clutter.py` |
| Frozen window grid | `src/m4/window_grid.py` |
| The 8 captures | `results/live_demo/` |
