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
- HEAD: `833bc6ed6848c6f158abb0c7638a1ebda0e50b1f`
- Committed on 2026-07-30, in order:
  - `2bfc167` deterministic Step 1a provenance + scorer OSR-03 tests
  - `f9e42b6` approved plan, Addendum A, gate-prediction evidence script, docs
  - `3aec30a` `.gitattributes` LF pin — recorded hashes were not reproducible
  - `833bc6e` pin git state in the scorer end-to-end test
- Uncommitted: `HANDOFF.md`, `HISTORY.md`, and Addendum A (amendment §A6b).
- No estimator, scorer, or product code has been changed. `git diff 1bad25c..HEAD --stat -- src/
  figures/` is empty; changes are confined to tests, plans, docs, and `.gitattributes`.
- **Test baseline: the full suite is green** — `1827 passed, 5 skipped, 0 failed`
  (was `3 failed, 1822 passed, 2 skipped` at the start of 2026-07-30). Focused Step 1a/adapter pair
  is `90 passed`.
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
| `plans/m8_step1b_ahmed_transfer_addendum_a.md` | `b0bdc047ab6e43b42f6e6d362b59a3cd6bfda817607fb74253008c7c3e5fd0c8` | **user-approved 2026-07-30**; cross-model re-review **waived** |

The base plan is deliberately **unmodified**, so its five acceptances remain valid. Governing
authority is the *pair*; where they conflict, the addendum wins. Any manifest binding
`approved_plan_sha256` must bind both.

Nothing is implemented. No Step 1b synthetic or real result exists.

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

**One user decision is open** (does not block implementation): the canonical Step 1a bundle no
longer self-verifies two of its own text payloads — see §5 and addendum §A6b. Repairing it means
touching a canonical scientific artifact, which base plan §6.1 forbids, so it is yours to call.

Next steps, in order:

1. ~~Commit the plans and evidence script.~~ **Done** — `f9e42b6`.
2. ~~Fix the ambient-Git Step 1a artifact test.~~ **Done** — `2bfc167`. Focused baseline is now
   **90 passed** (prior 85 + 5 new provenance tests), deterministic, with no product-code change.
3. Extract `src/m4/outcome.py` from `scripts/diagnose_bin_drift.py:592`, with bit-identical
   regression tests proving both existing callers are unaffected. **← current task**
4. Implement the rest of the fixture-testable system — `src/m8/ahmed_transfer.py`, neutral
   suite/runner/scoring contracts under `src/m4/`, stable `src/m4/outcome.py`, strict serializers,
   CLI, experiment config, capture registry, and the full test set — **without opening any real
   capture or Masimo file**.
5. Run the focused, affected, new, and broad fixture-only suites; freeze the scoped source, test, and
   environment attestations.
6. Execute the synthetic gate. Under Addendum A the gate verdict turns on reproducing predeclared
   predictions P1–P4, and the scientific transfer verdict is reported per domain, non-gating.
7. Stop after the gate. No real path may be touched unless the gate is complete,
   `promotion_eligible=true`, and one comprehensive real-evaluation authorization is frozen before
   first access.
8. If authorized, run the immutable `real-smoke -> radar -> scored` chain exactly as registered.

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
- **Canonical Step 1a bundle: two payload hashes are stale (open decision).** In
  `figures/generated/m8_ahmed_fig8/20260729T075443.145998Z_8e08f5ab0120/provenance.json`,
  `metrics_sha256` and `resolved_config_sha256` no longer match their on-disk files. The runner wrote
  them through Python text mode on Windows (CRLF) and hashed those bytes while Git stored LF, so
  before the LF pin they verified only on a Windows autocrlf checkout and never on Linux. The pin
  exposed this; it did not cause it. **Scientific outputs still verify** — `figure_png_sha256`,
  `figure_pdf_sha256`, `plan_sha256`, `implementation_module_sha256`, `runner_script_sha256` all
  match. (`test_file_sha256` also differs, for the unrelated and expected reason that the §6.1
  prerequisite changed that file.) Not repaired: base plan §6.1 forbids modifying the canonical
  bundle. Does **not** block Step 1b, which parents on its own gate and reads only
  `experiments/m8_ahmed_fig8/config.yaml`.
- **Three ambient-Git test defects were found and fixed this session** — the Step 1a artifact test,
  the scorer OSR-03 tests, and the scorer end-to-end test. When writing any test that asserts on
  promotion, `reproducible`, or git cleanliness, **pin the state** (`_pin_git_provenance`, or
  `monkeypatch.setattr(so, "is_tree_clean", ...)`). Do not let it read the ambient worktree.
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
- Planned files `src/m8/ahmed_transfer.py`, `src/m4/estimator_suite.py`,
  `src/m4/estimator_runner.py`, `src/m4/estimator_scoring.py`, and `src/m4/outcome.py` do not exist
  yet. The outcome classifier still lives at `scripts/diagnose_bin_drift.py:592` and is aliased by
  `scripts/score_offline.py`.
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
