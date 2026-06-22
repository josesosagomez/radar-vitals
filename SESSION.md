# Session Memory

> Update at the END of every working session. Newest entry on top.
> Three questions: What worked (with evidence)? What failed? What's next?

---

## Current state

- **Where we are (2026-06-21):** Cross-session ECA+AHET run (`exp_eca_all`) on all 10
  sessions completed. Only 2 sessions work well (exp008 MAE 5.20, exp009 MAE 8.40).
  All supine sessions (exp001, exp002, exp007) fail with known Blocker 6 (harmonic chain).
  Seated no-back sessions (exp003-005, exp010) fail with ~20 bpm negative bias, suspected
  4th respiratory harmonic at ~4×0.3 Hz ≈ 72 bpm landing in the cardiac band.
  Data pipeline fully built: 10 time-domain HDF5 cubes in `data/processed/time_domain_cubes/`
  (~7.9 GB total) and quality mask script ready to run (19/19 unit tests pass).

- **Confirmed working (seated frontal, 1.3–1.5 m):**
  - exp008 (ECA+AHET, k_max=6, locked_bin=29, 1.264 m): MAE 5.20, RMSE 6.82, NaN 0.
    Evidence: `results/exp_eca_all/20260621_095804/`.
  - exp009 (seated chair-back, 1.44 m, bin 33): MAE 8.40.
  - Historical exp002/cap1: MAE 5.17, RMSE 6.90, bias −2.21 bpm (N=21).
    Evidence: `results/exp002_harmonic_rejection/20260615_110410/`.

- **Failing:**
  - Supine overhead (exp001, exp002, exp007): Blocker 6 — k×f_r harmonics overlap
    cardiac band; ECA cannot distinguish or leaves respiratory harmonics dominant.
  - Seated no-back (exp003 MAE 11.37, exp004 MAE 20.76, exp005 MAE 22.70, exp010 MAE 20.43):
    ~20 bpm negative bias; heart_spectrum inspection needed.
  - Seated chair-back (exp006 MAE 11.47): partial failure.

- **Open blockers:**
  - **Blocker 5: open** — harmonic exclusion at candidate selection.
  - **Blocker 6: REVISED — partially refuted.** k_max reduction alone insufficient.
  - **Blocker 3: deferred.**
  - Blockers 1, 2, 4: resolved.

- **Immediate next steps:**
  1. Run `python -X utf8 scripts/add_quality_mask.py --all` → check `results/save_quality_mask.log`.
  2. Inspect heart_spectrum intermediates from exp_eca_all for exp003–005 to determine
     whether 4th respiratory harmonic is the dominant candidate in the cardiac band.
  3. Based on (2): implement Blocker 5 harmonic exclusion or investigate geometry fix.

---

## Log (newest first)

### 2026-06-21 — Cross-session ECA+AHET; bin correction; num_tx fix; HDF5 pipeline; quality mask

**What was done:**

**1. Cross-session ECA+AHET (`exp_eca_all`) — all 10 sessions**

Ran `experiments/exp_eca_all/run.py` on all 10 sessions via `data/manifest.local.csv`.
Results: `results/exp_eca_all/20260621_095804/`.

| Session | Posture | Bin | Distance | MAE (bpm) | RMSE (bpm) | NaN/total |
|---|---|---|---|---|---|---|
| exp001 | supine overhead | 28 | 1.221 m | 17.12 | 22.07 | 15/36 |
| exp002 | supine overhead | 28 | 1.221 m | 17.85 | 23.16 | 26/56 |
| exp003 | seated no-back | 31 | 1.351 m | 11.37 | 14.32 | 7/17 |
| exp004 | seated no-back | 31 | 1.351 m | 20.76 | 23.71 | 1/17 |
| exp005 | seated no-back | 30 | 1.307 m | 22.70 | 26.02 | 0/17 |
| exp006 | seated chair-back | 31 | 1.351 m | 11.47 | 14.35 | 7/17 |
| exp007 | supine overhead | 28 | 1.221 m | 23.22 | 26.15 | 36/56 |
| exp008 | seated no-back | 29 | 1.264 m | 5.20 | 6.82 | 0/9 |
| exp009 | seated chair-back | 33 | 1.439 m | 8.40 | 10.89 | 2/13 |
| exp010 | seated no-back | 30 | 1.307 m | 20.43 | 23.76 | 0/13 |
| **Overall** | | | | **16.38** | **21.13** | 94/271 |

Only exp008 and exp009 meet the target. Per-session overlay plots saved alongside comparison.csv.

**2. Bin correction — exp008 locked_bin 27 → 29**

Root cause: initial bin 27 came from visual inspection of `mean_range_profile` image
(uses mean of |magnitude|). The correct method is `vitals.select_range_bin()` which
uses `mean(|.|²)` over ALL analysis frames. These give different results when adjacent
bins have similar magnitude but different energy distributions. Script
`scripts/reselect_bins.py` re-ran energy-based selection on all 10 sessions; only
exp008 changed (27→29, 1.177 m→1.264 m). Effect: MAE 9.39→5.20, NaN 11→0.
Manifest updated: `data/manifest.local.csv`.

**3. num_tx corrected across all configs (2 → 1)**

User confirmed only 1 TX used in all captures. Fixed `num_tx: 2 → num_tx: 1` in all
7 active experiment configs. This field is metadata only — not used in decode math —
so no data was corrupted. Results/config_used.yaml snapshots left unchanged (frozen).
Files changed: `experiments/exp_eca_all/config.yaml`,
`experiments/exp000_range_plot/config.yaml`,
`experiments/exp004_window_length/config.yaml`,
`experiments/exp002_harmonic_rejection/config.yaml`,
`experiments/exp001_offline_baseline/config.yaml`,
`experiments/exp003_generalisation/config_chair_back.yaml`,
`experiments/exp003_generalisation/config_chair_no_back.yaml`.

**4. HDF5 time-domain cubes — `scripts/save_time_domain_cubes.py`**

Saved all 10 sessions to `data/processed/time_domain_cubes/<session_id>.h5`.
Each file: `/cube` dataset, shape `(N_frames, 32, 4, 256)` = (frames, chirps, rx,
adc_samples), dtype complex64, NO transpose. Time domain only.
Key metadata attributes: `session_id`, `posture`, `possible_distance_cm` (NOT
`distance_cm`; `locked_bin` NOT stored), `radar_start_epoch_seconds`,
`stationary_intervals`, `radar_orientation`, `num_tx=1`, `num_rx=4`,
`num_adc_samples=256`, `num_chirps_per_frame=32`, `frame_rate_hz=20.0`,
`range_resolution_m=0.0436`, `git_commit`, `source_bin_files`, `num_frames`.
File sizes: 364 MB (exp008) to 1113 MB (exp002); total ~7.9 GB.

**5. Quality mask — `scripts/add_quality_mask.py` (ready to run)**

