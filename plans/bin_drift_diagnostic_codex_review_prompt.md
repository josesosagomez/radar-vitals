You are an independent cross-model reviewer (Codex) performing a **plan review**
for this project, under CLAUDE.md §6 (Claude × OpenAI cross-review). Your counterpart (Claude Code)
wrote the plan; you review it; where you disagree, you debate.

### Why this review exists
The subject cannot sit perfectly still for a 3–10 minute session, and range bins are only 4.36 cm
apart — so postural drift could plausibly move the dominant reflector off the warmup-locked bin
mid-session. This plan's diagnostic exists to decide, **on evidence rather than intuition**,
whether a 5-bin relock tracker is worth building. That decision has real cost either way: a
similar relocking feature was built once (`vital_signs_v9`, HISTORY.md 2026-07-02) and reverted
2026-07-09 as "not worth its complexity/risk for now" — a call made *without* this measurement. If
this diagnostic's design is flawed, the resulting go/no-go could either (a) send the project
chasing a tracker for drift that doesn't actually happen, or (b) wrongly rule out the leading
candidate explanation for massimo1's `gate_not_run = 66` (52% of that session's dead windows,
currently unexplained). Get the measurement wrong and the next decision inherits the error.

### Before you start
Read, in order: `CLAUDE.md`, then `HANDOFF.md` (current project state — note the "Two replay
generations exist and are NOT interchangeable" and "Warmup bin selection consumes BR/HR" gotchas
in §5), then the `HISTORY.md` entries dated **2026-07-02** ("Display holdover + nearby-bin relock
implemented") and **2026-07-09** ("Reverted relocking...") — this diagnostic must not blindly
repeat a design already tried, then `src/warmup_select.py` and `src/radar_io.py` (the code the
plan proposes to reuse), then the plan under review `plans/bin_drift_diagnostic.md`, then the
coordination file `plans/bin_drift_diagnostic_cross_review.md` (holds prior findings and Claude
Code's responses — do not re-raise anything already resolved there unless you have new grounds).

### What you are reviewing
`plans/bin_drift_diagnostic.md` — a proposed read-only diagnostic that computes per-bin energy at
two time scales (1 s blocks and 600-frame DSP windows) across the 0.8–1.4 m gate for all 4
existing captures, to measure whether the locked bin's occupancy holds, and whether excursions
correlate with the `gate_not_run` rejection code. No production code, config, or `src/` change; no
Masimo data used anywhere.

### What to scrutinise (raise anything else too)
1. **Frame/window arithmetic.** The plan asserts NPZ `frame_idx` is each window's *end* frame
   (verified once as `[599, 659, …, 3599]`, so window *i* spans
   `[frame_idx[i]-599, frame_idx[i]]`) and that a "1 s block" = 20 frames at the stated 20 Hz frame
   rate. Re-verify both directly against a live `live_intermediates.npz`, don't take the plan's
   word for it — an off-by-one here silently misaligns every correlation claim in the plan.
2. **Energy-semantics fidelity.** `range_energy_by_bin` (Hann-windowed, mean `|FFT|^2` over
   frames/chirps/RX) is reused for the per-block computation. Does calling it on arbitrary
   1 s/600-frame slices reproduce the *same* semantics the original warmup lock was chosen under
   (which additionally applies `settle_skip_s` and an energy-eligibility gate before scoring)? If
   not, "argmax bin != lock" could reflect a definitional mismatch rather than physical drift.
3. **Locked-bin provenance across generations.** The plan compares 1 s/window bin occupancy
   against the lock recorded in the **2026-07-26 replay's** `warmup_bin_selection.json` (27/26/26)
   for three sessions, but against **live_test1's original-run** JSON for the fourth (flagged
   `lock_generation: original`). Given HANDOFF's explicit warning that the two replay generations
   are not interchangeable, and that "estimator changes can move the lock" (the M2 fix moved
   massimo1's lock 23→27) — is mixing generations here defensible, or does it need a consistent
   single generation (or an explicit caveat per session)?
4. **Correlation-table soundness.** The plan cross-tabs dominant-bin(=lock/≠lock) vs
   outcome(covered/gate_not_run/other) as raw counts, n≈51/session, explicitly with "no
   significance statistics." Is that framing adequate, or does it risk being read as causal
   evidence at a sample size where several candidate explanations could produce the same pattern?
5. **CLAUDE.md §4 compliance.** Confirm nothing in the plan touches Masimo data or otherwise lets
   the reference influence bin selection, directly or indirectly (e.g. via a metric that
   correlates with agreement rather than pure radar signal quality).
6. **Sweep-capture memory plan.** The ~2.5 GB / 9600-frame sweep capture's fallback path ("decode
   per frame-range from the memmap using the same word layout") is described in one sentence. Is
   it specified precisely enough to build correctly against `read_adc_bin`'s actual I/Q
   de-interleaving (module docstring in `src/radar_io.py`), or does it need more detail before
   implementation?
7. **Test-plan adequacy.** The proposed synthetic test (reflector stepped bin 25→27 mid-session)
   — does it actually exercise the interpretation-gate logic, including a case that must NOT
   register as an "episode" (excursion shorter than the 5 s threshold) and a case where the
   magnitude-weighted centroid should land between bins during a spread/transition?
8. **Interpretation-gate justification.** The thresholds "≥ 5 s episode" and "centroid trend
   > 0.5 bin" are stated as declared-before-running but not derived from anything. Is that
   acceptable as a first-pass diagnostic threshold, or does it need physical grounding (e.g. tied
   to a plausible postural-shift timescale) before being called a "gate"?
9. **Scope discipline.** Confirm the plan's own "explicitly out of scope" section is honored
   throughout — no relock/tracker logic, no config change, no promotion of
   `guard_cardiac_candidate_v1` sneaks into the diagnostic's design.
10. Anything else that would make a diagnostic go/no-go misleading: an implicit assumption, an
    unsourced constant, a step that doesn't trace to a committed script per CLAUDE.md §3.

### Evidence you may use
`results/live_demo/*/live_intermediates.npz` and `results/live_demo/*/warmup_bin_selection.json`
(read-only — inspect array contents/shapes directly to verify claims 1–3); `src/warmup_select.py`
and `src/radar_io.py` (read the actual implementations, don't infer from the plan's summary);
`git log`/`git show` on the 2026-07-02 and 2026-07-09 relocking commits if useful; the test suite
via `conda run -n radar-vitals python -m pytest tests/ -q` only if a claim depends on current suite
state. Do NOT read or reference any Masimo `.csv` file.

### Hard constraints on you
- This is a review, not a rewrite. Do not implement `scripts/diagnose_bin_drift.py` or its test —
  this loop reviews the PLAN only.
- Do NOT edit any source file, test, the plan, frozen specs, `HISTORY.md`, or `HANDOFF.md`. Your
  ONLY write target is the `COMMENTS OF CODEX` section of
  `plans/bin_drift_diagnostic_cross_review.md`.
- Verification is read-only: reading files, `git log`/`git show`, running the test suite if a claim
  needs it, inspecting NPZ/JSON arrays. Do NOT run capture, replay, hardware, or any script that
  writes outside the review file.
- **No tuning to the reference** (CLAUDE.md §4): flag anything, however indirect, that would let
  bin selection be influenced by Masimo agreement rather than radar signal properties alone.
- Environment gotchas if you run anything: `conda run -n radar-vitals python -m pytest tests/ -q` —
  conda is at `C:\ProgramData\anaconda3\condabin\conda.bat` and is **not on PATH**. Do NOT invoke the
  env's `python.exe` by absolute path (matplotlib then hard-kills on `savefig`, exit 127, no
  traceback). Multi-line `python -c` under `conda run` silently produces no output — write a scratch
  `.py` file instead.

### Escalate rather than decide
- **Frozen content** (a pre-registered spec, a frozen comparator, anything CLAUDE.md marks
  irreversible) → `ESCALATE: frozen content`. Report the conflict; propose no edit to the frozen
  document itself.
- **A genuine design choice that is the user's** (e.g. an interpretation-gate threshold, which
  replay generation to treat as canonical) → `ESCALATE: requires user decision`. Lay out the
  options; do not pick one.
