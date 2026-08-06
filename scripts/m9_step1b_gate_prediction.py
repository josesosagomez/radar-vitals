"""M9 Step 1b INDEPENDENT ORACLE — predictions before the gate exists.

Written and run **before** `src/m9/kotte_gate.py` (plans/m9_kotte_plan.md, step 4) so the
transfer gate's predictions P1-P6 are derived from physics and the paper's equations, not
read off the behaviour of the implementation they are meant to judge. It shares only
`src/m9/kotte_core.py`'s primitives; it does not import the gate.

Two independent models:

1. **Pinned Bessel comb** (`scipy.special.jv`). The chest phase is
   ``psi(t) = (4*pi/lambda_eff) * d(t)`` with
   ``d(t) = A_b sin(2 pi f_b t + phi_b) + A_h sin(2 pi f_h t + phi_h)``. Jacobi-Anger
   gives ``exp(j m sin(theta)) = sum_k J_k(m) exp(j k theta)``, so the complex baseband
   is a two-dimensional comb of lines at ``p*f_b + q*f_h`` with amplitude
   ``J_p(m_b) J_q(m_h) exp(j(p phi_b + q phi_h))``, ``m = 4 pi A / lambda_eff``.
   Coincident lines are combined **coherently** (they are the same frequency, so their
   complex amplitudes add and can cancel); lines beyond Nyquist are **folded**; the
   truncation order is chosen by a declared tail-mass criterion.

2. **Direct eq (25)/(26) evaluation** on noiseless analytic covariances built from that
   line spectrum, to predict which pair the selection line will choose.

Output: `results/m9/step1b/oracle/<stamp>/oracle.json` with the predicted P1-P6 outcomes,
the cancellation-critical phase pairs, the multi-CPI discrimination table, and a proposed
`transfer.gate_criteria` table to be reviewed and committed at the checkpoint.

Run:
  & 'C:\\ProgramData\\anaconda3\\condabin\\conda.bat' run -n radar-vitals python -X utf8 \\
      scripts/m9_step1b_gate_prediction.py
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys

import numpy as np
from scipy.special import jv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.m4.bundle import sha256_path  # noqa: E402
from src.m9.kotte_core import (  # noqa: E402
    CaponContext,
    alias_collapse,
    argmax_masked,
    pair_margin_db,
    signed_band_grid_hz,
    temporal_steering,
)
from src.m9.paper_control import (  # noqa: E402
    load_experiment_config,
    write_json,
)

#: Truncation is declared, not tuned: orders grow until the retained squared amplitude
#: covers this fraction of the total comb power.
TAIL_MASS_RETAINED = 0.9999
MAX_ORDER = 60


def bessel_comb(
    *,
    m_b: float,
    m_h: float,
    f_b_hz: float,
    f_h_hz: float,
    phi_b_rad: float,
    phi_h_rad: float,
    fs_hz: float,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Folded, coherently-combined line spectrum of ``exp(j psi(t))``.

    Returns ``(frequencies_hz, complex_amplitudes, diagnostics)`` sorted by frequency.
    """
    p_order = _truncation_order(m_b)
    q_order = _truncation_order(m_h)
    p = np.arange(-p_order, p_order + 1)
    q = np.arange(-q_order, q_order + 1)
    jb = jv(p, m_b)
    jh = jv(q, m_h)
    amplitude = np.outer(jb, jh) * np.exp(
        1j * (np.outer(p * phi_b_rad, np.ones_like(q)) + np.outer(np.ones_like(p), q * phi_h_rad))
    )
    frequency = np.add.outer(p * f_b_hz, q * f_h_hz)

    freq_flat = frequency.ravel()
    amp_flat = amplitude.ravel()
    # Fold onto [-fs/2, fs/2): sampling makes f and f + k*fs indistinguishable.
    folded = (freq_flat + fs_hz / 2.0) % fs_hz - fs_hz / 2.0
    # Combine coincident lines COHERENTLY (they are one line; amplitudes may cancel).
    keys = np.round(folded, 9)
    order = np.argsort(keys, kind="stable")
    keys_sorted = keys[order]
    amp_sorted = amp_flat[order]
    unique_freqs, starts = np.unique(keys_sorted, return_index=True)
    combined = np.add.reduceat(amp_sorted, starts)
    total_power = float(np.sum(np.abs(amp_flat) ** 2))
    diagnostics = {
        "p_order": int(p_order),
        "q_order": int(q_order),
        "n_lines_before_folding": int(freq_flat.size),
        "n_lines_after_combination": int(unique_freqs.size),
        "retained_power_fraction": float(
            np.sum(np.abs(combined) ** 2) / total_power
        ) if total_power > 0 else 0.0,
        "modulation_index_breath_rad": float(m_b),
        "modulation_index_heart_rad": float(m_h),
    }
    return unique_freqs, combined, diagnostics


