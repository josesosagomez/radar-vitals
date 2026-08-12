# M2 acquisition-sidecar scaffold plan (revision 3)

Operator tool that removes file creation, naming, derivation and hashing from the M2 capture
procedure ([notes/m2_capture_runbook.md](../notes/m2_capture_runbook.md) §1-§4), so the operator
supplies only genuine measurements and attestations.

**Revision history.** R1 independently reviewed → **REJECT** (2 blockers, 9 high, 6 medium).
R2 → **APPROVE WITH CHANGES** (blocker 1 verified resolved; blocker 2 partially survived; 8 high,
7 medium). Revision 3 applies every R2 finding plus four owner decisions. Changes from R2 are marked
**[R3]**. Per the second review, items 4-7 of its change list need no third review provided its
blocker-class items H1/H2/H6 land as described; those are §5 D1, §5 D13 and §6.3 below.

## 1. Objective and stop boundary

**Objective.** A script `scripts/m2_scaffold_sidecar.py` that:

1. derives every mechanically-determined acquisition-sidecar field from the committed cohort
   registry and the fixed protocol constants;
2. prompts the operator for exactly the fields that are physical measurements or attestations,
   staged so nothing is attested before it is observable;
3. computes the settle-evidence SHA-256 and stamps the recovery seating epoch, instead of having
   either transcribed;
4. creates and names the per-session working directory and its artifacts;
5. prints the exact `live_demo.py` invocation plus a machine-readable `launch_argv`.

**Stop boundary — this tool must not:**

