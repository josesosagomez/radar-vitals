# Capture protocol — seated, warmup auto-lock

> Single agreed capture protocol for all post-2026-07-09 sessions. All earlier
> (supine / mixed-protocol) captures were retired on 2026-07-09 — see HISTORY.md
> "Hard reset of all datasets". Keep this file, `scripts/live_demo_config.yaml`,
> CLAUDE.md, and `notes/approach.md` consistent; if the protocol changes, change
> all four in the same commit.

---

## Study design

- **10 subjects, 3 sessions each** (30 sessions total).
  > **Changed from 2 to 3 on 2026-08-03**, when IBEC approved the exertion amendment. Session 3
  > is the **HR dynamic-range (recovery) arm** — see its own section below for why it exists
  > and what it requires. `notes/analysis_prespec.md` is still frozen at 2 sessions and
  > **must be amended before M0 is deposited**; until then the two files disagree and this one
  > is the newer.
- **Session 1: natural breathing.** Subject breathes normally; no pacing.
- **Session 2: paced breathing** with a metronome at a fixed target rate.
  - Rates: **12, 15, 18 breaths/min**, one steady rate per subject, assigned by
    **enrolment-order rotation** (12 → 15 → 18 repeating), which for 10 subjects
    fixes the allocation at **4 / 3 / 3** (rate 12 takes the extra, tenth, subject).
    This allocation is **frozen before any data is collected** — it defines the
    mixture weights of the paced arm-level LoA (`notes/analysis_prespec.md` §1,
    M3R-31) and must never be chosen post-hoc. Rationale:
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
- Both sessions otherwise follow the identical fixed conditions below.

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

  > **Changed from 5 to 10 minutes on 2026-07-24** — decided *before* the
  > pre-registration deposit, so it is part of the frozen protocol rather than a
  > later amendment. **The ethics approval permits up to 10 minutes** (user-confirmed
  > 2026-07-24; recorded on the user's authority — the approval document itself is
  > not in this repo). Approval **`24IBEC051`**, issuing board **IBEC, KAUST**, covers
  > both collection and publication (user-confirmed 2026-07-25). **Do not exceed 10 minutes.**
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
  > is tolerable, but that subject was actively pacing. **The M5 pilot must verify
  > the locked bin still tracks the chest at minute 9-10** (inspect the run with
  > `scripts/diagnose_live_run.py`); if it does not, the duration comes back down.
  >
  > Storage: ~1.55 GB raw ADC per 10-min session, ~31 GB for the 20-session study.

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
- If warmup locks a poor bin (**`selected_confidence == "low"`** in
  `warmup_bin_selection.json`), re-run **at most once** rather than nudging the subject; if the
  retry is still "low", **record the session anyway** (do not keep retrying). Both attempts and the
  study-level retry incidence are logged; see the exact disposition in
  `notes/analysis_prespec.md` §6 (item 7). Inspect with `scripts/diagnose_live_run.py <run_dir>`.

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

## SETTLE CRITERION — mandatory, every arm, no exceptions

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

**This applies to natural, paced and diagnostic arms alike** (the previous 2-minute settle was
written only under the paced arm, and the smoke tests skipped it).

Record in `HISTORY.md`: settle duration, and the PR at the moment recording started.

## Session steps

1. Seat the subject in the fixed posture; measure/record the distance.
2. Confirm the Perfusion Index is adequate (low PI segments are treated as unreliable reference —
   flag, do not chase; see CLAUDE.md §4).
3. **Wait for the SETTLE CRITERION above.** This is a gate, not a suggestion.
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

