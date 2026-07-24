# Cross-model re-review: warmup range-bin eligibility partition (radar-vitals)

You are the independent reviewer in a cross-model review process (Claude implemented; you
verify — CLAUDE.md §6). You have access to this project's folder; use it. Your previous pass
on this same change (or a peer model's — treat the prior review as a claim to re-verify, not
a given) returned **REJECT**. The implementer says every finding is now addressed. Re-derive
that independently — do not take the fix summary's word for anything.

## Read first

1. `notes/cross_review_warmup_veto_prompt.md` — the original review request: system context,
   the mislock this whole effort fixes, the design-variant comparison, the original diff.
2. `notes/cross_review_warmup_veto_review_1_findings.md` — the REJECT verdict and all 5
   findings (2 MAJOR, 2 MINOR, 1 NOTE) you are re-checking here.

## What changed since the REJECT

Nothing is committed — these are uncommitted working-tree edits. Diff them directly:

```
git diff HEAD -- scripts/live_demo.py scripts/live_demo_config.yaml tests/test_live_demo_warmup_helpers.py
```

`scripts/live_demo.py` contains **one unrelated pre-existing edit** you should ignore: a
`delay_s = 15` → `120` change inside `main()` (a live-capture countdown tweak, present before
this investigation started, nothing to do with bin selection). Everything else in that diff
is in scope. Also new and in scope: `scripts/validate_warmup_selection.py` (untracked, not in
the `git diff` above — read it directly). Ignore the other untracked files
(`scripts/stage1a_harmonic_coherence.py`, `scripts/stage1b_exploratory_motion.py`,
`scripts/stage1b_temporal_continuity.py`) — unrelated WIP, not part of this change.

Summary of what the implementer changed, to check against the actual diff:

- **F1 (the rejection reason)**: `_run_warmup_selection` now partitions candidates into
  energy-eligible vs ineligible (via `energy_eligible[bin] = settled_db[bin] >= threshold_db`,
  computed for every candidate). Selection draws from
  `winner_pool = eligible_good if eligible_good else good` — an ineligible bin can only win
  when zero eligible candidates' DSP succeeded, and that path forces
  `selection_confidence = "low"` and appends `_no_energy_eligible_dsp_success` to
  `selection_reason`.
- **Renamed** the config key `hr_bonus_min_settled_db` → `energy_eligibility_min_settled_db`
  (clean rename, no alias — grep confirms zero remaining references to the old name).
- **F3**: `settle_skip_s` validated finite/>=0 (else `ValueError`); when the requested skip
  exceeds the warmup window, evidence now records `settle_skip_frames_applied` (0 on
  fallback), `settle_skip_fallback_full_window` (bool), and a stderr warning fires.
- **New threshold validation**: `energy_eligibility_min_settled_db` must be finite and <= 0,
  else `ValueError`.
- **F4**: empty `candidate_bins` now raises `ValueError` at function entry instead of an
  opaque `max()` crash.
- **F2 (scope)**: config/code comments reworded to state the −12 dB prior is empirical
  (4 sessions / 1 subject, 2026-07-14/15), scene-dependent (assumes the seated subject is the
  dominant in-gate reflector), and not yet validated against competing reflectors or the
  planned 10-subject study. A new stderr line fires whenever any `hr_valid` candidate is
  energy-ineligible, naming the bin(s) and their settled-dB deficit, so the veto firing is
  observable live (not just in the JSON artifact).
- Evidence JSON additions: `energy_eligible` per candidate (including DSP-failed ones —
  eligibility depends only on settled energy), `all_candidates_energy_ineligible`,
  `eligible_dsp_success_count`, `fallback_used`.
