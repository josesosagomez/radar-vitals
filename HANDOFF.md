# Handoff — M8 Step 1b approved; fixture-testable implementation is the next task

> Read this and `CLAUDE.md` before doing anything. State verified on 2026-07-30.
> `HISTORY.md` is the append-only record; this file is the current resume point.

## 1. Project snapshot

This project estimates heart rate and breathing rate from TI IWR1642BOOST/DCA1000 FMCW radar
captures and compares offline estimates with a Masimo MightySat. Heart-rate ground truth is the
Masimo `Beats / min` pulse-rate column; breathing reference is `Breaths / min`. Both are aligned
through the integer Unix-epoch `Timestamp`, subject to the reference comparators. Raw inputs and
saved live captures are read-only scientific inputs. The research bar is reproducible evidence,
not a visually plausible live value.

The active milestone is M8 Step 1b: test whether Ahmed et al.'s fixed-\(H\) harmonic accumulation
transfers from the Step 1a pulse-radar simulator to this project's extracted FMCW phase, then—only
after the synthetic gate—run a strictly exploratory comparison on all eight saved captures.

## 2. Current state

### Repository

- Branch: `vital_signs_ahmed_v10`
- Worktree clean. Last **code** commit is `e00d859`; anything after it on this branch is
  documentation only. (An exact HEAD SHA is not recorded here: a SHA written into this file is
  always one commit stale by the time the file is committed, which makes it a lie rather than a
  reference. Run `git log --oneline` for the true head.)
- 15 commits on 2026-07-30 since `1bad25c`. The load-bearing ones:
  - `2bfc167` deterministic Step 1a provenance + scorer OSR-03 tests
  - `f9e42b6` approved plan, Addendum A, gate-prediction evidence script
  - `3aec30a` `.gitattributes` LF pin — recorded hashes were not reproducible
  - `833bc6e` pin git state in the scorer end-to-end test
  - `53cf4c7` extract the AHET classifier into `src/m4/outcome.py`
  - `574657a` scientific core, suite contracts, corrected P2/P3
  - `025d259` `ProductionEstimatorSuite` and `AhmedPhaseEstimatorSuite`
  - `0e34078` synthetic generator and gate evaluation
  - `10e0711` bundle writer, provenance, synthetic CLI
  - `468b57a` capture registry with radar/reference isolation, neutral metrics
  - `e00d859` runner preflight and Cartesian completeness ledger
- Existing estimator/scorer behaviour is unchanged. The only edits to shipped code are the
  `src/m4/outcome.py` extraction (verified byte-for-byte identical to the original block) and its
  two import sites; everything else is new modules and tests.
- **Test baseline: the full suite is green** — `2028 passed, 5 skipped, 0 failed`
  (was `3 failed, 1822 passed, 2 skipped` at the start of 2026-07-30).
- **Line endings are pinned to LF and this is load-bearing.** Before `3aec30a`, `core.autocrlf=true`
  with no `.gitattributes` meant a fresh clone checked out CRLF and every recorded SHA-256 changed
  (the base plan hashed `fa64b234…` instead of `9294cb05…`). Do not remove `.gitattributes` without
  re-deriving every recorded hash.

### Step 1a

Step 1a is implemented, committed, canonicalized, and scientifically negative under its declared
assumptions:

- canonical bundle:
  `figures/generated/m8_ahmed_fig8/20260729T075443.145998Z_8e08f5ab0120/`
- status: `not_reproduced_under_declared_assumptions`
- selected breathing: about 20.0072 bpm for H=3 and H=5 (target 20)
- selected heart: about 40.0144 bpm for H=3 and 20.0072 bpm for H=5 (target 80)

Step 1a never decoded or scored a real capture. Its adapter test proves record normalization only.

### Step 1b authority — two files, both required

| File | SHA-256 | Status |
|---|---|---|
| `plans/m8_step1b_ahmed_transfer.md` | `9294cb0589b9f0d8f50cdfa0ea893862b1f8ac7f26eb6fee31ee57d622da33ac` | five-discipline PASS on these exact bytes |
| `plans/m8_step1b_ahmed_transfer_addendum_a.md` | `b8625f6e1e33aa4034591c30f528887910f049fbde75e78627d6d7cadd02fed0` | **user-approved 2026-07-30**; cross-model re-review **waived** |

The base plan is deliberately **unmodified**, so its five acceptances remain valid. Governing
authority is the *pair*; where they conflict, the addendum wins. Any manifest binding
`approved_plan_sha256` must bind both.

### Step 1b implementation progress

Built and tested (no real capture or Masimo file has been opened):

