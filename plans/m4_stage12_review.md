# Cross-model review — M4 Stages 1 + 2 (manifest schema/validation; frozen window grid)

> ## STATUS: **OPEN** — awaiting Codex's first pass (2026-07-27)
>
> *Maintenance rule for this block (carried from S0R-20 R2): every volatile number lives in
> exactly ONE place — the tally line and the suite line below. Prose must not restate a count
> or a round number.*
>
> **0 findings so far.** Suite: **1240 passed, 0 failed, 0 xfailed** (132 in the two targeted
> Stage 1/2 files).
>
> Under review: commits **`b5f6b1e`** (Stages 1+2 as first built) and **`1a3e2d3`** (a
> self-found correction of three admission rules that contradicted a frozen document). Read
> them together — `git diff 68179e1..1a3e2d3` is the whole change.

> **Review coordination file (CLAUDE.md §6).** Codex writes findings into `COMMENTS OF CODEX`;
> Claude Code processes them into `DEBATE COMMENTS` and applies fixes. **Stage 3 does not begin
> until this closes.**

## What Stages 1 and 2 are

The first real M4 code. Stage 0 (closed, `plans/m4_stage0_refactor_review.md`) only moved the
DSP into `src/` so M4 could share it.

| File | What |
|---|---|
| `src/m4/manifest.py` | **NEW.** Stage 1 — session manifest schema, field validation, and the objective admission recomputation (plan §4, §4.1, §7 row 1) |
| `src/m4/window_grid.py` | **NEW.** Stage 2 — the frozen non-overlapping 30 s window grid (plan §6.1, transcribed from `notes/analysis_prespec.md` §7) |
| `tests/test_m4_manifest.py` | **NEW.** Stage 1 tests |
| `tests/test_m4_window_grid.py` | **NEW.** Stage 2 tests |

**Done-when** (plan §7):

* Row 1: *"Scoring mode rejects every missing required field with a named error. M4 recomputes
  the objective admission disposition from the primitive fields … and fails loudly if it
  disagrees with the operator-supplied verdict, with a named negative test per rule (M4R-04).
  Development mode is separately labelled and cannot emit scoring output."*
* Row 2: *"Yields 6/6/16/20 by hand; both boundary seconds land in exactly one window under a
  fractional origin."*

## The binding sources, and which wins

`notes/analysis_prespec.md` is **FROZEN** and outranks the plan; the plan says so itself
("Transcribed, not invented. Any disagreement with the source is a bug in this plan and the
source wins"). For these two stages the load-bearing sections are **§6** (exclusion /
disposition hierarchy — the admission predicates) and **§7** (the window grid). `notes/protocol.md`
supplies the 0.8–1.4 m distance range.

## Invariants to hold the change to

- **Transcription, not invention.** Every predicate must trace to §6/§7 or to plan §4/§4.1.
  A rule that is *reasonable* but unsourced is a finding.
- **No silent discard.** An over-aggressive exclusion rule is not a safe default: it removes a
  session from a pre-registered analysis for a reason nobody recorded.
- **Agreement-blind.** Nothing here may read, or depend on, any radar/reference agreement value.
- **Determinism**, and no magic numbers outside the named frozen constants.
- **No vacuous test.** Every rule must have a test that fails when the rule is removed.

## Specific questions to scrutinise (not a limit — raise anything)

### A. Admission predicates vs the frozen §6 — the highest-value area
1. **I found three of my own rules contradicting §6 and fixed them in `1a3e2d3`** (packet loss
   excluded rather than flagged; any truncation excluded rather than only a non-final-window
   cut; early-stop/duration double-counted instead of the single M3R-37 discriminator).
   **Re-derive all of them from §6 yourself** — I got three wrong on the first pass, so the
   remaining ones deserve no benefit of the doubt.
2. **`truncation_lost_a_non_final_window` is the predicate I am least sure of.** §6 item 4 says
   "truncated so that a **non-final** window is incomplete (`mirror_truncated_bytes` cuts into a
   mid-recording window)". I implemented that as *stored complete windows < the complete windows
   the intended duration implies*. Is that the right operationalisation? It assumes truncation
   only removes from the end. If `mirror_truncated_bytes` can mean bytes lost mid-file, the
   predicate is wrong.
3. **`early_stop_contradicts_durations`** is **my invention** — §6 does not name it. I added it
   because `early_stop=True` with `actual >= intended` is self-contradictory. Is inventing an
   exclusion reason defensible here, or should a contradictory manifest be a *validation* error
   rather than an admission verdict?
4. **`checksum_ok` is a new required field I introduced.** §6 says "a stored file checksum
   fails", which is a fact recorded somewhere; `recompute_admission` is pure and cannot open the
   file. Is a recorded boolean the right shape, or does this smuggle an operator judgement back
   into the objective recomputation that M4R-04 exists to remove?
5. **Symmetric disagreement.** Operator-excluded-but-M4-admitted raises, just as the reverse
   does. I reasoned that silently accepting it would let a session be dropped for an unrecorded
   reason. Agree, or is one direction meant to be permitted?

### B. A known gap I did NOT fill, deliberately
6. **§6 item 6:** "A **wholly missing Masimo file** is a separately-logged **no-agreement**
   session (radar-only, descriptive at most)." My schema requires `masimo_path` and
   `masimo_sha256` in scoring mode, so such a session **cannot be loaded at all**. I did not
   invent a third disposition. Is this a Stage 1 defect, or correctly deferred to Stage 4/5
   where the reference and the disposition ledger live?

### C. The window grid (Stage 2)
7. **Check the transcription against §7 line by line.** I re-read §7 before coding and believe
   `src/m4/window_grid.py` matches, but an endpoint or an off-by-one here misaligns every window
   against the reference by 30 s and would still produce plausible-looking MAEs.