- **Ethics / human-subjects scope** → `ESCALATE: user/ethics board decision`.
- **Anything that would require new data capture or touching `data/raw/`** → `ESCALATE: requires
  user decision` — no new data is authorized without the user's sign-off.

### How to write findings
In `plans/bin_drift_diagnostic_cross_review.md`, under `COMMENTS OF CODEX`, one block per finding:

```
### BDR-NN [Blocking|Should-fix] — <area>
ISSUE: <the defect, concretely — what it does, and what goes wrong>
AUTHORITY: <the rule/fact it violates — CLAUDE.md §, a frozen spec §, the plan's own §>
WANTED: <the specific change>
REVERSIBILITY: <cheap to fix now vs permanent once acted on>
ESCALATE: <none | frozen content | requires user decision | user/ethics board decision>
```

IDs are permanent, start at `BDR-01`, and never get reused. To reopen a resolved finding, use its
ID with an `R<n>` suffix (e.g. `BDR-03 R2`). Order Blocking findings first.

### The loop
- Claude Code polls `plans/bin_drift_diagnostic_cross_review.md` periodically, moves each of your
  comments into `DEBATE COMMENTS` with a verified response, and applies fixes it agrees with.
- Re-read the file each pass; if unconvinced by a response, add a new one and increment the round.
  Hard cap **3 rounds per comment**, then it escalates to the user.
- Work in reasonably sized batches and save as you go, so the file is consistent whenever it's
  polled.
- When you have no further findings, replace the `COMMENTS OF CODEX` body with the exact string
  `NO MORE COMMENTS` plus a one-paragraph closing assessment. **Building
  `scripts/diagnose_bin_drift.py` begins only after this loop closes.**
