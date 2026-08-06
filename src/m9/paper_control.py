"""M9 controls 1 + 2: paper-faithful Kotte reproduction and the 4-RX ablation.

Control 1 (Step 1a) reproduces Figs 5 + 7(row 2) + 8 of Kotte et al. (IEEE T-RS 2024,
DOI 10.1109/TRS.2024.3352189) with direct ``Y_t`` generation (theta0 known; range/DOA
descoped), the pinned FFT + MUSIC comparators, and the verdict truth table. Control 2
is the fixed-endpoint 20->4 RX ablation on the same generated realizations.

Every parameter comes from ``experiments/m9_kotte/config.yaml``'s ``controls`` section —
nothing numerical is hardcoded here. Official runs require a clean git tree; ``--smoke``
runs are labelled non-gating scratch and write to ``*_smoke/`` directories.

Verdict truth table (plan section "Step 1a"): primary = Algorithm 1's selection line
(``1^T H^-1 1`` on the sample covariance, unloaded, 20 RX); secondary = eq-26
``|beta_hat|^2`` at known theta0; each judged against truth within one grid step with
the symmetric swap allowed. Audits vary ONE field each and can never upgrade a verdict.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Mapping

import numpy as np
import yaml

from src.m4.bundle import sha256_bytes, sha256_path, strict_json_bytes
from src.m4.estimator_suite import canonical_plain
from src.m8.ahmed_provenance import environment_attestation, git_status_paths, git_text
from src.m9.kotte_core import (
    AllMaskedError,
    CaponContext,
    argmax_masked,
    beta_surface,
    canonical_hash,
    joint_capon_surface,
    make_grid_hz,
    remove_per_rx_mean,
    sample_covariance,
    spatial_steering,
    temporal_steering,
)

__all__ = [
    "CaseResult",
    "ControlsConfig",
    "assemble_run_meta",
    "case_seed",
    "classify_case",
    "combine_verdicts",
    "evaluate_comparators",
    "evaluate_proposed",
    "fft_mean_periodogram",
    "generate_case_yt",
    "load_experiment_config",
    "merged_peak_frequencies",
    "music_pseudospectrum",
    "require_clean_tree",
    "run_ablation",
    "run_paper_controls",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_RELPATH = "experiments/m9_kotte/config.yaml"
PDF_RELPATH = (
    "literature/ref_papers/joint_estimation_high_amplitude_doppler/"
    "Joint_Estimation_of_Single_Targets_High_Amplitude_Difference_Doppler_"
    "Frequencies_in_FMCW_Radar.pdf"
)
#: Runtime files whose hashes bind every control run (plan: provenance/controls).
RUNTIME_RELPATHS = (
    "src/m9/kotte_core.py",
    "src/m9/paper_control.py",
    "figures/reproduce_kotte_controls.py",
    CONFIG_RELPATH,
    "environment.yml",
)

VERDICT_NOT_REPRODUCED = "not_reproduced_under_declared_assumptions"
VERDICT_AMBIGUOUS = "ambiguous_reproduction"
VERDICT_REPRODUCED = "behaviorally_reproduced"


# ---------------------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ControlsConfig:
    raw: dict
    whole_file_sha256: str
    controls_section_hash: str

    @property
    def controls(self) -> dict:
        return self.raw["controls"]


def load_experiment_config(path: Path | None = None) -> ControlsConfig:
    path = Path(path or (REPO_ROOT / CONFIG_RELPATH))
    data = path.read_bytes()
    raw = yaml.safe_load(data.decode("utf-8"))
    return ControlsConfig(
        raw=raw,
        whole_file_sha256=sha256_bytes(data),
        controls_section_hash=canonical_hash(
            {"controls": canonical_plain(raw["controls"])}
        ),
    )


def case_seed(root_seed: int, case_id: str) -> int:
    """Deterministic, order-independent per-case seed (low 32 bits of SHA-256)."""
    digest = sha256(f"{root_seed}:{case_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


def _beta(entry: Mapping[str, float]) -> complex:
    return complex(float(entry["re"]), float(entry["im"]))


# ---------------------------------------------------------------------------------------
# Case generation (paper eq (23), direct Y_t)
# ---------------------------------------------------------------------------------------


def generate_case_yt(
    *,
    beta1: complex,
    beta2: complex,
    f1_hz: float,
    f2_hz: float,
    theta0_deg: float,
    n_c: int,
    n_r: int,
    t_pri_s: float,
    snr_db: float,
    seed: int,
) -> tuple[np.ndarray, float]:
    """Two-line ``Y_t`` with AWGN at the paper's two-line power ratio.

    ``sigma^2 = (|beta1|^2 + |beta2|^2) / 10^(SNR/10)`` per complex element (page-07
    render). Returns ``(y_t, sigma2)``.
    """
    rng = np.random.default_rng(seed)
    a1 = temporal_steering(np.array([f1_hz]), n_c, t_pri_s)[:, 0]
    a2 = temporal_steering(np.array([f2_hz]), n_c, t_pri_s)[:, 0]
    s = beta1 * a1 + beta2 * a2
    a_theta = spatial_steering(np.deg2rad(theta0_deg), n_r)
    sigma2 = (abs(beta1) ** 2 + abs(beta2) ** 2) / (10.0 ** (snr_db / 10.0))
    noise = np.sqrt(sigma2 / 2.0) * (
        rng.standard_normal((n_c, n_r)) + 1j * rng.standard_normal((n_c, n_r))
    )
    return np.outer(s, a_theta) + noise, float(sigma2)


# ---------------------------------------------------------------------------------------
# Comparators (pinned as DECLARED REPRODUCTION ASSUMPTIONS; the paper is qualitative)
# ---------------------------------------------------------------------------------------


def fft_mean_periodogram(
    y_t: np.ndarray, *, n_fft: int, t_pri_s: float
) -> tuple[np.ndarray, np.ndarray]:
    """Mean per-RX periodogram: raw columns (no window, no mean removal), zero-padded,
    incoherently averaged over RX, amplitude-normalized to its maximum."""
    spec = (np.abs(np.fft.fft(y_t, n=n_fft, axis=0)) ** 2).mean(axis=1)
    freqs = np.fft.fftfreq(n_fft, d=t_pri_s)
    order = np.argsort(freqs)
    spec = spec[order]
    return freqs[order], spec / spec.max()


def music_pseudospectrum(
    y_t: np.ndarray, *, model_order: int, grid_hz: np.ndarray, t_pri_s: float
) -> np.ndarray:
    """MUSIC on ``Y_t Y_t^H / n_R`` (no loading): ``1/(a^H En En^H a)``, max-normalized."""
    n_c = y_t.shape[0]
    r = sample_covariance(y_t)
    _eigvals, eigvecs = np.linalg.eigh(r)  # ascending
    noise_subspace = eigvecs[:, : n_c - model_order]
    a = temporal_steering(grid_hz, n_c, t_pri_s)
    projected = noise_subspace.conj().T @ a
    denom = np.einsum("kf,kf->f", projected.conj(), projected).real
    pseudo = 1.0 / denom
    return pseudo / pseudo.max()


def merged_peak_frequencies(
    freqs_hz: np.ndarray,
    spectrum: np.ndarray,
    *,
    merge_radius_hz: float,
    top_k: int,
) -> list[float]:
    """The declared peak rule: local maxima, greedy-merged within ``merge_radius_hz``
    (larger amplitude wins), top ``top_k`` returned in descending amplitude."""
    interior = np.arange(1, len(spectrum) - 1)
    is_peak = (spectrum[interior] > spectrum[interior - 1]) & (
        spectrum[interior] >= spectrum[interior + 1]
    )
    candidates = interior[is_peak]
    order = candidates[np.argsort(spectrum[candidates])[::-1]]
    accepted: list[int] = []
    for idx in order:
        if all(abs(freqs_hz[idx] - freqs_hz[kept]) > merge_radius_hz for kept in accepted):
            accepted.append(int(idx))
        if len(accepted) >= top_k:
            break
    return [float(freqs_hz[idx]) for idx in accepted]


def evaluate_comparators(
    y_t: np.ndarray,
    *,
    controls: Mapping,
    grid_hz: np.ndarray,
    f_weak_hz: float,
    merge_radius_override_hz: float | None = None,
    music_order_override: int | None = None,
) -> dict:
    """FFT + MUSIC weak-component detection under the declared weak-peak rule."""
    rule = controls["comparators"]["weak_peak_rule"]
    merge_radius = float(
        merge_radius_override_hz
        if merge_radius_override_hz is not None
        else rule["merge_radius_hz"]
    )
    top_k = int(rule["top_k"])
    tolerance = float(rule["weak_tolerance_hz"])
    t_pri_s = float(controls["t_pri_s"])

    fft_cfg = controls["comparators"]["fft"]
    freqs, spec = fft_mean_periodogram(
        y_t, n_fft=int(fft_cfg["n_fft_comparator"]), t_pri_s=t_pri_s
    )
    fft_peaks = merged_peak_frequencies(
        freqs, spec, merge_radius_hz=merge_radius, top_k=top_k
    )

    music_cfg = controls["comparators"]["music"]
    order = int(
        music_order_override if music_order_override is not None else music_cfg["model_order"]
    )
    pseudo = music_pseudospectrum(
        y_t, model_order=order, grid_hz=grid_hz, t_pri_s=t_pri_s
    )
    music_peaks = merged_peak_frequencies(
        grid_hz, pseudo, merge_radius_hz=merge_radius, top_k=top_k
    )

    def detected(peaks: list[float]) -> bool:
        return any(abs(p - f_weak_hz) <= tolerance for p in peaks)

    return {
        "fft_weak_detected": detected(fft_peaks),
        "fft_top_peaks_hz": fft_peaks,
        "music_weak_detected": detected(music_peaks),
        "music_top_peaks_hz": music_peaks,
        "music_model_order": order,
        "merge_radius_hz": merge_radius,
        "weak_tolerance_hz": tolerance,
    }


# ---------------------------------------------------------------------------------------
# Proposed method evaluation + verdict truth table
# ---------------------------------------------------------------------------------------


def _pair_hit(
    selected: tuple[float, float],
    truth: tuple[float, float],
    step_hz: float,
) -> bool:
    """Within one grid step, symmetric swap allowed."""
    tol = step_hz + 1e-9
    s_sorted = sorted(selected)
    t_sorted = sorted(truth)
    return all(abs(a - b) <= tol for a, b in zip(s_sorted, t_sorted))


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    seed: int
    yt_sha256: str
    primary_selected_hz: tuple[float, float] | None
    primary_hit: bool
    secondary_selected_hz: tuple[float, float] | None
    secondary_hit: bool
    masked_fraction: float
    dsp_failure: str | None

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "seed": self.seed,
            "yt_sha256": self.yt_sha256,
            "primary_selected_hz": list(self.primary_selected_hz)
            if self.primary_selected_hz
            else None,
            "primary_hit": self.primary_hit,
            "secondary_selected_hz": list(self.secondary_selected_hz)
            if self.secondary_selected_hz
            else None,
            "secondary_hit": self.secondary_hit,
            "masked_fraction": self.masked_fraction,
            "dsp_failure": self.dsp_failure,
        }


def evaluate_proposed(
    y_t: np.ndarray,
    *,
    case_id: str,
    seed: int,
    truth_pair_hz: tuple[float, float],
    theta0_deg: float,
    controls: Mapping,
    grid_hz: np.ndarray,
    mean_removal_on: bool = False,
    loading_delta: float | None = None,
    covariance_override: np.ndarray | None = None,
) -> tuple[CaseResult, dict]:
    """Primary (selection surface) + secondary (eq-26 surface) against truth.

    Returns the classified case result plus an evidence dict of arrays.
    """
    t_pri_s = float(controls["t_pri_s"])
    n_c = int(controls["n_c"])
    step_hz = float(controls["grid"]["step_hz"])
    mask_threshold = float(controls["condition_mask_threshold"])
    rank_rtol = 1e-9

    data = remove_per_rx_mean(y_t) if mean_removal_on else y_t
    covariance = (
        covariance_override if covariance_override is not None else sample_covariance(data)
    )
    yt_hash = sha256_bytes(np.ascontiguousarray(data).tobytes())
    try:
        ctx = CaponContext.from_covariance(
            covariance, loading_delta=loading_delta, rank_rtol=rank_rtol
        )
        surface = joint_capon_surface(
            ctx, grid_hz, grid_hz, n_c=n_c, t_pri_s=t_pri_s,
            condition_mask_threshold=mask_threshold,
        )
        i, j = argmax_masked(surface.objective)
        primary_selected = (float(grid_hz[i]), float(grid_hz[j]))
        primary_hit = _pair_hit(primary_selected, truth_pair_hz, step_hz)

        bsurf = np.abs(
            beta_surface(
                ctx, data, grid_hz, grid_hz, theta0_rad=float(np.deg2rad(theta0_deg)),
                t_pri_s=t_pri_s, condition_mask_threshold=mask_threshold,
            )
        ) ** 2
        bi, bj = argmax_masked(bsurf)
        secondary_selected = (float(grid_hz[bi]), float(grid_hz[bj]))
        secondary_hit = _pair_hit(secondary_selected, truth_pair_hz, step_hz)
        result = CaseResult(
            case_id=case_id,
            seed=seed,
            yt_sha256=yt_hash,
            primary_selected_hz=primary_selected,
            primary_hit=primary_hit,
            secondary_selected_hz=secondary_selected,
            secondary_hit=secondary_hit,
            masked_fraction=surface.masked_fraction,
            dsp_failure=None,
        )
        evidence = {
            "objective": surface.objective,
            "beta_sq": bsurf,
            "masked": surface.masked,
            "grid_hz": np.asarray(grid_hz),
        }
        return result, evidence
    except (AllMaskedError, np.linalg.LinAlgError) as exc:
        result = CaseResult(
            case_id=case_id,
            seed=seed,
            yt_sha256=yt_hash,
            primary_selected_hz=None,
            primary_hit=False,
            secondary_selected_hz=None,
            secondary_hit=False,
            masked_fraction=1.0,
            dsp_failure=type(exc).__name__,
        )
        return result, {"grid_hz": np.asarray(grid_hz)}


def classify_case(primary_hit: bool, secondary_hit: bool) -> str:
    """The plan's verdict truth table for one case."""
    if not primary_hit:
        return VERDICT_NOT_REPRODUCED
    if not secondary_hit:
        return VERDICT_AMBIGUOUS
    return VERDICT_REPRODUCED


