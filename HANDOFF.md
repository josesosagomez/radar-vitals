# Handoff — resume here

> Read this and `CLAUDE.md` before doing anything. State verified 2026-08-03.
> `HISTORY.md` is the append-only log; this file is the always-current summary.

## 1. Project snapshot

Estimate heart rate and breathing rate from a TI IWR1642BOOST + DCA1000 FMCW radar while a subject
sits 0.8–1.4 m away, and quantify agreement against a Masimo MightySat fingertip pulse oximeter.
HR truth is the Masimo `Beats / min` column; BR reference is `Breaths / min`; both align on the
integer Unix-epoch `Timestamp`. Target output is a journal paper plus a thesis chapter.

**Method in one line:** range FFT → warmup-locked chest range bin → phase extraction
(`delta_before_mean`) → for BR, a fused FFT/HA/STFT estimate over 0.10–0.50 Hz; for HR, **ECA**
(project out the respiration-harmonic subspace) → argmax in 0.8–2.0 Hz → **AHET** second-harmonic
verification, which returns **NaN rather than a guess** when it fails. 30 s non-overlapping windows
at 20 Hz.

**There is no static clutter removal in that chain.** `notes/approach.md` and an earlier version of
this file both claimed there was; the claim was false and is corrected. See §6.

**That one-liner flattens warmup, and the flattening misleads.** Warmup is not a post-FFT filter
that picks a bin — it runs the *entire* downstream chain once per candidate bin and scores the
results. See §3, and read §3.0 first: warmup has been measured and is **not** the coverage
bottleneck.

## 2. Where the project actually is

**Infrastructure is strong; the science is thin.** That gap is the single most important thing to
understand before planning work.

- **BR end-to-end accuracy has never been measured. Not once.**
- HR agreement exists only as single-subject numbers stamped `exploratory_non_frozen`.
- 8 captures exist, **all one subject**, all exploratory.
- **HR coverage is 12%** pooled over those 8 captures (8 of 67 admissible windows, run
  `20260730T204448Z`). Three captures — massimo3, massimo5, massimo7 — score **0%**. Coverage, not
  accuracy, is the bottleneck.
- **Where that bottleneck lives is now measured, not guessed (2026-07-31).** It is *not* bin
  selection: at every plausibly-chest bin of all three 0% captures the yield is ~0%, and AHET's
  second-harmonic ratio runs a median 3.2 dB below its gate. See §3.0.
- **The captures are good enough for BR and NOT good enough to validate HR (2026-07-31).**
  BR presence is demonstrated; HR tracking is not, and — the load-bearing part — **these
  sessions cannot demonstrate it even in principle**, because within-session PR spread is
  narrower than any sane tolerance. See §2.1. **Read that before writing any HR acceptance
  criterion.**
- The 10-subject × 3-session study (M6) has not started, and neither has the pilot (M5). **Neither
  is blocked any more** — M0 was removed 2026-08-03 (§4).

| Track | Milestone | Status |
|---|---|---|
| A | M1 live smoke test | **not run** — cheapest risk reduction available |
| A | M2 respiration-collapse fix | **fix landed**; only done-when #5 (validation) open — see §5 |
| A | M3 BR comparator pre-spec | **closed**, 48/48 findings |
| A | M4 offline evaluation harness | built (`scripts/score_offline.py`), has run for real |
| 0 | ~~M0 pre-registration deposit~~ | **REMOVED from the project 2026-08-03 by user decision** — see §4 |
| B | M5 pilot / M6 main study | **not started; no longer blocked by anything** |
| C | **M8 / M9 — the two published methods** | **CURRENT FOCUS** — see §4 |
| C | M8 Step 1a (Ahmed reproduction) | done — **scientifically negative**, canonical bundle committed |
| C | M8 Step 1b | implemented through the gate; see §7 |
| C | M9 (Kotte) / M10 baselines | **not started** |
| D | M12 paper, figures, chapter | not started |

Active branch: **`vital_signs_ahmed_v10`**. Test baseline: **2064 passed, 5 skipped** (verified
2026-07-31). The 5 skips are honest absences (4 OSR-03 tests need replay artifacts that no longer
exist), not passes.

### 2.1 Are the 8 captures good enough? BR yes, HR no (2026-07-31)

Measured by `scripts/diagnose_signal_presence.py`; evidence
`results/diagnose/signal_presence/20260731T155946Z/`, full write-up in `HISTORY.md`
2026-07-31. Everything below is a **feasibility ceiling, not a result** — the audit used the
Masimo reference to decide where to look and switched every verification gate off. It may
never be quoted as accuracy or coverage, and **no threshold, band edge or bin choice may be
derived from it** (§3.5, CLAUDE.md §4).

**The reference is not the weak link.** All 8 captures have **100% of Masimo rows at
PI ≥ 0.5** (medians 3.8–15.0), physiological PR (67–91) and RR (15–18) medians, and full
temporal overlap with the radar. That candidate explanation is eliminated.

**BR — present and extractable.** The true respiration frequency beats random decoy
frequencies on the same spectra in **7 of 8** captures (beat fraction 0.57–0.68; sign test
significant in 4). BR oracle SNR at the locked bin is **+8.2 to +17.3 dB** in 7 of 8.
**Tracking is demonstrated in `sweep`** — permutation `p=.001`, Spearman +0.56 locked /
+0.90 best bin — and `sweep` is the only capture whose protocol deliberately varies BR
(stepped 12→15→18→21). BR work can proceed on this data.

