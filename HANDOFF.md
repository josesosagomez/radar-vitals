# Handoff — remediation integrated; next is Milestone 5 (recompute under the corrected estimator)

> Read this and `CLAUDE.md` first. **State verified 2026-09-30.** `HISTORY.md` is append-only; this
> file is the current resume point.

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

### Repository

- **Work in `C:\Users\josemsosag\Desktop\vitals_radar_3`, branch `vital_signs_own_v13`**, the
  owner-designated integration branch.
  - The discrepancy remediation (`codex/discrepancy-remediation`) was fast-forwarded into it on
    2026-09-30. The integration commit follows and is pushed.
  - Check `git status -sb` before acting.
- **Untracked, deliberately not committed** (each needs a decision or review first; §3, §5):
  - MATLAB/TI tooling:
    - `scripts/export_session_to_mat.m`, `export_session_to_ti_mat.m`, `animate_range_profiles.m`,
      `view_recording_2.m`;
    - `scripts/generate_rawdatareader_config.py` and `tests/test_generate_rawdatareader_config.py`;
    - `notes/matlab_export.md` and `plans/matlab_time_domain_export.md`.
  - Unreviewed plans: `plans/iot_edge_*.md` (seven files) and `plans/wst_collision_gate_plan.md`.
- **The Codex worktree** at `C:\Users\josemsosag\.codex\worktrees\discrepancy-remediation\vitals_radar_3`
  (branch `codex/discrepancy-remediation`) is fully merged and now stale; its `HANDOFF.md` is out of
  date. Do not work there. Removing it (`git worktree remove`) and the branch is an owner decision.
- **Data** (gitignored, main checkout only):
  - `data/raw/prospective/`: 9 sessions, externally backed up;
  - `results/live_demo/`: 8 development captures (subjects A–D) plus 9 prospective live folders.
- The M8/M9 provenance tests need two gitignored source PDFs under `literature/ref_papers/`. They
  are present.

### Acquisition — labels sealed; nothing scored

- **Registry:** `cohort_registry/registry_v010.json`, revision 10, SHA-256
  `20604fb49a397e86f82f1e0773d7a152d5664a8af53e5eb80a7511f7b765312f`. The v001→v010 chain is intact.
- **9 of 45 sessions captured, 36 planned:** P001 natural+paced, P002 natural+paced, P003 natural,
  P004 natural, P005 natural+paced, P006 natural. **No recovery session yet.**
- **All 15 subjects are `label_state: sealed`.** P001–P005 are `representation_validation`;
  P006–P015 are `final_evaluation`.
- **No agreement metric exists for any prospective session.**
- **Settle deviation:** the six natural sessions P001–P006 recorded **60 s** settle against the
  **120 s** rule (owner decision D-OWN-7, reaffirmed 2026-09-29). They stay captured, immutable
  **protocol deviations** (`notes/analysis_prespec.md` §6 item 3a). The three paced sessions
  recorded 120 s. Clock offsets of 0.000 s in all nine manifests are **measured values** (owner,
  2026-09-30).
- **P003 paced, P007 natural, P009 natural and P010 natural were never started** (owner,
  2026-09-30). Their `m2_capture_work/` folders are preparation placeholders; the registry keeps
  them `planned`.
- **Acquisition (Track 1) is unblocked from the remediation side:**
  - the 120 s rule is enforced for new captures;
  - the reference firewall is closed.

  When to resume is the owner's call.

### Remediation — integrated

Per-item status as of the 2026-09-30 verification is in `reports/remediation_verification_2026-09-30.md`.
Later work (config pin, records, firewall) is in HISTORY 2026-09-30.

In place and independently reviewed:
- **Settle rule:** `src/protocol.py` `MIN_SETTLE_S = 120.0`.
  - New natural/paced captures under 120 s are rejected.
  - Historical manifests load with `protocol_compliant=false`.
  - Deviations cannot enter primary scoring.
