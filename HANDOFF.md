# Handoff — resume here

> Read this and `CLAUDE.md` before doing anything. State verified 2026-07-30.
> `HISTORY.md` is the append-only log; this file is the always-current summary.

## 1. Project snapshot

Estimate heart rate and breathing rate from a TI IWR1642BOOST + DCA1000 FMCW radar while a subject
sits 0.8–1.4 m away, and quantify agreement against a Masimo MightySat fingertip pulse oximeter.
HR truth is the Masimo `Beats / min` column; BR reference is `Breaths / min`; both align on the
integer Unix-epoch `Timestamp`. Target output is a journal paper plus a thesis chapter.

**Method in one line:** range FFT → static clutter removal → warmup-locked chest range bin → phase
extraction (`delta_before_mean`) → for BR, a fused FFT/HA/STFT estimate over 0.10–0.50 Hz; for HR,
**ECA** (project out the respiration-harmonic subspace) → argmax in 0.8–2.0 Hz → **AHET**
second-harmonic verification, which returns **NaN rather than a guess** when it fails. 30 s
non-overlapping windows at 20 Hz.

## 2. Where the project actually is

**Infrastructure is strong; the science is thin.** That gap is the single most important thing to
understand before planning work.

- **BR end-to-end accuracy has never been measured. Not once.**
- HR agreement exists only as n≤2-per-bucket, single-subject numbers stamped
  `exploratory_non_frozen`. The old pilot MAEs (0.19/0.50/0.53) were **retired** as an anchor, and a
  "MAE 0.16 bpm" claim was withdrawn earlier still.
- 8 captures exist, **all one subject**, all classified pre-freeze exploratory.
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
| C | M8 Step 1b | implemented through the gate; see §5 |
| C | M9 (Kotte) / M10 baselines | **not started** |
| D | M12 paper, figures, chapter | not started |

Test baseline: **2028 passed, 5 skipped**. The 5 skips are honest absences (4 OSR-03 tests need
replay artifacts that no longer exist), not passes.

## 3. The critical path, and the one thing to decide

**M0 is the gate and is believed unblocked.** Its only blocking decision was the evidence floor,
which the user froze on 2026-07-24/25 (`notes/analysis_prespec.md` §2a/§2b: ≥1 evaluable window per
session, ≥4 per subject, LoA CI half-width ≤ 5 bpm, plus a miss rule; BR has **no** confirmatory
floor and is reported descriptively). M3 is closed. What remains is assembly + the user's
irreversible deposit act, plus recording the **ethics approval reference number and issuing board**,
which is still unwritten and needed for Methods.

Until M0 is deposited, **no study capture may be taken** — that gate sits before M5, not merely
before M6.

**Genuinely open decision** (deferred alongside the floor, never resolved): whether to attack
**coverage (M11a) before freezing**, so a better estimator can be the pre-registered primary rather
than a post-hoc footnote. Coverage is 10–46% and is the acknowledged real bottleneck — accuracy is
not. Freezing first locks the current estimator in as primary. This deserves an explicit decision.

## 4. Respiration collapse — the fix landed; only validation is open

Do not re-open the fix. The state is easy to misread, and an earlier version of this file did.

**What happened:** the BR search band is `[0.10, 0.50]` Hz, so 6 bpm is the *lowest bin in the
search*. Body motion (phase peak-to-peak 13.8 → 29–32 rad, ~9 mm, `massimo1` t≈144 s) filled the
band with low-frequency drift, the argmax slid to the wall and stayed there for 12 hops while
Masimo read 19 bpm — and `resp_valid` remained `1` the whole time. Observed 3-for-3 on the
Masimo-referenced captures (`massimo1` 12 hops, `massimo2` 5, `sweep` 10), absent from the
unreferenced `live_test1`.

**It also silently disables HR, which is not obvious.** `f_r` feeds ECA:

