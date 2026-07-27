# Plan: Range-bin drift measurement (`scripts/diagnose_bin_drift.py`)

> **Implemented, run on all 4 real captures, and committed.** Review has gone through 5 rounds
> (`plans/bin_drift_diagnostic_cross_review.md`, BDR-01…19), reopening twice after
> implementation as Codex found real gaps between the shipped code and what the plan claimed:
> round 4 (BDR-11…13) found the decided centroid-drift grid was never wired up, plus a stale
> table and a scope-text inconsistency; **round 5 (BDR-11 R2, BDR-14…19) found the window-scale
> energy measurement was approximated from 1 s blocks rather than computed directly, an exact
> "trailing 10 s" arithmetic bug, decode geometry validated against the wrong metadata file, an
> unnormalized transitional statistic, motion energy computed for the whole capture instead of
> per window, and several config values that were hashed but never actually governed behavior.**
> All fixed below. Both prior escalations (**BDR-04 Option A, BDR-07 Option A**) remain decided
> — see **§8**. No new escalations this round.

## Context

The subject cannot sit perfectly still for 3–10 minutes. Postural drift is centimetre-scale;
bins are 4.36 cm apart — so the chest can plausibly cross 1–2 range bins mid-session while the
pipeline stays parked on the warmup-locked bin. The user proposes a 5-bin tracker (locked ±2,
radar-only scoring, hysteresis, window-boundary switching). Before building that, this
diagnostic gathers evidence toward that question — **it does not itself decide it.** The
diagnostic measures: **does the in-gate radar energy profile move away from its settled warmup
baseline over a session, and is that movement temporally associated with radar DSP outcomes
(`gate_not_run` etc.)?** That is a narrower and more honest question than "does the reflector
leave the locked bin": the composite HR/BR-scored lock and the raw energy baseline are
demonstrably different quantities (massimo1's lock is bin 27; its baseline energy argmax is bin
23 — §3.1), so a finding here is **candidate evidence about whether relocking merits further
design work**, not proof that a locked±2 composite tracker would help. Nothing below produces an
automatic "verdict" in the sense of a decision — see §4's "evidence summary" language.

A relocking implementation existed before (Goals 1–3, `vital_signs_v9`, HISTORY.md 2026-07-02)
and was reverted 2026-07-09 as "not worth its complexity/risk **for now**" — a judgment made
without this measurement. `notes/relocking_bin_plan.md` was deleted with the revert; the v9 WIP
survives only in `stash@{0}`. This diagnostic is the evidence that decision lacked.

Specific hook: in the post-filter-fix massimo1 replay, 66 candidate slots across **22 windows**
(of 51) are `gate_not_run` — `f_r_hz` invalid, ECA+AHET never entered. Bin drift is a candidate
explanation. §3 defines drift relative to a declared energy baseline, never relative to the
composite-scored lock.

**Read-only diagnostic. No *existing/production* code, config, or `src/` changes** — one new
diagnostic-only config is in scope (`scripts/diagnose_bin_drift_config.yaml`, BDR-05 R3/BDR-13;
see §1's note on why it lives beside its script rather than under `experiments/<name>/config.yaml`).
**No Masimo data touched anywhere** — drift is a radar-side property; using the reference here
would be the CLAUDE.md §4 trap.

## 1. Inputs (all existing, read-only) + the diagnostic's own bound configuration

| Input | Source |
|---|---|
| Raw cubes | `results/live_demo/<capture>/adc_stream.bin` — all **4** captures |
| Chirp geometry | `scripts/live_demo_config.yaml` — loaded from config, **then validated against each capture's OWN recorded metadata** (§5, BDR-16) |
| Locked bin + warmup evidence | Generation-matched `warmup_bin_selection.json` (§8, BDR-07); schema-aware (§3.1) |
| Per-window DSP outcomes | Generation-matched `live_intermediates.npz`: `frame_idx`, `f_r_hz`, `accepted_candidate_rank`, `candidate_rejection_codes` |
| **Diagnostic's own parameters (BDR-05 R3)** | **`scripts/diagnose_bin_drift_config.yaml`** — a small, tracked, diagnostic-only config file (does not touch `live_demo_config.yaml`), per CLAUDE.md §2's one-config-per-experiment rule. Every value in it now actually governs behavior, or the run fails closed (BDR-19) — see §1.1. |

**Capture metadata vs. replay metadata are two distinct inputs, never conflated (BDR-16):** a
replay's raw-file hash proves which *bytes* were replayed; it does not prove the replay's own
recorded config snapshot is the geometry those bytes were originally captured with. The
diagnostic loads the **capture's own** `run_metadata.json` (always, from `capture_dir`) for
decode-geometry validation, and separately the **replay's** `run_metadata.json` (only when a
replay is matched) purely for provenance pairing (`replay_file_hashes`) and to source DSP
outcomes. Both are hashed into `summary.json` (§5) under distinct keys
(`capture_run_metadata_sha256`, `replay_run_metadata_sha256`).

