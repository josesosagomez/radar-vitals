> # RETIRED 2026-08-03
>
> **M0 (pre-registration freeze and deposit) was removed from the project by user decision.**
> This file is kept, not deleted, so the reasoning behind the evidence floor and the deposit
> design stays recoverable (CLAUDE.md §9). **Nothing in it is a commitment.** No deposit exists,
> no DOI exists, and no result in this project is pre-registered.
>
> The parts that remain useful are the *analytical* ones — the evidence-floor reasoning and the
> shortfall rules — which live on as design targets in `notes/analysis_prespec.md` §2a/§2b. The
> parts about freezing, depositing, timestamps and amendment DOIs are dead.
>
> See `plans/implementation_plan.md` "Track 0" and `HISTORY.md` 2026-08-03.

# B1 — Evidence-floor decision memo (M0)

> **Purpose.** Give the user the measured coverage picture (A2) plus the yield arithmetic so the
> **evidence floor** can be chosen *before* the deposit freezes — a floor chosen after seeing pilot
> yield is not a floor (`plans/implementation_plan.md` §M0). **This memo does not decide the floor;
> it frames the decision.** The chosen floor is written into `notes/analysis_prespec.md` §2.
> Prepared 2026-07-25.

---

## 1. A2 coverage characterization — measured, no algorithm changes

Source: the post-fix re-scoring `live_estimates.csv` (corrected bin) for the three Masimo
sessions. Per-hop `candidate_rejection_reason` was already recorded by the pipeline — no estimator
code was run. Timebase from `frame_idx / 20 Hz` (the `--replay-fast` `elapsed_s` is wall-clock and
unusable — HANDOFF §5). Non-overlapping 30 s windows = the hop nearest each 30 s boundary.

| session | per-hop accept | **non-overlap 30 s: windows** | **accepted** | dominant reject reason |
|---|---|---|---|---|
| natural (massimo1) | 5/51 = **10 %** | 6 | **0** | `ratio_db_low` |
| paced 16 (massimo2) | 23/51 = **45 %** | 6 | **3** | `low_candidate_floor_db_low` |
| sweep | 30/151 = **20 %** | 16 | **3** | `ratio_db_low` |

The per-hop column reproduces HANDOFF's 5/23/30 AHET-accepted counts exactly (consistency check).

### The finding that matters
**"10–46 % coverage" is a per-hop figure. At the non-overlapping 30 s cadence the comparator
actually scores on, the yield is far worse:** natural gives **0** accepted windows, paced16 and
sweep give **3 each — before any Masimo gate is applied.** The AHET reject reasons are almost
entirely evidence-strength gates (`ratio_db_low`, `*_floor_db_low`), i.e. the cardiac peak does not
clear the AHET floor at the correct bin — this is the coverage bottleneck, not accuracy.

Two caveats, both material:
1. **The non-overlapping-window selection rule is itself unfrozen and swings the count.** Picking
   the hop *nearest* each 30 s boundary (used above) can miss an accepted hop sitting a few seconds
   off-boundary. A "greedy: keep all accepted hops ≥30 s apart" rule would raise natural from 0 to
   ~1. **A4 must freeze this rule** (`notes/analysis_prespec.md` §7-window selection) — it is a
   pre-registration decision, not an implementation detail.
2. **Masimo gates further reduce.** The HR comparator excludes ~12 % (natural) / ~20 % (paced) of
   windows for non-stationarity/PI (`notes/comparator_prespec.md` §2.3). Applied on top of the
   counts above, evaluable windows shrink again.

## 2. Yield arithmetic for the 10-subject study (M6)

From `notes/protocol.md`: 10 min recording → ~19 non-overlapping 30 s windows/session after warmup.
Scaling the **non-overlap accept rates measured above** (0 %, 50 %, 19 %) and then the Masimo gate:

| per-subject (2 sessions × ~19 windows) | at 19 % accept | at 50 % accept | after −12–20 % Masimo |
|---|---|---|---|
| optimistic (paced-like) | — | ~19 | ~15 |
| realistic (sweep-like) | ~7 | — | ~6 |
| pessimistic (natural-like) | **~0–2** | — | **~0–2** |

The natural arm is the risk: on this one subject it produced **zero** evaluable non-overlapping
windows. If that transfers to the study, the natural session contributes nothing to per-subject
agreement, and the whole natural endpoint rests on between-subject variation alone.

## 3. The decision — three candidate floors (user picks one)

Each names a **per-session floor**, **per-subject floor**, **precision target**, and a
**prospective miss rule** (levers: the 10-min duration is already spent at the approval ceiling; add
sessions is a lever **only if `24IBEC051` permits >2 sessions/subject** — A5(a), still to confirm;
otherwise the only lever is a weaker claim).

| | **Option A — conservative** | **Option B — moderate** | **Option C — attack coverage first** |
|---|---|---|---|
| per-session floor | ≥1 evaluable window | ≥3 evaluable windows | set after M11a |
| per-subject floor | ≥4 (across 2 sessions) | ≥8 | set after M11a |
| precision target | LoA CI half-width ≤ 5 bpm | ≤ 3 bpm | ≤ 3 bpm |
| miss rule | drop subject's natural arm to descriptive; report paced only | add a 3rd session (needs A5(a)) or weaken claim | — |
| pro | almost certainly met; freezes now | publishable per-subject agreement | strongest primary estimator |
| con | very weak natural endpoint | may be unmet → forces more sessions | delays freeze; more tuning on n=1 |

**Recommendation: Option A**, consistent with the user's 2026-07-24 "characterize-then-freeze"
decision — freeze a floor that the measured yield can clear, publish coverage honestly alongside
accuracy, and pursue coverage (M11a) as a *post-freeze* secondary rather than delaying the gate.
Option A's risk is a thin natural endpoint; that is a **reportable limitation**, not a hidden one.

**Open sub-decision carried with this:** whether to attack coverage (M11a) before freezing so an
improved estimator is the pre-registered primary. The user chose characterize-then-freeze on
2026-07-24; the A2 numbers above are the "characterize" step. Option C remains available if the
natural-arm zero is judged unacceptable.

## 4. What the user must decide here
1. **Pick a floor option (A / B / C)** — or state a different floor. Written into
   `notes/analysis_prespec.md` §2 before freeze.
2. **Confirm A5(a):** does `24IBEC051` permit >2 sessions/subject? (Only needed if the miss rule
   uses the add-sessions lever.)