Adds `/quality_mask` (bool, N_analysis=total_frames−600) and `/quality_metrics/`
group to existing .h5 files. True = healthy. Indexes cube[600:] only; frames NOT
deleted (preserves phase continuity for unwrapping).
Config: `scripts/quality_mask_config.yaml`.
Hard failure checks: ADC clipping (`|sample| ≥ 32767`) and dead frame (energy < 1%
of session median). Extensible — soft failures (motion spike, RX imbalance) planned
but not yet implemented.
Log: `results/save_quality_mask.log`.
Tests: `tests/test_quality_mask.py` — 19/19 pass.

**Run command (not yet run on real data — user to execute):**
```
python -X utf8 scripts/add_quality_mask.py --all
```

**What failed (cross-session):**
- Supine sessions fail because of Blocker 6 (harmonic chain in cardiac band) — unchanged.
- Seated no-back sessions fail with ~20 bpm NEGATIVE bias. Most likely the 4th respiratory
  harmonic (~4×0.3 Hz ≈ 72 bpm or 4th × actual f_r) landing in the cardiac band and
  winning the peak pick. Requires heart_spectrum inspection to confirm.
- exp003 is partially better (MAE 11.37) — possibly different f_r putting harmonics in a
  different position. Inspect along with exp004/exp005.

**What was NOT changed:**
- `src/vitals.py`, `src/radar_io.py`, `src/compare.py` — no DSP logic modified.
- `data/raw/` — never touched.
- Results/config_used.yaml snapshots — frozen run records.

**Evidence:**
- Cross-session results: `results/exp_eca_all/20260621_095804/`
- Bin reselection script: `scripts/reselect_bins.py`
- Overlay plots: per-session `overlay.png` in each session's results subdirectory
- HDF5 cubes: `data/processed/time_domain_cubes/` (10 files)
- Quality mask tests: `tests/test_quality_mask.py` (19/19 pass)

---

### 2026-06-15 — exp005 adaptive k_max investigation; hypothesis REFUTED for supine captures

**What was done:** Wrote and ran `scripts/test_adaptive_kmax.py` (diagnostic-only; no pipeline
code modified) to test whether reducing k_max from 6 to the largest safe value (highest
suppressed harmonic stays ≥5 bpm below cardiac floor) fixes the supine failure for cap3_retake.
Ran the same script on cap5 (held-out preview only). No config files or src/ files were modified.

**Adaptive k_max formula (implemented in diagnostic script only):**
```
k_max_adaptive = floor((CARDIAC_FLOOR_HZ - MARGIN_HZ) / f_r_hz)
               = floor((0.833 - 0.083) / f_r_hz)
               clamped to [1, 6]
```
Rationale: the highest suppressed harmonic (k_max × f_r) must stay at least 5 bpm below the
cardiac band floor (50 bpm). For f_r = 14 bpm: k = floor(0.75/0.233) = floor(3.2) = 3.
For f_r = 13 bpm: k = floor(0.75/0.217) = 3 as well.

**cap3_retake (development, k_max adaptive vs fixed=3 vs canonical k_max=6):**

| Mode | N_finite | N_NaN | MAE | RMSE | Bias | AHET pass |
|---|---|---|---|---|---|---|
| Canonical k_max=6 | 49 | 32 | 23.53 bpm | 26.25 | −19.61 | 36/49 |
| Adaptive k_max | 40 | 41 | 23.13 bpm | 24.18 | −22.47 | 38/40 |
| Fixed k_max=3 | 40 | 41 | 23.13 bpm | 24.18 | −22.47 | 38/40 |

- Delta MAE vs canonical: −0.40 bpm (trivial). NaN count INCREASES from 32 to 41.
- k_max distribution: k=2 (6 windows), k=3 (69 windows), k=4 (4), k=5 (2).
- Adaptive = fixed k_max=3 (formula gives k=3 for 69/81 windows): formula confirmed correct.
- AHET pass rate 95% on WRONG answers — algorithm is confident and wrong.

**Root cause of adaptive k_max failure:**
With k_max=3, harmonics k=4 (4×14=56 bpm), k=5 (70 bpm), k=6 (84 bpm) are left in the
spectrum. In supine overhead geometry, respiratory harmonics are STRONGER than the cardiac
signal across the entire cardiac band. The k=4 harmonic (~56 bpm) dominates. AHET then
validates this false candidate: it finds the k=8 harmonic (~112 bpm) as the "second harmonic"
(2×56=112 bpm, within AHET ±6 bpm search window). Result: 38/40 windows confidently return
~56 bpm when true HR is 75–96 bpm.

Key insight: the AHET second-harmonic check does NOT discriminate between respiratory harmonic
chains (k=4 + k=8) and true cardiac. Respiratory harmonics form self-consistent N·f_r chains.

**cap5 (held-out preview — NOT used for algorithm decisions):**

| Mode | N_finite | N_NaN | MAE | Bias | AHET pass |
|---|---|---|---|---|---|
| Canonical k_max=6 | 54 | 27 | 19.50 bpm | −17.23 | — |
| Adaptive k_max=3 | 53 | 28 | 23.67 bpm | −21.76 | 53/53 (100%) |

- Delta MAE: +4.17 bpm — WORSE than canonical.
- 6 windows with error < 5 bpm (Wi=11,21,22,67,68,79): these windows have 6×f_r ≈ true HR
  (f_r≈12.75, HR≈77 bpm → 6×12.75=76.5 bpm). The algorithm is finding the k=6 respiratory
  harmonic, not the cardiac signal — it happens to give a small error by coincidence.
- Result confirms: adaptive k_max is not a viable fix for cap5 either.

**What was NOT done (hard constraints honoured):**
- Did not modify vitals.py, radar_io.py, compare.py, or any config.
- Did not implement Blocker 5 (harmonic exclusion at candidate selection).
- Did not run on cap4.
- Did not use cap5 results to drive any algorithm decision.

**What failed:** The adaptive k_max hypothesis. Predicted: ~10 bpm improvement on cap3_retake.
Actual: 0.40 bpm improvement, NaN count increases. The hypothesis was too optimistic because
it only considered the k=6 suppression problem (ECA erasing cardiac at k×f_r ≈ HR), not the
second-order problem (leaving k=4 unsuppressed hands the cardiac band to a stronger respiratory
harmonic that AHET cannot distinguish from a true cardiac candidate).

**Diagnosis update:**
Blocker 6 is more severe than originally stated. There is NO k_max value that satisfies
both constraints simultaneously for cap3_retake (f_r=14, HR≈84 bpm):
- k_max ≥ 6 suppresses the cardiac signal (6×14=84 bpm erased by ECA)
- k_max ≤ 3 leaves k=4 (56 bpm) unsuppressed; k=4 is stronger than cardiac; AHET fooled
- k_max = 4 or 5: k=5 (70 bpm) or k=6 (84 bpm) still create false candidates

