# Plan: Offline scoring script (`scripts/score_offline.py`)

> **REVIEW CLOSED (round 7, 2026-07-28):** `plans/offline_scoring_script_cross_review.md`'s
> `COMMENTS OF CODEX` reads the literal string `NO MORE COMMENTS`, with a closing assessment
> confirming revision 7 resolves OSR-19 completely and introduces no new inconsistency; every
> `DEBATE COMMENTS` item across all 7 rounds is either applied or was escalated to and decided by
> the user (OSR-01, OSR-04, OSR-16, OSR-06 R2, OSR-16 R2, and OSR-03's round-4 reopening — 6
> user decisions total, all recorded in the "User decisions" sections below). **Revision 7 is the
> build authority — implementation of `src/comparator.py` and `scripts/score_offline.py` may now
> begin**, per CLAUDE.md §5.
>
> **REVISION 7 — after cross-review round 6.** Round 6 found one Blocking gap, resolved directly
> (no user decision needed): `br_session_type` (natural/paced, needed since revision 3 for BR
> §2.6 reporting) had no declared source at all — verified directly that none of the 3 real
> `run_metadata.json` files carry a natural/paced field, so it could be neither read from
> metadata nor safely inferred from `--paced-schedule`'s presence (sweep is paced with no
> schedule). Fixed with a required `--session-type` CLI input plus a mandatory,
> mutually-exclusive `--paced-schedule`/`--paced-target-unavailable` pairing for every paced
> capture, validated before any scoring. All round-1–5 findings remain resolved.

---

## User decisions from cross-review round 1 (2026-07-28)

- **OSR-01 (approximate origin):** RUN scoring using `start_wall_utc` as a fallback origin when no
  persisted true `frame0_epoch` exists (true for all 3 current captures) — but every artifact must
  carry the caveat, and the result is **provisional/informal evidence** toward the guard_v1 call,
  **never** a final or frozen ruling.
- **OSR-04 (estimand choice):** compute **both** the pinned-bin (isolated `eca_mode` effect) and
  rerun-warmup (operational — what production would actually select/do) results, always, clearly
  labeled, **never pooled**.
- **OSR-16 (BR scope):** implement BR's mandated `comparator_prespec_br.md` §2.5 paced-metronome
  cross-check and §2.6 natural/paced separate reporting **now**, in this same build — BR is "in
  full" as originally claimed, not partially.

## User decisions from cross-review round 2 (2026-07-28)

- **OSR-06 R2 (§2.4 "excluded-by-PI" vs. §2.1's single usable-set denominator):** a genuine
  tension inside the frozen `comparator_prespec.md` itself — §2.4 lists "excluded-by-PI" as its
  own report category; the later §2.1 pre-deposit clarification folds PI into one single
  usable-sample denominator ("there is exactly one denominator"). Decided: **§2.1 supersedes
  §2.4's separate PI bucket** — report only `excluded_by_coverage` (the single usable-set
  denominator) plus `n_pi_qualified`/`n_finite_pr` as diagnostics; no separate PI exclusion
  bucket is constructed.
- **OSR-16 R2 (sweep metronome schedule):** verified directly — no file anywhere (`run_metadata.json`
  or otherwise) persists the sweep capture's actual step-transition timestamps; only
  `notes/protocol.md`'s nominal 120 s-per-step schedule exists. Decided: **refuse metronome
  target-concordance for the sweep capture** rather than fabricate a schedule from nominal
  timing — sweep is scored BR RRp-only, still labeled a **paced** session per §2.6 (never
  "natural" — round 3, OSR-16 R3, corrected an earlier draft's mislabel), just with no
  metronome column.
  Only `massimo2` (one constant commanded rate, no transitions to mistime) gets §2.5's metronome
  cross-check in this build.

## User decisions from cross-review round 4 (2026-07-28)

- **OSR-03, round 4 (the exact lock value, not just eca_mode identity):** verified directly —
  the original massimo1/massimo2 live capture directories (locks 23/20) are recorded under the
  production `eca_mode` and trivially hash-bind to themselves, so revision 4's
  `--reproduction-baseline-eca-mode` check alone would still accept them as "reproducing the
  measured methodology" despite not matching the actual measured locks (27/26). Decided: **add
  an explicit expected-lock check** — `--reproduction-baseline-lock <capture>=<int>`, asserted
  against the lock source's own recorded `locked_bin`, required alongside
  `--reproduction-baseline-eca-mode`. A declined alternative (dropping "reproducing the measured
  methodology" framing entirely and captioning every pinned run as a generic diagnostic) was not
  chosen.

---

## Context

HANDOFF.md §3.1 names this the immediate next action. Two things currently have no
number behind them:

1. `guard_cardiac_candidate_v1` (an alternate ECA mode) was measured to raise HR
   *coverage* (17.6→21.6%, 54.9→70.6%, 23.2→27.8% across the 3 Masimo captures,
   `experiments/exp_eca_modes/config_guard_v1.yaml`) — but that measurement never
   touched the Masimo reference, so whether the *extra* covered windows are actually
   correct (vs. just more frequent wrong answers) is unverified.
2. M8/M9 (simulation reproductions of the two reference papers, HANDOFF §3.3) will need
   the same MAE/RMSE/coverage machinery to score their estimators against Masimo — there
   is currently no reusable path to do that at all.

The project has a **binding, pre-registered comparator spec** for exactly this
(`notes/comparator_prespec.md` for HR, `notes/comparator_prespec_br.md` for BR) and a
**frozen window grid** already implemented (`src/m4/window_grid.py`) — both written for
the now-deprioritized M4 multi-subject harness, but neither depends on M4's manifest
machinery. The gap is a script that (a) decodes a real capture, (b) runs the shared DSP
per frozen 30 s window, (c) computes the reference per the frozen spec, and (d) reports
MAE/RMSE/coverage. Nothing here needs the M4 manifest, multi-subject weighting, or
cluster bootstrap (§3.3/§3.4 stats machinery) — that apparatus is out of scope, per
HANDOFF, for this narrower single-capture use.

**Known, unavoidable limitation, stated up front:** none of the 3 existing Masimo
captures has a persisted `frame0_epoch` (the true UTC time of frame 0). Only
`run_metadata.json`'s `start_wall_utc` exists, which is written *before* capture startup
and is therefore an **approximate** origin (same limitation already documented for
`scripts/derive_br_comparator_evidence.py`). Per the user's OSR-01 decision, the script
still runs on this origin, but **every output artifact — CSV rows, JSON, text reports, and
stdout — must carry the caveat**, and the result is reported as **provisional evidence**
toward the guard_v1 decision, never as "the answer" or a frozen score.

## Scope for this build

**In scope:** HR (PR) scoring per `comparator_prespec.md` in full (usable-sample gate,
median reference, coverage gate, stationarity gate, sensitivity table, separate
`n_finite_pr`/`n_pi_qualified` diagnostics per the §2.1 clarification). BR (RRp) scoring
per `comparator_prespec_br.md` **in full**, including §2.5's paced-metronome
target-concordance cross-check and §2.6's natural/paced separate reporting (per the
OSR-16 decision — this needs per-capture commanded-rate metadata, supplied explicitly via
CLI, see below). Both estimands — pinned-bin and rerun-warmup (OSR-04) — computed and
reported separately, never pooled. Coverage/rejection breakdown reusing the bin-drift
diagnostic's `classify_window_outcome`. The incremental-coverage question (is guard_v1's
*extra* coverage also *correct*) answered directly via a config-coverage partition, not
via `paired_metrics`'s finite-in-all-conditions intersection (which structurally excludes
exactly the windows in question — OSR-02).

**Explicitly out of scope for this pass:** multi-subject weighting, ANOVA LoA, cluster
bootstrap CI (M0-freeze stats, `plans/m4_offline_harness.md` §6.4-6.6) — those are for the
abandoned multi-subject freeze, not this narrower use.