**HR — not demonstrated, and not demonstrable here.** Two independent grounds:

1. **No evidence of tracking.** Permutation `p = .30–1.00` in every capture. The decoy
   control is significant in only 2 of 8 (massimo1, massimo2 — the short 2026-07-13 pair).
   At the locked bin the heart-band argmax is **worse than a constant predictor in all 8**.
2. **The captures structurally cannot settle it.** Within-session PR spread (p10–p90) is
   **2.6–5.2 bpm — narrower than the ±5 bpm tolerance.** A predictor that ignores the radar
   and emits the session-median PR scores **83–100%**. These sessions cannot distinguish a
   working HR estimator from a stub returning 85 bpm.

**Consequences — this is the part that changes plans:**

- **Do not write an HR acceptance criterion against these 8 captures.** It would be
  unfalsifiable: passed by a constant. This is the single most important constraint on the
  M11a coverage work described in §4.
- **The M5/M6 protocol needed HR dynamic range, and now has it.** `notes/protocol.md`'s
  "HR dynamic-range arm — the seated RECOVERY capture" was **ethics-approved 2026-08-03** and
  the study is now 10 subjects × 3 sessions. Two non-ethics gates remain (CLAUDE.md §6
  cross-review; the analysis pre-spec edit — both now lower-stakes since M0 was removed). See §2.2.
- Beware quoting the raw hit-rate table in that report without its controls. Its first
  version showed an "HR ceiling" of 50–100% (75% on massimo3, a 0%-coverage capture); the
  permutation null and constant baseline showed that was selection bias over 14 bins plus a
  narrow reference, not signal.
- **`oracle SNR` is not comparable to the AHET `ratio_db` of §3.0** — different spectra
  (no-ECA vs post-ECA second pass) and different floors. The H2 oracle SNR of +2.6 to
  +8.1 dB does **not** contradict AHET's ratio running a median −2.2 dB below its gate.

### 2.2 The recovery arm — ETHICS-APPROVED 2026-08-03

`notes/protocol.md` carries **"HR dynamic-range arm — the seated RECOVERY capture"**, written
to fix what §2.1 found. **IBEC, KAUST approved the amendment to `24IBEC051` on 2026-08-03**
(submitted 2026-07-25; text at `notes/ethics_amendment_hr_recovery.md`, filled in and used as
submitted). The study is now **10 subjects × 3 sessions**.

**As approved:** self-paced step-ups to **100–120 bpm**, under 4 minutes, in the lab with the
researcher present and the Masimo worn throughout; then the standard seated 10-minute
recording while PR decays; subject monitored until PR is within 5 bpm of pre-exertion resting.
Session 3 of 3, order fixed natural → paced → recovery, never same-day as another session.

**Approval came with conditions that are now mandatory** — PAR-Q+ before any exertion (any
positive response excludes the subject *from this arm only*), the cardiovascular/respiratory/
musculoskeletal/pregnancy/medication/acute-illness exclusions, the stopping rules, and the
amended consent and information sheet. They are listed in `notes/protocol.md`; the arm may not
be run without them.

**What ethics approval does NOT do — read this before planning a capture:**

- ~~It does not lift the M0 gate.~~ **Superseded 2026-08-03: M0 was removed and its gate with it.**
  Study captures are no longer blocked by governance. They are still gated on the ordinary
  prerequisites (M1, M2, M4) and on the screening conditions above.
- **It is not scientific review.** The arm is an experimental-plan change and has **not** had
  its CLAUDE.md §6 cross-review. Ethics ruled on safety and consent, not on whether the design
  answers the question.
- **It leaves the analysis pre-spec contradicting the protocol, in four specific places.**
  `notes/analysis_prespec.md` is **not yet frozen**, so this is a **pre-freeze edit, not a §4
  amendment** (no new version DOI implied) — but it needs §6 cross-review, since the completed
  M3 review covered the 2-arm design. A dated PENDING banner in that file enumerates the
  conflicts: §1's arm set and 2-session design; §2b's per-subject floor ("across the **2
  sessions**"); §2b's miss rule (no branch for a recovery arm); and §2b's "no add-sessions
  lever" clause, which declares the *"`24IBEC051` permits > 2 sessions/subject?"* question
  **moot** — now contradicted on its face. The edit must state that the recovery arm is *not*
  an add-sessions lever in the §2b sense: it does not add sessions to raise evidence yield, it
  adds an arm to make the HR claim falsifiable. **The protocol is the newer document and
  governs what is captured.**
- **The mental-arithmetic fallback (§10 of the submission) is moot.** Do not run it in place of
  the approved arm.

**The live risk to watch:** this arm is *deliberately* non-stationary, while HR admissibility
requires within-window PR spread ≤ 5 bpm (`src/comparator.py:_HR_STATIONARITY_MAX_BPM`).
Early-recovery windows will legitimately fail it, so **the frozen §2a/§2b evidence floor may
not be achievable in this arm.** Whether that floor is arm-specific must be decided *before*
any M6 claim — a floor adjusted after seeing this arm's yield is not a floor. This survives M0's
removal as an honesty rule, not a governance one.
- **It has not been cross-reviewed.** CLAUDE.md §6 covers experimental-plan changes.
- **Why recovery and not paced slow breathing.** The protocol's SETTLE CRITERION (no
  monotonic drift) and the comparator's within-window stationarity gate (spread ≤ 5 bpm,
  `src/comparator.py:_HR_STATIONARITY_MAX_BPM`) *both* enforce HR stationarity — that is the
  root of the problem. They reconcile with across-session range in exactly one shape: a slow
  monotonic ramp. RSA fails because it adds *within*-window variance (making windows
  inadmissible) rather than across-window range, and because 6 bpm = 0.10 Hz is the exact
  bottom edge of `respiration.band_hz`, which the M2 `resp_edge_veto` permanently invalidates
  (§5). The rejected alternatives are tabulated in the protocol — do not re-propose them
  without new argument.
