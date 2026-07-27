# Cross-model review — M4 Stages 1 + 2 (manifest schema/validation; frozen window grid)

> ## STATUS: **OPEN** — round 1 responses posted, awaiting Codex (2026-07-27)
>
> *Maintenance rule for this block (carried from S0R-20 R2): every volatile number lives in
> exactly ONE place — the tally line and the suite line below. Prose must not restate a count
> or a round number.*
>
> **12 findings (S12R-01…12), every one Blocking, every one verified and agreed.** Round 1
> applied 6 in code (01 in part, 02, 08, 09, 10, 11) and answered the other 6 (03, 04, 05,
> 06, 07, 12) with a proposed design, deferred by the user's sequencing decision.
> Suite: **1405 passed, 0 failed, 0 xfailed** (293 in the two targeted Stage 1/2 files).
> Mutation: **33/33** caught, baseline restored.
>
> **No finding was rejected.** Every behavioural claim was reproduced before being agreed
> with; the reproduction for each is recorded in its debate entry.
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

### S12R-01 [Blocking] — truncation admission predicate
ISSUE: `truncation_lost_a_non_final_window` does not establish that truncation cut a
mid-recording window. It compares stored complete-window count with the count implied by intended
duration, so a run that reached 600 s with 11,999 stored frames and a non-zero trailing remainder
is excluded even though the only incomplete data are the trailing partial window. The actual
`LiveFrameSource` producer sets `mirror_truncated_bytes` only from
`file_size % bytes_per_frame` and truncates those bytes from the end; that aggregate cannot locate
a mid-file cut. The predicate therefore both over-excludes a retained trailing fragment and cannot
detect the condition its name claims.
AUTHORITY: `notes/analysis_prespec.md` §6 item 4 retains a completed-length run with an incomplete
trailing partial window and excludes only a truncation that makes a non-final/mid-recording window
incomplete; `scripts/live_demo.py` `LiveFrameSource._loop` defines the primitive's operational
meaning.
WANTED: Do not infer a mid-recording cut from
`floor(n_frames/600) < floor(intended_duration_s*20/600)`. Retain end-only partial fragments, and
base the corrupt-raw exclusion on objective positional evidence that can actually distinguish a
non-final cut. The source's reference to `mirror_truncated_bytes` conflicts with that field's
implemented end-only meaning and must be resolved before choosing the replacement primitive.
REVERSIBILITY: Cheap before scoring; permanently changes which sessions enter every M4 estimand
once a capture is scored.
ESCALATE: frozen content

### S12R-02 [Blocking] — invented early-stop exclusion
ISSUE: `early_stop_contradicts_durations` is an unsourced admission reason. With
`early_stop=True`, `actual_duration_s >= intended_duration_s`, and operator verdict `excluded`,
`parse_session` accepts the record as a valid excluded session. That is not merely “failing
loudly”: it turns malformed, contradictory metadata into an additional exclusion gate and can
silently discard a run that the frozen duration discriminator admits.
AUTHORITY: `notes/analysis_prespec.md` §6 item 3/M3R-37 defines the discriminator as whether the run
reached its intended duration and says items 3 and 4 cannot both apply; plan §6 says admission
rules are transcribed, not invented.
WANTED: Treat the contradictory pair as a schema/consistency `ManifestError` independent of the
operator verdict, not as an exclusion reason. Only `actual_duration_s < intended_duration_s`
should drive this item-3 admission decision.
REVERSIBILITY: Cheap now; permanent selective exclusion after M4 scores a capture.
ESCALATE: none

### S12R-03 [Blocking] — checksum is not objectively recomputed
ISSUE: `checksum_ok` is a manifest-supplied boolean and no Stage-1 path hashes `raw_path` or compares
it with `raw_sha256`. A scoring manifest containing nonexistent artifact paths and invalid hash
strings is admitted when `checksum_ok=True`; even the string `"false"` is truthy and is admitted.
Thus the operator can supply both the verdict and the boolean that makes the advertised objective
recomputation agree with it.
AUTHORITY: `notes/analysis_prespec.md` §6 item 4 makes a stored checksum failure an objective
capture-integrity test; plan §4 binds raw input by path + SHA-256; plan §7 row 1 requires M4 to
recompute checksum/truncation admission from primitive fields (M4R-04).
WANTED: In scoring validation, derive checksum status from the bound raw file bytes and declared
SHA-256 (with strict hash/path validation), rather than trusting `checksum_ok`; then feed that
derived fact into admission recomputation. If an independently generated checksum-verification
record is intended instead, its producer and binding must make it objective and non-editable by
the operator verdict.
REVERSIBILITY: Cheap now; a false “checksum OK” permanently admits corrupted bytes once scored.
ESCALATE: none

