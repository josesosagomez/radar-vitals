# Comparator pre-specification — Masimo breathing rate (RR) vs radar

> **Status: INTERNAL ENGINEERING SPEC — BINDING (user decision 2026-08-04).** Prepared 2026-07-25
> as a draft for the M0 deposit; **M0 was removed on 2026-08-03, and this document is now binding
> in its own right** rather than pending anyone's freeze. **Nothing here is pre-registered** and no
> claim may be made about when it was written — its force is that it is written out in full and
> applied identically to every estimator compared. Companion specs, same status:
> `notes/analysis_prespec.md`, `notes/comparator_prespec.md`.
>
> `src/comparator.py:br_reference` implements §2.1/§2.2 and is the only place they become code.
>
> **Designed from the REFERENCE ALONE** — the
> Masimo trace, the metronome command, and FFT-resolution arithmetic. **No gate below refers to
> radar output** (CLAUDE.md §4; `plans/implementation_plan.md` M3). Deriving reference
> admissibility from radar agreement would be the mirror image of the tuning §4 forbids.
>
> **Binding on every BR agreement number in the paper** — mirrors `notes/comparator_prespec.md`
> (HR). No BR MAE / RMSE / Bland–Altman may be cited unless produced under this specification, or
> a documented amendment. **The prospective-only rule survives** (`analysis_prespec.md` §4): an
> amendment may not be made after seeing the data it will govern.
>
> **Cross-model review COMPLETE** (CLAUDE.md §6 — it touches peak-picking / reference handling):
> **all M3 findings (M3R-01…48) resolved across 17 rounds — see `plans/m3_prespec_cross_review.md`.**
> Status: **cross-reviewed and binding.** The old "READY FOR THE M0 FREEZE — NOT yet frozen"
> line named an act that can no longer happen; it was resolved by the 2026-08-04 decision above.
> First use under this status: the BR bin-selection pre-flight of 2026-08-04, whose primary
> labelling gate is §2.2's `admitted`.

---

## 1. Why this exists — and why Masimo RRp is a *weaker* reference than PR

The radar produces one BR estimate per **30 s window** (an FFT peak in the respiration band). The
Masimo produces a **Breaths / min (RRp)** value once per second. As with HR, how you bridge them
must be fixed in advance. But RRp carries an extra caveat that PR does not:

**Masimo RRp is pleth-derived** (the device documentation names it *Pleth Respiration Rate, RRp* —
§2.2 citation) and **appears smoothed and laggy** — either way a **weaker reference** than PR (and
not a gold standard; see §2.6). *"Appears" is deliberate (M3R-38):* the smoothing/lag is an
**inference** from the low **displayed** within-window variability shown below, **not** an
independently measured device property — we cite **no** external RRp algorithm and have **no**
reference-only lag measurement, and low displayed variability alone cannot by itself distinguish
device smoothing from genuinely stable breathing. Evidence of the *displayed* behaviour, from the
reference alone (the three exploratory captures; radar never consulted):

| session | windows | RRp present | within-window spread p90−p10 (bpm) | RRp range | PI median | median RRp vs commanded target |
|---|---|---|---|---|---|---|
| natural | 6 | 100 % | median **1.0**, max 3.0 | 12–19 | 8.5 | — (no pacing) |
| paced 16 | 6 | 100 % | median **0.0**, max 1.0 | 14–17 | 3.8 | offset from 16 bpm target **+0.0** |
| sweep 12/15/18/21 | 16 | 100 % | median **0.0**, max 2.0 | 12–22 | 9.6 | observed RRp range 12–22 |

*(**Origin-specific exploratory illustrations**, four subjects. Computed by the reference-only
`scripts/derive_br_comparator_evidence.py` on 30 s windows anchored at an **approximate** origin —
these exploratory captures have no persisted frame-0 epoch, so the alignment is not the exact
scoring grid (`notes/analysis_prespec.md` §7). The counts are illustrative of RRp's displayed
within-window variability at this origin; they are **not** frozen scores and no robustness across
origins is claimed. SHA-256 hashes in §4.)*

