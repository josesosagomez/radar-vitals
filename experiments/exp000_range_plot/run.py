#!/usr/bin/env python3
"""exp000 — diagnostic range-profile visualisation.

Produces three outputs from exp002_sit_chair_back data:
  - mean_range_profile.png  : mean profile averaged over all frames
  - range_heatmap.png       : 2-D (time × range) magnitude heatmap
  - range_animation.mp4     : frame-by-frame animation at 20 fps

Run from the repo root:
    python experiments/exp000_range_plot/run.py

Does NOT modify src/vitals.py or any existing source file.
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import numpy as np
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
    # Conda on Windows installs ffmpeg into <env_root>/Library/bin/ffmpeg.exe.
    # Build candidate env roots in priority order.
    home = Path.home()
    py_prefix = Path(sys.prefix)  # set to the active env root by the interpreter
    candidates = [
        py_prefix / "Library" / "bin" / "ffmpeg.exe",
        # sys.executable may sit in Scripts/ inside the env
        Path(sys.executable).parent.parent / "Library" / "bin" / "ffmpeg.exe",
        # Common named conda envs in user profile
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


def _build_range_profiles(
    cube: np.ndarray,
    range_resolution_m: float,
    max_range_m: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Average-then-FFT pipeline; return (rp_db, range_m_clipped).

    cube shape expected: (frames, chirps_per_frame, samples_per_chirp, rx_channels)

    Steps per frame:
      1. Average across chirps (axis=1) -> (frames, samples, rx)
      2. Average across RX    (axis=-1) -> (frames, samples)
      3. FFT along fast-time  (axis=-1) -> (frames, samples) complex
      4. Magnitude in dB (20 log10, +eps to avoid log 0)
      5. Keep positive-frequency half (first samples//2 bins)
      6. Discard bins beyond max_range_m
    """
    samples_per_chirp = cube.shape[2]

    mean_data = cube.mean(axis=1).mean(axis=-1)          # (frames, samples)
    rp_complex = np.fft.fft(mean_data, axis=-1)          # (frames, samples)

    eps = 1e-10
    rp_db = 20.0 * np.log10(np.abs(rp_complex) + eps)   # (frames, samples)

    n_pos = samples_per_chirp // 2
    rp_db = rp_db[:, :n_pos]                             # positive frequencies

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
               label=f"target gate [{gate_min_m}–{gate_max_m} m]")
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
    # extent: [left, right, bottom, top] — time on Y with frame 0 at top
    extent = [float(range_m[0]), float(range_m[-1]), total_s, 0.0]
    im = ax.imshow(
        rp_db, aspect="auto", extent=extent, cmap="viridis", origin="upper",
    )
    ax.axvline(
        x=locked_range_m, color="red", linestyle="--", linewidth=1.0,
        label=f"bin {locked_bin} ({locked_range_m:.3f} m)",
    )
    ax.axvspan(gate_min_m, gate_max_m, alpha=0.12, color="white",
               label=f"target gate [{gate_min_m}–{gate_max_m} m]")
    ax.set_xlabel("Range (m)")
    ax.set_ylabel("Time (s)")
    ax.set_title("Range profile heatmap (exp002 sit-chair-back)")
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
               label=f"target gate [{gate_min_m}–{gate_max_m} m]")
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
    print(f"Rendering animation ({num_frames} frames at {frame_rate_hz:.0f} fps) — this may take several minutes…")
    ani.save(str(out_path), writer=writer)
    plt.close(fig)


