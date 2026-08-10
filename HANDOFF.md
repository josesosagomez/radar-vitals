# Handoff — M2 engineering preflight PASS; acquisition pending

> Read this and `CLAUDE.md` before doing anything. **State verified 2026-08-10.**
> `HISTORY.md` is append-only; this file is the current resume point.

## 1. Current state

Active branch: **`vital_signs_own_v13`**. Current HEAD before the M2 working-tree changes:
**`da3287dd911c253f41e10feb02f3f4641c3b219e`**.

M2 acquisition engineering is implemented, tested and independently reviewed: **engineering
preflight PASS**. No prospective participant was captured, no real prospective reference was
opened, and no capture result was fabricated. Overall **M2 is FAIL / pending physical acquisition**.
Do not start M3.

The worktree intentionally contains uncommitted M2 code, tests, registry, templates, plan and
documentation. This is an operational stop: `scripts/live_demo.py` refuses prospective capture
until the reviewed M2 changes and initial registry are deliberately committed and the checkout is
clean. Do not weaken that gate.

## 2. Implemented M2 contract

- `src/m2/manifest_v3.py` plus `src/m2/manifest.py`: separate prospective-v3 scoring validation
  with historical-v2 compatibility.
- `src/m2/acquisition_metadata.py`: strict common/natural/paced/recovery metadata and privacy-safe
  finalization schemas; both clock offsets use the inclusive absolute 1-second gate.
- `src/m2/cohort_registry.py`: canonical immutable P001-P015 registry/history/digest validation,
  fixed five/ten roles and paced rotation, controlled missing states, retry attempts and the
  post-shutdown captured-session registration operation.
- `src/m2/label_firewall.py`: atomic-only prospective label transitions and capabilities. Final
  score output is built inside the transaction; score/audit/registry/digest rollback together.
- `src/m2/capture_artifacts.py`: strict sealed radar receipt, registry-bound registration,
  no-overwrite reference finalization and directly scoring-loadable manifest.
- `src/m2/validity.py`, `src/m2/time_sensitivity.py`, `src/m2/retry.py`, `src/m2/preflight.py`:
  frame/window validity, fixed -1/0/+1-second sensitivity, objective retries and ordered dry run.
- `scripts/live_demo.py`: acquisition-only changes for exact frame-0 start assignment, packet/map
  evidence and exact 12,000-frame cap. Recovery requires a synchronized seating timestamp, skips
  the natural/paced countdown, and binds the derived seat-to-frame-0 delay. The estimator/DSP path
  was not modified.
- Operator CLIs: `scripts/m2_register_capture.py`, `scripts/m2_finalize_capture.py`, and
  `scripts/m2_preflight.py`.

Initial registry SHA-256:
`e1bf942ff8bfbdd2b9f4b92e448099142058805bc8d0213576db12d8a3689a7c`, matching
`cohort_registry/registry_v001.json.sha256`.

## 3. Verification evidence

- Synthetic dry run and independent manifest recheck: PASS, all seven ordered stages; exact invalid
  window indices `[0, 1]`.
- Final focused suite: **816 passed, 12 warnings**.
- Final full suite: **3139 passed, 5 skipped, 1790 warnings** in 204.27 s. It requires full Conda
  activation and separate writable temp parents for outer and nested pytest on this Windows host.
- Independent code review: **PASS**, no remaining material M2 defect.
- `compileall`, M2 imports and `git diff --check`: PASS.
- No HR estimator, M3 representation or `data/raw/` engineering changes; no real-data access.

## 4. Exact next action

1. Review and commit only the intended M2 changes. Confirm `git status --short` is empty and the
   initial registry/digest is committed. Do not ask the operator to edit per-session YAML inside the
   checkout; use an external working directory so the capture commit stays clean.
2. Follow `notes/m2_capture_runbook.md` exactly. It contains the physical setup, arm-specific
   procedure, strict live command, post-shutdown registry command, finalizer and preflight command.
3. Preserve every attempt. Do not improvise a recapture. Only a validated objective protocol-abort,
   corrupt-raw or epoch-sync record can permit a same-subject retry before scoring.
4. Return to this same task with promoted session directories and the latest registry revision.
   Validate timing/provenance, packet conservation, frame map and radar-NaN propagation, required
   metadata, subject-disjoint membership, and recovery dynamic-HR acquisition before changing the
   M2 verdict.

## 5. Capture landmines

- Exact command duration is 600 seconds and the canonical stream is exactly 12,000 frames at 20 Hz.
- Frame origin is the first-byte/start-assignment event for frame index 0, never `start_wall_utc` or
  full-frame completion.
- Record signed `PC UTC - phone UTC` at both ends; each absolute value must be <=1.0 seconds.
- Use automatic warmup bin selection. Never use manual bin, replay, no-configure, alternate config,
  Masimo agreement, or reference values to choose DSP or alignment.
- Packet loss flags the session; the Boolean frame map decides affected radar-NaN windows. It does
  not authorize retry.
- Natural and paced require the numeric 60-second settle evidence and retain the 60-second command
  countdown. Recovery explicitly does not; after live Masimo exertion-stop PR reaches 100-120 bpm,
  record synchronized seating UTC, start with no countdown, and retain the derived seat-to-frame-0
  delay.
- Low warmup confidence, missing reference, recovery adequacy/yield, radar coverage and agreement
  never authorize recapture.
- Keep participant names, re-identification keys, screening answers, diagnoses, symptoms,
  medications/pregnancy data, consent/PIS and health narratives outside the repository.
- `data/raw/` becomes read-only immediately after each no-overwrite promotion.

## 6. Key files

| File | Purpose |
|---|---|
| `plans/m2_engineering_acquisition_preflight.md` | reviewed M2 engineering contract |
| `notes/m2_capture_runbook.md` | exact physical acquisition and finalization procedure |
| `notes/protocol.md` | approved three-arm participant protocol |
| `notes/analysis_prespec.md` | cohorts, timing, validity, retry and dynamic-HR rules |
| `cohort_registry/registry_v001.json` | initial P001-P015 cohort allocation |
| `templates/m2_acquisition_*.yaml` | arm-specific acquisition sidecars |
| `templates/m2_finalization.yaml` | post-capture/end-offset/reference record |
| `scripts/live_demo.py` | sole prospective live acquisition path |
| `scripts/m2_register_capture.py` | receipt-to-registry immutable revision command |
| `scripts/m2_finalize_capture.py` | reference binding and no-overwrite promotion command |
| `scripts/m2_preflight.py` | dry-run and real-manifest engineering validator |
| `HISTORY.md` | append-only evidence and decision log |
