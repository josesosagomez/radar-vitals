# Handoff — finish the discrepancy remediation, then integrate

> Read this and `CLAUDE.md` first. **State verified 2026-09-30.** `HISTORY.md` is append-only; this
> file is the current resume point.
>
> **Two copies until integration.** This file is kept identical in both checkouts named in §2; if
> they differ, reconcile before acting. Every pointer in §6 says which checkout holds the file.
>
> **`HISTORY.md` differs between checkouts until integration.** The worktree copy is authoritative
> for the remediation entries. The original-checkout copy holds the entries the worktree lacks
> (§3 step 2).

## 1. Project snapshot

**Goal:** estimate a seated person's heart rate in real time from a 76–81 GHz FMCW radar.
- **Hardware:** TI IWR1642BOOST plus a DCA1000EVM for raw ADC capture. The radar sits at chest
  height, facing the chest, with the subject at 0.8–1.4 m.
- **Reference:** a Masimo MightySat fingertip pulse oximeter, exported as a 1 Hz CSV.
  - `Beats / min` (PR) is the heart-rate reference.
  - It is aligned only by the integer Unix-epoch `Timestamp`.
  - `Perfusion Index` gates reference quality.
- **Paper-grade results** are MAE, RMSE and subject-clustered Bland–Altman against Masimo PR. They
  count only on sessions with real HR dynamic range; the seated recovery arm exists to supply it.
- **Target venue:** IEEE IoT-J, regular issue (`notes/venue_iotj.md`).

## 2. Current state

### Two checkouts — know which one you are in (`git status -sb`)

- **Original checkout:** `C:\Users\josemsosag\Desktop\vitals_radar_3`, branch
  **`vital_signs_own_v13`** @ `e6d055c`, which is the owner-designated integration branch.
  - It has **none** of the remediation code.
  - It holds uncommitted work:
    - `HISTORY.md` with four entries the worktree lacks: two 2026-08-31 MATLAB entries, a duplicate
      of the 2026-09-29 owner-decisions entry, and 2026-09-30 "WST collision-abstention planning";
    - this `HANDOFF.md`;
    - `.gitignore` `matlab_exports*/`;
    - the WST research note added as `notes/approach.md` §8.1;
    - the untracked MATLAB / TI-generator files (§5, §6);
    - the untracked `reports/remediation_verification_2026-09-30.md`;
    - the untracked `plans/iot_edge_*.md` (seven files) plus `plans/wst_collision_gate_plan.md`.
      These are not independently reviewed and not part of the discrepancy remediation (§5).
  - It also holds all data: `data/raw/prospective/` (9 sessions) and `results/live_demo/` (the 8
    development captures + 9 prospective live folders).
- **Remediation worktree:**
  `C:\Users\josemsosag\.codex\worktrees\discrepancy-remediation\vitals_radar_3`, branch
  **`codex/discrepancy-remediation`**, built on `e6d055c`. Commits after `6521787` record the
  2026-09-30 verification, the config-pin fix and the records clean-up.
  - **Pushed to `origin` (2026-09-30) and tracking `origin/codex/discrepancy-remediation`; not
    merged.** Commit and push each later step so the remote stays current.
  - It has no `data/` or `results/live_demo/`.
  - It needs two gitignored source PDFs under `literature/ref_papers/` for the M8/M9 provenance
    tests. They are present there and hash-matched to the original.

### Acquisition — paused; labels sealed; nothing scored

- **Registry:** `cohort_registry/registry_v010.json`, revision 10, SHA-256
  `20604fb49a397e86f82f1e0773d7a152d5664a8af53e5eb80a7511f7b765312f`. The v001→v010 chain is intact.
- **9 of 45 sessions captured, 36 planned:** P001 natural+paced, P002 natural+paced, P003 natural,
  P004 natural, P005 natural+paced, P006 natural. **No recovery session yet.**
- **All 15 subjects are `label_state: sealed`.** P001–P005 are `representation_validation`;
  P006–P015 are `final_evaluation`.
- **No agreement metric exists for any prospective session.**
- **Settle deviation:** the six natural sessions P001–P006 recorded **60 s** settle. The rule is
  **120 s** (owner decision D-OWN-7, reaffirmed 2026-09-29). They stay captured, immutable
  **protocol deviations**; the three paced sessions recorded 120 s.
