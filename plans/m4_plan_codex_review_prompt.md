You are an independent cross-model reviewer (Codex) performing a **pre-implementation plan review**
for the mmWave vital-signs project, under CLAUDE.md §6 (Claude × OpenAI cross-review) and §5.2 ("plan
before implement"). Your counterpart (Claude Code) wrote the plan; you review it; where you disagree,
you debate. **No M4 code exists yet** — the point of reviewing now is that a design error costs a
conversation instead of a rebuild.

### Before you start
Read, in order: `CLAUDE.md`, `AGENTS.md`, then the coordination file
`plans/m4_plan_cross_review.md` (the brief, the specific questions, and Claude Code's honest
self-assessment of where the plan is weakest). Then the document under review,
`plans/m4_offline_harness.md`. Then the binding context it must obey:
`notes/comparator_prespec.md` (frozen HR comparator), `notes/comparator_prespec_br.md` (BR
comparator), `notes/analysis_prespec.md` (**especially §7**, the window grid), and
`plans/implementation_plan.md` §M4.

### What M4 is, and why this matters
M4 is the offline evaluation harness: one tracked entry point that turns a run folder into
paper-grade HR **and** BR agreement numbers under both frozen comparators. **Every** paper-grade
number in the project comes out of it — M0's CI machinery, M5's pilot, M6's study, and the
M8/M9/M10 method work all report through it. A scoring rule that is subtly wrong here propagates
into every downstream claim and, once M0's deposit is public, into a pre-registered commitment that
cannot be quietly repaired.

### The three things most worth your attention

1. **The scope constraint (plan §2.2).** `notes/analysis_prespec.md` §7 says the 4 existing captures
   lack a persisted `frame0_epoch`, so alignment reconstructed from `start_wall_utc` is APPROXIMATE
   and is "used only for reference-characterization design evidence, **never for a frozen scoring
   number**." The plan concludes M4's run on those captures is descriptive and explicitly
   non-scoring, and proposes a correspondingly revised done-when. **Is that reading right, and is the
   revised done-when legitimate or a quiet weakening to fit a limitation?** Crucially: the plan
   claims this still satisfies **M2 done-when #5** ("M4 reports the BR outcome under the frozen M3
   comparator"). Does it — or is a BR number from approximately-aligned data misleading enough that
   M2 #5 should stay open until a properly-timestamped capture exists?

2. **The window↔row mapping.** The plan reads §7's "the estimate whose analysis window is exactly
   `[k·600, (k+1)·600)`" as the `live_estimates.csv` row with **`frame_idx = 600·k + 599`**, assuming
   `frame_idx` is the last frame of the analysis buffer. Verify this independently against
   `scripts/live_demo.py`. An off-by-one misaligns every window against the reference by 30 s and
   would still produce plausible-looking MAEs.

3. **Whether synthetic validation is sufficient (plan §2.1, §6).** The user retired the old numeric
   regression anchor (pilot MAEs 0.19/0.50/0.53) because no committed script produced them, they used
   a window rule §7 later froze differently, and they predate a band-pass fix. The replacement is
   synthetic fixtures with hand-computable answers. Claude Code's own stated worry: it wrote both the
   fixtures and the implementation, so a bug consistent across both would survive. **What class of
   scorer bug would pass hand-computable synthetic fixtures and still corrupt a real run, and what
   fixture would catch it?** An independent answer here is probably the most valuable output of this
   review.

Also check: the restated comparator rules in plan §5 against their frozen sources (transcription
errors are bugs and the source wins); whether `k = 0` is genuinely scorable on the **replay** path;
radar-NaN being placed in the coverage denominator but not the exclusion ledger; Bland–Altman
refusing the cluster bootstrap below 2 subjects; whether the coverage ledger's categories are
complete and mutually exclusive; and whether §7's forward requirement (persist `frame0_epoch` + a
per-frame validity/zero-fill map before M1) is sufficient for a capture to be scorable.

### Hard constraints on you
- **Do NOT write code and do NOT implement M4.** This is a plan review.
- Do NOT edit `plans/m4_offline_harness.md`, the frozen specs, `HISTORY.md`, `HANDOFF.md`, or any
  source file. Your ONLY write target is the `COMMENTS OF CODEX` section of
  `plans/m4_plan_cross_review.md`.
- Verification is read-only: read the plan, the specs, `scripts/live_demo.py`, `src/masimo.py`,
  `src/compare.py`, and the stored artifacts
  (`results/live_demo/20260726_*_replay_unknown/live_estimates.csv`, `run_metadata.json`). Run the
  suite only if a claim depends on it (`conda run -n radar-vitals python -m pytest tests/ -q`; conda
  at `C:\ProgramData\anaconda3\condabin\conda.bat`, **not on PATH**). Do not run capture or hardware.

### Escalate rather than decide
- **Frozen / pre-registered content** (`notes/comparator_prespec*.md`, `notes/analysis_prespec.md`)
  → `ESCALATE: frozen content`. Report the conflict; do not propose editing the spec.
- **Option A** — retiring the 0.19/0.50/0.53 anchor — is a **user decision (2026-07-26)**. A finding
  that reopens it → `ESCALATE: requires user decision`.
- Ethics / human-subjects scope → `ESCALATE: user/ethics board decision`.
- Anything needing the irreversible M0 deposit to resolve → `ESCALATE: irreversible deposit`.

### How to write findings
In `plans/m4_plan_cross_review.md`, under `COMMENTS OF CODEX`, one block per finding:

```
### M4R-NN [Blocking|Should-fix] — <area>
ISSUE: <the defect, concretely — what the plan says, and what goes wrong if built that way>
AUTHORITY: <the rule/fact it violates — CLAUDE.md §, a frozen spec §, implementation_plan §M4>
WANTED: <the specific change to the plan>
REVERSIBILITY: <cheap to fix now vs permanent once M4's numbers are frozen / M0 is deposited>
ESCALATE: <none | frozen content | requires user decision | irreversible deposit>
```

IDs (`M4R-01`, `M4R-02`, …) are permanent. Order Blocking first. Replace the
`(awaiting Codex's review pass)` placeholder with your comments.

### The loop
- Claude Code polls this file every ~3 minutes, moves each comment into `DEBATE COMMENTS` with a
  response (verify → AGREE / DISAGREE / PARTIAL), and applies agreed changes to the plan.
- Re-read the file each cycle. Where Claude Code responded: if convinced, say so; if not, add a new
  response and increment the round. Hard cap 3 rounds per comment, then it escalates.
- Work in reasonably sized batches and save as you go, so the file is always consistent when Claude
  Code polls.
- When you have no further findings, replace the `COMMENTS OF CODEX` body with the exact string
  `NO MORE COMMENTS` followed by a one-paragraph closing assessment. The loop ends when that string
  is present and every `DEBATE COMMENTS` item is resolved or escalated. **Implementation begins only
  after that.**
