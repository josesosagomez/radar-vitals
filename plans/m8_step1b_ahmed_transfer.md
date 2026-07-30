# M8 Step 1b — Ahmed HA phase transfer and exploratory FMCW evaluation

Status: **reviewed plan awaiting explicit user approval; implementation is not authorized**.

Planning baseline: branch `vital_signs_ahmed_v10`, commit
`1bad25cb034f7ce788c4c9b387e74b5d9adf7476`. The older branch/dirty-state description in
`HANDOFF.md` is stale; the Step 1a canonical bundle is committed. All claims and hashes below must
be rechecked at execution time.

## 1. Goal, order, and approval boundary

Step 1b has two ordered stages:

1. Run a synthetic transfer control that replaces Ahmed et al.'s real, even-harmonic pulse-radar
   return with this project's coherent FMCW phase-extraction path while retaining the collision
   scenario and fixed-\(H\) accumulator.
2. Only after a completed, hash-matched synthetic gate, run the production estimator and six
   preregistered Ahmed phase arms on all eight saved captures, then score the persisted radar-only
   output against Masimo.

The synthetic result is an **adaptation**, never a reproduction. A correct negative result is a
valid milestone outcome. No estimator, scoring, threshold, signal-mapping, or real-data result may
be changed to make the Ahmed arm agree with Masimo.

### Explicit non-goals

- Do not change production DSP, quality thresholds, the frozen HR/BR comparators, or Step 1a's
  scientific behavior.
- Do not use complex magnitude, envelope, a single arbitrary quadrature, real-part scoring, a
  partial-\(H\) accumulator, resampling, harmonic wrapping, or agreement-optimized timing.
- Do not claim population performance, independent-subject validation, confirmatory evidence,
  inferential confidence intervals, or limits of agreement from these single-subject development
  captures.
- Do not select a harmonic count, suppression profile, lock policy, capture subset, or signal
  mapping by real-data error. No “best arm” field or headline ranking is produced.
- Do not persist raw ADC cubes in the result bundle or reuse live estimates/intermediates as
  scientific evidence.

## 2. Source interpretation and synthetic transfer gate

### 2.1 Resolved source ambiguity

Ahmed equation (14) has lines at \(2f\) and its harmonics because it is a real demodulated
pulse-radar return. In contrast, the project estimator operates on unwrapped range-bin phase.
For a coherent phasor

\[
z[n]=\exp\{j(\theta_0+\beta_b\sin(2\pi f_b t_n)+
                         \beta_h\sin(2\pi f_h t_n))\},
\qquad
\beta_v=\frac{4\pi d_v}{\lambda},
\]

the complex phasor has Bessel/mixed spectral lines, but the clean delta/unwrapped phase is simply
the two displacement sinusoids (up to a constant) when adjacent phase increments do not cross a
branch boundary. Phase extraction does **not** manufacture higher displacement harmonics.

Therefore the primary control is named
`phase_fundamentals_only_transfer_v1`. “All-harmonic phase” in the roadmap is interpreted as the
accumulator's \(q,2q,\ldots,Hq\) convention for observed project phase, not as a claim that this
two-sinusoid generator contains nonzero higher harmonics. Adding non-sinusoidal displacement
coefficients would require a separately sourced, preregistered future control; coefficients may
not be invented or tuned here.

This correction is deliberately conservative: it directly tests whether fixed-\(H\) accumulation
transfers when the clean extracted phase contains only the breathing and heartbeat fundamentals.

### 2.2 Exact synthetic construction

Keep the Step 1a primary values:

- \(c=3.0\times10^8\) m/s, \(f_c=6.7\) GHz;
- \(f_b=20/60\) Hz, \(f_h=80/60\) Hz;
- \(d_b=20\) mm, \(d_h=10\) mm, equal contribution multipliers, \(\theta_0=0\);
- five breaths (requested 15 s), Step 1a PRF
  \(37.41985916275843\) Hz, `sample_count_rule=round` (561 samples);
- `n_fft=4096`, input SNR 10 dB, seed 42, `normalization=non_dc_mean`;
- \(H\in\{3,5\}\) and all three named suppression profiles.

Generate `u` and `v` as independent `float64` standard-normal arrays from
`numpy.random.Generator(numpy.random.PCG64(42))`. With
\(P_s=\operatorname{mean}|z_\text{clean}|^2=1\), inject circular complex noise before phase
extraction:

\[
n=\sqrt{\frac{P_s}{2\,10^{\mathrm{SNR}_{dB}/10}}}(u+jv),\qquad
z_\text{noisy}=z_\text{clean}+n.
\]

Report
\(10\log_{10}(\operatorname{mean}|z_\text{clean}|^2/\operatorname{mean}|n|^2)\) as the realized
**pre-extraction** SNR. Do not describe the nonlinear, wrapped extracted-phase error as 10 dB
AWGN. Hash both random draws and the clean/noisy phasors. Circular noise is an explicit model
adaptation necessitated by the complex phase input; it is not misrepresented as literally the
only changed algebraic field. Correctness checks exact algebraic agreement with

\[
\operatorname{mean}|n|^2 =
\frac{P_s}{2\,10^{\mathrm{SNR}_{dB}/10}}\operatorname{mean}(u^2+v^2),
\]

not closeness of the finite draw to exactly 10 dB. For the declared primary realization,
`mean(abs(n)**2)=0.0959419233` and realized SNR is 10.1799158 dB.

Exercise the unchanged production extraction path by embedding the noisy phasor in a deterministic
ADC cube of shape `(N, 1, 1, 64)`:

\[
x[n,0,0,m]=z_\text{noisy}[n]\exp(j2\pi\,7m/64).
\]

Cast the completed cube explicitly to `complex64`, matching decoded production cubes, and hash both
the pre-cast `complex128` phasor and the embedded `complex64` cube. Use `locked_bin=7`, then call
`extract_chest_phase(cube, 7, method="delta_before_mean")`. Its fast-time Hann/FFT coefficient is a
positive real scale times the slow-time phasor and therefore preserves the required phase. The
primary extraction is exactly

\[
d[0]=0,\quad d[n]=\arg(z[n]z^*[n-1]),\quad
\hat\phi[n]=\sum_{k=1}^{n}d[k].
\]

Store the clean/noisy phasor, cube parameters, extracted phase, maximum clean adjacent phase
increment, branch-boundary distance, and phase-slip diagnostics. Define

\[
\phi_c[n]=\theta_0+\beta_b\sin(2\pi f_bn/f_s)+\beta_h\sin(2\pi f_hn/f_s).
\]

A clean analytic oracle applies only when every
\(\Delta\phi_c[n]=\phi_c[n]-\phi_c[n-1]\) lies in the open interval \((-\pi,\pi)\); equality is
branch-boundary-degenerate. Under that condition, the extracted signal must equal
`unwrap(angle(z_clean)) - unwrap(angle(z_clean))[0]`. The declared primary has maximum clean
increment 0.94111946 rad and minimum branch margin 2.20047319 rad.

