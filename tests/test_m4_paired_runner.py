"""Portable end-to-end controls for the M3 paired runner."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from src.m4.bundle import BundleWriter, read_manifest, sha256_path
from src.m4.capture_registry import RadarCapture, RadarScope
from src.m4.estimator_runner import (
    CANONICAL_ARM_IDS,
    LOCK_ESTIMANDS,
    PreflightError,
    RunnerContractError,
    SelectorContractError,
    execute_paired_runner,
    run_radar_stage,
)
from src.m4.estimator_suite import EstimatorArmSpec, SuiteWindowResult, freeze_array
from src.m4.production_suite import PRODUCTION_ARM_ID
from src.m4.evidence_serialization import pack_ahmed_evidence, serialize_native_tree
from src.m4.estimator_scoring import validate_radar_rows
from src.m8.ahmed_transfer import (
    APPROVED_ARM_IDS,
    REAL_REPRESENTATIVE_DOMAIN,
    AhmedPhaseConfig,
    AhmedPhaseEstimatorSuite,
)
from src.respiration import extract_chest_phase
from src.window_pipeline import run_config_hash


def _fixture(tmp_path: Path):
    run_dir = tmp_path / "capture"
    run_dir.mkdir(parents=True)
    config = {
        "profile": {
            "num_adc_samples": 4,
            "num_rx": 1,
            "num_chirps_per_frame": 1,
            "iq_swap": True,
            "range_resolution_m": 0.1,
        },
        "session": {"frame_rate_hz": 20.0},
        "bin_selection": {"candidate_bins": [1]},
        "protocol": {"subject_distance_m": [0.1, 0.1]},
    }
    metadata = {
        "config": config,
        "iq_swap": True,
        "locked_bin": 1,
        "start_wall_utc": "2026-01-01T00:00:00+00:00",
    }
    warmup = {"selected_bin": 1}
    (run_dir / "run_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (run_dir / "warmup_bin_selection.json").write_text(json.dumps(warmup), encoding="utf-8")
    # 600 frames x 1 chirp x 1 RX x 4 complex-int16 samples.
    (run_dir / "adc_stream.bin").write_bytes(bytes(600 * 16))
    capture = RadarCapture(
        capture_id="m1",
        directory="capture",
        frames=600,
        windows=1,
        tail_frames=0,
        recorded_lock=1,
        rerun_lock=1,
        capture_config_sha256=run_config_hash(config),
        adc_stream_sha256=sha256_path(run_dir / "adc_stream.bin"),
        metadata_sha256=sha256_path(run_dir / "run_metadata.json"),
        warmup_sha256=sha256_path(run_dir / "warmup_bin_selection.json"),
    )
    radar = RadarScope(
        root=tmp_path,
        captures={"m1": capture},
        geometry={
            "adc_samples": 4,
            "rx": 1,
            "chirps_per_frame": 1,
            "sample_dtype": "complex_int16",
            "bytes_per_frame": 16,
            "iq_swap": True,
            "frame_rate_hz": 20.0,
            "range_resolution_m_approx": 0.1,
        },
        window_grid={"frames_per_window": 600, "total_windows": 1},
    )
    return radar


def _full_fixture(tmp_path: Path) -> RadarScope:
    """Portable eight-capture grid with the plan's exact 128-window cardinality."""
    layouts = {
        "m1": (3610, 6, 10),
        "m2": (3611, 6, 11),
        "sweep": (9611, 16, 11),
        "m3": (12005, 20, 5),
        "m4": (12002, 20, 2),
        "m5": (12005, 20, 5),
        "m6": (12003, 20, 3),
        "m7": (12020, 20, 20),
    }
    config = {
        "profile": {
            "num_adc_samples": 4,
            "num_rx": 1,
            "num_chirps_per_frame": 1,
            "iq_swap": True,
            "range_resolution_m": 0.1,
        },
        "session": {"frame_rate_hz": 20.0},
        "bin_selection": {"candidate_bins": [1]},
        "protocol": {"subject_distance_m": [0.1, 0.1]},
    }
    captures = {}
    for capture_id, (frames, windows, tail) in layouts.items():
        directory = tmp_path / capture_id
        directory.mkdir(parents=True)
        metadata = {
            "config": config,
            "iq_swap": True,
            "locked_bin": 1,
            "start_wall_utc": "2026-01-01T00:00:00+00:00",
        }
        warmup = {"selected_bin": 1}
        (directory / "run_metadata.json").write_text(
            json.dumps(metadata), encoding="utf-8"
        )
        (directory / "warmup_bin_selection.json").write_text(
            json.dumps(warmup), encoding="utf-8"
        )
        (directory / "adc_stream.bin").write_bytes(bytes(frames * 16))
        captures[capture_id] = RadarCapture(
            capture_id=capture_id,
            directory=capture_id,
            frames=frames,
            windows=windows,
            tail_frames=tail,
            recorded_lock=1,
            rerun_lock=1,
            capture_config_sha256=run_config_hash(config),
            adc_stream_sha256=sha256_path(directory / "adc_stream.bin"),
            metadata_sha256=sha256_path(directory / "run_metadata.json"),
            warmup_sha256=sha256_path(directory / "warmup_bin_selection.json"),
        )
    return RadarScope(
        root=tmp_path,
        captures=captures,
        geometry={
            "adc_samples": 4,
            "rx": 1,
            "chirps_per_frame": 1,
            "sample_dtype": "complex_int16",
            "bytes_per_frame": 16,
            "iq_swap": True,
            "frame_rate_hz": 20.0,
            "range_resolution_m_approx": 0.1,
        },
        window_grid={
            "frames_per_window": 600,
            "total_windows": 128,
            "evaluation_k_ge_1_windows": 120,
        },
    )


