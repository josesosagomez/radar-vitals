"""Static clutter removal for a selected range bin's slow-time series.

**Status: implemented, OFF by default, and NOT yet shown to help anything.**
`clutter_removal="none"` is the default everywhere and reproduces the pre-existing
pipeline bit-for-bit. Nothing in the project currently depends on this module; it
exists so the question "does removing static clutter improve coverage?" can be
answered with a measurement instead of an argument. Do not enable it in a scored
configuration until that measurement exists (see HISTORY.md 2026-07-30).

## What this addresses

The value of range bin `k` at slow-time sample `n` is, to first order,

    v[n] = C + A * exp(j * phi(n))

where `C` is the stationary return of everything in that bin that does not move
(wall, chair, equipment, antenna leakage skirt) and `A * exp(j*phi(n))` is the
moving chest. Vital-signs estimation wants `phi(n)`, but `angle(v[n])` is not
`phi(n)`: when `|C| >> |A|` the observed phase excursion is compressed toward zero
and acquires harmonic distortion, because the moving phasor is being read as a
small perturbation riding on a large fixed one.

**`delta_before_mean` does not fix this.** It cancels static *per-channel phase
offsets* — a constant multiplicative factor per (chirp, rx) — which is all its
docstring claims. `C` is *additive* and survives it.

Measured on the 8 canonical captures (2026-07-30), clutter-to-signal ratio at the
locked bin: massimo2 +7.8 dB, sweep +8.7 dB, massimo7 +10.6 dB, versus −11.9 to
−18.9 dB for the other five. In the three clutter-dominated cases the phase
peak-to-peak was 1.3–4.3 rad against 18–40 rad for the rest.

## The caveat that keeps this off by default

A high clutter-to-signal ratio does **not** prove clutter is the problem. A range
bin with no subject in it also has low variance and therefore also looks
clutter-dominated. Removing `C` cannot create a chest that was never there — on an
empty bin it removes the only stable thing present and amplifies noise. So this
module can plausibly improve a bin that holds a compressed subject and plausibly
degrade one that holds no subject, and which of those dominates in practice is an
empirical question nobody has answered yet.

Second-order concern, unquantified: after subtraction the residual phasor can pass
near the origin, where `angle()` is ill-conditioned and a half-turn of phase can
appear from noise alone. `delta_before_mean` averages the conjugate product over
(chirp, rx) before taking an angle, which mitigates but does not eliminate this.
"""
from __future__ import annotations

import numpy as np

#: Accepted values for the `clutter_removal` knob.
#: "none"            — identity. The default; reproduces the pre-2026-07-30 pipeline.
#: "slow_time_mean"  — subtract the per-(chirp, rx) mean over slow time.
CLUTTER_METHODS = ("none", "slow_time_mean")


def remove_static_clutter(
    bin_values: np.ndarray,
    method: str = "none",
) -> np.ndarray:
    """Remove the stationary component from one range bin's slow-time series.

    Parameters
    ----------
    bin_values : (N, chirps, rx) complex
        One range bin's complex value across slow time. N is the analysis window's
        frame count — this function never sees more than the current window, so the
        estimate of `C` is causal within that window and identical whether the caller
        is the live path or the offline harness (M4R-10).
    method : {"none", "slow_time_mean"}
        "none" returns `bin_values` unchanged (the same object, not a copy).

    Returns
    -------
    (N, chirps, rx) complex

    Notes
    -----
    `slow_time_mean` estimates `C` **per (chirp, rx) channel independently**. Channels
    have different fixed gains and phase offsets, so a single pooled mean would leave a
    per-channel residual that is itself static — exactly the term this is removing.

    The mean is taken over the analysis window, so a component slower than the window
    is indistinguishable from `C` and is removed with it. At the 30 s production window
    this suppresses anything below ~0.033 Hz (2 bpm), which is far under the 0.10 Hz
    respiration search-band floor — but it is a real high-pass, not a neutral operation,
    and it is why the window length and this knob cannot be reasoned about separately.
    """
    if method not in CLUTTER_METHODS:
        raise ValueError(
            f"clutter_removal must be one of {CLUTTER_METHODS}, got {method!r}"
        )
    if method == "none":
        return bin_values

    if bin_values.ndim != 3:
        raise ValueError(
            f"remove_static_clutter expects (N, chirps, rx), got shape {bin_values.shape}"
        )
    if bin_values.shape[0] < 2:
        # A single slow-time sample IS its own mean; subtracting it yields all-zeros
        # and destroys the window. Fail loudly rather than emit a silent zero signal.
        raise ValueError(
            "remove_static_clutter needs at least 2 slow-time samples to estimate a "
            f"static component, got {bin_values.shape[0]}"
        )

    static = bin_values.mean(axis=0, keepdims=True)   # (1, chirps, rx)
    return bin_values - static


def clutter_to_signal_db(bin_values: np.ndarray) -> float:
    """Ratio of stationary to moving power in a bin, in dB. Diagnostic only.

    Positive means the bin is dominated by whatever is not moving. This is the
    quantity tabulated in HISTORY.md 2026-07-30; it is reported, never used to gate
    or to select a bin, because it cannot distinguish "compressed subject" from
    "no subject here".
    """
    if bin_values.ndim != 3 or bin_values.shape[0] < 2:
        raise ValueError(
            f"clutter_to_signal_db expects (N>=2, chirps, rx), got {bin_values.shape}"
        )
    static = bin_values.mean(axis=0, keepdims=True)
    static_power = float(np.mean(np.abs(static) ** 2))
    moving_power = float(np.mean(np.abs(bin_values - static) ** 2))
    if moving_power <= 0.0:
        return float("inf")
    return float(10.0 * np.log10(static_power / moving_power))