For noisy data, use a model-relative phase-noise oracle:

\[
\epsilon[n]=\arg(z_\text{noisy}[n]z_\text{clean}^*[n]),\qquad
\phi_\text{ref}[n]=\phi_c[n]+\epsilon[n].
\]

Let
\(r[n]=\hat\phi[n]-(\phi_\text{ref}[n]-\phi_\text{ref}[0])\) and
\(k[n]=\operatorname{round}(r[n]/2\pi)\). First require
\(|r[n]-2\pi k[n]|\) to remain within the declared numeric tolerance; otherwise the extraction
implementation is inconsistent with the oracle. Record a cycle-slip event when integer offset
`k[n]` changes from `k[n-1]`, together with the model-relative adjacent branch margins. Noisy slips
are scientific behavior that may produce a negative transfer, not an implementation failure. For
the declared realization, `max(abs(epsilon))=0.69819` rad,
`max(abs(diff(phi_ref)))=1.64805 < pi`, and the cycle offset remains zero. Direct comparison with
`unwrap(angle(z_noisy))` is retained only as an implementation-consistency check, never called a
scientific phase-slip diagnostic. `mean_phasor` is a separately reported single-channel
equivalence audit only: compare
`mean_phasor - mean_phasor[0]` with `delta_before_mean`, because the raw former retains its initial
phase constant. It cannot upgrade the primary result.

Before gate correctness can pass, add a production-geometry aggregation oracle of shape
`(N, 32, 4, 64)`. Use the same noisy slow-time phasor in every chirp/RX channel, fixed preregistered
positive channel amplitudes, and fixed static chirp/RX phase offsets:

\[
x[n,c,r,m]=a_{c,r}z_\text{noisy}[n]
            \exp\{j(2\pi\,7m/64+\psi_{c,r})\}.
\]

The amplitude/offset tables are deterministic functions of integer `(c,r)` recorded in the
resolved config, not tuned values. After the explicit `complex64` cast,
`delta_before_mean` must match the 1-chirp/1-RX extracted phase within the declared dtype tolerance.
This is an aggregation correctness oracle, not a second scientific model or robustness claim.

Add a separately named 20 Hz sampling audit with the same five-breath duration, seed, input SNR,
`n_fft=4096`, and model. It has 300 samples. It tests sampling/extraction transfer only and cannot
upgrade, replace, or silently downgrade the primary-PRF gate verdict.

### 2.3 Frequency, accumulator, support, and suppression

After phase extraction, candidate frequency is \(q=f\) and rate is

\[
\mathrm{bpm}=60q,
\]

not Step 1a's \(q=2f\), `bpm=30q`.

Reuse `src/m8/ahmed_fig8.py`'s `accumulate_harmonics`, `SUPPRESSION_PROFILES`, and deterministic
lowest-bin tie selection unchanged. Do not call production `ha_estimate_rr`: it has detrending,
slow-time Hann windowing, truncated harmonics, a doubled fundamental, and a local-maximum veto that
are outside this fixed-\(H\) transfer.

The transfer spectrum is the magnitude of the real extracted phase's `rfft`. The primary synthetic
run retains Step 1a zero-padding to 4096. Real 600-frame windows use the native 600-point transform:
no slow-time detrend, slow-time window, zero padding, DC/static-clutter subtraction, impulse
cleaning, or resampling.

Candidates are exactly the `numpy.fft.rfftfreq(n_fft, d=1/fs)` bin centers satisfying the following
inequalities; there is no parabolic interpolation, refinement, or off-grid evaluation. Support and
suppression operate on these same integer rFFT-bin indices.

Candidate domains are:

| Stage | Breathing candidate \(q\) | Heart candidate \(q\) |
|---|---|---|
| Synthetic | \(0<q\le25/60\) Hz | \(f_b\le q\le100/60\) Hz |
| Real | \(0.10\le q\le0.50\) Hz | \(0.80\le q\le2.00\) Hz |

For every candidate, full fixed-\(H\) support requires the strict condition
\(Hq<f_s/2\). Equality at Nyquist is recorded as `nyquist_boundary_degenerate`, not generic
support, because a real sinusoid there loses one quadrature. Unsupported candidates are masked
before accumulation; the denominator remains exactly \(H\). Never truncate the harmonic row.

Consequences at real \(f_s=20\) Hz:

- \(H=3\) supports the full HR domain.
- \(H=5\) supports HR strictly below 120 bpm. On the native 30 s grid, 120 bpm is ineligible and
  the largest eligible candidate is 118 bpm.
- \(H=5\) supports the synthetic 100 bpm ceiling at both the primary PRF and 20 Hz
  (\(5\times100/60=8.33\) Hz).

For each \(H\), compute one Ahmed BR result. Its same-\(H\) selected breathing bin is the only bin
allowed to drive that \(H\)'s heart suppression profiles:

1. `figure_visible_unsuppressed`: no heart-bin exclusion;
2. `eq26_multiples_suppressed`: retain Step 1a's literal discrete rule
   `candidate_bin % breath_bin != 0`;
3. `prose_low_or_equal_suppressed`: retain `candidate_bin > breath_bin`.

Do not use truth or production BR. Do not assume equation (26) always removes the target after the
\(q=f\) change: nearest-bin rounding can break exact divisibility. Mark
`inconclusive_by_definition` only when the expected target bin is actually excluded. If the Ahmed
BR has no method-valid selection, the two BR-dependent heart profiles are invalid with
`breath_estimate_invalid`; the unsuppressed profile remains independently computable.

### 2.4 Synthetic correctness, scientific success, and gate truth table

Implementation correctness and scientific transfer are separate.

Correctness requires:

- deterministic config/signal/draw hashes;
- the clean direct-phase identity and no-slip check;
- measured noise power matching the declared complex-noise equation;
- candidate grids, strict support masks, harmonic-bin matrices, suppression masks, and all score
  curves matching an independent slow oracle at fixed tolerances;
- exact \(q=f\), `bpm=60q` conversion;
- complete finite scores on every supported unsuppressed candidate;
- all \(2\times3=6\) H/profile result records and complete evidence.

Scientific success is evaluated only on the primary-PRF
`figure_visible_unsuppressed` H=3 and H=5 results. Both BR and HR must, for both \(H\):

- have a unique maximum;
- select within the effective native resolution \(1/T_\text{effective}\) Hz of truth, equivalently
  absolute rate error at most \(60/T_\text{effective}\) bpm;
- have a strictly positive selected-versus-runner-up margin; and
- retain a finite, full-grid pre-exclusion curve.

Step 1a's observed `H3 target accumulation > H5` relationship is a diagnostic, not a correctness
or transfer gate. The suppression profiles and 20 Hz/`mean_phasor` audits are reported independently
and cannot rescue a failed primary.

Aggregate status is:

| Condition | Synthetic gate status |
|---|---|
| Correctness incomplete or failed, exception, hash/schema mismatch | `failed` |
| Correctness passes and all four unsuppressed H/vital rate checks pass | `passed_under_declared_seed_and_configuration` |
| Correctness passes but any primary unsuppressed rate check fails | `not_transferred_under_declared_assumptions` |

