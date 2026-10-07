# Movement recovery and extended breathing assessment

## 1. Goals and agreed behavior

Implement two development-demo features:

1. **Automatic recovery after movement:** detect disrupted measurements, retain previous readings in red, wait for stillness, select the radar bin again, and progressively display fresh estimates.
2. **Extended breathing assessment:** report positive breathing rates down to **3 bpm**, and show **“No breathing motion detected”** when sufficient clean evidence supports that observation.

The plan has been checked by the code explorer, research agent, milestone planner, test engineer, and independent plan reviewer. **Independent plan-review verdict: READY**, subject to the implementation, calibration, and acceptance checks below.

| Situation | Intended display |
|---|---|
| Movement, missing data, or an inadequate target signal | Previous HR and BR remain visible in red; show the reason |
| No previous accepted reading | Show `--` |
| 10 seconds of fresh data | Attempt preliminary estimates; accepted values appear amber |
| 20 seconds of fresh data | Attempt improved preliminary estimates |
| 30 seconds of fresh data | Attempt ordinary estimates using the existing DSP |
| 60 seconds of fresh data | Assess positive BR across 3–30 bpm, quiet activity, or unresolved activity |
| Quiet assessment accepted | Show “No breathing motion detected”; never display numeric `0 bpm` |
| Weak, inconsistent, or irregular activity | Keep the previous BR red and show an unresolved status |

HR and BR update independently. A rejected new estimate never replaces the corresponding previous value.

The 10-, 20-, and 30-second stages are opportunities to produce estimates, not guarantees of a number. The startup countdown remains a separate YAML setting, currently **10 seconds**.