The displayed within-window RRp spread is **far smaller than the ~3 bpm** the PR reference showed for
HR (`notes/comparator_prespec.md` §2.3). The **most plausible** reading is smoothing (RRp changing
slowly and lagging true breathing-rate changes) **rather than** superior stationarity — but, as
above, that is an inference from the *displayed* trace, not a measured device property. The
consequence is stated plainly in §2.3 and §3: **if** RRp is smoothed, a stationarity gate on it is
**weaker protection** than the same gate on PR, because RRp can look stationary inside a window where
the subject's actual breathing was not.

---

## 2. The specification

For a radar window ending at time `t`, the span is the **half-open** interval `[t − 30 s, t)`
(M3R-33) — the same convention as the frozen scoring grid, where the window for frame index `k` is
`[E(k·600), E((k+1)·600))` and a Masimo sample at integer `epoch_utc = e` belongs to it iff
`E(k·600) ≤ e < E((k+1)·600)` (`notes/analysis_prespec.md` §7; that frame-index grid, not this
generic `t`, is binding). Half-open is required: with integer-second RRp, a closed `[t − 30 s, t]`
could hold 31 samples and double-count an endpoint shared with the adjacent window. §2.1 and §2.2
below use this half-open interval throughout.

### 2.1 Reference value
**`RR_ref = median` of the finite Masimo RRp samples inside the window** (the availability gate of
§2.2 requires ≥ 24 of them).
- Median, not mean (robust; mirrors HR). Alignment uses the integer `epoch_utc` (Unix seconds,
  UTC) column **only** — never the `Date`/`Time` strings (CLAUDE.md §9; `src/masimo.py`).

### 2.2 Window admissibility — ALL of these, or the window is excluded

The quality gate is built **only** from the reference's own **availability** and **variance**
behaviour, exactly as M3 requires. **PI is deliberately NOT a primary BR admissibility gate**
(see the note below).

| gate | rule | rationale |
|---|---|---|
| **Availability (coverage)** | require **≥ 24 finite `rr_bpm` samples** in the exact 30 s (600-frame) interval — i.e. ≥ 80 % of the 30 expected once-per-second values are present and non-missing | a median resting on a few samples is not a reference; counts *finite RRp*, not "samples surviving a PI gate". |
| **Stationarity** | exclude if **`p90 − p10` of the finite RRp inside the window > 2.0 bpm**, quantiles computed with the **`linear`** method (see below) | see §2.3. |

> **CLARIFICATION — quantile method (user decision 2026-07-26, M4 plan review M4R-09).**
> Neither comparator named a quantile interpolation method. With integer-valued samples over a 24–30
> sample window, `p10` and `p90` usually fall *between* order statistics, so the interpolation rule
> alone can decide the verdict — see the **self-contained worked example** in
> `notes/comparator_prespec.md` §2.2, where one explicit 30-sample window is admitted by
> `higher`/`nearest` and excluded by `linear`/`lower`/`midpoint`.
>
> The mechanism is **identical in kind** for BR. It is expected to arise less often here, because
> RRp varies less within a window than PR does — but that is a qualitative expectation, **not a
> measured rate**, and nothing in this clarification depends on it. *(An earlier draft quoted a
> Monte-Carlo frequency for both vitals; it was removed as untraceable to a committed script and
> dependent on an unstated assumed distribution — CLAUDE.md §3.1.)*
>
> **Resolved: `method="linear"`** — NumPy's default and the one already used by
> `scripts/derive_br_comparator_evidence.py`, applied **identically to HR and BR** so the two
> comparators cannot diverge on a numerical convention. Named and passed explicitly at every call
> site — this gate, the §2.3 sensitivity table, and the `notes/analysis_prespec.md` §1 bootstrap CI
> endpoints. Recorded identically in `notes/comparator_prespec.md` §2.2.
>
> **Status:** a clarification, same class as M3R-40 — made before anything had been scored under
> the ambiguity (and no deposit or DOI ever existed). It resolves an
> ambiguity; it does not change the 2.0 bpm threshold, and §2.3's "exactly 2.0 bpm is retained"
> boundary rule is unaffected.

