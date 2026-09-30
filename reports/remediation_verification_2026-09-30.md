# Remediation verification — 2026-09-30

**Question:** were the owner decisions from `reports/discrepancy_audit_2026-09-29.md` taken, and were
the problems fixed?

**Scope:** branch `codex/discrepancy-remediation`, commits `4440945..6521787` (base `e6d055c`), in the
separate worktree `C:\Users\josemsosag\.codex\worktrees\discrepancy-remediation\vitals_radar_3`. Also
covered: the original checkout (`vital_signs_own_v13` @ `e6d055c`), which still holds the untracked
MATLAB/TI-generator work.

**Method:**
- Four independent read-only reviewers checked every audit item (H-1…H-11, M-1…M-24, L-1…L-12,
  T-1…T-3) against the branch: records and manuscripts; radar DSP; evaluation and firewall; code
  health, the full test run and merge readiness.
- The high-impact claims were then re-checked directly. These are marked **(checked)**.
- No project file was modified. No sealed reference CSV was opened.

**Status words:**
- **FIXED:** verified in code or text.
- **PARTIAL:** the core is fixed but part of the audit's fix is missing.
- **NOT FIXED:** unchanged.
- **RESOLVED:** closed by an owner fact rather than by code.
- **REGRESSED:** worse than before.

---

## 1. Bottom line

1. **All five owner decisions were taken** on 2026-09-29 and recorded in `HISTORY.md` and the audit
   addendum. A sixth owner fact was added on 2026-09-30 (the four preparation directories).
2. **The serious code defects are fixed correctly, but only on an unmerged and unpushed branch.**
   The fixed defects are:
   - unsafe peak refinement;
   - the relabelling tracker;
   - the ungated scoring paths;
   - the missing subject-clustered LoA;
   - the 120 s settle rule;
   - the single guarded reference entry point.

   Full suite on the branch: **3189 passed, 17 skipped, 0 failed**. The original checkout
   (`vital_signs_own_v13`) still contains **none** of these fixes, and the branch does not exist on
   `origin` **(checked)**.
3. **Remediation covered the HIGH items and stopped part-way through records.**
   - HIGH: 4 of 11 fully fixed, 7 partial, none untouched.
   - MEDIUM: 5 of 24 fixed or resolved, 4 partial, 15 not fixed.
   - LOW: none fixed, one regressed.
   - Most MEDIUM and LOW items were never in the remediation plan, and nothing records them as
     deferred.
4. **No recorded result has been recomputed** (plan Milestone 5 not started). The M1 headline
   (MAE 2.77 bpm, coverage 11/120) and the M8/M9 production-arm numbers were produced by the old
   `eca_ahet_v1` estimator and are still quoted as current.
5. **The remediation introduced new issues:**
   - a HANDOFF that no longer works as a resume point;
   - a test that pins the production config's hash;
   - a firewall audit test too narrow to catch new bypasses;
   - one scoring route (M8 CLI) that still bypasses the guarded loader **(checked)**.

---

## 2. Owner decisions

| # | Decision recorded (HISTORY 2026-09-29) | Reflected everywhere? |
|---|---|---|
| OD-1 | Settle floor is **120 s**. P001–P006 natural (60 s) remain captured records, classified as **protocol deviations**. D-OWN-7 is kept; `m2_sidecar_scaffold` semantics are to be ported, then the branch retired | **Partial.** Enforced in `src/protocol.py` and HANDOFF. **Missing from `notes/protocol.md`**: the SETTLE CRITERION still has two limbs and no 120 s floor **(checked)**. `analysis_prespec.md` does not mention the deviation. `m2_sidecar_scaffold` is still present locally and on origin, with no retirement entry |
| OD-2 | Approved ethics scope is **15 new participants**; the "existing 10" sentence is non-operative | **Yes.** A header note was added above the unchanged as-submitted body |
| OD-3 | The 2026-07-30 clutter A/B **is** the Track 0 result; clutter removal stays off | **Partial.** HANDOFF updated. `approach.md:47` still says "NEVER CONCLUDED", `protocol.md:114` is unchanged, and `venue_iotj.md:182` now points at a HANDOFF section that no longer exists |
| OD-4 | **P003 was not viewed**, so it did not inform Track 0 | **Yes** (HISTORY, HANDOFF, audit addendum) |
| OD-5 | **`vital_signs_own_v13`** is the integration branch; `master` is not a target | **Yes** |
| (new) | 2026-09-30: P003_paced, P007, P009 and P010 were **never started**. They are preparation folders only, all remain `planned`, and P010's `P001_natural_settle.json` is a misnamed placeholder | **Yes.** This also corrects the audit (§6) |

