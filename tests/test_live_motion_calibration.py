"""Synthetic score-fixture checks, never an accepted physical calibration."""
import copy
import hashlib
from pathlib import Path

import numpy as np
import pytest
import yaml

from src.live_motion import calibration as cal
from src.m2.common import canonical_json_bytes, sha256_bytes


def summaries(split="training", n=3, extended=False):
    values = []
    categories = [(state, None) for state in cal.PRESENCE_STATES]
    if extended:
        categories += [("strong", state) for state in cal.BREATHING_STATES]
    for category, (presence, breathing) in enumerate(categories):
        for i in range(n):
            identity = f"synthetic-{split}-{category}-{i}"
            measurement = []
            if breathing:
                periodic = breathing == "periodic"
                quiet = breathing == "quiet"
                measurement = [dict(fft_score=10. if periodic else 0. if quiet else 2.,
                    ha_score=4. if periodic else 0. if quiet else .4,
                    respiratory_block_rms=[1. if periodic else .01 if quiet else .3]*6,
                    subband_block_rms=[.01]*6, drift_rms=.01,
                    persistence=.95 if periodic else None if quiet else .2,
                    valid=True, peaks_agree=periodic, candidate_local_max=periodic,
                    frame_start=0, frame_stop=1200, frame_valid_count=1200,
                    phase=np.zeros(1200).tolist(),
                    target_kind="stationary_reflector" if quiet else "participant")]
            values.append(dict(schema=cal.SUMMARY_SCHEMA, acquisition_id=identity,
                raw_sha256=hashlib.sha256(identity.encode()).hexdigest(), split=split,
                capture_provenance="synthetic_fixture",
                capture_source_id=f"synthetic:{identity}", capture_git_commit="a"*40,
                capture_git_dirty=False, run_metadata_sha256="b"*64,
                frame_validity_sha256="c"*64,
                presence_state=presence, position=cal.POSITIONS[i % 3],
                placement_m=(.85, 1.1, 1.35)[i % 3],
                movement_negative_state=cal.MOVEMENT_NEGATIVE_STATES[i % 3],
                breathing_state=breathing,
                breathing_monitor_blocks=[dict(frame_start=start, frame_stop=start+20,
                    valid=True, presence=-5., phase_activity=.1, range_profile_change=.01)
                    for start in range(0, 1181, 5)] if breathing else [],
                presence_power_db=[{"empty": -30., "weak": -15., "strong": -5.}[presence]],
                still_features={"presence": [-5.], "phase_activity": [.1],
                                "range_profile_change": [.01]},
                movement_episodes=[dict(subtype=subtype,
                    assigned_features=["phase_activity"] if split == "training" else [],
                    feature_maxima={"presence": -5., "phase_activity": .9,
                                    "range_profile_change": .01},
                    frame_bounds=[1220, 1240] if breathing else [20, 40])
                    for subtype in cal.REQUIRED_MOVEMENT_SUBTYPES],
                breathing_measurements=measurement, seed=42,
                breathing_frame_bounds=[0, 1200] if breathing else None,
                target_kind=("stationary_reflector" if breathing == "quiet" else
                             "empty_scene" if presence == "empty" else
                             "weak_reflector" if presence == "weak" else "participant"),
                quiet_target_kind="stationary_reflector" if breathing == "quiet" else None))
    # Two holdouts still need all three positions/negative states represented.
    if n == 2:
        for j, value in enumerate(values):
            value["position"] = cal.POSITIONS[j % 3]
            value["placement_m"] = (.85, 1.1, 1.35)[j % 3]
            value["movement_negative_state"] = cal.MOVEMENT_NEGATIVE_STATES[j % 3]
    return values


