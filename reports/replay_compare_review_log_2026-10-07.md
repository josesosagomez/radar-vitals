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
