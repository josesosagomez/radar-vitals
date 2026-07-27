# Plan: Range-bin drift measurement (`scripts/diagnose_bin_drift.py`)

> **Implemented, run on all 4 real captures, and committed** (`scripts/diagnose_bin_drift.py`,
> `scripts/diagnose_bin_drift_config.yaml`, `tests/test_diagnose_bin_drift.py`). **Review
> reopened after implementation** (`plans/bin_drift_diagnostic_cross_review.md`, BDR-11…13):
> Codex re-reviewed after the user's Option A/Option A decisions were written into §8 and found
> a real gap in the shipped code — the decided centroid-drift grid was never operationalized
> (BDR-11, fixed below) — plus a stale plan table and a scope-text inconsistency (BDR-12/13,
> also fixed). Both prior escalations (**BDR-04 Option A, BDR-07 Option A**) remain decided —
> see **§8**.

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
| Chirp geometry | `scripts/live_demo_config.yaml` — loaded from config, **then validated per-capture against each capture's own recorded metadata** (§5) |
| Distance gate | `protocol.subject_distance_m` [0.8, 1.4] → bins 19–32 via `derive_candidate_bins` |
| Locked bin + warmup evidence | Generation-matched `warmup_bin_selection.json` (§8, BDR-07); schema-aware (§3.1) |
| Per-window DSP outcomes | Generation-matched `live_intermediates.npz`: `frame_idx`, `f_r_hz`, `accepted_candidate_rank`, `candidate_rejection_codes`, `resp_valid` |
| **Diagnostic's own parameters (new, BDR-05 R3)** | **`scripts/diagnose_bin_drift_config.yaml`** — a small, tracked, diagnostic-only config file (does not touch `live_demo_config.yaml`), per CLAUDE.md §2's one-config-per-experiment rule |

`scripts/diagnose_bin_drift_config.yaml` is a **prospective input**, not a record of what the
script happened to do — it is read at the start of every run and its own content is hashed into
`summary.json` (§5), never reconstructed from the run's own output. It fixes: the calibration
stratum (`[0, 599]` frames), block size (20 frames), the episode gap rule (`no_bridging`), the
10 offset phases (`[0..9]`), the trailing-block policy (`discard`, §7.1/BDR-08 R2), and the two
**independent** sensitivity-grid axes (§8/BDR-04, decided Option A; BDR-11 fixed their
operational definitions): duration `{2, 5, 10} s`, applied **per-window** to episode/outcome
association (§4), and centroid-drift `{0.3, 0.5, 1.0} bin`, applied to the **session-level**
robust centroid statistic only (§3.1/§4) — the two are reported separately, never as a
per-window Cartesian joint classifier, since no per-window centroid-displacement statistic is
defined.

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
`correlation_available=False`, `npz_path=None` for `live_test1`). The 30/1 figures describe the
*raw artifact on disk*, not anything this diagnostic consumes (BDR-12 — the table below
previously conflated the two):

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
  candidate bin, computed by `range_energy_by_bin` on that slice — the same computation
  `run_warmup_selection` performs internally. From it: `baseline_argmax_bin`,
  `baseline_rank_of_locked_bin`, `baseline_centroid`.
- **Warmup-recompute check, made schema-aware (BDR-07 R3):** the script's own recomputed
  calibration-stratum values are compared to the session's `warmup_bin_selection.json` **at
  whatever resolution that file actually provides.** Direct inspection confirms the schemas
  differ: the three 2026-07-26 replays' JSON has both full-buffer `energy`/`energy_rank` **and**
  `settled_energy_db` (`settle_skip_frames_applied` present); `live_test1`'s **original** JSON
  is an older schema with **only** full-buffer `energy`/`energy_rank` — no
  `settle_skip_frames_applied`, `settled_energy_db`, or eligibility fields at all. For a
  legacy-schema session, the check validates full-buffer values only and records
  `settled_warmup_json_validation: "not_available_legacy_schema"` in `summary.json` — **the
  diagnostic still computes its own settled baseline from frames 100–599 of the raw ADC bytes
  regardless** (that computation needs only the raw capture, never the JSON); what's unavailable
  is cross-validating that computation against a persisted settled figure for that session. This
  is reported as missing, never fabricated or silently skipped.
- **Physical drift** = **post-calibration** (frame > 599) block/window argmax or centroid moving
  away from the baseline, tracked over time.
