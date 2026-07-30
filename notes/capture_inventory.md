# Capture inventory — pre-registration deposit (M0 / A3)

> **Status: DRAFT, prepared 2026-07-25 for the M0 deposit.** Re-verified at freeze time
> (`plans/m0_preregistration.md` A3/C2). Every capture in existence at freeze time is enumerated
> here and labelled **pre-freeze exploratory** — none is confirmatory evidence
> (`plans/implementation_plan.md` §M0). Hashes computed with `sha256sum` on 2026-07-25.

---

## 1. Study captures (radar + optional Masimo)

All four are **pre-freeze exploratory**, single subject (the researcher, self-capture), collected
with `scripts/live_demo.py` in live mode. **Distance and posture were not machine-recorded** in
these exploratory runs (`run_metadata.json` `distance_cm`/`posture` = null) — a limitation; the
study protocol (`notes/protocol.md`) requires recording distance per session.

| id | date (UTC) | duration | Masimo? | live bin | corrected bin | arm |
|---|---|---|---|---|---|---|
| `20260713_170323_..._live_test1` | 2026-07-13 | 120 s | **no** | 22 | — | smoke test, natural |
| `20260713_172042_..._massimo1` | 2026-07-13 | 180 s | yes | 23 | 23 | natural |
| `20260713_182002_..._massimo2` | 2026-07-13 | 180 s | yes | 20 | 26 | paced 16 bpm |
| `20260714_180523_..._sweep` | 2026-07-14 | 480 s | yes | 21 | 26 | stepped 12→15→18→21 bpm |

**Corrected bin** = the bin the post-fix warmup locks on replay (`863600e`/`dfe7fb5`); it differs
from the live bin for `massimo2` and `sweep` (those live runs mis-locked — the 2026-07-14 mislock).
`massimo1`'s live lock was already correct.

### SHA-256 — raw ADC streams (`adc_stream.bin`)
```
a713ddb9f760b4338e0c05963a63c9f7a251458adfb3e53672ac6d0812ffa0e4  live_test1
dc2be2d008b48227c4bebde9bb5974133d3cf60196b4216e1ba630d175203f18  massimo1
112a64bf3cfc820868ad46df19a9dbfba7b0ce2612547e4dd3e5131bec1f88bf  massimo2
91bc422d9f33bbde0fc1549018692d008dee4f9c04ca374e04f153bb80a7a96d  sweep
```
### SHA-256 — Masimo reference CSVs
```
960af7f5038a8fe9233b38dfcb583872f3dba8c89082c72cf8e05ed371732bf9  demo_massimo1.csv (natural)
d1cb91a43691cb10907425b15c2f0f1d02da09d12825d399225c8b16e89d32ff  demo_massimo2.csv (paced 16)
5974a9f303f79729c254fb4dc904d1c85c4bea006fafc202d12e0267de93de04  demo_sweep.csv   (sweep)
```
`live_test1` has **no Masimo reference** — it can never contribute an agreement number.

## 2. Reprocessing artifacts (NOT captures)

Five `*_replay_unknown` folders are offline re-scorings of the streams above at the corrected bin,
not sessions. They contain no `adc_stream.bin`. Used for the A2 coverage characterization:

| folder | replays | corrected bin |
|---|---|---|
| `20260715_164124_replay_unknown` | massimo1 (natural) | 23 |
| `20260715_164018_replay_unknown` | massimo2 (paced 16) | 26 |
| `20260715_164132_replay_unknown` | sweep | 26 |

(Two further `20260714_2242*_replay_unknown` folders are earlier reprocessing scratch; not used.)

## 3. What of this inventory is PUBLIC (A6 privacy decision, 2026-07-25)

Approved public participant metadata schema (`plans/m0_preregistration.md` A6; ethics approval
`24IBEC051`, issuing board **IBEC, KAUST**, covers publication): **subject label, date, duration,
sex, age, height, cardiac condition, file hashes, class label** — nothing else. No names, no date
of birth, no raw recordings in the public payload.

At v1 freeze every row above is a **researcher self-capture**, so no third-party participant data
exists yet; the schema governs the amendment inventories that will carry M5/M6 participants. The
researcher's own sex/age/height/cardiac fields are **[to fill if the user chooses to publish own
demographics]** — not fabricated here.

## Provenance
- Method: `sha256sum` over `adc_stream.bin` and the Masimo CSV in each session folder;
  metadata from each folder's `run_metadata.json`.
- `data/raw/` is empty; `data/manifest.local.csv` is header-only (0 rows) — these four live-demo
  captures are the entire dataset at freeze time.
