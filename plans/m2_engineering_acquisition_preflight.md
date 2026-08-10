# M2 engineering and acquisition preflight plan

> **Scope:** M2 engineering only. This plan prepares prospective acquisition and then stops before
> any participant capture. It does not change the HR estimator, its settings, range-bin selection,
> the comparator, or any M3 representation. It is governed by `plans/plan_codex_milestones.md`,
> `notes/analysis_prespec.md`, and `notes/protocol.md`; if this plan conflicts with one of them, the
> authoritative file wins.

## 1. Objective and stop boundary

M2 engineering is complete only when a synthetic scoring-mode capture manifest, the live-capture
artifact producer, the fixed time-offset sensitivity, and the subject-level cohort registry all
fail closed under the approved protocol. No physical capture or real reference inspection is part
of the engineering preflight. After review and tests pass, stop and give the operator the exact
physical-capture procedure. New captures are validated in this same chat only after the researcher
returns with them.

## 2. Frozen engineering decisions

1. **Manifest version:** prospective study manifests use schema version 3. Historical version 2
   remains readable under its existing semantics and tests through explicit version dispatch (or a
   separate v2 module); it is never reinterpreted by v3 enums or rules.
2. **Roles and arms:** arms are `natural`, `paced`, and `recovery`. Roles are `development`,
   `engineering_only`, `representation_validation`, and `final_evaluation`. Only the last two are
   scoring-loadable. Existing A-D data remain development-only.
3. **Frame origin:** `frame0_epoch` is the synchronised-PC UTC timestamp recorded at the observable
   instant the first received payload byte—or leading zero-fill byte—is assigned to frame index 0,
   exactly as `notes/analysis_prespec.md` §7 permits. Persist the timestamp and source string
   `frame_index_0_start_assignment_pc_utc`; do not use the later full-frame completion/emission time,
   infer an earlier sensing time, back-correct leading loss, or substitute `start_wall_utc`. Leading
   or later packet loss affects the validity map, not this observed event timestamp. A regression
   distinguishes this start assignment from completion roughly one frame period later.
4. **Clock gate:** start and end PC-phone offsets are measured externally against one common NTP
   source and entered in seconds. Each must satisfy `abs(offset) <= 1.0`; equality passes. The
   capture tool does not infer or optimize an offset.
5. **Time sensitivity:** the fixed sensitivity grid is `[-1.0, 0.0, +1.0]` seconds added to the
   primary frame origin. A reference-only artifact is produced under the Stage-1 firewall and a
   later agreement artifact is produced only inside allowed scoring. Both are labelled sensitivity
   and neither chooses or replaces the measured primary alignment.
6. **Frame validity:** a persisted one-dimensional Boolean `.npy` has exactly one entry per stored
   complete frame; `True` means every byte came from received ADC payload, `False` means at least one
   injected zero-fill byte contributed. `n_invalid_frames` is derived from it. Any complete
   600-frame window containing `False` is radar-NaN but remains in the ledger.
7. **Packet loss:** persist received and dropped packet counts. `dropped / received > 0.05` is a
   reported session flag, not an exclusion; the validity map governs affected windows. Equality at
   5% is unflagged.
8. **Retry policy:** only objective protocol-abort, corrupt-raw, and epoch-sync failures may permit
   a same-subject recapture, before any subject data are scored. Low warmup confidence, missing
   reference, recovery adequacy/yield, radar coverage, and agreement never permit retry.
9. **Recovery privacy:** repository metadata records only positive operational attestations and
   measurements. Screening/clearance, current amended materials/consent and researcher presence are
   confirmations; no PAR-Q+ answers, eligibility failures, diagnoses, participant names, consent
   forms or health records enter the repository.
10. **Cohort identity:** preallocate pseudonymous prospective IDs `P001`-`P015` by enrolment order.
    `P001`-`P005` are representation validation with paced rates `12/15/18/12/15`; `P006`-`P015`
    are final evaluation with paced rates `12/15/18/12/15/18/12/15/18/12`. Role and slot never
    change. A later registry revision is a new immutable file; it may append
    assignment/session/hash/state information only, must bind the SHA-256 of the exact previous
    file bytes, and must not overwrite an earlier revision.
11. **Two-phase acquisition:** the live producer emits a sealed radar capture receipt only. After
    the Masimo export and the end clock measurement exist, a separate fail-closed finalizer binds
    the reference-acquisition record and promotes immutable raw artifacts without overwriting, then
    emits the scoring manifest. No shutdown-time record is falsely called scoring-complete.
12. **Synthetic isolation:** engineering tests use temporary fixture IDs and registries. They never
    add synthetic sessions or opened labels to the real `P001`-`P015` registry, and
    `engineering_only` remains non-scoring.

