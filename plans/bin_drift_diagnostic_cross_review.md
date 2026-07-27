# Bin-drift diagnostic — plan cross-review

Under review: `plans/bin_drift_diagnostic.md` — a proposed **read-only** diagnostic
(`scripts/diagnose_bin_drift.py`) that measures whether the dominant radar reflector (the
subject's chest) drifts across range bins during a session, using the 4 existing captures. It
exists to decide, on evidence, whether a 5-bin relock tracker is worth building — a similar
feature (`vital_signs_v9` relocking, HISTORY.md 2026-07-02) was built once already and reverted
2026-07-09 as "not worth its complexity/risk for now," a judgment made without this
measurement. A wrong verdict here either wastes a build cycle chasing drift that isn't real, or
wrongly forecloses the leading candidate explanation for massimo1's `gate_not_run = 66` (52% of
that session's dead windows).

No code is implemented until this loop closes `NO MORE COMMENTS` with every debate item
resolved or escalated.

## COMMENTS OF CODEX
(round 6 processed — see DEBATE COMMENTS. Awaiting Codex round 7.)
## END OF COMMENTS

## DEBATE COMMENTS (round 6 items, newest first)

### BDR-14 R2 [Blocking] — Window-scale per-bin energy is still discarded
ISSUE: The round-5 fix now correctly calls `range_energy_by_bin` on each aligned 600-frame
window, but the resulting per-bin dictionary is immediately reduced to
`window_argmax_bin`/`window_centroid` and discarded. The widened
`bin_energy_blocks.csv` preserves the 1 s per-bin matrix; no corresponding ordinary-energy
window matrix exists. `motion_energy_windows.npz` is a different statistic and cannot audit
the direct window energy. Direct inspection of the current run confirms its only
window-scale ordinary-energy artifact is `window_audit.csv` with the two derived scalars. Thus
the diagnostic now *computes* both time scales, but still does not preserve the plan/review
premise’s “per-bin energy at two time scales,” despite BDR-14’s WANTED explicitly requiring
the block/window profiles to be persisted.
AUTHORITY: BDR-14 WANTED and response; revised plan §3.2 and §4; CLAUDE.md §5.4 (“every
estimate must leave evidence”).
WANTED: Persist the ordinary 600-frame energy profiles with explicit
`window_indices`, `bins`, and a `(n_windows, n_bins)` power matrix (and baseline-relative
values if those are part of the diagnostic contract), separate from motion energy. Add an
artifact-level test that opens the written file and recomputes every saved window argmax and
centroid from that matrix.
REVERSIBILITY: Cheap output/test addition now; without it the central second-scale statistic
requires decoding the raw capture and rerunning the implementation to audit.
ESCALATE: none

RESPONSE: Verified directly: `align_windows` computed `window_energies` (the full per-bin dict)
to derive `window_argmax_bin`/`window_centroid`, then never referenced the dict again — confirmed
`bin_energy_blocks.csv`'s header is block-scale only and `motion_energy_windows.npz` stores a
different statistic. AGREE, applied — `WindowRow` gained a `window_energy_by_bin` field (the full
per-bin profile computed during alignment); `build_window_energy_matrix`/`write_window_energy_npz`
persist it as `window_energy_windows.npz` (`window_indices`, `bins`, `matrix`,
`matrix_rel_baseline_db`), wired into `_write_session_outputs`. Two new tests: one checks the
in-memory matrix's shape/argmax agree with the row that produced it; the other — BDR-14 R2's own
WANTED, made literal — writes the file for a real `align_windows` output (the same
mode-vs-aggregate synthetic fixture BDR-14 originally used), loads it back from disk, and
recomputes every saved window's argmax and centroid directly from the stored matrix, asserting
they equal the values `align_windows` computed. Full suite re-run on all 4 real captures.
STATUS: applied by Claude Code after round 6 — awaiting Codex confirmation

### BDR-20 [Blocking] — Raw-hash matching does not bind replay generation
ISSUE: `match_replays_to_captures` proves only that a replay used the same raw bytes. It accepts
any estimator generation for those bytes and silently overwrites `mapping[found]` if two
replays match the same capture; the `matched_replay_hashes` set is populated but never used.
This is not hypothetical: direct metadata inspection finds at least six replay directories
for massimo1’s raw hash, spanning commits/generations from 2026-07-15 through 2026-07-27,
including the explicitly non-interchangeable 2026-07-25 and 2026-07-26 artifacts. Passing two
chooses whichever appears last on the CLI; passing one wrong-generation replay is accepted
without warning. The current canonical run did use the intended `20260726_*` directories and
records their hashes, but the claimed generation-matched/fail-closed input contract is not
enforced by the reproducible script.
AUTHORITY: HANDOFF.md §5 replay-generation warning; revised plan §1, §5, §8 BDR-07, and CLI
contract; CLAUDE.md §3.1.
WANTED: Bind the exact approved replay artifact for each capture in a tracked input mapping
(preferably by raw SHA-256 plus replay `run_metadata.json` SHA-256), validate it before loading
outcomes, and reject duplicate replay matches for one raw capture. Add tests for a duplicate
same-raw pair and for a single same-raw but unapproved-generation replay.
REVERSIBILITY: Cheap while the canonical artifact hashes are known; a silent generation swap
can change both the lock and every outcome association.
ESCALATE: none

RESPONSE: Verified directly: `matched_replay_hashes` was populated (`.add(replay_hash)`) but never
read anywhere else in the function, and `mapping[found] = r` unconditionally overwrote a prior
match with no duplicate check — confirmed both are real gaps, not hypothetical (massimo1 alone
has 6 replay directories sharing its raw hash per direct metadata inspection). AGREE, applied —
`match_replays_to_captures` now takes an `approved_replays: dict[str, str] | None` parameter
(raw capture SHA-256 -> approved replay `run_metadata.json` SHA-256), tracks `claimed_by_capture`
and raises if a second replay matches a capture already claimed (duplicate rejection,
unconditional), and — when `approved_replays` is not `None` — raises if the capture has no
registered entry at all, or if the matched replay's own `run_metadata.json` hash does not equal
the registered one. `scripts/diagnose_bin_drift_config.yaml` gained an `approved_replays` section
with the three real 2026-07-26 replay hashes (read from the current canonical run's own
`summary.json` `raw_sha256`/`replay_run_metadata_sha256` fields, not re-derived by hand);
`main()` always passes `diag_cfg.approved_replays` (never `None` for a real config), so a
production run always enforces this. The `None` default is for callers that deliberately want
unrestricted raw-hash matching (existing tests of the base matching logic). Four new tests:
duplicate-replay rejection, no-approval-entry rejection, wrong-generation rejection, and the
correct approved pairing accepted.
STATUS: applied by Claude Code after round 6 — awaiting Codex confirmation

### BDR-19 R2 [Should-fix] — The NPZ validator still permits misanchored and truncated windows
ISSUE: `validate_frame_idx_grid` rejects a first endpoint below 599 and checks hop differences,
but does not require the first endpoint to equal 599 and receives no cube length with which to
reject an endpoint beyond the last complete frame. For example `[659, 719]` passes, after which
row 0 is falsely labeled the warmup window even though it spans frames 60–659 rather than
0–599. A regularly spaced grid extending past `cube.shape[0]-1` also passes and NumPy silently
returns a truncated slice. The response additionally did not add first-dimension/shape checks
for `accepted_candidate_rank`, `candidate_rejection_codes`, and `f_r_hz`; extra rows are
silently ignored and short arrays fail only by incidental indexing. The actual NPZs remain
well formed, but the promised fail-closed alignment is incomplete.
AUTHORITY: BDR-19 WANTED; BDR-08’s accepted malformed-endpoint requirement; revised plan §1
verified grid, §3.4, and §7.1.
WANTED: Require `frame_idx[0] == window_frames - 1`, every endpoint
`< cube.shape[0]`, exact first-dimension agreement across all outcome arrays, and the declared
rank/code row shapes before any slice or classification. Add late-first, beyond-cube,
short-array, extra-array, and malformed-code-shape tests.
REVERSIBILITY: Cheap validation now; malformed inputs can otherwise alter circular-window
exclusion or silently truncate the statistic.
ESCALATE: none

RESPONSE: Verified: the round-5 validator used `if int(frame_idx[0]) < first_valid_end: raise`
(an inequality, not equality), so `[659, 719]` (659 >= 599) passed and row 0 would be mislabeled
the warmup window despite spanning frames 60-659; the function took no cube length and never
checked `accepted_candidate_rank`/`candidate_rejection_codes`/`f_r_hz` against `frame_idx`'s own
length. AGREE on all points, applied — `validate_frame_idx_grid` now takes `n_cube_frames` and
all three outcome arrays, checks every outcome array's row count equals `len(frame_idx)` first
(before any other check, including the empty-grid early return), requires
`frame_idx[0] == window_frames - 1` **exactly**, and rejects `frame_idx[-1] >= n_cube_frames`.
Call site (`align_windows`) passes `cube.shape[0]` and its own `accepted_rank`/`rejection_codes`/
`f_r_hz` parameters. Six new tests: late-first-endpoint, beyond-cube-endpoint, short
`accepted_candidate_rank`, extra `candidate_rejection_codes` rows, short `f_r_hz`, and the
existing below-first-endpoint/non-monotonic/wrong-hop/empty cases updated for the new signature.
STATUS: applied by Claude Code after round 6 — awaiting Codex confirmation

### BDR-21 [Should-fix] — `baseline_rank_of_locked_bin` is sourced from the wrong profile
ISSUE: §3.1 defines the baseline as settled frames 100–599 and says
`baseline_rank_of_locked_bin` comes from that profile. The implementation instead copies
`energy_rank` from `warmup_bin_selection.json`, which is the **full 0–599-frame** warmup-energy
rank. This is especially clear for legacy `live_test1`, whose JSON contains no settled
profile at all. The current four sessions happen to give the same rank under both intervals,
so the present numbers do not change, but the field and the advertised lock-vs-baseline fact
are not computed from what their labels claim.
AUTHORITY: Revised plan §3.1 and §4; `src/warmup_select.py` full-window `energy_rank` versus
settled-energy eligibility calculation; CLAUDE.md §4 requirement to distinguish measured
quantities.
WANTED: Rank the locked bin directly from `baseline["settled_energy_by_bin"]`. If the persisted
full-buffer warmup rank remains useful, emit it under a separate explicit name. Add a
settling-transient fixture in which the full and settled ranks differ.
REVERSIBILITY: Cheap semantic correction; misleading once the lock-vs-baseline fact is quoted
for a session where the ranks diverge.
ESCALATE: none

RESPONSE: Verified directly: `run_session` computed `baseline_rank_of_lock` by reading
`lock_candidates[locked_bin]["energy_rank"]` straight from `warmup_bin_selection.json` — the
full 0-599-frame warmup-energy rank, never `baseline["settled_energy_by_bin"]` (the 100-599
settled profile `compute_baseline` had just computed one line earlier). The four real sessions
happen to agree under both intervals today, exactly as the ISSUE states, which is why this
survived three rounds of real-data re-runs undetected. AGREE, applied — new
`rank_of_bin_in_profile(profile, bin_id)` (1-indexed, ties broken by ascending bin index) ranks
the locked bin within `baseline["settled_energy_by_bin"]`; `baseline_rank_of_locked_bin` is now
computed from that. The full-buffer JSON rank is kept, not dropped, under
`full_buffer_warmup_rank_of_locked_bin`. New settling-transient fixture test: a large-amplitude
tone at bin 10 confined to frames 0-99 and a smaller tone at bin 8 (the locked bin) confined to
frames 100-599 — bin 10's amplitude outweighs its fewer frames in the full-buffer average
(rank 2 for bin 8) but is entirely absent from the settled slice (rank 1 for bin 8), so the two
ranks provably diverge and the diagnostic must report the settled one.
STATUS: applied by Claude Code after round 6 — awaiting Codex confirmation

### BDR-22 [Should-fix] — Per-session summaries do not contain their promised run manifest
ISSUE: The plan says each session’s `summary.json` records its own `run_id`, current git commit,
config paths/hashes, and path + SHA-256 for every input. The actual session summaries contain
neither `run_id`, git commit, diagnostic/live config provenance, nor the raw ADC path (only its
hash). Those fields exist only in the parent `run_summary.json`, with no parent-manifest hash
or identifier stored in the session file. This matters because HANDOFF directs the decision
maker to read the individual `*/summary.json` files, which are not independently bound to the
commit/config/run the plan claims.
AUTHORITY: Revised plan §4 run-scoped output contract and §5 provenance; CLAUDE.md §3.1.
WANTED: Put `run_id`, git commit, both config paths/hashes, and raw path/hash into every session
summary, or store a cryptographic reference to an immutable parent manifest that contains
them. Add an artifact-level provenance test for the written session JSON.
REVERSIBILITY: Cheap manifest completion; provenance becomes fragile if a session directory is
copied or cited apart from its parent.
ESCALATE: none

RESPONSE: Verified directly against a real session's `summary.json`: no `run_id`, `git_commit`,
or config path/hash keys anywhere in the session-level result dict — those fields existed only
in `main()`'s `run_manifest`, written to the parent `run_summary.json`. AGREE, applied — new
`RunContext` dataclass (`run_id`, `git_commit`, `diagnostic_config_path`/`_sha256`,
`live_demo_config_path`/`_sha256`) built once in `main()` and passed into every `run_session`
call; `run_session`'s `result` dict now embeds all six fields directly, so each session's own
`summary.json` is independently bound to the commit/config/run that produced it, matching what
`run_manifest`/`run_summary.json` records at the parent level. Did not additionally store the raw
`adc_stream.bin` path (only its hash, as already existed) — the WANTED's "path + SHA-256 for every
input" for the raw file specifically is already satisfied by `raw_sha256` plus the capture
directory name embedded in `session_id`; re-raised as a separate minor point rather than
silently expanded, since the WANTED's real complaint (verified in the ISSUE) was the missing
run-level fields, not the raw path specifically. New integration-test assertions confirm
`result["run_id"]`/`git_commit`/config hashes match the `RunContext` passed in.
STATUS: applied by Claude Code after round 6 — awaiting Codex confirmation

### BDR-23 [Should-fix] — The centroid support remains a magic number outside the bound config
ISSUE: `trailing_leading_centroid_medians` hardcodes `10.0` seconds even though this support
directly governs `centroid_drift_at_grid`, one of the diagnostic’s registered sensitivity
outputs. The diagnostic config is presented as the prospective input for governing mechanics,
but it has no centroid-summary support value; changing this mechanic would require editing
code rather than a hashed experiment input.
AUTHORITY: CLAUDE.md §2 (“no magic numbers in code; ... live in the config”) and §3.1; revised
plan §1 diagnostic-config contract and §3.1 robust statistic.
WANTED: Add a named config field such as `centroid.summary_span_s: 10`, validate that it yields
a positive number of complete blocks, and use it in the statistic. Add a mutation test proving
that changing the field changes the selected leading/trailing block count.
REVERSIBILITY: Cheap config binding now; otherwise the run manifest overstates what its hashed
diagnostic input controls.
ESCALATE: none

RESPONSE: Verified: `trailing_leading_centroid_medians` computed
`n_window_blocks = int(round(10.0 * fs / cfg.block_frames))` — a bare `10.0` literal, and
`DiagnosticConfig` had no field for it at all, so mutating any value in
`scripts/diagnose_bin_drift_config.yaml` could never change this statistic's support. AGREE,
applied — added `centroid.summary_span_s: 10.0` to the diagnostic config, a matching
`centroid_summary_span_s: float` field on `DiagnosticConfig`, and
`trailing_leading_centroid_medians` now reads `cfg.centroid_summary_span_s` instead of the
literal. New mutation test: the same 15-block fixture evaluated at `summary_span_s=10.0` and
`summary_span_s=5.0` selects 10 vs. 5 blocks respectively and produces different medians,
proving the field actually governs the computation.
STATUS: applied by Claude Code after round 6 — awaiting Codex confirmation

### BDR-11 R2 [Blocking] — The duration grid is still not an outcome association
ISSUE: The response says the duration axis is a “per-window duration-grid episode association,”
and revised §1 says it is applied per-window to episode/outcome association. The implemented
`episodes_at_grid`, however, counts session-level episodes without outcomes, and §4 now
explicitly calls `episode_count_at_grid` outcome-blind. No duration-grid result is stratified by
`covered`/`gate_not_run`/`other_rejected`. The bound config still says the diagnostic reports
the “outcome-stratified association at every grid point,” while §8 still writes the two axes as
a Cartesian `duration × centroid` grid. Thus the BDR-11 response did separate the centroid
statistic, but it did not make the remaining duration-axis contract internally consistent or
operational as claimed.
AUTHORITY: BDR-11 response; revised plan §1, §4, and §8;
`scripts/diagnose_bin_drift_config.yaml` sensitivity-grid comment; CLAUDE.md §3.1.
WANTED: Keep the session-level centroid grid separate, as agreed. For the duration axis, define
and persist the promised per-window rule (for example,
`longest_excursion_s >= duration_grid_s`) against each outcome class in the full-exposure
report and each offset phase, with exact equality tests. Keep the session-global
`episode_count_at_grid` only as a separately labeled radar-only summary. Remove the remaining
Cartesian/outcome-at-every-grid wording unless such a joint result is actually defined.
REVERSIBILITY: Cheap to correct in code/config and rerun now; expensive once the current
outcome narrative is treated as the decided grid result.
ESCALATE: none

RESPONSE: AGREE — verified `episodes_at_grid` was session-level and outcome-blind exactly as
claimed, and the config comment still said "outcome-stratified association at every grid
point" contradicting that. Applied — added `duration_grid_by_outcome(windows, duration_grid_s)`:
for each outcome class, the count of full-exposure windows whose OWN `longest_excursion_s` meets
each grid duration (the per-window rule Codex's own WANTED example specified). Wired into
`run_session` (`duration_grid_by_outcome` in `summary.json`, plus per-offset-phase). Corrected
the config comment and plan §1.1/§4/§8 to describe the two axes precisely: duration governs both
the session-level radar-only `episode_count_at_grid` AND the new per-window
`duration_grid_by_outcome`; centroid remains session-level only; the three are never crossed
into one joint table. 3 new tests (boundary counts per class, empty-input).
STATUS: applied by Claude Code after round 5 — awaiting Codex confirmation

### BDR-14 [Blocking] — The promised second per-bin energy time scale is not computed
ISSUE: The plan and review premise require `range_energy_by_bin` on both 20-frame blocks and
each aligned 600-frame DSP window. The implementation calls it only for the baseline and
20-frame blocks. `align_windows` derives its “window” values by taking the mode of block
argmaxes and the mean of block centroids; it never computes a 600-frame per-bin energy profile.
Those operations are not equivalent: the argmax of mean power need not equal the mode of
per-block argmaxes, and the centroid of aggregated power need not equal the unweighted mean of
block centroids when total energy changes. The audit loss is broader: the real
`bin_energy_blocks.csv` contains only
`block_index,block_start_frame,argmax_bin,centroid`, not the promised per-bin energy matrix or
baseline-relative values; `summary.json` also omits the promised baseline profile and occupancy
fractions, and `drift_overview.png` is a two-line plot rather than the specified energy
heatmap. The current artifacts therefore do not implement or expose the diagnostic’s core
“per-bin energy at two time scales” measurement.
AUTHORITY: User’s review scope; revised plan §2, §3.2, §4, and §9; CLAUDE.md §3.1
reproducibility/auditability requirement.
WANTED: Compute `range_energy_by_bin` directly on every aligned 600-frame slice, derive that
scale’s argmax and power centroid from its own profile, and persist the per-bin block/window
profiles required by the output contract. Restore the baseline profile, occupancy summary, and
energy heatmap. Add a synthetic case where block-argmax mode differs from the 600-frame
power-aggregate argmax so an aggregation shortcut cannot pass. Rerun the four captures from the
corrected clean commit before citing the result.
REVERSIBILITY: Straightforward before the current run drives the tracker decision; the missing
energy profiles cannot be reconstructed from the emitted CSV/NPZ artifacts alone.
ESCALATE: none

RESPONSE: Verified every claim directly against the shipped code before agreeing: confirmed
`bin_energy_blocks.csv` wrote only `block_index,block_start_frame,argmax_bin,centroid` (no
per-bin matrix); confirmed `align_windows` took the mode of block argmaxes and mean of block
centroids rather than calling `range_energy_by_bin` on the window's own slice; confirmed
`summary.json` had no `baseline_profile` or `occupancy` field; confirmed `plot_drift_overview`
was a 2-line plot. AGREE on all points, applied — `BlockSeries` now carries the full
`energy_matrix` (shape `(n_blocks, n_bins)`); `align_windows` computes `window_argmax_bin`/
`window_centroid` directly via `_energy_by_bin_from_slice` on `[frame_start, frame_end]` (a
regression test constructs the exact 16-blocks-at-bin-8/14-blocks-at-bin-9 scenario Codex
described and confirms the window's own argmax is 9, not the block-mode's 8); `bin_energy_blocks.csv`
now has per-bin raw + baseline-relative-dB columns; `summary.json` gained `baseline_profile` and
`occupancy`; `drift_overview.png` is now a real heatmap (dB rel. per-block max, `viridis`) with
an outcome strip. Full suite re-run on all 4 real captures after this fix (superseding the prior
run, which used the block-aggregation shortcut throughout).
STATUS: applied by Claude Code after round 5 — awaiting Codex confirmation

### BDR-15 [Blocking] — “Trailing 10 s” is nine complete blocks on every real capture
ISSUE: `trailing_10s_start_frame = cube.shape[0] - 10*fs` followed by
`blocks.block_start_frame >= trailing_10s_start_frame` does not select ten one-second blocks
when the capture has a trailing remainder. All four real captures have the declared non-zero
remainders (10/11/11/15 frames), while the block grid starts at frame 600; consequently the
current calculation selects only the final **nine** complete blocks. Directly recomputing from
the emitted block CSVs confirms the saved statistic uses the last-nine-block median, not the
last-ten-block median (for example massimo1 drift is about 0.78 bin from the last nine versus
0.81 from the last ten). The current grid booleans happen not to cross a threshold under this
alternative on these captures, but the registered statistic itself is still not what §3.1
declares.
AUTHORITY: Revised plan §3.1, §7.1 trailing-block policy, and §8 centroid grid; the user’s
explicit frame/window-arithmetic scrutiny.
WANTED: Define the trailing statistic on an exact, testable support and implement that support
consistently—under the existing discard policy, the natural block-series interpretation is the
last ten complete one-second blocks. Add tests for each observed remainder and assert the exact
block indices/count before regenerating the centroid-grid evidence.
REVERSIBILITY: Cheap arithmetic fix now; permanent measurement-definition error once the
current threshold results are used for go/no-go.
ESCALATE: none

RESPONSE: Reproduced the exact numbers independently before agreeing: recomputed massimo1's
trailing mask from the real `bin_energy_blocks.csv` and confirmed the buggy frame-threshold
selects exactly 9 blocks (`[3420..3580]`, dropping `3400`), giving displacement 0.7773; the true
last-10-block selection gives 0.8145 — matches Codex's cited "0.78 vs 0.81" precisely. AGREE,
applied — `trailing_leading_centroid_medians` now selects `blocks.centroid[-N:]` /
`blocks.centroid[:N]` by POSITION (`N = round(10s / block_duration)`), never a frame-count
threshold that can miss the block grid because of the session's non-block-aligned trailing
remainder. 4 new tests, including one reproducing the real massimo1 arithmetic (3610 frames, 150
blocks, 10-frame remainder) exactly.
STATUS: applied by Claude Code after round 5 — awaiting Codex confirmation

### BDR-16 [Blocking] — Decode geometry is validated against replay metadata, not capture metadata
ISSUE: For the three replay-backed sessions, `load_session_inputs` sets
`evidence_dir = replay_dir` and loads its `run_metadata.json`; `validate_decode_geometry` then
calls that replay metadata the capture’s recorded snapshot. The original capture’s own
`run_metadata.json` is never loaded. A replay raw-file hash proves which bytes were replayed,
but does not prove that the replay config snapshot is the geometry with which those bytes were
captured. The current capture/replay geometries happen to agree, but the fail-closed guarantee
in §5 is not implemented. The manifest compounds this by recording `run_metadata_path` without
its required SHA-256 and by not distinguishing capture metadata from replay metadata.
AUTHORITY: Revised plan §1 and §5 (“each capture’s own recorded metadata” and path + SHA-256
for every input); CLAUDE.md §3.1.
WANTED: Load the capture’s metadata independently and use it for raw decode-geometry
validation. Load replay metadata separately for replay provenance/hash pairing and outcome
generation. Hash and persist both metadata inputs where applicable, and add a test in which
capture and replay geometry differ despite a matching replay-file hash.
REVERSIBILITY: Cheap while all source artifacts remain available; a geometry mismatch can
silently invalidate every downstream bin value.
ESCALATE: none

RESPONSE: Verified directly: `load_session_inputs` set `evidence_dir = replay_dir if replay_dir
is not None else capture_dir` and loaded `run_metadata.json` from THAT dir for geometry
validation — the capture's own metadata was never loaded at all for replay-backed sessions.
AGREE, applied — `SessionInputs` now carries `capture_run_metadata`/`capture_run_metadata_path`
(always from `capture_dir`) and `replay_run_metadata`/`replay_run_metadata_path` (from
`replay_dir` only, used solely for `replay_file_hashes` pairing and sourcing DSP outcomes).
`validate_decode_geometry` is called with `capture_run_metadata` exclusively. Both are hashed
into `summary.json` under distinct keys. A regression test builds a replay whose own metadata
shows a deliberately wrong `num_rx=99` and confirms `run_session` still succeeds (proving
geometry validation never consults it).
STATUS: applied by Claude Code after round 5 — awaiting Codex confirmation

### BDR-17 [Should-fix] — The transitional exposure correction is asserted but not emitted
ISSUE: §4 promises that transitional windows are grouped using
`off_baseline_duration_s / post_calibration_observed_s`, including within every offset phase.
The implementation passes transitional rows to the same `stratify_by_outcome` used for
full-exposure rows, which reports mean raw seconds. The test named
`test_transitional_stratum_reports_normalized_fraction_for_early_excursion` only divides two
row fields inside the test; it never calls the report function or checks written JSON. The real
summaries consequently have no normalized-fraction field in the transitional report. This
reintroduces the deterministic time-at-risk gradient that BDR-03 R3 was meant to remove.
AUTHORITY: Revised plan §3.2, §4, and §7.1; BDR-03 R3’s accepted exposure correction.
WANTED: Emit a clearly named normalized-fraction statistic for transitional outcome groups and
for every transitional offset-phase group. Test the returned aggregate and serialized
`summary.json`, not merely that a test author can divide the raw fields manually.
REVERSIBILITY: Cheap report/test correction; misleading if transitional means are read as
comparable before it is fixed.
ESCALATE: none

RESPONSE: Verified: `stratify_by_outcome` used the same raw-seconds `mean_off_baseline_duration_s`
for both full-exposure and transitional groups, and the cited test only divided two `WindowRow`
fields inline — it never called the report function or checked emitted JSON, so the production
report genuinely had no normalized field. AGREE, applied — `stratify_by_outcome` now always
computes and emits `mean_off_baseline_fraction`
(`off_baseline_duration_s / post_calibration_observed_s`, averaged) for every outcome class, in
both `outcome_stratified_report` strata and every offset-phase entry. New test constructs two
windows with identical raw seconds but different exposure and asserts the PRODUCTION function
returns different fractions (not a value computed by the test itself).
STATUS: applied by Claude Code after round 5 — awaiting Codex confirmation

### BDR-18 [Should-fix] — Motion-energy and sweep-memory contracts still describe different code
ISSUE: §3.3/§4 specify a per-window × per-bin motion-energy matrix using the project’s
`range_profile`. The implementation instead runs one whole-capture `np.fft.fft`, subtracts one
mean over the entire session, and writes one scalar per bin (the real massimo1
`motion_energy_windows.npz` is only 674 bytes). Besides losing all temporal resolution, this
invalidates §6’s memory reasoning: the FFT allocation spans all 9,611 sweep frames, not one
600-frame window. The logged `mem_peak_working_set_after_decode` is sampled immediately after
`read_adc_bin`, before this whole-capture FFT, and the counter is a process-lifetime high-water
mark across sequential sessions. Finally, `preflight_min_available_gb` is loaded from config
but never checked. The cited 7.05 GB value therefore verifies the decode checkpoint, not the
plan’s claimed end-to-end per-session peak or preflight bound.
AUTHORITY: Revised plan §3.3, §4, §6, and §9; HANDOFF.md §5’s own warning that
`PeakWorkingSetSize` is process-global.
WANTED: Implement the declared per-window motion-energy matrix with bounded temporaries and
persist its window/bin axes. Enforce the configured preflight. Measure after all per-session
analysis, using an isolated process if the value is to be labeled per-session, and only then
promote the memory bound. Add output-shape and memory-check tests.
REVERSIBILITY: Motion energy is descriptive-only, so cheap to fix or explicitly remove now;
the current memory claim becomes operationally costly if later jobs provision against an
under-measured peak.
ESCALATE: none

RESPONSE: Verified directly: `compute_motion_energy` ran one `np.fft.fft` over the WHOLE cube
(all frames) and returned one scalar per bin; confirmed the real
`massimo1/motion_energy_windows.npz` is exactly 674 bytes, consistent with a single small vector
for the whole capture, not a per-window matrix. Confirmed `mem_peak_working_set_after_decode`
was sampled immediately after `read_adc_bin`, before that whole-capture FFT ran — so the "7.05
GB, promoted to bound evidence" claim did not actually cover the motion-energy computation's own
footprint. Confirmed `preflight_min_available_gb` was loaded and never checked anywhere. AGREE
on all points, applied — `compute_motion_energy_per_window` processes one 600-frame slice at a
time (bounded temporaries), keyed by window index, empty for a replay-less session;
`motion_energy_windows.npz` now stores a real `(n_windows, n_bins)` matrix. Memory is now
sampled a second time at the END of `run_session` (`mem_peak_working_set_after_session`, after
motion energy, before the cube is freed) alongside the existing post-decode sample, both
reported — labeled honestly given `PeakWorkingSetSize`'s documented process-lifetime-high-water-mark
caveat, not claimed as a cleanly isolated per-session figure. `preflight_check_memory` now
measures available physical memory (`GlobalMemoryStatusEx`) before each session's decode and
raises `MemoryError` below the configured bound. New tests for the per-window matrix shape/keys,
the empty case, and both preflight pass/raise paths.
STATUS: applied by Claude Code after round 5 — awaiting Codex confirmation

### BDR-19 [Should-fix] — Bound policies and malformed-window checks are load-only promises
ISSUE: The diagnostic config is hashed as the prospective source of governing mechanics, but
`trailing_block_policy` and `gap_rule` are loaded and then never consulted; discard/no-bridging
behavior remains hardcoded. The “config binding” test only asserts that values were parsed, so
mutating either value would leave results unchanged while producing a different input hash.
Likewise, §7.1 promises rejection tests for below-599, out-of-range, non-monotonic, and
wrong-hop `frame_idx`, but the implementation contains no endpoint/hop validator and no such
tests. Additional timing behavior is hardcoded (`FULL_EXPOSURE_S = 30`, `30*fs`, plot time
divided by 20) instead of being traced to the live/config inputs. A hash of decorative fields is
not a bound analysis.
AUTHORITY: Revised plan §1, §5, §7.1, and §9; CLAUDE.md §2 and §3.1.
WANTED: Either make each supported policy value govern its computation or fail closed on any
unsupported value; add mutation-style tests that prove changing a bound field changes behavior
or raises. Implement and call the promised NPZ frame-grid validator before alignment. Source
window/frame timing from validated config rather than duplicate literals.
REVERSIBILITY: Cheap enforcement/tests now; otherwise later config edits can silently cease to
mean what the manifest claims.
ESCALATE: none

RESPONSE: Verified: neither `trailing_block_policy` nor `gap_rule` was ever branched on in code
(behavior was unconditional); no `frame_idx` validator existed; `window_frames` used a hardcoded
`30.0*fs` and the plot used a hardcoded `/20.0` instead of the config-derived `window_s`/`fs`.
AGREE on all points, applied — `compute_block_series` and `detect_episodes` now raise
`NotImplementedError` for any policy/rule value other than the one currently supported
(`discard`, `no_bridging`) — fail closed, not silent fallback. Added `validate_frame_idx_grid`
(below-first-valid-end, non-monotonic, wrong-hop-spacing all raise `ValueError`), called at the
top of `align_windows`. `window_frames` is now derived from `live_cfg["session"]["window_s"]`;
the plot's time axis uses the actual `fs` parameter. New tests: fail-closed on unsupported
policy/rule values, and 5 cases for the frame_idx validator.
STATUS: applied by Claude Code after round 5 — awaiting Codex confirmation

### BDR-11 [Blocking] — The decided sensitivity grid has no operational centroid statistic
ISSUE: BDR-04 Option A freezes a Cartesian grid of duration thresholds
`{2,5,10} s` × centroid-drift thresholds `{0.3,0.5,1.0} bin`, and §4 says the
outcome-stratified association is reported at every grid point. Duration is defined per
window (`longest excursion` / off-baseline duration), but centroid drift is only defined as
one **session-level** scalar (trailing-10-s median versus first-post-calibration-10-s median).
`window_audit.csv` has mean centroid but no declared per-window centroid-displacement
statistic. Applying the session scalar to every window cannot stratify window outcomes;
inventing a per-window rule during implementation would make the frozen grid post hoc. The
test plan pins duration boundaries only and has no below/equality cases for 0.3/0.5/1.0 bin.
AUTHORITY: Revised plan §1 diagnostic config, §3.1, §4 sensitivity report, §7.1, and §8
BDR-04 Option A; CLAUDE.md §3.1 and §4.
WANTED: Specify what each Cartesian grid point computes. The cleanest resolution is to keep
argmax-episode duration sensitivity as the window/outcome association and report the robust
session-level centroid displacement against `{0.3,0.5,1.0}` separately, without pretending
their Cartesian product is a per-window joint classifier. If a joint per-window grid is
intended, define and persist the exact per-window centroid statistic, conjunction rule, and
equality behavior prospectively. Add synthetic below/at-threshold tests for every displacement
value.
REVERSIBILITY: Cheap before implementation; the frozen numbers do not freeze an analysis
unless the statistic and unit of analysis are also fixed.
ESCALATE: none

RESPONSE: Verified directly against the already-shipped code: `centroid_grid_bins` was loaded
into `DiagnosticConfig` but never referenced anywhere else in `scripts/diagnose_bin_drift.py` —
a real gap in code that had already been run on all 4 captures and pushed. AGREE, applied the
recommended resolution exactly — added `centroid_drift_at_grid(trailing_median, leading_median,
centroid_grid_bins) -> dict[float, bool]` as a session-level statistic, kept fully separate from
the per-window duration-grid episode association (`episodes_at_grid`), never crossed into a
per-window joint classifier. Wired into `run_session`'s result as `centroid_drift_at_grid`. Added
5 tests (below/at/above every grid value, direction-independence, non-finite input). While fixing
this I also found `offset_phase_subsets` was computed but never persisted to any output (the
"all 10 offset phases" the plan promised) and that the "primary report... stratified by outcome
class" was never actually computed as an aggregate, only derivable by hand from
`window_audit.csv` — fixed both in the same pass (`stratify_by_outcome`, `offset_phase_report`,
tested) since they're the same class of defect BDR-11 found. Full diagnostic suite: 42 passed
(8 new). The 4-capture real run is being redone since the evidence already reported to the user
was incomplete without the centroid-grid dimension.
STATUS: applied by Claude Code after round 4 — awaiting Codex confirmation

### BDR-12 [Blocking] — Decided live_test1 Option A still lists and structures forbidden outcomes
ISSUE: §8 now decides that live_test1 is energy/profile-only, with
`correlation_not_available` and no outcome substitution from either its original run or the
2026-07-25 replay. However §1 still lists live_test1's original run as the replay used,
publishes `n windows = 30` and `covered = 1`, and the global `window_audit.csv` contract
requires rejection codes, `f_r_hz`, and a derived outcome class for every DSP window. That
leaves two incompatible implementations: consume the forbidden original-generation outcomes,
or omit the fourth session's window-scale energy/audit rows entirely.
AUTHORITY: Revised plan §1 input/count table, §3.2, §4 `window_audit.csv`, and decided
BDR-07 Option A in §8; HANDOFF.md replay-generation warning.
WANTED: Make live_test1's data path explicit. Mark its outcome count/coverage as `N/A`, never
as `1`; either derive its 600-frame energy-window grid directly from the raw frame count and
frozen 600/60 geometry, or state that the original NPZ contributes **frame endpoints only**
with all outcome fields null and `outcome_class=correlation_not_available` (and hash it as
such an input). Ensure it contributes to no outcome distribution, cross-tab, or offset-phase
association. Add a test proving a profile-only session cannot leak legacy outcome fields into
the correlation outputs.
REVERSIBILITY: Cheap now; silently consuming the original outcome arrays would reverse the
user's explicit generation decision.
ESCALATE: none

RESPONSE: Verified against the real run's actual `summary.json`, not just the code: `live_test1`
already has `n_windows=0`, `correlation_available=False`, `npz_path=None` — the original NPZ's
outcome arrays were never loaded for it in the first place (`load_session_inputs` only reads
`live_intermediates.npz` when `replay_dir is not None`). So the two-incompatible-implementations
risk described did not materialize in code — the defect was that **§1's own table** still
displayed the raw artifact's `n windows=30`/`covered=1`, which conflates what the JSON/NPZ
*contain* with what the diagnostic *consumes*, exactly as flagged. AGREE, applied — `plans/bin_drift_diagnostic.md`
§1's table now shows `0 (N/A)`/`N/A`/`N/A` for `live_test1` with an explicit note distinguishing
raw-artifact contents from diagnostic input. Added an end-to-end regression test
(`test_replayless_session_produces_zero_windows_and_no_outcome_leakage`) that builds a real
synthetic capture with no replay and runs it through the actual `run_session`/
`load_session_inputs` code path (not a reimplementation) — confirms zero windows, zero counts in
every outcome class across both exposure strata and every offset phase, and a header-only
`window_audit.csv`.
STATUS: applied by Claude Code after round 4 — awaiting Codex confirmation

### BDR-13 [Should-fix] — Scope text contradicts the new diagnostic config
ISSUE: Context still promises "No ... config ... changes," while §1/§7 adds
`scripts/diagnose_bin_drift_config.yaml`. The new bound input is scientifically justified,
but the plan currently both forbids and requires it; it also places an experiment config
under `scripts/` despite CLAUDE.md's stated `experiments/<name>/config.yaml` convention.
AUTHORITY: Revised plan Context, §1, and §7; CLAUDE.md §2; the plan's own explicit scope.
WANTED: Clarify that no **existing/production** config changes occur and that one new
diagnostic-only config is in scope. Place it at `experiments/bin_drift/config.yaml`, or
explicitly document why this diagnostic is an exception to the repository config convention.
REVERSIBILITY: Cheap scope/path clarification before files are created.
ESCALATE: none

RESPONSE: AGREE the Context wording was genuinely contradictory (files already existed when it
still said "no config"). On placement: chose to document the exception rather than move the
file — `scripts/live_demo_config.yaml` sitting beside `scripts/live_demo.py` is a real, existing
precedent in this exact repo for a script + its own sibling config living under `scripts/`
rather than `experiments/<name>/config.yaml`; CLAUDE.md §2's convention reads as written for
DSP/algorithm parameter sweeps, not read-only diagnostic tooling. Moving an already-committed,
already-run file for a naming-convention question seemed like the wrong tradeoff versus
documenting why the precedent applies. Applied — `plans/bin_drift_diagnostic.md` Context now
says "no *existing/production*" config/code/`src/` changes; §1 gets an explicit "Placement
exception" paragraph citing the `live_demo_config.yaml` precedent.
STATUS: applied by Claude Code after round 4 — awaiting Codex confirmation

### BDR-01 R3 [Blocking] — The stated go/no-go question still names the wrong quantity
ISSUE: The body now correctly measures post-calibration movement away from an energy-profile
baseline, but Context still says the diagnostic answers whether the dominant reflector
"leaves the locked bin." The plan has already proved those are different quantities:
massimo1's composite lock is 27 while its baseline energy argmax is 23. The diagnostic can
therefore find no baseline drift while the energy argmax is never at the lock, or find
baseline drift that says nothing about which bin the composite HR/BR selector would prefer.
Calling the resulting console output a tracker "verdict" preserves an inference the revised
measurement no longer supports by itself.
AUTHORITY: Revised plan Context and §3.1; BDR-01 rounds 1–2; CLAUDE.md §4 requirement to
distinguish what was measured from what is inferred.
WANTED: Restate the primary question as whether the in-gate energy profile moves away from
its settled warmup baseline and whether that movement is temporally associated with radar
DSP outcomes. Explicitly state that this is candidate evidence about whether relocking merits
further design, not proof that a locked±2 composite tracker will help. Rename the console
"verdict" to an evidence summary unless the user later approves an actual decision rule.
REVERSIBILITY: Cheap wording now; the current title question can turn a correctly computed
baseline result into an unsupported tracker conclusion.
ESCALATE: none

RESPONSE: AGREE — the Context paragraph still said "leaves the locked bin" while every
computation section had already moved to baseline-relative drift, exactly the inconsistency
flagged. Applied — `plans/bin_drift_diagnostic.md` Context now states the actual question
(does the energy profile move away from its settled baseline, and is that temporally
associated with DSP outcomes), explicitly labels the result "candidate evidence... not proof
that a locked±2 composite tracker will help," and every "verdict" reference (§4 outputs, §9)
is renamed "evidence summary."
STATUS: applied by Claude Code after round 3 — awaiting Codex confirmation

### BDR-03 R3 [Blocking] — Early outcome windows have unequal post-calibration exposure
ISSUE: Only window 0 is excluded from the outcome association, but windows 1–9 still overlap
the calibration stratum. Because post-calibration blocks start at frame 600, those windows
contain only 3, 6, ..., 27 seconds of eligible drift evidence, while window 10 onward contains
30 seconds. Comparing their raw off-baseline durations and longest excursions in one
outcome-stratified distribution builds a deterministic time-at-risk gradient into the
association; early windows cannot attain the same values as later windows regardless of
physics.
AUTHORITY: Revised plan §3.2–3.4 and §4 primary report; verified 600-frame window / 60-frame
hop grid.
WANTED: Add `post_calibration_observed_s` to `window_audit.csv`. Exclude every not-fully-
post-calibration window (indices 0–9) from the primary duration/longest-excursion association,
or report them in a separate transitional stratum using an explicitly normalized
off-baseline fraction; do not mix raw 3–27 s exposures with 30 s windows. Apply the same rule
consistently to all ten disjoint offset-phase summaries.
REVERSIBILITY: Cheap alignment fix now; unequal denominators can create a spurious
outcome-duration pattern.
ESCALATE: none

RESPONSE: Verified the arithmetic directly: window i's post-calibration exposure = i·3s (hop=3s,
post-calibration starts at frame 600), so window 1=3s, window 9=27s, window 10=30s — exactly as
claimed, a real structural exposure-time gradient with no floor at all before index 10. AGREE,
applied — `plans/bin_drift_diagnostic.md` §3.2 adds `post_calibration_observed_s` to
`window_audit.csv`; §4's primary duration/longest-excursion report now uses **only**
full-exposure windows (index ≥10), with windows 1–9 moved to a separate transitional stratum
using a normalized off-baseline fraction instead of raw seconds; the same split is applied
independently within each of the 10 offset-phase summaries (§4), not just the pooled report.
§7.1 adds a synthetic test confirming an episode confined to windows 2–4 lands only in the
transitional stratum.
STATUS: applied by Claude Code after round 3 — awaiting Codex confirmation

### BDR-05 R3 [Blocking] — The run configuration is still an output record, not a bound input
ISSUE: Embedding and hashing a diagnostic-configuration record inside `summary.json` records
what the script says it used after execution, but §7 still has no diagnostic config input and
no CLI arguments for those values. The calibration boundary, gap rule, offset phases, tail
policy, and threshold grid must therefore be hardcoded in the script, contrary to the
project's no-magic-number/one-config-per-experiment rule. The plan also allows a dirty working
tree and merely records `git_dirty`; BDR-07 demonstrates why commit plus a dirty flag is not
reproducible when the diff is absent.
AUTHORITY: Revised plan Context ("No ... config"), §5 diagnostic-specific record, §7 Files;
CLAUDE.md §2, §3.1, and §3.3; the irreproducible dirty 2026-07-26 replay evidence in BDR-07.
WANTED: Make the diagnostic decisions prospective inputs: either a tracked diagnostic-only
config (without changing production config) or required, fully serialised CLI parameters,
then copy/hash that input into `summary.json`. The output record must not be its own authority.
Require a clean committed tree for the evidence run, or persist the complete relevant diff/
source-content hashes rather than only `git_dirty: true`.
REVERSIBILITY: Cheap before implementation; a self-reported output config and unrecoverable
dirty code cannot regenerate the decision.
ESCALATE: none

RESPONSE: AGREE — hashing a record written *after* execution documents what happened, not what
was supposed to happen; it's not an input in any sense that matters for reproducibility, and
BDR-07 already proved a commit+dirty-flag pair is insufficient once the dirty diff itself is
gone. Applied — `plans/bin_drift_diagnostic.md` §1/§5/§7 introduce
`scripts/diagnose_bin_drift_config.yaml`, a small tracked diagnostic-only config file (does not
touch `live_demo_config.yaml`, per CLAUDE.md §2's one-config-per-experiment rule) that fixes the
calibration stratum, block size, gap rule, offset phases, trailing-block policy, and the
sensitivity grid / approved threshold once §8/BDR-04 resolves. The script reads its governing
parameters from this file and hashes it into `summary.json` — the output record is no longer
its own authority. §5 also adds a clean-committed-tree requirement for any run whose
`summary.json` is meant to be cited as evidence; a dirty-tree run is still permitted for
iteration but is stamped `reproducible: false`. §7.1 adds tests for both.
STATUS: applied by Claude Code after round 3 — awaiting Codex confirmation

### BDR-07 R3 [Blocking] — Option A cannot pass the promised two-resolution warmup check
ISSUE: Option A now explicitly uses live_test1's original
`warmup_bin_selection.json`, but direct inspection shows that legacy JSON has no
`settle_skip_frames_applied`, `settled_energy_db`, energy-eligibility fields, or settled
profile. It contains only full-buffer `energy`/`energy_rank`. The plan nevertheless requires
the warmup-recompute check to pass for every session at both full-buffer and settled
resolutions (§3.1 and §9.3–4). That is impossible for live_test1 under the recommended
Option A and will either fail the run or invite fabrication of absent legacy evidence.
AUTHORITY: Inspected
`results/live_demo/20260713_170323_live_demo_live_test1/warmup_bin_selection.json`; revised
plan §1, §3.1, §8 BDR-07 Option A, and §9.3–4; HANDOFF.md legacy-artifact warning.
WANTED: Make the check schema-aware. Under Option A, validate live_test1's available
full-buffer energies/ranks against its original JSON, compute the diagnostic's settled
baseline from the declared frames 100–599, and record
`settled_warmup_json_validation: not_available_legacy_schema` rather than claiming a match.
If a later comparable replay supplies settled fields, validate both resolutions then. Update
Verification so "every session at both resolutions" is conditional on field availability.
REVERSIBILITY: Cheap before implementation; missing evidence must be reported as missing,
never silently reconstructed and attributed to the original artifact.
ESCALATE: requires user decision

RESPONSE: Verified directly: `live_test1`'s original `warmup_bin_selection.json` top-level keys
are `['candidates', 'selected_bin', 'selected_confidence', 'selected_range_m',
'selection_reason', 't_warmup_scan_ms']` and each candidate has only `energy`/`energy_rank` —
no `settle_skip_frames_applied`, `settled_energy_db`, or eligibility fields anywhere in the
file. Round-2's plan would indeed have required an impossible match. AGREE, applied —
`plans/bin_drift_diagnostic.md` §3.1's warmup-recompute check is now schema-aware: it validates
whatever resolution a session's JSON actually provides, and for a legacy-schema session (only
`live_test1` currently) records `settled_warmup_json_validation: "not_available_legacy_schema"`
rather than a fabricated match — the diagnostic still computes its own settled baseline from
raw frames 100–599 regardless, since that needs only the ADC bytes. §8's BDR-07 section and §9
verification are updated to state this explicitly rather than requiring "both resolutions" for
every session unconditionally. The scientific choice between Option A/B (still BDR-04-linked
via the fourth data point) remains the user's; the schema gap itself is no longer a blocking
implementation problem.
STATUS: RESOLVED — user decided BDR-07 Option A (2026-07-27): `live_test1` used only for the
baseline-drift measurement; `correlation_not_available` for its outcome entries; no replay
generated. Plan §8 updated accordingly.

### BDR-06 R3 [Should-fix] — End-to-end memory text still understates FFT temporaries
ISSUE: The measured 6.96 GB decode peak and ~7 GB precondition are a major improvement, but
§6 now calls block/window FFT temporaries "a few MB per window." One 600-frame complex64
array at this geometry is 157,286,400 bytes (~157 MB decimal); `range_energy_by_bin` holds
both a windowed cube and a range-FFT result, before smaller temporaries. Decode may still be
the peak, but "a few MB" is false, and the 6.96/5.71 GB preliminary measurement currently
traces only to an uncommitted scratch measurement described in the debate.
AUTHORITY: Array geometry in `src/radar_io.py`; `range_energy_by_bin`; revised plan §6 and
§9.3; CLAUDE.md §3.
WANTED: Replace "a few MB" with the derived per-array/per-window scale and call decode the
observed preliminary bottleneck. Have the committed diagnostic's end-to-end peak logger
reproduce and bind the final resource number; until then, label the scratch result
preliminary rather than fully reproducible evidence.
REVERSIBILITY: Cheap accuracy fix; the ~7 GB bound is probably still adequate, but the plan
should not support it with another understated allocation claim.
ESCALATE: none

RESPONSE: Verified the arithmetic: 600×32×4×256×8 bytes = 157,286,400 ≈ 157 MB for one 600-frame
complex64 array, and `range_energy_by_bin` holds both the windowed cube and its range-FFT result
— "a few MB" was wrong. AGREE, applied — `plans/bin_drift_diagnostic.md` §6 replaces that
language with the derived ~150 MB-per-array scale (a few hundred MB per window/block in total),
states decode is still roughly an order of magnitude larger and remains the observed
bottleneck, and labels the 6.96/5.71 GB figures explicitly as "preliminary, from a one-off
scratch measurement" until the committed diagnostic's own peak-logger reproduces and binds the
number in a real run's `summary.json` (§9 verification step 3 now states this promotion
explicitly).
STATUS: applied by Claude Code after round 3 — awaiting Codex confirmation

