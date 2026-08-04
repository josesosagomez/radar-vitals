"""The frozen HR/BR comparator specification, implemented.

`notes/comparator_prespec.md` (HR) and `notes/comparator_prespec_br.md` (BR) are
frozen and binding on every agreement number in the paper — a **transparency** claim
(the specs are written out in full and applied identically to every estimator
compared), never a claim about when they were written. Nothing in this project is
pre-registered; see CLAUDE.md §4. This module is the one place those specs are turned
into code.

Distinct from `src/masimo.py` (raw CSV parsing — untouched) and from
`src/compare.py`'s existing `compare()`/`metrics()` (mean-based, SUPERSEDED by the
frozen spec — left as-is, not deleted, since `paired_metrics`/`coverage_table` in
that same file are still reused elsewhere in a clearly-labeled supplementary role).

Every primary-gate constant below is a module constant, cited to its spec section —
**not** a function parameter (`plans/offline_scoring_script.md` OSR-10): no caller
can pass a keyword and silently move a frozen threshold while the result is still
labeled under `hr_reference`/`br_reference`'s name.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import masimo

# ── HR (notes/comparator_prespec.md §2.1/§2.2) ──────────────────────────────────
_HR_MIN_PI = 0.5
_HR_MIN_USABLE = 24
_HR_STATIONARITY_MAX_BPM = 5.0
_HR_SENSITIVITY_BPM = (3.0, 5.0, 8.0)

# ── BR (notes/comparator_prespec_br.md §2.1/§2.2) ───────────────────────────────
_BR_MIN_FINITE = 24
_BR_STATIONARITY_MAX_BPM = 2.0
_BR_SENSITIVITY_BPM = (2.0, 3.0, 5.0)

# CLAUDE.md / M4R-09: the quantile method must be named and passed explicitly at
# every call site, never left to a library default.
_QUANTILE_METHOD = "linear"


def hr_reference(df: pd.DataFrame, epoch_start: float, epoch_end: float) -> dict:
    """`notes/comparator_prespec.md` §2.1/§2.2 — the HR (PR) reference for one window.

    Usable = finite `pr_bpm` AND finite `pi` AND `pi >= 0.5` (the §2.1 clarification:
    one usable-sample set, used for the median, the stationarity
    quantiles and the coverage count alike — never a second, differently-defined
    denominator).

    Returns a dict with `n_total`, `n_finite_pr`, `n_pi_qualified`, `n_usable` (all
    reported separately per §2.1 — OSR-06: `n_finite_pr`/`n_pi_qualified` are
    per-window diagnostics only, never a second exclusion bucket alongside
    `n_usable`), `median_pr_bpm` (NaN if the usable set is empty), `coverage_ok`
    (`n_usable >= 24`), `spread_bpm` (p90-p10 of the usable set, `method="linear"`,
    NaN if the usable set is empty), `stationarity_ok` (`spread_bpm <= 5.0`, so a
    spread of exactly 5.0 is retained and NaN is never stationarity-ok),
    `admitted` (`coverage_ok and stationarity_ok`), and `sensitivity` —
    `{3.0: bool, 5.0: bool, 8.0: bool}`, each `spread_bpm <= threshold`, defined the
    same way regardless of `coverage_ok` (a caller aggregating the sensitivity
    fractions restricts the denominator to coverage-ok windows itself, mirroring
    `scripts/derive_br_comparator_evidence.py`'s convention).

    NOTE (OSR-06 R2, user decision): `comparator_prespec.md` §2.4's "excluded-by-PI"
    report category is superseded by §2.1's later single-usable-set clarification —
    there is no separate excluded-by-PI aggregate anywhere in this function or in
    any caller's reference marginal; `n_pi_qualified`/`n_finite_pr` remain per-window
    diagnostics only, never a second exclusion bucket.
    """
    w = masimo.window(df, epoch_start, epoch_end)
    n_total = int(len(w))

    pr = w["pr_bpm"].to_numpy(dtype=float)
    pi = w["pi"].to_numpy(dtype=float)
    finite_pr = np.isfinite(pr)
    pi_qualified = np.isfinite(pi) & (pi >= _HR_MIN_PI)
    usable = finite_pr & pi_qualified

    n_finite_pr = int(np.sum(finite_pr))
    n_pi_qualified = int(np.sum(pi_qualified))
    n_usable = int(np.sum(usable))
    coverage_ok = bool(n_usable >= _HR_MIN_USABLE)

    usable_pr = pr[usable]
    if n_usable > 0:
        median_pr_bpm = float(np.median(usable_pr))
        spread_bpm = float(
            np.percentile(usable_pr, 90, method=_QUANTILE_METHOD)
            - np.percentile(usable_pr, 10, method=_QUANTILE_METHOD)
        )
    else:
        median_pr_bpm = float("nan")
        spread_bpm = float("nan")

    stationarity_ok = bool(np.isfinite(spread_bpm) and spread_bpm <= _HR_STATIONARITY_MAX_BPM)
    admitted = bool(coverage_ok and stationarity_ok)
    sensitivity = {
        t: bool(np.isfinite(spread_bpm) and spread_bpm <= t) for t in _HR_SENSITIVITY_BPM
    }

    return {
        "n_total": n_total,
        "n_finite_pr": n_finite_pr,
        "n_pi_qualified": n_pi_qualified,
        "n_usable": n_usable,
        "coverage_ok": coverage_ok,
        "median_pr_bpm": median_pr_bpm,
        "spread_bpm": spread_bpm,
        "stationarity_ok": stationarity_ok,
        "admitted": admitted,
        "sensitivity": sensitivity,
    }


def br_reference(df: pd.DataFrame, epoch_start: float, epoch_end: float) -> dict:
    """`notes/comparator_prespec_br.md` §2.1/§2.2 — the BR (RRp) reference for one window.

    No PI gate on BR admissibility (PI is reported only as a per-window flag,
    never an exclusion — §2.2's "On PI" note). Returns `n_total`, `n_finite_rr`,
    `availability_ok` (`n_finite_rr >= 24`), `median_rr_bpm` (NaN if the finite set
    is empty), `spread_bpm` (p90-p10 of the finite set, `method="linear"`, NaN if
    empty), `stationarity_ok` (`spread_bpm <= 2.0`, strict `>` for exclusion so a
    spread of exactly 2.0 bpm is retained — M3R-39), `admitted`
    (`availability_ok and stationarity_ok`), `sensitivity` —
    `{2.0: bool, 3.0: bool, 5.0: bool}` — and `pi_median` (the window's median `pi`
    among finite values, reported for diagnostic/sensitivity use only; it never
    gates BR admissibility).
    """
    w = masimo.window(df, epoch_start, epoch_end)
    n_total = int(len(w))

    rr = w["rr_bpm"].to_numpy(dtype=float)
    pi = w["pi"].to_numpy(dtype=float)
    finite_rr = np.isfinite(rr)

    n_finite_rr = int(np.sum(finite_rr))
    availability_ok = bool(n_finite_rr >= _BR_MIN_FINITE)

    finite_rr_vals = rr[finite_rr]
    if n_finite_rr > 0:
        median_rr_bpm = float(np.median(finite_rr_vals))
        spread_bpm = float(
            np.percentile(finite_rr_vals, 90, method=_QUANTILE_METHOD)
            - np.percentile(finite_rr_vals, 10, method=_QUANTILE_METHOD)
        )
    else:
        median_rr_bpm = float("nan")
        spread_bpm = float("nan")

    stationarity_ok = bool(np.isfinite(spread_bpm) and spread_bpm <= _BR_STATIONARITY_MAX_BPM)
    admitted = bool(availability_ok and stationarity_ok)
    sensitivity = {
        t: bool(np.isfinite(spread_bpm) and spread_bpm <= t) for t in _BR_SENSITIVITY_BPM
    }

    finite_pi = pi[np.isfinite(pi)]
    pi_median = float(np.median(finite_pi)) if len(finite_pi) > 0 else float("nan")

    return {
        "n_total": n_total,
        "n_finite_rr": n_finite_rr,
        "availability_ok": availability_ok,
        "median_rr_bpm": median_rr_bpm,
        "spread_bpm": spread_bpm,
        "stationarity_ok": stationarity_ok,
        "admitted": admitted,
        "sensitivity": sensitivity,
        "pi_median": pi_median,
    }


def br_metronome_concordance(radar_br_bpm: float, commanded_rate_bpm: float) -> dict:
    """`notes/comparator_prespec_br.md` §2.5 — the paced target-concordance check.

    `RR_ref` (`br_reference`) remains the measured reference; this is reported
    ADDITIONALLY and separately, never blended into or substituted for RRp
    agreement (§2.5/§2.6). Only meaningful for a window with a known commanded
    rate — callers must not invoke this for a natural session or for a window
    whose span straddles a schedule transition.
    """
    radar_br_bpm = float(radar_br_bpm)
    commanded_rate_bpm = float(commanded_rate_bpm)
    target_error_bpm = (
        float("nan") if not np.isfinite(radar_br_bpm) else radar_br_bpm - commanded_rate_bpm
    )
    return {
        "radar_br_bpm": radar_br_bpm,
        "commanded_rate_bpm": commanded_rate_bpm,
        "target_error_bpm": target_error_bpm,
    }
