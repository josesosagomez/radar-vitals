"""Tests for exp004 window-length study — windowing, paired metrics, and run artifacts."""

import json
import random
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src.compare import paired_metrics
from src.windowing import common_center_windows, sliding_windows

REPO_ROOT = Path(__file__).resolve().parents[1]
EXP004_CONFIG = REPO_ROOT / "experiments" / "exp004_window_length" / "config.yaml"


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _load_cfg() -> dict:
    return yaml.safe_load(EXP004_CONFIG.read_text())


def _num_frames(cfg: dict) -> int:
    r = cfg["radar"]
    # num_tx is not included in bytes_per_frame — matches infer_num_frames() in radar_io.py
    bytes_per_frame = r["num_chirps_per_frame"] * r["num_rx"] * r["num_adc_samples"] * 4
    # bin_file is now per-capture; use cap1 (index 0) for windowing grid tests
    bin_path = REPO_ROOT / cfg["captures"][0]["bin_file"]
    return bin_path.stat().st_size // bytes_per_frame


def _window_params(cfg: dict) -> dict:
    """Derive integer frame counts from config for the common-center and baseline grids."""
    fps = cfg["radar"]["frame_rate_hz"]
    trim_frames = int(cfg["processing"]["trim_s"] * fps)
    return {
        "trim_frames": trim_frames,
        "num_frames": _num_frames(cfg),
        "window_lengths_frames": [int(s * fps) for s in cfg["exp004"]["window_lengths_s"]],
        "center_spacing_frames": int(cfg["exp004"]["center_spacing_s"] * fps),
        # first_center_s is relative to end of trim; add trim_frames for absolute index
        "first_center_offset_frames": trim_frames + int(cfg["exp004"]["first_center_s"] * fps),
        "baseline_window_frames": int(cfg["exp004"]["baseline_window_s"] * fps),
        "baseline_hop_frames": int(cfg["exp004"]["baseline_hop_s"] * fps),
    }


def _latest_exp004_dir() -> Path:
    base = REPO_ROOT / "results" / "exp004_window_length"
    candidates = sorted(
        p for p in base.iterdir()
        if p.is_dir()
        and len(p.name) == 15
        and p.name[8] == "_"
        and p.name.replace("_", "").isdigit()
        and (p / "cap1" / "paired_summary.json").is_file()
        and (p / "cap1" / "baseline_20s" / "comparison.csv").is_file()
    )
    assert candidates, "No exp004 result directory with required artifacts."
    return candidates[-1]


def _read_comparison_csv(path: Path) -> pd.DataFrame:
    """Read comparison CSV, converting bool-like string columns to actual bool dtype."""
    df = pd.read_csv(path)
    for col in ("low_quality", "ahet_verified"):
        if col in df.columns and df[col].dtype == object:
            df[col] = df[col].map(lambda x: str(x).strip().lower() == "true")
    return df


# ---------------------------------------------------------------------------
# Tests 1–4: window grid properties (no pipeline execution)
# ---------------------------------------------------------------------------

def test_exp004_config_produces_19_common_centers():
    p = _window_params(_load_cfg())
    wins = common_center_windows(
        total_frames=p["num_frames"],
        trim_frames=p["trim_frames"],
        window_lengths_frames=p["window_lengths_frames"],
        center_spacing_frames=p["center_spacing_frames"],
        first_center_offset_frames=p["first_center_offset_frames"],
    )
    for wl, windows in wins.items():
        assert len(windows) == 19, f"Expected 19 windows for {wl} frames, got {len(windows)}"
    # All three window lengths produce the same count
    assert len({len(w) for w in wins.values()}) == 1


def test_exp004_config_produces_21_baseline_windows():
    p = _window_params(_load_cfg())
    wins = sliding_windows(
        p["num_frames"], p["trim_frames"],
        p["baseline_window_frames"], p["baseline_hop_frames"],
    )
    assert len(wins) == 21
    starts = [s for s, _ in wins]
    expected = list(range(p["trim_frames"], p["trim_frames"] + 21 * p["baseline_hop_frames"], p["baseline_hop_frames"]))
    assert starts == expected


