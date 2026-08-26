# Handoff — start on Track 0: the static-clutter-removal decision

> Read this and `CLAUDE.md` before doing anything. **State verified 2026-08-26.**
> `HISTORY.md` is append-only; this file is the current resume point.

## 1. Project snapshot

Estimate a seated person's heart rate in real time from a 76-81 GHz FMCW radar (TI IWR1642BOOST plus
DCA1000EVM for raw ADC capture), radar at chest height facing the chest, subject 0.8-1.4 m away.
Ground truth is a Masimo MightySat fingertip pulse oximeter exported as a 1 Hz CSV: the
`Beats / min` (PR) column is the heart-rate reference, and `Perfusion Index` gates reference quality.
Paper-grade means MAE, RMSE and Bland-Altman agreement against Masimo PR — and only on sessions that
carry real HR dynamic range, which is why the seated recovery arm exists.

Target venue is **IEEE IoT-J, regular issue** — see `notes/venue_iotj.md` for the decision, the
prior-work triage and what the venue requires that this project does not yet have.

## 2. Current state

Branch **`vital_signs_own_v13`**, pushed and in sync with `origin/vital_signs_own_v13`, working tree
clean as of 2026-08-26. **Check `git status -sb` rather than trusting this line** — a HEAD hash
written here goes stale the moment anything is committed. Landmark commits, which do not move:
`fc4bc75` added static clutter removal (off by default), `e04ae16` restored the cohort registry,
`da9777c` recorded the milestone-0 test baseline, `5cc49fd` registered the most recent capture,
`1221bde` selected the target venue, `9a4330c` cleaned up orphaned artifacts.

**Acquisition is under way; nothing has been analysed.**

- Cohort registry is at **`cohort_registry/registry_v010.json`, revision 10**, digest
  `20604fb49a397e86f82f1e0773d7a152d5664a8af53e5eb80a7511f7b765312f`. Revisions v001-v010 are all
  committed; the history chain is intact.
- **9 of 45 sessions are `captured`, 36 remain `planned`:** P001 natural+paced, P002 natural+paced,
  P003 natural, P004 natural, P005 natural+paced, P006 natural.
- **All 15 subjects remain `label_state: sealed`.** No reference value has been opened for scoring.
- All 9 are finalized and promoted under `data/raw/prospective/<session>/` with `adc_stream.bin`,
  `session_manifest_v3.json`, `sealed_radar_receipt.json`, `<session>_reference.csv`,
  `reference_acquisition.json`, `settle_evidence.json`, `frame_validity.npy` and configs.
- **No recovery session has been captured yet** — every captured session is natural or paced.
- **No agreement metric exists.** No MAE/RMSE/Bland-Altman, no scoring run against any captured
  session. Do not start M3.

**M2 engineering is complete, committed and unchanged.** The contract lives in `src/m2/`:
`manifest_v3.py` + `manifest.py` (prospective-v3 scoring validation with historical-v2
compatibility); `acquisition_metadata.py` (strict common/natural/paced/recovery metadata plus the
privacy schema — the sole authority on sidecar validity); `cohort_registry.py` (immutable P001-P015
registry, history chain and digests); `label_firewall.py`; `capture_artifacts.py` (sealed receipt,
registry-bound registration, no-overwrite promotion); `validity.py`, `time_sensitivity.py`,
`retry.py`, `preflight.py`, `common.py`. Operator CLIs: `scripts/m2_register_capture.py`,
`scripts/m2_finalize_capture.py`, `scripts/m2_preflight.py`. `scripts/live_demo.py` is the sole
prospective live acquisition path.

**Test state — green, measured 2026-08-26 at `9a4330c`: 3140 passed, 5 skipped** in 208 s (exit 0),
matching the milestone-0 baseline count exactly. Invoke as
`conda run -n radar-vitals python -m pytest -q` **without** a shared `--basetemp`: the
`test_m8_ahmed_provenance`, `test_m1_production_scoring` and `test_m4_preflight_strict` provenance
tests build throwaway git repositories in temp dirs, and funnelling them through one temp parent
produced 25 spurious failures.

**The repository was cleaned on 2026-08-26** (`9a4330c`): 13.5 GB of duplicate raw streams, 11
unrunnable `diag_*.py` scripts, 11 one-off review prompts and `plans/m0_preregistration.md` were
deleted. Full rationale in `HISTORY.md` 2026-08-26.

## 3. Active task — Track 0: static clutter removal

**Start here.** The other tracks are in §4.

### 3.1 What is actually true

**Static clutter removal is implemented and has always been switched off.** Read this carefully —
`notes/approach.md` asserted the opposite until 2026-08-26 and that error was propagated into this
file, so do not trust any older statement about it.

