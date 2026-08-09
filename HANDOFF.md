# Handoff — resume here

> Read this and `CLAUDE.md` before doing anything. **State verified 2026-08-09.**
> `HISTORY.md` is append-only; this file is the current resume point.

## 1. Project snapshot

This project estimates HR/BR from a TI IWR1642BOOST + DCA1000 radar for a seated subject at
0.8–1.4 m. Masimo `Beats / min`, aligned by integer Unix `Timestamp`, is the HR reference. The
existing eight captures span four subjects A–D, have approximate frame origins and insufficient HR
dynamic range, and remain development-only/exploratory.

## 2. Current state

Active branch: **`vital_signs_own_v13`**. The reviewed M1 implementation was committed at
**`9b01d936db35b8427ee3b36579bd62a828a05926`**; only the later authorization-transition and
radar-parent-boundary corrections described below are currently uncommitted on top of it.

M0 is ready. The reviewed natural/paced/recovery analysis contract and owner-attested authorization
metadata remain binding in `notes/analysis_prespec.md` and `notes/protocol.md`.

The reviewed M1 implementation is committed. The current uncommitted provenance correction does
not change the estimator. The M4 scorer
now emits a canonical production rollup keyed by `(capture_id, k)` for
`production_eca_ahet_v1` under `current_production_rerun_lock`. It reports all complete windows,
separate `k=0`, and persisted-lock `k>=1`; reconstructs every denominator from per-capture rows; and
retains zero-output captures in capture-macro coverage. `scripts/score_production.py` is the
clean-tree-only entry point and `production_summary.json` is part of the immutable scored bundle.

Independent test and code review found multiple provenance defects, now repaired.
Canonical scoring no longer accepts caller identity as authority: `run_score_stage` itself invokes
Git on the repository containing the running scorer, requires the whole tree clean, resolves actual
HEAD, and rebuilds the transitive scientific source manifest. Gate source bytes/hash must remain
exact, but HEAD may be the direct one-commit child of the gate source commit when that commit changes
exactly the sole canonical authorization YAML passed to preflight. The authorization must bind the
exact gate/source and its filtered Git blob plus working SHA-256 are recorded. It then derives the
exact eight-capture raw/config maps from the source-bound registry and cross-checks configuration
hashes against validated radar-parent rows. There is no constructible module token or callable
module-level canonical writer. Canonical status/artifact writing is lexically contained in that
verified run branch; public persistence is always non-canonical and rejects identity maps.
Radar-stage promotion eligibility is now derived from that exact transition proof: non-strict
portable runs are explicitly noncanonical, and canonical scoring requires the promotion-eligible
radar parent to contain an exact transition record equal to the scorer's independently reconstructed
current Git transaction before reference access.

Development verification against the existing hash-verified M4 radar parent reproduced the parent
radar rows exactly and yielded:

- `14/128` all-window radar coverage (`10.9375%`);
- `9/67` joint-given-reference (`13.4328%`);
- `11/120` `k>=1` radar coverage (`9.1667%`);
- capture macro `19.4271%` for all windows and `16.9737%` for `k>=1`, with zero-output captures
  retained;
- estimate identity SHA-256
  `0ced713d76e2ac5d29a26d15a4b8a81f5e83f84d81e5bf156885d9d53ce17906`.

The historical 30.08% coverage is retired. The legacy M8 scorer remains descriptive-only, now uses
separate coverage/accuracy capture sets, retains even an all-zero estimator with zero coverage and
undefined accuracy, prints undefined MAE/hit metrics as `n/a`, and explicitly blocks a production
headline. Its pooled CSV retains the all-zero row.

## 3. Active task / next steps

1. Independently retest and review the authorization-transition and radar-parent-boundary
   corrections.
2. If accepted, commit the repair only with explicit user authorization and generate the synthetic
   gate from that clean gate-source commit.
3. Update only the sole canonical authorization YAML to bind that exact gate/source, commit that
   one-file direct-child transaction, then run smoke and the full M4 radar parent from the bound raw
   ADC/config inputs without further commits.
