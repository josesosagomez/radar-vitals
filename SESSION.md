# Session Memory

> Update at the END of every working session. Newest entry on top.
> Three questions: What worked (with evidence)? What failed? What's next?

---

## Current state

- **Where we are:** exp002 closed. Final form: ECA+AHET + physiological outlier gate,
  30 s trim, bin 29 pinned. Reproducible baseline: MAE 5.29, RMSE 7.03, bias −2.27 bpm
  (AHET-verified windows). Tagged `exp002-baseline`. 12/12 tests pass.
- **Last verified result:** exp002 MAE 5.29, RMSE 7.03, bias −2.27 bpm (AHET-verified).
  Evidence: `results/exp002_harmonic_rejection/20260613_171020/`. Run: 2026-06-13.
  All-windows (incl gated): MAE 5.16, RMSE 6.88, bias −2.28.
- **Open question / blocker:** Respiratory-dominance failure mode documented (windows 9-12,
  ~60 bpm harmonic passes AHET; physics limit of single-bin extraction). Second capture
  required to assess generalisation before any paper-grade claim.

---

## Log (newest first)

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
