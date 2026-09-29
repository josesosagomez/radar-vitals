"""Physical protocol facts shared by acquisition and scoring contracts.

These values are protocol decisions, not empirical estimates.  They live here because
duplicating them in M2 and M4 previously allowed acquisition and scoring to drift apart.
"""

from __future__ import annotations


DISTANCE_MIN_M = 0.8
DISTANCE_MAX_M = 1.4
SETTLE_SPREAD_MAX_BPM = 5.0
SETTLE_DRIFT_MAX_BPM = 3.0
SETTLE_EVIDENCE_WINDOW_S = 60.0

# Owner decision D-OWN-7 (2026-08-12), reaffirmed 2026-09-29.  Natural and
# paced captures must spend at least this long settling before recording.
MIN_SETTLE_S = 120.0

MAX_CLOCK_OFFSET_S = 1.0
PACED_RATES_BPM = (12, 15, 18)
