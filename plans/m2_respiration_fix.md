# M2 — Respiration-collapse root cause & fix plan

> Milestone M2 (`plans/implementation_plan.md`). Written 2026-07-25; revised 2026-07-25 in
> cross-review (`plans/m2_fix_cross_review.md`, M2R-01…07). **Plan → cross-review → implement**
> (CLAUDE.md §5.2/§6 — this touches peak-picking). Offline, on the 4 existing captures.

---

## 1. Root cause — reproduced and verified, with one corrected claim (M2R-01)

The respiration estimator silently reports **6.0 bpm with `resp_valid=True`** on many windows.
Reproduced at the **corrected bin** from the checkpointed intermediates
(`results/live_demo/*_replay_unknown/live_intermediates.npz` — natural = the `massimo1` replay,
sweep = the `massimo2`-era sweep replay at bin 26, paced 16 = the `massimo2` replay):

| session | windows | floor-pinned (br ≤ 6.5) | of those `resp_valid=1` |
|---|---|---|---|
| natural | 51 | 13 | **13** |
| sweep | 151 | 16 | **16** |
| paced 16 | 51 | 0 | 0 |

**6.0 bpm = 0.10 Hz = `band_hz[0]`, the exact lower edge of the `[0.10, 0.50] Hz` band.** Two
observed **selection classes** put estimates there (named by what is observed in the stored
branch outputs, not by inferred cause — M2R-01 r2), and a fusion flaw blesses them as valid:

- **Class A — both-branch edge selection** (FFT and HA both select the edge bin; natural 12/13,
  sweep 11/16). In **16 of these 23** windows (natural 8, sweep 8) the band magnitudes are a
  monotone non-increasing decay from below the band — e.g. natural win 38: `1.00, 0.56, 0.28,
  0.07, …` at 6/8/10/12 bpm, while the strongest in-band local maximum (16–18 bpm) reaches only
  `0.79–0.84` — the signature of residual low-frequency leakage after *linear* detrend (a 1/f
  drift tail whose energy is highest at the lowest band bin). That leakage cause is attributed
  **only to these 16**; the remaining 7 are the spectrally unresolved local-max edge cases
  (below). In all 23, `fft_estimate_rr`'s `argmax` selects the edge bin, HA pins with it, and
  `fuse_estimates` reads `|fft−ha|≈0` as **agreement → medium/high → `resp_valid=True`**.
  Agreement at the floor is a **shared failure**, not a confirmation.
- **Class B — HA-only edge selection** (FFT off the floor; sweep wins 77/78/112/115/143 —
  `fft_rr` 16.8–18.0 bpm except win 143 at 7.13 with `stft = 9.97` — and natural win 46,
  `fft_rr = 14.33`). In wins **77/78/112/115** the stored harmonic powers support
  **3rd-harmonic inheritance**: the 18 bpm bin is 2.0–3.3× the band floor, so the 6 bpm
  candidate (`3 × 6 = 18`) inherits that line's power, and the fundamental-support guard
  (`fund_power > median noise floor`, `src/respiration.py:308`) is too weak to stop it. Wins
  **143 and 46** are **unresolved edge-score wins** with no such support (win 143's 18 bpm bin
  sits at the band floor, 1.00×; win 46's 6 bpm fundamental, 1.79× floor, outweighs its own
  18 bpm term, 1.31×) — inheritance is *not* claimed for them; they are suppressed only by the
  independent edge veto (§2.3). In fusion the `(ha_strong and stft_stable_md)` path (`:472`)
  grants **medium** and **selects HA (6.0)** — even though the quantity that was *stable* (the
  STFT median, ≈ 16–18 bpm) is a **different rate**. **STFT stability of one rate blesses a
  different HA value.**
- **Paced-16 never pins** (0/51): strong, stable pacing leaves neither a dominant edge decay nor
  subharmonic ambiguity — confirming this is a weak-/variable-signal failure.