## New code

### 1. `src/comparator.py` (new module) — the frozen comparator spec, implemented

Distinct from `src/masimo.py` (raw CSV parsing — untouched) and from `src/compare.py`'s
existing `compare()`/`metrics()` (mean-based, explicitly marked **SUPERSEDED** by
`comparator_prespec.md` — left as-is, not deleted, since `paired_metrics`/`coverage_table`
in the same file are still reused below in a clearly-labeled supplementary role).

```python
def hr_reference(df: pd.DataFrame, epoch_start: float, epoch_end: float) -> dict:
    """comparator_prespec.md §2.1/§2.2. Usable = finite pr_bpm AND finite pi AND pi>=0.5.
    Every primary-gate constant (min_pi=0.5, min_usable=24, stationarity_max_bpm=5.0) is
    an internal module constant cited to its spec section — NOT a function parameter
    (OSR-10): no caller can silently alter a frozen threshold and still get output
    labeled under this function's name.
    Returns: n_total, n_finite_pr, n_pi_qualified, n_usable (all reported separately per
    the §2.1 clarification, not collapsed to one count — OSR-06), median_pr_bpm (NaN if
    gated out), coverage_ok (n_usable >= 24), spread_bpm (p90-p10 of the usable set,
    method='linear'), stationarity_ok (spread <= 5.0, strict >), admitted (coverage_ok and
    stationarity_ok), sensitivity = {3.0: bool, 5.0: bool, 8.0: bool} (computed only among
    windows with a defined spread, i.e. coverage_ok — same denominator convention as
    scripts/derive_br_comparator_evidence.py).
    NOTE (OSR-06 R2, user decision): §2.4's "excluded-by-PI" report category is superseded
    by §2.1's later single-usable-set clarification — there is no separate excluded-by-PI
    aggregate anywhere in this module or in Step 10's reference marginal; `n_pi_qualified`/
    `n_finite_pr` remain per-window diagnostics only, never a second exclusion bucket."""

def br_reference(df: pd.DataFrame, epoch_start: float, epoch_end: float) -> dict:
    """comparator_prespec_br.md §2.1/§2.2. No PI gate (flag only); internal constants
    min_finite=24, stationarity_max_bpm=2.0, not caller-configurable (OSR-10).
    Returns: n_total, n_finite_rr, availability_ok (n_finite_rr >= 24), median_rr_bpm,
    spread_bpm (p90-p10 of finite set, method='linear'), stationarity_ok (spread <= 2.0,
    strict >, so exactly 2.0 is retained per M3R-39), admitted, sensitivity =
    {2.0: bool, 3.0: bool, 5.0: bool}, pi_median (reported, never gates)."""

def br_metronome_concordance(radar_br_bpm: float, commanded_rate_bpm: float) -> dict:
    """comparator_prespec_br.md §2.5 — the paced target-concordance cross-check.
    RRp (br_reference) remains the measured reference; this is reported ADDITIONALLY and
    separately, never blended into or substituted for RRp agreement. Returns
    target_error_bpm = radar_br_bpm - commanded_rate_bpm (NaN if radar_br_bpm is NaN).
    Only computed for windows with a known commanded rate (see --paced-schedule below);
    natural sessions never call this (§2.6 — natural BR is "compared with RRp", never
    "validated against", and has no metronome). Per the OSR-16 R2 user decision, this is
    called for `massimo2` only in this build — the sweep capture has no persisted
    transition timing (see the Paced-schedule subsection below) and is deliberately
    excluded rather than scored against a fabricated nominal schedule."""
```

Both `hr_reference`/`br_reference` pass `method="linear"` explicitly to `np.percentile`
at every call site (never the library default — CLAUDE.md, M4R-09). Both use the exact
half-open window already computed by `src/m4/window_grid.py:window_reference_span` — this
module only adds the reference-side gate logic, not a second window convention.

### 2. `scripts/score_offline.py` (new script) — the scoring loop

Reuses, does not reimplement:
- `src.radar_io.ChirpConfig`, `read_adc_bin` — decode `adc_stream.bin`.
- `src.m4.window_grid.build_window_grid(n_frames, frame0_epoch, fs=validated_fs,
  frames_per_win=600)` — the frozen grid; `n_frames = cube.shape[0]`; `validated_fs` is
  Step 2's cross-checked rate, never a fresh `20.0` literal (OSR-05 R2).
- `src.window_pipeline.run_window_dsp(cube[frame_start:frame_end], locked_bin, validated_fs,
  cfg)` per window (OSR-05 R3 — `validated_fs` at every call site, never a bare `fs`),
  immediately normalized via `src.window_pipeline.as_window_estimate` — all
  scoring reads the normalized `hr_bpm`/`hr_valid`/`br_bpm`/`br_valid` fields, **never**
  `dsp["hr_raw"]`/`dsp["br_bpm"]` directly (OSR-12: `run_window_dsp`'s native `br_bpm` is a
  diagnostic value that can be finite even when `br_valid` is False — verified directly
  against `src/window_pipeline.py`: `"br_bpm": float(br_result.get("radar_rr_bpm",
  np.nan))` is set unconditionally, independent of `resp_valid`).
- `src.warmup_select.run_warmup_selection` — for the rerun-warmup estimand only; confirmed
  radar-only (no Masimo import anywhere in `src/warmup_select.py`), so this never leaks
  reference data into bin choice (CLAUDE.md §4).
- `classify_window_outcome` from `scripts/diagnose_bin_drift.py` (imported via
  `sys.path.insert(0, str(REPO/"scripts")); import diagnose_bin_drift`), called with
  `dsp["f_r_hz"]` **mapped `None → float("nan")` before the call** — verified directly
  that `np.isfinite(None)` raises `TypeError` in this environment, so passing `None`
  straight through would crash on every ordinary gate-not-run window (OSR-09), which is a
  large fraction of real data. Pass `dsp["hr_result"]["accepted_candidate_rank"]` and
  `dsp["hr_result"]["candidate_rejection_code"]` exactly — not a summary-only field.
- `src.compare.paired_metrics` / `coverage_table` — retained only as a **supplementary**
  common-finite-only report, explicitly captioned "common-window comparison only, not the
  incremental-coverage answer" (see Step 11 below — OSR-02).
- `src.masimo.load_masimo` — see Masimo discovery below (OSR-11).
- (Reused if its signature is capture-agnostic, else replicated identically)
  `scripts/diagnose_bin_drift.py`'s decode-geometry validation logic, extended to every
  scoring config (OSR-05).

