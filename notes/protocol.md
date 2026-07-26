# Capture protocol — seated, warmup auto-lock

> Single agreed capture protocol for all post-2026-07-09 sessions. All earlier
> (supine / mixed-protocol) captures were retired on 2026-07-09 — see HISTORY.md
> "Hard reset of all datasets". Keep this file, `scripts/live_demo_config.yaml`,
> CLAUDE.md, and `notes/approach.md` consistent; if the protocol changes, change
> all four in the same commit.

---

## Study design

- **10 subjects, 2 sessions each** (20 sessions total).
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
- **Sessions are on different days** (not back-to-back). This reduces within-day
  fatigue/carryover but adds day-to-day and time-of-day HR variability: **record
  the time of day** for every session and **re-measure the radar-to-chest
  distance** each day (a new setup each visit).
- **Order is fixed** (natural first, then paced) — record as a study limitation
  (no counterbalancing).
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

## Files & logging

- The live demo writes everything to `results/live_demo/<run_dir>/`
  (raw ADC mirror, `live_estimates.csv`, `live_intermediates.npz`,
  `warmup_bin_selection.json`, `run_metadata.json`).
- Add the session as a row in `data/manifest.local.csv`.
- Record the capture in HISTORY.md: subject, distance, duration, observed PR
  range, and any disturbances (CLAUDE.md reproducibility rule §6).

---

## Resolved / remaining decisions

Resolved (see Study design above): 10 subjects x 2 sessions; session 1 natural,
session 2 paced; **10-min recordings** (2026-07-24, within the approval's 10-min
ceiling); seated 0.8-1.4 m warmup auto-lock.

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
