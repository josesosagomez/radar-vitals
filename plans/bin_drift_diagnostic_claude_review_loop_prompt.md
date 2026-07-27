An independent reviewer (Codex) is reviewing `plans/bin_drift_diagnostic.md` and its
implementation (`scripts/diagnose_bin_drift.py`) — a **read-only** diagnostic that measures
whether the dominant radar reflector drifts across range bins during a session, using the 4
existing captures. Its feedback appears in a section starting `COMMENTS OF CODEX` and ending
`END OF COMMENTS` inside `plans/bin_drift_diagnostic_cross_review.md`. Process every comment.

> **Phase, as of round 5 (2026-07-28):** this is **no longer a pre-implementation plan review.**
> The diagnostic is implemented, tested, and has been run on all 4 real captures three times.
> Rounds 4–5 reopened the review AFTER implementation and found real defects in the shipped code
> — a decided sensitivity-grid axis never wired up (round 4); a core measurement (window-scale
> energy) approximated rather than computed, an arithmetic bug, geometry validated against the
> wrong file, motion energy computed for the whole capture instead of per window, and config
> values that were hashed but never enforced (round 5). **Treat every new finding as potentially
> a real code bug, not a wording issue — verify against the actual running code and real output,
> not just the plan's prose, every time.** Check `HANDOFF.md` §3.1 for the current `run_id` before
> reading any `results/diagnose/bin_drift/` output — two earlier runs are superseded.

This is a review under CLAUDE.md §6. The diagnostic's evidence informs whether a 5-bin relock
tracker is worth building — a similar feature was already built once (`vital_signs_v9`
relocking, HISTORY.md 2026-07-02) and reverted 2026-07-09 as "not worth its complexity/risk for
now," a call made without this measurement. A flawed diagnostic either sends the project chasing
a tracker for drift that isn't real, or wrongly rules out the leading candidate explanation for
massimo1's `gate_not_run = 66` (52% of that session's dead windows).

### Hard constraints
- Your writable files are `plans/bin_drift_diagnostic.md`, `scripts/diagnose_bin_drift.py`,
  `scripts/diagnose_bin_drift_config.yaml`, `tests/test_diagnose_bin_drift.py`, the
  `DEBATE COMMENTS` section of `plans/bin_drift_diagnostic_cross_review.md`, and — **only at the
  end of a processed round**, per the propagation steps below — `HISTORY.md` (append) and
  `HANDOFF.md` (rewrite). Do not edit Codex's comment text. Do not edit `notes/*_prespec.md` or
  any file outside this review's scope.
- **No tuning to the reference** (CLAUDE.md §4): every accepted fix must be justified from radar
  signal properties or measurement correctness — never from anything that would let Masimo
  agreement influence bin selection, even indirectly. If a comment (or your own change) would do
  that, reject it and say so.
- Verification is read-only for INSPECTION (reading files, `git log`/`git show`, inspecting
  NPZ/JSON/CSV evidence) but **applying an agreed fix means editing the real code**, running
  `tests/test_diagnose_bin_drift.py` then the full suite
  (`conda run -n radar-vitals python -m pytest tests/ -q`), and — once a batch of fixes is
  applied and tested — **committing** (the diagnostic's own clean-tree gate requires a commit
  before a citable re-run) and **re-running the diagnostic on all 4 real captures** (see
  Propagating a fix, below). Never run capture, live hardware, or anything touching `data/raw/`.

### Before you start
Re-read `CLAUDE.md`, then `HANDOFF.md` (current state; note §5's "Two replay generations exist and
are NOT interchangeable" and "Warmup bin selection consumes BR/HR" gotchas), then the `HISTORY.md`
entries for **2026-07-02** and **2026-07-09** (the relocking feature built, then reverted — this
plan must not repeat that design blind), then `plans/bin_drift_diagnostic.md`, then the code it
proposes to reuse — `src/warmup_select.py` (`range_energy_by_bin`, `derive_candidate_bins`,
`run_warmup_selection`) and `src/radar_io.py` (`read_adc_bin`, the I/Q de-interleaving convention).
Confirm you're reading current file state, not what you remember writing.

### Document layout
`plans/bin_drift_diagnostic_cross_review.md` has the brief, then `COMMENTS OF CODEX` …
`END OF COMMENTS` (Codex's inbox), then `DEBATE COMMENTS` … `END OF DEBATE` (yours — create the
exchange there). Move each comment verbatim into `DEBATE COMMENTS` keeping its `BDR-NN` id,
severity, and all five fields (`ISSUE`/`AUTHORITY`/`WANTED`/`REVERSIBILITY`/`ESCALATE`). IDs are
permanent.

### Processing each comment (work Blocking first)
1. **Verify before evaluating.** Comments will cite `frame_idx` semantics, `range_energy_by_bin`
   behavior, lock provenance, or specific NPZ/JSON values. Re-check yourself against the live
   files/code — don't trust or dismiss from memory.
2. **Evaluate on the merits.** Is it correct? Does it apply to this diagnostic's scope (not the
   tracker that might follow it)? Does acting on it conflict with CLAUDE.md §4, a frozen document,
   or a HANDOFF-documented gotcha? Does it improve the measurement's correctness/safety, or is it
   scope-creep toward building the tracker itself?
