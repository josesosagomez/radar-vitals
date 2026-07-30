#!/usr/bin/env python
"""M8 Step 1b command line (plan section 4.2).

Currently implements the `synthetic` command, which runs the transfer control and freezes
an immutable gate bundle. The real-data commands (`real-smoke`, `real-radar`, `score`) are
**not implemented**: they require a frozen, promotion-eligible gate plus a separate
comprehensive authorization, and the plan forbids adding executable code between the gate
and the real stages. They are registered here only so that invoking one fails loudly with
that explanation rather than with an unhelpful "unknown command".

    conda run -n radar-vitals python scripts/m8_ahmed_transfer.py synthetic --out <dir>

A gate that passes is not a licence to touch real data. It satisfies the scientific gate
only; base plan section 5.1 still requires `promotion_eligible=true` and a frozen
`real_evaluation` authorization before any capture path is opened.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.m4.bundle import BundleWriter, new_run_id, publish_latest  # noqa: E402
from src.m8.ahmed_gate import evaluate_gate  # noqa: E402
from src.m8.ahmed_provenance import (  # noqa: E402
    build_source_manifest,
    conda_explicit,
    environment_attestation,
)
from src.m8.ahmed_synthetic import MODEL_ID, SyntheticConfig, generate  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "results" / "m8_ahmed_transfer" / "synthetic"

_REAL_STAGE_REFUSAL = (
    "{command} is not implemented. The real-data chain requires a frozen, "
    "promotion-eligible synthetic gate bundle AND a separate comprehensive "
    "real_evaluation authorization (base plan section 5.1), and the plan forbids adding "
    "executable code between the gate and the real stages. Implement it as its own "
    "reviewed step; do not bolt it on here."
)


def _gate_payload(report) -> dict:
    return {
        "schema_version": 1,
        "model_id": report.model_id,
        "synthetic_config_hash": report.synthetic_config_hash,
        "gate_status": report.gate_status,
        "gate_criterion": "reproduction_of_predeclared_predictions_p1_p4",
        "gate_criterion_source": "plans/m8_step1b_ahmed_transfer_addendum_a.md section A4.2",
        "checks": [
            {"prediction_id": c.prediction_id, "passed": c.passed, "detail": c.detail}
            for c in report.checks
        ],
        "transfer_by_domain": dict(report.transfer_by_domain),
        "transfer_is_non_gating": True,
        "interpretation_note": (
            "complete never implies success; a positive result supports only this "
            "declared seed and configuration and is not evidence of robustness or of "
            "general phase-model transfer"
        ),
    }


def _metrics_payload(report, realization) -> dict:
    return {
        "schema_version": 1,
        "selection_table": [dict(row) for row in report.selection_table],
        "realized_snr_db": realization.realized_snr_db,
        "noise_power": realization.noise_power,
        "max_clean_increment_rad": realization.max_clean_increment_rad,
        "min_branch_margin_rad": realization.min_branch_margin_rad,
        "max_reference_increment_rad": realization.max_reference_increment_rad,
        "n_cycle_slips": realization.n_cycle_slips,
        "max_oracle_residual_rad": realization.max_oracle_residual_rad,
        "snr_note": (
            "pre-extraction SNR; the extracted-phase error is a nonlinear, wrapped "
            "function of this noise and is not 10 dB AWGN"
        ),
    }


def run_synthetic(out_root: Path, *, publish: bool = True) -> int:
    config = SyntheticConfig()
    realization = generate(config)
    report = evaluate_gate(config)
    source = build_source_manifest()

    run_id = new_run_id(report.synthetic_config_hash)
    writer = BundleWriter(stage_root=out_root, stage="synthetic", run_id=run_id)
    writer.add_json("gate.json", _gate_payload(report))
    writer.add_json("metrics.json", _metrics_payload(report, realization))
    writer.add_json("source_manifest.json", source.to_dict())
    writer.add_json("environment_attestation.json", environment_attestation())
    writer.add_text("conda_explicit.txt", conda_explicit())
    # Real YAML, as the plan's bundle layout specifies. sort_keys keeps it deterministic.
    writer.add_text(
        "resolved_config.yaml",
        yaml.safe_dump(config.to_dict(), sort_keys=True, default_flow_style=False),
    )
    writer.add_npz(
        "evidence.npz",
        {
            "clean_phase": np.asarray(realization.clean_phase),
            "extracted_phase": np.asarray(realization.extracted_phase),
            "reference_phase": np.asarray(realization.reference_phase),
            "cycle_offsets": np.asarray(realization.cycle_offsets),
        },
    )

    bundle = writer.finalize(
        status="complete" if report.gate_status == "passed" else "failed",
        promotion_eligible=source.promotion_eligible,
        provenance={
            "schema_version": 1,
            "model_id": MODEL_ID,
            "synthetic_config_hash": realization.config_hash,
            "draw_hash": realization.draw_hash,
            "clean_phasor_hash": realization.clean_phasor_hash,
            "cube_hash": realization.cube_hash,
            "source_manifest_sha256": source.manifest_sha256,
            "git_commit": source.git_commit,
            "git_branch": source.git_branch,
            "scoped_dirty": list(source.scoped_dirty),
            "scoped_untracked": list(source.scoped_untracked),
        },
        extra_manifest={"gate_status": report.gate_status},
    )

    print(f"stage        : synthetic")
    print(f"run_id       : {bundle.run_id}")
    print(f"gate_status  : {report.gate_status}")
    print(f"manifest     : {bundle.manifest_sha256}")
    print(f"promotion    : {'eligible' if bundle.promotion_eligible else 'INELIGIBLE'}")
    for domain, verdict in sorted(report.transfer_by_domain.items()):
        print(f"transfer     : {domain} -> {verdict}   (non-gating)")
    if not bundle.promotion_eligible:
        print(
            "NOTE: scoped sources are dirty or untracked, so this bundle is draft "
            "evidence and cannot parent a real-data stage."
        )
        for path in list(source.scoped_dirty) + list(source.scoped_untracked):
            print(f"       - {path}")
    if publish and bundle.status == "complete" and bundle.promotion_eligible:
        print(f"LATEST       : {publish_latest(out_root, bundle)}")
    return 0 if report.gate_status == "passed" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    synthetic = sub.add_parser("synthetic", help="run the transfer control and freeze a gate")
    synthetic.add_argument("--out", type=Path, default=DEFAULT_OUT)
    synthetic.add_argument("--no-publish", action="store_true")

    for name in ("real-smoke", "real-radar", "score"):
        sub.add_parser(name, help="not implemented; see base plan section 5.1")

    args = parser.parse_args(argv)
    if args.command in ("real-smoke", "real-radar", "score"):
        raise SystemExit(_REAL_STAGE_REFUSAL.format(command=args.command))
    return run_synthetic(args.out, publish=not args.no_publish)


if __name__ == "__main__":
    raise SystemExit(main())