**CLI:**
```
scripts/score_offline.py
  --captures <dir> [<dir> ...]           # results/live_demo/<ts>_..._<session> dirs (raw
                                          #   ADC bytes + Masimo CSV actually scored)
  --configs <label>=<path.yaml> [...]    # 1+ full config files
  --pinned-lock-source <capture>=<dir|int> [...]
                                          # REQUIRED per capture for the pinned estimand.
                                          #   An explicit directory carrying its own
                                          #   run_metadata.json (e.g. one of the
                                          #   2026-07-26 replay dirs, NOT necessarily the
                                          #   --captures dir itself) or a bare int.
                                          #   NEVER implicitly assumed equal to the
                                          #   scored capture's own recorded lock (OSR-03:
                                          #   the original captures' own locks are 23/20/21;
                                          #   the locks behind the quoted 17.6/54.9/23.2%
                                          #   coverage numbers are the 2026-07-26 replay
                                          #   generation's 27/26/26 — a different
                                          #   generation, verified directly against both
                                          #   sets of run_metadata.json). A directory source
                                          #   is REJECTED unless it is bound to the exact
                                          #   scored capture (OSR-03 R2 — see Step 4); a
                                          #   bare int is accepted unconditionally but is
                                          #   labeled a manual diagnostic source, never a
                                          #   reproduction of the measured methodology.
  --estimands {pinned,rerun,both}        # default: both (OSR-04) — computed and reported
                                          #   separately, never pooled or averaged. WHEN
                                          #   --isolate-fields IS ALSO GIVEN (i.e. this is a
                                          #   causal/promotion-framed comparison), `both` is
                                          #   REQUIRED — the script refuses `pinned` or
                                          #   `rerun` alone in that mode, so a single-
                                          #   estimand run can never carry promotion framing
                                          #   (OSR-04 R2). Outside that mode, a single
                                          #   estimand remains allowed for a general
                                          #   diagnostic run, captioned as incomplete with no
                                          #   promotion conclusion.
  --isolate-fields <dotted.path> [...]   # optional. When given: (a) requires EXACTLY TWO
                                          #   --configs (OSR-13 R2); (b) diffs the loaded
                                          #   config dicts field-by-field and raises unless
                                          #   every differing leaf is inside this allowlist;
                                          #   (c) raises if the diff is EMPTY at the declared
                                          #   field(s) — two configs that don't actually
                                          #   differ there cannot claim "eca_mode is the only
                                          #   variable" (OSR-13 R2 — closes the vacuous-pass
                                          #   case). Records the observed before/after value
                                          #   at each declared field in the paired report and
                                          #   provenance. Required for the guard_v1-vs-
                                          #   production comparison to claim isolation;
                                          #   omitted for a general multi-config/M8/M9
                                          #   comparison, captioned as non-causal.
  --reproduction-baseline-eca-mode <capture>=<value> [...]
                                          # REQUIRED per capture whenever --isolate-fields
                                          #   is used with a DIRECTORY --pinned-lock-source
                                          #   for that capture (OSR-03 R3). Names the
                                          #   `heart.eca_mode` the lock source must have been
                                          #   generated under for this run to be captioned as
                                          #   "reproducing the measured coverage methodology"
                                          #   (e.g. skip_forbidden_harmonics_v1, the config
                                          #   the quoted 17.6/54.9/23.2% figures were measured
                                          #   under). Asserted against the lock source's own
                                          #   recorded config (Step 4) — hash-binding alone is
                                          #   NOT sufficient, since a same-raw-capture replay
                                          #   generated under a DIFFERENT eca_mode (e.g. a
                                          #   guard_cardiac_candidate_v1 replay) passes the
                                          #   hash check too (verified concretely: massimo1's
                                          #   `20260727_182319_replay_unknown` shares its raw
                                          #   hash with the production replay
                                          #   `20260726_173434_replay_unknown` and even
                                          #   happens to share numeric lock 27, but was
                                          #   generated under `guard_cardiac_candidate_v1`,
                                          #   not the production config). Mismatch raises.
  --reproduction-baseline-lock <capture>=<int> [...]
                                          # REQUIRED alongside
                                          #   --reproduction-baseline-eca-mode, same
                                          #   trigger condition (OSR-03, round 4). Names
                                          #   the exact `locked_bin` the lock source must
                                          #   carry. eca_mode-matching is NOT sufficient on
                                          #   its own: verified concretely that the
                                          #   ORIGINAL massimo1/massimo2 capture
                                          #   directories (locks 23/20) are recorded under
                                          #   `eca_mode=skip_forbidden_harmonics_v1` and
                                          #   trivially hash-bind to themselves, so they
                                          #   would pass both the raw-hash and
                                          #   `--reproduction-baseline-eca-mode` checks —
                                          #   yet the quoted 17.6%/54.9% coverage figures
                                          #   were measured at locks 27/26 (the 2026-07-26
                                          #   replay generation), not 23/20. Asserted
                                          #   against the lock source's own recorded
                                          #   `locked_bin` — hard raise on mismatch. For the
                                          #   guard-v1 reproduction, this is 27/26/26 (see
                                          #   Verification §3).
  --masimo-csv <capture_dir>=<path>      # optional override; default auto-discovery (below)
  --session-type <capture>=<natural|paced>  # REQUIRED for EVERY --captures entry (OSR-19)
                                          #   — verified directly that none of the 3 real
                                          #   `run_metadata.json` files carry any
                                          #   natural/paced field (only generic session IDs
                                          #   like `demo_massimo1`), so `br_session_type`
                                          #   cannot be read from existing metadata or
                                          #   inferred from whether `--paced-schedule` was
                                          #   given (sweep is paced with no schedule).
                                          #   This is the sole, explicit, provenance-
                                          #   recorded source of `br_session_type` — never
                                          #   guessed from a filename/session ID.
  --paced-schedule <capture>=<bpm|path>  # a constant bpm (massimo2: 16.0) or a path to a
                                          #   committed, hashed schedule file (see the Paced
                                          #   schedule format subsection below — OSR-16 R2).
                                          #   Enables §2.5 metronome cross-check for that
                                          #   capture. REQUIRES `--session-type
                                          #   <capture>=paced`; rejected if that capture's
                                          #   `--session-type` is `natural` (contradictory).
  --paced-target-unavailable <capture>=<reason>  # REQUIRED for a `paced` capture that gets
                                          #   NO `--paced-schedule` (OSR-19) — e.g. `sweep`:
                                          #   `unavailable_missing_transition_timestamps`
                                          #   (verified directly: no file anywhere persists
                                          #   sweep's actual step-transition timestamps,
                                          #   only `notes/protocol.md`'s nominal 120 s-per-
                                          #   step schedule — OSR-16 R2 user decision).
                                          #   Mutually exclusive with `--paced-schedule` for
                                          #   the same capture. **Every `paced` capture must
                                          #   get EXACTLY ONE of `--paced-schedule` or
                                          #   `--paced-target-unavailable`** — never both,
                                          #   never neither. A `paced` capture with neither
                                          #   raises (this is the "forgotten target" case
                                          #   Codex specifically warned would otherwise be
                                          #   silently indistinguishable from sweep's
                                          #   deliberate, approved omission); a `natural`
                                          #   capture given either raises (contradictory).
  --allow-dirty                          # required to score on an uncommitted tree;
                                          #   default REFUSES (mirrors
                                          #   scripts/diagnose_bin_drift_config.yaml's
                                          #   existing require_clean_tree/--allow-dirty
                                          #   pattern — reused, not invented, OSR-08)
  --out <dir>                            # default results/score_offline/<UTC timestamp>/