def combine_verdicts(case_verdicts: list[str]) -> str:
    """Aggregate: any primary miss dominates; then any ambiguity; else reproduced.

    ``ambiguous_reproduction`` cannot pass and cannot be upgraded (plan truth table);
    audits are aggregated elsewhere and can never upgrade this value.
    """
    if any(v == VERDICT_NOT_REPRODUCED for v in case_verdicts):
        return VERDICT_NOT_REPRODUCED
    if any(v == VERDICT_AMBIGUOUS for v in case_verdicts):
        return VERDICT_AMBIGUOUS
    return VERDICT_REPRODUCED


# ---------------------------------------------------------------------------------------
# R1 / R2 / R3 / audits
# ---------------------------------------------------------------------------------------


def _grid_from_controls(controls: Mapping, step_override: float | None = None) -> np.ndarray:
    grid_cfg = controls["grid"]
    return make_grid_hz(
        float(grid_cfg["start_hz"]),
        float(grid_cfg["stop_hz"]),
        float(step_override if step_override is not None else grid_cfg["step_hz"]),
    )


def run_r1(config: ControlsConfig, *, snr_override_db: float | None = None) -> dict:
    """Fig 8 amplitude ladder: proposed truth-table + FFT/MUSIC comparator matrix."""
    controls = config.controls
    r1 = controls["r1_fig8"]
    grid = _grid_from_controls(controls)
    beta1 = _beta(r1["beta1"])
    truth = (float(r1["f_strong_hz"]), float(r1["f_weak_hz"]))
    snr_db = float(snr_override_db if snr_override_db is not None else controls["snr_db"])

    cases = []
    comparator_cells = []
    case_map = {}
    for row in r1["expected"]:
        ratio = float(row["ratio"])
        case_id = f"r1_fig8:ratio={ratio}"
        seed = case_seed(int(controls["root_seed"]), case_id)
        y_t, _sigma2 = generate_case_yt(
            beta1=beta1,
            beta2=ratio * beta1,
            f1_hz=truth[0],
            f2_hz=truth[1],
            theta0_deg=float(r1["theta0_deg"]),
            n_c=int(controls["n_c"]),
            n_r=int(controls["n_r"]),
            t_pri_s=float(controls["t_pri_s"]),
            snr_db=snr_db,
            seed=seed,
        )
        result, evidence = evaluate_proposed(
            y_t,
            case_id=case_id,
            seed=seed,
            truth_pair_hz=truth,
            theta0_deg=float(r1["theta0_deg"]),
            controls=controls,
            grid_hz=grid,
        )
        comparators = evaluate_comparators(
            y_t, controls=controls, grid_hz=grid, f_weak_hz=truth[1]
        )
        observed_cell = {
            "ratio": ratio,
            "fft_weak": "detect" if comparators["fft_weak_detected"] else "fail",
            "music_weak": "detect" if comparators["music_weak_detected"] else "fail",
            "proposed": "hit" if result.primary_hit and result.secondary_hit else "miss",
        }
        expected_cell = {
            "ratio": ratio,
            "fft_weak": row["fft_weak"],
            "music_weak": row["music_weak"],
            "proposed": row["proposed"],
        }
        comparator_cells.append(
            {
                "expected": expected_cell,
                "observed": observed_cell,
                "matches": observed_cell == expected_cell,
                "detail": comparators,
            }
        )
        cases.append(result)
        case_map[case_id] = {"evidence": evidence, "result": result}

    case_verdicts = [classify_case(c.primary_hit, c.secondary_hit) for c in cases]
    verdict = combine_verdicts(case_verdicts)
    return {
        "section": "r1_fig8",
        "snr_db": snr_db,
        "verdict": verdict,
        "comparator_matrix_matches": all(cell["matches"] for cell in comparator_cells),
        "comparator_cells": comparator_cells,
        "cases": [c.to_dict() for c in cases],
        "case_verdicts": case_verdicts,
        "_case_map": case_map,
    }