- **P003 paced, P007 natural, P009 natural and P010 natural were never started** (owner, 2026-09-30).
  Their folders in `m2_capture_work/` are preparation placeholders; the registry keeps them
  `planned`.

### Remediation — partially done on the branch, verified 2026-09-30

Full per-item status is in `reports/remediation_verification_2026-09-30.md`.
HIGH items: 4 of 11 fully fixed, 7 partial. MEDIUM: 5 of 24 fixed or resolved. LOW: none fixed, and
L-7 regressed (there are now five parabolic interpolators).

**Implemented and independently verified on the branch:**
- **Settle rule.** `src/protocol.py` `MIN_SETTLE_S = 120.0`. New natural/paced captures <120 s are
  rejected; recovery is exempt. Historical manifests load as `protocol_compliant=false` /
  `settle_below_120s`, and scoring-mode v3 parsing refuses them.
- **Single reference boundary.** `src/reference_access.py`:
  - development reads require the exact path and SHA from
    `reference_registry/development_references_v1.json` (8 captures, subjects A–D);
  - prospective reads require a `ScoringAuthorization` and go through `guarded_reference_bytes`.
- **Safe peak refinement.** `src/vitals.py` `refine_peak_hz_safe` at the three ECA+AHET call sites,
  under new estimator ID **`eca_ahet_safe_refine_v2`** (`src/window_pipeline.py:47-53`). The old
  `eca_ahet_v1` identity is kept for historical artifacts only. The pre-fix measurement
  (`reports/peak_refinement_diagnostic_2026-09-29.json`, radar-only, commit `4280e34`) found 39
  unsafe cases in 756 calls. It changes one accepted estimate: sweep k=4, by **26.48 bpm**.
- **ECA semantics.** Production `skip_forbidden_harmonics_v1` is unchanged. It projects only
  below-band harmonics and does **no in-band ECA**. `eca_mode: none` was added, and unknown modes
  fail closed.
- **Step 6 tracker.** It writes only `tracker_*` fields with `tracker_non_causal=true` and never
  overwrites AHET `hr_valid`.
- **Admission gate.** Enforced in `score_offline` comparisons, `simulate_bin_policy`, and legacy M8
  (k ≥ 1).
- **Agreement.** `src/agreement.py::arm_loa` implements `notes/analysis_prespec.md` §1. Its math was
  independently reproduced. **It has no caller yet.**
- **Manuscripts.** Pilot table withdrawn; Ahmed is `[R22]`; the ethics scope note was added; the
  Masimo-selected gate origin is disclosed.
- **Config comments.** The Masimo-selected origin of the heart gates is also stated in
  `scripts/live_demo_config.yaml`, `experiments/exp_eca_modes/config_guard_v1.yaml` and
  `steps/step_6/config_hop1*_safe.yaml`, replacing "Validated … copied verbatim". The production ECA
  comment now states the below-band-only semantics. Config values are unchanged.
  `tests/test_peak_refinement_artifact.py` checks the artifact against the config as committed at
  `4280e34`, so configs can be edited again.
- **Records clean-up (2026-09-30), independently reviewed:**
  - withdrawn pilot figures removed from both manuscripts;
  - stale facts updated (15 participants × 3 sessions, test counts, raw size, IoT-J venue, missing
    figure script);
  - "pending re-derivation" labels on the tables that depend on `eca_ahet_v1`;
  - the 120 s limb and the P001–P006 deviation in `notes/protocol.md` and `notes/analysis_prespec.md`
    §6 item 3a;
  - Track 0 resolution in `notes/approach.md` and `notes/venue_iotj.md`;
  - HISTORY records for the P001–P006 capture protocol and the development subject-identity
    mismatch;
  - every section-numbered "HANDOFF §N" reference repointed to a durable owner;
  - `tests/test_documentation_claims.py`.

### Tests

| Where | Result | Source |
|---|---|---|
| Worktree | **3202 passed, 17 skipped** | after the 2026-09-30 records clean-up (HISTORY 2026-09-30). The 13 tests beyond the earlier 3189 are `tests/test_documentation_claims.py` |
| Original checkout | 3144 passed | `reports/discrepancy_audit_2026-09-29.md`. The 2026-09-29 audit run also observed 5 skipped, and the 4 passes beyond the tracked 3140 are the untracked TI-generator test; neither detail is recorded in HISTORY. Fewer skips than the worktree because local `results/` artifacts exist |