`scripts/diagnose_bin_drift_config.yaml` is a **prospective input**, not a record of what the
script happened to do — it is read at the start of every run and its own content is hashed into
`summary.json` (§5), never reconstructed from the run's own output.

**Placement exception (BDR-13):** CLAUDE.md §2 names `experiments/<name>/config.yaml` as the
one-config-per-experiment convention, but this repo already has a precedent for a read-only
diagnostic tool's own config living beside its script rather than under `experiments/` —
`scripts/live_demo_config.yaml` next to `scripts/live_demo.py`. `scripts/diagnose_bin_drift_config.yaml`
follows that existing precedent rather than the `experiments/` convention, which is written for
DSP/algorithm parameter sweeps, not diagnostic tooling. Documented here rather than moved.

Verified session window counts (**do not use an approximate "n≈51/session" figure anywhere**).
**`live_test1` is deliberately `N/A`/0 here, not the 30-window/1-covered count its raw original
NPZ contains** — BDR-07 Option A means the diagnostic never loads that NPZ's outcome arrays at
all (verified in the implementation and in the real run: `n_windows=0`,
`correlation_available=False`, `npz_path=None`). The 30/1 figures describe the *raw artifact on
disk*, not anything this diagnostic consumes (BDR-12):

| session | replay used | n windows (consumed) | frame span | `covered` |
|---|---|---:|---|---:|
| massimo1 | `20260726_173434_replay_unknown` | 51 | [599, 3599] | 9 |
| massimo2 | `20260726_173653_replay_unknown` | 51 | [599, 3599] | 28 |
| sweep | `20260726_173914_replay_unknown` | 151 | [599, 9599] | 35 |
| live_test1 | none — its own original run supplies baseline evidence only (§8, BDR-07 Option A) | 0 (N/A) | N/A | N/A |

Verified: NPZ `frame_idx` is the window's **end frame** (599, 659, … ) → window *i* spans frames
`[frame_idx[i] - 599, frame_idx[i]]`. Verified: `settle_skip_frames_applied = 100` (5.0 s ×
20 Hz) for all three 2026-07-26 replays. Verified: raw-capture trailing remainders past the last
complete 20-frame block are massimo1=10, massimo2=11, sweep=11, live_test1=15 frames — all
non-zero, so §7.1's trailing-block policy is exercised on real data, not a hypothetical.

### 1.1 Every config value governs behavior, or the run fails closed (BDR-19)

Round 4 hashed the config file; round 5 found two of its values (`trailing_block_policy`,
`episodes.gap_rule`) were loaded and then never consulted — the discard/no-bridging behavior was
hardcoded, so mutating either value in the YAML would silently leave results unchanged despite
changing the input hash. Fixed: `compute_block_series` and `detect_episodes` now read their
respective config value and **raise `NotImplementedError`** for anything other than the one
currently-supported value (`discard`, `no_bridging`) — fail closed, never a silent fallback to a
default. `window_frames` (was `int(round(30.0 * fs))`) and the plot's time axis (was
`/ 20.0`) are now traced from `live_demo_config.yaml`'s own `session.window_s` /
`session.frame_rate_hz`, not hardcoded literals duplicating those config values.

A malformed NPZ hop grid is now rejected before alignment (`validate_frame_idx_grid`, BDR-19):
below the first valid window end, non-monotonic, or off the expected `hop_s × fs` spacing all
raise `ValueError` rather than silently producing a wrong alignment.

