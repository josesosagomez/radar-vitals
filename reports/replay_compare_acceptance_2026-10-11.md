# Replay comparison acceptance record

## Scope

This record covers the saved-session replay implementation on the separate
`codex/replay-masimo-compare` worktree. The implementation provides a baseline
replay dashboard for the registered `massimo3` recording and a separate
calibration-gated advanced path. It does not operate hardware, alter
`data/raw/`, use sealed references, or claim heart-rate accuracy.

The pushed implementation milestones are `8f2df39a3fea765a90648bc457c187d18f46d6ba`
(M3 baseline), `14309f6358b686a78781f57dad4a82e41c39b0bb` (M4 advanced), and
`efdb03cc79b20c9392f2aad9128aa8764e24b9c0` (M5 acceptance driver). Each remote
SHA was read back after pushing.

A precursor correction for the selector/publication ordering defect was pushed
and read back as `e1b2ae8d39ed6e263be144c10fbdd9bd7918cd5c`. It is not the final
native acceptance commit.

The reviewed selector correction and owned read-only stack optimization were
pushed and read back as `b9ccd3432fc2a46e338d476fb6b2712246935344`. Native
acceptance remains a separate observation gate.

The reviewed process/UI corrections were pushed and read back as
`36fb21dcadea70f58e72013fe9d172e6275cf103` and
`1700667605edb64a58c71b52dfafb1e2d05fb674`. They resolve the Windows
attached-page capacity, immutable-prefix, controller-copy, and RSS telemetry
findings described below; native acceptance remains separate.

The source-clean native acceptance driver was committed at
`522c4dfc0457036942b89a6bf2fd336c4262b337`; the reviewed UI milestone is
`c97efe23bfdc849924a4966446e5b5e9dde1189a`. Native records bind to source
commit `522c4dfc0457036942b89a6bf2fd336c4262b337`; delivery documents follow it.

The observed pinned Windows stack is Python 3.11.15, NumPy 1.26.4, SciPy
1.13.1, PySide6/Qt 6.7.3 and pyqtgraph 0.13.7. Replay uses no RNG seed.
Input/config identities, software bytes and all current verification evidence
are in `reports/replay_compare_delivery_index_2026-10-11.json` (SHA-256
`982bb9e08f8f6bd05e038e1711b8eaa522455299755e71cb6aa748a8597fd033`).
Its tracked adjacent builder rechecks the source commit blobs, every manifested
artifact, native/verification exits, screenshots, test JUnit/exits and refusal:

```powershell
python reports/replay_compare_delivery_index_builder_2026-10-11.py 8b8a214e7855425d8fb5f425b5984dc5
```

The latest independent process/UI gate passed **536 focused tests** with 12
warnings in 41.03 s and the complete non-real-data gate passed **4,167 tests**,
with 16 optional skips, one `real_data` test deselected, and 1,790 warnings in
297.28 s; both exited cleanly. Focus tag:
`replay_qt_display_focus_20261011_65332374cb2f4698a65a79176753c6da`; full tag:
`replay_qt_display_full_20261011_49b229f4ff80404298e97cca3bc797fc`.
The complete-run JUnit SHA-256 is
`b394ff1fb6f2673e79c9437e5eb049777a73294a16ac5ccfb8afb693a2657128`.
The exact serial runner is retained at
`results/test_tmp/run_replay_qt_tests_20261011.ps1 -Scope focus|full`; it creates unique external
temp/basetemp, local cache, and JUnit paths, disables real-data opt-ins, and
removes `EXP004_RUN_DIR`.

The earlier focused process/UI run exposed Windows attached-page capacity and
mutable prior-prefix forgery defects. Those findings are resolved by the
reviewed commits above; the native matrix below is still not accepted from
automated tests alone.

## Verified software behavior

The baseline consumes the registered recording chronologically at 20 Hz and
supports 0.5x, 1x, 2x, and 4x pacing, pause/resume, and restart. Controls are
controller-owned and snapshot based; pause must be acknowledged before the
snapshot is considered frozen, and restart creates a new generation only after
the previous worker has joined. The writer preserves intermediate signals and
lineage for every executed attempt, including failed or expired attempts.

