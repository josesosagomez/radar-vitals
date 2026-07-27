<!--
HOW TO USE THIS TEMPLATE (delete this block before use)

This is YOUR (Claude Code's) side of the loop — the counterpart to
plans/codex_review_prompt_template.md. Fill every {{PLACEHOLDER}} with the SAME
values used in the Codex prompt for this review, so both sides agree on file
paths and the ID prefix. Then run this as your own instructions for the loop.

Use this loop only after the paired Codex prompt has actually been sent and
{{REVIEW_FILE}} exists with a COMMENTS OF CODEX / DEBATE COMMENTS skeleton
(see the Codex template's setup block if you need to create it).
-->

An independent reviewer (Codex) is reviewing {{REVIEWED_ARTIFACT — e.g. "`{{PLAN_FILE}}` — <one-line
summary>" or "the diff `git diff {{BASE_REF}}..{{HEAD_REF}}`"}}. Its feedback appears in a section
starting `COMMENTS OF CODEX` and ending `END OF COMMENTS` inside `{{REVIEW_FILE}}`. Process every
comment.

This is a **{{REVIEW_KIND: plan review before implementation | code review}}** (CLAUDE.md §6).
{{WHY_IT_MATTERS — same one-paragraph stakes statement used in the paired Codex prompt, so both
reviewers are weighting severity the same way.}}

### Hard constraints
- {{IMPLEMENTATION_BOUNDARY — e.g. "Do not implement code yet; this loop reviews the PLAN only" /
  "The diff under review is already merged; only fix what the review agrees needs fixing."}}
- Your only writable files are {{PLAN_FILE}} (if this is a plan review) and the `DEBATE COMMENTS`
  section of `{{REVIEW_FILE}}`. Do not edit Codex's comment text. Do not edit frozen specs,
  `HISTORY.md`, `HANDOFF.md`, or any file outside this review's declared scope.
- **No tuning to the reference** (CLAUDE.md §4): every accepted fix must be justified from signal
  structure, DSP correctness, or a cited rule — never from making an output match the Masimo
  reference. If a comment (or your own change) would tune to the reference, reject it and say so.
- Verification is read-only unless the paired Codex prompt's "Evidence you may use" section says
  otherwise: reading files, `git log`/`git show`, running the test suite
  (`conda run -n radar-vitals python -m pytest tests/ -q`) if a claim depends on suite state, reading
  logged NPZ/CSV evidence. Do NOT run capture, replay, hardware, or scripts that mutate tracked
  outputs unless explicitly permitted.

### Before you start
Re-read `CLAUDE.md`, then {{BACKGROUND_DOCS — same list as the Codex prompt}}, then
{{REVIEWED_ARTIFACT}}, then the code/data it touches. Confirm you're reading the current state of
each — do not rely on what you remember from writing it.

### Document layout
`{{REVIEW_FILE}}` has the brief, then `COMMENTS OF CODEX` … `END OF COMMENTS` (Codex's inbox), then
`DEBATE COMMENTS` … `END OF DEBATE` (yours — create the exchange there if it doesn't exist yet).
Move each comment verbatim into `DEBATE COMMENTS`, keeping its `{{ID_PREFIX}}-NN` id, severity, and
all five fields (`ISSUE`/`AUTHORITY`/`WANTED`/`REVERSIBILITY`/`ESCALATE`). IDs are permanent.

### Processing each comment (work Blocking first)
1. **Verify before evaluating.** Comments will cite specific files, line numbers, data, or a rule.
   Re-check yourself — read the code/array/doc — rather than trusting or dismissing from memory.
2. **Evaluate on the merits.** Is it correct? Does it apply to this review's scope (not a different
   milestone)? Does acting on it conflict with CLAUDE.md §4, a frozen document, or this artifact's
   own stated invariants? Does it improve correctness/safety, or is it scope-creep?
3. **AGREE:** revise {{REVIEWED_ARTIFACT}}, move the comment to `DEBATE COMMENTS` with a response
   noting exactly what changed, then propagate (below).
4. **DISAGREE:** cut the comment verbatim into `DEBATE COMMENTS` and attach a rebuttal grounded in
   something checkable (a line of code, a data value, a CLAUDE.md rule, a frozen spec). End with
   `STATUS: awaiting Codex`.
5. **PARTIAL:** apply what you accept, state clearly what you did not and why.

Nothing is silently dropped.

### Escalate rather than decide
Mark and stop (do not decide) on:
- **Frozen content**: if a finding turns on changing a pre-registered spec or frozen comparator,
  `STATUS: ESCALATED — frozen content`.
- **Ethics / human-subjects scope**: `STATUS: ESCALATED — user/ethics board decision`.
- **A genuine design choice that is the user's** (a threshold, a band, a study-design parameter):
  lay out the options in {{REVIEWED_ARTIFACT}} and mark `STATUS: ESCALATED — requires user
  decision`.
- {{ADDITIONAL_ESCALATION_TRIGGERS — same list as the Codex prompt}}

### Propagating a fix
After each applied change, check and update every other part of {{REVIEWED_ARTIFACT}} that
references it (cross-sections, numbers, file lists, "done when" criteria) — keep it internally
consistent. If a comment reveals a defect in a document this artifact derives from (e.g. the
whole-project plan), do **not** silently edit that base document — surface it to the user as a
separate item.

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
is always consistent when polled. After a pass, wait `{{POLL_INTERVAL_S — default 180}}` seconds,
then re-read `{{REVIEW_FILE}}` from disk. Make targeted edits confined to
{{REVIEWED_ARTIFACT}} and `DEBATE COMMENTS`. Loop until `COMMENTS OF CODEX` contains the exact
string `NO MORE COMMENTS` **and** every `DEBATE COMMENTS` item is resolved/conceded/escalated. If
nothing changes for `{{STALL_LIMIT — default 10}}` consecutive polls, stop and report.

### When you're done
{{REVIEWED_ARTIFACT}} should read cleanly and be internally consistent, with all accepted fixes
integrated and propagated; `COMMENTS OF CODEX` holding only the closing note; only escalated items
left in `DEBATE COMMENTS`. Then summarise in chat: comments applied by severity; comments disputed
and how each resolved; every escalated item with the decision needed and your recommendation;
anything the artifact still leaves risky; and a proposed `HISTORY.md` entry text (for the user to
approve — do not append it yourself). {{POST_CLOSE_ACTION — e.g. "Then, once the loop is closed and
no escalations block it, proceed to implement from the reviewed plan" or "Then stop; no further
action is implied by this template."}} — or stop and ask if an escalation must be resolved first.
