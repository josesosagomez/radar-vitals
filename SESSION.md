# Session Memory

> Update at the END of every working session. Newest entry on top.
> Three questions: What worked (with evidence)? What failed? What's next?

---

## Current state

- **Where we are:** exp002 closed. Masimo parser fully fixed (half-open interval +
  deduplication). ECA+AHET pipeline confirmed correct. 32/32 tests pass.
  **Blocker 1 resolved:** `eca.k_max` and `eca.ahet_deviation_hz` are now actively
  wired from config through `run.py` → `run_pipeline_locked()` → `estimate_rate_from_phase()`
  → `eca_project()`.
  **Blocker 2 resolved:** exp002 now writes validated per-window evidence to
  `intermediates.npz` and alignment/AHET scalars to `comparison.csv`. The latest
  cross-artifact test passed on
  `results/exp002_harmonic_rejection/20260614_160204/` with 21 aligned rows.
  **Blocker 3: deferred** — criterion documented as known limitation (soft ratio
  threshold, no rejection population in exp002, window-length-dependent search-region
  bin counts); full evaluation deferred until second capture collected. exp004 may
  proceed with explicit caveat.
  **Blocker 4: open** — refine the no-ECA fallback peak beyond the raw FFT bin centre.
- **Last verified result (post all fixes):**
  - exp001 baseline (no ECA): MAE 6.202, RMSE 8.773, bias −4.457 bpm (N=21).
    Evidence: `results/exp001_offline_baseline/20260614_133858/`.
    (Supersedes previous canonical: MAE 6.214, RMSE 8.778, bias −4.448 bpm at `20260614_120843/`;
    difference < 0.015 bpm on all metrics — deduplication effect.)
  - exp002 all windows (ECA+AHET): MAE 5.189, RMSE 6.902, bias −2.228 bpm (N=21).
  - exp002 AHET-verified: MAE 5.326, RMSE 7.051, bias −2.217 bpm (N=20).
    Evidence: `results/exp002_harmonic_rejection/20260614_133839/`. Run: 2026-06-14.
- **Open question / blocker:** Respiratory-dominance failure mode documented (windows 9-12,
  ~60 bpm harmonic passes AHET; physics limit of single-bin extraction). Second capture
  required to assess generalisation before any paper-grade claim.

---

## Log (newest first)

### 2026-06-14 — Pre-exp004 readiness review

**Overall verdict: Not ready.** The variable-length DSP and per-condition NPZ
serializer are ready, but the experiment orchestration, paired analysis,
reproducibility logging, and permanent tests required for exp004 are not yet present.
This was a review only; no implementation files were changed.

**1. Pipeline correctness for variable window lengths — Ready**
- `run_pipeline_locked()` has no 400-sample-only array or FFT assumption. ECA,
  filtering, Hann windows, rFFT axes, AHET attempt spectra, and frame metadata derive
  from the supplied `window_frames`.
- Direct production-path checks succeeded for 20/25/30 s windows. For 19 matched
  windows, phase shapes were `(19,400)`, `(19,500)`, `(19,600)` and heart-spectrum
  shapes were `(19,201)`, `(19,251)`, `(19,301)`.
- Full windows are selected by `range(0, n-window_frames+1, hop_frames)`; partial
  terminal windows are dropped.

**2. Config and runner architecture — Blocking gap**
- No exp004 runner/config or common-center constructor exists. The current pipeline
  only produces a regular grid starting at frame zero of the supplied cube.
- The primary matched grid must be constructed and asserted explicitly:
  20 s absolute starts `100..1900`, 25 s `50..1850`, and 30 s `0..1800`, all in
  100-frame steps after the 30 s trim. These produce the same 19 centers,
  `300..2100` frames (15..105 s). Running each length from the same start would
  instead produce unmatched center grids; simply offsetting the 20 s input also
  produces an extra center at 110 s unless its support is cropped.
- A single exp004 config can cleanly contain the three lengths plus fixed hop and
  common-center support. The runner should expose a reusable per-condition function
  rather than copy the entire exp002 `main()`.
- Reproducibility is currently blocking: the relevant Blocker 1/2 source, serializer,
  and tests are uncommitted; runners save the config but not git revision/dirty
  state, input hashes, runtime/package versions, or stdout. W&B is disabled although
  `CLAUDE.md` requires it. Commit the reviewed implementation and add provenance
  logging before generating exp004 results.

**3. Metrics and comparison correctness — Blocking gap**
- `src/compare.py` computes only independent per-table MAE/RMSE/bias. It has no
  common-center join, pairwise accepted-center analysis, intersection analysis,
  AHET transition counts, or fixed-denominator coverage calculations.
- `compare.metrics()` excludes NaN radar HR but not NaN Masimo reference/error. A
  finite HR with a NaN reference is counted as a valid window and silently yields
  NaN metrics. Exp004 analysis must require finite HR and finite reference/error,
  assert unique identical center keys, and report every exclusion count.
- Pair on an integer center-frame key, not inferred row order or approximate float
  epochs. Report the predeclared 19-center denominator independently of conditional
  metric sample counts.

**4. Intermediate persistence — Ready**
- `src/intermediates.py` correctly permits different phase/spectrum shapes in
  separate archives while enforcing fixed shapes within one condition.
- Direct NPZ round trips passed at all three lengths with `allow_pickle=False` and
  identical center epochs. No serializer modification is required if exp004 writes
  one archive per condition and adds correct start/end epochs before serialization.

**5. Test coverage — Blocking gap**
- Full current suite passed: **32/32**.
- Production ECA/AHET tests use only `N=400`; there is no committed test for 500/600
  samples, exact common-center construction, the separate 21-window baseline versus
  19-window matched grid, paired NaN filtering, AHET transitions, or per-condition
  artifact alignment.
- `test_exp002_artifacts.py` discovers the latest local result directory, so it is
  not hermetic or reproducible in a clean checkout. Keep a synthetic artifact test
  and make any real-run integration check require an explicit artifact path.

**6. SESSION.md accuracy — Minor gap**
- The current canonical metrics and Blocker 1/2/3 statuses match the source and saved
  artifacts. The 32-test claim was reverified.
- The current-state text calls `20260614_160204` the latest cross-artifact evidence,
  but `20260614_161352` is newer and is what the dynamic artifact test currently
  selects. Its metrics agree to approximately `1e-6` bpm.
- `notes/approach.md` still contains stale baseline descriptions (phase-variance bin
  selection, 10 s hop, older metrics/evidence path) that conflict with the current
  energy selector, 5 s hop, and canonical results. Update these before exp004 is
  documented as reproducible.

**Blocking changes before exp004:**
1. Implement and assert the exact 19-center condition grids.
2. Implement paired/common-intersection metrics, coverage, transitions, and strict
   finite-reference handling.
3. Commit the current reviewed foundation and log complete run provenance/W&B.
4. Add permanent 20/25/30 s, common-center, paired-statistics, and artifact tests.

---

### 2026-06-14 — Blocker 3 deferred — AHET criterion documented as known limitation

**Current pass rule:** `second_peak_magnitude > comparison_floor` where `comparison_floor`
is the median cardiac-band magnitude on the second-pass ECA spectrum (ratio > 1.0).

**Known limitations established by four Codex review rounds:**
- exp002 has 20 AHET passes, 0 AHET-rejection failures, 1 respiratory fallback — no
  rejection population exists; the criterion cannot be validated or tuned from this
  capture alone.
- Accepted ratio distribution on exp002: min 1.035, median 1.233, max 2.385; 16/20
  passes fall in [1.0, 1.5] — the criterion is very soft.
- 5 verified windows still have absolute HR error ≥ 10 bpm — AHET verification does
  not guarantee HR accuracy at this threshold.
- The ±0.1 Hz search region contains ~5/5/7 FFT bins at 20/25/30 s windows — longer
  windows have more bins in the search region, creating a window-length-dependent
  multiple-comparison effect on pass rates.
- Raising the threshold to 1.5 would retain only 4/20 estimates (80% rejection); any
  threshold tuning requires independent capture data.

**Decision:**
- Blocker 3 deferred until second capture is collected.
- Full Monte Carlo evaluation (development/held-out split, paired window crops, ROC
  curves for predeclared alternative criteria) to be implemented alongside exp004
  validation on the independent capture.
- exp004 pass-rate comparisons must carry explicit caveat: "AHET pass rates are
  indicative only; criterion not independently validated; window-length-dependent
  search-region bin counts (~5/5/7 at 20/25/30 s) may inflate pass rates at longer
  windows."

**Blocker status:**
- Blocker 3: deferred (documented caveat; full evaluation after second capture)
- Blocker 4: still open (no-ECA fallback parabolic refinement)
- exp004: unblocked; may proceed with the caveat above recorded in SESSION.md

---

### 2026-06-14 — Blocker 3 AHET criterion plan v3 review

**Verdict:** Substantially improved, but not implementation-ready. The held-out
design, Wilson intervals, oracle/estimated-respiration split, and explicit candidate
recall are good additions. The following inconsistencies must be corrected first:

1. The evaluator must use the production FFT (`n_fft == window samples`), not
   next-power-of-two zero padding. Production uses 400/500/600-point rFFTs. At a
   representative 1.2 Hz candidate, zero padding changes the +/-0.1 Hz search from
   5/5/7 bins to 6/6/11 bins and therefore changes the null false-pass mechanism.
