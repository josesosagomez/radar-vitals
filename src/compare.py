"""Compare radar heart-rate estimates against the Masimo pulse-rate reference.

The radar produces one HR per sliding window; the Masimo provides 1 Hz PR. For each radar
window we take the PI-gated window-mean Masimo PR and compute agreement (MAE, RMSE) plus an
overlay plot — the figure that turns "something is wrong" into "I can see when/how".
"""
from __future__ import annotations

import itertools
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


def paired_metrics(
    conditions: dict[str, dict[int, dict]],
    reference_condition: str,
) -> dict:
    """Compute paired-center agreement metrics across window-length conditions.

    Each condition is a dict mapping integer center_frame to a per-window dict
    containing at minimum: "radar_hr", "masimo_pr", "error", "ahet_verified".
    The "error" field is always recomputed as radar_hr - masimo_pr; the stored
    value is not trusted (it may be NaN when one component is NaN).

    Parameters
    ----------
    conditions:
        Maps condition name (e.g. "20s") to a dict[int, dict] of per-center results.
    reference_condition:
        Must be a key in conditions. Validated but does not alter computation;
        reserved for callers that need to mark which condition is baseline.

    Returns
    -------
    dict with keys:
        "all_centers"   — sorted list of every center frame across all conditions
        "per_condition" — per-condition coverage counts and finite-only metrics
        "intersection"  — centers finite in ALL conditions, per-condition metrics,
                          and all pairwise error-diff / AHET-transition stats

    Raises
    ------
    ValueError
        For empty conditions, unknown reference condition, non-integer center
        frame keys, missing inner dict keys, or empty key intersection.
    """
    # ------------------------------------------------------------------ validation
    if not conditions:
        raise ValueError("conditions must be non-empty")
    if reference_condition not in conditions:
        raise ValueError(
            f"reference_condition {reference_condition!r} not found in conditions "
            f"(available: {list(conditions.keys())})"
        )

    required_inner = {"radar_hr", "masimo_pr", "error", "ahet_verified"}
    for cname, cdict in conditions.items():
        cf_keys = list(cdict.keys())
        if len(cf_keys) != len(set(cf_keys)):
            raise ValueError(
                f"Condition {cname!r} has duplicate center_frame keys"
            )
        for cf, entry in cdict.items():
            if isinstance(cf, bool) or not isinstance(cf, int):
                raise ValueError(
                    f"Center frame keys must be int; got {cf!r} "
                    f"in condition {cname!r}"
                )
            missing = required_inner - set(entry.keys())
            if missing:
                raise ValueError(
                    f"Entry at center {cf} in condition {cname!r} is missing "
                    f"required keys: {sorted(missing)}"
                )

    cond_names = list(conditions.keys())
    key_sets = [set(cdict.keys()) for cdict in conditions.values()]

    common_keys: set[int] = key_sets[0].copy()
    for ks in key_sets[1:]:
        common_keys &= ks
    if not common_keys:
        raise ValueError(
            "No common center_frame keys across all conditions; "
            "cannot compute paired metrics"
        )

    # ------------------------------------------------------------------ helpers
    def _finite(val) -> bool:
        try:
            return bool(np.isfinite(float(val)))
        except (TypeError, ValueError):
            return False

    def _metrics(errs: list[float]) -> tuple[float, float, float]:
        if not errs:
            nan = float("nan")
            return nan, nan, nan
        a = np.array(errs, dtype=float)
        return (
            float(np.mean(np.abs(a))),
            float(np.sqrt(np.mean(a ** 2))),
            float(np.mean(a)),
        )

    # ------------------------------------------------------------------ all_centers
    all_keys: set[int] = set()
    for ks in key_sets:
        all_keys |= ks
    all_centers = sorted(all_keys)

    # ------------------------------------------------------------------ per_condition
    per_condition: dict[str, dict] = {}
    for cname, cdict in conditions.items():
        n_total = len(cdict)
        n_finite = n_ahet = n_nan_radar = n_nan_ref = 0
        errs: list[float] = []

        for entry in cdict.values():
            rhr_ok = _finite(entry["radar_hr"])
            mpr_ok = _finite(entry["masimo_pr"])
            if not rhr_ok:
                n_nan_radar += 1
            if not mpr_ok:
                n_nan_ref += 1
            if rhr_ok and mpr_ok:
                n_finite += 1
                errs.append(float(entry["radar_hr"]) - float(entry["masimo_pr"]))
            if entry["ahet_verified"]:
                n_ahet += 1

        mae, rmse, bias = _metrics(errs)
        per_condition[cname] = {
            "n_total": n_total,
            "n_finite": n_finite,
            "n_ahet": n_ahet,
            "n_nan_radar": n_nan_radar,
            "n_nan_ref": n_nan_ref,
            "mae": mae,
            "rmse": rmse,
            "bias": bias,
        }

    # ------------------------------------------------------------------ intersection
    # Centers that appear in ALL condition dicts AND are finite in ALL conditions
    int_centers = {
        cf for cf in common_keys
        if all(
            _finite(conditions[cn][cf]["radar_hr"]) and
            _finite(conditions[cn][cf]["masimo_pr"])
            for cn in cond_names
        )
    }
    int_center_list = sorted(int_centers)
    n_int = len(int_center_list)

    int_per_cond: dict[str, dict] = {}
    for cname, cdict in conditions.items():
        int_errs = [
            float(cdict[cf]["radar_hr"]) - float(cdict[cf]["masimo_pr"])
            for cf in int_center_list
        ]
        n_ahet_int = sum(1 for cf in int_center_list if cdict[cf]["ahet_verified"])
        mae_i, rmse_i, bias_i = _metrics(int_errs)
        int_per_cond[cname] = {
            "mae": mae_i,
            "rmse": rmse_i,
            "bias": bias_i,
            "n_ahet": n_ahet_int,
            "error_vector": int_errs,
        }

    # ------------------------------------------------------------------ pairwise
    pairwise: dict[str, dict] = {}
    for name_a, name_b in itertools.combinations(cond_names, 2):
        cdict_a = conditions[name_a]
        cdict_b = conditions[name_b]
        err_diffs: list[float] = []
        abs_diffs: list[float] = []
        both = only_a = only_b = neither = 0

        for cf in int_center_list:
            ea = float(cdict_a[cf]["radar_hr"]) - float(cdict_a[cf]["masimo_pr"])
            eb = float(cdict_b[cf]["radar_hr"]) - float(cdict_b[cf]["masimo_pr"])
            err_diffs.append(ea - eb)
            abs_diffs.append(abs(ea) - abs(eb))
            aa = bool(cdict_a[cf]["ahet_verified"])
            ab_ = bool(cdict_b[cf]["ahet_verified"])
            if aa and ab_:
                both += 1
            elif aa:
                only_a += 1
            elif ab_:
                only_b += 1
            else:
                neither += 1

        if err_diffs:
            mean_ed = float(np.mean(err_diffs))
            mean_ad = float(np.mean(abs_diffs))
            med_ad = float(np.median(abs_diffs))
        else:
            mean_ed = mean_ad = med_ad = float("nan")

        pairwise[f"{name_a}_vs_{name_b}"] = {
            "mean_error_diff": mean_ed,
            "mean_abs_error_diff": mean_ad,
            "median_abs_error_diff": med_ad,
            "n_pairs": n_int,
            "error_diff_vector": err_diffs,
            "ahet_transitions": {
                "both_pass": both,
                "only_a_passes": only_a,
                "only_b_passes": only_b,
                "neither_passes": neither,
            },
        }

    return {
        "all_centers": all_centers,
        "per_condition": per_condition,
        "intersection": {
            "center_frames": int_center_list,
            "n_centers": n_int,
            "per_condition": int_per_cond,
            "pairwise": pairwise,
        },
    }


