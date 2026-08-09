r"""M9.2 synthetic transfer diagnostics for the project Kotte adaptation.

This script never reads radar captures or Masimo. It verifies the fixed-loop-0 adapter,
measures the four-RX direct-cisoid control, and records what the unchanged two-line
estimator does on phase-modulated chest displacement. Negative chest recovery is an
acceptable diagnostic result and never promotes estimator parameters.

Documented invocation::

    C:\ProgramData\anaconda3\condabin\conda.bat run -n radar-vitals python \
        scripts/m9_kotte_synthetic_transfer.py
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from scipy.special import jv
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.m9.kotte_core import (  # noqa: E402
    KotteJointDopplerConfig,
    estimate_window,
    extract_rx_slow_time,
    validate_project_frame_continuity,
)

CONFIG_PATH = REPO_ROOT / "experiments" / "m9_kotte" / "config.yaml"
LIVE_CONFIG_PATH = REPO_ROOT / "scripts" / "live_demo_config.yaml"
PDF_PATH = REPO_ROOT / "literature" / "ref_papers" / (
    "joint_estimation_high_amplitude_doppler"
) / "Joint_Estimation_of_Single_Targets_High_Amplitude_Difference_Doppler_Frequencies_in_FMCW_Radar.pdf"
OUTPUT_ROOT = REPO_ROOT / "results" / "m9_kotte_synthetic_transfer"
DOCUMENTED_INVOCATION = (
    r"C:\ProgramData\anaconda3\condabin\conda.bat run -n radar-vitals python "
    r"scripts/m9_kotte_synthetic_transfer.py"
)


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def array_sha256(values: np.ndarray) -> str:
    return sha256(np.ascontiguousarray(values).tobytes()).hexdigest()


def case_seed(root_seed: int, case_id: str) -> int:
    digest = sha256(f"{root_seed}:{case_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def effective_carrier_hz(live_config: dict) -> float:
    """Derive the fast-time midpoint carrier from the recorded hardware profile."""
    profile = live_config["hw_profile"]
    sample_rate_msps = float(profile["dig_out_sample_rate"]) / 1000.0
    sampled_duration_us = float(profile["num_adc_samples"]) / sample_rate_msps
    midpoint_us = float(profile["adc_start_time_us"]) + 0.5 * sampled_duration_us
    return (
        float(profile["start_freq_ghz"]) * 1.0e9
        + float(profile["freq_slope_mhz_us"]) * midpoint_us * 1.0e6
    )


def chest_displacement_phasor(
    *,
    n_frames: int,
    fs_hz: float,
    wavelength_m: float,
    breath_amplitude_m: float,
    heart_amplitude_m: float,
    f_breath_hz: float,
    f_heart_hz: float,
    phi_breath_rad: float,
    phi_heart_rad: float,
) -> np.ndarray:
    """Return ``exp(j 4 pi x/lambda)`` for the declared two-sinusoid displacement."""
    time_s = np.arange(n_frames, dtype=np.float64) / fs_hz
    displacement_m = breath_amplitude_m * np.sin(
        2.0 * np.pi * f_breath_hz * time_s + phi_breath_rad
    ) + heart_amplitude_m * np.sin(
        2.0 * np.pi * f_heart_hz * time_s + phi_heart_rad
    )
    return np.exp(1j * 4.0 * np.pi * displacement_m / wavelength_m)


def bessel_reconstruction(
    *,
    n_frames: int,
    fs_hz: float,
    wavelength_m: float,
    breath_amplitude_m: float,
    heart_amplitude_m: float,
    f_breath_hz: float,
    f_heart_hz: float,
    phi_breath_rad: float,
    phi_heart_rad: float,
    expansion_order: int,
) -> np.ndarray:
    """Independent Jacobi-Anger double-sum diagnostic of the clean phasor.

    ``exp(j m sin(theta)) = sum_k J_k(m) exp(j k theta)`` explicitly exposes the
    conjugate signed sidebands and their coherently combined Bessel comb.
    """
    time_s = np.arange(n_frames, dtype=np.float64) / fs_hz
    orders = np.arange(-expansion_order, expansion_order + 1)
    modulation_breath = 4.0 * np.pi * breath_amplitude_m / wavelength_m
    modulation_heart = 4.0 * np.pi * heart_amplitude_m / wavelength_m
    breath_terms = jv(orders, modulation_breath)[:, None] * np.exp(
        1j
        * orders[:, None]
        * (2.0 * np.pi * f_breath_hz * time_s[None, :] + phi_breath_rad)
    )
    heart_terms = jv(orders, modulation_heart)[:, None] * np.exp(
        1j
        * orders[:, None]
        * (2.0 * np.pi * f_heart_hz * time_s[None, :] + phi_heart_rad)
    )
    return breath_terms.sum(axis=0) * heart_terms.sum(axis=0)


def make_chest_z(
    phasor: np.ndarray,
    generator_config: dict,
    *,
    seed: int,
) -> tuple[np.ndarray, float]:
    """Add declared static clutter, complex channel gains, and dynamic-domain noise."""
    gains = np.asarray(generator_config["rx_gain_table"], dtype=np.float64)
    phases = np.asarray(generator_config["rx_phase_table_rad"], dtype=np.float64)
    channel_factors = gains * np.exp(1j * phases)
    clutter = np.asarray(
        [complex(v["re"], v["im"]) for v in generator_config["rx_clutter_table"]],
        dtype=np.complex128,
    )
    dynamic = phasor - phasor.mean()
    dynamic_power = float(np.mean(np.abs(dynamic) ** 2))
    noise_power = dynamic_power / 10.0 ** (
        float(generator_config["snr_z_dynamic_db"]) / 10.0
    )
    rng = np.random.default_rng(seed)
    noise = np.sqrt(noise_power / 2.0) * (
        rng.standard_normal((phasor.size, 4))
        + 1j * rng.standard_normal((phasor.size, 4))
    )
    before_channel = phasor[:, None] + clutter[None, :] + noise
    z = np.asarray(before_channel * channel_factors[None, :], dtype=np.complex128)
    realized_noise = noise * channel_factors[None, :]
    realized_dynamic = dynamic[:, None] * channel_factors[None, :]
    realized_snr_db = 10.0 * np.log10(
        np.mean(np.abs(realized_dynamic) ** 2) / np.mean(np.abs(realized_noise) ** 2)
    )
    return z, float(realized_snr_db)


def make_direct_cisoid_z(config: dict) -> np.ndarray:
    generator = config["generator"]
    control = config["direct_cisoid_control"]
    time_s = np.arange(int(generator["n_frames"])) / float(generator["fs_hz"])
    beta_b = complex(control["beta_breath"]["re"], control["beta_breath"]["im"])
    beta_h = complex(control["beta_heart"]["re"], control["beta_heart"]["im"])
    signal = beta_b * np.exp(2j * np.pi * float(control["f_breath_hz"]) * time_s)
    signal += beta_h * np.exp(2j * np.pi * float(control["f_heart_hz"]) * time_s)
    gains = np.asarray(generator["rx_gain_table"]) * np.exp(
        1j * np.asarray(generator["rx_phase_table_rad"])
    )
    rng = np.random.default_rng(int(control["seed"]))
    variance = float(control["noise_variance"])
    noise = np.sqrt(variance / 2.0) * (
        rng.standard_normal((time_s.size, 4)) + 1j * rng.standard_normal((time_s.size, 4))
    )
    return np.asarray(signal[:, None] * gains[None, :] + noise, dtype=np.complex128)


def fixed_loop_control(generator: dict) -> tuple[dict, dict[str, np.ndarray]]:
    """Exercise exact complex64 cube shape, Hann FFT, loop 0, RX order, and Hz timing."""
    n_frames = int(generator["n_frames"])
    n_chirps = int(generator["n_chirps"])
    n_rx = int(generator["n_rx"])
    n_adc = int(generator["n_adc_samples"])
    target_bin = int(generator["target_bin"])
    fs_hz = float(generator["fs_hz"])
    expected_hz = 0.30
    validate_project_frame_continuity(
        np.arange(n_frames, dtype=np.int64),
        frame_period_s=1.0 / fs_hz,
        metadata={
            "frame_count": n_frames,
            "num_chirps_per_frame": n_chirps,
            "num_rx": n_rx,
            "num_adc_samples": n_adc,
            "file_size_divisible": True,
            "packet_drop_count": 0,
        },
    )
    time_s = np.arange(n_frames) / fs_hz
    adc_index = np.arange(n_adc)
    range_tone = np.exp(2j * np.pi * target_bin * adc_index / n_adc)
    rx_factors = np.asarray(generator["rx_gain_table"]) * np.exp(
        1j * np.asarray(generator["rx_phase_table_rad"])
    )
    cube = np.empty((n_frames, n_chirps, n_rx, n_adc), dtype=np.complex64)
    decoy = np.exp(2j * np.pi * 1.75 * time_s)
    cube[:] = (decoy[:, None, None, None] * range_tone[None, None, None, :]).astype(
        np.complex64
    )
    loop_zero = (
        np.exp(2j * np.pi * expected_hz * time_s)[:, None, None]
        * rx_factors[None, :, None]
        * range_tone[None, None, :]
    )
    cube[:, 0, :, :] = loop_zero.astype(np.complex64)
    z = extract_rx_slow_time(cube, target_bin, chirp_loop_index=0)
    observed_hz = float(
        np.angle(np.mean(z[1:, 0] * z[:-1, 0].conj())) * fs_hz / (2.0 * np.pi)
    )
    expected_z = np.fft.fft(
        cube[:, 0, :, :] * np.hanning(n_adc)[None, None, :], axis=-1
    )[:, :, target_bin]
    max_error = float(np.max(np.abs(z - expected_z)))
    report = {
        "cube_shape": list(cube.shape),
        "cube_dtype": str(cube.dtype),
        "z_shape": list(z.shape),
        "z_dtype": str(z.dtype),
        "expected_phase_ramp_hz": expected_hz,
        "observed_phase_ramp_hz": observed_hz,
        "max_hann_fft_error": max_error,
        "input_cube_sha256": array_sha256(cube),
        "output_z_sha256": array_sha256(z),
        "passed": bool(abs(observed_hz - expected_hz) < 1.0e-6 and max_error == 0.0),
    }
    return report, {"z": z, "expected_z": expected_z}


def write_shared_z_artifact(
    out_dir: Path,
    *,
    case_id: str,
    seed: int,
    z: np.ndarray,
) -> dict[str, str]:
    """Persist one input-Z artifact shared by every estimator arm for a case."""
    input_z = np.asarray(z)
    input_hash = array_sha256(input_z)
    path = out_dir / f"{case_id}_input_z.npz"
    np.savez_compressed(
        path,
        z=input_z,
        case_id=np.asarray(case_id),
        seed=np.asarray(seed, dtype=np.uint64),
        input_z_sha256=np.asarray(input_hash),
        shape=np.asarray(input_z.shape, dtype=np.int64),
        dtype=np.asarray(str(input_z.dtype)),
    )
    return {
        "input_z_artifact": path.name,
        "input_z_artifact_sha256": sha256_file(path),
        "input_z_sha256": input_hash,
    }


def compact_evidence(
    evidence: dict,
    medoid_index: int,
    *,
    shared_z_reference: dict[str, str],
) -> dict[str, np.ndarray]:
    """Persist every scalar CPI diagnostic and the selected CPI's inspectable surface."""
    keep = {
        "cpi_eigvals",
        "loading_delta",
        "objective_name",
        "cpi_ranks",
        "cpi_delta_bar",
        "cpi_valid",
        "cpi_cause_codes",
        "cpi_masked_fraction",
        "f1_grid_hz",
        "f2_grid_hz",
        "cpi_estimates_bpm",
        "cpi_raw_signed_pairs_hz",
        "cpi_pair_margin_db",
    }
    arrays = {key: np.asarray(evidence[key]) for key in keep}
    arrays["medoid_index"] = np.asarray(medoid_index, dtype=np.int64)
    for key, value in shared_z_reference.items():
        arrays[key] = np.asarray(value)
    arrays["medoid_regularized_kotte_power"] = np.asarray(
        evidence["cpi_regularized_kotte_power"][medoid_index]
    )
    arrays["medoid_constraint_rcond"] = np.asarray(
        evidence["cpi_constraint_rcond"][medoid_index]
    )
    arrays["medoid_constraint_mask"] = np.asarray(
        evidence["cpi_constraint_mask"][medoid_index]
    )
    return arrays


