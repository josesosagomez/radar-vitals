# Independent correctness review: warmup range-bin selection change (radar-vitals)

You are the independent reviewer in a cross-model review process (Claude implemented; you verify).
Your job is an adversarial correctness pass on a small but load-bearing change to the range-bin
lock logic of an mmWave vital-signs radar pipeline. Do NOT assume the implementation or the
analysis behind it is correct — re-derive both. Report findings with severity
(blocker / major / minor / note), then a verdict: APPROVE, APPROVE-WITH-NITS, or REJECT.

## System context

TI IWR1642 FMCW radar (76–81 GHz), subject seated 0.8–1.4 m away, chest facing the radar.
Ground truth: Masimo MightySat pulse oximeter (PR at 1 Hz, PI-gated). The live pipeline locks ONE
range-FFT bin at session start (a 30 s / 600-frame warmup at 20 Hz frame rate scans candidate
bins ~19–32, ≈0.83–1.40 m at 0.0436 m/bin) and keeps it for the whole session — no relocking, by
design. Per-bin warmup evidence: a DSP pass (`_run_dsp`) returning `hr_valid` (a cardiac-band
candidate passed a 2nd-harmonic AHET verification gate), `br_confidence`/`br_valid` (respiration),
plus mean range-FFT energy per bin over the warmup window (Hann window over ADC samples).

## The failure this change fixes (established by offline forensics, summarized)

Session `20260714_..._sweep` (480 s): warmup locked bin 21 (0.92 m). Re-deriving per-bin cardiac
spectra from the session's raw ADC stream showed the subject's chest was at bin 26 (1.13 m),
~28 dB above bin 21 in the settled range profile; bin 21 sat on the skirt. Cause chain:
(1) a strong transient at ~0.92 m existed ONLY in the first ~2 s of the warmup window (subject
settling); (2) on that contaminated window, bin 21 — alone among 14 candidates — passed the AHET
gate, with hr_raw = 110.5 bpm while the reference read 91 (i.e. the pass itself was bogus);
(3) the scoring formula awards +1000 for `hr_valid`, while energy contributes only −5×rank
(max spread ~65 points across 14 candidates), so the single lucky pass decided the lock.
Full-pipeline replay of the same raw stream: locked bin 21 → MAE 9.6 bpm vs Masimo; locked
bin 26 → MAE 0.9 bpm. A second session (`massimo2`) was mislocked by the same mechanism WITHOUT
any transient: skirt bins 19/20 passed AHET with the CORRECT HR (genuine cardiac leakage into
low-energy bins) and outvoted the true chest bin 26 (energy rank 1), which also passed.

Design variants were tested against all four recorded sessions (per-bin ground truth = % of 30 s
windows whose cardiac-band argmax lands within 5 bpm of PI-gated Masimo):

| Variant | sweep | massimo2 | massimo1 | live_test1 (no ref) |
|---|---|---|---|---|
| current | 21 → 3% | 20 → 23% | 23 → 42% | 22 |
| shift DSP window +5 s | 24 → 14% | 20 → 23% (not fixed) | 23 | 23 |
| energy veto on hr bonus | 26 → 45% | 26 → 100% | 23 | 22 |
| both combined | 24 → 14% (worse) | 26 | 23 | 23 |
| adjacent-bin corroboration | 26 | 20 → 23% (not fixed) | 23 | 22 |

The chosen design: keep the DSP window as-is; veto the +1000 `hr_valid` bonus for bins whose
energy — measured over the SETTLED part of the warmup window (skipping the first 5 s) — is more
than 12 dB below the strongest candidate. Rationale for "settled": the sweep transient inflated
bin 21's full-window energy to −11.0 dB rel max (1 dB from the gate) but its settled energy is
−28.1 dB (16 dB margin). Across all 4 sessions, settled energies separate cleanly: every
false-lock bin ≤ −28 dB, every legitimate body bin ≥ −6 dB.

## The change (file: `scripts/live_demo.py`, function `_run_warmup_selection`)

OLD scoring block (verbatim):