**Next step (reviewed, not yet approved for implementation):**
1. Inspect heart_spectrum intermediates from exp004 canonical run (k_max=6) for cap3_retake
   to determine whether any residual cardiac signal is visible after ECA, or if the band
   is flat noise. This determines whether Blocker 5 (harmonic exclusion) can help.
2. If residual cardiac is visible: design Blocker 5 (reject candidates at k×f_r for all k),
   prototype in a new diagnostic script, validate on cap3_retake, preview on cap5.
3. If flat noise: current ECA approach is SNR-limited for supine geometry;
   need either geometry change or non-linear source separation.
4. Do NOT modify vitals.py until spectrum inspection is reviewed.

**Evidence:** `results/diagnostics/adaptive_kmax/cap3_retake_20s_adaptive_kmax.csv`,
`cap3_retake_20s_fixed_kmax3.csv`, `cap5_20s_adaptive_kmax.csv`.

---

### 2026-06-15 — exp004 cap3_retake + cap4 + cap5 pipeline run; supine captures fail

**What was done:** Ran exp004 pipeline on three new 2026-06-15 supine captures
(cap3_retake, cap4, cap5). Added all three to `experiments/exp004_window_length/config.yaml`
using a `bin_files` list (two split .bin paths each). Updated `run.py` to detect `bin_files`
vs `bin_file`, compute total-byte frame counts for multi-file captures, and pass a list to
`read_adc_bin`. Added `--captures` CLI argument so individual captures can be run without
re-running cap1/cap2/cap3. Generated 12 diagnostic plots (A/B/C/D × 3 captures) via
`scripts/diag_new_captures.py`, saved to `results/diagnostics/per_window_error/`.

**File / frame verification (all three captures):**

| File | Bytes | Frames (raw) |
|---|---|---|
| `cap3_retake_..._0.bin` | 1,073,741,760 | 8191.999512 (partial) |
| `cap3_retake_..._1.bin` | 105,906,240 | 808.000488 (partial) |
| Combined | 1,179,648,000 | 9000 exactly ✓ |
| `cap4_..._0.bin` | 1,073,741,760 | 8191.999512 |
| `cap4_..._1.bin` | 105,906,240 | 808.000488 |
| Combined | 1,179,648,000 | 9000 exactly ✓ |
| `cap5_..._0.bin` | 1,073,741,760 | 8191.999512 |
| `cap5_..._1.bin` | 105,906,240 | 808.000488 |
| Combined | 1,179,648,000 | 9000 exactly ✓ |

All 6 CSV files (3 LogFile, 3 masimo) present. Each capture: bin 28 (1.221 m) locked,
Masimo coverage 98.6%, PI 100%. Mechanically clean.

**cap3_retake results (development):**
- f_r = 14 bpm stable; 4×f_r = 56 bpm (below HR), **6×f_r = 84 bpm (inside HR 75–96 bpm)**
- Baseline 20 s: MAE 23.45, RMSE 26.07, bias −19.68 bpm (N=49)
- Condition 20 s: MAE 23.53, RMSE 26.25, bias −19.61 bpm (N=47 / 32 NaN)
- Condition 25 s: MAE 23.99, bias −23.90 bpm (N=59 / 20 NaN)
- Condition 30 s: MAE 24.14, bias −24.06 bpm (N=57 / 22 NaN)
- Intersection (N=37): **20 s 24.78, 25 s 23.60, 30 s 23.08 bpm — monotonically improving but far above acceptable**
- Results: `results/exp004_window_length/20260615_181343/cap3_retake/`

**cap4 results (development):**
- f_r = 17–19 bpm; **4×f_r = 68–76 bpm (overlaps HR 72–82 bpm)**; 5×f_r = 85–95 bpm
- Baseline 20 s: MAE 17.08, RMSE 18.37, bias −16.15 bpm (N=48 / 33 NaN)
- Condition 20 s: MAE 16.91, RMSE 18.20, bias −15.96 bpm (N=47 / 32 NaN)
- Condition 25 s: MAE 18.78, bias −18.75 bpm (N=60 / 19 NaN)
- Condition 30 s: MAE 19.20, bias −17.75 bpm (N=59 / 20 NaN)
- Intersection (N=43): **20 s 17.37, 25 s 18.74, 30 s 18.55 bpm — partial improvement only**
- Results: `results/exp004_window_length/20260615_181821/cap4/`

**cap5 results (held-out — NOT used for algorithm decisions):**
- f_r = 13 bpm stable; **6×f_r = 78 bpm (inside HR 69–94 bpm)**
- Baseline 20 s: MAE 19.16, RMSE 22.22, bias −16.93 bpm (N=53 / 28 NaN)
- Condition 20 s: MAE 19.50, RMSE 22.43, bias −17.23 bpm (N=52 / 27 NaN)
- Condition 25 s: MAE 19.70, bias −18.28 bpm (N=53 / 26 NaN)
- Condition 30 s: MAE 20.41, bias −19.34 bpm (N=62 / 17 NaN)
- Intersection (N=41): **20 s 19.51, 25 s 18.01, 30 s 19.69 bpm — partial improvement only**
- Results: `results/exp004_window_length/20260615_181912/cap5/`

**Cross-capture summary — all exp004 captures:**

| Capture | Posture | Role | BR | 4×f_r | 6×f_r | k×f_r ≈ HR? | 20 s MAE* | 25 s MAE* | 30 s MAE* | Monotonic? |
|---|---|---|---|---|---|---|---|---|---|---|
| cap1 | seated | dev | ~15–19 | ~60–76 | ~90–114 | No | 5.55 | 3.83 | 3.67 | YES |
| cap2 | seated | dev | ~17 | ~68 | ~102 | marginal | 8.80 | 8.51 | 4.46 | YES |
| cap3 | seated | dev | ~19–20 | ~76–80 | ~114–120 | YES (4×) | 13.50 | 14.14 | 16.19 | NO |
| cap3_retake | supine | dev | 14 | 56 | 84 | YES (6×) | 24.78 | 23.60 | 23.08 | YES |
| cap4 | supine | dev | 17–19 | 68–76 | 102–114 | YES (4×) | 17.37 | 18.74 | 18.55 | partial |
| cap5 | supine | held-out | 13 | 52 | 78 | YES (6×) | 19.51 | 18.01 | 19.69 | partial |

*Intersection values (same windows compared across all three conditions).
cap1/cap2/cap3 from prior session. cap5 = held-out; values shown for completeness only.

**Root cause — new finding (6×f_r ≈ HR):**
- The pre-session plan only checked that 4×f_r < 55 bpm (bottom of cardiac band).
  cap3_retake BR=14 satisfies this: 4×14=56 bpm. But 6×14=84 bpm lands squarely in
  the HR range (75–96 bpm).
- ECA first pass (no cardiac candidate) removes ALL k=1..k_max harmonics unconditionally.
  With k_max=6 and f_r=14, the subspace suppression nulls out energy at 84 bpm, erasing
  the cardiac signal before candidate selection begins.