- `src/clutter.py::remove_static_clutter` implements `CLUTTER_METHODS = ("none", "slow_time_mean")`.
  `slow_time_mean` subtracts the per-`(chirp, rx)` mean over slow time, estimated per channel
  independently.
- It is reached from `src/respiration.py::extract_chest_phase(clutter_removal=...)`, default
  `"none"`, and `src/window_pipeline.py:72` reads `cfg["phase"].get("clutter_removal", "none")`.
- **`scripts/live_demo_config.yaml`'s `phase:` block omits the `clutter_removal` key entirely**, so
  every capture to date — development and prospective — ran with `"none"`.
- Provenance: commit `fc4bc75`, 2026-07-30, "Add static clutter removal, off by default and not yet
  shown to help".
- On-chip removal is separately disabled: `steps/step_1/capture.py` sends `clutterRemoval -1 0` and
  `calibDcRangeSig -1 0`.

**So the decision is "turn it on, or justify leaving it off", decided on evidence.** Not "implement
it". Do not default either way.

**Do not conflate `delta_before_mean` with clutter removal.** It cancels static *per-channel phase
offsets*, which is all its docstring claims. It does **not** cancel additive static clutter in the
range bin, which compresses the phase excursion and introduces harmonic distortion — and that
matters here specifically because HR rests on AHET second-harmonic verification and ECA on
respiration harmonics.

### 3.2 Why it is not academic

`notes/protocol.md` (§"Scene behind the subject", note added 2026-07-30) records that the five
2026-07-28 captures contain static reflectors at **2.09 m, 2.88 m and 4.19 m returning more energy
than the subject**. The scene drifted from protocol, unrecorded. Those captures are development
data, so the effect can be measured directly rather than argued.

### 3.3 Settle this first — it sets the blast radius

**Does offline scoring re-derive the warmup bin from the raw stream, or reuse the bin recorded at
capture time?** `notes/analysis_prespec.md` is silent (searched 2026-08-25 for `locked_bin`,
"bin lock", "re-derive", "re-select" — no matches). Each capture records `locked_bin`,
`locked_bin_source`, `warmup_selected_bin` and `warmup_selection_confidence` in `run_metadata.json`
(P001_natural: bin 26, `warmup_auto`, high).

It matters because warmup scores candidate bins by running the **entire downstream chain** per bin,
so enabling clutter removal can move the lock. If offline re-derives, the 9 captured sessions are
unaffected and simply get reprocessed. If offline reuses the recorded bin, a mid-acquisition DSP
change splits the cohort across bin-selection rules — protocol drift under CLAUDE.md §3.6, and a
direct conflict with Track 1 capturing in parallel.

### 3.4 The harness already exists

`scripts/score_offline.py --isolate-fields phase.clutter_removal` enforces that two configs differ
in **that field alone**: it refuses to run if they differ outside the allowlist, if the declared
fields do not actually differ, or if the configs are identical. That is the A/B mechanism — use it
rather than building one.

A config pair using it (`experiments/exp_clutter_removal/`, `clutter_on.yaml` = `slow_time_mean`,
`clutter_off.yaml` = `none`, generated from `live_demo_config.yaml` with that single difference) was
**deliberately deleted 2026-08-26** on the owner's instruction to start clean. Recover it verbatim
with `git checkout e6efffe -- experiments/exp_clutter_removal/` only if a reference is wanted; the
intent is a fresh build.

### 3.5 Data — development only

The 8 development captures (subjects A-D) are under `results/live_demo/`, **not** `data/raw/`:
`20260713_172042_..._massimo1`, `20260713_182002_..._massimo2`, `20260714_180523_..._sweep`, and
`massimo3`-`massimo7` from 2026-07-28/29. They were never promoted, which is why `data/raw/` holds
only `prospective/` and `data/manifest.local.csv` is header-only with zero rows. **That is expected,
not damage.**

**Decision 2026-08-26 (Option B): read them in place. Do not copy them into `data/raw/`.**
Per-capture SHA-256, the subject map and the live-versus-corrected bin table are in
`notes/capture_inventory.md`; log the hash of every raw file the experiment reads (CLAUDE.md §3
rule 1). A small tracked resolver — capture id → run directory, verified against those committed
hashes — is the intended plumbing.

**`P001`-`P006` are off-limits.** Any method fitted, selected or changed using
`representation_validation` data consumes that cohort and taints it (`notes/analysis_prespec.md`
§3.1 data-roles table). Owner has confirmed those captures will not be touched.

### 3.6 Prior art to read before searching outward

