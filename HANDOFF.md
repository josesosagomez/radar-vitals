# Handoff — M2 engineering committed; physical acquisition pending

> Read this and `CLAUDE.md` before doing anything. **State verified 2026-08-12.**
> `HISTORY.md` is append-only; this file is the current resume point.

## 1. Project snapshot

Estimate a seated person's heart rate in real time from a 76-81 GHz FMCW radar (TI IWR1642BOOST plus
DCA1000EVM for raw ADC capture), radar at chest height facing the chest, subject 0.8-1.4 m away.
Ground truth is a Masimo MightySat fingertip pulse oximeter exported as a 1 Hz CSV: the
`Beats / min` (PR) column is the heart-rate reference, and `Perfusion Index` gates reference quality.
Paper-grade means MAE, RMSE and Bland-Altman agreement against Masimo PR — and only on sessions that
carry real HR dynamic range, which is why the seated recovery arm exists.

## 2. Current state

Branch **`vital_signs_own_v13`**, pushed and in sync with `origin/vital_signs_own_v13`, working tree
clean. Landmark commits: `e04ae16` restored the cohort registry, `1db9d8d` aligned the countdown to
30 s, `289d5b3` added the scaffold plan.

M2 acquisition engineering is implemented, tested, independently reviewed and **committed**. Overall
**M2 is FAIL / pending physical acquisition**: no prospective participant has been captured, no
prospective reference has been opened, and no capture result has been fabricated. **Do not start M3.**

The M2 contract lives in `src/m2/` (all files present): `manifest_v3.py` + `manifest.py`
(prospective-v3 scoring validation with historical-v2 compatibility); `acquisition_metadata.py`
(strict common/natural/paced/recovery metadata plus the privacy schema — the sole authority on sidecar
validity); `cohort_registry.py` (immutable P001-P015 registry, history chain and digests);
`label_firewall.py` (atomic-only prospective label transitions); `capture_artifacts.py` (sealed radar
receipt, registry-bound registration, no-overwrite promotion); `validity.py`, `time_sensitivity.py`,
`retry.py`, `preflight.py`. Operator CLIs: `scripts/m2_register_capture.py`,
`scripts/m2_finalize_capture.py`, `scripts/m2_preflight.py`. `scripts/live_demo.py` is the sole
prospective live acquisition path.

Cohort registry: `cohort_registry/registry_v001.json`, revision 1, digest
`e1bf942ff8bfbdd2b9f4b92e448099142058805bc8d0213576db12d8a3689a7c`, all 45 sessions `state: planned`,
all subjects `label_state: sealed`.

**Test state.** Verified today: `tests/test_m2_cohort_registry.py` + `tests/test_m2_capture_artifacts.py`
= **131 passed**. A full-suite baseline run is in flight and its number is **not yet recorded**. The
"3139 passed" figure quoted in earlier notes was measured at `da3287d`, predates several commits, and
three countdown tests were red in between — treat it as unverified until the baseline lands.

## 3. Active task / next steps

Building `scripts/m2_scaffold_sidecar.py`, an operator tool that derives every mechanical
acquisition-sidecar field from the committed registry, prompts only for genuine measurements and
attestations, hashes the settle evidence, and emits the exact `live_demo.py` invocation. The plan is
`plans/m2_sidecar_scaffold.md` (revision 3; two independent plan reviews, the second returning
APPROVE WITH CHANGES with its blocker-class items resolved — **no third plan review is needed**).

In order:

1. Record the full-suite baseline. Invoke the suite **without** a shared `--basetemp`: the
   `test_m8_ahmed_provenance`, `test_m1_production_scoring` and `test_m4_preflight_strict` provenance
   tests build throwaway git repositories in temp dirs, and funnelling them through one temp parent
   produced 25 spurious failures. Plain `python -m pytest -q` under `conda run -n radar-vitals` is
   correct.
2. **Milestone A**: the no-behavior-change constants extraction in `src/m2/acquisition_metadata.py`
   (plan §4) — its own commit, its own independent code review.
3. Implement the tool (plan §6), write the tests (plan §8), then an independent code review of the diff.

Physical acquisition is separately ready whenever a participant is scheduled: follow
`notes/m2_capture_runbook.md` exactly. The scaffold tool is a convenience layer over that procedure,
not a precondition for it.

## 4. Recent decisions that matter

