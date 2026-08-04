"""Radar-side feature table for the BR bin-selection study.

Production reports breathing rate from one range bin locked once at warmup and held
for the whole session. `HISTORY.md` 2026-08-04 measured the headroom: on four holdout
captures production got MAE 3.41 bpm while a reference-using per-window oracle got
1.38. This module builds the table needed to ask whether a rule learned from
**radar-side features only** can capture any of that gap.

Two invariants make the answer meaningful rather than circular, and both are enforced
here rather than documented:

1. **No reference-derived quantity may become a feature.** The reference labels the
   outcome at design time; it is not available at deploy time. `classify_columns`
   partitions every column into feature-eligible / reference-derived / key-or-diagnostic
   and raises on anything it has never seen, so a column added to either input CSV later
   must be classified rather than silently defaulted into the feature pool.
2. **Features are z-scored WITHIN each window, across that window's valid bins.** The
   rule is a ranking device over the 14 candidate bins of one window; nothing it sees
   carries an absolute scale. This matters concretely: the 2026-07-28/29 captures contain
   static reflectors returning more energy than the subject (`notes/protocol.md`), and
   subjects E/F/G will be a third scene again.

The BR admissibility gate (`src/comparator.py:br_reference`) gates **scoring only**. It
never touches feature construction — a deployed rule must never need the reference.

Distinct from `scripts/diagnose_bin_sweep.py` (which produces the radar-side CSV and
opens no Masimo file) and `scripts/diagnose_signal_presence.py` (which produces the
reference-free SNR/phase columns alongside reference-derived audit columns).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .comparator import br_reference
from .m4.window_grid import FRAMES_PER_WINDOW, build_window_grid

KEY_COLUMNS = ("capture_id", "k", "bin")

#: Subject map. Authoritative source is `notes/capture_inventory.md` "Subject map";
#: subject identity is not machine-recorded anywhere and cannot be recovered from the
#: artifacts, so it is transcribed here rather than derived.
SUBJECT_BY_CAPTURE = {
    "massimo1": "A", "massimo2": "A",
    "massimo3": "B", "sweep": "B",
    "massimo4": "C", "massimo5": "C",
    "massimo6": "D", "massimo7": "D",
}

#: Complete-window counts under the frozen grid (`notes/analysis_prespec.md` §7):
#: 180 s -> 6, 480 s -> 16, 600 s -> 20.
EXPECTED_WINDOWS_BY_CAPTURE = {
    "massimo1": 6, "massimo2": 6, "sweep": 16, "massimo3": 20,
    "massimo4": 20, "massimo5": 20, "massimo6": 20, "massimo7": 20,
}

#: `src/warmup_select.py:derive_candidate_bins` on the production config: the 0.8-1.4 m gate.
EXPECTED_BINS = tuple(range(19, 33))

#: Columns computed FROM the Masimo reference. Never features, under any sign.
#: Transcribed from `scripts/diagnose_signal_presence.py` ROW_COLUMNS. Note the `br_`/`hr_`
#: prefix is NOT a usable heuristic for this — `br_peak_snr_db`, `phase_std_rad`,
#: `br_argmax_bpm` and `hr_peak_snr_db` are all reference-free and permitted.
REFERENCE_DERIVED_COLUMNS = frozenset({
    "br_ref_bpm", "br_argmax_err_bpm", "br_oracle_snr_db", "br_decoy_beat_frac",
    "hr_ref_bpm", "hr_ref_admitted", "hr_ref_spread_bpm", "hr_argmax_err_bpm",
    "hr_oracle_snr_db", "hr_decoy_beat_frac", "h2_oracle_snr_db",
})

#: Radar-side but banned, with cause.
BANNED_COLUMNS = {
    "is_locked_bin": (
        "the RECORDED live lock from run_metadata.json, which for massimo1/massimo2/sweep "
        "was produced by pre-M2-fix code and is a documented mislock "
        "(notes/capture_inventory.md). Re-derive the lock with run_warmup_selection instead "
        "-- see scripts/simulate_bin_policy.py:current_code_lock."
    ),
    "br_bpm": "the estimate itself; use dev_consensus / spatial_dev / temporal_dev instead.",
}

#: Everything else: join keys, provenance, HR diagnostics, and radar-side columns that are
#: legal to read but are not offered to the search.
_KEY_OR_DIAGNOSTIC_COLUMNS = frozenset({
    "capture_id", "k", "bin", "frame_start", "frame_end", "range_m",
    "energy", "warmup_settled_energy_db", "warmup_energy_eligible",
    "dsp_failed", "dsp_error", "hr_valid", "hr_bpm", "fallback_hr_bpm", "rej_reason",
    "ahet_ratio_db_best", "accepted_candidate_rank", "n_eca_skipped", "k_max_eff",
    "f_r_hz", "br_valid", "hr_peak_snr_db", "br_argmax_bpm", "hr_argmax_bpm",
})

#: Source columns the search may use, after within-window z-scoring. Kept small and
#: deliberately enumerated: the search space must be exactly reportable, and every entry
#: here costs degrees of freedom against ~105 scorable windows and four subjects.
FEATURE_SOURCE_COLUMNS = (
    "rel_db_in_window",
    "energy_rank_in_window",
    "phase_std_rad",
    "br_peak_snr_db",
    "br_confidence",
    "spectrum_stage",
)

#: Derived within this module from `br_bpm` (radar-side). All three are "deviation"
#: quantities: larger = this bin disagrees more with its context.
DERIVED_FEATURES = ("dev_consensus", "spatial_dev", "temporal_dev")

FEATURE_NAMES = (
    "rel_db", "energy_rank", "phase_std", "br_snr", "conf_ord", "spectrum_stage",
    "dev_consensus", "spatial_dev", "temporal_dev",
)

#: Feature -> the numeric column it reads. `conf_ord` points at the ordinal that
#: `add_derived_features` computes, NOT at the raw `br_confidence` strings: pointing it at
#: the strings makes every value coerce to NaN, which z-scores to a constant zero and
#: contributes nothing while still consuming search-space slots. `feature_liveness` caught
#: exactly that, which is what it is for.
_SOURCE_OF_FEATURE = {
    "rel_db": "rel_db_in_window",
    "energy_rank": "energy_rank_in_window",
    "phase_std": "phase_std_rad",
    "br_snr": "br_peak_snr_db",
    "conf_ord": "conf_ord",
    "spectrum_stage": "spectrum_stage",
}

#: br_confidence as an ordinal. Higher = the fuser was more confident.
_CONFIDENCE_ORDER = {"low": 0.0, "medium": 1.0, "high": 2.0}

#: Neighbourhood half-width for `spatial_dev`, in bins.
_SPATIAL_HALF_WIDTH = 2

#: How many preceding windows `temporal_dev` may look back over. Causal by construction.
_TEMPORAL_LOOKBACK = 3


class ReferenceLeakError(ValueError):
    """A reference-derived or banned column reached the feature pool."""


class FeatureTableError(ValueError):
    """The merged table failed a structural invariant."""


# ── column classification ────────────────────────────────────────────────────


def classify_columns(columns) -> dict[str, set[str]]:
    """Partition columns into feature-eligible / reference-derived / other.

    Raises on an unclassified column. A permissive default here is exactly how a
    reference-derived quantity would slip into the feature pool unnoticed, so an
    unrecognised column is an error the human must resolve, not a warning.
    """
    cols = set(columns)
    known = (
        set(FEATURE_SOURCE_COLUMNS)
        | set(REFERENCE_DERIVED_COLUMNS)
        | set(BANNED_COLUMNS)
        | set(_KEY_OR_DIAGNOSTIC_COLUMNS)
    )
    unknown = cols - known
    if unknown:
        raise FeatureTableError(
            f"unclassified column(s) {sorted(unknown)}. Every column must be listed in "
            "FEATURE_SOURCE_COLUMNS, REFERENCE_DERIVED_COLUMNS, BANNED_COLUMNS or "
            "_KEY_OR_DIAGNOSTIC_COLUMNS in src/br_features.py before it can be used."
        )
    return {
        "feature_eligible": cols & set(FEATURE_SOURCE_COLUMNS),
        "reference_derived": cols & set(REFERENCE_DERIVED_COLUMNS),
        "banned": cols & set(BANNED_COLUMNS),
        "other": cols & _KEY_OR_DIAGNOSTIC_COLUMNS,
    }


def assert_no_reference_leak(feature_columns) -> None:
    """Guard the feature pool. Called before any search touches the table."""
    bad_ref = sorted(set(feature_columns) & REFERENCE_DERIVED_COLUMNS)
    if bad_ref:
        raise ReferenceLeakError(
            f"reference-derived column(s) {bad_ref} in the feature pool. These are computed "
            "from the Masimo reference and would not exist at deploy time; a rule using them "
            "is not a rule, it is the oracle."
        )
    bad_banned = sorted(set(feature_columns) & set(BANNED_COLUMNS))
    if bad_banned:
        why = "; ".join(f"{c}: {BANNED_COLUMNS[c]}" for c in bad_banned)
        raise ReferenceLeakError(f"banned column(s) in the feature pool -- {why}")


# ── merge ────────────────────────────────────────────────────────────────────


def load_merged(sweep_csv, presence_csv, *, strict_grid: bool = True) -> pd.DataFrame:
    """Keyed one-to-one merge of the bin-sweep and signal-presence tables.

    The two CSVs happen to be in identical row order today, so a positional join would
    work -- and would silently attach every feature to the wrong cell the first time
    either is regenerated with a different capture ordering. Hence `validate="one_to_one"`
    on the explicit key rather than `zip`.
    """
    sweep = pd.read_csv(sweep_csv)
    presence = pd.read_csv(presence_csv)

    for name, df in (("bin_sweep", sweep), ("signal_presence", presence)):
        missing = [c for c in KEY_COLUMNS if c not in df.columns]
        if missing:
            raise FeatureTableError(f"{name} CSV is missing key column(s) {missing}")
        if df[list(KEY_COLUMNS)].isna().any().any():
            raise FeatureTableError(f"{name} CSV has NaN in a join key")
        if df.duplicated(subset=list(KEY_COLUMNS)).any():
            raise FeatureTableError(f"{name} CSV has duplicate (capture_id, k, bin) rows")

    # Only the reference-free columns are taken from signal_presence; the reference-derived
    # ones come across separately and explicitly (see `attach_labels`), never as features.
    take = [c for c in presence.columns if c not in set(sweep.columns) - set(KEY_COLUMNS)]
    merged = sweep.merge(
        presence[take], on=list(KEY_COLUMNS), how="inner", validate="one_to_one"
    )
    if len(merged) != len(sweep) or len(merged) != len(presence):
        raise FeatureTableError(
            f"merge lost rows: sweep={len(sweep)}, presence={len(presence)}, "
            f"merged={len(merged)}. The two runs do not cover the same cells."
        )
    classify_columns(merged.columns)
    assert_structure(merged)
    if strict_grid:
        assert_grid(merged)
    return merged


def capture_suffix(capture_id: str) -> str:
    return str(capture_id).rsplit("_", 1)[-1]


def assert_structure(df: pd.DataFrame) -> None:
    """Shape invariants that hold for ANY capture: full bin set, complete window x bin grid.

    Split out from `assert_grid` so captures the study has not seen yet (subjects E, F, G) are
    still structurally checked. Only the per-capture window COUNT is capture-specific.
    """
    for cid, g in df.groupby("capture_id"):
        suffix = capture_suffix(cid)
        bins = tuple(sorted(g["bin"].unique()))
        if bins != EXPECTED_BINS:
            raise FeatureTableError(f"{suffix}: bins {bins}, expected {EXPECTED_BINS}")
        if len(g) != int(g["k"].nunique()) * len(EXPECTED_BINS):
            raise FeatureTableError(f"{suffix}: table is not a complete window x bin grid")
        ks = sorted(int(k) for k in g["k"].unique())
        if ks != list(range(len(ks))):
            raise FeatureTableError(f"{suffix}: window indices {ks} are not 0..n-1 contiguous")


def assert_grid(df: pd.DataFrame) -> None:
    """The frozen grid, checked rather than assumed (`notes/analysis_prespec.md` §7)."""
    assert_structure(df)
    for cid, g in df.groupby("capture_id"):
        suffix = capture_suffix(cid)
        if suffix not in EXPECTED_WINDOWS_BY_CAPTURE:
            raise FeatureTableError(f"unknown capture {cid!r}; not in the subject map")
        n_win = int(g["k"].nunique())
        if n_win != EXPECTED_WINDOWS_BY_CAPTURE[suffix]:
            raise FeatureTableError(
                f"{suffix}: {n_win} windows, expected "
                f"{EXPECTED_WINDOWS_BY_CAPTURE[suffix]} under the frozen 600-frame grid"
            )


# ── derived radar-side features ──────────────────────────────────────────────


def add_derived_features(df: pd.DataFrame, subject_map: dict | None = None) -> pd.DataFrame:
    """Add `dev_consensus`, `spatial_dev`, `temporal_dev` and the ordinal encodings.

    All three deviations are computed over **valid bins only** — an invalid bin's `br_bpm`
    is not an estimate and must not vote — and `temporal_dev` looks strictly backwards.
    A non-causal (prev+next) version scores better precisely because it peeks, so the
    causality is asserted by test, not left to review.

    `subject_map` overrides `SUBJECT_BY_CAPTURE` for captures outside the training eight
    (subjects E, F, G onward). It is required rather than inferred because subject identity
    is not machine-recorded anywhere — guessing it would silently break the by-subject folds
    that every honest number in this study rests on.
    """
    out = df.copy()
    smap = dict(SUBJECT_BY_CAPTURE)
    smap.update(subject_map or {})
    unknown = sorted({capture_suffix(c) for c in out["capture_id"].unique()} - set(smap))
    if unknown:
        raise FeatureTableError(
            f"no subject recorded for capture(s) {unknown}. Subject identity is not machine-"
            "recorded and must not be guessed; pass subject_map={'<suffix>': '<subject>'}."
        )
    out["subject"] = out["capture_id"].map(lambda c: smap[capture_suffix(c)])
    out["valid"] = out["br_valid"].astype(bool) & np.isfinite(
        pd.to_numeric(out["br_bpm"], errors="coerce")
    )
    out["conf_ord"] = out["br_confidence"].map(_CONFIDENCE_ORDER).astype(float)

    br = pd.to_numeric(out["br_bpm"], errors="coerce").to_numpy(dtype=float)
    br_valid_only = np.where(out["valid"].to_numpy(), br, np.nan)
    out["_br_valid_only"] = br_valid_only

    # dev_consensus: distance from the window's own median over valid bins.
    window_median = out.groupby(["capture_id", "k"])["_br_valid_only"].transform("median")
    out["dev_consensus"] = np.abs(br_valid_only - window_median.to_numpy(dtype=float))

    out["spatial_dev"] = _spatial_dev(out)
    out["temporal_dev"] = _temporal_dev(out)
    out = out.drop(columns=["_br_valid_only"])
    return out


def _positions(df: pd.DataFrame) -> pd.Series:
    """Row number of each label, so grouped work can write back positionally."""
    return pd.Series(np.arange(len(df), dtype=int), index=df.index)


def _spatial_dev(df: pd.DataFrame) -> np.ndarray:
    """|br_bpm - median(br_bpm of valid neighbours within +/- 2 bins, excluding self)|."""
    out = np.full(len(df), np.nan, dtype=float)
    pos_of = _positions(df)
    for (_cid, _k), g in df.groupby(["capture_id", "k"], sort=False):
        bins = g["bin"].to_numpy(dtype=int)
        vals = g["_br_valid_only"].to_numpy(dtype=float)
        rows = pos_of.loc[g.index].to_numpy(dtype=int)
        for i, b in enumerate(bins):
            if not np.isfinite(vals[i]):
                continue
            near = (np.abs(bins - b) <= _SPATIAL_HALF_WIDTH) & (bins != b)
            neigh = vals[near]
            neigh = neigh[np.isfinite(neigh)]
            if neigh.size:
                out[rows[i]] = abs(vals[i] - float(np.median(neigh)))
    return out


def _temporal_dev(df: pd.DataFrame) -> np.ndarray:
    """|br_bpm(k, b) - median(br_bpm(k-1 .. k-L, b) over valid)|. Strictly causal.

    Only windows STRICTLY BEFORE k contribute. A prev+next version scores better on this
    data, which is exactly why it must not be used: the improvement is peeking, and a live
    tracker has no window k+1.
    """
    out = np.full(len(df), np.nan, dtype=float)
    pos_of = _positions(df)
    for (_cid, _b), g in df.groupby(["capture_id", "bin"], sort=False):
        g = g.sort_values("k")
        ks = g["k"].to_numpy(dtype=int)
        vals = g["_br_valid_only"].to_numpy(dtype=float)
        rows = pos_of.loc[g.index].to_numpy(dtype=int)
        for i in range(len(ks)):
            if not np.isfinite(vals[i]):
                continue
            back = (ks < ks[i]) & (ks >= ks[i] - _TEMPORAL_LOOKBACK)
            hist = vals[back]
            hist = hist[np.isfinite(hist)]
            if hist.size:
                out[rows[i]] = abs(vals[i] - float(np.median(hist)))
    return out


# ── within-window z-scoring ──────────────────────────────────────────────────


def zscore_within_window(df: pd.DataFrame, feature_names=FEATURE_NAMES) -> pd.DataFrame:
    """Add `z_<name>` per feature: z-scored across the VALID bins of the same window.

    A window with fewer than two valid bins, or a feature that is constant across that
    window's valid bins, yields all-zero z for that window — the rule then falls through
    to its deterministic tie-break rather than selecting on numerical noise.
    """
    out = df.copy()
    source = {n: _SOURCE_OF_FEATURE.get(n, n) for n in feature_names}
    assert_no_reference_leak(source.values())

    valid = out["valid"].to_numpy(dtype=bool)
    keys = list(zip(out["capture_id"].to_numpy(), out["k"].to_numpy()))
    key_index: dict[tuple, list[int]] = {}
    for pos, key in enumerate(keys):
        key_index.setdefault(key, []).append(pos)

    for name in feature_names:
        raw = pd.to_numeric(out[source[name]], errors="coerce").to_numpy(dtype=float)
        z = np.zeros(len(out), dtype=float)
        for positions in key_index.values():
            idx = np.array(positions, dtype=int)
            live = idx[valid[idx]]
            if live.size < 2:
                continue
            vals = raw[live]
            finite = vals[np.isfinite(vals)]
            if finite.size < 2:
                continue
            mu = float(np.mean(finite))
            sd = float(np.std(finite))
            if sd <= 0.0:
                continue
            zz = (vals - mu) / sd
            z[live] = np.where(np.isfinite(zz), zz, 0.0)
        out[f"z_{name}"] = z
    return out


def feature_liveness(df: pd.DataFrame, feature_names=FEATURE_NAMES) -> pd.DataFrame:
    """Per feature: finite fraction, and the fraction of windows where it actually varies.

    The second number is the important one. `warmup_settled_energy_db` is empty on all
    1792 rows of the current sweep; a feature like that imputes to a constant, contributes
    nothing to any ranking, and still consumes a selection slot while inflating the
    reported size of the search space.
    """
    rows = []
    source = {n: _SOURCE_OF_FEATURE.get(n, n) for n in feature_names}
    valid = df["valid"].to_numpy(dtype=bool)
    pos_of = _positions(df)
    for name in feature_names:
        raw = pd.to_numeric(df[source[name]], errors="coerce").to_numpy(dtype=float)
        finite_frac = float(np.mean(np.isfinite(raw[valid]))) if valid.any() else 0.0
        varies = []
        for (_cid, _k), g in df.groupby(["capture_id", "k"], sort=False):
            pos = pos_of.loc[g.index].to_numpy(dtype=int)
            live = pos[valid[pos]]
            if live.size < 2:
                continue
            vals = raw[live]
            vals = vals[np.isfinite(vals)]
            varies.append(bool(vals.size >= 2 and float(np.std(vals)) > 0.0))
        rows.append({
            "feature": name,
            "source_column": source[name],
            "finite_frac_on_valid": round(finite_frac, 4),
            "frac_windows_varying": round(float(np.mean(varies)) if varies else 0.0, 4),
            "alive": bool(finite_frac >= 0.9 and (np.mean(varies) if varies else 0.0) >= 0.5),
        })
    return pd.DataFrame(rows)


# ── labels (scoring only — never a feature) ──────────────────────────────────


def window_labels(masimo_df: pd.DataFrame, frame0_epoch: float, n_windows: int) -> pd.DataFrame:
    """Per-window BR reference on the frozen grid, with the admissibility gate.

    `admitted` is `notes/comparator_prespec_br.md` §2.2: at least 24 finite RRp samples in
    the window AND p90-p10 <= 2.0 bpm. It is a reference-QUALITY gate, not an
    outcome-based exclusion, so it is compatible with `notes/analysis_prespec.md` §6 —
    admissibility is decided entirely from the Masimo channel, without reference to any
    radar estimate.

    `scripts/simulate_bin_policy.py:load_reference` kept any window with a finite median
    and skipped `admitted` entirely; that was a convenience, not a convention, and is
    carried here only as the labelled `finite` sensitivity.
    """
    grid = build_window_grid(
        n_windows * FRAMES_PER_WINDOW, float(frame0_epoch),
        fs=20.0, frames_per_win=FRAMES_PER_WINDOW,
    )
    rows = []
    for w in grid:
        ref = br_reference(masimo_df, w.epoch_start, w.epoch_end)
        rows.append({
            "k": int(w.k),
            "ref_bpm": float(ref["median_rr_bpm"]),
            "n_finite_rr": int(ref["n_finite_rr"]),
            "spread_bpm": float(ref["spread_bpm"]),
            "availability_ok": bool(ref["availability_ok"]),
            "stationarity_ok": bool(ref["stationarity_ok"]),
            "admitted": bool(ref["admitted"]),
        })
    return pd.DataFrame(rows)


def attach_labels(df: pd.DataFrame, labels_by_capture: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Join per-window labels and compute the per-cell absolute error.

    `err_bpm` is NaN wherever the bin is invalid or the window has no finite reference;
    it is the scoring quantity and never an input to any rule.
    """
    parts = []
    for cid, g in df.groupby("capture_id", sort=False):
        lab = labels_by_capture[cid]
        merged = g.merge(lab, on="k", how="left", validate="many_to_one")
        merged.index = g.index
        parts.append(merged)
    out = pd.concat(parts).loc[df.index]
    br = pd.to_numeric(out["br_bpm"], errors="coerce").to_numpy(dtype=float)
    ref = out["ref_bpm"].to_numpy(dtype=float)
    err = np.abs(br - ref)
    out["err_bpm"] = np.where(out["valid"].to_numpy(dtype=bool), err, np.nan)
    return out
