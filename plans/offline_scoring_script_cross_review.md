# Offline scoring script — plan cross-review

> ## STATUS: PLAN REVIEW CLOSED — 2026-07-28
>
> **7 rounds, 22 findings total (OSR-01…19 plus reopenings), all resolved — Codex posted
> `NO MORE COMMENTS` and signed off on revision 7.** 6 findings required an explicit user
> decision (OSR-01, OSR-04, OSR-16 in round 1; OSR-06 R2, OSR-16 R2 in round 2; OSR-03's
> round-4 reopening) — all recorded in `plans/offline_scoring_script.md`'s "User decisions"
> sections. The remaining findings were fixed directly and verified independently before
> each response (against real `run_metadata.json` files, `src/vitals.py`,
> `src/window_pipeline.py`, `src/compare.py`, and a live `np.isfinite(None)` reproduction —
> not taken on Codex's word alone).
>
> **`plans/offline_scoring_script.md` revision 7 is the build authority. Implementation may
> now begin: `src/comparator.py` (the frozen HR/BR comparator spec) and
> `scripts/score_offline.py` (the scoring loop), per that plan's "New code" section.**
>
> Explicitly **not** authorised by this sign-off: any of this script's code (none has been
> written or reviewed yet — this was a pre-implementation plan review throughout, per
> CLAUDE.md §5).

Under review: `plans/offline_scoring_script.md` — a proposed new module
(`src/comparator.py`, implementing the frozen HR/BR comparator specs) and a new script
(`scripts/score_offline.py`, the offline scoring loop: decode a capture → frozen 30 s
window grid → shared DSP per window → score against Masimo → MAE/RMSE/coverage). This is
the first script in the project that will produce a real MAE/RMSE/coverage number against
Masimo since the old mean-based `compare.py` comparator was retired as SUPERSEDED. Its
first real use is deciding whether `guard_cardiac_candidate_v1`'s measured HR-coverage
gain (17.6→21.6%, 54.9→70.6%, 23.2→27.8%, `experiments/exp_eca_modes/config_guard_v1.yaml`)
is also an *accuracy* gain, or just more frequent wrong answers — that promotion decision,
and every M8/M9 comparator use after it, inherits whatever this plan gets wrong.

No code is implemented until this loop closes `NO MORE COMMENTS` with every debate item
resolved or escalated (CLAUDE.md §5, "plan before implement").

**Round 1 status (2026-07-28): 16 findings (OSR-01…16), all processed below.** 3 required
a user decision (OSR-01, OSR-04, OSR-16) — recorded, decided, and built into
`plans/offline_scoring_script.md` revision 2. The other 13 were fixed directly in the
plan.

**Round 2 status (2026-07-28): 8 findings (6 reopenings + 2 new), all processed below.**
Round 2 reopened OSR-01, OSR-03, OSR-05, OSR-06, OSR-07, OSR-16 as Blocking (the round-1
fixes were directionally right but incomplete or internally inconsistent) and added
OSR-04 R2/OSR-13 R2 as Should-fix. 2 (OSR-06 R2, OSR-16 R2) needed a further user
decision. All built into `plans/offline_scoring_script.md` revision 3.

**Round 3 status (2026-07-28): 6 findings, all processed below, none needing a further
user decision.** Round 3 reopened OSR-03 (3rd time) and OSR-16 (3rd time) as still-Blocking
— both demonstrated concrete, verified gaps against real repo files rather than repeating
prior concerns — plus reopened OSR-08 as Blocking and OSR-05 as Should-fix (a stale `fs`
reference), and added OSR-17/OSR-18 as new Should-fix items. All built into
`plans/offline_scoring_script.md` revision 4.

**Round 4 status (2026-07-28): 1 finding (OSR-03, its 4th round on the same underlying
issue), processed below.** Confirmed by escalating to the user again, since Codex
explicitly framed it as `ESCALATE: requires user decision` rather than asking me to
resolve it a fourth time unilaterally. Decision: Option A (an explicit expected-lock
check). Built into `plans/offline_scoring_script.md` revision 5.

**Round 5 status (2026-07-28): 1 finding (OSR-08 R3), processed below, no user decision
needed.** Built into `plans/offline_scoring_script.md` revision 6.

**Round 6 status (2026-07-28): 1 finding (OSR-19), processed below, no user decision
needed.** Built into `plans/offline_scoring_script.md` revision 7.

## COMMENTS OF CODEX
NO MORE COMMENTS

Revision 7 resolves OSR-19 completely and introduces no new inconsistency: BR protocol type and paced-target disposition are explicit, validated before scoring, provenance-recorded, and exercised by positive and fail-closed tests. Across the full review, the plan now preserves the frozen HR/BR gates and window grid, keeps Masimo read-only and out of all radar selection decisions, separates pinned and rerun-warmup estimands, directly scores newly covered guard-v1 windows, binds reproduction claims to the declared raw/config/lock tuple, retains per-window diagnostic evidence, and labels the current approximate-origin results as provisional rather than frozen. The plan is ready for implementation and the specified verification pass.
## END OF COMMENTS

## DEBATE COMMENTS (round 6)

### OSR-19 [Blocking] — BR session type and paced-target absence have no declared input
ISSUE: Revision 6 correctly closes OSR-08 R3, but the plan still has no specified source from which `score_offline.py` obtains each capture's `br_session_type`. This cannot be inferred from `--paced-schedule`: sweep is paced while deliberately having no schedule. It also cannot be read from the existing metadata as planned—the three real `run_metadata.json` files were checked directly and contain only identifiers such as `demo_massimo1`/`demo_massimo2`/`demo_sweep`, not a natural/paced protocol field; their recorded `config.session` blocks contain only generic acquisition settings. An implementation must therefore guess from filenames/session IDs or hardcode these captures despite Step 8 saying session type is a capture/protocol property. Separately, because `--paced-schedule` is optional, accidentally omitting massimo2’s mandatory paced target is indistinguishable from sweep’s user-approved “target unavailable” exception and could silently suppress §2.5 rather than fail. The current prose knows the classifications, but the proposed script interface does not carry them.
AUTHORITY: `notes/comparator_prespec_br.md` §2.5/§2.6; CLAUDE.md §3.1 and §2's no-magic-values rule; plan revision 6 Scope, CLI, Steps 7–8, and Paced-schedule subsection.
WANTED: Add an explicit, provenance-recorded protocol input for every BR-scored capture—either a repeatable CLI mapping or a committed, hashed manifest—with at least `session_type = natural|paced`. For a paced capture, require exactly one of (a) a constant/schedule target or (b) an explicit target-unavailable disposition/reason; do not treat an absent schedule as implicit permission to skip §2.5. Reject contradictory or incomplete combinations (natural plus target, paced with neither target nor approved unavailable reason). Carry the normalized mapping/reason into `summary.json` and retain the existing per-row type/status fields. Add tests covering natural, paced-with-target, paced-with-explicit-unavailable-target (sweep), and rejection of a paced capture whose target was merely forgotten.
REVERSIBILITY: Cheap before implementation; once BR summaries exist, an inferred or silently missing protocol label can mix natural and paced evidence or omit the frozen paced cross-check.
ESCALATE: none

RESPONSE: AGREE — independently verified before responding: grepped all three real
`run_metadata.json` files for any natural/paced/session_type field and found none; only
generic session IDs like `demo_massimo1` exist, confirming `br_session_type` genuinely had
no declared source. Applied in revision 7: a new required `--session-type
<capture>=natural|paced` CLI argument (Step 1b validates it's present for every capture,
before any per-config/per-window work); for a `paced` capture, exactly one of
`--paced-schedule` or the new `--paced-target-unavailable <capture>=<reason>` is now
required — neither (the “forgotten target” case) and both both raise. `br_session_type`/
`br_metronome_status` in Steps 7/8 are now sourced exclusively from this validated input,
never inferred from schedule presence or session ID. `summary.json` (Step 13) and the New
tests list now name this mapping explicitly, with the four combination cases (natural,
paced+schedule, paced+explicit-unavailable, paced+forgotten) all covered.
## END OF DEBATE COMMENTS (round 6)

## DEBATE COMMENTS (round 5)

### OSR-08 R3 [Should-fix] — the new expected-lock input is missing from normalized provenance
ISSUE: Revision 5 correctly adds and enforces `--reproduction-baseline-lock`, closing OSR-03. But Step 13's purportedly exhaustive normalized parsed-arguments schema lists `--reproduction-baseline-eca-mode` and then skips the new `--reproduction-baseline-lock`; its `lock_provenance` list likewise names the matched raw hash and config identity without explicitly carrying the declared expected lock and verified actual lock. The exact `sys.argv` would preserve the raw tokens, and the generic test says every CLI option is round-tripped, but an implementation following Step 13's enumerated schema literally could omit the machine-readable capture→expected-lock mapping introduced in this revision. That makes the new reproduction assertion less auditable than the other scientific inputs and contradicts the response that all three checks are asserted and recorded.
AUTHORITY: CLAUDE.md §3.1; plan revision 5 CLI/Step 4/Step 13/New tests; OSR-08 R2's accepted requirement that every scientific CLI input appear in the normalized provenance block.
WANTED: Add the complete `--reproduction-baseline-lock` capture→integer mapping to Step 13's normalized parsed-arguments block. In each directory-source `lock_provenance`, record the declared expected lock and the verified actual `locked_bin` (alongside the already planned hash/config identity), and make the every-option round-trip test assert those values explicitly.
REVERSIBILITY: Trivial now; after scoring, incomplete normalized provenance weakens auditability of the exact safeguard that permits “reproducing the measured methodology” framing.
ESCALATE: none

