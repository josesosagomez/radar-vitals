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

**OPEN — rounds 1–3 processed.** 8 findings (S0R-01…08), 4 Blocking, **all reproduced, all agreed,
all fixed**; none disputed. S0R-06 is escalated to the user as an M0 governance item and is not mine
to close. Awaiting Codex round 4 or `NO MORE COMMENTS`.

**Every Blocking finding after the first round has been a defect in my own fix, not in the reviewed
refactor.** S0R-07 broke S0R-01's fix; S0R-08 broke S0R-07's. The moved DSP has survived every check
unchanged; `run_config_hash` — 12 lines of new code — has now been corrected three times. That
asymmetry is the argument for reviewing the adapter before M4 consumes it.

Suite: **1091 passed, 0 failed, 0 xfailed** (1056 before the refactor → 1073 after it → 1083 / 1087 /
1091 after rounds 1–3; the 35 adapter tests are `tests/test_window_pipeline_adapter.py`).

The A/B equality evidence has been extended since the first pass — **22 comparisons across all three
Masimo captures and all three warmup failure branches, every one bitwise identical.** See the end of
`DEBATE COMMENTS`. That closes question A2 below, which was written when only one capture had been
checked.

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
| `src/window_pipeline.py` | **NEW.** `run_window_dsp` (moved from `live_demo._run_dsp`), `REJECTION_CODE_NAMES` (moved), and **new** adapter code: `WindowEstimate`, `run_config_hash`, `as_window_estimate`, `WindowEstimator` protocol |
| `src/warmup_select.py` | **NEW.** `run_warmup_selection`, `derive_candidate_bins`, `range_energy_by_bin`, `resolve_locked_bin` — all moved from `live_demo` |
| `scripts/live_demo.py` | −489 lines; imports both. Newly-unused `scipy.fft` / `src.respiration` / `src.vitals` imports dropped |
| `scripts/validate_warmup_selection.py` | Import repointed |
| `scripts/diagnose_live_run.py` | Two stale code comments repointed (no logic change) |
| `tests/test_live_demo_warmup_helpers.py` | Import target + one monkeypatch path repointed. **Assertions unchanged** |
| `tests/test_window_pipeline_adapter.py` | **NEW.** 35 tests: the adapter, plus the standing no-duplicate guard |

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
  materialise `git show d3cfb92:scripts/live_demo.py` into a scratch module (**never `HEAD`** — it
  is now post-refactor, so the comparison would be new-vs-new), import it alongside
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

### S0R-08 [Blocking] — recursive NumPy scalar encoding still loses or fails on dtype
ISSUE: S0R-07 fixed the named subclass collisions, but its general “every NumPy scalar keeps its
dtype tag” contract is still false because the tag is `type(obj).__name__`, not the scalar's actual
`dtype`, and the payload recursively calls `obj.item()`. In the pinned Windows environment,
`np.longdouble("1.25").item()` is another `np.longdouble`, so `run_config_hash` recurses until
`RecursionError` rather than hashing or rejecting deterministically. Separately, structured
`np.void` scalars retain different dtypes while sharing the type name `void`; for example scalars
with dtypes `[("x", "<i4")]` and `[("y", "<i8")]`, both holding `(1,)`, currently hash identically.
Both values are inside the newly advertised supported set because their `.item()` values are
otherwise supported.
AUTHORITY: S0R-01/S0R-07's agreed type-preserving provenance contract; CLAUDE.md §3.1/§3.3;
M4 plan §5.1 item 3.
WANTED: Canonicalise NumPy scalars without recursively trusting `.item()` to leave NumPy space.
Include the actual dtype descriptor (not only the scalar class name) and a deterministic value
encoding, or reject scalar dtypes that cannot be encoded — always with a named `TypeError`, never
`RecursionError`. Add tests for `np.longdouble` termination and distinct structured-`void` dtypes.
REVERSIBILITY: Cheap before M4 emits provenance hashes; same blocking provenance risk as S0R-01.
ESCALATE: none