- modify `scripts/live_demo.py`, any DSP/estimator code, or any capture gate;
- weaken, bypass, or duplicate authority over `validate_acquisition_metadata`
  ([src/m2/acquisition_metadata.py:239](../src/m2/acquisition_metadata.py#L239)) — that validator
  stays the sole authority and every emitted sidecar must pass it unchanged;
- write anywhere except under the operator work root;
- mutate git state (no `restore`, `stash`, `commit`, `clean`) or the registry;
- read, parse, summarize or hash the Masimo session reference CSV;
- decide or authorize a retry, a recapture, or a range-bin/DSP choice;
- prompt for or store any field outside the allowed privacy schema — never names,
  re-identification keys, PAR-Q+ answers, eligibility reasons, diagnoses, symptoms,
  medication/pregnancy data, or consent/PIS paths.

**Amended boundary.** Two authorized exceptions to "do not modify
`src/m2/acquisition_metadata.py`", each in its own commit with its own independent code review:

1. Milestone A's **no-behavior-change constants extraction** (§4) — named constants only, no
   validation outcome changed.
2. **[R4] D-OWN-7's settle-floor change** (§2) — `MIN_SETTLE_S` 60.0 → 120.0. This one **does**
   change behaviour: it tightens admission. Authorized by the owner directly, not by this plan, and
   recorded as D-OWN-7 because a behaviour change to the validator is otherwise outside the stop
   boundary. An earlier draft made this edit without amending the boundary or recording the
   decision, and the independent review rejected it on exactly that ground.

**[R3] Explicit non-goal.** This tool cannot scaffold the engineering/synthetic dry-run path.
`synthetic_fixture: true` requires a `T*`/`SYN*` subject
([acquisition_metadata.py:251-255](../src/m2/acquisition_metadata.py#L251-L255)) and coupled
registry agreement ([capture_artifacts.py:137-152](../src/m2/capture_artifacts.py#L137-L152)); this
tool scaffolds prospective P001-P015 sessions only. `scripts/m2_preflight.py --dry-run` remains the
synthetic path.

This is operator tooling. It produces no scientific result and changes no metric.

## 2. Owner decisions

- **D-OWN-1** (2026-08-12). The natural/paced launch countdown is **30 s** (§12).
- **D-OWN-2** (2026-08-12). The cohort-registry deletion at `a5edecc` was a **mistake**; restored (§3).
- **D-OWN-3** (2026-08-12). The work root **stays inside the repository** at `m2_capture_work/`; the
  repository is private, so the exfiltration residual is accepted (§5 D9, §11).
- **[R3] D-OWN-4** (2026-08-12). Recovery `distance_m` is measured **at seating, to the actual
  chest** — so it belongs to the seated stage, not the main stage. Accepted cost: tape-measuring while
  PR decays consumes recovery ramp, against a `>= 20.0` bpm range requirement that cannot be
  recaptured ([analysis_prespec.md:314](../notes/analysis_prespec.md#L314),
  [:321](../notes/analysis_prespec.md#L321)).
- **[R3] D-OWN-5** (2026-08-12). The tool **stamps the recovery seating epoch** itself (§5 D13).
  `notes/m2_capture_runbook.md` §4 must be reworded accordingly (§11).
- **[R3] D-OWN-6** (2026-08-12). `sit_to_record_delay_s` gets **no bound yet** — observe real values
  first. The tool therefore records the delay and its own seated-stage duration every session (§5 D13).
- **[R5] D-OWN-8** (2026-08-12). **M2 and M4 share a protocol-constants module**, `src/protocol.py`.
  This does not cut across the deliberate M2/M4 separation: `src/m2/__init__.py` scopes that split to
  *schema semantics* (v2 historical vs v3 prospective), not to physical protocol facts. Scope rules,
  so the boundary is not arbitrary:
  - **Shared:** facts stated in `notes/protocol.md` that more than one module gates on — the distance
    range, the settle spread/drift limbs, the clock-offset limit, the paced-rate rotation, and the two
    settle-timing values M4 documents in prose.
  - **Aliased, not moved.** Both modules keep their existing public names, assigned from
    `src/protocol.py`. Every existing import keeps working and no value changes; the point is one
    *editable value* per fact, not one name. Unifying the near-transposed names
    (`SETTLE_MAX_PR_SPREAD_BPM` vs `SETTLE_SPREAD_MAX_BPM`) is a separate rename.
  - **Deliberately NOT shared:** `FRAME_RATE_HZ`, duplicated in `src/m2/acquisition_metadata.py`,
    `src/m2/time_sensitivity.py` and `src/m4/window_grid.py`. M2's is a capture parameter the operator
    attests to; `window_grid.py:30-33` marks its copy FROZEN by `notes/analysis_prespec.md` §7, where a
    change is a frozen-analysis amendment. Merging would couple an acquisition attestation to a frozen
    analysis decision. But a divergence would be a *silent* scientific error — windows of 20 s of data
    labelled 30 s — so an agreement test covers it instead.
  - **Never merge coincidentally equal values.** `src/comparator.py:30`
    `_HR_STATIONARITY_MAX_BPM = 5.0` equals the settle spread limit numerically and is an unrelated
    within-window quantity.
- **[R4] D-OWN-7** (2026-08-12). **Natural and paced settle is always at least 120 s**; the
  participant needs that long to stabilize. `MIN_SETTLE_S` raised 60.0 → 120.0, added to
  `notes/protocol.md` as SETTLE CRITERION limb 3, and this amends the §1 stop boundary. Three
  sub-decisions: the 120 s figure is **operator judgement, not measurement-derived**, and the protocol
  says so explicitly so the paper cannot inherit it as an empirical settling time; the floor binds
  **natural and paced only**, not diagnostic captures; and for paced the 120 s of metronome pacing
  **counts toward** the settle, so pre-record time is ~120 s, not 240 s. Recovery is unaffected — it
  has no settle time at all, since recording starts as the subject sits, and the validator already
  rejects every settle field for that arm.

## 3. Milestone 0 — restore the registry and reach a clean, baselined tree

Blocker-1 of the first review: HEAD `a5edecc` deleted `cohort_registry/registry_v001.json` and its
`.sha256`, breaking 37 of 53 tests in `tests/test_m2_cohort_registry.py`, 31 in
`tests/test_m2_capture_artifacts.py`, and every command in this plan. No committed generator for a
real registry exists — only the `tests/fixtures/m2/builders.py` synthetic fixture — so the bytes came
from the object store.

**Done 2026-08-12:** `git checkout 92d93ab -- cohort_registry/`. Computed SHA-256, the `.sha256`
sidecar and `HANDOFF.md` §2 all agree at
`e1bf942ff8bfbdd2b9f4b92e448099142058805bc8d0213576db12d8a3689a7c`. Second review independently
confirmed `validate_registry_history` returns a 1-element history at `revision: 1` with all 45
sessions `state: planned` and all subjects `label_state: sealed`. `tests/test_m2_cohort_registry.py`
+ `tests/test_m2_capture_artifacts.py`: **131 passed**.

**[R3] Remaining — the tree is dirty right now, so §6.1 item 1 and acceptance criterion 5 both fail
until this is done:**

1. Commit **all** current M2 working-tree changes, not just the registry: staged
   `cohort_registry/registry_v001.json` + `.sha256`; modified `HANDOFF.md`, `HISTORY.md`,
   `notes/m2_capture_runbook.md`, `scripts/live_demo.py`, `tests/test_m2_capture_artifacts.py`;
   untracked `plans/m2_sidecar_scaffold.md`. Push — `origin/vital_signs_own_v13` still carries the
   registry deletion. Then verify `git status --porcelain` is byte-empty.
2. **Record a verified full-suite baseline.** The "3139 passed" figure was measured at `da3287d`, and
   three countdown tests were red on its successors, so acceptance criterion 7 currently has no
   starting point. Run and record the full suite before implementation begins.
3. Add a guard test asserting `DEFAULT_REGISTRY_PATH.is_file()` and that its digest sidecar matches,
   so an accidental deletion fails one obvious test instead of 68 obscure ones.
4. `HANDOFF.md` §1/§4 rewrite (§11).

## 4. Milestone A — constants extraction (separate reviewed commit)

The admission thresholds exist only as literals inside `validate_acquisition_metadata`: 0.8/1.4
(line 295), 5.0/3.0 (line 336), 60.0 window (line 332), `>=60.0` settle (line 330), `>=120.0`
(line 361), `>=60.0` BR stability (line 362), 100.0/120.0 (line 392). `MAX_CLOCK_OFFSET_S` (line 46)
is the only named one. Any range text this tool prints would be a second copy.

**[R4] DONE 2026-08-12.** Extracted with scalar min/max names rather than tuples, matching the
existing precedent at `src/m4/manifest.py:142-143` and making a silent transposition impossible to
write: `DISTANCE_MIN_M`/`DISTANCE_MAX_M`, `SETTLE_EVIDENCE_WINDOW_S`, `MIN_SETTLE_S`,
`SETTLE_SPREAD_MAX_BPM`, `SETTLE_DRIFT_MAX_BPM`, `MIN_PACED_SETTLE_S`, `MIN_BR_STABILITY_S`,
`EXERTION_STOP_PR_MIN_BPM`/`EXERTION_STOP_PR_MAX_BPM`, `MAX_SCENE_NOTES_CHARS`, and the three
controlled vocabularies (as tuples — set iteration order over `str` is `PYTHONHASHSEED`-dependent and
prompt order must be reproducible). Error messages are now built from the constants too, so changing
a threshold cannot leave operator-facing text lying; every rendered message is pinned by test.

**[R4] The derived formulas were extracted as well** — R3 missed them. §7 has the tool derive
`metronome_rate_bpm` and compute `harmonic_collision_margin_bpm`, so leaving `2`, `4.0` and `1e-12`
as bare literals would have left the tool re-typing a *formula*, which fails later and less obviously
than a re-typed threshold: it aborts at the D3 round trip after the operator has already answered the
paced prompts. Now `METRONOME_BEATS_PER_BREATH`, `RESPIRATION_HARMONIC_ORDER`,
`HARMONIC_MARGIN_TOLERANCE_BPM`.

**[R4] These constants are NOT a project-wide single source of truth**, and the module comment now
says so explicitly. `src/m4/manifest.py:142-143` and `:174-175` independently define the same
distance range and 5/3 bpm settle limbs for the offline scoring admissibility gate, and
`MAX_CLOCK_OFFSET_S` and `PACED_RATES_BPM` are duplicated there too. A protocol change must land in
both modules or acquisition and scoring disagree silently — and M4 is the path feeding the paper's
agreement metrics. `tests/test_m2_acquisition_metadata.py` pins them equal so a one-sided edit fails.
Beware the near-transposed spellings: M4's `SETTLE_MAX_PR_SPREAD_BPM` is this module's
`SETTLE_SPREAD_MAX_BPM`.

**[R4, superseded by D-OWN-7] `MIN_SETTLE_S` is now 120.0 and protocol-sourced.** At the time of the
extraction it was 60.0 and *derived*: `notes/protocol.md` fixed only the 60 s evidence window and
required settle duration merely to be *recorded*, so the floor equalled the window and a capture whose
entire settle **was** the evidence window was admissible. D-OWN-7 replaced that with an explicit
protocol limb 3 at 120 s. Consequences recorded here so the two are not confused later:

- The floor is now stated in `notes/protocol.md` as limb 3, attributed to operator judgement rather
  than measurement.
- The `settle_duration_s >= settle_evidence_window_s` cross-check that §11 previously listed as
  pending is now **unreachable**, since 120.0 > 60.0 and the window is pinned by equality. It is not
  outstanding work; implementing it would be dead code.
- A **new** gap takes its place: there is no `MAX_SETTLE_S`, while the protocol aborts a settle that
  exceeds 5 minutes. Only the lower bound is enforced, so `settle_duration_s: 3600.0` is admitted.
  Named as a follow-up in §11.

**Validation, met.** M2 subset 333 passed before and after; full suite 3140 passed before and after
the extraction itself; no comparison operator or literal value changed; every rendered message
byte-identical. The independent code review additionally verified behavioural identity across 2912
probed inputs, and `tests/test_m2_acquisition_metadata.py` (32 tests) now pins the boundaries,
messages, formulas and M2/M4 agreement that previously had no coverage at validator level.

## 5. Frozen design decisions

### [R3] D1 — Stages, redrawn

R2's three-stage split had the right mechanism but the wrong boundary: it left four seated-posture
booleans in the recovery main stage, where they would be attested at exertion stop, before the
participant has adopted the posture they describe — the same fabrication D1 exists to prevent
(CLAUDE.md §4), landing in a promoted artifact
([capture_artifacts.py:240](../src/m2/capture_artifacts.py#L240)).

| Stage | When | Arms |
|---|---|---|
| `init` | before the visit | all |
| `measure --stage main` | after settle (natural/paced) or after exertion stop (recovery) | all |
| `measure --stage seated` | at the seating instant | recovery only |

**Recovery seated stage — exactly 9 fields, collected as 4 inputs:**

| Input | Fields covered |
|---|---|
| `distance_m` (number) | `distance_m` — D-OWN-4 |
| `seated_pr_t0_bpm` (number) | `seated_pr_t0_bpm` |
| one posture confirmation (y/n) | `back_straight`, `both_hands_on_thighs`, `facing_radar`, `sensored_hand_still` |
| one at-record-start confirmation (y/n) | `seated_at_record_start`, `still_at_record_start`, `hands_resting_at_record_start` |

Grouping is legitimate here and only here: within each group every field must be true to proceed
([`_TRUE_AT_CAPTURE_FIELDS`](../src/m2/acquisition_metadata.py#L73),
[:299-300](../src/m2/acquisition_metadata.py#L299-L300)), and each group is a single observation of the
seated participant. A "no" aborts, naming the group.

**[R3] Recovery `scene_changed` and `disturbances_category`** stay in the main stage, but with
arm-specific wording that does not claim knowledge of the future: "observed up to now; if anything
occurs between now and record start, abort and use the protocol-abort record"
([src/m2/retry.py](../src/m2/retry.py)). R2's generic "observed up to record start" wording was
self-contradictory for recovery, where the main stage runs minutes before record start.

### [R3] D13 — The tool stamps the recovery seating epoch, first

Per D-OWN-5, `measure --stage seated` calls `time.time()` as its **very first action**, before the
distance measurement and before any prompt, and emits the literal epoch inside `launch_argv` as
`--recovery-seated-start-utc <value>`. This:

- makes the stamp machine-generated rather than operator-transcribed, for an event whose declared
  source is `operator_observed_seated_start_synchronized_pc_utc`
  ([acquisition_metadata.py:47](../src/m2/acquisition_metadata.py#L47));
- deletes the PowerShell `[cultureinfo]::InvariantCulture` workaround entirely — a decimal comma can
  no longer reach `argparse type=float`;
- is the only way the launch test can exist for recovery (§8 test 14);
- still satisfies the non-future check at
  [live_demo.py:830-836](../scripts/live_demo.py#L830-L836).

Stamping *before* the distance measurement means the tape-measure time lands inside
`sit_to_record_delay_s` rather than being hidden — deliberate, given D-OWN-6 observes real values.
The seated stage records its own wall-clock duration in its provenance file.

### Other decisions

**D2 — `visit_utc` stamped at `measure --stage main`**, not `init` (which may run days early), and
formatted to end in `Z`: `datetime.now(timezone.utc).isoformat()` yields `+00:00`, which
`_validate_utc` rejects ([:201-210](../src/m2/acquisition_metadata.py#L201-L210)).

**[R3] D3 — Build, validate, serialize, re-parse, re-validate, JSON-round-trip, then write.**
Build → `validate_acquisition_metadata` → `yaml.safe_dump` → `yaml.safe_load` the bytes → validate
again → assert per-key equality **including `type()`** → **also assert the mapping survives
`json.loads(json.dumps(...))` with type equality**, because
[capture_artifacts.py:175](../src/m2/capture_artifacts.py#L175) compares after a *JSON* round trip
while the rest of D3 only proves the YAML → `write_new_bytes`. The second review verified empirically
that `safe_dump` quotes `visit_utc` and that float `repr` round-trips exactly, which is what makes
§8 test 8's exact-equality claim hold against the `1e-12` check. Two hazards to code against: never
hand `safe_dump` a numpy scalar or `Decimal` (`RepresenterError`), and reject YAML `.nan`/`.inf`/
`Infinity` tokens from `--answers`, not just typed input.

**D4 — Missing measurements are absent keys, never placeholder values.** The templates ship
`distance_m: 1.0` and `clock_offset_start_s: 0.0`, which are *schema-valid*; forgetting to replace
them starts a capture with invented metadata. Absence raises — verified exhaustively by the first
review for every required field in every arm.

**D5 — No defaults for a measurement or attestation.** An out-of-range answer **aborts with the
protocol remedy** — never a re-prompt loop, which invites typing 1.40 after measuring 1.45.

**D6 — The paced rate comes only from the registry.** `commanded_rate_bpm` from `paced_rate_bpm`,
`metronome_rate_bpm` = 2x. An operator-supplied rate is rejected, not merged. Independently enforced
at [live_demo.py:872-880](../scripts/live_demo.py#L872-L880) and
[capture_artifacts.py:131-134](../src/m2/capture_artifacts.py#L131-L134).

**[R3] D7 — `--answers` is a strict whitelist, gated by a discriminating marker.** The mapping may
contain *only* that arm-and-stage's operator-input keys; any other key — a derived field, a settle key
on recovery, `commanded_rate_bpm` — is rejected, not merged. R2 gated it by `data_role`, which was
vacuous: every registry subject is `representation_validation` or `final_evaluation`
([cohort_registry.py:42-43](../src/m2/cohort_registry.py#L42-L43)), so the gate fired always,
including in every test. Replaced with: `--answers` requires stdin to be a non-TTY **and** an explicit
`--non-interactive-test` marker. §8's preamble states the exact flag.

**[R3] D8 — No-overwrite creation** via `write_new_bytes`
([common.py:108](../src/m2/common.py#L108)). **`--attempt N` is dropped from this milestone.** No
retry-record schema, file or loader exists anywhere; `retry.py` exposes only
`validate_retry_pair` over two registry attempt mappings requiring `label_state == "sealed"` on both
([retry.py:37-38](../src/m2/retry.py#L37-L38)), registry session ids are immutable
`<subject>_<arm>` ([cohort_registry.py:170-172](../src/m2/cohort_registry.py#L170-L172)), and no CLI
appends an attempt. Inventing that contract would drift into "authorize a retry", which §1 forbids.
Directory naming is therefore unconditional: `<work-root>/<session_id>/`. Recorded in §11 gated on a
retry-record contract.

**[R3] D9 — Work root in-repo by default, with a deny list.** Default `m2_capture_work/`, ignored by
`.gitignore:14`. Because an untracked in-repo file makes `git status --porcelain` non-empty and kills
the launch at [live_demo.py:881-882](../scripts/live_demo.py#L881-L882), `init` **and** `measure` —
not just `check` — verify every path they write is ignored-or-outside and refuse otherwise.
"Ignored" alone is insufficient: `.gitignore:13-18` also ignores `data*/`, `results*/`, `literature*/`,
so `--work-root data/raw/prospective` would pass while violating CLAUDE.md §9 and colliding with the
promotion destination ([capture_artifacts.py:514-518](../src/m2/capture_artifacts.py#L514-L518)). The
work root must be `m2_capture_work/` or an absolute path outside the repo, and never under `data/`,
`results/`, `cohort_registry/`, `figures/`, or `paper/`.

**[R3] D10 — Provenance in per-stage sidecar files.** `_reject_unknown_fields`
([:177-185](../src/m2/acquisition_metadata.py#L177-L185)) closes the schema, so the acquisition YAML
cannot record how its values were obtained. R2's single `scaffold_provenance.json` could not be
written twice under no-overwrite — the failure would fire at the seating instant with the ramp
decaying. So: `scaffold_provenance.main.json` and, for recovery, `scaffold_provenance.seated.json`,
each written once. Contents: tool version, git commit, stage UTC stamps, seated-stage duration,
interactive vs `--answers`, answers-file SHA-256 when used, and the hardware-confirmation answer
(D-M5 below).

**D11 — `synthetic_fixture` is never emitted.** It cannot be truthfully derived nor sensibly
prompted; omitted entirely, defaulting to `False` in the validator.

**D12 — The countdown is never re-typed.** The launch text obtains it by calling
`_prospective_start_delay_s` (§12).

**[R3] D14 — `settle_evidence_path` is derived, not prompted, and constrained.** Nothing otherwise
stops it pointing at the Masimo session CSV, which `prepare_capture_inputs` would copy into the run
directory ([capture_artifacts.py:84-94](../src/m2/capture_artifacts.py#L84-L94)) and `finalize_capture`
would promote ([:533-535](../src/m2/capture_artifacts.py#L533-L535)) — putting reference bytes in a
promoted directory before the label firewall authorizes any reference access. So the tool derives
`evidence/<session_id>_settle.json` (matching
[templates/m2_acquisition_paced.yaml:42](../templates/m2_acquisition_paced.yaml#L42)), resolves it
exactly as [capture_artifacts.py:84-86](../src/m2/capture_artifacts.py#L84-L86) does (relative to the
sidecar's parent), and requires `is_file()`, non-zero size, a **non-empty suffix**, a name not equal
to `expected_reference_basename`, and a suffix that is not `.csv`. The extension check matters
independently: an extensionless file snapshots as `settle_evidence`, the later `settle_evidence.*`
glob matches nothing ([:282-284](../src/m2/capture_artifacts.py#L282-L284)), and the run fails only
*after* sealing 600 s, with no permitted retry.

**[R3] D15 — Repo-root cwd, repo-relative emitted paths.** R2's "emit absolute paths" was wrong twice
over. The launch is not cwd-independent regardless: `--config` defaults to the relative
`scripts/live_demo_config.yaml` ([live_demo.py:727](../scripts/live_demo.py#L727)) and is compared
against a `_ROOT`-resolved path ([:818](../scripts/live_demo.py#L818),
[:851](../scripts/live_demo.py#L851)); `_git_info()` shells git with **no `-C`**
([:500-505](../scripts/live_demo.py#L500-L505)), so launching from a *different clean* git repo would
bind a foreign commit into the sealed receipt; and a relative `--cohort-registry` resolves against cwd
inside the sibling glob at [cohort_registry.py:307](../src/m2/cohort_registry.py#L307). Absolute paths
additionally write `C:\Users\<name>\...` into `run_metadata["exact_cli_invocation"]`, the receipt
([capture_artifacts.py:253](../src/m2/capture_artifacts.py#L253)) and the promoted manifest
([:595](../src/m2/capture_artifacts.py#L595)) — machine-specific per CLAUDE.md §9 and an avoidable
operator-identity leak. So: `check` asserts its own cwd is the repo root, the printed launch text
begins with an explicit `Set-Location <repo_root>`, and emitted paths are repo-relative (which the
in-repo work root makes natural).

**[R3] D-M5 — The hardware confirmation stays a prompt.** Checking the committed config against
`CHIRPS_PER_FRAME`/`FRAME_RATE_HZ` compares two in-repo constants and says nothing about the sensor,
so it cannot restore falsifiability to `configured_chirps_per_frame`/`configured_frame_rate_hz`
(runbook §2.4 makes them operator confirmations, and `_validate_prospective_cli` never cross-checks
them). The tool asks explicitly — "does the radar/config readout show 32 chirps/frame and 20 Hz?" —
aborts on no, records the answer and stamp in the provenance file, and only then emits the derived
literals.

**[R3] D-M6 — Tool-side plausibility bounds.** `start_pr_bpm`, `resting_pr_bpm`,
`pre_exertion_resting_pr_bpm` and `seated_pr_t0_bpm` require only `>= 0`
([:331](../src/m2/acquisition_metadata.py#L331), [:364](../src/m2/acquisition_metadata.py#L364),
[:390](../src/m2/acquisition_metadata.py#L390), [:395](../src/m2/acquisition_metadata.py#L395)) and
durations have no upper bound, so a transposition (105 → 1050) or a decimal-comma distance passes
silently — and `resting_pr_bpm` propagates into the computed `harmonic_collision_margin_bpm`. The tool
aborts with the protocol remedy outside named plausibility bounds. These are **tool-side ergonomics
that change no validator outcome**, and are stated as such.

## 6. CLI contract

Conventions follow [scripts/m2_register_capture.py](../scripts/m2_register_capture.py):
`ContractError` to stderr as `ERROR: ...` with exit 2, machine-readable JSON on stdout, exit 0 on
success.

### 6.1 [R3] `check` — read-only capture-readiness report

Takes the **same `--subject`/`--arm`/`--registry` arguments as `init`**, since several gates are
session-specific (R2's `check` could not know which session to inspect, contradicting its own test 13).
Exits non-zero unless every gate holds, each cross-referenced to what enforces it:

| Gate | Enforced at |
|---|---|
| `git -C <repo_root> status --porcelain` empty — the same command the launch uses, not `status --short` | [live_demo.py:500-505](../scripts/live_demo.py#L500-L505), [:881-882](../scripts/live_demo.py#L881-L882) |
| HEAD resolves and is not `unknown` | [:881](../scripts/live_demo.py#L881) |
| cwd is the repo root (D15) | [:818](../scripts/live_demo.py#L818), [:500-505](../scripts/live_demo.py#L500-L505) |
| `validate_registry_history` passes on the exact `--registry` path — **not** `load_registry` | [cohort_registry.py:423](../src/m2/cohort_registry.py#L423), [capture_artifacts.py:110](../src/m2/capture_artifacts.py#L110) |
| target session `state` is exactly `planned` | [cohort_registry.py:517-520](../src/m2/cohort_registry.py#L517-L520) |
| `bin_selection.enabled` true | [live_demo.py:855](../scripts/live_demo.py#L855) |
| `session.locked_bin` null | [:857](../scripts/live_demo.py#L857) |
| config path resolves exactly to `scripts/live_demo_config.yaml` | [:818](../scripts/live_demo.py#L818), [:851](../scripts/live_demo.py#L851) |
| committed default config agrees on 32 chirps / 20 Hz (`profile.num_chirps_per_frame`, `hw_frame.num_loops`, `session.frame_rate_hz`, `hw_frame.period_ms == 50.0`) | D-M5 |
| work root is ignored-or-outside and not on the deny list | D9 |

Why `validate_registry_history` and not `load_registry`: the launch path validates the whole hash-linked
chain, so a latest revision with a missing or mutated predecessor passes `load_registry` and dies at
launch — not hypothetical in a repo that just lost files from that directory. Echo `revision` and the
registry SHA-256.

It **reports**; it never fixes. The clean-checkout gate exists so the sealed receipt binds a commit
whose code actually ran — re-checked at
[capture_artifacts.py:177](../src/m2/capture_artifacts.py#L177) and
[label_firewall.py:280](../src/m2/label_firewall.py#L280) — so auto-cleaning would bind a receipt to a
commit that does not contain the code that ran.

`check` does **not** require `data/manifest.local.csv`: prospective mode ignores it
([live_demo.py:929](../scripts/live_demo.py#L929)).

### 6.2 `init` — derive everything mechanical

```
python scripts/m2_scaffold_sidecar.py init --subject P001 --arm natural \
  --registry cohort_registry/registry_v001.json [--work-root m2_capture_work]
```

Refuses unless the target session's registry `state` is exactly `planned`; without this a duplicate
capture is only refused at registration, after 600 s of participant time, with no retry reason
covering it ([retry.py:11](../src/m2/retry.py#L11)).

Derives `subject_id`, `cohort_slot`, `data_role`, `session_id`, `visit_number`, `session_order`,
`arm`, `posture: seated`, `expected_reference_basename`, `configured_chirps_per_frame`,
`configured_frame_rate_hz`, `intended_duration_s`; paced also `commanded_rate_bpm`/
`metronome_rate_bpm`; natural/paced `settle_evidence_window_s` and `settle_evidence_path` (D14);
recovery `exertion_modality` and **no** settle keys
([:340-353](../src/m2/acquisition_metadata.py#L340-L353)).

Constants come from `src.m2.acquisition_metadata`, never re-typed.

Creates `<work-root>/<session_id>/` with `acquisition.draft.yaml` (derived keys only) and
`checklist.md`. `checklist.md` contains **no example values** (D5) and carries an explicit "no names,
no health narrative" instruction — §11 records why that risk is not automatable.

The directory is named exactly `<session_id>` deliberately:
`run_metadata["exact_cli_invocation"]` records the sidecar path verbatim
([live_demo.py:1017](../scripts/live_demo.py#L1017)), so an operator-named folder could leak identity
into the sealed receipt.

### 6.3 [R3] `measure` — collect measurements, emit the sidecar

```
python scripts/m2_scaffold_sidecar.py measure --session-dir m2_capture_work/P001_natural \
  --stage main|seated [--answers a.yaml --non-interactive-test]
```

**Artifacts, all no-overwrite (this is R2's H6 fix).** Recovery's main stage **cannot** pass
`validate_acquisition_metadata` — the four seated fields are required true at
[:381-387](../src/m2/acquisition_metadata.py#L381-L387) — so it does not attempt to, and R2's claim
that every `measure` writes `acquisition.yaml` was impossible:

| Arm | `--stage main` writes | `--stage seated` writes |
|---|---|---|
| natural, paced | `acquisition.yaml` (full D3 round trip), `scaffold_provenance.main.json` | n/a |
| recovery | `acquisition.stage_main.yaml` (partial mapping, schema-checked per-field but not whole-document), `scaffold_provenance.main.json` | `acquisition.yaml` (merge + full D3 round trip), `scaffold_provenance.seated.json` |

The seated stage re-reads `acquisition.stage_main.yaml`, merges its own 9 fields, and only then runs
the full D3 round trip. The merge is exact: a key present in both files with different values is an
error, not an overwrite.

**Launch output.** Two separate fields, because `_parse_args()` parses `sys.argv[1:]` and would die on
a `conda run -n ...` prefix:

- `launch_argv` — a JSON array whose element 0 is the **script path exactly as typed**
  (`scripts/live_demo.py`), matching what `list(sys.argv)` records at
  [live_demo.py:1017](../scripts/live_demo.py#L1017) and what
  [capture_artifacts.py:173](../src/m2/capture_artifacts.py#L173) binds. Recovery's
  `--recovery-seated-start-utc` carries a **literal** epoch (D13), never a placeholder.
- `launch_command` — the human-readable PowerShell string, including the `conda run` prefix and the
  `Set-Location <repo_root>` from D15.

**Recovery non-acquisition branch.** `participant_stop` / `researcher_safety_stop` writes no sidecar
and prints no launch command ([live_demo.py:822-826](../scripts/live_demo.py#L822-L826) admits only
`target_reached`). It writes `disposition.json` in the session dir, because runbook §3 requires the
disposition be reported. **[R3]** Its allowed keys are enumerated in the implementation and are
controlled-vocabulary only — category, UTC, subject id, session id — with **no free-text field**, so it
cannot become a health narrative. Setting the registry `withdrawn`/`recovery_not_cleared` state is out
of scope: no CLI exists for it, and that gap is recorded in §11.

**Attestation handling.** Must-be-true fields are presented as explicit checklists requiring
affirmative answers; a negative answer aborts, naming the field or group, and writes nothing.
`scene_changed` is prompted separately — the one common boolean that may be false.

## 7. [R3] Operator inputs per arm

Totals against the real frozensets, confirmed by the second review: `_COMMON_ACQUISITION_FIELDS` = 19
named + 17 booleans = 36; `_SETTLE_FIELDS` 7; `_PACED_FIELDS` 7; `_RECOVERY_FIELDS` 16. Allowed keys
total 43 natural / 50 paced / 52 recovery; `synthetic_fixture` is the only optional one and is never
emitted.

**Common 36** = 13 derived (`subject_id`, `cohort_slot`, `data_role`, `session_id`, `arm`,
`visit_number`, `session_order`, `expected_reference_basename`, `posture`,
`configured_chirps_per_frame`, `configured_frame_rate_hz`, `intended_duration_s`, `visit_utc`)
+ 22 prompted + `synthetic_fixture` omitted. The 22 = `distance_m`, `clock_offset_start_s`,
`scene_description_category`, `scene_non_health_notes`, `disturbances_category`, `scene_changed`, and
the 16 must-be-true attestations. Plus one non-field prompt: the D-M5 hardware confirmation.

**Natural, 7 settle keys** = 2 derived (`settle_evidence_window_s`, `settle_evidence_path` — D14)
+ 1 computed (`settle_evidence_sha256`) + 4 prompted (`settle_duration_s`, `start_pr_bpm`,
`settle_pr_spread_bpm`, `settle_pr_drift_bpm`).

**Paced, +7** = 2 derived (`commanded_rate_bpm`, `metronome_rate_bpm`) + 1 computed
(`harmonic_collision_margin_bpm`) + 4 prompted (`paced_settle_duration_s`,
`br_stability_duration_s`, `br_stability_confirmed`, `resting_pr_bpm`).

**Recovery, 16** = 1 derived (`exertion_modality`) + 11 prompted at `main`
(`pre_exertion_resting_pr_bpm`, `exertion_stop_pr_bpm`, `exertion_duration_s`,
`stopping_event_category`, `screening_completed`, `recovery_clearance_attested`,
`amended_materials_current_consent_confirmed`, `researcher_present`,
`suitable_footwear_confirmed`, `dry_unobstructed_area_confirmed`, `no_pacing_confirmed`)
+ 4 prompted at `seated`.

**Recovery's split of the common 22** is 17 at `main` (`clock_offset_start_s`,
`scene_description_category`, `scene_non_health_notes`, `disturbances_category`, `scene_changed`, and
12 attestations) + 5 at `seated` (`distance_m` per D-OWN-4, and the four posture booleans per D1).
Arithmetic: 13 + 1 derived, 17 + 11 main, 5 + 4 seated, 1 omitted = 52. ✓

Range text and controlled vocabularies come from milestone A (§4), never re-typed.

## 8. [R3] Tests

Deterministic, no hardware, no TTY. All operator input via `--answers` with
`--non-interactive-test` (D7). **Registry fixtures use `_copy_committed_registry`
([tests/test_m2_capture_artifacts.py:206-213](../tests/test_m2_capture_artifacts.py#L206-L213)) with
real P001-P015 subjects** — not `synthetic_registry()`, whose `T001`/`registry_kind:
synthetic_fixture` pairing is incompatible with never emitting `synthetic_fixture` (D11) and would be
rejected at [:257-259](../src/m2/acquisition_metadata.py#L257-L259).

1. `init` per arm emits exactly the expected derived key set and **no** measurement keys (D4).
2. `init` natural/paced emits no recovery keys; recovery emits no settle keys.
3. `load_sidecar(path) == validated_mapping` with per-key `type()` equality — not merely "accepted",
   which would pass vacuously.
4. Boundary rejection, one case each: `distance_m` 0.79/1.41; `clock_offset_start_s` ±1.01;
   `settle_pr_spread_bpm` 5.1; `settle_pr_drift_bpm` 3.1; `exertion_stop_pr_bpm` 99.9/120.1;
   `settle_duration_s` 119.9 (**[R4]** the floor is 120 s per D-OWN-7, not 60);
   `paced_settle_duration_s` 119.9; `br_stability_duration_s` 59.9. Plus the **accepted inclusive**
   values explicitly: 0.80/1.40, ±1.00, 5.0, 3.0, 60.0 window, 120.0 settle, 120.0 paced settle,
   60.0 BR stability, 100.0/120.0. Plus a negative-PR case.
5. A rejected `measure` leaves **no** `acquisition.yaml` on disk (D3).
6. `settle_evidence_sha256` equals `sha256_file`; a changed byte changes it. Missing, unreadable,
   zero-byte, **extensionless**, `.csv`-suffixed, and `expected_reference_basename`-named evidence each
   raise cleanly and write nothing (D14).
7. Paced `commanded_rate_bpm` always equals registry `paced_rate_bpm`; a conflicting answers value is
   rejected; `metronome_rate_bpm == 2 *` commanded.
8. `harmonic_collision_margin_bpm == abs(resting_pr_bpm - 4 * commanded_rate_bpm)` exactly, against
   the validator's 1e-12 check.
9. A negative answer to any must-be-true field or group aborts and writes nothing.
10. Recovery `participant_stop`/`researcher_safety_stop` writes `disposition.json` with only the
    enumerated controlled keys, no sidecar, no launch output; `target_reached` proceeds.
11. No-overwrite: second `init` on an existing session dir fails; each `measure` stage twice fails.
12. Privacy: an answers file with a forbidden key (`medication_notes`, `participant_name`) is
    rejected; so is any non-whitelisted key, including derived fields (`session_id`, `visit_utc`,
    `posture`, `intended_duration_s`) and settle keys on recovery (D7).
13. `check` exits non-zero on each gate in §6.1 independently: dirty tree; missing digest sidecar;
    incomplete registry chain whose latest revision loads; each of the three config gates; a
    non-`planned` session; a config/protocol-constant mismatch; a non-repo-root cwd; a deny-listed
    work root.
14. **Launch contract.** Patch `sys.argv` from the emitted `launch_argv`, assert
    `_validate_prospective_cli(args, cfg, cfg_path, {"git_commit": "abc", "git_dirty": False})`
    returns metadata without raising, for all three arms — recovery using its stamped literal epoch
    (D13) — and assert `launch_argv` equals an exact expected list. `launch_command` is asserted
    separately as a string and never fed to argparse.
15. NaN/Inf rejected by the tool, from typed input **and** from YAML `.nan`/`.inf`/`Infinity` tokens.
    Note `abs(float("nan")) > 1.0` is `False`, so only `math.isfinite`
    ([common.py:83-84](../src/m2/common.py#L83-L84)) catches it.
16. Type strictness: booleans as YAML bools; `cohort_slot`, `visit_number`, `session_order`,
    `configured_chirps_per_frame`, `commanded_rate_bpm`, `metronome_rate_bpm` as YAML ints
    (`require_int` uses `type(value) is not int`).
17. `scene_non_health_notes`: a `_FORBIDDEN_SCENE_NOTE_TERMS` term rejected; >500 chars rejected;
    whitespace-only rejected.
18. `visit_utc` stamp passes `_validate_utc` — asserts the `Z` suffix (D2).
19. `init` refuses an unknown subject and a non-`planned` session.
20. `git -C <root> status --porcelain` is byte-identical before and after `init` and every `measure`
    stage (acceptance criterion 5).
21. **Recovery staging.** `measure --stage seated` stamps its epoch before prompting (D13); the
    merge rejects a key whose value differs between the main and seated files; the seated stage
    refuses to run before the main stage.
22. D-M6 plausibility bounds abort with a remedy, and are asserted to change no validator outcome.

## 9. Review and build order

Milestone 0 commit + full-suite baseline → milestone A with its own code review → implement →
tests §8 → independent code review of the diff. The second review requires no third plan review
provided §5 D1, §5 D13 and §6.3 landed as described, which they have. The tool touches no range-FFT,
phase, filter, peak-picking or Masimo-parser code, so §6's DSP trigger does not apply, but the code
review still covers the privacy schema, the staging boundary and the no-bypass property.

## 10. Acceptance criteria

1. Every **required** field in the arm's allowed set is derived by `init` or prompted by `measure` at
   the stage where it first becomes observable — none left for hand-editing, none attested early.
   `synthetic_fixture` is never emitted.
2. No emitted sidecar reaches disk without passing the full D3 round trip, YAML **and** JSON.
3. No measurement or attestation has a default value anywhere in the tool.
4. The emitted `launch_argv` passes `_validate_prospective_cli` for all three arms.
5. `git -C <root> status --porcelain` is unchanged by running any subcommand.
6. `check` fails closed on every gate in §6.1.
7. Full suite passes against the milestone-0 baseline; every §8 case present.

## 11. Known limitations and open items

1. **The settle evidence is never parsed.** The operator supplies both the summary numbers and the
   file, and nothing checks they agree — the hash proves a file was bound, not that it supports the
   numbers. Deliberately not parsed: no format contract exists for the export. `plans/m4_stage12_review.md:246`
   classified this **[Blocking]** for its own milestone; it is not blocking here because this tool
   changes neither the numbers nor their use — it only stops the hash being transcribed by hand.
2. **`sit_to_record_delay_s` is unbounded.** `validate_recovery_runtime_timing`
   ([:488-495](../src/m2/acquisition_metadata.py#L488-L495)) requires only `>= 0` and
   `notes/analysis_prespec.md` sets no bound. This is a **design constraint, not a mere limitation**:
   [analysis_prespec.md:314](../notes/analysis_prespec.md#L314) requires `max(R_s) - min(R_s) >= 20.0`
   bpm per recovery session, [:321](../notes/analysis_prespec.md#L321) says a Stage-1 failure cannot
   trigger recapture, and §11 of the prespec gates the arm on 8 of 10 final subjects — so seated-stage
   time can permanently forfeit a subject. Per D-OWN-6 no bound is set yet; the tool records the delay
   and its seated-stage duration every session so the decision can be made on real values.
3. **[DONE at `bf1e51e`] The two runbook rewrites.** §1.3 now permits the gitignored in-repo
   `m2_capture_work/` per D-OWN-3, and `HANDOFF.md` §4 no longer carries the external-directory
   instruction. §4's `$recoverySeatedStartUtc` one-liner is **deliberately still there**, with a
   marked pending-change block: it remains the correct procedure until
   `scripts/m2_scaffold_sidecar.py` exists, and rewriting it to call a script that does not exist
   would be the dangling reference this item warned about. That block is written to be deleted when
   the tool ships — it is not documentation to keep.
4. **Five follow-up diffs named, all outside this stop boundary:** adding the sidecar-vs-config
   cross-check to `_validate_prospective_cli` (D-M5); a CLI for the registry
   `withdrawn`/`recovery_not_cleared` states (§6.3); a retry-record contract before `--attempt`
   can return (D8); **[R4]** a `MAX_SETTLE_S` upper bound, since `notes/protocol.md` aborts a settle
   over 5 minutes but only the lower bound is enforced (§4); and **[R4]** a decision on whether M2 and
   M4 should share a protocol-constants module instead of duplicating thresholds (§4). The last is an
   owner question: the M2/M4 split is deliberate per `src/m2/__init__.py`, and the agreement test in
   `tests/test_m2_acquisition_metadata.py` holds either way — note it does **not** cover
   `MIN_SETTLE_S`, which is why nothing warned when D-OWN-7 landed in M2 only and left M4's verbatim
   transcription of the criterion two limbs of three.
5. **The finalization sidecar has the same placeholder hazard, unfixed.**
   `templates/m2_finalization.yaml` ships `clock_offset_end_s: 0.0`, `final_pr_bpm: 70.0`,
   `post_monitoring_pr_bpm: 74.0` — schema-valid placeholders on the one artifact where a placeholder
   satisfies a *scientific* gate:
   `abs(post_monitoring_pr_bpm - pre_exertion_resting_pr_bpm) <= 5.0`
   ([:442-445](../src/m2/acquisition_metadata.py#L442-L445)). Out of scope here; the fix is
   non-schema-valid sentinels, as `settle_evidence_sha256` already uses. Named follow-up.
6. **Non-automatable privacy residual.** Neither `_reject_private_fields` (key-name based) nor
   `_FORBIDDEN_SCENE_NOTE_TERMS` catches a participant name typed into `scene_non_health_notes`. The
   `checklist.md` instruction is the only mitigation. Accepted exfiltration residual of the in-repo
   work root is recorded at D-OWN-3.
7. **[DONE at `bf1e51e`] `HANDOFF.md` rewritten** per CLAUDE.md §10.1 — the uncommitted-M2-changes
   claim, the `da3287d` HEAD and the external-work-directory instruction are gone, and the
   milestone-0 baseline is recorded at `da9777c`.

## 12. Resolved: the natural/paced countdown is 30 s

Commit `92d93ab` set [live_demo.py:773](../scripts/live_demo.py#L773) to return **30** while the
runbook, the docstring, `HANDOFF.md` and three tests said 60. The owner confirmed on 2026-08-12 that
**30 s is the intended protocol value**; all six places were aligned to 30 that day and logged in
`HISTORY.md`.

The pre-image was `return 1`, not 60 — independently confirmed by the second review, which found
`git show b7947f1:scripts/live_demo.py` line 773 is `return 1` while the same commit's test asserted
60. So those documents had already disagreed with the code *before* `92d93ab`; they were never in
agreement. The original M2 `HISTORY.md` entry stating "Natural/paced retain the 60-second countdown"
is wrong for the same reason; it is append-only and superseded by the 2026-08-12 entry, not edited.

Two clarifications this plan depends on:

- The launch countdown and `settle_evidence_window_s` are **different quantities**. The settle window
  is validated to be exactly 60.0 ([:332-333](../src/m2/acquisition_metadata.py#L332-L333)) and is
  unaffected.
- The countdown leaves **no evidentiary footprint**: absent from `run_metadata`, not a CLI argument,
  and `start_wall_utc` is stamped after it elapses. The tool records nothing about it and obtains its
  value by calling `_prospective_start_delay_s` (D12).
