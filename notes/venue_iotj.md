# Venue analysis — IEEE Internet of Things Journal (IoT-J)

> **Status: target venue selected, evidence partially unverified.** Written 2026-08-25.
> This file records a venue decision and the literature claims behind it. It contains **no
> results from this project**. Nothing here is paper-grade evidence.
>
> **Language rule (CLAUDE.md §4, standing rule `HISTORY.md` 2026-08-03).** The cohort design
> (4 development / 5 representation-validation / 10 final-evaluation, single one-shot M5 scoring)
> is a **transparency** property, not a timing one. Never describe it in this repo, the manuscript,
> a title, an abstract or a talk as "pre-registered", "pre-specified", "frozen before data",
> "registered <date>", "deposited" or "confirmatory". An external venue report received
> 2026-08-25 used the phrase "pre-registered ... design"; that phrasing is rejected and must not
> be copied forward.

---

## 1. Decision

**Target: IEEE IoT-J, regular issue.** Fallbacks in order: IEEE J-BHI, then IEEE Sensors Journal /
IEEE TIM.

Rationale: IoT-J is the highest-impact venue in this field that routinely publishes contactless
radar vital-signs work, it does not require the clinical cohort J-BHI expects, and n=10 scored
subjects is unremarkable there. The binding risk is **scope**, not cohort size — see §4.3.

No IoT-J special issue on contactless/RF health sensing is open (§5), so this goes to the regular
issue.

## 2. Provenance of the evidence below

An external literature report on IoT-J AIoT/healthcare sensing (2023–2026) was commissioned and
received **2026-08-25**. It was produced by a web-search agent, not by reading this repository.

**Its citations are largely unverified by its own admission.** Only two entries carried
full-text-verified metrics. Per CLAUDE.md §4 ("No invented citations"), this file separates what
may be cited from what may not.

| tier | may be cited? |
|---|---|
| §3.1 verified-metric subset | Only after independent confirmation on IEEE Xplore |
| §3.2 quarantine | **No.** Confirm existence, venue, year, DOI and every number first, then promote to §3.1 |

**Nothing in §3.2 may enter the manuscript, a related-work section or a claim of any kind until
promoted.** Promotion means: DOI resolves on IEEE Xplore, venue and year match, and each quoted
number is read from the full text.

## 3. Prior work

### 3.1 Verified-metric subset (still requires DOI confirmation before citing)

- **mmHRV** — Wang, Zeng, Wu, Wang, Liu, "mmHRV: Contactless Heart Rate Variability Monitoring
  Using Millimeter-Wave Radio," IoT-J 8(22):16623–16636, 2021. DOI `10.1109/JIOT.2021.3075167`
  (UNCONFIRMED). FMCW mmWave, TI IWR1843, **11 subjects**, contact ECG (BITALINO) reference.
  Reported: median IBI error 28 ms; RMSE 29.85 ms LOS / 31.71 ms NLOS. Resting/stationary only.
  Claims first contact-free multi-user HRV on commodity mmWave.
- **ViMo** — Wang, Zhang, Wu, Wang, Liu, "ViMo: Multiperson Vital Sign Monitoring Using Commodity
  Millimeter-Wave Radio," IoT-J 8(3):1294–1307, 2020/21. DOI `10.1109/JIOT.2020.3004046`
  (UNCONFIRMED). 60 GHz 802.11ad, **8 subjects**, Polar H10 chest-strap ECG reference.
  Reported: median error 0.19 BPM (RR) / 0.92 BPM (HR); HR MAE 1.29 BPM stationary, degrading to
  6.31 (speaking) and 3.06–4.16 (motion). Resting/stationary.

These two are the direct competitors: commodity-hardware contactless cardiac sensing with ECG
ground truth. Both are resting-only, neither uses Bland-Altman, neither holds out a scoring cohort.

### 3.2 Quarantine — existence plausible, details UNVERIFIED, not citable