---

## 3. HIGH items

| ID | Status | What was done | What remains |
|---|---|---|---|
| H-1 Settle floor | **PARTIAL** | Shared `MIN_SETTLE_S=120`. New natural/paced captures <120 s are rejected (119.999 rejected, 120 accepted, recovery exempt, all tested). Historical manifests load with `protocol_compliant=false` / `settle_below_120s`. SCORING-mode v3 parsing refuses them | (a) The v2 manifest route (`src/m4/manifest.py`) has no settle rule. (b) Minting a scoring capability does not check `protocol_compliant`. (c) The limb-3 text was not ported to `notes/protocol.md`. (d) `preflight.py:80` validates historical sessions as NEW_CAPTURE (fails closed, but semantically wrong) |
| H-2 Track 0 | **PARTIAL** | Owner decision recorded; Track 0 removed from HANDOFF | The dated correction notes in `approach.md`, `protocol.md` and `venue_iotj.md` were not added. No HISTORY line says the 08-25/26 entries missed the 07-30 result |
| H-3 Pilot table | **PARTIAL** | The table is withdrawn from both manuscripts | Numbers from the same live-demo sessions remain **(checked)**: "10–46%" coverage at `JOURNAL_PAPER.md:60, 465, 483` and `THIRD_CHAPTER.md:242, 1094`; "MAE around 0.2–0.5 bpm … from four subjects" at `JOURNAL_PAPER.md:81-86`; "10% / 46% / 20%" at `THIRD_CHAPTER.md:604` |
| H-4 Ahmed citation | **FIXED** | Ahmed is `[R22]` in both lists. The reviewer's closure script found 22/22 references closed; every `[R1]` is Tang | Nit: `THIRD_CHAPTER.md:1039` still lists [R22] under "not yet placed" |
| H-5 Peak refinement | **PARTIAL** (core FIXED) | `refine_peak_hz_safe` (`src/vitals.py:214`) enforces strict full-spectrum local maximum, concavity, \|δ\| ≤ 0.5 and in-band, and falls back to the bin centre with a reason code. The reviewer's synthetic checks passed. All three ECA+AHET call sites are migrated, under the new estimator ID `eca_ahet_safe_refine_v2`; `eca_ahet_v1` is kept for historical artifacts. A pre-fix diagnostic was committed and is radar-only | `scripts/diagnose_signal_presence.py:143` still calls the unsafe `refine_freq_hz` **(checked)**. `vitals.py:1339` (dead `run_pipeline_locked`) is also unmigrated. Step 6 NPZ/CSV do not persist refinement evidence. Edge-case tests (plateau, NaN, boundary, nonuniform grid) exercise only the diagnostic's copy of the helper, not the production helper. The audit's end-to-end test through `estimate_rate_from_phase` is missing |
| H-6 ECA semantics | **FIXED** | Production mode and numbers unchanged. A test pins in-band orders 3–6 as skipped and below-band 1–2 as projected at f_r = 0.30. `eca_mode: none` added; unknown modes fail closed. Docs and manuscripts now say "below-band projection + AHET". Guard mode not promoted | Minor: the test pins only one f_r; there is no `exp_eca_modes` none-arm config |
| H-7 Reference firewall | **PARTIAL** | `src/reference_access.py` plus `reference_registry/development_references_v1.json`. Development reads are bound to an exact path and SHA. Protected paths are rejected both lexically and after resolution. Prospective reads require a `ScoringAuthorization` and delegate to `guarded_reference_bytes`, which now has a production caller. `diagnose_signal_presence --all` reads the registry only (no P00x) | (a) **The M8 score CLI bypasses the guarded loader:** `m8_ahmed_transfer.py:430` does not pass `require_production_provenance`, so `execute_score(official=False)` hashes and loads the CSV directly **(checked)**. Its reach is limited to the development registry. (b) `m9_kotte_score.load_reference_strict` is the non-official default loader. (c) The bypass audit test (`tests/test_reference_access.py:220, 250`) checks only 9 named scripts and only `load_masimo`; it would miss `read_csv`, `load_masimo_bytes(...)` or a new glob. There is no symlink/junction test. (d) **Plan item M1.4 was never done:** the TI generator has no prospective-path refusal and `notes/matlab_export.md` still uses P001/P003 examples **(checked)**, yet HISTORY says Milestone 1 "items 1–4" were done |
| H-8 Step 6 tracker | **FIXED** | The tracker writes only `tracker_*` fields plus `tracker_non_causal=true`. Primary AHET fields are invariant. Summaries are split `ahet_*` / `tracker_*`. Earlier segments are kept on restart; the reviewer's synthetic checks passed | Minor: when the tracker is disabled, `tracker_non_causal=False` reads as "causal". One jump-threshold test lost its purpose |
| H-9 Masimo-tuned gates | **PARTIAL** | Disclosed in both manuscripts and in HISTORY | Config comments still say "Validated … copied verbatim" (`live_demo_config.yaml:3, 90`) and "Best safe combo … MAE=1.23" (step_6 configs). The live-config comments were reverted because a new test pins that file's hash (see N-2) |
| H-10 Bland–Altman | **PARTIAL** (math FIXED) | `src/agreement.py::arm_loa` implements prespec §1. The reviewer independently recomputed the hand example (19/9, 38/9, 205/12, 11/6, 463/66) and re-implemented the 10,000-replicate bootstrap (seed 20260725), matching to 1e-15. All mandatory diagnostics are present. M9 LoA fields were renamed to descriptive. `plot_bland_altman.py` now fails closed | `arm_loa` has **no caller** (plan item 3.4 not done). When bootstrap failure is >5%, `loa_low/high_bpm` stay numeric while `descriptive_only=True`, contradicting the prespec and HISTORY; for S ≤ 3 this always happens. The lag-1 diagnostic pairs admitted windows across gaps (k=3 with k=7) |
| H-11 Ethics text | **FIXED** | 5-line header note; body unchanged (diff verified) | — |

