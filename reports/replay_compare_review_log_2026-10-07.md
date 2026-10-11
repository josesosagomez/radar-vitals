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

The accepted M2 milestone is commit
`8dddf028074b8fcec0b273bde7572b5c683f1f29`, pushed on the separate replay branch
and verified by reading the remote SHA back. The original and feature branches
remain at their original checkpoints with their existing changes preserved.

## Initial M3/M4 focused check (not acceptance)

Independent test engineer ran current coverage/reference/UI/advanced tests
serially in the pinned environment. Observed **66 passed, 2 failed**, 14 warnings,
2.89 seconds; JUnit `results/test_tmp/replay_m34_focus.xml`. One failure is a real
UI defect: accepted asynchronous Restart must immediately disable repeated
restart requests. The other is a synthetic fixture missing the calibration
mapping required before the unchanged live preflight reaches its replay ban.
Production UI and test fixture fixes are assigned separately; the replay ban
will remain unchanged. Full application/reference isolation and source-bound
verification remain required M3 gates. Independent advanced review has pending
findings and is not an acceptance verdict.

### Application integration iterations

The test engineer's initial M3/M4 selection was coverage, reference, UI and
advanced test files, with `--basetemp results/test_tmp/replay_m34_focus_tmp` and
no explicit cache directory. Its 14 warnings were 12 installed deprecations and
two cache-permission warnings. The two failed nodes subsequently passed
(`replay_m34_two_failures.xml`: two passes, 3.69 seconds). Qt callback stderr
was investigated separately; production changed PlotWidget title construction
and must demonstrate clean callbacks on rerun.

Implementation-owned M3 integration iterations:

- An initial shell used `conda` without its absolute path and could not start.
- A run without explicit basetemp reported 50 passes and one setup error due to
  inaccessible default temporary storage (`replay_m3_primitives_after_fixes.xml`).
- Coverage/reference/UI/source-clock/runtime/application selection: 100 passed,
  three failed, 12 warnings, 5.69 seconds
  (`results/test_tmp/replay_m3_application_initial.xml`).
- Application/runtime/source-clock rerun: 50 passed, one failed, 12 warnings,
  5.26 seconds (`results/test_tmp/replay_m3_application_rerun.xml`). The remaining
  comparison included nondeterministic warmup elapsed-time diagnostics; the
  test engineer will exclude only elapsed diagnostics from scientific equality.

Those two implementation runs used unique basetemp directories under the
original checkout's `results/test_tmp`, rather than OS temporary storage. Their
test results are retained but do not satisfy the final external-basetemp gate.
Primary reinforced that all following runs must create a unique OS-temp parent,
use its `pytest` child, set a new-worktree-local cache and JUnit, and obtain a
central serial-environment grant. Final acceptance will use those settings.

Primary/reviewer M3 findings include controller-thread-only composition (UI
reads must not advance coverage/reference state), atomic cached view models,
durable-attempt lease deferral, and stop/restart transition coordination/failure
reporting. These are acceptance blockers, not optional polish. The primary's
native acceptance driver is separate from production processing and will refuse
dirty scientific sources before running verified actual playback.

### Standalone advanced/UI review and integration follow-up

Independent reviewer accepted the standalone advanced gate/orchestrator and UI
slice, explicitly withholding end-to-end M4 acceptance until common replay
finalization/source/coverage/reference verification passes. Gate/orchestrator
fixes include atomic pause/start acknowledgment, one cancellation terminal,
physical production authorization/source/writer binding, preserved malformed
returned arrays, native AHET admission, and frozen-denominator nonpublication.
UI close now waits for bounded artifact finalization after the owned event loop.

Fresh UI/advanced focused run first observed **49 passed, one failed** (4.55 s):
`test_advanced_stop_before_worker_start_is_one_cancellation_and_no_attempt`
reported non-clean shutdown. The worker now exits when stop is already set after
dequeue. The rerun passed **50 tests**, 12 installed deprecation warnings, 2.28 s,
exit 0, with no Qt callback stderr. Both used the absolute pinned command, explicit
external OS-temp basetemp and repo-local cache. The implementation owner reused
`replay_ui_advanced_review.xml`, overwriting the first XML; the observed failure
and reason are preserved here and in the tool transcript, rather than inventing
a replacement XML. Future runs must use distinct JUnit names.

The test engineer's application-inclusive iteration recorded **87 passed,
four failed**, 12 warnings, 4.74 s (`replay_m34_current.xml`): three missing test
imports and one reference-audit integration issue. A following centrally granted
run recorded **73 passed, four failed**, 12 warnings, 9.79 s, zero Qt callback
stderr (`replay_m34_rerun.xml`). This exposed a legitimate valid selector finishing
after EOF: it must preserve evidence without incrementing selection revision;
the verifier must accept that honest expired record. Primary required this
regression to remain separate from publication-forcing fixture changes. Missing
ADC must produce explicit incomplete verification, not an escaped source error.
The other fixture corrections concern recomputing impulse cleaning after phase
forgery and actually reaching a scheduled advanced preview.

Further M3 verifier review requires independently deriving expected reference
audit identities (not trusting saved rows), checking guarded reference even with
zero attempts, exact physical-event boundary clipping, cancellation vocabulary,
motion attempt/admission correspondence, and complete common manifest/summary
schemas. These remain unresolved acceptance gates at this point.

## Continuation audit - 2026-10-11

The prior centrally granted application/advanced/CLI run did complete before the
interruption. Its retained `results/test_tmp/replay_m34_cli.xml` reports **51
passed, five failed**, zero errors, 12.000 seconds (56 tests). Three failures
exposed a missing `BaselineChannel` import in the advanced composition adapter;
two were stale test expectations for source-binding failure and reference audit
field names. These are assigned to the production and test owners separately.
Missing-source, honest late-selector EOF and CLI guard checks passed in this
iteration; that does not establish integrated milestone acceptance.

Primary re-inspected actual Git state: replay HEAD remains
`8dddf028074b8fcec0b273bde7572b5c683f1f29`; original and feature checkouts retain
their original checkpoints and dirty work. All 20 protected source/config hashes
still match, and calibrated startup remains 10 seconds. The ordinary sandbox
process launcher failed before process creation with `setup refresh had errors`;
authorized escalated read-only inspection succeeded. No failed launcher call
executed a test or changed source. Editing and independent review responsibilities
were re-established after the interrupted agents were no longer live.

Continuation static audit confirmed remaining defects: canonicalized calibration
JSON was labelled as the exact hash-authorized file bytes; advanced restart reused
cached authorization and could create a new pass before fresh physical validation;
zero-attempt motion passes lacked native CSV headers; stop during a restart build
could finalize an unstarted pass as running without source integrity. Both replay
managers require an honest transition/shutdown gate. These findings are returned
to separate production owners and independent regression tests.

The plan's reference audit includes values shown by the current cards, as well as
window summaries. Inspection found only summaries persisted. M3 now requires a
manifested `reference_display_audit.json` scalar frame/bucket/value audit and
independent reconstruction from guarded reference inputs. This clarifies the
existing artifact requirement; it changes no reference alignment, PI threshold,
radar processing or coverage admission.

Independent continuation review also required saved source-integrity hashes and
cursor counts to match elapsed/completion claims; exact motion generation,
application epoch and selected-bin lineage; complete common schemas; and a
hash/parse binding of the saved calibration snapshot to current physical
authorization. Restart joins must report a still-live transition as non-clean;
an unstarted cancelled pass must not acquire a successful scientific manifest.
These are integrated acceptance blockers until reviewed fixes and tests pass.

Review found the native acceptance driver's speed flags acknowledged callbacks,
not controller application. Primary changed them to require observed generation-2
speed transitions, durable `speed_changed` events, and measured acknowledgment
latency. Peak RSS and steady-playback early/late observations are now saved;
there is no invented absolute memory threshold. The independent driver rereview
found no remaining material defect. Every actual pass will still receive a
separate source/reference artifact verification, including the partial pass
preserved by controls/restart.

### Baseline continuation iteration

The centrally granted test-engineer baseline run observed **156 passed, two
failed**, 12 installed deprecation warnings, 33.56 seconds. JUnit:
`results/test_tmp/replay_baseline_resume_20261011.xml`; external basetemp:
`C:/Users/josemsosag/AppData/Local/Temp/replay_baseline_resume_3d0eb0c78d00416da64c7779b0a459a1/pytest`;
cache: `results/test_tmp/pytest_cache_replay_baseline_resume`. Selection: application,
coverage, reference, UI, engine, runtime and evidence replay test files, using
the absolute pinned conda prefix and `-q --tb=short`.
The two failures expected ValueError where strict bool/fraction constructor
inputs correctly raise TypeError. Tests will assert the appropriate exception
class without weakening rejection. A control patch finished immediately before
the freeze notice; this run is retained as an iteration, with stable final
verification still required. The environment slot was returned after completion.

