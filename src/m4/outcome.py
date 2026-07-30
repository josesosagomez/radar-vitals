"""Stable, importable AHET window-outcome classifier.

Extracted verbatim from ``scripts/diagnose_bin_drift.py`` (M8 Step 1b plan section 4.1).
The classifier previously lived only inside an executable script, which
``scripts/score_offline.py`` reached by importing that script as a module. Step 1b needs a
third caller — the production estimator suite's ``eca_bindrift_outcome_v1`` adapter — and
the plan forbids importing an executable script, so the logic now lives here and both
existing callers import it.

**Behaviour is unchanged.** The classification rules, the exact ``ValueError`` messages, and
the constants are byte-for-byte the originals; ``tests/test_m4_outcome.py`` pins both
existing callers to this module and asserts identical results and messages. Any change to
the rules here changes committed scientific outputs and must go through the plan, not this
docstring.
"""
from __future__ import annotations

import numpy as np

from src.vitals import AHET_MAX_CANDIDATES

__all__ = [
    "AHET_MAX_CANDIDATES",
    "REJECTION_CODE_DOMAIN",
    "REJECTION_CODE_NOT_ATTEMPTED",
    "REJECTION_CODE_NOT_ATTEMPTED_WITHIN_GATE",
    "REJECTION_CODE_PASSED",
    "RESP_GATE_HI_HZ",
    "RESP_GATE_LO_HZ",
    "classify_window_outcome",
]

REJECTION_CODE_NOT_ATTEMPTED = -1
REJECTION_CODE_PASSED = 0
#: A candidate slot the executed strict_v1 gate never reached this window
#: (distinct from REJECTION_CODE_NOT_ATTEMPTED=-1, "the gate never ran at
#: all") -- src/vitals.py:940 assigns this to every slot past the last
#: attempted candidate.
REJECTION_CODE_NOT_ATTEMPTED_WITHIN_GATE = 5
REJECTION_CODE_DOMAIN = frozenset(range(-1, 8))  # -1 gate_not_run, 0 passed, 1-7 rejection reasons (src/vitals.py)

# Physiological respiration gate (src/vitals.py:507-509 -- local to
# estimate_rate_from_phase there, so not importable as a module constant).
# An f_r_hz outside this range routes through the SAME no-ECA early-return
# branch as f_r_hz=None (src/vitals.py:523), producing accepted_candidate_rank=-1
# and all-not-run rejection codes with a FINITE f_r_hz (BDR-02 R3).
RESP_GATE_LO_HZ = 0.15
RESP_GATE_HI_HZ = 0.60


def _f_r_in_gate(f_r_hz: float) -> bool:
    return bool(np.isfinite(f_r_hz) and (RESP_GATE_LO_HZ <= f_r_hz <= RESP_GATE_HI_HZ))


def _is_trailing_suffix_of_not_attempted(rejection_codes: np.ndarray) -> bool:
    """True iff every REJECTION_CODE_NOT_ATTEMPTED_WITHIN_GATE (5) forms a
    trailing suffix of the row -- once a 5 appears, every later slot must
    also be 5 (BDR-25 R2). `src/vitals.py:815` attempts candidates via
    `enumerate(candidates_global)` in order 0..N-1, so `candidate_attempted`
    is always a PREFIX and its complement (coded 5 at src/vitals.py:940) is
    always a SUFFIX -- a row like `[5, 2, 5]` (slot 0 not attempted, slot 1
    attempted) is structurally impossible."""
    is_five = rejection_codes == REJECTION_CODE_NOT_ATTEMPTED_WITHIN_GATE
    if not np.any(is_five):
        return True
    first_five = int(np.argmax(is_five))
    return bool(np.all(is_five[first_five:]))