def _native(
    arm_id: str,
    config_hash: str,
    evidence=None,
    breath_evidence=None,
    phase_raw=None,
):
    return {
        "arm_id": arm_id,
        "estimator_id": "eca_ahet_v1" if arm_id == PRODUCTION_ARM_ID else "ahmed_fixed_h_phase_v1",
        "run_config_hash": config_hash,
        "hr_valid": evidence is not None,
        "hr_raw": 48.0 if evidence is not None else None,
        "br_valid": True,
        "br_bpm": 16.0,
        "br_confidence": "high",
        "rej_reason": "" if evidence is not None else "expected_invalid",
        "f_r_hz": 0.25,
        "heart_evidence": evidence,
        "breath_evidence": breath_evidence or {"selected_bin": 8},
        **({"phase_raw": phase_raw} if phase_raw is not None else {}),
    }


class FakeProduction:
    suite_id = "production_eca_ahet_suite_v1"
    suite_config_hash = "p" * 64
    arm_specs = (EstimatorArmSpec(PRODUCTION_ARM_ID, "eca_ahet_v1", "p" * 64),)
    outcome_classifiers = {}

    def __init__(self, seen):
        self.seen = seen

    def __call__(self, frames, locked_bin, fs):
        self.seen.append((id(frames), frames.shape, locked_bin))
        # The production and Ahmed suites independently reconstruct the same raw phase.
        phase = np.linspace(0.0, 1.0, 600)
        return SuiteWindowResult(
            {},
            {PRODUCTION_ARM_ID: _native(PRODUCTION_ARM_ID, "p" * 64, phase_raw=phase)},
        )


class FakeAhmed:
    suite_id = "ahmed_fixed_h_phase_suite_v1"
    suite_config_hash = "a" * 64
    arm_specs = tuple(
        EstimatorArmSpec(arm, "ahmed_fixed_h_phase_v1", f"{i + 1:064x}")
        for i, arm in enumerate(APPROVED_ARM_IDS)
    )
    outcome_classifiers = {}

    def __init__(self, seen):
        self.seen = seen

    def __call__(self, frames, locked_bin, fs):
        self.seen.append((id(frames), frames.shape, locked_bin))
        phase = freeze_array(np.linspace(0.0, 1.0, 600))
        frequencies = freeze_array(np.fft.rfftfreq(600, 1 / fs))
        spectrum = freeze_array(np.abs(np.fft.rfft(phase)))
        candidates = freeze_array(np.array([24, 25], dtype=np.int64))
        evidence = {
            "candidate_bins": candidates,
            "harmonic_bins": freeze_array(np.array([[24, 48, 72], [25, 50, 75]])),
            "supported_mask": freeze_array(np.array([True, True])),
            "nyquist_degenerate_mask": freeze_array(np.array([False, False])),
            "suppression_mask": freeze_array(np.array([True, True])),
            "eligible_mask": freeze_array(np.array([True, True])),
            "scores_pre_exclusion": freeze_array(np.array([2.0, 1.0])),
            "scores": freeze_array(np.array([2.0, 1.0])),
            "selected_bin": 24,
            "selected_hz": 0.8,
            "selected_score": 2.0,
            "runner_up_bin": 25,
            "runner_up_hz": 25 / 30,
            "runner_up_score": 1.0,
        }
        breath_evidence = {
            **evidence,
            "candidate_bins": freeze_array(np.array([6, 8], dtype=np.int64)),
            "harmonic_bins": freeze_array(np.array([[6, 12, 18], [8, 16, 24]])),
            "scores_pre_exclusion": freeze_array(np.array([1.0, 2.0])),
            "scores": freeze_array(np.array([1.0, 2.0])),
            "selected_bin": 8,
            "selected_hz": 8 / 30,
            "runner_up_bin": 6,
            "runner_up_hz": 0.2,
        }
        native = {
            spec.arm_id: _native(
                spec.arm_id,
                spec.run_config_hash,
                evidence,
                breath_evidence=breath_evidence,
            )
            for spec in self.arm_specs
        }
        return SuiteWindowResult(
            {
                "phase": phase,
                "spectrum_frequencies_hz": frequencies,
                "spectrum_magnitude": spectrum,
                "heart_candidate_bins": candidates,
                "n_fft": 600,
            },
            native,
        )


