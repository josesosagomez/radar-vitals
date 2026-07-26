"""The one window-level DSP composition callable (M4 plan §5.1, Stage 0).

Extracted verbatim from `scripts/live_demo.py:_run_dsp`. Both the live path and the
M4 offline harness **import** this — neither reimplements it. Without the extraction
M4's "same DSP as production" equality test would compare M4 against whichever
duplicate the test author wrote, so two copies that silently drift apart would both
pass (M4R-10).

This module owns window-level *composition* only: config wiring, respiration
fusion/validity semantics, the ECA input handoff and fallback handling. The DSP
primitives stay in `src/vitals.py` and `src/respiration.py`.

Also defines the normalised estimator adapter (`WindowEstimate`) so M8/M9/M10
estimators can enter the M4 grid and scoring path without copying the comparator.
"""
from __future__ import annotations

import collections
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

import numpy as np

from .respiration import (
    extract_chest_phase,
    fft_estimate_rr,
    fuse_estimates,
    ha_estimate_rr,
    stft_stability,
)
from .vitals import estimate_rate_from_phase, remove_impulse_noise

# AHET candidate rejection codes. `scripts/diagnose_live_run.py` mirrors this table.
REJECTION_CODE_NAMES = {
    -1: "", 0: "passed",
    1: "no_second_harmonic_region", 2: "ratio_db_low",
    3: "prominence_low", 4: "low_candidate_competitor",
    5: "not_attempted", 6: "peak_to_floor_db_low",
    7: "low_candidate_floor_db_low",
}

#: Identifier for the production ECA+AHET window estimator implemented below.
#: M8/M9/M10 estimators declare their own ID and reuse `WindowEstimate`.
ESTIMATOR_ID = "eca_ahet_v1"


def run_window_dsp(
    frames: "collections.deque | np.ndarray",
    locked_bin: int,
    fs: float,
    cfg: dict,
) -> dict:
    """Run the full per-window DSP chain on one buffer of frames.

    `frames` is the live path's ring buffer (a deque of per-frame cubes) or an
    already-stacked `(window_frames, chirps, rx, adc)` array; both stack identically.
    """
    cube = np.stack(list(frames))   # (window_frames, chirps, rx, adc)

    phase_raw = extract_chest_phase(
        cube,
        locked_bin=locked_bin,
        method=cfg["phase"]["method"],
    )
    phase_clean = remove_impulse_noise(
        phase_raw,
        thresh=float(cfg["phase"]["impulse_clip_rad"]),
    )

    resp_cfg = cfg["respiration"]
    band_hz = tuple(resp_cfg["band_hz"])

    fft_r = fft_estimate_rr(
        phase_clean, fs, band_hz,
        detrend_type=resp_cfg["detrend"],
    )
    ha_r = ha_estimate_rr(
        phase_clean, fs, band_hz,
        max_harmonics=resp_cfg["max_harmonics"],
        harmonic_max_hz=resp_cfg["harmonic_max_hz"],
        detrend_type=resp_cfg["detrend"],
    )
    stft_r = stft_stability(
        phase_clean, fs, band_hz,
        subwindow_s=float(resp_cfg["stft_subwindow_s"]),
        overlap=float(resp_cfg["stft_overlap"]),
        detrend_type=resp_cfg["detrend"],
    )
    br_result = fuse_estimates(fft_r, ha_r, stft_r, resp_cfg)

    # Low-confidence BR invalidated before ECA — do not feed a bad f_r to AHET
    if br_result.get("resp_confidence") == "low":
        br_result = dict(br_result)
        br_result["resp_valid"] = False
    f_r_hz: float | None = (
        float(br_result["resp_peak_hz"]) if br_result["resp_valid"] else None
    )

    hcfg = cfg["heart"]
    hr_result = estimate_rate_from_phase(
        phase_clean,
        fs,
        tuple(hcfg["band_hz"]),
        f_r_hz=f_r_hz,
        k_max=int(hcfg["k_max"]),
        ahet_deviation_hz=float(hcfg["ahet_deviation_hz"]),
        eca_mode=hcfg["eca_mode"],
        ahet_gate_mode=hcfg["ahet_gate_mode"],
        eca_forbidden_guard_hz=float(hcfg["eca_forbidden_guard_hz"]),
        eca_cardiac_guard_hz=float(hcfg.get("eca_cardiac_guard_hz", 0.10)),
        k_max_cap=int(hcfg.get("k_max_cap", 10)),
        candidate_min_second_harmonic_ratio_db=float(
            hcfg["candidate_min_second_harmonic_ratio_db"]
        ),
        candidate_min_prominence=float(hcfg["candidate_min_prominence"]),
        low_candidate_hz=float(hcfg["low_candidate_hz"]),
        high_candidate_preference_hz=float(hcfg["high_candidate_preference_hz"]),
        high_competitor_min_mag_ratio=float(hcfg["high_competitor_min_mag_ratio"]),
        candidate_min_peak_to_floor_db=float(hcfg["candidate_min_peak_to_floor_db"]),
        low_candidate_min_peak_to_floor_db=float(
            hcfg["low_candidate_min_peak_to_floor_db"]
        ),
    )

    # Diagnostic-only no-ECA baseline — never shown as a confident estimate
    baseline = estimate_rate_from_phase(
        phase_clean, fs, tuple(hcfg["band_hz"])
    )
    fallback_hr_bpm = float(baseline.get("rate_bpm", np.nan))
    baseline_spectrum = baseline.get("spectrum", np.array([]))
    baseline_freqs_hz = baseline.get("freqs_hz", np.array([]))

    hr_valid = bool(hr_result.get("ahet_verified", False))
    hr_raw = float(hr_result["rate_bpm"]) if hr_valid else np.nan

    rej_codes = hr_result.get("candidate_rejection_code", np.array([-1, -1, -1]))
    # Fixed reporting length — k_max_eff varies per window but the artifact shape must not,
    # or the NPZ stack breaks / silently hides k > k_max (plan S8.1b).
    _skip_len = max(int(hcfg.get("k_max_cap", 10)), int(hcfg["k_max"]))
    eca_skip = np.asarray(
        hr_result.get("eca_skipped_harmonics", np.zeros(_skip_len, dtype=bool)), dtype=bool
    )

    accepted_rank = int(hr_result.get("accepted_candidate_rank", -1))
    if hr_valid and 0 <= accepted_rank < len(rej_codes):
        summary_code = int(rej_codes[accepted_rank])
    else:
        summary_code = int(rej_codes[0]) if len(rej_codes) > 0 else -1
    rej_reason = REJECTION_CODE_NAMES.get(summary_code, str(summary_code))

    return {
        "hr_valid": hr_valid,
        "hr_raw": hr_raw,
        "fallback_hr_bpm": fallback_hr_bpm,
        "baseline_spectrum": baseline_spectrum,
        "baseline_freqs_hz": baseline_freqs_hz,
        "hr_result": hr_result,
        "br_result": br_result,
        "br_bpm": float(br_result.get("radar_rr_bpm", np.nan)),
        "br_confidence": br_result.get("resp_confidence", "low"),
        "br_valid": bool(br_result.get("resp_valid", False)),
        "f_r_hz": f_r_hz,
        "spectrum_stage": int(hr_result.get("spectrum_stage", 0)),
        "rej_reason": rej_reason,
        "n_eca_skipped": int(np.sum(eca_skip)),
        # WHICH harmonics were spared, not just how many — the plan's predictions (S6.5)
        # depend on the identity of k, and the count alone cannot express it.
        "eca_skipped_harmonics": eca_skip,
        "k_max_eff": int(hr_result.get("k_max_eff", 0)),
        "n_eca_projected": int(hr_result.get("n_eca_projected", 0)),
        # Basis diagnostics: how many sin/cos columns SURVIVED Gram-Schmidt, not merely how
        # many harmonic orders were selected (cross-review 20.6). Without this a run can report
        # full harmonic coverage while the projection actually used fewer columns.
        "n_eca_cols_retained": int(hr_result.get("n_eca_cols_retained", 0)),
        "n_eca_cols_dropped": int(hr_result.get("n_eca_cols_dropped", 0)),
        # Intermediates for NPZ
        "phase_raw": phase_raw,
        "phase_clean": phase_clean,
        "fft_r": fft_r,
        "ha_r": ha_r,
        "stft_r": stft_r,
    }


