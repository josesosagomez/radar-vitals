# Live motion recovery and slow breathing acceptance record

## Final software status — 2026-10-07

Software acceptance is complete for the feature branch after the final full suite passed on committed code `e8dcbdc34da1cbe2c9f623d5811dc66821d17aee`. The pushed feature milestones are `26e92152915120f3d2d9d26d79b9dcbee8fd76f8`, `8363c035a8348eee6508877447560ee2b3b0b6d5`, `7e8fab924c456a0d2088ecd789183804145d77c5`, and `e8dcbdc34da1cbe2c9f623d5811dc66821d17aee`. The fallback remote checkpoint is `7f3dd2bfd405cdd73d5ae395347baeb991213acf` on `vital_signs_own_v13`.

The final suite reported **3,631 passed, 16 skipped, 1 deselected, and 1,790 existing warnings in 225.12 s**. It used external basetemp `C:\\Users\\josemsosag\\AppData\\Local\\Temp\\radar_motion_br_final2`, cache `results/test_tmp/pytest_cache_motion_br_final2`, and JUnit `results/test_tmp/motion_br_final2.xml`. Skips were four M8 all-bin tests missing their run, two M8 scoring tests missing their run, seven offline real-capture tests without `results/live_demo` captures, and three offline pinned-lock tests without `results/live_demo/20260726_173434_replay_unknown`; one `real_data` test was deselected. No sealed tests were enabled.

Framework and DSP reviews were accepted with no blocker, high, or medium correctness findings. The protected six source/configuration hashes match, and the development seed remains 42. The complete provenance is recorded in [live_motion_software_hashes_2026-10-07.json](live_motion_software_hashes_2026-10-07.json), including 35 source/configuration/test SHA-256 values and manifest SHA-256 `cc92edf0afdef9402efa512f842b1e2bfe6e51cb647483dc969c93e72b15a973`.

Physical calibration and held-out owner captures remain outside completed software work, and the tracked development YAML keeps both motion flags false. Representative-machine hardware rehearsal is owner work. Clinical validation is out of scope for this development demo; physical accuracy at 3 bpm remains unvalidated. Passing software and synthetic checks do not establish physical performance.

## Verified behavior and operator procedure

Behind the disabled opt-in configuration, raw acquisition remains continuous. At 10 and 20 seconds the controller may produce preliminary estimates when existing DSP validity and the two-cycle floors permit them. At 30 seconds it commits the ordinary bin and estimate path. At 60 seconds it assesses positive breathing from 3–30 bpm, quiet activity, or unresolved activity. HR and BR history are independent: rejected or disrupted values retain prior accepted values in red, no history displays as `--`, previews are amber, quiet breathing never becomes numeric zero, and low or contradictory BR can veto fresh HR.

The operator guide contains the clean-feature capture command, offline calibration-copy procedure, accepted-record binding, artifact verification, benchmark, and exact fallback commands: [live_motion_recovery_operator.md](../notes/live_motion_recovery_operator.md). Calibration locks the candidate before held-out metadata and ADC reads; pre-record failures retain `calibration_failure.json`, while completed evaluation retains an accepted or rejected record. Metadata hashes bind files and settings but cannot prove physical origin against deliberate forgery.

The canonical final-suite command is:

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run --no-capture-output -n radar-vitals python -m pytest tests -m "not real_data" -q --tb=short --basetemp C:\Users\josemsosag\AppData\Local\Temp\radar_motion_br_final2 -o cache_dir=results/test_tmp/pytest_cache_motion_br_final2 --junitxml=results/test_tmp/motion_br_final2.xml
```

## Review and failure history

Earlier failures were retained and resolved before the final suite:

| Finding | Resolution |
|---|---|
| Missing ordinary `f_r_hz` crashed an attempt | Failed component now has explicit invalid fields and reason. |
| Quiet acceptance was marked invalid | Quiet state and evidence are preserved without numeric BR. |
| Ordinary DSP failure suppressed quiet assessment | Extended assessment remains independent and preserves ordinary failure. |
| Extended exception discarded ordinary output/phase | Completed ordinary output and phase remain while extended status fails. |
| Selector JSON lacked M3 fields | Complete selector payload is emitted. |
| Extended attempts lacked lineage | Every attempt joins indexed selection and event records; orphans are rejected. |
| Coupling failure was disputed | Known AHET Hz/bpm inputs are retained; only unexecuted outputs are unavailable. |
| Wrong-length phase handling was disputed | The handled failed attempt verifies cleanly; forged state is rejected. |
| External copied-record validation used the repository root | Separate `record_root` containment was added without changing source authority or software hashes. |

The first integrated M4 run had 299 passed, 12 failed, and 12 warnings in 10.43 s; the second had 319 passed, 7 failed, and 12 warnings in 17.37 s. The focused M4 gate then passed 340 tests with 12 warnings in 16.39 s. The primitive breathing/calibration/benchmark run passed 155 tests with 12 warnings in 3.60 s; the final calibration/BR benchmark passed 143 tests with 12 warnings and no skips in 3.38 s. The first full-suite run had 3,630 passed, 1 failed, 16 skipped, 1 deselected, and 1,790 warnings in 395.42 s; its external follow-up had 51 passed, 1 failed, and 12 warnings in 7.07 s. The portable-fix gate passed 135 tests with 12 warnings in 8.83 s, followed by the final containment test with 1 passed and 12 warnings in 1.29 s. These historical results explain the resolved issues and are superseded by the final full-suite result above.

The initial default-suite run from the original checkout accessed nonsealed development ADC and Masimo regression inputs despite `-m not_real_data`. It did not access sealed prospective references, operate hardware, modify `data/raw/`, or tune thresholds against Masimo. Subsequent feature checks were isolated without recordings or references. This report makes no claim of whole-session reference isolation.

## Physical acceptance and rollback

The owner may collect independent training and held-out acquisitions, derive and lock calibration, run the representative-machine benchmark, and rehearse on hardware. A stationary reflector supplies quiet 60-second training windows; no participant breath hold is required. These steps are outside completed software work. Clinical validation is out of scope; physical 3-bpm accuracy remains unvalidated.

To return to the working demo, stop the experimental run and from `C:\\Users\\josemsosag\\Desktop\\vitals_radar_3` on `vital_signs_own_v13` run:

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python scripts/live_demo.py --config scripts/live_demo_calibrated_config.yaml --duration-s 300
```

The fallback requires no reset, revert, deletion, or merge.