**Structural observation — corrected in cross-review (M2R-01): it is *partial*, not universal.**
The original claim ("in every pinned case the 6 bpm bin is not a genuine local maximum") is
**false**. Measured on the corrected-bin NPZs: in **22 of 29** pinned windows the 6 bpm bin fails
a strict local-maximum test (the monotone decay of the 16 leakage-signature Class-A windows, or a
higher in-band neighbour in Class B — e.g. sweep win 77: `spec(8 bpm) = 1.03 × spec(6 bpm)`). But
in **7 of 29** — natural wins 41/42/43/47, sweep wins 119/120/137 (zero-based, all Class-A
windows) — the 6 bpm bin **is** a strict full-spectrum local maximum, with margins up to ~23 %.
Those seven are **spectrally unresolved**: spectral structure alone cannot classify them as
leakage vs a genuine slow component, and they must **not** be classified by whether STFT or the
reference favours another rate (CLAUDE.md §4). Consequence: the local-max predicates (§2.1/§2.2)
are a **partial structural rejection** — they remove the 16 leakage-signature Class-A pins
(8/12 natural, 8/11 sweep) and all 6 Class-B pins — and **only the independent band-edge validity
policy (§2.3) suppresses the remaining seven**. The edge policy is load-bearing, not
belt-and-suspenders. All counts in this section are regenerated, with NPZ SHA-256 hashes, by the
tracked diagnostic (§4.3).

---

## 2. The fix — three targeted, principled changes

### 2.1 `fft_estimate_rr` — require a genuine local maximum (kills the 16 leakage-signature Class-A pins)
The in-band `argmax` must be a genuine local maximum of the **full** spectrum, with an explicit
plateau policy (M2R-06): `spec[p] > spec[p−1]` (strict against the lower-index neighbour) **and**
`spec[p] ≥ spec[p+1]` (plateau-tolerant against the upper), where `p` is the **global** bin index —
so the band-edge bin is compared to the bin *just below the band*. Justification, from spectral
structure only: the shape being rejected is a monotone non-increasing decay from below the band
(the leakage tail), so strictness is required on the low side; equality on the high side is the
half-bin-split signature of a genuine line at 30 s resolution (a true 17 bpm rate splits its energy
between the 16 and 18 bpm bins), not of leakage. If the top-scoring in-band bin fails, fall back to
the strongest in-band bin that passes; if none passes, return NaN (no valid respiration peak this
window). On fallback, **recompute `fft_peak_snr_db` and all peak fields for the actually selected
bin**. Return separate evidence fields — `fft_band_argmax_bin`, `fft_band_argmax_is_local_max`
(the validity verdict on the *original* argmax, kept distinct from the fallback's),
`fft_selected_bin` (−1 on the NaN path) — because local-peak validity and band-edge status are
**different facts** (M2R-02), and a fallback must be reconstructable from the argmax verdict plus
the selected bin (M2R-05 r2). **Expected effect (measured, §1):** removes the 16
leakage-signature Class-A pins (8/12 natural, 8/11 sweep). The seven strict-local-max edge
windows still return 6.0 from this stage; an edge-bin local max is still *reported* as a scalar
estimate, but §2.3 makes it permanently non-valid at the system level.

### 2.2 `ha_estimate_rr` — fundamental must be a real spectral line (kills all six Class-B pins)
Replace the weak `fund_power > noise_floor` guard with the **same local-maximum predicate** (same
plateau policy) on the candidate's fundamental bin. A candidate whose "fundamental" is not a
distinct spectral line is rejected, so a candidate cannot win purely on harmonic-power
coincidence. In **all six** measured Class-B windows the 6 bpm fundamental fails the predicate
(e.g. win 77: `spec(8 bpm) = 1.03 × spec(6 bpm)`, so the `≥`-upper test fails) while the
candidate at the strongest in-band local maximum passes. **Declared residual risk (M2R-06):** a
genuine weak fundamental that noise nudges below a neighbour is rejected — a coverage loss on
low-SNR windows, accepted and reported honestly, never compensated by tuning; the noisy synthetic
controls (§3.5) quantify it. Evidence fields (all persisted per window, §4.3): per-candidate
`ha_candidate_freqs_hz` / `ha_candidate_scores` / `ha_fund_is_local_max`, winner
`ha_selected_bin`.

### 2.3 `fuse_estimates` — validity can never bless a floor-pin (M2 done-when #2)
- **Band-edge veto by bin identity, not frequency arithmetic (M2R-02):** the selection is vetoed
  iff the selected estimator's **raw selected bin equals the first in-band FFT bin** (the FFT and
  HA result dicts carry the raw selected bin and an `is_edge_bin` flag; fusion vetoes on the flag
  of whichever source it selects). The originally proposed `selected_hz ≤ band_hz[0] + freq_res/2`
  test is **wrong** when the band edge is not bin-aligned (the first in-band bin can lie almost a
  full bin above `lo`); bin identity is robust to aligned and non-aligned edges, and both cases are
  tested. On veto: `resp_valid=False`, `resp_confidence="low"`, evidence fields `resp_edge_veto`
  (+ reason); emission follows the existing `emit_low_confidence` semantics. **Conservative
  consequence, accepted and tested (M2R-02):** a genuine ≈6 bpm breather whose line falls on the
  edge bin is emitted only as invalid and never drives ECA. This is mandated by M2 done-when #2 —
  a floor-pinned `f_r` can never be `resp_valid=True` — and it is this veto, not §2.1/§2.2, that
  suppresses the seven spectrally unresolved windows of §1.
