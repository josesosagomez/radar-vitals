# Handoff - recorded massimo3 replay delivered

Read CLAUDE.md first. Do not operate hardware, alter raw inputs, access sealed
references, tune to Masimo or bypass physical calibration.

## 1. Project snapshot

The IWR1642/DCA1000 project estimates seated HR/BR at 0.8-1.4 m. Masimo ground
truth is Beats / min (PR), aligned by integer Unix Timestamp. Recorded replay
and live median-smoothed displays are development output, not paper metrics.

## 2. Current state

Replay branch: `codex/replay-masimo-compare`, checkout
`C:/Users/josemsosag/Desktop/vitals_radar_3/results/replay_compare_worktree`,
from `dcda615d1a2984848882ed7da6641860331577b1`. Tested runtime/driver source:
`522c4dfc0457036942b89a6bf2fd336c4262b337`; delivery documentation follows it.
No merge into either existing branch is authorized.

Baseline chronologically recalculates registered massimo3 through the unchanged
production decoder. The dashboard shows HR/BR states, passive Masimo PR/RRp,
descriptive summaries, exact display coverage, status, causal plots and warnings.
Pause/resume, preserved restart and 0.5x/1x/2x/4x work. Bounded processes/queues
and every executed attempt's intermediates are evidenced; the independent
verifier checks original-source phase/DSP, reference and complete lineage.

Acceptance: 536 focused and 4167 complete non-real-data tests passed with clean
exit 0; 16 optional skips and one real_data deselection. Independent source and
provenance reviews accept. Final delivery checks also passed: 32 tests, exit 0.
Final normal full 1x completed 12005 frames/600.25 s,
clean child exit and source-bound verification. Controller/UI-poll/age/heartbeat
maxima were 0.094/0.032/0.079/0.187 s, with zero 0.25-second breaches.
Availability: HR 180/12005 (1.50%), BR 9784/12005 (81.50%), joint 1.50%,
quiet zero; reference 98.67%. These are availability counts, not accuracy.

An earlier full 1x on identical source failed timing (heartbeat 0.594 s with
52 breaches, poll 0.266 s, age 0.297 s). Cause remains unestablished; the
passing repeat does not prove variance eliminated. Storage is capacity-bounded;
RSS includes retained images and finite chart history. No universal plateau or
physical-hardware acceptance is claimed.

Advanced recovery/extended-BR replay is implemented and tested but unavailable:
tracked flags false, calibration path/hash null, and 0.0784329 m bias incompatible
with the zero-bias recording. Actual refusal exits 2 before ADC content or
hardware access. Synthetic tests cannot unlock it. The live replay prohibition
and all 20 protected production/configuration files remain intact.

Original `vital_signs_own_v13` stays at `7f3dd2bfd405cdd73d5ae395347baeb991213acf`;
feature `codex/live-motion-recovery-slow-br` in `results/motion_br_worktree` stays
at `dcda615d1a2984848882ed7da6641860331577b1`. Their HEAD/status and all 23
preserved local/planning file hashes match the snapshot. Startup remains 10 s.

## 3. Active task / next steps

Launch baseline from the replay checkout:

```powershell
Set-Location C:/Users/josemsosag/Desktop/vitals_radar_3/results/replay_compare_worktree
C:/ProgramData/anaconda3/Scripts/conda.exe run --no-capture-output -n radar-vitals python scripts/replay_demo_compare.py --development-data-root C:/Users/josemsosag/Desktop/vitals_radar_3
```

Current full pass: `results/replay_compare/replay_native_final_full_1_20261011_2f3374fb38fc4b02b71592cd2cbd8837/pass_0001`.
Two real 3840x1985 DPR 2.5 PNGs in its parent were visually checked.

```powershell
C:/ProgramData/anaconda3/Scripts/conda.exe run --no-capture-output -n radar-vitals python scripts/verify_replay_demo_artifacts.py --run-dir results/replay_compare/replay_native_final_full_1_20261011_2f3374fb38fc4b02b71592cd2cbd8837/pass_0001 --development-data-root C:/Users/josemsosag/Desktop/vitals_radar_3
```

Owner-only physical work remains: independent training/held-out acquisition,
compatible accepted hash-bound calibration, hardware performance and rehearsal.
Follow `notes/live_motion_recovery_operator.md`; use a stationary reflector for
full quiet windows. Replay cannot calibrate or validate 3 bpm against this RRp
reference. Retain/profile recurring timing failures without changing gates/DPI.

Preserved fallback owner command, from the original checkout:

```powershell
Set-Location C:/Users/josemsosag/Desktop/vitals_radar_3
C:/ProgramData/anaconda3/Scripts/conda.exe run -n radar-vitals python scripts/live_demo.py --config scripts/live_demo_calibrated_config.yaml --duration-s 300
```

No reset, merge, revert or branch switch is needed to return to it.

## 4. Recent decisions that matter

Protected DSP/live/calibration bytes stay unchanged. Production SampleSwap,
ordinary AHET/ECA and single-bin phase order are reused. Only valid verified
positive HR enters the replay median; HR/BR history is independent, quiet never
numeric zero. Selector commit precedes age admission. Coverage is application
one-hop leases, never window backfill, including initial accumulation; held,
preliminary and quiet are excluded from accepted numeric coverage.

Reference uses integer Timestamp, PI >= 0.5 for PR qualification, positive RR
independently, visible approximate origin and no filling of missing seconds.
It never affects radar decisions. Advanced legacy validity requires owner opt-in
and strict byte conservation, never manufactured observed validity/calibration.

## 5. Gotchas / landmines

Use pinned radar-vitals serially. Disable sealed/real-data opt-ins; full-suite
basetemp must be unique/external, cache/JUnit local under results/test_tmp.
Recordings stay in the original checkout and guarded API. Keep scientific source
clean before launch; never add raw/reference overrides or full ADC RAM loading.
New physical captures require accepted source identity and owner configuration.

Speed/load can change publication availability. Low HR coverage is a reported
outcome, not a target to tune. Negative runs remain in results/history/review log.
Screenshot geometry differs across runs at the same DPR; do not infer a Qt root
cause or universal memory plateau from the passing run.

## 6. Pointers

| File | Purpose |
|---|---|
| plans/replay_demo_compare_implementation_prompt_2026-10-07.md | Approved implementation instructions |
| plans/replay_demo_compare_massimo3_2026-10-07.md | Design and justified revisions |
| src/replay_compare/ | Separate playback/controller/UI/evidence software |
| scripts/replay_demo_compare.py | Native/headless baseline and gated launcher |
| scripts/replay_demo_compare_config.yaml | Replay defaults |
| scripts/run_replay_compare_acceptance.py | Actual native acceptance driver |
| scripts/verify_replay_demo_artifacts.py | Independent source-bound verifier |
| notes/replay_demo_compare_operator.md | Commands, interpretation and fallback |
| reports/replay_compare_acceptance_2026-10-11.md | Verified tests, native outcomes and limitations |
| reports/replay_compare_delivery_index_2026-10-11.json | Six scenarios/seven verified passes and source/test/artifact hashes |
| reports/replay_compare_delivery_index_builder_2026-10-11.py | Reproducible evidence index builder |
| reports/replay_compare_review_log_2026-10-07.md | Independent reviews, disagreements and negatives |
| reports/replay_compare_protected_sources_2026-10-07.json | Twenty protected byte identities |
| notes/live_motion_recovery_operator.md | Owner physical procedures |
| HISTORY.md | Append-only project record |