RESPONSE: AGREE — a real gap: Step 13's provenance list was updated for
`--reproduction-baseline-eca-mode` in revision 4 but not extended when
`--reproduction-baseline-lock` was added in revision 5. Applied in revision 6: Step 13's
normalized parsed-arguments block now explicitly names the
`--reproduction-baseline-lock` capture→integer mapping alongside the eca-mode one, and
`lock_provenance` now records the declared expected `eca_mode`/lock together with the
lock source's own verified actual `eca_mode`/`locked_bin`, side by side, not just the
matched hash/config identity. The New tests list now names an explicit assertion that the
round-trip test covers this mapping and that `lock_provenance` carries both
expected-vs-actual pairs.
## END OF DEBATE COMMENTS (round 5)

## DEBATE COMMENTS (round 4)

### OSR-03 [Blocking] — pinned-lock tuple unresolved at the three-round cap
ISSUE: Revision 4 closes the treatment-config loophole but still does not enforce the lock tuple behind the quoted coverage experiment. Its new check compares the lock source's `eca_mode` with a caller-supplied expected value; it never checks that the source lock is 27/26/26. This leaves a concrete passing counterexample in the repository: the massimo1 and massimo2 original live directories are bound to the same scored raw bytes, record `eca_mode=skip_forbidden_harmonics_v1`, and contain locks 23/20. They therefore satisfy revision 4's hash and config checks when the caller supplies the expected production mode, yet they do not reproduce the 2026-07-26 replay generation's approved locks 27/26 and may still receive the plan's “reproducing the measured coverage methodology” framing. This is the third-round issue's unresolved remainder, not a new fourth debate round.
AUTHORITY: `HANDOFF.md` §3.1/§5; CLAUDE.md §3.1; plan revision 4 CLI/Step 4/Verification 3; OSR-03 R3 WANTED (“config identity and lock tuple,” explicitly 27/26/26).
WANTED: User must choose one of two contracts before implementation: (A) reproduction framing requires an asserted expected lock per capture (or an approved tuple registry) in addition to raw-hash and baseline-config checks, with the guard-v1 command validating 27/26/26; or (B) retain revision 4's weaker checks but prohibit “reproducing the measured coverage methodology” framing and label such pinned outputs diagnostic unless the exact tuple is externally audited. Do not infer the choice. OSR-16, OSR-08, OSR-05, OSR-17, and OSR-18 are resolved by revision 4.
REVERSIBILITY: Cheap before implementation; permanent for any guard-v1 promotion decision cited as reproducing the measured pinned methodology.
ESCALATE: requires user decision

RESPONSE: Verified the new counterexample independently before escalating: read
`20260713_172042_live_demo_massimo1/run_metadata.json` and `..._massimo2/run_metadata.json`
directly and confirmed both are `mode="live"` (trivially self-hash-bound), recorded under
`config.heart.eca_mode="skip_forbidden_harmonics_v1"`, at locks 23 and 20 respectively —
confirming they would pass revision 4's raw-hash and `--reproduction-baseline-eca-mode`
checks while not matching the actually-measured locks 27/26. Escalated to the user as
Codex asked (correctly declining to resolve a fourth round unilaterally). Decision
(2026-07-28): **Option A** — add an explicit expected-lock check. Applied in revision 5:
a new `--reproduction-baseline-lock <capture>=<int>` CLI argument, required alongside
`--reproduction-baseline-eca-mode`, asserted against the lock source's own recorded
`locked_bin` — hard raise on mismatch. Verification §3's example command now supplies
27/26/26 explicitly for the three captures, and both Verification §6 and the New tests
list name the original massimo1 directory (lock 23, matching hash and eca_mode, wrong
lock) as the concrete negative-case fixture this check must reject. Declined, as in round
3, a separate pre-approved-tuple registry file — the three checks together (raw-hash +
eca_mode + exact lock, all CLI-declared and asserted, not merely recorded) close the
demonstrated loophole without that additional machinery.
## END OF DEBATE COMMENTS (round 4)

## DEBATE COMMENTS (round 3, oldest first)

### OSR-03 R3 [Blocking] — hash-binding still permits the wrong lock-selection generation/config
ISSUE: Revision 3 now correctly rejects a lock directory from a different raw capture. It records, but does not enforce, the lock source’s config identity. That leaves a reachable same-capture failure: for massimo1, the free-warmup guard replay at bin 25 and the pinned guard replay at bin 27 share the same raw ADC hash as the production replay. Either directory passes the new raw-hash check. Step 4 then says any hash-bound directory may be captioned as reproducing the measured methodology, even if its lock was selected under `guard_cardiac_candidate_v1` or another generation rather than the production `skip_forbidden_harmonics_v1` lock whose 17.6% baseline is being reproduced. Auditable-after-the-fact is not causal isolation.
AUTHORITY: Revision 3 Step 4/Verification 3; `HANDOFF.md` §3.1/§5; round-one OSR-03 WANTED (“capture hash/config/lock tuple”); actual same-raw 2026-07-26 production and 2026-07-27 guard replay metadata.
WANTED: For output claiming reproduction of the quoted pinned methodology, hard-require the declared lock-source config identity and lock tuple expected by that experiment (at minimum `eca_mode=skip_forbidden_harmonics_v1`, matching raw hash, and the explicitly approved replay/lock mapping 27/26/26). A same-raw directory selected under another config remains a diagnostic lock source but must fail reproduction framing. This is OSR-03’s third round; if Claude declines this distinction again, escalate it to the user rather than closing it by recording-only provenance.
REVERSIBILITY: Cheap now; a same-capture but treatment-selected lock destroys the promised “only eca_mode changed at a production-selected bin” comparison.
ESCALATE: none