def run_r2(config: ControlsConfig, *, snr_override_db: float | None = None) -> dict:
    """Fig 5 surfaces: per-target argmax within one grid step (swap allowed)."""
    controls = config.controls
    grid = _grid_from_controls(controls)
    snr_db = float(snr_override_db if snr_override_db is not None else controls["snr_db"])
    cases = []
    case_map = {}
    for target in controls["r2_fig5"]["targets"]:
        case_id = f"r2_fig5:{target['label']}"
        seed = case_seed(int(controls["root_seed"]), case_id)
        truth = (float(target["f_pair_hz"][0]), float(target["f_pair_hz"][1]))
        y_t, _sigma2 = generate_case_yt(
            beta1=_beta(target["beta1"]),
            beta2=_beta(target["beta2"]),
            f1_hz=truth[0],
            f2_hz=truth[1],
            theta0_deg=float(target["theta0_deg"]),
            n_c=int(controls["n_c"]),
            n_r=int(controls["n_r"]),
            t_pri_s=float(controls["t_pri_s"]),
            snr_db=snr_db,
            seed=seed,
        )
        result, evidence = evaluate_proposed(
            y_t,
            case_id=case_id,
            seed=seed,
            truth_pair_hz=truth,
            theta0_deg=float(target["theta0_deg"]),
            controls=controls,
            grid_hz=grid,
        )
        cases.append(result)
        case_map[case_id] = {"evidence": evidence, "result": result}
    case_verdicts = [classify_case(c.primary_hit, c.secondary_hit) for c in cases]
    return {
        "section": "r2_fig5",
        "snr_db": snr_db,
        "verdict": combine_verdicts(case_verdicts),
        "cases": [c.to_dict() for c in cases],
        "case_verdicts": case_verdicts,
        "_case_map": case_map,
    }


