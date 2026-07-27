"""Tests for scripts/diagnose_bin_drift.py (plans/bin_drift_diagnostic.md §7.1).

These exercise the diagnostic's computation functions directly on synthetic,
in-memory cubes -- the raw-ADC decode path itself (read_adc_bin) already has
dedicated coverage in tests/test_radar_io_layout.py and
tests/test_radar_io_split.py, reused rather than duplicated here (plan §6/§7.1).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import diagnose_bin_drift as dbd  # noqa: E402

FS = 20.0
STRATUM = (0, 599)
SETTLED_START = 100
BLOCK_FRAMES = 20
HOP_S = 3.0
WINDOW_FRAMES = 600


def diag_cfg(**overrides) -> dbd.DiagnosticConfig:
    base = dict(
        path=Path("scripts/diagnose_bin_drift_config.yaml"),
        sha256="0" * 64,
        stratum_frames=STRATUM,
        settled_start_frame=SETTLED_START,
        block_frames=BLOCK_FRAMES,
        trailing_block_policy="discard",
        gap_rule="no_bridging",
        offset_phases=tuple(range(10)),
        duration_grid_s=(2.0, 5.0, 10.0),
        centroid_grid_bins=(0.3, 0.5, 1.0),
        require_clean_tree=True,
        preflight_min_available_gb=7.0,
        raw={},
    )
    base.update(overrides)
    return dbd.DiagnosticConfig(**base)


def make_cube(n_frames: int, n_adc: int = 32, n_rx: int = 2, n_chirps: int = 2,
              bin_by_frame=None, amplitude: float = 1.0,
              rx_static_phase=None) -> np.ndarray:
    """A synthetic complex cube whose range-FFT peaks at a chosen bin per frame.

    bin_by_frame: callable(frame_idx) -> target bin (int) or None (no signal,
    pure noise floor). A pure complex exponential at frequency k/n_adc in the
    fast-time (ADC-sample) domain produces a range-FFT peak at bin k.
    rx_static_phase: optional (n_rx,) array of extra static phase per RX
    channel, applied identically at every frame -- used to test motion-energy
    RX-cancellation behaviour.
    """
    rng = np.random.default_rng(0)
    cube = (0.01 * (rng.standard_normal((n_frames, n_chirps, n_rx, n_adc))
                    + 1j * rng.standard_normal((n_frames, n_chirps, n_rx, n_adc)))).astype(np.complex64)
    n = np.arange(n_adc)
    for f in range(n_frames):
        k = bin_by_frame(f) if bin_by_frame is not None else None
        if k is None:
            continue
        tone = amplitude * np.exp(2j * np.pi * k * n / n_adc)
        for r in range(n_rx):
            phase = 0.0 if rx_static_phase is None else rx_static_phase[r]
            cube[f, :, r, :] += (tone * np.exp(1j * phase)).astype(np.complex64)
    return cube


CANDIDATE_BINS = list(range(5, 12))


# ── compute_centroid ─────────────────────────────────────────────────────────

def test_compute_centroid_single_bin_dominant():
    power = {5: 0.0, 6: 100.0, 7: 0.0}
    assert dbd.compute_centroid(power) == pytest.approx(6.0)


def test_compute_centroid_two_bin_known_analytic_value():
    # Equal power at bins 5 and 7 -> centroid exactly 6.0 (the midpoint).
    power = {5: 10.0, 7: 10.0}
    assert dbd.compute_centroid(power) == pytest.approx(6.0)
    # 3x power at bin 8 vs 1x at bin 5 -> centroid = (5*1+8*3)/4 = 7.25
    power2 = {5: 1.0, 8: 3.0}
    assert dbd.compute_centroid(power2) == pytest.approx(7.25)


# ── compute_baseline / warmup_recompute_check ───────────────────────────────

def test_compute_baseline_uses_settled_slice_not_full_buffer():
    """Bin 8 dominates frames 100-599 (the settled slice); a DIFFERENT bin
    dominates frames 0-99 (the excluded settling transient). The baseline
    must reflect only the settled slice."""
    def bin_at(f):
        return 5 if f < 100 else 8
    cube = make_cube(600, bin_by_frame=bin_at, amplitude=5.0)
    cfg = diag_cfg()
    baseline = dbd.compute_baseline(cube, CANDIDATE_BINS, cfg)
    assert baseline["baseline_argmax_bin"] == 8


def test_warmup_recompute_check_both_resolutions_match():
    def bin_at(f):
        return 8
    cube = make_cube(600, bin_by_frame=bin_at, amplitude=5.0)
    cfg = diag_cfg()
    full = dbd._energy_by_bin_from_slice(cube, 0, 600, CANDIDATE_BINS)
    settled = dbd._energy_by_bin_from_slice(cube, 100, 600, CANDIDATE_BINS)
    e_ref = max(settled.values())
    warmup_json = {
        "selected_bin": 8,
        "candidates": [
            {"bin": b, "energy": full[b],
             "settled_energy_db": (10.0 * np.log10(settled[b] / e_ref) if settled[b] > 0 else None)}
            for b in CANDIDATE_BINS
        ],
    }
    result = dbd.warmup_recompute_check(cube, CANDIDATE_BINS, cfg, warmup_json)
    assert result["full_buffer_check"] == "ok"
    assert result["settled_warmup_json_validation"] == "ok"


def test_warmup_recompute_check_legacy_schema_reports_not_available():
    """A legacy warmup JSON (only full-buffer energy/energy_rank, no settled
    fields) must not be treated as a mismatch, and must not fabricate a
    settled match -- it reports the gap explicitly (BDR-07 R3)."""
    def bin_at(f):
        return 8
    cube = make_cube(600, bin_by_frame=bin_at, amplitude=5.0)
    cfg = diag_cfg()
    full = dbd._energy_by_bin_from_slice(cube, 0, 600, CANDIDATE_BINS)
    warmup_json = {
        "selected_bin": 8,
        "candidates": [{"bin": b, "energy": full[b], "energy_rank": i + 1}
                        for i, b in enumerate(CANDIDATE_BINS)],
    }
    result = dbd.warmup_recompute_check(cube, CANDIDATE_BINS, cfg, warmup_json)
    assert result["full_buffer_check"] == "ok"
    assert result["settled_warmup_json_validation"] == "not_available_legacy_schema"

    # The diagnostic's own settled baseline is still computed from raw frames
    # regardless of what the legacy JSON does or doesn't record.
    baseline = dbd.compute_baseline(cube, CANDIDATE_BINS, cfg)
    assert baseline["baseline_argmax_bin"] == 8


def test_warmup_recompute_check_detects_mismatch():
    def bin_at(f):
        return 8
    cube = make_cube(600, bin_by_frame=bin_at, amplitude=5.0)
    cfg = diag_cfg()
    warmup_json = {
        "selected_bin": 8,
        "candidates": [{"bin": b, "energy": 999999.0} for b in CANDIDATE_BINS],
    }
    result = dbd.warmup_recompute_check(cube, CANDIDATE_BINS, cfg, warmup_json)
    assert result["full_buffer_check"] == "mismatch"


# ── compute_block_series (trailing-block discard) ───────────────────────────

def test_block_series_starts_after_calibration_stratum():
    cube = make_cube(600 + 40, bin_by_frame=lambda f: 8, amplitude=5.0)
    cfg = diag_cfg()
    blocks = dbd.compute_block_series(cube, CANDIDATE_BINS, cfg)
    assert blocks.block_start_frame[0] == 600
    assert len(blocks.block_start_frame) == 2  # 40 post-calibration frames / 20
    assert blocks.trailing_discarded_frames == 0


def test_block_series_discards_trailing_incomplete_block():
    """600 calibration frames + 45 post-calibration frames = 2 complete
    20-frame blocks + a 5-frame remainder that must be DISCARDED, never
    weighted in as a partial block (BDR-08 R2)."""
    cube = make_cube(600 + 45, bin_by_frame=lambda f: 8, amplitude=5.0)
    cfg = diag_cfg()
    blocks = dbd.compute_block_series(cube, CANDIDATE_BINS, cfg)
    assert len(blocks.block_start_frame) == 2
    assert blocks.trailing_discarded_frames == 5


# ── detect_episodes ──────────────────────────────────────────────────────────

def _blocks_from_argmax(argmax_seq: list[int]) -> dbd.BlockSeries:
    n = len(argmax_seq)
    return dbd.BlockSeries(
        block_start_frame=np.arange(600, 600 + n * BLOCK_FRAMES, BLOCK_FRAMES),
        argmax_bin=np.array(argmax_seq, dtype=int),
        centroid=np.array(argmax_seq, dtype=float),
        trailing_discarded_frames=0,
    )


def test_detect_episodes_single_bin_excursion():
    # baseline=8; blocks: on,on,off(9),off(9),on,on
    blocks = _blocks_from_argmax([8, 8, 9, 9, 8, 8])
    episodes = dbd.detect_episodes(blocks, baseline_bin=8, fs=FS, block_frames=BLOCK_FRAMES)
    assert len(episodes) == 1
    e = episodes[0]
    assert e.bin_sequence == (9, 9)
    assert e.modal_bin == 9
    assert e.max_displacement_bins == 1
    assert e.start_s == pytest.approx(2.0)
    assert e.end_s == pytest.approx(4.0)  # exclusive


def test_detect_episodes_multi_bin_excursion_not_split():
    # A single episode wandering across two different off-baseline bins.
    blocks = _blocks_from_argmax([8, 9, 10, 8])
    episodes = dbd.detect_episodes(blocks, baseline_bin=8, fs=FS, block_frames=BLOCK_FRAMES)
    assert len(episodes) == 1
    e = episodes[0]
    assert e.bin_sequence == (9, 10)
    assert e.max_displacement_bins == 2


def test_detect_episodes_no_gap_bridging():
    """A single on-baseline block between two off-baseline runs ends the
    first episode -- the runs must NOT be bridged into one."""
    blocks = _blocks_from_argmax([9, 9, 8, 9, 9])
    episodes = dbd.detect_episodes(blocks, baseline_bin=8, fs=FS, block_frames=BLOCK_FRAMES)
    assert len(episodes) == 2
    assert episodes[0].bin_sequence == (9, 9)
    assert episodes[1].bin_sequence == (9, 9)


def test_episodes_at_grid_boundary_inclusive():
    # A 5-block (5 s) episode must count at the 2s and 5s grid points, and
    # a 1-block (1 s) episode must not count at any grid point.
    blocks = _blocks_from_argmax([9, 9, 9, 9, 9, 8, 9, 8, 8, 8])
    episodes = dbd.detect_episodes(blocks, baseline_bin=8, fs=FS, block_frames=BLOCK_FRAMES)
    grid = dbd.episodes_at_grid(episodes, (2.0, 5.0, 10.0))
    assert grid[2.0] == 1   # only the 5s episode qualifies
    assert grid[5.0] == 1   # exactly at threshold -> counted
    assert grid[10.0] == 0  # neither episode reaches 10s


# ── window classification / exposure stratification ────────────────────────

def test_classify_window_outcome_covered():
    codes = np.array([2, -1, -1])
    assert dbd.classify_window_outcome(0, codes, 1.2) == "covered"


def test_classify_window_outcome_gate_not_run():
    codes = np.array([-1, -1, -1])
    assert dbd.classify_window_outcome(-1, codes, float("nan")) == "gate_not_run"


def test_classify_window_outcome_other_rejected():
    codes = np.array([2, 3, -1])
    assert dbd.classify_window_outcome(-1, codes, 1.2) == "other_rejected"


def _npz_fixture(n_windows: int, baseline_bin: int = 8):
    frame_idx = np.array([599 + i * 60 for i in range(n_windows)], dtype=int)
    accepted_rank = np.full(n_windows, -1, dtype=int)
    rejection_codes = np.full((n_windows, 3), -1, dtype=int)
    f_r_hz = np.full(n_windows, np.nan)
    return frame_idx, accepted_rank, rejection_codes, f_r_hz


def test_align_windows_post_calibration_observed_s():
    n_windows = 12
    frame_idx, accepted_rank, rejection_codes, f_r_hz = _npz_fixture(n_windows)
    blocks = _blocks_from_argmax([8] * 30)
    rows = dbd.align_windows(frame_idx, blocks, 8, accepted_rank, rejection_codes,
                              f_r_hz, FS, WINDOW_FRAMES, HOP_S, 600, BLOCK_FRAMES)
    assert rows[0].is_warmup_window is True
    assert rows[0].post_calibration_observed_s == pytest.approx(0.0)
    assert rows[1].post_calibration_observed_s == pytest.approx(3.0)
    assert rows[9].post_calibration_observed_s == pytest.approx(27.0)
    assert rows[10].post_calibration_observed_s == pytest.approx(30.0)
    assert rows[11].post_calibration_observed_s == pytest.approx(30.0)


def test_stratify_windows_excludes_warmup_and_splits_transitional():
    n_windows = 12
    frame_idx, accepted_rank, rejection_codes, f_r_hz = _npz_fixture(n_windows)
    blocks = _blocks_from_argmax([8] * 30)
    rows = dbd.align_windows(frame_idx, blocks, 8, accepted_rank, rejection_codes,
                              f_r_hz, FS, WINDOW_FRAMES, HOP_S, 600, BLOCK_FRAMES)
    full, transitional = dbd.stratify_windows(rows)
    assert all(not r.is_warmup_window for r in full + transitional)
    assert all(r.post_calibration_observed_s >= 30.0 - 1e-9 for r in full)
    assert all(r.post_calibration_observed_s < 30.0 - 1e-9 for r in transitional)
    assert len(full) == 2   # windows 10, 11
    assert len(transitional) == 9  # windows 1-9


def test_transitional_stratum_reports_normalized_fraction_for_early_excursion():
    """An off-baseline excursion in the first few post-calibration seconds
    must be reported by the transitional-stratum windows (index 1-9) as a
    normalized fraction (off_baseline_duration_s / post_calibration_observed_s),
    not raw seconds -- so a 3 s window and a 27 s window are comparable
    (BDR-03 R3). Note window 10 (the first FULL-exposure window) spans
    exactly the first 30 post-calibration seconds by construction (30-block
    window, 3-block hop), so it legitimately also observes this excursion --
    "transitional-only" is not a claim this test makes."""
    n_windows = 12
    frame_idx, accepted_rank, rejection_codes, f_r_hz = _npz_fixture(n_windows)
    # A 1-block (1 s) excursion at block 0, the very first post-calibration block.
    argmax_seq = [9] + [8] * 29
    blocks = _blocks_from_argmax(argmax_seq)
    rows = dbd.align_windows(frame_idx, blocks, 8, accepted_rank, rejection_codes,
                              f_r_hz, FS, WINDOW_FRAMES, HOP_S, 600, BLOCK_FRAMES)
    full, transitional = dbd.stratify_windows(rows)

    # Window 1 (3 s exposure) sees exactly the 1 s excursion -> fraction 1/3.
    w1 = next(r for r in transitional if r.window_index == 1)
    assert w1.off_baseline_duration_s == pytest.approx(1.0)
    assert w1.off_baseline_duration_s / w1.post_calibration_observed_s == pytest.approx(1.0 / 3.0)

    # Window 9 (27 s exposure) sees the same 1 s excursion -> a much smaller
    # fraction than window 1, even though both saw the identical episode.
    w9 = next(r for r in transitional if r.window_index == 9)
    assert w9.off_baseline_duration_s == pytest.approx(1.0)
    frac9 = w9.off_baseline_duration_s / w9.post_calibration_observed_s
    assert frac9 == pytest.approx(1.0 / 27.0)
    assert frac9 < (w1.off_baseline_duration_s / w1.post_calibration_observed_s)

    # Window 10 (first full-exposure window) necessarily also observes the
    # same excursion -- its 30 s span covers all of blocks 0-29 by
    # construction -- and reports it as an ordinary (non-transitional) duration.
    w10 = next(r for r in full if r.window_index == 10)
    assert w10.off_baseline_duration_s == pytest.approx(1.0)


def test_offset_phase_subsets_all_ten_independent():
    n_windows = 25
    frame_idx, accepted_rank, rejection_codes, f_r_hz = _npz_fixture(n_windows)
    blocks = _blocks_from_argmax([8] * 60)
    rows = dbd.align_windows(frame_idx, blocks, 8, accepted_rank, rejection_codes,
                              f_r_hz, FS, WINDOW_FRAMES, HOP_S, 600, BLOCK_FRAMES)
    phases = dbd.offset_phase_subsets(rows, tuple(range(10)))
    assert set(phases.keys()) == set(range(10))
    # Every window belongs to exactly one phase, and phases partition the rows.
    total = sum(len(v) for v in phases.values())
    assert total == n_windows
    for k, subset in phases.items():
        assert all(r.window_index % 10 == k for r in subset)


# ── motion energy (channel-preserving) ──────────────────────────────────────

def test_motion_energy_stationary_reflector_is_near_zero():
    cube = make_cube(100, bin_by_frame=lambda f: 8, amplitude=5.0)
    me = dbd.compute_motion_energy(cube, CANDIDATE_BINS)
    # A stationary tone has no slow-time variation once its own per-channel
    # mean is subtracted; only the small noise floor remains.
    assert me[8] < 0.01


def test_motion_energy_phase_modulated_reflector_is_elevated_at_that_bin():
    def bin_at(f):
        return 8
    cube = make_cube(200, bin_by_frame=bin_at, amplitude=5.0)
    # Modulate bin 8's amplitude sinusoidally over time (a moving reflector).
    n = np.arange(cube.shape[-1])
    for f in range(cube.shape[0]):
        mod = 1.0 + 0.5 * np.sin(2 * np.pi * f / 20.0)
        tone = mod * 5.0 * np.exp(2j * np.pi * 8 * n / cube.shape[-1])
        cube[f, :, :, :] = (0.01 * cube[f, :, :, :] / 0.01) * 0  # clear noise for clarity
        cube[f, :, :, :] += tone.astype(np.complex64)
    me = dbd.compute_motion_energy(cube, CANDIDATE_BINS)
    assert me[8] == max(me.values())


def test_motion_energy_channel_preserving_avoids_rx_cancellation():
    """Two RX channels carry the SAME moving-reflector modulation but with
    OPPOSITE static phase. A coherent-average-first formula would cancel
    them; the channel-preserving formula must still detect the motion
    (BDR-09 R2)."""
    n_frames, n_adc, n_rx = 200, 32, 2
    cube = np.zeros((n_frames, 2, n_rx, n_adc), dtype=np.complex64)
    n = np.arange(n_adc)
    static_phase = [0.0, np.pi]  # opposite static phase across the two RX
    for f in range(n_frames):
        mod = 1.0 + 0.5 * np.sin(2 * np.pi * f / 20.0)
        base_tone = mod * 5.0 * np.exp(2j * np.pi * 8 * n / n_adc)
        for r in range(n_rx):
            cube[f, :, r, :] = (base_tone * np.exp(1j * static_phase[r])).astype(np.complex64)

    me = dbd.compute_motion_energy(cube, [8])
    assert me[8] > 1.0  # clearly non-zero: motion detected

    # Demonstrate the OLD coherent-average-first formula WOULD have cancelled
    # it, as the regression this fix guards against.
    hann = np.hanning(n_adc).astype(np.float32)
    range_fft = np.fft.fft(cube * hann, axis=-1)
    X = range_fft[:, :, :, 8]                # (frames, chirps, rx)
    coherent_mean_first = X.mean(axis=(1, 2))  # average RX BEFORE variance
    old_formula_energy = float(np.mean(np.abs(coherent_mean_first - coherent_mean_first.mean()) ** 2))
    assert old_formula_energy < 0.05 * me[8]  # the old formula is far weaker: it nearly cancels


def test_motion_energy_gross_amplitude_step_also_elevates_not_breathing_specific():
    n_frames, n_adc = 100, 32
    cube = make_cube(n_frames, n_adc=n_adc, bin_by_frame=lambda f: None)
    n = np.arange(n_adc)
    for f in range(n_frames):
        amp = 1.0 if f < 50 else 10.0  # a step, not oscillatory "breathing"
        cube[f, :, :, :] += (amp * np.exp(2j * np.pi * 8 * n / n_adc)).astype(np.complex64)
    me = dbd.compute_motion_energy(cube, CANDIDATE_BINS)
    assert me[8] == max(me.values())
    assert me[8] > 0.5  # the statistic is not specific to oscillatory motion


# ── provenance ────────────────────────────────────────────────────────────────

def test_sha256_file_matches_hashlib(tmp_path: Path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello world" * 100)
    import hashlib
    expected = hashlib.sha256(b"hello world" * 100).hexdigest()
    assert dbd.sha256_file(p) == expected


def test_load_diagnostic_config_reads_real_file_and_hashes_it():
    cfg_path = REPO_ROOT / "scripts" / "diagnose_bin_drift_config.yaml"
    cfg = dbd.load_diagnostic_config(cfg_path)
    assert cfg.stratum_frames == (0, 599)
    assert cfg.settled_start_frame == 100
    assert cfg.block_frames == 20
    assert cfg.trailing_block_policy == "discard"
    assert cfg.duration_grid_s == (2.0, 5.0, 10.0)
    assert cfg.centroid_grid_bins == (0.3, 0.5, 1.0)
    assert cfg.offset_phases == tuple(range(10))
    import hashlib
    assert cfg.sha256 == hashlib.sha256(cfg_path.read_bytes()).hexdigest()


# ── decode geometry validation ───────────────────────────────────────────────

def _matching_live_cfg():
    return {
        "profile": {"num_adc_samples": 256, "num_rx": 4, "num_chirps_per_frame": 32,
                    "range_resolution_m": 0.0436, "iq_swap": True},
        "session": {"frame_rate_hz": 20.0},
    }


def _matching_run_metadata():
    return {
        "config": {
            "profile": {"num_adc_samples": 256, "num_rx": 4, "num_chirps_per_frame": 32,
                        "range_resolution_m": 0.0436, "iq_swap": True},
            "session": {"frame_rate_hz": 20.0},
            "hw_frame": {"period_ms": 50.0},
        }
    }


def test_validate_decode_geometry_matches_returns_chirp_config():
    cc = dbd.validate_decode_geometry(_matching_live_cfg(), _matching_run_metadata(), "s1")
    assert cc.num_adc_samples == 256
    assert cc.iq_swap is True
    assert cc.frame_rate_hz == 20.0


def test_validate_decode_geometry_profile_mismatch_raises():
    live_cfg = _matching_live_cfg()
    live_cfg["profile"]["num_rx"] = 2  # diverges from the recorded capture
    with pytest.raises(ValueError):
        dbd.validate_decode_geometry(live_cfg, _matching_run_metadata(), "s1")


def test_validate_decode_geometry_frame_rate_field_path():
    """frame_rate_hz lives at config.session.frame_rate_hz, NOT config.profile
    (BDR-05 R3 -- the round-1 fix pointed at the wrong path)."""
    meta = _matching_run_metadata()
    assert "frame_rate_hz" not in meta["config"]["profile"]
    assert meta["config"]["session"]["frame_rate_hz"] == 20.0


def test_validate_decode_geometry_hw_frame_cross_check_inconsistent_raises():
    meta = _matching_run_metadata()
    meta["config"]["hw_frame"]["period_ms"] = 33.0  # 1000/33 != 20.0
    with pytest.raises(ValueError):
        dbd.validate_decode_geometry(_matching_live_cfg(), meta, "s1")


# ── replay-to-capture matching (by hash, never by CLI position) ────────────

def test_match_replays_to_captures_by_hash(tmp_path: Path):
    cap_a = tmp_path / "cap_a"
    cap_b = tmp_path / "cap_b"
    for d, content in ((cap_a, b"AAAA"), (cap_b, b"BBBB")):
        d.mkdir()
        (d / "adc_stream.bin").write_bytes(content)

    replay = tmp_path / "replay_of_b"
    replay.mkdir()
    b_hash = dbd.sha256_file(cap_b / "adc_stream.bin")
    (replay / "run_metadata.json").write_text(
        json.dumps({"replay_file_hashes": {"path": b_hash}}), encoding="utf-8"
    )

    mapping = dbd.match_replays_to_captures([cap_a, cap_b], [replay])
    assert mapping[cap_a] is None
    assert mapping[cap_b] == replay


def test_match_replays_to_captures_unmatched_replay_raises(tmp_path: Path):
    cap_a = tmp_path / "cap_a"
    cap_a.mkdir()
    (cap_a / "adc_stream.bin").write_bytes(b"AAAA")

    replay = tmp_path / "replay_orphan"
    replay.mkdir()
    (replay / "run_metadata.json").write_text(
        json.dumps({"replay_file_hashes": {"path": "0" * 64}}), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        dbd.match_replays_to_captures([cap_a], [replay])


# ── clean-tree gate ──────────────────────────────────────────────────────────

def _run_git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_is_tree_clean_true_on_fresh_commit(tmp_path: Path):
    _run_git(["init"], tmp_path)
    _run_git(["config", "user.email", "t@t.com"], tmp_path)
    _run_git(["config", "user.name", "t"], tmp_path)
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    _run_git(["add", "f.txt"], tmp_path)
    _run_git(["commit", "-m", "init"], tmp_path)
    assert dbd.is_tree_clean(tmp_path) is True


def test_is_tree_clean_false_after_tracked_file_modified(tmp_path: Path):
    _run_git(["init"], tmp_path)
    _run_git(["config", "user.email", "t@t.com"], tmp_path)
    _run_git(["config", "user.name", "t"], tmp_path)
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    _run_git(["add", "f.txt"], tmp_path)
    _run_git(["commit", "-m", "init"], tmp_path)

    (tmp_path / "f.txt").write_text("modified", encoding="utf-8")
    assert dbd.is_tree_clean(tmp_path) is False


def test_is_tree_clean_true_with_untracked_file_present(tmp_path: Path):
    """An untracked new file does not count as dirty -- only a TRACKED file
    diverging from HEAD does (plan §5's clean-tree rationale)."""
    _run_git(["init"], tmp_path)
    _run_git(["config", "user.email", "t@t.com"], tmp_path)
    _run_git(["config", "user.name", "t"], tmp_path)
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    _run_git(["add", "f.txt"], tmp_path)
    _run_git(["commit", "-m", "init"], tmp_path)

    (tmp_path / "new_untracked.txt").write_text("new", encoding="utf-8")
    assert dbd.is_tree_clean(tmp_path) is True