- **Composite-lock-vs-baseline-energy agreement** is reported **once per session**, separately,
  as a labeled fact (e.g. "locked bin is baseline rank 6") — never merged into the drift time
  series.
- **Centroid formula:** weights are **power** (`|FFT|²`, matching `range_energy_by_bin`'s own
  units): `centroid = Σ(bin_index · power[bin]) / Σ(power[bin])` over the candidate gate bins.
- **Robust centroid-drift statistic:** median centroid over the **trailing 10 s of the session**
  vs. median centroid over the **first 10 s strictly after the calibration stratum** (frames
  600–799).

### 3.2 Two time scales, per session (post-calibration only)

1. **1 s blocks (20 frames)**, starting at frame 600. Per block × candidate bin: settled energy
   via `range_energy_by_bin` → **argmax bin** and **power-weighted centroid**, both relative to
   the baseline. The trailing incomplete block past the last full 20-frame boundary is
   **discarded** from this series (§7.1/BDR-08 R2), its frame count recorded but never weighted
   in as a partial block.
2. **600-frame windows aligned to the NPZ hop grid** (from `frame_idx`) — the same two stats,
   **plus**:
   - `post_calibration_observed_s` — the window's actual seconds of eligible (post-frame-600)
     drift evidence. For window index *i* (hop = 3 s), this is `min(30, i · 3)` — window 0 has
     0 s (it *is* the calibration stratum, §3.4); windows 1–9 have 3, 6, …, 27 s; window 10
     onward has the full 30 s.
   - off-baseline duration and longest excursion, computed only over each window's own
     `post_calibration_observed_s` span.
3. **Motion-energy variant** (window-scale only) — see §3.3.

**Unequal exposure (BDR-03 R3):** windows 1–9 cannot structurally reach the same off-baseline
duration as window 10 onward, regardless of physics — mixing them into one distribution would
build a deterministic time-at-risk gradient into the reported association. §4 keeps them out of
the primary report.

### 3.3 Motion-energy statistic (channel-preserving)

For each window and candidate bin `b`, form the per-**(frame, chirp, RX)** range-FFT amplitude
via the project's own `range_profile` (Hann-windowed FFT along fast time) — `X(t, c, r, b)` for
`t = 1..600` frames, `c` = chirp, `r` = RX. Per-channel, subtract that **channel's own** temporal
mean **before** any averaging across chirps/RX:

```
motion_energy(b) = mean_(t, c, r) | X(t, c, r, b) - mean_t' X(t', c, r, b) |²
```

**Why not average chirps/RX first:** doing so risks destructive cancellation when RX channels
have different static phases — a genuinely moving reflector could coherently cancel across
channels and read as near-zero motion energy. Subtracting per-channel means first (matching the
principle behind the project's own `delta_before_mean` phase-extraction path,
`src/respiration.py:extract_chest_phase`) avoids that blind spot.

This is **labeled conservatively as "slow-time-varying energy"**, not "breathing target" — it is
not validated as breathing-specific and will also respond to gross motion or oscillator phase
noise. It is **descriptive only** and does not feed any evidence summary. Persisted per
window/bin alongside the ordinary energy matrix (§4).

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

- `bin_energy_blocks.csv` — per-block per-bin energy matrix + argmax + centroid, both raw and
  baseline-relative, calibration stratum flagged, trailing-incomplete-block frame count recorded
  separately (not included as a row)