## 2. Reused components (nothing reimplemented)

- `src/radar_io.py`: `ChirpConfig`, `read_adc_bin` (handles the SDK 4-word I/Q layout, used
  **whole**, no partial/sharded decode — see §6, BDR-06), `range_axis_m`
- `src/warmup_select.py`: `derive_candidate_bins` (gate → bins), `range_energy_by_bin`
  (Hann + `sp_fft`, mean |·|² over frames/chirps/RX) — **the project's one definition of
  per-bin energy.** This reuse gives the diagnostic the same *energy calculation*
  `run_warmup_selection` uses internally — **not** the same *selection* semantics: the lock
  itself comes from a composite HR/BR score in which this energy is only a weak tie-breaker
  (§3.1). The two must not be conflated.

## 3. Computation

### 3.1 Baseline, calibration stratum, and drift definition

Energy-argmax and the composite-scored `selected_bin` answer **different questions** and must
not be conflated:

- **Calibration stratum = frames 0–599**, the session's entire first 600-frame buffer — the
  *whole* interval `run_warmup_selection` uses to choose the lock (§3.4). It is excluded
  **entirely** from episode/occupancy statistics, not only its settling sub-interval.
- **Baseline** = the settled (frames 100–599 of the calibration stratum) energy profile per
  candidate bin, computed by `range_energy_by_bin` on that slice. From it: `baseline_argmax_bin`,
  `baseline_rank_of_locked_bin`, `baseline_centroid`, and the **full per-bin profile**
  (`baseline_profile` in `summary.json` — every candidate bin's settled energy, not just the
  argmax/centroid derived from it).
- **Warmup-recompute check, schema-aware (BDR-07 R3):** the script's own recomputed
  calibration-stratum values are compared to the session's `warmup_bin_selection.json` **at
  whatever resolution that file actually provides.** A legacy-schema session (only `live_test1`)
  records `settled_warmup_json_validation: "not_available_legacy_schema"` — the diagnostic still
  computes its own settled baseline from raw frames regardless; only the cross-check is
  unavailable.
- **Physical drift** = **post-calibration** (frame > 599) block/window argmax or centroid moving
  away from the baseline, tracked over time.
- **Composite-lock-vs-baseline-energy agreement** is reported **once per session**, separately,
  as a labeled fact (e.g. "locked bin is baseline rank 6") — never merged into the drift time
  series.
- **Centroid formula:** weights are **power** (`|FFT|²`, matching `range_energy_by_bin`'s own
  units): `centroid = Σ(bin_index · power[bin]) / Σ(power[bin])` over the candidate gate bins.
- **Robust centroid-drift statistic, corrected (BDR-15):** median centroid over the **last N
  complete 1 s blocks** vs. the **first N complete post-calibration blocks**, where
  `N = round(10 s / block duration) = 10`, selected **by position in the block series**, not by
  a frame-count threshold. The round-4 implementation computed the trailing window as
  `cube.shape[0] - 10·fs` and selected blocks whose start frame was `>=` that value — but
  `cube.shape[0]` includes the session's non-block-aligned trailing remainder (10–15 frames on
  every real capture), so the threshold does not land on a block boundary. Verified on
  massimo1's real data: the buggy threshold selected only the **last 9** blocks (dropping
  `block_start_frame=3400`), giving centroid displacement 0.777 bin; selecting the true last 10
  blocks gives 0.815 bin. Selecting by position (`blocks.centroid[-10:]` /
  `blocks.centroid[:10]`) is exact regardless of the trailing remainder's size.

### 3.2 Two time scales, per session (post-calibration only)

1. **1 s blocks (20 frames)**, starting at frame 600. Per block × candidate bin: settled energy
   via `range_energy_by_bin`, keeping the **full per-bin energy matrix** (`BlockSeries.energy_matrix`,
   shape `(n_blocks, n_candidate_bins)`) alongside the derived **argmax bin** and
   **power-weighted centroid** (BDR-14 — round 4 discarded the full profile immediately after
   computing argmax/centroid from it; `bin_energy_blocks.csv` (§4) now carries it). The trailing
   incomplete block past the last full 20-frame boundary is **discarded** (config-governed,
   §1.1), its frame count recorded but never weighted in.