`inconclusive_by_definition` is a per-profile scientific status and may coexist with an aggregate
positive status because suppression profiles are not the primary gate. `complete` never implies
success. A positive result supports only this declared seed/configuration and must not be described
as robust or as general phase-model transfer.

## 3. Real-capture exploratory evaluation

### 3.1 Signal mapping and estimands

The only real mapping is `project_phase_delta_before_mean_v1`:

1. fast-time Hann window and range FFT;
2. the common locked range bin;
3. per-channel frame-to-frame conjugate-product phase increments;
4. mean across all chirps and RX channels;
5. angle and cumulative sum;
6. the fixed-\(H\) transfer core defined above.

This is exactly `extract_chest_phase(..., method="delta_before_mean")`. There is no post-extraction
preprocessing. Every shared extracted phase is computed once per window/lock and referenced by all
six Ahmed arm rows.

On real data, `prose_low_or_equal_suppressed` is structurally identical to
`figure_visible_unsuppressed` because the fixed HR band begins at 0.8 Hz while the BR band ends at
0.5 Hz. Require identical real curves/selections and label the prose arm
`dependent_duplicate_by_disjoint_domains`; it remains present for source completeness but is never
counted as corroborating evidence.

Report two lock estimands separately and never pool or collapse them, even when their numeric bins
match:

- `recorded_lock_as_captured`: the capture's recorded lock, hash-bound to raw, metadata, and warmup
  evidence;
- `current_production_rerun_lock`: rerun `src/warmup_select.py::run_warmup_selection` once on
  `k=0` with the current production config, then share its selected bin across production and all
  Ahmed arms.

The rerun selector is identified as `production_warmup_selector_v1`; its resolved configuration,
current `scripts/live_demo_config.yaml` SHA-256
`8bc7438e887ddcb243cec124cbc316a2279429bad4bdffabb4577f23f3179d7e`, source commit, candidate
evidence, and selected lock are persisted. Recompute production and Ahmed estimates on `k=0` at
the selected lock; do not reuse the selector's winning production payload as one arm's result.

### 3.2 Frozen windows and timing

Use `src/m4/window_grid.py` exactly: contiguous, non-overlapping 600-frame/30 s windows beginning at
`k=0`; drop incomplete tails. The 3 s value in live metadata is a display/replay hop and must not
create overlapping offline windows. The primary grid has 128 complete windows across the eight
captures. A prespecified `k>=1` sensitivity has 120 windows and is computed from the same persisted
rows without rerunning locks or estimators.

Preserve and report all 128 rows, but both lock estimands were selected by production logic using
`k=0`. Therefore `k=0` is labeled `lock_selection_in_sample`, and `full_k0_diagnostic` metrics are
diagnostic only. The sole comparative/accuracy universe is `evaluation_k_ge_1` (120 windows). Ahmed-versus-
production conclusions must state that both are conditional on a production-selected range lock;
this experiment does not independently validate Ahmed range selection.

Reuse only `window_grid.py`'s exact frame-index spans. Its frozen `frame0_epoch` must not be supplied
from the live recording. A separate `start_wall_utc_approximate_v1` scoring mapper derives
`epoch_start/epoch_end = parsed_start_wall_utc + frame_start/frame_stop / fs`. Never search, shift,
or optimize this approximate time origin. Every radar and scored row carries:

- `time_origin_id=start_wall_utc_approximate`;
- `origin_is_approximate=true`;
- `evaluation_status=exploratory_non_frozen`;
- exact `frame_start`, `frame_stop`, `epoch_start`, and `epoch_end`.

Reject a capture before estimation if raw size is not divisible by recorded bytes/frame, geometry
or `iq_swap` disagrees with the registry, a complete span cannot be decoded, or direct hashes do
not match. Recorder sub-frame tail counts are provenance; the saved raw files themselves are
frame-divisible. Decode one capture at a time, once, run both lock estimands and all suites on each
identical read-only window slice, then release it before the next capture.

### 3.3 Binding capture registry

Implementation creates `experiments/m8_ahmed_transfer/capture_registry.yaml` with these exact
planning values. Paths are explicit; `session_id` is not used for discovery. The embedded metadata
configuration is authoritative; stale external `config_path` values are not followed.

All captures share 256 ADC samples, 4 RX, 32 chirps/frame, complex int16, 131072 bytes/frame,
`iq_swap=true`, 20 Hz frame rate, and approximately 0.0436 m range resolution.

| ID | Directory suffix | Frames | Windows | Tail | Recorded lock | Rerun lock | Capture config hash |
|---|---|---:|---:|---:|---:|---:|---|
| m1 | `20260713_172042_live_demo_massimo1` | 3610 | 6 | 10 | 23 | 27 | `c38ae7ae8b363b82d5635760716389aeffcc9021159c6dbcaf99adcae3493d71` |
| m2 | `20260713_182002_live_demo_massimo2` | 3611 | 6 | 11 | 20 | 26 | `c38ae7ae8b363b82d5635760716389aeffcc9021159c6dbcaf99adcae3493d71` |
| sweep | `20260714_180523_live_demo_sweep` | 9611 | 16 | 11 | 21 | 26 | `e171e13ca64b9f6ec47c2d9439244226c18faa5306d8b9073e9d1731d417431f` |
| m3 | `20260728_224902_live_demo_massimo3` | 12005 | 20 | 5 | 26 | 26 | `60bf86663283a7b6a425e91d1c5b8f7fefb9ffb9a18bf1eb8337ad77f0f236c6` |
| m4 | `20260728_230903_live_demo_massimo4` | 12002 | 20 | 2 | 25 | 25 | `60bf86663283a7b6a425e91d1c5b8f7fefb9ffb9a18bf1eb8337ad77f0f236c6` |
| m5 | `20260728_232415_live_demo_massimo5` | 12005 | 20 | 5 | 25 | 25 | `60bf86663283a7b6a425e91d1c5b8f7fefb9ffb9a18bf1eb8337ad77f0f236c6` |
| m6 | `20260729_002158_live_demo_massimo6` | 12003 | 20 | 3 | 24 | 24 | `60bf86663283a7b6a425e91d1c5b8f7fefb9ffb9a18bf1eb8337ad77f0f236c6` |
| m7 | `20260729_004815_live_demo_massimo7` | 12020 | 20 | 20 | 32 | 32 | `60bf86663283a7b6a425e91d1c5b8f7fefb9ffb9a18bf1eb8337ad77f0f236c6` |

Direct SHA-256 registry:

| ID | `adc_stream.bin` | Masimo CSV |
|---|---|---|
| m1 | `dc2be2d008b48227c4bebde9bb5974133d3cf60196b4216e1ba630d175203f18` | `960af7f5038a8fe9233b38dfcb583872f3dba8c89082c72cf8e05ed371732bf9` |
| m2 | `112a64bf3cfc820868ad46df19a9dbfba7b0ce2612547e4dd3e5131bec1f88bf` | `d1cb91a43691cb10907425b15c2f0f1d02da09d12825d399225c8b16e89d32ff` |
| sweep | `91bc422d9f33bbde0fc1549018692d008dee4f9c04ca374e04f153bb80a7a96d` | `5974a9f303f79729c254fb4dc904d1c85c4bea006fafc202d12e0267de93de04` |
| m3 | `cca0cdcbaa8235672c96f524666835824aadf40aa78b12197705da63dbeb7b00` | `92054b471a04eefdbe4de45988846bab503cf61a0e5c619e5d8f545ef0a965cb` |
| m4 | `2edc2c6d1976407eea40101550b1f3c0ab442843a8cb8311a8ab4538bc423968` | `286b6e3fe085acbef15a85448f383e95945cd3ef23bf309c0159d0dc3dc6dfe5` |
| m5 | `a55a0e42f9972d6bcf5173870475f69694aabd808d84d4182fbdef544e76bb5b` | `6e0de2788f3b1162bce1782b70a8a7a2d5a752fdc52b3ca12029119368aa8498` |
| m6 | `b81ff843eff68e0bc9c05194525f065a9edc8346ac65397bf96d181d2e5a9103` | `119b740e801a410be2af4ce8962b2defb2a95a9aaf2b2f9b7494d097a7143178` |
| m7 | `782166e0a0dda411e3f2eecf891fd1193ebdfa681c3edd9919ee7b1688c053c5` | `778cbbc8f4c2618294c6a29e2ba29c2022504bb4bdd2fe437637cf57ef4b0684` |

Metadata and warmup SHA-256 registry:

| ID | Metadata JSON | Warmup selection JSON |
|---|---|---|
| m1 | `d0b92f3be1fa1d214f3c72035ffc82799ae92b1b0c48f63f91356b67e43a7a6f` | `be781af51c03ef42baee172413c03b2b63a49784d45691181e8c9c4d2ef68e07` |
| m2 | `f6a922dd85ce093c943fba4791a96a507edf9aa4e12d202fb0d5041cfb6d8f01` | `2483b18df3a38759413d506c223686b62fdd8c1f50b7bb5717d8cea55d90bfc9` |
| sweep | `828b972c18736ac7e46d6a893bbf9e2ca486edea7e666cebdd6a22be171ff89c` | `183387940992588bb693669b4ec310eb5ce8fe502c9e39c55f026cb836e43465` |
| m3 | `4af3468d9e3809542e4c56c6ae67f1e18e060d0ea8b9d2bf8dc0a6907db0fcf8` | `de6c40db7550e5119806565edd10c6049f62f7135105648a6daf937d3f381136` |
| m4 | `22541f71195c0fbb78f2c8c9923d66958dcad372956105ec4c2bcdc268d191d4` | `30fdc423658517286fccc780bae3f5f61853335aa5d5ce62512b9873c5f7ed76` |
| m5 | `280a10c9ab99f6eb1cf019bd440432392b0514383a26bba87bbf569acd4a6fa1` | `3066e79137a89ac728149b648cd65509891cb829a3c4f8b58a524a710031b560` |
| m6 | `a97c74570be68585bea8d1382ae6739f4bbd86db164f7a54170a23eea2524a4d` | `467d436491d0684c99cae51e246ef64bfea4c8e7be8ad43b3722798f50af4181` |
| m7 | `7b73a0cb89dde2e4208b4213e929cc4ebd4b449a0a059ac889fe1c8b723403dd` | `75f26bd73bfae1b52a9c6a947a91e7ca6b690053f04eceefd5c31a5f0022067f` |

Recorded-lock resolution additionally asserts that registry lock,
`run_metadata.locked_bin`, and `warmup.selected_bin` agree. Only m1/m2 have a non-null
`live_raw_mirror_hash`; the direct registry hash is binding for all eight.

The recomputed `current_production_rerun_lock` must equal the preregistered rerun-lock table for
every capture. Any mismatch is fatal and requires a reviewed plan/registry amendment; it is never
silently accepted as a new result. Because `run_warmup_selection` currently catches per-bin
exceptions and can choose a fallback, the resolver must inspect its complete evidence after the
call: any candidate DSP failure or `fallback_used=true` is fatal after persisting selector
diagnostics, before accepting even a numerically matching lock. Low confidence without a candidate
failure/fallback remains reportable provenance, not a hidden reason to switch bins.

### 3.4 Development roles, validity, and reference rules

All eight captures are `development_apparent_single_subject`; none is a holdout. Protocol metadata:

- m1: `natural`;
- m2: `paced_16_bpm`, target retained as provenance but not scored in this milestone;
- sweep: `paced_schedule_target_unavailable` (nominal 12/15/18/21 transitions were not persisted);
- m3–m7: `unknown_protocol_development`.

Score only Masimo HR `Beats / min` and BR `Breaths / min`. Parse and deduplicate by integer Unix
`Timestamp`; m7's duplicates/missing seconds are handled by the existing parser, not hardcoded row
counts. Import `src/comparator.py::hr_reference` and `br_reference` unchanged. No metronome result is
emitted, and missing protocol labels are never guessed.

Ahmed method validity is reference-independent:

- supported finite spectrum, at least one eligible candidate, a finite selected score, and a
  unique maximum -> valid;
- exact ties still retain the deterministic lowest-bin raw argmax but are invalid with
  `non_unique_maximum`;
- a finite, correctly shaped signal with all candidates suppressed, or an invalid required Ahmed
  BR -> explicit invalid arm rows with evidence and a named reason;
- no production AHET/outcome classifier is applied to Ahmed arms.

Malformed dtype/shape/config, nonfinite phase or spectrum, an empty fully supported candidate
domain, unexpected exceptions, decode/schema/hash errors, invalid locks, inconsistent shapes,
missing/extra arms, estimator mutation, or artifact-write errors are fatal numerical/contract
failures rather than per-window invalidity. Reason precedence is: shared numerical/shape contract
first; configured-domain/support contract second; required-BR dependency third; all-suppressed
fourth; non-unique maximum fifth; otherwise valid. Every eligible Cartesian tuple
`(capture, lock_estimand, k, arm)` must have exactly one row in a completed run; a fatal run is
incomplete and cannot publish metrics.

### 3.5 Metrics and reporting universes

Compute HR and BR independently for every capture, lock estimand, window set
(`full_k0_diagnostic` and `evaluation_k_ge_1`), and arm. Never mix these grouping keys.
Only `evaluation_k_ge_1` supports comparative/accuracy interpretation; `full_k0_diagnostic`
preserves the frozen grid and exposes selection optimism.

For each grouping report:

- `n_source`, `n_reference_admitted`, `n_radar_valid`, and `n_joint`;
- reference, radar-valid, and joint coverage as fractions of `n_source`, plus
  `joint_given_reference=n_joint/n_reference_admitted` when its denominator is nonzero;
