"""Is the vital-sign information PRESENT in these captures at all?

A feasibility audit, not an estimator evaluation. It asks the prior question that has to
be settled before any acceptance criterion is worth writing:

    do the 8 existing captures contain enough cardiac and respiratory modulation that
    *some* estimator could recover HR and BR — or are we tuning against data that does
    not carry the signal?

The distinction from `scripts/score_offline.py` matters. That measures how well the
production estimator does. This measures **what any estimator could do at best**, by
looking for energy at the frequency the reference says is true, with every verification
gate removed. Its outputs are ceilings and diagnostics.

Why this may look at the Masimo reference when HANDOFF section 3.5 forbids it
-----------------------------------------------------------------------------
Section 3.5 forbids *choosing a bin because it agrees better with the reference*, and
CLAUDE.md section 4 forbids tuning the algorithm to match the reference. Neither is what
happens here. This script asks "is there measurable energy at the known-true frequency",
which is a **detectability** question; the answer is an upper bound on performance, not a
fitted parameter. That makes it safe for the decision "are these captures usable" and
**unsafe** for anything else. Concretely, and enforced by how the outputs are named:

* NOTHING here may be quoted as accuracy, coverage, or agreement. Those come from
  `scripts/score_offline.py` under the frozen comparator.
* NO threshold, band edge, or bin choice may be derived from these numbers. A gate tuned
  until it admits what the oracle knows is a gate fitted to the reference.
* The `oracle_*` prefix marks every column that used the reference. It is deliberate and
  should stay attached to those numbers wherever they are copied.

Method
------
Per capture, per complete window of the frozen grid, per candidate bin:

1. Production phase extraction (`extract_chest_phase` + `remove_impulse_noise`), the same
   call the real pipeline makes.
2. Linear detrend, Hann window, rFFT. **No bandpass** — the heart band stops at 2.0 Hz and
   the second-harmonic question needs content above it.
3. Reference-anchored (`oracle_*`): peak amplitude within +/- `--tol-hz` of the reference
   frequency, over the median amplitude of the surrounding band, in dB.
4. Reference-free: the band argmax and its own peak-to-median SNR — what an estimator sees
   with no reference at all.

The reference per window comes from `src/comparator.py` (`hr_reference`, `br_reference`),
so the PI gate and the window/reference pairing are the frozen comparator's, not a
reimplementation.

Alignment caveat, and how it is handled
---------------------------------------
`frame0_epoch` is `start_wall_utc`, which is written before capture start and is therefore
approximate (`scripts/score_offline.py` ORIGIN_CAVEAT). A constant offset re-pairs windows
with reference values. Two things make the headline result robust to it:

* the **band argmax is alignment-independent** — only the error against the reference
  moves — so the report scans a range of global offsets and reports the hit-rate curve;
* a **session-median reference** variant is computed, which has no per-window pairing at
  all and so cannot be affected by alignment.

If the nominal, offset-scanned and session-median answers agree, alignment is not what is
limiting the conclusion.

Usage
-----
    python -X utf8 scripts/diagnose_signal_presence.py --all
    python -X utf8 scripts/diagnose_signal_presence.py \\
        --captures results/live_demo/20260728_224902_live_demo_massimo3 --max-windows 4
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from src import masimo as masimo_mod  # noqa: E402
from src.comparator import br_reference, hr_reference  # noqa: E402
from src.m4.window_grid import (  # noqa: E402
    FRAMES_PER_WINDOW,
    build_window_grid,
    n_complete_windows,
)
from src.respiration import extract_chest_phase  # noqa: E402
from src.vitals import refine_freq_hz, remove_impulse_noise  # noqa: E402
from src.warmup_select import derive_candidate_bins  # noqa: E402

import diagnose_bin_drift as bindrift  # noqa: E402
from diagnose_bin_sweep import decode_frame_range, words_per_frame  # noqa: E402

sha256_file = bindrift.sha256_file
get_git_commit = bindrift.get_git_commit
is_tree_clean = bindrift.is_tree_clean
validate_decode_geometry = bindrift.validate_decode_geometry

DEFAULT_CONFIG = REPO_ROOT / "scripts" / "live_demo_config.yaml"

#: Second-harmonic search runs above the heart band, so the floor for it is measured over
#: this wider span rather than the 0.8-2.0 Hz band.
H2_FLOOR_BAND_HZ = (1.6, 4.0)

#: Decoy frequencies sampled per window/band for the oracle-SNR control (see
#: `decoy_fraction`). Every spectrum has peaks above its own median, so an oracle SNR is
#: uninterpretable without knowing what an arbitrary frequency scores on the same
#: spectrum.
N_DECOY = 20


def band_metrics(
    spec: np.ndarray,
    freqs: np.ndarray,
    band: tuple[float, float],
    f_ref_hz: float | None,
    tol_hz: float,
) -> dict:
    """Reference-free band argmax plus, if a reference is given, oracle peak-to-floor.

    `floor` is the median amplitude in `band` — the same peak-to-median convention
    `src/vitals.py` uses for its AHET ratio, so the two are directly comparable, and
    `20 * log10` for the same reason (amplitudes, not powers).
    """
    mask = (freqs >= band[0]) & (freqs <= band[1])
    if not mask.any():
        return {"argmax_hz": None, "peak_snr_db": None, "oracle_snr_db": None,
                "oracle_peak_hz": None, "floor": None}

    idx = np.where(mask)[0]
    sub = spec[idx]
    floor = float(np.median(sub))

    peak_local = int(np.argmax(sub))
    argmax_hz = refine_freq_hz(spec, freqs, int(idx[peak_local]))
    peak_snr_db = _db(float(sub[peak_local]), floor)

    oracle_snr_db = None
    oracle_peak_hz = None
    if f_ref_hz is not None and np.isfinite(f_ref_hz):
        omask = (freqs >= f_ref_hz - tol_hz) & (freqs <= f_ref_hz + tol_hz)
        if omask.any():
            oidx = np.where(omask)[0]
            osub = spec[oidx]
            opeak = int(np.argmax(osub))
            oracle_peak_hz = refine_freq_hz(spec, freqs, int(oidx[opeak]))
            oracle_snr_db = _db(float(osub[opeak]), floor)

    return {
        "argmax_hz": argmax_hz, "peak_snr_db": peak_snr_db,
        "oracle_snr_db": oracle_snr_db, "oracle_peak_hz": oracle_peak_hz,
        "floor": floor,
    }


def decoy_fraction(
    spec: np.ndarray, freqs: np.ndarray, band: tuple[float, float],
    f_ref_hz: float | None, tol_hz: float, rng: np.random.Generator,
    n_decoy: int = N_DECOY,
) -> float | None:
    """Fraction of decoy frequencies whose oracle SNR the TRUE frequency beats.

    The control for `oracle_snr_db`. Decoys are drawn uniformly from the same band and
    scored by the identical statistic on the identical spectrum, so the only difference is
    *where* we looked. ~0.5 means the reference frequency is spectrally unremarkable and
    its oracle SNR is measuring ordinary spectral roughness, not a vital sign.
    """
    if f_ref_hz is None or not np.isfinite(f_ref_hz):
        return None
    true = band_metrics(spec, freqs, band, f_ref_hz, tol_hz)["oracle_snr_db"]
    if true is None:
        return None
    wins = []
    for _ in range(n_decoy):
        f = float(rng.uniform(band[0] + tol_hz, band[1] - tol_hz))
        got = band_metrics(spec, freqs, band, f, tol_hz)["oracle_snr_db"]
        if got is not None:
            wins.append(true > got)
    return round(float(np.mean(wins)), 4) if wins else None


def _db(peak: float, floor: float) -> float | None:
    if not np.isfinite(peak) or not np.isfinite(floor) or floor <= 0.0 or peak <= 0.0:
        return None
    return round(float(20.0 * np.log10(peak / floor)), 3)


def phase_spectrum(cube: np.ndarray, locked_bin: int, cfg: dict) -> tuple[np.ndarray, np.ndarray, float]:
    """Production phase extraction -> detrended, Hann-windowed amplitude spectrum.

    Deliberately NOT bandpassed: the 0.8-2.0 Hz heart band would remove exactly the
    second-harmonic region this audit needs to inspect.
    """
    phase = extract_chest_phase(
        cube, locked_bin=locked_bin,
        method=cfg["phase"]["method"],
        clutter_removal=cfg["phase"].get("clutter_removal", "none"),
    )
    x = remove_impulse_noise(phase, thresh=float(cfg["phase"]["impulse_clip_rad"]))
    n = len(x)
    t = np.arange(n, dtype=float)
    coeffs = np.polyfit(t, x, 1)
    x = x - np.polyval(coeffs, t)
    spec = np.abs(np.fft.rfft(x * np.hanning(n)))
    return spec, np.fft.rfftfreq(n, d=1.0 / float(cfg["session"]["frame_rate_hz"])), float(np.std(x))


def audit_capture(
    capture_dir: Path, cfg: dict, *, tol_hz: float, max_windows: int | None,
    seed: int = 0, verbose: bool = True,
) -> dict:
    rng = np.random.default_rng(seed)
    capture_dir = Path(capture_dir)
    raw_path = capture_dir / "adc_stream.bin"
    run_metadata = json.loads((capture_dir / "run_metadata.json").read_text(encoding="utf-8"))
    capture_id = capture_dir.name
    chirp_cfg = validate_decode_geometry(cfg, run_metadata, capture_id)
    fs = float(chirp_cfg.frame_rate_hz)

    csv = [p for p in capture_dir.glob("*.csv") if p.name != "live_estimates.csv"]
    if len(csv) != 1:
        raise FileNotFoundError(f"{capture_id}: expected exactly one Masimo CSV, found {csv}")
    ref_df = masimo_mod.load_masimo(csv[0])

    bytes_per_frame = words_per_frame(chirp_cfg) * 2
    n_frames = raw_path.stat().st_size // bytes_per_frame
    frame0_epoch = datetime.fromisoformat(run_metadata["start_wall_utc"]).timestamp()
    windows = build_window_grid(int(n_frames), frame0_epoch, fs=fs, frames_per_win=FRAMES_PER_WINDOW)
    if max_windows is not None:
        windows = windows[:max_windows]

    candidate_bins = derive_candidate_bins(cfg)
    locked_bin = run_metadata.get("locked_bin")
    res_m = float(cfg["profile"]["range_resolution_m"])
    hr_band = tuple(cfg["heart"]["band_hz"])
    br_band = tuple(cfg["respiration"]["band_hz"])

    if verbose:
        print(f"  {capture_id}: {len(windows)} windows x {len(candidate_bins)} bins", flush=True)

    raw = np.memmap(raw_path, dtype="<i2", mode="r")
    rows: list[dict] = []
    t0 = time.monotonic()
    try:
        for w in windows:
            hr_ref = hr_reference(ref_df, w.epoch_start, w.epoch_end)
            br_ref = br_reference(ref_df, w.epoch_start, w.epoch_end)
            pr_bpm = hr_ref["median_pr_bpm"]
            rr_bpm = br_ref.get("median_rr_bpm", float("nan"))
            f_hr = pr_bpm / 60.0 if np.isfinite(pr_bpm) else None
            f_br = rr_bpm / 60.0 if np.isfinite(rr_bpm) else None

            cube = decode_frame_range(raw, chirp_cfg, w.frame_start, w.frame_end - w.frame_start)
            for b in candidate_bins:
                spec, freqs, phase_std = phase_spectrum(cube, b, cfg)
                hr_m = band_metrics(spec, freqs, hr_band, f_hr, tol_hz)
                br_m = band_metrics(spec, freqs, br_band, f_br, tol_hz)
                h2_m = band_metrics(
                    spec, freqs, H2_FLOOR_BAND_HZ,
                    (2.0 * f_hr) if f_hr is not None else None, tol_hz,
                )
                rows.append({
                    "capture_id": capture_id, "k": w.k, "bin": b,
                    "range_m": round(b * res_m, 4),
                    "is_locked_bin": bool(locked_bin is not None and b == int(locked_bin)),
                    "phase_std_rad": round(phase_std, 4),
                    "hr_ref_bpm": None if not np.isfinite(pr_bpm) else round(float(pr_bpm), 2),
                    "hr_ref_admitted": bool(hr_ref["admitted"]),
                    "hr_ref_spread_bpm": (
                        None if not np.isfinite(hr_ref["spread_bpm"])
                        else round(float(hr_ref["spread_bpm"]), 2)
                    ),
                    "hr_argmax_bpm": round(hr_m["argmax_hz"] * 60.0, 2) if hr_m["argmax_hz"] else None,
                    "hr_argmax_err_bpm": (
                        round(abs(hr_m["argmax_hz"] * 60.0 - pr_bpm), 2)
                        if hr_m["argmax_hz"] and np.isfinite(pr_bpm) else None
                    ),
                    "hr_peak_snr_db": hr_m["peak_snr_db"],
                    "hr_oracle_snr_db": hr_m["oracle_snr_db"],
                    "h2_oracle_snr_db": h2_m["oracle_snr_db"],
                    "br_ref_bpm": None if not np.isfinite(rr_bpm) else round(float(rr_bpm), 2),
                    "br_argmax_bpm": round(br_m["argmax_hz"] * 60.0, 2) if br_m["argmax_hz"] else None,
                    "br_argmax_err_bpm": (
                        round(abs(br_m["argmax_hz"] * 60.0 - rr_bpm), 2)
                        if br_m["argmax_hz"] and np.isfinite(rr_bpm) else None
                    ),
                    "br_peak_snr_db": br_m["peak_snr_db"],
                    "br_oracle_snr_db": br_m["oracle_snr_db"],
                    "hr_decoy_beat_frac": decoy_fraction(spec, freqs, hr_band, f_hr, tol_hz, rng),
                    "br_decoy_beat_frac": decoy_fraction(spec, freqs, br_band, f_br, tol_hz, rng),
                })
            del cube
            if verbose and (w.k + 1) % 5 == 0:
                print(f"    window {w.k + 1}/{len(windows)} ({time.monotonic() - t0:.0f}s)", flush=True)
    finally:
        del raw

    return {
        "capture_id": capture_id,
        "n_windows": len(windows),
        "candidate_bins": [int(b) for b in candidate_bins],
        "locked_bin": None if locked_bin is None else int(locked_bin),
        "frame0_epoch": frame0_epoch,
        "masimo_csv": csv[0].name,
        "session_median_pr_bpm": round(float(np.nanmedian(ref_df["pr_bpm"].to_numpy(float))), 2),
        "session_median_rr_bpm": round(float(np.nanmedian(ref_df["rr_bpm"].to_numpy(float))), 2),
        "elapsed_s": round(time.monotonic() - t0, 1),
        "rows": rows,
    }


# ── Aggregation ─────────────────────────────────────────────────────────────


def _rate(values: list[bool]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _med(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(statistics.median(vals), 2) if vals else None


def summarise_capture(audit: dict, hit_bpm: float) -> dict:
    """Locked-bin result plus the best-over-bins ceiling, for HR and BR."""
    rows = audit["rows"]
    by_bin: dict[int, list[dict]] = {}
    for r in rows:
        by_bin.setdefault(r["bin"], []).append(r)

    def bin_stats(cells: list[dict]) -> dict:
        return {
            "n_windows": len(cells),
            "hr_hit_rate": _rate([
                c["hr_argmax_err_bpm"] <= hit_bpm for c in cells
                if c["hr_argmax_err_bpm"] is not None
            ]),
            "hr_oracle_snr_db_median": _med([c["hr_oracle_snr_db"] for c in cells]),
            "hr_peak_snr_db_median": _med([c["hr_peak_snr_db"] for c in cells]),
            "h2_oracle_snr_db_median": _med([c["h2_oracle_snr_db"] for c in cells]),
            "br_hit_rate": _rate([
                c["br_argmax_err_bpm"] <= hit_bpm for c in cells
                if c["br_argmax_err_bpm"] is not None
            ]),
            "br_oracle_snr_db_median": _med([c["br_oracle_snr_db"] for c in cells]),
            "br_peak_snr_db_median": _med([c["br_peak_snr_db"] for c in cells]),
        }

    per_bin = {b: bin_stats(cells) for b, cells in by_bin.items()}
    locked = audit["locked_bin"]

    def best_by(metric: str) -> tuple[int | None, float | None]:
        scored = [(b, s[metric]) for b, s in per_bin.items() if s[metric] is not None]
        if not scored:
            return None, None
        b, v = max(scored, key=lambda kv: kv[1])
        return int(b), v

    best_hr_bin, best_hr = best_by("hr_hit_rate")
    best_br_bin, best_br = best_by("br_hit_rate")

    return {
        "capture_id": audit["capture_id"],
        "n_windows": audit["n_windows"],
        "locked_bin": locked,
        "locked": per_bin.get(locked),
        "ceiling_hr_bin": best_hr_bin, "ceiling_hr_hit_rate": best_hr,
        "ceiling_br_bin": best_br_bin, "ceiling_br_hit_rate": best_br,
        "per_bin": per_bin,
    }


def _argmax_matrix(audit: dict, argmax_col: str, ref_col: str):
    """(bins, ks, A[bin, window] argmax, R[window] reference) for one band."""
    rows = audit["rows"]
    bins = sorted({r["bin"] for r in rows})
    ref = {r["k"]: r[ref_col] for r in rows if r[ref_col] is not None}
    ks = sorted(ref)
    A = np.full((len(bins), len(ks)), np.nan)
    for r in rows:
        if r["k"] in ref and r[argmax_col] is not None:
            A[bins.index(r["bin"]), ks.index(r["k"])] = r[argmax_col]
    return bins, ks, A, np.array([ref[k] for k in ks], dtype=float)


def tracking_tests(
    audit: dict, band: str, hit_bpm: float, n_perm: int, seed: int
) -> dict:
    """Does the radar TRACK the reference, or merely emit a plausible constant?

    Three separate things are needed before a hit rate means anything:

    * **permutation null** — the reported ceiling is a max over 14 bins, which is
      optimistically biased on its own. Shuffling which window each reference value pairs
      with destroys the time association while preserving both marginals and the
      max-over-bins selection, so `p` is the fraction of shuffles that reach the observed
      ceiling.
    * **constant baseline** — a predictor that ignores the radar and always emits the
      session-median reference. When the within-session spread is smaller than the hit
      tolerance this scores near 100%, and hit rate becomes incapable of demonstrating
      anything. This is a property of the CAPTURE, not of the estimator.
    * **Spearman correlation** — tolerance-free, so it is not hostage to either. Reported
      at the locked bin (a single fixed bin, no selection, so its p is usable as-is)
      and at the best bin (max over 14, so its p is NOT — multiplicity uncorrected).
    """
    argmax_col, ref_col = f"{band}_argmax_bpm", f"{band}_ref_bpm"
    bins, ks, A, R = _argmax_matrix(audit, argmax_col, ref_col)
    if len(ks) < 3 or not np.isfinite(A).any():
        return {"n_windows": len(ks), "insufficient_data": True}

    rng = np.random.default_rng(seed)

    def max_hit(order: np.ndarray) -> float:
        with np.errstate(invalid="ignore"):
            return float(np.nanmax((np.abs(A[:, order] - R[None, :]) <= hit_bpm).mean(axis=1)))

    observed = max_hit(np.arange(len(ks)))
    null = np.array([max_hit(rng.permutation(len(ks))) for _ in range(n_perm)])

    locked = audit["locked_bin"]
    li = bins.index(locked) if locked in bins else None
    locked_hit = (
        float(np.nanmean(np.abs(A[li] - R) <= hit_bpm)) if li is not None else None
    )

    spread = float(np.percentile(R, 90) - np.percentile(R, 10))
    constant = float(np.mean(np.abs(np.median(R) - R) <= hit_bpm))

    rho_locked = p_locked = None
    if li is not None:
        rho_locked, p_locked = _spearman(A[li], R)
    best_rho, best_p, best_bin = None, None, None
    for i, b in enumerate(bins):
        rho, p = _spearman(A[i], R)
        if rho is not None and (best_rho is None or rho > best_rho):
            best_rho, best_p, best_bin = rho, p, b

    return {
        "n_windows": len(ks),
        "ceiling_hit_rate": round(observed, 4),
        "locked_hit_rate": None if locked_hit is None else round(locked_hit, 4),
        "null_median_hit_rate": round(float(np.median(null)), 4),
        "null_p95_hit_rate": round(float(np.percentile(null, 95)), 4),
        "permutation_p": round(float((null >= observed).mean()), 4),
        "constant_baseline_hit_rate": round(constant, 4),
        "reference_spread_p10_p90_bpm": round(spread, 2),
        "spread_below_tolerance": bool(spread <= hit_bpm),
        "spearman_locked": None if rho_locked is None else round(rho_locked, 3),
        "spearman_locked_p": None if p_locked is None else round(p_locked, 4),
        "spearman_best_bin": best_bin,
        "spearman_best": None if best_rho is None else round(best_rho, 3),
        "spearman_best_p_uncorrected": None if best_p is None else round(best_p, 4),
        "spearman_best_p_bonferroni": (
            None if best_p is None else round(min(1.0, best_p * len(bins)), 4)
        ),
    }


def _spearman(a: np.ndarray, b: np.ndarray) -> tuple[float | None, float | None]:
    from scipy import stats

    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 3:
        return None, None
    rho, p = stats.spearmanr(a[ok], b[ok])
    if not np.isfinite(rho):
        return None, None
    return float(rho), float(p)


def decoy_verdict(audit: dict, band: str) -> dict:
    """Sign test on the locked bin: does the true frequency beat decoys more than half the time?"""
    from scipy import stats

    fracs = [
        r[f"{band}_decoy_beat_frac"] for r in audit["rows"]
        if r["is_locked_bin"] and r[f"{band}_decoy_beat_frac"] is not None
    ]
    if not fracs:
        return {"n": 0}
    above = sum(1 for f in fracs if f > 0.5)
    return {
        "n": len(fracs),
        "mean_beat_fraction": round(float(np.mean(fracs)), 4),
        "n_windows_above_half": above,
        "sign_test_p": round(
            float(stats.binomtest(above, len(fracs), 0.5, alternative="greater").pvalue), 4
        ),
    }


def offset_scan(audit: dict, ref_df, hit_bpm: float, offsets_s: list[float]) -> list[dict]:
    """Hit rate at the locked bin as a function of a global reference time offset.

    The band argmax does not move with the offset — only which reference value it is
    compared against does — so this isolates alignment error from signal presence.
    """
    locked = audit["locked_bin"]
    cells = [r for r in audit["rows"] if r["bin"] == locked]
    frame0 = audit["frame0_epoch"]
    out = []
    for off in offsets_s:
        hr_hits, br_hits = [], []
        for c in cells:
            lo = frame0 + off + c["k"] * FRAMES_PER_WINDOW / 20.0
            hi = lo + FRAMES_PER_WINDOW / 20.0
            hr_ref = hr_reference(ref_df, lo, hi)
            br_ref = br_reference(ref_df, lo, hi)
            pr, rr = hr_ref["median_pr_bpm"], br_ref.get("median_rr_bpm", float("nan"))
            if c["hr_argmax_bpm"] is not None and np.isfinite(pr):
                hr_hits.append(abs(c["hr_argmax_bpm"] - pr) <= hit_bpm)
            if c["br_argmax_bpm"] is not None and np.isfinite(rr):
                br_hits.append(abs(c["br_argmax_bpm"] - rr) <= hit_bpm)
        out.append({"offset_s": off, "hr_hit_rate": _rate(hr_hits), "br_hit_rate": _rate(br_hits)})
    return out


def session_median_variant(audit: dict, hit_bpm: float) -> dict:
    """Hit rate against the session-median reference — immune to window/reference pairing."""
    locked = audit["locked_bin"]
    cells = [r for r in audit["rows"] if r["bin"] == locked]
    pr, rr = audit["session_median_pr_bpm"], audit["session_median_rr_bpm"]
    return {
        "hr_hit_rate": _rate([
            abs(c["hr_argmax_bpm"] - pr) <= hit_bpm for c in cells
            if c["hr_argmax_bpm"] is not None
        ]),
        "br_hit_rate": _rate([
            abs(c["br_argmax_bpm"] - rr) <= hit_bpm for c in cells
            if c["br_argmax_bpm"] is not None
        ]),
    }


ROW_COLUMNS = [
    "capture_id", "k", "bin", "range_m", "is_locked_bin", "phase_std_rad",
    "hr_ref_bpm", "hr_ref_admitted", "hr_ref_spread_bpm", "hr_argmax_bpm",
    "hr_argmax_err_bpm", "hr_peak_snr_db", "hr_oracle_snr_db", "hr_decoy_beat_frac",
    "h2_oracle_snr_db", "br_ref_bpm", "br_argmax_bpm", "br_argmax_err_bpm",
    "br_peak_snr_db", "br_oracle_snr_db", "br_decoy_beat_frac",
]


def render_report(meta: dict, summaries: list[dict], scans: dict, medians: dict) -> str:
    hit = meta["hit_tolerance_bpm"]
    lines = [
        "# Signal-presence audit — can HR and BR be extracted from these captures at all?",
        "",
        f"Generated {meta['generated_utc']} — `scripts/diagnose_signal_presence.py`",
        f"Git `{meta['git_commit'][:12]}` (clean: {meta['git_tree_clean']}), config "
        f"`{meta['config_path']}`, seed {meta['seed']}, tolerance ±{hit:.0f} bpm, "
        f"oracle search ±{meta['tol_hz']:.3f} Hz.",
        "",
        "> **These are ceilings, not results.** Every `oracle_*` number used the Masimo",
        "> reference to say where to look, and every verification gate is switched off.",
        "> Nothing here is accuracy, coverage or agreement, and no threshold, band edge or",
        "> bin choice may be derived from it (HANDOFF §3.5, CLAUDE.md §4).",
        "",
        "## READ THIS BEFORE THE TABLES — hit rates are misleading on this data",
        "",
        "The within-session reference spread is often **narrower than the hit tolerance**, so a",
        "predictor that ignores the radar and emits the session-median value scores near 100%.",
        "Where `constant baseline` >= `ceiling`, the hit rate cannot demonstrate anything and",
        "only the permutation p, the Spearman correlation and the decoy control carry",
        "information. Read those columns first.",
        "",
        "## Tracking tests — does the radar follow the reference, or emit a plausible constant?",
        "",
        "`ceiling` is the best of 14 bins chosen using the reference; `null` is the same",
        "statistic with the window/reference pairing shuffled, so `p` already accounts for that",
        "selection. Spearman is tolerance-free; the locked-bin p is a single fixed bin",
        "and usable as-is, the best-bin p is a max over 14 and shown Bonferroni-corrected.",
        "",
        "| capture | band | n | locked | ceiling | null p50 | perm p | constant | ref spread | ρ locked (p) | ρ best (p corr) |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        cid = s["capture_id"]
        for band in ("br", "hr"):
            t = meta["tracking"][cid][band]
            if t.get("insufficient_data"):
                continue
            flag = " ⚠" if t["constant_baseline_hit_rate"] >= t["ceiling_hit_rate"] else ""
            lines.append(
                "| {cid} | {band} | {n} | {lk} | {ceil} | {nul} | {p:.3f} | {const}{flag} | "
                "{spread:.1f} | {rl} ({pl}) | {rb} ({pb}) |".format(
                    cid=cid.split("_")[-1], band=band.upper(), n=t["n_windows"],
                    lk=_pct(t["locked_hit_rate"]), ceil=_pct(t["ceiling_hit_rate"]),
                    nul=_pct(t["null_median_hit_rate"]), p=t["permutation_p"],
                    const=_pct(t["constant_baseline_hit_rate"]), flag=flag,
                    spread=t["reference_spread_p10_p90_bpm"],
                    rl=_sig(t["spearman_locked"]), pl=_pv(t["spearman_locked_p"]),
                    rb=_sig(t["spearman_best"]), pb=_pv(t["spearman_best_p_bonferroni"]),
                )
            )
    lines += [
        "",
        "⚠ = the constant baseline matches or beats the radar ceiling; the hit rate is",
        "uninformative for that capture and band.",
        "",
        "## Decoy control — is the reference frequency spectrally special at all?",
        "",
        "Same oracle statistic evaluated at random decoy frequencies on the same spectra.",
        "`beat fraction` is how often the TRUE frequency outscores a decoy; 0.5 means the",
        "reference frequency is spectrally unremarkable. Sign test over windows, locked bin.",
        "",
        "| capture | BR beat frac | BR sign p | HR beat frac | HR sign p |",
        "|---|---|---|---|---|",
    ]
    for s in summaries:
        cid = s["capture_id"]
        br, hr = meta["decoy"][cid]["br"], meta["decoy"][cid]["hr"]
        lines.append(
            "| {cid} | {bf} | {bp} | {hf} | {hp} |".format(
                cid=cid.split("_")[-1],
                bf=_frac(br.get("mean_beat_fraction")), bp=_pv(br.get("sign_test_p")),
                hf=_frac(hr.get("mean_beat_fraction")), hp=_pv(hr.get("sign_test_p")),
            )
        )

    lines += [
        "",
        "## Spectral evidence at the locked bin (medians over windows)",
        "",
        "`oracle SNR` = peak amplitude within the search window of the reference frequency,",
        "over the median amplitude of the band, in dB. `peak SNR` is the same for the band",
        "argmax and uses no reference. `H2 oracle SNR` tests AHET's premise directly: is",
        "there energy at twice the true heart rate?",
        "",
        "| capture | BR oracle SNR | BR peak SNR | HR oracle SNR | HR peak SNR | H2 oracle SNR | phase std rad |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lk = s["locked"] or {}
        lines.append(
            "| {cid} | {a} | {b} | {c} | {d} | {e} | {f} |".format(
                cid=s["capture_id"].split("_")[-1],
                a=_dbs(lk.get("br_oracle_snr_db_median")), b=_dbs(lk.get("br_peak_snr_db_median")),
                c=_dbs(lk.get("hr_oracle_snr_db_median")), d=_dbs(lk.get("hr_peak_snr_db_median")),
                e=_dbs(lk.get("h2_oracle_snr_db_median")),
                f=meta["phase_std_by_capture"].get(s["capture_id"], "-"),
            )
        )

    lines += [
        "",
        "## Alignment robustness",
        "",
        "`frame0_epoch` is approximate. The band argmax does not move with a global offset —",
        "only the reference it is compared against does — so a flat curve here means the",
        "conclusion is not alignment-limited. The session-median column pairs nothing at all.",
        "",
        "| capture | HR hit @nominal | HR hit @best offset | best offset s | HR hit vs session median |",
        "|---|---|---|---|---|",
    ]
    for s in summaries:
        cid = s["capture_id"]
        scan = scans[cid]
        nominal = next((x for x in scan if x["offset_s"] == 0.0), {})
        rated = [x for x in scan if x["hr_hit_rate"] is not None]
        best = max(rated, key=lambda x: x["hr_hit_rate"]) if rated else {}
        lines.append(
            "| {cid} | {n} | {b} | {o} | {m} |".format(
                cid=cid.split("_")[-1], n=_pct(nominal.get("hr_hit_rate")),
                b=_pct(best.get("hr_hit_rate")), o=best.get("offset_s", "-"),
                m=_pct(medians[cid]["hr_hit_rate"]),
            )
        )
    return "\n".join(lines) + "\n"


def _pct(value) -> str:
    return "-" if value is None else f"{value:.0%}"


def _sig(value) -> str:
    return "-" if value is None else f"{value:+.2f}"


def _pv(value) -> str:
    return "-" if value is None else f"{value:.3f}"


def _frac(value) -> str:
    return "-" if value is None else f"{value:.2f}"


def _dbs(value) -> str:
    return "-" if value is None else f"{value:+.1f} dB"


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    def cell(value) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "1" if value else "0"
        text = str(value)
        return '"' + text.replace('"', '""') + '"' if any(c in text for c in ',"\n') else text

    lines = [",".join(columns)] + [",".join(cell(r.get(c)) for c in columns) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--captures", nargs="*", default=[], type=Path)
    ap.add_argument("--all", action="store_true", help="audit every capture in results/live_demo/")
    ap.add_argument("--config", default=DEFAULT_CONFIG, type=Path)
    ap.add_argument("--out", default=REPO_ROOT / "results" / "diagnose" / "signal_presence", type=Path)
    ap.add_argument("--tol-hz", type=float, default=0.05,
                    help="half-width of the oracle peak search around the reference frequency")
    ap.add_argument("--hit-bpm", type=float, default=5.0,
                    help="argmax counts as a hit within this many bpm of the reference")
    ap.add_argument("--max-windows", type=int, default=None)
    ap.add_argument("--offsets", nargs="*", type=float,
                    default=[-60, -30, -15, -5, 0, 5, 15, 30, 60])
    ap.add_argument("--n-perm", type=int, default=2000,
                    help="permutations for the tracking null")
    args = ap.parse_args(argv)

    captures = list(args.captures)
    if args.all:
        captures = sorted((REPO_ROOT / "results" / "live_demo").glob("*"))
    if not captures:
        ap.error("give --captures or --all")

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    np.random.seed(int(cfg.get("seed", 0)))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output -> {out_dir}", flush=True)

    seed = int(cfg.get("seed", 0))
    audits, summaries, all_rows = [], [], []
    scans, medians, phase_std, tracking, decoy = {}, {}, {}, {}, {}
    for capture_dir in captures:
        capture_dir = Path(capture_dir)
        print(f"Auditing {capture_dir.name} ...", flush=True)
        audit = audit_capture(
            capture_dir, cfg, tol_hz=args.tol_hz, max_windows=args.max_windows, seed=seed
        )
        csv = [p for p in capture_dir.glob("*.csv") if p.name != "live_estimates.csv"][0]
        ref_df = masimo_mod.load_masimo(csv)
        cid = audit["capture_id"]
        audits.append(audit)
        all_rows.extend(audit["rows"])
        summaries.append(summarise_capture(audit, args.hit_bpm))
        scans[cid] = offset_scan(audit, ref_df, args.hit_bpm, args.offsets)
        medians[cid] = session_median_variant(audit, args.hit_bpm)
        tracking[cid] = {
            band: tracking_tests(audit, band, args.hit_bpm, args.n_perm, seed)
            for band in ("br", "hr")
        }
        decoy[cid] = {band: decoy_verdict(audit, band) for band in ("br", "hr")}
        locked_cells = [r for r in audit["rows"] if r["is_locked_bin"]]
        phase_std[cid] = _med([c["phase_std_rad"] for c in locked_cells])

    meta = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "script": "scripts/diagnose_signal_presence.py",
        "git_commit": get_git_commit(),
        "git_tree_clean": is_tree_clean(),
        "config_path": str(Path(args.config).relative_to(REPO_ROOT)),
        "seed": int(cfg.get("seed", 0)),
        "tol_hz": args.tol_hz,
        "hit_tolerance_bpm": args.hit_bpm,
        "max_windows": args.max_windows,
        "offsets_s": args.offsets,
        "is_feasibility_ceiling_not_a_result": True,
        "n_perm": args.n_perm,
        "n_decoy": N_DECOY,
        "phase_std_by_capture": phase_std,
        "tracking": tracking,
        "decoy": decoy,
        "captures": [{k: v for k, v in a.items() if k != "rows"} for a in audits],
        "summaries": summaries,
        "offset_scans": scans,
        "session_median_variant": medians,
    }
    (out_dir / "run_meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8", newline="\n",
    )
    write_csv(out_dir / "windows.csv", all_rows, ROW_COLUMNS)
    (out_dir / "report.md").write_text(
        render_report(meta, summaries, scans, medians), encoding="utf-8", newline="\n"
    )
    print(f"\nWrote {out_dir}", flush=True)


if __name__ == "__main__":
    main()
