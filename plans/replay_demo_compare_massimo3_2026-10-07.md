# Recorded-session radar/Masimo comparison player with coverage

## Implementation review clarification - 2026-10-07

The owner approved implementation in the separate replay worktree. Independent
reconciliation against the actual repository returned READY WITH MINOR CHANGES.
Milestone 1 must additionally bind the selected ADC, metadata and warmup files
and frame counts to the committed radar-only m3 entry in
`experiments/m8_ahmed_transfer/capture_registry.yaml`, then compute and compare
their actual hashes. Initial/final self-hashing alone detects changes during a
pass but could accept an already substituted file before preparation. This
clarification uses the existing radar registry authority and does not provide
reference paths or values to radar processing. The actual ADC hash must still be
computed through the read-only playback handle; a registry value is an expected
identity, not evidence that those input bytes were read. Add pre-run substitution
tests as a Milestone-1 acceptance gate. No other processing contract changes.

The workflow audit also clarified the M2/M3 dependency: M2 may introduce tested,
radar-only controller event/state-interval schemas and minimal typed display/lease
primitives required for baseline evidence. M3 remains the milestone that accepts
full reconstructed coverage, passive reference mapping, and UI presentation.
This is sequencing clarity, not a change to coverage or scientific contracts.

Independent evidence review clarified the final verifier's source binding:
saved-phase-to-DSP recomputation proves internal consistency, but cannot reject
jointly invented phase, spectra and rates. M3 final verification must authorize
the registered ADC through the session/source boundary, stream it with a bounded
600-frame baseline ring (1200 for advanced windows), and compare production phase
at each indexed attempt's actual bounds/bin before checking saved-phase DSP.
Missing source inputs must produce an explicit incomplete/refused verification,
never a complete-verification claim. M2 may accept explicitly scoped internal
evidence checks while preserving all request bounds/bin/lineage needed for this
M3 verification. This strengthens the already required rejection of invented
values; it changes no estimator, admission rule or coverage definition.

## 1. Goal, decisions, and verified starting point

Build a separate development application that replays `massimo3` chronologically, calculates radar estimates during playback, and displays:

- Radar HR and BR.
- Recorded Masimo pulse rate and breathing rate.
- Reference summaries over each radar measurement window.
- Radar HR, numeric BR, and simultaneous HR+BR coverage.
- Separate Masimo availability and PR quality coverage.
- Movement recovery and extended-BR behavior when compatible physical calibration permits it.

The application must distinguish accepted estimates, preliminary estimates, held historical values, missing measurements, and quiet breathing assessments. Coverage measures **how much elapsed recording time actually has accepted numbers available on screen**. It does not measure accuracy.

Implementation will happen in another thread after approval. This planning thread makes no implementation changes.

### Decisions agreed with the owner

| Question | Decision |
|---|---|
| Initial recording | `20260728_224902_live_demo_massimo3` |
| Radar processing | Recalculate during playback |
| Initial usable mode | Ordinary HR/BR replay |
| Advanced features | Implement a separate calibration-gated replay mode |
| Playback controls | Pause/resume, restart, 0.5x, 1x, 2x, 4x |
| Arbitrary seeking | Excluded from this version |
| Masimo presentation | Current recorded 1 Hz readings plus descriptive matched-window summaries |
| Coverage presentation | Radar coverage and separate reference availability |
| Historical frame validity | Explicit development-only assumption permitted under the strict checks below |
| Reference alignment | Recorded timestamps; approximate-origin warning; no fitted time offset |
| Agreement metrics | No numerical differences, MAE, RMSE, or accuracy claims in this application |

### Verified repository state

- Working fallback branch: `vital_signs_own_v13`.
- Fallback checkpoint: `7f3dd2bfd405cdd73d5ae395347baeb991213acf`.
- Original checkout: `C:/Users/josemsosag/Desktop/vitals_radar_3`.
- Existing feature branch: `codex/live-motion-recovery-slow-br`.
- Existing feature HEAD: `dcda615d1a2984848882ed7da6641860331577b1`.
- Existing feature checkout: `C:/Users/josemsosag/Desktop/vitals_radar_3/results/motion_br_worktree`.
- The feature checkout was clean during plan review. Unrelated changes and untracked work remain in the original checkout.
- Movement recovery and extended BR have passed their software checks, but physical calibration and acceptance remain incomplete.
- The existing live feature preflight deliberately rejects replay. That prohibition will remain unchanged.

Saving this plan and its implementation prompt produces documentation-only uncommitted work in the existing feature checkout. Preserve those files and the associated HISTORY/HANDOFF updates. Copy the plan and prompt into the new implementation worktree after creating it from the specified committed base; uncommitted files are not copied by Git worktree creation. Do not reset, stash, or commit unrelated work to make the existing checkout clean.