4. Run `scripts/score_production.py`; verify its bundle, exact denominators and production estimate
   identity against the development value above.
5. Stop after M1. M2 is not authorized by this work.

## 4. Recent decisions that matter

- M1 changes evaluation and provenance only. ECA, AHET, thresholds, range-bin selection, signal
  representation and all estimator scientific behavior are frozen.
- The full `k>=0` ledger is the honest all-window coverage denominator. `k=0` is also reported
  separately as lock-selection-in-sample; `k>=1` is the persisted-lock comparison subset.
- Accuracy and coverage have different capture sets. Undefined accuracy never removes a capture
  from coverage; a zero-output capture contributes zero coverage.
- Canonical production scoring requires the whole Git tree to be clean, not merely a scoped subset.
  Raw ADC, capture config, code/source manifest, radar parent and reference files are SHA-256-bound.
- Caller claims are not evidence. Canonical status requires the scorer's own actual-clean-tree and
  HEAD check, an exact rebuilt source manifest, the source-bound registry, and the validated
  radar-parent configuration map. Public persistence cannot emit canonical status.
- Gate and authorization commits are distinct by design. Canonical use permits exactly one direct
  post-gate commit changing exactly the authorization passed; every other intervening history or
  working-tree change fails.
- Production intermediates remain the complete bounded M4 native tree: one typed, non-object,
  pickle-free record for every production cell, estimate and abstention.
- Existing A–D data remain approximate-origin development evidence. Correct arithmetic does not
  turn them into validation or demonstrate HR tracking.

## 5. Gotchas / landmines

- The official CLI currently **must fail** because the post-`9b01d936` provenance corrections are
  uncommitted. The reviewed M1 base itself is committed; the current refusal is the clean-tree
  contract working, not a reason to bypass it.
- The latest focused M1/scorer/preflight/runner and compatibility selection completed with
  `262 passed, 1 skipped`. These are dirty development-tree tests, not a canonical artifact.
- The full provenance suite completed with `101 passed` when its basetemp was placed under the
  system temporary root, as required by its outside-repository attestation-path assertion.
- The clean source manifest includes `scripts/score_production.py` and
  `tests/test_m1_production_scoring.py`; an old gate cannot authorize the changed scorer.
- Use `C:/Users/josemsosag/.conda/envs/radar-vitals/python.exe`; the shell's MSYS Python lacks
  pytest. Provenance tests require `--basetemp` outside the repository; the latest run used a
  uniquely named directory under the system temporary root and removed it afterward.
- The eight old frame origins use `start_wall_utc` and remain approximate by 5–15 seconds. Do not
  promote their agreement metrics or call them final evidence.
- Do not edit `data/raw/`, manually select rows, omit abstentions, or revive the 30.08% summary.
- Recovery and role/firewall rules from M0 remain unchanged; do not proceed to M2 or capture work.

## 6. Pointers

| File | Purpose |
|---|---|
| `plans/plan_codex_milestones.md` | authoritative milestone boundaries and audit quantities |
| `src/m4/estimator_scoring.py` | M4 scorer plus M1 exact production rollup and provenance gate |
| `scripts/score_production.py` | canonical clean-tree production-scoring entry point |
| `tests/test_m1_production_scoring.py` | denominator, zero-output, k=0, legacy and clean-tree regressions |
| `src/m4/estimator_runner.py` | complete M4 evidence producer plus pre-data authorization transition gate |
| `src/m4/evidence_serialization.py` | typed, non-object, pickle-free production evidence tree |
| `scripts/m8_ahmed_score.py` | legacy descriptive M8 scorer; production headline explicitly blocked |
| `experiments/m8_ahmed_transfer/capture_registry.yaml` | raw/config/reference hashes and fixed 128-window inventory |
| `notes/analysis_prespec.md` | binding estimands, roles, ledger and zero-output rules |
| `notes/protocol.md` | fixed capture procedure and recovery authorization status |
| `HISTORY.md` | append-only M1 evidence, failures, retirement and next action |
