# Plan: Range-bin drift measurement (`scripts/diagnose_bin_drift.py`)

> **Implemented, run on all 4 real captures, and committed.** Review has gone through 10 rounds
> (`plans/bin_drift_diagnostic_cross_review.md`, BDR-01…25 plus R2/R3 reopenings),
> reopening seven times after implementation as Codex found real gaps between the shipped code
> and what the plan claimed:
> round 4 (BDR-11…13) found the decided centroid-drift grid was never wired up, plus a stale
> table and a scope-text inconsistency; round 5 (BDR-11 R2, BDR-14…19) found the window-scale
> energy measurement was approximated from 1 s blocks rather than computed directly, an exact
> "trailing 10 s" arithmetic bug, decode geometry validated against the wrong metadata file, an
> unnormalized transitional statistic, motion energy computed for the whole capture instead of
> per window, and several config values that were hashed but never actually governed behavior;
> round 6 (BDR-14 R2, BDR-19 R2, BDR-20…23) found the round-5 window-scale energy fix computed
> the profile but still discarded it before persisting, the NPZ frame-grid validator still
> accepted a misanchored/truncated grid and never checked outcome-array shapes, replay-to-capture
> matching bound only raw bytes (not the estimator generation that produced the recorded
> outcomes), `baseline_rank_of_locked_bin` was sourced from the full-buffer warmup-JSON rank
> instead of the diagnostic's own settled baseline, per-session summaries lacked their own
> run_id/git-commit/config-hash provenance, and the centroid-drift support span was a hardcoded
> 10.0 literal outside the bound config; **round 7 (BDR-02 R2, BDR-19 R3, BDR-22 R2, BDR-23 R2)
> found the per-window outcome classifier accepted contradictory or out-of-domain evidence
> without error (an accepted rank paired with a non-finite `f_r_hz`, all-not-run rejection codes,
> or a rank outside the generation's 3-slot domain) and dropped its own input
> (`accepted_candidate_rank`) instead of persisting it in `window_audit.csv`; the round-6
> shape/row-count check for the NPZ outcome arrays still passed a wrong-shaped (e.g. `(n,1)`/
> `(n,2)`) array; the raw ADC path was still absent from every session summary (only its hash
> was recorded); and the config-bound centroid support (round 6) had no
> positive-block validation and still serialized hardcoded "10s" field names regardless of the
> configured span; **round 8 (BDR-02 R3, BDR-23 R3, BDR-24) found the round-7 `gate_not_run`
> criterion was itself wrong on real data** — it required a non-finite `f_r_hz` in addition to
> all-not-run rejection codes, but the producer's no-ECA early return
> (`src/vitals.py:523`) that produces all-not-run codes fires for `f_r_hz=None` **or** a finite
> value outside the physiological gate `[0.15, 0.60]` Hz, so round 7 mislabeled 6 real massimo1
> windows and 1 real sweep window as `other_rejected` instead of `gate_not_run` (verified
> directly against the approved replay NPZs: massimo1 has 22 true gate-skipped windows, not 16;
> sweep has 15, not 14); **the round-7 `n_blocks_used` field recorded the requested/configured
> centroid-support block count, not the block count actually used**, wrong whenever a session had
> fewer post-calibration blocks than the configured span; **and exact-shape validation
> (round 6/7) still permitted lossy numeric coercion** — a fractional `frame_idx`/
> `accepted_candidate_rank`/`candidate_rejection_codes` array passed shape validation and was
> then silently truncated by `int(...)`/`.astype(np.int64)` downstream instead of failing
> closed; **round 9 (BDR-25) found the round-8 classifier fix still validated only PART of the
> approved strict_v1 row contract**: a returned row must have ALL rejection codes `-1` (the
> no-ECA branch) or NO `-1` anywhere (`src/vitals.py:940` overwrites every never-attempted slot's
> `-1` with code 5 once the gate executes), and a nonnegative accepted rank must equal the FIRST
> code-`0` slot (`src/vitals.py:941-943`) — the diagnostic accepted a mix of `-1` and concrete
> codes, an accepted rank that was not the first passed slot, and an all-`-1` row paired with an
> in-gate finite `f_r_hz` (impossible, since ECA/AHET always executes and codes every slot in that
> case). Direct inspection confirmed the three approved NPZs violate none of these, so the real
> evidence counts are unchanged by this round; **round 10 (BDR-25 R2) found the round-9 fix was
> still only HALF the contract** — round 9 validated that the no-gate state (all `-1`) requires
> `f_r_hz` to have failed the gate, but never validated the converse: an executed-gate row (no
> `-1` anywhere) equally requires a finite, in-gate `f_r_hz`, regardless of whether any candidate
> passed, so `rank=-1, codes=[2,3,5], f_r_hz=NaN` still silently returned `"other_rejected"`.
> Separately, candidates are attempted in strict order 0..N-1 (`src/vitals.py:815`), so the
> never-attempted complement (code 5) can only ever be a TRAILING SUFFIX — `[5,2,5]` is
> impossible but was accepted. Direct inspection again confirmed the three approved NPZs violate
> neither invariant, so the real evidence counts are unchanged by this round too. The classifier
> is now expressed as two exhaustive producer states (no-gate / executed-gate) rather than an
> ad hoc check list, specifically so the converse of a check cannot be missed again the way
> rounds 8, 9, and 10 each missed one direction of the SAME function. All fixed below. Both prior
> escalations (**BDR-04 Option A, BDR-07 Option A**) remain decided — see **§8**. No new
> escalations this round.

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

**Raw-hash equality does not bind the estimator generation (BDR-20):** a replay's raw-file hash
proves only that it replayed the same *bytes* — it says nothing about which code/config
*generation* produced the recorded DSP outcomes. Direct inspection found at least six replay
directories sharing massimo1's raw hash across generations from 2026-07-15 through 2026-07-27,
including the explicitly non-interchangeable 2026-07-25/2026-07-26 pair. `scripts/diagnose_bin_drift_config.yaml`'s
`approved_replays` section maps each capture's raw `adc_stream.bin` SHA-256 to the SHA-256 of
that capture's ONE approved replay `run_metadata.json`; `match_replays_to_captures` enforces it
before any replay's outcomes are used — a capture with no approval entry rejects any replay
offered for it, a replay whose own `run_metadata.json` hash does not match the approved value is
rejected even though its raw bytes match, and two different replay directories matching the same
capture's raw hash are rejected outright (never silently keeping whichever came last on the CLI).

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

### 1.1 Every config value governs behavior, or the run fails closed (BDR-19, BDR-19 R2, BDR-19 R3, BDR-23, BDR-23 R2, BDR-23 R3, BDR-24)

Round 4 hashed the config file; round 5 found two of its values (`trailing_block_policy`,
`episodes.gap_rule`) were loaded and then never consulted — the discard/no-bridging behavior was
hardcoded, so mutating either value in the YAML would silently leave results unchanged despite
changing the input hash. Fixed: `compute_block_series` and `detect_episodes` now read their
respective config value and **raise `NotImplementedError`** for anything other than the one
currently-supported value (`discard`, `no_bridging`) — fail closed, never a silent fallback to a
default. `window_frames` (was `int(round(30.0 * fs))`) and the plot's time axis (was
`/ 20.0`) are now traced from `live_demo_config.yaml`'s own `session.window_s` /
`session.frame_rate_hz`, not hardcoded literals duplicating those config values.

