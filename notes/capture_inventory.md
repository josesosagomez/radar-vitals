# Capture inventory — pre-registration deposit (M0 / A3)

> **Status: DRAFT, prepared 2026-07-25 for the M0 deposit; AMENDED 2026-07-31.** Re-verified at
> freeze time (`plans/m0_preregistration.md` A3/C2). Every capture is **pre-freeze exploratory** —
> none is confirmatory evidence (`plans/implementation_plan.md` §M0).
>
> **The 2026-07-25 version of this file is no longer complete on its own.** It enumerated four
> captures and stated they were the entire dataset. Five further captures were recorded on
> 2026-07-28/29 (§1b), one listed capture has since been deleted, and the three replay folders in
> §2 are also gone. **Hash provenance differs between §1a and §1b and the difference is material —
> read §1b's provenance note before citing its hashes.**

---

## 1a. Study captures recorded 2026-07-13/14 (hashes contemporaneous)

**Pre-freeze exploratory**, single subject (the researcher, self-capture), collected with
`scripts/live_demo.py` in live mode. **Distance and posture were not machine-recorded** in these
exploratory runs (`run_metadata.json` `distance_cm`/`posture` = null) — a limitation; the study
protocol (`notes/protocol.md`) requires recording distance per session.

| id | date (UTC) | duration | Masimo? | live bin | corrected bin | arm |
|---|---|---|---|---|---|---|
| `20260713_172042_..._massimo1` | 2026-07-13 | 180 s | yes | 23 | 23 | natural |
| `20260713_182002_..._massimo2` | 2026-07-13 | 180 s | yes | 20 | 26 | paced 16 bpm |
| `20260714_180523_..._sweep` | 2026-07-14 | 480 s | yes | 21 | 26 | stepped 12→15→18→21 bpm |

**Corrected bin** = the bin the post-fix warmup locks on replay (`863600e`/`dfe7fb5`); it differs
from the live bin for `massimo2` and `sweep` (those live runs mis-locked — the 2026-07-14 mislock).
`massimo1`'s live lock was already correct.

> **`20260713_170323_..._live_test1` was listed here on 2026-07-25 and no longer exists.** It was a
> 120 s smoke test, natural breathing, live bin 22, **no Masimo reference** — so it could never have
> contributed an agreement number. Its recorded raw hash was
> `a713ddb9f760b4338e0c05963a63c9f7a251458adfb3e53672ac6d0812ffa0e4`. Retained here as a record of
> what was deleted (CLAUDE.md §4), not as an available input.