```

**Per capture × config × estimand:**
1. Read the capture's `run_metadata.json`: `iq_swap`, `start_wall_utc`, capture-time
   `git_commit`/`git_dirty`, full recorded `config` block (`profile`/`session`/`hw_frame`).
1b. **Validate the `--session-type`/`--paced-schedule`/`--paced-target-unavailable`
   combination for this capture, once, before any per-config/per-window work** (OSR-19):
   `--session-type` must be present for this capture (raise if missing — never inferred).
   If `natural`: raise if either `--paced-schedule` or `--paced-target-unavailable` is
   also given for it (contradictory — a natural session has no commanded rate to be
   unavailable or present). If `paced`: raise unless **exactly one** of `--paced-schedule`
   or `--paced-target-unavailable` is given (neither = the "forgotten target" case that
   must fail loudly, not silently score as if natural or as if deliberately unavailable;
   both = ambiguous). This validated `session_type` (and, for `paced`+unavailable, the
   declared reason string) is the **sole source** of `br_session_type`/`br_metronome_status`
   in Steps 7/8 below — never inferred from a filename, session ID, or the mere presence/
   absence of `--paced-schedule`.
2. Load the `--configs` YAML. **Validate the full decode geometry**, not just `iq_swap`
   (OSR-05): cross-check `profile.{num_adc_samples, num_rx, num_chirps_per_frame,
   range_resolution_m, iq_swap}` and `session.frame_rate_hz` against the capture's
   recorded config, and cross-check the recorded `session.frame_rate_hz` against
   `1000 / hw_frame.period_ms`; raise loudly, listing the exact mismatched field(s), on
   any disagreement. A wrong-but-byte-count-compatible geometry (e.g. a swapped
   `num_rx`/`num_chirps_per_frame`) must be caught here, not silently reshaped. This step
   produces exactly one `validated_fs` value (the cross-checked, agreed-upon frame rate) —
   **every** downstream call (`ChirpConfig`, `run_window_dsp`, `run_warmup_selection`,
   `build_window_grid`, Step 6) is threaded this same variable, never a fresh `20.0`
   literal (OSR-05 R2 — see Step 6).
3. Build `ChirpConfig` from the validated fields (including `frame_rate_hz=validated_fs`);
   `read_adc_bin`.
4. Resolve `locked_bin` for **both** estimands (OSR-04):
   - **pinned:** from `--pinned-lock-source`.
     - **Directory source:** read that directory's own `run_metadata.json["locked_bin"]`.
       Before accepting it, **bind it to the capture actually being scored** (OSR-03 R2 —
       recording provenance is not the same as validating it): if the source directory's
       `mode == "replay"`, require the current capture's `adc_stream.bin` SHA-256 to appear
       among that directory's own `replay_file_hashes`; if `mode == "live"` (using an
       original capture directory as its own lock source), require its
       `live_raw_mirror_hash` to equal the current capture's raw SHA-256. **Raise** on any
       mismatch — a directory from a different capture must never silently supply a
       "provenance-stamped" lock. Record the matched key/hash and the source directory's
       own recorded `config.heart.eca_mode`/`run_config_hash` as `lock_source_config_identity`
       in provenance.
       **Hash-binding alone is insufficient (OSR-03 R3)** — a directory that replays the
       *same* raw capture under a *different* `eca_mode` passes the raw-hash check too, and
       this is not hypothetical: `results/live_demo/20260727_182319_replay_unknown` was
       verified directly to replay massimo1's exact raw bytes (identical SHA-256 to the
       production replay `20260726_173434_replay_unknown`) and even happens to share the
       numeric lock `27`, but its recorded `config.heart.eca_mode` is
       `guard_cardiac_candidate_v1`, not the production `skip_forbidden_harmonics_v1` the
       quoted 17.6/54.9/23.2% coverage figures were measured under. **Whenever
       `--isolate-fields` is given for this capture, additionally require
       `--reproduction-baseline-eca-mode <capture>=<value>` and assert the lock source's
       recorded `config.heart.eca_mode` equals it exactly — raise, not merely record, on a
       mismatch.**
       **eca_mode-matching alone is still insufficient (OSR-03, round 4, user decision) —
       the exact `locked_bin` value must be asserted too.** Verified concretely: the
       *original* live capture directories `results/live_demo/20260713_172042_live_demo_massimo1`
       (`locked_bin: 23`) and `..._massimo2` (`locked_bin: 20`) are recorded under
       `config.heart.eca_mode: "skip_forbidden_harmonics_v1"` and trivially hash-bind to
       themselves (they *are* the scored capture) — so pointing `--pinned-lock-source` at
       the original directory itself would pass both the raw-hash check and an
       `--reproduction-baseline-eca-mode=skip_forbidden_harmonics_v1` check, yet locks
       23/20 are **not** the locks the quoted 17.6%/54.9% figures were measured at (those
       are 27/26, from the 2026-07-26 replay generation). **Whenever
       `--reproduction-baseline-eca-mode` applies, also require
       `--reproduction-baseline-lock <capture>=<int>` and assert the lock source's own
       recorded `locked_bin` equals it exactly — raise on mismatch.** Both assertions
       (config identity + exact lock) are CLI-parameterized, not hardcoded literals, so
       they generalize to other M8/M9 "reproduce methodology X" reproductions with a
       different expected baseline. Still declined: a separate pre-approved-tuple registry
       file (as `scripts/diagnose_bin_drift_config.yaml`'s `approved_replays` has) — the
       raw-hash binding plus these two CLI-declared assertions together close the
       demonstrated loophole without that additional machinery.
     - **Bare-int source:** accepted unconditionally, but labeled in every output
       `lock_provenance.kind = "manual"` — it may **never** be captioned as reproducing the
       measured coverage methodology (only a hash-bound directory source may be).
   - **rerun:** a fresh `run_warmup_selection(cube[:warmup_window], candidate_bins, cfg,
     validated_fs)` call for this specific config — may select a different bin per config
     (observed precedent: massimo1 under `guard_cardiac_candidate_v1` locks bin 25, not
     production's 27, per HANDOFF).
5. `frame0_epoch`: prefer a persisted true value if the capture's `run_metadata.json` ever
   carries one (future captures); otherwise fall back to `start_wall_utc`, and set
   `origin_source="start_wall_utc_approximate"`, `origin_is_approximate=True`,
   `comparator_status="exploratory_non_frozen"`. These three fields are carried in **every**
   `window_scores.csv` row, `summary.json`, every text report's header block, and a stdout
   banner printed at both the start and end of the run (OSR-01) — not only in `summary.json`.
6. `windows = build_window_grid(n_frames, frame0_epoch, fs=validated_fs, frames_per_win=600)`
   — **`validated_fs` from Step 2, never a fresh `20.0` literal** (OSR-05 R2: passing a
   literal here made the "independent second check" claim false, since `_require_frozen_grid`
   could never observe a real mismatch that Step 2 failed to catch). With `validated_fs`
   threaded through, `build_window_grid`'s own `_require_frozen_grid(validated_fs, 600)`
   check is a genuinely independent second guard: it fails on a bad rate even if Step 2's
   validation were buggy or bypassed.
7. Per window: `dsp = run_window_dsp(cube[frame_start:frame_end], locked_bin, validated_fs,
   cfg)` (OSR-05 R3 — **`validated_fs`, not a bare `fs`**: revision 3's Step 2/6 text
   established `validated_fs` as the one variable threaded everywhere, but this call site's
   own wording still said `fs`, an undefined alias in this plan's own notation; fixed here,
   and no other call site in this plan uses a bare `fs`);
   `est = as_window_estimate(dsp, estimator_id=ESTIMATOR_ID, run_config_hash=run_config_hash(cfg))`.
   All scoring below reads `est.hr_bpm/est.hr_valid/est.br_bpm/est.br_valid` (OSR-12).
   **Outcome classification requires `cfg["heart"]["ahet_gate_mode"] == "strict_v1"`**
   (OSR-17): `classify_window_outcome` is a fail-closed classifier for the `strict_v1`
   producer contract specifically (verified against `scripts/diagnose_bin_drift.py` and
   `src/vitals.py`'s legacy-vs-strict return states) and **raises** on a row the `legacy`
   gate mode can produce but `strict_v1` cannot. Before calling it, assert
   `cfg["heart"]["ahet_gate_mode"] == "strict_v1"` once per config (not per window); if a
   `--configs` entry uses a different gate mode, `outcome_class` is recorded as
   `"unavailable_non_strict_gate_mode"` for every window of that config and
   `classify_window_outcome` is never called for it — HR/BR reference scoring (Steps 8/10/11)
   continues normally, since those are estimator-agnostic and do not depend on the outcome
   taxonomy. When the gate mode check passes: `f_r_hz_for_outcome = float("nan") if
   dsp["f_r_hz"] is None else dsp["f_r_hz"]`; `outcome_class =
   classify_window_outcome(dsp["hr_result"]["accepted_candidate_rank"],
   dsp["hr_result"]["candidate_rejection_code"], f_r_hz_for_outcome)` (OSR-09).
   `hr_reference(masimo_df, epoch_start, epoch_end)` and
   `br_reference(masimo_df, epoch_start, epoch_end)` on the window's own span.
   `br_session_type` = this capture's validated `--session-type` (Step 1b) — never
   inferred. If `--session-type == "paced"` and `--paced-schedule` was given for this
   capture (massimo2 in this build): look up the commanded rate at this window's time;
   `br_metronome_status = "available"` if the window's full span lies in one schedule
   interval, else `"unavailable_transition"` (Paced-schedule subsection); when
   `"available"`, call `br_metronome_concordance(est.br_bpm, commanded_rate)`. If
   `--session-type == "paced"` and `--paced-target-unavailable` was given instead (sweep in
   this build): `br_metronome_status` = the declared reason string
   (`"unavailable_missing_transition_timestamps"` for sweep) for every window of this
   capture; `br_metronome_concordance` is never called. If `--session-type == "natural"`:
   `br_metronome_status = "not_applicable"`; `br_metronome_concordance` is never called.
8. Persist per-window row to `window_scores.csv`. **Every row is self-describing** — a
   detached CSV must not lose the origin caveat (OSR-01 R2: Step 5 said these fields are
   carried in every row, but the enumerated schema below previously omitted two of them;
   fixed here). Columns: `k, frame_start, frame_end, epoch_start, epoch_end, frame0_epoch,
   origin_source, origin_is_approximate, origin_caveat (a short fixed string, e.g.
   "start_wall_utc is pre-capture-start and approximate; this row is provisional evidence,
   not a frozen score"), comparator_status, hr_valid, hr_bpm (from `est`),
   outcome_class, rej_reason, masimo_hr_n_total, masimo_hr_n_finite_pr,
   masimo_hr_n_pi_qualified, masimo_hr_n_usable, masimo_hr_coverage_ok, masimo_hr_median,
   masimo_hr_spread_bpm, masimo_hr_stationarity_ok, masimo_hr_admitted, hr_error_bpm (only
   if admitted AND hr_valid), br_valid, br_bpm (from `est`), masimo_br_n_finite,
   masimo_br_availability_ok, masimo_br_median, masimo_br_spread_bpm,
   masimo_br_stationarity_ok, masimo_br_admitted, br_error_bpm (only if admitted AND
   br_valid), br_pi_median_flag, br_session_type (`"natural"` | `"paced"` — sourced
   **exclusively** from the capture's validated `--session-type` (Step 1b), never inferred
   from whether a schedule was supplied or from a filename/session ID — OSR-16 R3/OSR-19:
   `sweep` is `"paced"` even though it gets no metronome cross-check), br_metronome_status
   (`"not_applicable"` for natural sessions, `"available"`, `"unavailable_transition"` for a
   window straddling a schedule interval, or the capture's declared
   `--paced-target-unavailable` reason — `"unavailable_missing_transition_timestamps"` for
   the whole of `sweep` — OSR-16 R3/OSR-19), br_commanded_rate_bpm (only if
   `br_metronome_status == "available"`), br_metronome_error_bpm (only if
   `br_metronome_status == "available"`)`.
9. Also write a per-capture/config/estimand `evidence.npz` (OSR-07/OSR-07 R2 — CLAUDE.md
   §5.4, "every estimate must leave evidence"). Round 1's field list (validity/rate
   scalars, candidate codes, `f_r_hz`, `phase_clean`) was too thin to actually diagnose a
   wrong or newly-covered window — CLAUDE.md's own minimum example names the unwrapped
   phase, heart-band spectrum, and picked peak, none of which round 1 fully carried.
   **Revised contract: persist every key `run_window_dsp` returns, per window, not a
   hand-picked subset** — `phase_raw`, `phase_clean`, the complete `hr_result` dict (rate,
   validity, `accepted_candidate_rank`, `candidate_rejection_code`, `spectrum_stage`, the
   frequency axis, pre-ECA/first-pass/final heart spectra, the filtered signal, and the
   picked/refined peak — the implementer must enumerate `estimate_rate_from_phase`'s exact
   return keys against `src/vitals.py` at build time; this plan has not itself inventoried
   every key, since the earlier reading only traced the subset `run_window_dsp` re-exposes
   by name), the complete `br_result`/`fft_r`/`ha_r`/`stft_r` dicts (the respiration
   evidence that produced `f_r_hz`), `baseline_spectrum`/`baseline_freqs_hz`, `k_max_eff`,
   `n_eca_projected`/`n_eca_cols_retained`/`n_eca_cols_dropped`, `eca_skipped_harmonics`,
   plus the locked bin and exact frame/epoch bounds. Store scalars stacked over `k`; store
   per-window arrays (spectra, frequency axes) stacked if their shape is constant across
   windows for a fixed config (expected, since window length is fixed at 600 frames), or
   with an explicit padded/sentinel representation, documented in the artifact itself, if
   any native field's shape is found to vary. This is the **native, possibly-non-scorable**
   dict content, kept only here and explicitly documented as diagnostic evidence for a
   surprising or newly-covered window — never a scoring source (ties back to OSR-12).
   `window_scores.csv`'s `k` is the stable join key into this file. Invalid estimates keep
   their evidence too, not just valid ones.
10. Aggregate into **two independent marginals plus one joint**, not a single sequential
    priority-ordered waterfall (OSR-06 — the frozen spec does not define a cross-domain
    priority between a reference-side gate and radar-NaN, and forcing one would hide a
    config's coverage effect behind reference-side accounting):
    - **Reference marginal** (radar/config-independent — same for every config/estimand on
      a given capture): `n_windows, n_excluded_coverage (n_usable<24 for HR /
      n_finite_rr<24 for BR), n_excluded_stationarity (gate passed but spread over
      threshold), n_admitted` — mutually exclusive, sums to `n_windows`. **No separate
      `excluded_by_pi` bucket** (OSR-06 R2, user decision): `comparator_prespec.md` §2.4
      literally lists "excluded-by-PI" as its own category, but the later §2.1 clarification
      folds PI into the single usable-set denominator that IS `n_excluded_coverage` — the
      user decided §2.1 supersedes §2.4's older 3-category framing. `n_pi_qualified`/
      `n_finite_pr` remain per-window diagnostics (Step 8) for distinguishing a
      missing-data-driven coverage failure from a low-perfusion-driven one, but are not
      aggregated into a second exclusion bucket.
    - **Radar marginal** (per config × estimand, reference-independent): `n_valid,
      n_radar_nan`, plus the full `outcome_class` breakdown (`covered`/`gate_not_run`/
      `other_rejected`) computed over **all** windows, not just reference-admitted ones.
    - **Joint** (the actual scoring set): the 2×2 cross-tab {admitted, excluded} ×
      {valid, radar_nan}; `final_n = admitted ∧ valid` is what MAE/RMSE/bias are computed
      over, for both HR and BR.
    - Sensitivity fractions (HR: 3/5/8 bpm; BR: 2/3/5 bpm) computed over windows that pass
      the coverage/availability gate (i.e., have a defined spread) — same denominator
      convention as `scripts/derive_br_comparator_evidence.py`, stated explicitly in the
      output.
11. **The incremental-coverage answer** (replaces relying on `paired_metrics` alone,
    OSR-02 — verified directly against `src/compare.py:paired_metrics`: its intersection
    is centers finite in **all** conditions, which structurally excludes every
    guard-only newly-covered window, since `hr_bpm` is NaN whenever `hr_valid` is False).
    **This partition is defined for, and requires, EXACTLY TWO `--configs`** (OSR-18: the
    round-3 CLI accepts `1+` configs generally, but `both_pass`/`<A>_only`/`<B>_only`/
    `neither` has no single "both" for 3+ configs, and a naive extension would need `2^N`
    buckets). With exactly two configs — which `--isolate-fields` mode already requires,
    per OSR-13 R2 — over the reference-admissible window universe common to the capture
    (independent of config), partition by each config's `hr_valid` into `both_pass`,
    `<config_A>_only`, `<config_B>_only`, `neither`. Report `n` and MAE/RMSE/bias for each
    non-empty bucket — the `<guard_v1>_only` bucket's own MAE/RMSE is the direct answer to
    "are the extra covered windows also correct." Assert `masimo_hr_median`/`admitted` are
    bit-identical across configs for the same `k` (the reference must not depend on which
    config decoded the same window — a consistency check). **With 3+ `--configs` in
    non-isolated general mode, this partition is NOT computed** — only Step 10's
    per-config marginals and the supplementary `paired_metrics`/`coverage_table` report are
    emitted; the script must not silently compute a 2-way partition over an arbitrary first
    pair of a longer config list. `paired_metrics`/`coverage_table` may additionally run
    (for any config count) as a **supplementary** common-finite-only report, captioned
    "common-window comparison only, not the incremental-coverage answer."
12. **ECA-isolation assertion** (OSR-13/OSR-13 R2): if `--isolate-fields` is given: require
    **exactly two** `--configs` (a causal single-factor claim is an A/B statement, not an
    N-way one); diff the loaded config dicts field-by-field (recursively); **raise if the
    diff is empty** at the declared field(s) — two configs that don't actually differ there
    cannot claim isolation (OSR-13 R2: round 1's check only rejected differences *outside*
    the allowlist, so two identical configs vacuously "passed" and could receive causal
    framing while comparing a config to itself); raise unless every differing leaf path is
    inside the declared allowlist. Record the observed before/after value at each declared
    field (e.g. `heart.eca_mode: skip_forbidden_harmonics_v1 -> guard_cardiac_candidate_v1`)
    in both the paired report and provenance. Only when all of the above hold may the
    output use "eca_mode is the only variable" / causal framing (and — per OSR-04 R2 — only
    when `--estimands both` is also in effect, enforced in Step 4/CLI). Without
    `--isolate-fields`, the paired report is produced but captioned as a general,
    non-causal multi-config comparison, with no estimand restriction.
13. `summary.json` provenance (OSR-08/OSR-08 R2): **the exact `sys.argv` the script was
    invoked with, plus a normalized parsed-arguments block** — every `--captures`,
    `--configs` label/path, `--pinned-lock-source` mapping, `--reproduction-baseline-eca-mode`
    mapping, **`--reproduction-baseline-lock` mapping** (OSR-08 R3 — round 5's own
    provenance list named the eca-mode mapping but omitted this newer one, an omission this
    revision fixes so the new safeguard is exactly as auditable as the older one),
    `--estimands` selection, `--isolate-fields` allowlist, `--masimo-csv` override,
    `--session-type` mapping, `--paced-schedule` constant/path,
    `--paced-target-unavailable` mapping/reason (OSR-19), `--allow-dirty` flag, and `--out`
    target (OSR-08 R2:
    round 1/round 2's provenance covered file hashes and config content, but several
    scientific inputs — the 16 bpm metronome command, the estimand selection, the isolation
    allowlist — exist **only** as CLI arguments and were not recorded anywhere, so a clean
    git commit and matching input hashes still could not regenerate the exact run). Also:
    raw `adc_stream.bin` SHA-256, Masimo CSV path +
    SHA-256, **`run_metadata.json`'s own SHA-256** (not merely read for field values),
    any `--paced-schedule` file's own path + SHA-256,
    capture-time `git_commit`/`git_dirty` (from the capture's metadata) recorded
    **separately** from the scorer's **own current** `git_commit`/`git_dirty` (captured at
    scoring time via `git rev-parse HEAD` / `git status --porcelain` — so the artifact can
    always distinguish which code produced the number from which code captured the
    bytes); `run_config_hash(cfg)` plus the config file's own path + SHA-256;
    `lock_provenance` per estimand, including (for a directory pinned-lock-source) the
    matched raw-hash key, `lock_source_config_identity`, and — **whenever
    `--reproduction-baseline-eca-mode`/`--reproduction-baseline-lock` apply** — both the
    declared expected `eca_mode`/lock and the lock source's own verified actual
    `eca_mode`/`locked_bin`, side by side (OSR-08 R3: naming only the matched hash/config
    identity left the specific expected-vs-actual comparison that OSR-03's checks perform
    unrecorded, even though the checks themselves were correctly enforced) (OSR-03/OSR-03 R2);
    `frame0_epoch`
    / `origin_source` / `origin_is_approximate` / `comparator_status` (OSR-01/OSR-01 R2);
    python/numpy/scipy versions;
    an explicit `"deterministic": true, "seed_affects_output": false` note (mirroring
    `derive_br_comparator_evidence.py`'s own determinism statement). **Default: refuse to
    run if the scoring tree is dirty; `--allow-dirty` overrides and stamps
    `"reproducible": false"`** — this reuses, rather than invents, the exact
    `require_clean_tree`/`--allow-dirty` pattern already reviewed and shipped in
    `scripts/diagnose_bin_drift_config.yaml`.

Output root: `results/score_offline/<UTC timestamp>/<capture_label>/<config_label>/<estimand>/`.

### Masimo CSV auto-discovery (OSR-11)

Naive globbing for `*.csv` in a capture directory is unsafe: every one of the 3 target
directories contains **two** CSVs (`live_estimates.csv` — radar output — and
`demo_massimo*.csv`/`demo_sweep.csv` — the actual Masimo export), verified directly by
listing the three target directories. Discovery must instead: for each `*.csv` in the
capture directory, attempt `load_masimo(path)`; a candidate is accepted **iff** it parses
successfully (i.e. passes `load_masimo`'s own required-column check) **and** is not named
`live_estimates.csv` (an explicit exclusion, belt-and-suspenders even if a future radar
output file accidentally had compatible-looking headers). Require **exactly one** match;
raise, listing every `*.csv` found, on zero or multiple matches. `--masimo-csv
<capture_dir>=<path>` (repeatable, keyed by capture directory) overrides for ambiguous
cases. Record the resolved path + SHA-256 in provenance.

### Paced-schedule format and scope (OSR-16 R2)

Round 1's `--paced-schedule` had no schema, hashing, endpoint rule, or transition
disposition — and its own Verification §3 example used a non-executable placeholder.
Fixed as follows:

- **A committed, hashed schedule file** (for a stepped session) is a small table of
  half-open, time-keyed intervals: `[interval_start_s, interval_end_s) -> commanded_rate_bpm`,
  relative to the capture's own `frame0_epoch`/window-grid origin — never a bare "assume
  120 s dwells from t=0" computation inline in the script. Its path + SHA-256 are recorded
  in provenance, same as any other consumed input. A constant-rate capture (`massimo2`)
  needs no file — `--paced-schedule 20260713_182002_live_demo_massimo2=16.0` is sufficient
  and unambiguous, since there is exactly one interval covering the whole session.
- **Endpoint rule:** a window gets a commanded-rate target **only if its full
  `[epoch_start, epoch_end)` span lies entirely within one schedule interval**. A window
  straddling two intervals gets **no** target and is counted explicitly as
  `metronome_unavailable_transition`, never assigned an arbitrary side's rate — this
  mirrors `notes/protocol.md`'s own rule ("windows that straddle a step transition are
  excluded and reported").
- **`session_type` has one explicit source, and only one (OSR-19).** Verified directly
  that none of the 3 real `run_metadata.json` files carry any natural/paced field (only
  generic session IDs like `demo_massimo1`), so `br_session_type` can be neither read from
  existing metadata nor inferred from whether `--paced-schedule` was supplied — sweep is
  paced *with no schedule*, so that inference would misclassify it as natural. Every
  capture's `session_type` is therefore **required** via `--session-type` (Step 1b), and
  every `paced` capture must additionally get **exactly one** of `--paced-schedule` (a real
  target) or `--paced-target-unavailable` (an explicit, reasoned disposition) — never
  neither (which would silently look identical to "forgot to supply the target") and never
  both.
- **Sweep is out of scope for §2.5 in this build** (OSR-16 R2, user decision), via
  `--session-type 20260714_180523_live_demo_sweep=paced --paced-target-unavailable
  20260714_180523_live_demo_sweep=unavailable_missing_transition_timestamps` — an explicit,
  reasoned disposition (Step 1b/Step 7), not merely the absence of a `--paced-schedule`
  entry (OSR-19 corrected this: an implementation could previously have "known" this from
  prose alone, with no CLI-level distinction between "deliberately unavailable" and
  "forgotten"). Verified directly that no file anywhere — `run_metadata.json`,
  `live_estimates.csv`, or any other artifact in
  `results/live_demo/20260714_180523_live_demo_sweep/` — persists the sweep session's
  actual step-transition timestamps; only `notes/protocol.md`'s **nominal** 120 s-per-step
  schedule exists, and the protocol is manually operated (no automatic transition log).
  Rather than fabricate a schedule from nominal timing and risk mistiming
  transition-adjacent windows, the user decided to **refuse** metronome target-concordance
  for sweep entirely.
  **Sweep remains labeled `session_type="paced"` throughout — never "natural" or
  "natural-style"** (OSR-16 R3: an earlier draft of this subsection called sweep's RRp-only
  output "natural-style §2.6 reporting," which is wrong — sweep is a paced stepped session
  whose target-concordance is *unavailable*, not a session that was never paced in the
  first place; conflating the two would erase exactly the natural-vs-paced distinction
  §2.6 requires). Sweep's `br_metronome_status` is
  `"unavailable_missing_transition_timestamps"` for every window (Step 8), sourced from its
  declared `--paced-target-unavailable` reason — it is scored BR RRp-only (§2.1/§2.2's
  ordinary reference/gates, same as any other capture), and its agreement is reported under
  §2.6's *paced* wording with the metronome column explicitly absent — not folded into or
  mislabeled as the natural-session case. `sweep` gets no `--paced-schedule` CLI entry at
  all (Step 7 never looks it up for a capture with a declared `--paced-target-unavailable`),
  so there is no code path where it could be assigned a schedule-derived target. Only
  `massimo2` gets §2.5's aggregate target-concordance report (n, MAE, RMSE, bias against
  the commanded 16 bpm, separate from and never blended with its RRp agreement numbers)
  with `br_metronome_status = "available"`.

## New tests

- `tests/test_comparator.py`: `hr_reference` — the usable-sample definition (finite
  `pr_bpm` ∧ finite `pi` ∧ `pi≥0.5`), the coverage-gate boundary at exactly 24/30, `pi`
  exactly `0.5` (inclusive — passes), the spec's own worked stationarity example (30
  samples: 3×71, 23×72, 4×77 → `linear` gives p90-p10=5.100 → excluded —
  `comparator_prespec.md` §2.2) plus its strict-boundary sibling (spread exactly `5.0` →
  retained, `5.000001` → excluded), `method="linear"` passed explicitly. `br_reference` —
  the availability-gate boundary at 24, the 2.0 bpm **strict** exclusion boundary (exactly
  2.0 retained, `2.000001` excluded — M3R-39), PI reported but never gating.
  `br_metronome_concordance` arithmetic. A test asserting neither function accepts any
  keyword that could move a primary threshold (OSR-10).