def _truncation_order(m: float) -> int:
    """Smallest K with sum_{|k|<=K} J_k(m)^2 >= TAIL_MASS_RETAINED (total is 1)."""
    for k in range(1, MAX_ORDER + 1):
        orders = np.arange(-k, k + 1)
        if float(np.sum(jv(orders, m) ** 2)) >= TAIL_MASS_RETAINED:
            return k
    return MAX_ORDER


def signal_vector(
    freqs_hz: np.ndarray,
    amps: np.ndarray,
    *,
    n_c: int,
    t_pri_s: float,
    remove_dc: bool,
) -> np.ndarray:
    """The COHERENT slow-time signal ``v[n] = sum_i a_i exp(j 2 pi f_i n T_PRI)``.

    Coherent, not ``sum |a_i|^2``: the comb lines have deterministic relative phases and
    are free to reinforce or cancel. Summing their powers would erase precisely the
    phase dependence P5 exists to predict.

    ``remove_dc`` models the pipeline's per-RX mean removal over the retained support,
    which deletes the ``p=q=0`` line (and with it any static clutter).
    """
    freqs = np.asarray(freqs_hz)
    amps = np.asarray(amps)
    if remove_dc:
        keep = np.abs(freqs) > 1e-9
        freqs, amps = freqs[keep], amps[keep]
    steering = temporal_steering(freqs, n_c, t_pri_s)   # (n_c, n_lines)
    return steering @ amps


def sample_covariance_from_comb(
    freqs_hz: np.ndarray,
    amps: np.ndarray,
    *,
    n_c: int,
    t_pri_s: float,
    snr_db: float,
    remove_dc: bool,
    rx_gains: np.ndarray,
    seed: int,
) -> tuple[np.ndarray, float]:
    """One CPI's **sample** covariance, formed exactly as the pipeline forms it.

    Deliberately NOT the ensemble ``v v^H + sigma^2 I``. With only ``n_R = 4`` snapshots
    for ``N_c = 16`` dimensions, the sample covariance is nowhere near its ensemble
    limit — that gap is the rank landmine, and predicting the estimator means modelling
    what it actually sees. Sherman-Morrison on the ensemble form shows the objective
    varies only through ``|v^H a|^2 / (sigma^2 + ||v||^2)``, which a spread comb keeps
    far below ``N_c``, giving an almost flat surface and a meaningless argmax.

    ``Y = v g^T + noise`` with ``g`` the per-RX complex gains; ``R = Y Y^H / n_R``.
    """
    v = signal_vector(freqs_hz, amps, n_c=n_c, t_pri_s=t_pri_s, remove_dc=remove_dc)
    v_dynamic = signal_vector(freqs_hz, amps, n_c=n_c, t_pri_s=t_pri_s, remove_dc=True)
    dynamic_power = float(np.mean(np.abs(v_dynamic) ** 2))
    noise_power = dynamic_power / (10.0 ** (float(snr_db) / 10.0))
    rng = np.random.default_rng(seed)
    n_r = rx_gains.size
    noise = np.sqrt(noise_power / 2.0) * (
        rng.standard_normal((n_c, n_r)) + 1j * rng.standard_normal((n_c, n_r))
    )
    y_t = np.outer(v, rx_gains) + noise
    return (y_t @ y_t.conj().T) / n_r, noise_power


