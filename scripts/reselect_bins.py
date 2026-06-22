#!/usr/bin/env python3
"""Reselect range bins for all sessions using energy-based selection.

Runs select_range_bin() on each session's full analysis window (trim_s to end_s)
with a per-session gate of distance_cm/100 ± gate_margin_m, then prints a table
of old vs new bins and writes updated locked_bin values back to the manifest.

Run from repo root:
    python -X utf8 scripts/reselect_bins.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src import radar_io, vitals  # noqa: E402

MANIFEST_PATH = REPO_ROOT / "data" / "manifest.local.csv"
DATA_RAW      = REPO_ROOT / "data" / "raw"
GATE_MARGIN_M = 0.20   # ± metres around tape-measure distance

CHIRP_DEFAULTS = dict(
    num_adc_samples      = 256,
    num_rx               = 4,
    num_tx               = 2,
    num_chirps_per_frame = 32,
    frame_rate_hz        = 20.0,
    range_resolution_m   = 0.0436,
)


def _resolve_bin_paths(session_id: str) -> list[Path]:
    split0 = DATA_RAW / f"{session_id}_0.bin"
    split1 = DATA_RAW / f"{session_id}_1.bin"
    single = DATA_RAW / f"{session_id}.bin"
    if split0.exists():
        return [split0, split1] if split1.exists() else [split0]
    if single.exists():
        return [single]
    raise FileNotFoundError(f"No .bin for '{session_id}' in {DATA_RAW}")


def _total_frames(bin_paths: list[Path], cfg: radar_io.ChirpConfig) -> int:
    bpf = cfg.num_chirps_per_frame * cfg.num_rx * cfg.num_adc_samples * 4
    return sum(p.stat().st_size for p in bin_paths) // bpf


def main() -> None:
    manifest = pd.read_csv(MANIFEST_PATH)
    raxis    = radar_io.range_axis_m(
        CHIRP_DEFAULTS["num_adc_samples"],
        CHIRP_DEFAULTS["range_resolution_m"],
    )

    print(f"\n{'Session':<10}  {'Posture':<22}  {'Dist':>5}  "
          f"{'OldBin':>6}  {'NewBin':>6}  {'NewDist':>8}  {'Changed'}")
    print("-" * 75)

    new_bins: dict[str, int] = {}

    for _, row in manifest.iterrows():
        sid         = str(row["session_id"])
        posture     = str(row["posture"])
        dist_m      = float(row["distance_cm"]) / 100.0
        old_bin     = int(row["locked_bin"])
        interval    = str(row["stationary_intervals"])
        trim_s, end_s = (int(x) for x in interval.split("-"))
        trim_frames = int(trim_s * CHIRP_DEFAULTS["frame_rate_hz"])
        end_frames  = int(end_s  * CHIRP_DEFAULTS["frame_rate_hz"])

        gate = (max(0.0, dist_m - GATE_MARGIN_M), dist_m + GATE_MARGIN_M)

        bin_paths = _resolve_bin_paths(sid)
        cfg = radar_io.ChirpConfig(
            **CHIRP_DEFAULTS,
            num_frames=1,
        )
        num_frames = _total_frames(bin_paths, cfg)
        cfg = radar_io.ChirpConfig(**CHIRP_DEFAULTS, num_frames=num_frames)

        bin_arg = bin_paths[0] if len(bin_paths) == 1 else bin_paths
        cube    = radar_io.read_adc_bin(bin_arg, cfg, trim_frames=trim_frames)
        profiles = radar_io.range_profile(cube)
        del cube

        # Slice to analysis window
        analysis = profiles[trim_frames:end_frames]
        del profiles

        sel     = vitals.select_range_bin(analysis, raxis, gate)
        new_bin = sel["bin_idx"]
        new_dist = sel["range_m"]
        changed = "YES ←" if new_bin != old_bin else ""

        print(f"{sid:<10}  {posture:<22}  {dist_m*100:>5.0f}  "
              f"{old_bin:>6}  {new_bin:>6}  {new_dist:>7.3f}m  {changed}")

        new_bins[sid] = new_bin

    # Update manifest
    manifest["locked_bin"] = manifest["session_id"].map(
        lambda s: new_bins.get(str(s), manifest.loc[manifest["session_id"] == s, "locked_bin"].values[0])
    )
    manifest.to_csv(MANIFEST_PATH, index=False)
    print(f"\nManifest updated -> {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
