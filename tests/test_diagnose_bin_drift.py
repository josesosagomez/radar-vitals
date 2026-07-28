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
WINDOW_S = 30.0


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
        centroid_summary_span_s=10.0,
        require_clean_tree=True,
        preflight_min_available_gb=7.0,
        approved_replays=None,
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


def test_rank_of_bin_in_profile_basic():
    profile = {5: 10.0, 6: 100.0, 7: 50.0}
    assert dbd.rank_of_bin_in_profile(profile, 6) == 1
    assert dbd.rank_of_bin_in_profile(profile, 7) == 2
    assert dbd.rank_of_bin_in_profile(profile, 5) == 3


def test_baseline_rank_of_locked_bin_uses_settled_profile_not_full_buffer_json():
    """BDR-21: a settling-transient fixture where the locked bin's rank
    within the FULL buffer (frames 0-599) genuinely differs from its rank
    within the SETTLED baseline (100-599) -- `baseline_rank_of_locked_bin`
    must be computed from the settled profile `compute_baseline` produces,
    not copied from warmup_bin_selection.json's full-buffer `energy_rank`."""
    # A large-amplitude tone at bin 10 exists ONLY during the settling
    # transient (frames 0-99); bin 8 (the locked bin) carries a much smaller
    # tone throughout frames 100-599 only. Time-averaged over the FULL
    # 600-frame buffer, bin 10's huge amplitude outweighs its fewer frames and
    # outranks bin 8 -- but bin 10 is entirely absent from the SETTLED
    # 100-599 slice, where bin 8 dominates outright.
    rng = np.random.default_rng(0)
    n_adc, n_chirps, n_rx = 32, 2, 2
    cube = (0.01 * (rng.standard_normal((600, n_chirps, n_rx, n_adc))
                    + 1j * rng.standard_normal((600, n_chirps, n_rx, n_adc)))).astype(np.complex64)
    n = np.arange(n_adc)
    for f in range(600):
        k, amp = (10, 50.0) if f < 100 else (8, 5.0)
        tone = amp * np.exp(2j * np.pi * k * n / n_adc)
        cube[f, :, :, :] += tone.astype(np.complex64)

    cfg = diag_cfg()
    baseline = dbd.compute_baseline(cube, CANDIDATE_BINS, cfg)
    assert baseline["baseline_argmax_bin"] == 8

    full = dbd._energy_by_bin_from_slice(cube, 0, 600, CANDIDATE_BINS)
    full_rank_of_8 = dbd.rank_of_bin_in_profile(full, 8)
    settled_rank_of_8 = dbd.rank_of_bin_in_profile(baseline["settled_energy_by_bin"], 8)
    assert settled_rank_of_8 == 1  # bin 8 dominates the settled profile outright
    assert full_rank_of_8 != settled_rank_of_8  # the fixture's whole point (BDR-21)


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


# ── compute_block_series (trailing-block discard; config actually governs) ──

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


def test_block_series_carries_full_per_bin_energy_matrix():
    """The promised per-bin energy matrix (plan §4), not just the derived
    argmax/centroid (BDR-14)."""
    cube = make_cube(600 + 40, bin_by_frame=lambda f: 8, amplitude=5.0)
    cfg = diag_cfg()
    blocks = dbd.compute_block_series(cube, CANDIDATE_BINS, cfg)
    assert blocks.energy_matrix.shape == (2, len(CANDIDATE_BINS))
    assert blocks.candidate_bins == CANDIDATE_BINS
    bin8_col = CANDIDATE_BINS.index(8)
    assert np.all(blocks.energy_matrix[:, bin8_col] == blocks.energy_matrix.max(axis=1))


def test_block_series_fails_closed_on_unsupported_trailing_policy():
    """trailing_block_policy is READ from config and governs behavior, not
    decorative hashing (BDR-19) -- an unsupported value must raise, not
    silently fall back to a default."""
    cube = make_cube(600 + 40, bin_by_frame=lambda f: 8, amplitude=5.0)
    cfg = diag_cfg(trailing_block_policy="weight_partial")
    with pytest.raises(NotImplementedError):
        dbd.compute_block_series(cube, CANDIDATE_BINS, cfg)


# ── detect_episodes ──────────────────────────────────────────────────────────

