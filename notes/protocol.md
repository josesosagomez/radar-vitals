# Capture protocol — seated, warmup auto-lock

> Single agreed capture protocol for all post-2026-07-09 sessions. All earlier
> (supine / mixed-protocol) captures were retired on 2026-07-09 — see HISTORY.md
> "Hard reset of all datasets". Keep this file, `scripts/live_demo_config.yaml`,
> CLAUDE.md, and `notes/approach.md` consistent; if the protocol changes, change
> all four in the same commit.

---

## Study design

- **Exactly 5 representation-validation subjects plus exactly 10 final-evaluation slots,
  3 sessions each.** The existing four subjects A–D remain development-only and do not enter either
  prospective cohort. Scope is therefore exactly 15 new prospective people and at least 19 unique
  subjects in the project.
  > Session 3 is the **HR dynamic-range (recovery) arm**. Owner attestation 2026-08-09 confirms
  > approval on 2026-08-03 under parent approval `24IBEC051`, covering these 15 new prospective
  > participants in addition to A–D. The determination and consent/PIS records are confidential and
  > intentionally held outside the repository.
- **Session 1: natural breathing.** Subject breathes normally; no pacing.
- **Session 2: paced breathing** with a metronome at a fixed target rate.
  - Rates: **12, 15, 18 breaths/min**, one steady rate per subject, assigned by independent
    **cohort-slot rotation** (12 → 15 → 18 repeating). The five representation-validation
    slots are **2 / 2 / 1** and the ten final-evaluation slots are **4 / 3 / 3**. The approved
    15-person ceiling is fully allocated; no additional participant replacement is assumed. These are the
    mixture weights of the paced arm-level LoA (`notes/analysis_prespec.md` §1, M3R-31).
    Rationale:
    12 bpm harmonics sit clear of the cardiac band (easy case); 18 bpm (0.30 Hz)
    puts the 4th harmonic at ~1.2 Hz = 72 bpm, inside the resting-HR band.
  - Metronome = **2x the target rate** (each beat = one inhale or one exhale).
  - **Settle 2 min** at the target rate before recording; confirm Masimo BR is
    stable within +/-1-2 bpm for >=60 s, then start the **10-min** recording at
    that steady rate.
  - **RECORD THE SUBJECT'S RESTING HR (Masimo PR) BEFORE EACH PACED SESSION** and
    write down the margin **|HR - 4 x f_r|**. This is the single number that predicts
    whether the session will fail: approach.md S4.2 documents that when the 4th
    respiratory harmonic coincides with HR (margin <~ 2-5 bpm), ECA cancels the
    cardiac signal along with the harmonic and the session yields mostly NaN plus a
    large negative bias. Pacing makes this *worse* than natural breathing because it
    pins f_r constant, so the collision is sustained for the whole recording.
  - The **18 bpm arm is deliberately inside that failure zone** and is being run to
    measure it on the current pipeline. Its results must be **reported separately and
    never pooled into the headline agreement metrics**.
- **Session 3: HR dynamic-range (recovery).** Seated recording following brief submaximal
  exertion, so PR declines through the session. Natural breathing, no pacing. Full procedure,
  screening and gates in "HR dynamic-range arm" below.
- **Sessions are on different days** (not back-to-back). This reduces within-day
  fatigue/carryover but adds day-to-day and time-of-day HR variability: **record
  the time of day** for every session and **re-measure the radar-to-chest
  distance** each day (a new setup each visit). **This matters more for session 3** — it must
  not follow another session on the same day, so no residual fatigue confounds the recovery
  curve.
- **Order is fixed** (natural, then paced, then recovery) — record as a study limitation
  (no counterbalancing). Recovery is placed last deliberately: it is the only arm involving
  exertion, so a subject who withdraws after it still contributes two complete arms.
- All three sessions otherwise follow the fixed conditions below, except the recovery arm's
  explicitly stated settle-criterion exemption.

---

## Fixed conditions (do not vary between sessions)

- **Posture:** seated, **back straight**, **both hands resting on the legs**
  (thighs), **face toward the radar**. No slouching, no crossed arms, no phone
  in hand.
- **Radar geometry:** IWR1642BOOST at chest height, sensor face **horizontal and
  pointed at the chest** (not angled, not overhead). Confirm level with a phone
  spirit level.
