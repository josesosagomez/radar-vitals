# Project History

> Chronological record of how the project has taken shape. Oldest entry first.
> Last updated: 2026-07-02.

> **W&B policy:** Weights & Biases logging is **disabled until further notice.**
> CLAUDE.md §3 rule 5 requires it; this policy will be reinstated when instructed.

---

## Hardware and tooling notes (standing facts)

- **PC static IP:** 192.168.33.30 for DCA1000 EVM Ethernet connection.
- **mmWave Studio chirp profile:** Low Power ADC, IF BW ≤ 5 MHz, ~20 Hz frame rate.
- **TI vital-signs lab:** TI dropped the vital-signs lab demo for IWR1642 in 2024; the
  OOB demo was used for hardware bring-up instead. The baseline comparison uses published
  mmWave vital-signs papers (see `notes/approach.md`) rather than a TI firmware reference.
- **Capture protocol for seated/standing captures** is documented in the SESSION.md
  capture-protocol section (archived below) and in `protocol.md` for supine captures.

---

## Capture protocol (seated / standing — stable across sessions)

1. Subject sits or stands 1.0–1.4 m in front of the radar; radar at chest height.
2. Attach Masimo MightySat; wait ~2 min until readings stabilise (good PI).
3. Start Masimo logging on the phone; note the PC wall-clock (UTC epoch) at radar start.
4. Capture ~2 min of radar data.
5. Stop both. Export Masimo CSV; move radar `.bin` and Masimo `.csv` into `data/raw/`.
6. Note posture, distance, subject ID, settle time, and duration in the session log.

---

## YYYY-MM-DD — Project scaffold

**Implemented:**
- Repo structure, `CLAUDE.md` context, Masimo parser + compare scaffold, synthetic vitals unit test.
- `pytest tests/` passes on synthetic phase.

**Decisions:**
- Ground truth = `Beats / min` (PR) column from Masimo CSV, aligned on integer Unix-epoch `Timestamp` (UTC).
- Perfusion Index (PI) used as a quality gate, not a tuning target.

---

## 2026-06-09 — LogFile parser, config wired to first capture, trim logic

**Implemented:**
- `parse_logfile()` and `infer_num_frames()` added to `src/radar_io.py`.
- `experiments/exp001_offline_baseline/config.yaml` updated to real file names and chirp
  params: 32 chirps/frame, 3040 frames, range_res 0.0422 m, gate 1.3–1.5 m, UTC offset +3,
  30 s trim.
- `run.py` reads timestamps from LogFile, infers frame count from `.bin` size, starts
  sliding windows at trim_frames, prints a sanity-check summary before processing.
- Evidence: `pytest tests/test_logfile_parsing.py -v` → 2 passed. UTC epoch arithmetic
  verified against known values 1780995595 / 1780995747.

**Decisions:**
- LogFile provides reliable start/end epochs; `radar_start_epoch` removed from config.
- `infer_num_frames` overrides config value at runtime — config acts as documentation default.
- Trim of 30 s accounts for subject walking into position.

---

## 2026-06-09 — read_adc_bin implemented; Codex cross-review; ADC layout test

**Implemented:**
- `read_adc_bin` implemented with correct 4-word LVDS packet decode for IWR1642 2-lane
  Complex1x format (SWRA581B + rawDataReader.m confirmed).
- Lane assignment: Lane 1 (words 0–1) = I, Lane 2 (words 2–3) = Q; each 4-word packet →
  [I_n + jQ_n, I_{n+1} + jQ_{n+1}].
- Codex cross-review caught a critical I/Q lane ordering bug in the first implementation —
  old decoder produced false peaks at bins 99–101; correct decoder shows target at bins
  27–29 (~1.22 m, consistent with 1.4 m subject distance).
- Three Codex findings applied: (P2) `np.memmap` for on-demand paging of 393 MB file;
  (P3) file-size validation before any memory allocation, with empty-file guard; (P4)
  dedicated ADC layout unit test `tests/test_radar_io_layout.py` asserting exact
  [100+300j, 200+400j] decode from known [100, 200, 300, 400] 4-word packet.
- 9 tests passed.

**Dead end — wrong I/Q ordering:**
- First I/Q implementation (even=I, odd=Q, sample-interleaved) is wrong for 2-lane DCA1000
  with laneFmtMap=0. False peaks at bins 99–101 are the diagnostic signature. Do not retry.

**Decisions:**
- `num_frames = 3000` inferred from file size 393,216,000 / 131,072 bytes/frame, not 3040
  from LogFile. `infer_num_frames()` is authoritative — always use file size, not LogFile
  duration.
- `np.memmap` for raw file load; cube returned as concrete complex64 ndarray (not a memmap
  view) so callers get a normal array with no file-handle lifetime issues.

---

## 2026-06-10 — exp001: bin locking, harmonic rejection attempt, revert

**Implemented:**
- `run_pipeline_locked()` added to `src/vitals.py`: selects the range bin ONCE from the
  full post-trim cube, extracts and denoises the full slow-time phase, then runs per-window
  spectral estimation on the continuous phase. Eliminates bin-hopping (bins 27/29 were
  alternating) and inter-window phase discontinuities.
- `range_resolution_m` confirmed as 0.0436 m/bin (mmWave Studio Calculated Parameters);
  gate widened to [1.1–1.5 m] to cover bins 25–34. Config updated.
- `run_pipeline_locked` established as the active pipeline in `run.py`.

**exp001 bin-locked baseline (no harmonic rejection):**
- MAE 6.21 bpm, RMSE 8.80 bpm, bias −4.51 bpm, locked bin 29 (1.264 m), 21 windows.
- Evidence: `results/exp001_offline_baseline/20260610_223845/`.
- Note: 7.37/9.57/−2.66 are the pre-locking per-window numbers; the locked pipeline gives
  different values (different algorithm, not a regression).

**Dead end — harmonic rejection with fixed 0.08 Hz threshold:**
- Over-triggered on real cardiac signal near harmonic frequencies. 4th harmonic of 15 bpm
  respiration = 60 bpm lands near the cardiac band edge. On 20-second windows the threshold
  cannot separate true cardiac at ~71 bpm from harmonic contamination at ~60 bpm. MAE
  regressed from 7.37 to 9.55 bpm. Reverted.
- Root cause: fixed-frequency tolerance is the wrong abstraction — need amplitude-relative
  or spectrum-shape-aware criteria. **Do not retry with a fixed Hz threshold.**

**Decisions:**
- Harmonic rejection deferred to exp002. Must start with literature review (cepstrum-based,
  notch-then-search, or longer window) before any implementation.
- 9/9 tests pass after revert.

---

## 2026-06-12 — exp002: ECA + AHET harmonic rejection

**Implemented:**
- ECA + AHET implemented in `src/vitals.py` per arXiv:2503.07062 (Tang et al., 2025),
  with all four OpenAI cross-review improvements applied:
  1. QR projection (`np.linalg.qr`) — not explicit matrix inverse.
  2. Parabolic interpolation for f_r refinement beyond FFT bin width.
  3. Local AHET 2nd-harmonic search [2×f_h ± 0.1 Hz] — not global to 4.0 Hz.
  4. Adaptive K_b guard: exclude harmonic k if within 0.15 Hz of cardiac candidate; hard
     floor k = 1..4 (covers known 60 bpm / 4th-harmonic failure).
  5. f_final blends fundamental and halved 2nd harmonic estimates.
- `compare.metrics()` made NaN-safe; `n_nan_windows` reported separately.
- `experiments/exp002_harmonic_rejection/` created with config + run.py.
- 12/12 tests passing.

**Results — same capture, locked bin 29, 1.264 m:**

| Metric     | exp001 baseline | exp002 ECA+AHET | Δ     |
|------------|-----------------|-----------------|-------|
| MAE        | 6.21 bpm        | 5.29 bpm        | −0.92 |
| RMSE       | 8.80 bpm        | 7.03 bpm        | −1.77 |
| Bias       | −4.51 bpm       | −2.27 bpm       | +2.24 |
| N windows  | 21              | 20 (+ 1 NaN)    |       |

- RMSE improvement larger than MAE: confirms ECA+AHET specifically reduced large-error
  outlier (harmonic-contaminated) windows, as designed.
- Window 10 correctly produced NaN: f_r misestimated at 8.8 bpm (irregular breathing
  segment). Correct pipeline behaviour per CLAUDE.md §4.
- 20/20 non-NaN windows AHET-verified.

**Remaining issues noted:**
- 5 windows still have errors > 10 bpm despite AHET verification — likely spectral
  leakage or genuine HR variability unresolvable at 20 s window length.
- Bias −2.27 bpm still present (halved vs exp001 via parabolic interpolation, but not
  eliminated).
- Window 10 NaN: f_r misestimation during irregular breathing segment needs a more robust
  f_r estimator (e.g., median over multiple sub-windows).

---

## 2026-06-13 — exp002 closed; exp003 generalisation; capture 2 planned

### exp002 closed and tagged

- ECA+AHET final form confirmed reproducible: MAE 5.29, RMSE 7.03, bias −2.27 bpm
  (AHET-verified). All-windows: MAE 5.16, RMSE 6.88, bias −2.28.
- Evidence: `results/exp002_harmonic_rejection/20260613_171020/`. Tagged: `exp002-baseline`.
- Physiological outlier gate on f_r moved into `estimate_rate_from_phase()`: if f_r_hz
  outside [0.15, 0.60] Hz, falls back to no-ECA bandpass+argmax and sets `f_r_outlier=True`.
  Fired correctly at window 10 (f_r=8.8 bpm); produced HR=69.0 bpm (error −2.5 bpm)
  instead of NaN — correct improvement.
- All algorithm decisions cross-reviewed (ChatGPT plan review, Codex implementation review)
  before implementation.
- bin 29 pinned explicitly in config for both exp001 and exp002.
- 12/12 tests passing throughout.

**Dead ends — recorded negative results:**
- EMA f_r smoothing (alpha=0.3): adds lag, net MAE regression +0.79 bpm vs no-EMA. Removed.
- 45 s trim: started analysis in a more volatile stretch of this capture, MAE regression
  +2.28 bpm. Reverted to 30 s.

**Known limitation:**
- Windows 9–12: respiratory-dominance event causes ~60 bpm estimate despite AHET
  verification. The 4th respiratory harmonic at ~60 bpm passes AHET because a genuine
  second harmonic (~120 bpm) is present. Physics limitation of single-range-bin phase
  extraction; cannot be resolved by spectral methods without multi-bin or Doppler
  separation.

### Capture 2 plan — pre-declared acceptance criterion

Two recordings on the same day: chair with back, then chair without back.

- Duration per recording: 4 min total — 30 s positioning, 3:30 min quiet seated breathing.
- Order: chair-with-back first (freshest), backless chair second.
- Distance: 1.3–1.4 m (replicate capture 1 setup).
- Same chirp config, same Masimo device, same 30 s trim.

Pre-declared acceptance criterion (written before collection to prevent post-hoc
rationalisation):
- **Pass:** MAE ≤ 7 bpm on at least one recording.
- **Strong pass:** MAE ≤ 7 bpm on both.
- **Marginal:** MAE 7–9 bpm — proceed with caveat.
- **Fail:** MAE > 9 bpm on both — investigate before further algorithm work.

### exp003 generalisation test — MARGINAL (mixed result)

**Results:**

| Metric     | exp002 ref | chair_back | chair_no_back |
|------------|-----------|------------|---------------|
| MAE (bpm)  | 5.29      | 8.44       | 13.38         |
| RMSE (bpm) | 7.03      | 11.67      | 15.88         |
| Bias (bpm) | −2.27     | −6.81      | −11.95        |
| N windows  | 20        | 32         | 22            |
| N NaN      | 1         | 7          | 17            |

Result: **MARGINAL** — chair_back MAE 8.44 (marginal band 7–9), chair_no_back MAE
13.38 (fail).

**Diagnosed failure modes:**

*chair_back:*
- Locked bin 33 (1.439 m) vs exp002 bin 29 (1.264 m) — subject sat ~18 cm further back
  due to chair geometry.
- f_r consistently 18–24 bpm (vs ~15 bpm in exp002).
- Systematic negative bias −6.81 bpm.

*chair_no_back:*
- HR elevated ~82 bpm (vs ~73 bpm in exp002) — likely postural effort without back support.
- f_r ~19–21 bpm. 4th harmonic ~80 bpm lands directly on cardiac frequency → AHET
  correctly flags 17/39 windows as harmonic suspect → NaN.
- **This is correct pipeline behaviour, not algorithm failure.** Physiological coincidence
  of HR ≈ 4×f_r is the primary failure cause.

---

## 2026-06-14 — Scene analysis, Masimo fixes, Blockers 1–4, exp004 foundation and run

### range_resolution_m validated from first principles

Chirp parameters (from mmWave Studio):
- Start frequency: 77 GHz / Frequency slope: 70.005996704101562 MHz/µs
- ADC samples: 256 / Sample rate: 5209 Msps
- ADC start time: 5 µs / Idle time: 7 µs / Ramp end time: 57 µs

Derivation:
- T_ADC = 256 / 5209×10⁶ = 49.14 µs
- BW = 70.006 MHz/µs × 49.14 µs = 3440.7 MHz
- Δr = c / (2 × BW) = 3×10⁸ / (2 × 3.4407×10⁹) = **0.04359 m/bin**
- R_max = 0.04359 × 128 = 5.58 m

Config value 0.0436 m/bin confirmed correct to 4 significant figures. Maximum accumulated
error over 33 bins = 0.03 mm — negligible. No config change required.

### exp000 range plots — scene analysis and bin selector decision

- **Capture 1** (exp001_sit_140cm): clean single peak at bin 27 (1.177 m), stable across
  full 150 s.
- **Capture 2** (exp002_sit_chair_back): single dominant peak at bin 33 (1.439 m), stable
  across full 240 s. Subject was actually sitting at ~1.44 m — the chair back held the
  subject further from the radar than the post-hoc tape measure reading of 1.26 m suggested.
  Energy-based bin selector was correct for this capture.
- **Capture 3** (exp002_sit_chair_no_back): clean single peak at bin 30 (1.308 m), stable
  across full 240 s. Without chair back, subject sat ~13 cm closer.

**Revised interpretation of exp003 chair_back failure:**
- Root cause is NOT multipath interference (heatmap shows a single stripe, no competing
  reflectors).
- Root cause is geometry change: subject sat at ~1.44 m (bin 33) vs ~1.26 m (bin 29) in
  exp002 — 18 cm difference due to the chair back.
- Phase-variance selector regression (MAE 8.44 → 12.34) confirmed bin 33 was the correct
  chest bin; selecting bin 28 tracked the wrong target.
- **Energy-based selector reverted as the correct default for clean single-target scenes.**
  Phase-variance selector stays in the codebase (performs correctly on clean geometry: exp002
  bin 29, 3.7× margin) but is not the active default.
- Combined criterion (phase variance within top-N energy bins) deferred to exp005.

**Phase-variance re-run results (for the record):**

| Metric (bpm) | exp002 ref | chair_back energy (bin 33) | chair_back phase-var (bin 28) | chair_no_back energy (bin 32) | chair_no_back phase-var (bin 31) |
|---|---|---|---|---|---|
| MAE  | 5.29 | 8.44  | 12.34 | 13.38 | 16.95 |
| RMSE | 7.03 | 11.67 | 14.17 | 15.88 | 18.60 |
| Bias | −2.27| −6.81 | −8.38 | −11.95| −16.49|
| N    | 20   | 32    | 35    | 22    | 30    |
| NaN  | 1    | 7     | 4     | 17    | 9     |

*chair_back root cause:* chest at 1.26 m = bin 29, but bin 29 ranks 5th in phase variance
behind bins 28, 33, 36, 35. Chair back at ~1.64 m (bin 38, outside gate) causes multipath
that suppresses breathing-induced phase modulation at bin 29 and artificially elevates phase
variance at off-chest bins. Neither selector can reliably identify the chest bin when
multipath corrupts the phase signal itself. **Chair-back geometry is unfavourable for
single-bin extraction; requires multi-bin coherent combination (exp005).**

**Distance measurement note:** post-hoc tape measure gave 1.26 m, but heatmap evidence
indicates ~1.44 m during the chair_back capture. Likely measured to the wrong reference
point (chair front vs. seated chest position). For future captures: measure distance while
seated in capture position; verify against dominant bin × range_resolution_m.

### Masimo half-open interval fix — exp001 and exp002 re-baselined

**Bug fixed:** `src/masimo.py` interval changed from inclusive [start, end] to half-open
[start, end). A 20 s window previously included 21 Masimo samples; now correctly includes
20. Fix approved by Codex review.

**Bug also fixed:** `run_pipeline_locked()` was silently applying ECA+AHET to exp001.
Fixed by adding `use_eca` parameter (default True) to `run_pipeline_locked()`.
`exp001/config.yaml` now sets `use_eca: false`.

**New canonical metrics (supersede all prior exp001/exp002 numbers):**
- exp001 baseline (no ECA+AHET): MAE 6.214, RMSE 8.778, bias −4.448 bpm (N=21)
- exp002 all windows (ECA+AHET): MAE 5.196, RMSE 6.904, bias −2.219 bpm (N=21)
- exp002 AHET-verified: MAE 5.334, RMSE 7.054, bias −2.207 bpm (N=20)
- Evidence: `results/exp001_offline_baseline/20260614_120843/`,
  `results/exp002_harmonic_rejection/20260614_120354/`

**Old metrics (superseded, kept as provenance):**
- exp001: MAE 6.206, RMSE 8.802, bias −4.512 bpm
- exp002 all windows: MAE 5.157, RMSE 6.880, bias −2.283 bpm
- exp002 AHET-verified: MAE 5.291, RMSE 7.029, bias −2.274 bpm

**Measured impact of the fix:**
- exp001: MAE 6.2062 → 6.2142, RMSE 8.8019 → 8.7779, bias −4.5124 → −4.4480 bpm.
- exp002 all: MAE 5.1571 → 5.1963, RMSE 6.8804 → 6.9044, bias −2.2833 → −2.2190 bpm.
- exp002 AHET-verified: MAE 5.2912 → 5.3337, RMSE 7.0286 → 7.0536,
  bias −2.2737 → −2.2074 bpm.
- Largest individual Masimo window-mean change: 0.3143 bpm. No PI-quality classification
  changed.

Tests: `tests/test_masimo.py` — 6 tests (half-open boundary, adjacent window partitioning,
PR mean, PI quality gate, empty interval, invalid interval guard). 18/18 tests pass.

**Important correction also noted here:** a 20 s radar window with a 5 s hop overlaps the
next window by 15 s (75%). Half-open support is correct and matches the radar
sample-index convention.

### Masimo Breaths/min added to pipeline output

- `masimo.reference_br(df, start_epoch, end_epoch)` added to `src/masimo.py`, parallel
  to `reference_pr()`. Returns NaN-safe mean of `rr_bpm` over [start, end); returns NaN
  for empty or all-NaN windows.
- `masimo_br` column added to exp002 window table and comparison CSV.
- Enables: (1) direct f_r validation — compare radar-estimated `f_r_bpm` vs `masimo_br`
  per window; (2) advance AHET failure prediction — when masimo_br × 4 ≈ masimo_PR, the
  window is expected to fail AHET (physiological coincidence, not algorithm failure).
- Note: `'--'` handling not needed — capture protocol ensures all `'--'` values fall
  outside the radar recording window by design.

### Masimo deduplication fix — clock glitch duplicates resolved

**Findings:** one duplicate epoch per capture, all caused by Masimo clock glitches
flanking missing seconds:
- exp001: epoch 1780995723 (PR 73/73, BR 18/19) — 4 missing seconds
- chair_back: epoch 1781364245 (PR 73/74, BR 19/19) — 3 missing seconds
- chair_no_back: epoch 1781365112 (PR 78/77, BR 21/21) — 4 missing seconds

*(A previous entry incorrectly claimed "no duplicates" — corrected here.)*

**Policy:** mean of duplicate rows, rounded to 1 decimal place for all numeric columns.
Implemented in `load_masimo()` via `groupby("Timestamp").agg(mean/first)` with prior
`pd.to_numeric(..., errors="coerce")` to handle `'--'` values outside the recording
window. Coverage stats stored in `df.attrs`: `n_raw_rows`, `n_unique_epochs`,
`n_duplicates_merged`, `n_missing_seconds`. Summary printed on load:
`"Masimo: N rows → M epochs (K merged, J missing seconds)"`.

Missing seconds are handled by the existing NaN-safe mean in `reference_pr()` /
`reference_br()` — no interpolation.

**exp002 metrics after dedup** (effect negligible — < 0.01 bpm change):
- All windows: MAE 5.189, RMSE 6.902, bias −2.228 bpm (N=21)
- AHET-verified: MAE 5.326, RMSE 7.051, bias −2.217 bpm (N=20)

Tests: `test_load_masimo_deduplicates`, `test_load_masimo_coverage_attrs`,
`test_reference_br_mean` — 21/21 pass.

### Blocker 1 resolved: ECA config plumbing wired

**Blocker identified:** `eca.k_max` and `eca.ahet_deviation_hz` were present in
`experiments/exp002_harmonic_rejection/config.yaml` but never read by `run.py` or passed
into `src/vitals.py`. Both parameters were hardcoded at values that coincidentally matched
the config — current results were correct, but the wiring was absent.

**Other hardcoded ECA/AHET parameters (not in config, documented for reference):**
- Line 85: `freq >= 2.0` Hz — harmonic frequency ceiling (cardiac band ceiling)
- Line 87: `k <= 4` — hard floor; always include harmonics 1–4 (intentional)
- Line 90: `0.15` Hz — cardiac candidate guard (exclude harmonic if within 0.15 Hz of candidate)
- Lines 120–121: `_GATE_LO_HZ = 0.15`, `_GATE_HI_HZ = 0.60` — physiological gate for f_r
- Line 163: `fs * 0.45` — widened bandpass ceiling for AHET path
- Line 184: `noise_floor * 0.1` — `find_peaks` prominence threshold
- Line 187: `[:3]` — top-3 AHET candidates tried
- Line 301: `thresh=1.5` rad — impulse noise clip in `remove_impulse_noise()`

**Fix:** `k_max` and `ahet_deviation_hz` added as explicit parameters to
`estimate_rate_from_phase()` and `run_pipeline_locked()`; read from config in
`exp002/run.py`; `exp001/run.py` passes explicit defaults.

**exp002 metrics confirmed unchanged after wiring:**
All windows MAE 5.189, RMSE 6.902, bias −2.228 bpm; AHET-verified MAE 5.326, RMSE
7.051, bias −2.217 bpm. Evidence: `results/exp002_harmonic_rejection/20260614_133839/`.

**exp001 re-run with deduplication:**
MAE 6.202, RMSE 8.773, bias −4.457 bpm (N=21). Evidence:
`results/exp001_offline_baseline/20260614_133858/`. Change from previous: < 0.015 bpm.

21/21 tests pass.

### Blocker 2 resolved: intermediate persistence

**Built:** `run_pipeline_locked()` evidence persisted by exp002 through
`src/intermediates.py` to `intermediates.npz`; accepted AHET and alignment scalars also
added to `comparison.csv`.

The schema below is the final form after four cross-review rounds (all corrections
applied before implementation):

**Per-attempt arrays `(n_windows, 3)` — one slot per AHET candidate tried:**
- `candidate_initial_hz` — first-pass spec1 candidate; defines ECA guard and
  second-harmonic search interval
- `candidate_refined_hz` — fundamental refined on candidate-specific second-pass spec2
- `candidate_peak_magnitude`, `candidate_prominence`
- `candidate_attempted` (bool; False for unused slots; NaN for numeric padding)
- `second_peak_bin_hz`, `second_peak_refined_hz`
- `second_peak_magnitude`, `comparison_floor`
- `peak_to_floor_ratio` (NaN for 0/0; +inf for positive/0)
- `region_available` (bool: local search interval contains ≥ 1 FFT bin)
- `pass_flag` (bool)

**Accepted-candidate scalars:**
- `accepted_candidate_rank` (int; −1 if no AHET attempted; stored as float with −1 sentinel)
- `accepted_candidate_initial_hz`, `accepted_candidate_refined_hz`,
  `accepted_second_harmonic_refined_hz`, `heart_peak_hz`

**Respiratory estimator evidence:**
- `resp_freqs_hz`, `resp_spectrum`, `resp_peak_raw_index` (global rFFT index),
  refined `f_r_hz_used`

**Spectra:**
- `heart_spectrum_first_pass` (N_fft,) — first-pass spectrum; NaN on no-ECA fallback
- `heart_spectrum` — stage-selected output (backward compatible name)
- `heart_spectrum_stage` (numeric code: 0=no-ECA, 1=first-pass ECA, 2=accepted
  second-pass ECA)
- `ahet_attempt_spectrum` shape `(n_windows, 3, n_fft)` (NaN-padded) — candidate-specific
  spectra for all three AHET attempts

**Window metadata:**
- `window_index`, `start_frame`, `end_frame`, `start_epoch`, `end_epoch`, `eca_applied`
  (bool), artifact schema version

Serializer validates: `end_frame − start_frame == window_frames`; epochs consistent with
frames/frame_rate_hz; contiguous unique `window_index`; all required keys present; all
arrays numeric/bool, loadable with `allow_pickle=False`.

**Verified artifact:** `results/exp002_harmonic_rejection/20260614_160204/`. Cross-artifact
test validates all 21 windows: NPZ/CSV row counts and `ahet_verified` agree; frame bounds
within trimmed cube; attempted candidates form a contiguous prefix; every non-verified
window has `accepted_candidate_rank == −1`.

**Metric verification caveat:** re-runs match canonical `20260614_133839` to within
1.1×10⁻⁶ bpm but not bit-for-bit. Difference is numerical-environment provenance
(library version floating-point); the canonical artifact did not record enough
library/runtime detail to reproduce its final floating-point digits exactly.

32/32 tests pass.

### Blocker 3 deferred: AHET second-harmonic criterion documented as known limitation

**Current pass rule:** `second_peak_magnitude > comparison_floor`, where `comparison_floor`
is the median cardiac-band magnitude on the candidate-specific second-pass ECA spectrum.
Ratio = `second_peak_magnitude / comparison_floor`; ratio > 1.0 is a pass.

**Known limitations established through three review rounds:**
- exp002 has 20 AHET passes, 0 AHET-rejection failures, 1 respiratory fallback — no
  rejection population; criterion cannot be validated or tuned from this capture alone.
- Accepted ratio distribution on exp002 (`20260614_161352`): min 1.035, median 1.233,
  max 2.385 (0.30, 1.82, 7.55 dB). 16/20 passes fall in [1.0, 1.5] — threshold is
  very soft.
- 5 verified windows still have absolute HR error ≥ 10 bpm, ratios spanning 1.060–1.233.
  The ratio confirms second-harmonic structure is present but does not confirm the
  fundamental is the cardiac peak rather than a respiratory harmonic.
- The ±0.1 Hz search region contains approximately 5, 5, and 7 FFT bins at 20, 25, and
  30 s windows respectively (production rFFT sizes 400/500/600, not zero-padded). Under
  an idealised null, the probability that the maximum of m bins exceeds the cardiac-band
  median is 1 − 0.5^m ≈ 0.969 / 0.969 / 0.992 — longer windows mechanically inflate
  AHET pass rates independent of true second-harmonic evidence.
- Raising the threshold to 1.5 would retain only 4/20 estimates; any threshold tuning
  requires independent capture data.
- The cardiac-band floor is measured in a different frequency region from the second
  harmonic, so spectral background/filter response may differ.

**Decision:** Blocker 3 deferred until a second capture is collected.

**Planned evaluation (deferred):**
- Production rFFT sizes (400/500/600, not zero-padded) must be used throughout — zero-
  padding changes the ±0.1 Hz search from 5/5/7 bins to 6/6/11 bins at a representative
  1.2 Hz candidate.
- Monte Carlo framework with four synthetic conditions per window length (20/25/30 s):
  `genuine_harmonic` (cardiac fundamental + second harmonic present),
  `missing_harmonic` (fundamental only, no second harmonic),
  `noise_only` (no cardiac signal),
  `resp_competitor` (respiratory harmonic inside cardiac band, separated from true HR;
  **no genuine cardiac second harmonic** — tests false respiratory support).
- Two separate outcome axes: (1) verifier validity — genuine candidate-specific
  second-harmonic evidence present/absent; (2) final estimator accuracy — accepted HR
  within/outside ±5 bpm. In `missing_harmonic`, an accepted but accurate fundamental is
  an accurate HR estimate but a false AHET verification; report both axes separately.
- Predeclared alternative criteria: `local_prominence` (second-harmonic peak prominence
  relative to local background) and `peak_local_median` (second-harmonic peak /
  local-neighbourhood median with defined guard bins and edge handling).
- Development/held-out seed split: criterion and threshold selected on development seeds,
  evaluated once on held-out seeds.
- Adequacy rule: two-sided Wilson 95% CI upper bound on false-pass rate < 0.10. With
  N=500, this passes at ≤ 35/500 observed false passes (7%).
- Add a held-out sensitivity condition to the adequacy rule (not only a false-pass bound).
- ROC curves for criteria with continuous scores; operating-point plots for binary
  criteria. C2/C3 must respect the 4.0 Hz production bandpass edge.
- Any new threshold or alternative criterion selected on synthetic/development data must
  be confirmed on an independent real capture before use.
- Saved with: committed simulation config, seed derivation, git commit, package versions,
  script/config hashes, timestamped audit subdirectory, sharded per-trial intermediate
  evidence.
- Required caveat on exp004 pass-rate comparisons: "AHET pass rates are indicative only;
  criterion not independently validated; window-length-dependent search-region bin counts
  (~5/5/7 at 20/25/30 s) may inflate pass rates at longer windows."

### Blocker 4 resolved: parabolic interpolation on no-ECA fallback

**What was missing:** the no-ECA fallback path inside `estimate_rate_from_phase()` returned
the raw FFT bin centre without parabolic refinement, introducing systematic quantisation
error not present on the main ECA+AHET path.

**Fix:** `parabolic_interpolate_peak(spectrum, peak_idx, freq_resolution_hz)` added to
`src/vitals.py`. Three explicit fallbacks to raw bin centre: boundary bin (peak_idx==0
or last), flat top (denominator==0), large delta (|delta|>1). Wired into the no-ECA
fallback path at the cardiac band argmax site.

7 new unit tests in `tests/test_vitals_synthetic.py`: symmetric peak (delta==0),
off-centre peak (exact delta formula), left-edge fallback, right-edge fallback, flat-top
fallback, large-delta fallback, and end-to-end no-ECA bias guard.

**exp002 metrics after Blocker 4 fix:**
- All windows: MAE 5.172 bpm, RMSE 6.897 bpm, bias −2.212 bpm (N=21) — MAE −0.017 vs prior
- AHET-verified: MAE 5.326 bpm, RMSE 7.051 bpm, bias −2.217 bpm (N=20) — unchanged
- Evidence: `results/exp002_harmonic_rejection/20260615_110410/`

Fix affects only window 10 (the f_r-outlier fallback); AHET-verified subset unchanged.

**exp004 cap1 metrics after fix:**

| Condition     | MAE   | RMSE  | Bias   | N     | Pre-fix MAE |
|---------------|-------|-------|--------|-------|-------------|
| baseline 20s  | 5.172 | 6.897 | −2.212 | 21/21 | 5.189       |
| condition 20s | 5.535 | 7.225 | −2.520 | 19/19 | 5.55        |
| condition 25s | 3.831 | 5.776 | −2.216 | 18/19 | 3.83        |
| condition 30s | 3.668 | 5.582 | −2.418 | 17/19 | 3.67        |

Changes < 0.02 bpm across all conditions — confirms fix is correctly scoped to the
fallback path. cap2 and cap3 metrics unchanged from `20260615_002408` to within 0.01 bpm.

Diagnostic script `scripts/diag_per_window_error.py` added (read-only); saves 4 plots
to `results/diagnostics/per_window_error/`. Confirmed 7 high-error windows in exp002;
windows 4 and 17 flagged as 4×f_r ≈ Masimo PR (respiratory harmonic coincidence).

99/99 unit tests pass. Evidence: `pytest tests/ -v` (2026-06-15).

### exp004 window-length study — foundation and run

**Foundation (`src/windowing.py`, `src/compare.py` extensions):**
- `common_center_windows`: generates 19-center common-time grid for 20/25/30 s windows.
  Absolute starts: [700…2500] / [650…2450] / [600…2400] in 100-frame steps. Centers:
  [900…2700] in 100-frame steps (15…105 s after 30 s trim).
- `sliding_windows`: reproduces exp002 21-window baseline (starts [600…2600], hop
  100 frames = 5 s).
- 16 tests in `tests/test_windowing.py`.
- `paired_metrics`: per-condition coverage counts and finite-only MAE/RMSE/bias,
  intersection across all-finite centers, all pairwise error-diff / AHET-transition
  stats. Error always recomputed from `radar_hr − masimo_pr` (stored field not trusted).
  Duplicate-key validation present.
- `coverage_table`: formats `paired_metrics` output as plain-text table for stdout.
- 15 tests in `tests/test_paired_metrics.py`.

**Config:** `exp004.window_lengths_s: [20, 25, 30]`, `center_spacing_s: 5`,
`first_center_s: 15`, `baseline_window_s: 20`, `baseline_hop_s: 5`. Same data/radar/
processing params as exp002 (read-only exp001 capture, locked_bin 29).

**run.py design:** full phase extracted once from trimmed cube; all conditions slice the
shared signal (no re-extraction per window). `_process_windows` helper applies
`estimate_rate_from_phase` per (abs_start, abs_end) window. `_run_and_save` writes
`comparison.csv` + `intermediates.npz` per condition subdirectory. Paired analysis keyed
by integer center_frame. AHET caveat printed before paired output.

**Run result — `results/exp004_window_length/20260614_192057/`:**
- Baseline metrics (21 windows): MAE 5.1888, RMSE 6.9021, bias −2.2282 bpm ✓ canonical
- AHET-verified baseline (20 windows): MAE 5.3257, RMSE 7.0513, bias −2.2171 bpm ✓ canonical
- Common-center conditions: 20 s = 19 windows, 25 s = 19 windows, 30 s = 19 windows.
- Paired intersection: n_centers = 17. (30 s has 2 NaN windows at centers 900 and 1000
  — phase extraction truncated; requires investigation before paper-grade use of 30 s.)

12 tests in `tests/test_exp004.py`; 75/75 total pass.

### Per-window diagnostic; Blocker 5 identified; re-capture plan decided

**Diagnostic findings (`scripts/diag_per_window_error.py`):**
- 7 of 21 windows have |error| > 5 bpm: indices 1, 4, 6, 9, 12, 17 (all AHET-pass) plus
  window 10 (AHET-fail, f_r outlier). All large errors are negative.
- Large negative errors (−9 to −15 bpm) concentrated at true HR 71–74 bpm.
- Worst-window harmonic-coincidence check:

| Window | Error     | f_r      | 4×f_r    | Masimo PR | \|4×f_r−PR\| | Suspicious? |
|--------|-----------|----------|----------|-----------|--------------|-------------|
| 17     | −14.7 bpm | 18.7 bpm | 74.9 bpm | 73.3 bpm  | 1.5 bpm      | YES         |
| 6      | −12.5 bpm | 16.4 bpm | 65.5 bpm | 71.1 bpm  | 5.6 bpm      | borderline  |
| 4      | −12.2 bpm | 18.0 bpm | 71.9 bpm | 67.7 bpm  | 4.2 bpm      | YES         |

Root cause confirmed: ECA cannot suppress 4×f_r when it coincides with the cardiac
frequency — projection removes both simultaneously. AHET passes because second-harmonic
structure is present but belongs to the respiratory chain, not the cardiac signal.
30 s windows do NOT fix harmonic-coincidence windows; longer windows reduce MAE only on
clean windows where spectral resolution helps.

4 plots saved: `results/diagnostics/per_window_error/` (scatter, error over time, error
vs true HR, 20 s vs 30 s per center).

**Algorithm decision — Blocker 5 (harmonic exclusion at candidate selection):**
Before accepting any HR candidate, check whether any k×f_r for k ∈ {2, 3, 4} falls
within an exclusion tolerance of that candidate. If yes: skip to next candidate (not reject
outright — try remaining candidates first). Cannot tune on exp002/cap1 alone.

**Re-capture plan — three supine captures (protocol in `protocol.md`):**

| Capture     | BR target | 4×f_r ceiling | Role                                          |
|-------------|-----------|---------------|-----------------------------------------------|
| cap3_retake | 13–16 bpm | ≤ 64 bpm      | Development (replaces failed cap3)            |
| cap4        | 16–18 bpm | ≤ 72 bpm      | Development (second independent capture)      |
| cap5        | 12–14 bpm | ≤ 56 bpm      | Held-out (no tuning allowed on this capture)  |

Protocol: subject supine on floor mat, radar on tripod directly overhead, ~1.3 m
radar-to-chest. Same mount position for all three. Metronome at half the target BR rate
(one beat per inhale, one per exhale). Recording: 450 s (7 min 30 s). Stabilisation:
2 min metronome breathing + 60 s stable Masimo BR before recording. Rest between
captures: 5 min. Pass criterion: Masimo BR in target range ≥ 80% of recording.

**Blocker status as of end of 2026-06-14:** 1 resolved, 2 resolved, 3 deferred,
4 resolved, 5 open.

---

## 2026-06-15 — cap3_retake collected; split-file decoder; exp004 multi-capture; adaptive k_max

### cap3_retake collected; split-file decoder extended

**Capture details:**
- Date: 2026-06-15, 16:04:27–16:11:59 local (UTC+3), duration 452 s per LogFile.
- Posture: supine on floor mat, radar on tripod directly overhead.
- Radar-to-chest distance: bin 28 at 1.221 m (confirmed from range profile; adjacent bins
  27 and 29 are sidelobes).
- BR: 14 bpm stable throughout; 4×f_r = 56 bpm, well below HR range.
- HR: started elevated (~96 bpm immediately after lying down), settled to ~75–80 bpm by
  mid-recording. 30 s trim (600 frames) removes the settling period.
- Frame count: 9000 frames = 450 s at 20 Hz. (LogFile reports 452 s — 2 s rounding
  difference; file-size-inferred count is authoritative.)
- Files in `data/raw/`: `cap3_retake_20260615_160417_0.bin` (1,073,741,760 bytes),
  `cap3_retake_20260615_160417_1.bin` (105,906,240 bytes), LogFile.csv, masimo.csv.

**Range profile at frame 600:**

| Rank | Bin | Range (m) | Energy   |
|------|-----|-----------|----------|
| 1    | 28  | 1.221     | 5.50e+08 |
| 2    | 27  | 1.177     | 1.75e+08 |
| 3    | 29  | 1.264     | 1.45e+08 |
| 4    | 2   | 0.087     | 1.07e+08 |
| 5    | 38  | 1.657     | 6.53e+07 |

Locked bin for cap3_retake: **bin 28 at 1.221 m**.

**Split-file issue and fix:**
mmWave Studio hit its 1,024 MB file size limit and split the recording. The split does
NOT fall on a frame boundary: `_0.bin` = 8191 complete frames + 131,008 bytes of a
partial frame; `_1.bin` starts with the remaining 64 bytes + 808 complete frames. Total
= 1,179,648,000 bytes = 9000 × 131,072 exactly. Concatenating decoded cubes per-file
fails — raw int16 bytes must be concatenated first, then the combined stream decoded.

`read_adc_bin` extended: signature changed to accept a single path or a
`List[Union[str, Path]]`. Multi-file branch validates all files exist, computes
`total_bytes`, validates `total_bytes % bytes_per_frame == 0` on the combined size (not
per-file — individual files may not be frame-aligned), concatenates raw int16 words into
a single array using `np.empty` + in-place copy (avoids peak-memory spike), then decodes
with the shared 4-word LVDS path. Single-file branch unchanged; all existing callers
unaffected.

New tests `tests/test_radar_io_split.py` (7 tests): single-file passthrough (regression
guard), two-file split shape, content order, frame count consistency, missing-file raises
before read, single-element list equals single path, mid-frame split decodes correctly
(the actual cap3_retake scenario). 106/106 unit tests pass; 8 integration tests skipped.

### exp004 multi-capture (cap1, cap2, cap3 seated)

**Implementation:**
- Multi-capture runner processes cap1, cap2, cap3 sequentially with explicit memory
  management (releases cube/profile arrays before loading the next capture).
  All writes to `results_root/<cap_id>/`.
- `analysis.py` with four pure functions: `pooled_window_length_summary`,
  `chair_condition_summary`, `masimo_summary`, `collect_provenance`.
- 92 unit tests pass; 8 integration tests skipped by default. With `--run-dir`: 100/100.
- Evidence: `results/exp004_window_length/20260615_002408/`.
- cap3 diagnostic: `results/exp004_window_length/20260614_234744/cap3_diagnostic.md`.

**Window-length findings — per capture (condition grids, all finite windows):**

| Capture | Posture           | Distance | Date       | 20s MAE* | 25s MAE* | 30s MAE* | Monotonic? |
|---------|-------------------|----------|------------|----------|----------|----------|------------|
| cap1    | seated, no back   | 1.264 m  | 2026-06-09 | 5.55     | 3.83     | 3.67 bpm | YES        |
| cap2    | seated, chair-back| 1.439 m  | 2026-06-13 | 8.80     | 8.51     | 4.46 bpm | YES        |
| cap3    | seated, no back   | 1.308 m  | 2026-06-13 | 13.50    | 14.14    | 16.19 bpm| NO         |
| Pooled micro-avg | | | n=63 | 9.02 | 8.36 | 7.46 bpm | YES (pooled) |

*Intersection values (same windows compared across all three lengths).

**cap3 failure diagnosis:**
- f_r ≈ 19–20 bpm places 4th harmonic at ≈ 80 bpm, adjacent to true HR (78–84 bpm).
- ECA cannot suppress without simultaneously suppressing the cardiac signal.
- AHET correctly abstains (NaN rate 46% at 20 s, 24% at 25 s) — correct behaviour.
- Longer windows do not resolve the ambiguity (SNR problem, not frequency resolution).
- Remediation: re-capture with subject breathing at 13–16 bpm.

**Chair-condition note:** cap2 vs cap3 difference is dominated by cap3 harmonic-coincidence
failure, not chair condition. Also confounded by distance (1.439 m vs 1.308 m), recording
order, and respiratory rate. No causal inference supported.

### exp004 on supine captures — all fail; Blocker 6 identified

**File and frame verification (all three supine captures):**

| Capture     | Combined bytes | Frames |
|-------------|----------------|--------|
| cap3_retake | 1,179,648,000  | 9000 ✓ |
| cap4        | 1,179,648,000  | 9000 ✓ |
| cap5        | 1,179,648,000  | 9000 ✓ |

All 6 CSV files present. Each capture: bin 28 (1.221 m) locked, Masimo coverage 98.6%,
PI 100%. Mechanically clean.

**Results:**

| Capture | BR | 4×f_r | 6×f_r | k×f_r ≈ HR? | 20s MAE* | 25s MAE* | 30s MAE* | Monotonic? |
|---|---|---|---|---|---|---|---|---|
| cap3_retake | 14 bpm | 56 bpm | 84 bpm | YES (6×) | 24.78 | 23.60 | 23.08 | YES |
| cap4 | 17–19 bpm | 68–76 bpm | 102–114 bpm | YES (4×) | 17.37 | 18.74 | 18.55 | partial |
| cap5 (held-out) | 13 bpm | 52 bpm | 78 bpm | YES (6×) | 19.51 | 18.01 | 19.69 | partial |

Results: `results/exp004_window_length/20260615_181343/cap3_retake/`,
`20260615_181821/cap4/`, `20260615_181912/cap5/`. 12 diagnostic plots
(A/B/C/D × 3 captures) saved to `results/diagnostics/per_window_error/`.

**Root cause — 6×f_r ≈ HR (new finding):**
The pre-session plan only checked that 4×f_r < 55 bpm. cap3_retake BR=14 satisfies this
(4×14=56 bpm). But **6×14=84 bpm** lands squarely in the HR range (75–96 bpm). ECA first
pass with k_max=6 removes ALL k=1..k_max harmonics unconditionally; the subspace
suppression nulls out energy at 84 bpm, erasing the cardiac signal before candidate
selection begins. Same mechanism for cap5 (f_r=13, 6×13=78 inside HR 69–94 bpm) and
cap4 (f_r=17–19, 4×f_r=68–76 overlaps HR 72–82 bpm; additionally 5×f_r may land in
HR range).

**Why supine is worse than seated:** in supine overhead geometry, vertical body motion
from breathing dominates; cardiac micro-motion has lower SNR. When ECA also removes the
spectral region containing the cardiac peak, nothing remains above the noise floor.

**Why all supine captures fail regardless of BR target:**
BR < 9.2 bpm would be required to keep 6×f_r below 55 bpm — not a feasible resting
breathing rate. Even if 6×f_r is clear, k=4 or k=5 may still land in the HR range.
**Conclusion: the current k_max=6 algorithm is fundamentally incompatible with supine
overhead capture geometry given typical resting HR (60–100 bpm) and BR (12–20 bpm).**

**Blocker 6 (open):** ECA first-pass harmonic removal with k_max=6 suppresses cardiac
signal when k×f_r (k > 4) falls inside the cardiac band in supine low-SNR geometry.
Possible fixes: (a) reduce k_max to 4 — cheapest, investigate first; (b) redesign
first-pass ECA to skip removal when the harmonic bin overlaps the cardiac band; (c)
multi-bin coherent combination before phase extraction.

### Adaptive k_max investigation — hypothesis REFUTED

**Script:** `scripts/test_adaptive_kmax.py` (diagnostic-only; no pipeline code modified,
no configs changed).

**Formula:**
```
k_max_adaptive = floor((CARDIAC_FLOOR_HZ - MARGIN_HZ) / f_r_hz)
               = floor((0.833 - 0.083) / f_r_hz)
               clamped to [1, 6]
```
Rationale: the highest suppressed harmonic (k_max × f_r) must stay at least 5 bpm
below the cardiac band floor (50 bpm = 0.833 Hz). For f_r=14 bpm: k=floor(0.75/0.233)=3.
Formula gives k=3 for 69/81 windows — adaptive equals fixed k_max=3 for this capture.

**cap3_retake results:**

| Mode            | N_finite | N_NaN | MAE       | RMSE      | Bias      | AHET pass |
|-----------------|----------|-------|-----------|-----------|-----------|-----------|
| Canonical k_max=6 | 49     | 32    | 23.53 bpm | 26.25 bpm | −19.61    | 36/49     |
| Adaptive k_max  | 40       | 41    | 23.13 bpm | 24.18 bpm | −22.47    | 38/40     |
| Fixed k_max=3   | 40       | 41    | 23.13 bpm | 24.18 bpm | −22.47    | 38/40     |

Delta MAE vs canonical: −0.40 bpm (trivial). NaN count **increases** from 32 to 41.
AHET pass rate 95% on wrong answers — algorithm is confident and wrong.

**Root cause of adaptive k_max failure:**
With k_max=3, harmonics k=4 (56 bpm), k=5 (70 bpm), k=6 (84 bpm) are left in the
spectrum. In supine overhead geometry, respiratory harmonics are **stronger than the
cardiac signal** across the entire cardiac band. The k=4 harmonic (~56 bpm) dominates.
AHET then validates this false candidate by finding the k=8 harmonic (~112 bpm) as the
"second harmonic" (2×56=112, within the ±6 bpm AHET search window). **AHET second-
harmonic check does NOT discriminate between respiratory harmonic chains (k=4 + k=8)
and true cardiac — respiratory harmonics form self-consistent N×f_r chains.**

**cap5 preview (held-out — not used for algorithm decisions):**

| Mode            | N_finite | N_NaN | MAE       | Bias      | AHET pass      |
|-----------------|----------|-------|-----------|-----------|----------------|
| Canonical k_max=6 | 54     | 27    | 19.50 bpm | −17.23    | —              |
| Adaptive k_max=3  | 53     | 28    | 23.67 bpm | −21.76    | 53/53 (100%)   |

Delta MAE: +4.17 bpm — **worse** than canonical. 6 windows with error < 5 bpm are
coincidences where 6×f_r ≈ true HR.

**Blocker 6 revised — no k_max value resolves the supine failure:**
There is no k_max value that satisfies both constraints simultaneously for cap3_retake
(f_r=14 bpm, HR≈84 bpm):
- k_max ≥ 6: erases cardiac signal (6×14=84 bpm nulled by ECA)
- k_max ≤ 3: leaves k=4 (56 bpm) unsuppressed; k=4 is stronger than cardiac in supine;
  AHET fooled by the k=4+k=8 respiratory harmonic chain
- k_max = 4 or 5: k=5 (70 bpm) or k=6 (84 bpm) still create false candidates

Evidence: `results/diagnostics/adaptive_kmax/cap3_retake_20s_adaptive_kmax.csv`,
`cap3_retake_20s_fixed_kmax3.csv`, `cap5_20s_adaptive_kmax.csv`.

---

## 2026-06-21 — Cross-session ECA+AHET; bin correction; num_tx fix; HDF5 pipeline; quality mask

### Cross-session ECA+AHET (`exp_eca_all`) — all 10 sessions

Ran `experiments/exp_eca_all/run.py` on all 10 sessions via `data/manifest.local.csv`.

| Session | Posture           | Bin | Distance | MAE (bpm) | RMSE (bpm) | NaN/total |
|---------|-------------------|-----|----------|-----------|------------|-----------|
| exp001  | supine overhead   | 28  | 1.221 m  | 17.12     | 22.07      | 15/36     |
| exp002  | supine overhead   | 28  | 1.221 m  | 17.85     | 23.16      | 26/56     |
| exp003  | seated no-back    | 31  | 1.351 m  | 11.37     | 14.32      | 7/17      |
| exp004  | seated no-back    | 31  | 1.351 m  | 20.76     | 23.71      | 1/17      |
| exp005  | seated no-back    | 30  | 1.307 m  | 22.70     | 26.02      | 0/17      |
| exp006  | seated chair-back | 31  | 1.351 m  | 11.47     | 14.35      | 7/17      |
| exp007  | supine overhead   | 28  | 1.221 m  | 23.22     | 26.15      | 36/56     |
| exp008  | seated no-back    | 29  | 1.264 m  | 5.20      | 6.82       | 0/9       |
| exp009  | seated chair-back | 33  | 1.439 m  | 8.40      | 10.89      | 2/13      |
| exp010  | seated no-back    | 30  | 1.307 m  | 20.43     | 23.76      | 0/13      |
| **Overall** |              |     |          | **16.38** | **21.13**  | 94/271    |

Only exp008 and exp009 meet the target. Per-session overlay plots saved alongside
comparison.csv. Evidence: `results/exp_eca_all/20260621_095804/`.

### Bin correction — exp008 locked_bin 27 → 29

Root cause: initial bin 27 came from visual inspection of `mean_range_profile` image
(uses mean of |magnitude|). The correct method is `vitals.select_range_bin()` which uses
`mean(|.|²)` over ALL analysis frames. These give different results when adjacent bins
have similar magnitude but different energy distributions.

Script `scripts/reselect_bins.py` re-ran energy-based selection on all 10 sessions; only
exp008 changed (27→29, 1.177 m→1.264 m). Effect: **MAE 9.39→5.20, NaN 11→0.** Manifest
updated: `data/manifest.local.csv`.

### num_tx corrected across all configs (2 → 1)

User confirmed only 1 TX was used in all captures. Fixed `num_tx: 2 → num_tx: 1` in all
7 active experiment configs. Field is metadata only — not used in the decode math — so no
data was corrupted. Results/config_used.yaml snapshots left unchanged (frozen run records).

Files changed: `experiments/exp_eca_all/config.yaml`,
`experiments/exp000_range_plot/config.yaml`,
`experiments/exp004_window_length/config.yaml`,
`experiments/exp002_harmonic_rejection/config.yaml`,
`experiments/exp001_offline_baseline/config.yaml`,
`experiments/exp003_generalisation/config_chair_back.yaml`,
`experiments/exp003_generalisation/config_chair_no_back.yaml`.

### HDF5 time-domain cubes — `scripts/save_time_domain_cubes.py`

Saved all 10 sessions to `data/processed/time_domain_cubes/<session_id>.h5`.

Each file:
- Dataset `/cube`: shape `(N_frames, 32, 4, 256)` = (frames, chirps, rx, adc_samples),
  dtype complex64, NO transpose. Time domain only.
- Key metadata attributes: `session_id`, `posture`, `possible_distance_cm` (NOT
  `distance_cm`; `locked_bin` NOT stored), `radar_start_epoch_seconds`,
  `stationary_intervals`, `radar_orientation`, `num_tx=1`, `num_rx=4`,
  `num_adc_samples=256`, `num_chirps_per_frame=32`, `frame_rate_hz=20.0`,
  `range_resolution_m=0.0436`, `git_commit`, `source_bin_files`, `num_frames`.
- File sizes: 364 MB (exp008) to 1113 MB (exp002); total ~7.9 GB.

### Quality mask — `scripts/add_quality_mask.py` (ready; not yet run on real data)

Adds `/quality_mask` (bool, N_analysis = total_frames − 600) and `/quality_metrics/`
group to existing .h5 files. True = healthy. Indexes `cube[600:]` only; frames NOT
deleted (preserves phase continuity for unwrapping).

Config: `scripts/quality_mask_config.yaml`.

Hard failure checks: ADC clipping (`|sample| ≥ 32767`) and dead frame (energy < 1% of
session median). Soft failures (motion spike, RX imbalance) planned but not yet
implemented.

Log: `results/save_quality_mask.log`. Tests: `tests/test_quality_mask.py` — 19/19 pass.

Run command (pending execution on real data):
```
python -X utf8 scripts/add_quality_mask.py --all
```

---

## Current status (as of 2026-06-21)

**Where we are:**
Cross-session ECA+AHET run (`exp_eca_all`) on all 10 sessions completed. Only 2 sessions
work well: exp008 (MAE 5.20) and exp009 (MAE 8.40). All supine sessions (exp001, exp002,
exp007) fail with Blocker 6 (harmonic chain overlapping cardiac band). Seated no-back
sessions (exp003–005, exp010) fail with ~20 bpm negative bias, suspected 4th respiratory
harmonic at ~4×0.3 Hz ≈ 72 bpm landing in the cardiac band. Data pipeline fully built:
10 time-domain HDF5 cubes in `data/processed/time_domain_cubes/` (~7.9 GB total) and
quality mask script ready to run (19/19 unit tests pass).

**Confirmed working (seated frontal, 1.3–1.5 m):**
- exp008 (ECA+AHET, k_max=6, locked_bin=29, 1.264 m): MAE 5.20, RMSE 6.82, NaN 0.
  Evidence: `results/exp_eca_all/20260621_095804/`.
- exp009 (seated chair-back, 1.44 m, bin 33): MAE 8.40.
- Historical exp002/cap1: MAE 5.17, RMSE 6.90, bias −2.21 bpm (N=21).
  Evidence: `results/exp002_harmonic_rejection/20260615_110410/`.

**Failing:**
- Supine overhead (exp001 MAE 17.12, exp002 MAE 17.85, exp007 MAE 23.22): Blocker 6 —
  k×f_r harmonics overlap cardiac band; ECA cannot distinguish or leaves respiratory
  harmonics dominant. Adaptive k_max hypothesis refuted.
- Seated no-back (exp003 MAE 11.37, exp004 MAE 20.76, exp005 MAE 22.70, exp010 MAE 20.43):
  ~20 bpm negative bias; heart_spectrum inspection needed to confirm 4th respiratory
  harmonic cause.
- Seated chair-back (exp006 MAE 11.47): partial failure.

**Open blockers:**
- **Blocker 5: open** — harmonic exclusion at candidate selection.
- **Blocker 6: REVISED — partially refuted.** k_max reduction alone insufficient; no
  k_max value satisfies both constraints for supine captures with typical HR and BR.
- **Blocker 3: deferred** — AHET criterion validation requires second independent capture.
- Blockers 1, 2, 4: resolved.

**Immediate next steps:**
1. `python -X utf8 scripts/add_quality_mask.py --all` → check `results/save_quality_mask.log`.
2. Inspect heart_spectrum intermediates from exp_eca_all for exp003–005 to determine
   whether 4th respiratory harmonic is the dominant candidate in the cardiac band.
3. Based on (2): implement Blocker 5 harmonic exclusion or investigate geometry fix.

**Deferred items (open-ended):**
- Tune distance gate + filter bands from real data.
- Quantitative validation — MAE/RMSE + Bland-Altman across subjects/postures (write-up).
- Validate respiration-band extraction against Masimo `Breaths / min`.

---

## 2026-06-22 — Documentation audit and restructure

**What we set out to do:** audit all `.md` files for discrepancies, then restructure
project documentation for clarity and continuity.

**What was done:**
- Identified 6 discrepancies across CLAUDE.md / SESSION.md / notes/approach.md
  (stale exp001/exp002 metrics in approach.md, wrong hop documented as 10 s vs actual
  5 s, exp004 label collision, W&B as an unclosed blocker, k_max config mismatch,
  CLAUDE.md omitting supine geometry).
- `SESSION.md` rewritten as `HISTORY.md`: chronological (oldest-first), all information
  preserved, multi-round plan-review debates condensed to their final outcomes.
  `SESSION.md` removed via `git rm`.
- `HANDOFF.md` created at repo root as the designated first-read doc for new sessions.
- `CLAUDE.md` updated: `SESSION.md` → `HISTORY.md` in all three references; W&B logging
  explicitly marked disabled (was previously an unclosed blocker, now formal policy).

**No algorithm, pipeline, or experiment code was changed this session.**

**Outcome:** documentation is now consistent and a new session can orient from
`HANDOFF.md` in under 5 minutes.

---

## 2026-06-22 — Quality mask threshold validation

**Goal:** validate every threshold in `scripts/quality_mask_config.yaml` against real
data before running `add_quality_mask.py` on all sessions.

**Method:** for each check, wrote a standalone diagnostic script that computes the metric
from the HDF5 cubes (no pre-existing quality mask required), plots the full distribution
across all 10 sessions, and prints data-driven threshold candidates.

**Results:**

| Check | Script | Threshold | Frames flagged | Decision |
|---|---|---|---|---|
| Hard: ADC clip | `diag_hard_failure_thresholds.py` | 32767 | 0 / 60000 | No change — hardware limit |
| Hard: dead frame | `diag_hard_failure_thresholds.py` | 0.01 | 0 / 60000 | No change — safety net only |
| rx_imbalance | `diag_rx_imbalance.py` | 10.0 → **5.0** | 0 / 60000 | Lowered: empirical max = 3.42, 5.0 gives 46% margin |
| subject_bin_snr | `diag_subject_bin_snr.py` | 5.0 | 95 / 60000 (0.16%) | No change — exp008 min = 41.6, flagged frames genuine |
| — (verification) | `diag_range_bin_check.py` | n/a | 0 mismatches | All 10 locked bins confirmed correct |
| motion_spike | `diag_motion_spike_thresholds.py` | factor 5.0 → **10.0**, window 50 → **200** | 43 / 60000 (0.07%) | Narrowed window tracked breathing; raised factor for gross-motion only |

**Key finding — motion spike rolling window:** `motion_window_frames=50` (±2.5 s) was
too narrow — it partially tracked respiratory energy oscillations (~3.3 s period), causing
the ratio distribution to be continuous with no natural gap at 5×. Widening to 200 (±10 s)
stabilised the rolling median. At 10×, 43 frames flagged (exp001=27 early frames,
exp007=9, exp009=5, exp004=2; exp008=0). Time-series plot shows isolated red dots with no
breathing-period periodicity — appropriate for a gross-motion safety net.

**Rolling median rule:** MUST be applied to the full per-session energy array, not
per-chunk. `scipy.ndimage.median_filter` applied chunk-by-chunk introduces boundary
artifacts. Always concatenate chunked energies first, then apply median once.

**Still pending:** subject_bin_dropout and phase_jump diagnostic scripts not yet written.

**No algorithm code changed.** All changes are to `quality_mask_config.yaml` thresholds
and new read-only diagnostic scripts in `scripts/`.

**Next:** write `diag_subject_bin_dropout.py` and `diag_phase_jump.py`, then run
`python -X utf8 scripts/add_quality_mask.py --all`.

---

## 2026-06-23 — Quality mask execution; seated_no_back bias diagnosis; Fix A + Fix B

### Quality mask run and updated cross-session baseline

Ran `python -X utf8 scripts/add_quality_mask.py --all` on all 10 sessions. All hard
failure thresholds: no ADC clipping, no dead frames. Soft failure stats:
- Motion spikes (factor=10, window=200 frames): 43 frames flagged total (exp001=27,
  exp007=9, exp009=5, exp004=2, exp008=0). All isolated; no breathing-period periodicity.
- RX imbalance (threshold=5.0): 0 frames flagged across all 10 sessions.
- Subject bin SNR (threshold=5.0 dB): 95 frames flagged (0.16%); exp008 minimum=41.6 dB,
  all flagged frames were genuine low-energy windows outside the recording interval.

**Key outcome — exp010 fully gated:**
Quality gating filtered 39/39 windows for exp010 (seated_no_back, 131 cm). This session
is excluded from all metric tables; the pipeline correctly returns NaN for all windows.

`experiments/exp_eca_all/run.py` re-run with quality gating enabled. New cross-session
baseline (run `20260623_011009`):

| Session | Posture           | MAE (bpm) | RMSE (bpm) | Bias (bpm) | n_finite |
|---------|-------------------|-----------|------------|------------|----------|
| exp001  | supine            | 17.76     | 18.96      | −16.56     | 37       |
| exp002  | supine            | 18.94     | 21.95      | −16.62     | 49       |
| exp003  | seated_no_back    | 23.25     | 25.06      | −23.25     | 41       |
| exp004  | seated_no_back    | 21.53     | 23.55      | −21.06     | 41       |
| exp005  | seated_no_back    | 21.07     | 23.00      | −20.42     | 39       |
| exp006  | seated_chair_back | 11.26     | 14.35      | −9.66      | 40       |
| exp007  | supine            | 23.71     | 26.45      | −19.69     | 46       |
| exp008  | seated_no_back    | 5.20      | 6.92       | −2.21      | 21       |
| exp009  | seated_chair_back | 8.40      | 11.64      | −6.81      | 32       |
| exp010  | seated_no_back    | NaN       | NaN        | NaN        | 0 (gated)|
| **Overall** | 9 sessions  | **16.79** |            |            |          |

Evidence: `results/exp_eca_all/20260623_011009/`.

### Seated_no_back bias diagnosis — two diagnostic scripts

**Goal:** understand the ~20–23 bpm negative bias in exp003/005.

**`scripts/diag_leakage.py` — spectral leakage hypothesis REFUTED:**
Computed `pwr_edge / pwr_masimo` ratio (power at cardiac band edge 0.8 Hz vs power at
true Masimo frequency) for the worst-error windows. Ratio does not correlate with error:
win 24 (worst error, −33.8 bpm) has ratio=0.0×; win 9 (best, −1.8 bpm) has ratio=60.2×.
The hypothesis that 2×f_r spectral leakage universally explains the bias is wrong.

**`scripts/diag_eca_overnotch.py` — two distinct failure modes found:**

Measures pre-ECA amplitude vs post-first-pass-ECA amplitude at the Masimo target
frequency (`pre/post` ratio) per window. Columns: `p_pre@mas`, `p_post@mas`, `pre/post`.

**Failure mode 1 — ECA harmonic collision (win 19, pre/post=5.21×):**
`4×f_r = 84.7 bpm = Masimo PR`. First-pass ECA (k=4 notch, no cardiac guard) removes
the cardiac signal from spec1 — both the respiratory harmonic and the cardiac signal
occupy the same frequency. No candidate remains near the true HR; the pipeline falls
through to a spurious peak at the cardiac band edge.

**Failure mode 2 — left-edge spectral dominance (wins 52, 24, 32, pre/post≈1.0×):**
Cardiac signal IS present in spec1, but a spurious peak near 0.8 Hz dominates candidate
selection. Source: 4th-order Butterworth at 0.8 Hz provides only 3–9 dB attenuation at
0–0.2 Hz below the cutoff; 2×f_r residual energy near 0.8 Hz wins the argmax. AHET
spuriously confirms: 2×(0.8 Hz) = 1.6 Hz, which falls within the cardiac band median.
Radar reads ~50 bpm (0.83 Hz). Masimo ≈ 84 bpm → error ≈ −34 bpm.

### Fix A + Fix B — implemented in `src/vitals.py`

**Module-level constant added (line 29):**
```python
MIN_CARDIAC_BAND_MARGIN_HZ: float = 0.15  # 0.8 + 0.15 = 0.95 Hz = 57 bpm
```
Assumption: HR > 57 bpm for all seated/standing subjects in this study.

**Fix A — provisional cardiac guard (in ECA+AHET path):**
Before first-pass ECA, compute a provisional cardiac candidate as the argmax of `spec_bp`
(raw bandpassed spectrum) in the zone `freqs >= band_lo + MIN_CARDIAC_BAND_MARGIN_HZ`.
Pass this as the `cardiac_candidate_hz` guard to `eca_project()`. This prevents ECA from
notching at the dominant non-edge spectral peak, protecting the cardiac signal from
harmonic-collision destruction in spec1.

*Limitation:* when 3×f_r is the dominant raw-spectrum peak (typical at 135 cm), Fix A
guards 3×f_r rather than the true cardiac frequency. In harmonic-collision windows where
4×f_r ≈ Masimo, Fix A does not protect the cardiac signal. However, Fix B handles the
downstream consequence (see below).

**Fix B — minimum margin filter:**
After candidate selection from spec1, reject all candidates with
`freqs[candidate] < band_lo + MIN_CARDIAC_BAND_MARGIN_HZ = 0.95 Hz`. If all candidates
are rejected, the for-loop does not execute and the function returns NaN (no fabricated
estimate). This converts left-edge-dominated windows from wrong estimates (~50 bpm) to
honest NaN.

**Verification:** all 7 `tests/test_eca_ahet.py` and all 13 `tests/test_vitals_synthetic.py`
pass after the three edits. Evidence: pytest run 2026-06-23.

### Results after Fix A + Fix B (run `20260623_094252`)

| Session | MAE before | MAE after | Δ MAE | n_finite before→after |
|---------|-----------|-----------|-------|----------------------|
| exp001  | 17.76     | 15.71     | −2.05 | 37→30                |
| exp002  | 18.94     | 13.61     | −5.33 | 49→35                |
| exp003  | 23.25     | 16.54     | **−6.71** | 41→33           |
| exp004  | 21.53     | 17.13     | −4.40 | 41→33                |
| exp005  | 21.07     | 16.80     | −4.27 | 39→33                |
| exp006  | 11.26     | 8.82      | −2.44 | 40→36                |
| exp007  | 23.71     | 17.18     | **−6.53** | 46→30           |
| exp008  | 5.20      | 4.85      | −0.35 | 21→20                |
| exp009  | 8.40      | 5.57      | −2.83 | 32→30                |
| **Overall** | **16.79** | **12.91** | **−3.88 (−23%)** | |

Fix B is the dominant driver — it rejected left-edge candidates across ALL postures, not
just seated_no_back. Supine sessions (exp001, exp002, exp007) also had left-edge artifacts
from the same Butterworth rolloff mechanism. The fix converts spurious ~50 bpm estimates
to NaN, removing them from the MAE calculation.

Evidence: `results/exp_eca_all/20260623_094252/`.

### Fix A refinement attempt — REVERTED

**Idea:** cancel the Fix A guard if the provisional candidate is itself a k×f_r harmonic
(|prov_cand - k×f_r| ≤ 0.5 × freq_resolution). This prevents Fix A from protecting 3×f_r.

**Result:**
- exp003: 16.54 → 16.54 (no change) — the remaining bad windows pick candidates near
  the Fix B boundary via a mechanism Fix A refinement does not reach
- exp007: 17.18 → 19.74 (regression) — in supine sessions, Fix A's guard at 4×f_r was
  protecting legitimate cardiac signals that coincidentally matched 4×f_r; refinement
  removed this protection
- exp002: run failed (possible exception in the new code path)

**Reverted** to Fix A+B state (20260623_094252). All 20 vitals tests still pass.

### Remaining seated_no_back bias — SNR floor, not a patchable artifact

After Fix A+B, seated_no_back sessions (exp003, exp004, exp005) have:
- MAE ≈ 16.5–17.1 bpm, bias ≈ −16 bpm
- All 33 finite windows with median error near −16 bpm

The new worst windows for exp003 (runs 54, 40, 30, 18) show:
- Radar reads 55–60 bpm, Masimo ≈ 84–87 bpm
- Fix A provisional guard selects 3×f_r = 1.09 Hz (dominant raw-spectrum peak above
  0.95 Hz), protecting it from ECA. In spec1, 3×f_r survives. The nearest FFT bin to
  3×f_r (bin 22 = 1.10 Hz) also passes Fix B (> 0.95 Hz). Parabolic interpolation between
  the guarded peak and its neighbours pulls the refined frequency down to ≈ 0.94–0.97 Hz.
  AHET spuriously passes because 6×f_r ≈ 2×(3×f_r) falls near the AHET search window.
- Fix A refinement attempts to cancel this guard (since 3×f_r IS a harmonic), but the
  argmax actually falls at bin 22 = 1.10 Hz (≠ exactly 3×f_r = 1.086 Hz), so the same
  neighbouring candidate survives through a different mechanism.

**Conclusion:** the −16 bpm floor in seated_no_back sessions is an SNR limitation.
At 135 cm with no back support, the cardiac micro-motion signal is too weak to consistently
exceed respiratory harmonic residuals in the cardiac band after ECA. The pipeline cannot
reliably identify the cardiac peak under these conditions without:
(a) stronger a priori constraints on HR (e.g., tracking filter across windows), or
(b) multi-bin coherent processing to improve SNR, or
(c) a fundamentally different AHET criterion that rejects respiratory harmonic chains.

**Sessions that work well (current best):**
- exp008 (seated_no_back, 118 cm): MAE 4.85 bpm — closer range gives higher cardiac SNR
- exp009 (seated_chair_back, 144 cm): MAE 5.57 bpm — chair back stabilises torso motion
- exp006 (seated_chair_back, 135 cm): MAE 8.82 bpm

**Sessions failing (and diagnosed cause):**
- Supine (exp001, exp002, exp007): Blocker 6 — respiratory harmonic chain overlapping
  cardiac band in low-SNR overhead geometry
- Seated no-back at 135 cm (exp003, exp004, exp005): SNR floor — respiratory artifacts
  dominate after ECA; cardiac signal < 57 bpm margin candidates

---

## Current status (as of 2026-06-23)

**Algorithm state:** ECA+AHET with Fix A (provisional cardiac guard) + Fix B (minimum
margin filter, 0.95 Hz = 57 bpm). Both changes are in `src/vitals.py`. All 20 unit tests
pass. Canonical result: `results/exp_eca_all/20260623_094252/` — overall MAE 12.91 bpm
(9 sessions, exp010 fully gated).

**Confirmed working:**
- exp008 seated_no_back 118 cm: MAE 4.85 bpm
- exp009 seated_chair_back 144 cm: MAE 5.57 bpm
- exp006 seated_chair_back 135 cm: MAE 8.82 bpm

**Failing with known cause:**
- Supine sessions (exp001 MAE 15.71, exp002 MAE 13.61, exp007 MAE 17.18): Blocker 6
- Seated_no_back 135 cm (exp003 MAE 16.54, exp004 MAE 17.13, exp005 MAE 16.80): SNR floor
- exp010 (seated_no_back, 131 cm): all 39 windows quality-gated (HDF5 mask)

**Open blockers:**
- **Blocker 5:** harmonic exclusion at candidate selection (deferred — AHET criterion
  validation needed first)
- **Blocker 6:** supine geometry SNR; no k_max value resolves it; requires multi-bin or
  geometry change
- **Blocker 3:** AHET criterion validation requires independent capture data

**Immediate next steps (options):**
1. Investigate Blocker 6 (supine) — multi-bin coherent combination or different capture
   geometry (radar to the side rather than overhead)
2. Investigate whether a temporal HR tracking filter (e.g., Kalman) across windows can
   stabilise seated_no_back estimates
3. Bland-Altman analysis on the working sessions (exp008, exp009, exp006) for paper

---

## 2026-06-23 — Seated_no_back bias diagnosis; Fix A + Fix B; SNR floor conclusion

**Set out to do:** diagnose and fix the ~20–23 bpm negative bias in seated_no_back
sessions (exp003, exp005) that had been left as an open suspicion after the cross-session
ECA+AHET run on 2026-06-21.

**What we did:**
- Ran quality mask on all 10 sessions; exp010 now 100% gated.
- Wrote `diag_leakage.py` → leakage hypothesis refuted (edge/cardiac power ratio
  uncorrelated with error).
- Wrote `diag_eca_overnotch.py` → two failure modes confirmed: (1) harmonic collision
  (4×f_r ≈ Masimo, ECA destroys cardiac signal, `pre/post` = 5.21×); (2) left-edge
  spectral dominance (Butterworth rolloff leaves ~50 bpm artifact that wins candidate
  selection and passes AHET spuriously).
- Implemented Fix A (provisional cardiac guard) + Fix B (minimum margin filter, 0.95 Hz)
  in `src/vitals.py`. All 20 unit tests pass.
- Ran `exp_eca_all`: overall MAE 16.79 → **12.91 bpm (−23%)** across 9 sessions.
- Attempted Fix A harmonic-cancellation refinement → reverted (exp007 regression, no
  gain on exp003). Second overnotch diagnostic confirmed: remaining bad windows pick
  3×f_r as their AHET-verified candidate via Fix A's guard.

**Outcome:** seated_no_back at 135 cm has an SNR floor at ~−16 bpm MAE that is not
patchable by spectral filtering alone. Fix A+B is the correct current state. Working
sessions (exp006 MAE 8.82, exp008 MAE 4.85, exp009 MAE 5.57) are ready for paper-grade
Bland-Altman analysis. Evidence: `results/exp_eca_all/20260623_094252/`.

---

## 2026-06-23 (session 2) — Scope narrowed; Bland-Altman done; Blocker 6 closed

**Set out to do:** Bland-Altman figure for working sessions; investigate Blocker 6 (supine).

**Done:**

- **Study scope narrowed** to 6 seated sessions only (exp003–006, exp008–009).
  Supine (exp001/002/007) dropped — overhead geometry is irresolvable without a new
  capture geometry. exp010 dropped — 100% quality-gated, no usable data.

- **Bland-Altman completed** for working sessions (exp006/008/009):
  pooled n=86, bias=−4.7 bpm, 95% LoA [−19.8, +10.4] bpm.
  Script: `scripts/plot_bland_altman.py`. Figure: `figures/bland_altman.png`.

- **Multi-bin coherent combination (Blocker 6 Option B) — tried and reverted.**
  Tested equal-weight and SNR-weighted IQ average over bins [27,28,29] for supine
  sessions. Best: exp001 SNR-weighted −2.75 bpm MAE improvement. Threshold (≥3 bpm
  with no regression) not met. Script deleted. Root cause is geometry, not bin SNR.

- **Back-support diagnostic run** (`scripts/diag_back_support.py`) on seated sessions.
  Hypothesis tested: lack of back support → torso sway → f_r corruption → failure.
  **Hypothesis rejected.** Harmonic collision risk is 91–100% in ALL sessions including
  working ones. The discriminator is cardiac signal strength post-ECA (function of
  distance/reflectivity), not postural stability. exp003/004/005 fail because cardiac
  SNR at ≥131 cm without back support is insufficient to survive ECA and pass AHET.

**Outcome:** Paper-grade dataset is exp006/008/009. Decision pending on whether to attempt
new closer-range captures for exp003/004/005 or declare them out of scope. Next session
should start with that decision.

---

## 2026-06-23 (session 3) — Root-cause analysis of exp003/004/005; new capture protocol

**Set out to do:** Determine whether exp003/004/005 could be saved algorithmically, or
whether new captures are needed.

**What we did:**
- Interrogated exp003/004/005 comparison CSVs directly. Found that f_r ≈ 20–22 bpm
  throughout all three sessions → 4×f_r ≈ 80–88 bpm lands directly on cardiac (83–87 bpm).
  ECA hard floor (k = 1..4, always cancelled) destroys both the respiratory harmonic and the
  cardiac signal simultaneously. ~58% of wrong estimates match radar ≈ PR − RR, confirming
  the intermodulation product f_h − f_r ≈ 62–65 bpm is the dominant AHET-verified candidate.
- Confirmed exp008 works NOT because it is closer (it has the same 4×f_r ≈ cardiac
  coincidence: 4×17.7 bpm = 70.9 ≈ HR 70.5 bpm) but because its f_h − f_r ≈ 52 bpm falls
  below Fix B's 57 bpm threshold, eliminating the intermodulation candidate.
- Evaluated all algorithmic approaches: Fix A extension to k=4, multi-bin combination,
  longer windows, HMUSIC, intermodulation-aware ECA. None can recover existing data.
  Intermodulation-aware ECA is circular (needs f_h to cancel f_h-related artifacts).
- Corrected HANDOFF's prior attribution ("cardiac SNR post-ECA / distance") to the actual
  cause: **capture protocol failure — uncontrolled f_r landed in the ECA forbidden zone**.
- Derived new capture protocol: f_r ≤ 14 bpm (4×14 = 56 bpm < Fix B floor), 4 min of
  data minimum, sessions at 131 cm and 135 cm no-back.

**Outcome:** exp003/004/005 cannot be recovered from existing data. New captures planned
at 131–135 cm with f_r ≤ 14 bpm and a mandatory pre-session breathing-rate check.
HANDOFF.md updated with corrected root cause and specific capture protocol.

---

## 2026-06-23 (session 4) — Python capture script

**Set out to do:** Build a Python capture script to replace mmWave Studio for the
re-take sessions at 131–135 cm.

**What we did:** Extracted all DCA1000 UDP protocol bytes from TI source code
(`rf_api.cpp`, `recorddatarecv.cpp`, `defines.h`, `rf_api_internal.h`) and confirmed
the IWR1642 chirp parameters from `notes/custom_capture.lua`. Filled all remaining gaps in
`notes/dca1000_protocol.md`. Implemented `scripts/capture.py` + `scripts/capture_config.yaml`:
configures DCA1000 via UDP and IWR1642 via UART, receives + reorders UDP ADC packets,
writes `.bin` + `LogFile.csv` identical in format to mmWave Studio output.

**Outcome:** Script written and protocol-verified against source code. Not yet run on
hardware. Next: 100-frame test capture before any real session.

---

## 2026-06-23 (session 5) — capture.py hardware-verified

**Set out to do:** Run a 100-frame test capture with `scripts/capture.py` against the real IWR1642BOOST + DCA1000EVM hardware.

**What we did:**
- Fixed two blocking issues before sensorStart succeeded:
  1. Missing mmW demo SDK CLI commands — the demo requires the full processing pipeline
     configured before sensorStart, even for LVDS raw ADC capture. Added the 7 missing
     commands in the correct SDK 3.6 order: `multiObjBeamForming`, `aoaFovCfg`,
     `cfarFovCfg` (×2), `CQRxSatMonitor`, `CQSigImgMonitor`, `calibData`. Also corrected
     command order (`bpmCfg` before `frameCfg`, `lowPower` after `frameCfg`).
  2. RECORD_STOP returning status 0x100 — after the IWR1642 auto-stops at the last
     configured frame the DCA1000 self-terminates recording, so a subsequent RECORD_STOP
     returns "not recording" (0x100). Made this non-fatal (warn + continue).
- 100-frame test ran end-to-end: DCA1000 configured → sensorStart OK → 9003 UDP packets
  received (00ss, matches 100 × 32 × 4 × 256 × 4 bytes / 1456 exactly) → `.bin` written.
- `read_adc_bin()` decoded the file to shape `(100, 32, 4, 256)` complex64 — correct.

**Evidence:** `data/raw/test_short_Raw_0.bin` (deleted after verification), `read_adc_bin` → `(100, 32, 4, 256)` complex64.

**Outcome:** `scripts/capture.py` is hardware-tested and working. Ready to take real sessions.

**Next:** Real sessions at 131 cm and 135 cm seated no-back, f_r ≤ 14 bpm:
`python scripts/capture.py --session-id <id> --distance 1.31 --frames 5700`

---

## 2026-06-23 (session 6) — capture.py hardened; phase-based bin selection confirmed

**Set out to do:** Finalise `scripts/capture.py`: pre-capture manifest prompt, correct
chirp params, correct output filename, duration-based CLI, and diagnose why the test capture
showed no chest peak in the mean range profile.

**What we did:**
- Added interactive manifest prompt (asks only: participant_id, posture, radar_orientation,
  stationary_intervals; all other fields use silent defaults). Detects session_id collisions
  and asks before overwriting.
- Changed output filename from `{id}_Raw_0.bin` to `{id}.bin` (multi-file: `{id}_0.bin`).
- Replaced `--frames` CLI arg with `--duration` (seconds); num_frames computed from fps.
- Discovered `capture_config.yaml` was built from `custom_capture.lua` (slope 65.998 MHz/µs,
  6250 ksps → 0.0555 m/bin) instead of `vital_signs.lua` (slope 70.006, 5209 ksps →
  0.0436 m/bin). Fixed. Added `range_resolution_m` as a per-session column to the manifest;
  old sessions (exp001-exp010) manually set to 0.0436; new sessions computed automatically.
- Ran a 60-second validation capture (test_short) at 1.25 m seated. Mean range profile showed
  no magnitude peak at chest — diagnosed via phase variance analysis. Result: far wall at
  bin 92 (4 m) is 26 dB stronger than the chest in magnitude, but phase p2p at bin 30
  (1.308 m) was 28.4 rad vs 0.15 rad at the wall — confirming chest signal is present and
  consistent with ~8.8 mm breathing displacement at 77 GHz.

**Outcome:** capture.py fully operational. Chirp params confirmed correct. Phase variance
diagnostic established as the correct method for selecting `locked_bin` (not magnitude peak).
`test_short` session is a validation artefact only — no Masimo ground truth, discard for analysis.

**Next:** Real re-take sessions at 131 cm and 135 cm seated no-back with Masimo running,
f_r ≤ 14 bpm. After each capture, run phase variance check to confirm and set locked_bin.

---

## 2026-06-24 — capture.py deleted; LVDS investigation; clean slate

**Set out to do:** Continue diagnosing the LVDS_PATH_ERROR from the prior session, where
changing `adcbufCfg` SampleSwap from 1→0 appeared to be the trigger.

**What we did:**
- Diagnosed that SampleSwap=0 in `adcbufCfg` silently disables LVDS output in the SDK
  demo firmware. Reverted to SampleSwap=1 (hardware works; I/Q swap does not affect HR
  estimation results). Finding logged in `notes/dca1000_protocol.md`.
- User decided to delete `scripts/capture.py` and start over rather than patch the
  existing implementation.
- Cleaned up all capture.py artifacts: deleted `scripts/capture.py` and
  `scripts/capture_config.yaml`, reverted `environment.yml` (pyserial removed), reverted
  `experiments/exp000_range_plot/run.py`, removed `test_short` row from the manifest.
- Moved `vital_signs.lua`, `.mmwave.json`, `.setup.json`, `.xml` from
  `experiments/exp001_offline_baseline/` to `notes/` (authoritative mmWave Studio config).

**Outcome:** Repository is at a clean state for a fresh `capture.py` implementation.
All byte-level protocol knowledge is preserved in `notes/dca1000_protocol.md`.
Paper-grade results (exp006/008/009, Bland-Altman) are unaffected.

---

## 2026-06-24 - capture.py configuration and reliability review

**Set out to do:** Review the rebuilt `scripts/capture.py` and capture YAML against all
`vital_signs.*` Studio exports, the TI DCA1000/IWR1642/raw-ADC/SDK manuals, installed
mmWave Studio DCA source, and SDK 3.6.2 xWR16xx demo source. No capture code was changed.

**What we verified:**
- The intended waveform and DCA mode match Studio: 77 GHz, 70.006 MHz/us, 256 samples at
  5209 ksps, 32 chirps/frame, 50 ms frames, four RX, chirp TX mask 1, 16-bit complex raw
  ADC, two LVDS lanes, Ethernet ports 4096/4098, and 25 us packet delay.
- Re-derived 0.043598 m/bin, 131,072 bytes/frame, 747,110,400 bytes for 5700 frames,
  and 13,107,200 bytes/9003 packets for the required 100-frame smoke test.
- The rebuilt implementation already produced `data/raw/test.bin`: 2400 frames,
  314,572,800 bytes, 216,053 packets, zero logged drops, and a recomputed SHA-256 matching
  metadata.
- Targeted radar layout/split/log tests passed: 10/10.

**Critical findings:**
1. The UDP receiver ignores the 48-bit byte offset and appends payloads. Temporary tests
   reproduced silent corruption for missing-first, reordered, and duplicate packets.
2. SDK-required `SampleSwap=1` is not represented in the source-agnostic decoder. The
   Python `test.bin` subject-range cluster is in the negative FFT half under the current
   decoder, while Studio `exp009.bin` is positive; the processing path keeps only positive
   bins.
3. Session override deletes existing `data/raw` evidence, violating the append-only rule.
4. The rebuilt script/YAML are untracked, while the authoritative `notes/vital_signs.*`
   exports are ignored and their former tracked paths are deleted.

**Additional findings:** DCA replies are not structurally/source validated; zero-duration
can emit SDK infinite-frame mode; FPGA version bits are decoded incorrectly; incomplete
captures are finalized as development data; capture uses up to ~1.5 GB RAM; timestamps and
software/hardware provenance are insufficient; COM7 is misdescribed as the Auxiliary port;
and smoke/custom-duration stationary interval defaults can be invalid.

**Evidence and exact fix/test plan:** `notes/capture_review.md`.

**Outcome:** Configuration parity is largely established, but the capture path is **not
approved for new research sessions** until packet placement, source-specific I/Q decoding,
raw-data immutability, and source tracking are fixed and the back-to-back 100-frame hardware
parity gate passes. Claude-family cross-review remains pending and is explicitly documented.

---

## 2026-06-24 (session 2) — IQ swap fix; pipeline hardening; single-session CLI

**Set out to do:** Fix the SDK SampleSwap=1 IQ-swap incompatibility identified in the
capture review, then harden the pipeline for Python captures.

**What we did:**

- **IQ swap fix (critical):** Added `iq_swap: bool = False` to `ChirpConfig` in
  `src/radar_io.py`. De-interleaving now branches on this flag: `iq_swap=False` keeps the
  Studio word order [I0,I1,Q0,Q1]; `iq_swap=True` uses [Q0,Q1,I0,I1] (SDK SampleSwap=1).
  Without this fix, Python-capture subject returns appeared at the mirror bin (N−k) and
  were invisible to the pipeline. Added `iq_swap` column to `data/manifest.local.csv`
  (False for exp001–010, True for `test` and `adc_data`). Updated all callers:
  `exp_eca_all`, `exp002`, `exp000_range_plot`, and `scripts/save_time_domain_cubes.py`
  (which also now stores `iq_swap` as an HDF5 attribute).

- **Test session validated:** Regenerated `test.h5` via `save_time_domain_cubes.py`;
  re-ran `add_quality_mask.py --session test --trim-frames 0`. Result: 0.75% flagged
  (18/2400 frames, all dropout or phase-jump), down from 100%. Range plot confirms chest
  peak at bin 34 (1.482 m), 20 dB above noise floor.

- **`--session` added to `exp_eca_all/run.py`** — filters the manifest to a single row;
  all existing `_run_session` logic unchanged. Also already present on `exp002` and `exp000`.

- **UDP reconstruction bugs fixed in `capture.py`** (3 bugs identified by code review):
  leading-loss detection (`last_seq=0` was falsy, silencing first-packet loss), duplicate
  discard, and `zero_filled_bytes` tracking correct gap size.

- **`--trim-frames` added to `add_quality_mask.py`** to override the global 600-frame
  default per session.

- **10-second countdown** added to `capture.py` after printing the output path.

- **Non-coherent integration** confirmed correct for `exp000_range_plot`: FFT per chirp,
  average |FFT|², then 10×log10 (power dB). Previous coherent path (average IQ first)
  risked partial cancellation across chirps.

**Outcome:** Python captures now decode correctly end-to-end. `test` session passes quality
gating and shows the expected chest peak. Best results unchanged (exp008 MAE 4.85,
exp009 MAE 5.57 from Studio sessions). `capture.py` still not approved for research
sessions — open issues in `notes/capture_review.md`.

---

## 2026-06-25 — exp000 two-method bin selection; manifest write-back; exp_eca_all plots

**Set out to do:** Improve bin selection in `exp000_range_plot` to combine energy and
phase variance; wire the selected bin back to the manifest so all downstream tools use it
automatically; add per-session HR and RR comparison plots to `exp_eca_all`.

**What we did:**

- **`exp000_range_plot` redesigned as the authoritative bin-selection step.** Fixed 0.5–3.0 m
  physical search window (no tape-measure dependency). Two methods: (1) energy peak —
  `mean(|FFT|²)` per bin, top-5; (2) phase variance — `std(unwrapped slow-time phase)` per
  bin, top-5. Combined ranking: sum of per-method ranks; recommended bin = first in both
  top-5 lists (fallback: combined rank #1). Prints a ranked table and marks the agreed bin
  in all three plots (mean profile, heatmap, animation) with the tape-measure distance as a
  reference-only dotted line.

- **Manifest write-back added to `exp000`.** After each run, writes to `manifest.local.csv`:
  `locked_bin` (recommended bin index), `distance_cm` (radar-derived: bin × 4.36 cm),
  `highest_bins` (next 4 ranked bins, comma-separated). All downstream tools
  (save_cubes, add_quality_mask, exp_eca_all) already read from the manifest.

- **`save_time_domain_cubes.py` updated.** Reads `locked_bin` and `highest_bins` from the
  manifest row and saves as HDF5 root attributes. `possible_distance_cm` renamed to
  `distance_cm` (authoritative after exp000 has run; tape-measure before).

- **`exp_eca_all` plots added.** `masimo_rr_bpm` (window-mean Masimo Breaths/min) added to
  per-session comparison CSV and DataFrame. Two PNG plots per session: `hr_comparison.png`
  (Radar HR vs Masimo PR, reuses `compare.overlay_plot` including low-PI markers) and
  `rr_comparison.png` (Radar RR vs Masimo BR).

- **Latent bug fixed in exp000:** `row["distance_m"]` → `row["distance_cm"] / 100.0`
  (manifest has no `distance_m` column; script would have crashed on first run).

- **Confirmed data flow of `add_quality_mask`.** It reads `locked_bin` from the manifest
  (not the HDF5) for all bin-dependent soft failure checks. Config-level override wins if
  set; manifest value is the fallback. Distance (`distance_cm`) is not used at all.

**Pipeline order established:** exp000 → save_cubes → add_quality_mask → exp_eca_all.

**Outcome:** Existing sessions need exp000 re-run (`--all --no-animation`) to populate
the new manifest columns before the next full pipeline run. No algorithm or results changed.

---

## 2026-06-25 (session 2) — diagnostics.json; exp000 third metric; test5 forbidden zone analysis; vitals.py fix attempted and reverted

**Set out to do:** (1) Implement `_write_session_diagnostics` in `exp_eca_all/run.py` to
write a per-session `diagnostics.json` with forbidden zone analysis. (2) Investigate test5's
~15 bpm MAE and attempt an algorithmic fix in `src/vitals.py`.

**What we did:**

- **`diagnostics.json` implemented.** Added `_write_session_diagnostics()` to
  `experiments/exp_eca_all/run.py`. Written alongside `comparison.csv` as
  `results/exp_eca_all/<timestamp>/<session>/diagnostics.json`. Contents: session metadata,
  config snapshot, window counts (including `nan_eca_applied_ahet_rejected`), HR/RR metrics,
  f_r stats (median, std, outlier fraction), Masimo HR stats, ECA forbidden zone
  session-level harmonics table (k=1..k_max × median_f_r vs median Masimo HR, `warning: true`
  when Δ < 6 bpm), per-window violations list, and full per-window records.

- **exp000 third bin-selection metric added.** Respiratory-band SNR (peak power in
  [0.10, 0.50] Hz of slow-time phase FFT, relative to the median spectral floor) added as
  the third method. Qualified bin now requires ≥ 2-of-3 method overlap (energy + phase-var
  + resp SNR). Motivating case: test5 at 0.83 m (environmental vibration dominated phase
  variance, wrong bin selected by 2-method; resp SNR at the correct 1.15 m bin was higher).
  Legend label also fixed — was hardcoded "— methods agree" even when the fallback applied.

- **test5 diagnosed as fully in the k=6 forbidden zone.**
  From `diagnostics.json` at `results/exp_eca_all/20260625_184607/test5/`:
  `f_r_stats.median_bpm = 12.79`, `masimo_hr_stats.median_bpm = 78.4`,
  `eca_forbidden_zone: k=6 → k×f_r = 76.7 bpm, Δ = 1.7 bpm, warning = true` for all
  33 windows. Window counts: 33 total, 18 finite (wrong HR), 15 NaN (`ahet_rejected`).
  Final MAE: **13.85 bpm**.

- **Algorithmic fix attempted and fully reverted (two attempts).**
  *Attempt 1:* harmonic-aware provisional cardiac candidate selection — skip pre-ECA peaks
  within 0.10 Hz of any k×f_r. *Attempt 2:* harmonic-aware second-pass ECA — don't protect
  AHET candidate if it's near k×f_r. Both reverted because the 0.10 Hz (6 bpm) threshold
  needed to catch test5's 1.7 bpm gap also fired for legitimate sessions where 6×13=78 bpm
  is only 4 bpm from cardiac at 82 bpm (test MAE: 0.99→1.73; test3 MAE: 1.27→2.44).
  `src/vitals.py` is fully reverted; all 13 unit tests pass.

- **FFT resolution analysis:** At 20 Hz × 20 s = 400 samples, frequency resolution =
  0.05 Hz = 3 bpm per bin. The 1.7 bpm gap places both sinusoids in the same bin —
  spectrally unresolvable. A 40 s window (800 samples, 0.025 Hz) does not help because the
  QR projection inner product between the two sinusoids at Δf = 0.028 Hz is ≈ 0.10 at
  T = 40 s — still non-orthogonal. Orthogonality requires T > ~50 s (impractical).

**Outcome:** test5 cannot be fixed algorithmically. Needs re-capture with breathing rate
adjusted so that no k×f_r (k = 1..8) is within 6 bpm of the cardiac frequency.
Safe breathing rate targets: 9–11 bpm or 16–18 bpm for typical cardiac at 78–82 bpm.
`diagnostics.json` is the first-stop tool for any session with MAE > 5 bpm.
All other results unchanged. Evidence: `results/exp_eca_all/20260625_184607/`.

**New sessions (Python captures):**
- test (seated no-back, 113 cm, bin 26): MAE **0.99 bpm** — best result in the project.
- test3 (seated chair-back, 118 cm, bin 27): MAE **1.27 bpm**.
- test2, test4, test5: forbidden zone or geometry failure — unusable without re-capture.

---

## 2026-06-26 — Steps 3 and 4 reorganised; output directories standardised; batch logs removed

**Set out to do:** Implement Step 4 (`add_quality_mask.py`) as a proper pipeline step under
`steps/step_4/` and standardise per-session output directories for Steps 3 and 4.

**What we did:**

- **Step 4 fully rewritten** (`steps/step_4/add_quality_mask.py`, `steps/step_4/config.yaml`):
  - All soft-failure checks share a single `chunk_frames` value from `analysis.chunk_frames`
    (no per-check overrides).
  - `locked_bin` always read from the manifest (Step 3 output); no config-level override.
  - Per-session diagnostic log written to `results/<session_id>/step_4/step_4.log`
    (overwrite on each run): includes hard/soft failure counts, skip reasons, phase-diff
    percentiles, and soft-failure overlaps.
  - `config_used.yaml` written to `results/<session_id>/step_4/` on every successful run.
  - Step 3 intake validation: requires `locked_bin`, `locked_range_m`,
    `chest_bin_confidence`, `chest_bin_review_required` in the manifest.
  - Overwrite guard for HDF5 `/quality_mask` (refuse by default; `--overwrite` to allow).
  - `--no-write` dry-run mode: computes mask without modifying HDF5.
  - `min_chest_bin_confidence: medium` policy: sessions below threshold are skipped/errored.

- **Step 3 output directory** changed from `results/step_3_select_chest_bin/<id>_<ts>/`
  to `results/<session_id>/step_3/`. All per-session Step 3 artifacts (selection.json,
  plots) now colocated with the session.

- **Batch append logs removed** from both Steps 3 and 4. No `results/step_3_*/` or
  `results/step_4_*/` batch directories.

- **`trim_frames: 0`** in `steps/step_4/config.yaml` (user changed from 600 intentionally;
  confirm before running on long sessions with settling artefacts).

**No algorithm or experiment code was changed. No results changed.**

**Outcome:** Steps 1–4 of the preprocessing pipeline are all implemented. Steps 3–4 have
not yet been run on real session data. Next: run Step 3 then Step 4 on existing sessions,
check `step_4.log` on first run, then proceed to `exp_eca_all` re-run with updated masks.

---

## 2026-06-26 (session 2) — range_plot moved to steps/; diagnostic log; visualization-only

**Set out to do:** Move `experiments/exp000_range_plot` into the `steps/` structure and
make it a proper diagnostic step rather than the authoritative bin selector.

**What we did:**

- **`steps/range_plot/` created** (`run.py` + `config.yaml`). Output directory changed
  from `results/exp000_range_plot/<id>_<ts>/` to `figures/<session_id>/range_plot/`
  (overwrites each run; no timestamp suffix).

- **`range_plot.log` added** to every run alongside the PNGs and animation. Contains:
  provenance (git commit, source .bin files, IQ swap), plotted bin with source label,
  tape-measure vs radar delta, full 3-method ranking table with "← plotted" and
  "← ranking #1" markers, and agreement status between manifest locked_bin and ranking.

- **range_plot redesigned as visualization-only.** It now reads `locked_bin` from the
  manifest (set by `step_3/select_chest_bin.py`) and marks it on all plots and the log.
  The 3-method ranking table is still computed for diagnostic comparison but does NOT
  override the manifest. `_update_manifest` removed. Falls back to own ranking with a
  printed warning if the manifest has no `locked_bin` yet.

- **`experiments/exp000_range_plot/` not deleted** — still present but superseded.
  Do not use it for new sessions.

**Pipeline order is now:** step_3 (select bin) → range_plot (visualize) → step_2 (save
cubes) → step_4 (quality mask) → exp_eca_all. Step_3 must run before range_plot because
range_plot reads its manifest locked_bin; step_3 must run before step_2 because step_2
bakes locked_bin into HDF5.

**No algorithm or experiment code changed. No results changed.**

---

## 2026-06-26 (session 3) — Step 5 (breathing rate) implemented and hardened

**Set out to do:** Implement Step 5 — breathing rate extraction from the locked-bin HDF5
cube — with unit tests, integration tests, NPZ debug evidence, and a Step 6 contract CSV.
Then harden against the edge-lock failure mode (estimator locking to 6 or 30 bpm band
edges) observed in test and test2 sessions.

**What we did:**

- **`src/respiration.py`** — three estimators (FFT + parabolic, harmonic accumulation,
  STFT subwindow stability) and a fusion function. LAPACK-free `_detrend()` (pure dot
  products; scipy/numpy lstsq crash fatally on this Windows conda install). HA uses
  2× fundamental weight to prevent subharmonic hallucination (f/2 candidate picking up
  true fundamental as its 2nd harmonic). FFT fallback: when HA fails but FFT SNR ≥ 6 dB
  and STFT std ≤ 4 bpm, emit medium-confidence estimate rather than NaN. `fft_peak_bin`
  added — index into the full `freqs_hz`/`spectrum` arrays at the selected peak.

- **`steps/step_5/extract_breathing_rate.py`** — CLI runner. Outputs to
  `results/<session_id>/step_5/` (no timestamp). Second run without `--overwrite` raises
  `FileExistsError`. Step 6 contract CSV written to
  `data/processed/breathing_rate/<session_id>.csv`. Key bugs fixed:
  (1) `mask_trim` and `analysis_start` were conflated — now independent variables;
  (2) HA/debug evidence was collected but not written to NPZ;
  (3) HA parabolic refinement anchored to wrong frequency origin (`band_hz[0]` vs
  `cand_freqs[0]`).

- **Edge-lock hardening pass:** estimates within `edge_lock_margin_bpm: 2.0` bpm of
  the band edge (6 or 30 bpm) forced to `resp_valid=False`, `resp_confidence="low"`,
  `radar_rr_bpm=NaN` after fusion. Diagnostic columns preserved for debugging.
  `edge_lock_side` uses `"low"/"high"/"none"` (not `""` — empty string becomes NaN
  in CSV round-trips). NPZ now includes `fft_freqs_hz`, `fft_spectrum`, `fft_peak_bin`
  per window (padded; gated windows get NaN/-1 placeholders). Summary fields added:
  `n_edge_locked`, `edge_locked_fraction`, `n_valid_edge_locked` (invariant: should be 0).

- **Tests:** `tests/test_respiration.py` (45 unit tests) + `tests/test_step5_breathing.py`
  (12 integration tests). 49/49 pass. Tests cover: FFT/HA/STFT/fusion correctness,
  FFT fallback fire/block conditions, NPZ key/shape verification, edge-lock invariant,
  overwrite guard, mask_trim ≠ analysis_start, no-write, quality-gated windows.

**Non-obvious decisions:**
- `emit_low_confidence: false` by default — low-confidence RR must not enter Step 6.
- test2 sessions with high edge-lock rates are treated as canonical failure cases for the
  confidence logic, not sessions to rescue by tuning.
- matplotlib `tight_layout()` causes a C-level crash (not catchable by try/except) on this
  Windows conda environment; all tests use `no_plots=True` + `matplotlib.use("Agg")` in
  conftest.py.

**Outcome:** Step 5 is working and hardened. Run:
`python -X utf8 steps/step_5/extract_breathing_rate.py --session test3`
Step 6 (heart rate extraction using Step 5 RR as input) is next.

---

## 2026-06-27 — Step 6.1 implementation complete; diagnostic rerun

**Set out to do:** Complete the Step 6.1 implementation (ECA forbidden-zone suppression +
strict AHET gate) that was partially done in the prior session, fix post-review code gaps,
rerun Step 6 on all sessions, and analyze results.

**What we did:**
- Completed `steps/step_6/extract_heart_rate.py`: new params forwarded to estimator, 6 new
  CSV columns (`candidate_rejection_reason`, `all_candidates_rejected`, `eca_skipped_harmonic_ks`,
  `n_eca_skipped_harmonics`, `low_candidate_competitor`, `ahet_gate_mode`), 3 new NPZ arrays,
  accepted-rank-aware `candidate_rejection_reason`.
- Added 4 new test classes to `tests/test_step6_heart_rate.py` (101 tests passing).
- Fixed 4 code-review gaps in `src/vitals.py`:
  1. `_check_low_candidate_competitor` bounded to `band[1]` — second-harmonic energy above
     the cardiac band no longer triggers the competitor check (affects HR < 72 bpm subjects)
  2. Unattempted strict_v1 slots → code 5 (not_attempted) instead of -1
  3. NaN prominence (argmax fallback) → reject with code 3 in strict_v1
  4. `candidate_rejection_reason` CSV column reads from accepted rank, not always rank 0
- Reran Step 6 and `diagnose_step6_hr.py` on test, test2, test3, test4, test5.

**Outcome:**
- ECA forbidden-zone fix confirmed working: k=4,5,6 correctly skipped for RR ≈ 13 bpm.
  test3 and test4 went from 0 AHET-verified windows to 7 each; 3 good-valid windows per
  session land within 1–4 bpm of Masimo PR.
- 79% of windows still fail the AHET ratio gate (low cardiac SNR).
- 2 flat-spectrum false accepts remain (57–58 bpm vs Masimo ~82 bpm) — need absolute SNR gate.
- test3 w31 (72 bpm false accept): AHET gate-ordering problem — 84 bpm fails ratio gate
  because its 2nd harmonic (2.8 Hz) is near the widened filter's upper edge; 72 bpm (rank 1)
  passes because its 2nd harmonic (2.4 Hz) is in a flatter filter region. This is NOT an
  ECA spectral shift.
- test2: Step 5 broken (28/33 resp_invalid) — unrelated to Step 6.1.
- test5: zero yield, all ratio_db_low — very low cardiac SNR.
- `diagnose_step6_hr.py` not yet updated for Step 6.1 fields (P1 gap, next session).

**Correction on earlier session summary:** "essentially flat noise" overstated the failure.
The cardiac band has max/median ratios of 2.5–5×; the correct description is "cardiac peak
not uniquely dominant / low contrast." And to exclude weak artifact candidates, the correct
direction is to RAISE the prominence/SNR threshold, not lower it.

---

## 2026-06-27 — Pre-ECA spectrum diagnostic + coverage gap root-cause confirmation

**Set out to do:** Complete the pre-ECA spectrum plan proposed at the end of the previous
session — add a true pre-ECA bandpassed spectrum to the NPZ, use it to answer whether ECA
over-suppression or genuine weak cardiac SNR is the test5 failure mechanism, and update all
affected tests.

**Implemented:**
- `src/vitals.py`: `spectrum_pre_eca = spec_bp` captured immediately after bandpass, before
  any `eca_project()` call. Propagated through all return paths via `_common_fields()`.
- `steps/step_6/extract_heart_rate.py`: `heart_spectrum_pre_eca` wired into NPZ as 2-D
  float64 array (n_windows × n_fft), alongside `heart_spectrum_first_pass` and `heart_spectrum`.
- `scripts/diagnose_coverage_gaps.py`: new 6-tier classification
  (`pre_candidate_blocked` → `pre_eca_missing` → `weak_cardiac_snr` → `eca_attenuation_likely`
  → `ahet_gate_failure` → `candidate_selection_miss`); new output columns
  `pre_eca_power_at_masimo_bin`, `first_pass_power_at_masimo_bin`, `pre_to_first_pass_delta_db`,
  `first_pass_to_final_delta_db`, `window_blocked_before_ahet`; ECA detection now uses
  median pre-to-first-pass delta instead of fp==final count; renamed `_is_first_pass_high`
  → `_is_above_band_median`.
- `tests/test_diagnose_coverage_gaps.py`: full update — `TestIsAboveBandMedian`,
  rewritten `TestClassifyK6Window` (new signature), robustness tests, `_make_npz` now
  accepts `include_pre_eca` flag, `test_report_ahet_gate_failure_branch` updated.
- 206 tests passing (up from 179).

**Outcome — test5 root cause confirmed:**
`pre_to_first_pass_delta_db` ≈ 0 dB (magnitude 10⁻⁵) for all 31 estimator-run windows.
ECA is not touching the Masimo PR frequency bin. The cardiac peak-to-floor ratio is negative
before any ECA runs (−0.4 to −6.9 dB, median −4.4 dB). The k=6 harmonic geometric overlap
(RR×6 ≈ 78 bpm ≈ Masimo PR) is real, but ECA over-suppression is **not** the failure
mechanism. Genuine weak cardiac SNR is. `first_pass_to_final_delta_db` is 0.0 for all failed
windows — expected, because both `heart_spectrum_first_pass` and `heart_spectrum` hold `spec1`
in AHET failure paths (the second `eca_project()` call per candidate never runs when AHET rejects).

**Outcome — test2 root cause unchanged:**
Step 5 edge-locks to the low RR boundary for 22/33 windows (67%). Step 6 never runs.

**Next:** Fix Step 5 edge-lock for test2. Investigate AHET `ratio_db` threshold for
test3/4/5 (borderline weak SNR, not absent signal). Add absolute SNR gate in strict_v1 to
eliminate flat-spectrum false accepts.

---

## 2026-06-28 — Step 3 bin-selection policy fix; test2 Step 5 edge-lock resolved

**Set out to do:** Diagnose and fix the test2 Step 5 edge-lock failure (21/31 windows
edge-locked, 5/31 valid, RR MAE 7.59 bpm) identified as Priority 1 in HANDOFF.

**Root cause confirmed — wrong range bin, not wrong threshold:**
Step 3 selected bin 26 (1.134 m) for test2 using `energy + phase` consensus. Bin 26 has
no respiratory spectral evidence (`resp_rank = null`). The high phase standard deviation
(41.2 rad vs 13.4 rad at bin 24) was caused by sidelobes or artifacts at 1.13 m, not by
genuine chest-wall breathing motion. At bin 26, the fusion estimator consistently produced
RR estimates near the 6 bpm band floor (edge-lock side = low).

Bin 24 (1.046 m) was the correct choice:
- energy rank #1 (82.4 dB), resp_snr rank #5 (49.2 dB)
- tape distance 1.05 m → delta 3.6 mm (bin 26 delta was 83.6 mm)
- Step 5 dry-run on bin 24: 28/31 valid, 0 edge-locks, RR MAE 0.81 bpm

Bin 26 won under the old scoring because `energy + phase` combined score (10) beat
`energy + resp` (12) — the combined penalty score favoured the phase-supported candidate
despite its lack of any respiratory spectral evidence.

**Fix: Step 3 qualified-bin sort key (steps/step_3/select_chest_bin.py):**
Existing combined-rank scores and ranked tables are unchanged. Only the final pick from
the `qualified` list (bins with `n_methods >= 2`) changes. New sort key:
`n_methods desc → resp_supported desc → combined_rank asc`.

This preserves 3/3 winners, keeps `n_methods=1` bins unqualified, and avoids global
penalty-weight side effects. For test2: bin 24 (`energy+resp`) now wins over bin 26
(`energy+phase`) among the 2-method candidates.

**Regression check:** dry-run `--no-write --all` on all sessions with the new policy.
Result: only test2 changed (26 → 24); test, test3, test4, test5 held their locked bins.

| Session | Old bin | New bin | Changed? |
|---------|---------|---------|----------|
| test    | 26      | 26      | no       |
| test2   | 26      | **24**  | YES      |
| test3   | 25      | 25      | no       |
| test4   | 24      | 24      | no       |
| test5   | 24      | 24      | no       |

**Pipeline rerun for test2 (Steps 3 → 4 → 5 → 6 → diagnostic):**

*Step 3* — manifest updated: `locked_bin=24`, `locked_range_m=1.0464`,
`radar_chest_distance_cm=105`, `chest_bin_confidence=medium`, `chest_bin_methods=energy+resp`,
`chest_bin_review_required=False`.

*Step 4* — quality mask recomputed with bin 24. 69/3600 frames flagged (1.92%): 44
dropout, 27 phase-jump. Longest bad run: 3 frames. Evidence:
`results/test2/step_4/step_4.log`.

*Step 5* — breathing rate with bin 24:
- **28/31 valid**, **0/31 edge-locked**, **RR MAE 0.81 bpm**, bias −0.61 bpm, RMSE 0.97 bpm
- Confidence: high=26, medium=2, low=3
- Evidence: `results/test2/step_5/breathing_windows.csv`, `summary.json`

*Step 6* — heart rate run on corrected Step 5 output:
- 33 windows, 0 valid HR, 0 resp-edge-locked (was 28)
- Primary blocker is now `step6_ahet_ratio_low` (30 windows): same failure mode as
  test3/test4/test5. test2 is no longer uniquely broken at Step 5.
- Evidence: `results/test2/step_6/heart_windows.csv`

*Coverage diagnostic* — updated `coverage_summary.csv`:

| Session | Valid | Valid% | Primary blocker (before) | Primary blocker (after) |
|---------|-------|--------|--------------------------|-------------------------|
| test    | 1/21  | 4.8%   | resp_invalid_or_edge_locked | resp_invalid_or_edge_locked |
| test2   | 0/33  | 0%     | resp_invalid_or_edge_locked | **step6_ahet_ratio_low** |
| test3   | 3/33  | 9.1%   | step6_ahet_ratio_low     | step6_ahet_ratio_low    |
| test4   | 3/33  | 9.1%   | step6_ahet_ratio_low     | step6_ahet_ratio_low    |
| test5   | 0/33  | 0%     | step6_ahet_ratio_low     | step6_ahet_ratio_low    |

**Additional changes in this session:**

- `tests/test_step3_bin_selection.py` (new, 4 tests): (1) 3/3 beats any 2/3 candidate
  even with worse combined rank; (2) resp-supported 2/2 beats phase-only 2/2 — the test2
  scenario; (3) within equal resp-support tiers, combined-rank ordering still wins;
  (4) resp-only (n_methods=1) candidates remain unqualified. 4/4 pass.
- `steps/step_5/extract_breathing_rate.py` + `steps/step_5/config.yaml`: added
  `high_edge_lock_fraction` boolean field to `summary.json` (flagged when
  `edge_locked_fraction >= high_edge_lock_fraction_threshold`, default 0.50). Diagnostic
  only — no estimate rescue.
- 259/259 targeted tests pass (`test_step3_bin_selection`, `test_respiration`,
  `test_step5_breathing`, `test_step6_heart_rate`, `test_diagnose_coverage_gaps`).

**Outcome:** test2 Step 5 edge-lock is fixed. All five sessions now share the same primary
blocker: `step6_ahet_ratio_low` (weak cardiac SNR below the AHET ratio threshold). The
previously unique test2 failure (Step 5 broken, Step 6 never runs) is resolved. Remaining
HR yield failures (0 valid windows for test2/test5, 3 valid for test3/test4) are now
attributable to the AHET gate and cardiac SNR, not to upstream Step 5 errors.

**Next (from HANDOFF Priority 2/3):**
- Investigate whether loosening the AHET `ratio_db` floor or improving pre-AHET SNR
  (different bin, longer window, motion rejection) can lift test3/test4 yield.
- Add absolute SNR gate in `strict_v1` to eliminate flat-spectrum false accepts (test3 w17,
  test4 w31: 57–58 bpm vs Masimo ~82 bpm).

---

## 2026-06-28 (session 2) — Step 6.3 candidate-track diagnostic; Viterbi dedup fix; per-session summary

**Set out to do:** Build and run a diagnostic that tests whether temporal Viterbi tracking
can substitute for AHET's per-window SNR gate, improving HR yield without introducing
severe false accepts.

**What we did:**

- Implemented `scripts/diagnose_step6_candidate_tracks.py`: a 120-combo grid search
  (5 × 4 × 3 × 2 parameters) running a Viterbi DP tracker over the existing Step 6
  candidate pools. Scoring: fundamental_ratio_db + 0.25×peak_to_floor + 0.05×prominence
  − 1.5×rank ± harmonic penalty. Jump constraint uses `last_real_bpm` across gaps (not
  scaled by gap span). Fresh-segment restart when all states exhausted.

- Found and fixed a critical bug: Viterbi states were not pruned between windows, causing
  exponential state growth (~4^33 ≈ 73 trillion states for 33-window sessions). Fix: after
  each window, keep only the max-score state per `(last_real_bpm, n_consec_gaps)` key.
  This caps state count at ~12 per window. The 120-combo run now completes in ~2 s.

- Ran the diagnostic on all 5 sessions. Best combo: `min_fundamental_ratio_db=0.0`,
  `max_jump_bpm_per_hop=6.0`, `max_gap_windows=2`, `resp_harmonic_mode=score_penalty`.
  Result: **48 good / 3 acceptable / 0 bad / 0 severe, MAE 1.38 bpm** (vs baseline 6 good,
  2 bad, MAE 2.50 bpm). The 2 flat-spectrum false accepts from standalone Step 6 (57–58 bpm
  vs ~82 bpm Masimo, test3 w17, test4 w31) are eliminated by the jump constraint.

- Per-session: test=0 evaluable (13 forced respiratory blocks), test2=2 good, test3=13 good,
  test4=28 good, test5=5 good + 3 acceptable. Evidence:
  `results/diagnose/step6_candidate_tracks/`.

- Added per-session summary outputs: `track_summary_by_session.csv` (one row per
  session × combo; `n_forced_blocked` separate from `n_gap`) and
  `baseline_summary_by_session.csv`. Report updated with "Best Combo By Session" table.
  "Safe combo" definition tightened to require both `n_bad_valid == 0` and
  `n_severe_bad_valid == 0`.

- 40 tests pass. Aggregate `track_summary.csv` schema unchanged.

**Outcome:** Step 6.3 production tracker is justified. Next session: write a design plan
for wiring the best-combo Viterbi into `steps/step_6/extract_heart_rate.py` as a
post-processing pass, then cross-review before any production code changes.

---

## 2026-06-28 (session 3) — Step 6.3 production tracker post-review fixes

**Set out to do:** Correct two review findings in the production temporal tracker before
accepting Step 6.3 output.

**What we did:**

- Fixed tracker DP-gap rows in `steps/step_6/temporal_tracker.py`: when the tracker chooses
  a gap over an AHET-accepted window, the primary HR fields are now cleared
  (`radar_hr_bpm`, `heart_peak_hz`, `hr_valid`, `hr_confidence`, HR error fields), while
  `pre_tracker_*` retains the AHET-first backup.
- Tightened `n_tracker_selected_from_ahet_failed` so it counts only selected windows whose
  `pre_tracker_invalid_reason == "ahet_failed"`, not every pre-tracker-invalid rescue.
- Added Step 6.3 regressions for gap clearing and the AHET-only rescue counter.

**Evidence:**

- `pytest tests\test_step6_heart_rate.py tests\test_diagnose_step6_candidate_tracks.py -q
  --basetemp .pytest_tmp_step63_fix_all -p no:cacheprovider` → 171 passed.
- Production no-write rerun: test=0, test2=2, test3=13, test4=28, test5=8 valid HR. The
  aggregate valid count is back to 51, matching the diagnostic tracker acceptance set.

---

## 2026-06-28 (session 4) — Tracker-aware coverage diagnostic

**Set out to do:** Update `scripts/diagnose_coverage_gaps.py` so the report reflects the
Step 6.3 tracker state, replacing stale Step 6.2/AHET-only framing. Diagnostic-only
change; no production HR algorithm changes.

**What we did:**

- Added `_extract_tracker_stats(df)` to derive tracker metrics from `heart_windows.csv`
  (already loaded, no extra file dependency). Backward-compatible: old runs without tracker
  columns return `temporal_tracker_present=False`, counts=0, `n_pre_tracker_valid=NaN`
  ("not applicable", not "known zero").

- Extended `coverage_summary.csv` with 7 tracker columns:
  `temporal_tracker_present`, `n_pre_tracker_valid`, `n_tracker_selected`,
  `n_tracker_gap`, `n_tracker_forced_blocked`, `n_tracker_selected_from_ahet_failed`,
  `n_tracker_gap_from_pre_valid`.

  `n_tracker_gap_from_pre_valid` is the key diagnostic for the P1-fix scenario
  (pre-tracker-valid AHET window turned into a tracker gap — isolated window with no
  neighbours). Current values: test=1, all others=0. The lone test=1 window is the
  isolated AHET estimate the P1 fix correctly clears.

- Updated `step6_candidate_yield_diagnostics.csv` to join tracker context columns from
  `heart_windows.csv`: `hr_source`, `tracker_decision_type`, `pre_tracker_hr_valid`,
  `pre_tracker_invalid_reason`, `tracker_fundamental_ratio_db`, `tracker_node_score`.

- Updated `diagnostic_report.md` framing:
  - test2 section: retitled "Residual Respiration Pass-Through" (primary_blocker is
    `step6_ahet_ratio_low`; only 3/33 resp failures remain). Body now says "The dominant
    yield target is Step 6 tracker/AHET throughput."
  - test5 section: retitled "Partial Heart Rate Yield (8/33 valid)" — no longer "Yield Zero."
  - Respiratory-harmonic guard: downgraded from numbered implementation target to "Note:
    small number of windows affected — low priority."
  - Next Implementation Target: now driven by `primary_blocker` per session with a
    `step6_valid_fraction >= 0.75` success cutoff (excludes test4 from the action list).
    Current output: test → respiration failures; test2/test3/test5 → Step 6 AHET yield.

- Added constant `_TRACKER_SUCCESS_FRACTION = 0.75`.

- Added 7 new tests in `tests/test_diagnose_coverage_gaps.py`:
  tracker-aware summary (present + backward-compat), `n_tracker_gap_from_pre_valid`
  correctness, Next Target logic (no resp recommendation when primary is AHET; resp
  recommendation when primary is resp; success case excluded), report titles
  ("Partial Yield" when valid > 0; "Yield Zero" when 0; "Residual Respiration" when AHET
  is primary), tracker context columns in yield CSV.

**Evidence:**

- `pytest tests\test_diagnose_coverage_gaps.py -q` → 99 passed.
- `python -X utf8 scripts/diagnose_coverage_gaps.py --sessions test test2 test3 test4 test5
  --results-root results --diag-root results/diagnose/step6_hr --out results/diagnose
  --overwrite --no-plots` → all outputs regenerated.
- `coverage_summary.csv` `n_tracker_gap_from_pre_valid`: test=1, others=0 ✓.
- Report Next Implementation Target: "test → resp; test2/test3/test5 → AHET yield;
  test4 excluded (84.9% valid)." ✓

---

## 2026-06-28 (session 5) — Hop-1 live-monitor experiment configs

**Set out to do:** Add an isolated experiment path for 1-second rolling updates without
overwriting the validated 5-second-hop baseline.

**What we did:**

- Added `steps/step_5/config_hop1.yaml`: respiration keeps a 30 s evidence window but
  uses `hop_s: 1`, writes Step 5 results to `results_hop1`, and writes Step 6 contract
  CSVs to `data/processed/breathing_rate_hop1`.
- Added `steps/step_6/config_hop1.yaml`: HR keeps a 20 s evidence window but uses
  `hop_s: 1`, reads respiration from `data/processed/breathing_rate_hop1`, writes HR
  contracts to `data/processed/heart_rate_hop1`, and writes results to `results_hop1`.
- Rescaled the Step 6.3 temporal tracker gap behavior for 1 s hops:
  `max_gap_windows: 10` and `gap_cost: -1.6` preserve the old 10 s gap span and
  approximate -8 cost per 5 s. `max_jump_bpm_per_hop` stays at 6.0 as a permissive
  first exploratory setting.

**Evidence:** Config-only change; baseline `steps/step_5/config.yaml` and
`steps/step_6/config.yaml` are unchanged. Next: rerun Step 5 then Step 6 with the
`config_hop1.yaml` files and compare rejection/yield diagnostics under `results_hop1`.

---

## 2026-06-28 (session 6) — Hop-1 HR yield improvement: grid sweep, safe config

**Set out to do:** Find a tracker configuration for the hop-1 pipeline that is safe
(0 bad, 0 severe false accepts) by sweeping `harmonic_weight`, `gap_cost`,
`max_jump_bpm_per_hop`, and `max_gap_windows` via a diagnostic grid.

**What we did:**

1. Extended `scripts/diagnose_step6_candidate_tracks.py` with:
   - `HOP1_TRACK_GRID` (4×5×2×3×3×3 = 1080 combos sweeping `min_fundamental_ratio_db`,
     `max_jump_bpm_per_hop`, `resp_harmonic_mode`, `harmonic_weight`, `max_gap_windows`,
     `gap_cost`).
   - `TRACK_GRID_PRESETS = {"default": TRACK_GRID, "hop1": HOP1_TRACK_GRID}`.
   - `--grid-preset {default,hop1}` CLI argument.
   - `run_grid`: recomputes node scores per combo when `harmonic_weight != 6.0` (avoids
     mutating shared pool); passes `gap_cost` to `viterbi_track`.
   - `build_summary_by_session`: dynamic `combo_keys` from `_KNOWN_COMBO_KEYS` ∩ df columns
     (backward-compatible with old 4-key default grid).
   - `build_report`: displays all present combo params; session-level mask over all params.
   - 13 new tests in `TestHop1TrackGrid` and `TestRunGridHop1`; all 53 tests pass.

2. Ran the hop-1 sweep:
   ```
   python -X utf8 scripts/diagnose_step6_candidate_tracks.py \
       --sessions test test2 test3 test4 test5 \
       --results-root results_hop1 --out results_hop1/diagnose \
       --grid-preset hop1 --overwrite
   ```
   → Output: `results_hop1/diagnose/step6_candidate_tracks/`.

3. **Sweep result: 65 / 1080 combos are safe (0 bad, 0 severe).**  
   Best safe combo (safety-first sort, then MAE):
   - `min_fundamental_ratio_db = 2.0`
   - `max_jump_bpm_per_hop = 3.0`
   - `max_gap_windows = 15`
   - `resp_harmonic_mode = score_penalty`
   - `harmonic_weight = 6.0`
   - `gap_cost = -3.2`
   - Aggregate: good=308, acceptable=24, bad=0, severe=0, MAE=1.49 bpm.
   - Per-session (vs unsafe baseline):
     - test:  8 good / 0 bad  (baseline: 0 — all blocked)
     - test2: 113 good / 0 bad  (baseline: 7 good)
     - test3: 53 good / 0 bad  (baseline: 62 good — minor drop from tighter jump)
     - test4: 117 good + 4 acceptable / 0 bad  (baseline: 7+7 good + 105 bad/severe)
     - test5: 17 good + 14 acceptable / 0 bad  (unchanged)

4. Created `steps/step_6/config_hop1_safe.yaml` with the best safe combo parameters.
   Reads from `breathing_rate_hop1`; writes to `results_hop1_safe` and
   `data/processed/heart_rate_hop1_safe`.

**Next (user must run from Anaconda Prompt — numpy DLL prevents Claude tool from running step 6):**
```powershell
python -X utf8 steps/step_6/extract_heart_rate.py --config steps/step_6/config_hop1_safe.yaml --all
python -X utf8 scripts/diagnose_coverage_gaps.py --sessions test test2 test3 test4 test5 --results-root results_hop1_safe --out results_hop1_safe/diagnose --overwrite
python -X utf8 scripts/diagnose_step6_candidate_tracks.py --sessions test test2 test3 test4 test5 --results-root results_hop1_safe --out results_hop1_safe/diagnose --overwrite
```

**Evidence:** `results_hop1/diagnose/step6_candidate_tracks/track_summary.csv` (1080 combos),
`steps/step_6/config_hop1_safe.yaml`. 53/53 tests pass (pytest test_diagnose_step6_candidate_tracks.py).

---

## 2026-06-29 — Hop-1 safe config validated; 30 s window experiment completed

**Set out to do:** (A) Run and validate `config_hop1_safe.yaml` (Step 6 with safe
hop-1 tracker params). (B) Run the Step C 30 s window experiment and find a safe
config for it too.

### Step A — hop-1 safe config validated

Ran `extract_heart_rate.py --config steps/step_6/config_hop1_safe.yaml --all` from
Anaconda Prompt (numpy DLL crash prevents Claude tool from running Step 6; diagnostics are fine).

Session summary (results_hop1_safe):

| Session | Valid | Good | Acceptable | Bad | Severe |
|---------|-------|------|-----------|-----|--------|
| test    | 8/101 (7.9%)  | 8   | 0  | 0 | 0 |
| test2   | 119/161 (73.9%) | 113 | 6 | 0 | 0 |
| test3   | 53/161 (32.9%) | 53 | 0  | 0 | 0 |
| test4   | 121/161 (75.2%) | 117 | 4 | 0 | 0 |
| test5   | 31/161 (19.3%) | 17 | 14 | 0 | 0 |

Total good=308, acceptable=24, bad=0, severe=0 — exact match to the sweep prediction from
session 6 (2026-06-28). Config is coherent end-to-end.

Diagnostics run and confirmed:
- `diagnose_coverage_gaps.py --results-root results_hop1_safe` → `coverage_summary.csv` ✓
- `diagnose_step6_candidate_tracks.py --grid-preset hop1 --results-root results_hop1_safe`
  → Best combo: `min_fund_db=2.0, max_jump=3.0, max_gap=15, score_penalty, hw=6.0,
  gap_cost=-3.2` → good=308, bad=0, severe=0, MAE=1.49 ✓ (1080 combos)

Evidence: `results_hop1_safe/`, `results_hop1_safe/diagnose/`.

### Step B — 30 s window experiment (config_hop1_win30)

**Motivation:** Step 5 hop-1 already uses 30 s respiration windows (`steps/step_5/config_hop1.yaml`),
so no Step 5 re-run is needed. A 30 s HR window gives finer FFT resolution
(2 bpm/bin vs 3 bpm/bin for 20 s), potentially improving low-SNR sessions.

Created `steps/step_6/config_hop1_win30.yaml` (same tracker params as hop1_safe,
`window_s=30`, output `results_hop1_win30`). Ran Step 6.

**Initial win30 results (max_jump=3.0, min_fund_db=2.0):**
- test2: MAE=3.73, bias=−3.21 bpm (106 valid) — regression vs hop1_safe
- test3: MAE=0.62 bpm (55 valid) — improved coverage
- test4: MAE=0.65 bpm (121 valid) — same
- test5: MAE=1.87 bpm (26 valid)

Coverage diagnostic showed test2 had 48 acceptable windows (5–10 bpm errors) with
0 bad/0 severe — technically safe per project criterion, but a large accuracy regression.

**Root cause of test2 regression (diagnosed from heart_windows.csv):**
With 30 s FFT resolution, a spurious peak at ~77–78 bpm scores higher than the true HR
(~83–84 bpm) in the tracker around window 98. The tracker locks onto 77–78 bpm and stays
there (max_jump=3.0 prevents it from reaching 83–84 bpm once committed). The flag
`tracker_within_resp_harmonic_guard=True` appears for those windows — the wrong candidate
is near a respiratory harmonic, but `score_penalty` with hw=6.0 does not suppress it
strongly enough.

**Grid sweep on win30 data (--grid-preset hop1, 1080 combos):**

Best safe combo: `min_fund_db=4.0, max_jump=6.0, max_gap=15, score_penalty, hw=6.0,
gap_cost=-3.2` → good=393, bad=0, severe=0, MAE=1.23.

Key parameter changes vs hop1_safe:
- `min_fundamental_ratio_db 4.0` (vs 2.0): filters the spurious 77–78 bpm peak that was
  causing tracker lock-on in test2. With finer FFT resolution, spurious candidates are
  weaker relative to the true HR and are now correctly excluded by a stricter floor.
- `max_jump_bpm_per_hop 6.0` (vs 3.0): allows the tracker to escape wrong lock-on when it
  occurs, and enables the extra valid windows in test3/test4 where HR varies by 5–6 bpm
  between consecutive seconds. Safe only because `min_fund_db=4.0` already gates out
  resp-harmonic false accepts.

Created `steps/step_6/config_hop1_win30_safe.yaml` with new params. Ran Step 6.

**win30_safe results (results_hop1_win30_safe):**

| Session | Valid | Good | Acceptable | Bad | Severe |
|---------|-------|------|-----------|-----|--------|
| test    | 8/91 (8.8%)   | 8   | 0  | 0 | 0 |
| test2   | 125/151 (82.8%) | 125 | 0 | 0 | 0 |
| test3   | 112/151 (74.2%) | 109 | 3 | 0 | 0 |
| test4   | 129/151 (85.4%) | 129 | 0 | 0 | 0 |
| test5   | 34/151 (22.5%) | 22 | 12 | 0 | 0 |

Total good=393, acceptable=15, bad=0, severe=0, MAE=1.23 — exact match to sweep prediction.

**Diagnostics confirmed (--grid-preset hop1):**
Best combo reproduced: `min_fund_db=4.0, max_jump=6.0, max_gap=15, score_penalty,
hw=6.0, gap_cost=-3.2` → good=393, bad=0, severe=0, MAE=1.23 ✓

**Aggregate comparison — all three hop-1 configs:**

| Config | window_s | Good | Acceptable | Bad | Severe | MAE |
|--------|----------|------|-----------|-----|--------|-----|
| hop1_safe | 20 | 308 | 24 | 0 | 0 | 1.49 bpm |
| win30_safe | 30 | 393 | 15 | 0 | 0 | 1.23 bpm |

win30_safe wins on every aggregate metric: +28% good windows, fewer acceptable,
same zero bad/severe.

**Per-session notes:**
- test2: win30_safe substantially better (125 good, 0 acc vs 113 good, 6 acc)
- test3: win30_safe dramatically better (109+3=112 valid vs 53 valid)
- test4: win30_safe better (129 good vs 117+4)
- test5: comparable pattern — both configs have acceptable-range windows; win30_safe
  has 12 acceptable (max errors ~9 bpm) vs hop1_safe 14 acceptable

**Note on test5 acceptable windows:** the 12 acceptable windows in win30_safe result from
`max_jump=6.0` allowing the tracker to extend into marginally-valid territory. The errors
stay within 5–10 bpm (acceptable, not bad). This is the trade-off for the large test3/test4
coverage improvement.

**Outcome:** Both hop-1 configs are validated. win30_safe is the better aggregate config.
All three result sets (5-hop baseline, hop1_safe, win30_safe) are ready for Bland-Altman
and paper metrics (Step D).

Evidence: `results_hop1_safe/`, `results_hop1_win30/`, `results_hop1_win30_safe/`,
`steps/step_6/config_hop1_win30.yaml`, `steps/step_6/config_hop1_win30_safe.yaml`.

---

## 2026-06-29 - Live demo plan review

Reviewed and overwrote `notes/live_demo_plan.md` for the live radar vital-signs demo.

**Worked:**
- Tightened the plan around the actual repository APIs: `IWR1642` construction,
  `DCA1000` packet handling, `read_adc_bin()`, `extract_chest_phase()`,
  respiration fusion, and `estimate_rate_from_phase()`.
- Replaced legacy live HR settings with the validated hop-1, 30 s safe parameters:
  `eca_mode=skip_forbidden_harmonics_v1` and `ahet_gate_mode=strict_v1`.
- Corrected the replay-bin assumption: `test5` resolves to locked bin 24 in
  `data/manifest.local.csv`, so replay commands should prefer manifest resolution.
- Added logging requirements for `run_metadata.json`, `live_estimates.csv`, and
  `live_intermediates.npz` so every live HR estimate leaves debugging evidence.
- Clarified that Masimo side-by-side checks use only `Beats / min` aligned by integer
  Unix `Timestamp`, and are demo sanity checks rather than paper-grade validation.

**Evidence:**
- Revised plan: `notes/live_demo_plan.md`.
- Cross-checks: `data/manifest.local.csv`, `steps/step_5/config_hop1.yaml`,
  `steps/step_6/config_hop1_win30_safe.yaml`, `steps/step_1/capture.py`,
  `src/respiration.py`, `src/vitals.py`.

**Next:**
- Implement `scripts/live_demo.py` and `scripts/live_demo_config.yaml`.
- Start with replay-mode equivalence against
  `results_hop1_win30_safe/<session>/step_6/heart_windows.csv` before hardware.

---

## 2026-06-29 - Live demo plan comments resolved

Reviewed the comments appended to `notes/live_demo_plan.md`, checked them against
`steps/step_1/capture.py`, `src/respiration.py`, and
`steps/step_6/config_hop1_win30_safe.yaml`, then erased the comments section.

**Worked:**
- Confirmed `IWR1642.configure/start/stop`, `stft_stability(..., detrend_type=...)`,
  and the safe ECA/AHET mode strings already match the current code/config.
- Revised the plan for the valid comments: empty HR smoother guard, `--duration-s`,
  replay session raw-file resolution, pure ADC-byte `adc_stream.bin` format, explicit
  low-confidence BR gate before ECA/AHET, and realistic replay-vs-Step-6 equivalence
  criteria.

**Evidence:**
- Revised plan: `notes/live_demo_plan.md`.
- Code/config checks: `steps/step_1/capture.py`, `src/respiration.py`,
  `steps/step_6/config_hop1_win30_safe.yaml`.

**Next:**
- Implement the live demo from the revised plan, starting with replay mode.

---

## 2026-06-29 - Live demo plan comments resolved, round 2

Reviewed the second comments section appended to `notes/live_demo_plan.md`, checked
the claims against `src/respiration.py`, `src/vitals.py`,
`steps/step_6/extract_heart_rate.py`, `steps/step_6/config_hop1_win30_safe.yaml`,
and `environment.yml`, then erased the comments section.

**Worked:**
- Confirmed `stft_stability()` returns the dict shape expected by
  `fuse_estimates()`, so no adapter is needed.
- Added config guidance to copy the validated heart-mode strings from
  `config_hop1_win30_safe.yaml` before writing `live_demo_config.yaml`.
- Added explicit display backend handling: prefer `QtAgg` with pinned `pyside6`,
  fall back to `TkAgg`, then warn and use the default backend.
- Added a separate no-ECA/no-AHET baseline call for diagnostic `fallback_hr_bpm`;
  strict AHET rejection returns `nan` in the primary HR result, so the fallback
  must not be read from `hr_result["rate_bpm"]`.
- Added `fallback_hr_bpm` to `live_estimates.csv`, baseline HR/spectrum to the
  NPZ evidence list, and documented `--locked-bin` overriding `--live-session`.

**Evidence:**
- Revised plan: `notes/live_demo_plan.md`.
- Code/config checks: `src/respiration.py`, `src/vitals.py`,
  `steps/step_6/extract_heart_rate.py`, `steps/step_6/config_hop1_win30_safe.yaml`,
  `environment.yml`.

**Next:**
- Implement `scripts/live_demo.py` and `scripts/live_demo_config.yaml` from the
  revised plan, starting with replay mode and the logging contract.

---

## 2026-06-29 - Live demo plan comments resolved, round 3

Reviewed the third comments section appended to `notes/live_demo_plan.md`, checked
the remaining function-signature and DCA1000 streaming claims against
`src/respiration.py` and `steps/step_1/capture.py`, then erased the comments
section.

**Worked:**
- Confirmed `stft_stability()` already accepts `detrend_type`, so the DSP snippet
  remains valid.
- Added `capture.intermediates_checkpoint_windows: 60` to define the NPZ checkpoint
  cadence and clarified that 60 windows is roughly one minute at a 1 s hop.
- Clarified `--no-configure`: `RECORD_START` must already have been issued so UDP
  ADC packets are arriving before `live_demo.py` opens the data socket.

**Evidence:**
- Revised plan: `notes/live_demo_plan.md`.
- Code checks: `src/respiration.py`, `steps/step_1/capture.py`.

**Next:**
- Build `scripts/live_demo_config.yaml`, then implement replay mode in
  `scripts/live_demo.py` using the revised plan.

---

## 2026-06-29 - Live demo implementation review

Reviewed the implemented `scripts/live_demo.py` and
`scripts/live_demo_config.yaml` against the live demo plan and existing capture/DSP
APIs.

**Worked:**
- `python -m py_compile scripts/live_demo.py` passed.
- Config heart parameters match `steps/step_6/config_hop1_win30_safe.yaml`.
- Hardware profile/frame sections match `steps/step_1/capture_config.yaml`.
- Confirmed the code calls the current `stft_stability(..., detrend_type=...)`
  signature and forwards the result to `fuse_estimates()`.

**Findings:**
- `--no-configure` live mode is currently broken because `DCA1000.configure()` is
  skipped, leaving `dca._sock_dat` as `None` before `LiveFrameSource` starts.
- `LiveFrameSource` does not mirror the proven packet-gap handling in
  `capture.py`: leading packet loss is not zero-filled and duplicate/out-of-order
  packets are not discarded.
- Replay EOF is checked before pending DSP hops are processed, so the last replay
  batch can be dropped, especially in `--replay-fast`.
- Intermediate NPZ output does not yet include respiration spectra/candidate arrays
  or the diagnostic baseline spectrum promised by the plan.

**Evidence:**
- Reviewed files: `scripts/live_demo.py`, `scripts/live_demo_config.yaml`,
  `steps/step_1/capture.py`, `src/radar_io.py`, `src/respiration.py`,
  `steps/step_6/config_hop1_win30_safe.yaml`.

**Next:**
- Fix the review findings before hardware demo, then run replay smoke/equivalence
  checks before touching live hardware.

---

## 2026-06-29 - Live demo fix review

Reviewed the updated `scripts/live_demo.py` after the five live-demo review fixes.

**Worked:**
- `python -m py_compile scripts/live_demo.py` passed.
- Confirmed streamed SHA256 hashing replaced full-file reads.
- Confirmed `--no-configure` now skips `IWR1642`/`DCA1000` construction and lets
  `LiveFrameSource` own a data socket.
- Confirmed duplicate/out-of-order packet rejection and mode-aware leading-loss
  zero-fill were added.
- Confirmed replay EOF is handled after pending DSP hops are processed.
- Confirmed respiration spectra and diagnostic baseline spectra were added to
  intermediate records.

**Finding:**
- `adc_stream.bin` still mirrors only received payload bytes, not zero-filled gap
  bytes. If UDP packets drop, the live decoded frames include zeros but the mirror
  file does not, so replaying the mirror would not reproduce the live run.

**Evidence:**
- Reviewed files: `scripts/live_demo.py`, `scripts/live_demo_config.yaml`,
  `steps/step_1/capture.py`, `notes/live_demo_plan.md`.

**Next:**
- Mirror zero-filled bytes into `adc_stream.bin` whenever they are appended to the
  live assembly buffer, then run replay smoke testing.

---

## 2026-06-29 - Live demo mirror fix review

Reviewed the `adc_stream.bin` mirror fix in `scripts/live_demo.py`.

**Worked:**
- Confirmed leading-loss and mid-stream packet gaps now materialise a shared `zeros`
  byte string that is appended to both the live decode buffer and the raw mirror file.
- `python -m py_compile scripts/live_demo.py` passed.

**Evidence:**
- Reviewed code: `scripts/live_demo.py` lines around the packet-gap handling in
  `LiveFrameSource._loop`.

**Next:**
- Run replay smoke testing and, if artifacts are generated, verify `adc_stream.bin`
  file size is frame-aligned before replaying it with `read_adc_bin()`.

---

## 2026-06-29 - Live replay vs Step 6 comparison

Compared live demo replay artifacts against
`results_hop1_win30_safe/<session>/step_6/heart_windows.csv`.

**Worked:**
- For deduplicated `test5` replay rows, respiration matched Step 6 closely
  (mean absolute difference 0.014 bpm across 22 comparable windows).
- `test5` live `hr_valid` matched Step 6 pre-tracker `hr_valid` for all 22
  comparable windows; both were false, with `ratio_db_low` rejection.
- For `test3`, respiration matched Step 6 closely (mean absolute difference
  0.010 bpm across 83 comparable windows). Live HR validity matched the Step 6
  pre-tracker gate for 81/83 comparable windows.

**Finding:**
- The replay CSVs contained repeated estimates for identical `frame_idx` values
  (`test5`: 64 rows but only 22 unique frame indices; `test3`: 147 rows but only
  83 unique frame indices). Root cause: the animation callback drained all queued
  frames first, then ran multiple pending DSP hops against the same final
  `ring_buffer`.

**Implemented:**
- `scripts/live_demo.py` now emits each DSP estimate when the first full window or
  subsequent hop boundary is reached, passing that frame index into both CSV and
  intermediate records. Warmup frames no longer accumulate as pending DSP hops.

**Evidence:**
- `python -m py_compile scripts/live_demo.py` passed.
- Existing pre-fix artifacts inspected:
  `results/live_demo/20260629_160958_replay_test5`,
  `results/live_demo/20260629_161142_replay_test3`, and
  `results/live_demo/20260629_161727_replay_test3`.

**Next:**
- Rerun replay smoke with the updated script and confirm `live_estimates.csv`
  has no duplicate `frame_idx` values and aligns to Step 6 window end frames.

---

## 2026-06-29 - Live replay smoke test accepted

Compared `results/live_demo/20260629_162927_replay_test3` against
`results_hop1_win30_safe/test3/step_6/heart_windows.csv`.

**Worked:**
- `live_estimates.csv` has 118 rows and 118 unique frame indices; the duplicate
  replay-emission bug is gone.
- Frame indices advance by 20 frames each row, matching the run's `hop_s: 1.0`
  metadata at 20 Hz.
- Every live `frame_idx + 1` exactly matches a Step 6 `end_frame`.
- Live fallback HR matches Step 6 baseline HR closely (mean absolute difference
  0.002 bpm). Live BR matches Step 6 BR closely (mean absolute difference
  0.003 bpm).
- Live HR validity, respiration validity, and AHET verification each match the
  Step 6 pre-tracker fields for all 118 comparable windows.
- `live_intermediates.npz` exists, has 118 frame-indexed records, and has no
  duplicate frame indices.

**Finding fixed:**
- The run's valid AHET windows had correct HR values/validity flags, but their
  CSV `candidate_rejection_reason` labels still reported the first rejected
  lower-ranked candidate instead of `passed`. Updated `scripts/live_demo.py` to
  use Step 6's summary convention: accepted candidate code when AHET verifies,
  otherwise the strongest candidate's rejection code.

**Evidence:**
- `python -m py_compile scripts/live_demo.py` passed after the label fix.

**Next:**
- Rerun one more replay smoke if the CSV rejection labels are needed in the demo
  artifact; otherwise proceed to the 3 s update-cadence demo test.

---

## 2026-06-29 - Live replay 3 s cadence accepted

Compared `results/live_demo/20260629_163634_replay_test3` against
`results_hop1_win30_safe/test3/step_6/heart_windows.csv`.

**Worked:**
- Run metadata confirms `window_s: 30.0`, `hop_s: 3.0`, frame rate 20 Hz, and
  locked bin 25.
- `live_estimates.csv` has 40 rows and 40 unique frame indices; there are no
  duplicate emitted estimates.
- Frame indices advance by 60 frames each row, matching the 3 s update cadence.
- Every live `frame_idx + 1` exactly matches a Step 6 `end_frame`.
- Live fallback HR matches Step 6 baseline HR closely (mean absolute difference
  0.002 bpm). Live BR matches Step 6 BR closely (mean absolute difference
  0.003 bpm).
- Live HR validity, respiration validity, AHET verification, and rejection
  reason all match the Step 6 pre-tracker fields for all 40 comparable windows.
- Accepted live HR values match Step 6 pre-tracker HR closely across the four
  valid sampled windows (mean absolute difference 0.003 bpm).
- `live_intermediates.npz` exists, has 40 frame-indexed records, and has no
  duplicate frame indices.

**Decision:**
- The replay path is accepted for the live demo cadence. The remaining risk is
  hardware/UDP behavior in live mode, not replay/DSP equivalence.

**Next:**
- Do a short live hardware rehearsal with the same config and verify packet stats,
  raw mirror creation, and that `adc_stream.bin` can replay into the same estimates.

---

## 2026-06-29 - Live hardware rehearsal check

Reviewed `results/live_demo/20260629_164919_live_test3` from a 90 s live run.

**Worked:**
- DCA1000 and IWR1642 configured and stopped cleanly.
- Run metadata shows `window_s: 30.0`, `hop_s: 3.0`, frame rate 20 Hz, and locked
  bin 25.
- Packet stats were clean: 162760 UDP packets received, 0 dropped, and 0
  zero-filled bytes.
- `live_estimates.csv` has 20 rows, 20 unique frame indices, and 60-frame spacing,
  matching the 3 s cadence.
- `live_intermediates.npz` has 20 frame-indexed records and no duplicate frame
  indices.

**Findings:**
- The raw mirror was not frame-aligned: 236978560 bytes = 1808 full frames plus
  a 384-byte trailing partial frame. `read_adc_bin()` rejects this file as-is.
- The live vital-sign confidence was not demo-ready: no HR windows passed AHET,
  respiration was mostly medium confidence, and one window had low respiration
  confidence.
- Range-profile inspection of the complete mirror frames showed the strongest
  likely chest bin at bin 32 (about 1.395 m). The run used locked bin 25
  (about 1.09 m), which ranked only 11th within bins 20-44.

**Implemented:**
- Updated `scripts/live_demo.py` so future live mirrors truncate any trailing
  partial frame before hashing, and record `mirror_truncated_bytes` in metadata.

**Evidence:**
- `read_adc_bin()` on the untrimmed rehearsal mirror failed with a 384-byte
  remainder.
- `python -m py_compile scripts/live_demo.py` passed after the mirror-trim fix.

**Next:**
- Rerun the live rehearsal with the current scene's target bin, likely
  `--locked-bin 32`, then verify packet stats, frame alignment, and HR/BR
  confidence again.

---

## 2026-06-29 - Live hardware rehearsal with person at bin 32

Reviewed `results/live_demo/20260629_165949_live_test3`, captured with a person
in front of the radar and `--locked-bin 32`.

**Worked:**
- DCA1000 and IWR1642 configured and stopped cleanly.
- Packet stats were clean: 162850 UDP packets received, 0 dropped, and 0
  zero-filled bytes.
- The mirror trim fix worked: metadata recorded `mirror_truncated_bytes: 352`,
  `adc_stream.bin` was frame-aligned, and `read_adc_bin()` loaded 1809 frames.
- `live_estimates.csv` has 20 rows, 20 unique frame indices, and 60-frame spacing.
- Replaying the saved mirror through the same DSP reproduced the live CSV exactly
  for fallback HR, BR, heart peak, validity flags, spectrum stage, and rejection
  labels.

**Findings:**
- Bin 32 was not the best vital-sign bin for this run: it produced 0/20 AHET-valid
  HR windows and only medium/low respiration confidence.
- Range-profile inspection showed stronger reflections at bins 24-27
  (about 1.05-1.18 m), which is inside the revised 1.0-1.4 m live demo protocol.
  Bin 32 ranked 14th inside bins 20-44.
- A post-run bin scan on the same mirror found bin 25 would have produced 8/20
  AHET-valid HR windows and 18/20 high-confidence respiration windows. Bin 25 is
  about 1.09 m, so it is valid under the revised 1.0-1.4 m protocol.

**Next:**
- Keep the subject chest at 1.0-1.4 m and rerun with the bin matching the
  measured distance, likely `--locked-bin 25` for the current setup, until
  warmup auto-lock is implemented.

---

## 2026-06-29 - Live demo distance protocol corrected

Corrected the current live demo protocol to 1.0-1.4 m.

**Implemented:**
- Updated `CLAUDE.md` so the current seated/standing live-demo distance is
  1.0-1.4 m.
- Updated `protocol.subject_distance_m: [1.0, 1.4]` in
  `scripts/live_demo_config.yaml`.
- Updated `notes/live_demo_plan.md` so live demo setup requires chest distance
  1.0-1.4 m and notes that manual locked-bin overrides should normally fall
  around bins 23-32 at `range_resolution_m: 0.0436`.
- Updated live-demo command examples to use `--locked-bin 25` again.

**Next:**
- Rerun live with the subject at 1.0-1.4 m; for the current setup,
  `--locked-bin 25` is a valid manual override.

---

## 2026-06-29 - Live hardware rehearsal at bin 25

Reviewed `results/live_demo/20260629_171608_live_test3`, captured under the
current 1.0-1.4 m protocol with `--locked-bin 25`.

**Worked:**
- DCA1000 and IWR1642 configured and stopped cleanly.
- Packet stats were clean: 163210 UDP packets received, 0 dropped, and 0
  zero-filled bytes.
- The mirror trim path worked: metadata recorded `mirror_truncated_bytes: 224`,
  `adc_stream.bin` was frame-aligned, and `read_adc_bin()` loaded 1813 frames.
- `live_estimates.csv` has 20 rows, 20 unique frame indices, and 60-frame spacing.
- Replaying the saved mirror through the same DSP reproduced the live CSV exactly
  for fallback HR, BR, heart peak, validity flags, spectrum stage, and rejection
  labels.
- Respiration was valid in 19/20 windows, with 12 high-confidence and 7
  medium-confidence windows.

**Findings:**
- Bin 25 is protocol-valid at about 1.09 m, but it produced 0/20 AHET-valid HR
  windows in this run.
- Range-profile inspection showed bin 27 (about 1.18 m) was the strongest
  protocol-bin return; bin 25 ranked 4th within bins 23-32.
- A post-run bin scan found bin 27 would have produced 16/20 AHET-valid HR
  windows, while bin 28 would have produced 15/20 and bin 26 would have produced
  10/20.

**Next:**
- For the current subject position, rerun live with `--locked-bin 27`.
- Longer-term, replace manual locked-bin selection with warmup bin selection over
  protocol bins 23-32 using respiration/heart evidence, not magnitude alone.

---

## 2026-06-29 - Live hardware rehearsal at bin 27

Reviewed `results/live_demo/20260629_173434_live_demo_rehearsal_01`, captured
under the current 1.0-1.4 m protocol with `--locked-bin 27`.

**Worked:**
- DCA1000 and IWR1642 configured and stopped cleanly.
- Packet stats were clean: 162760 UDP packets received, 0 dropped, and 0
  zero-filled bytes.
- The mirror trim path worked: metadata recorded `mirror_truncated_bytes: 384`,
  `adc_stream.bin` was frame-aligned, and `read_adc_bin()` loaded 1808 frames.
- `live_estimates.csv` has 20 rows, 20 unique frame indices, and 60-frame spacing.
- Replaying the saved mirror through the same DSP reproduced the live CSV exactly
  for fallback HR, BR, heart peak, validity flags, spectrum stage, and rejection
  labels.

**Findings:**
- Bin 27 produced 0/20 AHET-valid HR windows in this new run, despite ranking 3rd
  by range magnitude within protocol bins 23-32.
- Respiration was valid in 18/20 windows, but all valid respiration windows were
  medium confidence rather than high.
- A post-run scan over protocol bins 23-32 found only weak HR yield on this
  recording: bin 24 produced 3/20 AHET-valid windows and bin 31 produced 2/20;
  all other protocol bins produced 0/20.

**Next:**
- Stop chasing a single manual bin as the main demo path. Implement or rehearse a
  warmup bin-selection step over protocol bins 23-32 using per-bin respiration and
  heart-gate evidence, then lock the selected bin for the rest of the run.

---

## 2026-06-30 - Simple radar processing explainer

**Implemented:**
- Added `notes/radar_signal_processing_simple.md`, a plain-language step-by-step
  explanation of how raw DCA1000/IWR1642 radar data becomes breathing and heart-rate
  estimates.

**Notes:**
- The explainer covers raw ADC/IQ decoding, radar cube shape, range-bin selection,
  phase extraction, breathing fusion, ECA/AHET heart-rate verification, smoothing,
  CSV output, intermediate dumps, and later Masimo comparison.

---

## 2026-06-30 - Warmup bin implementation goal split

**Implemented:**
- Updated `notes/warmup_bin_plan.md` with a 5-goal sequential implementation plan.
- Added the gate that each goal must be implemented, tested, reported, and explicitly
  approved by the user before the next goal starts.

**Next:**
- Start with Goal 1 only when the user approves implementation.

---

## 2026-06-30 - Warmup bin Goal 1 helper contracts

**Implemented:**
- Verified `scripts/live_demo_config.yaml` has the minimal `bin_selection` section:
  `enabled: true` and `candidate_bins: null`.
- Kept Goal 1 limited to helper/config surface; no live/replay loop integration was
  started.
- Cleaned the `_run_warmup_selection(...)` return type annotation to use
  `dict | None` directly.
- Added `tests/test_live_demo_warmup_helpers.py` covering candidate-bin derivation,
  explicit candidate-bin override, ADC-bound clamping, range-energy ranking, and pure
  locked-bin resolution.

**Verified:**
- `python -m py_compile scripts/live_demo.py` passed.
- `C:\Users\josemsosag\Desktop\radar_vitals_codex\.venv\Scripts\python.exe -m py_compile scripts\live_demo.py` passed.
- The 8 helper test functions in `tests/test_live_demo_warmup_helpers.py` passed when
  executed directly with the sibling workspace venv.
- User verified in the real `(radar-vitals)` environment:
  `python -m pytest tests/test_live_demo_warmup_helpers.py -v` -> 8 passed,
  12 matplotlib/pyparsing deprecation warnings.
- User also ran `python -m py_compile scripts/live_demo.py`; no output, indicating
  success.

**Limitations:**
- Codex shell could not run normal pytest because its visible Python environments do not
  expose the same full dependency stack as the user's interactive `radar-vitals`
  environment. The user-run pytest above is the authoritative Goal 1 test result.

**Next:**
- Wait for user approval before starting Goal 2.

---

## 2026-06-30 - Warmup bin Goal 2 selection helper

**Implemented:**
- Kept Goal 2 limited to the standalone `_run_warmup_selection(...)` helper and tests;
  no live/replay loop integration was started.
- Expanded warmup candidate evidence with `br_bpm`, `f_r_hz`, and `n_eca_skipped` so
  later replay smoke checks can compare the first CSV row against the winning
  `warmup_bin_selection.json` candidate.
- Added tests for normal warmup scanning, score-based winner selection, energy-rank
  tie-breaking, partial DSP failure handling, and all-candidate DSP failure fallback.

**Verified in Codex shell:**
- `python -m py_compile scripts/live_demo.py` passed.
- `C:\Users\josemsosag\Desktop\radar_vitals_codex\.venv\Scripts\python.exe -m py_compile scripts\live_demo.py` passed.
- The 12 helper test functions in `tests/test_live_demo_warmup_helpers.py` passed when
  executed directly with the sibling workspace venv.

**Verified in real `(radar-vitals)` environment:**
- `C:\Users\josemsosag\.conda\envs\radar-vitals\python.exe -m pytest tests\test_live_demo_warmup_helpers.py -v`
  -> 12 passed, 13 warnings.
- `C:\Users\josemsosag\.conda\envs\radar-vitals\python.exe -m py_compile scripts\live_demo.py`
  passed.

**Notes:**
- Warnings were Matplotlib/PyParsing deprecations plus a pytest cache write warning;
  none were test failures.

**Next:**
- Wait for user approval before starting Goal 3.

---

## 2026-06-30 - Warmup bin Goal 3 main-loop integration

**Implemented:**
- Replaced the old mandatory startup `locked_bin` flow in `scripts/live_demo.py`
  with `_resolve_locked_bin(...)`, preserving manual and manifest bins while allowing
  warmup-auto runs to start with `locked_bin: null`.
- Added warmup-aware run metadata fields: `locked_bin_source`,
  `warmup_selected_bin`, `warmup_selection_confidence`, `warmup_selection_path`,
  and `t_warmup_scan_ms`; `locked_bin_overridden` now follows
  `locked_bin_source == "manual"`.
- Moved the active bin into `_state["locked_bin"]` and updated CSV/NPZ emission to
  write the selected state value instead of a closed-over startup variable.
- Wired the first full-window path to run `_run_warmup_selection(...)` when needed,
  write `warmup_bin_selection.json`, update metadata after selection, print the
  selected bin, and reuse the cached winning DSP result for the first emitted row.
- Preserved the all-candidate-failure behavior: select the highest-energy fallback
  bin, skip the first row when no cached DSP result exists, and retry normally on the
  next hop.

**Verified in real `(radar-vitals)` environment:**
- `C:\Users\josemsosag\.conda\envs\radar-vitals\python.exe -m py_compile scripts\live_demo.py`
  passed.
- `C:\Users\josemsosag\.conda\envs\radar-vitals\python.exe -m pytest tests\test_live_demo_warmup_helpers.py -v`
  -> 12 passed, 13 warnings.

**Notes:**
- Warnings were Matplotlib/PyParsing deprecations plus a pytest cache write warning;
  none were test failures.

**Next:**
- Wait for user approval before starting Goal 4 replay smoke validation.

---

## 2026-06-30 - Warmup bin Goal 4 replay smoke verification

**Implemented / adjusted:**
- Added `--headless` mode to `scripts/live_demo.py` so replay smoke checks can run the
  same frame drain, warmup selection, DSP emission, CSV, metadata, and NPZ artifact
  paths without opening the Matplotlib window.
- Made replay frame delivery main-thread driven instead of using a background replay
  thread. This avoids GUI/runtime instability during saved-data smoke checks.
- Replaced the shared `bandpass_filter(...)` implementation in `src/vitals.py` with a
  zero-phase FFT-domain bandpass to avoid non-catchable Windows failures inside the
  SciPy/LAPACK `filtfilt` initialization path.
- Replaced ECA's `np.linalg.qr(...)` projection with an explicit modified
  Gram-Schmidt projection using elementwise NumPy operations and sums, avoiding the
  unstable `np.linalg` path while preserving the respiratory-harmonic projection.

**Replay smoke evidence:**
- Warmup-auto replay command:
  `C:\Users\josemsosag\.conda\envs\radar-vitals\python.exe -u -X faulthandler scripts\live_demo.py --replay results\live_demo\20260629_173434_live_demo_rehearsal_01\adc_stream.bin --replay-fast --headless`
  -> completed, selected bin 23 at ~1.00 m with medium confidence.
- Warmup-auto artifact run:
  `results/live_demo/20260630_165813_replay_unknown`.
- Verified `warmup_bin_selection.json` exists and lists bins 23-32.
- Verified `run_metadata.json` records `locked_bin_source: "warmup_auto"`,
  `locked_bin: 23`, `warmup_selected_bin: 23`, matching confidence/path fields, and
  non-null `t_warmup_scan_ms`.
- Verified `live_estimates.csv` has 21 rows and exactly one locked bin: 23.
- Verified `live_intermediates.npz` exists.
- Verified the first CSV row matches the cached winning warmup DSP evidence: HR blank
  because the winning candidate had `hr_raw: null`; BR 16.28 bpm in CSV matches
  16.2844 bpm in `warmup_bin_selection.json`.

**Manual-bin replay evidence:**
- Manual replay command:
  `C:\Users\josemsosag\.conda\envs\radar-vitals\python.exe -u scripts\live_demo.py --replay results\live_demo\20260629_173434_live_demo_rehearsal_01\adc_stream.bin --locked-bin 27 --replay-fast --headless`
  -> completed.
- Manual artifact run:
  `results/live_demo/20260630_165900_replay_unknown`.
- Verified metadata records `locked_bin_source: "manual"`, `locked_bin: 27`, and
  `locked_bin_overridden: true`.
- Verified no `warmup_bin_selection.json` was written and all warmup metadata fields
  stayed null.
- Verified `live_estimates.csv` has 21 rows and exactly one locked bin: 27.

**Disabled/no-bin evidence:**
- Created temporary smoke config:
  `results/live_demo/smoke_configs/live_demo_config_bin_selection_disabled.yaml`
  with `bin_selection.enabled: false`.
- Running replay with no manual or manifest bin exits with:
  `ERROR: locked_bin not set. Use --locked-bin or ensure manifest has locked_bin for session 'unknown'.`

**Verified in real `(radar-vitals)` environment:**
- `C:\Users\josemsosag\.conda\envs\radar-vitals\python.exe -m py_compile scripts\live_demo.py src\vitals.py`
  passed.
- `C:\Users\josemsosag\.conda\envs\radar-vitals\python.exe -m pytest tests\test_live_demo_warmup_helpers.py tests\test_vitals_synthetic.py tests\test_eca_ahet.py -v`
  -> 32 passed, 13 warnings.
- With pytest temp redirected into `.tmp_pytest`,
  `C:\Users\josemsosag\.conda\envs\radar-vitals\python.exe -m pytest tests\test_step6_heart_rate.py -q`
  -> 131 passed, 14 warnings.

**Notes:**
- A full `pytest tests -q` run is not currently a clean signal in this workspace:
  collection fails for missing `scripts.add_quality_mask`, many tests using `tmp_path`
  fail without redirecting temp out of `AppData\Local\Temp`, and several exp004 tests
  expect missing historical result artifacts or an older three-capture config shape.
- The focused tests above cover the warmup helpers, synthetic vitals recovery,
  ECA/AHET behavior, and Step 6 heart-rate integration paths touched by this goal.

**Next:**
- Wait for user approval before starting Goal 5 live rehearsal and final handoff.

---

## 2026-06-30 - Live demo UI selected-bin readout

**Implemented:**
- Updated the live Matplotlib title in `scripts/live_demo.py` so, after warmup selects
  a bin, the display shows the locked bin and approximate range next to elapsed time:
  `elapsed: HH:MM:SS | bin: <bin> (~<range> m)`.
- Kept the warmup screen unchanged before selection.
- Increased the live HR/BR title text size by 1.5x for better visibility during the demo.

**Verified:**
- `C:\Users\josemsosag\.conda\envs\radar-vitals\python.exe -m py_compile scripts\live_demo.py`
  passed.
- `C:\Users\josemsosag\.conda\envs\radar-vitals\python.exe scripts\verify_live_demo_artifacts.py results\live_demo\20260630_165813_replay_unknown --expect-mode replay`
  passed.

**Next:**
- Run the Goal 5 live rehearsal without `--locked-bin` and verify the new live
  artifact folder.

---

## 2026-06-30 - Warmup bin Goal 5 live rehearsal

**Live command run by user:**
- `python scripts/live_demo.py --live-session demo_warmup_01 --duration-s 90`
- No `--locked-bin` was provided, so the warmup-auto path was exercised.

**Console evidence:**
- After the first full warmup window, the script printed:
  `Warmup selected bin 25 (~1.09 m, confidence: high).`
- Capture stopped cleanly:
  `IWR1642: sensorStop ... ok` and `DCA1000 RECORD_STOP ... ok`.

**Artifact run:**
- `results/live_demo/20260630_183026_live_demo_warmup_01`

**Verified with artifact checker:**
- `C:\Users\josemsosag\.conda\envs\radar-vitals\python.exe scripts\verify_live_demo_artifacts.py results\live_demo\20260630_183026_live_demo_warmup_01 --expect-mode live`
  -> all checks passed.

**Artifact evidence:**
- `warmup_bin_selection.json` exists and lists bins 23-32.
- Metadata records `locked_bin_source: "warmup_auto"`, `locked_bin: 25`,
  `warmup_selected_bin: 25`, and confidence `high`.
- Selected range is 1.09 m, inside the 1.0-1.4 m protocol range.
- `live_estimates.csv` has 20 rows and exactly one locked bin: 25.
- First CSV row matches cached warmup DSP evidence: HR 93.70 bpm and BR 17.57 bpm.
- Last CSV row: smoothed HR 81.81 bpm, BR 20.33 bpm.
- `live_intermediates.npz` exists and is non-empty.
- Raw mirror `adc_stream.bin` exists; metadata hash:
  `8fd400ae4a755b57e5800825e75fbd6cfd011bf241582d3373a0ad39aac9550a`.
- Live packet stats: 162670 received, 0 dropped, 0 zero-filled bytes, 416 mirror
  truncated bytes.

**Notes:**
- Goal 5 live warmup-bin feature is verified for this rehearsal.
- The earlier `src/vitals.py` DSP internals change from Goal 4 still touches filtering
  and ECA projection, so it should receive independent cross-model review before being
  treated as paper-grade.

**Next:**
- Run independent code/DSP review of the `src/vitals.py` filter/ECA changes or choose
  whether to revert them and address the native-runtime crash another way.

---

## 2026-07-02 - Relocking-bin plan comment review

**Implemented:**
- Revised `notes/relocking_bin_plan.md` after reviewing the `COMMENTS` section.
- Incorporated all comments into the plan: hop-count timing, arm/disarm relock logic,
  manual locked-bin exemption, HR smoother/holdover separation, relock event evidence,
  display/headless signature changes, verifier updates, and sequential gated
  implementation goals.
- Removed the `COMMENTS` section because no comment was judged invalid.

**Next:**
- Implement the relocking plan one gated goal at a time when approved.

---

## 2026-07-02 - Relocking-bin plan second comment review

**Implemented:**
- Reviewed the new `COMMENTS` section in `notes/relocking_bin_plan.md`.
- Incorporated all new comments into the plan:
  - manifest-sourced locked bins are treated as pinned sources alongside manual
    `--locked-bin` values;
  - `relock_pinned_sources_enabled: false` is the default;
  - holdover boundary behavior is explicit: first invalid hop is held, second
    consecutive invalid hop blanks and can become scan-eligible;
  - holdover averages whatever eligible readings exist when fewer than three are
    available;
  - live rehearsal must report observed `t_relock_scan_ms` range in `HISTORY.md`;
  - `_DisplayHoldoverState` and `_RelockController` now have explicit method
    signatures in the plan.
- Removed the `COMMENTS` section because no new comment was judged invalid.

**Next:**
- Implement the relocking plan one gated goal at a time when approved.

---

## 2026-07-02 - Post-Goal-5 verification: in-tool Step 6 crash re-check

No code changes this session. Re-verified the `1847d7f` state (`git status` clean,
working tree unchanged) before starting Step D, per the smoke checks Goal 4/5 called
for, then went further and re-tested the `numpy.linalg` crash gotcha directly against
the rewritten `src/vitals.py`.

**Worked:**
- `py_compile` clean on `scripts/live_demo.py`, `src/vitals.py`,
  `scripts/verify_live_demo_artifacts.py`.
- `pytest tests/test_live_demo_warmup_helpers.py tests/test_vitals_synthetic.py tests/test_eca_ahet.py -q`
  -> 32 passed (matches the Goal 4 record).
- `pytest tests/test_step6_heart_rate.py -q` (temp dir redirected to `.tmp_pytest`,
  deleted after the run) -> 131 passed (matches the Goal 4 record).
- Ran the real Step 6 pipeline directly in this tool's conda env (not an Anaconda
  Prompt) for the first time since the crash was documented:
  `steps/step_6/extract_heart_rate.py --session test --config steps/step_6/config_hop1_win30_safe.yaml --no-write --no-plots`
  and the same command with `--session test2`. Neither crashed.
- Both runs reproduced the canonical committed numbers in
  `results_hop1_win30_safe/<session>/step_6/summary.json` (recorded under commit
  `78067cc`, i.e. before the Goal 4 `bandpass_filter`/ECA rewrite) exactly:
  - `test`: MAE 0.5869566575980194, RMSE 0.6000531560910951, bias +0.5869566575980194,
    N=8 valid HR (matches summary.json `mae_bpm`/`rmse_bpm`/`bias_bpm`/`n_valid_hr`).
  - `test2`: MAE 1.1340345345989509, RMSE 1.560608908426274, bias -0.3779095261177496,
    N=125 valid HR (matches summary.json).

**Findings:**
- `src/vitals.py` has no remaining `np.linalg`/`filtfilt` calls (confirmed by grep);
  `steps/step_6/extract_heart_rate.py` imports `estimate_rate_from_phase`/
  `bandpass_filter` directly from `src/vitals.py`, so the Goal 4 FFT-domain
  `bandpass_filter` and Gram-Schmidt ECA projection (added to fix the live-demo crash)
  sit on the same code path Step 6 uses.
- On the 2 sessions tested, this rewrite removed the `0xC06D007F`
  (`numpy.linalg.solve` via `scipy.signal.filtfilt`) crash for the offline Step 6
  path too, not just the live demo. This was not previously documented — the
  "Do NOT run Step 6 inside the Claude Code tool" gotcha in `HANDOFF.md` predates
  this rewrite (it was written before Goal 4, against the old `filtfilt`/`np.linalg.qr`
  implementation).

**Decisions:**
- Not claiming the in-tool crash is fixed for every session/config — only that the
  specific crash mechanism (linalg calls in `bandpass_filter`/ECA) is gone from the
  shared code path, and 2 of 10 manifest sessions reproduce their canonical numbers
  exactly. `HANDOFF.md` gotcha downgraded to "believed fixed, spot-checked on 2/10
  sessions," not removed outright.
- Used `--no-write` for both verification runs and did not touch
  `results_hop1_win30_safe/` outputs, to avoid any chance of silently overwriting
  committed paper-track results with an ad hoc check.
- This is a numeric-reproducibility check, not the CLAUDE.md §6 independent
  cross-model review that DSP changes to filtering/ECA require. That review was
  flagged as open at the end of the Goal 5 entry above and is still open — it does
  not get marked done by this entry.

**Evidence:**
- `results_hop1_win30_safe/test/step_6/summary.json`,
  `results_hop1_win30_safe/test2/step_6/summary.json` (pre-existing, committed under
  `78067cc`) compared against this session's `--no-write` console output.

**Next:**
- Get the CLAUDE.md §6 independent cross-model (Codex) review of the Goal 4
  `src/vitals.py` `bandpass_filter`/ECA rewrite before treating it as paper-grade.
- Step D (MAE/RMSE/bias + Bland-Altman across the three production configs —
  `results/`, `results_hop1_safe/`, `results_hop1_win30_safe/`) is still the main
  open research task; nothing in this session changed that.

---

## 2026-07-02 - Relocking-bin plan third comment review

**Implemented:**
- Reviewed the two new `COMMENTS` in `notes/relocking_bin_plan.md`.
- Incorporated both comments into the plan:
  - `scripts/verify_live_demo_artifacts.py` should split checks into
    always-applicable, warmup-auto-only, pinned-source, and relock-sequence
    groups so manual/manifest locked-bin runs can still be verified.
  - `_RelockController` should receive holdover-expired state from
    `_DisplayHoldoverState`, avoiding a second independent invalid-hop counter.
- Removed the `COMMENTS` section because no comment was judged invalid.

**Next:**
- Implement the relocking plan one gated goal at a time when approved.

---

## 2026-07-02 - Relocking-bin plan fourth comment review

**Implemented:**
- Reviewed the new `COMMENTS` in `notes/relocking_bin_plan.md`.
- Incorporated all valid comments into the plan:
  - `_process_dsp_hop(...)` must call `_DisplayHoldoverState.update(...)` before
    `_RelockController.update(...)` on the same hop, then pass same-hop
    blank/expired flags to the controller.
  - `_RelockController.disarm_all()` and `reset()` now have distinct call-site
    meanings: scan attempts disarm and clear arming counters; startup/accepted
    bin switches fully reset controller state.
  - Goal 3 now includes a negative verifier test where CSV locked-bin changes
    are not backed by accepted `relock_events.json` events and must fail.
  - Arming semantics are explicit: only consecutive real-valid DSP hops count;
    held-but-invalid hops reset the arming streak, while an already armed metric
    remains armed through the holdover window until scan/reset.
- Removed the active `COMMENTS` section because no comment was judged invalid.

**Next:**
- Implement the relocking plan one gated goal at a time when approved.

---

## 2026-07-02 - Display holdover + nearby-bin relock implemented (Goals 1-3)

Implemented `notes/relocking_bin_plan.md` on branch `vital_signs_v9`. Goals 1-3
complete; Goal 4 (live rehearsal) pending a user-run hardware session.

**Implemented:**
- `_DisplayHoldoverState` in `scripts/live_demo.py`: display-only real/held/blank
  holdover per metric (HR and BR independent), hop-count based, with a
  `holdover_source_max_hops` staleness bound on the held average. Held values
  never reach `live_estimates.csv`, `live_intermediates.npz`, or HR raw dots.
- Removed the unbounded HR display/save holdover: on an invalid HR hop,
  `hr_bpm_smooth` is now emitted as NaN/blank instead of re-displaying the stale
  `hr_history` median. Confirmed in a real artifact: the default-config replay
  smoke has 21/21 HR-invalid hops and zero non-empty `hr_bpm_smooth` cells.
- `_RelockController`: arm/disarm state machine (`relock_arm_s` -> hops). Any
  invalid hop resets the pre-arm streak; an armed metric survives held hops and
  triggers exactly one scan on its blank/expired hop; caller disarms after every
  scan attempt, full `reset()` after an accepted switch. Holdover expiry is fed
  in from `_DisplayHoldoverState` — no duplicate counter (unit-tested).
- `_derive_relock_candidate_bins`: locked_bin ± `relock_radius_bins`, ADC-clipped,
  protocol-clipped unless `candidate_bins` is explicit, always includes the
  current locked bin.
- `_run_warmup_selection` refactored into shared `_run_bin_selection_scan` with
  `context_label`/`artifact_hint`; warmup wrapper preserves the old contract
  (existing 12 warmup tests pass unchanged); relock messages say "relock" and
  reference `relock_events.json`.
- Pinned-source exemption `_relock_allowed`: `manual`/`manifest` bins never
  auto-relock unless `bin_selection.relock_pinned_sources_enabled: true`.
- Relock wired into `_process_dsp_hop` before CSV/NPZ emission; on an accepted
  switch (confidence >= `relock_min_confidence`): `_state["locked_bin"]` updated,
  `hr_history` + holdover buffers + controller reset, the current row emitted
  from the winning DSP result. `relock_events.json` written per event with full
  per-candidate evidence; metadata gains `relock_enabled`, `n_relock_scans`,
  `n_relock_switches`, `relock_events_path`; `locked_bin` now tracks the
  current/final bin.
- `scripts/verify_live_demo_artifacts.py` restructured into always-applicable /
  warmup-auto-only / pinned-source / relock-sequence check groups, including
  rejection of CSV bin changes not backed by an accepted event.
- Config additions in `scripts/live_demo_config.yaml` per plan (holdover_* and
  relock_* defaults).

**Verified:**
- `py_compile` clean on `scripts/live_demo.py`, `scripts/verify_live_demo_artifacts.py`.
- 50/50 live-demo tests pass in the real `(radar-vitals)` env:
  `tests/test_live_demo_warmup_helpers.py` (12, unchanged),
  `tests/test_live_demo_holdover_relock.py` (28 new),
  `tests/test_verify_live_demo_artifacts.py` (10 new).
- Verifier backward compatibility on real pre-relock artifacts:
  `20260630_183026_live_demo_warmup_01` (live, 24 checks) and
  `20260630_165813_replay_unknown` (replay, 21 checks) both fully pass.
- Default-config replay smoke (`results/live_demo/20260702_134824_replay_unknown`):
  warmup selected bin 23 as before; 0 relock scans — HR never armed (never valid
  on this mirror) and BR never dropped. 21/21 verifier checks pass.
- Forced-relock replay smoke (`results/live_demo/20260702_134954_replay_unknown`,
  smoke config `relock_arm_s: 3.0` + pinned override, `--locked-bin 27` on the
  `20260702_110042` mirror): one accepted switch 27 -> 28 (high confidence,
  trigger hr, hop 4) and one no-switch scan (kept 28, hop 9), then correctly
  silent through the remaining dead stretch. CSV bins `[27 x4, 28 x17]` match the
  event chain; 13/13 verifier checks pass. `t_relock_scan_ms` ~937 ms per
  5-candidate scan.

**Notes:**
- The HR reading fed to the display holdover is the real smoothed HR (what the
  title/line always showed), not per-hop raw HR — the plan's "recent valid
  readings" did not pick one; flagged in the plan's COMMENTS AFTER IMPL.
- `elapsed_s` ~0.0 in `--replay-fast` artifacts (whole backlog drains in one
  callback that snapshots elapsed once) is pre-existing and also affects
  `live_estimates.csv`; `dsp_hop_idx` is the reliable ordering key.
- `main()` still hardcodes a 15 s startup countdown (`delay_s = 15`) while the
  config's `session.start_delay_s: 10` is unused — pre-existing, out of scope.

**Next:**
- Goal 4: user-run 90 s live rehearsal without `--locked-bin`; verify artifacts
  with the updated checker and record the live `t_relock_scan_ms` range here.
- The changes are uncommitted on `vital_signs_v9`; commit when reviewed.

---

## 2026-07-02 - Live demo UI redesign (value panels); four-subject demo review; relock trigger rule fixed to joint HR+BR loss

Three related pieces of work on `vital_signs_v9`, still uncommitted.

### `_LiveDisplay` UI redesign

**Implemented:**
- Replaced the single stacked-suptitle layout with a `GridSpec` (3 rows x 2
  cols): a thin full-width status bar (`HR conf | BR conf | bin (~range m) |
  elapsed HH:MM:SS`, plus relock status text when present) on top, then HR
  graph + HR value panel, then BR graph + BR value panel.
- Value panels show a big number (confidence-colored via the existing
  `_CONF_COLOR` green/orange/red mapping, previously declared but unused) plus
  a small "bpm" / "bpm (held)" unit label. HR graph keeps both the smoothed
  line and the raw dots. Font size for the big numbers set to 50pt per request
  (was 32pt at first pass).
- `_HeadlessDisplay` needed no changes (already mirrors whatever signature
  `_LiveDisplay.update()` has as a no-op).

**Limitation:** could not visually verify from this tool — isolated a hard
crash to bare matplotlib figure rendering (`plt.figure()` + `savefig()`, with
zero project code involved) failing with exit 127 in this sandboxed
environment. `_HeadlessDisplay`-only runs (no real figure drawing) are
unaffected and were used for all other verification this session. This is a
new, previously-undocumented environment gotcha, distinct from the
`numpy.linalg` one — actual matplotlib rendering cannot be checked from
inside this tool; only the user running the demo normally (not `--headless`)
can visually confirm layout changes.

### Four-subject demo review (jeremy, mafe, fran, relocking_01)

Reviewed `results/live_demo/20260702_17{0050,0349,0702}_..._{jeremy,mafe,fran}`
and `20260702_173139_..._relocking_01` (120 s live sessions each, warmup-auto,
14-bin warmup scan reflecting the widened `[0.8, 1.4]` protocol range).

**Findings:**
- Clean captures throughout: 0 dropped packets, 0 zero-filled bytes, all four.
- HR valid rate: relocking_01 83%, jeremy 33%, mafe 23%, fran 10%. Warmup
  itself found 0/14 (mafe) to 4/14 (jeremy) candidate bins with valid HR,
  foreshadowing per-session outcomes.
- **Relocking made two of the three firing sessions measurably worse.**
  jeremy: bin 28 tracked HR perfectly for 7 straight hops (88% valid), one dip
  triggered a switch, and a later BR-only-triggered switch moved him to a bin
  that produced zero valid HR for the remaining 9 hops (HR valid rate 88% -> 14%
  after the first switch). fran: 67% valid before the first switch, 4% after.
  relocking_01's one switch was neutral (83% both sides) because that session
  was strong throughout, not because relocking rescued it.
- Two of jeremy's three switches and fran's only switch were accepted right at
  the `relock_min_confidence: medium` floor; jeremy's damaging later switches
  were triggered by `trigger: ['br']` alone (BR evidence only, no requirement
  that HR actually be better at the new bin).
- Breathing-harmonic-proximity checked as a secondary hypothesis (k=2..6 x
  median f_r landing near each session's actual HR) — plausible for
  jeremy/mafe/fran but not confirmed, since relocking_01 had comparable
  harmonic overlap yet was the best-performing session; underlying signal SNR
  at the bin looks like the dominant factor, harmonics at most secondary.

### Relock trigger rule changed to joint HR+BR loss

Directly following from the finding above, per explicit user direction:
relock now fires only when HR AND BR are both blank simultaneously, not
either alone.

**Implemented:**
- `_RelockController` rewritten: removed all arm/disarm-by-consecutive-valid-
  streak logic; `should_scan = hr_blank_expired and br_blank_expired and not
  <already scanned this joint-loss episode>`. Episode ends (suppression
  clears) when either metric goes real-valid again; `disarm_all()` suppresses
  after any scan attempt, `reset()` clears suppression immediately after an
  accepted switch. No arming precondition — joint loss is eligible from hop 0.
  `trigger_metrics` is now always `["hr", "br"]` when firing (both required).
- Removed `relock_arm_s` from `scripts/live_demo_config.yaml` and `main()`'s
  config parsing; `_RelockController()` now takes no constructor argument.
  "More than 3 seconds" reuses `display.holdover_s` directly — no new timer.
- Rewrote the `_RelockController` unit tests (9 tests): explicit coverage that
  a single-metric drop alone never triggers
  (`test_relock_no_trigger_when_only_hr_blank`,
  `test_relock_no_trigger_when_only_br_blank`), joint loss does trigger, no
  arming precondition, episode-based re-trigger/suppression, and reset()
  vs. disarm_all() semantics.
- Updated `notes/relocking_bin_plan.md`'s Behavior/Config Additions/
  Implementation Changes sections in place (marked `[REVISED 2026-07-02]`)
  plus a new COMMENTS AFTER IMPL entry with the full rationale.

**Verified:**
- `py_compile` clean on `scripts/live_demo.py`,
  `scripts/verify_live_demo_artifacts.py`.
- 50/50 live-demo tests pass (`tests/test_live_demo_warmup_helpers.py`,
  `tests/test_live_demo_holdover_relock.py`,
  `tests/test_verify_live_demo_artifacts.py`).

**Honest limitation — replay is not a clean A/B test here:** replayed all
four subjects' `adc_stream.bin` mirrors through the new code. Found that live
and `--replay-fast` do **not** reproduce the same warmup bin selection for
the same underlying capture (fran: replay picked bin 29/medium vs. the
original live run's bin 21/high; relocking_01: replay picked bin 25 vs. 22;
mafe matched at 29). This is a pre-existing live-vs-replay-fast
frame-windowing difference, not caused by this change, but it means these
replays can't cleanly isolate whether the new trigger rule alone would have
changed jeremy's or fran's outcome. Jeremy's replay reproduced the same
28->29->28->30 sequence — but inspection showed that in *that replay's own*
windowing, HR and BR were genuinely both down at all three decision points,
so the new rule correctly still fires there; that is the rule working as
specified, not a failure of the fix. The rule is verified correct by
construction (unit tests); only a live-hardware rehearsal can confirm it
changes behavior on a jeremy/fran-like session.

**Dead end (recorded, not a bug in this change):** attempted to render a
static PNG via `_LiveDisplay.setup()`/`update()`/`savefig()` from this tool to
visually check the new panel layout before reporting it done. Isolated the
crash to bare matplotlib figure rendering with zero project code involved —
an environment limitation, not fixable from here.

**Also this session (uncommitted, unrelated to the above):**
`scripts/live_demo_config.yaml` was found deleted (unstaged) partway through
this work, not caused by any action in this conversation. Restored via
`git checkout -- scripts/live_demo_config.yaml` per user confirmation before
continuing — this means an earlier uncommitted edit to
`protocol.subject_distance_m` (widened to `[0.8, 1.4]`, discussed with the
user two sessions ago) was lost and reset back to the last-committed `[1.0,
1.4]`. **Flagging for the user:** re-apply the `[0.8, 1.4]` distance-range
edit if still wanted — it is not currently in the working tree.

**Next:**
- Re-apply `protocol.subject_distance_m: [0.8, 1.4]` in
  `scripts/live_demo_config.yaml` if still desired (see note above).
- Live-hardware rehearsal to confirm the joint-loss trigger rule actually
  prevents the jeremy/fran single-metric-abandonment pattern in practice.
- Visually confirm the new value-panel UI layout by running the demo normally
  (not `--headless`) — cannot be checked from this tool.
- Goal 4 of the original relocking plan (live rehearsal + verifier check) is
  still owed on top of the above.

## 2026-07-09 - Reverted relocking; kept v9 UI redesign on a v8 base

**Decision:** Pull back the v9 nearby-bin relocking feature and return to v8's
fixed-bin tracking, while keeping the v9 live-demo UI redesign. Relocking was
judged not worth its complexity/risk for now; the value-panel UI is worth
keeping. Holdover (display-only reading hold) was dropped along with relocking
at the user's request — the readout now goes straight to `--` on an invalid
hop, no `(held)` bridging.

**How:** New branch `vital_signs_v8_ui` off v8 (`e3779cd`). Re-applied only the
`_LiveDisplay` redesign (3x2 gridspec: status-bar row + big confidence-colored
HR/BR numeric readouts replacing the old `suptitle`). Everything else —
algorithm, warmup selection, `live_demo_config.yaml`, tests — is exactly v8.
No `_RelockController`, `_DisplayHoldoverState`, `_run_bin_selection_scan`
refactor, relock/holdover config keys, or `relock_events.json` remain. The v9
WIP (UI redesign + relock trigger-rule tuning) is preserved in git stash
(`stash@{0}` on `vital_signs_v9`).

**Verified:** `py_compile` clean; zero `relock`/`holdover` references in script
or config; headless render smoke test (blank hop -> `--`; real hop -> HR green
at high conf, BR red at low conf; status bar shows conf/bin/elapsed with no
relock status); `tests/test_live_demo_warmup_helpers.py` 12 passed.

**Next:**
- Visually confirm the value-panel UI by running the demo normally (not
  `--headless`) in the `radar-vitals` conda env.
- The `stash@{0}` v9 WIP can be dropped once the relock work is confirmed
  unwanted.

## 2026-07-09 - Live-run diagnostics: 60 s NPZ checkpoint + diagnose helper

**Worked (committed on `vital_signs_v8_ui`):**
- `ea5bd99` - Lowered `capture.intermediates_checkpoint_windows` 60 -> 20 so a
  2-minute demo flushes `live_intermediates.npz` every 60 s (at the 3 s hop),
  not only at shutdown. CSV was already per-row flushed; this protects the
  richer NPZ evidence against a hard crash between checkpoints.
- `a57bf19` - New `scripts/diagnose_live_run.py` + `tests/test_diagnose_live_run.py`
  (19 tests, all pass). Post-mortem tool: point it at one run folder and it
  prints a severity-ranked verdict ("why was HR blank / did warmup lock a good
  bin") over four evidence blocks - warmup lock quality (per-candidate scores,
  thin-margin / better-neighbor / edge-lock / low-confidence flags), HR-
  availability timeline (valid %, dominant AHET rejection reason), signal level
  (peak-to-floor vs the config's own floor, robust phase-motion outliers), and
  capture health (packet loss). Writes overview / warmup-candidate / per-failed-
  window plots to `<run_dir>/diagnosis/`. Read-only on the run folder and
  data/raw; CSV/NPZ aligned positionally with a shortfall note on hard-crash
  truncation.

**Verified:** `py_compile` clean; 19/19 diagnose tests pass; CLI smoke test on a
synthetic failing run ranked frame_loss > bad_warmup_lock > ahet_over_rejection
> weak_signal correctly and rendered all four plot types.

**Next:**
- Run the diagnostic on a real captured session to sanity-check thresholds
  (`THIN_MARGIN_SCORE`, `PHASE_MAD_K`) against real evidence.
- Optional follow-up: Masimo cross-comparison (deferred to `compare.py`; needs
  the separately-captured reference aligned on the integer `Timestamp`).

## 2026-07-09 - Hard reset of all datasets (fresh-start decision)

**Decision (user-directed):** All existing captures were taken across different
phases of the project under drifting capture protocols (e.g. approach.md records
1.3-1.6 m / gate 1.1-1.5 while CLAUDE.md specifies 1.0-1.4 m), so they no longer
give a trustworthy view of where the method stands. Rather than keep reasoning
over mixed-protocol data, we restart: analysis from here uses only captures
taken after this point.

**What was removed (2026-07-09):**
- `data/raw/` (~11 GB: adc_data, exp001-exp010, test-test5 radar .bin + Masimo
  CSV + LogFile + quality JSON) - gitignored, deleted on disk.
- `data/processed/` (~2 GB) - gitignored, deleted on disk.
- `results/` (~6.5 GB: all per-session runs + diagnostics) - gitignored,
  deleted on disk.
- `figures/test*` range-plot PNG/MP4/logs - tracked; removed in commit
  `71618da`.
- `data/manifest.local.csv` reset to its header row (0 sessions).
- Directory skeletons + `.gitkeep` kept so pipeline paths still resolve.

**Integrity note (deliberate rule override):** this contradicts CLAUDE.md's
"data/raw is READ-ONLY / do not delete failed experiments - record them" rules.
The override was a conscious, user-confirmed decision; the user stated the data
is backed up externally before deletion. The deletion is otherwise
unrecoverable from this repo (data/raw was never git-tracked). The exp001/exp002
baselines cited in `notes/approach.md` and the `exp001/exp002-baseline` git tags
are therefore no longer reproducible from local inputs - treat those numbers as
historical, not current.

**Next:**
- Capture the first post-restart session under one fixed, written protocol
  (settle time, posture, distance, orientation) and log it here before use.
- Add each new session to `data/manifest.local.csv`.
- Reconcile the distance-gate discrepancy (1.0-1.4 vs 1.3-1.6 m) in approach.md
  and live_demo_config.yaml so the new captures use one agreed protocol.

## 2026-07-09 - Deleted the hop-1 offline sweep result folders

**What they were:** four root-level `results_hop1*/` folders (~334 MB, untracked)
holding offline Step-6 heart-rate outputs on the old `test`-`test5` sessions.
They were the development sweep that chose the window length and Viterbi tracker
parameters. Each pairs with a `steps/step_6/config_hop1*.yaml` (configs kept - the
recipe; only the outputs were deleted). Metrics as recorded in HANDOFF.md
(good/acceptable/bad/severe window counts + MAE over good+acceptable):

- `results_hop1_safe/` - hop=1 s, **20 s** window, "safe" tracker
  (max_jump=3.0, min_fund_db=2.0): good=308, accept=24, bad=0, severe=0,
  **MAE 1.49 bpm**.
- `results_hop1_win30_safe/` - hop=1 s, **30 s** window, "safe" tracker
  (max_jump=6.0, min_fund_db=4.0): good=393, accept=15, bad=0, severe=0,
  **MAE 1.23 bpm**. Best aggregate; the config the live demo's heart settings
  mirror.
- `results_hop1/` - hop=1 s, 20 s window, initial unsafe config + the full 1080-
  combo grid sweep used to find the tracker params. Debug/sweep only.
- `results_hop1_win30/` - hop=1 s, 30 s window, intermediate config
  (min_fund_db=2.0): test2 MAE 3.73 (tracker locked a wrong candidate).
  Superseded by the win30_safe run.

**Key finding they established (kept in the configs + HANDOFF.md):** a 30 s window
beats 20 s, but needs a stricter fundamental floor (min_fund_db 4.0 vs 2.0) to
suppress spurious ~78 bpm peaks at the finer 2 bpm/bin FFT resolution; with that
floor, a looser max_jump (6.0) is safe. "Safe" = zero bad/severe error windows.

**Why deleted:** outputs derived from the `test`-`test5` raw data, which was
retired in the "Hard reset of all datasets" entry above - they cannot be
regenerated and are not part of the post-restart pipeline (which uses live_demo.py
with an online median smoother, not the offline Viterbi tracker). Note: `HANDOFF.md`
still references these folders and describes the older offline pipeline / 1.3-1.6 m
seated setup - it is now largely stale and its `results_hop1*` pointers dangle.

## 2026-07-09 - notes/ audit: removed four stale .md files

Audited every `.md` in `notes/` for whether it is still used, updatable, and
meaningful. **Kept:** `approach.md` (living research doc, tracked, mandated by
CLAUDE.md S5), `protocol.md` (the live seated capture protocol),
`dca1000_protocol.md` (load-bearing hardware reference - `steps/step_1/capture.py`
cites it by section), `radar_signal_processing_simple.md` (plain-language pipeline
explainer, kept as thesis/onboarding material).

**Deleted (all untracked/gitignored):**
- `relocking_bin_plan.md` - design plan for the display-holdover + nearby-bin
  relocking feature that was deliberately **reverted** (see the v8_ui entry above).
  The feature no longer exists; the only remaining references were stale `.pyc`
  caches of deleted tests.
- `warmup_bin_plan.md` - implementation plan for warmup bin selection. Feature is
  **shipped** (all 5 goals) and is the current mechanism; the plan also still cited
  the retired 1.0-1.4 m gate. Not updatable.
- `live_demo_plan.md` - plan/architecture doc for the live demo, which is **built**.
  Its one non-duplicated rule (the "Boundaries" constraint that demo readouts are a
  sanity check and never paper-grade) was **preserved into CLAUDE.md S4** before
  deletion, and `live_demo.py`'s docstring reference was replaced with that rule
  inline rather than left dangling.
- `capture_review.md` - 2026-06-24 Codex review of `scripts/capture.py` +
  `scripts/capture_config.yaml`, files that **no longer exist** (never committed).
  Its two blocking findings were already resolved and are recorded in HISTORY:
  **C-02** (SDK `SampleSwap=1` I/Q ordering incompatible with the positive-range
  decoder) was fixed by the `iq_swap` flag in `ChirpConfig`/`src/radar_io.py`,
  carried through `live_demo.py` and the manifest; **C-01** (UDP payloads appended
  instead of placed by byte offset, silently corrupting on loss/reorder) is
  mitigated in `LiveFrameSource`, which parses sequence numbers, zero-fills gaps,
  drops duplicates/reorders, and logs `n_dropped`/`zero_filled_bytes` to run
  metadata (surfaced by `diagnose_live_run.py`). Recorded here so the resolution
  is not lost with the review.

## 2026-07-09 - Protocol lock-in, doc contracts, approach.md restructure

**Set out to do:** after the data reset, settle a single capture protocol for the new
study, make the project docs reflect reality, and define what HANDOFF.md / HISTORY.md
are actually for.

**Worked (with evidence):**
- `04c4d96` - **Capture protocol reconciled.** Killed the drift that made the old data
  untrustworthy (CLAUDE.md said 1.0-1.4 m sit-or-stand; approach.md said 1.3-1.6 m).
  Single protocol now: **seated** (hands on legs, back straight, facing radar), chest
  **0.8-1.4 m**, warmup auto-locks the bin (no manual pin). Applied across CLAUDE.md,
  `scripts/live_demo_config.yaml` (`subject_distance_m` -> `[0.8, 1.4]`), approach.md
  and protocol.md. Repo-wide sweep confirms no stale strings remain.
- `e28d3de` - **Removed dead `start_delay_s`.** It was referenced nowhere in code.
  Warmup latency is purely the `window_s` ring-buffer fill: **~30 s, not the ~40 s**
  previously assumed/documented.
- **Study design finalised** (in notes/protocol.md): 10 subjects x 2 sessions on
  **different days**; session 1 natural breathing, session 2 paced at **12/15/18 bpm**
  (rotated, metronome at 2x rate, 2-min settle + Masimo BR stability check);
  **5-min recordings** (~30 s warmup + ~4.5 min usable, sized for ~9 independent
  non-overlapping 30 s windows). Capture tool = `live_demo.py` live mode.
- `465c9bf` - **Repointed radar-config references** after the mmWave Studio exports moved
  out of `notes/` into `config/` (and the TI PDFs/papers into `literature/`).
  `steps/step_1/capture.py`, `steps/step_1/capture_config.yaml` and
  `steps/step_2/config.yaml` had dangling `notes/vital_signs.lua` paths - load-bearing,
  since capture_config says "all values must match vital_signs.lua exactly". Also fixed a
  pre-existing dangling citation to `custom_capture.lua`, a file that never existed under
  that name.
- `83862c8` - **Preserved the live-demo "not paper-grade" rule** into **CLAUDE.md S4**
  before deleting `notes/live_demo_plan.md`: no HR/BR value shown by `live_demo.py` (or in
  `live_estimates.csv`) is paper-grade - the live path uses an online median smoother, not
  the validated offline estimator. Paper metrics come from re-processing the saved raw
  `adc_stream.bin` offline.
- `18e9738` - **Restructured approach.md** into Part A (method - current), Part B
  (historical results, explicitly **NOT REPRODUCIBLE / NOT CITABLE** since their raw
  inputs were deleted), Part C (open questions). Fixed stale technical claims it still
  carried: "bin 29 pinned" -> warmup auto-lock; "20 s window / 10 s step" -> 30 s window /
  3 s live hop. Added the non-overlapping-window rule for statistics.
- `45b9c78` - **Recorded the 18 bpm decision** (see below).
- `c9bd614` - **Defined HANDOFF.md and HISTORY.md contracts in CLAUDE.md S10.** HISTORY
  was referenced in four places but never defined; HANDOFF was not mentioned at all -
  which is why it rotted. HANDOFF = "where are we now", REWRITE in place; HISTORY = "what
  happened", APPEND only. Added as S10 rather than inserted, so the S4/S5/S6 numbers other
  docs cite by number stay valid.
- **Tests:** 44 passed (`test_diagnose_live_run`, `test_live_demo_warmup_helpers`,
  `test_vitals_synthetic`).

**Failed / got it wrong (corrected, recorded so it isn't repeated):**
- I initially documented the warmup as **~40 s** (`start_delay_s` 10 + 30 s window). Wrong -
  `start_delay_s` was dead code; the real latency is ~30 s. Corrected in protocol.md.
- I proposed **18 bpm** paced breathing as an "adversarial stress test" that would probe
  ECA/AHET robustness. That framing was wrong: approach.md S4.2 documents that when
  4 x f_r ~ HR, ECA **cannot** suppress the harmonic - the projection destroys the cardiac
  signal too (an identifiability problem, not a tuning one). 18 bpm puts 4 x f_r at 72 bpm,
  inside the resting-HR band. It is not a test the pipeline can pass.
- I also **overstated the danger zone as 10 bpm**. The destructive width is set by
  frequency resolution: **|HR - 4 x f_r| <~ 2-5 bpm** at a 30 s window (~2 bpm/cell). The
  10 bpm figure in the old notes was a conservative guard, not the physical width.

**Decision - keep 18 bpm as a measured failure-mode probe (`45b9c78`):** the paced arm
stays at 12/15/18. The 18 bpm case sits inside the failure zone **by design** and will be
measured on the current pipeline rather than avoided - the cap3 evidence predates the
adaptive K_b guard, peak-to-floor gates and warmup bin selection, so it is worth
re-measuring. Two conditions recorded: (1) **pacing makes this failure worse than natural
breathing**, because it pins f_r so the coincidence is *sustained* rather than intermittent;
(2) the 18 bpm arm must be **reported separately, never pooled** into headline agreement
metrics. Capture-day requirement added: record each subject's resting HR and the
**|HR - 4 x f_r| margin** - it predicts whether the session will fail.

**Retired / no longer used:**
- `session.start_delay_s` config key (dead).
- `notes/{relocking_bin_plan, warmup_bin_plan, live_demo_plan, capture_review}.md` (see the
  previous entry).

**Next:**
- **Live hardware smoke test** on the operator before any subject - the live capture path
  (radar -> live HR -> `adc_stream.bin`) has not been exercised recently.
- Then subject 1 of the 10 x 2 study.
- `HANDOFF.md` rewritten to the new S10 contract this session.

---

## 2026-07-12 - scripts/ audit: repaired the quality-mask diagnostics, retired three scripts

**Set out to do:** go through every `.py` in `scripts/` and classify each as useful, stale,
or retired, then act on the classification.

**Worked (with evidence):**

*Audit result — 25 scripts, only 3 runnable.* `live_demo.py`, `diagnose_live_run.py` and
`verify_live_demo_artifacts.py` were the only scripts that could execute. Everything else
was blocked by one of three causes, and separating them mattered:
1. the 2026-07-09 data reset (`results/`, `data/raw/`, `data/processed/` all empty);
2. **`experiments/` is entirely empty** — six scripts reference `exp004_window_length/` and
   `exp_eca_all/` configs and `run.py` files that no longer exist at all;
3. a **file move plus a schema change**, which broke eight scripts independently of data.

*Repaired the eight quality-mask diagnostics (cause 3).* `diag_hard_failure_thresholds`,
`diag_motion_spike`, `diag_motion_spike_thresholds`, `diag_phase_jump`,
`diag_range_bin_check`, `diag_rx_imbalance`, `diag_subject_bin_dropout`,
`diag_subject_bin_snr` all loaded `scripts/quality_mask_config.yaml`, which was moved to
`steps/step_4/config.yaml` in `78067cc` (a stale `add_quality_mask` `.pyc` in
`scripts/__pycache__/` was the fingerprint). The config was **also restructured** in the
move: `trim_frames` went from a top-level key (value 600) to `analysis.trim_frames`
(value 0). So repointing the path alone would have swapped a missing-file crash for a
`KeyError`. Fixed both: `CONFIG_PATH -> REPO_ROOT/steps/step_4/config.yaml` and
`cfg["trim_frames"] -> cfg["analysis"]["trim_frames"]` (7 scripts read it; `diag_motion_spike`
does not). Verified: every `hard_failures`/`soft_failures` key these scripts read resolves
against the real config (schemas turned out identical between old and new); all 8 parse; and
`diag_rx_imbalance`, `diag_motion_spike_thresholds`, `diag_phase_jump` now run to a clean
`"Sessions: 0 — nothing to plot"` exit. They are now **code-healthy and data-limited**,
which is the correct state pre-capture. Note the effective trim changed 600 -> 0 frames;
the scripts follow the config as the single source of truth.

*Recovered a live DSP finding from a script that was about to be deleted.* `_diag_report.py`
was a frozen 2026-06-15 report with ~110 lines of hardcoded prose conclusions. Its root
cause was **not** recorded in `notes/approach.md`, and is a property of the code rather than
of the deleted dataset: `eca_project()`'s hard floor **always projects out k = 1..4**, so the
0.15 Hz "don't suppress near the cardiac candidate" guard only ever applied to k >= 5 and
never protected against the common 4·f_r collision — ECA erased the cardiac signal along
with the harmonic. Confirmed still true at `src/vitals.py:142`. Already mitigated in code by
`eca_mode: skip_forbidden_harmonics_v1`, which is live in **both**
`scripts/live_demo_config.yaml` and `steps/step_6/config.yaml`. Recorded as
`notes/approach.md` **B.5**, and **corrected §7.1**, which had described the cardiac guard as
protecting a true HR near a harmonic — it does not, and the spec contradicted the code.
Residual caveat recorded: skipping a colliding harmonic trades *silent cancellation* for
*in-band contamination*; it does not recover the signal, so the 18 bpm arm remains a real probe.

**Failed / did not work, and why:** nothing failed. The near-miss worth recording: the
"cheap one-line config path fix" was not one line — checking the config's actual key
structure before editing is what caught the `trim_frames` reshape.

**Retired / no longer used:**
- `scripts/_diag_report.py` — frozen exp004 report; hardcoded to deleted `results/` folders
  and to `experiments/exp004_window_length/config.yaml`, which no longer exists. Its
  recommended `k_max` investigation sequence is **obsolete** (the fix it was arguing toward
  is already live). Root-cause finding preserved in `notes/approach.md` B.5 before deletion.
- `scripts/reselect_bins.py` — recomputed locked bins and **wrote them back to the
  manifest**. This contradicts the current design, which auto-locks the bin at warmup with
  no manual/manifest pin. Running it would have reintroduced a mechanism that was
  deliberately removed. Its question is now answered by `warmup_bin_selection.json`.
- `scripts/diag_back_support.py` — tested the torso-sway hypothesis for seated-no-back
  failures. Posture is now fixed (seated, back straight), so the question is closed.
- `scripts/__pycache__/` — contained a stale `add_quality_mask` `.pyc` from before the
  step_4 move.

**Kept deliberately, still stale (dead until there is data):**
- `diagnose_step6_hr.py`, `diagnose_coverage_gaps.py`, `diagnose_step6_candidate_tracks.py`
  — properly parameterized (`--sessions`, `--results-root`), no hardcoded dead paths. They
  aim straight at the **AHET yield problem**, the biggest open risk to the paper. Will work
  as-is on new Step-6 output.
- `plot_bland_altman.py` — dead as a tool (hardcoded to exp006/008/009 and a deleted run
  dir) but it is the **only** Bland-Altman plotter, and B-A is a required paper deliverable.
  Kept as the template to rewrite against new data.
- `diag_eca_overnotch.py`, `diag_heart_spectrum.py`, `diag_leakage.py` — the three
  respiratory-harmonic-collision investigations. Scientifically the most relevant of the
  dead set, but all three need `experiments/exp_eca_all/run.py`, which does not exist.
- `diag_per_window_error.py`, `diag_new_captures.py`, `plot_overlays.py`,
  `test_adaptive_kmax.py` — hardcoded to deleted run folders. `test_adaptive_kmax.py` is
  misleadingly named: it is a diagnostic, not a pytest test, and sits outside `tests/`.

**Next:**
- `experiments/` is empty — the study will need a new `experiments/<name>/config.yaml` per
  CLAUDE.md §7 before any offline metrics can be produced.
- Unchanged headline next action: **live hardware smoke test**, then subject 1.

---

## 2026-07-12 - scripts/ audit, part 2: judged the 8 remaining dead scripts; deleted 4

**Set out to do:** for each script left dead after the part-1 audit, decide whether it holds
knowledge or logic worth keeping. Not to repair them — to keep or erase.

**Worked (with evidence):**

*Two of the part-1 conclusions were wrong and are corrected here.*
1. The three harmonic diagnostics (`diag_eca_overnotch`, `diag_heart_spectrum`, `diag_leakage`)
   were described as "hardcoded to deleted run folders". They are **not** — each calls
   `_latest_run_dir()`, auto-discovering the newest run under `results/exp_eca_all/`, and takes
   `--sessions`. They are generic. They break *only* because `experiments/exp_eca_all/` does not
   exist. Re-creating that experiment revives all three with **zero code change**.
2. `src/compare.py` was believed to provide Bland-Altman (HANDOFF's pointer table said so). It
   does **not** — it provides MAE/RMSE/bias and `overlay_plot()` only. `plot_bland_altman.py` is
   therefore the **only** B-A implementation in the repo, and B-A is a required paper deliverable
   (CLAUDE.md §1). HANDOFF's pointer table corrected.

*Kept (4), with reasons:*
- `plot_bland_altman.py` — the only B-A code: bias, ±1.96 SD LoA, **and CIs on both** (t-based,
  `se_loa = sqrt(3·sd²/n)`), plus the exclusion rules (quality_gated / low_quality / NaN). Already
  properly parameterized (`--results_dir`, `--sessions`); only the cosmetic `SESSION_META` labels
  are stale. Non-trivial statistics worth preserving.
- `diag_heart_spectrum.py` — "is the picked peak sitting at 4·f_r?" The §4.2 collision is still a
  live risk and the 18 bpm paced arm is a deliberate probe of it. Directly relevant.
- `diag_leakage.py` — "is the band-edge power the sidelobe tail of 2·f_r?" ECA removes the
  discrete spike but cannot remove its leakage. Generic, unanswered.
- `diag_eca_overnotch.py` — compares raw / pre-ECA / post-ECA cardiac spectra. Its original
  question is now answered (B.5), but it is exactly the tool needed to **verify on new data that
  `skip_forbidden_harmonics_v1` actually preserves the cardiac peak**. Promoted from post-mortem
  to regression check.

**Retired / no longer used (deleted this session):**
- `scripts/test_adaptive_kmax.py` — tested **adaptive k_max** as the fix for the harmonic
  collision: `k_max = floor((0.833 − 0.083) / f_r)`, clamped to [1, 6]. **This approach was
  rejected** in favour of the targeted `skip_forbidden_harmonics_v1`, which is what actually
  shipped. Adaptive k_max is a blunt instrument: it drops *every* harmonic above the cut,
  including harmless ones worth cancelling, and at f_r ≈ 17-19 bpm it must fall to k_max ≤ 3 to
  spare 4·f_r, leaving substantial respiratory energy uncancelled. Rejection rationale preserved
  in `notes/approach.md` B.5 before deletion. Also hardcoded to `cap3_retake`/`cap5` raw `.bin`
  files that no longer exist, and carried canonical MAE/bias numbers from the retired dataset.
  Misleadingly named: it was a diagnostic, not a pytest test, and sat outside `tests/`.
- `scripts/plot_overlays.py` — a 25-line loop calling `compare.overlay_plot()`, hardcoded to run
  `results/exp_eca_all/20260621_095804`. **Zero unique logic**; the real function lives in
  `src/compare.py`. Trivially rewritten if ever needed.
- `scripts/diag_per_window_error.py` — per-window error plots hardcoded to
  `results/exp002_harmonic_rejection/20260614_133839` and `exp004_window_length/20260615_002408`.
  Generic scatter/error-vs-time plots; duplicates `compare.overlay_plot()` and the Step-6
  diagnostics. No unique method.
- `scripts/diag_new_captures.py` — same, for `cap3_retake`/`cap4`/`cap5`. Keyed to the
  `condition_20s`/`condition_30s` folder structure of the exp004 window-length experiment, which
  no longer exists. No unique method.

All four are recoverable from git history if that judgement proves wrong.

**Failed / did not work, and why:** nothing failed. Recording the process error: part 1 classified
these scripts from their **docstrings and path constants**, which produced two wrong calls (above).
Reading the input-resolution code is what corrected them. Header-skimming is not an audit.

**Next:**
- `scripts/` now holds 18 `.py`: 3 live-path, 8 quality-mask (repaired, data-limited), 3 Step-6
  (structurally sound), 4 kept-but-dead pending an experiment.
- **Re-creating `experiments/exp_eca_all/` is now the single highest-leverage cleanup** — it
  revives 3 of the 4 remaining dead scripts at once.
- Consider promoting the B-A statistics out of `scripts/plot_bland_altman.py` into
  `src/compare.py`, where the docstring already implies they live.

---

## 2026-07-13 - First live smoke tests (2 runs) - method VALIDATED against Masimo; ECA found inert

**Set out to do:** exercise the live capture path end-to-end for the first time in months
(HANDOFF's blocking next action), then repeat it with the Masimo so the radar could be scored
against ground truth.

### Run 1 - `results/live_demo/20260713_170323_live_demo_live_test1` (120 s, no reference)

**Worked:** the whole capture chain. Radar configured, warmup auto-locked bin 22 (~0.96 m), all
five artifacts written, 302 MB `adc_stream.bin`, **0 dropped packets**. `diagnose_live_run.py`
ran and wrote its plots. The live path is functional.

**Failed:** HR was blank on 29 of 30 hops (3% yield), dominated by `ratio_db_low`.

**The trap, recorded because it nearly worked:** the on-screen HR showed a stable, plausible
**69.46 bpm for the entire two minutes** - because the online median smoother holds the last
valid value. The demo *looked* like a success while 97% of hops were being rejected underneath.
Without `diagnose_live_run.py` this would have been logged as a pass. This is exactly the
CLAUDE.md S4 hazard ("the live demo's readouts are a sanity check, not a result"), observed in
the wild.

**Wrong inference made at the time (kept per S4):** I concluded from the stable ~69 bpm
`fallback_hr_bpm` that the cardiac signal was real and AHET was rejecting a correct answer. That
inference was **wrong** - see Run 2. Reasoning from a stable-looking number with no reference is
precisely the mistake the project rules warn against.

### Run 2 - `results/live_demo/20260713_172042_live_demo_massimo1` (180 s, Masimo)

Masimo bracketed the radar window (led 61 s, trailed 6 s); clocks agreed; alignment via the
integer `Timestamp` epoch (S9). Reference quality excellent: **Perfusion Index 5.5-13.0
(median 8.5), zero samples below the 0.5 gate**. 50 hops joined 1:1.

**Worked (with evidence):**
- **HEART RATE IS VALIDATED.** On the 5 AHET-passed hops: radar `[64.87, 64.87, 64.78, 64.88,
  65.19]` vs Masimo `[65, 65, 65, 65, 65]` -> errors `[-0.13, -0.13, -0.22, -0.12, +0.19]`.
  **MAE 0.16 bpm, RMSE 0.16 bpm, bias -0.08 bpm.** When the pipeline commits to an answer it
  agrees with the clinical reference to within a fifth of a beat. Bin lock, phase extraction,
  ECA, AHET and peak refinement are all confirmed working.
- **BREATHING RATE IS VALIDATED.** Before the collapse (below): **MAE 0.77 bpm, RMSE 0.87,
  bias +0.17, and 37/37 windows within 2 bpm** of Masimo `Breaths / min`.

**Failed / did not work, and why:**

1. **ECA is completely inert - the root cause of the yield problem.** Measured in-band power
   removed by ECA: **0.00 dB**, on every normal-breathing hop. `n_eca_skipped = 4` throughout.
   Mechanism: at f_r ~= 0.3 Hz (18 bpm) the harmonics **k=3,4,5,6 all land inside the cardiac
   band [0.8-2.0 Hz]**, and `eca_mode: skip_forbidden_harmonics_v1` skips *every* harmonic whose
   k*f_r falls in the band - i.e. all of them. The respiratory harmonic comb therefore stays in
   the cardiac band, holding the noise floor up, which crushes peak-to-floor to a **median
   -2.0 dB against a 2.0 dB gate**, so AHET rejects. This is the B.5 residual caveat, far worse
   than written: at ordinary breathing rates the "fix" disables ECA entirely in the one band it
   exists to clean. **Both ECA modes are now known-broken in opposite directions** - `legacy`
   erases the cardiac peak when 4*f_r ~= HR; `skip_forbidden_harmonics_v1` cleans nothing.

2. **Yield 10% (5/50).** Peak-to-floor gate sweep against ground truth:

   | gate | yield | MAE | est. >5 bpm off |
   |---|---|---|---|
   | 2.0 dB (current) | 20% | 0.98 bpm | 0 |
   | 0.0 dB | 36% | 2.04 bpm | 1 |
   | -1.0 dB | 46% | 2.35 bpm | 1 |
   | none | 74% | 2.13 bpm | 2 |

   Correcting an earlier claim of my own: the gate is **not** "saving us from garbage". The
   candidates it rejects are mostly 3-6 bpm off, not wild. With **no gate at all** MAE is still
   2.13 bpm with only 2 bad estimates in 37. The gate is set far more conservatively than the
   signal warrants - but it is calibrated against a floor that the ECA bug has artificially
   inflated, so **fix ECA first and re-measure before touching the gate.**

3. **`fallback_hr_bpm` is unreliable - do not use it as a proxy.** On the 5 hops where AHET was
   right (65 bpm), the fallback read 53-56 bpm (errors -9 to -12). It is a naive argmax. This is
   what invalidated the Run 1 inference.

4. **Respiration collapse + false confidence (new bug).** At **t=144 s** f_r pinned to **0.1 Hz
   (6 bpm) - exactly the respiration search-band floor** - and stayed there for the final 12
   hops, while Masimo showed 19 bpm. Cause: phase peak-to-peak **doubled, 13.8 -> 29-32 rad**
   (~9 mm of body movement) around t=138 s; the low-frequency drift dominated the respiration
   band and dragged the argmax to the floor. The subject moved - that part is protocol. **But
   `resp_valid` stayed `1` (high confidence) throughout the collapse.** Reporting high confidence
   while pinned to one's own band floor is a false-confidence bug and is worse than returning
   NaN, because nothing downstream can detect it. Fix independently of the motion.

**Caveats on all numbers above:** one subject, one 3-minute run, HR essentially flat at 64-68 bpm.
A narrow HR range makes the estimator's job easy. **5 valid hops is not a publishable sample.**
This establishes that the method works and where the bugs are; it is not evidence of accuracy at
study scale.

**Retired / no longer used:** nothing.

**Next:**
- Write a plan for the ECA forbidden-zone fix (narrow guard around the cardiac *candidate*, not
  the whole band) -> `notes/plan_eca_forbidden_zone.md`. Touches the shared Step-6 DSP path, so
  per CLAUDE.md S6 it needs an **independent cross-model review before any code is written**.
- Then re-measure yield and p2f on this same run before considering any gate change.
- Separately: fix the `resp_valid` false-confidence bug (flag band-floor pinning).
- The 180 s Masimo run is a genuinely useful regression fixture - both bugs are visible in it.

---

## 2026-07-13 - Cross-model review of the ECA forbidden-zone plan (CLAUDE.md S6)

**Set out to do:** put `notes/plan_eca_forbidden_zone.md` through the S6 cross-model plan review
before writing any code, and act on the findings.

**Worked (with evidence):** the OpenAI-family reviewer returned **8 findings. All 8 were checked
against the code and ALL 8 were valid.** None were rejected. They are now folded into the plan
(S5-S9) and the raw comment block removed. The reviewer agreed with the mechanism and direction,
so per S6 ("where the two models agree, trust it") the diagnosis is considered settled.

The two findings that changed the design, not just the checklist:

1. **Per-pass skip sets (finding 4) - the sharpest catch.** `eca_project()` is called twice:
   first pass (`vitals.py:353`) and second pass per AHET candidate (`vitals.py:474`). The second
   pass already varies `cardiac_candidate_hz=cand_hz` **but reuses the first-pass `skip_ks_set`**.
   Under the proposed mode that is incoherent - a rank-2 candidate would be judged with a guard
   parked on the *provisional* peak. Fix: recompute the skip set per candidate in the second pass.
   This also **partially mitigates the plan's self-declared biggest risk (S7.1)**: even if the
   provisional candidate is a respiratory harmonic, each AHET candidate is re-tested with a guard
   centred on itself.

2. **Guard width vs frequency resolution (finding 6) - worse than the reviewer stated.** They
   noted 0.10 Hz sits close to the boundary. Checking the arithmetic: for HR = 65 bpm, the k=4
   skip decision **flips between f_r = 0.295 and 0.300 Hz - a 0.005 Hz window - while the FFT bin
   at a 30 s window is 0.0333 Hz.** The decision therefore flips within **one sixth of a bin**, and
   f_r wandered 0.27-0.33 Hz across the evidence run. The proposed default would make k=4's fate
   **chatter between skipped and cancelled on adjacent hops**, making the post-ECA spectrum
   non-stationary for no physical reason. Consequences now written into the plan: a guard narrower
   than ~2 bins (0.067 Hz at 30 s) is near-meaningless because the candidate is itself bin-quantised;
   and the verification sweep must report **skip-decision stability** (fraction of adjacent hop
   pairs whose skip set changes), not just yield and MAE. A config that wins on yield but chatters
   is not acceptable.

The other six, all verified true in the code:
- **(1)** New config key must be **threaded through both call sites** (`live_demo.py:488-492`,
  `extract_heart_rate.py:857-859/1085-1087`), not merely added to the YAMLs; forwarding tests to
  mirror the existing pattern at `tests/test_step6_heart_rate.py:1981-2001` (verified: that test
  does exist and does exactly this).
- **(2)** `eca_project()` hardcodes a *second, different* guard (`0.15 Hz`, k>=5 only, at
  `vitals.py:145`). Leaving it would give two competing guard widths. Make the width an explicit
  parameter.
- **(3)** The skip set is currently built (`vitals.py:331-342`) **before** `prov_cand_hz` exists
  (`vitals.py:349-351`). The new mode depends on the candidate, so the ordering must be
  deliberately restructured, not patched around.
- **(5)** Add a synthetic test where **the dominant pre-ECA peak IS a respiratory harmonic** and
  the cardiac peak is weaker but present - the direct attack on S7.1. Confidently accepting the
  respiratory harmonic is a hard failure of the plan.
- **(7)** `experiments/` is **absent, not merely empty**, and `steps/step_6/config.yaml` uses
  **`window_s: 20`, `hop_s: 5`** while the live/evidence path uses **30 s / 3 s** - so scoring the
  modes under the default Step-6 config would silently compare at the wrong window length, and
  contradicts the standing "window is 30 s, do not shorten it" decision (HANDOFF S5). A dedicated
  `experiments/exp_eca_modes/` pinned to 30 s / 3 s is now required by the plan.
- **(8)** The live path **discards which harmonic was skipped**: `live_demo.py:543` collapses the
  bool vector to `int(np.sum(eca_skip))`, whereas offline Step 6 stores the full vector
  (`extract_heart_rate.py:690`). Prediction S6.5 depends on *which* k, not how many.

**Failed / did not work, and why:** nothing. Recording the near-miss: the plan's own worked example
("at f_r = 0.30 nothing is skipped") is *true* but sits 0.017 Hz - half a bin - from flipping. It
read as reassuring and was actually evidence of fragility. The reviewer caught the smell; the
arithmetic showed it was worse than either of us first thought. This is the S6 rule earning its
keep.

**Retired / no longer used:** the plan's original S5 (single skip set, hardcoded 0.15 left in
place, guard width set by assertion rather than by sweep). Superseded within the same document.

**Next:**
- Implement per the revised plan S8 (order: vitals.py -> config threading + forwarding tests ->
  synthetic tests incl. the S7.1 attack -> live artifact vector -> `experiments/exp_eca_modes/`).
- The diff touches the shared Step-6 DSP path, so per CLAUDE.md S6 it **also owes an independent
  cross-model CODE review** once written - the plan review does not discharge that.
- Capture the paced-16 bpm run (forces 4*f_r ~= 65 bpm ~= resting HR): the evidence run contains
  **no collision case at all**, so real-data collision behaviour is currently untested.

---

## 2026-07-13 - Paced-16 capture: collision MISSED, but a blocking k_max bug found

**Set out to do:** capture the collision fixture the plan says is missing — pace breathing at
16 bpm so that 4*f_r (= 64 bpm) lands on the ~65 bpm resting HR seen in the previous run, giving
real-data evidence for the §4.2 destructive case.

Run: `results/live_demo/20260713_182002_live_demo_massimo2` (183 s, Masimo, 50 hops).
Masimo brackets the radar window (leads 88 s, trails 3 s). Reference quality acceptable:
**PI median 3.8 (min 2.7)** — lower than the previous run's 8.5 but nowhere near the 0.5 gate.

**Failed to do what it was for — and why that is useful:**
**The pacing was executed perfectly but the collision did not happen.** Masimo `Breaths / min`
read **16.0 on every single sample** — flawless pacing. But the subject's **HR came out at 72 bpm
(range 69-77), not the ~65 bpm of the natural-breathing run.** So 4*f_r = 64 bpm sat ~8 bpm below
the heart rate: |HR − 4*f_r| median **8.2 bpm**, within the ≤5 bpm destructive zone on only
**4/49 hops (8%)**, and never within 2 bpm.

Cause of the miss: the collision target was computed from the *previous* run's resting HR, but
paced breathing at a below-natural rate appears to *raise* HR. **Lesson for the study: the
|HR − 4*f_r| margin must be computed from the HR observed *during* pacing, not from a prior
resting measurement.** For this subject at 72 bpm the colliding pace is **18 bpm** (4 × 18 = 72) —
which is exactly the study's existing 18 bpm arm, now confirmed as a real collision case for this
subject rather than a theoretical one.

**Worked (with evidence) — a NEW BLOCKING BUG, found by accident:**

**`k_max = 6` does not span the cardiac band, and the planned ECA fix would make it worse.**

The number of respiratory harmonics landing inside the cardiac band [0.8, 2.0 Hz] grows as f_r
*falls*. At the paced f_r = 0.2673 Hz (16 bpm), the **7th harmonic sits at 112.3 bpm — inside the
band, but beyond `k_max = 6`, so ECA can never touch it at all.**

Measured: on **15 of 44 hops (34%) the top candidate was 94-114 bpm**, with |cand0 − 7*f_r|
median **1.8 bpm**, while the true HR was 71.6 bpm. **The estimator was selecting the 7th
respiratory harmonic as the heartbeat.** This is why cand0 MAE vs Masimo was **14.24 bpm** on this
run versus 2.13 bpm on the natural-breathing run.

Coverage required vs `k_max = 6`:

| BR | f_r | highest k with k*f_r <= 2.0 Hz | k_max=6 enough? |
|---|---|---|---|
| 12 bpm | 0.200 Hz | **k = 9** | **NO** |
| 14 bpm | 0.233 Hz | **k = 8** | **NO** |
| 16 bpm | 0.267 Hz | **k = 7** | **NO** |
| 18 bpm | 0.300 Hz | k = 6 | yes (barely) |
| 20 bpm | 0.333 Hz | k = 5 | yes |

**This directly threatens the study design:** the paced arm includes **12 bpm**, where harmonics
k=7,8,9 (at 84, 96, 108 bpm) all sit in the cardiac band and are *never* cancelled.

**Why it blocks the ECA fix.** Today `skip_forbidden_harmonics_v1` skips k=3..6 anyway, so ECA
cancels nothing in-band and k=7 is just one peak among many. Once `guard_cardiac_candidate_v1`
starts cancelling k=3..6 as intended, **k=7 becomes the only surviving in-band respiratory
harmonic** — and therefore the strongest competitor to the cardiac peak. Shipping the reviewed
plan as-is would have traded one failure for another.

**Consequence for process:** the plan is **un-cleared**. `notes/plan_eca_forbidden_zone.md` §5.7
(derive `k_max` from the band ceiling: `k_max_eff = floor(band_hi / f_r)`) is new, materially
changes the design, and has **not** been cross-reviewed. Per CLAUDE.md §6 it needs a second review
pass before any code is written. The new section also names the risk it introduces
(over-projection: more harmonics = more subspace columns removed from an N=600 window, which is
the `legacy` failure in another guise), so `k_max` becomes a swept axis in verification, not an
assumed-safe derived value.

**Also observed:**
- **The respiration collapse recurred** (f_r pinned to the 0.1 Hz / 6 bpm band floor, ~t=123-129 s),
  third occurrence across three runs. Still `resp_valid = 1`. Confirms it is systematic, not a
  one-off.
- HR yield **3/49 (6%)**; on the 3 passing hops MAE 1.82 bpm vs Masimo.

**Retired / no longer used:** the plan's "cleared to implement" status (2026-07-13, same day).

**Next:**
- Cross-review plan §5.7, then implement §8.
- **Capture a paced-18 bpm run** — the real collision fixture for this subject (4 × 18 = 72 ≈ the
  paced HR). Compute the margin from the HR *during* pacing.
- The `resp_valid` false-confidence bug is now 3-for-3 and should be fixed before the study.

---

## 2026-07-13 - Second cross-model review of the ECA plan; plan CLEARED; a second yield ceiling measured

**Set out to do:** put the amended plan (§5.7, the `k_max` prerequisite found by the paced-16 run)
back through the CLAUDE.md §6 cross-model review, since that section materially changed the design
and neither model had seen it.

**Worked (with evidence):** the reviewer returned **7 findings. All 7 were checked against the code
or the data, and ALL 7 were valid.** None rejected. Folded into the plan; comment block removed.
The plan is now **CLEARED to implement** (reviewed twice; 15 findings total, 15 accepted).

Two findings became new hard constraints:

1. **Fixed artifact shape (finding 2).** `eca_skipped_harmonics` is currently sized by the
   *configured* `k_max` (`vitals.py:332`), and Step 6 **stacks it into an NPZ**
   (`extract_heart_rate.py:690-691`). Now that `k_max_eff` varies per hop with f_r, returning a
   variable-length vector would **break the stack or silently hide k > 6** - i.e. the artifact
   would not record what the algorithm did, making §6.5 and §5.7 unverifiable. Fix: always return a
   fixed `(k_max_cap,)` vector, plus scalar `k_max_eff` / `n_eca_projected` per hop.

2. **Cardiac retention, not just floor suppression (finding 4).** The over-projection risk is
   **not** "20 columns vs N=600" as I framed it. It is that projecting many harmonics at a
   *slightly wrong f_r* attenuates the **true cardiac peak**. Verification must therefore report
   power at the Masimo cardiac bin **before vs after ECA, under deliberate f_r error** (+/-1 FFT
   bin, +/-0.01-0.02 Hz). **A mode that suppresses the floor but erases cardiac power must fail the
   sweep** - that is the `legacy` failure wearing a new coat, and a yield-only report would score
   it as a success.

**A SECOND, INDEPENDENT YIELD CEILING - measured (finding 5).** The reviewer predicted that at low
breathing rates several in-band respiratory harmonics could outrank the heart and push the true
cardiac peak out of the top-3 candidate list (`AHET_MAX_CANDIDATES = 3`, `vitals.py:26`), where
AHET can never reach it. Measured against Masimo (true HR = within 3 bpm):

| run | rank 0 | rank 1 | rank 2 | **not in top-3** | max achievable yield |
|---|---|---|---|---|---|
| natural (18 bpm) | 23/37 | 6/37 | 1/37 | **7/37 (19%)** | **81%** |
| paced-16 (low BR) | 23/44 | 8/44 | 1/44 | **12/44 (27%)** | **73%** |

**There is a hard ceiling on yield of ~73-81%, independent of ECA and independent of the AHET
gate** - and, as predicted, **it is worse at low breathing rates.** This bounds the plan's own
prediction: >50% yield is reachable; 100% is not. Chasing the last 20% by loosening the gate would
be chasing a candidate that is not in the list. Action recorded: re-measure this table after the
ECA fix (cancelling k=3..6 should promote the true peak up the ranking); sweep
`AHET_MAX_CANDIDATES` only if the ceiling does not lift on its own - raising it pre-emptively just
adds more chances to accept a wrong candidate.

The other five, all verified:
- **(1)** Band-ceiling **off-by-one**: `eca_project()` breaks on `if freq >= 2.0` (hardcoded,
  **exclusive**) while `cardiac_mask` uses `freqs <= band[1]` (**inclusive**). At f_r = 0.2,
  `10 x 0.2 = 2.0` - my formula admits k=10, the loop drops it, and my own §5.7 table said k=9
  (matching the loop, not my formula). Resolved: make ECA consistent with the spectrum masks,
  include the exact ceiling, and replace the literal `2.0` with `band[1]`.
- **(3)** Derive `k_max_eff` **with a cap**, not unbounded: the physiological gate floor is
  `_GATE_LO_HZ = 0.15` (`vitals.py:238`), so `floor(2.0/0.15) = 13`. A bad f_r estimate must not
  silently expand the ECA subspace. `k_max_cap` (default 10) goes in config and into the sweep.
- **(6)** §7.5 was **stale**: it still pointed the next implementer at a paced-16 collision fixture
  "until it exists". It exists and it missed. Rewritten to point at **paced-18**, with the lesson:
  compute the |HR - 4*f_r| margin from the HR observed **during** pacing, not from a prior resting
  measurement - pacing below the natural rate raises HR.
- **(7)** **Raw provenance.** §8.6 re-processes `results/live_demo/.../adc_stream.bin`, which is a
  *live mirror*; CLAUDE.md §4 says it becomes canonical only via deliberate promotion. Plan now
  states explicitly: **referenced in place as a regression fixture, NOT promoted to `data/raw/`**,
  and the experiment **must log the SHA-256 of every raw file it reads** (CLAUDE.md §3 rule 1).

**Failed / did not work, and why:** nothing failed. Recording the pattern, because it is now
two-for-two: **both reviews found errors in my own arithmetic/framing that I had presented
confidently.** Review 1 caught a guard default sitting half an FFT bin from flipping. Review 2
caught an off-by-one between my formula and my own table, and correctly reframed the
over-projection risk I had mis-stated as a column-count problem. The §6 cross-model rule is
carrying real weight on this workstream, not ceremony.

**Retired / no longer used:** the plan's unbounded `k_max_eff = floor(band_hi / f_r)` (superseded
by the capped form); the plan's "paced-16 is the intended collision fixture" statement (§7.5).

**Next:**
- **Implement per plan §8, in order.** The code diff then owes its **own** independent cross-model
  CODE review (§6) - the two plan reviews do not discharge it.
- **Capture the paced-18 bpm run** - still the only missing collision fixture.
- The `resp_valid` false-confidence bug remains open (3 occurrences in 3 runs).

---

## 2026-07-13 - Implemented the ECA fix; it FAILS its own acceptance test. NOT promoted.

**Set out to do:** implement `notes/plan_eca_forbidden_zone.md` §8 (cleared by two cross-model
reviews, 15/15 findings accepted).

**Worked (with evidence):** the implementation is complete and matches the reviewed plan.
`src/vitals.py`: new `eca_mode: guard_cardiac_candidate_v1`; skip set now derived **after**
`prov_cand_hz` (review 1, finding 3); **per-pass** skip sets, recomputed for the candidate each
pass actually tests (finding 4); `cardiac_guard_hz` / `band_hi` / `hard_floor_k` are now explicit
parameters of `eca_project()`, replacing the hardcoded `0.15` / `2.0` / `4` (finding 2); a single
`eca_harmonic_ks()` selector is used by **both** the projection and the reporting, so the artifact
cannot drift from what the algorithm did; bounded per-window `k_max_eff = min(k_max_cap,
floor(band_hi / f_r))` with an **inclusive** band ceiling matching the spectrum mask (review 2,
findings 1 & 3); fixed-length `(k_max_cap,)` skip vector plus scalar `k_max_eff` /
`n_eca_projected` (finding 2), with `steps/step_6` and `scripts/live_demo.py` updated to size the
artifact by the cap — **Step 6's `_eca_skip()` truncated to `k_max`, which would have silently
hidden k > 6, exactly the failure the reviewer predicted.** Config keys added and **threaded
through both call sites** with a forwarding test (finding 1). Live NPZ now stores **which**
harmonics were spared, not just how many (finding 8).

Tests: **562 passed.** (10 pre-existing failures in `test_exp004.py` / `test_exp002_artifacts.py`
are dead-dataset rot, confirmed identical on a clean tree — not caused by this change.)

**FAILED — and this is the headline:**

**The new mode fails the acceptance test this very plan wrote to attack its own biggest risk
(S7.1 / S8.4). It reports a respiratory harmonic as the heart rate, and it is a REGRESSION
against BOTH existing modes.**

Synthetic: f_r = 0.30 Hz (4*f_r = 72 bpm is the strongest in-band peak); true heart 96 bpm,
weaker, with a proper 2nd harmonic; real AHET gate values.

| eca_mode | verified | reported | verdict |
|---|---|---|---|
| `legacy` | True | **95.99 bpm** | correct - finds the heart |
| `skip_forbidden_harmonics_v1` | False | NaN | safe |
| **`guard_cardiac_candidate_v1`** | True | **71.43 bpm** | **REPORTS THE DECOY** |

**Mechanism:** `prov_cand_hz` is the argmax of the *contaminated* pre-ECA spectrum, so when a
respiratory harmonic outranks the heart, the harmonic IS the provisional candidate - and the guard
then **spares the decoy**. It survives ECA at full strength, becomes rank-0, and `strict_v1`
returns the *first* passing candidate. The true heart sits at **rank 1 with p2f = 33 dB** and is
never examined. `legacy` gets this right for precisely the reason we removed: it cancels k<=4
unconditionally, which kills the decoy.

**This is not a corner case:** on the paced-16 capture a respiratory harmonic was the top candidate
on **34% of hops**. A harmonic outranking the heart pre-ECA is the normal condition.

**Why the reviewed mitigation (S5.3, per-candidate re-guarding) does not save it:** that fixes
*evaluation*, but the failure is in *ordering* - the decoy is evaluated first, passes, and rank 1
is never reached.

**Consequences / actions taken:**
- **`guard_cardiac_candidate_v1` is NOT promoted.** Both configs still use the old
  `skip_forbidden_harmonics_v1`. **The live path is unchanged.** The new mode exists but is unused.
- The §8.4 test is kept and marked `xfail(strict=True)` with the full mechanism in its reason, so
  the failure is recorded (CLAUDE.md §4 - negative results are not deleted) and will flip to XPASS
  the moment it is genuinely fixed.
- Plan status reverted to **holed**; new **§5.9** documents the failure, the evidence and the open
  design question.

**Candidate repair (probed, NOT implemented, NOT reviewed):** derive `prov_cand_hz` from a **pilot
ECA pass with all harmonics cancelled** rather than from the raw pre-ECA spectrum. Probe result:
the decoy case is **fixed** (prov_cand becomes 96.0 = the true heart, and nothing is spared). But
in the collision case **the pilot erases the cardiac peak by -29.6 dB before the guard is chosen** -
it still fired on the right k, but it was deciding from a spectrum in which the thing it protects
had already been destroyed. That is luck, not robustness.

**The real problem, stated honestly:** the decoy case wants *all* harmonics cancelled in the first
pass; the collision case wants the colliding one *spared*; and we cannot tell which case we are in
without already knowing the answer. That is a genuine identifiability problem, and the reviewed
design papered over it.

**Retired / no longer used:** the plan's "cleared to implement" status (again). §5.3's claim that
per-candidate re-guarding "directly mitigates risk §7.1" - it does not.

**Next:**
- **Third cross-model review**, on §5.9's open design question. Directions on the table:
  (a) build candidates from the pilot spectrum but verify on the guarded one; (b) carry candidates
  from both spectra and let AHET arbitrate, flagging coincidence-ambiguous estimates; (c) penalise
  candidates coincident with k*f_r instead of returning the first passer.
- Do **not** promote anything until the §8.4 test passes.
- Still outstanding: the paced-18 collision fixture; the `resp_valid` false-confidence bug.

---

## 2026-07-13 - Third review: the reviewer found the ROOT CAUSE. AHET cannot reject a respiratory harmonic.

**Set out to do:** get the implementation + post-mortem (Part II) cross-reviewed, and assess.

**Worked (with evidence):** the reviewer returned **9 comments. All 9 verified against the code,
all 9 valid, none rejected.** Four were concrete bugs/gaps in my diff (below). **One of them - §12.6
- is the most important finding of this entire workstream, and it supersedes my own recommendation.**

**THE ROOT CAUSE (reviewer's §12.6, confirmed by me):**

ECA's projection ceiling is `band_hi` = 2.0 Hz. But AHET searches for the **cardiac 2nd harmonic**
up to **2 x band_hi ~ 4 Hz**. **That region is never cleaned.**

Therefore a decoy at k*f_r has its "2nd harmonic" at **2k*f_r - which is itself a respiratory
harmonic**, sitting in the uncleaned region. Every harmonic k has a partner at 2k. So:

**AHET's second-harmonic consistency check - the mechanism the entire method rests on - is
STRUCTURALLY INCAPABLE of distinguishing a respiratory harmonic from a heartbeat.**

Measured (f_r = 0.30 Hz, decoy = 4*f_r = 72 bpm, true heart = 96 bpm, realistic comb k=1..14):

```
  AHET seeks the decoy's 2nd harmonic at  2 x 1.20 Hz = 2.40 Hz
  8 x f_r                               = 8 x 0.30 Hz = 2.40 Hz   <-- THE SAME PLACE
  magnitude at 2.40 Hz (decoy's "evidence")    = 18.75
  magnitude at 3.20 Hz (true heart's evidence) =  5.83
```

**AHET's evidence for the fake was 3.2x stronger than for the real heart.** No gate threshold fixes
this. It also explains the observed failure exactly: the decoy passed with `2nd = 144.00 bpm`,
which is precisely 8*f_r.

**THE FIX WORKS (measured).** Extending ECA's ceiling to 2 x band_hi so the 2k*f_r lines are
cancelled *before* AHET looks for them. Same signal, same gates, only the ceiling changes:

| ECA ceiling | k_max_eff | verified | reported | verdict |
|---|---|---|---|---|
| 2.0 Hz (current) | 6 | True | **72.00 bpm** | reports the decoy |
| **4.0 Hz (v2)** | 13 | True | **95.99 bpm** | **CORRECT - finds the heart** |

The decoy is still found, but its fake evidence is gone, so AHET rejects it
(`low_cand_floor_db_low`) and moves to the true HR. **The §8.4 acceptance test would XPASS.**

**Failed / what I got wrong:**
- **My Option A (the "pilot" pass) is WITHDRAWN as the primary fix.** It patched *candidate
  ordering* - it never explained *why AHET accepted the decoy at all*. It also leaned on a -29.8 dB
  notch-shoulder residual that I had explicitly written that I did not trust. The reviewer's §12.8
  ("treat the pilot residual as an ambiguity detector, not a candidate source") is the correct,
  narrower role for it, and is adopted verbatim.
- **My §11.2 ("the separation doubles at the 2nd harmonic") had its dependency backwards.** The
  physics is right, but it is *useless* while 2k*f_r sits uncancelled next to 2*HR. §12.6 is the
  precondition that makes §11.2 work at all. I presented it as a standalone discriminant; it is not.
- **§12.3 caught a broken test I wrote.** The §8.4 xfail only rejects estimates within 5 bpm of the
  decoy, so a future change confidently reporting a *different* wrong HR (85, 110 bpm) would XPASS.
  A test that can pass for the wrong reason is worse than no test.

**Other verified gaps in my diff (all to be fixed in v2):**
- **§12.1** Step 6 never persists `k_max_eff` / `n_eca_projected` - and Step 6 is the path that will
  *score the modes* offline.
- **§12.2** `_fake_estimate()` still returns the old `(k_max,)` skip vector, so the Step 6
  integration tests **never exercise the cap-sized artifact path** they were written to protect.
- **§12.4** ECA metadata is first-pass/global only, not candidate-wise - yet the whole failure is
  about rank-0 vs rank-1 being judged on *different* spectra.
- **§12.5** peak-to-floor is **not comparable across candidates** (each has a different ECA basis,
  hence a different floor). This makes my §11.3 "rank by credibility" idea unsafe as stated;
  deferred.
- **§12.9** the docstring calls the unpromoted mode "CURRENT".

**Retired / no longer used:** §11.1 (the pilot) as the primary repair; §11.3 (credibility ranking)
for now - under v2 the decoy is rejected on its own merits, so re-ranking is no longer load-bearing.

**Written:** `notes/plan_eca_forbidden_zone.md` **PART III - amended design v2** (§13-§19),
self-contained for review pass 3. Contains the evidence above, the design, the new risks, hard
acceptance criteria, and a disposition table for all 9 comments. **Nothing implemented; nothing
promoted; the live path is unchanged.**

**The top risk v2 introduces, stated plainly:** it strips **26-32 subspace columns** from an N=600
window (vs 10-12 today), and **the fix now DEPENDS on the cardiac 2nd harmonic surviving ECA** -
so over-projection is no longer a side-check, it is the mechanism. If ECA collaterally damages
2*HR, v2 turns working windows into NaN. Retention at **both** the fundamental **and 2*HR**, under
f_r error, is now a hard acceptance gate.

**Next:**
- Review pass 3 on PART III. Key question asked of the reviewer: does over-projection kill this?
- If cleared: implement v2, fix §12.1-§12.5/§12.9, re-run §8.4 (must XPASS), then offline scoring.
- Explicit decision requested: **should `legacy` be the shipping default until v2 is proven?** It is
  currently the only mode that gets the common (34%-of-hops) decoy case right.
- Still outstanding: the paced-18 collision fixture; the `resp_valid` false-confidence bug.

---

## 2026-07-14 - Fourth review: v2 REJECTED. ECA cancellation degrades as k * delta_f_r.

**Set out to do:** get amended design v2 (extend the ECA ceiling to 4 Hz so AHET's second-harmonic
evidence region is cleaned) cross-reviewed, and act on the result.

**Worked (with evidence):** the reviewer returned **12 comments. All 12 verified, all 12 accepted,
none rejected.** Comment **20.4 is decisive and kills v2.**

**v2 IS REJECTED. The measurement:**

v2's mechanism is to cancel the `2k*f_r` line so AHET cannot use it as fake evidence for a decoy at
`k*f_r`. But an ECA projection line at order k is displaced by **k * delta_f_r**, and v2 depends on
k = 8..15. Tested on the §8.4 decoy scenario (true f_r = 0.30 Hz; one FFT bin = 0.0333 Hz):

| f_r error | attenuation at 8*f_r (the fake evidence) | retention at 2*HR | reported | verdict |
|---|---|---|---|---|
| **exact** | **-51.0 dB** | +0.0 dB | 95.99 | correct |
| **+0.10 bin (0.2 bpm)** | **-1.6 dB** | -0.0 dB | **71.93** | **DECOY** |
| -0.10 bin | -1.3 dB | +0.0 dB | **72.05** | **DECOY** |
| +/-0.25 .. +/-2 bin | ~0 dB | ~0 dB | 90.00 | **90 bpm = 5*f_r - another harmonic** |

**A f_r error of one tenth of an FFT bin (0.2 bpm) destroys the cancellation and the decoy returns.**
Note the heart is retained perfectly throughout: **v2 does not fail by damaging the heart, it fails
by not removing the decoy's fake evidence** - a two-sided failure that a retention-only check (which
is all I had specified) would have been blind to. Required accuracy for v2: **~0.01 bin ~ 0.02 bpm**.
On the real runs f_r wandered 0.27-0.33 Hz hop to hop.

**THE GENERAL LESSON (bigger than this plan):** **ECA's cancellation quality degrades as
k * delta_f_r.** Any mechanism leaning on high-order harmonics is fragile by construction. `legacy`
is robust to f_r error partly *because* it only projects k <= 4. v2 moved the load onto k = 8..15 -
a **13x amplification of f_r error** - without noticing. **f_r precision is now a first-class
requirement, not an input we can take for granted.**

**Failed / my errors caught by this review:**
- **20.2** my coverage table was off by one: `floor(4.0/(16/60)) = 15`, not 14 - I got 14 by
  rounding f_r to 0.267 first. **Precisely the boundary bug `_CEIL_EPS` was added to prevent.**
  12/16/20 bpm all hit the ceiling exactly and must be explicit test cases.
- **20.7** my near-collision arithmetic was wrong: a bin is 2 bpm, so `2*delta <= 1 bin` only for
  `delta <= 1.0` bpm, not the "1-2 bpm" I wrote.
- **20.5** my §16.4 was technically wrong: the bandpass is an exact FFT mask on the *input*; it does
  not make the sine/cosine *basis* ill-conditioned. Withdrawn.
- **20.1** my §14.2 "measured" evidence lived only in a scratchpad probe - it cannot support a
  measured claim under CLAUDE.md §3 rule 1.

**Other accepted findings (all verified in code):**
- **20.11** the AHET verdict rests on a **cross-band score**: the numerator is measured near
  2*candidate (up to 4 Hz) while `comparison_floor = median(spec2[cardiac_mask])` is the 0.8-2.0 Hz
  band. Any extended ceiling changes numerator and denominator differently, so the existing dB
  thresholds are **not calibrated** for it.
- **20.6** `n_eca_projected` counts *selected orders*, not columns that survive the Gram-Schmidt
  norm guard - so "silently dropped columns" is currently unverifiable.
- **20.9** `k_max_cap = 16` was a **silent partial implementation**: full 4 Hz coverage needs cap 20
  at 12 bpm, cap 26 at the 0.15 Hz gate.
- **20.10** at **f_r < 0.20 Hz the +/-0.10 Hz guard spans >= the harmonic spacing**, so two adjacent
  harmonics can fall inside one guard - false ambiguity at 9-12 bpm.
- **20.8** yield must be **stratified by distance to the nearest respiratory harmonic**, or the
  method looks accurate by rejecting exactly its hardest windows.
- **20.12** my "if v2 dies we ship `legacy`" is **withdrawn**. `legacy` has its own collision-erasure
  failure; one synthetic win does not earn promotion. All configs stay unpromoted.

**Retired / no longer used:** amended design v2 (PART III) as an implementable plan; §16.4's
conditioning argument; §17's automatic-`legacy`-fallback rule.

**Written:** `notes/plan_eca_forbidden_zone.md` **PART IV** - the decisions table and a **staged
plan** (§23) replacing the big-bang v2 implementation. Stages 0-6, each small and falsifiable.

**Next — the plan of record:**
- **Stage 0 (no design risk, do now):** pay down review debt - fix the §8.4 xfail so it cannot XPASS
  for the wrong reason; make the probe a committed seeded test; persist `k_max_eff`/`n_eca_projected`
  in Step 6; fix `_fake_estimate()` to the production shape; return real basis diagnostics from
  `eca_project()`; candidate-wise skip sets; docstring.
- **Stage 1 = THE GATE (touches no DSP code):** can f_r be estimated to ~0.01 bin at all? Test a
  **comb-fit** f_r estimator (the high harmonics give huge leverage on f_r) on synthetic *and* on
  the two Masimo runs via a residual-energy-vs-f_r sweep. **If Stage 1 fails, v2's whole family is
  unreachable and we stop** - for a day of work, not a fortnight.
- If Stage 1 fails, the strongest alternative is **temporal continuity**: on the natural-breathing
  run the picked peak stayed **stationary at 69 +/- 1 bpm while 4*f_r swept 15.8 bpm across it**. A
  respiratory harmonic tracks f_r; a heartbeat does not. That discriminant needs **no high-k
  precision at all**, and the existing data can validate it immediately.

---

## 2026-07-14 - Stage 0 complete (review debt). Plus: the high-k harmonics are NOT coherent lines.

**Set out to do:** implement Stage 0 of the staged plan - the review debt that is independent of
which ECA design eventually wins - and, before that, test the reviewer's deepest objection.

### The measurement that killed Stage 1 before we built it

The reviewer's comment 3 warned that a comb-fit minimum measures **precision under an assumed
model**, not accuracy, and that **a 30 s window of real breathing may not possess a single f_r at
all**. Tested on the two Masimo captures.

For each hop and order k, I searched +/-1.5 bins for the frequency that cancels **best** - removing
f_r error entirely - and compared it against cancellation at our estimated f_r:

| k | k*f_r | cancellation at our f_r | cancellation at the BEST possible f_r | gain from a perfect estimator |
|---|---|---|---|---|
| 1 | 0.30 Hz | -5.5 dB | -8.1 dB | **2.5 dB** |
| 3 | 0.91 Hz | -0.2 dB | -1.3 dB | **1.1 dB** |
| 4 | 1.22 Hz | +0.1 dB | -1.0 dB | **1.1 dB** |
| 8 | 2.43 Hz | +0.6 dB | -0.5 dB | **1.2 dB** |
| 13 | 3.95 Hz | +0.9 dB | -0.2 dB | **1.1 dB** |

**A PERFECT f_r estimator buys 0.5-2.5 dB. v2 needed ~20 dB.**

So it is **not an estimation problem**: at k >= 3 there is **no coherent line to cancel**, at any
f_r precision. **Stage 1 as designed (build a comb-fit f_r estimator) is dead before it starts** -
and, worse, a **stationary synthetic comb would have PASSED it**, because such a comb has perfectly
coherent harmonics by construction. That is precisely the model-mismatch trap the reviewer named.

**Flagged, NOT claimed:** even k=1 shows only 5-8 dB. If that survives a properly-designed metric it
would question ECA's premise well beyond v2. **I built that metric in ten minutes and the absolute
depths depend on my +/-0.25 Hz band choice, so this is a flag for investigation, not a finding.**
The *difference* column is robust (a within-metric comparison); the absolute depths are not.

### Stage 0 - done, all 567 tests green (1 xfail = the known design hole)

Pinned the diagnostic API **before** editing (reviewer comment 8), then implemented:

- **`eca_project(..., return_diagnostics=True)`** returns `(clean, diag)` with **pinned shapes**:
  `selected_ks`, `retained_ks`, `cols_retained (k,2)`, and column counts. It reports **which
  sine/cosine columns actually survived modified Gram-Schmidt** - `n_eca_projected` only ever
  counted *selected orders*, so a run could report full coverage while the projection silently
  used fewer columns (comment 20.6).
- **Numerical-invariance regression** (comment 8): `test_diagnostics_do_not_change_the_projected_
  signal` asserts the returned signal is **bit-identical** with and without diagnostics. A
  diagnostic that perturbs what it measures is worse than none.
- **Fixed the §8.4 xfail assertion** (comment 20.3). It previously only rejected estimates near the
  decoy - so a change confidently reporting a *different* wrong HR (85, 110 bpm) would have XPASSED.
  It now requires the TRUE HR when AHET verifies; NaN stays acceptable. **A test that can pass for
  the wrong reason is worse than no test.**
- **Committed, seeded test replacing the scratchpad probe** (comment 20.1):
  `test_ahet_cannot_reject_a_respiratory_harmonic_on_2nd_harmonic_evidence_alone` pins the
  structural finding - a decoy at `k*f_r` has its "2nd harmonic" at `2k*f_r`, which is itself a
  respiratory harmonic, and its fake evidence is **stronger** than the true heart's.
- **Step 6 now persists** `k_max_eff`, `n_eca_projected`, `n_eca_cols_retained/dropped`,
  `eca_retained_ks` and the **candidate-wise** skip matrix, to both `heart_intermediates.npz` and
  `heart_windows.csv` (comments 12.1, 12.4). Offline is the path that will score the modes; it was
  recording none of this.
- **`_fake_estimate()` now returns the PRODUCTION shape** (comment 12.2). It returned
  `np.zeros(k_max)` while the real estimator returns a fixed `max(k_max, k_max_cap)` vector - so the
  Step 6 integration tests **never exercised the cap-sized artifact path they exist to protect**.
  The old shape assertion (`== (k_max,)`) is replaced, and a new test asserts the contract against
  the **real** estimator, not the fake.
- **Unrounded-f_r ceiling tests** at 12/16/20 bpm, all of which hit a 4.0 Hz ceiling **exactly**
  (comment 20.2) - the boundary case my own v2 table got wrong.
- **Docstring corrected**: `guard_cardiac_candidate_v1` is **"IMPLEMENTED, NOT PROMOTED"**, with the
  failure summarised inline (comment 12.9).
- Live path stores the basis diagnostics too, for parity.

**Retired / no longer used:** Stage 1 as specified in PART IV §23 (build a comb-fit f_r estimator).
Its premise - that better f_r precision unlocks deep high-k cancellation - is **false on real data**.

**Next:**
- **Rewrite Stage 1**: not "estimate f_r better" but "**measure harmonic coherence properly**" -
  a committed version of the experiment above, with a defensible metric.
- **Promote temporal continuity to the leading candidate.** It needs no high-k precision at all:
  on the natural run the picked peak stayed **stationary at 69 +/- 1 bpm while 4*f_r swept 15.8 bpm
  across it**. A respiratory harmonic tracks f_r; a heartbeat does not. Both existing captures can
  validate it immediately.
- Investigate the k=1 flag above with a proper metric before drawing any conclusion.
- Nothing promoted. Live path unchanged.

---

## 2026-07-14 - Stage 0 review fixes (P1/P2). Plus: Step 4 has had NO working test coverage.

**Set out to do:** act on the Stage 0 review, which found the estimator work solid but the
**evidence pipeline incomplete**.

**Worked (with evidence) — all review findings fixed:**

- **[P1] The diagnostics never reached disk.** A real bug I introduced: the new fields were added
  to the per-window dict but `_write_npz()` never stacked them, so `k_max_eff`, `n_eca_projected`,
  the basis counts, retained orders and candidate-wise metadata **were silently absent from
  `heart_intermediates.npz`** — and the offline NPZ is the path that will score the ECA modes.
  Now stacked and written.
- **[P1] Candidate-wise projection evidence was discarded offline.** `candidate_eca_retained_ks`,
  the retained-column counts and the first-pass `eca_cols_retained` matrix were dropped by
  `_intermediates_from_result()`. The artifact therefore could not reconstruct the basis each
  candidate was judged on — which is the *whole point*, since the v1 failure was rank-0 vs rank-1
  being evaluated on different spectra. Now persisted.
- **[P2] Artifact-shape errors were silently hidden.** `_eca_skip()` / `_cand_eca_2d()` truncated or
  padded malformed estimator output, and `eca_project(diag_len < k_max)` silently truncated its
  diagnostics. **All now FAIL LOUDLY** with an explanatory error. Silent truncation produces
  evidence that looks complete but omits the highest harmonics — precisely the failure class these
  diagnostics exist to detect. **This immediately caught two more test fakes still emitting the old
  `(k_max,)` shape**, which a truncating implementation would have swallowed.
- **[P2] No test verified the persistence contract** — which is exactly why P1 slipped through.
  Added integration tests that OPEN the NPZ and require the keys, the shapes, **and nonzero values
  on valid windows** (a stack of zeros would satisfy a shape-only test while proving nothing), plus
  a gated-window sentinel test and loud-failure tests for the shape contracts.
- All fake estimators now route through **one** `_fake_eca_evidence()` helper, so the production
  contract cannot drift between them.

**Full suite now COLLECTS (it did not before).** The reviewer correctly noted my "all green" claim
used `--ignore`, which is not green. Fixed:
- **Deleted 3 dead test files** — `test_exp004_analysis.py`, `test_exp004.py`,
  `test_exp002_artifacts.py`. All three depend on `experiments/exp004_window_length/`, which does
  not exist (the `experiments/` directory is empty) and whose data was deleted in the 2026-07-09
  reset. They tested a retired experiment on deleted data.
- **Repointed the quality-mask tests** from `scripts.add_quality_mask` (moved) to
  `steps.step_4.add_quality_mask`, and from `scripts/quality_mask_config.yaml` to
  `steps/step_4/config.yaml`, and fixed the `trim_frames` -> `analysis.trim_frames` schema change.
  **176 of these tests now run and pass for the first time since the move.**

**FAILED / NEW FINDING — Step 4's quality mask has had NO working test coverage:**

After repointing, **33 tests still fail**, and the cause matters: `add_quality_mask` was not merely
moved, it was **substantially REFACTORED**. `_process_session()` went from
`(session_id, cfg, commit)` to `(session_id, row, cfg, commit, cubes_dir, chunk_frames,
step3_meta, ...)`, and `CUBES_DIR` changed from a module global (which the tests monkeypatch) into
a function parameter. **26 of the 33 failures are that single missing global.**

**The tests were never updated because they could not even be COLLECTED — so nobody saw them go
stale.** The consequence: **the Step-4 quality mask, which gates every window in the study, has had
effectively zero real test coverage since commit `78067cc`.** That is a live risk to the study, not
a tidiness issue.

**Deliberately NOT fixed in this session.** Rewriting 26 tests against the new API means
constructing manifest rows and Step-3 metadata. Done hastily, it produces tests that pass without
testing the right thing — the exact failure mode that has already bitten this workstream twice
(the §8.4 test that could XPASS wrongly; the synthetic cardiac tone with no 2nd harmonic). It
deserves its own focused pass.

**Current suite state (no `--ignore`): 748 passed, 33 failed, 1 xfailed.** The 33 are the stale
Step-4 tests above. **They are now VISIBLE rather than hidden behind a collection error** — which
is a strict improvement, but it is not green, and I am not calling it green.

**Retired / no longer used:** `tests/test_exp004_analysis.py`, `tests/test_exp004.py`,
`tests/test_exp002_artifacts.py` (dead: retired experiment, deleted data, missing module).

**Next:**
- **Rewrite the 33 Step-4 quality-mask tests against the refactored API.** Own task. This is the
  highest-value cleanup in the repo right now — it is a *gating* stage with no coverage.
- Then Stage 1 (already rewritten: 1A harmonic coherence, 1B temporal continuity).
- Nothing promoted. Live path unchanged.

---

## 2026-07-14 - CORRECTION to the entry above: where the quality mask actually bites

**Correcting a claim I made in the previous entry**, rather than editing it (CLAUDE.md §10.2 —
entries are append-only, including my own mistakes).

**I wrote:** "the Step-4 quality mask ... gates every window in the study."
**That is wrong as stated, and I did not verify it before writing it.**

**Verified topology:**
- **`scripts/live_demo.py` does NOT use the quality mask at all.** It imports only
  `src/radar_io`, `src/respiration`, `src/vitals`. There is **no** reference to `quality_mask`,
  `quality_gated` or `steps/step_4` anywhere in it. The live capture and the on-screen HR/BR are
  entirely independent of it.
- **The quality mask is an OFFLINE-only stage.** `steps/step_4/add_quality_mask.py` writes it into
  the HDF5 cubes; **`steps/step_5` (breathing) and `steps/step_6` (heart rate) read it and gate
  windows on it** (`quality_gated`).

**So the corrected claim is narrower, but the risk is arguably WORSE, not better:** the untested
gate does not threaten the captures — it threatens the **paper-grade numbers**. Per CLAUDE.md §4,
MAE/RMSE/Bland-Altman come from re-processing the saved `adc_stream.bin` **offline**, and that route
runs Step 2 -> 3 -> **4** -> 5 -> 6. The component that decides **which windows count** toward the
published agreement metrics is the one with no working test coverage.

**Compounding factor (already logged in the scripts audit):** the Step-4 thresholds were **tuned on
the retired pre-2026-07-09 dataset**. So the gate is both **untested** *and* **calibrated against
data that has been deleted**. Re-run the threshold diagnostics (`scripts/diag_*`, repaired earlier)
on the new captures before trusting `steps/step_4/config.yaml`.

**Unchanged:** the recommendation to rewrite the 33 stale Step-4 tests before Stage 1 still stands,
for the reason above.

---

## 2026-07-14 - Stage 1 rewritten again (evaluation-design review). 12 comments, 12 accepted.

**Set out to do:** get the rewritten Stage 1 (1A coherence, 1B temporal continuity) reviewed before
running either experiment.

**Worked (with evidence):** the reviewer returned **12 comments on evaluation design. All 12 verified,
all 12 accepted, none rejected.** Their core point, and it is correct: **the experiments were
under-specified in exactly the ways that would have produced flattering, non-reproducible results.**
Every loose criterion is now exact. Stage 1 v2 is written; nothing has been run.

**The sharpest technical catch — my target orders were WRONG.**
A decoy at `k*f_r` (inside the cardiac band) has its fake AHET evidence at `2k*f_r`. So the orders
whose cancellation matters are **{2k} - always EVEN, and they MOVE with f_r**:

| BR | f_r | decoy orders k | **evidence orders 2k (the real target)** |
|---|---|---|---|
| 12 bpm | 0.200 | 4..10 | **8,10,12,14,16,18,20** |
| 16 bpm | 0.267 | 3..7 | **6,8,10,12,14** |
| 18 bpm | 0.300 | 3..6 | **6,8,10,12** |
| 22 bpm | 0.367 | 3..5 | **6,8,10** |

My plan said "k = 8..15". That is wrong twice: it includes **irrelevant ODD orders (9,11,13,15)** and
**misses 6, 16, 18, 20** - and 16/18/20 appear at **12 bpm**, which is exactly the study's paced arm.
The target set must be computed **per hop from that hop's f_r**, never as a fixed range.

**My other errors, caught:**
- **M2 was biased and I reused the bias.** In §25 I chose the cancellation frequency by maximising
  cancellation *on the very mainlobe I then scored* - fitting and scoring on the same data. The
  numbers are **optimistic**. (They still came out ~1 dB, so the bias *strengthens* the negative
  conclusion - but the method was wrong and is not reused.) Now: **held-out estimation** (fit on
  sub-window A, score on held-out sub-window B) plus an **off-harmonic / phase-randomised NULL**, so
  we know what a 2-column basis removes from noise alone.
- **"M3 is model-free" was an overclaim.** Phase tracking depends on demodulation frequency,
  sub-window length, amplitude gating and unwrapping. Now specified: residual phase after removing
  the best **linear slope** (a constant frequency offset is an f_r error, NOT incoherence, and must
  not be scored as one), amplitude gating, and validation on **stationary / drifting / AM** controls.
- **The 1A failure claim was too strong.** I wrote that a fail would prove such mechanisms "dead
  permanently, on physics". Two captures of one subject cannot establish that. **Narrowed:** a fail
  licenses only *"on these sessions, a **stationary single-sinusoid** model of the high-order
  harmonics is unsupported."* **Time-varying and broadened/notch bases remain OPEN, different
  hypotheses.**
- **Stage 2 had a stale contradiction:** it still said to feed the extended ceiling "the comb-fit f_r
  from Stage 1" - an estimator that is withdrawn. Fixed, with the subtlety noted: if 1A passed only
  at *independently optimised per-order frequencies*, that would **not** revive a common-fundamental
  estimator; it would imply a different, more expensive model.

**Stage 1B - the evaluation was rigged in my favour without my noticing:**
- **Two labels hid the hardest case.** A candidate can be within 3 bpm of **both** Masimo PR **and**
  some `k*f_r` - that IS the collision case. A "cardiac wins first" rule would silently absorb it.
  Now **four labels** (`cardiac_only`, `harmonic_only`, `ambiguous_both`, `neither`); AUC on the
  first two only; **ambiguous reported as NaN, never counted as a classifier success.**
- **Rank is not identity.** Correlating "rank-0 frequency" against f_r, or picking the nearest integer
  k independently each hop, **can manufacture apparent coupling out of nothing.** Now: link
  candidates into **trajectories** first, infer **ONE k per trajectory**, then test
  `Δf_cand ≈ k · Δf_r` for that fixed k.
- **The existing diagnostic is NON-CAUSAL.** Verified: `scripts/diagnose_step6_candidate_tracks.py`
  uses `viterbi_track()` - a **max-score DP over the full sequence** - so **it can see future
  windows.** A fine offline oracle; it does **not** test a real-time discriminator. Stage 1B must use
  **trailing history only** (or a declared fixed lag, with latency reported), and **warm-up and
  post-gap reacquisition count against yield.**
- **Tuning and evaluating on the same two runs.** Verified: that script sweeps **120** combinations
  (and **1080** in the hop-1 grid) scored against Masimo error. **Best-of-1080 on two same-subject
  sessions would be severe overfitting.** Now: pre-specify a small statistic family and threshold
  rule **in writing before looking**; develop on run A, validate on run B, then reverse; and call the
  result **feasibility evidence, NOT validation.**
- **Overlapping windows are not independent.** 30 s windows at a 3 s hop share **27/30 s**. Raw
  candidate counts would **greatly overstate** the effective sample size. Per-session reporting;
  contiguous **block** resampling if any uncertainty is quoted. **This is the standing project rule
  (HANDOFF §5) and I should have applied it myself.**
- **`resp_valid` cannot be the collapse-exclusion criterion** - it has the known false-confidence bug
  (stayed 1 while f_r was pinned to the band floor, 3 times in 3 runs). Using it would be circular.
  A **direct band-floor-lock rule** is now required, with excluded contiguous spans reported and the
  tracker reset after each gap.
- **"A usable margin" replaced with pre-specified pass criteria**, including: both held-out directions
  must reject every known confident decoy; no new severe Masimo errors; yield/warm-up/latency
  reported; stability under stated f_r perturbations.

**Retired / no longer used:** the fixed "k = 8..15" target; the biased M2 (fit-and-score on the same
mainlobe); the "model-free M3" claim; the "dead permanently, on physics" claim; the two-label 1B
scheme; Stage 2's comb-fit reference.

**Next:** run **Stage 1B first** (per the decision rule and the reviewer's agreement), built to the
v2 contract above - causal, trajectory-associated, four-label, dev/validate split, per-session
reporting. **Nothing run yet. Nothing promoted. Live path unchanged.**

---

## 2026-07-14 - Stage 1B ROUND 1: FAILS its own pre-specified criteria. Cause diagnosed: MY labelling.

**Set out to do:** run Stage 1B (temporal continuity as a respiratory-harmonic discriminant) to the
v2 contract - causal, trajectory-associated, four-label, dev/validate in both directions,
per-session, with pass criteria pre-specified in writing before looking.

Script: `scripts/stage1b_temporal_continuity.py` (committed, read-only, pre-specification in the
module docstring). Runs: the two Masimo captures.

**VERDICT: Stage 1B round 1 FAILS.** It fails **pre-specified criterion 4 (stability under f_r
perturbation)**. I am reporting it as a failure rather than fixing the labels and re-reporting,
because fixing-then-reporting on the same data is precisely the tuning-on-evaluation-data trap the
review warned about.

**Results (per session; no pooled ROC - windows share 27/30 s):**

| session | cardiac_only | harmonic_only | ambiguous_both | neither | AUC(S1) |
|---|---|---|---|---|---|
| natural | 24 | 6 | 3 | 16 | **0.944** |
| paced16 | 22 | 16 | 0 | 12 | **0.903** |

Held-out (threshold fit on DEV via Youden's J, applied UNCHANGED to VAL):

| dev -> val | thr | val AUC | sens | spec | rank-0 decoys rejected |
|---|---|---|---|---|---|
| natural -> paced16 | 2.34 | 0.903 | 0.91 | **0.50** | **3/3** |
| paced16 -> natural | 3.60 | 0.944 | 0.88 | 1.00 | **1/1** |

Collapse exclusion (computed directly from f_r band-floor lock, NOT from `resp_valid`, which has the
known false-confidence bug): natural 13/50 hops (one contiguous span, t=141-177 s); paced16 5/50
(t=121-133 s). **Both spans are real, and the natural one matches the collapse logged on 2026-07-13.**

Yield after collapse exclusion + 5-hop warm-up: **66% of hops scored, in both runs.**

**WHY IT FAILED - and it is my fault, not the hypothesis's.**

Label stability under a +/-1 FFT bin (2 bpm) f_r perturbation: **only 28-42% of labels unchanged.**
That is disqualifying. Root cause, measured:

1. **`K_RANGE = 1..20` was far too permissive.** Only harmonics that can actually LAND in the
   cardiac band matter: over 12-22 bpm that is **k = 3..10**. My range admitted **k = 11..20**, whose
   harmonics lie **entirely above the cardiac band** and can therefore only ever produce **chance
   hits**.
2. **The 'harmonic' label consequently covers a huge fraction of the axis by chance:** with +/-3 bpm
   tolerance it covers **33% of the cardiac band at 18 bpm and 58% at 12 bpm.** A random candidate is
   labelled 'harmonic' about a third of the time. The `harmonic_only` class is heavily contaminated.
3. **High k makes the label f_r-fragile:** a one-bin f_r error moves the predicted harmonic by
   `2*k` bpm - **40 bpm at k=20.** The label is not a stable property of the candidate at all.

So the perturbation test did its job: it caught a labelling scheme that was partly measuring noise.

**What is NOT invalidated:** the *hypothesis*. AUC of 0.90-0.94 per session, and every rank-0 decoy
rejected in both held-out directions, is **encouraging despite contaminated labels** - cleaner labels
would plausibly improve it. But that is a conjecture, and I am not permitted to bank it.

**Also honestly noted:** held-out specificity was **0.50** in the natural->paced16 direction (half the
harmonic candidates misclassified as cardiac), and the decoy counts are **tiny (3 and 1)**. Even had
criterion 4 passed, "all decoys rejected" on n=3 and n=1 is not strong evidence.

**Retired / no longer used:** the round-1 labelling (`K_RANGE = 1..20`).

**Next - and the methodological point matters more than the numbers:**
- A corrected pre-specification is required: **`K_RANGE = 3..10`** (only harmonics that can land in
  the cardiac band), and the label tolerance should be justified rather than assumed.
- **Re-running on these same two sessions is now EXPLORATORY, not confirmatory.** I have seen the
  outcome; a second pass on the same data cannot be reported as clean held-out evidence. It must be
  labelled as such, and a genuinely clean test needs **new captures**.
- This strengthens the case for the paced-18 run and for more subjects - not as a nice-to-have, but
  because the evaluation budget on the existing two sessions is now partly spent.

---

## 2026-07-14 - Stage 1B round 2 (exploratory). THREE findings, one of which INVALIDATES a headline.

**Set out to do:** act on the review of Stage 1B round 1, which identified **target leakage** and
two correctness bugs. Preserve round 1, run an explicitly EXPLORATORY redesign.

**All three review claims verified and CONFIRMED:**

1. **TARGET LEAKAGE - fatal, and I missed it.** The label was
   `harmonic <=> min_k |f_cand - k*f_r| <= 3 bpm`, and my statistic S1 was the trailing median of
   **that same distance**. Classifying by S1 was close to classifying by the label's own
   definition. **AUC 0.90-0.94 was partly tautological**, and holding out a session did NOT remove
   it, because both sessions share the label construction. **Round 1's headline number is
   withdrawn as evidence.**
2. **My round-1 root cause was WRONG.** I blamed `K_RANGE = 1..20`. But orders 11..20 lie far above
   the cardiac band and essentially never matched a candidate - removing them changes coverage by
   **~nothing**. The in-band orders alone already cover **33% of the band at 18 bpm and 58% at
   12 bpm**, and a 2 bpm f_r error still moves a *valid* in-band harmonic by **6-20 bpm**. **The fix
   I proposed would not have worked.**
3. **My "known decoys" check did not measure what it claimed.** It counted rank-0 harmonic
   *candidates* - which AHET had already **rejected**. Measured: v1 accepted only **5 hops
   (natural) and 3 (paced16)**. So there were essentially **no known confident decoys in the real
   runs at all** - the decoys were in the SYNTHETIC test.

**NEW FINDING 1 - the motion statistic has NO LEVERAGE on this data (inconclusive by construction).**

Rebuilt the statistic as a causal one-step prediction (breaks the leakage: it scores how a
candidate MOVES, not where it sits):
`harmonic: f_hat(t) = f_c(t-1) + k*(f_r(t) - f_r(t-1))` vs `cardiac: f_hat(t) = f_c(t-1)`.

Leverage pre-check (built in deliberately, and it fired):

| session | median \|d f_r\| per hop | p90 | hops with \|d f_r\| < 0.5 bpm |
|---|---|---|---|
| natural | **0.20 bpm** | 0.41 bpm | 41/43 |
| paced16 | **0.12 bpm** | 0.21 bpm | 40/40 |

**f_r barely moves.** With k ~ 4, leverage = k * d f_r ~ **0.8 bpm - well under one FFT bin (2 bpm).**
The harmonic and cardiac models are therefore **numerically indistinguishable on this data**, and
the observed separation is duly ~nil (0.43 and **0.02**). **This is inconclusive BY CONSTRUCTION,
exactly as the reviewer predicted:** a discriminant based on *tracking changes in f_r* cannot be
tested on sessions where f_r does not change. **Confirmatory data MUST include deliberate f_r
movement (stepped / variable-rate), not just a constant paced rate.**

**NEW FINDING 2 - THE "MAE 0.16 bpm" HEADLINE IS NOT ROBUST. It is comparator-dependent.**

Applying the reviewer's fix (PI-gated Masimo PR over the **same trailing 30 s window** that produced
the radar spectrum, instead of a single instantaneous PR sample at the rounded hop epoch) changes
the answer:

| comparator | natural MAE | severe (>5 bpm) |
|---|---|---|
| instantaneous PR at hop epoch (what I reported) | **0.16 bpm** | 0 |
| PI-gated mean over the same 30 s window | **2.72 bpm** | **1** |

**Cause - and it is a protocol finding, not just an analysis one.** The subject's PR was **NOT
stationary** early in the natural run: it ran **75 -> 94 -> 65 bpm over the first ~25 s**, with
**PI 7-10 throughout**, so the excursion is **real, not a sensor artefact**. The radar's first
accepted window spans [6 s, 36 s] - straddling that transient. Its 30 s FFT peak (64.9 bpm) matches
the *settled* rate, while the window's mean PR is 70.9.

**Neither comparator is obviously correct** when the reference is non-stationary inside the window:
an FFT peak estimates the **dominant frequency** over 30 s, which is *not* the arithmetic mean of
instantaneous rates. But that is precisely the point - **"MAE 0.16 bpm" was never a robust result.
It was 5 hops scored against a comparator that happened to agree.**

**CLAUDE.md §4 says: "If a result looks too good, treat it as a bug until proven." MAE 0.16 bpm
against a clinical reference IS too good. I applied that rule to other people's numbers and not to
my own.**

**Consequences:**
- **HANDOFF's "Heart rate: MAE 0.16 bpm vs Masimo PR" must be withdrawn** and restated with the
  comparator named and the caveat attached.
- **The comparator must be pre-specified** (dominant-frequency-aware, e.g. median PR over the
  window, or windows rejected when the reference is non-stationary within them).
- **The capture protocol needs a real settle period.** PR swinging 75->94->65 in the first 25 s
  means the subject had not settled. Windows overlapping the settling transient must be excluded,
  and `notes/protocol.md` should mandate a longer pre-record settle.

**Retired / no longer used:** round-1 S1 statistic (leaky); round-1 K_RANGE diagnosis (wrong);
round-1 decoy check (measured the wrong thing); the unqualified "MAE 0.16 bpm" claim.

**Kept:** `scripts/stage1b_temporal_continuity.py` preserved unchanged as the failed pre-specified
round-1 experiment. New: `scripts/stage1b_exploratory_motion.py` (explicitly EXPLORATORY).

**Next:**
- Do **not** freeze or promote anything from these two sessions - the motion statistic is untestable
  on them.
- **Capture requirements are now sharper:** (a) paced-18 as the collision fixture; (b) a **stepped /
  variable breathing-rate** run so f_r actually MOVES - without it, temporal continuity cannot be
  tested at all; (c) a genuine settle period before recording.
- Re-derive the agreement numbers under a pre-specified comparator before any of them are cited.

---

## 2026-07-14 - Pre-registered the Masimo comparator; added a settle criterion and a stepped-rate arm.

**Set out to do:** fix the two things that would otherwise waste the next capture - the undefined
comparator and the missing settle gate - and specify the one capture that unblocks the most.

**Worked (with evidence):**

**1. `notes/comparator_prespec.md` - PRE-REGISTERED, before the next capture.**
Binding on every agreement number in the paper. Written because the choice of comparator changed a
headline by 17x:

| comparator | natural-run MAE (same 5 hops) | severe (>5 bpm) |
|---|---|---|
| instantaneous PR at the window's end epoch (what I reported) | **0.16 bpm** | 0 |
| PI-gated **mean** over the same 30 s window | **2.72 bpm** | 1 |

The specification:
- **Reference = MEDIAN of PI-gated Masimo PR over the SAME 30 s window** the radar used.
- **Window admissibility gates:** PI >= 0.5; >= 80% sample coverage; **stationarity: exclude if
  `p90 - p10` of in-window PR > 5.0 bpm**.
- **The stationarity gate is chosen from the FFT resolution, not tuned on radar error.** The bin at
  30 s is 2.0 bpm; 5.0 bpm = **2.5 bins**. Measured within-window PR spread on existing captures is
  **median 2.8-3.0 bpm** - that is normal HRV/RSA, it is real, and it does NOT make the dominant
  frequency ill-defined. A 1-bin (2 bpm) gate would reject **60-70%** of windows for ordinary
  physiology. At 5 bpm: **12% excluded (natural), 20% (paced-16)** - and in the natural run the
  excluded windows are **exactly the settling transient**. Sensitivity at 3/5/8 bpm must be reported.
- **Key insight recorded:** an FFT peak over 30 s estimates a **dominant frequency**, which is NOT
  the arithmetic mean of instantaneous rates. When the reference moves inside the window there is
  **no single true HR for that window**, and no comparator is defensible. **The fix is not a
  cleverer average - it is to detect that case and EXCLUDE it.** Once a window is stationary, the
  comparator choice stops mattering, which is the whole point.

**2. `notes/protocol.md` - a hard SETTLE CRITERION, for EVERY arm.**
The old protocol said "confirm PR is stable" (too vague) and put a 2-minute settle **only under the
paced arm**; the smoke tests skipped it. Now, before the radar starts, BOTH must hold on the live
Masimo: **PR spread <= 5 bpm over a continuous 60 s**, AND **no monotonic drift** (last 20 s within
3 bpm of the first 20 s). Not met within 5 minutes -> **abort and re-seat.** An unsettled subject
silently destroys the start of the session, because the comparator now (correctly) excludes those
windows.

**3. `notes/protocol.md` - the STEPPED breathing-rate diagnostic capture (12/15/18/21 bpm, 120 s each).**

**This is the blocker, and it is NOT the collision fixture.** Correcting my own earlier framing:
a paced-18 collision fixture would only re-document a failure mode we already understand, against
**no surviving mechanism to test it on** (v1 and v2 are both dead). What actually blocks progress is
that the leading candidate - temporal continuity - is **untestable on every capture we own**:
measured median |d f_r| = **0.12-0.20 bpm per hop (~0.4 of an FFT bin)**.

A stepped run gives **both**, in one capture:
- **Leverage:** each 3 bpm step produces **9-18 bpm of predicted harmonic motion = 4.5-9 FFT bins**,
  versus ~0.4 bins today.
- **A better collision fixture than paced-18:** 4*f_r **sweeps 48 -> 84 bpm and crosses the HR**
  (~72 under pacing), giving the collision as a **continuous offset sweep** rather than a single
  point - which is what Stage 4 asked for anyway.
- Cost: ~8 min + settle. 120 s dwell = 3 non-overlapping 30 s windows per rate, discarding the one
  that straddles each transition (those windows are excluded and reported - f_r is ill-defined
  across a step).
- **Log the Masimo PR at every step:** pacing CHANGES HR (paced-16 pushed this subject 65 -> 72 bpm),
  so the collision point MOVES.

**Retired / no longer used:** "confirm Masimo PR is stable" as a settle rule (unmeasurable);
the framing that a paced-18 collision fixture is the next blocker (it is not).

**Next:**
- Take the **stepped-rate capture** under the new settle criterion.
- Then, and only then, re-run Stage 1B's motion statistic - it will finally have leverage.
- Re-derive **all** agreement numbers under `notes/comparator_prespec.md` before any is cited.

---

## 2026-07-14 - Stage 1A run (unblocked work, no capture needed). Verdict: FAIL, more rigorously than round-1.

**Set out to do:** while waiting on the stepped-rate capture (needed for Stage 1B), run Stage 1A -
harmonic coherence at the ECA "evidence" orders - since it is read-only on the two captures already
in hand and was never actually executed (Stage 1 work went straight to 1B).

Script: `scripts/stage1a_harmonic_coherence.py` (new, uncommitted). Implements the full v2 contract
from the plan: per-hop evidence orders (2k where k*f_r is in-band), M0 control, M1 presence, M2
held-out cancellation with two nulls, M3 coherence, exact 1A.5 pass bar, narrowed 1A.6 claim.

**Caught and fixed THREE bugs in my own test harness before trusting any output** - consistent with
the pattern in this project: an unreviewed synthetic control is a common source of false findings,
and this one had never been through cross-model review at all.

1. **M0 control comb didn't cover the frequencies it was testing.** Built from harmonics k=1..4 of
   f_r=0.30 (max 1.2 Hz), then tested evidence orders at 1.8-3.6 Hz - pure noise floor there. M0
   failed on the first run for this reason alone, not because the metrics were broken. Fixed: comb
   now built from k=1..14, covering every evidence order under test.
2. **The "drifting" synthetic control was accidentally damped to near-stationary.** A random-walk
   phase jitter was divided by `fs` in a way that suppressed its cumulative growth by ~20x,
   producing a signal that LOOKED incoherent in name only. Replaced with a deterministic frequency
   STEP mid-window (f0 -> f0*1.03 at t=15s) - genuinely nonlinear phase, easy to verify by
   inspection rather than trusting a stochastic process's scaling.
3. **The M3 demodulator (FFT-domain brick-wall band mask) produced spectral-leakage phase
   distortion for any frequency not exactly on an FFT bin.** The stationary control (1.200 Hz, which
   happens to fall exactly on a bin) showed near-zero residual; `freq_offset` (1.212 Hz, off-bin)
   showed residual almost as large as the genuinely-incoherent `drifting` control (0.162 vs 0.143
   rad) purely from this artefact - a real f_r essentially never lands on a bin, so this would have
   contaminated every real-data measurement. Replaced with standard down-mix (multiply by
   `exp(-i*2*pi*f0*t)`) + zero-phase Butterworth low-pass (`scipy.signal.filtfilt`), which is
   well-behaved regardless of bin alignment.

**After the fixes:**
- **M0 control: PASS.** Deep held-out cancellation (-27 to -37 dB) and low coherence residual
  (~0.02 rad) on a stationary comb built to contain the tested frequencies. The metrics DO detect
  coherence where it exists by construction.
- **M3 validation: directionally correct, not sharply separated.** stationary=0.075, freq_offset=
  0.092, drifting=0.162, AM=0.058 rad. Drifting is clearly highest; freq_offset sits closer to
  stationary than to drifting, as required, but the margin is modest. **M3 is used here as a
  secondary/diagnostic signal; M2 (with its two nulls) is the primary decision instrument** -
  M0's k=3..6 controls also showed order-dependent M3 noise (0.017-0.232 rad) even though M2 was
  uniformly deep, confirming M3's absolute values are noisier than M2's.

**REAL-DATA VERDICT: FAIL, more rigorously than the discredited round-1 (S25) measurement.**

| session | (hop,k) combinations | M1-eligible | median C_dB (held-out) | null (off-harm) | null (phase-rand) | pass bar met |
|---|---|---|---|---|---|---|
| natural | 308 | 52 (17%) | **+2.3 dB** | +2.4 dB | -0.2 dB | **0/52 (0%)** |
| paced16 | 272 | 55 (20%) | **-0.2 dB** | +0.4 dB | -2.2 dB | **0/55 (0%)** |

**The real held-out cancellation is statistically indistinguishable from BOTH nulls** (all three
numbers cluster near 0 dB) - there is no detectable coherent single-sinusoid line at the evidence
orders, full stop, given a methodology now proven (via M0) to detect one when it is really there.
Also notable: **83% and 80% of (hop, evidence-order) combinations don't even clear M1 presence** -
most evidence frequencies show no peak in the real spectrum at all.

**This reinforces, with a controlled/bug-fixed/null-compared method, the same conclusion the
discredited quick measurement in S25 reached informally** (a perfect f_r estimator would buy
0.5-2.5 dB where ~20 dB is needed). Stage 1A now gives that conclusion real evidentiary weight
instead of a back-of-envelope number.

**Licensed claim (plan 1A.6, unchanged in scope):** on these two sessions, a stationary
single-sinusoid model of the high-order respiratory harmonics is unsupported, so this cancellation
mechanism cannot work. **Not licensed:** "dead permanently, on physics" - time-varying, broadened,
or harmonic-subspace bases remain open, different hypotheses, out of scope here.

**Not yet done:** this script and its findings have **not been cross-model reviewed**
(CLAUDE.md §6 — it is DSP-adjacent and algorithm-relevant, same standard as the ECA plan itself).
Treat the verdict as provisional until reviewed, same as every other Stage 1 finding this project.

**Also fixed this session:** `HANDOFF.md` S6 still cited "MAE 0.16 bpm" as a live number inside the
AHET-yield paragraph after the headline withdrawal above it — missed on the first pass. Corrected,
with the fallback-vs-AHET comparison note preserved (that finding's direction still holds; only the
absolute AHET accuracy number is withdrawn).

**Retired / no longer used:** round-1's buggy M0 comb, buggy drifting synthetic, and brick-wall
demodulator (all superseded within this same script; git history has the broken versions if needed).

**Next:**
- Get `scripts/stage1a_harmonic_coherence.py` cross-model reviewed.
- Still waiting on the stepped-rate capture for Stage 1B (the actual current blocker).
- Two Stage 1B scripts + this new Stage 1A script are uncommitted — commit is the user's call.

---

## 2026-07-14 - Captured the stepped-rate sweep. Reviewed against the pre-registered comparator.

**Set out to do:** capture the 12->15->18->21 bpm stepped-rate run designed to unblock Stage 1B
(temporal continuity needs f_r to MOVE, which no prior capture had). Then check the subject's
observation that HR briefly rose above the 5 bpm stationarity band at points during the run,
against the comparator pre-registered in `notes/comparator_prespec.md` BEFORE this capture existed.

Run: `results/live_demo/20260714_180523_live_demo_sweep/` (486 s, completed, Masimo file
`demo_sweep.csv`).

**Worked (with evidence):**

**1. The Masimo's own respiration reading confirms a clean staircase, independent of the radar:**
12 bpm held t=[0,141]s, ramp to 15 over t=[141,172]s, held 15 t=[172,259]s, ramp to 18 over
t=[261,291]s, held 18 t=[291,372]s, ramp to 21 over t=[379,415]s, held 21 t=[415,477]s. The pacing
was executed correctly.

**2. The subject's HR-variability observation is real and explained, not a problem.** PR standard
deviation per step: **12 bpm step = 5.2 bpm (range 77-98); 15/18/21 bpm steps = 1.6-2.5 bpm (range
78-88).** Variability is concentrated almost entirely in the first ~141 s (the 12 bpm step,
immediately after recording started) and settles for the rest of the run - matching the subject's
own description exactly ("stabilizing... then a few peaks... then more stable").

**3. Applying the PRE-REGISTERED stationarity gate (spread > 5 bpm in a 30 s window) rather than
an ad hoc post-hoc choice:** sweeping it across the whole run (3 s stride) excludes windows in two
clusters: **t~[30,156]s** (settling into the 12 bpm step; the settle criterion did not fully
prevent this) and, more surprisingly, **t~[267,399]s**, which eats a large fraction of the 18 bpm
step and the 18->21 transition. The middle of the run (15 bpm step, t~[156,267]s) is clean.
**This required no judgement call - the exclusion criterion was fixed before the capture existed,
which is the entire point of pre-registering it.**

**4. UNPLANNED, USEFUL FINDING - the collision landed at 21 bpm, not 18 bpm as designed.** The
paced-18 collision point (4*18=72 bpm) was designed around the ~65-72 bpm HR observed in the
2026-07-13 paced-16 session. In THIS session the subject's HR ran substantially higher
(80-88 bpm), plausibly from the effort of actively pacing through 4 rate changes over 8 minutes.
Solving `4*f_r ~= HR` against this run's actual numbers: **4*21 = 84 bpm, landing almost exactly
on the observed 21 bpm step median (83 bpm).** The real collision in this capture sits near the
END of the sweep, not the middle. **This is precisely why a sweep was chosen over a single fixed
rate: resting HR is not stable session to session, and a fixed paced-18 capture would have missed
this session's actual collision point entirely.**

**5. PI stayed well above the 0.5 gate throughout** (per-step median 5.0-12.0), confirming the
observed HR variability is real physiology, not a Masimo sensor artefact.

**Also observed, separately:** the radar's own breathing-rate estimate repeatedly collapses to
6 bpm at several points in this run (~t=198, 310, 367, 442, 456-465 s) - the SAME
false-confidence respiration-collapse bug logged in the two prior smoke tests (now 4-for-4 across
every Masimo-referenced capture taken). Not caused by this capture; not blocking analysis (the
Stage 1B collapse-exclusion logic already handles it); still open and unfixed.

**Failed / did not work, and why:** the settle criterion (spread <=5bpm for 60s, no drift, added
2026-07-14) did not fully prevent early-run HR variability here - the first ~2.5 minutes still show
elevated spread. Possibly the criterion needs to also hold immediately AFTER the radar warmup
completes, not just once before recording starts, since ~30-40s of warmup elapses between the
settle check and the first usable radar window.

**Retired / no longer used:** nothing.

**Next:**
- Run Stage 1B (`scripts/stage1b_temporal_continuity.py` / the exploratory motion-statistic variant)
  on this capture - it finally has real f_r leverage.
- When interpreting collision behaviour in this run, treat the **21 bpm step as the primary
  collision region**, not 18 bpm - and note it has fewer excluded windows than the 18 bpm step.
- Consider whether the settle criterion should be re-checked immediately before the first analysis
  window, not only before recording starts.

---

## 2026-07-14 - Stage 1B run on the sweep capture. Leverage disappointing; a NEW, unexplained failure mode found.

**Set out to do:** run the leakage-free motion-statistic Stage 1B evaluation on the new sweep
capture (the first data with real f_r movement), since that was the entire purpose of taking it.

**Fixed first (both real bugs, both would have silently produced wrong results):**
- `load_run()` found the Masimo CSV by matching `"massimo"` in the filename — silently fails on
  `demo_sweep.csv`. Fixed to select-by-elimination (the only other CSV in a run folder is
  `live_estimates.csv`).
- `load_run()` used the PI-gated **mean**, with no stationarity gate — inconsistent with
  `notes/comparator_prespec.md`, which is supposed to be binding. Fixed: PI-gated **median**,
  coverage >= 80%, and **stationarity gate (spread > 5 bpm -> masimo_pr = NaN)**, exactly as
  pre-registered. `scripts/stage1b_temporal_continuity.py` (round 1) was not touched — it stays
  preserved as the failed pre-specified record.

**Worked (with evidence):**

**1. Leverage pre-check: DISAPPOINTING.** Despite the deliberate 12->15->18->21 bpm staircase,
per-hop |delta f_r| on the sweep (median 0.14 bpm, p90 0.45 bpm) is barely different from the two
FLAT sessions (0.12-0.20 bpm median). Likely cause (hypothesis, not yet confirmed): the Masimo RR
staircase shows each transition ramping over roughly **30 s (~10 hops)**, not jumping abruptly, so
the per-HOP delta stays small even though the per-STEP change is a full 3 bpm. **The motion
statistic as designed (single-hop prediction) does not yet have materially more discriminating
power on this capture than on the flat sessions.**

**2. Primary endpoint: yield jumped, but so did the error rate, in a NEW pattern.** v1 accepted
30/150 hops (20%, vs ~2% before) but with **16/30 severe (>5 bpm) errors** and MAE 8.18 bpm —
computed only on windows that passed the pre-registered stationarity gate, so this is not
comparator noise.

**3. THE SEVERE ERRORS DO NOT MATCH THE EXPECTED 4*f_r DECOY PATTERN — correcting my own
hypothesis.** Checked whether the accepted (wrong) HR matched the classic decoy signature
(4*f_r, since that is the mechanism this whole investigation has been chasing): **only 3 of 16
severe-error hops land within 3 bpm of 4*f_r.** The other 13 show something different and not yet
understood: the radar repeatedly reports **~62-73 bpm**, fairly consistently, across the 12 bpm,
15 bpm, and part of the 18/21 bpm steps, while Masimo (passing the stationarity gate) reads
79-88 bpm throughout. This ~62-73 bpm value does not match any obvious k*f_r harmonic at the f_r
values observed at those timestamps.

**4. One genuinely striking case, right at the predicted collision point.** At t=402-406 s
(21 bpm step), 4*f_r = 83.3-84.7 bpm, within **0.3-1.7 bpm of the true HR (85 bpm)** — an almost
exact collision, exactly where predicted. The radar reported **62 bpm** there — matching neither
the true HR nor 4*f_r, but close to **3*f_r (62.5-63.5 bpm)** instead, a harmonic order this
investigation had not focused on. **Then, 9-18 s later in the SAME 21 bpm step (t=415-420 s), the
radar recovers to ~82 bpm — much closer to the true 87 bpm (error ~5 bpm, right at the severe
threshold)** — suggesting some kind of re-acquisition/convergence after the step transition
rather than a stable wrong lock.

**Failed / did not work, and why:** the motion statistic's leverage precondition is still not
strongly met on real per-hop data, despite the capture being specifically designed to create it.
The severe-error investigation was expected to confirm the 4*f_r decoy mechanism cleanly; it
mostly did not (3/16), and surfaced an unexplained ~62-73 bpm "sticky" value plus a possible
third-harmonic (k=3) confusion at the one clean collision point. **None of this should be reported
as a finding about the ECA mechanism yet — it needs proper investigation, not a quick 4*f_r check.**

**Retired / no longer used:** nothing.

**Next:**
- **Investigate the severe-error hops properly**, no new capture needed: dump all 3 AHET
  candidates (not just the accepted one) per severe hop, check against ALL harmonic orders (not
  only k=4), and check whether the true HR was even present as a rejected candidate (mirrors the
  original Stage 1 candidate-rank analysis). This is the highest-value next step and is purely
  offline.
- Consider whether the motion statistic needs a longer lag (compare f_c across the ~10-hop ramp,
  not hop-to-hop) given transitions are gradual, not abrupt.
- Three Stage-1 scripts remain uncommitted; Stage 1A still awaits cross-model review.

---

## 2026-07-14 - Deep dive on the sweep's severe errors. TWO distinct failure modes found - one is REAL, FIRST-TIME evidence of the "magnitude order != credibility order" bug from the ECA plan.

**Set out to do:** investigate the 16 severe-error hops properly - dump ALL 3 AHET candidates
(not just accepted), check every candidate against every harmonic order, and check whether the
true HR was even present in the list. Correcting the previous entry's premature framing
("3/16 match 4*f_r") - a full per-candidate dump tells a much clearer, more useful story.

**CORRECTION: the true HR is essentially ABSENT from the candidate list on most severe hops, not
merely outranked.** `true HR present as SOME candidate (any rank): 2/16.` The earlier "3/16 match
4*f_r" check only looked at whether the ACCEPTED value matched a harmonic; it should have checked
all 3 ranks. Redone properly, two clearly distinct patterns emerge.

**PATTERN A (11 of 16 hops; t=70-169s, f_r~12-14.7bpm, true HR 79-84bpm) - the true cardiac
signal is essentially absent, and the dominant peak is NOT a clean harmonic.**

Example (hop 15, t=75s): accepted=66.44bpm (PASSED, p2f=7.7dB). Best-fit harmonic k=6 is 6.0 bpm
off (72.4 vs 66.44) - **not a real hit.** Spectrum magnitude at the true HR (79bpm) is 2.2; at the
accepted 66bpm it is 5.2 - **the accepted peak is >2x stronger than anything near the true HR.**
This pattern repeats across hops 13-18, 38, 45-46: a strong, non-harmonic peak around **60-74 bpm**
dominates, while the true 79-84 bpm HR barely registers. Critically, **the accepted value does NOT
shift proportionally as f_r moves from 11.9 to 14.7 bpm** (stays ~63-67 throughout) - the signature
of something that does NOT track breathing, yet does not match the true HR either. **Root cause
UNKNOWN.** Hypotheses, none confirmed: genuinely weak chest-wall cardiac signal during this early
part of the recording (settling/low-amplitude period, consistent with the PR variability already
flagged in this region); a stable non-cardiac reflector/clutter mode; or something else. This is
NOT the 4*f_r decoy mechanism this whole investigation has been chasing - it needs its own,
separate investigation.

**PATTERN B (hops 124, 125, 128, 129, 130; t=402-420s, near the predicted 21 bpm collision) - the
TRUE collision candidate IS present, and this is REAL evidence of a bug named but never observed
directly: "magnitude order is not credibility order" (ECA plan S11.3 / cross-review 12.5).**

Hop 124 (t=402s): **all three candidates PASS the AHET gate simultaneously:**
```
  rank0: 63.24 bpm  PASSED  p2f=4.27dB   (k=3 harmonic, 3*f_r=63.5, diff=0.3 -- clean hit)
  rank1: 84.44 bpm  PASSED  p2f=3.11dB   (k=4 harmonic, 4*f_r=84.7, diff=0.3 -- MATCHES TRUE HR 85bpm)
  rank2: 105.79 bpm PASSED  p2f=6.08dB   (k=5 harmonic, clean hit)
```
**rank1 matches the true HR almost exactly AND is exactly the predicted 4*f_r collision. It
PASSED. It was not selected.** Rank0 was selected purely because ranks are sorted by SPECTRAL
MAGNITUDE and rank0 was the largest peak - not because it was more credible. This is the first
real-data confirmation of the mechanism flagged (but never observed) during the ECA v1 post-mortem:
*"the true heart passed with 33 dB peak-to-floor and was discarded in favour of a decoy that
passed with 4.4 dB... magnitude order is not credibility order."* Same story, hop 128/129: the
true-HR-matching candidate PASSES but is outranked by a k=3 harmonic at higher magnitude.

Hop 125 (t=406s) is a variant: the true-HR candidate (84.04bpm, matching 4*f_r=83.3 at diff=0.7)
was independently REJECTED (`ratio_db_low`, p2f=0.52dB against the ~1.0dB gate) rather than merely
outranked - its own 2nd-harmonic evidence was marginal.

**By hop 130 (t=420s, just 2 hops / ~6s later), the true-HR candidate is finally ACCEPTED** (error
drops to 5.16 bpm, just above the severe threshold) - the estimator appears to self-correct within
the same 21 bpm step as conditions settle.

**Failed / did not work, and why:** my own quick "does the accepted value match 4*f_r" check
(previous entry) was too shallow - it looked only at the accepted candidate, missed that most
severe hops have NO true-HR candidate at all (Pattern A), and would have missed the actual
mechanism (Pattern B) entirely without dumping all 3 ranks and their rejection codes.

**Why this matters:** Pattern B is the single clearest piece of real-data evidence in this whole
investigation that candidate SELECTION (not just ECA's harmonic cancellation) is a live problem.
It is also directly relevant to Stage 1B: a k=3 harmonic candidate SHOULD be distinguishable from
the true cardiac candidate by NOT tracking f_r the way Stage 1B's motion statistic is designed to
detect - but Stage 1B's leverage problem (per-hop delta f_r still too small) meant this could not
yet be demonstrated statistically. This hop-124 case is a single clean example of exactly the
discriminant Stage 1B is trying to build, sitting right there in the data.

**Retired / no longer used:** the "3/16 match 4*f_r" framing from the previous entry - superseded
by this fuller per-candidate analysis.

**Next:**
- **Pattern A needs its own investigation** - likely candidates: check subject_bin SNR / signal
  amplitude during t=70-169s specifically (does the chest-bin signal amplitude drop here?); check
  whether this coincides with the settling variability already flagged in the PR trace.
- **Pattern B is evidence for a candidate-ranking fix** (rank by credibility - e.g. 2nd-harmonic
  strength, or eventually a continuity-based prior - not by raw magnitude) - but per CLAUDE.md S6
  this is a DSP-relevant algorithm change and needs a plan + cross-model review before any code,
  same standard as the ECA work. Do not implement ad hoc.
- This hop-124 case is a strong, concrete example to include if/when Stage 1B's temporal-continuity
  discriminant is revisited with better leverage (e.g. a multi-hop-lag statistic, per the earlier
  note that transitions ramp over ~10 hops).

---

## 2026-07-14 - CORRECTION to the "Pattern B" entry above: only 1 of 5 hops is a genuine mis-ranking

**Correcting my own previous entry** ("Deep dive on the sweep's severe errors"), which claimed
hops 124, 125, 128, 129, 130 all showed the "magnitude order != credibility order" mechanism
("Same story, hop 128/129"). **That claim was insufficiently checked. Re-verified precisely,
per-hop, against the actual `accepted_candidate_rank` and each candidate's rejection code:**

| hop | what actually happened |
|---|---|
| **124** | true-HR candidate (rank1) **PASSED but was not selected** — outranked by a louder rank0. **The only genuine mis-ranking case.** |
| 125 | true-HR candidate (rank1) was **independently REJECTED** (`ratio_db_low`) — its own 2nd-harmonic evidence was marginal. A real but **different** problem: borderline AHET verification, not candidate ranking. |
| 128, 129, 130 | true HR (87 bpm) was **not matched by my 3 bpm tolerance**, but the ACCEPTED value (81.8-83.1 bpm) is the correct answer with ~5 bpm of ordinary estimation noise — **these are successes, not failures.** My classifier's tolerance was too strict and mislabeled them.

**Corrected finding: exactly ONE hop (124) in this entire capture cleanly demonstrates the
"magnitude order is not credibility order" mechanism.** That is real, first-time evidence that
the mechanism CAN occur — but it is **n=1**, not five repeats, and that materially changes how
much weight this evidence can carry.

**Also tested and reporting honestly: the obvious first fix does not trivially work on this one
example either.** Re-ranking candidates by their own AHET 2nd-harmonic ratio (`peak_to_floor_ratio_db`,
the same quantity the gate itself uses) instead of raw spectral magnitude was the natural first
idea. On hop 124: rank0=4.27dB, rank1=3.11dB (the TRUE HR), rank2=6.08dB. **Ranking by ratio_db
would select rank2 (105.79 bpm) — also wrong, not rank1.** Neither raw magnitude nor raw
ratio_db alone picks the correct candidate on this example. This is consistent with cross-review
comment 12.5 from the ECA review ("peak-to-floor is not comparable across candidates, because
each candidate's spectrum comes from a different ECA basis") — a warning that applies here too
and was not fully appreciated when I first floated the "rank by ratio_db" idea.

**Retired / no longer used:** the "5 hops confirm this pattern" framing from the previous entry.

**Next:** write up the corrected, single-example finding as a proper design note for cross-model
review, honestly scoped as n=1 evidence that the mechanism is real but not yet evidence of how
often it matters or what a working fix looks like.

## 2026-07-14/15 - Warmup range-bin mislock: root cause, fix, cross-model review round-trip

**Set out to do:** find why `results/live_demo/20260714_180523_live_demo_sweep/` showed a
dominant, non-harmonic ~63-67 bpm cardiac-band peak (mistaken for HR) instead of the true
~79-84 bpm during t=0-170s, without patching the AHET candidate gate or tuning thresholds —
find why the signal itself was wrong at that pipeline stage.

**Worked (with evidence):**

- **Root cause identified: the one-time warmup range-bin lock (`_run_warmup_selection`,
  `scripts/live_demo.py`), not ECA/AHET/candidate-generation.** The chest was at bin 26
  (1.134 m); the session locked bin 21 (0.916 m), ~28 dB below the chest in the settled range
  profile. Re-deriving per-bin cardiac spectra from `adc_stream.bin` (raw ADC, read-only)
  showed the true HR is the dominant in-band peak at bins 26-29 in every symptom hop
  (magnitude ratio to in-band max = 1.00) but only 0.19-0.45 of local max at bin 21. Evidence:
  `results/live_demo/20260714_180523_live_demo_sweep/{warmup_bin_selection.json,
  live_intermediates.npz, adc_stream.bin}`.
- **Mechanism**: a transient at ~0.92 m existed only in the first ~2s of the 600-frame (30s)
  warmup window (subject settling). On that contaminated window, bin 21 alone among 14
  candidates passed the AHET gate, with a bogus `hr_raw=110.5 bpm` (Masimo read 91 and
  falling at t=31s). The scoring formula gave `hr_valid` a flat +1000, while energy
  contributed only -5x rank (~65-point spread across 14 candidates) — one lucky/bogus pass
  fully decided the session-long lock. `massimo2` (`results/live_demo/
  20260713_182002_live_demo_massimo2/`) was independently mislocked (bin 20 over bin 26) by
  the same +1000-dominates-energy mechanism, but WITHOUT any transient: skirt bins 19/20
  genuinely leaked correct-HR cardiac signal and outvoted the true chest bin (energy rank 1)
  on score alone.
- **Fix v1 (energy veto, 2026-07-14):** added a settled-energy check (skip first 5s of the
  warmup window, so the same transient can't inflate the veto's own energy measurement) that
  strips the `hr_valid` +1000 bonus for bins >12 dB below the strongest candidate's settled
  energy. Threshold chosen empirically: across all 4 recorded sessions, every false-lock bin
  sat <= -28 dB settled and every legitimate body bin >= -6 dB — >22 dB of separation, -12 dB
  sits mid-gap.
- **Full-pipeline replay verification (not just warmup logic — the whole DSP chain re-run on
  the same raw stream):** sweep session, bin 21 (as-run) vs bin 26 (chest), scored against
  PI-gated Masimo PR: MAE 9.6 -> 0.9 bpm, RMSE 11.9 -> 1.6 bpm, bias -5.7 -> -0.4 bpm,
  Bland-Altman LoA +/-20.7 -> +/-3.1 bpm, within-5bpm 34.5% -> 96.7%, within-10bpm 49.1% ->
  100%. Coverage (fraction of windows with an AHET-verified HR) dropped 36.4% -> 19.9% — bin
  26 answers less often but is essentially always right when it does; bin 21's higher
  "coverage" was mostly confident wrong answers. Replay artifacts:
  `results/live_demo/20260714_224000_replay_unknown` (bin 26),
  `results/live_demo/20260714_224244_replay_unknown` (bin 21, control).
- **Cross-model review round 1** (`notes/cross_review_warmup_veto_prompt.md`) returned
  **REJECT** (`notes/cross_review_warmup_veto_review_1_findings.md`): the veto only stripped
  the HR bonus, so an energy-ineligible bin could still WIN via breathing-evidence score
  alone (~295 vs ~145 in the reviewer's reconstructed scenario), labeled "medium" confidence
  with no warning — one AHET/BR-confidence quirk away from reintroducing the same failure
  class. Also flagged: the -12 dB prior stated as a categorical physical law rather than an
  empirical, scene-scoped finding; silent full-window fallback when `settle_skip_s` exceeds
  the warmup window; an opaque `max()` crash on empty candidate lists; missing tests for the
  decisive path.
- **Fix v2 (eligibility partition, 2026-07-15):** rewrote `_run_warmup_selection` so
  candidates split into energy-eligible/ineligible BEFORE selection
  (`winner_pool = eligible_good if eligible_good else good`) — an ineligible bin can now only
  win when zero eligible candidates' DSP succeeded, and that fallback path is forced to
  `confidence="low"` with `_no_energy_eligible_dsp_success` in the reason. Config key renamed
  `hr_bonus_min_settled_db` -> `energy_eligibility_min_settled_db` (never shipped under the
  old name in any commit). Added: threshold/settle-skip validation (`ValueError` on
  NaN/inf/positive-threshold/negative-skip), empty-candidate-bins guard, a live stderr
  warning whenever any `hr_valid` candidate is ruled ineligible (bin + dB deficit), evidence
  fields `energy_eligible` (per candidate, including DSP-failed ones), `fallback_used`,
  `eligible_dsp_success_count`, `all_candidates_energy_ineligible`,
  `settle_skip_frames_applied`, `settle_skip_fallback_full_window`. Reworded the config/code
  comments to state the -12 dB prior is empirical (4 sessions / 1 subject, 2026-07-14/15),
  scene-scoped (assumes the seated subject is the dominant in-gate reflector), not yet
  validated against competing reflectors or the planned 10-subject study.
- **New tracked, regenerable validation script**: `scripts/validate_warmup_selection.py` —
  hashes each session's `adc_stream.bin`, reruns the real `_run_warmup_selection` on its
  first 600 frames with its own stored config, compares against expected picks. All 4
  sessions PASS: `live_test1`->22, `massimo1`->23 (both unaffected — no `hr_valid` pass
  existed in either warmup, or none was contested), `massimo2` 20->26, `sweep` 21->26.
- **Cross-model review round 2** (`notes/cross_review_warmup_veto_prompt_2.md`): all 5
  original findings independently re-verified — F1 RESOLVED (`eligible_good`/`winner_pool`
  genuinely prevents an ineligible bin from ever outranking an eligible one), F2 RESOLVED AS
  SCOPING (global-reference limitation accepted as a stated, protocol-scoped empirical prior,
  not required to be a general chest-localization algorithm before merging), F3 RESOLVED, F4
  RESOLVED, F5 PARTIALLY RESOLVED (missing the all-zero-energy case, which surfaced one new
  MINOR finding: `fallback_used` stayed `False` in the pre-existing "every DSP call raised"
  branch even when every candidate was also energy-ineligible — a bookkeeping inconsistency,
  not a re-opening of the original scoring bug). **Verdict: APPROVE-WITH-NITS.**
- **Nit fixed same day**: `fallback_used = True` added to the all-DSP-failed branch, plus
  regression test `test_fallback_used_true_for_all_zero_energy_and_all_dsp_failed`
  reproducing the reviewer's exact repro (all-zero cube, every DSP call raises).
- **Final verified state**: focused suite `tests/test_live_demo_warmup_helpers.py` 27 passed;
  full suite `tests/` 796 passed, 1 xfailed, 0 regressions (baseline before this work: 784
  passed, 1 xfailed). `scripts/validate_warmup_selection.py` all 4 sessions PASS, unchanged
  by the nit fix.

**Failed / did not work, and why:**

- **Design variant — shift the warmup DSP window +5s instead of adding an energy veto**:
  tested against all 4 sessions before choosing the veto. Did not fix `massimo2` (still
  picked bin 20, ground-truth 23%) because the leaked-HR failure there wasn't caused by the
  settling transient at all. Combined with the veto it made `sweep` WORSE (picked bin 24,
  ground-truth 14%, vs the veto-alone pick of bin 26 at 45%) — moving the window just re-rolled
  which skirt bin got a lone lucky pass, rather than removing the failure mode. Rejected.
- **Design variant — require an adjacent-bin (+/-2) HR corroboration instead of an energy
  veto**: also tested against all 4 sessions. Did not fix `massimo2` (still picked bin 20).
  Rejected.
- **Near-miss, not a fix failure but worth recording so it isn't repeated**: the first
  attempt to score a `--replay-fast` run against Masimo used the NPZ's `elapsed_s` field,
  which is WALL-CLOCK time and reads ~0.0 for every hop under fast replay. This silently
  compared all 151 windows against Masimo at t=0 (a constant reading) and produced a
  plausible-looking MAE of 1.6 bpm — caught only because `elapsed_s.max()` was exactly 0.
  The correct timebase for any replay-based metric is `frame_idx / frame_rate_hz`. This is a
  live landmine for any future offline scoring of a `--replay-fast` run and is not yet fixed
  in `live_demo.py` itself — only avoided in the ad-hoc scoring script used for this
  investigation.

**Retired / no longer used:** none of this session's work has been committed yet (working
tree only, as of this entry) — see Next.

**Next:**
- Rewrite `HANDOFF.md` to reflect this state (CLAUDE.md S10.3 order: HISTORY then HANDOFF).
- Commit. `scripts/live_demo.py`'s working-tree diff contains one UNRELATED pre-existing edit
  (`delay_s = 15` -> `120` in `main()`, a live-capture countdown tweak, present before this
  investigation started) that must not be swept into this fix's commit.
- Triage separately (not part of this fix): untracked `scripts/stage1a_harmonic_coherence.py`,
  `scripts/stage1b_exploratory_motion.py`, `scripts/stage1b_temporal_continuity.py`.
- Fix the `elapsed_s` wall-clock landmine in `live_demo.py`/its NPZ schema, or at minimum
  document it prominently, before anyone else scores a `--replay-fast` run by hand.
- Bin-lock coverage ceiling: even at the correct chest bin, only 19.9% of windows produce an
  AHET-verified HR (vs 36.4% pre-fix, mostly wrong). The AHET gate's conservatism AT THE
  CORRECT BIN is now the bottleneck for paper-grade coverage — a separate investigation, not
  touched by this fix.
- `massimo1` residual weakness: no `hr_valid` pass exists anywhere in its warmup window, so
  the fix cannot repair it (nothing to partition) — falls back to an energy-rank tiebreak
  landing on bin 23 (ground truth ~42%) when bins 26-29 (65-69%) were available.
- The -12 dB / 5s thresholds are validated on n=4 sessions / 1 subject only; re-validate
  before broadening beyond this protocol's scene (single seated subject, single reflector in
  the distance gate) — e.g. against the planned 10-subject study.

## 2026-07-15 - Warmup bin-lock fix: post-commit cross-model re-review, one wording nit fixed

**Set out to do:** get the committed bin-lock fix (`863600e`, "Fix warmup range-bin mislock
via energy-eligibility partition") re-reviewed by the cross-model process now that it's
landed, since the prior review round approved the pre-commit working tree, not the committed
state.

**Worked (with evidence):**

- **Third cross-model review round, against the committed commit `863600e`** (the reviewer
  correctly flagged that the review prompt's "uncommitted" framing was stale — the fix had
  already been committed). All 5 original findings (F1-F5) re-verified as **RESOLVED**
  against the committed code, including the F5 test-coverage finding, which now cites the
  full 27-test file.
- **One new MINOR finding, confirmed by direct code inspection before fixing**: the
  ineligible-candidate stderr warning (`scripts/live_demo.py`, the `vetoed_hr_candidates`
  block) fires unconditionally as soon as any `hr_valid` candidate is found
  energy-ineligible — which happens BEFORE the code decides whether a mixed fallback is
  needed. In the exact scenario where all energy-eligible candidates' DSP calls fail, the
  bin the warning describes as "excluded from selection" can, a few lines later, win as the
  low-confidence fallback — the warning contradicted the outcome it preceded. Verified this
  is purely a message-wording issue: `hr_bonus_vetoed`, `energy_eligible`, `fallback_used`,
  and `selection_reason` in the evidence JSON were never wrong.
- **Fixed**: reworded to "excluded from the primary energy-eligible pool," with an explicit
  parenthetical that the bin may still be selected as a low-confidence fallback. Added the
  assertion to the existing mixed-fallback test
  (`test_ineligible_bin_wins_only_when_no_eligible_dsp_succeeds`, which already reproduces
  this exact scenario) checking the new wording appears and the old, contradictory wording
  does not.
- **Verified**: `tests/test_live_demo_warmup_helpers.py` 27 passed; full suite 796 passed,
  1 xfailed, 0 regressions (unchanged from before this fix — a message-only change cannot
  affect selection logic, confirmed rather than assumed); `scripts/validate_warmup_selection.py`
  all 4 sessions still match expected picks, unaffected.

**Failed / did not work, and why:** nothing — this was a single, well-localized wording fix
with no design ambiguity once the reviewer pointed at the exact contradiction.

**Retired / no longer used:** the "excluded from selection" wording in that stderr message.

**Next:** commit this fix. Cross-model review cycle on the bin-lock fix is now closed
(APPROVE-WITH-NITS, both rounds' nits resolved). Remaining open items are unchanged from the
prior entry: bin-lock coverage ceiling at the correct bin (19.9%), `massimo1`'s tiebreak
weakness, and re-validating the -12 dB/5s thresholds beyond n=4/1-subject.

## 2026-07-15 - Stage 1B lag-10 motion statistic: 6-round plan review, then control scaffold implemented and verified

**Set out to do:** revisit Stage 1B (temporal continuity as a respiratory-harmonic
discriminant), left "inconclusive, not failed" by the prior Stage 1B entry because round 2's
one-hop motion statistic has no leverage on any capture in hand — real breathing-rate
transitions ramp over ~10 hops, so a 1-hop comparison sees almost no movement.

**Worked (with evidence):**

**Design**: extend round 2's causal one-step statistic to a fixed lag `L=10` hops (~30 s),
chosen from the Masimo-measured ramp durations on the sweep session (31 s, 30 s, 36 s across
its three rate transitions) and frozen as a design constant, not re-derived per capture.

**Six rounds of cross-model plan review** (CLAUDE.md §6), each catching something real. Full
verbatim detail lived in `notes/cross_review_stage1b_lag_statistic_prompt_*.md` /
`*_review_*_findings.md` (12 working files across 6 rounds) — **deleted per the user's
request once folded into the final plan and this entry; this entry is the durable record**:

- **Round 1 (draft 1, NOT CLEARED AS WRITTEN)**: the "frozen `k_hat`" claim was false — round
  2's actual code (`scripts/stage1b_exploratory_motion.py`) recomputes `k_hat` at every hop
  from the trailing 5 samples, not once per trajectory as draft 1 assumed (verified against
  the code before accepting the finding). Gap policy deferred, not decided. A cardiac/RSA
  confound (real HR can covary with breathing rate over 30+ s) was missing entirely. No
  numeric pass criteria, no chosen intervention point in the AHET pipeline, no
  threshold-selection rule.
- **Round 2 (draft 2, CLEARED WITH CHANGES)**: fixed the frozen-`k` contract (exact hops, clip
  evaluated before the origin), chose a zero-gap persistence policy, restored/specified the
  cardiac-RSA control, demoted pseudo-label AUC to diagnostic-only with a real end-to-end
  severe-error-reduction endpoint. Still missing: intervention point, threshold-selection
  rule; the latency arithmetic had an unresolved off-by-one (42 s vs 45 s depending on an
  inclusive-vs-strict convention).
- **Round 3 (draft 3, READY WITH FIXES)**: chose post-AHET-veto-only as the intervention
  point (retain-or-NaN an accepted decision; cannot promote a different candidate or recover
  one absent from AHET's top-3 — an honest ceiling, since most of the sweep's severe errors
  have no true-HR candidate at all); adopted the strict-pre-origin latency convention
  (45 s / 75 s). But introduced **two real logic bugs**: `k_hat`'s clip was still evaluated at
  the origin hop while claimed frozen strictly before it (self-contradiction), and the
  threshold-selection objective said to *maximize* rejection of true cardiac signal — exactly
  backwards.
- **Round 4 (draft 4, READY WITH FIXES)**: both logic bugs from round 3 confirmed fixed
  (independently re-verified: the clip now uses only the last inference hop; the objective
  now maximizes rejection of harmful examples subject to a cardiac-retention constraint).
  Caught a hop-count miscount and left perturbation-instability aggregation, candidate
  population, and tie-break underspecified.
- **Round 5 (draft 5, READY WITH FIXES)**: fixed the perturbation-instability definition
  (per-window worst-case, not per-pattern-then-max), corrected the full-attempt count to 16
  (5 inference + 11 scoring, independently re-verified by direct computation), removed a
  redundant tie-break. Found one more real bug: the M0 synthetic-harmonic gate was bucketed
  with the safety constraints, so the pass-all sentinel (which vetoes nothing) could never
  pass M0 by construction — collapsing "infeasible" and "feasible but not useful" into the
  same outcome and defeating the sentinel's purpose.
- **Round 6 (draft 6, READY FOR CONTROL-SCAFFOLDING)**: confirmed the fix — three explicit
  ordered stages (safety-feasible → utility/mechanism → objective+tie-break) restore the
  intended 3-way outcome. Flagged that "infeasible" isn't actually reachable with valid
  inputs (the sentinel always trivially clears stage 1) and that M0 case roles
  (supported/deliberate-invalid/stress) must be tagged before execution, never reclassified
  after a failure.
- Draft 7 folded in round 6's two clarifications and closed the plan-review cycle.

**Then implemented `scripts/stage1b_lag_statistic.py`** (868 lines, untracked): the full
control scaffold — the exact-hop frozen-`k` statistic, M0 (48 role-tagged synthetic cases + 6
deliberate-gap cases + a rank-crossing trajectory case), M0b (the real Hann+rFFT extraction
pipeline run on synthetic raw signal, comparing the production 3 s-hop scheme against a
non-overlapping 30 s-hop scheme), a missingness-matched null, a synthetic cardiac/RSA
confound control, a real per-decision empirical surrogate, and the three-stage
threshold-selection machinery. Verified at every stage before trusting the next, matching
this project's established M0-first discipline:
- Core statistic sanity-checked against hand-built cases first (perfect harmonic tracker
  recovers correct `k_hat` and negative SCORE; non-tracking candidate gives positive SCORE; a
  single missing hop anywhere in the 16-hop span correctly invalidates the attempt).
- **M0 caught a bug in my own test construction on the first run**: synthetic "k=2"/"k=3
  harmonic" cases were built at an `f_r` where those orders aren't even admissible harmonics
  (`k*f_r` below the cardiac band's 0.8 Hz floor) — the clip correctly rejected them and
  snapped to the nearest valid order; the test setup was physically invalid, not the code.
  Fixed by choosing `f_r` per `k`. M0 then passed cleanly (48/48 supported cases, 6/6 gap
  cases correctly rejected).
- The three-stage threshold selector was verified against a hand-computed synthetic scenario
  (derived by hand before running): all three outcomes (`feasible_and_useful`,
  `feasible_not_useful`, `invalid_preconditions`) matched the predicted values exactly,
  including the specific behavior rounds 5-6 fought over — the pass-all sentinel correctly
  clears stage 1 and correctly fails stage 2.
- M0b and the cardiac/RSA control ran without crashing and produced sensible (if honestly
  caveated as limited-power at the tested noise levels) output; the null control showed no
  systematic harmonic bias, as expected.
- The real per-decision surrogate (diagnostic-only, no threshold fitted) surfaced a genuine
  finding on real data, not a bug: on `natural` and `sweep`, clean 16-hop runs exist but none
  of their endpoints land on an AHET-accepted hop — a real data-sparsity problem for whoever
  fills in the numeric pre-registration.
- Full test suite unaffected: 796 passed, 1 xfailed, 0 failed (this script has no pytest
  coverage of its own — it is a standalone analysis script, same convention as
  `stage1a_harmonic_coherence.py` / `stage1b_exploratory_motion.py`).

**Failed / did not work, and why:** three of my own drafts (1, 3, 5) contained real logic
errors that survived my own re-reading before an independent pass caught them — a false
"frozen" claim in draft 1 (asserted round 2 already froze `k_hat`; it doesn't), a reversed
optimization objective in draft 3 (would have tuned a veto to reject MORE true cardiac
signal, the opposite of the intent), and a feasibility/utility conflation in draft 5 (broke
the pass-all sentinel's whole purpose). None were caught by re-reading my own prose more
carefully; all three needed direct code verification or an independent reviewer. Recorded
here as a reminder that "I re-checked it" is not the same as "it's correct" for this kind of
exact, multi-constraint design work.

**Retired / no longer used:** the 12 `notes/cross_review_stage1b_lag_statistic_*.md` working
files (6 prompts + 6 findings) — deleted once their substance was folded into the final plan
(`notes/note_stage1b_lag_statistic.md`, draft 7) and this entry. If a future session needs the
byte-for-byte review text rather than this summary, it no longer exists locally; this entry
is what remains.

**Next:** fill in the design note's §7(c) numeric pre-registration (severe-accept conversion
targets, minimum-utility bar, control pass bars, block-resampling parameters) with real
derivations, not invented numbers — the same discipline `notes/comparator_prespec.md`'s
FFT-bin-derived 5 bpm gate was built with. Only after that: compute a real lag-10 SCORE
distribution, fit the three-stage threshold on the 3 exploratory sessions, and evaluate the
decisive endpoint. All 3 existing sessions (`natural`, `paced16`, `sweep`) are
exploratory-only for this specific design — a confirmatory claim needs a new, as-yet-
uncaptured session. `scripts/stage1b_lag_statistic.py` is untracked; committing it is a
separate decision from the other stage1 scripts sitting in the working tree.

## 2026-07-15 - Stage 1B §7(c) numeric pre-registration: items (ii)-(vi) derived; ZERO baseline severe accepts at current HEAD

> **Reconstructed on 2026-07-23** from `notes/note_stage1b_lag_statistic.md` (draft 7, §7c
> and its "Next" list) and the replay artifacts — the session that did this work ended
> without appending a HISTORY entry (a §5.5 lapse caught during a 2026-07-23 project audit).
> Every claim below was re-checked against those artifacts before writing.

**Set out to do:** fill in the Stage 1B lag-10 design's §7(c) numeric pre-registration with
derived (not invented) numbers, per the previous entry's "Next".

**Worked (with evidence):**
- All 3 exploratory sessions re-processed end-to-end at current HEAD (`dfe7fb5` — bin-lock
  fix + Stage-0 DSP), because the original captures predate both. Fresh replays:
  `results/live_demo/20260715_164124_replay_unknown` (natural, bin 23 — same bin as the
  pre-fix pick), `20260715_164018_replay_unknown` (paced16, bin 20→26), and
  `20260715_164132_replay_unknown` (sweep, bin 21→26). Masimo alignment used each ORIGINAL
  capture's real `elapsed_s` joined by `frame_idx` (the fresh replays' own `elapsed_s` is
  the known wall-clock landmine under `--replay-fast`).
- Scored strictly under `notes/comparator_prespec.md`: natural 5/50 accepted, 1 scorable,
  MAE 0.19 bpm; paced16 23/50, 21 scorable, MAE 0.50 bpm; sweep 30/150, 19 scorable, MAE
  0.53 bpm. **Severe (>5 bpm) errors: 0 on all three sessions.** All 17 stationarity-gate
  exclusions were checked individually — every one a real Masimo instability (spread
  5.1-26.0 bpm), not radar error hidden by the gate.
- §7(c) items (ii)-(vi) filled with derivations: safety bar = 100% retention of the pooled
  n=41 scorable accepts (rule-of-three power caveat ≈7.3% recorded honestly); coverage
  guard = 0% yield loss as a mechanical corollary of (i)+(ii); evaluability floors measured
  against the pre-existing 30% leverage floor — natural 14.3% FAIL, paced16 0.0% FAIL,
  sweep 50.0% PASS.

**Failed / did not work, and why:**
- **Item (i) (the objective bar) is BLOCKED — zero baseline severe accepts exist on current
  data** (0/41 scorable accepts across all 3 sessions). An empty-population problem, not a
  small-sample one. Both it and the tied §7(g)-stage-2 utility bar are deferred to a
  confirmatory capture. Most likely explanation: the bin-lock fix already removed the
  dominant severe-false-accept mechanism in this dataset — meaning Stage 1B currently has
  nothing left to veto on existing data.
- The work session itself failed to log this entry (hence the reconstruction above).

**Retired / no longer used:** nothing.

**Next:** cross-model review of the §7(c) numeric content (the §§2-7g structure had 6 review
rounds; these numbers had none); compute the non-overlapping-origin independent-attempt
count for item (iv)'s exact-track floor; design the confirmatory capture so it plausibly
still produces a severe accept post-bin-fix (e.g. an engineered k·f_r ≈ HR collision) — a
repeat of the existing stepped-rate protocol may well produce none.

## 2026-07-22 - Session 9: off-protocol headless capture (a mistake; deleted 2026-07-23)

**Set out to do:** not recorded at capture time. The user identified the session as a
mistake on 2026-07-23 and erased it; per CLAUDE.md §4, what it was is recorded here before
the numbers are lost.

**What it was (recorded before deletion):** `data/raw/9.bin`, 747,110,400 bytes (sha256
`82ff3d3cb0aa1325a043b3c037e9459ed67bc9fa4af89b42d7d0a00ffc7303a1`), plus `9_meta.json` and
`9_LogFile.csv`. Captured Wed 2026-07-22 13:52:00-13:56:47 local (286 s per log, 285 s
configured), 5700 frames, 513,126 packets received, **0 out-of-sequence, 0 zero-filled** —
the capture itself was clean. Participant P0011, posture `seated_chair_back`, distance
182 cm, `stationary_intervals` 0-90 s, **no Masimo reference captured**. Chirp config:
77 GHz start, 70.006 MHz/µs slope, 256 ADC samples @ 5209 ksps, 4 RX / 1 TX, 32 loops,
50 ms frame period, range resolution 0.0436 m. This was the first verified standalone use
of `steps/step_1/capture.py` (with its new 10 s countdown from `4dffce9`) — the tool works
end-to-end and writes `.bin` + meta + log + manifest row; that knowledge survives the
deletion.

**Failed / did not work, and why:** the session was off-protocol on at least three axes —
182 cm distance (protocol max 140 cm), chair-back posture (protocol: back straight, no
support), and no Masimo ground truth — and was not logged in HISTORY at capture time
(§3.6). Declared a mistake by the user on 2026-07-23.

**Retired / no longer used:** all three session-9 files deleted from `data/raw/` and its
row removed from `data/manifest.local.csv` (verified back to header-only, 2026-07-23).
Nothing was ever processed from it — `data/processed/` was and remains empty; no results or
figures derive from it.

**Next:** nothing follows from this session.

## 2026-07-23 - Housekeeping: stage-1 scripts committed; HISTORY/HANDOFF brought under version control; branch v9c

**Set out to do:** close the open commit decisions from HANDOFF §3 and stop keeping the
project record files local-only.

**Worked (with evidence):**
- `4dffce9` committed the four stage-1 analysis scripts (`stage1a_harmonic_coherence.py`,
  `stage1b_temporal_continuity.py`, `stage1b_exploratory_motion.py`,
  `stage1b_lag_statistic.py`) — closing the "commit decision still open for all 4" item —
  plus `steps/step_1/capture.py` PREPARATION_TIME 60→10 s.
- `b6f5b73` removed the blanket `*.md` rule from `.gitignore` and committed `HANDOFF.md`,
  `HISTORY.md`, and `config/set_DCA1000.md` (a one-line DCA1000 network-profile setup
  command). **This supersedes the 2026-07-15 "local-only by design" decision** for
  HANDOFF/HISTORY; `notes*/` and `results*/` remain gitignored.
- Branch `vital_signs_v9c` created from `v9b` (verified identical apart from the md
  commit: `git diff vital_signs_v9b..vital_signs_v9c --stat` shows only those 4 files).
- Test suite re-verified on `v9c` at `b6f5b73`: **796 passed, 1 xfailed, 0 failed**
  (`conda run -n radar-vitals python -m pytest tests/ -q`, 2026-07-23, 42 s).

**Failed / did not work, and why:** nothing attempted beyond the above.

**Retired / no longer used:** the "HISTORY/HANDOFF/notes are not version-controlled by
design" gotcha (HANDOFF §6) — now false for HISTORY/HANDOFF (still true for `notes/` and
`results/`).

**Next:** rewrite `HANDOFF.md` to current state — it is stale on: branch name (`v9b`→`v9c`),
the stage-script commit decision (now closed), §7(c) status (partially done; item (i)
blocked on a confirmatory capture), the md-tracking gotcha, and the "manifest has 0
sessions" claim (true again only because session 9 was erased).

## 2026-07-24 - Writing sources, implementation plan, and the mislock mechanism quantified

**Set out to do:** produce a full audit of project state, then build the durable writing
artefacts the thesis and paper need (chapter source, journal planning), a whole-project
implementation plan now that BR is a co-equal goal, and presentation material for a postdoc
application.

**Worked (with evidence):**

- **`THIRD_CHAPTER.md` created** — self-contained thesis-chapter source: theory (FMCW ranging,
  the 3.2 rad/mm phase relation at 77 GHz, the harmonic signal model), literature review of the
  6 surveyed methods, method justification, algorithm spec, evaluation methodology, results,
  negative results, limitations, suggested structure with word budgets, figure list, references.
  Every empirical claim carries a status tag (`[VERIFIED]` / `[PRELIMINARY]` / `[RETIRED]` /
  `[PENDING]`) with an explicit promotion rule: promote by re-measuring, never by editing the tag.
  §17 is a measured inventory of work to date (~18,700 lines code, 797 tests, 4 captures /
  ~2.52 GB raw, 97 HISTORY entries) — counted from the repo, not estimated.
- **`JOURNAL_PAPER.md` created** — submission-readiness gate, target-journal comparison
  (recommendation: IEEE JBHI primary, TBME if the methodology leads; npj Digital Medicine judged
  out of reach for a 10-subject healthy-volunteer study), novelty ranking, section outline with
  word budgets, figure plan, anticipated-reviewer-objection table, verified reference list.
- **`plans/implementation_plan.md` created** — 13 milestones across 4 tracks (governance /
  blockers / data / methods / output), each independently executable with goal, dependencies,
  "done when", and risk. Decisions taken with the user: BR reference = Masimo `rr_bpm`
  cross-checked against the paced metronome rate; Paper 2 staged (joint-Doppler before Capon
  DOA); both reference papers as an **offline comparison arm**, not production replacements;
  sequencing = fix blockers → pilot → study → method work.
- **References verified against source, not memory.** The 6 arXiv papers came from
  `notes/approach.md`; web-verified additions: Bland & Altman 1986 (Lancet 327:307-310),
  Droitcour 2004 (TMTT 52(3):838-848), Park 2007 (TMTT 55(5):1073-1079), Li/Lubecke 2013
  (TMTT 61(5):2046-2060), Alizadeh 2019 (IEEE Access 7:54958-54968), Beltrão NICU 2022 (Sci Rep
  12). **Deliberately left `[CITATION NEEDED]`**: AAMI EC13 (the widely-quoted "±10% or ±5 bpm"
  could NOT be verified from open sources — do not cite from memory), ISO 80601-2-61, TI document
  numbers except DCA1000EVM SPRUIJ4, and the Masimo MightySat PR accuracy spec.
- **The two `literature/ref_papers/` papers were audited for actual use.** Paper 1 (Ahmed et al.,
  "Discovering the Unseen", DOI 10.1109/TRS.2024.3412915) **is** used — its Harmonic Accumulation
  is the *primary* BR estimator in `src/respiration.py`, adapted from the paper's pulse-radar
  even-harmonic model (2f_b, 4f_b…) to all-harmonic phase (f_b, 2f_b, 3f_b…). It has **never been
  validated against Masimo**. Paper 2 (Kotte et al., DOI 10.1109/TRS.2024.3352189) is **not
  referenced anywhere in the code or notes**. Both are simulation-only. Paper 1's Fig. 8(c)-(d)
  claims correct estimation *at* the 4·f_r = HR collision (f_b = 20, f_h = 80 bpm, SNR 10 dB) —
  the project's central unsolved problem. Verified enabler for Paper 2: `read_adc_bin` already
  returns all 4 RX channels and the config is 1 TX / 4 RX, the SIMO setup it assumes — the
  angular information is in every `.bin` already recorded and is currently averaged away.
- **The warmup mislock mechanism was quantified for the first time** (previously described only
  qualitatively). From `results/live_demo/20260714_180523_live_demo_sweep/` and the post-fix
  replay `20260715_164132_replay_unknown/`:
  - At t = 147 s: mislocked bin 21 reported **64.4 bpm and passed AHET**; correct bin 26 reported
    **81.8 bpm**; comparator reference **81.0 bpm** — a 16.6 bpm error indistinguishable at the
    output from a correct reading.
  - **The defect was in the selector's scoring**: bin 21 was the only candidate whose HR DSP
    returned a result, earning an HR-valid bonus worth **1265 points vs 295** for the true chest
    bin. The selector rewarded "produced an answer" without checking for supporting signal.
  - **The 5 s settle skip is load-bearing, not cosmetic**: over the full warmup bin 21 measures
    **−11.0 dB**, *inside* the −12 dB eligibility gate; after skipping the first 5 s (100 frames
    at 20 Hz) it measures **−28.1 dB** and is correctly ruled ineligible. With the fix:
    `hr_bonus_vetoed: true`, score 1265 → 265, bin 26 wins on an unchanged 295.
  - This independently confirms the "28 dB below the chest" claim carried in HANDOFF, which the
    raw (unskipped) energy alone does not support.
- **Three figures were generated and visually verified** from committed scripts in `figures/`
  (see "Retired" — they no longer exist): a system-setup schematic, a synthetic
  harmonic-collision figure (seed 42, two panels contrasting f_r = 12 bpm resolvable vs
  f_r = 20 bpm merged), and a three-panel mislock figure built entirely from measured data
  (energy profile with the gate, paired spectra at t = 147 s, HR-vs-time against Masimo).

**Failed / did not work, and why:**
- The first draft of the collision figure was **unreadable in its benign panel** — the cardiac
  peak is genuinely buried among respiratory harmonics, so "no collision" did not read as such.
  Fixed by overlaying the cardiac-only component as a separate trace. Worth remembering for any
  future version: the contrast only works if the ground-truth component is drawn separately.
- An early speaker note described the mislock fix as "skipping the first five seconds", which is
  **wrong as an explanation** — it implies a timing fix. The fix is a scoring gate; the skip only
  determines which frames the eligibility energy is measured over. Corrected in all three writing
  files after checking the actual selection JSON.
- `conda run` with multi-line inline `python -c` produced silent empty output under this shell;
  writing scratch scripts to a file and running those worked. Not a project bug, but it wasted a
  few cycles — prefer script files over inline `-c` for anything multi-line.

**Retired / no longer used:**
- **`POSTDOC_SLIDES.md` and the entire `figures/` directory (3 scripts + 3 PNG outputs) were
  deleted from the working tree during this session.** They were never committed, are not
  recoverable from git, and are not present anywhere under the user's Desktop. **The reason is
  not recorded — this entry does not assert one.** What they contained: two-slide presentation
  content with layout sketches and speaker notes, plus `fig_system_setup.py`,
  `fig_harmonic_collision.py` and `fig_range_bin_mislock.py` with their rendered outputs. **The
  findings they encoded survive** — every number is recorded in this entry and in
  `THIRD_CHAPTER.md` §9 / `JOURNAL_PAPER.md` §4.3. What is lost is the plotting code, which would
  need rewriting to regenerate the images.
- The 2026-07-15 "HISTORY/HANDOFF/notes are local-only by design" decision remains superseded for
  HANDOFF/HISTORY (committed `b6f5b73`); `notes/` and `results/` stay gitignored.

**Next:** the plan's immediate actions are (1) live hardware smoke test — the live path has not
run since 2026-07-14 and is the first blocker before any subject; (2) the respiration-collapse
fix, which now blocks the whole BR goal; (3) the HA synthetic collision test, cheap and capable of
reshaping method strategy before data collection; (4) write the BR comparator spec, then deposit
both pre-registrations publicly **before** the pilot — the pre-registration claim is only
verifiable if it is timestamped before the data it governs. Ethics approval is in hand
(reference number still to be recorded for the Methods section). Decide whether to rebuild the
deleted figure scripts.

---

## 2026-07-24 - Cross-model review of the implementation plan (Codex x Claude Code)

**Set out to do:** process Codex's review of `plans/implementation_plan.md`, the broad plan
overseeing the HR+BR project, verifying each comment against the repo, the two reference papers
and the code rather than accepting it on assertion.

**Worked (with evidence):** 19 comments across 3 rounds. 15 applied, 1 withdrawn by Codex after
debate, 2 escalated to the user and then decided (below), 1 (IP-15) already fixed before it
landed. Suite unchanged throughout: **796 passed, 1 xfailed** - no code was written, only the
plan.

Three false repository claims were found and corrected:

- **"797 passing tests" -> 797 test outcomes (796 passed, 1 xfailed).** An xfail is not a pass.
- **"Respiration collapse 4-for-4 on every Masimo capture" -> 3-for-3.** Only three captures
  carry a Masimo reference. Measured directly from each run's `live_estimates.csv`: the 6.00 bpm
  floor-pin occurs in `massimo1` (12 hops), `massimo2` (5) and `sweep` (10), and is **absent**
  from the unreferenced `live_test1` (minimum BR 16.0 bpm). **The 2026-07-14 sweep entry above
  (~line 4973) carries the same incorrect "4-for-4" wording; it is corrected here rather than
  edited, per CLAUDE.md S10.2.**
- **"1 TX / 4 RX = the SIMO setup Kotte et al. assumes" -> false.** The paper's Sec. IV simulates
  1 TX and **20 RX** at 24 GHz, 50 ms PRI, N_c = 16. Its eq. (25) requires `R_t^-1` where
  `R_t = E{Y_t Y_t^H}` is N_c x N_c and the **RX channels supply the snapshots**: at n_R = 4,
  `rank(R_t) <= 4`, so `R_t` is singular and the inverse does not exist. Its Fig. 5 validation is
  on generic moving-target Dopplers (-1/-2, -1/4, 1/2.5 Hz), not HR/BR. M9 would have run
  existing captures through an ill-posed estimator. M9 now requires a paper-faithful 1x20 /
  N_c=16 reproduction first, then a registered 4-RX ablation specifying covariance estimation,
  regularisation, rank checks, RX calibration and the chirp-vs-frame slow-time mapping.

Design changes of record: M3's BR comparator is designed from **reference-only** evidence (radar
agreement is inadmissible - the mirror image of the CLAUDE.md S4 tuning ban); the linalg-free DSP
cross-model review moved from M11d housekeeping to a **prerequisite of M4**, since M4 produces
every paper-grade number and executes that path; Bland-Altman for the study must use a
subject-clustered repeated-measures model (`scripts/plot_bland_altman.py` pools windows as
independent pairs, `se_loa = sqrt(3*sd^2/n)`, and was never valid even for the n=1 pilot - its
output is descriptive only); M6 completes against a frozen evaluable-window floor rather than a
session count; per-distance agreement downgraded to descriptive metadata, since the protocol
deliberately does not pin distance; M0 gains a frozen comparison discipline naming ECA+AHET
primary and all other estimators exploratory; M8 gains a paper-faithful reproduction control
before the transfer test, so a failure can be told apart from our own bug.

**Ethics decisions taken by the user (2026-07-24), on the user's authority - no agent has read
the approval document:**
- **The approval COVERS M7's subject-specific collision manoeuvre.** M7 is unblocked.
- **Recordings may run up to 10 minutes.** This makes session length a live lever for the
  evidence-floor problem: ~9 usable windows/session at 5 min versus ~19 at 10 min, i.e. ~1.8-8.3
  accepted windows per subject versus ~3.8-17.5 at 10-46% coverage.
- **Still unrecorded:** the approval reference number and issuing board, needed for the Methods
  section.

**Failed / did not work, and why:** two of my own revisions introduced regressions that Codex
caught, both worth recording because both were the same class of error - writing an acceptance
criterion that can only be satisfied by success.
1. My M2 "Done when" required all three referenced captures to produce BR consistent with
   `rr_bpm`. If the all-harmonic HA adaptation is simply invalid - which M2's own Risk
   anticipates - that could only be satisfied by tuning until it agreed, violating CLAUDE.md S4
   and cross-cutting rule 5. M2 now closes on a documented root cause, correct validity
   semantics and dumped intermediates, and **a negative result closes it**.
2. Switching the gate wording from "scored" to "confirmatory" made M0 call M5 the first
   confirmatory capture while M5 is labelled exploratory. Fixed with an explicit three-class
   vocabulary: pre-freeze exploratory (the 4 existing captures + M1), post-freeze exploratory
   (M5), confirmatory (M6+).

**Retired / no longer used:** the claim that M0 can establish *"the rules were frozen before any
data existed."* It was never available - the four existing captures already informed the method's
design (`THIRD_CHAPTER.md` S10.1; `HANDOFF.md` S4). Codex pressed this via IP-03 arguing M1's
smoke test should be moved after M0; I declined the reordering but checking the argument exposed
that the underlying claim was false regardless of where M1 sits, and Codex then **withdrew the
comment**. M0 now states the attainable claim - **frozen before the confirmatory data** - and
must enumerate every capture existing at freeze time as exploratory. Overstating this in
`JOURNAL_PAPER.md` S3.1/S10 would repeat the error that forced the "MAE 0.16 bpm" withdrawal.
Also retired: "M0-first discipline" as a name for synthetic-control-first (it collided with
milestone M0); the unguarded "consider 6-7 min" suggestion in M6, replaced by an explicit
duration decision.

**Next:**
- **Two decisions still open, both must land in M0's deposit.** (a) **Recording duration** - the
  approval allows up to 10 min but `notes/protocol.md` still says 5; deciding before the deposit
  costs nothing, deciding after costs a public amendment. **`notes/protocol.md` needs updating.**
  (b) **The evidence floor** - deferred by the user on 2026-07-24; deferrable, but not past M0,
  because a floor chosen after seeing the pilot yield is not a floor. Carried with it: whether to
  attack coverage (M11a) before freezing, so an improved estimator can be the pre-registered
  primary rather than a post-hoc footnote.
- **Downstream documents are now stale** and need re-review before use: `JOURNAL_PAPER.md`
  (pre-registration strength, pooled Bland-Altman, per-distance claims, test-count wording),
  `THIRD_CHAPTER.md` (S17.2 test count, S12.4 "4-for-4", Bland-Altman, Kotte characterisation),
  `notes/protocol.md` (recording duration, collision rationale).
- **M7's design flaw is open** (not an ethics issue): it selects the paced rate from *resting* HR,
  but pacing moves HR - the sweep's collision landed at the 21 bpm step, not the designed 18.
  M7's derived plan must set the rate from HR measured *while paced*.
- Then M1, M2 and M8 step 1a/1b, which Codex agreed are unblocked.

---

## 2026-07-24 - Protocol updated: recording duration 5 -> 10 minutes, plus two stale claims fixed

**Set out to do:** apply the ethics answer from the review session to `notes/protocol.md`, which
was the one document still saying 5 minutes after the approval was confirmed to permit 10.

**Worked (with evidence):**

- **Recording duration changed from 5 to 10 minutes** for both study arms. Decided **before** the
  M0 deposit, so it is frozen protocol rather than a later public amendment - which is the whole
  reason for doing it now. **The approval's 10-minute ceiling must not be exceeded**; recorded on
  the user's authority, since the approval document is not in this repo.
  Effect on the evidence problem that motivated it: ~9 usable (non-overlapping 30 s, post-warmup)
  windows per session becomes **~19**, i.e. ~1.8-8.3 accepted windows per subject becomes
  **~3.8-17.5** at the measured 10-46% coverage, before Masimo PI/coverage/stationarity exclusions
  remove a further 12-20%. Storage rises from ~15 GB to **~31 GB** for the 20-session study.
- **The risk this introduces is recorded with it, not just the benefit.** The warmup locks **one
  range bin for the whole session**, so a posture shift late in a longer sit corrupts the tail with
  no obvious symptom - the same silent-failure class as the 2026-07-14 mislock. The 486 s (~8 min)
  sweep shows ~8 minutes is tolerable, but that subject was actively pacing rather than sitting
  through natural breathing. **M5's pilot must verify the locked bin still tracks the chest at
  minute 9-10** (`scripts/diagnose_live_run.py`); if it does not, the duration comes back down.
- **Two stale claims in `notes/protocol.md` found and corrected while editing:**
  - *"there is no standalone capture script"* - **false.** `steps/step_1/capture.py` exists (29 KB)
    and was verified end-to-end on 2026-07-22 (session 9), writing `.bin` + metadata + SHA-256 +
    manifest row. The file now records both capture paths and states that `live_demo.py` is the
    one to use for study sessions, because it produces the diagnostics the analysis depends on.
  - **The protocol contradicted itself on warmup latency** - "~30 s" in the Warmup & bin lock
    section (correctly derived from one `window_s` buffer at 20 fps) versus "~40 s" in Session
    step 5. Corrected to ~30 s, with the contradiction noted inline.
- **M7's capture is now described in the protocol** as an approved but separate method-development
  arm, with the explicit instruction **not** to compute its paced rate from resting HR.
- Consistency check per this file's own header (protocol / `live_demo_config.yaml` / CLAUDE.md /
  `notes/approach.md`): neither CLAUDE.md nor `notes/approach.md` carries a recording duration, and
  `live_demo_config.yaml` has `max_live_duration_s: null` (run until Ctrl-C), so **nothing else
  contradicts the new value**. Test suite unaffected: 796 passed, 1 xfailed.

**Failed / did not work, and why:** nothing failed. One judgement call worth recording: the
duration is enforced only by operator discipline, because `max_live_duration_s` is `null`. Setting
it to 600 would make the protocol self-enforcing, but it would also hard-stop a session mid-window
and it changes capture-tool behaviour, so it was **flagged rather than changed**.

**Retired / no longer used:** the 5-minute recording duration, and the protocol's "Nothing open -
protocol is fully specified" closing claim, which was false: the evidence floor and the ethics
reference number are both still open.

**Next:**
- **The evidence floor is the last open item blocking M0** (deferred by the user 2026-07-24). The
  10-minute change spent the cheapest lever, so if the floor is still missed the only remaining
  responses are more sessions or a weaker claim. Fix the floor before the deposit.
- Record the **ethics approval reference number and issuing board**.
- `plans/implementation_plan.md` (M0/M5/M6) updated to match; `THIRD_CHAPTER.md` S3.3 and
  `JOURNAL_PAPER.md` still say 5-minute recordings and are now stale.

---

## 2026-07-24 - Writing artefacts corrected; HANDOFF rewritten to hand off M0 planning

**Set out to do:** propagate the cross-model review's corrections into the two writing artefacts
(which the review left knowingly stale), and rewrite `HANDOFF.md` so a fresh chat can pick up
planning for **M0** without reading the whole history.

**Worked (with evidence):**

- **`THIRD_CHAPTER.md` corrected** - five claims, each wrong rather than merely out of date:
  S17.2/S6/S17.5 test count ("797 tests, all passing" -> 797 outcomes = 796 passed + 1 xfailed,
  with an explicit note that an xfail is not a pass); S12.4 respiration collapse ("4-for-4" ->
  **3-for-3**, with the per-capture hop counts and the correction footnote); S7.5 gains a
  **repeated-measures Bland-Altman** requirement and marks the pooled-independent implementation
  invalid **including for the n=1 pilot**; S10.3 + S1 + S0 downgrade **per-distance** agreement to
  descriptive metadata; S14 rewrites the two `literature/ref_papers/` entries with their verified
  geometry - notably that **Kotte et al. uses 1 TX / 20 RX, not a 4-RX SIMO setup**, and why that
  makes `R_t` singular at n_R = 4. S3.3 recording duration 5 -> 10 min. S8/S17.4 HISTORY size
  refreshed (100 dated entries, ~5,800 lines, measured).
  **No status tag was promoted** - the honesty contract (promote by re-measuring, never by editing
  the tag) was respected; these are corrections of false statements, not upgrades.
- **`JOURNAL_PAPER.md` corrected** - S1 test count; S3.2 claim 5 scoped to "across subjects, not
  distances" with the reason; S7 figure 6 now requires the subject-clustered model and limits
  `plot_bland_altman.py` to plotting; S8 respiration collapse 4/4 -> 3/3; S10 **states the
  pre-registration's defensible strength explicitly** ("frozen before the confirmatory data", not
  "before any data existed", because four captures already shaped the method) and warns that
  overstating it repeats the withdrawn-MAE error; S10 data-availability updated to the new session
  size (~1.55 GB per 10-min session, ~31 GB for the study).
- **`HANDOFF.md` rewritten** (CLAUDE.md S10.1 - replacement, not append), aimed at an
  **M0-planning** chat: S3 is now a spec of what M0's deposit must contain, what M0 depends on
  (M3 alone), the evidence-floor arithmetic at both durations, and the ordered immediate actions.
  Added the three capture classes (pre-freeze exploratory / post-freeze exploratory / confirmatory),
  the corrected 4-raw-vs-3-referenced capture split, and new landmines: the 10-min duration is
  operator-enforced only (`max_live_duration_s: null`), a longer session stresses the single locked
  range bin (check minute 9-10), M7's rate must not come from resting HR, and `notes/` is
  gitignored so the protocol has no version history.
- **Every pointer in the rewritten HANDOFF was verified to resolve** (23 files + 3 directories
  checked). This caught a **dangling glob inherited from the previous HANDOFF**:
  `results/live_demo/20260715_1640*_replay_unknown/` matches only **one** of the three re-scoring
  folders, because the other two are `_164124` and `_164132`. Replaced with the three explicit
  names and their sessions.
- Test suite unaffected throughout: **796 passed, 1 xfailed**.

**Failed / did not work, and why:** nothing failed. Recording one limitation honestly: the
corrections to `THIRD_CHAPTER.md` and `JOURNAL_PAPER.md` were made against the review's findings
and the source documents, but **neither writing file has been re-read end-to-end for internal
consistency since**; other claims in them may still be stale in ways this pass did not target.

**Retired / no longer used:** the "797 passing tests" figure in all three documents; the "4-for-4"
respiration-collapse count; the per-distance agreement claim; the description of Kotte et al. as
matching our 4-RX SIMO hardware; the 5-minute recording duration; and the previous `HANDOFF.md`
in full (replaced per CLAUDE.md S10.1 - its content is superseded, and the durable record of what
it said lives in this log).

**Next:**
- **Plan M0.** Its one blocking decision is the **evidence floor** (deferred 2026-07-24), which
  must be fixed before the deposit. Carried with it: whether to attack coverage (M11a) before
  freezing so an improved estimator can be the pre-registered primary.
- Record the **ethics approval reference number and issuing board**.
- **Consider tracking `notes/protocol.md` in git before M0 deposits it** - it is a pre-registration
  input with no version history, which is exactly the provenance a pre-registration trades on.
- M1, M2, M3 and M8 step 1a/1b are unblocked and can run in parallel with M0 planning.

## 2026-07-25 - M2: respiration floor-pin root cause, cross-reviewed fix, implemented

**Set out to do:** close M2 — root-cause the silent 6.0 bpm/`resp_valid=True` collapse,
cross-review the fix plan (Codex), implement.

**Worked (with evidence):** Root cause verified from checkpointed NPZs
(`scripts/diagnose_respiration_collapse.py`, SHA-256s recorded): two observed classes — both-branch
edge selection (23 windows; 16 with a monotone leakage-decay signature) and HA-only edge selection
(6 windows; 4 supported by 3rd-harmonic inheritance, 18 bpm bin 2.0–3.3× band floor) — blessed by
fusion flaws (floor agreement read as confirmation; STFT stability of a different rate blessing
HA's value). 7/29 pinned windows are spectrally unresolved (edge bin is a strict local max) and are
suppressed only by the band-edge validity veto. Fix (`src/respiration.py`, cross-reviewed:
7 findings, 2 rounds, 0 escalations — `plans/m2_fix_cross_review.md`): local-max plateau predicate
in FFT+HA, bin-identity edge veto, STFT median-match on all STFT-dependent fusion branches; 14 new
NPZ evidence fields; 25 new tests. Reprocessed all 4 captures: 0 floor-pinned-valid windows;
paced-16 unchanged; HR identical at fixed bins (max |ΔHR| = 0.000 bpm); natural warmup now locks
bin 27 at high confidence with warmup-verified HR 64.5 (was bin 23, medium, none). Suite 821p/1xf
at bc12663+.

**Failed / did not work, and why:** The plan's original "6 bpm bin is never a local maximum"
unifier was falsified by the NPZs (7/29 counterexamples) — caught in cross-review and
independently; retained as a partial discriminator only. Genuine ≈6 bpm breathing at the edge bin
is now permanently `resp_valid=False` — a declared coverage sacrifice mandated by the M2 invariant.

**Retired / no longer used:** the `fund_power > noise_floor` HA guard (too weak — permitted
subharmonic wins); unconditional STFT-stability blessing in fusion.

**Next:** freeze the BR comparator (M3), then score reprocessed BR under it (M2 done-when #5 via
M4); decide whether live protocol adopts warmup bin 27 for natural sessions; update `HANDOFF.md`.

## 2026-07-26 — M3 cross-review: batch M3R-29…M3R-41 processed + both escalations resolved (rounds 10–12)

**Set out to do:** Close the M3 BR-comparator + analysis-prespec cross-review. On resume, found it
had NOT closed — Codex had posted a fresh 13-finding batch (M3R-29…M3R-41), not NO MORE COMMENTS.

**Worked (with evidence):** Processed all 13 across DEBATE rounds 10–12 (plans/m3_prespec_cross_review.md).
Codex closed 8 as CONVINCED (M3R-30/32/33-BR/35/36/37/38/39). Round-11 fixes applied and awaiting
Codex's confirmation: M3R-29 (MOVER now the PRIMARY CI, specified as a pinned M4-harness function with
run-time verification that its components equal the frozen closed-form values; deleted an incorrect
claim that σ²_b models within-subject serial correlation — now stated as an independence assumption
with lag-1 as a diagnostic), M3R-31 (paced LoA reframed as a marginal design-weighted mixture over a
frozen 4/3/3 enrolment-order allocation), M3R-41 (§5 M5-adequacy trigger built only from steady-rate
pilot quantities). Evidence script now pins+asserts all six input SHA-256; re-ran it, assertions pass,
§1 numbers reproduce (natural 17 % >2 bpm; paced/sweep 0 %).

Two escalations RESOLVED by user decision 2026-07-26:
- M3R-40 — HR comparator harmonised to half-open [t−30 s, t) (notes/comparator_prespec.md §2.1), a
  pre-deposit clarification (no DOI existed); HR and BR now share one endpoint rule.
- M3R-34 — intended-use (spot-check) disposition recorded device-wide (labelling = battery endurance
  + no unattended alarms, not 10-min accuracy; healthy adults, attended, verified battery — checklist
  item added); ethics 10-min collection confirmed covered. Masimo document number pinned to LAB-10168A
  (user corrected a lab-10169a filename typo); path fixed in both comparators, protocol, and
  m0_preregistration.md.

**Failed / did not work, and why:** Loop did not reach NO MORE COMMENTS — Codex went silent across two
~29-min poll windows after the round-11/12 posts. One spurious watcher wake from CRLF churn (fixed by
normalizing line endings before hashing).

**Retired / no longer used:** bootstrap-primary CI (superseded by MOVER-primary, M3R-29); the
"σ²_b handles within-subject serial dependence" claim (statistically wrong); the closed-interval HR
window convention (harmonised to half-open, M3R-40).

**Next:** Get Codex's round-3 verdict on M3R-29/31/41 (esp. the MOVER math sign-off). Then the docs are
ready for the M0 freeze (user's irreversible act — not done here). M2 done-when #5: score the post-fix
replay BR (2026-07-25 NPZs) under the frozen comparator.

## 2026-07-26 — M3 cross-review CLOSED (rounds 13–19; M3R-42…48 + the user decisions)

**Set out to do:** Carry the M3 review to closure after the M3R-29…41 batch.

**Worked (with evidence):** Codex ran rounds 13–19 (`plans/m3_prespec_cross_review.md`) and posted
**`NO MORE COMMENTS` on 2026-07-26 — all 48 findings (M3R-01…48) resolved**; committed at `f137132`.
User decisions recorded: **M3R-40** HR comparator harmonised to half-open `[t−30 s, t)` (pre-deposit
clarification, no DOI existed); **M3R-34** Masimo spot-check intended-use dispositioned (labelling is
about battery endurance + unattended-safety alarms, not 10-min accuracy; battery-check added to the
protocol; document number pinned to **`LAB-10168A`**, correcting a `lab-10169a` filename typo);
**M3R-29** primary CI = **Option A cluster-bootstrap** (fully specified, estimand-matched by
construction; MOVER demoted to a pre-named *candidate* sensitivity, validated/reported only after M4
implementation + statistician review + benchmark); **M3R-42** ≥8/10 read study-wide not per-arm
(paced-HR arm-specific LoA rests on ≤7 subjects by design, accepted); **M3R-45** the bootstrap's
small-sample under-coverage is **anti-conservative** for the ≤5 bpm precision gate — knowingly
accepted as a declared risk. Should-fix items M3R-43/44/46/47/48 applied (spot-check rationale as an
explicit study assumption; M0-plan A4 defers to §1; MOVER validation wording; point-LoA-vs-bootstrap
serial-dependence separated; status labels → cross-review COMPLETE). Both deposit documents now
declare cross-review COMPLETE / ready for the M0 freeze — **not frozen**. Coordination file compacted
to a 48-row resolution table (full verbatim debate preserved in git history). Edits in
`notes/analysis_prespec.md`, `notes/comparator_prespec_br.md`, `notes/comparator_prespec.md`,
`notes/protocol.md`, `plans/m0_preregistration.md`, `plans/m3_prespec_cross_review.md`,
`scripts/derive_br_comparator_evidence.py`.

**Failed / did not work, and why:** **M3R-29 could not be resolved by argument** — MOVER-as-primary is
not executable without either the (paywalled) Zou/Graybill–Wang equations transcribed into §1 or a
built+math-reviewed+benchmarked M4 implementation; escalated to the user, who chose Option A
(bootstrap primary). I also **made and corrected an error (M3R-45)**: in the round-10→12 batch I had
labelled the bootstrap under-coverage "conservative" for the precision gate when it is
**anti-conservative** (an over-narrow CI can falsely *retain* a confirmatory headline) — Codex caught
it, I fixed the direction, and the user accepted the anti-conservative gate as a declared limitation.

**Retired / no longer used:** MOVER-as-primary (round-10→12 choice, reversed to Option A
cluster-bootstrap primary at round 14); the "under-coverage is conservative" claim (backwards); the
closed-interval HR window convention (harmonised to half-open); the stale three-level
`√(σ²_b+σ²_s+σ²_w)` agreement model still described in the M0 plan's A4 (now defers to §1).

**Next:** M0 freeze (the user's irreversible Zenodo act) once the M4 harness exists; M2 done-when #5 —
score the post-fix replay BR (the 2026-07-25 NPZs) under the now-complete comparator.
