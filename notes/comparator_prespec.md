# Comparator pre-specification — how the Masimo is compared to the radar

> **Status: INTERNAL ENGINEERING SPEC — banner corrected 2026-08-04.** **M0 was removed from the
> project on 2026-08-03**, so **nothing here is pre-registered** and no claim may be made about
> when this document was written. This banner previously asserted the opposite; that assertion was
> void from 2026-08-03 and is retracted here. Companion spec with the identical status:
> `notes/analysis_prespec.md`, which already names this file as such.
>
> What survives, and it is the part that was ever load-bearing: this document fixes how a radar
> window is scored against the Masimo, **in full and in one place, and it is applied identically
> to every estimator compared.** That is a **transparency** property, not a timing one. It exists
> because we discovered the hard way that the answer depends on the choice.
>
> **Binding on every agreement number in the paper.** No MAE / RMSE / Bland-Altman may be cited
> unless it was produced under this specification, or under a documented amendment. **The
> prospective-only rule survives** (`analysis_prespec.md` §4): a threshold may not be chosen after
> seeing the data it will be judged on.

---

## 1. Why this exists

The radar produces one HR estimate per **30 s window** (an FFT peak). The Masimo produces an
**instantaneous PR once per second**. These are not the same kind of quantity, and how you bridge
them changes the answer:

|                     comparator              | run MAE (same 5 hops) | severe errors (>5 bpm) |
|---------------------------------------------|-----------------------|------------------------|
| instantaneous PR at the window's end epoch  |       **0.16 bpm**    |             0          |
| PI-gated **mean** over the same 30 s window |       **2.72 bpm**    |             1          |

Same radar output. Same reference. **A 17× difference in the headline number, purely from the
comparator.** The first version was reported as a validated result. It was not one.

**Root cause.** The subject's PR was **not stationary** early in that run: it ran
**75 → 94 → 65 bpm over the first ~25 s**, with **PI 7–10** throughout — so the excursion was
*real*, not a sensor artefact. The radar's first accepted window spans [6 s, 36 s] and straddles it.

**The deeper point:** an FFT peak over 30 s estimates a **dominant frequency**. That is *not* the
arithmetic mean of instantaneous rates. When the reference moves inside the window, **there is no
single "true HR" for that window**, and *no* comparator is defensible. The fix is therefore not to
pick a cleverer average — it is to **detect that case and exclude it**.

---

## 2. The specification

For a radar window ending at time `t`, the span is the **half-open** interval `[t − 30 s, t)`:

> **Endpoint convention harmonised to half-open (user decision 2026-07-26, M3R-40).** This span was
> originally written closed, `[t − 30 s, t]`. For integer-second Masimo samples a closed span can
> hold 31 samples and **double-count the boundary second** shared by adjacent non-overlapping
> windows. It is corrected here to the **half-open** `[t − 30 s, t)` so that (a) each integer second
> belongs to exactly one window and (b) HR and BR use the **identical** endpoint rule — the exact
> frame-index grid `[E(k·600), E((k+1)·600))` with integer-`epoch_utc` inclusion frozen in
> `notes/analysis_prespec.md` §7, which is binding for both comparators. This was recorded as a
> clarification rather than an amendment because nothing had yet been published or scored under the
> closed form. **No deposit or DOI ever existed** — M0 was removed on 2026-08-03 — so the
> pre/post-deposit distinction the original wording drew is void. The illustrative exclusion
> percentages in §2.3 predate this clarification
> and remain exploratory design evidence, not frozen scores.

### 2.1 Reference value
**`PR_ref = median` of the *usable* Masimo PR samples inside the half-open `[t − 30 s, t)`**, where
**usable** is defined immediately below.

- Median, not mean: robust to single-sample glitches. Under the stationarity gate (§2.2) the
  median and mean coincide anyway, which is precisely the point — **once the window is stationary,
  the comparator choice stops mattering.**
- Alignment uses the integer `Timestamp` (Unix epoch, UTC) column **only** — never the `Date`/`Time`
  strings (CLAUDE.md §9).