- **Distance:** seat the subject so the chest is **0.8–1.4 m** from the radar
  face. The exact distance need not be pinned — the warmup stage searches this
  whole range and locks the best bin (see below). Record the measured
  radar-to-chest distance in the session notes regardless.
- **Reference:** Masimo MightySat on a finger. The subject's hands stay still on
  the legs throughout — do not move the sensored hand during recording.
  > **Intended-use limitation (declared; DISPOSITIONED — user decision 2026-07-26, M3R-34).** The
  > MightySat Rx manual (`literature/ref_papers/lab-10168a_master.pdf`, p. 10) states *"Do not use
  > MightySat Rx for continuous monitoring. It is intended for spot-check use only. No alarms are
  > provided."*, and (p. 7) indicates PR/RRp for **spot checking**. This protocol logs the device
  > **continuously for 10 min** (PR for HR, RRp for BR). **Disposition (study assumption, M3R-43):**
  > the manual gives no rationale for the warning; **the study assumes** the labelling is principally
  > about **battery endurance** (multi-hour/day use) and the **absence of safety alarms** (unattended
  > monitoring) — **not** per-sample accuracy within a short session — and assumes accuracy over a
  > **10-min attended session with healthy adults and a verified battery** (checklist below) is
  > unaffected; the no-alarms point is irrelevant to an attended research capture (not patient
  > monitoring). The manufacturer does not certify this inference. Approval `24IBEC051` permits the
  > 10-min collection (user-confirmed 2026-07-24), and reference logging is intrinsic to it. See `notes/comparator_prespec_br.md` §2.2
  > and `notes/comparator_prespec.md` §2.2 (recorded identically, device-wide).
- **Environment:** quiet room, no one walking around, no fan/HVAC airflow at the
  subject.
- **Scene behind the subject:** the subject should be the **dominant reflector**
  the radar sees. Keep the space behind and beside the chair clear of large flat
  or metallic surfaces (equipment carts, monitors, cabinets, whiteboards, an
  un-setback wall) within ~3 m of the sensor. Record what is behind the chair in
  the session notes.

  > **Added 2026-07-30 after finding this had already drifted, unrecorded.** The
  > five captures of **2026-07-28** (`massimo3`–`massimo7`) contain static
  > reflectors at **2.09 m** and **2.88 m** (plus one at 4.19 m) that return
  > **more energy than the subject**, who sits **3.3–9.6 dB below them**; on
  > `massimo4` the body is only the *fifth* strongest return in the range profile.
  > The three earlier captures (`massimo1`, `massimo2`, `sweep`, 2026-07-13/14)
  > have the subject as the dominant reflector at +0.0 dB. **The room changed
  > between the two sessions and nothing recorded it** — exactly the protocol
  > drift CLAUDE.md §3.6 exists to prevent. It was found only by inspecting range
  > profiles two days later.
  >
  > **Why it matters.** `src/warmup_select.py` states its own operating assumption
  > as "single seated subject is the dominant reflector inside the distance gate",
  > and flags it as *not yet validated against competing reflectors*. Those five
  > captures are the competing-reflector case. The distance gate still constrains
  > selection, so the picks look sane — but the assumption behind them is only
  > marginally true, and the sidelobe skirts of strong static returns leak into the
  > gate. No stage of the pipeline removes static clutter (see `notes/approach.md`).
  >
  > **Not retroactively excluded.** Those five captures remain valid captures; this
  > is recorded as a known scene difference between eras, not a defect finding. What
  > it forbids is silently comparing across the two eras as if the scene were fixed.
