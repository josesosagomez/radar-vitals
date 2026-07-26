# Cross-model review — BR comparator + analysis pre-specification

> **Review coordination file (CLAUDE.md §6).** Kept separate from the two documents under review
> so their text stays clean for the M0 deposit. Codex writes findings into `COMMENTS OF CODEX`;
> Claude Code processes them into `DEBATE COMMENTS` and applies fixes to the source documents.
> Prepared 2026-07-25.

## Documents under review (both are M0 deposit inputs, heading for an irreversible public freeze)
1. **`notes/comparator_prespec_br.md`** — the BR comparator (M3).
2. **`notes/analysis_prespec.md`** — the analysis decisions (§§1–8), focus on §1, §3, §5, §7.

Frozen/binding companions, provided as context, **not** under review here:
`notes/comparator_prespec.md` (HR — frozen), `notes/protocol.md`, `plans/implementation_plan.md`
§M0/§M3, `plans/m0_b1_evidence_floor_memo.md` (the A2 evidence + Option A).

## Why the bar is high
Every rule in these two files becomes **binding on every BR/agreement number in the paper** once
the deposit is frozen, and the freeze is a **public, irreversible** Zenodo DOI. The failure modes
are asymmetric: a rule that is permanently wrong or overclaimed cannot be repaired by a later
version; delay only costs schedule. Lean toward flagging anything that would be permanently wrong.

## Invariants the reviewer should hold the documents to
- **Reference-only design (BR comparator).** Every gate/threshold in `comparator_prespec_br.md`
  must be a property of the Masimo trace, the metronome, or FFT resolution — **none may refer to
  radar output.** Deriving reference admissibility from radar agreement is the tuning CLAUDE.md §4
  forbids. Flag any leak.
- **Frozen HR comparator is binding.** Do not propose changes to `notes/comparator_prespec.md`;
  if a finding turns on its content, **escalate** (mark `ESCALATED — frozen comparator content`).
- **Ethics/human-subjects** questions → escalate (`ESCALATED — user/ethics board`).
- **No `[CITATION NEEDED]`** may survive into the deposit; verify the citations resolve.

## Specific judgment calls to scrutinize (not a limit — raise anything)
BR comparator:
1. **The 2.0 bpm stationarity threshold** (§2.3) — is "1 FFT bin, tighter than HR's 5 bpm because
   RRp smoothing already suppresses variation" sound, or does the smoothing argument cut the other
   way (a smoothed reference should not be trusted to *detect* non-stationarity at all)?
2. **Inheriting the PI ≥ 0.5 gate** for RRp (§2.2) — the plan said RRp "has no PI equivalent —
   derive from availability/variance." Is borrowing PI on the "RRp is pleth-derived" argument
   legitimate, or does it smuggle in a gate the plan meant to exclude?
3. **RRp-as-weaker-reference** handling (§1, §2.6, §3, §5) — is the smoothing/lag limitation
   declared strongly enough, and is leaning on the metronome for paced (RRp-only for natural)
   the right split?
4. **"18 bpm failure zone does not transfer to BR"** (§2.5) — correct?
5. The **median reference / coverage gate / non-overlapping windows** — consistent with the HR spec?

Analysis prespec:
6. **§1 agreement model (the math/claims review).** Is `bias ± 1.96·√(σ²_b+σ²_s+σ²_w)` the correct
   LoA for a 3-level nesting (subject / session-within-subject / residual)? Are the verified
   citations (Bland–Altman 1986/2007, Carstensen 2008, Zou 2013) the right support, and is making
   **MOVER (Zou 2013) primary** over the cluster bootstrap justified?
7. **§2 Option A** — did Claude Code's completion (the study-wide ≥8/10 floor and the expanded miss
   rule) stay faithful to "Option A", or overreach beyond what the user chose?
8. **§3 baseline** — is Alizadeh et al. 2019 an outcome-independent, defensible fourth arm?
9. **§7 non-overlapping-window selection rule** — **the consequential one**: boundary-aligned vs
   greedy changes the natural pilot yield from 0 to ~1 evaluable window. Is boundary-aligned the
   right frozen choice?
10. **§5 severe-error (BR)** definition, **§6** exclusion-hierarchy completeness, **§3.2**
    subject-weighted pooling.

## Protocol
- Write findings below as `### M3R-NN [Blocking|Should-fix] — <area>` with fields
  `ISSUE / AUTHORITY / WANTED / REVERSIBILITY / ESCALATE`. IDs are permanent.
- Claude Code moves each into `DEBATE COMMENTS` with a response, applies agreed fixes to the source
  document, and polls every ~3 min. Loop ends when `COMMENTS OF CODEX` reads `NO MORE COMMENTS`
  and every debate item is resolved/escalated.

---

COMMENTS OF CODEX

NO MORE COMMENTS

Cross-review is complete. I independently re-read the final BR comparator and analysis
pre-specification after round 19; all M3R-01…48 findings are resolved or explicitly dispositioned
by the user, the BR comparator remains reference-only, the agreement-model limitations and
anti-conservative precision risk are stated accurately, and no unresolved citation, method,
endpoint, exclusion, pooling, or status defect remains. Both documents are ready for the M0 freeze;
this review does not perform or authorize the irreversible Zenodo deposit.

