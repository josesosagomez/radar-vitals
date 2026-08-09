# Handoff — resume here

> Read this and `CLAUDE.md` before doing anything. **State verified 2026-08-09.**
> `HISTORY.md` is append-only; this file is the current resume point.

## 1. Project snapshot

This project estimates HR/BR from a TI IWR1642BOOST + DCA1000 radar for a seated subject at
0.8–1.4 m. Masimo `Beats / min` aligned by integer Unix `Timestamp` is HR reference. The existing
eight captures span four subjects A–D, have approximate frame origin and insufficient HR dynamic
range, and remain development-only/exploratory.

## 2. Current state

Active branch: **`vital_signs_own_v13`**. Verified base commit: **`409ec05`**.

M0 of `plans/plan_codex_milestones.md` is **READY**.
The recovery contract received the required `task_breakdown` check and two independent
`plan_reviewer` design passes; the substantive verdict was **READY WITH MINOR CHANGES** and all
minor changes were incorporated. The owner removed the model-family-specific requirement from
`CLAUDE.md` §6; independent review remains required and is complete. Owner attestation records
recovery approval on 2026-08-03 under `24IBEC051`, covering exactly 15 new prospective participants
in addition to A–D. No estimator, DSP, config, data, test, capture, scoring run or experiment was
changed or executed.

The documentation changes are uncommitted. `plans/plan_codex_milestones.md` is an owner-provided
untracked authority file; preserve it. Do not stage or commit without explicit authorization.

## 3. Active task / next steps

M0 has no unresolved analysis-contract or authorization-metadata decision. The determination is
confidential and researcher/PI-held; consent/PIS records are private researcher–participant records
and are intentionally not stored in the repo.

The next milestone is M1, but **do not start it without an explicit user request**. M0 itself
authorizes no capture, estimator or DSP work.

## 4. Decisions that must not drift

- HR has separate natural, paced and recovery estimands/LoA. Never pool a three-arm headline.
- HR paced inference uses 12/15 bpm only; 18 bpm remains descriptive for HR and included for BR.
- Natural+paced floor stays separate: ≥1 jointly evaluable window/session, ≥4 across those two
  sessions/subject, ≥8/10 final subjects, plus the existing arm precision disposition. Recovery
  cannot rescue it.
- Recovery Stage 1 is reference-only: ≥10 reference-admitted windows, ≥20 bpm range across
  window `median_pr_bpm`, and a full-precision session-median constant predictor hitting strictly
  <50% at absolute error ≤5 bpm.
- Recovery Stage 2 requires ≥4 jointly evaluable windows and reuses the same Stage-1 constant at
  strictly <50%. It controls headline eligibility only; it never filters accuracy rows.
- Recovery headline requires ≥8/10 fixed final subjects passing both stages plus existing
  estimability/bootstrap/precision rules.
- Existing A–D are `development`. First five eligible prospective slots are
  `representation_validation`; next ten are `final_evaluation`. Every session from a subject has
  one immutable role; no correlated sessions cross roles.
- Validation paced allocation is 2/2/1; final is 4/3/3. The approved 15-person prospective ceiling
  is fully allocated, so a withdrawal leaves a missing slot; no additional participant replacement
  is assumed.
- No recapture for recovery inadequacy, low radar yield/coverage, agreement, or low warmup
  confidence. Only objective technical admission failures may permit recapture before scoring.
- Every complete source window and expected estimator/vital/config key gets a ledger row. Missing,
  exceptional or invalid estimator output becomes radar-NaN.

## 5. Gotchas / landmines

- The recovery determination and consent/PIS records are intentionally private. Do not request that
  participant-level consent records be committed or exposed; cite the owner attestation and parent
  reference `24IBEC051` in internal provenance.
- The current scorer/manifest still encodes the old two-arm roles. Its M4 plan is now explicitly
  superseded; updating code is later work and was not authorized by M0.
- Five validation subjects are a feasibility floor, not powered population validation. State the
  small-cluster limitation.
- Final Stage 1 and scoring are one fail-closed M5 transaction; Stage-1 values are not exposed to the
  estimator team before final artifacts are sealed.
- The fixed grid, Masimo comparator, `Beats / min` reference, integer `Timestamp`, PI gate and raw
  intermediate-evidence requirements remain unchanged.

## 6. Pointers

| File | Purpose |
|---|---|
| `plans/plan_codex_milestones.md` | owner-designated authoritative recovery plan |
| `plans/m0_recovery_contract_cross_review.md` | task-breakdown and independent plan-review record |
| `notes/analysis_prespec.md` | binding three-arm estimands, floors, roles and ledger contract |
| `notes/protocol.md` | capture procedure, cohort slots and authorization gate |
| `notes/capture_inventory.md` | A–D development inventory and E/F/G validation-role reconciliation |
| `notes/comparator_prespec.md` | unchanged HR reference/admissibility rules |
| `plans/m4_offline_harness.md` | historical scorer plan with mandatory M0 supersession notice |
| `HISTORY.md` | durable session record and M0 review outcome |