3. **AGREE:** revise `plans/bin_drift_diagnostic.md`, move the comment to `DEBATE COMMENTS` with a
   response noting exactly what changed, then propagate (below).
4. **DISAGREE:** cut the comment verbatim into `DEBATE COMMENTS` and attach a rebuttal grounded in
   something checkable (a line of `src/warmup_select.py` or `src/radar_io.py`, an NPZ/JSON value, a
   CLAUDE.md rule, a HANDOFF/HISTORY fact). End with `STATUS: awaiting Codex`.
5. **PARTIAL:** apply what you accept, state clearly what you did not and why.

Nothing is silently dropped.

### Escalate rather than decide
Mark and stop (do not decide) on:
- **Frozen content**: if a finding turns on changing a pre-registered spec or frozen comparator,
  `STATUS: ESCALATED — frozen content`.
- **Ethics / human-subjects scope**: `STATUS: ESCALATED — user/ethics board decision`.
- **A genuine design choice that is the user's** (an interpretation-gate threshold, which replay
  generation is canonical for the lock comparison): lay out the options in
  `plans/bin_drift_diagnostic.md` and mark `STATUS: ESCALATED — requires user decision`.
- **Anything requiring new data capture or touching `data/raw/`**: `STATUS: ESCALATED — requires
  user decision` — no new data is authorized without the user's sign-off.

### Propagating a fix
A finding that changes CODE behavior needs more than a plan edit:
1. Apply the code fix (`scripts/diagnose_bin_drift.py` and/or
   `scripts/diagnose_bin_drift_config.yaml`), add/update tests in
   `tests/test_diagnose_bin_drift.py` that would have caught the defect.
2. Update `plans/bin_drift_diagnostic.md` to match — every section that references the changed
   behavior (Inputs table, Computation, Outputs, Files, Verification), not just the one Codex
   pointed at.
3. Run `conda run -n radar-vitals python -m pytest tests/test_diagnose_bin_drift.py -q`, then the
   full suite. Both must pass before moving to the next finding.
4. **After the whole batch for this round is applied and tested:** commit (the diagnostic's own
   `provenance.require_clean_tree` gate blocks a citable run otherwise), then re-run on all 4 real
   captures — `python -X utf8 scripts/diagnose_bin_drift.py --config scripts/live_demo_config.yaml
   --diagnostic-config scripts/diagnose_bin_drift_config.yaml --captures <4 dirs, see HANDOFF §6
   for paths> --replays <3 matched-generation dirs, see HANDOFF> --out results/diagnose/bin_drift`
   — and spot-check the changed/new `summary.json` fields against the real output before trusting
   them. Append a `HISTORY.md` entry and rewrite `HANDOFF.md` (new `run_id`, new commit hash, any
   new evidence the round's fixes surfaced), then commit and push both together with the code.
   This has happened after every round so far (rounds 4 and 5) — it is the expected pattern, not
   an exception.

If a comment reveals a defect in `HANDOFF.md`'s own claims that is NOT a consequence of a fix you
just made, do not silently edit it — surface it to the user as a separate item.

### The debate loop
On each pass, re-read `DEBATE COMMENTS` from disk (never from cache — Codex edits the same file)
and handle items where `STATUS` shows Codex responded. Convinced → apply, propagate,
`STATUS: RESOLVED — applied after round N`, delete from `DEBATE COMMENTS`.
`STATUS: Codex conceded — Claude Code to close` → delete, record as withdrawn. Not convinced → new
response, increment the round; each response must add something new. **Hard cap: 3 responses from
you per comment**, then `STATUS: ESCALATED — 3 rounds exhausted, awaiting user decision` and leave
it.

New comments (including follow-ups referencing a resolved id) go through the normal flow under
their own new id.

### Polling
Codex re-reads on its own cadence. Work in reasonably sized batches and save as you go so the file
is always consistent when polled. After a pass, wait 180 seconds, then re-read
`plans/bin_drift_diagnostic_cross_review.md` from disk. Make targeted edits confined to
`plans/bin_drift_diagnostic.md` and `DEBATE COMMENTS`. Loop until `COMMENTS OF CODEX` contains the
exact string `NO MORE COMMENTS` **and** every `DEBATE COMMENTS` item is resolved/conceded/
escalated. If nothing changes for 10 consecutive polls, stop and report.

### When you're done (per round, not just at final closure)
`plans/bin_drift_diagnostic.md` should read cleanly and be internally consistent with the shipped
code, with all accepted fixes integrated, propagated, tested, and re-run on real data;
`COMMENTS OF CODEX` reset to await the next round (or holding `NO MORE COMMENTS`); only escalated
items left in `DEBATE COMMENTS`. Summarise in chat: comments applied by severity; comments
disputed and how each resolved; every escalated item with the decision needed and your
recommendation; anything the diagnostic still leaves risky; confirmation the fix was committed,
pushed, and re-verified on all 4 real captures. **Do not treat `NO MORE COMMENTS` as a trigger to
build something new** — implementation is already done. A closing `NO MORE COMMENTS` with no open
escalations means: stop touching the diagnostic, and hand the evidence
(`results/diagnose/bin_drift/<run_id>/`) to the user for the actual go/no-go decision on the
5-bin relock tracker — that decision is not part of this loop.
