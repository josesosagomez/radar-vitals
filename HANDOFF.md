# Handoff - calibrated demo checkpoint; isolated motion/slow-BR development

## 1. Project snapshot

Seated HR/BR from TI IWR1642BOOST + DCA1000EVM, chest height, 0.8-1.4 m.
Masimo `Beats / min` PR is the HR reference, aligned only by integer Unix
`Timestamp`; Perfusion Index gates reference quality. Live output is a demo
sanity check, never a paper-grade result. Read CLAUDE.md first.

## 2. Current state

Original checkout: `C:\Users\josemsosag\Desktop\vitals_radar_3`, branch
`vital_signs_own_v13`, base `47c3aa1fa1ae997fe1fade0491771c27c507c03a`.
Reviewed range/startup work is being checkpointed; exact-commit clean-checkout
verification and remote SHA recording follow the commit. Check Git before work.

Owner-operated calibrated development launch from this checkout:

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python scripts/live_demo.py --config scripts/live_demo_calibrated_config.yaml --duration-s 300
```

Calibrated YAML retains startup_delay_s: 10. Missing field defaults to 30; zero
skips. Study timing stays 30/30/0. Owner reports still-person usability; movement
robustness, HR accuracy and selected-bin reflector range acceptance remain
unverified. Measured +0.0784329 m range bias is subtracted in Python coordinates;
do not tune selection/bias from the reflector observation. Raw stream is saved.

Focused final checks: 278 passed; full default before numerical-test split:
3320 passed, 4 optional-replay skips, 1 real_data deselected. Independent
checkpoint and numerical-test reviews accepted. HISTORY records exact commands
and environment failures. The exact committed tree still requires clean testing.

Unrelated local work is excluded: steps/step_1/capture_config.yaml (285 to 600 s),
MATLAB/TI tooling/tests/notes and IoT/WST plans. Supplied motion/BR plan is saved
locally, excluded from checkpoint allowlist, and belongs on the feature branch.
No feature implementation or accepted physical detector calibration exists yet.

## 3. Active task / next steps

1. Test exact checkpoint in clean checkout, push vital_signs_own_v13, verify SHA
   against `git ls-remote origin refs/heads/vital_signs_own_v13`.
2. Create codex/live-motion-recovery-slow-br from pushed SHA in separate clean
   worktree. Preserve this checkout as fallback. Implement/test/review each gate.
3. Leave features disabled or clearly unavailable pending owner training,
   independent holdouts, representative-machine benchmark and rehearsal.
4. Research next: discrepancy-remediation Milestone 5, safe-refinement recompute
   under new synthetic gate/authorization/radar parent, registry subject field,
   arm_loa integration and constant-median baseline. No prospective scoring yet.

Fallback: stop experimental run; launch the calibrated command above here.
No reset, revert, deletion, force push or merge is needed.

## 4. Recent decisions that matter

- Keep respiration/vitals/window_pipeline/warmup_select and both original live
  YAMLs byte-identical to checkpoint during feature work. Ordinary windows 30 s.
- Do not tune to Masimo, read sealed references or operate hardware as an agent.
- data/raw is read-only. P006 and later cannot inform algorithm/threshold choices.
- Settle floor 120 s; P001-P006 natural 60 s settles are deviations. Approved
  recovery amendment covers 15 new prospective participants beyond A-D.
- Track 0 is closed; clutter removal stays none. Production estimator is
  eca_ahet_safe_refine_v2. Earlier paper numbers are stale pending recomputation.

## 5. Gotchas / landmines

- Use pinned conda run for Matplotlib. Simultaneous conda runs can collide.
- Full provenance tests require basetemp outside repo and nested temp permissions.
- Float32 FFT/SIMD/layout variability is about 1.51e-7 rad in synthetic probes;
  retain exact cached samples plus bounded real extraction tests.
- SampleSwap=1 is mandatory for SDK LVDS; Python iq_swap:true.
- LF endings enforced. Write bytes/LF rather than Windows-default CRLF.
- Study auto-selection only; no manual bins/replay/config overrides.
- Historical gates/source identities are commit-bound; never rewrite results.
- MATLAB_CLI=false. Untracked MATLAB/TI tools are not yet firewall-safe.
- Existing discrepancy-remediation worktree is stale; do not work there.
- Literature PDFs/results/data are gitignored and absent in clean checkouts.

## 6. Pointers

| File | Purpose |
|---|---|
| CLAUDE.md | project rules and independent reviews |
| HISTORY.md | append-only evidence/failures/research context |
| scripts/live_demo.py | acquisition, ordinary DSP and display |
| scripts/live_demo_calibrated_config.yaml | board bias and 10 s startup |
| config/range_calibration_iwr1642_2026-10-06.json | range evidence/transfer limits |
| src/range_coordinates.py | corrected gate/display coordinates |
| plans/live_demo_range_calibration_2026-10-06.md | range software/physical limits |
| plans/live_demo_startup_delay_2026-10-06.md | configurable countdown isolation |
| plans/discrepancy_remediation_2026-09-29.md | research Milestone 5 sequence |
| reports/remediation_verification_2026-09-30.md | research checkpoint |
| notes/protocol.md | protocol and settle requirements |
| notes/analysis_prespec.md | cohorts/windows/admission |
| src/reference_access.py | guarded reference boundary |
| src/m2/label_firewall.py | prospective capabilities/receipts |
| src/window_pipeline.py | ordinary DSP interface |