**Round 6 found one more:** the robust centroid-drift statistic's support (`10.0` seconds) was
also a hardcoded literal, not traced to any config value (BDR-23) — mutating it would have no
effect on the run's behavior despite the sensitivity grid's centroid axis depending on it.
`scripts/diagnose_bin_drift_config.yaml` now has `centroid.summary_span_s: 10.0`, and
`trailing_leading_centroid_medians` reads `cfg.centroid_summary_span_s` instead. A mutation test
confirms changing this field changes the selected leading/trailing block count.

**Round 7 (BDR-23 R2) found the config binding itself was unvalidated and its output mislabeled:**
`load_diagnostic_config` accepted any `summary_span_s`, including `0` or negative — with
`summary_span_s: 0`, `round(...)` yields `n_window_blocks=0`, and `blocks.centroid[-0:]` selects
the **whole** series while `blocks.centroid[:0]` is empty/NaN, so the result was neither a
rejected config nor a genuine zero-span statistic. Fixed: `load_diagnostic_config` rejects a
non-finite or non-positive `summary_span_s` at load time; `trailing_leading_centroid_medians`
additionally rejects (raises `ValueError`) any span that rounds to fewer than one complete block
at the session's actual `fs`/`block_frames` (a config-level check alone cannot catch this, since
it depends on `fs`, which lives in `live_demo_config.yaml`, not the diagnostic config). Separately,
`summary.json`'s `centroid_drift` object hardcoded the field names `trailing_10s_median`/
`first_post_calibration_10s_median` regardless of the configured span — a run configured for 5 s
would still label its statistics "10s". Fixed: the keys are now neutral (`trailing_median`,
`leading_median`) plus two new fields recording what was actually used:
`summary_span_s` (the configured value) and `n_blocks_used` (the resolved block count) —
`trailing_leading_centroid_medians` now returns a third value precisely so callers can serialize
it truthfully. The BDR-23 mutation test now asserts through the return value's block count, not
just the numeric medians.