@pytest.fixture
def cfg(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    value = yaml.safe_load((root/"scripts/live_demo_calibrated_config.yaml").read_text())
    value["development_motion"] = dict(enabled=False, extended_breathing_enabled=False,
        preliminary_stages_s=[10, 20], ordinary_window_s=30, ordinary_hop_s=3,
        monitor_window_s=1, monitor_hop_s=.25, stillness_confirmation_s=3,
        extended_breathing_window_s=60, extended_breathing_band_hz=[.05, .5],
        calibration=dict(record_path=None, record_sha256=None))
    # Isolate source identity tests from an actively edited production checkout.
    monkeypatch.setattr(cal, "_source_hashes", lambda root, capabilities: {"synthetic.py": "a"*64})
    return value


def test_motion_only_candidate_and_holdout_do_not_require_breathing(cfg):
    train, held = summaries(), summaries("heldout", 2)
    candidate = cal.build_candidate(train, cfg)
    before = copy.deepcopy(candidate)
    record = cal.evaluate_candidate(candidate, held, cfg)
    assert candidate == before
    assert record["status"] == "accepted"
    assert "breathing" not in candidate["thresholds"]
    assert candidate["thresholds"]["movement"]["phase_activity"] == dict(enabled=True, entry=.5, exit=.1)
    assert candidate["thresholds"]["movement"]["presence"] == dict(enabled=False, entry=None, exit=None)


def test_extended_training_bounds_and_midpoints():
    thresholds = cal.derive_thresholds(summaries(extended=True))
    b = thresholds["breathing"]
    assert b["fft_score_min"] == 6.
    assert b["ha_score_min"] == 2.2
    assert b["persistence_min"] == .575
    assert b["quiet_resp_rms_max"] == .01
    assert b["periodic_resp_rms_min"] == 1.


@pytest.mark.parametrize("change", ["presence", "motion", "amplitude", "fft", "ha", "persistence"])
def test_training_overlap_rejects_calibration(change):
    train = summaries(extended=True)
    for item in train:
        if change == "presence" and item["presence_state"] == "empty":
            item["presence_power_db"] = [-5.]
        if change == "motion":
            for episode in item["movement_episodes"]:
                episode["feature_maxima"]["phase_activity"] = .1
        if item["breathing_state"] == "periodic":
            m = item["breathing_measurements"][0]
            if change == "amplitude": m["respiratory_block_rms"] = [.01]*6
            if change == "fft": m["fft_score"] = 2.
            if change == "ha": m["ha_score"] = .4
            if change == "persistence": m["persistence"] = .2
    with pytest.raises(cal.CalibrationError):
        cal.derive_thresholds(train)


def test_missing_independent_coverage_and_duplicate_hashes_fail(cfg):
    for key in ("acquisition_id", "raw_sha256"):
        train = summaries()
        train[1][key] = train[0][key]
        with pytest.raises(cal.CalibrationError):
            cal.build_candidate(train, cfg)
    with pytest.raises(cal.CalibrationError, match="coverage"):
        cal.build_candidate(summaries(n=2), cfg)


@pytest.mark.parametrize("group", ["periodic", "irregular"])
def test_coherence_requires_three_finite_independent_training_acquisitions(group):
    train = summaries(extended=True)
    next(v for v in train if v["breathing_state"] == group)["breathing_measurements"][0]["persistence"] = None
    with pytest.raises(cal.CalibrationError, match="finite independent"):
        cal.derive_thresholds(train)


@pytest.mark.parametrize("overlap", ["acquisition_id", "raw_sha256"])
def test_split_overlap_rejected_before_evaluation(cfg, overlap):
    train, held = summaries(), summaries("heldout", 2)
    held[0][overlap] = train[0][overlap]
    with pytest.raises(cal.CalibrationError, match="overlap"):
        cal.evaluate_candidate(cal.build_candidate(train, cfg), held, cfg)


def test_heldout_failure_does_not_tune_thresholds(cfg):
    candidate = cal.build_candidate(summaries(), cfg)
    held = summaries("heldout", 2)
    held[0]["movement_episodes"][0]["feature_maxima"]["phase_activity"] = .2
    result = cal.evaluate_candidate(candidate, held, cfg)
    assert result["status"] == "rejected" and result["rejection_reasons"]
    assert result["thresholds"] == candidate["thresholds"]


def test_modified_threshold_rejected_even_after_all_hashes_updated(cfg):
    candidate = cal.build_candidate(summaries(), cfg)
    candidate["thresholds"]["movement"]["phase_activity"]["entry"] = .45
    candidate["thresholds_sha256"] = cal._canonical_hash(candidate["thresholds"])
    candidate.pop("candidate_payload_sha256")
    candidate["candidate_payload_sha256"] = cal._canonical_hash(candidate)
    with pytest.raises(cal.CalibrationError, match="derivation"):
        cal.evaluate_candidate(candidate, summaries("heldout", 2), cfg)


def test_nonsemantic_options_do_not_invalidate_calibration(cfg):
    expected = cal.semantic_identity(cfg)
    cfg["session"]["startup_delay_s"] = 30
    cfg["display"]["matplotlib_backend"] = "Agg"
    cfg["paths"]["results_dir"] = "results/different"
    cfg["development_motion"]["enabled"] = True
    cfg["development_motion"]["calibration"] = dict(record_path="elsewhere", record_sha256="b"*64)
    assert cal.semantic_identity(cfg) == expected
    cfg["profile"]["range_bias_m"] += .01
    assert cal.semantic_identity(cfg)["settings_sha256"] != expected["settings_sha256"]


@pytest.mark.parametrize("section,key,value", [("bin_selection", "candidate_bins", [21,22]),
    ("bin_selection", "settle_skip_s", 4.), ("phase", "impulse_clip_rad", 1.4),
    ("heart", "ahet_deviation_hz", .2), ("respiration", "max_harmonics", 2)])
def test_signal_semantics_invalidate_calibration(cfg, section, key, value):
    before = cal.semantic_identity(cfg)
    cfg[section][key] = value
    assert cal.semantic_identity(cfg)["settings_sha256"] != before["settings_sha256"]


def test_validator_recomputes_failed_holdout_even_if_status_forged(cfg, tmp_path, monkeypatch):
    candidate = cal.build_candidate(summaries(), cfg)
    held = summaries("heldout", 2)
    held[0]["presence_power_db"] = [-20.]
    rejected = cal.evaluate_candidate(candidate, held, cfg)
    assert rejected["status"] == "rejected"
    rejected["status"] = "accepted"
    rejected["rejection_reasons"] = []
    content = canonical_json_bytes(rejected)
    path = tmp_path/"synthetic_record.json"
    path.write_bytes(content)
    cfg["development_motion"]["calibration"] = dict(record_path=path.name, record_sha256=sha256_bytes(content))
    monkeypatch.setattr(cal, "_require_runtime_hardware_provenance", _assert_synthetic_provenance)
    with pytest.raises(cal.CalibrationError, match="recomputed"):
        cal.validate_calibration(cfg, root=tmp_path)


def test_missing_load_bearing_source_is_error(monkeypatch, tmp_path):
    # Exercise actual file binding independently of the fixture's source stub.
    monkeypatch.undo()
    with pytest.raises(cal.CalibrationError, match="source is missing"):
        cal._source_hashes(tmp_path, ("movement",))


def test_rejected_prospective_paths_before_any_raw_read():
    from scripts.calibrate_live_motion import _relative_development_run
    root = Path(__file__).resolve().parents[1]
    for path in ("data/raw/P001", "results/live_demo/P001", "results/live_demo/prospective/x",
                 "../outside", "results/m2_capture_work/x"):
        with pytest.raises(cal.CalibrationError):
            _relative_development_run(root, path, "run_dir")


@pytest.mark.parametrize("change", ["short", "participant"])
def test_quiet_training_requires_complete_stationary_reflector_epoch(change):
    train = summaries(extended=True)
    quiet = next(v for v in train if v["breathing_state"] == "quiet")
    if change == "short":
        quiet["breathing_frame_bounds"] = [0, 1199]
    else:
        quiet["target_kind"] = "participant"
    with pytest.raises(cal.CalibrationError):
        cal.derive_thresholds(train)


def test_training_identity_lists_cannot_hide_split_overlap(cfg):
    candidate = cal.build_candidate(summaries(), cfg)
    candidate["training_acquisition_ids"] = ["forged-id"]
    candidate.pop("candidate_payload_sha256")
    candidate["candidate_payload_sha256"] = cal._canonical_hash(candidate)
    with pytest.raises(cal.CalibrationError):
        cal.evaluate_candidate(candidate, summaries("heldout", 2), cfg)


def test_heldout_requires_every_training_movement_subtype(cfg):
    train = summaries()
    for item in train:
        extra = copy.deepcopy(item["movement_episodes"][0])
        extra["subtype"] = "arm_motion"
        item["movement_episodes"].append(extra)
    candidate = cal.build_candidate(train, cfg)
    with pytest.raises(cal.CalibrationError, match="subtype"):
        cal.evaluate_candidate(candidate, summaries("heldout", 2), cfg)


def test_offline_capture_summary_uses_production_sampleswap_decoder(cfg, tmp_path, monkeypatch):
    from scripts import calibrate_live_motion as tool
    from scripts.live_demo import LiveFrameSource
    from scipy.fft import fft
    cfg["profile"].update(num_adc_samples=8, num_rx=1, num_chirps_per_frame=1,
                          range_resolution_m=.1, range_bias_m=0., iq_swap=True)
    cfg["protocol"]["subject_distance_m"] = [0., .7]
    cfg["bin_selection"]["candidate_bins"] = [1, 2]
    # Integer IQ pairs in wire Q,Q,I,I order, independent of decoder internals.
    real = np.array([100, 71, 0, -71, -100, -71, 0, 71], dtype="<i2")
    imag = np.array([0, 71, 100, 71, 0, -71, -100, -71], dtype="<i2")
    words = np.column_stack((imag[::2], imag[1::2], real[::2], real[1::2]))
    payload = words.astype("<i2").tobytes() * 40
    raw = tmp_path/"adc_stream.bin"
    raw.write_bytes(payload)
    decoded = []
    original = LiveFrameSource._decode_frame
    def spy(self, value):
        result = original(self, value)
        decoded.append(result.copy())
        return result
    monkeypatch.setattr(LiveFrameSource, "_decode_frame", spy)
    monkeypatch.setattr(LiveFrameSource, "start", lambda *a: pytest.fail("offline calibration opened hardware"))
    entry = summaries()[0]
    entry.update(target_kind="participant", presence_state="strong", still_intervals=[[0, 40]],
                 movement_episodes=[], breathing_window=None, placement_m=.1)
    prepared = dict(raw_path=raw, validity=np.ones(40, bool), n_frames=40,
                    bytes_per_frame=len(payload)//40, run_dir=tmp_path,
                    metadata_sha256="a"*64, validity_sha256="b"*64,
                    capture_provenance="synthetic_fixture", capture_source_id="synthetic:decoder",
                    capture_git_commit="a"*40, capture_git_dirty=False)
    evidence = tmp_path/"summaries"
    evidence.mkdir()
    result = tool._summarize_capture(tmp_path, entry, cfg, prepared, evidence)
    assert len(decoded) == 40 and raw.read_bytes() == payload
    expected = (real.astype(np.float32)+1j*imag.astype(np.float32)).reshape(1,1,8)
    np.testing.assert_array_equal(decoded[0], expected)
    spectrum = fft(expected*np.hanning(8).astype(np.float32), axis=-1)
    power = max(abs(spectrum[0,0,[1,2]])**2)
    np.testing.assert_allclose(result["presence_power_db"], 10*np.log10(power), atol=2e-6)
    # Complex64 conjugate multiplication may leave sub-ulp imaginary roundoff.
    np.testing.assert_allclose(result["still_features"]["phase_activity"], 0,
                               rtol=0, atol=np.finfo(np.float32).eps)
    with np.load(evidence/f"{entry['acquisition_id']}.npz", allow_pickle=False) as saved:
        assert saved["monitor_valid"].all()
        assert saved["monitor_frame_stop"][-1] == 40


@pytest.mark.parametrize("failure", [None, "training", "heldout", "heldout_prepare"])
def test_cli_locks_candidate_before_holdout_and_retains_failures(cfg, tmp_path, monkeypatch, failure):
    from scripts import calibrate_live_motion as tool
    (tmp_path/"results/live_demo").mkdir(parents=True)
    train, held = summaries(), summaries("heldout", 2)
    entries = train + held
    if failure == "training":
        for item in train:
            for episode in item["movement_episodes"]:
                episode["feature_maxima"]["phase_activity"] = .1
    if failure == "heldout":
        held[0]["presence_power_db"] = [-20.]
    config_path, manifest_path = tmp_path/"config.yaml", tmp_path/"manifest.yaml"
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    manifest_path.write_text("synthetic_orchestration_fixture: true\n", encoding="utf-8")
    monkeypatch.setattr(tool, "_validate_manifest", lambda *args: entries)
    output = tmp_path/"calibration"
    reads = []
    prepared_splits = []
    candidate_bytes = []
    def prepare(root, entry, config):
        prepared_splits.append(entry["split"])
        if entry["split"] == "heldout":
            # Metadata, validity, and file-size inspection are also held-out
            # evidence. None may precede locking the training candidate.
            content = (output/"candidate.json").read_bytes()
            assert content
            candidate_bytes.append(content)
            if failure == "heldout_prepare":
                raise cal.CalibrationError("synthetic held-out metadata failure")
        return {}
    monkeypatch.setattr(tool, "_prepare_capture", prepare)
    def verify(entry, prepared):
        reads.append(entry["split"])
        if entry["split"] == "heldout":
            content = (output/"candidate.json").read_bytes()
            assert content
            candidate_bytes.append(content)
    monkeypatch.setattr(tool, "_verify_raw_hash", verify)
    def summarize(root, entry, config, prepared, evidence_dir):
        value = copy.deepcopy(entry)
        value.update(run_dir="results/live_demo/synthetic", run_metadata_sha256="a"*64,
                     frame_validity_sha256="b"*64, evidence_npz_path="synthetic.npz",
                     evidence_npz_sha256="c"*64, selected_bin=None, selection_evidence={})
        return value
    monkeypatch.setattr(tool, "_summarize_capture", summarize)
    if failure in ("training", "heldout_prepare"):
        with pytest.raises(cal.CalibrationError):
            tool.run(config_path, manifest_path, output, capture_root=tmp_path)
        import json
        saved = json.loads((output/"calibration_failure.json").read_text())
        assert saved["status"] == "rejected" and saved["reason"]
        assert "heldout" not in reads
        if failure == "training":
            assert "heldout" not in prepared_splits
        else:
            assert prepared_splits[-1] == "heldout"
            assert (output/"candidate.json").read_bytes() == candidate_bytes[0]
    else:
        result = tool.run(config_path, manifest_path, output, capture_root=tmp_path)
        assert result["record"]["status"] == ("rejected" if failure else "accepted")
        assert candidate_bytes and all(value == candidate_bytes[0] for value in candidate_bytes)
        assert (output/"candidate.json").read_bytes() == candidate_bytes[0]
        assert reads == ["training"]*len(train) + ["heldout"]*len(held)


def _synthetic_phase_summaries():
    from src.live_motion.breathing import measure_breathing
    train, held = summaries(extended=True), summaries("heldout", 2, extended=True)
    for index, item in enumerate(train + held):
        state = item["breathing_state"]
        if not state:
            continue
        phase = (np.sin(2*np.pi*.2*np.arange(1200)/20 + .03*index) if state == "periodic"
                 else np.zeros(1200) if state == "quiet" else
                 np.random.default_rng(index).normal(0, .03, 1200))
        measured = measure_breathing(phase)
        measured.update(phase=phase, frame_start=0, frame_stop=1200,
                        frame_valid_count=1200, target_kind=item["target_kind"])
        item["breathing_measurements"] = [measured]
    return train, held


def _assert_synthetic_provenance(values, field):
    # Test-only bridge for algebra/proof checks. Production preflight retains
    # its strict hardware-provenance gate; no synthetic record can enable it.
    assert values and all(item["capture_provenance"] == "synthetic_fixture" for item in values)


def test_full_synthetic_record_recomputes_decisions_and_roundtrips(cfg, tmp_path, monkeypatch):
    cfg["development_motion"]["extended_breathing_enabled"] = True
    train, held = _synthetic_phase_summaries()
    candidate = cal.build_candidate(train, cfg)
    record = cal.evaluate_candidate(candidate, held, cfg)
    assert record["status"] == "accepted", record["rejection_reasons"]
    content = canonical_json_bytes(record)
    path = tmp_path/"synthetic_only_record.json"
    path.write_bytes(content)
    cfg["development_motion"]["calibration"] = dict(record_path=path.name, record_sha256=sha256_bytes(content))
    with pytest.raises(cal.CalibrationError, match="non-hardware"):
        cal.validate_calibration(cfg, root=tmp_path)
    monkeypatch.setattr(cal, "_require_runtime_hardware_provenance", _assert_synthetic_provenance)
    assert cal.validate_calibration(cfg, root=tmp_path) == record


@pytest.mark.parametrize("feature,value", [("presence", -6.), ("phase_activity", .11)])
def test_heldout_quiet_quality_failure_rejects_locked_record(cfg, feature, value):
    cfg["development_motion"]["extended_breathing_enabled"] = True
    train, held = _synthetic_phase_summaries()
    candidate = cal.build_candidate(train, cfg)
    before = copy.deepcopy(candidate)
    quiet = next(item for item in held if item["breathing_state"] == "quiet")
    quiet["breathing_monitor_blocks"][100][feature] = value
    record = cal.evaluate_candidate(candidate, held, cfg)
    assert record["status"] == "rejected"
    assert record["rejection_reasons"]
    assert candidate == before and record["thresholds"] == before["thresholds"]


@pytest.mark.parametrize("artifact", [None, "adc_stream.bin", "run_metadata.json", "frame_validity.npy"])
def test_capture_symlink_resolution_rejected_before_read(tmp_path, monkeypatch, artifact):
    from scripts import calibrate_live_motion as tool
    run_dir = tmp_path/"results/live_demo/synthetic"
    run_dir.mkdir(parents=True)
    outside = tmp_path/"outside"
    outside.mkdir()
    target = run_dir if artifact is None else run_dir/artifact
    if artifact is not None:
        target.write_bytes(b"unread synthetic fixture")
    original_resolve = Path.resolve
    def resolved(path, *args, **kwargs):
        if path == target:
            return outside if artifact is None else outside/artifact
        return original_resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", resolved)
    # Simulated resolution works on Windows without symlink privileges. No
    # content exists outside the allowed run, and neither function reads it.
    with pytest.raises(cal.CalibrationError, match="outside|symlink|escape"):
        if artifact is None:
            tool._relative_development_run(tmp_path, "results/live_demo/synthetic", "run_dir")
        else:
            tool._regular_run_artifact(run_dir, artifact, "synthetic")


@pytest.mark.parametrize("artifact", ["adc_stream.bin", "run_metadata.json", "frame_validity.npy"])
def test_hardlinked_capture_artifact_rejected_before_read(tmp_path, artifact):
    import os
    from scripts.calibrate_live_motion import _regular_run_artifact
    run_dir = tmp_path/"results/live_demo/synthetic"
    run_dir.mkdir(parents=True)
    original = tmp_path/"synthetic_alias_target"
    original.write_bytes(b"synthetic fixture only")
    try:
        os.link(original, run_dir/artifact)
    except OSError as exc:
        pytest.skip(f"filesystem does not support creating test hard links: {exc}")
    with pytest.raises(cal.CalibrationError, match="hard.link|alias|link count"):
        _regular_run_artifact(run_dir, artifact, "synthetic")


@pytest.mark.parametrize("kind", ["fake", "synthetic", "replay", "unknown"])
def test_fake_live_metadata_rejected_before_validity_or_adc_read(tmp_path, monkeypatch, kind):
    from scripts import calibrate_live_motion as tool
    metadata = dict(mode="live", prospective_study_mode=False,
                    completion_status="completed", raw_stream_format="adc_bytes_no_packet_headers",
                    source_kind=kind, source_id=f"{kind}:fixture", git_commit="a"*40,
                    git_dirty=False)
    monkeypatch.setattr(tool, "_relative_development_run", lambda *args: tmp_path)
    monkeypatch.setattr(tool, "_regular_run_artifact", lambda root, name, identity: root/name)
    monkeypatch.setattr(tool, "read_json_object", lambda path: metadata)
    monkeypatch.setattr(tool.np, "load", lambda *a, **k: pytest.fail("read validity before provenance"))
    monkeypatch.setattr(tool, "sha256_file", lambda *a: pytest.fail("read artifact before provenance"))
    with pytest.raises(cal.CalibrationError, match="source_kind|replay|synthetic"):
        tool._prepare_capture(tmp_path, dict(acquisition_id="synthetic"), {})


@pytest.mark.parametrize("change", ["dirty", "commit", "source", "replay"])
def test_legacy_capture_exception_is_exact_and_clean(change):
    from scripts import calibrate_live_motion as tool
    metadata = dict(git_commit=tool.FALLBACK_CHECKPOINT_SHA, git_dirty=False,
                    replay_files=None, replay_file_hashes=None)
    assert tool._capture_provenance(metadata, "fixture", run_name="unit_capture")["capture_provenance"] == "legacy_checkpoint_live_dca1000"
    if change == "dirty":
        metadata["git_dirty"] = True
    elif change == "commit":
        metadata["git_commit"] = "a"*40
    elif change == "source":
        metadata["source_id"] = "synthetic:fixture"
    else:
        metadata["replay_files"] = ["synthetic.bin"]
    with pytest.raises(cal.CalibrationError):
        tool._capture_provenance(metadata, "fixture", run_name="unit_capture")


def test_capture_provenance_is_bound_even_when_thresholds_are_unchanged(cfg):
    train = summaries()
    first = cal.build_candidate(train, cfg)
    changed = copy.deepcopy(train)
    changed[0]["run_metadata_sha256"] = "f"*64
    second = cal.build_candidate(changed, cfg)
    assert first["thresholds"] == second["thresholds"]
    assert first["candidate_payload_sha256"] != second["candidate_payload_sha256"]
    saved = {item["acquisition_id"]: item for item in first["training_summaries"]}
    for item in train:
        for field in ("capture_provenance", "capture_source_id", "capture_git_commit",
                      "capture_git_dirty", "run_metadata_sha256", "frame_validity_sha256"):
            assert saved[item["acquisition_id"]][field] == item[field]


@pytest.mark.parametrize("marker", [False, True, "false"])
def test_capture_synthetic_marker_has_strict_boolean_meaning(marker):
    from scripts import calibrate_live_motion as tool
    metadata = dict(git_commit="a"*40, git_dirty=False,
                    source_kind="live_dca1000", source_id="live:unit_capture:adc_stream.bin",
                    synthetic_fixture=marker)
    # This exercises metadata validation only; no capture or physical evidence
    # is constructed by this unit test.
    if marker is False:
        assert tool._capture_provenance(metadata, "unit", run_name="unit_capture")["capture_provenance"] == "live_dca1000"
    else:
        with pytest.raises(cal.CalibrationError, match="synthetic|Boolean"):
            tool._capture_provenance(metadata, "unit", run_name="unit_capture")


def test_core_cannot_forge_legacy_hardware_provenance_with_another_commit(cfg):
    train = summaries()
    train[0].update(capture_provenance="legacy_checkpoint_live_dca1000",
                    capture_source_id=None, capture_git_commit="a"*40)
    with pytest.raises(cal.CalibrationError, match="legacy|checkpoint"):
        cal.build_candidate(train, cfg)


@pytest.mark.parametrize("source", ["live:synthetic", "live:another_run:adc_stream.bin"])
def test_capture_source_identity_must_match_the_actual_run_directory(source):
    from scripts.calibrate_live_motion import _capture_provenance
    metadata = dict(git_commit="a"*40, git_dirty=False,
                    source_kind="live_dca1000", source_id=source)
    with pytest.raises(cal.CalibrationError, match="source_id"):
        _capture_provenance(metadata, "unit", run_name="unit_capture")


@pytest.mark.parametrize("feature,value", [("presence", -6.), ("phase_activity", .11)])
def test_training_breathing_quality_guard_covers_every_block(feature, value):
    train = summaries(extended=True)
    quiet = next(item for item in train if item["breathing_state"] == "quiet")
    quiet["breathing_monitor_blocks"][100][feature] = value
    with pytest.raises(cal.CalibrationError, match="quality|presence|movement|guard"):
        cal.derive_thresholds(train)


def test_missing_breathing_monitor_block_cannot_supply_quiet_evidence():
    train = summaries(extended=True)
    quiet = next(item for item in train if item["breathing_state"] == "quiet")
    quiet["breathing_monitor_blocks"].pop(100)
    with pytest.raises(cal.CalibrationError, match="cover|hop|gap"):
        cal.derive_thresholds(train)


def test_movement_overlap_with_breathing_window_rejected():
    from scripts.calibrate_live_motion import _reject_overlapping_labels
    entry = dict(acquisition_id="synthetic", still_intervals=[], breathing_window=[0,1200],
                 movement_episodes=[dict(frame_bounds=[1199,1220])])
    with pytest.raises(cal.CalibrationError, match="overlap"):
        _reject_overlapping_labels(entry, 1300)


@pytest.mark.parametrize("change", ["equal", "reversed"])
def test_position_labels_cannot_fabricate_distance_coverage(cfg, change):
    train = summaries()
    for item in train:
        if change == "equal":
            item["placement_m"] = 1.1
        else:
            item["placement_m"] = {"near":1.35, "middle":1.1, "far":.85}[item["position"]]
    with pytest.raises(cal.CalibrationError, match="placement|ordered|distance"):
        cal.build_candidate(train, cfg)
