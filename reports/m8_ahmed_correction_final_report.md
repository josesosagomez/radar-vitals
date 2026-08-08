# M8 Ahmed harmonic-accumulation correction — final report

## 1. Verdict

**IMPLEMENTATION CORRECTED AND VALIDATED.**

M8 is complete as an exploratory, reproducible evaluation. Figure 8 was **not reproduced under the declared assumptions** (`status: not_reproduced_under_declared_assumptions`; promoted bundle hash `7d6263f797c7c54e0d3badf27d599a5a339a9f6d5d4ae38f4519b5828f2e4da3`). The synthetic transfer gate passed its declared controls, but that gate is non-gating for real data. The canonical two-lock × seven-arm real run and scorer completed; all reported agreement is approximate-origin and descriptive, with no arm ranking or incompatible protocol pooling.

## 2. Files changed and provenance

The M8 implementation and authorization history is represented by runner repair `df51a95`, gate-source commit `3058fe1`, and authorization commit `e182288`. The final report documents persisted bundles only; it does not access or alter raw ADC/Masimo inputs.

The corrected implementation is grouped as follows (supporting provenance/gate files are listed with the scientific layer they protect):

- **Layer A HA:** `experiments/m8_ahmed_fig8/layer_a_profiles.yaml`, `src/m8/ahmed_fig8.py`, `figures/reproduce_ahmed_fig8.py`.
- **Layer B FMCW adapter:** `experiments/m8_ahmed_transfer/layer_b_profiles.yaml`, `src/m8/ahmed_transfer.py`, `src/m8/ahmed_gate.py`.
- **Runner/evidence:** `src/m4/estimator_runner.py`, `src/m4/evidence_serialization.py`, `src/m4/capture_registry.py`, `experiments/m8_ahmed_transfer/capture_registry.yaml`, `src/m8/ahmed_provenance.py`, `scripts/m8_ahmed_transfer.py`.
- **Scoring:** `src/m4/estimator_scoring.py`.
- **Tests:** the M4/M8 estimator, evidence, provenance, Figure 8, gate, transfer, pairing, registry/scoring, all-bin, and score tests under `tests/`, including the independent scorer and paired-runner oracles.
- **Documentation:** this report, `HISTORY.md`, and `HANDOFF.md`.

| Bundle | Outcome / manifest |
|---|---|
| `results/m8_ahmed_fig8/20260808T182844.340231Z_8e08f5ab0120` | Figure 8 successor; `not_reproduced_under_declared_assumptions`; promoted hash above |
| `results/m8_ahmed_transfer/synthetic/20260808T183335.468320Z_779928f3a61c` | 1293 collected, 1292 passed, 1 declared `real_data` skip; manifest `3f0467d50a767ab4f0d5a0e5b57371e7be8574ae01b7667cd27f1673e4ab83d6`; promotion eligible |
| `results/m8_ahmed_transfer/smoke/20260808T183457.801299Z_bc3ccf4635c5` | real-smoke structure; manifest `8459493d1e62cd55776962b70f00c673fc76868553c4359aed61005cc804180b` |
| `results/m8_ahmed_transfer/radar/20260808T183600.392760Z_bc3ccf4635c5` | canonical radar evidence; manifest `51b68e937c6bb15f08c6e6a81072ca5a01d8602b89e046f1273c551427a15532`; counts 128 source / 256 shared / 1792 estimator / 1536 Ahmed / 256 production |
| `results/m8_ahmed_transfer/scored/20260808T191921.669826Z_bc3ccf4635c5` | canonical scoring; manifest `96b8112062403300e398e9d83a90f9e5dab43623974a0c0e7c126eef7f335835`; 3584 rows; labels claim-ineligible |

## 3. D1–D8 disposition

