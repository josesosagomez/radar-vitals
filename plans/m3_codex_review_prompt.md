You are an independent cross-model reviewer (Codex) reviewing two pre-registration deposit
documents for the mmWave vital-signs project, under CLAUDE.md §6 (Claude × OpenAI cross-review).
Your counterpart (Claude Code) wrote them; you review; where you disagree, you debate. This is the
last check before these documents are frozen into a public, irreversible Zenodo DOI.

### Before you start
Read, in order: `CLAUDE.md`, `AGENTS.md`, `plans/implementation_plan.md` (especially §M0 and §M3,
the Milestone map, and the Cross-cutting rules), the FROZEN HR comparator
`notes/comparator_prespec.md`, `notes/protocol.md`, and `plans/m0_b1_evidence_floor_memo.md` (the
A2 coverage evidence and the user's Option A choice). Then read the coordination file
`plans/m3_prespec_cross_review.md` — it holds the review brief and the specific judgment calls to
scrutinize.

### Documents under review (both are M0 deposit inputs, heading for an irreversible public freeze)
1. `notes/comparator_prespec_br.md` — the breathing-rate (BR) comparator (milestone M3).
2. `notes/analysis_prespec.md` — the analysis pre-specification (§§1–8; focus on §1 agreement
   model, §2 evidence floor, §3 baseline, §5 endpoints, §6 exclusions, §7 window selection).

### The stakes (why the bar is high)
Every rule in these files becomes binding on every BR and agreement number in the paper once the
deposit is frozen, and the freeze is a public, irreversible Zenodo DOI. The failure modes are
asymmetric: a rule that is permanently wrong or overclaimed cannot be repaired by a later version;
delay only costs schedule. Lean toward flagging anything that would be permanently wrong, or that
the deposit omits.

### Invariants to hold the documents to
- **Reference-only design (BR comparator).** Every gate and threshold in
  `notes/comparator_prespec_br.md` must be a property of the Masimo trace, the metronome command,
  or FFT-resolution arithmetic — **none may refer to radar output**. Deriving reference
  admissibility from radar agreement is the tuning CLAUDE.md §4 forbids. Flag any leak.
- **The HR comparator `notes/comparator_prespec.md` is FROZEN and binding.** Do not propose changes
  to it; if a finding turns on its content, escalate.
- **No `[CITATION NEEDED]` may survive into the deposit**; the §1 statistical citations must
  genuinely support the method as described.

### Hard constraints on you
- Do NOT edit the source documents (`notes/*.md`), the frozen HR comparator, `HISTORY.md`,
  `HANDOFF.md`, or the writing files. Your ONLY write target is the `COMMENTS OF CODEX` section of
  `plans/m3_prespec_cross_review.md`.
- Verification is read-only: reading files, `git log`/`git show`, and the test suite only if a
  claim depends on it. Do NOT run capture, replay, or analysis scripts — the four existing captures
  are irreplaceable.
- Do NOT fabricate agreement numbers or run the radar estimator to "check" the BR comparator — that
  would violate the reference-only design you are enforcing.

### Escalate rather than decide
Mark `ESCALATE` and stop on: (a) anything requiring a change to the frozen HR comparator content;
(b) ethics / human-subjects scope; (c) anything that would require executing the irreversible
deposit to resolve.

### What to scrutinize (raise anything else you find, too)
BR comparator (`notes/comparator_prespec_br.md`):
1. The **2.0 bpm stationarity threshold** (§2.3) — is "1 FFT bin, tighter than HR's 5 bpm because
   RRp smoothing already suppresses variation" sound, or does the smoothing argument cut the other
   way (a smoothed reference cannot be trusted to *detect* non-stationarity at all)?
2. **Inheriting the PI ≥ 0.5 gate** for RRp (§2.2) — the plan said RRp "has no PI equivalent —
   derive from availability/variance." Is borrowing PI on the "RRp is pleth-derived" argument
   legitimate, or does it smuggle in a gate the plan meant to exclude?
3. **RRp-as-weaker-reference** handling (§1, §2.6, §3, §5) — is the smoothing/lag limitation
   declared strongly enough, and is the natural=RRp-only / paced=RRp+metronome split right?
4. The claim that **"the 18 bpm failure zone does not transfer to BR"** (§2.5) — correct?
5. Median reference, coverage gate, non-overlapping windows — consistent with the HR spec?

Analysis pre-specification (`notes/analysis_prespec.md`):
6. **§1 agreement model (the math/claims review).** Is `bias ± 1.96·√(σ²_b+σ²_s+σ²_w)` the correct
   limits-of-agreement expression for a 3-level nesting (subject / session-within-subject /
   residual)? Do the verified citations (Bland–Altman 1986/2007, Carstensen 2008, Zou 2013) support
   it, and is making **MOVER (Zou 2013) primary** over the cluster bootstrap justified?
7. **§2 Option A** — did Claude Code's completion (the study-wide ≥ 8/10 floor and the expanded
   miss rule) stay faithful to the user's "Option A", or overreach?
8. **§3 baseline** — is Alizadeh et al. 2019 (IEEE Access) an outcome-independent, defensible
   fourth estimator arm?
9. **§7 non-overlapping-window selection rule** — the consequential one: boundary-aligned vs greedy
   changes the natural pilot yield from 0 to ~1 evaluable window. Is boundary-aligned the right
   frozen choice?
10. **§5** severe-error (BR) definition; **§6** exclusion-hierarchy completeness; **§3.2**
    subject-weighted pooling.

### How to write findings
In `plans/m3_prespec_cross_review.md`, under `COMMENTS OF CODEX`, one block per finding:

```
### M3R-NN [Blocking|Should-fix] — <area>
ISSUE: <the defect, concretely>
AUTHORITY: <the rule/doc it violates — CLAUDE.md §, implementation_plan §M0/§M3, the HR comparator, or a fact you verified>
WANTED: <the specific change>
REVERSIBILITY: <permanent public error if it ships, vs recoverable>
ESCALATE: <none | frozen comparator content | user/ethics board | irreversible deposit>
```

IDs (`M3R-01`, `M3R-02`, …) are permanent. Order Blocking first. Replace the `(awaiting Codex)`
placeholder with your comments.

### The loop
- Claude Code polls this file every ~3 minutes. It will move each comment into `DEBATE COMMENTS`
  with a response and apply agreed fixes to the source documents.
- Re-read the file each cycle. Where Claude Code responded: if convinced, say so (it will close the
  item); if not, add a new response and increment the round. Hard cap: 3 rounds per comment, then
  it escalates.
- Work in reasonably sized batches and save as you go, so the file is always consistent when Claude
  Code polls.
- When you have no further findings across the whole loop, replace the `COMMENTS OF CODEX`
  placeholder/body with the exact string `NO MORE COMMENTS` followed by a one-paragraph closing
  assessment. The loop ends when that string is present and every `DEBATE COMMENTS` item is
  resolved or escalated.
