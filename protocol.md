# Re-capture protocol — caps 3, 4, 5
# Radar vitals project — harmonic exclusion development dataset

## Overview
Three new captures to support harmonic exclusion tolerance tuning.
Same subject, same radar hardware, same config as cap1/cap2.
Posture: supine on floor, radar mounted vertically above chest at ~1.3 m.
This eliminates postural sway and range-bin drift — cleaner signal than
sitting captures.
Total session time: ~45 minutes including setup and rests between captures.

---

## Equipment checklist (do before anything else)

### Radar
- [ ] IWR1642BOOST powered and connected via USB
- [ ] DCA1000EVM powered and connected via Ethernet
- [ ] PC static IP set to 192.168.33.30
- [ ] mmWave Studio open, chirp profile loaded and verified
      (32 chirps/frame, 20 Hz frame rate, same profile as cap1/cap2)
- [ ] Radar mounted on tripod or stand, aimed straight down
      (not angled) at chest position on floor
- [ ] Measure vertical distance from radar face to floor mat: 1.519 m
      record this number — chest surface will be ~5–10 cm above floor
      so effective radar-to-chest distance ≈ mount height − 0.07 m
- [ ] Target radar-to-chest distance: 1.2–1.4 m (1.15 m + 0.12 m = 1.27 m)
- [ ] Test frame capture — confirm range profile shows subject peak
      at expected bin before each recording (1.22 m)
- [ ] Confirm radar is level (not tilted) — use phone spirit level if
      available

### Masimo MightySat
- [ ] Clipped to finger, SpO2 and PR reading stable
- [ ] BR (Breaths/min) visible and updating
- [ ] Confirm device is logging (check CSV export is active if applicable)
- [ ] Arm resting at side — do not hold phone or move hand during recording

### Metronome
- [ ] Metronome app open on phone
- [ ] Phone placed where audio is audible while lying down but screen
      does not need to be watched during recording
- [ ] Set to correct BPM for each capture (see per-capture instructions)
- [ ] Test: breathe with metronome for 2 minutes before each capture
      and confirm Masimo BR stabilises in target range

### Environment
- [ ] Floor mat or thin yoga mat laid out below radar
- [ ] Room quiet — no walking around during recording
- [ ] No fan or HVAC airflow directly at subject during recording
- [ ] Phone/metronome placed to side so arm stays still

---

## Breathing control method

Each breath cycle = one inhale + one exhale.
Metronome marks individual beats, not full cycles.
Set metronome to half the target breath rate so each beat = one inhale
or one exhale phase.

| Target BR (bpm) | Metronome BPM |
|---|---|
| 12 | 24 |
| 13 | 26 |
| 14 | 28 |
| 15 | 30 |
| 16 | 32 |
| 17 | 34 |
| 18 | 36 |

Technique: inhale for one beat, exhale for one beat.
Do not hold breath between phases — keep it natural and smooth.
When supine, diaphragmatic (belly) breathing is easiest to sustain at
slow rates — let the abdomen rise on inhale rather than the chest.
If 12–13 bpm feels forced, start at 15 bpm and slow down over 2 minutes.

---

## Setup procedure (do once at the start of the session)

1. Place floor mat directly below the radar mount.
2. Lie down in the supine position. Arms at sides, legs flat.
3. Have an assistant (or use a measuring tape fixed to the tripod)
   confirm the radar face is 1.2–1.4 m above your chest surface.
4. Record the exact measured distance in your session notes.
5. Run a test frame capture in mmWave Studio. Confirm the strongest
   range bin corresponds to your chest distance.
6. Do not move the radar mount between captures — keep the same position
   for all three captures so range is identical across the session.

---

## Pre-capture stabilisation procedure (same for all three captures)

1. Lie down in the supine position on the mat.
2. Start metronome at the target rate for this capture.
3. Breathe with the metronome for **2 full minutes**.
   Diaphragmatic breathing — belly rises, shoulders stay still.
4. Watch Masimo BR (glance, do not hold head up). It updates every
   few seconds.
5. Confirm BR has been **stable in the target range for at least 60 s**
   before proceeding. If not stable after 3 minutes, rest and retry.
6. Note the Masimo PR at this point — resting HR baseline.
7. Once recording starts: do not speak, shift posture, or raise your
   head. Eyes closed is fine and reduces the urge to move.

---

## Cap3-retake

**Condition:** Supine, radar overhead
**Purpose:** Fixes the failed cap3 (f_r ≈ 20 bpm caused 4×f_r ≈ HR)
**Target BR:** 13–16 bpm (metronome 26–32 BPM)
**Target 4×f_r:** ≤ 64 bpm — well below expected HR ≈ 80 bpm
**Radar-to-chest distance:** 1.2–1.4 m (record exact value)
**Recording duration:** 7 minutes 30 seconds (450 s)

### Steps
1. Lie down on mat. Confirm radar position unchanged from setup.
2. Complete pre-capture stabilisation at 15 bpm (metronome 30 BPM).
3. Confirm Masimo BR shows 13–16 bpm for 60 s.
4. Confirm Masimo PR is stable.
5. Note exact time, BR reading, PR reading, radar-to-chest distance.
6. Start Masimo logging.
7. Start mmWave Studio recording.
8. Remain still. Eyes closed. Breathe with metronome throughout.
9. After 450 s, stop mmWave Studio recording first, then stop Masimo.
10. Sit up slowly. Note end time and final BR/PR readings.