> **STATUS: ETHICS-APPROVED 2026-08-03; part of the protocol. Two non-ethics gates remain
> open — see "Remaining gates" below. Do not capture a study session yet.**
> The amendment to **`24IBEC051`** adding brief submaximal exertion was submitted 2026-07-25
> and approved by IBEC, KAUST. Submission text: `notes/ethics_amendment_hr_recovery.md`
> (PI Slim Alouini; submitting researcher Jose Maria Sosa; project *Contactless Heart-Rate
> Estimation with a 77 GHz FMCW Radar*). Determination reference: `[[RECORD — the amendment's
> own approval reference and date, as issued; `24IBEC051` is the parent approval]]`.

### Why this arm has to exist

**Measured 2026-07-31** (`scripts/diagnose_signal_presence.py`, evidence
`results/diagnose/signal_presence/20260731T155946Z/`, write-up in `HISTORY.md`): across all
8 existing captures the within-session Masimo PR spread (p10–p90) is **2.6–5.2 bpm**, which
is **narrower than the ±5 bpm agreement tolerance**. A predictor that ignores the radar
entirely and emits the session-median PR scores **83–100%** on those captures.

That is not a statement about the estimator. It is a statement about the data: **these
sessions cannot distinguish a working HR estimator from a stub that returns 85 bpm.** Any HR
acceptance criterion validated on them is unfalsifiable, and running M6 unchanged would
reproduce the defect across 20 sessions instead of 8.

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

### Protocol (as approved)

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

Approved on condition of the screening below. These are **additional** to the existing
protocol, which carries no cardiovascular exclusions because it never needed any.

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

### Adequacy criterion — pre-specified, so the arm can fail

A capture from this arm is **adequate for HR validation** only if, computed from the Masimo
CSV alone and **before** any radar comparison:

1. **≥ 10 comparator-admissible windows** (`hr_reference(...)["admitted"]`), and
2. the admissible windows' reference PR spans **≥ 20 bpm** (max − min of `median_pr_bpm`), and
3. the **constant-predictor baseline scores < 50%** at ±5 bpm — i.e. emitting the session
   median PR fails on most admissible windows.

Criterion 3 is the operative one: it is the direct negation of the defect found on
2026-07-31, and it is what makes a later HR agreement claim falsifiable. **All three are
computed from the reference only**, so checking them cannot leak radar performance into the
protocol decision (CLAUDE.md §4).

If a capture fails adequacy, record it and re-run the arm — do not weaken the criterion.

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

**Ethics — DISCHARGED 2026-08-03.** Approved by IBEC, KAUST as an amendment to `24IBEC051`.
The screening in "Screening and exclusions" above is a condition of that approval, not a
suggestion. The mental-arithmetic fallback described in §10 of the submission is **moot** and
must not be run in place of the approved arm.

**Two gates remain, and neither is ethics:**

- [ ] **Cross-review (CLAUDE.md §6).** This arm is an experimental-plan change and **has not
      been independently reviewed**. Ethics approval is a safety and consent determination —
      it says nothing about whether the design answers the scientific question. Record the
      outcome here.
