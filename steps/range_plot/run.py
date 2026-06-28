#!/usr/bin/env python3
"""range_plot — diagnostic range-profile visualisation and chest-bin recommendation.

Reads session metadata from data/manifest.local.csv and produces:
  - mean_range_profile.png  : mean profile averaged over all frames
  - range_heatmap.png       : 2-D (time × range) magnitude heatmap
  - range_animation.mp4     : frame-by-frame animation at 20 fps
  - range_plot.log          : diagnostic log (bin ranking, provenance, distances)

All outputs written to figures/<session_id>/range_plot/ (overwritten each run).
Reads locked_bin from the manifest (set by step_3/select_chest_bin.py) and marks it
on the plots. Does NOT modify the manifest. The 3-method ranking table in the log
is kept for diagnostic comparison against the authoritative step_3 selection.

Run from the repo root:
    python steps/range_plot/run.py --session exp003
    python steps/range_plot/run.py          # lists available sessions

Does NOT modify src/vitals.py or any existing source file.
"""
from __future__ import annotations

import argparse
import datetime
import shutil
import subprocess
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
# Bin-selection helpers
# ---------------------------------------------------------------------------

def _phase_metrics_per_bin(
    cube: np.ndarray,
    frame_rate_hz: float,
    resp_lo_hz: float = 0.10,
    resp_hi_hz: float = 0.50,
) -> tuple[np.ndarray, np.ndarray]:
    """Phase variance and respiratory-band SNR per positive range bin.

    cube: (frames, chirps, samples, rx)  — already transposed by the caller

    Returns
    -------
    phase_std : (n_pos_bins,) — slow-time phase std in radians.
                Static walls → near-zero.  Chest wall → >10 rad.
    resp_snr  : (n_pos_bins,) — peak power in [resp_lo, resp_hi] Hz relative
                to the median spectral floor, in dB.  A bin driven by regular
                breathing scores high; broadband vibration or static clutter
                scores low.
    """
    n_pos = cube.shape[2] // 2
    n_samples = cube.shape[2]
    win = np.hanning(n_samples).reshape(1, 1, n_samples, 1)
    fft_data = np.fft.fft(cube * win, axis=2)[:, :, :n_pos, :]  # (frames, chirps, n_pos, rx)
    mean_complex = fft_data.mean(axis=1).mean(axis=-1)      # (frames, n_pos)
    phase_uw = np.unwrap(np.angle(mean_complex), axis=0)    # (frames, n_pos)

    phase_std = phase_uw.std(axis=0)                        # (n_pos,)

    # Respiratory SNR: detrend → rfft → peak in respiratory band / median floor
    n_frames = phase_uw.shape[0]
    t = np.arange(n_frames, dtype=float)
    coeffs = np.polyfit(t, phase_uw, 1)                     # (2, n_pos)
    phase_dt = phase_uw - (t[:, None] * coeffs[0] + coeffs[1])

    spec = np.abs(np.fft.rfft(phase_dt, axis=0)) ** 2      # (n_freq, n_pos)
    freqs = np.fft.rfftfreq(n_frames, d=1.0 / frame_rate_hz)

    resp_mask = (freqs >= resp_lo_hz) & (freqs <= resp_hi_hz)
    if resp_mask.sum() == 0:
        resp_snr = np.zeros(n_pos)
    else:
        resp_peak   = spec[resp_mask].max(axis=0)           # (n_pos,)
        noise_floor = np.maximum(np.median(spec, axis=0), 1e-12)
        resp_snr    = 10.0 * np.log10(resp_peak / noise_floor)

    return phase_std, resp_snr


