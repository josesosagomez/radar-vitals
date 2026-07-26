# Cross-model review — M4 Stage 0, the shared-callable refactor (gate on all M4 work)

> **Review coordination file (CLAUDE.md §6).** `plans/m4_offline_harness.md` §5.1 requires that the
> window-level DSP composition and the warmup bin-selection policy be extracted out of
> `scripts/live_demo.py` into `src/` **before any other M4 code is written**, and states that "the
> extraction is a behaviour-preserving refactor of reviewed DSP code and takes its own CLAUDE.md §6
> correctness review". This file is that review.
>
> Codex writes findings into `COMMENTS OF CODEX`; Claude Code processes them into `DEBATE COMMENTS`
> and applies fixes. Prepared 2026-07-26. **Stage 1 (manifest schema) does not begin until this
> closes** — plan §7 build order, row 0: "Nothing else starts before this."

## Status

**OPEN — awaiting Codex's first pass.**

Suite at time of writing: **1073 passed, 0 failed, 0 xfailed** (baseline before the refactor was
1056; the 17 new tests are `tests/test_window_pipeline_adapter.py`).

## Why this refactor exists (the failure it prevents)

`plans/m4_offline_harness.md` §5.1 / finding M4R-10: revision 2 of the M4 plan claimed M4 and the
live path "use the same DSP". They would not have. `_run_dsp` and `_run_warmup_selection` were
**private functions in a script**, so M4 would have had to duplicate the config wiring, respiration
fusion/validity semantics, ECA inputs, fallback handling and warmup policy. M4's central
"direct shared-DSP equality" test (plan §7 stage 3) would then have compared M4 against *whichever
duplicate the test author wrote* — two copies that silently drift apart **both pass**. The assertion
would have been load-bearing and hollow at the same time.

So the bar here is not "is this tidy". It is: **after this change, is there exactly one implementation
of the window DSP and one of the warmup policy, and is it bit-for-bit the one that produced every
existing artifact?**

## Code under review

| File | What |
|---|---|
| `src/window_pipeline.py` | **NEW.** `run_window_dsp` (moved from `live_demo._run_dsp`), `REJECTION_CODE_NAMES` (moved), and **new** adapter code: `WindowEstimate`, `config_hash`, `as_window_estimate`, `WindowEstimator` protocol |
| `src/warmup_select.py` | **NEW.** `run_warmup_selection`, `derive_candidate_bins`, `range_energy_by_bin`, `resolve_locked_bin` — all moved from `live_demo` |
| `scripts/live_demo.py` | −489 lines; imports both. Newly-unused `scipy.fft` / `src.respiration` / `src.vitals` imports dropped |
| `scripts/validate_warmup_selection.py` | Import repointed |
| `scripts/diagnose_live_run.py` | Two stale code comments repointed (no logic change) |
| `tests/test_live_demo_warmup_helpers.py` | Import target + one monkeypatch path repointed. **Assertions unchanged** |
| `tests/test_window_pipeline_adapter.py` | **NEW.** 17 tests: the adapter, plus the standing no-duplicate guard |

**The change is commit `4b64eb8`** (parent `d3cfb92`). `git show 4b64eb8` is the whole diff.

**Note the asymmetry:** the moved code is *reviewed DSP* and should be verbatim — for it, any
behavioural difference is a bug. The **adapter is new code** and is the part that deserves design
scrutiny, not just diff-checking.

## Invariants to hold the change to

- **Verbatim means verbatim.** The DSP and warmup bodies must be byte-equivalent in behaviour to
  `HEAD`. Not "equivalent modulo a cleanup I made while moving" — CLAUDE.md §9 forbids silently
  changing an estimator mid-project, and every existing artifact was produced by the old code.
- **Exactly one implementation.** No aliases, no re-export shims, no second copy anywhere.
- **No weakened test.** The suite must still fail for every reason it failed before. A test that
  passes because its monkeypatch now targets a name nobody reads is *worse* than a deleted test.
- **Determinism** (CLAUDE.md §3.1). No RNG, no import-order dependence, no wall-clock in a value.
- **No dangling references** to the old locations left in code or docs.

## Specific questions to scrutinise (not a limit — raise anything)

### A. Was the move actually behaviour-preserving?
1. **Diff the moved bodies against the pre-refactor original, line by line.**
   `git show d3cfb92:scripts/live_demo.py`. I
   claim `run_window_dsp` and `run_warmup_selection` (+3 helpers) are verbatim apart from the
   private→public rename and `_run_dsp` → `run_window_dsp` in `dsp_fn`'s default and in docstrings.
   **Verify that claim rather than trusting it.** Any changed constant, reordered statement, altered
   comparison or dropped guard is Blocking.
