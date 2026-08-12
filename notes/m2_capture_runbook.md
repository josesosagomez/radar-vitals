# M2 prospective capture runbook

This is the operator procedure for the physical acquisition phase that follows the M2 engineering
preflight. It implements `notes/protocol.md` and `notes/analysis_prespec.md`; it does not authorize
M3 work or any change to the HR estimator.

## 1. Before the first participant

1. Review and commit the M2 engineering changes and initial cohort registry. A prospective run is
   intentionally rejected unless `git status --short` is empty and `git rev-parse HEAD` is known.
2. Verify `cohort_registry/registry_v001.json.sha256` equals the SHA-256 of the exact JSON bytes.
3. Keep each editable acquisition/finalization YAML and settle-evidence file either **outside the Git
   checkout** or under the gitignored in-repository work root `m2_capture_work/` — for example
   `m2_capture_work\P001_natural\`. `.gitignore` ignores `m2_capture_work*/`, so files there do not
   make the checkout dirty. An editable sidecar anywhere else in the repository would, and the live
   command would then refuse to start. Note that gitignored is **not** the same as outside the
   checkout: a forced `git add -f`, an `-A -f` sweep or a repository archive would still pick these
   files up, so keep `scene_non_health_notes` free of any identifying detail.
4. Assign `P001` through `P015` strictly by enrolment order. Never change the registry's slot,
   role, paced-rate assignment, or subject ID. `P001`-`P005` are representation validation;
   `P006`-`P015` are final evaluation.
5. Schedule each subject on three different days, in fixed order: natural (visit 1), paced
   (visit 2), recovery (visit 3). Do not substitute or add a participant beyond P015.

For each session, copy the matching template from `templates/`, fill every field, and retain the
exact bytes. Natural and paced sessions also need a byte-preserved evidence file for the measured
continuous 60-second settle interval; put its relative path and lowercase SHA-256 in the YAML.

## 2. Common setup immediately before every run

1. Clear the scene so the seated subject is the dominant reflector. Record only the allowed
   non-health scene category/notes and whether the scene changed.
2. Place the radar at chest height, horizontal, aimed at the chest, and confirm level with the
   phone. Seat the subject with back straight, both hands on thighs, facing the radar, sensored hand
   still, and no phone in hand.
3. Tape-measure radar-face-to-chest distance for this visit. It must be 0.8-1.4 m; record metres in
   `distance_m`.
4. Confirm IWR1642/DCA1000 readiness, 32 chirps/frame, 20 Hz, quiet room, no walkers, no fan airflow,
   and Masimo battery/logging/PR/PI readiness.
5. Synchronize the PC and Masimo phone against the same NTP source. Record
   `clock_offset_start_s = PC UTC - phone UTC` in seconds. The inclusive gate is
   `abs(clock_offset_start_s) <= 1.0`. If it fails, resynchronize before any recording.
6. Start Masimo logging before the radar. Never use Masimo values or agreement to select a range
   bin, DSP option, time shift, threshold, or retry.

## 3. Arm-specific preparation

### Natural

- Let the seated subject breathe naturally.
- Settle for **at least 120 seconds total** before recording. The 60-second evidence interval below
  must fall inside that settled period, not constitute all of it.
- Wait until one continuous 60-second Masimo interval has PR spread <=5 bpm and the difference
  between the first and last 20-second PR summaries is <=3 bpm.
- Record settle duration, start PR, spread, drift, and the bound settle-evidence path/hash.

### Paced

- Use the subject's registry-assigned rate only: 12, 15, or 18 breaths/min. Set the metronome to
  exactly twice that rate (one beat per inhale/exhale).
- Pace for at least 120 seconds before recording and confirm Masimo BR stability for at least
  60 seconds. The same PR settle gate used for natural must also pass: **total settle >= 120 seconds**
  with a continuous 60-second evidence interval inside it. The 120 seconds of pacing **counts toward**
  that settle — the subject paces while PR settles, so pre-record time is ~120 seconds total, not 240.
- Record resting PR and `abs(resting_pr_bpm - 4*commanded_rate_bpm)`. Do not change the assigned
  rate because of this margin.

### Recovery

- Keep PAR-Q+, eligibility details, consent/PIS, names, diagnoses, symptoms, medication/pregnancy
  information, and the re-identification key outside the repository. The YAML stores only the
  approved positive operational attestations and measurements.
- Prepare the radar/seat/room first. With the researcher present, perform self-paced step-ups away
  from the radar on a dry unobstructed surface in suitable footwear. Keep both PR and SpO2 visible
  on the Masimo throughout exertion and recovery.
- Stop exertion on the **live Masimo reading** at 100-120 bpm, or immediately for participant stop
  or for chest pain/tightness, light-headedness/faintness, disproportionate breathlessness,
  palpitations, nausea, visible distress, or any researcher safety stop. Record only the controlled
  stopping category, never a health narrative.
- Only `stopping_event_category: target_reached` continues to radar acquisition. For participant
  request or any safety stop, terminate the attempted acquisition, monitor the participant, retain
  any existing artifacts, follow the approved return/safety procedure, and report the controlled
  non-acquisition disposition in this task. Do not launch the live command.
- On `target_reached`, seat the subject and, at the instant the subject is in the fixed posture,
  still, and hands are resting, execute the timestamp assignment shown below. Record seated PR at
  t=0, complete the already-prepared recovery sidecar, and launch immediately using that unchanged
  timestamp variable. The producer has no countdown for recovery and derives the actual
  seat-to-frame-0 delay. The natural/paced settle criterion is deliberately not used. The recorded
  delay is evidence, not a value to guess in the YAML.
- Use natural breathing only; no pacing.

### During every 600-second capture

- Keep the fixed posture and hand placement for the entire recording: still, no talking, no phone,
  and no deliberate posture shift. Record any disturbance; do not silently correct the evidence.
- Natural and recovery remain natural-breathing throughout. Paced continues the registry-assigned
  metronome rate throughout all 600 seconds; never change the rate from observed Masimo values.

## 4. Exact live command

Run natural and paced sessions from the clean repository checkout, substituting the session,
external sidecar, and latest committed registry revision:

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals --no-capture-output `
  python scripts/live_demo.py `
  --live-session P001_natural `
  --duration-s 600 `
  --prospective-sidecar m2_capture_work\P001_natural\acquisition.yaml `
  --cohort-registry cohort_registry\registry_v001.json