| Defect | Correction | Test or evidence | Status |
|---|---|---|---|
| D1 | Transitive dependency closure, clean/dirty checks, test attestation, and environment/Conda cross-binding | Promotion-eligible synthetic manifest `3f0467d5...`; provenance/preflight tamper tests | RESOLVED |
| D2 | Exact authorized two-lock/seven-arm identity with authorization checked before capture access | Smoke and radar manifests; 2 locks, 7 arms, 256 shared cells | RESOLVED |
| D3 | Comparative universe restricted to `k>=1`; k0 diagnostic only; natural/paced/unknown kept separate | 3,584-row scored bundle and protocol-partition tests | RESOLVED |
| D4 | Non-object evidence arrays, selected/runner-up bins, masks, validity, reasons, hashes, and reconstruction | 1,536 Ahmed plus 256 production evidence rows; serialization/reconstruction/failure-injection tests | RESOLVED |
| D5 | Production and Ahmed rerun on the same cube/window/lock/source/run; external historical comparator rejected | Same-run key/hash/cardinality validation and deliberate mismatch tests | RESOLVED |
| D6 | Successor-only Figure 8 bundle with honest status-derived title and verified hashes | Promoted successor hash `7d6263f7...`; mutation/status tests | RESOLVED |
| D7 | Immutable profile identities, magnitude sum, normalization/row/support/suppression distinctions | Literal full-score-array, factor-of-two, strict-Nyquist, and suppression controls | RESOLVED |
| D8 | Observed coverage and exact invalidity-reason reconciliation, including production `gate_not_run` normalization | Scored dispositions, hand-counted coverage tests, and 195-pass independent HR-reason repair verification | RESOLVED |

## 4. Frozen Layer A and Layer B profiles

Layer A uses magnitude score \(A(q)=\eta^{-1}\sum_h|S(hq)|\). Its frozen identities are:

- fixed H=3 and H=5 × `matrix_eta` × figure-visible, Eq. 26, or prose suppression (six equation-literal ambiguity audits);
- fixed H=3 and H=5 × `non_dc_mean` × the same three suppression interpretations (six reconstruction profiles, mapped to the six Layer B arms); and
- candidate-dependent-row `matrix_eta` × the same three suppressions (three matrix-row audits with no Layer B arm mapping).

Layer B uses `non_dc_mean`, fixed H=3 or 5, strict `Hq < f_Nyquist`, `q=f`, and rate `60q`. Its paired identities are:

| Arm/profile ID | Scientific identity |
|---|---|
| `production_eca_ahet_v1` | Same-run project production comparator; not an Ahmed profile |
| `ahmed_phase_h3_figure_visible_unsuppressed` | H=3, visible-figure unsuppressed interpretation |
| `ahmed_phase_h3_eq26_multiples_suppressed` | H=3, Eq. 26 breathing-bin-and-multiples suppression |
| `ahmed_phase_h3_prose_low_or_equal_suppressed` | H=3, prose candidate-at-or-below-breath exclusion |
| `ahmed_phase_h5_figure_visible_unsuppressed` | H=5, visible-figure unsuppressed interpretation |
| `ahmed_phase_h5_eq26_multiples_suppressed` | H=5, Eq. 26 breathing-bin-and-multiples suppression |
| `ahmed_phase_h5_prose_low_or_equal_suppressed` | H=5, prose candidate-at-or-below-breath exclusion |

## 5. Commands and outcomes

The persisted command interface was:

```powershell
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python -X utf8 -m pytest tests/test_m8_ahmed_fig8.py tests/test_m8_ahmed_transfer.py tests/test_m8_ahmed_gate.py tests/test_m8_ahmed_all_bins.py tests/test_m8_ahmed_score.py -q
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python -X utf8 figures/reproduce_ahmed_fig8.py
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python -X utf8 scripts/m8_ahmed_transfer.py synthetic
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python -X utf8 scripts/m8_ahmed_transfer.py real-smoke
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python -X utf8 scripts/m8_ahmed_transfer.py real-radar
C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python -X utf8 scripts/m8_ahmed_transfer.py score
```

