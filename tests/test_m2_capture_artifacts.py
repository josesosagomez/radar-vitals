"""Packet assembly, validity-ledger, prospective CLI, and two-phase artifact tests."""
from __future__ import annotations

import json
import struct
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "m2"))

from builders import acquisition_metadata, synthetic_registry, write_registry  # noqa: E402
import scripts.live_demo as live_demo  # noqa: E402
from scripts.live_demo import (  # noqa: E402
    PAYLOAD_BYTES_PER_PKT,
    LiveFrameSource,
    _validate_prospective_cli,
)
from src.m2.capture_artifacts import (  # noqa: E402
    finalize_capture,
    prepare_capture_inputs,
    validate_sealed_radar_receipt,
    write_sealed_radar_receipt,
)
import src.m2.cohort_registry as cohort_registry  # noqa: E402
from src.m2.cohort_registry import (  # noqa: E402
    DEFAULT_REGISTRY_PATH,
    RegistryError,
    load_registry,
    write_registry_revision,
)
from src.m2.common import ContractError, canonical_json_bytes, sha256_file  # noqa: E402
from src.m2.manifest import load_manifest  # noqa: E402
from src.m2.manifest_v3 import FRAME0_EVENT_SOURCE, Mode  # noqa: E402
from src.m2.preflight import build_synthetic_dry_run, validate_preflight  # noqa: E402
from src.m2.validity import (  # noqa: E402
    invalid_window_indices,
    load_frame_validity,
    materialize_radar_nan_windows,
    validity_by_window,
)


PROFILE_ONE_PACKET_FRAME = {
    "num_chirps_per_frame": 1,
    "num_rx": 1,
    "num_adc_samples": PAYLOAD_BYTES_PER_PKT // 4,
    "iq_swap": True,
}
RECOVERY_SEATED_START_UTC = 1_899_999_980.0
RECOVERY_START_EVENT_SOURCE = "operator_observed_seated_start_synchronized_pc_utc"
RECOVERY_CLI_INVOCATION = [
    "live_demo.py", "--recovery-seated-start-utc", str(RECOVERY_SEATED_START_UTC),
]


def _packet(sequence: int, byte: int = 1) -> bytes:
    return struct.pack("<I", sequence) + b"\0" * 6 + bytes([byte]) * PAYLOAD_BYTES_PER_PKT


class _FiniteSocket:
    def __init__(self, packets):
        self.packets = list(packets)
        self.source = None

    def settimeout(self, _timeout):
        return None

    def recv(self, _size):
        if self.packets:
            return self.packets.pop(0)
        self.source._stop.set()
        raise TimeoutError


def _assemble(tmp_path: Path, packets, *, profile=PROFILE_ONE_PACKET_FRAME):
    sock = _FiniteSocket(packets)
    source = LiveFrameSource(
        profile, sock_dat=sock, raw_mirror_path=tmp_path / "mirror.bin"
    )
    sock.source = source
    source._sock = sock
    source._loop()
    return source


def test_leading_loss_is_zero_filled_invalid_and_does_not_backdate_frame0(tmp_path):
    source = _assemble(tmp_path, [_packet(2)])
    assert source.n_received == 1
    assert source.n_dropped == 1
    assert source.leading_zero_filled_bytes == PAYLOAD_BYTES_PER_PKT
    assert source.frame_validity == [False, True]
    assert source.frame0_epoch_utc(20.0) == source.frame0_start_assignment_utc
    assert source.frame0_epoch_utc(20.0) - (
        source.frame0_start_assignment_utc - 0.05
    ) == pytest.approx(0.05, rel=0.0, abs=1e-6)


def test_frame0_persists_start_assignment_not_first_frame_completion(tmp_path, monkeypatch):
    class _ObservedUtc:
        def timestamp(self):
            return 100.0

    class _ObservedDatetime:
        @staticmethod
        def now(_timezone):
            return _ObservedUtc()

    monkeypatch.setattr("scripts.live_demo.datetime", _ObservedDatetime)
    sock = _FiniteSocket([_packet(1)])
    source = LiveFrameSource(
        PROFILE_ONE_PACKET_FRAME, sock_dat=sock, raw_mirror_path=tmp_path / "mirror.bin"
    )
    sock.source = source
    source._sock = sock
    observed = {}
    original_decode = source._decode_frame

    def decode_at_completion(frame_bytes):
        observed["completion_utc"] = 100.05
        return original_decode(frame_bytes)

    monkeypatch.setattr(source, "_decode_frame", decode_at_completion)
    source._loop()
    assert source.frame0_epoch_utc(20.0) == 100.0
    assert observed["completion_utc"] - source.frame0_epoch_utc(20.0) == pytest.approx(0.05)


def test_middle_gap_duplicate_late_and_short_packets_have_separate_counters(tmp_path):
    source = _assemble(
        tmp_path,
        [_packet(1), b"short", _packet(1), _packet(3), _packet(2)],
    )
    assert source.n_received == 2
    assert source.n_dropped == 1
    assert source.packets_short_discarded == 1
    assert source.packets_duplicate_or_late_discarded == 2
    assert source.frame_validity == [True, False, True]


def test_missing_packets_at_exact_599_600_window_boundary_mark_both_windows(tmp_path):
    packets = [_packet(sequence) for sequence in range(1, 600)] + [_packet(602)]
    source = _assemble(tmp_path, packets)
    validity = np.asarray(source.frame_validity, dtype=np.bool_)
    assert validity.size == 602
    assert np.flatnonzero(~validity).tolist() == [599, 600]
    assert invalid_window_indices(validity) == (0,)

    padded = np.concatenate([validity, np.ones(598, dtype=np.bool_)])
    assert invalid_window_indices(padded) == (0, 1)