| Module | Purpose |
|---|---|
| `src/m4/outcome.py` | AHET classifier, extracted verbatim; three callers share one function object |
| `src/m4/estimator_suite.py` | neutral arm/result/suite contracts; immutable evidence; config bound at construction |
| `src/m4/production_suite.py` | `ProductionEstimatorSuite` + the sole `eca_bindrift_outcome_v1` adapter |
| `src/m8/ahmed_transfer.py` | `estimate_phase_ha` core + `AhmedPhaseEstimatorSuite` (six arms) |
| `src/m8/ahmed_synthetic.py` | the §2.2 synthetic control, extraction oracles, phase-slip audit |
| `src/m8/ahmed_gate.py` | `evaluate_gate` — P1–P4 checks and the per-domain transfer verdict |
| `src/m4/bundle.py` | immutable staged bundles, acyclic manifests, fail-closed `LATEST.json` |
| `src/m8/ahmed_provenance.py` | scoped source manifest, git state, promotion eligibility, env attestation |
| `scripts/m8_ahmed_transfer.py` | `synthetic` command; real-data commands refuse with an explanation |
| `src/m4/capture_registry.py` | registry loader; **structural** radar/reference isolation |
| `src/m4/estimator_scoring.py` | §3.5 neutral metrics from persisted rows; never decodes or estimates |
| `src/m4/estimator_runner.py` | preflight (before any capture access) + Cartesian completeness ledger |
| `experiments/m8_ahmed_transfer/capture_registry.yaml` | the eight captures, transcribed from §3.3 |

**The gate passes in-process** (`evaluate_gate()` -> `passed`, 14/14 checks), and the transfer
verdicts are exactly as predeclared:

| Domain | Transfer verdict | Heart selection |
|---|---|---|
| `collision_domain_from_fb` | `not_transferred_under_declared_assumptions` | ~20 bpm (the breathing bin) |
| `real_representative_domain` | `transferred_under_declared_seed_and_configuration` | 80.04 bpm |

Verified end to end into a temporary directory: on a dirty tree the bundle writes and is labelled
`INELIGIBLE` with the offending paths listed; on a clean tree the same run reports
`promotion: eligible` and publishes `LATEST.json`.

**`src/m4/estimator_runner.py` is deliberately partial.** It has preflight and the completeness
ledger but **not** base plan §4.2 steps 2–7: stream-hash/geometry check, decode-exactly-once, frozen
span construction, both-lock resolution, read-only slice sharing between suites, post-run mutation
assertions, and radar artifact persistence. Those need either real data or a synthetic capture
fixture; writing them untested was the alternative, so they were left out. Build a small synthetic
capture (a few hundred frames at the registry geometry) and implement against it — §6.3 wants
portable fixtures as the default.

Also not yet built: the strict production serializer (`production_native_index.json` +
`production_evidence.npz`) and `test_attestation.json`.

**No canonical gate bundle has been frozen, and this is deliberate.** Base plan §5.1 requires *all*
executable scientific, runner, scorer, serializer, CLI, and fixture-test code to be implemented and
passing **before** the gate runs, with nothing added afterwards. The runner and scorer do not exist
yet, so freezing now would guarantee the gate's own invalidation. `results/m8_ahmed_transfer/` does
not exist. No real path may be touched.

### Why Addendum A exists

Independent review of the base plan found its synthetic gate fails, but for a cause that cannot
occur on the real path it gates. Evidence is regenerable:

```powershell
& 'C:\ProgramData\anaconda3\condabin\conda.bat' run -n radar-vitals `
  python scripts/m8_step1b_gate_prediction.py