- Same mechanism for cap5: f_r=13, 6×13=78 bpm inside HR 69–94 bpm.
- For cap4: f_r=17-19, 4×f_r=68-76 bpm overlaps HR 72-82 bpm — the k=4 hard floor
  removes cardiac signal; additionally 5×f_r=85-95 bpm may land in HR range.
- **Why supine is worse than seated:** in the seated frontal geometry the chest wall
  oscillation (cardiac) is the dominant target at the locked range bin. In the supine
  overhead geometry, the vertical body motion from breathing dominates; cardiac micro-
  motion has lower SNR. When ECA also removes the spectral region containing the cardiac
  peak, nothing remains above the noise floor.
- This failure is UPSTREAM of Blocker 5: Blocker 5 guards at candidate selection;
  ECA first-pass removes the energy before any candidate exists.

**Why all supine captures fail regardless of BR target:**
- BR < 9.2 bpm would be required to keep 6×f_r below 55 bpm. That is not a feasible
  resting breathing rate.
- Even if 6×f_r is clear, k=4 or k=5 may still land in the HR range at typical HR.
- Conclusion: the current k_max=6 algorithm is fundamentally incompatible with
  supine overhead capture geometry given typical resting HR (60–100 bpm) and BR (12–20 bpm).

**New Blocker 6 (open):**
ECA first-pass harmonic removal with k_max=6 suppresses cardiac signal when k×f_r
(k > 4) falls inside the cardiac band in supine low-SNR geometry. Possible fixes:
(a) Reduce k_max to 4 (hard floor only) — investigate first; cheapest change;
(b) Redesign first-pass ECA to skip removal when the harmonic bin overlaps the
    cardiac band (requires knowing the cardiac band before ECA runs);
(c) Investigate whether supine SNR can be improved via multi-bin coherent combination
    before phase extraction.
Must be resolved before Blocker 5 (harmonic exclusion) can be tuned on the new captures.

**What worked / failed:**
- WORKED: mechanical pipeline (split-file decoding, frame counts, Masimo alignment,
  AHET, evidence logging) — all three captures processed cleanly end-to-end.
- FAILED: all three supine captures, MAE 17–25 bpm vs ≤6 bpm target. Failure is
  algorithmic / ECA-structural, not data quality.

**Next step:**
1. Before implementing Blocker 5, investigate Blocker 6: run exp004 on cap3_retake
   with k_max reduced to 4 and to 3 — does the cardiac signal reappear in the spectrum?
   Use intermediate dumps (heart_band_spectrum, chosen_peak_hz) to confirm.
2. If k_max=4 restores cardiac visibility on cap3_retake and cap4, tune Blocker 5 on
   the development set and validate on held-out cap5.
3. If cardiac SNR is too low regardless of k_max, investigate multi-bin combination
   before committing to a supine-geometry fix.
4. Do NOT use cap5 results (any condition) to guide algorithm decisions.

---

### 2026-06-15 — cap3_retake collected; split-file decoder extended

**cap3_retake capture details:**
- Date: 2026-06-15, recorded 16:04:27–16:11:59 local (UTC+3), duration 452 s per LogFile.
- Posture: supine on floor mat, radar on tripod directly overhead.
- Radar-to-chest distance: bin 28 at 1.221 m (confirmed from range profile — plausible for
  supine overhead mount at ~1.2–1.3 m). Adjacent bins 27 (1.177 m) and 29 (1.264 m) visible.
- BR: 14 bpm stable (confirmed on Masimo throughout); 4×f_r = 56 bpm, well below HR range.
- HR: started elevated (~96 bpm, subject had just lain down), settled to ~75–80 bpm by
  mid-recording. The 30 s trim (600 frames) removes the settling period.
- Frame count: 9000 frames = 450 s at 20 Hz (LogFile duration 452 s — 2 s difference due
  to LogFile rounding, file-size-inferred count is authoritative).
- Files in `data/raw/`:
  - `cap3_retake_20260615_160417_0.bin`: 1,073,741,760 bytes (8191.999512 raw frames)
  - `cap3_retake_20260615_160417_1.bin`: 105,906,240 bytes (808.000488 raw frames)
  - `cap3_retake_20260615_160417_LogFile.csv`
  - `cap3_retake_20260615_160417_masimo.csv`

**Split-file issue — mid-frame split at the 1 GB boundary:**
mmWave Studio hit its 1,024 MB file size limit and split the recording. The split point does
NOT fall on a frame boundary: `_0.bin` contains 8191 complete frames plus 131,008 bytes of a
partial frame; `_1.bin` starts with the remaining 64 bytes of that frame, then 808 complete
frames. Total raw bytes = 1,179,648,000 = 9000 × 131,072 exactly.
Implication: concatenating decoded cubes per-file fails. Raw int16 bytes must be concatenated
first, then the combined stream decoded.

**`read_adc_bin` extended — `src/radar_io.py`:**
- Signature changed from `read_adc_bin(bin_path, cfg, trim_frames=0)` to
  `read_adc_bin(path, cfg, trim_frames=0)` where `path` accepts a single path or a
  `List[Union[str, Path]]`.
- `from typing import List, Union` added to imports.
- New multi-file branch: validates all files exist before reading; computes `total_bytes`
  from file sizes; validates `total_bytes % bytes_per_frame == 0` on the combined size (not
  per-file — individual files may not be frame-aligned); concatenates raw int16 words into
  a single array using `np.empty` + in-place copy (avoids peak-memory spike from list of
  large arrays); then decodes with the shared 4-word LVDS path.
- Single-file branch: unchanged. All existing callers pass a single path and are unaffected.
- Sanity-check gate `[20–45]` guarded for small `num_adc_samples` (was `argmax` on an empty
  slice; now skips gate sub-check with a NOTE print).

**New tests — `tests/test_radar_io_split.py` (7 tests):**
1. `test_single_file_passthrough` — regression guard: single-path behaviour unchanged.
2. `test_two_file_split_shape` — frame-aligned 2-file split → correct total shape (8 frames).
3. `test_two_file_split_content_order` — frames from `_0` precede frames from `_1`.
4. `test_frame_count_consistency` — total frames == sum of `infer_num_frames` per file (frame-aligned case).
5. `test_missing_file_raises_before_read` — `FileNotFoundError` before any data is read.
6. `test_single_element_list_equals_single_path` — `[path]` produces same cube as `path`.
7. `test_mid_frame_split_decodes_correctly` — split mid-frame (1.5 frames per file),
   verifies all 3 frames decode correctly. This is the actual cap3_retake scenario.

**Test count:** 106/106 unit tests pass; 8 integration tests skipped by default.
Evidence: `pytest tests/ -q --tb=short` (2026-06-15).

**Range profile — frame 600 (first frame after 30 s trim):**

