# M9 Step 1a — implementation finding: the declared SNR reading blocks reproduction

> **Status: DECISION REQUIRED (user + CLAUDE.md §6 cross-review) before the official R1
> verdict runs.** Written 2026-08-06 during step 1 of `plans/m9_kotte_plan.md`. Nothing
> official has run; nothing is scored; the committed config still declares the original
> assumption. The step-1 build is complete and committed (`3c0efc7`, 42 tests passing,
> full suite 2197/5 skipped). This memo exists because running a milestone-ending
> verdict on an assumption the paper's own figures contradict would mismeasure the
> paper, and silently changing the assumption would violate the plan's review
> discipline. Both bad options are declined; the decision is surfaced instead.

## 1. The finding, in one paragraph

Under the plan's declared assumption set — `Y_t`-domain AWGN at the paper's two-line
power ratio with **SNR = 0 dB**, sample covariance, unloaded, 20 RX — Algorithm 1's
literal selection objective `1ᵀH⁻¹1` **provably cannot** place its global argmax at the
truth pair for the Fig-8 geometry, independent of noise realization: the objective is
the constrained-minimum output power, so its value at the truth cell is bounded by
`|β1+β2|² + σ²·min‖w‖²` (= 4.31 for ratio 1.0), while near-diagonal cells *between* the
two tones legitimately exceed it (5.18 at (1.30, 1.70) **on the exact ensemble
covariance** — no sampling noise involved). The paper's own Fig-8 surfaces confirm the
authors did not compute this bounded quantity at 0 dB: their truth peaks read ≈ 90/70/50
across the three amplitude rows — 25–45× the mathematical bound — and Fig 7 row 1
(Δf = 3 Hz, a well-conditioned pair) still peaks ≈ 80, so the amplification is
separation-independent, consistent with a much smaller noise floor relative to signal
than Y_t-domain 0 dB provides. Reading the paper's "SNR = 0 dB" as **fast-time-referred**,
with the range FFT's processing gain `10·log10(N_s = 128) = +21.07 dB` folded in before
`Y_t(κ)` is formed — the same fast-time stage the plan already descoped as internally
inconsistent (:1336-1338) — makes the *same pinned objective* reproduce the paper's
proposed-method behavior wholesale.

## 2. Evidence

All numbers from scratch scripts (session scratchpad, non-evidence, dirty-tree) driving
the **committed** step-1 machinery at commit `3c0efc7`, root seed 20260806.

**(a) The bound.** At the truth pair the two unit-gain constraints force
`wᴴs = β1+β2` exactly, so the constrained minimum is
`|β1+β2|² + σ²·min‖w‖²` with `min‖w‖² = 1ᵀ(A₂ᴴA₂)⁻¹1 = 0.154` at Δ = 1 Hz
→ **4.31** for ratio 1.0 (σ² = 2). Computed value at the truth cell: 4.309 ✓.

**(b) The ridge wins at 0 dB — structurally.** Ensemble covariance (`ssᴴ + σ²I`, no
realization noise): argmax at (1.30, 1.70), value 5.18 > 4.31. Sample covariance,
5 seeds × 3 ratios: truth hit **0/15**; argmax always on the inter-truth/near-diagonal
ridge, at ratio 0.1 collapsing onto the strong-tone diagonal.

**(c) The paper's surfaces are not the bounded quantity.** Page-09 render, Fig 8
proposed column: colorbar maxima ≈ 90 / 70 / 50 for σ² = 2 / 1.25 / 1.01 — the ratio
peak/σ² ≈ 45/56/50 is roughly constant, i.e. the peak is dominated by a noise-times-
`‖w‖²` term with `‖w‖²` ≈ 50, 300× the constrained minimum norm. Fig 7 row 1 (Δ = 3 Hz)
peak ≈ 80 rules out truth-pair ill-conditioning as the source; the scale matches the
Marchenko-Pastur eigenvalue floor of a 16×16 sample covariance from 20 snapshots
(λ_min ≈ σ²(1−√(16/20))² ≈ 0.011·σ²). The faint anti-diagonal streak between the two
truth dots in Fig 8(a) is the same ridge our implementation finds — in their surface it
stays ~6× below the peaks; in the bounded quantity it wins. Their computation therefore
had an effective signal-to-noise-floor far above Y_t-domain 0 dB.

**(d) Candidate-interpretation sweep.** Five readings of the selection line (form matrix
= same sample / fresh sample / ensemble / signal-only; w from sample or ensemble; loaded)
all still rank the ridge above truth at Y_t-domain 0 dB — the SNR reading, not the
matrix pairing, is the discriminating variable.

**(e) The gain-corrected reading reproduces, with the committed per-case seeds:**