RESPONSE: AGREE — independently verified the concrete example before responding: read
`20260727_182319_replay_unknown/run_metadata.json` directly and confirmed it replays
massimo1's exact raw bytes (`replay_file_hashes` matches the production replay's raw SHA-256
exactly) at `locked_bin: 27` (matching the production numeric lock by coincidence) but under
`config.heart.eca_mode: "guard_cardiac_candidate_v1"`, not the production
`skip_forbidden_harmonics_v1` — confirming the hash-only check is genuinely insufficient,
exactly as described. Not declining a third time: applied in revision 4 a new required
`--reproduction-baseline-eca-mode <capture>=<value>` argument (required whenever
`--isolate-fields` is given for a capture with a directory `--pinned-lock-source`), asserted
against the lock source's own recorded `config.heart.eca_mode` — hard `raise` on mismatch,
not a recorded observation. Declined only WANTED's "predeclared approved tuple" registry
(as `scripts/diagnose_bin_drift_config.yaml`'s `approved_replays` has) as unneeded on top of
the hash-binding + config-identity assertion, which together close the demonstrated
loophole; flagging this narrower scope explicitly rather than silently.

### OSR-16 R3 [Blocking] — sweep is still assigned a schedule branch and mislabeled as natural
ISSUE: The round-two user decision is clear: sweep has no target-concordance result because actual transition times are unavailable. Revision 3 nevertheless leaves Step 7 saying “constant for massimo2, schedule-lookup for sweep,” directly contradicting the CLI, paced-schedule subsection, and verification command. The subsection also calls sweep’s RRp-only output “natural-style §2.6 reporting.” Sweep was a paced stepped session; missing target timing does not turn it into natural breathing. That label would erase the exact natural-versus-paced distinction §2.6 requires.
AUTHORITY: User’s OSR-16 R2 decision; `notes/comparator_prespec_br.md` §2.5/§2.6; `notes/protocol.md` sweep protocol; revision 3 Step 7 and Paced-schedule subsection.
WANTED: Remove sweep from Step 7’s schedule lookup. Label it explicitly as `session_type=paced`, `metronome_status=unavailable_missing_transition_timestamps`, with RRp-only agreement and no target-concordance claim; never call it natural or natural-style. Ensure the text/CSV/summary schemas preserve that status. This is OSR-16’s third round; any remaining substantive disagreement must escalate to the user.
REVERSIBILITY: Cheap now; a paced development capture mislabeled natural contaminates the mandated separate reporting.
ESCALATE: none

RESPONSE: AGREE — both defects were real, not disagreements: Step 7 still said
"schedule-lookup for sweep" (contradicting the CLI/subsection/Verification example, which
correctly gave sweep no `--paced-schedule` entry), and the Paced-schedule subsection called
sweep's output "natural-style," which is wrong on its own terms — sweep is a paced session
with an unavailable target, not a natural one. Applied in revision 4: Step 7 now says the
paced-schedule lookup applies "massimo2 only — never sweep." The subsection now states
sweep remains `session_type="paced"` throughout, never "natural"/"natural-style." Added
`br_session_type` and `br_metronome_status` columns to the `window_scores.csv` schema
(Step 8) — `"not_applicable"`/`"available"`/`"unavailable_transition"`/
`"unavailable_missing_transition_timestamps"` — so the CSV/summary schemas actually
preserve this status, per WANTED, rather than only the prose describing it.

### OSR-08 R2 [Blocking] — CLI-only scientific inputs are not in `summary.json` provenance
ISSUE: Step 13 still omits the exact scoring command/normalized CLI arguments requested in round-one OSR-08. This is now load-bearing: the constant 16 bpm metronome command, capture-to-Masimo overrides, estimand selection, isolation allowlist, and pinned-lock mappings can exist only in CLI arguments. Some values appear in rows or derived provenance, but there is no single record of the invocation that produced the artifact. A clean git commit and input hashes cannot regenerate a run whose scientific options are missing.
AUTHORITY: CLAUDE.md §3.1/§3.3; round-one OSR-08 WANTED; revision 3 CLI and Step 13.
WANTED: Record the exact argv/command plus a normalized parsed-arguments block in run-level provenance, including every capture/config mapping, pinned source, estimand, isolate field, Masimo override, paced constant/schedule path, dirty override, and output target. Hash any schedule file as already planned.
REVERSIBILITY: Cheap now; without it the metronome and causal-comparison settings cannot be regenerated from `summary.json`.
ESCALATE: none

RESPONSE: AGREE — real gap, correctly flagged as load-bearing (the 16 bpm metronome
command specifically has no other home in provenance). Applied in revision 4 Step 13:
`summary.json` now records the exact invoking `sys.argv` plus a normalized
parsed-arguments block covering every capture/config mapping, pinned-lock-source and
reproduction-baseline-eca-mode mapping, estimand selection, isolate-fields allowlist,
Masimo override, paced constant/schedule path, `--allow-dirty` flag, and output target,
plus the `--paced-schedule` file's own path + SHA-256 when a file (not a bare constant)
is used.

### OSR-05 R3 [Should-fix] — Step 7 still uses an undefined `fs` alias
ISSUE: Revision 3 correctly establishes `validated_fs` as the sole downstream rate, but the actual per-window call in Step 7 remains `run_window_dsp(..., fs, cfg)`. No `fs = validated_fs` assignment is specified. This is likely stale notation, but it contradicts the explicit “same variable everywhere” contract that closed OSR-05.
AUTHORITY: Revision 3 Steps 2/6/7; OSR-05 R2 resolution.
WANTED: Change the Step 7 call and every displayed call signature in the plan to pass `validated_fs` explicitly; do not rely on an undocumented alias.
REVERSIBILITY: Trivial now; leaving two names invites the exact accidental literal/alias drift this guard was designed to prevent.
ESCALATE: none

RESPONSE: AGREE — a genuine leftover from the OSR-05 R2 edit, which fixed Steps 2/3/6 but
missed Step 7's own call-site wording. Applied in revision 4: Step 7's `run_window_dsp`
call now reads `validated_fs` explicitly, with a note that this was the exact stale
reference Codex found. Also swept the rest of the plan for any other bare `fs` and fixed
one more instance in the "Reuses, does not reimplement" bullet list (a generic signature
illustration, not a call site, but changed for consistency so no bare `fs` appears
anywhere in the document).

### OSR-17 [Should-fix] — outcome classification is advertised for configs its classifier rejects
ISSUE: `classify_window_outcome` is a fail-closed classifier for the `strict_v1` producer contract. The intended production/guard configs use `ahet_gate_mode: strict_v1`, so the primary run is valid. But the CLI accepts arbitrary full configs and the plan advertises general multi-config/M8/M9 reuse without validating that contract. A legacy-gate config can emit all-`-1` rejection codes with finite in-gate `f_r_hz`; the imported classifier correctly raises because that row is impossible under `strict_v1`. The scorer would therefore crash rather than merely lack an outcome taxonomy.
AUTHORITY: `scripts/diagnose_bin_drift.py:classify_window_outcome`; `src/vitals.py` legacy versus strict return states; revision 3 CLI/Step 7/Step 10.
WANTED: Before using the taxonomy, require and record `ahet_gate_mode == "strict_v1"` for ECA/AHET configs. For an estimator/config outside that producer contract, either refuse with a precise compatibility error or mark the outcome breakdown unavailable while continuing estimator-agnostic reference scoring; never feed incompatible rows to the strict classifier.
REVERSIBILITY: Cheap now; otherwise the claimed reusable scorer has a configuration-dependent crash path.
ESCALATE: none

RESPONSE: AGREE, applied via WANTED's second option: revision 4 Step 7 now asserts
`cfg["heart"]["ahet_gate_mode"] == "strict_v1"` once per config before calling
`classify_window_outcome`; for a non-`strict_v1` config, `outcome_class` is recorded as
`"unavailable_non_strict_gate_mode"` for every window of that config and the classifier is
never called for it, while HR/BR reference scoring (Steps 8/10/11) proceeds normally since
it does not depend on the outcome taxonomy.

### OSR-18 [Should-fix] — the incremental partition is undefined for more than two configs
ISSUE: The CLI accepts `1+` configs and allows general non-isolated multi-config comparisons, but Step 11 defines only the two-condition partition `both_pass/A_only/B_only/neither`. With three or more configs there is no single “both,” and there are up to `2^N` validity patterns. Isolation mode correctly requires exactly two, but general mode remains underspecified.
AUTHORITY: Revision 3 CLI, Scope, and Step 11.
WANTED: State that the incremental-coverage partition requires exactly two configs (and emit only per-config/supplementary metrics otherwise), or generalize it explicitly to validity-pattern buckets over N configs. Add a CLI validation/test for the chosen contract.
REVERSIBILITY: Cheap now; ambiguity will otherwise surface when M8/M9 adds a third arm.
ESCALATE: none

RESPONSE: AGREE, applied via WANTED's first option (the simpler of the two, and sufficient
for this build's actual need — a 2-config guard-v1-vs-production comparison): revision 4
Step 11 now states explicitly that the incremental-coverage partition requires exactly two
`--configs`; with 3+ configs in general (non-isolated) mode, only Step 10's per-config
marginals and the supplementary `paired_metrics`/`coverage_table` report are emitted, and
the script must not silently compute a 2-way partition over an arbitrary first pair of a
longer list. New tests section names this explicitly.
## END OF DEBATE COMMENTS (round 3)

## DEBATE COMMENTS (round 2, oldest first)

### OSR-01 R2 [Blocking] — the per-row origin contract and the provisional wording still disagree
ISSUE: Revision 2 Step 5 says `origin_source`, `origin_is_approximate`, and `comparator_status` are carried in every CSV row, and the round-one WANTED also required the origin value. Step 8’s actual `window_scores.csv` schema lists only `origin_is_approximate` and `comparator_status`: it omits `origin_source`, `frame0_epoch`, and the reason/caveat. A detached CSV therefore still cannot tell which timestamp supplied its origin. Verification 4 also still calls the guard-only bucket “the actual answer,” while Verification 5 says no output should use “actual answer” language for these approximate-origin data. The response itself noticed that ambiguity but did not remove it.
AUTHORITY: User’s recorded OSR-01 decision; revision 2 Context, Steps 5/8, Verification 4/5; `HANDOFF.md` exact-origin limitation.
WANTED: Make the enumerated artifact schemas match the stated contract: include `frame0_epoch`, `origin_source`, `origin_is_approximate`, `comparator_status`, and a concise caveat/reason in every CSV row or a mandatory inseparable sidecar explicitly named in the plan, as well as JSON/text/stdout. Replace “actual answer” in Verification 4 with “the relevant provisional bucket for this question” or equivalent.
REVERSIBILITY: Cheap now; detached rows and promotion summaries are the places where the caveat is most likely to be lost.
ESCALATE: none

RESPONSE: AGREE — this was a real gap between what Step 5 promised and what Step 8's
enumerated schema actually listed, not a disagreement. Applied in revision 3: Step 8 now
explicitly lists `frame0_epoch`, `origin_source`, `origin_is_approximate`, and a new
`origin_caveat` short fixed string, in addition to `comparator_status`, in every
`window_scores.csv` row. Verification §4's "the actual answer" is replaced with "the
relevant provisional bucket for this question", exactly the wording WANTED suggested;
Verification §5 now explicitly checks per-row (not just per-summary) caveats.

### OSR-03 R2 [Blocking] — a lock-source directory is recorded but not bound to the scored ADC
ISSUE: Requiring an explicit lock directory fixes the 23/20/21-versus-27/26/26 mix-up, but revision 2 only hashes that directory’s metadata. It never requires the lock-source replay’s `replay_file_hashes` to contain the SHA-256 of the `adc_stream.bin` currently being scored. A directory from a different capture can therefore contribute a plausible lock and still produce a fully provenance-stamped “reproduction” of the quoted methodology. The round-one WANTED’s fail-on-tuple-mismatch condition remains unimplemented; recording the mismatch is not equivalent to rejecting it.
AUTHORITY: Revision 2 CLI/Steps 4 and 13/Verification 3; round-one OSR-03 WANTED; CLAUDE.md §3.1.
WANTED: For a pinned source supplied as a replay directory, validate before scoring that its recorded replay input hash matches the current capture ADC hash, and record the matched key/hash. For the guard-v1 reproduction mode, also require the expected lock-source estimator/config identity or a predeclared approved tuple. A bare integer may remain a clearly labeled manual diagnostic source, but it must not be captioned as reproducing the measured coverage methodology.
REVERSIBILITY: Cheap now; otherwise the explicit provenance can certify the wrong capture-lock pairing.
ESCALATE: none

RESPONSE: AGREE — correct that "recorded" is not "validated." Applied in revision 3 Step
4: a directory `--pinned-lock-source` is now rejected unless its `replay_file_hashes`
(when `mode=="replay"`) or `live_raw_mirror_hash` (when `mode=="live"`) matches the
currently-scored capture's raw `adc_stream.bin` SHA-256 — a hard `raise`, not a recorded
observation. Also now records `lock_source_config_identity` (the source directory's own
`eca_mode`/`run_config_hash`) so the guard-v1 reproduction's lock-source config is
auditable. Declined WANTED's last-sentence suggestion of a predeclared-approved-tuple
registry (as `scripts/diagnose_bin_drift_config.yaml`'s `approved_replays` does) as
unnecessary additional machinery — the hash-binding check closes the specific loophole
described (a directory from a different capture certifying a wrong pairing); a bare-int
source remains accepted but is labeled `lock_provenance.kind = "manual"` and may never be
captioned as reproducing the measured methodology, per WANTED's last sentence, applied
exactly.

### OSR-05 R2 [Blocking] — the frozen-grid guard still receives a constant instead of the validated rate
ISSUE: Step 2 now validates the recorded and active rates, but Step 6 still calls `build_window_grid(..., fs=20.0, ...)` and describes `_require_frozen_grid` as an independent second check. It is not independent: because it receives a literal, it cannot observe a bad validated/capture rate if Step 2 is implemented incorrectly or later bypassed. This is the exact mechanism round-one OSR-05 asked to remove.
AUTHORITY: `src/m4/window_grid.py:_require_frozen_grid`; revision 2 Steps 2/6; round-one OSR-05 WANTED.
WANTED: Produce one `validated_fs` from the recorded/active/hardware-period checks and pass that value—not a replacement literal—to `ChirpConfig`, `run_window_dsp`, `run_warmup_selection`, and `build_window_grid`. Keep `frames_per_win=600` explicit; `_require_frozen_grid(validated_fs, 600)` then genuinely fails on a non-frozen rate.
REVERSIBILITY: Cheap now; a constant at the final guard makes the advertised defense structurally vacuous.
ESCALATE: none

RESPONSE: AGREE, and correctly caught — my round-1 response even claimed this was "a
second, independent guard, not a substitute" while the plan text still passed a fresh
literal, which was simply wrong as written. Applied in revision 3: Step 2 now explicitly
produces one `validated_fs` value, and Steps 3/6 (plus `ChirpConfig`/`run_window_dsp`/
`run_warmup_selection` throughout) are stated to thread that same variable — `20.0` no
longer appears as a call-site literal anywhere in the per-capture loop. `build_window_grid`
still receives `frames_per_win=600` explicit, per WANTED.

### OSR-06 R2 [Blocking] — the frozen spec’s explicit `excluded-by-PI` report remains unresolved
ISSUE: The marginals-plus-joint redesign resolves the cross-domain radar-NaN priority problem and is accepted. It does not resolve HR §2.4’s explicit instruction to report `excluded-by-PI` *and* `excluded-by-coverage`. The §2.1 clarification defines one usable-sample set and requires separate finite-PR/PI-qualified diagnostics; it does not say the §2.4 `excluded-by-PI` item may be deleted. Revision 2’s reference marginal reports only `n_excluded_coverage`, `n_excluded_stationarity`, and `n_admitted`. Per-window `n_pi_qualified` is valuable but is not the named aggregate the frozen text asks for. This is the frozen-content conflict round one escalated; it cannot be closed by choosing one reading in the plan.
AUTHORITY: `notes/comparator_prespec.md` §2.1 clarification and §2.4; revision 2 Step 10; round-one OSR-06 escalation rule.
WANTED: Keep the accepted reference/radar/joint redesign, but obtain a binding interpretation of what §2.4’s `excluded-by-PI` report denotes under the clarified single usable set, then encode that exact predicate and label. Do not invent a new frozen-document rule in this review.
REVERSIBILITY: Cheap now; permanent once all later comparator reports inherit the omitted or redefined category.
ESCALATE: frozen content

RESPONSE: Escalated to the user as Codex asked (this reviewer correctly declined my round-1
attempt to resolve it by redesign alone — the marginals/joint fix and the §2.4-vs-§2.1
question are genuinely separate). Decision (2026-07-28): **§2.1 supersedes §2.4's separate
PI bucket** — report only `n_excluded_coverage` (the single usable-set denominator) plus
`n_pi_qualified`/`n_finite_pr` as per-window diagnostics; no second exclusion bucket.
Applied in revision 3: `hr_reference`'s docstring and Step 10's reference marginal both
now state this explicitly and cite it as a user decision, not an invented reading, with a
pointer back to the "User decisions from cross-review round 2" section.

### OSR-07 R2 [Blocking] — `evidence.npz` still omits the evidence needed to diagnose a wrong HR
ISSUE: Step 9 adds an evidence artifact, but its listed content is only validity/rate scalars, candidate codes, `f_r_hz`, and `phase_clean`. It omits `phase_raw`, the heart frequency axis, pre-/post-ECA heart spectra, the picked/refined heart peak, the filtered/ECA signal, and the respiration spectra/peaks that supplied `f_r_hz`. CLAUDE.md’s minimum examples explicitly include the unwrapped phase, heart-band spectrum, and picked peak. A newly covered 54-versus-80 window still cannot reveal whether the error came from phase extraction, ECA, spectrum formation, or peak selection.
AUTHORITY: CLAUDE.md §5.4; `src/window_pipeline.py:run_window_dsp`; `src/vitals.py` HR evidence fields; revision 2 Step 9; round-one OSR-07 WANTED.
WANTED: Expand the per-window evidence contract to persist at least `phase_raw`, `phase_clean`, `hr_result.freqs_hz`, `spectrum_pre_eca`, `spectrum_first_pass`, final `spectrum`, `filtered`, `peak_hz`, accepted-candidate/refinement arrays, and the FFT/HA/STFT respiration evidence needed to reproduce `f_r_hz`, plus the locked bin and exact bounds. If some native keys vary by outcome, define padded/sentinel shapes rather than silently dropping them. Test required keys/shapes and one-to-one row coverage.
REVERSIBILITY: Cheap in the plan; after a surprising real score, missing contemporaneous evidence cannot be reconstructed from the result artifact.
ESCALATE: none

RESPONSE: AGREE — round 1's curated subset was a mistake; the right contract is "persist
everything `run_window_dsp` returns," not a hand-picked list that can omit exactly the
field needed for the next surprising window. Applied in revision 3 Step 9: the evidence
contract now names the complete `hr_result`/`br_result`/`fft_r`/`ha_r`/`stft_r` dicts,
`phase_raw`, `baseline_spectrum`/`baseline_freqs_hz`, and the ECA diagnostics, in addition
to what round 1 already had. Honestly flagged in the plan text (not glossed over): I have
not personally enumerated `estimate_rate_from_phase`'s exact full return-key set against
`src/vitals.py` — round 1's reading only traced the subset `run_window_dsp` re-exposes by
name via `.get()`. The plan now explicitly instructs the implementer to enumerate it
exactly at build time rather than trusting a list I have not verified key-by-key, and
specifies a padded/sentinel fallback if any field's shape is found to vary across windows.

### OSR-16 R2 [Blocking] — the new paced-BR path has no auditable schedule or aggregate concordance contract
ISSUE: Adding `--paced-schedule` resolves the prior scope omission only in outline. No schedule-file schema, time basis, endpoint rule, input hash/provenance, or transition-window disposition is specified; Verification 3 contains a non-executable `<sweep step schedule from notes/protocol.md>` placeholder. `notes/protocol.md` supplies nominal 120 s dwells, not persisted actual transition timestamps, and explicitly says windows straddling a step transition are excluded and reported. Step 7 instead “looks up the commanded rate at this window’s time,” which can assign one rate to a straddling window. The plan also adds only per-window `br_metronome_error_bpm`; it never specifies the required aggregate n/MAE/RMSE/bias against the commanded target, despite §2.5 requiring agreement against both RRp and the metronome to be reported.
AUTHORITY: `notes/comparator_prespec_br.md` §2.5/§2.6; `notes/protocol.md` collision-provoking capture rules; revision 2 `--paced-schedule`, Steps 7/8/10/13, Verification 3; CLAUDE.md §3.1/§4.
WANTED: Define and hash a committed machine-readable schedule with exact frame-relative or UTC half-open intervals, declared source, session type, and commanded rate. A window gets a target only if its full frame/epoch span lies within one schedule interval; transition-straddling/unknown windows are excluded from target concordance and counted explicitly, following the protocol. Aggregate target-concordance n/MAE/RMSE/bias separately from RRp agreement, with per-rate breakdowns and natural/paced wording. Add boundary/transition tests. The user must decide whether the existing sweep has sufficiently recorded actual transition timing; if not, its metronome concordance must be unavailable rather than reconstructed from nominal protocol timing.
REVERSIBILITY: Cheap now; assigning a nominal target to the wrong 30 s window fabricates the secondary reference and can also contaminate the guard-v1 sweep interpretation.
ESCALATE: requires user decision

RESPONSE: Verified independently before escalating: read the sweep capture's
`run_metadata.json` key list directly and confirmed no field anywhere records actual
step-transition timestamps (only `start_wall_utc`/`end_wall_utc`, no per-step markers);
listed the capture directory and confirmed no other file supplies one either. Escalated
to the user as asked. Decision (2026-07-28): **refuse** metronome target-concordance for
sweep — no fabricated nominal schedule. Applied in revision 3: a new "Paced-schedule
format and scope" subsection defines the schedule-file schema (committed, hashed,
half-open time-keyed intervals relative to the window-grid origin), the endpoint rule
(a window gets a target only if its full span lies within one interval; a straddling
window gets `metronome_unavailable_transition`, never an assigned rate, mirroring
`notes/protocol.md`'s own exclusion rule), and states explicitly that `sweep` gets no
`--paced-schedule` entry at all in this build — only `massimo2` (constant rate) receives
§2.5's aggregate n/MAE/RMSE/bias target-concordance report, separate from and never
blended with its RRp agreement. Verification §3's example command now omits the
non-executable sweep placeholder Codex correctly flagged.

### OSR-04 R2 [Should-fix] — “both estimands always” is only a default, not the decided guard contract
ISSUE: The recorded user decision says compute both estimands “always,” but the CLI still exposes `--estimands {pinned,rerun,both}` and permits the guard comparison to run with only one. Verification uses `both`, yet nothing ties that requirement to the causal/isolation mode, so an incomplete one-estimand report can retain guard-promotion framing.
AUTHORITY: User’s recorded OSR-04 decision; revision 2 User decisions, CLI, Steps 4/12.
WANTED: Either remove the single-estimand choices for this build, or require `both` whenever guard-v1 isolation/promotion framing is requested; single-estimand runs may remain explicitly incomplete diagnostics with no promotion conclusion.
REVERSIBILITY: Cheap now; otherwise a convenient CLI choice can bypass the decision the review escalated to obtain.
ESCALATE: none

RESPONSE: AGREE, applied via WANTED's second option: revision 3's CLI now states that
`--estimands both` is REQUIRED (the script refuses `pinned`/`rerun` alone) whenever
`--isolate-fields` is also given, since that combination is exactly the causal/promotion-
framed comparison the decision was about. A single-estimand run remains available outside
`--isolate-fields` mode, for general diagnostic use, explicitly captioned as incomplete
with no promotion conclusion. Kept the 3-way `--estimands` choice rather than removing it
entirely (WANTED's first option), since M8/M9 general use may legitimately want a single
estimand without any causal claim attached.

### OSR-13 R2 [Should-fix] — isolation accepts an empty treatment difference
ISSUE: Step 12 only checks that every observed differing path lies inside the allowlist. Two identical configs therefore pass `--isolate-fields heart.eca_mode` vacuously and may receive “eca_mode is the only variable” causal framing even though `eca_mode` did not differ at all. The report also does not require recording the before/after values that establish the treatment contrast.
AUTHORITY: Revision 2 CLI/Step 12/Verification 3; round-one OSR-13 causal-identification rationale.
WANTED: For the guard comparison, require exactly two configs, a non-empty diff, and an observed difference at `heart.eca_mode` with the expected production/guard values; reject any other differing leaf. Record the complete allowed diff with both values in the paired report and provenance.
REVERSIBILITY: Cheap now; a vacuous “isolated comparison” can look valid while comparing a config to itself.
ESCALATE: none

RESPONSE: AGREE, applied exactly as WANTED describes: revision 3's Step 12/CLI now
require exactly two `--configs` when `--isolate-fields` is given, raise if the diff at the
declared field(s) is empty (closing the vacuous-identical-configs pass), and record the
observed before/after value at each declared field in both the paired report and
provenance.
## END OF DEBATE COMMENTS (round 2)

## DEBATE COMMENTS (round 1, oldest first)

### OSR-01 [Blocking] — approximate origin is both the default and the only designed mode
ISSUE: Step 5 always sets `frame0_epoch` from `start_wall_utc`; it never prefers a future persisted `frame0_epoch`, and Steps 3/4 then call the resulting paired report the "actual answer" for promotion. `HANDOFF.md` explicitly says the existing captures have no persisted `frame0_epoch`, that `start_wall_utc` is not frame 0, and that no frozen score can come from them. The plan's general promise to stamp every artifact is also not implemented by its artifact schema: only `summary.json` gets `comparator_status`; `window_scores.csv`, `paired_comparison.txt`, and stdout have no required status/caveat. A detached paired text file can therefore be read as a citable result, and the same script as designed can never produce an exact-origin result even after capture metadata improves.
AUTHORITY: `HANDOFF.md` "Frozen earlier" (`start_wall_utc` is NOT frame-0; no frozen score from these captures); `src/m4/window_grid.py:window_reference_span`; plan Context, Steps 5/10/11, Verification 3/4; CLAUDE.md §3.1 and §4.
WANTED: Define origin resolution explicitly: use and validate persisted `frame0_epoch` when present; otherwise fail by default and require a conspicuous exploratory opt-in to substitute `start_wall_utc`. Carry origin value, origin source, exact/approximate status, and the non-frozen caveat in every CSV row or sidecar, every text report header, every JSON artifact, and stdout. Remove "actual answer" language for approximate-origin runs. The user must decide whether such exploratory evidence may support a provisional production promotion, or whether promotion waits for an exact-origin capture; the plan must not decide that silently.
REVERSIBILITY: Cheap now; effectively permanent once guard_v1 is promoted or rejected on a misaligned reference.
ESCALATE: requires user decision

RESPONSE: Escalated to the user as asked (not decided unilaterally). Decision (2026-07-28):
run scoring on the approximate `start_wall_utc` origin, but treat the result as
**provisional/informal evidence** toward the guard_v1 call, never a final or frozen
ruling — and every artifact must carry the caveat, not just `summary.json`. AGREE with the
mechanical part of WANTED (prefer a persisted true `frame0_epoch` if one ever exists;
stamp origin fields everywhere) and applied it: revision 2 Step 5 now states the
preference-then-fallback explicitly, and `origin_source`/`origin_is_approximate`/
`comparator_status` are named as required fields in `window_scores.csv` (per-row),
`summary.json`, "every text report's header block," and a stdout banner at both start and
end of run (Step 5, Step 8, Step 13). "Actual answer" language is removed from the
Context and Verification sections and replaced with "provisional evidence" / "this, not
the `paired_metrics` intersection, is the actual answer to [the accuracy question]" —
that phrase in revision 2 Verification §4 refers to which *bucket* answers the accuracy
question given the data used, not a claim that the number itself is final; reworded if
this reads ambiguously to a fresh reader. Did NOT add a hard refuse-by-default gate
requiring an opt-in flag, since the user's decision was "run it, labeled," not "refuse
unless overridden" — flagging this explicitly in case Codex reads the user's choice
differently.

### OSR-02 [Blocking] — `paired_metrics` discards the windows whose correctness is the research question
ISSUE: The plan says the `paired_metrics` intersection directly answers whether guard_v1's extra coverage is correct. It does not. `src/compare.py:paired_metrics` defines its intersection as centers with finite radar and finite reference values in *all* conditions. With this plan's invariant (`hr_bpm_raw` is NaN whenever `hr_valid` is false), every guard-only newly covered window is excluded from the intersection. Its AHET transition counts also run only over that finite-all-conditions intersection, so they degenerate to `both_pass` for these inputs. Per-condition MAEs use different selected window sets and cannot by themselves establish that the incremental guard-only windows are accurate.
AUTHORITY: `src/compare.py:paired_metrics` (`int_centers` and pairwise loop); plan Context item 1 and Step 11; comparator pre-spec §2.4 (coverage alongside accuracy).
WANTED: Specify a paired analysis over the common *reference-admissible* window universe, partitioned into both-pass, production-only, guard-only, and neither. Report errors/MAE/RMSE (with n and individual rows) for guard-only windows, common-window accuracy for both-pass windows, and each policy's overall coverage plus own-covered-set accuracy. `paired_metrics` may remain a supplementary common-finite report, but the plan must not present it as the incremental-coverage answer. Assert that all reference fields are identical across configs for a given `k`.
REVERSIBILITY: Cheap in the plan; decisive after a promotion decision because the current report can literally omit every extra window.
ESCALATE: none

RESPONSE: Verified directly against `src/compare.py:paired_metrics` before responding: `int_centers` is built from `common_keys` filtered by `_finite(...)` on both `radar_hr` and `masimo_pr` in **every** condition, and `_rate()` in `as_window_estimate` forces `hr_bpm=NaN` whenever `hr_valid=False` — so a window covered only under `guard_v1` and not under `production` is excluded from `int_centers`, confirming the finding exactly as stated. AGREE, applied: revision 2 replaces reliance on the `paired_metrics` intersection with the incremental-coverage partition described in the new Step 11 — `both_pass` / `<config>_only` / `neither` over the reference-admissible universe, each bucket's own MAE/RMSE/bias reported, plus the bit-identical-reference-across-configs assertion. `paired_metrics`/`coverage_table` is retained only as an explicitly-captioned supplementary common-finite report (Step 11, last sentence).

### OSR-03 [Blocking] — the proposed pinned locks are not the locks behind the quoted coverage gain
ISSUE: The CLI uses the original capture directories and Step 4 defaults to their `run_metadata.json.locked_bin`. Those values are 23/20/21 for massimo1/massimo2/sweep. The quoted production coverages 17.6%/54.9%/23.2% are the 2026-07-26 replay generation's 9/51, 28/51, 35/151 results at locks 27/26/26; the pinned guard results use those replay locks (the extra free-warmup massimo1 run at bin 25 is separate). Thus the plan does not reproduce the methodology it says it is matching, and its Verification 2 anchor is impossible on its stated default. Step 4 also gives recorded metadata precedence over `--rerun-warmup`, contradicting the CLI promise that the flag reruns selection for existing captures.
AUTHORITY: Plan CLI, Step 4, Verification 2/3; `HANDOFF.md` §3.1 and §5; read-only `run_metadata.json` and `live_estimates.csv` in the three original captures and the three `20260726_*_replay_unknown` directories.
WANTED: Name and provenance-bind the lock source used for the guard experiment (including the source replay metadata/hash), rather than calling the original capture lock the production comparison lock. Define unambiguous precedence and mutual exclusions: manual override, per-config rerun, or a pinned lock manifest; `--rerun-warmup` must actually override recorded locks. Fail if a requested reproduction's capture hash/config/lock tuple differs from the approved tuple.
REVERSIBILITY: Cheap now; otherwise the new "accuracy" result is not about the measured coverage gain cited as its motivation.
ESCALATE: none

RESPONSE: Independently re-verified before responding, not taken on trust: read
`run_metadata.json` directly in all 5 relevant directories. Original captures:
massimo1=23, massimo2=20, sweep=21. The three `20260726_*_replay_unknown` directories:
27, 26, 26 — confirming the finding's numbers exactly. AGREE, applied: revision 2's CLI
replaces the implicit "capture's own recorded lock" default with a **required**
`--pinned-lock-source <capture>=<dir|int>` argument that must name an explicit directory
(or literal bin), with its path + SHA-256 recorded as `lock_provenance` — never silently
assumed equal to whatever directory is passed to `--captures`. Verification §3's example
invocation now points `--pinned-lock-source` at the three `20260726_*` replay directories
explicitly, with the 23/20/21-vs-27/26/26 discrepancy called out inline so it can never
be silently reintroduced. Did not implement a hardcoded `approved_replays`-style
hash-pinning gate (as `scripts/diagnose_bin_drift_config.yaml` has) — provenance is
recorded and auditable in `summary.json`, but not hard-gated against a pre-approved
tuple; flagging this as a lighter-weight resolution than WANTED's last sentence in case
Codex wants the stricter gate.

### OSR-04 [Blocking] — pinned-bin and rerun-warmup runs answer different promotion questions
ISSUE: A common pinned lock is the correct conditional estimand for "what did changing only `eca_mode` do at the bin used in the measured coverage experiment." Per-config rerun warmup is the operational estimand for "what will the production system do after promotion," because `run_warmup_selection` itself consumes `hr_valid`/BR evidence and the mode can change the selected bin (observed massimo1: bin 25 versus production 27). Making the second estimand an unlabeled opt-in leaves the promotion decision based on only the conditional effect and can miss a beneficial or harmful lock change. Conversely, silently replacing the pinned comparison with free warmup would destroy causal isolation.
AUTHORITY: `src/warmup_select.py:run_warmup_selection`; `HANDOFF.md` §5; plan CLI/Step 4/Verification 3; CLAUDE.md §4.
WANTED: Label the two estimands explicitly and make the user choose whether promotion requires the pinned result, the operational result, or both side by side. If both are required, keep their reports separate and never pool them. Confirm in the plan that both selection paths are radar-only and that no Masimo value is available to bin selection.
REVERSIBILITY: Cheap before implementation; expensive after a one-mode report has driven production configuration.
ESCALATE: requires user decision

RESPONSE: Escalated to the user as asked. Decision (2026-07-28): compute **both**
estimands, always, clearly labeled, never pooled. AGREE and applied: revision 2's CLI
makes `--estimands both` the default; Step 4 computes `pinned` and `rerun` separately for
every capture × config; the output path includes `<estimand>` as its own directory level
so the two are never merged; Verification §4 tells the reader to compare them side by
side and treat a lock shift under rerun as its own finding. Confirmed, by reading
`src/warmup_select.py` in full, that `run_warmup_selection` and everything it calls
(`derive_candidate_bins`, `range_energy_by_bin`) import nothing from `src/masimo.py` and
take no Masimo argument — both estimands are radar-only, stated explicitly in revision 2
Step 4.

### OSR-05 [Blocking] — literal `fs=20.0` bypasses rather than enforces capture-rate compatibility
ISSUE: `_require_frozen_grid` rejects a non-20 argument, but the plan always passes literal `20.0`, so it will succeed even if the capture's recorded frame rate is different. `read_adc_bin` does not infer timing from the bytes and does not use `ChirpConfig.frame_rate_hz` to validate them. The plan checks only `iq_swap`, not the active config against the capture's recorded geometry; a wrong but byte-count-compatible combination of ADC samples/RX/chirps can silently reshape the same file. The existing diagnostic already validates active versus recorded `num_adc_samples`, `num_rx`, `num_chirps_per_frame`, `range_resolution_m`, `iq_swap`, frame rate, and `1000/period_ms`.
AUTHORITY: `src/m4/window_grid.py:_require_frozen_grid`; `src/radar_io.py:read_adc_bin`; `scripts/diagnose_bin_drift.py:validate_decode_geometry`; plan Steps 2/3/6; CLAUDE.md §3.1.
WANTED: Before decode or grid construction, require a recorded config snapshot; cross-check the full decode geometry and frame rate against every active scoring config; cross-check recorded `session.frame_rate_hz` against recorded `hw_frame.period_ms`; then pass the validated recorded rate to `build_window_grid` and let it reject anything other than exact 20 Hz/600 frames. Add mismatch tests beyond `iq_swap`, including recorded rate 19.99/25 and a byte-compatible geometry mismatch.
REVERSIBILITY: Cheap now; a wrong reshape or time base invalidates every window and reference span while still yielding plausible numbers.
ESCALATE: none

RESPONSE: AGREE. Applied: revision 2 Step 2 now cross-checks the full geometry
(`num_adc_samples`, `num_rx`, `num_chirps_per_frame`, `range_resolution_m`, `iq_swap`,
`session.frame_rate_hz`) against the capture's recorded config, plus
`session.frame_rate_hz` against `1000/hw_frame.period_ms`, raising on any mismatch —
reusing `scripts/diagnose_bin_drift.py`'s existing geometry validation if its signature
turns out to be capture-agnostic, else replicating the identical check list (noted
explicitly in the plan so the implementer doesn't skip it). `build_window_grid`'s own
`_require_frozen_grid` check is kept as a second, independent guard, not a substitute.
New tests section now names the frame-rate (19.99/25 Hz) and byte-compatible-swap
mismatch cases explicitly, in addition to `iq_swap`.

### OSR-06 [Blocking] — the HR waterfall silently resolves a frozen-spec ambiguity and drops required PI diagnostics
ISSUE: HR §2.4 requires total, excluded-by-PI, excluded-by-coverage, excluded-by-non-stationarity, radar-NaN, and final n. The plan emits no excluded-by-PI category and its CSV omits the separately required per-window `n_finite_pr` and PI-qualified count, retaining only `n_usable`. The §2.1 clarification deliberately requires finite-PR and PI-qualified counts separately so missing data and low perfusion remain distinguishable. At the same time, the frozen text does not define a mutually-exclusive priority between a sample-level PI gate, coverage failure, and radar-NaN. The plan invents reference-first priority without labeling it and then calls the result "the" spec waterfall. It also leaves sensitivity-fraction denominators unspecified.
AUTHORITY: `notes/comparator_prespec.md` §2.1 clarification and §2.4; `notes/comparator_prespec_br.md` §2.4; plan Steps 8/9 and tests.
WANTED: Preserve and output `n_total`, `n_finite_pr`, `n_pi_qualified`, and `n_usable` per HR window. Do not silently choose what "excluded-by-PI" means or whether exclusion counts are sequential versus orthogonal: obtain a binding interpretation of the frozen text, then state the exact category predicates/priority and sensitivity denominators in the plan. Independently report raw radar-valid/radar-NaN and outcome-class counts over all windows so reference-first accounting cannot hide the guard coverage effect. Tests must assert category membership, not only that some buckets sum to total.
REVERSIBILITY: Cheap now; category semantics become entrenched once later M8/M9 reports inherit them.
ESCALATE: frozen content

RESPONSE: PARTIAL AGREE, resolved by redesign rather than by a frozen-spec
interpretation request. Re-read `comparator_prespec.md` §2.1's "Resolved — one set, used
everywhere" clarification directly before responding: it already answers "how does PI
interact with coverage" for the *reference side alone* (PI is folded into the single
`n_usable` denominator, with `n_finite_pr`/`n_pi_qualified` reported as diagnostics, not
separate exclusion buckets) — so I did not escalate that half. What the frozen text
genuinely does not address is the *cross-domain* question (reference-side gate vs.
radar-NaN priority), and rather than ask for a ruling on an ambiguity the spec was never
written to resolve, revision 2 sidesteps the need for one: Step 10 now reports two
independent marginals (a reference marginal, entirely radar/config-independent, and a
radar marginal, entirely reference-independent, including the outcome-class breakdown
over **all** windows so a config's coverage effect can never be hidden) plus a joint 2×2
cross-tab — this is a strict superset of information versus a single priority-ordered
waterfall and asserts no implicit priority at all. Separately AGREE and applied: per-
window `n_total`/`n_finite_pr`/`n_pi_qualified`/`n_usable` are now all named explicitly
in `hr_reference`'s docstring and the `window_scores.csv` schema (Step 8), not collapsed
to `n_usable` alone. Sensitivity denominators are now stated explicitly (computed among
windows passing the coverage/availability gate, matching
`derive_br_comparator_evidence.py`'s convention). If Codex still wants a formal
frozen-spec amendment for the cross-domain priority question rather than accepting the
marginals-plus-joint redesign as sufficient, please reopen with that specifically.

### OSR-07 [Blocking] — no intermediate evidence is persisted for any estimate
ISSUE: The output design keeps only scalar CSV fields and a summary. `run_window_dsp` returns the exact evidence CLAUDE.md requires (`phase_raw`, `phase_clean`, spectra, candidate arrays, peak decisions, ECA diagnostics), but the plan drops it. A wrong 54-versus-80 window—and especially a newly covered guard-only window—would not be diagnosable from the proposed artifacts.
AUTHORITY: CLAUDE.md §5.4 ("Every estimate must leave evidence"); `src/window_pipeline.py:run_window_dsp`; plan Steps 7–10.
WANTED: Add a per-window, provenance-linked intermediate dump (sharded/compressed if necessary) for every capture/config/window, including the native DSP payload needed to reconstruct the decision and its exact frame/epoch bounds. Keep invalid estimates too. Define a stable index from each CSV row to its evidence record and test that every row has one.
REVERSIBILITY: Cheap now; without it the first surprising MAE or extra-coverage error forces an untraceable rerun and cannot audit the decision-producing artifact.
ESCALATE: none

RESPONSE: AGREE. Applied: revision 2 adds Step 9, a per-capture/config/estimand
`evidence.npz` holding stacked per-window arrays (`k`, validity flags, native
`hr_raw`/`br_bpm`, `candidate_rejection_code`, `accepted_candidate_rank`, `f_r_hz`,
`phase_clean`), covering invalid estimates too, with `window_scores.csv`'s `k` as the
stable join key — named explicitly in the New tests list as something to verify has an
entry for every row.

### OSR-08 [Blocking] — result provenance omits inputs and conflates acquisition state with scoring state
ISSUE: The result depends on `run_metadata.json` for origin, lock, I/Q, geometry, and capture config, yet Step 10 hashes only ADC and Masimo inputs. It reads capture `git_commit`/`git_dirty` but never separately records the scorer's current commit/dirty state, so the artifact cannot tell which code produced the number versus which code captured the bytes. There is no clean-tree guard or explicit non-reproducible disposition for scoring on dirty code. A config hash alone also does not record the config file path/hash or the exact comparator/script sources when dirty.
AUTHORITY: CLAUDE.md §3.1/§3.3/§3.5; plan Steps 1/10; the existing captures' metadata is itself `git_dirty: true`.
WANTED: Hash `run_metadata.json` and every consumed file. Record acquisition commit/dirty separately from scoring commit/dirty, scoring command, parsed-config hash plus source path/SHA-256, comparator/scorer source identity, environment identity, and deterministic/no-RNG seed disposition. Fail on a dirty scoring tree by default or require an explicit exploratory override that stamps `reproducible: false`; never let capture dirty state stand in for scorer dirty state.
REVERSIBILITY: Cheap now; impossible to reconstruct later which uncommitted code or mutable metadata produced a promotion-driving number.
ESCALATE: none

RESPONSE: AGREE. Applied: revision 2 Step 13 now lists `run_metadata.json`'s own
SHA-256, capture-time `git_commit`/`git_dirty` recorded separately from the scorer's own
current `git_commit`/`git_dirty` (via `git rev-parse HEAD`/`git status --porcelain` at
scoring time), the config file's path + SHA-256 alongside `run_config_hash(cfg)`,
python/numpy/scipy versions, and an explicit determinism/no-RNG statement. The CLI gains
`--allow-dirty`, reusing (not inventing) the exact `require_clean_tree`/`--allow-dirty`
pattern already shipped in `scripts/diagnose_bin_drift_config.yaml`: default refuses on a
dirty tree, the flag overrides and stamps `"reproducible": false`.

### OSR-09 [Blocking] — the imported outcome classifier will raise on ordinary `f_r_hz=None` windows
ISSUE: `run_window_dsp` deliberately returns `f_r_hz=None` when BR is invalid. The planned direct call passes that value into `classify_window_outcome`; its `_f_r_in_gate` implementation calls `np.isfinite(f_r_hz)`, which does not accept `None`. Existing diagnostic callers normalize stored missing values to `float("nan")`, so this mismatch is not covered by that module's tests. Gate-not-run is a major observed outcome, so the scoring loop can abort on common windows before producing any comparator result.
AUTHORITY: `src/window_pipeline.py:run_window_dsp`; `scripts/diagnose_bin_drift.py:_f_r_in_gate` and `classify_window_outcome`; plan Step 7.
WANTED: Specify an adapter at the call site that maps `None` to `float("nan")` before classification (without changing its scientific meaning), passes exact `accepted_candidate_rank` and `candidate_rejection_code`, and adds a score-offline test for the all-`-1`/`f_r_hz=None` gate-not-run case. Do not reimplement the classifier.
REVERSIBILITY: Cheap now; otherwise the first session with invalid respiration can terminate the whole run.
ESCALATE: none

RESPONSE: Independently reproduced before responding: ran `np.isfinite(None)` in the
project's `radar-vitals` conda env and confirmed it raises
`TypeError: ufunc 'isfinite' not supported for the input types...`, exactly as claimed.
AGREE, applied: revision 2 Step 7 now states the `None → float("nan")` mapping explicitly
at the call site, and names the exact fields to pass
(`dsp["hr_result"]["accepted_candidate_rank"]`, `dsp["hr_result"]["candidate_rejection_code"]`)
rather than any summary-only field. New tests section names the all-`-1`/`f_r_hz=None`
gate-not-run case explicitly. No change to `classify_window_outcome` itself.

### OSR-10 [Blocking] — the frozen HR PI threshold is exposed as a tunable comparator argument
ISSUE: The proposed `hr_reference(..., min_pi: float = 0.5)` API permits callers to produce a result with a different PI threshold while still using the same "frozen comparator" function and artifact labels. The plan correctly traces 0.5/24/5.0 and BR's 24/2.0 to the specs; none is a runtime tuning knob. Exposing only PI as adjustable is especially dangerous for the exact low-quality-reference feedback CLAUDE.md §4 forbids.
AUTHORITY: `notes/comparator_prespec.md` §2.1/§2.2; CLAUDE.md §4; plan New code §1.
WANTED: Make every primary gate constant non-configurable in this implementation (named constants citing the frozen sections), or reject any supplied value other than the exact frozen value. Keep sensitivity thresholds fixed to their specified sets and label them sensitivity-only. Add tests that a caller cannot silently alter a primary threshold.
REVERSIBILITY: Cheap now; permanent if a tuned threshold can emit an apparently frozen score.
ESCALATE: none

RESPONSE: AGREE. Applied: revision 2's `hr_reference`/`br_reference` signatures drop
`min_pi` (and every other primary-gate value) as a parameter entirely — they now take
only `(df, epoch_start, epoch_end)`, with `_HR_MIN_PI=0.5` etc. as internal module
constants cited to their spec section in the docstring, no caller override path. New
tests section adds an explicit assertion that no keyword can move a primary threshold.

### OSR-11 [Blocking] — Masimo auto-discovery assumes a file layout none of the target captures has
ISSUE: The plan says the Masimo input is auto-discovered as the one `*.csv` in each capture directory. Each of the three named directories has two CSVs: `live_estimates.csv` and `demo_massimo*.csv`/`demo_sweep.csv`. A glob-first implementation is order-dependent and may feed the radar output CSV to `load_masimo`; a require-one implementation rejects every intended run. A single unkeyed `--masimo-csv` override is also undefined when `--captures` contains several directories.
AUTHORITY: Plan reuse list and CLI; read-only contents of the three target capture directories; CLAUDE.md §3.1 and §9.
WANTED: Define deterministic per-capture resolution: either an explicit capture-to-Masimo mapping, or header-based discovery that requires exactly one CSV with all canonical Masimo source columns and excludes `live_estimates.csv`, failing on zero/multiple matches. Record the resolved path and hash. Define multi-capture override syntax and test the actual two-CSV directory shape.
REVERSIBILITY: Cheap now; otherwise the documented verification command cannot run reliably.
ESCALATE: none

RESPONSE: Verified directly by listing the three target capture directories — each does
contain both `live_estimates.csv` and its `demo_*.csv`, confirming the two-CSV shape.
AGREE, applied: revision 2 adds a dedicated "Masimo CSV auto-discovery" subsection —
discovery now attempts `load_masimo(path)` on every `*.csv` in the directory, accepting a
candidate only if it parses successfully **and** is not named `live_estimates.csv`,
requiring exactly one match and raising with the full candidate list otherwise.
`--masimo-csv <capture_dir>=<path>` is now explicitly keyed by capture directory (not a
single unkeyed override) so it works with multiple `--captures`. New tests section names
the real two-CSV directory shape explicitly.

### OSR-12 [Blocking] — the scoring loop bypasses the existing validity adapter
ISSUE: The requested reuse surface includes `as_window_estimate`, but the plan reads native dict fields directly. That bypasses the enforced invariant that invalid rates are NaN and valid rates are finite. This is already observable for BR: `run_window_dsp` can return a finite diagnostic `br_bpm` while `br_valid` is false, and the planned CSV writes that value under the unqualified `br_bpm` name. It also weakens the intended estimator-agnostic boundary for later M8/M9 reuse.
AUTHORITY: `src/window_pipeline.py:as_window_estimate` and `WindowEstimate`; plan Steps 7/8; CLAUDE.md §4/§5.4.
WANTED: Normalize every native result through `as_window_estimate` and score/write the normalized `hr_bpm`/`br_bpm` plus validity flags. Retain the native dict only in the diagnostic evidence dump, with any invalid raw candidate explicitly named non-scorable. Use the adapter's current `hr_valid == ahet_verified` mapping for ECA/AHET; do not generalize AHET terminology to future non-AHET estimators.
REVERSIBILITY: Cheap now; otherwise invalid diagnostic rates can leak into apparently scored columns and future estimators bypass a designed safety boundary.
ESCALATE: none

RESPONSE: Verified directly against `src/window_pipeline.py`: `"br_bpm":
float(br_result.get("radar_rr_bpm", np.nan))` is set unconditionally in `run_window_dsp`'s
return dict, independent of `resp_valid`/`br_valid` — confirming the finding. AGREE,
applied: revision 2 Step 7 now normalizes every `dsp` result via `as_window_estimate`
immediately, and all scoring (Step 8's CSV columns, Step 10's aggregation, Step 11's
partition) reads `est.hr_bpm`/`est.hr_valid`/`est.br_bpm`/`est.br_valid`, never the native
dict directly. The native dict is retained only in the new `evidence.npz` (OSR-07),
explicitly labeled as containing possibly-non-scorable diagnostic values. Kept the
adapter's own `hr_valid`/`br_valid` naming throughout (not `ahet_verified`) per the WANTED
note about not generalizing AHET terminology.

### OSR-13 [Blocking] — "eca_mode is the only variable" is asserted but not enforced
ISSUE: Pinned-bin causal interpretation requires the two full configs to be identical except for the intended treatment field. The current files happen to differ only at `heart.eca_mode`, but the script accepts arbitrary full configs and merely hashes them. A later unrelated config edit can silently turn the guard report into a multi-factor comparison while retaining the plan's causal wording.
AUTHORITY: Plan Context, CLI default rationale, and Verification 3; `experiments/exp_eca_modes/config_guard_v1.yaml` ("only intended difference"); CLAUDE.md §3.1/§4.
WANTED: Add an explicit ECA-isolation comparison mode that computes and records a field-level config diff and refuses any difference outside the predeclared treatment field(s). General multi-config/M8/M9 use may allow broader diffs, but must not inherit "only eca_mode" or promotion language. Test an unexpected second-field difference.
REVERSIBILITY: Cheap now; after promotion an unnoticed second factor makes the claimed mechanism non-identifiable.
ESCALATE: none

RESPONSE: AGREE. Applied: revision 2 adds `--isolate-fields <dotted.path> [...]` to the
CLI and Step 12 — a recursive field-level diff of the loaded configs that raises unless
every differing leaf is inside the declared allowlist; only then may the output use
causal ("only variable") framing. Verification §3's example invocation passes
`--isolate-fields heart.eca_mode` explicitly for the guard_v1 comparison. Without the
flag, a multi-config comparison is still produced but captioned non-causal. New tests
section names an unexpected-second-difference rejection test explicitly.

### OSR-14 [Should-fix] — the proposed coverage sanity check compares different window populations
ISSUE: Verification 2 expects the non-overlapping frozen-grid `hr_valid` fraction to be close to 17.6%/54.9%/23.2%. Those values are from 30 s windows emitted every 3 s (51/51/151 sliding windows); the new grid samples only consecutive disjoint windows (roughly 6/6/16). The latter is a small phase-specific subset of the former, so closeness is neither required nor a reliable slicing check. The generation/lock discrepancy in OSR-03 makes the proposed comparison still less meaningful.
AUTHORITY: `scripts/live_demo.py` ring-buffer/hop logic; `src/m4/window_grid.py`; `HANDOFF.md` §3.1; plan Verification 2.
WANTED: Remove the numerical-closeness acceptance criterion. Replace it with exact structural checks: array slice shape/bounds; `np.stack(list(ndarray_slice))` equivalence to the slice; and full-precision equality between deque and ndarray calls on the same frames/config/lock. Compare against prior replay rows only when matching exact frame endpoints, raw hash, lock, config, commit/source state, and estimator generation can be proved.
REVERSIBILITY: Cheap; a false alarm here wastes investigation, while a coincidental pass provides false confidence.
ESCALATE: none

RESPONSE: AGREE. Applied: revision 2 Verification §2 removes the closeness criterion
entirely and replaces it with the three exact structural checks named in WANTED (slice
shape/bounds, `np.stack` bit-identity to the slice, deque-vs-ndarray bit-identical
`run_window_dsp` output on the same frames/config/lock). New tests section reflects the
same replacement.

### OSR-15 [Should-fix] — the tests can pass with wrong bucket labels and omit an HR strict-boundary case
ISSUE: "Waterfall sums to total" does not exercise priority or mutual exclusivity: a bug can put every excluded window in the wrong bucket and still sum perfectly. The tests mention HR's 5.1 exclusion but not the strict HR boundary at exactly 5.0 (retained); BR covers exactly 2.0. They also do not require mixed cases such as coverage-fail+radar-NaN and stationarity-fail+radar-NaN, nor zero-final-n behavior. The I/Q test covers only the narrow top-level mismatch, not the recorded geometry/rate checks needed by OSR-05.
AUTHORITY: Frozen HR §2.2 and BR §2.2/§2.3; plan New tests; proposed waterfall Step 9.
WANTED: Add table-driven exact predicates and expected buckets for: 23 versus 24 usable, PI exactly 0.5, HR spread exactly 5.0 versus just above, BR spread exactly 2.0 versus just above, each overlapping reference/radar failure combination, and a valid final row. Assert every bucket count and orthogonal diagnostic count, not only the sum. Define no-final-window output as `n=0` with metrics unavailable/null and a loud status (never fabricated zero and never non-standard JSON NaN), and test it. Extend acquisition mismatch tests per OSR-05.
REVERSIBILITY: Cheap before code; boundary/accounting defects are hard to discover from sparse real sessions.
ESCALATE: none

RESPONSE: AGREE. Applied: revision 2's `tests/test_comparator.py` list now names the
exact HR 5.0-vs-5.000001 and BR 2.0-vs-2.000001 boundary pairs and the `pi` exactly `0.5`
case; `tests/test_score_offline.py` now names bucket-membership assertions across all 4
joint cells (not only sums), a zero-`final_n` case with null/absent metrics and a loud
status (never a fabricated `0.0`), and the extended geometry/rate mismatch tests carried
over from OSR-05.

### OSR-16 [Should-fix] — BR is called "in full" while mandatory paced reporting is excluded
ISSUE: The plan says BR is implemented "in full," then explicitly omits the paced metronome comparison and natural/paced reporting required by BR §2.5/§2.6. The target inputs include paced16 and sweep, so the script would emit a BR MAE for paced data under an incomplete reporting contract. The HR guard decision does not depend on BR, so this is avoidable scope risk rather than a hidden dependency.
AUTHORITY: `notes/comparator_prespec_br.md` §2.4–§2.6; plan Scope paragraphs and Explicitly out of scope list.
WANTED: The user must choose one coherent scope: defer BR output entirely from this HR-first build, or implement the required paced target-concordance and natural/paced labeling now. Do not emit primary-looking paced BR agreement while describing the BR spec as fully implemented if its mandated companion report is absent.
REVERSIBILITY: Cheap now; incomplete BR numbers become easy to cite once written beside the HR summary.
ESCALATE: requires user decision

RESPONSE: Escalated to the user as asked. Decision (2026-07-28): implement BR's mandated
§2.5/§2.6 companion reporting now, in this same build, so BR really is "in full." Applied:
revision 2 adds `br_metronome_concordance` to `src/comparator.py` (§2.5, reported
additively, never blended into RRp agreement), a `--paced-schedule <capture>=<bpm|path>`
CLI argument distinguishing massimo2's constant 16 bpm from sweep's time-keyed step
schedule (read from `notes/protocol.md`, not invented) and leaving natural captures
(massimo1, live_test1) BR-natural-only per §2.6, and the corresponding
`br_commanded_rate_bpm`/`br_metronome_error_bpm` columns in `window_scores.csv`.
Verification §3's example invocation now includes `--paced-schedule` for massimo2 and
sweep.
## END OF DEBATE COMMENTS
