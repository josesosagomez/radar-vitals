# M8 Step 1b — Addendum A to the Ahmed HA phase transfer plan

Status: **user-approved 2026-07-30; cross-model re-review waived by the user (see §A8).
Implementation of the fixture-testable system is authorized; real-data access is not — that still
requires a passing gate plus the separate authorization of base plan §5.1.**

## A0. What this amends, and how authority composes

This addendum amends, but does not replace, the base plan:

| | Value |
|---|---|
| Base plan | `plans/m8_step1b_ahmed_transfer.md` |
| Base SHA-256 | `9294cb0589b9f0d8f50cdfa0ea893862b1f8ac7f26eb6fee31ee57d622da33ac` (verified 2026-07-30) |
| Base review status | five exact-file reviewers returned PASS on those bytes |

The base file is **not edited**, so its five exact-file acceptances remain valid for the bytes they
reviewed. The governing authority for Step 1b is the pair *(base plan at the SHA above, this
addendum at its own SHA)*. **Where the two conflict, this addendum wins.** Base plan §8 step 1
("record its exact SHA-256") is amended to require recording *both* hashes, and every downstream
manifest that binds `approved_plan_sha256` must bind both.

Rationale for an addendum rather than an in-place edit: the base bytes are the object five reviewers
accepted, and this repository already treats reviewed artifacts as immutable parent-linked objects
(base plan §5.2). Re-review scope is correspondingly narrow — see §A8.

## A1. Evidence

All numbers below are regenerable from a committed, data-free script:

```powershell
& 'C:\ProgramData\anaconda3\condabin\conda.bat' run -n radar-vitals `
  python scripts/m8_step1b_gate_prediction.py