> **CLARIFICATION — the usable-sample set (user decision 2026-07-26, M4 plan review M4R-11).**
> This specification said "PI-gated PR samples" and "≥ 80 % of the 30 expected samples
> surviving the PI gate", which did **not** settle whether a row with an acceptable PI but a
> **non-finite `pr_bpm`** counts toward the coverage floor. The case is reachable: `src/masimo.py`
> parses with `pd.to_numeric(..., errors="coerce")`, so any missing or malformed marker becomes NaN.
> Leaving it open permits two silent failures — admitting a window whose median rests on fewer than
> 24 actual PR values, or using **two different denominators** for aggregation and for coverage.
> The BR comparator never had this ambiguity because it counts *finite* RRp explicitly (§2.2 there).
>
> **Resolved — one set, used everywhere.** A Masimo row is a **usable HR reference sample iff**:
>
> 1. `pr_bpm` is **finite**, **and**
> 2. `pi` is **finite**, **and**
> 3. `pi ≥ 0.5`.
>
> **The same usable set is used for the median (§2.1), the stationarity quantiles (§2.2) and the
> coverage count (§2.2).** There is exactly one denominator. **Finite-PR** and **PI-qualified**
> counts are additionally **reported separately** per window, so a coverage failure caused by missing
> data is distinguishable from one caused by low perfusion (they are different physical stories and
> the disposition ledger separates them).
>
> **Effect on existing data: none.** All three exploratory captures have **zero** non-finite PR, PI
> or RR values and **zero** samples with `PI < 0.5` (`demo_massimo1.csv` n=247, `demo_massimo2.csv`
> n=272, `demo_sweep.csv` n=574). This clarification therefore changes no existing number; it
> forecloses a silent divergence that would first appear in M5/M6.
>
> **Status:** a clarification, same class as the M3R-40 half-open harmonisation and the M4R-09
> quantile method — made before anything had been scored under the ambiguity (and no deposit or
> DOI ever existed). It resolves an ambiguity; the `PI ≥ 0.5` threshold
> and the 80 % coverage floor are unchanged.

### 2.2 Window admissibility — ALL of these, or the window is excluded

| gate | rule | rationale |
|---|---|---|
| **PI** | drop samples with `Perfusion Index < 0.5` (and any sample with non-finite `pi` or non-finite `pr_bpm` — see the usable-sample definition in §2.1) | CLAUDE.md §4 — low PI means the reference itself is untrustworthy. Never chase it. |
| **Coverage** | require ≥ **80 %** of the 30 expected samples to be **usable** per §2.1 (i.e. ≥ 24) | a window scored on a handful of samples is not scored |
| **Stationarity** | exclude if **`p90 − p10` of the PI-gated PR inside the window > 5.0 bpm**, quantiles computed with the **`linear`** method (see below) | see §2.3 |

> **CLARIFICATION — quantile method (user decision 2026-07-26, M4 plan review M4R-09).**
> This specification did not name a quantile interpolation method, and the omission is **not**
> cosmetic: with integer-valued PR over 24–30 samples, `p10` and `p90` usually fall *between* order
> statistics, so the interpolation rule alone can decide the verdict.
>
> **Self-contained worked example** — a 30-sample window of `3 × 71`, `23 × 72`, `4 × 77` bpm
> (stated in full so it needs no script to reproduce; verify with
> `np.percentile(x, 90, method=m) - np.percentile(x, 10, method=m)`):
>
> | method | `p10` | `p90` | `p90 − p10` | verdict at the 5.0 bpm gate |
> |---|---|---|---|---|
> | `linear` | 71.90 | 77.00 | **5.100** | exclude |
> | `lower` | 71.00 | 77.00 | 6.000 | exclude |
> | `midpoint` | 71.50 | 77.00 | 5.500 | exclude |
> | `higher` | 72.00 | 77.00 | **5.000** | **admit** |
> | `nearest` | 72.00 | 77.00 | **5.000** | **admit** |
>
> Same window, same data, opposite admissibility. *(An earlier draft of this clarification also
> quoted a Monte-Carlo frequency for how often this occurs. It was removed: it traced to no committed
> script and depended on an unstated assumed PR distribution, so it could not be regenerated or
> audited — CLAUDE.md §3.1. The deterministic example above is sufficient to establish the
> ambiguity, and nothing here rests on how often it arises.)*
>
> **Resolved: `method="linear"`** — NumPy's default, and the method the existing design-evidence
> scripts already used, so it minimises retro-inconsistency. It must be **named and passed explicitly
> at every call site**, never left to a library default, in this gate, the §2.3 sensitivity table and
> the `notes/analysis_prespec.md` §1 bootstrap CI endpoints.
>
> **Status:** a clarification, in the same class as the M3R-40 half-open harmonisation — made
> before anything had been scored under the ambiguity, and no deposit or DOI ever existed. It
> resolves an ambiguity; it does not change a decided threshold.