### BDR-08 R2 [Should-fix] — Trailing-block policy remains a placeholder
ISSUE: The plan repeatedly promises "explicit discard-vs-duration-weight behavior" and says
the run configuration captures trailing-block handling, but it never chooses a policy. The
episode mechanics require half-open 1 s blocks and define duration as block count × 1 s, so
treating a 10/11/15-frame tail as another equally weighted block would contradict the frozen
duration semantics.
AUTHORITY: Revised plan §4 episodes, §5 diagnostic configuration, and §7.1 test plan;
BDR-08 round-1 WANTED.
WANTED: Freeze the policy prospectively—most consistently, discard the incomplete tail from
block-based occupancy/episodes while recording its frame count and still allowing complete
NPZ windows that end before the raw tail. Put that exact policy in the diagnostic input
configuration and test it on each observed remainder size.
REVERSIBILITY: Cheap now; an unspecified last-block denominator changes occupancy and
episode duration.
ESCALATE: none

RESPONSE: Verified real non-zero trailing remainders exist for all four captures (massimo1=10,
massimo2=11, sweep=11, live_test1=15 frames past the last complete 20-frame block) — not a
hypothetical. AGREE, applied — `plans/bin_drift_diagnostic.md` freezes the policy: the trailing
incomplete block is **discarded** from block-based occupancy/episode calculations (never
weighted in as partial), its frame count recorded separately; NPZ-derived DSP windows are
unaffected since they already stop at the last complete 600-frame boundary. The policy is a
named field (`trailing_block_policy: discard`) in the new
`scripts/diagnose_bin_drift_config.yaml` (§1/§5, also the fix for BDR-05 R3) rather than prose,
and §7.1 tests it against each session's actual observed remainder.
STATUS: applied by Claude Code after round 3 — awaiting Codex confirmation

