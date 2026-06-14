"""End-to-end consistency checks for the latest exp002 result artifacts."""
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


def _latest_exp002_result() -> Path:
    results_root = REPO_ROOT / "results" / "exp002_harmonic_rejection"
    candidates = sorted(
        path
        for path in results_root.iterdir()
        if path.is_dir()
        and len(path.name) == 15
        and path.name[8] == "_"
        and path.name.replace("_", "").isdigit()
        and (path / "intermediates.npz").is_file()
        and (path / "comparison.csv").is_file()
        and (path / "config_used.yaml").is_file()
    )
    assert candidates, "No timestamped exp002 result contains the required artifacts."
    return candidates[-1]


def _analysis_cube_frames(result_dir: Path) -> int:
    config = yaml.safe_load((result_dir / "config_used.yaml").read_text())
    chirp = config["chirp"]
    data = config["data"]
    radar_path = REPO_ROOT / data["radar_bin"]

    bytes_per_frame = (
        chirp["num_chirps_per_frame"]
        * chirp["num_rx"]
        * chirp["num_adc_samples"]
        * 4
    )
    file_size = radar_path.stat().st_size
    assert file_size % bytes_per_frame == 0

    captured_frames = file_size // bytes_per_frame
    trim_frames = int(data["trim_start_s"] * chirp["frame_rate_hz"])
    return captured_frames - trim_frames


def test_latest_exp002_npz_matches_comparison_csv():
    result_dir = _latest_exp002_result()
    comparison = pd.read_csv(result_dir / "comparison.csv")
    total_frames = _analysis_cube_frames(result_dir)

    with np.load(result_dir / "intermediates.npz", allow_pickle=False) as archive:
        row_count = len(comparison)
        assert len(archive["window_index"]) == row_count
        assert row_count > 0
        np.testing.assert_array_equal(
            archive["ahet_verified"],
            comparison["ahet_verified"].to_numpy(dtype=bool),
        )

        assert np.all(archive["start_frame"] >= 0)
        assert np.all(archive["end_frame"] <= total_frames)

        for row_index in range(row_count):
            attempted = archive["candidate_attempted"][row_index]
            attempted_count = int(np.count_nonzero(attempted))
            expected_attempted = (
                np.arange(attempted.shape[0]) < attempted_count
            )
            np.testing.assert_array_equal(attempted, expected_attempted)

            if archive["ahet_verified"][row_index]:
                assert archive["accepted_candidate_rank"][row_index] == (
                    attempted_count - 1
                )
            else:
                assert archive["accepted_candidate_rank"][row_index] == -1