def _rank_bins(
    mean_profile_db: np.ndarray,
    phase_std: np.ndarray,
    resp_snr: np.ndarray,
    range_m: np.ndarray,
    search_min_m: float,
    search_max_m: float,
    top_n: int = 5,
) -> tuple[list[dict], int, str]:
    """Rank candidate bins inside [search_min_m, search_max_m] by three methods.

    Method 1 — energy:    mean range-profile magnitude (dB).
    Method 2 — phase std: slow-time phase standard deviation (radians).
    Method 3 — resp SNR:  respiratory-band spectral peak SNR (dB).

    Combined rank = sum of per-method ranks (lower is better; bins absent from
    a method's top-n receive a penalty of top_n+1).

    A bin is 'qualified' when it appears in the top-n of at least 2 of the 3
    methods.  The recommended bin is the top combined-rank qualified bin;
    falls back to combined-rank #1 if no bin qualifies.

    Returns
    -------
    entries         : list[dict] — top_n bins sorted by combined rank, each with:
                        rank, bin_idx, range_m, energy_db, energy_rank,
                        phase_std_rad, phase_rank, resp_snr_db, resp_rank,
                        n_methods (int — how many methods ranked this bin)
    recommended_bin : int
    agree_note      : str — human-readable agreement label
    """
    mask = (range_m >= search_min_m) & (range_m <= search_max_m)
    idx = np.where(mask)[0]
    if len(idx) == 0:
        raise ValueError(
            f"No range bins in [{search_min_m:.2f}, {search_max_m:.2f}] m."
        )

    top_e  = idx[np.argsort(-mean_profile_db[idx])[:top_n]]
    top_pv = idx[np.argsort(-phase_std[idx])[:top_n]]
    top_rs = idx[np.argsort(-resp_snr[idx])[:top_n]]

    e_rank  = {int(b): i + 1 for i, b in enumerate(top_e)}
    pv_rank = {int(b): i + 1 for i, b in enumerate(top_pv)}
    rs_rank = {int(b): i + 1 for i, b in enumerate(top_rs)}

    penalty    = top_n + 1
    candidates = set(e_rank) | set(pv_rank) | set(rs_rank)
    combined   = {
        b: e_rank.get(b, penalty) + pv_rank.get(b, penalty) + rs_rank.get(b, penalty)
        for b in candidates
    }
    sorted_bins = sorted(candidates, key=lambda b: combined[b])[:top_n]

    entries = []
    for rank, b in enumerate(sorted_bins, 1):
        n_methods = sum([b in e_rank, b in pv_rank, b in rs_rank])
        entries.append({
            "rank":          rank,
            "bin_idx":       b,
            "range_m":       float(range_m[b]),
            "energy_db":     float(mean_profile_db[b]),
            "energy_rank":   e_rank.get(b, "-"),
            "phase_std_rad": float(phase_std[b]),
            "phase_rank":    pv_rank.get(b, "-"),
            "resp_snr_db":   float(resp_snr[b]),
            "resp_rank":     rs_rank.get(b, "-"),
            "n_methods":     n_methods,
        })

    qualified = [e for e in entries if e["n_methods"] >= 2]
    if qualified:
        recommended_bin = qualified[0]["bin_idx"]
        b = recommended_bin
        if qualified[0]["n_methods"] == 3:
            agree_note = "— all 3 methods agree"
        else:
            which = (
                (["energy"] if b in e_rank else [])
                + (["phase"]  if b in pv_rank else [])
                + (["resp"]   if b in rs_rank else [])
            )
            agree_note = f"— {' + '.join(which)} agree"
    else:
        recommended_bin = sorted_bins[0]
        agree_note = "— fallback (no 2-method overlap)"

    return entries, recommended_bin, agree_note


# ---------------------------------------------------------------------------
# Plotting helpers
# ---------------------------------------------------------------------------

def _build_range_profiles(
    cube: np.ndarray,
    range_resolution_m: float,
    max_range_m: float,
) -> tuple[np.ndarray, np.ndarray]:
    # cube: (frames, chirps, samples, rx)
    n_samples = cube.shape[2]

    # Non-coherent integration: FFT first, then average power across chirps and RX.
    # This gives ~6 dB more SNR than coherent IQ averaging and is robust to
    # inter-chirp phase drift and inter-RX phase spread.
    win = np.hanning(n_samples).reshape(1, 1, n_samples, 1)
    rp = np.fft.fft(cube * win, axis=2)             # (frames, chirps, range_bins, rx)
    power = np.abs(rp) ** 2                          # instantaneous power per bin
    nci = power.mean(axis=1).mean(axis=-1)           # avg over chirps, then RX → (frames, range_bins)

    eps = 1e-10
    rp_db = 10.0 * np.log10(nci + eps)              # power dB (10·log10, not 20)

    n_pos = n_samples // 2
    rp_db = rp_db[:, :n_pos]
    range_m_full = np.arange(n_pos) * range_resolution_m
    clip_mask = range_m_full <= max_range_m
    return rp_db[:, clip_mask], range_m_full[clip_mask]


