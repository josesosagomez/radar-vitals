# Handoff - isolated motion recovery and extended breathing development

Read CLAUDE.md first. Agents must not operate radar hardware, access sealed
prospective references, tune against Masimo, or modify data/raw.

## 1. Project snapshot

The IWR1642/DCA1000 project estimates seated HR/BR at 0.8-1.4 m. Masimo PR
ground truth is the Beats / min column aligned by integer Unix Timestamp.
Live median-smoothed readouts are development-demo output, not paper metrics.

## 2. Current state

The working fallback is vital_signs_own_v13, remotely verified at
7f3dd2bfd405cdd73d5ae395347baeb991213acf. Its original checkout is
C:/Users/josemsosag/Desktop/vitals_radar_3. The exact clean checkpoint passed
3305 tests, 16 optional-artifact skips, one real_data deselection. Startup is 10 s.
Unrelated capture-duration, MATLAB/TI and IoT/WST work remains preserved there.

The feature branch is codex/live-motion-recovery-slow-br in
C:/Users/josemsosag/Desktop/vitals_radar_3/results/motion_br_worktree.
Foundation 26e92152915120f3d2d9d26d79b9dcbee8fd76f8 is pushed. M3 movement
integration has independent acceptance and passed 365 focused checks; its final
telemetry/integration follow-up passed 28. It provides responsive acquisition,
bounded analysis, continuous raw recording, independent held/preview/fresh display,
source-starvation recovery, and strict versioned evidence verification.

Extended breathing primitives and calibration/benchmark checks passed 143
synthetic tests, but extended breathing is not yet integrated into the worker.
No accepted physical calibration exists. Development features remain disabled
and cannot start enabled without compatible accepted hardware calibration.

## 3. Active task / next steps

After the accepted M3 milestone is pushed, integrate the 60-second breathing
assessment, persistence, atomic HR veto and reconstructable numerical evidence.
Obtain independent DSP review and run the full isolated default/synthetic suite.
Owner gates afterward: independent physical training/holdouts, accepted calibration,
representative-machine benchmark and radar rehearsal. Synthetic tests do not
satisfy these gates or establish physical accuracy or clinical sensitivity.

Owner fallback command, from the original checkout:

```powershell
C:/ProgramData/anaconda3/Scripts/conda.exe run -n radar-vitals python scripts/live_demo.py --config scripts/live_demo_calibrated_config.yaml --duration-s 300
```

Owner capture command, from the clean feature checkout (features disabled):

```powershell
C:/ProgramData/anaconda3/Scripts/conda.exe run -n radar-vitals python scripts/live_demo.py --config scripts/live_demo_motion_config.yaml --duration-s 300
```

Fallback means stop the experimental run and use the original-checkout command.
No reset, revert, deletion or merge is needed. Do not merge the feature branch.

## 4. Recent decisions that matter

Keep the six protected modules/configs byte-identical to the checkpoint. Preserve
SampleSwap=1, ordinary AHET/ECA and single-bin phase extraction. Previews and held
values never enter the ordinary HR median. Invalid vitals and quiet activity never
trigger movement recovery. A dispatched job is not necessarily executed; only
started attempts receive signal evidence. Source silence uses the configured
0.25 s monitor hop, latches one data-gap event and requires 60 quiet frames again.

Calibration locks its candidate before any held-out metadata or ADC inspection.
New calibration captures require a clean feature checkout and exact live source
identity; old dirty original-checkout captures cannot qualify. Synthetic records
cannot enable runtime. Metadata/hash integrity relies on trusted owner acquisition.

## 5. Gotchas / landmines

Use C:/ProgramData/anaconda3/Scripts/conda.exe run --no-capture-output -n radar-vitals
serially. Complete-suite basetemp must be an isolated external temp directory;
cache/JUnit stay in results/test_tmp. Two existing gitignored literature PDFs are
needed by provenance tests. No recordings/references are copied into this checkout.
The original default suite conditionally read nonsealed development references
despite marker filtering; HISTORY corrects that limitation. No values informed
this feature. Subsequent feature checks use this isolated checkout.

Every executed attempt, including failure/expiry/supersession, needs typed atomic
NPZ plus its hashed CSV row. Pre-execution cancellations need events only. Keep LF.
Physical calibration, responsiveness and 3-bpm accuracy remain unvalidated.

## 6. Pointers

| File | Purpose |
|---|---|
| plans/live_demo_motion_recovery_slow_br_2026-10-07.md | Supplied design and justified clarifications |
| config/live_motion_checkpoint_2026-10-07.json | Fallback identity and six protected hashes |
| src/live_motion/ | Development controller, cache, worker, evidence and calibration |
| scripts/live_demo_motion_config.yaml | Disabled development configuration |
| scripts/calibrate_live_motion.py | Offline owner-capture calibration |
| scripts/verify_live_demo_artifacts.py | Legacy/development schema dispatch |
| HISTORY.md | Append-only decisions, failures and verification evidence |