- **The natural/paced launch countdown is 30 s.** Do not "restore" 60. The code returned 1, then 30,
  while the runbook, a docstring, this file and three tests said 60 — they had never agreed. The
  countdown leaves no evidentiary footprint (absent from `run_metadata`, not a CLI argument, and
  `start_wall_utc` is stamped after it elapses) and is a **different quantity** from
  `settle_evidence_window_s`, which remains validated at exactly 60.0.
- **Operator work files live in the gitignored in-repo `m2_capture_work/`**, not outside the checkout.
  Reason: the repository is private and this is more convenient. Residual accepted: gitignored is not
  the same as outside, so a forced add or a repo archive would capture them.
- **Recovery `distance_m` is measured at seating, to the actual chest** — a seated-stage measurement,
  not a pre-exertion one. Accepted cost: tape-measuring consumes recovery ramp.
- **The scaffold tool will stamp the recovery seating epoch itself**, before the distance measurement,
  so the recorded delay is honest rather than flattering. Until that tool ships, the manual
  `$recoverySeatedStartUtc` procedure in the runbook stands and is correct.
- **`sit_to_record_delay_s` stays unbounded for now** — observe real values before setting a threshold.
- **The registry was deleted by commit `a5edecc` and restored at `e04ae16`.** A guard test now asserts
  it exists and that its digest sidecar matches, because the deletion previously surfaced as 68
  unrelated-looking `FileNotFoundError` failures.

## 5. Gotchas / landmines

- Exact command duration is 600 seconds; the canonical stream is exactly 12,000 frames at 20 Hz.
- Frame origin is the start-assignment event for frame index 0 — never `start_wall_utc`, never
  full-frame completion.
- Record signed `PC UTC - phone UTC` at both ends; each absolute value must be <= 1.0 s.
- Use automatic warmup bin selection. Never use a manual bin, replay, `--no-configure`, an alternate
  config, or any Masimo/reference value to choose DSP, alignment or a retry.
- Packet loss flags the session; the Boolean frame map decides which windows carry radar NaNs. It does
  not authorize a retry.
- Low warmup confidence, a missing reference, recovery adequacy or yield, radar coverage and agreement
  **never** authorize recapture.
- **A recovery Stage-1 failure cannot trigger recapture** (`notes/analysis_prespec.md:321`) and the
  session needs `>= 20.0` bpm PR range (`:314`). Time spent at the seating instant eats that range, so
  it can permanently forfeit a final-evaluation subject.
- Keep participant names, re-identification keys, screening answers, diagnoses, symptoms,
  medication/pregnancy data, consent/PIS and health narratives out of this repository entirely.
- `data/raw/` becomes read-only immediately after each no-overwrite promotion.
- Run Python through `conda run -n radar-vitals`. Invoking the environment's `python.exe` by absolute
  path crashes matplotlib `savefig` with exit 127 and no traceback.
- Nothing `scripts/live_demo.py` shows on screen, and nothing in `live_estimates.csv`, is paper-grade.
  Paper metrics are computed offline by re-processing the saved `adc_stream.bin`.

## 6. Pointers

| File | Purpose |
|---|---|
| `plans/m2_sidecar_scaffold.md` | current build target: sidecar scaffold tool, revision 3 |
| `plans/m2_engineering_acquisition_preflight.md` | reviewed M2 engineering contract |
| `notes/m2_capture_runbook.md` | exact physical acquisition and finalization procedure |
| `notes/protocol.md` | approved three-arm participant protocol |
| `notes/analysis_prespec.md` | cohorts, timing, validity, retry and dynamic-HR rules |
| `src/m2/acquisition_metadata.py` | sole authority on acquisition-sidecar validity |
| `cohort_registry/registry_v001.json` | P001-P015 cohort allocation, revision 1 |
| `templates/m2_acquisition_*.yaml` | arm-specific acquisition sidecars |
| `templates/m2_finalization.yaml` | post-capture/end-offset/reference record |
| `scripts/live_demo.py` | sole prospective live acquisition path |
| `scripts/m2_register_capture.py` | receipt-to-registry immutable revision command |
| `scripts/m2_finalize_capture.py` | reference binding and no-overwrite promotion command |
| `scripts/m2_preflight.py` | dry-run and real-manifest engineering validator |
| `HISTORY.md` | append-only evidence and decision log |
