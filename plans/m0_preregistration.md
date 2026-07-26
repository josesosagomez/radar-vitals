# M0 — Pre-registration freeze & deposit

> Detailed milestone plan for `plans/implementation_plan.md` §M0. Written 2026-07-25 against
> branch `vital_signs_v9c`. Subject to cross-model review (CLAUDE.md §5.2/§6) before execution.

---

## Context

M0 is the project's hard gate: no study capture (M5 onward) may happen before a public,
timestamped DOI exists carrying both comparators, the protocol, and the frozen analysis
decisions.

**The timing claim, stated precisely (use this wording everywhere, including Zenodo metadata):**
the public v1 deposit predates the first study capture (M5) and every confirmatory capture
(M6 onward). The four existing captures — plus M1's smoke test **if it has run by freeze
time** — are **pre-freeze exploratory**; the
M5 pilot is **post-freeze exploratory**; none of them contributes to confirmatory/headline
agreement metrics. **Do not claim the DOI predates all agreement computation** — exploratory
agreement numbers on the four existing captures already exist (`HISTORY.md` 2026-07-15,
the 0.19/0.50/0.53 bpm re-scorings). The claim is "frozen before the confirmatory data,"
never "before any data."

**Scope of the milestone: governance and publication only. M0 contains no project code.**
Its outputs are frozen documents, a published archive, the public record, and the provenance
record. Preparation steps use existing tracked facilities; any genuinely new tooling belongs to
M4 (evaluation harness) or M11a (coverage work) and gets planned there.

Spec authority: `plans/implementation_plan.md` §M0 (cross-reviewed 2026-07-24, 19 comments, 3
rounds). **M0's only milestone dependency is M3** (the BR comparator, not yet written) — M1, M2
and M4 are prerequisites of M5, not of M0. Binding condition 1 of the ordering rule still
applies: any protocol/comparator discovery surfaced by whichever of M1/M2/M4 has run by freeze
time must be folded into the deposit before it is frozen.

**Decisions made by the user on 2026-07-24, binding on this plan:**
1. **Characterize-then-freeze.** Run a *no-tuning* diagnostic of where the 10–46% coverage dies
   before setting the evidence floor, then freeze ECA+AHET as-is as the primary estimator. Full
   coverage work (M11a) stays post-freeze, reachable only through the amendment mechanism.
2. **Venue = Zenodo.** Concept DOI + per-version DOIs become the backbone of the amendment
   mechanism (§Phase D).

**Verified facts this plan rests on (re-checked 2026-07-25 during cross-model review):**
- The deposit inputs are **already tracked**: commit `bc12663` (2026-07-24) committed the
  `.gitignore` fix (removing `notes*/`) plus `notes/comparator_prespec.md`,
  `notes/protocol.md`, `plans/implementation_plan.md` and the other notes. At review time the
  working tree holds only this plan file untracked. `bc12663` is the baseline commit *at this
  pass* — do not assume it is still HEAD at freeze; A1 audits the actual state then.
- 4 real captures exist under `results/live_demo/` (3 carry Masimo CSVs in-folder:
  `..._massimo1`, `..._massimo2`, `..._sweep`); the five `*_replay_unknown` folders are
  reprocessing artifacts, not sessions. The post-fix re-scorings live at
  `results/live_demo/20260715_164018_replay_unknown/`, `20260715_164124_replay_unknown/`,
  `20260715_164132_replay_unknown/`. `data/raw/` is empty; `data/manifest.local.csv` is
  header-only (0 rows).

---

## Execution status (2026-07-25)

Phase A preparation started. **Done (draft, uncommitted — freeze-commit happens at C2):**
- **A2** coverage characterization — `plans/m0_b1_evidence_floor_memo.md` §1. Key finding: at the
  non-overlapping 30 s cadence, evaluable-window yield is far below the per-hop "10–46 %" — natural
  **0/6**, paced16 3/6, sweep 3/16 windows accepted, *before* Masimo gates. Per-hop counts
  reproduce HANDOFF's 5/23/30 exactly.
