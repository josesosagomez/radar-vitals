"""Step 6 candidate-track diagnostic.

Tests whether temporal continuity can partially substitute for AHET's per-window
second-harmonic SNR gate without introducing severe false accepts.

This is READ-ONLY: does not modify Step 6 outputs, src/vitals.py, or steps/step_6/.

Usage
-----
python -X utf8 scripts/diagnose_step6_candidate_tracks.py \\
    --sessions test test2 test3 test4 test5 \\
    --results-root results \\
    --out results/diagnose \\
    --overwrite [--no-plots]

Output layout
-------------
results/diagnose/step6_candidate_tracks/
    diagnostic.log
    track_candidates.csv            one row per (session, window, candidate_rank)
    track_window_decisions.csv      one row per (session, window, combo)
    track_summary.csv               one row per combo, sorted safety-first (aggregate)
    track_summary_by_session.csv    one row per (session, combo); n_forced_blocked split from n_gap
    baseline_summary_by_session.csv one row per session, current Step 6 stats
    diagnostic_report.md
"""
from __future__ import annotations

import argparse
import itertools
import json
import logging
import math
import subprocess
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.diagnose_step6_hr import (
    GOOD_THRESH_BPM,
    BAD_THRESH_BPM,
    SEVERE_THRESH_BPM,
    _load_session,
    _to_float,
    _to_bool,
)
from steps.step_6.temporal_tracker import (
    GAP_COST,
    HEART_BAND_HZ,
    GUARD_HZ,
    _blocked_reason,
    _filter_eligible,
    _nearest_resp_harmonic,
    _rejection_code_to_str,
    _VState,
    _within_guard,
    build_candidate_pool,
    compute_fundamental_ratio_db,
    node_score,
    viterbi_track,
)

log = logging.getLogger("track_diag")

TRACK_GRID: dict[str, list] = {
    "min_fundamental_ratio_db": [0.0, 2.0, 4.0, 6.0, 8.0],
    "max_jump_bpm_per_hop":     [4.0, 6.0, 8.0, 12.0],
    "max_gap_windows":          [0, 1, 2],
    "resp_harmonic_mode":       ["exclude", "score_penalty"],
}
# 5 × 4 × 3 × 2 = 120 combinations


# ---------------------------------------------------------------------------
# Evaluation helpers
# ---------------------------------------------------------------------------

def _masimo_row(df_row: dict) -> tuple[float, bool]:
    return _to_float(df_row.get("masimo_pr_bpm")), _to_bool(df_row.get("masimo_low_quality", False))


def _window_class_from_error(abs_err: float) -> str:
    if abs_err <= GOOD_THRESH_BPM:
        return "good_valid"
    if abs_err <= BAD_THRESH_BPM:
        return "acceptable_valid"
    if abs_err <= SEVERE_THRESH_BPM:
        return "bad_valid"
    return "severe_bad_valid"


# ---------------------------------------------------------------------------
# Grid runner
# ---------------------------------------------------------------------------

