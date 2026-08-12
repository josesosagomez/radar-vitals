"""Physical protocol facts, transcribed from `notes/protocol.md`.

**Why this module exists.** The same protocol numbers were independently defined in
:mod:`src.m2.acquisition_metadata` (acquisition admission) and :mod:`src.m4.manifest` (offline
scoring admissibility). A protocol change applied to one and not the other would let acquisition and
scoring disagree about which sessions are admissible, with nothing failing — and the two modules even
spell some of them differently (``SETTLE_MAX_PR_SPREAD_BPM`` vs ``SETTLE_SPREAD_MAX_BPM``), so it was
easy to edit one and believe both were done. Owner decision 2026-08-12 (D-OWN-8,
`plans/m2_sidecar_scaffold.md` §2).

This does not cut across the deliberate M2/M4 package separation: :mod:`src.m2` scopes that split to
*schema semantics* — v2 historical versus v3 prospective — not to physical protocol facts.

**Scope.** Only facts that are (a) stated in `notes/protocol.md` and (b) needed by more than one
module. A value defined in exactly one place cannot drift, so single-user values stay where they are:
``CHIRPS_PER_FRAME``, ``INTENDED_DURATION_S``, the recovery exertion-stop range, the paced pacing and
BR-stability minimums and the controlled vocabularies all remain in
:mod:`src.m2.acquisition_metadata`.

**Consuming modules alias these names rather than replacing their own.** Every existing import keeps
working and no call site changes. The goal is one *editable value* per protocol fact, not one name.

**Two things deliberately NOT in this module.**

1. ``FRAME_RATE_HZ`` (20.0), which appears in :mod:`src.m2.acquisition_metadata`,
   :mod:`src.m2.time_sensitivity` and :mod:`src.m4.window_grid`. M2's is a capture parameter the
   operator attests to having configured; ``src/m4/window_grid.py`` marks its copy FROZEN by
   `notes/analysis_prespec.md` §7, where a change is an amendment to a frozen analysis decision, not
   a config edit. Merging them would couple an acquisition attestation to a frozen analysis decision.
   They must nevertheless agree — a mismatch is a *silent* scientific error, windows of 20 s of data
   labelled 30 s — so `tests/test_m2_acquisition_metadata.py` asserts agreement instead.
2. Anything merely equal by coincidence. ``src/comparator.py`` defines
   ``_HR_STATIONARITY_MAX_BPM = 5.0``, numerically identical to :data:`SETTLE_SPREAD_MAX_BPM` but an
   unrelated within-window HR quantity. Do not fold such values in here; equal numbers are not the
   same fact.
"""
from __future__ import annotations

#: `notes/protocol.md`: "seat the subject so the chest is **0.8-1.4 m** from the radar", and the
#: session checklist "must be within 0.8-1.4 m". Inclusive at both ends.
DISTANCE_MIN_M = 0.8
DISTANCE_MAX_M = 1.4

#: `notes/protocol.md` SETTLE CRITERION limbs 1-2, transcribed: PR spread <= 5 bpm (max - min) over a
#: continuous 60 s, and the last-20 s vs first-20 s PR difference <= 3 bpm. Both limbs are ``<=``, so
#: 5.0 and 3.0 exactly are PASSES; consuming comparisons are therefore strict ``>``.
SETTLE_SPREAD_MAX_BPM = 5.0
SETTLE_DRIFT_MAX_BPM = 3.0

#: `notes/protocol.md` SETTLE CRITERION limb 1: the continuous window over which spread and drift are
#: demonstrated. Enforced by exact equality at acquisition. :mod:`src.m4.manifest` deliberately keeps
#: no constant of its own for this — it receives spread/drift already reduced over the window and
#: cannot verify it — but documents the value, so it is defined here once.
SETTLE_EVIDENCE_WINDOW_S = 60.0

#: `notes/protocol.md` SETTLE CRITERION limb 3: total settle >= 120 s, natural and paced only
#: (diagnostic captures are not bound; recovery is exempt from the section entirely). Owner decision
#: 2026-08-12 based on operator judgement, **not derived from measurement** — the protocol says so
#: explicitly so the paper cannot inherit it as an empirical settling time. Must never be set below
#: :data:`SETTLE_EVIDENCE_WINDOW_S`; see :mod:`src.m2.acquisition_metadata` for why.
MIN_SETTLE_S = 120.0

#: `notes/protocol.md` session steps: the PC-to-phone offset must be within +/-1 s, re-checked at
#: session end for drift. Also `notes/analysis_prespec.md` §6 item 5 (NTP-synced, max +/-1 s).
MAX_CLOCK_OFFSET_S = 1.0

#: Commanded paced breathing rates, frozen by the M3R-31 rotation (12 -> 15 -> 18). The cohort
#: registry assigns one per subject; neither the operator nor any tool may substitute another.
PACED_RATES_BPM = (12, 15, 18)