- `window_audit.csv` — one row per DSP window — `window_index`, `frame_start`, `frame_end`,
  `is_warmup_window`, `post_calibration_observed_s`, aggregated dominant-bin stats over the
  window's constituent 1 s blocks (argmax mode, mean centroid), off-baseline duration and
  longest excursion within the window, the raw `candidate_rejection_codes` row, `f_r_hz`, and
  the derived class (§4's classifier). This is the audit trail the aggregate report is computed
  from — nothing in the summary is derivable from `bin_energy_blocks.csv` alone.
- `motion_energy_windows.npz` — per-window, per-bin motion-energy matrix (§3.3)
- `summary.json` — run manifest (§5) plus:
  - baseline profile, calibration-stratum values, `settled_warmup_json_validation` status
    (§3.1), and the composite-lock-vs-baseline-energy fact
  - occupancy: fraction of post-calibration blocks with argmax at baseline / ±1 / ±2 / outside
  - **episodes** — a **half-open 1 s block** `[t, t+1)` is off-baseline iff its argmax ≠
    `baseline_argmax_bin`. An episode is a **maximal run of consecutive off-baseline blocks** —
    a single on-baseline block ends it (no gap-bridging). Duration = block count × 1 s. Each
    episode: `{start_s, end_s (exclusive), bin_sequence, modal_bin, max_displacement_bins}`. All
    episodes ≥ 1 block are listed with duration — episode *detection* needs no threshold; the
    sensitivity grid (§8) governs only how they're later summarized against outcome data.
  - baseline-centroid drift (§3.1): trailing-10s vs. first-post-calibration-10s medians
  - **window-outcome classifier** — mutually exclusive: `covered`, `gate_not_run`,
    `other_rejected` (as before).
  - **Primary report — full-exposure windows only (BDR-03 R3), computed and persisted, not just
    derivable from `window_audit.csv` (BDR-11 — this was the missing piece):**
    `outcome_stratified_report.full_exposure` — for each of the three mutually exclusive outcome
    classes (`covered`, `gate_not_run`, `other_rejected`), the count and mean off-baseline
    duration / longest excursion among windows with `post_calibration_observed_s == 30`
    (index ≥ 10). Windows 1–9 (partial exposure) get the identical grouping in
    `outcome_stratified_report.transitional`, over a **normalized off-baseline fraction**
    (`off_baseline_duration_s / post_calibration_observed_s`) rather than raw seconds, so they
    are never pooled with the 30 s-exposure windows. Window 0 remains excluded entirely (§3.4).
  - **The two sensitivity-grid axes are reported independently, never as a per-window Cartesian
    joint classifier (BDR-11, decided):** duration `{2,5,10}s` governs **per-window episode
    detection** (`episode_count_at_grid` — how many detected excursions reach each duration, a
    radar-only, outcome-blind count); centroid-drift `{0.3,0.5,1.0} bin` governs a **single
    session-level statistic** (`centroid_drift_at_grid` — whether the trailing-vs-leading robust
    centroid displacement meets each grid value). No per-window centroid-displacement statistic
    is defined, so the two axes are never crossed into one joint table — a Cartesian "did this
    window pass both the duration AND centroid grid point" classifier would have had to invent
    that per-window statistic post hoc, which is exactly what BDR-11 flagged.
  - **Independence-aware reporting, persisted per phase (BDR-11 — `offset_phase_subsets` was
    computed but never written to output until this fix):** full hop-resolution counts,
    explicitly labeled non-independent (90 % frame overlap between neighbouring windows);
    `offset_phase_report` holds **all 10 disjoint non-overlapping hop-offset phases**
    (`{k, k+10, k+20, …}` for `k = 0..9`), **each independently split into its own
    full-exposure/transitional `outcome_stratified_report`** — the exposure-time correction
    applies identically within every phase, not only to the pooled hop-resolution report.
  - **No pooled multi-session aggregate.** Each session's counts/proportions are reported
    separately; there is no merged cross-session statistic (n = 1 subject).
  - No use of the word "concentrated" without a stated number attached; the output is described
    as **temporal association**, not causal explanation, and is labeled an **evidence summary**
    (§ Context) — never a "verdict."
- `drift_overview.png` — energy heatmap (time × bin, dB rel. per-block max) with baseline line +
  argmax/centroid overlay + covered/gate_not_run/warmup-window strip, transitional-window span
  shaded distinctly from full-exposure windows
- **Console evidence summary** (renamed from "verdict") per session: baseline vs. lock fact, max
  excursion, % time off-baseline (full-exposure windows only, transitional reported separately),
  per-outcome-class duration distributions — framed as "candidate evidence for further design,"
  never as a tracker recommendation

## 5. Provenance

`summary.json`'s run manifest records **path + SHA-256** for every input actually used: the raw
`adc_stream.bin`, the `warmup_bin_selection.json` used, the `live_intermediates.npz` used (where
applicable), the `run_metadata.json` used, the diagnostic's own
`scripts/diagnose_bin_drift_config.yaml` (§1 — the **bound input**, not a post-hoc record), and
the current git commit.

**Fail-closed validation, before decoding:** each capture's own `run_metadata.json` contains a
full config snapshot at capture time: `num_adc_samples`, `num_rx`, `num_chirps_per_frame`,
`range_resolution_m`, `iq_swap` under `config.profile`; **`frame_rate_hz` under
`config.session.frame_rate_hz`** (verified — `config.profile` has no such key), cross-checked
against `config.session.frame_rate_hz == 1000 / config.hw_frame.period_ms` (verified:
`20.0 == 1000/50.0` for every capture). The diagnostic validates its own active decode geometry
against **the recorded snapshot** and raises on any mismatch.

**Fail-closed validation, before pairing:** before combining a replay's DSP outcomes with a raw
capture, the diagnostic checks that replay's own recorded `replay_file_hashes` entry against the
SHA-256 of the raw capture file being used, and raises on mismatch.

**Clean-tree requirement (BDR-05 R3):** the canonical evidence-generating run (the one whose
`summary.json` is cited anywhere) **requires a clean, committed working tree** — the script
checks and refuses to run otherwise. This is a direct lesson from BDR-07: `git_commit` +
`git_dirty: true` with no saved diff already proved insufficient to reproduce a prior run's code
generation once HEAD moved on (§8). A dirty-tree run is still permitted for iteration/debugging
but its `summary.json` is stamped `reproducible: false` and must not be cited as evidence.

The diagnostic introduces no randomness; no seed is required.

## 6. Sweep memory (measured, preliminary)

The original "process per-shard by slicing after one `read_adc_bin` call" plan was wrong:
`read_adc_bin` already eagerly allocates the full decoded `complex64` array, so slicing
afterward does not reduce peak memory at all. Building a genuine bounded/offset decoder would
duplicate trusted, correctness-critical logic outside `src/`, with real risk: a wrong offset or
swapped I/Q silently moves the apparent target to the wrong range bin. **Fix: drop the shard
fallback entirely** — all 4 captures decode via one ordinary `read_adc_bin` call.

**Measured, preliminary (BDR-06 R3):** a one-off scratch measurement (Windows
`GetProcessMemoryInfo`) of decoding the sweep capture's 1,259,732,992-byte `adc_stream.bin`
found peak working set **6.96 GB** and peak pagefile/commit **5.71 GB**, against a final
resident cube of 2.52 GB. This is **preliminary evidence, not yet reproduced by the committed
diagnostic itself** — the committed script's own end-to-end peak-memory logger (below) is what
binds the final number into a run's `summary.json`; until a real run has done that, ~7 GB is
stated as the working precondition but labeled preliminary.

**Corrected FFT-temporary scale (BDR-06 R3 — "a few MB" was wrong):** a single 600-frame
complex64 array at this geometry (600 × 32 chirps × 4 RX × 256 samples) is
`600·32·4·256·8 = 157,286,400` bytes ≈ **157 MB**, and `range_energy_by_bin` holds both a
windowed cube and its range-FFT result simultaneously, so per-window/block analysis temporaries
are on the order of a few hundred MB — **not negligible, but still roughly an order of magnitude
smaller than the ~7 GB decode peak**, so decode remains the observed bottleneck; the plan simply
should not have called the analysis-side cost "a few MB."

The diagnostic logs its own observed peak working set per session into `summary.json` (same
measurement technique), and raises a clear, actionable error before decoding if a preflight
check finds less than the stated bound available.

## 7. Files

| File | Action |
|---|---|
| `scripts/diagnose_bin_drift.py` | **new** — CLI: `--config scripts/live_demo_config.yaml --diagnostic-config scripts/diagnose_bin_drift_config.yaml --captures <4 dirs> --replays <matched-generation dirs, per §8> --out results/diagnose/bin_drift` |
| `scripts/diagnose_bin_drift_config.yaml` | **new** — the diagnostic's own bound parameters (§1, §5) |
| `tests/test_diagnose_bin_drift.py` | **new** — expanded per §7.1 |

Nothing else is touched. `data/raw/` not involved (empty); originals opened read-only.

### 7.1 Test plan

- Synthetic reflector stepped bin 25→27 mid-session (original case): argmax series shows the
  step at the right block; occupancy fractions exact.
- A run **below** each grid duration (2/5/10 s) must **not** be counted at that grid point; a
  run at **exactly** that duration must be (boundary/equality pinned) — tested at all three
  grid values (§8, decided Option A), not one threshold.
- A multi-bin off-baseline run must produce **one** episode record with the correct
  `bin_sequence`/`modal_bin`/`max_displacement_bins` — not be incorrectly split at the bin
  change.
- A constructed two-bin power spread with a known analytic centroid, asserted against the exact
  declared formula (§3.1).
- **Trailing-block policy, frozen (BDR-08 R2):** the incomplete final block past the last
  complete 20-frame boundary is **discarded** from block-based occupancy/episode calculations —
  never weighted in as a partial block — with its frame count recorded separately. Tested
  against each session's actual observed remainder (verified: massimo1=10, massimo2=11,
  sweep=11, live_test1=15 frames — real, non-hypothetical cases). NPZ-derived DSP windows are
  unaffected (they already stop at the last complete 600-frame boundary on their own).