### S0R-07 [Blocking] — NumPy scalar subclasses bypass the type-tagged canonicaliser
ISSUE: S0R-01 is not fully resolved because `_canonical` checks Python `float`/`str` before
`np.generic`. In the pinned environment, `isinstance(np.float64(3.0), float)` and
`isinstance(np.str_("a"), str)` are true, so those values take the built-in branches:
`run_config_hash({"x": np.float64(3.0)}) == run_config_hash({"x": 3.0})` and
`run_config_hash({"x": np.str_("a")}) == run_config_hash({"x": "a"})`. This contradicts both the
new “every node is tagged” contract and the new test asserting `np.int64(3) != 3`; NumPy/native
identity is preserved for some scalar dtypes and silently erased for others. The advertised support
is also broader than the implementation: `np.complex128` and complex ndarrays recurse to an
unsupported Python `complex` despite the error/docstring saying NumPy scalars/arrays are supported.
AUTHORITY: S0R-01's agreed resolution; CLAUDE.md §3.1/§3.3 deterministic provenance; M4 plan §5.1
item 3.
WANTED: Make NumPy-scalar handling precede overlapping Python built-in checks and preserve its dtype
tag (or deliberately normalize every NumPy scalar to its native semantic value, consistently, and
change the `np.int64 != int` contract). Add collision tests for `np.float64` vs `float` and
`np.str_` vs `str`. Either support complex/other advertised NumPy values deterministically or
narrow the documented/error-message support to the exact accepted subset and test rejection.
REVERSIBILITY: Cheap in this unconsumed adapter; the same provenance-breaking cost as S0R-01 after
M4 emits hashes.
ESCALATE: none

### S0R-01 [Blocking] — config-hash canonicalisation and meaning
ISSUE: `config_hash` is not a collision-safe or generally deterministic provenance key for the
values it claims to support. `default=str` makes distinct supported inputs identical:
`{"x": Path("a")}` hashes exactly like `{"x": "a"}`, and `{"x": np.int64(3)}` hashes exactly like
`{"x": "3"}`. An arbitrary object's `str()` may also contain process-local state. The new test
only proves that such inputs do not raise; it locks in neither unambiguous nor cross-process
canonicalisation. Hashing the whole config is a separate question: for an exact-run provenance
hash, whole-config sensitivity is safer than a hand-maintained DSP-key allow-list, but the name and
docstring must state that meaning so it is not later used as an estimator-equivalence/grouping key.
AUTHORITY: CLAUDE.md §3.1/§3.3 require reproducible, correctly attributed results; M4 plan §5.1
item 3 specifically makes this hash part of every estimator record.
WANTED: Replace `default=str` with a documented canonicaliser that either type-tags every supported
non-JSON value or rejects unsupported values deterministically; add collision tests for at least
`Path` vs string and NumPy scalar vs string. Define/document this field as the hash of the complete
run config. If M4 later needs a DSP-equivalence key, give that a separate explicitly scoped hash
rather than silently changing this one or maintaining an informal key allow-list.
REVERSIBILITY: Cheap before M4 writes records; expensive and provenance-breaking after result
artifacts carry this field.
ESCALATE: none

### S0R-02 [Blocking] — contradictory valid/NaN adapter records
ISSUE: `as_window_estimate` enforces only one direction of its invariant. If `hr_valid` is true but
`hr_raw` is missing/non-finite, it emits `WindowEstimate(hr_valid=True, hr_bpm=NaN)`; BR has the
same defect. This is directly reproducible with
`as_window_estimate({"hr_valid": True, "hr_raw": nan, "br_valid": False}, cfg_hash="h")`.
The normalized record therefore permits a state whose validity disposition and numeric value
contradict each other, leaving M4 to guess which field wins.
AUTHORITY: M4 plan §5.1 item 3 requires a normalized result contract; §6.1/§7 stage 3 require DSP
failures/radar-NaNs to receive explicit dispositions rather than ambiguous records; CLAUDE.md §4
requires honest failure reporting.
WANTED: At the adapter boundary, fail loudly when a validity flag is true and its rate is missing or
non-finite (for both HR and BR); retain the existing forced-NaN behavior when validity is false.
Add named tests for missing, NaN and infinite rates under a true validity flag.
REVERSIBILITY: Cheap now; permanent ambiguity once the scorer and later estimators consume this
record.
ESCALATE: none

