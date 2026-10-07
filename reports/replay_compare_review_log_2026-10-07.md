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

## Milestone 2 - review findings before acceptance

Independent review of the draft returned **CHANGES REQUIRED**. This is retained
as the review record; it is not an acceptance verdict. Findings sent to the
implementer and independent test engineer:

- Validate selector actual-bin identity and candidate-gate membership before
  committing the fixed bin; cancel pending selector retries after success.
- Reconstruct request/terminal-event lineage, bounds, generation, epoch,
  revision and bin. Check every duplicated CSV/NPZ/application-event field and
  strict scalar types; a truthy string must not bypass scientific checks.
- Require exact manifest/actual/indexed-NPZ sets, reject temporary/unindexed
  files, detect deleted requests or attempts and duplicate terminal events.
- Recompute native admission, accepted-only median and accepted/held BR; compare
  scientific intermediate spectra, frequency grids, peaks and respiration input,
  not only final rates or phase cleaning. Preserve explicit typed failure data.
- Use authentic production-DSP synthetic fixtures for passing evidence checks;
  the original hand-made five-sample fixture could not support a 600-frame claim.
- Preserve executed evidence through stop, source failure, EOF and timeout;
  enforce pause execution gating and record cancelled unopened jobs as events.
- Preserve actual intermediate arrays when the worker returns malformed
  identity/bounds. Record returned identity separately, release the requested
  job with an explicit failed disposition and never publish it.
- Keep a slow active selector bounded across three or more update boundaries;
  replacing a pending retry must not assert that no active job exists or abandon
  its scheduler. Re-anchor pacing after input hashing to avoid a preparation-time
  catch-up burst.

The workflow audit clarified that M2 can accept tested radar-only event/lease
primitives needed for baseline evidence. Full coverage, passive reference and GUI
acceptance remain M3. Independent review also confirmed that final M3 artifact
verification must bind saved phase to the registered ADC; saved-phase DSP alone
can prove only internal consistency. Both clarifications are recorded in the plan.

M2 acceptance and primary test results are pending at this point in the log.

### M2 focused iterations and second independent review

The implementation owner's focused combined run observed **55 passed** in
4.21 seconds with 12 installed deprecation warnings; JUnit:
`results/test_tmp/replay_m2_engine_evidence.xml`. This is not primary acceptance.
Earlier iterations are retained: a 33-test report contained 25 passes and eight
failures from incomplete evidence fixtures. A later run was interrupted after
approximately five minutes when a pause test recursively acquired the same queue
mutex. The test engineer replaced the nested `qsize()` call with a queue-length
check under the existing lock and added bounded cleanup without weakening the
pause assertions. A real stop/read race was fixed separately.

The next independent static review still returned **CHANGES REQUIRED**:

- Evidence writing must not invent missing controller request/application events.
- Every unopened replacement needs one explicit cancellation terminal event;
  unknown-job cancellation/application events must fail verification.
- Application and terminal event fields must match indexed evidence exactly.
- Selection decisions must explain committed bins and revision increments.
- Reconstruct eligible acceptance and every accepted/held/missing display value
  in both directions; rejecting an eligible estimate cannot hide missing data.
- Malformed returned identities require separate lifecycle and scientific
  identities, retaining the actual arrays and making later source binding clear.

These findings remain acceptance blockers until independently cleared. No
estimator, scientific threshold, reference alignment or protected production file
was changed to resolve them. Primary rechecked all 20 protected hashes and the
10-second calibrated startup setting; they still match the feature checkpoint.

## Early M3 primitive check (not milestone acceptance)

Primary ran the existing draft coverage/reference tests serially before UI
integration. Command: pinned conda Python `-m pytest
tests/test_replay_compare_coverage.py tests/test_replay_compare_reference.py -q
--tb=short`, external isolated basetemp
`radar_replay_m3_primitives_64a584ee1297481ea1ae41f30fc33cd4/pytest`, repository
cache `results/test_tmp/pytest_cache_replay_m3_primitives`, JUnit
`results/test_tmp/replay_m3_primitives_initial.xml`.
Observed **31 passed, 2 failed**, 12 installed deprecation warnings, 1.44 seconds.
The failures require rejecting coverage events outside elapsed bounds and
duplicate reference timestamps instead of silently ignoring/overwriting them.
Both were returned to the production owner; tests were not weakened.

## Final M2 acceptance

Independent `code_reviewer` final verdict: **ACCEPT**, with no remaining
blocking/high/medium findings in the baseline engine/evidence slice. Later review
also required causal event ordering, exact numeric widths and baseline auxiliary
arrays, a coherent cached runtime snapshot, and durable events/attempt evidence
before making a new accepted value visible. Executed arrays remain recoverable
even when lineage is missing; the writer never fabricates controller events.
The integration test now runs the actual runtime through actual evidence writing,
EOF finalization and independent internal verification.

Implementation iterations were retained: `replay_m2_sixfix_initial.xml` recorded
63 passes/seven failures; the subsequent six-fix rerun passed 72. Further fixes
and tests increased the candidate to 76, then 78. Primary did not treat these
implementation-owned runs as independent acceptance.

Primary independent M2 command:

```powershell
C:/ProgramData/anaconda3/Scripts/conda.exe run --no-capture-output -n radar-vitals python -m pytest tests/test_replay_compare_engine.py tests/test_replay_compare_runtime.py tests/test_replay_compare_evidence.py -q --tb=short --basetemp C:/Users/JOSEMS~1/AppData/Local/Temp/radar_replay_m2_primary_b71a4ea38407423daa530c636dfd23c9/pytest -o cache_dir=results/test_tmp/pytest_cache_replay_m2_primary --junitxml=results/test_tmp/replay_m2_primary.xml
```

Observed **78 passed**, 12 installed deprecation warnings, 8.46 seconds, exit 0.
The preceding independent source/reference/startup/warmup/pipeline/scheduler/
legacy-evidence regression run passed **256 tests** in 5.98 seconds, with the
same 12 warnings and exit 0; JUnit `results/test_tmp/replay_m2_regressions.xml`.
Its exact test selection was `tests/test_replay_compare_session.py
tests/test_replay_compare_source_clock.py tests/test_replay_compare_reference_access.py
tests/test_reference_access.py tests/test_live_demo_startup_delay.py
tests/test_live_demo_warmup_helpers.py tests/test_window_pipeline_adapter.py
tests/test_live_motion_scheduler.py tests/test_live_motion_evidence.py`, with
external basetemp `C:/Users/JOSEMS~1/AppData/Local/Temp/radar_replay_m2_regressions_00e08738c9c941ecaf13a06c96fe49e0/pytest`, cache
`results/test_tmp/pytest_cache_replay_m2_regressions`, and the same pinned command
prefix/options as the focused run above.
Both ran serially in the pinned environment with isolated external basetemp and
repository-local cache/JUnit; sealed/real-data opt-ins were not enabled.

M2 verification explicitly proves internal evidence/math/lineage. Final M3
acceptance still requires binding retained phase to the registered raw ADC and
independently checking source metadata, coverage and passive reference artifacts.
An unusable malformed returned scientific coordinate remains explicitly
unverified; lifecycle normalization never relabels its scientific arrays.