`notes/approach.md` Part A §3 (pipeline as implemented) and Part C (open questions);
`notes/plan_eca_forbidden_zone.md`; `plans/bin_drift_diagnostic.md`; and the 2026-07-14 mislock
history in `notes/capture_inventory.md`.

## 4. Other tracks (not now)

| track | what | blocked by |
|---|---|---|
| 1 | capture the remaining 36 sessions, **including all 15 recovery** — the only source of HR dynamic range | participant scheduling and operator time; runs in parallel, but see §3.3 |
| 2 | `scripts/m2_scaffold_sidecar.py` (does not exist). Milestone A = constants extraction in `acquisition_metadata.py`, own commit + own review; then tool, tests, code review. Plan `plans/m2_sidecar_scaffold.md` rev 3, two reviews done, **no third needed**. Not started | nothing |
| 3 | make the live output a defensible real-time result for IoT-J | route choice, for the scoring half only |

**Track 3 detail.** Nothing `scripts/live_demo.py` prints and nothing in `live_estimates.csv` is
paper-grade (CLAUDE.md §4): the live path applies an online median smoother, while paper metrics
come from offline re-processing of the saved `adc_stream.bin`. Two routes, **choice deliberately
open**: (a) make the causal/online estimator reproducible from the saved stream and score *that*, or
(b) keep offline scoring as the headline and separately measure the online-versus-offline gap. The
measurements are needed under either route: per-window latency distribution, sustained throughput
against the 20 Hz frame budget, peak memory, CPU, edge/host split. **`t_warmup_scan_ms` already
exists** and should not be rebuilt — stamped into every capture's `run_metadata.json`
(`scripts/live_demo.py:1033`, `:1491`; P001_natural = 4032 ms), covering the warmup component of
time-to-first-estimate, since warmup runs the full chain over 14 candidate bins before the first
estimate. Everything else on that list is genuinely absent.

## 5. Recent decisions that matter

- **Track 0 is the priority** (owner, 2026-08-25), then Track 1 in parallel with Track 2.
- **Development data stays in `results/live_demo/`** (Option B, owner 2026-08-26). Traceability is
  already satisfied by the committed hashes in `notes/capture_inventory.md`; what was missing is
  only tool plumbing. Rejected: registering into `data/manifest.local.csv` + `data/raw/<id>.bin`,
  because it would duplicate ~9.5 GB, require filling manifest columns (`distance_cm`, `posture`)
  that are genuinely null for these exploratory runs, and buy access to a toolchain that was itself
  orphaned and has since been deleted.
- **No ECG sub-study. Closed 2026-08-25, do not re-propose.** Masimo 1 Hz fingertip PR stays the
  sole HR reference: the equipment is unavailable, a second device would need more subjects, and an
  ethics amendment could not clear in a workable timeframe. Competitors (mmHRV, ViMo) use contact
  ECG and reviewers will raise it; the answer is a Methods **reference-error budget** — undisclosed
  internal averaging window, peripheral pulse-transit lag (both worst on the recovery ramp), and the
  PI-based quality gating already applied — plus stating that IBI/HRV is out of scope by design.
- **Never call this study's design "pre-registered", "pre-specified", "frozen before data" or
  "confirmatory"** — CLAUDE.md §4, standing rule `HISTORY.md` 2026-08-03. An external literature
  report received 2026-08-25 used that phrasing; it was rejected. The design is a **transparency**
  property, not a timing one.
- **The natural/paced launch countdown is 30 s** (`scripts/live_demo.py:769-773`; recovery returns
  0). Do not "restore" 60. It leaves no evidentiary footprint and is a **different quantity** from
  `settle_evidence_window_s`, validated at exactly 60.0 (`src/m2/acquisition_metadata.py:332-333`).
- **Operator work files live in the gitignored in-repo `m2_capture_work/`** (`.gitignore:14`).
  Residual accepted: a forced add or repo archive would capture them.
- **Recovery `distance_m` is measured at seating, to the actual chest.** Accepted cost:
  tape-measuring consumes recovery ramp. The scaffold tool will stamp the seating epoch itself;
  until it ships the manual `$recoverySeatedStartUtc` runbook procedure stands.
- **`sit_to_record_delay_s` stays unbounded** — observe real values before setting a threshold.

## 6. Gotchas / landmines

- **`P006_natural` is a `final_evaluation` capture already in the can**, taken before the
  `representation_validation` cohort (P001-P005) completed. Enrolment-order role assignment permits
  this, but **nothing learned from P006 or later may feed back into the algorithm, thresholds or
  representation choice.** Role reassignment is forbidden.