## 3. Active task / next steps

Do these in order. Item 1 is on the **worktree branch**; commit and push each step with a HISTORY
append. The 2026-09-30 verification, config-pin fix and records clean-up are recorded in HISTORY.

1. **Close the firewall gaps.**
   - `scripts/m8_ahmed_transfer.py:430` calls `run_score_stage` without
     `require_production_provenance`. That defaults to False (`src/m4/estimator_scoring.py:2008`)
     and is passed on as `official=`. With `official=False`, the reference is hashed and loaded
     outside `reference_access` (`:1500-1506`). Route it through the guard.
   - Widen `tests/test_reference_access.py` beyond its 9 named scripts and `load_masimo`, and add a
     symlink/junction test.
   - Migrate `scripts/diagnose_signal_presence.py:143`, which still calls unsafe `refine_freq_hz`.
   - Reject v2 manifests for `P\d{3}` sessions, and check `protocol_compliant` when a scoring
     capability is minted.
2. **Integrate into `vital_signs_own_v13`.** The git merge is a clean fast-forward; the original
   checkout's working tree is what blocks it.
   1. In the original checkout, move aside the untracked `reports/discrepancy_audit_2026-09-29.md`,
      `plans/discrepancy_remediation_2026-09-29.md` and `reports/remediation_verification_2026-09-30.md`.
      The branch now tracks all three; its audit and plan are newer.
   2. Set aside the uncommitted `HANDOFF.md`, `HISTORY.md` and `notes/approach.md` (the branch also
      changes `approach.md`), then fast-forward. Re-apply the WST §8.1 note to `approach.md`.
   3. Re-append the original checkout's two 2026-08-31 MATLAB entries and the 2026-09-30 WST entry
      **after** the branch's last entry, as late-recorded appends. Say in the appended text that the
      branch's 2026-09-29 "entries above" reference points to these. Drop the duplicate 09-29
      owner-decisions entry.
   4. Commit `.gitignore` `matlab_exports*/`.
   5. Renormalize the CRLF files. On 2026-09-30 there were 20 `w/crlf` plus a `w/mixed`
      `HISTORY.md`; recount after the fast-forward. Otherwise `tests/test_repository_eol.py` fails.
   6. Before committing the MATLAB work, do plan item M1.4, the M-22 fixes, and an independent
      range-FFT review (CLAUDE.md §6).
   7. Push `vital_signs_own_v13`.
3. **Milestone 5 — recompute under `eca_ahet_safe_refine_v2`.**
   - Build a new synthetic gate, a **new authorization filename**, and a reference-blind radar
     parent. Make `--gate`, `--authorization` and `--radar-parent` required and digest-checked.
   - Add an explicit `subject` field to the new capture-registry revision, and relax the
     single-subject role check at `src/m4/capture_registry.py:287`, with a test (HISTORY 2026-09-30).
   - Then re-score M1, the 2026-08-04 bin-policy tables and the M8/M9 production arms.
   - Wire `arm_loa` into the canonical summaries, and null its LoA when `descriptive_only`.
   - Add a constant-median null baseline to `estimator_scoring`.
   - Add a scoring path for the labelled deviation-inclusive sensitivity analysis, if one is wanted.
     None exists: SCORING-mode parsing rejects every deviating session.
4. **Record each open DSP choice as a decision or a deferral.** None is currently recorded:
   - AHET codes 2 and 6 test the same `ratio_db`;
   - impulse clip 1.5 rad/frame;
   - absolute prominence;
   - `harmonic_max_hz: null`;
   - warmup +1000 HR weighting;
   - hardcoded f_r gate;
   - whether a non-peak second harmonic (refinement reason 6) should reject AHET.

   None may be tuned on Masimo.