The reference path uses integer Unix-epoch `Timestamp`, `Beats / min` as pulse
rate, and PI >= 0.5 as the pulse-rate quality gate. Positive respiratory rate
is independent of PI. The reference origin is approximate because the capture
does not contain exact frame-zero UTC. Coverage counts elapsed display
availability and keeps radar HR, positive BR, simultaneous HR+BR, reference
availability, and PI-qualified reference coverage separate. No accuracy or
agreement result is inferred from these counts.

The verifier performs fail-early artifact containment, registered-source and
saved-cursor/hash checks, exact event and motion lineage checks, independent
reference display reconstruction, source-bound phase/DSP checks, and complete
manifest/summary validation. An incomplete or tampered pass is refused.

## Test evidence

The current complete non-real-data suite passed **4,167 tests**, with **16
optional skips**, **one `real_data` test deselected**, and **1,790 warnings**
in **297.28 s**, with clean process exit. The focused selection passed **536
tests**, 12 warnings, in **41.03 s**, also with clean exit. The exact serial
runner and JUnit output are retained in `results/test_tmp`; the complete-run
JUnit SHA-256 is
`b394ff1fb6f2673e79c9437e5eb049777a73294a16ac5ccfb8afb693a2657128`.

The independent static code review accepted the integrated baseline/advanced
implementation with no blocker, high, or medium findings. Earlier complete-suite failures
caused only by two missing ignored literature PDFs were retained in the review
log; the PDFs were restored byte-identically and the suite was rerun. This
report records test evidence, not a physiological conclusion. Some existing
standalone child test processes still emitted their own default-cache
permission warnings; the primary suite itself used the explicit isolated
basetemp and worktree-local cache above. Those warnings do not change the
reported test outcome.

## Native acceptance status

Native evidence must be recorded separately for each scenario:

| Scenario | Exit | Frames/generations | Verifier | Screenshot/visual check | Status |
|---|---:|---|---|---|---|
| Full 1x repeat | 0 | 12,005 / 600.25 s; clean shutdown | 0, `accepted: true`, source-bound, no issues (`8b8a214e7855425d8fb5f425b5984dc5`) | Actual PNGs visually checked; honest HR 1.50%, BR 81.50%, joint 1.50% | current clean native baseline |
| Full 1x prior diagnostic | 1 (timing gate) | 12,005 / 600.25 s; clean shutdown | 0, `accepted: true`, source-bound, no issues | Diagnostic only | historical responsiveness failure |
| Smoke 1x | 0 | 800-frame smoke prefix | 0, `accepted: true`, source-bound, no issues | During PNG shows accepted BR 18.6 and missing HR (`--`) | verified smoke prefix |
| Smoke 0.5x | 0 | 800 frames; clean shutdown | 0, `accepted: true`, source-bound, no issues (`160097501472456b9a0b2f91b39331b6`) | Actual screenshot checked; numeric BR displayed, HR missing | diagnostic speed scenario complete |
| Smoke 2x | 0 | 800 frames; clean shutdown | 0, `accepted: true`, source-bound, no issues (`dc942eabfece4ce788794b0db9dad90d`) | Actual screenshot checked; numeric BR displayed, HR missing | diagnostic speed scenario complete |
| Smoke 4x | 0 | 800 frames; clean shutdown | 0, `accepted: true`, source-bound, no issues (`f51d96809703410181be7df688fc9b72`) | Actual screenshot checked; HR missing; BR was not accepted | diagnostic speed scenario complete |
| Controls 1x | 0 | pass 1 partial 680; pass 2 complete 800, generation 2 | 0, `accepted: true`, source-bound, no issues (`cd08154577b440ffaba2e789df78be74`, `1aed70d97600403ca2255d3ae9797825`) | Pause snapshot unchanged; speed 2/4 acknowledgements observed | controls scenario complete |
| Advanced refusal | 2 | no ADC content / no hardware access | structured `startup_refusal.json` retained | n/a | refusal observed; advanced replay remains unavailable |

The native Qt acceptance driver is
`scripts/run_replay_compare_acceptance.py`. It records source/configuration
hashes, actual controller and UI timing, RSS observations, coverage snapshots,
frame telemetry, and screenshots. Scenario records follow.