TI’s guide supports rejecting motion-corrupted segments and explains why phase measurements require a consistent range bin. Its thresholds come from a different device and pipeline, so we will calibrate this implementation on the actual setup. [TI Vital Signs Developer’s Guide](https://e2e.ti.com/cfs-file/__key/communityserver-discussions-components-files/1023/vitalSignsLab_5F00_xwr1443_5F00_DevelopersGuide.pdf)

These features remain development-demo behavior. Clinical breath-hold detection and paper-grade accuracy validation are outside this change.

## 2. Preserve the working version first

**No commits, pushes, branch creation, or file edits have occurred during planning.**

### Checkpoint the current branch

The current branch is `vital_signs_own_v13`, currently based on commit `47c3aa1fa1ae997fe1fade0491771c27c507c03a`.

Before feature work:

1. Inspect the working tree and fetch the remote branch.
2. Preserve the calibrated YAML’s current `startup_delay_s: 10`.
3. Correct the discovered test failure: the range-calibration test incorrectly requires a 30-second development countdown. Remove that fixed expectation, validate the configured value, and retain explicit startup tests for `0`, `10`, and `30`.
4. Run the existing calibration, startup, capture, diagnostics, documentation, and line-ending checks.
5. Stage only the reviewed working-demo changes.
6. Commit them as **“Checkpoint calibrated live demo and configurable startup delay.”**
7. Test the exact checkpoint in a clean checkout.
8. Push `vital_signs_own_v13`, verify the remote SHA, and record it as the fallback checkpoint.

The staging allowlist is:

```text
HANDOFF.md
HISTORY.md
notes/approach.md
notes/dca1000_protocol.md
scripts/live_demo.py
scripts/diagnose_live_run.py
scripts/verify_live_demo_artifacts.py
scripts/live_demo_calibrated_config.yaml
src/warmup_select.py
src/range_coordinates.py
tests/test_m2_capture_artifacts.py
tests/test_live_demo_range_calibration.py
tests/test_live_demo_startup_delay.py
config/profile_calibration_1p001m.cfg
config/profile_calibration_verify_1p001m.cfg
config/range_calibration_iwr1642_2026-10-06.json
plans/live_demo_range_calibration_2026-10-06.md
plans/live_demo_startup_delay_2026-10-06.md
```

Inspect the staged diff before committing. Preserve and exclude the separate capture-duration change, MATLAB tooling, IoT plans, and other unrelated work.

### Create the feature branch

Create **`codex/live-motion-recovery-slow-br`** from the pushed checkpoint in a separate clean worktree. This leaves the original checkout available for the demo and preserves unrelated local changes.

Record the checkpoint SHA, feature branch, checkout locations, and both launch commands in the handoff. Push feature milestones separately. Do not merge the feature branch into the working branch until acceptance passes.

Fallback requires stopping the experimental run and launching the calibrated demo from the original checkout. It requires no reset, revert, or deletion.

## 3. Implementation design

### A. Boundaries, configuration, and interfaces

Add a new opt-in development YAML based on the calibrated configuration. Preserve the existing range-bias record and 10-second countdown.

It will configure:

- Movement recovery enabled/disabled.
- Preliminary stages: 10 and 20 seconds.
- Ordinary window: 30 seconds; hop: 3 seconds.
- Motion monitor: 1-second window; 0.25-second hop.
- Stillness confirmation: 3 seconds.
- Extended BR window: 60 seconds; positive band: 0.05–0.50 Hz.
- A hash-bound, accepted detector-calibration record.

Extended BR requires the movement and data-quality guard. Movement recovery can operate alone.

Reject invalid settings, missing or incompatible calibration, replay, prospective capture, and manual bin locking before countdown, output creation, or hardware access.

Keep these files byte-identical relative to the checkpoint:

```text
src/respiration.py
src/vitals.py
src/window_pipeline.py
src/warmup_select.py
scripts/live_demo_config.yaml
scripts/live_demo_calibrated_config.yaml
```

Use new development modules for recovery/cache management, runtime scheduling, breathing assessment, and evidence writing. Add gated integration to the launcher and schema dispatch to verification and diagnosis.

The existing `run_window_dsp(frames, locked_bin, fs, cfg)` interface remains unchanged. New interfaces will provide:

- Frame observations with validity, frame index, and physical features.
- Analysis jobs/results with epoch, selection revision, stage, and frame bounds.
- Immutable display snapshots with independent HR/BR freshness.
- Breathing assessments with state, value, rejection reason, and intermediate evidence.

### B. Responsive acquisition and analysis

Separate expensive analysis from movement detection:

- One controller loop promptly drains incoming frames and owns recovery state.
- One bounded analysis worker runs the existing selector and DSP.
- The GUI consumes immutable display snapshots.
- The controller computes at most **one additional feature FFT per frame**. Existing batch FFTs inside shared DSP remain unchanged.

Queue policy:

- Mandatory 10-, 20-, 30-, and first 60-second jobs are ordered and never coalesced.
- Ordinary rolling updates have one pending slot; newer requests replace older pending requests with a logged event.
- Bound the queue to four milestone jobs, one pending rolling job, and one active job.
- Dependent stages wait for the preceding selection decision.
- Unexpected out-of-order results are buffered within that bound and published in stage order.

Every job owns immutable input samples. Ring-buffer overwrite must not change an in-flight job.

Immediately before publication, atomically check the epoch, selection revision, requested frame bounds, and whether a newer result superseded it. Frame bounds are compared with the requested job, allowing acquisition to advance during computation.

Movement or data loss invalidates pending results immediately. Results older than one ordinary hop are marked expired and retain diagnostic evidence without updating the display or smoother.

At shared 30/60-second update boundaries, apply HR and extended-BR decisions together. A contradictory BR result must not briefly expose a fresh HR.

### C. Movement and target-signal detection

Use the existing float32 Hann window, IQ convention, and SciPy range FFT.

For each 20-frame monitor block, evaluated every five frames, calculate:

- **Presence power, (P):** maximum mean bin power within the corrected distance gate, expressed in dB.
- **Phase activity, (E):** maximum eligible-bin weighted RMS of circular phase increments. Compute increments separately across chirps/RX before averaging their energy; weights are conjugate-product magnitudes.
- **Range-profile change, (G):** half the L1 distance between normalized gate-power profiles from the first and second halves of the block.

Use the existing −12 dB energy eligibility rule for (E). Detect activity **before impulse clipping**.

Process conditions in this order:

1. Invalid frames or discontinuous indices.
2. Missing/weak target signal or invalid feature calculations.
3. Physical movement.
4. Stillness and estimate scheduling.

Zero power, invalid normalization, nonfinite values, or invalid phase weights cannot count as quiet evidence.

Calibrate entry/exit thresholds:

- Motion enters when an enabled feature reaches its entry threshold.
- Motion clears only when enabled features are at or below their exit thresholds.
- For each feature, require strict separation between still negatives and its assigned movement positives.
- Entry is the midpoint between maximum still and minimum assigned-positive training values.
- Exit is the maximum still training value.
- Disable a feature without separation.
- Accept the ensemble only if every labelled movement episode triggers at least one enabled feature.

Presence requires strict empty/occupied separation. Values between the accepted presence limits remain unconfirmed.

Movement, data loss, and target loss clear analysis buffers, smoothing, stage counters, and bin-specific results. Raw acquisition continues.

After an event, require **60 consecutive valid, target-confirmed frames** with qualifying quiet monitor blocks before starting a fresh analysis epoch. Count frames, not overlapping blocks or GUI callbacks.

Invalid HR, invalid BR, and quiet breathing assessments never trigger a movement recovery.

### D. Bin selection and progressive estimates

Use this sequence for initial acquisition and every recovery:

1. **10 seconds:** run the existing selector with the unchanged ordinary DSP configuration. Record a provisional bin.
2. **20 seconds:** reuse that provisional bin.
3. **30 seconds:** run the selector once on the complete fresh 30-second window and commit its chosen bin.
4. **Afterward:** keep that bin until another physical event, data gap, or target loss.

A fallback bin may be recorded, but it does not make an invalid estimate valid. All-DSP-failed selections cannot emit numerical previews. A selector exception or missing bin returns to target-unconfirmed settling.

Every estimate extracts its entire phase window from one bin. Never join phases extracted from different bins.

Preview rules:

- Require existing DSP validity plus at least two implied breathing cycles.
- This gives numerical preview floors of 12 bpm at 10 seconds and 6 bpm at 20 seconds.
- Accepted previews are amber and labelled **Preliminary**.
- Previews and held values never enter the ordinary five-value HR median.
- The first accepted 30-second smoothed HR uses only fresh ordinary estimates.

### E. Cache the 60-second history

Maintain an owned circular complex64 cache for all candidate bins, preserving chirps and RX channels.

For the current profile, this is approximately **15.23 MiB**, rather than a 300 MiB raw 60-second cube. Keep the existing 30-second raw ring for ordinary DSP.

At 60 seconds, reconstruct the complete phase at the committed bin from that bin’s cached samples. This may include samples collected before the 30-second bin decision, provided every sample belongs to the same uninterrupted fresh epoch.

Record the commitment time and selection revision. This permits assessment at 60 seconds, without another 30-second wait.

Use exactly the existing conjugate-product, channel-mean, angle, and cumulative-sum operation order. Restrict the first implementation to the current `delta_before_mean` method with no clutter removal. Prove cache-derived phase matches direct extraction.

### F. Extended breathing assessment

Run a separate demo-only assessment on 1,200 contiguous valid frames. It does not modify shared DSP or feed a new breathing frequency into ECA.

**Positive rate**

Reuse the existing FFT and harmonic-accumulation helpers with the development band 0.05–0.50 Hz.

Require:

- Both methods select the same raw fundamental bin.
- The selected bin is a genuine local maximum against full-spectrum neighbors.
- Calibrated FFT-score and linear HA-score gates pass.
- All six respiratory-activity blocks meet the periodic amplitude threshold.
- The calibrated persistence score passes.

At 60 seconds, bins 3–30 correspond to 3–30 bpm. At boundary bins 3 and 30, report the raw center and record that refinement was unavailable. Interior refinement must remain within band.

The raw grid spacing is 1 bpm. Do not claim that this universally distinguishes 2.99 from 3.00 bpm or establishes ±1 bpm physical accuracy.

**Quiet activity and uncertainty**

Fit and remove the full-window linear trend, then compute separate full-window Fourier projections for:

- Bins 3–30: respiratory-range activity.
- Bins 1–2: activity below the supported rate range.

Divide each reconstructed waveform into six 10-second blocks and calculate RMS amplitudes. Also retain the centered linear-fit RMS as drift evidence.

The quiet threshold is the maximum quiet-training respiratory amplitude. The periodic threshold is the minimum periodic-training amplitude. Require strict separation; the interval between them is unresolved.

“No breathing motion detected” requires:

- A confirmed target signal and complete eligible 60-second window.
- No accepted periodic rate.
- All respiratory blocks at or below the quiet threshold.
- Sub-band activity and drift at or below their calibrated quiet limits.
- No conflicting physical-quality evidence.

Otherwise return unresolved activity. Numeric BR remains invalid/NaN during the quiet state; any previous number stays red.

**Irregular-breathing persistence**

Evaluate the fixed full-window candidate in each 30-second half; do not estimate a new rate from either half.

For each half:

1. Center its respiratory projection and sine/cosine bases at the full-window candidate frequency.
2. Orthonormalize the bases with modified Gram–Schmidt using float64 dot products.
3. Calculate the fraction of signal energy explained by that two-dimensional basis.

Use the minimum of the two fractions as (C). Reject nonfinite energy, nonpositive denominator, or numerically degenerate bases. Permit clipping to [0,1] only within a documented floating-point tolerance.

Derive (T_C) from the midpoint between maximum irregular-negative and minimum periodic-positive training scores, requiring strict separation. Numerical BR additionally requires (C \ge T_C).

This conservative check may leave real, naturally varying or harmonic-rich breathing unresolved. Its failure cannot trigger relocking or establish quiet activity.

**HR interaction**

Preserve existing AHET verification and its respiration limits.

At the same update boundary:

- Confirmed BR below 9 bpm or accepted quiet activity vetoes fresh HR.
- Confirmed BR of 9–30 bpm also vetoes HR if it differs by more than 2 bpm from the exact respiration input used by AHET.
- An unresolved extended assessment adds no new veto beyond existing AHET validity.
- Never pass low BR or zero into ECA or promote the diagnostic no-ECA result.

Preserve prior HR in red when vetoed. Record both respiration inputs, their difference, and the reason.

### G. Evidence and artifact compatibility

Introduce an explicit development evidence version. Preserve legacy output and verification when features are disabled.

For every executed analysis attempt:

- Write one NPZ atomically, close and hash it, then append its CSV row.
- Use primitive arrays loadable with `allow_pickle=False`.
- Save required phase, spectra, selected peaks, respiratory inputs, activity projections, coherence calculations, thresholds, and rejection reasons.
- Record epoch, selection revision, stage, actual bin, corrected range, configuration/source/calibration hashes, and half-open frame bounds.
- Give HR and BR separate window starts when they share an end frame.
- Separate fresh measurement fields from held display fields.

Executed but invalid, failed, expired, or superseded analyses retain diagnostic evidence and invalid measurement fields. Jobs cancelled before execution need event records, not fabricated signal evidence.

Append selection and controller/job events without overwriting initial selection evidence. Release signal arrays after writing.

Update verification and diagnosis to check per-attempt bins and variable window lengths. Missing evidence, hash mismatches, unexplained revisions, or silent field omission must fail verification.

## 4. Guided calibration and physical acceptance

Implement an offline calibration/evaluation tool and an operator guide. The owner controls captures; agents do not operate the radar.

### Training and held-out recordings

Use independent acquisitions, not overlapping windows from one recording.

Minimum coverage:

- Three training clips and two held-out clips per aggregate state.
- Normal, deep, and comfortable slow breathing as movement negatives.
- Periodic and scripted irregular breathing for extended-BR calibration.
- Every movement subtype in both splits, including same-bin torso movement and movement across bins.
- Empty, weak, and strong target scenes.
- Near, middle, and far positions within the existing distance gate.
- At least three complete 60-second quiet-target training epochs and two quiet holdouts.

Use a stationary reflector for full-duration quiet tests. No participant is required to hold their breath for 60 seconds. A brief voluntary pause can exercise display behavior but cannot establish full-duration breath-hold sensitivity.

Record raw hashes, frame validity, operator labels/cue times, profile, placement, configuration, source identity, and seed.

### Threshold derivation and validation

- Derive thresholds from training recordings only.
- Use separate quiet and periodic amplitude bounds.
- Derive FFT, HA, and coherence thresholds from strictly separated negative/positive training scores.
- Use defined no-peak baselines of 0 dB for the FFT score and 0 for HA.
- Require at least three finite independent training acquisitions per coherence group.
- Lock the candidate calibration record before evaluating held-out recordings.
- Held-out results cannot change thresholds.

Bind calibration to algorithm source content and semantic settings: hardware profile, IQ convention, frame rate, range gate/bias, phase method, feature definitions, and assessment timing. Exclude startup delay, display preferences, output paths, and the calibration record’s own hash to avoid circular provenance.

An overlap, failed holdout, incompatible record, or modified threshold produces a rejected calibration. The feature configuration cannot start with it. Preserve failures and continue using the checkpoint branch.

### Live acceptance

After offline calibration passes, rehearse the feature branch on the actual computer and radar:

- Normal stillness produces usable previews and ordinary estimates.
- Movement is detected without stopping capture.
- Previous values become red and remain visible.
- Fresh accumulation begins only after the stillness requirement.
- Stage requests use exactly 200/400/600/1,200 fresh frames.
- Same-position recovery and range-change recovery behave correctly.
- Removed/weak targets never produce the quiet status.
- A strong stationary target exercises the full 60-second quiet branch.
- Irregular activity remains unresolved.
- Raw recording and artifact verification pass.

Guided breathing cadence is an operator cue, not physiological ground truth. Physical accuracy at 3 bpm remains unvalidated without a suitable independent respiration reference.

## 5. Tests, milestones, and completion gates

### Automated test matrix

| Area | Required verification |
|---|---|
| Timing | Test one frame before, on, and after every stage boundary; frame timing is independent of callback speed |
| Event precedence | Movement, invalid data, index holes, and target loss cancel estimates at the exact boundary, including frame 1,200 |
| Recovery | Repeated movement resets correctly; no accumulation through invalid data |
| Historical regression | Invalid HR/BR and quiet breathing never trigger the earlier v9-style relocking failure |
| Selection | Provisional/final ordering, same-bin and changed-bin cases, fallback, all-failed DSP, selector exceptions |
| Phase | Complete single-bin extraction; no stitching; cache equivalence across stages, channel offsets, sparse gates, and wraparound |
| Display | Independent red retention, `--` without history, amber previews, independent replacement, no numeric zero |
| Smoothing | No stale or preview contamination; reset across recovery and final bin changes |
| Slow BR | Exact 3/4/5/6/12/30 bpm, 3.2 bpm, phases, noise, harmonics, and boundary handling |
| Negative signals | DC, exact 2 bpm, drift, out-of-band peaks, weak/noisy signals, and missing targets |
| Quiet classification | Full 60 seconds, all six blocks, threshold equality, sub-band/drift vetoes, perfect zero |
| Persistence | Pure periodic signals, alternating cadence, chirps, sighs, amplitude changes, phase discontinuities, degenerate math |
| HR coupling | Existing AHET validity, low-BR veto, contradictory respiration veto, no new ECA input |
| Concurrency | Busy worker, bounded queues, immutable snapshots, stale completion, same-bin epoch reuse, atomic HR/BR publication |
| Shutdown/errors | Worker failure, interrupted writes, bounded shutdown, no late state mutation or partial indexed files |
| Evidence | NPZ/CSV cardinality, required fields, hashes, revisions, frame bounds, typed arrays, tamper rejection |
| Compatibility | Disabled-feature outputs, legacy artifacts, range correction, shared-file hashes, prospective/replay rejection |
| Reference isolation | No Masimo or sealed prospective reference access |

Use production SampleSwap decoding in the fake-live integration test. Verify exact mirrored ADC bytes, one source start/stop, continuous acquisition identity, frame validity, UI snapshots, events, and artifact verification.

Structural tests enforce bounded storage and one additional feature FFT per frame. A separate representative-machine benchmark must sustain 20 Hz without source-queue overflow, keep controller/UI lag within 0.25 seconds, complete steady analysis within the 3-second hop, and show no continuing memory growth. Report selector latency separately.

### Sequential milestones

1. **Working checkpoint:** correct the startup test, verify, commit, push, and establish the fallback SHA.
2. **Controller and evidence:** implement state transitions, cache, asynchronous jobs, display retention, and versioned artifacts; pass deterministic timing/concurrency tests.
3. **Movement recovery:** integrate provisional/final selection and progressive estimates; pass production-decoder recovery and compatibility tests.
4. **Extended BR:** implement assessment, persistence, HR vetoes, calibration builder, and synthetic tests.
5. **Independent review:** correctness/DSP review, adversarial test review, and targeted reruns for findings.
6. **Physical acceptance:** training, held-out evaluation, real-time benchmark, and owner rehearsal.
7. **Delivery:** update the operator guide, append HISTORY, rewrite HANDOFF, commit and push the feature branch with its acceptance report.

Use the pinned `radar-vitals` environment. Run focused tests after each milestone and the complete synthetic/default suite at checkpoint and final acceptance, with isolated temporary/cache directories:

```powershell
python -m pytest tests -m "not real_data" -q --tb=short `
  --basetemp results/test_tmp/motion_br_final `
  -o cache_dir=results/test_tmp/pytest_cache_motion_br_final `
  --junitxml=results/test_tmp/motion_br_final.xml
```

Record exact commands, source/configuration hashes, failures, and skipped-test reasons. Do not enable sealed-data tests.

**Completion requires** passing automated checks, accepted independent review, accepted calibration and held-out evaluation, responsive real-hardware behavior, verified artifacts, and a pushed feature branch. Until those gates pass, the pushed `vital_signs_own_v13` checkpoint remains the demo version.

### Verified checkpoint and test-environment revision (2026-10-07)

The working checkpoint is pushed and verified by `git ls-remote` as
`7f3dd2bfd405cdd73d5ae395347baeb991213acf` on `vital_signs_own_v13`.
The feature branch is `codex/live-motion-recovery-slow-br`, in the separate
`results/motion_br_worktree` checkout under the original repository.
The exact clean checkpoint passed 3305 tests, with 16 optional-artifact skips
and one real_data deselection (`results/test_tmp/checkpoint_clean2.xml`).
Its six protected-file hashes are recorded in
`config/live_motion_checkpoint_2026-10-07.json`.

Full-suite basetemp must be outside the repository because an existing provenance
test asserts this, and nested pytest requires Windows-temp permissions. Use
isolated external basetemp directories for complete-suite runs; retain isolated
cache/JUnit outputs under results/test_tmp. The clean checkout also requires the
two existing gitignored literature PDFs for provenance tests; copies were hash
verified. No recordings or Masimo references were copied into the feature tree.
The original checkout's existing optional development-data regressions run
automatically when recordings exist, despite the not-real_data marker selection;
this was discovered after its default suite ran. No resulting reference values
inform feature implementation or calibration. All subsequent feature verification
runs in the isolated checkout, where those optional tests skip explicitly.

## 6. Implementation reconciliation — 2026-10-07

This note records implementation interpretation without changing the supplied design above. The exact 20-second, 6-bpm value is only the two-cycle eligibility floor; the existing shared DSP first-in-band-bin gate may still reject a 20-second estimate. A stillness monitor’s first quiet 20-frame block may retrospectively validate the covered 20 frames, while the recovery controller still requires 60 consecutive valid, target-confirmed frames before starting a fresh analysis epoch. The preflight must lock and verify the capture manifest before countdown. If the required detector calibration is absent or incompatible, startup fails closed; no physical validation is claimed or implied by synthetic checks.

### Independent breathing review clarification

Persistence evaluates the fixed full-window **raw fundamental-bin center** in
both halves; interior reported BR may use the shared helper's band-bounded
refinement. This follows the raw-bin FFT/HA agreement decision and is explicitly
recorded as `raw_full_window_bin_center`. Stable off-grid activity can therefore
be unresolved. Calibration binds this policy and source content; an interior
10.4-bpm test and the 3.2-bpm boundary test verify it. No physical sensitivity or
accuracy follows from these synthetic cases. Review also required a stable
outcome schema and reconstructable breathing/HR-veto evidence; those are
acceptance requirements before feature integration is accepted.

### Asynchronous selection and mixed-window bounds

An expired selector attempt can advance the current, identity-checked selection
control decision to unblock the mandatory dependent stages; its expired HR/BR
measurements never publish or enter smoothing. Obsolete epochs, revisions, bins
and request bounds cannot apply a control decision. This prevents a busy worker
from permanently blocking the 20/30/60-second sequence while retaining the
one-hop measurement expiry rule. Every applied selection decision is explicit
in per-attempt evidence, independently of its measurement disposition.

A job sharing the 30-second HR and 60-second BR end frame records overall
half-open bounds covering 1200 frames, plus hr_window_start=end-600 and
br_window_start=end-1200. It owns the last 600 raw frames and a complete owned
candidate-bin cache snapshot; no phase stitching is used. The same convention
applies to first-60-second and subsequent rolling assessments.

### Missing-stream and cancellation clarification

Independent runtime review found that a silent source can return no frame
without supplying an invalid frame or a discontinuous index. The development
runtime therefore uses the existing configured monitor hop, 5/20 = 0.25 seconds,
as its stream-starvation interval after the first frame. One latched
`data_gap/source_frame_timeout` event holds historical readings red, clears fresh
buffers and cancels pending work. Acquisition continues, no frame is fabricated,
and resumed input must satisfy the same 60-frame stillness rule. This is a
configuration-derived data-delivery guard, not a fitted physical-motion threshold.

Dispatch into the bounded worker queue does not prove execution. Worker-start
tracking distinguishes cancellation before execution (events only) from an
executed analysis interrupted at bounded shutdown (one typed attempt with an
explicit interruption reason). Tombstones prevent later completion from changing
display state or appending duplicate signal evidence.