---

## 4. MEDIUM items

### Evaluation and reference

| ID | Status | Notes |
|---|---|---|
| M-1 `score_offline` comparison gate | **FIXED** (code) | Non-admitted windows now give NaN, with a test. The 5 contaminated run directories are not annotated as superseded |
| M-2 `simulate_bin_policy` gate and frame-0 | **FIXED** (code) | The 2026-08-04 bin-policy tables are **not re-derived** |
| M-3 legacy M8 scorer | **FIXED** (code, untested) | `k ≥ 1` universe and guarded discovery. There is no test for k ≥ 1, and it has not been re-scored |
| M-4 reference definitions | **PARTIAL** | Steps 5/6 are radar-only by default. `masimo.reference_pr` still falls back to low-PI rows (`masimo.py:159`). The Step 5/6 mean-based references carry no `legacy_mean` stamp |
| M-5 subject identity | **PARTIAL** | The new reference registry records A–D. The M4/M8 capture registry still says "apparent single subject". No reconciling HISTORY entry; natural labels are not marked "reference-inferred" |
| M-6 m4/m2 enum divergence | **NOT FIXED** | m4 `Arm` still lacks RECOVERY and `WARMUP_LOW_CONFIDENCE` remains. v2 manifests for P-subjects are not rejected, which is also the H-1 gap |
| M-7 reproducibility chain | **NOT FIXED** (plan assigns it to Milestone 5) | `--gate` and `--radar-parent` default to LATEST, and `--authorization` defaults to the old file. `m8_ahmed_all_bins` reads LATEST without a digest check. In practice old parents now fail closed on the estimator-ID mismatch |

### Acquisition and records

