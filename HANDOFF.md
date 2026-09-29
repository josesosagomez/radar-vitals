# Handoff — discrepancy-audit remediation

> Read this and `CLAUDE.md` first. State verified 2026-09-29. `HISTORY.md` is append-only;
> this file is the current resume point.

## 1. Project snapshot

The project estimates seated heart rate from a TI IWR1642BOOST/DCA1000 76–81 GHz FMCW radar at
0.8–1.4 m. The reference is Masimo MightySat `Beats / min` aligned only by integer Unix-epoch
`Timestamp`; `Perfusion Index` controls reference admission. Paper-grade outputs require a
reproducible script, config, seed, data hash, intermediate estimator evidence, and subject-aware
agreement analysis.

## 2. Current state

Audit remediation is being implemented on branch **`codex/discrepancy-remediation`**, in a managed
worktree created from integration branch **`vital_signs_own_v13`** at `e6d055c`. The original
checkout contains unrelated uncommitted MATLAB/export work and must not be overwritten or cleaned.

The authoritative discrepancy report is `reports/discrepancy_audit_2026-09-29.md`; the independently
reviewed implementation plan is `plans/discrepancy_remediation_2026-09-29.md`. The plan is revision
3. Independent review cleared Milestone 0 and Milestone 1 items 1–4 with its safety clarifications
incorporated. The remaining milestones still require their specified code or math reviews.

The last full-suite evidence before this remediation was supplied as **3144 passed, 5 skipped**.
Milestone 0 is green: the managed checkout has no tracked CRLF/mixed files and the EOL plus M8
provenance set passed **204 tests in 65.29 s**. No new full-suite result has yet been measured on
this branch. No sealed prospective reference has been opened, no raw capture has been modified,
and no reported result has been recomputed.

## 3. Active task / next steps

1. Implement Milestone 1 items 1–4:
   - reject new natural/paced captures below 120 s while metadata-only historical loading derives
     `protocol_compliant=false` / `settle_below_120s`;
   - exclude protocol deviations from primary scoring;
   - create one guarded, hash-bound, in-memory reference loader and route all production reads
     through it;
   - reject prospective paths from MATLAB examples and TI-config generation by default.
2. Do not test historical compatibility in scoring mode. Use synthetic 60 s fixtures; any real
   manifest check must be metadata-only and prove failure before a reference path can be opened.
3. Milestone 1 item 5 awaits the owner's factual dispositions for P003 paced, P007 natural, P009
   natural, and P010 natural. Do not infer their state or inspect their CSVs.

## 4. Recent decisions that matter

- Future natural/paced sessions require **120 s** settle. P001–P006 natural remain immutable 60 s
  protocol deviations and are excluded from the primary per-protocol analysis.
- The approved recovery scope is **15 new prospective participants**, not a third session for the
  existing 10 participants.
- The 2026-07-30 clutter-removal A/B is accepted as the negative Track 0 result. Static clutter
  removal remains off and P003 was not viewed.
- `vital_signs_own_v13` is the integration branch. There is no `main` target.
- A changed heart-rate estimator needs a new immutable estimator ID and a completely new clean
  provenance chain; historical estimator artifacts are not rewritten.

## 5. Gotchas / landmines

- Never open sealed P00x references while developing or testing the firewall. Prospective bytes may
  be parsed only after the atomic capability transition, through `guarded_reference_bytes`, and
  must be parsed in memory without reopening the path.
- `parse_session_v3(..., Mode.SCORING)` verifies bound artifacts and can open/hash a reference.
  It is forbidden for the historical-settle compatibility test.
- Do not change files in `data/raw/`, immutable manifests, receipts, or historical HISTORY entries.
- The original checkout's CRLF observations do not justify rewriting this managed worktree; verify
  its committed materialization first.
- M8 figure tests require the gitignored Ahmed source PDF. This worktree's copy was verified at
  SHA-256 `2D13BCA3FDFCBF249A500622DAC0BE9AD37E35A6AA880C440FF0C12EB4F8689F`.
- Attempt stage is not retry permission. The future ledger must separately record disposition,
  evidence, retry permission, and its exact policy basis.
- MATLAB source may be written but not executed (`MATLAB_CLI = false`).

## 6. Pointers

| File | Purpose |
|---|---|
| `reports/discrepancy_audit_2026-09-29.md` | evidence, severity, affected results, and owner addendum |
| `plans/discrepancy_remediation_2026-09-29.md` | reviewed milestone plan and acceptance criteria |
| `src/m2/acquisition_metadata.py` | current shared acquisition-metadata validator |
| `src/m2/manifest_v3.py` | historical/session parsing; scoring mode can verify reference artifacts |
| `src/m2/label_firewall.py` | existing atomic prospective-reference byte guard |
| `notes/analysis_prespec.md` | admission, retry, cohort-role, and repeated-measures rules |
| `cohort_registry/registry_v010.json` | current immutable cohort registry revision |
| `HISTORY.md` | append-only decisions and evidence |
