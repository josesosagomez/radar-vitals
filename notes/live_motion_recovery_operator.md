# Live motion recovery and slow breathing: owner guide

This guide covers the development demo on branch `codex/live-motion-recovery-slow-br`. It is an owner-run capture and calibration procedure. It does not operate radar hardware, read Masimo or sealed prospective references, or establish clinical performance.

## Current software state

The feature worktree is based on checkpoint `7f3dd2bfd405cdd73d5ae395347baeb991213acf` from remote branch `vital_signs_own_v13`. The original checkout at `C:\\Users\\josemsosag\\Desktop\\vitals_radar_3` remains the fallback. The feature remains unavailable until its opt-in configuration, accepted hash-bound calibration record, and software gates are verified. Missing or incompatible calibration must fail closed before countdown, output creation, or hardware access.

The opt-in configuration is `scripts/live_demo_motion_config.yaml`. Keep the feature disabled until an accepted hash-bound record exists. Extended breathing requires movement recovery to be enabled; the offline builder may parse an extended-enabled configuration with no record while deriving thresholds, but that configuration must not be launchable until the accepted record is bound. Put owner manifests, calibration output, and enablement copies under ignored `results/` paths so a clean feature checkout remains clean. Do not add untracked scripts or configs.

## Owner capture requirements

Use independent development acquisitions. Do not use `data/raw/`, prospective folders, sealed references, replay inputs, or overlapping windows from one recording. The builder accepts only completed unsealed runs under `results/live_demo/` and checks the raw ADC hash against the run metadata.

Provide at least three independent training acquisitions and two independent held-out acquisitions for each aggregate state required by the plan. Cover the required movement subtypes in both splits, including same-bin torso movement (`same_bin_torso`) and movement across range bins (`range_change`). Include normal, deep, and comfortable slow breathing as movement-negative labels; periodic and scripted irregular breathing for extended-BR calibration; empty, weak, and strong target scenes; and near, middle, and far positions inside the configured distance gate.

For quiet assessment, use a stationary reflector for complete 60-second windows. A participant breath hold is neither required nor a valid substitute for this training state. A 60-second window is 1,200 frames at the configured 20 Hz rate. Record at least three complete quiet-target training epochs and two quiet holdouts.

For every acquisition, preserve the run directory, raw ADC SHA-256, frame-validity artifact, effective profile and IQ convention, placement and target description, operator labels and cue times, and the seed. Acquisition IDs must be unique and one raw hash may not supply multiple clips or splits.

## Manifest

The manifest schema is `live_motion_calibration_manifest_v1` with purpose `development_live_motion_calibration` and `development_only: true`. Its `seed` must exactly match the development YAML seed. Each `run_dir` is repository-relative and must be under `results/live_demo/`.

Copy this template, replace every `REPLACE_...` value with owner-recorded values, and save it outside `data/raw/` (for example, `results/live_motion_calibration/manifests/<unique-id>.yaml`). Placeholder hashes are deliberately invalid until replaced.

```yaml
schema: live_motion_calibration_manifest_v1
purpose: development_live_motion_calibration
development_only: true
seed: REPLACE_WITH_CONFIG_SEED
required_movement_subtypes:
  - same_bin_torso
  - range_change
  - REPLACE_WITH_EVERY_OTHER_REQUIRED_SUBTYPE
acquisitions:
  - acquisition_id: train_normal_near_001
    split: training
    run_dir: results/live_demo/REPLACE_TRAIN_NORMAL_NEAR_RUN
    raw_sha256: REPLACE_WITH_64_HEX_ADC_SHA256
    presence_state: strong
    position: near
    target_kind: participant
    movement_negative_state: normal
    breathing_state: null
    still_intervals: [[0, REPLACE_END_FRAME]]
    movement_episodes:
      - subtype: same_bin_torso
        assigned_features: [phase_activity]
        frame_bounds: [REPLACE_START, REPLACE_STOP]
        operator_label: REPLACE_OWNER_LABEL
        cue_time_utc: REPLACE_ISO8601_OR_NULL
    breathing_window: null
    operator_notes: REPLACE_CAPTURE_NOTES
  - acquisition_id: heldout_quiet_reflector_near_001
    split: heldout
    run_dir: results/live_demo/REPLACE_HELDOUT_QUIET_RUN
    raw_sha256: REPLACE_WITH_DIFFERENT_64_HEX_ADC_SHA256
    presence_state: strong
    position: near
    target_kind: stationary_reflector
    movement_negative_state: null
    breathing_state: quiet
    still_intervals: [[0, 1200]]
    movement_episodes: []
    breathing_window: [0, 1200]
    operator_notes: stationary reflector, full quiet window
```

