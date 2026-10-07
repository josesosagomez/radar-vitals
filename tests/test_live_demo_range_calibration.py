"""Range calibration: sign, gate, provenance, isolation and hardware-free live run."""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest
import yaml

import scripts.live_demo as demo
from scripts.verify_live_demo_artifacts import verify_run
from src.range_coordinates import bin_range_m, range_bias_m, validate_range_gate
from src.warmup_select import derive_candidate_bins
from src.window_pipeline import run_window_dsp

ROOT = Path(__file__).resolve().parents[1]
CALIBRATED = ROOT / "scripts/live_demo_calibrated_config.yaml"


def config():
    return yaml.safe_load(CALIBRATED.read_text())


def args(monkeypatch, *extra):
    monkeypatch.setattr(sys, "argv", ["live_demo.py", "--config", str(CALIBRATED), *extra])
    return demo._parse_args()


def test_calibration_config_preserves_baseline_and_binds_tracked_record(monkeypatch):
    calibrated = config()
    original = yaml.safe_load((ROOT / "scripts/live_demo_config.yaml").read_text())
    without_calibration = copy.deepcopy(calibrated)
    del without_calibration["profile"]["range_bias_m"]
    del without_calibration["range_calibration"]
    configured_delay = without_calibration["session"].pop("startup_delay_s")
    assert type(configured_delay) is int and configured_delay >= 0
    assert demo._startup_delay_s(calibrated, None) == configured_delay
    assert without_calibration == original
    assert range_bias_m(original) == 0
    provenance = demo._validate_range_calibration(args(monkeypatch), calibrated)
    record = provenance["record"]
    record_bytes = Path(provenance["record_path"]).read_bytes()
    assert hashlib.sha256(record_bytes).hexdigest() == provenance["record_sha256"]
    assert record["range_bias_m"] == 0.0784329
    assert len(record["reported_commands"]) == 8
    assert record["ti_compensation_command"] == record["reported_commands"][0]
    assert float(record["ti_compensation_command"].split()[1]) == range_bias_m(calibrated)
    assert record["python_live_profile_verified"] is False


def test_corrected_gate_and_display_coordinates():
    cfg = config()
    bins = derive_candidate_bins(cfg)
    assert bins == list(range(21, 34))
    assert all(0.8 <= bin_range_m(b, cfg) <= 1.4 for b in bins)
    assert bin_range_m(20, cfg) < 0.8
    assert bin_range_m(34, cfg) > 1.4
    assert bin_range_m(24, cfg) == pytest.approx(0.9679671)
    assert bin_range_m(0, cfg) == -0.0784329  # no hidden clamping
    cfg["profile"]["range_bias_m"] = -0.0784329
    assert bin_range_m(24, cfg) == pytest.approx(1.1248329)
    assert derive_candidate_bins(cfg) == list(range(17, 31))


def test_inclusive_boundaries_bounds_and_explicit_raw_bins():
    cfg = {"profile": {"range_resolution_m": 0.125, "range_bias_m": 0.125,
                       "num_adc_samples": 8},
           "protocol": {"subject_distance_m": [0.125, 0.5]}}
    assert derive_candidate_bins(cfg) == [2, 3, 4, 5]
    cfg["protocol"]["subject_distance_m"] = [-2, 2]
    assert derive_candidate_bins(cfg) == list(range(8))
    cfg["bin_selection"] = {"candidate_bins": [3, 7]}
    assert derive_candidate_bins(cfg) == [3, 7]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), None, True, "bad"])
def test_invalid_bias(value):
    cfg = config()
    cfg["profile"]["range_bias_m"] = value
    with pytest.raises(ValueError, match="range_bias_m"):
        range_bias_m(cfg)


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True])
def test_invalid_resolution(value):
    cfg = config()
    cfg["profile"]["range_resolution_m"] = value
    with pytest.raises(ValueError, match="range_resolution_m"):
        bin_range_m(24, cfg)


@pytest.mark.parametrize("value", [[1, 0], [1], [1, 2, 3], [float("nan"), 1], "0.8,1.4"])
def test_invalid_gate(value):
    cfg = config()
    cfg["protocol"]["subject_distance_m"] = value
    with pytest.raises(ValueError, match="subject_distance_m"):
        validate_range_gate(cfg)