- **Single reference boundary:** `src/reference_access.py`.
  - M4, M9 and `score_offline` read every reference through it with their default readers.
  - Minting a prospective scoring capability requires the session's registry-bound sealed radar
    receipt, and refuses protocol deviations before the reference is touched
    (`src/m2/label_firewall.py`).
- **Corrected estimator `eca_ahet_safe_refine_v2`:** bounded peak refinement
  (`src/vitals.py` `refine_peak_hz_safe`; `src/window_pipeline.py:47-53`).
  - The pre-fix measurement found 39 unsafe cases in 756 calls.
  - It changed one accepted estimate: sweep k=4, by 26.48 bpm.
- **ECA semantics:** production `skip_forbidden_harmonics_v1` projects only below-band
  harmonics, so there is no in-band ECA. `eca_mode: none` exists for comparisons.
- **Step 6 tracker:** writes only `tracker_*` fields and is labelled non-causal.
- **Agreement:** `src/agreement.py::arm_loa` implements the subject-clustered LoA. **It has no
  caller yet.**
- **Records:**
  - pilot figures withdrawn;
  - Ahmed is `[R22]`;
  - Masimo-selected gate origin disclosed in the manuscripts and configs;
  - "pending re-derivation" labels on the tables that depend on `eca_ahet_v1`;
  - `tests/test_documentation_claims.py` guards citations, CLAUDE.md §4 wording and HANDOFF
    references.

### Tests

| Where | Result | Source |
|---|---|---|
| Main checkout, after integration | **3233 passed, 5 skipped, 0 failed** | HISTORY 2026-09-30 integration entry |

- The count includes 4 tests from the untracked TI-generator test file.
- The 5 skips are the opt-in real-data test and 4 tests that need deleted replay folders.
- A clean clone without `results/` skips 17.

## 3. Active task / next steps

Do these in order; commit and push each step with a HISTORY append.

1. **Milestone 5 — recompute under `eca_ahet_safe_refine_v2`**
   (`plans/discrepancy_remediation_2026-09-29.md`, Milestone 5).
   - Build a new synthetic gate, a **new authorization filename**, and a reference-blind radar
     parent. Make `--gate`, `--authorization` and `--radar-parent` required and digest-checked.
   - Add an explicit `subject` field to the new capture-registry revision, and relax the
     single-subject role check at `src/m4/capture_registry.py:287`, with a test.
   - Re-score M1, the 2026-08-04 bin-policy tables and the M8/M9 production arms. Supersede, never
     rewrite, the old outputs.
   - Wire `arm_loa` into the canonical summaries, and null its LoA when `descriptive_only`.
   - Add a constant-median null baseline to `estimator_scoring`.
   - Any production caller that mints prospective scoring capabilities must pass each session's
     registry-bound `radar_receipt_path`. None exists yet.
   - A deviation-inclusive sensitivity analysis has no scoring path; add one only if wanted.
2. **Record each open DSP choice as a decision or a deferral.** None is recorded yet:
   - AHET codes 2 and 6 test the same `ratio_db`;
   - impulse clip 1.5 rad/frame;
   - absolute prominence;
   - `harmonic_max_hz: null`;
   - warmup +1000 HR weighting;
   - hardcoded f_r gate;
   - whether a non-peak second harmonic (refinement reason 6) should reject AHET.

   None may be tuned on Masimo.
3. **Before committing the MATLAB/TI tooling:**
   - make the TI generator refuse prospective paths and guard `--output-dir`;
   - give `rawFileName` and `processedFileName` distinct names;
   - switch the `notes/matlab_export.md` examples to development captures and remove its absolute
     paths;
   - apply the M-22 exporter fixes;
   - obtain an independent review of the range-FFT code (CLAUDE.md §6);
   - record `export_session_to_ti_mat.m` and `view_recording_2.m` in HISTORY.