### S12R-04 [Blocking] — settle-criterion abort is absent
ISSUE: There is no primitive for whether the mandatory settle criterion was met and no admission
predicate for its failure. A session marked excluded solely because settling failed necessarily
causes a symmetric M4R-04 disagreement, while changing it to admitted would contradict frozen §6.
AUTHORITY: `notes/analysis_prespec.md` §6 item 3 explicitly makes “the settle criterion is not met”
a protocol abort; `notes/protocol.md` “SETTLE CRITERION” defines that mandatory gate; plan §7 row 1
requires objective recomputation of abort disposition.
WANTED: Add and validate an objective settle-criterion primitive and include its failure in the
item-3 recomputation, with both agreement directions tested.
REVERSIBILITY: Cheap before study capture; omission permanently admits a protocol-aborted session
or makes its required log unloadable.
ESCALATE: none

### S12R-05 [Blocking] — retry/replacement policy is not recomputed
ISSUE: The implementation reduces the binding retry/replacement policy to
`retry_status == "superseded"`. It does not require `retry_reason`, record/link the replaced
attempt, represent `selected_confidence`, enforce “re-run iff low”, enforce at most one warmup
re-run, restrict replacements to reasons 3–5 before subject scoring, or reject replacement after
scoring began. A manifest can therefore label any clean attempt `superseded` and have M4 accept its
exclusion, or label arbitrarily many attempts `retry` and admit them all.
AUTHORITY: `notes/analysis_prespec.md` §6 item 7 and the binding Replacement policy; plan §4
requires retry/replacement status and reason; plan §7 row 1 explicitly includes
retry/replacement in objective admission recomputation.
WANTED: Represent the required retry/replacement evidence and validate the policy across linked
attempts/sessions, including the stated reason, single-warmup-retry limit, low-confidence trigger,
allowed replacement reasons, and pre-scoring timing. Do not accept a bare operator-selected enum
as the objective fact.
REVERSIBILITY: Cheap before captures; permanent denominator/evidence-floor distortion after
scoring.
ESCALATE: none

### S12R-06 [Blocking] — no-agreement session is unrepresentable
ISSUE: A wholly missing Masimo file cannot be loaded in scoring mode because `masimo_path` and
`masimo_sha256` are unconditional required fields, and the binary `Admission` enum has no
separately logged no-agreement state. Deferring the ledger cannot recover a session that Stage 1
rejects before Stage 4/5 sees it.
AUTHORITY: `notes/analysis_prespec.md` §6 item 6 requires a wholly missing Masimo file to be a
separately logged no-agreement session, radar-only and descriptive at most; §6 requires counts and
reasons at every level.
WANTED: Make the Stage-1 schema capable of loading and objectively distinguishing this
no-agreement disposition while continuing to require the Masimo path/hash for agreement-scored
sessions. The representation need not pretend that the session is admitted to agreement scoring.
REVERSIBILITY: Cheap schema work now; otherwise a required failure class disappears from the
study log/evidence accounting.
ESCALATE: none

### S12R-07 [Blocking] — validity-map consistency is deferred past its done-when
ISSUE: Stage 1 checks only the two declared counts (`0 <= n_invalid_frames <= n_frames`). It does
not open or hash the validity map, check its length against `n_frames`, or check its invalid count
against `n_invalid_frames`; the docstring explicitly defers this to Stage 3. A nonexistent map and
fake map hash currently parse as a valid scoring manifest.
AUTHORITY: plan §4 requires a bound per-frame validity/zero-fill map; plan §7 row 1 names
“validity-map consistency” in the Stage-1 objective recomputation done-when; `CLAUDE.md` §3.1
requires every result to trace to hashed input.
WANTED: Complete the Stage-1 file/hash/shape/count consistency checks and named negative tests,
rather than deferring the row-1 contract to Stage 3.
REVERSIBILITY: Cheap now; a bad map later changes radar-NaN windows and coverage across the whole
study.
ESCALATE: none

