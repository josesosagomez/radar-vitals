# Capture inventory

> **Status: internal record.** Prepared 2026-07-25; M0 was removed from the project 2026-08-03, so
> this is no longer a deposit artifact. Every capture is **exploratory** — none is confirmatory
> evidence. Hashes computed with `sha256sum` on 2026-07-25.
>
> **CORRECTION 2026-08-03 — the eight captures are FOUR subjects, not one.** User-stated; it
> supersedes every "single subject / self-capture" claim in this file and elsewhere. See
> "Subject map" below. This materially changes prior conclusions that were caveated `n=1`.

---

## 1. Study captures (radar + optional Masimo)

All are **exploratory**, collected with `scripts/live_demo.py` in live mode, across **four
subjects** (see "Subject map" below). **Distance and posture were not machine-recorded** in
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

Rows above span four subjects (see "Subject map"), so third-party participant data
exists yet; the schema governs the amendment inventories that will carry M5/M6 participants. The
researcher's own sex/age/height/cardiac fields are **[to fill if the user chooses to publish own
demographics]** — not fabricated here.

## Provenance
- Method: `sha256sum` over `adc_stream.bin` and the Masimo CSV in each session folder;
  metadata from each folder's `run_metadata.json`.
- `data/raw/` is empty; `data/manifest.local.csv` is header-only (0 rows) — these four live-demo
  captures are the entire dataset at freeze time.


---

## Subject map — CORRECTION, 2026-08-03

**Source: user statement, 2026-08-03.** The project record previously said all eight captures were
a single researcher self-capture. That was wrong. The eight captures come from **four subjects**,
paired as follows:

| Subject | Captures | Date(s) |
|---|---|---|
| **A** | `massimo1`, `massimo2` | 2026-07-13 |
| **B** | `massimo3`, `sweep` | 2026-07-28, 2026-07-14 |
| **C** | `massimo4`, `massimo5` | 2026-07-28 |
| **D** | `massimo6`, `massimo7` | 2026-07-29 |

**Subject identity is not machine-recorded anywhere** — no `run_metadata.json` field carries it,
and it cannot be recovered from the artifacts. This table is the only record. Treat it as
authoritative and do not re-derive it.

**What this changes.** Several conclusions were explicitly caveated "n=1 subject" and are now
better supported than they were recorded as being:

- the 2026-07-31 signal-presence and bin-sweep findings (BR extractable; HR not demonstrated;
  bin selection not the coverage bottleneck) were drawn across four subjects, not one;
- the **−12 dB energy-eligibility threshold** in `src/warmup_select.py` was documented as
  "4 sessions / 1 subject" — its empirical basis is broader than stated;
- the **5-bin relock tracker** was deferred on 2026-07-28 *specifically* because the evidence was
  "n=1 subject who barely moved". **That deferral condition no longer holds.**

**What it does NOT change.** Four subjects is still small, and the subjects were not randomly
sampled. Nothing here becomes confirmatory.

**Train/test split agreed 2026-08-03** for the BR bin-selection work: discovery on **A + B**
(`massimo1`, `massimo2`, `massimo3`, `sweep`); the held-out test on **C + D** (`massimo4`,
`massimo5`, `massimo6`, `massimo7`) is touched **once**, with the feature set already frozen.