4. **Remaining hygiene** (none changes a result):
   - stale notes: `notes/approach.md:633` "physical acquisition pending";
     `notes/capture_inventory.md:146, 158` "E/F/G"; `notes/dca1000_protocol.md:310` names
     `scripts/capture.py` (now `steps/step_1/capture.py`); `notes/note_candidate_ranking.md:3`;
   - a "superseded" banner on the `plans/implementation_plan.md` milestone map;
   - dated banners on plans with affirmative timing wording and stale status headers
     (`plans/implementation_plan.md`, `plans/m8_step1b_ahmed_transfer.md`,
     `plans/m4_offline_harness.md`, `plans/m4_stage12_review.md`);
   - firewall blind spots (HISTORY 2026-09-30 firewall entry): guard
     `src.m4.manifest.load_manifest` directly; extend the bypass audit to `sha256_file`,
     `read_bytes` and `open` on reference paths; retire the unsafe refinement in the dead
     `vitals.run_pipeline_locked`;
   - write a regeneration script for `JOURNAL_PAPER.md` §4.1 (MAE 0.16 vs 2.72 bpm), or withdraw it,
     before submission (CLAUDE.md §3.1). The owner keeps it as a claim for now.
5. **Owner decisions pending:**
   - retire the `m2_sidecar_scaffold` branch (local and origin); its semantics are ported;
   - remove the stale Codex worktree and branch;
   - accept or shelve the IoT-edge and WST plans. The WST plan proposes one P001–P005 promotion
     transaction, which is a firewall decision.

**Do not:**
- start codex-M3 or any prospective scoring before Milestone 5;
- rerun Track 0;
- implement the IoT/WST plans before owner acceptance and independent plan review.

## 4. Recent decisions that matter

- **Settle floor is 120 s (owner, 2026-09-29).** P001–P006 natural are recorded 60 s deviations
  (a post-capture disposition). Disclose any later use; never rewrite their manifests, and never
  recapture to erase the deviation.
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
  hash, and the 2026-07-25 / 2026-07-26 pair differs (`plans/bin_drift_diagnostic.md` §1, BDR-20).
  Pair replays by the explicit config entry, never by raw-hash content match alone.
- **Line endings are pinned to LF** (`.gitattributes`); `tests/test_repository_eol.py` fails on CRLF
  or mixed tracked files. On Windows, Python `write_text` produces CRLF: write bytes or pass
  `newline="\n"`.

**Data and firewall:**
- **Never open a P00x reference** during development. P001–P005 are representation-validation data,
  and P006 is a `final_evaluation` capture. Nothing learned from P006 or later may feed back into the
  algorithm, thresholds or representation. Role reassignment is forbidden.
- **`m2_capture_work/` (gitignored) holds folders for all 9 captured sessions** plus the 4
  preparation placeholders. The P010 folder's `P001_natural_settle.json` is a misnamed placeholder
  with no evidentiary status; never register it.
- **The untracked MATLAB/TI tooling is not firewall-safe yet** (§3 step 3).
  `matlab_exports/configs/` (gitignored) already holds pairs for all 9 sessions, including P006.
  MATLAB was never run (`MATLAB_CLI = false`). A full cube is about **2.93 GiB**; trial a small
  `NumFrames` first and read via `matfile`.
- **The WST collision-gate plan is not authorized** (`plans/wst_collision_gate_plan.md`,
  untracked). Its HISTORY entry and the `notes/approach.md` §8.1 note describe it as a candidate
  only.
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
- **Never write a prospective session's PR into HISTORY.** For sealed sessions the start PR stays
  in the sealed manifest (`notes/protocol.md` SETTLE CRITERION).
- Keep all participant identity, screening, health and consent data out of the repository.

**Deleting, testing and environment:**
- **Before deleting anything, search code and tests, not only markdown, and run the suite.** Several
  plan and test files are hashed provenance dependencies, e.g. `plans/m8_ahmed_correction_plan.md`.
- **`tests/test_diagnose_bin_drift.py` replayless test needs ≥7 GB free RAM** (`diag_cfg()` default)
  and fails spuriously otherwise.