### SHA-256 — raw ADC streams (`adc_stream.bin`)
```
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

**Independently confirmed 2026-07-30.** `scripts/verify_capture_integrity.py --verify-hashes`
recomputed all three raw hashes and they **MATCH** the values above. Because these were recorded on
2026-07-25 and re-derived from an independent path five days later, they do attest the files are
unmodified over that interval.

## 1b. Study captures recorded 2026-07-28/29 (hashes NOT contemporaneous — see provenance)

Same subject, same script, same live mode. All five are **natural** breathing.

| id | date (UTC) | duration | Masimo? | live bin | corrected bin | arm |
|---|---|---|---|---|---|---|
| `20260728_224902_..._massimo3` | 2026-07-28 | 600 s | yes | 26 | not established | natural |
| `20260728_230903_..._massimo4` | 2026-07-28 | 600 s | yes | 25 | not established | natural |
| `20260728_232415_..._massimo5` | 2026-07-28 | 600 s | yes | 25 | not established | natural |
| `20260729_002158_..._massimo6` | 2026-07-28 | 600 s | yes | 24 | not established | natural |
| `20260729_004815_..._massimo7` | 2026-07-28 | 601 s | yes | 32 | not established | natural |

**"Natural" basis:** user declaration (2026-07-31), corroborated independently by the Masimo RRp
channel — each of these wanders 3–10 bpm within a session (e.g. `massimo6` per-120 s medians
`18|18|18|18|16|11`), unlike `massimo2`, which reads a flat `16.0` with IQR 0.0. RRp is Masimo's
*measurement*, not the commanded rate, so this corroborates the declaration rather than proving it.

**"Corrected bin" is not established** for these five: no post-fix replay comparison of the kind
that produced §1a's corrected bins has been run against them. The live bin is recorded, not endorsed
— `massimo7`'s live lock of 32 sits at the gate edge (1.40 m) while its in-gate energy peak is at
bin 24, which is unexplained.

### SHA-256 — raw ADC streams (`adc_stream.bin`)
```
cca0cdcbaa8235672c96f524666835824aadf40aa78b12197705da63dbeb7b00  massimo3
2edc2c6d1976407eea40101550b1f3c0ab442843a8cb8311a8ab4538bc423968  massimo4
a55a0e42f9972d6bcf5173870475f69694aabd808d84d4182fbdef544e76bb5b  massimo5
b81ff843eff68e0bc9c05194525f065a9edc8346ac65397bf96d181d2e5a9103  massimo6
782166e0a0dda411e3f2eecf891fd1193ebdfa681c3edd9919ee7b1688c053c5  massimo7
```
### SHA-256 — Masimo reference CSVs
```
92054b471a04eefdbe4de45988846bab503cf61a0e5c619e5d8f545ef0a965cb  demo_massimo3.csv (natural)
286b6e3fe085acbef15a85448f383e95945cd3ef23bf309c0159d0dc3dc6dfe5  demo_massimo4.csv (natural)
6e0de2788f3b1162bce1782b70a8a7a2d5a752fdc52b3ca12029119368aa8498  demo_massimo5.csv (natural)
119b740e801a410be2af4ce8962b2defb2a95a9aaf2b2f9b7494d097a7143178  demo_massimo6.csv (natural)
778cbbc8f4c2618294c6a29e2ba29c2022504bb4bdd2fe437637cf57ef4b0684  demo_massimo7.csv (natural)
```

> ### Provenance of the §1b hashes — read before citing them
>
> **These hashes were computed on 2026-07-31, two to three days after capture, and there is no
> earlier record to compare them against.** They attest the files as they stood on 2026-07-31. They
> do **not** attest that the bytes are what the radar wrote, and nothing else does either.
>
> **Why the contemporaneous hash is missing.** `scripts/live_demo.py` computed the raw-mirror
> SHA-256 inside the receive thread's `finally`, while `stop()` joined that thread with a 3 s
> timeout. Hashing a multi-GB mirror does not finish in 3 s, so the join expired and
> `run_metadata.json` was written with `live_raw_mirror_hash: null`. The threshold is a file-size
> one and the correlation is exact: the 0.47 GB captures in §1a kept their hash; `sweep` (1.26 GB)
> and all five captures here (1.57 GB) lost theirs. Fixed 2026-07-31 in `cee8644` — finalisation
> moved to the main thread, and a missing hash now always carries a written reason. See
> `HISTORY.md` 2026-07-30.
>
> `sweep` sits in the same gap but is **not** affected in substance, because §1a recorded its hash
> on 2026-07-25 by a separate route and that value still matches. These five have no such fallback.
>
> **Consequence for M0.** If any of `massimo3`–`massimo7` is cited in the deposit, this weaker
> provenance must be disclosed alongside it. They are pre-freeze exploratory in any case, so no
> confirmatory claim rests on them — but "hashed at capture" and "hashed three days later" are not
> the same evidential status and must not be presented as if they were.

## 2. Reprocessing artifacts (NOT captures) — ALL DELETED

Five `*_replay_unknown` folders were offline re-scorings of the §1a streams at the corrected bin,
not sessions; they contained no `adc_stream.bin`. They were used for the A2 coverage
characterization.

**Verified 2026-07-31: none of them still exists.** Recorded here so the A2 numbers can be traced to
what produced them, and so nobody looks for inputs that are gone:

| folder | replayed | corrected bin | status |
|---|---|---|---|
| `20260715_164124_replay_unknown` | massimo1 (natural) | 23 | deleted |
| `20260715_164018_replay_unknown` | massimo2 (paced 16) | 26 | deleted |
| `20260715_164132_replay_unknown` | sweep | 26 | deleted |

(Two further `20260714_2242*_replay_unknown` folders were earlier reprocessing scratch, not used,
also gone.)

These are regenerable from the §1a raw streams, which survive and whose hashes still match — so
this is a reproducibility cost, not lost evidence.

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
- Method: SHA-256 over `adc_stream.bin` and the Masimo CSV in each session folder; metadata from
  each folder's `run_metadata.json`.
- §1a hashes: computed 2026-07-25, independently re-confirmed 2026-07-30 via
  `scripts/verify_capture_integrity.py --verify-hashes` — all match.
- §1b hashes: computed **2026-07-31 only**, days after capture. See §1b's provenance note; this is
  a weaker attestation and must be disclosed as such wherever those captures are cited.
- Verified 2026-07-31: `data/raw/` is empty (0 entries) and `data/manifest.local.csv` is
  header-only (0 data rows). **The eight live-demo captures in §1a and §1b are the entire dataset.**
  (The 2026-07-25 version of this line said "these four ... are the entire dataset"; that was true
  when written and is not now.)
- Session-type attributions: §1a from this file's original 2026-07-25 entries; §1b from user
  declaration 2026-07-31, corroborated against the Masimo RRp channel as described in §1b.