@pytest.mark.parametrize("value", [0, -1, 256.0, True])
def test_invalid_adc_count(value):
    cfg = config()
    cfg["profile"]["num_adc_samples"] = value
    with pytest.raises(ValueError, match="num_adc_samples"):
        validate_range_gate(cfg)


@pytest.mark.parametrize("change", ["replay", "study", "nan", "hash", "bias_record", "missing"])
def test_rejection_precedes_all_launcher_side_effects(tmp_path, monkeypatch, change):
    cfg = config()
    extra = []
    if change == "replay":
        extra = ["--replay", "unused.bin"]
    elif change == "study":
        extra = ["--prospective-sidecar", "unused.yaml"]
    elif change == "nan":
        cfg["profile"]["range_bias_m"] = float("nan")
    elif change == "hash":
        cfg["range_calibration"]["record_sha256"] = "0" * 64
    elif change == "bias_record":
        cfg["profile"]["range_bias_m"] = 0.129
    else:
        cfg["range_calibration"]["record_path"] = "nonexistent.json"
    cfg_path = tmp_path / "invalid.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg))
    monkeypatch.setattr(sys, "argv", ["live_demo.py", "--config", str(cfg_path), *extra])

    def forbidden(*_args, **_kwargs):
        pytest.fail("rejected config reached launcher side effects")

    for name in ("_git_info", "_select_backend", "_create_run_dir", "LiveFrameSource"):
        monkeypatch.setattr(demo, name, forbidden)
    monkeypatch.setattr(demo.time, "sleep", forbidden)
    with pytest.raises(SystemExit, match="ERROR"):
        demo.main()


def _assert_same(left, right):
    if isinstance(left, dict):
        assert left.keys() == right.keys()
        for key in left:
            _assert_same(left[key], right[key])
    elif isinstance(left, np.ndarray):
        # Repeated calls on this float32 fixture showed sub-1e-6 variation in
        # low-energy spectral-floor values; decisions and phase stay exact.
        np.testing.assert_allclose(left, right, rtol=1e-6, atol=1e-6, equal_nan=True)
    elif isinstance(left, (float, np.floating)) and np.isnan(left):
        assert np.isnan(right)
    elif isinstance(left, (float, np.floating)):
        # Repeated least-squares/FFT calculations can differ at ~1e-11 bpm.
        assert left == pytest.approx(right, rel=1e-9, abs=1e-8)
    else:
        assert left == right


def test_fixed_bin_calibration_leaves_dsp_and_adc_unchanged(monkeypatch):
    cfg = config()
    baseline = copy.deepcopy(cfg)
    baseline["profile"].pop("range_bias_m")
    baseline.pop("range_calibration")
    times = np.arange(600) / 20
    phase = 0.4 * np.sin(2 * np.pi * 0.2 * times) + 0.03 * np.sin(2 * np.pi * 1.2 * times)
    samples = np.arange(64)
    cube = np.exp(1j * (phase[:, None, None, None]
                       + 2 * np.pi * 24 * samples[None, None, None, :] / 64)).astype(np.complex64)
    before = cube.tobytes()
    # Independently re-running the float32 FFT can differ with allocation/SIMD
    # alignment on this platform (observed phase difference 1.51e-7 rad).
    # Isolate config wiring from that numerical effect: both compositions must
    # request the same real extraction inputs and receive the same phase.
    import src.window_pipeline as pipeline
    real_extract = pipeline.extract_chest_phase
    extracted = real_extract(cube, 24, method=cfg["phase"]["method"])
    calls = []

    def extraction_spy(input_cube, **kwargs):
        np.testing.assert_array_equal(input_cube, cube)
        calls.append(kwargs)
        return extracted.copy()

    monkeypatch.setattr(pipeline, "extract_chest_phase", extraction_spy)
    calibrated_dsp = run_window_dsp(cube, 24, 20, cfg)
    baseline_dsp = run_window_dsp(cube, 24, 20, baseline)
    assert calls == [{"locked_bin": 24, "method": "delta_before_mean",
                      "clutter_removal": "none"}] * 2
    np.testing.assert_array_equal(calibrated_dsp["phase_raw"], baseline_dsp["phase_raw"])
    np.testing.assert_array_equal(calibrated_dsp["phase_clean"], baseline_dsp["phase_clean"])
    _assert_same(calibrated_dsp, baseline_dsp)
    assert cube.tobytes() == before


