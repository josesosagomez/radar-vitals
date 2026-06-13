"""Compare radar heart-rate estimates against the Masimo pulse-rate reference.

The radar produces one HR per sliding window; the Masimo provides 1 Hz PR. For each radar
window we take the PI-gated window-mean Masimo PR and compute agreement (MAE, RMSE) plus an
overlay plot — the figure that turns "something is wrong" into "I can see when/how".
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from . import masimo


def compare(radar_df: pd.DataFrame, masimo_df: pd.DataFrame, min_pi: float = 0.5) -> pd.DataFrame:
    """Join radar windows to the Masimo reference.

    radar_df columns required: start_epoch, end_epoch, hr_bpm (Unix seconds, UTC epochs).
    Returns radar_df augmented with masimo_pr_bpm, error_bpm, abs_error_bpm, and a
    low_quality flag for windows where the reference PI was poor.
    """
    rows = []
    for _, r in radar_df.iterrows():
        ref = masimo.reference_pr(masimo_df, r["start_epoch"], r["end_epoch"], min_pi=min_pi)
        err = r["hr_bpm"] - ref["pr_bpm_mean"]
        rows.append(
            {
                **r.to_dict(),
                "masimo_pr_bpm": ref["pr_bpm_mean"],
                "error_bpm": err,
                "abs_error_bpm": abs(err),
                "low_quality": ref["low_quality"],
            }
        )
    return pd.DataFrame(rows)


def metrics(merged: pd.DataFrame, drop_low_quality: bool = True) -> dict:
    """MAE / RMSE / bias over the merged comparison (optionally excluding low-PI windows).

    NaN hr_bpm rows (ECA+AHET low-confidence windows) are excluded from error stats
    and counted separately under 'n_nan_windows'.
    """
    d = merged[~merged["low_quality"]] if drop_low_quality else merged
    if len(d) == 0:
        raise ValueError("No usable windows after quality filtering.")
    nan_mask = d["hr_bpm"].isna()
    n_nan = int(nan_mask.sum())
    d_valid = d[~nan_mask]
    if len(d_valid) == 0:
        raise ValueError("All windows are NaN after ECA+AHET; no credible estimates.")
    err = d_valid["error_bpm"].to_numpy()
    return {
        "n_windows": int(len(d_valid)),
        "n_nan_windows": n_nan,
        "mae_bpm": float(np.mean(np.abs(err))),
        "rmse_bpm": float(np.sqrt(np.mean(err ** 2))),
        "bias_bpm": float(np.mean(err)),
    }


def overlay_plot(merged: pd.DataFrame, out_path: str | Path, title: str = "") -> Path:
    """Radar HR vs Masimo PR over time. Low-quality windows marked so they aren't trusted."""
    out_path = Path(out_path)
    t = pd.to_datetime(merged["start_epoch"], unit="s", utc=True)

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(t, merged["masimo_pr_bpm"], "o-", label="Masimo PR (ref)", color="black")
    ax.plot(t, merged["hr_bpm"], "s-", label="Radar HR", color="tab:red")
    if merged["low_quality"].any():
        bad = merged[merged["low_quality"]]
        ax.scatter(
            pd.to_datetime(bad["start_epoch"], unit="s", utc=True),
            bad["hr_bpm"], facecolors="none", edgecolors="tab:orange",
            s=120, label="low-PI window (suspect ref)",
        )
    ax.set_xlabel("time (UTC)")
    ax.set_ylabel("rate (bpm)")
    ax.set_title(title or "Radar HR vs Masimo PR")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path