def test_trailing_partial_frame_is_truncated_and_never_enters_validity_map(tmp_path):
    profile = dict(PROFILE_ONE_PACKET_FRAME, num_adc_samples=PAYLOAD_BYTES_PER_PKT // 2)
    source = _assemble(tmp_path, [_packet(1)], profile=profile)
    assert source.frame_validity == []
    assert source.mirror_truncated_bytes == PAYLOAD_BYTES_PER_PKT
    assert (tmp_path / "mirror.bin").stat().st_size == 0


def test_boolean_map_hash_count_and_invalid_window_nan_ledger(tmp_path):
    validity = np.ones(1201, dtype=np.bool_)
    validity[[599, 600]] = False
    path = tmp_path / "validity.npy"
    np.save(path, validity)
    loaded = load_frame_validity(path, expected_frames=1201)
    assert loaded.dtype == np.bool_ and loaded.ndim == 1
    assert int(np.count_nonzero(~loaded)) == 2
    assert sha256_file(path) == sha256_file(path)
    assert [(w.window_index, w.radar_valid) for w in validity_by_window(loaded)] == [
        (0, False), (1, False)
    ]
    rows = materialize_radar_nan_windows(
        [{"window_index": 0, "hr_bpm": 70.0}, {"window_index": 1, "hr_bpm": 71.0}],
        loaded,
    )
    assert len(rows) == 2
    assert [row["radar_validity_reason"] for row in rows] == [
        "frame_validity_zero_fill", "frame_validity_zero_fill"
    ]
    assert all(row["hr_bpm"] is None and row["br_bpm"] is None for row in rows)


@pytest.mark.parametrize("received,dropped,flag", [(1000, 50, False), (1000, 51, True), (0, 0, False)])
def test_packet_loss_integer_threshold_and_zero_denominator(received, dropped, flag):
    if received:
        assert (20 * dropped > received) is flag
        assert dropped / received == pytest.approx(dropped / 1000)
    else:
        ratio = None
        assert ratio is None


def _write_sidecar(tmp_path: Path, metadata: dict) -> Path:
    path = tmp_path / "sidecar.yaml"
    path.write_text(yaml.safe_dump(metadata, sort_keys=True), encoding="utf-8")
    return path


def _copy_committed_registry(destination: Path) -> Path:
    destination.write_bytes(DEFAULT_REGISTRY_PATH.read_bytes())
    source_digest = DEFAULT_REGISTRY_PATH.with_suffix(DEFAULT_REGISTRY_PATH.suffix + ".sha256")
    destination.with_suffix(destination.suffix + ".sha256").write_bytes(
        source_digest.read_bytes()
    )
    return destination


def _prospective_args(sidecar: Path, registry: Path, **changes):
    values = dict(
        prospective_sidecar=sidecar,
        cohort_registry=registry,
        live_session="T001_recovery",
        replay_session=None,
        replay_paths=None,
        replay_fast=False,
        locked_bin=None,
        no_configure=False,
        duration_s=600.0,
        recovery_seated_start_utc=1_700_000_000.0,
    )
    values.update(changes)
    return SimpleNamespace(**values)


def test_prospective_cli_accepts_only_exact_600s_auto_lock_live_clean_default(tmp_path):
    sidecar = _write_sidecar(tmp_path, acquisition_metadata())
    registry = write_registry(tmp_path / "registry.json", synthetic_registry())
    cfg = {"bin_selection": {"enabled": True}, "session": {"locked_bin": None}}
    metadata = _validate_prospective_cli(
        _prospective_args(sidecar, registry), cfg,
        ROOT / "scripts" / "live_demo_config.yaml",
        {"git_commit": "abc123", "git_dirty": False},
    )
    assert metadata["intended_duration_s"] == 600.0


def test_prospective_start_delay_skips_countdown_only_for_recovery():
    helper = getattr(live_demo, "_prospective_start_delay_s", None)
    assert callable(helper), "prospective CLI needs a pure arm-specific countdown helper"
    assert helper(acquisition_metadata("recovery")) == 0
    assert helper(acquisition_metadata("natural")) == 30
    assert helper(acquisition_metadata("paced")) == 30


def test_live_cli_parses_runtime_recovery_seating_timestamp(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["live_demo.py", "--recovery-seated-start-utc", "1700000000.25"],
    )
    assert live_demo._parse_args().recovery_seated_start_utc == 1_700_000_000.25


@pytest.mark.parametrize(("arm", "expected_sleeps"), [("recovery", 0), ("natural", 30), ("paced", 30)])
def test_main_applies_arm_specific_countdown_before_any_backend_or_hardware(
    monkeypatch, arm, expected_sleeps
):
    args = _prospective_args(Path("unused.yaml"), Path("unused_registry.json"))
    args.config = str(ROOT / "scripts" / "live_demo_config.yaml")
    args.headless = False
    sleeps = []

    monkeypatch.setattr(live_demo, "_parse_args", lambda: args)
    monkeypatch.setattr(live_demo, "_git_info", lambda: {"git_commit": "abc123", "git_dirty": False})
    monkeypatch.setattr(
        live_demo, "_validate_prospective_cli", lambda *_args: acquisition_metadata(arm)
    )
    monkeypatch.setattr(live_demo.time, "sleep", lambda seconds: sleeps.append(seconds))

    def stop_before_backend(_cfg):
        raise RuntimeError("stopped before backend and hardware")

    monkeypatch.setattr(live_demo, "_select_backend", stop_before_backend)
    with pytest.raises(RuntimeError, match="before backend and hardware"):
        live_demo.main()
    assert sleeps == [1] * expected_sleeps


@pytest.mark.parametrize("timestamp", [None, float("nan"), float("inf")])
def test_recovery_cli_preflight_rejects_missing_or_nonfinite_runtime_seating_utc(
    tmp_path, timestamp
):
    sidecar = _write_sidecar(tmp_path, acquisition_metadata("recovery"))
    registry = write_registry(tmp_path / "registry.json", synthetic_registry())
    cfg = {"bin_selection": {"enabled": True}, "session": {"locked_bin": None}}
    with pytest.raises(ValueError, match="recovery|seated|start|UTC|finite"):
        _validate_prospective_cli(
            _prospective_args(
                sidecar, registry, recovery_seated_start_utc=timestamp
            ),
            cfg,
            ROOT / "scripts" / "live_demo_config.yaml",
            {"git_commit": "abc123", "git_dirty": False},
        )


@pytest.mark.parametrize("arm", ["natural", "paced"])
def test_nonrecovery_cli_preflight_rejects_recovery_seating_timestamp(tmp_path, arm):
    metadata = acquisition_metadata(arm)
    sidecar = _write_sidecar(tmp_path, metadata)
    registry = write_registry(tmp_path / "registry.json", synthetic_registry())
    cfg = {"bin_selection": {"enabled": True}, "session": {"locked_bin": None}}
    with pytest.raises(ValueError, match="recovery|seated|only"):
        _validate_prospective_cli(
            _prospective_args(
                sidecar,
                registry,
                live_session=metadata["session_id"],
                recovery_seated_start_utc=1_899_999_980.0,
            ),
            cfg,
            ROOT / "scripts" / "live_demo_config.yaml",
            {"git_commit": "abc123", "git_dirty": False},
        )


@pytest.mark.parametrize(
    "stopping_event_category", ["participant_stop", "researcher_safety_stop"]
)
def test_recovery_cli_preflight_rejects_non_target_stop_before_hardware(
    tmp_path, stopping_event_category
):
    metadata = acquisition_metadata("recovery")
    metadata["stopping_event_category"] = stopping_event_category
    sidecar = _write_sidecar(tmp_path, metadata)
    registry = write_registry(tmp_path / "registry.json", synthetic_registry())
    cfg = {"bin_selection": {"enabled": True}, "session": {"locked_bin": None}}
    with pytest.raises(
        (ValueError, ContractError), match="target_reached|non-acquisition|participant|safety"
    ):
        _validate_prospective_cli(
            _prospective_args(sidecar, registry),
            cfg,
            ROOT / "scripts" / "live_demo_config.yaml",
            {"git_commit": "abc123", "git_dirty": False},
        )


def test_recovery_runbook_names_visible_pr_spo2_safety_stops_and_abort_branch():
    runbook = (ROOT / "notes" / "m2_capture_runbook.md").read_text(encoding="utf-8")
    recovery = runbook.split("### Recovery", 1)[1].split("## 4.", 1)[0].lower()
    assert "pr" in recovery and "spo2" in recovery and "visible" in recovery
    for safety_signal in (
        "chest pain", "light-headedness", "breathlessness", "palpitations",
        "nausea", "visible distress", "participant stop",
    ):
        assert safety_signal in recovery
    assert "abort" in recovery or "do not start" in recovery or "do not launch" in recovery


def test_history_contains_no_prospective_observed_pr_range_while_labels_are_sealed():
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    assert all(subject["label_state"] == "sealed" for subject in registry["subjects"])
    history = (ROOT / "HISTORY.md").read_text(encoding="utf-8").lower()
    for subject_id in (f"p{slot:03d}" for slot in range(1, 16)):
        for line in history.splitlines():
            if subject_id in line:
                assert not (
                    "observed pr" in line
                    or "measured pr" in line
                    or ("pr range" in line and "guarded" not in line)
                ), f"sealed {subject_id} must not expose prospective PR outcomes in HISTORY"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"duration_s": 599.999}, "exactly 600"),
        ({"duration_s": 600.001}, "exactly 600"),
        ({"locked_bin": 24}, "locked-bin"),
        ({"no_configure": True}, "no-configure"),
        ({"replay_session": "old", "live_session": None}, "replay"),
        ({"replay_fast": True}, "replay-fast"),
    ],
)
def test_prospective_cli_rejects_duration_manual_attach_and_replay(tmp_path, changes, message):
    sidecar = _write_sidecar(tmp_path, acquisition_metadata())
    registry = write_registry(tmp_path / "registry.json", synthetic_registry())
    cfg = {"bin_selection": {"enabled": True}, "session": {"locked_bin": None}}
    with pytest.raises(ValueError, match=message):
        _validate_prospective_cli(
            _prospective_args(sidecar, registry, **changes), cfg,
            ROOT / "scripts" / "live_demo_config.yaml",
            {"git_commit": "abc123", "git_dirty": False},
        )


