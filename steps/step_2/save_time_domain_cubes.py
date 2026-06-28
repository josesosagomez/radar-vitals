#!/usr/bin/env python3
"""Save radar time-domain cubes to HDF5 for downstream processing.

Reads each session from the manifest, decodes the raw .bin into a
complex radar cube (time domain), and writes it to:
    data/processed/time_domain_cubes/<session_id>.h5

HDF5 layout
-----------
/cube               complex64 dataset, shape (frames, chirps, rx, adc_samples)
                    = (N, 32, 4, 256)  — canonical shape, no transpose applied
Root attributes     session metadata + chirp config + provenance

Run from repo root:
    python -X utf8 steps/step_2/save_time_domain_cubes.py --session exp003
    python -X utf8 steps/step_2/save_time_domain_cubes.py --all
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

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src import radar_io  # noqa: E402


_REQUIRED_FIELDS = [
    "radar_start_epoch_seconds",
    "posture",
    "distance_cm",
    "stationary_intervals",
    "radar_orientation",
    "iq_swap",
]


def _validate_row(row: pd.Series) -> list[str]:
    """Return a list of validation error strings; empty means the row is processable."""
    errors = []
    for field in _REQUIRED_FIELDS:
        val = row.get(field, "")
        if pd.isna(val) or str(val).strip() == "":
            errors.append(f"'{field}' is missing or blank")
    # Check numeric fields are actually parseable
    for field, cast in [("radar_start_epoch_seconds", int), ("distance_cm", int)]:
        val = row.get(field, "")
        if not (pd.isna(val) or str(val).strip() == ""):  # skip if already caught above
            try:
                cast(val)
            except (ValueError, TypeError):
                errors.append(f"'{field}' is not a valid {cast.__name__}: {val!r}")
    return errors


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def _resolve_bin_paths(session_id: str, data_raw: Path) -> list[Path]:
    parts = sorted(
        data_raw.glob(f"{session_id}_*.bin"),
        key=lambda p: int(p.stem.rsplit("_", 1)[-1]),
    )
    if parts:
        return parts
    single = data_raw / f"{session_id}.bin"
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


def _save_session(
    row: pd.Series,
    r: dict,
    h5cfg: dict,
    data_raw: Path,
    out_dir: Path,
    commit: str,
    overwrite: bool = False,
) -> Path:
    session_id  = str(row["session_id"])
    posture     = str(row["posture"])
    dist_cm     = int(row["distance_cm"])
    t0          = int(row["radar_start_epoch_seconds"])
    interval    = str(row["stationary_intervals"])
    orientation = str(row["radar_orientation"])

    locked_bin_val  = row.get("locked_bin")
    locked_bin      = (int(float(locked_bin_val))
                       if pd.notna(locked_bin_val) and str(locked_bin_val).strip() != ""
                       else None)
    highest_bins_val = row.get("highest_bins", "")
    highest_bins     = str(highest_bins_val).strip() if pd.notna(highest_bins_val) else ""

    bin_paths   = _resolve_bin_paths(session_id, data_raw)
    iq_swap_val = row.get("iq_swap", "")
    if pd.isna(iq_swap_val) or str(iq_swap_val).strip() == "":
        raise ValueError(
            f"Session '{session_id}': 'iq_swap' is missing or blank in the manifest. "
            "Set it to 'True' (Python/DCA1000 capture) or 'False' (mmWave Studio capture) "
            "before running Step 2."
        )
    iq_swap = str(iq_swap_val).strip().lower() == "true"

    chirp_cfg = radar_io.ChirpConfig(
        num_adc_samples      = r["num_adc_samples"],
        num_rx               = r["num_rx"],
        num_tx               = r["num_tx"],
        num_chirps_per_frame = r["num_chirps_per_frame"],
        num_frames           = 1,           # placeholder; overwritten below
        frame_rate_hz        = r["frame_rate_hz"],
        range_resolution_m   = r["range_resolution_m"],
        iq_swap              = iq_swap,
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
        iq_swap              = iq_swap,
    )

    print(f"  Source: {[p.name for p in bin_paths]}")
    print(f"  Frames: {num_frames}  ({num_frames / r['frame_rate_hz']:.1f} s)")

    bin_arg = bin_paths[0] if len(bin_paths) == 1 else bin_paths
    cube = radar_io.read_adc_bin(bin_arg, chirp_cfg)
    # cube shape: (frames, chirps, rx, adc_samples) = (N, 32, 4, 256)
    # canonical time-domain shape — no transpose applied here

    out_path = out_dir / f"{session_id}.h5"
    if out_path.exists():
        if not overwrite:
            raise FileExistsError(
                f"{out_path.name} already exists. "
                "Re-run with --overwrite to replace it (quality masks will be lost)."
            )
        print(f"  WARNING: overwriting {out_path.name} -- "
              "run Step 3 (add_quality_mask) again for this session.")
    with h5py.File(out_path, "w") as f:
        chunk = (min(h5cfg["chunk_frames"], num_frames),
                 r["num_chirps_per_frame"], r["num_rx"], r["num_adc_samples"])
        ds = f.create_dataset(
            "cube",
            data=cube,
            chunks=chunk,
            compression=h5cfg["compression"],
            compression_opts=h5cfg["compression_level"],
        )
        ds.attrs["axes"] = "frames, chirps_per_frame, rx, adc_samples"
        ds.attrs["dtype"] = "complex64"

        # Session metadata
        f.attrs["session_id"]               = session_id
        f.attrs["posture"]                  = posture
        f.attrs["distance_cm"]              = dist_cm
        f.attrs["radar_start_epoch_seconds"] = t0
        f.attrs["stationary_intervals"]     = interval
        f.attrs["radar_orientation"]        = orientation
        if locked_bin is not None:
            f.attrs["locked_bin"]           = locked_bin
        if highest_bins:
            f.attrs["highest_bins"]         = highest_bins

        # Chirp config
        f.attrs["num_adc_samples"]          = r["num_adc_samples"]
        f.attrs["num_rx"]                   = r["num_rx"]
        f.attrs["num_tx"]                   = r["num_tx"]
        f.attrs["num_chirps_per_frame"]     = r["num_chirps_per_frame"]
        f.attrs["frame_rate_hz"]            = r["frame_rate_hz"]
        f.attrs["range_resolution_m"]       = r["range_resolution_m"]
        f.attrs["num_frames"]               = num_frames
        f.attrs["iq_swap"]                  = iq_swap

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
    parser.add_argument(
        "--config", default="steps/step_2/config.yaml",
        help="Path to config.yaml (default: steps/step_2/config.yaml)"
    )
    parser.add_argument("--session", metavar="EXPID",
                        help="Single session ID from manifest (e.g. exp003)")
    parser.add_argument("--all", action="store_true",
                        help="Process all sessions in manifest")
    parser.add_argument("--overwrite", action="store_true",
                        help="Replace existing .h5 files (quality masks will be lost; re-run Step 3)")
    args = parser.parse_args()

    if not args.session and not args.all:
        parser.print_help()
        sys.exit(0)

    cfg_path = REPO_ROOT / args.config
    if not cfg_path.exists():
        sys.exit(f"ERROR: config not found: {cfg_path}")
    cfg = yaml.safe_load(cfg_path.read_text())

    r      = cfg["radar"]
    h5cfg  = cfg["hdf5"]
    paths  = cfg["paths"]

    manifest_path = REPO_ROOT / paths["manifest"]
    data_raw      = REPO_ROOT / paths["raw_dir"]
    out_dir       = REPO_ROOT / paths["output_dir"]

    manifest = pd.read_csv(manifest_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    commit = _git_commit()

    session_ids = manifest["session_id"].tolist() if args.all else [args.session]

    sep = "=" * 60
    failed  = []
    skipped = []
    for sid in session_ids:
        print(f"\n{sep}\n  {sid}\n{sep}")
        rows = manifest[manifest["session_id"] == sid]
        if rows.empty:
            print(f"  ERROR: '{sid}' not found in manifest.")
            failed.append(sid)
            continue

        row = rows.iloc[0]

        # Skip excluded rows in --all mode; a direct --session call still proceeds
        # (the user explicitly asked for it) but gets a warning.
        exclusion = str(row.get("exclusion_reason", "")).strip()
        if exclusion:
            if args.all:
                print(f"  SKIPPED (excluded): {exclusion}")
                skipped.append(sid)
                continue
            else:
                print(f"  WARNING: session is excluded in manifest: {exclusion}")

        errors = _validate_row(row)
        if errors:
            for e in errors:
                print(f"  ERROR: {e}")
            failed.append(sid)
            continue

        try:
            _save_session(row, r, h5cfg, data_raw, out_dir, commit, args.overwrite)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            failed.append(sid)

    processed = len(session_ids) - len(failed) - len(skipped)
    print(f"\n{sep}")
    print(f"  Done: {processed}/{len(session_ids)} saved  "
          f"skipped={len(skipped)}  failed={len(failed)}")
    if failed:
        print(f"  Failed:  {failed}")
    if skipped:
        print(f"  Skipped: {skipped}")
    print(sep)

    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
