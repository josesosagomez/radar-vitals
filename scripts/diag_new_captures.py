"""Diagnostic plots for new exp004 captures: cap3_retake, cap4, cap5.

Produces 4 plots per capture (A: scatter, B: error over time, C: error vs HR,
D: 20s vs 30s comparison) and saves to results/diagnostics/per_window_error/.

Run from repo root: python scripts/diag_new_captures.py
"""

import os
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CAPTURES = {
    "cap3_retake": "results/exp004_window_length/20260615_181343/cap3_retake",
    "cap4":        "results/exp004_window_length/20260615_181821/cap4",
    "cap5":        "results/exp004_window_length/20260615_181912/cap5",
}

OUT_DIR = os.path.join(ROOT, "results/diagnostics/per_window_error")
os.makedirs(OUT_DIR, exist_ok=True)


def _metrics(df):
    fin = df.dropna(subset=["error_bpm"])
    if len(fin) == 0:
        return float("nan"), float("nan"), float("nan"), 0
    mae = fin["error_bpm"].abs().mean()
    rmse = float(np.sqrt((fin["error_bpm"] ** 2).mean()))
    bias = fin["error_bpm"].mean()
    return mae, rmse, bias, len(fin)


def _colours(df):
    return ["#2ca02c" if v else "#d62728" for v in df["ahet_verified"].fillna(False)]