2. Maintain two separate outcome axes:
   - verifier validity: genuine candidate-specific second-harmonic evidence present
     or absent;
   - final estimator accuracy: accepted HR within or outside +/-5 bpm.
   In `missing_harmonic`, an accepted but accurate fundamental is an accurate HR
   estimate but a false AHET verification. In `noise_only`, HR correctness is
   undefined. Report both; do not overload “wrong pass.”
3. The existing production function returns at the first C1 pass, so it does not
   compute all three candidate-specific spectra needed to compare criteria fairly.
   Add a behavior-preserving shared evidence-extraction/scoring helper in
   `src/vitals.py` (with regression tests) or explicitly justify and test an exact
   duplicate. Therefore `src/vitals.py` cannot remain “only if criterion changes.”
4. Pair window lengths within each synthetic trial: use the same physiological
   parameters and continuous noise realization, center-cropped to 20/25/30 s.
   The current per-length off-bin draw otherwise compares different cases. Define
   deterministic seed derivation and clarify whether N=500 is total per cell or per
   each listed seed.
5. Finish specifying the model: respiratory harmonic phases, exact sigma/SNR
   equation, matched nominal noise levels for `noise_only`, constrained generation
   of respiratory overlap/competitor cases, and the chirp phase/truth definition
   for frequency drift. The current `resp_competitor` still contains a genuine
   cardiac second harmonic, so it does not test false respiratory support.
6. C2/C3 must respect the 4.0 Hz production bandpass edge. For candidates near
   2.0 Hz, upper guard bins lie in the stopband but still exist in the rFFT, so the
   proposed “missing bins” edge fallback will not fire and can create an
   artificially low floor. Define a valid local-background domain and one-sided or
   unavailable behavior explicitly.
7. ROC curves require score threshold sweeps for C1/C2 as well as C3; otherwise
   show C1/C2 as operating points. Define zero-floor behavior and exact minimum
   local-bin requirements with direct formula tests.
8. Add a held-out sensitivity condition to adequacy, not only a false-pass bound.
   Specify whether constraints apply per window length and to oracle, estimated
   respiration, or both. Define the development aggregation, tie breakers, and the
   selection rule if both C2 and C3 qualify. With N=500, a two-sided Wilson upper
   bound is below 0.10 only through 35/500 observed false passes (7%).
9. Replace “confirmed adequate” with “passes the predeclared synthetic operating
   envelope.” Synthetic data cannot establish real-capture adequacy.
10. Add a tracked evaluation config, `notes/approach.md` update, W&B logging/run ID,
    script/config hashes and dirty-worktree provenance. Save audits under an
    immutable timestamped subdirectory. Persist sharded per-trial intermediate
    evidence, not only aggregate CSV rows, to satisfy the project evidence rule.
11. Strengthen tests with hand-constructed exact C1/C2/C3 score cases, signal/SNR
    formula checks, condition-constraint tests, and a production-C1 regression.
    “Finite and recorded” and array-length assertions alone do not validate the
    formulas.

**Approved elements:** descriptive exp002 audit with explicit run directory,
development/held-out separation, actual search-bin accounting, Wilson intervals,
criterion-isolated plus production-respiration analyses, and independent-capture
confirmation.

---

### 2026-06-14 — Blocker 3 AHET criterion plan v2 review

**Verdict:** Directionally correct but not implementation-ready. The real-data audit
is now appropriately descriptive, but the synthetic protocol and decision rule
remain underspecified. No source code changed during this review.

**Required revisions:**
1. Define outcome labels at the window level:
   - correct pass = finite accepted estimate within a predeclared tolerance of true HR;
   - wrong pass = finite estimate outside tolerance or any pass in a no-cardiac trial;
   - rejection, coverage, and top-3 candidate recall reported separately.
   A pass in `genuine_harmonic` is not automatically a true pass.
2. Specify the exact signal model: equations, randomized frequency ranges, phases,
   respiratory-harmonic amplitudes, cardiac fundamental/second-harmonic amplitude
   ratio, noise distribution/color, and drift trajectory. Use off-bin frequencies
   and include band-edge strata.
3. Split fundamental SNR from second-harmonic-to-noise ratio. `noise_only` has no
   cardiac SNR, so pair it with explicitly defined noise variances from the positive
   scenarios. State whether f_r is estimated by the full pipeline or supplied as
   oracle; preferably report both criterion-isolated and end-to-end results.
4. Correct the respiratory scenarios:
   - respiratory competitor: k*f_r is in the heart band but separated from true HR;
   - respiratory overlap: k*f_r lies within a defined tolerance of true HR.
   `4*f_r ~= f_c` alone does not define a wrong candidate.
5. Fully define alternative criteria before seeing results. Raw prominence is not
   normalized. Define local-background windows, excluded guard bins, edge handling,
   normalized scores, and threshold grids. If thresholds are swept, produce ROC/PR;
   otherwise call the plot an operating-point plot.
6. Ensure all criteria score the same ordered top-3 candidates and candidate-specific
   spectra. Apply each criterion sequentially to select its first passing candidate.
   Record candidate recall so candidate-generation failures are not attributed to
   the verification criterion.
7. Increase Monte Carlo size and report Wilson/binomial confidence intervals.
   With 20 false passes in 200 trials, the 95% Wilson interval is approximately
   0.066-0.149. Use a predeclared CI-based adequacy rule (for example, upper 95%
   bound <0.10), not the observed proportion alone.
8. The adequacy rule must constrain both sensitivity and false passes across the
   intended SNR/harmonic-strength range, respiratory competitors, and all window
   lengths. Coverage cannot substitute for correctness.
9. “Select the best tradeoff” is post-hoc tuning. Use fixed development seeds to
   choose criterion/threshold under a predeclared objective, then evaluate once on
   held-out seeds. Keep independent real captures as the final confirmation.
10. The fixed +/-0.1 Hz search includes roughly 5, 5, and 7 bins at 20, 25, and
    30 s for representative frequencies. Under an idealized null, a maximum of m
    independent bins exceeds a median with probability `1 - 0.5^m` (about 0.969,
    0.969, 0.992). Report search-bin count and explicitly test this
    window-length-dependent multiple-comparison effect.
11. Save a committed simulation config, seed/seed derivation, git commit, package
    versions, script/config hashes, and source artifact hashes. Use a timestamped
    audit subdirectory rather than modifying a prior run in place.
12. Unit-test exact criterion formulas and deterministic signal construction.
    Monte Carlo rate assertions should be limited to stable smoke/integration checks,
    not serve as the sole correctness tests.

**Descriptive audit clarification:** define “marginal at threshold” as the number
and fraction of currently accepted estimates that would remain/reject under that
hypothetical threshold; do not imply threshold validation.

---

### 2026-06-14 — Blocker 3 AHET criterion plan v1 review

**Verdict:** Not approved as written. The proposed exp002 audit is useful as a
descriptive failure analysis, but it cannot validate or tune the AHET criterion.
No source code changed during this review.

**Why the current validation logic is circular:**
- Pass/fail is exactly `peak_to_floor_ratio > 1`. Therefore accepted candidates
  necessarily have ratio >1 and attempted rejected candidates have ratio <=1.
  Pass/fail distribution separation and most “criterion inversion” checks merely
  restate the implementation.
- AHET stops at the first passing candidate. Earlier attempted candidates failed
  by definition and later candidates are not evaluated, so asking whether the
  accepted candidate has the highest attempted ratio is not an independent test.
- The latest exp002 artifact has 20 AHET passes, zero AHET-rejection windows, and
  one f_r-outlier fallback with no AHET attempts. There is no failed-window
  population from which to estimate rejection performance.

**Observed exp002 evidence (`20260614_161352`):**
- Accepted ratio: min 1.035, median 1.233, max 2.385
  (0.30, 1.82, 7.55 dB).
- 16/20 verified windows lie in the proposed `[1.0, 1.5]` marginal range.
- Five verified windows have absolute HR error >=10 bpm; their ratios span
  1.060-1.233. The ratio confirms harmonic structure but not cardiac correctness.
- A threshold of 1.5 would retain only 4/20 verified outputs. Its lower MAE would
  primarily reflect selective abstention, not improved estimates.

**Statistical/algorithmic issue requiring a null study:**
- The statistic is the maximum over several second-harmonic bins compared with
  a median from the cardiac band. Even under equal independent noise, the chance
  that a maximum of `m` bins exceeds a median is `1 - 0.5^m`; it increases as
  longer windows place more bins in the fixed +/-0.1 Hz search interval.
- The cardiac-band floor is measured in a different frequency region from the
  second harmonic, so spectral background/filter response may differ.

**Required revised plan:**
1. Keep exp002 as a descriptive audit only: ratio margin, accepted rank, absolute
   error, known wrong passes, and coverage. Do not select a new threshold from it.
2. Add fixed-seed synthetic Monte Carlo tests at 20/25/30 s:
   cardiac+second-harmonic positives, no-second-harmonic/null signals,
   respiratory-harmonic coincidences, noise levels, and small frequency drifts.
   Report false-pass, true-pass, and coverage by window length.