- **Recording duration:** **10 minutes** (600 s) per session, yielding **exactly 20
  independent non-overlapping 30 s windows** on the frozen frame-index grid
  (`notes/analysis_prespec.md` §7), `k = 0 … 19`. The first window `k = 0` (`[0, 30) s`)
  **is scored** — it is the warmup-fill buffer, re-processed with the selected bin via
  `dsp_override` (not discarded). Radar-acceptance and reference gates then reduce this
  count; report coverage alongside accuracy.

  > **Changed from 5 to 10 minutes on 2026-07-24**, before any session was captured under it,
  > so it is simply what the protocol has always said rather than a later amendment. (The
  > original wording justified this by its position relative to the M0 deposit; no deposit ever
  > existed — M0 was removed 2026-08-03 — but the decision and its date are unaffected.) The
  > project record says parent approval **`24IBEC051`** permits up to 10 minutes and covers
  > collection/publication (user-confirmed 2026-07-24/25); the approval document itself is not in
  > the repo. **Do not exceed 10 minutes.**
  >
  > **Why.** At the measured 10-46% HR coverage, a 5-min session produced only ~9
  > windows, i.e. ~1.8-8.3 accepted windows per subject across both sessions before
  > Masimo PI / coverage / stationarity exclusions removed a further 12-20%. At the
  > pessimistic end that is ~2 usable windows per subject, which cannot support a
  > per-subject agreement claim or subject-clustered Bland-Altman limits. Doubling
  > the recording roughly doubles the windows at no extra recruitment cost.
  >
  > **The risk this introduces, and how to check it.** The warmup locks **one range
  > bin for the entire session**, so a posture shift late in a longer sit corrupts
  > the tail of the recording without any obvious symptom — the same class of silent
  > failure as the 2026-07-14 mislock. The 486 s (~8 min) sweep session showed this
  > is tolerable, but that subject was actively pacing. **Representation validation must verify
  > the locked bin still tracks the chest at minute 9-10** (inspect the run with
  > `scripts/diagnose_live_run.py`); if it does not, the duration comes back down.
  >
  > Storage: ~1.55 GB raw ADC per 10-min session; the 45 prospective sessions at the minimum
  > 5-validation/10-final design require approximately 69.8 GB before technical session recaptures.

## Warmup & bin lock

- Bin selection is **automatic**: `scripts/live_demo.py` fills its window buffer,
  then scans candidate bins across the 0.8–1.4 m gate
  (`protocol.subject_distance_m` in `scripts/live_demo_config.yaml`) and locks the
  best-scoring bin for the rest of the session. **No manual/manifest bin pin.**
- Warmup latency before HR appears ≈ **~30 s** — the time to fill one full
  `window_s` (30 s x 20 fps = 600 frames) buffer for the first estimate. This is
  set by the DSP window length (which fixes HR frequency resolution at ~2 bpm);
  do not shrink `window_s` to speed warmup, as it coarsens accuracy and the bin
  lock. There is no extra pre-buffer settle delay — HR appears one window after
  the stream starts. The subject must be seated and still from before the run
  starts through the end.
- If warmup reports **`selected_confidence == "low"`**, record the flag but do **not** retry or
  replace a `representation_validation` or `final_evaluation` session. Continue the session and
  count every complete window. Separately labelled engineering/development diagnostics may repeat,
  but never enter validation/final denominators. See `notes/analysis_prespec.md` §6 item 7.

## Equipment checklist (before each session)

### Radar
- [ ] IWR1642BOOST powered, USB connected; DCA1000EVM powered, Ethernet connected
- [ ] PC static IP set to 192.168.33.30
- [ ] Chirp profile loaded/verified (32 chirps/frame, 20 Hz frame rate)
- [ ] Sensor level and pointed horizontally at the seated chest
- [ ] Measure and record radar-to-chest distance (must be within 0.8–1.4 m)
- [ ] **Scene behind/beside the chair clear** of large flat or metallic surfaces
      within ~3 m; **write down what is there** (see "Fixed conditions"). If the
      room has been rearranged since the last session, say so explicitly in the
      session notes — this drifted unrecorded between 2026-07-13/14 and 2026-07-28.

### Masimo MightySat
- [ ] **Battery charged / in good state** — it is a spot-check device; confirm it will run the full
      10-min session without dropout (M3R-34 disposition)
- [ ] Clipped to finger; PR (Beats/min) and Perfusion Index stable and updating
- [ ] Logging/CSV export active
- [ ] Sensored hand resting on the leg — will not move during recording

## SETTLE CRITERION — mandatory for natural, paced and diagnostic captures

> **Added 2026-07-14 after a measured failure.** In the 2026-07-13 smoke run the subject's PR ran
> **75 → 94 → 65 bpm over the first 25 s of recording** (PI 7–10, so the excursion was **real**,
> not a sensor artefact). The radar's first analysis windows straddle that transient, which makes
> their reference value **undefined** (see `notes/comparator_prespec.md`). Those windows are now
> excluded by the comparator — i.e. **an unsettled subject silently destroys the start of the
> session.** "Confirm PR is stable" was too vague to prevent it.

**Do not start the radar capture until BOTH hold, measured on the live Masimo:**