| ID | Status | Notes |
|---|---|---|
| M-8 capture attempts | **RESOLVED** by owner fact (2026-09-30) | No attempts exist, so no ledger was needed. Session-matching settle-evidence paths are enforced at preflight, sealing and registration. Residual: only the declared path string is checked, not the evidence file's own session identity |
| M-9 P001–P006 capture record | **NOT FIXED** | No HISTORY entry with settle, duration, posture and distance per session (CLAUDE.md §3.6; plan Milestone 4 item 5) |
| M-10 CRLF | **FIXED on branch, residual in original** | The branch is all LF, and `tests/test_repository_eol.py` was added. The original checkout still has 20 CRLF files plus a mixed-ending `HISTORY.md`. A fast-forward rewrites only 3 of them, so **the new EOL test will fail in the original checkout after merging** until the rest are renormalized |

### Signal processing

| ID | Status | Notes |
|---|---|---|
| M-11 AHET codes 2/6 | **NOT FIXED** | `vitals.py:1062, 1078` still gate the same `ratio_db` |
| M-12 live vs offline wording | **NOT FIXED** | The comments were added and then reverted because of the config hash pin (N-2) |
| M-13 a–e design choices | **NOT FIXED**; not recorded as deferred | Impulse clip 1.5 rad, absolute prominence 3.0, `harmonic_max_hz: null`, warmup +1000, and the hardcoded f_r gate are all unchanged |
| M-14 offline-step latent bugs | **NOT FIXED** | Steps 2–4 have no diff; interval parsing and Step 3 concatenation are unchanged |
| M-15 live frame drops | **NOT FIXED** | `live_demo.py:398` still has `except queue.Full: pass`; no decoder parity test |

### Documentation, MATLAB and tests

| ID | Status | Notes |
|---|---|---|
| M-16 stale notes | **NOT FIXED** | `approach.md:628`, `capture_inventory.md:146`, `dca1000_protocol.md:310`, `note_candidate_ranking.md:3` |
| M-17 stale manuscript facts | **NOT FIXED** | Still present: 796/797 tests, "10-subject", "one subject", "~31 GB / 20-session", the JBHI recommendation vs the chosen IoT-J, the missing `fig_range_bin_mislock.py`, and "live smoke test is the first blocker" |
| M-18 milestone numbering | **NOT FIXED** | — |
| M-19 affirmative forbidden wording in plans | **NOT FIXED** | `implementation_plan.md:341, 350, 379`, `m8_step1b_ahmed_transfer.md:18`, `m4_offline_harness.md:103` |
| M-20 stale headers/pointers | **PARTIAL** | The HANDOFF dev-hash pointer is fixed. The plan status headers and the bin-drift "HANDOFF §3.1" pointer are unchanged |
| M-21 MATLAB files undocumented | **NOT FIXED** | `export_session_to_ti_mat.m` and `view_recording_2.m` are still not recorded. No CLAUDE.md §6 review of the range-FFT code |
| M-22 MATLAB/TI-generator correctness | **NOT FIXED** | Files unchanged since 08-31 / 09-08: `rawFileName == processedFileName`; no output-dir guard; Overwrite deletes first; no completion marker; `frame_validity` ignored; `rawSha256` copied, not recomputed |
| M-23 RAM-dependent test | **NOT FIXED** | It passed this time only because enough RAM was free |
| M-24 skip counts | **PARTIAL** | HANDOFF states the clean-clone count (17) only. Correction: the audit's request for skip markers was unnecessary, since they already existed |

---

## 5. LOW items

None of L-1…L-11 was in the remediation plan, and none is fixed.
- **L-7 regressed.** There are now five parabolic interpolators; the diagnostic script holds a verbatim
  copy of the production helper. There are 17 `sha256` file helpers.
- **L-12 is only partly addressed.** The integration target is decided, but the new branch is
  unpushed, `m2_sidecar_scaffold` is not retired, `stash@{0}` is still present, the local-only
  branches remain, and there is no note on the "s" / "solv3" commits.
- **L-10 is owner-controlled** and unchanged: the CLAUDE.md layout and wandb in `.mcp.json`.

---

## 6. Threats to validity and corrections to the audit

**Threats to validity:**
- **T-1** (the stationarity gate works against the recovery arm): not addressed or recorded.
- **T-2** (no constant-median null in the canonical scorer): not addressed.
- **T-3** (approximate frame-0): already recorded in prespec §7. Two scripts still use
  `start_wall_utc` directly.

