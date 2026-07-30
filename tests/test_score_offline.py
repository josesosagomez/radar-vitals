"""Tests for scripts/score_offline.py (plans/offline_scoring_script.md).

Pure-logic tests (CLI parsing/validation, config diffing, evidence stacking, paced
schedule arithmetic) use synthetic inputs. Tests of the lock-resolution rejection
paths (OSR-03 R2/R3/round 4) and Masimo auto-discovery use the real capture
directories under results/live_demo/, per the plan's own negative-case fixtures
(the massimo1 guard_cardiac_candidate_v1 replay sharing its raw hash and even its
numeric lock with the production replay; the original massimo1 directory as the
wrong-lock-value case). A small handful of tests run the real DSP end-to-end on
the smallest real capture (massimo2, 6 windows) to check the full pipeline wiring.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import score_offline as so  # noqa: E402

LIVE_DEMO = REPO_ROOT / "results" / "live_demo"
MASSIMO1 = LIVE_DEMO / "20260713_172042_live_demo_massimo1"
MASSIMO2 = LIVE_DEMO / "20260713_182002_live_demo_massimo2"
SWEEP = LIVE_DEMO / "20260714_180523_live_demo_sweep"
REPLAY_MASSIMO1_PROD = LIVE_DEMO / "20260726_173434_replay_unknown"
REPLAY_MASSIMO1_GUARD = LIVE_DEMO / "20260727_182319_replay_unknown"

_REQUIRE_REAL_DATA = pytest.mark.skipif(
    not MASSIMO1.exists() or not MASSIMO2.exists(),
    reason="results/live_demo/ real captures not present in this environment",
)

# The two replay directories are transient diagnostic artifacts from the 2026-07-26/27
# bin-drift work; `results/` blobs are gitignored, so they are absent in a fresh clone.
# _REQUIRE_REAL_DATA does not cover them (it only checks the massimo captures), which
# left the tests below failing rather than skipping. The portable equivalents further
# down cover the same OSR-03 branches without any real data.
_REQUIRE_REPLAY_PROD = pytest.mark.skipif(
    not REPLAY_MASSIMO1_PROD.exists(),
    reason="results/live_demo/20260726_173434_replay_unknown replay artifact not present",
)


def _write_lock_source(
    directory: Path,
    *,
    raw_sha256: str,
    locked_bin: int = 27,
    eca_mode: str = "skip_forbidden_harmonics_v1",
    mode: str = "replay",
) -> Path:
    """Build a minimal portable `--pinned-lock-source` directory.

    `resolve_pinned_lock` reads only `run_metadata.json`, so a few fields reproduce a
    real lock source exactly, without a multi-hundred-megabyte capture.
    """
    directory.mkdir(parents=True, exist_ok=True)
    payload: dict = {
        "mode": mode,
        "locked_bin": locked_bin,
        "config": {"heart": {"eca_mode": eca_mode}},
    }
    if mode == "replay":
        payload["replay_file_hashes"] = {"adc_stream.bin": raw_sha256}
    elif mode == "live":
        payload["live_raw_mirror_hash"] = raw_sha256
    (directory / "run_metadata.json").write_text(
        json.dumps(payload, sort_keys=True), encoding="utf-8"
    )
    return directory


# ── CLI value parsing ────────────────────────────────────────────────────────

def test_parse_kv_list_basic():
    assert so.parse_kv_list(["a=1", "b=2"], "--x") == {"a": "1", "b": "2"}


def test_parse_kv_list_empty():
    assert so.parse_kv_list(None, "--x") == {}
    assert so.parse_kv_list([], "--x") == {}


def test_parse_kv_list_malformed_raises():
    with pytest.raises(ValueError):
        so.parse_kv_list(["no_equals_sign"], "--x")


def test_parse_kv_list_duplicate_key_raises():
    with pytest.raises(ValueError):
        so.parse_kv_list(["a=1", "a=2"], "--x")


# ── Step 1b: session-type / paced-schedule / paced-target-unavailable ──────

def test_session_type_natural_with_no_pacing_ok():
    so.validate_session_type_combination(["cap1"], {"cap1": "natural"}, {}, {})


def test_session_type_paced_with_schedule_ok():
    so.validate_session_type_combination(["cap1"], {"cap1": "paced"}, {"cap1": "16.0"}, {})


def test_session_type_paced_with_unavailable_ok():
    so.validate_session_type_combination(["cap1"], {"cap1": "paced"}, {}, {"cap1": "reason"})


def test_session_type_missing_raises():
    with pytest.raises(ValueError, match="required for every"):
        so.validate_session_type_combination(["cap1"], {}, {}, {})


def test_session_type_natural_with_schedule_raises():
    with pytest.raises(ValueError, match="contradictory"):
        so.validate_session_type_combination(["cap1"], {"cap1": "natural"}, {"cap1": "16.0"}, {})


def test_session_type_natural_with_unavailable_raises():
    with pytest.raises(ValueError, match="contradictory"):
        so.validate_session_type_combination(["cap1"], {"cap1": "natural"}, {}, {"cap1": "reason"})


def test_session_type_paced_with_neither_raises():
    """The 'forgotten target' case (OSR-19) must fail loudly."""
    with pytest.raises(ValueError, match="forgotten target"):
        so.validate_session_type_combination(["cap1"], {"cap1": "paced"}, {}, {})


def test_session_type_paced_with_both_raises():
    with pytest.raises(ValueError, match="ambiguous"):
        so.validate_session_type_combination(
            ["cap1"], {"cap1": "paced"}, {"cap1": "16.0"}, {"cap1": "reason"}
        )


# ── Paced schedule ───────────────────────────────────────────────────────────

def test_paced_schedule_constant_always_available():
    sched = so.parse_paced_schedule_value("16.0")
    assert sched.kind == "constant"
    status, rate = so.lookup_commanded_rate(sched, 0.0, 30.0)
    assert status == "available"
    assert rate == pytest.approx(16.0)


def test_paced_schedule_file_straddling_transition_unavailable(tmp_path):
    schedule_path = tmp_path / "schedule.yaml"
    schedule_path.write_text(
        "intervals:\n"
        "  - {start_s: 0.0, end_s: 120.0, commanded_rate_bpm: 12.0}\n"
        "  - {start_s: 120.0, end_s: 240.0, commanded_rate_bpm: 15.0}\n",
        encoding="utf-8",
    )
    sched = so.parse_paced_schedule_value(str(schedule_path))
    assert sched.kind == "file"
    # Window [110, 140) straddles the 120 s transition -> no target, ever.
    status, rate = so.lookup_commanded_rate(sched, 110.0, 140.0)
    assert status == "unavailable_transition"
    assert rate is None
    # Window fully inside one interval -> available.
    status, rate = so.lookup_commanded_rate(sched, 10.0, 40.0)
    assert status == "available"
    assert rate == pytest.approx(12.0)


def test_paced_schedule_value_not_number_not_file_raises():
    with pytest.raises(ValueError):
        so.parse_paced_schedule_value("not_a_number_and_not_a_file.yaml")


# ── Config diffing / ECA isolation assertion (OSR-13/OSR-13 R2) ─────────────

def test_diff_configs_flat_and_nested():
    a = {"heart": {"eca_mode": "x", "k_max": 6}, "top": 1}
    b = {"heart": {"eca_mode": "y", "k_max": 6}, "top": 1}
    diffs = so.diff_configs(a, b)
    assert diffs == {"heart.eca_mode": ("x", "y")}


def test_assert_isolated_fields_identical_configs_raises():
    a = {"heart": {"eca_mode": "x"}}
    b = {"heart": {"eca_mode": "x"}}
    with pytest.raises(ValueError, match="identical"):
        so.assert_isolated_fields(a, b, ["heart.eca_mode"])


def test_assert_isolated_fields_unexpected_diff_raises():
    a = {"heart": {"eca_mode": "x", "k_max": 6}}
    b = {"heart": {"eca_mode": "y", "k_max": 7}}
    with pytest.raises(ValueError, match="OUTSIDE"):
        so.assert_isolated_fields(a, b, ["heart.eca_mode"])


def test_assert_isolated_fields_declared_but_unchanged_raises():
    """OSR-13 R2: a declared field that does NOT differ cannot claim isolation."""
    a = {"heart": {"eca_mode": "x", "k_max": 6}}
    b = {"heart": {"eca_mode": "y", "k_max": 6}}
    with pytest.raises(ValueError, match="do not\n?\\s*actually differ|do not actually differ"):
        so.assert_isolated_fields(a, b, ["heart.eca_mode", "heart.k_max"])


def test_assert_isolated_fields_correct_isolation():
    a = {"heart": {"eca_mode": "x", "k_max": 6}}
    b = {"heart": {"eca_mode": "y", "k_max": 6}}
    result = so.assert_isolated_fields(a, b, ["heart.eca_mode"])
    assert result == {"heart.eca_mode": ("x", "y")}


# ── Evidence stacking (OSR-07 R2) ────────────────────────────────────────────

def test_stack_evidence_field_scalar_bool_int_float_str():
    assert list(so._stack_evidence_field("b", [True, False, True])) == [True, False, True]
    assert list(so._stack_evidence_field("i", [1, 2, 3])) == [1, 2, 3]
    arr = so._stack_evidence_field("f", [1.0, np.nan, 3.0])
    assert np.isnan(arr[1]) and arr[0] == 1.0 and arr[2] == 3.0
    arr = so._stack_evidence_field("s", ["a", "b"])
    assert list(arr) == ["a", "b"]


def test_stack_evidence_field_handles_missing_key_across_windows():
    """A key present on window 0 but absent (None) on window 1 must fill with a
    type-appropriate sentinel, not raise (mirrors hr_result's own
    ahet_second_harmonic_hz, which is only present when a candidate is accepted)."""
    arr = so._stack_evidence_field("ahet_second_harmonic_hz", [1.23, None, 4.56])
    assert arr[0] == pytest.approx(1.23)
    assert np.isnan(arr[1])
    assert arr[2] == pytest.approx(4.56)


def test_stack_evidence_field_array_shape_mismatch_raises():
    with pytest.raises(ValueError, match="varying shape"):
        so._stack_evidence_field("arr", [np.zeros(3), np.zeros(4)])


def test_stack_evidence_field_array_with_missing_window():
    values = [np.array([1.0, 2.0]), None, np.array([3.0, 4.0])]
    arr = so._stack_evidence_field("arr", values)
    assert arr.shape == (3, 2)
    assert np.all(np.isnan(arr[1]))
    assert np.array_equal(arr[0], [1.0, 2.0])


def test_build_evidence_arrays_union_of_keys_one_to_one_row_coverage():
    """Simulates run_window_dsp's real behaviour: hr_result's
    ahet_second_harmonic_hz key exists on some windows and not others."""
    dsp_list = [
        {"hr_valid": True, "hr_result": {"rate_bpm": 70.0, "ahet_second_harmonic_hz": 2.3}},
        {"hr_valid": False, "hr_result": {"rate_bpm": float("nan")}},
    ]
    evidence = so.build_evidence_arrays(
        dsp_list, k_values=[0, 1], frame_starts=[0, 600], frame_ends=[600, 1200],
        epoch_starts=[0.0, 30.0], epoch_ends=[30.0, 60.0], locked_bin=27,
    )
    assert evidence["hr_result__ahet_second_harmonic_hz"].shape == (2,)
    assert evidence["hr_result__ahet_second_harmonic_hz"][0] == pytest.approx(2.3)
    assert np.isnan(evidence["hr_result__ahet_second_harmonic_hz"][1])
    assert list(evidence["k"]) == [0, 1]
    assert evidence["locked_bin"][0] == 27


def test_build_evidence_arrays_empty_windows():
    evidence = so.build_evidence_arrays(
        [], k_values=[], frame_starts=[], frame_ends=[], epoch_starts=[], epoch_ends=[], locked_bin=5,
    )
    assert len(evidence["k"]) == 0
    assert evidence["locked_bin"][0] == 5


# ── score_window: as_window_estimate normalization, never leak native fields ─

def _hr_ref_admitted():
    return {
        "n_total": 30, "n_finite_pr": 30, "n_pi_qualified": 30, "n_usable": 30,
        "coverage_ok": True, "median_pr_bpm": 72.0, "spread_bpm": 1.0,
        "stationarity_ok": True, "admitted": True,
        "sensitivity": {3.0: True, 5.0: True, 8.0: True},
    }


def _br_ref_admitted():
    return {
        "n_total": 30, "n_finite_rr": 30, "availability_ok": True, "median_rr_bpm": 16.0,
        "spread_bpm": 0.5, "stationarity_ok": True, "admitted": True,
        "sensitivity": {2.0: True, 3.0: True, 5.0: True}, "pi_median": 3.0,
    }


def test_score_window_native_finite_invalid_br_bpm_does_not_leak():
    """OSR-12: run_window_dsp's native br_bpm can be finite even when br_valid is
    False; the scored column must come from as_window_estimate's normalized est,
    never the raw dsp dict."""
    from src.m4.window_grid import Window
    from src.window_pipeline import WindowEstimate

    w = Window(k=0, frame_start=0, frame_end=600, epoch_start=0.0, epoch_end=30.0)
    dsp = {"br_bpm": 17.5, "br_valid": False, "rej_reason": ""}  # native finite-but-invalid
    est = WindowEstimate(
        estimator_id="eca_ahet_v1", run_config_hash="deadbeef",
        hr_bpm=float("nan"), hr_valid=False, br_bpm=float("nan"), br_valid=False,
    )
    row = so.score_window(
        w, dsp, est, "gate_not_run", _hr_ref_admitted(), _br_ref_admitted(),
        frame0_epoch=0.0, session_type="natural", paced_schedule=None, paced_unavailable_reason=None,
    )
    assert np.isnan(row["br_bpm"])
    assert row["br_valid"] is False


def test_score_window_natural_session_metronome_not_applicable():
    from src.m4.window_grid import Window
    from src.window_pipeline import WindowEstimate

    w = Window(k=0, frame_start=0, frame_end=600, epoch_start=0.0, epoch_end=30.0)
    dsp = {"br_bpm": 16.0, "br_valid": True, "rej_reason": "passed"}
    est = WindowEstimate(
        estimator_id="eca_ahet_v1", run_config_hash="deadbeef",
        hr_bpm=72.0, hr_valid=True, br_bpm=16.0, br_valid=True,
    )
    row = so.score_window(
        w, dsp, est, "covered", _hr_ref_admitted(), _br_ref_admitted(),
        frame0_epoch=0.0, session_type="natural", paced_schedule=None, paced_unavailable_reason=None,
    )
    assert row["br_session_type"] == "natural"
    assert row["br_metronome_status"] == "not_applicable"
    assert np.isnan(row["br_commanded_rate_bpm"])


def test_score_window_paced_unavailable_reason_never_natural():
    """OSR-16 R3: sweep must be 'paced' with the declared reason, never 'natural'."""
    from src.m4.window_grid import Window
    from src.window_pipeline import WindowEstimate

    w = Window(k=0, frame_start=0, frame_end=600, epoch_start=0.0, epoch_end=30.0)
    dsp = {"br_bpm": 16.0, "br_valid": True, "rej_reason": "passed"}
    est = WindowEstimate(
        estimator_id="eca_ahet_v1", run_config_hash="deadbeef",
        hr_bpm=72.0, hr_valid=True, br_bpm=16.0, br_valid=True,
    )
    row = so.score_window(
        w, dsp, est, "covered", _hr_ref_admitted(), _br_ref_admitted(),
        frame0_epoch=0.0, session_type="paced", paced_schedule=None,
        paced_unavailable_reason="unavailable_missing_transition_timestamps",
    )
    assert row["br_session_type"] == "paced"
    assert row["br_metronome_status"] == "unavailable_missing_transition_timestamps"


def test_score_window_hr_error_only_when_admitted_and_valid():
    from src.m4.window_grid import Window
    from src.window_pipeline import WindowEstimate

    w = Window(k=0, frame_start=0, frame_end=600, epoch_start=0.0, epoch_end=30.0)
    dsp = {"rej_reason": ""}
    est_invalid = WindowEstimate(
        estimator_id="e", run_config_hash="h", hr_bpm=float("nan"), hr_valid=False,
        br_bpm=float("nan"), br_valid=False,
    )
    row = so.score_window(
        w, dsp, est_invalid, "other_rejected", _hr_ref_admitted(), _br_ref_admitted(),
        frame0_epoch=0.0, session_type="natural", paced_schedule=None, paced_unavailable_reason=None,
    )
    assert np.isnan(row["hr_error_bpm"])

    not_admitted_ref = dict(_hr_ref_admitted())
    not_admitted_ref["admitted"] = False
    est_valid = WindowEstimate(
        estimator_id="e", run_config_hash="h", hr_bpm=72.0, hr_valid=True,
        br_bpm=float("nan"), br_valid=False,
    )
    row2 = so.score_window(
        w, dsp, est_valid, "covered", not_admitted_ref, _br_ref_admitted(),
        frame0_epoch=0.0, session_type="natural", paced_schedule=None, paced_unavailable_reason=None,
    )
    assert np.isnan(row2["hr_error_bpm"])


# ── Masimo CSV auto-discovery (OSR-11) ───────────────────────────────────────

@_REQUIRE_REAL_DATA
def test_discover_masimo_csv_skips_live_estimates():
    path = so.discover_masimo_csv(MASSIMO1, override=None)
    assert path.name == "demo_massimo1.csv"


@_REQUIRE_REAL_DATA
def test_discover_masimo_csv_override():
    override_path = MASSIMO1 / "demo_massimo1.csv"
    path = so.discover_masimo_csv(MASSIMO1, override=str(override_path))
    assert path == override_path


# ── Lock resolution (OSR-03 R2/R3/round 4) ──────────────────────────────────

@_REQUIRE_REAL_DATA
def test_resolve_pinned_lock_manual_int():
    lock = so.resolve_pinned_lock("cap", "deadbeef", "27", False, {}, {})
    assert lock.kind == "manual"
    assert lock.locked_bin == 27


@_REQUIRE_REAL_DATA
@_REQUIRE_REPLAY_PROD
def test_resolve_pinned_lock_directory_correct():
    raw_sha256 = so.sha256_file(MASSIMO1 / "adc_stream.bin")
    lock = so.resolve_pinned_lock(
        MASSIMO1.name, raw_sha256, str(REPLAY_MASSIMO1_PROD),
        isolate_fields_active=True,
        reproduction_baseline_eca_mode={MASSIMO1.name: "skip_forbidden_harmonics_v1"},
        reproduction_baseline_lock={MASSIMO1.name: "27"},
    )
    assert lock.kind == "directory"
    assert lock.locked_bin == 27


@_REQUIRE_REAL_DATA
@_REQUIRE_REPLAY_PROD
def test_resolve_pinned_lock_rejects_unrelated_raw_hash():
    """OSR-03 R2: a directory not hash-bound to the scored capture is rejected."""
    wrong_raw_sha256 = "0" * 64
    with pytest.raises(ValueError, match="OSR-03 R2"):
        so.resolve_pinned_lock(
            MASSIMO1.name, wrong_raw_sha256, str(REPLAY_MASSIMO1_PROD),
            isolate_fields_active=False, reproduction_baseline_eca_mode={},
            reproduction_baseline_lock={},
        )


@_REQUIRE_REAL_DATA
def test_resolve_pinned_lock_rejects_different_eca_mode_same_hash_same_lock():
    """OSR-03 R3: massimo1's real guard_cardiac_candidate_v1 replay shares the raw
    hash AND numeric lock (27) with the production replay, but was generated under
    a different eca_mode — must be rejected when --reproduction-baseline-eca-mode
    names the production value."""
    if not REPLAY_MASSIMO1_GUARD.exists():
        pytest.skip("20260727_182319_replay_unknown fixture not present")
    raw_sha256 = so.sha256_file(MASSIMO1 / "adc_stream.bin")
    with pytest.raises(ValueError, match="OSR-03 R3"):
        so.resolve_pinned_lock(
            MASSIMO1.name, raw_sha256, str(REPLAY_MASSIMO1_GUARD),
            isolate_fields_active=True,
            reproduction_baseline_eca_mode={MASSIMO1.name: "skip_forbidden_harmonics_v1"},
            reproduction_baseline_lock={MASSIMO1.name: "27"},
        )


@_REQUIRE_REAL_DATA
def test_resolve_pinned_lock_rejects_wrong_lock_value_round4():
    """OSR-03 round 4: the ORIGINAL massimo1 live directory (lock 23, same
    production eca_mode, trivially self-hash-bound) must still be rejected when
    --reproduction-baseline-lock names 27 (the actual measured lock, from the
    2026-07-26 replay generation)."""
    raw_sha256 = so.sha256_file(MASSIMO1 / "adc_stream.bin")
    with pytest.raises(ValueError, match="OSR-03 round 4"):
        so.resolve_pinned_lock(
            MASSIMO1.name, raw_sha256, str(MASSIMO1),  # original live dir as its own lock source
            isolate_fields_active=True,
            reproduction_baseline_eca_mode={MASSIMO1.name: "skip_forbidden_harmonics_v1"},
            reproduction_baseline_lock={MASSIMO1.name: "27"},
        )


@_REQUIRE_REAL_DATA
@_REQUIRE_REPLAY_PROD
def test_resolve_pinned_lock_isolate_active_requires_baseline_eca_mode():
    raw_sha256 = so.sha256_file(MASSIMO1 / "adc_stream.bin")
    with pytest.raises(ValueError, match="OSR-03 R3"):
        so.resolve_pinned_lock(
            MASSIMO1.name, raw_sha256, str(REPLAY_MASSIMO1_PROD),
            isolate_fields_active=True, reproduction_baseline_eca_mode={},
            reproduction_baseline_lock={},
        )


# ── OSR-03 lock resolution, portable (no real data) ──────────────────────────
# These mirror the real-data tests above so the OSR-03 guard rails stay covered in
# environments where the transient replay artifacts are absent.

_FAKE_RAW = "a" * 64


def test_resolve_pinned_lock_directory_binds_hash_and_lock_portable(tmp_path: Path):
    source = _write_lock_source(tmp_path / "replay", raw_sha256=_FAKE_RAW)
    lock = so.resolve_pinned_lock(
        "cap", _FAKE_RAW, str(source),
        isolate_fields_active=True,
        reproduction_baseline_eca_mode={"cap": "skip_forbidden_harmonics_v1"},
        reproduction_baseline_lock={"cap": "27"},
    )
    assert lock.kind == "directory"
    assert lock.locked_bin == 27


def test_resolve_pinned_lock_rejects_unrelated_raw_hash_portable(tmp_path: Path):
    """OSR-03 R2: a directory not hash-bound to the scored capture is rejected."""
    source = _write_lock_source(tmp_path / "replay", raw_sha256=_FAKE_RAW)
    with pytest.raises(ValueError, match="OSR-03 R2"):
        so.resolve_pinned_lock(
            "cap", "0" * 64, str(source),
            isolate_fields_active=False, reproduction_baseline_eca_mode={},
            reproduction_baseline_lock={},
        )


def test_resolve_pinned_lock_live_mode_rejects_unrelated_mirror_hash_portable(
    tmp_path: Path,
):
    """OSR-03 R2 on the `mode="live"` branch, which the real-data tests never reach."""
    source = _write_lock_source(tmp_path / "live", raw_sha256=_FAKE_RAW, mode="live")
    with pytest.raises(ValueError, match="OSR-03 R2"):
        so.resolve_pinned_lock(
            "cap", "0" * 64, str(source),
            isolate_fields_active=False, reproduction_baseline_eca_mode={},
            reproduction_baseline_lock={},
        )


def test_resolve_pinned_lock_isolate_active_requires_baseline_eca_mode_portable(
    tmp_path: Path,
):
    source = _write_lock_source(tmp_path / "replay", raw_sha256=_FAKE_RAW)
    with pytest.raises(ValueError, match="OSR-03 R3"):
        so.resolve_pinned_lock(
            "cap", _FAKE_RAW, str(source),
            isolate_fields_active=True, reproduction_baseline_eca_mode={},
            reproduction_baseline_lock={},
        )


def test_resolve_pinned_lock_missing_metadata_is_rejected_portable(tmp_path: Path):
    empty = tmp_path / "no_metadata"
    empty.mkdir()
    with pytest.raises(ValueError, match="no run_metadata.json"):
        so.resolve_pinned_lock(
            "cap", _FAKE_RAW, str(empty),
            isolate_fields_active=False, reproduction_baseline_eca_mode={},
            reproduction_baseline_lock={},
        )


# ── --isolate-fields / --estimands CLI-level validation (no real data needed) ─

def test_main_isolate_fields_requires_exactly_two_configs():
    with pytest.raises(ValueError, match="EXACTLY TWO"):
        so.main([
            "--captures", "fake_capture",
            "--configs", "a=fake_a.yaml",
            "--session-type", "fake_capture=natural",
            "--isolate-fields", "heart.eca_mode",
        ])


def test_main_isolate_fields_requires_estimands_both():
    with pytest.raises(ValueError, match="OSR-04 R2"):
        so.main([
            "--captures", "fake_capture",
            "--configs", "a=fake_a.yaml", "b=fake_b.yaml",
            "--session-type", "fake_capture=natural",
            "--isolate-fields", "heart.eca_mode",
            "--estimands", "pinned",
        ])


def test_main_pinned_estimand_requires_lock_source_for_every_capture():
    with pytest.raises(ValueError, match="pinned-lock-source"):
        so.main([
            "--captures", "fake_capture",
            "--configs", "a=fake_a.yaml",
            "--session-type", "fake_capture=natural",
            "--estimands", "pinned",
        ])


def test_main_duplicate_capture_basenames_raise():
    with pytest.raises(ValueError, match="duplicate"):
        so.main([
            "--captures", "dirA/fake_capture", "dirB/fake_capture",
            "--configs", "a=fake_a.yaml",
            "--session-type", "fake_capture=natural",
            "--estimands", "rerun",
        ])


def test_main_dirty_tree_refuses_without_allow_dirty(monkeypatch):
    monkeypatch.setattr(so, "is_tree_clean", lambda: False)
    with pytest.raises(SystemExit):
        so.main([
            "--captures", "fake_capture",
            "--configs", "a=fake_a.yaml",
            "--session-type", "fake_capture=natural",
            "--estimands", "rerun",
        ])


def test_main_dirty_tree_with_allow_dirty_proceeds_past_the_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(so, "is_tree_clean", lambda: False)
    monkeypatch.setattr(so, "get_git_commit", lambda: "deadbeef")
    # It should get past the dirty-tree gate and fail later trying to open the
    # (nonexistent) capture's real files — never a SystemExit from the gate.
    with pytest.raises(Exception) as excinfo:
        so.main([
            "--captures", "fake_capture",
            "--configs", "a=fake_a.yaml",
            "--session-type", "fake_capture=natural",
            "--estimands", "rerun",
            "--allow-dirty",
            "--out", str(tmp_path / "score_offline_scratch"),
        ])
    assert not isinstance(excinfo.value, SystemExit)


# ── End-to-end real-capture run (small capture, both estimands, single config) ─

@_REQUIRE_REAL_DATA
def test_end_to_end_real_capture_rerun_estimand(tmp_path):
    out_root = tmp_path / "score_offline_out"
    so.main([
        "--captures", str(MASSIMO2),
        "--configs", f"production={REPO_ROOT / 'scripts' / 'live_demo_config.yaml'}",
        "--estimands", "rerun",
        "--session-type", f"{MASSIMO2.name}=paced",
        "--paced-schedule", f"{MASSIMO2.name}=16.0",
        "--allow-dirty",
        "--out", str(out_root),
    ])
    # --out is a ROOT: a run_id timestamp subdirectory is always appended
    # (CLAUDE.md §3 rule 5) — there must be exactly one, this run's own.
    run_dirs = list(out_root.iterdir())
    assert len(run_dirs) == 1
    out_dir = run_dirs[0]
    triple_dir = out_dir / MASSIMO2.name / "production" / "rerun"
    assert (triple_dir / "window_scores.csv").exists()
    assert (triple_dir / "evidence.npz").exists()
    assert (triple_dir / "summary.json").exists()

    import pandas as pd
    df = pd.read_csv(triple_dir / "window_scores.csv")
    assert list(df.columns) == so.WINDOW_SCORES_COLUMNS
    assert len(df) == 6  # 180 s / 30 s
    assert df["br_session_type"].eq("paced").all()
    assert df["br_metronome_status"].eq("available").all()
    assert df["comparator_status"].eq("exploratory_non_frozen").all()
    assert df["origin_is_approximate"].eq(True).all()

    evidence = np.load(triple_dir / "evidence.npz", allow_pickle=True)
    assert len(evidence["k"]) == 6
    assert evidence["locked_bin"].shape == (1,)

    summary = json.loads((triple_dir / "summary.json").read_text())
    assert summary["comparator_status"] == "exploratory_non_frozen"
    assert summary["origin_is_approximate"] is True
    assert summary["lock_provenance"]["kind"] == "rerun_warmup"
    assert summary["hr_reference_marginal"]["n_windows"] == 6
    assert summary["reproducible"] is False  # --allow-dirty was used above


@_REQUIRE_REAL_DATA
def test_end_to_end_structural_slice_equivalence():
    """OSR-14 replacement check: cube[frame_start:frame_end] fed to run_window_dsp
    as a plain ndarray slice vs. a collections.deque of the same frames produces
    bit-identical output (run_window_dsp's own "both stack identically" claim)."""
    import collections

    from src.radar_io import ChirpConfig, read_adc_bin
    from src.window_pipeline import run_window_dsp
    import yaml

    meta = json.loads((MASSIMO2 / "run_metadata.json").read_text())
    cfg = yaml.safe_load((REPO_ROOT / "scripts" / "live_demo_config.yaml").read_text())
    chirp_cfg = so.validate_decode_geometry(cfg, meta, MASSIMO2.name)
    cube = read_adc_bin(MASSIMO2 / "adc_stream.bin", chirp_cfg)

    frame_start, frame_end = 0, 600
    sliced = cube[frame_start:frame_end]
    assert sliced.shape == (600, cfg["profile"]["num_chirps_per_frame"], cfg["profile"]["num_rx"], cfg["profile"]["num_adc_samples"])
    assert np.array_equal(np.stack(list(sliced)), sliced)

    locked_bin = int(meta["locked_bin"])
    dsp_array = run_window_dsp(sliced, locked_bin, chirp_cfg.frame_rate_hz, cfg)
    dsp_deque = run_window_dsp(collections.deque(sliced), locked_bin, chirp_cfg.frame_rate_hz, cfg)
    assert dsp_array["hr_raw"] == dsp_deque["hr_raw"] or (
        np.isnan(dsp_array["hr_raw"]) and np.isnan(dsp_deque["hr_raw"])
    )
    assert np.array_equal(dsp_array["phase_clean"], dsp_deque["phase_clean"])