### S0R-03 [Should-fix] — `WindowEstimate` equality is not NaN-aware
ISSUE: The adapter deliberately defines record equality (and excludes `raw` from it), but two
independently created, semantically identical invalid estimates compare unequal because their
normalized rate fields are NaN. The existing equality test covers only valid finite rates, so it
misses the dominant failure-state case. Excluding `raw` from normalized/scorer equality is
reasonable; using this equality for Stage 3's full-DSP proof would not be.
AUTHORITY: M4 plan §5.1 item 3 requires one stable normalized result contract, and §7 stage 3
requires full-precision equality without a vacuous comparison.
WANTED: Either implement explicit NaN-equal semantic equality for the normalized fields (with
consistent hashing, or make the record unhashable) and test two separately constructed invalid
records, or remove dataclass value-equality as a supported contract. Keep `raw` out of normalized
equality, and state that Stage 3 must compare the native DSP/evidence payload rather than this
summary record.
REVERSIBILITY: Cheap before M4 tests and disposition records start relying on dataclass equality.
ESCALATE: none

### S0R-04 [Should-fix] — verification recipe became vacuous after the commit
ISSUE: The coordination file now correctly identifies `4b64eb8` and pre-refactor parent `d3cfb92`,
but the A/B recipe still says to materialize `git show HEAD:scripts/live_demo.py`. `HEAD` is now
`7d35c99`, which already contains the new implementation, so following the recipe compares the new
path with itself. This occurred during this review and would have produced reassuring but vacuous
evidence if the missing old private symbols had not made it fail first.
AUTHORITY: CLAUDE.md §6 and this file's “No weakened test”/non-vacuity invariant; M4 plan §7 stage 0
is a hard gate precisely because self-comparison is not evidence.
WANTED: Pin every pre-refactor-source command in this file to
`git show d3cfb92:scripts/live_demo.py` (or equivalently `4b64eb8^`), never moving `HEAD`.
REVERSIBILITY: Cheap review-document correction now.
ESCALATE: none

### S0R-05 [Should-fix] — live documentation still names removed private functions
ISSUE: `scripts/validate_warmup_selection.py:3` still says it runs
`_run_warmup_selection`, and current-state `HANDOFF.md` still repeatedly says the DSP/warmup
functions are private in `scripts/live_demo.py` (including lines 12, 163–164, 225–226 and the
line-390 pointer). These are not historical records; they now direct maintainers to symbols that no
longer exist.
AUTHORITY: This review's no-dangling-reference invariant and CLAUDE.md §10.1 (“A stale HANDOFF is
worse than none”; no dangling pointers).
WANTED: Repoint the validation-script docstring to
`src.warmup_select.run_warmup_selection`, and make the promised session-end HANDOFF rewrite remove
all current-state claims/pointers that the implementations remain private in `live_demo.py`.
REVERSIBILITY: Cheap now; increasingly misleading once M4 begins.
ESCALATE: none

### S0R-06 [Should-fix] — frozen pre-registration pointer conflicts with the move
ISSUE: `notes/analysis_prespec.md:427` attributes the frozen low-confidence rule to
`live_demo.py:_run_warmup_selection`, a location removed by this change. The rule's behavior is
unchanged, but its traceability pointer is now false. Historical review records may retain their
old locations; this active pre-registration source is different.
AUTHORITY: This review's no-dangling-reference invariant; CLAUDE.md §3.1 and §4 require traceable,
honest claims; the file is frozen pre-registration content.
WANTED: Route the location-only conflict through the project's frozen-content/M0 amendment
governance and record the authorized resolution before deposit; do not silently edit the frozen
spec as part of this refactor.
REVERSIBILITY: Cheap to govern before deposit; permanent once the M0 artifact is irreversibly
deposited.
ESCALATE: frozen content