3. Compare the current cardiac-band-floor ratio with predeclared alternatives such
   as second-harmonic local prominence or peak/local-median ratio. Do not use raw
   absolute magnitude because FFT magnitude scales with window length.
4. Any threshold/criterion change is a hypothesis selected on synthetic/development
   data and must be confirmed on independent captures. Absence of A/B/C on exp002
   cannot “confirm adequate.”
5. Require explicit `--run-dir`; never silently choose “most recent.” Write
   `ahet_audit.csv`, `audit_summary.json`, and plots inside a run-specific audit
   directory with source artifact/config/git/hash provenance.
6. Unit-test importable audit functions using temporary synthetic artifacts; keep
   real-result execution as an integration check rather than a test that writes
   into existing results.

---

### 2026-06-14 — Blocker 2 resolved: intermediate persistence verified end to end

**Worked:**
- `run_pipeline_locked()` evidence is now persisted by exp002 through
  `src/intermediates.py` to `intermediates.npz`; accepted AHET and alignment scalars
  are also present in `comparison.csv`.
- Latest verified artifact:
  `results/exp002_harmonic_rejection/20260614_160204/`.
- Cross-artifact test loads the most recent timestamped exp002 result and verifies all
  21 windows: NPZ/CSV row counts and `ahet_verified` agree, frame bounds stay within
  the trimmed radar cube, attempted candidates form a contiguous prefix, and every
  non-verified window uses `accepted_candidate_rank == -1`.
- `intermediates.npz` loads with `allow_pickle=False`; no object arrays are present.
- Full suite: **32/32 passed** with `pytest tests/ -v`.

**Metric verification caveat:**
- Re-runs preserve window counts, AHET decisions, reference values, and metrics to
  within `1.1e-6` bpm of canonical run `20260614_133839`, but not bit-for-bit.
- Replaying the pre-instrumentation estimator produces the same current values, so
  the difference is numerical-environment provenance rather than persistence wiring.
  The canonical artifact did not record enough library/runtime detail to reproduce
  its final floating-point digits exactly.

**Still open:**
- Blocker 3: verify/strengthen the AHET second-harmonic comparison criterion.
- Blocker 4: add parabolic refinement to the no-ECA fallback peak.

---

### 2026-06-14 — Blocker 2 persistence plan v4 review

**Verdict:** Not yet implementation-ready; two substantive schema issues remain.
No source code changed during this review.

**Required corrections:**
1. `candidate_initial_hz` is defined both as a scalar and as a `(3,)` attempt array.
   A dict/NPZ cannot contain both. Remove the scalar; the primary candidate is
   `candidate_initial_hz[:, 0]`. Keep the accepted-candidate scalar names.
2. Successful windows retain only accepted second-pass `heart_spectrum`; the
   first-pass `spec1` that generated/ranked candidates is lost. Add
   `heart_spectrum_first_pass` `(N_fft,)` (NaN on no-ECA fallback). Keep
   `heart_spectrum` as the stage-selected output for backward compatibility.
3. To reproduce candidate generation/ranking exactly, add per-attempt
   `candidate_peak_bin_index` (integer, `-1` unused),
   `candidate_peak_magnitude`, and `candidate_prominence`. Add a boolean/source
   marker for the argmax fallback used when `find_peaks` returns no peaks.
4. Clarify that `resp_peak_raw_index` is the global rFFT index. Use integer `-1`
   only where that field can be unavailable.
5. Strengthen serializer validation:
   `end_frame - start_frame == window_frames`,
   `end_epoch - start_epoch == window_frames / frame_rate_hz` within tolerance,
   contiguous unique `window_index`, and all required keys present.
6. Refining `candidate_refined_hz` and `second_peak_refined_hz` for failed
   region-available attempts is additive diagnostic computation; state this
   explicitly so reviewers know it does not alter pass/fail behavior.

**After these changes, the persistence plan is implementation-ready.**

---

### 2026-06-14 — Blocker 2 persistence plan v3 review

**Verdict:** Nearly approved. One final schema clarification is required before
implementation; no source code changed during this review.

**Required final corrections:**
1. Candidate frequency fields remain ambiguous and duplicated. Replace the generic
   per-attempt `candidate_hz` with:
   - `candidate_initial_hz` `(3,)`: refined from first-pass `spec1`; this frequency
     defines the ECA guard and second-harmonic search interval.
   - `candidate_refined_hz` `(3,)`: fundamental refined on candidate-specific
     second-pass `spec2`.
   Remove the scalar fields with those names.
2. Accepted-candidate CSV/NPZ scalars must be explicit:
   `accepted_candidate_initial_hz`, `accepted_candidate_refined_hz`,
   `accepted_second_harmonic_refined_hz`, and final `heart_peak_hz`.
3. Distinguish the second-harmonic frequency used to locate the tested magnitude
   from the refined frequency used in the final blend:
   `second_peak_bin_hz` `(3,)` and `second_peak_refined_hz` `(3,)`.
   The pass criterion uses magnitude at the bin peak, not the interpolated value.
4. Add `resp_peak_raw_index` and optionally cardiac/second-harmonic bin indices so
   exact FFT choices can be reconstructed without nearest-frequency inference.
5. Use integer sentinel `-1` for `accepted_candidate_rank` in NPZ (and document an
   empty/NaN CSV representation if desired), rather than storing an inherently
   integer identifier as float.
6. Do not add NaN `start_epoch`/`end_epoch` placeholders inside
   `run_pipeline_locked()`. These fields belong solely to the runner. Define
   `phase_eca` as corresponding to `heart_spectrum_stage`: NaN for stage 0,
   first-pass residual for stage 1, accepted second-pass residual for stage 2.
7. Put the production stacking/writing helper in a reusable module such as
   `src/intermediates.py`, rather than burying it in the exp002 runner, because
   exp004 is intended to reuse it. The helper should validate row counts, fixed
   shapes, metadata alignment, and non-object dtypes before writing.

**After these naming/ownership changes, the plan is implementation-ready.**

---

### 2026-06-14 — Blocker 2 revised persistence plan review

**Verdict:** Substantially corrected, but one more revision is required before
implementation. No source code changed during this review.

**Remaining required changes:**
1. Persist respiratory-estimator evidence because refined `f_r_hz_used` determines
   the ECA subspace: `resp_freqs_hz`, `resp_spectrum`, raw-bin peak frequency/index,
   and refined respiratory frequency.
2. Separate AHET frequency meanings:
   - `candidate_initial_hz`: first-pass `spec1` candidate used to define the local
     second-harmonic region and candidate-specific ECA guard.
   - `candidate_refined_hz`: fundamental refined on candidate-specific `spec2`.
   - `accepted_candidate_rank`: attempt slot/rank in magnitude-sorted candidates.
   - `heart_peak_hz`: final blend of refined fundamental and halved second harmonic.
   Do not use ambiguous `candidate_hz` / `accepted_candidate_hz` names.
3. Add `candidate_attempted` boolean. Numeric padding is NaN; boolean padding is
   False. For the f_r-outlier path, all candidate numeric fields are NaN and all
   candidate booleans are False.
4. Define `region_available` as “the local search interval contains at least one
   FFT bin,” not “the region is within band.”
5. Define zero-floor ratio behavior without changing AHET: keep pass criterion
   `peak > floor`; ratio is `peak/floor`, with NaN for 0/0 and +inf for positive/0.
6. `heart_spectrum` currently has mixed stage semantics: no-ECA spectrum on fallback,
   first-pass ECA spectrum on failure, accepted second-pass spectrum on success.
   Persist a numeric `heart_spectrum_stage` code and, to independently audit all
   AHET attempts, persist candidate-specific `ahet_attempt_spectrum` with shape
   `(n_windows, 3, n_fft)` (NaN-padded). Candidate-specific ECA residuals are useful
   but optional if the attempt spectra are retained.
7. Epochs are runner metadata, not values known inside `run_pipeline_locked()`.
   Add start/end epochs in `exp002/run.py`; frame bounds remain pipeline outputs.
8. Test the real production serializer/helper, not a mock NPZ assembled separately.
   The round-trip test must exercise the same stacking/NaN-padding code called by
   `exp002/run.py`.

**Confirmed correct:**
- Existing list-of-dicts API remains unchanged.
- One compressed NPZ per run/condition is appropriate.
- Full windows have fixed lengths; exp002 shapes are phase `(21,400)` and spectrum
  `(21,201)`.
- Canonical metric target `20260614_133839` is correct.
- Existing suite passes 21/21.

---

### 2026-06-14 — Blocker 2 persistence plan cross-review

**Verdict:** Approved in principle after required schema corrections; no source code
changed during this review.

**Corrections required before implementation:**
1. `run_pipeline_locked()` already returns `list[dict]`, not a scalar. Keep that API
   unchanged and add diagnostic keys only. exp001 requires no unpacking change.
2. Use the existing canonical names `ahet_verified`, `phase_clean`,
   `phase_unwrapped`, `heart_spectrum`, `heart_freqs_hz`, and `phase_eca`. The stored
   spectrum is magnitude, not power. `phase_eca` is the widened-band filtered ECA
   residual, not raw phase after projection.
