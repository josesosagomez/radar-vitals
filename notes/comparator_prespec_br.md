# Comparator pre-specification — Masimo breathing rate (RR) vs radar

> **DRAFT for the M0 deposit, prepared 2026-07-25. Designed from the REFERENCE ALONE** — the
> Masimo trace, the metronome command, and FFT-resolution arithmetic. **No gate below refers to
> radar output** (CLAUDE.md §4; `plans/implementation_plan.md` M3). Deriving reference
> admissibility from radar agreement would be the mirror image of the tuning §4 forbids.
>
> **Binding, once frozen, on every BR agreement number in the paper** — mirrors
> `notes/comparator_prespec.md` (HR). No BR MAE / RMSE / Bland–Altman may be cited unless produced
> under this specification, or a documented amendment made *before* the data it scores.
>
> **Requires cross-model review before it is frozen into M0** (CLAUDE.md §6 — it touches
> peak-picking / reference handling). Status: **awaiting cross-review.**

---

## 1. Why this exists — and why Masimo RRp is a *weaker* reference than PR

The radar produces one BR estimate per **30 s window** (an FFT peak in the respiration band). The
Masimo produces a **Breaths / min (RRp)** value once per second. As with HR, how you bridge them
must be fixed in advance. But RRp carries an extra caveat that PR does not:

**Masimo RRp is pleth-derived, heavily smoothed, and laggy** — a **weaker reference** than PR (and
not a gold standard; see §2.6).
Evidence, from the reference alone (the three exploratory captures; radar never consulted):

| session | windows | RRp present | within-window spread p90−p10 (bpm) | RRp range | PI median | median RRp vs commanded target |
|---|---|---|---|---|---|---|
| natural | 6 | 100 % | median **1.0**, max 3.0 | 12–19 | 8.5 | — (no pacing) |
| paced 16 | 6 | 100 % | median **0.0**, max 1.0 | 14–17 | 3.8 | offset from 16 bpm target **+0.0** |
| sweep 12/15/18/21 | 16 | 100 % | median **0.0**, max 2.0 | 12–22 | 9.6 | observed RRp range 12–22 |

*(**Origin-specific exploratory illustrations**, single subject. Computed by the reference-only
`scripts/derive_br_comparator_evidence.py` on 30 s windows anchored at an **approximate** origin —
these exploratory captures have no persisted frame-0 epoch, so the alignment is not the exact
scoring grid (`notes/analysis_prespec.md` §7). The counts are illustrative of RRp's displayed
within-window variability at this origin; they are **not** frozen scores and no robustness across
origins is claimed. SHA-256 hashes in §4.)*

The within-window RRp spread is **far smaller than the ~3 bpm** the PR reference showed for HR
(`notes/comparator_prespec.md` §2.3). That is not superior stationarity — it is **smoothing**: RRp
changes slowly and lags true breathing-rate changes. The consequence is stated plainly in §2.3 and
§3: a stationarity gate on RRp is **weaker protection** than the same gate on PR, because RRp can
look stationary inside a window where the subject's actual breathing was not.

---

## 2. The specification

For a radar window ending at time `t` (span `[t − 30 s, t]`):

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
| **Stationarity** | exclude if **`p90 − p10` of the finite RRp inside the window > 2.0 bpm** | see §2.3. |

**On PI (why it is not a primary gate).** Masimo Corporation, *MightySat™ Rx Fingertip Pulse
Oximeter — Home Care Manual*, © 2019 (revision code **0119**, i.e. January 2019), **p. 10**, states:
*"Inaccurate respiration rate (RRp) measurements may be caused by: low arterial perfusion; motion."*
It supplies **no numeric PI threshold for RRp.** *(Verified directly against the PDF at
`literature/ref_papers/lab-10169a_master.pdf`, 2026-07-25. **Document-number note for the user:**
the manual's back cover prints `300162/LAB-10168A-0119` while the distributed file is
`lab-10169a` — confirm the citable document number against the official source at the deposit
gate.)* No source supplies a numeric PI threshold validated **for RRp**
(the `PI ≥ 0.5` value is the one **specified by the frozen HR comparator for PR**, not a
manufacturer-validated RRp cutoff). Transferring it would
be an unsupported new BR exclusion, and M3 explicitly asked for an availability/variance gate, not a
borrowed PI gate. **PI is therefore reported only as a per-window quality flag / sensitivity
covariate, and never removes a window from primary BR admissibility.** (Measured: no window in the
three captures had PI < 0.5, so this changes none of them; the point is to avoid freezing an
unsupported gate.)

