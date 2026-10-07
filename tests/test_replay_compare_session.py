"""Replay-session authorization, provenance, and legacy-validity contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from src.m2.common import ContractError, sha256_bytes
from src.window_pipeline import run_config_hash
import src.replay_compare.session as session_module
from src.replay_compare.session import (
    LEGACY_VALIDITY_ID,
    SOURCE_KIND,
    SUPPORTED_CAPTURE_ID,
    duration_frame_limit,
    resolve_replay_session,
)
from src.replay_compare.source import (
    ProductionFrameDecoder,
    ReplaySourceError,
    SequentialAdcSource,
)


def _base_config() -> dict:
    return {
        "session": {"frame_rate_hz": 20.0},
        "profile": {
            "num_adc_samples": 91,
            "num_rx": 1,
            "num_chirps_per_frame": 4,
            "iq_swap": True,
            "range_resolution_m": 0.1,
        },
        "phase": {"method": "delta_before_mean"},
        "seed": 42,
    }


def _base_metadata(config: dict) -> dict:
    return {
        "mode": "live",
        "raw_stream_format": "adc_bytes_no_packet_headers",
        "completion_status": "completed",
        "start_wall_utc": "2026-07-28T19:49:02.711071+00:00",
        "iq_swap": True,
        "locked_bin": 26,
        "config": config,
        "live_packet_stats": {
            "n_received": 1,
            "n_dropped": 0,
            "zero_filled_bytes": 0,
            "mirror_truncated_bytes": 0,
        },
    }


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _make_session_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    metadata_mutator=None,
    config_mutator=None,
    root_name: str = "development_data",
) -> dict[str, object]:
    data_root = tmp_path / root_name
    capture = data_root / "results" / "live_demo" / SUPPORTED_CAPTURE_ID
    capture.mkdir(parents=True)
    config = _base_config()
    if config_mutator is not None:
        config_mutator(config)
    metadata = _base_metadata(config)
    if metadata_mutator is not None:
        metadata_mutator(metadata)
    warmup = {"selected_bin": 26, "selection_reason": "synthetic_fixture"}
    # One complete 1456-byte frame: exact production packet payload conservation.
    adc_bytes = bytes((index % 251 for index in range(1456)))
    metadata_bytes = _json_bytes(metadata)
    warmup_bytes = _json_bytes(warmup)
    adc_path = capture / "adc_stream.bin"
    metadata_path = capture / "run_metadata.json"
    warmup_path = capture / "warmup_bin_selection.json"
    adc_path.write_bytes(adc_bytes)
    metadata_path.write_bytes(metadata_bytes)
    warmup_path.write_bytes(warmup_bytes)

    source_registry = yaml.safe_load(
        Path(session_module.DEFAULT_REGISTRY).read_text(encoding="utf-8")
    )
    source_registry["geometry"].update(
        {
            "adc_samples": 91,
            "rx": 1,
            "chirps_per_frame": 4,
            "bytes_per_frame": 1456,
            "iq_swap": True,
            "frame_rate_hz": 20.0,
            "range_resolution_m_approx": 0.1,
        }
    )
    m3 = source_registry["radar"]["m3"]
    m3.update(
        {
            "frames": 1,
            "windows": 0,
            "tail_frames": 1,
            "recorded_lock": 26,
            "capture_config_sha256": run_config_hash(config),
            "adc_stream_sha256": sha256_bytes(adc_bytes),
            "metadata_sha256": sha256_bytes(metadata_bytes),
            "warmup_sha256": sha256_bytes(warmup_bytes),
        }
    )
    all_captures = source_registry["radar"]
    total_windows = sum(int(entry["windows"]) for entry in all_captures.values())
    source_registry["window_grid"]["total_windows"] = total_windows
    source_registry["window_grid"]["evaluation_k_ge_1_windows"] = (
        total_windows - len(all_captures)
    )
    registry = tmp_path / "capture_registry.yaml"
    registry.write_text(
        yaml.safe_dump(source_registry, sort_keys=False, line_break="\n"),
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(session_module, "DEFAULT_REGISTRY", registry)
    return {
        "root": data_root,
        "capture": capture,
        "adc_path": adc_path,
        "metadata_path": metadata_path,
        "warmup_path": warmup_path,
        "adc_bytes": adc_bytes,
        "metadata": metadata,
        "warmup": warmup,
        "registry": registry,
    }


def test_resolve_baseline_binds_registry_and_only_normalizes_implicit_defaults(
    tmp_path, monkeypatch
):
    fixture = _make_session_fixture(tmp_path, monkeypatch)

    spec = resolve_replay_session(
        SUPPORTED_CAPTURE_ID, development_data_root=fixture["root"]
    )

    assert spec.source_kind == SOURCE_KIND
    assert spec.frame_count == 1
    assert spec.frame_bytes == 1456
    assert spec.frame_rate_hz == pytest.approx(20.0)
    assert spec.duration_s == pytest.approx(0.05)
    assert spec.registered_adc_sha256 == sha256_bytes(fixture["adc_bytes"])
    assert spec.metadata_sha256 == sha256_bytes(
        Path(fixture["metadata_path"]).read_bytes()
    )
    assert spec.warmup_sha256 == sha256_bytes(
        Path(fixture["warmup_path"]).read_bytes()
    )
    assert "range_bias_m" not in spec.original_config["profile"]
    assert "clutter_removal" not in spec.original_config["phase"]
    assert spec.effective_config["profile"]["range_bias_m"] == 0.0
    assert spec.effective_config["phase"]["clutter_removal"] == "none"
    assert spec.anchor_utc_us == 1_785_268_142_711_071
    assert spec.time_origin_approximate is True


def test_session_nested_provenance_accessors_return_defensive_copies(
    tmp_path, monkeypatch
):
    fixture = _make_session_fixture(tmp_path, monkeypatch)
    spec = resolve_replay_session(
        SUPPORTED_CAPTURE_ID, development_data_root=fixture["root"]
    )
    original_hashes = (
        spec.original_config_sha256,
        spec.effective_config_sha256,
        spec.metadata_sha256,
    )

    original = spec.original_config
    effective = spec.effective_config
    metadata = spec.metadata
    original["profile"]["num_rx"] = 999
    effective["phase"]["clutter_removal"] = "forged"
    metadata["locked_bin"] = 999

    assert spec.original_config["profile"]["num_rx"] == 1
    assert spec.effective_config["phase"]["clutter_removal"] == "none"
    assert spec.metadata["locked_bin"] == 26
    assert (
        spec.original_config_sha256,
        spec.effective_config_sha256,
        spec.metadata_sha256,
    ) == original_hashes


@pytest.mark.parametrize(
    ("config_mutator", "match"),
    [
        (lambda c: c["profile"].update(range_bias_m=0.25), "zero range bias"),
        (
            lambda c: c["phase"].update(clutter_removal="mean"),
            "clutter_removal=none",
        ),
    ],
)
def test_session_rejects_explicitly_incompatible_historical_defaults(
    tmp_path, monkeypatch, config_mutator, match
):
    fixture = _make_session_fixture(
        tmp_path, monkeypatch, config_mutator=config_mutator
    )

    with pytest.raises(ContractError, match=match):
        resolve_replay_session(
            SUPPORTED_CAPTURE_ID, development_data_root=fixture["root"]
        )


@pytest.mark.parametrize("which", ["metadata_path", "warmup_path"])
def test_session_rejects_pre_run_metadata_or_warmup_substitution(
    tmp_path, monkeypatch, which
):
    fixture = _make_session_fixture(tmp_path, monkeypatch)
    Path(fixture[which]).write_bytes(Path(fixture[which]).read_bytes() + b" ")

    with pytest.raises(ContractError, match="SHA-256.*committed radar registry"):
        resolve_replay_session(
            SUPPORTED_CAPTURE_ID, development_data_root=fixture["root"]
        )


def test_session_registered_adc_digest_rejects_pre_run_substitution_at_source_open(
    tmp_path, monkeypatch
):
    fixture = _make_session_fixture(tmp_path, monkeypatch)
    spec = resolve_replay_session(
        SUPPORTED_CAPTURE_ID, development_data_root=fixture["root"]
    )
    adc_path = Path(fixture["adc_path"])
    substituted = bytearray(adc_path.read_bytes())
    substituted[0] ^= 0x01
    adc_path.write_bytes(substituted)
    decoder = ProductionFrameDecoder(spec.effective_config["profile"])
    source = SequentialAdcSource(
        spec.adc_path,
        decoder,
        expected_sha256=spec.registered_adc_sha256,
        expected_frame_count=spec.frame_count,
    )

    with pytest.raises(ReplaySourceError, match="committed radar registry"):
        source.__enter__()


def test_session_reads_each_bound_json_once_so_hash_and_parse_share_bytes(
    tmp_path, monkeypatch
):
    fixture = _make_session_fixture(tmp_path, monkeypatch)
    targets = {
        Path(fixture["metadata_path"]),
        Path(fixture["warmup_path"]),
    }
    calls = {path: 0 for path in targets}
    original_read_bytes = Path.read_bytes

    def changing_second_read(self):
        if self in targets:
            calls[self] += 1
            if calls[self] > 1:
                return b'{"forged":true}'
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", changing_second_read)
    spec = resolve_replay_session(
        SUPPORTED_CAPTURE_ID, development_data_root=fixture["root"]
    )

    assert calls == {path: 1 for path in targets}
    assert spec.metadata["locked_bin"] == 26
    assert spec.original_config["profile"]["num_rx"] == 1


@pytest.mark.parametrize("root_name", ["P001_natural", "m2_capture_work"])
def test_session_rejects_protected_development_root_before_input_bytes(
    tmp_path, monkeypatch, root_name
):
    fixture = _make_session_fixture(
        tmp_path, monkeypatch, root_name=root_name
    )
    input_paths = {
        Path(fixture["metadata_path"]),
        Path(fixture["warmup_path"]),
        Path(fixture["adc_path"]),
    }
    original_read_bytes = Path.read_bytes

    def forbidden_input_read(self):
        if self in input_paths:
            raise AssertionError("protected replay input opened before rejection")
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", forbidden_input_read)
    with pytest.raises(ContractError, match="forbidden"):
        resolve_replay_session(
            SUPPORTED_CAPTURE_ID, development_data_root=fixture["root"]
        )


def test_motion_mode_requires_explicit_legacy_opt_in(tmp_path, monkeypatch):
    fixture = _make_session_fixture(tmp_path, monkeypatch)

    with pytest.raises(ContractError, match="allow-legacy-validity-assumption"):
        resolve_replay_session(
            SUPPORTED_CAPTURE_ID,
            development_data_root=fixture["root"],
            mode="motion",
        )


def test_valid_legacy_zero_loss_is_visible_and_unknown_diagnostics_remain_unknown(
    tmp_path, monkeypatch
):
    def unknown_diagnostics(metadata):
        metadata["live_packet_stats"].update(
            {
                "packets_short_discarded": None,
                "packets_duplicate_or_late_discarded": None,
                "queue_overflow_count": None,
            }
        )

    fixture = _make_session_fixture(
        tmp_path, monkeypatch, metadata_mutator=unknown_diagnostics
    )
    spec = resolve_replay_session(
        SUPPORTED_CAPTURE_ID,
        development_data_root=fixture["root"],
        mode="motion",
        allow_legacy_validity_assumption=True,
    )

    assert spec.validity_assumed is True
    assert spec.validity_provenance == LEGACY_VALIDITY_ID
    assert spec.metadata["live_packet_stats"]["packets_short_discarded"] is None


@pytest.mark.parametrize(
    ("mutator", "match"),
    [
        (lambda m: m.update(mode="replay"), "live-capture"),
        (lambda m: m.update(raw_stream_format="numpy_cube"), "mirror format"),
        (lambda m: m.update(completion_status="failed"), "completed capture"),
        (
            lambda m: m["live_packet_stats"].update(n_received=0),
            "positive packet count",
        ),
        (
            lambda m: m["live_packet_stats"].pop("n_dropped"),
            "explicit zero dropped",
        ),
        (
            lambda m: m["live_packet_stats"].update(n_dropped=1),
            "explicit zero dropped",
        ),
        (
            lambda m: m["live_packet_stats"].pop("zero_filled_bytes"),
            "zero-filled bytes",
        ),
        (
            lambda m: m["live_packet_stats"].update(zero_filled_bytes=1),
            "zero-filled bytes",
        ),
        (
            lambda m: m["live_packet_stats"].update(mirror_truncated_bytes=-1),
            "terminal truncation",
        ),
        (
            lambda m: m["live_packet_stats"].update(mirror_truncated_bytes=1456),
            "terminal truncation",
        ),
        (
            lambda m: m["live_packet_stats"].update(n_received=2),
            "byte conservation",
        ),
        (
            lambda m: m["live_packet_stats"].update(queue_overflow_count=1),
            "queue_overflow_count",
        ),
        (
            lambda m: m["live_packet_stats"].update(packets_short_discarded="0"),
            "packets_short_discarded",
        ),
    ],
)
def test_legacy_validity_fails_closed_for_loss_or_malformed_provenance(
    tmp_path, monkeypatch, mutator, match
):
    fixture = _make_session_fixture(
        tmp_path, monkeypatch, metadata_mutator=mutator
    )

    with pytest.raises(ContractError, match=match):
        resolve_replay_session(
            SUPPORTED_CAPTURE_ID,
            development_data_root=fixture["root"],
            mode="motion",
            allow_legacy_validity_assumption=True,
        )


@pytest.mark.parametrize(
    ("duration_s", "expected"),
    [(None, 12005), (0.05, 1), (1.999, 39), (600.25, 12005), (9999, 12005)],
)
def test_duration_limit_is_integer_recording_frame_floor(duration_s, expected):
    assert duration_frame_limit(
        duration_s, total_frames=12005, frame_rate_hz=20.0
    ) == expected


@pytest.mark.parametrize("duration_s", [True, 0, -1, 0.049, float("inf"), float("nan")])
def test_duration_limit_rejects_nonpositive_nonfinite_or_subframe(duration_s):
    with pytest.raises(ValueError):
        duration_frame_limit(
            duration_s, total_frames=12005, frame_rate_hz=20.0
        )