def _plot_mean_profile(
    mean_profile: np.ndarray,
    range_m: np.ndarray,
    locked_bin: int,
    range_resolution_m: float,
    search_min_m: float,
    search_max_m: float,
    max_range_m: float,
    out_path: Path,
    distance_m: float | None = None,
    bin_label: str = "locked_bin",
) -> None:
    rec_range_m = locked_bin * range_resolution_m
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(range_m, mean_profile, linewidth=1.0, color="steelblue")
    ax.axvspan(search_min_m, search_max_m, alpha=0.10, color="orange",
               label=f"search window [{search_min_m:.2f}–{search_max_m:.2f} m]")
    ax.axvline(
        x=rec_range_m, color="red", linestyle="--", linewidth=1.8,
        label=f"{bin_label} {locked_bin} ({rec_range_m:.3f} m)",
    )
    if distance_m is not None:
        ax.axvline(x=distance_m, color="gray", linestyle=":", linewidth=1.0,
                   label=f"tape measure ({distance_m:.2f} m)")
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
    search_min_m: float,
    search_max_m: float,
    out_path: Path,
    distance_m: float | None = None,
    bin_label: str = "locked_bin",
) -> None:
    num_frames = rp_db.shape[0]
    total_s = (num_frames - 1) / frame_rate_hz
    rec_range_m = locked_bin * range_resolution_m
    fig, ax = plt.subplots(figsize=(12, 7))
    extent = [float(range_m[0]), float(range_m[-1]), total_s, 0.0]
    im = ax.imshow(rp_db, aspect="auto", extent=extent, cmap="viridis", origin="upper")
    ax.axvspan(search_min_m, search_max_m, alpha=0.08, color="white",
               label=f"search window [{search_min_m:.2f}–{search_max_m:.2f} m]")
    ax.axvline(x=rec_range_m, color="red", linestyle="--", linewidth=1.2,
               label=f"{bin_label} {locked_bin} ({rec_range_m:.3f} m)")
    if distance_m is not None:
        ax.axvline(x=distance_m, color="yellow", linestyle=":", linewidth=0.9,
                   label=f"tape measure ({distance_m:.2f} m)")
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
    search_min_m: float,
    search_max_m: float,
    max_range_m: float,
    out_path: Path,
    distance_m: float | None = None,
    bin_label: str = "locked_bin",
) -> None:
    num_frames = rp_db.shape[0]
    vmin, vmax = float(rp_db.min()), float(rp_db.max())
    rec_range_m = locked_bin * range_resolution_m
    fig, ax = plt.subplots(figsize=(10, 4))
    (line,) = ax.plot(range_m, rp_db[0], linewidth=1.0, color="steelblue")
    ax.axvline(x=rec_range_m, color="red", linestyle="--", linewidth=1.5,
               label=f"{bin_label} {locked_bin} ({rec_range_m:.3f} m)")
    ax.axvspan(search_min_m, search_max_m, alpha=0.10, color="orange",
               label=f"search window [{search_min_m:.2f}–{search_max_m:.2f} m]")
    if distance_m is not None:
        ax.axvline(x=distance_m, color="gray", linestyle=":", linewidth=1.0,
                   label=f"tape measure ({distance_m:.2f} m)")
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
# Diagnostic log
# ---------------------------------------------------------------------------

def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO_ROOT), stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        return "unknown"