- Rejection of malformed frame endpoints: below 599, beyond the complete-frame count,
  non-monotonic, or unexpected hop spacing.
- NPZ-alignment helper test (`frame_idx` → frame span, window 0 flagged `is_warmup_window`,
  `post_calibration_observed_s` computed correctly for windows 0/1/9/10).
- **Exposure-stratification test (BDR-03 R3), corrected during implementation:** a synthetic
  1 s excursion at the first post-calibration block confirms transitional-stratum windows (index
  1–9) report a **normalized fraction**, not raw seconds — e.g. window 1 (3 s exposure) reports
  fraction 1/3 for the same excursion window 9 (27 s exposure) reports as 1/27. **Not tested:**
  "excluded from the full-exposure report" — given the frozen 30 s window / 3 s hop grid, window
  10 (the first full-exposure window) spans *exactly* the first 30 post-calibration seconds by
  construction, so it necessarily also observes any excursion in that span and legitimately
  reports it as an ordinary duration. That is correct behavior, not a defect; the original
  BDR-03 R3 wording implied a stronger, geometrically unachievable guarantee. Confirms the same
  split is applied independently within each of the 10 offset-phase summaries.
- Warmup-recompute-check test, **schema-aware (BDR-07 R3):** one fixture with both JSON
  resolutions present (asserts both match); one fixture mimicking the legacy schema (only
  full-buffer `energy`/`energy_rank`, no settled fields) asserts
  `settled_warmup_json_validation == "not_available_legacy_schema"` and that the diagnostic
  still computes its own settled baseline from raw frames regardless.