def test_real_phase_extraction_agrees_across_float32_memory_layouts():
    from src.respiration import extract_chest_phase

    times = np.arange(600) / 20
    phase = 0.4 * np.sin(2 * np.pi * 0.2 * times)
    cube = np.exp(1j * (phase[:, None, None, None]
                       + 2 * np.pi * 24 * np.arange(64) / 64)).astype(np.complex64)
    storage = np.empty((*cube.shape[:-1], 65), dtype=np.complex64)
    shifted = storage[..., 1:]
    shifted[:] = cube
    np.testing.assert_array_equal(shifted, cube)
    direct = extract_chest_phase(cube, 24)
    strided = extract_chest_phase(shifted, 24)
    np.testing.assert_allclose(direct, strided, rtol=0, atol=1e-6)


@pytest.fixture
def synthetic_live_run(tmp_path, monkeypatch, request):
    """Real launcher + warmup + DSP + artifacts; source/display mocked, no hardware."""
    cfg = config()
    cfg["paths"]["results_dir"] = str(tmp_path / "runs")
    cfg["paths"]["manifest"] = str(tmp_path / "absent_manifest.csv")
    cfg["session"]["startup_delay_s"] = 0
    cfg["profile"].update(num_adc_samples=64, num_rx=1, num_chirps_per_frame=1)
    cfg_path = tmp_path / "synthetic_live.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg))
    t = np.arange(600) / 20
    phase = 0.4 * np.sin(2 * np.pi * 0.2 * t)
    cube = (1000 * np.exp(1j * (phase[:, None, None, None]
                              + 2 * np.pi * 33 * np.arange(64)[None, None, None, :] / 64))).astype(np.complex64)
    # Encode the real SDK SampleSwap=1 word order; decode via production source.
    flat = cube.ravel().reshape(-1, 2)
    raw = np.column_stack((flat.imag[:, 0], flat.imag[:, 1],
                           flat.real[:, 0], flat.real[:, 1])).astype("<i2").tobytes()
    original_source = demo.LiveFrameSource
    updates = []

    class SyntheticSource(original_source):
        def start(self):
            self._fixture_index = 0
            self._raw_mirror_path.write_bytes(raw)
            self.n_received = 106
            self.frame_validity = [True] * 600

        def get_frame(self, timeout_s=0.1):
            idx = self._fixture_index
            if idx == 600:
                return demo._REPLAY_END
            self._fixture_index += 1
            data = raw[idx * self._bytes_per_frame:(idx + 1) * self._bytes_per_frame]
            return idx, self._decode_frame(data)

        def stop(self):
            return

    class Display(demo._HeadlessDisplay):
        def update(self, *values):
            updates.append(values[-1])

    # No socket/UART/DCA is created: --no-configure and mocked source start.
    monkeypatch.setattr(demo, "LiveFrameSource", SyntheticSource)
    monkeypatch.setattr(demo, "_HeadlessDisplay", Display)
    monkeypatch.setitem(sys.modules, "capture", types.SimpleNamespace(DCA1000=None, IWR1642=None))
    extra = ["--locked-bin", "33"] if getattr(request, "param", None) == "manual" else []
    monkeypatch.setattr(sys, "argv", ["live_demo.py", "--config", str(cfg_path),
                                      "--headless", "--no-configure", *extra])
    demo.main()
    run_dir = next((tmp_path / "runs").iterdir())
    assert (run_dir / "adc_stream.bin").read_bytes() == raw
    assert updates == [pytest.approx(1.3603671)]
    return run_dir


