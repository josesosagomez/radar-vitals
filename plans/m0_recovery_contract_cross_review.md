# M0 recovery analysis-contract review

**Date:** 2026-08-09  
**Scope:** documentation/planning only; no capture, estimator, DSP, scoring run or experiment  
**Authority:** `plans/plan_codex_milestones.md`, designated by the owner as the authoritative
recovery plan for this task

## Reviewed sources

- `CLAUDE.md`
- `plans/plan_codex_milestones.md`
- `notes/analysis_prespec.md`
- `notes/protocol.md`
- `notes/capture_inventory.md`
- `notes/comparator_prespec.md`
- `plans/m4_offline_harness.md`
- `notes/dca1000_protocol.md`
- `notes/ethics_amendment_hr_recovery.md`

## Task-breakdown check

The required read-only `task_breakdown` review found the draft direction scientifically coherent
but initially not ready. Its material findings were:

1. replace the two-arm contract with three separate natural/paced/recovery estimands;
2. keep the natural+paced floor separate from a recovery-specific two-stage floor;
3. prevent reference adequacy or radar yield from becoming a recapture lever;
4. make subject roles immutable, hash-bound and subject-disjoint;
5. preserve ten final subjects and add a separate representation-validation cohort;
6. distinguish recorded approval assertions from an authorization document actually held;
7. define constant-baseline, paced allocation and claim-status wording exactly.

All seven were incorporated into the review draft.

## Independent plan review

The required read-only `plan_reviewer` performed two passes.

### Pass 1 — NOT READY

Blocking findings:

- Stage 2 could be misread as selecting recovery accuracy rows;
- the constant-predictor baseline was not executable at even-count and 50% boundaries;
- limiting edits to the prespec/protocol would leave stale authorization claims in governing docs.

High findings:

- cohort assignment and label-opening state were underspecified;
- the canonical window ledger lacked primary keys and an expected Cartesian product;
- the old M4 two-arm plan required a mandatory supersession notice.

Medium finding:

- the prospective low-warmup-confidence retry had to be explicitly retired.

The draft was revised to resolve every finding.

### Pass 2 — READY WITH MINOR CHANGES

No scientific blocker remained. Three minor changes were required:

1. a withdrawal replacement inherits the vacated cohort/rate slot and is allowed only when the
   approved enrollment maximum covers it;
2. representation validation gets its own label-state branch, and final Stage 1 plus scoring is
   one fail-closed transaction whose Stage-1 values are not exposed early;
3. empty Stage-1/Stage-2 sets fail count gates before median/range/division and emit null, never
   NaN/Inf.

All three are incorporated in `notes/analysis_prespec.md` and `notes/protocol.md`.

**Final independent plan-review verdict: READY WITH MINOR CHANGES — changes incorporated.**

This record documents an independent `plan_reviewer` role. It does not assert a reviewer model
family that the tool did not expose; any separate other-family sign-off required by `CLAUDE.md`
§6 remains an owner-visible governance item.

### Actual-file conformance pass — FAIL on governance status

After the documentation edits, the same reviewer verified that all substantive contract terms and
the documentation-only scope conformed. It returned **FAIL** on one status contradiction: files
called M0 complete while this record could not establish the other-model-family sign-off required
by `CLAUDE.md` §6. The status was corrected everywhere. The other-family review is now an explicit
M0 blocker rather than an implied completion.

The reviewer then re-checked the corrected status lines and returned **PASS**.

### Owner rule change after review

The owner decided on 2026-08-09 that this project will not require review by a specified model
family. `CLAUDE.md` §6 now requires independent plan, code and math/claims review without a
model-family condition. `AGENTS.md` was reconciled to the same single-source rule. The independent
review recorded above therefore discharges the current review requirement.

The owner then attested that the recovery amendment was approved on 2026-08-03 under parent
reference `24IBEC051`; the approval covers exactly 15 new prospective participants in addition to
development subjects A–D. The determination is confidential and held by the researcher/PI, while
consent/PIS records are private between the researcher and participants. They are intentionally not
repository artifacts. Because the 5-validation/10-final design uses all 15 approved participants,
no additional participant replacement is assumed.

**Overall M0 verdict: READY.**

After the owner's final authorization/privacy and 15-participant clarification was incorporated,
the independent reviewer performed a final conformance check and returned **PASS**.

## Accepted M0 contract

- Separate HR estimands and LoA for natural, paced and recovery; no pooled three-arm headline.
- HR paced inference includes 12/15 bpm only; 18 bpm remains descriptive for HR and included for BR.
- Natural+paced floor: at least one jointly evaluable window per session, at least four across those
  two sessions per subject, at least 8/10 final subjects, and the existing arm precision rule.
- Recovery Stage 1: at least ten reference-admitted windows, at least 20 bpm window-median PR range,
  and a full-precision session-median constant predictor with hit rate strictly below 50% at
  absolute error at most 5 bpm.
- Recovery Stage 2: at least four jointly evaluable windows and the same fixed Stage-1 constant
  again below 50%; Stage 2 controls headline eligibility only and never selects accuracy rows.
- Recovery headline: at least 8/10 fixed final subjects pass both stages, plus the existing
  estimability/bootstrap/precision rules.
- Existing A–D are development-only; first five prospective slots are representation validation;
  next ten are final evaluation. All sessions from one subject have one immutable role.
- Cohort scope: exactly 15 new prospective people in addition to A–D, giving at least 19 unique
  subjects total. Final paced allocation is 4/3/3; validation is 2/2/1. No additional participant
  replacement is authorized by this contract.
- Every complete source window and expected estimator/vital/config combination has a ledger row;
  missing/invalid/exceptional output becomes radar-NaN rather than a missing row.
- No recapture for recovery inadequacy, low joint yield, radar coverage, agreement or low warmup
  confidence. Only objective technical admission failures may permit recapture before scoring.

## Authorization and privacy disposition

- Parent approval reference: `24IBEC051`.
- Recovery amendment approval date: 2026-08-03 (owner attestation 2026-08-09).
- Issuing body label in the project record: `IBEC, KAUST`.
- Approved enrollment: exactly 15 new prospective participants in addition to A–D.
- Determination: confidential, researcher/PI-held, intentionally not committed.
- Consent/PIS records: private researcher–participant records, intentionally not committed.
- Participant replacements beyond the approved 15: not authorized or assumed.

No later milestone is authorized by this review.