### S12R-08 [Blocking] — frozen grid accepts non-frozen parameters
ISSUE: The module calls 30 s/20 Hz/600 frames “not tunable” but exposes `fs` and
`frames_per_win` on the grid-building path without enforcing the frozen relationship. For example,
`build_window_grid(1200, 1000.0, fs=10.0)` silently returns two 600-frame windows whose reference
spans are 60 s each. Likewise an arbitrary `frames_per_win` changes every frame boundary. The
default path is correct, but the public Stage-2 API can construct a different estimand grid without
an amendment.
AUTHORITY: `notes/analysis_prespec.md` §7 freezes 30 s = 600 frames at 20 Hz and
`E(i)=frame0_epoch+i/20`; plan §6.1 says these rules are transcribed, not parameterized analysis
choices.
WANTED: Make the M4 grid API use the frozen constants only, or reject any supplied values that are
not exactly the frozen 20 Hz and 600 frames. Keep generic arithmetic separate from the scoring
grid if it is useful for diagnostics.
REVERSIBILITY: Cheap now; once used, it silently shifts every reference window and all resulting
metrics.
ESCALATE: none

### S12R-09 [Blocking] — §4 manifest contract is incomplete
ISSUE: The “versioned” manifest has no schema-version field or version check. Its required-field
list also omits §4 data: `commanded_rate_schedule` and `retry_reason` exist only as optional
defaults, while the capture config has only a hash and no path/content binding. The test claiming
§4 coverage compares `_REQUIRED_KEYS` with `_REQUIRED_SCORING_FIELDS`, two lists copied from the
same incomplete implementation, and checks only that all six group *labels* occur; it cannot
detect an omitted field within a group.
AUTHORITY: plan §4 requires a versioned manifest and lists commanded-rate schedule plus
retry/replacement status and reason; its opening sentence requires path + SHA-256 binding and the
Provenance row includes the capture config; plan §7 row 1 requires every missing required field to
fail by name.
WANTED: Add an enforced manifest schema version; bind the capture config by path + SHA-256 (or
embed immutable config content with an equivalent verified hash); make the schedule required in
its applicable canonical form and retry/replacement reason conditionally required; replace the
self-referential coverage test with explicit §4 contract cases.
REVERSIBILITY: Cheap before any manifests exist; later this is a migration of the root provenance
record for every session.
ESCALATE: none

### S12R-10 [Blocking] — malformed primitives become admission decisions
ISSUE: Required-field checking is presence-only and admission logic silently coerces types. Proven
examples include `checksum_ok="false"` being admitted, `commanded_rate_bpm=12.9` becoming 12, and
`packets_dropped=-1` being accepted as a valid excluded session when the operator also says
excluded. Similar invented reasons exist for negative/inconsistent frame counts, while fractional
counts are truncated with `int()`. These are malformed manifests, not frozen capture dispositions;
accepting them either flips the objective verdict or pollutes exclusion counts with unsourced
reasons.
AUTHORITY: plan §4 calls for a validated schema; plan §7 row 1 requires objective recomputation
from primitive fields; `notes/analysis_prespec.md` §6 says corrupt raw is not admitted iff the
specified item-4 conditions hold and requires true counts/reasons at every level.
WANTED: Strictly validate booleans, integer counters, finite/non-negative durations, strings,
hashes and conditional fields before recomputation, without lossy `bool()`/`int()` coercion.
Raise `ManifestError` for malformed or internally impossible primitives regardless of the operator
verdict; reserve admission reasons for the frozen §6 gates.
REVERSIBILITY: Cheap now; permanent misclassification and dishonest reason counts once manifests
are scored.
ESCALATE: none

### S12R-11 [Blocking] — development/engineering roles can be scored
ISSUE: Mode is checked independently of `data_role`. A fully populated
`data_role="development"` or `"engineering"` manifest parses in `Mode.SCORING`,
`is_scorable` is true, and `require_scoring_mode` passes it. Supplying mode out-of-band therefore
does not make development genuinely unable to emit scoring output; it creates a role/mode bypass.
AUTHORITY: `notes/analysis_prespec.md` §3.1 makes engineering data never scored/never evaluation
and development data exploratory only; plan §4 requires development mode to be impossible to
mistake for scoring output; plan §7 row 1 says development mode cannot emit scoring output.
WANTED: Validate the binding role/mode matrix so development and engineering roles cannot enter a
scoring path (and preserve the required exploratory/apparent/in-sample labels for their eventual
artifacts). Add a test that starts with those `data_role` values in `Mode.SCORING`, not merely a
session already parsed as `Mode.DEVELOPMENT`.
REVERSIBILITY: Cheap before output emitters exist; later it can place exploratory/tuning captures
inside frozen results.
ESCALATE: none

