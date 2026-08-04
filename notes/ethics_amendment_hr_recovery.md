Amendment request to approval `24IBEC051`

> **RECORD AS SUBMITTED — header added 2026-08-04, body untouched.** This is the text as sent
> to the IBEC board, so it is a record and **must not be edited to match later project
> vocabulary** — the same reasoning that forbids "fixing" the Step 1a bundle digests
> (`HISTORY.md`). Project rules changed on **2026-08-03**: M0 was removed and **nothing in this
> study is pre-registered**. The phrase "pre-specified quality criteria" below therefore
> reflects the submission, not current project vocabulary, and no timing claim derived from
> it may be repeated in any manuscript, talk or spec. The criteria themselves are real and
> unchanged: `notes/comparator_prespec.md` §2.2 (HR stationarity/coverage gates).
> *Submission status was recorded on the user's authority 2026-08-04.*
---

## 1. Administrative

| Field | Value |
|---|---|
| Existing approval reference | **`24IBEC051`** |
| Issuing board | IBEC, KAUST |
| Original approval date | 25/05/2026 |
| Principal investigator | Slim Alouini |
| Submitting researcher | Jose Maria Sosa |
| Project title |  Contactless Heart-Rate Estimation with a 77 GHz FMCW Radar |
| Amendment type | Addition of a procedure (physical exertion) to an approved observational protocol |
| Date of this request | 25/7/2026 |

## 2. Summary of the requested change

We request approval to add **one additional recording condition** to the approved protocol, in
which the participant undertakes **brief, self-paced, submaximal physical exertion** before a
seated recording, so that heart rate declines gradually during the recording rather than
remaining constant.

Everything else about the session is unchanged: the same radar sensor, the same fingertip
pulse oximeter, the same seated posture, the same 10-minute maximum recording duration, the
same data handling. The change is the addition of an exertion step *before* the participant
sits down, and the removal — for this condition only — of the internal requirement that heart
rate be stable before recording begins.

We are requesting this because, as set out in §4, **the approved protocol cannot produce data
capable of testing the study's primary heart-rate claim.**

## 3. What is currently approved

Under `24IBEC051`, participants sit still in a fixed posture 0.8–1.4 m from a
76–81 GHz automotive-class radar sensor (Texas Instruments IWR1642BOOST) for a recording of up
to 10 minutes, wearing a Masimo MightySat fingertip pulse oximeter as a reference. Participants
breathe naturally or in time with a metronome. There is no physical exertion and no
intervention of any kind; the radar is a passive receiver of reflected signal it emits at power
levels not dangerous.

## 4. Scientific justification

This is the substance of the request, so it is stated precisely.

The study's aim is to show that heart rate estimated from the radar **tracks** a clinical
reference. Demonstrating tracking requires the reference to *move*: if a participant's heart
rate is effectively constant throughout a session, then a system that simply reports that
constant will appear to succeed, and a system that genuinely tracks cannot be distinguished
from it.

On **2026-07-31 we analysed all eight pilot recordings collected to date** under the approved
protocol and found exactly that failure:

- Within-session heart-rate spread (10th-to-90th percentile) was **2.6–5.2 beats per minute** —
  **narrower than the ±5 bpm agreement tolerance the study reports against.**
- Restricting to the analysis windows that pass our pre-specified quality criteria, the spread
  was **1.0–8.0 bpm**, and a "predictor" that ignores the radar entirely and always reports the
  session's median heart rate was **correct on 100% of those windows, in every one of the eight
  recordings.**

The consequence is that **no heart-rate result obtained under the approved protocol can be
falsified.** A non-functional system passes. Continuing to the planned 10-participant study
without this change would collect 20 sessions incapable of supporting or refuting the study's
main claim — which we consider a poor justification for asking 10 people to give up their time,
independent of any risk consideration.

The same analysis found the **breathing-rate** side of the study is unaffected and remains
valid: one existing recording in which breathing rate was deliberately varied does demonstrate
tracking. That recording is the model for this request — it works precisely *because* the
quantity being measured was made to change.


## 5. Less-invasive alternatives considered, and why they are insufficient

We did not reach exertion first. Three lower-risk options were evaluated and rejected on
technical grounds:

| Alternative | Why it does not work |
|---|---|
| **Slow paced breathing** to induce respiratory sinus arrhythmia (natural heart-rate variation with the breath) | Produces heart-rate oscillation *within* each short analysis window rather than a change across the session. This makes the reference value for each window ill-defined and causes those windows to be discarded by our pre-specified quality criteria — the opposite of the intended effect. It additionally disables the breathing-rate measurement, because the required breathing rate sits at the exact lower edge of the analysis band. |
| **Relying on natural day-to-day variation** in resting heart rate | Real, but occurs *between* sessions rather than within one. The study's agreement claim is made per session, so between-session variation does not help; it is also uncontrolled and cannot be recorded as a design variable. |
| **Cold pressor test** (hand in ice water) | Produces a comparable heart-rate change but involves deliberate discomfort and a higher risk profile, with no analytical advantage over recovery from exertion. |

A **fourth, lower-risk option** — a seated mental-arithmetic stress task — is described in §10
as a fallback if the board declines the exertion request. It is genuinely weaker, and we would
rather state that plainly than present it as equivalent.