- **The arm carries a pre-specified adequacy criterion** computed from the Masimo CSV alone
  (≥10 admissible windows, PR span ≥20 bpm, constant-predictor hit <50%), so the capture can
  fail honestly. It was calibrated against all 8 existing captures: **all 8 fail**, and the
  constant-predictor criterion fails at **100%** on every one.
- **The SETTLE CRITERION is deliberately disapplied to this arm only.** It forbids exactly the
  drift the arm exists to create. It still binds every other arm, without exception.
- **Consistency propagation is deliberately deferred.** `notes/protocol.md`'s header requires
  protocol changes to propagate to `scripts/live_demo_config.yaml`, CLAUDE.md and
  `notes/approach.md` in the same commit. No DSP setting changes, and the other two are left
  alone **on purpose** so an unapproved arm does not read as frozen protocol. Propagate on
  approval, not before.

### Active task — undecided, and that is a user call

The previous active task (warmup range-bin selection) **closed on 2026-07-31**: it was measured and
is not the coverage bottleneck (§3.0). No replacement has been chosen. The four concrete options,
all unblocked:

1. **M1 live smoke test** — still never run, still the cheapest risk reduction. If it carries a
   Masimo reference *and* the `notes/protocol.md` step 3a clock sync, it is also the **only**
   remaining route to discharge M2 done-when #5 (§5).
2. **Coverage (M11a), aimed at what §3.0 actually found** — AHET second-harmonic verification
   failing at chest bins, and the 27.6% of cells that never reach AHET because respiration is
   invalid. Higher value, higher risk: it touches heart-band peak-picking, so CLAUDE.md §6
   cross-review applies, and loosening a verification gate raises coverage by construction — the
   acceptance criterion must be written **before** any threshold is touched, and §4 forbids tuning
   it against Masimo. **§2.1 constrains this hard:** an HR criterion cannot be validated on the
   existing captures at all, so either scope it to BR, or treat the HR half as exploratory and
   defer its acceptance test to data that has HR dynamic range.
3. **Close out the §2.2 recovery arm** — ethics is done (2026-08-03). What remains is the
   CLAUDE.md §6 cross-review and the `notes/analysis_prespec.md` pre-freeze edit (3 arms, and
   the arm-specific evidence-floor decision). Documentation-only, and it is now the **shortest
   way to keep the protocol and the analysis spec from contradicting each other.**
4. **M8 Step 1b remaining items** (§7) — synthetic capture fixture + runner decode/dispatch loop +
   strict serializer, then `test_attestation.json`, then freeze the canonical gate bundle.

**Do not** start M5/M6 captures (§4), and do not re-open warmup as a coverage fix (§3.0).

## 3. Warmup range-bin selection — measured, and NOT the bottleneck

**Read §3.0 before proposing any warmup work.** The question that made warmup the active task has
been answered, and the answer moved the work elsewhere. The rest of §3 is retained because warmup is
still load-bearing and its prior art is still easy to miss — not because it is the next task.

### 3.0 The per-bin sweep result (2026-07-31) — read this first

massimo3/5/7 were scored at **every** candidate bin on every window, not just the locked one:
3 captures × 20 windows × 14 bins = 840 cells. Radar-side only — **no Masimo file was opened**, by
construction (§3.5 forbids picking a bin by reference agreement). Evidence:
`results/diagnose/bin_sweep/20260731T004043Z/` (on disk, gitignored), script
`scripts/diagnose_bin_sweep.py`, full write-up in `HISTORY.md` 2026-07-31.

**The 0% captures are not bad locks.** Split at the −12 dB eligibility line:

| bin class | cells | hr_valid | yield |
|---|---|---|---|
| energy-eligible (≥ −12 dB, plausibly the chest) | 491 | 4 | **0.8%** |
| skirt (< −12 dB) | 349 | 20 | 5.7% |

Every energy-eligible bin in all three captures yields ~0%. The only bins with any yield sit
20–34 dB down — exactly the bins the eligibility threshold exists to distrust. Best in-gate bin per
capture is massimo3 21 → 15%, massimo5 28 → 5%, massimo7 20 → 30%, all skirt bins, none usable.

**The binding constraint is AHET's second-harmonic check.** At energy-eligible bins the best
available second-harmonic peak-to-floor ratio has **median −2.23 dB** against the +1.0 dB
`candidate_min_second_harmonic_ratio_db` gate; only 14.1% of eligible cells reach it. The second
harmonic is usually *below* the in-band noise floor — there is nothing at any in-gate bin for AHET
to verify. Separately, **232/840 cells (27.6%) never attempt AHET at all**: `spectrum_stage == 0`,
the `f_r_hz is None or f_r_is_outlier` no-ECA path (`src/vitals.py:523`), so `hr_valid` is False by
construction. The two modes cover 88.6% of cells.