2. **600-frame windows aligned to the NPZ hop grid** (from `frame_idx`) — **argmax and centroid
   computed DIRECTLY on the window's own `[frame_start, frame_end]` slice via
   `range_energy_by_bin`** (BDR-14, corrected), not derived from the constituent 1 s blocks. The
   round-4 implementation took the *mode* of the blocks' argmax and the *mean* of their
   centroids as the window's own values — provably not equivalent to the window's true
   aggregate: a synthetic regression test (§7.1) constructs a 600-frame window where 16 of 30
   blocks (320 frames) have a low-amplitude tone at bin 8 and 14 blocks (280 frames) have a
   3×-amplitude (9× power) tone at bin 9 — the block-mode shortcut picks bin 8 (majority of
   blocks), the correct aggregate argmax is bin 9 (9× the energy). Off-baseline duration and
   longest excursion **still use the finer 1 s-block series** — a genuinely different, correctly
   -scoped sub-window statistic, unaffected by this fix — plus:
   - `post_calibration_observed_s` — the window's actual seconds of eligible (post-frame-600)
     drift evidence: `min(30, i · 3)` for window index *i* (hop = 3 s). Window 0 has 0 s (it *is*
     the calibration stratum, §3.4); windows 1–9 have 3, 6, …, 27 s; window 10 onward has 30 s.
3. **Motion-energy variant, per window** (§3.3).

**Unequal exposure (BDR-03 R3):** windows 1–9 cannot structurally reach the same off-baseline
duration as window 10 onward, regardless of physics. §4 keeps them out of the primary report,
using a normalized fraction instead (BDR-17).

### 3.3 Motion-energy statistic (channel-preserving, per window — BDR-18)

For each **NPZ-defined 600-frame window** (not the whole capture — round 4's implementation ran
one `np.fft.fft` over all 9,611 sweep frames at once, invalidating the memory reasoning in §6 and
collapsing all temporal resolution into one scalar per bin for the entire session, confirmed by
the real `motion_energy_windows.npz` being 674 bytes) and candidate bin `b`, form the
per-**(frame, chirp, RX)** range-FFT amplitude via the project's own `range_profile`
(Hann-windowed FFT along fast time) — `X(t, c, r, b)` for `t` = the window's own 600 frames,
`c` = chirp, `r` = RX. Per-channel, subtract that **channel's own** temporal mean **before** any
averaging across chirps/RX:

```
motion_energy(b) = mean_(t, c, r) | X(t, c, r, b) - mean_t' X(t', c, r, b) |²
```

**Why not average chirps/RX first:** doing so risks destructive cancellation when RX channels
have different static phases — a genuinely moving reflector could coherently cancel across
channels and read as near-zero motion energy. Subtracting per-channel means first (matching the
principle behind the project's own `delta_before_mean` phase-extraction path,
`src/respiration.py:extract_chest_phase`) avoids that blind spot.

**Why per-window, not per-capture:** bounded temporaries (one 600-frame slice processed at a
time, not the whole 9,611-frame sweep cube), a real `(n_windows, n_bins)` matrix instead of one
scalar per bin, and correct memory accounting (§6 — the whole-capture version's cost happened
*after* the point the old code sampled peak memory, so it was invisibly excluded from the
reported figure).

This is **labeled conservatively as "slow-time-varying energy"**, not "breathing target" — it is
not validated as breathing-specific and will also respond to gross motion or oscillator phase
noise. It is **descriptive only** and does not feed any evidence summary. Empty (zero windows)
for a session with no NPZ-defined windows (`live_test1`, BDR-07 Option A) — motion energy is a
window-scale statistic with no equivalent for a replay-less session, same as every other
window-level field.

### 3.4 Window 0 exclusion

`scripts/live_demo.py` fills its ring buffer to exactly 600 frames, then calls
`run_warmup_selection(first_window=that_same_buffer, ...)`; the winning candidate's DSP result
(`dsp_override`) becomes window 0's own recorded outcome (`scripts/live_demo.py:1122–1169`).
Window 0's frames are exactly the calibration stratum, and its outcome is produced by the same
computation that chose the lock. Including it in the drift-vs-outcome correlation would be
circular. It is retained in `window_audit.csv` (flagged `is_warmup_window: true`,
`post_calibration_observed_s: 0`) for transparency but excluded from every correlation
statistic.

