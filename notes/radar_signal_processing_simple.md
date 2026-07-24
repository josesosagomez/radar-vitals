# Radar Signal Processing, From Raw Data To Breathing And Heart Rate

This note explains the radar pipeline in simple words. The big idea is:

The radar watches tiny chest movements. Big slow movement is breathing. Smaller faster
movement is the heartbeat. The code turns raw radar numbers into those two rhythms.

## Short Version

1. The radar sends many tiny radio waves called chirps.
2. The waves bounce off the person and come back to the radar.
3. The DCA1000 records the raw echo data as ADC samples.
4. The code reshapes those raw samples into a radar cube.
5. The code looks at the chest distance bin.
6. The code turns that bin into a movement signal called phase.
7. The code cleans sudden spikes from the phase.
8. The code estimates breathing from the slow rhythm.
9. The code uses the breathing rhythm to help remove breathing from the heart search.
10. The code estimates heart rate from the faster rhythm.
11. The code only reports heart rate when the safety checks pass.
12. The code saves CSV results and debugging signals.

## Step By Step

### 1. Radar sends chirps

The IWR1642 radar sends very short radio sweeps called chirps. Each chirp is like a
quick "ping" that goes out, hits objects, and comes back.

### 2. The chest reflects the signal

The person's chest reflects part of the signal back to the radar. When the chest moves
forward and backward from breathing and heartbeat, the returned signal changes a tiny bit.

### 3. DCA1000 records raw ADC data

The DCA1000 captures the radar's raw ADC samples. These are just numbers at first. They
are not breathing or heart rate yet.

In live mode, packets arrive from the DCA1000. In replay mode, the code reads a saved
`.bin` file. Both paths are turned into the same kind of radar data.

### 4. Raw bytes become complex I/Q samples

The raw file contains little pieces called I and Q. Together, I and Q make one complex
sample. A complex sample keeps both strength and wave angle.

The code also handles the radar's I/Q order setting (`iq_swap`) so the samples are put
back in the right order.

### 5. Samples become a radar cube

The code reshapes the samples into a 4-D radar cube:

```text
frames x chirps x receive_antennas x adc_samples
```

Simple meaning:

- `frames`: time steps, like video frames.
- `chirps`: many radar pings inside one frame.
- `receive_antennas`: the radar ears.
- `adc_samples`: samples inside one chirp, used to find distance.

### 6. The code uses a rolling time window

The live demo keeps a rolling window of recent frames. The current demo configuration
uses a 30-second window and updates every few seconds.

This is like looking at the last 30 seconds of chest movement, then sliding the window
forward to make the next estimate.

### 7. The code picks the chest distance bin

The radar does not just see one point. It sees many distance bins. A bin is like a small
distance slot.

The processing needs the bin where the person's chest is. The demo finds this by itself:
during warmup it looks at every bin in the protocol distance range (0.8 to 1.4 m), scores
each one, and locks onto the best one for the rest of the session. Nobody picks the bin by
hand. The scoring evidence is written to `warmup_bin_selection.json` so the choice can be
checked afterwards.

### 8. Range FFT turns samples into distance bins

Inside each chirp, the code applies a Hann window and then runs a range FFT. This turns
raw samples into distance bins.

Simple meaning:

```text
raw chirp samples -> distance slots
```

Then the code keeps only the chosen chest bin.

### 9. Chest-bin signal becomes phase

At the chosen chest bin, the code looks at the wave angle over time. This angle is called
phase.

When the chest moves, the phase changes. So the phase becomes the main chest-motion
signal.

The code combines information across chirps and receive antennas to make one phase value
per frame.

### 10. Phase is unwrapped or accumulated

Phase can jump from `+pi` to `-pi`, like a clock hand wrapping around from 12 back to 1.
The code fixes this by unwrapping or accumulating phase changes.

The result is a smoother time signal that follows chest motion.

### 11. Sudden phase spikes are cleaned

Radar phase can have sudden bad jumps from noise or motion glitches. The code removes
large impulse spikes before estimating breathing or heart rate.

This does not invent a new rhythm. It only reduces obvious one-frame glitches.

## Breathing Rate Path

### 12. The code searches the breathing band

Breathing is slow. The demo searches the respiration band from the config, currently
around `0.10` to `0.50` Hz.

That is about:

```text
6 to 30 breaths per minute
```

The conversion is:

```text
bpm = Hz * 60
```

### 13. FFT breathing estimate

The code detrends the phase, applies a Hann window, and runs an FFT. The biggest peak in
the breathing band gives one breathing-rate estimate.

Simple meaning:

```text
find the strongest slow rhythm
```

### 14. Harmonic-accumulation breathing estimate

Breathing can also create harmonics, like echoes at 2x or 3x the breathing rhythm. The
code checks whether a breathing candidate is supported by those harmonics.

Simple meaning:

```text
if the slow rhythm is real, its helper rhythms should also make sense
```

### 15. STFT stability check

The code splits the 30-second window into shorter pieces and estimates breathing in each
piece. This checks whether the breathing estimate stays stable over time.