The following application/advanced/CLI iteration executed no tests: collection
reported two errors from an in-flight unclosed parenthesis in verification.py,
12 installed warnings, 2.27 seconds. JUnit:
`results/test_tmp/replay_m34_tests_resume_20261011.xml`; external parent:
`C:/Users/josemsosag/AppData/Local/Temp/replay_m34_tests_resume_1e620a2dbdfd4c59a3d72b49730920b8`;
cache: `results/test_tmp/pytest_cache_replay_m34_tests_resume`. The primary granted
the run before every editing owner had finished and acknowledged the source
freeze. That coordination error is corrected: subsequent execution requires
completed static edits, an AST check, and all production owners' freeze
acknowledgments. The failed XML is preserved; no passing test claim is made.

Independent containment review requires a fail-early artifact preflight before
either internal verifier: detecting a link/escape later is insufficient if an
earlier read has already followed it. Required artifacts, NPZ paths and saved
calibration snapshot paths must be contained before bytes are opened. The CLI
and native driver also reject a redirected fixed output root rather than treating
its resolved target as authorized output storage.

### Stable all-replay iteration

After all owners acknowledged completed AST checks and source freezes, the
test engineer ran all 12 `test_replay_compare_*.py` files serially with the pinned
absolute conda command. Observed **307 passed, ten failed**, 12 installed
deprecation warnings, 32.21 seconds, exit 1; no Qt callback stderr. JUnit:
`results/test_tmp/replay_all_resume_20261011.xml`; external basetemp:
`C:/Users/josemsosag/AppData/Local/Temp/replay_all_resume_74f836664dfb45e0bf2bb4789f3d494e/pytest`;
cache: `results/test_tmp/pytest_cache_replay_all_resume`.

Two tests demonstrated a verifier defect: re-signed zero-attempt motion playback
events with generation `true` or `1.0` were accepted through Python's coercive
numeric equality. Exact event integer types must be checked before equality.
The remaining failures concern fixture structure, stopped-manager reuse,
class/instance monkeypatching, source-prepared file identity, and changed
fail-early issue codes; production/test owners are checking each rather than
weakening scientific rejection. Independent review also requires preserving
the first baseline transition timeout after later cancellation/finalization.
The files were unfrozen only after the run ended and its XML was preserved.

### Final integrated static review

Independent `code_reviewer` continuation verdict: **ACCEPT**, no remaining
BLOCKER, HIGH, or MEDIUM findings. The reviewer independently checked the
baseline and advanced lifecycle, first-causal timeout preservation, atomic
controls, fail-before-read artifact containment, physical calibration snapshot
bytes/hash/record authorization, strict event and motion scalar types, source
cursor/completion/hash binding, guarded per-frame reference reconstruction,
checkout output authority, and the native full-1x responsiveness gates.
This is static acceptance only; the earlier 307-pass/ten-fail run is not dynamic
acceptance. Test corrections prove untampered artifacts are accepted before
re-signing adversarial modifications, and both stuck-restart regressions assert
that later transition joins preserve the original timeout failure.

Primary's frozen-source audit independently parsed 29 replay Python files,
confirmed LF endings and clean diff whitespace, and matched all 20 protected
source/configuration hashes. Calibrated startup remains 10 seconds.

### Stable all-replay rerun

The serial all-12-file rerun observed **317 passed, two failed**, 12 installed
deprecation warnings, 32.21 seconds, exit 1, no Qt callback stderr. Retained
JUnit: `results/test_tmp/replay_all_resume_rerun_20261011.xml`; cache:
`results/test_tmp/pytest_cache_replay_all_resume_rerun`; external basetemp:
`C:/Users/josemsosag/AppData/Local/Temp/replay_all_resume_rerun_43dd3baac58a4b99aa430831bf1f902d/pytest`.
All untampered acceptance prechecks, re-signed boolean/float event rejection,
and first-timeout identity checks passed. Two stale test assertions remained:
the missing calibration now correctly reports physical calibration validation
failure, and the baseline runtime does not have the advanced orchestrator's
private `_controller_thread` member. Only these test assertions are corrected;
no production rejection or lifecycle safeguard is weakened. A fresh focused
rerun and primary complete suite remain required.

The two corrected nodes passed the fresh focused confirmation: **2 passed**,
12 installed Matplotlib/pyparsing warnings, 1.69 seconds, exit 0, no Qt callback
stderr. Nodes: advanced wrong-hash/missing-record refusal and baseline stuck
restart transition. JUnit `results/test_tmp/replay_two_resume_20261011.xml`;
external basetemp `C:/Users/josemsosag/AppData/Local/Temp/replay_two_resume_f90c475008ab401e8c82e85f0640de15/pytest`;
cache `results/test_tmp/pytest_cache_replay_two_resume`. Source and tests remain
frozen for the primary complete-suite run.

### Primary complete-suite dependency failure

The primary ran the approved complete command with `M8_RUN_REAL_DATA_TESTS=0`
and no EXP004 run-directory opt-in, serially in the pinned environment:

```powershell
C:/ProgramData/anaconda3/Scripts/conda.exe run --no-capture-output -n radar-vitals python -m pytest tests -m "not real_data" -q --tb=short --basetemp C:/Users/josemsosag/AppData/Local/Temp/replay_compare_final_20261011_50e9f075e9714415a9c42c5b29ec5a08/pytest -o cache_dir=results/test_tmp/pytest_cache_replay_compare_final_20261011_primary --junitxml=results/test_tmp/replay_compare_final_20261011_primary.xml
```

Observed **3930 passed, 20 failed, 16 skipped, one deselected**, 1391 warnings,
280.35 seconds, exit 1. All **319 replay cases passed**. Test engineer independently
classified all 20 failures: 14 Ahmed PDF dependency failures (M4, 12 M8 figure
cases and the inherited child wrapper), six Joint Estimation PDF dependency
failures (M9). Both ignored literature inputs were missing only in the new
worktree. No code or test was changed, bypassed, or skipped to resolve them.
Existing fixture subprocesses also emitted their own default-cache permission
warnings; the primary invocation used the explicit approved isolated paths.

After completion, primary copied exactly the two existing ignored PDFs from the
original checkout, leaving the source files unchanged, and verified both copies:

- Discovering the Unseen PDF: 1,462,401 bytes, SHA-256
  `2d13bca3fdfcbf249a500622dac0be9ad37e35a6aa880c440ff0c12eb4f8689f`.
- Joint Estimation PDF: 2,447,944 bytes, SHA-256
  `4d2c99f9c1a6fd71e4100fba610e0297cc1a54a0ef6a7558c37a6f0135fc1ddd`.

The failed JUnit remains preserved. A fresh complete rerun uses a new external
parent, cache and JUnit rather than overwriting that report.

### Final complete software gate

Primary's independent serial rerun passed **3950 tests**, with **16 optional
artifact/mode skips, one real_data deselection**, 1790 warnings, 274.92 seconds,
exit 0. All 319 replay cases passed. No sealed-data opt-in was enabled. The
unchanged complete command used external basetemp
`C:/Users/josemsosag/AppData/Local/Temp/replay_compare_final_restored_20261011_0101a914cbc9432580caf05ae5ea6d66/pytest`,
cache `results/test_tmp/pytest_cache_replay_compare_final_restored_20261011`, and
JUnit `results/test_tmp/replay_compare_final_restored_20261011.xml`.
Warnings are existing Matplotlib/pyparsing deprecations, an inherited invalid
escape warning, and ten existing quality-mask overflow warnings. An existing
fixture's child pytest also emitted a default-cache permission warning while
passing; the primary run used the explicit local cache and external basetemp.

The integrated M3/M4 code and adversarial tests meet software acceptance with
the independent static ACCEPT verdict. This gate does not establish actual
recorded UI performance, physical calibration, hardware or clinical acceptance.
The reviewed source will be committed in dependency order: M3 common baseline
application/reference/coverage/UI/verifier, M4 advanced replay and guarded CLI,
then M5 native acceptance driver. Each push receives a remote SHA read-back.

### Native acceptance exposed additional defects

