"""Unit tests for Step 3 qualified-bin selection ordering.

Tests the _rank_bins function in steps/step_3/select_chest_bin.py, specifically
the three-tier sort key: n_methods desc → resp_supported desc → combined_rank asc.

Run: pytest tests/test_step3_bin_selection.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from steps.step_3.select_chest_bin import _rank_bins


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_range_m(n: int, start: float = 0.3, step: float = 0.1) -> np.ndarray:
    return np.array([start + i * step for i in range(n)])


# ---------------------------------------------------------------------------
# Test 1 — 3/3 candidate beats any 2/3 candidate (even with worse combined rank)
# ---------------------------------------------------------------------------

def test_three_method_beats_two_method():
    """A bin supported by all three methods must win even when its combined score
    is worse than a 2-method candidate that appears in the top-N first."""
    # 9 bins, search covers all, top_n=5, penalty=6
    # bin0: energy=#1, phase=#1, resp=missing  → combined=8, n_methods=2 (energy+phase)
    # bin1: energy=#4, phase=#4, resp=#3       → combined=11, n_methods=3
    # Under old rule: bin0 wins (combined=8 < 11).
    # Under new rule: bin1 wins (n_methods=3 > 2).
    n = 9
    range_m = _make_range_m(n)

    # energy desc: 0(100), 2(90), 3(80), 1(70), 4(60), 5..8 below top-5
    mean_profile_db = np.array([100, 70, 90, 80, 60, 50, 40, 30, 20], dtype=float)

    # phase desc: 0(10), 2(9), 3(8), 1(7), 5(6), rest below top-5
    phase_std = np.array([10, 7, 9, 8, 5, 6, 4, 3, 2], dtype=float)

    # resp desc: 6(10), 7(9), 1(8), 8(7), 4(5), rest not in top-5
    # bin0 NOT in resp top-5; bin1 IS at rank 3
    resp_snr = np.array([0.1, 8, 0.2, 0.3, 5, 0.4, 10, 9, 7], dtype=float)

    entries, recommended_bin, agree_note = _rank_bins(
        mean_profile_db, phase_std, resp_snr, range_m,
        search_min_m=range_m[0], search_max_m=range_m[-1], top_n=5,
    )

    assert recommended_bin == 1, (
        f"Expected bin 1 (3/3 all-methods), got {recommended_bin}. "
        "3-method bin must beat 2-method bin regardless of combined score."
    )
    best_entry = next(e for e in entries if e["bin_idx"] == recommended_bin)
    assert best_entry["n_methods"] == 3

    # The old 2/2 winner (bin0) must still appear in entries
    bin0_entry = next((e for e in entries if e["bin_idx"] == 0), None)
    assert bin0_entry is not None
    assert bin0_entry["n_methods"] == 2


# ---------------------------------------------------------------------------
# Test 2 — among equal 2/3 candidates, resp-supported beats phase-only
# ---------------------------------------------------------------------------

def test_resp_supported_beats_phase_only():
    """When two 2-method candidates exist, the one with respiratory-band evidence
    must win even if the phase-only bin has a better combined score.

    This is the test2 session scenario:
      bin1 = energy+phase  (combined=6, no resp) — would win under old rule
      bin0 = energy+resp   (combined=7, resp supported) — must win under new rule
    """
    # 6 bins, top_n=3, penalty=4
    # energy top-3: [1, 0, 2]   → e_rank: {1:1, 0:2, 2:3}
    # phase top-3:  [1, 3, 4]   → pv_rank: {1:1, 3:2, 4:3}
    # resp  top-3:  [0, 3, 5]   → rs_rank: {0:1, 3:2, 5:3}
    #
    # Combined: bin1=6 (energy+phase), bin0=7 (energy+resp), bin3=8 (phase+resp)
    n = 6
    range_m = _make_range_m(n)

    mean_profile_db = np.array([85, 90, 80, 70, 60, 50], dtype=float)
    phase_std       = np.array([0.1, 5.0, 0.3, 4.0, 3.0, 0.2], dtype=float)
    resp_snr        = np.array([10.0, 0.5, 0.3, 7.0, 0.2, 5.0], dtype=float)

    entries, recommended_bin, agree_note = _rank_bins(
        mean_profile_db, phase_std, resp_snr, range_m,
        search_min_m=range_m[0], search_max_m=range_m[-1], top_n=3,
    )

    assert recommended_bin == 0, (
        f"Expected bin 0 (energy+resp), got {recommended_bin}. "
        "Resp-supported 2/2 must beat phase-only 2/2 even with worse combined score."
    )
    best_entry = next(e for e in entries if e["bin_idx"] == recommended_bin)
    assert best_entry["resp_rank"] is not None, "Winner must have respiratory-band rank"
    assert best_entry["n_methods"] == 2

    # Confirm the phase-only bin is still present in entries but not selected
    phaseonly_entry = next((e for e in entries if e["bin_idx"] == 1), None)
    assert phaseonly_entry is not None
    assert phaseonly_entry["resp_rank"] is None
    assert phaseonly_entry["rank"] < best_entry["rank"], (
        "Phase-only bin still has better combined rank — new rule overrides it"
    )


# ---------------------------------------------------------------------------
# Test 3 — within equal resp-support tier, existing combined-rank ordering wins
# ---------------------------------------------------------------------------

def test_combined_rank_tiebreaks_within_resp_tier():
    """When two resp-supported 2/2 bins exist, the one with the better combined
    score (lower rank) must win — the original ordering is preserved as tiebreaker."""
    # From test 2: bin0 (combined=7) and bin3 (combined=8) are both resp-supported 2/2.
    # bin0 should win over bin3 because its combined rank is lower.
    n = 6
    range_m = _make_range_m(n)

    mean_profile_db = np.array([85, 90, 80, 70, 60, 50], dtype=float)
    phase_std       = np.array([0.1, 5.0, 0.3, 4.0, 3.0, 0.2], dtype=float)
    resp_snr        = np.array([10.0, 0.5, 0.3, 7.0, 0.2, 5.0], dtype=float)

    entries, recommended_bin, _ = _rank_bins(
        mean_profile_db, phase_std, resp_snr, range_m,
        search_min_m=range_m[0], search_max_m=range_m[-1], top_n=3,
    )

    # Both bin0 and bin3 are resp-supported; bin0 must win (lower combined rank)
    resp_qualified = [e for e in entries if e["n_methods"] >= 2 and e["resp_rank"] is not None]
    assert len(resp_qualified) >= 2, "Need at least 2 resp-supported qualified bins for this test"

    bin0_entry = next(e for e in entries if e["bin_idx"] == 0)
    bin3_entry = next((e for e in entries if e["bin_idx"] == 3), None)
    assert bin3_entry is not None

    assert bin0_entry["resp_rank"] is not None
    assert bin3_entry["resp_rank"] is not None
    assert bin0_entry["rank"] < bin3_entry["rank"], "bin0 must have better combined rank"
    assert recommended_bin == 0, (
        f"Expected bin 0 (better combined rank within resp tier), got {recommended_bin}"
    )


# ---------------------------------------------------------------------------
# Test 4 — n_methods=1 resp-only candidate stays unqualified
# ---------------------------------------------------------------------------

def test_resp_only_bin_stays_unqualified():
    """A bin that appears only in the respiratory-band top-N (n_methods=1) must NOT
    be selected; it should remain below the n_methods >= 2 qualification threshold.
    The new resp-preference rule must not accidentally promote these bins."""
    # 5 bins, top_n=2, penalty=3
    # energy top-2: [0, 1]  → e_rank: {0:1, 1:2}
    # phase  top-2: [0, 2]  → pv_rank: {0:1, 2:2}
    # resp   top-2: [3, 4]  → rs_rank: {3:1, 4:2}
    #
    # bin0: energy+phase (n_methods=2) — only qualified candidate
    # bins 3,4: resp-only (n_methods=1) — must NOT be selected
    n = 5
    range_m = _make_range_m(n)

    # Energy desc: bin0 #1, bin1 #2
    mean_profile_db = np.array([90, 80, 70, 60, 50], dtype=float)
    # Phase desc: bin0 #1, bin2 #2 (others lower)
    phase_std = np.array([8, 2, 6, 1, 0.5], dtype=float)
    # Resp desc: bin3 #1, bin4 #2 (bin0 has very low resp_snr)
    resp_snr  = np.array([0.1, 0.2, 0.3, 10, 9], dtype=float)

    entries, recommended_bin, agree_note = _rank_bins(
        mean_profile_db, phase_std, resp_snr, range_m,
        search_min_m=range_m[0], search_max_m=range_m[-1], top_n=2,
    )

    assert recommended_bin == 0, (
        f"Expected bin 0 (only qualified 2-method candidate), got {recommended_bin}. "
        "Resp-only (n_methods=1) bins must not be promoted by the resp-preference rule."
    )
    best_entry = next(e for e in entries if e["bin_idx"] == recommended_bin)
    assert best_entry["n_methods"] == 2

    # Confirm resp-only bins are in entries but have n_methods=1
    for resp_only_bin in [3, 4]:
        e = next((x for x in entries if x["bin_idx"] == resp_only_bin), None)
        if e is not None:
            assert e["n_methods"] == 1, f"bin {resp_only_bin} should have n_methods=1"
