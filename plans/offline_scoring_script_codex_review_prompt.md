You are an independent cross-model reviewer (Codex) performing a **pre-implementation plan review**
for this project, under CLAUDE.md §6 (Claude × OpenAI cross-review) and §5 ("plan before
implement"). Your counterpart (Claude Code) wrote the plan; you review it; where you disagree, you
debate. **No code exists yet** — the point of reviewing now is that a design error costs a
conversation instead of a rebuild.

### Why this review exists
This is the first script in the project that will produce a real MAE/RMSE/coverage number against
Masimo since the old mean-based comparator in `src/compare.py` was retired as SUPERSEDED (see
`notes/comparator_prespec.md`'s §1 "17× difference in the headline number, purely from the
comparator" episode — the exact class of mistake a comparator bug repeats). Its first real use is
deciding whether `guard_cardiac_candidate_v1`'s measured HR-coverage gain (17.6→21.6%,
54.9→70.6%, 23.2→27.8%, `experiments/exp_eca_modes/config_guard_v1.yaml`) is also an *accuracy*
gain, not just more frequent wrong answers, before it is promoted into the production config. A
scoring bug here could wrongly justify promoting an ECA mode that doesn't actually help, or wrongly
block a real gain — and every M8/M9 comparator use after this one (HANDOFF.md §3.3) inherits
whatever this plan gets wrong, since nothing else in the project currently computes agreement
against the frozen spec at all.

### Before you start
Read, in order: `CLAUDE.md`, then `HANDOFF.md` §3.1 (the task this plan answers) and §5 (the
`frame0_epoch`/`start_wall_utc` limitation — note it explicitly, don't take the plan's restatement
on trust), then the two frozen specs this plan must implement faithfully —
`notes/comparator_prespec.md` (HR) and `notes/comparator_prespec_br.md` (BR) — then the existing
code the plan proposes to reuse: `src/m4/window_grid.py` (the frozen window grid),
`src/window_pipeline.py` (`run_window_dsp`, `as_window_estimate`), `src/warmup_select.py`
(`run_warmup_selection`, `resolve_locked_bin`), `src/masimo.py` (`load_masimo`, `window`), and
`src/compare.py` (`paired_metrics`, `coverage_table` — reused; `compare`/`metrics` — superseded, not
reused). Then `scripts/diagnose_bin_drift.py`'s `classify_window_outcome` (imported, not
reimplemented, per the plan). Then the plan under review, `plans/offline_scoring_script.md`. Then
the coordination file `plans/offline_scoring_script_cross_review.md` (holds prior findings and
Claude Code's responses — do not re-raise anything already resolved there unless you have new
grounds).

### What you are reviewing
`plans/offline_scoring_script.md` — proposes a new module `src/comparator.py` (implementing
`comparator_prespec.md`/`comparator_prespec_br.md`'s median-reference, gate, and sensitivity logic)
and a new script `scripts/score_offline.py` (decode a capture → the frozen 30 s/600-frame window
grid → `run_window_dsp` per window → score against Masimo → MAE/RMSE/coverage waterfall, with an
optional 2-config paired comparison for the guard_v1 question). No production DSP change, no
config change to `scripts/live_demo_config.yaml`.

### What to scrutinise (raise anything else too)
1. **The approximate-origin handling.** The plan stamps every output
   `"comparator_status": "exploratory_non_frozen"` because none of the 3 existing Masimo captures
   has a persisted `frame0_epoch` (only the pre-startup `start_wall_utc`). Is that framing
   sufficient, or does any part of the plan's output (e.g. the paired-comparison report, the
   sanity-check step) risk being read later as a citable number despite the stamp? Is there any
   place the plan should refuse to run rather than merely label the output?
2. **Window-grid arithmetic.** The plan slices `cube[frame_start:frame_end]` directly from
   `build_window_grid`'s `frame_start`/`frame_end` (half-open, per `src/m4/window_grid.py`) and
   passes that slice to `run_window_dsp`. Verify independently that a plain `(frame_end-frame_start,
   chirps, rx, adc)` slice really is accepted by `run_window_dsp`'s `frames` argument with the same
   semantics as the live ring-buffer path (its docstring claims a deque and an already-stacked array
   "both stack identically" — confirm this against the actual `np.stack(list(frames))` call, not
   just the docstring). Also verify `fs=20.0`/`frames_per_win=600` are the only values
   `window_grid.py` will accept (`_require_frozen_grid`) and that the plan's script will actually
   fail loudly, not silently coerce, if a capture's real frame rate differs.