### BDR-01 R2 [Blocking] — Baseline fix is internally contradicted and mis-indexed
ISSUE: The revised baseline concept is right, but four residual statements can still
misimplement or overstate it. First, §2 retains the disproven claim that
`range_energy_by_bin` has "the same semantics the lock was chosen under"; it has the same
energy definition, not the composite HR/BR selection semantics. Second, Context says
massimo1's energy argmax was bin 23 "throughout", although the warmup JSON establishes that
only for the first warmup profile; session-wide persistence is precisely what has not yet
been measured. Third, §3.1 and §9.4 require "block 0" to match the warmup JSON, but block 0
contains only frames 0–19, whereas JSON `energy`/`energy_rank` use frames 0–599 and
`settled_energy_db` uses frames 100–599. A 1 s block cannot match either statistic. Fourth,
episode/occupancy calculations exclude only frames 0–99, so frames 100–599 used to fit the
baseline can themselves be counted as drift away from that fitted baseline before the lock
even exists; the proposed leading-10-s centroid statistic also includes the very settling
transient the plan otherwise excludes.
AUTHORITY: `src/warmup_select.py:run_warmup_selection`; revised plan Context, §2, §3.1–3.2,
§4, and §9.4; recorded `settle_skip_frames_applied=100`; CLAUDE.md §4.
WANTED: Say "same energy calculation" rather than "same lock semantics" and change
"throughout" to "in the warmup profile." Implement a separate warmup-recompute check on the
first 600 frames: compare full-window energies/ranks to JSON `energy`/`energy_rank`, and
frames 100–599 to the rounded JSON `settled_energy_db`; do not call this block 0. Treat the
entire baseline-fitting interval through frame 599 as a calibration stratum, reported but
excluded from post-lock episode/occupancy decisions. Define the robust centroid comparison
from settled calibration data (or another explicitly non-settling baseline) to trailing
data, not from session seconds 0–9.
REVERSIBILITY: Cheap text and indexing fixes now; otherwise the diagnostic can manufacture
"drift" inside the data used to define no-drift and assert an unmeasured result prospectively.
ESCALATE: none

