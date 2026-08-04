# Implementation Plan — mmWave Vital Signs (HR + BR)

> **Scope.** This is the **broad** plan overseeing the whole project. Each milestone below is
> sized to be executed independently and to get its own detailed plan file later
> (`plans/<milestone>.md`).
>
> Written 2026-07-24 against branch `vital_signs_v9c`, HEAD `b6f5b73`. Companion documents:
> `THIRD_CHAPTER.md` (thesis source), `JOURNAL_PAPER.md` (paper planning), `HISTORY.md` (log),
> `HANDOFF.md` (current state).

---

## Context

The project estimates heart rate — and now, co-equally, **breathing rate** — from a 77 GHz FMCW
radar (IWR1642BOOST + DCA1000EVM) for a seated subject 0.8–1.4 m away, validated against a Masimo
MightySat. Output: a PhD thesis chapter and a journal paper.

Why this plan exists now: the project has **mature infrastructure and immature evidence**.
~18,700 lines of code and **797 test outcomes (796 passed, 1 xfailed)** support **four raw captures
from one subject — only three of which carry a Masimo reference**. Ethics
approval is in hand, so the expensive, irreversible step — collecting 20 sessions from 10 people —
is unblocked and imminent. Two things must be true before that happens: the known-broken parts
must be fixed (breathing rate is currently on a **known-buggy path**, which matters much more now
that BR is a stated goal). **Pre-registration (M0) was removed from the project on 2026-08-03 by
user decision**, so no evaluation rule is frozen in advance and every agreement result is
exploratory/descriptive rather than confirmatory — see Track 0.

The plan also schedules first-ever real-data validation of the two KAUST reference papers in
`literature/ref_papers/`. Both are **simulation-only**, and both claim to solve the exact problem
this project is stuck on. That is the project's best available novelty and it is currently unused.

**Decisions taken (2026-07-24):** BR reference = Masimo `rr_bpm` cross-checked against the paced
metronome rate; Paper 2 implemented **staged** (joint-Doppler first, DOA later); both papers
implemented as an **offline comparison arm**, not production replacements; sequencing =
**fix blockers → pilot → study → method work**.

---

## Goal

**System goal.** Real-time HR *and* BR from radar, tracking the Masimo while seated at 0.8–1.4 m.

**Paper-grade goal.** Quantified agreement vs Masimo for **both** HR (vs `Beats / min`) and BR
(vs `Breaths / min` + paced rate): MAE, RMSE, Bland–Altman limits of agreement, **always reported
with coverage**, across 10 subjects, under a fixed comparator (`notes/comparator_prespec*.md` —
an internal spec, not a pre-registration; see Track 0).

**Method goal.** An evidence-based comparison of four estimators on the same data under the same
comparator: ECA+AHET (current), Harmonic Accumulation (Paper 1), joint-Doppler (Paper 2), and a
published-pipeline baseline. **ECA+AHET is the pre-declared primary estimator; the other three are
secondary and exploratory** unless promoted by a separate documented decision. The primary endpoint
and the comparison discipline are fixed in `notes/analysis_prespec.md` as an internal engineering
spec — otherwise one small dataset would be used to implement, tune, select *and* crown a winner.
That risk is real whether or not anything is pre-registered.

---

## Current state

### Done and tested — trust these