def _select_bin_file(data_raw_dir: Path) -> Path:
    """Print a numbered list of .bin files and return the user's selection."""
    bin_files = sorted(data_raw_dir.glob("*.bin"))
    if not bin_files:
        raise FileNotFoundError(f"No .bin files found in {data_raw_dir}")
    rel = data_raw_dir.relative_to(REPO_ROOT)
    print(f"\nAvailable .bin files in {rel}:")
    for i, f in enumerate(bin_files, 1):
        print(f"  [{i}] {f.name}")
    while True:
        raw = input(f"Select a file (1-{len(bin_files)}): ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(bin_files):
            return bin_files[int(raw) - 1]
        print(f"  Please enter a number between 1 and {len(bin_files)}.")


def _find_associated_files(bin_path: Path) -> dict[str, Path | None]:
    """Return {'logfile': Path|None, 'masimo': Path|None} and print what was found."""
    parent = bin_path.parent

    # LogFile
    if "_radar" in bin_path.name:
        logfile_name = bin_path.name.replace("_radar.bin", "_radar_LogFile.csv")
        logfile_path = parent / logfile_name
        logfile: Path | None = logfile_path if logfile_path.exists() else None
    else:
        prefix = bin_path.stem
        candidates = sorted(parent.glob(f"{prefix}*LogFile*.csv"))
        logfile = candidates[0] if candidates else None
        logfile_name = logfile.name if logfile else f"{prefix}_LogFile.csv"

    # Masimo
    if "_radar.bin" in bin_path.name:
        masimo_name = bin_path.name.replace("_radar.bin", "_masimo.csv")
    else:
        masimo_name = bin_path.stem + "_masimo.csv"
    masimo_path = parent / masimo_name
    masimo: Path | None = masimo_path if masimo_path.exists() else None

    print(f"\nSelected: {bin_path.name}")
    print("Associated files found:")
    if logfile:
        print(f"  {'LogFile':<7} : {logfile.name}")
    else:
        print(f"  {'LogFile':<7} : {logfile_name} (not found — skipping)")
    if masimo:
        print(f"  {'Masimo':<7} : {masimo.name}")
    else:
        print(f"  {'Masimo':<7} : {masimo_name} (not found — skipping)")

    return {"logfile": logfile, "masimo": masimo}


def _confirm_frame_count(
    bin_path: Path, chirp_cfg: radar_io.ChirpConfig, frame_rate_hz: float
) -> int:
    """Call infer_num_frames, show the result, and let the user accept or override."""
    inferred = radar_io.infer_num_frames(bin_path, chirp_cfg)
    duration_s = inferred / frame_rate_hz
    print(
        f"Inferred frame count from file size: {inferred} frames"
        f" ({duration_s:.1f} s at {frame_rate_hz:.0f} Hz)"
    )
    raw = input("Press Enter to accept or type a number to override: ").strip()
    if raw == "":
        return inferred
    if raw.isdigit():
        return int(raw)
    print(f"  Invalid input '{raw}' — using inferred count {inferred}.")
    return inferred


def main(config_path: Path) -> None:
    cfg = yaml.safe_load(config_path.read_text())
    np.random.seed(cfg["seed"])

    c = cfg["chirp"]
    p = cfg["plot"]

    # --- 1. Select .bin file interactively ---
    data_raw_dir = REPO_ROOT / "data" / "raw"
    bin_path = _select_bin_file(data_raw_dir)

    # --- 2. Find associated LogFile and Masimo ---
    _find_associated_files(bin_path)   # prints summary; paths not used by exp000 pipeline

    # --- 3. Build chirp config (placeholder num_frames), confirm frame count ---
    chirp_cfg = radar_io.ChirpConfig(
        num_adc_samples=c["num_adc_samples"],
        num_rx=c["num_rx"],
        num_tx=c["num_tx"],
        num_chirps_per_frame=c["num_chirps_per_frame"],
        num_frames=c["num_frames"],
        frame_rate_hz=c["frame_rate_hz"],
        range_resolution_m=c["range_resolution_m"],
    )
    num_frames = _confirm_frame_count(bin_path, chirp_cfg, c["frame_rate_hz"])

    # Rebuild with confirmed frame count so read_adc_bin prints the reconciled value
    chirp_cfg = radar_io.ChirpConfig(
        num_adc_samples=c["num_adc_samples"],
        num_rx=c["num_rx"],
        num_tx=c["num_tx"],
        num_chirps_per_frame=c["num_chirps_per_frame"],
        num_frames=num_frames,
        frame_rate_hz=c["frame_rate_hz"],
        range_resolution_m=c["range_resolution_m"],
    )

    # Derive a clean sample label from the .bin filename (strip trailing _radar.bin).
    bin_stem = bin_path.name
    if bin_stem.endswith("_radar.bin"):
        sample_name = bin_stem[: -len("_radar.bin")]
    else:
        sample_name = bin_path.stem

    run_dir = (
        REPO_ROOT / "results" / "exp000_range_plot"
        / f"{sample_name}_{time.strftime('%Y%m%d_%H%M%S')}"
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    # --- 4. Load radar cube ---
    # read_adc_bin returns (frames, chirps_per_frame, num_rx, num_adc_samples)
    cube = radar_io.read_adc_bin(bin_path, chirp_cfg, trim_frames=0)

    # Transpose to (frames, chirps_per_frame, samples_per_chirp, rx_channels)
    # so axis=1 = chirps and axis=-1 = rx, matching the averaging spec.
    cube = cube.transpose(0, 1, 3, 2)

    range_resolution_m: float = c["range_resolution_m"]
    frame_rate_hz: float = c["frame_rate_hz"]
    max_range_m: float = p["max_range_m"]
    gate_min_m: float = p["gate_min_m"]
    gate_max_m: float = p["gate_max_m"]

    # --- Compute range profiles for all frames ---
    rp_db, range_m = _build_range_profiles(cube, range_resolution_m, max_range_m)
    n_range_bins = rp_db.shape[1]
    mean_profile = rp_db.mean(axis=0)

    # Find the bin inside the target gate with the highest mean amplitude.
    # This is where the subject actually is, rather than a fixed config value.
    gate_indices = np.where((range_m >= gate_min_m) & (range_m <= gate_max_m))[0]
    if len(gate_indices) == 0:
        raise ValueError(
            f"No range bins fall within gate [{gate_min_m}, {gate_max_m}] m. "
            f"Check gate_min_m / gate_max_m in config.yaml."
        )
    subject_bin = int(gate_indices[np.argmax(mean_profile[gate_indices])])

    # --- Output 1: mean range profile ---
    mean_path = run_dir / "mean_range_profile.png"
    _plot_mean_profile(
        mean_profile, range_m, subject_bin, range_resolution_m,
        gate_min_m, gate_max_m, max_range_m, mean_path,
    )
    print(f"Saved {mean_path}")

    # --- Output 2: heatmap ---
    heatmap_path = run_dir / "range_heatmap.png"
    _plot_heatmap(
        rp_db, range_m, frame_rate_hz, subject_bin, range_resolution_m,
        gate_min_m, gate_max_m, heatmap_path,
    )
    print(f"Saved {heatmap_path}")

    # --- Output 3: animation ---
    anim_path = run_dir / "range_animation.mp4"
    _save_animation(
        rp_db, range_m, frame_rate_hz, subject_bin, range_resolution_m,
        gate_min_m, gate_max_m, max_range_m, anim_path,
    )
    print(f"Saved {anim_path}")

    # --- Summary ---
    total_duration_s = num_frames / frame_rate_hz
    print("\n" + "=" * 60)
    print("SUMMARY — exp000_range_plot")
    print("=" * 60)
    print(f"Inferred frame count:          {num_frames}")
    print(f"Total duration:                {total_duration_s:.1f} s")
    print(f"Range resolution:              {range_resolution_m:.4f} m/bin")
    print(f"Range bins kept (0–{max_range_m} m):   {n_range_bins}")
    print(
        f"Subject bin (peak in gate):    {subject_bin}"
        f"  ({subject_bin * range_resolution_m:.3f} m)"
        f"  gate [{gate_min_m}–{gate_max_m} m]"
    )
    print(f"\nResults saved to: {run_dir}")


if __name__ == "__main__":
    main(Path(__file__).resolve().parent / "config.yaml")