def plot_capture(cap_id, cap_dir):
    csv_20 = os.path.join(ROOT, cap_dir, "condition_20s", "comparison.csv")
    csv_30 = os.path.join(ROOT, cap_dir, "condition_30s", "comparison.csv")
    for p in [csv_20, csv_30]:
        if not os.path.exists(p):
            print(f"ERROR: {p} not found — skipping {cap_id}")
            return

    df20 = pd.read_csv(csv_20)
    df30 = pd.read_csv(csv_30)

    mae, rmse, bias, n = _metrics(df20)
    print(f"\n{cap_id} condition_20s: MAE={mae:.2f}  RMSE={rmse:.2f}  "
          f"bias={bias:.2f}  N_finite={n}  N_nan={df20['hr_bpm'].isna().sum()}")

    # ---- Plot A: scatter radar HR vs Masimo HR ----------------------------------
    fig, ax = plt.subplots(figsize=(6, 6))
    fin = df20.dropna(subset=["hr_bpm", "masimo_pr_bpm"])
    cols = _colours(fin)
    ax.scatter(fin["masimo_pr_bpm"], fin["hr_bpm"],
               c=cols, s=60, zorder=3, edgecolors="k", linewidths=0.4)
    lo = min(fin["masimo_pr_bpm"].min(), fin["hr_bpm"].min()) - 5
    hi = max(fin["masimo_pr_bpm"].max(), fin["hr_bpm"].max()) + 5
    ax.plot([lo, hi], [lo, hi], color="grey", linewidth=1, zorder=1)
    ax.set_xlabel("Masimo PR (bpm)")
    ax.set_ylabel("Radar HR (bpm)")
    ax.set_title(f"{cap_id} — radar HR vs Masimo PR (condition_20s, N={n})\n"
                 f"MAE={mae:.1f}  bias={bias:.1f} bpm")
    ax.legend(handles=[
        mpatches.Patch(color="#2ca02c", label="AHET pass"),
        mpatches.Patch(color="#d62728", label="AHET fail / NaN"),
        plt.Line2D([0], [0], color="grey", label="identity"),
    ], fontsize=8)
    ax.set_aspect("equal", adjustable="box")
    plt.tight_layout()
    path_A = os.path.join(OUT_DIR, f"{cap_id}_A_scatter.png")
    fig.savefig(path_A, dpi=150)
    plt.close(fig)
    print(f"  A -> {path_A}")

    # ---- Plot B: error over window index (time proxy) ---------------------------
    fig, ax = plt.subplots(figsize=(10, 4))
    fin_b = df20.dropna(subset=["error_bpm"]).copy()
    cols_b = _colours(fin_b)
    ax.axhline(0, color="k", linewidth=0.8)
    ax.axhspan(-5, 5, alpha=0.10, color="steelblue")
    ax.scatter(fin_b["window_index"], fin_b["error_bpm"],
               c=cols_b, s=50, zorder=3, edgecolors="k", linewidths=0.3)
    # NaN windows as vertical grey lines
    nan_idx = df20[df20["hr_bpm"].isna()]["window_index"]
    for xi in nan_idx:
        ax.axvline(xi, color="lightgray", linewidth=0.6, zorder=0)
    ax.set_xlabel("Window index (condition_20s, time proxy)")
    ax.set_ylabel("Error  radar − Masimo (bpm)")
    ax.set_title(f"{cap_id} — per-window HR error over time")
    ax.legend(handles=[
        mpatches.Patch(color="#2ca02c", label="AHET pass"),
        mpatches.Patch(color="#d62728", label="AHET fail"),
        mpatches.Patch(color="steelblue", alpha=0.3, label="±5 bpm band"),
        plt.Line2D([0], [0], color="lightgray", label="NaN window"),
    ], fontsize=8)
    plt.tight_layout()
    path_B = os.path.join(OUT_DIR, f"{cap_id}_B_error_time.png")
    fig.savefig(path_B, dpi=150)
    plt.close(fig)
    print(f"  B -> {path_B}")

    # ---- Plot C: error vs true HR -----------------------------------------------
    fig, ax = plt.subplots(figsize=(7, 4))
    fin_c = df20.dropna(subset=["error_bpm", "masimo_pr_bpm"]).copy()
    cols_c = _colours(fin_c)
    ax.axhline(0, color="k", linewidth=0.8)
    ax.scatter(fin_c["masimo_pr_bpm"], fin_c["error_bpm"],
               c=cols_c, s=55, zorder=3, edgecolors="k", linewidths=0.3)
    ax.set_xlabel("Masimo PR (bpm) — true HR")
    ax.set_ylabel("Error  radar − Masimo (bpm)")
    ax.set_title(f"{cap_id} — HR error vs true HR (condition_20s)")
    ax.legend(handles=[
        mpatches.Patch(color="#2ca02c", label="AHET pass"),
        mpatches.Patch(color="#d62728", label="AHET fail"),
    ], fontsize=8)
    plt.tight_layout()
    path_C = os.path.join(OUT_DIR, f"{cap_id}_C_error_vs_hr.png")
    fig.savefig(path_C, dpi=150)
    plt.close(fig)
    print(f"  C -> {path_C}")

    # ---- Plot D: 20s vs 30s error comparison (matched centers) ------------------
    df20["_center"] = (df20["start_frame"] + df20["end_frame"]) // 2
    df30["_center"] = (df30["start_frame"] + df30["end_frame"]) // 2
    merged = pd.merge(
        df20[["_center", "error_bpm", "ahet_verified", "window_index"]].rename(
            columns={"error_bpm": "err_20", "ahet_verified": "av_20",
                     "window_index": "wi_20"}),
        df30[["_center", "error_bpm", "ahet_verified"]].rename(
            columns={"error_bpm": "err_30", "ahet_verified": "av_30"}),
        on="_center", how="inner",
    )
    fig, ax = plt.subplots(figsize=(14, 4))
    x = np.arange(len(merged))
    w = 0.38
    ax.bar(x - w/2, merged["err_20"].fillna(0), w, label="20 s", color="#4c78a8", alpha=0.8)
    ax.bar(x + w/2, merged["err_30"].fillna(0), w, label="30 s", color="#f58518", alpha=0.8)
    ax.axhline(0, color="k", linewidth=0.8)
    ax.axhspan(-5, 5, alpha=0.07, color="grey")
    ax.set_xticks(x[::5])
    ax.set_xticklabels([str(int(merged["_center"].iloc[i])) for i in range(0, len(merged), 5)],
                        fontsize=7, rotation=45)
    ax.set_xlabel("Center frame (matched across window lengths)")
    ax.set_ylabel("Error  radar − Masimo (bpm)")
    ax.set_title(f"{cap_id} — error: 20 s vs 30 s (N={len(merged)} matched centers)")
    ax.legend(fontsize=8)
    plt.tight_layout()
    path_D = os.path.join(OUT_DIR, f"{cap_id}_D_20vs30_error.png")
    fig.savefig(path_D, dpi=150)
    plt.close(fig)
    print(f"  D -> {path_D}")


# ---- run all captures ---------------------------------------------------------
for cap_id, cap_dir in CAPTURES.items():
    print(f"\n{'='*60}")
    print(f"  {cap_id}")
    print(f"{'='*60}")
    plot_capture(cap_id, cap_dir)

print(f"\nAll plots saved to: {OUT_DIR}")