- **A3** capture inventory — `notes/capture_inventory.md` (hashes, durations, bins, A6 schema).
- **A4** analysis prespec — `notes/analysis_prespec.md` (8 decisions; **cross-reviewed, all findings
  resolved — no `[OPEN]` items remain**).
- **A5/A6** ethics + privacy — recorded from the user's 2026-07-25 decisions (protocol `24IBEC051`,
  issuing board **IBEC, KAUST**, metadata schema) into A5/A6, the inventory, and the prespec.
- **B1** evidence-floor memo (3 options) → `plans/m0_b1_evidence_floor_memo.md`; the user chose
  **Option A**, now frozen in `notes/analysis_prespec.md` §2.
- **M3** BR comparator (`notes/comparator_prespec_br.md`) + the analysis prespec
  (`notes/analysis_prespec.md`) — **drafted AND cross-model reviewed** (Codex, 2026-07-25/26):
  **48 findings across 17 rounds, all resolved — cross-review COMPLETE.** The user decisions are
  decided (M3R-29 primary-CI → **Option A, cluster-bootstrap primary**; M3R-42 → **≥8/10 read
  study-wide, not per-arm**; M3R-40 endpoint half-open; M3R-34/45 dispositions — all 2026-07-26). Major
  corrections: agreement model rewritten to a 2-level arm-specific unbalanced-ANOVA LoA
  (**cluster-bootstrap primary CI**, with its anti-conservative-precision-gate limitation declared —
  M3R-45; MOVER a pre-named *candidate* sensitivity, validated only after implementation + statistician
  review + benchmark — M3R-46); Alizadeh baseline redeclared a
  reference-blind adaptation (it leaked the reference); BR PI-gate removed (availability/variance
  only); frame-0 epoch origin + clock-sync + corruption/retry rules frozen; BR made secondary with
  no confirmatory floor. Evidence-floor §2b **frozen by user decision** (≥8/10 study-wide, symmetric
  zero-window handling, no whole-subject exclusion, precision-miss → descriptive). Full log:
  `plans/m3_prespec_cross_review.md`.
- **Prespec §3 baseline — NAMED**: Alizadeh et al. 2019 (IEEE Access), reference-blind adaptation.
- **Prespec §1 citations — VERIFIED**: Bland & Altman 1986/2007, Carstensen 2008, Zou 2013.

**Masimo eIFU citation — VERIFIED and RESOLVED** (verified directly against the PDF
`literature/ref_papers/lab-10168a_master.pdf`, 2026-07-26): *MightySat Rx Home Care Manual*, © 2019
rev. 0119, p. 10 RRp/low-perfusion warning — this **corrected** Codex's pasted metadata (it was not
"Operator's Manual", not rev. 2019-02-18). **Document number `LAB-10168A`** (printed on the back
cover) is the citable identifier, **confirmed by the user 2026-07-26**; the earlier local filename
`lab-10169a` was a typo, corrected. **The M3R-34 intended-use (spot-check) disposition and the
M3R-40 HR-comparator endpoint harmonisation are also resolved** (user decisions 2026-07-26; see the
comparators and `plans/m3_prespec_cross_review.md`).

**Still open before freeze:** the **cross-model review of the fully assembled deposit** (C2).
**User-only, irreversible:** C1/C2 assembly then D1/D2 publish.

---

## Phase A — Prepare
*Starts now; runs in parallel with M2/M3, does not wait on them.*

### A1 — Provenance audit of the deposit inputs (at freeze, not now)
The inputs are already tracked as of `bc12663` — there is no pending commit to make. The action
is an **audit at freeze time**: confirm the final version of every deposit input is committed
(committing only real changes arising from A4/B1/M3), and record the actual freeze commit.
**Rationale:** a pre-registration input with no version history is unverifiable — the freeze
must point at a specific commit, whatever HEAD is by then.

### A2 — Coverage characterization (decision input; no new code, no algorithm changes)
For the three Masimo-referenced sessions (post-fix re-scorings:
`results/live_demo/20260715_164018_replay_unknown/`, `20260715_164124_replay_unknown/`,
`20260715_164132_replay_unknown/`, plus their Masimo CSVs), tabulate per non-overlapping 30 s
window where it dies: AHET rejection (and which criterion), radar-NaN, PI gate,
sample-coverage gate, stationarity gate. Deliverable: a per-session waterfall table inside the
B1 decision memo, with the exact method and commands recorded so it is reproducible.