def classify_window_outcome(accepted_rank: int, rejection_codes: np.ndarray,
                             f_r_hz: float) -> str:
    """Mutually exclusive classifier (plan §4). Fail-closed on evidence the
    producer (scripts/live_demo.py, the strict_v1 AHET gate mode production
    runs use) can never actually emit (BDR-02 R2, BDR-02 R3, BDR-25,
    BDR-25 R2).

    Expressed as the two EXHAUSTIVE producer states rather than an ad hoc
    set of checks (BDR-25 R2's own recommendation, to keep the converse from
    being missed again the way round 8/9 each missed one direction):

    1. **No-gate** (`src/vitals.py:523`): `f_r_hz` is `None` or fails the
       physiological gate `[RESP_GATE_LO_HZ, RESP_GATE_HI_HZ]`. Every
       `rejection_codes` slot is `-1`, `accepted_rank` is always `-1`
       (never accepted). -> `"gate_not_run"`.
    2. **Executed-gate**: `f_r_hz` is finite and within the gate. NO
       `rejection_codes` slot is `-1` -- every slot got a concrete code, with
       `REJECTION_CODE_NOT_ATTEMPTED_WITHIN_GATE` (5) forming a trailing
       suffix only (`src/vitals.py:815-820,940`: candidates are attempted in
       order 0..N-1, so the never-attempted complement is always the tail,
       never interleaved). If any slot is coded `REJECTION_CODE_PASSED` (0),
       `accepted_rank` is the FIRST such slot (`src/vitals.py:941-943`) ->
       `"covered"`; otherwise `accepted_rank` stays `-1` -> `"other_rejected"`.

    A row that is not exactly one of these two states -- a domain-invalid
    rank, a MIX of `-1` and concrete codes, a concrete row with a
    non-finite/out-of-gate `f_r_hz` (BDR-25 R2 -- the converse of state 1:
    round 9 only checked this for `other_rejected`'s "does NOT hold" side
    via the no-gate branch, never for the executed-gate side itself, so
    `rank=-1, codes=[2,3,5], f_r_hz=NaN` still silently returned
    `"other_rejected"`), a non-suffix `REJECTION_CODE_NOT_ATTEMPTED_WITHIN_GATE`
    (BDR-25 R2), an accepted rank whose own slot isn't `REJECTION_CODE_PASSED`
    or isn't the first such slot, or a negative rank alongside any
    `REJECTION_CODE_PASSED` slot -- raises `ValueError` rather than silently
    landing in `"covered"`, `"gate_not_run"`, or `"other_rejected"`."""
    if accepted_rank < -1 or accepted_rank >= AHET_MAX_CANDIDATES:
        raise ValueError(
            f"accepted_candidate_rank={accepted_rank} is outside the valid domain "
            f"{{-1, 0, ..., {AHET_MAX_CANDIDATES - 1}}} for AHET_MAX_CANDIDATES="
            f"{AHET_MAX_CANDIDATES}."
        )
    all_not_run = bool(np.all(rejection_codes == REJECTION_CODE_NOT_ATTEMPTED))
    any_not_run = bool(np.any(rejection_codes == REJECTION_CODE_NOT_ATTEMPTED))
    if any_not_run and not all_not_run:
        raise ValueError(
            f"rejection_codes={rejection_codes.tolist()} mixes 'not-run' (-1) with concrete "
            "codes -- a strict_v1 row either has ALL codes -1 (the gate never ran) or NO -1 "
            "anywhere (an executed gate always assigns every slot a concrete code, including "
            "5 for a never-attempted slot)."
        )

    if all_not_run:
        # State 1: no-gate. Requires accepted_rank=-1 and f_r_hz to have
        # FAILED the physiological gate (None or an outlier).
        if accepted_rank >= 0:
            raise ValueError(
                f"accepted_candidate_rank={accepted_rank} is contradictory with all-"
                "not-run rejection codes -- an executed AHET gate always assigns "
                "concrete codes, including 'passed' for the accepted slot."
            )
        if _f_r_in_gate(f_r_hz):
            raise ValueError(
                f"accepted_candidate_rank=-1 with all-not-run rejection codes is "
                f"contradictory with an in-gate finite f_r_hz={f_r_hz} -- ECA/AHET always "
                "executes (and assigns concrete codes) whenever f_r_hz passes the "
                "physiological gate."
            )
        return "gate_not_run"

    # State 2: executed-gate (no -1 anywhere in rejection_codes). Requires a
    # finite, in-gate f_r_hz -- the CONVERSE of state 1's requirement
    # (BDR-25 R2): concrete codes can only exist if ECA/AHET actually ran,
    # regardless of whether any candidate ultimately passed.
    if not np.isfinite(f_r_hz):
        raise ValueError(
            f"rejection_codes={rejection_codes.tolist()} has no not-run (-1) entries, "
            "implying the AHET gate executed, which is contradictory with a non-finite "
            "f_r_hz -- ECA/AHET only runs when f_r_hz is finite."
        )
    if not _f_r_in_gate(f_r_hz):
        raise ValueError(
            f"rejection_codes={rejection_codes.tolist()} has no not-run (-1) entries, "
            f"implying the AHET gate executed, which is contradictory with f_r_hz={f_r_hz}, "
            f"outside the physiological gate [{RESP_GATE_LO_HZ}, {RESP_GATE_HI_HZ}] Hz."
        )
    if not _is_trailing_suffix_of_not_attempted(rejection_codes):
        raise ValueError(
            f"rejection_codes={rejection_codes.tolist()}: "
            f"REJECTION_CODE_NOT_ATTEMPTED_WITHIN_GATE ({REJECTION_CODE_NOT_ATTEMPTED_WITHIN_GATE}) "
            "must form a trailing suffix -- candidates are attempted in order 0..N-1, so a "
            "never-attempted slot cannot precede an attempted one."
        )

    passed_mask = rejection_codes == REJECTION_CODE_PASSED
    if accepted_rank >= 0:
        if int(rejection_codes[accepted_rank]) != REJECTION_CODE_PASSED:
            raise ValueError(
                f"accepted_candidate_rank={accepted_rank}'s own rejection_codes entry "
                f"is {int(rejection_codes[accepted_rank])}, not the 'passed' code "
                f"({REJECTION_CODE_PASSED})."
            )
        first_passed_idx = int(np.argmax(passed_mask))
        if first_passed_idx != accepted_rank:
            raise ValueError(
                f"accepted_candidate_rank={accepted_rank} is not the FIRST passed slot -- "
                f"slot {first_passed_idx} is also coded 'passed' in "
                f"rejection_codes={rejection_codes.tolist()}; strict_v1 always selects the "
                "first passing candidate."
            )
        return "covered"

    if bool(np.any(passed_mask)):
        raise ValueError(
            f"accepted_candidate_rank=-1 is contradictory with a 'passed' "
            f"({REJECTION_CODE_PASSED}) entry in rejection_codes={rejection_codes.tolist()} "
            "-- a passed slot always forces the corresponding non-negative rank."
        )
    return "other_rejected"