3. **Comparator-spec transcription fidelity.** Re-derive the plan's restated gate logic
   (`hr_reference`: usable = finite `pr_bpm` ∧ finite `pi` ∧ `pi≥0.5`; `coverage_ok = n_usable≥24`;
   stationarity excludes iff `p90−p10 (method="linear") > 5.0`; `br_reference`:
   `availability_ok = n_finite_rr≥24`; stationarity excludes iff `p90−p10 (method="linear") > 2.0`,
   **strict** so exactly 2.0 is retained) directly against `comparator_prespec.md` §2.1/§2.2 and
   `comparator_prespec_br.md` §2.1/§2.2 — not against the plan's paraphrase. A transcription error
   here is a bug, and the source document wins. Confirm `method="linear"` is named explicitly at
   every quantile call site the plan describes, per M4R-09.
4. **The coverage waterfall's priority order.** The plan computes a *mutually-exclusive* partition
   — `total = excluded_by_coverage + excluded_by_stationarity + radar_nan + final_n`, with
   reference-side exclusion evaluated before radar-NaN. `comparator_prespec.md` §2.4 lists "total
   windows, excluded-by-PI, excluded-by-coverage, excluded-by-non-stationarity, radar-NaN, and the
   final n" without stating an explicit priority/exclusivity rule. Does the plan's chosen ordering
   (reference gates take priority over radar-NaN) match the spec's intent, or should radar-NaN be
   counted independently/orthogonally (so a window could be double-counted across categories,
   contradicting "mutually exclusive")? Is collapsing PI into the single `n_usable` coverage
   denominator (rather than a separate `excluded_by_PI` bucket) a faithful reading of §2.1's
   pre-deposit clarification, or does it lose the diagnostic distinction that clarification was
   written to preserve (a coverage failure caused by missing data vs. low perfusion)?
5. **`locked_bin` pinning as the default.** The plan pins `locked_bin` to each capture's recorded
   production lock across all `--configs` under test by default (only `--rerun-warmup` re-derives
   it), reasoning that this isolates `eca_mode` as the only variable, matching the existing
   ad-hoc coverage-gain methodology. But the production lock was itself chosen by
   `run_warmup_selection` scoring on `hr_valid`/`br_confidence` under `skip_forbidden_harmonics_v1`
   — could pinning to that lock structurally bias the comparison in favor of
   `guard_cardiac_candidate_v1` losing accuracy at *this* bin even when it might do better at the
   bin it would have chosen for itself (bin 25 vs. production 27, per HANDOFF's own observed case)?
   Is the plan's chosen default actually the right one to answer "is the coverage gain also
   correct," or does it need both modes run and reported side by side rather than one as an
   opt-in flag?
6. **CLAUDE.md §4 compliance.** Confirm nothing in the plan lets Masimo agreement feed back into
   `locked_bin` selection, `eca_mode` choice, or any gate/threshold — this must be pure read-only
   scoring. Also confirm the stationarity/coverage gate thresholds are asserted as coming from the
   frozen spec, not re-derived or re-justified in this plan (which would be a §4 violation by a
   different name — tuning the *reference* admissibility to the radar's own output).
7. **Reuse mechanics.** The plan imports `classify_window_outcome` from `scripts/diagnose_bin_drift.py`
   via `sys.path.insert(...); import diagnose_bin_drift`. Check that module for import-time side
   effects (module-level code outside `if __name__ == "__main__":`) that could fire unexpectedly
   when imported rather than run as a script. Separately, confirm `paired_metrics`'s required inner
   keys (`radar_hr`, `masimo_pr`, `error`, `ahet_verified`) map cleanly onto what
   `score_offline.py` would actually supply per window — in particular, is `ahet_verified` the same
   thing as `hr_valid` from `run_window_dsp`'s output, or a different field that the plan is
   conflating?
8. **The sanity-check step's validity.** Plan's verification step 2 compares each session's raw
   `hr_valid` fraction (before Masimo gating) against the previously-measured 17.6%/54.9%/23.2%
   production coverage figures as a consistency check on the new window-slicing path. Those
   figures came from replaying via `scripts/live_demo.py` (a 3 s-hop **sliding** window, per
   HANDOFF), while this plan's grid is **non-overlapping** 30 s windows. Is comparing these two
   numbers actually a meaningful sanity check, or a structural mismatch that would produce a false
   alarm (or a false pass) regardless of whether the new script is correct?
9. **Scope discipline.** Confirm the plan's own "explicitly out of scope" list (BR paced metronome
   cross-check, natural/paced separate reporting, multi-subject weighting/bootstrap) is honored
   throughout, and that nothing in the core HR/BR scoring path silently depends on one of those
   being present.
10. **Test-plan adequacy.** Do the proposed synthetic tests actually exercise the waterfall's
    mutual-exclusivity, the exact-boundary cases (24/30 usable, spread exactly 5.0/2.0 bpm), and the
    `iq_swap` mismatch guard — or would a bug in the waterfall accounting or a boundary
    off-by-one survive them?
11. Anything else that would let this script's first MAE/RMSE/coverage number mislead the guard_v1
    promotion decision: an unsourced constant, a step that doesn't trace to a committed script per
    CLAUDE.md §3, an implicit assumption about what "the same DSP as production" means here.

### Evidence you may use
`notes/comparator_prespec.md`, `notes/comparator_prespec_br.md`, `src/m4/window_grid.py`,
`src/window_pipeline.py`, `src/warmup_select.py`, `src/masimo.py`, `src/compare.py`,
`scripts/diagnose_bin_drift.py` (read-only — inspect `classify_window_outcome` and any module-level
code), `HANDOFF.md`, `results/live_demo/*/run_metadata.json` (read-only, to verify claims about
`start_wall_utc`/`locked_bin`/`iq_swap`). Run the test suite only if a claim depends on current
state: `conda run -n radar-vitals python -m pytest tests/ -q`. Do not run capture, replay, or any
script that writes outside the review file — no code has been written yet for this plan, so there
is nothing to run against real data.

### Hard constraints on you
- **Do NOT write code and do NOT implement this plan.** This is a plan review.
- Do NOT edit `plans/offline_scoring_script.md`, the frozen specs, `HISTORY.md`, `HANDOFF.md`, or
  any source file. Your ONLY write target is the `COMMENTS OF CODEX` section of
  `plans/offline_scoring_script_cross_review.md`.
- Verification is read-only: reading files, `git log`/`git show`, running the test suite if a claim
  needs it. Do NOT run capture, replay, hardware, or any script that writes outside the review file.
- **No tuning to the reference** (CLAUDE.md §4): any gate/threshold must trace to the frozen spec,
  never to what makes the radar output look better. Flag any leak of this, including in the
  proposed test design.
- Environment gotchas if you run anything: `conda run -n radar-vitals python -m pytest tests/ -q` —
  conda is at `C:\ProgramData\anaconda3\condabin\conda.bat` and is **not on PATH**. Do NOT invoke the
  env's `python.exe` by absolute path (matplotlib then hard-kills on `savefig`, exit 127, no
  traceback). Multi-line `python -c` under `conda run` silently produces no output — write a scratch
  `.py` file instead.

