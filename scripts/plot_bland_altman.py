"""
Bland-Altman agreement plot for the working sessions.

Excludes:
  - quality_gated == True  (radar quality mask rejected window)
  - low_quality   == True  (Masimo PI below threshold — unreliable reference)
  - hr_bpm        is NaN   (ECA+AHET returned no credible estimate)

Usage
-----
    python scripts/plot_bland_altman.py \
        --results_dir results/exp_eca_all/20260623_094252 \
        [--sessions exp006 exp008 exp009] \
        [--out figures/bland_altman.png]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

SESSION_META = {
    "exp006": {"label": "exp006  seated w/ back  135 cm", "color": "#1f77b4"},
    "exp008": {"label": "exp008  seated no back  118 cm", "color": "#ff7f0e"},
    "exp009": {"label": "exp009  seated w/ back  144 cm", "color": "#2ca02c"},
}


def _load(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    mask = (~df["quality_gated"]) & (~df["low_quality"]) & (df["hr_bpm"].notna())
    return df[mask].copy()


def _ba_stats(diff: np.ndarray) -> dict:
    n = len(diff)
    bias = float(np.mean(diff))
    sd = float(np.std(diff, ddof=1))
    loa_up = bias + 1.96 * sd
    loa_lo = bias - 1.96 * sd
    t_crit = float(stats.t.ppf(0.975, df=n - 1))
    se_bias = sd / np.sqrt(n)
    se_loa = np.sqrt(3 * sd**2 / n)
    return {
        "bias": bias, "sd": sd,
        "loa_up": loa_up, "loa_lo": loa_lo,
        "bias_ci": (bias - t_crit * se_bias, bias + t_crit * se_bias),
        "loa_up_ci": (loa_up - t_crit * se_loa, loa_up + t_crit * se_loa),
        "loa_lo_ci": (loa_lo - t_crit * se_loa, loa_lo + t_crit * se_loa),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results_dir", required=True)
    ap.add_argument("--sessions", nargs="+", default=["exp006", "exp008", "exp009"])
    ap.add_argument("--out", default="figures/bland_altman.png")
    args = ap.parse_args()

    results_dir = Path(args.results_dir)

    frames = []
    for sid in args.sessions:
        df = _load(results_dir / sid / "comparison.csv")
        df["session"] = sid
        frames.append(df)
    data = pd.concat(frames, ignore_index=True)

    data["mean_hr"] = (data["hr_bpm"] + data["masimo_pr_bpm"]) / 2
    data["diff"] = data["error_bpm"]  # hr_bpm − masimo_pr_bpm

    st = _ba_stats(data["diff"].values)
    n_total = len(data)

    # ── plot ──────────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(8, 5))

    for sid in args.sessions:
        sub = data[data["session"] == sid]
        meta = SESSION_META.get(sid, {"label": sid, "color": "gray"})
        ax.scatter(sub["mean_hr"], sub["diff"],
                   color=meta["color"], label=meta["label"],
                   alpha=0.78, s=32, zorder=3)

    x_lo = data["mean_hr"].min() - 3
    x_hi = data["mean_hr"].max() + 3

    # zero reference
    ax.axhline(0, color="silver", linewidth=0.8, linestyle=":", zorder=1)

    # CI bands
    for key, alpha in [("bias_ci", 0.10), ("loa_up_ci", 0.07), ("loa_lo_ci", 0.07)]:
        lo, hi = st[key]
        ax.fill_between([x_lo, x_hi], lo, hi, color="black", alpha=alpha, linewidth=0)

    # bias and LoA lines
    ax.axhline(st["bias"],   color="black", linewidth=1.5, linestyle="-",  zorder=2)
    ax.axhline(st["loa_up"], color="black", linewidth=1.0, linestyle="--", zorder=2)
    ax.axhline(st["loa_lo"], color="black", linewidth=1.0, linestyle="--", zorder=2)

    # right-margin annotations
    x_ann = x_hi + 1.0
    for val, label in [
        (st["bias"],   f"Bias\n{st['bias']:+.1f} bpm"),
        (st["loa_up"], f"+1.96 SD\n{st['loa_up']:+.1f} bpm"),
        (st["loa_lo"], f"−1.96 SD\n{st['loa_lo']:+.1f} bpm"),
    ]:
        ax.text(x_ann, val, label, va="center", ha="left", fontsize=7.5)

    ax.set_xlim(x_lo, x_hi + 14)
    ax.set_xlabel("Mean of radar HR and Masimo PR  (bpm)", fontsize=10)
    ax.set_ylabel("Radar HR − Masimo PR  (bpm)", fontsize=10)
    ax.set_title(
        f"Bland–Altman: radar HR vs Masimo PR\n"
        f"n = {n_total} windows  |  "
        f"bias = {st['bias']:+.1f} bpm  |  "
        f"95 % LoA [{st['loa_lo']:+.1f}, {st['loa_up']:+.1f}] bpm",
        fontsize=10,
    )
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    plt.close(fig)

    # ── stdout summary ────────────────────────────────────────────────────────
    print(f"Saved: {out}")
    print(
        f"\nPooled  n={n_total}  "
        f"bias={st['bias']:+.2f} bpm  "
        f"SD={st['sd']:.2f} bpm  "
        f"LoA [{st['loa_lo']:+.2f}, {st['loa_up']:+.2f}] bpm"
    )
    print("\nPer-session breakdown:")
    for sid in args.sessions:
        sub = data[data["session"] == sid]
        d = sub["diff"].values
        s = _ba_stats(d)
        print(
            f"  {sid}  n={len(d):2d}  "
            f"bias={s['bias']:+.2f}  "
            f"SD={s['sd']:.2f}  "
            f"LoA [{s['loa_lo']:+.2f}, {s['loa_up']:+.2f}]  "
            f"MAE={np.mean(np.abs(d)):.2f} bpm"
        )


if __name__ == "__main__":
    main()
