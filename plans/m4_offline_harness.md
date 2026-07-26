# M4 — Offline evaluation harness (HR + BR): implementation plan

> **Status: DRAFT, awaiting cross-model review (CLAUDE.md §5.2 — plan → review → build).**
> Written 2026-07-26, after the linalg-free DSP review closed (`plans/m4_linalg_free_dsp_review.md`).
> Both M4 gates (M3, linalg review) are cleared. Nothing in this plan has been built yet.

---

## 1. What M4 is

One tracked entry point that turns a run folder into paper-grade agreement numbers for **both**
vitals, under **both** frozen comparators, on the frozen non-overlapping 30 s grid — writing config,
seed, git commit and input SHA-256 alongside every result (CLAUDE.md §3).

M4 is the acceptance harness for every later milestone: M0's CI machinery, M5's pilot, M6's study,
and the M8/M9/M10 method work all report through it. It is the single most reusable artefact in the
plan, so its correctness matters more than its speed.

---

## 2. Two decisions that set this plan's shape

### 2.1 The regression anchor — **Option A** (user decision, 2026-07-26)

`plans/implementation_plan.md` §M4's done-when says M4 must reproduce the pilot HR numbers
**0.19 / 0.50 / 0.53 bpm** "exactly". That anchor is **retired**. It cannot be met and should not be:

1. **No committed script produced those numbers** — there is nothing to reproduce, only something to
   guess at.
2. **They used a "nearest-hop" window rule** that `notes/analysis_prespec.md` §7 later froze
   differently (boundary-aligned, `[k·600, (k+1)·600)`).
3. **They were produced under the retired brick-wall band-pass** (2026-06-30 `1847d7f` → 2026-07-26),
   which the cross-review established was an unintended estimator change.

**Under Option A:** M4's scorer is validated against **synthetic windows with hand-computable
expected answers**, and the frozen-§7 outputs become canonical. The old numbers are recorded in
`HISTORY.md` as superseded. A reconstruction of the old rule (Option B) was declined: it would have
to guess an undocumented rule *and* resurrect the deleted filter to be a true bridge, and a
reconstruction tuned until it emits 0.19 proves nothing.

### 2.2 The existing captures cannot produce a frozen scoring number — **this is a scope constraint**

`notes/analysis_prespec.md` §7 is explicit:

> **The 4 existing exploratory captures lack a persisted `frame0_epoch`.** Any window alignment
> reconstructed for them from `start_wall_utc` is **APPROXIMATE** … and is used **only for
> reference-characterization design evidence**, never for a frozen scoring number.

`frame0_epoch` is bound to the UTC time at receipt of frame 0. `run_metadata.json`'s
`start_wall_utc` is written *before* DCA/IWR configuration (`live_demo.py:1213` vs `1300+`), so the
offset is the capture-startup latency — not sub-second, and not recoverable after the fact.

**Consequence, and it is stronger than "n=1 is descriptive":** running M4 on the three Masimo
captures produces a **validation and demonstration output, explicitly labelled non-scoring**. Even
with a perfect scorer and 10 subjects, alignment on *these four files* would remain inadmissible.

**This is a tension with `plans/implementation_plan.md` §M4's done-when**, which asks for numbers
from exactly these captures. Proposed resolution, for the reviewer to accept or reject: M4's
done-when is met by (a) synthetic validation, (b) an end-to-end run on the three captures emitting
**descriptive, alignment-flagged** HR and BR, and (c) the harness being ready to produce frozen
numbers the moment a capture carries a persisted `frame0_epoch`. **M2 done-when #5 ("M4 reports the
BR outcome under the frozen comparator") is satisfied by (b)** — the BR outcome is reported, with its
alignment status declared. See §7 for the forward requirement this places on M1/M5.

---

## 3. Inputs, and what must never be read

**Input:** a replay/live run folder. Canonical set for the first run is the **2026-07-26 post-filter-fix**
generation: `20260726_173434` (natural), `_173653` (paced16), `_173914` (sweep) — never the
2026-07-25 pre-fix dirs, which were produced by the retired filter.

| use | do not use | why |
|---|---|---|
| `hr_bpm_raw` | `hr_bpm_smooth` | online median smoother, not the validated estimator (CLAUDE.md §4) |
| `hr_bpm_raw` | `fallback_hr_bpm` | a naive argmax — it lies (HANDOFF §5) |
| `br_bpm` | — | |
| `ahet_verified`, `resp_valid` | — | false → radar-NaN, not "excluded" |
| `frame_idx` | `elapsed_s` | wall-clock, ~0 under `--replay-fast` |
| Masimo integer `Timestamp`/`epoch_utc` | `Date`/`Time` strings | CLAUDE.md §9 |
| `Beats / min` (PR), `Breaths / min` (RRp) | SpO2 / PI / PVi as a rate | CLAUDE.md §9 |