## 3. Milestone A - prospective schema and cohort contract

Preserve `src/m4/manifest.py` as the historical v2 implementation and add a separate prospective v3
model plus a narrow version dispatcher. Natural and paced
records require the numeric 60-second settle evidence. Recovery instead requires the protocol's
post-exertion start confirmations (seated, still, hands resting), plus operational recovery fields;
it must not fabricate stable-PR spread/drift values. All arms require posture `seated`, tape-measured
distance in `[0.8, 1.4]` metres, exact timebase/integrity/provenance fields, intended/actual duration,
reference-acquisition status, and required fixed-condition metadata.

Add a small cohort-registry module and an initial committed registry document. It validates:

- the exact five/ten role split and paced-rate rotations;
- unique canonical subject IDs, roles and slots;
- subject-disjoint sessions and one immutable role per subject;
- label states `sealed -> stage1_reference_only -> validation_opened` or
  `sealed -> stage1_reference_only -> final_scored`;
- role-membership SHA-256 identities;
- append-only revision semantics and previous-registry hash chaining.

Registry files use canonical UTF-8 JSON bytes produced with sorted keys, compact separators,
`ensure_ascii=false`, `allow_nan=false`, and exactly one trailing newline. The digest is the SHA-256
of those exact bytes and is stored outside the document to avoid circular hashing. Revision `n`
contains revision `n-1`'s exact-file digest. Role-membership hashes cover canonical JSON arrays of
subject IDs in cohort-slot order. An optional current pointer has no authority; validation walks
every immutable revision from revision 1.

The registry also materializes all three expected session slots for every prospective subject.
States include `planned`, `captured`, and controlled nonmedical non-acquisition dispositions
`withdrawn`, `recovery_not_cleared`, `missing`, and `technical_not_acquired`. Recovery ineligibility,
withdrawal, or a missing session remains a visible fixed-denominator outcome; it is not rejected
from the registry or silently removed because no scoring manifest exists.

Acceptance tests reject stale roles, omission of an expected recovery **slot**, warmup retries,
recovery stable-settle fabrication, role/slot mutation, cross-role sessions, label-state skips,
altered/deleted/reordered historical revisions, and a wrong predecessor digest. A materialized
controlled non-acquisition outcome is valid and remains in the denominator; no underlying health
reason is stored.

Version 3 is implemented separately from the existing v2 model, with a narrow dispatcher selecting
by document version. All current v2 fixtures and M1 behavior must pass unchanged. V3 recovery,
roles, metadata and retry rules cannot be constructed through or silently applied to the v2 API.

### Label-access firewall

The registry is not merely descriptive. Every project consumer that opens prospective Masimo
content uses one guarded loader and an immutable access-transition artifact:

- `sealed`: reference contents cannot be opened; retry records may still be created only under the
  objective rule below;
- `stage1_reference_only`: only the allow-listed recovery Stage-1/reference-time-sensitivity
  transaction may read the reference; it cannot import radar samples, radar validity, estimates or
  agreement and does not expose final Stage-1 values to the estimator team;
- `validation_opened`: only representation-validation subjects may enter declared validation
  scoring;
- `final_scored`: reached from `stage1_reference_only` by one atomic final transaction bound to
  already committed scorer/config/cohort hashes; no repair, retry, role or parameter mutation is
  possible inside or after it.

Each transition is a new immutable audit JSON bound to the prior registry/audit digest, UTC time,
clean commit, config/scorer hashes and permitted operation. Tests call the guarded loader at every
state and prove forbidden reads fail before file contents are returned. Direct unguarded prospective
reference loading is forbidden in M2/M4 scoring entry points.

## 4. Milestone B - acquisition producer and artifacts

Extend `scripts/live_demo.py` and its artifact verifier without touching the estimator path:

- accept one validated acquisition sidecar rather than reconstructing study metadata from the
  legacy local CSV;
- require a clean capture commit for prospective live mode and record the commit plus dirty flag;
- copy and hash the exact YAML bytes and separately serialize/hash the canonical effective
  configuration plus exact CLI invocation, so runtime choices are bound;
- hash the final raw mirror;
- persist the frame-index-0 assembly UTC, packet counts, stored-frame count, trailing truncation
  bytes, the Boolean validity map and its hash;
- persist the start PC-phone offset and planned/protocol metadata in the sealed radar receipt;
- after capture, finalize with the separately measured end offset and Masimo export/acquisition
  evidence, promote immutable artifacts with no-overwrite semantics, and only then emit a version-3
  session manifest directly loadable by the scoring validator;
- preserve `start_wall_utc` only as descriptive process timing.

