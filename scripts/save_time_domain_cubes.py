#!/usr/bin/env python3
"""Save radar time-domain cubes to HDF5 for downstream processing.

Reads each session from data/manifest.local.csv, decodes the raw .bin into a
complex radar cube (time domain), and writes it to:
    data/processed/time_domain_cubes/<session_id>.h5

The post-FFT sanity print inside read_adc_bin() runs automatically and serves
as the decode validation step.

HDF5 layout
-----------
/cube               complex64 dataset, shape (frames, chirps, rx, adc_samples)
                    = (N, 32, 4, 256)  — canonical shape, no transpose applied
Root attributes     session metadata + chirp config + provenance

Run from repo root:
    python -X utf8 scripts/save_time_domain_cubes.py --session exp003
    python -X utf8 scripts/save_time_domain_cubes.py --all
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src import radar_io  # noqa: E402

MANIFEST_PATH  = REPO_ROOT / "data" / "manifest.local.csv"
CONFIG_PATH    = REPO_ROOT / "experiments" / "exp_eca_all" / "config.yaml"
OUT_DIR        = REPO_ROOT / "data" / "processed" / "time_domain_cubes"


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def _resolve_bin_paths(session_id: str, data_raw: Path) -> list[Path]:
    split0 = data_raw / f"{session_id}_0.bin"
    split1 = data_raw / f"{session_id}_1.bin"
    single = data_raw / f"{session_id}.bin"
    if split0.exists():
        return [split0, split1] if split1.exists() else [split0]
    if single.exists():
        return [single]
    raise FileNotFoundError(f"No .bin for '{session_id}' in {data_raw}")


def _total_frames(bin_paths: list[Path], cfg: radar_io.ChirpConfig) -> int:
    bpf = cfg.num_chirps_per_frame * cfg.num_rx * cfg.num_adc_samples * 4
    total = sum(p.stat().st_size for p in bin_paths)
    if total % bpf != 0:
        raise ValueError(
            f"Total bytes {total} not divisible by bytes_per_frame {bpf} "
            f"for {[p.name for p in bin_paths]}"
        )
    return total // bpf


def _save_session(row: pd.Series, r: dict, data_raw: Path, commit: str) -> Path:
    session_id  = str(row["session_id"])
    posture     = str(row["posture"])
    dist_cm     = int(row["distance_cm"])
    t0          = int(row["radar_start_epoch_seconds"])
    interval    = str(row["stationary_intervals"])
    orientation = str(row["radar_orientation"])

    bin_paths = _resolve_bin_paths(session_id, data_raw)

    chirp_cfg = radar_io.ChirpConfig(
        num_adc_samples      = r["num_adc_samples"],
        num_rx               = r["num_rx"],
        num_tx               = r["num_tx"],
        num_chirps_per_frame = r["num_chirps_per_frame"],
        num_frames           = 1,           # placeholder; overwritten below
        frame_rate_hz        = r["frame_rate_hz"],
        range_resolution_m   = r["range_resolution_m"],
    )
    num_frames = _total_frames(bin_paths, chirp_cfg)
    chirp_cfg = radar_io.ChirpConfig(
        num_adc_samples      = r["num_adc_samples"],
        num_rx               = r["num_rx"],
        num_tx               = r["num_tx"],
        num_chirps_per_frame = r["num_chirps_per_frame"],
        num_frames           = num_frames,
        frame_rate_hz        = r["frame_rate_hz"],
        range_resolution_m   = r["range_resolution_m"],
    )

    print(f"  Source: {[p.name for p in bin_paths]}")
    print(f"  Frames: {num_frames}  ({num_frames / r['frame_rate_hz']:.1f} s)")

    bin_arg = bin_paths[0] if len(bin_paths) == 1 else bin_paths
    # read_adc_bin runs post-FFT sanity print internally — decode validation
    cube = radar_io.read_adc_bin(bin_arg, chirp_cfg, trim_frames=0)
    # cube shape: (frames, chirps, rx, adc_samples) = (N, 32, 4, 256)
    # canonical time-domain shape — no transpose applied here

    out_path = OUT_DIR / f"{session_id}.h5"
    with h5py.File(out_path, "w") as f:
        # Main dataset — chunked along frame axis for efficient sequential access
        chunk = (min(100, num_frames), r["num_chirps_per_frame"],
                 r["num_rx"], r["num_adc_samples"])
        ds = f.create_dataset(
            "cube",
            data=cube,
            chunks=chunk,
            compression="gzip",
            compression_opts=4,
        )
        ds.attrs["axes"] = "frames, chirps_per_frame, rx, adc_samples"
        ds.attrs["dtype"] = "complex64"

        # Session metadata
        f.attrs["session_id"]               = session_id
        f.attrs["posture"]                  = posture
        f.attrs["possible_distance_cm"]     = dist_cm
        f.attrs["radar_start_epoch_seconds"] = t0
        f.attrs["stationary_intervals"]     = interval
        f.attrs["radar_orientation"]        = orientation

        # Chirp config
        f.attrs["num_adc_samples"]          = r["num_adc_samples"]
        f.attrs["num_rx"]                   = r["num_rx"]
        f.attrs["num_tx"]                   = r["num_tx"]
        f.attrs["num_chirps_per_frame"]     = r["num_chirps_per_frame"]
        f.attrs["frame_rate_hz"]            = r["frame_rate_hz"]
        f.attrs["range_resolution_m"]       = r["range_resolution_m"]
        f.attrs["num_frames"]               = num_frames

        # Provenance
        f.attrs["git_commit"]               = commit
        f.attrs["source_bin_files"]         = ",".join(p.name for p in bin_paths)

    size_mb = out_path.stat().st_size / 1e6
    print(f"  Saved -> {out_path.name}  ({size_mb:.1f} MB)")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Save time-domain radar cubes to HDF5"
    )
    parser.add_argument("--session", metavar="EXPID",
                        help="Single session ID from manifest (e.g. exp003)")
    parser.add_argument("--all", action="store_true",
                        help="Process all sessions in manifest")
    args = parser.parse_args()

    if not args.session and not args.all:
        parser.print_help()
        sys.exit(0)

    cfg    = yaml.safe_load(CONFIG_PATH.read_text())
    r      = cfg["radar"]
    manifest = pd.read_csv(MANIFEST_PATH)
    data_raw = REPO_ROOT / "data" / "raw"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    commit = _git_commit()

    session_ids = manifest["session_id"].tolist() if args.all else [args.session]

    sep = "=" * 60
    failed = []
    for sid in session_ids:
        print(f"\n{sep}\n  {sid}\n{sep}")
        rows = manifest[manifest["session_id"] == sid]
        if rows.empty:
            print(f"  ERROR: '{sid}' not found in manifest.")
            failed.append(sid)
            continue
        try:
            _save_session(rows.iloc[0], r, data_raw, commit)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            failed.append(sid)

    if args.all:
        ok = len(session_ids) - len(failed)
        print(f"\n{sep}")
        print(f"  Done: {ok}/{len(session_ids)} sessions saved to {OUT_DIR}")
        if failed:
            print(f"  Failed: {failed}")
        print(sep)


if __name__ == "__main__":
    main()