- `tests/test_score_offline.py`: the reference/radar/joint cross-tab's bucket membership
  (not only that counts sum to the total) across all 4 joint cells in at least one
  synthetic session; a zero-`final_n` session (MAE/RMSE reported as absent/null, never a
  fabricated `0.0`, with a loud "no scorable windows" status); the decode-geometry
  mismatch guard for a wrong frame rate (19.99, 25 Hz) and a byte-count-compatible-but-
  wrong RX/chirp swap, in addition to `iq_swap` (OSR-05); the `classify_window_outcome`
  all-`-1`/`f_r_hz=None` gate-not-run path end-to-end (OSR-09); confirmation that
  `as_window_estimate` normalization is actually used — a native finite-but-invalid
  `br_bpm` must not leak into the scored CSV column (OSR-12); Masimo auto-discovery
  correctly skipping `live_estimates.csv` in a real two-CSV directory shape (OSR-11);
  `--isolate-fields` rejecting an unexpected second config difference AND rejecting two
  identical configs (empty diff at the declared field — OSR-13/OSR-13 R2), and requiring
  exactly two `--configs`; `--estimands pinned`/`rerun` alone being refused whenever
  `--isolate-fields` is also given (OSR-04 R2); the structural slice-equivalence checks
  that replace the old closeness sanity check (OSR-14, see Verification below); the
  incremental-coverage partition (`both_pass`/`config_only`/`neither`) on a small synthetic
  2-config run (OSR-02); dirty-tree refusal and `--allow-dirty` override (OSR-08); a
  directory `--pinned-lock-source` being rejected when its `replay_file_hashes`/
  `live_raw_mirror_hash` does not match the currently-scored capture's raw SHA-256
  (OSR-03 R2); `validated_fs` actually reaching `build_window_grid` — a test that a bad
  rate is caught even if the Step-2 cross-check is monkeypatched to no-op, proving the
  frozen-grid guard is a real independent check, not a literal (OSR-05 R2); a window
  straddling two `--paced-schedule` intervals producing `metronome_unavailable_transition`,
  never an assigned rate (OSR-16 R2); `evidence.npz` carrying the full `run_window_dsp`
  key set (not the round-1 subset) with one-to-one row coverage (OSR-07 R2); a directory
  `--pinned-lock-source` sharing the scored capture's raw hash but recorded under a
  *different* `heart.eca_mode` being rejected when `--reproduction-baseline-eca-mode` names
  the expected value — using the real massimo1 case (`20260727_182319_replay_unknown`:
  same raw hash as `20260726_173434_replay_unknown`, same numeric lock 27, different
  `eca_mode`) as the fixture (OSR-03 R3); a directory `--pinned-lock-source` matching both
  raw hash and `--reproduction-baseline-eca-mode` but recorded under a *different*
  `locked_bin` than `--reproduction-baseline-lock` names being rejected — using the real
  *original* `20260713_172042_live_demo_massimo1` directory (lock 23, same
  `skip_forbidden_harmonics_v1` eca_mode, trivially self-hash-bound) against an expected
  lock of 27 as the fixture (OSR-03, round 4); `sweep` scoring with `br_session_type="paced"`
  and `br_metronome_status="unavailable_missing_transition_timestamps"` for every window,
  never `"natural"` (OSR-16 R3); `summary.json` round-tripping `sys.argv`/the normalized
  parsed-arguments block for a run using every CLI option at least once, with an explicit
  assertion that the round-trip covers `--reproduction-baseline-lock`'s capture→integer
  mapping specifically, and that a directory-source `lock_provenance` entry carries both
  the declared expected `eca_mode`/lock and the verified actual `eca_mode`/`locked_bin`
  side by side (OSR-08 R2/OSR-08 R3); the
  Step 7 `run_window_dsp` call site using `validated_fs`, not a bare `fs` (OSR-05 R3); a
  non-`strict_v1` `ahet_gate_mode` config producing `outcome_class =
  "unavailable_non_strict_gate_mode"` for every window without raising, while HR/BR
  reference scoring still proceeds normally (OSR-17); a 3-config, non-isolated run
  producing only per-config marginals plus the supplementary `paired_metrics` report, with
  no incremental-coverage partition attempted or silently computed over a first pair
  (OSR-18); the four `--session-type`/`--paced-schedule`/`--paced-target-unavailable`
  combination cases (OSR-19): a `natural` capture (massimo1) scoring with no metronome
  fields at all; a `paced` capture with a schedule (massimo2) scoring normally; a `paced`
  capture with an explicit `--paced-target-unavailable` (sweep) scoring RRp-only with the
  declared reason string, never inferred; and a `paced` capture given **neither**
  `--paced-schedule` nor `--paced-target-unavailable` — the "forgotten target" case —
  raising at Step 1b rather than silently scoring as if natural or as if deliberately
  unavailable. Also: a `natural` capture given `--paced-schedule` or
  `--paced-target-unavailable` raising as contradictory, and a `paced` capture given both
  raising as ambiguous.