## 4. Outputs → `results/diagnose/bin_drift/<run_id>/<session>/`

**Run-scoped path:** `<run_id>` is a timestamp assigned once per invocation of
`scripts/diagnose_bin_drift.py`. The script **fails rather than overwrites** if the target run
directory already exists; `summary.json` records its own `run_id`.

- `bin_energy_blocks.csv` — one row per 1 s block: `block_index`, `block_start_frame`,
  `argmax_bin`, `centroid`, then **for every candidate bin** `b`: `energy_bin_{b}` (raw power)
  and `energy_rel_baseline_db_bin_{b}` (`10·log10(energy / baseline_profile[b])`) — the full
  per-bin matrix the plan always promised, restored after BDR-14 found only the derived
  argmax/centroid had been persisted.
- `window_audit.csv` — one row per DSP window — `window_index`, `frame_start`, `frame_end`,
  `is_warmup_window`, `post_calibration_observed_s`, `window_argmax_bin`, `window_centroid`
  (**directly computed on the window's own slice, BDR-14** — renamed from
  `dominant_argmax_mode`/`mean_centroid` to make that explicit), off-baseline duration and
  longest excursion within the window (still block-derived — see §3.2), the raw
  `candidate_rejection_codes` row, `f_r_hz`, and the derived outcome class. This is the audit
  trail the aggregate report is computed from.
- `motion_energy_windows.npz` — real `(n_windows, n_bins)` matrix (`window_indices`, `bins`,
  `matrix`) — BDR-18, replacing the round-4 single-vector-per-capture file.
- `summary.json` — run manifest (§5) plus:
  - `baseline_profile` — the full per-bin settled-energy dict (BDR-14; previously only the
    derived argmax/centroid were kept), `baseline_argmax_bin`, `baseline_centroid`,
    `baseline_rank_of_locked_bin`, `settled_warmup_json_validation` status (§3.1), and the
    composite-lock-vs-baseline-energy fact
  - `occupancy` — fraction of post-calibration blocks with argmax at baseline / within 1 bin /
    within 2 bins / outside 2 bins (BDR-14; promised in earlier rounds, never actually computed
    until now)
  - **episodes** — a **half-open 1 s block** `[t, t+1)` is off-baseline iff its argmax ≠
    `baseline_argmax_bin`. An episode is a **maximal run of consecutive off-baseline blocks** —
    a single on-baseline block ends it (no gap-bridging, config-governed §1.1). Duration = block
    count × 1 s. Each episode: `{start_s, end_s (exclusive), bin_sequence, modal_bin,
    max_displacement_bins}`. All episodes ≥ 1 block are listed with duration — episode
    *detection* needs no threshold.
  - `centroid_drift` — trailing/leading 10 s medians (§3.1, corrected exact-block-count
    selection) — `centroid_drift_at_grid`: whether the displacement meets each of
    `{0.3, 0.5, 1.0}` bin. **Session-level only.**
  - **window-outcome classifier** — mutually exclusive: `covered`, `gate_not_run`,
    `other_rejected`.
  - **`outcome_stratified_report`** (full_exposure / transitional) — for each outcome class:
    count, mean off-baseline duration (raw seconds), mean longest excursion, **and
    `mean_off_baseline_fraction`** (`off_baseline_duration_s / post_calibration_observed_s`,
    always emitted by the report function itself, not left for a reader to compute — BDR-17:
    round 4's transitional report used the same raw-seconds statistic as full-exposure, silently
    reintroducing the exposure-time bias BDR-03 R3 existed to remove). For full-exposure windows
    the fraction is a trivial linear rescaling (equal denominators); for transitional windows it
    is the number that actually makes a 3 s window and a 27 s window comparable.
  - **`duration_grid_by_outcome`** (full-exposure windows) — the per-window rule the
    `sensitivity_grid.duration_s` axis actually governs (BDR-11 R2, corrected): for each outcome
    class, the count of windows whose own `longest_excursion_s` meets or exceeds each grid
    duration. `episode_count_at_grid` remains the separate, deliberately outcome-blind,
    session-level episode count — the two axes (per-window-by-outcome vs. session-level
    radar-only) are never crossed into one joint table, and neither is crossed with
    `centroid_drift_at_grid` (session-level, the other axis) — no per-window
    centroid-displacement statistic is defined, so a three-way Cartesian join would invent one
    post hoc.
  - **`offset_phase_report`** — **all 10 disjoint non-overlapping hop-offset phases**
    (`{k, k+10, k+20, …}` for `k = 0..9`), **each independently split into full-exposure /
    transitional, each with its own `outcome_stratified_report` AND `duration_grid_by_outcome`**
    — the exposure-time correction and the duration-grid association both apply identically
    within every phase, not only to the pooled hop-resolution report.
  - **No pooled multi-session aggregate.** Each session's counts/proportions are reported
    separately (n = 1 subject).
  - No use of the word "concentrated" without a stated number attached; the output is described
    as **temporal association**, not causal explanation, and is labeled an **evidence summary**
    — never a "verdict."
