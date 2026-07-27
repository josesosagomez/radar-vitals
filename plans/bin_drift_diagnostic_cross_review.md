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
(round 4 processed — see DEBATE COMMENTS. Awaiting Codex round 5.)
## END OF COMMENTS

## DEBATE COMMENTS

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