def test_live_main_logs_corrected_coordinates_and_verifier_accepts_gate(synthetic_live_run):
    run_dir = synthetic_live_run
    meta = json.loads((run_dir / "run_metadata.json").read_text())
    assert meta["startup_delay_s"] == meta["config"]["session"]["startup_delay_s"] == 0
    warmup = json.loads((run_dir / "warmup_bin_selection.json").read_text())
    assert meta["locked_bin"] == warmup["selected_bin"] == 33
    assert meta["selected_raw_range_m"] == pytest.approx(1.4388)
    assert meta["selected_corrected_range_m"] == pytest.approx(1.3603671)
    assert warmup["selected_corrected_range_m"] == meta["selected_corrected_range_m"]
    assert meta["range_calibration"]["record"]["range_bias_m"] == 0.0784329
    with np.load(run_dir / "live_intermediates.npz", allow_pickle=False) as arrays:
        assert arrays["selected_corrected_range_m"].tolist() == [pytest.approx(1.3603671)]
        assert arrays["selected_raw_range_m"].tolist() == [pytest.approx(1.4388)]
    assert verify_run(run_dir, "live") == 0
    from scripts.diagnose_live_run import analyze_lock, load_run
    lock_report = analyze_lock(load_run(run_dir))
    assert lock_report["selected_range_m"] == pytest.approx(1.3604)
    assert lock_report["selected_raw_range_m"] == pytest.approx(1.4388)
    assert lock_report["range_bias_m"] == 0.0784329
    assert lock_report["range_coordinate_model"] == meta["range_coordinate_model"]


@pytest.mark.parametrize("synthetic_live_run", ["manual"], indirect=True)
def test_preset_bin_logs_and_displays_corrected_coordinates(synthetic_live_run):
    meta = json.loads((synthetic_live_run / "run_metadata.json").read_text())
    assert meta["locked_bin_source"] == "manual"
    assert meta["selected_raw_range_m"] == pytest.approx(1.4388)
    assert meta["selected_corrected_range_m"] == pytest.approx(1.3603671)
    assert not (synthetic_live_run / "warmup_bin_selection.json").exists()
    from scripts.diagnose_live_run import analyze_lock, load_run
    lock_report = analyze_lock(load_run(synthetic_live_run))
    assert lock_report["selected_range_m"] == meta["selected_corrected_range_m"]
    assert lock_report["selected_raw_range_m"] == meta["selected_raw_range_m"]
    assert lock_report["range_bias_m"] == meta["range_bias_m"]


@pytest.mark.parametrize("change", ["missing", "bias", "application", "hash", "record_content"])
def test_verifier_rejects_provenance_tamper(synthetic_live_run, change):
    path = synthetic_live_run / "run_metadata.json"
    meta = json.loads(path.read_text())
    if change == "missing":
        meta.pop("range_calibration")
    elif change == "bias":
        meta["range_calibration"]["record"]["range_bias_m"] = 0.129
    elif change == "application":
        meta["range_calibration"]["application"] = "firmware_and_python"
    elif change == "hash":
        meta["range_calibration"]["record_sha256"] = "0" * 64
    else:
        meta["range_calibration"]["record"]["reported_commands"][0] = "edited"
    path.write_text(json.dumps(meta))
    assert verify_run(synthetic_live_run, "live") == 1


@pytest.mark.parametrize("field", ["range_bias_m", "selected_raw_range_m", "selected_corrected_range_m"])
def test_verifier_rejects_coordinate_tamper(synthetic_live_run, field):
    path = synthetic_live_run / "run_metadata.json"
    meta = json.loads(path.read_text())
    meta[field] += 0.01
    path.write_text(json.dumps(meta))
    assert verify_run(synthetic_live_run, "live") == 1


def test_verifier_accepts_legacy_zero_bias_artifact(synthetic_live_run):
    run_dir = synthetic_live_run
    meta_path = run_dir / "run_metadata.json"
    warm_path = run_dir / "warmup_bin_selection.json"
    meta, warm = json.loads(meta_path.read_text()), json.loads(warm_path.read_text())
    meta["config"]["profile"].pop("range_bias_m")
    meta["config"]["protocol"]["subject_distance_m"] = [0.8, 1.5]
    for record in (meta, warm):
        for name in ("range_bias_m", "range_coordinate_model", "selected_raw_range_m", "selected_corrected_range_m"):
            record.pop(name)
    warm["selected_range_m"] = 1.4388
    for candidate in warm["candidates"]:
        candidate["range_m"] = candidate.pop("raw_range_m")
        candidate.pop("corrected_range_m")
    meta_path.write_text(json.dumps(meta))
    warm_path.write_text(json.dumps(warm))
    assert verify_run(run_dir, "live") == 0
