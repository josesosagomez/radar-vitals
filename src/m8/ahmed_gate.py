"""Synthetic gate evaluation for M8 Step 1b (Addendum A section A4.2).

Two verdicts, deliberately separated:

* **Gate verdict** — the sole control on real-data eligibility. Passes only when the
  implementation reproduces the plan-declared predictions P1-P4 across **both** candidate
  domains. Because P1-P4 include the predicted *failures*, the gate cannot be made to pass
  by choosing a favourable candidate band: a gate that tests whether the method *works*
  can be gamed by narrowing the domain; one that tests whether the code matches the
  mathematics cannot.
* **Transfer verdict** — the scientific result. Truth recovery per domain, reported and
  **non-gating**.

`complete` never implies success, and a positive result supports only this declared seed
and configuration. It is not evidence of robustness or of general phase-model transfer.

The P1 predictor here works from the *line model* — which bins carry signal and how much —
and never touches the FFT, so it is independent of the path it checks.
`scripts/m8_step1b_gate_prediction.py` is a third, separately written implementation;
`tests/test_m8_ahmed_gate.py` cross-checks all three.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Mapping

import numpy as np

from src.m8.ahmed_synthetic import SyntheticConfig, generate
from src.m8.ahmed_transfer import (
    COLLISION_DOMAIN_FROM_FB,
    REAL_REPRESENTATIVE_DOMAIN,
    AhmedPhaseConfig,
    CandidateDomain,
    arm_id_for,
    estimate_phase_ha,
)

__all__ = [
    "DEGENERACY_REL_TOL",
    "GATE_DOMAINS",
    "NON_DIVISOR_REL_TO_PEAK",
    "GateReport",
    "PredictionCheck",
    "evaluate_gate",
    "predict_selected_bin",
]

UNSUPPRESSED = "figure_visible_unsuppressed"

#: Both domains are evaluated. Neither gates on its own (Addendum A section A3).
GATE_DOMAINS: tuple[CandidateDomain, ...] = (
    COLLISION_DOMAIN_FROM_FB,
    REAL_REPRESENTATIVE_DOMAIN,
)

# P2/P3 are exact in exact arithmetic; the rFFT introduces round-off. Observed on the real
# 30 s grid: 2.0e-15 relative for P2, 6e-15 for the ratios, and 8.8e-16 of the peak for a
# non-divisor candidate. These tolerances are ~1000x that — robust to a different BLAS,
# still ~9 orders below any real spectral line. Full values: Addendum A section A4.1.
DEGENERACY_REL_TOL = 1e-12
NON_DIVISOR_REL_TO_PEAK = 1e-12

GateStatus = Literal["passed", "failed"]
TransferStatus = Literal[
    "transferred_under_declared_seed_and_configuration",
    "not_transferred_under_declared_assumptions",
]


@dataclass(frozen=True)
class PredictionCheck:
    prediction_id: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class GateReport:
    model_id: str
    synthetic_config_hash: str
    gate_status: GateStatus
    checks: tuple[PredictionCheck, ...]
    selection_table: tuple[dict, ...]
    transfer_by_domain: Mapping[str, TransferStatus]

    @property
    def promotion_inputs_complete(self) -> bool:
        return bool(self.checks) and bool(self.selection_table)


def predict_selected_bin(
    line_bins: Mapping[int, float],
    candidate_bins: np.ndarray,
    harmonics: int,
    n_fft: int,
) -> int | None:
    """Predict the selection from the line model alone, without any FFT.

    A candidate `d` captures a line at bin `b` when `b` is a multiple of `d` within the
    first `H` harmonics. The accumulated mean is the captured magnitude sum over `H`, so
    exact integer divisors of a line's bin score identically to the line's own bin — the
    degeneracy behind P2. Ties resolve to the lowest bin, matching Step 1a's rule.
    """
    supported = [
        int(d) for d in candidate_bins if int(d) > 0 and int(d) * harmonics < n_fft / 2.0
    ]
    if not supported:
        return None
    best_bin, best_score = None, -np.inf
    for d in sorted(supported):
        captured = sum(
            magnitude
            for bin_index, magnitude in line_bins.items()
            if bin_index % d == 0 and 1 <= bin_index // d <= harmonics
        )
        score = captured / harmonics
        if score > best_score:  # strict: first (lowest) bin wins a tie
            best_bin, best_score = d, score
    return best_bin


def _line_model(config: SyntheticConfig, n_fft: int, spectrum: np.ndarray) -> dict[int, float]:
    """Map each declared displacement fundamental to its bin and observed magnitude."""
    resolution = config.prf_hz / n_fft
    lines = {}
    for frequency in (config.breath_hz, config.heart_hz):
        bin_index = int(round(frequency / resolution))
        lines[bin_index] = float(spectrum[bin_index])
    return lines


def _check_p1_p4(
    config: SyntheticConfig,
    clean_result,
    spectrum: np.ndarray,
    n_fft: int,
    domain: CandidateDomain,
    harmonics: int,
) -> tuple[bool, str, dict]:
    lines = _line_model(config, n_fft, spectrum)
    record = {"domain_id": domain.domain_id, "harmonic_count": harmonics}
    ok = True
    details = []
    arm = clean_result.arm_native_results[arm_id_for(harmonics, UNSUPPRESSED)]
    for vital, evidence_key, selected_key in (
        ("breath", "breath_evidence", "br_selected_bin"),
        ("heart", "heart_evidence", "hr_selected_bin"),
    ):
        evidence = arm[evidence_key]
        predicted = predict_selected_bin(
            lines, np.asarray(evidence["candidate_bins"]), harmonics, n_fft
        )
        observed = arm[selected_key]
        record[f"{vital}_predicted_bin"] = predicted
        record[f"{vital}_observed_bin"] = observed
        record[f"{vital}_observed_bpm"] = (
            None if observed is None else 60.0 * observed * config.prf_hz / n_fft
        )
        if predicted != observed:
            ok = False
            details.append(f"{vital}: predicted bin {predicted}, observed {observed}")
    return ok, "; ".join(details) or "all selections match the line-model prediction", record


def _check_p2_p3(
    config: SyntheticConfig, clean_result, spectrum: np.ndarray, n_fft: int, harmonics: int
) -> list[PredictionCheck]:
    """P2/P3 on the collision domain, where f_h, f_h/2, and f_b are all in band."""
    resolution = config.prf_hz / n_fft
    heart_bin = int(round(config.heart_hz / resolution))
    breath_bin = int(round(config.breath_hz / resolution))
    arm = clean_result.arm_native_results[arm_id_for(harmonics, UNSUPPRESSED)]
    evidence = arm["heart_evidence"]
    scores = {
        int(b): float(s)
        for b, s in zip(evidence["candidate_bins"], evidence["scores"])
        if np.isfinite(s)
    }
    checks: list[PredictionCheck] = []

    sub_bin = heart_bin // 2
    if heart_bin in scores and sub_bin in scores and heart_bin % 2 == 0:
        rel = abs(scores[heart_bin] - scores[sub_bin]) / scores[heart_bin]
        checks.append(
            PredictionCheck(
                f"P2_subharmonic_degeneracy_h{harmonics}",
                rel <= DEGENERACY_REL_TOL,
                f"rel_diff={rel:.3e} (tol {DEGENERACY_REL_TOL:.0e})",
            )
        )

    if breath_bin in scores and heart_bin in scores:
        ratio = scores[breath_bin] / scores[heart_bin]
        # q=f_b reaches f_h only when heart_bin/breath_bin <= H: with H=3 the candidate
        # captures breathing alone (ratio d_b/d_h = 2); with H=5 it also captures the
        # heart line at the fourth harmonic (ratio (d_b + d_h)/d_h = 3).
        expected = 2.0 if heart_bin // breath_bin > harmonics else 3.0
        rel = abs(ratio - expected) / expected
        checks.append(
            PredictionCheck(
                f"P3_collision_ratio_h{harmonics}",
                rel <= DEGENERACY_REL_TOL,
                f"ratio={ratio!r} expected={expected} rel_diff={rel:.3e}",
            )
        )

    peak = float(spectrum.max())
    non_divisors = [
        b for b in scores if heart_bin % b != 0 and breath_bin % b != 0
    ]
    if non_divisors and peak > 0:
        worst = max(scores[b] for b in non_divisors) / peak
        checks.append(
            PredictionCheck(
                f"P3_non_divisor_carries_no_signal_h{harmonics}",
                worst < NON_DIVISOR_REL_TO_PEAK,
                f"max_rel_to_peak={worst:.3e} (tol {NON_DIVISOR_REL_TO_PEAK:.0e})",
            )
        )
    return checks


def _transfer_status(
    config: SyntheticConfig, noisy_result, n_fft: int, harmonic_counts: tuple[int, ...]
) -> TransferStatus:
    """Base plan section 2.4's four truth-recovery checks, on the unsuppressed arms."""
    duration_s = config.sample_count / config.prf_hz
    tolerance_bpm = 60.0 / duration_s
    for harmonics in harmonic_counts:
        arm = noisy_result.arm_native_results[arm_id_for(harmonics, UNSUPPRESSED)]
        for value, truth, valid in (
            (arm["br_bpm"], 60.0 * config.breath_hz, arm["br_valid"]),
            (arm["hr_raw"], 60.0 * config.heart_hz, arm["hr_valid"]),
        ):
            if not valid or value is None:
                return "not_transferred_under_declared_assumptions"
            if abs(value - truth) > tolerance_bpm:
                return "not_transferred_under_declared_assumptions"
    return "transferred_under_declared_seed_and_configuration"


