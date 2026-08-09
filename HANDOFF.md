# Handoff — resume here

> Read this and `CLAUDE.md` before doing anything. **State verified 2026-08-09.**
> `HISTORY.md` is append-only; this file is the current resume point.

## 1. Project snapshot

This project estimates HR/BR from a TI IWR1642BOOST + DCA1000 radar for a seated subject at
0.8–1.4 m. Masimo `Beats / min`, aligned by integer Unix `Timestamp`, is the HR reference. The
existing eight captures span subjects A–D, have approximate frame origins and insufficient HR
dynamic range, and remain development-only evidence.

## 2. Current state

Active branch: **`vital_signs_own_v13`**. The reviewed M1 scoring, authorization-transition and
radar-parent-boundary implementation is committed at clean gate-source commit
**`53019f309e5810ba1495a38fe0e1ab97e0a6d381`**. The only current uncommitted implementation repair
is the Windows-safe test-attestation v3 change in `src/m8/ahmed_provenance.py` and its tests/logs.
It does not change ECA, AHET, thresholds, range-bin selection, signal representation or estimator
scientific behavior.

M1 production scoring reports the complete `k>=0` window ledger, separate `k=0`, and persisted-lock
`k>=1`; reconstructs denominators from exact per-capture rows; retains zero-output captures in
capture-macro coverage; and binds source, raw ADC, config, registry, environment, gate, radar parent,
reference and score outputs. The development audit values remain `14/128` all-window coverage,
`9/67` joint-given-reference and `11/120` `k>=1`, with radar estimate identity SHA-256
`0ced713d76e2ac5d29a26d15a4b8a81f5e83f84d81e5bf156885d9d53ce17906`. The historical 30.08%
coverage is retired.

Canonical Phase A gate generation at `53019f...` exposed a Windows process-transport defect. The
first three collection commands must remain captured because their node lists are parsed. The final
pytest execute command now inherits stdout/stderr and uses its exact bounded xUnit2 XML as the sole
machine-readable outcome authority. Schema v3 embeds and SHA-256-binds at most 8 MiB of exact XML,
records explicit stdio mode, argv, working directory and return status, and reconciles every count
and skip identity. Before parsing, it rejects DTD/entity and all other declarations, namespaces and
unexpected processing instructions. The parsed tree must match the closed pytest 8.4 xUnit2
element/attribute/order/text grammar; unknown or duplicate structural content fails closed.
Counts are ASCII decimal only, durations must match pytest's exact fixed three-decimal formatter,
and a UTF-8 BOM is forbidden before declaration handling.
Execute evidence schema `pytest_xunit2_single_suite_v2` also persists every reconstructed pytest
node ID in exact JUnit document order. Producer and validator both require exact ordered equality
with collection; same-count substitution, reordering, duplication or omission fails closed.
Immutable v2 all-captured attestations remain explicitly supported. This repair is implemented and
tested but is not committed or independently reviewed yet.

No new canonical gate, authorization edit, smoke artifact, radar parent or scored artifact was
created. The sole canonical authorization YAML remains unchanged.

## 3. Active task / next steps

1. Independently test and code-review the uncommitted test-attestation v3 repair.
2. If accepted, commit only with explicit user authorization. That new clean commit becomes the
   gate-source commit; do not reuse `53019f...` as the source identity after this code change.
3. Under full Conda activation and external writable `TEMP`/`TMP`, run
   `scripts/m8_ahmed_transfer.py synthetic --out results/m8_ahmed_transfer/synthetic` and verify the
   resulting gate, source, test, environment and Conda hashes.
4. Update only
   `experiments/m8_ahmed_transfer/authorizations/real_evaluation_20260808.yaml` to bind the verified
   gate/source, review it, and commit it as the sole direct authorization-only child.
5. Only then run smoke, the full eight-capture radar parent and `scripts/score_production.py`; verify
   bundle integrity, exact denominators and estimate invariance. Stop after M1; do not begin M2.

## 4. Recent decisions that matter

- The final attested pytest process inherits ordinary handles for Windows native-library
  compatibility. It is not exempt from verification: exact argv/cwd/return status and exact bounded
  JUnit bytes/counts/skips are persisted and validated.
- Collection commands remain captured and their stdout/stderr hashes remain required because
  collection stdout defines the ordered test and declared-skip sets.
- Missing console output is represented by `inherited_not_captured` plus null hashes, never by an
  empty or fabricated digest. Missing, malformed, oversized or tampered JUnit fails closed.
- Entity-bearing XML is rejected before ElementTree parsing. Only the documented pytest 8.4 xUnit2
  root, suite, properties, testcase, outcome and system-output grammar is accepted; structural text,
  namespaces, unknown attributes/elements and nonsensical duplicate containers/outcomes fail.
- Canonical scoring requires actual clean Git state, exact HEAD/source reconstruction, the sole
  direct authorization-only child transition, internally derived registry hashes and exact
  promotion-eligible radar-parent transition evidence.
- Full `k>=0` is the all-window coverage denominator; `k=0` remains in the ledger and is also
  reported separately. Undefined accuracy never removes a zero-output capture from coverage.
- Existing A–D captures remain development evidence. Corrected arithmetic does not make them final
  validation or demonstrate HR tracking.

## 5. Gotchas / landmines

- Do not run a canonical builder while this repair tree is dirty. Its refusal is the required
  clean-tree behavior.
- Use full activation via
  `C:/ProgramData/anaconda3/Scripts/conda.exe run -n radar-vitals --no-capture-output ...`.
  Direct MSYS Python lacks pytest, and manually prepending common DLL directories still produced
  Windows fatal `0xc06d007f` in the Matplotlib Fig8 node.
- Test `TEMP`, `TMP`, `--basetemp` and JUnit output must be outside the repository. The verified runs
  used a uniquely named root under the writable Codex visualization directory and cleaned it.
- The strict-parser full provenance command completed with `144 passed, 15 warnings` in 35.22 s;
  the nested real Fig8 node completed in 2.41 s. Transfer/bundle/preflight compatibility completed
  with `105 passed, 12 warnings` in 6.55 s. These are dirty development-tree tests, not a canonical
  gate.
- Do not edit `data/raw/`, manually select rows, weaken the exact JUnit/clean-tree gates, revive the
  30.08% summary, or begin M2.

## 6. Pointers

| File | Purpose |
|---|---|
| `plans/plan_codex_milestones.md` | authoritative milestone boundaries and audit quantities |
| `src/m8/ahmed_provenance.py` | source closure, clean-tree transition and v2/v3 test attestation |
| `tests/test_m8_ahmed_provenance.py` | provenance, transition and Windows stdio/JUnit regressions |
| `tests/conftest.py` | deterministic v3 fake pytest/JUnit runner |
| `src/m4/estimator_scoring.py` | canonical M1 rollup and scorer-side provenance gate |
| `src/m4/estimator_runner.py` | bounded evidence producer and radar-parent transition gate |
| `scripts/score_production.py` | canonical clean-tree production-scoring CLI |
| `experiments/m8_ahmed_transfer/capture_registry.yaml` | bound capture/config/reference identities and inventory |
| `notes/analysis_prespec.md` | estimands, roles, ledger and zero-output rules |
| `notes/protocol.md` | fixed capture procedure and recovery authorization status |
| `HISTORY.md` | append-only evidence, failures, retirements and next actions |