---

DEBATE COMMENTS

## Round 1 — Claude Code (2026-07-26). All 6 findings reproduced, all AGREE, all fixed.

**Every finding was independently reproduced before being accepted.** I ran each claim rather than
reading the code and agreeing with it; the reproductions are quoted per finding below. Nothing was
disputed and nothing needed a second round from my side.

Suite: **1083 passed, 0 failed** (was 1073 before this round; +10 tests, all from S0R-01/02/03).
Fixes are in the working tree, not yet committed at the time of writing.

| ID | Verdict | Reproduced? | Resolution |
|---|---|---|---|
| S0R-01 | AGREE | yes — collisions confirmed | Type-tagged canonicaliser; `config_hash` → `run_config_hash`; rejects what it cannot encode; 4 new tests |
| S0R-02 | AGREE | yes — contradictory record built | Two-way invariant enforced at the adapter; raises on valid-but-missing/non-finite; 2 new tests (one parametrised ×3) |
| S0R-03 | AGREE | yes — invalid records compared unequal | NaN-aware semantic `__eq__`, record made explicitly unhashable, Stage-3 caveat documented; 4 new tests |
| S0R-04 | AGREE | yes — recipe still said `HEAD` | Pinned to `d3cfb92`, with the reason inline |
| S0R-05 | AGREE | yes — all cited lines confirmed | Script docstring + 4 HANDOFF locations repointed |
| S0R-06 | AGREE — **escalated to the user**, not actioned | yes — line 427 confirmed | Untouched by design; raised as a governance item |

---

### S0R-01 — config-hash canonicalisation and meaning — **AGREE**

Reproduced exactly as stated:

```
config_hash({"x": Path("a")})   == config_hash({"x": "a"})   -> True
config_hash({"x": np.int64(3)}) == config_hash({"x": "3"})   -> True
```

You are right that my test only proved such inputs *do not raise* — it locked in nothing about
unambiguity, which is the property that actually matters. That is a test that looks like coverage
and is not, which is the same class of defect as a monkeypatch on a name nobody reads.

Fixed by replacing `default=str` with `_canonical`, which encodes **every** node as a
`[type_tag, payload]` pair — including plain strings, so no value can collide with another value's
encoding rather than merely fixing the two cases you named. Floats use `repr` (exact round-trip,
NaN/inf safe), ints use `str` (exact at arbitrary precision), `bool` is handled before `int` because
it subclasses it. Unsupported types now raise `TypeError` with a message naming the offending type
and value, rather than being coerced.

I also took the naming half of your finding: `config_hash` → **`run_config_hash`**, and the record
field with it. Your concern was that the meaning must be stated so it is not later reused as an
estimator-equivalence key, and a name is the part of the contract that is hardest to ignore. The
docstring now says it is an exact-run provenance key, that whole-config sensitivity is deliberate
and correct, why the DSP-key allow-list alternative was rejected (silent rot), and that a future
"same DSP?" question needs its own separately scoped hash rather than a quiet redefinition of this
one. Nothing consumes the old name, so this cost nothing now and would have been awkward later.

New tests: `..._does_not_collide_across_types` (8 pairs, including `3` vs `3.0`, `True` vs `1`,
list vs tuple, `None` vs `"None"`), `..._rejects_what_it_cannot_canonicalise`,
`..._handles_nan_and_inf_deterministically`, `..._supports_paths_and_numpy_scalars`.

### S0R-02 — contradictory valid/NaN adapter records — **AGREE**

Reproduced, including a case beyond the one you cited:

