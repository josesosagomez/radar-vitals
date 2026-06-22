"""Tests for experiments/exp004_window_length/analysis.py."""

import hashlib
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
EXP004_CONFIG = REPO_ROOT / "experiments" / "exp004_window_length" / "config.yaml"

from experiments.exp004_window_length.analysis import (
    chair_condition_summary,
    collect_provenance,
    masimo_summary,
    pooled_window_length_summary,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_cap_results(ev20, ev25, ev30):
    """Build a minimal cap_results entry from three error vectors."""
    def _pc(ev):
        arr = np.array(ev, dtype=float)
        return {
            "error_vector": list(arr),
            "mae": float(np.mean(np.abs(arr))),
            "rmse": float(np.sqrt(np.mean(arr ** 2))),
            "bias": float(np.mean(arr)),
            "n_ahet": len(ev),
        }

    return {
        "paired": {
            "intersection": {
                "per_condition": {
                    "20s": _pc(ev20),
                    "25s": _pc(ev25),
                    "30s": _pc(ev30),
                }
            }
        },
        "per_condition": {},
    }


def _make_chair_cap(mae, rmse, bias, wls=("20s", "25s", "30s")):
    """Minimal _run_capture() return for chair_condition_summary tests."""
    pc = {
        wl: {
            "n_total": 19,
            "n_finite": 18,
            "n_nan_radar": 1,
            "n_nan_ref": 0,
            "n_ahet": 15,
            "n_f_r_outlier": 2,
            "n_harmonic_suspect": 1,
            "mae": float(mae),
            "rmse": float(rmse),
            "bias": float(bias),
        }
        for wl in wls
    }
    return {"per_condition": pc, "paired": {}}


# ---------------------------------------------------------------------------
# pooled_window_length_summary
# ---------------------------------------------------------------------------

def test_pooled_micro_average_correct():
    cap_results = {
        "cap1": _make_cap_results([1.0, -2.0, 3.0], [0.5, -1.0, 1.5], [0.2, -0.4, 0.8]),
        "cap2": _make_cap_results([2.0, -1.0, 4.0], [1.0, -0.5, 2.0], [0.3, -0.6, 0.9]),
    }
    result = pooled_window_length_summary(cap_results)

    ev_20_all = [1.0, -2.0, 3.0, 2.0, -1.0, 4.0]
    expected_mae = float(np.mean(np.abs(ev_20_all)))

    micro = result["per_window_length"]["20s"]["micro"]
    assert abs(micro["mae"] - expected_mae) < 1e-10
    assert micro["n"] == 6


def test_pooled_macro_average_correct():
    # cap1 20s errors: [1, -2, 3] → MAE 2.0
    # cap2 20s errors: [2, -1, 4] → MAE 7/3
    cap_results = {
        "cap1": _make_cap_results([1.0, -2.0, 3.0], [0.5, -1.0, 1.5], [0.2, -0.4, 0.8]),
        "cap2": _make_cap_results([2.0, -1.0, 4.0], [1.0, -0.5, 2.0], [0.3, -0.6, 0.9]),
    }
    result = pooled_window_length_summary(cap_results)

    mae_cap1 = float(np.mean(np.abs([1.0, -2.0, 3.0])))  # 2.0
    mae_cap2 = float(np.mean(np.abs([2.0, -1.0, 4.0])))  # 7/3
    expected_macro_mae = (mae_cap1 + mae_cap2) / 2

    macro = result["per_window_length"]["20s"]["macro"]
    assert abs(macro["mae_mean"] - expected_macro_mae) < 1e-10


def test_pooled_trend_monotonic():
    # 20s: large errors → high MAE
    # 25s: medium errors → medium MAE
    # 30s: small errors → low MAE
    cap_results = {
        "cap1": _make_cap_results(
            [5.0, -5.0, 6.0],   # MAE 16/3 ≈ 5.33
            [3.0, -3.0, 4.0],   # MAE 10/3 ≈ 3.33
            [1.0, -1.0, 2.0],   # MAE 4/3 ≈ 1.33
        ),
    }
    result = pooled_window_length_summary(cap_results)

    mae_20 = result["per_window_length"]["20s"]["micro"]["mae"]
    mae_25 = result["per_window_length"]["25s"]["micro"]["mae"]
    mae_30 = result["per_window_length"]["30s"]["micro"]["mae"]
    assert mae_20 > mae_25 > mae_30

    assert result["trend"]["micro"] == "monotonic_improvement"


def test_pooled_trend_no_improvement():
    # MAE worsens monotonically: 20s < 25s < 30s → no improvement
    cap_results = {
        "cap1": _make_cap_results(
            [1.0, -1.0],   # MAE 1.0
            [2.0, -2.0],   # MAE 2.0
            [3.0, -3.0],   # MAE 3.0
        ),
    }
    result = pooled_window_length_summary(cap_results)

    mae_20 = result["per_window_length"]["20s"]["micro"]["mae"]
    mae_30 = result["per_window_length"]["30s"]["micro"]["mae"]
    assert mae_30 > mae_20

    assert result["trend"]["micro"] == "no_improvement"


# ---------------------------------------------------------------------------
# chair_condition_summary
# ---------------------------------------------------------------------------

def test_chair_condition_summary_structure():
    cap2 = _make_chair_cap(mae=5.0, rmse=7.0, bias=-2.0)
    cap3 = _make_chair_cap(mae=4.5, rmse=6.5, bias=-1.5)
    result = chair_condition_summary(cap2, cap3)

    assert "per_window_length" in result
    assert set(result["per_window_length"].keys()) == {"20s", "25s", "30s"}
    for wl in ("20s", "25s", "30s"):
        entry = result["per_window_length"][wl]
        assert "cap2" in entry
        assert "cap3" in entry
        assert "difference_cap2_minus_cap3" in entry
    assert "confounds" in result


def test_chair_condition_confounds_present():
    cap2 = _make_chair_cap(mae=5.0, rmse=7.0, bias=-2.0)
    cap3 = _make_chair_cap(mae=4.5, rmse=6.5, bias=-1.5)
    result = chair_condition_summary(cap2, cap3)

    confounds = result["confounds"]
    assert any("locked_bin_range_m differs: cap2=1.439m vs cap3=1.308m" in c for c in confounds)
    assert any("no causal inference about chair condition is supported" in c for c in confounds)


# ---------------------------------------------------------------------------
# masimo_summary
# ---------------------------------------------------------------------------

def _masimo_df(epochs, prs, brs, pis):
    return pd.DataFrame({"epoch": epochs, "PR": prs, "BR": brs, "PI": pis})


def test_masimo_summary_unique_samples():
    # 5 rows, epochs 101 and 102 each appear twice → 3 unique
    df = _masimo_df(
        epochs=[100, 101, 101, 102, 102],
        prs=[70.0, 72.0, 75.0, 68.0, 69.0],
        brs=[14.0, 15.0, 16.0, 13.0, 14.0],
        pis=[2.0, 2.0, 1.5, 2.0, 1.8],
    )
    result = masimo_summary(df, trim_start_epoch=100, trim_end_epoch=103, min_pi=0.5)
    assert result["n_unique_samples"] == 3


def test_masimo_summary_pi_filter():
    # 3 unique samples: PI at epoch 101 is 0.3 (below min_pi=0.5)
    df = _masimo_df(
        epochs=[100, 101, 102],
        prs=[70.0, 72.0, 68.0],
        brs=[14.0, 15.0, 13.0],
        pis=[2.0, 0.3, 2.0],
    )
    result = masimo_summary(df, trim_start_epoch=100, trim_end_epoch=103, min_pi=0.5)

    assert result["n_pi_filtered"] == 1
    # PR mean from surviving samples: epoch 100 (70.0) and epoch 102 (68.0)
    expected_pr_mean = (70.0 + 68.0) / 2
    assert abs(result["pr"]["mean"] - expected_pr_mean) < 1e-10


def test_masimo_summary_empty_after_filter():
    # All 3 samples below min_pi → NaN metrics, but no exception raised
    df = _masimo_df(
        epochs=[100, 101, 102],
        prs=[70.0, 72.0, 68.0],
        brs=[14.0, 15.0, 13.0],
        pis=[0.1, 0.2, 0.3],
    )
    result = masimo_summary(df, trim_start_epoch=100, trim_end_epoch=103, min_pi=0.5)

    assert result["n_unique_samples"] == 3
    assert result["n_pi_filtered"] == 3
    assert math.isnan(result["pr"]["mean"])


# ---------------------------------------------------------------------------
# collect_provenance
# ---------------------------------------------------------------------------

def test_provenance_required_fields(tmp_path):
    cfg = {"seed": 0, "data": {"bin_file": "data/raw/test.bin"}}
    result = collect_provenance(cfg, tmp_path, source_files=[])

    required_keys = {
        "git_commit", "git_dirty", "git_diff", "git_untracked",
        "file_hashes", "python_version", "numpy_version",
        "scipy_version", "timestamp_iso", "config",
    }
    assert required_keys.issubset(result.keys())


def test_provenance_file_hash_correct(tmp_path):
    content = b"radar-vitals provenance test content"
    src = tmp_path / "source.py"
    src.write_bytes(content)

    expected_sha256 = hashlib.sha256(content).hexdigest()
    result = collect_provenance({}, tmp_path, source_files=[src])

    assert result["file_hashes"][str(src)] == expected_sha256


def test_provenance_missing_file_recorded(tmp_path):
    missing = tmp_path / "does_not_exist.py"
    result = collect_provenance({}, tmp_path, source_files=[missing])

    assert result["file_hashes"][str(missing)] == "missing"


# ---------------------------------------------------------------------------
# config.yaml — multi-capture structure
# ---------------------------------------------------------------------------

_REQUIRED_CAPTURE_FIELDS = {
    "id", "bin_file", "masimo_file", "logfile",
    "locked_bin", "locked_bin_range_m", "expected_frames",
    "session_date", "chair_condition", "recording_order",
}


def test_exp004_config_loads_three_captures():
    cfg = yaml.safe_load(EXP004_CONFIG.read_text())

    assert len(cfg["captures"]) == 3
    assert [c["id"] for c in cfg["captures"]] == ["cap1", "cap2", "cap3"]
    for cap in cfg["captures"]:
        missing = _REQUIRED_CAPTURE_FIELDS - set(cap.keys())
        assert not missing, f"Capture {cap.get('id')!r} missing fields: {sorted(missing)}"


def test_exp004_bytes_per_frame_derived_correctly():
    cfg = yaml.safe_load(EXP004_CONFIG.read_text())
    r = cfg["radar"]
    # num_tx is excluded — num_chirps_per_frame already encodes the full per-frame
    # chirp count as stored in the bin file, matching infer_num_frames() in radar_io.py
    bytes_per_frame = r["num_chirps_per_frame"] * r["num_rx"] * r["num_adc_samples"] * 4
    assert bytes_per_frame == 131072


# ---------------------------------------------------------------------------
# Integration tests (skipped by default; activate with --run-dir or EXP004_RUN_DIR)
# ---------------------------------------------------------------------------

_CAP_EXPECTED_COUNTS = {
    "cap1": {"baseline_20s": 21, "condition_20s": 19, "condition_25s": 19, "condition_30s": 19},
    "cap2": {"baseline_20s": 39, "condition_20s": 37, "condition_25s": 37, "condition_30s": 37},
    "cap3": {"baseline_20s": 39, "condition_20s": 37, "condition_25s": 37, "condition_30s": 37},
}


def _read_csv(path: Path) -> "pd.DataFrame":
    df = pd.read_csv(path)
    for col in ("low_quality", "ahet_verified"):
        if col in df.columns and df[col].dtype == object:
            df[col] = df[col].map(lambda x: str(x).strip().lower() == "true")
    return df


def test_exp004_all_artifacts_exist(run_dir):
    subdirs = ("baseline_20s", "condition_20s", "condition_25s", "condition_30s")
    for cap_id in ("cap1", "cap2", "cap3"):
        cap_d = run_dir / cap_id
        assert (cap_d / "paired_summary.json").is_file(), f"Missing {cap_id}/paired_summary.json"
        for sub in subdirs:
            assert (cap_d / sub / "comparison.csv").is_file(), f"Missing {cap_id}/{sub}/comparison.csv"
            assert (cap_d / sub / "intermediates.npz").is_file(), f"Missing {cap_id}/{sub}/intermediates.npz"
    for fname in ("window_length_summary.json", "chair_condition_summary.json",
                  "provenance.json", "stdout.log"):
        assert (run_dir / fname).is_file(), f"Missing {fname}"


def test_exp004_cap1_baseline_metrics_match_canonical(run_dir):
    import sys, importlib
    sys.path.insert(0, str(REPO_ROOT))
    cmp = importlib.import_module("src.compare")

    df = _read_csv(run_dir / "cap1" / "baseline_20s" / "comparison.csv")
    m_all = cmp.metrics(df)
    assert m_all["n_windows"] == 21
    assert abs(m_all["mae_bpm"]  - 5.1887797747) < 1e-4, f"MAE {m_all['mae_bpm']}"
    assert abs(m_all["rmse_bpm"] - 6.9021357101) < 1e-4, f"RMSE {m_all['rmse_bpm']}"
    assert abs(m_all["bias_bpm"] - (-2.2281949852)) < 1e-4, f"bias {m_all['bias_bpm']}"

    verified = df[df["ahet_verified"].astype(bool)].copy()
    m_ver = cmp.metrics(verified)
    assert m_ver["n_windows"] == 20
    assert abs(m_ver["mae_bpm"]  - 5.3257187634) < 1e-4, f"Verified MAE {m_ver['mae_bpm']}"
    assert abs(m_ver["rmse_bpm"] - 7.0513350671) < 1e-4, f"Verified RMSE {m_ver['rmse_bpm']}"
    assert abs(m_ver["bias_bpm"] - (-2.2171047344)) < 1e-4, f"Verified bias {m_ver['bias_bpm']}"


def test_exp004_condition_window_counts(run_dir):
    for cap_id, counts in _CAP_EXPECTED_COUNTS.items():
        for subdir, n_expected in counts.items():
            df = pd.read_csv(run_dir / cap_id / subdir / "comparison.csv")
            assert len(df) == n_expected, (
                f"{cap_id}/{subdir}: expected {n_expected} rows, got {len(df)}"
            )


def test_exp004_npz_row_counts_match_csv(run_dir):
    for cap_id in ("cap1", "cap2", "cap3"):
        for subdir in ("baseline_20s", "condition_20s", "condition_25s", "condition_30s"):
            path = run_dir / cap_id / subdir
            df = pd.read_csv(path / "comparison.csv")
            with np.load(path / "intermediates.npz", allow_pickle=False) as archive:
                npz_n = len(archive["window_index"])
            assert npz_n == len(df), (
                f"{cap_id}/{subdir}: NPZ={npz_n} rows vs CSV={len(df)} rows"
            )


def test_exp004_paired_summary_json_structure(run_dir):
    import json
    for cap_id in ("cap1", "cap2", "cap3"):
        data = json.loads((run_dir / cap_id / "paired_summary.json").read_text())
        assert "all_centers" in data, f"{cap_id}: missing all_centers"
        assert "per_condition" in data, f"{cap_id}: missing per_condition"
        assert "intersection" in data, f"{cap_id}: missing intersection"
        assert set(data["per_condition"].keys()) == {"20s", "25s", "30s"}, (
            f"{cap_id}: per_condition keys = {set(data['per_condition'].keys())}"
        )
        assert "pairwise" in data["intersection"], f"{cap_id}: missing intersection.pairwise"


def test_exp004_window_length_summary_structure(run_dir):
    import json
    data = json.loads((run_dir / "window_length_summary.json").read_text())
    assert "per_window_length" in data
    assert "trend" in data
    assert set(data["per_window_length"].keys()) == {"20s", "25s", "30s"}
    for wl, v in data["per_window_length"].items():
        assert "micro" in v, f"{wl}: missing micro"
        assert "macro" in v, f"{wl}: missing macro"
        assert "per_capture" in v, f"{wl}: missing per_capture"
    assert "micro" in data["trend"]
    valid_trends = {"monotonic_improvement", "partial_improvement", "no_improvement"}
    assert data["trend"]["micro"] in valid_trends, (
        f"trend.micro={data['trend']['micro']!r} not in {valid_trends}"
    )


def test_exp004_chair_condition_summary_structure(run_dir):
    import json
    data = json.loads((run_dir / "chair_condition_summary.json").read_text())
    assert "per_window_length" in data
    assert "confounds" in data
    assert set(data["per_window_length"].keys()) == {"20s", "25s", "30s"}
    for wl, v in data["per_window_length"].items():
        assert "cap2" in v, f"{wl}: missing cap2"
        assert "cap3" in v, f"{wl}: missing cap3"
    assert len(data["confounds"]) > 0, "confounds list is empty"
    assert any("no causal inference about chair condition is supported" in c
               for c in data["confounds"]), "expected causal-inference disclaimer in confounds"


def test_exp004_provenance_structure(run_dir):
    import json
    data = json.loads((run_dir / "provenance.json").read_text())
    required = {"git_commit", "git_dirty", "git_diff", "git_untracked",
                "file_hashes", "python_version", "numpy_version",
                "scipy_version", "timestamp_iso", "config"}
    missing = required - set(data.keys())
    assert not missing, f"provenance.json missing keys: {sorted(missing)}"
    assert len(data["file_hashes"]) > 0, "file_hashes is empty"
