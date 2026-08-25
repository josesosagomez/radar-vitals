# Handoff — M2 physical acquisition under way; 9 of 45 sessions captured

> Read this and `CLAUDE.md` before doing anything. **State verified 2026-08-25.**
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

Branch **`vital_signs_own_v13`**, HEAD `5cc49fd`. **HEAD is 2 commits ahead of
`origin/vital_signs_own_v13`** — `b807bb0` and `5cc49fd` are unpushed. The working tree carries
uncommitted work from the 2026-08-25 session: modified `HISTORY.md` and untracked
`notes/venue_iotj.md`.

**Physical acquisition has started.** This is the biggest change since the last handoff.

- Cohort registry is at **`cohort_registry/registry_v010.json`, revision 10**, digest
  `20604fb49a397e86f82f1e0773d7a152d5664a8af53e5eb80a7511f7b765312f`. Revisions v001-v010 are all
  committed; the history chain is intact.
- **9 of 45 sessions are `captured`, 36 remain `planned`:** P001 natural+paced, P002 natural+paced,
  P003 natural, P004 natural, P005 natural+paced, P006 natural.
- **All 15 subjects remain `label_state: sealed`.** No reference value has been opened for scoring.
- All 9 captured sessions are fully finalized and promoted: each directory under
  `data/raw/prospective/<session>/` holds `adc_stream.bin`, `session_manifest_v3.json`,
  `sealed_radar_receipt.json`, `<session>_reference.csv`, `reference_acquisition.json`,
  `settle_evidence.json`, `frame_validity.npy`, the effective/source config and the registry
  revisions the capture was bound to.
- **No recovery session has been captured yet** — every captured session is natural or paced.

**M2 engineering is complete, committed and unchanged.** The contract lives in `src/m2/`:
`manifest_v3.py` + `manifest.py` (prospective-v3 scoring validation with historical-v2
compatibility); `acquisition_metadata.py` (strict common/natural/paced/recovery metadata plus the
privacy schema — the sole authority on sidecar validity); `cohort_registry.py` (immutable P001-P015
registry, history chain and digests); `label_firewall.py` (atomic-only prospective label
transitions); `capture_artifacts.py` (sealed radar receipt, registry-bound registration,
no-overwrite promotion); `validity.py`, `time_sensitivity.py`, `retry.py`, `preflight.py`,
`common.py`. Operator CLIs: `scripts/m2_register_capture.py`, `scripts/m2_finalize_capture.py`,
`scripts/m2_preflight.py`. `scripts/live_demo.py` is the sole prospective live acquisition path.

**Nothing has been analysed.** No agreement metric, no MAE/RMSE/Bland-Altman, no scoring run
against any captured session. Do not start M3.

**Test state — green, re-measured 2026-08-25 at HEAD `5cc49fd`: 3140 passed, 5 skipped** in 346.9 s
(exit 0). This reproduces the 2026-08-12 milestone-0 baseline of 3140 passed / 5 skipped exactly;
the wall-clock difference from the 231 s then recorded is invocation overhead, not new work.
Consistent with `git diff da9777c..HEAD -- src/ tests/ scripts/` being **empty** — the only changes
since the baseline commit are added registry revision files. Invoke as
`conda run -n radar-vitals python -m pytest -q`
**without** a shared `--basetemp`: the `test_m8_ahmed_provenance`, `test_m1_production_scoring` and
`test_m4_preflight_strict` provenance tests build throwaway git repositories in temp dirs, and
funnelling them through one temp parent produced 25 spurious failures.

## 3. Active task / next steps

Owner-set priority, 2026-08-25: **Track 0 first, then Track 1 in parallel with Track 2.**

**Track 0 — resolve static clutter removal. Do this before other technical work.** Open since
2026-07-30 (`notes/approach.md` §3 step 3, `HISTORY.md` 2026-07-30): the production path has **no**
static clutter removal — `src/respiration.py::extract_chest_phase` goes Hann → range FFT → locked
bin → `delta_before_mean` → cumsum with nothing between, and it is disabled on-chip too
(`clutterRemoval -1 0`, `calibDcRangeSig -1 0`). `delta_before_mean` cancels static *per-channel
phase offsets* only; it does **not** cancel additive static clutter in the range bin, which
compresses the phase excursion and introduces harmonic distortion. That matters here specifically
because HR rests on AHET second-harmonic verification and ECA on respiration harmonics, and the
2026-07-28 captures contain static reflectors stronger than the subject (`notes/protocol.md`,
"Scene behind the subject"). The decision is binary: **justify the omission with a citation, or
implement it.** Investigate properly and decide — do not default. Whatever is chosen, note that
changing downstream DSP can move the warmup bin lock.

**Track 1 — continue physical acquisition.** Follow `notes/m2_capture_runbook.md` exactly. 36
sessions remain, including **all 15 recovery sessions**, which are the only source of the HR dynamic
range any HR agreement claim depends on. Work directories already exist for `P003_paced`,
`P007_natural` and `P009_natural` under `m2_capture_work/` but those sessions are **not** captured
or promoted — treat them as prepared, not done.