3. AHET can try up to three candidates. Persist fixed-width `(n_windows, 3)` attempt
   arrays for candidate frequency, second-harmonic peak frequency/amplitude, comparison
   floor, peak-to-floor ratio, region-available flag, and pass flag. Also persist
   accepted candidate rank/frequency. A single accepted-candidate record cannot audit
   failed or changed decisions.
4. Do not label the current test as SNR. It compares second-harmonic magnitude against
   the median cardiac-band magnitude. Persist both values and their ratio (and optional
   `20*log10` ratio) without changing the criterion; Blocker 3 will assess validity.
5. Full windows do not differ in length at the edges: partial windows are already
   dropped. Within exp002, stack directly (`phase`: 21x400, spectrum: 21x201).
   Represent missing ECA arrays with NaN rows plus `eca_applied`. For exp004, write
   one NPZ per window-length condition rather than padding unlike conditions together.
6. Save `window_index`, start/end frame, start/end epoch, window/hop frames, frame rate,
   chosen bin/range, and an artifact schema version so NPZ rows can be joined to
   `comparison.csv` unambiguously.
7. The existing tabular artifact is `comparison.csv`, not `results.csv`. Add accepted
   AHET summary scalars to that CSV; keep per-attempt arrays in NPZ.
8. Prefer `np.savez_compressed`; all arrays must be numeric/bool and loadable with
   `np.load(..., allow_pickle=False)`.

**Verification required:**
- Add tests for pass, fail, and f_r-outlier diagnostic schemas, including NaN/sentinel
  behavior and three-candidate shapes.
- Add NPZ round-trip/alignment test and assert NPZ `ahet_verified`, frame bounds, and
  row count exactly match `comparison.csv`.
- Re-run exp002 and compare full-precision metrics against canonical run
  `20260614_133839`: all 5.1887797747/6.9021357101/-2.2281949852; verified
  5.3257187634/7.0513350671/-2.2171047344.

**Review verification:** existing suite passes 21/21 using
`pytest -q -p no:cacheprovider --basetemp .codex-pytest-blocker2review`.

---

### 2026-06-14 — Blocker 1 resolved — ECA config plumbing wired; exp001 re-run with deduplication

**Blocker 1 — ECA config plumbing wired:**
- `k_max` and `ahet_deviation_hz` added as explicit parameters (with matching defaults `6` and `0.1`) to `estimate_rate_from_phase()` in `src/vitals.py`.
- Both are now passed to all `eca_project()` call sites inside `estimate_rate_from_phase()` (first-pass and second-pass ECA).
- The two hardcoded `0.1` literals in the AHET search window (lines 201–202) replaced with `ahet_deviation_hz`.
- `run_pipeline_locked()` extended with `k_max: int = 6` and `ahet_deviation_hz: float = 0.1` parameters; passes them through to `estimate_rate_from_phase()` on the ECA path.
- `experiments/exp002_harmonic_rejection/run.py` reads `cfg["eca"]["k_max"]` and `cfg["eca"]["ahet_deviation_hz"]` from config and passes them to `run_pipeline_locked()`. Config comment updated to confirm these are actively wired (not just documentation).
- `experiments/exp001_offline_baseline/run.py` passes `k_max=6, ahet_deviation_hz=0.1` explicitly (exp001 has no `eca` config section; values match the existing defaults).

**exp002 metrics confirmed unchanged after wiring** (evidence: `results/exp002_harmonic_rejection/20260614_133839/`):
- All windows: MAE 5.189, RMSE 6.902, bias −2.228 bpm (N=21) — exact match to post-dedup reference.
- AHET-verified: MAE 5.326, RMSE 7.051, bias −2.217 bpm (N=20) — exact match.

**exp001 re-run with deduplication (not Blocker 2)** (evidence: `results/exp001_offline_baseline/20260614_133858/`):
- MAE 6.202, RMSE 8.773, bias −4.457 bpm (N=21).
- Change from previous canonical run (6.214/8.778/−4.448 at `20260614_120843/`): < 0.015 bpm on all metrics — negligible, as expected (duplicate PR values differed by 0–1 bpm).
- Does not resolve any blocker.

**Tests:** 21/21 pass. No test signatures changed — new parameters are all keyword-with-default.

**Open blockers remaining before exp004:**
- **Blocker 2:** Per-window intermediate arrays and AHET diagnostics not persisted by the experiment runner. `run_pipeline_locked()` returns `phase_clean`, `heart_spectrum`, `heart_freqs_hz`, `phase_eca`, `bin_energy`, and AHET evidence per window, but `exp002/run.py` discards all of these when building `radar_df`. exp004 must save these per-window arrays as required by CLAUDE.md §5.4 and the cross-review findings.
- **Blocker 3:** AHET second-harmonic SNR/prominence criterion not yet verified before interpreting pass-rate changes across window lengths. The current local-maximum test examines more FFT bins at longer window lengths; its pass rate is therefore resolution-dependent and must be verified/strengthened before exp004 results are interpreted.
- **Blocker 4 (lower priority):** No-ECA fallback (f_r outlier gate path) returns the raw FFT bin centre without parabolic refinement, introducing a systematic quantisation error not present on the main ECA path.

---

### 2026-06-14 — Blocker 1 identified — ECA config plumbing absent

**Finding:** `eca.k_max` and `eca.ahet_deviation_hz` are present in `experiments/exp002_harmonic_rejection/config.yaml` but are never read by `run.py` or passed into `src/vitals.py`. Both parameters are hardcoded in the implementation at values that coincidentally match the config — current results are therefore correct, but the wiring is absent.

**k_max** — `eca_project()` signature, line 67:
```python
def eca_project(theta, f_r, fs, k_max: int = 6, cardiac_candidate_hz=None):
```
Both call sites in `estimate_rate_from_phase()` (lines 172 and 196) pass only `x_bp`, `f_r_hz`, `fs`, and `cardiac_candidate_hz` — `k_max` is never passed explicitly. Config value `eca.k_max: 6` matches the default by coincidence.

**ahet_deviation_hz** — hardcoded literal `0.1` at lines 201–202 in `estimate_rate_from_phase()`:
```python
lo2 = 2.0 * cand_hz - 0.1
hi2 = 2.0 * cand_hz + 0.1
```
`estimate_rate_from_phase()` has no `ahet_deviation_hz` parameter. Config value `eca.ahet_deviation_hz: 0.1` matches the literal by coincidence.

**cfg["eca"] is never accessed by run.py** — the section sits in config as documentation only.

**Other hardcoded ECA/AHET parameters** (not in config, for reference):
- Line 85: `freq >= 2.0` Hz — harmonic frequency ceiling (cardiac band ceiling)
- Line 87: `k <= 4` — hard floor, always include harmonics 1–4 (noted in config comment as intentional)
- Line 90: `0.15` Hz — cardiac candidate guard (exclude harmonic if within 0.15 Hz of candidate)
- Lines 120–121: `_GATE_LO_HZ = 0.15`, `_GATE_HI_HZ = 0.60` — physiological gate for f_r
- Line 163: `fs * 0.45` — widened bandpass ceiling for AHET path
- Line 184: `noise_floor * 0.1` — `find_peaks` prominence threshold
- Line 187: `[:3]` — top-3 AHET candidates tried
- Line 301: `thresh=1.5` rad — impulse noise clip threshold in `remove_impulse_noise()`

**Fix needed:**
1. Add `k_max` and `ahet_deviation_hz` parameters to `estimate_rate_from_phase()` and thread through to `eca_project()` and the AHET search window.
2. In `run.py`, read `cfg["eca"]["k_max"]` and `cfg["eca"]["ahet_deviation_hz"]` and pass to the pipeline.
3. Re-verify exp002 baseline is unchanged after wiring (values match, so result should be identical).

---

### 2026-06-14 — Masimo deduplication fix — clock glitch duplicates resolved

**Duplicate epoch findings:** one duplicate per capture, all caused by Masimo clock glitches flanking missing seconds.
- exp001: epoch 1780995723 (PR 73/73, BR 18/19) — 4 missing seconds
- chair_back: epoch 1781364245 (PR 73/74, BR 19/19) — 3 missing seconds
- chair_no_back: epoch 1781365112 (PR 78/77, BR 21/21) — 4 missing seconds

**Policy:** mean of duplicate rows, rounded to 1 decimal place for all numeric columns. Implemented in `load_masimo()` via `groupby("Timestamp").agg(mean/first)` with prior `pd.to_numeric(..., errors="coerce")` to handle `'--'` values outside recording window. Coverage stats stored in `df.attrs` (`n_raw_rows`, `n_unique_epochs`, `n_duplicates_merged`, `n_missing_seconds`). Print summary on load: `"Masimo: N rows -> M epochs (K merged, J missing seconds)"`.

**Missing seconds** handled by existing NaN-safe mean in `reference_pr()` / `reference_br()` — no interpolation.

**Previous SESSION.md note claiming "no duplicates"** was incorrect — corrected here. Codex review finding confirmed.

**exp002 re-run metrics after dedup** (evidence: `results/exp002_harmonic_rejection/20260614_132459/`):
- All windows: MAE 5.189, RMSE 6.902, bias −2.228 bpm (N=21)
- AHET-verified: MAE 5.326, RMSE 7.051, bias −2.217 bpm (N=20)

