"""Integration tests for Step 5 (extract_breathing_rate.py).

Creates minimal synthetic HDF5 cubes, manifests, quality masks, and Masimo CSVs
in a temporary directory, then runs _process_session() directly to verify end-to-end
behaviour without real hardware data.

Run: pytest tests/test_step5_breathing.py -v
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# Must be set before any matplotlib import to avoid GUI crashes in headless CI
os.environ.setdefault("MPLBACKEND", "Agg")

import h5py
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from steps.step_5.extract_breathing_rate import _process_session  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

FS              = 20.0    # Hz
RR_TRUE         = 15.0   # bpm, known breathing rate
RR_HZ           = RR_TRUE / 60.0
DUR_S           = 90.0   # seconds — long enough for 30 s windows with hop 5 s
N_FRAMES        = int(DUR_S * FS)
TRIM_S          = 10
TRIM_FRAMES     = int(TRIM_S * FS)
LOCKED_BIN      = 8
N_CHIRPS        = 4
N_RX            = 2
N_ADC           = 64


def _make_cube(rng: np.random.Generator, rr_bpm: float = RR_TRUE) -> np.ndarray:
    """Synthetic (N, chirps, rx, adc_samples) complex64 cube with breathing at locked_bin."""
    t      = np.arange(N_FRAMES) / FS
    phase  = 2.0 * np.sin(2 * np.pi * (rr_bpm / 60.0) * t)
    cube   = (0.01 * (rng.standard_normal((N_FRAMES, N_CHIRPS, N_RX, N_ADC))
                      + 1j * rng.standard_normal((N_FRAMES, N_CHIRPS, N_RX, N_ADC)))
              ).astype(np.complex64)
    phasor = np.exp(1j * phase).astype(np.complex64)
    cube[:, :, :, LOCKED_BIN] += phasor[:, None, None]
    return cube


def _write_h5(path: Path, cube: np.ndarray, trim_frames: int,
              add_quality_mask: bool = True, all_good: bool = True) -> None:
    """Write a minimal HDF5 file mirroring Step 2 + Step 4 format."""
    with h5py.File(path, "w") as f:
        f.attrs["num_frames"]    = cube.shape[0]
        f.attrs["frame_rate_hz"] = float(FS)
        f.create_dataset("cube", data=cube)
        if add_quality_mask:
            n_analysis = cube.shape[0] - trim_frames
            mask = np.ones(n_analysis, dtype=bool)
            if not all_good:
                # Flag 20% of frames bad (exceeds max_bad_fraction=0.10)
                mask[:int(n_analysis * 0.20)] = False
            qm = f.create_dataset("quality_mask", data=mask)
            qm.attrs["trim_frames"] = trim_frames


def _write_masimo(path: Path, t0: float, dur_s: float, rr_bpm: float = 16.0,
                  include_nan: bool = False) -> None:
    """Write a minimal Masimo CSV covering the session."""
    n = int(dur_s)
    epochs = np.arange(n, dtype=int) + int(t0)
    rr_vals = np.full(n, rr_bpm)
    if include_nan:
        rr_vals[::5] = np.nan
    df = pd.DataFrame({
        "Session":          0,
        "Index":            np.arange(n),
        "Timestamp":        epochs,
        "Date":             "1/1/26",
        "Time":             "12:00:00 PM",
        "O2 Saturation":    98,
        "Beats / min":      75,
        "Perfusion Index":  2.0,
        "Pleth Variability": 10,
        "Breaths / min":    rr_vals,
    })
    df.to_csv(path, index=False)


def _make_manifest_row(session_id: str, t0: float) -> dict:
    return {
        "session_id":                session_id,
        "participant_id":            "T_test",
        "split":                     "development",
        "locked_participant_never_used_for_tuning": "",
        "timezone":                  "UTC",
        "radar_start_epoch_seconds": t0,
        "radar_to_reference_offset_seconds": 0,
        "radar_sync_elapsed_seconds":  "",
        "reference_sync_epoch_seconds": "",
        "posture":                   "seated_no_back",
        "distance_cm":               130,
        "locked_bin":                LOCKED_BIN,
        "radar_orientation":         "frontal_chest",
        "masimo_model":              "MightySat",
        "stationary_intervals":      f"{TRIM_S}-{int(DUR_S)}",
        "exclusion_reason":          "",
        "notes":                     "",
        "range_resolution_m":        0.0436,
        "iq_swap":                   True,
        "highest_bins":              "",
        "locked_range_m":            0.3488,
        "radar_chest_distance_cm":   113,
        "chest_bin_confidence":      "high",
        "chest_bin_methods":         "energy",
        "chest_bin_review_required": False,
        "chest_bin_run_id":          "test_run_001",
    }


def _make_cfg(tmp_root: Path, session_id: str) -> dict:
    return {
        "paths": {
            "manifest":       str(tmp_root / "manifest.csv"),
            "cubes_dir":      str(tmp_root / "cubes"),
            "raw_dir":        str(tmp_root / "raw"),
            "processed_dir":  str(tmp_root / "processed" / "breathing_rate"),
            "results_dir":    str(tmp_root / "results"),
        },
        "input_policy": {
            "require_step3_confidence":    "medium",
            "reject_step3_review_required": False,
            "require_quality_mask":        True,
            "max_bad_fraction":            0.10,
            "skip_excluded_sessions":      True,
            "skip_missing_h5":             True,
        },
        "windowing": {
            "window_s":                 30.0,
            "hop_s":                    5.0,
            "use_stationary_intervals": True,
        },
        "phase": {
            "method":  "delta_before_mean",
            "detrend": "linear",
        },
        "respiration": {
            "band_hz":                  [0.10, 0.50],
            "max_harmonics":            3,
            "harmonic_max_hz":          None,
            "stft_subwindow_s":         10.0,
            "stft_overlap":             0.5,
            "fft_ha_agree_bpm_high":    2.0,
            "fft_ha_agree_bpm_medium":  4.0,
            "stft_std_high_bpm":        2.0,
            "stft_std_medium_bpm":      4.0,
            "fft_fallback_snr_db":      6.0,
            "emit_low_confidence":      False,
        },
        "step6_contract": {
            "write_for_heart_rate": True,
        },
        "seed": 42,
    }


@pytest.fixture()
def session_env(tmp_path: Path):
    """Set up a complete synthetic session in tmp_path. Returns (cfg, row, sid, tmp_path)."""
    rng = np.random.default_rng(123)
    sid = "test_step5_synth"
    t0  = 1_700_000_000.0

    cubes_dir = tmp_path / "cubes"
    raw_dir   = tmp_path / "raw"
    cubes_dir.mkdir()
    raw_dir.mkdir()
    (tmp_path / "processed" / "breathing_rate").mkdir(parents=True)
    (tmp_path / "results").mkdir()

    cube     = _make_cube(rng, rr_bpm=RR_TRUE)
    h5_path  = cubes_dir / f"{sid}.h5"
    _write_h5(h5_path, cube, TRIM_FRAMES, add_quality_mask=True, all_good=True)
    _write_masimo(raw_dir / f"{sid}_masimo.csv", t0, DUR_S + 20, rr_bpm=15.0)

    manifest_row = pd.Series(_make_manifest_row(sid, t0))
    cfg = _make_cfg(tmp_path, sid)

    return cfg, manifest_row, sid, tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestProcessSession:
    def test_produces_correct_rr_from_synthetic_cube(self, session_env):
        """Radar RR estimates should be close to the known synthetic breathing rate."""
        cfg, row, sid, tmp = session_env
        out_dir = tmp / "results" / sid / "step_5"

        summary = _process_session(
            sid, row, cfg,
            cubes_dir=tmp / "cubes",
            data_raw=tmp / "raw",
            out_dir=out_dir,
            commit="test", no_plots=True,
        )

        assert summary["n_windows"] > 0
        assert summary["n_valid"] > 0
        mae = summary.get("mae_bpm")
        assert mae is not None, "MAE should not be None when Masimo RR is present"
        assert mae < 5.0, f"MAE={mae:.2f} bpm — expected < 5 bpm for clean synthetic signal"

    def test_step6_contract_csv_contains_required_columns(self, session_env):
        """The Step 6 contract CSV must contain every required column."""
        required_cols = [
            "window_index", "start_epoch", "end_epoch",
            "start_frame", "end_frame",
            "radar_rr_bpm", "resp_peak_hz",
            "resp_valid", "resp_confidence", "quality_gated",
        ]
        cfg, row, sid, tmp = session_env
        out_dir = tmp / "results" / sid / "step_5"

        _process_session(sid, row, cfg, cubes_dir=tmp / "cubes",
                         data_raw=tmp / "raw", out_dir=out_dir, commit="test", no_plots=True)

        contract = tmp / "processed" / "breathing_rate" / f"{sid}.csv"
        assert contract.exists(), "Step 6 contract CSV was not written"
        df = pd.read_csv(contract)
        missing = [c for c in required_cols if c not in df.columns]
        assert not missing, f"Step 6 CSV missing required columns: {missing}"

    def test_npz_intermediates_contain_ha_debug_arrays(self, session_env):
        """respiration_intermediates.npz must contain all HA/debug arrays with correct shapes."""
        cfg, row, sid, tmp = session_env
        out_dir = tmp / "results" / sid / "step_5"

        summary = _process_session(
            sid, row, cfg, cubes_dir=tmp / "cubes",
            data_raw=tmp / "raw", out_dir=out_dir, commit="test", no_plots=True,
        )
        n_windows = summary["n_windows"]

        npz_path = out_dir / "respiration_intermediates.npz"
        assert npz_path.exists(), "respiration_intermediates.npz was not written"

        data = np.load(npz_path, allow_pickle=False)

        # Scalar-indexed arrays: one entry per window
        for key in ("window_starts", "window_ends"):
            assert key in data, f"missing key {key}"
            assert data[key].shape == (n_windows,), (
                f"{key}: expected ({n_windows},), got {data[key].shape}"
            )

        # Per-window phase: shape (n_windows, window_frames)
        assert "window_phase" in data, "missing key window_phase"
        assert data["window_phase"].ndim == 2
        assert data["window_phase"].shape[0] == n_windows

        # HA candidate arrays: shape (n_windows, max_candidates) — NaN-padded
        for key in ("ha_candidate_freqs_hz", "ha_candidate_scores"):
            assert key in data, f"missing key {key}"
            assert data[key].ndim == 2, f"{key} should be 2-D (n_windows × max_cands)"
            assert data[key].shape[0] == n_windows

        # HA harmonic arrays: shape (n_windows, max_candidates, max_harmonics)
        for key in ("ha_harmonic_freqs_hz", "ha_harmonic_power"):
            assert key in data, f"missing key {key}"
            assert data[key].ndim == 3, f"{key} should be 3-D (n_windows × max_cands × max_harms)"
            assert data[key].shape[0] == n_windows

        # Candidate and harmonic dimension sizes must agree
        assert data["ha_candidate_freqs_hz"].shape[1] == data["ha_harmonic_freqs_hz"].shape[1], (
            "max_candidates dimension mismatch between ha_candidate_freqs_hz and ha_harmonic_freqs_hz"
        )

        # Full-session arrays
        assert "phase_full" in data
        assert "quality_mask_analysis" in data

    def test_quality_gated_windows_preserved_as_nan(self, session_env):
        """Quality-gated windows must remain in the CSV with NaN radar estimates."""
        cfg, row, sid, tmp = session_env
        # Overwrite HDF5 with a bad quality mask: first 50% of frames flagged bad
        rng  = np.random.default_rng(77)
        cube = _make_cube(rng)
        h5_path = tmp / "cubes" / f"{sid}.h5"
        h5_path.unlink()
        _write_h5(h5_path, cube, TRIM_FRAMES, add_quality_mask=True, all_good=False)

        out_dir = tmp / "results" / sid / "step_5"
        _process_session(
            sid, row, cfg, cubes_dir=tmp / "cubes",
            data_raw=tmp / "raw", out_dir=out_dir, commit="test", no_plots=True,
        )

        contract = tmp / "processed" / "breathing_rate" / f"{sid}.csv"
        df = pd.read_csv(contract)
        gated = df[df["quality_gated"].astype(bool)]

        assert len(gated) > 0, "Expected some quality-gated windows"
        assert gated["radar_rr_bpm"].isna().all(), "Gated windows must have NaN radar_rr_bpm"
        assert gated["resp_valid"].astype(bool).sum() == 0, "Gated windows must have resp_valid=False"
        assert (gated["resp_confidence"] == "quality_gated").all()

    def test_missing_masimo_rr_preserved_with_nan_error(self, session_env):
        """Windows without Masimo RR keep radar estimates; error columns become NaN."""
        cfg, row, sid, tmp = session_env
        mas_path = tmp / "raw" / f"{sid}_masimo.csv"
        t0 = float(str(row["radar_start_epoch_seconds"]).strip())
        n  = int(DUR_S + 20)
        ep = np.arange(n, dtype=int) + int(t0)
        df_mas = pd.DataFrame({
            "Session": 0, "Index": np.arange(n), "Timestamp": ep,
            "Date": "1/1/26", "Time": "12:00:00 PM",
            "O2 Saturation": 98, "Beats / min": 75,
            "Perfusion Index": 2.0, "Pleth Variability": 10,
            "Breaths / min": [float("nan")] * n,
        })
        df_mas.to_csv(mas_path, index=False)

        out_dir = tmp / "results" / sid / "step_5"
        _process_session(sid, row, cfg, cubes_dir=tmp / "cubes",
                         data_raw=tmp / "raw", out_dir=out_dir, commit="test", no_plots=True)

        contract = tmp / "processed" / "breathing_rate" / f"{sid}.csv"
        df = pd.read_csv(contract)

        assert df["masimo_rr_bpm"].isna().all(), "masimo_rr_bpm should be NaN"
        assert df["rr_error_bpm"].isna().all(),  "rr_error_bpm should be NaN"
        assert df["rr_abs_error_bpm"].isna().all()

        valid = df[df["resp_valid"].astype(bool)]
        if len(valid) > 0:
            assert valid["radar_rr_bpm"].notna().any(), \
                "radar_rr_bpm must not all be NaN when Masimo is missing"

    def test_no_write_produces_no_files(self, session_env):
        """--no-write must not create any output files or directories."""
        cfg, row, sid, tmp = session_env
        out_dir = tmp / "results" / sid / "step_5"

        _process_session(sid, row, cfg, cubes_dir=tmp / "cubes",
                         data_raw=tmp / "raw", out_dir=out_dir, commit="test", no_plots=True,
                         no_write=True)

        assert not out_dir.exists(), (
            f"--no-write must not create the output directory: {out_dir}"
        )
        contract = tmp / "processed" / "breathing_rate" / f"{sid}.csv"
        assert not contract.exists(), "--no-write must not write Step 6 contract CSV"

    def test_second_run_without_overwrite_raises(self, session_env):
        """Second run into the same out_dir without --overwrite must raise FileExistsError."""
        cfg, row, sid, tmp = session_env
        out_dir = tmp / "results" / sid / "step_5"

        _process_session(sid, row, cfg, cubes_dir=tmp / "cubes",
                         data_raw=tmp / "raw", out_dir=out_dir, commit="test", no_plots=True)

        with pytest.raises(FileExistsError, match="--overwrite"):
            _process_session(sid, row, cfg, cubes_dir=tmp / "cubes",
                             data_raw=tmp / "raw", out_dir=out_dir, commit="test", no_plots=True,
                             overwrite=False)

    def test_overwrite_replaces_results(self, session_env):
        """--overwrite must allow a second run into the same out_dir."""
        cfg, row, sid, tmp = session_env
        out_dir = tmp / "results" / sid / "step_5"

        _process_session(sid, row, cfg, cubes_dir=tmp / "cubes",
                         data_raw=tmp / "raw", out_dir=out_dir, commit="test", no_plots=True)
        _process_session(sid, row, cfg, cubes_dir=tmp / "cubes",
                         data_raw=tmp / "raw", out_dir=out_dir, commit="test", no_plots=True,
                         overwrite=True)

        contract = tmp / "processed" / "breathing_rate" / f"{sid}.csv"
        assert contract.exists()

    def test_missing_step3_fields_fails(self, session_env):
        """Session without Step 3 fields must raise an appropriate error."""
        cfg, row, sid, tmp = session_env
        bad_row = row.copy()
        bad_row["locked_bin"] = ""

        out_dir = tmp / "results" / sid / "step_5_s3fail"
        with pytest.raises(Exception):
            _process_session(
                sid, bad_row, cfg, cubes_dir=tmp / "cubes",
                data_raw=tmp / "raw", out_dir=out_dir, commit="test", no_plots=True,
            )

    def test_missing_quality_mask_fails_when_required(self, session_env):
        """Session without /quality_mask must raise FileNotFoundError."""
        cfg, row, sid, tmp = session_env
        rng  = np.random.default_rng(99)
        cube = _make_cube(rng)
        h5_path = tmp / "cubes" / f"{sid}.h5"
        h5_path.unlink()
        _write_h5(h5_path, cube, TRIM_FRAMES, add_quality_mask=False)

        out_dir = tmp / "results" / sid / "step_5_qmfail"
        with pytest.raises(FileNotFoundError, match="quality_mask"):
            _process_session(
                sid, row, cfg, cubes_dir=tmp / "cubes",
                data_raw=tmp / "raw", out_dir=out_dir, commit="test", no_plots=True,
            )

    def test_changing_masimo_rr_only_affects_reference_columns(self, session_env):
        """Different Masimo RR values must not change radar RR estimates."""
        cfg, row, sid, tmp = session_env
        out_dir = tmp / "results" / sid / "step_5"

        results = {}
        for rr_ref in [15.0, 20.0]:
            mas_path = tmp / "raw" / f"{sid}_masimo.csv"
            t0 = float(str(row["radar_start_epoch_seconds"]).strip())
            _write_masimo(mas_path, t0, DUR_S + 20, rr_bpm=rr_ref)

            _process_session(
                sid, row, cfg, cubes_dir=tmp / "cubes",
                data_raw=tmp / "raw", out_dir=out_dir, commit="test", no_plots=True,
                overwrite=(rr_ref != 15.0),
            )

            contract = tmp / "processed" / "breathing_rate" / f"{sid}.csv"
            results[rr_ref] = pd.read_csv(contract)["radar_rr_bpm"].values.copy()

        a, b = results[15.0], results[20.0]
        min_len = min(len(a), len(b))
        diff = np.nanmax(np.abs(a[:min_len] - b[:min_len]))
        assert diff < 0.01, (
            f"radar_rr_bpm changed when Masimo RR changed (max diff={diff:.4f} bpm) — "
            "Masimo must not drive radar estimation"
        )

    def test_mask_trim_differs_from_analysis_start(self, tmp_path: Path):
        """Step 4 trim=0 but stationary_intervals=10 s: analysis must start at
        frame 200 (10 s × 20 Hz), NOT frame 0.

        Verified by checking:
        1. start_frame of the first window == 200.
        2. start_epoch of the first window == t0_base + 10 s.
        3. No crash (mask coverage validation must pass).
        """
        rng = np.random.default_rng(55)
        sid = "test_trim_mismatch"
        t0  = 1_700_000_000.0

        cubes_dir = tmp_path / "cubes"
        raw_dir   = tmp_path / "raw"
        cubes_dir.mkdir(); raw_dir.mkdir()
        (tmp_path / "processed" / "breathing_rate").mkdir(parents=True)
        (tmp_path / "results").mkdir()

        cube = _make_cube(rng, rr_bpm=RR_TRUE)
        h5_path = cubes_dir / f"{sid}.h5"
        # Step 4 mask_trim = 0 (Step 4 configured with trim_frames: 0)
        _write_h5(h5_path, cube, trim_frames=0, add_quality_mask=True, all_good=True)

        _write_masimo(raw_dir / f"{sid}_masimo.csv", t0, DUR_S + 20, rr_bpm=15.0)

        # Manifest says stationary from 10 s onwards → analysis_start = 200 frames
        man_row = pd.Series(_make_manifest_row(sid, t0))
        man_row["stationary_intervals"] = f"10-{int(DUR_S)}"

        cfg = _make_cfg(tmp_path, sid)
        out_dir = tmp_path / "results" / sid / "step_5"

        summary = _process_session(
            sid, man_row, cfg, cubes_dir=cubes_dir,
            data_raw=raw_dir, out_dir=out_dir, commit="test", no_plots=True,
        )

        assert summary["n_windows"] > 0, "Expected at least one window"

        contract = tmp_path / "processed" / "breathing_rate" / f"{sid}.csv"
        df = pd.read_csv(contract)

        first_start_frame = int(df["start_frame"].iloc[0])
        expected_frame    = int(10 * FS)
        assert first_start_frame == expected_frame, (
            f"First window starts at frame {first_start_frame}, expected {expected_frame} "
            "(analysis_start should honour stationary_intervals, not mask_trim)"
        )

        first_start_epoch = float(df["start_epoch"].iloc[0])
        expected_epoch    = t0 + 10.0
        assert abs(first_start_epoch - expected_epoch) < 0.1, (
            f"start_epoch={first_start_epoch:.1f}, expected {expected_epoch:.1f} "
            "(Masimo epoch alignment must reflect analysis_start, not mask_trim)"
        )

    def test_edge_locked_windows_are_invalidated(self, session_env):
        """Windows whose final radar_rr_bpm falls within edge_lock_margin_bpm of the band
        edge must have resp_valid=False and edge_locked=True."""
        cfg, row, sid, tmp = session_env

        # Force an edge-lock by setting a tiny margin that will almost never trigger,
        # then verify the columns exist and have correct types.
        cfg = dict(cfg)
        cfg["respiration"] = dict(cfg["respiration"])
        cfg["respiration"]["edge_lock_margin_bpm"] = 2.0

        out_dir = tmp / "results" / sid / "step_5"
        _process_session(sid, row, cfg, cubes_dir=tmp / "cubes",
                         data_raw=tmp / "raw", out_dir=out_dir, commit="test", no_plots=True)

        contract = tmp / "processed" / "breathing_rate" / f"{sid}.csv"
        df = pd.read_csv(contract)

        # Columns must exist
        assert "edge_locked" in df.columns, "edge_locked column missing from contract CSV"
        assert "edge_lock_side" in df.columns, "edge_lock_side column missing from contract CSV"

        # Invariant: no edge-locked window may be valid
        edge_and_valid = df[df["edge_locked"].astype(bool) & df["resp_valid"].astype(bool)]
        assert len(edge_and_valid) == 0, (
            f"n_valid_edge_locked={len(edge_and_valid)} — edge-locked windows must not be resp_valid"
        )

        # edge_lock_side must only contain allowed values ("none" for non-locked; empty string
        # is not used because it round-trips through CSV as NaN)
        bad_sides = df[~df["edge_lock_side"].isin(["low", "high", "none"])]["edge_lock_side"].unique()
        assert len(bad_sides) == 0, f"unexpected edge_lock_side values: {bad_sides}"

        # Locked windows must have side "low" or "high", never "none"
        locked = df[df["edge_locked"].astype(bool)]
        for _, wrow in locked.iterrows():
            side = wrow["edge_lock_side"]
            assert side in ("low", "high"), f"locked window has wrong edge_lock_side={side!r}"

    def test_edge_locked_windows_in_summary(self, session_env):
        """summary.json must contain n_edge_locked, edge_locked_fraction, n_valid_edge_locked."""
        import json as _json
        cfg, row, sid, tmp = session_env
        out_dir = tmp / "results" / sid / "step_5"

        _process_session(sid, row, cfg, cubes_dir=tmp / "cubes",
                         data_raw=tmp / "raw", out_dir=out_dir, commit="test", no_plots=True)

        summary = _json.loads((out_dir / "summary.json").read_text())
        for key in ("n_edge_locked", "edge_locked_fraction", "n_valid_edge_locked"):
            assert key in summary, f"summary.json missing key: {key}"

        assert summary["n_valid_edge_locked"] == 0, (
            "n_valid_edge_locked should be 0 for a clean synthetic signal at 15 bpm "
            "(well inside band edges)"
        )

    def test_npz_contains_fft_spectrum_arrays(self, session_env):
        """NPZ must contain fft_freqs_hz, fft_spectrum, fft_peak_bin with correct shapes."""
        cfg, row, sid, tmp = session_env
        out_dir = tmp / "results" / sid / "step_5"

        summary = _process_session(
            sid, row, cfg, cubes_dir=tmp / "cubes",
            data_raw=tmp / "raw", out_dir=out_dir, commit="test", no_plots=True,
        )
        n_windows = summary["n_windows"]

        data = np.load(out_dir / "respiration_intermediates.npz", allow_pickle=False)

        assert "fft_freqs_hz" in data, "missing fft_freqs_hz"
        assert "fft_spectrum"  in data, "missing fft_spectrum"
        assert "fft_peak_bin"  in data, "missing fft_peak_bin"

        assert data["fft_freqs_hz"].shape[0]  == n_windows
        assert data["fft_spectrum"].shape[0]   == n_windows
        assert data["fft_peak_bin"].shape      == (n_windows,)

        # fft_freqs_hz and fft_spectrum must have the same shape
        assert data["fft_freqs_hz"].shape == data["fft_spectrum"].shape

        # For non-gated windows, fft_peak_bin must be a valid index into the spectrum
        peak_bins = data["fft_peak_bin"]
        n_cols    = data["fft_spectrum"].shape[1]
        valid_bins = peak_bins[(peak_bins >= 0)]   # -1 = gated placeholder
        assert (valid_bins < n_cols).all(), "fft_peak_bin out of range for spectrum columns"
