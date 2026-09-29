# Discrepancy audit — 2026-09-29

**Scope:** whole repository at `vital_signs_own_v13` @ `e6d055c`, plus the uncommitted working tree
(MATLAB export work, modified `HANDOFF.md` / `HISTORY.md` / `.gitignore`).

**Method:**
- Four independent read-only reviews ran in parallel: documentation and project state; radar DSP and
  configuration; evaluation, reference and label firewall; code health including a full test run.
- The high-impact claims were then re-checked directly against the code and data.
- No project file was modified.
- No sealed reference CSV (`data/raw/prospective/*`, `results/live_demo/*P00*`, `m2_capture_work/*`)
  was opened. Only file names and `session_manifest_v3.json` fields were read.

**Legend:**
- **Verified** means re-checked directly during synthesis; otherwise the item is reviewer-confirmed
  with the cited evidence.
- **Re-derive** lists previously recorded numbers that a fix would change. Per CLAUDE.md §10, those go
  in a *new* `HISTORY.md` entry and never in an edit of an old one.
- Line numbers are as of `e6d055c`.

## Post-audit owner decisions — 2026-09-29

The five blocking decisions in §1 were resolved by the owner after the read-only audit:

1. **Settle floor:** the 120 s minimum stands. The six natural sessions P001–P006 recorded with
   60 s settle are retained as captured sessions but classified as protocol deviations; they are
   not 120 s-compliant primary per-protocol sessions. Any later use must identify the deviation.
   The relevant sidecar-branch semantics are to be reconciled into `vital_signs_own_v13`; the
   divergent branch is not the integration target and may be retired after the port is verified.
2. **Ethics scope:** the approved scope was 15 new prospective participants in addition to the
   development subjects. The later owner attestation is the operative scope; the conflicting
   in-repository amendment wording is not the approved scope.
3. **Track 0:** the 2026-07-30 clutter-removal A/B is accepted as the result. Static clutter removal
   remains off; a rerun is not a prerequisite for remediation.
4. **P003 exposure:** the MATLAB scripts were written, but neither the P003 animation nor any
   generated animation output was viewed. P003 did not inform the Track 0 decision.
5. **Integration branch:** `vital_signs_own_v13` is the current integration branch. The exp002-era
   local `master` is not a merge target.

The original §1 table is retained below as the audit-time statement of the questions that were
open. This addendum is their durable disposition; implementation and documentation fixes remain
outstanding.

---

## 0. Summary

The code base is internally rigorous: 3144 tests pass, the cohort-registry hash chain is intact, and
the canonical M1 scoring chain is hash-bound. The discrepancies are concentrated in three places:

1. **Project state has diverged from the records.**
   - An owner protocol decision (settle floor 120 s) lives only on an unmerged branch, and all six
     natural sessions were captured at 60 s.
   - The active task (Track 0) ignores a completed negative A/B.
   - The paper and chapter carry a pilot table that violates the project's own rules.
2. **Firewall and reference hygiene are enforced by convention, not by code, outside the M2/M4
   chain.** Eight scripts will read any Masimo CSV they are pointed at, including sealed ones. Several
   secondary scoring paths skip the admission gate.
3. **Three DSP defects can bias heart rate:**
   - an unguarded parabolic refinement on the AHET second harmonic (up to ≈4.8 bpm);
   - an in-band ECA that is inactive in production while the estimator's name implies otherwise;
   - an offline tracker that relabels rejected windows as valid.

**Five owner decisions block the rest** (§1). Nothing in §2–§4 should be merged before they are made.

---

## 1. Owner decisions required first

| # | Decision | Why it blocks |
|---|---|---|
| OD-1 | **Settle floor:** do the 6 natural sessions captured at 60 s (P001–P006) stand, given owner decision D-OWN-7 (≥120 s, 2026-08-12)? Is `m2_sidecar_scaffold` merged or retired? | Determines admissibility of 6/9 captured sessions and the rule for all remaining captures (H-1) |
| OD-2 | **Ethics scope wording:** which document was approved — the in-repo submission text ("existing 10 participants") or the attested "15 new participants"? | Methods section and consent traceability (H-11) |
| OD-3 | **Track 0 framing:** accept the 2026-07-30 negative A/B as the answer, or re-run it on current code as a confirmation? | The HANDOFF active task is built on a false premise (H-2) |
| OD-4 | **P003 exposure:** was the P003 static-removed animation (MATLAB tooling) viewed? | If yes, the Track 0 record must disclose that representation-validation data was seen (H-7) |
| OD-5 | **PR base branch:** `main` does not exist locally or on origin; only a local `master` (exp002-era). What is the integration branch? | Every "merge" in this plan needs a target (L-12) |

---

## 2. HIGH — can produce a wrong number, a false claim, or a label leak