Deduplication effect on metrics is negligible (MAE change < 0.01 bpm), as expected — duplicate PR values differed by 0–1 bpm.

**Tests added:** `test_load_masimo_deduplicates`, `test_load_masimo_coverage_attrs`, `test_reference_br_mean` — 21/21 tests pass.

---

### 2026-06-14 — corrected exp004 plan cross-review

**Verdict:** Close, but not implementation-ready until the remaining items below are
made explicit. No DSP or experiment code changed during this review.

**Primary analysis design:**
- Keep a separate 20 s start-aligned run (`starts = 0, 5, ..., 100 s`) solely to
  reproduce exp002: 21 outputs, 20 AHET-verified.
- Use common center epochs for the primary paired study:
  `15, 20, ..., 105 s` after trim (19 centers).
  - 20 s starts: `5, 10, ..., 95 s`
  - 25 s starts: `2.5, 7.5, ..., 92.5 s`
  - 30 s starts: `0, 5, ..., 90 s`
- Evaluate the success criterion against the matched 20 s result, not against the
  full-grid exp002 bias of -2.207 bpm.
- For each longer length vs 20 s, report pairwise-common accepted-center bias/MAE
  for both conditions, mean and SD of `error_long - error_20`, mean and median of
  `abs(error_long) - abs(error_20)`, and pair count. Do not use naive p-values or
  independent-window confidence intervals.
- Add a 10 s decimation sensitivity table for both alternating common-center grids.

**AHET fairness requirements:**
- Report separately: AHET-verified coverage, finite-HR output coverage, NaN rate,
  f_r-outlier fallback rate, and harmonic-suspect rate, all with the same 19-center
  denominator.
- Add pairwise AHET transition counts: both pass, only 20 s passes, only longer
  passes, neither passes.
- Persist the AHET evidence (candidate frequency/rank, second-harmonic frequency,
  second-harmonic amplitude/noise or prominence) because the current local maximum
  test examines more FFT bins at longer window lengths and its pass rate is therefore
  resolution-dependent.

**Remaining implementation blockers:**
1. `eca.k_max` and `eca.ahet_deviation_hz` are still present in config but are not
   passed into `src/vitals.py`; defaults/hard-coded values control the algorithm.
   Wire them without tuning, then re-verify the 20 s baseline.
2. `run_pipeline_locked()` returns phase/spectrum/ECA arrays, but experiment runners
   do not persist them. exp004 must save per-window intermediate arrays and AHET
   diagnostics as required by `CLAUDE.md`.
3. The saved exp002 baseline artifact at `20260614_120354` does not contain
   `masimo_br`; the code addition post-dates that run. Re-run or verify it in exp004.
4. Duplicate Masimo epochs are real, contrary to the newest session note:
   exp001 epoch 1780995723, chair-back 1781364245, chair-no-back 1781365112.
   Each capture also has missing seconds. Define a deterministic per-epoch
   aggregation policy and log unique-time coverage; never edit raw CSV files.
5. Add tests for `reference_br()` and common-center window construction.

**Respiration comparison:**
- Use refined radar `f_r_hz_used * 60`, not the unrefined `rr_bpm` FFT-bin value.
- Report per-window differences plus aggregate bias, MAE, RMSE, valid N, and
  temporal coverage. Report all windows and identify the physiological-outlier
  fallback rather than silently excluding it.
- Treat Masimo Breaths/min as a device-derived secondary comparator, not established
  respiratory ground truth.
- On the current 20 s run, provisional all-window RR comparison is bias +0.67 bpm,
  MAE 1.89 bpm, RMSE 2.80 bpm; the single f_r-outlier window contributes -9.23 bpm.

**Verification:** `pytest -q -p no:cacheprovider --basetemp
.codex-pytest-exp004review` passed 18/18 tests.

---

### 2026-06-14 — Masimo Breaths/min added to pipeline window output

- `masimo.reference_br(df, start_epoch, end_epoch)` added to src/masimo.py, parallel to `reference_pr()`. Returns NaN-safe mean of `rr_bpm` over [start, end); returns NaN for empty or all-NaN windows.
- `masimo_br` column added to exp002 window table (between `f_r_bpm` and `outlier`) and to the saved comparison CSV via automatic flow-through.
- Enables direct f_r validation: compare radar-estimated `f_r_bpm` vs `masimo_br` per window to diagnose ECA subspace estimation errors.
- Enables advance AHET failure prediction: when masimo_br × 4 ≈ masimo_PR, window is expected to fail AHET (physiological coincidence, not algorithm failure).
- Note: '--' handling not needed — capture protocol ensures all '--' values fall outside radar recording window by design.
- Note: duplicate epoch investigation found no duplicates in any capture — no deduplication policy needed.

---

### 2026-06-14 — Masimo half-open interval fix — exp001 and exp002 re-baselined

**Bug fixed:** src/masimo.py interval changed from inclusive [start, end] to half-open [start, end). A 20 s window previously included 21 Masimo samples; now correctly includes 20. Fix approved by Codex review.

**New reference metrics (supersede all previous exp001/exp002 numbers):**
- exp001 baseline (no ECA+AHET): MAE 6.214, RMSE 8.778, bias −4.448 bpm (N=21)
- exp002 all windows (ECA+AHET): MAE 5.196, RMSE 6.904, bias −2.219 bpm (N=21)
- exp002 AHET-verified: MAE 5.334, RMSE 7.054, bias −2.207 bpm (N=20)
- Evidence: `results/exp001_offline_baseline/20260614_120843/`, `results/exp002_harmonic_rejection/20260614_120354/`

**Bug also fixed:** run_pipeline_locked() was silently applying ECA+AHET to exp001. Fixed by adding use_eca parameter (default True) to run_pipeline_locked(). exp001/config.yaml now sets use_eca: false. exp001 metrics confirmed to match Codex cross-review prediction exactly.

**Old metrics (superseded, kept as provenance):**
- exp001: MAE 6.206, RMSE 8.802, bias −4.512 bpm
- exp002 all windows: MAE 5.157, RMSE 6.880, bias −2.283 bpm
- exp002 AHET-verified: MAE 5.291, RMSE 7.029, bias −2.274 bpm

**Deferred:** duplicate epoch policy in Masimo exports — duplicate timestamps currently receive extra statistical weight. Policy decision required before exp004 results are finalized. Options: keep first, keep last, or mean of duplicates.

**Tests added:** tests/test_masimo.py — 6 tests covering half-open boundary, adjacent window partitioning, PR mean calculation, PI quality gate, empty interval, and invalid interval guard.

18/18 tests pass. Evidence: `pytest tests/ -v`.

---

### 2026-06-14 — Masimo half-open window fix plan cross-review

**Verdict:** Approved with additions; no source code changed during this review.

**Required implementation:**
- Change Masimo selection from `[start_epoch, end_epoch]` to
  `[start_epoch, end_epoch)`.
- Update the docstring/error notation and reject `end_epoch <= start_epoch`.
- Add `tests/test_masimo.py` cases for start inclusion/end exclusion, adjacent
  interval partitioning, exact reference mean/counts, PI gating at the excluded
  endpoint, and empty/invalid intervals.

**Important correction:** A 20 s radar window with a 5 s hop overlaps the next
window by 15 s (75% overlap); it does not share zero frames. Half-open support is
still correct and should match the radar sample-index convention.

**Measured impact on the original capture:**
- exp001 locked baseline: MAE 6.2062 -> 6.2142, RMSE 8.8019 -> 8.7779,
  bias -4.5124 -> -4.4480 bpm.
- exp002 all estimates: MAE 5.1571 -> 5.1963, RMSE 6.8804 -> 6.9044,
  bias -2.2833 -> -2.2190 bpm.
- exp002 AHET-verified: MAE 5.2912 -> 5.3337, RMSE 7.0286 -> 7.0536,
  bias -2.2737 -> -2.2074 bpm.
- Largest individual Masimo window-mean change: 0.3143 bpm. No PI-quality
  classification changed.

**Adjacent data-quality issue found:**
- Every available Masimo export contains one duplicate Unix epoch and several
  missing seconds. The original capture has one duplicate epoch and four missing
  seconds overall; half-open 20 s windows contain 18-20 unique epochs.
- `low_quality` currently measures PI quality only among observed rows. It does
  not detect missing temporal coverage, and duplicate epochs receive extra weight.
- Define and test a duplicate-epoch policy and report unique-time coverage before
  treating exp004 metrics as final. Do not edit the raw CSV files.

---

### 2026-06-14 — exp004 window-length plan cross-review

**Verdict:** Not approved as written. The original capture can support an exploratory,
within-session window-length comparison, but not a paper-grade conclusion.

**Corrections found:**
- The exp002 capture is 152 s, so the 30 s trim leaves 122 s, not ~90 s.
- The reproducible exp002 baseline uses a 5 s hop, not 50% overlap: 21 total windows,
  20 AHET-verified. Changing to 50% overlap would change both the hop and the sampled
  time grid, so it would not be a window-length-only comparison.
- Even treating the 20 verified exp002 errors as independent, the naive 95% bias CI
  half-width is ~3.19 bpm; a 0.5 bpm change cannot be resolved from this capture alone.