def _git_text(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, text=True, capture_output=True, check=False
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def main() -> int:
    with CONFIG_PATH.open(encoding="utf-8") as handle:
        full_config = yaml.safe_load(handle)
    with LIVE_CONFIG_PATH.open(encoding="utf-8") as handle:
        live_config = yaml.safe_load(handle)
    transfer = full_config["synthetic_transfer_diagnostics"]
    generator = transfer["generator"]
    estimator_config = KotteJointDopplerConfig.from_stage_a(full_config["stage_a"])
    carrier_hz = effective_carrier_hz(live_config)
    wavelength_m = 299_792_458.0 / carrier_hz

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    config_hash = sha256_file(CONFIG_PATH)
    out_dir = OUTPUT_ROOT / f"{stamp}_{config_hash[:12]}_diagnostic_nondeployable"
    out_dir.mkdir(parents=True, exist_ok=False)
    (out_dir / "config.yaml").write_bytes(CONFIG_PATH.read_bytes())

    fixed_report, fixed_arrays = fixed_loop_control(generator)
    np.savez_compressed(out_dir / "fixed_loop_control.npz", **fixed_arrays)

    rows: list[dict] = []
    direct_z = make_direct_cisoid_z(transfer)
    direct_control = transfer["direct_cisoid_control"]
    direct_z_reference = write_shared_z_artifact(
        out_dir,
        case_id="direct_four_rx_two_cisoid",
        seed=int(direct_control["seed"]),
        z=direct_z,
    )
    for arm in estimator_config.arms:
        native, evidence = estimate_window(
            direct_z, config=estimator_config, arm=arm, fs_hz=float(generator["fs_hz"])
        )
        medoid_index = int(native["medoid_cpi_index"])
        artifact = out_dir / f"direct_cisoid_{arm.arm_id}.npz"
        np.savez_compressed(
            artifact,
            **compact_evidence(
                evidence, medoid_index, shared_z_reference=direct_z_reference
            ),
        )
        rows.append(
            {
                "case_id": "direct_four_rx_two_cisoid",
                "case_role": "project_four_rx_loaded_two_cisoid_adaptation",
                "seed": int(direct_control["seed"]),
                "arm_id": arm.arm_id,
                "expected_breath_hz": float(direct_control["f_breath_hz"]),
                "expected_heart_hz": float(direct_control["f_heart_hz"]),
                "selected_breath_hz": float(native["br_bpm"]) / 60.0,
                "selected_heart_hz": float(native["hr_raw"]) / 60.0,
                "selected_raw_signed_hz": native["selected_raw_signed_hz"],
                "recovered_within_one_grid_step": bool(
                    abs(float(native["br_bpm"]) / 60.0 - float(direct_control["f_breath_hz"]))
                    <= estimator_config.grid_step_bpm / 60.0 + 1e-12
                    and abs(float(native["hr_raw"]) / 60.0 - float(direct_control["f_heart_hz"]))
                    <= estimator_config.grid_step_bpm / 60.0 + 1e-12
                ),
                **direct_z_reference,
                "artifact": artifact.name,
                "artifact_sha256": sha256_file(artifact),
            }
        )

    analytic_tolerance = float(transfer["bessel_diagnostic"]["max_relative_rms_error"])
    expansion_order = int(transfer["bessel_diagnostic"]["expansion_order"])
    analytic_errors: list[float] = []
    snr_errors_db: list[float] = []
    for scenario in transfer["scenarios"]:
        for phase_index, phase_pair in enumerate(generator["phase_pairs_rad"]):
            case_id = f"{scenario['scenario_id']}_phase{phase_index}"
            seed = case_seed(int(generator["root_seed"]), case_id)
            common = {
                "n_frames": int(generator["n_frames"]),
                "fs_hz": float(generator["fs_hz"]),
                "wavelength_m": wavelength_m,
                "breath_amplitude_m": float(scenario["breath_amplitude_m"]),
                "heart_amplitude_m": float(scenario["heart_amplitude_m"]),
                "f_breath_hz": float(scenario["f_breath_hz"]),
                "f_heart_hz": float(scenario["f_heart_hz"]),
                "phi_breath_rad": float(phase_pair[0]),
                "phi_heart_rad": float(phase_pair[1]),
            }
            phasor = chest_displacement_phasor(**common)
            analytic = bessel_reconstruction(**common, expansion_order=expansion_order)
            relative_rms_error = float(
                np.sqrt(np.mean(np.abs(phasor - analytic) ** 2))
                / np.sqrt(np.mean(np.abs(phasor) ** 2))
            )
            analytic_errors.append(relative_rms_error)
            z, realized_snr_db = make_chest_z(phasor, generator, seed=seed)
            shared_z_reference = write_shared_z_artifact(
                out_dir, case_id=case_id, seed=seed, z=z
            )
            snr_errors_db.append(
                abs(realized_snr_db - float(generator["snr_z_dynamic_db"]))
            )
            for arm in estimator_config.arms:
                native, evidence = estimate_window(
                    z, config=estimator_config, arm=arm, fs_hz=float(generator["fs_hz"])
                )
                medoid_index = int(native["medoid_cpi_index"])
                artifact = out_dir / f"{case_id}_{arm.arm_id}.npz"
                np.savez_compressed(
                    artifact,
                    **compact_evidence(
                        evidence, medoid_index, shared_z_reference=shared_z_reference
                    ),
                )
                rows.append(
                    {
                        "case_id": case_id,
                        "case_role": "diagnostic_nondeployable_chest_transfer",
                        "phase_case_label": generator["phase_pair_labels"][phase_index],
                        "phase_pair_rad": [float(phase_pair[0]), float(phase_pair[1])],
                        "seed": seed,
                        "arm_id": arm.arm_id,
                        "expected_breath_hz": float(scenario["f_breath_hz"]),
                        "expected_heart_hz": float(scenario["f_heart_hz"]),
                        "selected_breath_hz": float(native["br_bpm"]) / 60.0,
                        "selected_heart_hz": float(native["hr_raw"]) / 60.0,
                        "selected_raw_signed_hz": native["selected_raw_signed_hz"],
                        "realized_snr_db": realized_snr_db,
                        "bessel_relative_rms_error": relative_rms_error,
                        **shared_z_reference,
                        "artifact": artifact.name,
                        "artifact_sha256": sha256_file(artifact),
                    }
                )

    # The declared robustness seeds describe stability only. They repeat the small-
    # displacement, zero-phase case and cannot select or promote a loading arm.
    robustness_scenario = transfer["scenarios"][0]
    for robustness_seed in transfer["robustness_seeds"]:
        case_id = f"{robustness_scenario['scenario_id']}_robustness_seed{robustness_seed}"
        common = {
            "n_frames": int(generator["n_frames"]),
            "fs_hz": float(generator["fs_hz"]),
            "wavelength_m": wavelength_m,
            "breath_amplitude_m": float(robustness_scenario["breath_amplitude_m"]),
            "heart_amplitude_m": float(robustness_scenario["heart_amplitude_m"]),
            "f_breath_hz": float(robustness_scenario["f_breath_hz"]),
            "f_heart_hz": float(robustness_scenario["f_heart_hz"]),
            "phi_breath_rad": 0.0,
            "phi_heart_rad": 0.0,
        }
        z, realized_snr_db = make_chest_z(
            chest_displacement_phasor(**common), generator, seed=int(robustness_seed)
        )
        shared_z_reference = write_shared_z_artifact(
            out_dir, case_id=case_id, seed=int(robustness_seed), z=z
        )
        snr_errors_db.append(
            abs(realized_snr_db - float(generator["snr_z_dynamic_db"]))
        )
        for arm in estimator_config.arms:
            native, evidence = estimate_window(
                z, config=estimator_config, arm=arm, fs_hz=float(generator["fs_hz"])
            )
            medoid_index = int(native["medoid_cpi_index"])
            artifact = out_dir / f"{case_id}_{arm.arm_id}.npz"
            np.savez_compressed(
                artifact,
                **compact_evidence(
                    evidence, medoid_index, shared_z_reference=shared_z_reference
                ),
            )
            rows.append(
                {
                    "case_id": case_id,
                    "case_role": "diagnostic_nondeployable_robustness_description",
                    "seed": int(robustness_seed),
                    "arm_id": arm.arm_id,
                    "expected_breath_hz": float(robustness_scenario["f_breath_hz"]),
                    "expected_heart_hz": float(robustness_scenario["f_heart_hz"]),
                    "selected_breath_hz": float(native["br_bpm"]) / 60.0,
                    "selected_heart_hz": float(native["hr_raw"]) / 60.0,
                    "selected_raw_signed_hz": native["selected_raw_signed_hz"],
                    "realized_snr_db": realized_snr_db,
                    **shared_z_reference,
                    "artifact": artifact.name,
                    "artifact_sha256": sha256_file(artifact),
                }
            )

    source_hashes = {
        "config": config_hash,
        "live_config": sha256_file(LIVE_CONFIG_PATH),
        "kotte_core": sha256_file(REPO_ROOT / "src" / "m9" / "kotte_core.py"),
        "runner": sha256_file(Path(__file__).resolve()),
        "focused_tests": sha256_file(REPO_ROOT / "tests" / "test_m9_kotte_core.py"),
        "canonical_plan": sha256_file(REPO_ROOT / "plans" / "m9_kotte_plan.md"),
        "paper_pdf": sha256_file(PDF_PATH),
    }
    snr_tolerance_db = float(generator["snr_tolerance_db"])
    structural_checks_passed = bool(
        fixed_report["passed"]
        and max(analytic_errors) <= analytic_tolerance
        and max(snr_errors_db) <= snr_tolerance_db
    )
    summary = {
        "status": "complete" if structural_checks_passed else "failed",
        "role": "diagnostic_nondeployable",
        "parameter_promotion_allowed": False,
        "negative_transfer_is_acceptable": True,
        "fixed_loop_control": fixed_report,
        "carrier_hz": carrier_hz,
        "wavelength_m": wavelength_m,
        "bessel_expansion_order": expansion_order,
        "bessel_max_relative_rms_error_observed": max(analytic_errors),
        "bessel_max_relative_rms_error_allowed": analytic_tolerance,
        "snr_max_absolute_error_db_observed": max(snr_errors_db),
        "snr_max_absolute_error_db_allowed": snr_tolerance_db,
        "cases": rows,
    }
    actual_argv = [sys.executable, *sys.argv]
    run_meta = {
        "run_id": out_dir.name,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "actual_argv": actual_argv,
        "actual_invocation": subprocess.list2cmdline(actual_argv),
        "documented_invocation": DOCUMENTED_INVOCATION,
        "git_commit": _git_text("rev-parse", "HEAD"),
        "git_dirty_paths": _git_text("status", "--short").splitlines(),
        "thesis_grade": False,
        "evidence_role": "implementation_validation_non_thesis_diagnostic",
        "source_hashes": source_hashes,
        "root_seed": int(generator["root_seed"]),
        "seed_derivation": generator["seed_derivation"],
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    (out_dir / "run_meta.json").write_text(
        json.dumps(run_meta, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(f"artifact_dir : {out_dir.relative_to(REPO_ROOT)}")
    print(f"status       : {summary['status']}")
    print(f"fixed_loop   : {fixed_report['passed']}")
    print(f"bessel_error : {max(analytic_errors):.3e} <= {analytic_tolerance:.3e}")
    print(f"snr_error_db : {max(snr_errors_db):.3f} <= {snr_tolerance_db:.3f}")
    print(f"summary_sha256: {sha256_file(out_dir / 'summary.json')}")
    return 0 if summary["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
