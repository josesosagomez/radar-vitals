# Cross-model review — M4 offline evaluation harness PLAN (pre-implementation)

> **Review coordination file (CLAUDE.md §6, §5.2 "plan before implement").** `plans/m4_offline_harness.md`
> is a DRAFT and **no M4 code exists yet**. This review happens *before* implementation, so a design
> error costs a conversation rather than a rebuild.
>
> Codex writes findings into `COMMENTS OF CODEX`; Claude Code processes them into `DEBATE COMMENTS`
> and applies agreed changes to the plan. Prepared 2026-07-26.

## Document under review

**`plans/m4_offline_harness.md`** — the build plan for M4, the offline evaluation harness.

Binding context (**not** review targets — these are frozen or user-decided; a finding that would
change them is an escalation, not a fix):

| document | status |
|---|---|
| `notes/comparator_prespec.md` | HR comparator, **pre-registered and binding** |
| `notes/comparator_prespec_br.md` | BR comparator, cross-review COMPLETE, ready for M0 freeze |
| `notes/analysis_prespec.md` | analysis pre-spec — §1 agreement model, §7 window grid |
| `plans/implementation_plan.md` §M4 | scope authority for the milestone |
| `plans/m4_linalg_free_dsp_review.md` | the DSP review that gates M4 — CLOSED 2026-07-26 |

## Why the bar is high

M4 produces **every** paper-grade number in the project. M0's CI machinery, M5's pilot, M6's study
and the M8/M9/M10 method work all report through it. A scoring rule that is subtly wrong here
propagates into every downstream claim, and — once M0's deposit is public — into a pre-registered
commitment that cannot be quietly repaired.

The harness is also the last place a transcription error can be caught cheaply. §5 of the plan
**restates** the frozen comparator rules in implementation terms. If any restatement disagrees with
its source document, the source wins and the plan has a bug.

## What to scrutinise

### A. The scope constraint (plan §2.2) — the most important item
`notes/analysis_prespec.md` §7 states the 4 existing captures lack a persisted `frame0_epoch`, so
alignment reconstructed from `start_wall_utc` is APPROXIMATE and is "used **only** for
reference-characterization design evidence, **never** for a frozen scoring number". The plan
concludes M4's run on these captures is a **descriptive, explicitly non-scoring** output.

1. Is that reading of §7 correct?
2. The plan proposes M4's done-when is met by synthetic validation + a descriptive alignment-flagged
   run + readiness for a properly-timestamped capture. `plans/implementation_plan.md` §M4 instead
   asks for numbers from exactly these captures. **Is the proposed resolution legitimate, or is it
   quietly weakening a done-when to fit a limitation?**
3. **M2 done-when #5** ("M4 reports the BR outcome under the frozen M3 comparator") is claimed to be
   satisfied by that descriptive run. Is it? Or is a BR number from approximately-aligned data
   misleading enough that reporting it does more harm than leaving M2 #5 open?

### B. Correctness of the restated rules (plan §5)
4. **Window↔row mapping.** The plan reads "the estimate whose analysis window is exactly
   `[k·600, (k+1)·600)`" as the `live_estimates.csv` row with **`frame_idx = 600·k + 599`**, assuming
   `frame_idx` is the **last** frame of the analysis buffer. The first CSV row is `frame_idx = 599`,
   which is consistent — but this is load-bearing and deserves an independent check against
   `scripts/live_demo.py`. An off-by-one here silently misaligns every window.
5. Any disagreement between plan §5.1–5.4 and the frozen specs (HR: PI ≥ 0.5, coverage ≥ 80 %,
   stationarity > 5.0 bpm, median reference, 3/5/8 sensitivity. BR: ≥ 24 finite samples,
   stationarity > 2.0 bpm, median, PI **not** a gate, metronome as target-concordance only).
6. **`k = 0` is scored.** The plan carries §7's justification (warmup runs on the `k=0` buffer and is
   applied back via `dsp_override`). Does that hold for the **replay** path as well as live?

### C. Statistics and reporting
7. **Radar-NaN placement.** The plan puts radar-NaN **in the coverage denominator but not the
   exclusion ledger**, arguing that conflating "the reference was unusable" with "the radar declined"
   inflates apparent accuracy. Is that the right reading of both specs?
8. **Bland–Altman on n=1.** The plan makes the cluster bootstrap **refuse** below 2 subjects rather
   than emit a degenerate interval. Correct? And is the constant `μ ± 1.96·SD` point estimate still
   reportable at n=1, or should it also be suppressed?