```python
        for r in good:
            dsp = r["dsp"]
            score = 0
            if dsp["hr_valid"]:
                score += 1000
            br_conf = dsp["br_confidence"]
            if br_conf == "high":
                score += 250
            elif br_conf == "medium":
                score += 100
            else:
                score -= 100
            if dsp["br_valid"]:
                score += 50
            score -= 5 * r["energy_rank"]
            r["score"] = score

        good.sort(key=lambda r: (
            -r["score"],
            int(not r["dsp"]["hr_valid"]),
            _br_conf_order(r["dsp"]["br_confidence"]),
            int(not r["dsp"]["br_valid"]),
            r["energy_rank"],
            abs(r["bin"] * res - center_m),
            r["bin"],
        ))
        winner = good[0]
        selected_bin = winner["bin"]
        winning_dsp = winner["dsp"]

        if winning_dsp["hr_valid"] and winning_dsp["br_valid"]:
            selection_confidence = "high"
        ...
        selection_reason = (
            f"score={winner['score']}"
            f"_hr={int(winning_dsp['hr_valid'])}"
            f"_br={winning_dsp['br_confidence']}"
        )
```

NEW code (verbatim, current state of the function; `cube` is the (600, chirps, rx, adc) warmup
window, `fs` = 20.0, `_range_energy_by_bin` = mean |Hann-windowed range FFT|² per bin):

```python
    energies = _range_energy_by_bin(cube, candidate_bins)
    sorted_by_energy = sorted(candidate_bins, key=lambda b: energies[b], reverse=True)
    energy_rank = {b: i + 1 for i, b in enumerate(sorted_by_energy)}

    # Settled-window energy for the hr_valid veto. Measured AFTER the settling
    # transient so the same transient cannot both fake an AHET pass and inflate
    # the bin's energy past the gate (the 20260714 sweep failure had 1 dB of
    # margin on full-window energy vs 18 dB on settled energy).
    bcfg = cfg.get("bin_selection", {}) or {}
    veto_db = float(bcfg.get("hr_bonus_min_settled_db", -12.0))
    settle_skip = int(round(float(bcfg.get("settle_skip_s", 5.0)) * fs))
    settled_cube = cube[settle_skip:] if 0 < settle_skip < cube.shape[0] else cube
    settled_energies = _range_energy_by_bin(settled_cube, candidate_bins)
    e_ref = max(settled_energies.values())
    settled_db = {
        b: float(10.0 * np.log10(e / e_ref)) if (e > 0 and e_ref > 0) else float("-inf")
        for b, e in settled_energies.items()
    }
    ...
        for r in good:
            dsp = r["dsp"]
            # hr_valid only counts if the bin's settled energy is plausibly the
            # chest: a lone AHET pass at a skirt bin >12 dB below the strongest
            # candidate must not outvote the body's dominant return.
            granted = bool(dsp["hr_valid"]) and settled_db[r["bin"]] >= veto_db
            r["hr_bonus_granted"] = granted
            r["hr_bonus_vetoed"] = bool(dsp["hr_valid"]) and not granted
            score = 0
            if granted:
                score += 1000
            br_conf = dsp["br_confidence"]
            if br_conf == "high":
                score += 250
            elif br_conf == "medium":
                score += 100
            else:
                score -= 100
            if dsp["br_valid"]:
                score += 50
            score -= 5 * r["energy_rank"]
            r["score"] = score

        good.sort(key=lambda r: (
            -r["score"],
            int(not r["hr_bonus_granted"]),
            _br_conf_order(r["dsp"]["br_confidence"]),
            int(not r["dsp"]["br_valid"]),
            r["energy_rank"],
            abs(r["bin"] * res - center_m),
            r["bin"],
        ))
        winner = good[0]
        selected_bin = winner["bin"]
        winning_dsp = winner["dsp"]

        if winner["hr_bonus_granted"] and winning_dsp["br_valid"]:
            selection_confidence = "high"
        elif winning_dsp["br_valid"] and winning_dsp["br_confidence"] in ("high", "medium"):
            selection_confidence = "medium"
        else:
            selection_confidence = "low"

        selection_reason = (
            f"score={winner['score']}"
            f"_hr={int(winner['hr_bonus_granted'])}"
            f"_br={winning_dsp['br_confidence']}"
        )
        if winner["hr_bonus_vetoed"]:
            selection_reason += "_hr_bonus_vetoed"
```