For training movement episodes, `assigned_features` must be labelled before fitting. Held-out episodes must have an empty or omitted `assigned_features` list so the held-out data cannot alter detector assignment. `still_intervals` and movement bounds are half-open integer frame intervals and may not overlap. A quiet breathing entry must use `target_kind: stationary_reflector`; periodic and irregular breathing entries must use `target_kind: participant`; an empty presence entry must use `target_kind: empty_scene`.

The actual manifest should contain enough unique entries to meet every split, subtype, position, target, and breathing requirement. Do not copy the two illustrative entries as if they were evidence.

## Build and evaluate calibration

From the clean feature worktree, first copy `scripts/live_demo_motion_config.yaml` to the ignored offline path `results/live_motion_calibration/offline/live_demo_motion_config.yaml`, set both motion flags true, and leave its calibration record fields null. Then run with the pinned `radar-vitals` environment:

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python scripts/calibrate_live_motion.py `
  --config results/live_motion_calibration/offline/live_demo_motion_config.yaml `
  --manifest results/live_motion_calibration/manifests/<unique-id>.yaml `
  --capture-root C:\Users\josemsosag\Desktop\vitals_radar_3\results\motion_br_worktree `
  --output-dir results/live_motion_calibration/<new-id>
```

New owner captures should be created in `results/live_demo/` within the clean feature checkout. Use `--capture-root` only when the owner explicitly copies a completed capture to another approved root; it does not change the manifest’s repository-relative `run_dir` values. Owner captures must come from a clean feature checkout with `source_kind: live_dca1000` and exact `source_id: live:<run-name>:adc_stream.bin`. Replay, fake, synthetic, and dirty captures are rejected before validity or raw-byte reads. Legacy captures are accepted only when their Git commit is exactly the clean fallback checkpoint `7f3dd2bfd405cdd73d5ae395347baeb991213acf`; the original checkout is currently dirty with unrelated work, so do not use its old captures for calibration.

The tool reads and validates metadata and labels, processes training acquisitions, writes `candidate.json`, hashes and reloads that candidate, then reads held-out metadata and ADC bytes. The candidate is therefore locked before held-out evaluation. Pre-record failures retain `calibration_failure.json` with the phase and reason, plus the locked candidate when one exists; once held-out evaluation completes, the output retains an accepted or rejected calibration record and diagnostic summaries. A rejected record is not eligible for startup. On acceptance, review the printed record SHA-256 and set both the record path and record hash in the opt-in development YAML; changing semantic settings or source content invalidates the calibration binding.

The tool’s current source is `scripts/calibrate_live_motion.py`; its manifest validator is the authority for exact field names and compatibility checks. No accepted calibration, held-out result, or physical calibration has been established in this documentation update.

## Intended display and launch procedure

With an accepted bound record and `development_motion.enabled: true`, the runtime keeps raw acquisition continuous. At 10 and 20 seconds it may produce preliminary estimates when the existing DSP is valid and at least two cycles are available; the 12-bpm and 6-bpm floors are opportunities, not guarantees. At 30 seconds it commits the ordinary selected bin and starts ordinary estimates. At 60 seconds it assesses positive breathing from 3–30 bpm, quiet activity, or unresolved activity. HR and BR are independent: movement, target loss, invalid data, and rejected estimates retain prior accepted values in red; no history displays as `--`; preliminary values are amber; quiet breathing never displays numeric zero. Confirmed low or contradictory BR can veto a fresh HR while preserving the previous HR in red.

The tracked development config has both `development_motion.enabled: false` and `development_motion.extended_breathing_enabled: false`; it remains disabled and has no shipped motion thresholds. For offline calibration of both features, copy that YAML into an ignored `results/` directory, set both flags to `true`, and leave the copied calibration `record_path` and `record_sha256` null. Use that local offline copy with the builder. After the builder produces an accepted record, bind its path and SHA-256 in the same ignored copy, then use that bound copy for the owner launch. A missing, rejected, incompatible, or hash-mismatched record must leave the feature unavailable.

To create owner captures, run from the clean feature checkout with the tracked disabled configuration:

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python scripts/live_demo.py --config scripts/live_demo_motion_config.yaml --duration-s 300
```

This capture command keeps the feature disabled while preserving continuous raw acquisition and the development provenance metadata needed by the offline builder.