| Rank | Bin | Range (m) | Energy |
|---|---|---|---|
| 1 | 28 | 1.221 | 5.50e+08 |
| 2 | 27 | 1.177 | 1.75e+08 |
| 3 | 29 | 1.264 | 1.45e+08 |
| 4 |  2 | 0.087 | 1.07e+08 |
| 5 | 38 | 1.657 | 6.53e+07 |

Locked bin for cap3_retake: **bin 28 at 1.221 m**. Peak is clear and dominant within the
gate. Bins 27 and 29 are adjacent sidelobes. Bin 2 is near-DC leakage (expected).
No anomalies. The supine overhead geometry produces a single clean target peak — good SNR.

**What did NOT change:**
- No experiment configs modified.
- No ECA, AHET, or DSP logic changed.
- `infer_num_frames` unchanged (not applicable to individual split files; total-byte
  validation handles the cap3_retake case).

**Next step:**
1. Add cap3_retake to `exp004/config.yaml` as a new capture entry (locked_bin 28,
   expected_frames 9000, files as a list of two paths).
2. Collect cap4 (BR 16–18 bpm, development) and cap5 (BR 12–14 bpm, held-out).
3. Run exp004 pipeline on cap3_retake and cap4.
4. Implement Blocker 5 harmonic exclusion (candidate rejection when k×f_r ≈ HR candidate).
5. Tune exclusion tolerance on development set (cap1 + cap3_retake + cap4).
6. Validate on held-out cap5.

---

### 2026-06-15 — Per-window diagnostic, re-capture plan, Blocker 5 (planning session)

**What was analysed (no code written, no pipeline run this session):**

**Diagnostic findings — `scripts/diag_per_window_error.py`:**
- 7 of 21 windows have |error| > 5 bpm: indices 1, 4, 6, 9, 12, 17 (all AHET-pass)
  plus window 10 (AHET-fail, f_r outlier). All large negative errors.
- Large errors (−9 to −15 bpm) are concentrated at true HR 71–74 bpm, not spread
  uniformly across the HR range.
- Worst-window harmonic-coincidence check:

  | Window | Error | f_r | 4×f_r | Masimo PR | \|4×f_r−PR\| | Suspicious? |
  |---|---|---|---|---|---|---|
  | 17 | −14.7 bpm | 18.7 bpm | 74.9 bpm | 73.3 bpm | 1.5 bpm | YES |
  | 6 | −12.5 bpm | 16.4 bpm | 65.5 bpm | 71.1 bpm | 5.6 bpm | borderline |
  | 4 | −12.2 bpm | 18.0 bpm | 71.9 bpm | 67.7 bpm | 4.2 bpm | YES |

- Root cause confirmed: bias is a harmonic contamination problem. ECA cannot
  suppress 4×f_r when it coincides with the cardiac frequency — projection removes
  both simultaneously. AHET passes these windows because second-harmonic structure
  is present, but it belongs to the respiratory chain, not the cardiac signal.
- 30 s windows do NOT fix harmonic-coincidence windows. Worst centers (400, 600,
  800, 1100, 1200, 1700, 1900) remain bad or worsen at 30 s (Plot D).
- Longer windows reduce MAE only on clean windows where spectral resolution helps.
  They are not the solution to the harmonic-coincidence failure mode.
- Four plots saved to `results/diagnostics/per_window_error/`:
  A (scatter), B (error over time), C (error vs true HR), D (20 s vs 30 s per center).

**Algorithm decision — Blocker 5 (harmonic exclusion at candidate selection):**
- Before accepting any HR candidate, check whether any k×f_r for k ∈ {2, 3, 4}
  falls within an exclusion tolerance of that candidate.
- If yes: skip to next candidate (not reject outright — try remaining candidates first).
- This is a targeted fix at the candidate-ranking step, not a change to ECA or AHET.
- Exclusion tolerance needs tuning. Cannot tune on exp002/cap1 alone — the harmonic-
  coincidence windows are the only failures and have no clean contrast class there.
- Decision: tune on development set (cap1 + cap3_retake + cap4), validate on held-out cap5.

**Re-capture plan — three supine captures decided, not yet executed:**

| Capture | BR target | 4×f_r ceiling | Role |
|---|---|---|---|
| cap3_retake | 13–16 bpm | ≤ 64 bpm | Development (replaces failed cap3) |
| cap4 | 16–18 bpm | ≤ 72 bpm | Development (second independent capture) |
| cap5 | 12–14 bpm | ≤ 56 bpm | Held-out (no tuning allowed on this capture) |

Protocol:
- Subject supine on floor mat, radar on tripod directly overhead, ~1.3 m radar-to-chest.
- Same mount position for all three captures.
- Breathing controlled with a metronome set to half the target BR rate (count each inhale).
- Recording duration: 450 s (7 min 30 s) per capture → ~39 windows after 30 s trim.
- Stabilisation before each capture: 2 min metronome breathing, then confirm Masimo BR
  stable in target range for 60 s before starting the radar recording.
- Rest between captures: 5 min.
- Pass criterion: Masimo BR stays in target range for ≥ 80% of the recording.

**Blocker status:**
- Blocker 1: resolved
- Blocker 2: resolved
- Blocker 3: deferred
- Blocker 4: resolved
- **Blocker 5: open** — harmonic exclusion at candidate selection (new)

**Next step:**
1. Execute three-capture supine session following the protocol above.
2. Orientation pass in Claude Code: verify files, frame counts, Masimo BR distributions.
3. Run exp004 pipeline on cap3_retake and cap4.
4. Implement and tune Blocker 5 harmonic exclusion on development set.
5. Validate on held-out cap5.

---

### 2026-06-15 — Blocker 4 resolved: parabolic interpolation on no-ECA fallback

**What was implemented:**
- `parabolic_interpolate_peak(spectrum, peak_idx, freq_resolution_hz) -> float` added
  to `src/vitals.py` (after `refine_freq_hz`). Pure function: parabola through the peak
  bin and its two neighbours, with three explicit fallbacks to raw bin centre:
  boundary bin (peak_idx==0 or last), flat top (denominator==0), large delta (|delta|>1).
- Wired into the no-ECA fallback path inside `estimate_rate_from_phase()` at the
  cardiac band argmax site (previously `peak_hz = float(band_freqs[peak_idx])`).
  New form: `peak_hz = band_freqs[0] + parabolic_interpolate_peak(band_spec, peak_idx, freq_res_hz)`.
  The ECA+AHET path already used `refine_freq_hz()` for parabolic refinement on all
  candidate frequencies; this change closes the gap on the no-ECA/f_r-outlier fallback.
- 7 new unit tests in `tests/test_vitals_synthetic.py`: symmetric peak (delta==0),
  off-centre peak (exact delta formula), left-edge fallback, right-edge fallback,
  flat-top fallback, large-delta fallback, and end-to-end no-ECA bias guard.
- Diagnostic script `scripts/diag_per_window_error.py` added (read-only, saves 4 plots
  to `results/diagnostics/per_window_error/`). Confirmed 7 high-error windows in exp002;
  windows 4 and 17 flagged as 4×f_r ≈ Masimo PR (respiratory harmonic coincidence).