**Round 8 (BDR-23 R3) found that third return value itself was wrong:** it was
`n_window_blocks`, the REQUESTED/configured block count, not the block count actually applied.
`n_trailing`/`n_leading` (`min(n_window_blocks, n_blocks)`) were used to compute the medians, but
the function returned the unclamped `n_window_blocks` instead — with a 10-block configured
support and only 3 blocks available, all three are used but the field said 10; for an empty
series it said 10 despite using zero. All four real sessions happen to have ≥10 post-calibration
blocks, so their reported values were coincidentally correct; a shorter capture would not be.
Fixed: `trailing_leading_centroid_medians` now returns `n_used = min(n_window_blocks, n_blocks)`
(`0` for an empty series) — the actual count, not the request. The requested value is not
duplicated under another name since `summary_span_s` (already serialized) fully determines it
given `fs`/`block_frames`. Tests for the few-blocks and empty-series cases now assert the true
used count (3 and 0) instead of ignoring/asserting the stale requested value.

A malformed NPZ hop grid is rejected before alignment (`validate_frame_idx_grid`). Round 5's
version (BDR-19) rejected a first endpoint *below* the first valid window end, non-monotonic
ordering, and off-`hop_s × fs` spacing. Round 6 (BDR-19 R2) found this was still incomplete:
a first endpoint *above* 599 (e.g. `[659, 719]`) passed and mislabeled row 0 as the warmup window
even though it actually spans frames 60–659; the validator had no cube length with which to
reject a regularly spaced grid whose last endpoint runs past the last complete frame (NumPy
silently truncates such a slice); and `accepted_candidate_rank`/`candidate_rejection_codes`/
`f_r_hz` were never checked against `frame_idx`'s own length, so a short or over-long outcome
array would fail only by incidental indexing, or silently ignore extra rows. Fixed:
`validate_frame_idx_grid` took the cube's frame count and all three outcome arrays, required
`frame_idx[0] == window_frames - 1` **exactly** (not just `>=`), required every endpoint
`< n_cube_frames`, and required every outcome array's row count to equal `len(frame_idx)`.

**Round 7 (BDR-19 R3) found row-count checking alone was still insufficient:** a
`candidate_rejection_codes` array shaped `(n, 1)` or `(n, 2)` — the wrong number of AHET candidate
slots — passed the round-6 check, as did a `(n, 1)` `accepted_candidate_rank`/`f_r_hz` (all three
reproduced directly). A wrong-width code row would then either fail incidentally downstream or
silently change `gate_not_run` classification. Fixed: `validate_frame_idx_grid` now checks the
**exact declared shape** of every array — `frame_idx`, `accepted_candidate_rank`, and `f_r_hz`
must each be `(n,)`; `candidate_rejection_codes` must be `(n, AHET_MAX_CANDIDATES)` (imported from
`src.vitals`, not a duplicated literal `3`) — before any slicing or classification, all raising
`ValueError`.