### H-1 Settle floor raised by the owner, never applied (Verified)
- **Discrepancy:**
  - Commit `df437b9` on the unmerged, pushed branch `m2_sidecar_scaffold` raises `MIN_SETTLE_S` from
    60 to 120 s ("D-OWN-7: owner decision 2026-08-12", 14:37 UTC).
  - `P001_natural` was captured about 80 min later.
  - All six natural manifests record `"settle_duration_s": 60.0`; the three paced sessions record 120.0.
  - The current branch still accepts ≥60 s (`src/m2/acquisition_metadata.py:330`), even though its own
    template says `120.0` (`templates/m2_acquisition_natural.yaml:37`).
  - `HANDOFF.md:167` calls Track 2 "Not started", and `HISTORY.md:11748` says "Milestone A … still not
    started", but the branch already carries Milestone A (`7875e50`) and D-OWN-8 (`3c90683`, `src/protocol.py`).
- **Fix:**
  1. OD-1.
  2. Either merge the branch and re-run the suite, or append a HISTORY entry recording why D-OWN-7 is
     withdrawn.
  3. If 120 s stands, append a HISTORY entry listing the 6 affected sessions and their disposition
     (admit as a recorded deviation, or exclude). The no-retry rule in `notes/protocol.md` applies to
     any recapture.
  4. Rewrite HANDOFF §4 Track 2 and add a settle-floor gotcha.
- **Must happen before the next Track 1 capture.**

### H-2 Track 0 re-asks a question already answered (Verified)
- **Discrepancy:**
  - `HISTORY.md:8655` (2026-07-30) records the `--isolate-fields phase.clutter_removal` A/B (run
    `20260730T204448Z`, 8 dev captures, 67 windows):
    - pinned lock: 12% → 12% coverage;
    - re-selected lock: 13% → 7%, the lock moved in 4/8 captures, massimo2 went 80% → 0%.
  - Its conclusion: "should **not** be enabled … must not be proposed as a coverage fix without new
    data". `HISTORY.md:9791` listed it as "measured, rejected".
  - `HANDOFF.md` §3 (73–140), the 08-25/08-26 HISTORY entries, `notes/approach.md:47` ("NEVER
    CONCLUDED") and `notes/venue_iotj.md` §6.7 treat it as open.
  - HANDOFF §3.3's "first question" (re-derive vs reuse the recorded bin) is partly answered: the scorer
    already reports both *pinned* and *rerun* estimands.
- **Fix:**
  - OD-3.
  - Append a HISTORY entry noting that the 08-25/26 entries missed 8655.
  - Rewrite HANDOFF §3 as "a negative A/B exists; optional confirmation re-run on current code (the
    frame-0 epoch fix and later changes post-date it)".
  - Add dated correction notes under `approach.md` §3.3, `protocol.md:114` and `venue_iotj.md` §6.7.