def predict_selection(
    freqs_hz: np.ndarray,
    amps: np.ndarray,
    *,
    config_stage_a: dict,
    n_c: int,
    t_pri_s: float,
    loading_delta: float,
    snr_db: float,
    rx_gains: np.ndarray,
    seeds: list[int],
    remove_dc: bool = True,
) -> dict:
    """Predicted (|f1|, |f2|) selection, reported across the declared seeds.

    Multiple seeds because the per-CPI sample covariance is a random object: a
    single-seed "prediction" would be a coin flip dressed as physics. The reported
    prediction is the modal pair with its hit fraction.
    """
    step_hz = float(config_stage_a["grid_step_bpm"]) / 60.0
    f1_grid = signed_band_grid_hz(config_stage_a["breath_band_hz"], step_hz)
    f2_grid = signed_band_grid_hz(config_stage_a["heart_band_hz"], step_hz)
    from src.m9.kotte_core import joint_capon_surface

    per_seed = []
    for seed in seeds:
        covariance, noise_power = sample_covariance_from_comb(
            freqs_hz, amps, n_c=n_c, t_pri_s=t_pri_s, snr_db=snr_db,
            remove_dc=remove_dc, rx_gains=rx_gains, seed=seed,
        )
        ctx = CaponContext.from_covariance(
            covariance,
            loading_delta=loading_delta,
            rank_rtol=float(config_stage_a["rank_rtol"]),
        )
        surface = joint_capon_surface(
            ctx, f1_grid, f2_grid, n_c=n_c, t_pri_s=t_pri_s,
            condition_mask_threshold=float(config_stage_a["condition_mask_threshold"]),
        )
        collapsed = alias_collapse(surface.objective, f1_grid, f2_grid)
        i, j = argmax_masked(collapsed.objective)
        per_seed.append(
            {
                "seed": int(seed),
                "br_bpm": float(collapsed.abs_f1_hz[i] * 60.0),
                "hr_bpm": float(collapsed.abs_f2_hz[j] * 60.0),
                "pair_margin_db": float(
                    pair_margin_db(collapsed.objective, [(i, j)], exclusion_steps=1)
                ),
                "rank": int(ctx.rank),
                "noise_power": float(noise_power),
            }
        )
    br_values = np.array([r["br_bpm"] for r in per_seed])
    hr_values = np.array([r["hr_bpm"] for r in per_seed])
    pairs = [(r["br_bpm"], r["hr_bpm"]) for r in per_seed]
    modal_pair = max(set(pairs), key=pairs.count)
    return {
        "per_seed": per_seed,
        "modal_br_bpm": float(modal_pair[0]),
        "modal_hr_bpm": float(modal_pair[1]),
        "modal_fraction": float(pairs.count(modal_pair) / len(pairs)),
        "br_median_bpm": float(np.median(br_values)),
        "hr_median_bpm": float(np.median(hr_values)),
        "br_spread_bpm": float(br_values.max() - br_values.min()),
        "hr_spread_bpm": float(hr_values.max() - hr_values.min()),
        "median_pair_margin_db": float(
            np.median([r["pair_margin_db"] for r in per_seed])
        ),
        "rank": int(per_seed[0]["rank"]),
        "dc_removed": bool(remove_dc),
    }


def band_line_table(
    freqs_hz: np.ndarray, amps: np.ndarray, band_hz: tuple[float, float], top: int = 6
) -> list[dict]:
    """The strongest comb lines inside a band — the decoy inventory."""
    lo, hi = float(band_hz[0]), float(band_hz[1])
    inside = (np.abs(freqs_hz) >= lo) & (np.abs(freqs_hz) <= hi)
    idx = np.argsort(np.abs(amps[inside]))[::-1][:top]
    return [
        {
            "freq_hz": float(np.abs(freqs_hz[inside][k])),
            "bpm": float(np.abs(freqs_hz[inside][k]) * 60.0),
            "abs_amplitude": float(np.abs(amps[inside][k])),
        }
        for k in idx
    ]