- **New tests** in `tests/test_live_demo_warmup_helpers.py` (11 added, 26 total in the file):
  the exact F1 regression scenario (ineligible bin with br=high+valid vs eligible bin with
  br=medium — fails on yesterday's veto-only code, passes now); the mixed-fallback path
  (eligible bins' DSP all raise, only an ineligible bin succeeds); settle-skip overrun;
  invalid settle_skip_s (negative, NaN); invalid threshold (positive, inf); the eligibility
  boundary at exactly −12.0 dB via a monkeypatched `_range_energy_by_bin` (avoids float fuzz
  from FFT/log10); empty candidate_bins; a dominant-non-chest-reflector scenario asserting the
  stderr warning fires and the true chest stays visible as `hr_valid=True,
  energy_eligible=False` even though it loses; `energy_eligible` present for DSP-failed
  candidates.
- New tracked `scripts/validate_warmup_selection.py`: re-runs the real
  `_run_warmup_selection` on the first 600 frames of each of 4 recorded sessions'
  `adc_stream.bin` (hashed), compares against expected picks. Claimed output: all 4 PASS
  (`live_test1`→22, `massimo1`→23, `massimo2`→26, `sweep`→26), i.e. the eligibility partition
  changes **no historical pick** — it only removes the reachable failure path.
- Claimed test results: `pytest tests/test_live_demo_warmup_helpers.py` → 26 passed;
  full suite `pytest tests/` → 795 passed, 1 xfailed, 0 regressions (up from 784 pre-change).

## What to verify (re-derive, don't trust)

For each of the 5 original findings, confirm independently whether it is actually resolved:

1. **F1**: Read the eligibility-partition logic yourself. Can you construct a scenario where
   an ineligible bin still wins WITHOUT `fallback_used=True`? Check the sort key applied to
   `winner_pool` — does filtering to `eligible_good` before sorting fully prevent an
   ineligible candidate from being compared against eligible ones at all, or is there a path
   where `good` (unfiltered) still gets used for the win decision while `eligible_good` is
   only used for something else (i.e. a bug where the filter is computed but not applied)?
2. **F2**: Is the rewording sufficient, or does this deserve an actual robustness change (e.g.
   local-mainlobe / body-cluster reference instead of global max) before being called
   resolved? The implementer's position is that honest scoping + live observability is the
   correct scope for now, deferring the more general fix. Push back on this if you disagree.
3. **F3**: Trace the settle-skip validation and the `settle_skip_fallback_full_window` logic.
   Edge case: what happens when `settle_skip_s` is valid but rounds to exactly
   `cube.shape[0]` frames (boundary, not `>`)? What about `settle_skip_s = 0` intentionally
   configured (is that correctly treated as "no skip requested" rather than "invalid
   fallback"?).
4. **F4**: Confirm the empty-candidate-bins check actually fires before any code path that
   could still hit `max()` on empty data (e.g. `max(settled_energies.values())` — is this
   still reachable with a non-empty `candidate_bins` list but degenerate energies?).
5. **Tests**: Do the new tests actually pin the mechanisms they claim to, or could they pass
   by accident (e.g. wrong assertion, or a scenario that doesn't actually exercise the sort
   key change)? Specifically check the boundary test (`test_energy_eligibility_threshold_
   boundary`) — does monkeypatching `_range_energy_by_bin` correctly intercept BOTH the
   full-window and settled-window calls inside the function, or could stale/real energies
   leak through for one of them?

Also independently sanity-check the new claims that weren't part of the original findings:
run the test suite and `scripts/validate_warmup_selection.py` yourself if you have execution
access (`conda run -n radar-vitals python -m pytest tests/test_live_demo_warmup_helpers.py -q`
and `conda run -n radar-vitals python scripts/validate_warmup_selection.py`); don't just trust
the reported pass counts. And flag anything NEW the rewrite introduced that wasn't in the
original 5 findings — this was a substantial restructure of the function, not a patch, so
treat it as a fresh review of the whole function, not just a diff of the 5 line items.

## Output

For each of the 5 original findings: state whether it is RESOLVED, PARTIALLY RESOLVED, or
NOT RESOLVED, with evidence (file/line, or a concrete scenario that still breaks it). Then
list any new findings from reviewing the rewritten function as a whole. Then give a verdict:
APPROVE, APPROVE-WITH-NITS, or REJECT.