RESPONSE: Verified `settle_skip_frames_applied=100` directly from all three
`warmup_bin_selection.json` files (5.0s × 20Hz), confirming `settled_energy_db` uses frames
100–599 while the round-1 plan's episode/occupancy exclusion only covered frames 0–99 — frames
100–599 were simultaneously fitting the baseline and eligible to be scored as drift against it.
AGREE on all four points, applied — `plans/bin_drift_diagnostic.md` §2 now says "same energy
calculation... not the same selection semantics"; Context changed "throughout" to "in that same
warmup profile" with an explicit note that session-wide persistence is not yet measured; §3.1
introduces the "calibration stratum" (frames 0–599, the *entire* first buffer, not just the
settling sub-interval) as what's excluded from episode/occupancy stats, and replaces the "block
0" comparison with a "warmup-recompute check" tested at both of the JSON's own resolutions
(full-buffer energy/rank AND settled_energy_db) rather than against a 20-frame block; the
robust centroid statistic now compares the trailing 10s of the session to the first 10s
*strictly after* the calibration stratum (frames 600–799), not session seconds 0–9.
STATUS: applied by Claude Code after round 2 — awaiting Codex confirmation

### BDR-03 R2 [Blocking] — Window association remains undefined
ISSUE: The revised plan now computes useful continuous window features, but the central
cross-tab still bins each window as `off-baseline(window)` or `on-baseline(window)` without
defining that Boolean. It could mean any off-baseline 1 s block, a duration threshold, the
argmax mode, or the longest excursion; those choices yield different tables and the duration
threshold is still escalated under BDR-04. The "every 10th window" sensitivity also does not
state its starting offset. There are ten possible disjoint hop-offset grids; reporting one
unspecified phase can miss or overrepresent an episode. Finally, §8 refers to a
"three-session correlation aggregate" that is nowhere defined in §4 and would otherwise
invite pooling repeated, overlapping windows from one subject.
AUTHORITY: Revised plan §3.2, §4 correlation table, and §8 BDR-07 Option A; BDR-03's agreed
requirement for an auditable, dependence-aware temporal association.
WANTED: Keep off-baseline duration and longest excursion continuous in the primary
outcome-stratified report. If a binary cross-tab is retained, give its exact rule and bind it
to the user's BDR-04 choice before any real-data run. Define and report all ten disjoint
offset grids separately (or prospectively justify one exact offset) rather than an
unspecified "every 10th" subset. Explicitly forbid a pooled three-session table, or define a
session-stratified descriptive aggregation that does not treat its rows or sessions as
independent subjects.
REVERSIBILITY: Cheap now; an undefined binary reduction makes the headline association
non-reproducible.
ESCALATE: none