### Verified recording facts

`massimo3` contains:

- 1,573,519,360 ADC bytes.
- 12,005 complete frames at 20 Hz.
- 600.25 seconds of recording.
- 256 ADC samples, four RX channels, and 32 chirps per frame.
- SampleSwap decoding with `iq_swap: true`.
- Historical zero range bias.
- A registered, hash-bound development Masimo reference.
- No recorded exact frame-zero UTC timestamp.
- No per-frame validity file.

The recorded reference SHA-256 is:

```text
92054b471a04eefdbe4de45988846bab503cf61a0e5c619e5d8f545ef0a965cb
```

The raw ADC hash must be calculated during implementation preflight; it must not be invented or copied from an unavailable field.

### Isolation and protection

After approval, create:

```text
Branch:   codex/replay-masimo-compare
Base:     dcda615d1a2984848882ed7da6641860331577b1
Checkout: C:/Users/josemsosag/Desktop/vitals_radar_3/results/replay_compare_worktree
```

Inspect Git state and branch-name availability before creation. Preserve both existing checkouts. Do not merge into either existing branch.

Keep all calibration-fingerprinted production files byte-identical to the starting feature commit, including the existing live launcher, radar reader, and `src/live_motion/` modules. Preserve the six previously protected scientific/configuration files and the calibrated YAML's 10-second startup setting.

The only existing production module expected to change is `src/reference_access.py`, through the narrow development-data-root extension described below.

## 2. Observable behavior and measurement contracts

### Display layout

Implement a native desktop dashboard using the pinned PySide6 and pyqtgraph dependencies:

1. Header: recording name, processing mode, playback state, speed, elapsed/total recording time.
2. Persistent warnings: approximate reference alignment; legacy validity assumption when applicable; unavailable advanced features.
3. Four main cards: radar HR, Masimo PR, radar BR, Masimo RRp.
4. Descriptive reference summaries associated with the current radar windows.
5. Coverage panel: radar HR, numeric BR, both radar values, and separate Masimo availability.
6. Two comparison plots: heart rate and breathing rate.
7. Status panel: accumulation stage, bin/range, recovery reason, measurement-window age, and analysis lag.
8. Pause/resume, restart, speed selector, and a read-only progress bar.
9. Footer: development replay; not clinical or paper-grade validation.

The generated mockup guides appearance only. Its values and curves are fictional and must never enter the implementation.

Plots reveal only data available by the current playback position. Future portions remain blank. Do not preload future curves onto the display.

Use teal for accepted radar traces and purple for reference traces. Use explicit state labels alongside colors:

| Radar state | Presentation | Accepted numeric coverage |
|---|---|---|
| Accepted ordinary estimate | Accepted/green status | Included |
| Preliminary estimate | Amber, "Preliminary" | Excluded |
| Historical value | Red, "Held" | Excluded |
| No history | `--` | Excluded |
| Unresolved breathing | Explicit unresolved status; previous BR held red | Excluded |
| Accepted quiet assessment | "No breathing motion detected"; no numeric zero | Separate quiet-status coverage |

Track HR and BR independently. Rejection of one must not erase or replace the other.

### Recording clock and controls

Recording time is authoritative:

```text
recording_elapsed_s = processed_frame_count / 20
```

Wall-clock time controls pacing and records computation latency. It must not determine stage boundaries, reference alignment, or coverage denominators.

Speed changes preserve the current frame position and re-anchor pacing deadlines. They must not skip, duplicate, or reorder frames.

Pause is acknowledged by the controller:

- Clicking Pause first shows "Pausing...".
- The controller completes its current atomic boundary and acknowledges "Paused".
- After acknowledgment, the source cursor, processed frame count, publication state, elapsed recording time, and coverage remain unchanged.
- One active analysis may finish into the bounded result slot.
- Its result remains unpublished until resume.
- No new queued analysis begins while paused.
- Resume re-anchors pacing so paused wall time causes no catch-up burst.
- Stop remains available while paused.

Restart closes the current pass and starts a new pass from frame zero with a new generation identifier, fresh controller state, empty smoothing, and zero coverage. Preserve the previous pass's artifacts.

Restart requires clean shutdown of the old worker. A shutdown exceeding five seconds marks the pass failed and blocks restart; do not accumulate abandoned workers.

`--duration-s` limits processed recording frames:

```text
limit = min(total_frames, floor(duration_s * 20))
```

Reject nonfinite, nonpositive, or sub-frame limits. Without this option, process the complete recording.

### Accepted display coverage

Coverage comes from exact controller application events, never GUI callback counts or the number of successful analysis windows.