**Test count:** 99/99 unit tests pass; 8 integration tests skipped by default.
Evidence: `pytest tests/ -v` (2026-06-15).

**exp002 metrics after Blocker 4 fix:**

| Subset | MAE | RMSE | Bias | N | vs canonical |
|---|---|---|---|---|---|
| All windows | 5.172 bpm | 6.897 bpm | −2.212 bpm | 21 | MAE −0.017, bias +0.016 |
| AHET-verified | 5.326 bpm | 7.051 bpm | −2.217 bpm | 20 | unchanged |

Evidence: `results/exp002_harmonic_rejection/20260615_110410/`.

The bias moved from −2.228 to −2.212 bpm (improvement of +0.016 bpm) — very small,
because the fix affects only window 10 (the one f_r-outlier window per capture where
the no-ECA path fires). The AHET-verified subset is unchanged. This is the correct
and expected behaviour; the ECA+AHET path already had refinement.

No fallback events triggered during the exp002 run other than the previously-known
window 10 f_r outlier (f_r = 8.8 bpm, below the 9 bpm physiological gate).

**exp004 cap1 metrics after Blocker 4 fix (post-interpolation):**

| Condition | MAE | RMSE | Bias | N | Pre-fix MAE |
|---|---|---|---|---|---|
| baseline 20s | 5.172 | 6.897 | −2.212 | 21/21 | 5.189 |
| condition 20s | 5.535 | 7.225 | −2.520 | 19/19 | 5.55 |
| condition 25s | 3.831 | 5.776 | −2.216 | 18/19 | 3.83 |
| condition 30s | 3.668 | 5.582 | −2.418 | 17/19 | 3.67 |

Monotonic MAE improvement with window length preserved. Changes from pre-fix are
<0.02 bpm on all conditions — confirms the fix is correctly scoped to the fallback path.

**cap2 and cap3 metrics are unchanged from 20260615_002408 to within 0.01 bpm.**

**Blocker status:**
- Blocker 1: resolved
- Blocker 2: resolved
- Blocker 3: deferred
- **Blocker 4: resolved** — parabolic interpolation wired into no-ECA fallback path.

**Next step:** re-capture cap3 with controlled breathing (13–16 bpm) before any
paper-grade window-length claim (unchanged from prior session).

---

### 2026-06-15 — exp004 multi-capture complete

**What was implemented:**
- Multi-capture runner (`experiments/exp004_window_length/run.py`) processing cap1,
  cap2, cap3 sequentially with explicit memory management. All three captures now write
  to `results_root/<cap_id>/` (symmetric directory structure).
- `analysis.py` with four pure functions: `pooled_window_length_summary`,
  `chair_condition_summary`, `masimo_summary`, `collect_provenance`.
- `tests/conftest.py` with `pytest_addoption` and `run_dir` fixture.
- 8 integration tests in `tests/test_exp004_analysis.py` (skipped by default,
  activated via `--run-dir <path>` or `EXP004_RUN_DIR`).
- **Total test count: 92 unit tests pass; 8 integration tests skipped by default.**
  With `--run-dir`: 100/100 pass.

**Window-length findings — per capture (condition grids, all finite windows):**
- cap1 (sit, 1.264 m, 2026-06-09):
  20s MAE 5.55, 25s MAE 3.83, 30s MAE 3.67 bpm — **monotonic improvement**
- cap2 (chair-back, 1.439 m, 2026-06-13):
  20s MAE 8.80, 25s MAE 8.51, 30s MAE 4.46 bpm — **monotonic improvement**
- cap3 (chair-no-back, 1.308 m, 2026-06-13):
  20s MAE 13.50, 25s MAE 14.14, 30s MAE 16.19 bpm — **no improvement**
  (physiological failure, not algorithmic — see cap3 diagnosis below)
- Pooled micro-average (intersection, n=63):
  20s MAE 9.02, 25s MAE 8.36, 30s MAE 7.46 bpm — monotonic improvement pooled,
  but masked by cap3 reversal in the macro view.

**Cap3 diagnosis:**
- Subject respiratory rate ≈ 19–20 bpm places 4th harmonic at ≈ 80 bpm, directly
  adjacent to true HR (78–84 bpm).
- ECA cannot suppress the 4th respiratory harmonic without suppressing the cardiac
  signal at the same frequency — projection removes both.
- AHET correctly abstains (NaN rate 46% at 20 s, 24% at 25 s) when no clean second
  harmonic survives — correct pipeline behaviour, not algorithm failure.
- When AHET passes, accepted candidate is often a respiratory harmonic — large
  negative bias (−12 to −14 bpm) across all window lengths.
- Longer windows do not resolve the ambiguity (SNR problem, not frequency resolution).
- Full diagnostic: `results/exp004_window_length/20260614_234744/cap3_diagnostic.md`.
- **Remediation:** re-capture with subject breathing at 13–16 bpm so
  4×f_r ≤ 64 bpm, well below HR ≈ 80 bpm.

**Chair-condition comparison (descriptive only):**
- cap2 MAE lower than cap3 at all window lengths, but the difference is dominated
  by the cap3 harmonic-coincidence failure, not chair condition.
- Confounded by distance (1.439 m vs 1.308 m), recording order, and respiratory rate.
  No causal inference supported.

**Evidence:**
- Results: `results/exp004_window_length/20260615_002408/`
- cap3 diagnostic: `results/exp004_window_length/20260614_234744/cap3_diagnostic.md`

**Blocker status:**
- Blocker 3: deferred (AHET correctly abstains on cap3 but cannot prevent passing a
  respiratory harmonic as cardiac when 4×f_r ≈ HR)
- Blocker 4: open (no-ECA fallback parabolic refinement)
- **Next step:** re-capture cap3 with controlled breathing (13–16 bpm) before any
  paper-grade window-length claim.

---

### 2026-06-14 — exp004 multi-capture extension plan v2 review

**Verdict:** Close, but not implementation-ready. W&B is excluded by project design
for this extension; review requirements below concern local provenance only.

**Confirmed corrected:** exact grids (cap1 19/21; cap2/cap3 37/39), two-session
metadata, descriptive non-causal chair comparison, micro plus macro summaries,
sequential capture processing, explicit-run integration checks, and local provenance.

**Required final corrections:**
1. Fix all capture paths to the repository's flat `data/raw/` layout. The proposed
   subdirectories do not exist, and cap3's logfile path incorrectly contains
   `exp002_sit_chair_back`. Use the exact existing filenames.
2. Restore required shared config fields: `seed`, UTC offset, and `compare.min_pi`.
   Add `subject_id`, `session_id`, posture, and locked-bin source. Rename `range_m`
   to `locked_bin_range_m` because it is spectral-bin range, not an independent
   physical distance measurement.