def _blocks_from_argmax(argmax_seq: list[int], candidate_bins: list[int] = CANDIDATE_BINS) -> dbd.BlockSeries:
    n = len(argmax_seq)
    energy_matrix = np.full((n, len(candidate_bins)), 1.0)
    for i, b in enumerate(argmax_seq):
        j = candidate_bins.index(b)
        energy_matrix[i, j] = 100.0  # the argmax bin clearly dominates
    return dbd.BlockSeries(
        block_start_frame=np.arange(600, 600 + n * BLOCK_FRAMES, BLOCK_FRAMES),
        argmax_bin=np.array(argmax_seq, dtype=int),
        centroid=np.array(argmax_seq, dtype=float),
        energy_matrix=energy_matrix,
        candidate_bins=list(candidate_bins),
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


def test_detect_episodes_fails_closed_on_unsupported_gap_rule():
    """gap_rule is READ from config and governs behavior (BDR-19)."""
    blocks = _blocks_from_argmax([9, 9, 8, 9, 9])
    with pytest.raises(NotImplementedError):
        dbd.detect_episodes(blocks, baseline_bin=8, fs=FS, block_frames=BLOCK_FRAMES,
                             gap_rule="bridge_short_gaps")


def test_episodes_at_grid_boundary_inclusive():
    # A 5-block (5 s) episode must count at the 2s and 5s grid points, and
    # a 1-block (1 s) episode must not count at any grid point.
    blocks = _blocks_from_argmax([9, 9, 9, 9, 9, 8, 9, 8, 8, 8])
    episodes = dbd.detect_episodes(blocks, baseline_bin=8, fs=FS, block_frames=BLOCK_FRAMES)
    grid = dbd.episodes_at_grid(episodes, (2.0, 5.0, 10.0))
    assert grid[2.0] == 1   # only the 5s episode qualifies
    assert grid[5.0] == 1   # exactly at threshold -> counted
    assert grid[10.0] == 0  # neither episode reaches 10s


# ── compute_occupancy ────────────────────────────────────────────────────────

def test_compute_occupancy_fractions():
    # baseline=8; displacements: 0,0,1,1,2,2,3,3 -> 8 blocks
    blocks = _blocks_from_argmax([8, 8, 9, 7, 10, 6, 11, 5])
    occ = dbd.compute_occupancy(blocks, baseline_bin=8)
    assert occ["at_baseline"] == pytest.approx(2 / 8)
    assert occ["within_1_bin"] == pytest.approx(4 / 8)
    assert occ["within_2_bins"] == pytest.approx(6 / 8)
    assert occ["outside_2_bins"] == pytest.approx(2 / 8)


# ── trailing_leading_centroid_medians (BDR-15: exact last-10-complete-blocks,
# not a frame-count threshold that can miss the block grid) ────────────────

def test_trailing_leading_centroid_medians_selects_exact_block_counts():
    # 15 blocks -> last 10 (N=round(10*20/20)=10) for trailing, first 10 for leading.
    seq = list(range(15))  # centroid == argmax bin by construction of the helper
    blocks = _blocks_from_argmax([8] * 15)
    blocks.centroid[:] = seq  # give each block a distinct, known centroid value
    cfg = diag_cfg()
    trailing, leading, n_blocks_used = dbd.trailing_leading_centroid_medians(blocks, cfg, FS)
    assert trailing == pytest.approx(np.median(seq[-10:]))
    assert leading == pytest.approx(np.median(seq[:10]))
    assert n_blocks_used == 10


def test_trailing_leading_centroid_medians_matches_real_capture_arithmetic():
    """Reproduces the exact BDR-15 regression on real capture numbers:
    massimo1 has 3610 total frames (a 10-frame trailing remainder past the
    block grid). A frame-count threshold (`cube.shape[0] - 10*fs`) does not
    land on a block boundary and silently selects only 9 of the last 10
    blocks; selecting the last 10 block-series ENTRIES by position must
    select exactly 10."""
    n_total_frames = 3610
    n_available = n_total_frames - 600
    n_blocks = n_available // BLOCK_FRAMES  # 150, remainder 10 (discarded)
    assert n_blocks == 150
    seq = np.arange(n_blocks, dtype=float)
    blocks = _blocks_from_argmax([8] * n_blocks)
    blocks.centroid[:] = seq
    cfg = diag_cfg()
    trailing, _, n_blocks_used = dbd.trailing_leading_centroid_medians(blocks, cfg, FS)
    # last 10 entries of a 0..149 arange -> 140..149, median 144.5
    assert trailing == pytest.approx(np.median(seq[-10:]))
    assert trailing == pytest.approx(144.5)
    assert n_blocks_used == 10


def test_trailing_leading_centroid_medians_degrades_gracefully_with_few_blocks():
    """BDR-23 R3: with only 3 blocks available against a 10-block requested
    support, `n_blocks_used` must report the ACTUAL count applied (3), not
    the requested 10 -- round 7 returned the requested count unconditionally."""
    blocks = _blocks_from_argmax([8, 9, 10])  # only 3 blocks, fewer than N=10
    cfg = diag_cfg()
    trailing, leading, n_blocks_used = dbd.trailing_leading_centroid_medians(blocks, cfg, FS)
    assert np.isfinite(trailing) and np.isfinite(leading)
    assert n_blocks_used == 3


def test_trailing_leading_centroid_medians_empty_series_is_nan():
    """BDR-23 R3: an empty block series actually uses ZERO blocks -- must
    report 0, not the requested/configured support (round 7 returned 10)."""
    blocks = _blocks_from_argmax([])
    cfg = diag_cfg()
    trailing, leading, n_blocks_used = dbd.trailing_leading_centroid_medians(blocks, cfg, FS)
    assert np.isnan(trailing) and np.isnan(leading)
    assert n_blocks_used == 0


def test_trailing_leading_centroid_medians_support_is_config_bound():
    """BDR-23: `centroid.summary_span_s` must actually govern the number of
    blocks selected, not be a hardcoded 10.0 literal -- mutating the config
    field must change the result."""
    seq = list(range(15))
    blocks = _blocks_from_argmax([8] * 15)
    blocks.centroid[:] = seq

    cfg_10s = diag_cfg(centroid_summary_span_s=10.0)
    trailing_10s, leading_10s, n_10s = dbd.trailing_leading_centroid_medians(blocks, cfg_10s, FS)
    assert trailing_10s == pytest.approx(np.median(seq[-10:]))
    assert leading_10s == pytest.approx(np.median(seq[:10]))
    assert n_10s == 10

    cfg_5s = diag_cfg(centroid_summary_span_s=5.0)
    trailing_5s, leading_5s, n_5s = dbd.trailing_leading_centroid_medians(blocks, cfg_5s, FS)
    assert trailing_5s == pytest.approx(np.median(seq[-5:]))
    assert leading_5s == pytest.approx(np.median(seq[:5]))
    assert trailing_5s != pytest.approx(trailing_10s)
    assert n_5s == 5


# ── BDR-23 R2: positive-span validation and truthful serialized labels ──────

def test_load_diagnostic_config_rejects_non_positive_summary_span(tmp_path: Path):
    bad_cfg_path = tmp_path / "diagnose_bin_drift_config.yaml"
    bad_cfg_path.write_text(
        """
calibration: {stratum_frames: [0, 599], settled_start_frame: 100}
blocks: {block_frames: 20, trailing_block_policy: discard}
episodes: {gap_rule: no_bridging}
centroid: {summary_span_s: 0}
offsets: {phases: [0,1,2,3,4,5,6,7,8,9]}
sensitivity_grid: {duration_s: [2,5,10], centroid_drift_bins: [0.3,0.5,1.0]}
provenance: {require_clean_tree: true}
memory: {preflight_min_available_gb: 0.1}
approved_replays: {}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        dbd.load_diagnostic_config(bad_cfg_path)


def test_load_diagnostic_config_rejects_negative_summary_span(tmp_path: Path):
    bad_cfg_path = tmp_path / "diagnose_bin_drift_config.yaml"
    bad_cfg_path.write_text(
        """
calibration: {stratum_frames: [0, 599], settled_start_frame: 100}
blocks: {block_frames: 20, trailing_block_policy: discard}
episodes: {gap_rule: no_bridging}
centroid: {summary_span_s: -5.0}
offsets: {phases: [0,1,2,3,4,5,6,7,8,9]}
sensitivity_grid: {duration_s: [2,5,10], centroid_drift_bins: [0.3,0.5,1.0]}
provenance: {require_clean_tree: true}
memory: {preflight_min_available_gb: 0.1}
approved_replays: {}
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        dbd.load_diagnostic_config(bad_cfg_path)


def test_trailing_leading_centroid_medians_rejects_span_rounding_to_zero_blocks():
    """BDR-23 R2: a positive but tiny span that rounds to fewer than one
    complete block must raise, not silently select the whole series
    (`centroid[-0:]`) or an empty/NaN slice (`centroid[:0]`)."""
    blocks = _blocks_from_argmax([8] * 15)
    cfg = diag_cfg(centroid_summary_span_s=0.1)  # 0.1*20/20 = 0.1 -> rounds to 0
    with pytest.raises(ValueError):
        dbd.trailing_leading_centroid_medians(blocks, cfg, FS)


# ── centroid_drift_at_grid (a separate SESSION-LEVEL statistic, never a
# per-window joint classifier with the duration grid, BDR-11/BDR-11 R2) ────

def test_centroid_drift_at_grid_below_all_thresholds():
    grid = dbd.centroid_drift_at_grid(trailing_median=24.1, leading_median=24.0,
                                       centroid_grid_bins=(0.3, 0.5, 1.0))
    assert grid == {0.3: False, 0.5: False, 1.0: False}


def test_centroid_drift_at_grid_exact_boundary_counts():
    grid = dbd.centroid_drift_at_grid(trailing_median=24.3, leading_median=24.0,
                                       centroid_grid_bins=(0.3, 0.5, 1.0))
    assert grid[0.3] is True
    assert grid[0.5] is False
    assert grid[1.0] is False


def test_centroid_drift_at_grid_above_all_thresholds():
    grid = dbd.centroid_drift_at_grid(trailing_median=25.5, leading_median=24.0,
                                       centroid_grid_bins=(0.3, 0.5, 1.0))
    assert all(grid.values())


def test_centroid_drift_at_grid_direction_independent():
    forward = dbd.centroid_drift_at_grid(24.0, 24.5, (0.3, 0.5, 1.0))
    backward = dbd.centroid_drift_at_grid(24.5, 24.0, (0.3, 0.5, 1.0))
    assert forward == backward


def test_centroid_drift_at_grid_nan_input_is_false_everywhere():
    grid = dbd.centroid_drift_at_grid(float("nan"), 24.0, (0.3, 0.5, 1.0))
    assert grid == {0.3: False, 0.5: False, 1.0: False}


# ── window classification (BDR-02 R2/R3: fail-closed on evidence the
# strict_v1 producer can never actually emit) ───────────────────────────────

# 0.3 Hz (18 bpm) is a realistic in-physiological-gate respiration rate
# (src/vitals.py: [0.15, 0.60] Hz) -- used wherever a fixture needs a
# LEGITIMATELY accepted window (accepted_rank>=0), since round 8 (BDR-02 R3)
# now enforces that gate for the accepted branch. 1.2 Hz (72 bpm) is
# deliberately OUT of gate, used for the gate-violation test below.
IN_GATE_F_R_HZ = 0.3
OUT_OF_GATE_F_R_HZ = 1.2


def test_classify_window_outcome_covered():
    # Accepted slot 0 is coded PASSED (0); slots 1/2 get real strict_v1 codes
    # (2=ratio_db_low, 5=not_attempted), never -1 -- a strict_v1 row either
    # has ALL codes -1 (gate never ran) or NO -1 anywhere (BDR-25).
    codes = np.array([0, 2, 5])
    assert dbd.classify_window_outcome(0, codes, IN_GATE_F_R_HZ) == "covered"


def test_classify_window_outcome_gate_not_run_nonfinite_f_r_hz():
    codes = np.array([-1, -1, -1])
    assert dbd.classify_window_outcome(-1, codes, float("nan")) == "gate_not_run"


def test_classify_window_outcome_gate_not_run_finite_outlier_f_r_hz():
    """BDR-02 R3: the no-ECA early return (src/vitals.py:523) that produces
    all-not-run rejection codes fires for f_r_hz=None OR a FINITE value
    outside the physiological gate -- not only for non-finite f_r_hz. Real
    data confirms this: massimo1 has 6 such windows (f_r_hz in
    [0.1168, 0.1403], all < the 0.15 Hz gate floor) and sweep has 1
    (f_r_hz=0.1189), all with accepted_rank=-1 and all-not-run codes. Round
    7's classifier required non-finite f_r_hz in addition to all-not-run
    codes, mislabeling these as other_rejected."""
    codes = np.array([-1, -1, -1])
    assert dbd.classify_window_outcome(-1, codes, 0.1168) == "gate_not_run"
    assert dbd.classify_window_outcome(-1, codes, OUT_OF_GATE_F_R_HZ) == "gate_not_run"


def test_classify_window_outcome_other_rejected():
    # No -1 anywhere: an executed gate that rejected every candidate (BDR-25).
    codes = np.array([2, 3, 5])
    assert dbd.classify_window_outcome(-1, codes, IN_GATE_F_R_HZ) == "other_rejected"


def test_classify_window_outcome_rejects_out_of_domain_rank():
    """BDR-02 R2: rank 5 exceeds AHET_MAX_CANDIDATES=3's valid {-1,0,1,2}."""
    codes = np.array([0, 2, 5])
    with pytest.raises(ValueError):
        dbd.classify_window_outcome(5, codes, IN_GATE_F_R_HZ)


def test_classify_window_outcome_rejects_below_domain_rank():
    codes = np.array([-1, -1, -1])
    with pytest.raises(ValueError):
        dbd.classify_window_outcome(-2, codes, float("nan"))


def test_classify_window_outcome_rejects_accepted_with_nonfinite_f_r_hz():
    """BDR-02 R2: accepted_rank>=0 can never legitimately pair with a
    non-finite f_r_hz under strict_v1 -- ECA/AHET only runs when f_r_hz is
    finite; the no-ECA branch that yields non-finite f_r_hz always hardcodes
    accepted_rank=-1."""
    codes = np.array([0, 2, 5])
    with pytest.raises(ValueError):
        dbd.classify_window_outcome(0, codes, float("nan"))


def test_classify_window_outcome_rejects_accepted_with_out_of_gate_finite_f_r_hz():
    """BDR-02 R3: accepted_rank>=0 can also never legitimately pair with a
    FINITE f_r_hz outside the physiological gate -- ECA/AHET only runs when
    f_r_hz passes the gate, so an accepted rank implies f_r_hz was in-gate."""
    codes = np.array([0, 2, 5])
    with pytest.raises(ValueError):
        dbd.classify_window_outcome(0, codes, OUT_OF_GATE_F_R_HZ)


def test_classify_window_outcome_rejects_accepted_with_all_not_run_codes():
    codes = np.array([-1, -1, -1])
    with pytest.raises(ValueError):
        dbd.classify_window_outcome(0, codes, IN_GATE_F_R_HZ)


def test_classify_window_outcome_rejects_accepted_slot_not_coded_passed():
    """The accepted rank's OWN slot must be coded PASSED (0); here rank 1 is
    "accepted" but slot 1's own code is 3 (a rejection code), contradictory.
    Slot 0's code (2) is a real rejection reason, not -1 (BDR-25: no mixing)."""
    codes = np.array([2, 3, 5])
    with pytest.raises(ValueError):
        dbd.classify_window_outcome(1, codes, IN_GATE_F_R_HZ)


def test_classify_window_outcome_rejects_negative_rank_with_passed_code():
    """BDR-02 R3: accepted_rank=-1 can never legitimately pair with a
    'passed' (0) code anywhere in rejection_codes -- a passed slot always
    forces the corresponding non-negative rank to be returned
    (src/vitals.py:941-943). Codes has no -1 (BDR-25: an executed gate never
    leaves a slot at -1), isolating this contradiction from the mixed-codes
    one."""
    codes = np.array([0, 2, 5])
    with pytest.raises(ValueError):
        dbd.classify_window_outcome(-1, codes, IN_GATE_F_R_HZ)


# ── BDR-25: the complete approved strict_v1 row contract ────────────────────

def test_classify_window_outcome_rejects_mixed_rejection_codes():
    """A row with SOME codes -1 and SOME concrete is impossible: strict_v1
    either never runs the gate (all -1) or fully codes every slot once it
    does (never leaves one at -1)."""
    codes = np.array([0, -1, -1])
    with pytest.raises(ValueError):
        dbd.classify_window_outcome(0, codes, IN_GATE_F_R_HZ)


def test_classify_window_outcome_rejects_accepted_rank_not_first_passed_slot():
    """strict_v1 always selects `passed_ranks[0]` (src/vitals.py:941-943) --
    if slot 0 is ALSO coded passed, the accepted rank cannot legitimately be
    1, even though slot 1's own code is correctly 0."""
    codes = np.array([0, 0, 2])
    with pytest.raises(ValueError):
        dbd.classify_window_outcome(1, codes, IN_GATE_F_R_HZ)


def test_classify_window_outcome_rejects_all_not_run_with_in_gate_finite_f_r_hz():
    """An all-not-run row can only legitimately occur when f_r_hz failed the
    physiological gate (None or an outlier) -- ECA/AHET always executes (and
    assigns concrete codes) whenever f_r_hz is finite and in-gate, so this
    combination is impossible."""
    codes = np.array([-1, -1, -1])
    with pytest.raises(ValueError):
        dbd.classify_window_outcome(-1, codes, IN_GATE_F_R_HZ)


# ── validate_frame_idx_grid (BDR-19, BDR-19 R2) ─────────────────────────────

def _outcome_arrays(n: int):
    return (np.full(n, -1, dtype=int), np.full((n, 3), -1, dtype=int), np.full(n, np.nan))


def test_validate_frame_idx_grid_accepts_well_formed_grid():
    frame_idx = np.array([599, 659, 719], dtype=int)
    rank, codes, f_r = _outcome_arrays(3)
    dbd.validate_frame_idx_grid(frame_idx, WINDOW_FRAMES, HOP_S, FS, 720,
                                 rank, codes, f_r)  # must not raise


def test_validate_frame_idx_grid_rejects_below_first_valid_end():
    frame_idx = np.array([500, 659, 719], dtype=int)
    rank, codes, f_r = _outcome_arrays(3)
    with pytest.raises(ValueError):
        dbd.validate_frame_idx_grid(frame_idx, WINDOW_FRAMES, HOP_S, FS, 720, rank, codes, f_r)


def test_validate_frame_idx_grid_rejects_late_first_endpoint():
    """BDR-19 R2: `[659, 719]` (first endpoint ABOVE 599, not just below) must
    also raise -- row 0 would otherwise be mislabeled the warmup window while
    actually spanning frames 60-659, not 0-599."""
    frame_idx = np.array([659, 719], dtype=int)
    rank, codes, f_r = _outcome_arrays(2)
    with pytest.raises(ValueError):
        dbd.validate_frame_idx_grid(frame_idx, WINDOW_FRAMES, HOP_S, FS, 720, rank, codes, f_r)


def test_validate_frame_idx_grid_rejects_non_monotonic():
    frame_idx = np.array([599, 719, 659], dtype=int)
    rank, codes, f_r = _outcome_arrays(3)
    with pytest.raises(ValueError):
        dbd.validate_frame_idx_grid(frame_idx, WINDOW_FRAMES, HOP_S, FS, 1000, rank, codes, f_r)


def test_validate_frame_idx_grid_rejects_wrong_hop_spacing():
    frame_idx = np.array([599, 659, 800], dtype=int)  # last hop is 141 frames, not 60
    rank, codes, f_r = _outcome_arrays(3)
    with pytest.raises(ValueError):
        dbd.validate_frame_idx_grid(frame_idx, WINDOW_FRAMES, HOP_S, FS, 1000, rank, codes, f_r)


def test_validate_frame_idx_grid_rejects_endpoint_beyond_cube():
    """BDR-19 R2: a regularly spaced, correctly anchored grid whose last
    endpoint is at or past the cube's last complete frame must raise --
    NumPy would otherwise silently return a truncated slice for that window."""
    frame_idx = np.array([599, 659, 719], dtype=int)
    rank, codes, f_r = _outcome_arrays(3)
    with pytest.raises(ValueError):
        dbd.validate_frame_idx_grid(frame_idx, WINDOW_FRAMES, HOP_S, FS, 700, rank, codes, f_r)


def test_validate_frame_idx_grid_rejects_short_outcome_array():
    """BDR-19 R2: accepted_candidate_rank with fewer rows than frame_idx must
    raise before any slicing/classification, not fail later by incidental
    indexing."""
    frame_idx = np.array([599, 659, 719], dtype=int)
    _, codes, f_r = _outcome_arrays(3)
    short_rank = np.full(2, -1, dtype=int)
    with pytest.raises(ValueError):
        dbd.validate_frame_idx_grid(frame_idx, WINDOW_FRAMES, HOP_S, FS, 720, short_rank, codes, f_r)


def test_validate_frame_idx_grid_rejects_extra_rejection_code_rows():
    """BDR-19 R2: candidate_rejection_codes with MORE rows than frame_idx must
    raise, not silently ignore the extra rows."""
    frame_idx = np.array([599, 659, 719], dtype=int)
    rank, _, f_r = _outcome_arrays(3)
    extra_codes = np.full((4, 3), -1, dtype=int)
    with pytest.raises(ValueError):
        dbd.validate_frame_idx_grid(frame_idx, WINDOW_FRAMES, HOP_S, FS, 720, rank, extra_codes, f_r)


def test_validate_frame_idx_grid_rejects_short_f_r_hz():
    frame_idx = np.array([599, 659, 719], dtype=int)
    rank, codes, _ = _outcome_arrays(3)
    short_f_r = np.full(1, np.nan)
    with pytest.raises(ValueError):
        dbd.validate_frame_idx_grid(frame_idx, WINDOW_FRAMES, HOP_S, FS, 720, rank, codes, short_f_r)


def test_validate_frame_idx_grid_empty_is_ok():
    rank, codes, f_r = _outcome_arrays(0)
    dbd.validate_frame_idx_grid(np.array([], dtype=int), WINDOW_FRAMES, HOP_S, FS, 0,
                                 rank, codes, f_r)


# ── validate_frame_idx_grid exact-shape checks (BDR-19 R3: row-count alone
# still passed a wrong-width/2-D array) ─────────────────────────────────────

def test_validate_frame_idx_grid_rejects_2d_accepted_rank():
    frame_idx = np.array([599, 659, 719], dtype=int)
    _, codes, f_r = _outcome_arrays(3)
    rank_2d = np.full((3, 1), -1, dtype=int)  # right row count, wrong shape
    with pytest.raises(ValueError):
        dbd.validate_frame_idx_grid(frame_idx, WINDOW_FRAMES, HOP_S, FS, 720, rank_2d, codes, f_r)


def test_validate_frame_idx_grid_rejects_2d_f_r_hz():
    frame_idx = np.array([599, 659, 719], dtype=int)
    rank, codes, _ = _outcome_arrays(3)
    f_r_2d = np.full((3, 1), np.nan)
    with pytest.raises(ValueError):
        dbd.validate_frame_idx_grid(frame_idx, WINDOW_FRAMES, HOP_S, FS, 720, rank, codes, f_r_2d)


def test_validate_frame_idx_grid_rejects_1d_rejection_codes():
    frame_idx = np.array([599, 659, 719], dtype=int)
    rank, _, f_r = _outcome_arrays(3)
    codes_1d = np.full(3, -1, dtype=int)  # missing the per-slot axis entirely
    with pytest.raises(ValueError):
        dbd.validate_frame_idx_grid(frame_idx, WINDOW_FRAMES, HOP_S, FS, 720, rank, codes_1d, f_r)


def test_validate_frame_idx_grid_rejects_wrong_rejection_code_column_count():
    """A (n, 1) or (n, 2) candidate_rejection_codes has the right row count
    but the wrong number of AHET candidate slots (must be
    AHET_MAX_CANDIDATES=3) -- passing this would silently reinterpret which
    slots exist and could change gate_not_run classification."""
    frame_idx = np.array([599, 659, 719], dtype=int)
    rank, _, f_r = _outcome_arrays(3)
    for n_cols in (1, 2, 4):
        codes_wrong_width = np.full((3, n_cols), -1, dtype=int)
        with pytest.raises(ValueError):
            dbd.validate_frame_idx_grid(frame_idx, WINDOW_FRAMES, HOP_S, FS, 720,
                                         rank, codes_wrong_width, f_r)


# ── validate_frame_idx_grid integer-valued/domain checks (BDR-24: exact-shape
# checking alone still let lossy numeric coercion through) ─────────────────

def test_validate_frame_idx_grid_rejects_fractional_frame_idx():
    """BDR-24: [599.9, 659.9] passed shape validation, then was silently
    truncated to [599, 659] by int() downstream instead of failing closed."""
    frame_idx = np.array([599.9, 659.9])
    rank, codes, f_r = _outcome_arrays(2)
    with pytest.raises(ValueError):
        dbd.validate_frame_idx_grid(frame_idx, WINDOW_FRAMES, HOP_S, FS, 720, rank, codes, f_r)


def test_validate_frame_idx_grid_rejects_fractional_accepted_rank():
    frame_idx = np.array([599, 659], dtype=int)
    _, codes, f_r = _outcome_arrays(2)
    rank = np.array([0.9, -1.0])
    with pytest.raises(ValueError):
        dbd.validate_frame_idx_grid(frame_idx, WINDOW_FRAMES, HOP_S, FS, 720, rank, codes, f_r)


def test_validate_frame_idx_grid_rejects_fractional_rejection_codes():
    frame_idx = np.array([599, 659], dtype=int)
    rank, _, f_r = _outcome_arrays(2)
    codes = np.array([[0.9, 2.0, 5.0], [-1, -1, -1]])
    with pytest.raises(ValueError):
        dbd.validate_frame_idx_grid(frame_idx, WINDOW_FRAMES, HOP_S, FS, 720, rank, codes, f_r)


def test_validate_frame_idx_grid_rejects_out_of_domain_rejection_codes():
    """BDR-24: an integer-valued but out-of-domain code (99) must raise --
    the producer's own domain is {-1, 0, ..., 7}."""
    frame_idx = np.array([599, 659], dtype=int)
    rank, _, f_r = _outcome_arrays(2)
    codes = np.array([[99, -1, -1], [-1, -1, -1]], dtype=int)
    with pytest.raises(ValueError):
        dbd.validate_frame_idx_grid(frame_idx, WINDOW_FRAMES, HOP_S, FS, 720, rank, codes, f_r)


def test_validate_frame_idx_grid_rejects_boolean_accepted_rank():
    frame_idx = np.array([599, 659], dtype=int)
    _, codes, f_r = _outcome_arrays(2)
    rank = np.array([True, False])
    with pytest.raises(ValueError):
        dbd.validate_frame_idx_grid(frame_idx, WINDOW_FRAMES, HOP_S, FS, 720, rank, codes, f_r)


def test_validate_frame_idx_grid_does_not_require_f_r_hz_integrality():
    """f_r_hz is a genuine float (a frequency in Hz), never meant to be
    integer-valued -- only frame_idx/accepted_candidate_rank/
    candidate_rejection_codes are checked."""
    frame_idx = np.array([599, 659], dtype=int)
    rank, codes, _ = _outcome_arrays(2)
    f_r = np.array([0.31977403022976636, np.nan])
    dbd.validate_frame_idx_grid(frame_idx, WINDOW_FRAMES, HOP_S, FS, 720,
                                 rank, codes, f_r)  # must not raise


# ── align_windows (window-scale energy computed DIRECTLY on the cube, BDR-14;
# exposure stratification, BDR-03 R3) ───────────────────────────────────────

def _npz_fixture(n_windows: int):
    frame_idx = np.array([599 + i * 60 for i in range(n_windows)], dtype=int)
    accepted_rank = np.full(n_windows, -1, dtype=int)
    rejection_codes = np.full((n_windows, 3), -1, dtype=int)
    f_r_hz = np.full(n_windows, np.nan)
    return frame_idx, accepted_rank, rejection_codes, f_r_hz


def _uniform_cube_for_windows(n_windows: int, bin_val: int = 8) -> np.ndarray:
    """A cube long enough to cover n_windows worth of NPZ hops, with a
    constant tone at bin_val throughout (so direct per-window argmax/centroid
    come out at bin_val, matching a `_blocks_from_argmax([bin_val] * n)`
    block fixture passed alongside it)."""
    last_end_frame = 599 + (n_windows - 1) * 60
    return make_cube(last_end_frame + 1, bin_by_frame=lambda f: bin_val, amplitude=5.0)


def test_align_windows_computes_window_energy_directly_not_from_block_aggregation():
    """BDR-14's core claim, made concrete: construct a 600-frame window where
    the MODE of per-block argmax is bin 8 (16 of 30 blocks), but bin 9's
    aggregate POWER over the whole window is far larger (14 blocks at 3x
    amplitude = 9x power). The window's OWN argmax must be 9 (the true
    aggregate), not 8 (the block-mode shortcut this diagnostic shipped
    with)."""
    n_adc, n_rx, n_chirps = 32, 2, 2
    cube = make_cube(600, n_adc=n_adc, n_rx=n_rx, n_chirps=n_chirps, bin_by_frame=lambda f: None)
    n = np.arange(n_adc)
    for block_i in range(30):
        lo, hi = block_i * BLOCK_FRAMES, (block_i + 1) * BLOCK_FRAMES
        if block_i < 16:
            k, amp = 8, 1.0
        else:
            k, amp = 9, 3.0
        tone = amp * np.exp(2j * np.pi * k * n / n_adc)
        cube[lo:hi, :, :, :] += tone.astype(np.complex64)

    frame_idx = np.array([599], dtype=int)
    accepted_rank = np.array([-1])
    rejection_codes = np.array([[-1, -1, -1]])
    f_r_hz = np.array([np.nan])
    # Blocks passed in still show the mode-losing aggregation (16 blocks @ 8, 14 @ 9)
    blocks = _blocks_from_argmax([8] * 16 + [9] * 14)

    rows = dbd.align_windows(cube, CANDIDATE_BINS, frame_idx, blocks, 8,
                              accepted_rank, rejection_codes, f_r_hz, FS, WINDOW_FRAMES,
                              HOP_S, 600, BLOCK_FRAMES)
    assert rows[0].window_argmax_bin == 9  # NOT 8 (the block-mode shortcut)


def test_align_windows_post_calibration_observed_s():
    n_windows = 12
    frame_idx, accepted_rank, rejection_codes, f_r_hz = _npz_fixture(n_windows)
    cube = _uniform_cube_for_windows(n_windows)
    blocks = _blocks_from_argmax([8] * 30)
    rows = dbd.align_windows(cube, CANDIDATE_BINS, frame_idx, blocks, 8, accepted_rank,
                              rejection_codes, f_r_hz, FS, WINDOW_FRAMES, HOP_S, 600, BLOCK_FRAMES)
    assert rows[0].is_warmup_window is True
    assert rows[0].post_calibration_observed_s == pytest.approx(0.0)
    assert rows[1].post_calibration_observed_s == pytest.approx(3.0)
    assert rows[9].post_calibration_observed_s == pytest.approx(27.0)
    assert rows[10].post_calibration_observed_s == pytest.approx(30.0)
    assert rows[11].post_calibration_observed_s == pytest.approx(30.0)


def test_align_windows_rejects_malformed_frame_idx():
    n_windows = 3
    _, accepted_rank, rejection_codes, f_r_hz = _npz_fixture(n_windows)
    cube = _uniform_cube_for_windows(n_windows)
    blocks = _blocks_from_argmax([8] * 30)
    bad_frame_idx = np.array([100, 659, 719], dtype=int)  # below first valid end
    with pytest.raises(ValueError):
        dbd.align_windows(cube, CANDIDATE_BINS, bad_frame_idx, blocks, 8, accepted_rank,
                           rejection_codes, f_r_hz, FS, WINDOW_FRAMES, HOP_S, 600, BLOCK_FRAMES)


def test_stratify_windows_excludes_warmup_and_splits_transitional():
    n_windows = 12
    frame_idx, accepted_rank, rejection_codes, f_r_hz = _npz_fixture(n_windows)
    cube = _uniform_cube_for_windows(n_windows)
    blocks = _blocks_from_argmax([8] * 30)
    rows = dbd.align_windows(cube, CANDIDATE_BINS, frame_idx, blocks, 8, accepted_rank,
                              rejection_codes, f_r_hz, FS, WINDOW_FRAMES, HOP_S, 600, BLOCK_FRAMES)
    full, transitional = dbd.stratify_windows(rows, WINDOW_S)
    assert all(not r.is_warmup_window for r in full + transitional)
    assert all(r.post_calibration_observed_s >= 30.0 - 1e-9 for r in full)
    assert all(r.post_calibration_observed_s < 30.0 - 1e-9 for r in transitional)
    assert len(full) == 2   # windows 10, 11
    assert len(transitional) == 9  # windows 1-9


def test_transitional_stratum_off_baseline_duration_shape():
    """An off-baseline excursion in the first few post-calibration seconds
    shows up in the transitional stratum's raw duration -- window 1 (3 s
    exposure) sees a much larger share of it than window 9 (27 s exposure),
    even though both saw the identical 1 s episode. Window 10 (the first
    FULL-exposure window) spans exactly the first 30 post-calibration seconds
    by construction (30-block window, 3-block hop), so it legitimately also
    observes this excursion -- "transitional-only" is not a claim this test
    makes (see BDR-03 R3 correction in the plan)."""
    n_windows = 12
    frame_idx, accepted_rank, rejection_codes, f_r_hz = _npz_fixture(n_windows)
    cube = _uniform_cube_for_windows(n_windows)
    # A 1-block (1 s) excursion at block 0, the very first post-calibration block.
    argmax_seq = [9] + [8] * 29
    blocks = _blocks_from_argmax(argmax_seq)
    rows = dbd.align_windows(cube, CANDIDATE_BINS, frame_idx, blocks, 8, accepted_rank,
                              rejection_codes, f_r_hz, FS, WINDOW_FRAMES, HOP_S, 600, BLOCK_FRAMES)
    full, transitional = dbd.stratify_windows(rows, WINDOW_S)

    w1 = next(r for r in transitional if r.window_index == 1)
    assert w1.off_baseline_duration_s == pytest.approx(1.0)

    w9 = next(r for r in transitional if r.window_index == 9)
    assert w9.off_baseline_duration_s == pytest.approx(1.0)

    w10 = next(r for r in full if r.window_index == 10)
    assert w10.off_baseline_duration_s == pytest.approx(1.0)


def test_offset_phase_subsets_all_ten_independent():
    n_windows = 25
    frame_idx, accepted_rank, rejection_codes, f_r_hz = _npz_fixture(n_windows)
    cube = _uniform_cube_for_windows(n_windows)
    blocks = _blocks_from_argmax([8] * 60)
    rows = dbd.align_windows(cube, CANDIDATE_BINS, frame_idx, blocks, 8, accepted_rank,
                              rejection_codes, f_r_hz, FS, WINDOW_FRAMES, HOP_S, 600, BLOCK_FRAMES)
    phases = dbd.offset_phase_subsets(rows, tuple(range(10)))
    assert set(phases.keys()) == set(range(10))
    total = sum(len(v) for v in phases.values())
    assert total == n_windows
    for k, subset in phases.items():
        assert all(r.window_index % 10 == k for r in subset)


# ── window energy npz (BDR-14 R2: the window-scale ordinary energy profile
# align_windows already computes but previously discarded, now persisted) ──

def test_build_window_energy_matrix_shape_and_argmax_agrees_with_row():
    n_adc, n_rx, n_chirps = 32, 2, 2
    cube = make_cube(600, n_adc=n_adc, n_rx=n_rx, n_chirps=n_chirps,
                      bin_by_frame=lambda f: 8, amplitude=5.0)
    frame_idx = np.array([599], dtype=int)
    accepted_rank = np.array([-1])
    rejection_codes = np.array([[-1, -1, -1]])
    f_r_hz = np.array([np.nan])
    blocks = _blocks_from_argmax([8] * 30)
    rows = dbd.align_windows(cube, CANDIDATE_BINS, frame_idx, blocks, 8, accepted_rank,
                              rejection_codes, f_r_hz, FS, WINDOW_FRAMES, HOP_S, 600, BLOCK_FRAMES)
    baseline_by_bin = {b: 1.0 for b in CANDIDATE_BINS}
    window_indices, matrix, matrix_rel_db = dbd.build_window_energy_matrix(
        rows, CANDIDATE_BINS, baseline_by_bin
    )
    assert window_indices.tolist() == [0]
    assert matrix.shape == (1, len(CANDIDATE_BINS))
    assert matrix_rel_db.shape == (1, len(CANDIDATE_BINS))
    bin_idx_of_argmax = CANDIDATE_BINS.index(rows[0].window_argmax_bin)
    assert int(matrix[0].argmax()) == bin_idx_of_argmax


def test_write_window_energy_npz_recomputes_saved_argmax_and_centroid(tmp_path: Path):
    """BDR-14 R2's own WANTED: an artifact-level test that opens the WRITTEN
    file and recomputes every saved window argmax/centroid from the matrix it
    contains -- the whole point of persisting this profile is that it can be
    audited without redecoding the raw capture."""
    n_adc, n_rx, n_chirps = 32, 2, 2
    cube = make_cube(600, n_adc=n_adc, n_rx=n_rx, n_chirps=n_chirps, bin_by_frame=lambda f: None)
    n = np.arange(n_adc)
    for block_i in range(30):
        lo, hi = block_i * BLOCK_FRAMES, (block_i + 1) * BLOCK_FRAMES
        k, amp = (8, 1.0) if block_i < 16 else (9, 3.0)
        tone = amp * np.exp(2j * np.pi * k * n / n_adc)
        cube[lo:hi, :, :, :] += tone.astype(np.complex64)

    frame_idx = np.array([599], dtype=int)
    accepted_rank = np.array([-1])
    rejection_codes = np.array([[-1, -1, -1]])
    f_r_hz = np.array([np.nan])
    blocks = _blocks_from_argmax([8] * 16 + [9] * 14)
    rows = dbd.align_windows(cube, CANDIDATE_BINS, frame_idx, blocks, 8, accepted_rank,
                              rejection_codes, f_r_hz, FS, WINDOW_FRAMES, HOP_S, 600, BLOCK_FRAMES)

    baseline_by_bin = {b: 1.0 for b in CANDIDATE_BINS}
    path = dbd.write_window_energy_npz(tmp_path, rows, CANDIDATE_BINS, baseline_by_bin)
    loaded = np.load(path)
    window_indices, bins, matrix = loaded["window_indices"], loaded["bins"], loaded["matrix"]
    assert "matrix_rel_baseline_db" in loaded

    for i, widx in enumerate(window_indices):
        row = next(r for r in rows if r.window_index == widx)
        recomputed_argmax = int(bins[matrix[i].argmax()])
        recomputed_centroid = float(np.sum(bins * matrix[i]) / np.sum(matrix[i]))
        assert recomputed_argmax == row.window_argmax_bin  # must be 9, not the block-mode 8
        assert recomputed_centroid == pytest.approx(row.window_centroid, rel=1e-9)


# ── stratify_by_outcome (the primary report; normalized fraction, BDR-17) ──

def _window_row(idx, cls, dur, longest, observed_s=30.0):
    # accepted_candidate_rank/rejection_codes/f_r_hz here are placeholders --
    # these tests exercise report functions that operate on the already-
    # classified `outcome_class` string, not `classify_window_outcome` itself.
    return dbd.WindowRow(
        window_index=idx, frame_start=0, frame_end=0, is_warmup_window=False,
        post_calibration_observed_s=observed_s, window_argmax_bin=8, window_centroid=8.0,
        off_baseline_duration_s=dur, longest_excursion_s=longest,
        accepted_candidate_rank=-1, rejection_codes=(-1, -1, -1), f_r_hz=1.2, outcome_class=cls,
    )


def test_stratify_by_outcome_groups_correctly():
    windows = [
        _window_row(10, "covered", 1.0, 1.0),
        _window_row(11, "covered", 3.0, 2.0),
        _window_row(12, "gate_not_run", 5.0, 5.0),
        _window_row(13, "other_rejected", 2.0, 1.0),
    ]
    report = dbd.stratify_by_outcome(windows)
    assert report["covered"]["n"] == 2
    assert report["covered"]["mean_off_baseline_duration_s"] == pytest.approx(2.0)
    assert report["gate_not_run"]["n"] == 1
    assert report["gate_not_run"]["mean_off_baseline_duration_s"] == pytest.approx(5.0)
    assert report["other_rejected"]["n"] == 1


def test_stratify_by_outcome_empty_class_reports_none_not_crash():
    report = dbd.stratify_by_outcome([])
    for cls in dbd.OUTCOME_CLASSES:
        assert report[cls]["n"] == 0
        assert report[cls]["mean_off_baseline_duration_s"] is None
        assert report[cls]["mean_off_baseline_fraction"] is None


def test_stratify_by_outcome_emits_normalized_fraction_for_transitional_windows():
    """BDR-17: the PRODUCTION report function itself must emit a normalized
    fraction for unequal-exposure windows, not just a value a test author can
    compute by hand from two raw fields. Window A: 3 s exposure, 3 s
    off-baseline -> fraction 1.0. Window B: 27 s exposure, 3 s off-baseline
    -> fraction 1/9. Raw means would show 3.0 for both; the fraction must
    differ."""
    windows = [
        _window_row(1, "covered", dur=3.0, longest=3.0, observed_s=3.0),
        _window_row(9, "covered", dur=3.0, longest=3.0, observed_s=27.0),
    ]
    report = dbd.stratify_by_outcome(windows)
    assert report["covered"]["mean_off_baseline_duration_s"] == pytest.approx(3.0)
    # If the fraction field just echoed raw seconds, this would fail.
    expected_fraction = np.mean([3.0 / 3.0, 3.0 / 27.0])
    assert report["covered"]["mean_off_baseline_fraction"] == pytest.approx(expected_fraction)
    assert report["covered"]["mean_off_baseline_fraction"] != pytest.approx(
        report["covered"]["mean_off_baseline_duration_s"]
    )


# ── duration_grid_by_outcome (BDR-11 R2: the per-window duration-grid <->
# outcome association the plan's config comment claims but never computed) ──

def test_duration_grid_by_outcome_counts_per_class():
    windows = [
        _window_row(1, "covered", dur=1.0, longest=1.0),     # below 2s
        _window_row(2, "covered", dur=6.0, longest=6.0),     # above 2s and 5s
        _window_row(3, "gate_not_run", dur=2.0, longest=2.0),  # exactly at 2s
        _window_row(4, "other_rejected", dur=0.5, longest=0.5),  # below everything
    ]
    report = dbd.duration_grid_by_outcome(windows, (2.0, 5.0, 10.0))
    assert report["covered"]["n"] == 2
    assert report["covered"]["count_at_grid"]["2.0"] == 1  # only window 2
    assert report["covered"]["count_at_grid"]["5.0"] == 1
    assert report["covered"]["count_at_grid"]["10.0"] == 0
    assert report["gate_not_run"]["count_at_grid"]["2.0"] == 1  # exact boundary -> counted
    assert report["other_rejected"]["count_at_grid"]["2.0"] == 0


def test_duration_grid_by_outcome_empty_class_is_zero():
    report = dbd.duration_grid_by_outcome([], (2.0, 5.0, 10.0))
    for cls in dbd.OUTCOME_CLASSES:
        assert report[cls]["n"] == 0
        assert all(v == 0 for v in report[cls]["count_at_grid"].values())


# ── motion energy (channel-preserving) ──────────────────────────────────────

def test_motion_energy_stationary_reflector_is_near_zero():
    cube = make_cube(100, bin_by_frame=lambda f: 8, amplitude=5.0)
    me = dbd._motion_energy_slice(cube, CANDIDATE_BINS)
    assert me[8] < 0.01


def test_motion_energy_phase_modulated_reflector_is_elevated_at_that_bin():
    cube = make_cube(200, bin_by_frame=lambda f: 8, amplitude=5.0)
    n = np.arange(cube.shape[-1])
    for f in range(cube.shape[0]):
        mod = 1.0 + 0.5 * np.sin(2 * np.pi * f / 20.0)
        tone = mod * 5.0 * np.exp(2j * np.pi * 8 * n / cube.shape[-1])
        cube[f, :, :, :] = (0.01 * cube[f, :, :, :] / 0.01) * 0  # clear noise for clarity
        cube[f, :, :, :] += tone.astype(np.complex64)
    me = dbd._motion_energy_slice(cube, CANDIDATE_BINS)
    assert me[8] == max(me.values())


def test_motion_energy_channel_preserving_avoids_rx_cancellation():
    """Two RX channels carry the SAME moving-reflector modulation but with
    OPPOSITE static phase. A coherent-average-first formula would cancel
    them; the channel-preserving formula must still detect the motion
    (BDR-09 R2)."""
    n_frames, n_adc, n_rx = 200, 32, 2
    cube = np.zeros((n_frames, 2, n_rx, n_adc), dtype=np.complex64)
    n = np.arange(n_adc)
    static_phase = [0.0, np.pi]
    for f in range(n_frames):
        mod = 1.0 + 0.5 * np.sin(2 * np.pi * f / 20.0)
        base_tone = mod * 5.0 * np.exp(2j * np.pi * 8 * n / n_adc)
        for r in range(n_rx):
            cube[f, :, r, :] = (base_tone * np.exp(1j * static_phase[r])).astype(np.complex64)

    me = dbd._motion_energy_slice(cube, [8])
    assert me[8] > 1.0

    hann = np.hanning(n_adc).astype(np.float32)
    range_fft = np.fft.fft(cube * hann, axis=-1)
    X = range_fft[:, :, :, 8]
    coherent_mean_first = X.mean(axis=(1, 2))
    old_formula_energy = float(np.mean(np.abs(coherent_mean_first - coherent_mean_first.mean()) ** 2))
    assert old_formula_energy < 0.05 * me[8]


def test_motion_energy_gross_amplitude_step_also_elevates_not_breathing_specific():
    n_frames, n_adc = 100, 32
    cube = make_cube(n_frames, n_adc=n_adc, bin_by_frame=lambda f: None)
    n = np.arange(n_adc)
    for f in range(n_frames):
        amp = 1.0 if f < 50 else 10.0
        cube[f, :, :, :] += (amp * np.exp(2j * np.pi * 8 * n / n_adc)).astype(np.complex64)
    me = dbd._motion_energy_slice(cube, CANDIDATE_BINS)
    assert me[8] == max(me.values())
    assert me[8] > 0.5


def test_compute_motion_energy_per_window_indexed_and_bounded():
    """BDR-18: a real (window x bin) matrix, one 600-frame slice at a time,
    not one whole-capture scalar per bin."""
    n_windows = 2
    cube = _uniform_cube_for_windows(n_windows, bin_val=8)
    frame_idx, accepted_rank, rejection_codes, f_r_hz = _npz_fixture(n_windows)
    blocks = _blocks_from_argmax([8] * 30)
    rows = dbd.align_windows(cube, CANDIDATE_BINS, frame_idx, blocks, 8, accepted_rank,
                              rejection_codes, f_r_hz, FS, WINDOW_FRAMES, HOP_S, 600, BLOCK_FRAMES)
    me = dbd.compute_motion_energy_per_window(cube, CANDIDATE_BINS, rows)
    assert set(me.keys()) == {0, 1}
    assert set(me[0].keys()) == set(CANDIDATE_BINS)


def test_compute_motion_energy_per_window_empty_for_no_windows():
    cube = make_cube(600, bin_by_frame=lambda f: 8, amplitude=5.0)
    me = dbd.compute_motion_energy_per_window(cube, CANDIDATE_BINS, [])
    assert me == {}


# ── memory preflight (BDR-18: loaded config value must actually be enforced) ─

def test_preflight_check_memory_passes_when_bound_trivially_low():
    cfg = diag_cfg(preflight_min_available_gb=0.0001)
    # Must not raise -- some available memory certainly exceeds a near-zero bound
    # (or the check degrades to a no-op if it cannot measure at all).
    dbd.preflight_check_memory(cfg, "s1")


def test_preflight_check_memory_raises_when_bound_absurdly_high():
    cfg = diag_cfg(preflight_min_available_gb=1e9)  # 1 exabyte -- no real machine has this
    available = dbd.get_available_memory_bytes()
    if available is None:
        pytest.skip("cannot measure available memory on this platform")
    with pytest.raises(MemoryError):
        dbd.preflight_check_memory(cfg, "s1")


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
    assert cfg.centroid_summary_span_s == 10.0
    # The three 2026-07-26 replays used by the current canonical run (BDR-20).
    assert len(cfg.approved_replays) == 3
    assert all(len(k) == 64 and len(v) == 64 for k, v in cfg.approved_replays.items())
    import hashlib
    assert cfg.sha256 == hashlib.sha256(cfg_path.read_bytes()).hexdigest()


# ── decode geometry validation (BDR-16: capture metadata only) ─────────────

def _matching_live_cfg():
    return {
        "profile": {"num_adc_samples": 256, "num_rx": 4, "num_chirps_per_frame": 32,
                    "range_resolution_m": 0.0436, "iq_swap": True},
        "session": {"frame_rate_hz": 20.0},
    }


def _matching_capture_metadata():
    return {
        "config": {
            "profile": {"num_adc_samples": 256, "num_rx": 4, "num_chirps_per_frame": 32,
                        "range_resolution_m": 0.0436, "iq_swap": True},
            "session": {"frame_rate_hz": 20.0},
            "hw_frame": {"period_ms": 50.0},
        }
    }


def test_validate_decode_geometry_matches_returns_chirp_config():
    cc = dbd.validate_decode_geometry(_matching_live_cfg(), _matching_capture_metadata(), "s1")
    assert cc.num_adc_samples == 256
    assert cc.iq_swap is True
    assert cc.frame_rate_hz == 20.0


def test_validate_decode_geometry_profile_mismatch_raises():
    live_cfg = _matching_live_cfg()
    live_cfg["profile"]["num_rx"] = 2  # diverges from the recorded capture
    with pytest.raises(ValueError):
        dbd.validate_decode_geometry(live_cfg, _matching_capture_metadata(), "s1")


def test_validate_decode_geometry_frame_rate_field_path():
    """frame_rate_hz lives at config.session.frame_rate_hz, NOT config.profile
    (BDR-05 R3 -- the round-1 fix pointed at the wrong path)."""
    meta = _matching_capture_metadata()
    assert "frame_rate_hz" not in meta["config"]["profile"]
    assert meta["config"]["session"]["frame_rate_hz"] == 20.0


def test_validate_decode_geometry_hw_frame_cross_check_inconsistent_raises():
    meta = _matching_capture_metadata()
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


# ── replay-generation binding (BDR-20: raw-hash equality alone does not bind
# which ESTIMATOR GENERATION produced a replay's recorded outcomes) ─────────

def _make_replay(tmp_path: Path, name: str, raw_hash: str, tag: bytes) -> Path:
    """A replay dir with a distinguishable run_metadata.json (so two replays
    of the SAME raw capture still hash to different run_metadata.json SHA-256
    values, as real distinct generations would)."""
    d = tmp_path / name
    d.mkdir()
    (d / "run_metadata.json").write_text(
        json.dumps({"replay_file_hashes": {"path": raw_hash}, "generation_tag": tag.decode()}),
        encoding="utf-8",
    )
    return d


def test_match_replays_to_captures_rejects_duplicate_replay_for_same_capture(tmp_path: Path):
    """BDR-20: two different replay directories both matching the same
    capture's raw hash must raise, never silently keep whichever appears
    last on the CLI (`matched_replay_hashes` was computed but never checked
    before this fix)."""
    cap_a = tmp_path / "cap_a"
    cap_a.mkdir()
    (cap_a / "adc_stream.bin").write_bytes(b"AAAA")
    raw_hash = dbd.sha256_file(cap_a / "adc_stream.bin")

    replay_1 = _make_replay(tmp_path, "replay_gen1", raw_hash, b"gen1")
    replay_2 = _make_replay(tmp_path, "replay_gen2", raw_hash, b"gen2")

    with pytest.raises(ValueError):
        dbd.match_replays_to_captures([cap_a], [replay_1, replay_2])


def test_match_replays_to_captures_rejects_replay_with_no_approval_entry(tmp_path: Path):
    """BDR-20: a capture whose raw hash has NO entry at all in
    `approved_replays` must reject any replay offered for it, not silently
    accept an unregistered generation."""
    cap_a = tmp_path / "cap_a"
    cap_a.mkdir()
    (cap_a / "adc_stream.bin").write_bytes(b"AAAA")
    raw_hash = dbd.sha256_file(cap_a / "adc_stream.bin")
    replay = _make_replay(tmp_path, "replay_1", raw_hash, b"gen1")

    with pytest.raises(ValueError):
        dbd.match_replays_to_captures([cap_a], [replay], approved_replays={})


def test_match_replays_to_captures_rejects_unapproved_generation(tmp_path: Path):
    """BDR-20: a replay matching a capture's raw BYTES but not the approved
    run_metadata.json hash for that capture must be rejected -- proving raw
    equality is not sufficient."""
    cap_a = tmp_path / "cap_a"
    cap_a.mkdir()
    (cap_a / "adc_stream.bin").write_bytes(b"AAAA")
    raw_hash = dbd.sha256_file(cap_a / "adc_stream.bin")
    replay = _make_replay(tmp_path, "replay_wrong_gen", raw_hash, b"gen1")

    approved = {raw_hash: "0" * 64}  # a hash that does NOT match replay's own metadata
    with pytest.raises(ValueError):
        dbd.match_replays_to_captures([cap_a], [replay], approved_replays=approved)


def test_match_replays_to_captures_accepts_approved_generation(tmp_path: Path):
    """BDR-20: the approved-generation check must actually let the correct,
    registered pairing through."""
    cap_a = tmp_path / "cap_a"
    cap_a.mkdir()
    (cap_a / "adc_stream.bin").write_bytes(b"AAAA")
    raw_hash = dbd.sha256_file(cap_a / "adc_stream.bin")
    replay = _make_replay(tmp_path, "replay_approved", raw_hash, b"gen1")
    approved_meta_hash = dbd.sha256_file(replay / "run_metadata.json")

    approved = {raw_hash: approved_meta_hash}
    mapping = dbd.match_replays_to_captures([cap_a], [replay], approved_replays=approved)
    assert mapping[cap_a] == replay


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


# ── end-to-end integration tests (real run_session pipeline) ───────────────

_TINY_ADC = 16
_TINY_RX = 1
_TINY_CHIRPS = 1


def _write_tiny_capture(capture_dir: Path, n_frames: int) -> None:
    """A minimal but real adc_stream.bin -- random int16 words, decodable by
    the project's own read_adc_bin. Content doesn't matter for these tests;
    only that run_session runs the real decode + computation path end to end."""
    capture_dir.mkdir(parents=True)
    rng = np.random.default_rng(1)
    words_per_frame = _TINY_CHIRPS * _TINY_RX * _TINY_ADC * 2
    raw = rng.integers(-1000, 1000, size=n_frames * words_per_frame, dtype="<i2")
    raw.tofile(capture_dir / "adc_stream.bin")


def _tiny_live_cfg() -> dict:
    return {
        "protocol": {"subject_distance_m": [0.8, 1.4]},
        "bin_selection": {"candidate_bins": [3, 4, 5]},
        "profile": {"num_adc_samples": _TINY_ADC, "num_rx": _TINY_RX,
                    "num_chirps_per_frame": _TINY_CHIRPS,
                    "range_resolution_m": 0.0436, "iq_swap": True},
        "session": {"frame_rate_hz": FS, "hop_s": HOP_S, "window_s": WINDOW_S},
    }


def _tiny_capture_metadata(num_rx: int = _TINY_RX) -> dict:
    return {
        "config": {
            "profile": {"num_adc_samples": _TINY_ADC, "num_rx": num_rx,
                        "num_chirps_per_frame": _TINY_CHIRPS,
                        "range_resolution_m": 0.0436, "iq_swap": True},
            "session": {"frame_rate_hz": FS},
            "hw_frame": {"period_ms": 1000.0 / FS},
        }
    }


def _dummy_run_ctx(run_id: str = "20260101T000000Z") -> "dbd.RunContext":
    """A stand-in run manifest (BDR-22) for tests that exercise run_session
    directly without going through main()'s CLI."""
    return dbd.RunContext(
        run_id=run_id,
        git_commit="d" * 40,
        diagnostic_config_path="scripts/diagnose_bin_drift_config.yaml",
        diagnostic_config_sha256="1" * 64,
        live_demo_config_path="scripts/live_demo_config.yaml",
        live_demo_config_sha256="2" * 64,
    )


def test_replayless_session_produces_zero_windows_and_no_outcome_leakage(tmp_path: Path):
    """A session with no matched replay (live_test1's real situation, plan §8
    BDR-07 Option A) must never consume or leak an outcome array -- run
    through the ACTUAL run_session/load_session_inputs code path, not a
    reimplementation of the guarantee."""
    capture_dir = tmp_path / "cap_no_replay"
    n_frames = 660  # 600-frame calibration stratum + 60 post-calibration = 3 blocks
    _write_tiny_capture(capture_dir, n_frames)

    warmup_json = {
        "selected_bin": 4,
        "candidates": [{"bin": b, "energy": 1.0, "energy_rank": i + 1}
                        for i, b in enumerate([3, 4, 5])],
    }
    (capture_dir / "warmup_bin_selection.json").write_text(
        json.dumps(warmup_json), encoding="utf-8"
    )
    (capture_dir / "run_metadata.json").write_text(
        json.dumps(_tiny_capture_metadata()), encoding="utf-8"
    )

    session = dbd.load_session_inputs("cap_no_replay", capture_dir, replay_dir=None)
    assert session.npz is None
    assert session.npz_path is None
    assert session.replay_run_metadata is None
    assert session.replay_run_metadata_path is None

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    cfg = diag_cfg()
    run_ctx = _dummy_run_ctx()
    result = dbd.run_session(session, _tiny_live_cfg(), cfg, run_ctx, out_dir)

    assert result["n_windows"] == 0
    assert result["n_full_exposure_windows"] == 0
    assert result["n_transitional_windows"] == 0
    assert result["correlation_available"] is False
    assert result["npz_path"] is None
    assert result["npz_sha256"] is None
    assert result["replay_run_metadata_path"] is None
    assert result["replay_run_metadata_sha256"] is None

    # BDR-22: the session's OWN summary carries the full run manifest, not
    # just the parent run_summary.json -- independently bound even if this
    # session directory is later cited or copied apart from its parent.
    assert result["run_id"] == run_ctx.run_id
    assert result["git_commit"] == run_ctx.git_commit
    assert result["diagnostic_config_path"] == run_ctx.diagnostic_config_path
    assert result["diagnostic_config_sha256"] == run_ctx.diagnostic_config_sha256
    assert result["live_demo_config_path"] == run_ctx.live_demo_config_path
    assert result["live_demo_config_sha256"] == run_ctx.live_demo_config_sha256

    # BDR-21: the locked bin's rank must come from the settled baseline
    # profile this run computed, not the full-buffer warmup-JSON energy_rank.
    assert result["baseline_rank_of_locked_bin"] == dbd.rank_of_bin_in_profile(
        {int(b): e for b, e in result["baseline_profile"].items()}, result["locked_bin"]
    )
    assert result["full_buffer_warmup_rank_of_locked_bin"] == 2  # bin 4's energy_rank in the fixture JSON

    # BDR-14 R2: the window-scale ordinary energy profile is now persisted
    # separately from motion energy, even for a replay-less (zero-window)
    # session -- an empty (0, n_bins) matrix, not a missing file.
    we = np.load(out_dir / "cap_no_replay" / "window_energy_windows.npz")
    assert we["matrix"].shape == (0, 3)
    for cls in dbd.OUTCOME_CLASSES:
        assert result["outcome_stratified_report"]["full_exposure"][cls]["n"] == 0
        assert result["outcome_stratified_report"]["transitional"][cls]["n"] == 0
    for phase_report in result["offset_phase_report"].values():
        assert phase_report["n_full_exposure_windows"] == 0
        assert phase_report["n_transitional_windows"] == 0

    # The written CSV has a header row only -- no data rows.
    audit_csv = (out_dir / "cap_no_replay" / "window_audit.csv").read_text(encoding="utf-8")
    lines = [ln for ln in audit_csv.splitlines() if ln.strip()]
    assert len(lines) == 1  # header only

    # Occupancy/baseline profile still computed -- these need only raw bytes.
    assert result["occupancy"]["at_baseline"] == result["occupancy"]["at_baseline"]  # not NaN-crash
    assert set(result["baseline_profile"].keys()) == {"3", "4", "5"}


def test_run_session_validates_geometry_against_capture_not_replay_metadata(tmp_path: Path):
    """BDR-16: a replay whose OWN recorded metadata shows a materially
    different (wrong) geometry must not affect the run -- decode-geometry
    validation uses only the capture's metadata, which is correct. The
    replay's raw-file hash still proves which bytes were replayed; its
    config snapshot is not trusted for geometry."""
    capture_dir = tmp_path / "cap"
    n_frames = 660
    _write_tiny_capture(capture_dir, n_frames)
    (capture_dir / "warmup_bin_selection.json").write_text(
        json.dumps({"selected_bin": 4,
                    "candidates": [{"bin": b, "energy": 1.0, "energy_rank": i + 1}
                                    for i, b in enumerate([3, 4, 5])]}),
        encoding="utf-8",
    )
    (capture_dir / "run_metadata.json").write_text(
        json.dumps(_tiny_capture_metadata()), encoding="utf-8"
    )

    replay_dir = tmp_path / "replay"
    replay_dir.mkdir()
    raw_hash = dbd.sha256_file(capture_dir / "adc_stream.bin")
    # The replay's OWN metadata deliberately shows a WRONG num_rx (99) -- if
    # validate_decode_geometry used this instead of the capture's own record,
    # the run would raise. It must not.
    bad_replay_metadata = _tiny_capture_metadata(num_rx=99)
    bad_replay_metadata["replay_file_hashes"] = {"path": raw_hash}
    (replay_dir / "run_metadata.json").write_text(json.dumps(bad_replay_metadata), encoding="utf-8")
    (replay_dir / "warmup_bin_selection.json").write_text(
        json.dumps({"selected_bin": 4,
                    "candidates": [{"bin": b, "energy": 1.0, "energy_rank": i + 1}
                                    for i, b in enumerate([3, 4, 5])]}),
        encoding="utf-8",
    )
    npz_path = replay_dir / "live_intermediates.npz"
    np.savez(npz_path, frame_idx=np.array([599], dtype=int),
              accepted_candidate_rank=np.array([-1]),
              candidate_rejection_codes=np.array([[-1, -1, -1]]),
              f_r_hz=np.array([np.nan]))

    session = dbd.load_session_inputs("cap", capture_dir, replay_dir=replay_dir)
    assert session.replay_run_metadata["config"]["profile"]["num_rx"] == 99
    assert session.capture_run_metadata["config"]["profile"]["num_rx"] == _TINY_RX

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    cfg = diag_cfg()
    # Must NOT raise: geometry validation uses capture_run_metadata (num_rx=1,
    # matching the active config), never the replay's num_rx=99.
    result = dbd.run_session(session, _tiny_live_cfg(), cfg, _dummy_run_ctx(), out_dir)
    assert result["session_id"] == "cap"
    assert result["replay_run_metadata_path"] is not None
    assert result["replay_run_metadata_sha256"] is not None


def test_window_audit_csv_persists_rank_and_outcome_recomputes_from_raw_fields(tmp_path: Path):
    """BDR-02 R2: window_audit.csv must persist accepted_candidate_rank (not
    just the derived outcome_class), and every saved outcome_class must be
    exactly recomputable from that row's own persisted rank/codes/f_r_hz --
    run through the real run_session pipeline end to end, one window per
    outcome class, each satisfying the real strict_v1 producer invariants."""
    capture_dir = tmp_path / "cap_outcomes"
    n_frames = 780
    _write_tiny_capture(capture_dir, n_frames)
    (capture_dir / "warmup_bin_selection.json").write_text(
        json.dumps({"selected_bin": 4,
                    "candidates": [{"bin": b, "energy": 1.0, "energy_rank": i + 1}
                                    for i, b in enumerate([3, 4, 5])]}),
        encoding="utf-8",
    )
    (capture_dir / "run_metadata.json").write_text(
        json.dumps(_tiny_capture_metadata()), encoding="utf-8"
    )

    replay_dir = tmp_path / "replay_outcomes"
    replay_dir.mkdir()
    raw_hash = dbd.sha256_file(capture_dir / "adc_stream.bin")
    replay_metadata = _tiny_capture_metadata()
    replay_metadata["replay_file_hashes"] = {"path": raw_hash}
    (replay_dir / "run_metadata.json").write_text(json.dumps(replay_metadata), encoding="utf-8")
    (replay_dir / "warmup_bin_selection.json").write_text(
        json.dumps({"selected_bin": 4,
                    "candidates": [{"bin": b, "energy": 1.0, "energy_rank": i + 1}
                                    for i, b in enumerate([3, 4, 5])]}),
        encoding="utf-8",
    )
    # Window 0: gate_not_run via non-finite f_r_hz. Window 1: covered (slot 0
    # coded PASSED, in-gate f_r_hz). Window 2: other_rejected. Window 3:
    # gate_not_run via a FINITE respiration value outside the physiological
    # gate (BDR-02 R3 -- 0.12 Hz < the 0.15 Hz gate floor). Each satisfies the
    # real strict_v1 producer invariants BDR-02 R2/R3/BDR-25 now enforce: an
    # executed gate (windows 1, 2) never leaves a slot at -1 (every slot gets
    # 0=passed or a real 1-7 rejection reason, including 5=not_attempted).
    frame_idx = np.array([599, 659, 719, 779], dtype=int)
    accepted_rank = np.array([-1, 0, -1, -1])
    rejection_codes = np.array([
        [-1, -1, -1],
        [0, 2, 5],
        [2, 3, 5],
        [-1, -1, -1],
    ])
    f_r_hz = np.array([np.nan, 0.3, 1.5, 0.12])
    np.savez(replay_dir / "live_intermediates.npz", frame_idx=frame_idx,
             accepted_candidate_rank=accepted_rank,
             candidate_rejection_codes=rejection_codes, f_r_hz=f_r_hz)

    session = dbd.load_session_inputs("cap_outcomes", capture_dir, replay_dir=replay_dir)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    result = dbd.run_session(session, _tiny_live_cfg(), diag_cfg(), _dummy_run_ctx(), out_dir)

    # BDR-22 R2: the exact raw input path, not just its hash.
    assert result["raw_path"] == str(capture_dir / "adc_stream.bin")

    # BDR-23 R2: the WRITTEN summary uses the neutral, truthful field names
    # (not the retired "trailing_10s_median"/"first_post_calibration_10s_median"
    # literals, which would mislabel any non-10s configured span).
    assert result["centroid_drift"]["summary_span_s"] == diag_cfg().centroid_summary_span_s
    assert "n_blocks_used" in result["centroid_drift"]
    assert "trailing_median" in result["centroid_drift"]
    assert "leading_median" in result["centroid_drift"]
    assert "trailing_10s_median" not in result["centroid_drift"]

    import csv
    audit_path = out_dir / "cap_outcomes" / "window_audit.csv"
    with audit_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 4
    assert [r["outcome_class"] for r in rows] == [
        "gate_not_run", "covered", "other_rejected", "gate_not_run",
    ]
    for row in rows:
        rank = int(row["accepted_candidate_rank"])
        codes = np.array([int(c) for c in row["rejection_codes"].split(";")])
        f_r = float(row["f_r_hz"])
        recomputed = dbd.classify_window_outcome(rank, codes, f_r)
        assert recomputed == row["outcome_class"]
