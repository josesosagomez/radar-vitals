# Discrepancy remediation plan — 2026-09-29

**Status:** revision 3 after independent plan review. Revision 2 was judged **READY WITH MINOR
CHANGES** for Milestone 0 and Milestone 1 items 1–4. This revision incorporates those changes. The
four prepared-work-directory dispositions remain an explicit owner-input blocker for closing item
1.5, but do not block items 1.1–1.4 or the later independent code work.

**Authority:** `CLAUDE.md`; owner decisions in `HISTORY.md` 2026-09-29; evidence and fix order in
`reports/discrepancy_audit_2026-09-29.md`. Integration target is `vital_signs_own_v13`.

## Invariants

- Never open a sealed prospective reference CSV while building or testing these fixes.
- Never modify `data/raw/` or immutable capture artifacts.
- P001–P006 natural remain recorded 60 s protocol deviations; no metadata is rewritten.
- Static clutter removal stays off; the 2026-07-30 result is not rerun or reinterpreted.
- Every changed estimator/scorer leaves its intermediate evidence and carries a new or explicit
  semantic label; historical artifacts and HISTORY entries are never rewritten.
- Existing uncommitted audit and MATLAB work is preserved.

## Milestone 0 — clean, reproducible implementation boundary

1. Work in a clean isolated worktree created from `vital_signs_own_v13`, leaving the current
   uncommitted audit/MATLAB work untouched.
2. Verify the managed worktree's tracked files are LF before any scientific measurement or
   provenance artifact. The 20 CRLF files and mixed-ending `HISTORY.md` were a property of the
   original dirty checkout; this clean checkout already materialized the committed blobs as LF.
   Do not mechanically rewrite files that are already normalized. Preserve the original checkout
   and import only the authoritative audit, plan, owner decisions, and current handoff.
3. Add a tracked-text LF preflight test. Verify:

   ```powershell
   git ls-files --eol
   git diff --check
   C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python -m pytest -q tests/test_m8_ahmed_provenance.py tests/test_m8_ahmed_fig8.py
   ```

4. At the milestone checkpoint, append HISTORY and rewrite HANDOFF in that order.

**Acceptance:** every tracked text path reports `w/lf`; the worktree is clean before any gate or
real-data diagnostic; M8 provenance tests remain green.

## Milestone 1 — capture boundary and reference firewall

1. Port the intended physical-protocol constants from `m2_sidecar_scaffold` into current v13
   without merging the divergent branch. Split two contracts that currently share one validator:
   - **new capture admission:** natural/paced `<120 s` is rejected;
   - **historical immutable manifest loading:** existing 60 s v3 records remain structurally
     loadable but derive `protocol_compliant=false` with reason `settle_below_120s`.

   Primary scorers must exclude that disposition. A separately requested deviation-inclusive
   sensitivity may retain it only with an explicit label. Recovery remains exempt. Add tests for
   119.999 rejection, 120 acceptance, recovery, synthetic historical 60 s loading, and primary
   exclusion. Compatibility checks against real manifests, if run at all, must use a metadata-only
   path that cannot resolve, hash, or open the reference binding, with a fail-before-open assertion.
2. Add `src/reference_access.py` as the only public reference discovery/loading entry point. Its
   return value is a parsed Masimo DataFrame plus immutable source identity/hash metadata.
   Development authority comes only from a committed capture registry mapping identity to expected
   resolved path and SHA-256; callers cannot nominate an allowlist root. Prospective reads accept
   only a capability minted by the existing atomic transition, delegate the first byte access to
   `guarded_reference_bytes`, and parse those returned bytes in memory without reopening the path.
   Canonical resolution must reject symlink/junction escapes and protected path components.
3. Route every production Masimo open—not only the eight audited glob families—through the guarded
   entry point. Batch discovery must exclude prospective sessions. An audit test fails on any
   production `load_masimo(path)`, `pandas.read_csv` reference read or CSV discovery outside the
   approved boundary. Entry-point tests use a synthetic `P001_natural/reference.csv` and assert
   failure before open.
