"""Ahmed et al. Fig. 8(c)-(d) simulation and harmonic accumulation.

This module intentionally does not import the production respiration or heart-rate
estimators. It implements the paper's pulse-radar, single-TX/RX slow-time model on its
own terms, while exposing the source's contradictory heart-row suppression rules as
named profiles rather than hiding the disagreement.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
import math
from types import MappingProxyType
from typing import Any, Literal, Mapping

import numpy as np


SampleCountRule = Literal["round", "floor", "ceil"]
PowerMode = Literal["raw", "centered"]
Normalization = Literal["non_dc_mean", "matrix_eta"]
RowSupport = Literal["fixed_h", "candidate_row"]
Vital = Literal["breath", "heart"]
SuppressionProfile = Literal[
    "figure_visible_unsuppressed",
    "eq26_multiples_suppressed",
    "prose_low_or_equal_suppressed",
]

SUPPRESSION_PROFILES: tuple[SuppressionProfile, ...] = (
    "figure_visible_unsuppressed",
    "eq26_multiples_suppressed",
    "prose_low_or_equal_suppressed",
)

# Layer A operates on the repeated spectral frequency q=2f. Converting q to
# physiological rate therefore uses 60(q/2)=30q, not the usual 60q.
SPECTRAL_TO_PHYSIOLOGICAL_BPM = 30.0


@dataclass(frozen=True)
class LayerAProfile:
    """One immutable interpretation in the approved Layer A ambiguity register."""

    profile_id: str
    row_support: RowSupport
    harmonics: int | None
    normalization: Normalization
    score_functional: Literal["sum_of_spectral_magnitudes"]
    suppression_profile: SuppressionProfile
    paper_support: str
    layer_b_mapping: str
    layer_b_arm_id: str | None


def _fixed_profile(
    harmonics: int,
    normalization: Normalization,
    suppression_profile: SuppressionProfile,
    paper_support: str,
    layer_b_mapping: str,
    layer_b_arm_id: str | None,
) -> LayerAProfile:
    suppression_id = {
        "figure_visible_unsuppressed": "unsuppressed",
        "eq26_multiples_suppressed": "eq26",
        "prose_low_or_equal_suppressed": "prose",
    }[suppression_profile]
    return LayerAProfile(
        profile_id=(
            f"a_fixed_h{harmonics}_{normalization}_magnitude_{suppression_id}_v1"
        ),
        row_support="fixed_h",
        harmonics=harmonics,
        normalization=normalization,
        score_functional="sum_of_spectral_magnitudes",
        suppression_profile=suppression_profile,
        paper_support=paper_support,
        layer_b_mapping=layer_b_mapping,
        layer_b_arm_id=layer_b_arm_id,
    )


_FIXED_PROFILE_METADATA: tuple[
    tuple[Normalization, SuppressionProfile, str, str, str | None], ...
] = (
    (
        "matrix_eta",
        "figure_visible_unsuppressed",
        "PAPER AMBIGUITY INTERPRETATION: fixed H from Fig. 8 prose; eta from Eq. 23; unsuppressed from visible figure",
        "Layer A selection-equivalence audit only; corresponding accepted unsuppressed arm",
        None,
    ),
    (
        "matrix_eta",
        "eq26_multiples_suppressed",
        "PAPER AMBIGUITY INTERPRETATION combining fixed-H prose with direct Eq. 26 suppression",
        "Layer A audit only; corresponding accepted Eq. 26 arm",
        None,
    ),
    (
        "matrix_eta",
        "prose_low_or_equal_suppressed",
        "PAPER AMBIGUITY INTERPRETATION combining fixed-H prose with subsequent suppression prose",
        "Layer A audit only; corresponding accepted prose arm",
        None,
    ),
    (
        "non_dc_mean",
        "figure_visible_unsuppressed",
        "RECONSTRUCTION CONVENTION with source-supported fixed H and visible suppression behavior",
        "ahmed_phase_h{H}_figure_visible_unsuppressed",
        "ahmed_phase_h{H}_figure_visible_unsuppressed",
    ),
    (
        "non_dc_mean",
        "eq26_multiples_suppressed",
        "RECONSTRUCTION CONVENTION plus DIRECT PAPER SUPPORT for suppression",
        "ahmed_phase_h{H}_eq26_multiples_suppressed",
        "ahmed_phase_h{H}_eq26_multiples_suppressed",
    ),
    (
        "non_dc_mean",
        "prose_low_or_equal_suppressed",
        "RECONSTRUCTION CONVENTION plus DIRECT PAPER SUPPORT for prose rule",
        "ahmed_phase_h{H}_prose_low_or_equal_suppressed",
        "ahmed_phase_h{H}_prose_low_or_equal_suppressed",
    ),
)

_fixed_profiles = tuple(
    _fixed_profile(
        harmonics,
        normalization,
        suppression_profile,
        paper_support,
        layer_b_mapping.replace("{H}", str(harmonics)),
        (
            None
            if layer_b_arm_id is None
            else layer_b_arm_id.replace("{H}", str(harmonics))
        ),
    )
    for (
        normalization,
        suppression_profile,
        paper_support,
        layer_b_mapping,
        layer_b_arm_id,
    ) in _FIXED_PROFILE_METADATA
    for harmonics in (3, 5)
)

_candidate_profiles = (
    LayerAProfile(
        profile_id="a_candidate_row_matrix_eta_magnitude_unsuppressed_v1",
        row_support="candidate_row",
        harmonics=None,
        normalization="matrix_eta",
        score_functional="sum_of_spectral_magnitudes",
        suppression_profile="figure_visible_unsuppressed",
        paper_support="DIRECT PAPER SUPPORT / mathematical implication of displayed matrices; figure behavior supplies unsuppressed identity",
        layer_b_mapping="None—Layer A ambiguity audit only",
        layer_b_arm_id=None,
    ),
    LayerAProfile(
        profile_id="a_candidate_row_matrix_eta_magnitude_eq26_v1",
        row_support="candidate_row",
        harmonics=None,
        normalization="matrix_eta",
        score_functional="sum_of_spectral_magnitudes",
        suppression_profile="eq26_multiples_suppressed",
        paper_support="DIRECT PAPER SUPPORT from displayed matrices and Eq. 26",
        layer_b_mapping="None—Layer A ambiguity audit only",
        layer_b_arm_id=None,
    ),
    LayerAProfile(
        profile_id="a_candidate_row_matrix_eta_magnitude_prose_v1",
        row_support="candidate_row",
        harmonics=None,
        normalization="matrix_eta",
        score_functional="sum_of_spectral_magnitudes",
        suppression_profile="prose_low_or_equal_suppressed",
        paper_support="DIRECT PAPER SUPPORT from displayed matrices and suppression prose, but their combination is not uniquely specified",
        layer_b_mapping="None—Layer A ambiguity audit only",
        layer_b_arm_id=None,
    ),
)

LAYER_A_PROFILES: tuple[LayerAProfile, ...] = _fixed_profiles + _candidate_profiles
LAYER_A_PROFILE_REGISTER: Mapping[str, LayerAProfile] = MappingProxyType(
    {profile.profile_id: profile for profile in LAYER_A_PROFILES}
)


def layer_a_profile(profile_id: str) -> LayerAProfile:
    """Return one registered profile, rejecting undeclared interpretation drift."""
    try:
        return LAYER_A_PROFILE_REGISTER[profile_id]
    except KeyError as exc:
        raise ValueError(f"unknown Layer A profile_id {profile_id!r}") from exc


def _readonly(values: np.ndarray | list[float] | list[int] | list[bool]) -> np.ndarray:
    array = np.array(values, copy=True)
    array.setflags(write=False)
    return array


def _finite_number(name: str, value: Any, *, positive: bool = False) -> float:
    if type(value) not in (int, float):
        raise TypeError(f"{name} must be an int or float, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number) or (positive and number <= 0.0):
        qualifier = "finite and positive" if positive else "finite"
        raise ValueError(f"{name} must be {qualifier}, got {value!r}")
    return number


@dataclass(frozen=True)
class AhmedConfig:
    """Complete, validated scientific configuration for one simulation variant."""

    c_m_s: float = 3.0e8
    fc_hz: float = 6.7e9
    heart_hz: float = 80.0 / 60.0
    breath_hz: float = 20.0 / 60.0
    heart_displacement_m: float = 0.010
    breath_displacement_m: float = 0.020
    heart_amplitude: float = 1.0
    breath_amplitude: float = 1.0
    theta0_rad: float = 0.0
    duration_breaths: float = 5.0
    prf_bandwidth_multiplier: float = 5.0
    sample_count_rule: SampleCountRule = "round"
    n_fft: int = 4096
    snr_db: float = 10.0
    snr_power_mode: PowerMode = "raw"
    seed: int = 42
    breath_max_bpm: float = 25.0
    heart_max_bpm: float = 100.0
    normalization: Normalization = "non_dc_mean"

    def __post_init__(self) -> None:
        for name in (
            "c_m_s",
            "fc_hz",
            "heart_hz",
            "breath_hz",
            "heart_displacement_m",
            "breath_displacement_m",
            "duration_breaths",
            "prf_bandwidth_multiplier",
            "breath_max_bpm",
            "heart_max_bpm",
        ):
            _finite_number(name, getattr(self, name), positive=True)
        for name in ("heart_amplitude", "breath_amplitude"):
            _finite_number(name, getattr(self, name), positive=True)
        _finite_number("theta0_rad", self.theta0_rad)
        _finite_number("snr_db", self.snr_db)
        if type(self.n_fft) is not int or self.n_fft < 2:
            raise ValueError(f"n_fft must be an integer >= 2, got {self.n_fft!r}")
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError(f"seed must be a non-negative integer, got {self.seed!r}")
        if self.sample_count_rule not in ("round", "floor", "ceil"):
            raise ValueError(f"unsupported sample_count_rule {self.sample_count_rule!r}")
        if self.snr_power_mode not in ("raw", "centered"):
            raise ValueError(f"unsupported snr_power_mode {self.snr_power_mode!r}")
        if self.normalization not in ("non_dc_mean", "matrix_eta"):
            raise ValueError(f"unsupported normalization {self.normalization!r}")
        if self.sample_count < 2:
            raise ValueError(
                f"sample_count must be at least 2, got {self.sample_count} from "
                f"duration_breaths={self.duration_breaths!r}, breath_hz={self.breath_hz!r}, "
                f"and sample_count_rule={self.sample_count_rule!r}"
            )
        if self.breath_max_bpm <= 60.0 * self.breath_hz:
            raise ValueError("breath_max_bpm must exceed the simulated breathing rate")
        if self.heart_max_bpm <= 60.0 * self.heart_hz:
            raise ValueError("heart_max_bpm must exceed the simulated heart rate")
        if self.n_fft < self.sample_count:
            raise ValueError(
                f"n_fft={self.n_fft} is smaller than sample_count={self.sample_count}"
            )

    @property
    def wavelength_m(self) -> float:
        return self.c_m_s / self.fc_hz

    @property
    def heart_phase_index(self) -> float:
        return 4.0 * math.pi * self.heart_displacement_m / self.wavelength_m

    @property
    def breath_phase_index(self) -> float:
        return 4.0 * math.pi * self.breath_displacement_m / self.wavelength_m

    @property
    def heart_modulation_bandwidth_hz(self) -> float:
        return 2.0 * self.heart_hz * self.heart_phase_index

    @property
    def prf_hz(self) -> float:
        return self.prf_bandwidth_multiplier * self.heart_modulation_bandwidth_hz

    @property
    def requested_duration_s(self) -> float:
        return self.duration_breaths / self.breath_hz

    @property
    def sample_count_float(self) -> float:
        return self.prf_hz * self.requested_duration_s

    @property
    def sample_count(self) -> int:
        rule = {"round": round, "floor": math.floor, "ceil": math.ceil}[self.sample_count_rule]
        return int(rule(self.sample_count_float))

    @property
    def effective_duration_s(self) -> float:
        return self.sample_count / self.prf_hz

    @property
    def last_sample_time_s(self) -> float:
        return (self.sample_count - 1) / self.prf_hz

    @property
    def native_resolution_hz(self) -> float:
        return 1.0 / self.effective_duration_s

    def validate_harmonic_support(self, harmonics: int, vital: Vital) -> None:
        if type(harmonics) is not int or harmonics <= 0:
            raise ValueError(f"harmonics must be a positive integer, got {harmonics!r}")
        if vital not in ("breath", "heart"):
            raise ValueError(f"unsupported vital {vital!r}")
        maximum = (
            2.0 * self.breath_max_bpm / 60.0
            if vital == "breath"
            else 2.0 * self.heart_max_bpm / 60.0
        )
        required = harmonics * maximum
        nyquist = self.prf_hz / 2.0
        # A harmonic at Nyquist is degenerate for this positive-frequency
        # construction. The approved contract requires Hq to be strictly below it.
        if required >= nyquist:
            raise ValueError(
                f"{vital} H={harmonics} requires {required:.6g} Hz strictly below "
                f"Nyquist, but Nyquist is "
                f"{nyquist:.6g} Hz (prf_hz={self.prf_hz:.6g})"
            )

    def to_dict(self) -> dict[str, Any]:
        return {key: _builtin(value) for key, value in asdict(self).items()}


@dataclass(frozen=True)
class AhmedSignal:
    config_hash: str
    signal_hash: str
    effective_seed: int | None
    t_s: np.ndarray
    clean_heart: np.ndarray
    clean_breath: np.ndarray
    clean_combined: np.ndarray
    standard_normal: np.ndarray
    noise: np.ndarray
    samples: np.ndarray
    prf_hz: float
    requested_duration_s: float
    effective_duration_s: float
    last_sample_time_s: float
    noise_sigma: float
    reference_power: float
    requested_snr_db: float
    realized_snr_db: float


@dataclass(frozen=True)
class HAScoreResult:
    config_hash: str
    signal_hash: str
    effective_seed: int | None
    vital: Vital
    profile_id: str
    row_support: RowSupport
    harmonics: int | None
    suppression_profile: SuppressionProfile
    normalization: Normalization
    frequencies_hz: np.ndarray
    candidate_bins: np.ndarray
    harmonic_bins: np.ndarray
    harmonic_support: np.ndarray
    spectrum_frequencies_hz: np.ndarray
    spectrum_magnitude: np.ndarray
    scores_before_exclusion: np.ndarray
    scores: np.ndarray
    support_eligible: np.ndarray
    suppression_eligible: np.ndarray
    eligible: np.ndarray
    selected_index: int | None
    selected_bin: int | None
    selected_frequency_hz: float | None
    selected_bpm: float | None
    expected_frequency_hz: float
    expected_bin: int
    runner_up_bin: int | None
    runner_up_score: float | None
    score_margin: float | None
    valid: bool
    unique_maximum: bool
    status: str
    rejection_reason: str

    @property
    def selected_score(self) -> float | None:
        if self.selected_index is None:
            return None
        return float(self.scores[self.selected_index])

    @property
    def spectral_error_hz(self) -> float | None:
        if self.selected_frequency_hz is None:
            return None
        return abs(self.selected_frequency_hz - self.expected_frequency_hz)

    @property
    def rate_error_bpm(self) -> float | None:
        error = self.spectral_error_hz
        return None if error is None else 30.0 * error


def _builtin(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {str(key): _builtin(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_builtin(item) for item in value]
    return value


def config_hash(config: AhmedConfig | Mapping[str, Any]) -> str:
    payload = config.to_dict() if isinstance(config, AhmedConfig) else _builtin(dict(config))
    encoded = json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _signal_hash(model_config_hash: str, standard_normal: np.ndarray) -> str:
    digest = sha256()
    digest.update(model_config_hash.encode("ascii"))
    z = np.asarray(standard_normal, dtype="<f8")
    digest.update(str(z.shape).encode("ascii"))
    digest.update(z.tobytes(order="C"))
    return digest.hexdigest()


def simulate_eq14(
    config: AhmedConfig,
    rng: np.random.Generator,
    *,
    standard_normal: np.ndarray | None = None,
    effective_seed: int | None = None,
) -> AhmedSignal:
    """Simulate equation (14) at one fixed fast-time sample."""
    if not isinstance(rng, np.random.Generator):
        raise TypeError("rng must be numpy.random.Generator")
    if effective_seed is not None and (
        type(effective_seed) is not int or effective_seed < 0
    ):
        raise ValueError("effective_seed must be a non-negative exact int or None")
    n = config.sample_count
    t = np.arange(n, dtype=np.float64) / config.prf_hz
    heart = config.heart_amplitude * np.cos(
        config.heart_phase_index * np.sin(2.0 * math.pi * config.heart_hz * t)
        + config.theta0_rad
    )
    breath = config.breath_amplitude * np.cos(
        config.breath_phase_index * np.sin(2.0 * math.pi * config.breath_hz * t)
        + config.theta0_rad
    )
    clean = heart + breath
    power_signal = clean - np.mean(clean) if config.snr_power_mode == "centered" else clean
    reference_power = float(np.mean(np.square(power_signal)))
    if not math.isfinite(reference_power) or reference_power <= 0.0:
        raise ValueError(f"clean signal has invalid reference power {reference_power!r}")
    sigma = math.sqrt(reference_power / (10.0 ** (config.snr_db / 10.0)))
    if standard_normal is None:
        z = rng.standard_normal(n)
    else:
        z = np.asarray(standard_normal, dtype=np.float64)
        if z.shape != (n,) or not np.all(np.isfinite(z)):
            raise ValueError(
                f"standard_normal must be finite with shape {(n,)}, got {z.shape}"
            )
        z = np.array(z, copy=True)
    noise = sigma * z
    samples = clean + noise
    noise_power = float(np.mean(np.square(noise)))
    realized_snr = (
        float("inf")
        if noise_power == 0.0
        else 10.0 * math.log10(reference_power / noise_power)
    )
    model_config_hash = config_hash(config)
    return AhmedSignal(
        config_hash=model_config_hash,
        signal_hash=_signal_hash(model_config_hash, z),
        effective_seed=effective_seed,
        t_s=_readonly(t),
        clean_heart=_readonly(heart),
        clean_breath=_readonly(breath),
        clean_combined=_readonly(clean),
        standard_normal=_readonly(z),
        noise=_readonly(noise),
        samples=_readonly(samples),
        prf_hz=float(config.prf_hz),
        requested_duration_s=float(config.requested_duration_s),
        effective_duration_s=float(config.effective_duration_s),
        last_sample_time_s=float(config.last_sample_time_s),
        noise_sigma=float(sigma),
        reference_power=float(reference_power),
        requested_snr_db=float(config.snr_db),
        realized_snr_db=float(realized_snr),
    )


def _candidate_bounds(config: AhmedConfig, vital: Vital) -> tuple[float, float]:
    if vital == "breath":
        return 0.0, 2.0 * config.breath_max_bpm / 60.0
    if vital == "heart":
        return 2.0 * config.breath_hz, 2.0 * config.heart_max_bpm / 60.0
    raise ValueError(f"unsupported vital {vital!r}")


def _select_scores(
    scores: np.ndarray, candidate_bins: np.ndarray
) -> tuple[int | None, int | None, float | None, float | None, bool]:
    finite_indices = np.flatnonzero(np.isfinite(scores))
    if finite_indices.size == 0:
        return None, None, None, None, False
    finite_scores = scores[finite_indices]
    maximum = float(np.max(finite_scores))
    winners = finite_indices[finite_scores == maximum]
    selected_index = int(winners[0])
    selected_bin = int(candidate_bins[selected_index])
    other = finite_indices[finite_indices != selected_index]
    if other.size == 0:
        return selected_index, selected_bin, None, None, winners.size == 1
    ordered = sorted(
        ((float(scores[index]), int(candidate_bins[index])) for index in other),
        key=lambda pair: (-pair[0], pair[1]),
    )
    runner_score, runner_bin = ordered[0]
    return selected_index, selected_bin, runner_bin, runner_score, winners.size == 1


def accumulate_harmonics(
    spectrum_magnitude: np.ndarray,
    candidate_bins: np.ndarray,
    harmonics: int,
    normalization: Normalization = "non_dc_mean",
) -> tuple[np.ndarray, np.ndarray]:
    """Accumulate fixed-H FFT-bin harmonics before any eligibility masking.

    The returned pair is ``(harmonic_bins, scores)``. Keeping this operation separate
    makes equations (23)-(26) independently testable with hand-computable spectra and a
    literal matrix oracle.
    """
    spectrum = np.asarray(spectrum_magnitude, dtype=np.float64)
    bins = np.asarray(candidate_bins)
    if spectrum.ndim != 1 or spectrum.size < 2 or not np.all(np.isfinite(spectrum)):
        raise ValueError("spectrum_magnitude must be a finite one-dimensional array")
    if bins.ndim != 1 or bins.size == 0 or not np.issubdtype(bins.dtype, np.integer):
        raise ValueError("candidate_bins must be a nonempty one-dimensional integer array")
    if np.any(bins <= 0):
        raise ValueError("candidate_bins must exclude DC and contain only positive bins")
    if type(harmonics) is not int or harmonics <= 0:
        raise ValueError(f"harmonics must be a positive integer, got {harmonics!r}")
    if normalization not in ("non_dc_mean", "matrix_eta"):
        raise ValueError(f"unsupported normalization {normalization!r}")
    harmonic_numbers = np.arange(1, harmonics + 1, dtype=np.int64)
    harmonic_bins = bins.astype(np.int64, copy=False)[:, None] * harmonic_numbers[None, :]
    if np.any(harmonic_bins >= spectrum.size):
        raise ValueError("candidate harmonic lies outside spectrum_magnitude")
    divisor = harmonics if normalization == "non_dc_mean" else harmonics + 1
    scores = np.sum(spectrum[harmonic_bins], axis=1) / float(divisor)
    return _readonly(harmonic_bins), _readonly(scores)


def spectral_hz_to_physiological_bpm(spectral_frequency_hz: float) -> float:
    """Convert Ahmed's repeated-pattern frequency q=2f to physiological bpm."""
    q_hz = _finite_number("spectral_frequency_hz", spectral_frequency_hz)
    if q_hz < 0.0:
        raise ValueError("spectral_frequency_hz must be non-negative")
    return SPECTRAL_TO_PHYSIOLOGICAL_BPM * q_hz


