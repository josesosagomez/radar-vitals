# Replay comparison operator guide

This guide describes the recorded `massimo3` replay dashboard. It is a
development research tool for reproducing the current software path from saved
radar and Masimo files. It does not operate radar hardware, read a live
reference, or establish physiological accuracy.

The current baseline is the verified full 1x replay at
`results/replay_compare/replay_native_final_full_1_20261011_2f3374fb38fc4b02b71592cd2cbd8837`.
It completed all 12,005 frames with clean shutdown, accepted source-bound
artifacts, and no timing or telemetry breaches. The 0.5x, 1x, 2x, 4x, and
controls scenarios have verified artifact records; the acceptance details,
prior negative run, and exact evidence paths are in
`reports/replay_compare_acceptance_2026-10-11.md`.

The earlier full 1x run failed timing gates, so a passing repeat does not prove
that timing variance has been eliminated. The software/UI gates remain 536
focused and 4,167 complete clean-exit tests. This guide documents replay
operation and display evidence; it does not establish physiological accuracy.

The current run displayed HR 83 and BR 17.8 during capture, and HR 83 and BR
17.7 in the final image; passive PR/RR readouts were also present. Honest
independent coverage was HR 1.50%, BR 81.50%, and joint HR+BR 1.50% over the
full recording. These are display observations, not accuracy results.

The 0.5x, 2x, and 4x smoke scenarios and the controls scenario each exited
cleanly and passed independent artifact verification with source binding and no
issues. Their 800-frame diagnostic prefixes produced BR counts of 172, 7, and
0 respectively, with HR count 0 in each. Controls retained a 680-frame partial
pass and an 800-frame generation-2 pass; pause preserved the snapshot and
speed changes were acknowledged. These are replay diagnostics, not accuracy
or native-performance claims.

The current independent process/UI gate passed 536 focused tests with 12
warnings in 41.03 s and the complete non-real-data gate passed 4,167 tests,
with 16 optional skips, one `real_data` test deselected, and 1,790 warnings in
297.28 s; both exited cleanly. Focus tag:
`replay_qt_display_focus_20261011_65332374cb2f4698a65a79176753c6da`; full tag:
`replay_qt_display_full_20261011_49b229f4ff80404298e97cca3bc797fc`. The
complete-run JUnit SHA-256 is
`b394ff1fb6f2673e79c9437e5eb049777a73294a16ac5ccfb8afb693a2657128`.
The reviewed UI source is frozen at SHA-256
`22a5c4ff82017ba65a87d13be35e80fd27eca533763a32d91094ccc4ba9f8d16`.
The source correction was pushed as
`36fb21dcadea70f58e72013fe9d172e6275cf103` and the UI correction as
`1700667605edb64a58c71b52dfafb1e2d05fb674`; both remote SHAs were read back.
Windows attached-page capacity and mutable prior-prefix forgery findings are
resolved by those reviewed corrections. Native replay acceptance remains a
separate gate.

## Inputs and interpretation

The supported capture is the registered
`20260728_224902_live_demo_massimo3` session. The recording contains 12,005
complete frames at 20 Hz (600.25 s), with 256 ADC samples, four receive
channels, and 32 chirps per frame. The replay source reads frames in order and
keeps a bounded history. It does not seek, substitute a range bin, or use a
reference value to select radar processing.

Masimo rows are aligned with the integer Unix-epoch `Timestamp` column. The
`Beats / min` column is the pulse-rate reference; `Perfusion Index` must be at
least 0.5 before a pulse-rate row is qualified. Positive respiratory-rate rows
are displayed independently and do not use the PI gate. The capture has no
exact frame-zero UTC timestamp, so the dashboard labels its reference origin
as approximate. No interpolation, carry-forward, fitted offset, or accuracy
metric is produced.

An accepted radar heart-rate value requires valid DSP, verified AHET evidence,
and a finite positive rate. The accepted-candidate rank is a nonnegative
integer. The displayed heart-rate summary is the
median of the last five accepted values. There is no unverified or no-ECA
fallback. A previously accepted value may remain visible as historical red,
while a missing value is shown as `--`.
Quiet breathing is a separate state and is not displayed as numeric zero.

An invalid source frame or source discontinuity resets the raw ring, selected
bin, and smoothing state. A rejected vital estimate has separate behavior: it
may retain prior accepted HR or BR as historical output and does not relock the
range bin or reset that history.

## Baseline launch

Run from the replay worktree after the reviewed scientific source is committed
and the checkout has no changes in `src`, `scripts`, `experiments`,
`reference_registry`, or `environment.yml`:

```powershell
Set-Location C:\Users\josemsosag\Desktop\vitals_radar_3\results\replay_compare_worktree
C:\ProgramData\anaconda3\Scripts\conda.exe run --no-capture-output -n radar-vitals python scripts/replay_demo_compare.py --development-data-root C:\Users\josemsosag\Desktop\vitals_radar_3
```