4. Change MATLAB documentation examples to development data. Make the Python TI-config generator
   reject prospective paths by default, with an explicit radar-only operational purpose flag rather
   than a method-development escape hatch.
5. Validate evidence basename against `session_id` during sidecar/live preflight before capture,
   then repeat at sealing and registration. Add an append-only attempt ledger that records attempt
   stage (`prepared_only`, `started_aborted`, or `completed_unpromoted`), termination/disposition
   reason, supporting evidence, whether recapture is permitted, and the exact policy reason that
   authorizes or forbids recapture. Attempt stage alone never decides retry eligibility. Owner
   disposition is required for P003 paced, P007 natural, P009 natural and P010 natural before this
   item can close. Do not infer state or read their CSVs.

**Validation:** 

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python -m pytest -q tests/test_m2_acquisition_metadata.py tests/test_m2_manifest.py tests/test_m2_cohort_registry.py tests/test_m2_capture_artifacts.py tests/test_m2_label_firewall.py tests/test_m4_preflight_strict.py tests/test_reference_access.py tests/test_score_offline.py
```

**Acceptance:** no production reference read bypass remains; a new 60 s capture is rejected;
synthetic 60 s historical manifests load with a machine-readable deviation and cannot enter primary
scoring. No compatibility test opens a real prospective reference. Append HISTORY and rewrite
HANDOFF at the checkpoint.

## Milestone 2 — estimator and scoring correctness

1. Before changing behavior, add a committed dual-calculation radar-only diagnostic over development
   inputs that records call site/candidate, legacy raw delta, safe delta, fallback reason and
   potential bpm difference. Then replace unsafe refinement with a structured-result safe helper:
   uniform finite frequency grid, full-spectrum strict local maximum, concave finite parabola,
   `abs(delta) <= 0.5`, and in-band result; boundary, plateau, NaN/Inf or failure returns the bin
   centre with a reason. Persist that evidence wherever an estimate is emitted.

   The corrected behavior receives a new immutable estimator ID; historical
   `production_eca_ahet_v1` artifacts remain unchanged. The corrected ID was frozen on
   2026-09-29 as **`eca_ahet_safe_refine_v2`** before any changed output was assigned.
2. Preserve current ECA behavior and numbers but correct its semantic description: in-band ECA is
   inactive under production mode. Add an explicit no-ECA comparison mode and reference-free tests
   pinning projected harmonic orders. Do not promote `guard_cardiac_candidate_v1`.
3. Enforce comparator admission at every scoring boundary. Excluded references become non-numeric
   to metric code. Fix `score_offline` supplementary comparisons, `simulate_bin_policy` admission
   and frame-zero origin, and legacy M8's `k >= 1` universe plus guarded discovery.
4. Make the Step-6 tracker a separate non-causal estimator. Preserve all AHET fields and emit a
   complete tracker contract: bpm, validity, confidence/source, invalid reason, error fields,
   selected rank and `non_causal=true`. Keep prior Viterbi segments on restart. Produce separately
   named AHET and tracker summaries; downstream diagnostics may not use ambiguous generic MAE,
   coverage or `hr_valid` for the tracker.

**Validation:**

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python -m pytest -q tests/test_vitals.py tests/test_eca_ahet.py tests/test_score_offline.py tests/test_m1_production_scoring.py tests/test_m8_ahmed_score.py tests/test_step6_heart_rate.py tests/test_temporal_tracker.py
```

**Acceptance:** focused suites pass; independent code review finds no blocker; AHET rows are
immutable under tracking; all changed estimates retain diagnostic evidence. Append HISTORY and
rewrite HANDOFF at the checkpoint.

## Milestone 3 — repeated-measures agreement