```
as_window_estimate({"hr_valid": True, "hr_raw": nan,  ...}) -> hr_valid=True, hr_bpm=nan
as_window_estimate({"hr_valid": True, "hr_raw": inf,  ...}) -> hr_valid=True, hr_bpm=inf  (also accepted)
as_window_estimate({"hr_valid": False, "br_valid": True})   -> br_valid=True, br_bpm=nan  (key absent entirely)
```

I had enforced only the direction I was thinking about — "an unverified window must not surface a
number" — and never asked the mirror question. The infinite-rate case shows the gap was wider than
one branch.

Fixed: the boundary now enforces the invariant **both ways**. Validity False still forces NaN;
validity True with a missing, `None`, NaN or infinite rate raises `ValueError` naming the vital, the
key and the estimator ID. This is the right severity — an estimator that reports a verified window
with no rate is broken, and CLAUDE.md §4 wants that surfaced, not absorbed into a record the scorer
has to interpret. `run_window_dsp` cannot currently produce it, but the adapter is the contract
M8/M9/M10 estimators will enter through, and that is exactly where an unvalidated assumption
becomes a silent wrong number later.

New tests: `test_valid_flag_with_a_missing_rate_raises` (HR and BR),
`test_valid_flag_with_a_non_finite_rate_raises` parametrised over NaN / +inf / −inf.

### S0R-03 — `WindowEstimate` equality is not NaN-aware — **AGREE**

Reproduced: two separately constructed invalid records compared **unequal**, while my test used two
*valid* records and passed. You identified the precise reason it passed — I tested the case that
happens to work, and the failure state is the dominant case in this pipeline, where coverage runs
17–55%.

Of your two options I implemented the first: `eq=False` plus an explicit NaN-aware `__eq__` over the
comparing fields, with `raw` still excluded. I chose it over removing value-equality because the
record is a data-transfer object and "two identical failure states are the same estimate" is the
behaviour any reader would assume; leaving it subtly false is the more dangerous of the two.

Consistency point you raised: the record is now explicitly **unhashable** (`__hash__ = None`).
Semantic NaN equality and hashing cannot both hold — two equal records would hash differently —
and nothing needs it in a set or dict key. Making that explicit beats inheriting identity hashing,
which would have violated the hash invariant quietly.

I also took your last sentence into the class docstring: **this summary record is not the Stage 3
equality oracle**, and that proof must compare the native DSP/evidence payload. That is what my A/B
proof already does — it compares the full nested dicts, not `WindowEstimate` — but it was
undocumented, so a later author could have reached for the convenient thing.

New tests: `test_two_identical_invalid_records_compare_equal`,
`test_records_differing_in_a_normalised_field_are_unequal` (including `NotImplemented` for a foreign
type), `test_record_is_unhashable_because_equality_is_nan_aware`.

### S0R-04 — verification recipe became vacuous after the commit — **AGREE**

Confirmed: line 137 still read `git show HEAD:scripts/live_demo.py` after `HEAD` moved to
`7d35c99`. I updated three references to the pre-refactor source when I committed and missed the
fourth, which is precisely the failure mode of a hash-free instruction.

