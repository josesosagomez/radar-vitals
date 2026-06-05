# Session Memory

> Update at the END of every working session. Newest entry on top.
> Three questions: What worked (with evidence)? What failed? What's next?

---

## Current state

- **Where we are:** Project scaffold created. No radar data captured or processed yet.
- **Last verified result:** none.
- **Open question / blocker:** confirm Masimo export granularity and that PC/phone clocks
  agree (both on network time) before first paired capture.

---

## Capture protocol (keep this stable across sessions)

1. Subject sits or stands 1.3-1.6 m in front of the radar; radar at chest height.
2. Attach Masimo MightySat; wait ~2 min until readings stabilize (good PI).
3. Start Masimo logging on the phone; record the PC wall-clock (UTC epoch) at radar start.
4. Capture ~2 min of radar data.
5. Stop both. Export Masimo CSV; move radar .bin and Masimo .csv into `data/raw/`.
6. Note posture, distance, subject ID, settle time, and duration in the log below.

---

## Backlog (what's left)

- [ ] Rung A: flash TI vital-signs lab, confirm plausible HR vs Masimo (hardware bring-up).
- [ ] Confirm Masimo CSV granularity + clock-sync method.
- [ ] Rung B: first paired offline capture; run exp001; inspect overlay plot.
- [ ] Implement real IWR1642 .bin parser (radar_io.py) once chirp config is fixed.
- [ ] Tune distance gate + filter bands from real data.
- [ ] DEFERRED (write-up): quantitative validation — MAE/RMSE + Bland-Altman across
      subjects/postures. Rig is the same; just start logging paired numbers.
- [ ] DEFERRED: validate respiration-band extraction against Masimo `Breaths / min`.

---

## Log (newest first)

### YYYY-MM-DD — Project scaffold created
**Worked:** repo structure, CLAUDE.md context, Masimo parser + compare scaffold, synthetic
vitals test. Evidence: `pytest tests/` passes on synthetic phase.
**Failed / dead ends:** none yet.
**Decisions:** ground truth = `Beats / min`; align on integer Unix-epoch `Timestamp` (UTC);
PI used as a quality gate, not a tuning target.
**Next:** Rung A hardware bring-up.
