# Plan — fix the ECA forbidden zone (`skip_forbidden_harmonics_v1`)

> **Status: v1 IMPLEMENTED and FAILED (§8.4). v2 PROPOSED and REJECTED (§21). NOTHING PROMOTED.**
> **The live path is unchanged. Current plan of record: the STAGED PLAN in PART IV (§23).**
>
> v2's mechanism (cancel the `2k*f_r` line) collapses under a f_r error of one tenth of an FFT
> bin (§21) — and, measured on both Masimo runs, **a PERFECT f_r estimator would buy only
> 0.5–2.5 dB where ~20 dB is needed (§25): the high-k harmonics are not coherent lines at
> all.** So it was never an estimation problem.
>
> **Stage 0 is COMPLETE** (review debt; 567 tests green, 1 xfail = the known design hole).
> **Stage 1 is REWRITTEN**: 1A measures harmonic coherence with a metric that validates
> itself on a control; **1B tests temporal continuity — now the leading candidate**, because
> it needs no high-k precision at all. Both are read-only on data we already have.
>
> **Read order for a reviewer: §10 (what failed) → PART III (the amended design).**
> PART III is self-contained. §11 is superseded — my own repair was demoted by review
> comment §12.6, which found the actual root cause.
>
> - **Review 1** (on §1–§9): 8 findings, all verified, all accepted.
> - **Un-cleared** by the paced-16 capture → **§5.7** (`k_max = 6` does not span the cardiac band).
> - **Review 2** (on §5.7): 7 findings, all verified, all accepted. Plan cleared.
> - **Implemented** 2026-07-13. Code is complete and 562 tests pass — **but the §8.4 test, the
>   one this plan wrote to attack its own biggest risk (§7.1), FAILS.** The mode reports a
>   respiratory harmonic as the heart rate. The plan's own words: *"Confidently accepting the
>   respiratory harmonic is a hard failure of this plan."* It is therefore a hard failure.
>   See **§5.9**.
>
> `guard_cardiac_candidate_v1` is implemented and available, but is **NOT set in any config**
> (`live_demo_config.yaml` and `steps/step_6/config.yaml` still use the old, broken-but-safer
> `skip_forbidden_harmonics_v1`). Nothing in the live path has changed.
>
> **The code diff also still owes its own independent cross-model CODE review (§6) — the plan
> reviews do not discharge it.**
>
> Author: Claude. Reviewer: OpenAI (other model family). Date: 2026-07-13.
>
> Evidence runs:
> - `results/live_demo/20260713_172042_live_demo_massimo1` — natural breathing (18 bpm). No collision.
> - `results/live_demo/20260713_182002_live_demo_massimo2` — paced 16 bpm. **Collision NOT
>   achieved** (HR rose to 72 bpm under pacing, so 4·f_r = 64 missed by ~8 bpm) — but it exposed
>   §5.7 and §5.8.
> - **Still missing: a paced-18 bpm run** — the real collision fixture for this subject
>   (4 × 18 = 72 ≈ the paced HR). Until it exists, §8.4's synthetic test is the *only* collision
>   evidence. See §7.5.

---

## 1. The bug, in one sentence

`eca_mode: skip_forbidden_harmonics_v1` skips **every** respiratory harmonic whose k·f_r falls
inside the cardiac band, and at ordinary breathing rates that is *all* of them — so ECA removes
nothing from the band it exists to clean.

## 2. Evidence it is real (measured, not theorised)

From the 180 s Masimo-referenced run (50 hops, subject breathing 17–19 bpm, HR 64–68 bpm):

- **In-band power removed by ECA: 0.00 dB**, on every normal-breathing hop.
- `n_eca_skipped = 4` on every normal-breathing hop.
- Cardiac peak-to-floor ratio: **median −2.0 dB**, against a gate of **+2.0 dB** → 90% of hops
  rejected (`ratio_db_low` / `peak_to_floor_db_low`).
- The estimator is *not* broken: on the 5 hops that did pass, **MAE vs Masimo = 0.16 bpm**. The
  cardiac peak is found correctly; it simply cannot clear a gate whose floor is inflated by the
  uncancelled respiratory comb.

## 3. Mechanism (exact, with line references)

`src/vitals.py:331-342` computes the skip set:

```python
if eca_mode == "skip_forbidden_harmonics_v1":
    fz_lo = band[0] - eca_forbidden_guard_hz     # 0.8 - 0.0 = 0.8 Hz
    fz_hi = band[1] + eca_forbidden_guard_hz     # 2.0 + 0.0 = 2.0 Hz
    skip_ks_set = frozenset(
        k for k in range(1, k_max + 1) if fz_lo <= k * f_r_hz <= fz_hi
    )
```

The "forbidden zone" **is the entire cardiac band** [0.8, 2.0] Hz. With f_r ≈ 0.30 Hz
(18 bpm — an utterly ordinary resting rate):

| k | k·f_r | in [0.8, 2.0]? | skipped? |
|---|---|---|---|
| 1 | 0.30 Hz | no | cancelled |
| 2 | 0.60 Hz | no | cancelled |
| 3 | 0.90 Hz | **yes** | **skipped** |
| 4 | 1.20 Hz | **yes** | **skipped** |
| 5 | 1.50 Hz | **yes** | **skipped** |
| 6 | 1.80 Hz | **yes** | **skipped** |

Only k=1,2 are cancelled, and they lie *below* the cardiac band, where they were harmless. Every
harmonic actually inside the band survives. `skip_ks` is checked first at `vitals.py:140-141`, so
it also overrides the `k <= 4` hard floor.

**Both ECA modes are therefore broken, in opposite directions:**

| mode | behaviour | failure |
|---|---|---|
| `legacy` | hard floor always projects k=1..4 | erases the cardiac peak when 4·f_r ≈ HR (approach.md B.5) |
| `skip_forbidden_harmonics_v1` (live) | skips every in-band harmonic | cancels nothing in-band; floor stays high; AHET rejects |

## 4. Root cause

The forbidden zone was scoped to **the cardiac band** when its actual purpose is to protect
**the cardiac signal**. Those are not the same set. A harmonic at 0.9 Hz (54 bpm) is inside the
band but 11 bpm away from a 65 bpm heart rate — cancelling it is *desirable*, and skipping it
just leaves noise. Only a harmonic that lands *on* the cardiac peak must be spared.

## 5. Proposed fix — `eca_mode: guard_cardiac_candidate_v1`

Scope the forbidden zone to a **narrow guard around the cardiac candidate**, not the band.

### 5.1 Skip rule

```python
def cardiac_skip_ks(f_r_hz, cand_hz, k_max, guard_hz):
    """Harmonics to spare: those landing within guard_hz of the cardiac candidate."""
    if cand_hz is None:
        return frozenset()
    return frozenset(
        k for k in range(1, k_max + 1)
        if abs(k * f_r_hz - cand_hz) <= guard_hz
    )
```

### 5.2 Ordering — the skip set must be computed AFTER the candidate *(review finding 3)*

In the current code the forbidden-zone skip set is built at `vitals.py:331-342`, **before**
`prov_cand_hz` is computed at `vitals.py:349-351`. The new mode depends on that candidate, so the
ordering must be **deliberately restructured**, not patched around: compute `prov_cand_hz` first,
then derive the skip set from it.

### 5.3 Per-pass skip sets *(review finding 4)*

`eca_project()` is called twice — first pass at `vitals.py:353` and second pass, per AHET
candidate, at `vitals.py:474`. The second pass already varies `cardiac_candidate_hz=cand_hz`, but
it **reuses the first-pass `skip_ks_set`**. Under the new mode that is incoherent: a rank-2
candidate would be evaluated with a guard positioned on the *provisional* peak.

Therefore:
- **First pass:** `skip_ks = cardiac_skip_ks(f_r, prov_cand_hz, ...)`
- **Second pass (per candidate):** `skip_ks = cardiac_skip_ks(f_r, cand_hz, ...)` — recomputed for
  the candidate actually being tested.

This directly mitigates risk §7.1: even if `prov_cand_hz` is a respiratory harmonic, each AHET
candidate is re-evaluated with a guard centred on *itself*.

### 5.4 One guard width, passed explicitly *(review finding 2)*

`eca_project()` currently hardcodes a **second, different** guard — `abs(freq -
cardiac_candidate_hz) > 0.15` at `vitals.py:145`, applying only to k ≥ 5. Leaving that in place
while adding a 0.10 Hz skip rule would mean two competing guard widths, and `n_eca_skipped` would
no longer describe which harmonics were actually protected.

**Make the guard width an explicit parameter of `eca_project()`** (`cardiac_guard_hz`), replace
the hardcoded `0.15`, and have all protection flow through the one rule.

### 5.5 Guard width — and a resolution hazard *(review finding 6, extended)*

Proposed default **`eca_cardiac_guard_hz: 0.10`** (6 bpm). But this default is **dangerously close
to the boundary in exactly our operating regime**, and the review's concern is worse than stated.
For HR = 65 bpm (1.0833 Hz), k=4:

| f_r | 4·f_r | \|4·f_r − HR\| | decision (guard 0.10) |
|---|---|---|---|
| 0.290 Hz | 1.160 Hz | 0.0767 Hz | **skipped** |
| 0.295 Hz | 1.180 Hz | 0.0967 Hz | **skipped** |
| **0.300 Hz** | 1.200 Hz | **0.1167 Hz** | **cancelled** |
| 0.310 Hz | 1.240 Hz | 0.1567 Hz | cancelled |

**The decision flips between f_r = 0.295 and 0.300 Hz — a 0.005 Hz window — while the FFT bin
width at a 30 s window is 0.0333 Hz.** The skip decision therefore flips within *one sixth of a
frequency bin*. In the evidence run f_r wandered over 0.27–0.33 Hz hop to hop, so k=4's fate would
**chatter between skipped and cancelled across adjacent hops**, making the post-ECA spectrum
non-stationary for no physical reason.

Consequences for the design:
- A guard narrower than ~2 bins (**0.067 Hz at 30 s**) is close to meaningless, because
  `prov_cand_hz` is itself quantised to the bin grid (parabolic refinement notwithstanding).
- The guard should be **sanity-checked against the FFT resolution at run time**, not just set in Hz.
- The verification sweep (§8.6) **must report skip-decision stability** — the fraction of adjacent
  hop pairs whose skip set changes — not only yield and MAE. A configuration that wins on yield but
  chatters is not acceptable.

The committed default stays a simple scalar in Hz; the sweep decides its value.

### 5.6 Worked effect

At f_r = 0.30 Hz, prov_cand = 1.08 Hz (65 bpm): |k·f_r − 1.08| = 0.18 / 0.12 / 0.42 / 0.72 Hz for
k = 3/4/5/6 → **nothing is skipped; all four in-band harmonics are cancelled.** The floor drops.

At f_r = 0.271 Hz (16.25 bpm, 4·f_r = 1.083 Hz ≈ HR): |4·f_r − prov_cand| ≈ 0.003 Hz ≤ guard →
**k=4 alone is skipped**, exactly as intended, and k=3,5,6 are still cancelled.

