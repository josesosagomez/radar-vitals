# Implementation prompt

Paste the following text into a new implementation thread when ready to approve implementation.

```text
Implement the saved replay-comparison plan in this new thread. This message approves its implementation, including creation of the isolated implementation branch/worktree and committing and pushing reviewed relevant milestones. Do the software work through verification and delivery; do not stop after producing another plan.

Repository:
C:/Users/josemsosag/Desktop/vitals_radar_3

Plan to read in full before making implementation changes:
C:/Users/josemsosag/Desktop/vitals_radar_3/results/motion_br_worktree/plans/replay_demo_compare_massimo3_2026-10-07.md

Read AGENTS.md, CLAUDE.md, and HANDOFF.md first, including the current handoff in results/motion_br_worktree. Inspect the actual Git status, branches, worktrees, source, and recording metadata. The original checkout's handoff is older; verify facts against Git and the feature checkout.

Protect existing work:
- Original working demo: vital_signs_own_v13 at 7f3dd2bfd405cdd73d5ae395347baeb991213acf.
- Existing feature branch: codex/live-motion-recovery-slow-br at dcda615d1a2984848882ed7da6641860331577b1.
- Existing feature checkout: C:/Users/josemsosag/Desktop/vitals_radar_3/results/motion_br_worktree.
- Create codex/replay-masimo-compare from dcda615d1a2984848882ed7da6641860331577b1 in C:/Users/josemsosag/Desktop/vitals_radar_3/results/replay_compare_worktree.
- The existing feature checkout now contains documentation-only uncommitted plan/prompt and HISTORY/HANDOFF changes. Preserve them. Copy the plan and prompt to the new worktree; Git does not copy uncommitted files when creating it.
- Preserve unrelated changes and untracked work in every checkout. Do not stash/reset other work, blanket-stage, force-push, or merge into either existing branch.
- Keep the plan's protected and calibration-fingerprinted production files byte-identical. Preserve the calibrated YAML's 10-second startup setting.
- Stage explicit relevant paths only, inspect each staged diff, and verify pushed remote SHAs.

Use these role agents with explicit file ownership and responsibilities:
1. workflow_manager: read-only milestone order, dependencies, isolation, and acceptance gates.
2. plan_reviewer: independently reconcile the saved plan with the actual repository and identify blocking inconsistencies before implementation. The prior review was READY WITH MINOR CHANGES, incorporated in the plan; verify rather than assume.
3. python_expert: production replay package, scripts/configuration, and the narrow development_data_root extension in src/reference_access.py, implemented in small milestones.
4. test_engineer: meaningful deterministic, production-decoder integration, concurrency, failure-path, artifact/tamper, UI, reference-isolation, numerical and regression tests.
5. research_agent: specific unresolved scientific questions and pinned-version API checks using primary sources.
6. code_reviewer: independent correctness/DSP/runtime/security/evidence review, especially the decoder adapter, phase/bin reuse, coverage boundaries, reference-root containment, calibration gate, asynchronous publication, and restart.
7. doc_generator: operator procedures, limitations, and verified acceptance report after implementation and tests pass.
The primary agent owns integration, sequencing, shared-file coordination, final tests, documentation truth, commits/pushes, and final verification. Tell every editing agent that it shares the codebase, must preserve others' edits, and must coordinate shared-file changes. Parallelize only independent work. Reviewers must not implement what they review. Resolve findings and record disagreements.

Deliver the complete separate recorded-session application for 20260728_224902_live_demo_massimo3:
- Recalculate radar during chronological playback through the production SampleSwap decoder with bounded memory.
- Show radar HR/BR, recorded 1 Hz Masimo PR/RRp, descriptive summaries over each radar window, coverage, and recovery/analysis status.
- Pause/resume with controller acknowledgment; restart into a preserved new pass/generation; speeds 0.5x, 1x, 2x, 4x; no arbitrary seeking.
- Accepted display coverage is independent HR, positive numeric BR, and their intersection, divided by all elapsed recording frames including initial accumulation/recovery.
- Use the plan's application-based one-hop leases. Do not backfill coverage to measurement windows. Held/red, preliminary/amber, quiet and invalid states do not count as accepted numeric coverage. Quiet has a separate status metric.
- Show separate Masimo availability/PR quality. Reference values must never affect radar processing or radar coverage.
- Keep approximate alignment visible; use integer Unix timestamps and the plan's historical origin. No interpolation across missing seconds, fitted offset, error panels, accuracy claims, or invented results.
- Baseline must work now. Implement advanced recovery/extended-BR replay support, but fail closed until compatible accepted physical calibration exists.
- Preserve the existing live replay prohibition by using the separate replay preflight/orchestrator.
- The owner permits only the plan's explicit legacy zero-loss validity assumption for this registered recording, with the CLI opt-in for advanced mode, strict byte conservation, visible provenance, and no physical/calibration-validation claims.
- Do not display numeric zero BR or promote unverified/fallback/no-ECA HR.
- Save evidence for every executed analysis, including failed, invalid, expired and superseded attempts. Keep cancelled-before-execution jobs as events. Implement independent artifact verification.

Read only the authorized registered development recording/reference through the guarded API. Do not access sealed prospective references, tune to Masimo, modify data/raw or any recording, promote replay into research validation, or operate radar hardware. Prepare owner-run physical calibration instructions instead of bypassing a gate.

Run focused checks after each milestone before proceeding. Use the pinned radar-vitals environment serially and the plan's complete non-real-data suite with isolated external basetemp. Keep sealed tests disabled. After automated acceptance and independent review, run baseline massimo3 replay, verify artifacts, exercise supported controls/speeds, report measured coverage honestly without tuning, and capture an actual application screenshot. Do not present the prior illustrative mockup as a result. Report recorded-playback performance separately from real-hardware acceptance.

Keep the plan current only for evidence-backed necessary revisions and record their reasons. Complete all software that does not require physical recordings; do not stop because physical calibration is pending. Leave advanced operation unavailable with an exact explanation and owner next steps.

Append HISTORY.md, then rewrite HANDOFF.md in the new implementation branch using verified facts. Document and commit reviewed milestones separately, push the new branch, verify remote SHAs, and do not merge.

Finish with a concise report containing:
- Implementation branch, checkout location, pushed commits and remote SHA.
- Implemented behavior and exact launch/verification commands.
- Test commands/results, independent review outcomes and resolved disagreements.
- Actual baseline replay artifact locations, measured coverage, performance observations and screenshot.
- Advanced-mode availability, remaining physical calibration/acceptance work.
- How to return to the unchanged working demo.

Proceed autonomously through routine implementation decisions. Ask only when missing information materially blocks correct work.
```