def test_one_decode_selector_once_common_window_and_exact_cartesian(tmp_path):
    radar = _fixture(tmp_path)
    decoded = {"n": 0}
    selected = {"n": 0}
    seen = []
    cube = np.zeros((600, 1, 1, 4), dtype=np.complex64)

    def decode(path, chirp):
        decoded["n"] += 1
        return cube

    def selector(window, candidates, config, fs):
        selected["n"] += 1
        return 1, {"must_not_be_reused": True}, {
            "candidates": [{"bin": 1, "failed": False}],
            "fallback_used": False,
        }

    result = execute_paired_runner(
        radar=radar,
        capture_ids=["m1"],
        suites=[FakeProduction(seen), FakeAhmed(seen)],
        run_id="portable",
        source_hash="s" * 64,
        decode_fn=decode,
        selector_fn=selector,
    )
    assert decoded["n"] == selected["n"] == 1
    assert result.estimator_count == 14
    assert result.shared_count == 2
    assert len(result.ahmed_evidence) == 12
    assert len(result.production_native) == 2
    assert {row["arm_id"] for row in result.rows} == set(CANONICAL_ARM_IDS)
    assert {row["lock_estimand_id"] for row in result.rows} == {
        "recorded_lock_as_captured", "current_production_rerun_lock"
    }
    assert {row["time_origin_id"] for row in result.rows} == {
        "start_wall_utc_approximate_v1"
    }
    assert {row["window_set"] for row in result.rows} == {"full_k0_diagnostic"}
    assert {row["window_origin_role"] for row in result.rows} == {
        "lock_selection_in_sample"
    }
    assert {row["schema_version"] for row in result.rows} == {2}
    assert all((row["hr_validity_reason"] == "ok") == row["hr_valid"] for row in result.rows)
    assert all((row["br_validity_reason"] == "ok") == row["br_valid"] for row in result.rows)
    assert all(row["validity_reason"] == row["hr_validity_reason"] for row in result.rows)
    # Within a lock/window both suites receive the exact same slice object.
    assert seen[0][0] == seen[1][0] and seen[2][0] == seen[3][0]
    assert all(row["native"].get("must_not_be_reused") is None for row in result.production_native)
    packed = pack_ahmed_evidence(result.ahmed_evidence)
    for vital in ("breath", "heart"):
        for packed_row in range(len(result.ahmed_evidence)):
            present = packed[f"{vital}_candidate_present"][packed_row]
            candidates = packed[f"{vital}_candidate_bins"][packed_row, present]
            scores = packed[f"{vital}_score_post_suppression"][packed_row, present]
            assert packed[f"{vital}_selected_frequency_hz_present"][packed_row]
            assert packed[f"{vital}_runner_up_frequency_hz_present"][packed_row]
            assert packed[f"{vital}_reported_bpm_present"][packed_row]
            selected = packed[f"{vital}_selected_bin"][packed_row]
            runner_up = packed[f"{vital}_runner_up_bin"][packed_row]
            selected_index = np.flatnonzero(candidates == selected)[0]
            runner_up_index = np.flatnonzero(candidates == runner_up)[0]
            assert scores[selected_index] == packed[f"{vital}_selected_score"][packed_row]
            assert scores[runner_up_index] == packed[f"{vital}_runner_up_score"][packed_row]
            assert packed[f"{vital}_reported_bpm"][packed_row] == pytest.approx(
                60.0 * packed[f"{vital}_selected_frequency_hz"][packed_row]
            )


