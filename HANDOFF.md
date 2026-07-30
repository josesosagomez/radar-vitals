# Handoff — resume here

> Read this and `CLAUDE.md` before doing anything. State verified 2026-07-31.
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
results. That is the active work area; see §3.

## 2. Where the project actually is

**Infrastructure is strong; the science is thin.** That gap is the single most important thing to
understand before planning work.

- **BR end-to-end accuracy has never been measured. Not once.**
- HR agreement exists only as single-subject numbers stamped `exploratory_non_frozen`.
- 8 captures exist, **all one subject**, all pre-freeze exploratory.
- **HR coverage is 12%** pooled over those 8 captures (8 of 67 admissible windows, run
  `20260730T204448Z`). Three captures — massimo3, massimo5, massimo7 — score **0%**. Coverage, not
  accuracy, is the bottleneck.
- The 10-subject × 2-session study (M6) has not started, and neither has the pilot (M5).

| Track | Milestone | Status |
|---|---|---|
| A | M1 live smoke test | **not run** — cheapest risk reduction available |
| A | M2 respiration-collapse fix | **fix landed**; only done-when #5 (validation) open — see §5 |
| A | M3 BR comparator pre-spec | **closed**, 48/48 findings |
| A | M4 offline evaluation harness | built (`scripts/score_offline.py`), has run for real |
| 0 | **M0 pre-registration deposit** | **POSTPONED 2026-07-31 by user decision; gate STANDS** — see §4 |
| B | M5 pilot / M6 main study | **not started; blocked by M0 and staying blocked** |
| C | M8 Step 1a (Ahmed reproduction) | done — **scientifically negative**, canonical bundle committed |
| C | M8 Step 1b | implemented through the gate; see §7 |
| C | M9 (Kotte) / M10 baselines | **not started** |
| D | M12 paper, figures, chapter | not started |

Active branch: **`vital_signs_ahmed_v10`**. Test baseline: **2049 passed, 5 skipped** (verified
2026-07-31). The 5 skips are honest absences (4 OSR-03 tests need replay artifacts that no longer
exist), not passes.

## 3. ACTIVE TASK — warmup range-bin selection

**This is what the next session is for.** Read §3 fully before touching `src/warmup_select.py`;
there is more prior art here than is obvious, including a deferred decision that must not be
silently reversed.

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

### 3.2 Why this is worth working on

Coverage is 12% pooled and is the bottleneck (§2). Warmup picks the single bin every downstream
estimate depends on, and **three captures score 0% coverage in both A/B arms** (massimo3, massimo5,
massimo7). Whether that is a bad lock or genuinely no cardiac signal at any in-gate bin is
**unknown and is the first question worth answering** — it is cheap to test by scoring those three
at every candidate bin instead of the locked one.

### 3.3 Known-suspicious observations (evidence, not verdicts)

- **massimo7 locked bin 32** = the gate *edge* (1.40 m), while its strongest in-gate energy is at
  bin 24 (1.05 m). That bin is clutter-dominated (+10.6 dB static-to-moving) with a phase
  peak-to-peak of 1.3 rad against 18–40 rad for signal-dominated bins. Unexplained.
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

## 4. The critical path — M0 postponed, and Track B is parked behind it

**Decision, user, 2026-07-31: nothing will be deposited for now, and the gate stands.**

Read that precisely, because the two halves are independent and both matter:

- **Postponed, not abandoned.** M0 is not blocked on anything — the evidence floor was frozen
  2026-07-24/25 (`notes/analysis_prespec.md` §2a/§2b), M3 is closed, the ethics reference is
  recorded (approval `24IBEC051`, board **IBEC, KAUST**, in `notes/protocol.md`; only the formal
  expansion of the "IBEC" acronym is outstanding, for Methods). What remains is assembly plus the
  user's irreversible deposit act, whenever they choose to do it.
- **The gate still holds.** Until M0 is deposited, **no study capture may be taken** — that gate
  sits before M5, not merely before M6. Postponing the deposit therefore postpones the study.
  **Do not take M5 or M6 captures. Do not propose starting them as "the obvious next step".**

**So Track B is parked, and the forward work is elsewhere:**

| available now | why |
|---|---|
| **M1 live smoke test** | not a study session. `notes/protocol.md` distinguishes method-development captures from study sessions (it labels the stepped sweep exactly that way), so the M0 gate does not cover it. Still the cheapest risk reduction available. |
| **M8 Step 1b** | entirely synthetic; has never opened a real capture. See §6. |
| offline work on the 8 existing captures | they are pre-freeze exploratory already; re-scoring them changes nothing about their status. |

**Still open, and no longer urgent:** whether to attack **coverage (M11a)**. It used to be framed as
"before freezing"; with the freeze postponed indefinitely that framing is gone, and coverage work is
simply available whenever wanted. It is better informed than when last deferred — clutter removal
was the leading candidate fix and has been measured and rejected (§6). Coverage remains 12% and
unexplained.

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

**Open: M2 done-when #5** — score reprocessed BR under the frozen M3 comparator. **Blocked on data,
not DSP:** approximate time alignment cannot produce a frozen-comparator outcome, and none of the 8
existing captures can discharge it. With M5 parked behind the postponed M0 deposit (§4), **M1 is now
the only route** — and only if that smoke test carries a Masimo reference and the clock sync of
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
| Capture inventory, session types, hashes | `notes/capture_inventory.md` |
| M2 respiration fix plan | `plans/m2_respiration_fix.md` |
| Step 1b authority (both required) | `plans/m8_step1b_ahmed_transfer.md` + `…_addendum_a.md` |
| Step 1b gate evidence (regenerable) | `scripts/m8_step1b_gate_prediction.py` |
| Canonical Step 1a bundle | `figures/generated/m8_ahmed_fig8/20260729T075443.145998Z_8e08f5ab0120/` |
| Production DSP | `src/respiration.py`, `src/vitals.py`, `src/window_pipeline.py` |
| **Warmup selection (ACTIVE — §3)** | **`src/warmup_select.py`** |
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
