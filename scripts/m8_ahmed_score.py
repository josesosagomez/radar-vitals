"""Score the M8 Ahmed all-bins sweep against Masimo, alongside production on identical cells.

Consumes `scripts/m8_ahmed_all_bins.py`'s radar-only output and `scripts/diagnose_bin_sweep.py`'s
production output, joins them on `(capture_id, k, bin)`, and reports three conditions per arm:

* **production lock** — the bin the deployed system would have used, re-derived with current
  code. This is the only deployable number.
* **best bin per capture** — chosen post-hoc using the reference. A **CEILING, NOT A RESULT**;
  no honest selector can beat it, and it is reported so that "the bin was wrong" cannot be
  offered as an excuse for a negative finding.
* **every fixed bin** — the full 14-row picture, so a reader can see the whole surface.

Two baselines are mandatory, not optional:

* **`constant_session_median`** — a predictor that ignores the radar entirely and emits the
  session's median reference. `HANDOFF.md` §2.2 measured that this scores 83-100 % of admissible
  HR windows on these captures, because within-session PR spread (2.6-5.2 bpm) is narrower than
  the +/-5 bpm tolerance. **Any HR number not reported next to it is uninterpretable.**
* **production's own estimator** at the same cells, so the comparison is estimator-vs-estimator.

A note on coverage that decides how the whole table reads: **Ahmed never abstains.** It has no
verification stage, so it emits an argmax for every cell — 100 % coverage by construction.
Production's HR coverage is low precisely because AHET *refuses to guess*. Comparing the two
coverages is comparing an estimator that verifies against one that does not, so coverage is
reported but is never scored as if Ahmed had won it.

Usage
-----
    conda run -n radar-vitals python -X utf8 scripts/m8_ahmed_score.py \
        --ahmed-run results/m8/ahmed_all_bins/<stamp>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from src import masimo as masimo_mod                                    # noqa: E402
from src.br_features import SUBJECT_BY_CAPTURE, capture_suffix          # noqa: E402
from src.comparator import br_reference, hr_reference                   # noqa: E402
from src.m4.window_grid import (                                        # noqa: E402
    FRAMES_PER_WINDOW, build_window_grid,
)

import diagnose_bin_drift as bindrift                                   # noqa: E402
from score_offline import resolve_frame0_epoch                          # noqa: E402
from simulate_bin_policy import current_code_lock                       # noqa: E402
from m8_ahmed_all_bins import write_csv                                 # noqa: E402

#: HR tolerance from `notes/comparator_prespec.md`; BR from `..._br.md` §2.3.
HR_HIT_BPM = 5.0
BR_HIT_BPM = (2.0, 3.0)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def window_references(capture_dir: Path, n_windows: int) -> pd.DataFrame:
    """Per-window HR and BR reference on the frozen grid, with both admissibility gates."""
    meta = json.loads((capture_dir / "run_metadata.json").read_text(encoding="utf-8"))
    frame0, origin, approx, _c = resolve_frame0_epoch(meta)
    csv_path = [p for p in capture_dir.glob("*.csv") if p.name != "live_estimates.csv"][0]
    df = masimo_mod.load_masimo(csv_path)
    rows = []
    for w in build_window_grid(n_windows * FRAMES_PER_WINDOW, frame0,
                               fs=20.0, frames_per_win=FRAMES_PER_WINDOW):
        hr = hr_reference(df, w.epoch_start, w.epoch_end)
        br = br_reference(df, w.epoch_start, w.epoch_end)
        rows.append({
            "k": int(w.k),
            "hr_ref_bpm": hr["median_pr_bpm"], "hr_admitted": bool(hr["admitted"]),
            "br_ref_bpm": br["median_rr_bpm"], "br_admitted": bool(br["admitted"]),
        })
    out = pd.DataFrame(rows)
    out.attrs["frame0_origin"] = origin
    out.attrs["frame0_is_approximate"] = bool(approx)
    return out


def _score(err: np.ndarray, n_windows: int, n_emitted: int, hit_bands) -> dict:
    err = err[np.isfinite(err)]
    out = {
        "n_windows": int(n_windows),
        "n_emitted": int(n_emitted),
        "coverage": round(n_emitted / n_windows, 4) if n_windows else None,
        "n_scored": int(err.size),
        "mae_bpm": round(float(np.mean(err)), 4) if err.size else None,
        "rmse_bpm": round(float(np.sqrt(np.mean(err ** 2))), 4) if err.size else None,
    }
    for b in hit_bands:
        out[f"hit_{b:g}bpm"] = round(float(np.mean(err <= b)), 4) if err.size else None
    return out


def score_condition(cells: pd.DataFrame, vital: str, hit_bands) -> dict:
    """Score one already-selected (one row per window) set of estimates."""
    ref = cells[f"{vital}_ref_bpm"].to_numpy(dtype=float)
    est = cells[f"{vital}_est_bpm"].to_numpy(dtype=float)
    emitted = cells[f"{vital}_emitted"].to_numpy(dtype=bool)
    scored = emitted & np.isfinite(ref) & np.isfinite(est)
    return _score(np.abs(est[scored] - ref[scored]), len(cells), int(emitted.sum()), hit_bands)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ahmed-run", type=Path, required=True)
    ap.add_argument("--production-run", type=Path,
                    default=REPO_ROOT / "results/diagnose/bin_sweep/20260804T131040Z")
    ap.add_argument("--config", type=Path,
                    default=REPO_ROOT / "scripts" / "live_demo_config.yaml")
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "results" / "m8" / "ahmed_score")
    args = ap.parse_args(argv)

    ahmed = pd.read_csv(args.ahmed_run / "windows.csv")
    prod = pd.read_csv(args.production_run / "windows.csv")
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) / stamp
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"-> {out_dir}", flush=True)

    rows, locks, origins = [], {}, {}
    for cid, g in ahmed.groupby("capture_id", sort=True):
        capture_dir = REPO_ROOT / "results" / "live_demo" / cid
        n_windows = int(g["k"].nunique())
        refs = window_references(capture_dir, n_windows)
        origins[cid] = {"origin": refs.attrs["frame0_origin"],
                        "approximate": refs.attrs["frame0_is_approximate"]}
        lock = current_code_lock(capture_dir, cfg)
        locks[cid] = int(lock)
        subject = SUBJECT_BY_CAPTURE[capture_suffix(cid)]
        pg = prod[prod["capture_id"] == cid]

        # ── baseline: ignores the radar entirely ────────────────────────────
        for vital, hit in (("hr", (HR_HIT_BPM,)), ("br", BR_HIT_BPM)):
            adm = refs[refs[f"{vital}_admitted"]]
            med = float(np.nanmedian(adm[f"{vital}_ref_bpm"])) if len(adm) else np.nan
            cells = pd.DataFrame({
                f"{vital}_ref_bpm": np.where(refs[f"{vital}_admitted"],
                                             refs[f"{vital}_ref_bpm"], np.nan),
                f"{vital}_est_bpm": med,
                f"{vital}_emitted": True,
            })
            rows.append({"capture_id": cid, "subject": subject, "vital": vital,
                         "method": "constant_session_median", "condition": "no_radar",
                         "bin": None, **score_condition(cells, vital, hit)})

        # ── production, at its own re-derived lock ──────────────────────────
        pl = pg[pg["bin"] == lock].merge(refs, on="k", how="left")
        for vital, col, valid_col, hit in (
            ("hr", "hr_bpm", "hr_valid", (HR_HIT_BPM,)),
            ("br", "br_bpm", "br_valid", BR_HIT_BPM),
        ):
            cells = pd.DataFrame({
                f"{vital}_ref_bpm": np.where(pl[f"{vital}_admitted"],
                                             pl[f"{vital}_ref_bpm"], np.nan),
                f"{vital}_est_bpm": pd.to_numeric(pl[col], errors="coerce"),
                f"{vital}_emitted": pl[valid_col].fillna(0).astype(bool),
            })
            rows.append({"capture_id": cid, "subject": subject, "vital": vital,
                         "method": "production_eca_ahet", "condition": "production_lock",
                         "bin": lock, **score_condition(cells, vital, hit)})

        # ── Ahmed: every arm, every condition ───────────────────────────────
        for arm, ag in g.groupby("arm_id", sort=True):
            m = ag.merge(refs, on="k", how="left")
            for vital, col, valid_col, hit in (
                ("hr", "hr_bpm", "hr_valid", (HR_HIT_BPM,)),
                ("br", "br_bpm", "br_valid", BR_HIT_BPM),
            ):
                m2 = m.assign(**{
                    f"{vital}_ref_bpm": np.where(m[f"{vital}_admitted"],
                                                 m[f"{vital}_ref_bpm"], np.nan),
                    f"{vital}_est_bpm": pd.to_numeric(m[col], errors="coerce"),
                    f"{vital}_emitted": m[valid_col].fillna(False).astype(bool),
                })
                # every fixed bin
                per_bin = {}
                for b, bg in m2.groupby("bin", sort=True):
                    sc = score_condition(bg, vital, hit)
                    per_bin[int(b)] = sc
                    rows.append({"capture_id": cid, "subject": subject, "vital": vital,
                                 "method": arm, "condition": "fixed_bin", "bin": int(b),
                                 **sc})
                # production's lock
                if lock in per_bin:
                    rows.append({"capture_id": cid, "subject": subject, "vital": vital,
                                 "method": arm, "condition": "production_lock",
                                 "bin": lock, **per_bin[lock]})
                # best bin, chosen using the reference -- a CEILING, not a result
                scored = {b: s for b, s in per_bin.items() if s["mae_bpm"] is not None}
                if scored:
                    best = min(scored, key=lambda b: scored[b]["mae_bpm"])
                    rows.append({"capture_id": cid, "subject": subject, "vital": vital,
                                 "method": arm, "condition": "best_bin_CEILING",
                                 "bin": best, **scored[best]})

    cols = ["capture_id", "subject", "vital", "method", "condition", "bin",
            "n_windows", "n_emitted", "coverage", "n_scored", "mae_bpm", "rmse_bpm",
            "hit_5bpm", "hit_2bpm", "hit_3bpm"]
    write_csv(out_dir / "scores.csv", cols, rows)

    df = pd.DataFrame(rows)
    pooled = []
    for (vital, method, condition), g in df.groupby(["vital", "method", "condition"]):
        g = g[g["mae_bpm"].notna()]
        if not len(g):
            continue
        pooled.append({
            "vital": vital, "method": method, "condition": condition,
            "n_captures": int(g["capture_id"].nunique()),
            "n_scored": int(g["n_scored"].sum()),
            "mean_coverage": round(float(g["coverage"].mean()), 4),
            "mae_bpm": round(float(np.average(g["mae_bpm"], weights=g["n_scored"])), 4),
            "hit_5bpm": (round(float(np.average(g["hit_5bpm"], weights=g["n_scored"])), 4)
                         if vital == "hr" else None),
            "hit_3bpm": (round(float(np.average(g["hit_3bpm"], weights=g["n_scored"])), 4)
                         if vital == "br" else None),
        })
    # `fixed_bin` rows are per-bin and would double-count if pooled with the rest.
    pooled = [p for p in pooled if p["condition"] != "fixed_bin"]
    write_csv(out_dir / "pooled.csv",
              ["vital", "method", "condition", "n_captures", "n_scored", "mean_coverage",
               "mae_bpm", "hit_5bpm", "hit_3bpm"], pooled)

    meta = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": bindrift.get_git_commit(),
        "git_tree_clean": bindrift.is_tree_clean(),
        "ahmed_run": str(args.ahmed_run),
        "production_run": str(args.production_run),
        "input_hashes": {
            "ahmed_windows_csv": sha256(args.ahmed_run / "windows.csv"),
            "production_windows_csv": sha256(args.production_run / "windows.csv"),
        },
        "parent_gate_bundle": json.loads(
            (args.ahmed_run / "run_meta.json").read_text(encoding="utf-8")
        )["parent_gate_bundle"],
        "locks_rederived": locks,
        "frame0_origins": origins,
        "hr_limit": (
            "HANDOFF §2.2: these captures support HR coverage/feasibility, NOT HR tracking. "
            "Within-session PR spread is 2.6-5.2 bpm, narrower than the +/-5 bpm tolerance, so "
            "constant_session_median scores 83-100%. No HR agreement claim may be made here."
        ),
        "ceiling_note": (
            "best_bin_CEILING selects the bin using the reference. It is an upper bound that no "
            "deployable selector can reach, reported so a negative result cannot be blamed on "
            "bin choice. It is never a result."
        ),
        "coverage_note": (
            "Ahmed has no verification stage and emits an argmax for every cell, so its coverage "
            "is 100% by construction. Production's HR coverage is low because AHET refuses to "
            "guess. These coverages are not comparable quantities."
        ),
    }
    (out_dir / "run_meta.json").write_text(json.dumps(meta, indent=2, default=str),
                                           encoding="utf-8", newline="\n")

    for vital in ("hr", "br"):
        print(f"\n=== {vital.upper()} — pooled, weighted by scored windows ===")
        sub = sorted([p for p in pooled if p["vital"] == vital],
                     key=lambda p: (p["condition"], p["mae_bpm"]))
        print(f"{'method':<46} {'condition':<19} {'cov':>6} {'MAE':>7} {'hit':>6} {'n':>5}")
        for p in sub:
            hit = p["hit_5bpm"] if vital == "hr" else p["hit_3bpm"]
            print(f"{p['method']:<46} {p['condition']:<19} {p['mean_coverage']:>6.3f} "
                  f"{p['mae_bpm']:>7.3f} {str(hit):>6} {p['n_scored']:>5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