5. **Remaining record hygiene** (lower priority; none changes a result):
   - stale notes: `notes/approach.md:633` "physical acquisition pending";
     `notes/capture_inventory.md:146, 158` "E/F/G"; `notes/dca1000_protocol.md:310` names
     `scripts/capture.py` (now `steps/step_1/capture.py`); `notes/note_candidate_ranking.md:3`
     "awaiting cross-model review";
   - a "superseded" banner on the `plans/implementation_plan.md` milestone map;
   - dated banners on plans with affirmative timing wording (`plans/implementation_plan.md:341, 350,
     379`, `plans/m8_step1b_ahmed_transfer.md`, `plans/m4_offline_harness.md`) and stale status
     headers (`plans/m4_offline_harness.md`, `plans/m8_step1b_ahmed_transfer.md`,
     `plans/m4_stage12_review.md`);
   - in the original checkout, record `scripts/export_session_to_ti_mat.m` and
     `scripts/view_recording_2.m` in HISTORY.
6. **Owner questions** (answers go in HISTORY):
   - how were the clock offsets obtained? All nine prospective manifests record exactly 0.000 s at
     both ends (HISTORY 2026-09-30 capture record);
   - is `JOURNAL_PAPER.md` §4.1 (MAE 0.16 vs 2.72 bpm, from 2026-07-14 live hops) paper-grade? No
     regeneration script was found.

**Do not:**
- resume acquisition (Track 1) until items 1–2 land;
- start codex-M3 or any prospective scoring before Milestone 5;
- rerun Track 0;
- implement the IoT/WST plans before owner acceptance and independent plan review.

## 4. Recent decisions that matter

- **Settle floor is 120 s (owner, 2026-09-29).** P001–P006 natural are recorded 60 s deviations.
  Disclose any later use; never rewrite their manifests, and never recapture to erase the deviation.
  `m2_sidecar_scaffold` semantics were ported into `src/protocol.py`. Retire that branch after
  integration; do not merge it.
- **Approved ethics scope is 15 new prospective participants** in addition to development subjects
  A–D. The "existing 10 participants" sentence in `notes/ethics_amendment_hr_recovery.md` is
  as-submitted text, not the approved scope.
- **Track 0 is closed** on the 2026-07-30 negative A/B: pinned coverage 12%→12%; re-selected
  13%→7%, with 4/8 locks moving. `clutter_removal` stays `none`.
- **P003 output was never viewed** (owner, 2026-09-29).
- **`vital_signs_own_v13` is the integration branch.** There is no `main`; the local `master` is
  exp002-era and not a target.
- **Behaviour changes get a new estimator ID and a new provenance chain.** Historical artifacts are
  superseded, never rewritten.
- **Milestone labels:** "codex-M0…M5" (`plans/plan_codex_milestones.md`) governs acquisition and
  evaluation. The remediation plan's "Milestone 0–5" is a separate, local scheme. The old
  `plans/implementation_plan.md` M0–M12 is historical.
- **No ECG sub-study** (closed 2026-08-25). Masimo PR is the sole HR reference; Methods carries a
  reference-error budget instead.
- **Development data stays in `results/live_demo/`** (owner, 2026-08-26), read in place. ADC hashes
  are in `experiments/m8_ahmed_transfer/capture_registry.yaml`; reference hashes are in
  `reference_registry/development_references_v1.json`.
- **Timing constants:** the natural/paced launch countdown is **30 s** (`scripts/live_demo.py:770`;
  recovery 0). It is distinct from `settle_evidence_window_s` (exactly 60.0) and from the 120 s
  minimum settle.
- Never describe this study's design with the timing words CLAUDE.md §4 forbids. Its analysis specs
  are a transparency property, not a timing one.

## 5. Gotchas / landmines

**Standing analysis rules** (owner: `notes/analysis_prespec.md` §7; replay landmine:
`HISTORY.md` 2026-07-14/15):
- **Windows are 30 s; do not shorten them.**
- **Statistics use non-overlapping 30 s windows only.** A 3 s hop shares 27/30 s of data, so those
  rows are not independent. Use per-session results, or contiguous block resampling; never a
  per-row bootstrap.
- **Index windows by frame number, never wall-clock.** `elapsed_s` in `--replay-fast` NPZs is
  wall-clock and unusable.

**Capture and file-format rules:**
- **Never change `adcbufCfg` SampleSwap=1.** SampleSwap=0 silently disables LVDS output in the SDK
  demo firmware (`steps/step_1/capture.py:333`, which cites `notes/dca1000_protocol.md` §5.4).
  Python captures therefore decode with `iq_swap: true`.
- **Replay directories are not interchangeable.** Several replay generations share massimo1's raw
  hash, and the 2026-07-25 / 2026-07-26 pair differs (`scripts/diagnose_bin_drift_config.yaml:40-46`).
  Pair replays by the explicit config entry, never by raw-hash content match alone.