Use **existing facilities only**: each run's `live_estimates.csv` already records per-hop
rejection reasons; the Masimo gates come from the tracked `src/masimo.py` / `src/compare.py`
paths; `scripts/diagnose_live_run.py` is the existing post-mortem. If this turns out to need
genuinely new tooling, that tooling is planned and reviewed under M4/M11a — it is not built
inside M0. This is **measurement only** — it exists so the evidence floor (Phase B) is chosen
on understood numbers, not on an opaque 10–46%.

### A3 — Capture inventory (a document, not a script)
Prepare `notes/capture_inventory.md` + CSV as reviewable data preparation: per capture, SHA-256
of `adc_stream.bin` and any Masimo CSV (standard tooling, e.g. `Get-FileHash`, with the exact
command recorded in the document), duration/frame count from `run_metadata.json`, date, subject
label, Masimo present (y/n), class label (**pre-freeze exploratory**). `*_replay_unknown`
folders are listed separately, tagged as reprocessing artifacts, not sessions. Re-verified at
freeze time: the invariant is **complete enumeration of every capture that actually exists at
freeze** — M1's smoke capture is included and labelled pre-freeze exploratory *if it has run by
then*; otherwise the inventory records that M1 had not yet run. (M1 is not a prerequisite of
M0.) **The public projection of this inventory carries only the A6-approved fields** (subject
label, date, duration, sex, age, height, cardiac condition, file hashes; class label); the full
repo-internal inventory may hold more for provenance, but only the approved fields are
published.

### A4 — Draft the analysis decisions → `notes/analysis_prespec.md`

The four decisions required by `plans/implementation_plan.md` §M0, plus the endpoint,
exclusion, and reporting rules that make them enforceable:

1. **Agreement model.** **The binding specification is `notes/analysis_prespec.md` §1 — assemble the
   deposit by reference to it, do NOT restate its equations here** (a divergent copy could reintroduce
   a model this cross-review already rejected — M3R-44). In brief, and superseding the earlier draft
   of this item: the model is **arm-specific and two-level** (subject + within-subject-arm residual);
   there is **no** session-within-subject variance component (unidentifiable with one session per arm,
   so the old `√(σ²_b + σ²_s + σ²_w)` three-level LoA is retired), estimated by **closed-form
   unbalanced one-way ANOVA**; the CI method, estimability conditions, precision gate, and diagnostics
   are exactly as frozen in §1 (its citations — BA 1986/2007, Carstensen 2008, Zou 2013 — are already
   verified there). **The primary-CI method is resolved (M3R-29 Option A: cluster-bootstrap primary,
   its anti-conservative-precision-gate limitation declared; MOVER a pre-named *candidate* sensitivity
   validated only after implementation + statistician review + benchmark) — take it from §1 at
   assembly; do not restate its equations here.** **Requires math/claims cross-review (CLAUDE.md §6).** The pooled statistics already produced by `scripts/plot_bland_altman.py` are
   declared descriptive-only in the deposit, never a limit of agreement.
2. **Evidence floor + precision target.** Written as a decision gate (Phase B) rather than
   pre-decided here: the frozen rule must name a per-session floor, a per-subject floor, a
   study-wide floor, the precision the study claims (e.g. max CI half-width on the LoA), and a
   **prospective miss rule**. The miss rule's levers must be ones actually available — "add
   sessions" is a lever *only if* the ethics approval permits more than 2 sessions/subject
   (check once A5 supplies the reference number); otherwise the sole lever is a reduced claim.