def run_r3(config: ControlsConfig, *, snr_override_db: float | None = None) -> dict:
    """Fig 7 row 2: proposed resolves (1.5, 1.0); FFT and MUSIC fail the weak line."""
    controls = config.controls
    r3 = controls["r3_fig7_row2"]
    grid = _grid_from_controls(controls)
    snr_db = float(snr_override_db if snr_override_db is not None else controls["snr_db"])
    beta1 = _beta(r3["beta1"])
    truth = (float(r3["f_strong_hz"]), float(r3["f_weak_hz"]))
    case_id = "r3_fig7_row2"
    seed = case_seed(int(controls["root_seed"]), case_id)
    y_t, _sigma2 = generate_case_yt(
        beta1=beta1,
        beta2=float(r3["beta2_ratio"]) * beta1,
        f1_hz=truth[0],
        f2_hz=truth[1],
        theta0_deg=float(r3["theta0_deg"]),
        n_c=int(controls["n_c"]),
        n_r=int(controls["n_r"]),
        t_pri_s=float(controls["t_pri_s"]),
        snr_db=snr_db,
        seed=seed,
    )
    result, evidence = evaluate_proposed(
        y_t,
        case_id=case_id,
        seed=seed,
        truth_pair_hz=truth,
        theta0_deg=float(r3["theta0_deg"]),
        controls=controls,
        grid_hz=grid,
    )
    comparators = evaluate_comparators(
        y_t, controls=controls, grid_hz=grid, f_weak_hz=truth[1]
    )
    expected = r3["expected"]
    observed = {
        "fft_weak": "detect" if comparators["fft_weak_detected"] else "fail",
        "music_weak": "detect" if comparators["music_weak_detected"] else "fail",
        "proposed": "resolves_both"
        if result.primary_hit and result.secondary_hit
        else "miss",
    }
    return {
        "section": "r3_fig7_row2",
        "snr_db": snr_db,
        "verdict": classify_case(result.primary_hit, result.secondary_hit),
        "expected": dict(expected),
        "observed": observed,
        "comparator_matrix_matches": observed == dict(expected),
        "comparator_detail": comparators,
        "cases": [result.to_dict()],
        "_case_map": {case_id: {"evidence": evidence, "result": result}},
    }