```
f_r = 0.1 Hz  →  k_max_eff = min(k_max_cap=10, floor(2.0/0.1)=20) = 10
harmonics at 0.1 … 1.0 Hz
  k=8,9,10 → 0.8, 0.9, 1.0 Hz → inside cardiac band → skipped as "forbidden"
  k=1..7   → 0.1–0.7 Hz       → below the band → cancelling them changes nothing there
⇒ ECA removes 0.00 dB in-band; HR degrades to a bare argmax on an uncancelled spectrum
```

So "respiration collapse" and "ECA is inert at low f_r" are **one causal chain**, not two defects.

**Fixed** in `src/respiration.py`: band-edge veto by *bin identity* (`resp_edge_veto`) — frequency
arithmetic is wrong when the band edge is not bin-aligned — plus STFT-consistency gates on every
STFT-dependent branch. Accepted, tested cost: a genuine ~6 bpm breather on the edge bin is
permanently invalid.

**Open: M2 done-when #5** — score reprocessed BR under the frozen M3 comparator. **Blocked on data,
not DSP.** Approximate time alignment cannot produce a frozen-comparator outcome, and no capture
that can discharge it exists yet. M1/M5 unblock it.

## 5. M8 Step 1b — current in-flight work

Authority is the **pair** of files; the addendum wins on conflict.

| File | SHA-256 | Status |
|---|---|---|
| `plans/m8_step1b_ahmed_transfer.md` | `9294cb0589b9f0d8f50cdfa0ea893862b1f8ac7f26eb6fee31ee57d622da33ac` | five-discipline PASS on these exact bytes; **unmodified** |
| `plans/m8_step1b_ahmed_transfer_addendum_a.md` | `b8625f6e1e33aa4034591c30f528887910f049fbde75e78627d6d7cadd02fed0` | user-approved 2026-07-30; cross-model re-review **waived** |

Built and tested — **no real capture or Masimo file has ever been opened by this work**:

| Module | Purpose |
|---|---|
| `src/m4/outcome.py` | AHET classifier, extracted verbatim; 3 callers share one function object |
| `src/m4/estimator_suite.py` | neutral arm/result/suite contracts; immutable evidence |
| `src/m4/production_suite.py` | `ProductionEstimatorSuite` + the sole `eca_bindrift_outcome_v1` adapter |
| `src/m8/ahmed_transfer.py` | `estimate_phase_ha` core + `AhmedPhaseEstimatorSuite` (6 arms) |
| `src/m8/ahmed_synthetic.py` | the §2.2 synthetic control and extraction oracles |
| `src/m8/ahmed_gate.py` | `evaluate_gate` — P1–P4 checks + per-domain transfer verdict |
| `src/m4/bundle.py` | immutable staged bundles, acyclic manifests, fail-closed `LATEST.json` |
| `src/m8/ahmed_provenance.py` | scoped source manifest, promotion eligibility, env attestation |
| `src/m4/capture_registry.py` | **structural** radar/reference isolation |
| `src/m4/estimator_scoring.py` | §3.5 neutral metrics from persisted rows |
| `src/m4/estimator_runner.py` | preflight (before *any* capture access) + Cartesian ledger |
| `scripts/m8_ahmed_transfer.py` | `synthetic` command; real-data commands refuse with an explanation |

**Gate passes in-process, 14/14 checks.** Transfer verdicts are exactly as predeclared:

| Domain | Verdict | Heart selection |
|---|---|---|
| `collision_domain_from_fb` | `not_transferred_under_declared_assumptions` | ~20 bpm — the breathing bin |
| `real_representative_domain` | `transferred_under_declared_seed_and_configuration` | 80.04 bpm |

**Remaining, in order:** (1) a synthetic capture fixture + the runner's decode/dispatch loop (base
plan §4.2 steps 2–7) + the strict production serializer; (2) `test_attestation.json` with enumerated
node IDs; (3) freeze the canonical gate bundle. **No canonical gate bundle exists yet** — §5.1
requires *all* executable code to exist before the gate runs, so freezing early guarantees its own
invalidation. Real-data access needs a separate frozen authorization after that.

Residual risk: cross-model re-review of Addendum A was waived. One internal contradiction was caught
only by re-reading. The four unasked reviewer questions are in addendum §A8.