def run_oracle(config, *, output_root: Path) -> dict:
    transfer = config.raw["transfer"]
    stage_a = config.raw["stage_a"]
    generator = transfer["generator"]
    fs_hz = float(generator["fs_hz"])
    t_pri_s = 1.0 / fs_hz
    lam_eff = _lambda_eff()
    snr_db = float(generator["snr_z_dynamic_db"])
    phase_pairs = [tuple(float(v) for v in pair) for pair in generator["phase_pairs_rad"]]
    primary_arm = next(
        a for a in stage_a["arms"] if a["arm_id"] == stage_a["primary_arm_id"]
    )
    n_c = int(primary_arm["n_c"])
    loading_delta = float(primary_arm["loading_delta"])
    rx_gains = np.asarray(generator["rx_gain_table"], dtype=np.float64) * np.exp(
        1j * np.asarray(generator["rx_phase_table_rad"], dtype=np.float64)
    )
    seeds = [int(s) for s in transfer["robustness_seeds"]]

    scenarios_out = []
    for scenario in transfer["scenarios"]:
        m_b = 4.0 * np.pi * float(scenario["breath_amplitude_m"]) / lam_eff
        m_h = 4.0 * np.pi * float(scenario["heart_amplitude_m"]) / lam_eff
        per_phase = []
        for phi_b, phi_h in phase_pairs:
            freqs, amps, diag = bessel_comb(
                m_b=m_b, m_h=m_h,
                f_b_hz=float(scenario["f_breath_hz"]),
                f_h_hz=float(scenario["f_heart_hz"]),
                phi_b_rad=phi_b, phi_h_rad=phi_h, fs_hz=fs_hz,
            )
            selection = predict_selection(
                freqs, amps, config_stage_a=stage_a, n_c=n_c, t_pri_s=t_pri_s,
                loading_delta=loading_delta, snr_db=snr_db,
                rx_gains=rx_gains, seeds=seeds,
            )
            # P1's pair: the same case with the DC/clutter line RETAINED.
            selection_dc_kept = predict_selection(
                freqs, amps, config_stage_a=stage_a, n_c=n_c, t_pri_s=t_pri_s,
                loading_delta=loading_delta, snr_db=snr_db,
                rx_gains=rx_gains, seeds=seeds, remove_dc=False,
            )
            heart_lines = band_line_table(freqs, amps, tuple(stage_a["heart_band_hz"]))
            true_hr_bpm = float(scenario["f_heart_hz"]) * 60.0
            true_br_bpm = float(scenario["f_breath_hz"]) * 60.0
            per_phase.append(
                {
                    "phi_b_rad": phi_b,
                    "phi_h_rad": phi_h,
                    "comb": diag,
                    "selection": selection,
                    "selection_dc_retained": selection_dc_kept,
                    "heart_band_lines": heart_lines,
                    "hr_error_bpm": abs(selection["modal_hr_bpm"] - true_hr_bpm),
                    "br_error_bpm": abs(selection["modal_br_bpm"] - true_br_bpm),
                    "hr_median_error_bpm": abs(selection["hr_median_bpm"] - true_hr_bpm),
                    "dominant_heart_band_line_is_true_hr": bool(
                        heart_lines
                        and abs(heart_lines[0]["bpm"] - true_hr_bpm) < 1e-6
                    ),
                }
            )
        scenarios_out.append(
            {
                "scenario_id": scenario["scenario_id"],
                "true_br_bpm": float(scenario["f_breath_hz"]) * 60.0,
                "true_hr_bpm": float(scenario["f_heart_hz"]) * 60.0,
                "modulation_index_breath_rad": float(m_b),
                "modulation_index_heart_rad": float(m_h),
                "per_phase": per_phase,
            }
        )

    payload = {
        "schema_version": 1,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "role": "independent_oracle_written_before_kotte_gate",
        "lambda_eff_m": lam_eff,
        "bessel_model": {
            "identity": "exp(j m sin(theta)) = sum_k J_k(m) exp(j k theta)",
            "tail_mass_retained": TAIL_MASS_RETAINED,
            "coincident_lines": "combined coherently",
            "aliasing": "folded onto [-fs/2, fs/2)",
            "implementation": "scipy.special.jv",
        },
        "snr_z_dynamic_db": snr_db,
        "scenarios": scenarios_out,
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    out_dir = Path(output_root) / stamp
    payload["oracle_sha256_of_config"] = config.whole_file_sha256
    write_json(out_dir / "oracle.json", payload)
    print(f"[m9-oracle] wrote {out_dir}")
    return payload


def _lambda_eff() -> float:
    """Effective wavelength from the committed hardware profile (never hardcoded)."""
    import yaml

    live = yaml.safe_load(
        (REPO_ROOT / "scripts" / "live_demo_config.yaml").read_text(encoding="utf-8")
    )
    hw = live["hw_profile"]
    start_hz = float(hw["start_freq_ghz"]) * 1e9
    slope_hz_per_s = float(hw["freq_slope_mhz_us"]) * 1e12
    adc_start_s = float(hw["adc_start_time_us"]) * 1e-6
    fs_adc = float(hw["dig_out_sample_rate"]) * 1e3
    n_adc = int(hw["num_adc_samples"])
    centre_time_s = adc_start_s + (n_adc / fs_adc) / 2.0
    f_eff = start_hz + slope_hz_per_s * centre_time_s
    return 299_792_458.0 / f_eff


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root", type=Path, default=REPO_ROOT / "results" / "m9" / "step1b" / "oracle"
    )
    args = parser.parse_args()
    config = load_experiment_config()
    payload = run_oracle(config, output_root=args.output_root)

    print("\n[m9-oracle] predicted selection on the analytic (noiseless-ensemble) model:")
    for scenario in payload["scenarios"]:
        print(
            f"\n  {scenario['scenario_id']}  "
            f"(true BR {scenario['true_br_bpm']:.1f}, HR {scenario['true_hr_bpm']:.1f} bpm; "
            f"m_b={scenario['modulation_index_breath_rad']:.2f} rad)"
        )
        for entry in scenario["per_phase"]:
            sel = entry["selection"]
            print(
                f"    phases ({entry['phi_b_rad']:.2f}, {entry['phi_h_rad']:.2f}): "
                f"BR {sel['modal_br_bpm']:6.1f} (err {entry['br_error_bpm']:5.1f}, "
                f"spread {sel['br_spread_bpm']:5.1f})  "
                f"HR {sel['modal_hr_bpm']:6.1f} (err {entry['hr_error_bpm']:5.1f}, "
                f"spread {sel['hr_spread_bpm']:5.1f})  "
                f"modal {sel['modal_fraction']:.0%}  "
                f"margin {sel['median_pair_margin_db']:5.2f} dB  "
                f"trueHRdom={entry['dominant_heart_band_line_is_true_hr']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