9. Is the coverage ledger's category set complete and mutually exclusive (does it sum to the window
   total under every combination of gate failures)?

### D. Build order and testability
10. Is the §6 stage order genuinely incremental — can each stage be verified before the next, per
    CLAUDE.md §5.3? Is any stage hiding a dependency on a later one?
11. **Is synthetic validation sufficient to replace the retired numeric anchor?** This is the crux of
    Option A. What class of scorer bug would pass hand-computable synthetic fixtures and still
    corrupt a real run?
12. §7's forward requirement (persist `frame0_epoch` + a per-frame validity/zero-fill map before M1).
    Is that complete, or is something else needed for a capture to be scorable?

### E. Anything else
13. Out-of-scope items (§8) that should be in scope, or vice versa.

## Escalate rather than decide

- **Frozen / pre-registered content** (`notes/comparator_prespec*.md`, `notes/analysis_prespec.md`)
  → `ESCALATE: frozen content`. Report the conflict; do not propose editing the spec.
- **Option A** (the retired 0.19/0.50/0.53 anchor) is a **user decision, 2026-07-26**. A finding that
  reopens it → `ESCALATE: requires user decision`.
- **Ethics / human-subjects scope** → `ESCALATE: user/ethics board decision`.
- Anything needing the irreversible M0 deposit to resolve → `ESCALATE: irreversible deposit`.

## Verification available (read-only)

Read the plan, the frozen specs, `scripts/live_demo.py`, `src/masimo.py`, `src/compare.py`,
`results/live_demo/20260726_*_replay_unknown/live_estimates.csv` and `run_metadata.json`. Run the
suite if a claim depends on it (`conda run -n radar-vitals python -m pytest tests/ -q`; conda at
`C:\ProgramData\anaconda3\condabin\conda.bat`, **not on PATH**). **Do not write code and do not
implement M4** — this is a plan review.

## Protocol

- Findings below as `### M4R-NN [Blocking|Should-fix] — <area>` with
  `ISSUE / AUTHORITY / WANTED / REVERSIBILITY / ESCALATE`. IDs are permanent.
- Claude Code moves each into `DEBATE COMMENTS` with a response (verify → AGREE / DISAGREE /
  PARTIAL) and applies agreed changes to `plans/m4_offline_harness.md`. Hard cap **3 responses per
  comment**, then `STATUS: ESCALATED — 3 rounds exhausted`.
- Loop ends when `COMMENTS OF CODEX` reads `NO MORE COMMENTS` and every debate item is
  resolved/escalated. **Then implementation may begin.**

---

## Author's self-assessment (Claude Code, 2026-07-26) — context, not findings

Where I think this plan is solid, and where I do not. Correct me.

**Most confident.** The restatement of the comparator rules in §5 — those were transcribed directly
from the frozen documents with the section numbers attached, and the yields (6/6/16) were verified
against the actual captures rather than assumed. The build order is a genuine dependency order.

**Least confident, and what I most want attacked:**

1. **§2.2 is a judgement call I made, not a rule I found.** §7 clearly forbids a *frozen scoring
   number* from approximate alignment. It does **not** explicitly say "a descriptive number is fine".
   I inferred that. If that inference is wrong, M2 #5 cannot be closed by M4 at all, and the honest
   move is to leave it open until a properly-timestamped capture exists — which would be a
   significant schedule finding. I would rather be told this now than after building.
2. **The `600·k + 599` mapping is the single highest-consequence assumption in the plan** and I
   verified it only from the first CSV row plus the buffer semantics. An off-by-one misaligns every
   window against the reference by 30 s and would still produce plausible-looking MAEs.
3. **Whether synthetic validation really substitutes for the numeric anchor (question 11).** I argued
   Option A partly because Option B is unachievable, which is a reason to *reject B*, not by itself a
   proof that **A is sufficient**. The gap I cannot close by introspection: a bug that is consistent
   between my fixture and my implementation because I wrote both. An independent statement of what
   fixtures would catch that class of error would be the most valuable thing this review produces.
4. **Radar-NaN placement (question 7)** — defensible, but I am reasoning from the spirit of "report
   coverage alongside accuracy" rather than from an explicit rule in either spec.

---

COMMENTS OF CODEX

(awaiting Codex's review pass)

END OF COMMENTS

DEBATE COMMENTS

(no findings processed yet)

END OF DEBATE