## Verification

1. Run unit tests: `conda run -n radar-vitals python -m pytest tests/test_comparator.py
   tests/test_score_offline.py -v`, then full suite to confirm no regression
   (`conda run -n radar-vitals python -m pytest tests/ -q`).
2. **Structural slice-equivalence check** (replaces the old "closeness to
   17.6%/54.9%/23.2%" sanity check — removed, OSR-14: those figures came from
   `scripts/live_demo.py`'s 3 s-hop **sliding** window over 51/51/151 windows, an
   incompatible population against this script's non-overlapping 30 s grid's ~6/6/16
   windows, so closeness was never a meaningful check). On one real capture: assert
   `cube[frame_start:frame_end]` has the exact expected shape; assert
   `np.stack(list(cube[frame_start:frame_end]))` is bit-identical to the slice itself
   (proving `run_window_dsp`'s "deque or already-stacked array, both stack identically"
   docstring claim holds bit-for-bit); assert `run_window_dsp` called with the ndarray
   slice vs. with `collections.deque(...)` of the same frames/config/lock produces
   bit-identical output.
3. Run for real on the 3 Masimo captures. The pinned estimand requires an explicit
   `--pinned-lock-source` per capture; to reproduce the existing guard_v1 coverage-gain
   methodology exactly, point it at the **2026-07-26 replay directories** — verified
   directly against `run_metadata.json` in each: locks **27/26/26**, not the original
   capture directories' own recorded locks **23/20/21**, a different generation (OSR-03):
   ```
   conda run -n radar-vitals python scripts/score_offline.py \
     --captures results/live_demo/20260713_172042_live_demo_massimo1 \
                results/live_demo/20260713_182002_live_demo_massimo2 \
                results/live_demo/20260714_180523_live_demo_sweep \
     --configs production=scripts/live_demo_config.yaml \
               guard_v1=experiments/exp_eca_modes/config_guard_v1.yaml \
     --pinned-lock-source \
        20260713_172042_live_demo_massimo1=results/live_demo/20260726_173434_replay_unknown \
        20260713_182002_live_demo_massimo2=results/live_demo/20260726_173653_replay_unknown \
        20260714_180523_live_demo_sweep=results/live_demo/20260726_173914_replay_unknown \
     --estimands both \
     --isolate-fields heart.eca_mode \
     --reproduction-baseline-eca-mode \
        20260713_172042_live_demo_massimo1=skip_forbidden_harmonics_v1 \
        20260713_182002_live_demo_massimo2=skip_forbidden_harmonics_v1 \
        20260714_180523_live_demo_sweep=skip_forbidden_harmonics_v1 \
     --reproduction-baseline-lock \
        20260713_172042_live_demo_massimo1=27 \
        20260713_182002_live_demo_massimo2=26 \
        20260714_180523_live_demo_sweep=26 \
     --session-type \
        20260713_172042_live_demo_massimo1=natural \
        20260713_182002_live_demo_massimo2=paced \
        20260714_180523_live_demo_sweep=paced \
     --paced-schedule 20260713_182002_live_demo_massimo2=16.0 \
     --paced-target-unavailable \
        20260714_180523_live_demo_sweep=unavailable_missing_transition_timestamps \
     --out results/score_offline
   ```
   (`--session-type` is REQUIRED for every capture (OSR-19) — natural for massimo1, paced
   for massimo2 and sweep. `sweep` gets `--paced-target-unavailable`, not a
   `--paced-schedule` entry — OSR-16 R2/R3, user decision: no persisted transition timing
   exists for it, so it scores BR RRp-only with `br_session_type="paced"`/
   `br_metronome_status="unavailable_missing_transition_timestamps"`, an explicit declared
   disposition, never mislabeled natural and never silently inferred from the mere absence
   of a schedule. `--reproduction-baseline-eca-mode` and `--reproduction-baseline-lock` are
   both required here for every capture because `--isolate-fields` is in effect — OSR-03
   R3/round 4 — and together assert each pinned lock source was actually generated under
   the production config AND actually carries the measured lock (27/26/26), not merely the
   same raw bytes or the same `eca_mode` alone — pointing `--pinned-lock-source` at the
   *original* massimo1/massimo2 directories instead (locks 23/20) would fail the
   `--reproduction-baseline-lock` check, exactly as intended.)