M3/M4/M5 were pushed and read back as `8f2df39a3fea765a90648bc457c187d18f46d6ba`,
`14309f6358b686a78781f57dad4a82e41c39b0bb`, and
`efdb03cc79b20c9392f2aad9128aa8764e24b9c0`. The native full-1x invocation used:

```powershell
C:/ProgramData/anaconda3/Scripts/conda.exe run --no-capture-output -n radar-vitals python scripts/run_replay_compare_acceptance.py --development-data-root C:/Users/josemsosag/Desktop/vitals_radar_3 --scenario full --speed 1 --output-root results/replay_compare/acceptance_full_1x_20261011
```

This is a preserved **failed diagnostic**, not final acceptance. It stopped at
8387 frames / 419.35 seconds, with zero accepted HR/BR/both coverage and null
final source integrity. Observations, both actual screenshots, events, and
executed attempt arrays remain in that run directory.

Independent reviewer raised a new **HIGH, required fix**: a current-identity
successful selector older than one hop never commits its bin because publication
age is checked before selector-state commit. Native selectors took more than
three seconds, repeatedly selecting instead of retaining the successful bin.
The plan's one-hop guard governs numeric publication. Commit a valid bin during
active current-epoch playback, cancel the queued retry, and expire old numerics
without HR/BR admission; EOF/reset/revision/bounds/malformed/out-of-gate cases
must still never commit. The existing lag-61 test encoded the wrong expectation.
This observation supersedes the prior static acceptance for final delivery.

The run then failed on Windows RSS telemetry: concurrent controller and Qt
calls entered the unchanged production getter, which defines a new ctypes
Counters type and changes a cached DLL function's pointer type on each call.
The observed error was `expected LP_Counters instance instead of pointer to
Counters`. Replay-only serialization will guard those calls; the protected
live runtime stays byte-identical. The driver's failed-pass throughput used
the requested limit as numerator and is therefore not a valid failed-run FPS;
the correction uses the last actual telemetry boundary. The stored diagnostic
JSON is not overwritten or presented as final performance evidence.

No production/test file was changed while this native invocation was active.
After it finished, coordinated corrections were authorized. They require new
regressions, independent review, fresh complete-suite acceptance, a new committed
revision and a new full native run. No estimator, age threshold, reference,
physical calibration gate, or raw input is changed to resolve these defects.

The first correction review found an additional **HIGH composite forgery**:
unchanged revision with an invented freeze reason could suppress the mandatory
current-epoch over-age selector commit. No-commit expiry must bind to an actual
preceding denominator-freeze event with its exact reason, or an evidenced epoch
reset with its exact stale-epoch reason; re-signing several mutually consistent
fields cannot manufacture that causal state. A combined-tamper regression is
required in addition to individual field tampering.

The initial all-13 replay correction run observed **321 passed, one failed**, 12
installed deprecation warnings, 32.79 seconds, exit 1, no Qt callback stderr.
JUnit `results/test_tmp/replay_all13_selector_20261011.xml`, cache
`results/test_tmp/pytest_cache_replay_all13_selector`, external basetemp
`C:/Users/josemsosag/AppData/Local/Temp/replay_all13_selector_7b39d81f1a8740b3a82134759271f8ea/pytest`.
The production owner applied the new review finding after a source freeze while
the test invocation was active. Primary stopped further edits, preserved the
report, and treats this as a **mixed-version iteration**, never acceptance.
The failure was the busy-selector coalescing fixture's stale-result disposition.
Further execution requires the reviewer's final verdict and renewed AST/freeze
acknowledgments; the primary will broadcast the active environment grant to all
owners and reviewers. Source/test fixes resume only after the process ends.

Final correction rereview: independent **ACCEPT**, no remaining
BLOCKER/HIGH/MEDIUM. The reviewer checked the required active successful
selector commit versus nonpublication, exact causal EOF/reset no-commit
states, composite false-freeze rejection, and a narrowly proven pre-freeze
current-identity all-DSP-failed rejection. Every over-age publication/admission
remains prohibited. The same narrow exception is passed to the duplicated
application helper; its default remains strict. No busy-coalescing fixture
was weakened. Replay RSS calls share one lock and release it on exceptions;
the partial-run FPS uses the actual last telemetry boundary.

Primary independently parsed all 31 replay Python files, checked LF/whitespace,
and matched all 20 protected hashes. All editing owners renewed their freeze
acknowledgments. The next environment grant is broadcast to implementer,
test engineer and reviewer before execution; no follow-up edit is allowed
until the primary returns the slot and explicitly authorizes it.

### Reviewed correction automated gates (2026-10-11)

The renewed frozen all-13 replay run passed **322 tests**, 12 installed
import-time deprecation warnings, 31.46 s, exit 0, with no Qt callback stderr.
JUnit `results/test_tmp/replay_all13_finalfix_20261011.xml`, cache
`results/test_tmp/pytest_cache_replay_all13_finalfix`, external basetemp
`C:/Users/josemsosag/AppData/Local/Temp/replay_all13_finalfix_c64de4d17d0d4e1cbd848f0874e8ee16/pytest`.
No source/test edits occurred during this invocation.

Primary's fresh serial complete non-real-data suite then passed **3953 tests**,
16 optional skips, one real_data deselection, 1790 existing warnings, 272.17 s,
exit 0. JUnit `results/test_tmp/replay_compare_final_correction_20261011.xml`,
cache `results/test_tmp/pytest_cache_replay_compare_final_correction_20261011`,
external basetemp
`C:/Users/JOSEMS~1/AppData/Local/Temp/replay_compare_final_correction_20261011_c5a6695876104a7883647315fdc5b3c8/pytest`.
The pinned command was `python -m pytest tests -m 'not real_data' -q --tb=short`
with those explicit basetemp/cache/JUnit arguments; M8_RUN_REAL_DATA_TESTS=0 and
EXP004_RUN_DIR absent. All 322 replay cases passed; child-process cache warnings
remain distinct from the explicit primary cache. No owners edited during the
broadcast source/test freeze.

Primary independently rechecked 31 Python ASTs and LF, all 20 protected hashes,
and both existing checkout HEAD/status plus all 23 preserved dirty-file hashes.
All matched. A read-only performance assessment now examines the earlier failed
run's hop-boundary copy costs before the replacement native acceptance.

### Evidence-backed single-copy window correction

Correction commit `e1b2ae8d39ed6e263be144c10fbdd9bd7918cd5c` was pushed and
its exact remote SHA read back. Before starting the replacement native process,
independent review identified a further HIGH responsiveness risk in saved
telemetry: all 47 controller-work measurements above 0.25 s were at 60-frame
hop boundaries; 8247 non-hop rows had no breach (maximum 0.141 s). Hop median
was 0.219 s and maximum 0.453 s. The baseline stacked a fresh owned
157286400-byte window, then AnalysisJob copied it again because it was writable.

No native replacement process had yet started. Primary explicitly cancelled
the announced freeze before granting a coordinated engine-only optimization
and test-only regression. Mark the newly owned stack read-only before passing
it through unchanged AnalysisJob; its existing owned/nonwriteable fast path
avoids the redundant copy. Numeric values, frame ordering, shape/dtype, input
isolation and immutable ownership are unchanged; no DSP or protected file edits.
Independent final verdict ACCEPT, no BLOCKER/HIGH/MEDIUM. The new regression
captures the actual stack object and proves exact identity, ownership,
nonwriteability and first/middle/last values; the previous mutable-source
isolation test remains unchanged. Trace-history costs are monitor-only until
actual replacement full playback.

All 13 focused files passed **323 tests**, 12 installed deprecation warnings,
31.53 s, exit 0, JUnit
`results/test_tmp/replay_owned_window_focus_20261011.xml`, cache
`results/test_tmp/pytest_cache_replay_owned_window_focus_20261011`, external
basetemp `C:/Users/JOSEMS~1/AppData/Local/Temp/replay_owned_window_focus_20261011_f28666d1ba8f4324a9192a894a1141fc/pytest`.
The complete suite follows serially on the same frozen source/tests. An initial
permission-review timeout did not execute the combined test command; the
allowed single retry succeeded. No source/test edits occurred during testing.