**On PI (why it is not a primary gate).** Masimo Corporation, *MightySat™ Rx Fingertip Pulse
Oximeter — Home Care Manual*, © 2019 (revision code **0119**, i.e. January 2019), **p. 10**, states:
*"Inaccurate respiration rate (RRp) measurements may be caused by: low arterial perfusion; motion."*
It supplies **no numeric PI threshold for RRp.** *(Verified directly against the PDF at
`literature/ref_papers/lab-10168a_master.pdf`, 2026-07-26. **Document number: `LAB-10168A`** — the
identifier printed on the manual's back cover (`300162/LAB-10168A-0119`); this is the correct citable
number, confirmed by the user 2026-07-26. An earlier local filename `lab-10169a` was a typo and has
been corrected.)* No source supplies a numeric PI threshold validated **for RRp**
(the `PI ≥ 0.5` value is the one **specified by the frozen HR comparator for PR**, not a
manufacturer-validated RRp cutoff). Transferring it would
be an unsupported new BR exclusion, and M3 explicitly asked for an availability/variance gate, not a
borrowed PI gate. **PI is therefore reported only as a per-window quality flag / sensitivity
covariate, and never removes a window from primary BR admissibility.** (Measured: no window in the
three captures had PI < 0.5, so this changes none of them; the point is to avoid freezing an
unsupported gate.)

**Intended-use limitation — spot-check only (M3R-34): declared, and DISPOSITIONED (user decision
2026-07-26).** The same manual states two further limits, **verified directly against the PDF**
(`literature/ref_papers/lab-10168a_master.pdf`, 2026-07-26):
- **p. 10 (Performance Warnings):** *"Do not use MightySat Rx for continuous monitoring. It is
  intended for spot-check use only. No alarms are provided."*
- **p. 7 (Indications for Use):** the device is *"indicated for the noninvasive **spot checking** of
  respiration rate (RRp) for adult patients."*

The protocol logs the 1 Hz RRp trace **continuously for 10 minutes** as the measured reference,
nominally outside that spot-check labelling. **Recorded disposition (study assumption, M3R-43):** the
manual supplies the spot-check-only / no-alarms / not-for-continuous-monitoring warning but gives no
rationale for it and does not address short-session accuracy. **The study *assumes*** that this
labelling is **principally** about (i) **battery endurance** for multi-hour/day use and (ii) the
**absence of safety alarms** for unattended monitoring, and **assumes** that per-sample RRp accuracy
over a **10-min attended session with healthy subjects and verified battery state** (added to the
`notes/protocol.md` equipment checklist) is unaffected — the alarm limitation being irrelevant to an
attended research capture (not patient monitoring) of healthy adults recorded for 10 minutes only.
**The manufacturer does not certify this inference**; it is the study's recorded engineering/clinical
judgment, flagged as an assumption (CLAUDE.md §4). *(This does **not** disturb the §1 finding
that RRp is a weaker, non-gold-standard reference for a different reason — its smoothing/lag
**inference**; the two limitations are independent.)*
> **Ethics scope:** the approval `24IBEC051` **permits the 10-min collection** (user-confirmed
> 2026-07-24; `notes/protocol.md`), and the reference logging is intrinsic to that approved
> collection. **Device-wide note:** this same disposition applies to the Masimo **PR** reference in
> the (now harmonised) HR comparator `notes/comparator_prespec.md` §2.2, where it is recorded
> identically.

### 2.3 The stationarity gate, and why 2 bpm (HR used 5)

**Chosen from FFT resolution, not tuned on radar error.** The FFT bin at a 30 s window is
**2.0 bpm** (1/30 Hz × 60), identical to HR. But the threshold differs from HR's 5.0 bpm, and the
reason is the reference's measured behaviour:

- HR set 5.0 bpm (= 2.5 bins) because genuine HRV of ~3 bpm is real physiology that must **not** be
  excluded — a 1-bin gate there would reject 60–70 % of windows.
- RRp's measured within-window (displayed) spread is **median 0–1.0 bpm** — its displayed sub-window
  variation is already small (**consistent with**, though not proof of, smoothing). On the (approximate-origin) exploratory windows the
  **1-bin (2.0 bpm)** gate excludes **1 of 6 natural windows (~17 %) and 0 of the paced-16 (0/6) and
  sweep (0/16) windows**; a looser 2.5-bin gate (5 bpm) excludes **nothing anywhere** and is
  toothless. The gate therefore **bites where the displayed breathing rate varies (natural) and
  passes the stable paced sessions** — the desired behaviour, though on a very small, exploratory,
  small-`n` (four subjects) with approximate window alignment (§4).