def coverage_table(paired: dict) -> str:
    """Format paired_metrics output as a plain-text table for stdout.

    Shows per-condition n_total, n_finite, n_ahet, n_nan_radar, n_nan_ref,
    MAE, RMSE, Bias — one row per condition — then an Intersection section
    at the bottom with the same columns on the intersection subset.
    """
    cond_names = list(paired["per_condition"].keys())
    n_int = paired["intersection"]["n_centers"]

    header = (
        f"{'Condition':<12} {'n_total':>7} {'n_finite':>8} {'n_ahet':>6} "
        f"{'n_nan_radar':>11} {'n_nan_ref':>9} {'MAE':>6} {'RMSE':>6} {'Bias':>7}"
    )
    sep = "-" * len(header)

    def _f(v: float, w: int = 6) -> str:
        try:
            fv = float(v)
            return f"{fv:{w}.2f}" if np.isfinite(fv) else f"{'NaN':>{w}}"
        except (TypeError, ValueError):
            return f"{'NaN':>{w}}"

    def _row(
        name: str,
        n_total: int,
        n_finite: int,
        n_ahet: int,
        n_nan_radar: int,
        n_nan_ref: int,
        mae: float,
        rmse: float,
        bias: float,
    ) -> str:
        return (
            f"{name:<12} {n_total:>7} {n_finite:>8} {n_ahet:>6} "
            f"{n_nan_radar:>11} {n_nan_ref:>9} {_f(mae)} {_f(rmse)} {_f(bias, 7)}"
        )

    lines = [header, sep]
    for name in cond_names:
        c = paired["per_condition"][name]
        lines.append(_row(
            name,
            c["n_total"], c["n_finite"], c["n_ahet"],
            c["n_nan_radar"], c["n_nan_ref"],
            c["mae"], c["rmse"], c["bias"],
        ))

    lines.append("")
    lines.append(f"Intersection (n={n_int})")
    lines.append(sep)
    for name in cond_names:
        ic = paired["intersection"]["per_condition"].get(name, {})
        lines.append(_row(
            name,
            n_int, n_int,
            ic.get("n_ahet", 0),
            0, 0,
            ic.get("mae", float("nan")),
            ic.get("rmse", float("nan")),
            ic.get("bias", float("nan")),
        ))

    return "\n".join(lines)