3. **Method-comparison discipline, including a normative pooling table.** Primary estimator =
   ECA+AHET as currently committed; primary endpoint = HR MAE **with coverage**, under the
   frozen HR comparator. Secondary and exploratory: BR agreement, Harmonic Accumulation (M8),
   joint-Doppler (M9), baselines (M10). A data-roles table: the 4 existing captures + M1's
   smoke test = design/tuning only; the M5 pilot = post-freeze exploratory; M6 onward =
   evaluation only, never tuning. The **pooling table** states normatively: which capture
   classes and arms enter each headline endpoint; **M5 never enters confirmatory or headline
   metrics**; **the 18 bpm paced arm is always reported separately and never pooled into
   headline metrics**; the unit and weighting of every pooled MAE/RMSE (window-weighted,
   session-weighted, or subject-weighted — named explicitly per summary); the session→subject
   aggregation rule; and how unequal evaluable-window counts affect each reported summary.
   **The fourth estimator arm is pinned:** the published-pipeline baseline
   (`plans/implementation_plan.md` §M10 item (b)) is either **named before freeze** or fixed by
   a frozen, **outcome-independent selection rule** (criteria such as citation standing,
   protocol comparability, and implementability — never performance on any of this project's
   data); post-freeze substitution requires a dated amendment. **TI's on-chip output (§M10
   item (a)) is an additional engineering baseline, not one of the four estimator arms** of the
   Method goal. Multiplicity: secondaries are reported descriptively (CIs labeled exploratory,
   no formal winner-crowning test); promoting any secondary to primary requires a documented
   decision plus an amendment.
4. **Amendment mechanism.** An amendment is a dated changelog entry (what changed, why),
   cross-model review where CLAUDE.md §6 applies, a **new Zenodo version DOI** under the concept
   DOI, and prospective-only application — it is never retrofitted to already-collected data.
   An un-re-deposited change is void; the frozen version governs. An amendment that changes what
   a participant is asked to do must clear ethics before it is used (ties to M7/M5).
5. **Endpoint definitions.** Every endpoint that will be reported is defined normatively —
   in particular **severe error**: threshold, equality boundary (e.g. "|err| > 5.0 bpm", stating
   whether 5.0 exactly is severe), and denominator (severe count over which window population).
   Defined per vital sign where it will be reported; **if no severe-error endpoint is intended
   for BR, the deposit says so explicitly** rather than leaving it open.
6. **Exclusion hierarchy.** All exclusions above the comparator's window gates are enumerated —
   participant, session, arm, protocol-deviation, incomplete-reference, unusable-capture — or
   the deposit states explicitly that **no exclusions other than protocol-abort and the frozen
   comparator/window rules are permitted**. Counts and reasons are reported at every level of
   the hierarchy, always.
7. **Required breakdowns.** Per-subject HR and BR accuracy (MAE/RMSE) and coverage are required
   outputs, not options. Distance is reported **descriptively against the measured continuous
   distance** (CLAUDE.md §1's "per distance" as resolved by `plans/implementation_plan.md`
   §M4): no post-hoc distance strata, no per-distance agreement claim, unless separately
   pre-specified by amendment.
8. **Deliberately not pre-specified.** A normative section naming every degree of freedom the
   deposit intentionally leaves open — coverage work (M11a), secondary-method internals and
   tuning (the *identity* of the published-pipeline baseline is **not** open — see item 3),
   sensitivity analyses beyond those listed, figure/presentation choices — and, for
   each: what data may be used to resolve it, and whether resolving it requires an amendment.
   The deposit freezes **only the enumerated rules**; this section prevents a reader from
   assuming more was fixed than actually was.

### A5 — Ethics record — RESOLVED (user decision, 2026-07-25)
**Ethics approval covers both collection and publication. Reference: Research protocol
`24IBEC051`.** (User-confirmed 2026-07-25; recorded on the user's authority — no agent has read
the approval document.) Consequences for the deposit:
- (a) sessions/subject beyond 2 — **still to confirm from the approval document** before the
  evidence-floor miss rule (A4 item 2) can list "add sessions" as an available lever. Not
  publication-blocking, but B1 needs it.
- (b) raw-data *sharing* — covered by the "publish" approval; the data-availability statement
  in `JOURNAL_PAPER.md` §10 can proceed (subject to the archive-size practicality note there).
