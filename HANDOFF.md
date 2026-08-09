# Handoff — M1 complete

> Read this and `CLAUDE.md` before doing anything. **State verified 2026-08-10.**
> `HISTORY.md` is append-only; this file is the current resume point.

## 1. Current state

Active branch: **`vital_signs_own_v13`**. M1 implementation, canonical execution and independent
artifact verification are **PASS**. There is no active M1 code or artifact work.

The scientific gate-source commit is
**`0dc0698f5f208077744c6b90561645ddc0eea024`**. The clean sole authorization-only child used for
all real-data stages is **`a47182677d808911c551fae8585e02a23532b74f`**. This HANDOFF/HISTORY
refresh may appear in a later documentation-only commit; such a descendant does not change the
scientific source, authorization transition or artifact identities below.

M1 did not change ECA, AHET, thresholds, range-bin selection, signal representation or estimator
scientific behavior, and it was not tuned to Masimo. It corrected the scoring/provenance contract:
the complete `k>=0` ledger, separate `k=0`, persisted-lock `k>=1`, exact denominator reconstruction,
zero-output macro inclusion, typed bounded evidence and fail-closed source/data/config/reference
identity are now canonical.

## 2. Canonical artifact chain

| Stage | Artifact | Manifest SHA-256 |
|---|---|---|
| Gate | `results/m8_ahmed_transfer/synthetic/20260809T212052.240643Z_779928f3a61c` | `db941e47ac424af67fa57fe256b2bec7f115d2e4ab0a50155e10b519ab9d7991` |
| Smoke | `results/m8_ahmed_transfer/smoke_m1_a4718267/20260809T212711.258830Z_1a9372ff2b33` | `74c39a9b07c16c29881a239e44b8cdc4d05c9a1db2b77235503111ac6c18d641` |
| Radar | `results/m8_ahmed_transfer/radar_m1_a4718267/20260809T212904.045947Z_1a9372ff2b33` | `1530fbf6942c21e32a9b889a2c73bd1f988fa2038bac23760bb22fd31eb06f50` |
| Score | `results/production_eca_ahet/scored/20260809T220332.406724Z_1a9372ff2b33` | `c359fa71191e1271ee17fe61e74731b1622d4a2c05a70668c378d48b5f53f9d7` |

The score's `production_summary.json` SHA-256 is
`6bf50705df8d720615d5d2b0e64fa07175904673c79afccb99bfa46d04ba0d94`. Its canonical provenance
status is `complete_clean_tree_hash_bound`; source-chain verification is true. The authorization
SHA-256 is `e4f187ac7f19e4281a983cea6e1e3061188a662c11ae571c88a687c1f36944fb`, source-manifest identity
is `1a9372ff2b33cc7589e93cbb7a9565f07ad957d515cd5402db1ff9eefae15079`, reference identity is
`d79a907e68ddbf3970ebaebc428f6fe90490d967414d771627f09fdc18673e87`, environment identity is
`be1d5b17caa15772263be0deeb859b81f0e8b89ecbad1b0bd3e07d32bd874b5f`, and Conda explicit identity
is `0fb28a7698955668a26a552347239cd703a9aac08ead973ca14e2d3c47a0b050`.

The gate attestation recorded 1,373 collected tests: `1372 passed, 1 skipped, 0 failed/errors`.
Smoke cardinalities are 1 source/2 shared/14 estimator/12 Ahmed/2 production. Radar cardinalities
are 128 source/256 shared/1,792 estimator/1,536 Ahmed/256 production. All manifest and payload
hashes, parents, exact Cartesian row identities, authorization transition, source reconstruction,
raw/config/reference maps and bounded typed NPZ/index evidence passed verification; NPZ evidence is
pickle-free.

## 3. Canonical M1 result

- All-window radar coverage: **`14/128 = 0.109375`**.
- Joint given reference: **`9/67 = 0.13432835820895522`**.
- Persisted-lock `k>=1` radar coverage: **`11/120 = 0.09166666666666666`**.
- Separate `k=0`: 8 source, 3 radar-valid, 1 reference-admitted, 0 joint.
- All-window MAE/RMSE/bias: `2.7655614552159387 / 5.275212052978818 /
  -2.385366027726006` bpm.
- All-window capture-macro radar coverage: `0.19427083333333334`; zero-radar m3/m5,
  zero-joint m3/m5/m7.
- `k>=1` capture-macro radar coverage: `0.16973684210526316`; zero-radar and zero-joint
  m3/m5/m7.
- All eight captures remain in coverage macros even when accuracy is undefined.

All 128 fresh production HR values, validity decisions and reasons exactly equal the audited parent
and have identity SHA-256
`0ced713d76e2ac5d29a26d15a4b8a81f5e83f84d81e5bf156885d9d53ce17906`.

## 4. Interpretation and landmines

- The historical **30.08%** survivor-biased coverage is retired. Do not revive or cite it.
- `k=0` remains in the complete ledger and is reported separately; never silently drop it.
- The score manifest's `promotion_eligible: false` expresses claim status for exploratory,
  approximate-origin, single-subject development data. It is **not** a provenance failure; canonical
  provenance is `complete_clean_tree_hash_bound`.
- Existing captures have approximate frame origins (roughly 5–15 s uncertainty), insufficient HR
  dynamic range and no population-validation role. Correct arithmetic does not make them final
  agreement evidence.
- Canonical Windows execution requires full
  `C:/ProgramData/anaconda3/Scripts/conda.exe run -n radar-vitals --no-capture-output`, a unique short
  external `TEMP`/`TMP` under `C:\tmp`, and inherited handles for the final attested pytest child.
  Deep temp paths can break temporary Git repositories; captured/redirected final Matplotlib handles
  can fail native DLL execution.
- Do not edit `data/raw/`, manually handle score rows, weaken clean-tree/hash gates, tune to Masimo,
  or rerun/replace the canonical chain without explicit authorization.

## 5. Next step

M1 is complete. M2 is the next milestone only if the user separately authorizes it and its accepted
plan is reviewed before implementation. Do not infer M2 authorization from M1 completion.

## 6. Key files

| File | Purpose |
|---|---|
| `plans/plan_codex_milestones.md` | milestone boundaries and acceptance criteria |
| `src/m4/estimator_scoring.py` | canonical M1 rollup and scorer provenance contract |
| `src/m4/estimator_runner.py` | bounded evidence producer and radar transition gate |
| `src/m8/ahmed_provenance.py` | source, authorization and test-attestation contracts |
| `scripts/score_production.py` | canonical M1 scoring CLI |
| `experiments/m8_ahmed_transfer/capture_registry.yaml` | bound capture/config/reference inventory |
| `experiments/m8_ahmed_transfer/authorizations/real_evaluation_20260808.yaml` | canonical authorization |
| `notes/analysis_prespec.md` | estimands, ledgers and zero-output rules |
| `notes/protocol.md` | capture protocol and timing limitations |
| `HISTORY.md` | append-only implementation and evidence record |