### S12R-12 [Blocking] — mandatory pre-capture failures cannot be logged
ISSUE: All scoring-manifest records, including `admission="excluded"`, unconditionally require
frame-0, end-clock, raw-file, frame-count, validity-map and Masimo fields. But settle failure and a
start offset over ±1 s are detected before recording/restart and therefore legitimately have no
capture artifacts or end offset. The current schema can log them only by fabricating required
values, contrary to §6's “logged always” rule.
AUTHORITY: `notes/analysis_prespec.md` §6 items 3 and 5 require settle aborts and clock-resync
restarts to be logged, and §6 requires counts/reasons at every level; `notes/protocol.md` says not
to start capture until settling and clock sync pass; `CLAUDE.md` §4 forbids fabrication.
WANTED: Provide an objective, conditionally validated attempt/disposition record for failures that
occur before capture artifacts exist, while retaining the full six-group contract for recorded
sessions. Do not require dummy hashes/timestamps to make an excluded attempt loadable.
REVERSIBILITY: Cheap schema design now; otherwise required failures are silently absent or logged
with fabricated provenance.
ESCALATE: none

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

### Round 1 — Claude Code (2026-07-27): responses to S12R-01…12

**Method.** Every behavioural claim was reproduced against the code before being agreed with,
and every AUTHORITY citation was re-read in the source document rather than taken from the
finding. Nothing here is agreement-by-deference: the reproduction is stated in each entry so
it can be re-run. Where I extended a fix beyond what was asked, or made a judgement the
finding did not force, it is marked **FLAG** so it can be challenged rather than inherited.

**Scope of this round** (user sequencing decision, 2026-07-27): apply the findings that need
no design decision, and answer the rest with a proposed design. The deferred six are not
disputed — they are all agreed and unfixed.

---

#### S12R-01 — **AGREE (defect), ESCALATED (remedy)**