**Warmup is locking onto the subject, and breathing proves it independently.** Locked-bin BR
validity is 17/20, 16/20, 16/20; the highest-BR-yield bins are high-energy in-gate bins adjacent to
each lock. Phase extraction works and the chest is where warmup says it is.

**Consequences for planning:**
- **Do not re-run this sweep** on massimo3/5/7 expecting a different answer, and do not open warmup
  work justified by "the 0% captures might be mislocked". They are not.
- The script's own verdict string says `another_in_gate_bin_yields_more` for all three. That is
  literally true and **misleading on its own** — the winners are unusable skirt bins. Quote the
  table, not the verdict.
- **Still genuinely open:** whether the deep-skirt-bin passes are true cardiac readings. massimo7
  bin 20 (6 passes, median 80.3 bpm, successive-difference MAD 5.6) and massimo3 bin 21 (3 passes,
  median 87.1, MAD 4.0) look coherent; massimo3 bin 27 (56.3 then 93.5 bpm, MAD 37.1) does not.
  Settling it needs a reference comparison, which is the §3.5 hazard — design that carefully or
  leave it open.
- massimo7's bin 32 lock is **corroborated as anomalous** (§3.3): bins 23/26 have BR validity 19/20
  and sit 5–7 dB higher. But both yield **0%** HR, so relocking it would change nothing.

### 3.1 What warmup actually does

`src/warmup_select.py::run_warmup_selection`, called once at session start on the first
`FRAMES_PER_WINDOW` (600) frames:

1. `range_energy_by_bin` over every candidate bin → mean power (Hann + `scipy.fft.fft`).
2. Same again on the **settled** sub-window, skipping `settle_skip_s` (default 5 s), so a settling
   transient cannot inflate a skirt bin past the eligibility gate.
3. **For each candidate bin, run `run_window_dsp` in full** — phase extraction, BR fusion, ECA,
   AHET. This is the part the one-line method summary hides.
4. Score: `hr_valid` **+1000** (only if the bin's settled energy is within
   `energy_eligibility_min_settled_db`, default **−12 dB**, of the strongest candidate — an
   *eligibility partition*, not a bonus), BR confidence high/medium/low **+250/+100/−100**,
   `br_valid` **+50**, minus `5 ×` energy rank. Tie-breaks then run through `hr_bonus_granted`,
   BR confidence order, `br_valid`, energy rank, distance from gate centre, bin index.
5. **Lock the winner for the whole session.** Evidence → `warmup_bin_selection.json` per capture.

Candidates come from `derive_candidate_bins` — the 0.8–1.4 m protocol gate at 0.0436 m/bin = bins
**19–32**, i.e. 14 candidates. So warmup transforms its window ~16 times and runs the full HR/BR
chain 14 times before the first estimate exists.

**Consequence that has bitten twice: any downstream DSP change can move the bin lock.** The M2
respiration fix moved massimo1 from 23 to 27; enabling clutter removal moved 4 of 8 captures. A
change intended to be "phase-extraction only" is not isolated from selection.

### 3.2 Why warmup still matters, even though it is not the bottleneck

Warmup picks the single bin every downstream estimate depends on, and a wrong lock still costs a
session. What §3.0 settles is narrower: **it is not what is capping coverage at 12%.** Warmup work
is therefore correctness/robustness work — protecting against a bad lock on a *future* subject —
not coverage work, and it should not be sold as the latter.

### 3.3 Known-suspicious observations (evidence, not verdicts)

- **massimo7 locked bin 32** = the gate *edge* (1.40 m), while its strongest in-gate energy is at
  bin 24 (1.05 m). That bin is clutter-dominated (+10.6 dB static-to-moving) with a phase
  peak-to-peak of 1.3 rad against 18–40 rad for signal-dominated bins. Still unexplained as a
  *lock*, and the §3.0 sweep corroborates that it is anomalous — bins 23 and 26 have BR validity
  19/20 against bin 32's 16/20 and sit 5–7 dB higher. **But it is not worth chasing for coverage:**
  bins 23 and 26 both yield 0% HR over all 20 windows.
- **massimo1 now locks 27, was 23 live.** Bin 27 won on a lone AHET pass at **−8.7 dB**, which
  clears the −12 dB eligibility threshold. The threshold exists precisely to stop a skirt-bin AHET
  pass outvoting the chest; −8.7 dB is inside the margin.
- **massimo2 and sweep live-locked 20 and 21; both are mislocks**, corrected bin 26 for each
  (`notes/capture_inventory.md`). massimo3–7 have **no** corrected bin established.
- **The +1000 `hr_valid` bonus makes selection partly circular** — the bin is chosen using the
  quantity the bin is then used to measure. Not necessarily wrong; not examined.
- `src/warmup_select.py:86-90` states its own operating assumption as "single seated subject is the
  dominant reflector inside the distance gate" and flags it as *not validated against competing
  reflectors*. For the five 2026-07-28 captures the subject sits **3.3–9.6 dB below** static
  reflectors at 2.09 m and 2.88 m (`notes/protocol.md`, "Scene behind the subject").

