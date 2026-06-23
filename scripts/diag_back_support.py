"""
Back-support diagnostic — seated session failure analysis.

Tests the hypothesis that torso sway (absent chair back) drives failure in seated no-back
sessions at 131–135 cm by examining three metrics from existing comparison CSVs:

  1. Respiratory rate stability  — f_r CV per session (sway raises variance)
  2. Harmonic collision risk     — % windows where k×f_r (k=2,3,4) lands in [0.8,2.0] Hz
  3. NaN window breakdown        — quality_gate / collision_risk / other

No HDF5 loading, no pipeline re-run. Full revert = delete this file.

Usage
-----
    python scripts/diag_back_support.py \\
        --results_dir results/exp_eca_all/20260623_094252 \\
        [--sessions exp003 exp004 exp005 exp006 exp008 exp009] \\
        [--out figures/diag_back_support.png]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import pandas as pd

CARDIAC_LO_HZ = 0.8
CARDIAC_HI_HZ = 2.0
K_HARMONICS   = [2, 3, 4]

SESSION_META = {
    "exp003": {"posture": "no back", "dist": 135, "group": "no_back_far"},
    "exp004": {"posture": "no back", "dist": 135, "group": "no_back_far"},
    "exp005": {"posture": "no back", "dist": 131, "group": "no_back_far"},
    "exp006": {"posture": "w/ back", "dist": 135, "group": "chair_back"},
    "exp008": {"posture": "no back", "dist": 118, "group": "no_back_close"},
    "exp009": {"posture": "w/ back", "dist": 144, "group": "chair_back"},
}

GROUP_COLOR = {
    "chair_back":    "#2ca02c",
    "no_back_close": "#ff7f0e",
    "no_back_far":   "#d62728",
}


def _collision_risk(rr_bpm: float) -> bool:
    """True if any k×f_r (k=2,3,4) falls in the cardiac band."""
    f_r = rr_bpm / 60.0
    return any(CARDIAC_LO_HZ <= k * f_r <= CARDIAC_HI_HZ for k in K_HARMONICS)


def _analyse_session(df: pd.DataFrame) -> dict:
    """Compute all three metrics from a single comparison CSV."""
    n_total = len(df)

    # ── 1. f_r stability (exclude quality-gated and low-quality Masimo) ──────
    stable = df[~df["quality_gated"] & ~df["low_quality"] & df["rr_bpm"].notna()]
    rr_vals = stable["rr_bpm"].values
    f_r_mean = float(np.mean(rr_vals)) if len(rr_vals) > 0 else float("nan")
    f_r_std  = float(np.std(rr_vals, ddof=1)) if len(rr_vals) > 1 else float("nan")
    f_r_cv   = f_r_std / f_r_mean if f_r_mean > 0 else float("nan")

    # ── 2. Harmonic collision risk (all non-quality-gated windows) ───────────
    valid = df[~df["quality_gated"] & df["rr_bpm"].notna()]
    coll_flags = valid["rr_bpm"].apply(_collision_risk)
    coll_risk_rate = float(coll_flags.mean()) if len(valid) > 0 else float("nan")

    # ── 3. NaN window breakdown ───────────────────────────────────────────────
    nan_windows = df[df["hr_bpm"].isna()]
    n_nan = len(nan_windows)

    n_quality_gate   = int(nan_windows["quality_gated"].sum())
    non_gated_nan    = nan_windows[~nan_windows["quality_gated"]]
    n_collision_risk = int(
        non_gated_nan["rr_bpm"].apply(
            lambda v: _collision_risk(v) if pd.notna(v) else False
        ).sum()
    ) if len(non_gated_nan) > 0 else 0
    n_other    = n_nan - n_quality_gate - n_collision_risk
    n_finite   = int(df["hr_bpm"].notna().sum())

    return {
        "n_total":          n_total,
        "n_finite":         n_finite,
        "n_nan":            n_nan,
        "n_quality_gate":   n_quality_gate,
        "n_collision_risk": n_collision_risk,
        "n_other":          n_other,
        "f_r_mean":         f_r_mean,
        "f_r_std":          f_r_std,
        "f_r_cv":           f_r_cv,
        "coll_risk_rate":   coll_risk_rate,
        "rr_vals":          rr_vals,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results_dir", required=True)
    ap.add_argument(
        "--sessions", nargs="+",
        default=["exp003", "exp004", "exp005", "exp006", "exp008", "exp009"],
    )
    ap.add_argument("--out", default="figures/diag_back_support.png")
    args = ap.parse_args()

    results_dir = Path(args.results_dir)
    sessions = args.sessions

    stats: dict[str, dict] = {}
    for sid in sessions:
        csv = results_dir / sid / "comparison.csv"
        df = pd.read_csv(csv)
        stats[sid] = _analyse_session(df)

    # ── stdout table ──────────────────────────────────────────────────────────
    hdr = (
        f"{'Sess':<7} {'Posture':<12} {'Dist':>5}  "
        f"{'n_fin':>5} {'f_r_mean':>8} {'f_r_std':>7} {'f_r_CV':>6}  "
        f"{'coll%':>6}  {'NaN_coll':>8} {'NaN_qg':>7} {'NaN_oth':>8}"
    )
    print(hdr)
    print("-" * len(hdr))
    for sid in sessions:
        m = stats[sid]
        meta = SESSION_META.get(sid, {})
        print(
            f"{sid:<7} {meta.get('posture','?'):<12} {meta.get('dist','?'):>5}  "
            f"{m['n_finite']:>5} {m['f_r_mean']:>8.1f} {m['f_r_std']:>7.2f} {m['f_r_cv']:>6.3f}  "
            f"{100*m['coll_risk_rate']:>6.1f}  "
            f"{m['n_collision_risk']:>8} {m['n_quality_gate']:>7} {m['n_other']:>8}"
        )

    # ── figure ────────────────────────────────────────────────────────────────
    labels = [
        f"{sid}\n{SESSION_META[sid]['posture']}\n{SESSION_META[sid]['dist']} cm"
        for sid in sessions
    ]
    colors = [GROUP_COLOR[SESSION_META[sid]["group"]] for sid in sessions]

    fig, axes = plt.subplots(3, 1, figsize=(10, 11), constrained_layout=True)

    # Panel 1: f_r stability boxplot
    ax1 = axes[0]
    rr_data = [stats[sid]["rr_vals"] for sid in sessions]
    bp = ax1.boxplot(rr_data, patch_artist=True, widths=0.5,
                     medianprops=dict(color="black", linewidth=1.5))
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    # Overlay CV annotation
    for i, sid in enumerate(sessions):
        cv = stats[sid]["f_r_cv"]
        ax1.text(i + 1, ax1.get_ylim()[1] if ax1.get_ylim()[1] > 0 else 25,
                 f"CV={cv:.3f}", ha="center", va="bottom", fontsize=7.5)
    ax1.set_xticks(range(1, len(sessions) + 1))
    ax1.set_xticklabels(labels, fontsize=8)
    ax1.set_ylabel("Respiratory rate (bpm)")
    ax1.set_title("Panel 1 — Respiratory rate stability per session")
    ax1.grid(True, axis="y", alpha=0.3)

    # Panel 2: harmonic collision risk rate
    ax2 = axes[1]
    coll_pct = [100 * stats[sid]["coll_risk_rate"] for sid in sessions]
    bars = ax2.bar(range(len(sessions)), coll_pct, color=colors, alpha=0.8, width=0.5)
    ax2.set_xticks(range(len(sessions)))
    ax2.set_xticklabels(labels, fontsize=8)
    ax2.set_ylabel("Windows with k×f_r in cardiac band (%)")
    ax2.set_title(
        "Panel 2 — Harmonic collision risk  "
        "(k=2,3,4; cardiac band = 0.8–2.0 Hz)"
    )
    ax2.set_ylim(0, 100)
    ax2.grid(True, axis="y", alpha=0.3)
    for bar, val in zip(bars, coll_pct):
        ax2.text(bar.get_x() + bar.get_width() / 2, val + 1,
                 f"{val:.0f}%", ha="center", va="bottom", fontsize=8)

    # Panel 3: NaN breakdown stacked bar
    ax3 = axes[2]
    n_totals     = [stats[sid]["n_total"]          for sid in sessions]
    n_finites    = [stats[sid]["n_finite"]          for sid in sessions]
    n_colls      = [stats[sid]["n_collision_risk"]  for sid in sessions]
    n_qgates     = [stats[sid]["n_quality_gate"]    for sid in sessions]
    n_others     = [stats[sid]["n_other"]           for sid in sessions]

    x = np.arange(len(sessions))
    w = 0.5
    p1 = ax3.bar(x, n_finites, w, label="finite",         color="#2ca02c", alpha=0.85)
    p2 = ax3.bar(x, n_colls,   w, label="NaN: collision",  color="#d62728", alpha=0.85,
                 bottom=n_finites)
    p3 = ax3.bar(x, n_qgates,  w, label="NaN: quality gate", color="#ff7f0e", alpha=0.85,
                 bottom=[f + c for f, c in zip(n_finites, n_colls)])
    p4 = ax3.bar(x, n_others,  w, label="NaN: other",      color="#9467bd", alpha=0.85,
                 bottom=[f + c + q for f, c, q in zip(n_finites, n_colls, n_qgates)])
    ax3.set_xticks(x)
    ax3.set_xticklabels(labels, fontsize=8)
    ax3.set_ylabel("Windows")
    ax3.set_title("Panel 3 — NaN window breakdown per session")
    ax3.legend(fontsize=8, loc="upper right")
    ax3.grid(True, axis="y", alpha=0.3)

    # Fix Panel 1 CV annotation y-position (needs axes drawn first)
    ax1.relim()
    ax1.autoscale_view()
    y_top = ax1.get_ylim()[1]
    for i, sid in enumerate(sessions):
        cv = stats[sid]["f_r_cv"]
        ax1.texts[i].set_y(y_top * 0.99)

    # Legend for group colours (shared across panels 1 and 2)
    legend_patches = [
        mpatches.Patch(color=GROUP_COLOR["chair_back"],    alpha=0.7, label="chair back"),
        mpatches.Patch(color=GROUP_COLOR["no_back_close"], alpha=0.7, label="no back, close (118 cm)"),
        mpatches.Patch(color=GROUP_COLOR["no_back_far"],   alpha=0.7, label="no back, far (131–135 cm)"),
    ]
    fig.legend(handles=legend_patches, loc="upper right",
               fontsize=8, title="group", bbox_to_anchor=(1.0, 1.0))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"\nFigure saved: {out}")


if __name__ == "__main__":
    main()