1. **PR spread ≤ 5 bpm** (max − min) over a **continuous 60 s**; and
2. **no monotonic drift** — the PR in the last 20 s differs from the first 20 s by ≤ 3 bpm.

Seated settling typically takes **2–3 minutes**. Budget it. If the criterion is not met within
5 minutes, **abort and re-seat** — do not record and hope.

**This applies to natural, paced and diagnostic captures alike.** Recovery uses its explicit
post-exertion start rule below; that is the only study-arm exemption.

Record in `HISTORY.md`: settle duration, and the PR at the moment recording started.

## Session steps

1. Seat the subject in the fixed posture; measure/record the distance.
2. Confirm the Perfusion Index is adequate (low PI segments are treated as unreliable reference —
   flag, do not chase; see CLAUDE.md §4).
3. **Wait for the SETTLE CRITERION above.** This is a gate for natural/paced/diagnostic captures.
   For recovery, follow the recovery section's explicit post-exertion start rule instead.
3a. **CLOCK SYNC (mandatory, agreement-blind).** Before recording, synchronise the PC and the
   Masimo phone to a common NTP time source and **record both clock offsets** in the session log.
   The PC↔phone offset must be **within ±1 s**; re-check at session end for drift. If it exceeds
   ±1 s, **resync and restart before recording**. **Never** choose or adjust a time offset by
   looking at radar–reference agreement (CLAUDE.md §4). The radar frame-0 epoch is taken from the
   synchronised PC UTC clock (`notes/analysis_prespec.md` §7).
4. Start Masimo logging; note the start wall-clock time.
5. Start the radar capture (`scripts/live_demo.py`). Wait out the **~30 s** warmup
   (one full `window_s` buffer at 20 fps; see "Warmup & bin lock" above — this file
   previously said ~40 s in this step, which contradicted its own derivation).
6. Subject stays still and breathes **naturally** (or to the metronome) for the full recording.
   No talking, no posture shifts.
7. Stop the radar capture, then stop Masimo. Note end time and final PR.

---

## Diagnostic arm — the STEPPED breathing-rate capture (do this before the study)

> **Purpose: this single capture unblocks the two things nothing else can.** It is a *method
> development* capture, not a study session.

**Why it is needed.** Both candidate harmonic-rejection mechanisms are dead (see
`notes/plan_eca_forbidden_zone.md`). The leading remaining candidate — **temporal continuity**
("a respiratory harmonic *tracks* f_r across hops; a heartbeat does not") — is **untestable on
every capture we own**, because f_r barely moves: **median |Δf_r| = 0.12–0.20 bpm per hop, ~0.4 of
an FFT bin.** You cannot test a discriminant based on *tracking changes in f_r* on data where f_r
does not change.

**Protocol.**

| step | breathing rate | dwell | 4·f_r lands at |
|---|---|---|---|
| 1 | **12 bpm** | 120 s | 48 bpm |
| 2 | **15 bpm** | 120 s | 60 bpm |
| 3 | **18 bpm** | 120 s | **72 bpm — the collision** |
| 4 | **21 bpm** | 120 s | 84 bpm |

Metronome = 2× the target rate. **Settle at 12 bpm per the criterion above, then step without
stopping the recording.** Total ≈ 8 min of recording + settle — within the
approval's 10-minute ceiling, which applies to this arm too.

**What this buys, in one capture:**
- **Continuity becomes testable.** Each 3 bpm step produces **9–18 bpm of predicted harmonic
  motion — 4.5 to 9 FFT bins**, versus ~0.4 bins today. That is the leverage the discriminant needs.
- **A better collision fixture than paced-18 alone.** The 4th harmonic **sweeps 48 → 84 bpm and
  crosses the heart rate**, giving the collision as a **continuous offset sweep** rather than a
  single point — which is what is actually needed to find where the method breaks.

**Rules:**
- **Record the Masimo PR at every step.** Pacing *changes* HR (the paced-16 run pushed this subject
  from ~65 to 72 bpm), so the collision point **moves**. Log |HR − 4·f_r| at each step.
- **Windows that straddle a step transition are excluded and reported** — f_r is ill-defined across
  a step, and those windows would poison both experiments.
- Everything else (posture, distance, hands, warmup, stillness) is unchanged.

---

## HR dynamic-range arm — the seated RECOVERY capture