def lines_are_on_grid(config: SyntheticConfig, n_fft: int, *, tol: float = 1e-9) -> bool:
    """True when both declared fundamentals fall exactly on rFFT bin centres.

    P2 and P3 are identities about *which bins a harmonic row lands on*. They hold to
    round-off only when each line occupies a single bin. On the primary PRF grid the lines
    are off-grid — f_b sits at 4.9976 bins — so leakage spreads each line across
    neighbours and the identities degrade to roughly 1e-3. The 20 Hz grids place f_b and
    f_h exactly on bins 5/20 (300 samples) or 10/40 (600 samples).
    """
    resolution = config.prf_hz / n_fft
    return all(
        abs(frequency / resolution - round(frequency / resolution)) < tol
        for frequency in (config.breath_hz, config.heart_hz)
    )


def _evaluate_realization(
    config: SyntheticConfig, *, evaluate_p2_p3: bool
) -> tuple[list[PredictionCheck], list[dict], dict[str, TransferStatus]]:
    realization = generate(config)
    clean = np.asarray(realization.clean_phase)
    noisy = np.asarray(realization.extracted_phase)
    n_fft = int(config.n_fft) if config.n_fft is not None else int(noisy.size)
    spectrum = np.abs(np.fft.rfft(clean, n=n_fft))
    on_grid = lines_are_on_grid(config, n_fft)

    checks: list[PredictionCheck] = []
    table: list[dict] = []
    transfer: dict[str, TransferStatus] = {}
    tag = f"fs{config.prf_hz:.6g}"

    for domain in GATE_DOMAINS:
        phase_config = AhmedPhaseConfig(domain=domain, n_fft=config.n_fft)
        clean_result = estimate_phase_ha(clean, config.prf_hz, phase_config)
        noisy_result = estimate_phase_ha(noisy, config.prf_hz, phase_config)

        for harmonics in phase_config.harmonic_counts:
            ok, detail, record = _check_p1_p4(
                config, clean_result, spectrum, n_fft, domain, harmonics
            )
            checks.append(
                PredictionCheck(
                    f"P1_P4_selection_{tag}_{domain.domain_id}_h{harmonics}", ok, detail
                )
            )
            noisy_arm = noisy_result.arm_native_results[arm_id_for(harmonics, UNSUPPRESSED)]
            record["grid"] = tag
            record["lines_on_grid"] = on_grid
            record["noisy_breath_bpm"] = noisy_arm["br_bpm"]
            record["noisy_heart_bpm"] = noisy_arm["hr_raw"]
            table.append(record)

            if evaluate_p2_p3 and domain is COLLISION_DOMAIN_FROM_FB:
                if not on_grid:
                    raise ValueError(
                        "P2/P3 require on-grid lines; refusing to evaluate them on "
                        f"{tag}, where the declared fundamentals are off-grid"
                    )
                checks.extend(_check_p2_p3(config, clean_result, spectrum, n_fft, harmonics))

        transfer[domain.domain_id] = _transfer_status(
            config, noisy_result, n_fft, phase_config.harmonic_counts
        )
    return checks, table, transfer


def evaluate_gate(config: SyntheticConfig | None = None) -> GateReport:
    """Run the synthetic control and evaluate both verdicts.

    P1/P4 are checked on every grid. P2/P3 are checked only on the on-grid 20 Hz
    realization, where they are mathematically well-posed; that realization is an
    implementation identity check and never an upgrade or downgrade of the primary-PRF
    **transfer** verdict, which base plan section 2.4 reserves to the primary grid.
    """
    config = config or SyntheticConfig()
    realization = generate(config)

    checks, table, transfer = _evaluate_realization(config, evaluate_p2_p3=False)

    on_grid_config = replace(config, prf_hz=20.0)
    grid_checks, grid_table, _grid_transfer = _evaluate_realization(
        on_grid_config, evaluate_p2_p3=True
    )
    checks.extend(grid_checks)
    table.extend(grid_table)

    status: GateStatus = "passed" if all(c.passed for c in checks) else "failed"
    return GateReport(
        model_id="phase_fundamentals_only_transfer_v1",
        synthetic_config_hash=realization.config_hash,
        gate_status=status,
        checks=tuple(checks),
        selection_table=tuple(table),
        transfer_by_domain=transfer,
    )
