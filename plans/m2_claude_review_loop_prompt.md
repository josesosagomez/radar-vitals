An independent reviewer (Codex) is reviewing `plans/m2_respiration_fix.md` — the M2
respiration-collapse root-cause and fix plan. Its feedback appears in a section starting
`COMMENTS OF CODEX` and ending `END OF COMMENTS` inside `plans/m2_fix_cross_review.md`. Process
every comment.

This is a **plan review before implementation** (CLAUDE.md §5.2/§6 — the fix touches respiration
peak-picking). The plan is recoverable by editing, but the fix it produces changes the estimator
that feeds the **co-equal BR goal** and also perturbs the HR path (via `f_r` → ECA), so a wrong or
under-specified fix is costly. Lean toward catching correctness and safety issues now; push back on
scope-creep that turns the plan into a treatise.

### Hard constraints
- **Do not implement code yet.** This loop reviews the PLAN. Do not edit `src/respiration.py`,
  `tests/`, or any source; implementation happens only after the loop closes.
- Your only writable files are `plans/m2_respiration_fix.md` (the plan) and the `DEBATE COMMENTS`
  section of `plans/m2_fix_cross_review.md`. Do not edit Codex's comment text; do not edit
  `notes/comparator_prespec.md`, `notes/comparator_prespec_br.md`, `notes/protocol.md`,
  `notes/analysis_prespec.md`, `HISTORY.md`, `HANDOFF.md`, or the writing files.
- **No tuning to the reference** (CLAUDE.md §4): every fix decision must be justified from spectral
  structure or DSP correctness, never from making BR match `rr_bpm`/metronome. If a comment (or your
  own change) would tune to the reference, reject it and say so.
- Verification is read-only: reading files, `git log`/`git show`, `conda run -n radar-vitals python
  -m pytest tests/ -q` if a claim depends on suite state, and reading the checkpointed NPZs in
  `results/live_demo/*_replay_unknown/live_intermediates.npz` (the evidence). **Do not run capture,
  replay, or analysis scripts.** Reproduce claims about the estimator from `src/respiration.py` and
  the NPZ arrays, not by re-running the pipeline.

### Before you start
Re-read `CLAUDE.md`, then `plans/implementation_plan.md` §M2, then the plan under review
`plans/m2_respiration_fix.md`, then the code it changes — `src/respiration.py` (`fft_estimate_rr`,
`ha_estimate_rr`, `fuse_estimates`) — and how respiration is invoked and how `f_r` feeds ECA
(`scripts/live_demo.py:440–494`). Note the frozen BR comparator `notes/comparator_prespec_br.md`
(binding; the BR outcome is scored against it) and the frozen HR comparator
`notes/comparator_prespec.md`.

### Document layout
`plans/m2_fix_cross_review.md` has the brief, then `COMMENTS OF CODEX` … `END OF COMMENTS` (Codex's
inbox), then `DEBATE COMMENTS` … `END OF DEBATE` (yours — create the exchange there). Move each
comment verbatim into `DEBATE COMMENTS` keeping the `M2R-NN` id, severity, and all five fields
(`ISSUE`/`AUTHORITY`/`WANTED`/`REVERSIBILITY`/`ESCALATE`). Ids are permanent.

### Processing each comment (work Blocking first)
1. **Verify before evaluating.** Comments will cite `src/respiration.py`, the NPZ evidence, the
   comparator, or line numbers. Re-check yourself — read the code/array — rather than trusting or
   dismissing from memory. The root-cause claims (two mechanisms; the "6 bpm bin is not a local
   maximum" unifier; the fusion medium-path selecting a disagreeing HA value) are all checkable
   against `src/respiration.py` and the NPZs; confirm any that a comment disputes.
2. **Evaluate on the merits.** Is it correct? Does it apply to the M2 fix (not to M4/M8)? Does
   acting on it conflict with CLAUDE.md §4 (no tuning), a frozen comparator, or the plan's own
   invariants? Does it improve the fix's correctness/safety, or is it scope-creep?
3. **AGREE:** revise `plans/m2_respiration_fix.md`, move the comment to `DEBATE COMMENTS` with a
   response noting exactly what changed, then propagate (below).
4. **DISAGREE:** cut the comment verbatim into `DEBATE COMMENTS` and attach a rebuttal grounded in
   something checkable (a line of `src/respiration.py`, an NPZ array value, a CLAUDE.md rule, the
   comparator). End with `STATUS: awaiting Codex`.
5. **PARTIAL:** apply what you accept, state what you did not and why.

Nothing is silently dropped.

### Escalate rather than fix
Mark and stop (do not decide) on:
- **Frozen-comparator content** (`comparator_prespec.md` / `comparator_prespec_br.md`): if a finding
  turns on changing either, `STATUS: ESCALATED — frozen comparator content`.
- **Ethics / human-subjects scope**: `STATUS: ESCALATED — user/ethics board decision`.
- **A genuine design choice that is the user's**, e.g. changing the `[0.10, 0.50] Hz` band or a
  config threshold (the plan deliberately keeps the band; a comment demanding a band change is a
  user decision, not a review fix): lay out the options in the plan and mark
  `STATUS: ESCALATED — requires user decision`.

### Propagating a fix
After each applied change, check and update every part of `plans/m2_respiration_fix.md` that
references it: the root-cause section (§1), the fix (§2.1/§2.2/§2.3), the tests (§3), the
verification/"done when" (§4), and the files list (§6). Keep numbers consistent (if you correct a
figure, fix all occurrences). If a comment reveals a real defect in `plans/implementation_plan.md`
§M2 itself, do **not** edit the base plan silently — surface it to the user as a separate item.

### The debate loop
On each pass, re-read `DEBATE COMMENTS` from disk and handle items where `STATUS` shows Codex
responded. Convinced → apply, propagate, `STATUS: RESOLVED — applied after round N`, delete from
`DEBATE COMMENTS`. `STATUS: Codex conceded — Claude Code to close` → delete, record as withdrawn.
Not convinced → new response, increment the round; each must add something new. **Hard cap: 3
responses from you per comment**, then `STATUS: ESCALATED — 3 rounds exhausted, awaiting user
decision` and leave it.

New comments (including follow-ups referencing a resolved id) go through the normal flow under their
new id.

### Polling
Codex re-reads every ~3 minutes. Work in reasonably sized batches and save as you go so the file is
always consistent. After a pass, wait 3 minutes (`sleep 180`), then re-read
`plans/m2_fix_cross_review.md` from disk (never from cache — Codex edits the same file). Make
targeted edits confined to the plan and `DEBATE COMMENTS`. Loop until `COMMENTS OF CODEX` contains
the exact string `NO MORE COMMENTS` **and** every `DEBATE COMMENTS` item is resolved/conceded/
escalated. If nothing changes for 10 consecutive polls (~30 min), stop and report.

### When you're done
`plans/m2_respiration_fix.md` should read cleanly and be internally consistent, with all accepted
fixes integrated and propagated; `COMMENTS OF CODEX` holding only the closing note; only escalated
items left in `DEBATE COMMENTS`. Then summarise in chat: comments applied by severity; comments
disputed and how each resolved; every escalated item with the decision needed and your
recommendation; anything the fix plan still leaves risky; and a proposed `HISTORY.md` entry text
(for the user to approve — do not append it yourself). **Then, once the loop is closed and no
escalations block it, proceed to implement the fix from the reviewed plan** (edit `src/respiration.py`,
add the tests, add `scripts/diagnose_respiration_collapse.py`, reprocess the four captures, run the
suite) — or stop and ask if an escalation must be resolved first.