- MAE, RMSE, bias (`radar-reference`), and absolute-error percentiles
  p50/p75/p90/p95 using `method="linear"`;
- count and reasons for every reference/radar exclusion.

Reference and radar dispositions remain separate for coverage cross-tabs. For the one mutually
exclusive scoring disposition, apply reference precedence first: HR `insufficient_usable` before
`nonstationary`, BR `insufficient_available` before `nonstationary`; only a reference-admitted row
can then be `radar_<reason>` or `joint`. Every error metric uses only `joint` rows.

For production versus each Ahmed arm, on the common reference-admitted \(k\) universe report:

- validity partitions `both`, `production_only`, `ahmed_only`, and `neither`;
- each estimator's metrics on the `both` intersection;
- the descriptive difference `Ahmed metric - production metric`, without a p-value or winner.

An empty intersection yields `n=0` and JSON `null` metrics, not a run abort. Per-capture results are
primary. Do not pool errors across natural, paced, and unknown protocols. Within each protocol
stratum only, emit two explicitly descriptive single-subject summaries:

- `protocol_pooled_window_micro`: concatenate eligible rows with equal weight per window;
- `protocol_capture_macro`: arithmetic mean of defined per-capture metrics with equal weight per
  capture, alongside a separate contributing-capture count for every metric.

The strata are `natural` (m1), `paced` (m2 and sweep), and `unknown` (m3–m7); no all-capture error
aggregate is emitted. Neither summary treats windows/captures as independent subjects.
Suppression profiles and harmonic counts are never pooled. For each vital and production/Ahmed
pair, the four validity partitions must sum exactly to `n_reference_admitted`.

## 4. Python architecture and interfaces

### 4.1 Scientific core

Add `src/m8/ahmed_transfer.py` and leave `src/m8/ahmed_fig8.py` scientifically unchanged.

The pure core is:

```python
def estimate_phase_ha(
    phase: np.ndarray,
    fs: float,
    config: AhmedPhaseConfig,
) -> SuiteWindowResult: ...
```

It validates a finite 1-D `float64` phase, builds the declared spectrum/candidate grids, computes
one BR per \(H\), and emits the six arm-native results plus shared spectrum evidence. Synthetic
and real wrappers must call this same core.

The new estimator-neutral contracts live in `src/m4/estimator_suite.py`:

```python
@dataclass(frozen=True)
class EstimatorArmSpec:
    arm_id: str
    estimator_id: str
    run_config_hash: str
    harmonic_count: int | None
    suppression_profile: str | None
    outcome_classifier_id: str | None

@dataclass(frozen=True)
class SuiteWindowResult:
    shared_evidence: Mapping[str, EvidenceValue]
    arm_native_results: Mapping[str, dict]

class WindowEstimatorSuite(Protocol):
    suite_id: str
    suite_config_hash: str
    arm_specs: tuple[EstimatorArmSpec, ...]
    outcome_classifiers: Mapping[str, OutcomeClassifier]
    def __call__(
        self,
        frames: collections.deque | np.ndarray,
        locked_bin: int,
        fs: float,
    ) -> SuiteWindowResult: ...
```

`EvidenceValue` is exactly `np.ndarray | None | bool | int | float | str`; arrays must be
non-object, C-contiguous, immutable copies. `OutcomeClassifier` has an immutable `classifier_id`
and `__call__(native_result: Mapping[str, object]) -> str`.

`arm_id` is path-safe and unique; `(estimator_id, run_config_hash)` is unique; the exact declared
arm set is returned for every window. Missing, duplicate, unexpected, or identity-mismatched arms
are fatal. Native IDs/hashes, if present, must equal the static spec. Shared arrays are immutable
and stored once, outside `WindowEstimate.raw`; each arm refers to a `shared_signal_hash`.

Configurations are bound at construction and copied into immutable canonical plain values:
`ProductionEstimatorSuite(current_live_config, production_outcome_classifier)` and
`AhmedPhaseEstimatorSuite(resolved_ahmed_config)`. Runtime callers cannot pass or substitute a
config. Construction validates and deep-copies an exact plain dict, computes `suite_config_hash`
before freezing, and retains canonical bytes plus a recursive immutable representation. Only the
legacy production call receives a newly materialized private dict. Mutating the caller's original
dict after construction cannot change hashes or results. Decode geometry remains separate and
comes only from hash-verified capture metadata. `outcome_classifiers` is keyed exclusively by
`arm_id`; every mapping entry must match that arm spec's declared classifier ID.

`ProductionEstimatorSuite` wraps `run_window_dsp` as estimator ID `eca_ahet_v1`, arm ID
`production_eca_ahet_v1`, and is full-native-payload equivalent to a direct call.
`production_outcome_classifier` is the sole `eca_bindrift_outcome_v1` adapter around
`src/m4/outcome.py::classify_window_outcome`; it is invoked only for that arm and its scalar result
is stored outside the unchanged native payload. Move the existing implementation/constants/helpers
from `scripts/diagnose_bin_drift.py` into that importable module, and make both
`scripts/diagnose_bin_drift.py` and `scripts/score_offline.py` import it with bit-identical behavior
regression tests. Do not duplicate or import an executable script. `AhmedPhaseEstimatorSuite` uses estimator ID
`ahmed_fixed_h_phase_v1`, has no outcome classifiers, and calls
`extract_chest_phase` once, then the pure core, producing:

- `ahmed_phase_h3_figure_visible_unsuppressed`;
- `ahmed_phase_h3_eq26_multiples_suppressed`;
- `ahmed_phase_h3_prose_low_or_equal_suppressed`;
- the corresponding three H=5 IDs.

`as_window_estimate` remains unchanged. The neutral runner normalizes each native arm explicitly
with its own static ID/hash. Every arm-native result must have exact Boolean `hr_valid`/`br_valid`;
finite `hr_raw`/`br_bpm` whenever the corresponding flag is true; and string `br_confidence` and
`rej_reason`, with optional finite-or-null `f_r_hz`. Only the production spec declares and invokes
the existing production outcome classifier.

### 4.2 Radar-only runner and separate scorer

Add:

- `src/m4/estimator_runner.py`: registry preflight, decode, frozen spans, both locks, suite dispatch,
  Cartesian completeness, and radar-only artifacts;
- `src/m4/estimator_scoring.py`: load a completed radar artifact, load Masimo, call frozen
  comparators, and compute neutral metrics;
- `scripts/m8_ahmed_transfer.py`: thin commands `synthetic`, `real-smoke`, `real-radar`, and
  `score`.

The radar stage must never load Masimo. The scoring stage must never decode ADC or invoke an
estimator; it consumes and hash-verifies the persisted frame/epoch spans. Keep
`scripts/score_offline.py` behavior unchanged during Step 1b; do not import it as a utility or copy
its AHET-specific pairing. The new modules import the existing grid, Masimo parser, and comparator
functions and implement one neutral aggregation path.

For each capture:

1. Before any capture-path `stat` or `open`, verify the source-dependency manifest, gate, required
   stage authorization, and parent-stage manifest.