The default configuration is
`scripts/replay_demo_compare_config.yaml`: baseline mode, 1x playback, a
50-ms UI polling interval, and output below the checkout's canonical
`results/replay_compare/<run-id>/` directory. Use `--speed 0.5`, `--speed 2`,
or `--speed 4` for the other supported playback rates. `--duration-s` limits
the number of complete source frames processed. `--headless` runs the
baseline manager without opening the dashboard. The application exposes pause,
resume, restart, and the four playback speeds; controls affect pacing only and
cannot seek into the recording. Pause is acknowledged only after the complete
controller snapshot is frozen. Restart begins a new generation after the old
worker has joined and preserves the prior pass, which may be partial.

Each pass writes metadata, controller and playback events, attempts, source
integrity information, frame telemetry, intermediate DSP evidence, coverage,
reference display audit rows, and a final manifest/summary. The application
lease for a measurement is `[A, A+60)`, where `A` is the next unprocessed
application-frame boundary. Admission requires the application boundary minus
the window stop to be at most 60 frames. An admitted numeric value receives a
display lease `[A, A+60)`, clipped earlier by rejection, data quality, or EOF.
Measurement-window age is displayed separately; the lease is a display
availability rule, not proof of physiological freshness. Coverage uses all
processed frames, including the initial 600-frame accumulation, in the
denominator. Wall time spent paused is excluded. HR, positive BR, and
simultaneous HR+BR coverage are separate; reference availability and PI-qualified
reference coverage are also separate. Quiet assessment is separate from numeric
coverage. These are display-availability measures, not agreement measures.

A reproducible baseline smoke run is:

```powershell
Set-Location C:\Users\josemsosag\Desktop\vitals_radar_3\results\replay_compare_worktree
C:\ProgramData\anaconda3\Scripts\conda.exe run --no-capture-output -n radar-vitals python scripts/replay_demo_compare.py --development-data-root C:\Users\josemsosag\Desktop\vitals_radar_3 --duration-s 40 --headless
```

## Artifact verification

Verify each completed pass separately, including a partial first pass retained
by a controls/restart run:

```powershell
Set-Location C:\Users\josemsosag\Desktop\vitals_radar_3\results\replay_compare_worktree
C:\ProgramData\anaconda3\Scripts\conda.exe run --no-capture-output -n radar-vitals python scripts/verify_replay_demo_artifacts.py --run-dir "results/replay_compare/<run-id>/pass_0001" --development-data-root C:\Users\josemsosag\Desktop\vitals_radar_3
```

The verifier binds the pass to the registered ADC and reference inputs, checks
the saved source cursor and hashes, recomputes phase/DSP and guarded reference
display rows, and checks event, attempt, motion, coverage, manifest, and
summary lineage. It returns exit status 0 only for an accepted complete
verification. Missing source, redirected paths, altered calibration snapshots,
wrong generations, malformed values, and incomplete evidence are refused.
The printed JSON is the record to retain with the run; it is not an accuracy
report.

## Advanced mode and fallback

Motion/recovery and extended-breathing replay use a separate path and require
an accepted physical calibration record. The record must match the exact
recorded RF profile, frame format, raw/IQ convention, sampling rate, and
historical zero-bias semantics. The tracked motion configuration is disabled,
has no qualifying calibration hash, and cannot unlock the mode. Synthetic data
or monkeypatches cannot unlock the command. A refused startup writes
`startup_refusal.json` without opening ADC content or accessing hardware.

The physical calibration procedure is owner work and is described in
`notes/live_motion_recovery_operator.md`; this replay guide does not authorize
hardware operation. If advanced authorization is unavailable, use baseline
replay or the existing working fallback from the original checkout:

The refusal path can be exercised without opening ADC content or hardware:

```powershell
Set-Location C:\Users\josemsosag\Desktop\vitals_radar_3\results\replay_compare_worktree
C:\ProgramData\anaconda3\Scripts\conda.exe run --no-capture-output -n radar-vitals python scripts/replay_demo_compare.py --development-data-root C:\Users\josemsosag\Desktop\vitals_radar_3 --mode motion --motion-config scripts/live_demo_motion_config.yaml --allow-legacy-validity-assumption --headless
```

The expected result is a structured startup refusal, not an unlocked advanced
run. Its diagnostic is written below the canonical replay output directory.
The verified refusal record is
`results/replay_compare/replay_native_final_refusal_1_20261011_44762d9a681a4df78af6d4cc3ebedcb5/20261011T082322_228204Z/startup_refusal.json`;
the command exited 2 without ADC-content or hardware access because advanced
flags are disabled, accepted calibration is null, and the range bias is
incompatible with the historical zero-bias requirement.

```powershell
Set-Location C:\Users\josemsosag\Desktop\vitals_radar_3
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python scripts/live_demo.py --config scripts/live_demo_calibrated_config.yaml --duration-s 300
```

Current evidence cannot validate a 3 bpm RR claim; no RR accuracy or clinical
conclusion is made. The development seed is 42; replay execution does not use
an RNG seed.

That command is documented for the preserved fallback branch only. Do not
reset, merge, or modify the original or feature checkout to obtain it.