def _write_diagnostic_log(
    log_path: Path,
    session_id: str,
    posture: str,
    orientation: str,
    bin_paths: list[Path],
    iq_swap: bool,
    tape_distance_m: float,
    num_frames: int,
    duration_s: float,
    frame_rate_hz: float,
    range_resolution_m: float,
    search_min_m: float,
    search_max_m: float,
    ranking: list[dict],
    ranking_recommendation: int,
    agree_note: str,
    highest_bins_str: str,
    manifest_locked_bin: int | None,
    bin_source: str,
    plot_bin: int,
) -> None:
    commit = _git_commit()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    plot_bin_m = plot_bin * range_resolution_m
    tape_delta_cm = abs(plot_bin_m * 100 - tape_distance_m * 100)

    agree_status = (
        "AGREE" if manifest_locked_bin is not None and manifest_locked_bin == ranking_recommendation
        else ("NO MANIFEST BIN" if manifest_locked_bin is None else "DIFFER")
    )

    lines = [
        f"range_plot diagnostic log — {session_id}",
        f"Generated: {now}  |  git commit: {commit}",
        "=" * 70,
        "",
        "# Provenance",
        f"  session_id        : {session_id}",
        f"  posture           : {posture}",
        f"  radar_orientation : {orientation}",
        f"  iq_swap           : {iq_swap}",
        f"  source_bin_files  : {[p.name for p in bin_paths]}",
        "",
        "# Plotted bin  (from manifest — set by step_3/select_chest_bin.py)",
        f"  bin_source        : {bin_source}",
        f"  plot_bin          : {plot_bin}  ({plot_bin_m:.3f} m)",
        f"  tape_distance_m   : {tape_distance_m:.3f} m  (manifest reference only)",
        f"  tape_vs_plot_Δ    : {tape_delta_cm:.1f} cm",
        "",
        "# Capture info",
        f"  num_frames        : {num_frames}  ({duration_s:.1f} s at {frame_rate_hz:.0f} Hz)",
        f"  range_resolution  : {range_resolution_m:.4f} m/bin",
        f"  search_window     : [{search_min_m:.2f}, {search_max_m:.2f}] m",
        "",
        "# 3-method ranking (diagnostic — range_plot independent estimate)",
        f"  ranking_recommendation : {ranking_recommendation}  "
        f"({ranking_recommendation * range_resolution_m:.3f} m)  {agree_note}",
        f"  next_best_bins    : [{highest_bins_str}]",
        f"  vs manifest       : {agree_status}",
        "",
        f"  {'Rank':>4}  {'Bin':>4}  {'Range':>7}  {'E.rank':>6}  {'Energy(dB)':>10}  "
        f"{'P.rank':>6}  {'Phase σ(rad)':>12}  {'R.rank':>6}  {'Resp SNR(dB)':>12}  {'Methods':>7}",
        f"  {'-'*4}  {'-'*4}  {'-'*7}  {'-'*6}  {'-'*10}  {'-'*6}  {'-'*12}  {'-'*6}  {'-'*12}  {'-'*7}",
    ]
    for e in ranking:
        er = f"{e['energy_rank']:>6}" if isinstance(e["energy_rank"], int) else f"{'—':>6}"
        pr = f"{e['phase_rank']:>6}"  if isinstance(e["phase_rank"],  int) else f"{'—':>6}"
        rr = f"{e['resp_rank']:>6}"   if isinstance(e["resp_rank"],   int) else f"{'—':>6}"
        marker = "  ← plotted" if e["bin_idx"] == plot_bin else (
            "  ← ranking #1" if e["rank"] == 1 and e["bin_idx"] != plot_bin else ""
        )
        lines.append(
            f"  {e['rank']:>4}  {e['bin_idx']:>4}  {e['range_m']:>6.3f}m  "
            f"{er}  {e['energy_db']:>10.1f}  "
            f"{pr}  {e['phase_std_rad']:>12.2f}  "
            f"{rr}  {e['resp_snr_db']:>12.1f}  {e['n_methods']:>5}/3{marker}"
        )

    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Per-session runner
# ---------------------------------------------------------------------------