2. Access only the registry's `radar` section; verify metadata and warmup hashes/contents, then
   stream-hash the raw file and compare size/geometry.
3. Decode the capture exactly once with its embedded geometry. The prior raw hash pass is not a
   decode.
4. Build the frozen frame spans once and resolve both locks from the common `k=0`.
5. Mark each window slice read-only and pass the identical object/span to production and Ahmed
   suites for both locks.
6. Assert the base cube and every previously emitted result are unmodified.
7. Persist every row/evidence object, assert exact Cartesian counts, finalize, and release the
   capture cube.

The capture registry has separate `radar` and `reference` mappings. Radar code is not given the
reference mapping and tests make any access to a Masimo path fatal. Scoring first validates its
source/authorization and exact radar-parent manifest, then accesses only `reference`, verifies the
Masimo hash, and never stats or opens raw ADC.

### 4.3 Hash identities

Use the existing canonical `run_config_hash` for plain resolved dicts. Define and persist:

- source config file SHA-256;
- resolved experiment config hash;
- suite config hash over extraction, spectrum, candidate/support, normalization, and shared
  evidence policy;
- arm run-config hash over the suite config plus exact \(H\), suppression profile, \(q=f\), and
  bpm conversion;
- selector ID/config/source hash;
- shared-signal hash
  `sha256(canonical_header || NUL || little_endian_float64_phase_bytes)`, where the header contains
  schema, capture, `lock_estimand_id`, numeric `locked_bin`, `k`, frame span, fs, dtype, and shape;
- source commit, scoped dirty state, approved-plan SHA-256, tests-attestation SHA-256, input hashes,
  payload-file hashes, and parent-stage manifest hashes.

Every estimate row carries run, capture, lock, window-set derivation, `k`, suite ID/hash, arm ID,
estimator ID, arm hash, shared-signal hash, and timing provenance.

Before executing the synthetic gate, generate a scoped `source_manifest.json` containing paths,
sizes, and SHA-256 for:

- `CLAUDE.md`, this approved plan, the local Ahmed PDF, `environment.yml`, and pytest configuration;
- every repository `src/**/*.py`, `scripts/**/*.py`, and `tests/**/*.py`, whether tracked or
  untracked, plus any root/project `conftest.py`;
- `scripts/live_demo_config.yaml`; and
- the Step 1a config plus the new resolved experiment config and capture registry.

The synthetic and every downstream stage verify this exact source-manifest hash and every member
hash. Git commit/dirty state remains provenance, but an approval-only commit may differ after the
gate only if all scoped files are clean and byte-identical. Any scoped source/config/test/plan
change invalidates the gate and requires rerunning it. Authorization/continuation YAMLs are
deliberately outside the scoped source set and are separately hashed by the stage that consumes
them.

Persist `test_attestation.json` with the exact ordered commands, source-manifest hash, ordered
pytest node IDs and counts, exit statuses, timestamps, Python/NumPy/SciPy/pytest versions, platform
and byte order, NumPy `show_config`/BLAS identity, and hashes of all test/config inputs. Persist
`conda_explicit.txt` from `conda list --explicit` and hash it from
`environment_attestation.json`, which also records the active environment path and full platform
identity. The synthetic gate binds all three attestation hashes; every descendant inherits them.

## 5. Gate enforcement, artifacts, and failures

### 5.1 Fail-closed gate contract

All executable scientific, runner, scorer, serializer, CLI, and portable-fixture test code is
implemented and passes before the synthetic gate is executed. No executable/config code is added
between the gate and real stages.

Every real command requires a completed synthetic stage manifest whose plan/model/config/scoped
source/test hashes match and whose six H/profile records are complete. Validation occurs before any
capture path is touched.

- `failed`, missing, stale, incomplete, or tampered gates always refuse execution.
- A complete but dirty, untracked, or otherwise promotion-ineligible synthetic bundle is draft
  evidence only and cannot parent any real-data stage.
- A matching `passed_under_declared_seed_and_configuration` gate satisfies the scientific gate but
  does not itself authorize real data.
- A `not_transferred_under_declared_assumptions` gate additionally requires a committed
  user-approved YAML under `experiments/m8_ahmed_transfer/continuations/` containing the exact gate
  manifest hash, plan/source-manifest hashes, approval date/approver label, allowed downstream
  stages, and substantive rationale. There is no `--force` flag; every descendant is permanently
  labeled `continued_after_negative_synthetic_gate`.

After the gate verdict and, if needed, the negative-continuation decision—but before the first real
capture path is touched—freeze one committed, hash-verified
`real_evaluation` authorization under `experiments/m8_ahmed_transfer/authorizations/`. It binds the
exact gate/source/environment/test manifests and preregisters the entire chain: smoke m1/k=0, all
eight full captures, both locks, all seven arms, the full stored grid, the
`evaluation_k_ge_1` comparative universe, all eight reference IDs, scorer/metrics, and required
parent relationships. It authorizes `real-smoke`, `real-radar`, and `score` together; later
parent-stage checks are mechanical and cannot depend on observed outputs. A negative-gate
continuation is an additional requirement, never a substitute.

The authorization and every real command require the synthetic manifest field
`promotion_eligible=true`, proving that every scoped plan/source/config/test file was committed and
clean when the gate was created. This is stricter than merely requiring `status=complete`.

Wrong stage/scope/capture/window/arm/parent refuses before capture access. If the authorized chain
is abandoned for any reason, finalize the current stage as `incomplete_abandoned` with a reason and
record the stop in `HISTORY.md`; never silently omit later results.

The default test suite never touches real raw data. `real-smoke` produces a structural-only
one-window bundle for m1/k=0, both locks, and all seven arms, with no numeric performance
assertion. `real-radar` and `score` require the same pre-data authorization plus exact completed
parents; reviewing an intermediate cannot change the authorized scope.

### 5.2 Immutable stage bundles

Each command writes a new immutable bundle through a same-filesystem staging directory, then
atomically renames it:

```text
results/m8_ahmed_transfer/
  synthetic/<run_id>/
    manifest.json
    provenance.json
    source_manifest.json
    test_attestation.json
    environment_attestation.json
    conda_explicit.txt
    resolved_config.yaml
    gate.json
    metrics.json
    evidence.npz
    transfer_curves.png
  smoke/<run_id>/
    manifest.json
    provenance.json
    resolved_config.yaml
    rows.json
    shared_evidence.npz
    ahmed_evidence.npz
    production_native_index.json
    production_evidence.npz
  radar/<run_id>/                 # same schema as smoke, full scope
  scored/<run_id>/
    manifest.json
    provenance.json
    resolved_config.yaml
    rows.json
    metrics.json
    partitions.json
    plots/*.png
```

Every child manifest stores and verifies its exact parent manifest SHA-256; finalized parent
directories are never reopened. Each stage root may have a `LATEST.json` pointing to a run ID and
manifest hash, but `LATEST.json` is outside the immutable run directory.

