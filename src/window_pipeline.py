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
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

import numpy as np
from numpy.lib import format as _npformat

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


#: Exactly what `_canonical` accepts. Kept in the error message so the advertised
#: support and the implementation cannot drift apart (S0R-07).
_SUPPORTED_TYPES = (
    "None, bool, int, float, complex, str, Path, list, tuple, mapping, and NumPy "
    "scalars/arrays of any non-object dtype"
)


def _numpy_payload(x: "np.generic | np.ndarray") -> list:
    """Encode a NumPy value as `[dtype_descriptor, raw_bytes_hex]`.

    Deliberately does **not** recurse through `.item()` (S0R-08). Two reasons that
    approach was wrong:

    * `.item()` is not guaranteed to leave NumPy space — on this platform
      `np.longdouble("1.25").item()` is another `np.longdouble`, so the recursion never
      terminated and raised `RecursionError` instead of hashing or rejecting.
    * the tag was `type(obj).__name__`, which is not the dtype. Structured `np.void`
      scalars all share the name `void`, so `[("x", "<i4")]` and `[("y", "<i8")]`
      holding the same value hashed identically.

    The dtype descriptor is NumPy's own canonical serialisation (the one `.npy` files
    use), so it distinguishes byte order, itemsize and structured field names/offsets.
    Raw bytes then pin the value exactly, with no float formatting in the path.

    **Declared consequence:** identity is by *dtype*, not by scalar class. Where a
    platform makes two classes the same dtype — here `np.longdouble` is `float64` — the
    two hash identically. That is correct: the dtype is what determines the value's
    representation.

    Object dtype is rejected: its bytes are process-local pointers, so hashing them
    would produce a key that changes between runs of the same config.
    """
    if x.dtype.hasobject:
        raise TypeError(
            f"run_config_hash cannot canonicalise NumPy object dtype ({x.dtype!r}) "
            "deterministically: its buffer holds process-local pointers, so the same "
            f"config would hash differently between runs. Supported: {_SUPPORTED_TYPES}."
        )
    try:
        descr = _npformat.dtype_to_descr(x.dtype)
    except Exception as exc:                      # pragma: no cover - defensive
        raise TypeError(
            f"run_config_hash cannot canonicalise NumPy dtype {x.dtype!r} "
            f"deterministically: {exc}. Supported: {_SUPPORTED_TYPES}."
        ) from exc

    def _plain(d):                                 # tuples -> lists, for stable JSON
        if isinstance(d, (list, tuple)):
            return [_plain(v) for v in d]
        return d

    return [_plain(descr), x.tobytes().hex()]


def _canonical(obj: Any) -> list:
    """Encode a config value as an unambiguous `[type_tag, payload]` pair.

    **Every** node is tagged, including plain strings, so no value can ever collide with
    another value's encoding — the defect in the first version, which stringified
    non-JSON values and so hashed `Path("a")` identically to `"a"` and `np.int64(3)`
    identically to `"3"` (S0R-01).

    **NumPy is checked before the Python built-ins, and the order matters** (S0R-07):
    `np.float64`, `np.str_` and `np.complex128` *subclass* `float`/`str`/`complex`, while
    `np.int64` and `np.bool_` do not. Testing the built-ins first therefore preserved the
    dtype of some scalars and silently erased it for others — an inconsistency inside the
    very contract that was meant to remove ambiguity. NumPy scalars now always keep their
    dtype, encoded by `_numpy_payload` rather than by recursing through `.item()`
    (S0R-08).

    Unsupported types raise rather than being coerced: a provenance key must never
    quietly absorb something it cannot represent.
    """
    if obj is None:
        return ["null", ""]

    # ── NumPy first: several NumPy scalars subclass Python built-ins (S0R-07) ──
    if isinstance(obj, np.generic):
        return ["np.scalar", _numpy_payload(obj)]
    if isinstance(obj, np.ndarray):
        # Contiguous copy first: a view's buffer order must not change the hash.
        return ["ndarray", [list(obj.shape), _numpy_payload(np.ascontiguousarray(obj))]]

    if isinstance(obj, bool):                      # before int — bool subclasses int
        return ["bool", "1" if obj else "0"]
    if isinstance(obj, int):
        return ["int", str(obj)]                   # str: exact for arbitrary precision
    if isinstance(obj, float):
        return ["float", repr(obj)]                # repr round-trips exactly; nan/inf safe
    if isinstance(obj, complex):
        return ["complex", [repr(obj.real), repr(obj.imag)]]
    if isinstance(obj, str):
        return ["str", obj]
    if isinstance(obj, Path):
        return ["path", obj.as_posix()]
    if isinstance(obj, tuple):
        return ["tuple", [_canonical(v) for v in obj]]
    if isinstance(obj, list):
        return ["list", [_canonical(v) for v in obj]]
    if isinstance(obj, Mapping):
        items = sorted(
            ([_canonical(k), _canonical(v)] for k, v in obj.items()),
            key=lambda kv: json.dumps(kv[0], separators=(",", ":")),
        )
        return ["dict", items]
    raise TypeError(
        f"run_config_hash cannot canonicalise {type(obj).__name__!r} deterministically "
        f"(value: {obj!r}). Convert it first. Supported: {_SUPPORTED_TYPES}."
    )