def test_exp004_common_centers_within_bounds():
    p = _window_params(_load_cfg())
    wins = common_center_windows(
        total_frames=p["num_frames"],
        trim_frames=p["trim_frames"],
        window_lengths_frames=p["window_lengths_frames"],
        center_spacing_frames=p["center_spacing_frames"],
        first_center_offset_frames=p["first_center_offset_frames"],
    )
    for wl, windows in wins.items():
        for abs_start, abs_end in windows:
            assert abs_start >= p["trim_frames"], (
                f"wl={wl}: window [{abs_start},{abs_end}] starts before trim_frames={p['trim_frames']}"
            )
            assert abs_end <= p["num_frames"], (
                f"wl={wl}: window [{abs_start},{abs_end}] ends after num_frames={p['num_frames']}"
            )


def test_exp004_baseline_vs_common_center_grids_are_separate():
    p = _window_params(_load_cfg())
    common_wins = common_center_windows(
        total_frames=p["num_frames"],
        trim_frames=p["trim_frames"],
        window_lengths_frames=p["window_lengths_frames"],
        center_spacing_frames=p["center_spacing_frames"],
        first_center_offset_frames=p["first_center_offset_frames"],
    )
    first_wl = p["window_lengths_frames"][0]
    center_frames = {(s + e) // 2 for s, e in common_wins[first_wl]}

    baseline_wins = sliding_windows(
        p["num_frames"], p["trim_frames"],
        p["baseline_window_frames"], p["baseline_hop_frames"],
    )
    baseline_start_frames = {s for s, _ in baseline_wins}

    assert center_frames != baseline_start_frames


# ---------------------------------------------------------------------------
# Tests 5–7: paired_metrics correctness (synthetic data only)
# ---------------------------------------------------------------------------

def _make_cond_19(radar_hr_base: float, ahet: bool = True) -> dict[int, dict]:
    """Synthetic condition with 19 centers at [900, 1000, …, 2700]."""
    centers = list(range(900, 2800, 100))
    return {
        cf: {
            "radar_hr": float(radar_hr_base + i),
            "masimo_pr": 70.0,
            "error": float("nan"),
            "ahet_verified": ahet,
        }
        for i, cf in enumerate(centers)
    }


def test_exp004_paired_metrics_three_conditions():
    conditions = {
        "20s": _make_cond_19(70.0),
        "25s": _make_cond_19(71.0),
        "30s": _make_cond_19(72.0),
    }
    result = paired_metrics(conditions, "20s")

    assert result["intersection"]["n_centers"] == 19
    pairwise = result["intersection"]["pairwise"]
    assert len(pairwise) == 3
    assert set(pairwise.keys()) == {"20s_vs_25s", "20s_vs_30s", "25s_vs_30s"}
    for pw in pairwise.values():
        t = pw["ahet_transitions"]
        total = t["both_pass"] + t["only_a_passes"] + t["only_b_passes"] + t["neither_passes"]
        assert total == 19


def test_exp004_paired_metrics_nan_drops_from_intersection():
    centers = list(range(900, 2800, 100))  # 19 centers

    def good(i: int) -> dict:
        return {"radar_hr": 71.0 + i, "masimo_pr": 70.0, "error": float("nan"), "ahet_verified": True}

    nan_entry = {"radar_hr": float("nan"), "masimo_pr": 70.0, "error": float("nan"), "ahet_verified": False}

    conditions = {
        "20s": {cf: good(i) for i, cf in enumerate(centers)},
        "25s": {
            centers[0]: nan_entry,
            **{cf: good(i) for i, cf in enumerate(centers[1:], start=1)},
        },
        "30s": {cf: good(i) for i, cf in enumerate(centers)},
    }
    result = paired_metrics(conditions, "20s")

    assert result["intersection"]["n_centers"] == 18


def test_exp004_paired_metrics_ahet_transitions_sum_to_intersection():
    rng = random.Random(42)
    centers = list(range(900, 2800, 100))

    def make_rand_cond() -> dict[int, dict]:
        return {
            cf: {
                "radar_hr": 70.0,
                "masimo_pr": 70.0,
                "error": float("nan"),
                "ahet_verified": rng.choice([True, False]),
            }
            for cf in centers
        }

    conditions = {"20s": make_rand_cond(), "25s": make_rand_cond(), "30s": make_rand_cond()}
    result = paired_metrics(conditions, "20s")

    n = result["intersection"]["n_centers"]
    for pw in result["intersection"]["pairwise"].values():
        t = pw["ahet_transitions"]
        total = t["both_pass"] + t["only_a_passes"] + t["only_b_passes"] + t["neither_passes"]
        assert total == n


# ---------------------------------------------------------------------------
# Tests 8–12: artifact checks (require a completed exp004 run)
# ---------------------------------------------------------------------------

def test_exp004_artifacts_exist_after_run():
    run_dir = _latest_exp004_dir()
    # per-capture artifacts for cap1 (canonical capture)
    assert (run_dir / "cap1" / "paired_summary.json").is_file()
    for subdir in ("baseline_20s", "condition_20s", "condition_25s", "condition_30s"):
        assert (run_dir / "cap1" / subdir / "comparison.csv").is_file(), f"Missing cap1/{subdir}/comparison.csv"
        assert (run_dir / "cap1" / subdir / "intermediates.npz").is_file(), f"Missing cap1/{subdir}/intermediates.npz"
    # cross-capture files at root
    for fname in ("window_length_summary.json", "chair_condition_summary.json",
                  "provenance.json", "stdout.log"):
        assert (run_dir / fname).is_file(), f"Missing {fname}"


def test_exp004_baseline_metrics_match_canonical():
    """Baseline 20 s grid must reproduce exp002 canonical metrics to within 1e-4 bpm.

    Canonical updated 2026-06-15 after parabolic_interpolate_peak was wired into the
    no-ECA fallback path (Blocker 4 fix). Only window 10 (f_r outlier) is affected;
    the AHET-verified subset is unchanged.
    """
    from src import compare as cmp

    run_dir = _latest_exp004_dir()
    df = _read_comparison_csv(run_dir / "cap1" / "baseline_20s" / "comparison.csv")

    m_all = cmp.metrics(df)
    assert m_all["n_windows"] == 21
    assert abs(m_all["mae_bpm"]  - 5.1720981083) < 1e-4, f"MAE {m_all['mae_bpm']}"
    assert abs(m_all["rmse_bpm"] - 6.8966354865) < 1e-4, f"RMSE {m_all['rmse_bpm']}"
    assert abs(m_all["bias_bpm"] - (-2.2115133188)) < 1e-4, f"bias {m_all['bias_bpm']}"

    # AHET-verified subset (metrics function also drops low_quality within the subset)
    verified = df[df["ahet_verified"]].copy()
    m_ver = cmp.metrics(verified)
    assert m_ver["n_windows"] == 20
    assert abs(m_ver["mae_bpm"]  - 5.3257187634) < 1e-4, f"Verified MAE {m_ver['mae_bpm']}"
    assert abs(m_ver["rmse_bpm"] - 7.0513350671) < 1e-4, f"Verified RMSE {m_ver['rmse_bpm']}"
    assert abs(m_ver["bias_bpm"] - (-2.2171047344)) < 1e-4, f"Verified bias {m_ver['bias_bpm']}"


def test_exp004_condition_window_counts():
    run_dir = _latest_exp004_dir()
    expected = {
        "baseline_20s": 21,
        "condition_20s": 19,
        "condition_25s": 19,
        "condition_30s": 19,
    }
    for subdir, n_expected in expected.items():
        df = pd.read_csv(run_dir / "cap1" / subdir / "comparison.csv")
        assert len(df) == n_expected, f"cap1/{subdir}: expected {n_expected} rows, got {len(df)}"


def test_exp004_npz_row_counts_match_csv():
    run_dir = _latest_exp004_dir()
    for subdir in ("baseline_20s", "condition_20s", "condition_25s", "condition_30s"):
        df = pd.read_csv(run_dir / "cap1" / subdir / "comparison.csv")
        with np.load(run_dir / "cap1" / subdir / "intermediates.npz", allow_pickle=False) as archive:
            npz_n = len(archive["window_index"])
            assert npz_n == len(df), f"cap1/{subdir}: NPZ={npz_n} rows vs CSV={len(df)} rows"


def test_exp004_paired_summary_json_structure():
    run_dir = _latest_exp004_dir()
    with open(run_dir / "cap1" / "paired_summary.json") as f:
        data = json.load(f)

    assert "all_centers" in data
    assert "per_condition" in data
    assert "intersection" in data
    assert set(data["per_condition"].keys()) == {"20s", "25s", "30s"}
    assert "pairwise" in data["intersection"]
