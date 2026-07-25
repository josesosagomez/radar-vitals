# Claude Code loop prompt — M3 cross-review CLOSE-OUT (BR comparator + analysis pre-spec)

> Paste everything below this line into a fresh Claude Code chat to resume and close the M3
> cross-review. Written 2026-07-25, after round 9.

---

An independent reviewer (Codex) has been reviewing two M0-deposit documents —
`notes/comparator_prespec_br.md` (the BR comparator, M3) and `notes/analysis_prespec.md` (the
analysis pre-spec) — via the coordination file `plans/m3_prespec_cross_review.md`. **This review is
NOT starting fresh: it is 9 rounds deep and every one of the 28 findings (M3R-01…M3R-28) is
resolved**, including the two that needed the user (M3R-03 — the §2b evidence-floor extensions,
decided by the user in round 8 and now FROZEN in §2b; M3R-25 — the Masimo citation, verified
against `literature/ref_papers/lab-10169a_master.pdf` in round 9). Read the `DEBATE COMMENTS`
section of the coordination file first — it is the authoritative state.

Your job is to **close the loop**: get Codex's final confirmation pass, process anything new it
raises, and leave the documents ready for the M0 freeze (which you do NOT perform).

### The stakes
Both documents become **binding on every BR/agreement number in the paper** at the M0 deposit —
a public, irreversible Zenodo DOI. A permanently-wrong rule cannot be repaired later; delay only
costs schedule. Lean toward catching anything permanently wrong, even this late.

### Current state (verify, don't trust — read the files first)
- `COMMENTS OF CODEX` still holds Codex's **round-3-era batch** (stale — the debate log shows every
  item in it was subsequently applied and closed in rounds 4–9, with Codex's closes acknowledged).
  The loop is waiting on Codex to confirm the final round-6/7 M3R-23 close and post
  `NO MORE COMMENTS`.
- All content changes so far live in `notes/comparator_prespec_br.md`, `notes/analysis_prespec.md`,
  `notes/protocol.md` (reconciliation edits required by accepted findings: the 20-window count, the
  one-retry rule, the NTP clock-sync gate) and `scripts/derive_br_comparator_evidence.py`.
- Two items are deliberately **left for the user at the deposit gate**, not for you: the Masimo
  document-number discrepancy (back cover prints `LAB-10168A` while the file is `lab-10169a` —
  flagged inside the comparator) and the M0 deposit itself.

### Before you start
Re-read `CLAUDE.md`, then `plans/implementation_plan.md` §M0/§M3, then the coordination file
`plans/m3_prespec_cross_review.md` **in full** (brief + COMMENTS OF CODEX + DEBATE COMMENTS), then
the two documents under review, then `plans/m0_b1_evidence_floor_memo.md` (the user's Option A +
the frozen §2b decision). The frozen HR comparator `notes/comparator_prespec.md` is binding
context, never a write target.

### Hard constraints
- **Writable files:** `notes/comparator_prespec_br.md`, `notes/analysis_prespec.md`, the
  `DEBATE COMMENTS` section of `plans/m3_prespec_cross_review.md`, and — **only** where an accepted
  finding forces a reconciliation — `notes/protocol.md` and
  `scripts/derive_br_comparator_evidence.py`. Nothing else. Never edit Codex's comment text, the
  frozen HR comparator, `HISTORY.md`/`HANDOFF.md` mid-loop, or the writing files.
- **Reference-only design (the §4-mirror rule):** every gate/threshold in the BR comparator must be
  justified from the Masimo trace, the metronome command, or FFT-resolution arithmetic — **never
  from radar output or radar–reference agreement.** If any comment (or your own edit) would leak
  radar agreement into reference admissibility, reject it and say so.
- **Verification is read-only** for the radar side: reading files, `git log`/`git show`,
  `conda run -n radar-vitals python -m pytest tests/ -q` if a claim depends on suite state.
  You MAY run the deterministic, reference-only `scripts/derive_br_comparator_evidence.py` to
  verify the §1/§2.3 numbers (it reads the Masimo CSVs + `run_metadata.json` only). You may NOT
  run capture, replay, or any radar-pipeline script.
- **No invented citations.** Where a source file exists in `literature/`, verify against the PDF;
  where it doesn't, the citation carries an explicit user-verifies-at-deposit note (M3R-25 pattern).

### Processing (same protocol as before)
Findings arrive as `### M3R-NN [Blocking|Should-fix] — <area>` with
`ISSUE / AUTHORITY / WANTED / REVERSIBILITY / ESCALATE`; IDs are permanent and new ones continue
the numbering (M3R-29+). For each: **verify before evaluating** (re-check the cited section,
formula, or script output yourself), then AGREE (apply + propagate + respond), DISAGREE (rebut
with something checkable), or PARTIAL. Propagate every applied change across both documents (and
the protocol/evidence script if touched) — the §1 agreement model, §2 floor, §6 dispositions and
§7 grid are tightly cross-referenced, and the comparator mirrors several of them. Nothing is
silently dropped. **Hard cap: 3 responses from you per comment**, then
`STATUS: ESCALATED — 3 rounds exhausted, awaiting user decision`. Several items already sit at
that cap — do not reopen them beyond acknowledging Codex's closes.

### Escalate rather than fix
- **Frozen HR comparator content** (`notes/comparator_prespec.md`) →
  `STATUS: ESCALATED — frozen comparator content`.
- **The user's recorded decisions** — Option A (§2a) and the round-8 §2b package (≥8/10 study-wide
  floor, symmetric zero-window handling, no automatic whole-subject exclusion, descriptive-only
  precision-miss) are **frozen by user decision**. A comment reopening them →
  `STATUS: ESCALATED — requires user decision`.
- **Ethics / human-subjects scope** → `STATUS: ESCALATED — user/ethics board decision`.

### Polling
Codex re-reads every ~3 minutes. After each pass, wait (`sleep 180` or a file-watch), then re-read
`plans/m3_prespec_cross_review.md` **from disk** (Codex edits the same file). Loop until
`COMMENTS OF CODEX` contains the exact string `NO MORE COMMENTS` **and** every `DEBATE COMMENTS`
item is resolved/conceded/escalated. If nothing changes for 10 consecutive polls (~30 min), stop
and report.

### When the loop closes
1. Tidy the coordination file per the M2 pattern: inbox holds only Codex's closing note; resolved
   threads compacted to a resolution table in `DEBATE COMMENTS` (the verbatim record is in the
   session transcripts); only escalated items (if any) left open.
2. Update the **status headers** of `notes/comparator_prespec_br.md` (currently "awaiting
   cross-review") and, if it carries one, `notes/analysis_prespec.md`: cross-review **complete**
   (date, pointer to the coordination file), **ready for the M0 freeze — NOT yet frozen**. Do not
   freeze, do not deposit, do not publish anything — the M0 deposit is the user's irreversible act.
3. Summarise in chat: findings by severity and how each closed; every escalated/deposit-gate item
   with the decision needed and your recommendation (carry forward: the `LAB-10168A`/`lab-10169a`
   document-number check); anything the documents still leave risky; a proposed `HISTORY.md` entry
   **for the user to approve — do not append it yourself**, and offer the matching `HANDOFF.md`
   rewrite + commit once approved.
4. Remind the user of the M2 tie-in: with the comparator frozen at M0, the reprocessed post-fix BR
   (the 2026-07-25 replay NPZs) can be scored to close M2 done-when #5.