**Required design changes before implementation:**
1. Use a common center-time grid for 20/25/30 s windows, with full windows only and
   fixed 5 s hop to preserve the exp002 update cadence. Restrict all conditions to the
   time support available to the 30 s windows. Report a 10 s decimation only as a
   sensitivity analysis.
2. Drop partial windows; do not zero-pad or shorten them. Change Masimo reference
   support from inclusive `[start, end]` to half-open `[start, end)` and rerun the
   20 s baseline under the same corrected comparison code.
3. Report paired common-center bias, MAE, RMSE, and coverage/NaN rate. Also report
   metrics on the intersection of centers accepted by every condition so AHET
   rejection cannot improve conditional metrics by dropping difficult periods.
4. Treat the single-capture result as exploratory. Validate any selected length on
   independent sessions/subjects and estimate uncertainty by session-level paired
   bootstrap or a hierarchical model, not by treating overlapping windows as
   independent replicates.
5. Add fixed-seed synthetic tests at all three lengths for an isolated tone,
   close respiratory/intermodulation peaks, and time-varying HR/respiration.

**Implementation blockers identified:**
- `eca.k_max` and `eca.ahet_deviation_hz` exist in config but are not passed into
  `estimate_rate_from_phase()`; the values are hard-coded/defaulted in `src/vitals.py`.
- The local AHET search makes frequency proximity true by construction and accepts
  a peak when it merely exceeds the cardiac-band median. Verify/strengthen the
  second-harmonic SNR/prominence criterion before interpreting pass-rate changes.
- The no-ECA fallback returns the raw FFT bin center without parabolic refinement.
- Intermediate arrays returned by `run_pipeline_locked()` are not persisted by the
  experiment runner. exp004 must dump phase, ECA residual, spectra, selected harmonics,
  candidate peaks, and AHET evidence per estimate.

**Interpretation:** 20 to 30 s improves nominal spacing from 3 to 2 bpm and can reduce
leakage or help separate close components, but parabolic interpolation already gives
sub-bin estimates for an isolated stationary tone. Longer windows stop helping when
HR/respiration nonstationarity, motion, and latency dominate. ECA harmonic order should
not scale with window length; it should remain tied to the modeled respiratory harmonics
and be logged for every matched center.

---

### 2026-06-14 — range_resolution_m validated from first principles

**Chirp parameters (from mmWave Studio):**
- Start frequency: 77 GHz
- Frequency slope: 70.005996704101562 MHz/µs
- ADC samples: 256
- Sample rate: 5209 Msps
- ADC start time: 5 µs
- Idle time: 7 µs
- Ramp end time: 57 µs

**Derivation:**
- T_ADC = 256 / 5209e6 = 49.14 µs
- BW = 70.006 MHz/µs × 49.14 µs = 3440.7 MHz
- Δr = c / (2 × BW) = 3e8 / (2 × 3.4407e9) = 0.04359 m/bin
- R_max = 0.04359 × 128 = 5.58 m (4 m display cap in exp000 is well within this)

**Result:** config value 0.0436 m/bin is correct to 4 significant figures. Maximum accumulated error over 33 bins is 0.03 mm — negligible. No config change required.

---

### 2026-06-14 — exp000 range plots — scene analysis and bin selector revert

**exp000 range plot findings:**

capture 1 (exp001_sit_140cm): clean single peak at bin 27 (1.177 m), stable across full 150 s. One dominant stripe in heatmap, no competing reflectors. Reference clean-scene result.

capture 2 (exp002_sit_chair_back): single dominant peak at bin 33 (1.439 m), stable across full 240 s. No multipath — one clean stripe. Heatmap confirms bin 33 was the chest return, not a spurious reflector. Subject was actually sitting at ~1.44 m (chair back held subject further from radar than the post-hoc measurement of 1.26 m suggested). Energy-based bin selector was correct for this capture.

capture 3 (exp002_sit_chair_no_back): clean single peak at bin 30 (1.308 m), stable across full 240 s. Without chair back, subject sat naturally at ~1.31 m — 13 cm closer than with chair back.

**Revised interpretation of exp003 chair_back failure:**
- Root cause is NOT multipath interference (heatmap shows single stripe, no competing reflectors)
- Root cause is geometry change: subject sat at ~1.44 m (bin 33) vs ~1.26 m (bin 29) in exp002 — 18 cm difference due to chair back
- f_r elevated at 20–24 bpm vs ~15 bpm in exp002 — different session physiology
- Phase-variance selector regression (MAE 8.44 → 12.34) confirmed bin 33 was the correct chest bin: selecting bin 28 tracked the wrong target
- Energy-based selector reverted as the correct default for clean single-target scenes

**Distance measurement note:**
Post-hoc tape measure gave 1.26 m but heatmap evidence indicates ~1.44 m during the chair_back capture. Likely measured to wrong reference point (chair front vs seated chest position). For future captures: measure distance while seated in capture position, verify against dominant bin × range_resolution_m.

**Bin selector status:**
Phase-variance selector removed. Energy-based selector restored. Combined criterion (phase variance within top-N energy bins) deferred to exp005.

**Next:**
- [ ] Re-capture: no chair back, same distance ~1.26 m, different day, normal resting HR (~72 bpm)
      Rationale: chair_no_back failure was physiological (HR 82 bpm ≈ 4×f_r), not algorithmic
- [ ] exp004: window length study on original capture (exp001_sit_140cm) — bias reduction
- [ ] exp005: combined bin selector (phase variance within top-N energy bins)

---

### 2026-06-14 — exp003 re-run with phase-variance bin selector — metrics regressed, root cause identified

**Change tested:** phase-variance bin selector (replaces energy-based selector in src/vitals.py). Correctly rejects static reflectors with high amplitude but low breathing modulation.

**Results after phase-variance fix:**

| Metric     | exp002 ref | chair_back energy (bin 33) | chair_back phase-var (bin 28) | chair_no_back energy (bin 32) | chair_no_back phase-var (bin 31) |
|------------|-----------|---------------------------|-------------------------------|-------------------------------|----------------------------------|
| MAE (bpm)  | 5.29      | 8.44                      | 12.34                         | 13.38                         | 16.95                            |
| RMSE (bpm) | 7.03      | 11.67                     | 14.17                         | 15.88                         | 18.60                            |
| Bias (bpm) | −2.27     | −6.81                     | −8.38                         | −11.95                        | −16.49                           |
| N windows  | 20        | 32                        | 35                            | 22                            | 30                               |
| N NaN      | 1         | 7                         | 4                             | 17                            | 9                                |

**Root cause of regression (chair_back):**
- Chest at 1.26 m = bin 29, but bin 29 ranks 5th in phase variance (var=149) behind bins 28, 33, 36, 35
- Chair back at 1.64 m = bin 38 (outside gate) but close enough to cause multipath interference at chest bins
- Multipath suppresses breathing-induced phase modulation at bin 29 and artificially elevates phase variance at off-chest bins
- Neither energy nor phase-variance selector can reliably identify the chest bin when multipath corrupts the phase signal itself
- f_r consistently 20–24 bpm in this capture vs ~15 bpm in exp002 — chair back likely altering chest-wall motion pattern or multipath corrupting f_r estimation
- Conclusion: chair-back capture geometry is unfavourable for single-bin extraction. Not fixable by bin selection criterion alone.

**Root cause (chair_no_back):**
- HR elevated ~82 bpm during this session (postural effort without back support)
- f_r ~19–21 bpm → 4th harmonic ~80 bpm lands directly on cardiac frequency
- AHET correctly flags as harmonic suspect → NaN (correct pipeline behaviour)
- Failure is physiological coincidence, not algorithmic

**Decisions:**
- chair_back geometry is deferred — requires multi-bin coherent combination (exp005) to handle multipath
- chair_no_back failure is physiological and will resolve on a re-capture day when HR is ~72 bpm
- Phase-variance selector is correct in principle and stays in the codebase — it performs correctly on clean geometry (exp002: bin 29, 3.7× margin)
- Revert exp003 config to use phase-variance selector (already in place); do not revert src/vitals.py

**Next:**
- [ ] Re-capture: no chair back, same distance 1.26 m, different day, normal resting HR
      Goal: confirm MAE ~5 bpm generalises to a second session with clean geometry
- [ ] Only proceed to exp004 (window length study) after clean generalisation capture confirms MAE ≤ 7 bpm
- [ ] exp005: multi-bin coherent combination — needed to handle chair-back multipath geometry

---

### 2026-06-13 — exp003 generalisation test — MARGINAL (mixed result, investigation required)

**Results:**

| Metric     | exp002 reference | chair_back | chair_no_back |
|------------|-----------------|------------|---------------|
| MAE (bpm)  | 5.29            | 8.44       | 13.38         |
| RMSE (bpm) | 7.03            | 11.67      | 15.88         |
| Bias (bpm) | -2.27           | -6.81      | -11.95        |
| N windows  | 20              | 32         | 22            |
| N NaN      | 1               | 7          | 17            |

Acceptance criterion result: MARGINAL (mixed) — chair_back MAE 8.44 bpm (marginal band 7–9), chair_no_back MAE 13.38 bpm (fail).

**Diagnosed failure modes:**