def test_full_portable_run_has_exact_counts_keys_and_same_cell_identity(tmp_path):
    radar = _full_fixture(tmp_path)
    decoded: list[str] = []
    selected: list[str] = []

    def decode(path, chirp):
        decoded.append(Path(path).parent.name)
        return np.zeros(
            (
                chirp.num_frames,
                chirp.num_chirps_per_frame,
                chirp.num_rx,
                chirp.num_adc_samples,
            ),
            dtype=np.complex64,
        )

    def selector(window, candidates, config, fs):
        selected.append("call")
        return 1, {"payload_must_not_be_reused": True}, {
            "candidates": [{"bin": 1, "failed": False}],
            "fallback_used": False,
        }

    result = execute_paired_runner(
        radar=radar,
        capture_ids=list(radar.captures),
        suites=[FakeProduction([]), FakeAhmed([])],
        run_id="full-portable",
        source_hash="s" * 64,
        decode_fn=decode,
        selector_fn=selector,
    )

    assert len(decoded) == len(set(decoded)) == 8
    assert len(selected) == 8
    assert result.source_span_count == 128
    assert result.shared_count == 256
    assert result.estimator_count == 1792
    assert len(result.ahmed_evidence) == 1536
    assert len(result.production_native) == 256
    comparative = result.evaluation_k_ge_1
    assert comparative.source_span_count == 120
    assert comparative.shared_count == 240
    assert comparative.estimator_count == 1680

    estimator_keys = [
        (row["capture_id"], row["lock_estimand_id"], row["k"], row["arm_id"])
        for row in result.rows
    ]
    shared_keys = [
        (row["capture_id"], row["lock_estimand_id"], row["k"])
        for row in result.shared_evidence
    ]
    assert len(estimator_keys) == len(set(estimator_keys)) == 1792
    assert len(shared_keys) == len(set(shared_keys)) == 256

    for shared_key in shared_keys:
        cell = [
            row
            for row in result.rows
            if (row["capture_id"], row["lock_estimand_id"], row["k"]) == shared_key
        ]
        assert {row["arm_id"] for row in cell} == set(CANONICAL_ARM_IDS)
        for identity_field in (
            "run_id",
            "run_hash",
            "source_hash",
            "capture_id",
            "lock_estimand_id",
            "locked_bin",
            "k",
            "frame_start",
            "frame_stop",
            "cube_hash",
            "window_cube_hash",
            "shared_signal_hash",
            "capture_config_hash",
        ):
            assert len({row[identity_field] for row in cell}) == 1
        if shared_key[2] == 0:
            assert {row["window_set"] for row in cell} == {"full_k0_diagnostic"}
            assert {row["window_origin_role"] for row in cell} == {
                "lock_selection_in_sample"
            }
        else:
            assert {row["window_set"] for row in cell} == {"evaluation_k_ge_1"}
            assert {row["window_origin_role"] for row in cell} == {"evaluation"}
        assert {row["time_origin_id"] for row in cell} == {
            "start_wall_utc_approximate_v1"
        }


def _run_with(tmp_path, *, decode=None, selector=None, production=None):
    radar = _fixture(tmp_path)
    cube = np.zeros((600, 1, 1, 4), dtype=np.complex64)
    default_selector = lambda *args: (  # noqa: E731
        1,
        None,
        {"candidates": [{"bin": 1, "failed": False}], "fallback_used": False},
    )
    return execute_paired_runner(
        radar=radar,
        capture_ids=["m1"],
        suites=[production or FakeProduction([]), FakeAhmed([])],
        run_id="fault",
        source_hash="s" * 64,
        decode_fn=decode or (lambda path, chirp: cube),
        selector_fn=selector or default_selector,
    )


class ProductionNoGateInvalid(FakeProduction):
    """Faithful extracted native contract observed for m1/rerun-lock/bin27/k1."""

    def __init__(
        self,
        explicit_hr_reason="",
        respiration_hz=None,
        *,
        hr_result_update=None,
        remove_hr_result=False,
    ):
        super().__init__([])
        self.explicit_hr_reason = explicit_hr_reason
        self.respiration_hz = respiration_hz
        self.hr_result_update = hr_result_update
        self.remove_hr_result = remove_hr_result
        self.serialized_before_return = []

    def __call__(self, frames, locked_bin, fs):
        result = super().__call__(frames, locked_bin, fs)
        native = dict(result.arm_native_results[PRODUCTION_ARM_ID])
        native.update(
            hr_valid=False,
            hr_raw=np.nan,
            rej_reason=self.explicit_hr_reason,
            f_r_hz=self.respiration_hz,
            hr_result={
                "accepted_candidate_rank": -1,
                "candidate_rejection_code": np.array([-1, -1, -1], dtype=np.int64),
            },
            br_valid=False,
            br_bpm=np.nan,
            br_confidence="low",
            br_result={"resp_edge_veto_reason": "band_edge_bin"},
        )
        if self.hr_result_update is not None:
            native["hr_result"] = self.hr_result_update
        if self.remove_hr_result:
            native.pop("hr_result")
        # The production payload has no Ahmed-style breath_evidence mapping.
        native.pop("breath_evidence", None)
        snapshot = serialize_native_tree(native)
        self.serialized_before_return.append(
            (
                snapshot.index,
                {key: value.copy() for key, value in snapshot.arrays.items()},
            )
        )
        return SuiteWindowResult(
            result.shared_evidence,
            {PRODUCTION_ARM_ID: native},
            {PRODUCTION_ARM_ID: "gate_not_run"},
        )


