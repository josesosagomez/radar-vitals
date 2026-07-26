"""M4 Stage 0: the normalised estimator adapter, and the no-duplicate invariant.

The adapter (`WindowEstimate` + `run_config_hash`) is what lets M8/M9/M10 estimators enter
the M4 grid and scoring path without copying the comparator (plan §5.1 item 3).

The identity tests at the bottom are the standing guard for M4R-10: if anyone ever
re-adds a private DSP or warmup copy to `scripts/live_demo.py`, M4's equality test
would start comparing M4 against a duplicate rather than against production. These
tests fail the moment that happens.
"""
from __future__ import annotations

import inspect
import sys
import types
from pathlib import Path

import numpy as np
import pytest

try:
    import yaml as _yaml  # noqa: F401
except ModuleNotFoundError:
    sys.modules["yaml"] = types.ModuleType("yaml")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.window_pipeline import (  # noqa: E402
    ESTIMATOR_ID,
    REJECTION_CODE_NAMES,
    WindowEstimate,
    as_window_estimate,
    run_config_hash,
    run_window_dsp,
)


def _dsp_dict(**over) -> dict:
    base = {
        "hr_valid": True,
        "hr_raw": 72.5,
        "fallback_hr_bpm": 51.0,
        "br_bpm": 15.25,
        "br_valid": True,
        "br_confidence": "high",
        "f_r_hz": 0.254,
        "rej_reason": "passed",
    }
    base.update(over)
    return base


# ── run_config_hash ───────────────────────────────────────────────────────────

def test_run_config_hash_is_deterministic_across_calls():
    cfg = {"heart": {"k_max": 4}, "phase": {"method": "atan2"}}
    assert run_config_hash(cfg) == run_config_hash(cfg)


def test_run_config_hash_ignores_key_insertion_order():
    a = {"heart": {"k_max": 4, "band_hz": [0.8, 2.0]}, "phase": {"method": "atan2"}}
    b = {"phase": {"method": "atan2"}, "heart": {"band_hz": [0.8, 2.0], "k_max": 4}}
    assert run_config_hash(a) == run_config_hash(b)


def test_run_config_hash_changes_when_any_value_changes():
    a = {"heart": {"k_max": 4}}
    b = {"heart": {"k_max": 5}}
    assert run_config_hash(a) != run_config_hash(b)


def test_run_config_hash_does_not_collide_across_types():
    """S0R-01. The first version stringified non-JSON values, so distinct values shared
    a hash. A provenance key that cannot tell them apart silently misattributes a run.
    (S0R-01's Path and NumPy cases are now covered by *rejection* instead — see
    `test_pathlib_paths_are_rejected_because_flavours_collide` and
    `test_numpy_values_are_rejected_with_actionable_guidance`.)"""
    assert run_config_hash({"x": 3}) != run_config_hash({"x": "3"})
    assert run_config_hash({"x": 3}) != run_config_hash({"x": 3.0})
    assert run_config_hash({"x": True}) != run_config_hash({"x": 1})
    assert run_config_hash({"x": [1, 2]}) != run_config_hash({"x": (1, 2)})
    assert run_config_hash({"x": None}) != run_config_hash({"x": "None"})


def test_run_config_hash_rejects_what_it_cannot_canonicalise():
    """Deterministic rejection beats silent coercion for a provenance key."""
    class Opaque:
        pass

    with pytest.raises(TypeError, match="cannot canonicalise"):
        run_config_hash({"x": Opaque()})


def test_run_config_hash_handles_nan_and_inf_deterministically():
    a = run_config_hash({"x": float("nan")})
    assert a == run_config_hash({"x": float("nan")})
    assert a != run_config_hash({"x": float("inf")})
    assert a != run_config_hash({"x": "nan"})