chair_back:
- Locked bin 33 (1.439 m) vs exp002 bin 29 (1.264 m) — subject sat ~18 cm further back due to chair geometry. Need to verify whether chair back surface is inside gate [1.1–1.5 m] and contributing energy to bin 33.
- f_r consistently 18–24 bpm (vs ~15 bpm in exp002). Physiologically elevated or chair-back mechanical vibration coupling into phase signal.
- Systematic negative bias −6.81 bpm (doubled vs exp002 −2.27 bpm). Consistent with spectral leakage or mixed chest+chair reflection at bin 33.

chair_no_back:
- HR elevated ~82 bpm (vs ~73 bpm in exp002) — likely postural effort without back support.
- f_r ~19–21 bpm. 4th harmonic ~80 bpm lands directly on cardiac frequency → AHET correctly flags 17/39 windows as harmonic suspect → NaN (correct behaviour, not fabricated).
- Physiological coincidence of HR ≈ 4×f_r is the primary failure cause, not algorithm failure.

**Immediate investigation required before exp004:**
- Measure actual distance radar→chest and radar→chair back for chair-with-back setup
- Confirm whether chair back surface is inside gate [1.1–1.5 m] — if yes, widen gate lower bound or reposition chair
- Re-run chair_back capture with subject positioned at confirmed 1.264 m (mark floor for chair front legs, not body)

**Next:**
- [ ] Physical measurement: radar→chest and radar→chair back distances
- [ ] Re-capture chair_back at confirmed 1.264 m with chair back verified outside gate
- [ ] If re-capture confirms MAE ≤ 7 bpm: generalisation confirmed, proceed to exp004
- [ ] exp004: window length study (25 s, 30 s vs current 20 s) — bias reduction target
- [ ] exp005: multi-bin coherent combination for respiratory dominance robustness

---

### 2026-06-13 — capture 2 planned

**Plan:**
- Two separate recordings on the same day: chair with back, then chair without back
- Duration per recording: 4 min total — 30 s positioning, 3:30 min quiet seated breathing
- Order: chair-with-back first (freshest, least drift risk), backless chair second
- Distance: 1.3–1.4 m (replicate capture 1 setup as closely as possible)
- Same chirp config, same Masimo device, same trim (30 s baked into config)
- File naming: capture2_chair_back and capture2_chair_none

**Acceptance criterion (written before collection to prevent post-hoc rationalisation):**
- Pass: MAE ≤ 7 bpm on at least one of the two recordings
- Strong pass: MAE ≤ 7 bpm on both recordings
- Marginal: MAE 7–9 bpm — proceed to exp003 with caveat
- Fail: MAE > 9 bpm on both — investigate before any further algorithm work

**Expected window count:** ~19 windows at 20 s / 50% hop per recording (vs ~8 in capture 1), giving more statistical power for exp003 window length comparison.

---

### 2026-06-13 — exp002 closed: final cleanup + outlier gate + tag

**Worked:**
- ECA+AHET final form confirmed reproducible: MAE 5.29, RMSE 7.03, bias −2.27 bpm
  (AHET-verified windows only). All-windows: MAE 5.16, RMSE 6.88, bias −2.28.
  Evidence: `results/exp002_harmonic_rejection/20260613_171020/`. Tagged: `exp002-baseline`.
- Physiological outlier gate on f_r moved into `estimate_rate_from_phase()`:
  if f_r_hz outside [0.15, 0.60] Hz, falls back to no-ECA bandpass+argmax and sets
  `f_r_outlier=True`. Correctly fired at window 10 (f_r=8.8 bpm); produced HR=69.0 bpm
  (error -2.5 bpm) instead of NaN — a real improvement over the unprotected NaN.
- All algorithm decisions cross-reviewed (ChatGPT plan review, Codex implementation
  review) and findings applied before implementation.
- bin 29 pinned explicitly in config for both exp001 and exp002 — decouples bin
  selection from trim window length, makes experiments directly comparable.
- 12/12 tests passing throughout. Evidence: `pytest tests/ -v`.

**Failed / documented negative results:**
- EMA f_r smoothing (alpha=0.3): adds lag, net MAE regression +0.79 bpm vs no-EMA.
  Removed. Documented in notes/approach.md.
- 45 s trim: started analysis in more volatile stretch of this capture, MAE
  regression +2.28 bpm. Reverted to 30 s. Documented in notes/approach.md.

**Known limitation:**
- Windows 9-12: respiratory-dominance event causes ~60 bpm estimate despite AHET
  verification. Physics limitation of single-range-bin extraction, not algorithm.
  Documented in notes/approach.md §exp002 Final Assessment.
  Candidate fix: multi-bin coherent combination (deferred to exp004).

**Next:**
- [ ] Second capture: different day, same setup (1.4 m seated)
  Protocol: 2 min settle, 2 min record, same Masimo export procedure
  Goal: confirm MAE ~5 bpm generalises beyond this recording
- [ ] If second capture confirms generalisation: add standing posture capture
- [ ] exp003: window length study (25 s, 30 s vs current 20 s)
- [ ] exp004: multi-bin coherent combination for respiratory dominance robustness

---

### 2026-06-12 — exp002: ECA + AHET harmonic rejection

**Worked:**
- ECA + AHET implemented in `src/vitals.py` per arXiv:2503.07062 (Tang et al., 2025),
  with all four OpenAI cross-review improvements applied:
  1. QR projection (`np.linalg.qr`) — not explicit matrix inverse
  2. Parabolic interpolation for f_r refinement beyond FFT bin width
  3. Local AHET 2nd-harmonic search [2×f_h ± 0.1 Hz] — not global to 4.0 Hz
  4. Adaptive K_b guard: exclude harmonic k if within 0.15 Hz of cardiac candidate;
     hard floor k = 1..4 (covers known 60 bpm / 4th-harmonic failure).
  5. f_final blends fundamental and halved 2nd harmonic estimates.
- `compare.metrics()` made NaN-safe; `n_nan_windows` reported separately.
- `experiments/exp002_harmonic_rejection/` created with config + run.py.
- 12/12 tests passing (9 existing + 3 new ECA/AHET unit tests). Evidence: `pytest tests/ -v`.

- **exp002 vs exp001 metrics (same capture, locked bin 29, 1.264 m):**

  | Metric   | exp001 baseline | exp002 ECA+AHET | Δ        |
  |----------|-----------------|-----------------|----------|
  | MAE      | 6.21 bpm        | 5.29 bpm        | −0.92    |
  | RMSE     | 8.80 bpm        | 7.03 bpm        | −1.77    |
  | Bias     | −4.51 bpm       | −2.27 bpm       | +2.24    |
  | N windows| 21              | 20 (+ 1 NaN)    |          |

  RMSE improvement larger than MAE: confirms ECA+AHET specifically reduced large-error
  outlier windows (harmonic-contaminated windows), as designed.

- Window 10 correctly produced NaN: f_r misestimated at 8.8 bpm (implausible —
  irregular breathing segment). Pipeline refused to fabricate per CLAUDE.md §4.
  This is correct behaviour.
- 20/20 non-NaN windows AHET-verified.

**Remaining issues / not yet resolved:**
- 5 windows still have errors > 10 bpm despite AHET verification. ECA removed the
  harmonic cause; remaining errors likely spectral leakage or genuine HR variability
  unresolvable at 20 s window length.
- Bias −2.27 bpm still present. Parabolic interpolation halved it vs exp001 (−4.51 bpm)
  but did not eliminate. Candidate: 20 s window too short for sub-bin precision at
  resting HR (~60–75 bpm → 1.0–1.25 Hz, where bin spacing is 0.05 Hz = 3 bpm).
- Window 10 NaN: f_r misestimation during irregular breathing segment. Needs a
  more robust f_r estimator (e.g., median over multiple sub-windows).

**Decisions:**
- exp002 is the new performance baseline: MAE 5.29, RMSE 7.03, bias −2.27 bpm.
- Tag this commit as `exp002-baseline` before any further changes.
- exp001 results remain archived in `results/exp001_offline_baseline/` at git
  commit 2ae18de — reproducible independently.
- `run_pipeline_locked()` now always applies ECA + AHET. exp001 re-runs with the new
  code will differ; original exp001 results are pinned to the archived results dir.

**Next:**
- [ ] Analyse overlay plot for large-error window pattern (leakage vs HR variability)
- [ ] `git tag exp002-baseline`
- [ ] Collect second capture (different session) to test generalisation
- [ ] exp003: window length and hop tuning (30 s vs 20 s tradeoff study)

---

## Capture protocol (keep this stable across sessions)

1. Subject sits or stands 1.3-1.6 m in front of the radar; radar at chest height.
2. Attach Masimo MightySat; wait ~2 min until readings stabilize (good PI).
3. Start Masimo logging on the phone; record the PC wall-clock (UTC epoch) at radar start.
4. Capture ~2 min of radar data.
5. Stop both. Export Masimo CSV; move radar .bin and Masimo .csv into `data/raw/`.
6. Note posture, distance, subject ID, settle time, and duration in the log below.

---

## Backlog (what's left)

