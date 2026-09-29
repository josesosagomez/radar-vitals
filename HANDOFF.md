# Handoff — discrepancy-audit remediation

> Read this and `CLAUDE.md` first. State verified 2026-09-29. `HISTORY.md` is append-only;
> this file is the current resume point.

## 1. Project snapshot

The project estimates seated heart rate from a TI IWR1642BOOST/DCA1000 76–81 GHz FMCW radar at
0.8–1.4 m. The Masimo MightySat `Beats / min` column is the heart-rate reference and is aligned only
by integer Unix-epoch `Timestamp`; `Perfusion Index` controls reference admission. Paper-grade
outputs require script, config, seed, input hashes, intermediate estimator evidence, and
subject-aware agreement analysis.

## 2. Current state

Audit remediation is on branch **`codex/discrepancy-remediation`** in the managed worktree
`C:\Users\josemsosag\.codex\worktrees\discrepancy-remediation\vitals_radar_3`, based on integration
branch **`vital_signs_own_v13`**. The original checkout contains unrelated uncommitted MATLAB/export
work and must not be overwritten or cleaned.

Milestone 1 is implemented and independently reviewed READY: new natural/paced captures require
120 s settle; historical 60 s records remain loadable as explicit protocol deviations and cannot
enter primary scoring; all production reference reads use the central guarded boundary; and the
reference admission gate is applied before error scoring. Step 5/6 are radar-only unless a
per-session exact registered development capture is configured. No sealed prospective reference
was opened and no historical result was rewritten.

The final complete pinned suite passed **3189 passed, 17 skipped** in 252.94 s. Independent reviews
of the reference boundary, refinement, ECA semantics, tracker separation, and agreement mathematics
all returned READY. The ignored Ahmed and Kotte source PDFs are present only as hash-verified test
dependencies and remain untracked.

The pre-fix refinement diagnostic is committed and executed. On 128 development radar-only windows
it found 39 unsafe fallbacks among 756 calls: 31 legacy offsets exceeded half a bin and 30 legacy
refined frequencies were outside their permitted band, with overlap. The worst accepted
fixed-spectrum counterfactual was 26.4794 bpm (`sweep`, k=4, candidate rank 2). The full artifact is
`reports/peak_refinement_diagnostic_2026-09-29.json`; it is bound to clean commit `4280e34` and
records no reference access.

Production heart refinement now uses bounded, structured interpolation at all three ECA+AHET call
sites and emits fixed-width delta/reason evidence. New output identity is
`eca_ahet_safe_refine_v2`; the M4 production arm/suite are versioned consistently. Independent
DSP/provenance review is READY. The old real-data authorization remains immutable and does not
authorize this new estimator.

Production `skip_forbidden_harmonics_v1` semantics are now explicit: it projects only harmonics
below the cardiac band and performs no in-band ECA. A separate reference-free `none` mode performs
no projection; unknown modes fail closed. Independent review is READY. The Step-6 temporal tracker
is now a separately labelled non-causal diagnostic: it cannot relabel AHET rejection rows, generic
summary metrics remain AHET-only, and Viterbi restarts preserve completed segments. Independent
tracker review is READY.

The incorrect pilot agreement table is withdrawn, the Ahmed citation points to new `[R22]`, the
approved ethics scope is recorded as 15 new prospective participants, and the reference-informed
legacy gate origin is disclosed. M9 pooled-window intervals are now explicitly descriptive.
`src/agreement.py::arm_loa` implements the subject-clustered unbalanced-ANOVA estimator, fixed
whole-subject bootstrap, mandatory diagnostics, and the REML random-intercept proportional-bias
sensitivity. The retired pooled-window plotting script fails closed.

## 3. Active task / next steps

No audit-remediation item is waiting on an owner disposition. On 2026-09-30 the owner confirmed
that the P003 paced, P007 natural, P009 natural, and P010 natural work directories were preparation
placeholders only; none was started. Their registry state remains `planned`, no attempt was
consumed, and their future acquisition is the initial capture rather than a recapture. The misnamed
P010-area `P001_natural_settle.json` placeholder has no evidentiary status and must not be reused;
generate correctly named settle evidence when P010 natural actually starts.

## 4. Recent decisions that matter

- Future natural/paced sessions require **120 s** settle. P001–P006 natural are immutable 60 s
  protocol deviations excluded from the primary per-protocol analysis.
- Approved recovery scope is **15 new prospective participants**.
- The 2026-07-30 clutter-removal A/B is the accepted negative result; static clutter removal stays
  off. P003 animation/output was not viewed.
- `vital_signs_own_v13` is the integration branch; there is no `main` target.
- Any estimator behavior change needs a new immutable estimator ID and a new clean provenance
  chain. Historical artifacts are labelled/superseded, never rewritten.

## 5. Gotchas / landmines

- Never open P00x references during estimator development. The refinement diagnostic is radar-only
  and may use only the development ADC identities bound by the M8 capture registry.
- Prospective reference bytes may be parsed only through `src/reference_access.py` after an atomic
  firewall capability transition. Capability-free preflight validates metadata, not payload bytes.
- Step 5/6 `reference_access.development_capture_dirs` is a per-session mapping. An empty mapping is
  intentionally radar-only; never restore flat `data/raw` discovery.
- Do not edit `data/raw/`, immutable manifests/receipts, or previous HISTORY entries.
- MATLAB source may be written but not executed (`MATLAB_CLI = false`).
- The managed worktree needs the pinned Conda launcher; direct environment Python can reproduce a
  Windows Matplotlib loader failure.

## 6. Pointers

| File | Purpose |
|---|---|
| `reports/discrepancy_audit_2026-09-29.md` | evidence, severity, affected results, owner addendum |
| `plans/discrepancy_remediation_2026-09-29.md` | reviewed milestone plan and acceptance criteria |
| `src/protocol.py` | shared 120 s protocol constants |
| `src/reference_access.py` | single development/prospective reference boundary |
| `reference_registry/development_references_v1.json` | exact development reference identities/hashes |
| `src/m2/label_firewall.py` | prospective capability and single-read byte guard |
| `src/vitals.py` | bounded peak refinement and explicit ECA modes |
| `src/agreement.py` | subject-clustered LoA, bootstrap, diagnostics and regression sensitivity |
| `experiments/m8_ahmed_transfer/capture_registry.yaml` | development ADC identities/hashes for diagnostic |
| `notes/analysis_prespec.md` | admission, retry, cohort-role, repeated-measures rules |
| `HISTORY.md` | append-only decisions and evidence |