def test_numpy_values_are_rejected_with_actionable_guidance():
    """S0R-09/10/11. NumPy support was speculative and produced five Blocking findings in
    three rounds — subclass collisions, non-terminating `.item()` recursion, structured
    `void` collisions, dropped `dtype.metadata`, erased mask state, and hashed alignment
    padding. Each was a *different* way for a NumPy value to carry state a byte- or
    descriptor-level encoding misses. The project's real configs contain no NumPy at all,
    so the surface is gone rather than patched a sixth time."""
    for value in (
        np.int64(3), np.float64(3.0), np.str_("a"), np.bool_(True),
        np.longdouble("1.25"), np.datetime64("2026-07-26"),
        np.array([1, 2]), np.ma.MaskedArray([1, 2], mask=[False, True]),
        np.array([(1,)], dtype=[("x", "<i4")])[0],
        np.array([1], dtype=np.dtype("i4", metadata={"unit": "a"})),
    ):
        with pytest.raises(TypeError, match="does not accept NumPy"):
            run_config_hash({"x": value})


def test_numpy_rejection_names_a_conversion():
    with pytest.raises(TypeError, match=r"x\.tolist\(\)"):
        run_config_hash({"x": np.array([1, 2])})


def test_dispatch_is_by_exact_type_so_subclasses_are_rejected():
    """A subclass may carry state the encoder cannot see, and encoding it as its base
    type would silently discard that state while assigning the base type's key — the
    MaskedArray failure (S0R-10) in general form. `IntEnum` is the everyday example:
    two members of different enums with the same value would otherwise collide."""
    import enum

    class Unit(enum.IntEnum):
        A = 1

    class Tagged(str):
        pass

    for value in (Unit.A, Tagged("x"), {"a": 1}.keys()):
        with pytest.raises(TypeError, match="matched EXACTLY|cannot canonicalise"):
            run_config_hash({"x": value})


def test_a_real_project_config_hashes():
    """The contract must actually cover what this project feeds it. Guards against a
    future narrowing that looks principled and breaks the only real caller.

    S0R-14: the first version of this test read `run_metadata.json` from
    `results/live_demo/…` under `if meta.exists()`. That path is gitignored (`results*/`)
    and not in the index, so in a clean clone the test passed **without calling
    `run_config_hash` at all** — a vacuous pass in the one test written to prevent
    vacuous coverage. It now reads a TRACKED fixture unconditionally: a verbatim copy of
    that same config block, checked in at `tests/fixtures/sample_run_config.json`."""
    import json as _json

    fixture = Path(__file__).resolve().parent / "fixtures" / "sample_run_config.json"
    cfg = _json.loads(fixture.read_text(encoding="utf-8"))   # no exists() guard, on purpose
    assert len(run_config_hash(cfg)) == 64
    assert run_config_hash(cfg) == run_config_hash(cfg)


def test_reference_cycles_raise_instead_of_exhausting_the_stack():
    """S0R-13. A self-referential container has no finite canonical form. It used to
    recurse to RecursionError — an implementation-detail crash, not the named rejection
    the contract promises — and YAML anchors can express exactly this."""
    cyclic_list = [1, 2]
    cyclic_list.append(cyclic_list)
    with pytest.raises(TypeError, match="self-referential list"):
        run_config_hash({"heart": {"k_max": 4}, "x": cyclic_list})

    cyclic_dict: dict = {"a": 1}
    cyclic_dict["self"] = cyclic_dict
    with pytest.raises(TypeError, match="self-referential dict"):
        run_config_hash(cyclic_dict)

    nested: dict = {"outer": {}}
    nested["outer"]["back"] = nested
    with pytest.raises(TypeError, match="self-referential"):
        run_config_hash(nested)


def test_repeated_non_cyclic_references_still_hash():
    """Cycle detection must be scoped to the ACTIVE traversal path, not a global seen-set:
    the same subtree appearing twice side by side is ordinary, not a cycle."""
    shared = {"band_hz": [0.8, 2.0]}
    assert len(run_config_hash({"a": shared, "b": shared})) == 64
    row = [1, 2]
    assert run_config_hash({"x": [row, row]}) == run_config_hash({"x": [[1, 2], [1, 2]]})