def run_audits(config: ControlsConfig) -> dict:
    """One-field audits. Recorded as diagnostics; they can NEVER upgrade a verdict."""
    controls = config.controls
    audits_cfg = controls["audits"]
    grid = _grid_from_controls(controls)
    r1 = controls["r1_fig8"]
    beta1 = _beta(r1["beta1"])
    truth = (float(r1["f_strong_hz"]), float(r1["f_weak_hz"]))
    root_seed = int(controls["root_seed"])
    audits: dict[str, object] = {}

    def eval_r1_variant(case_id: str, ratio: float, **kwargs) -> dict:
        seed = case_seed(root_seed, case_id)
        y_t, _sigma2 = generate_case_yt(
            beta1=beta1,
            beta2=ratio * beta1,
            f1_hz=truth[0],
            f2_hz=truth[1],
            theta0_deg=float(r1["theta0_deg"]),
            n_c=int(controls["n_c"]),
            n_r=int(controls["n_r"]),
            t_pri_s=float(controls["t_pri_s"]),
            snr_db=float(kwargs.pop("snr_db", controls["snr_db"])),
            seed=seed,
        )
        grid_local = kwargs.pop("grid_hz", grid)
        result, _evidence = evaluate_proposed(
            y_t,
            case_id=case_id,
            seed=seed,
            truth_pair_hz=truth,
            theta0_deg=float(r1["theta0_deg"]),
            controls=controls,
            grid_hz=grid_local,
            **kwargs,
        )
        return result.to_dict()

    ratios = [float(row["ratio"]) for row in r1["expected"]]

    # Ensemble covariance (Cases 1-4 oracle field): exact E{Y_t Y_t^H}.
    if audits_cfg.get("ensemble_covariance"):
        rows = []
        for ratio in ratios:
            case_id = f"audit:ensemble:ratio={ratio}"
            beta2 = ratio * beta1
            a1 = temporal_steering(
                np.array([truth[0]]), int(controls["n_c"]), float(controls["t_pri_s"])
            )[:, 0]
            a2 = temporal_steering(
                np.array([truth[1]]), int(controls["n_c"]), float(controls["t_pri_s"])
            )[:, 0]
            s = beta1 * a1 + beta2 * a2
            sigma2 = (abs(beta1) ** 2 + abs(beta2) ** 2) / (
                10.0 ** (float(controls["snr_db"]) / 10.0)
            )
            r_ens = np.outer(s, s.conj()) + sigma2 * np.eye(int(controls["n_c"]))
            seed = case_seed(root_seed, case_id)
            y_t, _ = generate_case_yt(
                beta1=beta1,
                beta2=beta2,
                f1_hz=truth[0],
                f2_hz=truth[1],
                theta0_deg=float(r1["theta0_deg"]),
                n_c=int(controls["n_c"]),
                n_r=int(controls["n_r"]),
                t_pri_s=float(controls["t_pri_s"]),
                snr_db=float(controls["snr_db"]),
                seed=seed,
            )
            result, _ = evaluate_proposed(
                y_t,
                case_id=case_id,
                seed=seed,
                truth_pair_hz=truth,
                theta0_deg=float(r1["theta0_deg"]),
                controls=controls,
                grid_hz=grid,
                covariance_override=r_ens,
            )
            rows.append(result.to_dict())
        audits["ensemble_covariance"] = rows

    for step in audits_cfg.get("grid_step_hz", []):
        rows = [
            eval_r1_variant(
                f"audit:grid_step={step}:ratio={ratio}",
                ratio,
                grid_hz=_grid_from_controls(controls, step_override=float(step)),
            )
            for ratio in ratios
        ]
        audits[f"grid_step_{step}"] = rows

    delta = audits_cfg.get("mean_removal_on_loading_delta")
    if delta is not None:
        audits["mean_removal_on"] = [
            eval_r1_variant(
                f"audit:mean_removal:ratio={ratio}",
                ratio,
                mean_removal_on=True,
                loading_delta=float(delta),
            )
            for ratio in ratios
        ]

    cancellation = audits_cfg.get("cancellation_ratio")
    if cancellation is not None:
        audits["cancellation_predicted_failure"] = [
            eval_r1_variant(
                f"audit:cancellation:ratio={cancellation}", float(cancellation)
            )
        ]

    # Comparator-rule audits (merge radius, MUSIC order) on the primary R1 data.
    comparator_audits = []
    for ratio in ratios:
        case_id = f"r1_fig8:ratio={ratio}"
        seed = case_seed(root_seed, case_id)
        y_t, _ = generate_case_yt(
            beta1=beta1,
            beta2=ratio * beta1,
            f1_hz=truth[0],
            f2_hz=truth[1],
            theta0_deg=float(r1["theta0_deg"]),
            n_c=int(controls["n_c"]),
            n_r=int(controls["n_r"]),
            t_pri_s=float(controls["t_pri_s"]),
            snr_db=float(controls["snr_db"]),
            seed=seed,
        )
        row = {"ratio": ratio}
        for radius in audits_cfg.get("merge_radius_hz", []):
            row[f"merge_radius_{radius}"] = evaluate_comparators(
                y_t, controls=controls, grid_hz=grid, f_weak_hz=truth[1],
                merge_radius_override_hz=float(radius),
            )
        for order in audits_cfg.get("music_model_order", []):
            row[f"music_p{order}"] = evaluate_comparators(
                y_t, controls=controls, grid_hz=grid, f_weak_hz=truth[1],
                music_order_override=int(order),
            )
        comparator_audits.append(row)
    audits["comparator_rules"] = comparator_audits

    for snr in audits_cfg.get("fig5_snr_db", []):
        audits[f"fig5_snr_{snr}"] = {
            k: v for k, v in run_r2(config, snr_override_db=float(snr)).items()
            if not k.startswith("_")
        }

    audits["note"] = "audits vary one field each and cannot upgrade any verdict"
    return audits