- Motion-energy synthetic cases (§3.3): stationary complex reflector → near-zero motion energy;
  same-phase sinusoidal modulation at one bin → motion energy elevated with the expected
  Hann-leakage-tolerant profile; an opposite-static-phase two-RX synthetic demonstrating the
  channel-preserving formula still detects motion where a coherent-pre-average formula would
  cancel it; a gross constant-amplitude step also elevates motion energy (confirming the
  statistic is not breathing-specific).
- All-10-offset-phase test: a synthetic drift episode positioned so only some phases capture it
  fully, confirming each phase (and its exposure split) is computed and reported independently.
- **Clean-tree gate test:** the diagnostic refuses (or stamps `reproducible: false`) when run
  against a dirty tree, verified via a fixture that mocks `git status`.
- **Diagnostic-config binding test:** the run reads `scripts/diagnose_bin_drift_config.yaml` for
  every governing parameter (calibration stratum, block size, gap rule, offset phases, trailing
  policy) and hashes that exact file into `summary.json` — no governing constant is hardcoded
  outside it.
- **Centroid-grid sensitivity test (BDR-11):** below-, at-, and above-threshold displacement
  cases at all three grid values (`{0.3,0.5,1.0}` bin), plus direction-independence (a negative
  drift of the same magnitude gives the same grid result) and a non-finite-input case.
- **Outcome-stratification test (BDR-11):** a synthetic set of windows across all three outcome
  classes confirms per-class counts and mean durations group correctly, and that an empty class
  reports zero/`None` rather than crashing.
- **Replay-less leak-proof test (BDR-12), run through the real `run_session`/
  `load_session_inputs` path end to end, not a reimplementation of the guarantee:** a synthetic
  capture with no matched replay produces `n_windows == 0`, `correlation_available == False`,
  `npz_path is None`, every outcome class's count is 0 in both `outcome_stratified_report` strata
  and in every offset-phase entry, and `window_audit.csv` has a header row only.
- Packet-layout helper reused from `tests/test_radar_io_layout.py`.

## 8. Design decisions (both escalations resolved by the user, 2026-07-27)

### BDR-04 — Interpretation-gate thresholds: **Option A, decided**
The `≥ 5 s` episode duration and `> 0.5 bin` centroid-drift thresholds were asserted, not
derived. **Every mechanic independent of the threshold value itself is fixed** (§4): half-open
1 s blocks, no gap-bridging, multi-bin episode records, exclusive `end_s`, exposure-stratified
reporting.

