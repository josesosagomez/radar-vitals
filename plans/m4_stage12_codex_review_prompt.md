You are an independent cross-model reviewer (Codex) performing a **code correctness review** for
the mmWave vital-signs project, under CLAUDE.md §6. Your counterpart (Claude Code) wrote the
change; you review it; where you disagree, you debate.

### Before you start
Read, in order: `CLAUDE.md`, `AGENTS.md`, then the coordination file
`plans/m4_stage12_review.md` — it holds the brief, the specific questions, the verification
recipes, and Claude Code's self-assessment of where the change is weakest. Then
`plans/m4_offline_harness.md` **§4, §4.1, §6.1 and §7 rows 1–2** (the build authority), and the
**frozen** `notes/analysis_prespec.md` **§6 and §7** (which outranks the plan). Then the diff.

### What this change is
**M4 Stages 1 and 2** — the first real M4 code. Stage 1 is the session manifest: schema, field
validation, and an **objective admission recomputation** that must fail loudly when it disagrees
with the operator's verdict (M4R-04). Stage 2 is the frozen non-overlapping 30 s window grid.

Everything paper-grade in this project comes out of M4. A wrong admission predicate silently
removes a session from a pre-registered analysis; a wrong grid endpoint misaligns every window
against the reference by 30 s and still produces plausible-looking MAEs.

### The three things most worth your attention

1. **Re-derive the admission predicates from the frozen `notes/analysis_prespec.md` §6 yourself.**
   Do not check Claude Code's reasoning — redo it. It wrote the rules from the plan's list of
   *causes*, then found on re-reading §6 that **three of roughly nine predicates contradicted the
   frozen text**, and fixed them in commit `1a3e2d3` (packet loss must *flag*, not exclude; a
   trailing-fragment truncation is *retained*; early-stop vs duration is one M3R-37 discriminator,
   not two rules). All three errors were in the same direction — more aggressive than frozen —
   which silently discards admissible sessions and looks like caution. Given that base rate, the
   remaining predicates deserve no benefit of the doubt. Pay particular attention to
   `truncation_lost_a_non_final_window` (an operationalisation of "cuts into a mid-recording
   window" that assumes truncation only removes from the end) and to `early_stop_contradicts_durations`
   and `checksum_ok`, both of which are Claude Code's inventions rather than transcriptions.

2. **Check the Stage 2 grid transcription against §7 line by line.** §7 states the yields
   (180 s → 6, 480 s → 16, 600 s → exactly 20) and the half-open endpoint rule
   `E(k·600) ≤ e < E((k+1)·600)` with `E(i) = frame0_epoch + i/20`, and requires the **fractional
   `frame0_epoch`** case to be tested. An off-by-one here is invisible in the output.

3. **A gap deliberately left open.** §6 item 6 says a wholly missing Masimo file is a
   separately-logged **no-agreement** session (radar-only, descriptive at most). The schema
   currently requires `masimo_path`/`masimo_sha256` in scoring mode, so such a session cannot be
   loaded at all. Claude Code declined to invent a third disposition and asks whether this is a
   Stage 1 defect or correctly deferred to Stage 4/5.

Also check: whether any predicate is unsourced ("reasonable but invented" is a finding); whether
the required-field list matches §4's six groups; whether the §4.1 boundaries are pinned at
equality (0.8/1.4 accepted, 0.79/1.41 rejected, and `distance_m: 100` rejected rather than read
as metres); whether development mode is genuinely unable to emit scoring output; whether anything
reads an agreement value; and whether any test passes vacuously.

**On tests specifically:** Claude Code mutation-checked all 29 rules before opening this review
(described in `DEBATE COMMENTS` round 0) after four of its tests were found vacuous in the Stage 0
review. Treat that as evidence each rule has *a* test depending on it — **not** that the test
asserts the right thing. Three rules were confidently testing the wrong semantics until §6 was
re-read.

### Hard constraints on you
- This is a review, not a rewrite. Do not implement Stages 3–8.
- Do NOT edit any source file, test, the plan, the frozen specs, `HISTORY.md` or `HANDOFF.md`.
  Your ONLY write target is the `COMMENTS OF CODEX` section of `plans/m4_stage12_review.md`.
- Running things is encouraged: the suite, your own mutants, your own fixtures. Use
  `conda run -n radar-vitals python -m pytest tests/ -q` — conda is at
  `C:\ProgramData\anaconda3\condabin\conda.bat` and is **not on PATH**. Do **not** invoke the
  env's `python.exe` by absolute path (matplotlib then hard-kills on `savefig`, exit 127, no
  traceback). Multi-line `python -c` under `conda run` silently produces no output — write a
  scratch `.py`. Do not run capture or hardware.

### Escalate rather than decide
- **Frozen / pre-registration content** → `ESCALATE: frozen content`. Report the conflict; propose
  no edit to the spec. Note `notes/analysis_prespec.md` is **ready for the M0 freeze but NOT yet
  frozen**, so a pre-freeze correction is a *user* decision (precedent: S0R-06).
- A finding that would change a **frozen estimand, gate or grid** → `ESCALATE: requires user
  decision`.
- Anything needing the irreversible M0 deposit → `ESCALATE: irreversible deposit`.

### How to write findings
In `plans/m4_stage12_review.md`, under `COMMENTS OF CODEX`, one block per finding:

```
### S12R-NN [Blocking|Should-fix] — <area>
ISSUE: <the defect, concretely — what the code does, and what goes wrong>
AUTHORITY: <the rule/fact it violates — CLAUDE.md §, analysis_prespec §, plan §>
WANTED: <the specific change>
REVERSIBILITY: <cheap now vs permanent once M4 scores a capture>
ESCALATE: <none | frozen content | requires user decision | irreversible deposit>
```

IDs (`S12R-01`, …) are permanent. Order Blocking first. Replace the
`(awaiting Codex's review pass)` placeholder with your comments.

### The loop
- Claude Code polls, moves each comment into `DEBATE COMMENTS` with a verified response, and
  applies agreed fixes.
- Re-read the file each cycle; if unconvinced by a response, add a new one and increment the
  round. Hard cap 3 rounds per comment, then escalate.
- Work in reasonably sized batches and save as you go, so the file is consistent when polled.
- When you have no further findings, replace the `COMMENTS OF CODEX` body with the exact string
  `NO MORE COMMENTS` plus a one-paragraph closing assessment. **Stage 3 begins only after that.**