- (c) public pre-recruitment deposit of the detailed protocol — **PERMITTED.**
- (d) public disclosure of the approval reference + issuing board — **PERMITTED.** Reference
  `24IBEC051`, issuing board **IBEC, KAUST** (user-supplied 2026-07-25), recorded in
  `notes/protocol.md`, the deposit README, and both writing files. *(For the Methods section,
  confirm the board's full formal expansion of "IBEC" as it appears on the approval — recorded
  here verbatim as given, not expanded by inference.)*

### A6 — Public-payload privacy schema — RESOLVED (user decision, 2026-07-25)
**Approved public participant metadata, and nothing more:** capture **date**, **duration**,
**sex**, **age**, **height**, and **any cardiac condition**. Published under protocol
`24IBEC051`. Constraints that make this non-identifying, applied on top of the user's schema:
- **Subject label only** (e.g. `S01`) — never a name or contact detail.
- **Age in years**, not date of birth; capture **date** only (the folder-name timestamps are
  reduced to a date in the public inventory).
- **No raw recordings, no `adc_stream.bin`/CSV content** in the public payload — file **hashes**
  and the demographic fields above are the only capture-derived public data.
- At v1 freeze every inventoried capture is a **researcher self-capture** (no third-party
  participants exist yet); this schema governs the **amendment inventories** that will carry
  pilot/study participants. For the researcher's own v1 rows the same schema applies.

This replaces the earlier "zero participant data" framing: the study now **deliberately
publishes the limited demographic schema above under approval `24IBEC051`.** The **final
publication gate (D1)** attestation is worded accordingly — see D1.

---

## Phase B — Decide

### B1 — Evidence-floor decision memo → user decision
Built from A2's waterfall table plus the yield arithmetic already in `notes/protocol.md`
(10 min ≈ 19 windows/session; × 10–46% AHET-acceptance; − 12–20% Masimo gates → roughly
3–14 evaluable windows per subject across 2 sessions). Present 2–3 candidate floor + precision
combinations with their consequences (what happens to statistical power, what the miss rule
does in each case).

**The user decides; the choice is written into `notes/analysis_prespec.md` §2.** This cannot
slip past the freeze — a floor chosen after seeing the pilot yield is not a floor
(`plans/implementation_plan.md` §M0).

---

