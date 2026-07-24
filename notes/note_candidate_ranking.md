# Note — AHET candidate selection ranks by magnitude, not credibility

> **Status: DRAFT. NOT IMPLEMENTED. Awaiting cross-model review (CLAUDE.md §6).**
> This is a **separate question from the ECA forbidden-zone work** (`notes/plan_eca_forbidden_zone.md`).
> ECA decides which harmonics get cancelled *before* candidate search; this note is about what
> happens *after* — when multiple candidates survive AHET's per-candidate gates, which one wins.
>
> **Evidence scope, stated plainly up front: this is n=1.** One hop, one session, one subject.
> It demonstrates the mechanism is *real*, not hypothetical. It does **not** establish how often
> it matters, and my own first proposed fix does not work on this example either (§3). Do not
> over-read this note as more than it is.

---

## 1. The mechanism (grounded in code, not inferred from data)

`src/vitals.py:829-836`, the `strict_v1` post-loop selection:

```python
passed_ranks = [r for r in range(AHET_MAX_CANDIDATES) if candidate_passed[r]]
if passed_ranks:
    return _build_accepted(passed_ranks[0])
```

Candidates are pre-sorted by **descending spectral magnitude** before this point (the earlier
`sort_order = np.argsort(band_spec1[peaks_local])[::-1]` step). So `passed_ranks[0]` is: *iterate
candidates loudest-first, return the first one that clears every AHET gate.*

**This selects the loudest candidate that passes, not the most credible one.** A quieter
candidate with much stronger 2nd-harmonic evidence, sitting at rank 1 or 2, is never reached once
rank 0 passes — even though `_build_accepted` never compares the two.

This is exactly the mechanism named (but not observed) during the ECA v1 post-mortem
(`plan_eca_forbidden_zone.md` §10.3): *"the true heart passed with 33 dB peak-to-floor and was
discarded in favour of a decoy that passed with 4.4 dB... magnitude order is not credibility
order."* That was a synthetic example. This note is the first time it has been seen in real
captured data.

## 2. The one clean example

Sweep capture, hop 124, t=402s (the paced-21 bpm step, where 4·f_r ≈ 84.7 bpm and the true HR,
per PI-gated Masimo, was 85 bpm — an almost exact predicted collision):

| rank | freq (bpm) | AHET gate | 2nd-harmonic ratio (dB) |
|---|---|---|---|
| 0 | 63.24 | **PASSED** — selected | 4.27 |
| **1** | **84.44** | **PASSED — matches true HR (85) to 0.6 bpm** | 3.11 |
| 2 | 105.79 | PASSED | 6.08 |

**All three candidates independently passed AHET's gate.** Rank 1 — the correct answer — was
never compared against rank 0. It lost purely because it was quieter.

*(Full context: `HISTORY.md`, two 2026-07-14 entries — "Deep dive on the sweep's severe errors"
and the correction immediately after it, which narrows this from an initially-overclaimed 5-hop
pattern down to this single verified case. Read the correction; it matters.)*

## 3. The obvious first fix does NOT work on this example — tested, reported honestly

The natural idea: rank by AHET's own confidence signal (`peak_to_floor_ratio_db`, the same
quantity the gate itself thresholds) instead of raw magnitude.

Applied to hop 124: rank0 = 4.27 dB, **rank1 (true HR) = 3.11 dB**, rank2 = 6.08 dB. **Ranking by
this score selects rank 2 (105.79 bpm) — also wrong.** Neither raw magnitude nor raw
`peak_to_floor_ratio_db` alone picks the correct candidate here.