## 6. The proposed procedure

1. The equipment and room are set up and verified **before** the participant exerts, so they
   sit down into a ready apparatus and no time is lost.
2. **Exertion.** The participant undertakes brief, **self-paced** exertion —
   Self-paced step ups — in the presence of the researcher, wearing the pulse oximeter throughout.
   - The endpoint is a **heart rate**, not an amount of exercise: the participant stops when
     pulse rate reaches approximately 100 - 120 bpm, or sooner at their own
     discretion.
   - Expected duration: **under 4 minutes.**
   - This is **submaximal** exertion, comparable to climbing stairs at an ordinary pace. It is
     not a fitness test, there is no performance target, and no measure of the participant's
     fitness is recorded or reported.
3. The participant sits in the standard fixed posture. Seated pulse rate is recorded.
4. **Recording begins once the participant is seated, still and comfortable** — typically
   within 30 seconds. For this condition only, the study's internal requirement that heart
   rate be *stable* before recording is deliberately not applied, because the declining heart
   rate is the point of the condition. This is an internal analysis convention, not a
   safety-related provision.
5. A standard recording of **up to 10 minutes** (the existing approved ceiling, unchanged)
   follows, during which the participant sits still and breathes naturally. Heart rate declines
   toward baseline over this period.
6. The participant remains seated and monitored until pulse rate has returned to within
   5 bpm of their pre-exertion resting value before leaving.

**Number of participants and sessions affected:** A third session for the existing 10 participants.

## 7. Risk assessment

The added risk is that of brief submaximal exertion in a healthy adult — broadly the exertion
of steps ups, which participants undertake in ordinary daily life without supervision. The recognised risks are:

- **Musculoskeletal:** slip, trip, or strain during the exertion.
- **Cardiovascular:** the small but non-zero risk that unaccustomed exertion provokes a cardiac
  event in a person with an undiagnosed cardiovascular condition. This is the risk that
  screening (§8) exists to address, and it is the reason this amendment is being sought rather
  than the change being made unilaterally.
- **Transient discomfort:** breathlessness, light-headedness or fatigue shortly after exertion.

We assess the residual risk after the §8 mitigations as 2, and note that it is not zero. No radar- or
oximeter-related risk changes: emitted power, exposure duration, contact with the participant
and data handling are all unchanged.

## 8. Risk mitigation

1. **Pre-participation health screening.** Every participant in this condition completes a
   validated screening questionnaire — we propose the **PAR-Q+** (Physical Activity Readiness
   Questionnaire for Everyone), or an alternative instrument if
   the board prefers one. Any positive response excludes the participant from this condition;
   they may still take part in the existing non-exertional conditions.
2. **Exclusion criteria** (additional to any already approved): known cardiovascular,
   respiratory or musculoskeletal condition; current pregnancy; any medication affecting heart
   rate; acute illness on the day; any condition making stair climbing inadvisable. The existing
   protocol carries no cardiovascular exclusions because it required none.
3. **Self-paced and self-terminated.** The participant sets the intensity and may stop at any
   moment without giving a reason, consistent with the existing consent.
4. **Stopping rules.** The researcher halts the exertion immediately on any of: chest pain or
   tightness, light-headedness or faintness, disproportionate breathlessness, palpitations,
   nausea, or visible distress — and on participant request.
5. **Continuous monitoring.** Pulse rate and oxygen saturation are visible on the pulse
   oximeter throughout the exertion and recovery.
6. **Supervision.** A researcher is present for the whole session.
7. **Recovery before departure**, as §6 step 6.
8. **Environment.** Exertion takes place in the laboratory, on a dry, unobstructed
   surface, with the participant in suitable footwear.

## 9. Consent and participant information

The participant information sheet and consent form require amendment to add:

- a plain-language description of the exertion step and its expected duration and intensity;
- the risks in §7, in plain language;
- an explicit statement that the participant may decline the exertion condition while still
  taking part in the rest of the study, and may stop at any time;
- the screening questionnaire and what a positive response means (exclusion from this condition
  only);
- confirmation that no fitness measure is recorded, retained or reported.

## 10. Fallback if this request is declined

If the board declines the exertion condition, we would substitute a **seated mental-stress task
(silent serial arithmetic)**, which raises heart rate modestly without physical exertion and
sits within the currently approved seated procedure.

We record honestly that this is **not equivalent**. The heart-rate change is typically
**5–15 bpm and highly variable between individuals**, and may fail to reach the ~20 bpm range
our pre-specified adequacy criterion requires. If it does fail, the consequence is that the
heart-rate component of this work is reported as a **feasibility result rather than a validated
measurement**, and the study's headline claim narrows to breathing rate. We would rather accept
that outcome than overstate what the data support.

## 11. What does NOT change

- Radar hardware, emitted power, sensor-to-participant distance, and exposure duration.
- Maximum recording duration (10 minutes) and seated posture during recording.
- The reference device (Masimo MightySat) and its use.
- Data collected, storage, retention, anonymisation and publication arrangements.
- Participant recruitment route and compensation.
- All existing approved conditions, which continue unchanged.

## 12. Determination requested

We request approval to add the exertion condition described in §6, subject to the screening and
mitigation in §8 and the revised consent documents in §9.
