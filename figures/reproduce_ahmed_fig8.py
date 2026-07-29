"""Generate M8 Step 1a Ahmed Fig. 8(c)-(d) reproduction evidence."""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import traceback
from typing import Any, Iterable, Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import yaml  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.m8.ahmed_fig8 import (  # noqa: E402
    SUPPRESSION_PROFILES,
    AhmedConfig,
    AhmedSignal,
    HAScoreResult,
    amplitude_ratio_variant,
    build_adapter_result,
    config_hash,
    derive_variant_seed,
    run_profile,
    simulate_eq14,
)


DEFAULT_CONFIG = REPO_ROOT / "experiments" / "m8_ahmed_fig8" / "config.yaml"
DEFAULT_OUT = REPO_ROOT / "results" / "m8_ahmed_fig8"
DEFAULT_CANONICAL = REPO_ROOT / "figures" / "generated" / "m8_ahmed_fig8"
PLAN_PATH = REPO_ROOT / "plans" / "m8_step1a_ahmed_reproduction.md"
IMPLEMENTATION_PATH = REPO_ROOT / "src" / "m8" / "ahmed_fig8.py"
TEST_PATH = REPO_ROOT / "tests" / "test_m8_ahmed_fig8.py"
PAPER_PATH = (
    REPO_ROOT
    / "literature"
    / "ref_papers"
    / "discovering_the_unseen_radar_vitals"
    / "Discovering_the_Unseen_Radar-Based_Estimation_of_Heartbeat_Breathing_Rate_and_Underlying_Muscle_Expansion_Without_Probes.pdf"
)