```

Identical signal, grid, and accumulator; only the heart candidate domain differs:

| Heart candidate domain | H=3 | H=5 | Target |
|---|---|---|---|
| `[f_b, 100/60]` Hz — base plan §2.3 synthetic | 20.011 bpm FAIL | 20.011 bpm FAIL | 80 |
| `[0.80, 2.00]` Hz — base plan §2.3 real | 80.042 bpm PASS | 80.042 bpm PASS | 80 |

The synthetic domain starts at \(f_b\), admitting the breathing fundamental as a heart candidate;
\(\beta_b/\beta_h = d_b/d_h = 2\) exactly, so it outscores the true heart bin. The real band excludes
it. Two further findings: the synthetic `n_fft=4096` pad is leakage-dominated (BR selects 6.578 /
4.933 bpm padded vs 20.011 bpm native), and the accumulator is degenerate under
\(q \to q/m\) for integer divisors \(m\) — score(\(f_h\)) equals score(\(f_h/2\)), non-divisor
candidates carry no signal, and the collision ratio is 2 (H=3) and 3 (H=5). These are exact in
exact arithmetic and hold to ~1e-15 relative in float64; see addendum §A4.1 for the measured values
and the gate tolerances. Do not restate them as bit-exact — an earlier revision did, from rounded
output, and it was wrong.

## 3. Active task and next steps

**No decisions are open.** On 2026-07-30 the user approved Addendum A, **waived** cross-model
re-review, and resolved the gate criterion (§A9): **the gate is reproduction of predictions P1–P4
across both heart domains**, not truth recovery on either one. P1–P4 include the predicted
*failures*, so the gate cannot be made to pass by choosing a favourable candidate domain — that is
what makes it immune to the tuning-to-pass objection §A2/§A3 would otherwise attract.

Residual risk to carry forward: re-review was waived, and one internal contradiction (§A3 vs §A4.2,
now fixed) was caught only by re-reading. The four unasked reviewer questions are listed in
addendum §A8. Treat the addendum as unreviewed by the other model family.

**No user decisions are outstanding.** The canonical Step 1a bundle's two stale payload hashes were
resolved on 2026-07-30: the bundle is left byte-for-byte as committed (§6.1) and a dated erratum in
`HISTORY.md` records both the recorded and the correct digests. See §5 and addendum §A6b.

Next steps, in order:

1. ~~Commit the plans and evidence script.~~ **Done** — `f9e42b6`.
2. ~~Fix the ambient-Git Step 1a artifact test.~~ **Done** — `2bfc167`. Focused baseline is now
   **90 passed** (prior 85 + 5 new provenance tests), deterministic, with no product-code change.
3. ~~Extract `src/m4/outcome.py`.~~ **Done** — `53cf4c7`.
4. ~~Implement the scientific core and suite contracts.~~ **Done** — `574657a`.
5. ~~Implement both concrete suites.~~ **Done** — `025d259`.
6. ~~Implement the synthetic generator and gate evaluation.~~ **Done** — `0e34078`.
7. ~~Implement the bundle writer, provenance, and `synthetic` CLI.~~ **Done** — `10e0711`.
8. ~~Capture registry, neutral metrics, runner preflight and ledger.~~ **Done** — `468b57a`,
   `e00d859`.
9. Build a synthetic capture fixture and implement the runner's decode/dispatch loop (§4.2
   steps 2–7) plus the strict production serializer against it. **← current task**
10. Add `test_attestation.json` (exact ordered pytest node IDs and counts, not a bare number — the
    plan's "85-test set" was never enumerated) and run the focused, affected, new, and broad
    fixture-only suites.
10. Execute the synthetic gate as a **frozen bundle**. It already passes in-process; what remains is
    writing it immutably with its attestations so it can parent a real stage.
11. Stop after the gate. No real path may be touched unless the gate is complete,
    `promotion_eligible=true`, and one comprehensive real-evaluation authorization is frozen before
    first access.
12. If authorized, run the immutable `real-smoke -> radar -> scored` chain exactly as registered.

Planning approval and real-data authorization remain separate decisions at different stages.

## 4. Recent decisions that must not be reversed silently

### Amended by Addendum A

- **Transform length:** native `n_fft = len(phase)` in *both* synthetic and real paths. The
  4096-padded configuration survives only as the named non-gating `zero_padded_4096_audit`.
- **Heart candidate domains:** both retained and both reported —
  `collision_domain_from_fb` = `[f_b, 100/60]` Hz and
  `real_representative_domain` = `[0.80, 2.00]` Hz. **Neither gates.** Neither may be dropped after
  real results are seen, and the collision result gets equal prominence everywhere.
- **Two verdicts:** the gate verdict — the *sole* control on real-data access — requires base plan
  §2.4 correctness plus exact reproduction of predeclared P1–P4 across **both** domains, failures
  included. Transfer verdicts (truth recovery, per domain) are reported and non-gating. Base plan
  §5.1's continuation machinery binds the gate verdict.
- **`simulate_eq14` is not a displacement source** — it models a real received signal
  (`A*cos(eta*sin(2*pi*f*t)+theta0)`), so it cannot feed `extract_chest_phase`. A harmonically rich
  displacement control needs a separately sourced, preregistered future experiment.

### Unchanged from the base plan

- Primary synthetic ID `phase_fundamentals_only_transfer_v1`; ideal phase extraction leaves the two
  displacement fundamentals; "all-harmonic" means accumulation at \(q,2q,\ldots,Hq\).
- Step 1b uses \(q=f\) and `bpm=60*q`, not Step 1a's \(q=2f\), `bpm=30*q`.
- Strict support \(Hq<f_s/2\); equality is Nyquist-degenerate. At 20 Hz, H=5 supports 100 bpm; the
  120 bpm endpoint is excluded and 118 bpm is the largest eligible real candidate.
- Reuse Step 1a's accumulator, normalization, tie behavior, and three suppression interpretations.
  Do not call production `ha_estimate_rr`.
- Only real mapping: unchanged `extract_chest_phase(..., method="delta_before_mean")`, with no extra
  detrending, windowing, resampling, or static/DC cleaning.
- Six Ahmed arms: H=3/5 × `figure_visible_unsuppressed`, `eq26_multiples_suppressed`,
  `prose_low_or_equal_suppressed`. Suppression uses the same-H Ahmed breathing bin, never truth or
  production BR. On real data the prose profile is a structural duplicate, not corroborating.
- Report `recorded_lock_as_captured` and `current_production_rerun_lock` separately; never pool.
- Offline grid is consecutive non-overlapping 600-frame/30 s windows from `k=0`; 128 complete
  windows total, `evaluation_k_ge_1` (120) is the only comparative accuracy universe.
- `start_wall_utc_approximate_v1` is explicitly non-frozen; every reference-derived output must say
  `exploratory_non_frozen`, approximate origin, and not promotion-eligible. No agreement-optimized
  shift.
- All eight captures are one-subject development/apparent data; none is a holdout. Summaries are per
  capture and protocol-stratified, never an all-protocol headline.
- Radar-only and Masimo scoring are separate immutable parent-linked stages. No Ahmed arm is promoted
  to production by Step 1b.

## 5. Gotchas and landmines

- Verify both plan hashes before approval or implementation:

  ```powershell
  (Get-FileHash plans/m8_step1b_ahmed_transfer.md -Algorithm SHA256).Hash.ToLower()
  (Get-FileHash plans/m8_step1b_ahmed_transfer_addendum_a.md -Algorithm SHA256).Hash.ToLower()
  ```

- **Step 1a provenance tests are now deterministic.** They monkeypatch
  `figures/reproduce_ahmed_fig8.py::_git_text` via `_pin_git_provenance`, so promotion follows
  injected state, not the ambient worktree. If you add a test that calls `figure_script.execute()`
  **and** asserts anything about `canonical_promoted`, pin provenance or it will flip with whatever
  is uncommitted. Base plan §6.1 and the first 2026-07-30 `HISTORY.md` entry still record the old
  inverted observation; Addendum A §A5 corrects it.
- **Canonical Step 1a bundle: two payload hashes are stale — RESOLVED as a documented erratum.**
  In `figures/generated/m8_ahmed_fig8/20260729T075443.145998Z_8e08f5ab0120/provenance.json`,
  `metrics_sha256` and `resolved_config_sha256` do not match their files: the runner hashed CRLF
  bytes that Git stored as LF, so the bundle verified on Windows only and never on Linux. The LF pin
  exposed this; it did not cause it. **The figures, plan, implementation module, and runner script
  all still verify, and no scientific content changed** — only line endings.
  User decision 2026-07-30: leave the bundle byte-for-byte as committed (§6.1) and publish an
  erratum. **See the `ERRATUM: canonical Step 1a bundle payload hashes` entry in `HISTORY.md`** for
  both the recorded and the correct digests. Do **not** "fix" this by editing the digests inside
  `provenance.json` — that is falsifying a provenance record, and it was explicitly rejected.
  Does not block Step 1b.
- **Any script that hashes a text payload must write it in binary or with `newline="\n"`**, so the
  bytes it hashes are the bytes that persist. The erratum above is exactly what happens otherwise.
  `src/m4/bundle.py` hashes the same `bytes` object it writes.
- **Three ambient-Git test defects were found and fixed this session** — the Step 1a artifact test,
  the scorer OSR-03 tests, and the scorer end-to-end test. When writing any test that asserts on
  promotion, `reproducible`, or git cleanliness, **pin the state** (`_pin_git_provenance`, or
  `monkeypatch.setattr(so, "is_tree_clean", ...)`). Do not let it read the ambient worktree.
- **Never parse `git status --porcelain` with a stripped string.** Its status column carries
  significant leading spaces; stripping them and slicing `[3:]` truncates every path by two
  characters and makes a dirty tree look clean — a fail-open bug that defeated the promotion check
  until it was caught. Use `src/m8/ahmed_provenance.py::git_status_paths`, which uses `-z` and
  `--untracked-files=all`.
- **Git's index caches on `(size, mtime)`.** A test that rewrites a file with same-length content in
  the same second will not be seen as modified. Change the length.
- The Ahmed PDF is copyrighted and gitignored, so it is hashed in `reference_entries` and does
  **not** gate promotion. Do not "fix" this by adding it to the gating set — that deadlocks the gate
  permanently.
- **The two replay directories are gone for good.** `results/live_demo/` holds exactly the eight
  canonical captures; `20260726_173434_replay_unknown` and `20260727_182319_replay_unknown` were
  transient 2026-07-26/27 bin-drift artifacts and `results/` blobs are gitignored. The
  `test_score_offline.py` OSR-03 tests that depend on them now skip via `_REQUIRE_REPLAY_PROD`
  rather than fail, and portable equivalents cover the same branches. Do not assume the real-data
  OSR-03 tests are exercising anything — they are skipped until those artifacts are regenerated.
- The "85-test Step 1a/adapter set" was never enumerated in the plan; it is
  `tests/test_m8_ahmed_fig8.py` (33 → now 38) plus `tests/test_window_pipeline_adapter.py`, and the
  pair now totals 90. Enumerate exact pytest node IDs in `test_attestation.json` rather than
  asserting a bare count.
- Still to be written: `src/m4/estimator_runner.py` and `src/m4/estimator_scoring.py`. Everything
  else the plan names now exists — see the module table in §2. The outcome classifier has **moved**
  to `src/m4/outcome.py`; `scripts/diagnose_bin_drift.py` re-exports it and `scripts/score_offline.py`
  imports it directly, so do not reintroduce the old script-importing-script path.
- Candidate domains are **not** shared between the synthetic and real paths. Applying the wrong one
  to a grid silently changes the answer — this exact mistake was made and caught during review.
- The exact capture registry/hashes/locks are in the base plan §3.3; do not rediscover captures by
  uniform `session_id`. Massimo 3 stores `massimo_3`. Only Massimo 1-2 persist a live raw-mirror
  hash; the registry uses direct SHA-256 for all eight.
- m7 has duplicate/missing Masimo seconds; use the parser's integer-`Timestamp` deduplication, never
  hardcoded CSV row counts.
- Do not mutate `results/live_demo/`, `data/raw/`, live estimates, intermediates, metadata, warmup
  evidence, or Masimo CSVs.
- Largest decoded capture is roughly 3.15 GB plus FFT temporaries. One capture at a time; if that is
  not feasible, stop and amend rather than inventing a streaming decoder.
- Use the `radar-vitals` conda environment and a workspace-local pytest base on Windows:

  ```powershell
  & 'C:\ProgramData\anaconda3\condabin\conda.bat' run -n radar-vitals `
    python -m pytest <tests> -q --basetemp=.pytest_tmp\<name>
  ```

  Plain `conda` is **not** on PATH — the `conda.bat` full path is required. Calling the
  environment's `python.exe` by absolute path crashes matplotlib `savefig`.