The fresh complete single-copy suite passed **3954 tests**, 16 optional skips,
one real_data deselection, 1790 existing warnings, **267.99 s**, exit 0.
All 323 replay cases passed. JUnit
`results/test_tmp/replay_owned_window_final_20261011.xml`, cache
`results/test_tmp/pytest_cache_replay_owned_window_final_20261011`, external
basetemp `C:/Users/JOSEMS~1/AppData/Local/Temp/replay_owned_window_final_20261011_544db8d4fae5417da56b4965b0d036d2/pytest`.
The command was the same complete `python -m pytest tests -m 'not real_data'
-q --tb=short` with those explicit paths in the pinned serial environment;
sealed opt-in stayed disabled. Primary rechecked all 31 ASTs/LF and all 20
protected source hashes. No source/test edit occurred during either test run.
This reviewed optimization is committed separately before actual native replay.

### Completed replacement replay: artifacts valid, responsiveness not accepted

Committed `b9ccd3432fc2a46e338d476fb6b2712246935344` completed all **12005**
source/frame-telemetry rows and 600.25 recorded seconds, with clean shutdown,
under `results/replay_compare/acceptance_full_1x_corrected_20261011`.
Initial, streamed and final ADC hashes all match the registered hash. The
independent guarded verifier exited 0 with accepted=true, source_binding_complete=true,
and no internal/source issues; saved output and exit status are
`verification_pass_0001.txt` and `verification_pass_0001.exit.txt` outside the
pass manifest. This establishes artifact consistency, not native responsiveness.

Native driver exit **1**: controller work max **0.375 s**, UI snapshot age max
**0.422 s**, and Qt heartbeat max **0.641 s** failed the unchanged **0.25 s**
acceptance gate (162 heartbeat breaches). UI callback max was **0.063 s**, with
no callback breach. Actual playback wall time **600.36 s**, measured throughput
**19.996335532014104 frames/s**, selector **4.468 s**. Coverage was HR **178/12005**,
positive BR **9643/12005**, both **178/12005**, quiet **0**, and each PR/PI-qualified
PR/RRp availability **11845/12005**. These are genuine diagnostic availability
counts, not accuracy or final acceptance. Both real dashboard PNGs are retained.

Driver RSS samples peaked at **1156190208 bytes**. Steady early/late quarter
medians were **635920384 / 718245888 bytes**, difference **82325504 bytes**, late
endpoint growth **207511.35263675736 bytes/s**. This is not an accepted memory
plateau. Review found all controller breaches at 60-frame window-assembly
boundaries; non-hop maximum was 0.110 s. The single-copy correction removed one
copy but did not remove synchronous materialization from the controller.
No code or tests changed during this complete invocation or verification.

Next required revision is independently plan-reviewed worker-only window
materialization: the controller captures exact individually owned/read-only
frames in a bounded immutable tuple; the unchanged scheduler carries metadata
and that recipe, and only the existing analysis worker builds the final owned
read-only array. Cancellation/coalescing release recipes by object lifetime.
No protected source, DSP, threshold, calibration/reference gate or raw input
changes. Total analysis latency must include assembly. New deterministic
ownership/order/release/lifecycle regressions, independent code review, focused
and complete tests, commit/push, and a new full native run remain required.

### Revised worker input and stable telemetry: research before implementation

Independent plan reviewer returned READY with required exclusive request/execution
representations, base validation, truthful scheduler strong-reference lifetime,
assembly-failure evidence, timing including assembly, idle-worker release, and
objective ownership/order/coalescing/lifecycle tests. These requirements are
incorporated in plan section 6 before implementation authorization.