_TOP_KEYS = {
    "schema_version",
    "experiment_id",
    "citation",
    "primary",
    "harmonics",
    "suppression_profiles",
    "audit",
}
_AUDIT_KEYS = {
    "sample_count_rule",
    "theta0_rad",
    "amplitude_ratio_h_to_b",
    "snr_power_mode",
    "n_fft",
    "normalization",
}
_CONFIG_KEYS = set(AhmedConfig.__dataclass_fields__)
_CITATION_KEYS = {"title", "authors", "doi", "url", "figure"}
_FIXED_AUDIT = {
    "sample_count_rule": ["floor", "ceil"],
    "theta0_rad": [math.pi / 4.0, math.pi / 2.0],
    "amplitude_ratio_h_to_b": [0.5, 2.0],
    "snr_power_mode": ["centered"],
    "n_fft": [2048, 8192],
    "normalization": ["matrix_eta"],
}
_EXPERIMENT_ID = "m8_ahmed_fig8_behavioral_reproduction_v1"
_APPROVED_CITATION = {
    "title": (
        "Discovering the Unseen: Radar-Based Estimation of Heartbeat, Breathing Rate, "
        "and Underlying Muscle Expansion Without Probes"
    ),
    "authors": "Ahmed et al.",
    "doi": "10.1109/TRS.2024.3412915",
    "url": "https://repository.kaust.edu.sa/handle/10754/698235",
    "figure": "8(c)-(d)",
}


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_builtin(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_builtin(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_builtin(item) for item in value]
    return value


def _write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(_json_builtin(dict(data)), allow_nan=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_yaml(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        yaml.safe_dump(_json_builtin(dict(data)), sort_keys=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def _require_exact_keys(where: str, mapping: Mapping[str, Any], expected: set[str]) -> None:
    unknown = sorted(set(mapping) - expected)
    missing = sorted(expected - set(mapping))
    if unknown or missing:
        details = []
        if unknown:
            details.append(f"unknown keys={unknown}")
        if missing:
            details.append(f"missing keys={missing}")
        raise ValueError(f"{where}: {'; '.join(details)}")


def load_experiment_config(path: Path) -> dict[str, Any]:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if type(document) is not dict:
        raise TypeError(f"{path}: root must be a mapping")
    _require_exact_keys("config root", document, _TOP_KEYS)
    if document["schema_version"] != 1:
        raise ValueError(
            f"schema_version must be 1, got {document['schema_version']!r}"
        )
    if type(document["citation"]) is not dict:
        raise TypeError("citation must be a mapping")
    _require_exact_keys("citation", document["citation"], _CITATION_KEYS)
    primary = document["primary"]
    if type(primary) is not dict:
        raise TypeError("primary must be a mapping")
    _require_exact_keys("primary", primary, _CONFIG_KEYS)
    AhmedConfig(**primary)
    harmonics = document["harmonics"]
    if harmonics != [3, 5] or any(type(value) is not int for value in harmonics):
        raise ValueError("schema version 1 requires harmonics exactly [3, 5]")
    profiles = document["suppression_profiles"]
    if type(profiles) is not list or tuple(profiles) != SUPPRESSION_PROFILES:
        raise ValueError(
            f"suppression_profiles must be exactly {list(SUPPRESSION_PROFILES)!r}"
        )
    audit = document["audit"]
    if type(audit) is not dict:
        raise TypeError("audit must be a mapping")
    _require_exact_keys("audit", audit, _AUDIT_KEYS)
    for key, expected_values in _FIXED_AUDIT.items():
        if audit[key] != expected_values:
            raise ValueError(
                f"schema version 1 requires audit.{key} exactly {expected_values!r}, "
                f"got {audit[key]!r}"
            )
    return deepcopy(document)


def _canonical_contract_matches(
    config_path: Path, document: Mapping[str, Any]
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if config_path.resolve() != DEFAULT_CONFIG.resolve():
        reasons.append("config_path_is_not_default")
    if document.get("experiment_id") != _EXPERIMENT_ID:
        reasons.append("experiment_id_mismatch")
    if document.get("citation") != _APPROVED_CITATION:
        reasons.append("citation_mismatch")
    if document.get("primary") != AhmedConfig().to_dict():
        reasons.append("primary_config_differs_from_approved_contract")
    if document.get("harmonics") != [3, 5]:
        reasons.append("harmonics_differ_from_approved_contract")
    if document.get("suppression_profiles") != list(SUPPRESSION_PROFILES):
        reasons.append("suppression_profiles_differ_from_approved_contract")
    if document.get("audit") != _FIXED_AUDIT:
        reasons.append("audit_differs_from_approved_contract")
    return not reasons, reasons


def _variant_configs(
    primary: AhmedConfig, audit: Mapping[str, list[Any]]
) -> list[tuple[str, AhmedConfig, str]]:
    variants: list[tuple[str, AhmedConfig, str]] = []
    for rule in audit["sample_count_rule"]:
        variants.append((f"sample_count_rule={rule}", replace(primary, sample_count_rule=rule), "sample_count_rule"))
    for theta in audit["theta0_rad"]:
        variants.append((f"theta0_rad={theta:.12g}", replace(primary, theta0_rad=theta), "theta0_rad"))
    for ratio in audit["amplitude_ratio_h_to_b"]:
        variants.append(
            (
                f"amplitude_ratio_h_to_b={ratio:.12g}",
                amplitude_ratio_variant(primary, float(ratio)),
                "amplitude_ratio_h_to_b",
            )
        )
    for mode in audit["snr_power_mode"]:
        variants.append((f"snr_power_mode={mode}", replace(primary, snr_power_mode=mode), "snr_power_mode"))
    for n_fft in audit["n_fft"]:
        variants.append((f"n_fft={n_fft}", replace(primary, n_fft=n_fft), "n_fft"))
    for normalization in audit["normalization"]:
        variants.append(
            (
                f"normalization={normalization}",
                replace(primary, normalization=normalization),
                "normalization",
            )
        )
    return variants


def _signal_arrays(signal: AhmedSignal) -> dict[str, np.ndarray]:
    return {
        "signal_config_hash": np.asarray(signal.config_hash),
        "signal_hash": np.asarray(signal.signal_hash),
        "effective_seed": np.asarray(
            -1 if signal.effective_seed is None else signal.effective_seed,
            dtype=np.int64,
        ),
        "t_s": signal.t_s,
        "clean_heart": signal.clean_heart,
        "clean_breath": signal.clean_breath,
        "clean_combined": signal.clean_combined,
        "standard_normal": signal.standard_normal,
        "noise": signal.noise,
        "samples": signal.samples,
    }


def _result_arrays(prefix: str, result: HAScoreResult) -> dict[str, np.ndarray]:
    return {
        f"{prefix}__config_hash": np.asarray(result.config_hash),
        f"{prefix}__signal_hash": np.asarray(result.signal_hash),
        f"{prefix}__effective_seed": np.asarray(
            -1 if result.effective_seed is None else result.effective_seed,
            dtype=np.int64,
        ),
        f"{prefix}__frequencies_hz": result.frequencies_hz,
        f"{prefix}__candidate_bins": result.candidate_bins,
        f"{prefix}__harmonic_bins": result.harmonic_bins,
        f"{prefix}__spectrum_frequencies_hz": result.spectrum_frequencies_hz,
        f"{prefix}__spectrum_magnitude": result.spectrum_magnitude,
        f"{prefix}__scores_before_exclusion": result.scores_before_exclusion,
        f"{prefix}__scores": result.scores,
        f"{prefix}__eligible": result.eligible,
    }


def _save_evidence(
    path: Path,
    signal: AhmedSignal,
    results: Mapping[int, tuple[HAScoreResult, HAScoreResult]],
) -> None:
    arrays = _signal_arrays(signal)
    for harmonics, (breath, heart) in sorted(results.items()):
        arrays.update(_result_arrays(f"h{harmonics}_breath", breath))
        arrays.update(_result_arrays(f"h{harmonics}_heart", heart))
    if any(np.asarray(value).dtype == object for value in arrays.values()):
        raise TypeError("object dtype is forbidden in evidence NPZ")
    np.savez_compressed(path, **arrays)


def _result_metric(result: HAScoreResult, config: AhmedConfig) -> dict[str, Any]:
    target_position = np.flatnonzero(result.candidate_bins == result.expected_bin)
    target_score = (
        None
        if target_position.size == 0
        else float(result.scores_before_exclusion[int(target_position[0])])
    )
    return {
        "vital": result.vital,
        "harmonics": result.harmonics,
        "suppression_profile": result.suppression_profile,
        "normalization": result.normalization,
        "status": result.status,
        "valid": bool(result.valid),
        "rejection_reason": result.rejection_reason,
        "expected_frequency_hz": result.expected_frequency_hz,
        "expected_bin": result.expected_bin,
        "expected_bpm": 30.0 * result.expected_frequency_hz,
        "target_score_before_exclusion": target_score,
        "selected_frequency_hz": result.selected_frequency_hz,
        "selected_bin": result.selected_bin,
        "selected_bpm": result.selected_bpm,
        "selected_score": result.selected_score,
        "spectral_error_hz": result.spectral_error_hz,
        "rate_error_bpm": result.rate_error_bpm,
        "native_resolution_hz": config.native_resolution_hz,
        "native_rate_resolution_bpm": 30.0 * config.native_resolution_hz,
        "within_native_resolution": (
            False
            if result.spectral_error_hz is None
            else bool(result.spectral_error_hz <= config.native_resolution_hz)
        ),
        "unique_maximum": bool(result.unique_maximum),
        "runner_up_bin": result.runner_up_bin,
        "runner_up_score": result.runner_up_score,
        "score_margin": result.score_margin,
        "valid_candidate_count": int(np.sum(result.eligible)),
        "excluded_candidate_count": int(np.sum(~result.eligible)),
    }


def _profile_metrics(
    config: AhmedConfig,
    results: Mapping[int, tuple[HAScoreResult, HAScoreResult]],
) -> dict[str, Any]:
    curves: dict[str, Any] = {}
    for harmonics, (breath, heart) in sorted(results.items()):
        curves[f"h{harmonics}_breath"] = _result_metric(breath, config)
        curves[f"h{harmonics}_heart"] = _result_metric(heart, config)
    return {"curves": curves}


def _acceptance(
    config: AhmedConfig,
    results: Mapping[int, tuple[HAScoreResult, HAScoreResult]],
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    for harmonics, (breath, heart) in sorted(results.items()):
        for result in (breath, heart):
            key = f"h{harmonics}_{result.vital}"
            checks[f"{key}_valid"] = bool(result.valid)
            checks[f"{key}_unique_maximum"] = bool(result.unique_maximum)
            checks[f"{key}_within_native_resolution"] = bool(
                result.spectral_error_hz is not None
                and result.spectral_error_hz <= config.native_resolution_hz
            )
            checks[f"{key}_positive_margin"] = bool(
                result.score_margin is not None and result.score_margin > 0.0
            )
    if 3 in results and 5 in results:
        for index, vital in enumerate(("breath", "heart")):
            h3 = results[3][index]
            h5 = results[5][index]
            h3_target = h3.scores_before_exclusion[
                np.flatnonzero(h3.candidate_bins == h3.expected_bin)[0]
            ]
            h5_target = h5.scores_before_exclusion[
                np.flatnonzero(h5.candidate_bins == h5.expected_bin)[0]
            ]
            checks[f"{vital}_h3_target_exceeds_h5"] = bool(h3_target > h5_target)
    passed = bool(checks and all(checks.values()))
    return {
        "claim": "behavioral reproduction under declared assumptions",
        "status": (
            "behaviorally_reproduced"
            if passed
            else "not_reproduced_under_declared_assumptions"
        ),
        "passed": passed,
        "checks": checks,
    }


def _render_figure(
    path_png: Path,
    path_pdf: Path,
    config: AhmedConfig,
    results: Mapping[int, tuple[HAScoreResult, HAScoreResult]],
) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.6), constrained_layout=True)
    colors = {3: "#0067B1", 5: "#D55E00"}
    markers = {3: None, 5: "+"}
    for axis, vital, result_index, panel in (
        (axes[0], "Breathing", 0, "(c)"),
        (axes[1], "Heartbeat", 1, "(d)"),
    ):
        for harmonics in sorted(results):
            result = results[harmonics][result_index]
            axis.plot(
                result.frequencies_hz,
                result.scores,
                color=colors.get(harmonics),
                linewidth=1.15,
                marker=markers.get(harmonics),
                markersize=3.2,
                markevery=8 if harmonics == 5 else None,
                label=rf"$N_h={harmonics}$",
            )
        target = (
            2.0 * config.breath_hz if result_index == 0 else 2.0 * config.heart_hz
        )
        axis.axvline(target, color="#333333", linestyle=":", linewidth=1.0)
        axis.text(
            target + 0.01 * (axis.get_xlim()[1] - axis.get_xlim()[0]),
            0.04,
            rf"target $2f_{{{'b' if result_index == 0 else 'h'}}}$",
            transform=axis.get_xaxis_transform(),
            rotation=90,
            ha="left",
            va="bottom",
            fontsize=8,
        )
        axis.set_xlabel("Spectral frequency $q$ (Hz)")
        axis.set_ylabel("Harmonics accumulation mean")
        axis.set_title(f"{panel} {vital} estimation")
        axis.grid(True, alpha=0.25, linewidth=0.7)
        axis.legend(loc="upper right")
    axes[0].set_xlim(0.0, 2.0 * config.breath_max_bpm / 60.0)
    axes[1].set_xlim(2.0 * config.breath_hz, 2.0 * config.heart_max_bpm / 60.0)
    fig.suptitle(
        "Ahmed et al. Fig. 8(c)-(d) behavioral reproduction\n"
        "single TX/RX equation-(14) model, 10 dB SNR, five breaths",
        fontsize=11,
    )
    fig.savefig(path_png, dpi=220, bbox_inches="tight")
    fig.savefig(
        path_pdf,
        bbox_inches="tight",
        metadata={
            "Title": "Ahmed Fig. 8(c)-(d) behavioral reproduction",
            "Author": "radar-vitals reproducible simulation",
            "Subject": "M8 Step 1a",
        },
    )
    plt.close(fig)


def _git_text(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        return f"<unavailable: {type(exc).__name__}: {exc}>"


def _package_version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "<not-installed>"


def _provenance(
    run_id: str,
    config_path: Path,
    resolved_path: Path,
    document: Mapping[str, Any],
    output_hashes: Mapping[str, str],
) -> dict[str, Any]:
    required = [
        PLAN_PATH,
        IMPLEMENTATION_PATH,
        Path(__file__).resolve(),
        TEST_PATH,
        config_path,
    ]
    tracked = set(_git_text("ls-files").splitlines())

    def tracking_key(path: Path) -> tuple[str, bool]:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            return str(resolved), False
        return relative, relative in tracked

    tracking = dict(tracking_key(path) for path in required)
    status_porcelain = _git_text("status", "--porcelain")
    return {
        "experiment_id": document["experiment_id"],
        "run_id": run_id,
        "claim": "behavioral reproduction under declared assumptions",
        "citation": document["citation"],
        "plan_review": {
            "reviewed_source": "conversation plan later saved at the hashed plan path",
            "roles": {
                "architecture": "reject_then_addressed_in_revised_plan",
                "correctness_and_edge_cases": "reject_then_addressed_in_revised_plan",
                "python_implementation": "reject_then_addressed_in_revised_plan",
                "testing_and_validation": "reject_then_addressed_in_revised_plan",
                "adversarial": "reject_then_addressed_in_revised_plan",
            },
            "reviewed_commit": None,
            "reviewed_commit_reason": (
                "the approved plan was conversational and had not yet been saved at a commit"
            ),
        },
        "git": {
            "commit": _git_text("rev-parse", "HEAD"),
            "branch": _git_text("branch", "--show-current"),
            "status_porcelain": status_porcelain,
            "required_files_tracked": tracking,
            "clean_and_required_tracked": bool(
                not status_porcelain and all(tracking.values())
            ),
        },
        "hashes": {
            "source_config_sha256": _sha256(config_path),
            "resolved_config_sha256": _sha256(resolved_path),
            "plan_sha256": _sha256(PLAN_PATH),
            "implementation_module_sha256": _sha256(IMPLEMENTATION_PATH),
            "runner_script_sha256": _sha256(Path(__file__).resolve()),
            "test_file_sha256": _sha256(TEST_PATH),
            "paper_pdf_sha256": _sha256(PAPER_PATH),
            **dict(output_hashes),
        },
        "source_paths": {
            "config": str(config_path.resolve()),
            "plan": str(PLAN_PATH.resolve()),
            "paper_pdf": str(PAPER_PATH.resolve()),
        },
        "runtime": {
            "python": sys.version,
            "numpy": np.__version__,
            "scipy": _package_version("scipy"),
            "matplotlib": matplotlib.__version__,
            "pyyaml": _package_version("PyYAML"),
            "pytest": _package_version("pytest"),
            "os": platform.platform(),
            "backend": matplotlib.get_backend(),
        },
        "command": [str(item) for item in sys.argv],
        "seed_policy": {
            "primary_seed": int(document["primary"]["seed"]),
            "same_length": "reuse primary standard-normal realization",
            "different_length": "SHA256-derived seed from base seed and variant ID",
        },
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def _unique_run_dir(root: Path, short_hash: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    stem = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + f"_{short_hash}"
    suffix = 0
    while True:
        candidate = root / f"{stem}_{suffix}"
        if suffix == 0:
            candidate = root / stem
        try:
            candidate.mkdir(exist_ok=False)
            return candidate
        except FileExistsError:
            suffix += 1


def _promote_canonical(
    canonical_root: Path,
    run_id: str,
    files: Mapping[Path, str],
    hashes: Mapping[str, str],
) -> Path:
    """Publish one complete versioned bundle, then atomically update LATEST.json."""
    canonical_root.mkdir(parents=True, exist_ok=True)
    staging = canonical_root / f".staging-{run_id}"
    destination = canonical_root / run_id
    if staging.exists() or destination.exists():
        raise FileExistsError(f"canonical bundle already exists for run_id={run_id}")
    staging.mkdir()
    try:
        for source, name in files.items():
            shutil.copy2(source, staging / name)
        bundle = {
            "schema_version": 1,
            "run_id": run_id,
            "files": {
                name: _sha256(staging / name) for name in sorted(files.values())
            },
            "source_output_hashes": dict(hashes),
        }
        _write_json(staging / "bundle.json", bundle)
        staging.replace(destination)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    latest = canonical_root / "LATEST.json"
    _write_json(
        latest,
        {
            "schema_version": 1,
            "run_id": run_id,
            "bundle": run_id,
            "bundle_manifest_sha256": _sha256(destination / "bundle.json"),
        },
    )
    return destination


def _run_config(
    config: AhmedConfig,
    harmonics: Iterable[int],
    profiles: Iterable[str],
    standard_normal: np.ndarray | None,
    *,
    seed: int,
) -> tuple[
    AhmedSignal,
    dict[str, dict[int, tuple[HAScoreResult, HAScoreResult]]],
]:
    signal = simulate_eq14(
        config,
        np.random.default_rng(seed),
        standard_normal=standard_normal,
        effective_seed=seed,
    )
    results = {
        profile: {
            int(h): run_profile(signal, config, int(h), profile) for h in harmonics
        }
        for profile in profiles
    }
    return signal, results


def execute(
    config_path: Path,
    output_root: Path,
    canonical_root: Path,
) -> Path:
    document = load_experiment_config(config_path)
    primary = AhmedConfig(**document["primary"])
    resolved = deepcopy(document)
    resolved["derived"] = {
        "wavelength_m": primary.wavelength_m,
        "heart_phase_index": primary.heart_phase_index,
        "breath_phase_index": primary.breath_phase_index,
        "heart_modulation_bandwidth_hz": primary.heart_modulation_bandwidth_hz,
        "prf_hz": primary.prf_hz,
        "sample_count_float": primary.sample_count_float,
        "sample_count": primary.sample_count,
        "requested_duration_s": primary.requested_duration_s,
        "effective_duration_s": primary.effective_duration_s,
        "last_sample_time_s": primary.last_sample_time_s,
        "native_resolution_hz": primary.native_resolution_hz,
    }
    resolved_hash = config_hash(resolved)
    run_dir = _unique_run_dir(output_root, resolved_hash[:12])
    run_id = run_dir.name
    status_path = run_dir / "run_status.json"
    started = datetime.now(timezone.utc).isoformat()
    _write_json(
        status_path,
        {
            "run_id": run_id,
            "status": "running",
            "started_at_utc": started,
            "stage": "initializing",
        },
    )
    stage = "initializing"
    try:
        stage = "resolved_config"
        resolved_path = run_dir / "resolved_config.yaml"
        _write_yaml(resolved_path, resolved)
        stage = "primary_simulation"
        base_rng = np.random.default_rng(primary.seed)
        base_z = base_rng.standard_normal(primary.sample_count)
        primary_signal, primary_profiles = _run_config(
            primary,
            document["harmonics"],
            document["suppression_profiles"],
            base_z,
            seed=primary.seed,
        )
        evidence_paths: dict[str, Path] = {}
        profile_metrics: dict[str, Any] = {}
        for profile, results in primary_profiles.items():
            path = run_dir / f"primary__{profile}.npz"
            _save_evidence(path, primary_signal, results)
            evidence_paths[f"evidence_primary_{profile}_sha256"] = path
            profile_metrics[profile] = _profile_metrics(primary, results)

        stage = "ambiguity_audit"
        audit_metrics: dict[str, Any] = {}
        for variant_id, variant, changed_field in _variant_configs(primary, document["audit"]):
            if variant.sample_count == primary.sample_count:
                z = base_z
                seed = primary.seed
                noise_policy = "reused_primary_standard_normal"
            else:
                seed = derive_variant_seed(primary.seed, variant_id)
                z = None
                noise_policy = "derived_seed"
            signal, profile_result = _run_config(
                variant,
                document["harmonics"],
                ("figure_visible_unsuppressed",),
                z,
                seed=seed,
            )
            safe_id = (
                variant_id.replace("=", "-")
                .replace(".", "p")
                .replace("/", "_")
            )
            path = run_dir / f"audit__{safe_id}.npz"
            results = profile_result["figure_visible_unsuppressed"]
            _save_evidence(path, signal, results)
            evidence_paths[f"evidence_audit_{safe_id}_sha256"] = path
            audit_metrics[variant_id] = {
                "changed_field": changed_field,
                "seed": seed,
                "noise_policy": noise_policy,
                "config": variant.to_dict(),
                **_profile_metrics(variant, results),
            }

        stage = "metrics"
        figure_results = primary_profiles["figure_visible_unsuppressed"]
        adapter_native = build_adapter_result(
            figure_results[3][0],
            figure_results[3][1],
            {
                "config_hash": config_hash(primary),
                "experiment_config_hash": resolved_hash,
            },
        )
        metrics = {
            "schema_version": 1,
            "run_id": run_id,
            "experiment_id": document["experiment_id"],
            "experiment_config_hash": resolved_hash,
            "primary_model_config_hash": config_hash(primary),
            "signal": {
                "config_hash": primary_signal.config_hash,
                "signal_hash": primary_signal.signal_hash,
                "effective_seed": primary_signal.effective_seed,
                "requested_duration_s": primary_signal.requested_duration_s,
                "effective_duration_s": primary_signal.effective_duration_s,
                "last_sample_time_s": primary_signal.last_sample_time_s,
                "prf_hz": primary_signal.prf_hz,
                "sample_count": int(primary_signal.samples.size),
                "n_fft": primary.n_fft,
                "noise_sigma": primary_signal.noise_sigma,
                "reference_power": primary_signal.reference_power,
                "requested_snr_db": primary_signal.requested_snr_db,
                "realized_snr_db": primary_signal.realized_snr_db,
            },
            "profiles": profile_metrics,
            "audit": audit_metrics,
            "adapter_record": {
                key: value
                for key, value in adapter_native.items()
                if key != "raw"
            },
            "acceptance": _acceptance(primary, figure_results),
        }
        metrics_path = run_dir / "metrics.json"
        _write_json(metrics_path, metrics)

        stage = "rendering"
        figure_png = run_dir / "ahmed_fig8cd_behavioral.png"
        figure_pdf = run_dir / "ahmed_fig8cd_behavioral.pdf"
        _render_figure(figure_png, figure_pdf, primary, figure_results)

        stage = "provenance"
        output_hashes = {
            key: _sha256(path) for key, path in sorted(evidence_paths.items())
        }
        output_hashes.update(
            {
                "metrics_sha256": _sha256(metrics_path),
                "figure_png_sha256": _sha256(figure_png),
                "figure_pdf_sha256": _sha256(figure_pdf),
            }
        )
        provenance_path = run_dir / "provenance.json"
        provenance = _provenance(
            run_id, config_path, resolved_path, document, output_hashes
        )
        contract_matches, contract_reasons = _canonical_contract_matches(
            config_path, document
        )
        canonical_eligible = bool(
            provenance["git"]["clean_and_required_tracked"]
            and contract_matches
        )
        provenance["canonical_promotion"] = {
            "eligible": canonical_eligible,
            "policy": (
                "clean tree, all required source files tracked, and exact approved "
                "default v1 experiment contract"
            ),
            "contract_matches": contract_matches,
            "ineligibility_reasons": (
                contract_reasons
                + (
                    []
                    if provenance["git"]["clean_and_required_tracked"]
                    else ["git_tree_or_required_tracking_not_clean"]
                )
            ),
            "destination": (
                str((canonical_root / run_id).resolve())
                if canonical_eligible
                else None
            ),
        }
        _write_json(provenance_path, provenance)

        stage = "canonical_bundle"
        canonical_bundle = None
        if canonical_eligible:
            canonical_bundle = _promote_canonical(
                canonical_root,
                run_id,
                {
                    resolved_path: "resolved_config.yaml",
                    metrics_path: "metrics.json",
                    provenance_path: "provenance.json",
                    figure_png: figure_png.name,
                    figure_pdf: figure_pdf.name,
                },
                output_hashes,
            )

        _write_json(
            status_path,
            {
                "run_id": run_id,
                "status": "complete",
                "started_at_utc": started,
                "completed_at_utc": datetime.now(timezone.utc).isoformat(),
                "stage": "complete",
                "acceptance_status": metrics["acceptance"]["status"],
                "acceptance_passed": metrics["acceptance"]["passed"],
                "canonical_promoted": canonical_bundle is not None,
                "canonical_bundle": (
                    None if canonical_bundle is None else str(canonical_bundle.resolve())
                ),
            },
        )
        return run_dir
    except Exception as exc:
        _write_json(
            status_path,
            {
                "run_id": run_id,
                "status": "failed",
                "started_at_utc": started,
                "failed_at_utc": datetime.now(timezone.utc).isoformat(),
                "stage": stage,
                "exception_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--canonical-out", type=Path, default=DEFAULT_CANONICAL)
    args = parser.parse_args(argv)
    run_dir = execute(
        args.config.resolve(),
        args.out.resolve(),
        args.canonical_out.resolve(),
    )
    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    print(str(run_dir))
    print(metrics["acceptance"]["status"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
