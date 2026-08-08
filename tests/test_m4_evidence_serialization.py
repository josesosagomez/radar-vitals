"""Portable M3 evidence/serializer controls; no real capture access."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from src.m4.evidence_serialization import (
    EVIDENCE_COMPUTED,
    EVIDENCE_NOT_COMPUTED_BREATHING_UNAVAILABLE,
    array_sha256,
    deserialize_native_tree,
    pack_ahmed_evidence,
    pack_shared_evidence,
    serialize_native_tree,
)


def _assert_equivalent(left, right):
    assert type(left) is type(right)
    if isinstance(left, dict):
        assert list(left) == list(right)
        for key in left:
            _assert_equivalent(left[key], right[key])
    elif isinstance(left, (list, tuple)):
        assert len(left) == len(right)
        for x, y in zip(left, right):
            _assert_equivalent(x, y)
    elif isinstance(left, np.ndarray):
        assert left.dtype == right.dtype and left.shape == right.shape
        if np.issubdtype(left.dtype, np.inexact):
            assert np.array_equal(left, right, equal_nan=True)
        else:
            assert np.array_equal(left, right)
    elif isinstance(left, np.generic):
        assert left.dtype == right.dtype
        assert left == right or (np.issubdtype(left.dtype, np.inexact) and np.isnan(left))
    elif isinstance(left, float) and np.isnan(left):
        assert np.isnan(right)
    else:
        assert left == right


def test_full_native_tree_round_trip_preserves_types_values_and_nonfinite(tmp_path):
    native = {
        "array": np.array([[1.0, np.nan]], dtype=np.float32),
        "tuple": (None, True, 4, -0.0, "x"),
        "list": [np.int16(-2), np.float32(np.inf), np.bool_(True)],
        "nested": {"unicode": np.array(["a", "β"], dtype="<U1")},
        "python_nonfinite": [float("nan"), float("inf"), float("-inf")],
    }
    tree = serialize_native_tree(native)
    # The deterministic JSON index is strict and contains no non-standard NaN token.
    json.dumps(tree.index, allow_nan=False, sort_keys=True)
    path = tmp_path / "native.npz"
    np.savez(path, **tree.arrays)
    with np.load(path, allow_pickle=False) as loaded:
        restored = deserialize_native_tree(tree.index, loaded)
        assert all(loaded[key].dtype != object for key in loaded.files)
    _assert_equivalent(native, restored)


def test_native_tree_rejects_cycles_unsupported_objects_and_raw_cube():
    cycle = []
    cycle.append(cycle)
    with pytest.raises(ValueError, match="cycle"):
        serialize_native_tree(cycle)
    with pytest.raises(TypeError, match="unsupported"):
        serialize_native_tree({"x": object()})
    with pytest.raises(ValueError, match="raw frame cube"):
        serialize_native_tree({"cube": np.zeros((2, 3, 4, 5), dtype=np.complex64)})


@pytest.mark.parametrize(
    "payload",
    [
        {"opaque_payload": np.zeros((2, 3, 4, 5), dtype=np.complex64)},
        {"nested": [{"diagnostic": np.zeros((2, 3, 4, 5), dtype=np.complex64)}]},
    ],
)
def test_native_tree_rejects_raw_cube_independent_of_mapping_key(payload):
    """A renamed raw cube is still raw ADC data and must never enter the artifact."""
    with pytest.raises(ValueError, match="raw frame cube"):
        serialize_native_tree(payload)


@pytest.mark.parametrize(
    "index, arrays",
    [
        ({"schema_version": 1, "root": {"type": "none"}, "extra": 1}, {}),
        ({"schema_version": 1, "root": {"type": "none", "value": 1}}, {}),
        (
            {
                "schema_version": 1,
                "root": {
                    "type": "dict",
                    "entries": [{"key": "x", "value": {"type": "none"}, "extra": 1}],
                },
            },
            {},
        ),
        ({"schema_version": 1, "root": {"type": "list", "items": "not-a-list"}}, {}),
        (
            {
                "schema_version": 1,
                "root": {"type": "float_nonfinite", "value": "not-a-token"},
            },
            {},
        ),
        (
            {
                "schema_version": 1,
                "root": {"type": "ndarray", "array_key": "array_00000000", "extra": 1},
            },
            {"array_00000000": np.arange(2)},
        ),
        (
            {
                "schema_version": 1,
                "root": {
                    "type": "list",
                    "items": [
                        {"type": "ndarray", "array_key": "array_00000000"},
                        {"type": "ndarray", "array_key": "array_00000000"},
                    ],
                },
            },
            {"array_00000000": np.arange(2)},
        ),
    ],
)
def test_native_tree_deserializer_rejects_noncanonical_or_malformed_schema(index, arrays):
    """The persisted JSON index is a strict schema, not a permissive object graph."""
    with pytest.raises(ValueError, match="malformed|unsupported|unknown"):
        deserialize_native_tree(index, arrays)


def test_ahmed_npz_uses_padding_presence_and_reconstructs_selected_score(tmp_path):
    stage = {
        "candidate_bins": np.array([2, 3]),
        "harmonic_bins": np.array([[2, 4, 6], [3, 6, 9]]),
        "support_mask": np.array([True, False]),
        "nyquist_mask": np.array([False, True]),
        "suppression_mask": np.array([True, False]),
        "eligibility_mask": np.array([True, False]),
        "score_pre_suppression": np.array([8.0, np.nan]),
        "score_post_suppression": np.array([8.0, np.nan]),
        "selected_bin": 2,
        "selected_frequency_hz": 1.2,
        "runner_up_bin": None,
        "runner_up_frequency_hz": None,
        "selected_score": 8.0,
        "runner_up_score": None,
        "valid": True,
        "reason": "ok",
        "evidence_status": "computed",
        "reported_bpm": 72.0,
    }
    record = {
        "capture_id": "c",
        "lock_estimand_id": "recorded_lock_as_captured",
        "arm_id": "a",
        "k": 0,
        **{f"breath_{key}": value for key, value in stage.items()},
        **{f"heart_{key}": value for key, value in stage.items()},
        "cube_hash": "a" * 64,
        "signal_hash": "b" * 64,
        "config_hash": "c" * 64,
        "source_hash": "d" * 64,
        "run_hash": "e" * 64,
    }
    arrays = pack_ahmed_evidence([record])
    path = tmp_path / "evidence.npz"
    np.savez(path, **arrays)
    with np.load(path, allow_pickle=False) as loaded:
        for vital in ("breath", "heart"):
            present = loaded[f"{vital}_candidate_present"][0]
            candidates = loaded[f"{vital}_candidate_bins"][0, present]
            scores = loaded[f"{vital}_score_post_suppression"][0, present]
            score_present = loaded[f"{vital}_score_post_suppression_present"][0, present]
            selected_index = np.flatnonzero(
                candidates == loaded[f"{vital}_selected_bin"][0]
            )[0]
            assert scores[selected_index] == loaded[f"{vital}_selected_score"][0] == 8.0
            assert loaded[f"{vital}_reported_bpm"][0] == pytest.approx(
                60.0 * loaded[f"{vital}_selected_frequency_hz"][0]
            )
            assert score_present.tolist() == [True, False]
            # Presence describes real matrix cells; scientific support is separate.
            # The unsupported candidate still has three persisted harmonic-bin cells.
            assert loaded[f"{vital}_harmonic_present"][0, 0].tolist() == [True] * 3
            assert loaded[f"{vital}_harmonic_present"][0, 1].tolist() == [True] * 3
            assert loaded[f"{vital}_support_mask"][0].tolist() == [True, False]
        assert all(loaded[key].dtype != object for key in loaded.files)
        for key, original in arrays.items():
            assert loaded[key].dtype == original.dtype
            assert loaded[key].shape == original.shape
            assert array_sha256(loaded[key]) == array_sha256(original)


def _suppressed_stage(evidence_status: str) -> dict:
    """One stage whose arrays say 'nothing survived and nothing scored'.

    The array content is deliberately identical for both statuses: an all-False
    keep-mask with all-NaN scores is exactly what a genuinely evaluated
    fully-suppressed candidate set looks like, which is why the placeholder cannot be
    identified from the arrays alone.
    """
    return {
        "candidate_bins": np.array([2, 3], dtype=np.int64),
        "harmonic_bins": np.array([[2, 4, 6], [3, 6, 9]], dtype=np.int64),
        "support_mask": np.array([True, True]),
        "nyquist_mask": np.array([False, False]),
        "suppression_mask": np.zeros(2, dtype=bool),
        "eligibility_mask": np.zeros(2, dtype=bool),
        "score_pre_suppression": np.full(2, np.nan),
        "score_post_suppression": np.full(2, np.nan),
        "selected_bin": None,
        "selected_frequency_hz": None,
        "selected_score": None,
        "runner_up_bin": None,
        "runner_up_frequency_hz": None,
        "runner_up_score": None,
        "valid": False,
        "reason": "breath_estimate_invalid",
        "evidence_status": evidence_status,
        "reported_bpm": None,
    }


def test_ahmed_npz_distinguishes_a_not_computed_stage_from_a_computed_one(tmp_path):
    """Placeholder heart evidence must not read as a computed 'all suppressed' result.

    CLAUDE.md section 4 forbids presenting fabricated values as results.  The two rows
    below carry byte-identical mask/score arrays, so the persisted status column is the
    only thing that lets a reader of the NPZ alone tell measurement from placeholder.
    """
    computed_stage = _suppressed_stage(EVIDENCE_COMPUTED)
    placeholder_stage = _suppressed_stage(EVIDENCE_NOT_COMPUTED_BREATHING_UNAVAILABLE)
    records = [
        {
            "capture_id": "m1",
            "lock_estimand_id": "recorded_lock_as_captured",
            "arm_id": "ahmed_phase_h3_eq26_multiples_suppressed",
            "k": k,
            **{f"breath_{key}": value for key, value in computed_stage.items()},
            **{f"heart_{key}": value for key, value in stage.items()},
            "cube_hash": "a" * 64,
            "signal_hash": "b" * 64,
            "config_hash": "c" * 64,
            "source_hash": "d" * 64,
            "run_hash": "e" * 64,
        }
        for k, stage in enumerate((computed_stage, placeholder_stage))
    ]

    arrays = pack_ahmed_evidence(records)
    path = tmp_path / "ahmed_evidence.npz"
    np.savez(path, **arrays)
    with np.load(path, allow_pickle=False) as loaded:
        status = loaded["heart_evidence_status"]
        assert status.dtype != object
        assert status.tolist() == [
            EVIDENCE_COMPUTED,
            EVIDENCE_NOT_COMPUTED_BREATHING_UNAVAILABLE,
        ]
        # The breathing stage really was computed in both rows.
        assert loaded["breath_evidence_status"].tolist() == [EVIDENCE_COMPUTED] * 2
        # Everything else about the two heart rows is identical, so without the status
        # column the placeholder would be indistinguishable from a measured outcome.
        for name in (
            "heart_suppression_mask",
            "heart_eligibility_mask",
            "heart_score_pre_suppression",
            "heart_score_pre_suppression_present",
            "heart_score_post_suppression",
            "heart_valid",
            "heart_reason",
        ):
            assert array_sha256(loaded[name][0]) == array_sha256(loaded[name][1])


def test_ahmed_packer_rejects_an_undeclared_evidence_status():
    """An unlabelled or invented status must fail rather than be persisted."""
    stage = _suppressed_stage("looks_fine_to_me")
    record = {
        "capture_id": "m1",
        "lock_estimand_id": "recorded_lock_as_captured",
        "arm_id": "ahmed_phase_h3_eq26_multiples_suppressed",
        "k": 0,
        **{f"breath_{key}": value for key, value in stage.items()},
        **{f"heart_{key}": value for key, value in stage.items()},
        "cube_hash": "a" * 64,
        "signal_hash": "b" * 64,
        "config_hash": "c" * 64,
        "source_hash": "d" * 64,
        "run_hash": "e" * 64,
    }

    with pytest.raises(ValueError, match="evidence_status must be one of"):
        pack_ahmed_evidence([record])


@pytest.mark.parametrize(
    "field, malformed",
    [
        ("candidate_bins", np.array([2.5], dtype=np.float64)),
        ("harmonic_bins", np.array([[2.5, 5.0, 7.5]], dtype=np.float64)),
        ("support_mask", np.array([1], dtype=np.int64)),
    ],
)
def test_ahmed_packer_rejects_lossy_dtype_coercion(field, malformed):
    """Malformed evidence must fail, never be truncated or truth-coerced."""
    stage = {
        "candidate_bins": np.array([2], dtype=np.int64),
        "harmonic_bins": np.array([[2, 4, 6]], dtype=np.int64),
        "support_mask": np.array([True]),
        "nyquist_mask": np.array([False]),
        "suppression_mask": np.array([True]),
        "eligibility_mask": np.array([True]),
        "score_pre_suppression": np.array([8.0]),
        "score_post_suppression": np.array([8.0]),
        "selected_bin": 2,
        "selected_frequency_hz": 1.2,
        "runner_up_bin": None,
        "runner_up_frequency_hz": None,
        "selected_score": 8.0,
        "runner_up_score": None,
        "valid": True,
        "reason": "ok",
        "evidence_status": "computed",
        "reported_bpm": 72.0,
    }
    stage[field] = malformed
    record = {
        "capture_id": "c",
        "lock_estimand_id": "recorded_lock_as_captured",
        "arm_id": "a",
        "k": 0,
        **{f"breath_{key}": value for key, value in stage.items()},
        **{f"heart_{key}": value for key, value in stage.items()},
        "cube_hash": "a" * 64,
        "signal_hash": "b" * 64,
        "config_hash": "c" * 64,
        "source_hash": "d" * 64,
        "run_hash": "e" * 64,
    }
    with pytest.raises((TypeError, ValueError), match="dtype|integer|bool|malformed"):
        pack_ahmed_evidence([record])


def test_shared_npz_round_trip_preserves_every_array_hash_shape_and_dtype(tmp_path):
    records = []
    for k in (0, 1):
        records.append(
            {
                "capture_id": "m1",
                "lock_estimand_id": "recorded_lock_as_captured",
                "locked_bin": 23,
                "k": k,
                "frame_start": 600 * k,
                "frame_stop": 600 * (k + 1),
                "epoch_start": 1_700_000_000.0 + 30 * k,
                "epoch_stop": 1_700_000_030.0 + 30 * k,
                "sample_time_s": np.arange(4, dtype=np.float64) / 20.0,
                "phase": np.arange(4, dtype=np.float64) + k,
                "frequency_grid_hz": np.array([0.0, 5.0, 10.0]),
                "spectrum_magnitude": np.array([1.0, 2.0, 3.0]) + k,
                "cube_hash": "a" * 64,
                "window_cube_hash": f"{k + 1:064x}",
                "signal_hash": f"{k + 3:064x}",
                "config_hash": "b" * 64,
                "source_hash": "c" * 64,
                "run_hash": "d" * 64,
            }
        )
    arrays = pack_shared_evidence(records)
    path = tmp_path / "shared.npz"
    np.savez(path, **arrays)
    with np.load(path, allow_pickle=False) as loaded:
        assert set(loaded.files) == set(arrays)
        for key, original in arrays.items():
            assert loaded[key].dtype == original.dtype
            assert loaded[key].shape == original.shape
            assert loaded[key].dtype != object
            assert array_sha256(loaded[key]) == array_sha256(original)


def test_shared_packer_rejects_shape_drift_between_paired_rows():
    base = {
        "capture_id": "m1",
        "lock_estimand_id": "recorded_lock_as_captured",
        "locked_bin": 23,
        "k": 0,
        "frame_start": 0,
        "frame_stop": 600,
        "epoch_start": 1.0,
        "epoch_stop": 31.0,
        "sample_time_s": np.arange(4, dtype=np.float64),
        "phase": np.arange(4, dtype=np.float64),
        "frequency_grid_hz": np.arange(3, dtype=np.float64),
        "spectrum_magnitude": np.arange(3, dtype=np.float64),
        "cube_hash": "a" * 64,
        "window_cube_hash": "b" * 64,
        "signal_hash": "c" * 64,
        "config_hash": "d" * 64,
        "source_hash": "e" * 64,
        "run_hash": "f" * 64,
    }
    changed = dict(base, k=1, phase=np.arange(5, dtype=np.float64))
    with pytest.raises(ValueError, match="shape or dtype"):
        pack_shared_evidence([base, changed])


# =============================================================================
# Persisted-evidence equation-level reconstruction (independent oracle)
# =============================================================================
#
# WHAT THIS SECTION CLAIMS
# ------------------------
# A third party handed only ``shared_evidence.npz``, ``ahmed_evidence.npz`` and
# ``rows.json`` can rebuild every Ahmed number at the equation level.  The claim is
# worthless unless the rebuild is *independent*, so everything below the
# "ORACLE" banner is written longhand in plain NumPy/Python from:
#
#   * ``plans/m8_ahmed_correction_plan.md`` section 3 (the six canonical Layer B
#     arms: fixed H in {3, 5}, ``non_dc_mean``, magnitude accumulation, one of three
#     suppression identities, q = f, rate = 60q, strict H*q < f_Nyquist), and
#     section 4 (the exact magnitude-score functional and the eta register);
#   * Ahmed et al., "Discovering the Unseen", IEEE Trans. Radar Systems 2 (2024),
#     pp. 601-602 -- Eq. (23) with Lambda = diag{eta_m} and eta_m = the number of
#     nonzero elements of the mth row of B (hence the equation-literal eta = H + 1
#     counting the DC column, which the plan names ``matrix_eta`` and which has no
#     Layer B arm), Eq. (24) f_b = i_b/(2 N_f) PRF (the *Layer A* q = 2f mapping),
#     Eqs. (25)-(26) with the rows at i_b and its multiples zeroed, and the Fig. 8
#     discussion "all frequencies that are equal to or lower than the breathing
#     rate are disregarded".
#
# The oracle deliberately uses readable loops instead of compact vectorisation so a
# human examiner can check it against the paper by eye.
#
# INDEPENDENCE RULE (do not weaken)
# ---------------------------------
# No function below the ORACLE banner may call, import or delegate to
# ``src.m8.ahmed_fig8`` (``accumulate_harmonics``, ``_select_scores``,
# ``suppression_eligibility_mask``, ``accumulate_layer_a_profile``) or to
# ``src.m8.ahmed_transfer`` (``_score_vital``, ``_support_masks``,
# ``_suppression_mask``, ``_candidate_bins``, ``estimate_phase_ha``,
# ``AhmedPhaseEstimatorSuite``).  Production is imported only by the *fixture*
# builder, whose job is to produce and persist a real artifact -- independence is
# required of the mathematics, not of the plumbing that writes the file.
#
# Two easy-to-get-backwards points are asserted deliberately rather than inherited:
#   * Layer B uses q = f and rate = 60q.  Layer A / the original pulse-radar paper
#     uses q = 2f and 30q.  Using 30q here is one of the mutation controls.
#   * Nyquist support is STRICT: H*q < f_Nyquist.  Equality is degenerate and
#     unsupported.  Relaxing it to <= is another mutation control.
# =============================================================================


# ---------------------------------------------------------------------------
# Fixture plumbing (production imports live here, never in the oracle)
# ---------------------------------------------------------------------------

#: Frozen offline contract: 600-frame / 30 s windows at 20 Hz (src/m4/window_grid.py).
_F4_FS_HZ = 20.0
_F4_FRAMES_PER_WINDOW = 600
_F4_ADC_SAMPLES = 4
_F4_LOCKED_BIN = 1

#: Two windows of hand-placed rFFT lines, chosen so that (a) breathing and heart both
#: have a unique argmax, (b) real harmonic mass is accumulated (a 3rd breathing and a
#: 2nd heart harmonic are present), (c) window 1's heart runner-up (bin 33 = 3 x 11)
#: is removed by Eq. 26 suppression but not by the prose rule, and (d) the two windows
#: differ, so an inexact (capture, lock, k) join is detectable.
#: Bin b of a 600-sample / 20 Hz rFFT is b/30 Hz.
_F4_WINDOW_TONES = (
    ((7, 1.0), (21, 0.3), (37, 0.6), (74, 0.2)),
    ((11, 1.0), (33, 0.25), (43, 0.5), (86, 0.15)),
)


def _f4_window_phase(tones) -> np.ndarray:
    """Sum of exact-bin cosines. Amplitudes are small enough that the frame-to-frame
    increment stays far below pi, which is what ``delta_before_mean`` needs."""
    n = _F4_FRAMES_PER_WINDOW
    samples = np.arange(n, dtype=np.float64)
    phase = np.zeros(n, dtype=np.float64)
    for bin_index, amplitude in tones:
        phase += amplitude * np.cos(2.0 * np.pi * bin_index * samples / n)
    return phase


def _f4_cube() -> np.ndarray:
    """Build a raw cube whose extracted chest phase is the designed signal.

    The ADC vector of frame ``n`` is a unit tone at range bin 1 carrying slow-time
    phase ``phi[n]``.  With ``np.hanning(4) == [0, .75, .75, 0]`` the windowed range
    FFT at bin 1 equals ``1.5 * exp(1j*phi[n])``, so ``delta_before_mean`` returns
    ``phi - phi[0]`` (the frame-to-frame increments stay far below pi, so no wrap
    occurs; the residual is ~1e-7 rad from the complex64 range FFT).
    """
    tone = np.exp(2j * np.pi * _F4_LOCKED_BIN * np.arange(_F4_ADC_SAMPLES) / _F4_ADC_SAMPLES)
    windows = [_f4_window_phase(tones) for tones in _F4_WINDOW_TONES]
    phase = np.concatenate(windows)
    cube = np.zeros((phase.size, 1, 1, _F4_ADC_SAMPLES), dtype=np.complex64)
    cube[:, 0, 0, :] = (np.exp(1j * phase)[:, None] * tone[None, :]).astype(np.complex64)
    return cube


def _f4_persist_bundle(tmp_path: Path):
    """Run the real paired runner over a portable two-window capture and persist it.

    Everything here is plumbing: the production Ahmed suite must generate the evidence,
    otherwise there is nothing real to reconstruct.  The oracle never sees these objects.
    """
    from src.m4.bundle import sha256_path
    from src.m4.capture_registry import RadarCapture, RadarScope
    from src.m4.estimator_runner import execute_paired_runner, persist_radar_artifacts
    from src.m4.estimator_suite import EstimatorArmSpec, SuiteWindowResult
    from src.m4.production_suite import PRODUCTION_ARM_ID
    from src.m8.ahmed_transfer import (
        AhmedPhaseConfig,
        AhmedPhaseEstimatorSuite,
        REAL_REPRESENTATIVE_DOMAIN,
    )
    from src.respiration import extract_chest_phase
    from src.window_pipeline import run_config_hash

    cube = _f4_cube()
    frames = int(cube.shape[0])
    capture_dir = tmp_path / "capture"
    capture_dir.mkdir(parents=True)
    config = {
        "profile": {
            "num_adc_samples": _F4_ADC_SAMPLES,
            "num_rx": 1,
            "num_chirps_per_frame": 1,
            "iq_swap": True,
            "range_resolution_m": 0.1,
        },
        "session": {"frame_rate_hz": _F4_FS_HZ},
        "bin_selection": {"candidate_bins": [_F4_LOCKED_BIN]},
        "protocol": {"subject_distance_m": [0.1, 0.1]},
    }
    (capture_dir / "run_metadata.json").write_text(
        json.dumps(
            {
                "config": config,
                "iq_swap": True,
                "locked_bin": _F4_LOCKED_BIN,
                "start_wall_utc": "2026-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (capture_dir / "warmup_bin_selection.json").write_text(
        json.dumps({"selected_bin": _F4_LOCKED_BIN}), encoding="utf-8"
    )
    (capture_dir / "adc_stream.bin").write_bytes(bytes(frames * 16))
    capture = RadarCapture(
        capture_id="m1",
        directory="capture",
        frames=frames,
        windows=frames // _F4_FRAMES_PER_WINDOW,
        tail_frames=0,
        recorded_lock=_F4_LOCKED_BIN,
        rerun_lock=_F4_LOCKED_BIN,
        capture_config_sha256=run_config_hash(config),
        adc_stream_sha256=sha256_path(capture_dir / "adc_stream.bin"),
        metadata_sha256=sha256_path(capture_dir / "run_metadata.json"),
        warmup_sha256=sha256_path(capture_dir / "warmup_bin_selection.json"),
    )
    radar = RadarScope(
        root=tmp_path,
        captures={"m1": capture},
        geometry={
            "adc_samples": _F4_ADC_SAMPLES,
            "rx": 1,
            "chirps_per_frame": 1,
            "sample_dtype": "complex_int16",
            "bytes_per_frame": 16,
            "iq_swap": True,
            "frame_rate_hz": _F4_FS_HZ,
            "range_resolution_m_approx": 0.1,
        },
        window_grid={
            "frames_per_window": _F4_FRAMES_PER_WINDOW,
            "total_windows": capture.windows,
        },
    )

    class PhaseProduction:
        """Minimal production stand-in that reconstructs the same raw phase."""

        suite_id = "production_eca_ahet_suite_v1"
        suite_config_hash = "p" * 64
        arm_specs = (EstimatorArmSpec(PRODUCTION_ARM_ID, "eca_ahet_v1", "p" * 64),)
        outcome_classifiers: dict = {}

        def __call__(self, window, locked_bin, fs):
            phase = extract_chest_phase(
                window, locked_bin=int(locked_bin), method="delta_before_mean"
            )
            return SuiteWindowResult(
                {},
                {
                    PRODUCTION_ARM_ID: {
                        "arm_id": PRODUCTION_ARM_ID,
                        "estimator_id": "eca_ahet_v1",
                        "run_config_hash": "p" * 64,
                        "hr_valid": True,
                        "hr_raw": 74.0,
                        "br_valid": True,
                        "br_bpm": 14.0,
                        "br_confidence": "high",
                        "rej_reason": "",
                        "f_r_hz": 0.2333333333,
                        "phase_raw": phase,
                    }
                },
            )

    run = execute_paired_runner(
        radar=radar,
        capture_ids=["m1"],
        suites=[
            PhaseProduction(),
            AhmedPhaseEstimatorSuite(AhmedPhaseConfig(domain=REAL_REPRESENTATIVE_DOMAIN)),
        ],
        run_id="f4-reconstruction",
        source_hash="s" * 64,
        decode_fn=lambda path, chirp: cube,
        selector_fn=lambda *args: (
            _F4_LOCKED_BIN,
            None,
            {"candidates": [{"bin": _F4_LOCKED_BIN, "failed": False}], "fallback_used": False},
        ),
    )
    bundle = persist_radar_artifacts(
        run,
        out_root=tmp_path / "bundles",
        provenance={
            "schema_version": 1,
            "run_hash": run.run_hash,
            "source_manifest_sha256": "s" * 64,
        },
        parents={},
        promotion_eligible=True,
    )
    return run, bundle


# ===========================================================================
# ORACLE -- longhand mathematics, no production imports below this banner
# ===========================================================================

#: The three accepted suppression identities, as they appear as arm-ID suffixes.
_ORACLE_SUPPRESSION_RULES = (
    "figure_visible_unsuppressed",
    "eq26_multiples_suppressed",
    "prose_low_or_equal_suppressed",
)

#: Real Layer B candidate bands, in Hz (plan section 3 / the real representative
#: domain): breathing [0.10, 0.50] closed, heart [0.80, 2.00] closed, DC never a
#: candidate.
_ORACLE_BANDS = {"breath": (0.10, 0.50), "heart": (0.80, 2.00)}

#: Both sides are float64 sums of the *same* stored magnitudes, so only summation
#: order can differ (~1e-14 relative).  1e-12 is therefore tight: the interpretation
#: errors this test exists to catch (eta = H vs H + 1) move the score by 25-33 %.
_ORACLE_RTOL = 1e-12
_ORACLE_ATOL = 1e-12


def _oracle_harmonic_count(arm_id: str) -> int:
    """H is recoverable from the arm ID alone (``..._h3_...`` / ``..._h5_...``)."""
    found = [h for h in (3, 5) if f"_h{h}_" in arm_id]
    assert len(found) == 1, f"arm {arm_id!r} does not declare exactly one harmonic count"
    return found[0]


def _oracle_suppression_rule(arm_id: str) -> str:
    """The suppression identity is recoverable from the arm-ID suffix alone."""
    found = [rule for rule in _ORACLE_SUPPRESSION_RULES if arm_id.endswith(rule)]
    assert len(found) == 1, f"arm {arm_id!r} does not declare exactly one suppression rule"
    return found[0]


def _oracle_geometry(sample_time_s, frequency_grid_hz, phase):
    """Recover fs, n_fft and Nyquist from the stored arrays alone."""
    dt = float(sample_time_s[1]) - float(sample_time_s[0])
    assert dt > 0.0, "stored sample_time_s is not increasing"
    fs_hz = 1.0 / dt
    df_hz = float(frequency_grid_hz[1]) - float(frequency_grid_hz[0])
    assert df_hz > 0.0, "stored frequency_grid_hz is not increasing"
    n_fft = int(round(fs_hz / df_hz))
    assert frequency_grid_hz.size == n_fft // 2 + 1, "stored grid is not an rFFT grid"
    assert n_fft == phase.size, "stored transform is not the native (untruncated) length"
    for index in range(frequency_grid_hz.size):
        assert float(frequency_grid_hz[index]) == pytest.approx(
            index * fs_hz / n_fft, rel=_ORACLE_RTOL, abs=_ORACLE_ATOL
        ), "stored frequency grid is not k*fs/N"
    return fs_hz, n_fft, fs_hz / 2.0


def _oracle_candidate_bins(frequency_grid_hz, vital):
    """rFFT bins inside the declared closed band; DC is never a candidate."""
    low_hz, high_hz = _ORACLE_BANDS[vital]
    bins = []
    for index in range(frequency_grid_hz.size):
        if index == 0:
            continue
        frequency = float(frequency_grid_hz[index])
        if low_hz - 1e-9 <= frequency <= high_hz + 1e-9:
            bins.append(index)
    return np.asarray(bins, dtype=np.int64)


def _oracle_accumulate(spectrum_magnitude, candidate_bins, harmonics, n_fft, *, eta_rule,
                       support_rule):
    """A(q) = (1/eta) * sum_{h=1..H} |S(hq)| over the harmonic locations q, 2q, ..., Hq.

    This is a sum of spectral magnitudes, NOT |sum_h S(hq)|; the stored
    ``spectrum_magnitude`` is already |S|.  The six canonical Layer B arms use
    ``non_dc_mean``, i.e. eta = H.  ``matrix_eta`` (eta = H + 1, the equation-literal
    Layer A reading that counts B's DC column) has no Layer B arm mapping and is
    exposed here only as a mutation control.

    Support is strict: a candidate is supported only if H*q < f_Nyquist, i.e.
    H*q < n_fft/2 in bin terms.  Equality is degenerate (a real line at Nyquist loses
    one quadrature) and is recorded separately, never scored.
    """
    assert eta_rule in ("non_dc_mean", "matrix_eta")
    assert support_rule in ("strict", "nyquist_equality_allowed")
    eta = float(harmonics) if eta_rule == "non_dc_mean" else float(harmonics + 1)
    half_n_fft = n_fft / 2.0

    count = int(candidate_bins.size)
    harmonic_bins = np.zeros((count, harmonics), dtype=np.int64)
    supported = np.zeros(count, dtype=bool)
    nyquist_degenerate = np.zeros(count, dtype=bool)
    scores = np.full(count, np.nan, dtype=np.float64)

    for row in range(count):
        q = int(candidate_bins[row])
        for column in range(harmonics):
            harmonic_bins[row, column] = (column + 1) * q
        reach = harmonics * q  # the highest harmonic location, H*q
        if support_rule == "strict":
            in_band = reach < half_n_fft
        else:
            in_band = reach <= half_n_fft
        # The second clause is an array-bounds guard; strict support already implies it.
        supported[row] = bool(in_band and reach < spectrum_magnitude.size)
        nyquist_degenerate[row] = bool(reach == half_n_fft and not supported[row])
        if supported[row]:
            total = 0.0
            for column in range(harmonics):
                total += float(spectrum_magnitude[harmonic_bins[row, column]])
            scores[row] = total / eta
    return harmonic_bins, supported, nyquist_degenerate, scores


def _oracle_keep_mask(rule, candidate_bins, breath_bin):
    """Suppression applied to HEART candidates using the same-arm Ahmed breathing bin.

    Returned is the *keep* mask -- True means the candidate survives.  That is what the
    persisted field named ``*_suppression_mask`` holds, and the naming is a landmine
    worth stating out loud.  Never Masimo, never production BR.
    """
    keep = np.ones(int(candidate_bins.size), dtype=bool)
    if rule == "figure_visible_unsuppressed":
        return keep
    assert breath_bin is not None and breath_bin > 0, (
        "Eq. 26 and prose suppression both require a valid Ahmed breathing bin"
    )
    for index in range(int(candidate_bins.size)):
        q = int(candidate_bins[index])
        if rule == "eq26_multiples_suppressed":
            keep[index] = (q % breath_bin) != 0
        elif rule == "prose_low_or_equal_suppressed":
            keep[index] = q > breath_bin
        else:  # pragma: no cover - guarded by _oracle_suppression_rule
            raise AssertionError(f"unsupported suppression rule {rule!r}")
    return keep


def _oracle_select(scores, candidate_bins):
    """argmax over eligible candidates; a non-unique maximum is a declared invalidity."""
    finite = [i for i in range(int(scores.size)) if np.isfinite(scores[i])]
    if not finite:
        return None
    maximum = max(float(scores[i]) for i in finite)
    winners = [i for i in finite if float(scores[i]) == maximum]
    selected_index = winners[0]  # candidate bins ascend, so index order is bin order
    others = [i for i in finite if i != selected_index]
    runner_index = None
    if others:
        runner_index = sorted(
            others, key=lambda i: (-float(scores[i]), int(candidate_bins[i]))
        )[0]
    return {
        "selected_index": selected_index,
        "selected_bin": int(candidate_bins[selected_index]),
        "selected_score": float(scores[selected_index]),
        "runner_index": runner_index,
        "unique_maximum": len(winners) == 1,
    }


def _f4_open_persisted(bundle_root: Path):
    """Reopen the persisted files from disk. No in-memory run object is consulted."""
    root = Path(bundle_root)
    with np.load(root / "shared_evidence.npz", allow_pickle=False) as handle:
        shared = {key: np.array(handle[key]) for key in handle.files}
    with np.load(root / "ahmed_evidence.npz", allow_pickle=False) as handle:
        ahmed = {key: np.array(handle[key]) for key in handle.files}
    rows = json.loads((root / "rows.json").read_text(encoding="utf-8"))
    return shared, ahmed, rows


def _f4_reconstruct(
    bundle_root: Path,
    *,
    eta_rule: str = "non_dc_mean",
    support_rule: str = "strict",
    bpm_per_hz: float = 60.0,
    join_on_k: bool = True,
):
    """Rebuild every persisted Ahmed number from the stored arrays alone.

    The keyword arguments are mutation-control seams.  Their defaults are the approved
    scientific contract; every non-default value must make this function fail.
    """
    shared, ahmed, rows = _f4_open_persisted(bundle_root)

    shared_by_key: dict = {}
    for index in range(int(shared["capture_id"].size)):
        key = (
            str(shared["capture_id"][index]),
            str(shared["lock_estimand_id"][index]),
            int(shared["k"][index]) if join_on_k else None,
        )
        if join_on_k:
            assert key not in shared_by_key, f"duplicate shared key {key}"
            shared_by_key[key] = index
        else:
            shared_by_key.setdefault(key, index)

    tally = {
        "ahmed_rows": 0,
        "vital_stages": 0,
        "nyquist_degenerate_candidates": 0,
        "suppressed_candidates": 0,
    }

    for row in range(int(ahmed["arm_id"].size)):
        capture_id = str(ahmed["capture_id"][row])
        lock_id = str(ahmed["lock_estimand_id"][row])
        arm_id = str(ahmed["arm_id"][row])
        k = int(ahmed["k"][row])
        shared_row = shared_by_key[(capture_id, lock_id, k if join_on_k else None)]

        frequency_grid_hz = shared["frequency_grid_hz"][shared_row]
        spectrum_magnitude = shared["spectrum_magnitude"][shared_row]
        phase = shared["phase"][shared_row]
        _fs_hz, n_fft, _nyquist_hz = _oracle_geometry(
            shared["sample_time_s"][shared_row], frequency_grid_hz, phase
        )

        # The stored spectrum must be the spectrum the arms scored: |rfft(stored phase)|.
        recomputed = np.abs(np.fft.rfft(phase, n=n_fft))
        assert recomputed.shape == spectrum_magnitude.shape
        assert np.allclose(
            spectrum_magnitude, recomputed, rtol=1e-11, atol=1e-9
        ), "stored spectrum is not |rfft(stored phase)|"

        harmonics = _oracle_harmonic_count(arm_id)
        assert ahmed["heart_reason"][row] != "breath_estimate_invalid", (
            "fixture degenerated: the Ahmed breathing estimate is invalid, so the heart "
            "evidence is the runner's placeholder rather than a scored row"
        )

        for vital in ("breath", "heart"):
            present = ahmed[f"{vital}_candidate_present"][row]
            candidate_bins = ahmed[f"{vital}_candidate_bins"][row][present]
            assert np.all(
                ahmed[f"{vital}_candidate_bins"][row][~present] == 0
            ), "ragged padding must be zero-filled"
            assert np.array_equal(
                candidate_bins, _oracle_candidate_bins(frequency_grid_hz, vital)
            ), f"{vital} candidate set does not match the declared band"

            (
                harmonic_bins,
                supported,
                nyquist_degenerate,
                scores_pre,
            ) = _oracle_accumulate(
                spectrum_magnitude,
                candidate_bins,
                harmonics,
                n_fft,
                eta_rule=eta_rule,
                support_rule=support_rule,
            )

            stored_harmonics = ahmed[f"{vital}_harmonic_bins"][row][present]
            stored_harmonic_present = ahmed[f"{vital}_harmonic_present"][row][present]
            for index in range(int(candidate_bins.size)):
                assert stored_harmonic_present[index].tolist() == (
                    [True] * harmonics
                    + [False] * (stored_harmonic_present.shape[1] - harmonics)
                ), "the persisted harmonic row does not carry exactly H real cells"
            assert np.array_equal(
                stored_harmonics[:, :harmonics], harmonic_bins
            ), f"{vital} harmonic-bin matrix does not equal q, 2q, ..., Hq"
            assert np.all(stored_harmonics[:, harmonics:] == 0)

            assert np.array_equal(
                ahmed[f"{vital}_support_mask"][row][present], supported
            ), f"{vital} support mask is not strict H*q < f_Nyquist"
            assert np.array_equal(
                ahmed[f"{vital}_nyquist_mask"][row][present], nyquist_degenerate
            ), f"{vital} Nyquist-degenerate mask does not match H*q == f_Nyquist"
            tally["nyquist_degenerate_candidates"] += int(np.count_nonzero(nyquist_degenerate))

            if vital == "breath":
                keep = np.ones(int(candidate_bins.size), dtype=bool)
            else:
                breath_bin = (
                    int(ahmed["breath_selected_bin"][row])
                    if bool(ahmed["breath_selected_bin_present"][row])
                    else None
                )
                keep = _oracle_keep_mask(
                    _oracle_suppression_rule(arm_id), candidate_bins, breath_bin
                )
            assert np.array_equal(
                ahmed[f"{vital}_suppression_mask"][row][present], keep
            ), f"{vital} suppression (keep) mask does not match the arm's declared rule"
            tally["suppressed_candidates"] += int(np.count_nonzero(~keep))

            eligible = supported & keep
            assert np.array_equal(
                ahmed[f"{vital}_eligibility_mask"][row][present], eligible
            ), f"{vital} eligibility is not (supported AND not suppressed)"

            scores_post = np.where(eligible, scores_pre, np.nan)
            for name, expected in (
                ("score_pre_suppression", scores_pre),
                ("score_post_suppression", scores_post),
            ):
                stored_values = ahmed[f"{vital}_{name}"][row][present]
                stored_present = ahmed[f"{vital}_{name}_present"][row][present]
                for index in range(int(candidate_bins.size)):
                    if np.isfinite(expected[index]):
                        assert bool(stored_present[index]), (
                            f"{vital} {name} is absent for a scored candidate"
                        )
                        assert float(stored_values[index]) == pytest.approx(
                            float(expected[index]), rel=_ORACLE_RTOL, abs=_ORACLE_ATOL
                        ), f"{vital} {name} does not equal the recomputed A(q)"
                    else:
                        assert not bool(stored_present[index]), (
                            f"{vital} {name} is present for an unscored candidate"
                        )
                        assert float(stored_values[index]) == 0.0

            selection = _oracle_select(scores_post, candidate_bins)
            if selection is None:
                assert not bool(ahmed[f"{vital}_selected_bin_present"][row])
                assert not bool(ahmed[f"{vital}_valid"][row])
                tally["vital_stages"] += 1
                continue

            assert bool(ahmed[f"{vital}_selected_bin_present"][row])
            assert int(ahmed[f"{vital}_selected_bin"][row]) == selection["selected_bin"], (
                f"{vital} selected bin is not the argmax over eligible candidates"
            )
            assert float(ahmed[f"{vital}_selected_score"][row]) == pytest.approx(
                selection["selected_score"], rel=_ORACLE_RTOL, abs=_ORACLE_ATOL
            )
            assert float(ahmed[f"{vital}_selected_frequency_hz"][row]) == pytest.approx(
                float(frequency_grid_hz[selection["selected_bin"]]),
                rel=_ORACLE_RTOL,
                abs=_ORACLE_ATOL,
            )
            if selection["runner_index"] is None:
                assert not bool(ahmed[f"{vital}_runner_up_bin_present"][row])
            else:
                runner_index = selection["runner_index"]
                assert bool(ahmed[f"{vital}_runner_up_bin_present"][row])
                assert int(ahmed[f"{vital}_runner_up_bin"][row]) == int(
                    candidate_bins[runner_index]
                ), f"{vital} runner-up bin does not match the recomputed ordering"
                assert float(ahmed[f"{vital}_runner_up_score"][row]) == pytest.approx(
                    float(scores_post[runner_index]), rel=_ORACLE_RTOL, abs=_ORACLE_ATOL
                )
                assert float(
                    ahmed[f"{vital}_runner_up_frequency_hz"][row]
                ) == pytest.approx(
                    float(frequency_grid_hz[int(candidate_bins[runner_index])]),
                    rel=_ORACLE_RTOL,
                    abs=_ORACLE_ATOL,
                )

            # A non-unique maximum is a declared invalidity, never a silent tie-break.
            assert bool(ahmed[f"{vital}_valid"][row]) == selection["unique_maximum"]
            if bool(ahmed[f"{vital}_valid"][row]):
                assert str(ahmed[f"{vital}_reason"][row]) == "ok"
                assert bool(ahmed[f"{vital}_reported_bpm_present"][row])
                # Layer B: q = f, rate = 60q.  Layer A's 30q would be the classic
                # factor-of-two error this correction exists to prevent.
                assert float(ahmed[f"{vital}_reported_bpm"][row]) == pytest.approx(
                    bpm_per_hz * float(frequency_grid_hz[selection["selected_bin"]]),
                    rel=_ORACLE_RTOL,
                    abs=_ORACLE_ATOL,
                ), f"{vital} reported bpm is not 60 * f(selected bin)"
            else:
                assert str(ahmed[f"{vital}_reason"][row]) != "ok"
                assert not bool(ahmed[f"{vital}_reported_bpm_present"][row])
            tally["vital_stages"] += 1

        tally["ahmed_rows"] += 1

    # Every estimator row in rows.json must agree with the evidence it points at.
    assert rows["schema_version"] == 2
    ahmed_keys = {
        (
            str(ahmed["capture_id"][i]),
            str(ahmed["lock_estimand_id"][i]),
            int(ahmed["k"][i]),
            str(ahmed["arm_id"][i]),
        ): i
        for i in range(int(ahmed["arm_id"].size))
    }
    for record in rows["rows"]:
        key = (
            record["capture_id"],
            record["lock_estimand_id"],
            record["k"],
            record["arm_id"],
        )
        if key not in ahmed_keys:
            continue  # the production arm keeps its evidence in the native tree
        index = ahmed_keys[key]
        assert record["hr_valid"] is bool(ahmed["heart_valid"][index])
        assert record["br_valid"] is bool(ahmed["breath_valid"][index])
        if record["hr_valid"]:
            assert record["hr_raw"] == pytest.approx(
                float(ahmed["heart_reported_bpm"][index]),
                rel=_ORACLE_RTOL,
                abs=_ORACLE_ATOL,
            )
        if record["br_valid"]:
            assert record["br_bpm"] == pytest.approx(
                float(ahmed["breath_reported_bpm"][index]),
                rel=_ORACLE_RTOL,
                abs=_ORACLE_ATOL,
            )
    return tally


#: Every function that participates in the reconstruction mathematics.
_ORACLE_FUNCTIONS = (
    _oracle_harmonic_count,
    _oracle_suppression_rule,
    _oracle_geometry,
    _oracle_candidate_bins,
    _oracle_accumulate,
    _oracle_keep_mask,
    _oracle_select,
    _f4_open_persisted,
    _f4_reconstruct,
)

#: Production symbols the oracle must never reach.  ``_suppression_mask`` is
#: deliberately absent: the persisted *field* is called ``<vital>_suppression_mask``,
#: so a bare substring ban would be a false positive.  The module-scope and
#: ``src.m8`` bans already make the production helper unreachable.
_FORBIDDEN_ORACLE_SYMBOLS = (
    "src.m8",
    "ahmed_fig8",
    "ahmed_transfer",
    "accumulate_harmonics",
    "accumulate_layer_a_profile",
    "suppression_eligibility_mask",
    "_select_scores",
    "_score_vital",
    "_support_masks",
    "estimate_phase_ha",
    "AhmedPhaseEstimatorSuite",
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_reconstruction_oracle_is_independent_of_production_mathematics():
    """The oracle must not call, import or delegate to the Ahmed production core.

    An oracle that re-runs the production accumulator and compares it with itself
    proves nothing, so independence is enforced structurally rather than by review
    convention.  Production may appear only inside the fixture builder.
    """
    import inspect
    import sys

    module = sys.modules[__name__]
    for name, value in vars(module).items():
        origin = getattr(value, "__module__", None)
        if isinstance(origin, str):
            assert not origin.startswith("src.m8"), (
                f"module-level name {name!r} binds production Ahmed code"
            )

    for function in _ORACLE_FUNCTIONS:
        source = inspect.getsource(function)
        for symbol in _FORBIDDEN_ORACLE_SYMBOLS:
            assert symbol not in source, (
                f"{function.__name__} references production symbol {symbol!r}"
            )


def test_persisted_ahmed_evidence_reconstructs_at_the_equation_level(tmp_path):
    """Rebuild A(q), masks, selection and reported rate from the stored arrays alone."""
    _run, bundle = _f4_persist_bundle(tmp_path)
    tally = _f4_reconstruct(bundle.root)

    # 1 capture x 2 windows x 2 locks x 6 Ahmed arms; each row carries breath + heart.
    assert tally["ahmed_rows"] == 24
    assert tally["vital_stages"] == 48
    # Hand-counted: heart candidate 60 reaches exactly n_fft/2 = 300 at H=5, so it is
    # Nyquist-degenerate on each of the 12 H=5 rows (3 profiles x 2 windows x 2 locks)
    # and supported nowhere.  The strict rule and the degenerate rule are both exercised.
    assert tally["nyquist_degenerate_candidates"] == 12
    # Hand-counted: only the Eq. 26 arms suppress here.  Window k=0 selects breathing
    # bin 7, whose multiples inside the heart band 24..60 are {28, 35, 42, 49, 56} = 5;
    # window k=1 selects bin 11, whose multiples are {33, 44, 55} = 3.  (5 + 3) x 2
    # harmonic counts x 2 locks = 32.
    assert tally["suppressed_candidates"] == 32


def test_persisted_ahmed_suppression_identities_stay_separate(tmp_path):
    """Eq. 26 removes breathing multiples; the prose rule does not, on disjoint bands."""
    _run, bundle = _f4_persist_bundle(tmp_path)
    _shared, ahmed, _rows = _f4_open_persisted(bundle.root)

    by_arm = {
        (str(ahmed["arm_id"][i]), int(ahmed["k"][i])): i
        for i in range(int(ahmed["arm_id"].size))
        if str(ahmed["lock_estimand_id"][i]) == "recorded_lock_as_captured"
    }
    for harmonics in (3, 5):
        unsuppressed = by_arm[(f"ahmed_phase_h{harmonics}_figure_visible_unsuppressed", 1)]
        eq26 = by_arm[(f"ahmed_phase_h{harmonics}_eq26_multiples_suppressed", 1)]
        prose = by_arm[(f"ahmed_phase_h{harmonics}_prose_low_or_equal_suppressed", 1)]

        present = ahmed["heart_candidate_present"][unsuppressed]
        candidates = ahmed["heart_candidate_bins"][unsuppressed][present]
        breath_bin = int(ahmed["breath_selected_bin"][unsuppressed])

        keep_unsuppressed = ahmed["heart_suppression_mask"][unsuppressed][present]
        keep_eq26 = ahmed["heart_suppression_mask"][eq26][present]
        keep_prose = ahmed["heart_suppression_mask"][prose][present]

        assert keep_unsuppressed.all()
        assert np.array_equal(keep_eq26, (candidates % breath_bin) != 0)
        assert not keep_eq26.all()
        # Real HR band starts at 0.80 Hz and the BR band ends at 0.50 Hz, so the prose
        # rule is a dependent duplicate of the unsuppressed arm here (plan section 3).
        assert np.array_equal(keep_prose, candidates > breath_bin)
        assert keep_prose.all()

        # Suppression changes the reported estimate's neighbourhood, not just a label:
        # window k=1 places the heart runner-up at bin 33 = 3 x 11, which Eq. 26 removes.
        assert int(ahmed["heart_runner_up_bin"][unsuppressed]) != int(
            ahmed["heart_runner_up_bin"][eq26]
        )
        assert int(ahmed["heart_runner_up_bin"][unsuppressed]) == int(
            ahmed["heart_runner_up_bin"][prose]
        )


def test_persisted_ahmed_storage_contract_shapes_dtypes_and_hash_round_trip(tmp_path):
    """No object arrays, exact shapes/dtypes, and a byte-identical hash round trip."""
    from src.m4.bundle import verify_bundle

    run, bundle = _f4_persist_bundle(tmp_path)
    verify_bundle(bundle.root)  # every payload re-hashes to its manifest entry

    expected_shared = pack_shared_evidence(run.shared_evidence)
    expected_ahmed = pack_ahmed_evidence(run.ahmed_evidence)

    with np.load(bundle.root / "shared_evidence.npz", allow_pickle=False) as shared, np.load(
        bundle.root / "ahmed_evidence.npz", allow_pickle=False
    ) as ahmed:
        for loaded, expected in ((shared, expected_shared), (ahmed, expected_ahmed)):
            assert set(loaded.files) == set(expected)
            for key, original in expected.items():
                assert loaded[key].dtype != object
                assert loaded[key].dtype == original.dtype
                assert loaded[key].shape == original.shape
                assert array_sha256(loaded[key]) == array_sha256(original)

        # Frozen geometry: 2 windows x 2 locks shared rows, 600-sample native rFFT.
        assert shared["phase"].shape == (4, 600)
        assert shared["frequency_grid_hz"].shape == (4, 301)
        assert shared["spectrum_magnitude"].shape == (4, 301)
        assert shared["spectrum_magnitude"].dtype == np.float64
        assert shared["k"].dtype == np.int64
        assert shared["capture_id"].dtype.kind == "U"

        # 24 Ahmed rows; breathing band 0.10-0.50 Hz spans bins 3..15 (13 candidates),
        # heart band 0.80-2.00 Hz spans bins 24..60 (37 candidates); the ragged harmonic
        # axis is padded to max(H) = 5.
        assert ahmed["arm_id"].shape == (24,)
        assert ahmed["breath_candidate_bins"].shape == (24, 13)
        assert ahmed["breath_harmonic_bins"].shape == (24, 13, 5)
        assert ahmed["heart_candidate_bins"].shape == (24, 37)
        assert ahmed["heart_harmonic_bins"].shape == (24, 37, 5)
        assert ahmed["heart_candidate_bins"].dtype == np.int64
        assert ahmed["heart_harmonic_bins"].dtype == np.int64
        assert ahmed["heart_support_mask"].dtype == np.bool_
        assert ahmed["heart_score_pre_suppression"].dtype == np.float64

        # NaN never reaches the file: absence is carried by an explicit presence mask.
        for vital in ("breath", "heart"):
            for name in ("score_pre_suppression", "score_post_suppression"):
                assert np.all(np.isfinite(ahmed[f"{vital}_{name}"]))
                assert ahmed[f"{vital}_{name}_present"].dtype == np.bool_


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        # eta = H + 1 is the equation-literal `matrix_eta` reading; it has no Layer B
        # arm and must fail on the full score arrays, not merely on the winning bin.
        ({"eta_rule": "matrix_eta"}, "A\\(q\\)"),
        # Nyquist equality is degenerate, never supported.
        ({"support_rule": "nyquist_equality_allowed"}, "strict"),
        # Layer A's 30q instead of Layer B's 60q: the factor-of-two error.
        ({"bpm_per_hz": 30.0}, "reported bpm"),
        # The evidence join is exact on (capture_id, lock_estimand_id, k): joining
        # window k=1's Ahmed row to window k=0's spectrum must not reconstruct.
        ({"join_on_k": False}, "A\\(q\\)"),
    ],
)
def test_reconstruction_oracle_bites_when_the_contract_is_mutated(tmp_path, kwargs, expected):
    """A green oracle proves nothing unless the wrong mathematics is rejected."""
    _run, bundle = _f4_persist_bundle(tmp_path)
    _f4_reconstruct(bundle.root)  # the approved contract passes
    with pytest.raises(AssertionError, match=expected):
        _f4_reconstruct(bundle.root, **kwargs)


@pytest.mark.parametrize(
    "field, expected",
    [
        ("spectrum_magnitude", "stored spectrum"),
        ("heart_score_pre_suppression", "score_pre_suppression"),
        ("heart_suppression_mask", "suppression"),
    ],
)
def test_reconstruction_oracle_bites_when_the_persisted_arrays_are_perturbed(
    tmp_path, field, expected
):
    """Perturbing one persisted value must break the reconstruction, not be absorbed."""
    _run, bundle = _f4_persist_bundle(tmp_path)
    shared, ahmed, rows = _f4_open_persisted(bundle.root)

    if field == "spectrum_magnitude":
        shared[field][0, 37] = float(shared[field][0, 37]) * 1.5 + 1.0
    elif field == "heart_score_pre_suppression":
        scored = np.flatnonzero(ahmed["heart_score_pre_suppression_present"][0])
        assert scored.size, "fixture must contain at least one scored heart candidate"
        ahmed[field][0, scored[0]] = float(ahmed[field][0, scored[0]]) + 1.0
    else:
        ahmed[field][0, 0] = not bool(ahmed[field][0, 0])

    mutated = tmp_path / "mutated"
    mutated.mkdir()
    np.savez(mutated / "shared_evidence.npz", **shared)
    np.savez(mutated / "ahmed_evidence.npz", **ahmed)
    (mutated / "rows.json").write_text(json.dumps(rows), encoding="utf-8")

    with pytest.raises(AssertionError, match=expected):
        _f4_reconstruct(mutated)
