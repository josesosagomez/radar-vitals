# Replay comparison review and verification log

Implementation checkout: `results/replay_compare_worktree`, branch
`codex/replay-masimo-compare`, starting feature commit
`dcda615d1a2984848882ed7da6641860331577b1`.

## Isolation and plan reconciliation

- Primary inspected all worktrees and preserved original unrelated changes and
  feature planning-only edits. The new worktree starts from the required commit.
- `workflow_manager` reviewed sequencing and isolation read-only. Its observation
  that the replay branch already existed was a timing overlap with primary's
  approved creation; no pre-existing branch was overwritten.
- Independent `plan_reviewer` returned READY WITH MINOR CHANGES. Its mandatory
  clarification binds selected ADC/metadata/warmup/counts to the committed radar
  registry, preventing pre-run substitution that self-hashing alone would miss.
  The agent encountered model-capacity failures before and after that verdict;
  the separate independent `code_reviewer` also reconciled the plan/interfaces
  and returned READY TO IMPLEMENT M1 WITH MANDATORY GATES.
- `research_agent` checked pinned GUI APIs and the Masimo manual using primary
  documentation. No estimator or threshold change was needed. Findings and
  citations are incorporated in `notes/approach.md`.
- Protected byte hashes are recorded in
  `reports/replay_compare_protected_sources_2026-10-07.json`; startup is 10 seconds.
- Approved-plan/isolation commit `a2a01d885efa19a19c598636016936911a7d1019`
  was pushed and its remote SHA independently read back.

## Milestone 1 - initial input/reference checks

Independent reviewer findings:

1. **High, resolved in reference access:** a same-name directory symlink inside
   the allowed root could redirect the registered capture while passing mere
   containment. Require exact resolved registered relative identity.
2. **Medium, resolved in reference access:** arbitrary supplied aliases to an
   authorized directory could bypass lexical capture identity. Check supplied
   and resolved paths, including traversal, before opening reference bytes.
3. **Regression, resolved:** the new substituted-link diagnostic initially
   masked the existing protected-path diagnostic. Check resolved protected paths
   first and retain explicit refusal reasons.

Primary focused command (pinned environment, serial execution):

```powershell
C:/ProgramData/anaconda3/Scripts/conda.exe run --no-capture-output -n radar-vitals python -m pytest tests/test_replay_compare_source_clock.py tests/test_replay_compare_reference_access.py tests/test_reference_access.py -q --tb=short --basetemp C:/Users/josemsosag/AppData/Local/Temp/radar_replay_compare_m1_source/pytest -o cache_dir=results/test_tmp/pytest_cache_replay_m1_source --junitxml=results/test_tmp/replay_m1_source.xml
```

Observed: **48 passed**, 12 installed Matplotlib/pyparsing deprecation warnings,
3.17 seconds. This is a focused source/reference/clock check, not full milestone
or delivery acceptance. Decoder tests compare both SampleSwap conventions against
the production decoder and offline reader using known bytes.

Earlier focused attempts are preserved under `results/test_tmp`: the first had
6 passes/20 setup errors because primary omitted creating the external basetemp
parent; the next had 24 passes/2 diagnostic failures described above. The external
parent was created and production diagnostics were fixed before the passing run.
No sealed-data opt-in or real-data tests were enabled.

### Final M1 gate

Additional independent findings resolved before acceptance:

- Canonical immutable JSON bytes with defensive copy access prevent nested
  settings/provenance mutation after hashing.
- Metadata and warmup JSON are each read once; those exact bytes are hashed and
  parsed, removing the hash/second-open substitution gap.
- Both initial and final integrity failures close the ADC handle and report an
  explicit replay-source error; constructor types/digests are validated exactly.
- Session input roots receive lexical and resolved protected-path checks before
  reading any input. Legacy validity requires live/mirror metadata and imports
  the production packet payload constant. Explicit incompatible settings are
  rejected; only absent historical defaults are normalized.

Independent `code_reviewer` final static verdict: **ACCEPT**, no remaining
blocking/high/medium correctness findings in M1.

Final focused command adds `tests/test_replay_compare_session.py` to the command
above and uses external basetemp `radar_replay_compare_m1_final/pytest`, cache
`results/test_tmp/pytest_cache_replay_m1_final`, JUnit
`results/test_tmp/replay_m1_final_rerun.xml`. Observed: **93 passed**, 12 installed
deprecation warnings, 4.33 seconds. The immediately preceding run had 87 passes
and six failures caused only by over-specific error-message regexes; the test
engineer changed those regexes to semantic field names without weakening the
rejection assertions.

Authorized real-input preflight (no frames processed and no reference opened)
observed initial and final ADC SHA-256:
`cca0cdcbaa8235672c96f524666835824aadf40aa78b12197705da63dbeb7b00`.
It matched the committed radar registry; metadata, warmup and original-config
hashes also matched. The capture contains 12,005 frames, 600.25 seconds, with
approximate integer-microsecond origin 1785268142711071. The observed input report
is `results/test_tmp/replay_m1_massimo3_input.json`; this is input verification,
not replay/DSP or coverage acceptance. Effective configuration SHA-256 is
`759ff50d0beecc1e6b372ac828e62e9d43ccedbb27cc1ff4b77d00fb59c5dfe6`.