**Track 2 — build `scripts/m2_scaffold_sidecar.py`** (does not exist yet). The operator tool that
derives every mechanical acquisition-sidecar field from the committed registry, prompts only for
genuine measurements and attestations, hashes the settle evidence, and emits the exact
`live_demo.py` invocation. Plan: `plans/m2_sidecar_scaffold.md` (revision 3; two independent plan
reviews, the second returning APPROVE WITH CHANGES with its blocker-class items resolved — **no
third plan review is needed**). In order:

1. **Milestone A**: the no-behavior-change constants extraction in `src/m2/acquisition_metadata.py`
   (plan §4) — its own commit, its own independent code review. **Not started**; that file is
   unchanged since the baseline commit.
2. Implement the tool (plan §6), write the tests (plan §8), then an independent code review of the
   diff.

The scaffold tool is a convenience layer over the runbook, not a precondition for capture.

**Track 3 — make the live output a real, paper-defensible real-time result.** Owner-designated
focus area, 2026-08-25. Today nothing `scripts/live_demo.py` prints and nothing in
`live_estimates.csv` is paper-grade (CLAUDE.md §4): the live path applies an online median smoother,
while paper metrics come from offline re-processing of the saved `adc_stream.bin`. IoT-J judges this
as a systems paper, so an unvalidated demo will not carry a real-time claim. **Two routes, and the
choice is deliberately still open** — decide it when the work is actually picked up, not before:
(a) make the causal/online estimator reproducible from the saved stream and score *that* estimator,
or (b) keep offline scoring as the headline and separately measure and report the online-versus-
offline gap. Either way the missing measurements are the same and are needed regardless: per-window
latency distribution, sustained throughput against the 20 Hz frame budget, peak memory, CPU, the
edge/host split, and time-to-first-estimate (warmup runs the full downstream chain over 14 candidate
bins before the first reported estimate). None of that instrumentation exists anywhere in `src/` or
`scripts/`.

## 4. Recent decisions that matter

- **Target venue is IEEE IoT-J.** It judges this as a *systems* paper, so the deployable-system
  evidence is a submission requirement, not polish: measured latency, throughput, memory, the
  edge/host split, and a real-time claim that survives review. None of that instrumentation exists
  anywhere in `src/` or `scripts/` (verified 2026-08-25). Details and open actions in
  `notes/venue_iotj.md`.
- **No ECG sub-study. Decided 2026-08-25, closed.** The Masimo MightySat 1 Hz fingertip pulse rate
  stays the sole HR reference. Reasons: the equipment is not available, adding a reference device
  would require more subjects, and an ethics amendment could not clear in a workable timeframe.
  Competitors (mmHRV, ViMo) use contact ECG, so reviewers will raise it — the answer is a Methods
  **reference-error budget**, not a new device: the undisclosed internal averaging window,
  peripheral pulse-transit lag (both worst where HR changes fastest, i.e. the recovery ramp), and
  the PI-based quality gating the pipeline already applies. State plainly that IBI/HRV is out of
  scope by design. Do not re-propose ECG.
- **Never call this study's design "pre-registered", "pre-specified", "frozen before data" or
  "confirmatory"** — CLAUDE.md §4, standing rule `HISTORY.md` 2026-08-03. An external literature
  report received 2026-08-25 used that phrasing about our cohort design; it was rejected. The
  design is a **transparency** property, not a timing one.
- **The natural/paced launch countdown is 30 s.** Do not "restore" 60. Verified at
  `scripts/live_demo.py:769-773`: recovery returns 0, everything else returns 30. It leaves no
  evidentiary footprint (absent from `run_metadata`, not a CLI argument, and `start_wall_utc` is
  stamped after it elapses) and is a **different quantity** from `settle_evidence_window_s`, which
  remains validated at exactly 60.0 (`src/m2/acquisition_metadata.py:332-333`).
- **Operator work files live in the gitignored in-repo `m2_capture_work/`**, not outside the
  checkout. Reason: the repository is private and this is more convenient. Confirmed gitignored via
  `.gitignore:14`. Residual accepted: gitignored is not the same as outside, so a forced add or a
  repo archive would capture them. The same applies to `data/` (`.gitignore:13`) — **no captured
  session is tracked by git**, so the raw data exists only on this machine.
- **Recovery `distance_m` is measured at seating, to the actual chest** — a seated-stage
  measurement, not a pre-exertion one. Accepted cost: tape-measuring consumes recovery ramp.
- **The scaffold tool will stamp the recovery seating epoch itself**, before the distance
  measurement, so the recorded delay is honest rather than flattering. Until that tool ships, the
  manual `$recoverySeatedStartUtc` procedure in the runbook stands and is correct.
- **`sit_to_record_delay_s` stays unbounded for now** — observe real values before setting a
  threshold.

## 5. Gotchas / landmines

- **`P006_natural` is a `final_evaluation` capture and it is already in the can**, taken before the
  five-subject `representation_validation` cohort (P001-P005) is complete. Enrolment-order role
  assignment permits this, but it means **nothing learned from P006 or any later subject may feed
  back into the algorithm, thresholds or representation choice.** Role reassignment is forbidden
  (`notes/analysis_prespec.md` §3.1).