3. Set `radar.num_tx: 2`, matching the canonical configs and acquisition JSON
   (`txChannelEn: 0x3`). Although the current decoder's byte geometry does not use
   this field, recording incorrect hardware metadata is unacceptable.
4. Derive bytes per frame from radar config rather than hardcoding 131072. A mismatch
   with `expected_frames` should fail closed, not merely warn, unless the user passes
   an explicit override.
5. Define the compact `_run_capture()` return schema. `paired_metrics()` currently
   contains aggregate metrics but not the per-center errors required for micro
   pooling. Return, for each condition, the finite error vector on that capture's
   all-three-length intersection, plus compact coverage/metric summaries. Do not
   return cubes, spectra, window dictionaries, or full DataFrames.
6. Define pooling precisely:
   - micro metrics concatenate those intersection error vectors and report summed N;
   - macro metrics are the arithmetic mean and range of the three per-capture
     intersection metrics, labelled as mean per-capture MAE/RMSE/bias;
   - define “longer is better” explicitly (for example monotonic
     `MAE20 >= MAE25 >= MAE30`);
   - retain the predeclared exp004 outcome: change in absolute bias versus matched
     20 s with the MAE guardrail, reported per capture and descriptively pooled.
7. Define Masimo summaries over unique 1 Hz samples in the usable radar interval,
   not repeated overlapping window rows. State whether PR is PI-gated; report sample
   count, missing-second coverage, and both raw and PI-gated PR summaries if useful.
8. The chair-condition JSON must include the full denominators/rates promised in the
   reporting section: total, finite, NaN radar/reference, AHET, f_r outlier, harmonic
   suspect, plus Masimo summaries. Counts without denominators can be misleading.
9. Complete local provenance: hash logfile inputs and relevant source modules in
   addition to run/config/bin/Masimo. Because the current tree is dirty and contains
   untracked experiment code, either require a clean committed tree for the real run
   or save the full local diff and hashes/snapshots of untracked source files; a
   dirty boolean alone cannot reproduce the result.
10. Replace the existing “latest exp004 result” tests in `tests/test_exp004.py`;
    merely adding explicit-run tests leaves the suite non-hermetic. Define how
    `--run-dir` is supplied (pytest option or a separate verification script) and
    skip integration checks by default. Add tests for path existence/unique IDs,
    expected-frame mismatch, finite-intersection pooling, RMSE/bias as well as MAE,
    exact trend semantics, and coverage denominators.

**Recommended organization:** keep the runner orchestration in `run.py`, but place
pure pooled/chair/provenance builders in an experiment-local `analysis.py` so unit
tests can import them without coupling to the full runner.

---

### 2026-06-14 — exp004 multi-capture extension plan review

**Verdict:** Not implementation-ready. Extending the fixed exp004 pipeline to all
three captures is useful, but the proposed common-grid counts, capture metadata,
chair-effect interpretation, pooled statistics, config paths, tests, and provenance
need correction first. No implementation files changed during this review.

**Required corrections:**
1. Common-center counts must be equal across lengths. With 4800 total frames,
   600-frame trim, 600-frame maximum window, first center 900, and 100-frame
   spacing, cap2 and cap3 each have exactly **37** common centers for 20/25/30 s.
   Their separate 20 s sliding baselines have 39 windows. The proposed
   `~39/~39/~38` table contradicts common-center construction.
2. Correct capture metadata: cap1 was recorded on 2026-06-09; cap2 and cap3 were
   recorded sequentially on 2026-06-13. They are the same subject but not all the
   same session. Treat the dataset as three captures from two sessions.
3. Do not call cap2-vs-cap3 a chair-back “effect.” Chair condition is confounded
   with distance (1.439 m vs 1.308 m), recording order/time, HR, and respiration.
   The known no-back capture has elevated PR and respiratory-harmonic coincidence.
   Rename this a descriptive chair-condition/capture comparison and make no causal
   claim. Report Masimo PR/BR/PI distributions, finite-output coverage, NaN rate,
   f_r-outlier rate, harmonic-suspect rate, and AHET coverage alongside conditional
   MAE/RMSE/bias.
4. Pooled window rows are highly overlapping and unequal in number (cap2/cap3
   contribute roughly twice cap1), so simple concatenation is a length-weighted
   descriptive micro-average, not independent evidence. Report both:
   - micro-pooled metrics on each capture's all-condition finite intersection;
   - macro averages of per-capture metrics with each capture weighted equally.
   Keep per-capture results primary; do not calculate naive window-level p-values
   or confidence intervals.
5. Recommend one exp004 config with a `captures` list because algorithm parameters
   are shared and one runner produces one study. Use the repository's actual flat
   paths with `.csv` extensions. The paths shown in the plan point to nonexistent
   subdirectories and omit CSV suffixes. Do not treat configured `total_frames` as
   authoritative; infer it from file size and optionally compare against an
   `expected_frames` field.
6. Add capture metadata to config: session ID/date, chair condition, posture,
   nominal/measured range, locked-bin provenance, and capture order. Validate unique
   capture IDs and required files before processing.
7. Process captures sequentially in a scoped `_run_capture()` helper and release
   cube/profile arrays before loading the next 4800-frame capture. Do not retain all
   large radar arrays in memory. Return only compact per-capture tables/summaries.
8. Add pure summary builders for pooled and descriptive chair-condition outputs.
   Unit-test them using synthetic tables. Tests must assert exact cap2/cap3 counts
   (`37` common, `39` baseline), finite-intersection pooling, macro/micro weighting,
   and coverage denominators.
9. Do not add tests that discover the “most recent” real results directory.
   Those tests are non-hermetic and fail in clean checkouts. Use temporary synthetic
   artifacts for structure tests; keep real-run validation as an explicit integration
   check requiring a supplied `--run-dir`.
10. Add the provenance omitted from the plan and currently required by `CLAUDE.md`:
    git commit and dirty state, config/script hashes, raw input hashes, seed,
    Python/NumPy/SciPy versions, captured stdout, and W&B run ID(s).

**Confirmed implementation direction:**
- Existing `_process_windows`, `_run_and_save`, `src/windowing.py`,
  `src/compare.py`, and `src/intermediates.py` can support the extension without DSP
  changes.
- Use cap1 as the already-observed exploratory result and cap2/cap3 as additional
  descriptive condition checks. This remains a single-subject, two-session study.

---

### 2026-06-14 — exp004 implemented and run

**Scaffold:**
- `experiments/exp004_window_length/__init__.py` (empty) and `config.yaml` created.
  Config keys: `exp004.window_lengths_s: [20, 25, 30]`, `center_spacing_s: 5`,
  `first_center_s: 15`, `baseline_window_s: 20`, `baseline_hop_s: 5`.
  Same data/radar/processing params as exp002 (read-only `exp001` capture, locked_bin 29).

**run.py implementation:**
- Full phase extracted once from trimmed cube via `phase_at_bin` + `remove_impulse_noise`.
  All conditions slice this shared signal (no re-extraction per window). Guarantees
  baseline exactly reproduces exp002 single-pass output.