def _run_session(session_id: str, manifest: pd.DataFrame, data_raw: Path, cfg: dict, *, no_animation: bool = False) -> dict:
    session_rows = manifest[manifest["session_id"] == session_id]
    if session_rows.empty:
        ids = manifest["session_id"].tolist()
        raise ValueError(
            f"Session '{session_id}' not found in manifest. Available: {ids}"
        )
    row = session_rows.iloc[0]
    distance_m = float(row["distance_cm"]) / 100.0
    posture = str(row["posture"])
    orientation = str(row["radar_orientation"])

    bin_paths = _resolve_bin_paths(session_id, data_raw)
    iq_swap   = str(row.get("iq_swap", "False")).strip().lower() == "true"

    c = cfg["chirp"]
    p = cfg["plot"]
    search_min_m = float(cfg["search_min_m"])
    search_max_m = float(cfg["search_max_m"])
    top_n_bins   = int(cfg.get("top_n_bins", 5))
    range_resolution_m = float(c["range_resolution_m"])
    frame_rate_hz = float(c["frame_rate_hz"])
    max_range_m = float(p["max_range_m"])

    # Read locked_bin from manifest (authoritative — set by step_3/select_chest_bin.py).
    _raw = row.get("locked_bin", "")
    if pd.isna(_raw) or str(_raw).strip() == "":
        manifest_locked_bin: int | None = None
    else:
        manifest_locked_bin = int(float(_raw))

    print(f"Session:      {session_id}")
    print(f"Posture:      {posture}  ({orientation})")
    print(f"Distance:     {distance_m:.2f} m ({row['distance_cm']} cm)  [tape ref only]")
    print(f"Search:       [{search_min_m:.2f}, {search_max_m:.2f}] m  (fixed physical limits)")
    print(f"Bin file(s):  {[bp.name for bp in bin_paths]}")
    print(f"IQ swap:      {iq_swap}")
    if manifest_locked_bin is not None:
        print(f"Manifest bin: {manifest_locked_bin}  ({manifest_locked_bin * range_resolution_m:.3f} m)  [step_3]")
    else:
        print("Manifest bin: NOT SET — run step_3/select_chest_bin.py first")

    chirp_cfg = radar_io.ChirpConfig(
        num_adc_samples=c["num_adc_samples"],
        num_rx=c["num_rx"],
        num_tx=c["num_tx"],
        num_chirps_per_frame=c["num_chirps_per_frame"],
        num_frames=1,
        frame_rate_hz=frame_rate_hz,
        range_resolution_m=range_resolution_m,
        iq_swap=iq_swap,
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
        iq_swap=iq_swap,
    )

    run_dir = REPO_ROOT / "figures" / session_id / "range_plot"
    run_dir.mkdir(parents=True, exist_ok=True)

    bin_arg = bin_paths[0] if len(bin_paths) == 1 else bin_paths
    cube = radar_io.read_adc_bin(bin_arg, chirp_cfg, trim_frames=0)
    cube = cube.transpose(0, 1, 3, 2)   # → (frames, chirps, samples, rx)

    rp_db, range_m = _build_range_profiles(cube, range_resolution_m, max_range_m)
    n_range_bins = rp_db.shape[1]
    mean_profile = rp_db.mean(axis=0)

    # Methods 2 & 3: phase variance + respiratory SNR (single pass before del cube)
    phase_std, resp_snr = _phase_metrics_per_bin(cube, frame_rate_hz)
    phase_std = phase_std[:len(range_m)]
    resp_snr  = resp_snr[:len(range_m)]
    del cube

    # Combined ranking inside fixed physical search window (diagnostic comparison)
    ranking, ranking_recommendation, agree_note = _rank_bins(
        mean_profile, phase_std, resp_snr, range_m,
        search_min_m, search_max_m, top_n_bins,
    )

    # Decide which bin to mark on plots
    if manifest_locked_bin is not None:
        plot_bin = manifest_locked_bin
        bin_source = "manifest (step_3)"
    else:
        plot_bin = ranking_recommendation
        bin_source = "range_plot ranking fallback (no manifest locked_bin)"
        print("  WARNING: manifest has no locked_bin — falling back to range_plot ranking.")
        print("           Run step_3/select_chest_bin.py to set the authoritative bin.")

    bin_label = f"locked_bin [{bin_source}]"

    # --- Print ranking table ---
    SEP = "-" * 70
    print(f"\n{SEP}")
    print(f"  Search window: {search_min_m:.2f} – {search_max_m:.2f} m")
    print(f"  {'Rank':>4}  {'Bin':>4}  {'Range':>7}  {'E.rank':>6}  {'Energy(dB)':>10}  "
          f"{'P.rank':>6}  {'Phase σ(rad)':>12}  {'R.rank':>6}  {'Resp SNR(dB)':>12}")
    print(f"  {'-'*4}  {'-'*4}  {'-'*7}  {'-'*6}  {'-'*10}  {'-'*6}  {'-'*12}  "
          f"{'-'*6}  {'-'*12}")
    for e in ranking:
        n   = e["n_methods"]
        tag = f"  ← {n}/3" if n >= 2 else ""
        er  = f"{e['energy_rank']:>6}" if isinstance(e["energy_rank"], int) else f"{'—':>6}"
        pr  = f"{e['phase_rank']:>6}"  if isinstance(e["phase_rank"],  int) else f"{'—':>6}"
        rr  = f"{e['resp_rank']:>6}"   if isinstance(e["resp_rank"],   int) else f"{'—':>6}"
        print(f"  {e['rank']:>4}  {e['bin_idx']:>4}  {e['range_m']:>6.3f}m  "
              f"{er}  {e['energy_db']:>10.1f}  "
              f"{pr}  {e['phase_std_rad']:>12.2f}  "
              f"{rr}  {e['resp_snr_db']:>12.1f}{tag}")
    rec_m = ranking_recommendation * range_resolution_m
    plot_m = plot_bin * range_resolution_m
    print(f"\n  range_plot ranking:  bin {ranking_recommendation}  ({rec_m:.3f} m)  {agree_note}")
    print(f"  Plotted on figures:  bin {plot_bin}  ({plot_m:.3f} m)  [{bin_source}]")
    if manifest_locked_bin is not None and manifest_locked_bin != ranking_recommendation:
        print(f"  NOTE: manifest locked_bin ({manifest_locked_bin}) differs from ranking recommendation ({ranking_recommendation})")
    print(SEP)

    highest_bins_str = ",".join(str(e["bin_idx"]) for e in ranking[1:])

    mean_path = run_dir / "mean_range_profile.png"
    _plot_mean_profile(
        mean_profile, range_m, plot_bin, range_resolution_m,
        search_min_m, search_max_m, max_range_m, mean_path,
        distance_m=distance_m,
        bin_label=bin_label,
    )
    print(f"Saved {mean_path}")

    heatmap_path = run_dir / "range_heatmap.png"
    _plot_heatmap(
        rp_db, range_m, frame_rate_hz, plot_bin, range_resolution_m,
        search_min_m, search_max_m, heatmap_path,
        distance_m=distance_m,
        bin_label=bin_label,
    )
    print(f"Saved {heatmap_path}")

    if no_animation:
        print("Skipping animation (--no-animation)")
    else:
        anim_path = run_dir / "range_animation.mp4"
        _save_animation(
            rp_db, range_m, frame_rate_hz, plot_bin, range_resolution_m,
            search_min_m, search_max_m, max_range_m, anim_path,
            distance_m=distance_m,
            bin_label=bin_label,
        )
        print(f"Saved {anim_path}")

    log_path = run_dir / "range_plot.log"
    _write_diagnostic_log(
        log_path=log_path,
        session_id=session_id,
        posture=posture,
        orientation=orientation,
        bin_paths=bin_paths,
        iq_swap=iq_swap,
        tape_distance_m=distance_m,
        num_frames=num_frames,
        duration_s=duration_s,
        frame_rate_hz=frame_rate_hz,
        range_resolution_m=range_resolution_m,
        search_min_m=search_min_m,
        search_max_m=search_max_m,
        ranking=ranking,
        ranking_recommendation=ranking_recommendation,
        agree_note=agree_note,
        highest_bins_str=highest_bins_str,
        manifest_locked_bin=manifest_locked_bin,
        bin_source=bin_source,
        plot_bin=plot_bin,
    )

    print(f"\nSUMMARY — {session_id}")
    print(f"  Frames:           {num_frames}  ({duration_s:.1f} s)")
    print(f"  Range resolution: {range_resolution_m:.4f} m/bin")
    print(f"  Range bins kept:  {n_range_bins}  (0–{max_range_m} m)")
    print(f"  Plotted bin:      {plot_bin}  ({plot_m:.3f} m)  [{bin_source}]")
    print(f"  Ranking estimate: {ranking_recommendation}  ({rec_m:.3f} m)  {agree_note}")
    print(f"  Figures:          {run_dir}")
    print(f"  Log:              {log_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(config_path: Path) -> None:
    cfg = yaml.safe_load(config_path.read_text())
    np.random.seed(cfg["seed"])

    parser = argparse.ArgumentParser(
        description="range_plot: range-profile plots for a single session from the manifest"
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
        print("\nRun with: python steps/range_plot/run.py --session <EXPID>")
        print("      or: python steps/range_plot/run.py --all")
        sys.exit(0)

    # Single-session path
    _run_session(args.session, manifest, data_raw, cfg, no_animation=args.no_animation)


if __name__ == "__main__":
    main(Path(__file__).resolve().parent / "config.yaml")