### 3.4 Prior art — read before proposing anything

- **`plans/bin_drift_diagnostic.md`** (+ `_cross_review.md`, review loop closed) and
  **`scripts/diagnose_bin_drift.py`** — a read-only diagnostic measuring whether in-gate energy
  drifts from its settled warmup baseline. It has been **run**; evidence at
  `results/diagnose/bin_drift/20260728T004453Z/` (on disk, gitignored).
  **Finding:** all 4 sessions show frequent short (<2 s) argmax flicker but almost no sustained
  (≥5 s) drift, and in massimo1 ≥2 s excursions occur across every outcome class without separating
  `covered` from `gate_not_run` — i.e. drift is **not** obviously the coverage-loss mechanism.
  **It has never been run on massimo3–7.**
- **The 5-bin relock tracker is DEFERRED, not rejected** (decision 2026-07-28, Option C). Deferred
  because the evidence is n=1 subject who barely moved; a higher-movement subject could change it.
  A working prior implementation (~371 lines in `live_demo.py` + ~555 test lines, commit `0022845`,
  reverted 2026-07-09 as not worth the complexity) still sits in **`git stash@{0}`** — stale
  relative to HEAD, so reviving it is a port plus re-validation, not a `git stash pop`.
  **Do not record this as "decided against."**
- `scripts/validate_warmup_selection.py` — pins the selection for massimo1/massimo2/sweep against
  expected bins, and each expectation names the fix that last moved it. **Run it after any change**;
  it is the fastest signal that selection moved.
- `scripts/diagnose_live_run.py`, `scripts/diagnose_coverage_gaps.py`, and each capture's
  `warmup_bin_selection.json` (all 8 present) carry the per-candidate evidence.
- Tests: `tests/test_live_demo_warmup_helpers.py`, `tests/test_step3_bin_selection.py`,
  `tests/test_diagnose_bin_drift.py`.

### 3.5 Constraints on this work

- **CLAUDE.md §6 applies.** Bin selection is explicitly in the cross-review list — any change to
  the scoring rule, the −12 dB threshold, or the candidate derivation needs an independent pass
  before it lands.
- **Do not tune against Masimo agreement.** Selection must be decided on radar-side evidence; §4
  forbids choosing a bin because it matches the reference better.
- Changing selection **breaks live/offline bin reproduction** for existing captures, which M4R-10
  exists to protect (`src/warmup_select.py:5-7`). It has already happened once for massimo1. If it
  happens again, record it rather than re-baselining silently.
- `src/m8/ahmed_provenance.py::_SCOPED_TREES` covers `src/**/*.py` — edits here invalidate any
  frozen Step 1b gate bundle (none exists yet; see §7).

## 4. The critical path — the two published methods (M8, M9)

**Decision, user, 2026-08-03: M0 (pre-registration) is REMOVED from the project, not postponed.**
Its hard gate on study captures is removed with it. The focus is now Track C — establishing
whether either published method recovers HR or BR on the eight captures that already exist.

**What M0's removal costs — and the standing rule that follows.** Nothing in this project is
pre-registered. Agreement results are **exploratory / descriptive**, never confirmatory. Both
writing files were **purged** on 2026-08-03, not merely flagged: every affirmative claim is gone
and a sweep confirms the only surviving mentions are prohibitions. **Never reintroduce
"pre-registered", "pre-specified", "frozen before data", "registered <date>", "deposited" or
"confirmatory"** about this study's own specs or results, in any file, manuscript, title or talk.
The full standing rule is `HISTORY.md` 2026-08-03 ("Pre-registration language purged"); it governs
over any residual M0 mention in the append-only review logs under `plans/`.

**Say this instead, because it is true and still strong:** the comparator and analysis
specifications are written down in full and applied *identically to every estimator compared*. A
**transparency** contribution, not a **timing** one.

**The paper's headline is now:** *first real-data validation of two simulation-only published
methods — Ahmed harmonic accumulation [R1] and Kotte joint high-amplitude-difference Doppler [R2]
— under one common comparator, with coverage reported.* User-agreed 2026-08-03. It is
contribution 1 in `THIRD_CHAPTER.md`, the Paper A headline and title option 1 in
`JOURNAL_PAPER.md`, and it opens the abstract skeleton. **It is IN PROGRESS: M8's real-data arm
has never opened a capture and M9 has not started. If the methods work is abandoned, the paper has
no headline** — the old one is gone and cannot be reinstated.

**What survives and must NOT be deleted.** `notes/analysis_prespec.md`,
`notes/comparator_prespec.md`, `notes/comparator_prespec_br.md`, `notes/protocol.md` and
`notes/capture_inventory.md` stop being deposit artifacts and continue as **internal engineering
specs**. Code depends on them — `src/m4/window_grid.py` cites `analysis_prespec` §7 as its
authority and hard-errors against it, and `src/comparator.py` implements `comparator_prespec`. The
frozen-grid invariants stay frozen: they keep results comparable across runs, which has nothing to
do with pre-registration. `plans/m0_preregistration.md` and `plans/m0_b1_evidence_floor_memo.md`
are marked RETIRED in place, not deleted (CLAUDE.md §9).

**The current focus, in order:**