- **Line endings are pinned to LF** (`.gitattributes`). Diagnostic writers pin LF explicitly
  (e.g. `scripts/diagnose_bin_sweep.py:567`), and the worktree's `tests/test_repository_eol.py`
  fails on CRLF or mixed tracked files.

**Data and firewall:**
- **Never open a P00x reference** during development. P001–P005 are representation-validation data,
  and P006 is a `final_evaluation` capture. Nothing learned from P006 or later may feed back into the
  algorithm, thresholds or representation. Role reassignment is forbidden.
- **`m2_capture_work/` (gitignored) holds folders for all 9 captured sessions** plus the 4
  preparation placeholders. The P010 folder's `P001_natural_settle.json` is a misnamed placeholder
  with no evidentiary status; never register it.
- **The MATLAB/TI tooling (original checkout, untracked) is not firewall-safe yet.**
  `notes/matlab_export.md` uses P001/P003 as worked examples and has absolute user paths.
  `scripts/generate_rawdatareader_config.py` accepts prospective paths and has no output-dir guard.
  Its generated JSON sets `rawFileName == processedFileName`. `matlab_exports/configs/` already holds
  pairs for all 9 sessions, including P006. MATLAB was never run (`MATLAB_CLI = false`). A full cube
  is about **2.93 GiB**; trial a small `NumFrames` first and read via `matfile`.
- **The WST collision-gate plan is not authorized** (`plans/wst_collision_gate_plan.md`, original
  checkout). It proposes a single P001–P005 promotion transaction. Any use of representation-
  validation data is the owner's firewall decision, and it needs independent DSP/ML review first
  (HISTORY 2026-09-30, original checkout).
- **Raw-capture integrity is a hash chain:** registry → `radar_receipt_sha256` →
  `sealed_radar_receipt.json` → `raw_sha256`. It was verified 9/9 on 2026-08-25. Re-check after any
  promotion.

**Acquisition rules:**
- **Pass the latest registry explicitly** (`--cohort-registry`, `--registry`).
  `src/m2/cohort_registry.py:24` still defaults to genesis `registry_v001.json`.
- **No retry for yield, warmup confidence, missing reference, recovery adequacy, coverage or
  agreement.** A recovery Stage-1 failure (<20 bpm PR range) never authorizes recapture.
- Command duration is exactly 600 s (`src/m2/acquisition_metadata.py:59`). A stream is exactly
  12,000 frames (`src/m2/capture_artifacts.py:202, 391`). Clock offset must be ≤ 1.0 s at both ends.
  Frame origin is the frame-0 start-assignment event, never `start_wall_utc`.
- Use automatic warmup bin selection only. Never a manual bin, replay, `--no-configure`, an alternate
  config, or any reference value to choose DSP, alignment or a retry.
- Keep all participant identity, screening, health and consent data out of the repository.

**Deleting, testing and environment:**
- **Before deleting anything, search code and tests, not only markdown, and run the suite.** Several
  plan and test files are hashed provenance dependencies, e.g. `plans/m8_ahmed_correction_plan.md`.
- **`tests/test_diagnose_bin_drift.py` replayless test needs ≥7 GB free RAM** (`diag_cfg()` default)
  and fails spuriously otherwise.
- **Run Python as `C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python ...`.**
  Bare `conda` fails (exit 127) in Git Bash. Calling the env's `python.exe` directly crashes
  Matplotlib (exit 127). Two concurrent `conda run` calls can collide on a temp file. Do not pass a
  shared `--basetemp`: the provenance tests build throwaway git repos.
- **`tests/test_documentation_claims.py` guards the records.** It fails on:
  - a cited `[Rn]` that is not in the reference list;
  - CLAUDE.md §4 timing words outside a prohibition in the manuscripts, HANDOFF, `notes/` or code
    (two allowlisted lines);
  - any section-numbered HANDOFF reference outside HISTORY, HANDOFF, `reports/` and `plans/`.

  Point at a durable owner instead: a note section, CLAUDE.md, or a dated HISTORY entry.
- **Never write a prospective session's PR into HISTORY.** For sealed sessions the start PR stays
  in the sealed manifest (`notes/protocol.md` SETTLE CRITERION).