The manifest contains stage/schema/status, parent identities, exact payload allowlist, and each
payload's size/SHA-256. It excludes itself and external `LATEST.json`; the SHA-256 of the final
manifest bytes is the unambiguous stage identity used by parents/children. There is no
self-referential `bundle.json`.

JSON is strict: UTF-8, sorted keys, no NaN/Infinity, absent numeric values as `null`. NPZ loads with
`allow_pickle=False`, contains no object dtype, and uses explicit presence/support/selection masks
rather than ambiguous sentinels. Reasons/profiles/IDs in NPZ use versioned integer codebooks or
fixed-width Unicode, never object dtype.

`shared_evidence.npz` has one first-dimension row per `(capture, lock, k)` and stores phase,
timestamps, frequency grid/spectrum, and hashes. `ahmed_evidence.npz` has one row per Ahmed arm and
stores padded candidate/harmonic matrices, pre-exclusion scores, support/suppression masks,
selected/runner-up bins and scores, validity, and reason codes. Padding values are ignored only
through explicit Boolean presence masks.

Production's large nested native result uses a lossless deterministic serializer:
`production_native_index.json` recursively describes the exact dict/list/tuple/scalar tree and
references every ndarray leaf by a unique NPZ key; `production_evidence.npz` stores those
non-object arrays unchanged. Exact string mapping keys are required; cycles and unsupported objects
are fatal. Python scalar types are exact `None/bool/int/float/str`, with nonfinite Python floats
using explicit tagged JSON descriptors. Every `np.generic` scalar—including nonfinite values—is
stored as a dtype-tagged 0-D NPZ leaf. Reconstruction must be type- and value-equivalent to the
full native payload and pass the direct-call regression. No raw frame cube is allowed in either
serializer.

Full radar counts are pinned: 128 source spans; 256 `(capture, lock_estimand_id, k)` shared rows;
1,792 estimator rows for seven arms; 1,536 Ahmed evidence rows; and 256 production evidence rows.
The `k>=1` view is derived without estimation and contains 120 source spans, 240 shared rows, and
1,680 estimator rows.

Manifests transition `running -> complete|failed|incomplete_abandoned`. A failed or abandoned run
retains diagnostic provenance and its exception/reason but cannot publish `LATEST.json`. Only a
complete, clean, fully hash-matched stage may atomically update its own `LATEST.json`; dirty or
untracked scoped sources/configs remain useful draft outputs but are promotion-ineligible.

Every manifest, JSON metrics/partition file, table, plot/caption, and stdout summary inherits the
synthetic gate verdict and, after a negative gate, the exact continuation-rationale hash. Every
reference-derived output must visibly state
`exploratory_non_frozen`, `time_origin_id=start_wall_utc_approximate_v1`,
`origin_is_approximate=true`, and `not_eligible_for_promotion_or_final_agreement_claims`; row-only
labels are insufficient.

## 6. Verification and acceptance

### 6.1 Prerequisite

Fix `test_cli_execute_writes_strict_complete_artifacts` so it injects deterministic clean/dirty Git
provenance instead of depending on the ambient worktree. Add explicit clean eligible, dirty
ineligible, untracked-required-file ineligible, and external-config ineligible cases; scientific
outputs must be identical across provenance states. Do not weaken production promotion checks or
modify the canonical Step 1a bundle.

The currently observed focused result is 84 passed/1 failed because the real worktree is clean,
despite the handoff recording 85 passed in its earlier dirty state. After this prerequisite, the
same 85-test Step 1a/adapter set must pass deterministically.

### 6.2 Unit, oracle, and property tests

- Hand-computable and randomized independent slow-oracle checks for fixed-\(H\) accumulation,
  normalization, exact tie policy, candidate grids, suppression, and full score curves.
- Analytic coherent-phasor/direct-phase identity across multiple \(2\pi\) crossings, global complex
  rotations, amplitude scales, static RX phase offsets, and increments just below/at/above the
  branch boundary.
- Exact circular-noise power/scaling, PCG64 seed determinism, independent real/imaginary draws,
  realized SNR, and draw/signal hashes.
- Synthetic ADC cube pins the fast-time Hann/FFT bin and `delta_before_mean` chirp/RX aggregation;
  the 32-chirp/4-RX static-offset/amplitude oracle matches the single-channel extraction;
  `mean_phasor` is checked only as the declared single-channel audit.
- Exact 20 and 80 bpm conversion for both \(H\) values and all profiles, proving no residual
  `30*q` path.
- Strict support tests immediately below, at, and above \(Hq=f_s/2\); pin H=5/100 bpm supported at
  20 Hz and H=5/120 bpm boundary-degenerate.
- Suppression-bin rounding cases where target-bin divisibility both holds and does not hold.
- Same-\(H\) Ahmed BR dependency; changing production BR cannot change any Ahmed result.
- Missing `iq_swap=true` decoder word-layout oracle.

### 6.3 Interface, integration, and regression tests

- Exact suite arm set/IDs/hashes, shared phase extracted once, common immutable frame object/span,
  one decode per capture, malicious mutation detection, suite identity/hash attributes, classifier
  keying by arm ID, and immunity to caller config mutation.
- Full nested native production payload equals direct `run_window_dsp` on the same input/config/bin,
  including arrays with `equal_nan=True`; `WindowEstimate` is not this equality oracle.
- No production AHET classifier on Ahmed arms; missing/extra/duplicate arms and identity mismatch
  are fatal. Old/new callers of the extracted `src/m4/outcome.py` classifier are bit-identical.
- Finite non-identifiable per-window cases retain invalid rows/evidence with the declared reason
  precedence; nonfinite phase/spectrum, malformed types/shapes/configs, unexpected exceptions, and
  contract errors fail the run.
- Gate/authorization rejection occurs before capture `stat/open/decode` for missing, negative,
  stale, wrong-hash, wrong-scope, incomplete, or tampered artifacts. The one comprehensive
  pre-data authorization binds the whole smoke/radar/score chain, and a continuation authorization
  binds exactly one negative gate in addition. Abandonment produces an explicit terminal manifest.
- A complete synthetic gate with `promotion_eligible=false` is rejected before capture access and
  cannot be made real-data-eligible by an authorization file.
- Any rerun-selector candidate exception or fallback is fatal after diagnostic persistence; an
  exact lock-table mismatch is fatal even if selection otherwise succeeds.
- Lock and window grouping cannot mix. Equal numeric locks remain distinct. Full and `k>=1`
  summaries have the pinned 128/256/1792 and 120/240/1680 span/shared/estimator counts and derive
  from the same rows.
- Radar-only stage cannot access the registry reference mapping or any Masimo path; scoring cannot
  access the radar mapping, raw path, decoder, or estimator. Persisted span/reference partitions
  are hash-checked and identical for paired arms.
- Hand-computed HR and BR metric/coverage/partition oracles, including empty intersections,
  explicit `n_source/n_reference_admitted/n_radar_valid/n_joint`, mutually exclusive disposition
  precedence, partition sums, exact percentile method, `k>=1`-only comparative interpretation,
  protocol-stratified window-micro weighting, and per-metric capture-macro contributing counts.