### Files to save
- Radar: `cap3_retake_<YYYYMMDD>_<HHMMSS>.bin`
- Radar logfile: `cap3_retake_<YYYYMMDD>_<HHMMSS>_LogFile.csv`
- Masimo: `cap3_retake_<YYYYMMDD>_<HHMMSS>_masimo.csv`
- Move all three to `data/raw/` immediately after capture.

### Pass / abort criteria
- PASS: Masimo BR stays 13–16 bpm for ≥ 80% of the recording.
- ABORT and re-do: BR drifts above 17 bpm for more than 30 consecutive
  seconds, or subject moves significantly (felt or visible on range
  profile).

### Rest before cap4
Sit up. Rest **5 minutes** seated. Normal breathing. Do not rush.

---

## Cap4

**Condition:** Supine, radar overhead
**Purpose:** Development capture at mid-range f_r — tests harmonic
  exclusion at a different 4×f_r position from cap3-retake
**Target BR:** 16–18 bpm (metronome 32–36 BPM)
**Target 4×f_r:** 64–72 bpm — below expected HR, distinct from
  cap3-retake harmonic position
**Radar-to-chest distance:** same mount as cap3-retake (do not move)
**Recording duration:** 7 minutes 30 seconds (450 s)

### Steps
1. Lie down on mat. Radar position unchanged.
2. Complete pre-capture stabilisation at 17 bpm (metronome 34 BPM).
3. Confirm Masimo BR shows 16–18 bpm for 60 s.
4. Confirm PR stable.
5. Note time, BR, PR.
6. Start Masimo logging.
7. Start mmWave Studio recording.
8. Remain still. Eyes closed. Breathe with metronome for full 450 s.
9. Stop mmWave Studio, then stop Masimo.
10. Sit up slowly. Note end time and final readings.

### Files to save
- Radar: `cap4_<YYYYMMDD>_<HHMMSS>.bin`
- Radar logfile: `cap4_<YYYYMMDD>_<HHMMSS>_LogFile.csv`
- Masimo: `cap4_<YYYYMMDD>_<HHMMSS>_masimo.csv`
- Move all three to `data/raw/` immediately.

### Pass / abort criteria
- PASS: Masimo BR stays 16–18 bpm for ≥ 80% of the recording.
- ABORT: BR drifts outside 15–19 bpm for more than 30 consecutive
  seconds, or significant movement occurs.

### Rest before cap5
Sit up. Rest **5 minutes**. Stand and move around if comfortable.
Then lie back down for cap5.

---

## Cap5

**Condition:** Supine, radar overhead
**Purpose:** Held-out validation capture — lowest f_r, maximum
  separation between 4×f_r and HR. Not used for tuning.
**Target BR:** 12–14 bpm (metronome 24–28 BPM)
**Target 4×f_r:** ≤ 56 bpm — well clear of expected HR ≈ 80 bpm
**Radar-to-chest distance:** same mount as cap3-retake and cap4
**Recording duration:** 7 minutes 30 seconds (450 s)

### Steps
1. Lie down on mat. Radar position unchanged.
2. Complete pre-capture stabilisation at 13 bpm (metronome 26 BPM).
   Allow 3 full minutes to stabilise — 12–13 bpm is slow.
3. Confirm Masimo BR shows 12–14 bpm for 60 s.
4. Confirm PR stable.
5. Note time, BR, PR.
6. Start Masimo logging.
7. Start mmWave Studio recording.
8. Remain still. Eyes closed. Diaphragmatic breathing with metronome.
9. Stop mmWave Studio, then stop Masimo.
10. Sit up slowly. Note end time and final readings.

### Files to save
- Radar: `cap5_<YYYYMMDD>_<HHMMSS>.bin`
- Radar logfile: `cap5_<YYYYMMDD>_<HHMMSS>_LogFile.csv`
- Masimo: `cap5_<YYYYMMDD>_<HHMMSS>_masimo.csv`
- Move all three to `data/raw/` immediately.

### Pass / abort criteria
- PASS: Masimo BR stays 12–14 bpm for ≥ 80% of the recording.
- ABORT: BR drifts above 15 bpm for more than 30 consecutive seconds.

---

## Post-session checklist

- [ ] All nine files in `data/raw/`:
      3× .bin, 3× LogFile.csv, 3× masimo.csv
- [ ] File size sanity check — each .bin should be ~480–500 MB:
      verify: file size in bytes / 131072 / 20 ≈ 450 s
- [ ] Write down before opening Claude Code:
      - Radar-to-chest distance (same for all three if mount not moved)
      - Actual BR range observed on Masimo for each capture
      - Actual PR range observed on Masimo for each capture
      - Any aborts or re-takes and why
      - Any disturbances during recording (noise, movement, metronome
        drift)
      - Time of day of session (HR varies across the day)

---

## What to tell Claude Code after the session

Once all files are in data/raw/, open a new Claude Code session and say:

"Three new supine captures completed. Files are in data/raw/:
cap3_retake, cap4, cap5 — all with .bin, LogFile.csv, and masimo.csv.
Radar mounted overhead, same position for all three captures.
Radar-to-chest distance: [X] m.
Observed BR: cap3_retake [X–Y bpm], cap4 [X–Y bpm], cap5 [X–Y bpm].
Observed PR: cap3_retake [X–Y bpm], cap4 [X–Y bpm], cap5 [X–Y bpm].
Please do a read-only orientation pass: verify file sizes, infer frame
counts, check Masimo CSV epoch ranges, and confirm the usable radar
interval overlaps with the Masimo recording for each capture.
Do not run any pipeline yet."