**Round 8 (BDR-24) found exact-shape checking alone still permitted lossy numeric coercion:**
`frame_idx=[599.9, 659.9]`, `accepted_rank=[0.9, -1.0]`, and a fractional/out-of-domain
`candidate_rejection_codes` row all passed shape validation (a `.shape` check does not care about
dtype or fractional values), then were silently truncated by `int(...)`/`.astype(np.int64)`
downstream — the frame endpoint became 599 (dropping the fractional evidence of corruption), the
rank became 0, a code of `0.9` became `0` ("passed"), classifying a malformed row `"covered"`
instead of failing closed. Fixed: `frame_idx`, `accepted_candidate_rank`, and
`candidate_rejection_codes` (never `f_r_hz`, a genuine float) must now be finite, non-boolean, and
integer-valued (`np.array_equal(arr, np.round(arr))`) before any cast; `candidate_rejection_codes`
is additionally checked against the producer's own code domain `{-1, 0, ..., 7}`
(`REJECTION_CODE_DOMAIN`, src/vitals.py's documented contract) — an out-of-domain integer code
(e.g. `99`) now raises even though it is integer-valued.

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
  series. **`baseline_rank_of_locked_bin` is the locked bin's rank within the diagnostic's OWN
  settled baseline profile (BDR-21, corrected)** — round 5 sourced this field from
  `warmup_bin_selection.json`'s full-buffer (frames 0–599) `energy_rank`, a different quantity
  than what the field's name and §3.1's own definition claim (most starkly for legacy
  `live_test1`, whose JSON has no settled profile at all). The four real sessions' displayed
  numbers happen not to change (the two ranks agree on today's data), but the field is now
  actually computed from `baseline["settled_energy_by_bin"]` via `rank_of_bin_in_profile`. The
  full-buffer JSON rank remains available under its own explicit name,
  `full_buffer_warmup_rank_of_locked_bin`, rather than silently dropped.
- **Centroid formula:** weights are **power** (`|FFT|²`, matching `range_energy_by_bin`'s own
  units): `centroid = Σ(bin_index · power[bin]) / Σ(power[bin])` over the candidate gate bins.
- **Robust centroid-drift statistic, corrected (BDR-15):** median centroid over the **last N
  complete 1 s blocks** vs. the **first N complete post-calibration blocks**, where
  `N = round(cfg.centroid_summary_span_s / block duration)`, `centroid_summary_span_s = 10.0 s`
  by default (BDR-23, config-bound — see §1.1), selected **by position in the block series**, not
  by a frame-count threshold. The round-4 implementation computed the trailing window as
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
   blocks), the correct aggregate argmax is bin 9 (9× the energy). **The full per-bin profile
   this computation produces is now persisted, not discarded (BDR-14 R2):** round 5 computed
   `window_energies` directly on the window's slice but reduced it immediately to the two derived
   scalars (`window_argmax_bin`/`window_centroid`) and discarded the profile — `bin_energy_blocks.csv`
   only ever carried the 1 s-**block**-scale matrix, and `motion_energy_windows.npz` is a
   different statistic (channel-preserving temporal variance) that cannot audit this one. Each
   `WindowRow` now carries `window_energy_by_bin` (the full per-bin power dict), and
   `write_window_energy_npz` persists it as `window_energy_windows.npz` (§4) — an
   artifact-level test opens the written file and recomputes every saved window's argmax and
   centroid directly from its stored matrix, confirming they match the values computed during
   alignment exactly. Off-baseline duration and longest excursion **still use the finer 1 s-block
   series** — a genuinely different, correctly-scoped sub-window statistic, unaffected by this
   fix — plus:
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
  `accepted_candidate_rank` (BDR-02 R2, restored — round 6 computed the outcome class from it but
  never persisted the input itself), `candidate_rejection_codes` row, `f_r_hz`, and the derived
  outcome class. This is the audit trail the aggregate report is computed from — every saved
  `outcome_class` is exactly recomputable from that same row's own `accepted_candidate_rank`/
  `candidate_rejection_codes`/`f_r_hz` (tested end to end, BDR-02 R2).
- `motion_energy_windows.npz` — real `(n_windows, n_bins)` matrix (`window_indices`, `bins`,
  `matrix`) — BDR-18, replacing the round-4 single-vector-per-capture file.
- `window_energy_windows.npz` — the ordinary (non-motion) per-window per-bin energy profile
  (BDR-14 R2): `window_indices`, `bins`, a real `(n_windows, n_bins)` power `matrix`, and
  `matrix_rel_baseline_db` (baseline-relative dB, matching `bin_energy_blocks.csv`'s convention)
  — separate from motion energy above and from the 1 s-block matrix in `bin_energy_blocks.csv`.
  Empty `(0, n_bins)` for a session with no NPZ-defined windows (`live_test1`, BDR-07 Option A),
  same as every other window-level artifact.
- `summary.json` — **its own run manifest, not just the parent's (BDR-22, corrected)**:
  `run_id`, `git_commit`, `diagnostic_config_path`/`diagnostic_config_sha256`,
  `live_demo_config_path`/`live_demo_config_sha256` — round 5 recorded these only in the parent
  `run_summary.json`; a session directory cited or copied apart from its parent had no
  independent binding to the commit/config/run that produced it. Plus:
  - `baseline_profile` — the full per-bin settled-energy dict (BDR-14; previously only the
    derived argmax/centroid were kept), `baseline_argmax_bin`, `baseline_centroid`,
    `baseline_rank_of_locked_bin` (BDR-21, corrected — now the locked bin's rank **within this
    settled profile**, via `rank_of_bin_in_profile`, not copied from
    `warmup_bin_selection.json`'s full-buffer `energy_rank`), `full_buffer_warmup_rank_of_locked_bin`
    (the full-buffer JSON rank, kept under its own explicit name rather than dropped),
    `settled_warmup_json_validation` status (§3.1), and the composite-lock-vs-baseline-energy fact
  - `occupancy` — fraction of post-calibration blocks with argmax at baseline / within 1 bin /
    within 2 bins / outside 2 bins (BDR-14; promised in earlier rounds, never actually computed
    until now)
  - **episodes** — a **half-open 1 s block** `[t, t+1)` is off-baseline iff its argmax ≠
    `baseline_argmax_bin`. An episode is a **maximal run of consecutive off-baseline blocks** —
    a single on-baseline block ends it (no gap-bridging, config-governed §1.1). Duration = block
    count × 1 s. Each episode: `{start_s, end_s (exclusive), bin_sequence, modal_bin,
    max_displacement_bins}`. All episodes ≥ 1 block are listed with duration — episode
    *detection* needs no threshold.
  - `centroid_drift` — `summary_span_s` (the configured centroid support, BDR-23 R2),
    `n_blocks_used` (the resolved block count actually applied), and neutral `trailing_median`/
    `leading_median` fields (§3.1, corrected exact-block-count selection; renamed from
    `trailing_10s_median`/`first_post_calibration_10s_median`, which hardcoded "10s" regardless
    of the configured span — BDR-23 R2) — `centroid_drift_at_grid`: whether the displacement
    meets each of `{0.3, 0.5, 1.0}` bin. **Session-level only.**
  - **window-outcome classifier** — mutually exclusive: `covered`, `gate_not_run`,
    `other_rejected`. **`gate_not_run` is decided from all-not-run rejection codes ALONE
    (BDR-02 R3, corrected):** the no-ECA early return that produces them (`src/vitals.py:523`)
    fires whenever `f_r_hz` is `None` **or** a finite value outside the physiological gate
    `[0.15, 0.60]` Hz (`src/vitals.py:507-509`) — round 7 additionally required `f_r_hz` to be
    non-finite, which mislabeled real finite-outlier windows as `other_rejected` (verified
    directly against the approved replay NPZs: massimo1 has 6 such windows, sweep has 1; the
    corrected counts are massimo1 `gate_not_run=22`/`other_rejected=20`, sweep `gate_not_run=15`,
    against round 7's `16`/`26` and `14`). And **fail-closed on evidence the producer can never
    legitimately emit** (BDR-02 R2, BDR-02 R3): an `accepted_candidate_rank` outside
    `{-1, 0, ..., AHET_MAX_CANDIDATES-1}`; `accepted_candidate_rank >= 0` paired with a
    non-finite `f_r_hz`, or a finite `f_r_hz` outside the physiological gate (either failure
    means ECA/AHET could not have run, so a non-negative rank is impossible); an accepted rank
    paired with all-not-run rejection codes; an accepted rank whose own slot is not coded
    "passed"; or `accepted_candidate_rank == -1` paired with any slot coded "passed" (a passed
    slot always forces the corresponding non-negative rank, `src/vitals.py:941-943`) — each
    raises rather than silently landing in `"covered"` or `"other_rejected"`.
    **Round 9 (BDR-25) validates the remainder of the approved strict_v1 row contract:** a
    `rejection_codes` row must be ALL `-1` (the no-ECA branch never touches them) or have NO `-1`
    anywhere (`src/vitals.py:940` overwrites every never-attempted slot's `-1` with code 5 once
    the gate executes) — a MIX of the two is rejected; a nonnegative `accepted_candidate_rank`
    must equal the FIRST slot coded "passed" (`src/vitals.py:941-943`'s `passed_ranks[0]`
    selection), not merely its own slot being "passed" — an earlier passed slot with a later
    accepted rank is rejected; and an all-`-1` row is rejected when paired with a finite `f_r_hz`
    WITHIN the physiological gate, since ECA/AHET always executes (and assigns concrete codes to
    every slot) whenever `f_r_hz` passes the gate. Direct inspection of all three approved NPZs
    confirms zero mixed-`-1` rows, zero non-first-passed accepted ranks, and zero all-`-1` rows
    with an in-gate finite `f_r_hz` — the real evidence counts are unchanged by this round.
    **Round 10 (BDR-25 R2) found round 9 validated only ONE direction of the no-gate/executed-gate
    split:** an executed-gate row (no `-1` anywhere in `rejection_codes`) equally requires a
    finite, in-gate `f_r_hz` — the converse of round 9's all-`-1`-requires-out-of-gate check —
    regardless of whether any candidate ultimately passed; `rank=-1, codes=[2,3,5], f_r_hz=NaN`
    (or any out-of-gate finite value) previously returned `"other_rejected"` silently. Separately,
    `REJECTION_CODE_NOT_ATTEMPTED_WITHIN_GATE` (5) must form a trailing suffix only — candidates
    are attempted in strict order `0..N-1` (`src/vitals.py:815`), so the never-attempted
    complement (coded 5 at `src/vitals.py:940`) can never precede an attempted slot; `[5,2,5]` is
    structurally impossible but was accepted. The classifier is now expressed as two exhaustive
    producer states — **no-gate** (all `-1`, `accepted_rank=-1`, `f_r_hz` fails the gate) and
    **executed-gate** (no `-1`, finite in-gate `f_r_hz`, trailing code-5 suffix, then the
    first-passed/rank rules) — rather than an ad hoc check list, specifically so a missed
    converse cannot recur. Direct inspection of all three approved NPZs confirms zero concrete
    rows with invalid/out-of-gate `f_r_hz` and zero non-suffix code-5 rows — the real evidence
    counts are unchanged by this round too.
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

`summary.json`'s run manifest records **path + SHA-256** for every input actually used: `raw_path`
+ `raw_sha256` for `adc_stream.bin` (BDR-22 R2, corrected — round 6 recorded only the hash; a
`session_id` basename is not the exact CLI input path and the manifest was not self-contained
without it), the `warmup_bin_selection.json` used, the `live_intermediates.npz` used (where
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

**Fail-closed validation, before pairing a GENERATION (BDR-20):** raw-byte equality alone does
not prove which estimator generation produced a replay's recorded outcomes — direct inspection
found six replay directories sharing massimo1's raw hash across generations from 2026-07-15
through 2026-07-27. `scripts/diagnose_bin_drift_config.yaml`'s `approved_replays` maps each
capture's raw SHA-256 to the SHA-256 of that capture's one approved replay `run_metadata.json`;
`match_replays_to_captures` checks this before any outcome is loaded, and separately rejects two
different replay directories that both match the same capture's raw hash (previously computed
but never checked). A capture with no `approved_replays` entry rejects any replay offered for it
— a production run always enforces this (`diag_cfg.approved_replays` is never `None` once loaded
from a real config file); the function accepts an explicit `None` only for callers that
deliberately want unrestricted raw-hash matching (e.g. tests of the matching logic itself).

**Every session's own summary carries the full run manifest (BDR-22):** `run_id`, `git_commit`,
both config paths/hashes are embedded in each session's `summary.json`, not only in the parent
`run_summary.json` — HANDOFF directs the decision-maker to read individual session
`summary.json` files, which previously had no independent binding to the commit/config/run that
produced them.

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
| `tests/test_diagnose_bin_drift.py` | 108 tests (§7.1) |

Nothing else is touched. `data/raw/` not involved (empty); originals opened read-only.

### 7.1 Test plan (expanded across rounds 4–10)

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
- **Window-scale energy is persisted, not just computed (BDR-14 R2):** an artifact-level test
  writes `window_energy_windows.npz` for a real `align_windows` output, loads it back, and
  recomputes every saved window's argmax/centroid directly from the stored matrix — confirming
  they match the values `align_windows` itself computed, not merely that the file exists.
- **`validate_frame_idx_grid` rejects a late-anchored, beyond-cube, or shape-mismatched grid
  (BDR-19 R2):** a first endpoint above `window_frames - 1` (not just below), an otherwise
  well-formed grid whose last endpoint reaches or exceeds the cube's frame count, and each of
  `accepted_candidate_rank`/`candidate_rejection_codes`/`f_r_hz` too short or too long relative to
  `frame_idx` all raise.
- **`baseline_rank_of_locked_bin` is sourced from the settled baseline, not the full-buffer
  warmup JSON (BDR-21):** a settling-transient fixture where a large-amplitude tone exists only
  in frames 0–99 and a smaller tone (at the locked bin) exists only in frames 100–599 — the
  locked bin's rank differs between the full 0–599 buffer and the settled 100–599 slice, and the
  diagnostic's reported value must equal the latter.
- **Every session summary carries its own run manifest (BDR-22):** an integration test asserts
  `run_id`, `git_commit`, and both config paths/hashes appear in `run_session`'s result, matching
  the `RunContext` passed in, not only in the parent `run_summary.json`.
- **The centroid-drift support span is config-bound, not hardcoded (BDR-23):** a mutation test
  confirms changing `centroid_summary_span_s` from 10.0 s to 5.0 s changes the number of
  leading/trailing blocks selected and the resulting median.
- **Replay-generation binding (BDR-20):** two different replay directories matching the same
  capture's raw hash raise; a capture with no `approved_replays` entry rejects any replay offered
  for it; a replay matching a capture's raw bytes but not the approved `run_metadata.json` hash
  is rejected; the correctly approved pairing is accepted.
- **Outcome classifier is fail-closed on contradictory/out-of-domain evidence (BDR-02 R2):**
  `accepted_candidate_rank` of 5 (or -2) raises (outside `{-1,0,1,2}`); an accepted rank paired
  with a non-finite `f_r_hz` raises; an accepted rank paired with all-not-run rejection codes
  raises; an accepted rank whose own slot is not coded "passed" raises. An end-to-end integration
  test runs three real windows (one per outcome class, each satisfying the real invariants)
  through `run_session`, then reads back the written `window_audit.csv` and recomputes each row's
  `outcome_class` from that row's own persisted `accepted_candidate_rank`/`rejection_codes`/
  `f_r_hz` via `classify_window_outcome` directly, confirming it matches what was saved.
- **`validate_frame_idx_grid` checks exact declared shape, not just row count (BDR-19 R3):** a
  `(n,1)` `accepted_candidate_rank`, a `(n,1)` `f_r_hz`, a `(n,)` `candidate_rejection_codes`
  (missing its slot axis entirely), and `candidate_rejection_codes` with 1, 2, or 4 columns
  (instead of `AHET_MAX_CANDIDATES=3`) all raise.
- **`raw_path` is persisted in every session summary (BDR-22 R2):** asserted directly against
  `run_session`'s result in the same end-to-end integration test above.
- **Centroid-support validation and truthful serialization (BDR-23 R2):** `load_diagnostic_config`
  raises on `summary_span_s <= 0` (tested for both `0` and a negative value);
  `trailing_leading_centroid_medians` raises when a positive span still rounds to fewer than one
  complete block; the BDR-23 mutation test now also asserts the returned block count changes
  between a 10 s and a 5 s configured span, not just the numeric medians.
- **`gate_not_run` fires on a finite physiological outlier, not only non-finite `f_r_hz`
  (BDR-02 R3):** `classify_window_outcome(-1, [-1,-1,-1], 0.1168)` (a real massimo1 value, below
  the 0.15 Hz gate floor) returns `"gate_not_run"`, same as the non-finite case; an accepted rank
  paired with a finite out-of-gate `f_r_hz` raises; `accepted_rank=-1` paired with any "passed"
  code raises. The BDR-02 R2 end-to-end integration test was extended to a fourth window
  demonstrating the finite-outlier `gate_not_run` case through the real `run_session` pipeline,
  not just the unit-level classifier.
- **`n_blocks_used` reports the actual count, not the request (BDR-23 R3):** the few-blocks test
  (3 available against a 10-block request) now asserts `3`; the empty-series test asserts `0` —
  both previously asserted or ignored the stale requested value.
- **Integer-valued and domain checks reject lossy coercion (BDR-24):** a fractional `frame_idx`,
  a fractional `accepted_candidate_rank`, a fractional `candidate_rejection_codes` row, an
  integer-valued but out-of-domain code (`99`), and a boolean-dtype `accepted_candidate_rank` all
  raise; `f_r_hz` (a genuine float) is confirmed exempt from the integer-valued check.
- **The complete approved strict_v1 row contract is validated (BDR-25):** a mixed row
  (`[0, -1, -1]`, some codes `-1` and some concrete) raises; an accepted rank that is not the
  FIRST passed slot (`[0, 0, 2]` with `accepted_rank=1`, even though slot 1's own code is
  correctly `0`) raises; an all-`-1` row paired with an in-gate finite `f_r_hz` (`[-1,-1,-1]` with
  `accepted_rank=-1, f_r_hz=0.3`) raises. Every existing fixture using a since-shown-impossible
  pattern (e.g. `[0, -1, -1]` for a "covered" row) was replaced with a producer-valid one
  (`[0, 2, 5]`) so each test isolates the one contradiction it names, including the BDR-02 R2
  end-to-end integration test's four fixture windows.
- **The converse of the no-gate/executed-gate split is validated (BDR-25 R2):** a concrete
  (no-`-1`) row with a non-finite `f_r_hz` (`[2,3,5]`, `f_r_hz=NaN`) raises; the same row with an
  out-of-gate finite `f_r_hz` (`f_r_hz=1.2`) raises; a non-suffix `REJECTION_CODE_NOT_ATTEMPTED_
  WITHIN_GATE` (`[5,2,5]`) raises; and the valid edge case of an executed gate with zero
  candidates attempted (`[5,5,5]`, `accepted_rank=-1`) correctly returns `"other_rejected"`
  without raising. The integration test's `other_rejected` fixture window (previously
  `f_r_hz=1.5`, out of gate) was corrected to `f_r_hz=0.4` (in-gate) once this check made the
  previous value invalid.

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

1. `conda run -n radar-vitals python -m pytest tests/test_diagnose_bin_drift.py -q` — all 108
   cases pass.
2. Full suite still green (script is additive; expect 1616 baseline + 108 = 1724 passed, 1
   skipped).
3. Run on all 4 captures **from a clean committed tree**; confirm all output files exist
   (including the widened `bin_energy_blocks.csv`, the real `motion_energy_windows.npz` matrix,
   the new `window_energy_windows.npz` matrix, and the heatmap `drift_overview.png`), the
   warmup-recompute check passes at every resolution each session's own JSON provides,
   `summary.json` parses and includes `baseline_profile`, `occupancy`, `duration_grid_by_outcome`,
   and both `mem_peak_working_set_after_decode` / `mem_peak_working_set_after_session`.
4. Confirm `summary.json` records both `capture_run_metadata_sha256` and
   `replay_run_metadata_sha256` (the latter `None` for `live_test1`), and that
   `trailing_block_policy`/`gap_rule` values other than the supported ones raise rather than
   silently no-op.
5. Load the `dataviz` skill before writing the plotting code (chart-code trigger) — done; the
   heatmap uses a perceptually uniform sequential colormap and the outcome strip uses fixed
   categorical colors + distinct marker shapes.
6. **Round 6:** confirm each session's own `summary.json` (not just the parent `run_summary.json`)
   carries `run_id`/`git_commit`/`diagnostic_config_sha256`/`live_demo_config_sha256` (BDR-22);
   confirm `baseline_rank_of_locked_bin` and `full_buffer_warmup_rank_of_locked_bin` are both
   present and distinct fields (BDR-21); confirm the real run's three replays are each accepted
   under `scripts/diagnose_bin_drift_config.yaml`'s `approved_replays` mapping (BDR-20) — a run
   with an unregistered or wrong-generation replay must fail before decoding.
7. **Round 7:** confirm `window_audit.csv` has an `accepted_candidate_rank` column and every row's
   `outcome_class` recomputes from its own persisted rank/codes/`f_r_hz` (BDR-02 R2); confirm
   `summary.json` carries `raw_path` beside `raw_sha256` (BDR-22 R2); confirm `centroid_drift`
   uses the neutral `summary_span_s`/`n_blocks_used`/`trailing_median`/`leading_median` keys, not
   the retired `trailing_10s_median`/`first_post_calibration_10s_median` names (BDR-23 R2).
8. **Round 8:** confirm massimo1's real re-run reports `gate_not_run=22`/`other_rejected=20` and
   sweep reports `gate_not_run=15` (BDR-02 R3, corrected from round 7's `16`/`26` and `14` — spot
   -check directly against `window_audit.csv`, not just the console summary); confirm
   `centroid_drift.n_blocks_used` reflects the actual block count for a session shorter than the
   configured span, not the requested value (BDR-23 R3); confirm a fractional or out-of-domain
   NPZ field is rejected by `validate_frame_idx_grid` before any cast (BDR-24).
9. **Round 9:** confirm the real re-run's counts are UNCHANGED from round 8 (BDR-25 validates
   additional malformed/replaced-input states the 4 real captures never exercise — massimo1
   `22`/`20`, sweep `15` must still hold); confirm a mixed rejection-codes row, a non-first-passed
   accepted rank, and an all-not-run row paired with an in-gate finite `f_r_hz` are each rejected
   by `classify_window_outcome`.
10. **Round 10:** confirm the real re-run's counts are STILL unchanged (BDR-25 R2 validates the
    converse direction of the same producer-state contract, on states the 4 real captures don't
    exercise); confirm a concrete row (no `-1`) with a non-finite or out-of-gate finite `f_r_hz`
    raises, a non-suffix `REJECTION_CODE_NOT_ATTEMPTED_WITHIN_GATE` (`[5,2,5]`) raises, and the
    valid all-not-attempted-within-gate edge case (`[5,5,5]`) does not.
11. Session end: HISTORY.md append + HANDOFF.md rewrite (per CLAUDE.md §10) — including the
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