def physiological_hz_to_spectral_hz(physiological_frequency_hz: float) -> float:
    """Map physiological frequency f to the Layer A candidate frequency q=2f."""
    frequency_hz = _finite_number(
        "physiological_frequency_hz", physiological_frequency_hz
    )
    if frequency_hz < 0.0:
        raise ValueError("physiological_frequency_hz must be non-negative")
    return 2.0 * frequency_hz


@dataclass(frozen=True)
class LayerAAccumulation:
    """Profile-resolved Layer A arrays before breathing-related suppression."""

    harmonic_bins: np.ndarray
    harmonic_support: np.ndarray
    support_eligible: np.ndarray
    scores: np.ndarray


def accumulate_layer_a_profile(
    spectrum_magnitude: np.ndarray,
    candidate_bins: np.ndarray,
    profile_id: str,
    *,
    nyquist_bin_exclusive: int,
) -> LayerAAccumulation:
    """Evaluate one approved profile using sums of individual magnitudes.

    ``nyquist_bin_exclusive`` is the first bin that is not strictly below
    Nyquist. For an even-length rFFT it is the Nyquist bin itself. Candidate-row
    profiles use every positive multiple below that boundary; fixed-H profiles
    retain their H columns and mark a row ineligible unless all H are supported.
    """
    profile = layer_a_profile(profile_id)
    spectrum = np.asarray(spectrum_magnitude, dtype=np.float64)
    bins = np.asarray(candidate_bins)
    if spectrum.ndim != 1 or spectrum.size < 2 or not np.all(np.isfinite(spectrum)):
        raise ValueError("spectrum_magnitude must be a finite one-dimensional array")
    if bins.ndim != 1 or bins.size == 0 or not np.issubdtype(bins.dtype, np.integer):
        raise ValueError("candidate_bins must be a nonempty one-dimensional integer array")
    if np.any(bins <= 0):
        raise ValueError("candidate_bins must exclude DC and contain only positive bins")
    if (
        type(nyquist_bin_exclusive) is not int
        or nyquist_bin_exclusive <= 1
        or nyquist_bin_exclusive > spectrum.size
    ):
        raise ValueError(
            "nyquist_bin_exclusive must be an exact integer in [2, spectrum size]"
        )
    if np.any(bins >= nyquist_bin_exclusive):
        raise ValueError("candidate bins must be strictly below Nyquist")

    if profile.row_support == "fixed_h":
        assert profile.harmonics is not None
        harmonic_numbers = np.arange(1, profile.harmonics + 1, dtype=np.int64)
        harmonic_bins = bins.astype(np.int64, copy=False)[:, None] * harmonic_numbers
        harmonic_support = harmonic_bins < nyquist_bin_exclusive
        support_eligible = np.all(harmonic_support, axis=1)

        # Unsupported locations are never read. Their score rows remain NaN so
        # Nyquist-equal and above-Nyquist candidates cannot enter selection.
        safe_bins = np.where(harmonic_support, harmonic_bins, 0)
        magnitude_sum = np.sum(
            np.where(harmonic_support, spectrum[safe_bins], 0.0), axis=1
        )
        divisor = (
            profile.harmonics
            if profile.normalization == "non_dc_mean"
            else profile.harmonics + 1
        )
        scores = np.where(support_eligible, magnitude_sum / float(divisor), np.nan)
    else:
        maximum_multiple_count = (nyquist_bin_exclusive - 1) // int(np.min(bins))
        harmonic_numbers = np.arange(1, maximum_multiple_count + 1, dtype=np.int64)
        harmonic_bins = bins.astype(np.int64, copy=False)[:, None] * harmonic_numbers
        harmonic_support = harmonic_bins < nyquist_bin_exclusive
        support_count = np.sum(harmonic_support, axis=1)
        support_eligible = support_count > 0
        safe_bins = np.where(harmonic_support, harmonic_bins, 0)
        magnitude_sum = np.sum(
            np.where(harmonic_support, spectrum[safe_bins], 0.0), axis=1
        )
        # The matrix row includes its DC column, which Eq. 23 subtracts from
        # the numerator after multiplication. Thus eta is 1 + positive support.
        divisor = 1 + support_count
        scores = np.where(support_eligible, magnitude_sum / divisor, np.nan)

    return LayerAAccumulation(
        harmonic_bins=_readonly(harmonic_bins),
        harmonic_support=_readonly(harmonic_support),
        support_eligible=_readonly(support_eligible),
        scores=_readonly(scores),
    )