@pytest.mark.parametrize("respiration_hz", [None, 0.1168, 0.61])
def test_production_no_gate_native_invalidity_retains_separate_reasons(
    tmp_path, respiration_hz
):
    production = ProductionNoGateInvalid(respiration_hz=respiration_hz)
    result = _run_with(tmp_path, production=production)
    production_rows = [
        row for row in result.rows if row["arm_id"] == PRODUCTION_ARM_ID
    ]

    assert len(production_rows) == 2
    assert {row["hr_validity_reason"] for row in production_rows} == {
        "gate_not_run"
    }
    assert {row["br_validity_reason"] for row in production_rows} == {
        "band_edge_bin"
    }
    assert all(row["validity_reason"] == "gate_not_run" for row in production_rows)
    assert {row["outcome"] for row in production_rows} == {"gate_not_run"}

    # Normalization and serialization must not rewrite the production-native payload.
    assert len(production.serialized_before_return) == len(result.production_native) == 2
    for before, record in zip(
        production.serialized_before_return, result.production_native, strict=True
    ):
        after = serialize_native_tree(record["native"])
        before_index, before_arrays = before
        assert after.index == before_index
        assert set(after.arrays) == set(before_arrays)
        for key, before_array in before_arrays.items():
            assert after.arrays[key].dtype == before_array.dtype
            assert after.arrays[key].shape == before_array.shape
            assert np.array_equal(after.arrays[key], before_array, equal_nan=True)
        assert record["native"]["rej_reason"] == ""
        assert np.isnan(record["native"]["hr_raw"])


def test_explicit_native_hr_reason_precedes_no_gate_normalization(tmp_path):
    result = _run_with(
        tmp_path,
        production=ProductionNoGateInvalid("producer_specific_rejection"),
    )
    production_rows = [
        row for row in result.rows if row["arm_id"] == PRODUCTION_ARM_ID
    ]

    assert {row["hr_validity_reason"] for row in production_rows} == {
        "producer_specific_rejection"
    }
    assert {row["br_validity_reason"] for row in production_rows} == {
        "band_edge_bin"
    }


@pytest.mark.parametrize("respiration_hz", [0.15, 0.25, 0.60])
def test_no_gate_reason_is_not_applied_to_contradictory_in_gate_evidence(
    tmp_path, respiration_hz
):
    with pytest.raises(
        RunnerContractError,
        match="invalid HR estimate is missing its native rejection reason",
    ):
        _run_with(
            tmp_path,
            production=ProductionNoGateInvalid(respiration_hz=respiration_hz),
        )


@pytest.mark.parametrize(
    "hr_result",
    [
        {"accepted_candidate_rank": 0,
         "candidate_rejection_code": np.array([-1, -1, -1], dtype=np.int64)},
        {"accepted_candidate_rank": -2,
         "candidate_rejection_code": np.array([-1, -1, -1], dtype=np.int64)},
        {"accepted_candidate_rank": np.int64(-1),
         "candidate_rejection_code": np.array([-1, -1, -1], dtype=np.int64)},
        {"accepted_candidate_rank": -1,
         "candidate_rejection_code": np.array([-1, -1], dtype=np.int64)},
        {"accepted_candidate_rank": -1,
         "candidate_rejection_code": np.array([-1, -1, -1], dtype=np.float64)},
        {"accepted_candidate_rank": -1,
         "candidate_rejection_code": np.array([-1, -1, -1], dtype=np.bool_)},
        {"accepted_candidate_rank": -1,
         "candidate_rejection_code": np.array([-1, 2, -1], dtype=np.int64)},
        {"accepted_candidate_rank": -1,
         "candidate_rejection_code": [-1, -1, -1]},
        {"accepted_candidate_rank": -1,
         "candidate_rejection_code": [[-1], [-1, -1]]},
        {},
        {"accepted_candidate_rank": -1},
        {"candidate_rejection_code": np.array([-1, -1, -1], dtype=np.int64)},
        [],
        "not-a-mapping",
    ],
    ids=[
        "accepted-rank",
        "rank-out-of-domain",
        "numpy-rank",
        "wrong-shape",
        "float-dtype",
        "bool-dtype",
        "mixed-codes",
        "list-not-native-array",
        "ragged-codes",
        "missing-both-fields",
        "missing-codes",
        "missing-rank",
        "list-hr-result",
        "string-hr-result",
    ],
)
def test_no_gate_reason_rejects_nonproducer_hr_result_states(tmp_path, hr_result):
    with pytest.raises(
        RunnerContractError,
        match="invalid HR estimate is missing its native rejection reason",
    ):
        _run_with(
            tmp_path,
            production=ProductionNoGateInvalid(hr_result_update=hr_result),
        )