- **STFT consistency on every STFT-dependent branch (M2R-03)** — the high branch, the
  `(ha_strong and stft_stable)` medium branch, **and** the FFT-fallback branch: STFT stability may
  support a selection only if `stft_rr_bpm` is finite, `stft_valid_fraction ≥
  stft_min_valid_fraction`, and `|selected_hz·60 − stft_rr_bpm| ≤ stft_match_bpm`. Rationale:
  temporal stability of a *different* rate is not corroboration of the selected rate — on any
  branch, not just the one observed failing. The agreement-only medium clause (FFT≈HA within
  `fft_ha_agree_bpm_medium`) is unchanged; it uses no STFT evidence.
- **New config keys (new keys; no existing threshold changes):** `stft_match_bpm`, default
  **6.0 bpm** = one STFT-subwindow FFT bin (`60 / stft_subwindow_s` at the 10 s default) — the
  coarsest quantum either compared quantity can be trusted to, assuming nothing about refinement
  accuracy; and `stft_min_valid_fraction`, default **0.5** — a median over fewer than half the
  subwindows is not a stability measurement of the window (this matters after §2.1: subwindow
  rejections shrink the surviving set, and a small survivor set with low std must not count as
  "stable"). Both derived from resolution/estimator semantics, not from reference agreement, and
  deliberately **not** a reuse of the differently-named `fft_ha_agree_bpm_medium` (M2R-03). Keys
  are added to the respiration config section (`scripts/live_demo_config.yaml` and the step-5
  config) alongside the existing gates.

**No band change.** The `[0.10, 0.50] Hz` band and all existing config thresholds stay; legitimate
estimates away from the band edge (e.g. paced-16 at 16 bpm, a strong local max matching a stable
STFT) are unaffected. The deliberate exception is the edge bin itself, whose permanent invalidity
is the M2 invariant (above).

---

## 3. Tests (mechanism controls assert their pre-fix precondition, then the post-fix outcome)

New tests in `tests/` (synthetic-control-first, CLAUDE.md §5.3; fixed seeds throughout):
1. **Class-A (leakage) control** — synthetic phase = large 0.05 Hz drift + weak 0.28 Hz (17 bpm)
   breathing. **Precondition asserted first** (M2R-06): the band argmax falls on the edge bin and
   that bin fails the §2.1 predicate (monotone decay from below). Then assert `fft_estimate_rr`
   returns ≈17 bpm via the fallback, with `fft_selected_bin ≠ fft_band_argmax_bin` and SNR
   recomputed for the selected bin.
2. **Class-B (harmonic-inheritance) control** — synthetic phase = strong 0.30 Hz (18 bpm) breathing with harmonics,
   **no** real 0.10 Hz component. **Precondition asserted first**: under the old weighting the
   6 bpm candidate's score beats the true candidate while its fundamental fails the new predicate.
   Then assert `ha_estimate_rr` does **not** select 6 bpm and selects ≈18 bpm.