| Component | Evidence |
|---|---|
| Raw ADC I/O, 2-lane LVDS, `iq_swap` conventions | `src/radar_io.py`, `tests/test_radar_io_*.py` |
| Range FFT, static clutter removal, phase extraction + unwrap | `src/vitals.py`, `src/respiration.py` |
| ECA + AHET heart-rate core | `src/vitals.py`, 22 tests + 138 Step-6 tests |
| **Warmup bin lock + energy-eligibility fix** | committed `863600e`/`dfe7fb5`, 27 tests, 3× cross-reviewed, `scripts/validate_warmup_selection.py` |
| Masimo parser (epoch alignment, PI gate, dedup) | `src/masimo.py`, 9 tests |
| Windowing, quality mask, Step 5/6 pipelines | 209 + 34 + 15 + 138 tests |
| Live demo: capture, replay, raw mirror, diagnostics | `scripts/live_demo.py`, `diagnose_live_run.py` |
| **HR comparator pre-specification** | `notes/comparator_prespec.md` (applied, binding) |
| Full suite | **2028 passed, 5 skipped** (2026-07-30). Skips are honest absences, not passes: 4 are OSR-03 tests needing replay artifacts that no longer exist, 1 is elsewhere |
| Data | **8 live captures**, of which 3 are Masimo-referenced; all single-subject, all pre-freeze exploratory |
| M2 respiration-collapse fix | implemented and tested — band-edge veto + STFT-consistency gates (`src/respiration.py`). **Validation (#5) still open** |
| M8 Step 1b scientific core, suites, synthetic gate, bundle writer, registry, metrics | implemented, 2026-07-30; gate passes 14/14 in-process. **Not yet frozen as a bundle** |

### Built but never validated against a reference

- **Harmonic Accumulation BR estimator** (Paper 1) — the *primary* BR estimator in
  `src/respiration.py`, adapted from pulse-radar even-harmonics to all-harmonics phase. **Never
  compared to Masimo `rr_bpm`.**
- **BR end-to-end accuracy** — never measured, ever.
- **HR agreement** — pilot only: MAE 0.19/0.50/0.53 bpm, 0 severe, but n=1 subject, 10–46%
  coverage, exploratory (the sessions informed the method's design).
- **AHET criterion** — soft threshold (~1.0), no genuine rejection population.
- **linalg-free DSP path** (FFT masking replacing `filtfilt`; Gram–Schmidt replacing `linalg.qr`)
  — no independent cross-model review. Required before paper-grade, and therefore **a prerequisite
  of M4** (see M4), since M4 executes this path to produce every paper-grade number.

### Known broken / open

| Defect | Status |
|---|---|
| **Respiration collapse** — `f_r` pinned to the 6 bpm floor while `resp_valid` stayed `True` | **FIX IMPLEMENTED** (band-edge veto by bin identity + STFT-consistency gates, `src/respiration.py::resp_edge_veto`). Historical: 3-for-3 on the Masimo-referenced captures — `massimo1` (12 hops), `massimo2` (5), `sweep` (10); absent from the unreferenced `live_test1`. **Validation open (M2 #5)**: needs a frozen-comparator score, and no capture that can discharge it exists yet |
| ECA v1 (`skip_forbidden_harmonics_v1`) removes **0.00 dB** in-band at low f_r | **Same causal chain as the row above, not an independent defect.** With `f_r`=0.1 Hz, `k_max_eff = min(10, floor(2.0/0.1)) = 10`, so harmonics land at 0.1…1.0 Hz; k=8,9,10 fall inside the cardiac band and are skipped as forbidden, while k=1..7 sit below 0.8 Hz and cannot affect it. Net: production harmonic cancellation is inert, and HR degrades to a bare argmax over an uncancelled spectrum while every status flag reads healthy |
| Coverage 10–46% of windows yield an accepted HR | The real bottleneck; accuracy is not |
| Candidate ranking: magnitude order ≠ credibility order | Confirmed, n=1, no plan yet |
| Stage 1B lag-10 veto | Designed + scaffolded, **blocked**: zero baseline severe accepts exist post-bin-fix |
| `elapsed_s` is wall-clock in replay NPZ under `--replay-fast` | Unfixed; use `frame_idx / frame_rate_hz` |
| `guard_cardiac_candidate_v1` not promoted | `experiments/exp_eca_modes/config_guard_v1.yaml` now exists (the old "`experiments/` empty" blocker is cleared), but promotion still waits on evidence: the only real numbers are n≤2 per bucket, single-subject, `exploratory_non_frozen` |
| Canonical Step 1a bundle: 2 payload hashes are CRLF-era | Resolved as a documented erratum (`HISTORY.md`, 2026-07-30). Bundle deliberately unmodified per §6.1 |

### Not started

Paper 1 validation · Paper 2 (anything) · baselines (TI on-chip, published pipeline) ·
Bland–Altman on post-reset data · `figures/` · the 10-subject study.

---

## Milestone map

```
TRACK A — blockers.
  M1  live-chain smoke test ......  independent · UNSCORED engineering check
  M2  respiration-collapse fix ...  independent · offline, on existing captures
  M3  BR comparator spec .........  independent · reference-only design
  M4  offline evaluation harness .  needs M3 + the linalg-free DSP review

TRACK C — METHODS (current focus, 2026-08-03).  Offline, on the 8 existing captures.
  M8   Paper 1 — Ahmed et al., Harmonic Accumulation
  M9   Paper 2 — Kotte et al., joint high-amplitude-difference Doppler (uses the 4 RX
                 channels currently thrown away)
  M10  baselines (TI on-chip + published pipeline)
  M11  open defects: coverage, candidate ranking, Stage 1B
        │
TRACK B (data) — no longer gated
  M5  pilot 1–2 subjects
  M6  main study 10×3 (natural / paced / recovery)
  M7  collision capture · ethics CLEARED
        │                                │
        └────────────────┬───────────────┘
                         ▼
              M12  results, figures, chapter + paper
```

**Critical path (2026-08-03):** M8/M9 → M12. Track C is the current focus: establish whether either
published method recovers HR or BR on the captures we already have, before spending any more
subject time. Track B is available whenever wanted and is no longer gated by anything.

> **M0 (pre-registration freeze and deposit) was REMOVED from the project on 2026-08-03 by user
> decision — see "Track 0" below.** Its hard gate on study captures is gone with it. Every
> reference to a freeze, a deposit, a DOI or a "post-freeze" status elsewhere in this plan is
> obsolete; where such wording survives, the removal note governs.

---

## Track 0 — Governance · **REMOVED**

### M0 — Freeze and deposit the pre-registrations · **REMOVED 2026-08-03**

**Removed from the project by user decision, 2026-08-03**, on grounds of time cost. The
milestone is not deferred or postponed — it is not being done. Its **hard gate on study
captures is removed with it**: M5, M6 and M7 are no longer blocked by anything in Track 0.

**What this costs, recorded once so the paper does not overstate itself.** Nothing in this
project is pre-registered. The agreement results are therefore **exploratory / descriptive**,
not confirmatory, and must be written that way — no "pre-specified", "pre-registered",
"frozen before data" or "confirmatory" language anywhere in `JOURNAL_PAPER.md` or the thesis
chapter. Analysis decisions made after seeing data are legitimate; presenting them as though
they preceded it is not. This is the same discipline as CLAUDE.md §4, applied to claims about
timing rather than about numbers.

**What SURVIVES the removal — these are load-bearing and must not be deleted:**

| Document | What it is now |
|---|---|
| `notes/analysis_prespec.md` | internal analysis spec — the window grid, agreement model and evidence floor. `src/m4/window_grid.py` cites §7 as its authority and hard-errors against it. |
| `notes/comparator_prespec.md` | internal HR scoring spec — implemented by `src/comparator.py`. |
| `notes/comparator_prespec_br.md` | internal BR scoring spec (M3, cross-reviewed). |
| `notes/protocol.md` | the capture protocol. Still governs what is captured. |
| `notes/capture_inventory.md` | the capture record. |

They stop being *deposit artifacts* and continue as *engineering specifications*. The
frozen-grid invariants in code stay frozen: they exist so results stay comparable across runs,
which is an engineering property independent of pre-registration.

**Retired with M0:** `plans/m0_preregistration.md` and `plans/m0_b1_evidence_floor_memo.md`
(both marked RETIRED in place, not deleted — CLAUDE.md §9). The evidence-floor *values* remain
useful as a design target; they are simply no longer a commitment made in advance.

## Track A — Blockers (all four before any subject is recorded)

### M1 — Live capture chain smoke test · **UNSCORED**
**Goal.** Prove radar → live HR/BR → `adc_stream.bin` → diagnostics still works on hardware.
**Depends on:** nothing. **Do this first** — it is the only milestone that can fail for reasons no
amount of offline work will reveal.
**Status of its output:** this is an **engineering check, not data.** Its capture is never scored,
never promoted to paper-grade, and never counted as a study session (CLAUDE.md §4). That is why it
If it surfaces a protocol defect, that defect is fixed *in* the protocol, and dated in
`HISTORY.md`.
**Work.** Run `scripts/live_demo.py` live on yourself under the real protocol — **run the full
10 min**, so this doubles as the first check that the bin lock survives a long session. Verify with
`scripts/verify_live_demo_artifacts.py` + `scripts/diagnose_live_run.py` that all five artefacts
are written and the warmup locks a sane bin. Confirm the Masimo CSV export aligns on `epoch_utc`.
**Done when:** one self-capture produces a complete artefact set and a non-empty HR track; result
logged in `HISTORY.md`.
**Risk:** the live path has not run since 2026-07-14. Discovering breakage here costs an hour;
discovering it with a volunteer seated costs a session and goodwill.

### M2 — Respiration-collapse root cause and fix
**STATUS (2026-07-30): done-when 1–4 CLOSED; only #5 remains, and it is not discharegable with
current data.** The fix is implemented — band-edge veto by bin identity plus STFT-consistency gates
on every STFT-dependent branch (`src/respiration.py`, `resp_edge_veto`). What is open is the
*validation*: #5 requires scoring reprocessed BR under the frozen M3 comparator, and approximate
time alignment cannot produce a frozen-comparator outcome. **No capture capable of discharging it
exists yet**, so M2 is blocked on data (M1/M5), not on further DSP work. Do not re-open the fix.

**Goal.** BR cannot be a project goal while its estimator silently fails on every capture that has
a reference to fail against (3-for-3).
**Depends on:** nothing (offline, uses existing captures).
**Work.** Reproduce on the **three Masimo-referenced captures** (`massimo1`, `massimo2`, `sweep`);
instrument `src/respiration.py` (`fft_estimate_rr`, `ha_estimate_rr`, `fuse_estimates`) to dump the
pre-fusion candidates; determine whether the collapse originates in the FFT branch, the HA branch,
the fusion, or the `[0.10, 0.50] Hz` band edge. Fix, and **fix the validity flag** so `resp_valid`
cannot be `True` for a floor-pinned estimate. Cross-model review (CLAUDE.md §6 — this touches
peak-picking).
**Done when — note carefully that none of these is an accuracy win:**
1. The **root cause is documented** in `HISTORY.md` — which branch (FFT, HA, fusion, or the
   `[0.10, 0.50] Hz` band edge) produces the floor-pin, and why.
2. **Validity semantics are correct**: a floor-pinned `f_r` can never be reported `resp_valid:
   True`, with a regression test pinning it.
3. **Intermediates are dumped** for the pre-fusion candidates, so a future collapse is diagnosable
   (cross-cutting rule 3).
4. **All four captures reprocess** without the false-confidence flag; suite green.
5. **M4 reports the BR outcome under the frozen M3 comparator on the three referenced captures** —
   whatever that outcome is.

**A negative result closes this milestone.** If the root cause turns out to be that the
all-harmonic HA adaptation is invalid — which M2's own Risk anticipates and which would fold into
M8 — then "BR does not transfer" is the finding, it is recorded, and M2 is **complete**. Requiring
BR to *agree* with `rr_bpm` before M2 can close would leave only one exit: tune until it agrees.
That is precisely what CLAUDE.md §4 forbids and what cross-cutting rule 5 exists to protect. The
unreferenced `live_test1` is an **engineering/reprocessing check only** — it can show the collapse
is gone, but with no reference it cannot establish BR plausibility and must never be cited as
accuracy evidence.
**Risk:** may reveal the HA adaptation itself is wrong — which would fold into M8.

### M3 — BR comparator pre-specification + Masimo RRp quality gate
**Goal.** Decide *in advance* how radar BR is scored, exactly as was done for HR.
**Depends on:** nothing. **Explicitly NOT M2.**
**The comparator is designed from the reference alone.** Deriving the RRp eligibility rules by
"sanity-checking them against a working estimator" would tune *reference admissibility* on *radar
agreement* — the mirror image of the tuning CLAUDE.md §4 forbids, and it would void the spec's
independence before it was even frozen. The HR comparator is the model to copy: every gate in
`notes/comparator_prespec.md` §§2.1–2.3 (PI, sample coverage, stationarity) is a property of the
Masimo trace or of FFT resolution, and **not one of them refers to radar output.**
**Work.** Write `notes/comparator_prespec_br.md` mirroring the HR spec: reference = median Masimo
`rr_bpm` over the same 30 s window; a **quality gate for RRp** (it has no PI equivalent — derive
one from the *reference's own* availability/variance behaviour, and state honestly that Masimo RRp
is pleth-derived, smoothed and laggy, so it is a weaker gold standard than PR); a stationarity gate
derived from FFT resolution; non-overlapping windows; coverage reported alongside accuracy. Specify
the **paced cross-check**: in paced sessions compare against the commanded metronome rate and
report both. Permissible design evidence is the Masimo traces, the metronome command, and
resolution arithmetic — **radar–reference agreement is not admissible evidence here.**
**Done when:** spec written, cross-model reviewed, and settled **before M4
computes any BR agreement number**. All existing radar/reference pairs are declared **exploratory**
in the spec itself.
**Risk:** RRp may prove too weak to validate against — if so, the plan pivots to metronome-only
truth and that limitation is declared, not hidden.

### M4 — Offline evaluation harness (HR + BR)
**Goal.** One tracked script that turns a run folder into paper-grade numbers, for both vitals.
**Depends on:** M3, **and the linalg-free DSP cross-model review** (below).
**Prerequisite — the linalg-free DSP review is a gate on M4, not housekeeping.** The FFT-masking
replacement for `filtfilt` and the modified-Gram–Schmidt replacement for `np.linalg.qr` have never
had an independent cross-model correctness pass, and CLAUDE.md §6 requires one for any diff
touching filtering. M4 is the path that produces **every** paper-grade number, and it executes that
path — so the review must clear **before M4's output is trusted**, hence before M5, M8, M9 and M10.
It was previously parked in M11d, behind the milestones that would already have consumed it.
**Work.** A single entry point that: reprocesses `adc_stream.bin` offline, applies both comparator
specs, emits MAE/RMSE/bias/**Bland–Altman** and coverage, on **non-overlapping 30 s windows**, per
subject, writing to `results/<experiment>/<timestamp>/` with config + seed + git commit + input
SHA-256. Reuse `src/compare.py`, `src/windowing.py`, `src/masimo.py`. Include the stationarity
sensitivity table (3/5/8 bpm). Fix the `elapsed_s` wall-clock bug or make the harness immune to it
by using `frame_idx / frame_rate_hz`.

- **Bland–Altman must implement `notes/analysis_prespec.md` §1's repeated-measures model**,
  clustered by subject.
  `scripts/plot_bland_altman.py` pools every window as an independent pair
  (`se_loa = sqrt(3·SD²/n)`, `n` = pooled windows, no subject or session term). That is wrong for
  10 subjects × 2 sessions, and it was never right for the one-subject pilot either — its output
  there is **descriptive only**, not a limit of agreement. **Reuse it for plotting only**, with its
  statistics replaced or bypassed.
- **Distance is descriptive, not inferential.** `notes/protocol.md` deliberately does not pin the
  distance — it records it and lets the warmup auto-lock anywhere in 0.8–1.4 m — so there are no
  predefined, replicated distance strata and a per-distance *comparison* would be confounded with
  subject. Report distance as recorded **exploratory metadata** (e.g. error against measured
  distance), which is what CLAUDE.md §1's "report per distance" requires. **A per-distance
  agreement claim is not an acceptance criterion of this plan.**

**Done when:** running it on the existing 3 Masimo captures reproduces the known pilot numbers
(0.19/0.50/0.53 bpm) exactly, plus BR numbers for the first time; unit-tested; and the linalg-free
review is closed. **The BR outcome is reported whatever it is** — a non-transfer or a negative
result is a valid, reportable output of this harness, not a failure of it (see M2).
**Risk:** low. This is the single most reusable artefact in the plan — every later milestone
reports through it.

---

## Track B — Data collection

### M5 — Pilot: 1–2 subjects · **EXPLORATORY**
**Goal.** Validate protocol, BR reference, settle criterion and bin lock on people who are not you,
**before** committing 20 sessions.
**Depends on:** M1, M2, M3, M4. **No longer gated by Track 0** (M0 removed 2026-08-03).
**Status of its data:** the pilot is **still excluded from the M6 metrics**, and the reason is
unchanged by M0's removal: its whole purpose is to expose defects that will change the protocol,
and data that is allowed to change the rules cannot also be evidence under them. That argument
never depended on a deposit.
**Work.** Full protocol per `notes/protocol.md`: seated, 0.8–1.4 m, **10 min**, natural + paced
arms, settle criterion enforced. Record distance, time of day, resting HR, and **|HR − 4·f_r|**
margin.
Run M4's harness immediately on each session.

**Change discipline (this is what makes "fix it before M6" legitimate).** Any pilot-driven change
to `notes/protocol.md` or to either comparator must be, *before M6 starts*: dated in `HISTORY.md`
with its rationale; cross-model reviewed where CLAUDE.md §6 applies; and applied **prospectively
only** — never retro-fitted to already-collected data. The public re-deposit step is gone with M0;
**the prospective-only rule is not**, and it is the part that actually protects the result. An
amendment that alters what a participant is asked to do must clear ethics before it is used
(see M7).

**Feasibility test — run this before M6 is scheduled.** Compute, on the pilot sessions, the number
of **evaluable windows** (radar-accepted *and* comparator-admissible, non-overlapping) actually
obtained per session, and compare against the `notes/analysis_prespec.md` §2b evidence floor. If
the pilot cannot clear it,
M6 as specified will not either, and the response is decided **now**, not after 20 sessions: add
sessions per subject, or reduce the claim (recording length is already at the 10-min ceiling).
**Run the pilot at the full 10 minutes**, so its yield measurement actually predicts M6's — and
**check the locked bin still tracks the chest at minute 9–10**, which is the specific risk the
longer session introduces.
**Done when:** ≥2 sessions pass end to end; bin lock behaves on new body types; BR reference is
usable; the evaluable-window yield is measured against the §2b floor; any protocol defect is fixed
and dated **before** M6. Logged in `HISTORY.md`.
**Risk:** the −12 dB bin-lock threshold is a scene-scoped prior from one subject — this is where it
gets its first real test.

### M6 — Main study: 10 subjects × 3 sessions · **EXPLORATORY** (nothing is pre-registered)
**Goal.** The evidence base the thesis and paper are missing. **This is the first confirmatory
capture set** — the headline agreement numbers come from here and nowhere earlier.
**Depends on:** M5 clean.
**Work.** 20 sessions on different days; paced rates 12/15/18 bpm rotated (~3–4 subjects each);
**recordings are the frozen 10 minutes — do not shorten** (600 s → ~19 independent windows after
warmup; at 5 min you get ~9, and at 10–46% coverage that leaves too little to score).
Log every session in `HISTORY.md` and `data/manifest.local.csv`.

> **Recording length: SET TO 10 MINUTES** (decided 2026-07-24; `notes/protocol.md` updated the
> same day, *before* the deposit, so this is frozen protocol rather than a later amendment). The
> approval permits up to 10 minutes and **that ceiling must not be exceeded**.
>
> **The risk it introduces, and who checks it:** the warmup locks **one range bin for the whole
> session**, so a posture shift late in a longer sit silently corrupts the tail — the same class of
> failure as the 2026-07-14 mislock. **M5's pilot must confirm the locked bin still tracks the
> chest at minute 9–10** (`scripts/diagnose_live_run.py`); if it does not, the duration comes back
> down and the evidence floor has to be met another way. Storage: ~1.55 GB/session, ~31 GB for the
> study.
**The completion criterion is evidence, not sessions.** "20 sessions captured" is a schedule, not a
result. Windows available per session, after the ~30 s warmup, at 30 s non-overlapping:

| recording | windows/session | ×2 sessions, at 10–46% coverage | per subject after Masimo gates (−12–20%) |
|---|---|---|---|
| 5 min (former protocol) | ~9 | 1.8 – 8.3 | ~1.5 – 7 |
| **10 min — ADOPTED 2026-07-24** | **~19** | **3.8 – 17.5** | **~3 – 14** |

At 5 minutes the pessimistic end was roughly **two** usable windows per subject, which would not
support per-subject agreement, let alone clustered Bland–Altman limits. Ten minutes roughly doubles
the evidence at no extra recruitment cost and sits at the approval ceiling, so **this lever is now
spent** — it cannot be pulled again. M6 completes against the §2b **evidence floor and precision
target**; if the floor is still missed, the remaining levers are **add sessions** or **reduce the
claim**. Not renegotiated after the fact.
**Done when:** 20 sessions captured, all with raw streams; M4 run over the full set; **and the
evaluable-window yield meets the §2b floor**, or the shortfall rule has been applied and
logged.
**Risk:** the **18 bpm arm is deliberately inside the failure zone** — report it separately, never
pooled into headline metrics.

### M7 — Collision-provoking confirmatory capture
**Goal.** Give the central limitation (4·f_r ≈ HR) direct, current evidence, and unblock Stage 1B.
**Depends on:** M6 (needs a subject whose resting HR is known).
**Ethics scope: CLEARED.** The user confirmed on **2026-07-24** that the existing approval covers
the subject-specific collision manoeuvre. Recorded on the user's authority — no one in this repo
has read the approval document. Two follow-ups remain open: the
**approval reference number and issuing board** (still unrecorded, and required for the Methods
section), and confirmation that the paced rates M7 actually selects stay inside whatever range the
approval names.

> **Still open, and it is a design problem rather than an ethics one.** M7 picks the paced rate
> from the subject's **resting** HR, but pacing *moves* HR — `notes/protocol.md` records this
> subject going from ~65 to 72 bpm, and the sweep session pushed them to 80–88 bpm, which is why
> its collision landed on the **21 bpm** step rather than the designed 18 (`HISTORY.md`,
> 2026-07-14). A rate computed from resting HR will therefore often miss the collision it was
> chosen to create. **M7's derived plan must solve this** — e.g. measure HR *during* a short paced
> warm-up and set the rate from that, or sweep a narrow bracket around the predicted rate rather
> than committing to a single value.

**Work.** Design a capture that *engineers* the collision — choose the paced rate per subject so
4·f_r lands on their HR **as measured while paced**, not at rest — rather than reusing the stepped
sweep and hoping. This is what unblocks Stage 1B's empty severe-accept population, and it is also
the fixture for M8's collision claim.
**Done when:** ≥1 session with a sustained, documented |HR − 4·f_r| < 5 bpm.

---

## Track C — Method comparison (offline; starts now on existing captures)

All Track C milestones follow the project's **synthetic-control-first discipline**: synthetic
controls proving the maths before any real-data claim. **This rule survives M0's removal** — it is
a method rule about not believing your own implementation, which never depended on
pre-registration. All are scored through M4's harness under the fixed comparators,
as an **offline comparison arm** — no production promotion without a separate documented decision.

### M8 — Paper 1: Harmonic Accumulation (Ahmed et al., DOI 10.1109/TRS.2024.3412915)
**Goal.** First real-data validation of a simulation-only method, and a test of its most valuable
claim.
**Depends on:** M2, M4. **Can start immediately on existing captures.**

> **Status clarification (2026-07-30).** Step 1a is implemented and canonicalized, with status
> `not_reproduced_under_declared_assumptions`; it has not run on any real capture. The
> decision-complete Step 1b authority is now
> `plans/m8_step1b_ahmed_transfer.md` (SHA-256
> `9294cb0589b9f0d8f50cdfa0ea893862b1f8ac7f26eb6fee31ee57d622da33ac`), passed on those
> exact bytes by architecture, mathematical, Python, testing, and adversarial reviewers.
> Implementation still requires explicit user approval. Preserve the reviewed ordering: implement
> and fixture-test the complete synthetic/runner/scorer system without opening real inputs; execute
> the clean synthetic transfer gate; only then, with a comprehensive pre-data authorization, run
> the explicitly exploratory arm on `demo_massimo1` through `demo_massimo7` and `demo_sweep`.
> Adapter-record compatibility from Step 1a remains neither a real `WindowEstimator` nor
> `score_offline.py` integration.

**Work, in order:**
1. **Two synthetic controls, in this order — together the go/no-go.** The paper's Fig. 8(c)–(d)
   claims HA estimates both rates correctly *when the 4th breathing harmonic equals the heartbeat*
   (f_h = 80/60 Hz, f_b = 20/60 Hz, f_c = 6.7 GHz, d_h = 10 mm, d_b = 20 mm, five breaths sampled,
   SNR 10 dB).
   - **1a — Reproduce the paper on its own terms.** Implement the paper's signal model (Fig. 1: a
     **pulse radar with a single TX and single RX**), its harmonic indexing (the spectrum is built
     on **2f_h and 2f_b and their harmonics** — the even-harmonic structure is intrinsic to its
     demodulated model, not an incidental choice), its parameters and its success metric, and
     reproduce Fig. 8(c)–(d). **This is the control that makes the next step interpretable.**
     Without it, a failure at step 1b cannot be told apart from a bug in our own implementation,
     and a success is not a reproduction of anything the paper claimed.
   - **1b — Then change the signal mapping.** Use the project's unchanged coherent locked-bin
     `delta_before_mean` phase extraction with the same sinusoidal breathing/heartbeat displacement
     control. Ideal extraction leaves the two displacement fundamentals; it does not preserve the
     phasor's Bessel/mixed harmonics. The registered primary is therefore
     `phase_fundamentals_only_transfer_v1`: the fixed-\(H\) accumulator still evaluates
     \(q,2q,\ldots,Hq\), with \(q=f\), `bpm=60*q`, and strict \(Hq<f_s/2\) support.
     **Label this result an adaptation, never a reproduction or general robustness proof.** A
     negative result is a valid endpoint.
2. **After a promotion-eligible synthetic gate and one frozen pre-data authorization, validate HA
   as BR and HR estimators** against the unchanged Masimo comparators on all eight existing
   captures. A negative gate requires an additional user-approved continuation rationale; it
   cannot be silently treated as passed.
3. Run production and all six Ahmed \(H\)/suppression arms on identical non-overlapping windows and
   both separately reported lock estimands. Retain `k=0` as lock-selection-in-sample diagnostics;
   use `k>=1` as the only comparative accuracy universe. Keep all results
   exploratory/apparent, approximate-origin, protocol-stratified, and ineligible for production
   promotion.
4. Optionally reproduce the paper's CRLB as a benchmark.
**Done when:** **both** synthetic verdicts documented (1a reproduction, 1b adaptation, reported
separately); and, if the gate/authorization chain permits real continuation, immutable radar-only
and scored bundles contain complete BR/HR coverage and agreement evidence for every declared arm
without a winner-selection claim.
**Risk / why it matters:** this addresses the project's central unsolved problem using a method
already half-implemented in the repo. Cheapest high-value milestone in the plan.

### M9 — Paper 2: joint high-amplitude-difference Doppler (Kotte et al., DOI 10.1109/TRS.2024.3352189)
**Goal.** Exploit the 4 RX channels currently thrown away, via the paper that targets exactly the
lung-masks-heart problem.
**Depends on:** M4. **Synthetic work can start immediately; no existing capture is touched until
the controls below have written pass/fail criteria.**

**Correction — the "SIMO setup the paper assumes" claim was false, and it mattered.** The paper's
§IV simulates **one TX and 20 RX** at 24 GHz, 50 ms PRI, `N_c = 16`. Our 1 TX / 4 RX is *not* the
configuration it assumes, and the gap is not cosmetic:

- Eq. (23) defines `Y_t(κ) ∈ C^(N_c×n_R)`; eq. (25) forms `w = R_t⁻¹[a(f₁) a(f₂)]H⁻¹[1 1]ᵀ`, where
  `R_t = E{Y_t(κ)Y_tᴴ(κ)}` is **N_c × N_c**. The **RX channels supply the snapshots** for that
  covariance. With `n_R = 20` and `N_c = 16`, `R_t` can reach full rank. **With `n_R = 4`,
  `rank(R_t) ≤ 4`, so `R_t` is singular and `R_t⁻¹` does not exist.**
- The slow-time mapping is unresolved and makes it worse. The paper's `N_c = 16` chirps at 50 ms
  PRI is a **0.8 s** CPI. Our vital-signs slow time is the 20 Hz *frame* axis over a 30 s window —
  600 samples. Estimating a 600×600 covariance from 4 snapshots is not a hard problem, it is an
  undefined one. Whether slow time is the 32 chirps within a frame or the frames themselves is a
  decision this milestone must make explicitly, not inherit.
- The paper's motivation *is* ours (lungs masking the heart within one range bin, §I), but its
  **validation is not**: Fig. 5 estimates generic moving-target Dopplers (−1/−2, −1/4, 1/2.5 Hz).
  There is no HR/BR validation in it to inherit.

**Work — control 1 (do first, synthetic only):** reproduce the paper faithfully at **1 TX / 20 RX,
`N_c = 16`**, on its own target set and metric. This establishes the implementation is right before
anything is asked of it.
**Work — control 2 (registered ablation):** reduce to 4 RX and measure where it breaks. This must
specify **in advance**: how `R_t` is estimated, the regularisation/diagonal-loading used to make it
invertible, an explicit **rank check**, the RX calibration assumed, and which axis is slow time.
Pass/fail criteria are written before it runs.
**Work — Stage A (only after both controls):** the amplitude-independent **joint f_r/f_h
estimator** on the already-locked range bin, no DOA. Verify on synthetic two-Doppler signals with a
large amplitude ratio (the lung/heart case), then run on existing captures through M4.
**Work — Stage B (only if A shows promise):** add Capon DOA / spatial combining across the 4 RX.
First verify the IWR1642BOOST RX spacing is λ/2 from the board user guide, and budget for array
calibration.
**Done when:** both controls have documented outcomes, Stage A has a synthetic control pass +
real-data agreement numbers, and there is a documented go/no-go for Stage B.
**Risk — now the leading one:** this method may not transfer to a 4-RX board at all. That is a
legitimate and reportable finding, but it must be discovered on synthetics, not misdiagnosed as a
null result on our captures. The paper also assumes higher SNR and does not address a 20 Hz
slow-time rate.

### M10 — Baselines
**Goal.** The most common rejection reason for a paper like this is having no baseline.
**Depends on:** M4.
**Work.** (a) TI on-chip vital-signs output — needs the TI application-note reference (currently
`[CITATION NEEDED]`) and a way to log the chip's own estimate; (b) one reimplemented published
phase-based pipeline. Score both through M4.
**Done when:** head-to-head table vs ECA+AHET on the same windows.

### M11 — Open algorithm defects
Independently executable, each needs its own plan + cross-model review before code:
- **M11a — Coverage** (highest value): AHET conservatism at the correct bin yields only 10–46%.
  Characterise where windows die, then decide whether to loosen gates or accept and report.
- **M11b — Candidate ranking:** rank by credibility, not raw magnitude. Confirmed bug, n=1.
- **M11c — Stage 1B lag-10:** unblocked only by M7. Complete §7(c) item (i), cross-model review the
  numeric content, then fit and freeze the threshold on exploratory data.
- **M11d — Housekeeping:** create `experiments/exp_eca_modes` to unblock
  `guard_cardiac_candidate_v1`. *(The cross-model review of the linalg-free DSP path has been
  **moved out of housekeeping and made a prerequisite of M4** — it gates every paper-grade number,
  so it cannot sit behind the milestones that consume it.)*

---

## Track D — Output

### M12 — Results, figures, chapter, paper
**Depends on:** M6 + whichever of Track C lands.
**Work.** Generate all figures from committed scripts in `figures/` (never hand-edited) — the
mislock figure, the comparator figure, Bland–Altman, coverage/accuracy trade-off, the collision
arm. Promote `[PENDING]` → `[VERIFIED]` in `THIRD_CHAPTER.md` **by re-measuring, never by editing
tags**. Fill every `[CITATION NEEDED]`. Decide Paper A (methodology) vs Paper B (system) per
`JOURNAL_PAPER.md` §3.4 once the numbers are visible.

---

## Cross-cutting rules (apply to every milestone)

1. **Synthetic-control-first** — a synthetic control proving the maths before any real-data claim.
2. **Cross-model review** for anything touching range-FFT, phase extraction, filtering,
   peak-picking, or the Masimo parser (CLAUDE.md §6).
3. **Every estimate leaves evidence** — dump intermediates so a wrong reading is diagnosable.
4. **Report coverage with accuracy, always.** Non-overlapping 30 s windows only.
5. **Negative results are recorded, never deleted.** `HISTORY.md` append-only; `HANDOFF.md`
   rewritten each session.
6. **No agreement number outside the frozen comparators**, and no *confirmatory* agreement number
   from exploratory data — the eight existing captures and the M5 pilot are exploratory by
   construction (they informed, or are allowed to change, the rules).
7. **Live readouts are never paper-grade** — metrics come from offline reprocessing of
   `adc_stream.bin`.

---

## Verification

- **Per milestone:** `conda run -n radar-vitals python -m pytest tests/ -q` stays green
  (currently **796 passed, 1 xfailed** — 797 outcomes; the xfail is a known design hole, not a
  pass); new behaviour ships with tests.
- **M4 is the acceptance harness** for every method milestone — same script, same comparator, same
  window rule, so M8/M9/M10 results are directly comparable to ECA+AHET.
- **End-to-end check:** a run folder → M4 → MAE/RMSE/Bland–Altman/coverage for HR *and* BR, with
  config, seed, git commit and input SHA-256 logged to `results/`.
- **Regression guard:** M4 must reproduce the known pilot HR numbers (0.19 / 0.50 / 0.53 bpm,
  0 severe) on the three existing Masimo captures before it is trusted on new data.
- **Gate on paper-grade output:** no agreement number is trusted until the linalg-free DSP path has
  passed cross-model review (M4 prerequisite), and no Bland–Altman limit from the study is quoted
  from the pooled-independent statistics in `scripts/plot_bland_altman.py`.
- **Evidence, not schedule:** M6 is verified against the `notes/analysis_prespec.md` §2b
  evaluable-window floor, not against the session count.

---

## Immediate next actions

> **Refreshed 2026-08-03.** M0 was removed from the project by user decision; the focus is now
> Track C — establishing whether either published method recovers HR or BR on the eight captures
> that already exist, before any further subject time is spent.

1. **M8 — Paper 1 (Ahmed, Harmonic Accumulation) on real data.** Step 1a is done and negative;
   Step 1b is built through the synthetic gate and **has never opened a real capture**. The
   remaining build is the runner's decode/dispatch loop against a synthetic capture fixture plus
   the production serializer, then the real-data arm on the eight captures. **This is the shortest
   path to an answer** — the plan already calls it the cheapest high-value milestone, and its
   target (breathing harmonics masking the heartbeat) is exactly the failure the 2026-07-31 audit
   measured.
2. **M9 — Paper 2 (Kotte, joint high-amplitude-difference Doppler).** Not started. Uses the **4 RX
   channels the current pipeline throws away**, and targets the lung-masks-heart problem directly.
   Synthetic controls first, per the Track C rule.
3. **M2 done-when #5** — the only open part of M2. Needs a BR score under the fixed comparator on a
   capture with non-approximate time alignment. Still blocked on data, not DSP. Do not re-open the
   fix.
4. **M1** — live smoke test. Hours, no dependencies, unscored. No longer gated by anything, and now
   also the cheapest way to unblock item 3.
5. **M5 pilot** — available whenever wanted; no longer gated.

Also start early, since it gates more than it looks like it does: the **linalg-free DSP cross-model
review** — a prerequisite of M4, therefore of M8/M9/M10.

**Two documentation debts left open by M0's removal**, both cheap and both capable of embarrassing
the paper if forgotten:
- `JOURNAL_PAPER.md` and `THIRD_CHAPTER.md` must carry **no** "pre-registered", "pre-specified",
  "frozen before data" or "confirmatory" language about this study's own results.
- `notes/analysis_prespec.md` still carries a PENDING banner for the 3-arm recovery change and its
  arm-specific evidence-floor question. It is now an internal spec rather than a deposit, so the
  urgency drops — but the floor question still decides what M6 can claim.
