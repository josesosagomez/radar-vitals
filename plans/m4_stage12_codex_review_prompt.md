You are an independent cross-model reviewer (Codex) performing a **code correctness review** for
the mmWave vital-signs project, under CLAUDE.md §6. Your counterpart (Claude Code) wrote the
change; you review it; where you disagree, you debate.

> **VERIFICATION PASS, revision 2 (2026-07-27, after round 6).** You have raised **S12R-01…25**
> across six rounds; **every one was reproduced and agreed, and none rejected.** This pass is not
> a fresh read of a new change — it is checking whether the fixes you asked for are actually right.
>
> **Seven of your round-6 findings are now fixed** — S12R-16, 17, 21 (rule half), 19, 25, 20, 23,
> in commits `1c1c6fb`, `bf7826f`, `1429dfd`. **Five items remain UNBUILT and are not claimed
> otherwise:** S12R-22, S12R-24, S12R-05 R3, S12R-12 R3, and S12R-21's artifact binding. **Two are
> escalated to the M0 freeze by user decision:** S12R-01 and S12R-18. Please do not re-raise any
> of those seven as new findings; comment on the *fixes* and on anything else you find.

### Before you start
Read, in order: `CLAUDE.md`, `AGENTS.md`, then the coordination file
`plans/m4_stage12_review.md` — it holds every finding, Claude Code's verified response to each, and
the round-5 entry describing what was built. Then `plans/m4_offline_harness.md` **§4, §4.1, §6.1 and
§7 rows 1–2** (the build authority), and the **frozen** `notes/analysis_prespec.md` **§6 and §7**
(which outranks the plan). `notes/protocol.md` supplies the settle criterion and the distance range.
Then the diff.

### What you are reviewing

`git diff e409cd6..HEAD -- src/ tests/` — the coupled Stage-1 redesign **plus** the three round-6
fix commits. The most recent, and the ones this pass is really about, are `1c1c6fb` (eligibility
gate), `bf7826f` (acquisition derived from record content) and `1429dfd` (replacement graph).
The earlier six built the redesign itself:

| commit | finding(s) |
|---|---|
| `754536f` | S12R-06 — `SessionDisposition` partition; `RecordKind`; schema **v2** |
| `8a159b8` | S12R-04 settle evidence + S12R-12 discriminated record kinds |
| `c249ad8` | S12R-03 derived checksum + S12R-07 mandatory verification |
| `251ff2c` | S12R-06 — `NO_AGREEMENT` derivation |
| `ee8bd74` | S12R-05 — cross-record retry policy |
| `4b8981b` | S12R-11 R2 method-aware scorability + the capture-inventory fixture |

Everything paper-grade in this project comes out of M4. A wrong disposition predicate silently
removes a session from a pre-registered analysis; a wrong grid endpoint misaligns every window
against the reference by 30 s and still produces plausible-looking MAEs.

### The base rate you should weight this pass by

Not the original "three of nine predicates were wrong" — that is superseded. What this review has
actually shown about the author:

- **Round 1's fixes introduced four new defects** (S12R-08 R2, 09 R2, 10 R2, 13). One of them,
  S12R-13, was an **over-exclusion added while fixing over-exclusions**. Another was a test that
  **asserted the opposite of a frozen document** — `notes/protocol.md` calls the stepped sweep "a
  *method development* capture, not a study session", and the test celebrated admitting it to
  scoring mode.
- **S12R-14** existed because every development-mode test used synthetic plausible values, so the
  first *real* capture parameter to meet the code was the one that broke it.
- In the redesign, **slice 5's first mutation pass caught only 7 of 12**: every retry *link* rule
  shipped with no test depending on it. Two further self-inflicted defects were an **unreachable
  guard** and a rule keyed on `retry_status` that **silently never fired** for a three-attempt chain.

**So: fixes to findings are exactly where this codebase has been weakest.** Re-derive; do not check
the reasoning.

### The five rules DEFINED rather than transcribed — challenge these first

Each is flagged in the code as invented. None traces to a binding document, so "reasonable but
unsourced is a finding" applies to all five:

1. **Validity-map polarity** (`_verify_validity_map`) — 1-D `bool`, `True` = valid, so
   `n_invalid_frames` is the count of `False`. §7 requires the map without fixing dtype or polarity.
2. **The `pre_capture_attempt` field boundary** (`_REQUIRED_PRE_CAPTURE_FIELDS`,
   `_FORBIDDEN_ON_PRE_CAPTURE`) — in particular `capture_git_commit`, which is *forbidden* on an
   attempt on the grounds that no capture occurred, though the attempt did happen at some commit.
3. **`_RETRY_REASON_EVIDENCE`** — the mapping from each permitted replacement cause to the §6
   exclusion reasons that must evidence it on the predecessor.
4. **`MethodProvenance(method_id, fitted_on_session_ids)`** — the shape of the provenance the
   output guard consumes. You required it be method-aware; the field set is the author's choice.
5. **`commanded_rate_schedule`'s entry shape** — `{commanded_rate_bpm, start_s}`, ordered, first at
   0. You accepted the serialization in S12R-09 R2; the surrounding rules are still invented.

### What to verify, by finding