def run_grid(
    sessions_data: dict[str, tuple[dict, list[list[dict]]]],
    track_grid: dict[str, list] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run all parameter grid combos; return (cand_df, decisions_df, summary_df).

    sessions_data: {session_id: (data_dict, window_pool)}
    """
    if track_grid is None:
        track_grid = TRACK_GRID

    # track_candidates_df — session/window level, independent of combo
    cand_rows: list[dict] = []
    for sid, (data, pool) in sessions_data.items():
        df = data["df"]
        for wi, cands in enumerate(pool):
            blocked = _blocked_reason(df.iloc[wi].to_dict())
            for c in cands:
                cand_rows.append({"session_id": sid, "blocked_reason": blocked, **c})
    track_candidates_df = pd.DataFrame(cand_rows) if cand_rows else pd.DataFrame()

    # track_window_decisions_df and track_summary_df — one entry per combo
    decision_rows: list[dict] = []
    summary_rows:  list[dict] = []

    thresh_keys = list(track_grid.keys())
    all_combos  = list(itertools.product(*[track_grid[k] for k in thresh_keys]))

    for combo_vals in all_combos:
        combo        = dict(zip(thresh_keys, combo_vals))
        min_fund_db  = combo["min_fundamental_ratio_db"]
        max_jump     = combo["max_jump_bpm_per_hop"]
        max_gap      = combo["max_gap_windows"]
        harm_mode    = combo["resp_harmonic_mode"]

        agg_good = agg_accept = agg_bad = agg_severe = agg_gap = agg_eval = 0
        agg_abs:    list[float] = []
        agg_signed: list[float] = []

        for sid, (data, pool) in sessions_data.items():
            df = data["df"]
            n  = data["n_windows"]

            filtered = [_filter_eligible(cands, min_fund_db, harm_mode) for cands in pool]
            path     = viterbi_track(filtered, max_jump, max_gap, harm_mode)

            for wi in range(n):
                df_row   = df.iloc[wi].to_dict()
                decision = path[wi]
                blocked  = _blocked_reason(df_row)
                masimo_bpm, masimo_low = _masimo_row(df_row)

                if blocked:
                    dtype = "forced_blocked"
                elif decision is None:
                    dtype = "gap"
                else:
                    dtype = "candidate"

                decided_bpm  = decision["bpm"] if decision is not None else float("nan")
                decided_rank = decision["candidate_rank"] if decision is not None else float("nan")
                ns_key       = "node_score_penalty" if harm_mode == "score_penalty" else "node_score_exclude"
                decided_ns   = decision[ns_key] if decision is not None else float("nan")
                n_eligible   = len(filtered[wi])

                masimo_ok    = math.isfinite(masimo_bpm) and not masimo_low
                abs_err      = float("nan")
                window_class = "gap"

                if decision is not None and masimo_ok:
                    abs_err      = abs(decided_bpm - masimo_bpm)
                    window_class = _window_class_from_error(abs_err)
                    agg_eval    += 1
                    agg_abs.append(abs_err)
                    agg_signed.append(decided_bpm - masimo_bpm)
                    if window_class == "good_valid":
                        agg_good   += 1
                    elif window_class == "acceptable_valid":
                        agg_accept += 1
                    elif window_class == "bad_valid":
                        agg_bad    += 1
                    else:
                        agg_severe += 1
                elif decision is None:
                    agg_gap += 1

                decision_rows.append({
                    "session_id":               sid,
                    "window_index":             wi,
                    "min_fundamental_ratio_db": min_fund_db,
                    "max_jump_bpm_per_hop":     max_jump,
                    "max_gap_windows":          max_gap,
                    "resp_harmonic_mode":       harm_mode,
                    "decision_bpm":             decided_bpm,
                    "decision_type":            dtype,
                    "selected_rank":            decided_rank,
                    "node_score":               decided_ns,
                    "n_eligible_candidates":    n_eligible,
                    "masimo_pr_bpm":            masimo_bpm,
                    "masimo_low_quality":       masimo_low,
                    "abs_error_bpm":            abs_err,
                    "window_class":             window_class,
                })

        mae  = float(np.mean(agg_abs))                              if agg_abs else float("nan")
        rmse = float(np.sqrt(np.mean(np.array(agg_abs) ** 2)))     if agg_abs else float("nan")
        bias = float(np.mean(agg_signed))                           if agg_signed else float("nan")

        summary_rows.append({
            **combo,
            "n_good_valid":       agg_good,
            "n_acceptable_valid": agg_accept,
            "n_bad_valid":        agg_bad,
            "n_severe_bad_valid": agg_severe,
            "n_gap":              agg_gap,
            "n_evaluable":        agg_eval,
            "mae_bpm":            mae,
            "rmse_bpm":           rmse,
            "bias_bpm":           bias,
        })

    decisions_df = pd.DataFrame(decision_rows)
    summary_df   = pd.DataFrame(summary_rows)
    if not summary_df.empty:
        summary_df["_neg_good"] = -summary_df["n_good_valid"]
        summary_df = (
            summary_df
            .sort_values(
                ["n_severe_bad_valid", "n_bad_valid", "_neg_good", "mae_bpm"],
                ascending=True,
            )
            .drop(columns=["_neg_good"])
            .reset_index(drop=True)
        )

    return track_candidates_df, decisions_df, summary_df


# ---------------------------------------------------------------------------
# Per-session tracker summary
# ---------------------------------------------------------------------------

def build_summary_by_session(decisions_df: pd.DataFrame) -> pd.DataFrame:
    """One row per (session_id, combo) with per-session counts and error metrics.

    n_gap counts only DP-chosen gaps (decision_type=="gap").
    n_forced_blocked counts windows blocked before the tracker ran (decision_type=="forced_blocked").
    These are kept separate so blocked sessions (e.g. heavy Step 5 invalid) are visible.
    bias_bpm = mean(decision_bpm - masimo_pr_bpm) for evaluable windows (signed, not absolute).
    """
    if decisions_df.empty:
        return pd.DataFrame()

    combo_keys = [
        "min_fundamental_ratio_db", "max_jump_bpm_per_hop",
        "max_gap_windows", "resp_harmonic_mode",
    ]
    group_keys = ["session_id"] + combo_keys
    rows: list[dict] = []
    for key_vals, grp in decisions_df.groupby(group_keys, sort=False):
        key_dict = dict(zip(group_keys, key_vals))
        evaluable = grp[grp["window_class"] != "gap"]
        abs_errs  = pd.to_numeric(evaluable["abs_error_bpm"], errors="coerce").dropna()
        decision_bpm = pd.to_numeric(evaluable["decision_bpm"], errors="coerce")
        masimo_bpm   = pd.to_numeric(evaluable["masimo_pr_bpm"], errors="coerce")
        signed = (decision_bpm - masimo_bpm).dropna()
        rows.append({
            **key_dict,
            "n_good_valid":       int((grp["window_class"] == "good_valid").sum()),
            "n_acceptable_valid": int((grp["window_class"] == "acceptable_valid").sum()),
            "n_bad_valid":        int((grp["window_class"] == "bad_valid").sum()),
            "n_severe_bad_valid": int((grp["window_class"] == "severe_bad_valid").sum()),
            "n_gap":              int((grp["decision_type"] == "gap").sum()),
            "n_forced_blocked":   int((grp["decision_type"] == "forced_blocked").sum()),
            "n_evaluable":        int(len(evaluable)),
            "mae_bpm":            float(abs_errs.mean())                       if len(abs_errs) > 0 else float("nan"),
            "rmse_bpm":           float(np.sqrt((abs_errs ** 2).mean()))       if len(abs_errs) > 0 else float("nan"),
            "bias_bpm":           float(signed.mean())                         if len(signed)   > 0 else float("nan"),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Baseline Step 6 summary (from existing CSV output)
# ---------------------------------------------------------------------------

def _baseline_summary(sessions_data: dict[str, tuple[dict, list]]) -> dict:
    dfs = [data["df"] for data, _ in sessions_data.values()]
    if not dfs:
        return {}
    all_df   = pd.concat(dfs, ignore_index=True)
    valid     = all_df["hr_valid"].astype(str).str.lower().isin(("true", "1"))
    masimo_ok = ~all_df["masimo_low_quality"].astype(str).str.lower().isin(("true", "1"))
    masimo_bpm = pd.to_numeric(all_df["masimo_pr_bpm"], errors="coerce")
    abs_err   = pd.to_numeric(all_df.get("hr_abs_error_bpm", pd.Series(dtype=float)), errors="coerce")
    signed_err = pd.to_numeric(all_df.get("hr_error_bpm", pd.Series(dtype=float)), errors="coerce")
    evaluable = valid & masimo_ok & masimo_bpm.notna()
    errs      = abs_err[evaluable].dropna()
    return {
        "n_good_valid":       int((evaluable & (abs_err <= GOOD_THRESH_BPM)).sum()),
        "n_acceptable_valid": int((evaluable & (abs_err >  GOOD_THRESH_BPM) & (abs_err <= BAD_THRESH_BPM)).sum()),
        "n_bad_valid":        int((evaluable & (abs_err >  BAD_THRESH_BPM)  & (abs_err <= SEVERE_THRESH_BPM)).sum()),
        "n_severe_bad_valid": int((evaluable & (abs_err >  SEVERE_THRESH_BPM)).sum()),
        "n_evaluable":        int(evaluable.sum()),
        "mae_bpm":            float(errs.mean())                         if len(errs) > 0 else float("nan"),
        "rmse_bpm":           float(np.sqrt((errs ** 2).mean()))         if len(errs) > 0 else float("nan"),
        "bias_bpm":           float(signed_err[evaluable].dropna().mean()) if evaluable.any() else float("nan"),
    }


def _baseline_summary_by_session(sessions_data: dict[str, tuple[dict, list]]) -> pd.DataFrame:
    """One row per session_id with current Step 6 stats (same metrics as _baseline_summary)."""
    rows: list[dict] = []
    for sid, (data, pool) in sessions_data.items():
        b = _baseline_summary({sid: (data, pool)})
        rows.append({"session_id": sid, **b})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------

def build_report(
    summary_df: pd.DataFrame,
    baseline: dict,
    sessions: list[str],
    n_combos: int,
    summary_by_session_df: pd.DataFrame | None = None,
    baseline_by_session_df: pd.DataFrame | None = None,
) -> str:
    def _fmt(v) -> str:
        return f"{v:.2f}" if isinstance(v, float) and math.isfinite(v) else "—"

    def _int_or_dash(v) -> str:
        if v is None:
            return "—"
        try:
            f = float(v)
            return str(int(f)) if math.isfinite(f) else "—"
        except (TypeError, ValueError):
            return "—"

    lines = [
        "# Step 6 Candidate-Track Diagnostic Report",
        "",
        "**Hypothesis:** Temporal continuity can partially substitute for AHET's per-window "
        "second-harmonic SNR gate without introducing severe false accepts.",
        "",
        f"Sessions: {', '.join(sessions)}  |  Grid combos: {n_combos}",
        "",
        "## Baseline (current Step 6)",
        "",
        "| good_valid | acceptable_valid | bad_valid | severe_bad_valid | evaluable | MAE (bpm) |",
        "|------------|-----------------|-----------|-----------------|-----------|-----------|",
        f"| {baseline.get('n_good_valid','—')} "
        f"| {baseline.get('n_acceptable_valid','—')} "
        f"| {baseline.get('n_bad_valid','—')} "
        f"| {baseline.get('n_severe_bad_valid','—')} "
        f"| {baseline.get('n_evaluable','—')} "
        f"| {_fmt(baseline.get('mae_bpm', float('nan')))} |",
        "",
    ]

    if summary_df.empty:
        lines += ["## Result", "", "No output produced (no sessions loaded)."]
        return "\n".join(lines) + "\n"

    best = summary_df.iloc[0]
    lines += [
        "## Best combo (safety-first: severe → bad → −good → MAE)",
        "",
        f"- `min_fundamental_ratio_db = {best['min_fundamental_ratio_db']}`",
        f"- `max_jump_bpm_per_hop = {best['max_jump_bpm_per_hop']}`",
        f"- `max_gap_windows = {int(best['max_gap_windows'])}`",
        f"- `resp_harmonic_mode = {best['resp_harmonic_mode']}`",
        "",
        "| Metric | Baseline | Best combo |",
        "|--------|----------|------------|",
        f"| good_valid       | {baseline.get('n_good_valid','—')} | {int(best['n_good_valid'])} |",
        f"| acceptable_valid | {baseline.get('n_acceptable_valid','—')} | {int(best['n_acceptable_valid'])} |",
        f"| bad_valid        | {baseline.get('n_bad_valid','—')} | {int(best['n_bad_valid'])} |",
        f"| severe_bad_valid | {baseline.get('n_severe_bad_valid','—')} | {int(best['n_severe_bad_valid'])} |",
        f"| evaluable        | {baseline.get('n_evaluable','—')} | {int(best['n_evaluable'])} |",
        f"| MAE (bpm)        | {_fmt(baseline.get('mae_bpm', float('nan')))} | {_fmt(best['mae_bpm'])} |",
        "",
    ]

    # "Safe" = no bad-valid (>10 bpm) and no severe-bad-valid (>15 bpm) windows.
    # Acceptable-valid (5-10 bpm) is permitted. This affects only conclusion text, not sort order.
    safe_df = summary_df[
        (summary_df["n_severe_bad_valid"] == 0) & (summary_df["n_bad_valid"] == 0)
    ]
    n_safe = len(safe_df)
    lines += ["## Conclusion", ""]

    if safe_df.empty:
        lines.append(
            "**All combos produce bad or severe false accepts (>10 bpm error).** "
            "Temporal continuity is insufficient as an AHET substitute. "
            "Do not promote Step 6.3 tracker."
        )
    else:
        base_good = baseline.get("n_good_valid", 0)
        best_safe_good = int(safe_df.iloc[0]["n_good_valid"])
        if best_safe_good > base_good:
            lines.append(
                f"**{n_safe} combo(s) improve good/acceptable yield with zero bad or severe false accepts.** "
                f"A Step 6.3 production tracker may be justified. "
                f"Verify per-session lists in track_window_decisions.csv before deciding."
            )
        else:
            lines.append(
                f"**{n_safe} safe combo(s) found but none improve good_valid count vs baseline "
                f"({base_good}).** "
                f"Temporal continuity does not add yield for these sessions. "
                f"Do not promote Step 6.3 tracker."
            )

    # Per-session breakdown for the aggregate-best combo
    if (
        summary_by_session_df is not None
        and baseline_by_session_df is not None
        and not summary_by_session_df.empty
        and not baseline_by_session_df.empty
    ):
        best = summary_df.iloc[0]
        mask = (
            (summary_by_session_df["min_fundamental_ratio_db"] == best["min_fundamental_ratio_db"])
            & (summary_by_session_df["max_jump_bpm_per_hop"] == best["max_jump_bpm_per_hop"])
            & (summary_by_session_df["max_gap_windows"] == best["max_gap_windows"])
            & (summary_by_session_df["resp_harmonic_mode"] == best["resp_harmonic_mode"])
        )
        sess_best = summary_by_session_df[mask].set_index("session_id")
        bl_df     = baseline_by_session_df.set_index("session_id")
        lines += [
            "",
            "## Best Combo By Session",
            "",
            "BL = current Step 6 baseline; Trk = tracker replay at aggregate-best combo params.  ",
            "`Trk blkd` = windows forced-blocked by Step 5 invalid/edge-lock (not a tracker failure).",
            "",
            "| Session"
            " | BL eval | BL good | BL accept | BL bad | BL sev | BL MAE"
            " | Trk eval | Trk blkd | Trk good | Trk accept | Trk bad | Trk sev | Trk MAE |",
            "|---------|---------|---------|----------|--------|--------|--------|"
            "----------|----------|---------|------------|---------|---------|---------|",
        ]
        for sid in sessions:
            bl = bl_df.loc[sid].to_dict() if sid in bl_df.index else {}
            tr = sess_best.loc[sid].to_dict() if sid in sess_best.index else {}
            lines.append(
                f"| {sid}"
                f" | {bl.get('n_evaluable', '—')}"
                f" | {bl.get('n_good_valid', '—')}"
                f" | {bl.get('n_acceptable_valid', '—')}"
                f" | {bl.get('n_bad_valid', '—')}"
                f" | {bl.get('n_severe_bad_valid', '—')}"
                f" | {_fmt(bl.get('mae_bpm', float('nan')))}"
                f" | {_int_or_dash(tr.get('n_evaluable'))}"
                f" | {_int_or_dash(tr.get('n_forced_blocked'))}"
                f" | {_int_or_dash(tr.get('n_good_valid'))}"
                f" | {_int_or_dash(tr.get('n_acceptable_valid'))}"
                f" | {_int_or_dash(tr.get('n_bad_valid'))}"
                f" | {_int_or_dash(tr.get('n_severe_bad_valid'))}"
                f" | {_fmt(tr.get('mae_bpm', float('nan')))} |"
            )
        lines.append("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Git commit
# ---------------------------------------------------------------------------

def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(REPO_ROOT), text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--sessions",           nargs="+", required=True)
    ap.add_argument("--results-root",       default="results")
    ap.add_argument("--out",                default="results/diagnose")
    ap.add_argument("--overwrite",          action="store_true")
    ap.add_argument("--no-plots",           action="store_true")
    ap.add_argument("--heart-band-hz",      nargs=2, type=float,
                    default=list(HEART_BAND_HZ), metavar=("LO", "HI"))
    ap.add_argument("--resp-harmonic-guard-hz", type=float, default=GUARD_HZ)
    args = ap.parse_args(argv)

    out_dir      = REPO_ROOT / args.out / "step6_candidate_tracks"
    results_root = REPO_ROOT / args.results_root
    heart_band   = tuple(args.heart_band_hz)
    guard_hz     = args.resp_harmonic_guard_hz

    if out_dir.exists() and not args.overwrite:
        print(f"Output already exists: {out_dir}\nRe-run with --overwrite.", file=sys.stderr)
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(out_dir / "diagnostic.log", mode="w", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    log.info("diagnose_step6_candidate_tracks — commit %s", _git_commit())
    log.info("Sessions: %s", args.sessions)
    log.info("Output:   %s", out_dir)

    sessions_data: dict[str, tuple[dict, list[list[dict]]]] = {}
    failed: list[str] = []

    for sid in args.sessions:
        log.info("--- Loading %s ---", sid)
        data = _load_session(sid, results_root)
        if data is None:
            failed.append(sid)
            continue
        pool = build_candidate_pool(data, heart_band, guard_hz)
        n_total   = sum(len(w) for w in pool)
        n_blocked = sum(
            1 for wi in range(data["n_windows"])
            if not pool[wi] and _blocked_reason(data["df"].iloc[wi].to_dict())
        )
        log.info(
            "  %d windows | %d candidate slots | %d blocked windows",
            data["n_windows"], n_total, n_blocked,
        )
        sessions_data[sid] = (data, pool)

    if not sessions_data:
        log.error("No sessions loaded — aborting.")
        return 1

    n_combos = len(list(itertools.product(*TRACK_GRID.values())))
    log.info("Running %d combos across %d sessions...", n_combos, len(sessions_data))

    cand_df, decisions_df, summary_df = run_grid(sessions_data)

    if not cand_df.empty:
        cand_df.to_csv(out_dir / "track_candidates.csv", index=False)
    decisions_df.to_csv(out_dir / "track_window_decisions.csv", index=False)
    summary_df.to_csv(out_dir / "track_summary.csv", index=False)

    summary_by_session_df  = build_summary_by_session(decisions_df)
    baseline_by_session_df = _baseline_summary_by_session(sessions_data)
    summary_by_session_df.to_csv(out_dir / "track_summary_by_session.csv", index=False)
    baseline_by_session_df.to_csv(out_dir / "baseline_summary_by_session.csv", index=False)

    baseline = _baseline_summary(sessions_data)
    report   = build_report(
        summary_df, baseline, list(sessions_data.keys()), n_combos,
        summary_by_session_df=summary_by_session_df,
        baseline_by_session_df=baseline_by_session_df,
    )
    (out_dir / "diagnostic_report.md").write_text(report, encoding="utf-8")

    if not summary_df.empty:
        best = summary_df.iloc[0]
        log.info(
            "Best combo: min_fund_db=%.1f max_jump=%.1f max_gap=%d harm=%s "
            "→ good=%d bad=%d severe=%d MAE=%s",
            best["min_fundamental_ratio_db"], best["max_jump_bpm_per_hop"],
            int(best["max_gap_windows"]), best["resp_harmonic_mode"],
            int(best["n_good_valid"]), int(best["n_bad_valid"]),
            int(best["n_severe_bad_valid"]),
            f"{best['mae_bpm']:.2f}" if math.isfinite(best["mae_bpm"]) else "—",
        )

    if failed:
        log.warning("Failed sessions (missing Step 6 outputs): %s", failed)
    log.info("Done. Output: %s", out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