2. **My equality evidence is one capture.** I ran old and new in the same process on the **natural**
   capture's first 600 frames (`20260713_172042_..._massimo1`): `run_window_dsp` bitwise-identical at
   the locked bin, `run_warmup_selection` bitwise-identical across all 14 candidate bins including
   the full evidence dict. **Not** covered: paced16, sweep, the partial-DSP-failure path, and the
   all-candidates-fail energy-fallback path. Is one capture plus unit tests sufficient evidence for a
   refactor on the path to every paper number, or do you want the other two captures run?
3. **Relative vs absolute imports.** `src/window_pipeline.py` uses `from .respiration import …`,
   whereas `live_demo` used `from src.respiration import …`. This binds the new modules to being
   imported *as part of the `src` package*. Is there any entry point in this repo (a `steps/` script,
   a `sys.path.insert(…, "src")`) that would import them top-level and break? I believe not, but I
   did not exhaustively check `steps/`.
4. **`np.stack(list(frames))` preserved verbatim.** `run_window_dsp` still accepts a deque *or* an
   ndarray. For M4, which will pass a `(600, …)` array, this is a wasteful copy. I kept it because
   changing it is a behaviour change and this stage is a move. Agree, or should it be tightened now
   while the diff is small?

### B. Is the test repointing honest?
5. `tests/test_live_demo_warmup_helpers.py:451` monkeypatched
   `"scripts.live_demo._range_energy_by_bin"`. Once the function lives in `src/warmup_select.py`,
   that patch installs a fake on a name nothing reads. **The plan's Stage-0 done-when says "the
   1056-test suite passes unchanged", which this makes literally unachievable** — that line had to
   move under any design. Is my reading right, and is repointing (rather than adding a back-compat
   alias in `live_demo`) the correct resolution?
6. I mutation-checked that one test: with a deliberately wrong patch target it **fails**; restored,
   it passes. Confirm that check is meaningful, and check the other 26 tests in that file are still
   testing what they did. The rename was done by regex with a lookbehind so that function *names*
   like `test_run_warmup_selection_*` were not mangled — **verify none were.**
7. Is anything else in the suite now passing vacuously as a result of the move?

### C. The adapter — new code, design review not diff review
8. **`config_hash` uses `json.dumps(cfg, sort_keys=True, default=str)`.** Two concerns I have and
   want ruled on: (a) `default=str` is **lossy** — `Path("a")` and the string `"a"` hash identically;
   (b) it hashes the **whole** config, so a display/backend field that cannot affect DSP changes the
   hash. Is that over-sensitivity acceptable for a provenance key, or should it hash only the
   DSP-relevant subtree? Note the second choice needs a definition of "DSP-relevant" that will not
   silently rot.
9. **`WindowEstimate` shape.** Is this the right normalised record for M8/M9/M10 estimators to enter
   the M4 grid through (plan §5.1 item 3)? Specifically: is dropping `fallback_hr_bpm` from the
   record (kept only in `raw`) right — I did it because CLAUDE.md §4 calls it a liar — and is
   `raw: dict` with `compare=False` a reasonable escape hatch or a hole?
10. **NaN-when-invalid is enforced at the adapter boundary**, so an unverified window can never
    surface a rate. Is enforcing it there (rather than in the scorer) the right place?
11. **What the adapter does NOT do.** It carries no window index, no session/subject ID and no
    alignment info — I judged those to belong to M4's own records (plan §5.1 item 2: "M4 owns only
    raw slicing, validity dispositions and evidence persistence *around* that call"). Is that split
    right, or will it force M4 to re-thread provenance awkwardly?

### D. Scope
12. Stage 0 as built covers plan §5.1 items **1 and 3**. Item 2 is a statement about M4's
    responsibilities and item 4 is the Stage-3 equality test. Do you agree Stage 0 is complete at
    items 1+3, or is something in §5.1 unbuilt that I have mis-scoped as later work?
13. **Dangling references I did not fix, deliberately.** `notes/analysis_prespec.md:427` still says
    `live_demo.py:_run_warmup_selection`. That file is **pre-registration content bound for the M0
    deposit** — I did not touch it. Correct call, or does it need an amendment before deposit?
    (`HANDOFF.md:390` has the same stale pointer and will be fixed in the session-end rewrite;
    `notes/cross_review_warmup_veto_review_1_findings.md` is a historical record and stays as-is.)

## Verification available (read-only)

- `git show 4b64eb8` (the change, all seven files). `git show d3cfb92:scripts/live_demo.py` for the
  pre-refactor original.
- Suite: `conda run -n radar-vitals python -m pytest tests/ -q` — conda at
  `C:\ProgramData\anaconda3\condabin\conda.bat`, **not on PATH**. Do not invoke the env's
  `python.exe` by absolute path (matplotlib then hard-kills on `savefig`, exit 127, no traceback).
