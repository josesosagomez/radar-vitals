#!/usr/bin/env python
"""M8 Step 1b command line (plan section 4.2).

Implements the synthetic control, M3 radar-only stages, and the separate M4 scorer.
All real commands fail closed through the frozen gate and comprehensive authorization.
The scorer additionally verifies the exact complete radar parent before opening Masimo.

    conda run -n radar-vitals python scripts/m8_ahmed_transfer.py synthetic --out <dir>

A gate that passes is not a licence to touch real data. It satisfies the scientific gate
only; base plan section 5.1 still requires `promotion_eligible=true` and a frozen
`real_evaluation` authorization before any capture path is opened.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Callable

import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.m4.bundle import (  # noqa: E402
    BundleWriter,
    new_run_id,
    publish_latest,
    read_manifest,
    sha256_bytes,
    strict_json_bytes,
)
from src.m4.capture_registry import load_registry  # noqa: E402
from src.m4.estimator_runner import (  # noqa: E402
    run_radar_stage,
    verify_gate_bundle,
    verify_repository_authorization,
)
from src.m4.production_suite import ProductionEstimatorSuite  # noqa: E402
from src.m4.estimator_scoring import (  # noqa: E402
    CLAIM_STATUS,
    EVALUATION_STATUS,
    TIME_ORIGIN_ID,
    run_score_stage,
)
from src.m8.ahmed_gate import evaluate_gate  # noqa: E402
from src.m8.ahmed_provenance import (  # noqa: E402
    SourceManifest,
    build_source_manifest,
    build_test_attestation,
    conda_explicit,
    environment_attestation,
    load_source_manifest,
    verify_source_manifest,
    validate_test_attestation,
)
from src.m8.ahmed_synthetic import MODEL_ID, SyntheticConfig, generate  # noqa: E402
from src.m8.ahmed_transfer import (  # noqa: E402
    APPROVED_ARM_IDS,
    APPROVED_HARMONIC_COUNTS,
    APPROVED_LAYER_A_PROFILE_IDS,
    AhmedPhaseConfig,
    AhmedPhaseEstimatorSuite,
    FREQUENCY_MAPPING_ID,
    LAYER_B_NORMALIZATION,
    PHASE_EXTRACTION_METHOD,
    RATE_MAPPING_ID,
    SCORE_FORMULA_ID,
    SCORE_FUNCTIONAL,
    SUPPORT_RULE_ID,
    REAL_REPRESENTATIVE_DOMAIN,
)

DEFAULT_OUT = REPO_ROOT / "results" / "m8_ahmed_transfer" / "synthetic"
DEFAULT_SMOKE_OUT = REPO_ROOT / "results" / "m8_ahmed_transfer" / "smoke"
DEFAULT_RADAR_OUT = REPO_ROOT / "results" / "m8_ahmed_transfer" / "radar"
DEFAULT_SCORE_OUT = REPO_ROOT / "results" / "m8_ahmed_transfer" / "scored"


def _authority_payload(source_manifest) -> dict:
    """Exact M2 authority/profile identity, without touching capture or reference data."""
    profile_path = REPO_ROOT / "experiments" / "m8_ahmed_transfer" / "layer_b_profiles.yaml"
    document = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    configured_arm_ids = tuple(row["arm_id"] for row in document["ahmed_arms"])
    if configured_arm_ids != APPROVED_ARM_IDS:
        raise ValueError(
            f"Layer B profile register arms {configured_arm_ids} do not match code "
            f"authority {APPROVED_ARM_IDS}"
        )
    configured_rows = tuple(
        (
            row.get("arm_id"),
            row.get("layer_a_profile_id"),
            row.get("harmonics"),
            row.get("suppression"),
            row.get("profile_dependency"),
        )
        for row in document["ahmed_arms"]
    )
    expected_rows = tuple(
        (
            arm_id,
            layer_a_id,
            harmonics,
            profile,
            "dependent_duplicate_by_disjoint_domains"
            if profile == "prose_low_or_equal_suppressed"
            else "distinct_profile_identity",
        )
        for arm_id, layer_a_id, (harmonics, profile) in zip(
            APPROVED_ARM_IDS,
            APPROVED_LAYER_A_PROFILE_IDS,
            (
                (harmonics, profile)
                for harmonics in APPROVED_HARMONIC_COUNTS
                for profile in (
                    "figure_visible_unsuppressed",
                    "eq26_multiples_suppressed",
                    "prose_low_or_equal_suppressed",
                )
            ),
        )
    )
    if configured_rows != expected_rows:
        raise ValueError("Layer B profile rows do not match the six approved identities")
    signal = document.get("signal", {})
    expected_signal = {
        "representation": "unwrapped_phase",
        "extraction": PHASE_EXTRACTION_METHOD,
        "candidate_frequency": "q = f",
        "physiological_rate": "bpm = 60q",
        "spectrum": "rFFT magnitude",
        "score": "sum_h |S(hq)| / H",
        "score_functional": SCORE_FUNCTIONAL,
        "score_formula_id": SCORE_FORMULA_ID,
        "normalization": LAYER_B_NORMALIZATION,
        "support": "Hq < f_Nyquist",
    }
    if signal != expected_signal:
        raise ValueError("Layer B profile register signal identity does not match code authority")
    entries = {entry["path"]: entry for entry in source_manifest.entries}
    authority_paths = (
        "plans/m8_ahmed_correction_plan.md",
        "plans/m8_step1a_ahmed_reproduction.md",
        "plans/m8_step1b_ahmed_transfer.md",
        "plans/m8_step1b_ahmed_transfer_addendum_a.md",
        "experiments/m8_ahmed_fig8/layer_a_profiles.yaml",
        "experiments/m8_ahmed_transfer/layer_b_profiles.yaml",
        "experiments/m8_ahmed_transfer/capture_registry.yaml",
    )
    missing = [path for path in authority_paths if path not in entries]
    if missing:
        raise ValueError(f"source manifest omitted authority inputs: {missing}")
    return {
        "schema_version": 1,
        "authority_status": "m2_preparation_only_no_real_data_authorization",
        "authority_inputs": {
            path: {"sha256": entries[path]["sha256"], "source_commit": entries[path]["source_commit"]}
            for path in authority_paths
        },
        "lock_ids": ["recorded_lock_as_captured", "current_production_rerun_lock"],
        "production_arm_id": "production_eca_ahet_v1",
        "ahmed_arm_ids": list(APPROVED_ARM_IDS),
        "harmonic_counts": list(APPROVED_HARMONIC_COUNTS),
        "frequency_mapping_id": FREQUENCY_MAPPING_ID,
        "rate_mapping_id": RATE_MAPPING_ID,
        "normalization": LAYER_B_NORMALIZATION,
        "phase_extraction_method": PHASE_EXTRACTION_METHOD,
        "score_functional": SCORE_FUNCTIONAL,
        "score_formula_id": SCORE_FORMULA_ID,
        "support_rule": SUPPORT_RULE_ID,
        "real_commands_enabled": False,
    }


def _gate_payload(report, authority: dict) -> dict:
    return {
        "schema_version": 1,
        "model_id": report.model_id,
        "synthetic_config_hash": report.synthetic_config_hash,
        "gate_status": report.gate_status,
        # "plan_declared", not "predeclared": the criterion is the one written in the plan
        # cited below and applied identically to every arm — a transparency claim, never a
        # claim about when it was written relative to any result (CLAUDE.md section 4).
        "gate_criterion": "reproduction_of_plan_declared_predictions_p1_p4",
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
        "authority": authority,
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


def run_synthetic(
    out_root: Path,
    *,
    publish: bool = True,
    test_attestation_builder: Callable[[SourceManifest], dict] | None = None,
) -> int:
    """Build the official synthetic bundle from one verified scientific source state.

    ``test_attestation_builder`` is a narrow nonrecursive testing seam.  Production calls
    leave it unset and therefore execute the complete attested pytest set exactly once.
    """
    source = build_source_manifest()
    # Rebuild immediately and compare the exact closure.  Later real-stage prerequisite
    # checks use the same helper with require_promotion_eligible=True.
    verify_source_manifest(source)
    attestation_builder = test_attestation_builder or build_test_attestation
    test_attestation = attestation_builder(source)
    validate_test_attestation(test_attestation, source)
    test_attestation_sha256 = sha256_bytes(strict_json_bytes(test_attestation))
    # An authoritative bundle must contain a real, recreatable environment lock.  Capture
    # and validate it before constructing any bundle payload so failure leaves no result.
    explicit_environment_lock = conda_explicit()
    conda_explicit_sha256 = sha256_bytes(explicit_environment_lock.encode("utf-8"))
    environment_document = environment_attestation()
    environment_document["conda_explicit_sha256"] = conda_explicit_sha256
    environment_attestation_sha256 = sha256_bytes(
        strict_json_bytes(environment_document)
    )
    config = SyntheticConfig()
    realization = generate(config)
    report = evaluate_gate(config)
    authority = _authority_payload(source)

    run_id = new_run_id(report.synthetic_config_hash)
    writer = BundleWriter(stage_root=out_root, stage="synthetic", run_id=run_id)
    writer.add_json("gate.json", _gate_payload(report, authority))
    writer.add_json("metrics.json", _metrics_payload(report, realization))
    writer.add_json("source_manifest.json", source.to_dict())
    writer.add_json("test_attestation.json", test_attestation)
    writer.add_json("environment_attestation.json", environment_document)
    writer.add_text("conda_explicit.txt", explicit_environment_lock)
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
            "test_attestation_sha256": test_attestation_sha256,
            "conda_explicit_sha256": conda_explicit_sha256,
            "environment_attestation_sha256": environment_attestation_sha256,
            "git_commit": source.git_commit,
            "git_branch": source.git_branch,
            "scoped_dirty": list(source.scoped_dirty),
            "scoped_untracked": list(source.scoped_untracked),
        },
        extra_manifest={
            "gate_status": report.gate_status,
            "test_attestation_sha256": test_attestation_sha256,
        },
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


def _latest_bundle(stage_root: Path, expected_stage: str) -> Path:
    pointer = stage_root / "LATEST.json"
    if not pointer.is_file():
        raise ValueError(f"no {expected_stage} LATEST pointer at {pointer}")
    import json

    document = json.loads(pointer.read_text(encoding="utf-8"))
    if document.get("stage") != expected_stage or type(document.get("run_id")) is not str:
        raise ValueError(f"malformed {expected_stage} LATEST pointer at {pointer}")
    bundle = stage_root / document["run_id"]
    _manifest, digest = read_manifest(bundle)
    if digest != document.get("manifest_sha256"):
        raise ValueError(f"{expected_stage} LATEST pointer digest mismatch")
    return bundle


def _default_authorization() -> Path:
    root = REPO_ROOT / "experiments" / "m8_ahmed_transfer" / "authorizations"
    paths = sorted(root.glob("*.yaml")) if root.is_dir() else []
    if len(paths) != 1:
        raise ValueError(
            f"expected exactly one frozen real-evaluation authorization in {root}, "
            f"found {len(paths)}; pass --authorization explicitly"
        )
    return paths[0]


def run_real_stage(
    command: str,
    *,
    gate_dir: Path | None,
    authorization_path: Path | None,
    parent_dir: Path | None,
    out_root: Path,
    publish: bool,
) -> int:
    """Run M3 only. This function never imports or opens a Masimo/reference path."""
    gate = gate_dir or _latest_bundle(DEFAULT_OUT, "synthetic")
    # Validate the immutable gate before trusting its persisted source manifest.
    verify_gate_bundle(gate)
    source = load_source_manifest(gate / "source_manifest.json")
    verify_source_manifest(source, require_promotion_eligible=True)
    authorization = authorization_path or _default_authorization()
    parent = parent_dir
    if command == "real-radar" and parent is None:
        parent = _latest_bundle(DEFAULT_SMOKE_OUT, "smoke")

    config = yaml.safe_load(
        (REPO_ROOT / "scripts" / "live_demo_config.yaml").read_text(encoding="utf-8")
    )
    if not isinstance(config, dict):
        raise ValueError("scripts/live_demo_config.yaml must contain a mapping")
    suites = (
        ProductionEstimatorSuite(config),
        AhmedPhaseEstimatorSuite(AhmedPhaseConfig(domain=REAL_REPRESENTATIVE_DOMAIN)),
    )
    registry = load_registry()
    run_id = new_run_id(source.manifest_sha256)
    bundle = run_radar_stage(
        stage=command,
        gate_dir=gate,
        authorization_path=authorization,
        source_manifest_sha256=source.manifest_sha256,
        radar=registry.radar_scope(),
        suites=suites,
        selector_config=config,
        run_id=run_id,
        out_root=out_root,
        parent_dir=parent,
        authorization_validator=verify_repository_authorization,
        require_scientific_gate=True,
    )
    print(f"stage        : {bundle.stage}")
    print(f"run_id       : {bundle.run_id}")
    print(f"manifest     : {bundle.manifest_sha256}")
    print("score_status : disabled_until_m4")
    if publish:
        print(f"LATEST       : {publish_latest(out_root, bundle)}")
    return 0


def run_score(
    *,
    gate_dir: Path | None,
    authorization_path: Path | None,
    radar_dir: Path | None,
    out_root: Path,
    publish: bool,
) -> int:
    """Score one complete same-run radar bundle; no ADC path is available here."""
    gate = gate_dir or _latest_bundle(DEFAULT_OUT, "synthetic")
    verify_gate_bundle(gate)
    source = load_source_manifest(gate / "source_manifest.json")
    verify_source_manifest(source, require_promotion_eligible=True)
    authorization = authorization_path or _default_authorization()
    radar_parent = radar_dir or _latest_bundle(DEFAULT_RADAR_OUT, "radar")
    registry = load_registry()
    run_id = new_run_id(source.manifest_sha256)
    bundle = run_score_stage(
        gate_dir=gate,
        authorization_path=authorization,
        source_manifest_sha256=source.manifest_sha256,
        radar_dir=radar_parent,
        reference=registry.reference_scope(),
        run_id=run_id,
        out_root=out_root,
        authorization_validator=verify_repository_authorization,
        require_scientific_gate=True,
    )
    print(f"stage        : {bundle.stage}")
    print(f"run_id       : {bundle.run_id}")
    print(f"manifest     : {bundle.manifest_sha256}")
    score_manifest, _digest = read_manifest(bundle.root)
    print(f"source       : {score_manifest['source_manifest_sha256']}")
    print(f"authorization: {score_manifest['authorization_sha256']}")
    print(f"radar_parent : {score_manifest['radar_parent_manifest_sha256']}")
    print(f"references   : {score_manifest['reference_identity_sha256']}")
    print(f"evaluation   : {EVALUATION_STATUS}")
    print(f"time_origin  : {TIME_ORIGIN_ID} (approximate=true)")
    print(f"claim_status : {CLAIM_STATUS}")
    print("limitations  : development_apparent_single_subject; no population claim")
    if publish:
        print("LATEST       : not published (reference-derived claim status is ineligible)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    synthetic = sub.add_parser("synthetic", help="run the transfer control and freeze a gate")
    synthetic.add_argument("--out", type=Path, default=DEFAULT_OUT)
    synthetic.add_argument("--no-publish", action="store_true")

    for name, default_out in (
        ("real-smoke", DEFAULT_SMOKE_OUT),
        ("real-radar", DEFAULT_RADAR_OUT),
    ):
        real = sub.add_parser(name, help="authorized M3 radar-only stage")
        real.add_argument("--gate", type=Path)
        real.add_argument("--authorization", type=Path)
        real.add_argument("--parent", type=Path)
        real.add_argument("--out", type=Path, default=default_out)
        real.add_argument("--no-publish", action="store_true")
    score = sub.add_parser("score", help="authorized M4 scoring of one radar parent")
    score.add_argument("--gate", type=Path)
    score.add_argument("--authorization", type=Path)
    score.add_argument("--radar-parent", "--parent", dest="radar_parent", type=Path)
    score.add_argument("--out", type=Path, default=DEFAULT_SCORE_OUT)
    score.add_argument("--no-publish", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "score":
        return run_score(
            gate_dir=args.gate,
            authorization_path=args.authorization,
            radar_dir=args.radar_parent,
            out_root=args.out,
            publish=not args.no_publish,
        )
    if args.command in ("real-smoke", "real-radar"):
        return run_real_stage(
            args.command,
            gate_dir=args.gate,
            authorization_path=args.authorization,
            parent_dir=args.parent,
            out_root=args.out,
            publish=not args.no_publish,
        )
    return run_synthetic(args.out, publish=not args.no_publish)


if __name__ == "__main__":
    raise SystemExit(main())