8. **`frames_per_window` raises on a non-integer `window_s × fs`.** Is raising right, or should
   the frozen 600 simply be a constant with no parameterisation at all? The parameters exist so
   the frozen values are visible rather than hard-coded, but they also create a way to build a
   non-frozen grid.
9. **`build_window_grid` takes only `(n_frames, frame0_epoch, fs, frames_per_win)`.** I added a
   test asserting exactly that signature, to pin "boundary-aligned, never greedy" structurally.
   Is a signature assertion a legitimate way to encode that invariant, or theatre?

### D. Scope
10. Is anything in plan §7 rows 1–2 unbuilt that I have mis-scoped as a later stage? I have
    treated the golden fixtures of §7.1 as belonging to the stages they validate (the raw-format
    Masimo CSV fixture to Stage 4, the frame-stream fixture to Stage 3), and built neither here.

## Verification available (read-only)

- `git diff 68179e1..1a3e2d3` — the whole change. `git show b5f6b1e` is the first build,
  `git show 1a3e2d3` the frozen-source correction.
- Suite: `conda run -n radar-vitals python -m pytest tests/ -q` — conda at
  `C:\ProgramData\anaconda3\condabin\conda.bat`, **not on PATH**. Do not invoke the env's
  `python.exe` by absolute path (matplotlib then hard-kills on `savefig`, exit 127, no
  traceback). Multi-line `python -c` under `conda run` silently produces no output — write a
  scratch `.py` and run that.
- **Mutation evidence.** Every rule was mutation-checked: 29 mutants, each disabling exactly one
  rule, **all 29 caught by a failing test** — including two *regression* mutants that restore the
  pre-`1a3e2d3` behaviour (any truncation excludes; any packet loss excludes). The harness is not
  committed (it patches source in place and restores); it is described in the round-0 note at the
  end of `DEBATE COMMENTS` so you can reconstruct or extend it.

## Protocol

- Write findings below as `### S12R-NN [Blocking|Should-fix] — <area>` with fields
  `ISSUE / AUTHORITY / WANTED / REVERSIBILITY / ESCALATE`. IDs are permanent. Blocking first.
- Claude Code moves each into `DEBATE COMMENTS` with a verified response (AGREE / DISAGREE /
  PARTIAL), applies agreed fixes, and polls. Hard cap 3 rounds per finding, then escalate.
- Loop ends when `COMMENTS OF CODEX` reads `NO MORE COMMENTS` and every debate item is resolved
  or escalated. **Only then does Stage 3 begin.**

### Escalate rather than decide
- **Frozen / pre-registration content** (`notes/analysis_prespec.md`, `notes/comparator_prespec*.md`,
  `notes/protocol.md`) → `ESCALATE: frozen content`. Report the conflict; propose no edit to the
  spec. Note `analysis_prespec.md` is **ready for the M0 freeze but NOT yet frozen** — a
  pre-freeze correction is a user decision, not a reviewer's or mine (this was S0R-06).
- A finding that would change a **frozen estimand, gate or grid** → `ESCALATE: requires user
  decision`.
- Anything needing the irreversible M0 deposit to resolve → `ESCALATE: irreversible deposit`.

---

## Author's self-assessment (Claude Code, 2026-07-27) — context, not findings

**Where I am weakest is the admission predicates, and I have evidence for that rather than a
feeling.** I wrote them from plan §7 row 1, which enumerates the *causes*; the frozen §6 defines
what each cause *decides*, and when I finally read §6 line by line I found three of my rules
contradicting it. All three erred the same way — more aggressive than the frozen rule — which is
the dangerous direction, because an over-exclusion looks like caution and silently removes
sessions from a pre-registered analysis. I fixed them before opening this review, but the base
rate is what matters: three wrong out of roughly nine on the first pass. Please re-derive the
rest from §6 rather than checking my reasoning.

**The window grid I am much more confident about**, because §7 states the yields (6/16/20) and
the endpoint rule explicitly and I could test against stated numbers rather than my own
interpretation. The residual risk is transcription, not judgement.

**On testing I have done more than usual and still would not claim non-vacuity.** Four of my
tests were found vacuous in the Stage 0 review, twice while I was fixing a vacuity finding, so
here I mutation-checked every rule before opening the review rather than after being asked. All
29 mutants are caught. But a mutation harness proves each rule has *a* test that depends on it;
it does not prove the test asserts the *right* thing — and three of my rules were confidently
testing the wrong semantics until I read §6.

**One thing I deliberately left undone** (question 6, the missing-Masimo "no-agreement" session).
I could have invented a third disposition. Given that inventing predicates is exactly what went
wrong in this stage, I would rather be told what it should be.

---

COMMENTS OF CODEX

(awaiting Codex's review pass)

---

DEBATE COMMENTS

### Round 0 — Claude Code (2026-07-27): mutation evidence, for reconstruction

Not a finding; recorded so the claim in *Verification available* is checkable. The harness
rewrites one source line at a time and requires the targeted test suite to fail:

- 16 admission-rule mutants (each `if <predicate>:` → `if False:`), 2 regression mutants
  (`flags.append("raw_truncated_trailing")` → `reasons.append(...)`; the packet-loss threshold
  → `ratio >= 0.0` with `reasons.append`), 7 field-validation mutants, 4 window-grid mutants —
  including flipping the reference span from half-open `e < hi` to closed `e <= hi`, and the
  complete-window count from floor to ceiling.
- Baseline must pass; each mutant must make `tests/test_m4_manifest.py` or
  `tests/test_m4_window_grid.py` fail; the file is restored after every mutant and the baseline
  re-run at the end.

Result at the time of writing: **29/29 caught, baseline restored and passing.**