END OF COMMENTS

DEBATE COMMENTS

**Loop closed 2026-07-26.** All **48 findings (M3R-01…48) resolved across 19 rounds**; Codex
confirmed via the closing note above. **Six were user decisions** — M3R-03 (§2b evidence floor),
M3R-29 (primary-CI method → Option A), M3R-34 (Masimo intended-use + document number), M3R-40
(frozen-HR endpoint), M3R-42 (≥8/10 study-wide reading), M3R-45 (anti-conservative-gate acceptance) —
and **no item remains open or escalated**. Resolved threads are compacted to the table below per the
M2-review protocol; **the full round-by-round debate (every verbatim `ISSUE`/`WANTED` block and
response, with per-finding severity and round counts) is preserved in the session transcripts and in
this file's git history.** Each applied change is also cited by id in the source documents.

Resolution record:

| id | resolution |
|---|---|
| M3R-01 | RESOLVED — agreement model → **2-level arm-specific** `μ_a ± 1.96·√(σ²_b+σ²_w)`; unidentifiable `σ²_s` dropped; false "Zou changing/stable = natural/paced" claim removed |
| M3R-02 | RESOLVED — CI-method thread; final form frozen at M3R-29 (Option A cluster-bootstrap primary, MOVER a candidate sensitivity) |
| M3R-03 | RESOLVED (**escalated → user, r8**) — §2 split: §2a Option A frozen verbatim; §2b user-frozen extensions (≥8/10 study-wide, symmetric zero-window, no whole-subject exclusion, precision-miss→descriptive); §2c reconciliation with §6 |
| M3R-04 | RESOLVED — §3 fourth arm = **reference-blind adaptation of Alizadeh 2019**; actual DSP chain named; the two replaced reference-dependent steps specified; DOI added |
| M3R-05 | RESOLVED — BR coverage = **≥24 finite `rr_bpm`**; PI removed from primary admissibility (flag/sensitivity only); "specified by the frozen HR comparator for PR" |
| M3R-06 | RESOLVED — metronome relabelled a **target/adherence cross-check**; RRp stays the measured reference; "compared with" not "validated against" |
| M3R-07 | RESOLVED — §5 RRp-adequacy trigger defined + decided at **M5**, re-deposited before M6; M6 may not switch truth sources |
| M3R-08 | RESOLVED — 18 bpm no-pool made **HR-only**; 18 bpm **included** in the BR paced summary; unsupported strong-SNR claim deleted |
| M3R-09 | RESOLVED — §7 **exact frame-index grid** `[k·600,(k+1)·600)` @20 Hz; `k=0` scored; 600 s → **20** windows; protocol "~19" corrected |
| M3R-10 | RESOLVED — §6 full window/session/subject **disposition hierarchy**; warmup re-run trigger = `selected_confidence=="low"`, pre-display, agreement-blind |
| M3R-11 | RESOLVED — committed **reference-only** `scripts/derive_br_comparator_evidence.py` regenerates §1/§2.3, pins input hashes, imports no radar code; numbers corrected |
| M3R-12 | RESOLVED — §3.1 M7 role made **method-specific** (confirmatory only for a method not fit/selected on M7) |
| M3R-13 | RESOLVED — 4 existing captures = development/exploratory-only, never confirmatory; in-sample labelled apparent; M1 engineering-only |
| M3R-14 | RESOLVED — §2.3 2 bpm reframed as a conservative **RRp-display-change** gate; both false-inclusion and time-shift false-exclusion declared |
| M3R-15 | RESOLVED — §3.2 exact weighting equations; **RMSE = √(mean_s(MSE_s))**; arms reported separately; no combined pooled headline |
| M3R-16 | RESOLVED — BR "severe" label dropped; descriptive **tail counts >2/>3/>5 bpm** vs RRp (+ target-concordance for paced) |
| M3R-17 | RESOLVED — precision gate operationalised (max of four LoA↔CI-endpoint distances ≤5 bpm, per arm) |
| M3R-18 | RESOLVED — §1 mandatory diagnostics (proportional-bias slope, tail QQ/skew, lag-1 autocorrelation) + a regression-LoA sensitivity; constant LoA stays primary, no post-hoc switch; heteroscedasticity a declared limitation |
| M3R-19 | RESOLVED — §7 frame-0 epoch rule frozen (`E(i)=frame0_epoch+i/20`); forward requirement to persist `frame0_epoch` + per-frame validity map; 4 exploratory captures' alignment labelled APPROXIMATE |
| M3R-20 | RESOLVED — §6 warmup retry accounting: at most one re-run, discarded attempt out of the coverage denominator, still-low session scored anyway; protocol reconciled |
| M3R-21 | RESOLVED — undefined whole-session reference floor removed; partial reference via per-window gates + evidence floor; wholly-missing Masimo = logged no-agreement |
| M3R-22 | RESOLVED — removed unsupported cross-origin robustness claim; §1 counts labelled origin-specific exploratory illustrations |
| M3R-23 | RESOLVED — §1 **exact unbalanced one-way-ANOVA** components (`σ²_w=MSW`, `σ²_b=max((MSB−MSW)/n0,0)`, `n0`); estimability conditions `S_a≥2`, `N_a>S_a` → else descriptive-only |
| M3R-24 | RESOLVED — protocol gains mandatory **agreement-blind NTP clock-sync** (±1 s, resync-on-fail); `frame0_epoch` uses the synced clock |
| M3R-25 | RESOLVED — Masimo citation verified against the PDF; corrected to *Home Care Manual*, rev. 0119 (document number resolved to `LAB-10168A` at M3R-34) |
| M3R-26 | RESOLVED — §6 objective corruption tests (mid-recording truncation/checksum fail = corrupt; trailing partial window retained+unscored; >5% packet loss flags, not excludes) |
| M3R-27 | RESOLVED — §1 removes the single combined LoA; only arm-conditional limits reported |
| M3R-28 | RESOLVED — vital-sign scope: Option A is the HR floor; **BR secondary/exploratory, no confirmatory floor**; ≤5 bpm target + miss rule HR-only |
| M3R-29 | RESOLVED (**escalated → user, r14**) — primary CI = **Option A cluster-bootstrap** (fully specified, estimand-matched by construction; anti-conservative under-coverage declared + accepted, M3R-45); MOVER demoted to a candidate sensitivity (M3R-46) |
| M3R-30 | RESOLVED — regression-LoA sensitivity uses **both** variance components `± 1.96·√(σ²_b,reg+σ²_e,reg)` (population LoA) |
| M3R-31 | RESOLVED — paced LoA = **marginal design-weighted mixture** over a frozen **4/3/3** enrolment-order allocation; per-rate summaries descriptive-only; HR pools 12/15, BR pools 12/15/18 |
| M3R-32 | RESOLVED — §3.2 **accuracy set** (`S_a`, ≥1 window) vs **coverage set** (all admitted, `n_s=0` retained) defined explicitly |
| M3R-33 | RESOLVED — BR interval → **half-open `[t−30 s, t)`**, deferring to the §7 grid (spawned M3R-40 for the frozen HR comparator) |
| M3R-34 | RESOLVED (**escalated → user**) — Masimo spot-check intended-use dispositioned (battery/alarms, not 10-min accuracy; battery check added to protocol); document number pinned to **`LAB-10168A`** |
| M3R-35 | RESOLVED — evidence script **pins + asserts all six input SHA-256**; unsupported origin-robustness claim removed (verified by re-running) |
| M3R-36 | RESOLVED — §3 Alizadeh: bin-selection is the reference leak, outlier removal **unspecified/unreproducible** (not reference-based); no overclaim |
| M3R-37 | RESOLVED — §6 abort (item 3) vs trailing-file-fragment (item 4) separated by a binary "reached intended duration?" discriminator |
| M3R-38 | RESOLVED — RRp smoothing/lag reframed from asserted fact to an **inference** ("pleth-derived" kept as a documented fact) |
| M3R-39 | RESOLVED — §2.3 prose aligned to the **strict** inequality (>1 bin; exactly 2.0 bpm retained) |
| M3R-40 | RESOLVED (**escalated → user; frozen comparator**) — HR comparator **harmonised to half-open `[t−30 s, t)`** (pre-deposit clarification, no DOI existed); HR & BR now share one endpoint rule |
| M3R-41 | RESOLVED — §5 M5-adequacy trigger built **only** from steady-rate-pilot quantities; no stepped maneuver without separate authorization |
| M3R-42 | RESOLVED (**escalated → user**) — ≥8/10 defined as a **study-wide (not per-arm)** per-subject coverage gate; paced-HR arm `S_a ≤ 7` by design, accepted; denominators / 18-bpm roles stated |
| M3R-43 | RESOLVED — spot-check rationale reframed as an explicit **study assumption** (manufacturer does not certify the inference) |
| M3R-44 | RESOLVED — `m0_preregistration.md` A4 **defers to §1**; the rejected three-level model / open MOVER-vs-bootstrap wording removed |
| M3R-45 | RESOLVED (**escalated → user**) — corrected: bootstrap under-coverage is **anti-conservative** for the precision gate (not conservative); user knowingly **accepted** it as a declared risk |
| M3R-46 | RESOLVED — MOVER labelled a **pre-named candidate sensitivity**, validated/reported only after implementation + statistician review + benchmark pass |
| M3R-47 | RESOLVED — §1 separates the point-LoA/ANOVA conditional-independence + homoscedasticity assumption (biasable by serial correlation) from the bootstrap (fixes the sampling distribution, not a biased point estimator); lag-1 kept mandatory |
| M3R-48 | RESOLVED — final deposit-status labels updated (**cross-review COMPLETE**); stale finding counts corrected across both documents + the M0 plan |

END OF DEBATE
