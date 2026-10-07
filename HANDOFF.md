# Handoff - motion recovery and extended breathing software accepted

Read CLAUDE.md first. Do not operate radar hardware, modify data/raw, access sealed
prospective references, tune against Masimo, or enable uncalibrated features.

## 1. Project snapshot

The IWR1642/DCA1000 project estimates seated HR/BR at 0.8-1.4 m. Masimo ground truth
is Beats / min (PR), aligned by integer Unix Timestamp. Live median-smoothed values
are development-demo output, not paper metrics.

## 2. Current state

Working fallback: vital_signs_own_v13, remotely verified checkpoint
7f3dd2bfd405cdd73d5ae395347baeb991213acf, original checkout
C:/Users/josemsosag/Desktop/vitals_radar_3. Startup remains 10 s. Its exact clean
checkpoint passed 3305 tests; unrelated capture-duration, MATLAB/TI and IoT/WST
work remains preserved there.

Feature branch: codex/live-motion-recovery-slow-br, separate checkout
C:/Users/josemsosag/Desktop/vitals_radar_3/results/motion_br_worktree.
Reviewed software commits 26e9215, 8363c03, 7e8fab9 and e8dcbdc are pushed.
Latest source-code commit is e8dcbdc34da1cbe2c9f623d5811dc66821d17aee; the delivery
documentation commit follows it. No merge into the working branch is authorized.

Both features are implemented. Acquisition stays continuous while bounded analysis
runs asynchronously. Recovery preserves independent HR/BR history in red, confirms
60 quiet valid frames, then requests 200/400/600/1200 fresh-frame stages. Previews
are amber and excluded from the ordinary median. Extended BR is positive 3-30 bpm,
quiet (never numeric zero), or unresolved, with atomic HR vetoes. Evidence is
versioned, numerical components are recomputed, and final-bin lineage is verified.

Software acceptance: focused M4 340 passed; portable gate 135 passed; complete
isolated default/synthetic suite 3631 passed, 16 optional-artifact skips, one real_data
deselection, 1790 existing warnings in 225.12 s. DSP/runtime, framework/evidence and
calibration-provenance reviews accepted. All six protected files match checkpoint.
Staged documentation/configuration/EOL delivery checks passed 32 tests in 1.21 s.
No accepted physical calibration exists. Both tracked flags are false and calibration
path/hash are null. Synthetic tests do not establish physical or clinical validation.

## 3. Active task / next steps

Owner gates: independent physical training and held-out acquisitions, accepted
hash-bound calibration, representative-machine 20 Hz/0.25 s/3 s-hop benchmark with
reviewed RSS, and radar rehearsal. Follow notes/live_motion_recovery_operator.md.
Use a stationary reflector for complete quiet windows; no 60-second participant
breath hold is required. Physical 3-bpm accuracy needs an independent respiration
reference. Keep the working checkpoint as the demo until physical acceptance passes.

Fallback owner command, from the original checkout:

```powershell
C:/ProgramData/anaconda3/Scripts/conda.exe run -n radar-vitals python scripts/live_demo.py --config scripts/live_demo_calibrated_config.yaml --duration-s 300
```

Owner capture command, from the clean feature checkout, with features disabled:

```powershell
C:/ProgramData/anaconda3/Scripts/conda.exe run -n radar-vitals python scripts/live_demo.py --config scripts/live_demo_motion_config.yaml --duration-s 300
```

After accepted calibration only, bind its path/hash in the ignored local copy and
launch from the feature checkout:

```powershell
C:/ProgramData/anaconda3/Scripts/conda.exe run -n radar-vitals python scripts/live_demo.py --config results/live_motion_calibration/offline/live_demo_motion_config.yaml --duration-s 300
```

Returning to the demo means stop the experimental run and use the original-checkout
command. No reset, revert, deletion or merge is needed.

## 4. Recent decisions that matter

Keep src/respiration.py, src/vitals.py, src/window_pipeline.py, src/warmup_select.py
and both baseline live YAMLs byte-identical. Preserve SampleSwap=1, ordinary AHET/ECA
and single-bin phase order. Extended BR never feeds ECA or promotes no-ECA HR.
Invalid vitals and quiet classification never cause movement recovery.

Executed failures retain completed phase/assessment/input evidence. Cancelled-before-
execution jobs have events only. Source silence uses the configured 0.25 s hop and
latches a data-gap event. Calibration candidate locking precedes held-out metadata
and ADC reads; synthetic records cannot enable production. Copied record containment
is separate from repository software-source authority during portable verification.

## 5. Gotchas / landmines

Use pinned conda serially. Full-suite basetemp must be an isolated external directory;
cache/JUnit stay in results/test_tmp. Two gitignored literature PDFs are needed by
provenance tests; no recordings/references were copied here. The initial original-
checkout suite conditionally read nonsealed development references; HISTORY and
the report correct that limitation. No reference values informed this feature.

New calibration captures require a clean feature checkout and exact live_dca1000
identity. Keep owner configs/manifests/output under ignored results so the checkout
stays clean. Old dirty original-checkout captures cannot qualify. Metadata hashes
do not prove physical origin against deliberate forgery. Preserve LF and failed logs.

## 6. Pointers

| File | Purpose |
|---|---|
| plans/live_demo_motion_recovery_slow_br_2026-10-07.md | Supplied design and justified revisions |
| config/live_motion_checkpoint_2026-10-07.json | Fallback and six protected hashes |
| src/live_motion/ | Controller, cache, worker, breathing, calibration and evidence |
| scripts/live_demo_motion_config.yaml | Disabled development configuration |
| scripts/calibrate_live_motion.py | Offline owner-capture calibration |
| scripts/benchmark_live_motion.py | Saved telemetry and explicit owner memory acceptance |
| scripts/verify_live_demo_artifacts.py | Legacy/development artifact verification |
| notes/live_motion_recovery_operator.md | Exact owner procedures and fallback |
| reports/live_motion_recovery_acceptance_2026-10-07.md | Tests, reviews, failures and remaining gates |
| reports/live_motion_software_hashes_2026-10-07.json | Source/config/test/JUnit hashes |
| HISTORY.md | Append-only decisions, failures and verification evidence |
