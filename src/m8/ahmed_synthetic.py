"""Synthetic transfer control for the M8 Step 1b gate (base plan section 2.2).

Replaces Ahmed et al.'s real, even-harmonic pulse-radar return with this project's
coherent FMCW phase-extraction path, keeping the collision scenario and the fixed-H
accumulator. The result is an **adaptation, never a reproduction**.

The generator is deliberately conservative and its name says so:
`phase_fundamentals_only_transfer_v1`. For the declared two-sinusoid coherent phasor,
ideal phase extraction leaves exactly the breathing and heart *displacement fundamentals*
— the phasor's Bessel/mixed lines do not survive unwrap. "All-harmonic" in the roadmap
refers to the accumulator's q, 2q, ..., Hq convention, not to harmonics in this generator.

Circular complex noise is an explicit model adaptation forced by the complex phase input;
it is not presented as the only changed algebraic field. The reported SNR is the
**pre-extraction** value: the extracted-phase error is a nonlinear, wrapped function of
that noise and must never be described as 10 dB AWGN.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

import numpy as np

from src.m4.estimator_suite import canonical_plain, freeze_array
from src.respiration import extract_chest_phase
from src.window_pipeline import run_config_hash

__all__ = [
    "MODEL_ID",
    "SyntheticConfig",
    "SyntheticPhase",
    "build_phasor",
    "embed_single_channel_cube",
    "embed_production_geometry_cube",
    "extract_phase",
    "generate",
]

MODEL_ID = "phase_fundamentals_only_transfer_v1"

_C_M_S = 3.0e8
_FC_HZ = 6.7e9
#: Bin carrying the synthetic target in the fast-time range FFT.
_RANGE_BIN = 7
_N_ADC = 64
#: Production decode geometry, used by the aggregation oracle.
_N_CHIRPS = 32
_N_RX = 4


@dataclass(frozen=True)
class SyntheticConfig:
    """Base plan section 2.2 primary values. `n_fft=None` means native (addendum A2)."""

    prf_hz: float = 37.41985916275843
    breath_hz: float = 20.0 / 60.0
    heart_hz: float = 80.0 / 60.0
    breath_displacement_m: float = 0.020
    heart_displacement_m: float = 0.010
    theta0_rad: float = 0.0
    breaths: int = 5
    snr_db: float = 10.0
    seed: int = 42
    n_fft: int | None = None
    carrier_hz: float = _FC_HZ
    speed_of_light_m_s: float = _C_M_S

    @property
    def wavelength_m(self) -> float:
        return self.speed_of_light_m_s / self.carrier_hz

    @property
    def beta_breath(self) -> float:
        return 4.0 * np.pi * self.breath_displacement_m / self.wavelength_m

    @property
    def beta_heart(self) -> float:
        return 4.0 * np.pi * self.heart_displacement_m / self.wavelength_m

    @property
    def requested_duration_s(self) -> float:
        return self.breaths / self.breath_hz

    @property
    def sample_count(self) -> int:
        """`sample_count_rule=round` — 561 at the primary PRF, 300 at 20 Hz."""
        return int(round(self.requested_duration_s * self.prf_hz))

    def to_dict(self) -> dict:
        return canonical_plain(
            {
                "model_id": MODEL_ID,
                "prf_hz": self.prf_hz,
                "breath_hz": self.breath_hz,
                "heart_hz": self.heart_hz,
                "breath_displacement_m": self.breath_displacement_m,
                "heart_displacement_m": self.heart_displacement_m,
                "theta0_rad": self.theta0_rad,
                "breaths": self.breaths,
                "snr_db": self.snr_db,
                "seed": self.seed,
                "n_fft": self.n_fft,
                "carrier_hz": self.carrier_hz,
                "speed_of_light_m_s": self.speed_of_light_m_s,
                "sample_count": self.sample_count,
                "sample_count_rule": "round",
                "range_bin": _RANGE_BIN,
                "n_adc": _N_ADC,
                "noise_model": "circular_complex_pre_extraction",
            }
        )

    def config_hash(self) -> str:
        return run_config_hash(self.to_dict())


@dataclass(frozen=True)
class SyntheticPhase:
    """Everything the gate needs to audit one synthetic realization."""

    config_hash: str
    clean_phase: np.ndarray
    extracted_phase: np.ndarray
    reference_phase: np.ndarray
    clean_phasor: np.ndarray
    noisy_phasor: np.ndarray
    noise_power: float
    realized_snr_db: float
    max_clean_increment_rad: float
    min_branch_margin_rad: float
    max_reference_increment_rad: float
    cycle_offsets: np.ndarray
    n_cycle_slips: int
    max_oracle_residual_rad: float
    draw_hash: str
    clean_phasor_hash: str
    cube_hash: str


def _hash_array(array: np.ndarray, dtype: str) -> str:
    digest = sha256()
    values = np.asarray(array, dtype=dtype)
    digest.update(str(values.shape).encode("ascii"))
    digest.update(values.tobytes(order="C"))
    return digest.hexdigest()


def build_phasor(config: SyntheticConfig) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """Return (clean_phase, clean_phasor, noisy_phasor, diagnostics).

    Noise is circular complex, injected **before** phase extraction:
        n = sqrt(P_s / (2 * 10**(SNR_dB/10))) * (u + j v)
    with u, v independent float64 standard normals from PCG64(seed).
    """
    n = config.sample_count
    t = np.arange(n, dtype=np.float64) / config.prf_hz
    clean_phase = (
        config.theta0_rad
        + config.beta_breath * np.sin(2 * np.pi * config.breath_hz * t)
        + config.beta_heart * np.sin(2 * np.pi * config.heart_hz * t)
    )
    clean_phasor = np.exp(1j * clean_phase)

    rng = np.random.Generator(np.random.PCG64(config.seed))
    u = rng.standard_normal(n)
    v = rng.standard_normal(n)
    signal_power = float(np.mean(np.abs(clean_phasor) ** 2))
    sigma = np.sqrt(signal_power / (2.0 * 10 ** (config.snr_db / 10.0)))
    noise = sigma * (u + 1j * v)
    noisy_phasor = clean_phasor + noise

    noise_power = float(np.mean(np.abs(noise) ** 2))
    # Correctness is exact algebraic agreement with the declared equation, not closeness
    # of this finite draw to exactly 10 dB.
    expected_noise_power = float(
        signal_power / (2.0 * 10 ** (config.snr_db / 10.0)) * np.mean(u**2 + v**2)
    )
    increments = np.abs(np.diff(clean_phase))
    diagnostics = {
        "signal_power": signal_power,
        "noise_power": noise_power,
        "expected_noise_power": expected_noise_power,
        "realized_snr_db": float(10 * np.log10(signal_power / noise_power)),
        "max_clean_increment_rad": float(increments.max()),
        "min_branch_margin_rad": float(np.pi - increments.max()),
        "draw_hash": _hash_array(np.concatenate([u, v]), "<f8"),
        "clean_phasor_hash": _hash_array(clean_phasor, "<c16"),
    }
    return clean_phase, clean_phasor, noisy_phasor, diagnostics


def embed_single_channel_cube(phasor: np.ndarray) -> np.ndarray:
    """(N, 1, 1, 64) cube whose range-FFT bin 7 carries the slow-time phasor.

    The fast-time Hann/FFT coefficient at the exact bin is a positive real scale times the
    phasor, so the required phase is preserved. Cast to complex64 to match decoded
    production cubes.
    """
    m = np.arange(_N_ADC)
    tone = np.exp(1j * 2 * np.pi * _RANGE_BIN * m / _N_ADC)
    cube = phasor[:, None, None, None] * tone[None, None, None, :]
    return cube.astype(np.complex64)


def _channel_tables() -> tuple[np.ndarray, np.ndarray]:
    """Deterministic positive amplitudes and static phase offsets from integer (c, r).

    Fixed functions of the indices, recorded in the resolved config — not tuned values.
    """
    c = np.arange(_N_CHIRPS)[:, None]
    r = np.arange(_N_RX)[None, :]
    amplitudes = 1.0 + 0.25 * ((c % 5) / 4.0) + 0.5 * ((r % 3) / 2.0)
    offsets = (2 * np.pi) * (((3 * c + 7 * r) % 16) / 16.0)
    return amplitudes, offsets


def embed_production_geometry_cube(phasor: np.ndarray) -> np.ndarray:
    """(N, 32, 4, 64) aggregation oracle with per-channel gains and static offsets.

    A correctness oracle, not a second scientific model: `delta_before_mean` must return
    the same phase as the single-channel cube, because the conjugate product cancels every
    static offset and the mean of positive squared gains is a positive real scale.
    """
    amplitudes, offsets = _channel_tables()
    m = np.arange(_N_ADC)
    tone = np.exp(1j * 2 * np.pi * _RANGE_BIN * m / _N_ADC)
    channel = amplitudes * np.exp(1j * offsets)  # (chirps, rx)
    cube = (
        phasor[:, None, None, None]
        * channel[None, :, :, None]
        * tone[None, None, None, :]
    )
    return cube.astype(np.complex64)


def extract_phase(cube: np.ndarray) -> np.ndarray:
    """Run the unchanged production extraction path at the synthetic range bin."""
    return extract_chest_phase(cube, locked_bin=_RANGE_BIN, method="delta_before_mean")


def generate(config: SyntheticConfig) -> SyntheticPhase:
    """Build one realization and audit it against the declared oracles."""
    clean_phase, clean_phasor, noisy_phasor, diag = build_phasor(config)
    cube = embed_single_channel_cube(noisy_phasor)
    extracted = extract_phase(cube)

    # Model-relative phase-noise oracle: the extracted phase is compared against the
    # clean model plus the noise's own angular perturbation, not against unwrap(angle(z)),
    # which is only an implementation-consistency check.
    epsilon = np.angle(noisy_phasor * np.conj(clean_phasor))
    reference = clean_phase + epsilon
    reference_zeroed = reference - reference[0]
    residual = extracted - reference_zeroed
    cycle_offsets = np.round(residual / (2 * np.pi)).astype(np.int64)
    oracle_residual = np.abs(residual - 2 * np.pi * cycle_offsets)
    n_slips = int(np.count_nonzero(np.diff(cycle_offsets)))

    return SyntheticPhase(
        config_hash=config.config_hash(),
        clean_phase=freeze_array(clean_phase - clean_phase[0]),
        extracted_phase=freeze_array(extracted),
        reference_phase=freeze_array(reference_zeroed),
        clean_phasor=freeze_array(clean_phasor),
        noisy_phasor=freeze_array(noisy_phasor),
        noise_power=diag["noise_power"],
        realized_snr_db=diag["realized_snr_db"],
        max_clean_increment_rad=diag["max_clean_increment_rad"],
        min_branch_margin_rad=diag["min_branch_margin_rad"],
        max_reference_increment_rad=float(np.abs(np.diff(reference)).max()),
        cycle_offsets=freeze_array(cycle_offsets),
        n_cycle_slips=n_slips,
        max_oracle_residual_rad=float(oracle_residual.max()),
        draw_hash=diag["draw_hash"],
        clean_phasor_hash=diag["clean_phasor_hash"],
        cube_hash=_hash_array(cube, "<c8"),
    )