def run_config_hash(cfg: Mapping[str, Any]) -> str:
    """Deterministic SHA256 over the **complete run config**.

    This is an *exact-run provenance* key and nothing else. It is deliberately sensitive
    to the whole config, including fields (display backend, output paths) that cannot
    affect the DSP: whole-config sensitivity is the safe direction for provenance, and
    the alternative — a hand-maintained list of "DSP-relevant" keys — rots silently as
    the config grows (S0R-01).

    **Do not use it as an estimator-equivalence or grouping key.** Two runs that differ
    only in a display setting produce different hashes here and that is correct
    behaviour. If M4 later needs "did these two runs use the same DSP?", that requires a
    separate, explicitly scoped hash — not a quiet redefinition of this one.

    Raises `TypeError` on a value it cannot canonicalise, rather than coercing it.
    """
    canonical = json.dumps(_canonical(cfg), separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, eq=False)
class WindowEstimate:
    """One window's estimator-agnostic result, tagged with its provenance.

    The record enforces a **two-way** invariant (S0R-02): a rate is NaN whenever its
    validity flag is False, and a True validity flag is guaranteed to carry a finite
    rate. A record whose disposition and value contradict each other cannot be
    constructed through `as_window_estimate`, so the scorer never has to guess which
    field wins. The scorer maps an invalid estimate to a *recorded* radar-NaN
    disposition, never to a missing window.

    `raw` keeps the estimator's native dict for evidence dumps and is excluded from
    equality.

    **Equality is NaN-aware and semantic** (S0R-03): two separately-constructed invalid
    records compare equal, which plain dataclass equality would not give (NaN != NaN).
    The record is deliberately **unhashable** — semantic NaN equality and hashing cannot
    both hold consistently, and nothing needs it in a set or dict key.

    **This summary record is not the Stage 3 equality oracle.** Plan §7 stage 3 requires
    full-precision agreement between M4 and a direct shared-DSP call; that comparison
    must be made against the native DSP/evidence payload, not against this lossy summary.
    """

    estimator_id: str
    run_config_hash: str
    hr_bpm: float
    hr_valid: bool
    br_bpm: float
    br_valid: bool
    br_confidence: str = "low"
    f_r_hz: float | None = None
    rejection_reason: str = ""
    raw: dict = field(default_factory=dict, repr=False, compare=False)

    __hash__ = None   # see class docstring: semantic NaN equality precludes hashing

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, WindowEstimate):
            return NotImplemented

        def _same(a, b) -> bool:
            if isinstance(a, float) and isinstance(b, float):
                return a == b or (np.isnan(a) and np.isnan(b))
            return a == b

        return all(
            _same(getattr(self, f.name), getattr(other, f.name))
            for f in fields(self)
            if f.compare
        )


def as_window_estimate(
    dsp: dict,
    *,
    estimator_id: str = ESTIMATOR_ID,
    run_config_hash: str,
) -> WindowEstimate:
    """Normalise a `run_window_dsp`-shaped result dict into a `WindowEstimate`.

    Enforces the record's two-way invariant at the boundary:

    * validity False → the rate is forced to NaN, so no downstream consumer can read a
      rate off an unverified window;
    * validity True → the rate **must** be present and finite, or this raises. A
      silently contradictory record (`hr_valid=True, hr_bpm=NaN`) would leave the scorer
      to guess which field wins, and CLAUDE.md §4 requires the failure be reported, not
      absorbed (S0R-02).

    `hr_bpm_smooth` and `fallback_hr_bpm` are deliberately NOT carried: the first is an
    online median (not paper-grade) and the second is a naive argmax (CLAUDE.md §4).
    """

    def _rate(valid: bool, value, vital: str, key: str) -> float:
        if not valid:
            return float("nan")
        if value is None:
            raise ValueError(
                f"{vital} is marked valid but {key!r} is missing from the estimator "
                f"result. A valid estimate must carry a finite rate (estimator "
                f"{estimator_id!r})."
            )
        rate = float(value)
        if not np.isfinite(rate):
            raise ValueError(
                f"{vital} is marked valid but {key!r} is {rate!r}. A valid estimate "
                f"must carry a finite rate (estimator {estimator_id!r})."
            )
        return rate

    hr_valid = bool(dsp.get("hr_valid", False))
    br_valid = bool(dsp.get("br_valid", False))
    f_r = dsp.get("f_r_hz")
    return WindowEstimate(
        estimator_id=estimator_id,
        run_config_hash=run_config_hash,
        hr_bpm=_rate(hr_valid, dsp.get("hr_raw"), "HR", "hr_raw"),
        hr_valid=hr_valid,
        br_bpm=_rate(br_valid, dsp.get("br_bpm"), "BR", "br_bpm"),
        br_valid=br_valid,
        br_confidence=str(dsp.get("br_confidence", "low")),
        f_r_hz=None if f_r is None else float(f_r),
        rejection_reason=str(dsp.get("rej_reason", "")),
        raw=dsp,
    )