def test_prospective_cli_rejects_config_override_disabled_warmup_and_dirty_commit(tmp_path):
    sidecar = _write_sidecar(tmp_path, acquisition_metadata())
    registry = write_registry(tmp_path / "registry.json", synthetic_registry())
    with pytest.raises(ValueError, match="config overrides|bin_selection|clean git"):
        _validate_prospective_cli(
            _prospective_args(sidecar, registry),
            {"bin_selection": {"enabled": False}, "session": {"locked_bin": 2}},
            tmp_path / "override.yaml",
            {"git_commit": "unknown", "git_dirty": True},
        )


def test_prospective_cli_rejects_paced_rate_that_disagrees_with_fixed_registry(tmp_path):
    metadata = acquisition_metadata(
        arm="paced", subject_id="P001", data_role="representation_validation",
        synthetic_fixture=False,
    )
    metadata.update(
        session_id="P001_paced", commanded_rate_bpm=15,
        metronome_rate_bpm=30,
        harmonic_collision_margin_bpm=abs(metadata["resting_pr_bpm"] - 60.0),
    )
    sidecar = _write_sidecar(tmp_path, metadata)
    registry = _copy_committed_registry(tmp_path / "registry_v001.json")
    cfg = {"bin_selection": {"enabled": True}, "session": {"locked_bin": None}}
    with pytest.raises((ValueError, ContractError), match="paced|rate|registry"):
        _validate_prospective_cli(
            _prospective_args(sidecar, registry, live_session="P001_paced"), cfg,
            ROOT / "scripts" / "live_demo_config.yaml",
            {"git_commit": "abc123", "git_dirty": False},
        )