def suppression_eligibility_mask(
    candidate_bins: np.ndarray,
    suppression_profile: SuppressionProfile,
    *,
    vital: Vital,
    breath_bin: int | None = None,
) -> np.ndarray:
    """Return exactly one of the three approved suppression interpretations."""
    if suppression_profile not in SUPPRESSION_PROFILES:
        raise ValueError(f"unsupported suppression_profile {suppression_profile!r}")
    if vital not in ("breath", "heart"):
        raise ValueError(f"unsupported vital {vital!r}")
    bins = np.asarray(candidate_bins)
    if bins.ndim != 1 or not np.issubdtype(bins.dtype, np.integer):
        raise ValueError("candidate_bins must be a one-dimensional integer array")
    eligible = np.ones(bins.size, dtype=bool)
    if vital == "breath":
        return _readonly(eligible)
    if breath_bin is None or type(breath_bin) is not int or breath_bin <= 0:
        raise ValueError("heart scoring requires a positive exact-int breath_bin")
    if suppression_profile == "eq26_multiples_suppressed":
        eligible = bins % breath_bin != 0
    elif suppression_profile == "prose_low_or_equal_suppressed":
        eligible = bins > breath_bin
    return _readonly(eligible)


def compute_layer_a_scores(
    signal: AhmedSignal,
    config: AhmedConfig,
    profile_id: str,
    *,
    vital: Vital,
    breath_bin: int | None = None,
) -> HAScoreResult:
    """Compute one registered Layer A score curve on FFT-bin fundamentals."""
    profile = layer_a_profile(profile_id)
    expected_config_hash = config_hash(config)
    if signal.config_hash != expected_config_hash:
        raise ValueError(
            "signal/config mismatch: signal was generated with config hash "
            f"{signal.config_hash}, scoring config hash is {expected_config_hash}"
        )
    if not math.isclose(signal.prf_hz, config.prf_hz, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError(
            f"signal/config PRF mismatch: {signal.prf_hz!r} vs {config.prf_hz!r}"
        )
    if not math.isclose(
        signal.effective_duration_s,
        config.effective_duration_s,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ValueError(
            "signal/config duration mismatch: "
            f"{signal.effective_duration_s!r} vs {config.effective_duration_s!r}"
        )
    if profile.harmonics is not None:
        config.validate_harmonic_support(profile.harmonics, vital)
    if signal.samples.shape != (config.sample_count,):
        raise ValueError(
            f"signal length {signal.samples.size} does not match config {config.sample_count}"
        )
    spectrum = np.abs(np.fft.rfft(signal.samples, n=config.n_fft))
    spectrum_frequencies = np.fft.rfftfreq(config.n_fft, d=1.0 / config.prf_hz)
    low_hz, high_hz = _candidate_bounds(config, vital)
    tolerance = np.finfo(float).eps * max(1.0, high_hz) * 8.0
    candidate_bins = np.flatnonzero(
        (spectrum_frequencies > low_hz + tolerance if vital == "breath"
         else spectrum_frequencies >= low_hz - tolerance)
        & (spectrum_frequencies <= high_hz + tolerance)
        & (spectrum_frequencies < config.prf_hz / 2.0)
    ).astype(np.int64)
    if candidate_bins.size == 0:
        raise ValueError(f"{vital} candidate domain is empty")
    nyquist_bin_exclusive = (
        spectrum.size - 1 if config.n_fft % 2 == 0 else spectrum.size
    )
    accumulation = accumulate_layer_a_profile(
        spectrum,
        candidate_bins,
        profile_id,
        nyquist_bin_exclusive=nyquist_bin_exclusive,
    )
    suppression_eligible = suppression_eligibility_mask(
        candidate_bins,
        profile.suppression_profile,
        vital=vital,
        breath_bin=breath_bin,
    )
    eligible = accumulation.support_eligible & suppression_eligible
    scores = np.where(eligible, accumulation.scores, np.nan)
    selected_index, selected_bin, runner_bin, runner_score, unique = _select_scores(
        scores, candidate_bins
    )
    expected_hz = physiological_hz_to_spectral_hz(
        config.breath_hz if vital == "breath" else config.heart_hz
    )
    expected_bin = int(np.argmin(np.abs(spectrum_frequencies - expected_hz)))
    target_eligible = bool(np.any(candidate_bins[eligible] == expected_bin))
    if selected_index is None:
        status = "invalid"
        rejection = "no_finite_eligible_scores"
    elif vital == "heart" and not target_eligible:
        status = "inconclusive_by_definition"
        rejection = "suppression_profile_excludes_expected_target"
    else:
        status = "ok"
        rejection = ""
    selected_frequency = (
        None if selected_bin is None else float(spectrum_frequencies[selected_bin])
    )
    selected_score = None if selected_index is None else float(scores[selected_index])
    margin = (
        None
        if selected_score is None or runner_score is None
        else float(selected_score - runner_score)
    )
    return HAScoreResult(
        config_hash=expected_config_hash,
        signal_hash=signal.signal_hash,
        effective_seed=signal.effective_seed,
        vital=vital,
        profile_id=profile.profile_id,
        row_support=profile.row_support,
        harmonics=profile.harmonics,
        suppression_profile=profile.suppression_profile,
        normalization=profile.normalization,
        frequencies_hz=_readonly(spectrum_frequencies[candidate_bins]),
        candidate_bins=_readonly(candidate_bins),
        harmonic_bins=accumulation.harmonic_bins,
        harmonic_support=accumulation.harmonic_support,
        spectrum_frequencies_hz=_readonly(spectrum_frequencies),
        spectrum_magnitude=_readonly(spectrum),
        scores_before_exclusion=accumulation.scores,
        scores=_readonly(scores),
        support_eligible=accumulation.support_eligible,
        suppression_eligible=suppression_eligible,
        eligible=_readonly(eligible),
        selected_index=selected_index,
        selected_bin=selected_bin,
        selected_frequency_hz=selected_frequency,
        selected_bpm=(
            None
            if selected_frequency is None
            else spectral_hz_to_physiological_bpm(selected_frequency)
        ),
        expected_frequency_hz=float(expected_hz),
        expected_bin=expected_bin,
        runner_up_bin=runner_bin,
        runner_up_score=runner_score,
        score_margin=margin,
        valid=bool(selected_index is not None and status == "ok"),
        unique_maximum=bool(unique),
        status=status,
        rejection_reason=rejection,
    )


def compute_ha_scores(
    signal: AhmedSignal,
    config: AhmedConfig,
    harmonics: int,
    suppression_profile: SuppressionProfile,
    *,
    vital: Vital,
    breath_bin: int | None = None,
) -> HAScoreResult:
    """Compatibility entry point for the accepted fixed-H reconstruction rows."""
    if type(harmonics) is not int or harmonics not in (3, 5):
        raise ValueError("registered fixed-H scoring requires harmonics 3 or 5")
    if suppression_profile not in SUPPRESSION_PROFILES:
        raise ValueError(f"unsupported suppression_profile {suppression_profile!r}")
    suppression_id = {
        "figure_visible_unsuppressed": "unsuppressed",
        "eq26_multiples_suppressed": "eq26",
        "prose_low_or_equal_suppressed": "prose",
    }[suppression_profile]
    profile_id = (
        f"a_fixed_h{harmonics}_{config.normalization}_magnitude_"
        f"{suppression_id}_v1"
    )
    return compute_layer_a_scores(
        signal,
        config,
        profile_id,
        vital=vital,
        breath_bin=breath_bin,
    )


def run_profile(
    signal: AhmedSignal,
    config: AhmedConfig,
    harmonics: int,
    suppression_profile: SuppressionProfile,
) -> tuple[HAScoreResult, HAScoreResult]:
    breath = compute_ha_scores(
        signal,
        config,
        harmonics,
        suppression_profile,
        vital="breath",
    )
    if breath.selected_bin is None:
        raise ValueError("breathing estimate is unavailable, so heart scoring cannot proceed")
    heart = compute_ha_scores(
        signal,
        config,
        harmonics,
        suppression_profile,
        vital="heart",
        breath_bin=breath.selected_bin,
    )
    return breath, heart


def run_layer_a_profile(
    signal: AhmedSignal,
    config: AhmedConfig,
    profile_id: str,
) -> tuple[HAScoreResult, HAScoreResult]:
    """Run one registered Layer A ambiguity profile for simulation auditing."""
    breath = compute_layer_a_scores(signal, config, profile_id, vital="breath")
    if breath.selected_bin is None:
        raise ValueError(
            "breathing estimate is unavailable, so heart scoring cannot proceed"
        )
    heart = compute_layer_a_scores(
        signal,
        config,
        profile_id,
        vital="heart",
        breath_bin=breath.selected_bin,
    )
    return breath, heart


def build_adapter_result(
    breath_result: HAScoreResult,
    heart_result: HAScoreResult,
    provenance: Mapping[str, Any],
) -> dict[str, object]:
    """Build an adapter-record-compatible native result without scorer integration."""
    if breath_result.vital != "breath" or heart_result.vital != "heart":
        raise ValueError("adapter inputs must be ordered as breath result, then heart result")
    if breath_result.harmonics != heart_result.harmonics:
        raise ValueError("adapter inputs use different harmonic counts")
    if breath_result.profile_id != heart_result.profile_id:
        raise ValueError("adapter inputs use different Layer A profiles")
    registered_profile = layer_a_profile(breath_result.profile_id)
    if registered_profile.layer_b_arm_id is None:
        raise ValueError(
            "candidate-row and matrix_eta audit profiles have no Layer B arm mapping"
        )
    if breath_result.config_hash != heart_result.config_hash:
        raise ValueError("adapter inputs were produced from different configurations")
    if breath_result.signal_hash != heart_result.signal_hash:
        raise ValueError("adapter inputs were produced from different signal realizations")
    if breath_result.suppression_profile != heart_result.suppression_profile:
        raise ValueError("adapter inputs use different suppression profiles")
    provenance_hash = str(provenance.get("config_hash", ""))
    if provenance_hash != breath_result.config_hash:
        raise ValueError(
            "adapter provenance config_hash does not match the result configuration"
        )
    raw = {
        "breath_candidate_bins": np.array(breath_result.candidate_bins, copy=True),
        "breath_scores": np.array(breath_result.scores, copy=True),
        "heart_candidate_bins": np.array(heart_result.candidate_bins, copy=True),
        "heart_scores": np.array(heart_result.scores, copy=True),
        "provenance": _builtin(dict(provenance)),
        "signal_hash": breath_result.signal_hash,
        "effective_seed": breath_result.effective_seed,
    }
    for value in raw.values():
        if isinstance(value, np.ndarray):
            value.setflags(write=False)
    reasons = [
        result.rejection_reason
        for result in (breath_result, heart_result)
        if result.rejection_reason
    ]
    return {
        "estimator_id": "ahmed_fig8_sim_v1",
        "config_hash": provenance_hash,
        "hr_valid": bool(heart_result.valid),
        "hr_raw": (
            float(heart_result.selected_bpm) if heart_result.selected_bpm is not None else None
        ),
        "br_valid": bool(breath_result.valid),
        "br_bpm": (
            float(breath_result.selected_bpm)
            if breath_result.selected_bpm is not None
            else None
        ),
        "br_confidence": "simulation_control",
        "f_r_hz": (
            float(breath_result.selected_bpm / 60.0)
            if breath_result.selected_bpm is not None
            else None
        ),
        "rej_reason": ";".join(reasons),
        "raw": raw,
    }


def amplitude_ratio_variant(config: AhmedConfig, ratio: float) -> AhmedConfig:
    """Change alpha_h/alpha_b while preserving alpha_h^2 + alpha_b^2."""
    ratio = _finite_number("amplitude ratio", ratio, positive=True)
    total_squared = config.heart_amplitude**2 + config.breath_amplitude**2
    breath = math.sqrt(total_squared / (ratio**2 + 1.0))
    return replace(config, heart_amplitude=ratio * breath, breath_amplitude=breath)


def derive_variant_seed(base_seed: int, variant_id: str) -> int:
    digest = sha256(f"{base_seed}:{variant_id}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % (2**63 - 1)
