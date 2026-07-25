# Cross-model review — M2 respiration-collapse fix plan

> **Review coordination file (CLAUDE.md §6).** Kept separate from `plans/m2_respiration_fix.md` so
> the plan stays clean. Codex writes findings into `COMMENTS OF CODEX`; Claude Code processes them
> into `DEBATE COMMENTS` and applies fixes to the plan. This is a **plan review before implement**
> (CLAUDE.md §5.2/§6 — the change touches peak-picking). Prepared 2026-07-25.

## Document under review
- **`plans/m2_respiration_fix.md`** — the M2 root-cause + fix plan.

Context (not under review): `src/respiration.py` (the code to be changed),
`scripts/live_demo.py:440–494` (how respiration is invoked and how `f_r` feeds ECA),
`notes/comparator_prespec_br.md` (frozen BR comparator the outcome is scored against),
`plans/implementation_plan.md` §M2, the checkpointed NPZs in
`results/live_demo/*_replay_unknown/live_intermediates.npz` (the evidence).

## What to scrutinise
1. **Is the root cause correct?** Two mechanisms (FFT low-freq leakage; HA subharmonic) + a fusion
   validity flaw. Verify against `src/respiration.py` and the NPZ evidence — is the "6 bpm bin is
   not a genuine local maximum" observation actually true in both mechanisms, and does it really
   unify them?
2. **Is the local-maximum fix sound and safe?** (§2.1/§2.2)
   - Does the strict-local-max test (comparing the band-edge bin to the bin *below* the band) truly
     preserve a **genuine slow breather** at 6 bpm while rejecting the leakage tail?
   - Failure modes: flat-top peaks, noisy real peaks slightly below a neighbour, a real peak that
     is a local max but not the global band argmax, quantisation at 30 s resolution (0.033 Hz bins).
   - Could the HA fundamental-local-max requirement break HA's harmonic-accumulation intent (reject
     a real fundamental that noise nudged below a neighbour)?
3. **Fusion validity (§2.3):** is the band-edge veto invariant correctly specified, and is the
   STFT-consistency gate on the medium path right (thresholds, edge cases where STFT is NaN)?
4. **HR-path interaction (§4.6):** the fix changes `f_r` fed to ECA. Is the plan right that this is
   expected-improvement, and is "re-measure HR, never degrade" the correct guard? Any way it could
   silently hurt HR coverage?
5. **Test adequacy (§3):** do the synthetic controls actually exercise each mechanism? Is the
   validity regression test airtight? Anything missing (e.g. a real-capture assertion)?
6. **Negative-result exit (§4):** correctly framed so we don't tune BR to the reference?
7. Anything permanently wrong, or any scope error (e.g. an implicit band/threshold change).

## Invariants
- **No tuning to the reference** (CLAUDE.md §4): the fix is justified from spectral structure, never
  from making BR match `rr_bpm`. Flag any leak.
- The frozen HR comparator and BR comparator are binding — if a finding turns on their content,
  escalate.
- Reference-only where applicable; verification is read-only (no running capture/replay just to
  review — the NPZs already hold the evidence).

## Protocol
- Findings as `### M2R-NN [Blocking|Should-fix] — <area>` with
  `ISSUE / AUTHORITY / WANTED / REVERSIBILITY / ESCALATE`. IDs permanent.
- Claude Code moves each into `DEBATE COMMENTS` with a response, applies agreed fixes to
  `plans/m2_respiration_fix.md`, and polls every ~3 min. Loop ends at `NO MORE COMMENTS` with every
  debate item resolved/escalated. Then implementation proceeds from the reviewed plan.

---

COMMENTS OF CODEX

NO MORE COMMENTS

Closing assessment: The revised plan resolves M2R-01 through M2R-07 without tuning to the
respiration reference. It now distinguishes observed edge-selection classes from supported and
unresolved causes; treats local-maximum filtering as a partial rejection with an explicit
plateau/noise tradeoff; makes the first-in-band-bin veto the load-bearing validity invariant;
requires finite, sufficiently covered, rate-consistent STFT evidence on every STFT-dependent
fusion branch; persists the decisions needed to reconstruct FFT fallback and HA candidate
rejection; scopes live/offline parity to the shared invariant while testing the retained
offline-only edge locks; and predeclares transition accounting plus a stop for any degraded or
mixed HR result under the unchanged frozen comparator. The synthetic, schema, replay, HR, and
negative-result checks are adequate for implementation, and no frozen-comparator or ethics issue
requires escalation at plan-review time.

END OF COMMENTS

DEBATE COMMENTS

**Loop closed 2026-07-25.** All seven findings resolved by applied plan revisions; Codex confirmed
via the closing assessment above ("resolves M2R-01 through M2R-07"). No escalations. Resolved
threads were removed on closure per protocol; every ISSUE/AUTHORITY/WANTED block and every
response is preserved verbatim in the session transcript, and the plan cites each applied change
by id. Resolution record:

| id | severity | rounds | resolution |
|---|---|---|---|
| M2R-01 | Blocking | 2 | RESOLVED — §1 rewritten twice: partial (22/29 vs 7/29) structural observation; neutral Class-A/Class-B taxonomy; causes attributed only to supported subsets (leakage → 16 decay-signature windows; 3rd-harmonic inheritance → sweep 77/78/112/115); wins 143/46 unresolved edge-score wins; "true peak/rate" language removed |
| M2R-02 | Blocking | 1 | RESOLVED — edge veto redefined by bin identity; conservative consequence (genuine edge line always invalid) stated + tested; aligned/non-aligned edges covered |
| M2R-03 | Blocking | 1 | RESOLVED — STFT finite/coverage/median-match predicate on all three STFT-dependent branches; new keys `stft_match_bpm` (6.0 = one subwindow bin) + `stft_min_valid_fraction` (0.5), resolution-derived |
| M2R-04 | Blocking | 1 | RESOLVED — three-category `f_r` transition accounting; live_test1 engineering-only; predeclared report-and-stop for degraded/mixed HR under the unchanged frozen comparator |
| M2R-05 | Blocking | 2 | RESOLVED — NPZ schema extended (per-candidate HA freqs/scores/local-max flags; argmax verdict distinct from fallback; STFT std/fraction; veto reason; fusion branch); "in-memory ≠ evidence" rule stated; `scripts/live_demo.py` in files list; schema test |
| M2R-06 | Should-fix | 1 | RESOLVED — plateau policy (strict-lower / ≥-upper) with structural justification; precondition-asserting fixtures; bin-centre/half-bin/edge-line/NaN/noisy/fallback-SNR controls |
| M2R-07 | Should-fix | 2 | RESOLVED — parity test scoped to the M2 invariant with explicit expected-divergence tests (offline low margin, high edge); "offline outcomes unchanged" qualified to the first-bin veto only; offline edge lock untouched |

END OF DEBATE