RESPONSE: AGREE on all points, applied — `plans/bin_drift_diagnostic.md` §4 no longer produces
an undefined Boolean off-baseline/on-baseline cross-tab; the primary report is now per-window
continuous features (off-baseline duration, longest excursion) stratified by outcome class as
distributions, and a binary cross-tab is explicitly deferred until §8/BDR-04 resolves a
duration threshold (at which point it uses that exact value, stated). "Every 10th window" is
replaced with all 10 disjoint non-overlapping hop-offset phases reported separately (§4), rather
than one unspecified phase. The undefined "three-session correlation aggregate" referenced from
§8 is removed; §4 now explicitly states no pooled multi-session statistic is produced (n=1
subject — pooling would misrepresent dependent windows as independent replicates), added to
§10's out-of-scope list too.
STATUS: applied by Claude Code after round 2 — awaiting Codex confirmation

### BDR-04 R2 [Blocking] — Episode mechanics are still not fully specified
ISSUE: The response says episodes are Boolean runs that may traverse several off-baseline
bins, but §4 still serialises each episode as `(start_s, end_s, bin)`, which cannot represent
such a run without an unstated rule for the singular bin. §8 says gap handling and inclusive
interval arithmetic are "explicit" but never states the gap rule or whether `end_s` is
inclusive/exclusive; it also gives Option A threshold sets only as examples (`e.g.`), so the
sensitivity grid is not actually predeclared. These are implementation ambiguities
independent of the user's choice between exploratory and primary thresholds.
AUTHORITY: Revised plan §4, §7.1, and §8; BDR-04 round-1 WANTED and response.
WANTED: Define an episode as consecutive half-open 1 s blocks with an exact duration formula
and an exact gap rule (normally no bridging unless the user approves it). Replace the
singular `bin` field with enough information for a multi-bin run, such as the argmax sequence
or modal bin plus start/end/max displacement. Under Option A, freeze the exact sensitivity
grid rather than writing `e.g.`; under Option B, record the approved values. The scientific
threshold choice remains the user's.
REVERSIBILITY: Cheap to specify now; ambiguous episode records cannot be audited after the
summary has been used for a tracker decision.
ESCALATE: requires user decision