def test_pathlib_paths_are_rejected_because_flavours_collide():
    """S0R-12. Paths were the single `isinstance` exception left after round 4, and they
    reintroduced the exact flaw exact-type dispatch had just removed: the two flavours
    compare UNEQUAL as values but share one POSIX string, so encoding by `as_posix()`
    gave distinct values the same provenance key."""
    from pathlib import PurePosixPath, PureWindowsPath

    assert PurePosixPath("a/b") != PureWindowsPath("a/b")        # the mechanism
    for value in (Path("results/x"), PurePosixPath("a/b"), PureWindowsPath("a/b")):
        with pytest.raises(TypeError, match="does not accept pathlib paths"):
            run_config_hash({"x": value})


# ── as_window_estimate ────────────────────────────────────────────────────────

def test_adapter_carries_estimator_id_and_run_config_hash():
    est = as_window_estimate(_dsp_dict(), run_config_hash="abc123")
    assert est.estimator_id == ESTIMATOR_ID
    assert est.run_config_hash == "abc123"


def test_adapter_passes_through_valid_rates():
    est = as_window_estimate(_dsp_dict(), run_config_hash="h")
    assert est.hr_bpm == pytest.approx(72.5)
    assert est.br_bpm == pytest.approx(15.25)
    assert est.hr_valid is True and est.br_valid is True
    assert est.f_r_hz == pytest.approx(0.254)
    assert est.rejection_reason == "passed"


def test_invalid_hr_becomes_nan_even_when_a_rate_is_present():
    """An unverified window must never surface a number. `hr_raw` can still hold a
    value when ahet_verified is False, so the NaN rule is enforced at the boundary."""
    est = as_window_estimate(
        _dsp_dict(hr_valid=False, hr_raw=61.0, rej_reason="ratio_db_low"), run_config_hash="h"
    )
    assert est.hr_valid is False
    assert np.isnan(est.hr_bpm)
    assert est.rejection_reason == "ratio_db_low"


def test_invalid_br_becomes_nan_even_when_a_rate_is_present():
    est = as_window_estimate(_dsp_dict(br_valid=False, br_bpm=6.0), run_config_hash="h")
    assert est.br_valid is False
    assert np.isnan(est.br_bpm)


def test_adapter_does_not_expose_the_naive_argmax_fallback():
    """`fallback_hr_bpm` is a LIAR (CLAUDE.md §4) — it must not reach the scorer as a
    rate field. It stays reachable only inside `raw` for diagnostics."""
    est = as_window_estimate(_dsp_dict(), run_config_hash="h")
    assert not hasattr(est, "fallback_hr_bpm")
    assert "fallback_hr_bpm" not in {f for f in est.__dataclass_fields__}
    assert est.raw["fallback_hr_bpm"] == 51.0


def test_adapter_accepts_a_foreign_estimator_id():
    """M8/M9/M10 estimators reuse the record rather than copying the comparator."""
    est = as_window_estimate(_dsp_dict(), estimator_id="ha_paper1_v1", run_config_hash="h")
    assert est.estimator_id == "ha_paper1_v1"


def test_missing_optional_fields_do_not_raise():
    est = as_window_estimate({"hr_valid": False, "br_valid": False}, run_config_hash="h")
    assert np.isnan(est.hr_bpm) and np.isnan(est.br_bpm)
    assert est.br_confidence == "low"
    assert est.f_r_hz is None


def test_record_is_frozen_and_raw_is_excluded_from_equality():
    a = as_window_estimate(_dsp_dict(), run_config_hash="h")
    b = as_window_estimate(_dsp_dict(fallback_hr_bpm=99.0), run_config_hash="h")
    assert a == b, "raw is diagnostic payload and must not affect record equality"
    with pytest.raises(Exception):
        a.hr_bpm = 1.0  # frozen dataclass