---

## 4. Architecture

Pure scoring logic separated from I/O so the whole comparator is unit-testable without a run folder.

```
src/scoring.py          NEW. Pure functions, no file I/O, no globals:
                          window_grid(n_frames)              -> [(k, lo_frame, hi_frame)]
                          reference_window(masimo, k, ...)   -> epoch span (half-open)
                          hr_reference(samples)              -> (value, gate verdicts)
                          br_reference(samples)              -> (value, gate verdicts)
                          agreement(pairs)                   -> MAE/RMSE/bias/n/coverage
                          bland_altman(pairs, subjects)      -> point estimate (+ CI when admissible)
scripts/run_m4_harness.py  NEW. Entry point: run folder(s) -> results/<experiment>/<timestamp>/
src/masimo.py           REUSE unchanged (parser, PI, PR, RR).
src/compare.py          NOT reused for scoring. It implements the OLD PI-gated-*mean* comparator
                        (`pr_bpm_mean`), which the frozen spec replaced with a median. Leave it in
                        place, mark it superseded; do not import it into the new path.
scripts/plot_bland_altman.py  REUSE FOR PLOTTING ONLY. Its statistics
                        (`se_loa = sqrt(3·SD²/n)`, no subject term) are invalid for repeated
                        measures and were never valid for the pilot either.
```

**Determinism:** no RNG except the cluster bootstrap, which takes an explicit seed logged with the
result. Same input + same config + same seed → byte-identical output.

---

## 5. The scoring rules, restated from the frozen specs

These are transcribed, not invented. Any disagreement with the source documents is a bug in this
plan, and the specs win.

### 5.1 Window grid (`notes/analysis_prespec.md` §7, FROZEN)
- 30 s = **600 frames** at 20 Hz. Windows are half-open frame intervals `[k·600, (k+1)·600)`.
- **`k = 0` IS scored.** Warmup bin-selection runs on the `k = 0` buffer and is applied back to it
  via `dsp_override`, so the window has a valid estimate.
- Window `k` is scored by the single estimate whose analysis window is exactly that interval —
  in `live_estimates.csv` the row with **`frame_idx = 600·k + 599`**. **Greedy selection of accepted
  hops is forbidden** (it maximises accepted windows and is estimator-dependent).
- A window is scored **iff** it is a complete 600 frames.
- Yield: 180 s → 6 windows; 480 s → 16; 600 s → exactly 20. *(Verified against the three captures:
  6 / 6 / 16.)*
- Reference span for window `k` is the half-open epoch interval `[E(k·600), E((k+1)·600))` with
  `E(i) = frame0_epoch + i/20`; a Masimo sample at integer epoch `e` belongs iff
  `E(k·600) ≤ e < E((k+1)·600)`. **HR and BR use the identical half-open rule** (M3R-40).

### 5.2 HR comparator (`notes/comparator_prespec.md`)
- **Reference:** median of **PI-gated** PR samples in the window (`PI ≥ 0.5`).
- **Gates, ALL required:** PI (drop samples `< 0.5`); **coverage ≥ 80 %** of the 30 expected samples
  surviving the PI gate; **stationarity** — exclude if `p90 − p10` of gated PR `> 5.0 bpm`.
- **Sensitivity table required:** exclusion fraction at **3 / 5 / 8 bpm**.

### 5.3 BR comparator (`notes/comparator_prespec_br.md`)
- **Reference:** median of **finite** RRp samples in the window.
- **Gates, ALL required:** **availability ≥ 24 finite `rr_bpm` samples**; **stationarity** —
  exclude if `p90 − p10` of finite RRp `> 2.0 bpm`.
- **PI is NOT a BR admissibility gate** — reported as a per-window flag / sensitivity covariate only.
  No manufacturer-validated PI threshold exists for RRp; borrowing the PR value would freeze an
  unsupported exclusion.
- **Paced sessions:** compute and report agreement against **both** RRp *and* the commanded metronome
  rate, clearly labelled. The metronome is **target-concordance, not physiological truth** — RRp
  remains the measured reference and disagreement is not resolved in the metronome's favour.
- **Natural sessions:** RRp only. Use the words "compared with", never "validated against".

### 5.4 Reporting (both vitals)
- **Coverage alongside accuracy, always.** Emit total windows, excluded-by-PI (HR) /
  excluded-by-availability (BR), excluded-by-coverage, excluded-by-non-stationarity, radar-NaN, final n.
- **Radar-NaN is not an exclusion** — it is a covered window the radar declined to estimate, and it
  belongs in the coverage denominator. Conflating the two inflates apparent accuracy.