def test_no_gate_reason_rejects_missing_hr_result(tmp_path):
    with pytest.raises(
        RunnerContractError,
        match="invalid HR estimate is missing its native rejection reason",
    ):
        _run_with(
            tmp_path,
            production=ProductionNoGateInvalid(remove_hr_result=True),
        )


@pytest.mark.parametrize(
    "respiration_hz",
    [float("nan"), float("inf"), float("-inf"), "0.1168", np.float64(0.1168), True],
    ids=["nan", "positive-inf", "negative-inf", "string", "numpy-scalar", "bool"],
)
def test_no_gate_reason_does_not_coerce_malformed_respiration_values(
    tmp_path, respiration_hz
):
    with pytest.raises(RunnerContractError, match="f_r_hz must be finite or null"):
        _run_with(
            tmp_path,
            production=ProductionNoGateInvalid(respiration_hz=respiration_hz),
        )


def test_ahmed_invalidity_cannot_borrow_the_production_no_gate_reason(tmp_path):
    class AhmedWithProductionShapedInvalidity(FakeAhmed):
        def __call__(self, frames, locked_bin, fs):
            result = super().__call__(frames, locked_bin, fs)
            native = {
                arm_id: dict(payload)
                for arm_id, payload in result.arm_native_results.items()
            }
            native[APPROVED_ARM_IDS[0]].update(
                hr_valid=False,
                hr_raw=None,
                rej_reason="",
                f_r_hz=None,
                hr_result={
                    "accepted_candidate_rank": -1,
                    "candidate_rejection_code": np.array(
                        [-1, -1, -1], dtype=np.int64
                    ),
                },
            )
            return SuiteWindowResult(result.shared_evidence, native)

    radar = _fixture(tmp_path)
    cube = np.zeros((600, 1, 1, 4), dtype=np.complex64)
    with pytest.raises(
        RunnerContractError,
        match="invalid HR estimate is missing its native rejection reason",
    ):
        execute_paired_runner(
            radar=radar,
            capture_ids=["m1"],
            suites=[ProductionNoGateInvalid(), AhmedWithProductionShapedInvalidity([])],
            run_id="ahmed-no-fallback",
            source_hash="s" * 64,
            decode_fn=lambda path, chirp: cube,
            selector_fn=lambda *args: (
                1,
                None,
                {"candidates": [{"bin": 1, "failed": False}], "fallback_used": False},
            ),
        )


def test_scoring_contract_accepts_reconciled_no_gate_label(tmp_path):
    result = _run_with(tmp_path, production=ProductionNoGateInvalid())
    rows = [dict(row) for row in result.rows]
    expected_locks = dict(zip(LOCK_ESTIMANDS, (23, 27), strict=True))
    for row in rows:
        row["locked_bin"] = expected_locks[row["lock_estimand_id"]]
        row["source_hash"] = "a" * 64
        if row["arm_id"] == PRODUCTION_ARM_ID:
            row["suite_config_hash"] = "b" * 64
            row["arm_config_hash"] = "b" * 64

    identity = validate_radar_rows(rows, expected_capture_windows={"m1": 1})

    assert identity["estimator_row_count"] == 14
    production_rows = [row for row in rows if row["arm_id"] == PRODUCTION_ARM_ID]
    assert {row["hr_validity_reason"] for row in production_rows} == {"gate_not_run"}


def test_selector_fallback_and_candidate_failure_are_fatal(tmp_path):
    def fallback(*args):
        return 1, None, {
            "candidates": [{"bin": 1, "failed": True}],
            "fallback_used": True,
        }

    with pytest.raises(SelectorContractError, match="candidate DSP failures") as raised:
        _run_with(tmp_path, selector=fallback)
    assert raised.value.evidence["selector_id"] == "production_warmup_selector_v1"


def test_unexpected_decode_and_suite_failures_abort(tmp_path):
    with pytest.raises(RunnerContractError, match="ADC decode failed"):
        _run_with(
            tmp_path / "decode",
            decode=lambda path, chirp: (_ for _ in ()).throw(RuntimeError("x")),
        )

    class BrokenProduction(FakeProduction):
        def __call__(self, frames, locked_bin, fs):
            raise RuntimeError("injected DSP fault")

    with pytest.raises(RunnerContractError, match="unexpected production_eca_ahet_suite"):
        _run_with(tmp_path / "suite", production=BrokenProduction([]))