Evidence JSON additions: top-level `hr_bonus_min_settled_db`, `settle_skip_s` (stored as
`settle_skip / fs if fs > 0 else None`); per candidate `settled_energy_db`
(`round(db,1)`, `None` if −inf) and, for non-failed candidates, `hr_bonus_vetoed`.
The all-DSP-failed fallback branch (highest-energy bin, confidence "low") is unchanged.

Config (`scripts/live_demo_config.yaml`), new keys with defaults mirrored in code:

```yaml
bin_selection:
  enabled: true
  candidate_bins: null
  hr_bonus_min_settled_db: -12.0
  settle_skip_s: 5.0
```

## Tests (file: `tests/test_live_demo_warmup_helpers.py`)

Three new tests: (a) a lone hr_valid pass at ~−40 dB settled energy is vetoed; winner falls back
to breathing evidence and selection_confidence is downgraded to "medium"; (b) regression test for
the sweep mechanism: a 50× transient confined to the first 100 of 200 frames makes the skirt bin
energy-rank 1 over the full window, but settled energy (frames 100:) still vetoes it; (c) setting
`hr_bonus_min_settled_db: -100` in cfg lets the −40 dB bin keep its bonus (config plumbing).
Two pre-existing tests had synthetic tone amplitudes adjusted (bin now at −3.5 dB / −1.9 dB rel
max instead of −15.6 / −14 dB) so their original intents survive the new gate. NOTE: the new
tests deliberately place tones at well-separated bins (4 and 12) because the Hann range window
leaks ~−6 dB into ADJACENT bins, which would defeat the veto in a 2-bin toy cube.

Status: full suite 784 passed / 1 xfailed. Re-running the modified function on the real warmup
windows of all four recorded sessions: live_test1 22→22, massimo1 23→23, massimo2 20→26
(vetoed: 19 @ −39.5 dB, 20 @ −28.9 dB), sweep 21→26 (vetoed: 21 @ −28.1 dB).

## What to verify (re-derive independently; do not take the summary's word)

1. Logic: trace the new code for correctness — veto condition, sort-key change
   (`hr_valid` → `hr_bonus_granted` in the tie-break), confidence downgrade, reason string.
   Any path where a vetoed bin still wins and is then presented as trustworthy?
2. Edge cases: all-zero cube (e_ref = 0 → all −inf → all vetoed); empty candidate list
   (`max()` on empty dict — is this reachable given `_derive_candidate_bins` clamps to ADC
   bounds, and is it a NEW crash vs pre-existing?); `settle_skip >= len(cube)` silently falls
   back to full-window energy (live warmup: 600 frames vs skip 100, but is any other caller
   affected?); fs ≤ 0; JSON serialization of the new fields.
3. Design: is vetoing ONLY the +1000 bonus (leaving vetoed bins eligible to win on breathing
   evidence) the right call vs excluding them outright? Is measuring the veto energy on the
   settled window while running DSP on the FULL window (including the transient) coherent, or
   should the DSP window also move? The variant table above says moving it is empirically worse —
   challenge that reasoning.
4. Threshold: −12 dB and 5 s are justified by a >22 dB separation across n=4 sessions of ONE
   subject. Is the physical argument (a seated subject's chest cannot be 12 dB below the
   strongest reflector inside the 0.8–1.4 m gate) sound? What scene would break it (e.g. a
   moving fan or second person inside the gate; chest partially outside the gate)?
5. Adjacent-bin leakage: Hann mainlobe leaks ~−6 dB into bins ±1 from the chest, so chest-adjacent
   bins always clear the gate. Problem or fine?
6. Tests: do the modified pre-existing tests still test what they claim? Do the new tests
   actually pin the failure mechanism (would they fail on the OLD code)?
7. Residual risks the implementer already flagged (confirm/deny): massimo1 remains locked to a
   mediocre bin (no hr pass anywhere → energy-rank tiebreak); n=4 validation; the veto does not
   help when NO bin passes AHET.

If you have repo + environment access, you may additionally run:
`conda run -n radar-vitals python -m pytest tests/test_live_demo_warmup_helpers.py -q` and the
full suite. Treat all quoted results as claims to check, not facts. The working tree contains
UNRELATED pre-existing modifications to `scripts/live_demo.py` and untracked `scripts/stage1*.py`
files — review only the change described here.

Output: numbered findings (severity + file/line + why it's wrong + concrete failure scenario),
then your verdict.