The explicit five-file focused command passed **147 tests**. During `synthetic`, the frozen transitive test attestation collected 1,293 nodes and recorded **1,292 passed plus one declared `real_data` skip**. Figure 8 was negative; synthetic gate passed; real-smoke, real-radar, and score completed with the persisted counts/manifests above.

The Figure 8 command produced the negative verdict above. Synthetic completed with one authorized real-data skip and a passed transfer gate. Smoke, radar and score completed with the bundle identities above. The optional diagnostic is **NOT RUN — NOT AUTHORIZED**.

## 6. Synthetic controls

The gate passed P1–P4 line-model controls for H=3 and H=5. It explicitly covered harmonic overlap and factor-of-two controls: the collision domain selects the same 20 bpm line for breath and heart, while the representative domain selects 20 bpm and 80 bpm; the predicted 2×/3× harmonic ratios matched within 1e-12 relative tolerance and non-divisor candidates carried no signal. The persisted synthetic metrics report zero cycle slips and a maximum oracle residual of `4.17e-7 rad`.

The Figure 8 successor did not recover its declared 80 bpm heart target. In the visible-unsuppressed profile, H=3 selected `40.0144 bpm` and H=5 selected `20.0072 bpm`; both recovered the declared 20 bpm breath target as `20.0072 bpm`. The prose-suppressed H=3/H=5 heart curves both selected `40.0144 bpm`; the Eq. 26 heart curves were `inconclusive_by_definition` because the declared suppression excluded the expected target. No assumption was changed to force reproduction. These controls support only the declared seed/configuration and do not establish real-data robustness.

## 7. Canonical real results (`evaluation_k_ge_1`)

Each cell is `n_source / n_reference_admitted / n_radar_valid / n_joint / coverage / MAE / RMSE / bias`; rates are bpm and coverage is joint coverage. `NA` means no joint rows, not zero error. The values are the scorer's protocol-pooled summaries; k=0 is excluded.

The first three tables are the `recorded_lock_as_captured` estimand. The complete `current_production_rerun_lock` estimand follows them; the two locks are never pooled.

### Natural

| Arm | HR | BR |
|---|---|---|
| production | 5/5/0/0/0.000/NA/NA/NA | 5/5/5/5/1.000/0.769/0.776/0.181 |
| Ahmed H3 figure | 5/5/5/5/1.000/4.550/6.512/-4.150 | 5/5/5/5/1.000/9.000/10.227/-9.000 |
| Ahmed H3 Eq26 | 5/5/5/5/1.000/5.750/8.100/-5.350 | 5/5/5/5/1.000/9.000/10.227/-9.000 |
| Ahmed H3 prose | 5/5/5/5/1.000/4.550/6.512/-4.150 | 5/5/5/5/1.000/9.000/10.227/-9.000 |
| Ahmed H5 figure | 5/5/5/5/1.000/4.550/6.512/-4.150 | 5/5/5/5/1.000/11.400/11.550/-11.400 |
| Ahmed H5 Eq26 | 5/5/5/5/1.000/7.350/9.034/-7.350 | 5/5/5/5/1.000/11.400/11.550/-11.400 |
| Ahmed H5 prose | 5/5/5/5/1.000/4.550/6.512/-4.150 | 5/5/5/5/1.000/11.400/11.550/-11.400 |

### Paced

| Arm | HR | BR |
|---|---|---|
| production | 20/13/6/3/0.150/5.973/6.731/-5.973 | 20/20/20/20/1.000/1.409/3.322/-0.104 |
| Ahmed H3 figure | 20/13/20/13/0.650/25.154/26.072/-25.154 | 20/20/20/20/1.000/8.850/9.521/-8.850 |
| Ahmed H3 Eq26 | 20/13/20/13/0.650/23.462/24.515/-23.462 | 20/20/20/20/1.000/8.850/9.521/-8.850 |
| Ahmed H3 prose | 20/13/20/13/0.650/25.154/26.072/-25.154 | 20/20/20/20/1.000/8.850/9.521/-8.850 |
| Ahmed H5 figure | 20/13/20/13/0.650/25.462/26.337/-25.462 | 20/20/20/20/1.000/9.350/9.862/-9.350 |
| Ahmed H5 Eq26 | 20/13/20/13/0.650/23.769/24.796/-23.769 | 20/20/20/20/1.000/9.350/9.862/-9.350 |
| Ahmed H5 prose | 20/13/20/13/0.650/25.462/26.337/-25.462 | 20/20/20/20/1.000/9.350/9.862/-9.350 |

