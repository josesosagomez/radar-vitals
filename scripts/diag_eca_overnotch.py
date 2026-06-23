#!/usr/bin/env python3
"""Diagnostic: compare raw / pre-ECA / post-ECA cardiac spectra.

Question
--------
Does the Butterworth bandpass + ECA process destroy a cardiac peak that IS
present in the bandpassed-phase signal?

Three spectra per window (all Hann-windowed, same frequency axis):
  Raw      rfft(phase_clean × hann)           — no filtering, no ECA
  Pre-ECA  rfft(bandpass(phase_clean) × hann) — after Butterworth(0.8–4.0 Hz,
                                                  order=4, filtfilt); matches
                                                  vitals.py line 254 exactly
  Post-ECA heart_spectrum_first_pass (spec1)  — stored in intermediates; after
                                                  ECA removes k×f_r (k=1..k_max)
                                                  with NO cardiac guard

All three are normalised to the pre-ECA max in 0.8–2.0 Hz so the zoom panel
shows shape + relative attenuation on the same log scale.

Decision criteria (stdout table + zoom panels)
----------------------------------------------
  Pre-ECA peak at Masimo AND post-ECA does NOT → ECA over-notching confirmed
  Neither pre nor post has peak at Masimo       → SNR problem before ECA
  Both have peak at Masimo                      → ECA not the problem;
                                                  look at AHET pass criterion

Stdout also prints AHET internal evidence (argmax fallback flag, 2nd-harmonic
ratio, picked candidate) to clarify why AHET passes for bad windows.

Outputs
-------
  Stdout: per-window table + AHET evidence
  figures/diag_eca_overnotch_<session>.png : 2-panel plot per window

Run from repo root:
    python -X utf8 scripts/diag_eca_overnotch.py
    python -X utf8 scripts/diag_eca_overnotch.py --sessions exp003 exp008
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from scipy.signal import butter, filtfilt
from scipy.fft import rfft, rfftfreq

REPO_ROOT    = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src import radar_io, vitals  # noqa: E402

MANIFEST      = REPO_ROOT / "data" / "manifest.local.csv"
DATA_RAW      = REPO_ROOT / "data" / "raw"
RESULTS_BASE  = REPO_ROOT / "results" / "exp_eca_all"
EXP_CFG_PATH  = REPO_ROOT / "experiments" / "exp_eca_all" / "config.yaml"
FIGURES_DIR   = REPO_ROOT / "figures"

CARDIAC_LO_HZ = 0.8
CARDIAC_HI_HZ = 2.0
AHET_DEV_HZ   = 0.1      # ahet_deviation_hz from config
ZOOM_LO_HZ    = 0.60     # zoom panel lower edge (shows 2xf_r in context)
ZOOM_HI_HZ    = 2.10

DEFAULT_SESSIONS = ["exp003", "exp008"]
N_WORST = 4
N_BEST  = 2

HARMONIC_COLORS = {1: "steelblue", 2: "goldenrod", 3: "darkorange",
                   4: "red", 5: "purple", 6: "brown"}


# ---------------------------------------------------------------------------
# Session loading (same pattern as other diag scripts)
# ---------------------------------------------------------------------------

def _latest_run_dir() -> Path:
    runs = sorted(RESULTS_BASE.iterdir())
    if not runs:
        raise FileNotFoundError(f"No runs found under {RESULTS_BASE}")
    return runs[-1]


def _resolve_bin_paths(session_id: str) -> list[Path]:
    for pat in [f"{session_id}_0.bin", f"{session_id}.bin"]:
        p = DATA_RAW / pat
        if p.exists():
            partner = DATA_RAW / f"{session_id}_1.bin"
            return [p, partner] if "_0.bin" in pat and partner.exists() else [p]
    raise FileNotFoundError(f"No .bin for '{session_id}' in {DATA_RAW}")


def _total_frames(bin_paths: list[Path], cfg_r: dict) -> int:
    total_bytes = sum(p.stat().st_size for p in bin_paths)
    bpf = cfg_r["num_chirps_per_frame"] * cfg_r["num_rx"] * cfg_r["num_adc_samples"] * 4
    return total_bytes // bpf


def _run_session(row: pd.Series, cfg: dict) -> list[dict]:
    session_id = str(row["session_id"])
    locked_bin = int(row["locked_bin"])
    trim_s, end_s = (int(x) for x in str(row["stationary_intervals"]).split("-"))

    r             = cfg["radar"]
    fs            = float(r["frame_rate_hz"])
    trim_frames   = int(trim_s * fs)
    end_frames    = int(end_s  * fs)
    window_frames = int(cfg["processing"]["window_s"] * fs)
    hop_frames    = int(cfg["processing"]["hop_s"]    * fs)

    bin_paths = _resolve_bin_paths(session_id)
    n_frames  = _total_frames(bin_paths, r)
    chirp_cfg = radar_io.ChirpConfig(
        num_adc_samples      = r["num_adc_samples"],
        num_rx               = r["num_rx"],
        num_tx               = r["num_tx"],
        num_chirps_per_frame = r["num_chirps_per_frame"],
        num_frames           = n_frames,
        frame_rate_hz        = fs,
        range_resolution_m   = r["range_resolution_m"],
    )
    bin_arg  = bin_paths[0] if len(bin_paths) == 1 else bin_paths
    cube     = radar_io.read_adc_bin(bin_arg, chirp_cfg, trim_frames=trim_frames)
    profiles = radar_io.range_profile(cube);  del cube
    analysis = profiles[trim_frames:end_frames];  del profiles

    raxis  = radar_io.range_axis_m(r["num_adc_samples"], r["range_resolution_m"])
    params = vitals.VitalsParams(fs_hz=fs, gate_min_m=0.5, gate_max_m=2.5)

    window_results = vitals.run_pipeline_locked(
        analysis, raxis, params, window_frames, hop_frames,
        locked_bin        = locked_bin,
        k_max             = cfg["eca"]["k_max"],
        ahet_deviation_hz = cfg["eca"]["ahet_deviation_hz"],
    )
    return window_results


def _load_comparison(session_id: str, run_dir: Path) -> pd.DataFrame:
    csv = run_dir / session_id / "comparison.csv"
    if not csv.exists():
        raise FileNotFoundError(
            f"comparison.csv not found for {session_id}. "
            "Run experiments/exp_eca_all/run.py first."
        )
    return pd.read_csv(csv)


def _merge(window_results: list[dict], comparison: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for o in window_results:
        rows.append({
            "window_index":           o["window_index"],
            "start_frame":            o["start_frame"],
            "hr_bpm":                 o["hr_bpm"],
            "f_r_hz":                 o["f_r_hz_used"],
            "heart_peak_hz":          o["heart_peak_hz"],
            "heart_freqs_hz":         o["heart_freqs_hz"],
            "heart_spectrum":         o["heart_spectrum"],
            "heart_spectrum_fp":      o["heart_spectrum_first_pass"],
            "phase_clean":            o["phase_clean"],
            "ahet_verified":          o["ahet_verified"],
            "candidate_argmax_fb":    bool(o["candidate_argmax_fallback"][0])
                                      if len(o["candidate_argmax_fallback"]) else False,
            "ahet_ratio":             float(o["peak_to_floor_ratio"][0])
                                      if np.isfinite(o["peak_to_floor_ratio"][0]) else float("nan"),
            "cand0_hz":               float(o["candidate_initial_hz"][0])
                                      if np.isfinite(o["candidate_initial_hz"][0]) else float("nan"),
            "ahet_2nd_hz":            float(o["second_peak_bin_hz"][0])
                                      if np.isfinite(o["second_peak_bin_hz"][0]) else float("nan"),
        })
    wdf  = pd.DataFrame(rows)
    comp = comparison[["window_index", "masimo_pr_bpm",
                        "error_bpm", "abs_error_bpm"]].copy()
    return wdf.merge(comp, on="window_index", how="left")


# ---------------------------------------------------------------------------
# Spectrum utilities
# ---------------------------------------------------------------------------

def _bandpass_preeca(phase: np.ndarray, fs: float) -> np.ndarray:
    """Apply the same Butterworth bandpass as vitals.py line 254.

    bp_hi = min(2.0 * CARDIAC_HI_HZ, fs * 0.45) = min(4.0, 9.0) = 4.0 Hz
    order = 4, zero-phase (filtfilt) — same as vitals.bandpass_filter().
    """
    bp_lo = CARDIAC_LO_HZ
    bp_hi = min(2.0 * CARDIAC_HI_HZ, fs * 0.45)
    nyq   = fs / 2.0
    b, a  = butter(4, [bp_lo / nyq, bp_hi / nyq], btype="band")
    return filtfilt(b, a, phase)


def _amplitude_spectrum(signal: np.ndarray, fs: float) -> tuple[np.ndarray, np.ndarray]:
    """Return (freqs_hz, amplitude) with Hann window — matches vitals.py _spec()."""
    n     = len(signal)
    win   = np.hanning(n)
    freqs = rfftfreq(n, d=1.0 / fs)
    amp   = np.abs(rfft(signal * win))
    return freqs, amp.astype(np.float64)


def _interp_amp(freqs: np.ndarray, amp: np.ndarray, target_hz: float) -> float:
    if not np.isfinite(target_hz):
        return float("nan")
    idx = np.searchsorted(freqs, target_hz)
    if idx == 0:
        return float(amp[0])
    if idx >= len(amp):
        return float(amp[-1])
    lo, hi = freqs[idx - 1], freqs[idx]
    t = (target_hz - lo) / (hi - lo) if hi > lo else 0.0
    return float((1 - t) * amp[idx - 1] + t * amp[idx])


def _normalise(amp: np.ndarray, freqs: np.ndarray,
               lo: float, hi: float) -> np.ndarray:
    """Normalise amplitude to the pre-ECA spectrum max in [lo, hi]."""
    mask = (freqs >= lo) & (freqs <= hi)
    mx   = amp[mask].max() if mask.any() else 1.0
    return amp / max(mx, 1e-30)


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def _plot_window_row(
    axes: list[plt.Axes],
    freqs: np.ndarray,
    raw_amp: np.ndarray,
    pre_amp: np.ndarray,
    post_amp: np.ndarray,      # = heart_spectrum_first_pass (spec1)
    heart_freqs: np.ndarray,   # frequency axis for spec1 (same as freqs)
    f_r_hz: float,
    masimo_pr_bpm: float,
    heart_peak_hz: float,
    hr_bpm: float,
    error_bpm: float,
    ahet_verified: bool,
    argmax_fb: bool,
    ahet_ratio: float,
    win_idx: int,
    start_s: float,
    fs: float,
) -> None:
    masimo_hz = masimo_pr_bpm / 60.0 if np.isfinite(masimo_pr_bpm) else float("nan")

    # Normalise all three to pre-ECA max in cardiac band
    ref_max = float(pre_amp[(freqs >= CARDIAC_LO_HZ) & (freqs <= CARDIAC_HI_HZ)].max())
    ref_max = max(ref_max, 1e-30)
    raw_n   = raw_amp  / ref_max
    pre_n   = pre_amp  / ref_max
    # post_amp is spec1 from vitals — use its own frequency axis (heart_freqs)
    post_ref = float(post_amp[(heart_freqs >= CARDIAC_LO_HZ) &
                               (heart_freqs <= CARDIAC_HI_HZ)].max())
    post_ref = max(post_ref, 1e-30)
    post_n  = post_amp / ref_max   # normalise to same reference as pre

    ahet_label = "Y" if ahet_verified else "N"
    fb_label   = "argmax" if argmax_fb else "peak"
    ratio_s    = f"{ahet_ratio:.1f}" if np.isfinite(ahet_ratio) else "nan"
    title = (
        f"win={win_idx}  t={start_s:.0f}s  "
        f"radar={hr_bpm:.1f}  masimo={masimo_pr_bpm:.1f}  err={error_bpm:+.1f} bpm\n"
        f"f_r={f_r_hz:.4f} Hz  cand={fb_label}  ahet={ahet_label}  2nd/floor={ratio_s}"
    )

    ax_full, ax_zoom = axes

    # ---- Panel A: full spectrum 0.1-3 Hz (raw only, context) ----
    mask_full = (freqs >= 0.1) & (freqs <= 3.0)
    ax_full.semilogy(freqs[mask_full], raw_n[mask_full],
                     lw=0.8, color="gray", alpha=0.8, label="raw")
    ax_full.axvspan(0.1, 0.5, alpha=0.06, color="gold",      zorder=0)
    ax_full.axvspan(0.5, CARDIAC_LO_HZ, alpha=0.06, color="salmon", zorder=0)
    ax_full.axvspan(CARDIAC_LO_HZ, CARDIAC_HI_HZ, alpha=0.06,
                    color="dodgerblue", zorder=0)
    ax_full.axvline(CARDIAC_LO_HZ, color="gray", lw=0.8, linestyle="-", alpha=0.5)
    if np.isfinite(masimo_hz):
        ax_full.axvline(masimo_hz, color="green", lw=1.2, linestyle="--",
                        label=f"Masimo {masimo_pr_bpm:.0f}")
    ax_full.axvline(heart_peak_hz, color="red", lw=1.2, linestyle="-",
                    label=f"picked {hr_bpm:.0f}")
    if np.isfinite(f_r_hz) and f_r_hz > 0:
        for k, col in HARMONIC_COLORS.items():
            hz = k * f_r_hz
            if 0.1 <= hz <= 3.1:
                ax_full.axvline(hz, color=col, lw=0.7, linestyle=":",
                                alpha=0.85, label=f"{k}×fr={hz*60:.0f}")
    ax_full.set_xlim(0.1, 3.0)
    ax_full.set_xlabel("Hz", fontsize=7)
    ax_full.set_ylabel("Amplitude (norm, log)", fontsize=7)
    ax_full.set_title(f"Full spectrum 0.1–3 Hz (raw)\n{title}", fontsize=6.5)
    ax_full.tick_params(labelsize=6)
    ax_full.legend(fontsize=4.5, loc="upper right", ncol=2)
    ax_full_bpm = ax_full.secondary_xaxis(
        "top", functions=(lambda hz: hz * 60, lambda bpm: bpm / 60))
    ax_full_bpm.set_xlabel("bpm", fontsize=6)
    ax_full_bpm.tick_params(labelsize=5)

    # ---- Panel B: cardiac band zoom ----
    mask_z = (freqs >= ZOOM_LO_HZ) & (freqs <= ZOOM_HI_HZ)
    mz_h   = (heart_freqs >= ZOOM_LO_HZ) & (heart_freqs <= ZOOM_HI_HZ)

    ax_zoom.semilogy(freqs[mask_z], raw_n[mask_z],
                     lw=0.8, color="gray", alpha=0.4, label="raw")
    ax_zoom.semilogy(freqs[mask_z], pre_n[mask_z],
                     lw=1.6, color="steelblue", alpha=0.9, label="pre-ECA (BP only)")
    ax_zoom.semilogy(heart_freqs[mz_h], post_n[mz_h],
                     lw=1.6, color="darkorange", linestyle="--", alpha=0.9,
                     label="post-ECA (spec1)")

    # Band shading
    ax_zoom.axvspan(ZOOM_LO_HZ, CARDIAC_LO_HZ, alpha=0.08, color="salmon", zorder=0)
    ax_zoom.axvspan(CARDIAC_LO_HZ, CARDIAC_HI_HZ, alpha=0.06,
                    color="dodgerblue", zorder=0)
    ax_zoom.axvline(CARDIAC_LO_HZ, color="gray", lw=1.0, linestyle="-", alpha=0.6)

    # k×f_r harmonics
    if np.isfinite(f_r_hz) and f_r_hz > 0:
        for k, col in HARMONIC_COLORS.items():
            hz = k * f_r_hz
            if ZOOM_LO_HZ <= hz <= ZOOM_HI_HZ * 1.05:
                ax_zoom.axvline(hz, color=col, lw=0.9, linestyle=":",
                                alpha=0.9, label=f"{k}×fr={hz*60:.1f}")

    # Masimo target
    if np.isfinite(masimo_hz):
        ax_zoom.axvline(masimo_hz, color="green", lw=1.5, linestyle="--",
                        label=f"Masimo {masimo_pr_bpm:.0f}")

    # Picked peak
    if np.isfinite(heart_peak_hz):
        ax_zoom.axvline(heart_peak_hz, color="red", lw=1.5, linestyle="-",
                        label=f"picked {hr_bpm:.0f}")

    # AHET 2nd-harmonic search window [2×picked ± AHET_DEV_HZ]
    if np.isfinite(heart_peak_hz) and heart_peak_hz > 0:
        lo2 = max(2.0 * heart_peak_hz - AHET_DEV_HZ, ZOOM_LO_HZ)
        hi2 = min(2.0 * heart_peak_hz + AHET_DEV_HZ, ZOOM_HI_HZ)
        if lo2 < hi2:
            ax_zoom.axvspan(lo2, hi2, alpha=0.18, color="red", zorder=1,
                            label=f"AHET search [{lo2*60:.0f}–{hi2*60:.0f}]")

    ax_zoom.set_xlim(ZOOM_LO_HZ, ZOOM_HI_HZ)
    ax_zoom.set_xlabel("Hz", fontsize=7)
    ax_zoom.set_ylabel("Amplitude (norm to pre-ECA max, log)", fontsize=7)
    ax_zoom.set_title(
        f"Zoom {ZOOM_LO_HZ*60:.0f}–{ZOOM_HI_HZ*60:.0f} bpm\n"
        "gray=raw  blue=pre-ECA  orange=post-ECA(spec1)",
        fontsize=6.5,
    )
    ax_zoom.tick_params(labelsize=6)
    ax_zoom.legend(fontsize=4.5, loc="upper right", ncol=2)
    ax_zoom_bpm = ax_zoom.secondary_xaxis(
        "top", functions=(lambda hz: hz * 60, lambda bpm: bpm / 60))
    ax_zoom_bpm.set_xlabel("bpm", fontsize=6)
    ax_zoom_bpm.tick_params(labelsize=5)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(sessions: list[str]) -> None:
    cfg      = yaml.safe_load(EXP_CFG_PATH.read_text(encoding="utf-8"))
    manifest = pd.read_csv(MANIFEST)
    run_dir  = _latest_run_dir()
    fs       = float(cfg["radar"]["frame_rate_hz"])

    print(f"Latest run dir: {run_dir.name}")
    print(f"Cardiac band: {CARDIAC_LO_HZ}–{CARDIAC_HI_HZ} Hz  "
          f"({CARDIAC_LO_HZ*60:.0f}–{CARDIAC_HI_HZ*60:.0f} bpm)")
    print(f"Pre-ECA bandpass: {CARDIAC_LO_HZ}–{min(2*CARDIAC_HI_HZ, fs*0.45):.1f} Hz"
          f"  (order=4, filtfilt)\n")

    for sid in sessions:
        rows = manifest[manifest["session_id"] == sid]
        if rows.empty:
            print(f"SKIP {sid} — not in manifest");  continue
        row = rows.iloc[0]
        print(f"{'='*70}")
        print(f"  {sid}  |  {row['posture']}  |  {row['distance_cm']} cm  "
              f"|  bin {int(row['locked_bin'])}")
        print(f"{'='*70}")

        try:
            print("  Running pipeline...")
            window_results = _run_session(row, cfg)
            comparison     = _load_comparison(sid, run_dir)
            merged         = _merge(window_results, comparison)
        except Exception as exc:
            print(f"  ERROR: {exc}");  continue

        # Select windows
        fin    = merged.dropna(subset=["masimo_pr_bpm", "error_bpm"])
        fin    = fin.sort_values("abs_error_bpm", ascending=False)
        worst  = fin.head(N_WORST)
        best   = fin.tail(N_BEST)
        sel    = pd.concat([worst, best]).drop_duplicates("window_index")
        sel    = sel.sort_values("abs_error_bpm", ascending=False)

        # ---- Stdout table ----
        hdr = (f"  {'win':>4}  {'t(s)':>5}  {'err':>6}  {'f_r':>6}  "
               f"{'3xfr':>5}  {'4xfr':>5}  | "
               f"{'p_pre@mas':>10}  {'p_post@mas':>11}  {'pre/post':>9}  "
               f"{'ahet':>4}  {'fb':>5}")
        print(f"\n{hdr}")
        print(f"  {'-'*4}  {'-'*5}  {'-'*6}  {'-'*6}  "
              f"{'-'*5}  {'-'*5}  +-{'-'*10}--{'-'*11}--{'-'*9}  "
              f"{'-'*4}  {'-'*5}")

        for _, r in sel.iterrows():
            phase     = r["phase_clean"]
            freqs, raw_amp  = _amplitude_spectrum(phase, fs)
            x_bp            = _bandpass_preeca(phase, fs)
            _,     pre_amp  = _amplitude_spectrum(x_bp, fs)
            heart_freqs     = r["heart_freqs_hz"]
            post_amp        = r["heart_spectrum_fp"]   # spec1

            masimo_hz = float(r["masimo_pr_bpm"]) / 60.0 if np.isfinite(
                float(r["masimo_pr_bpm"])) else float("nan")

            p_pre  = _interp_amp(freqs,       pre_amp,  masimo_hz)
            p_post = _interp_amp(heart_freqs, post_amp, masimo_hz)
            ratio  = p_pre / p_post if (np.isfinite(p_post) and p_post > 0) else float("nan")

            f_r  = float(r["f_r_hz"])
            t_s  = float(r["start_frame"]) / fs
            err  = float(r["error_bpm"])
            ahet = "Y" if r["ahet_verified"] else "N"
            fb   = "argmx" if r["candidate_argmax_fb"] else "peak"
            ah_r = float(r["ahet_ratio"])

            print(
                f"  {int(r['window_index']):>4}  {t_s:>5.0f}  {err:>+6.1f}  "
                f"{f_r:>6.4f}  "
                f"{3*f_r*60:>5.1f}  {4*f_r*60:>5.1f}  | "
                f"{p_pre:>10.2e}  {p_post:>11.2e}  "
                f"{ratio:>9.2f}×  "
                f"{ahet:>4}  {fb:>5}"
            )

        print()

        # ---- Figure ----
        n_win = len(sel)
        fig   = plt.figure(figsize=(16, 4.5 * n_win))
        gs    = gridspec.GridSpec(n_win, 2, figure=fig, hspace=0.60, wspace=0.30)
        fig.suptitle(
            f"{sid}  {row['posture']}  {row['distance_cm']} cm — ECA over-notching diagnostic\n"
            "gray=raw  blue=pre-ECA (BP only)  orange=post-ECA (spec1, first-pass)\n"
            "red shading=AHET 2nd-harmonic search window  green=Masimo  red=picked",
            fontsize=9,
        )

        for i, (_, r) in enumerate(sel.iterrows()):
            phase          = r["phase_clean"]
            freqs, raw_amp = _amplitude_spectrum(phase, fs)
            x_bp           = _bandpass_preeca(phase, fs)
            _,     pre_amp = _amplitude_spectrum(x_bp, fs)
            heart_freqs    = r["heart_freqs_hz"]
            post_amp       = r["heart_spectrum_fp"]
            axes_row       = [fig.add_subplot(gs[i, j]) for j in range(2)]
            t_s            = float(r["start_frame"]) / fs

            _plot_window_row(
                axes         = axes_row,
                freqs        = freqs,
                raw_amp      = raw_amp,
                pre_amp      = pre_amp,
                post_amp     = post_amp,
                heart_freqs  = heart_freqs,
                f_r_hz       = float(r["f_r_hz"]),
                masimo_pr_bpm= float(r["masimo_pr_bpm"]),
                heart_peak_hz= float(r["heart_peak_hz"]),
                hr_bpm       = float(r["hr_bpm"]),
                error_bpm    = float(r["error_bpm"]),
                ahet_verified= bool(r["ahet_verified"]),
                argmax_fb    = bool(r["candidate_argmax_fb"]),
                ahet_ratio   = float(r["ahet_ratio"]),
                win_idx      = int(r["window_index"]),
                start_s      = t_s,
                fs           = fs,
            )

        FIGURES_DIR.mkdir(parents=True, exist_ok=True)
        out = FIGURES_DIR / f"diag_eca_overnotch_{sid}.png"
        fig.savefig(out, dpi=150, bbox_inches="tight")
        plt.close(fig)
        print(f"  Figure saved -> {out}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sessions", nargs="+", default=DEFAULT_SESSIONS,
        help="Session IDs to diagnose (default: exp003 exp008)",
    )
    args = parser.parse_args()
    main(args.sessions)