| | committed 0 dB | gain-corrected +21.07 dB |
|---|---|---|
| R1 ratio 1.0 proposed | miss — sel (1.40, 1.50) | **hit — sel (2.00, 1.00), secondary (2.00, 1.00)** |
| R1 ratio 0.5 proposed | miss — sel (1.75, 1.80) | **hit — (2.00, 1.00) both surfaces** |
| R1 ratio 0.1 proposed | miss — sel (2.50, 2.45) | **hit — (2.00, 1.00) both surfaces** |
| R1 verdict | not_reproduced_under_declared_assumptions | **behaviorally_reproduced** |
| R2 (all 3 Fig-5 targets) | not_reproduced (0/3 primary) | **behaviorally_reproduced (3/3 exact)** |
| R3 Fig 7 row 2 (Δ = 0.5 Hz) | miss | **resolves_both; FFT/MUSIC fail as expected** |

Multi-seed robustness at ≈ +21 dB (5 seeds/ratio): 14–15/15 proposed hits. The official
run is one declared seed per case; at the committed seeds every case hits.

**(f) Residual comparator-cell mismatches (both SNRs): peak pulling.** The FFT and
MUSIC weak peaks *exist* in the expected "detect" cells but are displaced beyond the
declared ±0.25 Hz weak tolerance: measured pulls 0.27 (FFT, ratio 1.0), 0.30 (MUSIC,
ratio 1.0), 0.46 (FFT, ratio 0.5) at +21.07 dB. Two interacting Dirichlet mainlobes at
Δ = 1 Hz with a 0.8 s aperture displace each other's maxima; the paper's own Fig-8 FFT
panels visibly show the same displaced peaks. The paper's "detects both" is qualitative
two-peak visibility, not ±0.25 Hz accuracy. The truth table already handles this
("differing cells recorded as diagnostic"), but the expected matrix as pinned would
never show FFT/MUSIC "detect" under our rule.

## 3. Options

**A — run the official verdict under the committed 0 dB reading.** Near-certain
`not_reproduced_under_declared_assumptions` on R1/R2/R3; the milestone ends with a
documented non-reproduction; real data is never touched. Defensible procedurally, but
§2(c) shows the verdict would measure our SNR reading, not the paper's method.

**B — prospective amendment (RECOMMENDED).** Before any official control run, amend
`experiments/m9_kotte/config.yaml` `controls` (and note it in the plan):
1. `snr_reference: fast_time_with_range_fft_gain`, `n_s_fast_time: 128` — effective
   Y_t-domain SNR = `snr_db + 10·log10(n_s_fast_time)` ≈ 21.07 dB. A paper-derived
   constant (§IV: N_s = 128), not a tuned value; the 0-dB literal reading is retained
   as a one-field audit (`snr_reference: yt_domain_literal`) recording §2(b).
2. Decide the comparator question (either is defensible; pick one, declared):
   (i) keep expectations at Fig 8's published pattern and let mismatched cells stand as
   recorded diagnostics per the truth table — the R1 gate then rides on the proposed
   columns; or (ii) widen `weak_tolerance_hz` to a declared half-mainlobe value
   (0.625 = 1/(2·N_c·T_PRI); measured pulls 0.27–0.46) so "detect" matches the paper's
   qualitative claim. My recommendation: (ii), because the expected matrix was pinned
   to *gate* on the FFT/MUSIC contrast and leaving permanently-mismatched cells makes
   the comparator matrix dead weight.
3. §6 cross-review of the amendment, commit, THEN run the official R1 (single declared
   seeds) → R2/R3 + audits → continue the plan unchanged.

**C — dual declared arms.** Official primary at the literal 0 dB (records the
non-reproduction of the literal reading) plus a declared gain-corrected secondary arm;
the behavioral-reproduction claim attaches to whichever arm the user designates. Most
exhaustive, most complex to report; the transfer gate would inherit two SNR regimes.

## 4. What this does NOT change

- Stage A's objective (`loaded_capon_power`), the rank rule, grids, alias collapse,
  aggregation contract, sweep/scorer/comparator architecture, Stage-B rule — all
  independent of this decision.
- The Bessel-comb transfer question (S1/S2/S3) and P1–P6 — the gate generator's
  `snr_z_dynamic_db` is our own declared quantity, not the paper's.
- The approximate-origin amendment and its pending cross-review.
- M8. No M8 file is touched.

## 5. Provenance

- Committed machinery used: `src/m9/kotte_core.py`, `src/m9/paper_control.py` at
  `3c0efc7`; config `experiments/m9_kotte/config.yaml` at `b39888f`.
- Scratch drivers (session scratchpad, NON-evidence): `proto_r1.py`, `proto_diag.py`,
  `proto_interp.py`, `proto_argmax.py`, `proto_snr.py`, `memo_numbers.py`. Two labelled
  `--smoke` runs exist under `results/m9_kotte_controls/*_smoke/` (non-gating).
- Page renders consulted: page-07 (Table I, SNR equation), page-08 (Figs 6-7),
  page-09 (Fig 8) of the Kotte extraction.