**Reference intended-use disposition (device-wide; user decision 2026-07-26, M3R-34).** The Masimo
MightySat™ Rx *Home Care Manual* (`literature/ref_papers/lab-10168a_master.pdf`, p. 10) states the
device is *"intended for spot-check use only… Do not use… for continuous monitoring. No alarms are
provided."* The protocol logs **PR continuously for 10 min** as the HR reference — nominally outside
that spot-check labelling. **Recorded disposition (study assumption, M3R-43):** the manual gives the
warning but no rationale for it and does not address short-session accuracy. **The study *assumes***
this labelling is **principally** about (i) **battery endurance** for multi-hour/day use and (ii) the
**absence of safety alarms** for unattended monitoring, and **assumes** per-sample PR accuracy over a
**10-min attended session with healthy subjects and verified battery state** (added to the
`notes/protocol.md` equipment checklist) is unaffected; the alarm limitation is irrelevant to an
attended research capture, not patient monitoring. **The manufacturer does not certify this
inference** — it is the study's recorded engineering/clinical judgment, flagged as an assumption
(CLAUDE.md §4); it applies **identically** to the RRp reference in
`notes/comparator_prespec_br.md` §2.2.

### 2.3 The stationarity gate, and why 5 bpm

**Chosen from the frequency resolution, not tuned on radar error.** The FFT bin at a 30 s window is
**2.0 bpm**. If the reference moves by more than about one bin inside the window, the target is not
a single number.

- 5.0 bpm = **2.5 bins**.
- Measured within-window PR spread on the existing captures: **median 2.8–3.0 bpm** — that is normal
  **HRV / RSA**, it is real, and it does **not** make the dominant frequency ill-defined. A gate at
  1 bin (2 bpm) would reject 60–70 % of windows for ordinary physiology. Too aggressive.
- At 5 bpm: **12 % of windows excluded (natural), 20 % (paced-16)** — and in the natural run the
  excluded windows are **exactly the settling transient** (t = 31–46 s, whose spans reach back into
  the 75→94→65 excursion).

**Sensitivity must be reported** (exclusion fraction at 3 / 5 / 8 bpm) so the choice is auditable
rather than asserted.

### 2.4 Statistics
- **Non-overlapping 30 s windows only** (a 3 s hop shares 27/30 s of data — those estimates are not
  independent, and treating them as such fakes tight Bland-Altman limits). Standing decision,
  HANDOFF §5.
- **Report coverage alongside accuracy, always.** Accuracy computed only on surviving windows is
  selection bias. Report: total windows, excluded-by-PI, excluded-by-coverage,
  excluded-by-non-stationarity, radar-NaN, and the final n.
- **Never** compare radar HR against SpO2 / PI / PVi — only `Beats / min` (PR). CLAUDE.md §9.

---

## 3. What this specification does NOT fix

- It does not make a **non-stationary** window scorable. Those windows are **excluded and counted**,
  not rescued. If a large fraction of a session is excluded, that is a **finding about the session**
  (the subject was not settled), not a nuisance to be tuned away.
- It says nothing about whether the radar estimate is *right* — only about what it is being compared
  against.

---

## 4. Consequences already incurred

- **The "MAE 0.16 bpm" claim is WITHDRAWN.** It was 5 hops scored against a comparator that happened
  to agree, on a session whose early windows straddle a real 30 bpm PR excursion. It was never
  robust. CLAUDE.md §4: *"If a result looks too good, treat it as a bug until proven."* MAE 0.16 bpm
  against a clinical reference is too good, and I did not apply that rule to my own number.
- **The breathing-rate figure (MAE 0.77 bpm) has not been re-audited** under this specification and
  must be treated with the same caution until it is.
- **The capture protocol gains a hard settle criterion** — see `notes/protocol.md`. A protocol that
  lets recording start during a 30 bpm PR transient will keep manufacturing this problem.