Rong/Lenz/Bliss radar acoustics (IoT-J 2024, 11(5):7630–7639, and a 2026 motion-robust follow-up);
Wang et al. driver vital signs (IoT-J 9(13), 2022); Li et al. UWB elderly in-home monitoring
(IoT-J 11(4), 2024, HR MAE reported as 1.23±1.16 bpm); Abedi et al. mmWave gait + cloud (IoT-J
10(11), 2023 — activity recognition, not vital signs); Mercuri et al. SISO FMCW localization +
vital signs (IoT-J 8(14), 2021); Li et al. STC-RIS multi-person (IoT-J 2024); Wu et al. respiratory
abnormality segmentation (IoT-J 2026); Yin et al. smartphone WiFi respiration (IoT-J 9(2), 2022);
Fan et al. RespEnh (IoT-J 2024); Pitafi et al. animal vital signs (IoT-J 2025); Zhang et al.
multi-point reflection modelling (**IEEE TMC** 23(12), 2024 — not IoT-J); Pi-ViMo (**ACM TIoT**
4(2), 2023 — not IoT-J).

2026 IoT-J coverage is thin and partially indexed; treat it as incomplete.

**One quarantine item matters disproportionately:** the Rong radar-acoustics line is reported as
the only IoT-J work targeting *varying* heart rate. It operates in the 20–80 Hz heart-sound band —
different physics from our ~1 Hz chest-displacement phase — so it is complementary rather than a
scoop. Whether its HR variation was exercise-induced is UNVERIFIED. **Confirm this before claiming
the dynamic-range gap in §4.2 is unoccupied.**

## 4. Consequences for this project

### 4.1 Do not position on accuracy

Reported resting HR accuracy in the closest work is ~0.8–1.3 bpm (ViMo 0.92 median / 1.29 MAE).
We should expect to land near, not above, that. Accuracy is table stakes here.

### 4.2 Position on three properties the field under-reports

1. **Genuine HR dynamic range.** The recovery arm requires ≥20.0 bpm PR range
   (`notes/analysis_prespec.md:314`). Competitors vary geometry and body motion, not physiology.
   Our own 2026-07-31 measurement — that a session-median predictor scores 100% on
   effectively-constant-PR sessions — is the argument for why this matters.
2. **Subject-disjoint held-out scoring with no tuning.** Development on A–D, gate on 5, single M5
   scoring of 10 under committed scorer/config hashes (`notes/analysis_prespec.md:388-392`,
   `:400-411`). Describe as transparency and role separation — never as timing (see banner).
3. **Honest harmonic-collision reporting.** The 18 bpm paced arm sits in the 4·f_r ≈ HR collision
   zone and is reported separately for HR, never pooled (`notes/analysis_prespec.md`, pooling
   table; `notes/protocol.md`).

Subject-clustered Bland-Altman limits of agreement are the headline agreement metric and appear to
be rare-to-absent in this IoT-J subset.

### 4.3 Scope gate — the actual desk-reject risk

IoT-J judges this as a **systems** paper. A chest-phase estimator with an accuracy table reads as
signal processing and is redirected to Sensors J./TIM without review. Accepted work foregrounds
deployment: edge/host split, real-time streaming, measured latency and compute, scalability.

Required before submission:

- **Elevate the live path from demo to measured system component.** Needed: per-window latency
  distribution, sustained throughput against the 20 Hz frame budget, peak memory, CPU, and an
  explicit statement of what runs on the IWR1642 versus the host. None of those exist in `src/` or
  `scripts/`. **One exception, corrected 2026-08-25:** `t_warmup_scan_ms` *is* already measured and
  written into every capture's `run_metadata.json` (`scripts/live_demo.py:1033`, `:1491`;
  P001_natural = 4032 ms). It covers the warmup component of time-to-first-estimate, which matters
  because warmup runs the full downstream chain over 14 candidate bins before the first reported
  estimate (`notes/approach.md`, §3 step 4). An earlier draft of this file claimed no timing
  instrumentation existed at all; that was wrong because the field is populated from the warmup
  evidence dict rather than by a timing call in `src/`.