## Phase C — Assemble & review
*Waits on **M3 only** — the sole milestone dependency (`plans/implementation_plan.md`: "M0
waits only on M3"; M1, M2 and M4 are prerequisites of M5, not of M0). If any of M1/M2/M4
happens to have run by the final gate, its relevant discoveries must be reconciled into the
deposit first — but none of those milestones is itself a freeze prerequisite. **Any revision of
M3's comparator while M0 is being drafted invalidates the assembled draft and triggers
re-assembly and re-review before the final gate.** Non-binding scheduling note: freezing after
M4's regression check reduces amendment risk, because turning comparator prose into code is
when ambiguities surface — but this must not delay the deposit once M3 and the decisions are
ready.*

### C1 — Deposit contents (every payload file is a tracked file in the freeze tag — no new script)

- **Standalone normative specification — `notes/prereg_normative_spec.md` (tracked)** — the
  core of the deposit. A self-contained document reproducing **every binding rule** needed to
  interpret HR scoring, BR scoring, the capture protocol, the analysis decisions (A4 items
  1–8), exclusions, aggregation, and the amendment mechanism — readable and enforceable by
  someone with the DOI and **no repo access**. Every internal cross-reference in the source
  notes (e.g. `comparator_prespec.md` citing "CLAUDE.md §9" / "HANDOFF §5"; `protocol.md`
  leaning on `scripts/live_demo_config.yaml`, `notes/approach.md`, named scripts and repo
  paths) is either reproduced in place or explicitly downgraded to non-normative provenance.
  **No frozen rule may depend on following a repo path.** Producing this restatement does not
  modify the frozen `notes/comparator_prespec.md` — its rules are reproduced
  verbatim-with-context, and the source file ships alongside as provenance.
- Source files verbatim, as provenance: `notes/comparator_prespec.md` (HR),
  `notes/comparator_prespec_br.md` (BR, produced by M3), `notes/protocol.md`,
  `notes/analysis_prespec.md`.
- Capture inventory (`notes/capture_inventory.md` + CSV) — public content per the A6 privacy
  decision.
- **README — `notes/prereg_readme.md` (tracked)** — states the timing claim exactly as worded
  in §Context, the ethics record as permitted by A5(d), the tag name `prereg-v1.0`, per-file
  SHA-256 of **every payload file except itself**, and the archive manifest. **Circularity
  note:** no file can embed its own final SHA-256, and a tracked file cannot contain the hash
  of the commit that contains it. So: the README hashes every *other* payload file and states
  this self-exclusion explicitly; the README's **own hash**, the freeze **commit hash**, and
  the **archive hash** are recorded outside the payload — the complete per-file manifest
  (README's own hash included) plus the archive hash and size go in the Zenodo record
  description (metadata, editable pre-publish only) and in `HISTORY.md`. D3 verifies the
  downloaded payload against that external manifest.

### C2 — Freeze: non-circular pinning order + checklist

**Order (binding — nothing may be reordered):**
1. **Finalize every input** and commit: A4/B1 decisions written into
   `notes/analysis_prespec.md`; M3's comparator final and cross-reviewed;
   `notes/prereg_normative_spec.md` and `notes/prereg_readme.md` written; A2 memo and A3
   inventory current (M1's capture included **if it exists**, its absence recorded otherwise);
   discoveries from any completed M1/M2/M4 reconciled.
2. **Cross-model review of the deposit content at that commit** passes; all review comments
   closed. Any change → fix, commit, re-review (so the tag is never orphaned by a
   review-forced change).
3. **Tag `prereg-v1.0` on the reviewed commit** (A1's provenance audit happens here).
4. **Assemble the payload from the tag** — it packages **only bytes already present in
   `prereg-v1.0`**; the sole untracked object is the archive container itself.
5. **Hash the final archive** (SHA-256); record together, **outside the payload**: tag,
   commit, the complete per-file manifest (including `prereg_readme.md`'s own hash — see C1's
   circularity note), archive hash and size.
6. Proceed to the final publication gate (D1) — which verifies the mechanical assembly
   (hashes, contents), the content having been reviewed at step 2.

**Checklist (all boxes before D1):**
- [ ] M3 done and cross-reviewed; BR comparator included in the bundle. (M0 deposits **both**
      comparators — `plans/implementation_plan.md` §M0 "Done when". An HR-only deposit is
      **not an execution branch of this plan**: any change to M0's scope requires the user to
      stop and revise the authoritative base plan first, as a separate user decision.)
- [ ] Discoveries from whichever of M1/M2/M4 have run by now are folded in
- [ ] Evidence floor decided (B1) and written into `notes/analysis_prespec.md`
- [ ] Ethics reference number + issuing board recorded; A5(c)/(d) and A6 decisions made and
      documented
- [ ] Capture inventory re-verified at freeze — complete enumeration of every capture that
      exists; M1's capture included and labelled pre-freeze exploratory **if it has run**,
      otherwise its absence recorded
- [ ] Zero unresolved `[CITATION NEEDED]` / `[PENDING]` / placeholders anywhere in the
      normative deposit (the A4.1 Bland–Altman citation verified against the source papers)
- [ ] Reference audit passed: no dangling normative references in the standalone specification
- [ ] Pinning order steps 1–6 complete: tag `prereg-v1.0` on the reviewed freeze commit;
      archive hash recorded

---

## Phase D — Publish & record

### D1 — FINAL PUBLICATION GATE (human-operated, immediately before the irreversible click)
Everything up to and including a Zenodo **draft** upload is reversible; **pressing Publish is
not**. The user personally verifies, in one sitting, immediately before publishing — and
nothing after this gate may alter the payload:

- [ ] Exact final archive SHA-256 **and size** match the record from C2 step 5
- [ ] Final metadata and claim preview read exactly as approved — the timing claim worded as
      in §Context ("frozen before the confirmatory data"; no claim of predating all agreement
      computation)
- [ ] Required contents present: standalone normative spec, both comparators, protocol,
      analysis prespec, capture inventory (A6-approved fields only), ethics record with
      reference `24IBEC051` (A5(d) permits) and the issuing board's full name
- [ ] Zero unresolved placeholders and zero dangling normative references
- [ ] Capture inventory is current and **no M5 capture has begun**
- [ ] Privacy attestation (A6): the public payload carries only the approved participant
      metadata schema (subject label, date, duration, sex, age, height, cardiac condition,
      hashes) — no names, no DOB, no raw recordings — published under protocol `24IBEC051`
- [ ] Public access, no embargo; the immutable **v1 version DOI** will be minted on publish
- [ ] All cross-model review comments closed
- [ ] Explicit user confirmation to publish

### D2 — Publish (user action — outward-facing and irreversible, never automated)
The user presses Publish. Record **both identifiers**: the **concept DOI** (collection-level
pointer only) and the **v1 version DOI** — the immutable v1 version DOI is what is cited for
the original frozen rules, everywhere. The publication date must precede the first M5 pilot
capture.

### D3 — Record, propagate, and independently verify

**Record:** append a `HISTORY.md` entry (date, both DOIs, tag `prereg-v1.0`, freeze commit,
archive hash).

**Propagate — named insertion points, placeholder-aware:**
- `JOURNAL_PAPER.md` §10, the pre-registration bullet ("Deposit it publicly (OSF or similar)…")
  — cite the v1 version DOI; note the venue decision (Zenodo).
- `THIRD_CHAPTER.md` — the pre-registration-deposit `[PENDING]` (the "specified in advance in
  the pre-registration deposit" sentence, currently near line 448) and the §10.3 `[PENDING]`
  material **only where the DOI actually resolves the pending fact**. Per `THIRD_CHAPTER.md`'s
  own rules, no unrelated empirical `[PENDING]` is promoted, and tags are promoted by
  re-measuring, never by editing.
- `notes/protocol.md` header — a provenance note ("deposited as <v1 DOI> on <date>"), which
  does not alter any frozen rule.
- The ethics reference (`24IBEC051`) and the issuing board's full name enter the writing files'
  Methods/ethics sections (A5(d) permits public disclosure).
- Rewrite `HANDOFF.md` to the new current state (§10.1 rules — replace, don't append).

**Post-publication acceptance record — a named independent checker** (the non-authoring model
family per CLAUDE.md §6 for content checks; the user for anything needing live web access),
with separate pass/fail evidence per item, recorded in `HISTORY.md`:
- [ ] DOI resolves publicly — no login, no embargo
- [ ] Publication timestamp verified to precede the first M5 capture
- [ ] Payload downloaded; its archive hash matches the final-gate record from C2 step 5, and
      its files match the external complete manifest — including `prereg_readme.md`'s own
      hash, which by construction lives only in that external record (C1 circularity note)
- [ ] Required contents confirmed present in the downloaded payload
- [ ] v1 version DOI propagated to `HISTORY.md`, `JOURNAL_PAPER.md` §10, and
      `THIRD_CHAPTER.md` exactly

---

## Explicitly out of scope for M0
- **Any project code.** M0 creates no scripts and no tests; preparation uses existing tracked
  facilities (A2/A3). Genuinely new tooling belongs to M4 or M11a and is planned there.
- Any DSP/algorithm change — A2 is measurement only; full coverage work (M11a) stays
  post-freeze, reachable only via the amendment mechanism.
- M3's content — it is its own milestone with its own cross-review; M0 only consumes its output.
- Any confirmatory agreement number — those come from M4's harness on post-freeze study data
  (M5 exploratory, M6 confirmatory), never from M0. (Exploratory numbers on the four existing
  captures already exist and are enumerated as such — see §Context.)

## Blocking inputs needed from the user
1. **Ethics — RESOLVED (2026-07-25):** approval covers collect **and** publish, reference
   `24IBEC051`, issuing board **IBEC, KAUST**; A5(c)/(d) permitted; A6 public metadata schema
   fixed. A5(a) (>2 sessions/subject) is **moot** under the frozen Option A miss rule, which does
   not add sessions.
2. **Evidence floor — RESOLVED (2026-07-25): Option A**, transcribed into
   `notes/analysis_prespec.md` §2.
3. **Still open before freeze (not user-only):** the published-pipeline baseline (§3 of the
   prespec), the Bland–Altman citation (§1), M3's BR comparator, and the cross-model review.
4. The final publication gate and the Publish click (D1/D2) — irreversible, user-only.

## Files created/modified

| File | Action |
|---|---|
| `plans/m0_preregistration.md` | this file |
| `notes/analysis_prespec.md` | new (document) |
| `notes/capture_inventory.md` (+ CSV) | new (document + recorded-command data preparation); re-verified at freeze |
| B1 evidence-floor decision memo | new (document, in `plans/` or `notes/`) |
| `notes/prereg_normative_spec.md`, `notes/prereg_readme.md` | new, tracked; finalized and committed **before** the freeze tag (C1, C2 step 1) |
| Deposit archive | generated at C2 step 4 from tag `prereg-v1.0`; the only untracked object (the container); hash recorded |
| `HISTORY.md` | appended (D3: freeze record, then acceptance record) |
| `HANDOFF.md` | rewritten (D3) |
| `JOURNAL_PAPER.md`, `THIRD_CHAPTER.md` | v1 DOI (+ ethics reference only if A5(d) permits) at the named insertion points (D3) |
| `notes/protocol.md` | header provenance note only (D3) — no rule changes |

## Verification

- **Pinning-order integrity:** tag ↔ freeze commit ↔ per-file hashes ↔ archive hash ↔ v1
  version DOI are all recorded and mutually consistent (C2/D2).
- A2/A3 are reproducible from their recorded methods/commands: re-running them produces
  identical tables and hashes; A2's window counts reconcile with the known 10–46% coverage
  figures.
- Every claim in the deposit README is verified against the repo at freeze time — no dangling
  file references, no unverified "works" claims (HANDOFF §10.1 discipline).
- The C2 checklist is fully checked before D1; D1's gate is fully checked before D2; D3's
  acceptance record carries pass evidence for every item, from a named independent checker.
- The test suite (`conda run -n radar-vitals python -m pytest tests/ -q`) stays green as a
  standing invariant — trivially, since M0 adds no code.

## Immediate next step
This document's cross-model review (2026-07-25) **is** the CLAUDE.md §5.2/§6 plan review; the
ethics/privacy escalations (A5(c)/(d), A6) were resolved by the user the same day. Phase A
preparation (A2, A3, drafting A4) can start now; A1 is an at-freeze audit; Phase C waits on M3.
Nothing in Phase A is gated on M2.

**Remaining inputs before freeze/publish:** (1) the issuing board's exact full name
(publication-blocking); (2) the >2-sessions fact — only if B1's miss rule needs the add-sessions
lever; (3) the evidence-floor decision (B1); (4) M3's completed, cross-reviewed BR comparator
before Phase C.

COMMENTS OF CODEX

NO MORE COMMENTS
I consider this plan safe to execute only through its stated gates: publication remains blocked
until M3's cross-reviewed BR comparator exists, the B1 evidence-floor/precision rule is frozen,
and the issuing board's exact full name is supplied and checked. There is no frozen-comparator
content escalation; the privacy and ethics escalations were resolved on the user's authority
under protocol `24IBEC051`, including the deliberately limited public metadata schema. At the
plan level the deposit now omits none of the agreement, pooling, estimator-comparison,
amendment, pinning, propagation, or independently verifiable completion rules identified in
this review; the normative deposit artifacts themselves must still be assembled and reviewed
before the irreversible D1/D2 gate.

END OF COMMENTS

DEBATE COMMENTS

(M0-06 and M0-07 — the two escalated ethics/privacy items — were RESOLVED by user decision
on 2026-07-25 and integrated into A5/A6/D1/D3. M0-23 (base-plan edit) was resolved by reverting
`plans/implementation_plan.md` to committed `bc12663`; the corrected timing wording remains in
this plan's §Context, and the base-plan wording defect is surfaced to the user rather than
patched into the base here. No items remain in debate.)

END OF DEBATE