- **Bland–Altman:** the constant `μ ± 1.96·SD` **point estimate** is primary and never switched
  post-hoc. Its CI is the **whole-subject cluster bootstrap** (M3R-29 Option A), which **requires
  ≥ 2 subjects** — on the pilot it must be **suppressed, not degenerate**. MOVER is a pre-named
  *candidate* sensitivity, reported only after implementation + statistician math-review (M3R-46);
  **not in this build**.
- **Distance:** descriptive only, and **not recorded in the 4 existing captures** — this breakdown
  applies from M5 onward.

---

## 6. Build order — scoring core and tests before any capture is scored (CLAUDE.md §5.3)

Each stage is unit-tested and committed before the next begins.

| # | Stage | Done when |
|---|---|---|
| 1 | **Window grid.** `window_grid`, frame↔window mapping, `frame_idx = 600k+599` lookup, incomplete-tail rejection. | Hand-checked yields 6/6/16/20; a partial trailing window is dropped; a missing `frame_idx` row raises rather than silently skipping a window. |
| 2 | **Reference aggregation + gates**, HR and BR, on **synthetic Masimo frames** with hand-computed expected values. | Every gate fires exactly at its boundary (PI 0.5, coverage 24/30, stationarity 5.0 / 2.0 bpm), tested from both sides. Median-vs-mean divergence asserted on a skewed window. |
| 3 | **Metrics.** MAE/RMSE/bias/n, the coverage ledger, the 3/5/8 stationarity sensitivity table. | Metrics match hand arithmetic on a fixture; the ledger's categories sum to the window total exactly. |
| 4 | **Bland–Altman.** Point estimate; cluster bootstrap behind an `n_subjects ≥ 2` guard. | Point estimate matches hand arithmetic; bootstrap **refuses** on 1 subject with a clear message rather than emitting a degenerate interval. |
| 5 | **Harness wiring + provenance.** Run folder → `results/<experiment>/<timestamp>/` with config, seed, git commit, input SHA-256, and an explicit `alignment: APPROXIMATE` flag. | Re-running reproduces byte-identical output; the alignment flag is present and true. |
| 6 | **Run on the 3 Masimo captures.** | HR recomputed under the frozen grid; **BR emitted for the first time**; both labelled descriptive / non-scoring per §2.2. Closes **M2 done-when #5**. |

**Synthetic validation is the Option A backbone.** Stage 2–4 fixtures are built so the correct answer
is computable by hand — e.g. a window of 30 PR samples with a known median, a known `p90 − p10`, and
a known count below PI 0.5 — so a scorer bug cannot hide behind plausible-looking output. This is what
replaces "reproduce 0.19 exactly".

---

## 7. Forward requirement this creates (M1 / M5)

For any capture to yield a **frozen** scoring number, the capture path must persist:

1. **`frame0_epoch`** — synchronised-clock UTC at receipt/assembly of frame index 0. **Not**
   `start_wall_utc`.
2. **A per-frame validity / zero-fill map** — not merely aggregate `n_dropped` /
   `zero_filled_bytes`, which cannot identify *which* windows are affected. A window containing any
   dropped or zero-filled frame is flagged from that map and its estimate treated as radar-NaN.

Neither exists today. **This should be implemented before M1's smoke test**, so M1 validates the
capture path that M5/M6 will actually rely on. Flagged here because it is easy to discover too late.

---

## 8. Explicitly out of scope

- Reproducing 0.19 / 0.50 / 0.53 (§2.1).
- MOVER CI (M3R-46 — needs statistician review first).
- Per-distance agreement claims (not recorded in these captures; forbidden without amendment).
- Promoting `guard_cardiac_candidate_v1` (blocked on `experiments/exp_eca_modes`).
- Re-measuring the "34 % of hops" paced-16 decoy figure — related, still open, but not M4.
- Fixing `elapsed_s` in `live_demo.py`. M4 is made **immune** to it by using `frame_idx` throughout;
  the underlying bug stays recorded.

---

## 9. Questions for the cross-reviewer

1. **§2.2 is the big one.** Is the proposed done-when resolution right — synthetic validation +
   a descriptive, alignment-flagged run — or does the approximate `frame0_epoch` make even a
   *descriptive* BR number from these captures misleading enough to withhold? M2 #5 hangs on this.
2. Is `frame_idx = 600·k + 599` the correct reading of "the estimate whose analysis window is exactly
   `[k·600, (k+1)·600)`"? It assumes `frame_idx` is the **last** frame of the analysis buffer —
   consistent with the first CSV row being `frame_idx = 599`, but worth an independent check.
3. Is putting **radar-NaN in the coverage denominator but not the exclusion ledger** the right
   reading of both specs' "report coverage alongside accuracy"?
4. Should the harness **refuse to run** on a folder whose git commit predates the filter fix, rather
   than trusting the operator to pick the 2026-07-26 dirs?
5. Anything in §5 that misreads a frozen spec. Those are transcription bugs and the specs win.
