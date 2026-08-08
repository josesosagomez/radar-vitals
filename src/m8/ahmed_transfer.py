"""Ahmed fixed-H harmonic accumulation applied to this project's extracted FMCW phase.

M8 Step 1b scientific core. Authority is the pair
`plans/m8_step1b_ahmed_transfer.md` + `plans/m8_step1b_ahmed_transfer_addendum_a.md`;
the addendum wins on conflict.

What this is **not**: it is not `src/respiration.py::ha_estimate_rr`. That production
estimator detrends, applies a slow-time Hann window, truncates harmonics, doubles the
fundamental, and vetoes non-local-maxima — all outside this fixed-H transfer. This module
reuses Step 1a's `accumulate_harmonics`, `SUPPRESSION_PROFILES`, and deterministic
lowest-bin tie selection unchanged, and adds only what Step 1b declares.

Differences from Step 1a that are easy to get wrong:

* candidate frequency is ``q = f`` and ``bpm = 60 * q`` — **not** Step 1a's ``q = 2f``,
  ``bpm = 30 * q``;
* full fixed-H support is the **strict** condition ``H*q < fs/2``; equality is recorded as
  `nyquist_boundary_degenerate`, because a real sinusoid at Nyquist loses one quadrature.
  Unsupported candidates are masked *before* accumulation and the denominator stays
  exactly ``H`` — the harmonic row is never truncated;
* the transform is **native length** (addendum section A2). Step 1a's zero-pad to 4096 is
  available only as the named non-gating `zero_padded_4096_audit`.

Two candidate domains are declared (addendum section A3). Both are computed and reported
on synthetic data and **neither gates**; gating is defined solely by the addendum's
section A4.2 prediction reproduction. ``COLLISION_DOMAIN_FROM_FB`` is synthetic-only: its
heart band starts at the *known synthetic* breathing frequency, a quantity that does not
exist on real data, where only ``REAL_REPRESENTATIVE_DOMAIN`` applies.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Literal

import numpy as np

from src.m4.estimator_suite import (
    EstimatorArmSpec,
    SuiteWindowResult,
    canonical_plain,
    freeze_array,
    validate_arm_specs,
    validate_returned_arms,
)
from src.m8.ahmed_fig8 import (
    SUPPRESSION_PROFILES,
    SuppressionProfile,
    accumulate_harmonics,
    _select_scores,
)
from src.respiration import extract_chest_phase
from src.window_pipeline import run_config_hash

__all__ = [
    "APPROVED_ARM_IDS",
    "APPROVED_HARMONIC_COUNTS",
    "APPROVED_LAYER_A_PROFILE_IDS",
    "AhmedPhaseConfig",
    "AhmedPhaseEstimatorSuite",
    "CandidateDomain",
    "COLLISION_DOMAIN_FROM_FB",
    "ESTIMATOR_ID",
    "FREQUENCY_MAPPING_ID",
    "LAYER_B_NORMALIZATION",
    "PHASE_EXTRACTION_METHOD",
    "RATE_MAPPING_ID",
    "SCORE_FORMULA_ID",
    "SCORE_FUNCTIONAL",
    "SUPPORT_RULE_ID",
    "REAL_REPRESENTATIVE_DOMAIN",
    "arm_id_for",
    "estimate_phase_ha",
    "phase_signal_hash",
]

ESTIMATOR_ID = "ahmed_fixed_h_phase_v1"
FREQUENCY_MAPPING_ID = "q_equals_phase_frequency_f"
RATE_MAPPING_ID = "bpm_equals_60_times_q"
SCORE_FUNCTIONAL = "sum_of_spectral_magnitudes"
SCORE_FORMULA_ID = "sum_h_abs_s_hq_divided_by_h"
SUPPORT_RULE_ID = "strict_h_q_lt_nyquist"
LAYER_B_NORMALIZATION = "non_dc_mean"
APPROVED_HARMONIC_COUNTS = (3, 5)

#: The only real mapping (base plan section 3.1). Not configurable: an alternative method
#: would be a different experiment, not a different setting.
PHASE_EXTRACTION_METHOD = "delta_before_mean"

Vital = Literal["breath", "heart"]

#: Synthetic breathing fundamental used as the collision domain's heart floor, in Hz.
#: This is the declared synthetic truth (20 bpm), not anything measured.
_SYNTHETIC_FB_HZ = 20.0 / 60.0


def _layer_a_profile_id(harmonics: int, profile: SuppressionProfile) -> str:
    profile_suffix = {
        "figure_visible_unsuppressed": "unsuppressed",
        "eq26_multiples_suppressed": "eq26",
        "prose_low_or_equal_suppressed": "prose",
    }[profile]
    return f"a_fixed_h{harmonics}_non_dc_mean_magnitude_{profile_suffix}_v1"


APPROVED_ARM_IDS = tuple(
    f"ahmed_phase_h{harmonics}_{profile}"
    for harmonics in APPROVED_HARMONIC_COUNTS
    for profile in SUPPRESSION_PROFILES
)
APPROVED_LAYER_A_PROFILE_IDS = tuple(
    _layer_a_profile_id(harmonics, profile)
    for harmonics in APPROVED_HARMONIC_COUNTS
    for profile in SUPPRESSION_PROFILES
)


@dataclass(frozen=True)
class CandidateDomain:
    """Breathing and heart candidate bands, as a named pair.

    `breath_lo_exclusive` preserves base plan section 2.3's asymmetry: the synthetic
    breathing band is ``0 < q <= 25/60`` (open at zero, since DC is never a candidate)
    while the real band is the closed ``[0.10, 0.50]``.
    """

    domain_id: str
    breath_lo_hz: float
    breath_hi_hz: float
    heart_lo_hz: float
    heart_hi_hz: float
    breath_lo_exclusive: bool
    real_data_eligible: bool

    def bounds(self, vital: Vital) -> tuple[float, float, bool]:
        if vital == "breath":
            return self.breath_lo_hz, self.breath_hi_hz, self.breath_lo_exclusive
        if vital == "heart":
            return self.heart_lo_hz, self.heart_hi_hz, False
        raise ValueError(f"unsupported vital {vital!r}")


#: Base plan section 2.3 synthetic row. The heart band starts at the synthetic breathing
#: fundamental, so the breathing line competes as a heart candidate — the "collision
#: scenario" of base plan section 1. Synthetic only: there is no known f_b on real data.
COLLISION_DOMAIN_FROM_FB = CandidateDomain(
    domain_id="collision_domain_from_fb",
    breath_lo_hz=0.0,
    breath_hi_hz=25.0 / 60.0,
    heart_lo_hz=_SYNTHETIC_FB_HZ,
    heart_hi_hz=100.0 / 60.0,
    breath_lo_exclusive=True,
    real_data_eligible=False,
)

#: Base plan section 2.3 real row — the band the production pipeline actually uses.
REAL_REPRESENTATIVE_DOMAIN = CandidateDomain(
    domain_id="real_representative_domain",
    breath_lo_hz=0.10,
    breath_hi_hz=0.50,
    heart_lo_hz=0.80,
    heart_hi_hz=2.00,
    breath_lo_exclusive=False,
    real_data_eligible=True,
)


@dataclass(frozen=True)
class AhmedPhaseConfig:
    """Resolved, hashable configuration for one invocation of the core."""

    domain: CandidateDomain
    harmonic_counts: tuple[int, ...] = APPROVED_HARMONIC_COUNTS
    suppression_profiles: tuple[SuppressionProfile, ...] = SUPPRESSION_PROFILES
    normalization: str = LAYER_B_NORMALIZATION
    #: None means the native transform length, len(phase) (addendum section A2).
    n_fft: int | None = None

    def __post_init__(self) -> None:
        if not self.harmonic_counts or any(
            type(h) is not int or h <= 0 for h in self.harmonic_counts
        ):
            raise ValueError("harmonic_counts must be non-empty positive exact ints")
        if len(set(self.harmonic_counts)) != len(self.harmonic_counts):
            raise ValueError(f"duplicate harmonic count in {self.harmonic_counts}")
        unknown = set(self.suppression_profiles) - set(SUPPRESSION_PROFILES)
        if unknown:
            raise ValueError(f"unsupported suppression profiles {sorted(unknown)}")
        if self.normalization != LAYER_B_NORMALIZATION:
            raise ValueError(
                "Layer B normalization is fixed to non_dc_mean; matrix_eta belongs "
                "only to the Layer A ambiguity audit"
            )
        if self.n_fft is not None and (type(self.n_fft) is not int or self.n_fft <= 0):
            raise ValueError("n_fft must be a positive exact int or None")

    def to_dict(self) -> dict:
        return canonical_plain(
            {
                "domain_id": self.domain.domain_id,
                "breath_lo_hz": self.domain.breath_lo_hz,
                "breath_hi_hz": self.domain.breath_hi_hz,
                "breath_lo_exclusive": self.domain.breath_lo_exclusive,
                "heart_lo_hz": self.domain.heart_lo_hz,
                "heart_hi_hz": self.domain.heart_hi_hz,
                "harmonic_counts": list(self.harmonic_counts),
                "suppression_profiles": list(self.suppression_profiles),
                "normalization": self.normalization,
                "n_fft": self.n_fft,
                "frequency_mapping_id": FREQUENCY_MAPPING_ID,
                "rate_mapping_id": RATE_MAPPING_ID,
                "phase_representation": "native_unwrapped_phase",
                "phase_extraction_method": PHASE_EXTRACTION_METHOD,
                "spectrum_functional": "rfft_magnitude",
                "score_functional": SCORE_FUNCTIONAL,
                "score_formula_id": SCORE_FORMULA_ID,
                "support_rule": SUPPORT_RULE_ID,
                "estimator_id": ESTIMATOR_ID,
            }
        )

    def config_hash(self) -> str:
        return run_config_hash(self.to_dict())

    def arm_config_hash(self, harmonics: int, profile: SuppressionProfile) -> str:
        """Arm hash = suite config plus this arm's exact H and suppression profile."""
        payload = dict(self.to_dict())
        payload["harmonic_count"] = harmonics
        payload["suppression_profile"] = profile
        payload["layer_a_profile_id"] = _layer_a_profile_id(harmonics, profile)
        return run_config_hash(canonical_plain(payload))