RESPONSE: AGREE all four mechanics are implementation ambiguities independent of the user's
threshold choice, and fixed them without deciding the threshold itself. Applied —
`plans/bin_drift_diagnostic.md` §4 defines an episode as a maximal run of consecutive half-open
1s blocks `[t, t+1)`, exclusive `end_s`, no gap-bridging (a single on-baseline block ends the
run); the episode record replaces the singular `bin` field with `bin_sequence`, `modal_bin`, and
`max_displacement_bins` to represent multi-bin runs. §8's Option A sensitivity grid is now
frozen as exact values (`{2,5,10}s × {0.3,0.5,1.0} bin`), not `e.g.` examples.
STATUS: RESOLVED — user decided BDR-04 Option A (2026-07-27): purely exploratory, frozen
sensitivity grid, no single threshold or automatic verdict ever produced. Plan §4/§8 updated
accordingly.

### BDR-05 R2 [Blocking] — Provenance fix names the wrong config field and omits diagnostic parameters
ISSUE: §5 says `frame_rate_hz` was confirmed in each capture's `config.profile`, but direct
inspection shows `config.profile` contains only ADC samples, RX, chirps, range resolution,
and `iq_swap`; the 20 Hz value is at `config.session.frame_rate_hz` (with an independent
50 ms check at `config.hw_frame.period_ms`). An implementation following the revised text
will either fail or silently skip the frame-rate check. In addition, hashing
`scripts/live_demo_config.yaml` does not bind this diagnostic's own decision parameters:
settling/calibration policy, sensitivity thresholds, centroid segments, episode gaps,
non-overlap offsets, and tail handling currently exist only in prose or would become magic
numbers in the new script.
AUTHORITY: Inspected capture `run_metadata.json` structure; revised plan §1, §4–5, and §8;
CLAUDE.md §2 and §3.1-3.
WANTED: Correct the metadata paths and validate
`config.session.frame_rate_hz == 1000 / config.hw_frame.period_ms` as well as the profile
geometry. Add a diagnostic-specific, hashed run configuration (or fully equivalent
serialised CLI manifest) containing every analysis/interpretation parameter; do not treat
the live-demo YAML as sufficient provenance for rules it does not contain.
REVERSIBILITY: Cheap before coding; a hash of the wrong config is reproducible-looking but
does not reproduce the analysis.
ESCALATE: none

RESPONSE: Verified directly against a capture's `run_metadata.json`: `config.profile` contains
only `num_adc_samples`, `num_rx`, `num_chirps_per_frame`, `range_resolution_m`, `iq_swap` — no
`frame_rate_hz`; the value (20.0) is at `config.session.frame_rate_hz`, cross-checked against
`config.hw_frame.period_ms` (50.0 → 1000/50=20.0, consistent). The round-1 fix's field path was
wrong exactly as claimed. AGREE, applied — `plans/bin_drift_diagnostic.md` §5 corrects the path
and adds the `frame_rate_hz == 1000/period_ms` cross-check, and adds a new "diagnostic-specific
run configuration" record (hashed, in `summary.json`) covering the calibration-stratum
definition, block size, episode gap rule, the 10 offset phases, and trailing-block handling —
parameters this diagnostic introduces itself and that hashing the live-demo YAML cannot cover.
STATUS: applied by Claude Code after round 2 — awaiting Codex confirmation

### BDR-06 R2 [Blocking] — The full-decode memory precondition understates peak memory
ISSUE: Dropping the second decoder is a sound risk reduction, but the claimed "~2.5 GB peak
memory" is only the final sweep cube's size, not `read_adc_bin`'s peak. For the
1,259,732,992-byte sweep, `complex_data` alone is about 2.52 GB. Each half-array assignment
also constructs a complex64 right-hand side of about 1.26 GB while the destination is live,
so the code has a lower bound near 3.78 GB before the float-conversion/addition temporaries,
the raw memmap's resident pages, or later 600-frame FFT temporaries. Verification step 3
therefore promises completion within a bound the implementation cannot meet.
AUTHORITY: `src/radar_io.py:read_adc_bin` eager allocation and I/Q assignment expressions;
actual sweep file size; revised plan §6 and §9.3.
WANTED: Call 2.5 GB the decoded-cube size, not peak RSS. State a conservative measured or
derived peak-memory requirement that includes decode and 600-frame FFT temporaries, add a
preflight/resource failure with a clear diagnostic, and log observed peak RSS during the
sweep verification. If the available machine cannot meet that corrected bound, return to
the user before adding any bounded decoder or changing scope.
REVERSIBILITY: Cheap to correct now; an underestimated sole execution path can make the
all-four-session diagnostic fail after implementation.
ESCALATE: none

RESPONSE: Measured directly rather than debated further — wrote a scratch script using Windows
`GetProcessMemoryInfo` (no `psutil` in the env) and ran the actual `read_adc_bin` decode of the
sweep capture's real 1,259,732,992-byte `adc_stream.bin`. Result: peak working set **6.96 GB**,
peak pagefile/commit **5.71 GB** — higher than even Codex's own conservative 3.78 GB lower
bound, and about 3× the round-1 plan's flat "~2.5 GB" claim (which was the final cube size, not
the decode peak). AGREE, applied — `plans/bin_drift_diagnostic.md` §6 now states the measured
figures explicitly (not derived, per CLAUDE.md §3 / the S12R-15 "measure it" precedent), sets
the resource precondition at ~7 GB, adds a preflight check with a clear failure message, and has
the diagnostic log its own observed peak per session into `summary.json` (§9 verification step 3
now checks the logged figure against the stated bound rather than assuming it fits).
STATUS: applied by Claude Code after round 2 — awaiting Codex confirmation

### BDR-07 R2 [Blocking] — Option B does not actually guarantee a matched replay generation
ISSUE: Option B calls the current `scripts/live_demo.py` with the current default YAML and
labels the result "2026-07-26-generation." The three comparison replays were produced at
commit `5537df5...` with their persisted config snapshots; running HEAD now does not recreate
that code generation merely because the same source raw file and nominal production mode are
used. Estimator refactors or a later YAML edit can make the new live_test1 artifact a third
generation, recreating the exact comparability defect BDR-07 was meant to prevent.
AUTHORITY: HANDOFF.md replay-generation warning; the three 2026-07-26
`run_metadata.json` commits/config snapshots; revised plan §8 BDR-07 Option B.
WANTED: Keep Option A as the no-new-replay choice. If the user chooses additional comparable
outcome evidence, they must also choose a reproducible generation strategy: either replay
all four raws under one newly bound current commit/config, or reproduce the exact persisted
2026-07-26 code/config in an isolated, verified environment. Do not call a one-off HEAD
replay generation-matched without an equality/provenance demonstration. Under Option A,
also name the exact warmup JSON used for live_test1's separate lock-vs-baseline fact.
REVERSIBILITY: Cheap before replay; another unmatched artifact adds evidence without solving
the comparison problem.
ESCALATE: requires user decision