Validity is computed from ordered absolute stream-byte intervals. Every missing-packet interval is
zero-filled in the mirror and marks every overlapping assembled frame invalid. Duplicate or late
out-of-order packets remain discarded and cannot turn an invalid interval valid. A trailing partial
frame is not represented as a complete frame.

Packet counters have fixed meanings: `packets_received` counts accepted, in-order payload packets;
`packets_dropped` is the sum of positive sequence-number gaps; short and duplicate/late packets have
separate discard counters. When `packets_received == 0`, loss ratio is null (never NaN/Inf), the
capture has no exact usable radar stream, and finalization cannot declare it admitted.
The 5% flag uses integer arithmetic, `20 * packets_dropped > packets_received`, so equality is
unambiguously unflagged.

Prospective study mode additionally enforces the capture contract at the CLI boundary: duration is
exactly 600 seconds and never greater; warmup automatic bin selection is mandatory; manual
`--locked-bin`, `--no-configure`, replay, and study-config overrides are rejected. Engineering
fixtures use a separately named dry-run mode and cannot be promoted.

Required sidecar metadata and canonical units/types are:

- identity: string `subject_id`, exact integer `cohort_slot`, role enum, session ID, arm enum,
  exact integer `visit_number` (1/2/3), UTC visit date/time, and expected reference basename;
- design: `posture="seated"`; Booleans for back straight, both hands on thighs, facing radar,
  radar at chest height, radar face horizontal, phone-level check passed, sensored hand still;
  tape-measured numeric `distance_m` in `[0.8,1.4]`;
- scene/equipment: controlled scene-description category plus non-health free text, Boolean
  scene-changed flag, quiet/no-walk/no-fan confirmations, radar/DCA readiness, exact configured
  `32` chirps/frame and `20 Hz`, Masimo battery/logging/PR/PI readiness;
- common acquisition: numeric start offset seconds, intended duration `600.0 s`, session order,
  disturbances category, and final measured PR; end offset and actual duration enter finalization;
- natural: settle duration seconds, start PR bpm, 60-second PR spread bpm and first-vs-last-20-second
  drift bpm with bound evidence;
- paced: all natural settle fields plus exact commanded rate `12/15/18 bpm`, metronome rate exactly
  twice it, at least 120 seconds paced settle, at least 60 seconds BR stability confirmation,
  resting PR bpm, and derived `abs(resting_pr_bpm - 4*commanded_rate_bpm)`;
- recovery: no stable-settle spread/drift fields; private-clearance-recorded confirmation, amended
  materials/current consent confirmation, researcher-present confirmation, step-ups modality,
  suitable-footwear/dry-unobstructed-area confirmations, pre-exertion resting PR,
  `exertion_stop_pr_bpm` in `[100,120]`, exertion duration seconds, seated PR at `t=0`,
  seated/still/hands-resting
  confirmations, sit-to-record delay seconds, no-pacing confirmation, stopping-event category,
  numeric `post_monitoring_pr_bpm`, and monitoring duration seconds. The finalizer derives and
  requires `abs(post_monitoring_pr_bpm - pre_exertion_resting_pr_bpm) <= 5`; safety/participant-stop
  categories remain controlled and contain no symptom narrative.

No PAR-Q+ answers, condition/medication/pregnancy fields, names, consent documents or paths, health
free text, or filenames containing participant names are stored. Pseudonymous basenames are
validated. An acquired recovery session stores only positive operational attestations
(`screening_completed` and `recovery_clearance_attested`). A non-captured recovery slot is simply
one of the controlled non-acquisition dispositions without an underlying medical reason. The
re-identification key remains outside the repository.

## 5. Milestone C - time sensitivity and dry-run preflight

Implement two pure sensitivity stages on the fixed non-overlapping grid and the three frozen shifts:

1. a reference-only Stage-1-safe artifact reporting reference admission and summaries at every
   shift, with no radar input; and
2. a later scoring artifact that applies the same three shifts to already frozen radar rows and
   reports agreement sensitivity without changing the primary origin, admission decision, estimator,
   or selected result.

Neither stage contains a `best`, `selected`, optimizer, or promotion field. Final labels remain
sealed until their allowed transaction. Synthetic tests pin integer-second half-open boundaries and
prove the reference-only stage cannot import radar estimates or agreement.

The sign convention is `shifted_frame0_epoch = primary_frame0_epoch + shift_s`. Every per-shift
reference artifact records `shift_s`, `primary_shift_s=0`, each complete window's half-open epoch
bounds, HR/BR admission and fixed reason, comparator sample counts and medians, null-safe totals,
input/config hashes, and—for recovery HR—Stage-1 `n`, range, `c_s`, hit rate and status. The later
agreement artifact records the same shift and ledger identities plus joint count, coverage,
MAE/RMSE/bias, HR severe-error count, and, when executable, arm-specific LoA output. All shifts are
always emitted.