> **STATUS: ANALYSIS CONTRACT REVIEWED; AUTHORIZATION OWNER-ATTESTED 2026-08-09.** The amendment
> to **`24IBEC051`** was approved on 2026-08-03 and covers 15 new prospective participants in
> addition to existing development subjects A–D. The determination is confidential and held by the
> researcher/PI; consent/PIS records are private between the researcher and participants and are
> intentionally not stored here. Submission text: `notes/ethics_amendment_hr_recovery.md`
> (PI Slim Alouini; submitting researcher Jose Maria Sosa; project *Contactless Heart-Rate
> Estimation with a 77 GHz FMCW Radar*). Parent approval: `24IBEC051`; original approval date
> recorded in the submission: 25/05/2026; request date: 25/07/2026; issuing board currently
> recorded only as `IBEC, KAUST`.

### Why this arm has to exist

**Measured 2026-07-31** (`scripts/diagnose_signal_presence.py`, evidence
`results/diagnose/signal_presence/20260731T155946Z/`, write-up in `HISTORY.md`): across all
8 existing captures the within-session Masimo PR spread (p10–p90) is **2.6–5.2 bpm**, which
is **narrower than the ±5 bpm agreement tolerance**. A predictor that ignores the radar
entirely and emits the session-median PR scores **83–100%** on those captures.

That is not a statement about the estimator. It is a statement about the data: **these
sessions cannot distinguish a working HR estimator from a stub that returns 85 bpm.** Any HR
acceptance criterion validated on them is unfalsifiable, and collecting the former two-arm
10-subject design unchanged would reproduce the defect rather than test tracking.

The BR side already solved this. The stepped sweep arm above deliberately moves the
breathing rate 12 → 21 bpm, and it is the **only** capture in which BR tracking has been
demonstrated (permutation `p = .001`, Spearman +0.56 at the locked bin). **This arm is the
HR equivalent, and nothing else in the protocol supplies it.**

### The tension this arm must resolve, and the only shape that resolves it

Two existing requirements pull in opposite directions, and both are correct:

* the **SETTLE CRITERION** above forbids starting a capture while PR is drifting
  (last-20 s vs first-20 s ≤ 3 bpm), and
* the comparator admits a window only if its within-window PR spread is
  **≤ 5 bpm** (`src/comparator.py:_HR_STATIONARITY_MAX_BPM`) over ≥ 24 usable samples —
  a 30 s window whose HR swings wildly has no well-defined single truth value.

Between them they guarantee HR stationarity, which is precisely what removes falsifiability.
They are reconcilable in exactly one shape: a **slow monotonic ramp**. Each 30 s window stays
locally stationary (spread within tolerance, so it is admissible), while the *session* spans
a wide PR range. Post-exertion seated recovery is that shape — HR decays quickly at first,
then slowly, over several minutes.

**Rejected alternatives, and why — do not re-propose these without new argument:**

| candidate | why it fails |
|---|---|
| **Slow paced breathing (~6 bpm) to drive RSA** | Wrong axis: RSA oscillates HR *within* the window, inflating within-window spread past the 5 bpm gate and making windows **inadmissible** rather than adding across-session range. It also breaks BR outright — 6 bpm = 0.10 Hz is the exact bottom edge of `respiration.band_hz`, which the M2 `resp_edge_veto` permanently invalidates (HANDOFF §5). Fails twice. |
| **Natural day-to-day / time-of-day variation** | Real but uncontrolled and *between* sessions, not within one. Does nothing for a per-session agreement claim, and cannot be commanded or logged as a design variable. |
| **Cold pressor / Valsalva** | Larger HR excursion, but adds discomfort and a materially higher risk profile for no advantage over recovery. Would need the same ethics amendment and more. |

### Recovery procedure recorded in the submitted amendment

1. Set up and verify the room exactly as "Fixed conditions" and the equipment checklist
   require. Do this **before** the exertion, so the subject sits down into a ready rig.
2. **Exertion, away from the radar: self-paced step-ups**, in the laboratory, on a dry
   unobstructed surface, in suitable footwear, with the researcher present and the Masimo worn
   throughout. Raise PR to **100–120 bpm**, confirmed on the live Masimo. **Expected duration
   under 4 minutes.** Stop on the Masimo reading, never on a fixed repetition count — the
   target is a heart rate, not a dose of exercise. The subject sets the intensity and may stop
   at any moment without giving a reason.
   - **Stopping rules (researcher halts immediately):** chest pain or tightness,
     light-headedness or faintness, disproportionate breathlessness, palpitations, nausea,
     visible distress — or on subject request.
   - PR and SpO2 stay visible on the Masimo throughout exertion and recovery.