- **Before deleting anything, search code and tests, not just markdown, and run the suite before
  committing.** On 2026-08-26 `plans/m8_ahmed_correction_plan.md` was deleted as an "orphan"; it is
  a hashed provenance dependency (`figures/reproduce_ahmed_fig8.py:51` writes
  `correction_plan_sha256`; `scripts/m8_ahmed_transfer.py:146` lists it as a Layer B authority
  path). 15 M8 tests failed. It was restored.
- **Pass the *latest* registry revision explicitly.** `--cohort-registry` is required by
  `scripts/live_demo.py`, `--registry` by `scripts/m2_register_capture.py`; nothing auto-selects the
  newest, and `src/m2/cohort_registry.py:24` still defaults `DEFAULT_REGISTRY_PATH` to the genesis
  `registry_v001.json`.
- Exact command duration is 600 s (`src/m2/acquisition_metadata.py:45`); the canonical stream is
  exactly 12,000 frames at 20 Hz (`src/m2/capture_artifacts.py:202`, `:391`).
- Frame origin is the start-assignment event for frame index 0 — never `start_wall_utc`, never
  full-frame completion.
- Record signed `PC UTC - phone UTC` at both ends; each absolute value must be <= 1.0 s
  (`MAX_CLOCK_OFFSET_S`, `src/m2/acquisition_metadata.py:46`).
- Use automatic warmup bin selection. Never a manual bin, replay, `--no-configure`, an alternate
  config, or any Masimo/reference value to choose DSP, alignment or a retry.
- Packet loss flags the session; the Boolean frame map decides which windows carry radar NaNs. It
  does not authorize a retry. Low warmup confidence, a missing reference, recovery adequacy or
  yield, radar coverage and agreement **never** authorize recapture.
- **A recovery Stage-1 failure cannot trigger recapture**, and the session needs `>= 20.0` bpm PR
  range (`notes/analysis_prespec.md`, recovery Stage-1 adequacy). Time spent at the seating instant
  eats that range and can permanently forfeit a final-evaluation subject.
- Keep participant names, re-identification keys, screening answers, diagnoses, symptoms,
  medication/pregnancy data, consent/PIS and health narratives out of this repository entirely.
- **Raw-capture integrity is a hash chain, not file permissions — settled, do not reopen.**
  Committed registry → `radar_receipt_sha256` → `sealed_radar_receipt.json` → `raw_sha256` of
  `adc_stream.bin`, plus digests for the metadata, configs, settle evidence, frame map and capture
  commit. Verified 2026-08-25: **9/9 receipts and 9/9 raw streams match.** Captures are also backed
  up to an external drive (owner-confirmed). Re-run the check after any promotion; ignore read-only
  attributes.
- Run Python through `conda run -n radar-vitals`. **`conda` is not on PATH** in this session's
  shells — it lives at `C:\ProgramData\anaconda3\Scripts\conda.exe`. The bare `python` on PATH is
  MSYS2 (`C:/msys64/mingw64/bin/python.exe`), fine for stdlib work, but invoking the environment's
  `python.exe` by absolute path crashes matplotlib `savefig` with exit 127 and no traceback.
- Heredocs through the Bash tool fail on multi-line Markdown; write with the Write tool instead.

## 7. Pointers

| File | Purpose |
|---|---|
| `src/clutter.py` | `remove_static_clutter` — the Track 0 subject |
| `src/respiration.py` | `extract_chest_phase` — where clutter removal is applied |
| `src/window_pipeline.py` | reads `phase.clutter_removal` from config (line 72) |
| `scripts/score_offline.py` | `--isolate-fields` A/B harness |
| `scripts/live_demo_config.yaml` | production config; its `phase:` block omits `clutter_removal` |
| `notes/capture_inventory.md` | the 8 development captures: hashes, subject map, bin table |
| `results/live_demo/` | development capture data (A-D), read in place, never promoted |
| `notes/approach.md` | method, physics, pipeline as implemented, open DSP decisions |
| `notes/protocol.md` | approved three-arm participant protocol; the reflector-scene note |
| `notes/analysis_prespec.md` | cohorts, roles, timing, validity, retry and dynamic-HR rules |
| `notes/venue_iotj.md` | venue decision, prior-work triage, submission requirements |
| `notes/m2_capture_runbook.md` | exact physical acquisition and finalization procedure (Track 1) |
| `plans/m2_sidecar_scaffold.md` | Track 2 build target, revision 3 |
| `src/m2/acquisition_metadata.py` | sole authority on acquisition-sidecar validity |
| `cohort_registry/registry_v010.json` | current cohort registry, revision 10 |
| `scripts/live_demo.py` | sole prospective live acquisition path |
| `data/raw/prospective/` | the 9 promoted captures (gitignored, plus external backup) |
| `HISTORY.md` | append-only evidence and decision log |