def test_prospective_live_stream_hard_caps_frames_and_canonical_mirror(tmp_path):
    profile = {"num_chirps_per_frame": 1, "num_rx": 1, "num_adc_samples": 2, "iq_swap": True}
    packets = [_packet(i) for i in range(1, 67)]  # 12,012 complete 8-byte frames
    sock = _FiniteSocket(packets)
    mirror = tmp_path / "mirror.bin"
    source = LiveFrameSource(profile, sock_dat=sock, raw_mirror_path=mirror, max_frames=12_000)
    sock.source = source
    source._sock = sock
    source._loop()
    assert len(source.frame_validity) == 12_000
    assert source._frame_idx == 12_000
    assert mirror.stat().st_size == 12_000 * 8


def test_physical_profile_terminal_buffer_never_retains_one_complete_frame(tmp_path):
    profile = {
        "num_chirps_per_frame": 32, "num_rx": 4,
        "num_adc_samples": 256, "iq_swap": True,
    }
    source = _assemble(tmp_path, [_packet(i) for i in range(1, 92)], profile=profile)
    bytes_per_frame = 32 * 4 * 256 * 4
    assert source._frame_idx == 1
    assert source.mirror_truncated_bytes == 91 * PAYLOAD_BYTES_PER_PKT - bytes_per_frame
    assert 0 <= source.mirror_truncated_bytes < bytes_per_frame
    assert (tmp_path / "mirror.bin").stat().st_size == bytes_per_frame


def test_physical_profile_terminal_gap_over_90_packets_crossing_cap_is_frame_aligned(tmp_path):
    profile = {
        "num_chirps_per_frame": 32, "num_rx": 4,
        "num_adc_samples": 256, "iq_swap": True,
    }
    sock = _FiniteSocket([_packet(1), _packet(93)])  # 91 missing packet intervals
    mirror = tmp_path / "physical_mirror.bin"
    source = LiveFrameSource(
        profile, sock_dat=sock, raw_mirror_path=mirror, max_frames=1
    )
    sock.source = source
    source._sock = sock
    source._loop()
    bytes_per_frame = 32 * 4 * 256 * 4
    # The packet interval containing seq=93 starts beyond the one-frame logical
    # acquisition.  Only the 90 missing intervals needed to reach the packet-aligned
    # cap are represented; the rest of the observed sequence gap is out of scope.
    assert source.n_received == 1
    assert source.n_dropped == 90
    assert source._frame_idx == 1
    assert source.frame_validity == [False]
    assert mirror.stat().st_size == bytes_per_frame
    assert source.mirror_truncated_bytes == (
        (source.n_received + source.n_dropped) * PAYLOAD_BYTES_PER_PKT - bytes_per_frame
    )
    assert 0 <= source.mirror_truncated_bytes < bytes_per_frame

    # Exercise the same represented dropped count through the public sealing boundary.
    # The compact fixture uses 91-byte frames, so 660 accepted + 90 dropped intervals
    # conserve exactly to its 12,000 complete frames with no trailing bytes.
    registry = write_registry(tmp_path / "registry.json", synthetic_registry())
    run, sidecar = _make_run(tmp_path, acquisition_metadata(), registry)
    metadata_path = run / "run_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    stats = metadata["live_packet_stats"]
    stats["packets_received"] = 660
    stats["packets_dropped"] = source.n_dropped
    metadata_path.write_bytes(canonical_json_bytes(metadata))
    receipt_path = write_sealed_radar_receipt(
        run,
        acquisition_sidecar_path=sidecar,
        cohort_registry_path=registry,
        exact_cli_invocation=RECOVERY_CLI_INVOCATION,
        engineering_dry_run=True,
    )
    receipt = validate_sealed_radar_receipt(receipt_path)
    assert receipt["packets_dropped"] == 90
    assert (
        (receipt["packets_received"] + receipt["packets_dropped"])
        * PAYLOAD_BYTES_PER_PKT
        == receipt["n_frames"] * receipt["bytes_per_frame"]
        + receipt["trailing_partial_frame_bytes"]
    )


def test_receipt_rejects_packet_byte_conservation_tamper(tmp_path):
    registry = write_registry(tmp_path / "registry.json", synthetic_registry())
    run, sidecar = _make_run(tmp_path, acquisition_metadata(), registry)
    metadata_path = run / "run_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    stats = metadata["live_packet_stats"]
    # The exact conservation identity must be checked independently of ratio/flag fields.
    stats["packets_received"] += 1
    metadata_path.write_bytes(canonical_json_bytes(metadata))
    with pytest.raises(ContractError, match="byte|conservation|1456|packet"):
        write_sealed_radar_receipt(
            run, acquisition_sidecar_path=sidecar, cohort_registry_path=registry,
            exact_cli_invocation=RECOVERY_CLI_INVOCATION, engineering_dry_run=True,
        )


