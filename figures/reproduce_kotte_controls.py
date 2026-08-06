"""M9 controls CLI (CLAUDE.md section 3 rule 4: figures come from committed scripts).

Subcommands:
  paper     control 1 — R1 (Fig 8, gating) / R2 (Fig 5) / R3 (Fig 7 row 2) + audits
  ablation  control 2 — fixed-endpoint 20->4 RX ablation

Official runs require a clean git tree and write to results/m9_kotte_controls/<run_id>/.
--smoke permits a dirty tree and writes to <run_id>_smoke/ — labelled non-gating
scratch that can never gate, score, or decide (plans/m9_kotte_plan.md).

Run via:
  & 'C:\\ProgramData\\anaconda3\\condabin\\conda.bat' run -n radar-vitals python -X utf8 \\
      figures/reproduce_kotte_controls.py paper
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.m4.bundle import new_run_id, sha256_bytes  # noqa: E402
from src.m9.paper_control import (  # noqa: E402
    ControlsConfig,
    assemble_run_meta,
    load_experiment_config,
    require_clean_tree,
    run_ablation,
    run_paper_controls,
    write_json,
)

RESULTS_ROOT = REPO_ROOT / "results" / "m9_kotte_controls"
ALL_SECTIONS = ("r1", "r2", "r3", "audits")


def _save_case_figure(out_dir: Path, case_id: str, evidence: dict) -> None:
    if "objective" not in evidence:
        return
    grid = evidence["grid_hz"]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4), constrained_layout=True)
    for ax, key, title in (
        (axes[0], "objective", "selection objective 1'H^-1 1"),
        (axes[1], "beta_sq", "|beta_hat|^2 (eq 26)"),
    ):
        surface = evidence[key]
        image = ax.imshow(
            surface.T,
            origin="lower",
            extent=[grid[0], grid[-1], grid[0], grid[-1]],
            aspect="auto",
            interpolation="nearest",
        )
        ax.set_xlabel("f1 (Hz)")
        ax.set_ylabel("f2 (Hz)")
        ax.set_title(title)
        fig.colorbar(image, ax=ax, shrink=0.85)
    fig.suptitle(case_id)
    safe = case_id.replace(":", "_").replace("=", "_").replace("/", "_")
    fig.savefig(out_dir / f"{safe}.png", dpi=110)
    plt.close(fig)


def _run_dir(config: ControlsConfig, smoke: bool) -> Path:
    run_id = new_run_id(config.whole_file_sha256)
    return RESULTS_ROOT / (f"{run_id}_smoke" if smoke else run_id)


def _finalize(
    config: ControlsConfig,
    out_dir: Path,
    *,
    subcommand: str,
    smoke: bool,
    dirty: list[str],
    payloads: dict[str, dict],
    case_seed_map: dict,
    extra: dict,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    output_hashes: dict[str, str] = {}
    for name, payload in payloads.items():
        output_hashes[name] = write_json(out_dir / name, payload)
    for figure in sorted((out_dir / "figures").glob("*.png")) if (
        out_dir / "figures"
    ).is_dir() else []:
        output_hashes[f"figures/{figure.name}"] = sha256_bytes(figure.read_bytes())
    for npz in sorted((out_dir / "evidence").glob("*.npz")) if (
        out_dir / "evidence"
    ).is_dir() else []:
        output_hashes[f"evidence/{npz.name}"] = sha256_bytes(npz.read_bytes())
    run_meta = assemble_run_meta(
        config,
        subcommand=subcommand,
        smoke=smoke,
        dirty_paths=dirty,
        case_seed_map=case_seed_map,
        output_hashes=output_hashes,
        extra=extra,
    )
    write_json(out_dir / "run_meta.json", run_meta)
    print(f"[m9-controls] wrote {out_dir}")


def cmd_paper(args: argparse.Namespace) -> int:
    config = load_experiment_config(args.config)
    dirty = require_clean_tree(smoke=args.smoke)
    sections = [s.strip() for s in args.sections.split(",") if s.strip()]
    unknown = set(sections) - set(ALL_SECTIONS)
    if unknown:
        raise SystemExit(f"unknown sections {sorted(unknown)}; pick from {ALL_SECTIONS}")

    out_dir = _run_dir(config, args.smoke)
    (out_dir / "figures").mkdir(parents=True, exist_ok=True)
    (out_dir / "evidence").mkdir(parents=True, exist_ok=True)

    # Re-run sections, harvesting evidence for figures/NPZ before JSON assembly.
    from src.m9 import paper_control as pc

    results: dict[str, dict] = {}
    case_seed_map: dict[str, dict] = {}
    section_runners = {
        "r1": pc.run_r1,
        "r2": pc.run_r2,
        "r3": pc.run_r3,
    }
    for section in sections:
        if section == "audits":
            results["audits"] = pc.run_audits(config)
            continue
        section_result = section_runners[section](config)
        case_map = section_result.pop("_case_map", {})
        for case_id, payload in case_map.items():
            result = payload["result"]
            case_seed_map[case_id] = {"seed": result.seed, "yt_sha256": result.yt_sha256}
            evidence = payload["evidence"]
            arrays = {
                k: np.asarray(v) for k, v in evidence.items() if isinstance(v, np.ndarray)
            }
            safe = case_id.replace(":", "_").replace("=", "_").replace("/", "_")
            np.savez(out_dir / "evidence" / f"{safe}.npz", **arrays)
            _save_case_figure(out_dir / "figures", case_id, evidence)
        results[section] = section_result

    payloads: dict[str, dict] = {}
    if "r1" in results:
        # The OFFICIAL verdict comes from R1 alone; audits are recorded separately and
        # can never upgrade it (plan truth table).
        payloads["r1_verdict.json"] = results["r1"]
    if "r2" in results:
        payloads["r2_results.json"] = results["r2"]
    if "r3" in results:
        payloads["r3_results.json"] = results["r3"]
    if "audits" in results:
        payloads["audits.json"] = results["audits"]

    _finalize(
        config,
        out_dir,
        subcommand="paper",
        smoke=args.smoke,
        dirty=dirty,
        payloads=payloads,
        case_seed_map=case_seed_map,
        extra={"sections": sections},
    )
    if "r1" in results:
        print(
            f"[m9-controls] R1 verdict: {results['r1']['verdict']} "
            f"(comparator matrix matches: {results['r1']['comparator_matrix_matches']})"
        )
    return 0


def cmd_ablation(args: argparse.Namespace) -> int:
    config = load_experiment_config(args.config)
    dirty = require_clean_tree(smoke=args.smoke)
    out_dir = _run_dir(config, args.smoke)
    result = run_ablation(config)
    _finalize(
        config,
        out_dir,
        subcommand="ablation",
        smoke=args.smoke,
        dirty=dirty,
        payloads={"ablation_results.json": result},
        case_seed_map={
            row["case_id"]: {"seed": row["seed"]}
            for row in result["rows"]
        },
        extra={},
    )
    print(
        f"[m9-controls] ablation complete: all_cases_pass={result['all_cases_pass']} "
        f"(completion, not outcome, gates the transfer bundle)"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="subcommand", required=True)

    paper = sub.add_parser("paper", help="control 1: R1/R2/R3 + audits")
    paper.add_argument("--sections", default=",".join(ALL_SECTIONS))
    paper.add_argument("--smoke", action="store_true")
    paper.add_argument("--config", type=Path, default=None)
    paper.set_defaults(func=cmd_paper)

    ablation = sub.add_parser("ablation", help="control 2: fixed-endpoint 4-RX ablation")
    ablation.add_argument("--smoke", action="store_true")
    ablation.add_argument("--config", type=Path, default=None)
    ablation.set_defaults(func=cmd_ablation)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
