# YAML startup delay for the development demo

User request: make the launcher's 30-second startup countdown configurable in YAML.

Scope: startup timing only. No range, signal-window, DSP, reference or hardware
changes. Keep existing study timing (natural/paced 30 seconds, recovery zero).

1. Add `session.startup_delay_s: 30` to the calibrated development config with a
   comment explaining zero skips the countdown and study timing stays fixed.
   Leave the original default config byte-identical because it feeds source
   identity; users can add the same setting to a development YAML themselves.
2. Add a pure resolver taking config and prospective metadata. Without study
   metadata, use the YAML field or default 30. Require a nonnegative integer
   (reject booleans, strings, floats, null, negative values). With study metadata,
   use the existing arm-specific delay and ignore this development option.
3. Resolve before countdown/backend/output/hardware; report invalid values as
   clean errors. Persist the resolved `startup_delay_s` in run metadata; this is
   the scheduled countdown, not measured wall-clock elapsed time.
4. Verify default/zero/custom delays, invalid input early failures, and study
   timing regardless of development override. Adapt calibration config comparison
   for the new documented development-only setting. Use mocked sleeps/backend;
   never operate hardware. Run targeted countdown, calibration and study tests.
5. Append HISTORY then rewrite HANDOFF. State where to edit and how zero works.

Acceptance: YAML controls ordinary launch countdown, omitted field retains 30,
zero calls no sleep, N calls N one-second sleeps, invalid values fail before
side effects, study natural/paced/recovery stays 30/30/0, resolved delay saved.
The 30-second analysis window is unaffected. No commit/push.

Exact validation from the repository root (existing `results/test_tmp` parent):

```powershell
& 'C:\ProgramData\anaconda3\Scripts\conda.exe' run --no-capture-output -n radar-vitals python -m pytest tests/test_live_demo_startup_delay.py tests/test_live_demo_range_calibration.py tests/test_m2_capture_artifacts.py tests/test_documentation_claims.py tests/test_repository_eol.py -q --tb=short --basetemp results/test_tmp/startup_delay_final -o cache_dir=results/test_tmp/pytest_cache_startup
```

Verify default YAML SHA-256 remains
`254c14c9b738d8450dbf37d322ac7930fb9f7d23485233041cdb639d65efe2c1`,
and `git diff -- scripts/live_demo_config.yaml` remains empty. Study override
tests include zero, custom and invalid development values. Log ignored study
override source explicitly if useful, but no new timing measurement is required.

Independent plan review: ready with minor clarifications. Incorporated the
distinction between resolved countdown and measured elapsed time, exact test
command, default source-identity check, and study invalid-override cases before
implementation. No outstanding review disagreements.

Implemented and verified: exact command above passed 177 tests, 0 failed, with
13 existing Matplotlib/Pyparsing warnings. The default YAML hash is unchanged
and its diff empty. Calibrated config currently uses 10 seconds; edit its
new field to zero to skip the countdown. No hardware operated.