def test_two_identical_invalid_records_compare_equal():
    """S0R-03. Both rates are NaN in an invalid record, and NaN != NaN, so plain
    dataclass equality reports two identical failure states as different — the
    dominant case, which the valid-rate test above never exercised."""
    a = as_window_estimate({"hr_valid": False, "br_valid": False}, run_config_hash="h")
    b = as_window_estimate({"hr_valid": False, "br_valid": False}, run_config_hash="h")
    assert a == b


def test_records_differing_in_a_normalised_field_are_unequal():
    a = as_window_estimate(_dsp_dict(), run_config_hash="h")
    assert a != as_window_estimate(_dsp_dict(), run_config_hash="other")
    assert a != as_window_estimate(_dsp_dict(hr_raw=70.0), run_config_hash="h")
    assert a != as_window_estimate(_dsp_dict(br_valid=False), run_config_hash="h")
    assert a.__eq__(object()) is NotImplemented


def test_record_is_unhashable_because_equality_is_nan_aware():
    a = as_window_estimate(_dsp_dict(), run_config_hash="h")
    with pytest.raises(TypeError):
        hash(a)


def test_valid_flag_with_a_missing_rate_raises():
    """S0R-02. A record whose disposition and value contradict each other would leave
    the scorer to guess which field wins."""
    with pytest.raises(ValueError, match="HR is marked valid"):
        as_window_estimate({"hr_valid": True, "br_valid": False}, run_config_hash="h")
    with pytest.raises(ValueError, match="BR is marked valid"):
        as_window_estimate({"hr_valid": False, "br_valid": True}, run_config_hash="h")


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_valid_flag_with_a_non_finite_rate_raises(bad):
    with pytest.raises(ValueError, match="HR is marked valid"):
        as_window_estimate(
            {"hr_valid": True, "hr_raw": bad, "br_valid": False}, run_config_hash="h"
        )
    with pytest.raises(ValueError, match="BR is marked valid"):
        as_window_estimate(
            {"hr_valid": False, "br_valid": True, "br_bpm": bad}, run_config_hash="h"
        )


# ── the no-duplicate invariant (M4R-10) ───────────────────────────────────────

def test_live_demo_imports_the_shared_callables_rather_than_defining_its_own():
    import scripts.live_demo as live_demo
    import src.warmup_select as warmup_select
    import src.window_pipeline as window_pipeline

    assert live_demo.run_window_dsp is window_pipeline.run_window_dsp
    assert live_demo.run_warmup_selection is warmup_select.run_warmup_selection
    assert live_demo.derive_candidate_bins is warmup_select.derive_candidate_bins
    assert live_demo.resolve_locked_bin is warmup_select.resolve_locked_bin


def test_live_demo_defines_no_private_dsp_or_warmup_copy():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "live_demo.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "def _run_dsp",
        "def _run_warmup_selection",
        "def _derive_candidate_bins",
        "def _range_energy_by_bin",
        "def _resolve_locked_bin",
    ):
        assert forbidden not in source, (
            f"{forbidden} is back in live_demo.py — M4 would then compare itself "
            "against a duplicate of the production path (M4 plan §5.1 / M4R-10)."
        )


def test_warmup_selection_defaults_to_the_shared_window_dsp():
    import src.warmup_select as warmup_select

    default = inspect.signature(warmup_select.run_warmup_selection).parameters["dsp_fn"].default
    assert default is run_window_dsp


def test_window_dsp_presents_the_estimator_call_signature():
    """The adapter contract M8/M9/M10 estimators must match."""
    names = list(inspect.signature(run_window_dsp).parameters)
    assert names == ["frames", "locked_bin", "fs", "cfg"]


def test_rejection_code_table_moved_intact():
    """Pinned in full: `scripts/diagnose_live_run.py` keeps a mirror of this table,
    so a silent edit here would desynchronise run post-mortems from the estimator."""
    assert REJECTION_CODE_NAMES == {
        -1: "", 0: "passed",
        1: "no_second_harmonic_region", 2: "ratio_db_low",
        3: "prominence_low", 4: "low_candidate_competitor",
        5: "not_attempted", 6: "peak_to_floor_db_low",
        7: "low_candidate_floor_db_low",
    }