1. Add `src/agreement.py::arm_loa` with an explicit per-window contract: arm, subject ID, session ID,
   within-session order, radar bpm, reference bpm, difference and pair mean. Reject mixed arms,
   unknown subject identity and nonfinite admitted values; never substitute capture ID for subject.
   Output counts, estimability status, descriptive fields, `MSW`, `MSB`, `n0`, variance components,
   LoA, endpoint CIs, failed-bootstrap count/fraction, and diagnostics. Serialized non-estimable
   fields are `None`, never NaN/Inf.
2. Implement the exact unbalanced one-way ANOVA estimator and 10,000-replicate subject-cluster
   bootstrap with seed 20260725 and explicit linear percentiles. Duplicate sampled subjects become
   distinct bootstrap clusters. Test the exact `>5%` failure boundary.
3. Implement every mandatory diagnostic in the analysis spec: proportional bias, residual-spread
   trend, residual-tail/QQ summary, within-session lag-1 residual autocorrelation, and the always-
   reported subject-clustered regression sensitivity. Use only pinned NumPy/SciPy primitives unless
   a separately reviewed dependency change is approved.
4. Rename M9 pooled-window outputs as descriptive and remove the claim that they are population
   Bland–Altman LoA. Route canonical arm summaries through `arm_loa` only when subject identity and
   arm are available.

**Validation:**

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python -m pytest -q tests/test_agreement.py tests/test_m9_kotte_score.py
```

**Acceptance:** independent math/claims review reproduces the hand example; tests are deterministic;
no manuscript calls pooled-window `bias ± 1.96 SD` a population LoA. Append HISTORY and rewrite
HANDOFF at the checkpoint.

## Milestone 4 — records, manuscripts and provenance hygiene

1. Withdraw the unsupported pilot table from `JOURNAL_PAPER.md` and `THIRD_CHAPTER.md`; leave a
   precise pending-result statement. Do not substitute another unreviewed table.
2. Add Ahmed as `[R22]` in both reference lists and point only Ahmed harmonic-accumulation citations
   to it. Keep Tang ECA+AHET as `[R1]`. Check reference-number closure.
3. Correct ECA descriptions and disclose that inherited heart gates were selected against Masimo on
   deleted development data. Label the frozen estimator reference-informed/legacy; do not retune.
4. Add the approved-scope header note to the ethics amendment without editing its as-submitted body.
5. Append capture-session and prepared-attempt records to HISTORY using manifests/filenames only;
   do not expose label values. Rewrite HANDOFF to the verified post-fix state.
6. Add executable citation-closure and forbidden-claim checks.

**Validation:**

```powershell
git diff --check
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python -m pytest -q tests/test_documentation_claims.py tests/test_m8_ahmed_provenance.py
```

**Acceptance:** citation closure passes and documentation contains no superseded headline claim.
Append HISTORY and rewrite HANDOFF at the checkpoint.

## Milestone 5 — recomputation and final verification

1. Recompute only after Milestones 0–4 pass review and are committed in a clean checkout. The new
   estimator ID requires a complete new immutable chain: corrected-source synthetic gate, new
   authorization filename, reference-blind radar parent, then scoring. Never reuse or rewrite the
   existing M1 gate, authorization, radar parent, estimator identity or `LATEST` pointer. Require
   explicit `--gate`, `--authorization` and `--radar-parent` arguments, each digest-checked.
2. Supersede, never rewrite, affected M1, 2026-08-04 bin-policy and legacy M8/M9 outputs. Record
   script, config, seed, clean commit, estimator ID, authorization/gate/radar-parent hashes and input
   hashes. The exact commit/transition sequence is part of the authorization record.
3. Run focused tests after each milestone and the complete suite at the end.
4. Obtain independent final code review for DSP, reference parsing/firewall and scoring, plus a
   separate math/claims review for agreement and manuscript statements.

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python -m pytest -q
```

**Acceptance:** full suite passes; no sealed prospective label was accessed; every new artifact is
bound to the new estimator chain; all disagreements are recorded; HISTORY is appended and HANDOFF
is current.