- Re-running my A/B equality check on the other captures is legitimate review evidence. Recipe:
  materialise `git show HEAD:scripts/live_demo.py` into a scratch module, import it alongside
  `src.window_pipeline` / `src.warmup_select` in one process, feed both the same 600-frame cube from
  `results/live_demo/<capture>/adc_stream.bin`, and compare recursively with NaN-equals-NaN. Exclude
  `t_warmup_scan_ms` (wall-clock, expected to differ).
- `scripts/validate_warmup_selection.py` runs the real warmup on the stored captures. **Its
  `SESSIONS` expectation table is stale** (pre-M2: it expects bin 23 for `massimo1`, but the current
  lock is 27) — that staleness predates this diff and is not introduced by it.
- A headless replay smoke run works end-to-end: `python scripts/live_demo.py --replay
  results/live_demo/20260713_172042_live_demo_massimo1/adc_stream.bin --replay-fast --headless
  --duration-s 25`. It writes a new artifact dir under `results/live_demo/`; delete it afterwards.

## Protocol

- Write findings below as `### S0R-NN [Blocking|Should-fix] — <area>` with fields
  `ISSUE / AUTHORITY / WANTED / REVERSIBILITY / ESCALATE`. IDs are permanent. Blocking first.
- Claude Code moves each into `DEBATE COMMENTS` with a verified response (AGREE / DISAGREE /
  PARTIAL), applies agreed fixes, and polls. Hard cap 3 rounds per finding, then escalate.
- Loop ends when `COMMENTS OF CODEX` reads `NO MORE COMMENTS` and every debate item is resolved or
  escalated. **Only then does M4 Stage 1 begin.**

### Escalate rather than decide
- **Frozen / pre-registration content** (`notes/comparator_prespec*.md`, `notes/analysis_prespec.md`)
  → `ESCALATE: frozen content`. Report the conflict; do not propose editing the spec (see Q13).
- Any finding that would change the **estimator's behaviour** rather than its location →
  `ESCALATE: requires user decision`. This stage is a move; a genuine DSP improvement found along the
  way is a separate, separately-reviewed change (CLAUDE.md §9).
- Anything needing the irreversible M0 deposit to resolve → `ESCALATE: irreversible deposit`.

---

## Author's self-assessment (Claude Code, 2026-07-26) — context for the reviewer, not findings

Honest pre-analysis of where I think this is sound vs weak. Correct me.

**The move itself I believe is genuinely verbatim**, and I have stronger evidence than usual for that:
old and new implementations run in the same process on real capture data produce bitwise-identical
output, including every nested array in the evidence dict and across all 14 candidate bins. That check
matters specifically because the existing warmup unit tests drive the scorer with a **fake `dsp_fn`**
— they never execute the real DSP composition at all, so a suite pass alone would not have caught a
botched move of `run_window_dsp`. My residual worry is **coverage of the failure paths**: the partial-
failure and all-fail branches were exercised only by unit tests with fakes, and the equality check ran
on one capture where every bin's DSP succeeded.

**Where I am least confident is `config_hash`** (Q8). I wrote it to be obviously deterministic, and
`default=str` was the cheap way to make an arbitrary YAML-derived dict hashable. But a lossy
canonicaliser in a *provenance* key is exactly the kind of thing that looks fine until two genuinely
different configs collide, and I would rather be told now than discover it after M4's outputs carry
the hash. I also suspect hashing the whole config is the wrong granularity, but every alternative I
considered required a hand-maintained list of DSP-relevant keys, which rots silently — that trade
seemed worse. I want this ruled on rather than accepted.

**The `WindowEstimate` design is the piece with the least evidence behind it.** It is shaped by my
reading of what M8/M9/M10 will need, and nothing consumes it yet, so nothing has pressure-tested it.
If it is wrong, it is cheap to change now and expensive once M4's scorer and three milestones' worth
of estimators depend on it. Q9/Q11 are where I would most value an independent opinion.

**On the test repointing I think I am right but I am aware of the incentive.** I changed a test file
in a diff whose done-when says the suite passes "unchanged", which is exactly the situation where an
author talks themselves into a weakening. My defence is that the change is import-target-only, the
assertions are untouched, and I mutation-checked the one test whose semantics actually depended on
the module path. Please check that defence rather than accept it.

**One thing I explicitly did not do:** I made no cleanup, no rename, no "while I'm here" improvement
to the DSP bodies, even where I noticed something (the `np.stack(list(frames))` copy, Q4). If the
reviewer wants those, they should be a separate change with its own review, because this diff's whole
value is that it is provably a move.

---

COMMENTS OF CODEX

(awaiting Codex's review pass)

---

DEBATE COMMENTS

(none yet — Claude Code fills this in as findings are processed)
