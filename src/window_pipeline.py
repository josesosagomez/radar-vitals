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
import struct
from dataclasses import dataclass, field, fields
from pathlib import PurePath
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
#: Exactly what `_canonical` accepts, by EXACT type. Interpolated into every rejection
#: message so the advertised set and the implementation cannot drift apart (S0R-07).
#: This is precisely what YAML and JSON produce — nothing speculative (S0R-12).
_SUPPORTED_TYPES = "None, bool, int, float, str, list, tuple, dict"

#: Exact-type encoders for the atomic values. Keyed by `type(obj)`, never `isinstance`
#: — see `_canonical` for why.
_ATOMIC: dict = {
    type(None): lambda o: ["null", ""],
    bool: lambda o: ["bool", "1" if o else "0"],
    int: lambda o: ["int", str(o)],            # str: exact at arbitrary precision
    # Fixed big-endian IEEE-754 bytes, NOT repr: repr collapses every NaN sign and
    # payload to "nan", so distinct floats shared one encoding (S0R-15). Bytes are
    # injective over the whole float domain, and still distinguish 0.0 from -0.0.
    float: lambda o: ["float", struct.pack(">d", o).hex()],
    str: lambda o: ["str", o],
}


def _canonical(obj: Any, _active: set | None = None) -> list:
    """Encode a config value as an unambiguous `[type_tag, payload]` pair.

    **Every** node is tagged, including plain strings, so no value can collide with
    another value's encoding (S0R-01).

    **Dispatch is on `type(obj)` exactly — there are no `isinstance` checks at all.**
    A subclass may carry state this encoder cannot see, and encoding it as its base type
    silently discards that state while assigning it the base type's key. Not
    hypothetical: it made `np.ma.MaskedArray` hash identically to a plain array with a
    different mask (S0R-10), and made NumPy scalars subclassing `float`/`str` lose their
    dtype (S0R-07).

    **The accepted set covers this project's JSON/YAML-derived configs** — not everything
    those formats can express (`yaml.safe_load` also yields `date` and `set`, both
    rejected), and `tuple` is accepted although neither format produces one (S0R-17).
    Two speculative extensions were tried and both failed review:

    * *NumPy* — five Blocking findings across three rounds, five different mechanisms:
      subclass dispatch (S0R-07), non-terminating `.item()` recursion and
      class-name-vs-dtype (S0R-08), dropped `dtype.metadata` (S0R-09), erased mask state
      (S0R-10), and hashed alignment padding that made *equal* configs hash differently
      (S0R-11).
    * *`pathlib` paths* — the one `isinstance` exception left after round 4, and it
      reintroduced exactly the flaw exact-type dispatch had just removed:
      `PurePosixPath("a/b") != PureWindowsPath("a/b")` as values, yet both encode to
      `as_posix() == "a/b"` and collided (S0R-12).

    Neither appears in this project's real configs (`scripts/live_demo_config.yaml` and
    the `config` block of `run_metadata.json` contain only `NoneType`, `bool`, `int`,
    `float` and `str` — verified, not assumed). Both are now loud, named errors naming
    the conversion. Anything else outside the set is rejected the same way, so widening
    the contract is a deliberate act rather than an accident.

    **Cycles are rejected, not crashed into** (S0R-13). `_active` tracks the containers
    on the *current* traversal path, so a self-referential list or dict — which YAML
    anchors can express — raises a named `TypeError` rather than `RecursionError`, while
    the same subtree referenced twice side-by-side still hashes normally.
    """
    encode = _ATOMIC.get(type(obj))
    if encode is not None:
        return encode(obj)

    t = type(obj)
    if t is list or t is tuple or t is dict:
        if _active is None:
            _active = set()
        if id(obj) in _active:
            raise TypeError(
                f"run_config_hash cannot canonicalise a self-referential {t.__name__} "
                "(the config contains a reference cycle, which YAML anchors can create). "
                "A cycle has no finite canonical form. Break the cycle before hashing. "
                "Note repeated NON-cyclic references to the same object are fine."
            )
        _active.add(id(obj))
        try:
            if t is dict:
                # Sort on the PRECOMPUTED canonical key, and refuse ties. Two distinct
                # keys with one canonical form make the sort order depend on insertion
                # order, so equal dicts would hash differently (S0R-15).
                by_key: dict[str, list] = {}
                for k, v in obj.items():
                    ck = _canonical(k, _active)
                    kj = json.dumps(ck, separators=(",", ":"))
                    if kj in by_key:
                        raise TypeError(
                            "run_config_hash cannot canonicalise a dict with two "
                            f"distinct keys sharing one canonical form ({kj}). This "
                            "happens with NaN keys, which are never equal to each "
                            "other, so their order — and therefore the hash — would "
                            "depend on insertion order. Use finite, distinct keys."
                        )
                    by_key[kj] = [ck, _canonical(v, _active)]
                return ["dict", [by_key[k] for k in sorted(by_key)]]
            return [t.__name__, [_canonical(v, _active) for v in obj]]
        finally:
            _active.discard(id(obj))   # path-scoped, not global: siblings may repeat

    if isinstance(obj, (np.generic, np.ndarray)):
        raise TypeError(
            f"run_config_hash does not accept NumPy values (got {t.__name__!r}, dtype "
            f"{getattr(obj, 'dtype', '?')!r}). NumPy values can carry state that no "
            "byte-level encoding captures reliably — dtype metadata, masks, alignment "
            "padding — so hashing them risks either colliding distinct configs or "
            "giving the same config different keys. Convert first: float(x) / int(x) / "
            "bool(x) / str(x) for scalars, x.tolist() for arrays."
        )
    if isinstance(obj, PurePath):
        raise TypeError(
            f"run_config_hash does not accept pathlib paths (got {t.__name__!r}). "
            "Path flavours compare unequal but share one POSIX string — "
            'PurePosixPath("a/b") != PureWindowsPath("a/b") — so encoding by string '
            "collides distinct values. Convert explicitly: str(p) or p.as_posix(), "
            "whichever the config actually means."
        )
    raise TypeError(
        f"run_config_hash cannot canonicalise {t.__name__!r} deterministically "
        f"(value: {obj!r}). Note the accepted types are matched EXACTLY, so a subclass "
        "is rejected on purpose: it may carry state this encoder cannot see. Convert it "
        f"to a supported type first. Supported: {_SUPPORTED_TYPES}."
    )