3. **Validity invariant (regression)** — edge-bin selection → `fuse_estimates` returns
   `resp_valid=False`, for a **bin-aligned and a non-bin-aligned** band edge (M2R-02); the
   STFT-stable-but-disagreeing case on **each** STFT-dependent branch — high, HA-medium,
   FFT-fallback — is not blessed (M2R-03); STFT NaN, single-valid-subwindow (std NaN), and
   `stft_valid_fraction` below threshold all fail the STFT predicate.
4. **No-regression on good signal** — a clean 16 bpm synthetic still returns ≈16 bpm,
   `resp_valid=True` (the STFT match passes).
5. **Local-max policy controls** (M2R-06) — genuine peak at a bin centre (accepted); exact
   half-bin line producing equal adjacent bins (accepted via the plateau rule); genuine band-edge
   line (scalar reported, fusion invalid — pins the conservative consequence of §2.3); no in-band
   local max at all (NaN path); HA weak-fundamental-with-strong-harmonics (accepted while the
   fundamental is a true local max; rejected when it is not); noisy fixed-seed variants of each.
6. **Evidence-schema test** (M2R-05) — the live NPZ writer persists every new decision field of
   §4.3; assert presence and shapes.
7. **Live/offline parity — scoped to the M2 invariant** (M2R-07 r2) — `fuse_estimates` is shared
   with `steps/step_5/extract_breathing_rate.py`. Parity is asserted **only** for the invariant:
   a first-in-band-bin selection is invalid under both callers. **Expected divergence is asserted
   explicitly, not papered over**: an estimate in the offline-only low margin (e.g. 8 bpm, inside
   `lo + 2 bpm` but not the first bin) is valid from fusion yet invalidated by step-5's
   edge lock, and likewise at the high edge — both tested as *expected* differences. Step-5's
   high-edge lock behaviour is unchanged.

---

## 4. Verification (M2 "done when")

1. **Root cause documented** — this §1 (including the corrected structural observation), and
   appended to `HISTORY.md` at session end.
2. **Validity semantics correct** — a floor-pinned `f_r` can never be `resp_valid=True`; test #3
   pins it. ✔ by §2.3.