def test_malformed_decode_shape_and_native_schema_abort(tmp_path):
    with pytest.raises(RunnerContractError, match="decoded cube"):
        _run_with(
            tmp_path / "shape",
            decode=lambda path, chirp: np.zeros((599, 1, 1, 4), dtype=np.complex64),
        )

    class MalformedProduction(FakeProduction):
        def __call__(self, frames, locked_bin, fs):
            result = super().__call__(frames, locked_bin, fs)
            native = dict(result.arm_native_results[PRODUCTION_ARM_ID])
            native["hr_valid"] = np.bool_(True)
            return SuiteWindowResult({}, {PRODUCTION_ARM_ID: native})

    with pytest.raises(RunnerContractError, match="hr_valid must be an exact bool"):
        _run_with(tmp_path / "schema", production=MalformedProduction([]))


def test_unserializable_native_payload_aborts_instead_of_returning_partial_run(tmp_path):
    class UnserializableProduction(FakeProduction):
        def __call__(self, frames, locked_bin, fs):
            result = super().__call__(frames, locked_bin, fs)
            native = dict(result.arm_native_results[PRODUCTION_ARM_ID])
            native["bad_object_array"] = np.array([object()], dtype=object)
            return SuiteWindowResult({}, {PRODUCTION_ARM_ID: native})

    with pytest.raises(TypeError, match="object-dtype"):
        _run_with(tmp_path, production=UnserializableProduction([]))


def test_declared_estimator_invalidity_retains_row_and_reconstructable_evidence(tmp_path):
    class InvalidAhmed(FakeAhmed):
        def __call__(self, frames, locked_bin, fs):
            result = super().__call__(frames, locked_bin, fs)
            native = {arm: dict(payload) for arm, payload in result.arm_native_results.items()}
            arm_id = APPROVED_ARM_IDS[0]
            evidence = dict(native[arm_id]["heart_evidence"])
            evidence["scores_pre_exclusion"] = freeze_array(np.array([2.0, 2.0]))
            evidence["scores"] = freeze_array(np.array([2.0, 2.0]))
            evidence["selected_score"] = 2.0
            evidence["runner_up_score"] = 2.0
            native[arm_id].update(
                hr_valid=False,
                hr_raw=None,
                rej_reason="non_unique_maximum",
                heart_evidence=evidence,
            )
            return SuiteWindowResult(result.shared_evidence, native)

    radar = _fixture(tmp_path)
    cube = np.zeros((600, 1, 1, 4), dtype=np.complex64)
    result = execute_paired_runner(
        radar=radar,
        capture_ids=["m1"],
        suites=[FakeProduction([]), InvalidAhmed([])],
        run_id="invalidity",
        source_hash="s" * 64,
        decode_fn=lambda path, chirp: cube,
        selector_fn=lambda *args: (
            1,
            None,
            {"candidates": [{"bin": 1, "failed": False}], "fallback_used": False},
        ),
    )
    invalid_rows = [
        row for row in result.rows
        if row["arm_id"] == APPROVED_ARM_IDS[0]
    ]
    assert len(invalid_rows) == 2
    assert all(row["hr_valid"] is False for row in invalid_rows)
    assert all(row["hr_raw"] is None for row in invalid_rows)
    assert {row["validity_reason"] for row in invalid_rows} == {"non_unique_maximum"}

    packed = pack_ahmed_evidence(result.ahmed_evidence)
    indices = np.flatnonzero(packed["arm_id"] == APPROVED_ARM_IDS[0])
    assert indices.size == 2
    for index in indices:
        assert not packed["heart_valid"][index]
        assert packed["heart_selected_bin_present"][index]
        assert packed["heart_runner_up_bin_present"][index]
        assert not packed["heart_reported_bpm_present"][index]
        assert packed["heart_reason"][index] == "non_unique_maximum"


def test_malicious_suite_mutation_is_detected(tmp_path):
    class MutatingProduction(FakeProduction):
        def __call__(self, frames, locked_bin, fs):
            frames.base.setflags(write=True)
            frames.base[0, 0, 0, 0] = 1 + 0j
            return super().__call__(frames, locked_bin, fs)

    with pytest.raises(RunnerContractError, match="mutated input"):
        _run_with(tmp_path, production=MutatingProduction([]))