The completed smoke evidence is under
`results/replay_compare/replay_native_final_smoke_1_20261011_c9f561ee6ceb498dac073495ab8f1097`.
Its verifier exited 0 with `accepted: true`, complete source binding, and no
issues. The observed 1x playback was 20.0 frames/s; controller work, UI poll,
snapshot age, and Qt heartbeat maxima were 0.032 s, 0.016 s, 0.063 s, and
0.079 s respectively, with no value above the 0.25 s diagnostic threshold.
Two 3450 x 2108 PNGs at DPR 2.5 were retained; the detached-image peak for
the pair was 58,180,800 bytes, and each was encoded after the loop in 0.281 s.
The 800-frame prefix displayed 0 accepted HR values and 73 accepted BR values.
The during-capture PNG shows accepted radar BR 18.6 and missing HR (`--`).
These observations document the smoke run only and do not
establish full native acceptance, accuracy, or physiological tracking.

The current full 1x repeat is under
`results/replay_compare/replay_native_final_full_1_20261011_2f3374fb38fc4b02b71592cd2cbd8837`.
It processed all 12,005 frames (600.25 s), shut down cleanly, and verified with
exit 0, `accepted: true`, complete source binding, and no issues. Playback was
19.99950022 frames/s over 600.265 s; controller work, UI polling, snapshot age,
and Qt heartbeat maxima were 0.094, 0.032, 0.079, and 0.187 s, with no
0.25-second breaches or telemetry errors. Selector time was 2.859 s.
Coverage was HR 180/12,005 (1.50%), positive BR 9,784/12,005 (81.50%), joint
180/12,005 (1.50%), quiet 0; PR, PI-qualified PR, and RR availability were each
11,845/12,005 (98.67%).

The current screenshots were visually checked at 3840 x 1985 and DPR 2.5. The
during image showed held HR 83, accepted BR 17.8, passive PR 90, and RR 14;
the final image showed held HR 83, accepted BR 17.7, passive PR 88, and RR 16.
Each detached image was 30,489,600 bytes; the pair peak was 60,979,200 bytes.
Capture times were 0.063 s and 0.110 s; post-shutdown PNG encoding took 0.296 s
and 0.282 s. These are display observations with honest independent coverage,
not accuracy or physiological claims.

The current run's parent RSS peak was 657,530,880 bytes; early/late steady
medians were 617,541,632 and 656,334,848 bytes (delta 38,793,216 bytes,
including the 30,489,600-byte during image). Combined parent/child peak was
1,372,561,408 bytes and may double-count shared pages. Child current peak was
743,903,232 bytes; child steady values were 114,741,248 and 114,356,224 bytes,
and lifetime peak was 744,452,096 bytes. These observations support bounded
capacity structures (ring/queue/trace arrays, frame limit/chunks, and two image
slots); they do not establish zero growth or a universal memory plateau.

The earlier full 1x on the identical `522c4df` source remains a timing negative:
`results/replay_compare/replay_native_final_full_1_20261011_5abaa70e2325495197b943944b446cb1`.
It completed all 12,005 frames and independently verified its scientific
artifacts, but driver exit was 1: UI poll 0.266 s, snapshot age 0.297 s,
Qt heartbeat 0.594 s with 52 breaches. No root cause has been established;
the passing repeat does not prove timing variance eliminated. Actual prior PNGs
were 3450x2108 versus the repeat's 3840x1985, both DPR 2.5. No agent changed
DPI, priority, timing gates or scientific settings. Both viewport policies in
a later diagnostic failed to reproduce the delay, so no production rendering
change was justified. See the review log for diagnostic scripts and evidence.
Independent final review accepts the exact passing run and capacity-bounded
storage interpretation, with these limitations retained.

The remaining speed and controls scenarios also completed with clean exit and
accepted, source-bound, issue-free verifier results. The 0.5x run observed
10.0 frames/s with controller/UI-poll/snapshot-age/Qt maxima of 0.031/0.016/
0.125/0.079 s and HR 0, BR 172 of 800. The 2x run observed maxima
0.062/0.016/0.047/0.094 s and HR 0, BR 7 of 800. The 4x run observed maxima
0.016/0.015/0.031/0.125 s and HR 0, BR 0 of 800. None exceeded 0.25 s.
The controls run retained a 680-frame partial pass and an 800-frame generation-2
pass; pause acknowledgement took 0.063 s, speed-2 acknowledgement took
0.063 s, and speed-4 acknowledgement took 0.062 s. The final generation-2
controller-work maximum was 0.016 s (the overall two-pass maximum was 0.031 s);
UI polling, snapshot age, and Qt heartbeat maxima were 0.016 s, 0.063 s, and
0.125 s. These are
diagnostic replay observations, not physiological or native-performance claims.

