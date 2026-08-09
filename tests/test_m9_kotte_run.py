"""Independent fixture tests for the radar-only M9.3 runner.

No production radar capture or physiological reference file is opened here.  The binary
decoder oracle is the already-established ``src.radar_io.read_adc_bin`` implementation.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from scripts import m9_kotte_run as runner
from src import warmup_select
from src.radar_io import ChirpConfig, read_adc_bin


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "experiments" / "m9_kotte" / "config.yaml"
BYTES_PER_PRODUCTION_FRAME = 32 * 4 * 256 * 4


def _canonical_document() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def _metadata(*, iq_swap: bool = False, recorded_bin: int = 7) -> dict:
    return {
        "completion_status": "completed",
        "raw_stream_format": "adc_bytes_no_packet_headers",
        "iq_swap": iq_swap,
        "warmup_selected_bin": recorded_bin,
        "config": {
            "profile": {
                "num_chirps_per_frame": 32,
                "num_rx": 4,
                "num_adc_samples": 256,
                "iq_swap": iq_swap,
            },
            "hw_profile": {"num_rx": 4, "num_adc_samples": 256, "tx_channel_en": 1},
            "hw_frame": {"num_loops": 32, "period_ms": 50.0},
            "session": {"frame_rate_hz": 20.0},
            "capture": {"raw_stream_format": "adc_bytes_no_packet_headers"},
        },
        "live_packet_stats": {
            "n_dropped": 0,
            "zero_filled_bytes": 0,
            "mirror_truncated_bytes": 17,
        },
    }


def _write_sparse_raw(path: Path, num_frames: int, extra_bytes: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    size = num_frames * BYTES_PER_PRODUCTION_FRAME + extra_bytes
    with path.open("wb") as handle:
        if size:
            handle.seek(size - 1)
            handle.write(b"\0")


def _write_fixture_config(root: Path, *, num_frames: int = 1207) -> tuple[Path, Path]:
    capture_dir = root / "capture"
    _write_sparse_raw(capture_dir / "adc_stream.bin", num_frames)
    (capture_dir / "run_metadata.json").write_text(
        json.dumps(_metadata()), encoding="utf-8"
    )
    live_path = root / "live.yaml"
    live_path.write_text("range_selection: fixture\n", encoding="utf-8")
    document = _canonical_document()
    document["stage_a"]["grid_step_bpm"] = 24.0
    document["radar_evaluation"] = {
        "range_selection": {
            "source": "current_production_rerun_lock",
            "selector": "src.warmup_select.run_warmup_selection",
            "selector_config": "live.yaml",
            "recorded_warmup_lock_role": "diagnostic_only_never_pooled",
            "kotte_all_bin_selection_allowed": False,
        },
        "window_grid": {
            "window_frames": 600,
            "step_frames": 600,
            "interval": "half_open",
            "first_window_role": "lock_selection_in_sample",
            "comparative_windows_start_k": 1,
        },
        "captures": [
            {
                "capture_id": "fixture",
                "directory": "capture",
                "subject": "synthetic",
                "protocol_role": "fixture",
                "data_role": "test_only",
            }
        ],
    }
    # Deliberate traps: the runner must not consume these M9.4-only paths.
    document["scoring"] = {
        "capture_references": [{"csv": "must_not_open_reference.csv"}],
        "outcome_threshold": "must_not_read",
    }
    config_path = root / "config.yaml"
    config_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return config_path, capture_dir


def _source_hashes() -> dict[str, str]:
    names = (
        "kotte_core",
        "runner",
        "warmup_selector",
        "window_pipeline",
        "radar_io",
        "live_selector_config",
        "m9_config",
        "m9_plan",
        "focused_core_tests",
        "runner_tests",
    )
    return {name: f"{index:064x}" for index, name in enumerate(names, start=1)}


class _FixturePipeline:
    def __init__(self, *, failing_arm: str | None = None) -> None:
        self.decode_starts: list[int] = []
        self.decode_counts: list[int] = []
        self.selector_cube_id: int | None = None
        self.adapter_cube_ids: list[int] = []
        self.adapter_z_ids: list[int] = []
        self.estimator_z_ids: list[int] = []
        self.selector_calls = 0
        self.failing_arm = failing_arm

    def decode(self, _path, *, geometry, start_frame, frame_count):
        self.decode_starts.append(start_frame)
        self.decode_counts.append(frame_count)
        base = np.zeros((frame_count, 1, 1, 1), dtype=np.complex64)
        base[:, 0, 0, 0] = np.exp(1j * 2 * np.pi * 0.2 * np.arange(frame_count) / 20)
        return np.broadcast_to(base, (frame_count, 32, 4, 256))

    def selector(self, cube, candidate_bins, config, *, fs):
        self.selector_calls += 1
        self.selector_cube_id = id(cube)
        assert candidate_bins == [5]
        assert fs == 20.0
        return 5, {"unused": True}, {"fixture_selector": True}

    def adapter(self, cube, selected_range_bin, *, chirp_loop_index):
        self.adapter_cube_ids.append(id(cube))
        assert selected_range_bin == 5
        assert chirp_loop_index == 0
        frame = np.arange(600, dtype=np.float64)[:, None]
        rx = np.arange(4, dtype=np.float64)[None, :]
        z = np.asarray(np.exp(1j * (0.03 * frame + 0.2 * rx)), dtype=np.complex128)
        self.adapter_z_ids.append(id(z))
        return z

    def estimator(self, z, *, config, arm, fs_hz):
        self.estimator_z_ids.append(id(z))
        if arm.arm_id == self.failing_arm:
            raise RuntimeError("declared synthetic estimator failure")
        nf1, nf2 = 2, 3
        surfaces = np.full((37, nf1, nf2), 2.5 + arm.loading_delta)
        evidence = {
            "z": z,
            "objective_name": np.asarray("regularized_kotte_power"),
            "cpi_eigvals": np.ones((37, 16)),
            "cpi_ranks": np.full(37, 4, dtype=np.int32),
            "cpi_delta_bar": np.full(37, arm.loading_delta),
            "cpi_valid": np.ones(37, dtype=bool),
            "cpi_cause_codes": np.zeros(37, dtype=np.int16),
            "cpi_masked_fraction": np.zeros(37, dtype=np.float64),
            "f1_grid_hz": np.asarray([-0.1, 0.1]),
            "f2_grid_hz": np.asarray([-0.8, 0.8, 1.2]),
            "cpi_regularized_kotte_power": surfaces,
            "cpi_constraint_rcond": np.ones_like(surfaces),
            "cpi_constraint_mask": np.zeros_like(surfaces, dtype=bool),
            "cpi_raw_signed_pairs_hz": np.tile([0.1, 0.8], (37, 1)),
            "cpi_estimates_bpm": np.tile([6.0, 48.0], (37, 1)),
            "cpi_pair_margin_db": np.full(37, 1.25),
        }
        native = {
            "br_valid": True,
            "hr_valid": True,
            "rej_reason": "",
            "br_bpm": 6.0,
            "hr_raw": 48.0,
            "selected_hz": [0.1, 0.8],
            "selected_raw_signed_hz": [0.1, 0.8],
            "medoid_cpi_index": 0,
            "pair_margin_db": 1.25,
            "n_cpis_valid": 37,
        }
        return native, evidence


def _run_fixture(root: Path, *, run_id: str, pipeline: _FixturePipeline) -> Path:
    config_path, _ = _write_fixture_config(root)
    paper_pdf_path = root / "fixture_paper.pdf"
    paper_pdf_path.write_bytes(b"independent fixture paper bytes")
    return runner.run_radar_only(
        config_path=config_path,
        output_root=root / "outputs",
        repo_root=root,
        paper_pdf_path=paper_pdf_path,
        official=False,
        decoder=pipeline.decode,
        selector=pipeline.selector,
        candidate_deriver=lambda _config: [5],
        slow_time_adapter=pipeline.adapter,
        window_estimator=pipeline.estimator,
        raw_hasher=lambda _path: "a" * 64,
        source_hashes_override=_source_hashes(),
        run_id=run_id,
        created_utc="2030-01-02T03:04:05+00:00",
    )


def test_manifest_is_exact_unique_and_radar_config_has_no_references() -> None:
    document = _canonical_document()
    radar = document["radar_evaluation"]
    specs = runner.validate_manifest(radar, repo_root=REPO_ROOT, require_canonical=True)
    assert {spec.capture_id for spec in specs} == set(runner.CANONICAL_CAPTURE_DIRECTORIES)
    assert len(specs) == 8
    runner.assert_radar_section_reference_isolated(radar)
    rendered = yaml.safe_dump(radar).lower()
    assert "reference" not in rendered
    assert ".csv" not in rendered


def test_manifest_rejects_duplicate_and_noncanonical_capture() -> None:
    radar = deepcopy(_canonical_document()["radar_evaluation"])
    radar["captures"][1]["capture_id"] = radar["captures"][0]["capture_id"]
    with pytest.raises(ValueError, match="unique"):
        runner.validate_manifest(radar, repo_root=REPO_ROOT, require_canonical=False)
    radar = deepcopy(_canonical_document()["radar_evaluation"])
    radar["captures"] = radar["captures"][:-1]
    with pytest.raises(ValueError, match="exactly eight"):
        runner.validate_manifest(radar, repo_root=REPO_ROOT, require_canonical=True)


def test_manifest_rejects_capture_directory_escape(tmp_path: Path) -> None:
    radar = deepcopy(_canonical_document()["radar_evaluation"])
    radar["captures"] = [
        {
            "capture_id": "fixture",
            "directory": "../outside",
            "subject": "x",
            "protocol_role": "x",
            "data_role": "test_only",
        }
    ]
    with pytest.raises(ValueError, match="escapes repository"):
        runner.validate_manifest(radar, repo_root=tmp_path, require_canonical=False)


def test_section_selection_never_accesses_scoring() -> None:
    class Guarded(dict):
        def __getitem__(self, key):
            if key not in {"stage_a", "radar_evaluation"}:
                raise AssertionError(f"forbidden section accessed: {key}")
            return super().__getitem__(key)

    stage_a, radar = runner.select_runner_sections(
        Guarded(stage_a={"a": 1}, radar_evaluation={"b": 2}, scoring={"trap": True})
    )
    assert stage_a == {"a": 1}
    assert radar == {"b": 2}


def test_runner_has_no_reference_or_scoring_imports() -> None:
    source = (REPO_ROOT / "scripts" / "m9_kotte_run.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    forbidden = ("masimo", "pandas", "comparator", "scoring")
    assert not [name for name in imports if any(token in name.lower() for token in forbidden)]
    assert "capture_references" not in source


def test_streaming_raw_hash_uses_bounded_reads() -> None:
    payload = bytes(range(251)) * 9000

    class TrackingReader(io.BytesIO):
        def __init__(self, values: bytes) -> None:
            super().__init__(values)
            self.read_sizes: list[int] = []

        def read(self, size: int = -1) -> bytes:
            self.read_sizes.append(size)
            assert size == 1024 * 1024
            return super().read(size)

    reader = TrackingReader(payload)

    class FakePath:
        def open(self, mode: str):
            assert mode == "rb"
            return reader

    assert runner.sha256_file(FakePath()) == sha256(payload).hexdigest()
    assert len(reader.read_sizes) >= 3  # two data chunks plus the terminating empty read


@pytest.mark.parametrize("iq_swap", [False, True])
def test_window_decoder_is_bit_exact_radar_io_for_nonzero_start(tmp_path: Path, iq_swap: bool) -> None:
    geometry = runner.RadarGeometry(2, 2, 3, 1, 0.05, iq_swap)
    rng = np.random.default_rng(931)
    words = rng.integers(-30000, 30000, size=3 * geometry.bytes_per_frame // 2, dtype=np.int16)
    path = tmp_path / "small.bin"
    words.astype("<i2").tofile(path)
    config = ChirpConfig(3, 2, 1, 2, 3, 20.0, 0.1, iq_swap=iq_swap)
    expected = read_adc_bin(path, config)[1:3]
    actual = runner.decode_frame_window(path, geometry=geometry, start_frame=1, frame_count=2)
    assert actual.dtype == np.complex64
    np.testing.assert_array_equal(actual, expected)


def test_window_decoder_rejects_truncated_or_out_of_bounds_request(tmp_path: Path) -> None:
    geometry = runner.RadarGeometry(1, 1, 2, 1, 0.05, False)
    path = tmp_path / "truncated.bin"
    path.write_bytes(b"\0" * (geometry.bytes_per_frame + 2))
    with pytest.raises(ValueError, match="extends beyond"):
        runner.decode_frame_window(path, geometry=geometry, start_frame=1, frame_count=1)
    with pytest.raises(ValueError, match="nonnegative"):
        runner.decode_frame_window(path, geometry=geometry, start_frame=-1, frame_count=1)


def test_complete_windows_use_floor_and_ignore_partial_tail() -> None:
    assert runner.complete_window_count(0) == 0
    assert runner.complete_window_count(599) == 0
    assert runner.complete_window_count(600) == 1
    assert runner.complete_window_count(1207) == 2


def test_runner_uses_shared_selector_identity() -> None:
    assert runner.run_warmup_selection is warmup_select.run_warmup_selection
    assert runner.derive_candidate_bins is warmup_select.derive_candidate_bins


def test_current_live_config_candidate_bins_match_independent_distance_oracle() -> None:
    live_path = REPO_ROOT / "scripts" / "live_demo_config.yaml"
    live = yaml.safe_load(live_path.read_text(encoding="utf-8"))
    assert live["bin_selection"]["candidate_bins"] is None
    lo_m, hi_m = live["protocol"]["subject_distance_m"]
    resolution_m = float(live["profile"]["range_resolution_m"])
    expected = list(
        range(
            int(np.ceil(lo_m / resolution_m)),
            int(np.floor(hi_m / resolution_m)) + 1,
        )
    )
    assert expected == list(range(19, 33))
    assert runner.derive_candidate_bins(live) == expected


def test_fixture_run_decodes_once_per_window_and_reuses_k0(tmp_path: Path) -> None:
    pipeline = _FixturePipeline()
    out = _run_fixture(tmp_path, run_id="fixture_a", pipeline=pipeline)
    assert pipeline.decode_starts == [0, 600]
    assert pipeline.decode_counts == [600, 600]
    assert pipeline.selector_calls == 1
    assert pipeline.selector_cube_id == pipeline.adapter_cube_ids[0]
    assert pipeline.estimator_z_ids == [
        pipeline.adapter_z_ids[0],
        pipeline.adapter_z_ids[0],
        pipeline.adapter_z_ids[1],
        pipeline.adapter_z_ids[1],
    ]
    rows = json.loads((out / "estimates.json").read_text(encoding="utf-8"))
    assert len(rows) == 4
    assert {(row["window_index"], row["arm_id"]) for row in rows} == {
        (0, "kotte_cpi_medoid_nc16_dl1em2"),
        (0, "kotte_cpi_medoid_nc16_dl1em4"),
        (1, "kotte_cpi_medoid_nc16_dl1em2"),
        (1, "kotte_cpi_medoid_nc16_dl1em4"),
    }
    assert {
        (row["window_index"], row["frame_start"], row["frame_end"])
        for row in rows
    } == {(0, 0, 600), (1, 600, 1200)}
    run_meta = json.loads((out / "run_meta.json").read_text(encoding="utf-8"))
    assert run_meta["capture_inputs"][0]["partial_tail_frames_ignored"] == 7


def test_evidence_is_complete_shared_and_pickle_free(tmp_path: Path) -> None:
    out = _run_fixture(tmp_path, run_id="fixture_evidence", pipeline=_FixturePipeline())
    rows = json.loads((out / "estimates.json").read_text(encoding="utf-8"))
    first_window = [row for row in rows if row["window_index"] == 0]
    assert first_window[0]["shared_z_artifact"] == first_window[1]["shared_z_artifact"]
    shared_path = out / first_window[0]["shared_z_artifact"]
    assert runner.sha256_file(shared_path) == first_window[0]["shared_z_artifact_sha256"]
    with np.load(shared_path, allow_pickle=False) as shared:
        assert shared["z"].shape == (600, 4)
        assert shared["z"].dtype == np.complex128
        assert str(shared["lock_estimand"]) == "current_production_rerun_lock"
    required = {
        "cpi_frame_spans",
        "cpi_eigvals",
        "cpi_ranks",
        "cpi_delta_bar",
        "cpi_valid",
        "cpi_cause_codes",
        "cpi_masked_fraction",
        "cpi_constraint_rcond",
        "cpi_constraint_mask",
        "cpi_regularized_kotte_power",
        "f1_grid_hz",
        "f2_grid_hz",
        "cpi_raw_signed_pairs_hz",
        "cpi_canonical_pairs_hz",
        "cpi_estimates_bpm",
        "cpi_pair_margin_db",
        "medoid_cpi_index",
        "selected_hz",
        "pair_margin_db",
        "shared_z_artifact_sha256",
        "raw_sha256",
        "config_sha256",
    }
    for row in first_window:
        arm_path = out / row["evidence_artifact"]
        assert runner.sha256_file(arm_path) == row["evidence_sha256"]
        with np.load(arm_path, allow_pickle=False) as arm_evidence:
            assert required <= set(arm_evidence.files)
            assert "z" not in arm_evidence.files
            assert str(arm_evidence["objective_name"]) == "regularized_kotte_power"

    manifest = json.loads((out / "evidence_manifest.json").read_text(encoding="utf-8"))
    assert {entry["role"] for entry in manifest} == {"shared_z", "arm"}
    assert all(
        runner.sha256_file(out / entry["artifact"]) == entry["sha256"]
        for entry in manifest
    )
    handoff = json.loads((out / "radar_only_handoff.json").read_text(encoding="utf-8"))
    expected_outputs = {
        "estimates_json": "estimates.json",
        "estimates_csv": "estimates.csv",
        "lock_map": "lock_map.json",
        "capture_failures": "capture_failures.json",
        "evidence_manifest": "evidence_manifest.json",
        "run_meta": "run_meta.json",
    }
    assert handoff["role"] == "immutable_radar_only_handoff"
    assert handoff["estimator_settings_mutable"] is False
    for key, relative in expected_outputs.items():
        assert handoff["artifacts"][key] == runner.sha256_file(out / relative)


def test_hash_binding_and_recorded_lock_are_separate(tmp_path: Path) -> None:
    out = _run_fixture(tmp_path, run_id="fixture_hashes", pipeline=_FixturePipeline())
    rows = json.loads((out / "estimates.json").read_text(encoding="utf-8"))
    lock_map = json.loads((out / "lock_map.json").read_text(encoding="utf-8"))
    lock_payload = json.loads((out / lock_map[0]["lock_evidence"]).read_text(encoding="utf-8"))
    assert lock_payload["canonical"]["selected_bin"] == 5
    assert lock_payload["diagnostic_recorded"]["selected_bin"] == 7
    assert lock_payload["diagnostic_recorded"]["estimates_generated"] is False
    assert lock_map[0]["recorded_and_rerun_are_separate"] is True
    assert all(row["lock_estimand"] == "current_production_rerun_lock" for row in rows)
    assert all(row["recorded_warmup_bin_diagnostic_only"] == 7 for row in rows)
    for row in rows:
        assert row["raw_sha256"] == "a" * 64
        assert row["core_sha256"] == _source_hashes()["kotte_core"]
        assert runner.sha256_file(out / row["lock_evidence"]) == row["lock_evidence_sha256"]


def test_radar_artifact_contains_no_reference_path_or_value(tmp_path: Path) -> None:
    out = _run_fixture(tmp_path, run_id="fixture_isolated", pipeline=_FixturePipeline())
    assert not (out / "config.yaml").exists()
    snapshot = json.loads((out / "radar_runner_config.json").read_text(encoding="utf-8"))
    assert set(snapshot) == {"stage_a", "radar_evaluation"}
    assert "scoring" not in snapshot
    for path in out.rglob("*"):
        if path.suffix not in {".json", ".csv"}:
            continue
        text = path.read_text(encoding="utf-8").lower()
        assert "must_not_open_reference" not in text
        assert "beats / min" not in text
        assert "capture_references" not in text


@pytest.mark.parametrize("bad_kind", ["shape", "dtype", "nonfinite"])
def test_invalid_decoded_window_fails_closed(tmp_path: Path, bad_kind: str) -> None:
    config_path, _ = _write_fixture_config(tmp_path, num_frames=600)

    def bad_decoder(*args, **kwargs):
        if bad_kind == "shape":
            return np.zeros((599, 32, 4, 256), dtype=np.complex64)
        if bad_kind == "dtype":
            return np.broadcast_to(np.zeros((600, 1, 1, 1), dtype=np.float32), (600, 32, 4, 256))
        base = np.zeros((600, 1, 1, 1), dtype=np.complex64)
        base[0, 0, 0, 0] = np.nan
        return np.broadcast_to(base, (600, 32, 4, 256))

    out = runner.run_radar_only(
        config_path=config_path,
        output_root=tmp_path / "outputs",
        repo_root=tmp_path,
        official=False,
        decoder=bad_decoder,
        selector=lambda *args, **kwargs: pytest.fail("selector must not see invalid cube"),
        candidate_deriver=lambda _: [5],
        raw_hasher=lambda _: "a" * 64,
        source_hashes_override=_source_hashes(),
        run_id=f"bad_{bad_kind}",
        created_utc="2030-01-02T03:04:05+00:00",
    )
    rows = json.loads((out / "estimates.json").read_text(encoding="utf-8"))
    assert len(rows) == 2
    assert {row["failure_reason"] for row in rows} == {"warmup_selection_failed"}


def test_runner_outputs_are_deterministic_for_same_fixture(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    out_a = _run_fixture(root_a, run_id="same", pipeline=_FixturePipeline())
    out_b = _run_fixture(root_b, run_id="same", pipeline=_FixturePipeline())
    for relative in (
        "estimates.json",
        "estimates.csv",
        "lock_map.json",
        "evidence_manifest.json",
        "evidence/fixture/k0000_shared_z.npz",
        "evidence/fixture/k0000_kotte_cpi_medoid_nc16_dl1em2.npz",
    ):
        assert runner.sha256_file(out_a / relative) == runner.sha256_file(out_b / relative)


def test_nonzero_window_failure_stops_capture_and_emits_all_remaining_rows(
    tmp_path: Path,
) -> None:
    config_path, _ = _write_fixture_config(tmp_path, num_frames=1805)
    pipeline = _FixturePipeline()

    def fail_second_window(path, *, geometry, start_frame, frame_count):
        if start_frame == 600:
            pipeline.decode_starts.append(start_frame)
            pipeline.decode_counts.append(frame_count)
            raise OSError("synthetic short read")
        return pipeline.decode(
            path,
            geometry=geometry,
            start_frame=start_frame,
            frame_count=frame_count,
        )

    out = runner.run_radar_only(
        config_path=config_path,
        output_root=tmp_path / "outputs",
        repo_root=tmp_path,
        official=False,
        decoder=fail_second_window,
        selector=pipeline.selector,
        candidate_deriver=lambda _config: [5],
        slow_time_adapter=pipeline.adapter,
        window_estimator=pipeline.estimator,
        raw_hasher=lambda _path: "a" * 64,
        source_hashes_override=_source_hashes(),
        run_id="window_failure",
        created_utc="2030-01-02T03:04:05+00:00",
    )
    assert pipeline.decode_starts == [0, 600]
    assert pipeline.selector_calls == 1
    rows = json.loads((out / "estimates.json").read_text(encoding="utf-8"))
    assert len(rows) == 6
    assert all(row["valid"] for row in rows if row["window_index"] == 0)
    failures = [row for row in rows if row["window_index"] in {1, 2}]
    assert len(failures) == 4
    assert {row["failure_reason"] for row in failures} == {
        "window_decode_or_adapter_failed"
    }
    assert {(row["frame_start"], row["frame_end"]) for row in failures} == {
        (600, 1200),
        (1200, 1800),
    }
    failure_payload = json.loads(
        (out / failures[0]["evidence_artifact"]).read_text(encoding="utf-8")
    )
    assert failure_payload["capture_stopped"] is True
    assert failure_payload["alternate_bin_rescue_attempted"] is False


def test_estimator_failure_still_emits_explicit_row_and_evidence(tmp_path: Path) -> None:
    failing = "kotte_cpi_medoid_nc16_dl1em4"
    out = _run_fixture(
        tmp_path, run_id="fixture_failure", pipeline=_FixturePipeline(failing_arm=failing)
    )
    rows = json.loads((out / "estimates.json").read_text(encoding="utf-8"))
    failures = [row for row in rows if row["arm_id"] == failing]
    assert len(failures) == 2
    assert all(row["valid"] is False for row in failures)
    assert all("estimator_exception:RuntimeError" in row["failure_reason"] for row in failures)
    assert all((out / row["evidence_artifact"]).is_file() for row in failures)


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda m: m.update(completion_status="interrupted"), "capture_incomplete"),
        (lambda m: m.update(raw_stream_format="packetized"), "raw_stream_format"),
        (
            lambda m: m["config"]["capture"].update(raw_stream_format="packetized"),
            "raw_stream_format",
        ),
        (lambda m: m["live_packet_stats"].update(n_dropped=1), "packet_drop"),
        (lambda m: m["live_packet_stats"].update(n_dropped="0"), "packet_drop"),
        (lambda m: m["live_packet_stats"].update(zero_filled_bytes=4), "zero_fill"),
        (lambda m: m["live_packet_stats"].update(zero_filled_bytes="0"), "zero_fill"),
        (
            lambda m: m["live_packet_stats"].update(mirror_truncated_bytes=-1),
            "packet_stats_invalid",
        ),
        (lambda m: m.pop("live_packet_stats"), "packet_stats_missing"),
        (
            lambda m: m["config"]["profile"].update(num_chirps_per_frame=31),
            "metadata_geometry",
        ),
        (lambda m: m["config"]["profile"].update(num_rx=3), "metadata_geometry"),
        (
            lambda m: m["config"]["profile"].update(num_adc_samples=255),
            "metadata_geometry",
        ),
        (
            lambda m: m["config"]["hw_profile"].update(num_rx=3),
            "metadata_geometry",
        ),
        (
            lambda m: m["config"]["hw_profile"].update(num_adc_samples=255),
            "metadata_geometry",
        ),
        (lambda m: m["config"]["hw_frame"].update(num_loops=31), "metadata_geometry"),
        (lambda m: m["config"]["hw_frame"].update(period_ms=51.0), "frame_timing"),
        (lambda m: m["config"]["session"].update(frame_rate_hz=19.0), "frame_timing"),
        (lambda m: m["config"]["hw_profile"].update(tx_channel_en=3), "metadata_geometry"),
        (
            lambda m: m["config"]["profile"].update(iq_swap=not m["iq_swap"]),
            "metadata_iq_swap",
        ),
        (lambda m: m.update(iq_swap="false"), "metadata_iq_swap"),
    ],
)
def test_capture_validation_fails_closed_for_metadata(
    tmp_path: Path, mutate, reason: str
) -> None:
    capture = tmp_path / "capture"
    _write_sparse_raw(capture / "adc_stream.bin", 600)
    metadata = _metadata()
    mutate(metadata)
    (capture / "run_metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    spec = runner.CaptureSpec("fixture", capture, "x", "x", "x")
    with pytest.raises(runner.CaptureInputError) as caught:
        runner.validate_capture_input(spec, window_frames=600, raw_hasher=lambda _: "a" * 64)
    assert caught.value.reason == reason


def test_capture_validation_rejects_partial_network_fragment(tmp_path: Path) -> None:
    capture = tmp_path / "capture"
    _write_sparse_raw(capture / "adc_stream.bin", 600, extra_bytes=2)
    (capture / "run_metadata.json").write_text(json.dumps(_metadata()), encoding="utf-8")
    spec = runner.CaptureSpec("fixture", capture, "x", "x", "x")
    with pytest.raises(runner.CaptureInputError) as caught:
        runner.validate_capture_input(spec, window_frames=600, raw_hasher=lambda _: "a" * 64)
    assert caught.value.reason == "raw_size_not_frame_divisible"


def test_capture_validation_rejects_empty_raw_stream(tmp_path: Path) -> None:
    capture = tmp_path / "capture"
    _write_sparse_raw(capture / "adc_stream.bin", 0)
    (capture / "run_metadata.json").write_text(
        json.dumps(_metadata()), encoding="utf-8"
    )
    spec = runner.CaptureSpec("fixture", capture, "x", "x", "x")
    with pytest.raises(runner.CaptureInputError) as caught:
        runner.validate_capture_input(
            spec, window_frames=600, raw_hasher=lambda _: "a" * 64
        )
    assert caught.value.reason == "raw_empty"


def test_warmup_failure_does_not_rescue_with_another_bin(tmp_path: Path) -> None:
    config_path, _ = _write_fixture_config(tmp_path, num_frames=600)
    selector_calls = []

    def fail_selector(*args, **kwargs):
        selector_calls.append(1)
        raise ValueError("no valid selector candidate")

    out = runner.run_radar_only(
        config_path=config_path,
        output_root=tmp_path / "outputs",
        repo_root=tmp_path,
        official=False,
        decoder=_FixturePipeline().decode,
        selector=fail_selector,
        candidate_deriver=lambda _: [5, 6],
        raw_hasher=lambda _: "a" * 64,
        source_hashes_override=_source_hashes(),
        run_id="warmup_failure",
        created_utc="2030-01-02T03:04:05+00:00",
    )
    assert len(selector_calls) == 1
    rows = json.loads((out / "estimates.json").read_text(encoding="utf-8"))
    assert len(rows) == 2
    assert {row["failure_reason"] for row in rows} == {"warmup_selection_failed"}
    failure = json.loads((out / rows[0]["evidence_artifact"]).read_text(encoding="utf-8"))
    assert failure["alternate_bin_rescue_attempted"] is False


def test_runner_contains_no_retired_estimator_variants() -> None:
    source = (REPO_ROOT / "scripts" / "m9_kotte_run.py").read_text(encoding="utf-8").lower()
    for forbidden in ("nc32", "nc64", "pooled_snapshot", "mean_surface", "coherent_mean"):
        assert forbidden not in source


@pytest.mark.parametrize(
    "override",
    [
        {"selector": lambda *_args, **_kwargs: None},
        {"candidate_deriver": lambda _config: [5]},
        {"slow_time_adapter": lambda *_args, **_kwargs: None},
        {"window_estimator": lambda *_args, **_kwargs: None},
        {"decoder": lambda *_args, **_kwargs: None},
        {"raw_hasher": lambda _path: "a" * 64},
        {"source_hashes_override": _source_hashes()},
    ],
)
def test_canonical_api_rejects_fixture_boundary_overrides(
    tmp_path: Path, override: dict
) -> None:
    with pytest.raises(ValueError, match="canonical|fixture-only"):
        runner.run_radar_only(
            config_path=CONFIG_PATH,
            output_root=tmp_path,
            repo_root=REPO_ROOT,
            **override,
        )


@pytest.mark.parametrize(
    "override",
    [
        {"run_id": "caller_selected_official_id"},
        {"created_utc": "2030-01-02T03:04:05+00:00"},
    ],
)
def test_canonical_api_rejects_fixture_provenance_overrides_before_input_access(
    monkeypatch, override: dict
) -> None:
    monkeypatch.setattr(
        runner,
        "load_runner_config",
        lambda _path: pytest.fail("official override reached config or capture access"),
    )
    with pytest.raises(ValueError, match="official|fixture"):
        runner.run_radar_only(**override)


def test_atomic_npz_rejects_pickle_data_and_removes_temporary_file(
    tmp_path: Path,
) -> None:
    target = tmp_path / "evidence.npz"
    with pytest.raises(TypeError, match="object dtype"):
        runner.atomic_write_npz(
            target, {"forbidden": np.asarray([{"x": 1}], dtype=object)}
        )
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "override",
    [
        ["--config", "alternate.yaml"],
        ["--output-root", "alternate-results"],
    ],
)
def test_official_cli_refuses_config_and_output_overrides(monkeypatch, override) -> None:
    """The reviewed official command has no mutable config/output path arguments."""
    monkeypatch.setattr(
        runner,
        "run_radar_only",
        lambda **_kwargs: pytest.fail("official override reached radar execution"),
    )
    with pytest.raises(SystemExit):
        runner.main(["--execute-radar-only", *override])


def test_run_id_cannot_escape_output_root(tmp_path: Path) -> None:
    config_path, _ = _write_fixture_config(tmp_path)
    pipeline = _FixturePipeline()
    with pytest.raises(ValueError, match="run_id|output"):
        runner.run_radar_only(
            config_path=config_path,
            output_root=tmp_path / "outputs",
            repo_root=tmp_path,
            official=False,
            decoder=pipeline.decode,
            selector=pipeline.selector,
            candidate_deriver=lambda _config: [5],
            slow_time_adapter=pipeline.adapter,
            window_estimator=pipeline.estimator,
            raw_hasher=lambda _path: "a" * 64,
            source_hashes_override=_source_hashes(),
            run_id="../escaped",
            created_utc="2030-01-02T03:04:05+00:00",
        )
    assert not (tmp_path / "escaped").exists()


@pytest.mark.parametrize(
    ("metadata_text", "reason"),
    [("[]", "metadata_not_mapping"), ("{not-json", "metadata_unreadable")],
)
def test_capture_validation_rejects_nonmapping_and_malformed_json(
    tmp_path: Path, metadata_text: str, reason: str
) -> None:
    capture = tmp_path / "capture"
    _write_sparse_raw(capture / "adc_stream.bin", 600)
    (capture / "run_metadata.json").write_text(metadata_text, encoding="utf-8")
    spec = runner.CaptureSpec("bad", capture, "x", "x", "x")
    with pytest.raises(runner.CaptureInputError) as caught:
        runner.validate_capture_input(spec, window_frames=600)
    assert caught.value.reason == reason
    assert caught.value.context["num_windows_floor"] == 1


@pytest.mark.parametrize(
    ("failure_point", "reason"),
    [
        ("raw_stat", "raw_stat_failed"),
        ("metadata_stat", "metadata_stat_failed"),
        ("metadata_read", "metadata_read_failed"),
        ("metadata_hash", "metadata_hash_failed"),
        ("metadata_race", "metadata_changed_during_validation"),
        ("raw_hash", "raw_hash_failed"),
    ],
)
def test_capture_validation_wraps_input_io_errors(
    tmp_path: Path, monkeypatch, failure_point: str, reason: str
) -> None:
    capture = tmp_path / "capture"
    _write_sparse_raw(capture / "adc_stream.bin", 600)
    (capture / "run_metadata.json").write_text(json.dumps(_metadata()), encoding="utf-8")
    spec = runner.CaptureSpec("bad", capture, "x", "x", "x")
    original_stat = Path.stat
    original_read_bytes = Path.read_bytes

    if failure_point in {"raw_stat", "metadata_stat"}:
        target_name = "adc_stream.bin" if failure_point == "raw_stat" else "run_metadata.json"

        def failing_stat(path: Path, *args, **kwargs):
            if path.name == target_name:
                raise PermissionError(f"denied {target_name}")
            return original_stat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", failing_stat)
    if failure_point == "metadata_read":

        def failing_read(path: Path, *args, **kwargs):
            if path.name == "run_metadata.json":
                raise PermissionError("denied metadata read")
            return original_read_bytes(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_bytes", failing_read)

    def metadata_hasher(path: Path) -> str:
        if failure_point == "metadata_hash":
            raise PermissionError("denied metadata hash")
        if failure_point == "metadata_race":
            return "c" * 64
        return runner.sha256_file(path)

    def raw_hasher(path: Path) -> str:
        if failure_point == "raw_hash":
            raise PermissionError("denied raw hash")
        return "a" * 64

    with pytest.raises(runner.CaptureInputError) as caught:
        runner.validate_capture_input(
            spec,
            window_frames=600,
            raw_hasher=raw_hasher,
            metadata_hasher=metadata_hasher,
        )
    assert caught.value.reason == reason


@pytest.mark.parametrize(
    ("metadata_text", "reason"),
    [("[]", "metadata_not_mapping"), ("{not-json", "metadata_unreadable")],
)
def test_bad_metadata_emits_stable_rows_and_later_capture_continues(
    tmp_path: Path, metadata_text: str, reason: str
) -> None:
    config_path, first_capture = _write_fixture_config(tmp_path, num_frames=600)
    (first_capture / "run_metadata.json").write_text(metadata_text, encoding="utf-8")
    second_capture = tmp_path / "capture_second"
    _write_sparse_raw(second_capture / "adc_stream.bin", 600)
    (second_capture / "run_metadata.json").write_text(
        json.dumps(_metadata(recorded_bin=9)), encoding="utf-8"
    )
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    document["radar_evaluation"]["captures"].append(
        {
            "capture_id": "fixture_second",
            "directory": "capture_second",
            "subject": "synthetic",
            "protocol_role": "fixture",
            "data_role": "test_only",
        }
    )
    config_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"fixture paper")
    pipeline = _FixturePipeline()

    out = runner.run_radar_only(
        config_path=config_path,
        output_root=tmp_path / "outputs",
        repo_root=tmp_path,
        paper_pdf_path=paper,
        official=False,
        decoder=pipeline.decode,
        selector=pipeline.selector,
        candidate_deriver=lambda _: [5],
        slow_time_adapter=pipeline.adapter,
        window_estimator=pipeline.estimator,
        raw_hasher=lambda _path: "a" * 64,
        source_hashes_override=_source_hashes(),
        run_id=f"bad_metadata_{reason}",
        created_utc="2030-01-02T03:04:05+00:00",
    )
    rows = json.loads((out / "estimates.json").read_text(encoding="utf-8"))
    first_rows = [row for row in rows if row["capture_id"] == "fixture"]
    second_rows = [row for row in rows if row["capture_id"] == "fixture_second"]
    assert len(first_rows) == 2
    assert {row["failure_reason"] for row in first_rows} == {reason}
    assert all(row["lock_binding_status"] == "unavailable_phase_before_lock" for row in first_rows)
    assert len(second_rows) == 2
    assert all(row["locked_bin"] == 5 for row in second_rows)
    assert pipeline.selector_calls == 1


def test_capture_hash_failure_emits_rows_and_later_capture_continues(tmp_path: Path) -> None:
    config_path, first_capture = _write_fixture_config(tmp_path, num_frames=600)
    second_capture = tmp_path / "capture_second"
    _write_sparse_raw(second_capture / "adc_stream.bin", 600)
    (second_capture / "run_metadata.json").write_text(
        json.dumps(_metadata(recorded_bin=9)), encoding="utf-8"
    )
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    document["radar_evaluation"]["captures"].append(
        {
            "capture_id": "fixture_second",
            "directory": "capture_second",
            "subject": "synthetic",
            "protocol_role": "fixture",
            "data_role": "test_only",
        }
    )
    config_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"fixture paper")
    hash_calls: list[str] = []

    def selective_raw_hash(path: Path) -> str:
        hash_calls.append(path.parent.name)
        if path.parent == first_capture:
            raise PermissionError("first raw hash denied")
        return "b" * 64

    out = runner.run_radar_only(
        config_path=config_path,
        output_root=tmp_path / "outputs",
        repo_root=tmp_path,
        paper_pdf_path=paper,
        official=False,
        decoder=_FixturePipeline().decode,
        selector=_FixturePipeline().selector,
        candidate_deriver=lambda _: [5],
        slow_time_adapter=_FixturePipeline().adapter,
        window_estimator=_FixturePipeline().estimator,
        raw_hasher=selective_raw_hash,
        source_hashes_override=_source_hashes(),
        run_id="cohort_continues",
        created_utc="2030-01-02T03:04:05+00:00",
    )
    rows = json.loads((out / "estimates.json").read_text(encoding="utf-8"))
    first_rows = [row for row in rows if row["capture_id"] == "fixture"]
    second_rows = [row for row in rows if row["capture_id"] == "fixture_second"]
    assert len(first_rows) == 2
    assert {row["failure_reason"] for row in first_rows} == {"raw_hash_failed"}
    assert all(row["lock_binding_status"] == "unavailable_phase_before_lock" for row in first_rows)
    assert len(second_rows) == 2
    assert all(row["locked_bin"] == 5 for row in second_rows)
    assert hash_calls.count("capture") == 1  # recovery never retries the denied hash
    expected_pdf_sha256 = runner.sha256_file(paper)
    assert {row["paper_pdf_sha256"] for row in first_rows} == {expected_pdf_sha256}
    failure_payload = json.loads(
        (out / first_rows[0]["evidence_artifact"]).read_text(encoding="utf-8")
    )
    assert failure_payload["paper_pdf_sha256"] == expected_pdf_sha256
    run_meta = json.loads((out / "run_meta.json").read_text(encoding="utf-8"))
    assert run_meta["paper_pdf_sha256"] == expected_pdf_sha256


def test_pdf_hash_is_bound_to_rows_runmeta_and_npz(tmp_path: Path) -> None:
    out = _run_fixture(tmp_path, run_id="fixture_pdf", pipeline=_FixturePipeline())
    expected = runner.sha256_file(tmp_path / "fixture_paper.pdf")
    rows = json.loads((out / "estimates.json").read_text(encoding="utf-8"))
    run_meta = json.loads((out / "run_meta.json").read_text(encoding="utf-8"))
    assert run_meta["paper_pdf_sha256"] == expected
    assert {row["paper_pdf_sha256"] for row in rows} == {expected}
    with np.load(out / rows[0]["shared_z_artifact"], allow_pickle=False) as shared:
        assert str(shared["paper_pdf_sha256"]) == expected
    with np.load(out / rows[0]["evidence_artifact"], allow_pickle=False) as arm:
        assert str(arm["paper_pdf_sha256"]) == expected


def test_post_lock_window_failure_keeps_lock_binding(tmp_path: Path) -> None:
    pipeline = _FixturePipeline()
    original_decode = pipeline.decode

    def fail_second_window(*args, **kwargs):
        if kwargs["start_frame"] == 600:
            raise PermissionError("second window disappeared")
        return original_decode(*args, **kwargs)

    pipeline.decode = fail_second_window  # type: ignore[method-assign]
    out = _run_fixture(tmp_path, run_id="post_lock_failure", pipeline=pipeline)
    rows = json.loads((out / "estimates.json").read_text(encoding="utf-8"))
    failed = [row for row in rows if row["window_index"] == 1]
    assert len(failed) == 2
    assert all(row["locked_bin"] == 5 for row in failed)
    assert all(row["lock_binding_status"] == "available" for row in failed)
    assert all(row["lock_evidence"] and row["lock_evidence_sha256"] for row in failed)
    failure = json.loads((out / failed[0]["evidence_artifact"]).read_text(encoding="utf-8"))
    assert failure["locked_bin"] == 5
    assert failure["lock_binding_status"] == "available"


def test_official_dirty_guard_refuses_before_config_or_data_access(monkeypatch) -> None:
    monkeypatch.setattr(runner, "_official_dirty_paths", lambda _root: [" M src/m9/kotte_core.py"])
    monkeypatch.setattr(
        runner,
        "load_runner_config",
        lambda _path: pytest.fail("dirty official run reached config access"),
    )
    with pytest.raises(ValueError, match="committed reviewed M9 paths"):
        runner.run_radar_only()
    assert "HANDOFF.md" not in runner.OFFICIAL_REVIEWED_PATHS
    assert "HISTORY.md" not in runner.OFFICIAL_REVIEWED_PATHS
    assert "plans/m9_kotte_plan.md" in runner.OFFICIAL_REVIEWED_PATHS
    assert "scripts/m9_kotte_run.py" in runner.OFFICIAL_REVIEWED_PATHS
    assert any(path.endswith(".pdf") for path in runner.OFFICIAL_REVIEWED_PATHS)


def test_official_dirty_check_scopes_git_to_reviewed_paths(monkeypatch, tmp_path: Path) -> None:
    observed: dict[str, object] = {}

    class GitResult:
        returncode = 0
        stdout = ""

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["cwd"] = kwargs["cwd"]
        return GitResult()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner._official_dirty_paths(tmp_path) == []
    command = observed["command"]
    assert command[:5] == ["git", "status", "--porcelain=v1", "--", *runner.OFFICIAL_REVIEWED_PATHS[:1]]
    assert command[4:] == list(runner.OFFICIAL_REVIEWED_PATHS)
    assert "HANDOFF.md" not in command
    assert "HISTORY.md" not in command
    assert observed["cwd"] == tmp_path


def test_official_pdf_identity_refuses_mismatch_before_config_or_data_access(monkeypatch) -> None:
    monkeypatch.setattr(runner, "_official_dirty_paths", lambda _root: [])
    monkeypatch.setattr(runner, "sha256_file", lambda _path: "0" * 64)
    monkeypatch.setattr(
        runner,
        "load_runner_config",
        lambda _path: pytest.fail("PDF mismatch reached config or capture access"),
    )
    with pytest.raises(ValueError, match="reviewed original"):
        runner.run_radar_only(raw_hasher=runner.sha256_file)