def arm_id_for(harmonics: int, profile: SuppressionProfile) -> str:
    if harmonics not in APPROVED_HARMONIC_COUNTS:
        raise ValueError(f"Layer B supports only H=3 and H=5, got H={harmonics}")
    if profile not in SUPPRESSION_PROFILES:
        raise ValueError(f"unsupported Layer B suppression profile {profile!r}")
    return f"ahmed_phase_h{harmonics}_{profile}"


def phase_signal_hash(header: str, phase: np.ndarray) -> str:
    """sha256(canonical_header || NUL || little-endian float64 phase bytes)."""
    digest = sha256()
    digest.update(header.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(np.asarray(phase, dtype="<f8").tobytes(order="C"))
    return digest.hexdigest()


def _validate_phase(phase: np.ndarray) -> np.ndarray:
    array = np.asarray(phase)
    if array.ndim != 1:
        raise ValueError(f"phase must be one-dimensional, got shape {array.shape}")
    if array.size < 2:
        raise ValueError("phase must contain at least two samples")
    if array.dtype == object or not np.issubdtype(array.dtype, np.floating):
        raise ValueError(f"phase must be a floating array, got dtype {array.dtype}")
    values = array.astype(np.float64, copy=False)
    if not np.all(np.isfinite(values)):
        raise ValueError("phase contains non-finite values")
    return values


def _candidate_bins(
    freqs: np.ndarray, lo_hz: float, hi_hz: float, lo_exclusive: bool
) -> np.ndarray:
    """rFFT bin indices inside the band. DC is never a candidate."""
    tolerance = np.finfo(float).eps * max(1.0, hi_hz) * 8.0
    lower = freqs > lo_hz + tolerance if lo_exclusive else freqs >= lo_hz - tolerance
    bins = np.flatnonzero(lower & (freqs <= hi_hz + tolerance)).astype(np.int64)
    return bins[bins > 0]


def _support_masks(
    bins: np.ndarray, harmonics: int, n_fft: int, spectrum_size: int
) -> tuple[np.ndarray, np.ndarray]:
    """Strict full-H support, plus the Nyquist-equality degenerate set.

    ``H*q < fs/2`` in bin terms is ``H*k < n_fft/2``; equality can only occur for even
    ``n_fft`` and is degenerate rather than supported.
    """
    reach = bins.astype(np.int64) * harmonics
    half = n_fft / 2.0
    supported = (reach < half) & (reach < spectrum_size)
    degenerate = (reach == half) & ~supported
    return supported, degenerate


def _score_vital(
    spectrum: np.ndarray,
    freqs: np.ndarray,
    bins: np.ndarray,
    harmonics: int,
    normalization: str,
    n_fft: int,
    *,
    suppress_mask: np.ndarray | None,
) -> dict:
    """Accumulate, mask, select. Returns a plain evidence dict for one (vital, arm)."""
    supported, degenerate = _support_masks(bins, harmonics, n_fft, spectrum.size)
    supported_bins = bins[supported]
    # Keep a candidate-aligned matrix even for invalid rows.  Evidence consumers can
    # reconstruct every score and use ``supported_mask`` to distinguish real cells from
    # unsupported harmonic indices; no sentinel value carries scientific meaning.
    harmonic_bins_all = bins[:, None] * np.arange(1, harmonics + 1, dtype=np.int64)[None, :]
    suppression = (
        np.ones(bins.size, dtype=bool)
        if suppress_mask is None
        else np.asarray(suppress_mask, dtype=bool)
    )
    if suppression.shape != bins.shape:
        raise ValueError("suppression mask must align with candidate bins")
    result: dict = {
        "candidate_bins": bins,
        "harmonic_bins": harmonic_bins_all,
        "supported_mask": supported,
        "nyquist_degenerate_mask": degenerate,
        "suppression_mask": suppression,
        "n_candidates": int(bins.size),
        "n_supported": int(supported_bins.size),
    }
    if supported_bins.size == 0:
        result.update(
            scores=np.full(bins.size, np.nan),
            scores_pre_exclusion=np.full(bins.size, np.nan),
            eligible_mask=np.zeros(bins.size, dtype=bool),
            selected_bin=None,
            selected_hz=None,
            selected_score=None,
            runner_up_bin=None,
            runner_up_hz=None,
            runner_up_score=None,
            margin=None,
            unique_maximum=False,
            reason="no_supported_candidate",
        )
        return result

    supported_harmonic_bins, supported_scores = accumulate_harmonics(
        spectrum, supported_bins, harmonics, normalization
    )
    if not np.array_equal(supported_harmonic_bins, harmonic_bins_all[supported]):
        raise RuntimeError("harmonic accumulation returned a misaligned bin matrix")
    scores_pre = np.full(bins.size, np.nan)
    scores_pre[supported] = supported_scores

    eligible = supported.copy()
    eligible &= suppression
    scores = np.where(eligible, scores_pre, np.nan)

    index, selected_bin, runner_bin, runner_score, unique = _select_scores(scores, bins)
    selected_score = None if index is None else float(scores[index])
    margin = (
        None
        if selected_score is None or runner_score is None
        else float(selected_score - runner_score)
    )
    if index is None:
        reason = "all_candidates_suppressed" if suppress_mask is not None else "no_finite_score"
    elif not unique:
        reason = "non_unique_maximum"
    else:
        reason = ""

    result.update(
        # Persist the full candidate-aligned matrix. Unsupported candidates remain
        # present and are interpreted only through ``supported_mask``.
        harmonic_bins=harmonic_bins_all,
        scores=scores,
        scores_pre_exclusion=scores_pre,
        eligible_mask=eligible,
        selected_bin=None if selected_bin is None else int(selected_bin),
        selected_hz=None if selected_bin is None else float(freqs[selected_bin]),
        selected_score=selected_score,
        runner_up_bin=runner_bin,
        runner_up_hz=None if runner_bin is None else float(freqs[runner_bin]),
        runner_up_score=runner_score,
        margin=margin,
        unique_maximum=bool(unique),
        reason=reason,
    )
    return result


def _suppression_mask(
    profile: SuppressionProfile, bins: np.ndarray, breath_bin: int | None
) -> np.ndarray | None:
    """Heart-candidate exclusion driven by the same-H Ahmed breathing bin.

    Never truth, never production BR (base plan section 2.3).
    """
    if profile == "figure_visible_unsuppressed":
        return None
    if breath_bin is None:
        raise ValueError(f"profile {profile!r} requires a valid Ahmed breathing bin")
    if profile == "eq26_multiples_suppressed":
        return bins % breath_bin != 0
    if profile == "prose_low_or_equal_suppressed":
        return bins > breath_bin
    raise ValueError(f"unsupported suppression profile {profile!r}")


def estimate_phase_ha(
    phase: np.ndarray,
    fs: float,
    config: AhmedPhaseConfig,
    *,
    shared_signal_hash: str | None = None,
) -> SuiteWindowResult:
    """Run fixed-H harmonic accumulation on one window of extracted phase.

    Emits one Ahmed breathing result per harmonic count, and one heart result per
    (harmonic count, suppression profile) arm, plus shared spectrum evidence.
    """
    values = _validate_phase(phase)
    if not np.isfinite(fs) or fs <= 0:
        raise ValueError(f"fs must be finite and positive, got {fs!r}")
    n_fft = int(config.n_fft) if config.n_fft is not None else int(values.size)
    if n_fft < values.size:
        raise ValueError(
            f"n_fft={n_fft} truncates a {values.size}-sample phase; "
            "the transfer never truncates the signal"
        )

    spectrum = np.abs(np.fft.rfft(values, n=n_fft))
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / fs)
    if not np.all(np.isfinite(spectrum)):
        raise ValueError("phase spectrum contains non-finite values")

    breath_lo, breath_hi, breath_excl = config.domain.bounds("breath")
    heart_lo, heart_hi, _ = config.domain.bounds("heart")
    breath_bins = _candidate_bins(freqs, breath_lo, breath_hi, breath_excl)
    heart_bins = _candidate_bins(freqs, heart_lo, heart_hi, False)
    if breath_bins.size == 0 or heart_bins.size == 0:
        raise ValueError(
            f"domain {config.domain.domain_id!r} yields an empty candidate set at "
            f"fs={fs}, n_fft={n_fft} (breath={breath_bins.size}, heart={heart_bins.size})"
        )

    arm_native_results: dict[str, dict] = {}
    breath_by_h: dict[int, dict] = {}

    for harmonics in config.harmonic_counts:
        breath = _score_vital(
            spectrum, freqs, breath_bins, harmonics, config.normalization, n_fft,
            suppress_mask=None,
        )
        breath_by_h[harmonics] = breath
        breath_valid = breath["selected_bin"] is not None and breath["unique_maximum"]

        for profile in config.suppression_profiles:
            arm_id = arm_id_for(harmonics, profile)
            # Reason precedence (base plan section 3.4): required-BR dependency outranks
            # all-suppressed, which outranks non-unique-maximum.
            if profile != "figure_visible_unsuppressed" and not breath_valid:
                arm_native_results[arm_id] = _invalid_arm(
                    harmonics, profile, config, breath,
                    reason="breath_estimate_invalid",
                    shared_signal_hash=shared_signal_hash,
                )
                continue
            mask = _suppression_mask(profile, heart_bins, breath["selected_bin"])
            heart = _score_vital(
                spectrum, freqs, heart_bins, harmonics, config.normalization, n_fft,
                suppress_mask=mask,
            )
            arm_native_results[arm_id] = _arm_record(
                harmonics, profile, config, breath, heart,
                shared_signal_hash=shared_signal_hash,
            )

    shared_evidence = {
        "domain_id": config.domain.domain_id,
        "fs_hz": float(fs),
        "n_samples": int(values.size),
        "n_fft": int(n_fft),
        "suite_config_hash": config.config_hash(),
        "shared_signal_hash": shared_signal_hash or "",
        "phase": freeze_array(values),
        "spectrum_magnitude": freeze_array(spectrum),
        "spectrum_frequencies_hz": freeze_array(freqs),
        "breath_candidate_bins": freeze_array(breath_bins),
        "heart_candidate_bins": freeze_array(heart_bins),
    }
    return SuiteWindowResult(
        shared_evidence=shared_evidence, arm_native_results=arm_native_results
    )