3. Seat the subject in the fixed posture and clip the Masimo. **Record the seated PR at
   t = 0.**
4. **The SETTLE CRITERION above is deliberately NOT applied to this arm** — it forbids exactly
   the monotonic drift this capture exists to create. It is replaced by: start recording once
   the subject is in the fixed posture, still, and hands are resting; typically < 30 s after
   sitting.
5. Record the standard **10 minutes** (the approval ceiling applies to this arm too), subject
   still and breathing naturally throughout. No pacing — pacing moves HR (documented above)
   and would confound the ramp with a respiratory effect.
6. Everything else — posture, distance, hands, scene, warmup, clock sync (step 3a), stillness
   — is unchanged.
7. **The subject stays seated and monitored until PR is within 5 bpm of their pre-exertion
   resting value before leaving.** Record that value and the time taken.

### Screening and exclusions — mandatory for this arm

The submitted amendment and project protocol specify the screening below. These are
**additional** to the existing non-exertional protocol.

- [ ] **PAR-Q+** (Physical Activity Readiness Questionnaire for Everyone) completed **before**
      any exertion. **Any positive response excludes the subject from this arm** — they may
      still take part in the natural and paced arms.
- [ ] **Exclusion criteria:** known cardiovascular, respiratory or musculoskeletal condition;
      current pregnancy; any medication affecting heart rate; acute illness on the day; any
      condition making the exertion inadvisable.
      > Beta blockers in particular are excluded on **two** grounds — they raise the risk and
      > they flatten the very HR response this arm depends on, so an included subject on them
      > would likely fail the adequacy criterion anyway.
- [ ] Researcher present for the whole session.
- [ ] Consent form and participant information sheet **in their amended versions** (§9 of the
      submission: exertion description, risks, right to decline this arm while remaining in
      the study, screening and what a positive response means, and that no fitness measure is
      recorded or reported).

> **Wording note, harmless but worth knowing.** The approved submission's exclusion list says
> "any condition making **stair climbing** inadvisable" while the agreed modality is
> **step-ups** (§6 of the same document). The intent is plainly the same class of exertion.
> The approved wording is left as approved — **do not edit the submitted document** — and the
> operational criterion is read as "the exertion in §6". Raise it with the board only if they
> ask.

**Expect the first few windows to be excluded and that is correct, not a failure.** Early
recovery decays fastest, so those windows legitimately breach within-window stationarity. The
usable evidence is the slower mid-to-late ramp, which is both admissible and wide.

### Recovery adequacy and evidence floor

The complete executable contract is `notes/analysis_prespec.md` §2c. In summary, Stage 1 is
reference-only. Let `R_s` be the full-precision finite `median_pr_bpm` values from complete
HR-reference-admitted recovery windows. Require:

1. `|R_s| ≥ 10`;
2. `max(R_s) − min(R_s) ≥ 20.0 bpm`;
3. with `c_s` the ordinary numeric median of `R_s` (mean of the two centre values for even `n`,
   no rounding), `sum 1(|R_s − c_s| ≤ 5.0) / |R_s| < 0.5`.

Count gates run before median/range/division; empty values are recorded as null, not NaN/Inf.
Stage 1 may use Masimo plus fixed grid/timebase/integrity metadata only — no radar samples,
radar-validity result, estimator output or agreement.

After scoring, Stage 2 requires at least four jointly evaluable recovery windows and applies the
same bit-for-bit `c_s` to that subset, again requiring a hit rate strictly below 0.5. Exactly 0.5
fails. Stage 2 governs headline eligibility only; it never filters analysis rows. At least 8 of the
fixed 10 final subjects must pass both stages for a recovery population headline, in addition to
the arm estimability and precision rules.

**No adequacy/yield retry.** A Stage-1 or Stage-2 failure is retained, counted and reported. It
does not trigger recapture, replacement, estimator change or threshold change. A Stage-1-failing
session is scored once after its reference-only status is sealed, for descriptive radar coverage
and results, but never enters the recovery LoA estimand.

**Calibration check, 2026-07-31.** The criterion was evaluated against all 8 existing
captures, from their Masimo CSVs alone. **All 8 fail**, which is the intended behaviour — it
is the defect they exhibit. Over *comparator-admissible* windows only:

| capture | admissible windows | PR span (bpm) | constant-predictor hit |
|---|---|---|---|
| massimo1 | 5 | 1.0 | 100% |
| massimo2 | 5 | 3.0 | 100% |
| sweep | 8 | 7.0 | 100% |
| massimo3 | 11 | 8.0 | 100% |
| massimo4 | 13 | 4.0 | 100% |
| massimo5 | 9 | 2.5 | 100% |
| massimo6 | 12 | 5.0 | 100% |
| massimo7 | 4 | 3.0 | 100% |

Criterion 1 (≥ 10 windows) already passes on three captures, so it is not the discriminator;
**criterion 3 is**, and it fails at 100% on every capture — a constant predictor is never
wrong on any admissible window we own. Restricting to admissible windows makes the picture
*worse* than the whole-session figure (spans of 1.0–8.0 bpm against 2.6–5.2 p10–p90), because
admissibility itself selects for stationarity.

### Gates

- [x] **Independent analysis-contract review.** The required `task_breakdown` and independent
      `plan_reviewer` reviews completed 2026-08-09. Substantive verdict:
      **READY WITH MINOR CHANGES**; all minor changes were incorporated. Record:
      `plans/m0_recovery_contract_cross_review.md`.
- [x] **Three-arm prespec reconciliation.** `notes/analysis_prespec.md` now defines the separate
      recovery estimand, two-stage floor, data roles, subject counts and missing-window rules.
- [x] **Authorization/enrollment attestation.** Owner confirmation 2026-08-09 records approval on
      2026-08-03 under parent reference `24IBEC051`, exactly 15 new prospective participants in
      addition to A–D, and confidential determination/consent/PIS records held outside the repo.
      No participant replacement beyond the approved 15 is assumed.

**M0 verdict: READY.** The mental-arithmetic fallback is not authorized as a substitute for the
reviewed recovery arm.

---

## Files & logging

- The live demo writes everything to `results/live_demo/<run_dir>/`
  (raw ADC mirror, `live_estimates.csv`, `live_intermediates.npz`,
  `warmup_bin_selection.json`, `run_metadata.json`). In prospective mode it additionally seals the
  exact config/sidecar bytes, frame-validity map, packet counters, raw/config hashes, clean capture
  commit, exact frame-0 start-assignment UTC, and `sealed_radar_receipt.json`.
- Prospective sessions do **not** use `data/manifest.local.csv`. Follow
  `notes/m2_capture_runbook.md`: register the sealed receipt in the next immutable cohort-registry
  revision, then run the separate no-overwrite finalizer after the end-clock measurement and Masimo
  export. The finalizer emits the version-3 scoring manifest.
- Maintain the committed, SHA-256-bound cohort registry required by
  `notes/analysis_prespec.md` §3.1. It records subject ID, immutable role and slot, paced rate,
  session IDs/hashes and label-access state. All sessions from one subject use the same role.
- Record privacy-safe capture facts in HISTORY.md: subject pseudonym, arm, distance, duration,
  settle or recovery-start facts, disturbances, and sealed receipt/registry hashes. Do **not**
  record a prospective PR range or any other reference outcome before the authorized label-firewall
  transition. Finalization and preflight may copy, hash, and bind opaque reference bytes; they do
  not authorize parsing, inspection, summarization, or scoring.

---

## Resolved / remaining decisions

**Resolved by M0:**

- three separate natural, paced and recovery HR estimands; no pooled headline;
- natural+paced and recovery evidence floors remain separate;
- existing A–D development-only; first five prospective slots representation validation; next ten
  final evaluation; no subject or correlated session crosses roles;
- final paced allocation 4/3/3 and validation allocation 2/2/1;
- no recovery/yield/warmup retry; only objective technical admission failures may be re-captured;
- every complete grid window and expected estimator/vital/config key is materialized in the ledger;
- study capture path remains `scripts/live_demo.py`, which mirrors `adc_stream.bin` and emits the
  required diagnostics. The standalone `steps/step_1/capture.py` is not the study path.

**Authorization/privacy record:** the recovery determination exists and is held privately by the
researcher and PI. Participant consent/PIS records remain private between the researcher and each
participant. Approval covers exactly 15 new prospective participants in addition to A–D; no extra
participant replacements are assumed.

M0 is documentation-only and now ready. **Do not infer authorization for M1 implementation or
capture execution from this status; those actions require their own milestone authorization.**