### H-3 Pilot agreement table in the paper and chapter breaks the project's own rules (Verified in part)
- **Discrepancy:**
  - `JOURNAL_PAPER.md:205-212` §4.2 and `THIRD_CHAPTER.md:588-601` §10.1 report natural / paced-16 /
    sweep MAE 0.19 / 0.50 / 0.53 bpm with "**n = 4 subjects**".
  - Those three sessions are massimo1, massimo2 and sweep, which `notes/capture_inventory.md:111-112`
    maps to **subjects A and B only (n = 2)**. (Verified.)
  - Per the reviewer, the windows are live-demo replays with overlapping ~3 s hops
    (`HISTORY.md:5487-5498`, a reconstructed entry). This breaks CLAUDE.md §4 ("live demo … not
    paper-grade") and the chapter's own §7.4.
  - No committed script regenerates the table (CLAUDE.md §3.1).
  - The implied 10–46% coverage conflicts with canonical M1 (radar coverage 11/120 on k ≥ 1).
- **Fix:** withdraw the table from both manuscripts, or replace it with the canonical M1 k ≥ 1 output
  of `scripts/score_production.py`, with the correct n.
- **Re-derive:** chapter §10.1/§10.2 and paper §4.2.

### H-4 Wrong citation for the Ahmed method
- **Discrepancy:**
  - Ahmed et al. (TRS 2024) is cited as **[R1]** at `JOURNAL_PAPER.md:401` and
    `THIRD_CHAPTER.md:92-93, 291-292`.
  - [R1] in both reference lists is **Tang et al.** (`JOURNAL_PAPER.md:531`, `THIRD_CHAPTER.md:976`).
  - Ahmed has no number: it is unnumbered at `THIRD_CHAPTER.md:1040` and absent from `JOURNAL_PAPER.md`.
- **Fix:** add Ahmed as a numbered reference in both files, re-point every Ahmed citation, and
  re-check every [Rn] against its list entry.

### H-5 Unguarded parabolic refinement biases HR (Verified)
- **Discrepancy:**
  - `src/vitals.py:168-184` `refine_freq_hz` has no local-maximum check and no bound on the shift.
  - At `:856` it refines `peak2_global`, which is the argmax *within* the ±0.1 Hz AHET second-harmonic
    window and can sit on a rising edge.
  - For neighbour magnitudes (1, 2, 2.9): shift = 0.5·(1−2.9)/(1−4+2.9) = **+9.5 bins**.
  - Through `f_final = 0.5·f_h + 0.25·f_h2` (`:794`, `:913`), that is ≈0.079 Hz, or **≈4.8 bpm**, on a
    30 s window.
  - The same function is used at `:819`, `:838` and `:1132`. The safer `parabolic_interpolate_peak`
    already exists at `:~200`.
- **Frequency on real data: unknown.** Measure first: log `|shift|` per window over the 8 dev captures.
- **Fix:**
  - Return the bin centre unless the peak is a local maximum of the full spectrum and |shift| ≤ 0.5;
    or replace the function with `parabolic_interpolate_peak`.
  - Test: a synthetic spectrum whose in-window argmax borders a larger out-of-window value must refine
    to within ±0.5 bin.
  - Needs the independent code review required by CLAUDE.md §6 (peak-picking).
- **Re-derive:** canonical M1 (MAE 2.77 / coverage 11/120) and every M8/M9 production-arm number.

### H-6 Production in-band ECA is inactive, but the estimator is described as ECA+AHET (Verified in part)
- **Discrepancy:**
  - `scripts/live_demo_config.yaml:99` selects `eca_mode: skip_forbidden_harmonics_v1`.
  - `src/vitals.py:466-470` documents that mode as "BROKEN: … cancels nothing in the cardiac band
    (0.00 dB)". (Verified.)
  - Reviewer's synthetic check: the mode still projects out harmonics **below** 0.8 Hz, so it is not a
    total no-op. In-band, production HR is effectively "top-3 band-passed peaks ≥ 0.95 Hz + strict AHET".
  - The replacement `guard_cardiac_candidate_v1` fails its own acceptance test and must not be promoted.
- **Fix:**
  - Leave the numbers unchanged.
  - Describe the estimator accurately wherever `eca_ahet_v1` is named (HANDOFF, `notes/approach.md`,
    both manuscripts): "in-band ECA inactive; sub-band harmonics removed".
  - Add an explicit `eca_mode: none` control arm to `experiments/exp_eca_modes/`, judged only on
    synthetic and reference-free criteria.
  - Add a test pinning which k are projected.

### H-7 Label firewall is not enforced outside the M2/M4 chain
- **Discrepancy:**
  - `guarded_reference_bytes` / `transition_label_access_atomically` have **no production caller**.
  - These scripts read the first (or every) `*.csv` in whatever capture directory they are given:
    - `score_offline.py:269-278`
    - `diagnose_signal_presence.py:225, 750`
    - `br_bin_rule.py:312`
    - `simulate_bin_policy.py:99`
    - `m8_ahmed_score.py:78`
    - `br_bin_preflight.py:192`
    - `stage1b_*.py:99/115`
  - Pointing any of them at `data/raw/prospective/P00x` would read a sealed label.
  - `diagnose_signal_presence.py --all` (`:729`) already sweeps the 9 `results/live_demo/*P00*`
    folders. (Verified present.) It crashes before leaking only because those folders hold no CSV.
  - `br_bin_rule.py --mode test` (the only planned one-touch check) targets nonexistent `massimo8..10`.
    Its intended subjects are now the sealed P001–P003.
  - The untracked MATLAB tooling uses **P001/P003** (representation_validation) as worked examples
    (`notes/matlab_export.md:38, 98-119`). `matlab_exports/configs/` also holds **P006**
    (final_evaluation). A static-clutter-removed animation of P003 bears directly on Track 0, and
    `HANDOFF.md:152-154` says any use of that cohort for method decisions consumes it.
  - Reference PR values (`start/resting/final_pr_bpm`) are stored in radar-side sidecars, and sealed CSV
    copies exist in `m2_capture_work/`, outside the hash chain.
- **Fix:**
  - Add a single `src/reference_access.py::load_reference(capture_dir, authorization=None)` that
    refuses `data/raw/prospective/`, `m2_capture_work/` and `P0\d\d` paths without a
    `ScoringAuthorization`.
  - Route all eight call sites through it, and make `--all` exclude P00x.
  - Test: a temp dir named `P001_natural` containing a CSV must make every entry point raise before
    `open`.
  - Switch the MATLAB examples to massimo3–7, and make `generate_rawdatareader_config.py` refuse
    prospective paths without an explicit cohort-consumption flag.
  - OD-4.

### H-8 Step 6 temporal tracker relabels rejected windows as valid
- **Discrepancy:**
  - `steps/step_6/temporal_tracker.py:543-570` sets `hr_valid=True, hr_confidence="high"` on candidates
    AHET rejected (eligibility excludes only code 4, `:245`).
  - It is a session-wide Viterbi, so it is non-causal.
  - When all states die (`:382-397`), backtracking stops, earlier windows become gaps, and previously
    AHET-valid rows are overwritten as invalid (`:585-594`).
  - The Step 6 summary MAE and coverage use the relabelled validity (`extract_heart_rate.py:1338-1352`).
  - It is not in the live or M4 canonical path, so it is latent today.
- **Fix:**
  - Write `tracker_hr_valid` and `hr_confidence="tracker"` and never overwrite `hr_valid`.
  - Keep the previous segment's best end-state on restart.
  - Label the tracker non-causal.
  - Test: `hr_valid` is unchanged after `apply_tracker`, and a three-segment pool keeps segment-1
    decisions.

### H-9 Production gates were selected against Masimo on deleted data
- **Discrepancy:**
  - The step-6 hop1 "safe" configs and floor gates came from a grid search scored against Masimo:
    `diagnose_step6_candidate_tracks.py:227-240`; `config_hop1_win30_safe.yaml:49-53` "1080 combos …
    MAE=1.23".
  - That search ran on sessions test..test5, wiped before 2026-07-09, so it is irreproducible
    (CLAUDE.md §3.1).
  - `live_demo_config.yaml:3, 90` calls the result "Validated … copied verbatim", and the production
    AHET floor gates (2.0 / 4.0 dB) descend from it.
  - No recorded result reuses the tuning data (reviewer), so this is a disclosure problem, not
    contamination.
- **Fix:**
  - Reword the config comments to "heart gates inherited from a Masimo-selected sweep on deleted
    development data; not validated".
  - Disclose this in the paper Methods.
  - Report agreement only on held-out prospective subjects.
  - Record it in a HISTORY entry.

### H-10 No spec-compliant Bland–Altman exists; the reported LoA violates the analysis spec
- **Discrepancy:**
  - `scripts/plot_bland_altman.py` is dead: it hard-codes exp006–009, pools windows and logs no
    provenance.
  - The only live LoA (`m9_kotte_score.py:541-553`) is bias ± 1.96·SD over windows pooled across 4
    subjects.
  - `THIRD_CHAPTER.md:744-757` reports it as "Bland–Altman LoA", contradicting `notes/analysis_prespec.md`
    §1 (subject-clustered ANOVA estimator).
  - Bland–Altman is a primary metric (CLAUDE.md §1).
- **Fix:**
  - Add `src/agreement.py::arm_loa()` implementing prespec §1, tested against a hand-computed unbalanced
    3-subject example.
  - Rename the M9 field `pooled_window_loa_descriptive`.
  - Retire `plot_bland_altman.py` with a HISTORY note (keep the file as provenance).
- **Re-derive:** chapter §10.4 LoA column.

### H-11 Ethics submission text contradicts the attested approved scope
- **Discrepancy:** `notes/ethics_amendment_hr_recovery.md:124` says "a third session for the existing 10
  participants". CLAUDE.md §1 and `notes/protocol.md:15, 279, 492` say 15 new participants, and
  `protocol.md:282` cites that file as the submission text.
- **Fix:**
  - OD-2.
  - Do **not** edit the body: it is an as-submitted record.
  - Add a second dated header note: "approved scope per owner attestation 2026-08-09 differs from this
    text; approved document held off-repo".
  - Append a HISTORY entry.

---

## 3. MEDIUM — misleads a future session, breaks traceability, or is a latent correctness risk

### Evaluation and reference

| ID | Discrepancy | Fix | Re-derive |
|---|---|---|---|
| M-1 | `score_offline.py:1203-1215` puts `median_pr_bpm` into `paired_metrics` whether or not the window is `admitted` (Verified). Stored `comparison_<estimand>.json` files are contaminated, e.g. massimo7 `off` MAE 28.78 from a non-admitted k=0. No HISTORY number came from these files (reviewer). | Use NaN unless `admitted`; test that a non-admitted window gives n_finite=0; annotate the 5 run dirs as superseded | None in HISTORY |
| M-2 | `simulate_bin_policy.py:95-112` keeps any finite BR reference without checking `admitted` (Verified) and anchors on approximate `start_wall_utc` | Filter on `admitted`; use `resolve_frame0_epoch` | **2026-08-04 bin-policy tables** (train 85%/1.56, 68%/0.97; holdout 46%/2.60 vs 3.41) |
| M-3 | Legacy `m8_ahmed_score.py`: `:78` takes the first CSV; it scores k=0 with a lock chosen on k=0; the results came from `b89729e`, older than the current code; `HISTORY.md:10284-10293` quotes production coverage 0.301 (the retired 30.08%) | Use `discover_masimo_csv`; restrict to k ≥ 1 | That HISTORY table |
| M-4 | Four reference definitions coexist. Canonical is `comparator.hr_reference`/`br_reference`. Non-canonical: Step 6 `_compute_masimo_pr` (mean, no stationarity), Step 5 `reference_br` (mean, no gate), and `masimo.reference_pr` (falls back to **low-PI rows** when none qualify, `masimo.py:148-149`, which violates CLAUDE.md §4; dead path) | Switch Steps 5/6 to the comparator, or stamp `reference_definition: legacy_mean` in their outputs; make `reference_pr` return NaN on all-low-PI | Step 5/6 summaries (not cited in manuscripts) |
| M-5 | Subject identity is inconsistent: M4/M8 `capture_registry` enforces `development_apparent_single_subject` and "unknown" protocol for m3–m7 (`capture_registry.py:38-52, 287`; `capture_registry.yaml:156-160`), while M9 and `br_features.py:43-48` map A–D; `HISTORY.md:11381` says "single-subject". The "natural" label for m3–m7 was inferred from Masimo RRp, i.e. from the reference. The registry is hash-bound, so do not edit it. | New HISTORY entry; add a `subject` key in the next registry revision; mark natural labels "reference-inferred" | Blocks any per-subject/LOSO result for M1/M8 |
| M-6 | `src/m4/manifest.py` enums have **diverged** from `src/m2`: `Arm` lacks `RECOVERY`, `DataRole` uses different vocabulary, and `RetryReason` has `WARMUP_LOW_CONFIDENCE`, which `analysis_prespec.md` §2d forbids. `m2/manifest.py:22-31` routes any v2 manifest to the m4 rules. The 600-frame / 20 Hz constants are duplicated in 3 places. | Reject v2 manifests for P-subjects (with a test); import the constants from `window_grid` | None |
| M-7 | Reproducibility of the canonical chain: `score_production.py:40, 109` defaults to the M8 radar parent, not M1's `radar_m1_a4718267`; the authorization YAML was rewritten in place 3× (`d0f2910`, `e182288`, `a471826`); `m8_ahmed_all_bins.py:94-103` reads `LATEST` without checking its digest; gate bundles live in gitignored `results/` | Make `--radar-parent` / `--gate` required; check the digest; give each authorization revision a new filename; back up `results/` gate bundles | None |

### Acquisition and records

| ID | Discrepancy | Fix |
|---|---|---|
| M-8 | Unregistered capture attempts in the gitignored `m2_capture_work/`: P003_paced (08-18), P007_natural (08-17), P009_natural (08-19), P010_natural (08-25). The registry still shows them as `planned` with no attempt record, and `HISTORY.md:11749` lists 3 of the 4. **P010's only evidence file is named `P001_natural_settle.json`** (Verified). | HISTORY entry recording the operator's disposition of each; a registry `attempts`/aborted state; `m2_register_capture` rejects evidence files whose basename doesn't match `session_id` |
| M-9 | No HISTORY entry records the P001–P006 capture sessions (08-12 → 08-18). CLAUDE.md §3.6 requires settle, duration, posture and distance per session. | Append an entry built from the manifests |
| M-10 | 20 tracked files have CRLF line endings in the working tree against `.gitattributes eol=lf` (Verified). They include `tests/test_m4_evidence_serialization.py`, which is in the M8 attested set (`ahmed_provenance.py:122, 157`), and all `figures/generated/m8_ahmed_fig8/*/bundle.json`, `metrics.json` and `provenance.json`. Hashes of working-tree bytes differ from the committed ones, so a manifest built here will not verify from a clean clone. `HISTORY.md` in the working copy has mixed endings. | For each file, confirm `git diff` is empty, then delete and `git checkout --`; renormalize `HISTORY.md` before committing |

### Signal processing

| ID | Discrepancy | Fix | Changes numbers |
|---|---|---|---|
| M-11 | AHET codes 2 and 6 test the **same** `ratio_db` (`vitals.py:881, 897`; thresholds 1.0 / 2.0 dB) (Verified). The effective gate is ≥2 dB, and code 6 is not an independent "absolute SNR floor" as its comment claims. | Minimal: delete code 2 (acceptance unchanged, only reason labels change) and test that `hr_valid` is identical over a sweep. A fundamental-based floor would be a new gate: that is a decision, and its threshold must not be tuned on Masimo. | Labels only |
| M-12 | Live and offline estimators differ, but `live_demo_config.yaml:3, 90` says "copied verbatim". Live lacks the Step 5 ±2 bpm edge-lock, the Step 4 quality mask, `resp_harmonic_guard` and the tracker; its hop is 3 s vs 1 s; f_r comes from the same window (impulse-clipped) vs Step 5 (unclipped). **Step 5/6 metrics are not the production estimator.** | Reword the comments to list the exclusions; never quote Step 5/6 metrics as production | No |
| M-13 | Design choices that affect results but were never justified. Each is a decision, and none may be tuned on Masimo. (a) `impulse_clip_rad: 1.5` rad/frame ≈ 9.1 mm/s chest velocity, while paced 18 bpm breathing at ~6 mm peaks near 11 mm/s, and the recovery arm will be faster. (b) `candidate_min_prominence: 3.0` is in absolute FFT units, so its meaning changes with window length and RCS. (c) HA respiration evidence reaches 1.5 Hz (`harmonic_max_hz: null`), inside the cardiac band. (d) The warmup score gives +1000 for `hr_valid`, which biases the first-window HR. (e) The hardcoded f_r gate 0.15–0.60 Hz (`vitals.py:507`) forces HR invalid whenever BR is 6–9 bpm. | (a) Derive the clip from λ, frame rate and a cited maximum chest velocity in config, and log the clipped fraction. (b) Express prominence relative to the noise floor. (c) Cap at `heart_band_lo`. (d) Exclude the warmup window from scoring. (e) Move the gate to config. Each needs a synthetic test. | Yes, if changed |
| M-14 | Latent offline-step defects (no data flows through Steps 2–6 today, since `data/manifest.local.csv` is empty). The Step 4 FFTs are unwindowed while bin choice uses Hann (`add_quality_mask.py:158-167, 350-356, 486-490`). Steps 5/6 parse `stationary_intervals` with `int(split("-"))` and raise on multi-interval or decimal input (step5 `:344-346`, step6 `:1015-1017`). Step 3 concatenates non-contiguous intervals and unwraps across the joins (`:243-244`). Step 3 (0.8–2.5 m), range_plot (0.6–2.0 m, comment says 0.5–3.0) and live warmup (0.8–1.4 m) all pick bins differently. | Apply Hann in Step 4; move `_parse_intervals` into `src/` and share it; compute per-interval phase metrics; derive the gates from `protocol.subject_distance_m`; share one `_rank_bins` | Yes, when steps are used |
| M-15 | `live_demo.py:396-399`: `queue.Full` drops a frame silently while `frame_validity` stays "valid". There is no parity test between the live `_decode_frame` and `radar_io.read_adc_bin`. | Count drops and invalidate the next window; add a parametrized parity test for both `iq_swap` values | Live only |

### Documentation and plans

| ID | Discrepancy | Fix |
|---|---|---|
| M-16 | Stale notes: `approach.md:623` says "acquisition pending"; `capture_inventory.md:146, 158` still says "E/F/G"; `dca1000_protocol.md:310` points to `scripts/capture.py`, now `steps/step_1/capture.py`; the runbook hard-codes `registry_v001/v002` (`:106, 125, 166-181`); `protocol.md:114` says "no stage removes static clutter"; `venue_iotj.md` §4.4 contradicts §6.5; `note_candidate_ranking.md:3` says "awaiting cross-model review"; `note_stage1b_lag_statistic.md:5` cites a file that never existed | Dated correction notes under the old text; runbook uses `registry_v<latest>` |
| M-17 | Manuscript facts are stale: "10-subject × 2-session" (`JOURNAL_PAPER.md:71, 439`); "one subject across four sessions" (`THIRD_CHAPTER.md:79`); "10 × 3" (`:242, 476`); 797 tests (`:1135, 1203`); "~31 GB / 20 sessions" (`JOURNAL_PAPER.md:501`); dangling "HANDOFF §2.1" (`:597`); missing `figures/fig_range_bin_mislock.py` (`JOURNAL_PAPER.md:234`, `THIRD_CHAPTER.md:526`); venue recommendation JBHI/TBME (`JOURNAL_PAPER.md:98-109`) vs chosen IoT-J | Update to 15 × 3 = 45 sessions and 3140+ tests; write the missing figure script or drop the figure (CLAUDE.md §3.4); align the venue |
| M-18 | Three incompatible milestone numbering schemes (`implementation_plan.md` M0–M12, `plan_codex_milestones.md` M0–M5, `m8_ahmed_correction_plan.md` local M0–M5), plus the sidecar plan's "Milestone 0/A". "M0 removed" and "M0 READY" coexist. | HANDOFF names the governing scheme (codex) and prefixes references (e.g. "codex-M3"); add a "superseded" banner on the `implementation_plan.md` milestone map |
| M-19 | Wording that CLAUDE.md §4 forbids survives as *affirmative claims* about this study, contradicting HISTORY's "purge complete" (`:10070`): `implementation_plan.md:341, 350, 379`; `m8_step1b_ahmed_transfer.md:18, 62, 159, 393, 671`; `m4_offline_harness.md:103, 121, 359`. All hits in the manuscripts are prohibitions (fine). | Dated banners plus rewording of the listed lines; HISTORY entry |
| M-20 | Stale pointers and headers: `m4_offline_harness.md:13` says "under review"; `m8_step1b_ahmed_transfer.md:3` says "not authorized" (it was executed); `m4_stage12_review.md` is OPEN and waiting on an M0 freeze that no longer exists; the bin-drift plans point to "HANDOFF §3.1" for the relock decision (now clutter); `reports/m8_ahmed_correction_final_report.md:174` claims a HANDOFF pointer that is gone; **HANDOFF points to `capture_inventory.md` for the dev-capture hashes, but massimo3–7 are only in `experiments/m8_ahmed_transfer/capture_registry.yaml:49-125`** | One-line dated status banners; correct the pointers in the HANDOFF rewrite |
| M-21 | Uncommitted MATLAB work: `export_session_to_ti_mat.m` and `view_recording_2.m` are recorded nowhere. `export_session_to_ti_mat.m` holds the full ~2.9 GiB cube in RAM, against the HANDOFF guidance. `notes/matlab_export.md` has 11 absolute user paths. No review is recorded, although `animate_range_profiles.m` contains a range FFT (CLAUDE.md §6). | Record the files in HISTORY; replace the paths with `<repo>`; run an independent review before committing |
| M-22 | MATLAB tooling correctness: no completion marker, and `Overwrite` deletes the old output before parsing (`export_session_to_mat.m:45-48`); `frame_validity.npy` is ignored; `rawSha256` copies `live_raw_mirror_hash` instead of recomputing; generated JSON has `rawFileName == processedFileName` (`generate_rawdatareader_config.py:114-115`), so a TI tool could overwrite the raw input; no guard that `--output-dir` is outside the session dir; the frame-major matfile layout is slow; tests don't cover `iq_swap=False`, TX ≠ 1 or profile/hw mismatch | Distinct processed name; write-then-rename plus a marker; apply validity; output-dir guard; add the 5 missing tests |

### Test health

| ID | Discrepancy | Fix |
|---|---|---|
| M-23 | `test_diagnose_bin_drift.py::test_replayless_session_produces_zero_windows_and_no_outcome_leakage` fails when free RAM < 7 GB (the default `preflight_min_available_gb`). It failed once in this audit and passed on re-run. | Pass `preflight_min_available_gb=0.0001` or monkeypatch the memory probe |
| M-24 | HANDOFF's "5 skipped" is specific to this machine. On a clean clone about 17 tests skip (static count), including 10 in `test_score_offline.py` that need deleted replay dirs and have no marker. | State both counts in HANDOFF; add the optional-artifact marker |

---

## 4. LOW — cosmetic, dead code, or owner-controlled layout

| ID | Item | Fix |
|---|---|---|
| L-1 | `config/` frame counts disagree (5700 / 1200 / 3000 / 100; production 12000/continuous); `"awr1642"`; stale absolute paths to the old repo in `cf_vital.json:24`, `vital_signs.setup.json:4`; `txChannelEn 0x3` (harmless, chirps use TX0) | Mark `config/*` as historical mmWave Studio configs in a README line |
| L-2 | Config comments contradict values: COM7 "NOT this one" but `port: "COM7"`; step_4 `trim_frames: 0` vs "600"; motion factor 10 vs "start at 5"; range_plot range. Unused keys in `live_demo_config.yaml` (`max_live_duration_s`, `session.default_session_id`, `session.locked_bin`, `capture.raw_stream_format`) | Fix the comments; delete the unused keys |
| L-3 | Magic numbers in code (CLAUDE.md §2): warmup weights 1000/250/100/−100/50/−5, `_FUND_WEIGHT`, `MIN_CARDIAC_BAND_MARGIN_HZ`, 0.1×median prominence, `live_demo` `max_frames=12000`, `num_tx=1`; Step 6 floor-gate defaults of 0.0 when the key is missing | Move to config; make the Step 6 keys required |
| L-4 | Dead code (callers only in tests or other dead code): `radar_io.parse_logfile/infer_num_frames/range_profile/range_axis_m`, `vitals.run_pipeline/select_range_bin/phase_at_bin/VitalsParams` (1.3–1.6 m gate contradicts protocol), `compare.compare/metrics/overlay_plot`, `masimo.reference_pr`, `src/m4/manifest.py` (v2 only) | Keep the stage1*, `plot_bland_altman`, `run_pipeline_locked` and m4 manifest as provenance; delete the rest **only after** checking the M8 attested set (see the HISTORY 2026-08-26 orphan-delete incident) |
| L-5 | Scripts whose inputs were deleted: `compare_filter_fix_impact.py`, `diagnose_step6_hr.py`, `diagnose_coverage_gaps.py`, `diagnose_step6_candidate_tracks.py`, `br_bin_rule.py --mode test` | Add a docstring line "inputs deleted 2026-08-26; kept as provenance" |
| L-6 | `tests/conftest.py:3-5, 24-30, 71-76`: dead `--run-dir` fixture for the deleted `test_exp004*` | Delete, after checking whether conftest is attested |
| L-7 | Duplication: the I/Q decoder in 4 Python + 2 MATLAB copies; 13 file-hash helpers; `AHET_MAX_CANDIDATES` ×3; three parabolic interpolators; 8 scripts import `diagnose_bin_drift` although `outcome.py:7` says scripts must not be imported; `validate_warmup_selection.py:35` imports all of `live_demo` | Consolidate after H-5 lands |
| L-8 | Step 2 globs `{sid}_*.bin`, which would absorb a session named `{sid}_2` (`save_time_domain_cubes.py:75`); `np.polyfit` (LAPACK) is used in Step 3 and range_plot despite the LAPACK-avoidance policy | Match digits only (`{sid}_\d+\.bin`); use the LAPACK-free fit |
| L-9 | `ChirpConfig.iq_swap` defaults to `False`. Every call site passes it explicitly, so the risk is low. | Remove the default so it is required |
| L-10 | CLAUDE.md vs reality: dev captures live in `results/live_demo/`, not `data/raw/`; no experiment has `config.yaml` + `run.py`; `paper/` is absent; `.mcp.json` enables wandb while W&B is disabled; no `environment.lock.yml` (environment.yml asks for one) | Owner updates CLAUDE.md §2/§7; drop wandb from `.mcp.json`; commit `conda env export --no-builds` |
| L-11 | `.codex_pytest/` still exists although `HISTORY.md:11946` says it was deleted; `src/m2/cohort_registry.py:24` default `registry_v001` (only tests rely on it) | Note in HISTORY; optionally rename it to `GENESIS_REGISTRY_PATH` |
| L-12 | Git: no `main`; local-only `master`, `exp003-generalization`, `vital_signs_v4/v6/v7`; `v5` ahead of its remote by 6; `stash@{0}` (v9 relock WIP); commits named "s" (`b7947f1`) and "solv3" (`c82480d`) | OD-5; push or retire the branches; a HISTORY note explaining the two commits (never rewrite history) |

---

## 5. Checked and **not** discrepancies (do not re-raise)

- **Radar parameters are consistent** across `iwr1642.cfg`, lua, xml, mmwave.json, `capture_config.yaml`
  and `live_demo_config.yaml`: 77 GHz, 70.006 MHz/µs, 256 samples @ 5209 ksps → B = 3.44 GHz,
  ΔR = 0.0436 m, 20 Hz, complex1x, SampleSwap=1.
- **`iq_swap`:** production sets `true` (`live_demo_config.yaml:45`), the prospective metadata records
  `true`, and `estimator_runner.py:731` refuses a mismatch (Verified).
- **Cohort registry:** all 10 `.sha256` files match; the v001→v010 `previous_registry_sha256` chain is
  intact; 9/45 captured; all labels sealed.
- **Environment:** installed versions match `environment.yml` (py 3.11.15, numpy 1.26.4, scipy 1.13.1,
  pandas 2.2.3, mpl 3.8.4, h5py 3.11.0, pyside6 6.7.3).
- **Test count:** the tracked suite gives 3140 passed / 5 skipped, as HANDOFF claims. The +4 is the
  untracked generator test.
- **MATLAB_CLI rule:** complied with. No agent ran MATLAB; `notes/matlab_export.md` only prints commands.
- **Mentions of deleted files that are intentional, not dangling:** `m9_*_finding.md`,
  `relocking_bin_plan.md`, `m0_preregistration.md` (each marked deleted where cited);
  `experiments/bin_drift/config.yaml` (only a suggestion); `scripts/m2_scaffold_sidecar.py` (planned,
  though see H-1).
- **M-1 does not contaminate any HISTORY number** (reviewer traced the quoted values to gated
  functions).
- **H-9 tuning data was not reused** for any recorded result.
- **Forbidden wording in the manuscripts:** every hit is a prohibition, not a claim.

---

## 6. Threats to validity (not defects, but they belong in the paper)

- **T-1: the stationarity gate works against the recovery arm.** The p90−p10 ≤ 5 bpm per 30 s gate
  removes the steep part of the recovery decay, and the ≥20 bpm range criterion
  (`analysis_prespec.md:307-321`) is computed on admitted windows only. Report the admitted fraction
  per recovery minute.
- **T-2: the canonical scorer has no null baseline.** `estimator_scoring` has no constant
  session-median predictor; only M8/M9 do. M1's MAE of 2.77 bpm rests on 9 windows from low-dynamic-range
  sessions, where that null scores ≈100%. Add the null and a label-permutation test.
- **T-3: the frame-0 time origin is approximate** (5–15 s) for all development captures.

---

## 7. Fix order

1. **Owner decisions** OD-1 … OD-5 (§1). No code.
2. **Before the next capture:**
   - H-1: settle-floor rule and validator, per OD-1.
   - H-7: reference-access guard, and MATLAB examples moved off P00x.
   - M-8: dispose of the unregistered attempts; evidence-basename check.
   - M-9: HISTORY entry for the P001–P006 capture sessions.
3. **Correctness fixes that change numbers.** Each needs a synthetic test plus the independent review
   required by CLAUDE.md §6. They can be batched into one re-score.
   - H-5: `refine_freq_hz`. Measure the frequency of the problem first.
   - M-1, M-2, M-3: ungated / legacy scoring.
   - H-8: tracker relabelling.
   - H-10: `arm_loa`.
   - Then re-run `score_production.py` and append a HISTORY entry with the re-derived M1, the bin-policy
     tables and the M8 legacy numbers.
4. **Claims and manuscripts:** H-3, H-4, H-6, H-9, M-17, T-1 … T-3.
5. **Records:**
   - one HISTORY append covering H-2, H-11, M-5, M-8, M-9, M-19 and L-11;
   - then the HANDOFF rewrite (H-1, H-2, M-18, M-20, M-24);
   - then the notes and plan banners (M-16, M-19, M-20).
6. **Hygiene:** M-10 (renormalize line endings; do this in the same commit as step 5), M-23, M-11,
   M-15, M-21, M-22, then §4.

---

## 8. Not verified in this audit

- How often H-5 fires on real captures, which bounds its actual bias.
- The TI rawDataReader's expectations for device name and file names (M-22). `rawDataReader.m` is not
  in the repo.
- Whether the recovery arm's chest velocity exceeds the impulse clip (M-13a).
- The contents of `m2_capture_work/*` beyond file names (sealed).
- The skip count on a clean clone (M-24 is a static estimate).