def _bpm(hz: float | None) -> float | None:
    """bpm = 60 * q. Step 1b uses q = f, not Step 1a's q = 2f."""
    return None if hz is None else 60.0 * hz


def _base_record(
    harmonics: int,
    profile: SuppressionProfile,
    config: AhmedPhaseConfig,
    shared_signal_hash: str | None,
) -> dict:
    return {
        "estimator_id": ESTIMATOR_ID,
        "arm_id": arm_id_for(harmonics, profile),
        "run_config_hash": config.arm_config_hash(harmonics, profile),
        "harmonic_count": harmonics,
        "suppression_profile": profile,
        "layer_a_profile_id": _layer_a_profile_id(harmonics, profile),
        "domain_id": config.domain.domain_id,
        "frequency_mapping_id": FREQUENCY_MAPPING_ID,
        "rate_mapping_id": RATE_MAPPING_ID,
        "phase_representation": "native_unwrapped_phase",
        "phase_extraction_method": PHASE_EXTRACTION_METHOD,
        "spectrum_functional": "rfft_magnitude",
        "score_functional": SCORE_FUNCTIONAL,
        "score_formula_id": SCORE_FORMULA_ID,
        "normalization": LAYER_B_NORMALIZATION,
        "support_rule": SUPPORT_RULE_ID,
        "profile_dependency": (
            "dependent_duplicate_by_disjoint_domains"
            if profile == "prose_low_or_equal_suppressed"
            else "distinct_profile_identity"
        ),
        "shared_signal_hash": shared_signal_hash or "",
    }


