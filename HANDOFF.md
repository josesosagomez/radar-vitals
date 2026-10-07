# Handoff - protected calibrated demo; isolated motion/slow-BR development

Read CLAUDE.md first. Live HR/BR output is development-demo evidence, not
paper-grade accuracy. No sealed prospective reference or hardware operation
is authorized for agents. data/raw is read-only.

## Verified fallback

Working branch and verified remote SHA: vital_signs_own_v13 at
7f3dd2bfd405cdd73d5ae395347baeb991213acf.
Original checkout: C:/Users/josemsosag/Desktop/vitals_radar_3.
The exact checkpoint passed 3305 tests, 16 optional-artifact skips and one
real_data deselection in a clean checkout (checkpoint_clean2.xml).
The calibrated YAML retains startup_delay_s: 10.

Owner-operated launch from the original checkout:

```powershell
C:/ProgramData/anaconda3/Scripts/conda.exe run -n radar-vitals python scripts/live_demo.py --config scripts/live_demo_calibrated_config.yaml --duration-s 300
```

Fallback: stop the experimental run and launch this command in the original
checkout. No reset, revert, deletion or merge is required. Unrelated local
capture-duration, MATLAB/TI and IoT/WST work is preserved there.

## Feature checkout and current gate

Branch: codex/live-motion-recovery-slow-br, based on the pushed fallback.
Checkout: C:/Users/josemsosag/Desktop/vitals_radar_3/results/motion_br_worktree.
Plan: plans/live_demo_motion_recovery_slow_br_2026-10-07.md.
Protected hashes: config/live_motion_checkpoint_2026-10-07.json.

M2 foundation passed 127 focused/documentation/EOL checks and independent review: frame-indexed recovery, owned candidate-bin
cache, bounded scheduling, immutable display and per-attempt atomic evidence.
M3 launcher integration is now in progress; it requires its own integration and review gate.
Breathing/calibration code and synthetic checks are in progress independently.
No accepted physical detector calibration exists. Do not enable the feature.
Physical training, independent holdouts, representative-machine responsiveness
and owner rehearsal remain required. Synthetic passing tests do not satisfy
those gates or establish physical/clinical sensitivity.

## Verification environment

Use C:/ProgramData/anaconda3/Scripts/conda.exe run --no-capture-output -n radar-vitals
for Python, serially (simultaneous conda runs can collide). Full-suite basetemp
must be an isolated external temporary directory; cache/JUnit stay under
results/test_tmp. The feature checkout contains no recordings or Masimo
references. Optional saved-data tests skip; never populate it with sealed data.
Two gitignored literature PDFs are required by existing provenance tests.

The original checkout's default suite conditionally accessed existing
nonsealed development data/reference regressions despite marker filtering;
HISTORY appends the correction. No resulting values informed this feature.
All feature verification uses the isolated checkout without recordings.

Keep src/respiration.py, src/vitals.py, src/window_pipeline.py,
src/warmup_select.py and both original live YAMLs byte-identical to checkpoint.
Keep LF endings, unchanged SampleSwap=1 IQ convention and ordinary AHET/ECA
interfaces. Every executed analysis must retain typed, hash-bound evidence,
including failed, expired and superseded attempts. Cancellation before
execution requires events only. See the supplied plan for remaining gates.