def test_strict_nyquist_unsupported_candidate_stays_aligned_through_packer(tmp_path):
    class PhaseProduction(FakeProduction):
        def __call__(self, frames, locked_bin, fs):
            phase = extract_chest_phase(frames, locked_bin, method="delta_before_mean")
            return SuiteWindowResult(
                {},
                {
                    PRODUCTION_ARM_ID: _native(
                        PRODUCTION_ARM_ID, "p" * 64, phase_raw=phase
                    )
                },
            )

    radar = _fixture(tmp_path)
    cube = np.zeros((600, 1, 1, 4), dtype=np.complex64)
    result = execute_paired_runner(
        radar=radar,
        capture_ids=["m1"],
        suites=[
            PhaseProduction([]),
            AhmedPhaseEstimatorSuite(
                AhmedPhaseConfig(domain=REAL_REPRESENTATIVE_DOMAIN)
            ),
        ],
        run_id="nyquist",
        source_hash="s" * 64,
        decode_fn=lambda path, chirp: cube,
        selector_fn=lambda *args: (
            1,
            None,
            {"candidates": [{"bin": 1, "failed": False}], "fallback_used": False},
        ),
    )
    row = next(
        record
        for record in result.ahmed_evidence
        if record["arm_id"] == "ahmed_phase_h5_figure_visible_unsuppressed"
    )
    candidates = np.asarray(row["heart_candidate_bins"])
    harmonic_bins = np.asarray(row["heart_harmonic_bins"])
    support = np.asarray(row["heart_support_mask"])
    boundary_index = np.flatnonzero(candidates == 60)[0]  # H*k = 5*60 = N/2.
    assert harmonic_bins.shape == (candidates.size, 5)
    assert support[boundary_index] == np.False_

    packed = pack_ahmed_evidence(result.ahmed_evidence)
    packed_row = np.flatnonzero(
        packed["arm_id"] == "ahmed_phase_h5_figure_visible_unsuppressed"
    )[0]
    present = packed["heart_candidate_present"][packed_row]
    packed_candidates = packed["heart_candidate_bins"][packed_row, present]
    packed_harmonics = packed["heart_harmonic_bins"][packed_row, present]
    assert packed_harmonics.shape[0] == packed_candidates.size
    assert packed["heart_selected_bin_present"][packed_row]
    selected_index = np.flatnonzero(
        packed_candidates == packed["heart_selected_bin"][packed_row]
    )[0]
    assert packed["heart_score_post_suppression_present"][packed_row, selected_index]
    assert packed["heart_selected_score"][packed_row] == packed[
        "heart_score_post_suppression"
    ][packed_row, selected_index]


def test_real_radar_rejects_smoke_parent_not_bound_to_the_authorized_gate(tmp_path):
    """Any complete smoke directory is not automatically the authorized parent."""
    radar = _fixture(tmp_path / "capture-root")
    source_hash = "s" * 64

    gate_writer = BundleWriter(
        stage_root=tmp_path / "synthetic", stage="synthetic", run_id="gate"
    )
    gate_writer.add_json("gate.json", {"gate_status": "passed"})
    gate = gate_writer.finalize(
        status="complete",
        provenance={"source_manifest_sha256": source_hash},
        promotion_eligible=True,
        extra_manifest={"gate_status": "passed"},
    )
    _manifest, gate_digest = read_manifest(gate.root)

    authorization_path = tmp_path / "authorization.yaml"
    authorization_path.write_text(
        yaml.safe_dump(
            {
                "authorization_id": "portable-chain",
                "gate_manifest_sha256": gate_digest,
                "source_manifest_sha256": source_hash,
                "allowed_stages": ["real-smoke", "real-radar", "score"],
                "capture_ids": ["m1"],
                "lock_estimands": list(LOCK_ESTIMANDS),
                "arm_ids": list(CANONICAL_ARM_IDS),
                "approved_by": "test",
                "approved_on": "2026-01-01",
            }
        ),
        encoding="utf-8",
    )

    wrong_parent_writer = BundleWriter(
        stage_root=tmp_path / "smoke", stage="smoke", run_id="wrong-parent"
    )
    wrong_parent_writer.add_json("rows.json", {"rows": []})
    wrong_parent = wrong_parent_writer.finalize(
        status="complete",
        provenance={"source_manifest_sha256": source_hash},
        promotion_eligible=True,
        parents={"synthetic": "0" * 64},
    )

    with pytest.raises(PreflightError, match="parent|synthetic|gate"):
        run_radar_stage(
            stage="real-radar",
            gate_dir=gate.root,
            authorization_path=authorization_path,
            source_manifest_sha256=source_hash,
            radar=radar,
            suites=[FakeProduction([]), FakeAhmed([])],
            run_id="must-not-run",
            parent_dir=wrong_parent.root,
            decode_fn=lambda path, chirp: (_ for _ in ()).throw(
                AssertionError("capture decode must not run")
            ),
        )