| priority | why |
|---|---|
| **M8 — Ahmed, Harmonic Accumulation** | Step 1a done and **negative**; Step 1b built through the synthetic gate but **has never opened a real capture**. The plan already calls it the cheapest high-value milestone, and its target — breathing harmonics masking the heartbeat — is exactly the failure §2.1 measured. Shortest path to an answer. |
| **M9 — Kotte, joint high-amplitude-difference Doppler** | Not started. Uses the **4 RX channels the current pipeline throws away**, and targets the lung-masks-heart problem directly. Synthetic controls first, per the Track C rule. |
| M1 live smoke test | hours, no dependencies, unscored, and now the cheapest route to discharging M2 done-when #5 (§5). |
| M5 / M6 captures | **no longer blocked by governance.** Still gated on the ordinary prerequisites and on §2.2's screening conditions. |

**Read §2.1 before scoring either method on HR.** The eight captures cannot falsify an HR claim —
a constant predictor scores 100% on every admissible window. That constrains what M8/M9 can
conclude: on this data they can demonstrate **BR** agreement and **HR coverage/feasibility**, but
**not HR tracking**. Design the comparison to say so up front rather than discovering it at the end.

**Still open:** whether to attack **coverage (M11a)** directly rather than via M8/M9. It is much
better informed than when last deferred — two of the
three leading candidate explanations have now been measured and eliminated:

- **static clutter removal** — measured, rejected, and it destabilises the bin lock (§6);
- **a bad warmup bin lock** — measured and eliminated as the cause (§3.0).

What the same measurement points at instead, on the three 0%-coverage captures: **AHET
second-harmonic verification failing at chest bins** (median ratio −2.23 dB against a +1.0 dB gate;
only 14.1% of eligible cells reach it), and **27.6% of cells never attempting AHET at all** because
respiration is invalid or an outlier so the estimator takes the no-ECA path. Coverage remains 12%,
but it is no longer *unexplained* — see §3.0 and `HISTORY.md` 2026-07-31.

Anything that changes the AHET gate is a heart-band peak-picking change and so falls squarely under
CLAUDE.md §6 cross-review. It is also the classic place to fool yourself: loosening a verification
gate raises coverage by construction, and §4 forbids tuning it against Masimo agreement. Any such
work needs its acceptance criterion written down *before* the first threshold is touched.

## 5. Respiration collapse — the fix landed; only validation is open

Do not re-open the fix.

**What happened:** the BR search band is `[0.10, 0.50]` Hz, so 6 bpm is the *lowest bin in the
search*. Body motion filled the band with low-frequency drift, the argmax slid to the wall and
stayed there, and `resp_valid` remained `1` throughout.

**It also silently disables HR.** `f_r = 0.1 Hz` makes every ECA harmonic either forbidden (in-band)
or irrelevant (below band), so ECA removes 0.00 dB in-band and HR degrades to a bare argmax.
"Respiration collapse" and "ECA is inert at low f_r" are **one causal chain**, not two defects.

**Fixed** in `src/respiration.py`: band-edge veto by *bin identity* (`resp_edge_veto`) plus
STFT-consistency gates. Accepted cost: a genuine ~6 bpm breather on the edge bin is permanently
invalid.

**Open: M2 done-when #5** — score reprocessed BR under the M3 comparator. **Blocked on data,
not DSP:** approximate time alignment cannot produce a frozen-comparator outcome, and none of the 8
existing captures can discharge it. **M1 or M5 discharges it** — M5 is no longer gated (§4), and M1
is the cheaper of the two — but only if the capture carries a Masimo reference and the clock sync of
`notes/protocol.md` step 3a. A smoke test without those does not discharge it.

A worked consequence of this fix is recorded in `HISTORY.md` 2026-07-30: it moved massimo1's warmup
lock from 23 to 27, so **offline no longer reproduces that session's live bin**.

## 6. Static clutter removal — implemented, OFF, and measured not to help

`src/clutter.py` provides `remove_static_clutter`; `phase.clutter_removal` defaults to `"none"`,
which is the pre-existing pipeline bit-for-bit (pinned by
`tests/test_clutter.py::test_default_path_is_bit_identical`).

**Measured and rejected as a coverage fix** (run `20260730T204448Z`, 8 captures, 67 windows,
`--isolate-fields phase.clutter_removal`, `reproducible: true`):

| estimand | coverage OFF | coverage ON |
|---|---|---|
| pinned (bin held identical) | 12% | 12% |
| rerun (warmup re-selects) | 13% | **7%** |

With the bin pinned there is no net change. With warmup free the lock moved in **4 of 8** captures
and coverage halved — worst case massimo2 **80% → 0%**. Wiring it into `extract_chest_phase` alone
does **not** isolate it from bin selection, because warmup scores candidates through
`run_window_dsp`.

Do not enable it, and do not re-propose it as a coverage fix without new data. The code stays
because it is what makes the negative result reproducible.

## 7. M8 Step 1b — current in-flight work

Authority is the **pair** of files; the addendum wins on conflict.

| File | SHA-256 | Status |
|---|---|---|
| `plans/m8_step1b_ahmed_transfer.md` | `9294cb0589b9f0d8f50cdfa0ea893862b1f8ac7f26eb6fee31ee57d622da33ac` | five-discipline PASS on these exact bytes; **unmodified** |
| `plans/m8_step1b_ahmed_transfer_addendum_a.md` | `b8625f6e1e33aa4034591c30f528887910f049fbde75e78627d6d7cadd02fed0` | user-approved 2026-07-30; cross-model re-review **waived** |