## 6. Gotchas that will bite you

- **Line endings are pinned to LF and it is load-bearing.** Before `3aec30a`, `core.autocrlf=true`
  meant a fresh clone checked out CRLF and *every* recorded SHA-256 changed. Do not remove
  `.gitattributes` without re-deriving every recorded hash.
- **Any script that hashes a text payload must write it binary or with `newline="\n"`**, so the
  bytes it hashes are the bytes that persist. Otherwise you get the Step 1a erratum below.
- **Canonical Step 1a bundle: 2 payload hashes are CRLF-era.** Resolved as a documented erratum
  (see `HISTORY.md`, `ERRATUM: canonical Step 1a bundle payload hashes`). Bundle deliberately
  unmodified per §6.1. **Do not "fix" it by editing the digests in `provenance.json`** — that is
  falsifying a provenance record, and it was explicitly rejected.
- **Never parse `git status --porcelain` from a stripped string.** Its status column has significant
  leading spaces; stripping then slicing `[3:]` truncates paths and makes a dirty tree look clean —
  a fail-open bug that defeated the promotion check until caught. Use
  `src/m8/ahmed_provenance.py::git_status_paths`.
- **Pin git state in any test that asserts on promotion, `reproducible`, or cleanliness.** Three
  ambient-Git test defects were found and fixed this session. Use `_pin_git_provenance` or
  `monkeypatch.setattr(so, "is_tree_clean", ...)`.
- Git's index caches on `(size, mtime)`; a same-length rewrite in the same second is not seen as
  modified. Change the length in fixtures.
- **Candidate domains are not shared between the synthetic and real Step 1b paths.** Applying the
  wrong one silently changes the answer — this exact mistake was made and caught.
- **P2/P3 require on-grid lines.** They are identities about which bins a harmonic row lands on. On
  the primary PRF grid the fundamentals are off-grid and they degrade to ~1e-3. Do not restate them
  as bit-exact — an earlier revision did, from rounded output, and it was wrong.
- The two replay directories (`20260726_173434_replay_unknown`, `20260727_182319_replay_unknown`)
  are **gone**; the OSR-03 tests that need them skip. Do not assume they are exercising anything.
- `results/live_demo/` holds exactly the 8 canonical captures. m7 has duplicate/missing Masimo
  seconds — use the parser's integer-`Timestamp` dedup, never hardcoded row counts.
- Do not mutate `data/raw/`, `results/live_demo/`, live estimates, metadata, warmup evidence, or
  Masimo CSVs.
- Environment: plain `conda` is **not** on PATH. Use
  `& 'C:\ProgramData\anaconda3\condabin\conda.bat' run -n radar-vitals python …`, and never call the
  env's `python.exe` by absolute path (it crashes matplotlib `savefig`).

## 7. Pointers

| Purpose | Path |
|---|---|
| Project rules | `CLAUDE.md` |
| Append-only log | `HISTORY.md` |
| Milestone roadmap (refreshed 2026-07-30) | `plans/implementation_plan.md` |
| Method rationale, literature, ECA+AHET spec | `notes/approach.md` |
| Frozen analysis pre-spec (evidence floor §2a/§2b) | `notes/analysis_prespec.md` |
| HR comparator pre-spec | `notes/comparator_prespec.md` |
| Capture protocol | `notes/protocol.md` |
| M2 respiration fix plan | `plans/m2_respiration_fix.md` |
| Step 1b authority (both required) | `plans/m8_step1b_ahmed_transfer.md` + `…_addendum_a.md` |
| Step 1b gate evidence (regenerable) | `scripts/m8_step1b_gate_prediction.py` |
| Canonical Step 1a bundle | `figures/generated/m8_ahmed_fig8/20260729T075443.145998Z_8e08f5ab0120/` |
| Production DSP | `src/respiration.py`, `src/vitals.py`, `src/window_pipeline.py` |
| Offline scorer / comparators | `scripts/score_offline.py`, `src/comparator.py` |
| Frozen window grid | `src/m4/window_grid.py` |
| The 8 captures | `results/live_demo/` |