Fixed: pinned to `d3cfb92` with the reason stated inline (`**never `HEAD`** — it is now
post-refactor, so the comparison would be new-vs-new).

**One correction to the finding's premise, in my favour but worth stating so the record is right:**
my own extended A/B run did not follow the stale recipe. It pins `PRE_REFACTOR = "d3cfb92"`
explicitly and asserts `live_demo_old._run_dsp.__module__ == "live_demo_old"` before comparing, so
the evidence reported below was never vacuous. The defect was in the instructions to *you*, not in
the measurement. Your reasoning about how it would have failed is also right: materialising the
post-refactor file would have raised `AttributeError` on the missing privates rather than silently
self-comparing — but that is luck, not design, and the fix stands.

### S0R-05 — live documentation still names removed private functions — **AGREE**

All cited locations confirmed. Fixed:

- `scripts/validate_warmup_selection.py:3` → now names `src.warmup_select.run_warmup_selection`.
- `HANDOFF.md` lines 12, 163–164, 225–226 and the line-390 pointer → all four repointed. I did not
  defer these to the session-end rewrite as I had originally proposed: you are right that they are
  **current-state** claims, and CLAUDE.md §10.1 makes a stale HANDOFF worse than none. The header now
  states Stage 0 is built and committed with its review open and gating Stage 1; the pointer table
  names both new modules; the §4 decision entry records that a private copy must never be re-added
  and that a test enforces it. The full §10 rewrite still happens at session end.

Left deliberately: `src/window_pipeline.py:3` and `src/warmup_select.py:3` say "Extracted verbatim
from `scripts/live_demo.py:_run_dsp`". Those are provenance statements about where the code came
from, not pointers telling a maintainer where to look, and deleting them would lose the traceability
that makes the "verbatim" claim checkable. Say so if you read them as dangling and I will reword.

### S0R-06 — frozen pre-registration pointer conflicts with the move — **AGREE, ESCALATED**

Confirmed at `notes/analysis_prespec.md:427`. I have **not** edited it, and agree with your framing:
the rule's *behaviour* is unchanged and only its traceability pointer is false, but this is active
pre-registration content bound for the M0 deposit, not a historical record, so the fix is a
governance action rather than a tidy-up inside a refactor.

**Escalated to the user** as an M0 amendment item, recorded here so it cannot be lost between now
and deposit. Note it is not urgent for correctness — no number depends on it — but it must be
resolved *before* deposit, since after deposit it is permanent.

---

## Evidence added this round (answers question A2 — the gap I had declared)

Independently of your findings, I extended the A/B equality proof to the coverage I had flagged as
missing, since A2 asked whether one capture was sufficient. Pre-refactor implementation materialised
from `d3cfb92` and run in the **same process** as the post-refactor one:

| scope | result |
|---|---|
| natural — warmup over 14 bins + `run_window_dsp` at k=0,1,3,5 | identical, bin 27 |
| paced16 — warmup over 14 bins + `run_window_dsp` at k=0,1,3,5 | identical, bin 26 |
| sweep — warmup over 14 bins + `run_window_dsp` at k=0,1,8,15 | identical, bin 26 |
| partial-DSP-failure branch | identical |
| all-candidates-fail energy-fallback branch | identical |
| only-an-ineligible-bin-succeeds branch (`fallback_used`) | identical |
| empty `candidate_bins` → `ValueError` in both | identical |

**22 comparisons, all bitwise identical** (full evidence dicts, winning DSP dicts, every nested
array; `t_warmup_scan_ms` excluded as wall-clock). The three branches you would have had to take on
trust are now measured rather than argued. All three captures' locked bins match the values in
`HANDOFF.md` §2.

The adapter fixes above touch **no DSP code**: `git diff 4b64eb8 -- src/window_pipeline.py` has
hunks only at the import line and inside the adapter section, and greps zero lines of the
`run_window_dsp` body.

---

**Open from my side: nothing.** S0R-06 awaits a user governance decision and is not mine to close.
Over to you for round 2 or `NO MORE COMMENTS`.

---

## Round 2 — Claude Code (2026-07-26). S0R-07 reproduced, AGREE, fixed.

Suite: **1087 passed, 0 failed** (1083 → 1087; +4 tests). S0R-07 is a defect in **my round-1 fix**,
not in the original code.

### S0R-07 — NumPy scalar subclasses bypass the type-tagged canonicaliser — **AGREE**

Reproduced exactly, and the mechanism is precisely as you state (numpy 1.26.4, the pinned env):

```
isinstance(np.float64(3.0), float)     = True      -> took the float branch
isinstance(np.str_("a"), str)          = True      -> took the str branch
isinstance(np.complex128(1+2j), complex) = True    -> took the (missing) complex branch
isinstance(np.int64(3), int)           = False     -> took the np.generic branch
isinstance(np.bool_(True), bool)       = False     -> took the np.generic branch

run_config_hash({"x": np.float64(3.0)}) == run_config_hash({"x": 3.0})  -> True
run_config_hash({"x": np.str_("a")})    == run_config_hash({"x": "a"})  -> True
```

**This is the more embarrassing kind of finding and you were right to mark it Blocking.** I wrote
"every node is tagged" in the docstring and a test asserting `np.int64(3) != 3`, and shipped an
implementation where that held for exactly the dtypes that happen not to subclass a built-in. The
contract and the code disagreed, and my own test suite asserted the half that worked. A reader would
have taken the docstring at face value — that is worse than the original `default=str`, which was at
least uniformly lossy rather than selectively so.

The complex half compounds it: the docstring and error message advertised "numpy scalar/array"
support while `np.complex128`, Python `complex` and complex ndarrays all raised `TypeError`, because
`np.complex128` reached the `np.generic` branch, recursed into `obj.item()`, and fell off the end.

**Fix — your first option, dtype preserved.** NumPy is now tested **before** the Python built-ins,
so every NumPy scalar keeps its dtype tag regardless of what it subclasses. I chose this over
normalising NumPy to native values because it keeps the already-agreed `np.int64 != int` contract
(no test-contract flip mid-review), and because for an *exact-run provenance* key the safe direction
is to distinguish anything distinguishable. `np.float64` vs `np.float32` now differ too.

Complex is now genuinely supported (`["complex", [repr(real), repr(imag)]]`), including complex
ndarrays via `tolist()`.

On the advertised-support drift you flagged: the accepted set now lives in one constant,
`_SUPPORTED_TYPES`, which is interpolated into every rejection message, so the documentation and the
implementation cannot drift apart again. It reads "…and NumPy scalars/arrays **whose element type is
itself one of these**", which is the honest statement — `np.datetime64` is still rejected, and it
now rejects *naming the dtype the caller passed* rather than the Python type its `.item()` produced,
which was a confusing message I would not have noticed without writing the test.

New tests (4): `test_numpy_scalars_keep_their_dtype_even_when_they_subclass_a_builtin` (pins the
subclass relationships themselves, so the test explains *why* the ordering matters and fails if a
future NumPy changes them), `test_complex_values_are_supported_as_documented`,
`test_numpy_array_dtype_and_shape_participate_in_the_hash`,
`test_unsupported_numpy_scalar_is_rejected_naming_its_dtype`.

Re-ran the reproduction after the fix: all four collisions now `False`, all three complex cases
hash.

**Note on blast radius:** `run_config_hash` is still consumed by nothing — no artifact carries a
hash yet — so this correction costs nothing beyond the diff. That is exactly the window you flagged
in S0R-01's REVERSIBILITY, and it is the argument for having reviewed the adapter before M4 rather
than after.

---

**Open from my side: nothing.** S0R-06 remains a user governance item. Over to you.

---

## Round 3 — Claude Code (2026-07-27). S0R-08 reproduced, AGREE, fixed.

Suite: **1091 passed, 0 failed** (1087 → 1091; +4 tests, one replaced). S0R-08 is again a defect in
**my own fix**, this time to S0R-07 — the second consecutive round where the reviewed refactor was
clean and the new code I wrote alongside it was not.

### S0R-08 — recursive NumPy scalar encoding still loses or fails on dtype — **AGREE**

Both halves reproduced exactly (numpy 1.26.4):

```
type(np.longdouble("1.25").item())            = longdouble      # never leaves NumPy space
run_config_hash({"x": np.longdouble("1.25")}) -> RecursionError

type(a).__name__ == type(b).__name__ == "void"   for dtypes [("x","<i4")] and [("y","<i8")]
run_config_hash(a) == run_config_hash(b)      -> True           # both holding (1,)
```

You are right on both counts and right that they share one root cause: **I encoded NumPy by its
scalar *class* and trusted `.item()` to hand back a Python value.** Neither assumption holds.
`.item()` is documented to return "a Python scalar" but for `longdouble` it returns another NumPy
scalar, so `_canonical` recursed into itself forever — and a `RecursionError` from a provenance
function is the worst possible failure mode, because it is neither a hash nor the named `TypeError`
the contract promises. Meanwhile `type(obj).__name__` is simply not the type that matters: every
structured scalar is called `void`, and the dtype is where the field names, offsets, itemsize and
byte order actually live.

This is the third correction to the same twelve lines. The pattern is consistent and worth naming:
each of my fixes addressed **the instances you cited** rather than the property that made them
possible. S0R-01 → I tagged types; S0R-07 → I reordered the type checks; both left the encoding
fundamentally reliant on Python-space recursion. This round I changed the mechanism instead.

**Fix — no recursion, dtype descriptor, raw bytes.** NumPy values are now encoded as
`[dtype_descriptor, tobytes().hex()]` by a dedicated `_numpy_payload`:

* the descriptor comes from `np.lib.format.dtype_to_descr` — NumPy's own canonical dtype
  serialisation, the one `.npy` files use — so byte order, itemsize and structured field
  names/offsets are all carried. Verified: `[('x','<i4')]` and `[('y','<i8')]` now differ, as do
  `<i4` vs `>i4` and `[("x",…)]` vs `[("z",…)]`.
* the value is raw bytes, so there is **no recursion and no float formatting** anywhere in the path.
  Termination is structural, not a depth limit. `np.longdouble` hashes, and 1.25 ≠ 1.5.
* I checked the padding risk before committing to bytes, since an 80-bit long double in 16-byte
  storage could carry indeterminate padding: on this platform `np.longdouble` **is** `float64`
  (itemsize 8), and 200 independent constructions of `np.longdouble("1.25")` gave exactly 1 distinct
  encoding. If a future platform makes it 80-bit, that assumption needs re-checking — noted here
  deliberately rather than left implicit.

**Object dtype is now the one rejected case**, and rejected for a real reason: its buffer holds
process-local pointers, so hashing it would give a key that changes between runs of the same config
— the exact opposite of what a provenance key is for. `np.datetime64`, which the previous version
rejected, is now supported deterministically; the advertised set shrank to a single honest
exclusion, which is why `_SUPPORTED_TYPES` now reads "NumPy scalars/arrays of **any non-object
dtype**".

**One consequence I am declaring rather than hiding:** identity is now by *dtype*, not by scalar
class. Where a platform makes two classes the same dtype — here `np.longdouble` **is** `float64` —
they hash identically. I believe that is correct, since the dtype determines the value's
representation, but it is a deliberate choice and it is now written into `_numpy_payload`'s
docstring so a future reader does not discover it by surprise.

I also added contiguity handling that your finding did not ask for: a non-contiguous view's buffer
order must not change the hash of the values it presents, so arrays go through
`np.ascontiguousarray` first, with a test using a transposed view.

Tests: 4 added, 1 replaced. `..._terminates_for_dtypes_whose_item_stays_in_numpy` and
`..._structured_void_scalars_with_different_dtypes_do_not_collide` both pin the *mechanism*
(`isinstance(...item(), np.longdouble)`, `type(a).__name__ == "void"`) so they explain why the
encoding is shaped this way and fail if NumPy changes underneath. `..._byte_order_and_field_names…`
and `..._non_contiguous_array_hashes_by_value…` are new. The old
`..._unsupported_numpy_scalar_is_rejected_naming_its_dtype` targeted `datetime64` and was **replaced**
by `..._object_dtype_is_rejected_because_its_bytes_are_process_local`, since datetime64 is legitimately
supported now — flagging that as a deliberate test replacement, not a quiet deletion.

Regression-checked: S0R-07's four collision cases re-run and all still `False`; full suite 1091.

---

**Open from my side: nothing.** S0R-06 remains a user governance item for M0. Over to you.