- [x] Rung A: flash TI vital-signs lab, confirm plausible HR vs Masimo (hardware bring-up).
      → OOB demo used instead; TI dropped vital-signs lab for IWR1642 in 2024. Baseline
        will be published mmWave vital-signs papers (see notes/approach.md).
- [x] Set PC static IP 192.168.33.30 for DCA1000 Ethernet connection.
- [x] Configure mmWave Studio chirp profile (Low Power ADC, IF BW ≤5 MHz, ~20 Hz frames).
- [x] First paired capture: radar .bin + Masimo .csv into data/raw/.
- [x] Implement radar_io.read_adc_bin with actual chirp config; Codex review the diff.
- [x] Update exp001 config.yaml with real parameters and run pipeline.
- [x] Rung B: first paired offline capture; run exp001; inspect overlay plot.
      → MAE 6.21 bpm (locked bin), 7.37 bpm (per-window, pre-locking). Pipeline green.
- [ ] Tune distance gate + filter bands from real data.
- [x] DONE: exp002 — ECA + AHET harmonic rejection. MAE 5.29, RMSE 7.03, bias −2.27 bpm.
- [ ] DEFERRED (write-up): quantitative validation — MAE/RMSE + Bland-Altman across
      subjects/postures. Rig is the same; just start logging paired numbers.
- [ ] DEFERRED: validate respiration-band extraction against Masimo `Breaths / min`.

---

### 2026-06-10 — Part 7: revert verification and SESSION update
**Worked:**
- Bin locking (`run_pipeline_locked`) confirmed as keeper — eliminates late-window
  phase discontinuities caused by bin hopping between adjacent bins 27 and 29.
- Bias improved to near-zero when harmonic rejection was active (but at cost of
  higher MAE — see Failed).
- Revert confirmed clean: 9/9 tests pass (`pytest tests/ -v`). Pipeline re-run:
  MAE 6.21 bpm, RMSE 8.80 bpm, bias -4.51 bpm (locked bin 29, 1.264 m, 21 windows).
  Evidence: `results/exp001_offline_baseline/20260610_223845/`.
  Note: expected 7.37/9.57/-2.66 in task brief — those are the per-window pre-locking
  numbers. Locked pipeline gives 6.21/8.80/-4.51. Different algorithm, not a regression.

**Failed:**
- Harmonic rejection with fixed 0.08 Hz threshold: over-triggers on true cardiac
  signal near harmonic frequencies. Substitutes 60 bpm wrong peak across majority
  of windows. MAE regressed from 7.37 to 9.55 bpm. Do not retry with fixed threshold.
- Root cause: 4th harmonic of ~15 bpm respiration = ~60 bpm lands near cardiac band
  edge; a simple frequency proximity threshold cannot reliably separate the true
  cardiac peak (~71 bpm) from harmonic contamination (~60 bpm) on 20-second windows.

**Decision:**
- Reverted harmonic rejection. Kept bin locking.
- Harmonic rejection needs a principled approach (cepstrum-based, notch-then-search,
  or longer window) before re-attempting. Defer to exp002.
- exp002 must start with a literature review of how published mmWave vital-signs
  papers handle respiration harmonic interference — research-first, not another
  threshold guess.

**Next:**
- [x] Confirm revert restores baseline — done: locked pipeline 6.21/8.80/-4.51 bpm,
      per-window baseline 7.37/9.57/-2.66 both consistent (see Part 6 for full history)
- [ ] exp002: literature review of harmonic rejection methods (arXiv MCP)
- [ ] exp002: design and implement principled harmonic rejection
- [ ] Collect a second capture for validation (different session, same setup)

---

### 2026-06-10 — Part 6: bin locking, harmonic rejection attempt, revert
**Worked:**
- `run_pipeline_locked()` added to `src/vitals.py`: selects the range bin ONCE from
  the full post-trim cube, extracts and denoises the full slow-time phase, then runs
  per-window spectral estimation on the continuous phase. Eliminates bin-hopping
  (bins 27/29 were alternating) and inter-window phase discontinuities.
- Bin-locked baseline (no harmonic rejection): MAE 6.21 bpm, RMSE 8.80 bpm,
  bias -4.51 bpm, locked bin 29 (1.264 m), 21 windows. Evidence: pipeline output
  at `results/exp001_offline_baseline/20260610_210158/`.
- range_resolution_m confirmed as 0.0436 m/bin (mmWave Studio Calculated Parameters);
  gate widened to [1.1–1.5 m] to cover bins 25–34. Config updated.
- 9/9 tests pass after revert. Evidence: `pytest tests/` green.
**Failed / dead ends:**
- Harmonic rejection with fixed 0.08 Hz threshold: over-triggers on real cardiac signal
  near harmonic frequencies. 4th harmonic of 15 bpm respiration = 60 bpm lands near
  cardiac band edge; on 20-second windows, threshold cannot reliably separate true
  cardiac at ~71 bpm from harmonic contamination at ~60 bpm. MAE regressed from 7.37
  to 9.55 bpm. Reverted. **Do not retry with a fixed Hz threshold.**
- Root cause: fixed-frequency tolerance is the wrong abstraction — need amplitude-
  relative or spectrum-shape-aware criteria.
**Decisions:**
- Harmonic rejection deferred to exp002. Must start with literature review (cepstrum-
  based, notch-then-search, or longer window) before any implementation.
- Revert confirmed clean: per-window `run_pipeline` reproduces 7.37/9.57/-2.66 exactly;
  locked `run_pipeline_locked` gives 6.21/8.80/-4.51 (different algorithm, not a bug).
- `run_pipeline_locked` is now the active pipeline in `run.py`.
**Next:** exp002 design — research how published mmWave vital-signs papers handle
resp harmonic interference before writing any code.

### 2026-06-09 — Part 5: read_adc_bin implemented, Codex cross-review, ADC layout test
**Worked:**
- `read_adc_bin` implemented with correct 4-word LVDS packet decode for IWR1642 2-lane
  Complex1x format (SWRA581B + rawDataReader.m confirmed). Lane 1 (words 0-1) = I,
  lane 2 (words 2-3) = Q; each 4-word packet → [I_n+jQ_n, I_{n+1}+jQ_{n+1}].
- Codex cross-review caught critical I/Q lane ordering bug in first implementation —
  old decoder produced false peaks at bins 99-101; correct decoder shows target at
  bins 27-29 (~1.22 m, consistent with 1.4 m subject and nominal range_resolution_m).
- Three Codex findings applied: (P2) `np.memmap` for on-demand paging of 393 MB file;
  (P3) file-size validation before any memory allocation, with empty-file guard;
  (P4) dedicated ADC layout unit test (`tests/test_radar_io_layout.py`) that asserts
  the exact [100+300j, 200+400j] decode from a known [100,200,300,400] 4-word packet.
- Full test suite: 9 passed (test_logfile_parsing ×2, test_radar_io_layout ×1,
  test_vitals_synthetic ×6). Evidence: `pytest tests/` green on all.
**Failed / dead ends:**
- First I/Q implementation (even=I, odd=Q, sample-interleaved) was wrong for this lane
  config — do not use for any future 2-lane DCA1000 capture with laneFmtMap=0. False
  peaks at bins 99-101 were the diagnostic signature.
**Decisions:**
- `num_frames = 3000` (inferred from file size 393,216,000 / 131,072 bytes/frame),
  not 3040 from LogFile duration estimate. `infer_num_frames()` is authoritative —
  always use file size, not LogFile duration.
- `np.memmap` used for raw file load; cube returned as concrete complex64 ndarray (not
  a memmap view) so callers get a normal array with no file-handle lifetime issues.
**Next:** Part 6 — run full pipeline (`python experiments/exp001_offline_baseline/run.py`);
check printed startup summary (Masimo epoch overlap); read overlay plot.

### 2026-06-09 — Stage 4: LogFile parser, config wired to real capture, trim logic
**Worked:** `parse_logfile()` and `infer_num_frames()` added to `src/radar_io.py`.
`experiments/exp001_offline_baseline/config.yaml` updated to real file names, correct chirp
params (32 chirps/frame, 3040 frames, range_res 0.0422 m, gate 1.3–1.5 m), UTC offset 3,
and 30 s trim. `run.py` now reads timestamps from the LogFile, infers frame count from
.bin size, starts sliding windows at trim_frames, and prints a sanity-check summary before
processing. Evidence: `pytest tests/test_logfile_parsing.py -v` → 2 passed (UTC epoch
arithmetic verified against known values 1780995595 / 1780995747).
**Failed / dead ends:** none.
**Decisions:** LogFile provides reliable start/end epochs; `radar_start_epoch` removed from
config. `infer_num_frames` overrides config value at runtime so the config acts as a
documentation default. Trim of 30 s accounts for subject walking into position.
**Next:** Set PC static IP; configure chirp profile; first paired capture; implement
`read_adc_bin`.

### YYYY-MM-DD — Project scaffold created
**Worked:** repo structure, CLAUDE.md context, Masimo parser + compare scaffold, synthetic
vitals test. Evidence: `pytest tests/` passes on synthetic phase.
**Failed / dead ends:** none yet.
**Decisions:** ground truth = `Beats / min`; align on integer Unix-epoch `Timestamp` (UTC);
PI used as a quality gate, not a tuning target.
**Next:** Rung A hardware bring-up.
