# Cross-model review #1 — findings (REJECT)

Review of the settled-energy veto added to `scripts/live_demo.py:_run_warmup_selection`
(prompt: `notes/cross_review_warmup_veto_prompt.md`). Verdict: **REJECT**.

## Findings

1. **MAJOR: Energy-vetoed bins can still win and receive medium confidence**
   `scripts/live_demo.py` (~line 696)

   The threshold only removes the `+1000` HR bonus. A vetoed candidate can still receive
   roughly 300 points from breathing evidence, beat every energy-plausible candidate, and
   then be classified as medium confidence.

   Concrete scenario: a low-energy sidelobe has high-confidence valid BR, while the dominant
   body bin has medium BR. Their scores can be approximately 295 versus 145. The sidelobe
   wins despite failing the new physical plausibility test. Because medium-confidence
   selections do not trigger the low-confidence warning, that bin may remain fixed for the
   session without clearly exposing the veto.

   Resolve the intended policy explicitly:
   - If below-threshold bins are physically ineligible, exclude them from physiological
     scoring and select among plausible bins first, using vetoed bins only as a clearly
     marked low-confidence fallback.
   - If the threshold is only intended to suppress HR evidence, permit the win but cap a
     vetoed winner at low confidence, include `_energy_vetoed` in its reason, and add a
     regression test for this path.

2. **MAJOR: The global strongest-reflector threshold is not yet validated as a general chest
   prior**
   `scripts/live_demo.py` (~line 645), `scripts/live_demo_config.yaml` (~line 24)

   A chair, arm, nearby person, or other strong reflector inside the gate can become the
   reference and place the genuine chest more than 12 dB below it. The correct chest bin
   would then lose valid HR evidence.

   The current four-session replay supports this dataset but does not establish the config
   comment's broad claim that a seated chest cannot be 12 dB below the strongest reflector.
   Treat the threshold as experimental until it is tested across subjects, distances,
   postures, and competing reflectors. A body-cluster or local-mainlobe reference may
   eventually be safer than the global maximum.

3. **MINOR: Invalid settling intervals silently disable settling while evidence says
   otherwise**
   `scripts/live_demo.py` (~lines 646, 755)

   When `settle_skip >= frame_count`, the implementation uses the full cube but records the
   requested skip duration. A short warmup or misconfigured value therefore disables the
   transient protection while producing misleading evidence.

   Validate that the value is finite and within the available interval. Record both
   requested and actually applied frames, plus an explicit fallback indicator.

4. **MINOR: Empty candidate sets now fail at an opaque `max()` call**
   `scripts/live_demo.py` (~line 679)

   An explicit empty candidate list, or a gate with no valid ADC bins, produces `max() arg is
   an empty sequence`. Validate the candidate set earlier and emit a configuration-specific
   error.

5. **NOTE: Test coverage is good for the two observed regressions but misses the decisive
   path above**
   `tests/test_live_demo_warmup_helpers.py` (~line 231)

   The low-energy HR and early-transient tests would fail under the old behavior, which is
   useful. Add tests for:
   - a vetoed candidate winning through BR;
   - invalid or excessive settling intervals;
   - the exact `-12 dB` boundary;
   - a dominant non-chest reflector;
   - empty candidates and all-zero energy.

Keeping the DSP calculation on the full warmup while using the settled portion only for the
energy prior is defensible. Adjacent Hann-mainlobe bins surviving the threshold is also
reasonable because they represent nearly the same physical reflector.

## Verdict: REJECT

The implementation fixes the documented replay cases, and verification passed at review
time: 15 focused tests passed; full suite: 784 passed, 1 expected failure. However, finding 1
leaves the original failure class reachable through another scoring branch. Approve after the
vetoed-winner policy is made explicit, implemented consistently in confidence/reason
reporting, and covered by a regression test.
