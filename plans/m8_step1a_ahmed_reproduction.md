# M8 Step 1a - Ahmed Fig. 8(c)-(d) Reproduction

## Status and claim

This is the approved build authority for a simulation-only, behavioral reproduction of
Ahmed et al., "Discovering the Unseen: Radar-Based Estimation of Heartbeat, Breathing
Rate, and Underlying Muscle Expansion Without Probes," Fig. 8(c)-(d).

The architecture, correctness/edge-case, Python implementation, testing/validation, and
adversarial reviewers all rejected the earlier draft. This revision addresses their
shared blockers: it is a stable plan artifact; separates the equation, prose, and visible
figure suppression behaviors; narrows compatibility to the adapter record; uses native
resolution rather than a zero-padding bin as the scientific tolerance; removes the
invalid 20 Hz audit; requires an independent accumulation oracle; and binds source,
configuration, evidence, and figures with hashes.

The implementation must reproduce and retain the complete harmonic-accumulation curves
under declared assumptions. It must not claim numerical equivalence to the authors'
unpublished implementation and must not tune assumptions to force a successful result.
Step 1b adaptation and `scripts/score_offline.py` integration are out of scope.

## Architecture

- Put the paper-faithful simulation and estimator in `src/m8/ahmed_fig8.py`.
- Put the configuration in `experiments/m8_ahmed_fig8/config.yaml`.
- Use `figures/reproduce_ahmed_fig8.py` as a thin artifact/plotting CLI.
- Do not import or modify `src.respiration`, `run_window_dsp`, or
  `scripts/score_offline.py`.
- Expose an `as_window_estimate`-compatible native record, but describe it only as
  adapter-record compatible.
- Bind each simulated signal and score result to both its model-config hash and a
  signal-realization hash over the standard-normal draw; never combine rates from
  different realizations.

## Primary reconstruction contract

- `c=3e8 m/s`, `fc=6.7e9 Hz`, `fh=80/60 Hz`, `fb=20/60 Hz`.
- `dh=0.01 m`, `db=0.02 m`, equal component amplitudes, and `theta0=0`.
- Duration is five breaths. PRF is five times
  `2*fh*(4*pi*dh/lambda)`. The integer sample count uses `round`.
- Use a fixed fast-time sample and the sum of the two independent equation-(14)
  cosine returns.
- Add real AWGN at 10 dB using the raw combined clean-signal mean square and
  `numpy.random.default_rng(42)`.
- Use an unwindowed, undetrended 4096-point FFT magnitude.
- Estimate the spectral fundamentals at `2*fb` and `2*fh`; convert spectral Hz to
  physiological bpm with `30*q`.
- Sweep breathing over `0 < q <= 2*(25/60)` Hz and heart over
  `2*fb <= q <= 2*(100/60)` Hz.
- Accumulate the mean magnitude at `q, 2q, ..., Hq` for `H=3,5`, requiring every
  harmonic to be supported by Nyquist.
- Resolve exact finite ties to the lowest frequency. Empty or all-ineligible domains
  are invalid.

## Paper ambiguity handling

Run the same simulated signal through three independently reported profiles:

1. `figure_visible_unsuppressed`: no heart-row suppression. This is the figure
   reconstruction because Fig. 8(d) visibly retains the disputed bins.
2. `eq26_multiples_suppressed`: suppress the estimated breathing bin and all integer
   multiples. If this removes the collision target, report
   `inconclusive_by_definition`.
3. `prose_low_or_equal_suppressed`: suppress heart candidates at or below the
   estimated breathing spectral frequency.

Do not silently choose one as the uniquely faithful author implementation.

## Fixed ambiguity audit

Each variant changes exactly one primary field and cannot upgrade the primary verdict:

- sample count: `floor`, `ceil`
- phase: `pi/4`, `pi/2`
- heart-to-breath amplitude ratio: `0.5`, `2.0`, preserving total squared amplitude
- SNR reference power: centered clean signal
- FFT size: `2048`, `8192`
- normalization: literal matrix-eta divisor

Same-length variants reuse the same standard-normal realization. Different lengths use
a seed derived from the base seed and variant ID. A 20 Hz PRF audit is prohibited
because `H=5` is unsupported over the declared heart sweep.

## Evidence and acceptance

Every run writes resolved config, strict JSON metrics and provenance, non-object NPZ
intermediates, PNG/PDF figures, and a running/complete/failed status under a unique
`results/m8_ahmed_fig8/<run_id>/`. A dirty or untracked run remains there and is never
promoted. Only a run using the exact approved default v1 contract and starting from a
clean tree whose required source files are tracked may publish a complete versioned bundle at
`figures/generated/m8_ahmed_fig8/<run_id>/`; `LATEST.json` is updated atomically only
after the entire bundle validates.

The implementation is accepted when:

- unit, oracle, randomized-property, integration, adapter, failure-path, and rendering
  tests pass on Python 3.11;
- all four primary curves (breath/heart by `H=3,5`) are finite, retain their full
  candidate grids, and have unique maxima within native `1/T_effective` resolution of
  the expected spectral fundamentals;
- the corresponding rate error is within `30/T_effective` bpm;
- each target exceeds its runner-up and its `H=3` target accumulation exceeds `H=5`;
- all three suppression profiles and every audit remain separately identifiable;
- a failure is recorded as `not_reproduced_under_declared_assumptions`, never tuned away.

## Reviewed assumptions

The paper does not state the component amplitudes, phase, seed, SNR-power convention,
integer endpoint rule, or a Fig. 8-specific FFT length. FFT magnitude is used because
the printed complex FSC vector has no ordered argmax. Primary normalization divides by
the non-DC harmonic count; the matrix-eta divisor is audited. These are declared
reconstruction assumptions, not claims about unpublished author code.