This is a strict generalisation: it preserves the original intent (don't erase the cardiac peak)
while restoring ECA's function everywhere else.

### 5.7 BLOCKING PREREQUISITE — `k_max = 6` does not span the cardiac band

*Discovered on the paced-16 bpm capture `20260713_182002_live_demo_massimo2` (2026-07-13),
**after** the cross-model review. This was not known to either model at review time.*

`k_max = 6` is **too low to cover the cardiac band at ordinary breathing rates.** The number of
respiratory harmonics that land inside [0.8, 2.0] Hz grows as f_r *falls*:

| BR | f_r | highest k with k·f_r ≤ 2.0 Hz | k_max=6 sufficient? |
|---|---|---|---|
| 12 bpm | 0.200 Hz | **k = 9** | **NO** |
| 14 bpm | 0.233 Hz | **k = 8** | **NO** |
| 16 bpm | 0.267 Hz | **k = 7** | **NO** |
| 18 bpm | 0.300 Hz | k = 6 | yes (barely) |
| 20 bpm | 0.333 Hz | k = 5 | yes |

**Measured consequence.** At the paced 16 bpm (f_r = 0.2673 Hz), the **7th harmonic sits at
112.3 bpm — inside the cardiac band, but beyond `k_max`, so ECA can never touch it.** On
**15 of 44 hops (34%) the top candidate was 94–114 bpm**, with |cand0 − 7·f_r| median **1.8 bpm**,
while the true HR was 71.6 bpm. The estimator was picking the 7th respiratory harmonic as the
heartbeat.

**Why this blocks the fix — the fix makes it worse.** Today `skip_forbidden_harmonics_v1` skips
k=3..6 anyway, so ECA cancels nothing in-band and k=7 is merely one peak among many. Once
`guard_cardiac_candidate_v1` starts cancelling k=3..6 as intended, **k=7 becomes the only
surviving in-band respiratory harmonic** — and therefore the strongest competitor to the cardiac
peak. Shipping §5.1–§5.6 alone would trade one failure for another.

**Required change — derive `k_max`, but bounded** *(second review, findings 1 & 3)*:

```python
k_max_eff = min(k_max_cap, int(np.floor(band_hi / f_r_hz)))
```

- **`k_max_cap` is a config key** (proposed default 10) and is **swept** (§8.6). An *unbounded*
  derivation is unsafe: the physiological gate floor is `_GATE_LO_HZ = 0.15` (`vitals.py:238`), so
  `floor(2.0 / 0.15) = 13`, and any future gate/config drift pushes it higher. A bad f_r estimate
  must not be able to silently expand the ECA subspace.

- **Band-ceiling off-by-one — resolve it explicitly.** The loop currently breaks on
  `if freq >= 2.0` (`vitals.py:138`) — a **hardcoded, exclusive** ceiling — while `cardiac_mask`
  uses `freqs <= band[1]` (`vitals.py:327`) — **inclusive**. These disagree exactly at the edge:
  at f_r = 0.2 Hz, `10 × 0.2 = 2.0`, so the formula admits k=10 but the loop drops it (and the
  §5.7 table above says k=9, matching the *loop*, not the formula). **Decision: make ECA
  consistent with the spectrum masks — include the exact ceiling** (`if freq > band_hi + eps:
  break`) and replace the literal `2.0` with `band[1]`. Anything else must be documented as a
  deliberate exclusion, not left as an accident.

**New risk introduced — over-projection** *(second review, finding 4)*: more harmonics ⇒ more
subspace columns (2 per harmonic) stripped from an N=600 window. At f_r = 0.20 Hz with a cap of
10 → 20 columns. Small against N=600, but the real hazard is **not** column count: it is that
projecting many harmonics at a **slightly wrong f_r** attenuates the *true cardiac peak*. That is
the `legacy` failure in another guise.

Therefore verification must **report cardiac retention, not just floor suppression** (§8.7):
power at the Masimo-referenced cardiac bin **before vs after** ECA, **under deliberate f_r error**
(± one FFT bin, ≈ ±0.033 Hz at 30 s, and ±0.01–0.02 Hz). **A mode that suppresses the floor but
erases cardiac power must fail the sweep.**

### 5.8 SECOND CEILING — the top-3 candidate list *(second review, finding 5; now measured)*

`AHET_MAX_CANDIDATES = 3` (`vitals.py:26`). At low breathing rates several in-band respiratory
harmonics can outrank the heart *before* ECA, pushing the true cardiac peak out of the list —
where AHET can never reach it, no matter how the gate or ECA is tuned.

**Measured** (true HR = within 3 bpm of Masimo PR):

| run | true HR at rank 0 | rank 1 | rank 2 | **not in top-3** | max achievable yield |
|---|---|---|---|---|---|
| natural (18 bpm) | 23/37 | 6/37 | 1/37 | **7/37 (19%)** | **81%** |
| paced-16 (low BR) | 23/44 | 8/44 | 1/44 | **12/44 (27%)** | **73%** |

So there is a **hard ceiling on yield of ~73–81%**, independent of ECA and independent of the
gate — and, as the reviewer predicted, **it is worse at low breathing rates**. This bounds §6's
prediction: >50% is achievable, but 100% is not, and chasing the last 20% by loosening the gate
would be chasing a candidate that is not in the list.

**Action:** after the ECA fix, **re-measure this table.** If cancelling k=3..6 promotes the true
cardiac peak up the ranking, the ceiling should rise on its own. If it does not,
`AHET_MAX_CANDIDATES` must be swept alongside `k_max` (§8.6). Do **not** raise it pre-emptively —
more candidates means more chances to accept a wrong one.

### 5.9 THE DESIGN IS HOLED — §7.1 materialised

`guard_cardiac_candidate_v1` was implemented as specified above and **fails the acceptance test
this plan wrote for itself (§8.4): it reports a respiratory harmonic as the heart rate, and is a
regression against BOTH modes it was meant to replace.** It is implemented but **NOT promoted** —
no config uses it.

**Full post-mortem, evidence and recommendations: §10 and §11 below.** §5.3's claim that
per-candidate re-guarding "directly mitigates risk §7.1" is **wrong** and is retracted.

## 6. Falsifiable prediction

Re-running the stored run `20260713_172042_live_demo_massimo1` offline with the new mode should
show, **without changing any AHET gate**:

1. In-band power removed by ECA: **0.00 dB → materially negative** (predict −3 to −10 dB).
2. Median cardiac peak-to-floor: **−2.0 dB → above the existing +2.0 dB gate** on a majority of
   normal-breathing hops.
3. HR yield: **10% → substantially higher** (predict >50%).
4. **MAE on the newly-admitted hops stays low** (predict < 3 bpm vs Masimo). This is the critical
   one: if yield rises but MAE degrades, the fix is admitting noise, not signal, and must be
   rejected.
5. The **skipped-harmonic set** (not just the count) should be **empty on most hops**, and
   **{4}** on hops where 4·f_r genuinely collides with the HR. *(Requires finding 8 — see §8.5.)*
6. **Skip-decision stability:** the skip set should be stable across adjacent hops. Frequent
   flipping indicates the guard width is fighting the frequency resolution (§5.5).

7. **Cardiac retention holds.** Power at the Masimo-referenced cardiac bin must **not** fall
   materially after ECA, including under ±1 FFT bin of f_r error (§8.7).
8. **The §5.8 candidate-rank ceiling should lift**, since cancelling k=3..6 should promote the
   true cardiac peak up the ranking. Re-measure; do not assume.

If (1)–(3) improve but (4) degrades, **the hypothesis is wrong** and the plan must be withdrawn.
If (1)–(4) hold but **(7) fails**, the mode is over-projecting and must also be rejected — a good
yield bought by erasing cardiac power is the `legacy` failure in disguise.

## 7. Risks and open questions

1. **`prov_cand_hz` is derived from the contaminated pre-ECA spectrum.** If the provisional
   candidate is itself a respiratory harmonic (not the heart), the first-pass guard would protect
   the *harmonic* and cancel the *heart*. **Partially mitigated by §5.3** (each AHET candidate is
   re-evaluated in the second pass with a guard centred on itself), but the first-pass spectrum
   still shapes which candidates are found at all. **Still the biggest risk. It gets a dedicated
   synthetic test — §8.4.**
2. **Guard width vs frequency resolution.** See §5.5. Settled by the sweep (§8.6), which must
   report skip-decision stability alongside yield/MAE.
3. **Should the `k <= 4` hard floor at `vitals.py:142` survive?** It covers a "known 60 bpm
   failure" predating the forbidden-zone machinery. **Decision: leave it in place for
   `legacy` only.** Under `guard_cardiac_candidate_v1` the guard is the sole protection mechanism
   and the floor is redundant (it only ever *forced* cancellation, which is now the default).
   Revisit only if the sweep shows otherwise.
4. **Interaction with the AHET peak-to-floor gate.** A ground-truthed sweep shows the gate is set
   very conservatively (yield 20% at 2.0 dB; 74% at no gate, MAE still 2.13 bpm, only 2 estimates
   >5 bpm off in 37). **Deliberate decision: do NOT touch the gate in this change.** Fix ECA
   first, re-measure, then reconsider — otherwise two coupled changes land at once and neither can
   be attributed.
5. **Sample size, and there is still NO real collision fixture** *(second review, finding 6)*.
   All evidence is one subject and two 3-minute runs. The mechanism arguments (§3, §5.7) are
   deterministic and sample-size independent; the *predicted improvement* (§6) is not.

   **The paced-16 bpm capture was taken and MISSED the collision.** Pacing was executed perfectly
   (Masimo `Breaths / min` = 16.0 on every sample), but the subject's HR rose to **72 bpm** under
   paced breathing (vs ~65 natural), so 4·f_r = 64 bpm sat ~8 bpm below the heart:
   |HR − 4·f_r| median 8.2 bpm, inside the ≤5 bpm destructive zone on only **4/49 hops**.

   **Do not re-target 16 bpm.** For this subject the colliding pace is **18 bpm** (4 × 18 = 72 ≈
   the paced HR) — which is the study's existing 18 bpm arm, now confirmed as a real collision
   case rather than a theoretical one. **Lesson: compute the |HR − 4·f_r| margin from the HR
   observed *during* pacing, not from a prior resting measurement — paced breathing below the
   natural rate raises HR.**

   Until a paced-18 run exists, **§8.4's synthetic test is the only collision evidence.**

## 8. Implementation & verification steps

1. **`src/vitals.py`** — add `eca_mode: guard_cardiac_candidate_v1` **alongside** the two existing
   modes (do not delete `legacy` or `skip_forbidden_harmonics_v1`; they are the comparison arms).
   Restructure the ordering per §5.2, add per-pass skip sets per §5.3, add an explicit
   `cardiac_guard_hz` parameter to `eca_project()` replacing the hardcoded `0.15` per §5.4, and
   implement the bounded `k_max_eff` + band-ceiling fix per §5.7.

1b. **Keep the diagnostic artifact shape FIXED even though `k_max_eff` now varies by hop**
   *(second review, finding 2)*. `eca_skipped_harmonics` is currently shaped by the configured
   `k_max` (`vitals.py:332`) and Step 6 **stacks it into an NPZ**
   (`extract_heart_rate.py:690-691`). A variable-length return would either break that stack or
   silently hide k > 6. Therefore:
   - return a **fixed `(k_max_cap,)` bool vector**, always;
   - add **scalar** fields `k_max_eff` and `n_eca_projected` per hop, so diagnostics can tell how
     many harmonics were *considered* versus *skipped*.

   Without this, §6.5 and §5.7 cannot even be evaluated — the artifact would not record what the
   algorithm did.

2. **Config + threading** *(review finding 1)*. Add `eca_cardiac_guard_hz` (default 0.10) **and
   `k_max_cap` (default 10)** to `scripts/live_demo_config.yaml` and `steps/step_6/config.yaml` —
   no magic numbers in code (CLAUDE.md §2). It is **not enough to add the YAML keys**: the values
   must be forwarded through both call sites, which read config and pass it on explicitly:
   - `scripts/live_demo.py:488-492`
   - `steps/step_6/extract_heart_rate.py:857-859, 1085-1087`

   Add **forwarding tests** asserting the new key actually reaches `estimate_rate_from_phase()`,
   mirroring the existing pattern at `tests/test_step6_heart_rate.py:1981-2001` (which does exactly
   this for `candidate_min_peak_to_floor_db` / `low_candidate_min_peak_to_floor_db`).

3. **Synthetic unit tests** in `tests/test_eca_ahet.py` (canonical pattern, CLAUDE.md §5.3):
   - non-colliding harmonic in band → **is** cancelled (the regression this bug caused);
   - harmonic colliding with the cardiac tone → **is not** cancelled, cardiac tone survives;
   - skipped-harmonic set is `{}` and `{4}` respectively.

4. **Synthetic test for the §7.1 risk** *(review finding 5)* — the important one. Construct a case
   where **the dominant pre-ECA peak IS a respiratory harmonic** and the true cardiac peak is
   weaker but present. Required behaviour: strict AHET rejects the protected respiratory candidate
   and still accepts the cardiac candidate — **or, at minimum, returns NaN.** Confidently accepting
   the respiratory harmonic is a hard failure of this plan.

5. **Live artifact completeness** *(review finding 8)*. Offline Step 6 already writes the full
   `eca_skipped_harmonics` bool vector to NPZ (`extract_heart_rate.py:690`) plus an
   `eca_skipped_harmonic_ks` string. The **live path throws this away**: `live_demo.py:543`
   collapses it immediately to `int(np.sum(eca_skip))`. Prediction §6.5 depends on knowing *which*
   k was skipped, not how many. **Store the full vector in `live_intermediates.npz`.**

6. **Offline verification harness** *(review finding 7)*. `experiments/` is currently **absent, not
   merely empty**, and `steps/step_6/config.yaml` uses **`window_s: 20`, `hop_s: 5`** while the
   live/evidence path uses **30 s / 3 s** — scoring the modes under the default Step-6 config would
   silently compare at the wrong window length, and contradicts the standing "window is 30 s; do
   not shorten it" decision (HANDOFF §5). Therefore: **create a dedicated
   `experiments/exp_eca_modes/` with `config.yaml` + `run.py` pinned to 30 s / 3 s**, re-processing
   the raw `adc_stream.bin` (never `live_estimates.csv` — CLAUDE.md §4) and scoring all three ECA
   modes against Masimo PR on the §6 metrics.

   **Swept axes:** `eca_cardiac_guard_hz` (§5.5), **`k_max_cap`** (§5.7), and — only if §5.8's
   ceiling does not lift on its own — `AHET_MAX_CANDIDATES`.

   **Reported metrics (all of them, together):**
   - yield **and** MAE — yield alone is meaningless (§7.4);
   - **skip-decision stability** — fraction of adjacent hop pairs whose skip set changes (§5.5);
   - **cardiac retention** — see §8.7;
   - **the §5.8 candidate-rank table**, re-measured.

   **Input provenance** *(second review, finding 7)*. `results/live_demo/.../adc_stream.bin` is a
   **live raw mirror**, and CLAUDE.md §4 says it only becomes canonical input via a *deliberate
   promotion step*. This experiment **references it in place as a regression fixture — it is NOT
   promoted to `data/raw/`.** The experiment **must log the SHA-256 of every raw file it reads**
   (CLAUDE.md §3 rule 1: every number traces to the input data file's hash). Do not let the
   offline experiment quietly depend on an untracked live artifact.

   *Side benefit:* creating `experiments/` also revives `diag_eca_overnotch.py`,
   `diag_heart_spectrum.py` and `diag_leakage.py`, which auto-discover the latest run under
   `results/<experiment>/` and are currently dead only for want of an experiment.

7. **Cardiac-retention check — the over-projection guard** *(second review, finding 4)*. The §5.7
   risk is **not** merely "20 columns vs N=600". It is that projecting many harmonics at a
   *slightly wrong* f_r attenuates the true cardiac peak. Add a metric (and a synthetic test):
   **power at the Masimo-referenced cardiac bin, before vs after ECA**, evaluated under
   **deliberate f_r error** — ±1 FFT bin (≈0.033 Hz at 30 s) and ±0.01–0.02 Hz.

   **A mode that suppresses the noise floor but erases cardiac power must FAIL the sweep**, even
   if its yield looks good. This is the `legacy` failure wearing a new coat, and it is exactly the
   kind of result that would look like success in a yield-only report.

8. **Promotion.** Only if §6 criteria (1)–(4) hold **and** the §8.7 retention check passes:
   promote `guard_cardiac_candidate_v1` to the live config. Then, and only then, revisit the AHET
   gate (§7.4).

## 9. Not in scope

- The AHET gate thresholds (§7.4) — deliberately deferred until ECA is fixed and re-measured.
- The `resp_valid` false-confidence bug (f_r pinned to the 0.1 Hz band floor at t=144 s of the
  evidence run while `resp_valid` stayed 1). Real, logged in HISTORY.md, but independent.
- The `legacy` mode's `k <= 4` hard floor, which stays as-is for `legacy` (§7.3).

---
---

# PART II — POST-MORTEM AND RECOMMENDATIONS

> Written 2026-07-13, after implementing §8. **This part has NOT been reviewed.**
> Everything above (§1–§9) was reviewed twice and accepted; it is the implementation of that
> reviewed plan that failed. Reviewer: please attack §11 hardest, especially §11.1.

---

## 10. Post-mortem — what was built, and exactly how it failed

### 10.1 What was built (all of it, and it works as specified)

Every item of the reviewed §8 was implemented. **562 tests pass.** The mechanical findings from
both reviews were all real and are all fixed:

| item | status |
|---|---|
| §5.2 skip set derived *after* `prov_cand_hz` | done |
| §5.3 per-pass skip sets (2nd pass re-guards on its own candidate) | done |
| §5.4 `cardiac_guard_hz` / `band_hi` / `hard_floor_k` explicit; hardcoded `0.15`/`2.0`/`4` gone | done |
| §5.7 bounded `k_max_eff = min(k_max_cap, floor(band_hi/f_r))`, inclusive ceiling | done |
| §8.1b fixed `(k_max_cap,)` artifact + scalar `k_max_eff` / `n_eca_projected` | done |
| §8.2 config keys threaded through **both** call sites + forwarding test | done |
| §8.5 live NPZ stores **which** harmonics were spared | done |

One reviewer prediction was confirmed the hard way: Step 6's `_eca_skip()` **truncated to `k_max`**,
so a length-10 vector would have silently hidden k > 6. Fixed.

**None of that saved the design.**

### 10.2 The failure

Synthetic, 30 s window, real AHET gate values from `live_demo_config.yaml`:
f_r = 0.30 Hz, so 4·f_r = **72 bpm** is the strongest in-band peak. True heart at **96 bpm**,
weaker, with a proper cardiac 2nd harmonic.

| eca_mode | verified | reported | verdict |
|---|---|---|---|
| `legacy` | True | **95.99 bpm** | **correct — finds the heart** |
| `skip_forbidden_harmonics_v1` | False | NaN | safe (reports nothing) |
| **`guard_cardiac_candidate_v1`** (new) | True | **71.43 bpm** | **REPORTS THE DECOY** |

**The new mode is worse than both modes it was designed to replace.** `legacy` gets it right; the
old broken mode at least stays silent; the new one is confidently wrong.

### 10.3 Mechanism, precisely

1. `prov_cand_hz` is the **argmax of the contaminated pre-ECA spectrum** (§5.2 — reviewed and
   accepted by both models). When a respiratory harmonic outranks the heart, **the harmonic *is*
   the provisional candidate.**
2. The guard therefore **spares the decoy** (`skip_ks = {4}`).
3. The decoy survives first-pass ECA **at full strength** and becomes **rank 0**.
4. `ahet_gate_mode: strict_v1` returns the **first passing candidate in magnitude order**.
5. The decoy passes. The true heart — **sitting at rank 1 with peak-to-floor 33 dB** — is never
   examined.

```
  rank 0:  72.00 bpm   p2f =  4.40 dB  -> PASSED     <-- DECOY (= 4·f_r). RETURNED.
  rank 1:  95.98 bpm   p2f = 33.04 dB  -> PASSED     <-- TRUE HR. NEVER REACHED.
  rank 2: 113.02 bpm   p2f =  2.07 dB  -> prominence_low
```

### 10.4 Why the reviewed mitigation does not work

§5.3 (per-candidate re-guarding in the second pass) was accepted by both models as *"directly
mitigating risk §7.1"*. **It does not.** It fixes how a candidate is *evaluated*; the failure is in
how candidates are *ordered*. The decoy is evaluated first, passes, and rank 1 is never reached.
**§5.3's mitigation claim is retracted.**

### 10.5 Why `legacy` survives this — and what that tells us

`legacy` cancels k ≤ 4 **unconditionally**. That kills the decoy, so the heart becomes rank 0. The
hard floor we dismissed as "redundant" (§7.3) was the only thing standing between us and this
failure. Its blunt behaviour is *right* in the common case and *wrong* only in the rare exact-
collision case (approach.md B.5) — **and we optimised for the rare case at the expense of the
common one.**

### 10.6 This is not a corner case

On the paced-16 capture, **a respiratory harmonic was the top candidate on 34% of hops** (§5.7).
**A harmonic outranking the heart in the pre-ECA spectrum is the normal condition, not the
exception.** The synthetic above is a faithful model of the real data, not a contrived edge case.

### 10.7 The root design error

**The forbidden zone was scoped to protect "the cardiac candidate" — but we never established that
the candidate *is* cardiac.** We built a mechanism that protects whatever the strongest in-band
peak happens to be, in a spectrum whose entire defect is that the strongest in-band peak is
usually a respiratory harmonic. The guard is a loaded gun pointed at whichever peak wins a contest
it was supposed to referee.

### 10.8 Process observations (honest)

- The plan's own §8.4 acceptance test is the **only** reason this was caught. Two cross-model
  reviews and 15 accepted findings did not see it. **Writing a test designed to fail the plan was
  worth more than both reviews combined.**
- My first version of that test was **invalid**: the synthetic cardiac tone had no 2nd harmonic,
  which AHET requires by construction, so it could never verify the heart. Had I not checked the
  repo's own signal convention (`_make_harmonic_signal`), I would have "fixed" a phantom bug.
- While probing repairs, one of my scripts printed a **pre-written conclusion that the data then
  contradicted** (it asserted the collision residual would be rejected; it is in fact accepted, at
  +20.8 dB). Conclusions must be read off the numbers, not written before them.

---

## 11. Recommendations

> **⚠ SUPERSEDED — do not implement §11.1.** Review comment **§12.6** identified the actual root
> cause (ECA never cleans the 2.0–4.0 Hz region where AHET looks for the cardiac 2nd harmonic), and
> my "pilot" repair below was patching a symptom. **The live proposal is PART III (§13–§19).**
> §11.1 survives only in the reduced role of an *ambiguity detector* (§15.4, adopting §12.8).
> §11.2 was right about the physics but had its dependency backwards — it only works *after* §12.6.
> Kept unedited for the review record.

Three repair options were **prototyped against the real code** (read-only; `src/vitals.py`
untouched). Scenarios: **NORMAL** (heart 65 bpm, no collision), **DECOY** (heart 96 bpm, 4·f_r = 72
dominates), **COLLISION** (heart sits exactly on 4·f_r).

**Question asked: is the true HR reachable as a candidate, and does a decoy outrank it?**

| scenario | CURRENT (broken) | **OPT-A: pilot** | OPT-B: union of both spectra |
|---|---|---|---|
| NORMAL | rank 0 | **rank 0** | rank 0 |
| DECOY | **rank 1 — decoy at rank 0** | **rank 0** | rank 0 |
| COLLISION | rank 0 | **rank 0** | rank 0 |

OPT-B (carry candidates from both the pilot and the guarded spectrum) is strictly more machinery
than OPT-A and, on these scenarios, buys nothing OPT-A does not already achieve. **Recommend
OPT-A**; keep OPT-B in reserve if OPT-A fails real-data validation.

### 11.1 RECOMMENDED — Option A: derive the provisional candidate from a *pilot* pass

Replace `prov_cand_hz = argmax(pre-ECA spectrum)` with:

```python
# Pilot pass: cancel EVERY harmonic up to the band ceiling, with no guard at all.
x_pilot      = eca_project(x_bp, f_r, fs, k_max=k_max_eff, skip_ks=frozenset(),
                           cardiac_candidate_hz=None, hard_floor_k=0, band_hi=band[1])
prov_cand_hz = argmax(spec(x_pilot) in cardiac zone)   # decoys are GONE from this spectrum
```

Then proceed exactly as §5.1–§5.6 already specify (guard the *pilot's* candidate; per-pass skip
sets; bounded `k_max_eff`). This is a **small, local change to one line of §5.2** — all the rest of
the implemented and tested machinery is unchanged.

**Why it works:** the pilot spectrum contains *no* respiratory harmonics by construction, so the
strongest in-band peak can no longer be a decoy. Measured on the DECOY scenario, `prov_cand` moves
from **72.0 bpm (the harmonic)** to **96.0 bpm (the true heart)**, and the guard then correctly
spares **nothing** (cardiac power retained: **−0.0 dB**).

**The collision case also survives — but understand *why*, because it is subtle and I do not fully
trust it.** In COLLISION the pilot pass *does* destroy the cardiac peak: **−29.8 dB** at the true
HR bin. Yet the surviving candidate is still usable — **peak-to-floor +20.8 dB** against a +2.0 dB
gate — and lands at 66.0 bpm against a true 65.04 bpm. The reason: **ECA lowers the noise floor as
well as the peak**, so the residual stands proud of a floor that fell with it. The candidate
*location* survives (biased ~1 bpm), the guard then fires on the correct k = 4, and the real first
pass spares it, restoring the peak.

**This is the load-bearing claim and the thing to attack.** That −29.8 dB residual is a notch
shoulder. It behaved well in one synthetic realisation. I do not trust it, for two reasons:
- a **~1 bpm location bias at the fundamental is within one FFT bin** (2.0 bpm at 30 s) and could
  easily land on the wrong side of a boundary and select the **wrong k**;
- in real data the heart is **not** a pure sinusoid pinned exactly at k·f_r, so the residual's
  behaviour may differ in either direction.

**It must be validated on the paced-18 real collision fixture — which does not yet exist.**

### 11.2 Additional discriminant — the cardiac 2nd harmonic (the separation DOUBLES)

If HR = k·f_r + δ, then 2·HR = 2k·f_r + **2δ**. The nearest respiratory harmonic to the *cardiac
2nd harmonic* is 2k·f_r, so **the separation between heart and harmonic doubles at 2·HR.**

| HR offset δ | separation at HR | separation at 2·HR |
|---|---|---|
| 1 bpm | 1.0 bpm (**< 1 bin — unresolvable**) | 2.0 bpm (1 bin) |
| 2 bpm | 2.0 bpm (1 bin) | **4.0 bpm (2 bins — resolvable)** |
| 3 bpm | 3.0 bpm | **6.0 bpm** |
| 5 bpm | 5.0 bpm | **10.0 bpm** |

FFT resolution at a 30 s window = 2.0 bpm. **A near-collision that is unresolvable at the
fundamental becomes resolvable at the 2nd harmonic.** AHET already searches that region — the
information is being computed and then thrown away.

**Recommendation:** when a candidate is *coincident* with some k·f_r (within the guard), do not
accept it on fundamental evidence alone. Require the 2nd-harmonic check to discriminate at 2·HR,
where the offset is doubled and the competing respiratory harmonic (2k·f_r) is weaker.

**Honest limit:** at **exact** coincidence (δ = 0), 2·HR = 2k·f_r as well — the 2nd harmonic
collides too, and the problem is **fundamentally unidentifiable by frequency**. No DSP recovers it.
The correct output there is **NaN with an explicit `coincidence_ambiguous` reason code**, not a
guess. The 2nd harmonic buys the *near*-collision band — which is where essentially all real cases
live, since δ = 0 exactly is measure-zero.

### 11.3 Do not return the first passing candidate blindly

Independent of the above: `strict_v1` returns the first candidate that passes, **in magnitude
order**. In §10.3 the true heart passed with **33 dB** peak-to-floor and was discarded in favour of
a decoy that passed with **4.4 dB**. **Magnitude order is not credibility order.** At minimum: when
several candidates pass, prefer the one with the stronger evidence; and treat a candidate that
coincides with k·f_r as *suspect*, not as *first in line*.

### 11.4 Reconsider deleting the `legacy` hard floor

§7.3 decided the `k ≤ 4` hard floor was "redundant" under the new guard. §10.5 shows it was the
**only** protection against the decoy case, and that it is right in the common case. **Do not
remove it without a replacement that demonstrably handles §10.2.** Option A is intended to be that
replacement — but only if it survives review *and* real-data validation.

### 11.5 What I did NOT do, deliberately

- **Did not promote the new mode.** Both configs still use `skip_forbidden_harmonics_v1`; the live
  path is unchanged.
- **Did not implement Option A.** It is a design change; per CLAUDE.md §6 it needs review before
  code. I prototyped it read-only and reported the numbers, nothing more.
- **Did not delete the failing test.** §8.4 is kept and marked `xfail(strict=True)` with the full
  mechanism in its reason, so it flips to XPASS the moment this is genuinely fixed (CLAUDE.md §4 —
  negative results are recorded, not deleted).

### 11.6 Proposed order of work, if the reviewer agrees

1. Implement **Option A** (§11.1) — a one-line change to where `prov_cand_hz` comes from.
2. Add the **coincidence-ambiguous** path (§11.2): coincident candidates require 2nd-harmonic
   discrimination; exact coincidence returns NaN with an explicit reason code.
3. Fix **candidate ordering** (§11.3) so credibility, not magnitude, decides.
4. Re-run §8.4. **It must XPASS.** If it does not, Option A is dead too.
5. Only then run the §8.6 offline verification across all three modes; only then consider promotion.
6. **Capture the paced-18 fixture** and validate the collision case on real data before trusting
   §11.1's collision behaviour — which, to be explicit, §11.1 itself does not trust.

### 11.7 Questions I want the reviewer to answer

1. **Is the pilot-residual argument in §11.1 sound, or am I relying on an artifact?** A −29.8 dB
   notch shoulder surviving at +20.8 dB peak-to-floor with a ~1 bpm bias is the load-bearing claim,
   and I do not trust it.
2. **Is there a better provisional-candidate estimator than the pilot argmax?** For example: choose
   the candidate that maximises cardiac-2nd-harmonic consistency directly, skipping the
   fundamental-magnitude beauty contest entirely.
3. **Should the exact-collision case return NaN, or report with a flag?** I argue NaN + reason code.
   Reporting a number we cannot distinguish from a respiratory harmonic seems indefensible in a
   paper (CLAUDE.md §4).
4. **Is `AHET_MAX_CANDIDATES = 3` now the binding constraint?** §5.8 measured a hard yield ceiling
   of 73–81% from the candidate list alone. If the pilot spectrum promotes the heart up the
   ranking, does that ceiling lift by itself — or does the list need to grow?
5. **Is there a case for keeping `legacy` as the shipping default** until this is settled? It is
   blunt and it fails the B.5 collision case, but it is the only mode that gets §10.2 right, and
   §10.6 says §10.2 is the common case while B.5 is the rare one.

---

## 12. OpenAI code/post-mortem review comments — 2026-07-13

I reviewed the implemented diff and the failure notes. The broad post-mortem is right: the
implementation faithfully built the reviewed candidate-guard design, and the xfailed §8.4 test is
evidence of a real design failure, not just a coding typo. I would add the following review points
before the next design pass.

1. **Offline Step 6 does not appear to persist `k_max_eff` / `n_eca_projected`.** `src/vitals.py`
   returns both scalars and `scripts/live_demo.py` stores them in live intermediates, but
   `steps/step_6/extract_heart_rate.py` still only stacks `eca_skipped_harmonics`; I do not see
   `k_max_eff` or `n_eca_projected` added to `heart_intermediates.npz` or `heart_windows.csv`.
   That undercuts §8.1b's requirement that diagnostics tell how many harmonics were considered
   versus projected in the offline experiment, which is the path that will score the modes. Add
   those scalars to the Step 6 artifact contract and tests before relying on offline sweeps.

2. **The Step 6 fake estimator still models the old skip-vector shape.** `_fake_estimate()` in
   `tests/test_step6_heart_rate.py` accepts `k_max_cap`, but returns
   `eca_skipped_harmonics=np.zeros(k_max)`, and its shape test still asserts `(k_max,)`. The real
   estimator now returns a fixed `max(k_max, k_max_cap)` vector. This means the Step 6 integration
   tests do not actually exercise the new cap-sized artifact path unless a real estimator call is
   used. Either update the fake to return the production shape plus `k_max_eff` /
   `n_eca_projected`, or add a dedicated Step 6 artifact test with a fake length > `k_max`.

3. **The current xfail can XPASS for the wrong reason.** The §8.4 test says the acceptable outcomes
   are "true HR" or NaN, but the assertion only rejects estimates within 5 bpm of the decoy. A
   future change that confidently reports a different wrong HR, e.g. 85 or 110 bpm, would XPASS.
   Tighten the test so, when `ahet_verified=True`, the estimate must be close to `f_h * 60`; NaN
   remains acceptable.

4. **Candidate evidence is per-candidate, but ECA projection metadata is only first-pass/global.**
   With per-candidate second-pass ECA, each candidate can have a different skip/projected set.
   Current artifacts report the first-pass skip set and `n_eca_projected`, but not the skip set
   used for each `ahet_attempt_spectrum`. Since the failure is specifically about rank-0 versus
   rank-1 candidate evaluation, future diagnostics should store candidate-wise skipped/projected
   sets, or at least the accepted candidate's set, so reviewers can reconstruct what spectrum each
   candidate was judged on.

5. **Be careful comparing peak-to-floor values across candidates.** §11.3 is directionally right:
   "first passing by magnitude" is not credibility order. But each candidate's AHET spectrum is
   produced by a different per-candidate ECA guard, so peak-to-floor values may not be perfectly
   comparable across candidates. If the next fix ranks candidates by evidence strength, validate
   that the score remains meaningful when each candidate changed the projection basis that created
   its own floor.

6. **The second-harmonic discriminant needs an ECA-coverage decision.** §11.2 proposes using the
   cardiac second harmonic to distinguish near-collisions, but the current ECA coverage is bounded
   by `band_hi` for the fundamental cardiac band, while AHET searches up to `2 * band_hi`. That
   means respiratory harmonics in the AHET second-harmonic region can remain uncancelled, and in
   low-HR cases an exact/near `2k*f_r` harmonic may be inside the projected range while higher-HR
   cases are not. If the second harmonic becomes load-bearing, specify whether ECA should also
   cover the AHET region, or explicitly model the nearest respiratory harmonic at `2k*f_r` as part
   of the AHET gate.

7. **Option A is not just a one-line operational change once it becomes real.** Deriving
   `prov_cand_hz` from a pilot ECA spectrum may be a small local code edit, but making it
   reviewable requires new evidence artifacts: the pilot spectrum, pilot candidate, pilot
   skipped/projected set, and cardiac retention at the pilot candidate. Without those, a later
   wrong estimate will be hard to diagnose, especially in the exact-collision case where the pilot
   has already notched the true cardiac peak.

8. **The pilot-residual argument should probably be treated as an ambiguity detector, not a
   candidate source, in exact collisions.** §11.1 notes that the pilot pass erased the true cardiac
   peak by about 30 dB but still left a usable residual. I agree that this is the load-bearing
   claim to attack. My recommendation: if the pilot candidate is itself close to `k*f_r` and its
   cardiac-bin retention is deeply negative, treat that as `coincidence_ambiguous` unless the
   second-harmonic evidence independently separates it. Do not let a notch shoulder become the
   thing that chooses which harmonic to spare.

9. **The source comments now call `guard_cardiac_candidate_v1` "CURRENT" even though configs do not
   promote it.** This is minor but potentially misleading during review. The plan and YAMLs
   correctly say the mode is implemented but not promoted; the `estimate_rate_from_phase()`
   docstring should use the same wording so nobody mistakes availability for the live/default
   algorithm.

---
---

# PART III — AMENDED DESIGN v2 (for review)

> **⚠ SUPERSEDED — v2 was REJECTED by the §20 review. Do not implement any of this Part.**
> Review comment §20.4 proved v2's mechanism collapses under a f_r error of one tenth of an
> FFT bin (§21). **The plan of record is PART IV (§23).** Kept unedited for the record.

> **Status: NOT IMPLEMENTED. Awaiting review pass 3.**
> Written 2026-07-13 in response to the §12 review. **Self-contained: a reviewer needs only
> this Part plus §10 (the post-mortem) to assess it.**
>
> **Headline: review comment §12.6 supersedes my own Option A (§11.1).** The reviewer found the
> root cause. I was patching a symptom. My recommendation is now *their* recommendation, and
> §11.1's pilot is demoted to the narrower role §12.8 proposed for it.
>
> All nine §12 comments were verified against the code. **All nine are valid; none rejected.**

---

## 13. What changed, and why my own recommendation is withdrawn

**v1 (implemented, failed):** guard the cardiac candidate; spare only colliding harmonics.
→ Failed: the guard protected a *decoy* harmonic, which AHET then confidently reported (§10).

**§11.1 (my proposed repair): the "pilot" pass.** Derive the provisional candidate from an
all-harmonics-cancelled spectrum so decoys cannot become the provisional candidate.
→ This fixes *candidate ordering*. It does **not** explain why AHET accepted the decoy at all.
It also leaned on a −29.8 dB notch-shoulder residual that I said, in writing, I did not trust.

**§12.6 (reviewer): the real root cause.** ECA's ceiling is `band_hi` (2.0 Hz), but AHET searches
for the cardiac 2nd harmonic up to **2 × band_hi** (~4 Hz). **That region is never cleaned.**

**Withdrawn:** §11.1 as the primary fix. Retained only in the §12.8 role — an *ambiguity
detector*, not a candidate source (§15.4).

## 14. Evidence for §12.6 — AHET cannot reject a respiratory harmonic

### 14.1 The structural flaw

A decoy at k·f_r has its "2nd harmonic" at **2k·f_r — which is itself a respiratory harmonic.**
Every harmonic k has a partner at 2k. And 2k·f_r lives in the 2.0–4.0 Hz region that ECA never
touches. So **AHET's second-harmonic consistency check — the mechanism the whole method rests on —
is structurally incapable of distinguishing a respiratory harmonic from a heartbeat.**

Measured (f_r = 0.30 Hz; decoy = 4·f_r = 72 bpm; true heart = 96 bpm; realistic respiratory comb
k = 1..14, because real breathing is not a sinusoid):

```
  AHET seeks the decoy's 2nd harmonic at   2 × 1.20 Hz = 2.40 Hz
  8 × f_r                                = 8 × 0.30 Hz = 2.40 Hz   <-- THE SAME PLACE

  spectral magnitude at 2.40 Hz (decoy's "evidence")   = 18.75
  spectral magnitude at 3.20 Hz (true heart's evidence) =  5.83
```

**AHET's evidence for the fake is 3.2× stronger than for the real heart.** This is not a tuning
problem and no gate threshold fixes it.

This also explains the observed failure exactly: in §10.3 the decoy passed with `2nd = 144.00 bpm`
— which is precisely 8·f_r.

### 14.2 The fix, measured

Extend ECA's projection ceiling from `band_hi` to **2 × band_hi**, so the 2k·f_r lines are
cancelled *before* AHET looks for them. Same signal, same gates, only the ceiling changes:

| ECA ceiling | k_max_eff | verified | reported | verdict |
|---|---|---|---|---|
| 2.0 Hz (current) | 6 | True | **72.00 bpm** | reports the decoy |
| **4.0 Hz (v2)** | 13 | True | **95.99 bpm** | **CORRECT — finds the heart** |

Candidate detail under v2:

```
  rank 0:  72.00 bpm  2nd=139.35  -> low_cand_floor_db_low   <-- DECOY, now REJECTED
  rank 1:  95.98 bpm  2nd=192.02  -> PASSED                  <-- TRUE HR, returned
```

The decoy is still *found* (its fundamental is still spared by the guard), but its **fake evidence
is gone**, so AHET rejects it and moves on. **§8.4 would XPASS.**

*Caveat on my own probe:* `n_eca_projected` printed 5 in both arms — an artifact of the
monkeypatch (the reporting call still used the old ceiling). The HR verdict is real; that counter
is not. It is a warning for the implementation — see §15.5.

### 14.3 Why this is the better fix

- It addresses **why the decoy passes**, not **which candidate is examined first**.
- It does not depend on the notch-shoulder residual I distrusted (§11.1).
- **It makes §11.2 work.** I proposed the cardiac 2nd harmonic as a discriminant ("the separation
  doubles: HR = k·f_r + δ ⇒ 2·HR = 2k·f_r + 2δ"). That was useless while 2k·f_r sat *uncancelled*
  right next to it. §12.6 is the precondition that makes §11.2 viable. I had the dependency
  backwards.
- **It handles exact collision correctly by construction.** If HR = k·f_r exactly, then
  2·HR = 2k·f_r is also cancelled, AHET cannot verify, and the window returns **NaN** — the honest
  answer for a case that is genuinely unidentifiable by frequency (§11.2).

---

## 15. The amended design (v2)

### 15.1 Extend the ECA projection ceiling to cover the AHET search region

New config key, replacing the implicit use of `band[1]` as the ECA ceiling:

```yaml
heart:
  eca_ceiling_hz: 4.0     # = 2 x band_hi. ECA must clean the region AHET inspects.
  k_max_cap:      16      # raised from 10 — the ceiling now admits more harmonics
```

```python
eca_ceiling = min(eca_ceiling_hz, bp_hi)          # never exceed the bandpass (bp_hi = min(2*band_hi, fs*0.45))
k_max_eff   = min(k_max_cap, floor(eca_ceiling / f_r))
```

**Applies to the new mode only.** `legacy` and `skip_forbidden_harmonics_v1` keep the old ceiling —
they are the comparison arms and must not move.

Coverage required (ceiling 4.0 Hz):

| BR | f_r | k_max_eff needed | subspace columns (2 per k) |
|---|---|---|---|
| 20 bpm | 0.333 Hz | 12 | 24 |
| 18 bpm | 0.300 Hz | 13 | 26 |
| 16 bpm | 0.267 Hz | 14 | 28 |
| 12 bpm | 0.200 Hz | **20** → capped at 16 | 32 |

### 15.2 The guard — unchanged from v1

`cardiac_skip_ks()`, per-pass skip sets, `eca_cardiac_guard_hz` (§5.1–§5.6) all stay exactly as
implemented. They are not the bug. The guard still spares a harmonic that lands *on* the cardiac
candidate, which is what protects a genuine collision-case heart at the **fundamental**.

### 15.3 What happens in each case under v2

| case | fundamental | 2nd harmonic | AHET outcome |
|---|---|---|---|
| **NORMAL** (no collision) | heart clean; harmonics cancelled | heart's 2·HR clean | **correct HR** |
| **DECOY** (harmonic outranks heart) | decoy spared by guard | decoy's 2k·f_r **cancelled** | decoy **rejected**; heart accepted → **correct HR** |
| **NEAR-collision** (δ ≳ 2 bpm) | colliding k spared | 2·HR sits 2δ from 2k·f_r → **survives** (§11.2) | **correct HR** |
| **EXACT collision** (δ ≈ 0) | colliding k spared | 2·HR = 2k·f_r → **cancelled with it** | **NaN** — honest |

### 15.4 The pilot, demoted — an ambiguity detector (adopting §12.8)

Not a candidate source. Used only to *label* a coincidence:

```python
if pilot_candidate is within eca_cardiac_guard_hz of some k·f_r
   AND cardiac-bin retention through the pilot pass is deeply negative (e.g. < -15 dB):
       mark the window coincidence_ambiguous
```

An ambiguous window may only be accepted if the **2nd-harmonic evidence independently separates
it** (§11.2); otherwise it returns **NaN with an explicit `coincidence_ambiguous` reason code**.
Per §12.8: *do not let a notch shoulder become the thing that chooses which harmonic to spare.*

### 15.5 Implementation notes forced by the §12 review

- **One ceiling, one source of truth.** My probe artifact (§14.2) happened because the *reporting*
  call to `eca_harmonic_ks()` used a different ceiling from the *projection* call. `eca_ceiling`
  must be threaded into **both**, or the artifact will lie about what the algorithm did — the exact
  failure §8.1b exists to prevent.
- **§12.1** — Step 6 must persist `k_max_eff` and `n_eca_projected` to `heart_intermediates.npz`
  and `heart_windows.csv`. They are currently returned by `vitals.py` and stored by
  `live_demo.py`, but **dropped by the offline path** — which is the path that will score the
  modes.
- **§12.2** — `_fake_estimate()` still returns `eca_skipped_harmonics=np.zeros(k_max)` and the
  shape test still asserts `(k_max,)`. The Step 6 integration tests therefore **never exercise the
  cap-sized artifact path**. Fix the fake to the production shape (+ the new scalars), or add a
  dedicated test with a fake vector longer than `k_max`.
- **§12.3** — the §8.4 xfail is **broken as written**: it only rejects estimates within 5 bpm of the
  decoy, so a future change that confidently reports a *different* wrong HR (85, 110 bpm) would
  XPASS. Tighten it: when `ahet_verified` is True, the estimate must be within tolerance of the
  true HR; NaN stays acceptable.
- **§12.4** — store **candidate-wise** skip/projected sets (or at minimum the accepted candidate's),
  since the whole failure is about rank-0 vs rank-1 being judged on *different* spectra.
- **§12.9** — the `estimate_rate_from_phase()` docstring calls `guard_cardiac_candidate_v1`
  "CURRENT" while no config promotes it. Reword to "implemented, not promoted".

### 15.6 Deferred, deliberately

- **§11.3 / §12.5 — candidate ordering by credibility.** §12.5 is right that peak-to-floor is **not
  comparable across candidates**, because each candidate's spectrum comes from a *different* ECA
  basis and therefore a different floor. Under v2 the decoy is rejected on its own merits, so
  re-ranking is **no longer load-bearing**. Defer it rather than land two coupled changes; revisit
  only if v2 leaves ordering-driven failures.

---

## 16. New risks introduced by v2 (attack these)

1. **OVER-PROJECTION — now the top risk, and it is load-bearing.** v2 strips **26–32 subspace
   columns** from an N = 600 window (vs 10–12 today). Review 2 (finding 4) already warned that
   projecting many harmonics at a *slightly wrong* f_r attenuates the true cardiac peak. **v2 makes
   this worse *and* raises the stakes**: the fix now *depends* on the cardiac **2nd harmonic**
   surviving, so retention is no longer a side-check — it is the mechanism. If ECA collaterally
   damages 2·HR, v2 converts working windows into NaN.
   **Mandatory:** measure retention at **both** the cardiac fundamental **and 2·HR**, under
   deliberate f_r error (±1 FFT bin ≈ ±0.033 Hz, and ±0.01–0.02 Hz). **A mode that suppresses the
   floor but erases cardiac power must fail.**

2. **Near-collision yield cost.** For δ ≲ 1–2 bpm, 2δ ≲ 1 FFT bin, so cancelling 2k·f_r may take
   the heart's 2nd harmonic with it → NaN. v2 therefore *loses* some near-collision windows that
   v1 would have reported. **I claim this is the right trade** — those reports were not
   trustworthy anyway (they are exactly the §4.2 ambiguous cases) — but it is a real yield cost and
   it must be **measured**, not assumed. It should show up in the 18 bpm paced arm.

3. **`k_max_cap = 16` is a guess.** At 12 bpm the ceiling wants k = 20. Capping at 16 leaves
   harmonics k = 17..20 (3.4–4.0 Hz) uncancelled inside the AHET region — i.e. **the v2 mechanism
   is only partially applied at low breathing rates, which is exactly where the study's 12 bpm arm
   lives.** Sweep `k_max_cap`; the cap must be justified by retention, not convenience.

4. **The 2.0–4.0 Hz region is near the bandpass edge.** `bp_hi = min(2·band_hi, fs·0.45)` = 4.0 Hz
   at fs = 20 Hz. Projecting harmonics right at the filter rolloff may behave badly. Verify the
   basis is well-conditioned there (the Gram-Schmidt norm guard at `vitals.py` will silently drop
   near-degenerate columns).

5. **Unchanged: the §5.8 candidate-list ceiling.** The true HR is absent from the top-3 candidates
   on 19% (natural) / 27% (paced-16) of hops. v2 may lift this by promoting the heart up the
   ranking. **Re-measure; do not assume.**

---

## 17. Acceptance criteria (v2 must meet ALL of these)

**Synthetic (must hold before any real-data run):**
1. **§8.4 XPASSES.** The decoy scenario returns the true HR (or NaN) — never the harmonic.
2. NORMAL: correct HR, material in-band suppression (< −3 dB).
3. EXACT collision: **NaN with `coincidence_ambiguous`** — never a confident number.
4. **Cardiac retention at BOTH the fundamental and 2·HR**, under ±1 bin of f_r error (§16.1).

**Real data — the two Masimo runs, offline from raw `adc_stream.bin` (§8.6):**
5. Yield rises from 10% (natural) / 6% (paced-16), **with MAE ≤ 3 bpm vs Masimo PR.**
6. **On paced-16, the 34% of hops where a respiratory harmonic was the top candidate must
   collapse.** This is the direct, measurable prediction of §14.1 and the single best real-data
   test of v2.
7. Report yield **and** MAE **and** retention **and** skip-decision stability together.

**If (1) or (4) fails, v2 is dead and we ship `legacy`** (see §18.3).

---

## 18. Open questions for this review

1. **Is 2 × band_hi the right ceiling, or should ECA cover the full bandpass?** AHET searches
   `2·f_h ± 0.1 Hz` with f_h up to 2.0 Hz, so it can look as high as 4.1 Hz — just past a 4.0 Hz
   ceiling. Off-by-one risk, exactly like the §12.1 ceiling bug we already fixed once.

2. **Does over-projection (§16.1) kill this?** 26–32 columns from N = 600. My synthetic says the
   heart's 2nd harmonic survives — but that is one realisation of one signal, and v2 *depends* on
   it. **This is the question I most want answered.**

3. **Is the near-collision yield cost (§16.2) acceptable for the paper?** v2 turns some
   near-collision windows into NaN. I argue that is correct (they were ambiguous), and that
   coverage-with-honest-NaN beats accuracy-on-a-biased-subset (CLAUDE.md §4). Confirm.

4. **Should `k_max_cap` scale with f_r instead of being a flat cap?** A flat 16 under-covers the
   12 bpm arm (§16.3).

5. **Given §10.6 (the decoy case is 34% of real hops) and §10.5 (`legacy` is the only mode that
   currently gets it right) — should `legacy` be the shipping default until v2 is proven?** I think
   yes, and that this should be an explicit decision rather than a default-by-inaction.

---

## 19. Disposition of the §12 review comments

| # | comment | disposition |
|---|---|---|
| 12.1 | Step 6 does not persist `k_max_eff` / `n_eca_projected` | **valid — verified. Fix in v2 (§15.5)** |
| 12.2 | fake estimator still models the old skip-vector shape | **valid — verified. Fix in v2 (§15.5)** |
| 12.3 | the xfail can XPASS for the wrong reason | **valid — a real bug I introduced. Fix (§15.5)** |
| 12.4 | ECA metadata is first-pass only, not candidate-wise | **valid. Fix in v2 (§15.5)** |
| 12.5 | peak-to-floor not comparable across candidates | **valid. Makes §11.3 unsafe → deferred (§15.6)** |
| 12.6 | 2nd-harmonic discriminant needs an ECA-coverage decision | **valid — and it is the ROOT CAUSE. Now the primary fix (§14, §15.1)** |
| 12.7 | Option A needs new evidence artifacts to be reviewable | **valid — moot: Option A demoted (§15.4)** |
| 12.8 | treat the pilot residual as an ambiguity detector, not a candidate source | **valid. Adopted verbatim (§15.4)** |
| 12.9 | docstring calls the unpromoted mode "CURRENT" | **valid. Fix (§15.5)** |

**Nine comments, nine valid, zero rejected. §12.6 changed the design; §12.8 changed the role of my
own proposal; §12.3 caught a broken test I wrote.**

---

## 20. OpenAI review comments on amended design v2 — 2026-07-14

The central v2 diagnosis is sound but should be stated narrowly: **AHET's second-harmonic gate
cannot, by itself, distinguish a respiratory harmonic `k*f_r` while the matching respiratory
line `2k*f_r` is left in the spectrum.** The complete estimator still has other gates, so
"AHET cannot reject a respiratory harmonic" is too absolute. Extending ECA coverage to the
evidence region is the correct mechanism to test next, subject to the points below.

1. **The §14.2 probe is not yet reproducible evidence.** I found no script, config, result artifact,
   or test outside this plan containing the reported 18.75/5.83 magnitudes or 95.99 bpm verdict.
   A monkeypatch whose own `n_eca_projected` counter was known wrong is useful exploration, but it
   cannot support "measured" claims under `CLAUDE.md` §3. Before implementation, preserve the
   probe as a deterministic test/script with seed and exact signal parameters; then make the real
   implementation reproduce it without monkeypatching.

2. **The 16 bpm coverage row is off by one.** Using the actual value `16/60` Hz and the inclusive
   ceiling, `floor(4.0 / (16/60)) = 15`, not 14; the table gets 14 only by first rounding `f_r` to
   0.267 Hz. This is exactly the kind of boundary error `_CEIL_EPS` was added to prevent. Tests and
   tables should use unrounded rates and include exact-ceiling cases at 12, 16, 18, and 20 bpm.

3. **Answer to §18.1: 4.0 Hz is the coherent ceiling for the current pipeline, but AHET must have
   an explicit supported-region rule.** `bandpass_filter()` is an exact FFT mask and `bp_hi` is
   4.0 Hz, so "full bandpass" and `2*band_hi` are currently the same thing. The `+0.1 Hz` AHET
   tolerance does not require cancelling a respiratory partner above 4.0 Hz: for every candidate
   at or below 2.0 Hz, its exact `2k*f_r` partner is at or below 4.0 Hz. However, a refined
   candidate near the upper edge can make an asymmetric/partly unsupported search window, and
   subtracting finite-record off-bin ECA bases can itself put leakage outside the original FFT
   mask. Clip the AHET mask to the declared analysis ceiling and reject an edge candidate when its
   required second-harmonic centre is unsupported (or deliberately widen the bandpass, ECA ceiling,
   and artifacts together). Add a candidate-at-`band_hi` boundary test. Do not silently search to
   4.1 Hz while claiming only 4.0 Hz is analysed.

4. **The mandatory `f_r`-error experiment must be two-sided.** §16.1 checks whether the cardiac
   fundamental and second harmonic survive, but v2 also depends on actually removing the
   respiratory `2k*f_r` line. Harmonic frequency error grows as `k*Delta f_r`; at k=13, a one-bin
   `f_r` error moves the projection by about 0.43 Hz. The heart may be retained while the fake AHET
   evidence is not cancelled, recreating the original failure. For every `f_r` perturbation,
   report both (a) attenuation at the true respiratory `2k*f_r` and (b) retention at `2*HR`, plus
   the final candidate verdict. This is more load-bearing than the raw count of projected columns.

5. **Answer to §18.2: column count alone is unlikely to decide viability; mismatch and coherence
   are the real risks.** Even 40-52 columns are a small fraction of N=600, and the harmonic bases
   are normally well separated over 30 seconds. The dangerous cases are a wrong `f_r`, overlap
   with cardiac frequencies, and an ill-conditioned or partly dropped basis. Also, §16.4's
   explanation is technically incorrect: the filter edge does not make the generated sine/cosine
   basis ill-conditioned. The input is hard-masked at 4.0 Hz, while basis conditioning is set by
   the finite-window frequencies. Test the projection matrix condition/coherence and actual basis
   rank separately from edge attenuation.

6. **Current diagnostics cannot verify the "silently dropped columns" risk.** `n_eca_projected`
   is computed from `eca_harmonic_ks()` before modified Gram-Schmidt; it counts selected harmonic
   orders, not harmonic orders or sine/cosine columns that actually survive the norm guard inside
   `eca_project()`. If §16.4 remains an acceptance concern, `eca_project()` needs to return the
   actual retained/dropped basis diagnostics (candidate-wise for second passes). Otherwise a run
   can report complete coverage while the projection used fewer columns.

7. **The near-collision arithmetic in §16.2 needs correction.** A 30 s bin is 0.0333 Hz = 2 bpm
   at the fundamental. If the HR offset is `delta_bpm`, separation at the second harmonic is
   `2*delta_bpm`: 1 bpm becomes one FFT bin, while 2 bpm becomes two bins. Therefore
   "delta <= 1-2 bpm implies `2*delta` <= 1 bin" is not true; only roughly delta <= 1 bpm does.
   The claimed transition should be measured as a continuous offset sweep rather than fixed at
   2 bpm, because projection leakage and the Hann spectrum make a hard bin-count boundary unsafe.

8. **Answer to §18.3: honest NaN is the right scientific outcome for unresolved coincidence, but
   report the selection effect.** Acceptance should include yield/coverage stratified by distance
   to the nearest respiratory harmonic, not only aggregate yield and conditional MAE. Otherwise
   v2 can appear accurate by rejecting its hardest windows. Pre-specify the ambiguity band from
   the synthetic offset sweep and report MAE together with rejected-window fraction inside and
   outside that band.

9. **Answer to §18.4: do not make `k_max_cap` scale with `f_r`; `k_max_eff` already does.** The cap
   is a safety/resource bound and should be fixed from the supported domain. Full 4.0 Hz coverage
   needs cap 20 for a declared 12 bpm minimum, or cap 26 for the estimator's current 0.15 Hz
   physiological gate. A cap of 16 knowingly breaks v2's discriminant at low BR. Either choose a
   cap that covers the declared minimum and validate its rank/retention, or explicitly flag
   `eca_coverage_incomplete` and forbid AHET verification where the required `2k` order exceeds
   the cap. A silent partial implementation is not acceptable.

10. **The pilot in §15.4 is still under-specified and should not be coupled to the first v2 test.**
    Define which ceiling/mode creates it, how retention is measured without already knowing the
    heart bin, whether ambiguity is a candidate rejection or a whole-window reason, threshold
    configuration, and reason-code precedence. My preference is a staged implementation: first
    extend coverage and test NORMAL/DECOY/offset-sweep/EXACT behavior; add the pilot only if the
    exact-collision residual can still pass AHET incorrectly. If retained now, add false-ambiguity
    tests at low BR where a 0.10 Hz guard can overlap two adjacent harmonics (`f_r < 0.20 Hz`).

11. **The v2 verdict currently depends on a cross-band score that should be validated.** In
    `vitals.py`, the AHET numerator is taken near `2*candidate` (up to 4.0 Hz), while its comparison
    floor is the median of the 0.8-2.0 Hz cardiac band. Extending ECA changes those regions
    differently. Preserve this gate for the first isolated experiment if desired, but compare it
    against a local second-harmonic-region floor on synthetic and real data before treating the
    existing dB thresholds as calibrated for v2.

12. **Answer to §18.5: do not automatically ship `legacy` if v2 fails.** `legacy` gets the current
    synthetic decoy right but has a known collision-erasure failure; that is not enough evidence
    to promote it. Keep all configs explicitly unpromoted until the same offline, Masimo-gated
    comparison is run for `legacy`, current, and v2. Replace §17's "v2 is dead and we ship legacy"
    with "v2 is rejected; choose a fallback only from the comparison criteria fixed below."

**Recommended implementation order after this review:** first make the §14.2 probe reproducible
and repair §8.4's assertion; then implement only the common 4.0 Hz ceiling plus complete artifacts;
then run the two-sided `f_r`-error and collision-offset sweeps. The pilot and any fallback promotion
should be separate decisions based on those results.

---
---

# PART IV — v2 IS NOT VIABLE AS WRITTEN. STAGED PLAN.

> **Status: v2 REJECTED in its current form. No *v2 algorithm* implementation until Stage 1A
> passes.** (Stage 0 *does* change code — including an `eca_project()` API addition — and
> Stage 1 needs experimental DSP code; neither touches the production path. Wording fixed
> per review comment 7.)
> Written 2026-07-14 in response to the §20 review.
>
> **All 12 §20 comments accepted; none rejected.** Comment **§20.4** is decisive: I tested it, and
> **v2 collapses under a f_r error of one tenth of an FFT bin.** See §21.
>
> This Part replaces PART III's "implement v2" with a **staged plan** (§23) in which each stage is
> small, cheap, and independently falsifiable — so we learn whether the idea is dead *before*
> investing in it.

---

## 21. §20.4 tested — v2 collapses under realistic f_r error

v2's mechanism is: *cancel the `2k*f_r` line so AHET cannot use it as fake evidence for a decoy at
`k*f_r`.* But an ECA projection line at order k is displaced by **k · Δf_r**. v2 depends on
k = 8..15. The reviewer predicted this is fatal. **It is.**

Measured (true f_r = 0.30 Hz / 18 bpm; decoy = 4·f_r = 72 bpm; true heart = 96 bpm; ECA ceiling
4.0 Hz; realistic respiratory comb k = 1..14; one FFT bin = 0.0333 Hz):

| f_r error | attenuation at 8·f_r *(the fake evidence)* | retention at 2·HR | reported | verdict |
|---|---|---|---|---|
| **exact** | **−51.0 dB** | +0.0 dB | 95.99 | **correct** |
| **+0.10 bin** (= 0.2 bpm) | **−1.6 dB** | −0.0 dB | **71.93** | **DECOY** |
| −0.10 bin | −1.3 dB | +0.0 dB | **72.05** | **DECOY** |
| +0.25 bin | −0.0 dB | +0.0 dB | 89.99 | **90 bpm = 5·f_r — another harmonic** |
| ±0.5 … ±2 bin | ~0 dB | ~0 dB | 90.00 | **another harmonic** |

**Read the second column.** Cancellation of the fake-evidence line goes from −51 dB to −1.6 dB for
a f_r error of **one tenth of a bin — 0.2 bpm.** Every non-exact row then reports a respiratory
harmonic (72 = 4·f_r, or 90 = 5·f_r). Note the heart itself is retained perfectly throughout
(column 3): **v2 does not fail by damaging the heart. It fails by not removing the decoy's fake
evidence.** That is exactly the two-sided failure §20.4 predicted, and it would have been invisible
to a retention-only check.

**Required f_r accuracy for v2 to work: ≲ 0.01 bin ≈ 0.02 bpm (≈ 3 × 10⁻⁴ Hz).**
For reference, on the real Masimo runs f_r wandered over 0.27–0.33 Hz hop to hop.

### 21.1 The general principle (this is the real lesson)

**ECA's cancellation quality degrades as k · Δf_r.** Any mechanism that leans on *high-order*
harmonic lines is fragile by construction.

`legacy` is robust to f_r error **partly because it only projects k ≤ 4**, where the same f_r error
does 3–5× less damage. v2 deliberately moved the load onto k = 8..15 — **a 13× amplification of
f_r error** — without noticing. That is the design error, and it is more fundamental than the
forbidden-zone bug this whole plan started from.

**Consequence for the project:** f_r precision is now a *first-class* requirement, not an input we
take for granted. Everything downstream of ECA inherits k · Δf_r.

---

## 22. Decisions on the §20 review — 12 accepted, 0 rejected

| # | comment | decision |
|---|---|---|
| — | "AHET cannot reject a respiratory harmonic" is too absolute | **Accepted.** Correct claim: *AHET's second-harmonic gate cannot do it **alone**, while `2k*f_r` remains in the spectrum.* Narrowed throughout. |
| 20.1 | the §14.2 probe is not reproducible evidence | **Accepted.** Scratchpad probes cannot support "measured" claims (CLAUDE.md §3 rule 1). → **Stage 0.** |
| 20.2 | 16 bpm coverage row is off by one | **Accepted — my error.** `floor(4.0/(16/60)) = 15`, not 14; I got 14 by rounding f_r to 0.267 first. **The exact bug `_CEIL_EPS` exists to prevent.** 12, 16 and 20 bpm all hit the ceiling *exactly* → mandatory test cases. |
| 20.3 | AHET needs an explicit supported-region rule | **Accepted.** 4.0 Hz is coherent (`bp_hi` = 4.0), but the AHET mask must be **clipped to the declared ceiling** and an edge candidate rejected when its 2nd-harmonic centre is unsupported. No silent search to 4.1 Hz. Boundary test at `band_hi`. |
| 20.4 | the f_r-error experiment must be two-sided | **Accepted — and DECISIVE. See §21. It kills v2 as written.** |
| 20.5 | column count is not the risk; mismatch/coherence are. §16.4 is technically wrong | **Accepted — my error.** The bandpass is an exact FFT mask on the *input*; it does not make the sine/cosine *basis* ill-conditioned. Conditioning is set by the window and the frequencies. §16.4 withdrawn. Test basis rank/coherence separately from edge attenuation. |
| 20.6 | diagnostics cannot verify silently-dropped basis columns | **Accepted.** `n_eca_projected` counts *selected orders*, not columns surviving the Gram-Schmidt norm guard. `eca_project()` must return retained/dropped basis diagnostics (candidate-wise on 2nd passes). → **Stage 0.** |
| 20.7 | near-collision arithmetic in §16.2 is wrong | **Accepted — my error.** A bin is 2 bpm, so `2δ ≤ 1 bin` only for **δ ≤ 1.0 bpm**, not the "1–2 bpm" I wrote. Replace the fixed boundary with a **continuous offset sweep**. |
| 20.8 | honest NaN is right, but report the selection effect | **Accepted.** Yield/coverage must be **stratified by distance to the nearest respiratory harmonic**, with MAE *and* rejected-window fraction inside and outside an ambiguity band fixed in advance of scoring. Otherwise the method looks accurate by rejecting exactly its hardest windows. |
| 20.9 | do not scale `k_max_cap` with f_r; cap 16 is a silent partial implementation | **Accepted.** Full 4.0 Hz coverage needs **cap 20** at a declared 12 bpm minimum, **cap 26** at the 0.15 Hz physiological gate. Either cover the declared domain, or explicitly flag `eca_coverage_incomplete` and **forbid AHET verification** where the required `2k` order exceeds the cap. |
| 20.10 | the pilot is under-specified; stage it separately | **Accepted.** Pilot is **removed from the critical path**. Also correct: at **f_r < 0.20 Hz the ±0.10 Hz guard spans 0.20 Hz ≥ the harmonic spacing**, so two adjacent harmonics can fall inside one guard → false ambiguity at 9–12 bpm. |
| 20.11 | the AHET verdict rests on a cross-band score | **Accepted — verified in code.** `peak2_magnitude` is measured near 2×candidate (up to 4 Hz) but `comparison_floor = median(spec2[cardiac_mask])` is the **0.8–2.0 Hz band** (`vitals.py`). Extending ECA changes numerator and denominator **differently**, so the existing dB thresholds are **not calibrated** for any extended-ceiling mode. Must be validated against a **local** 2nd-harmonic-region floor. |
| 20.12 | do not auto-ship `legacy` if v2 fails | **Accepted.** §17's *"v2 is dead and we ship `legacy`"* is **withdrawn** and replaced by: *"v2 is rejected; a fallback is chosen only from the offline comparison fixed below."* `legacy` has its own known collision-erasure failure; one synthetic win does not earn promotion. **All configs stay unpromoted.** |

---

## 23. Staged plan — small, cheap, falsifiable

**Principle: do not implement v2. Find out whether its precondition is even achievable, first.**
Each stage below is small, ends in a **measurable pass/fail**, and — critically — **Stages 0 and 1
require no DSP design decisions at all.** If Stage 1 fails, v2 dies for ~a day of work instead of a
fortnight.

### Stage 0 — pay down the review debt (no design risk) — ✅ **COMPLETE 2026-07-14**

> Done. 567 tests green, 1 xfail (the known design hole, now asserted strictly). Includes the
> pinned `eca_project()` diagnostic API + a **numerical-invariance regression** proving the
> projected signal is bit-identical with diagnostics on (review comment 8). See HISTORY.md.

Pure hygiene. Nothing here depends on which fix we eventually choose.

| task | source |
|---|---|
| Fix the §8.4 xfail so it **cannot XPASS for the wrong reason** — when `ahet_verified`, require the estimate near the TRUE HR; NaN stays acceptable | §20 review, comment 3 |
| Turn the §14.2 probe into a **committed, seeded, deterministic test** (exact signal params, no monkeypatch) so its numbers are reproducible evidence | 20.1 |
| Step 6 must persist **`k_max_eff` / `n_eca_projected`** to `heart_intermediates.npz` + `heart_windows.csv` (offline is the path that will score the modes) | 20.1 (§12.1) |
| Fix `_fake_estimate()` to return the **production skip-vector shape** + new scalars, so Step 6 tests actually exercise the cap-sized path | §12.2 |
| `eca_project()` returns **retained/dropped basis diagnostics** (not just selected orders) | 20.6 |
| Store **candidate-wise** skip/projected sets | §12.4 |
| Docstring: `guard_cardiac_candidate_v1` is **"implemented, not promoted"**, not "CURRENT" | §12.9 |
| Coverage table + tests use **unrounded f_r**, with exact-ceiling cases at 12/16/20 bpm | 20.2 |

**Pass:** all tests green; artifacts describe what the algorithm actually did.
**This stage is worth doing even if every design below is abandoned.**

---

### Stage 1 — WITHDRAWN and REWRITTEN. Evidence first, then the v2 design.

> **The old Stage 1 ("build a comb-fit f_r estimator") is dead, and it was tested before being
> built.** Its premise — that better f_r precision unlocks deep high-k cancellation — is **false on
> real data.** Worse, **a stationary synthetic comb would have PASSED it**, because such a comb has
> perfectly coherent harmonics by construction. That is exactly the model-mismatch trap the review
> named. See §25.

**The gate question has changed.** It is no longer *"can we estimate f_r precisely enough?"*
It is: **"does a cancellable respiratory line even EXIST at high k in real data?"** — and, in
parallel, **"is there a discriminant that needs no high-k precision at all?"**

---

#### §25 — Why the old Stage 1 was withdrawn (measured, on both Masimo runs)

For each hop and order k, search ±1.5 bins for the frequency that cancels **best** — i.e. remove
f_r error *entirely*, giving the estimator an oracle — and compare against cancellation at our
current f_r:

| k | k·f_r | cancellation at our f_r | cancellation at the **best possible** f_r | **gain from a perfect estimator** |
|---|---|---|---|---|
| 1 | 0.30 Hz | −5.5 dB | −8.1 dB | **2.5 dB** |
| 3 | 0.91 Hz | −0.2 dB | −1.3 dB | **1.1 dB** |
| 4 | 1.22 Hz | +0.1 dB | −1.0 dB | **1.1 dB** |
| 8 | 2.43 Hz | +0.6 dB | −0.5 dB | **1.2 dB** |
| 13 | 3.95 Hz | +0.9 dB | −0.2 dB | **1.1 dB** |

**A perfect f_r estimator buys 0.5–2.5 dB. v2 needed ~20 dB.**

It is therefore **not an estimation problem.** At k ≥ 3 there is no coherent line to cancel, at any
f_r precision. *(Caveat, stated because it matters: this metric was built quickly and its absolute
depths depend on a ±0.25 Hz band choice. The **difference** column — the gain from an oracle f_r —
is a within-metric comparison and is robust. Stage 1A exists to redo the absolute measurement
properly.)*

---

#### Stage 1 — v2 DESIGN (2026-07-14), after the evaluation-design review

> The old Stage 1 ("build a comb-fit f_r estimator") was withdrawn: measured, a **perfect** f_r
> estimator buys 0.5–2.5 dB where ~20 dB is needed (§25). It was never an estimation problem.
>
> **This v2 folds in the evaluation-design review. All 12 comments accepted; none rejected.**
> The review's core point: *the experiments were under-specified in exactly the ways that would
> have produced flattering, non-reproducible results.* Every loose criterion below is now exact.

---

##### Stage 1A — Is a respiratory line at the *evidence* orders cancellable at all?

### 1A.0 The target orders — I had them WRONG

A decoy sits at **k·f_r inside the cardiac band** [0.8, 2.0] Hz. AHET looks for its "2nd harmonic"
at **2·(k·f_r) = (2k)·f_r**. So the orders whose cancellation matters are **{2k} — always EVEN, and
they MOVE with f_r.**

| BR | f_r | decoy orders k (k·f_r in band) | **evidence orders 2k (must cancel)** |
|---|---|---|---|
| 12 bpm | 0.2000 | 4,5,6,7,8,9,10 | **8,10,12,14,16,18,20** |
| 16 bpm | 0.2667 | 3,4,5,6,7 | **6,8,10,12,14** |
| 18 bpm | 0.3000 | 3,4,5,6 | **6,8,10,12** |
| 22 bpm | 0.3667 | 3,4,5 | **6,8,10** |

Union across 12–22 bpm: **even orders 6…20.**

**My previous target of "k = 8..15" was wrong twice over:** it included **irrelevant odd orders
(9, 11, 13, 15)** and **missed 6, 16, 18, 20** — and 16/18/20 appear at **12 bpm**, which is exactly
the study's paced arm. **Compute the target set PER HOP from that hop's f_r.** Do not use a fixed
order range.

*(Consistency check: 2k·f_r = 2·(k·f_r) ≤ 4.0 Hz always, so the evidence line always lies inside the
4.0 Hz bandpass. Good — that was not luck, it is forced.)*

### 1A.1 M2 — cancellation, with an exact formula and an honest null

```
C_dB(k, hop) = 10 · log10( E_after / E_before )        # negative = energy removed
E = energy within the Hann MAINLOBE (±2 bins) centred on the projected frequency
```

**The bias I must not repeat.** In §25 I chose the frequency *by maximising cancellation on the very
mainlobe I then scored*. That is fitting and scoring on the same data — **the numbers are
optimistic.** (They still came out at ~1 dB, so the bias *strengthens* the negative conclusion — but
the method was wrong and must not be reused.)

**Two required controls:**
- **Held-out estimation.** Choose the frequency and coefficients on **sub-window A**; measure
  cancellation on **held-out sub-window B**. This is the headline number.
- **Null.** Repeat at **off-harmonic frequencies** (e.g. (k+0.5)·f_r) and on **phase-randomised**
  surrogates. **The null tells us what C_dB a 2-column basis removes from noise alone.** Any real
  cancellation must beat the null by a stated margin, or it is an artefact of spending 2 degrees of
  freedom.

### 1A.2 M1 — presence
Fraction of hops where the evidence order 2k has a detectable peak (prominence above a local floor)
within ±1 bin. **A cancellation number for a line that isn't there is meaningless**, so M2 is
reported only over *eligible* hops, and eligibility is reported.

### 1A.3 M3 — coherence (NOT "model-free" — I overclaimed)

Phase tracking depends on the demodulation frequency, sub-window length, amplitude gating and
unwrapping. It is **not** assumption-free. Therefore, specified:
- demodulate at the per-hop 2k·f_r; split into sub-windows;
- **residual phase after removing the best linear slope** (a constant frequency offset is *not*
  incoherence — it is an f_r error, and must not be scored as one);
- **reject sub-windows whose amplitude is below a stated floor** — otherwise low-SNR phase wander is
  mistaken for physical incoherence;
- **validate M3 on three synthetic controls: stationary, frequency-drifting, and amplitude-modulated.**
  It must distinguish them.

### 1A.4 M0 — validate every metric on a control FIRST
Run M1–M3 on a **synthetic stationary comb**, where coherence exists by construction. They **must**
report deep cancellation and mainlobe-width lines. **A metric that cannot detect coherence where
coherence exists is broken**, and its verdict on real data is worthless.

### 1A.5 Pass/fail — exact

- **PASS:** held-out **C_dB ≤ −15 dB**, beating the null by **≥ 10 dB**, on **≥ 70 % of eligible
  hops**, at the per-hop evidence orders, in **both** runs.
- **FAIL:** anything less.

### 1A.6 What a FAIL does and does NOT license *(claim narrowed)*

**Licensed:** "On these two sessions, a **stationary single-sinusoid** model of the high-order
respiratory harmonics is **unsupported**, so *this* cancellation mechanism cannot work."

**NOT licensed** (my previous wording — withdrawn): *"dead permanently, on physics"*. Two captures of
one subject cannot establish that. **Time-varying bases, broadened/notch bases, and
harmonic-subspace models remain OPEN and DIFFERENT hypotheses.** They are simply out of scope here.

---

##### Stage 1B — Temporal continuity ⭐ (run this first)

**Hypothesis:** a respiratory-harmonic candidate **tracks f_r** across hops; a cardiac candidate does
not. Indifferent to high-k coherence, to f_r precision, and to ECA working at all.

### 1B.1 FOUR labels, not two — the collision case must not be hidden

A candidate can be within 3 bpm of **both** the Masimo PR **and** some k·f_r. That is precisely the
collision case, i.e. **the hardest one**. A "cardiac wins first" rule would silently absorb it and
flatter the classifier.

| label | definition |
|---|---|
| `cardiac_only` | within 3 bpm of Masimo PR, **not** near any k·f_r |
| `harmonic_only` | within 3 bpm of some k·f_r, **not** near Masimo PR |
| `ambiguous_both` | near **both** — the collision case |
| `neither` | near neither |

**AUC is computed on `cardiac_only` vs `harmonic_only` ONLY.** `ambiguous_both` and `neither` are
**excluded from AUC but reported separately, with counts and outcomes.** Ambiguous collisions must be
reported as **NaN**, never counted as classifier successes.

### 1B.2 Track association BEFORE coupling — rank is not identity

Candidate **rank can switch between peaks** from hop to hop. Correlating "rank-0 frequency" against
f_r, or picking the nearest integer k independently each hop, **can manufacture apparent coupling out
of nothing.**

Therefore:
1. **Link candidates into trajectories** first (explicit association rule, stated and fixed).
2. **Infer ONE k per trajectory** (not per hop).
3. Test the coupling on the trajectory: **Δf_candidate ≈ k · Δf_r** for that fixed k.

The statistic is about **how a trajectory moves with f_r**, not about a per-hop nearest-integer fit.

### 1B.3 The evaluation must be CAUSAL

**Verified:** `scripts/diagnose_step6_candidate_tracks.py` uses `viterbi_track()` — a **max-score DP
over the full sequence** (`steps/step_6/temporal_tracker.py`). **It can see future windows.** That is
a legitimate offline oracle, but it does **not** test a real-time discriminator, and reporting its
performance as if it did would be wrong.

Stage 1B therefore uses **trailing history only**, or declares a **fixed lag** and **reports the
latency**. **Warm-up windows and post-gap reacquisition count against yield** — a discriminator that
needs 10 hops of history has a real cost and it must be visible.

### 1B.4 Do NOT tune and evaluate on the same two runs

**Verified:** that script searches **120** (and, in the hop-1 sweep, **1080**) parameter combinations,
scored against Masimo error. **Selecting the best of 1080 on two same-subject sessions and reporting
it would be severe overfitting.**

Protocol:
- **Pre-specify a SMALL statistic family and a threshold-selection rule, in writing, before looking.**
- **Develop on run A, validate on run B. Then reverse. Report both directions.**
- With **two same-subject sessions**, the result is **feasibility evidence, NOT validation.** Say so
  in those words.

### 1B.5 Overlapping windows are not independent observations

30 s windows at a 3 s hop share **27/30 s of data**. Candidate rows are **not** independent ROC
samples, and raw counts will **greatly overstate** the effective sample size. (This is the standing
project decision — HANDOFF §5 — and I should have applied it here myself.)
- Report **per-session** results, not a pooled ROC.
- If any uncertainty is quoted, use **contiguous block resampling**, never per-row bootstrap.

### 1B.6 The respiration-collapse detector must be INDEPENDENT of `resp_valid`

`resp_valid` has a **known false-confidence bug** — it stayed `1` while f_r was pinned to the 0.1 Hz
band floor (3 occurrences in 3 runs). **It therefore cannot be the exclusion criterion**; using it
would be circular.

Specify a **direct band-floor-lock rule** (e.g. f_r within ε of the search-band edge for ≥ N
consecutive hops), **report the excluded contiguous spans**, and **reset the tracker after each gap**.

### 1B.7 Pass criteria — fixed here, not "a usable margin"

**All** of the following, or 1B fails:
1. **Both held-out directions (A→B and B→A) reject every known confident decoy**, including the ones
   `guard_cardiac_candidate_v1` reported.
2. **No severe Masimo errors introduced** (no new large-error accepted windows).
3. **Yield, warm-up and latency reported** — including the cost of the history requirement.
4. **Stable under specified f_r perturbations** (labels use k·f_r, so f_r error mislabels candidates;
   report label stability under a stated perturbation).
5. **Ambiguous collisions reported as NaN**, not counted as successes.

---

##### Stage 1 — decision rule

| 1A | 1B | conclusion |
|---|---|---|
| pass | — | a stationary single-sinusoid cancellation at the evidence orders is viable; reconsider that family (note: this does **not** by itself revive a common-fundamental f_r estimator — see Stage 2) |
| **fail** | **pass** | **expected. Build the continuity discriminant; keep ECA low-order.** |
| fail | fail | no discriminant. Keep ECA low-order, report `harmonic_suspect` + honest NaN, accept lower yield. Still publishable — an honest NaN beats a confident harmonic. |

---

### Stage 2 — ONLY if Stage 1A passes (not expected): the extended ceiling, in isolation

> **STALE-REFERENCE FIX (review, 2026-07-14).** This stage previously said to feed the
> extended ceiling "the **comb-fit f_r** from Stage 1". **That estimator and the old Stage 1
> are both WITHDRAWN**, so that instruction is void.
>
> **Stage 2 must state its frequency source explicitly, and cannot assume one.** Note the
> subtlety: if 1A passes only at *independently optimised per-order frequencies*, that does
> **NOT** revive a common-fundamental estimator — it would mean each evidence order needs its
> own frequency, which is a different (and more expensive) model. Whichever source is chosen
> must be named here, with its measured accuracy, before Stage 2 begins.

Implement **only** the 4.0 Hz ECA ceiling + `k_max_cap` covering the declared domain (20 at 12 bpm)
+ the `eca_coverage_incomplete` flag (20.9) + the clipped AHET mask (20.3). Feed it the **comb-fit
f_r** from Stage 1.

**No pilot. No re-ranking. No ambiguity path.** One change at a time.

**Pass:** §8.4 **XPASSES**; NORMAL still correct; the two-sided f_r sweep (§20.4) shows the fake
evidence stays cancelled across the *realistically achievable* f_r error range from Stage 1.

---

### Stage 3 — recalibrate the AHET score (20.11)

The dB thresholds are **not calibrated** for an extended ceiling: the numerator lives near 2×cand
(≤ 4 Hz), the floor is the 0.8–2.0 Hz median. Compare against a **local** 2nd-harmonic-region floor
on synthetic + real data before trusting any gate number.

**Pass:** the gate behaves equivalently or better; thresholds justified, not inherited.

---

### Stage 4 — collision behaviour, measured as a continuous sweep (20.7)

Sweep δ = |HR − k·f_r| continuously from 0 upward. Report, per δ: verdict, cardiac retention at the
fundamental and at 2·HR, and attenuation at 2k·f_r. **Derive** the ambiguity band from the data —
do not assert it at 2 bpm as I did.

**Pass:** a coherent band emerges in which the honest output is NaN, and outside which the HR is
recovered.

---

### Stage 5 — offline scoring, criteria fixed below (20.12, 20.8)

Only now: `experiments/exp_eca_modes/` (30 s / 3 s), raw `adc_stream.bin` + SHA-256, scoring
**`legacy` vs `skip_forbidden_harmonics_v1` vs the Stage-2 mode** against Masimo PR.

Report **together**: yield, MAE, **coverage stratified by distance to the nearest respiratory
harmonic** (20.8), cardiac retention, skip-decision stability, and the §5.8 candidate-rank table
re-measured.

**Direct, falsifiable prediction:** on paced-16, the **34% of hops where a respiratory harmonic was
the top candidate must collapse.**

**Promotion** is decided *only* from the criteria above, fixed before the scoring run. Not before.

---

### Stage 6 (optional, decoupled) — the pilot as an ambiguity detector (20.10)

Add only if Stage 4 shows the exact-collision residual can still pass AHET incorrectly. Must
specify: which ceiling/mode creates it; how retention is measured without already knowing the heart
bin; whether ambiguity rejects a candidate or the whole window; thresholds; reason-code precedence.
**Add false-ambiguity tests at f_r < 0.20 Hz**, where a ±0.10 Hz guard spans ≥ the harmonic spacing
(20.10).

---

## 23.1 If Stage 1 FAILS — the alternatives, in preference order

1. **Widen the notch with k.** Make each projected harmonic a *narrow subspace* rather than a single
   sinusoid, with width growing as `k · σ(f_r)`, so the notch absorbs the f_r uncertainty. Cost: it
   removes more signal at high k, and the heart's 2nd harmonic lives up there. Measurable directly
   as retention at 2·HR.
2. **Abandon the "cancel `2k·f_r`" mechanism entirely** and find a decoy discriminant that does not
   depend on high-k precision — e.g. **temporal continuity** (a respiratory harmonic tracks f_r
   across hops; a heartbeat does not — this is directly testable on the two Masimo runs, where f_r
   swept 15.8 bpm while the heart stayed put), or amplitude/phase structure.
3. **Keep ECA low-order (k ≤ 4–6) and accept the decoy problem**, handling it at candidate-selection
   level with an explicit `harmonic_suspect` flag and honest NaN.

**(2) has since been PROMOTED out of this fallback list and into Stage 1B** — it is now the
leading candidate, not a consolation prize. The natural-breathing run showed the picked peak
**stationary at 69 ± 1 bpm while 4·f_r swept 15.8 bpm across it**: a discriminant that needs no
high-k precision at all, and which the existing captures can validate immediately.

---

## 24. What is NOT changing

- **Nothing is promoted.** `live_demo_config.yaml` and `steps/step_6/config.yaml` still use
  `skip_forbidden_harmonics_v1`. The live path is unchanged. `legacy` is **not** being shipped as a
  fallback (20.12).
- The §8.4 xfail stays, and stays failing, until a fix genuinely passes it.
- **Still outstanding, independent of all the above:** the paced-18 collision fixture, and the
  `resp_valid` false-confidence bug (3 occurrences in 3 runs).
