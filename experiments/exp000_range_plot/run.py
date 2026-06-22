#!/usr/bin/env python3
"""exp000 — diagnostic range-profile visualisation.

Reads session metadata from data/manifest.local.csv and produces three outputs:
  - mean_range_profile.png  : mean profile averaged over all frames
  - range_heatmap.png       : 2-D (time × range) magnitude heatmap
  - range_animation.mp4     : frame-by-frame animation at 20 fps

Run from the repo root:
    python experiments/exp000_range_plot/run.py --session exp003
    python experiments/exp000_range_plot/run.py          # lists available sessions

Does NOT modify src/vitals.py or any existing source file.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.animation as anim_mod
import yaml

# Resolve ffmpeg: prefer PATH, then search conda env locations on Windows.
def _find_ffmpeg() -> str | None:
    hit = shutil.which("ffmpeg")
    if hit:
        return hit
    home = Path.home()
    py_prefix = Path(sys.prefix)
    candidates = [
        py_prefix / "Library" / "bin" / "ffmpeg.exe",
        Path(sys.executable).parent.parent / "Library" / "bin" / "ffmpeg.exe",
        home / ".conda" / "envs" / "radar-vitals" / "Library" / "bin" / "ffmpeg.exe",
        home / "miniconda3" / "envs" / "radar-vitals" / "Library" / "bin" / "ffmpeg.exe",
        home / "anaconda3" / "envs" / "radar-vitals" / "Library" / "bin" / "ffmpeg.exe",
        Path(r"C:\ProgramData\anaconda3\envs\radar-vitals\Library\bin\ffmpeg.exe"),
        Path(r"C:\ProgramData\miniconda3\envs\radar-vitals\Library\bin\ffmpeg.exe"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None

_FFMPEG = _find_ffmpeg()
if _FFMPEG:
    matplotlib.rcParams["animation.ffmpeg_path"] = _FFMPEG

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src import radar_io  # noqa: E402


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def _load_manifest(manifest_path: Path) -> pd.DataFrame:
    df = pd.read_csv(manifest_path)
    df["distance_m"] = df["distance_cm"] / 100.0
    return df


def _resolve_bin_paths(session_id: str, data_raw: Path) -> list[Path]:
    """Return [path] for a single .bin or [path_0, path_1] for split files."""
    split0 = data_raw / f"{session_id}_0.bin"
    split1 = data_raw / f"{session_id}_1.bin"
    single = data_raw / f"{session_id}.bin"
    if split0.exists():
        paths = [split0]
        if split1.exists():
            paths.append(split1)
        return paths
    if single.exists():
        return [single]
    raise FileNotFoundError(
        f"No .bin file found for session '{session_id}' in {data_raw}. "
        f"Expected {single.name} or {split0.name}."
    )


def _infer_num_frames_multi(bin_paths: list[Path], cfg: radar_io.ChirpConfig) -> int:
    """Sum bytes across all parts and derive total frame count."""
    total_bytes = sum(p.stat().st_size for p in bin_paths)
    bytes_per_frame = cfg.num_chirps_per_frame * cfg.num_rx * cfg.num_adc_samples * 4
    remainder = total_bytes % bytes_per_frame
    if remainder != 0:
        names = [p.name for p in bin_paths]
        raise ValueError(
            f"Total size {total_bytes} B across {names} is not divisible by "
            f"bytes_per_frame {bytes_per_frame} (remainder {remainder} B)."
        )
    return total_bytes // bytes_per_frame


# ---------------------------------------------------------------------------
# Plotting helpers (unchanged)
# ---------------------------------------------------------------------------

def _build_range_profiles(
    cube: np.ndarray,
    range_resolution_m: float,
    max_range_m: float,
) -> tuple[np.ndarray, np.ndarray]:
    samples_per_chirp = cube.shape[2]
    mean_data = cube.mean(axis=1).mean(axis=-1)
    rp_complex = np.fft.fft(mean_data, axis=-1)
    eps = 1e-10
    rp_db = 20.0 * np.log10(np.abs(rp_complex) + eps)
    n_pos = samples_per_chirp // 2
    rp_db = rp_db[:, :n_pos]
    range_m_full = np.arange(n_pos) * range_resolution_m
    clip_mask = range_m_full <= max_range_m
    return rp_db[:, clip_mask], range_m_full[clip_mask]


def _plot_mean_profile(
    mean_profile: np.ndarray,
    range_m: np.ndarray,
    locked_bin: int,
    range_resolution_m: float,
    gate_min_m: float,
    gate_max_m: float,
    max_range_m: float,
    out_path: Path,
) -> None:
    locked_range_m = locked_bin * range_resolution_m
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(range_m, mean_profile, linewidth=1.0, color="steelblue")
    ax.axvline(
        x=locked_range_m, color="red", linestyle="--", linewidth=1.5,
        label=f"bin {locked_bin} ({locked_range_m:.3f} m)",
    )
    ax.axvspan(gate_min_m, gate_max_m, alpha=0.15, color="orange",
               label=f"target gate [{gate_min_m:.2f}–{gate_max_m:.2f} m]")
    ax.set_xlabel("Range (m)")
    ax.set_ylabel("Magnitude (dB)")
    ax.set_title("Mean range profile (averaged across all frames)")
    ax.set_xlim(0, max_range_m)
    ax.legend()
    ax.grid(True, alpha=0.4)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def _plot_heatmap(
    rp_db: np.ndarray,
    range_m: np.ndarray,
    frame_rate_hz: float,
    locked_bin: int,
    range_resolution_m: float,
    gate_min_m: float,
    gate_max_m: float,
    out_path: Path,
) -> None:
    num_frames = rp_db.shape[0]
    total_s = (num_frames - 1) / frame_rate_hz
    locked_range_m = locked_bin * range_resolution_m
    fig, ax = plt.subplots(figsize=(12, 7))
    extent = [float(range_m[0]), float(range_m[-1]), total_s, 0.0]
    im = ax.imshow(rp_db, aspect="auto", extent=extent, cmap="viridis", origin="upper")
    ax.axvline(x=locked_range_m, color="red", linestyle="--", linewidth=1.0,
               label=f"bin {locked_bin} ({locked_range_m:.3f} m)")
    ax.axvspan(gate_min_m, gate_max_m, alpha=0.12, color="white",
               label=f"target gate [{gate_min_m:.2f}–{gate_max_m:.2f} m]")
    ax.set_xlabel("Range (m)")
    ax.set_ylabel("Time (s)")
    ax.set_title("Range profile heatmap")
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("Magnitude (dB)")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def _save_animation(
    rp_db: np.ndarray,
    range_m: np.ndarray,
    frame_rate_hz: float,
    locked_bin: int,
    range_resolution_m: float,
    gate_min_m: float,
    gate_max_m: float,
    max_range_m: float,
    out_path: Path,
) -> None:
    num_frames = rp_db.shape[0]
    vmin, vmax = float(rp_db.min()), float(rp_db.max())
    locked_range_m = locked_bin * range_resolution_m
    fig, ax = plt.subplots(figsize=(10, 4))
    (line,) = ax.plot(range_m, rp_db[0], linewidth=1.0, color="steelblue")
    ax.axvline(x=locked_range_m, color="red", linestyle="--", linewidth=1.5,
               label=f"bin {locked_bin} ({locked_range_m:.3f} m)")
    ax.axvspan(gate_min_m, gate_max_m, alpha=0.15, color="orange",
               label=f"target gate [{gate_min_m:.2f}–{gate_max_m:.2f} m]")
    ax.set_xlabel("Range (m)")
    ax.set_ylabel("Magnitude (dB)")
    ax.set_xlim(0.0, max_range_m)
    ax.set_ylim(vmin, vmax)
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.4)
    title = ax.set_title("Frame 0 | t = 0.00 s")

    def _update(frame_idx: int):
        line.set_ydata(rp_db[frame_idx])
        title.set_text(f"Frame {frame_idx} | t = {frame_idx / frame_rate_hz:.2f} s")
        return line, title

    ani = anim_mod.FuncAnimation(
        fig, _update,
        frames=num_frames,
        interval=1000.0 / frame_rate_hz,
        blit=True,
    )
    if not _FFMPEG:
        plt.close(fig)
        raise RuntimeError(
            "ffmpeg not found. Install it with:\n"
            "  conda install -c conda-forge ffmpeg -n radar-vitals"
        )
    writer = anim_mod.FFMpegWriter(fps=int(frame_rate_hz), bitrate=1800)
    print(f"Rendering animation ({num_frames} frames at {frame_rate_hz:.0f} fps) — "
          "this may take several minutes...")
    ani.save(str(out_path), writer=writer)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Per-session runner
# ---------------------------------------------------------------------------

def _run_session(session_id: str, manifest: pd.DataFrame, data_raw: Path, cfg: dict, *, no_animation: bool = False) -> None:
    session_rows = manifest[manifest["session_id"] == session_id]
    if session_rows.empty:
        ids = manifest["session_id"].tolist()
        raise ValueError(
            f"Session '{session_id}' not found in manifest. Available: {ids}"
        )
    row = session_rows.iloc[0]
    distance_m = float(row["distance_m"])
    posture = str(row["posture"])
    orientation = str(row["radar_orientation"])

    bin_paths = _resolve_bin_paths(session_id, data_raw)

    c = cfg["chirp"]
    p = cfg["plot"]
    gate_margin = float(cfg["gate_margin_m"])
    range_resolution_m = float(c["range_resolution_m"])
    frame_rate_hz = float(c["frame_rate_hz"])
    max_range_m = float(p["max_range_m"])
    gate_min_m = max(0.0, distance_m - gate_margin)
    gate_max_m = distance_m + gate_margin

    print(f"Session:      {session_id}")
    print(f"Posture:      {posture}  ({orientation})")
    print(f"Distance:     {distance_m:.2f} m ({row['distance_cm']} cm)")
    print(f"Gate:         [{gate_min_m:.2f}, {gate_max_m:.2f}] m")
    print(f"Bin file(s):  {[bp.name for bp in bin_paths]}")

    chirp_cfg = radar_io.ChirpConfig(
        num_adc_samples=c["num_adc_samples"],
        num_rx=c["num_rx"],
        num_tx=c["num_tx"],
        num_chirps_per_frame=c["num_chirps_per_frame"],
        num_frames=1,
        frame_rate_hz=frame_rate_hz,
        range_resolution_m=range_resolution_m,
    )
    num_frames = _infer_num_frames_multi(bin_paths, chirp_cfg)
    duration_s = num_frames / frame_rate_hz
    print(f"Frames:       {num_frames}  ({duration_s:.1f} s at {frame_rate_hz:.0f} Hz)")

    chirp_cfg = radar_io.ChirpConfig(
        num_adc_samples=c["num_adc_samples"],
        num_rx=c["num_rx"],
        num_tx=c["num_tx"],
        num_chirps_per_frame=c["num_chirps_per_frame"],
        num_frames=num_frames,
        frame_rate_hz=frame_rate_hz,
        range_resolution_m=range_resolution_m,
    )

    run_dir = (
        REPO_ROOT / "results" / "exp000_range_plot"
        / f"{session_id}_{time.strftime('%Y%m%d_%H%M%S')}"
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    bin_arg = bin_paths[0] if len(bin_paths) == 1 else bin_paths
    cube = radar_io.read_adc_bin(bin_arg, chirp_cfg, trim_frames=0)
    cube = cube.transpose(0, 1, 3, 2)

    rp_db, range_m = _build_range_profiles(cube, range_resolution_m, max_range_m)
    del cube
    n_range_bins = rp_db.shape[1]
    mean_profile = rp_db.mean(axis=0)

    gate_indices = np.where((range_m >= gate_min_m) & (range_m <= gate_max_m))[0]
    if len(gate_indices) == 0:
        raise ValueError(
            f"No range bins fall within gate [{gate_min_m:.2f}, {gate_max_m:.2f}] m."
        )
    subject_bin = int(gate_indices[np.argmax(mean_profile[gate_indices])])

    mean_path = run_dir / "mean_range_profile.png"
    _plot_mean_profile(
        mean_profile, range_m, subject_bin, range_resolution_m,
        gate_min_m, gate_max_m, max_range_m, mean_path,
    )
    print(f"Saved {mean_path}")

    heatmap_path = run_dir / "range_heatmap.png"
    _plot_heatmap(
        rp_db, range_m, frame_rate_hz, subject_bin, range_resolution_m,
        gate_min_m, gate_max_m, heatmap_path,
    )
    print(f"Saved {heatmap_path}")

    if no_animation:
        print("Skipping animation (--no-animation)")
    else:
        anim_path = run_dir / "range_animation.mp4"
        _save_animation(
            rp_db, range_m, frame_rate_hz, subject_bin, range_resolution_m,
            gate_min_m, gate_max_m, max_range_m, anim_path,
        )
        print(f"Saved {anim_path}")

    print(f"\nSUMMARY — {session_id}")
    print(f"  Frames:           {num_frames}  ({duration_s:.1f} s)")
    print(f"  Range resolution: {range_resolution_m:.4f} m/bin")
    print(f"  Range bins kept:  {n_range_bins}  (0–{max_range_m} m)")
    print(
        f"  Subject bin:      {subject_bin}"
        f"  ({subject_bin * range_resolution_m:.3f} m)"
        f"  gate [{gate_min_m:.2f}–{gate_max_m:.2f} m]"
    )
    print(f"  Results:          {run_dir}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(config_path: Path) -> None:
    cfg = yaml.safe_load(config_path.read_text())
    np.random.seed(cfg["seed"])

    parser = argparse.ArgumentParser(
        description="exp000: range-profile plots for a single session from the manifest"
    )
    parser.add_argument(
        "--session", metavar="EXPID",
        help="Session ID from manifest (e.g. exp003). Omit to list available sessions.",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Run all sessions listed in the manifest.",
    )
    parser.add_argument(
        "--no-animation", action="store_true",
        help="Skip .mp4 animation (much faster; still saves PNG outputs).",
    )
    args = parser.parse_args()

    manifest_path = REPO_ROOT / cfg["manifest"]
    manifest = _load_manifest(manifest_path)
    data_raw = REPO_ROOT / "data" / "raw"

    # --all: iterate over every session in manifest
    if args.all:
        session_ids = manifest["session_id"].tolist()
        print(f"Running all {len(session_ids)} sessions: {session_ids}\n")
        failed = []
        for sid in session_ids:
            print("\n" + "=" * 60)
            print(f"  SESSION: {sid}")
            print("=" * 60)
            try:
                _run_session(sid, manifest, data_raw, cfg, no_animation=args.no_animation)
            except Exception as exc:
                print(f"  ERROR in {sid}: {exc}")
                failed.append((sid, str(exc)))
        print("\n" + "=" * 60)
        print("ALL-SESSION RUN COMPLETE")
        if failed:
            print(f"Failed ({len(failed)}):")
            for sid, msg in failed:
                print(f"  {sid}: {msg}")
        else:
            print("All sessions completed successfully.")
        return

    # List sessions if no session given
    if args.session is None:
        print("\nAvailable sessions in manifest:")
        print(f"  {'ID':<10}  {'Posture':<20}  {'Dist (cm)':<10}  {'Orientation'}")
        print("  " + "-" * 60)
        for _, row in manifest.iterrows():
            print(f"  {row['session_id']:<10}  {row['posture']:<20}  "
                  f"{row['distance_cm']:<10}  {row['radar_orientation']}")
        print("\nRun with: python experiments/exp000_range_plot/run.py --session <EXPID>")
        print("      or: python experiments/exp000_range_plot/run.py --all")
        sys.exit(0)

    # Single-session path
    _run_session(args.session, manifest, data_raw, cfg, no_animation=args.no_animation)


if __name__ == "__main__":
    main(Path(__file__).resolve().parent / "config.yaml")