Simple meaning:

```text
do the smaller pieces agree, or is the result jumping around?
```

### 16. Breathing estimates are fused

The code combines the FFT estimate, harmonic estimate, and stability check.

The final breathing result gets a confidence label:

- `high`: methods agree well and are stable.
- `medium`: methods agree enough or the signal is stable enough.
- `low`: not trusted.

If breathing confidence is low, the breathing result is marked invalid and is not used to
guide the heart-rate algorithm.

## Heart Rate Path

### 17. The code searches the heart band

Heart motion is faster than breathing. The demo searches the heart band from the config,
currently around `0.8` to `2.0` Hz.

That is about:

```text
48 to 120 beats per minute
```

### 18. Breathing is used as a helper

If breathing was valid, the code knows the breathing frequency. It uses that frequency to
reduce breathing harmonics before looking for heart rate.

This is important because breathing can be much stronger than heartbeat and can hide it.

### 19. ECA removes breathing harmonics

The code applies ECA, which removes rhythm pieces related to breathing and its harmonics.

The current safe mode skips any breathing harmonic that lands inside the heart band, so
the algorithm does not accidentally remove a real heart signal.

Simple meaning:

```text
remove breathing shadows, but do not erase possible heartbeat
```

### 20. The code finds heart candidates

After breathing cleanup, the code looks for strong peaks in the heart band. These are
candidate heart rates.

It does not automatically trust the biggest peak.

### 21. AHET checks the second harmonic

The heart signal should often have a related peak near 2 times the heart frequency. AHET
checks for that second harmonic.

Simple meaning:

```text
if the code thinks the heart is at f, it checks for support near 2f
```

### 22. Bad heart candidates are rejected

The code rejects a heart candidate if it fails checks such as:

- no second-harmonic search region exists.
- second harmonic is too weak.
- peak prominence is too low.
- a better high-frequency competitor exists.
- peak-to-noise-floor ratio is too low.

If no candidate passes, the heart rate is not reported as valid.

### 23. Verified heart rate becomes the raw HR

If a candidate passes AHET, the code reports it as `hr_bpm_raw`.

If no candidate passes, `hr_bpm_raw` is left blank or NaN, and `hr_valid` is false.

The code also computes a no-ECA fallback heart estimate for debugging only. That fallback
is saved, but it is not shown as a confident heart rate.

### 24. Heart rate is smoothed

When valid raw heart-rate values exist, the live demo keeps a small history and reports a
median-smoothed value.

Simple meaning:

```text
use the middle of the recent good heart numbers so one weird number does not jump the display
```

## Outputs And Debugging

### 25. The demo writes a CSV row

For each processed window, the live demo writes `live_estimates.csv`. Important fields
include:

- `locked_bin`: the distance bin used for the chest.
- `hr_bpm_raw`: verified heart rate for this window.
- `hr_bpm_smooth`: smoothed heart rate.
- `hr_valid`: whether the heart estimate passed checks.
- `fallback_hr_bpm`: diagnostic no-ECA heart estimate.
- `br_bpm`: breathing rate.
- `br_confidence`: breathing confidence.
- `resp_valid`: whether breathing was trusted.
- `candidate_rejection_reason`: why a heart candidate failed, if it failed.

### 26. The demo updates the live display

The display shows recent heart-rate and breathing-rate values over time. Low-confidence
or invalid heart estimates are not treated as trusted heart readings.

### 27. The demo saves intermediate signals

The project saves debugging data in `live_intermediates.npz`, including:

- raw and cleaned phase.
- breathing spectra.
- heart spectra before and after breathing cleanup.
- candidate heart frequencies.
- second-harmonic evidence.
- rejection codes.
- baseline no-ECA spectrum.

This is important because a wrong number can be investigated later instead of guessed at.

### 28. Metadata records the run

Each run also writes metadata, including config, session information, hashes where
available, and packet/capture details. This helps make the run reproducible.

### 29. Masimo is used later for checking accuracy

The radar estimate is made from radar only. Later, for analysis, radar heart rate is
compared against the Masimo `Beats / min` pulse-rate column using the integer Unix
`Timestamp`.

The Masimo data is not used to make the radar number during the live estimate.

## Tiny Glossary

- ADC sample: a raw number recorded from the radar receiver.
- I/Q sample: two numbers that together describe a radar wave.
- Frame: one radar time step.
- Chirp: one quick radar ping.
- Range bin: one distance slot.
- FFT: a tool that finds rhythms or distance slots inside data.
- Phase: the wave angle; tiny chest movement changes it.
- Hz: cycles per second.
- bpm: cycles per minute.
- ECA: breathing-harmonic cleanup before heart-rate search.
- AHET: a heart-rate check that looks for a matching second harmonic.

## One-Sentence Summary

The project turns raw radar echoes into chest motion, finds the slow breathing rhythm,
uses that breathing rhythm to avoid being fooled, and only reports heart rate when the
heart signal passes extra evidence checks.