- Unknown m3–m7 protocols remain unknown, sweep has an unavailable-target reason, and no
  commanded-rate metric is produced.
- Changing live metadata `hop_s` cannot alter the frozen non-overlapping grid.
- Registry preflight checks all eight direct hashes, 128 windows, locks, geometry, and capture
  roles under a separately marked real-data integration test. Mutation tests use temporary small
  files, never bytes under `results/live_demo`.
- Register a `real_data` pytest marker and a collection hook that skips it unless
  `M8_RUN_REAL_DATA_TESTS=1` is explicitly set. The default suite uses only portable temporary
  fixtures for normal execution, hash mutation, authorization scope, lock identity, and
  multidimensional `iq_swap=true` decoding.

### 6.4 Artifact and visual acceptance

- Assert exact per-stage file/key sets, schema versions, shapes, dtypes, the pinned
  256/1792/1536/256 row counts, codebooks, `allow_pickle=False`, strict JSON, explicit masks,
  lossless type-preserving production reconstruction including `np.generic` scalars, no raw cubes,
  no unexpected files, every source/test/environment/payload hash, and acyclic parent/manifest
  identities.
- Repeated scientific runs with identical inputs match after excluding run ID/time; do not require
  byte-identical PDF/NPZ containers unless deterministic serialization is explicitly guaranteed.
- Failed/dirty/incomplete bundles never promote and retain actionable diagnostics.
- Render synthetic full curves and real per-capture coverage/error plots; visually inspect labels,
  collision markers, support boundaries, protocol strata, `k=0` in-sample taint, approximate-origin
  taint, dependent prose duplicate, and negative/inconclusive annotations.
- Run the focused Step 1a suite, all new M8 tests, affected decoder/window/scorer tests, then the
  broader repository suite. Record exact commands, environment, counts, code/config/plan hashes,
  seed, and output hashes.

## 7. Risks, leakage controls, and assumptions

### Risks controlled by construction

- **Synthetic-model overclaim:** the primary name states that extracted clean phase has fundamental
  displacement lines only.
- **Masimo leakage:** mapping, locks, validity, support, profiles, and thresholds are frozen before
  the radar-only artifact; Masimo is unavailable to that stage.
- **Lock optimism:** recorded and rerun estimands are separate; `k=0` is retained but marked
  selection-in-sample, while `k>=1` is the only comparative universe.
- **Timing leakage:** no offset search; approximate origin is explicit.
- **Coverage bias:** every Cartesian arm row is required and invalidity partitions are reported.
- **Profile/winner bias:** every \(H\)/profile/lock/capture result is reported without selection.
- **Population overclaim:** every output states single-subject development/apparent status.
- **Negative-gate bypass:** hash-bound, committed rationale plus explicit user approval is required.
- **Outcome-adaptive stopping:** the entire smoke/radar/score chain is authorized before real data
  access, and any abandonment is an explicit artifact/history event.

### Assumptions fixed by this plan

- The conservative fundamentals-only control is primary because no source-fixed higher
  displacement coefficients exist in the repository; the user did not supply an alternative
  waveform.
- All eight captures are development/apparent; none becomes a holdout retroactively.
- Both recorded and current-production rerun locks have equal reporting status and remain separate.
- The stored frozen grid starts at `k=0`; comparative accuracy uses only `k>=1`.
- Real error pooling is descriptive and protocol-stratified; no all-capture, protocol, or
  suppression-arm blend is emitted.
- Full capture decoding is bounded to one capture at a time. If profiling shows that this exceeds
  available memory, implementation must stop and amend this approved plan before introducing a
  streaming decoder path.

## 8. Execution sequence and stop conditions

1. Approve this plan and record its exact SHA-256.
2. Implement/fix deterministic Step 1a provenance tests; run the focused baseline.
3. Implement all scientific core, suite, runner, scorer, serializer, CLI, registry/config, and
   portable fixture tests described here. No real capture or Masimo path is opened.
4. Run the focused, new, affected, and broad fixture-only suites; generate and freeze the scoped
   source manifest and test attestation.
5. Run and report the synthetic primary and audits; finalize the immutable gate without accessing
   any real capture.
6. Stop if the gate is failed. If it is a correct negative, stop unless the user explicitly
   approves a committed continuation rationale bound to that gate.
7. Freeze the one comprehensive real-evaluation authorization (and, after a negative gate, the
   separate continuation rationale) before any capture access.
8. Run `real-smoke` for m1/k=0, both locks/all seven arms and finalize its immutable structural
   bundle.
9. Mechanically verify the smoke parent, run all eight radar-only captures once, verify Cartesian
   completeness, and finalize the immutable radar bundle.
10. Mechanically verify the radar parent, load/verify Masimo, and produce the immutable descriptive
   scored bundle without changing scope.
11. Render/inspect outputs, run affected and broad tests, record every hash/result in `HISTORY.md`,
   update `HANDOFF.md`, and stop without promoting any Ahmed arm to production.

## 9. Independent review record

Five read-only reviewers inspected exact plan revisions, the local paper, and relevant repository
code. Their initial verdicts were all `REQUEST CHANGES`; no disagreement was hidden:

| Review | Principal finding | Resolution in this plan |
|---|---|---|
| Architecture | Mutable multi-stage bundle, gate/source-order conflict, self-referential hashing, and undefined suite/lock interfaces | Immutable parent-linked stages, scoped source gate after all code/tests, acyclic manifests, config-bound suites, exact locks |
| Mathematics | Phasor harmonics disappear after phase extraction; Nyquist equality is degenerate; noise/slip/bin rules were underspecified | Fundamentals-only name, strict \(Hq<f_s/2\), exact complex-noise and model-relative slip oracles, exact rFFT bins |
| Python | Nonexistent classifier module, incomplete serializer/config contracts, hidden selector fallback | Stable `src/m4/outcome.py`, type-preserving serializer, immutable suite configs, fatal selector failure/fallback |
| Testing | Real smoke/gate bypass, radar/Masimo leakage, ambiguous failure/count/metric schemas | Pre-access gate checks, separated registry access, exact fatal/invalid precedence, pinned counts/denominators/artifacts |
| Adversarial pre-mortem | \(k=0\) lock optimism, timing taint, adaptive stopping, incompatible pooling, aggregation/duplicate overclaims, dirty-gate path | \(k\ge1\) comparison, propagated taint, one pre-data authorization, protocol strata, production-geometry oracle, promotion-eligible gate only |

The central wording disagreement is resolved by repository math rather than consensus: Step 1b
tests the fixed-\(H\) all-integer accumulator on a phase signal whose declared clean generator has
only fundamental displacement lines. A future non-sinusoidal displacement control would require a
new source and plan.

Five exact-file reviews—architecture, mathematical correctness, Python implementation, testing,
and adversarial pre-mortem—must accept the final revision of this plan before step 1. Review
disagreements and their repository-evidence resolutions remain visible in the review record; they
are not silently averaged away.