def run_config_hash(cfg: dict) -> str:
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

    **The root must be an exact `dict`** — the shape every config in this project has.
    A non-dict root (including a `Mapping` subclass such as `MappingProxyType`) raises,
    so the annotation and the runtime agree; previously the signature advertised
    `Mapping[str, Any]` while rejecting `MappingProxyType` and silently accepting a
    *list* root (S0R-17).

    Nested values are accepted **by exact type**: `None`, `bool`, `int`, `float`, `str`,
    `list`, `tuple`, `dict`. That covers **this project's JSON/YAML-derived configs** —
    verified against `scripts/live_demo_config.yaml` and the `config` block of a real
    `run_metadata.json`, both of which contain only `NoneType`/`bool`/`int`/`float`/`str`.
    It is deliberately **not** a claim to cover everything those formats can express:
    `yaml.safe_load` can yield `date` and `set`, which are rejected, and `tuple` is
    accepted although neither format produces one.

    Everything else — **including all NumPy values and all `pathlib` paths** — raises
    `TypeError` naming the conversion, rather than being coerced. Reference cycles raise
    too, rather than exhausting the stack. See `_canonical` for why the set is exact and
    why the two speculative extensions were removed.
    """
    if type(cfg) is not dict:
        raise TypeError(
            f"run_config_hash needs an exact dict at the root, got {type(cfg).__name__}. "
            "A run config is a mapping; hashing a bare sequence or a Mapping subclass "
            "would make the annotation and the runtime disagree. Convert with dict(cfg)."
        )
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

    def _flag(key: str, vital: str) -> bool:
        """Read a validity flag without coercing it.

        `bool(...)` was worse than no check at all here (S0R-18): a foreign estimator
        reporting `hr_valid="false"` — or `"0"`, or `[0]` — had its disposition silently
        **reversed** into True, and the finite-rate invariant below then happily promoted
        a rejected window's rate into a paper-grade number. This is the boundary whose
        entire job is to stop exactly that.
        """
        if key not in dsp:
            return False                       # absent stays False, as before
        value = dsp[key]
        if type(value) is not bool:            # exact: np.bool_ and 0/1 are NOT sanctioned
            raise TypeError(
                f"{vital} validity flag {key!r} must be an exact bool, got "
                f"{type(value).__name__} ({value!r}) from estimator {estimator_id!r}. "
                "Truthiness coercion is refused here because it can reverse a rejection "
                "into a scored rate."
            )
        return value

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

    hr_valid = _flag("hr_valid", "HR")
    br_valid = _flag("br_valid", "BR")
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