```

For recovery, execute this assignment at the seating event described above, before completing the
few remaining sidecar measurements:

```powershell
$recoverySeatedStartUtc = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
```

Then pass that unchanged synchronized-PC Unix epoch to the otherwise identical command. Do this
only after the target-reached branch above:

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals --no-capture-output `
  python scripts/live_demo.py `
  --live-session P001_recovery `
  --duration-s 600 `
  --prospective-sidecar m2_capture_work\P001_recovery\acquisition.yaml `
  --cohort-registry cohort_registry\registry_v001.json `
  --recovery-seated-start-utc $recoverySeatedStartUtc
```

> **Pending change, not yet active.** The manual `$recoverySeatedStartUtc` assignment above is the
> current procedure and remains correct today. `plans/m2_sidecar_scaffold.md` (D-OWN-5, D13) decides
> that `scripts/m2_scaffold_sidecar.py measure --stage seated` will stamp this epoch itself, as its
> first action, so it cannot be mistyped or culture-formatted. That script **does not exist yet**.
> Keep following the manual procedure until it ships, at which point this section is replaced.

Do not add `--locked-bin`, `--no-configure`, `--replay`, `--replay-session`, `--replay-fast`, a
non-default `--config`, or any duration other than 600. Natural and paced have the fixed 30-second
countdown; recovery has no countdown and requires `--recovery-seated-start-utc`. The command
auto-locks the warmup bin, caps the canonical stream at exactly 12,000 frames, and writes the run to
`results/live_demo/<timestamp>_live_<session_id>/`. Low warmup confidence is recorded; it is not a
reason to repeat a validation/final session.