```

`scripts/m8_step1b_gate_prediction.py` reimplements the base plan's §2.2 generator and §2.3
accumulator **independently of `src/m8/`**, so it remains a genuine external oracle once Step 1b is
implemented. It reads no project data and writes no files.

Its faithfulness to the base plan is established by exact agreement with the base plan's own
declared realization: `mean|n|² = 0.0959419233`, realized SNR `10.1799158` dB, maximum clean adjacent
increment `0.94111946` rad, minimum branch margin `2.20047319` rad (base plan lines 103 and 134).

Adding this script places a new file under `scripts/`, which base plan §4.3 scopes into
`source_manifest.json`. It must therefore be committed and clean before the gate runs.

### A1.1 The primary gate fails, and the cause is the candidate domain

Identical signal, identical grid, identical accumulator, native transform. Only the **heart
candidate domain** differs:

| Heart candidate domain | H=3 selection | H=5 selection | Truth |
|---|---|---|---|
| `[f_b, 100/60]` Hz — base plan §2.3 synthetic | 20.011 bpm **FAIL** | 20.011 bpm **FAIL** | 80 |
| `[0.80, 2.00]` Hz — base plan §2.3 real | 80.042 bpm **PASS** | 80.042 bpm **PASS** | 80 |

The synthetic heart domain begins at \(f_b\) and therefore admits the **breathing fundamental itself
as a heart candidate**. Because \(\beta_b/\beta_h = d_b/d_h = 2\) exactly, that candidate outscores
the true heart bin and wins. The real heart domain starts at 0.80 Hz and excludes it, whereupon the
true heart bin is selected exactly, for both \(H\), clean and noisy.

**The base plan's primary gate therefore fails for a reason that cannot arise on the real path it
gates.**

### A1.2 Zero-padding to 4096 is leakage-dominated

The base plan forbids zero-padding on real 600-frame windows but retains Step 1a's `n_fft=4096` on
the 561-sample rectangular synthetic record. On the synthetic domain:

| Transform | BR H=3 | BR H=5 |
|---|---|---|
| `n_fft=4096` (base plan §2.2) | 6.578 bpm **FAIL** | 4.933 bpm **FAIL** |
| native `n_fft=561` | 20.011 bpm **PASS** | 20.011 bpm **PASS** |

Padded selections (6.578, 4.933 bpm) are leakage-skirt bins, not subharmonics. The method cannot
locate a clean 20 bpm sinusoid through this lens.

### A1.3 The degeneracy is exact and band-limited

On the clean signal the accumulator's behaviour is exactly determined by integer-divisor structure,
not approximately:

- score at \(q=f_h\) and \(q=f_h/2\) are **bit-identical** (both equal `spectrum[b_h]/H`);
- a non-divisor candidate scores exactly `0.0000` (bin 13 on the 30 s grid — 13·{1,2,3} misses 40);
- when \(q=f_b\) reaches \(f_h\) within \(H\) harmonics, score ratio to \(q=f_h\) is exactly
  `2.0000` (H=3) and `3.0000` (H=5).

Consequently: **selection is the lowest in-band integer divisor of the truth bin whose harmonic row
captures the truth line.** This explains every observed outcome, including why BR H=3 fails on the
real 30 s grid (bin 5 is in-band and divides bin 10 → exact tie → lowest bin wins → 10 bpm) while
BR H=5 passes (bin 10's fifth harmonic reaches bin 40, breaking the tie in its favour).

## A2. Amendment 1 — native transform in the synthetic path

Base plan §2.2 (`n_fft=4096`) and §2.3 ("The primary synthetic run retains Step 1a zero-padding to
4096") are amended: **the transfer core uses the native transform length, `n_fft = len(phase)`, in
both the synthetic and real paths.**

Grounds, in order of weight:

1. Base plan §2.3 already states candidates are "exactly the `rfftfreq` bin centers" with "no
   parabolic interpolation, refinement, or off-grid evaluation". Zero-padding *is* spectral
   interpolation onto off-grid frequencies; the base plan contradicts itself here.
2. Ahmed's (23)–(26) are formulated over integer **bin indices**. Native length makes \(q, 2q, …\)
   exact bin multiples of a real DFT bin; heavy padding makes them interpolated values.
3. Synthetic and real paths must share one spectral treatment or the gate does not predict the
   real path.

The 4096-padded configuration is **retained as a named non-gating audit**
(`zero_padded_4096_audit`), reported alongside, so comparability with Step 1a is not lost.

**Disclosure:** this amendment moves BR H=3 and BR H=5 from FAIL to PASS (§A1.2). It is adopted for
the three reasons above, declared before implementation, and it does **not** change the aggregate
verdict — HR still fails under the base plan's synthetic domain.

## A3. Amendment 2 — compute and report both heart domains

Base plan §2.3's synthetic heart domain `[f_b, 100/60]` Hz is **retained**, not deleted: admitting
the breathing fundamental is the "collision scenario" of base plan §1, and the resulting negative is
a real scientific finding about fixed-\(H\) accumulation under collision. The real path's
`[0.80, 2.00]` Hz band is added alongside it, because a control strictly more adversarial than the
real path cannot on its own predict how the real path will behave.

Both are computed and reported for every \(H\), profile, and grid:

| Domain ID | Heart candidates | Role |
|---|---|---|
| `collision_domain_from_fb` | `[f_b, 100/60]` Hz | reported; matches base plan §2.3 synthetic row |
| `real_representative_domain` | `[0.80, 2.00]` Hz | reported; matches base plan §2.3 real row |

**Neither domain gates real-data access.** An earlier revision of this addendum designated the
real-representative domain as "gating", which contradicted §A4.2 and would have made the gate turn
on the domain choice — exactly the tuning-to-pass risk this addendum set out to control. That
designation is withdrawn. Gating is defined solely in §A4.2 and rests on reproducing the predicted
outcomes of **both** domains, including the collision domain's predicted failure.

Three controls remain mandatory:

1. The collision-domain result is reported with equal prominence in every manifest, table, plot, and
   stdout summary. It is never omitted, footnoted, or described as a sensitivity.
2. Both domains are frozen in the resolved config **before** implementation begins, with the
   predicted outcomes of §A4 already committed.
3. Base plan §4 keeps its prohibition: no arm, domain, \(H\), or profile may be selected by real-data
   error, and neither domain may be dropped after real results are seen.

## A4. Amendment 3 — predeclared prediction, and a gate that tests the implementation

Base plan §2.4's gate conflates two questions. This addendum separates them into two verdicts, both
persisted in `gate.json`.

### A4.1 Predeclared prediction (frozen before implementation)

The following is committed **now**, from §A1, and is falsifiable:

- **P1 — selection rule.** On the clean signal, the selected candidate is the lowest in-band integer
  divisor of the truth bin whose harmonic row captures the truth line.
- **P2 — subharmonic degeneracy.** score(\(q=f_h\)) equals score(\(q=f_h/2\)) when both are in band,
  and a non-divisor candidate carries no signal.
- **P3 — collision ratio.** With \(q=f_b\) in the heart domain, the score ratio to \(q=f_h\) is
  2 for H=3 and 3 for H=5.

**P2 and P3 are exact in exact arithmetic but not in float64.** An earlier revision of this addendum
described them as holding "bit-identically" and "exactly zero"; that came from reading 4-decimal
output and was an overclaim, corrected here. Measured on the real 30 s grid by
`scripts/m8_step1b_gate_prediction.py`, whose `degeneracy_report()` now prints these at full
precision:

| Quantity | Observed | Gate tolerance |
|---|---|---|
| P2 relative difference | 2.0e-15 (H=3), 6.8e-16 (H=5) | ≤ 1e-12 relative |
| P3 ratio | 1.999999999999994 (H=3), 2.999999999999981 (H=5) | ≤ 1e-12 relative |
| P3 non-divisor score | 1.5e-12 absolute = 8.8e-16 of the 1683.89 peak | ≤ 1e-12 relative to peak |

The tolerances are roughly 1000× the observed round-off, so they survive a different BLAS while
remaining about nine orders of magnitude below any real spectral line.

**P2 and P3 additionally require the declared fundamentals to fall exactly on rFFT bin centres**, a
precondition an earlier revision omitted. They are identities about which bins a harmonic row lands
on, so they hold only when each line occupies a single bin. Measured 2026-07-30:

| Grid | \(f_b\), \(f_h\) in bins | P2 relative difference |
|---|---|---|
| primary PRF, 561 samples | 4.9976, 19.990 — **off-grid** | 8.1e-04 (H=3), 1.7e-03 (H=5) |
| 20 Hz, 300 samples | 5, 20 — on-grid | 0.0 (H=3), 6.8e-16 (H=5) |
| 20 Hz, 600 samples | 10, 40 — on-grid | 2.0e-15 (H=3), 6.8e-16 (H=5) |

Off-grid lines leak across neighbouring bins and degrade the identities by roughly twelve orders of
magnitude, which is spectral leakage rather than an implementation fault. The gate therefore
evaluates **P1 and P4 on every grid** and **P2 and P3 only on the on-grid 20 Hz realization**, and
refuses outright to evaluate P2/P3 off-grid rather than silently reporting a failure.

This does **not** give the 20 Hz audit authority over the primary grid. Base plan §2.2 forbids the
audit upgrading, replacing, or downgrading the primary-PRF verdict, and that constraint is about the
**transfer** verdict, which remains computed on the primary grid alone. P2/P3 are implementation
identities, not transfer claims.
- **P4 — selection table.** The full grid × domain × vital × \(H\) table emitted by
  `scripts/m8_step1b_gate_prediction.py`, whose stdout SHA-256 is recorded in the resolved config at
  freeze time.

### A4.2 Two verdicts

**Gate verdict** — the sole criterion governing real-data eligibility. `passed` requires base plan
§2.4 correctness in full, **plus** exact reproduction of P1–P4 by the implementation, across **both**
domains of §A3. Anything else is `failed`.

Reproducing a derived prediction is a strictly stronger implementation check than truth recovery:
it pins the mechanism, on the clean signal, with zero tolerance fudge.

Critically, P1–P4 include the predicted **failures** — reproducing "HR selects the \(f_b\) bin on
`collision_domain_from_fb`" is part of passing. The gate therefore cannot be made to pass by
choosing a favourable candidate domain, which is what makes it immune to the tuning-to-pass
objection that §A2 and §A3 would otherwise attract. A gate that tests whether the *method works* can
be gamed by narrowing the band; a gate that tests whether the *code matches the mathematics* cannot.

**Transfer verdict** — scientific result, reported, **non-gating**. Base plan §2.4's four
truth-recovery checks, evaluated and reported *per domain*:

| Domain | Predicted transfer verdict |
|---|---|
| `real_representative_domain` | `transferred_under_declared_seed_and_configuration` |
| `collision_domain_from_fb` | `not_transferred_under_declared_assumptions` (HR selects \(f_b\)) |

Base plan §5.1's continuation-rationale machinery now binds the **gate** verdict, not the transfer
verdict. Every §7 leakage control — k≥1-only comparison, one pre-data authorization, radar/Masimo
separation, no offset search, protocol strata, `exploratory_non_frozen` labelling — is unchanged.
Those, not the synthetic gate, are what prevent real-data overclaim.

## A5. Amendment 4 — correct §6.1's factual claim

Base plan §6.1 states: "The currently observed focused result is 84 passed/1 failed because the real
worktree is clean."

Verified on 2026-07-30: the worktree is **dirty** (5 modified documentation files plus the untracked
plan), and `test_cli_execute_writes_strict_complete_artifacts` **passes** (`1 passed, 1821
deselected`). The observation is inverted.

The underlying diagnosis survives and the prerequisite is still required: the test is
provenance-state-dependent, flipping with ambient worktree cleanliness. That is exactly the
fragility base plan §6.1 sets out to remove.

Additionally: `tests/test_m8_ahmed_fig8.py` alone reports **33 passed**, so the "85-test Step 1a/
adapter set" spans files the base plan never enumerates. The implementation must **enumerate the
exact pytest node IDs** of that set in `test_attestation.json` rather than asserting a bare count.

## A6. Amendment 5 — remove the stale baseline note

Base plan §1 lines 5–8 state that "the older branch/dirty-state description in `HANDOFF.md` is
stale". `HANDOFF.md` was rewritten on 2026-07-30 and now agrees with the plan. That sentence is
deleted; the instruction that all claims and hashes be rechecked at execution time is retained.

## A6b. Amendment 5 — corrected `live_demo_config.yaml` hash, and a repo-wide EOL pin

Base plan §3.1 records `scripts/live_demo_config.yaml` as
`8bc7438e887ddcb243cec124cbc316a2279429bad4bdffabb4577f23f3179d7e`. That value was computed on a
**CRLF** working copy and is not reproducible.

`core.autocrlf=true` with no `.gitattributes` stores LF in the index but checks out CRLF on Windows,
so committed files hashed differently in a fresh clone than in a long-lived working tree. Verified
2026-07-30 in a throwaway worktree: the base plan itself hashed `fa64b234…` rather than the reviewed
`9294cb05…`. Since base plan §4.3 hashes every scoped source, config, and test file into
`source_manifest.json` and §5.1 binds `approved_plan_sha256`, the entire provenance scheme would have
produced machine-dependent values.

Resolved by committing `.gitattributes` with `* text=auto eol=lf` plus explicit binary declarations.
The index was already uniformly LF, so no committed content changed. After renormalizing the working
tree, the base plan, this addendum, and the evidence script all hash to their recorded values both
locally and in a fresh worktree.

**Amendment:** the binding value for `scripts/live_demo_config.yaml` is its LF hash

`0862076d6a8f7a05278d2c43dd259bcb8f7e66268f9d3e1fc49e81798ba19f0a`

Base plan §3.1's `8bc7438e…` is superseded. All other §3.3 registry hashes are unaffected:
they cover `adc_stream.bin`, Masimo CSVs, and metadata/warmup JSON under `results/live_demo/`, which
is gitignored and therefore never subject to Git EOL conversion. Re-verify every one of them at
execution time regardless, per base plan §1.

**Known consequence, not repaired here.** The canonical Step 1a bundle no longer self-verifies two of
its own text payloads: `metrics_sha256` and `resolved_config_sha256` in its `provenance.json` no
longer match the on-disk files, because the runner wrote them through Python text mode on Windows
(CRLF) and hashed those bytes while Git stored LF. Its scientific outputs still verify —
`figure_png_sha256`, `figure_pdf_sha256`, `plan_sha256`, `implementation_module_sha256`, and
`runner_script_sha256` all match. The pin did not break the bundle; it exposed a pre-existing
platform dependency. Repair is a scientific-integrity decision for the user under base plan §6.1
("do not modify the canonical Step 1a bundle") and is **not** a Step 1b blocker: Step 1b parents its
stages on its own synthetic gate and reads only `experiments/m8_ahmed_fig8/config.yaml`.

## A7. Withdrawn suggestion — `simulate_eq14` is not a displacement source

A harmonically rich displacement control was considered and is **withdrawn**. Inspection of
`src/m8/ahmed_fig8.py::simulate_eq14` shows it produces
\(A\cos(\eta\sin(2\pi f t)+\theta_0)\) — a *real received-signal* model whose harmonics are Bessel
sidebands of the carrier. It is not a chest-displacement waveform and cannot be fed through
`extract_chest_phase`.

Base plan §2.1 is therefore correct as written: a non-sinusoidal displacement control requires a
separately sourced, preregistered future experiment. No coefficients are invented here.

## A8. What is unchanged, and re-review scope

Unchanged: the capture registry and all hashes (spot-verified: `scripts/live_demo_config.yaml` =
`8bc7438e…9d7e`); window/row counts 128/120 and 256/1792/1536/256, 240/1680 (all re-derived
independently and correct); §3 estimands, lock separation, and timing rules; §4 architecture and
serializer contracts; §5 immutable bundles and authorization flow; §7 leakage controls.

Also verified present: `extract_chest_phase`, `ha_estimate_rr`, `run_window_dsp`,
`as_window_estimate`, `run_config_hash`, `run_warmup_selection`, `hr_reference`, `br_reference`,
`accumulate_harmonics`, `SUPPRESSION_PROFILES`, and `classify_window_outcome` at
`scripts/diagnose_bin_drift.py:592`. `src/m4/outcome.py` correctly does not yet exist.

**Cross-model re-review was waived by the user on 2026-07-30.** Recorded here because base plan §9
otherwise requires exact-file acceptance before implementation, and because one internal
inconsistency (§A9) was found by re-reading rather than by review. The questions re-review would
have been asked, left standing as the residual risk this addendum carries into implementation:

| Reviewer | Question that went unasked |
|---|---|
| Mathematics | Are P1–P3 correctly derived, and is native-vs-padded reasoning sound? |
| Adversarial pre-mortem | Do §A2/§A3 constitute tuning-to-pass? Are the §A3 controls sufficient? |
| Testing | Is the gate still fail-closed for real-data access? |
| Architecture | Does composite base+addendum authority break any manifest hash binding? |

Python implementation review was not required regardless: no interface in base plan §4 is modified.

## A9. Resolved decision — gate criterion

**Decided by the user on 2026-07-30: the gate is reproduction of P1–P4 (§A4.2).**

The question was what must be true before the implementation may open the eight real captures.
Three coherent answers existed:

| | Gate criterion | Predicted outcome | Cost |
|---|---|---|---|
| A | Truth recovery on `collision_domain_from_fb` (base plan, unamended) | fails | continuation rationale; all descendants labelled `continued_after_negative_synthetic_gate` |
| B | Truth recovery on `real_representative_domain` | passes | vulnerable to tuning-to-pass |
| **C** | **Reproduction of P1–P4 across both domains** | **passes if code matches theory** | **none — chosen** |

C was chosen because P1–P4 include the predicted failures, so the gate cannot be made to pass by
choosing a favourable candidate domain. It tests whether the code matches the mathematics rather
than whether the method works, which is the property a pre-real-data gate actually needs.

**This decision resolved a contradiction in an earlier revision of this addendum**, in which §A3
designated one domain as "gating" while §A4.2 made both transfer verdicts non-gating. §A3's gating
designation is withdrawn; §A4.2 is now the sole definition of gating. No open decisions remain.
