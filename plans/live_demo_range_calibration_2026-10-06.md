# Live demo range calibration — 2026-10-06

## Evidence and scope

User authorizes correcting the live demo using the IWR1642 corner-reflector
calibration. First complete measured command gives range bias +0.0784329 m.
TI Visualizer checks reported 1.011 m at physical 1.001 m and 1.4855 m at
physical 1.503 m. These do not establish HR accuracy or continuous-range accuracy.
The live hardware profile differs from TI's calibration profile; the applied
Python correction still requires a physical check under the live profile.

TI SDK guide section 6.8 supports reuse of measured compensation commands:
https://dr-download.ti.com/software-development/software-development-kit-sdk/MD-PIrUeCYr3X/03.06.00.00-LTS/mmwave_sdk_user_guide.pdf#page=75.
Installed SDK aoaprocdsp.c subtracts rangeBias from detected-object range.
Context7 is unavailable in this session. Primary Python math documentation and
SciPy FFT docs are the API reference fallback; existing FFT calls are unchanged.

## Design

1. Add `src/range_coordinates.py` with small pure helpers for finite, signed
   `profile.range_bias_m` (default 0), positive finite range resolution, and
   `range_m = bin * range_resolution_m - range_bias_m`. Validate gate as two finite
   ordered distances and ADC count as a positive integer in candidate derivation.
   Validate calibration
   at launcher startup before countdown or hardware; do not clamp negative ranges.
2. Shared warmup derives integer candidates by ceil((gate_lo+bias)/resolution)
   to floor((gate_hi+bias)/resolution), clamped to ADC bin bounds. Explicit
   candidate lists remain explicit raw-bin overrides. Use corrected coordinates
   for distance tie-break, warnings, selected range and candidate evidence;
   include raw ranges and applied bias in evidence. Coordinate schema version 1:
   legacy `selected_range_m`/candidate `range_m` mean corrected physical coordinates;
   explicit `selected_raw_range_m`, `selected_corrected_range_m`, candidate raw and
   corrected fields remove ambiguity. Old artifacts with absent bias retain zero.
   Model string is `fft_bin_center_minus_bias_v1`; diagnostic reports show the model,
   raw coordinate and bias while continuing to display legacy `selected_range_m`.
   FFT/energy/scoring/HR/BR and
   sample data stay untouched.
3. Launcher uses the same helper for display and logs raw/corrected selected
   ranges, bias and calibration provenance in run metadata and intermediate
   records. Record selected ranges even with manifest/manual bin lock. Bias
   changes coordinates only and is applied once; do not send measured RX complex
   coefficients to firmware or multiply raw RX data in Python.
4. Add `scripts/live_demo_calibrated_config.yaml`, a full copy of the existing
   live config with optional bias and calibration provenance (date, board/SDK,
   original complete compensation command, reference and verification points,
   evidence SHA-256). Add a tracked JSON calibration record with the eight complete
   owner-reported lines, first-line selection, and two owner-reported TI checks;
   reference/hash this record in config and bind/validate it at startup for nonzero
   bias. Preserve default config and default CLI path. Launch the
   corrected demo explicitly with `--config scripts/live_demo_calibrated_config.yaml`.
   Nonzero bias is rejected for replay and prospective study mode before hardware,
   avoiding retroactive use on historical/other-board recordings. Prospective
   default zero-bias behavior remains unchanged. Also reject explicitly injected
   nonzero bias when prospective validation is called directly.
   Isolation is enforced by the launcher only; direct shared-helper/offline users
   can explicitly opt into a bias. Mark the calibrated config as demo-only and do
   not promote it into paper/offline production configs. Test canonical default
   remains zero/absent; do not claim universal offline-entry-point enforcement.
5. Update `scripts/verify_live_demo_artifacts.py` to use the same coordinate helper
   for physical gate checks, and cross-check calibrated metadata/warmup evidence.
   Preserve checks for old zero-bias artifacts. Display tests spy on actual update
   arguments rather than treating the headless no-op as GUI rendering evidence.
   Update `scripts/diagnose_live_run.py` to expose raw range, bias and model in its
   lock report, with legacy fallbacks; run its regression tests as well.
6. Add focused tests: corrected gate inclusivity and bounds, signed bias and
   invalid inputs, zero-bias regression, same raw cube/selected bin DSP for fixed
   candidates, corrected evidence, metadata/display consistency, study/replay
   rejection, saved config provenance. Use deterministic synthetic replay/live
   source mocks for smoke verification; do not launch hardware or read prospective
   reference data.

## Acceptance and review

- Independent plan review before implementation; incorporate disagreements here.
- Focused warmup, launcher/artifact, adapter and study-contract tests pass.
- Hardware-free headless integration exercises corrected display and persisted
  metadata/evidence; raw bytes and fixed-bin DSP remain unchanged.
- Exact software checks: `conda run --no-capture-output -n radar-vitals python -m
  pytest tests/test_live_demo_range_calibration.py tests/test_live_demo_warmup_helpers.py
  tests/test_m2_capture_artifacts.py tests/test_window_pipeline_adapter.py
  tests/test_diagnose_live_run.py -q --basetemp results/test_tmp/range_calibration_final
  -o cache_dir=results/test_tmp/pytest_cache_range` (create parent first; use a fresh
  basetemp path for subsequent invocations). Include artifact verifier
  regression tests in the new file. Failure tests assert no countdown, backend,
  output-directory creation or hardware source construction.
- Hardware acceptance is separate and owner-operated: with the actual live chirp
  and dedicated config, place the reflector at 1.001 m and a second in-gate distance
  near 1.30 m. Automatic selection must identify the reflector; record bin, raw and
  corrected range and full metadata/evidence. Aim for <=0.5 bin (0.0218 m) residual;
  <=1 bin is a preliminary demo sanity limit, justified by integer-bin reporting and
  transfer to a different chirp, not a research accuracy specification. Larger error
  or wrong reflector selection fails the check and must be investigated/logged.
  Software completion does not assert this hardware acceptance passed.
- Independent correctness review of coordinate/gate diff before completion.
- Log exact checks and physical verification limitation in HISTORY/HANDOFF.
- No commit/push unless requested; preserve concurrent/unrelated work.

## Review record

Independent reviewer initially rejected readiness: live-profile physical acceptance
was missing, and the artifact verifier still used raw coordinates. Added both as
explicit requirements. Also added tracked calibration evidence, coordinate schema,
input validation and display-spy/fail-before-side-effect tests per medium findings.
Revised plan accepted with minor changes: made exact test command include local
temporary/cache paths and diagnostic regressions as requested. Both incorporated.

Independent code review accepted after two fixes: artifact verification now binds
calibration provenance/content/hash, and preset-bin diagnostics include corrected
coordinates. Reviewer rerun: 60 calibration/diagnostic tests passed. Root focused
suite: 218 passed. A repeated-call DSP equivalence test initially used overly strict
equality; it now preserves exact ADC and phase and exact discrete decisions while
allowing observed sub-micro spectral-floor floating-point variation. Physical
acceptance under the actual live profile remains pending; no agent hardware run.