The run must finish with `sealed_radar_receipt.json`, `adc_stream.bin`, `frame_validity.npy`, exact
source/effective configs, the snapshotted sidecar, `run_metadata.json`, packet counters, raw/config
hashes, the clean capture commit, and the exact frame-0 start-assignment UTC.

## 5. Immediately after the 12,000-frame stop

1. Re-measure `clock_offset_end_s = PC UTC - phone UTC`; `abs(offset) <= 1.0` is required. Record
   final PR and `actual_duration_s: 600.0` in a copy of `templates/m2_finalization.yaml` outside the
   checkout.
2. Natural/paced: stop Masimo and export the CSV with the exact expected pseudonymous basename.
3. Recovery: keep the subject seated and monitored until PR is within 5 bpm of the recorded
   pre-exertion resting PR. Record numeric post-monitoring PR and elapsed monitoring seconds, then
   stop/export Masimo.
4. Do not open, graph, summarize, rename based on values, or otherwise inspect the exported CSV.
   A missing/export-failed reference is recorded with the controlled missing reason; it is not a
   reason to repeat the session.

## 6. Register, finalize, and validate without inspecting outcomes

Append the receipt to a new no-overwrite registry revision in the same registry directory:

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals --no-capture-output `
  python scripts/m2_register_capture.py `
  --registry cohort_registry\registry_v001.json `
  --receipt results\live_demo\<RUN_DIR>\sealed_radar_receipt.json `
  --output-registry cohort_registry\registry_v002.json
```

Preserve and deliberately commit the new registry JSON plus `.sha256` sidecar before finalization.
Finalization and preflight may copy, hash, and bind the opaque reference bytes, but they do not
authorize analytical access. Do not parse, inspect, summarize, or score prospective reference
content until the later label-firewall transition explicitly authorizes it in this same task. Then
create a new immutable promoted directory. For an acquired reference:

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals --no-capture-output `
  python scripts/m2_finalize_capture.py results\live_demo\<RUN_DIR> `
  --finalization-sidecar m2_capture_work\P001_natural\finalization.yaml `
  --cohort-registry cohort_registry\registry_v002.json `
  --reference m2_capture_work\P001_natural\P001_natural_reference.csv `
  --destination data\raw\prospective\P001_natural

C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals --no-capture-output `
  python scripts/m2_preflight.py `
  --manifest data\raw\prospective\P001_natural\session_manifest_v3.json `
  --root data\raw\prospective\P001_natural
```

If `reference_acquired: false`, omit `--reference`; set `reference_missing_reason` to exactly one of
`export_failed`, `logging_failed`, or `file_missing`, and remove `reference_basename` and
`reference_export_utc` from the copied finalization template. The destination must not already
exist. After promotion, everything under that new raw directory is read-only.

Only three objective conditions may enter retry validation: a protocol abort; corrupt raw proven by
the exact checksum/non-final-window test; or start/end epoch synchronization failure. Retain every
artifact and stop. Do not delete, overwrite, or recapture on your own; the linked objective retry
record must be validated first. Participant-requested withdrawal never automatically authorizes a
retry. Packet loss, invalid frames, low warmup confidence, missing reference, recovery dynamic-range
adequacy, radar yield, or agreement never authorize a retry.

## 7. Return to this task

Return with the promoted session directories, latest registry revision plus digest, run-directory
paths, and any controlled non-acquisition/retry records. Validation in this task will check timing,
hash/provenance bindings, packet conservation, the Boolean frame-validity map and affected-window
NaNs, required metadata, subject-disjoint cohort membership, and the recovery dynamic-HR rules.
Do not proceed to M3 while M2 remains pending.