**Decided: Option A.** The diagnostic is purely exploratory. It reports a **frozen sensitivity
grid** — duration thresholds `{2, 5, 10} s` × centroid-drift thresholds `{0.3, 0.5, 1.0} bin` —
with **no single number driving an automatic go/no-go verdict.** A human reads the full evidence
(occupancy, episodes at every grid point, the outcome-stratified association) and decides
whether relocking merits further design work. `scripts/diagnose_bin_drift_config.yaml` (§1/§5)
fixes this exact grid; the diagnostic never produces a single binary drift/no-drift
classification, at any threshold, now or later.

### BDR-07 — `live_test1`: no matched replay generation, and a legacy warmup-JSON schema: **Option A, decided**
`live_test1` was never re-replayed under the current config, so it has no artifact comparable in
generation to the other three sessions' `20260726_*` replays, and its **original** run's
`warmup_bin_selection.json` — the one Option A uses for its lock-vs-baseline fact — is a legacy
schema containing **only full-buffer `energy`/`energy_rank`**, with no `settled_energy_db` or
eligibility fields at all (verified directly). §3.1's warmup-recompute check is now schema-aware
and handles this without fabricating a match, so this is no longer a blocking implementation
gap — it is a scope fact for the user to be aware of: **live_test1's baseline is measured from
raw ADC bytes as normal, but cannot be cross-validated against a settled figure the legacy JSON
never recorded.**

**Checked and rejected:** simply re-running `scripts/live_demo.py --replay
.../live_test1/adc_stream.bin` now does **not** produce a matched generation — the three
existing `20260726_*` comparison replays were made at commit `5537df5182e1...` with an
**uncommitted working-tree diff** (`git_dirty: true`); current HEAD is `accfd53d105a...`, a
different commit, and the original dirty diff is not recoverable. Running now would produce a
**third**, still-unmatched generation.

**Decided: Option A.** `live_test1` is used **only** for the radar-energy/baseline-drift
measurement (§3, needs no DSP outcome, works regardless of estimator generation). Its own
**original run's** `warmup_bin_selection.json` is used for its lock-vs-baseline fact and its
(partial, legacy-schema) warmup-recompute check. Its outcome-correlation entries are reported
as `correlation_not_available` — never substituted from the `20260725_*` replay or its original
run, and never merged into any cross-session table. **No replay of `live_test1` is generated by
this work; Option B is not pursued.**

## 9. Verification

1. `conda run -n radar-vitals python -m pytest tests/test_diagnose_bin_drift.py -q` — all §7.1
   cases pass.
2. Full suite still green (script is additive; expect 1616+new passed, 1 skipped).
3. Run on all 4 captures **from a clean committed tree** (§5); confirm
   `results/diagnose/bin_drift/<run_id>/` artifacts exist (including `window_audit.csv` and
   `motion_energy_windows.npz`), the warmup-recompute check passes at every resolution each
   session's own JSON provides (schema-aware — `live_test1` expected to report
   `not_available_legacy_schema` for the settled resolution, not a failure), the heatmap
   renders, `summary.json` parses, and the sweep session's **logged, measured** peak working set
   is reported alongside the ~7 GB preliminary precondition (§6) — this run is what promotes
   that figure from preliminary to bound evidence.
4. Confirm `summary.json` records the diagnostic-config file's own hash and that every governing
   parameter traces to it, not to a hardcoded constant in the script.
5. Load the `dataviz` skill before writing the plotting code (chart-code trigger).
6. Session end: HISTORY.md append + HANDOFF.md rewrite (per CLAUDE.md §10) — including the
   evidence summary, its evidence paths, the measured sweep memory figure (now non-preliminary),
   and the resolution of §8's two escalations.

## 10. Explicitly out of scope

- Any tracker/relock implementation (that decision *follows* this measurement)
- Any Masimo comparison, any DSP or config change, any promotion of `guard_cardiac_candidate_v1`
- The "common properties of good bins" study (deferred: physics-first metrics, held-out
  validation on new subjects — as agreed)
- Building a second/bounded raw-ADC decoder (§6 — rejected on correctness-risk grounds)
- A pooled multi-session/multi-subject correlation statistic (§4 — rejected: n=1 subject)
- Any automatic tracker decision rule (§ Context/§4 — this diagnostic produces an evidence
  summary, not a verdict)