For a published result, define:

```text
A = application_frame + 1
```

`A` is the next unprocessed recording-frame boundary.

Preserve the existing publication-age guard: a result more than one ordinary hop - 60 frames - behind `A` cannot publish.

An accepted ordinary number receives a display lease:

```text
[A, A + 60)
```

Clip the lease at an earlier applicable rejection, physical/data-quality event, or end of playback. Renewal exactly at the endpoint is contiguous. A later renewal leaves an explicit held/red gap.

This lease is a presentation availability rule. Display the measurement-window age separately; do not call the lease proof of physiological freshness.

At `N` processed frames:

```text
HR coverage   = accepted HR frames / N
BR coverage   = accepted positive numeric BR frames / N
Both coverage = intersection of accepted HR and BR frames / N
```

Rules:

- Include initial accumulation, recovery, invalid frames, and target-loss periods in the denominator.
- Exclude paused wall time and pre-play preparation.
- Never backfill coverage to a measurement-window start or end.
- Never sum overlapping 30- or 60-second input windows.
- Exclude previews, held values, missing values, quiet assessments, rejected results, and expired/superseded results.
- Track quiet-status coverage separately using its own accepted display lease.
- Keep coverage independent of reference availability and agreement.
- Show numerator seconds, denominator seconds, and percentage.
- Round only presentation; preserve exact frame counts.
- At zero processed frames, show `--`.
- A stopped pass reports coverage for its elapsed portion and is labelled partial.
- At EOF, freeze the denominator. Analyses completing afterward cannot retroactively earn coverage.

A hung worker must cause accepted display leases to expire into held/red state rather than leave numbers indefinitely green.

Playback speed and machine load can change publication timing and therefore coverage. Display and persist speed. Use 1x for live-style acceptance; do not interpret cross-speed coverage differences as physiological differences.

### Masimo display and reference availability

Load reference data only through the guarded reference API.

Use:

- `Beats / min` for PR.
- `Breaths / min` for RRp.
- Integer Unix `Timestamp` for alignment.
- Perfusion Index for PR quality labelling.

Never align using the CSV's Date/Time text, value matching, cross-correlation, or a fitted/manual offset.

For `massimo3`, use the existing `start_wall_utc` fallback semantics. Keep this warning visible:

> Approximate reference alignment - exact radar frame-zero UTC was not recorded.

Represent the anchor internally in integer UTC microseconds. At 20 Hz, a frame interval begins at:

```text
anchor_us + frame_index * 50,000
```

A recorded reference row occupies its one-second timestamp bucket `[Timestamp, Timestamp + 1)`. Show it only when the playback cursor reaches that bucket. Missing buckets produce `--`; do not carry a value across a missing second, interpolate, or smooth it.

The main Masimo cards show the current logged sample with its source timestamp:

- Finite positive PR remains visible when PI is low, with a red/low-quality label.
- PI-qualified PR requires finite PI >= 0.5.
- Missing/nonpositive PR or RRp displays `--`, while preserving original values in evidence.
- RRp availability is not gated by PI. Show any PI warning separately.

Reference availability uses the same elapsed-frame denominator as radar coverage, with separate counts for:

- Positive finite PR present.
- Positive finite PR with qualifying PI.
- Positive finite RRp present.

These measure reference-card availability, not agreement or comparator admission.

For each radar estimate, calculate a **descriptive** reference summary over that estimate's actual half-open window:

- HR: median of positive finite, PI-qualified PR.
- BR: median of positive finite RRp.
- Record window duration, expected timestamp count, observed count, finite count, and PR-qualified count.
- Show `--` if no eligible values exist.
- Label the result "Window summary - descriptive".

Use each channel's actual bounds. At an extended update, HR can use 30 seconds while BR uses 60 seconds.

Do not apply the repository's fixed 30-second comparator admission rules to 10-, 20-, or 60-second summaries. Comparator admission badges and agreement metrics are outside this version.