**The audit was wrong or imprecise in three places:**
- **M-8 was overstated.** The four directories were preparation only, not capture attempts.
- **H-5 was mis-sized in both directions.** The pre-fix measurement on development data found
  756 refinement calls. 39 fell back under the safe rules, all at the second-harmonic step. The legacy
  offset reached 209 bins, on a rejected candidate. Only **one accepted estimate** changes: sweep
  k=4, by **26.48 bpm** (δ = −52.96 bins; 52.96 × 1/30 Hz × 0.25 × 60 = 26.48). First-pass refinements
  were identical to the legacy ones. So the error is rare, but far larger than the audit's ≈4.8 bpm
  example.
- **M-24:** the skip markers already existed.

---

## 7. New issues introduced by the remediation

| # | Sev | Issue | Fix |
|---|---|---|---|
| N-1 | HIGH | **HANDOFF is no longer a working resume point** (CLAUDE.md §10.1). §3 names no next action and never mentions Milestone 4 items 5–6, Milestone 5, the unmerged state, or the open items above. The rewrite also dropped true state: 9/45 captured with labels sealed; IoT-J; the P006 final-evaluation warning; "pass the latest registry"; no recapture after a Stage-1 failure; the hash chain; the 2.93 GiB MATLAB cube warning; the `--replay-fast elapsed_s` landmine **(checked)**. Inbound "HANDOFF §5" pointers (`analysis_prespec.md:589`, `comparator_prespec.md:179`, `src/m4/window_grid.py:10`, `plan_eca_forbidden_zone.md:406`) now point at the wrong content | Rewrite HANDOFF with the dropped state, an explicit next action and the open-item list |
| N-2 | MED | `tests/test_peak_refinement_artifact.py:37` compares the artifact's config hash with the **current** `live_demo_config.yaml` **(checked)**. Any future config fix (M-12, L-2, M-13) fails the suite; this already forced the M-12 comments to be reverted | Compare against `git show 4280e34:scripts/live_demo_config.yaml` instead |
| N-3 | MED | The M8 score CLI runs non-official scoring, which hashes and loads the reference directly (H-7a) **(checked)**. HISTORY says scorer pre-hashing was removed | Pass `require_production_provenance=True`, or route non-official scoring through `reference_access` |
| N-4 | MED | HISTORY overstates Milestone 1 ("items 1–4"); item 4 was never done | Append a correcting HISTORY entry; implement the TI-generator guard and the MATLAB example change in the original checkout |
| N-5 | MED | The pre-fix M8/M9 manuscript tables (`JOURNAL_PAPER.md` §4.4, `THIRD_CHAPTER.md` §10.3, including the "current-production rerun" lock rows) are not flagged as legacy or pending re-derivation | Add "computed with `eca_ahet_v1`; pending re-derivation" labels |
| N-6 | MED | HISTORY:12096 cites "the 2026-08-31 entries above", which are absent on the branch (they exist only in the original checkout) | Resolved by the merge procedure in §9 |
| N-7 | LOW | `src/agreement.py:58` says "prespecified" (a variant of a CLAUDE.md §4 forbidden term); the new manuscript text says "frozen estimator" | Use "specified" |
| N-8 | LOW | The official M9 path no longer runs `load_reference_strict`'s raw-header, duplicate-column and parser-diagnostic checks | Move those checks into `reference_access` |
| N-9 | LOW | Readability: `arm_loa` is about 290 lines with no equations in its docstrings; the custom REML has no cited derivation; `estimate_rate_from_phase` is now 678 lines with 4 duplicated result dicts; the diagnostic monkeypatches and inspects stack frames; `BOOTSTRAP_SEED = 20_260_725` | Split functions; add equation docstrings and citations |
| N-10 | LOW | The committed 527 KB JSON artifact records Windows backslash paths (platform-dependent provenance); `git diff --check` flags trailing whitespace at `plans/discrepancy_remediation_2026-09-29.md:79`; the plan's validation commands name nonexistent `tests/test_vitals.py` and `tests/test_temporal_tracker.py`, and `tests/test_documentation_claims.py` (Milestone 4 item 6) was never written **(checked)** | Use POSIX paths in provenance; correct the plan's commands; write the doc-claims test |
| N-11 | DECISION | AHET accepted sweep k=4 with a second-harmonic bin that is **not a local maximum**. The fallback bounds the frequency, but `ratio_db` still uses that edge magnitude | Owner decides whether refinement reason 6 should reject the candidate. The threshold must not be tuned on Masimo |