Independent telemetry review confirmed per-call type creation/cache retention;
research used primary sources: [Python 3.11 ctypes POINTER documentation](https://docs.python.org/3.11/library/ctypes.html#ctypes.POINTER),
[CPython cache-growth issue #100926](https://github.com/python/cpython/issues/100926),
[Microsoft GetProcessMemoryInfo](https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-getprocessmemoryinfo),
[PROCESS_MEMORY_COUNTERS](https://learn.microsoft.com/en-us/windows/win32/api/psapi/ns-psapi-process_memory_counters),
and [GetCurrentProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getcurrentprocess).
The pointer-type cache retains each distinct local Structure type. This defect
can explain growth but is not proof that every RSS change came from telemetry.
A stable replay-only class/API binding under the existing lock preserves current
working-set meaning and protected-file isolation; no new dependency or scientific
method change. Final memory behavior requires fresh observed playback and review.

### Worker/RSS frozen verification iteration

Independent code reviewer accepted the production and test diff with no material
findings. Primary rechecked all 20 protected hashes and both preserved checkout
HEAD/status plus 23 dirty-file hashes: unchanged. Initial focused invocation
used a Windows wildcard not expanded by PowerShell (zero tests); second lacked
the required external temporary parent (141 passed, 192 fixture errors). These
setup reports remain distinct and are not acceptance. The corrected invocation
reported 332 passed, one failed, 12 warnings in 30.15 s, JUnit
`results/test_tmp/replay_worker_rss_focus_20261011_49c6b481c7814c64bc31bad30d13e7ea.xml`.
The failing stale-result evidence fixture called production analysis directly
with the new request form, bypassing worker materialization. Test owner is
adapting only that fixture to the real execution form; all lineage/tamper
assertions remain. Source/tests were frozen throughout each invocation.

Independent follow-up fixture review: ACCEPT. Original request stays scheduler
active identity; materialization only precedes the two direct analysis calls.
Reset timing, stale epoch, missing-reset and forged-publication rejection
assertions are intact. Frozen focused rerun: **333 passed, 12 warnings in
30.23 s**, exit 0. JUnit:
`results/test_tmp/replay_worker_rss_focus_final_20261011_51b920818e83403f877d3e33c3d6e0f9.xml`.
The matching unique suffix identifies repository-local cache and external
`C:/Users/JOSEMS~1/AppData/Local/Temp/<suffix>/pytest` basetemp. Parent explicitly
created its parent directory first. Complete frozen non-real-data suite follows.

Complete frozen corrected suite: **3964 passed, 16 skipped, one real_data
deselected, 1790 warnings in 262.34 s**, exit 0. JUnit:
`results/test_tmp/replay_worker_rss_full_20261011_b346fae5d69a4fd2ba9882438e0f79e8.xml`.
Unique matching cache suffix and external basetemp under
`C:/Users/JOSEMS~1/AppData/Local/Temp/replay_worker_rss_full_20261011_b346fae5d69a4fd2ba9882438e0f79e8/pytest`.
Command: pinned conda run python -m pytest tests -m 'not real_data' -q
--tb=short --basetemp <external>/pytest -o cache_dir=<unique results/test_tmp cache>
--junitxml <unique results/test_tmp XML>; M8_RUN_REAL_DATA_TESTS=0 and
EXP004_RUN_DIR absent. Tests/source remained frozen; primary AST/LF check
passed all 31 replay Python files, whitespace clean. Existing warnings and
child cache-permission warning retained. All 20 protected hashes and both
preserved checkout HEAD/status/23 dirty-file hashes passed. This is software
verification; native recorded-playback acceptance remains a separate gate.

Reviewed worker correction committed as
`eed95f5f1323b1ba1fafaae712ac4b5c91f5badf`. Initial push failed transient DNS
resolution, with no remote change. Stable Windows telemetry committed separately
as `cfcb2c00a59e72e2164eebd1e38909acbeaadffe`. Retried publication succeeded
for both commits; `git ls-remote origin refs/heads/codex/replay-masimo-compare`
read back exact cfcb2c00a59e72e2164eebd1e38909acbeaadffe. Source scope clean
before fresh full native run under
`results/replay_compare/acceptance_full_1x_worker_20261011`; source/tests frozen
and pinned environment exclusively owned by primary during playback.

### Worker correction native diagnostic: complete artifacts, UI gate failed

The full 1x native run at cfcb2c00a59e72e2164eebd1e38909acbeaadffe completed
all 12005 frames with clean shutdown and exact source accounting. Artifact
verifier exited 0: accepted true, source_binding_complete true, no internal or
source issues. Preserved run: `results/replay_compare/acceptance_full_1x_worker_20261011`;
verifier text/exit live outside its pass manifest. Native driver exited 1.
Controller work max 0.234 s, no controller breaches; UI snapshot age max 0.531 s,
Qt heartbeat max 0.640 s with 158 breaches, UI callback max 0.500 s with three
breaches. No gate relaxed. Playback wall 600.281 s, throughput
19.9989671503846 frames/s. HR 180/12005, BR 9618/12005, both 180/12005,
quiet zero; reference PR/qualified PR/RRp each 11845/12005. These are diagnostic
availability counts, not final performance acceptance or accuracy.
RSS driver peak 1101815808 bytes; early/late steady medians 458790912/473315328,
delta 14524416 bytes, endpoint slope 106670.72197087832 bytes/s. The per-call
ctypes growth was removed, but final memory interpretation needs independent
review alongside bounded signal ownership and retained display/audit rows.
Actual during/final screenshots retained; primary during visual check confirmed
explicit held/accepted states, reference isolation warnings and legible plots.
Remaining UI stalls persist around analysis; primary is profiling unchanged
worker calls before an evidence-backed revision. A diagnostic runner's initial
argument-interface error performed no replay and saved zero call records;
corrected diagnostic uses a fresh log and distinct run directory. No source or
tests changed during full playback, verification, or profiling.

The unchanged short native diagnostic completed 800 frames at 1x under
`results/replay_compare/diagnostic_gil_native_20261011` with profile hooks on
ReplayAnalysis only. The smoke driver exits 0 because full-run timing gates
apply to full scenario; its Qt maximum was 0.516 s, so this is **not** responsive
acceptance. Long-call log:
`results/test_tmp/replay_gil_calls_valid_20261011.json` (103 records). Queue
waits are intentional. Non-wait C FFT calls were about 0.093-0.109 s; stack
reached 0.187 s and unchanged per-bin DSP 0.25-0.42 s; selector with profiling
4.891 s. These timings locate contention but do not prove C-call GIL ownership.
Independent review found 165/190 analyses over 0.25 s versus 158 heartbeat
breaches, and a separate UI stall when analysis was inactive. Residual RSS
growth is consistent with bounded per-frame display/audit histories, not the
removed ctypes type-cache leak; it is not labelled a plateau.
Direct ndarray-fast-path edits in protected window_pipeline.py are excluded by
the owner's explicit byte-identity instruction. A replay-only isolated analysis
process and incremental UI work are being independently plan-reviewed before
implementation. DSP, references, thresholds, windows/hops and calibration remain
unchanged.

### Process/UI revision independent research and first plan review

Independent plan review initially returned **NOT READY**. HIGH findings: exact
GUI prefix/redraw interface, fresh per-generation backend factory, and typed
durable fatal-result ordering. MEDIUM: single shutdown deadline, complete result
transport, SHM ownership; LOW: telemetry outside 20 Hz path. Primary incorporated
all requirements in plan section 7.1 before requesting re-review. No production
implementation began while the plan was not ready.

Independent research recommends one persistent spawned child and one fixed
parent-owned shared-memory window; preserve exact inputs and complete
ExecutedAnalysis, bound transport and lifecycle, and observe combined memory.
Primary API sources: [Python 3.11 multiprocessing](https://docs.python.org/3.11/library/multiprocessing.html#contexts-and-start-methods),
[spawn import restrictions](https://docs.python.org/3.11/library/multiprocessing.html#the-spawn-and-forkserver-start-methods),
[SharedMemory](https://docs.python.org/3.11/library/multiprocessing.shared_memory.html),
[CPython 3.11 shared-memory implementation](https://github.com/python/cpython/blob/v3.11.11/Lib/multiprocessing/shared_memory.py),
[Pipe/Connection](https://docs.python.org/3.11/library/multiprocessing.html#pipes-and-queues),
[termination semantics](https://docs.python.org/3.11/library/multiprocessing.html#multiprocessing.Process.terminate),
and [Microsoft working-set meaning](https://learn.microsoft.com/en-us/windows/win32/psapi/process-memory-usage-information).
Use get_context('spawn'), module-level target and safe main import. Windows
3.11 uses named mmap; all handles must close, unlink is no-op and 3.13 track
argument is unavailable. Parent alone owns creation/unlink. Terminal terminate
can corrupt transport, so never reuse it. Shared-page counts may be duplicated
in summed RSS. Read-only NumPy flags are cooperative, not a sandbox. Synthetic
API correctness does not guarantee native timing. The protected-source fast
path proposed by test review is excluded by the owner's explicit byte-identity
rule; process isolation retains all scientific files unchanged.

Independent process/UI plan re-review returned READY WITH MINOR CHANGES.
Required exact full-trace endpoint completeness and objective inclusive IPC
limits are appended before implementation authorization. Exact module path
process_analysis.py; control<=65536 bytes, result<=16777216 bytes. Full-result
transport remains mandatory. No unresolved architecture finding remains;
independent code review and actual native acceptance are still required.

### Process/UI implementation review, first pass (2026-10-11)

Independent review found blocking draft defects before environment execution:
multiple fresh shutdown deadlines; possible duplicate or undrained fatal evidence;
startup endpoint cleanup outside the exception path; cleanup/containment truth and
concurrent close races; a restart handshake under the manager lock; missing manager
lifecycle properties; child geometry/dtype validation and validation-before-copy;
child death between its last result and EOF being treated as clean; and missing
partial fatal transport timings/active request sequence. These are assigned to the
production owner with deterministic failure-path tests owned independently.

The UI reviewer accepted the prefix/chunk/NaN design but required validation of
all displayed scalar payloads before any mutation. The UI owner is correcting
that atomicity issue. Primary driver corrections expose sampled owned-child
count, distinct restart PIDs and final exit code, and stream timestamped UI
poll/heartbeat intervals outside the pass manifest. The strict 0.25 s full-run
limits are unchanged. No pinned environment execution or acceptance claim is
authorized until owners freeze and independent final review resolves findings.

The reviewer also required a production-native telemetry gate because the
scientific artifact verifier manifests performance.jsonl without validating its
timing schema. The primary added one-to-one ordered binding to the attempt CSV,
exact contiguous sequence, finite nonnegative complete spans, inclusive message
bounds, total/round-trip/parent-span nesting (1 ns floating-point tolerance), and
an explicit normal fatal-error field of None. The test owner added adversarial
tests. Injected-thread scientific artifact compatibility is preserved. Final
review additionally required retaining available child phase timings on fatal
replies; that correction remains under the production owner before final freeze.

Production and test owners subsequently corrected the available child fatal
spans, retryable retained-resource cleanup, strict UI real-scalar/state/mode
contracts, and their regression fixtures. The pure frozen job/materializer and
baseline analyzer builder now live in process_analysis.py with engine aliases;
the actual child target no longer imports engine/source/application. This is a
stronger import boundary, not evidence of prior hardware operation or a changed
scientific result. Every normal analysis timing row explicitly records
fatal_backend_error=None. All owners issued fresh STATIC_READY/FREEZE_ACK.
Primary AST/LF checks passed 32 replay source/test files and integrated diff-check
passed; the exact hashes are saved in ignored
results/test_tmp/process_ui_freeze_20261011.json. No dynamic test has yet run on
this revision; independent final static verdict is still required.

### Process/UI focused execution and observed corrections (2026-10-11)

Independent final static review accepted the frozen revision before execution;
dynamic verification superseded that provisional verdict. Primary alone ran the
pinned environment with isolated created external basetemp and unique cache,
JUnit and console files. Result: **483 passed, 7 failed, 12 warnings, 37.31 s**.
Retained evidence: results/test_tmp/replay_process_ui_focus_20261011_fde9784698d04b598cc5441653c18515
(.xml, .console.txt, .exit.txt); exact run source/test hashes:
results/test_tmp/process_ui_focus_failed_source_20261011.json.

Independent test and code reviewers agreed on two HIGH production defects:
Windows attached SHM capacity is page-rounded, and candidate-left prefix equality
admits an attribute-equal mutable SimpleNamespace substitute. Five remaining
failures are fixtures: unchanged NaN cleaning yields completed scientific rejection,
stricter geometry message, fake start timeout_s signature, and two invalid
preparing snapshots rejected by the constructor's initial poll. None justify
changing scientific processing, deleting adversarial cases or weakening gates.

The primary pinned synthetic mapping probe (no recording or hardware) confirmed
requested/created 38400, attached 40960, page size 4096, Python 3.11.15. Script and
JSON: results/test_tmp/probe_shared_memory_capacity_20261011.py and
results/test_tmp/shared_memory_capacity_probe_20261011.json. An initial multiline
conda -c invocation failed in its launcher before Python execution; the saved
script subsequently exited zero and closed/unlinked both owned handles.
Research independently tied the observation to CPython's Windows VirtualQuerySize
attach path and documented page allocation. Full registered window bytes happen
to be page-aligned; the compact valid geometry exposed the platform bug.

Plan section 7.2 records exact platform-normalized capacity with unchanged exact
scientific byte count and exact retained object identity for prior UI samples.
Owners remain frozen pending independent narrow plan review. Native acceptance
and the complete suite are still required on the corrected revision.

Independent narrow section-7.2 plan review returned **READY** before scoped
implementation grants. It required unchanged parent creator size, strict child
platform-normalized capacity, strong prior UI tuple references plus exact cached
integer identities, and both mutable and reconstructed-frozen substitutions in
tests. Production owners then froze process_analysis.py (SHA-256
a5cf571640783309fb8b3820c2296f47ce6a3d97848e41a8229f245fe1494700) and
ui.py (ed27647ec33c284e21ee54c4ca90314e8c1b737f19b244684c5a4b6ed264e9ed).
Independent production static re-review found no material defect in these fixes;
test corrections and dynamic gates are still pending.

All three scoped test files then froze, and independent final static review
returned **ACCEPT**, with no BLOCKER/HIGH/MEDIUM. It confirmed bad-capacity
rejection before buffer/analyzer access, exact non-page-aligned callback payload,
two caught scientific failures on the same child, handled-NaN exact equivalence,
and mutable plus reconstructed-value-equal prefix attacks preserving every UI
cache/rendered state. The primary static freeze audit passed all 32 AST/LF files,
20 protected hashes, both preserved HEAD/status records and all 23 local files.
Snapshot: results/test_tmp/process_ui_freeze_ready_20261011.json. All source/tests
remain frozen; the primary alone owns the focused and complete environment runs.

Corrected focused execution: **508 passed, 3 failed, 12 warnings, 38.99 s**.
Retained tag: results/test_tmp/replay_process_ui_focus_20261011_fd3647f1ba9148c1958549d1d010fb13
(.xml, .console.txt, .exit.txt). All newly added capacity and prefix-forgery tests
passed. Remaining failures expose fixture mismatches: dtype rejection message,
fake assuming a five-second start timeout in a one-second-configured runtime,
and sparse-poll fixture recreating prior samples instead of retaining controller
objects. Production remains frozen pending independent classification and narrow
test correction. Post-run frozen hashes and all preservation checks passed:
results/test_tmp/process_ui_focus_second_failed_20261011.json.

Independent review classified all three residual failures as fixture defects and
approved only the precise dtype diagnostic, configured 1.0-second fake timeout,
and retention of first.trace_samples[0] in the sparse-poll fixture. Production
did not change. After scoped corrections and renewed freeze, independent review
again returned **ACCEPT**, no BLOCKER/HIGH/MEDIUM. Primary static preservation and
AST/LF checks passed; new frozen snapshot:
results/test_tmp/process_ui_freeze_ready2_20261011.json. A fresh focused rerun
precedes the complete suite; previous failures and their exact assertions remain
in their retained logs.

Fresh focused gate **PASSED: 511 tests, 12 warnings, 38.53 s**, exit zero.
Tag: results/test_tmp/replay_process_ui_focus_20261011_48c3f4a2f27c471d82318f751b8ef7d2
(.xml, .console.txt, .exit.txt); external created parent:
C:/Users/JOSEMS~1/AppData/Local/Temp/replay_process_ui_focus_20261011_48c3f4a2f27c471d82318f751b8ef7d2/pytest.
Post-run hashes matched the exact freeze, and all preservation checks passed:
results/test_tmp/process_ui_focus_pass_preserved_20261011.json. The primary then
started the complete non-real-data suite serially, retaining all previous failures.

Complete gate **PASSED: 4142 passed, 16 skipped, one real_data deselected,
1790 warnings, 275.96 s**, exit zero. Tag:
results/test_tmp/replay_process_ui_full_20261011_f5646d8a5f5748f8bb97d125211140b1
(.xml, .console.txt, .exit.txt); JUnit SHA-256:
927c068cdce2e94b80feb15f376a92b5be2903f68a05ddd43907109e98ec5c08.
JUnit independently reports 4158 tests, 16 skips, zero failures/errors. Skips are
optional all-bin/scoring artifacts, absent other real captures and old replay
artifacts; neither opt-in/sealed authorization was enabled. Existing deprecation,
quality-mask overflow and nested-child default-cache warnings remain recorded.
Exact serial runner: results/test_tmp/run_replay_tests_20261011.ps1 -Scope full;
created basetemp parent C:/Users/JOSEMS~1/AppData/Local/Temp/replay_process_ui_full_20261011_f5646d8a5f5748f8bb97d125211140b1,
local cache/JUnit use the same unique tag. Post-run all 32 frozen hashes,
20 protected files, both checkout HEAD/status records and 23 preserved local
files passed (process_ui_full_pass_preserved_20261011.json). Independent reviewer
accepted the automated software gate; actual native acceptance remains required.

Reviewed process/UI/driver milestones were committed separately and each pushed
SHA read back: 36fb21dcadea70f58e72013fe9d172e6275cf103,
1700667605edb64a58c71b52dfafb1e2d05fb674,
73a48e13718f35beb24347a861fd2541db907e97. Scientific-source status was clean and
committed bytes matched the tested freeze; original checkouts remained preserved.

Fresh native 1x short diagnostic at 73a48e1 completed 800/800 source/telemetry
frames, child exit zero, clean shutdown and complete process spans. Its smoke
driver exited zero but does not enforce the full 0.25-second gate. Primary
explicitly inspected all metrics and **refused timing acceptance**: controller
max 0.031 s, UI poll/age max 0.454 s, Qt heartbeat max 0.547 s with 19 breaches.
Output: results/replay_compare/acceptance_smoke_1x_process_20261011. HR coverage
0/800, BR 74/800, both zero, quiet zero; no desired coverage target was applied.
Most heartbeat stalls precede the first analysis, including boundary zero.
All memory-sample spans round to zero at monotonic clock resolution, so the
recorded evidence does not implicate RSS calls. Parent sampled peak 615780352
bytes, combined sampled peak 1194696704, child lifetime peak 743813120.
Sources remain frozen; primary is profiling main-thread Python/native calls in
a separately labelled diagnostic run before any further implementation revision.

Native diagnostic evidence at frozen 73a48e1 (all scripts/outputs retained):
results/test_tmp/profile_native_gui_process_20261011.py and its JSON profile,
results/replay_compare/diagnostic_gui_process_profile_20261011 identify native
QProgressBar.setValue calls of 0.399–0.471 s; screenshot totals 0.246–0.323 s
include PNG encoding 0.205–0.214 s, grab 0.040–0.052 s and an event drain.
results/test_tmp/profile_native_qt_events_20261011.py and JSON profile,
results/replay_compare/diagnostic_qt_events_process_20261011 attribute
0.314–0.516 s UpdateRequest to the whole dashboard at DPR 2.5. The captured
image is 3450x2108 pixels. Controller work remains at most 0.032 s in that
probe. Independent reviewer and primary-source Qt research corroborate the
synchronous progress repaint/backing-store path; this is not scientific tuning.

The identical-setter A/B diagnostic
results/test_tmp/diagnostic_gui_memo_setters_20261011.py skipped 22857 unchanged
calls, but results/replay_compare/diagnostic_gui_memo_setters_20261011 still
reports UI callback 0.454 s, snapshot age 0.469 s and Qt heartbeat 0.750 s with
19 breaches. All 800 frames, clean child exit and process telemetry remain
complete. This probe is NOT accepted production behavior, despite smoke exit
zero. All interventions are identified by their ignored diagnostic script;
the clean tracked source hash alone does not reproduce the intervention.

Evidence-backed amendment §7.3 proposes a passive deferred progress widget and
bounded capture of two detached QImages followed by PNG encoding after the
event loop ends. No extra writer thread is necessary for these two artifacts.
Independent plan review precedes tracked edits. Scientific files, source/tests,
timing limits and all existing checkouts remain frozen/preserved.

Independent §7.3 plan review: READY WITH MINOR CHANGES; code reviewer found a
HIGH defect in the first draft's impossible bounded-thread-join/no-orphan claim.
The primary independently simplified it before implementation: exactly two
detached QImages, sequential same-thread PNG encoding after the event loop ends,
no new writer thread. Final code-review plan verdict READY, no BLOCKER/HIGH/
MEDIUM remaining. Plan review's progress range/reset/accessibility, cache
coherence, destination-before-capture, fixed slots, real PNG reopen/hash, timer
stop and failure-release details were incorporated before scoped grants.
Python expert owns ui.py; separate Python expert owns only native driver; test
engineer owns UI/driver tests. Primary owns integration, retained serial tests,
native checks, Git and documentation truth. Reviewers do not implement edits.
Prepared ignored deferred-progress probe is retained but UNEXECUTED because
tracked edits were already granted; no runtime intervention counts as acceptance.

§7.3 final independent static verdict **ACCEPT**, no material findings. Reviewer
found two initial test gaps (real pixel detachment and unchanged setter/X-range
suppression); test owner added both before freeze. Frozen production SHA-256:
ui.py d7c6207f8a4c9419eac4f6ec9ebbe8a37eb51c83c5f7c005ba5c610fb6bf8314;
native driver fe5d4a7f2652c82218420efb12eb65dee64d06787048f17ff105167fbe26f84f.
Frozen tests: UI 023bd2b408a0f76972083cac8002195b38502a15c0c2914ba822cba830d0eda5;
acceptance f2c01d28388c613158d90e4a357db0bd2ebbb819810cf3c44ad8af53e78b10b4.
Primary AST/LF32/protected20/preserved2-checkout23-file audit passed:
results/test_tmp/qt_display_freeze_ready_20261011.json. Primary starts focused
serial execution; all editing owners remain frozen and reviewers independent.

§7.3 first focused execution retained **526 passed, one failed, 12 warnings,
39.70 s**, exit one. Tag:
results/test_tmp/replay_process_ui_focus_20261011_712d7bbec45445a3929830dc573484c5
(.xml/.console.txt/.exit.txt). The pause progress-flush assertion is positioned
after a running poll but before the fixture supplies its paused snapshot;
independent classification precedes any correction. Console additionally prints
pyqtgraph LabelItem partially-initialized sizeHint callback errors; these remain
an explicit investigation gate even though pytest does not fail them. Post-run
all 32 frozen hashes, 20 protected files, two checkouts and 23 local files passed:
results/test_tmp/qt_display_focus_failed_preserved_20261011.json. No production
or test edits are granted until the independent finding is classified.

Independent classification: the lone failure is a misplaced test assertion,
and the 16 callbacks match eight added offscreen dashboards with two plots.
Pinned pyqtgraph 0.13.7 LabelItem calls GraphicsWidget base before initializing
its instance _sizeHint; Qt may reenter virtual sizeHint during that base call.
Both independent reviewer and UI owner corroborated installed-source ordering;
retained native logs have no equivalent error. Grant is TEST ONLY: assert paused
after the paused snapshot/poll, retain a no-new-flush assertion for the running
poll, and provide a documented offscreen-construction class-level empty cache
fallback matching LabelItem's own unknown-hint zero-size behavior. Instance
initialization replaces it; pytest restores it. Do not suppress exceptions or
stderr, alter installed libraries, or patch production. Production remains
frozen. Fresh targeted/focused/full execution and native callback inspection
remain required; the original failed console is preserved.

Test-only correction independently re-reviewed **ACCEPT**; production hashes and
acceptance-test hash remain unchanged. UI test SHA-256 now
5c8a1f0a888b41bc7616dafcdabf2103c72d1da56eeff8339261f68c7255a0a5.
Refreeze audit: results/test_tmp/qt_display_freeze_ready2_20261011.json.
Fresh verbose targeted UI execution **PASSED 34 tests, 12 warnings, 3.10 s**,
exit zero, no Traceback/AttributeError/LabelItem/FAILED in console. Retained tag:
results/test_tmp/replay_qt_display_ui_20261011_df4b2d5ef17e4d84a36e1932fd3bfc06.
Post-run 32-hash/protected20/preserved2-checkout23-file audit passed:
results/test_tmp/qt_display_ui_pass_preserved_20261011.json. The primary proceeds
to full focused and complete serial suites under this exact new freeze.

The quiet focused rerun then stalled at 95%; retained tag
results/test_tmp/replay_qt_display_focus_20261011_5625bd2c1d574d71bf2ec1f187c25cc0.
No pass is claimed. Primary verified command-tag/owned PID 25716 and no remaining
children before terminating only that process; conda/runner retained exit one.
A fresh unchanged-source verbose diagnostic with built-in
faulthandler_timeout=20 reproduced the stall and retained a stack at ui.py:1292
QImage.save in the force-flush test, after the preceding UI cases passed.
Tag: results/test_tmp/replay_qt_display_diagnostic_20261011_64da87a897ea405cada382742ccbd144.
It printed no LabelItem callback error. Primary verified the exact tag and pinned
executable for owned pytest PID26992, checked zero children and terminated it;
exit one and its stack remain. All32 source/test hashes and protected/preserved
files still matched (qt_display_stall_preserved_20261011.json).

Independent reviewer and test owner identified two fixture problems: mutating
ui.time.monotonic mutates the singleton stdlib clock for every imported library;
and the force-flush case still encodes PNG in the path now explicitly defined
as in-loop detached image capture. Grant TEST ONLY: replace the UI module's time
binding with a local deterministic proxy and exercise real detached capture,
including non-null/dimensions/dirty-plot/progress assertions. Earlier genuine
synchronous PNG saving and driver encoding/reopen/hash coverage remain required.
The exact native-code reason for the mixed-suite encoding stall is not claimed.
Production remains frozen; targeted/focused/full reruns and actual native PNG
completion are still required, with no skip, timeout relaxation or suppression.

The module-local clock/capture-only correction received independent STATIC
ACCEPT at UI-test SHA 3d48256176284fba8166d003310772a42727d16c0686a4693233947dbc2345ca.
Ready3 audit verified 32 AST/LF source files, 20 protected files and both preserved
checkouts/23 local files. Targeted UI tag
replay_qt_display_ui_20261011_61ba0337e481470cb3240a863a14db37 passed
34 tests, 12 warnings, 2.76 s, exit zero. Focused diagnostic tag
replay_qt_display_diagnostic_20261011_f9315ab7e6b34ceeaa3aa657f6d36b16
passed all 527 assertions, 12 warnings, 42.51 s, but exited -1073741819
(Windows access violation) after its summary. This is NOT a passing gate.
No callback traceback was printed; post-run freeze/preservation audit matched.
The required complete suite is now running unchanged sources with Python crash
diagnostics enabled. Reviewers are investigating test Qt object cleanup;
production and tests remain frozen during execution.

The unchanged ready3 complete suite finished exit zero: 4158 passed, 16 optional
skips, one real_data deselected, 1790 warnings in 315.99 s. Retained tag:
replay_qt_display_full_20261011_989205c53d7e4139b855fb2a2d840229.
It used the isolated serial runner with PYTHONFAULTHANDLER=1. This does not erase
the preceding focused process access violation. Independent reviewer/test owner
identified an asymmetric Qt test teardown: the no-event-drain capture test kept
its QApplication.processEvents/window-method patches active through close and
did not process restored events. Root grants only a scoped monkeypatch.context
around the capture assertions, restoring real methods before close/processEvents
cleanup. No assertion, production behavior, skip, timeout or gate is changed.
This is an evidence-backed lifecycle correction/probe; the exact native reason
for the preceding access violation is not established. Renewed independent review
and fresh focused/complete process exits are required.

Scoped teardown correction independently STATIC ACCEPT; frozen UI test SHA
00e47ba01e458ffdf598d725fd6419c538d891b7ee7ec5242535d94f8b6999f0.
Ready3-to-ready4 comparison changes only this test file; UI/driver/acceptance
tests remain byte-identical. Ready4 preservation/AST/LF audit passes. The prior
post-full ready3 comparison was attempted after the authorized test edit had
begun and rejected the expected changed test hash; it is not a preservation
failure or evidence of an in-run mutation. New targeted UI tag
replay_qt_display_ui_20261011_a58d4f0af8e14defa9ba76143fa79221
passes 34 tests, 12 warnings, 2.69 s, exit zero. Focused/complete reruns pending.

Ready4 diagnostic tag replay_qt_display_diagnostic_20261011_280c75c655b441cba248f3ec48fdaba8
again passed 527 assertions/12 warnings/41.61 s but exited -1073741819.
Normal focused gate without the optional watchdog instrumentation also reproduced
the post-summary access violation (tag replay_qt_display_focus_20261011_9b14f3ec0cf948319ded3c948ec7ac7d).
Thus restored capture teardown is useful cleanup but does not establish the crash
cause. Task-specific Windows crash records identify pinned Qt6Core.dll 6.7.3;
no acceptance is claimed. Independent review proposes isolating the one test
calling QThread.currentThread from a Python threading.Thread (adopted Qt thread).
An ignored diagnostic runner now supports explicit predecessor slices and one-test
exclusion. Reduced runs are diagnosis only, never replacements for full gates.

All-minus-wrong-thread diagnostic tag
replay_qt_display_thread_exclusion_20261011_48240f1e73db4eb0a09760f6a7966afb
passed 526 assertions/one deselected/46.11 s then also crashed. This falsifies
the claim that the adopted-thread case alone explains the failure; no such
correction is authorized from this evidence. Ordered group diagnostics both
exit zero with every UI case intact: native predecessor group (application,
engine, process_analysis, runtime, telemetry) 201 passed/28.39 s, tag
replay_qt_display_native_group_20261011_d929755a81204f0db16958ce00386637;
complementary artifact/reference group 360 passed/16.32 s, tag
replay_qt_display_pure_group_20261011_eab9eb3f31e54a5bb399c12997656375.
Both report 12 warnings. All32 hashes/protected20/preserved23 files still match
ready4. Review now examines explicit lazy QApplication/widget lifetime in tests;
combined gate remains unsuccessful, not replaced by group results.

Independent reviewer accepts a narrowly owned test-lifecycle probe: a lazy
module-scoped QApplication fixture, explicitly requested only by actual GUI
tests, holds one strong application reference while dashboards exist. Save/set
offscreen platform before its first native import, restore after teardown.
Close/deleteLater remaining owned top-level widgets and explicitly deliver
DeferredDelete before processing events/releasing application ownership. No
application quit, forced native deletion, garbage-collection hack, suppression,
skip, test weakening or production edit. Root granted only UI-test fixture and
application-reference replacement; independent re-review and all527 gate remain
required. This does not assert an unsymbolized crash cause.

The fixture review found and resolved one blocker before execution: platform
restoration must enclose optional Qt imports and QApplication creation, not only
the yield. Missing dependency/import/setup failure now restores the original
environment too. Independent renewed STATIC ACCEPT at UI-test SHA
d6c6d89efd61527ceb808b976fe0c7762cf864dc44a6726a1cf7d4c883b7c6ee;
13 real GUI functions request the fixture, the lazy import test does not.
Ready5 32-source AST/LF/protected20/preserved2-checkout23-file audit passes.
Production and driver hashes remain d7c6207f and fe5d4a7f respectively.

Ready5 all527 focused tag replay_qt_display_focus_20261011_40bbe49dc36d4e4c81b66301455e2002
passed assertions/12 warnings/43.47 s then also exited unsuccessfully. Explicit
application ownership is correct fixture hygiene but is not a demonstrated
resolution. Additive cross-group diagnosis (native group plus acceptance,
advanced, CLI, coverage and evidence modules; all UI cases) localized a native
abort to fixture widget.close(), exit3, with retained Python crash stack:
replay_qt_display_cross_a_20261011_e304d8e4cfa043969d808a0638b38e70.
The source stays frozen for an uncaptured-message rerun and narrower pairing.
Static PE export landmarks for Qt crash offsets are retained in ignored
qt_export_nearest_20261011.py; those are not symbols or a claimed causal stack.

Cross-A verbose reproduces the fixture close abort without a preceding Qt text
diagnostic (tag replay_qt_display_cross_a_verbose_20261011_1543c3e97d544a3babcbeae1a844f89b).
The narrower acceptance/process_analysis/UI combination passes 198 assertions
in 10.90 s then exits unsuccessfully, tag
replay_qt_display_acceptance_process_20261011_ac649ffba623419e851b549d5927d612.
An ignored close-identity logging plugin in cross-A records internal QMenu,
QFrame and ViewBoxMenu popup windows in topLevelWidgets; the last QFrame close
precedes the native abort. Tag:
replay_qt_display_cross_a_close_20261011_176fbe96d83646058b19d3db79caad7a.
These internal parent-owned windows defeat the proposed blanket ownership
cleanup. Independent reviewer rejects that fixture as remediation; root grants
its exact removal back to ready4 UI-test bytes, preserving the previously
accepted scoped capture cleanup and all assertions. No production change yet.
The next decisive diagnostic excludes only the three constructor-compatibility
cases, whose two invalid initial snapshots currently raise after Qt widget/timer
construction. Any reduced result remains diagnosis, not acceptance.

Test fixture removal exactly restores ready4 (00e47ba0), all32-hash preservation
comparison passes. All-527 minus the three constructor-compatibility rows exits
zero: 524 passed/three deselected/12 warnings/42.57 s, tag
replay_qt_display_constructor_exclusion_20261011_11f121e79fd94d04a80b772f420279e3.
The narrowed acceptance/process_analysis plus only those three UI rows ALSO
exits zero: 167 passed/31 deselected/12 warnings/9.53 s, tag
replay_qt_display_constructor_pair_20261011_d0128ff625214f87ba39f921b8d239af.
This supports further constructor scrutiny, not a complete crash-cause claim.
Source inspection independently proves timer/widget construction precedes initial
validation and invalid startup returns no owned handle. Proposed §7.4 uses shared
pure full validation before GUI allocation and pins a single initial snapshot,
applied before timer start. Independent plan review is required before edits.

### 2026-10-11 — §7.4 independent startup plan disagreement

Independent plan and code reviewers returned NOT READY: factory-only preflight
would leave run_dashboard loading Qt and creating QApplication before validation;
passing only the snapshot also left the validated plot update underspecified.
The primary amended §7.4 before implementation to require one pure preflight at
both public entry points, returning the exact pinned snapshot and immutable
_ValidatedPlotUpdate. Private construction consumes that pair without reread or
revalidation, renders before timer start, and both APIs receive failure/race tests.
The shared pure validator retains full scalar/trace and incremental prefix rules.
No production/test edit is authorized until independent plan re-review is READY.

Independent amended §7.4 verdicts: plan_reviewer READY with no remaining
findings; code_reviewer independently ACCEPT. Both confirmed exact source
boundaries and one pinned snapshot+update pair before any GUI work in both APIs.
Primary granted python_expert exclusive ui.py ownership and test_engineer
exclusive UI-test ownership, shared-edit preservation, no pinned environment
or Git operations. Primary retains integration and serial final verification.

### 2026-10-11 — §7.4 frozen integration and primary verification

Python owner froze ui.py at SHA-256
22a5c4ff82017ba65a87d13be35e80fd27eca533763a32d91094ccc4ba9f8d16;
test owner froze UI tests at
94b1d9def1c92c026f637a109cd5a1b8892ec8a484c4f7a95dfd32162a718588.
Independent code_reviewer verified exact bytes and returned FINAL STATIC ACCEPT
with no blocker/high/medium finding. Both-API corruption/advancing-adapter tests,
actual Qt identity preservation, apply-before-timer/failure ownership and
owned-loop finalized shutdown coverage were inspected without implementation by
the reviewer. Root static freeze qt_startup_ready_20261011.json verifies all32
AST/LF files, 20 protected files, two unchanged checkout HEAD/status and 23
preserved local file hashes. Root starts all focused tests together using the
serial pinned runner, tag 65332374cb2f4698a65a79176753c6da. Dynamic clean exit
and full/native gates remain required; static acceptance is not native acceptance.

The complete replay-focused selection now passes: **536 passed**, 12 warnings,
41.03 s, clean process exit 0. Tag
replay_qt_display_focus_20261011_65332374cb2f4698a65a79176753c6da; JUnit SHA-256
e6f11f871371692217d0d607a01834a0dc61c89fb38cf612d2c4e01796917956.
Unlike prior post-assertion native crashes, this is a clean dynamic focused gate.
It does not establish the complete cause of previous interaction-sensitive
Qt failures. All32 source/test/driver hashes match qt_startup_ready; 20protected
and both preserved checkouts/all23 local hashes remain unchanged. Primary
starts full non-real-data suite serially, tag49b229f4ff80404298e97cca3bc797fc.

Final full gate passes **4167 passed, 16 skipped, one real_data deselected**,
1790 warnings, 297.28 s, clean exit0. Tag
replay_qt_display_full_20261011_49b229f4ff80404298e97cca3bc797fc; JUnit SHA-256
b394ff1fb6f2673e79c9437e5eb049777a73294a16ac5ccfb8afb693a2657128.
The exact serial runner is results/test_tmp/run_replay_qt_tests_20261011.ps1
-Scope full: tests -m not real_data, unique existing external temporary parent,
worktree cache/JUnit, M8_RUN_REAL_DATA_TESTS=0, EXP004_RUN_DIR removed. No sealed
opt-in. Post-full all32 frozen hashes and all20protected/two checkouts/23local
hashes pass unchanged. Independent code review ACCEPT and dynamic focused/full
gates permit separate explicit UI and screenshot-driver commits, not a native
performance claim. Native source-clean recording acceptance is next.