### Escalate rather than decide
- **Frozen content** (`notes/comparator_prespec.md`, `notes/comparator_prespec_br.md`, anything
  CLAUDE.md marks irreversible) → `ESCALATE: frozen content`. Report the conflict; propose no edit
  to the frozen document itself.
- **A genuine design choice that is the user's** (e.g. `locked_bin` pinning-vs-rerun-warmup as the
  default, the waterfall's priority ordering if the spec is genuinely silent on it, whether to
  include BR paced metronome scoring now rather than defer it) → `ESCALATE: requires user decision`.
  Lay out the options; do not pick one.
- **Ethics / human-subjects scope** → `ESCALATE: user/ethics board decision`.

### How to write findings
In `plans/offline_scoring_script_cross_review.md`, under `COMMENTS OF CODEX`, one block per finding:

```
### OSR-NN [Blocking|Should-fix] — <area>
ISSUE: <the defect, concretely — what the plan says, and what goes wrong if built that way>
AUTHORITY: <the rule/fact it violates — CLAUDE.md §, a frozen spec §, the plan's own §>
WANTED: <the specific change to the plan>
REVERSIBILITY: <cheap to fix now vs permanent once the guard_v1 promotion decision is made on it>
ESCALATE: <none | frozen content | requires user decision | user/ethics board decision>
```

IDs (`OSR-01`, `OSR-02`, …) are permanent. Order Blocking first. Replace the
`(awaiting Codex's review pass)` placeholder with your comments.

### The loop
- Claude Code polls `plans/offline_scoring_script_cross_review.md` periodically, moves each of
  your comments into `DEBATE COMMENTS` with a verified response, and applies fixes it agrees with.
- Re-read the file each pass; if unconvinced by a response, add a new one and increment the round.
  Hard cap **3 rounds per comment**, then it escalates to the user.
- Work in reasonably sized batches and save as you go, so the file is consistent whenever it's
  polled.
- When you have no further findings, replace the `COMMENTS OF CODEX` body with the exact string
  `NO MORE COMMENTS` plus a one-paragraph closing assessment. **Implementation of
  `src/comparator.py` and `scripts/score_offline.py` begins only after this loop closes.**
