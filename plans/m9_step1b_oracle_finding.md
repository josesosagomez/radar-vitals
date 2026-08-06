# M9 Step 1b — oracle finding: conjugate sidebands make the two-line fit misspecified

> **Status: DECISION REQUIRED at the step-4 commit checkpoint (user + CLAUDE.md §6
> cross-review).** Written 2026-08-06 by the independent oracle
> (`scripts/m9_step1b_gate_prediction.py`), which the plan requires to run **before**
> `src/m9/kotte_gate.py` exists precisely so predictions cannot be reverse-engineered
> from the implementation. `transfer.gate_criteria` remains `null` — the checkpoint has
> **not** happened, and this finding changes what the gate should test.

## 1. The finding

Kotte's model (eq 23) is two complex Doppler lines, `β1 a(f_d1) + β2 a(f_d2)`, and the
estimator enforces exactly two constraints, `wᴴ[a(f1) a(f2)] = [1 1]`. A **real** chest
displacement does not produce two lines. With
`d(t) = A_b sin(2π f_b t + φ_b) + A_h sin(2π f_h t + φ_h)` the baseband is
`exp(j(4π/λ_eff)d(t))`, and Jacobi-Anger gives lines at `p·f_b + q·f_h` with amplitude
`J_p(m_b)J_q(m_h)e^{j(pφ_b+qφ_h)}`. Even in the **small-modulation limit** the first-order
terms are conjugate **pairs**: `J_{-1} = −J_{+1}`, so `±f_b` and `±f_h` are all present.
The signal therefore carries **at least four** significant lines where the estimator fits
two. That is a model-order misspecification, not a noise problem.

## 2. Evidence — the mechanism isolated

Two-cisoid signal vs the same frequencies as conjugate pairs, identical aperture, SNR,
4 RX, per-RX gain/phase tables, δ=1e-2, 9 seeds; truth BR 18.0 / HR 81.0 bpm; "hit" =
HR within 3 bpm. (Scratch driver `sidebands.py`; the committed oracle reproduces the
same behaviour on the full Bessel comb.)

| signal | N_c=16 (0.8 s) | N_c=32 (1.6 s) | N_c=64 (3.2 s) |
|---|---|---|---|
| two cisoids | 9/9 at ≥30 dB (3/9 at 10 dB) | 9/9 at ≥10 dB | 9/9 |
| **conjugate pairs** | **0/9 at 10, 30 AND 60 dB** | **0/9 at 10, 30 AND 60 dB** | 9/9 (HR 80.0) |

**Raising SNR by 50 dB does not recover a single case** at N_c=16 or 32. Only lengthening
the CPI does. At N_c=64 the aperture (3.2 s, Rayleigh 0.31 Hz) finally separates `+f_b`
from `−f_b` (0.6 Hz apart ≈ 2 resolution cells) and the estimate returns — with HR 80.0
vs truth 81.0, a **biased BR of 15.0 vs 18.0**, and a `pair_margin_db` of only 0.015 dB.

A separate aperture/SNR sweep on pure tones (`aperture.py`) confirms the contrast is not
aperture alone: at N_c=16 pure tones are recovered **exactly** (18.0 / 81.0) once SNR
≥ 30 dB, with margins 0.09–0.15 dB. Aperture and model order interact; neither alone
explains the failure.

**Full-comb oracle output** (`results/m9/step1b/oracle/20260806T160339.308982Z/`),
primary arm N_c=16, `snr_z_dynamic_db` = 10, across the four declared phase pairs:
S1 (small modulation, m_b=0.17 rad) selects the 48 bpm heart-band **floor** in 3 of 4
phase pairs; S2 (m_b=6.63 rad) errs 11.5–31.5 bpm; S3 errs 5.5–22 bpm. Median
`pair_margin_db` ≈ 0.00 dB throughout and the modal pair holds for only 20–60 % of
seeds, with per-seed HR spreads up to 72 bpm — i.e. at N_c=16 the selection is **not a
measurement**, it is a coin flip on a flat surface.