Built and tested — **no real capture or Masimo file has ever been opened by this work**. Modules:
`src/m4/outcome.py`, `estimator_suite.py`, `production_suite.py`, `bundle.py`, `capture_registry.py`,
`estimator_scoring.py`, `estimator_runner.py`; `src/m8/ahmed_transfer.py`, `ahmed_synthetic.py`,
`ahmed_gate.py`, `ahmed_provenance.py`; `scripts/m8_ahmed_transfer.py`.

**Gate passes in-process, 14/14 checks.** Transfer verdicts are exactly as predeclared:
`collision_domain_from_fb` → `not_transferred_under_declared_assumptions` (~20 bpm, the breathing
bin); `real_representative_domain` → `transferred_under_declared_seed_and_configuration` (80.04 bpm).

**Remaining, in order:** (1) a synthetic capture fixture + the runner's decode/dispatch loop + the
strict production serializer; (2) `test_attestation.json` with enumerated node IDs; (3) freeze the
canonical gate bundle. **No canonical gate bundle exists yet** — §5.1 requires all executable code
to exist before the gate runs.

**Sequencing note:** `src/m8/ahmed_provenance.py`'s `_SCOPED_TREES` covers `src/**/*.py`,
`scripts/**/*.py` and `tests/**/*.py`, so **any** change under those trees invalidates a frozen gate
bundle. Several landed on 2026-07-30/31 (`src/clutter.py`, and the capture-integrity apparatus that
was subsequently removed). Nothing was invalidated because no bundle exists — but freeze Step 1b
only once you intend to stop touching those trees.

## 8. Capture stage — measured once, no longer checked

On 2026-07-30 all 8 captures were verified to have 0 dropped/zero-filled UDP packets, exact frame
alignment, no ADC sample within 68 counts of int16 full scale (peaks 3.0–4.9% FS), and a configured
I/Q convention concentrating 12.8–38.8 dB more energy in the 0.8–1.4 m gate than in its mirror
image. Frame rate could not be certified from wall clock: pooling all 8 reads 19.896 Hz with a
0.485 s residual while the homogeneous 2026-07-14+ subset reads 19.9884 Hz (+0.047 bpm bias at
80 bpm); the real argument for the rate is the sensor's crystal-derived frame timer.

**That evidence is a one-time record in `HISTORY.md` 2026-07-30, not a live check.** The tooling
that produced it — `src/capture_integrity.py`, `scripts/verify_capture_integrity.py`, and the
capture-time hooks in both capture paths — was **removed on the user's instruction 2026-07-31**.
Nothing now checks a new capture for packet loss, frame misalignment, ADC clipping, or a mirrored
I/Q convention, and no raw-ADC hash is recorded at capture time.

What that means in practice, stated plainly so the next chat is not surprised:

- **`scripts/live_demo.py` no longer writes `live_raw_mirror_hash`.** The field still exists in
  `run_metadata.json` and is always `null`. `scripts/score_offline.py`'s directory-form
  `--pinned-lock-source` hash-binds against that field (OSR-03 R2), so it will refuse every future
  live capture; use the integer form, which is tagged `kind="manual"` and may not be captioned as
  reproducing the measured methodology.
- Frame-alignment truncation of the raw mirror is **retained** in `LiveFrameSource._loop` — it is
  required for `read_adc_bin` to load the file at all, and is not a provenance feature.
- `steps/step_1/capture.py` still computes its own `sha256` over the finished `.bin` (pre-existing
  behaviour, untouched), so that path does record a capture hash. The live path does not.
- The failure modes are documented in `HISTORY.md` and remain real; they are simply unguarded.

## 9. Gotchas that will bite you

- **Line endings are pinned to LF and it is load-bearing.** Before `3aec30a`, `core.autocrlf=true`
  meant a fresh clone checked out CRLF and *every* recorded SHA-256 changed. Any script that hashes
  a text payload must write it binary or with `newline="\n"`.
- **Canonical Step 1a bundle: 2 payload hashes are CRLF-era.** Documented erratum in `HISTORY.md`.
  **Do not "fix" it by editing the digests in `provenance.json`** — that is falsifying a provenance
  record, and it was explicitly rejected.
- **Never parse `git status --porcelain` from a stripped string.** Its status column has significant
  leading spaces. Use `src/m8/ahmed_provenance.py::git_status_paths`.
- **Pin git state in any test that asserts on promotion, `reproducible`, or cleanliness.** Use
  `_pin_git_provenance` or `monkeypatch.setattr(so, "is_tree_clean", ...)`.
- Git's index caches on `(size, mtime)`; a same-length rewrite in the same second is not seen as
  modified. Change the length in fixtures.
- **Candidate domains are not shared between the synthetic and real Step 1b paths.**
- **P2/P3 require on-grid lines.** On the primary PRF grid the fundamentals are off-grid and they
  degrade to ~1e-3. Do not restate them as bit-exact.
- **Session types are not machine-recorded anywhere.** `notes/capture_inventory.md` is the source
  for the first three: massimo1 natural, **massimo2 paced 16 bpm**, sweep stepped 12→15→18→21. It
  does **not** cover massimo3–7, which the user declared natural on 2026-07-31. Verify against the
  Masimo RRp channel rather than trusting recollection — massimo2 reads a flat 16.0 with IQR 0.0,
  while massimo3–7 wander 3–10 bpm within a session. `scripts/score_offline.py` requires an explicit
  `--session-type` per capture (OSR-19) and forbids inferring it.
