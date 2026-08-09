# Handoff — resume here

> Read this and `CLAUDE.md` before doing anything. **State verified 2026-08-10.**
> `HISTORY.md` is append-only; this file is the current resume point.

## 1. Project snapshot

This project estimates HR/BR from a TI IWR1642BOOST + DCA1000 radar for a seated subject at
0.8–1.4 m. Masimo `Beats / min`, aligned by integer Unix `Timestamp`, is the HR reference. The
existing eight captures span subjects A–D, have approximate frame origins and insufficient HR
dynamic range, and remain development-only evidence.

## 2. Current state

Active branch: **`vital_signs_own_v13`**. The reviewed cumulative M1 implementation, including the
Windows-safe v3 test-attestation transport and exact ordered JUnit identity binding, is committed at
**`15134e260905af8640c2720bb99c6927164bb56e`**. The only current uncommitted implementation repair
replaces v3's raw namespace regex with namespace-aware parser events in
`src/m8/ahmed_provenance.py`; its deterministic tests and current logs are also uncommitted. It does
not change ECA, AHET, thresholds, range-bin selection, signal representation or estimator behavior.

M1 scoring reports the complete `k>=0` ledger, separate `k=0`, and persisted-lock `k>=1`;
reconstructs exact denominators; retains zero-output captures in capture-macro coverage; and binds
source, raw ADC, config, registry, environment, gate, radar parent, reference and score outputs. The
development audit values remain `14/128` all-window coverage, `9/67` joint-given-reference and
`11/120` `k>=1`, with radar estimate identity SHA-256
`0ced713d76e2ac5d29a26d15a4b8a81f5e83f84d81e5bf156885d9d53ce17906`. The historical 30.08%
coverage is retired.

Two clean Phase A attempts at `15134e2...` failed closed before gate publication. The first used a
long external visualization TEMP path: the attested run reported `25 failed, 1344 passed, 1 skipped`
because temporary Git repositories exceeded Windows path limits. The second used short external
`C:\tmp\m1g15134`: all attested tests passed (`1369 passed, 1 skipped`), but the strict JUnit parser
mistook escaped `xmlns:evil=` text in a parametrized testcase name for a real namespace declaration.
No canonical gate, authorization edit, smoke artifact, radar parent or score artifact was created.

The uncommitted correction now detects actual namespace bindings via ElementTree `start-ns` events
over bounded exact XML bytes. Escaped namespace-like testcase/output text is accepted as data;
default, prefixed, used and unused declarations remain rejected. DTD/entity, BOM, PI, unknown XML
grammar, count/duration, and exact ordered node-identity protections remain unchanged and tested.

## 3. Active task / next steps

1. Independently test and code-review the uncommitted namespace-event correction.
2. If accepted, commit only with explicit user authorization. The new clean commit becomes the
   gate-source identity; do not reuse `15134e2...` after this source/test change.
3. Use full Conda activation and a unique short external TEMP root. Run unchanged
   `scripts/m8_ahmed_transfer.py synthetic --out results/m8_ahmed_transfer/synthetic`, then verify
   gate, source, test, environment and Conda identities completely.
4. Update only
   `experiments/m8_ahmed_transfer/authorizations/real_evaluation_20260808.yaml` to bind that gate and
   source, review it, and commit it as the sole direct authorization-only child.
5. Only then run smoke, the eight-capture radar parent and `scripts/score_production.py`; verify
   bundle integrity, exact denominators and estimate invariance. Stop after M1; do not begin M2.

## 4. Recent decisions that matter

- Final attested pytest inherits ordinary handles for Windows native-library compatibility; exact
  argv/cwd/return status and bounded exact xUnit2 bytes/counts/skips/ordered node IDs remain required.
- The first three collection commands remain captured because their stdout defines the exact
  ordered collection and declared-skip identities.
- Namespace declarations are identified by XML parser namespace events, not raw regex text. The
  closed tree grammar still rejects expanded namespace tags and attributes.
- Entity-bearing XML is rejected before either iterative or tree parsing. Unknown declarations,
  processing instructions, BOMs and malformed/oversized/tampered JUnit fail closed.
- Canonical scoring requires actual clean Git state, exact source reconstruction, the sole direct
  authorization-only transition, internally derived registry hashes and matching promotion-eligible
  radar-parent transition evidence.
- Full `k>=0` is the all-window denominator; `k=0` stays in the ledger and is reported separately.
  Undefined accuracy never removes a zero-output capture from coverage.

## 5. Verification and landmines

- Targeted namespace/strict parser: `24 passed, 123 deselected, 12 warnings`.
- Full provenance: `147 passed, 15 warnings` in 30.89 s.
- Transfer/bundle/strict-preflight: `105 passed, 12 warnings` in 6.02 s.
- Standalone inherited Agg regression: `1 passed, 12 warnings` in 3.27 s; its real Fig8 child passed
  in 2.44 s.
- These are dirty development-tree tests, not a canonical gate. Do not run a canonical builder until
  the repair is reviewed, committed and the worktree is clean.
- Use full activation via
  `C:/ProgramData/anaconda3/Scripts/conda.exe run -n radar-vitals --no-capture-output ...` and a short
  external TEMP/TMP. Never use a repo-local pytest/JUnit temp root for canonical work.
- Do not edit `data/raw/`, manually handle score rows, weaken provenance gates, revive the 30.08%
  summary, tune to Masimo, or begin M2.

## 6. Pointers

| File | Purpose |
|---|---|
| `plans/plan_codex_milestones.md` | authoritative milestone boundaries and audit quantities |
| `src/m8/ahmed_provenance.py` | source closure, transition and v2/v3 test attestation |
| `tests/test_m8_ahmed_provenance.py` | provenance, XML, transition and Windows regressions |
| `tests/conftest.py` | deterministic v3 fake pytest/JUnit runner |
| `src/m4/estimator_scoring.py` | canonical M1 rollup and scorer-side provenance gate |
| `src/m4/estimator_runner.py` | bounded evidence producer and radar-parent transition gate |
| `scripts/score_production.py` | canonical clean-tree production-scoring CLI |
| `experiments/m8_ahmed_transfer/capture_registry.yaml` | bound capture/config/reference identities |
| `notes/analysis_prespec.md` | estimands, roles, ledger and zero-output rules |
| `notes/protocol.md` | fixed capture procedure and recovery authorization status |
| `HISTORY.md` | append-only evidence, failures, retirements and next actions |