- **S12R-03/07** — is verification *unavoidable*? `parse_session` in scoring mode refuses without
  the derived digest; `load_manifest` computes it. Look for a path that reaches a scorable record
  without hashing. Check the root-escape guard, hash-before-read ordering, and the validity-map
  shape/count checks.
- **S12R-06** — the `NO_AGREEMENT` derivation. Absence is derived from: reference unbound **+**
  acquisition record verified **+** nothing at `reference_expected_path`. Is that actually
  objective, or can an operator still manufacture it? Confirm a bound-then-missing reference is
  **LOST** and never no-agreement, and that exclusion outranks no-agreement.
- **S12R-04** — both settle limbs, transcribed as ≤ 5 bpm spread and ≤ 3 bpm drift, inclusive.
  Verify the equality boundaries pass and that the wiring into §6 item 3 is real.
- **S12R-05** — the trigger is read as an **iff**, so an *original* still reporting
  `selected_confidence == "low"` is rejected as never re-run. Is that the right reading of §6 item 7,
  or over-reach? Also check the one-re-run rule holds for chains longer than two.
- **S12R-11 R2** — `is_scorable` now answers **False** for `collision`. Is refusing it in the
  method-agnostic guard the right consequence, or does it strand M7 evidence?
- **S12R-12** — are the per-kind required/forbidden sets exactly right, and is "both gates pass ⇒
  raise" correct rather than merely convenient?
- **Schema v2** — no v1 manifest exists anywhere (`data/manifest.local.csv` is header-only), so
  there is nothing to migrate. Confirm that reasoning.

Also check, as before: whether any predicate is unsourced; whether the required-field lists match
§4 **plus** the named non-§4 authorities (a captured session now spans **nine** groups — Identity,
Design, Timebase, Integrity, Provenance, Reference, Settle, Retry, Disposition — against §4's six,
and a test asserts every required field traces to a named authority; a pre-capture attempt spans
four — Identity, Design, Timebase, Settle); whether §4.1's boundaries stay pinned at equality;
whether development mode can still emit scoring output; whether anything reads an agreement value;
and whether any test passes vacuously.

### Two items are deliberately open — do not re-raise them as new findings

- **S12R-01's §6 item-4 truncation limb is UNIMPLEMENTED**, by user decision (2026-07-27): the
  conflict between §6's named `mirror_truncated_bytes` and its sub-frame implementation is recorded
  and resolves at the M0 freeze. `test_the_item_4_truncation_limb_is_UNIMPLEMENTED_and_escalated`
  pins the state. Comment on the *pinning* if you wish, not on the absence.
- **The temporal replacement clause** ("no replacement once any of that subject's data is scored")
  is documented with a named Stage-5 enforcement point, per your own S12R-05 R2.

### One incident you may want to verify independently

The slice-6 mutation harness crashed with `OSError [Errno 22]` mid-restore and **left a mutant live
in `src/m4/manifest.py`** (the S12R-14 regression). It was caught, restored, and verified three
ways: the suite returned to its pre-mutation count, a scripted audit found no mutant text, and there
are zero stray `if False:` occurrences. **You are welcome to re-verify the tree yourself** rather
than take that on trust — that is why it is disclosed.

**On mutation evidence generally:** the author reports 12/12, 12/12, 8/8, 12/12 and 5/5 across the
redesign slices. Treat that as evidence each rule has *a* test depending on it — **not** that the
test asserts the right thing. This review has now demonstrated mutation testing missing in **both**
directions: a rule never written (`capture_config_path`, absent from the schema *and* from the
self-referential test claiming to check §4 coverage) and a rule that should never have been written
(S12R-13, which had a happily passing test asserting the wrong semantics).

### Hard constraints on you
- This is a review, not a rewrite. Do not implement Stages 3–8.
- Do NOT edit any source file, test, the plan, the frozen specs, `HISTORY.md` or `HANDOFF.md`.
  Your ONLY write target is the `COMMENTS OF CODEX` section of `plans/m4_stage12_review.md`.
- Running things is encouraged: the suite, your own mutants, your own fixtures. Use
  `conda run -n radar-vitals python -m pytest tests/ -q` — conda is at
  `C:\ProgramData\anaconda3\condabin\conda.bat` and is **not on PATH**. Do **not** invoke the
  env's `python.exe` by absolute path (matplotlib then hard-kills on `savefig`, exit 127, no
  traceback). Multi-line `python -c` under `conda run` silently produces no output — write a
  scratch `.py`. **If you run your own mutation harness, verify the restore** — that is how the
  incident above happened. Do not run capture or hardware.

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

IDs are permanent and **continue from S12R-25** — the next new finding is `S12R-26`. To reopen an
existing one, use its ID with an `R<n>` suffix (`S12R-16 R2`). Order Blocking first.

### The loop
- Claude Code polls, moves each comment into `DEBATE COMMENTS` with a verified response, and
  applies agreed fixes.
- Re-read the file each cycle; if unconvinced by a response, add a new one and increment the
  round. Hard cap 3 rounds per comment, then escalate.
- Work in reasonably sized batches and save as you go, so the file is consistent when polled.
- When you have no further findings, replace the `COMMENTS OF CODEX` body with the exact string
  `NO MORE COMMENTS` plus a one-paragraph closing assessment. **Stage 3 begins only after that.**