def _invalid_arm(
    harmonics: int,
    profile: SuppressionProfile,
    config: AhmedPhaseConfig,
    breath: dict,
    *,
    reason: str,
    shared_signal_hash: str | None,
) -> dict:
    record = _base_record(harmonics, profile, config, shared_signal_hash)
    record.update(
        br_valid=bool(breath["selected_bin"] is not None and breath["unique_maximum"]),
        br_bpm=_bpm(breath["selected_hz"]),
        br_selected_bin=breath["selected_bin"],
        br_confidence=breath["reason"] or "ok",
        hr_valid=False,
        hr_raw=None,
        hr_selected_bin=None,
        hr_margin=None,
        f_r_hz=breath["selected_hz"],
        rej_reason=reason,
        heart_evidence=None,
        breath_evidence=breath,
    )
    return record


class AhmedPhaseEstimatorSuite:
    """Six Ahmed arms over one window, sharing a single extracted phase.

    `extract_chest_phase` is called **once** per window and the resulting phase is scored
    by all six arms, so every arm row references the same `shared_signal_hash`. The suite
    declares no outcome classifiers: the production AHET classifier's preconditions are
    properties of the production gate and do not hold for these arms (plan section 3.4).

    Configuration is bound at construction and cannot be substituted at call time.
    """

    suite_id = "ahmed_fixed_h_phase_suite_v1"

    def __init__(self, config: AhmedPhaseConfig) -> None:
        if not isinstance(config, AhmedPhaseConfig):
            raise TypeError("config must be an AhmedPhaseConfig")
        if config.domain != REAL_REPRESENTATIVE_DOMAIN:
            raise ValueError("the real Layer B suite requires real_representative_domain")
        if config.harmonic_counts != APPROVED_HARMONIC_COUNTS:
            raise ValueError("the real Layer B suite requires exactly H=(3, 5)")
        if config.suppression_profiles != SUPPRESSION_PROFILES:
            raise ValueError(
                "the real Layer B suite requires exactly the three accepted suppression profiles"
            )
        if config.normalization != LAYER_B_NORMALIZATION:
            raise ValueError("the real Layer B suite requires non_dc_mean")
        if config.n_fft is not None:
            raise ValueError("the real Layer B suite requires the native transform length")
        self._config = config  # frozen dataclass; nothing to copy
        self.suite_config_hash = config.config_hash()
        self.arm_specs = tuple(
            EstimatorArmSpec(
                arm_id=arm_id_for(harmonics, profile),
                estimator_id=ESTIMATOR_ID,
                run_config_hash=config.arm_config_hash(harmonics, profile),
                harmonic_count=harmonics,
                suppression_profile=profile,
                outcome_classifier_id=None,
            )
            for harmonics in config.harmonic_counts
            for profile in config.suppression_profiles
        )
        validate_arm_specs(self.arm_specs)
        self.outcome_classifiers: dict[str, object] = {}

    @property
    def config(self) -> AhmedPhaseConfig:
        return self._config

    def __call__(self, frames, locked_bin: int, fs: float) -> SuiteWindowResult:
        cube = np.stack(list(frames)) if not isinstance(frames, np.ndarray) else frames
        phase = extract_chest_phase(
            cube, locked_bin=int(locked_bin), method=PHASE_EXTRACTION_METHOD
        )
        header = (
            f"schema=ahmed_phase_v1|domain={self._config.domain.domain_id}"
            f"|locked_bin={int(locked_bin)}|fs={float(fs)!r}"
            f"|n={int(np.asarray(phase).size)}|dtype=float64"
        )
        result = estimate_phase_ha(
            phase,
            fs,
            self._config,
            shared_signal_hash=phase_signal_hash(header, phase),
        )
        validate_returned_arms(self.arm_specs, result)
        return result


def _arm_record(
    harmonics: int,
    profile: SuppressionProfile,
    config: AhmedPhaseConfig,
    breath: dict,
    heart: dict,
    *,
    shared_signal_hash: str | None,
) -> dict:
    br_valid = breath["selected_bin"] is not None and breath["unique_maximum"]
    hr_valid = heart["selected_bin"] is not None and heart["unique_maximum"]
    record = _base_record(harmonics, profile, config, shared_signal_hash)
    record.update(
        br_valid=bool(br_valid),
        br_bpm=_bpm(breath["selected_hz"]) if br_valid else None,
        br_selected_bin=breath["selected_bin"],
        br_confidence=breath["reason"] or "ok",
        hr_valid=bool(hr_valid),
        hr_raw=_bpm(heart["selected_hz"]) if hr_valid else None,
        hr_selected_bin=heart["selected_bin"],
        hr_margin=heart["margin"],
        f_r_hz=breath["selected_hz"],
        rej_reason=heart["reason"],
        heart_evidence=heart,
        breath_evidence=breath,
    )
    return record