- **What the gate actually observes.** 2.0 bpm = one FFT bin, so the gate flags a **displayed RRp
  transition of more than one bin** inside the window. The inequality is **strict** — the frozen gate
  excludes iff `p90 − p10 > 2.0 bpm`, so a spread of **exactly** one bin (2.0 bpm) is **retained**,
  not excluded (M3R-39). It is a **conservative RRp-display-change gate**, not
  a detector of biological breathing stationarity — smoothing does **not** "validate" a tighter
  physiological threshold. The 1-bin value is chosen because RRp's displayed spread is small
  (median 0–1 bpm), so a 1-bin gate has teeth (excludes ~17 % of natural, 0 % of paced/sweep
  windows — §1) where a 2.5-bin gate would exclude nothing; that is a property of the *displayed*
  trace, not proof about the underlying breathing.

**Sensitivity must be reported** (exclusion fraction at 2 / 3 / 5 bpm), so the choice is auditable.

**Limitation, declared (both directions).** **To the extent RRp is smoothed and laggy** (the working
inference of §1, not a measured fact), the gate errs **both ways**: (i) *false inclusion* — a real within-window transition may be hidden and passed; (ii)
*false exclusion / time-shift* — a real transition may surface in a **later, physiologically stable
window** and exclude it. So the gate cannot certify stationarity in either direction. For **paced**
sessions the metronome target-concordance (§2.5) is a **stronger check** on whether the commanded
rate held (not a guarantee of breathing); for **natural** sessions there is none, and natural-BR is
reported as **device agreement between radar and RRp under this limitation**, not validated truth
(§2.6).

### 2.4 Statistics
- **Non-overlapping 30 s windows only**, on the exact frame-index grid frozen in
  `notes/analysis_prespec.md` §7 (a 3 s hop shares 27/30 s — not independent). Same standing
  decision as HR (`notes/comparator_prespec.md` §2.4).
- **Report coverage alongside accuracy, always.** Report: total windows,
  excluded-by-availability, excluded-by-non-stationarity, radar-NaN, and the final n. (PI is
  reported as a flag only — §2.2 — not an exclusion category.)
- Never compare radar BR against SpO2 / PI / PVi / PR — only `Breaths / min` (RRp) and, for paced
  sessions, the metronome (§2.5).

### 2.5 Paced cross-check — the metronome (a target, not physiological truth)
In **paced** sessions the commanded metronome rate is a second, **target-concordance** reference —
**RRp remains the measured reference.**
- Compute agreement against **both** `RR_ref` (RRp, the measured reference) **and** the commanded
  metronome rate (**adherence / target-concordance cross-check**), and **report both**, clearly
  labelled.
- **The metronome command is exact and stationary; the participant's breathing is not "exact and
  stationary by construction."** The command is a prescribed cadence, not measured biological truth
  — a subject may lead, lag, or drift from it. So the metronome result is reported as **agreement
  with the commanded target**, and RRp–metronome disagreement is **not** resolved in the
  metronome's favour as if it were ground truth; it is reported as a compliance signal. RRp's median
  tracked the 16 bpm command with a median offset of **0.0 bpm** (design evidence, §1), but this is
  *concordance with the target*, not proof of constant breathing.
- **The 18 bpm arm:** the HR "collision" exclusion (4·f_r ≈ HR at 18 bpm → `notes/protocol.md`) is
  an **HR-cancellation** mechanism and **does not itself mandate a BR exclusion**. The 18 bpm paced
  session is therefore **included in the BR paced summary** (`notes/analysis_prespec.md` §3.2);
  report each paced rate (12/15/18) on its own terms. *(No claim is made here about BR SNR at
  18 bpm — only that the HR-specific mechanism does not carry over.)* **Estimand (M3R-31):** the
  per-rate breakdowns are **descriptive only** (bias + observed SD; each rate has ~3–4 subjects); the
  **inferential** paced-BR LoA is the **arm-level** LoA pooling **all** paced subjects (12/15/18),
  per `notes/analysis_prespec.md` §1/§3.2. (For HR the paced LoA pools 12/15 only — 18 bpm separate.)