## 6. Pointers

| Purpose | Path |
|---|---|
| Project rules | `CLAUDE.md` |
| Chronological record | `HISTORY.md` |
| Step 1b base authority | `plans/m8_step1b_ahmed_transfer.md` |
| Step 1b Addendum A | `plans/m8_step1b_ahmed_transfer_addendum_a.md` |
| Gate-prediction evidence script | `scripts/m8_step1b_gate_prediction.py` |
| Master M8 roadmap | `plans/implementation_plan.md` §M8 |
| Method rationale/current M8 interpretation | `notes/approach.md` §5.7 |
| Data roles/window/timing rules | `notes/analysis_prespec.md` §§3, 7 |
| Immutable Step 1a plan | `plans/m8_step1a_ahmed_reproduction.md` |
| Canonical Step 1a bundle | `figures/generated/m8_ahmed_fig8/20260729T075443.145998Z_8e08f5ab0120/` |
| Step 1a code/config | `src/m8/ahmed_fig8.py`; `experiments/m8_ahmed_fig8/config.yaml` |
| Ahmed paper | `literature/ref_papers/discovering_the_unseen_radar_vitals/Discovering_the_Unseen_Radar-Based_Estimation_of_Heartbeat_Breathing_Rate_and_Underlying_Muscle_Expansion_Without_Probes.pdf` |
| Current phase extraction/production DSP | `src/respiration.py`; `src/window_pipeline.py` |
| Current offline scorer/comparators | `scripts/score_offline.py`; `src/comparator.py` |
| Frozen frame-index grid | `src/m4/window_grid.py` |
| Saved eight-capture inputs | `results/live_demo/` |
