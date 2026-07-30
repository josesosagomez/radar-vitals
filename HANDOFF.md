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
this file both claimed there was; the claim was false and is corrected. See §5.

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
| A | M2 respiration-collapse fix | **fix landed**; only done-when #5 (validation) open — see §4 |
| A | M3 BR comparator pre-spec | **closed**, 48/48 findings |
| A | M4 offline evaluation harness | built (`scripts/score_offline.py`), has run for real |
| 0 | **M0 pre-registration deposit** | **HARD GATE — believed unblocked**, see §3 |
| B | M5 pilot / M6 main study | **not started**; blocked by M0 |
| C | M8 Step 1a (Ahmed reproduction) | done — **scientifically negative**, canonical bundle committed |
| C | M8 Step 1b | implemented through the gate; see §6 |
| C | M9 (Kotte) / M10 baselines | **not started** |
| D | M12 paper, figures, chapter | not started |

Active branch: **`vital_signs_ahmed_v10`**. Test baseline: **2074 passed, 5 skipped**. The 5 skips
are honest absences (4 OSR-03 tests need replay artifacts that no longer exist), not passes.

## 3. The critical path, and the one thing to decide

**M0 is the gate and is believed unblocked.** Its blocking decision was the evidence floor, frozen
by the user on 2026-07-24/25 (`notes/analysis_prespec.md` §2a/§2b). M3 is closed. What remains is
assembly plus the user's irreversible deposit act.

The ethics reference **is** recorded — approval `24IBEC051`, issuing board **IBEC, KAUST**
(`notes/protocol.md`). The only residual is confirming the formal expansion of the "IBEC" acronym
for the Methods section.

Until M0 is deposited, **no study capture may be taken** — that gate sits before M5, not merely
before M6.

**Genuinely open decision:** whether to attack **coverage (M11a) before freezing**. This is now
better informed than when it was last deferred: clutter removal was the leading candidate fix and
has been measured and rejected (§5), so one argument for delaying the freeze is gone. Coverage
remains 12% and unexplained.

## 4. Respiration collapse — the fix landed; only validation is open

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
not DSP.** M1/M5 unblock it.

A worked consequence of this fix is recorded in `HISTORY.md` 2026-07-30: it moved massimo1's warmup
lock from 23 to 27, so **offline no longer reproduces that session's live bin**.

## 5. Static clutter removal — implemented, OFF, and measured not to help

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

## 6. M8 Step 1b — current in-flight work

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
bundle. Several landed on 2026-07-30/31 (`src/capture_integrity.py`, `src/clutter.py`, the
`live_demo.py` fix). Nothing was invalidated because no bundle exists — but freeze Step 1b only once
you intend to stop touching those trees.

## 7. Capture stage — closed and gated

Verified 2026-07-30 across all 8 captures: 0 dropped/zero-filled UDP packets, exact frame alignment,
no ADC sample within 68 counts of int16 full scale (peaks 3.0–4.9% FS), and the configured I/Q
convention concentrates 12.8–38.8 dB more energy in the 0.8–1.4 m gate than in its mirror image.

`scripts/verify_capture_integrity.py` regenerates that evidence. The same checks run automatically
at capture time in **both** capture paths via `src/capture_integrity.py` (one implementation, so the
in-room verdict cannot drift from the offline one). Gating: frame alignment, packet loss, saturation,
I/Q convention. **Reported but not gating:** the scene margin.

Frame rate is **not** certifiable from wall clock. Regressing span on frame count assumes one fixed
setup overhead; pooling all 8 captures reads 19.896 Hz with a 0.485 s residual, while the
homogeneous 2026-07-14+ subset reads 19.9884 Hz (+0.047 bpm bias at 80 bpm). The script reports
INDETERMINATE rather than certifying a biased slope. The real argument for the rate is the sensor's
crystal-derived frame timer.

## 8. Gotchas that will bite you

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
- **Session types are not machine-recorded anywhere.** `notes/capture_inventory.md` is the source:
  massimo1 and massimo3–7 natural, **massimo2 paced 16 bpm**, sweep stepped 12→15→18→21. Verify
  against the Masimo RRp channel rather than trusting recollection — massimo2 reads a flat 16.0 with
  IQR 0.0. `scripts/score_offline.py` requires an explicit `--session-type` per capture (OSR-19) and
  forbids inferring it.
- **massimo2's and sweep's live locks (20, 21) are mislocks;** the corrected bin is 26 for both. A
  comparison pinned to a live lock is measuring at a known-bad bin.
- **6 of 8 captures have no contemporaneous raw hash** (sweep, massimo3–7). The cause was fixed in
  `cee8644`, but not retroactively. massimo1/massimo2/sweep have independent 2026-07-25 hashes in
  `notes/capture_inventory.md` that still match; **massimo3–7 have no independent record at all**,
  and their hashes were computed 2026-07-31, days after capture. Disclose that if they are cited.
- `results/live_demo/` holds exactly the 8 canonical captures. m7 has duplicate/missing Masimo
  seconds — use the parser's integer-`Timestamp` dedup, never hardcoded row counts.
- The `20260715_*_replay_unknown` folders referenced by older notes are **gone**; the OSR-03 tests
  that need replay artifacts skip.
- Do not mutate `data/raw/`, `results/live_demo/`, live estimates, metadata, warmup evidence, or
  Masimo CSVs.
- Environment: plain `conda` is **not** on PATH. Use
  `& 'C:\ProgramData\anaconda3\condabin\conda.bat' run -n radar-vitals python …`, and never call the
  env's `python.exe` by absolute path (it crashes matplotlib `savefig`).

## 9. Pointers

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
| Static clutter removal (off by default) | `src/clutter.py` |
| Capture acceptance gate (shared core) | `src/capture_integrity.py` |
| Capture integrity verifier | `scripts/verify_capture_integrity.py` |
| Warmup selection regression check | `scripts/validate_warmup_selection.py` |
| Offline scorer / comparators | `scripts/score_offline.py`, `src/comparator.py` |
| Clutter A/B config pair | `experiments/exp_clutter_removal/` |
| Frozen window grid | `src/m4/window_grid.py` |
| The 8 captures | `results/live_demo/` |
