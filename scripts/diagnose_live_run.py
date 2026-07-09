"""Post-mortem diagnostic for a single ``live_demo.py`` run folder.

Point it at one ``results/<ts>_<mode>_<session>/`` directory and it answers,
with evidence, *why heart rate was blank* and *whether warmup locked a good
range bin* -- without hand-loading the four artifacts the demo writes.

Inputs (all optional-degrading -- a hard-killed run with no NPZ still reports):
    run_metadata.json          run context, git commit, config, packet stats
    warmup_bin_selection.json  per-candidate warmup scoring (the lock decision)
    live_estimates.csv         per-hop HR/BR + validity + rejection reason
    live_intermediates.npz     per-hop phase + heart/resp spectra + candidates

This script is READ-ONLY with respect to the run folder and data/raw. It writes
only PNGs, into ``<run_dir>/diagnosis/`` (override with --out).

Usage
-----
    python -X utf8 scripts/diagnose_live_run.py <run_dir> [--worst K]
        [--window-idx N] [--no-plots] [--out DIR]

The CSV/NPZ are appended in lockstep, one record per DSP hop in the same order
(scripts/live_demo.py:_process_dsp_hop), so row ``i`` of the CSV corresponds to
index ``i`` of every NPZ array. On a hard crash the NPZ may be shorter than the
CSV (it is only checkpointed every ``intermediates_checkpoint_windows`` hops);
the two are aligned from the start and the shortfall is reported.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]

# Mirror of scripts/live_demo.py:_REJ_CODE_NAMES (AHET candidate rejection
# codes). Kept in sync deliberately rather than imported, so this diagnostic
# has no heavy import-time coupling to the live demo module.
REJ_CODE_NAMES = {
    -1: "(none)", 0: "passed",
    1: "no_second_harmonic_region", 2: "ratio_db_low",
    3: "prominence_low", 4: "low_candidate_competitor",
    5: "not_attempted", 6: "peak_to_floor_db_low",
    7: "low_candidate_floor_db_low",
}
# Rejection reasons that mean "a candidate peak existed but a confidence gate
# rejected it" (as opposed to no peak at all). A run dominated by these points
# at the AHET verifier being too strict for the session's SNR.
GATE_REJECTIONS = {
    "ratio_db_low", "prominence_low", "peak_to_floor_db_low",
    "low_candidate_floor_db_low", "low_candidate_competitor",
}

# ── Heuristic thresholds (documented, not magic) ────────────────────────────
# Warmup score gap (winner minus runner-up) below which the lock is "thin".
# Scoring in live_demo._run_warmup_selection weights hr_valid at +1000 and a
# medium-vs-low BR swing at ~200, so a gap under this means the runner-up was
# essentially as good on the DSP evidence.
THIN_MARGIN_SCORE = 200.0
# Below this fraction of HR-valid hops the run is treated as "mostly blank".
LOW_VALID_FRAC = 0.5
# Phase-motion flag: a window's phase_clean peak-to-peak above
# median + PHASE_MAD_K * MAD (robust, run-relative -- no absolute magic value).
PHASE_MAD_K = 4.0
# Fallback peak-to-floor floor (dB) when the run metadata does not carry the
# configured candidate_min_peak_to_floor_db.
DEFAULT_PEAK_TO_FLOOR_DB = 6.0


# ── Loading ─────────────────────────────────────────────────────────────────

@dataclass
class RunData:
    run_dir: Path
    metadata: dict = field(default_factory=dict)
    warmup: Optional[dict] = None          # None => warmup JSON absent
    df: Optional[pd.DataFrame] = None       # None => CSV absent/empty
    npz: dict[str, np.ndarray] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def n_hops(self) -> int:
        return 0 if self.df is None else int(len(self.df))


def _read_json(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_run(run_dir: Path) -> RunData:
    """Load whatever of the four artifacts is present; never raise on a
    missing or truncated file -- record it in ``missing``/``notes`` instead."""
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise FileNotFoundError(f"Not a run directory: {run_dir}")

    rd = RunData(run_dir=run_dir)

    meta = _read_json(run_dir / "run_metadata.json")
    if meta is None:
        rd.missing.append("run_metadata.json")
    else:
        rd.metadata = meta

    warm = _read_json(run_dir / "warmup_bin_selection.json")
    if warm is None:
        rd.missing.append("warmup_bin_selection.json")
    else:
        rd.warmup = warm

    csv_path = run_dir / "live_estimates.csv"
    if csv_path.is_file():
        try:
            df = pd.read_csv(csv_path)
            rd.df = df if len(df) else None
            if rd.df is None:
                rd.notes.append("live_estimates.csv present but has no rows.")
        except (pd.errors.ParserError, OSError, ValueError):
            rd.missing.append("live_estimates.csv (unreadable)")
    else:
        rd.missing.append("live_estimates.csv")

    npz_path = run_dir / "live_intermediates.npz"
    if npz_path.is_file():
        try:
            with np.load(npz_path, allow_pickle=True) as z:
                rd.npz = {k: z[k] for k in z.files}
        except (OSError, ValueError, EOFError):
            rd.missing.append("live_intermediates.npz (unreadable)")
    else:
        rd.missing.append("live_intermediates.npz")

    # Alignment check: NPZ arrays are per-hop, positionally matched to CSV rows.
    if rd.df is not None and rd.npz:
        npz_len = _npz_len(rd.npz)
        if npz_len is not None and npz_len < rd.n_hops:
            rd.notes.append(
                f"NPZ has {npz_len} hops vs {rd.n_hops} CSV rows -- likely a hard "
                f"crash between checkpoints; per-window plots cover the first "
                f"{npz_len} hops only (CSV-based triage still covers all rows)."
            )
    return rd


def _npz_len(npz: dict[str, np.ndarray]) -> Optional[int]:
    for key in ("elapsed_s", "frame_idx", "hr_raw"):
        arr = npz.get(key)
        if arr is not None and arr.ndim >= 1:
            return int(arr.shape[0])
    return None


# ── Config accessors (thresholds come from the run's own embedded config) ────

def _cfg(rd: RunData) -> dict:
    return rd.metadata.get("config", {}) if rd.metadata else {}


def peak_to_floor_floor_db(rd: RunData) -> float:
    heart = _cfg(rd).get("heart", {})
    val = heart.get("candidate_min_peak_to_floor_db")
    try:
        return float(val)
    except (TypeError, ValueError):
        return DEFAULT_PEAK_TO_FLOOR_DB


# ── Analysis: warmup lock quality ───────────────────────────────────────────

def analyze_lock(rd: RunData) -> dict:
    """Assess the warmup bin lock. Returns a dict with flags + evidence.

    ``status`` is one of: "manual" (lock preset, no warmup scan), "absent"
    (warmup expected but JSON missing), or "scanned".
    """
    src = (rd.metadata or {}).get("locked_bin_source")
    if rd.warmup is None:
        if src in ("manual", "manifest"):
            return {"status": "manual", "locked_bin_source": src,
                    "locked_bin": (rd.metadata or {}).get("locked_bin"),
                    "flags": []}
        return {"status": "absent", "locked_bin_source": src, "flags": []}

    w = rd.warmup
    cands = list(w.get("candidates", []))
    scored = [c for c in cands if c.get("score") is not None]
    scored.sort(key=lambda c: c["score"], reverse=True)

    selected_bin = w.get("selected_bin")
    confidence = w.get("selected_confidence")
    flags: list[dict] = []

    # Thin margin between the top two scored candidates.
    margin = None
    if len(scored) >= 2:
        margin = float(scored[0]["score"] - scored[1]["score"])
        if margin < THIN_MARGIN_SCORE:
            flags.append({
                "flag": "thin_margin",
                "detail": (
                    f"winner bin {scored[0]['bin']} (score {scored[0]['score']}) "
                    f"beat bin {scored[1]['bin']} (score {scored[1]['score']}) "
                    f"by only {margin:.0f}"
                ),
            })

    # A non-selected candidate produced a valid HR while the winner did not.
    sel = next((c for c in cands if c.get("bin") == selected_bin), None)
    sel_hr_valid = bool(sel.get("hr_valid")) if sel else False
    better = [
        c for c in cands
        if c.get("bin") != selected_bin and bool(c.get("hr_valid"))
    ]
    if not sel_hr_valid and better:
        flags.append({
            "flag": "better_neighbor",
            "detail": (
                "selected bin had no valid HR but "
                + ", ".join(f"bin {c['bin']}" for c in better)
                + " did -- consider pinning locked_bin to one of these"
            ),
        })

    # Selected bin at the edge of the scanned candidate range (subject may be
    # outside the distance gate).
    bins = [c.get("bin") for c in cands if c.get("bin") is not None]
    if bins and selected_bin in (min(bins), max(bins)) and len(bins) > 1:
        flags.append({
            "flag": "edge_lock",
            "detail": (
                f"selected bin {selected_bin} is at the edge of the scanned "
                f"range [{min(bins)}, {max(bins)}] -- the true chest bin may be "
                f"outside the configured distance gate"
            ),
        })

    if confidence == "low":
        flags.append({
            "flag": "low_confidence",
            "detail": f"warmup reported confidence '{confidence}'",
        })

    return {
        "status": "scanned",
        "selected_bin": selected_bin,
        "selected_range_m": w.get("selected_range_m"),
        "confidence": confidence,
        "selection_reason": w.get("selection_reason"),
        "margin": margin,
        "candidates": scored,
        "flags": flags,
    }


# ── Analysis: HR availability timeline ──────────────────────────────────────

def _valid_series(df: pd.DataFrame) -> np.ndarray:
    return df["hr_valid"].fillna(0).astype(int).to_numpy()


def analyze_hr_timeline(df: pd.DataFrame) -> dict:
    n = int(len(df))
    valid = _valid_series(df)
    n_valid = int(valid.sum())
    valid_frac = n_valid / n if n else 0.0

    ahet = (
        df.get("ahet_verified", pd.Series(dtype=float)).fillna(0).astype(int)
    )
    ahet_frac = float(ahet.sum()) / n if n else 0.0

    # Dominant rejection reason among HR-invalid hops.
    invalid = df[valid == 0]
    reasons = (
        invalid.get("candidate_rejection_reason", pd.Series(dtype=object))
        .fillna("")
        .replace("", "(none)")
        .astype(str)
    )
    reason_counts = reasons.value_counts().to_dict()
    dominant_reason = next(iter(reason_counts), None)

    stage = df.get("spectrum_stage", pd.Series(dtype=float)).dropna()
    stage_counts = (
        stage.astype(int).value_counts().sort_index().to_dict() if len(stage) else {}
    )

    return {
        "n_hops": n,
        "n_valid": n_valid,
        "valid_frac": valid_frac,
        "ahet_frac": ahet_frac,
        "reason_counts": reason_counts,
        "dominant_reason": dominant_reason,
        "stage_counts": stage_counts,
        "timeline": "".join("#" if v else "." for v in valid),
    }


# ── Analysis: signal level (NPZ) ────────────────────────────────────────────

def analyze_signal(npz: dict[str, np.ndarray], floor_db: float) -> dict:
    out: dict[str, Any] = {"available": False}
    if not npz:
        return out
    out["available"] = True

    ptf = npz.get("peak_to_floor_ratio_db")
    if ptf is not None and ptf.size:
        # peak_to_floor is per-candidate; take the best (max) candidate per hop.
        arr = np.asarray(ptf, dtype=float)
        with np.errstate(all="ignore"):
            per_hop = np.nanmax(arr, axis=1) if arr.ndim == 2 else arr
        per_hop = per_hop[np.isfinite(per_hop)]
        if per_hop.size:
            out["ptf_median_db"] = float(np.median(per_hop))
            out["ptf_floor_db"] = float(floor_db)
            out["weak_signal"] = bool(np.median(per_hop) < floor_db)

    rank = npz.get("accepted_candidate_rank")
    if rank is not None and rank.size:
        r = np.asarray(rank, dtype=float)
        r = r[r >= 0]
        if r.size:
            out["median_accepted_rank"] = float(np.median(r))

    # Phase-motion: flag hops whose phase_clean peak-to-peak is a robust outlier.
    phase = npz.get("phase_clean")
    if phase is not None and phase.ndim == 2 and phase.shape[0] > 2:
        p2p = np.ptp(np.asarray(phase, dtype=float), axis=1)
        med = float(np.median(p2p))
        mad = float(np.median(np.abs(p2p - med))) or 1e-9
        thresh = med + PHASE_MAD_K * mad
        motion_idx = np.nonzero(p2p > thresh)[0]
        out["phase_p2p"] = p2p
        out["motion_hops"] = motion_idx.tolist()
        out["motion_frac"] = float(len(motion_idx)) / int(phase.shape[0])
    return out


# ── Analysis: capture link health ───────────────────────────────────────────

def analyze_capture(rd: RunData) -> dict:
    stats = (rd.metadata or {}).get("live_packet_stats") or {}
    n_dropped = int(stats.get("n_dropped", 0) or 0)
    zero_filled = int(stats.get("zero_filled_bytes", 0) or 0)
    truncated = int(stats.get("mirror_truncated_bytes", 0) or 0)
    return {
        "available": bool(stats),
        "n_received": stats.get("n_received"),
        "n_dropped": n_dropped,
        "zero_filled_bytes": zero_filled,
        "mirror_truncated_bytes": truncated,
        "frame_loss": bool(n_dropped or zero_filled or truncated),
    }


# ── Ranked verdict ──────────────────────────────────────────────────────────

def rank_causes(lock: dict, timeline: dict, signal: dict, capture: dict) -> list[dict]:
    """Combine the block-level flags into a severity-ordered cause list.

    Each cause: {"cause", "severity" (0-1), "evidence" (str)}.
    """
    causes: list[dict] = []

    if capture.get("frame_loss"):
        sev = 0.9 if capture["n_dropped"] else 0.6
        causes.append({
            "cause": "frame_loss",
            "severity": sev,
            "evidence": (
                f"dropped={capture['n_dropped']}, "
                f"zero_filled_bytes={capture['zero_filled_bytes']}, "
                f"truncated={capture['mirror_truncated_bytes']}"
            ),
        })

    if lock.get("status") == "scanned" and lock.get("flags"):
        names = {f["flag"] for f in lock["flags"]}
        sev = 0.85 if {"better_neighbor", "low_confidence"} & names else 0.6
        causes.append({
            "cause": "bad_warmup_lock",
            "severity": sev,
            "evidence": "; ".join(f["detail"] for f in lock["flags"]),
        })
    elif lock.get("status") == "absent":
        causes.append({
            "cause": "warmup_evidence_missing",
            "severity": 0.4,
            "evidence": "warmup_bin_selection.json absent though lock was auto",
        })

    vfrac = timeline.get("valid_frac", 1.0)
    dom = timeline.get("dominant_reason")
    if vfrac < LOW_VALID_FRAC and dom in GATE_REJECTIONS:
        causes.append({
            "cause": "ahet_over_rejection",
            "severity": 0.5 + 0.4 * (1.0 - vfrac),
            "evidence": (
                f"{(1 - vfrac) * 100:.0f}% of hops HR-invalid; dominant "
                f"rejection reason '{dom}' is a confidence gate"
            ),
        })
    elif vfrac < LOW_VALID_FRAC:
        causes.append({
            "cause": "hr_mostly_blank",
            "severity": 0.4 + 0.4 * (1.0 - vfrac),
            "evidence": (
                f"{(1 - vfrac) * 100:.0f}% of hops HR-invalid; dominant reason "
                f"'{dom}'"
            ),
        })

    if signal.get("weak_signal"):
        causes.append({
            "cause": "weak_signal",
            "severity": 0.55,
            "evidence": (
                f"median peak-to-floor {signal['ptf_median_db']:.1f} dB is below "
                f"the {signal['ptf_floor_db']:.1f} dB acceptance floor"
            ),
        })

    if signal.get("motion_frac", 0.0) >= 0.25:
        causes.append({
            "cause": "motion_corruption",
            "severity": 0.5,
            "evidence": (
                f"{signal['motion_frac'] * 100:.0f}% of windows show outlier "
                f"phase excursions (possible subject motion)"
            ),
        })

    causes.sort(key=lambda c: c["severity"], reverse=True)
    return causes


# ── Text report ─────────────────────────────────────────────────────────────

def _fmt_timeline(s: str, width: int = 60) -> str:
    return "\n    ".join(s[i:i + width] for i in range(0, len(s), width)) or "(none)"


def build_report(rd: RunData, lock: dict, timeline: dict, signal: dict,
                 capture: dict, causes: list[dict]) -> str:
    L: list[str] = []
    meta = rd.metadata or {}
    L.append("=" * 72)
    L.append(f"Live-run diagnosis: {rd.run_dir.name}")
    L.append("=" * 72)
    L.append(f"mode={meta.get('mode')}  session={meta.get('session_id')}  "
             f"git={str(meta.get('git_commit'))[:10]}  "
             f"status={meta.get('completion_status')}")
    if rd.missing:
        L.append(f"missing artifacts: {', '.join(rd.missing)}")
    for note in rd.notes:
        L.append(f"note: {note}")

    # Verdict first -- it is the point of the tool.
    L.append("")
    L.append("VERDICT (most likely first)")
    L.append("-" * 72)
    if not causes:
        L.append("  No failure signature detected -- HR looks healthy across the run.")
    for i, c in enumerate(causes, 1):
        L.append(f"  {i}. {c['cause']}  [severity {c['severity']:.2f}]")
        L.append(f"       {c['evidence']}")

    # 1. Lock
    L.append("")
    L.append("1. WARMUP LOCK")
    L.append("-" * 72)
    if lock["status"] == "manual":
        L.append(f"  Bin was preset ({lock['locked_bin_source']}), bin="
                 f"{lock.get('locked_bin')} -- no warmup scan performed.")
    elif lock["status"] == "absent":
        L.append("  Auto lock expected but warmup_bin_selection.json is missing.")
    else:
        L.append(f"  selected bin {lock['selected_bin']} "
                 f"(~{lock['selected_range_m']} m), confidence "
                 f"{lock['confidence']}, reason {lock['selection_reason']}")
        if lock.get("margin") is not None:
            L.append(f"  score margin over runner-up: {lock['margin']:.0f}")
        L.append("  candidates (bin: score, hr_valid, br_conf, energy_rank):")
        for c in lock["candidates"][:8]:
            mark = " <= selected" if c.get("bin") == lock["selected_bin"] else ""
            L.append(f"      {c['bin']:>3}: score={c.get('score')}, "
                     f"hr_valid={int(bool(c.get('hr_valid')))}, "
                     f"br={c.get('br_confidence')}, "
                     f"e_rank={c.get('energy_rank')}{mark}")
        if lock["flags"]:
            for f in lock["flags"]:
                L.append(f"  FLAG {f['flag']}: {f['detail']}")
        else:
            L.append("  no lock-quality flags raised.")

    # 2. HR timeline
    L.append("")
    L.append("2. HR AVAILABILITY")
    L.append("-" * 72)
    L.append(f"  hops={timeline['n_hops']}  HR-valid={timeline['n_valid']} "
             f"({timeline['valid_frac'] * 100:.0f}%)  "
             f"ahet_verified={timeline['ahet_frac'] * 100:.0f}%")
    L.append(f"  timeline (#=valid, .=blank):\n    "
             f"{_fmt_timeline(timeline['timeline'])}")
    if timeline["reason_counts"]:
        L.append("  rejection reasons on blank hops:")
        for reason, cnt in timeline["reason_counts"].items():
            L.append(f"      {cnt:>4}  {reason}")
    if timeline["stage_counts"]:
        L.append(f"  spectrum_stage distribution: {timeline['stage_counts']}")

    # 3. Signal level
    L.append("")
    L.append("3. SIGNAL LEVEL (from NPZ)")
    L.append("-" * 72)
    if not signal.get("available"):
        L.append("  live_intermediates.npz not available -- signal-level "
                 "diagnosis skipped.")
    else:
        if "ptf_median_db" in signal:
            L.append(f"  median peak-to-floor: {signal['ptf_median_db']:.1f} dB "
                     f"(floor {signal['ptf_floor_db']:.1f} dB) -> "
                     f"{'WEAK' if signal.get('weak_signal') else 'ok'}")
        if "median_accepted_rank" in signal:
            L.append(f"  median accepted candidate rank: "
                     f"{signal['median_accepted_rank']:.1f}")
        if "motion_frac" in signal:
            L.append(f"  phase-motion windows: {signal['motion_frac'] * 100:.0f}% "
                     f"(hops {signal['motion_hops'][:12]}"
                     f"{'...' if len(signal['motion_hops']) > 12 else ''})")

    # 4. Capture
    L.append("")
    L.append("4. CAPTURE HEALTH")
    L.append("-" * 72)
    if not capture.get("available"):
        L.append("  no packet stats (replay run or older metadata).")
    else:
        L.append(f"  received={capture['n_received']}  dropped="
                 f"{capture['n_dropped']}  zero_filled_bytes="
                 f"{capture['zero_filled_bytes']}  "
                 f"truncated={capture['mirror_truncated_bytes']} -> "
                 f"{'FRAME LOSS' if capture['frame_loss'] else 'clean'}")
    L.append("=" * 72)
    return "\n".join(L)


# ── Plots ───────────────────────────────────────────────────────────────────

def _worst_window_indices(rd: RunData, k: int) -> list[int]:
    """Pick the k most informative windows: prefer HR-invalid hops with the
    lowest peak-to-floor (closest calls / clearest failures)."""
    if rd.df is None:
        return []
    valid = _valid_series(rd.df)
    invalid_idx = list(np.nonzero(valid == 0)[0])
    if not invalid_idx:
        return []
    ptf = rd.npz.get("peak_to_floor_ratio_db")
    if ptf is not None and ptf.size:
        arr = np.asarray(ptf, dtype=float)
        score = np.nanmax(arr, axis=1) if arr.ndim == 2 else arr
        invalid_idx = [i for i in invalid_idx if i < len(score)]
        invalid_idx.sort(key=lambda i: (np.nan_to_num(score[i], nan=1e9)))
    return invalid_idx[:k]


def plot_overview(rd: RunData, timeline: dict, signal: dict, out_dir: Path) -> Optional[Path]:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    df = rd.df
    if df is None:
        return None
    t = df["elapsed_s"].to_numpy(dtype=float)
    valid = _valid_series(df)
    fig, axs = plt.subplots(3, 1, figsize=(11, 8), sharex=True)

    axs[0].plot(t, df.get("hr_bpm_smooth"), color="#00cc66", label="HR smooth")
    axs[0].plot(t, df.get("hr_bpm_raw"), ".", color="#00cc66", alpha=0.35, label="HR raw")
    blank_t = t[valid == 0]
    for bt in blank_t:
        axs[0].axvline(bt, color="#cc3333", alpha=0.12, lw=1)
    axs[0].set_ylabel("HR (bpm)")
    axs[0].legend(loc="upper right", fontsize=8)
    axs[0].set_title(f"{rd.run_dir.name} -- HR valid "
                     f"{timeline['valid_frac'] * 100:.0f}%")

    ptf = rd.npz.get("peak_to_floor_ratio_db")
    if ptf is not None and ptf.size:
        arr = np.asarray(ptf, dtype=float)
        per_hop = np.nanmax(arr, axis=1) if arr.ndim == 2 else arr
        n = min(len(t), len(per_hop))
        axs[1].plot(t[:n], per_hop[:n], color="#4499ff")
        if "ptf_floor_db" in signal:
            axs[1].axhline(signal["ptf_floor_db"], color="#cc3333", ls="--",
                           lw=1, label="floor")
            axs[1].legend(loc="upper right", fontsize=8)
    axs[1].set_ylabel("peak-to-floor (dB)")

    reasons = (df.get("candidate_rejection_reason", pd.Series(dtype=object))
               .fillna("").replace("", "(none)").astype(str))
    codes, uniques = pd.factorize(reasons)
    axs[2].scatter(t, codes, c=np.where(valid == 1, "#00cc66", "#cc3333"), s=10)
    axs[2].set_yticks(range(len(uniques)))
    axs[2].set_yticklabels(list(uniques), fontsize=7)
    axs[2].set_ylabel("rejection reason")
    axs[2].set_xlabel("elapsed (s)")

    fig.tight_layout()
    out = out_dir / "overview.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def plot_warmup_candidates(lock: dict, out_dir: Path) -> Optional[Path]:
    if lock.get("status") != "scanned" or not lock.get("candidates"):
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cands = sorted(lock["candidates"], key=lambda c: c["bin"])
    bins = [c["bin"] for c in cands]
    scores = [c.get("score", 0) for c in cands]
    colors = ["#00cc66" if bool(c.get("hr_valid")) else "#888" for c in cands]
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.bar([str(b) for b in bins], scores, color=colors)
    sel = lock["selected_bin"]
    if sel in bins:
        ax.axvline(bins.index(sel), color="#eeaa00", lw=2, ls="--", label="selected")
        ax.legend()
    ax.set_xlabel("candidate bin")
    ax.set_ylabel("warmup score")
    ax.set_title("Warmup lock decision (green = HR-valid candidate)")
    fig.tight_layout()
    out = out_dir / "warmup_candidates.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


def plot_window(rd: RunData, idx: int, out_dir: Path) -> Optional[Path]:
    npz = rd.npz
    hf = npz.get("heart_freqs_hz")
    hs = npz.get("heart_spectrum")
    if hf is None or hs is None:
        return None
    n = _npz_len(npz) or 0
    if idx < 0 or idx >= n:
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    freqs = np.asarray(hf[idx], dtype=float)
    fig, axs = plt.subplots(2, 1, figsize=(10, 7))

    def _spec(key, **kw):
        arr = npz.get(key)
        if arr is not None and arr.ndim == 2 and idx < arr.shape[0]:
            fk = npz.get("heart_freqs_hz")
            fx = np.asarray(fk[idx], dtype=float) if fk is not None else freqs
            y = np.asarray(arr[idx], dtype=float)
            m = min(len(fx), len(y))
            axs[0].plot(fx[:m] * 60.0, y[:m], **kw)

    _spec("heart_spectrum_pre_eca", color="#888", lw=1, label="pre-ECA")
    _spec("heart_spectrum_first_pass", color="#4499ff", lw=1, alpha=0.7,
          label="first-pass")
    _spec("heart_spectrum", color="#00cc66", lw=2, label="final")
    bf = npz.get("baseline_freqs_hz")
    bs = npz.get("baseline_spectrum")
    if bf is not None and bs is not None and idx < len(bs):
        fx = np.asarray(bf[idx], dtype=float)
        y = np.asarray(bs[idx], dtype=float)
        m = min(len(fx), len(y))
        axs[0].plot(fx[:m] * 60.0, y[:m], color="#cc8800", ls=":", label="baseline (no-ECA)")

    # Mark the accepted peak (from CSV) and candidate peaks (from NPZ).
    if rd.df is not None and idx < len(rd.df):
        pk = rd.df.iloc[idx].get("heart_peak_hz")
        if pd.notna(pk):
            axs[0].axvline(float(pk) * 60.0, color="#00cc66", lw=1.5,
                           label=f"accepted {float(pk) * 60:.0f} bpm")
    cand = npz.get("candidate_refined_hz")
    if cand is not None and cand.ndim == 2 and idx < cand.shape[0]:
        for ch in np.asarray(cand[idx], dtype=float):
            if np.isfinite(ch):
                axs[0].axvline(ch * 60.0, color="#cc3333", ls="--", lw=0.8, alpha=0.6)
    axs[0].set_xlabel("frequency (bpm)")
    axs[0].set_ylabel("magnitude")
    valid = int(_valid_series(rd.df)[idx]) if rd.df is not None else -1
    axs[0].set_title(f"hop {idx} heart-band spectrum (hr_valid={valid})")
    axs[0].legend(fontsize=8)

    phase = npz.get("phase_clean")
    if phase is not None and phase.ndim == 2 and idx < phase.shape[0]:
        axs[1].plot(np.asarray(phase[idx], dtype=float), color="#ccc")
        axs[1].set_title("phase_clean (this window)")
        axs[1].set_xlabel("sample")
    fig.tight_layout()
    out = out_dir / f"window_{idx}.png"
    fig.savefig(out, dpi=110)
    plt.close(fig)
    return out


# ── Orchestration ───────────────────────────────────────────────────────────

def diagnose(rd: RunData) -> dict:
    """Run every analysis block and the ranked verdict. Pure (no I/O)."""
    lock = analyze_lock(rd)
    timeline = (
        analyze_hr_timeline(rd.df) if rd.df is not None
        else {"n_hops": 0, "n_valid": 0, "valid_frac": 0.0, "ahet_frac": 0.0,
              "reason_counts": {}, "dominant_reason": None, "stage_counts": {},
              "timeline": ""}
    )
    signal = analyze_signal(rd.npz, peak_to_floor_floor_db(rd))
    capture = analyze_capture(rd)
    causes = rank_causes(lock, timeline, signal, capture)
    return {"lock": lock, "timeline": timeline, "signal": signal,
            "capture": capture, "causes": causes}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Diagnose why HR was blank / whether warmup locked well "
                    "for one live_demo run folder.")
    ap.add_argument("run_dir", type=Path, help="results/<ts>_<mode>_<session>/")
    ap.add_argument("--worst", type=int, default=3,
                    help="render the K most informative failed windows (default 3)")
    ap.add_argument("--window-idx", type=int, default=None,
                    help="also render this specific hop index")
    ap.add_argument("--no-plots", action="store_true", help="text report only")
    ap.add_argument("--out", type=Path, default=None,
                    help="plot output dir (default <run_dir>/diagnosis/)")
    args = ap.parse_args(argv)

    try:
        rd = load_run(args.run_dir)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    res = diagnose(rd)
    report = build_report(rd, res["lock"], res["timeline"], res["signal"],
                          res["capture"], res["causes"])
    print(report)

    if not args.no_plots and rd.df is not None:
        out_dir = args.out or (rd.run_dir / "diagnosis")
        out_dir.mkdir(parents=True, exist_ok=True)
        written = []
        p = plot_overview(rd, res["timeline"], res["signal"], out_dir)
        if p:
            written.append(p)
        p = plot_warmup_candidates(res["lock"], out_dir)
        if p:
            written.append(p)
        idxs = list(_worst_window_indices(rd, args.worst))
        if args.window_idx is not None and args.window_idx not in idxs:
            idxs.append(args.window_idx)
        for i in idxs:
            p = plot_window(rd, i, out_dir)
            if p:
                written.append(p)
        if written:
            print("\nplots written:")
            for p in written:
                print(f"  {p}")
        else:
            print("\n(no plots written -- NPZ/CSV lacked the needed arrays)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