Pinned-environment commands, run from the clean feature checkout, are:

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python scripts/live_demo.py --config results/live_motion_calibration/offline/live_demo_motion_config.yaml --duration-s 300
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python scripts/verify_live_demo_artifacts.py results/live_demo/<completed-feature-run> --expect-mode live
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python scripts/calibrate_live_motion.py --config results/live_motion_calibration/offline/live_demo_motion_config.yaml --manifest results/live_motion_calibration/manifests/<unique-id>.yaml --capture-root C:\Users\josemsosag\Desktop\vitals_radar_3\results\motion_br_worktree --output-dir results/live_motion_calibration/<new-id>
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python scripts/benchmark_live_motion.py --run-dir results/live_demo/<completed-feature-run> --output results/live_motion_benchmark/<new-report>.json --machine-label <owner-machine-label>
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python scripts/benchmark_live_motion.py --run-dir results/live_demo/<completed-feature-run> --output results/live_motion_benchmark/<owner-attested-report>.json --machine-label <owner-machine-label> --memory-accepted
```

For example, the owner may copy `scripts/live_demo_motion_config.yaml` to `results/live_motion_calibration/offline/live_demo_motion_config.yaml`, change only the two development-motion flags to `true`, and keep the record fields null while deriving thresholds. The offline copy is an ignored working artifact; do not commit it or use it for launch until the accepted record path and hash have been written into it. The tracked YAML itself must remain disabled.

The `--capture-root` example above points at the clean feature worktree. Replace it only with an owner-approved root containing explicitly copied captures. For fallback, stop the feature run and, from `C:\Users\josemsosag\Desktop\vitals_radar_3` on `vital_signs_own_v13`, execute `C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python scripts/live_demo.py --config scripts/live_demo_calibrated_config.yaml --duration-s 300`. This preserves the calibrated demo and its 10-second startup setting. Metadata hashes bind files and settings but cannot prove physical origin against deliberate forgery, so retain owner capture records and labels.

## Performance telemetry benchmark

After a completed feature run, evaluate saved telemetry first without memory attestation:

```powershell
python scripts/benchmark_live_motion.py `
  --run-dir results/live_demo/<completed-feature-run> `
  --output results/live_motion_benchmark/<new-report>.json `
  --machine-label <owner-machine-label>
```

If the owner reviews the RSS series and accepts the absence of continuing growth, rerun with a new output path and append `--memory-accepted`.

The evaluator reads `run_metadata.json`, `performance.csv`, and `performance_meta.json` only; it never opens raw ADC data, controls hardware, or reads a physiological reference. The telemetry must identify a structured `source_kind`, source ID, checkout commit, source/configuration/calibration hashes, and a source-arrival clock. Missing `frame_arrival_clock_available: true` fails the lag gate. The benchmark requires contiguous 20-Hz frames, no source-queue overflow, controller and UI lag at or below 0.25 s, ordinary and extended stage coverage, at least two completed rolling updates after the full 60-second window, and steady rolling analysis within the 3-second hop. RSS telemetry must cover 60 seconds; memory status remains unresolved unless the owner explicitly supplies `--memory-accepted`. The tool reports RSS slopes and segments and deliberately invents no memory-growth tolerance.

The run and performance metadata must preserve the source/settings snapshot provenance used to produce the telemetry. A fake, synthetic, or replay source can exercise software gates but produces `physical_acceptance_status: not_assessed_nonhardware_source`; it is not representative hardware acceptance. The capture/calibration path also rejects hard links, symlinks, junctions, and reparse-point escapes. For calibration, held-out metadata preparation and the first held-out ADC-byte/hash read occur only after the candidate has been written, hashed, and reloaded; failures during preparation remain in the retained rejected output evidence.

## Live rehearsal and benchmark

After software checks and accepted offline calibration, the owner may rehearse on the actual computer and radar. Confirm continuous raw acquisition, movement recovery, red retention of prior HR/BR, fresh accumulation after stillness, and the 200/400/600/1,200-frame stage boundaries. Check same-position and range-change recovery, weak-target handling, the stationary-reflector quiet branch, unresolved irregular activity, and artifact verification. Do not enable the launch configuration while the calibration record is absent, rejected, incompatible, or hash-mismatched.

On a representative machine, record a reproducible benchmark showing 20 Hz operation without source-queue overflow, controller/UI lag no greater than 0.25 s, steady analysis completion within the 3 s hop, and no continuing memory growth. Report selector latency separately. Synthetic tests and this benchmark do not establish clinical accuracy.

## Fallback

Stop the experimental run, leave the feature worktree untouched, and launch the calibrated demo from the original checkout on `vital_signs_own_v13`, using the owner’s existing calibrated command and its preserved 10-second startup setting. The fallback checkpoint is remote SHA `7f3dd2bfd405cdd73d5ae395347baeb991213acf`. Returning to it requires no reset, revert, deletion, or merge. Do not claim a feature branch result until the feature branch’s tests, review, calibration, rehearsal, and artifact checks are recorded.

## Limits

This is development-demo behavior. A positive slow breathing estimate is a raw-grid estimate within the configured 3–30 bpm band; it does not establish universal 3-bpm discrimination or ±1 bpm physical accuracy. “No breathing motion detected” is a guarded demo state and must never be represented as numeric `0 bpm`. Guided breathing cadence is an operator cue, not ground truth. No synthetic result or owner rehearsal should be described as clinical validation.
