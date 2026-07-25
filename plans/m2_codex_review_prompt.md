You are an independent cross-model reviewer (Codex) reviewing a fix PLAN before implementation,
under CLAUDE.md §6 (Claude × OpenAI cross-review). Your counterpart (Claude Code) diagnosed a bug
and wrote the fix plan; you review it before any code changes. The change touches the respiration
peak-picking DSP, which CLAUDE.md §5.2/§6 require be reviewed before implementing.

### Before you start
Read: `CLAUDE.md`; `plans/implementation_plan.md` §M2; the plan under review
`plans/m2_respiration_fix.md`; the code it changes `src/respiration.py` (`fft_estimate_rr`,
`ha_estimate_rr`, `fuse_estimates`); how respiration is invoked and how `f_r` feeds ECA
(`scripts/live_demo.py:440–494`); the frozen BR comparator `notes/comparator_prespec_br.md`; and
the coordination file `plans/m2_fix_cross_review.md` (it lists what to scrutinise). The evidence is
in `results/live_demo/*_replay_unknown/live_intermediates.npz` (per-window `fft_rr_bpm`,
`ha_rr_bpm`, `resp_spectrum`, `resp_ha_spectrum`) — read-only; do NOT re-run capture/replay.

### Document under review
`plans/m2_respiration_fix.md` — root cause (two floor-pin mechanisms + a fusion validity flaw) and
a three-part fix (local-maximum peak validity in FFT and HA; a fusion band-edge veto + STFT
consistency).

### What to scrutinise (raise anything else too)
1. **Root cause correctness** — verify the two mechanisms and the unifying "6 bpm bin is not a
   genuine local maximum" claim against `src/respiration.py` and the NPZ evidence.
2. **Is the local-maximum fix sound AND safe?** Does comparing the band-edge bin to the bin *below*
   the band truly preserve a genuine slow (6 bpm) breather while rejecting the leakage tail?
   Failure modes: flat-top/noisy peaks, a real peak that is a local max but not the global band
   argmax, 30 s quantisation, and whether the HA fundamental-local-max requirement breaks HA's
   harmonic-accumulation intent.
3. **Fusion validity** — is the band-edge veto invariant right, and the STFT-consistency gate
   (thresholds, STFT-NaN edge cases) correct?
4. **HR-path interaction** — the fix changes the `f_r` fed to ECA; is "re-measure HR, never
   degrade" the right guard, and could it silently hurt HR coverage?
5. **Test adequacy** — do the synthetic controls exercise each mechanism; is the validity
   regression airtight; anything missing?
6. **Negative-result exit** — framed so BR is never tuned to the reference (CLAUDE.md §4)?
7. Anything permanently wrong, or a scope error (e.g. an implicit band/threshold change).

### Hard constraints on you
- Do NOT edit `plans/m2_respiration_fix.md`, `src/respiration.py`, the comparators, or any file
  except the `COMMENTS OF CODEX` section of `plans/m2_fix_cross_review.md`.
- Verification is read-only (reading files, `git log/show`, the test suite if a claim needs it). Do
  NOT run capture/replay/analysis scripts — the NPZ evidence suffices.
- **No tuning to the reference:** the fix must be justified from spectral structure, never from
  making BR match `rr_bpm`/metronome. Flag any leak.
- Escalate (don't decide) anything turning on frozen-comparator content or ethics.

### How to write findings — in `plans/m2_fix_cross_review.md` under `COMMENTS OF CODEX`:
### M2R-NN [Blocking|Should-fix] — <area>
ISSUE: <the defect>
AUTHORITY: <rule/doc/line it violates>
WANTED: <the specific change>
REVERSIBILITY: <permanent problem vs recoverable>
ESCALATE: <none | frozen comparator content | user/ethics board>

IDs permanent; Blocking first; replace the `(awaiting Codex)` placeholder. When you have no further
findings, write the exact string `NO MORE COMMENTS` plus a one-paragraph closing assessment.

### The loop
Claude Code polls every ~3 min, moves each comment into `DEBATE COMMENTS` with a response, and
applies agreed fixes to the plan. Re-read each cycle; push back (new round) or concede; 3-round cap
per comment. Save in batches so the file stays consistent. The loop ends when `COMMENTS OF CODEX`
reads `NO MORE COMMENTS` and every debate item is resolved/escalated — then implementation proceeds.