# ---------------------------------------------------------------------------------------
# Control 2 — the fixed-endpoint 4-RX ablation
# ---------------------------------------------------------------------------------------


def run_ablation(config: ControlsConfig) -> dict:
    """20 RX vs the fixed first-four-element subset, same realization, loaded solve."""
    controls = config.controls
    ablation = controls["ablation"]
    grid = _grid_from_controls(controls)
    root_seed = int(controls["root_seed"])
    endpoints = [int(v) for v in ablation["n_r_endpoints"]]
    deltas = [float(v) for v in ablation["loading_delta"]]
    rank_rtol = float(ablation["rank_rtol"])
    step_hz = float(controls["grid"]["step_hz"])

    r1 = controls["r1_fig8"]
    beta1 = _beta(r1["beta1"])
    case_specs = [
        {
            "case_id": f"ablation:r1:ratio={float(row['ratio'])}",
            "beta1": beta1,
            "beta2": float(row["ratio"]) * beta1,
            "truth": (float(r1["f_strong_hz"]), float(r1["f_weak_hz"])),
            "theta0_deg": float(r1["theta0_deg"]),
        }
        for row in r1["expected"]
    ] + [
        {
            "case_id": f"ablation:r2:{target['label']}",
            "beta1": _beta(target["beta1"]),
            "beta2": _beta(target["beta2"]),
            "truth": (float(target["f_pair_hz"][0]), float(target["f_pair_hz"][1])),
            "theta0_deg": float(target["theta0_deg"]),
        }
        for target in controls["r2_fig5"]["targets"]
    ]

    rows = []
    for spec in case_specs:
        seed = case_seed(root_seed, spec["case_id"])
        y_full, _sigma2 = generate_case_yt(
            beta1=spec["beta1"],
            beta2=spec["beta2"],
            f1_hz=spec["truth"][0],
            f2_hz=spec["truth"][1],
            theta0_deg=spec["theta0_deg"],
            n_c=int(controls["n_c"]),
            n_r=max(endpoints),
            t_pri_s=float(controls["t_pri_s"]),
            snr_db=float(controls["snr_db"]),
            seed=seed,
        )
        for n_r in endpoints:
            y_endpoint = y_full[:, :n_r]  # the fixed nested first-`n_r` subset
            covariance = sample_covariance(y_endpoint)
            eigvals = np.linalg.eigvalsh(covariance)[::-1]
            for delta in deltas:
                ctx = CaponContext.from_covariance(
                    covariance, loading_delta=delta, rank_rtol=rank_rtol
                )
                surface = joint_capon_surface(
                    ctx, grid, grid, n_c=int(controls["n_c"]),
                    t_pri_s=float(controls["t_pri_s"]),
                    condition_mask_threshold=float(controls["condition_mask_threshold"]),
                )
                try:
                    i, j = argmax_masked(surface.objective)
                    selected = (float(grid[i]), float(grid[j]))
                    hit = _pair_hit(selected, spec["truth"], step_hz)
                except AllMaskedError:
                    selected, hit = None, False
                rows.append(
                    {
                        "case_id": spec["case_id"],
                        "seed": seed,
                        "n_r": n_r,
                        "loading_delta": delta,
                        "rank": ctx.rank,
                        "eigvals_top8": [float(v) for v in eigvals[:8]],
                        "selected_hz": list(selected) if selected else None,
                        "peak_within_one_grid_step": hit,
                    }
                )

    # Whole-column phase-offset cancellation check at the 4-RX endpoint.
    spec = case_specs[0]
    seed = case_seed(root_seed, spec["case_id"])
    y_full, _ = generate_case_yt(
        beta1=spec["beta1"], beta2=spec["beta2"], f1_hz=spec["truth"][0],
        f2_hz=spec["truth"][1], theta0_deg=spec["theta0_deg"],
        n_c=int(controls["n_c"]), n_r=max(endpoints),
        t_pri_s=float(controls["t_pri_s"]), snr_db=float(controls["snr_db"]), seed=seed,
    )
    y4 = y_full[:, :4]
    phases = np.exp(1j * np.array([0.5, -1.2, 2.0, 0.9]))
    surf_a = joint_capon_surface(
        CaponContext.from_covariance(
            sample_covariance(y4), loading_delta=deltas[0], rank_rtol=rank_rtol
        ),
        grid, grid, n_c=int(controls["n_c"]), t_pri_s=float(controls["t_pri_s"]),
        condition_mask_threshold=float(controls["condition_mask_threshold"]),
    )
    surf_b = joint_capon_surface(
        CaponContext.from_covariance(
            sample_covariance(y4 * phases[None, :]), loading_delta=deltas[0],
            rank_rtol=rank_rtol,
        ),
        grid, grid, n_c=int(controls["n_c"]), t_pri_s=float(controls["t_pri_s"]),
        condition_mask_threshold=float(controls["condition_mask_threshold"]),
    )
    finite = np.isfinite(surf_a.objective)
    phase_invariance_max_rel_dev = float(
        np.max(
            np.abs(surf_b.objective[finite] - surf_a.objective[finite])
            / np.abs(surf_a.objective[finite])
        )
    )

    per_case_pass = {}
    for spec in case_specs:
        case_rows = [r for r in rows if r["case_id"] == spec["case_id"]]
        per_case_pass[spec["case_id"]] = all(r["peak_within_one_grid_step"] for r in case_rows)

    return {
        "section": "ablation_4rx",
        "endpoints": endpoints,
        "loading_deltas": deltas,
        "subset_rule": ablation["subset_rule"],
        "pass_rule": ablation["pass_rule"],
        "rows": rows,
        "per_case_pass": per_case_pass,
        "all_cases_pass": all(per_case_pass.values()),
        "phase_invariance_max_rel_dev": phase_invariance_max_rel_dev,
        "notes": [
            "slow-time = chirp index; mean removal off",
            "pooling is impossible here: one CPI exists, so there is nothing to pool",
            "role fixed in advance: arms cannot be relabelled; equal-amplitude failure "
            "is carried as expected-negative; COMPLETION (not outcome) gates the "
            "transfer bundle",
            "the unloaded inverse is never formed at the 4-RX endpoint",
        ],
    }