def _make_run(tmp_path: Path, metadata: dict, registry: Path) -> tuple[Path, Path]:
    run = tmp_path / "run"
    run.mkdir()
    sidecar = _write_sidecar(tmp_path, metadata)
    source_config = tmp_path / "config.yaml"
    source_config.write_text("fixture: true\n", encoding="utf-8")
    prepare_capture_inputs(
        run, source_config_path=source_config, effective_config={"fixture": True},
        acquisition_sidecar_path=sidecar,
    )
    (run / "adc_stream.bin").write_bytes(b"\0" * (750 * PAYLOAD_BYTES_PER_PKT))
    assert (run / "adc_stream.bin").stat().st_size == 1_092_000
    validity_path = run / "frame_validity.npy"
    np.save(validity_path, np.ones(12000, dtype=np.bool_))
    run_metadata = {
        "completion_status": "completed", "mode": "live",
        "prospective_study_mode": True,
        "exact_cli_invocation": RECOVERY_CLI_INVOCATION,
        "acquisition_metadata": metadata,
        "session_id": metadata["session_id"], "git_dirty": False, "git_commit": "abc123",
        "frame0_epoch_source": FRAME0_EVENT_SOURCE, "frame0_epoch_utc": 1_900_000_000.0,
        "start_wall_utc": "2030-01-02T03:03:00Z",
        "live_packet_stats": {
            "packets_received": 750, "packets_dropped": 0,
            "packets_short_discarded": 0, "packets_duplicate_or_late_discarded": 0,
            "n_frames": 12000, "n_invalid_frames": 0,
            "trailing_partial_frame_bytes": 0, "bytes_per_frame": 91,
            "frame_validity_map_path": validity_path.name,
            "frame_validity_map_sha256": sha256_file(validity_path),
        },
    }
    if metadata["arm"] == "recovery":
        run_metadata.update({
            "recovery_seated_start_utc": RECOVERY_SEATED_START_UTC,
            "recovery_seated_start_event_source": RECOVERY_START_EVENT_SOURCE,
            "sit_to_record_delay_s": 20.0,
        })
    (run / "run_metadata.json").write_bytes(canonical_json_bytes(run_metadata))
    return run, sidecar


def _physical_recovery_receipt(tmp_path: Path) -> tuple[Path, Path, Path]:
    registry = _copy_committed_registry(tmp_path / "registry_v001.json")
    run, sidecar = _make_run(
        tmp_path,
        acquisition_metadata(
            subject_id="P001", data_role="representation_validation",
            synthetic_fixture=False,
        ),
        registry,
    )
    receipt = write_sealed_radar_receipt(
        run, acquisition_sidecar_path=sidecar, cohort_registry_path=registry,
        exact_cli_invocation=RECOVERY_CLI_INVOCATION,
    )
    return registry, run, receipt


def test_recovery_runtime_seating_event_and_derived_delay_are_bound_in_sealed_receipt(tmp_path):
    _registry, run, receipt_path = _physical_recovery_receipt(tmp_path)
    run_metadata = json.loads((run / "run_metadata.json").read_text(encoding="utf-8"))
    receipt = validate_sealed_radar_receipt(receipt_path)
    for artifact in (run_metadata, receipt):
        assert artifact["recovery_seated_start_utc"] == RECOVERY_SEATED_START_UTC
        assert artifact["recovery_seated_start_event_source"] == RECOVERY_START_EVENT_SOURCE
        assert artifact["sit_to_record_delay_s"] == pytest.approx(
            artifact["frame0_epoch_utc" if artifact is run_metadata else "frame0_epoch"]
            - artifact["recovery_seated_start_utc"]
        )
    assert "sit_to_record_delay_s" not in receipt["acquisition_metadata"]
    assert receipt["acquisition_metadata"]["stopping_event_category"] == "target_reached"


@pytest.mark.parametrize(
    "stopping_event_category", ["participant_stop", "researcher_safety_stop"]
)
def test_recovery_non_target_stop_cannot_write_acquired_receipt(
    tmp_path, stopping_event_category
):
    registry = _copy_committed_registry(tmp_path / "registry_v001.json")
    metadata = acquisition_metadata(
        subject_id="P001", data_role="representation_validation", synthetic_fixture=False
    )
    run, sidecar = _make_run(tmp_path, metadata, registry)
    stopped = dict(metadata, stopping_event_category=stopping_event_category)
    stopped_yaml = yaml.safe_dump(stopped, sort_keys=True)
    sidecar.write_text(stopped_yaml, encoding="utf-8")
    (run / "acquisition_sidecar.yaml").write_text(stopped_yaml, encoding="utf-8")
    run_metadata_path = run / "run_metadata.json"
    run_metadata = json.loads(run_metadata_path.read_text(encoding="utf-8"))
    run_metadata["acquisition_metadata"] = stopped
    run_metadata_path.write_bytes(canonical_json_bytes(run_metadata))
    with pytest.raises(ContractError, match="target_reached|non-acquisition|participant|safety"):
        write_sealed_radar_receipt(
            run,
            acquisition_sidecar_path=sidecar,
            cohort_registry_path=registry,
            exact_cli_invocation=RECOVERY_CLI_INVOCATION,
        )


@pytest.mark.parametrize(
    "stopping_event_category", ["participant_stop", "researcher_safety_stop"]
)
def test_sealed_acquired_receipt_rejects_non_target_stop_tamper(
    tmp_path, stopping_event_category
):
    _registry, _run, receipt_path = _physical_recovery_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["acquisition_metadata"]["stopping_event_category"] = stopping_event_category
    receipt_path.write_bytes(canonical_json_bytes(receipt))
    with pytest.raises(ContractError, match="target_reached|non-acquisition|participant|safety"):
        validate_sealed_radar_receipt(receipt_path)