- `drift_overview.png` — **a real heatmap** (time × bin, dB rel. per-block max, `viridis`,
  colorbar) with baseline line + argmax/centroid overlay, plus a window-outcome strip
  (covered/gate_not_run/other_rejected/warmup, fixed categorical colors + distinct marker
  shapes for colorblind safety) below it — BDR-14, replacing round 4's two-line plot with no
  heatmap at all.
- **Console evidence summary** per session: baseline vs. lock fact, episode/centroid grid
  results, `correlation_available` — framed as "candidate evidence for further design," never as
  a tracker recommendation.

## 5. Provenance

`summary.json`'s run manifest records **path + SHA-256** for every input actually used: the raw
`adc_stream.bin`, the `warmup_bin_selection.json` used, the `live_intermediates.npz` used (where
applicable), **the capture's own `run_metadata.json` AND, separately, the replay's own
`run_metadata.json` when one is matched** (`capture_run_metadata_sha256` /
`replay_run_metadata_sha256` — BDR-16, previously a single conflated field), the diagnostic's own
`scripts/diagnose_bin_drift_config.yaml`, and the current git commit.

**Fail-closed validation, before decoding (BDR-16, corrected):** decode-geometry validation uses
**only the capture's own recorded metadata**, never the replay's — a replay's raw-file hash
proves which bytes were replayed, not that its config snapshot matches the geometry those bytes
were captured with. `num_adc_samples`, `num_rx`, `num_chirps_per_frame`, `range_resolution_m`,
`iq_swap` under `config.profile`; `frame_rate_hz` under `config.session.frame_rate_hz`
(cross-checked against `1000 / config.hw_frame.period_ms`) — all from the **capture's**
`run_metadata.json`. A regression test constructs a replay whose own metadata deliberately shows
a wrong `num_rx` and confirms the run still succeeds (because it never consults that field).

**Fail-closed validation, before pairing:** before combining a replay's DSP outcomes with a raw
capture, the diagnostic checks that replay's own recorded `replay_file_hashes` entry against the
SHA-256 of the raw capture file being used, and raises on mismatch.

**Clean-tree requirement:** the canonical evidence-generating run (the one whose `summary.json`
is cited anywhere) **requires a clean, committed working tree** — the script checks and refuses
to run otherwise. A dirty-tree run is still permitted for iteration but is stamped
`reproducible: false` and must not be cited as evidence.

The diagnostic introduces no randomness; no seed is required.

## 6. Sweep memory (measured, corrected placement — BDR-18)

`read_adc_bin` already eagerly allocates the full decoded `complex64` array, so a sharded decode
would not reduce peak memory; the plan drops that approach entirely (§ was BDR-06, unchanged).