- **Pass the *latest* registry revision explicitly.** `--cohort-registry` is required by
  `scripts/live_demo.py` and `--registry` by `scripts/m2_register_capture.py`; nothing auto-selects
  the newest. `src/m2/cohort_registry.py:24` still defaults `DEFAULT_REGISTRY_PATH` to
  `registry_v001.json`, which is the genesis revision, not current state.
- Exact command duration is 600 seconds (`src/m2/acquisition_metadata.py:45`); the canonical stream
  is exactly 12,000 frames at 20 Hz (`src/m2/capture_artifacts.py:202`, `:391`).
- Frame origin is the start-assignment event for frame index 0 — never `start_wall_utc`, never
  full-frame completion.
- Record signed `PC UTC - phone UTC` at both ends; each absolute value must be <= 1.0 s
  (`MAX_CLOCK_OFFSET_S`, `src/m2/acquisition_metadata.py:46`).
- Use automatic warmup bin selection. Never use a manual bin, replay, `--no-configure`, an alternate
  config, or any Masimo/reference value to choose DSP, alignment or a retry.
- Packet loss flags the session; the Boolean frame map decides which windows carry radar NaNs. It
  does not authorize a retry.
- Low warmup confidence, a missing reference, recovery adequacy or yield, radar coverage and
  agreement **never** authorize recapture.
- **A recovery Stage-1 failure cannot trigger recapture** and the session needs `>= 20.0` bpm PR
  range (`notes/analysis_prespec.md` §Stage-1 recovery adequacy). Time spent at the seating instant
  eats that range, so it can permanently forfeit a final-evaluation subject.
- Keep participant names, re-identification keys, screening answers, diagnoses, symptoms,
  medication/pregnancy data, consent/PIS and health narratives out of this repository entirely.
- **Raw-capture integrity is guaranteed by a hash chain, not by file permissions — this is settled,
  do not reopen it.** The committed `cohort_registry/registry_v010.json` holds a
  `radar_receipt_sha256` per captured session; each `sealed_radar_receipt.json` holds `raw_sha256`
  for `adc_stream.bin` plus SHA-256 for `run_metadata.json`, `effective_config.json`,
  `source_config.yaml`, `settle_evidence.json`, `frame_validity.npy`, the acquisition sidecar and
  the capture git commit. Full verification 2026-08-25: **9/9 receipts match the committed registry
  and 9/9 raw streams match their `raw_sha256`.** That chain is what satisfies CLAUDE.md §3.1
  input-hash traceability. Raw captures are additionally backed up to an external hard drive
  (owner-confirmed 2026-08-25), so the gitignored `data/` tree is not a single point of failure.
  Re-run the check after any promotion; ignore filesystem read-only attributes entirely.
- Run Python through `conda run -n radar-vitals`. `conda` is **not** on PATH in this session's
  shells; it lives at `C:\ProgramData\anaconda3\Scripts\conda.exe`. The bare `python` on PATH is
  MSYS2 (`C:/msys64/mingw64/bin/python.exe`) — stdlib-only work is fine with it, but invoking the
  environment's `python.exe` by absolute path crashes matplotlib `savefig` with exit 127 and no
  traceback.
- Nothing `scripts/live_demo.py` shows on screen, and nothing in `live_estimates.csv`, is
  paper-grade. Paper metrics are computed offline by re-processing the saved `adc_stream.bin`. See
  `notes/venue_iotj.md` §4.3 — this collides with the real-time claim IoT-J expects and needs a
  decision.

## 6. Pointers

| File | Purpose |
|---|---|
| `notes/m2_capture_runbook.md` | exact physical acquisition and finalization procedure — Track 1 |
| `plans/m2_sidecar_scaffold.md` | Track 2 build target: sidecar scaffold tool, revision 3 |
| `plans/m2_engineering_acquisition_preflight.md` | reviewed M2 engineering contract |
| `notes/venue_iotj.md` | venue decision, prior-work triage, submission requirements |
| `notes/protocol.md` | approved three-arm participant protocol |
| `notes/analysis_prespec.md` | cohorts, timing, validity, retry and dynamic-HR rules |
| `notes/approach.md` | method, physics, pipeline as implemented, open DSP decisions |
| `src/m2/acquisition_metadata.py` | sole authority on acquisition-sidecar validity |
| `cohort_registry/registry_v010.json` | current cohort registry, revision 10 |
| `templates/m2_acquisition_*.yaml` | arm-specific acquisition sidecars |
| `templates/m2_finalization.yaml` | post-capture/end-offset/reference record |
| `scripts/live_demo.py` | sole prospective live acquisition path |
| `scripts/m2_register_capture.py` | receipt-to-registry immutable revision command |
| `scripts/m2_finalize_capture.py` | reference binding and no-overwrite promotion command |
| `scripts/m2_preflight.py` | dry-run and real-manifest engineering validator |
| `data/raw/prospective/` | the 9 promoted captures (gitignored, this machine only) |
| `m2_capture_work/` | operator work files, gitignored |
| `HISTORY.md` | append-only evidence and decision log |