3. **Intermediates: must be extended, not "already met" (M2R-05).** The current NPZ persists only
   the scalar FFT/HA/STFT rates and the spectra (`scripts/live_demo.py:1466–1481`) — none of the
   new decisions would be traceable from it. **A field that exists only in an in-memory result
   dict is not evidence — every field below is persisted per window by the live NPZ writer
   (M2R-05 r2).** Extend the NPZ schema with: `fft_band_argmax_bin`,
   `fft_band_argmax_is_local_max` (the verdict on the original argmax, distinct from the
   fallback's validity), `fft_selected_bin` (−1 on the NaN path); the **per-candidate** HA arrays
   `ha_candidate_freqs_hz`, `ha_candidate_scores`, `ha_fund_is_local_max` (so a rejected
   candidate is reconstructable from persisted decisions, without re-running the picker on the
   spectrum), plus winner `ha_selected_bin`; `stft_rr_std_bpm`, `stft_valid_fraction`;
   `resp_edge_veto` (+ reason code); and the fusion branch taken. The tracked
   `scripts/diagnose_respiration_collapse.py` regenerates §1's counts from the NPZs and records
   their SHA-256, the git revision, and the config (CLAUDE.md §3); the schema test (§3.6) pins
   all of these fields.
4. **All four captures reprocess without the false-confidence flag; suite green.** Reprocess the 4
   captures (replay), assert **0 windows** with (br at floor **and** `resp_valid=True`); confirm
   **paced-16 is unchanged** (still ≈16 bpm); confirm the seven unresolved edge windows of §1 are
   invalid **via `resp_edge_veto`** (not via local-max rejection). Report every window whose BR
   value or validity changed relative to the pre-fix runs — including formerly pinned or
   edge-locked windows the revised picker moves to a valid non-edge line (M2R-07 r2). Full
   `pytest` stays green (currently 796p/1xf).
5. **BR outcome under the frozen M3 comparator** — score the reprocessed BR against
   `notes/comparator_prespec_br.md` on the 3 Masimo captures. **Whatever the outcome, it is
   reported** (M4 will automate this; until M4 exists, score via the comparator's rules directly).
6. **HR-path interaction — measured with transition accounting and a predeclared stop (M2R-04).**
   `f_r` feeds ECA in the heart path (`live_demo.py:478`, `f_r_hz=None` when respiration is
   invalid). The fix changes `f_r` in **three** ways, not two: (i) floor-pin → `None` (ECA
   skipped — previously a pinned 0.10 Hz was fed to ECA, cancelling harmonics at 0.10/0.20/
   **0.30** Hz, i.e. near 18 bpm, potentially corrupting the cardiac band); (ii) floor-pin → a
   **different valid `f_r`** via the §2.1 fallback (ECA then cancels *different* harmonics);
   (iii) unchanged. Reprocess the 4 captures and produce **per-window before/after transition
   accounting**: `f_r` (value/None), `resp_valid`, ECA skipped/projected counts, HR validity and
   value — summarised by category. Score HR under the frozen `notes/comparator_prespec.md` on the
   **three referenced captures**; `live_test1` is **engineering-only** (coverage/transition
   accounting; no accuracy claim — it has no reference). **Predeclared decision rule:** if
   HR-valid coverage or HR accuracy is degraded — or the result is mixed — on any referenced
   capture, **report the numbers and stop for a user decision** under the frozen HR comparator.
   Do not tune BR or HR to fix it, and do not silently roll back the M2 validity fix.

**Negative-result exit (binding).** If, after the pins are removed, radar BR still does not track
`rr_bpm`/metronome — e.g. the all-harmonic HA adaptation cannot estimate BR — then **"BR does not
transfer" is the finding**, recorded in `HISTORY.md`, and M2 is complete. We do **not** tune the
estimator to make BR agree (CLAUDE.md §4; that folds into M8).

## 5. Out of scope
- Any **change** to the HR/AHET path code (untouched — but its **output is re-checked**, §4.6).
- Any band change or change to an **existing** config threshold (none; §2.3 adds two **new** keys,
  with their derivation stated there).
- The offline step-5 post-fusion edge lock (`steps/step_5/extract_breathing_rate.py:477–489`,
  `edge_lock_margin_bpm`, default 2 bpm, both edges): **kept unchanged on both edges** (M2R-07).
  Intended relationship: the new in-fusion low-edge veto sits strictly inside the offline
  low-edge lock (the 6.0 bpm edge bin is ≤ lo + 2 bpm), so **only the first-bin veto outcome is
  guaranteed unchanged offline** (invalid before via the lock, invalid now via fusion). Estimates
  the revised picker moves **off** the edge bin can change offline outcomes — e.g. a formerly
  edge-locked 6.0 bpm window becoming a valid non-edge line — which is deliberate and reported
  in §4.4's reprocessing (M2R-07 r2). The live path — which previously had **no** edge gate at
  all — now enforces the invariant inside fusion, closing the live/offline gap for the
  invariant itself; other live/offline differences (the 2 bpm margins, the high edge) remain
  offline-only and are asserted as *expected* divergence in §3.7. Consolidating the two policies
  is deferred and would get its own cross-review.
- Building M4 (the BR-scoring is done directly against the M3 comparator until M4 exists).

## 6. Files
- `src/respiration.py` — `fft_estimate_rr`, `ha_estimate_rr`, `fuse_estimates` (peak validity +
  evidence fields).
- `scripts/live_demo.py` — NPZ writer only: persist the new decision/evidence fields (§4.3). No
  DSP change.
- `scripts/live_demo_config.yaml` + step-5 config — add `stft_match_bpm`,
  `stft_min_valid_fraction` (§2.3).
- `tests/test_respiration_*.py` — the tests in §3.
- `scripts/diagnose_respiration_collapse.py` — new, tracked (regenerates §1's counts; records NPZ
  SHA-256 + git revision + config).
- `HISTORY.md` — root cause + outcome (session end).