- **`scripts/live_demo_config.yaml` is a hashed provenance input** for M8/M9 gates
  (`src/m8/ahmed_provenance.py:103, 191`; `scripts/m9_kotte_run.py:80`). Any byte change, comments
  included, changes the source identity of gates built afterwards. That is expected; historical
  gates verify at their own commit.
- **Estimator identity is checked at scoring.** Historical radar parents labelled
  `production_eca_ahet_v1` fail validation at the branch HEAD; re-verify them at their own commit.

**Stale numbers — produced by `eca_ahet_v1` or ungated scoring, not yet recomputed:**
- M1 MAE 2.77 bpm (n = 9) and coverage 10.94% / 9.17% (`plans/plan_codex_milestones.md:7, 148`);
- the M8/M9 production-arm tables in both manuscripts;
- the 2026-08-04 bin-policy tables and the legacy M8 coverage 0.301 (HISTORY).

Do not quote any of these as current.

## 6. Pointers

**W** = remediation worktree / branch; **O** = original checkout only (untracked or data); **W+O** =
both.

| Where | File | Purpose |
|---|---|---|
| W | `reports/discrepancy_audit_2026-09-29.md` | the audit: items H/M/L/T, evidence, owner-decision addendum (the O copy is older) |
| W+O | `reports/remediation_verification_2026-09-30.md` | per-item status after remediation; open items; integration steps |
| W | `plans/discrepancy_remediation_2026-09-29.md` | remediation milestones 0–5 and acceptance criteria (the O copy is rev 2) |
| W | `src/protocol.py` | shared protocol constants, including `MIN_SETTLE_S = 120.0` |
| W | `src/reference_access.py` | the single reference-read boundary |
| W | `reference_registry/development_references_v1.json` | development reference paths, SHA-256s, subjects A–D |
| W+O | `src/m2/label_firewall.py` | prospective capabilities and `guarded_reference_bytes` |
| W+O | `src/m2/acquisition_metadata.py` | acquisition-sidecar validity (W adds NEW_CAPTURE / HISTORICAL_RECORD purposes) |
| W | `src/agreement.py` | `arm_loa`: subject-clustered LoA, bootstrap, diagnostics |
| W+O | `src/vitals.py` | ECA+AHET; W adds `refine_peak_hz_safe` and `eca_mode: none` |
| W+O | `src/window_pipeline.py` | shared window DSP; estimator IDs (W: `:47-53`) |
| W | `reports/peak_refinement_diagnostic_2026-09-29.json` | pre-fix refinement incidence (radar-only) |
| W+O | `experiments/m8_ahmed_transfer/capture_registry.yaml` | development ADC identities and hashes |
| W+O | `cohort_registry/registry_v010.json` | current cohort registry |
| W+O | `notes/analysis_prespec.md` | cohorts, roles, window grid, admission, retry, LoA rules |
| W+O | `notes/protocol.md` | three-arm participant protocol (120 s limb not yet added) |
| W+O | `notes/m2_capture_runbook.md` | physical acquisition and finalization procedure |
| W+O | `notes/venue_iotj.md` | venue decision and requirements |
| W+O | `scripts/live_demo.py` | sole prospective live acquisition path (not paper-grade output) |
| W+O | `scripts/score_offline.py` | exploratory offline scorer with the `--isolate-fields` A/B guard |
| W+O | `scripts/score_production.py` | canonical M1 scoring entry |
| O | `data/raw/prospective/` | 9 promoted prospective captures (gitignored; externally backed up) |
| O | `results/live_demo/` | 8 development captures (A–D) + 9 prospective live folders |
| O | `scripts/export_session_to_mat.m`, `export_session_to_ti_mat.m`, `animate_range_profiles.m`, `view_recording_2.m`, `generate_rawdatareader_config.py`, `tests/test_generate_rawdatareader_config.py`, `notes/matlab_export.md`, `plans/matlab_time_domain_export.md` | uncommitted MATLAB/TI tooling (see §5) |
| O | `plans/iot_edge_gateway_plan.md`, `plans/iot_edge_phase_0…5_*.md`, `plans/wst_collision_gate_plan.md` | unreviewed, unauthorized IoT-edge and WST plans |
| W+O | `HISTORY.md` | append-only log (O has four entries W lacks; see §2) |