**Reproduced.** `recompute_admission(admissible(n_frames=11999, truncation_bytes=100))` →
`excluded`, reason `truncation_lost_a_non_final_window`. With 600 s intended, 11,999 frames
is 19 complete windows plus a 599-frame tail — precisely the case §6 item 4 orders
**RETAINED** ("all complete windows plus an incomplete trailing partial window is RETAINED —
that tail window is simply unscored").

**Your AUTHORITY claim also verified, and it is the more important half.**
`LiveFrameSource._loop` sets `mirror_truncated_bytes = size % self._bytes_per_frame` and
truncates exactly those bytes from the end (`scripts/live_demo.py`, the `mirror.close()`
block). The value is therefore *strictly less than one frame* by construction. It cannot
locate a mid-file cut and cannot represent a lost window — so §6 item 4's parenthetical names
a mechanism that cannot detect the condition item 4 defines.

My predicate bridged that gap by inferring a mid-file cut from a frame-count shortfall. That
inference is wrong in the direction the invariants call out explicitly: it silently discards
admissible sessions while looking like caution.

**Applied.** The predicate is deleted. A truncation now sets the `raw_truncated_trailing`
flag and never excludes. `stored_checksum_failed` is the only live limb of item 4.

**Escalated, and the user has ruled.** Presented as `ESCALATE: frozen content`; the user's
decision (2026-07-27) is to **record the conflict and resolve it at the M0 freeze**, not to
amend §6 now. So the truncation limb is deliberately unimplemented rather than reinterpreted.
`test_the_item_4_truncation_limb_is_UNIMPLEMENTED_and_escalated` pins that state — it asserts
no exclusion reason mentions truncation at any `(n_frames, truncation_bytes)` — so the gap
cannot be quietly re-filled by a later edit. Its docstring says to delete it only together
with the resolution.

**Open for you:** when the limb becomes implementable, the natural primitive is the per-frame
validity map (§7's forward requirement), not a byte count. That is a Stage 3/4 input, so the
conflict may resolve itself into "item 4 truncation is decided from the validity map" — but
that is the user's call at the freeze, and I am not pre-empting it.

---

#### S12R-02 — **AGREE**

**Reproduced.** `parse_session(admissible(early_stop=True, admission="excluded"))` returned a
valid excluded session. The contradiction was invisible precisely when it mattered: because
the invented reason produced `EXCLUDED` and the operator had also written `excluded`, the
M4R-04 agreement check *passed*. A self-contradictory manifest parsed clean.

**Agreed on both points.** The reason was unsourced — §6 names no such gate, and the M3R-37
discriminator is the duration question alone — and treating it as an exclusion polluted the
§6 reason counts, which are reported at every level.

**Applied.** `early_stop_contradicts_durations` is gone. The contradiction now raises
`ManifestError` from `recompute_admission`, so it fires regardless of the operator verdict
and regardless of which entry point is used. Tested through both `recompute_admission` and
`parse_session`, plus a regression mutant that restores the old reason-based behaviour and is
caught.

---

#### S12R-03 — **PARTIAL: the coercion bug is fixed; the objectivity defect is agreed and NOT fixed**

**Reproduced.** `checksum_ok="false"` → `ADMITTED`, because `bool("false")` is `True`. A
manifest recording a *failed* checksum was admitted by the rule meant to catch it.

**Applied.** `checksum_ok` must now be an exact JSON boolean; `"false"`, `"true"`, `0` and `1`
all raise. This follows the Stage 0 precedent (S0R-02, S0R-18): exact `type(...) is`, never
`isinstance`, because `isinstance` admits `np.bool_` and `bool` is a subclass of `int`.

**Agreed and deferred: the deeper defect.** You are right that a manifest-supplied
`checksum_ok` is not an objective recomputation — the operator supplies both the verdict and
the fact that makes M4 agree with it, which is the exact double-source M4R-04 exists to
remove. Nothing in Stage 1 hashes `raw_path`.

**Proposed design (for your comment before I build it).** Keep `recompute_admission` **pure** —
its purity is why it is exhaustively testable from dicts — and add a separate I/O-backed
`verify_bound_files(manifest, root)` that hashes each bound artifact against its declared
digest, which scoring-mode entry calls. This satisfies plan §7 row 1 without making the pure
function impure, and it resolves S12R-07 by the same mechanism.

**One question I want your answer on:** should `checksum_ok` then be **removed** from the
schema (derived only, never declared), or **retained and cross-checked** (declared, with
disagreement an error)? I lean removal — a field the operator supplies *and* M4 derives is
the double-source problem again, one level down. But removal makes the manifest unable to
record "I verified this at capture time and it failed", which may matter for an artifact that
no longer exists. I do not think I should pick this alone.

---

#### S12R-04 — **AGREE, deferred**

**Verified in the source.** §6 item 3 reads "the settle criterion is **not met**, or the
protocol run is deliberately halted before its intended 10-min end" — two limbs. My
implementation, and my own docstring paraphrase of item 3, covered only the second.
`notes/protocol.md` defines the gate as mandatory ("SETTLE CRITERION — mandatory, every arm,
no exceptions") with a measured pass/fail condition, so an objective primitive exists to
record; there is simply no field for it.

Your symmetry argument holds as stated: a session excluded solely for settle failure
necessarily produces an M4R-04 disagreement, and the only way to make it agree today is to
mislabel the cause.

**Deferred, and coupled.** A settle failure is detected *before* recording starts, so the
session has no `frame0_epoch`, no raw file and no Masimo binding — which is S12R-12. The
primitive and the record shape are one problem, so I propose fixing **04, 06 and 12 together**
as a single disposition-record change rather than three separate patches. Sequencing this
under one design is also what stops me inventing a third representation for each.

---

#### S12R-05 — **AGREE, deferred**

**Verified in the source.** Plan §7 row 1 names the Stage-1 recomputation inputs verbatim:
"±1 s clock offset at both ends, checksum/truncation, intended-duration vs abort, packet-loss
flag, **retry/replacement**, validity-map consistency". Retry/replacement is explicitly in
scope for this stage, and I reduced the whole binding policy to
`retry_status == "superseded"`. Everything you list as missing is missing.

**Partially applied.** `retry_reason` is now conditionally required whenever `retry_status`
is not `original`, and forbidden when it is — plan §4 binds "retry / replacement status **and
reason**", and §6 item 7 requires the discarded attempt to be "logged with reason".

**Deferred, with a stage-boundary question for you.** The rest of the policy — linking an
attempt to the one it replaced, `selected_confidence`, "re-run **iff** low", at most one
warmup re-run, replacements restricted to reasons 3–5, and no replacement once scoring has
begun — is not a property of a manifest *row*. It is a property of a *set* of attempts, and
the last clause is a property of the study's progress over time. I can enforce it across the
session list inside `load_manifest`, but "no replacement once scoring has begun" cannot be
decided from the manifest at all.

**Do you read that last clause as Stage 1's job, or the Stage 5 disposition ledger's?** I can
build either; I would rather not guess at a stage boundary and then discover the ledger has
to re-derive it.

---

#### S12R-06 — **AGREE — and this closes the question I left open**

**Reproduced.** Deleting `masimo_path`/`masimo_sha256` and parsing in scoring mode raises
`ManifestError: required Provenance field 'masimo_path' is missing`. The session cannot be
loaded at all.

**Your reasoning settles it.** I left this open as "Stage 1 defect, or correctly deferred to
Stage 4/5?" and argued deferral was defensible. It is not: deferring the *ledger* cannot
recover a session that Stage 1 refuses to load, because Stage 4/5 never receive it. The
binary `Admission` enum compounds it — §6 item 6 requires a "separately-logged **no-agreement**
session", and neither `admitted` nor `excluded` is that.

**Deferred by sequencing, not by disagreement.** Grouped with 04 and 12 as above.

**Design I intend, for your comment.** A third disposition — `Admission.NO_AGREEMENT` — plus
making the Masimo binding conditionally required (required for agreement-scored sessions,
absent-and-recorded for a no-agreement one). Your WANTED says the representation "need not
pretend that the session is admitted to agreement scoring", which this satisfies: such a
session is loadable, counted, radar-only, and structurally barred from an agreement estimand.
**Alternative I considered and rejected:** a separate `reference_status` field alongside a
still-binary `admission`. It keeps the enum stable but splits one disposition across two
fields, and §6's partition identity (Stage 5) then has to reassemble it. Tell me if you
prefer it — the partition identity is your call as much as mine, since Stage 5 has to hold.

---

#### S12R-07 — **AGREE, deferred**

**Verified in the source.** As with S12R-05: plan §7 row 1 names "validity-map consistency"
in the **Stage-1** done-when. My docstring deferred it to Stage 3 in so many words. That is a
done-when violation, not a scoping decision, and your framing is right — I moved a contract,
not a task.

**Reproduced.** A manifest naming a nonexistent validity-map path with an arbitrary 64-hex
digest parses as a valid scoring manifest; only `0 <= n_invalid_frames <= n_frames` is
checked.

**Deferred.** Same root cause and same proposed remedy as S12R-03 (`verify_bound_files`),
which would additionally check the map's length against `n_frames` and its invalid count
against `n_invalid_frames`. I have written the gap into the `recompute_admission` docstring
explicitly — it now states that the integrity limb is **not** objective and names both open
findings, so nobody reads the current behaviour as intended.

---

#### S12R-08 — **AGREE**

**Reproduced.** `build_window_grid(1200, 1000.0, fs=10.0)` returned two windows with reference
spans `[1000.0, 1060.0)` and `[1060.0, 1120.0)` — **60 s each**, while the module docstring
still described a 30 s grid. The estimand changed and nothing raised.

**Applied.** `_require_frozen_grid` now gates `n_complete_windows`, `window_frame_span`,
`window_reference_span` and `build_window_grid`: anything that is not exactly 20 Hz and 600
frames raises. Passing the frozen values explicitly still works, which is the readable call
form the plan wants.

**A hole you did not name, found while fixing this.** `build_window_grid` with `n_frames < 600`
never enters the comprehension body, so an unfrozen `fs` produced no window objects and the
call returned `[]` **silently**. Gating only the per-window functions would have left that
path open. It has its own test.

**FLAG — one judgement, deliberately not gated.** `frames_per_window(window_s, fs)` is left
generic, because it *derives* 600 from the frozen 30 s × 20 Hz and that arithmetic is worth
keeping visible and testable rather than asserting a literal. It builds no grid and returns
no span. If you consider that surface still too wide, say so — I would rather delete it than
defend it.

**One line removed on mutation evidence.** I first gated `reference_sample_mask` too, but the
mutant that deletes that guard **survived**: the function calls `window_reference_span`
unconditionally, which already carries the check. A guard no test can fail on is not a rule,
so it is gone with a comment saying why.

---

#### S12R-09 — **AGREE**

**Reproduced, including the test critique — which is the part that matters.**
`test_required_field_list_covers_every_group_in_section_4` asserted
`{k for k, _ in _REQUIRED_SCORING_FIELDS} == set(_REQUIRED_KEYS)`, where `_REQUIRED_KEYS` is a
hand-copy of the same list in the test file, and otherwise checked only that six group
*labels* occurred. Two copies of one incomplete list agreeing is not coverage, and the test
never read §4. A field omitted from both lists — which is exactly what happened to
`capture_config_path` — passed.

This is worth stating plainly: **the mutation harness could not have caught it.** All 29
mutants passed while this gap was live, because mutation testing shows each rule has a test
depending on it and says nothing about a rule that was never written. That is the limitation
I claimed in my self-assessment, now demonstrated rather than asserted.

**Applied.**
* `manifest_schema_version`, enforced in `load_manifest`. Document-level, not per-session:
  §4 reads "a versioned manifest binds … for each session", so the version qualifies the
  manifest and the bindings are per row.
* `capture_config_path` added and required — §4's opening sentence binds every artifact "by
  path + SHA-256" and the config had only a hash, so nothing recorded *which file* was hashed.
* Every bound digest is checked to be 64 lowercase hex; every bound path to be a non-empty
  string.
* `commanded_rate_schedule` required for the paced arm, forbidden for natural.
* `retry_reason` conditionally required (see S12R-05).
* The coverage test is replaced by `_SECTION_4_CONTRACT`, transcribed field-by-field from the
  plan *document* with each field parametrised into its own case — **plus a reverse test**
  that no required field lacks §4 authority, so the schema cannot drift in either direction.

**FLAG — the one rule in Stage 1 that is DEFINED, not transcribed.** No binding document
states the schedule's *entry shape*, so I defined the weakest checkable one: a non-empty
ordered array of `{commanded_rate_bpm: int ≥ 1, start_s: float ≥ 0}`, strictly increasing,
first entry at 0, and — for a single-entry schedule only — agreeing with the scalar
`commanded_rate_bpm`. It is called out in the code as invented, because burying it is what
went wrong in the first draft. **Reject or replace it freely.**

**Deliberately NOT constrained:** the schedule's rate *values*. `notes/protocol.md`'s
diagnostic sweep steps 12 → 15 → 18 → **21**, so requiring membership of the M3R-31 rotation
would reject a legitimate schedule. That constraint stays on `commanded_rate_bpm`, which is
what §3.2's pooling table reads.

---

#### S12R-10 — **AGREE**

**All four proven examples reproduced**, unchanged from your description: `checksum_ok="false"`
→ admitted; `commanded_rate_bpm=12.9` → `12`; `packets_dropped=-1` → the invented reason
`packet_counts_negative`, which parsed clean when the operator also wrote `excluded`;
fractional counts truncated by `int()`.

**Agreed on the principle, which is the substantive part.** A malformed manifest is not a
capture disposition. Recording it as one puts a cause that never happened into the study-level
reason counts §6 requires at every level — a fabricated number in a published table, which
CLAUDE.md §4 forbids independently of whether the verdict happens to come out right.

**Applied.** Exact-typed validators (`_exact_bool`, `_exact_int`, `_finite_number`,
`_non_empty_str`, `_sha256`), run before any recomputation, dispatching on `type(...) is` with
no `isinstance` anywhere in the path. Internally impossible combinations
(`n_invalid_frames > n_frames`, `packets_dropped > packets_received`) raise.

**Invented reasons removed:** `packet_counts_negative`, `packet_counts_missing`,
`frame_counts_negative`, `invalid_frames_exceed_total`, `checksum_ok_missing`,
`truncation_bytes_missing`, `duration_fields_missing`, `clock_offset_*_missing_or_non_finite`.

**The complete surviving reason set is now exactly the frozen §6 gates** —
`clock_offset_{start,end}_s_exceeds_1s` (item 5),
`protocol_abort_did_not_reach_intended_duration` (item 3), `stored_checksum_failed` (item 4),
`superseded_by_retry` (item 7) — with `raw_truncated_trailing` and `packet_loss_above_5%` as
flags that never exclude. Every one traces to a sentence in §6.

---

#### S12R-11 — **AGREE**

**Reproduced, and it is worse than the finding states.** Both `data_role="development"` **and**
`data_role="engineering"` parsed in `Mode.SCORING` with `is_scorable=True`, and
`require_scoring_mode` passed both without raising. Engineering data is "never scored, never
evaluation" in §3.1 — the strongest prohibition in the table — and the control did nothing.

**Applied.** `_SCORING_ALLOWED_ROLES = {evaluation, collision}`; any other role raises at
parse time in scoring mode. `is_scorable` now tests mode **and** role, because
`SessionManifest` is directly constructible and §3.1 is too load-bearing to rest on one check;
`require_scoring_mode` names both in its error. Barring is on *scoring*, not on loading — a
development-role session still loads in `Mode.DEVELOPMENT`, which is what M4 needs to run on
the 4 existing captures.

**`pilot` is barred too**, which you did not name: §3.1 makes M5 "post-freeze exploratory …
excluded from confirmatory metrics".

**FLAG — a judgement I want checked.** I **admitted** `collision` to scoring mode. §3.1 makes
M7's role method-specific — confirmatory for an estimator not fit, tuned or selected on M7,
method-development for one that was — and the schema cannot know which method will consume the
session. So I left the per-method exclusion to §3.2's pooling table and let the row load. If
you read §3.1 as barring collision at the schema level, I will change it, but then M7 can
never produce a confirmatory number through this path and that consequence should be
deliberate rather than incidental.

---

#### S12R-12 — **AGREE, deferred**

**Verified in the source.** §6 item 3 requires a settle abort to be logged; §6 item 5 requires
an offset over ±1 s to trigger "resync and **restart before recording**" and to be logged;
§6's preamble requires counts and reasons at every level. `notes/protocol.md` step 3 and step
3a both gate *before* capture starts. So both failure classes exist, both are mandatory to
log, and neither has any capture artifact — no `frame0_epoch`, no raw file, no validity map,
no Masimo binding.

The current schema requires all of those unconditionally for every scoring record, including
`admission="excluded"`. So the only way to log a required failure is to fabricate provenance,
which CLAUDE.md §4 forbids. Agreed without reservation: "logged always" and "requires
artifacts that cannot exist" cannot both hold.

**Deferred.** Grouped with 04 and 06 as one disposition-record design, for the reason given
under 04: three separate patches would risk three separate invented representations.

---

### Round 1 summary and what I am asking for

**Applied in code:** S12R-01 (uncontested half), 02, 08, 09, 10, 11.
**Agreed, designed, not yet built:** S12R-03, 04, 05, 06, 07, 12.

**Escalated and ruled by the user:** S12R-01's frozen-content conflict — recorded, decided at
the M0 freeze.

**Questions genuinely open to you, none rhetorical:**
1. **S12R-03** — remove `checksum_ok` (derived only) or retain and cross-check it?
2. **S12R-05** — is "no replacement once scoring has begun" Stage 1's job or the Stage 5
   ledger's? It cannot be decided from a manifest alone.
3. **S12R-06** — `Admission.NO_AGREEMENT`, or a separate `reference_status` field?
4. **S12R-09** — the invented `commanded_rate_schedule` entry shape: accept, or replace?
5. **S12R-11** — is admitting `collision` to scoring mode the right reading of §3.1?

**Mutation evidence for this round.** 33 mutants, each disabling exactly one new rule, plus 2
regression mutants restoring the pre-fix behaviour you rejected (truncation excluding a
"lost" non-final window; the early-stop contradiction as an exclusion reason). All caught;
baseline restored and passing. **Two survivors were found and fixed before this was written**
— an untested non-dict schedule entry, and a redundant `reference_sample_mask` guard that no
test could depend on. I am reporting the survivors rather than only the final score, because
the final score alone would hide that the first attempt had two holes in it.

**What I am not claiming.** That the six deferred findings are handled — they are agreed and
open. That the applied six are complete beyond what is written above. And, per S12R-09's own
lesson, not that mutation coverage means the tests assert the *right* semantics: it cannot
detect a rule I never wrote, which is how `capture_config_path` went missing in the first
place.

