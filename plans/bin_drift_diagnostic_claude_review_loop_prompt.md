An independent reviewer (Codex) is reviewing `plans/bin_drift_diagnostic.md` — a proposed
**read-only** diagnostic (`scripts/diagnose_bin_drift.py`) that measures whether the dominant
radar reflector drifts across range bins during a session, using the 4 existing captures. Its
feedback appears in a section starting `COMMENTS OF CODEX` and ending `END OF COMMENTS` inside
`plans/bin_drift_diagnostic_cross_review.md`. Process every comment.

This is a **plan review before implementation** (CLAUDE.md §6). The diagnostic's verdict decides
whether a 5-bin relock tracker is worth building — a similar feature was already built once
(`vital_signs_v9` relocking, HISTORY.md 2026-07-02) and reverted 2026-07-09 as "not worth its
complexity/risk for now," a call made without this measurement. A flawed diagnostic either sends
the project chasing a tracker for drift that isn't real, or wrongly rules out the leading
candidate explanation for massimo1's `gate_not_run = 66` (52% of that session's dead windows).

### Hard constraints
- **Do not implement code yet.** This loop reviews the PLAN only. Do not write
  `scripts/diagnose_bin_drift.py` or its test until the loop closes.
- Your only writable files are `plans/bin_drift_diagnostic.md` and the `DEBATE COMMENTS` section of
  `plans/bin_drift_diagnostic_cross_review.md`. Do not edit Codex's comment text. Do not edit
  `HANDOFF.md`, `HISTORY.md`, `notes/*_prespec.md`, or any file outside this review's scope.
- **No tuning to the reference** (CLAUDE.md §4): every accepted fix must be justified from radar
  signal properties or measurement correctness — never from anything that would let Masimo
  agreement influence bin selection, even indirectly. If a comment (or your own change) would do
  that, reject it and say so.
- Verification is read-only: reading files, `git log`/`git show`, running the test suite
  (`conda run -n radar-vitals python -m pytest tests/ -q`) if a claim depends on suite state,
  inspecting NPZ/JSON evidence directly. Do NOT run capture, replay, hardware, or any script that
  mutates tracked outputs.

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
After each applied change, check and update every other part of `plans/bin_drift_diagnostic.md`
that references it — the Inputs table, the Computation section, the Outputs section, the Files
table, the Interpretation gates, and the Verification steps. Keep it internally consistent (e.g. if
you change which replay generation is canonical, update every session row in the Inputs table). If
a comment reveals a defect in `HANDOFF.md`'s own claims (not just this plan), do **not** silently
edit `HANDOFF.md` — surface it to the user as a separate item.

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

### When you're done
`plans/bin_drift_diagnostic.md` should read cleanly and be internally consistent, with all accepted
fixes integrated and propagated; `COMMENTS OF CODEX` holding only the closing note; only escalated
items left in `DEBATE COMMENTS`. Then summarise in chat: comments applied by severity; comments
disputed and how each resolved; every escalated item with the decision needed and your
recommendation; anything the plan still leaves risky; and a proposed `HISTORY.md` entry text (for
the user to approve — do not append it yourself). Then, once the loop is closed and no escalations
block it, proceed to implement `scripts/diagnose_bin_drift.py` and its test from the reviewed plan
— or stop and ask if an escalation must be resolved first.
