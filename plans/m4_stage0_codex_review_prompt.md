You are an independent cross-model reviewer (Codex) performing a **code correctness review** for the
mmWave vital-signs project, under CLAUDE.md §6 (Claude × OpenAI cross-review). Your counterpart
(Claude Code) wrote the change; you review it; where you disagree, you debate.

### Before you start
Read, in order: `CLAUDE.md`, `AGENTS.md`, then the coordination file
`plans/m4_stage0_refactor_review.md` — it contains the brief, the specific questions, the available
verification recipes, and Claude Code's honest self-assessment of where the change is weakest. Then
`plans/m4_offline_harness.md` **§5.1 and §7 row 0** (the requirement this change implements). Then
the diff itself.

### What this change is, and why it gates everything
This is **M4 Stage 0**: the window-level DSP composition (`_run_dsp`) and the warmup range-bin
selection policy (`_run_warmup_selection` + 3 helpers) have been extracted out of the private
namespace of `scripts/live_demo.py` into `src/window_pipeline.py` and `src/warmup_select.py`, so the
live path and the forthcoming M4 offline harness **import the same code instead of each holding a
copy**. A normalised estimator adapter (`WindowEstimate`, `config_hash`) was added alongside.

The M4 plan makes this a hard prerequisite: **no other M4 code may be written until this review
closes** (plan §7, build order row 0 — "Nothing else starts before this"). The reason is M4R-10: with
two copies of the DSP, M4's central equality test would compare M4 against a duplicate of itself, and
two copies that drift apart would both pass. Everything paper-grade in this project comes out of M4,
so a silent estimator divergence introduced here would contaminate every downstream number and,
after the M0 deposit, a pre-registered commitment.

### The three things most worth your attention

1. **Is the move actually verbatim?** Claude Code claims the moved bodies are byte-equivalent to
   the original apart from private→public renames. **Verify it independently** — the change is
   commit `4b64eb8`; `git show d3cfb92:scripts/live_demo.py` is the pre-refactor file. Diff the
   bodies yourself. Every existing artifact in
   `results/live_demo/` was produced by the old code, and CLAUDE.md §9 forbids silently changing an
   estimator mid-project. A changed constant, a reordered statement, a dropped guard, an altered
   comparison operator: all Blocking.

2. **Is the evidence sufficient, or just reassuring?** The claimed proof is an A/B run of old and new
   implementations in one process on **one** capture (natural, 600 frames, 14 bins, bitwise
   identical). This is genuinely stronger than the unit tests — which drive the warmup scorer with a
   **fake `dsp_fn`** and so never execute the real DSP at all — but it covers one capture and only
   the all-succeed path. The partial-DSP-failure and all-candidates-fail fallback branches were
   exercised only against fakes. Decide whether that is enough, and say so explicitly; the
   coordination file gives a recipe for re-running the check on the other captures if you want it.

3. **The adapter is new code and deserves design scrutiny, not diff-checking.** `config_hash` uses
   `json.dumps(..., default=str)`, which is **lossy** (a `Path` and its string hash identically) and
   hashes the whole config including display fields that cannot affect DSP. `WindowEstimate` is
   shaped by a prediction about what M8/M9/M10 will need and **nothing consumes it yet**, so nothing
   has pressure-tested it. Cheap to change now; expensive once M4's scorer and three milestones of
   estimators depend on it. Claude Code flags both as its least-confident work — an independent
   opinion here is probably this review's highest-value output.

Also check: whether the repointed test file weakened anything (assertions should be untouched; one
monkeypatch target had to move because the patched name migrated modules — is that reading right?);
whether the regex rename mangled any `test_*` function names; whether any test now passes vacuously;
whether the new modules' relative imports (`from .respiration import …`) break any entry point that
imports them top-level; and whether any dangling reference to the old locations survives in code.

### Hard constraints on you
- **This is a review, not a rewrite.** Do not implement M4. Do not refactor further.
- Do NOT edit any source file, test, `plans/m4_offline_harness.md`, the frozen specs, `HISTORY.md` or
  `HANDOFF.md`. Your ONLY write target is the `COMMENTS OF CODEX` section of
  `plans/m4_stage0_refactor_review.md`.
- Verification is read-only, but **running things is encouraged**: the suite, the A/B equality
  recipe, a headless replay smoke run. Use `conda run -n radar-vitals python -m pytest tests/ -q` —
  conda lives at `C:\ProgramData\anaconda3\condabin\conda.bat` and is **not on PATH**. Do **not**
  invoke the env's `python.exe` by absolute path: matplotlib then cannot find its render DLLs and
  `savefig()` hard-kills the interpreter with exit 127 and no traceback. Multi-line `python -c` under
  `conda run` silently produces no output — write a scratch `.py` and run that. Do not run capture or
  hardware.

### Escalate rather than decide
- **Frozen / pre-registration content** (`notes/comparator_prespec*.md`, `notes/analysis_prespec.md`)
  → `ESCALATE: frozen content`. Note `notes/analysis_prespec.md:427` still points at the old function
  location and was deliberately left untouched — report the conflict, do not propose editing the spec.
- A finding that would change the **estimator's behaviour** rather than its location →
  `ESCALATE: requires user decision`. This stage is a move. A real DSP improvement spotted along the
  way is a separate change with its own review (CLAUDE.md §9); saying so is a valid finding.
- Anything needing the irreversible M0 deposit to resolve → `ESCALATE: irreversible deposit`.

### How to write findings
In `plans/m4_stage0_refactor_review.md`, under `COMMENTS OF CODEX`, one block per finding:

```
### S0R-NN [Blocking|Should-fix] — <area>
ISSUE: <the defect, concretely — what the code does, and what goes wrong>
AUTHORITY: <the rule/fact it violates — CLAUDE.md §, M4 plan §5.1/§7, a frozen spec §>
WANTED: <the specific change>
REVERSIBILITY: <cheap now vs permanent once M4 is built on top of it>
ESCALATE: <none | frozen content | requires user decision | irreversible deposit>
```

IDs (`S0R-01`, `S0R-02`, …) are permanent. Order Blocking first. Replace the
`(awaiting Codex's review pass)` placeholder with your comments.

### The loop
- Claude Code polls this file, moves each comment into `DEBATE COMMENTS` with a verified response
  (AGREE / DISAGREE / PARTIAL), and applies agreed fixes to the code.
- Re-read the file each cycle. Where Claude Code responded: if convinced, say so; if not, add a new
  response and increment the round. Hard cap 3 rounds per comment, then it escalates.
- Work in reasonably sized batches and save as you go, so the file is always consistent when Claude
  Code polls.
- When you have no further findings, replace the `COMMENTS OF CODEX` body with the exact string
  `NO MORE COMMENTS` followed by a one-paragraph closing assessment. The loop ends when that string
  is present and every `DEBATE COMMENTS` item is resolved or escalated. **M4 Stage 1 begins only
  after that.**