### 2.6 Natural vs paced reference strength (report separately)
- **Natural sessions:** radar BR is **compared with RRp only** — the weak case: smoothing hides
  non-stationarity and there is no independent target. Report natural-BR agreement with explicit
  caution and the word "compared with", never "validated against".
- **Paced sessions:** radar BR is compared with **RRp (measured reference) + the metronome
  (target-concordance cross-check)**. Stronger than natural, because a second, independent target
  exists — but still bounded by RRp's quality and by compliance, not a gold standard.

---

## 3. What this specification does NOT fix
- It does not correct RRp's **apparent** smoothing/lag — that limitation is **declared** (as an
  inference, §1), not removed.
- It does not make a **non-stationary natural** window scorable, and — unlike HR — it cannot fully
  detect one, because RRp may lag the change (§2.3 limitation). Such windows may slip the gate; this
  is a known weakness of the natural-BR reference, reported as a study limitation.
- It says nothing about whether the radar BR estimate is *right* — only what it is compared against.

## 4. Design evidence (reference-only) and exploratory status
- The numbers in §1 come from the Masimo traces of the **three Masimo-referenced captures**
  (`natural`, `paced16`, `sweep`; the fourth capture, `live_test1`, has **no** Masimo) and the
  metronome command — **radar BR output was not consulted in any gate or threshold above.**
- **Reproducibility trace (CLAUDE.md §3.1).** Every §1 table cell (including the RRp range) and the
  §2.3 exclusion counts are regenerated by the committed, **reference-only, deterministic
  (no RNG/seed)** script `scripts/derive_br_comparator_evidence.py`. It anchors 30 s windows at
  `run_metadata.json`'s `start_wall_utc`, an **approximate** origin (that field is written *before*
  capture startup, so it is not the frame-0 epoch — `notes/analysis_prespec.md` §7); the resulting
  counts are **origin-specific exploratory illustrations**, not frozen scores, and no cross-origin
  robustness is claimed. The script pins the thresholds and the exact SHA-256 of **all six** consumed
  inputs — the three source CSVs **and** the three `run_metadata.json` files — in an
  `EXPECTED_SHA256` map and **asserts each against the file on disk, aborting on any mismatch**
  (M3R-35), so the printed numbers are bound to those exact inputs. (The three CSV digests also match
  `notes/capture_inventory.md`; the three metadata digests, which the inventory does not carry, are
  pinned in the script.) It **imports no radar-pipeline code and reads no radar estimate** (it reads
  `run_metadata.json` only for the `start_wall_utc` timestamp).
- **All existing radar/RRp pairs are EXPLORATORY** (four subjects; the sessions informed the
  method's design). No primary BR number may come from them.

## 5. Risk and adequacy rule — decided at M5, prospectively
Whether RRp is an adequate BR reference is decided at the **M5 pilot**, not retrospectively on M6:
- **M5 defines the trigger**, before M6: a quantitative RRp-adequacy criterion, fixed before the
  pilot runs, built
  **only from quantities the approved steady-rate pilot actually records** (M3R-41). The M5 protocol
  paces each subject at **one steady rate** — it has **no** paced steps or transitions, so an "RRp lag
  across paced steps" measure is **not available** and is **not** used. Admissible pilot quantities
  include: a bound on the **median |RRp − commanded-rate| offset** (bias vs the target), the
  **within-session RRp dispersion / stability** at the steady rate, and **RRp availability/coverage**
  under §2.2. "Systematically diverging" is made numeric from these — it is not a usable trigger while
  undefined. *(A step-response/lag measure would require a stepped maneuver, which is **not** in the
  approved study protocol and must not be added without separate protocol/ethics authorization — the
  existing stepped "sweep" capture is a method-development arm, not an M5 study session.)*
- If the M5 pilot fails that criterion, the plan **amends before M6** to
  **metronome-target-concordance for paced sessions and natural-BR exploratory-only**, and that
  amendment is **recorded before the primary data it governs** (`analysis_prespec.md` §4,
  `plans/implementation_plan.md` §M5).
- **M6 may not retrospectively switch truth sources.** Once M6 data exist, the frozen (or
  M5-amended) RRp-primary analysis stands regardless of result. A data-triggered comparator switch
  on M6 is **forbidden** — it would mean choosing the comparator by the answer it gives, which is
  the exact failure this spec exists to prevent.
