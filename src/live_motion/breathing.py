"""Demo-only 60-second respiratory activity assessment; never an ECA input.

The development plan defines the decision rules. Spectral peak selection reuses
the unchanged production helpers. Full-window Fourier projections are distinct
from the Hann-windowed peak spectrum. Quiet is an observation of radar motion,
not a diagnosis of apnea. No detector thresholds are supplied by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

import numpy as np
from scipy.fft import rfft, irfft

from src.respiration import _detrend, _is_local_max, fft_estimate_rr, ha_estimate_rr

FRAME_RATE_HZ = 20.0
WINDOW_FRAMES = 1200
BAND_HZ = (0.05, 0.50)
# Numerical allowance only: 64 float64 epsilons, not a fitted signal threshold.
COHERENCE_ROUNDOFF_TOL = 64 * np.finfo(np.float64).eps


@dataclass(frozen=True)
class BreathingAssessment:
    state: str
    value_bpm: float
    reason: str
    evidence: dict


def persistence_score(respiratory_projection: np.ndarray, candidate_hz: float,
                      fs: float = FRAME_RATE_HZ) -> dict:
    """Energy explained by one fixed full-window candidate in both halves.

    Modified Gram-Schmidt uses centered float64 bases and dot products. The
    relative norm guard detects numerical rank loss; it is not a quality gate.
    No frequency is selected independently in either half.
    """
    y = np.asarray(respiratory_projection, dtype=np.float64)
    evidence = {
        "persistence": float("nan"), "persistence_valid": False,
        "persistence_evaluated": True,
        "persistence_reason": "invalid_input",
        "persistence_half_scores": np.full(2, np.nan),
        "persistence_half_energy": np.full(2, np.nan),
        "persistence_basis_norms": np.full((2, 2), np.nan),
        "persistence_coefficients": np.full((2, 2), np.nan),
        "persistence_bases": np.empty((0, 2, 0), dtype=np.float64),
        "persistence_centered_halves": np.empty((0, 0), dtype=np.float64),
        "persistence_roundoff_tolerance": COHERENCE_ROUNDOFF_TOL,
    }
    if (y.ndim != 1 or len(y) < 4 or len(y) % 2 or not np.isfinite(y).all()
            or not np.isfinite(candidate_hz) or not np.isfinite(fs) or fs <= 0):
        return evidence
    half = len(y) // 2
    centered_halves = np.empty((2, half), dtype=np.float64)
    bases = np.empty((2, 2, half), dtype=np.float64)
    evidence["persistence_bases"] = bases
    evidence["persistence_centered_halves"] = centered_halves
    # Initialize in case a later numerical rejection leaves a partial calculation.
    bases.fill(np.nan)
    centered_halves.fill(np.nan)
    for h in range(2):
        section = y[h * half:(h + 1) * half]
        centered = section - section.mean()
        centered_halves[h] = centered
        denominator = float(np.dot(centered, centered))
        evidence["persistence_half_energy"][h] = denominator
        if not np.isfinite(denominator) or denominator <= 0:
            evidence["persistence_reason"] = "nonpositive_signal_energy"
            return evidence
        t = (np.arange(half, dtype=np.float64) + h * half) / fs
        candidates = (np.sin(2 * np.pi * candidate_hz * t),
                      np.cos(2 * np.pi * candidate_hz * t))
        q_columns = []
        for j, base in enumerate(candidates):
            v = base - base.mean()
            initial_energy = float(np.dot(v, v))
            for q in q_columns:
                v = v - float(np.dot(q, v)) * q
            norm_sq = float(np.dot(v, v))
            evidence["persistence_basis_norms"][h, j] = norm_sq
            rank_floor = np.finfo(np.float64).eps ** 2 * initial_energy
            if (not np.isfinite(norm_sq) or not np.isfinite(initial_energy)
                    or initial_energy <= 0 or norm_sq <= rank_floor):
                evidence["persistence_reason"] = "degenerate_basis"
                return evidence
            q = v / np.sqrt(norm_sq)
            q_columns.append(q)
            bases[h, j] = q
            evidence["persistence_coefficients"][h, j] = float(np.dot(q, centered))
        coefficients = evidence["persistence_coefficients"][h]
        score = float(np.dot(coefficients, coefficients) / denominator)
        if not np.isfinite(score) or score < -COHERENCE_ROUNDOFF_TOL or score > 1 + COHERENCE_ROUNDOFF_TOL:
            evidence["persistence_reason"] = "energy_fraction_out_of_bounds"
            return evidence
        evidence["persistence_half_scores"][h] = np.clip(score, 0.0, 1.0)
    evidence["persistence"] = float(np.min(evidence["persistence_half_scores"]))
    evidence["persistence_valid"] = True
    evidence["persistence_reason"] = "passed"
    return evidence


def measure_breathing(phase: np.ndarray, fs: float = FRAME_RATE_HZ) -> dict:
    """Threshold-free primitives for calibration and the runtime decision.

    All fields are primitive scalars/arrays, serializable without pickle. Scores
    with no peak use the plan's explicit 0-dB FFT / 0-linear-HA baselines.
    """
    x = np.asarray(phase, dtype=np.float64)
    out = {
        "valid": False, "reason": "ineligible_window", "phase": x.copy(),
        "detrended": np.empty(0), "linear_trend": np.empty(0),
        "respiratory_projection": np.empty(0), "subband_projection": np.empty(0),
        "respiratory_block_rms": np.full(6, np.nan),
        "subband_block_rms": np.full(6, np.nan), "drift_rms": float("nan"),
        "fft_score": 0.0, "ha_score": 0.0, "persistence": float("nan"),
        "persistence_valid": False, "persistence_reason": "no_candidate",
        "persistence_evaluated": False,
        "persistence_frequency_policy": "raw_full_window_bin_center",
        "candidate_bin": -1, "candidate_hz": float("nan"),
        "rate_bpm": float("nan"), "peaks_agree": False,
        "candidate_local_max": False, "refinement_available": False,
        "refinement_reason": "no_candidate", "fft_spectrum": np.empty(0),
        "fft_freqs_hz": np.empty(0), "ha_candidate_scores": np.empty(0),
        "ha_harmonic_power": np.empty((0, 0)),
        "fft_selected_bin": -1, "ha_selected_bin": -1,
        "respiratory_fourier_coefficients": np.empty(0, dtype=np.complex128),
        "subband_fourier_coefficients": np.empty(0, dtype=np.complex128),
        "raw_grid_spacing_bpm": 1.0,
        "persistence_half_scores": np.full(2, np.nan),
        "persistence_half_energy": np.full(2, np.nan),
        "persistence_basis_norms": np.full((2, 2), np.nan),
        "persistence_coefficients": np.full((2, 2), np.nan),
        "persistence_bases": np.empty((0, 2, 0)),
        "persistence_centered_halves": np.empty((0, 0)),
        "persistence_roundoff_tolerance": COHERENCE_ROUNDOFF_TOL,
    }
    if x.ndim != 1 or len(x) != WINDOW_FRAMES or fs != FRAME_RATE_HZ:
        return out
    if not np.isfinite(x).all():
        out["reason"] = "nonfinite_phase"
        return out
    detrended = _detrend(x, "linear")
    trend = x - detrended
    centered_trend = trend - trend.mean()
    spectrum = rfft(detrended)
    respiratory_coefficients = np.zeros_like(spectrum)
    subband_coefficients = np.zeros_like(spectrum)
    respiratory_coefficients[3:31] = spectrum[3:31]
    subband_coefficients[1:3] = spectrum[1:3]
    respiratory = irfft(respiratory_coefficients, n=WINDOW_FRAMES)
    subband = irfft(subband_coefficients, n=WINDOW_FRAMES)
    out.update({
        "detrended": detrended, "linear_trend": trend,
        "respiratory_projection": respiratory, "subband_projection": subband,
        "respiratory_fourier_coefficients": respiratory_coefficients,
        "subband_fourier_coefficients": subband_coefficients,
        "respiratory_block_rms": np.sqrt(np.mean(respiratory.reshape(6, 200) ** 2, axis=1)),
        "subband_block_rms": np.sqrt(np.mean(subband.reshape(6, 200) ** 2, axis=1)),
        "drift_rms": float(np.sqrt(np.mean(centered_trend ** 2))),
        "raw_grid_spacing_bpm": 1.0,
    })
    fft_result = fft_estimate_rr(x, fs, BAND_HZ, detrend_type="linear")
    ha_result = ha_estimate_rr(x, fs, BAND_HZ, max_harmonics=3,
                               harmonic_max_hz=None, detrend_type="linear")
    # Preserve every helper field with unambiguous method prefixes.
    for prefix, result in (("fft", fft_result), ("ha", ha_result)):
        for key, value in result.items():
            name = key if key.startswith(prefix + "_") else prefix + "_" + key
            out[name] = value
    fft_bin = int(fft_result["fft_selected_bin"])
    ha_bin = int(ha_result["ha_selected_bin"])
    fft_score = float(fft_result["fft_peak_snr_db"])
    ha_score = float(ha_result["ha_score"])
    out["fft_score"] = fft_score if fft_bin >= 0 and np.isfinite(fft_score) else 0.0
    out["ha_score"] = ha_score if ha_bin >= 0 and np.isfinite(ha_score) else 0.0
    local_max = 3 <= fft_bin <= 30 and _is_local_max(fft_result["spectrum"], fft_bin)
    out["candidate_local_max"] = bool(local_max)
    out["peaks_agree"] = bool(local_max and fft_bin == ha_bin)
    if local_max:
        candidate_hz = fft_bin * fs / WINDOW_FRAMES
        out["candidate_bin"] = fft_bin
        out["candidate_hz"] = candidate_hz
        out.update(persistence_score(respiratory, candidate_hz, fs))
        if fft_bin in (3, 30):
            rate = candidate_hz * 60
            out["refinement_reason"] = "band_boundary_raw_center"
        else:
            refined_hz = float(fft_result["fft_peak_hz"])
            if np.isfinite(refined_hz) and BAND_HZ[0] <= refined_hz <= BAND_HZ[1]:
                rate = refined_hz * 60
                out["refinement_available"] = True
                out["refinement_reason"] = "interior"
            else:
                rate = candidate_hz * 60
                out["refinement_reason"] = "refinement_outside_band_raw_center"
        out["rate_bpm"] = float(rate)
    finite_activity = all(np.isfinite(out[key]).all() for key in (
        "respiratory_block_rms", "subband_block_rms", "drift_rms"))
    out["valid"] = bool(finite_activity)
    out["reason"] = "measured" if finite_activity else "invalid_activity_projection"
    return out


def _validate_thresholds(thresholds: Mapping) -> dict:
    keys = ("fft_score_min", "ha_score_min", "quiet_resp_rms_max",
            "periodic_resp_rms_min", "subband_rms_max", "drift_rms_max", "persistence_min")
    clean = {}
    for key in keys:
        value = thresholds.get(key)
        if isinstance(value, (bool, str)) or value is None or not np.isfinite(value):
            raise ValueError(f"invalid calibrated breathing threshold: {key}")
        clean[key] = float(value)
    if not (0 <= clean["quiet_resp_rms_max"] < clean["periodic_resp_rms_min"]):
        raise ValueError("quiet and periodic amplitude bounds must strictly separate")
    if (clean["ha_score_min"] < 0 or clean["subband_rms_max"] < 0
            or clean["drift_rms_max"] < 0 or not 0 <= clean["persistence_min"] <= 1):
        raise ValueError("invalid calibrated breathing threshold bounds")
    return clean


def assess_breathing(phase: np.ndarray, fs: float, thresholds: Mapping,
                     eligible: bool = True) -> BreathingAssessment:
    thresholds = _validate_thresholds(thresholds)
    evidence = measure_breathing(phase, fs)
    evidence.update({"threshold_" + k: v for k, v in thresholds.items()})
    evidence["window_eligible"] = bool(eligible)
    evidence["positive_evaluated"] = False
    evidence["quiet_evaluated"] = False
    evidence["positive_rejections"] = np.empty(0, dtype="U64")
    evidence["quiet_rejections"] = np.empty(0, dtype="U64")
    if not eligible or not evidence["valid"]:
        return BreathingAssessment("unresolved", float("nan"),
                                   "physical_quality" if not eligible else evidence["reason"], evidence)
    positive_checks = {
        "peaks_disagree": evidence["peaks_agree"],
        "not_local_maximum": evidence["candidate_local_max"],
        "fft_score_low": evidence["fft_score"] >= thresholds["fft_score_min"],
        "ha_score_low": evidence["ha_score"] >= thresholds["ha_score_min"],
        "periodic_amplitude_low": bool(np.all(evidence["respiratory_block_rms"] >= thresholds["periodic_resp_rms_min"])),
        "persistence_low": bool(evidence["persistence_valid"] and evidence["persistence"] >= thresholds["persistence_min"]),
    }
    rejected = [reason for reason, passed in positive_checks.items() if not passed]
    evidence["positive_evaluated"] = True
    evidence["positive_rejections"] = np.asarray(rejected, dtype="U64")
    if not rejected and np.isfinite(evidence["rate_bpm"]) and 3 <= evidence["rate_bpm"] <= 30:
        return BreathingAssessment("positive", evidence["rate_bpm"], "passed", evidence)
    quiet_checks = {
        "respiratory_activity_unresolved": bool(np.all(evidence["respiratory_block_rms"] <= thresholds["quiet_resp_rms_max"])),
        "subband_activity": bool(np.all(evidence["subband_block_rms"] <= thresholds["subband_rms_max"])),
        "drift_activity": evidence["drift_rms"] <= thresholds["drift_rms_max"],
    }
    quiet_rejections = [reason for reason, passed in quiet_checks.items() if not passed]
    evidence["quiet_evaluated"] = True
    evidence["quiet_rejections"] = np.asarray(quiet_rejections, dtype="U64")
    if not quiet_rejections:
        return BreathingAssessment("quiet", float("nan"), "No breathing motion detected", evidence)
    reason = ";".join(dict.fromkeys(rejected + quiet_rejections))
    return BreathingAssessment("unresolved", float("nan"), reason, evidence)


def hr_veto_decision(assessment: BreathingAssessment, ordinary_dsp: Mapping) -> Mapping:
    """Record the additional HR decision without altering or rerunning ECA/AHET."""
    respiration_input = ordinary_dsp.get("f_r_hz", float("nan"))
    try:
        respiration_input = float(respiration_input)
    except (TypeError, ValueError):
        respiration_input = float("nan")
    br = float(assessment.value_bpm)
    decision = {
        "extended_br_state": assessment.state,
        "extended_br_bpm": br,
        "ahet_respiration_input_hz": respiration_input,
        "ahet_respiration_input_bpm": respiration_input * 60,
        "extended_ahet_difference_bpm": (
            abs(br - respiration_input * 60)
            if np.isfinite(br) and np.isfinite(respiration_input) else float("nan")
        ),
        "hr_veto_reason": "",
    }
    if assessment.state == "quiet":
        decision["hr_veto_reason"] = "quiet_breathing"
    elif assessment.state == "positive":
        if not np.isfinite(br) or br <= 0:
            decision["hr_veto_reason"] = "invalid_extended_breathing_value"
        elif br < 9:
            decision["hr_veto_reason"] = "extended_breathing_below_9_bpm"
        elif not np.isfinite(respiration_input):
            decision["hr_veto_reason"] = "missing_ahet_respiration_input"
        elif decision["extended_ahet_difference_bpm"] > 2:
            decision["hr_veto_reason"] = "extended_ahet_respiration_disagreement"
    return MappingProxyType(decision)


def hr_veto(assessment: BreathingAssessment, ordinary_dsp: Mapping) -> str:
    """Compatibility helper; runtime persists the structured decision above."""
    return hr_veto_decision(assessment, ordinary_dsp)["hr_veto_reason"]
