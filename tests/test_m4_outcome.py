"""Regression tests for the extraction of the AHET classifier into src/m4/outcome.py.

M8 Step 1b plan section 4.1 requires the classifier to move out of the executable
`scripts/diagnose_bin_drift.py` into an importable module, with "bit-identical behaviour
regression tests" for the old and new callers. These tests pin three things:

1. every caller resolves to the *same function object* — not a copy that could drift;
2. the classification of every reachable producer state is unchanged; and
3. the exact `ValueError` message text of every fail-closed branch is unchanged, since
   those messages are the diagnostic surface the bin-drift work depends on.

`tests/test_diagnose_bin_drift.py` continues to exercise the classifier through its
original import path, so behaviour is covered from both directions.
"""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from src.m4 import outcome  # noqa: E402
from src.m4.outcome import classify_window_outcome  # noqa: E402

import diagnose_bin_drift as bindrift  # noqa: E402
import score_offline as so  # noqa: E402

NOT_RUN = outcome.REJECTION_CODE_NOT_ATTEMPTED
PASSED = outcome.REJECTION_CODE_PASSED
NOT_ATTEMPTED = outcome.REJECTION_CODE_NOT_ATTEMPTED_WITHIN_GATE


def codes(*values: int) -> np.ndarray:
    return np.array(values, dtype=np.int64)


# ── Caller identity: no copies, no drift ─────────────────────────────────────

def test_every_caller_resolves_to_the_same_function_object():
    assert bindrift.classify_window_outcome is classify_window_outcome
    assert so.classify_window_outcome is classify_window_outcome


def test_constants_are_shared_not_duplicated():
    for name in (
        "REJECTION_CODE_NOT_ATTEMPTED",
        "REJECTION_CODE_PASSED",
        "REJECTION_CODE_NOT_ATTEMPTED_WITHIN_GATE",
        "REJECTION_CODE_DOMAIN",
        "RESP_GATE_LO_HZ",
        "RESP_GATE_HI_HZ",
    ):
        assert getattr(bindrift, name) == getattr(outcome, name), name


def test_module_does_not_import_the_executable_script():
    """Plan section 4.1: do not duplicate or import an executable script.

    Checks the import graph via AST rather than grepping the text, because the module
    docstring legitimately names `scripts/diagnose_bin_drift.py` as the extraction source.
    """
    import ast

    tree = ast.parse((REPO_ROOT / "src" / "m4" / "outcome.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "diagnose_bin_drift" not in imported
    assert "score_offline" not in imported
    assert imported <= {"__future__", "numpy", "src"}, f"unexpected imports: {imported}"


# ── The two valid producer states ────────────────────────────────────────────

@pytest.mark.parametrize("f_r_hz", [float("nan"), 0.0, 0.1499, 0.6001, 5.0])
def test_no_gate_state_returns_gate_not_run(f_r_hz):
    assert classify_window_outcome(-1, codes(NOT_RUN, NOT_RUN, NOT_RUN), f_r_hz) == "gate_not_run"


@pytest.mark.parametrize("rank", [0, 1, 2])
def test_executed_gate_with_a_passed_slot_returns_covered(rank):
    row = [3, 3, 3]
    row[rank] = PASSED
    assert classify_window_outcome(rank, codes(*row), 0.3) == "covered"


def test_executed_gate_without_a_passed_slot_returns_other_rejected():
    assert classify_window_outcome(-1, codes(2, 3, 4), 0.3) == "other_rejected"


def test_trailing_not_attempted_suffix_is_accepted():
    assert classify_window_outcome(-1, codes(2, NOT_ATTEMPTED, NOT_ATTEMPTED), 0.3) == "other_rejected"
    assert classify_window_outcome(0, codes(PASSED, NOT_ATTEMPTED, NOT_ATTEMPTED), 0.3) == "covered"


@pytest.mark.parametrize("f_r_hz", [outcome.RESP_GATE_LO_HZ, outcome.RESP_GATE_HI_HZ])
def test_physiological_gate_bounds_are_inclusive(f_r_hz):
    assert classify_window_outcome(-1, codes(2, 3, 4), f_r_hz) == "other_rejected"


# ── Fail-closed branches, pinned by exact message ────────────────────────────

@pytest.mark.parametrize(
    "rank, row, f_r_hz, fragment",
    [
        (-2, (2, 3, 4), 0.3, "is outside the valid domain"),
        (3, (2, 3, 4), 0.3, "is outside the valid domain"),
        (-1, (NOT_RUN, 2, 3), 0.3, "mixes 'not-run' (-1) with concrete"),
        (0, (NOT_RUN, NOT_RUN, NOT_RUN), 0.3, "is contradictory with all-not-run"),
        (-1, (NOT_RUN, NOT_RUN, NOT_RUN), 0.3, "contradictory with an in-gate finite"),
        (-1, (2, 3, 4), float("nan"), "contradictory with a non-finite"),
        (-1, (2, 3, 4), 0.9, "outside the physiological gate"),
        (-1, (NOT_ATTEMPTED, 2, NOT_ATTEMPTED), 0.3, "must form a trailing suffix"),
        (0, (2, PASSED, 3), 0.3, "not the 'passed' code"),
        (1, (PASSED, PASSED, 3), 0.3, "is not the FIRST passed slot"),
        (-1, (PASSED, 2, 3), 0.3, "is contradictory with a 'passed'"),
    ],
)
def test_fail_closed_branches_raise_with_unchanged_messages(rank, row, f_r_hz, fragment):
    with pytest.raises(ValueError, match=re_escape(fragment)):
        classify_window_outcome(rank, codes(*row), f_r_hz)


def re_escape(text: str) -> str:
    import re

    return re.escape(text)


def test_every_caller_produces_identical_results_and_messages():
    """Exhaustive equivalence across all three import paths."""
    cases = [
        (-1, (NOT_RUN, NOT_RUN, NOT_RUN), float("nan")),
        (-1, (NOT_RUN, NOT_RUN, NOT_RUN), 0.9),
        (0, (PASSED, 2, 3), 0.3),
        (1, (2, PASSED, 3), 0.3),
        (-1, (2, 3, 4), 0.3),
        (-1, (2, NOT_ATTEMPTED, NOT_ATTEMPTED), 0.3),
        (-2, (2, 3, 4), 0.3),
        (3, (2, 3, 4), 0.3),
        (-1, (NOT_RUN, 2, 3), 0.3),
        (0, (NOT_RUN, NOT_RUN, NOT_RUN), 0.3),
        (-1, (NOT_RUN, NOT_RUN, NOT_RUN), 0.3),
        (-1, (2, 3, 4), float("nan")),
        (-1, (2, 3, 4), 0.9),
        (-1, (NOT_ATTEMPTED, 2, NOT_ATTEMPTED), 0.3),
        (0, (2, PASSED, 3), 0.3),
        (1, (PASSED, PASSED, 3), 0.3),
        (-1, (PASSED, 2, 3), 0.3),
    ]

    def evaluate(fn, rank, row, f_r_hz):
        try:
            return ("ok", fn(rank, codes(*row), f_r_hz))
        except ValueError as exc:
            return ("error", str(exc))

    for rank, row, f_r_hz in cases:
        reference = evaluate(classify_window_outcome, rank, row, f_r_hz)
        assert evaluate(bindrift.classify_window_outcome, rank, row, f_r_hz) == reference
        assert evaluate(so.classify_window_outcome, rank, row, f_r_hz) == reference