# ---------------------------------------------------------------------------------------
# Provenance / clean tree / run meta
# ---------------------------------------------------------------------------------------


def require_clean_tree(*, smoke: bool) -> list[str]:
    """Official runs refuse a dirty tree; smoke runs record it. Returns dirty paths."""
    dirty = sorted(git_status_paths(cwd=REPO_ROOT))
    if dirty and not smoke:
        raise RuntimeError(
            "official control runs require a clean git tree; found changed/untracked: "
            + ", ".join(dirty[:10])
            + (" ..." if len(dirty) > 10 else "")
            + " — commit first, or use --smoke for non-gating scratch"
        )
    return dirty


def assemble_run_meta(
    config: ControlsConfig,
    *,
    subcommand: str,
    smoke: bool,
    dirty_paths: list[str],
    case_seed_map: Mapping[str, Mapping[str, object]],
    output_hashes: Mapping[str, str],
    extra: Mapping[str, object] | None = None,
) -> dict:
    runtime_hashes = {}
    for rel in RUNTIME_RELPATHS:
        path = REPO_ROOT / rel
        runtime_hashes[rel] = sha256_path(path) if path.is_file() else None
    pdf_path = REPO_ROOT / PDF_RELPATH
    return {
        "schema_version": 1,
        "stage": f"m9_kotte_controls:{subcommand}",
        "smoke": bool(smoke),
        "non_gating": bool(smoke),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_text("rev-parse", "HEAD", cwd=REPO_ROOT),
        "git_branch": git_text("branch", "--show-current", cwd=REPO_ROOT),
        "git_tree_clean": not dirty_paths,
        "git_dirty_paths": dirty_paths,
        "config_whole_file_sha256": config.whole_file_sha256,
        "controls_section_hash": config.controls_section_hash,
        "resolved_controls": canonical_plain(config.controls),
        "runtime_source_hashes": runtime_hashes,
        "paper_pdf_sha256": sha256_path(pdf_path) if pdf_path.is_file() else None,
        "case_seed_map": {k: dict(v) for k, v in case_seed_map.items()},
        "output_hashes": dict(output_hashes),
        "environment": environment_attestation(),
        **dict(extra or {}),
    }


def run_paper_controls(
    config: ControlsConfig,
    *,
    sections: list[str],
    smoke: bool,
) -> tuple[dict, dict]:
    """Run the requested control sections. Returns (results, case_seed_map)."""
    results: dict[str, dict] = {}
    case_seed_map: dict[str, dict] = {}

    def harvest(section_result: dict) -> dict:
        case_map = section_result.pop("_case_map", {})
        for case_id, payload in case_map.items():
            result: CaseResult = payload["result"]
            case_seed_map[case_id] = {"seed": result.seed, "yt_sha256": result.yt_sha256}
        return section_result

    if "r1" in sections:
        results["r1"] = harvest(run_r1(config))
    if "r2" in sections:
        results["r2"] = harvest(run_r2(config))
    if "r3" in sections:
        results["r3"] = harvest(run_r3(config))
    if "audits" in sections:
        results["audits"] = run_audits(config)
    return results, case_seed_map


def write_json(path: Path, payload: Mapping[str, object]) -> str:
    """Strict JSON with LF-only bytes; returns the sha256 of the exact bytes."""
    data = strict_json_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return sha256_bytes(data)