The advanced-mode refusal was observed with exit 2. Its retained diagnostic is
`results/replay_compare/replay_native_final_refusal_1_20261011_44762d9a681a4df78af6d4cc3ebedcb5/20261011T082322_228204Z/startup_refusal.json`.
It records that no ADC content or hardware was accessed; the tracked flags are
disabled, accepted calibration is null, and range bias 0.0784329 is
incompatible with the historical zero-bias requirement.

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run --no-capture-output -n radar-vitals python scripts/run_replay_compare_acceptance.py --development-data-root C:\Users\josemsosag\Desktop\vitals_radar_3 --scenario full --speed 1
C:\ProgramData\anaconda3\Scripts\conda.exe run --no-capture-output -n radar-vitals python scripts/run_replay_compare_acceptance.py --development-data-root C:\Users\josemsosag\Desktop\vitals_radar_3 --scenario smoke --speed 0.5
C:\ProgramData\anaconda3\Scripts\conda.exe run --no-capture-output -n radar-vitals python scripts/run_replay_compare_acceptance.py --development-data-root C:\Users\josemsosag\Desktop\vitals_radar_3 --scenario smoke --speed 2
C:\ProgramData\anaconda3\Scripts\conda.exe run --no-capture-output -n radar-vitals python scripts/run_replay_compare_acceptance.py --development-data-root C:\Users\josemsosag\Desktop\vitals_radar_3 --scenario smoke --speed 4
C:\ProgramData\anaconda3\Scripts\conda.exe run --no-capture-output -n radar-vitals python scripts/run_replay_compare_acceptance.py --development-data-root C:\Users\josemsosag\Desktop\vitals_radar_3 --scenario controls --speed 1
```

The earlier native full 1x run is retained under
`results/replay_compare/acceptance_full_1x_20261011` as diagnostic evidence. It
exposed a high-severity selector/publication ordering defect: the publication
age check ran before a newly completed selection could commit its bin, so a
selection older than 60 frames could repeatedly fail to lock and produce zero
accepted values. The correction is now independently reviewed and software
tested in the pushed commit above. The clean native full 1x rerun under
`results/replay_compare/acceptance_full_1x_corrected_20261011` completed all
12,005 frames and clean shutdown, but failed responsiveness gates. It is not a
final acceptance result. Alternate-speed, controls, and advanced-refusal facts
are recorded in the current matrix above; the earlier timing failure remains a
historical negative alongside the passing repeat. After each scenario, verify
every produced pass with
`verify_replay_demo_artifacts.py` and append the observed JSON paths and
measurements here.

The failed run processed 8,387 of 12,005 frames (419.35 s) before stopping;
HR, BR, and simultaneous HR+BR coverage were all zero, and its
`source_integrity` observation was null. Its screenshots and NPZ evidence are
preserved under `results/replay_compare/acceptance_full_1x_20261011`. The run
also exposed a Windows RSS telemetry race: a ctypes argument pointer signature
changed concurrently, producing “expected LP_Counters instance instead of
pointer to Counters”. The replay-only serialized wrapper correction is included
in the reviewed process/UI commits without changing the protected native
implementation. The earlier failed driver also computed
an invalid FPS field from the requested frame limit rather than the final
telemetry boundary; that field is excluded from interpretation. Controller,
work and UI-snapshot-age maxima were 0.453 s, and the Qt-heartbeat maximum was
0.625 s. The retained UI-poll callback maximum was 0.078 s and did not exceed
0.25 s. The controller-work, UI-snapshot-age, and Qt-heartbeat breaches are
diagnostic observations only, not acceptance measurements.

The corrected run's final ADC initial, streamed, and final hashes matched the
registered ADC. Artifact verification exited 0 with `accepted: true`,
`source_binding_complete: true`, and no issues; the verifier output and exit
status are preserved outside the pass manifest as
`verification_pass_0001.txt` and `verification_pass_0001.exit.txt` under
`results/replay_compare/acceptance_full_1x_corrected_20261011`. The native run
still exited 1 for responsiveness: maximum controller work was 0.375 s, UI
snapshot age 0.422 s, and Qt heartbeat 0.641 s with 162 heartbeat breaches;
the UI callback maximum was 0.063 s with no callback breaches. Playback wall
time was 600.36 s and observed playback rate was 19.996335532014104 frames/s;
selector time was 4.468 s. Diagnostic coverage was HR 178/12,005, BR
9,643/12,005, simultaneous HR+BR 178/12,005, quiet 0, and reference PR,
PI-qualified PR, and RR each 11,845/12,005. Peak RSS was 1,156,190,208 bytes;
steady medians were 635,920,384 and 718,245,888 bytes, a delta of 82,325,504
bytes and observed endpoint growth of 207,511.35 bytes/s. These RSS values do
not establish a plateau or accepted memory behavior. Screenshots were captured
and visually checked, but are not final acceptance evidence.

The controller breaches occurred at 60-frame hop assembly boundaries. Worker-side
materialization and the stable Windows RSS correction are covered by the
reviewed automated gates above. No tuning, gate relaxation, or protected
raw-source change was authorized by these diagnostics.

The earlier 3,950-test suite and 319 replay-test result remain valid observed
test outcomes for the earlier code, but they do not establish final replay
acceptance.

The reviewed precursor correction then passed **322 focused tests**, 12
warnings, in 31.46 s; JUnit is
`results/test_tmp/replay_all13_finalfix_20261011.xml`. The fresh complete suite
passed **3,953 tests**, with **16 optional skips**, **one `real_data` test
deselected**, and **1,790 warnings** in 272.17 s. Its JUnit is
`results/test_tmp/replay_compare_final_correction_20261011.xml`, with the
matching cache suffix and external basetemp
`C:/Users/JOSEMS~1/AppData/Local/Temp/replay_compare_final_correction_20261011_c5a6695876104a7883647315fdc5b3c8/pytest`.
These are precursor software results. The optimized correction then passed
**323 focused tests**, 12 warnings, in 31.53 s; JUnit is
`results/test_tmp/replay_owned_window_focus_20261011.xml`, with cache
`results/test_tmp/pytest_cache_replay_owned_window_focus_20261011` and external
basetemp
`C:/Users/JOSEMS~1/AppData/Local/Temp/replay_owned_window_focus_20261011_f28666d1ba8f4324a9192a894a1141fc/pytest`.
The fresh complete suite passed **3,954 tests**, with **16 optional skips**,
**one `real_data` test deselected**, and **1,790 warnings** in 267.99 s; JUnit
is `results/test_tmp/replay_owned_window_final_20261011.xml`, cache is
`results/test_tmp/pytest_cache_replay_owned_window_final_20261011`, and the
external basetemp is
`C:/Users/JOSEMS~1/AppData/Local/Temp/replay_owned_window_final_20261011_544db8d4fae5417da56b4965b0d036d2/pytest`.
These are historical precursor software results; the current process/UI gates
are the 536-focused and 4,167-complete clean-exit runs recorded above. Native
acceptance remains governed by the scenario matrix.

Independent review had found all 47 prior controller-work observations at hop
boundaries above the 0.25 s target, associated with a redundant `np.stack` to
`AnalysisJob` copy. The owned read-only stack optimization is now accepted by
focused tests and the complete suite above. No native responsiveness claim is
made until the remaining native scenario matrix is reviewed.

## Advanced physical gate and limits

Advanced replay remains unavailable until an owner supplies an accepted
physical calibration snapshot matching the recorded RF/frame/raw/IQ/sampling
configuration and historical zero-bias assumptions. The current tracked
configuration is disabled and its nonzero-bias/absent-record state does not
qualify. Software tests and synthetic authorization fixtures cannot establish
physical calibration. No clinical, accuracy, or hardware conclusion follows
from this software acceptance record.

Current evidence cannot validate a 3 bpm RR claim. No HR, BR, RR, or memory
plateau conclusion is made. The development seed is 42; replay execution does
not use an RNG seed. Review disagreements and negative runs remain recorded in
`reports/replay_compare_review_log_2026-10-07.md`.

The preserved fallback is run from the original checkout:

```powershell
Set-Location C:\Users\josemsosag\Desktop\vitals_radar_3
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python scripts/live_demo.py --config scripts/live_demo_calibrated_config.yaml --duration-s 300
```