- **Run Python as `C:\ProgramData\anaconda3\Scripts\conda.exe run -n radar-vitals python ...`.**
  - Bare `conda` fails (exit 127) in Git Bash.
  - Calling the env's `python.exe` directly crashes Matplotlib (exit 127).
  - `conda run` rejects multi-line `-c` scripts; use a script file.
  - Two concurrent `conda run` calls can collide on a temp file.
  - Do not pass a shared `--basetemp`: the provenance tests build throwaway git repos.
- **`tests/test_documentation_claims.py` guards the records.** It fails on:
  - a cited `[Rn]` missing from the reference list;
  - CLAUDE.md §4 timing words outside a prohibition in the manuscripts, HANDOFF, `notes/` or code;
  - any section-numbered HANDOFF reference outside HISTORY, HANDOFF, `reports/` and `plans/`.
- **`scripts/live_demo_config.yaml`, `src/m4/estimator_scoring.py` and `src/m2/label_firewall.py`
  feed the M8/M9 source identity.** Any byte change alters the identity of gates built afterwards.
  That is expected; historical gates verify at their own commit.
- **Estimator identity is checked at scoring.** Historical radar parents labelled
  `production_eca_ahet_v1` fail validation at HEAD; re-verify them at their own commit.

**Stale numbers — produced by `eca_ahet_v1` or ungated scoring, not yet recomputed:**
- M1 MAE 2.77 bpm (n = 9) and coverage 10.94% / 9.17% (`plans/plan_codex_milestones.md:7, 148`);
- the M8/M9 production-arm and rerun-lock tables in both manuscripts;
- the 2026-08-04 bin-policy tables and the legacy M8 coverage 0.301 (HISTORY).

Do not quote any of these as current.

## 6. Pointers

| File | Purpose |
|---|---|
| `reports/discrepancy_audit_2026-09-29.md` | the audit: items H/M/L/T, evidence, owner-decision addendum |
| `reports/remediation_verification_2026-09-30.md` | per-item status after the first remediation pass |
| `plans/discrepancy_remediation_2026-09-29.md` | remediation milestones 0–5; Milestone 5 is next |
| `src/protocol.py` | shared protocol constants, including `MIN_SETTLE_S = 120.0` |
| `src/reference_access.py` | the single reference-read boundary |
| `reference_registry/development_references_v1.json` | development reference paths, SHA-256s, subjects A–D |
| `src/m2/label_firewall.py` | prospective capabilities; receipt-bound compliance check |
| `src/m2/acquisition_metadata.py` | acquisition-sidecar validity (NEW_CAPTURE / HISTORICAL_RECORD) |
| `src/agreement.py` | `arm_loa`: subject-clustered LoA, bootstrap, diagnostics |
| `src/vitals.py` | ECA+AHET; `refine_peak_hz_safe`; `eca_mode: none` |
| `src/window_pipeline.py` | shared window DSP; estimator IDs (`:47-53`) |
| `src/m4/estimator_scoring.py`, `scripts/score_production.py` | canonical scoring (Milestone 5 entry) |
| `reports/peak_refinement_diagnostic_2026-09-29.json` | pre-fix refinement incidence (radar-only) |
| `experiments/m8_ahmed_transfer/capture_registry.yaml` | development ADC identities and hashes |
| `cohort_registry/registry_v010.json` | current cohort registry |
| `notes/analysis_prespec.md` | cohorts, roles, window grid, admission (§6 incl. 3a), retry, LoA rules |
| `notes/protocol.md` | three-arm participant protocol, including the 120 s settle limb |
| `notes/m2_capture_runbook.md` | physical acquisition and finalization procedure |
| `notes/venue_iotj.md` | venue decision and requirements |
| `scripts/live_demo.py` | sole prospective live acquisition path (not paper-grade output) |
| `scripts/score_offline.py` | exploratory offline scorer with the `--isolate-fields` A/B guard |
| `data/raw/prospective/`, `results/live_demo/` | captures (gitignored) |
| `HISTORY.md` | append-only evidence and decision log |