---

## 8. Numbers that are now stale but still quoted

These were produced by `eca_ahet_v1` or by ungated scoring, and no HISTORY entry lists them as
superseded:

| Number | Where it is still quoted |
|---|---|
| M1: MAE 2.77 bpm (n = 9), coverage 10.94% / 9.17% | `plans/plan_codex_milestones.md:7, 148` |
| "10–46%" coverage | `JOURNAL_PAPER.md:60, 465, 483`; `THIRD_CHAPTER.md:242, 1094`; `notes/protocol.md:134`; `plans/implementation_plan.md:84, 97, 362, 522` |
| 10% / 46% / 20% | `THIRD_CHAPTER.md:604` |
| M8 "current-production rerun" lock rows | `JOURNAL_PAPER.md` §4.4, `THIRD_CHAPTER.md` §10.3 |
| 2026-08-04 bin-policy tables; legacy M8 coverage 0.301 | HISTORY only |

---

## 9. Integration path (branch → `vital_signs_own_v13`)

A git-level merge is a **clean fast-forward** (`merge-tree`: no conflicts). The original checkout's
working tree blocks it until these steps are done:

1. **Push `codex/discrepancy-remediation` to origin.** It currently exists only on this machine.
2. **In the original checkout, move aside the untracked `reports/discrepancy_audit_2026-09-29.md` and
   `plans/discrepancy_remediation_2026-09-29.md`.** The branch versions are newer (audit with the
   09-30 addendum; plan revision 3).
3. **Set aside the uncommitted `HANDOFF.md` / `HISTORY.md` edits, then fast-forward.** Re-append the
   two 2026-08-31 MATLAB entries **after** the branch's last entry as a late-recorded append;
   inserting them earlier breaks append-only order. Drop the duplicate 09-29 owner-decisions entry,
   which is identical on both sides.
4. **Re-add the MATLAB state to the rewritten HANDOFF**, and commit `.gitignore`'s
   `matlab_exports*/` rule. That rule is absent on the branch; without it, P006 config pairs could be
   staged.
5. **Renormalize the remaining 18 CRLF files**, or `tests/test_repository_eol.py` fails.
6. **Before committing the MATLAB work:** do N-4 / H-7(d) (TI-generator guard, development-capture
   examples) and M-22, then run the independent range-FFT review (M-21).

---

## 10. Recommended next actions

1. **Finish Milestone 4.** Rewrite HANDOFF (N-1) and fix N-2 so config comments can be corrected.
   Then:
   - remove the leftover pilot numbers (H-3);
   - add the H-2 dated notes and the OD-1 limb-3 text;
   - append HISTORY entries for M-5, M-9, M-21, N-4 and the §8 superseded-number list;
   - write `tests/test_documentation_claims.py`;
   - fix the M-17 manuscript facts.
2. **Close the firewall gaps:**
   - N-3 (M8 CLI) and a wider bypass audit test with a symlink test;
   - the TI-generator guard;
   - check `protocol_compliant` at capability minting;
   - reject v2 manifests for P-subjects (M-6).
3. **Integrate** (§9) and push.
4. **Milestone 5, re-derivation under the v2 estimator chain:**
   - new gate, new authorization filename, reference-blind radar parent;
   - required, digest-checked `--gate`, `--authorization` and `--radar-parent` (M-7);
   - re-score M1, the bin-policy tables and the M8/M9 production arms;
   - wire `arm_loa` in and null its descriptive-only LoA;
   - add the T-2 null baseline.
5. **Decide or record as deferred:** M-11, M-13 a–e, N-11, T-1. Then work through M-14, M-15, M-23
   and the LOW list.

---

## 11. Not verified

- The "independent review READY" verdicts and intermediate pass counts in HISTORY. No review
  artifacts are committed; only the final full-suite result was reproduced.
- The TI rawDataReader's handling of `processedFileName` (`rawDataReader.m` is not in the repo).
- Whether any real symlink or junction exists that `reference_access` would need to reject. The
  logic was reviewed, but it has no test.