## 3. What this predicts, and why it is worth having early

- **P3 (two-rate recovery) is predicted to FAIL at the primary arm** `N_c=16`, even in
  the paper-like small-modulation regime S1. Under the plan's stated gate this is a
  transfer NO-GO.
- **P4's decoy question is partly moot at N_c=16**: the estimator does not resolve the
  true line well enough for "true line vs breathing harmonic" to be the operative
  failure. The decoy analysis becomes meaningful only at longer CPIs.
- **The N_c=32 diagnostic arm (`kotte_cpi_medoid_nc32_dl1em2`) is predicted insufficient
  too.** The plan's risk #7 ("aperture vs breath band") is real and is now quantified:
  the threshold is nearer **N_c=64** (3.2 s), which yields only 9 CPIs per 30 s window
  (592/64) and still shows BR bias and a sub-0.02 dB margin.
- It is found on synthetics, so a subsequent real-data null is **attributable**: the
  method's own two-line assumption, not our hardware, our captures, or our tuning. This
  is exactly what makes M8's negative result publishable, obtained the same way.

## 4. Options for the checkpoint

**A — freeze the gate criteria as they stand and let the transfer gate return NO-GO.**
Honest and cheap. Records "Kotte's two-line model does not transfer to real chest
displacement at production geometry" as the M9 result; Stage A real-data work would
then be reported as a documented-null confirmation rather than an evaluation.

**B — add a declared longer-CPI arm before freezing (RECOMMENDED).** Add `N_c=64` as a
**declared, pre-results** arm (config amendment + §6 cross-review) so the gate tests the
regime where the method can work at all, and keep `N_c=16` as the primary so the
production-geometry result stays the headline. Cost: 592/64 = 9 CPIs per window; the
medoid's per-CPI spread statistics weaken accordingly. This buys a two-point aperture
curve (16, 64) that turns "it failed" into "it fails below a measurable aperture
threshold", which is a materially stronger paper claim.

**C — reformulate the constraint set to four lines (±f_b, ±f_h).** Scientifically the
"right" fix, and the natural extension the paper's conclusion itself gestures at
("can be extended for more than two Doppler frequencies"). But it is **no longer Kotte's
published estimator**, so it cannot be reported as an evaluation of [R2]; it would be a
new method needing its own controls. Out of scope for M9 as chartered — worth recording
as future work.

My recommendation is **B**, with **C** recorded in `notes/approach.md` as the principled
extension we deliberately did not take inside M9's evaluation charter.

## 5. What this does NOT change

The controls (R1/R2/R3 all `behaviorally_reproduced`) stand: the paper's own two-cisoid
scenario reproduces exactly. This finding concerns **transfer to vital signs**, which is
precisely the question Step 1b exists to answer. Stage A's sweep/scorer/comparator
architecture, the Stage-B rule, and the approximate-origin amendment are all unaffected.

## 6. Cross-review status (CLAUDE.md §6)

`m9_oracle_finding_cross_review: pending`

To check independently: the Jacobi-Anger expansion and the `J_{-1} = −J_{+1}` sideband
claim; the coherent-combination and alias-folding rules in `bessel_comb`; the choice to
model the **sample** (not ensemble) covariance and the Sherman-Morrison argument for why
the ensemble form is near-flat; and the N_c=64 threshold.

## 7. Provenance

- Committed oracle: `scripts/m9_step1b_gate_prediction.py`; output
  `results/m9/step1b/oracle/20260806T160339.308982Z/oracle.json` (gitignored tree).
- Config at `2fec07e`; oracle records `oracle_sha256_of_config`.
- Scratch drivers (session scratchpad, NON-evidence): `aperture.py`, `sidebands.py`.
- Superseded during development, recorded so it is not repeated: an initial oracle built
  the covariance as the **incoherent** `Σ|a_i|² s sᴴ`, which erased all phase dependence
  (identical output for every phase pair) and was replaced by the coherent rank-1 form,
  then by the sample-covariance form actually used.