Masimo RRp is derived from plethysmography. The cited manual specifies a 4-70 rpm display range; this application cannot validate radar performance at 3 bpm using that reference. Record this limitation without suppressing genuine radar outputs. [Masimo operator manual](https://techdocs.masimo.com/globalassets/techdocs/pdf/lab-10169a_master.pdf)

## 3. Architecture, interfaces, and processing modes

### New components and ownership boundaries

Use a new `src/replay_compare/` package with these responsibilities:

| Component | Responsibility |
|---|---|
| `session.py` | Registered capture resolution, metadata/configuration checks, input integrity, validity provenance |
| `source.py` | Production decoder adapter and bounded sequential ADC reading |
| `clock.py` | Playback pacing, pause acknowledgment, speed changes |
| `engine.py` | Controller ownership, baseline processing, motion adapter, worker lifecycle |
| `coverage.py` | Display leases, state intervals, exact coverage accounting |
| `reference.py` | Passive timestamp mapping, descriptive summaries, reference availability |
| `evidence.py` | Replay artifacts and independent verification |
| `view_model.py` | Immutable GUI snapshots |
| `ui.py` | Lazy-loaded PySide6/pyqtgraph dashboard |

Entrypoints:

```text
scripts/replay_demo_compare.py
scripts/replay_demo_compare_config.yaml
scripts/verify_replay_demo_artifacts.py
```

Keep scientific logic outside the GUI and executable script.

Public replay types must include:

- `ReplaySessionSpec`: capture identity, input hashes, geometry, frame count, effective configuration, timestamp origin, validity provenance.
- `ReplayOptions`: mode, initial speed, frame limit, output location, explicit legacy-assumption choice.
- `PlaybackSnapshot`: generation, processed boundary, playback state, independent HR/BR states, quiet status, bin/revision, lag, reference values, coverage.
- `CoverageSnapshot`: exact numerator/denominator frame counts and separate state categories.

Snapshots must be immutable and contain no writable signal buffers.

### Cross-worktree input access

Recordings remain in the original checkout; do not copy the 1.57 GB ADC file into the new worktree.

Add an optional keyword-only `development_data_root` to the development reference-loading/listing APIs.

Its contract:

- The committed registry and expected reference hashes always come from the active source checkout.
- The supplied root changes only where registered development-relative input paths resolve.
- Resolve paths strictly and prove containment beneath that root.
- Preserve exact `results/live_demo/<registered capture>/...` identity.
- Reject traversal, escaping links, prospective/sealed paths, and P001-style protected components before reference bytes are opened.
- Check both supplied and resolved paths.
- Reject the option when prospective scoring authorization is supplied.
- Preserve all existing behavior when the option is absent.
- Do not monkeypatch `REPO_ROOT`, introduce arbitrary CSV overrides, or weaken the reference registry.

Resolve the selected capture without requiring every other registered recording to exist.

Only the selected registered capture's fixed ADC and metadata filenames are accepted. No raw-file override, CSV override, manual bin, or prospective capability option belongs in this player.

### ADC integrity and bounded decoding

Do not reuse `ReplayFrameSource.start()`, which materializes the full recording.

Read ADC sequentially, one complete frame at a time. For `massimo3`, that is 131,072 bytes per frame.

Reuse the exact production implementation through a small geometry-only adapter calling `LiveFrameSource._decode_frame`. The adapter must perform no IQ transformation of its own and must never initialize or start hardware.

This private dependency is deliberate to preserve calibration-fingerprinted production files. Protect it with bit-exact tests for SampleSwap 0 and 1 against known bytes and `read_adc_bin`.

Input preparation must:

1. Authorize the capture and validate configuration/mode constraints.
2. Validate metadata and whole-frame file size.
3. Open the raw file read-only.
4. Hash it in bounded chunks through the same handle used for playback.
5. Rewind that handle.
6. Record file identity/stat information.
7. Stream frames without loading a full cube.
8. Verify final file identity and content integrity before accepting the run.

A complete pass can compare its streamed digest with the initial full-file digest. Partial passes require a final bounded full-file hash. Changed/replaced inputs produce a failed integrity result.

No input files are modified or promoted into canonical research data.

### Baseline mode

Baseline works without detector calibration.

Use the capture metadata's scientific configuration with current committed production DSP. Normalize only documented implicit defaults, including historical zero range bias and no clutter removal. Persist the original and effective configurations and their hashes.

Label this as current-software reprocessing of a historical capture, not reproduction of its original saved display.

Processing:

- Maintain a 600-frame raw ring.
- At the first complete 600-frame window, run the existing candidate-bin selector.
- Reuse its selected DSP result where available.
- After selection succeeds, keep that bin.
- Request ordinary updates every 60 frames using the latest complete 600-frame window.
- Use the existing selector, `run_window_dsp`, job/result types, and bounded scheduler.
- Reuse `production_analysis_function` for execution and intermediate extraction with a private baseline settings contract: extended assessment disabled, no motion thresholds, no calibration identity.
- Do not instantiate `RecoveryController` or `LiveMotionRuntime` for baseline.

Selection failures:

- An exception, missing bin, or all-DSP-failed selection cannot emit numbers.
- Save the failed attempt.
- Cancel dependent rolling requests with events.
- Retry selection at the next ordinary boundary using the latest complete window.
- Retain at most one pending selector retry.
- A fallback bin does not turn an invalid estimate into a valid estimate.

Presentation is deliberately stricter than the legacy finite-value smoother:

- Only valid, AHET-verified, finite positive ordinary HR enters the five-value median.
- Only valid positive BR becomes accepted.
- Invalid HR/BR independently hold their previous values in red.
- Never display diagnostic no-ECA HR, fallback HR, or rejected raw HR as accepted.
- Invalid vital estimates never trigger relocking.

Document this smoothing-admission difference. Numerical equivalence tests target the unchanged per-window DSP, not the legacy smoother's treatment of rejected finite values.

A recorded invalid frame or discontinuity clears analysis buffers, pending jobs, bin-specific state, and smoothing. Hold historical values red and require a new complete contiguous ordinary window. This data-integrity reset does not introduce motion detection into baseline mode.

### Calibration-gated motion mode

Implement a separate replay-only preflight. Do not weaken or call around the existing live launcher's replay prohibition.

Require:

- An explicitly supplied motion configuration.
- Movement recovery enabled.
- Extended BR enabled only when its movement guard is enabled.
- An accepted physical calibration record.
- Correct calibration file hash, source identity, semantic settings, and capability.
- Acquisition geometry, IQ convention, frame rate, and range-coordinate compatibility with the recording.
- Valid frame-validity provenance under the policy below.

Call the existing calibration validator with the **active source checkout** as software authority, then parse settings using the validated record.

The currently tracked motion configuration has a nonzero range bias and no accepted calibration. It cannot enable advanced replay for `massimo3`. Report the precise incompatibility; never silently substitute zero bias or edit the calibration record.

Reuse unchanged:

- `RangeFeatureExtractor`.
- `RangeBinCache`.
- `RecoveryController`.
- `BoundedAnalysisScheduler`.
- `production_analysis_function`.
- Motion attempt evidence writing and numerical verification.

Build a replay-specific orchestrator instead of using `LiveMotionRuntime`, whose wall-clock starvation behavior is incompatible with pause and speed changes.

Preserve all implemented scientific behavior: ordered progressive stages, single-bin phase extraction, motion/data-quality precedence, independent history, preview exclusion from smoothing, extended positive/quiet/unresolved decisions, and atomic HR vetoes.

Replay provenance must remain `recorded_development_replay`. It must never masquerade as `live_dca1000`.

### Legacy validity assumption

Prefer a verified per-frame validity map when available.

For advanced `massimo3` replay without one, require the explicit flag:

```text
--allow-legacy-validity-assumption
```

Limit this exception to the exact registered `massimo3` capture. Require:

- Completed live-capture metadata.
- Positive integer received-packet count.
- Explicit zero dropped packets.
- Explicit zero zero-filled bytes.
- Complete raw frames.
- Integer terminal truncation count between zero and one frame.
- Exact byte conservation using the production packet payload size.
- No present diagnostic counter proving malformed/short-packet loss.
- Verified raw and metadata hashes.

For this recording, the consistency check is:

```text
1,080,741 * 1,456 - 39,536 = 1,573,519,360 bytes
```

The terminal partial bytes were discarded; do not append or synthesize them.

Missing newer diagnostic counters remain `null`/unknown. Do not manufacture zero values.

When permitted, label the validity provenance:

```text
legacy_zero_loss_inferred_v1
```

Keep the assumption visible and attach it to evidence and coverage outputs. An internal all-true execution mask is an assumption, not an observed validity map.

This replay - including any quiet assessment - cannot become calibration training, held-out acceptance, physical validation, or clinical evidence.

### Controller, concurrency, and boundary order

One controller thread owns frame advancement, state, result application, coverage, and evidence indexing. One bounded worker performs expensive analysis. The Qt main thread only consumes immutable view models and operates controls.

Bounds:

- Baseline: one active analysis, one pending rolling request, and at most one pending selector retry.
- Motion: existing four mandatory milestone requests, one pending rolling request, and one active job.
- One completed-result slot.
- Owned immutable inputs for every dispatched job.
- No accumulation of raw recording cubes or completed signal arrays.

At the same boundary, process physical/data-integrity events before applying completed results. A movement or invalid frame must not briefly expose a fresh result that it immediately invalidates.

Validate generation, epoch, selection revision, requested bounds, actual bin, age, and supersession before publication. Reference data must never enter these checks.

Pacing waits and pause are not physical data gaps. A reader/decoder failure is an explicit replay-system failure. Do not treat a slow disk as evidence of target movement.

## 4. Evidence, tests, and acceptance

### Artifact contract

Use a separate replay run schema and verifier. Preserve existing live artifact schemas and verification unchanged.

Write under:

```text
results/replay_compare/<UTC run id>/pass_<generation>/
```

Required artifacts:

| Artifact | Contents |
|---|---|
| Run metadata | Capture/mode/provenance, hashes, configuration, source commit, seed, geometry, origin, validity policy, frame limit, completion |
| Attempt index and NPZ files | Every executed analysis, its disposition and intermediate evidence |
| Controller/application events | Exact publication boundaries, resets, expiries, revisions and cancellations |
| Display state intervals | Half-open spans, independent channel states, originating attempt IDs |
| Reference audit | Logged reference values and descriptive-window summaries used by the display |
| Playback events | Pause acknowledgments, resume, speed changes, restart, stop |
| Performance telemetry | Analysis/selector latency, lag, queue bounds, memory observations |
| Summary and manifest | Exact coverage counts, limitations, completion state and artifact hashes |

Baseline requires its own explicit attempt schema. Do not fabricate motion `AttemptDisposition` objects or calibration hashes.

For each executed attempt:

1. Write primitive arrays to an atomic NPZ.
2. Close the file.
3. Hash it.
4. Append the indexed row.

Save available phase, spectra, peaks, AHET decisions, respiration inputs, bin/range, frame bounds, native validity, presentation eligibility, rejection reasons, application boundary, and displayed/held values.

Unavailable components use typed empty arrays and explicit failure reasons. Cancelled-before-execution jobs receive event records only.

Release signal arrays after persistence.

The verifier must independently:

- Check hashes, primitive types, cardinality, required fields, bounds and file containment.
- Reconstruct selector/dependency/revision lineage.
- Recompute HR admission and accepted-only median history.
- Recompute application leases and independent HR/BR/both coverage.
- Verify quiet is separate from numeric BR.
- Check reference mapping, summaries, counts and availability against guarded source inputs.
- Verify calibration and replay provenance for motion mode.
- Reject tampering, missing evidence, unexplained revisions, partial indexed files, and invented values.
- Report missing source inputs explicitly rather than claim complete verification.

### Deterministic and integration tests

Use synthetic fixtures and controlled clocks/thread events; avoid sleep-based concurrency assertions.

Required groups:

1. **Input and reference boundary:** registered capture resolution, external development root, traversal/links, protected paths, reference hash mismatch, prospective-option rejection, missing metadata, empty/truncated ADC, and integrity changes.
2. **Decoder:** exact mirrored SampleSwap bytes; channel/chirp ordering; SampleSwap 0/1 equivalence; bounded reads; no whole-recording cube.
3. **Baseline DSP:** exact 600/60-frame windows, selector DSP reuse, successful/fallback/all-failed/exception cases, fixed-bin operation, valid-only HR median, and independent holding.
4. **Advanced gates:** absent/rejected/synthetic/incompatible calibration, incorrect hashes, nonzero historical bias, capability mismatch, invalid legacy assumption, and unchanged live replay prohibition.
5. **Timing:** one frame before/on/after all boundaries, frame-zero accumulation, pause acknowledgment, speed re-anchoring, integer duration limits and EOF.
6. **Coverage:** application-only start, no backfill, exact renewal, one-frame late gap, expiry, event clipping, partial runs, quiet, preview exclusion, and independent channel unions.
7. **Reference behavior:** fractional UTC origin, integer-second boundaries, duplicate handling, missing seconds, low PI, nonpositive readings, no future values, and separate 30/60-second channel windows.
8. **Concurrency:** busy worker, bounded queues, rolling replacement, immutable input, out-of-order/stale results, paused completion, restart generation rejection, bounded shutdown and no late mutation.
9. **Evidence:** NPZ/CSV cardinality, numerical component verification, interval reconstruction, schema failures, hashes, tampering and interrupted writes.
10. **UI:** labels, states/colors, unavailable-mode explanations, plot gaps, future blanking, pause/restart/EOF, no numeric zero BR, and lazy imports/headless operation.
11. **Reference isolation:** change synthetic PR/RRp/PI values and missingness drastically while keeping ADC and execution events fixed; radar jobs, bins, DSP evidence, estimates, dispositions and radar coverage must remain identical.
12. **Regressions:** existing reference, startup, range correction, legacy artifacts, motion controller/scheduler/cache/breathing/evidence, protected hashes and line endings.

Exact coverage regression fixture:

- `N = 800`, hop = 60.
- Accepted HR+BR published at `A = 665` receives `[665,725)`.
- HR-only renewal at `A = 725`; BR is held.
- Movement at boundary 750 ends HR's renewed lease.

Expected:

```text
HR:   85 / 800 frames = 10.625%
BR:   60 / 800 frames = 7.5%
Both: 60 / 800 frames = 7.5%
```

Also test a result exactly one hop old at publication, one frame beyond the limit, renewal exactly at expiry, and completion during pause.

Identical controlled frame/result event traces must yield identical coverage at every speed. Naturally scheduled real runs are allowed to differ.

### Commands and software acceptance

Use the pinned `radar-vitals` environment serially.

Focused tests run after each milestone. Final acceptance runs the complete non-real-data suite with external isolated temporary storage and repository-local cache/JUnit artifacts:

```powershell
$replayTestRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("radar_replay_compare_" + [guid]::NewGuid().ToString("N"))

C:/ProgramData/anaconda3/Scripts/conda.exe run --no-capture-output -n radar-vitals python -m pytest tests -m "not real_data" -q --tb=short `
  --basetemp "$replayTestRoot/pytest" `
  -o cache_dir=results/test_tmp/pytest_cache_replay_compare_final `
  --junitxml=results/test_tmp/replay_compare_final.xml
```

Record exact commands, commits, hashes, results, failures, warnings and skip reasons. Do not enable sealed-data tests.

After synthetic acceptance:

- Run complete baseline `massimo3` playback at 1x.
- Run smoke checks at the other supported speeds and exercise pause/restart.
- Verify the resulting artifacts.
- Record measured coverage without a target percentage and without tuning.
- Observe UI/controller lag, analysis latency, actual throughput and memory.
- At 1x, require no omitted source frames and responsive controls/UI; use the existing 0.25-second responsiveness target.
- Report selector latency separately.
- Require bounded storage and no continuing memory growth.
- Capture an actual application screenshot from the verified run.

These checks validate recorded playback. They do not satisfy the separate real-hardware benchmark or physical acceptance gates.

## 5. Sequential milestones, agents, and delivery

### Agent responsibilities

The primary agent owns integration, sequencing, shared-file coordination, milestone gates and final verification.

| Role | Explicit ownership/responsibility |
|---|---|
| `workflow_manager` | Read-only milestone dependencies, isolation and acceptance-gate audit |
| `python_expert` | Production replay package, new scripts/configuration, narrow reference-root API extension |
| `test_engineer` | New replay tests and necessary reference-boundary/audit regression additions |
| `research_agent` | Read-only scientific/reference questions and pinned GUI API checks |
| `plan_reviewer` | Independent read-only review of this plan and material revisions |
| `code_reviewer` | Independent read-only decoder, DSP, concurrency, calibration, reference-boundary and evidence review |
| `doc_generator` | Operator guide and verified delivery report after behavior passes tests |
| Primary agent | Plan persistence, integration, history/handoff, staging, commits, pushes and final acceptance |

Tell every editing agent that they share the codebase, must preserve others' work, and must coordinate shared-file edits. Parallelize only independent responsibilities. Reviewers must not implement the changes they review.

### Milestone 0 - approved plan and clean isolation

- Re-read AGENTS, CLAUDE and the feature handoff.
- Verify starting Git state and hashes.
- Create the new branch/worktree from the specified feature commit.
- Save/copy the approved plan as `plans/replay_demo_compare_massimo3_2026-10-07.md` in the new worktree.
- Preserve the source copy and planning-only HISTORY/HANDOFF edits in the existing feature checkout.
- Record the original fallback and feature checkout locations.
- Establish protected-file hash checks.

**Gate:** clean isolated branch, approved plan saved, unrelated work untouched.

### Milestone 1 - authorized session inputs and streaming source

- Implement development-data-root support.
- Implement metadata/integrity validation, decoder adapter, sequential source and playback clock.
- Implement strict legacy-validity checks and provenance.
- Add deterministic input, decoder, clock and boundary tests.
- Obtain independent review of the reference-access change.

**Gate:** bounded production decoding and guarded cross-worktree inputs pass; no hardware access; protected files unchanged.

### Milestone 2 - baseline processing and evidence

- Implement baseline controller, bounded worker and selector dependencies.
- Implement strict accepted-only smoothing and independent held values.
- Implement application events, baseline evidence and verification.
- Test failures, stale results, coalescing, shutdown and restart isolation.

**Gate:** baseline processing is reproducible and every executed attempt is evidenced; no unverified HR promotion.

### Milestone 3 - reference, coverage and dashboard

- Implement passive reference mapping and descriptive window summaries.
- Implement exact leases, coverage and interval reconstruction.
- Build the agreed Qt dashboard and controls.
- Add GUI smoke and reference-isolation tests.
- Verify pause acknowledgment and no future-data display.

**Gate:** all displayed numbers trace to evidence; coverage reconstructs exactly; reference changes cannot affect radar.

### Milestone 4 - gated advanced replay

- Add replay-only calibration preflight.
- Integrate unchanged recovery/controller/cache/analysis components.
- Preserve milestone order, HR coupling and scientific safeguards.
- Test synthetic advanced execution through production decoding.
- Test all unavailable/refusal paths.

**Gate:** advanced replay software passes, but remains unavailable for real `massimo3` execution until compatible physical calibration exists. Synthetic fixtures cannot unlock production.

### Milestone 5 - independent review and complete acceptance

- Independent DSP/runtime and reference-boundary/evidence review.
- Independent adversarial test review.
- Resolve findings and record disagreements.
- Run focused reruns for changes, then the complete suite.
- Perform verified baseline development replay and performance observations.

Do not require any particular HR, BR, agreement, or coverage result to pass. Failures and low coverage are reportable outcomes.

### Milestone 6 - documentation, commits and handoff

Document:

- Exact launch and verification commands.
- Coverage definitions and freshness leases.
- Recorded/current-software distinction.
- Approximate alignment and legacy validity.
- Advanced calibration prerequisites and incompatibilities.
- Pause/restart/speed behavior.
- Evidence locations and troubleshooting.
- Physical-validation limitations and fallback.

Append `HISTORY.md`, then rewrite `HANDOFF.md` in the new branch with verified facts. Preserve the other branches' documents and planning artifacts.

Commit and push reviewed milestones separately on `codex/replay-masimo-compare`. Stage only explicit relevant paths after inspecting the diff. Never blanket-stage, reset unrelated work, force-push, or merge into the demo branch. Verify each pushed remote SHA.

### Planned launch commands

Baseline, from the new replay checkout:

```powershell
C:/ProgramData/anaconda3/Scripts/conda.exe run --no-capture-output -n radar-vitals python scripts/replay_demo_compare.py `
  --capture 20260728_224902_live_demo_massimo3 `
  --development-data-root "C:/Users/josemsosag/Desktop/vitals_radar_3" `
  --config scripts/replay_demo_compare_config.yaml `
  --mode baseline `
  --speed 1
```

Headless smoke example:

```powershell
C:/ProgramData/anaconda3/Scripts/conda.exe run --no-capture-output -n radar-vitals python scripts/replay_demo_compare.py `
  --capture 20260728_224902_live_demo_massimo3 `
  --development-data-root "C:/Users/josemsosag/Desktop/vitals_radar_3" `
  --mode baseline --headless --speed 4 --duration-s 40
```

Advanced mode, **only after compatible accepted physical calibration exists**:

```powershell
C:/ProgramData/anaconda3/Scripts/conda.exe run --no-capture-output -n radar-vitals python scripts/replay_demo_compare.py `
  --capture 20260728_224902_live_demo_massimo3 `
  --development-data-root "C:/Users/josemsosag/Desktop/vitals_radar_3" `
  --mode motion `
  --motion-config results/replay_compare_calibration/motion_bias0.yaml `
  --allow-legacy-validity-assumption `
  --speed 1
```

Verifier:

```powershell
C:/ProgramData/anaconda3/Scripts/conda.exe run --no-capture-output -n radar-vitals python scripts/verify_replay_demo_artifacts.py `
  --run-dir "<actual pass directory>" `
  --development-data-root "C:/Users/josemsosag/Desktop/vitals_radar_3"
```

Returning to the working hardware demo requires stopping replay and launching from the original checkout:

```powershell
C:/ProgramData/anaconda3/Scripts/conda.exe run -n radar-vitals python scripts/live_demo.py --config scripts/live_demo_calibrated_config.yaml --duration-s 300
```

No reset, revert, deletion or merge is required.

### Review record and remaining physical work

Planning used workflow, implementation-architecture, test, research and independent plan-review agents. Independent verdict: **READY WITH MINOR CHANGES**; the requested pause, duration, integrity and unknown-counter clarifications are incorporated above.

Resolved disagreements:

- Application-based coverage leases were chosen over measurement-window-end leases because the requested metric is accepted display availability.
- Advanced capability will be implemented now but remain calibration-gated, rather than being omitted until calibration.
- The production decoder adapter preserves existing calibration fingerprints instead of refactoring live decoding.
- Historical validity is an explicit owner-authorized assumption, never manufactured proof.
- Baseline median admission excludes rejected/unverified HR despite the legacy finite-value append behavior.

Physical training, held-out calibration, real-hardware responsiveness and rehearsal remain owner-run work. Calibration for a nonzero-bias live configuration does not automatically qualify historical zero-bias `massimo3`.

The delivered baseline player can be accepted independently. The advanced replay capability remains clearly unavailable until its compatible calibration gate passes. Neither synthetic tests nor recorded-session replay establish clinical validity or physical accuracy at 3 bpm.