4. Read each capture's `<guard_v1>_only` bucket MAE/RMSE (Step 11) — this is the relevant
   provisional bucket for "is guard_v1's extra coverage also correct" (not the
   `paired_metrics` intersection, which structurally excludes it). Compare the pinned and
   rerun-warmup estimands side by side; a lock shift under rerun-warmup (as previously
   observed for massimo1: bin 25 vs. production 27) is itself a finding to report, not to
   average away.
5. Confirm every artifact — including each `window_scores.csv` row, not only
   `summary.json` (OSR-01 R2) — carries `frame0_epoch`, `origin_source`,
   `origin_is_approximate: true`, `origin_caveat`, and `comparator_status:
   exploratory_non_frozen`, and that no output anywhere uses "actual answer" or otherwise
   final/frozen language for this data (OSR-01/OSR-01 R2) — this is provisional evidence
   toward the guard_v1 call, pending a capture with a persisted true `frame0_epoch`.
6. Confirm a directory `--pinned-lock-source` is rejected when it is not hash-bound to the
   scored capture (OSR-03 R2), separately rejected when it IS hash-bound but recorded
   under a different `eca_mode` than `--reproduction-baseline-eca-mode` names (OSR-03 R3)
   — using massimo1's real `20260727_182319_replay_unknown` (same raw hash and even the
   same numeric lock 27 as the production replay, but recorded under
   `guard_cardiac_candidate_v1`) as the concrete negative-case fixture — and separately
   rejected when it matches both hash and `eca_mode` but not the exact
   `--reproduction-baseline-lock` value (OSR-03, round 4) — using the *original*
   `20260713_172042_live_demo_massimo1` directory itself (locks 23, same
   `skip_forbidden_harmonics_v1` eca_mode, trivially hash-bound to itself, but not lock 27)
   as the concrete negative-case fixture. Confirm
   `validated_fs` (not a literal, and not a bare `fs` at any call site including Step 7) is
   what reaches every downstream call (OSR-05/OSR-05 R2/OSR-05 R3).
7. Confirm `sweep`'s output never uses "natural" anywhere — `br_session_type="paced"`,
   `br_metronome_status="unavailable_missing_transition_timestamps"` throughout (OSR-16 R3).
8. Confirm `summary.json` contains the exact invoking `sys.argv` and a normalized
   parsed-arguments block sufficient to regenerate the run without consulting shell
   history (OSR-08 R2).

## What this deliberately does not touch

- `src/m4/manifest.py`, `src/masimo.py`'s existing `reference_pr`/`reference_br` (mean-
  based, kept for whatever still calls them), `src/compare.py`'s `compare()`/`metrics()`
  (superseded but not deleted) — no cleanup of superseded code in this pass, per the
  user's standing instruction not to silently drop or "clean up" open/parked work.
- Multi-subject weighting, ANOVA LoA, cluster bootstrap CI (M0-freeze stats) — out of
  scope for this narrower single-capture use.
- The 5-bin relock tracker decision (HANDOFF §3.2, deferred) and M4 Stage-1 review
  (HANDOFF §3.3) — untouched, per HANDOFF's explicit deferral.