- **Blocking constraint (CLAUDE.md §4).** No live readout and nothing in `live_estimates.csv` is
  paper-grade: the live path uses an online median smoother, and paper metrics come from offline
  reprocessing of the saved `adc_stream.bin`. To claim a validated real-time system we must either
  make the causal/online estimator reproducible from the saved stream and score *it*, or quantify
  the online-versus-offline gap. Reporting offline numbers beside an unvalidated live demo is not
  acceptable and would not survive review.
- **TI on-chip output as a deployable baseline** (already planned, `notes/approach.md:612`, `:727`).
  Framed as accuracy-versus-cost it doubles as the edge-compute comparison, and the report suggests
  almost no IoT-J paper does this.
- **Quantify the raw-ADC data rate** over the DCA1000 link and state its deployment implication.

### 4.4 Known weaknesses to state, not hide

- **Reference device.** Masimo MightySat gives 1 Hz fingertip pulse *rate*; competitors use ECG
  (BITALINO, Polar H10). Two limits are unfixable in software: an undisclosed internal averaging
  window and peripheral pulse-transit lag, both worst where HR changes fastest — i.e. the recovery
  ramp. IBI/HRV is therefore **out of scope by design**, so we cannot contest mmHRV on its ground.
  Mitigation is a Methods reference-error budget (averaging window, transit lag, PI-based quality
  gating, which the pipeline already applies), **not** a new reference device: adding ECG is a
  protocol change requiring an ethics amendment, and the 15 prospective slots are fully allocated
  with no replacement budget (`notes/analysis_prespec.md:415-427`). An ECG sub-study on
  development subjects A–D is the only cheap route and remains a PI decision, not an assumption.
- **Single-subject only.** mmHRV and ViMo both claim multi-person. We are single-subject, seated,
  chest-facing, 0.8–1.4 m. Not fixable within the current allowance — declare it.
- **Single site, fixed posture and distance range, healthy adults, no motion robustness.**

## 5. Special issues

Per the report (source: `ieee-iotj.org/special-issues`, read 2026-08-25 — UNVERIFIED): **no open
IoT-J special issue targets contactless/RF health sensing or AIoT healthcare.** The nearest health
SI ("IoT-based Healthcare Industry 5.0") closed 2025-05-31. Partial-relevance open calls include
multimodal ISAC (deadline reported 2026-09-01) and ambient intelligence for AI-native IoT (reported
2027-02-15). The page reportedly states most new SI proposals are being rejected.

Fallback note: J-BHI's AIoMT SI is closed; its open call is reported as "BHI 2026: Intelligent
Informatics for Personalized Healthcare," deadline 2026-06-12 — **already past as of writing**.

Venue metrics reported for IoT-J: 2024 JCR impact factor 8.9, 5-year 9.6, average
submission-to-first-decision 6.9 weeks. UNVERIFIED — confirm in JCR before relying on them.

## 6. Open actions

1. Confirm the §3.1 DOIs on IEEE Xplore; promote or discard §3.2 items individually.
2. Confirm whether Rong et al. induce HR variation (§3.2) — it bears directly on the §4.2 item 1 claim.
3. **Designated focus area (owner, 2026-08-25).** Make the live output a defensible real-time
   result. The §4.3 choice — score the causal/online estimator, or quantify the online/offline gap
   — is **intentionally left open** until the work is picked up. It is an engineering decision, not
   a writing one.
4. Add latency/compute/memory instrumentation and a stated edge/host split. Needed under either
   route in item 3, so it is not blocked by that choice.
5. **Closed 2026-08-25: no ECG sub-study.** Equipment unavailable; it would require additional
   subjects and an ethics amendment that could not clear in a workable timeframe. The mitigation is
   the Methods reference-error budget described in §4.4, and IBI/HRV is out of scope by design.
   Do not re-propose it.
6. Re-check IoT-J special issues and the JCR impact factor near submission.
7. **Prerequisite to the DSP story (owner priority, 2026-08-25):** resolve the static
   clutter-removal omission — cite a justification or implement it. See `HANDOFF.md` §3 Track 0.
   Reviewers who know this field will ask, and the answer interacts with the harmonic-verification
   claims in §4.2.