### 2.3 The stationarity gate, and why 2 bpm (HR used 5)

**Chosen from FFT resolution, not tuned on radar error.** The FFT bin at a 30 s window is
**2.0 bpm** (1/30 Hz × 60), identical to HR. But the threshold differs from HR's 5.0 bpm, and the
reason is the reference's measured behaviour:

- HR set 5.0 bpm (= 2.5 bins) because genuine HRV of ~3 bpm is real physiology that must **not** be
  excluded — a 1-bin gate there would reject 60–70 % of windows.
- RRp's measured within-window (displayed) spread is **median 0–1.0 bpm** — smoothing has already
  suppressed sub-window variation. On the (approximate-origin) exploratory windows the
  **1-bin (2.0 bpm)** gate excludes **1 of 6 natural windows (~17 %) and 0 of the paced-16 (0/6) and
  sweep (0/16) windows**; a looser 2.5-bin gate (5 bpm) excludes **nothing anywhere** and is
  toothless. The gate therefore **bites where the displayed breathing rate varies (natural) and
  passes the stable paced sessions** — the desired behaviour, though on a very small, exploratory,
  single-subject `n` with approximate window alignment (§4).
- **What the gate actually observes.** 2.0 bpm = one FFT bin, so the gate flags a **displayed RRp
  transition of ≥ ~1 bin** inside the window. It is a **conservative RRp-display-change gate**, not
  a detector of biological breathing stationarity — smoothing does **not** "validate" a tighter
  physiological threshold. The 1-bin value is chosen because RRp's displayed spread is small
  (median 0–1 bpm), so a 1-bin gate has teeth (excludes ~17 % of natural, 0 % of paced/sweep
  windows — §1) where a 2.5-bin gate would exclude nothing; that is a property of the *displayed*
  trace, not proof about the underlying breathing.

**Sensitivity must be reported** (exclusion fraction at 2 / 3 / 5 bpm), so the choice is auditable.

**Limitation, declared (both directions).** Because RRp is smoothed and laggy, the gate errs **both
ways**: (i) *false inclusion* — a real within-window transition may be hidden and passed; (ii)
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
  18 bpm — only that the HR-specific mechanism does not carry over.)*

### 2.6 Natural vs paced reference strength (report separately)
- **Natural sessions:** radar BR is **compared with RRp only** — the weak case: smoothing hides
  non-stationarity and there is no independent target. Report natural-BR agreement with explicit
  caution and the word "compared with", never "validated against".
- **Paced sessions:** radar BR is compared with **RRp (measured reference) + the metronome
  (target-concordance cross-check)**. Stronger than natural, because a second, independent target
  exists — but still bounded by RRp's quality and by compliance, not a gold standard.

---

## 3. What this specification does NOT fix
- It does not correct RRp's smoothing/lag — those are **declared**, not removed.
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
  robustness is claimed. The script pins the thresholds and both consumed-input SHA-256 (the source
  CSV and `run_metadata.json`; cross-check `notes/capture_inventory.md`), and
  **imports no radar-pipeline code and reads no radar estimate** (it reads `run_metadata.json` only
  for the `start_wall_utc` timestamp).
- **All existing radar/RRp pairs are EXPLORATORY** (single subject; the sessions informed the
  method's design). No confirmatory BR number may come from them.

## 5. Risk and adequacy rule — decided at M5, prospectively
Whether RRp is an adequate BR reference is decided at the **M5 pilot**, not retrospectively on M6:
- **M5 defines the trigger**, before M6: a pre-specified, quantitative RRp-adequacy criterion
  (e.g. a bound on the median |RRp − metronome| offset and on the RRp lag across paced steps,
  measured on the pilot). "Systematically diverging" is made numeric there — it is not a usable
  trigger while undefined.
- If the M5 pilot fails that criterion, the plan **amends before M6** to
  **metronome-target-concordance for paced sessions and natural-BR exploratory-only**, and that
  amendment is **re-deposited before the confirmatory data it governs** (`analysis_prespec.md` §4,
  `plans/implementation_plan.md` §M5).
- **M6 may not retrospectively switch truth sources.** Once M6 data exist, the frozen (or
  M5-amended) RRp-primary analysis stands regardless of result. A data-triggered comparator switch
  on M6 would void the pre-registration and is forbidden.