# ── Normalised estimator adapter (M4 plan §5.1 item 3) ───────────────────────────


@runtime_checkable
class WindowEstimator(Protocol):
    """The call signature every window estimator must present to M4.

    `run_window_dsp` satisfies it, as must any M8/M9/M10 estimator that wants to be
    scored on the same grid. The estimator returns its own native result dict;
    `as_window_estimate` normalises that dict into the record the scorer consumes.
    """

    def __call__(
        self,
        frames: "collections.deque | np.ndarray",
        locked_bin: int,
        fs: float,
        cfg: dict,
    ) -> dict: ...


def config_hash(cfg: Mapping[str, Any]) -> str:
    """Deterministic SHA256 over a config mapping.

    Canonical JSON (sorted keys, non-JSON values stringified) so the same config
    always hashes the same regardless of dict insertion order. Carried on every
    `WindowEstimate` so a result can never be silently attributed to the wrong config.
    """
    canonical = json.dumps(cfg, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WindowEstimate:
    """One window's estimator-agnostic result, tagged with its provenance.

    `hr_bpm` / `br_bpm` are NaN whenever the corresponding validity flag is False —
    the scorer maps an invalid estimate to a *recorded* radar-NaN disposition, never
    to a missing window. `raw` keeps the estimator's native dict for evidence dumps.
    """

    estimator_id: str
    config_hash: str
    hr_bpm: float
    hr_valid: bool
    br_bpm: float
    br_valid: bool
    br_confidence: str = "low"
    f_r_hz: float | None = None
    rejection_reason: str = ""
    raw: dict = field(default_factory=dict, repr=False, compare=False)


def as_window_estimate(
    dsp: dict,
    *,
    estimator_id: str = ESTIMATOR_ID,
    cfg_hash: str,
) -> WindowEstimate:
    """Normalise a `run_window_dsp`-shaped result dict into a `WindowEstimate`.

    Enforces the NaN-when-invalid rule at the boundary so no downstream consumer can
    read a rate off an unverified window. `hr_bpm_smooth` and `fallback_hr_bpm` are
    deliberately NOT carried: the first is an online median (not paper-grade) and the
    second is a naive argmax (CLAUDE.md §4).
    """
    hr_valid = bool(dsp.get("hr_valid", False))
    br_valid = bool(dsp.get("br_valid", False))
    hr_bpm = float(dsp.get("hr_raw", np.nan)) if hr_valid else float("nan")
    br_bpm = float(dsp.get("br_bpm", np.nan)) if br_valid else float("nan")
    f_r = dsp.get("f_r_hz")
    return WindowEstimate(
        estimator_id=estimator_id,
        config_hash=cfg_hash,
        hr_bpm=hr_bpm,
        hr_valid=hr_valid,
        br_bpm=br_bpm,
        br_valid=br_valid,
        br_confidence=str(dsp.get("br_confidence", "low")),
        f_r_hz=None if f_r is None else float(f_r),
        rejection_reason=str(dsp.get("rej_reason", "")),
        raw=dsp,
    )
