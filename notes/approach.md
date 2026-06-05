# Approach — mmWave Heart-Rate Estimation

> Fill this in BEFORE writing pipeline code (research-first). Use the arXiv MCP to find
> references; have ChatGPT review the plan for what a reviewer would attack.
> Keep it honest: mark `[CITATION NEEDED]` rather than guessing.

## 1. Problem
Estimate heart rate from a 76-81 GHz FMCW radar (IWR1642), subject seated/standing at
1.3-1.6 m, radar facing the chest. Reference: Masimo MightySat pulse rate.

## 2. Physical principle
Heartbeat and respiration cause sub-millimetre chest displacement. FMCW radar resolves
the chest into a range bin; the displacement shows up as a slow-time phase change at that
bin. Heart rate is recovered from the phase signal's spectral content in the cardiac band.
- Range resolution dR = c / (2 * B_sweep). Confirm the chest at 1.3-1.6 m lands in a
  clean bin given your chirp bandwidth. `[fill from chirp config]`
- Slow-time (phase) sample rate = frame rate. Target ~20 Hz. `[fill from frame config]`

## 3. Candidate pipeline (baseline to reproduce first)
Standard phase-based chain — implement and verify each stage in isolation:
1. Parse raw ADC (IWR1642 non-interleaved 2's-complement). `[CITATION NEEDED: TI raw data app note]`
2. Range FFT (fast time) -> complex range profile per chirp/frame.
3. Static clutter removal (subtract slow-time mean per bin).
4. Range-bin selection within the 1.3-1.6 m gate (max energy / max phase variance).
5. Phase extraction + unwrapping at the chosen bin.
6. Phase differencing / impulse-noise removal to suppress drift and motion spikes.
7. Bandpass filter -> cardiac band (~0.8-2.0 Hz = 48-120 bpm).
   (Respiration band ~0.1-0.5 Hz, kept for the breathing cross-check.)
8. Spectral estimation over a sliding window (FFT peak, or autocorrelation) -> bpm.
9. Respiration-harmonic suppression: heartbeat can collide with respiration harmonics —
   note the mitigation used. `[CITATION NEEDED]`

## 4. Known pitfalls (the things that cause wrong readings)
- Wrong range-bin pick (locks onto clutter or a non-chest reflector).
- Respiration harmonics leaking into the cardiac band and winning the peak search.
- Spurious/noise spectral peaks when SNR is low.
- Large body motion corrupting the window (gain-control / segment removal needed).
- Posture change (sit vs stand) shifting the chest range bin mid-capture.

## 5. References to gather (use arXiv + Zotero)
- [ ] TI application note: vital signs with mmWave sensors `[CITATION NEEDED]`
- [ ] TI raw ADC data capture / DCA1000 data-format app note `[CITATION NEEDED]`
- [ ] Foundational FMCW phase-based vital-signs papers `[CITATION NEEDED]`
- [ ] Recent (2023-2026) mmWave HR papers; note their metrics + distances `[CITATION NEEDED]`
- [ ] Survey of respiration-harmonic / clutter handling methods `[CITATION NEEDED]`

## 6. Evaluation plan
- Metric: HR error vs Masimo PR — MAE (bpm), RMSE (bpm), Bland-Altman limits.
- Conditions: sit vs stand; 1.3 vs 1.6 m; multiple subjects.
- Quality gate: exclude/flag Masimo segments with low Perfusion Index.

## 7. Chosen plan for the first experiment (exp001)
`[Write the concrete first cut here once the above is reviewed.]`