- **massimo2's and sweep's live locks (20, 21) are mislocks;** the corrected bin is 26 for both. A
  comparison pinned to a live lock is measuring at a known-bad bin. massimo3–7 have **no** corrected
  bin established.
- **6 of 8 captures have no raw hash in their metadata** (sweep, massimo3–7) — a join-timeout race
  that cost every capture ≥1.26 GB its hash. massimo1/massimo2/sweep have independent 2026-07-25
  hashes in `notes/capture_inventory.md` that were re-confirmed matching on 2026-07-30;
  **massimo3–7 have no independent record anywhere.** Their SHA-256s as of 2026-07-31 are recorded
  in `HISTORY.md` only. The race was fixed and the fix then removed with the rest of the hashing
  work on 2026-07-31, so future live captures also record no hash.
- **`notes/capture_inventory.md` is stale and known to be so.** It is dated 2026-07-25: it predates
  massimo3–7, still lists `20260713_170323_..._live_test1` (deleted), and its §2 lists three
  `20260715_*_replay_unknown` folders that are all gone. Its §1 hashes are correct and still match.
  An amendment was written and then reverted on 2026-07-31 with the rest of the hash-provenance
  work; `HISTORY.md` 2026-07-31 has the content if it is ever wanted back.
- `results/live_demo/` holds exactly the 8 canonical captures. m7 has duplicate/missing Masimo
  seconds — use the parser's integer-`Timestamp` dedup, never hardcoded row counts.
- The `20260715_*_replay_unknown` folders referenced by older notes are **gone**; the OSR-03 tests
  that need replay artifacts skip.
- Do not mutate `data/raw/`, `results/live_demo/`, live estimates, metadata, warmup evidence, or
  Masimo CSVs.
- Environment: plain `conda` is **not** on PATH. Use
  `& 'C:\ProgramData\anaconda3\condabin\conda.bat' run -n radar-vitals python …`, and never call the
  env's `python.exe` by absolute path (it crashes matplotlib `savefig`).

## 10. Pointers

| Purpose | Path |
|---|---|
| Project rules | `CLAUDE.md` |
| Append-only log | `HISTORY.md` |
| Milestone roadmap | `plans/implementation_plan.md` |
| Method rationale, literature, ECA+AHET spec | `notes/approach.md` |
| Frozen analysis pre-spec (evidence floor §2a/§2b) | `notes/analysis_prespec.md` |
| HR comparator pre-spec | `notes/comparator_prespec.md` |
| Capture protocol, ethics ref, scene requirement | `notes/protocol.md` |
| Draft IBEC amendment request (DRAFT — placeholders unresolved) | `notes/ethics_amendment_hr_recovery.md` |
| Capture inventory, session types, hashes | `notes/capture_inventory.md` |
| M2 respiration fix plan | `plans/m2_respiration_fix.md` |
| Step 1b authority (both required) | `plans/m8_step1b_ahmed_transfer.md` + `…_addendum_a.md` |
| Step 1b gate evidence (regenerable) | `scripts/m8_step1b_gate_prediction.py` |
| Canonical Step 1a bundle | `figures/generated/m8_ahmed_fig8/20260729T075443.145998Z_8e08f5ab0120/` |
| Production DSP | `src/respiration.py`, `src/vitals.py`, `src/window_pipeline.py` |
| Warmup selection (measured, not the bottleneck — §3) | `src/warmup_select.py` |
| **Per-bin sweep — the §3.0 result** | **`scripts/diagnose_bin_sweep.py`**, `tests/test_diagnose_bin_sweep.py` |
| Per-bin sweep evidence (gitignored, on disk) | `results/diagnose/bin_sweep/20260731T004043Z/` |
| **Signal-presence audit — the §2.1 result** | **`scripts/diagnose_signal_presence.py`**, `tests/test_diagnose_signal_presence.py` |
| Signal-presence evidence (gitignored, on disk) | `results/diagnose/signal_presence/20260731T155946Z/` |
| Warmup config knobs (`bin_selection`) | `scripts/live_demo_config.yaml` |
| Warmup selection regression check — run after any change | `scripts/validate_warmup_selection.py` |
| Per-capture warmup evidence (all 8) | `results/live_demo/*/warmup_bin_selection.json` |
| Bin-drift diagnostic + its closed cross-review | `plans/bin_drift_diagnostic.md`, `scripts/diagnose_bin_drift.py` |
| Bin-drift evidence run (gitignored, on disk) | `results/diagnose/bin_drift/20260728T004453Z/` |
| Deferred relock-tracker WIP (stale, needs porting) | `git stash@{0}` |
| Warmup / bin-selection tests | `tests/test_live_demo_warmup_helpers.py`, `tests/test_step3_bin_selection.py` |
| Static clutter removal (off by default) | `src/clutter.py` |
| Offline scorer / comparators | `scripts/score_offline.py`, `src/comparator.py` |
| Clutter A/B config pair | `experiments/exp_clutter_removal/` |
| Frozen window grid | `src/m4/window_grid.py` |
| The 8 captures | `results/live_demo/` |