- `_process_windows` helper applies `estimate_rate_from_phase` (resp then HR with ECA+AHET)
  per (abs_start, abs_end) window; epoch computed as `t0 + s / fps` where `s` is
  trim-relative start.
- `_run_and_save` writes `comparison.csv` + `intermediates.npz` per condition subdirectory.
- Paired analysis: `conditions` dict keyed by center_frame (integer `(abs_start+abs_end)//2`).
  Called `paired_metrics(conditions, "20s")` and `coverage_table`; JSON written as
  `paired_summary.json`. NaN sentinel replaced with `null` via `_json_safe`.
- AHET caveat printed before paired output (±0.1 Hz region contains 5/5/7 FFT bins at
  20/25/30 s; pass-rate comparison is indicative only; not independently validated).

**Run result — `results/exp004_window_length/20260614_192057/`:**
- Baseline metrics (21 windows): MAE 5.1888, RMSE 6.9021, bias −2.2282 bpm ✓ canonical.
- AHET-verified baseline (20 windows): MAE 5.3257, RMSE 7.0513, bias −2.2171 bpm ✓ canonical.
- Common-center conditions: 20 s = 19 windows, 25 s = 19 windows, 30 s = 19 windows.
- Paired intersection: n_centers = 17 (30 s has 2 NaN windows at centers 900 and 1000;
  phase extraction truncated — physical short-window coverage at these centers needs
  investigation before paper-grade use of 30 s condition).
- All expected files present: `baseline_20s/`, `condition_{20,25,30}s/`,
  each with `comparison.csv` + `intermediates.npz`; `paired_summary.json` at run root.

**Tests — `tests/test_exp004.py` (12 tests):**
- Tests 1–4: window grid properties from config (no pipeline execution).
- Tests 5–7: `paired_metrics` correctness on synthetic data (NaN drops, 3-condition
  pairwise keys, transition counts sum to intersection size).
- Tests 8–12: artifact checks from most recent exp004 results directory.
  Test 9 verifies baseline MAE/RMSE/bias to within 1e-4 bpm against canonical.
- **75/75 tests pass.** Evidence: `pytest tests/ -v` (2026-06-14).

**Blocker status:**
- Blocker 1: resolved
- Blocker 2: resolved
- Blocker 3: deferred (documented in `notes/approach.md`)
- Blocker 4: open (no-ECA fallback parabolic refinement)

**Next:** Second capture session to assess generalisation across subjects/postures
before any paper-grade claim. Investigate 30 s NaN windows at centers 900/1000.

---

### 2026-06-14 — exp004 foundation complete — windowing and paired metrics

**Worked:**
- `src/windowing.py` created with two functions:
  - `common_center_windows`: generates 19-center common-time grid for 20/25/30 s
    windows (starts [700…2500] / [650…2450] / [600…2400], verified by
    `python src/windowing.py`).
  - `sliding_windows`: reproduces exp002 21-window baseline (starts [600…2600],
    hop 100 frames, verified by `python src/windowing.py`).
  - 16 tests in `tests/test_windowing.py` — all pass.
- `src/compare.py` extended with two functions:
  - `paired_metrics`: computes per-condition coverage counts and finite-only
    MAE/RMSE/bias, intersection across all-finite centers, and all pairwise
    error-diff / AHET-transition stats. Error always recomputed from
    `radar_hr - masimo_pr` (stored field not trusted).
  - `coverage_table`: formats `paired_metrics` output as a plain-text table
    for stdout (per-condition rows + Intersection section).
  - During test writing (Prompt 2b), a gap was found: the duplicate-key
    validation was specified in 2a but not implemented. Fixed by adding a
    `len(list(cdict.keys())) != len(set(...))` check; tested via `_DupKeyMap`
    subclass (Python dicts cannot expose duplicate keys directly).
  - 15 tests in `tests/test_paired_metrics.py` — all pass.
- **63/63 tests pass.** Evidence: `pytest tests/ -v` (2026-06-14).

**Not yet done:**
- exp004 runner (`experiments/exp004_window_length/run.py` + `config.yaml`)
  not yet implemented. Next session starts at Prompt 3a.

**Blocker status (unchanged):**
- Blocker 1: resolved
- Blocker 2: resolved
- Blocker 3: deferred (AHET criterion documented as known limitation; full
  evaluation after second capture)
- Blocker 4: open (no-ECA fallback parabolic refinement)

---

### 2026-06-14 — Common-center constructor plan review

**Verdict:** Correct numerical design, but revise before implementation to make the
frame-coordinate contract and validation behavior explicit.

**Confirmed:**
- With raw-capture support `[600,3000)`, centers `900..2700` in 100-frame steps
  produce 19 matched windows.
- Absolute starts are `700..2500` for 400 frames, `650..2450` for 500 frames, and
  `600..2400` for 600 frames. Corresponding trim-relative starts are `100..1900`,
  `50..1850`, and `0..1800`.
- The separate 400-frame/100-hop baseline over `[600,3000)` has 21 starts,
  `600..2600`.

**Required plan corrections:**
1. Correct the duration statement: the LogFile reports 152 s, but the binary has
   3000 frames at 20 Hz = 150 s. After 600-frame trim, usable radar support is
   2400 frames = 120 s, not 122 s. File-size-inferred frames are authoritative.
2. Define one coordinate convention. The proposed helper currently returns absolute
   raw-capture frames, while `run_pipeline_locked()` reports frames relative to the
   supplied cube and existing runners supply `profiles[trim_frames:]`. Either return
   trim-relative bounds to match that API, or keep absolute bounds and require an
   explicit conversion at the runner boundary. Never add trim twice.
3. Remove `frame_rate_hz` from this frame-only helper unless it has a concrete use.
   Seconds-to-frame conversion belongs in config/runner code with exact-integer
   validation.
4. Put the helper in `src/windowing.py` (or equivalently named focused module), not
   the DSP module. The statement that this replaces inline generation in
   `exp002/run.py` is inaccurate: regular generation currently occurs inside
   `run_pipeline_locked()`, and this prompt explicitly makes no runner change.
5. Do not silently shift an invalid requested first center. Validate that the
   predeclared first center supports every length, then generate through the last
   common valid center. Raise on an invalid first center or no valid grid.
6. Validate integer/non-boolean inputs, `0 <= trim < total`, positive spacing,
   positive unique lengths, and center parity. Since centers are integer frames,
   either require even window lengths or explicitly define half-frame centers.
7. Make the partial-window test concrete by asserting an exact expected tuple list.
   Add tests for invalid spacing/bounds and odd lengths if even-only semantics are
   chosen. Verification printing should be a separate command, not library output.

**Implementation scope:** This resolves only the common-grid foundation. Paired
metrics, exp004 runner integration, provenance, and artifact tests remain separate
pre-exp004 blockers.

---

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