This is consistent with a warning already raised and not fully heeded: cross-review comment 12.5
on the ECA plan (`plan_eca_forbidden_zone.md` §12) — *"peak-to-floor is not comparable across
candidates, because each candidate's spectrum comes from a different ECA basis and therefore a
different floor."* Each candidate here goes through its **own** second-pass ECA projection
(`vitals.py:474`, guarded on that candidate's own frequency), so its floor is not the same floor
the others were measured against. A raw cross-candidate comparison of `ratio_db` is exactly the
kind of number that warning said not to trust.

**So this note has no working fix to propose — only the mechanism, one real example, and a ruled-out
first idea.**

## 4. Directions for the reviewer to weigh (none implemented, none endorsed over the others)

1. **A combined score**, not either signal alone — e.g. some function of magnitude *and*
   `ratio_db` *and* prominence together. Untested on this example; would need to be checked
   against it before being trusted.
2. **Fix the floor-comparability problem first** (12.5's actual ask): normalise each candidate's
   `ratio_db` against a *shared* reference floor rather than its own per-candidate ECA output,
   so cross-candidate comparison means what it appears to mean. This may be a precondition for
   *any* ranking-based fix, not an alternative to one.
3. **Defer to temporal continuity** (Stage 1B) when multiple candidates pass within some margin of
   confidence — prefer the one whose recent trajectory does *not* track f_r. Architecturally
   heavier: `estimate_rate_from_phase()` is currently stateless per window; this needs history
   threaded through. Also currently unproven at scale — Stage 1B's own statistic has an unresolved
   leverage problem (`plan_eca_forbidden_zone.md` PART IV) and cannot yet be trusted as a tiebreaker.
4. **Do nothing yet; collect more examples first.** Given n=1, a reviewer may reasonably conclude
   there isn't enough evidence to design a fix at all — only enough to confirm the failure mode
   exists and is worth watching for in future captures.

## 5. Question for the reviewer

**Is n=1 sufficient to warrant a design change, or should this wait for more real-data examples
before any fix is attempted?** I lean toward the latter — one hop is enough to say "this is real,"
not enough to say "here is what fixes it," and a fix designed against a single example risks being
tuned to that example rather than the underlying problem (the same trap this whole workstream has
been catching itself in repeatedly — see the ECA v2 rejection, `plan_eca_forbidden_zone.md` PART IV
§21, for a case where a plausible-looking fix collapsed under slightly more scrutiny).

## 6. Not in scope here

- ECA's harmonic cancellation itself — separate document, separate question.
- **"Pattern A"** — the *other*, larger finding from the same severe-error investigation: 11 of 16
  severe hops show an unexplained, strong, non-harmonic ~60-74 bpm peak with the true cardiac
  signal nearly absent. Unrelated mechanism, own investigation, not started.
- Any change to `AHET_MAX_CANDIDATES`, gate thresholds, or ECA modes.

---

## 7. OpenAI review comments — 2026-07-14

The note's mechanism is correct and its evidence scope is appropriately restrained. **One real
hop is sufficient to establish that magnitude-ordered selection can choose the wrong candidate,
but it is not sufficient to design or tune a replacement ranking rule.** No candidate-ranking
code change is justified yet.

The hop-124 state is better described as an **unresolved multi-hypothesis window**. AHET has shown
that three candidates clear its binary eligibility gates; it has not produced calibrated evidence
that identifies which candidate is cardiac. Magnitude selects rank 0, the per-candidate AHET ratio
would select rank 2, and the available within-window evidence does not select rank 1 without using
the Masimo reference. In that state, an explicit NaN is more defensible than another unvalidated
score.

### Recommended next step 1 — multi-pass census and selection-regret analysis

Before changing DSP, run a read-only census over every PI-gated, correctly aligned window in the
available captures. For each window, preserve and report:

- the number and ranks of candidates that passed AHET;
- the selected rank and all passed candidate frequencies;
- whether a Masimo-matching candidate passed but was not selected;
- **selection regret:** selected-candidate absolute error minus the lowest absolute error among
  passed candidates (an oracle diagnostic, never a deployable selector);
- candidate separation and distance to the nearest respiratory harmonic; and
- a mutually exclusive failure category: true HR absent from the candidate list, present but
  rejected, passed but outranked, or correctly selected.

This separates three mechanisms that must not be conflated: candidate-generation failure, AHET
gate failure, and post-gate selection failure. Report per session and do not treat overlapping
hops as independent observations.

The census must be produced offline from the saved raw `adc_stream.bin`, using the pre-specified
PI-gated Masimo PR comparator aligned through integer `Timestamp`. Live estimates and live
intermediates may guide exploration, but they are not paper-grade evidence under `CLAUDE.md` §4.

### Recommended next step 2 — conservative abstention baseline

Evaluate, read-only, the following baseline before proposing any ranking score:

```text
exactly one candidate passes                         -> accept it
multiple materially separated candidates pass       -> NaN: multiple_candidates_ambiguous
```

Pre-specify what "materially separated" means from frequency resolution rather than tuning it on
hop 124. Report severe errors removed, correct estimates lost, yield, and ambiguity rate together.
This baseline may not be the final algorithm, but it establishes the safety/yield trade and gives
every future ranking proposal a comparator it must beat.

### Priority — investigate Pattern A before designing a ranker

Pattern A is currently the larger failure mechanism: on 11 of 16 severe hops the true cardiac
signal is nearly absent, and the true HR appeared anywhere in the candidate list on only 2 of 16
hops. A ranking method cannot recover a candidate that was never generated. Investigating chest-bin
amplitude/SNR, phase quality, range-bin stability, motion/settling, and pre-/post-ECA spectra over
the Pattern A interval therefore has higher expected value than fitting a selector to the one
verified mis-ranking hop.

### Disposition of the proposed directions

1. **Combined score — do not pursue yet.** Magnitude, prominence, and candidate-specific AHET
   ratios provide enough freedom to fit hop 124 and overfit immediately. Wait for recurrent,
   independent selection-regret examples.
2. **Shared floor — conceptually relevant, but not sufficient by itself.** Candidates also have
   different second-harmonic frequencies and candidate-specific ECA spectra. A genuinely
   comparable score may require evidence computed on a common spectrum or projection basis, not
   merely a shared scalar denominator.
3. **Temporal continuity — defer.** Stage 1B round 1 failed and its exploratory motion redesign is
   not confirmatory. It cannot yet serve as a production tiebreaker.
4. **Collect more examples — accepted.** If multi-pass selection regret recurs on untouched
   captures, then a ranking design is warranted. If it remains a one-hop event, conservative
   abstention or no change is preferable to a tuned ranker.

**Recommended order:** (1) multi-pass census and abstention simulation, (2) Pattern A root-cause
investigation in parallel or immediately afterward, (3) collect untouched captures, and only then
decide whether recurrent evidence justifies a candidate-ranking design. Any resulting selector
still requires a separate cross-model plan review and code review before promotion.