### Unknown protocol

| Arm | HR | BR |
|---|---|---|
| production | 95/48/3/3/0.032/7.373/9.109/-7.368 | 95/80/83/73/0.768/3.184/4.420/-1.151 |
| Ahmed H3 figure | 95/48/95/48/0.505/32.984/34.111/-32.984 | 95/80/95/80/0.842/10.300/10.690/-10.162 |
| Ahmed H3 Eq26 | 95/48/95/48/0.505/31.651/32.813/-31.651 | 95/80/95/80/0.842/10.300/10.690/-10.162 |
| Ahmed H3 prose | 95/48/95/48/0.505/32.984/34.111/-32.984 | 95/80/95/80/0.842/10.300/10.690/-10.162 |
| Ahmed H5 figure | 95/48/95/48/0.505/33.568/34.421/-33.568 | 95/80/95/80/0.842/11.162/11.345/-11.162 |
| Ahmed H5 Eq26 | 95/48/95/48/0.505/31.901/32.770/-31.901 | 95/80/95/80/0.842/11.162/11.345/-11.162 |
| Ahmed H5 prose | 95/48/95/48/0.505/33.568/34.421/-33.568 | 95/80/95/80/0.842/11.162/11.345/-11.162 |

The pairwise production-versus-Ahmed intersections, including null/count rows and lock identities, are retained without pooling in `partitions.json` in the scored bundle. The production arm is not ranked against Ahmed arms here; identical H3 figure/prose values in some strata reflect the declared disjoint-domain dependency, not independent corroboration.

### Complete `current_production_rerun_lock` tables

These are the second lock's exact protocol-pooled rows, in the same cell format as above.