Add a preflight CLI that validates, in order:

1. clean capture commit and exact config identity;
2. cohort registry hash and subject/slot/role membership;
3. arm-specific acquisition metadata and clock offsets;
4. raw/map/config/reference-acquisition bindings and SHA-256 values;
5. packet counts, map length/count, frame origin and duration consistency;
6. scoring-mode version-3 manifest loading;
7. fixed time-sensitivity artifact shape when reference data exist.

The engineering dry run uses synthetic packet bytes, synthetic reference data, and an isolated
temporary representation-validation registry only. It must produce a self-contained artifact
directory, load in scoring mode, identify exactly which 600-frame windows are invalid, materialize
those source windows as radar-NaN through the scoring wrapper (not only validate the map), and fail
after any one-byte change to raw, config, validity map, metadata, or registry. The real registry
remains sealed and contains no synthetic session.

### Exact retry predicates

A retry record requires `label_state == sealed`, symmetric predecessor/replacement links, and exact
identity equality for subject, role, slot, arm, commanded rate and protocol/config identity. The only
evidence predicates are:

- §6 item 3: the normal-arm settle gate failed before capture, or an intentional run ended before
  its intended 600-second end;
- §6 item 4: stored raw checksum failure, or raw truncation cutting a non-final complete-window
  region under the frozen corruption test;
- §6 item 5: either measured start/end PC-phone offset exceeded 1 second.

Packet-loss flags, zero-filled/invalid frames, an incomplete final tail after the intended end,
missing reference, warmup confidence, recovery Stage-1/2 status, radar yield and agreement are never
retry causes. Every attempt is retained. Participant-requested withdrawal never triggers an
automatic retry; any later attempt requires renewed participant direction outside the software and
must still satisfy the technical/state contract. No retry-count cap is invented.

## 6. Test and review order

1. `python_expert` implements the scoped acquisition/preflight code.
2. `test_engineer` independently adds/strengthens deterministic unit, integration and failure-path
   tests, including leading/middle/boundary packet gaps, exact clock thresholds, arm-specific settle
   rules, protocol metadata, fixed sensitivity offsets, registry mutation, and hash tampering.
3. Run focused tests, then the full repository suite in the pinned `radar-vitals` environment.
4. `code_reviewer` independently reviews the final diff for protocol fidelity, provenance,
   scientific leakage, regressions, and accidental estimator/M3 changes. Resolve and record every
   disagreement.
5. Append `HISTORY.md`, then rewrite `HANDOFF.md` after evidence is final.

## 7. Engineering PASS criteria

M2 engineering preflight passes only if all of the following are observed, not assumed:

- a complete synthetic version-3 scoring manifest validates and every missing/invalid required field
  fails with a named error;
- exact frame-index-0 start-assignment origin and its named event evidence are persisted;
- both PC-phone offsets enforce the inclusive +/-1-second rule;
- packet counts and the per-frame map agree with the stored raw frame count;
- packet loss is flagged at the frozen threshold and invalid windows become radar-NaN without being
  omitted;
- raw/config/map/metadata/registry identities are SHA-256 bound and the capture commit is recorded;
- all required fixed-condition, posture, distance, arm-specific and approved recovery metadata are
  machine-validated without storing confidential documents;
- the primary alignment and reference-only `-1/0/+1 s` stage are deterministic and
  agreement-blind; the later fixed agreement sensitivity runs only inside allowed scoring and
  cannot select or replace alignment;
- cohort IDs, roles, slots, rotations, label states and revision hashes are subject-disjoint and
  immutable, and missing fixed-slot recovery sessions remain materialized;
- focused and full tests pass; independent review has no unresolved blocker;
- the HR estimator and M3 representations are byte-for-byte untouched by the diff.

The objective commands are:

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals --no-capture-output `
  python -m pytest tests/test_m2_manifest.py tests/test_m2_cohort_registry.py `
  tests/test_m2_capture_artifacts.py tests/test_m2_time_sensitivity.py `
  tests/test_m2_label_firewall.py tests/test_m4_manifest.py -q

C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals --no-capture-output `
  python -m pytest -q
```

Milestone-A tests include v2 compatibility; Milestone-B packet/assembly tests are pure and require
no hardware; Milestone-C end-to-end tests use temporary synthetic artifacts only. Engineering
preflight must not invoke live hardware, read a real prospective reference, or write `data/raw/`.

This is an **engineering** verdict only. Overall M2 remains **FAIL / pending acquisition** until the
new physical captures themselves pass timing, provenance, frame-validity, metadata, subject-disjoint
cohort, and approved dynamic-HR protocol checks.
