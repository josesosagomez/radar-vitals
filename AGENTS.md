# Agent Instructions

All project rules for every AI agent (Claude Code, Codex, and any other harness)
live in **`CLAUDE.md`** at the repo root. Read it first and follow it exactly.

This file exists so Codex and other tools that look for `AGENTS.md` pick up the same
rules Claude Code uses. Do not maintain a separate copy of the rules here — keep
`CLAUDE.md` as the single source of truth and edit only that file.

Non-negotiables (full detail in `CLAUDE.md`):
1. Reproducibility is the bar: every number/figure traces to script + config + seed + data hash.
2. `data/raw/` is read-only. Research before code; plan before implement; verify in small steps.
3. Never fabricate results; never tune the radar to a low-quality Masimo segment.
4. Ground truth = the `Beats / min` (PR) column, aligned via the integer Unix-epoch `Timestamp`.
5. Every heart-rate estimate must dump intermediate signals for debugging.
6. Independent review rules live in `CLAUDE.md` §6; surface disagreements, don't hide them.