- [ ] **Analysis pre-spec edit (`notes/analysis_prespec.md` §1).** The pre-spec says
      **10 subjects × 2 sessions** with arm `a ∈ {natural, paced}`. This arm makes it three.
      It is **not yet frozen** (the freeze is the user's irreversible M0 act), so this is a
      **pre-freeze edit, not a §4 amendment** — no new version DOI is implied. It still needs
      CLAUDE.md §6 cross-model review, since the completed M3 review covered the 2-arm design.
      A dated PENDING banner in that file makes the contradiction visible; **this file is the
      newer one and governs what is captured.**
      > **Watch the evidence floor.** This arm is deliberately non-stationary while HR
      > admissibility requires within-window PR spread ≤ 5 bpm, so early-recovery windows will
      > legitimately fail. Whether the §2a/§2b floor is arm-specific must be decided **before**
      > the freeze — a floor adjusted after seeing this arm's yield is not a floor.

**The M0 gate is unaffected and still stands.** `HANDOFF.md` §4: no study capture may be taken
until the pre-registration is deposited, and that gate sits before M5. Ethics approval permits
this arm; it does not lift M0. **Nothing about 2026-08-03 makes a study session capturable.**

---

## Files & logging

- The live demo writes everything to `results/live_demo/<run_dir>/`
  (raw ADC mirror, `live_estimates.csv`, `live_intermediates.npz`,
  `warmup_bin_selection.json`, `run_metadata.json`).
- Add the session as a row in `data/manifest.local.csv`.
- Record the capture in HISTORY.md: subject, distance, duration, observed PR
  range, and any disturbances (CLAUDE.md reproducibility rule §6).

---

## Resolved / remaining decisions

Resolved (see Study design above): **10 subjects x 3 sessions** — session 1 natural,
session 2 paced, session 3 recovery (added 2026-08-03, ethics-approved); **10-min recordings**
(2026-07-24, within the approval's 10-min ceiling); seated 0.8-1.4 m warmup auto-lock.

**Capture tool — resolved:** raw `.bin` is recorded by `scripts/live_demo.py` in
live mode (mirrors the raw ADC to `adc_stream.bin`). **This is the study capture
path.** A second, standalone headless capture tool also exists —
`steps/step_1/capture.py`, which writes `data/raw/<id>.bin` plus metadata with a
SHA-256 and a manifest row, and was verified end-to-end on 2026-07-22 (HISTORY.md,
session 9). *(This file previously said "there is no standalone capture script",
which was false.)* **Use `live_demo.py` for study sessions** — it is the path that
produces the diagnostics the analysis depends on.

**Paced breathing rates — resolved:** 12 / 15 / 18 bpm, rotated across subjects
(see Study design), **for the M6 study arm**.

**Collision-provoking capture (M7) — approved, design still open.** The ethics
approval covers a **subject-specific** paced rate chosen so 4 x f_r lands on the
subject's HR (user-confirmed 2026-07-24). This is a *method-development* capture,
separate from the 12/15/18 study rotation above. **Its rate must NOT be computed
from resting HR:** pacing moves HR — this subject went ~65 -> 72 bpm when paced,
and in the sweep session ran 80-88 bpm, which is why that capture's real collision
landed on the **21 bpm** step rather than the designed 18 (HISTORY.md 2026-07-14).
Set the rate from HR measured *during* a short paced warm-up, or bracket a narrow
range around the prediction. Full design lives in `plans/implementation_plan.md` M7.

**HR dynamic range — OPEN, and it constrains what M6 can claim.** Measured 2026-07-31: the
within-session PR spread of all 8 existing captures (2.6–5.2 bpm) is narrower than the
agreement tolerance, so those sessions cannot falsify an HR claim — a constant predictor
scores 83–100% on them. The **seated recovery arm** proposed above is the fix, and it is
**blocked on an ethics amendment** because it adds exertion. Two decisions are outstanding:
(1) whether to seek that amendment or accept the weaker mental-stress fallback; and
(2) whether the M6 sessions themselves should carry a ramp segment, or whether the ramp stays
a separate method-development arm. **Until one of these lands, an HR acceptance criterion
cannot be validated on any data this study will produce** — a BR criterion is unaffected and
can proceed on the stepped-sweep evidence.

**Open, and blocking the pre-registration deposit (M0):**
- **The evidence floor** — the minimum number of evaluable (radar-accepted *and*
  comparator-admissible) non-overlapping windows per session and per subject, and
  the agreement precision the study claims. Deferred by the user 2026-07-24, but it
  must be fixed **before** M0 freezes: a floor chosen after seeing the pilot yield
  is not a floor.
- **Ethics approval** — reference **`24IBEC051`**, issuing board **IBEC, KAUST**
  (user-confirmed 2026-07-25; covers collection *and* publication). For the Methods
  section, confirm the full formal expansion of "IBEC" as it appears on the approval.

Next action is a live hardware smoke test on yourself before running any subject
(`plans/implementation_plan.md` M1).