@pytest.mark.parametrize("tamper", ["missing", "future", "derived_delay"])
def test_recovery_receipt_sealing_rejects_missing_future_or_edited_runtime_timing(
    tmp_path, tamper
):
    registry = _copy_committed_registry(tmp_path / "registry_v001.json")
    metadata = acquisition_metadata(
        subject_id="P001", data_role="representation_validation", synthetic_fixture=False
    )
    run, sidecar = _make_run(tmp_path, metadata, registry)
    metadata_path = run / "run_metadata.json"
    document = json.loads(metadata_path.read_text(encoding="utf-8"))
    if tamper == "missing":
        document.pop("recovery_seated_start_utc")
    elif tamper == "future":
        document["recovery_seated_start_utc"] = document["frame0_epoch_utc"] + 0.001
    else:
        document["sit_to_record_delay_s"] += 1.0
    metadata_path.write_bytes(canonical_json_bytes(document))
    with pytest.raises(ContractError, match="recovery|seated|future|delay|timing"):
        write_sealed_radar_receipt(
            run,
            acquisition_sidecar_path=sidecar,
            cohort_registry_path=registry,
            exact_cli_invocation=RECOVERY_CLI_INVOCATION,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("recovery_seated_start_utc", RECOVERY_SEATED_START_UTC - 1.0),
        ("recovery_seated_start_event_source", "post_hoc_operator_edit"),
        ("sit_to_record_delay_s", 19.0),
    ],
)
def test_sealed_recovery_receipt_rejects_runtime_timing_tamper(tmp_path, field, value):
    _registry, _run, receipt_path = _physical_recovery_receipt(tmp_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt[field] = value
    receipt_path.write_bytes(canonical_json_bytes(receipt))
    with pytest.raises(ContractError, match="recovery|seated|source|delay|timing|disagrees"):
        validate_sealed_radar_receipt(receipt_path)


@pytest.mark.parametrize("mismatch", ["non_strict_run", "cli", "acquisition_metadata"])
def test_physical_receipt_rejects_non_strict_run_cli_and_metadata_mismatch(tmp_path, mismatch):
    registry = _copy_committed_registry(tmp_path / "registry_v001.json")
    metadata = acquisition_metadata(
        subject_id="P001", data_role="representation_validation", synthetic_fixture=False
    )
    run, sidecar = _make_run(tmp_path, metadata, registry)
    invocation = RECOVERY_CLI_INVOCATION
    if mismatch == "non_strict_run":
        path = run / "run_metadata.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["prospective_study_mode"] = False
        path.write_bytes(canonical_json_bytes(document))
    elif mismatch == "cli":
        invocation = ["live_demo.py", "--live-session", "P999_recovery", "--duration-s", "600"]
    else:
        changed = dict(metadata, clock_offset_start_s=0.5)
        sidecar.write_text(yaml.safe_dump(changed, sort_keys=True), encoding="utf-8")
    with pytest.raises(ContractError, match="strict|prospective|CLI|session|metadata|snapshot|mismatch"):
        write_sealed_radar_receipt(
            run, acquisition_sidecar_path=sidecar, cohort_registry_path=registry,
            exact_cli_invocation=invocation,
        )


@pytest.mark.parametrize(
    "missing",
    [
        "prospective_contract_enforced", "exact_cli_invocation", "frame0_epoch",
        "frame0_event_source", "intended_duration_s",
    ],
)
def test_direct_receipt_verifier_rejects_missing_strict_origin_cli_and_duration_semantics(
    tmp_path, missing
):
    _registry, _run, receipt = _physical_recovery_receipt(tmp_path)
    document = json.loads(receipt.read_text(encoding="utf-8"))
    if missing == "intended_duration_s":
        document["acquisition_metadata"].pop(missing)
    else:
        document.pop(missing)
    receipt.write_bytes(canonical_json_bytes(document))
    with pytest.raises(ContractError, match="strict|prospective|CLI|cli|frame0|duration|metadata"):
        validate_sealed_radar_receipt(receipt)


def _register_capture(registry: Path, output: Path, receipt: Path) -> Path:
    api = getattr(cohort_registry, "register_captured_session", None)
    assert callable(api), "physical workflow needs an explicit capture-registration API"
    return api(registry, output, receipt)


def test_receipt_is_radar_only_no_overwrite_and_engineering_dry_run_not_promotable(tmp_path):
    registry = write_registry(tmp_path / "registry.json", synthetic_registry())
    run, sidecar = _make_run(tmp_path, acquisition_metadata(), registry)
    receipt_path = write_sealed_radar_receipt(
        run, acquisition_sidecar_path=sidecar, cohort_registry_path=registry,
        exact_cli_invocation=RECOVERY_CLI_INVOCATION, engineering_dry_run=True,
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["receipt_state"] == "sealed_radar_only"
    assert not any("reference" in key for key in receipt)
    with pytest.raises(ContractError, match="overwrite"):
        write_sealed_radar_receipt(
            run, acquisition_sidecar_path=sidecar, cohort_registry_path=registry,
            exact_cli_invocation=RECOVERY_CLI_INVOCATION, engineering_dry_run=True,
        )
    with pytest.raises(ContractError, match="never be promoted"):
        finalize_capture(
            run, finalization_metadata={}, cohort_registry_path=registry,
            destination_dir=tmp_path / "promoted", reference_path=None,
        )


def test_zero_received_packets_cannot_seal_or_finalize_a_session(tmp_path):
    registry = write_registry(tmp_path / "registry.json", synthetic_registry())
    run, sidecar = _make_run(tmp_path, acquisition_metadata(), registry)
    metadata_path = run / "run_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["live_packet_stats"]["packets_received"] = 0
    metadata["live_packet_stats"]["packets_dropped"] = 0
    metadata_path.write_bytes(canonical_json_bytes(metadata))
    with pytest.raises(ContractError, match="packets_received=0"):
        write_sealed_radar_receipt(
            run, acquisition_sidecar_path=sidecar, cohort_registry_path=registry,
            exact_cli_invocation=RECOVERY_CLI_INVOCATION, engineering_dry_run=True,
        )


def test_synthetic_fixture_cannot_mint_a_promotable_receipt_by_clearing_dry_run_flag(tmp_path):
    registry = write_registry(tmp_path / "registry.json", synthetic_registry())
    run, sidecar = _make_run(tmp_path, acquisition_metadata(), registry)
    with pytest.raises(ContractError, match="synthetic|dry run|promot"):
        write_sealed_radar_receipt(
            run, acquisition_sidecar_path=sidecar, cohort_registry_path=registry,
            exact_cli_invocation=RECOVERY_CLI_INVOCATION, engineering_dry_run=False,
        )


def test_two_phase_finalization_binds_reference_registry_and_refuses_overwrite(tmp_path, monkeypatch):
    registry1 = _copy_committed_registry(tmp_path / "registry_v001.json")
    metadata = acquisition_metadata(
        subject_id="P001", data_role="representation_validation", synthetic_fixture=False
    )
    run, sidecar = _make_run(tmp_path, metadata, registry1)
    receipt = write_sealed_radar_receipt(
        run, acquisition_sidecar_path=sidecar, cohort_registry_path=registry1,
        exact_cli_invocation=RECOVERY_CLI_INVOCATION,
    )
    registry2 = write_registry_revision(
        registry1, tmp_path / "registry_v002.json",
        subject_updates={"P001": {"sessions": {"P001_recovery": {
            "state": "captured", "radar_receipt_sha256": sha256_file(receipt)
        }}}},
    )
    reference = tmp_path / "P001_recovery_reference.csv"
    reference.write_text("epoch_utc,pr_bpm,pi,rr_bpm\n1900000000,90,5,15\n", encoding="utf-8")
    finalization = {
        "clock_offset_end_s": 0.0, "actual_duration_s": 600.0, "final_pr_bpm": 70.0,
        "reference_acquired": True, "reference_basename": reference.name,
        "reference_export_utc": "2030-01-02T03:14:06Z",
        "post_monitoring_pr_bpm": 75.0, "monitoring_duration_s": 60.0,
    }
    monkeypatch.setattr("src.m2.manifest_v3.PHYSICAL_BYTES_PER_FRAME", 91)
    destination = tmp_path / "promoted"
    manifest = finalize_capture(
        run, finalization_metadata=finalization, cohort_registry_path=registry2,
        destination_dir=destination, reference_path=reference,
    )
    (loaded,) = load_manifest(manifest, Mode.SCORING, root=destination)
    assert loaded.is_scorable and loaded.reference_acquired
    assert (destination / "reference_acquisition.json").is_file()
    with pytest.raises(ContractError, match="overwrite"):
        finalize_capture(
            run, finalization_metadata=finalization, cohort_registry_path=registry2,
            destination_dir=destination, reference_path=reference,
        )


def test_capture_registration_cli_is_explicit_and_validated():
    script = ROOT / "scripts" / "m2_register_capture.py"
    assert script.is_file(), "physical shutdown workflow needs a production registration CLI"
    result = subprocess.run(
        [sys.executable, str(script), "--help"], capture_output=True, text=True,
        cwd=ROOT, timeout=10, check=False,
    )
    assert result.returncode == 0
    help_text = result.stdout + result.stderr
    assert "--registry" in help_text
    assert "--receipt" in help_text
    assert "--output-registry" in help_text


def test_capture_registration_cli_end_to_end_and_rejects_output_other_directory(tmp_path):
    registry, _run, receipt = _physical_recovery_receipt(tmp_path)
    script = ROOT / "scripts" / "m2_register_capture.py"
    output = tmp_path / "registry_v002.json"
    result = subprocess.run(
        [sys.executable, str(script), "--registry", str(registry), "--receipt", str(receipt),
         "--output-registry", str(output)],
        capture_output=True, text=True, cwd=ROOT, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    session = load_registry(output)["subjects"][0]["sessions"][2]
    assert session["state"] == "captured"
    assert session["radar_receipt_sha256"] == sha256_file(receipt)

    other = tmp_path / "other"
    other.mkdir()
    forbidden_output = other / "registry_v003.json"
    result = subprocess.run(
        [sys.executable, str(script), "--registry", str(output), "--receipt", str(receipt),
         "--output-registry", str(forbidden_output)],
        capture_output=True, text=True, cwd=ROOT, timeout=20, check=False,
    )
    assert result.returncode != 0
    assert not forbidden_output.exists()


def test_physical_receipt_registration_appends_captured_revision_usable_by_finalizer(
    tmp_path, monkeypatch
):
    registry, run, receipt = _physical_recovery_receipt(tmp_path)
    next_registry = _register_capture(
        registry, tmp_path / "registry_v002.json", receipt
    )
    document = load_registry(next_registry)
    assert document["revision"] == 2
    assert document["previous_registry_sha256"] == sha256_file(registry)
    subject = next(row for row in document["subjects"] if row["subject_id"] == "P001")
    session = next(row for row in subject["sessions"] if row["session_id"] == "P001_recovery")
    assert session == {
        "session_id": "P001_recovery", "arm": "recovery", "visit_number": 3,
        "state": "captured", "radar_receipt_sha256": sha256_file(receipt),
    }

    reference = tmp_path / "P001_recovery_reference.csv"
    reference.write_text(
        "epoch_utc,pr_bpm,pi,rr_bpm\n1900000000,90,5,15\n", encoding="utf-8"
    )
    monkeypatch.setattr("src.m2.manifest_v3.PHYSICAL_BYTES_PER_FRAME", 91)
    manifest = finalize_capture(
        run,
        finalization_metadata={
            "clock_offset_end_s": 0.0, "actual_duration_s": 600.0,
            "final_pr_bpm": 70.0, "reference_acquired": True,
            "reference_basename": reference.name,
            "reference_export_utc": "2030-01-02T03:14:06Z",
            "post_monitoring_pr_bpm": 75.0, "monitoring_duration_s": 60.0,
        },
        cohort_registry_path=next_registry,
        destination_dir=tmp_path / "promoted",
        reference_path=reference,
    )
    assert manifest.is_file()
    promoted = tmp_path / "promoted"
    for copied_registry in (
        promoted / "cohort_registry_v001.json",
        promoted / "cohort_registry.json",
    ):
        digest_sidecar = copied_registry.with_suffix(copied_registry.suffix + ".sha256")
        assert digest_sidecar.is_file()
        assert digest_sidecar.read_text(encoding="ascii").strip() == sha256_file(copied_registry)


@pytest.mark.parametrize("failure", ["membership", "receipt", "bound_artifact_hash"])
def test_capture_registration_rejects_wrong_membership_receipt_and_hash(tmp_path, failure):
    registry, run, receipt = _physical_recovery_receipt(tmp_path)
    if failure == "membership":
        document = json.loads(receipt.read_text(encoding="utf-8"))
        document["subject_id"] = "P999"
        document["session_id"] = "P999_recovery"
        document["acquisition_metadata"]["subject_id"] = "P999"
        document["acquisition_metadata"]["session_id"] = "P999_recovery"
        receipt.write_bytes(canonical_json_bytes(document))
    elif failure == "receipt":
        receipt.write_bytes(canonical_json_bytes({"schema": "not_a_sealed_receipt"}))
    else:
        (run / "adc_stream.bin").write_bytes(b"tampered after sealing")
    with pytest.raises(
        (ContractError, RegistryError),
        match="membership|receipt|schema|hash|binding|absent from registry|same P001-P015 slot",
    ):
        _register_capture(registry, tmp_path / "registry_v002.json", receipt)


def test_capture_registration_refuses_output_overwrite_without_changing_existing_bytes(tmp_path):
    registry, _run, receipt = _physical_recovery_receipt(tmp_path)
    output = tmp_path / "registry_v002.json"
    output.write_bytes(b"pre-existing authoritative bytes")
    before = output.read_bytes()
    with pytest.raises((ContractError, RegistryError), match="overwrite|exists"):
        _register_capture(registry, output, receipt)
    assert output.read_bytes() == before


def test_capture_registration_rejects_mutated_or_incomplete_predecessor_history(tmp_path):
    registry, _run, receipt = _physical_recovery_receipt(tmp_path)
    latest = write_registry_revision(
        registry, tmp_path / "registry_v002.json", subject_updates={}
    )
    predecessor = load_registry(registry)
    predecessor["subjects"][0]["paced_rate_bpm"] = 15
    registry.write_bytes(canonical_json_bytes(predecessor))
    with pytest.raises((ContractError, RegistryError), match="history|predecessor|digest|chain"):
        _register_capture(latest, tmp_path / "registry_v003.json", receipt)


def test_capture_registration_rejects_nonsealed_and_engineering_receipts(tmp_path):
    physical_dir = tmp_path / "physical"
    physical_dir.mkdir()
    registry, _run, receipt = _physical_recovery_receipt(physical_dir)
    document = json.loads(receipt.read_text(encoding="utf-8"))
    document["receipt_state"] = "draft"
    receipt.write_bytes(canonical_json_bytes(document))
    with pytest.raises((ContractError, RegistryError), match="sealed|state|receipt"):
        _register_capture(registry, physical_dir / "registry_v002.json", receipt)

    engineering_dir = tmp_path / "engineering"
    engineering_dir.mkdir()
    fixture_registry = write_registry(
        engineering_dir / "registry.json", synthetic_registry()
    )
    run, sidecar = _make_run(
        engineering_dir, acquisition_metadata(), fixture_registry
    )
    engineering_receipt = write_sealed_radar_receipt(
        run, acquisition_sidecar_path=sidecar, cohort_registry_path=fixture_registry,
        exact_cli_invocation=RECOVERY_CLI_INVOCATION, engineering_dry_run=True,
    )
    with pytest.raises((ContractError, RegistryError), match="engineering|dry.run|physical|prospective"):
        _register_capture(
            fixture_registry, engineering_dir / "registry_v002.json", engineering_receipt
        )


def test_complete_synthetic_dry_run_has_frozen_order_and_nan_materialization(tmp_path):
    committed_registry_before = DEFAULT_REGISTRY_PATH.read_bytes()
    bundle = tmp_path / "dry_run"
    report = build_synthetic_dry_run(bundle)
    assert report["stages"] == [
        "clean_commit_and_config_identity",
        "registry_membership",
        "arm_metadata_and_clock_offsets",
        "artifact_hash_bindings",
        "packet_map_origin_duration",
        "v3_scoring_load",
        "reference_sensitivity_shape",
    ]
    assert report["invalid_window_indices"] == [0, 1]
    rows = json.loads((bundle / "materialized_radar_rows.json").read_text())["rows"]
    assert len(rows) == 20
    assert [(row["window_index"], row["radar_valid"]) for row in rows[:3]] == [
        (0, False), (1, False), (2, True)
    ]
    assert rows[0]["hr_bpm"] is None and rows[1]["br_bpm"] is None
    assert DEFAULT_REGISTRY_PATH.read_bytes() == committed_registry_before
    assert b"T001" not in committed_registry_before


@pytest.mark.parametrize(
    "relative_path",
    [
        "adc_stream.bin",
        "source_config.yaml",
        "effective_config.json",
        "run_metadata.json",
        "frame_validity.npy",
        "T001_recovery_reference.csv",
        "cohort_registry.json",
        "sealed_radar_receipt.json",
        "reference_acquisition.json",
    ],
)
def test_preflight_fails_after_one_byte_tampering_of_every_bound_class(tmp_path, relative_path):
    bundle = tmp_path / relative_path.replace(".", "_")
    build_synthetic_dry_run(bundle)
    target = bundle / relative_path
    original = target.read_bytes()
    target.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    with pytest.raises(ContractError, match="binding|identity|registry|SHA|hash"):
        validate_preflight(
            bundle / "session_manifest_v3.json", root=bundle,
            reference_sensitivity_path=bundle / "reference_time_sensitivity.json",
        )