| Stratum / arm | HR | BR |
|---|---|---|
| natural / production | 5/5/1/1/0.200/0.418/0.418/0.418 | 5/5/3/3/0.600/2.367/2.541/-0.159 |
| natural / Ahmed H3 figure | 5/5/5/5/1.000/4.250/7.390/-2.150 | 5/5/5/5/1.000/11.400/11.446/-11.400 |
| natural / Ahmed H3 Eq26 | 5/5/5/5/1.000/9.050/10.928/-7.350 | 5/5/5/5/1.000/11.400/11.446/-11.400 |
| natural / Ahmed H3 prose | 5/5/5/5/1.000/4.250/7.390/-2.150 | 5/5/5/5/1.000/11.400/11.446/-11.400 |
| natural / Ahmed H5 figure | 5/5/5/5/1.000/4.250/7.390/-2.150 | 5/5/5/5/1.000/11.800/11.857/-11.800 |
| natural / Ahmed H5 Eq26 | 5/5/5/5/1.000/9.050/10.928/-7.350 | 5/5/5/5/1.000/11.800/11.857/-11.800 |
| natural / Ahmed H5 prose | 5/5/5/5/1.000/4.250/7.390/-2.150 | 5/5/5/5/1.000/11.800/11.857/-11.800 |
| paced / production | 20/13/7/5/0.250/0.470/0.523/0.043 | 20/20/18/18/0.900/0.683/1.179/0.537 |
| paced / Ahmed H3 figure | 20/13/20/13/0.650/17.769/23.404/-17.308 | 20/20/20/20/1.000/8.950/9.646/-8.950 |
| paced / Ahmed H3 Eq26 | 20/13/20/13/0.650/17.923/22.656/-17.000 | 20/20/20/20/1.000/8.950/9.646/-8.950 |
| paced / Ahmed H3 prose | 20/13/20/13/0.650/17.769/23.404/-17.308 | 20/20/20/20/1.000/8.950/9.646/-8.950 |
| paced / Ahmed H5 figure | 20/13/20/13/0.650/17.923/23.627/-17.462 | 20/20/20/20/1.000/9.350/9.851/-9.350 |
| paced / Ahmed H5 Eq26 | 20/13/20/13/0.650/18.846/23.744/-17.923 | 20/20/20/20/1.000/9.350/9.851/-9.350 |
| paced / Ahmed H5 prose | 20/13/20/13/0.650/17.923/23.627/-17.462 | 20/20/20/20/1.000/9.350/9.851/-9.350 |
| unknown / production | 95/48/3/3/0.032/7.373/9.109/-7.368 | 95/80/83/73/0.768/3.184/4.420/-1.151 |
| unknown / Ahmed H3 figure | 95/48/95/48/0.505/32.984/34.111/-32.984 | 95/80/95/80/0.842/10.300/10.690/-10.162 |
| unknown / Ahmed H3 Eq26 | 95/48/95/48/0.505/31.651/32.813/-31.651 | 95/80/95/80/0.842/10.300/10.690/-10.162 |
| unknown / Ahmed H3 prose | 95/48/95/48/0.505/32.984/34.111/-32.984 | 95/80/95/80/0.842/10.300/10.690/-10.162 |
| unknown / Ahmed H5 figure | 95/48/95/48/0.505/33.568/34.421/-33.568 | 95/80/95/80/0.842/11.162/11.345/-11.162 |
| unknown / Ahmed H5 Eq26 | 95/48/95/48/0.505/31.901/32.770/-31.901 | 95/80/95/80/0.842/11.162/11.345/-11.162 |
| unknown / Ahmed H5 prose | 95/48/95/48/0.505/33.568/34.421/-33.568 | 95/80/95/80/0.842/11.162/11.345/-11.162 |

## 8. Authorization, limitations, and process disclosure

The optional all-bin diagnostic was not run and was not authorized. One legacy tester command read saved M1/M2/Masimo material read-only; it made no source, raw, or result writes, was excluded from validation evidence, and did not influence behavior. Real timestamps use the persisted approximate `start_wall_utc` origin; therefore these results are claim-ineligible for final agreement or promotion claims. The existing sessions also do not provide a falsifiable dynamic HR range.

## 9. Historical disposition

Historical Figure 8 and transfer bundles are historical-only, invalid for canonical promotion, and unaffected/immutable. The old real-data sweep/scorer path is superseded and invalid as canonical evidence. The replacement Figure 8, synthetic, smoke, radar, and scored bundles listed in section 2 are the successor evidence. No historical artifact or raw input was overwritten.

## 10. Thesis-safe conclusion and documentation changed

The corrected evidence supports a narrow conclusion: Ahmed HA's declared equations and ambiguities are now traceable through readable code, independent controls, paired evidence, and protocol-stratified scoring. The Figure 8 heart behavior did not reproduce under the declared assumptions, and the FMCW phase adaptation produced large, quantized, generally low-biased Ahmed HR/BR errors in these exploratory sessions. Because capture timing is approximate, the data are development-only, and the sessions lack a falsifiable HR dynamic range, these values cannot support a final agreement, population, or winner claim. This evaluates the project's **FMCW unwrapped-phase adaptation** (`q=f`, `60q bpm`); it does not validate or refute Ahmed's original **pulse-radar** mapping (`q=2f`, `30q bpm`) in its native hardware regime.

Documentation changed:

- `reports/m8_ahmed_correction_final_report.md` (this report).
- `HISTORY.md` (dated M8 completion entry).
- `HANDOFF.md` (current M8 report pointer and status note).