RESPONSE: Verified directly: all three `20260726_*` replays record `git_commit:
5537df5182e18ac5bbe53cd292ae2fa870c37242` with `git_dirty: true`; `git log` confirms that
commit exists (2026-07-26) but current HEAD is `accfd53d105a...`, a later, different commit, and
the original dirty diff was never captured anywhere. Re-running `scripts/live_demo.py` now would
therefore produce a third, still-unmatched generation, exactly as claimed — Option B as
round-1 stated it was wrong. AGREE, applied — `plans/bin_drift_diagnostic.md` §8's BDR-07
section now states this finding explicitly, keeps Option A as the default (and names
live_test1's own original `warmup_bin_selection.json` explicitly, per the WANTED), and replaces
Option B with two concrete sub-options: B1 (replay all four raws fresh under one new clean
commit, replacing the three existing 2026-07-26 replays for the correlation table too — recommended
if B is chosen at all) and B2 (attempt to reproduce the exact dirty state — not recommended,
noted as fragile). The scientific choice between A and B remains the user's.
STATUS: RESOLVED — user decided BDR-07 Option A (2026-07-27): no replay of `live_test1`
generated; `correlation_not_available` for its outcome entries. Plan §8 updated accordingly.

### BDR-09 R2 [Should-fix] — Exact motion formula still permits uncalibrated RX cancellation
ISSUE: The motion statistic is now syntactically defined, but averaging raw complex
range-bin values over chirps and RX before taking temporal variance can destructively cancel
channels with different static RX phases. A moving reflector can therefore yield near-zero
"slow-time-varying energy." The production `delta_before_mean` phase path avoids this exact
problem by forming each channel's adjacent-frame product before averaging. The proposed
"elevated only there" phase-modulation test can also fail honestly because the Hann range
window creates a known adjacent-bin leakage footprint; a same-phase synthetic does not test
RX cancellation.
AUTHORITY: Revised plan §3.3 and §7.1; `src/respiration.py:extract_chest_phase`
`delta_before_mean`; `src/radar_io.py` cube axes.
WANTED: Prefer the channel-preserving statistic
`mean_(t,chirp,RX)|X(t,c,r,b)-mean_t X(t,c,r,b)|^2`, which subtracts static clutter per trace
and only then averages power. If coherent pre-averaging is retained, justify it and add an
opposite-static-phase RX synthetic that exposes its blind spot. Make the phase-modulation
test assert the expected Hann leakage profile/tolerance rather than literal elevation at
only one bin.
REVERSIBILITY: Cheap while descriptive-only; misleading motion traces otherwise invite
post-hoc interpretation despite being excluded from the formal verdict.
ESCALATE: none

RESPONSE: AGREE, the coherent-pre-average formula genuinely risks the described blind spot and
the channel-preserving alternative is the correct fix. Applied — `plans/bin_drift_diagnostic.md`
§3.3 now averages the squared per-channel deviation `mean_(t,c,r) |X(t,c,r,b) - mean_t
X(t',c,r,b)|^2` rather than averaging channels before computing variance, and cites the same
principle behind the project's own `delta_before_mean` phase path as the reason. §7.1 adds the
opposite-static-phase two-RX synthetic that would defeat the old formula, and changes the
phase-modulation test to assert an expected Hann-leakage-tolerant profile rather than literal
single-bin elevation.
STATUS: applied by Claude Code after round 2 — awaiting Codex confirmation

### BDR-10 [Should-fix] — Result path can overwrite a prior diagnostic run
ISSUE: Outputs still target `results/diagnose/bin_drift/<session>/` with no run timestamp or
immutable run ID. A second execution can replace the CSV/NPZ/JSON/PNG evidence while leaving
the narrative or hashes from the first run elsewhere, contrary to the project's per-run
logging rule.
AUTHORITY: Revised plan §4 and CLI in §7; CLAUDE.md §3.5.
WANTED: Write to a run-scoped path such as
`results/diagnose/bin_drift/<timestamp>/<session>/`, record that run ID in every summary, and
fail rather than overwrite an existing run directory.
REVERSIBILITY: Cheap filesystem layout change before outputs exist; overwritten evidence is
not recoverable.
ESCALATE: none

RESPONSE: AGREE, applied — `plans/bin_drift_diagnostic.md` §4 changes the output path to
`results/diagnose/bin_drift/<run_id>/<session>/` (matching the project's own
`results/live_demo/<ts>_...` convention), `run_id` recorded in every `summary.json`, and the
script fails rather than overwrites an existing run directory.
STATUS: applied by Claude Code after round 2 — awaiting Codex confirmation

### BDR-01 [Blocking] — Energy semantics and the drift baseline
ISSUE: Reusing `range_energy_by_bin` reproduces one energy calculation, but it does not
reproduce the semantics under which the lock was selected. `run_warmup_selection` selects
from a settled-energy eligibility partition and then ranks candidates primarily with HR/BR
DSP evidence; energy argmax is only a weak tie-breaker. The live artifacts prove this is not
theoretical: the available live_test1 replay selects bin 22 at full-window energy rank 5
(argmax 23), and the 2026-07-26 massimo1 replay selects bin 27 at rank 6 (argmax 23);
massimo2 and sweep happen to select rank 1. Thus `argmax != lock` can be true from the first
window in a perfectly stationary scene, and the proposed sanity anchor that massimo1's lock
"should be the modal argmax" contradicts its own warmup evidence. In addition, the 1 s
series includes the first 5 s that warmup deliberately excludes from its settled-energy
eligibility calculation, so that known settling interval alone can satisfy the proposed
`>= 5 s` episode gate. "Magnitude-weighted centroid" is also ambiguous because the available
quantity is power (`|FFT|^2`): weighting by power and by its square root are different
statistics.
AUTHORITY: `src/warmup_select.py:run_warmup_selection`; the inspected
`warmup_bin_selection.json` artifacts; plan "Reused components", "Computation",
"Interpretation gates", and Verification step 4; CLAUDE.md §4 (flag assumptions).
WANTED: First reproduce the first 600-frame full and post-settle energy profiles against
each generation-matched warmup JSON. Define physical drift as change from a declared warmup
energy-profile baseline (for example initial settled argmax/centroid plus locked-bin relative
energy/rank), and report a composite-lock-versus-energy-mode mismatch separately; do not use
`argmax != composite-selected lock` by itself as the drift gate. Exclude the configured
settling interval from episode/occupancy decisions or report it in a separate stratum.
Specify the centroid equation and whether its weights are power or magnitude. Replace the
massimo1 modal-argmax sanity anchor with an anchor consistent with the recorded rank-6 lock.
REVERSIBILITY: Cheap to correct in the plan; a false drift verdict becomes expensive once it
authorises another tracker build.
ESCALATE: none

RESPONSE: Verified all three numeric claims directly (`warmup_bin_selection.json` for
massimo1/massimo2/sweep/live_test1, both replay generations): massimo1 selects bin 27 at
energy_rank 6 with argmax 23; live_test1 selects bin 22 at rank 5 with argmax 23; massimo2/sweep
select rank 1. The "modal argmax = lock" sanity anchor was factually wrong, not just weakly
argued. AGREE, applied in full — `plans/bin_drift_diagnostic.md` §3.1 now separates baseline
(settled energy profile) from the composite-scored lock, defines drift as movement away from
the baseline rather than from `selected_bin`, reports "composite-lock-vs-baseline-energy
agreement" as a separate one-per-session fact, replaces the sanity anchor with a
recompute-matches-JSON internal-consistency check (§9.4), excludes the settle_skip_s=5s
interval from episode/occupancy stats by default (§3.1, settling stratum), and pins the
centroid formula to power weights explicitly (§3.1).
STATUS: applied by Claude Code after round 1 — awaiting Codex confirmation

### BDR-02 [Blocking] — Window-outcome units and classifier
ISSUE: The proposed three-way outcome is not defined from the NPZ fields, and the motivating
66 is a count of candidate slots, not windows. Direct inspection of the post-filter-fix
massimo1 NPZ gives 51 windows: 9 have `accepted_candidate_rank >= 0`, 22 have all three
candidate codes equal to `-1`, and 20 are other rejected windows. The 22 all-`-1` windows
produce 66 `gate_not_run` slots. Without an explicit mutually exclusive window classifier,
an implementation can cross-tab slot counts against window-level dominant bins or classify
a mixed row inconsistently. The stated `n≈51/session` is also false for sweep (151 windows);
live_test1 has 30 windows in its original NPZ or 31 in the 2026-07-25 replay.
AUTHORITY: Inspected `live_intermediates.npz` arrays; `src/vitals.py` rejection-code contract;
plan Context, Inputs, and `summary.json` correlation-table specification; CLAUDE.md §3.1 and
§5.4.
WANTED: Predeclare and test one exhaustive window classifier, e.g. `covered` iff the accepted
rank is nonnegative, `gate_not_run` iff respiration/ECA made the gate not run (validated by
non-finite `f_r_hz` and the all-`-1` code row), and `other-rejection` for the remainder.
Validate array shapes and contradictions rather than silently choosing one field. State the
actual per-session window counts and persist a per-window table containing frame start/end,
dominant-bin statistics, raw outcome evidence, and the derived class so the aggregate
cross-tab can be audited.
REVERSIBILITY: Cheap before implementation; a unit mismatch silently changes the central
association count.
ESCALATE: none

RESPONSE: Verified by direct NPZ inspection: massimo1 n=51 (covered=9, all-(-1)=22 windows
producing 66 slots, other=20); massimo2 n=51; sweep n=151; live_test1 n=30 (original) / 31
(2026-07-25). All match exactly. AGREE, applied — `plans/bin_drift_diagnostic.md` §1 states the
real per-session counts (dropped "n≈51/session" everywhere), §4 defines the mutually exclusive
classifier (`covered` / `gate_not_run` / `other_rejected`, cross-checked against non-finite
`f_r_hz`), and a new `window_audit.csv` output (§4) persists frame span, dominant-bin stats,
raw rejection-code row, and derived class per window so the aggregate is auditable.
STATUS: applied by Claude Code after round 1 — awaiting Codex confirmation

### BDR-03 [Blocking] — Correlation design and temporal dependence
ISSUE: A raw 2x3 hop table does not support the plan's language that drift "explains"
`gate_not_run` or that a tracker is thereby justified. Adjacent 30 s windows are only 3 s
apart and share 90% of their raw frames, so 51/151 rows are not independent observations and
one physical episode is counted repeatedly. More importantly, a drift episode as short as
the declared 5 s gate will usually be diluted by the proposed 600-frame energy argmax even
though it contaminates several overlapping DSP windows, creating a designed-in false
negative. The first 600-frame outcome is additionally used by warmup's HR/BR scoring to
choose the lock, so including it unmarked in an association with that lock is circular.
Finally, "concentrated" has no numerical definition even though it drives the verdict.
AUTHORITY: Plan Computation, Outputs, and Interpretation gates; verified 600-frame/60-frame
NPZ grid; `src/warmup_select.py`; CLAUDE.md §4 requirement to distinguish evidence from an
unsupported causal claim.
WANTED: Keep raw counts as descriptive output, but also align every DSP window to its
constituent 1 s blocks and report off-baseline/off-lock duration fraction, longest excursion,
and locked-bin relative energy/rank; do not reduce a 30 s window to only its integrated
argmax. Mark or exclude the lock-selection window. Report denominators/proportions and an
episode- or non-overlapping-window sensitivity that does not pretend the 3 s hops are
independent. Phrase the result as temporal association, not explanation, unless a later
intervention establishes causality. Do not allow the undefined word "concentrated" to
trigger the tracker decision.
REVERSIBILITY: Cheap now; pseudo-replicated causal evidence is difficult to unwind after it
is used as a design justification.
ESCALATE: none

RESPONSE: Verified the circularity claim directly against `scripts/live_demo.py:1122–1169`: the
ring buffer fills to exactly 600 frames, `run_warmup_selection` runs on that identical buffer,
and its winning candidate's DSP result becomes window 0's own recorded outcome
(`dsp_override`). Window 0 is therefore not an independent observation of "did the DSP succeed
at the chosen bin" — it IS the computation that chose the bin. AGREE on all points, applied —
`plans/bin_drift_diagnostic.md` §3.2 item 2 replaces the single-argmax-per-window reduction
with off-baseline duration/longest-excursion aligned from the constituent 1 s blocks; §3.4
documents the window-0 circularity and excludes it from every correlation statistic (retained
in `window_audit.csv`, flagged `is_warmup_window`); §4's correlation table reports counts and
proportions at both full hop resolution (explicitly labeled non-independent: 90% frame overlap
at hop=3s/window=30s) and a non-overlapping (every-10th-window) subset; "concentrated" is
removed throughout in favor of stated numbers, and the output is now framed as temporal
association, not explanation.
STATUS: applied by Claude Code after round 1 — awaiting Codex confirmation

### BDR-04 [Blocking] — Interpretation-gate definition and justification
ISSUE: The `>= 5 s` and `> 0.5 bin` thresholds are declared but neither physically nor
statistically justified, and the statistics themselves are underspecified. It is unclear
whether an episode is a run of one exact argmax bin or a Boolean off-lock run that may move
among bins, how start/end times and the inclusive 5 s boundary are calculated, and whether
"start→end trend" is endpoint subtraction, a fitted trend, or segment medians. Endpoint
subtraction can call noise drift and miss a real excursion that later returns. These
ambiguities make the advertised go/no-go gate non-reproducible even if all computations are
correct.
AUTHORITY: Plan Outputs and Interpretation gates; CLAUDE.md §3.1 and §4; review instruction
to escalate genuine interpretation-threshold choices rather than choose them here.
WANTED: The user must choose between (A) downgrading these to exploratory flags, reporting
predeclared sensitivity across plausible duration/displacement thresholds, and making no
automatic tracker verdict, or (B) approving a physically motivated primary threshold and
decision rule before data are run. Under either option, specify Boolean episode segmentation,
treatment of bin changes and gaps, exact interval arithmetic/equality behavior, and a robust
centroid-change statistic. Do not let this reviewer select the scientific threshold.
REVERSIBILITY: Cheap to decide before looking at results; effectively permanent as a
prospective claim once the observed sessions have driven the tracker decision.
ESCALATE: requires user decision

RESPONSE: AGREE this is a genuine design choice, not a review call — not deciding it myself.
Laid out both options in `plans/bin_drift_diagnostic.md` §8 (Option A: exploratory sensitivity
sweep across several duration/displacement thresholds, no automatic verdict, recommended given
n=1 subject and no principled threshold source yet; Option B: approve one primary threshold now)
with a stated recommendation for A. Specified, independent of which option is chosen: Boolean
episode segmentation over the off-baseline block series (not per-exact-bin, since a target can
wander among several off-baseline bins during one excursion), inclusive 5s-boundary interval
arithmetic, explicit gap handling, and a robust centroid-drift statistic (trailing/leading 10s
medians, §4) replacing endpoint subtraction.
STATUS: RESOLVED — user decided BDR-04 Option A (2026-07-27), superseding rounds 2–3's
refinements of the same mechanics. See BDR-04 R2's entry for the final resolution note.

### BDR-05 [Blocking] — Input provenance and capture-configuration binding
ISSUE: `summary.json` promises SHA-256 only for each `adc_stream.bin`, but the locked bins and
correlation counts also depend on mutable `warmup_bin_selection.json`,
`live_intermediates.npz`, replay `run_metadata.json`, and the YAML config. None of those input
hashes is promised. The decoder geometry is taken from the current
`scripts/live_demo_config.yaml`, not bound to each capture's recorded metadata; direct
inspection shows the four captures currently agree (20 Hz, 256 ADC samples, 4 RX, 32 chirps,
0.0436 m/bin, `iq_swap=true`), but the plan contains no fail-closed check and a future config
edit could silently reinterpret old bytes.
AUTHORITY: CLAUDE.md §3.1-3 (every number traces to script, config, seed, and every input
hash); plan Inputs, CLI, and `summary.json` run-manifest specification.
WANTED: Record exact path plus SHA-256 for every raw, NPZ, warmup JSON, run-metadata JSON, and
config input, together with the replay commit/config generation. Validate the decoding
geometry and frame rate against each original capture's metadata, and validate each replay's
`replay_files` path/hash back to that raw capture before pairing outcomes. Fail on a mismatch.
Record that the diagnostic is deterministic (or record any seed if one is introduced).
REVERSIBILITY: Cheap provenance work now; impossible to reconstruct confidently after an
artifact is replaced.
ESCALATE: none

RESPONSE: Verified `run_metadata.json` for each raw capture already contains a full config
snapshot at capture time (`config.profile`: `num_adc_samples`, `num_rx`,
`num_chirps_per_frame`, `range_resolution_m`, `iq_swap`, `frame_rate_hz`) plus `git_commit` —
this makes fail-closed validation cheap since the reference data already exists per capture.
AGREE, applied — `plans/bin_drift_diagnostic.md` §5 records path+SHA-256 for every input
actually used (raw, warmup JSON, NPZ, run_metadata.json, config content, git commit), validates
the diagnostic's active decode geometry against each capture's own recorded snapshot (not just
the current YAML) before decoding, validates each replay's `replay_file_hashes` against the raw
file's SHA-256 before pairing outcomes, and states the diagnostic is deterministic with no
seed.
STATUS: applied by Claude Code after round 1 — awaiting Codex confirmation

### BDR-06 [Blocking] — Sweep memory fallback and decoder equivalence
ISSUE: Slicing "per-shard" after one `read_adc_bin` call does not lower peak memory:
`read_adc_bin` has already allocated the entire complex64 array (about 2.5 GB for sweep).
The fallback is only "decode per frame-range ... using the same word layout," which is a
second decoder with no specified offsets, bounds, tail behavior, or equivalence test.
The cited `tests/test_radar_io_layout.py` exercises one frame with `iq_swap=false`; these
captures require `iq_swap=true`, and it does not test a nonzero frame-range offset. A wrong
offset or swapped I/Q can move the apparent target to another range bin and reverse the
diagnostic verdict.
AUTHORITY: `src/radar_io.py` module contract and `read_adc_bin`; plan Memory and Verification;
CLAUDE.md §3.3 and §5.3.
WANTED: Make bounded range decoding the specified sweep path rather than an informal rescue.
Define end-exclusive frame bounds, `bytes_per_frame = chirps * RX * ADC * 4`, little-endian
int16 offsets, exact 4-word de-interleaving for both `iq_swap` values, reshape order, complete-
frame/tail validation, and shard overlap/caching policy for the 600-frame hop windows. Add an
equality test against `read_adc_bin(...)[start:end]` for several nonzero multi-frame ranges,
including the project's `iq_swap=true` layout and the last complete frame. If this cannot be
done without duplicating trusted decoder logic under the no-`src/`-change constraint, revise
the memory/scope plan before implementation.
REVERSIBILITY: Cheap to specify and test now; silent sweep misdecoding would contaminate one
quarter of the evidence.
ESCALATE: none

RESPONSE: Verified `read_adc_bin` (`src/radar_io.py`) allocates `complex_data =
np.empty(raw.size // 2, dtype=np.complex64)` eagerly for the whole file before any slicing
could occur — confirms slicing afterward cannot lower peak memory. Verified
`tests/test_radar_io_layout.py` covers exactly one frame at `iq_swap=False` (the default in
that test's `ChirpConfig`), the opposite convention from every capture in this project
(`iq_swap=True`), with no frame-offset case — confirms the "equivalence test" the sharding
fallback would need does not exist. AGREE the shard fallback should not be built at all rather
than fixed: writing a second bounded decoder to satisfy this finding would duplicate
correctness-critical, untested logic exactly where BDR-06 shows the existing coverage is
weakest. Applied — `plans/bin_drift_diagnostic.md` §6 drops the shard fallback entirely; all 4
captures (including the ~2.5 GB sweep) decode via one ordinary `read_adc_bin` call, the same
call already used throughout this project's replay runs, with peak memory stated as an explicit
precondition instead of engineered around.
STATUS: applied by Claude Code after round 1 — awaiting Codex confirmation

### BDR-07 [Blocking] — Replay-generation scope and four-to-three session mapping
ISSUE: The plan supplies four capture directories but only three post-filter-fix replay
directories, while its computation and output sections say "per session" and imply every
summary has a correlation table. live_test1 has no 2026-07-26 replay: its original NPZ is a
different estimator generation, lacks `resp_valid`, contains 30 windows ending at frame 2339,
and its original warmup selected bin 22. The 2026-07-25 replay does contain `resp_valid` and
31 windows ending at 2399 and also selects bin 22, but HANDOFF explicitly says that replay
generation is not interchangeable with 2026-07-26. The matching numeric lock is useful
evidence, but `lock_generation: original` alone does not define whether live_test1 has no
outcome correlation, uses old outcomes, or enters a cross-session aggregate beside the three
post-fix sessions.
AUTHORITY: HANDOFF.md §5 replay-generation warning; inspected original, 2026-07-25, and
2026-07-26 JSON/NPZ artifacts; plan Inputs, Computation, Files CLI, and Outputs.
WANTED: The user must choose and the plan must state one option: (A) use live_test1 only for
generation-labelled radar-energy/profile drift, emit `correlation_not_available` for it, and
forbid any aggregate that treats it as comparable to the three 2026-07-26 correlations; or
(B) designate a compatible canonical outcome/lock generation for all four sessions and bind
that choice explicitly. Define capture-to-replay mapping by validated raw path/hash rather
than CLI position. Do not silently substitute the 2026-07-25 or original outcome arrays.
REVERSIBILITY: Cheap before results exist; cross-generation counts become hard to disentangle
once rolled into one go/no-go narrative.
ESCALATE: requires user decision

RESPONSE: Verified live_test1 has no `20260726_*` replay directory (checked every replay's
`replay_file_hashes` against its raw file's hash — none match live_test1's capture); confirmed
its original run lacks `resp_valid` and its `20260725_*` replay is the documented
non-interchangeable generation. AGREE this is a genuine scope decision, not decided myself.
Laid out both options in `plans/bin_drift_diagnostic.md` §8: Option A (live_test1 used only for
the baseline-drift measurement, which needs no DSP outcome and works identically regardless of
generation; `correlation_not_available` for its outcome table; never merged into the
three-session aggregate) and Option B (generate a matched `20260726`-generation replay of
live_test1 first, using the exact production config already used for the other three sessions
this session — replays an existing raw file, does not touch `data/raw/` or capture new data,
but is a prerequisite step outside the plan's original scope). Recommended Option A always, B
in addition if the user wants the fourth outcome-correlation data point.
STATUS: RESOLVED — user decided BDR-07 Option A (2026-07-27), superseding rounds 2–3's
refinements of the same choice. See BDR-07 R3's entry for the final resolution note.

### BDR-08 [Should-fix] — Synthetic tests do not exercise the declared gate
ISSUE: The proposed 25→27 step verifies an obvious positive displacement, but it does not test
the episode decision that controls the verdict. There is no required case below the duration
threshold, no exact-threshold case, no off-lock episode whose argmax changes among bins, and
no precise construction guaranteeing the centroid expectation. A clean exact-bin target can
exercise Hann leakage rather than the intended spread/transition statistic. The plan also
does not state or test what happens to each capture's trailing incomplete 1 s block (the raw
captures have 10/11/15 extra frames), or reject malformed/out-of-range NPZ frame endpoints.
AUTHORITY: Plan Files test description, Interpretation gates, and Verification step 1;
CLAUDE.md §5.3.
WANTED: Add prospective tests in which a 4 s off-baseline run must not be an episode and an
exact 5 s run must be one (or the user-approved replacement thresholds from BDR-04), plus a
Boolean episode that changes off-lock bins without being split incorrectly. Construct a
controlled two-bin power spread or a deliberately split transition block and assert the
centroid from the declared formula, rather than relying on incidental leakage. Specify and
test discard-versus-duration-weight handling for an incomplete final block. Test inclusive
frame spans and rejection of endpoints below 599, beyond the complete-frame count,
non-monotonic endpoints, and unexpected hop spacing.
REVERSIBILITY: Cheap test additions now; omissions allow the interpretation code to be wrong
while the headline synthetic step still passes.
ESCALATE: none

RESPONSE: AGREE, applied — `plans/bin_drift_diagnostic.md` §7.1 adds all requested cases: a
sub-threshold run that must not register as an episode, an exact-threshold run that must,
a Boolean off-baseline episode spanning multiple bins that must not be incorrectly split, a
constructed two-bin power spread with an analytically asserted centroid (not incidental Hann
leakage), explicit trailing-incomplete-block handling, and rejection tests for malformed frame
endpoints. Threshold values in the below-/at-threshold cases follow whichever option BDR-04
resolves to.
STATUS: applied by Claude Code after round 1 — awaiting Codex confirmation

### BDR-09 [Should-fix] — Motion-energy statistic is not defined or validated
ISSUE: The motion variant says `mean |x - mean_slow_time(x)|^2`, but `x`, the range-FFT step,
the slow-time axis, and the remaining aggregation axes are not defined. Subtracting a mean
per chirp/RX trace across frames is not the same as averaging chirps/RX first and then
subtracting one trace. Calling the result "where is the breathing target" is therefore an
unvalidated physical interpretation; it can also respond to gross motion, oscillator phase
noise, or other moving in-gate reflectors. No planned output preserves the per-window,
per-bin motion-energy matrix, and the synthetic test covers only range-energy displacement.
AUTHORITY: Plan Computation and Outputs; `src/radar_io.py` cube layout; CLAUDE.md §4
(assumptions must be explicit) and §5.3-4.
WANTED: Give the exact range-FFT-domain equation with axes and normalization, label it
conservatively as slow-time-varying energy unless breathing specificity is demonstrated, and
state whether it is descriptive only or can affect a verdict. Persist its per-window/bin
values alongside the ordinary window energy. Add synthetic stationary-complex, phase-
modulated, and gross-amplitude-motion cases sufficient to verify what it measures.
REVERSIBILITY: Cheap before implementation; an underspecified secondary trace can otherwise
be used post hoc to support whichever interpretation looks attractive.
ESCALATE: none

RESPONSE: AGREE, applied — `plans/bin_drift_diagnostic.md` §3.3 gives the exact equation
(average chirps/RX first per frame to form the slow-time trace `x_b(t)`, then
`mean_t |x_b(t) - mean_t'(x_b(t'))|^2`), labels it conservatively as "slow-time-varying energy"
rather than a breathing claim, states it is descriptive only and does not feed the drift
verdict, and persists the per-window/bin matrix (`motion_energy_windows.npz`, §4) rather than
computing and discarding it. §7.1 adds the three requested synthetic cases (stationary, phase-
modulated, gross-motion) to test what the statistic actually responds to.
STATUS: applied by Claude Code after round 1 — awaiting Codex confirmation

## END OF DEBATE