**Measurement placement corrected (BDR-18):** round 4's `mem_peak_working_set_after_decode` was
sampled immediately after `read_adc_bin`, **before** the (then whole-capture) motion-energy FFT
— so the 7.05 GB figure verified only the decode checkpoint, not the true end-to-end per-session
peak the plan implied it was. The diagnostic now also samples
`mem_peak_working_set_after_session` at the very end of `run_session` (after block/window
computation and the now-bounded per-window motion energy, before the cube is freed), and reports
both. `PeakWorkingSetSize` remains a whole-**process** high-water mark, not cleanly isolated
per-session when multiple sessions run in one process (HANDOFF.md's own documented caveat) — the
two samples bound what happened *during* each session without claiming false isolation.

**Preflight now enforced, not just loaded (BDR-18):** `memory.preflight_min_available_gb` was
hashed into the config and never checked. `preflight_check_memory` now measures available
physical memory (`GlobalMemoryStatusEx` via `ctypes`, the same best-effort/Windows-only pattern
as the working-set logger) before each session's decode and **raises `MemoryError`** if below
the configured bound — never silently proceeds on an unmeasurable or insufficient machine when a
measurement is available.

## 7. Files

| File | Action |
|---|---|
| `scripts/diagnose_bin_drift.py` | CLI: `--config scripts/live_demo_config.yaml --diagnostic-config scripts/diagnose_bin_drift_config.yaml --captures <4 dirs> --replays <matched-generation dirs, per §8> --out results/diagnose/bin_drift` |
| `scripts/diagnose_bin_drift_config.yaml` | The diagnostic's own bound parameters (§1.1, §5) |
| `tests/test_diagnose_bin_drift.py` | 65 tests (§7.1) |

Nothing else is touched. `data/raw/` not involved (empty); originals opened read-only.

### 7.1 Test plan (expanded across rounds 4–5)

- Synthetic reflector stepped bin 25→27 mid-session: argmax series shows the step at the right
  block; occupancy fractions exact (`test_compute_occupancy_fractions`).
- **Window-scale energy is computed directly, not via block aggregation (BDR-14):** a 600-frame
  window with 16/30 blocks at low-amplitude bin 8 and 14/30 blocks at 3×-amplitude bin 9 — block
  mode says 8, true aggregate power says 9; the window's own `window_argmax_bin` must be 9.
- **Trailing-10s selection is exact (BDR-15):** reproduces the real massimo1 arithmetic (3610
  frames, 10-frame trailing remainder, 150 blocks) and confirms the corrected function selects
  the true last 10 blocks (median of positions 140–149), not the 9 a frame-count threshold
  would select.
- **Config values actually govern behavior (BDR-19):** `compute_block_series` and
  `detect_episodes` raise `NotImplementedError` for any `trailing_block_policy`/`gap_rule` value
  other than the one supported one.
- **`validate_frame_idx_grid` rejects malformed grids (BDR-19):** below the first valid window
  end, non-monotonic, and wrong-hop-spacing cases each raise; a well-formed grid and an empty
  grid do not.
- **Decode geometry uses only capture metadata (BDR-16):** an integration test builds a replay
  whose own `run_metadata.json` shows a deliberately wrong `num_rx`; `run_session` must still
  succeed, because geometry validation never reads the replay's metadata.
- **Transitional fraction is emitted by the report function itself (BDR-17):** two windows with
  identical raw off-baseline seconds but different `post_calibration_observed_s` must produce
  different `mean_off_baseline_fraction` values from `stratify_by_outcome` directly — not a
  fraction a test computes by hand from two raw fields.
- **`duration_grid_by_outcome` counts per class at grid boundaries (BDR-11 R2):** below/at/above
  each grid duration, per outcome class, plus an empty-input case.
- **Motion energy is per-window and bounded (BDR-18):** `compute_motion_energy_per_window`
  returns a matrix keyed by window index with one row per NPZ window (not one scalar for the
  whole capture), and is empty for a session with no NPZ-defined windows.
- **Preflight actually raises (BDR-18):** an absurdly high `preflight_min_available_gb` bound
  causes `MemoryError`; a near-zero bound passes.
- A multi-bin off-baseline run produces **one** episode record with the correct
  `bin_sequence`/`modal_bin`/`max_displacement_bins`.
- Trailing-block discard, tested against each session's actual observed remainder
  (massimo1=10, massimo2=11, sweep=11, live_test1=15 frames).
- `post_calibration_observed_s` correct for windows 0/1/9/10; exposure stratification splits
  full-exposure/transitional correctly (index ≥10 vs. 1–9); window 0 always excluded.
- Warmup-recompute-check, schema-aware: both-resolutions-present and legacy-schema
  (`not_available_legacy_schema`) fixtures.
- Motion-energy physics (stationary near-zero, phase-modulated elevation, RX-cancellation
  resistance, gross-motion non-specificity) — same synthetic cases as before, now exercising the
  per-slice primitive `_motion_energy_slice` directly.
- All-10-offset-phase partition test.
- Clean-tree gate: fresh commit clean, modified tracked file dirty, untracked new file still
  clean.
- **Replay-less leak-proof test (BDR-12), run through the real `run_session`/
  `load_session_inputs` path end to end:** zero windows, zero outcome-class counts in every
  stratum and every offset phase, header-only `window_audit.csv`.
- Packet-layout helper reused from `tests/test_radar_io_layout.py`.

## 8. Design decisions (both escalations resolved by the user, 2026-07-27)

### BDR-04 — Interpretation-gate thresholds: **Option A, decided**
The `≥ 5 s` episode duration and `> 0.5 bin` centroid-drift thresholds were asserted, not
derived. **Decided: Option A.** The diagnostic is purely exploratory. It reports a **frozen
sensitivity grid** — duration thresholds `{2, 5, 10} s` (governing `episode_count_at_grid`,
session-level, and `duration_grid_by_outcome`, per-window-by-outcome — BDR-11 R2) × centroid-drift
thresholds `{0.3, 0.5, 1.0} bin` (governing `centroid_drift_at_grid`, session-level only) — with
**no single number driving an automatic go/no-go verdict**, and the two axes never crossed into
a per-window joint classifier (BDR-11/BDR-11 R2).

### BDR-07 — `live_test1`: no matched replay generation, and a legacy warmup-JSON schema: **Option A, decided**
`live_test1` was never re-replayed under the current config; its **original** run's
`warmup_bin_selection.json` is a legacy schema (only full-buffer `energy`/`energy_rank`, no
`settled_energy_db`). §3.1's warmup-recompute check is schema-aware and handles this without
fabricating a match. Re-running `scripts/live_demo.py` now would **not** reproduce the
`5537df5`+dirty generation the other three replays were made at (HEAD has moved on).

**Decided: Option A.** `live_test1` is used **only** for the radar-energy/baseline-drift
measurement — needs no DSP outcome, works regardless of estimator generation. Its outcome
entries are `correlation_not_available`; no replay is generated for it.

## 9. Verification

1. `conda run -n radar-vitals python -m pytest tests/test_diagnose_bin_drift.py -q` — all 65
   cases pass.
2. Full suite still green (script is additive; expect 1616 baseline + 65).
3. Run on all 4 captures **from a clean committed tree**; confirm all output files exist
   (including the widened `bin_energy_blocks.csv`, the real `motion_energy_windows.npz` matrix,
   and the heatmap `drift_overview.png`), the warmup-recompute check passes at every resolution
   each session's own JSON provides, `summary.json` parses and includes `baseline_profile`,
   `occupancy`, `duration_grid_by_outcome`, and both `mem_peak_working_set_after_decode` /
   `mem_peak_working_set_after_session`.
4. Confirm `summary.json` records both `capture_run_metadata_sha256` and
   `replay_run_metadata_sha256` (the latter `None` for `live_test1`), and that
   `trailing_block_policy`/`gap_rule` values other than the supported ones raise rather than
   silently no-op.
5. Load the `dataviz` skill before writing the plotting code (chart-code trigger) — done; the
   heatmap uses a perceptually uniform sequential colormap and the outcome strip uses fixed
   categorical colors + distinct marker shapes.
6. Session end: HISTORY.md append + HANDOFF.md rewrite (per CLAUDE.md §10) — including the
   evidence summary, its evidence paths, and this round's fixes.

## 10. Explicitly out of scope

- Any tracker/relock implementation (that decision *follows* this measurement)
- Any Masimo comparison, any DSP or config change, any promotion of `guard_cardiac_candidate_v1`
- The "common properties of good bins" study (deferred: physics-first metrics, held-out
  validation on new subjects — as agreed)
- Building a second/bounded raw-ADC decoder (§6 — rejected on correctness-risk grounds)
- A pooled multi-session/multi-subject correlation statistic (§4 — rejected: n=1 subject)
- Any automatic tracker decision rule (§ Context/§4 — this diagnostic produces an evidence
  summary, not a verdict)
