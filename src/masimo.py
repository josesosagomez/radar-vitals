"""Parse Masimo MightySat CSV exports into a tidy, time-indexed table.

Exact source format (1 Hz), as exported by the Masimo phone app:

    Session,Index,Timestamp,Date,Time,O2 Saturation,Beats / min,Perfusion Index,
    Pleth Variability,Breaths / min
    0,1,1778672295,5/13/26,2:38:15 PM,95,80,1.6,17,24
    ...

Key facts (do not "fix" these elsewhere):
- `Timestamp` is a Unix epoch in SECONDS, in UTC. This is the alignment key.
- The `Date`/`Time` strings are local time (e.g. UTC+3) and are for humans only — ambiguous
  (12-hour, M/D/YY); never parse them for alignment.
- Ground truth for HEART RATE is `Beats / min` (pulse rate, PR). NOT SpO2/PI/PVi.
- `Perfusion Index` is the signal-quality gate: low PI => PR less trustworthy.
- `Breaths / min` is available for cross-checking the radar respiration band.
"""
from __future__ import annotations

from pathlib import Path
import pandas as pd

# Map the messy source headers -> clean snake_case names used everywhere downstream.
_COLUMN_MAP = {
    "Session": "session",
    "Index": "index",
    "Timestamp": "epoch_utc",      # Unix seconds, UTC — the alignment key
    "O2 Saturation": "spo2",
    "Beats / min": "pr_bpm",       # pulse rate = heart-rate ground truth
    "Perfusion Index": "pi",       # quality gate
    "Pleth Variability": "pvi",
    "Breaths / min": "rr_bpm",     # respiration ground truth
}


def load_masimo(csv_path: str | Path) -> pd.DataFrame:
    """Load a Masimo CSV into a tidy DataFrame indexed by UTC datetime.

    Duplicate Timestamp rows (Masimo clock glitches) are merged: numeric
    columns take the mean rounded to 1 dp; non-numeric columns keep first.
    Coverage stats are stored in df.attrs:
        n_raw_rows, n_unique_epochs, n_duplicates_merged, n_missing_seconds.

    Returns a frame with columns:
        epoch_utc (int s), pr_bpm, spo2, pi, pvi, rr_bpm
    and a UTC DatetimeIndex named 'time_utc'. The integer `epoch_utc` column is kept
    for exact, timezone-free alignment with radar capture timestamps.
    """
    csv_path = Path(csv_path)
    raw = pd.read_csv(csv_path)

    missing = [c for c in _COLUMN_MAP if c not in raw.columns]
    if missing:
        raise ValueError(
            f"{csv_path.name} is missing expected Masimo columns: {missing}. "
            f"Found: {list(raw.columns)}"
        )

    # Deduplication: merge duplicate Timestamp rows caused by Masimo clock glitches.
    # Numeric columns: mean rounded to 1 dp. Non-numeric: keep first.
    # Coerce numeric columns first — raw exports may contain '--' outside the
    # recording window, which would make pandas read them as object/str dtype.
    n_raw    = len(raw)
    _numeric = ["O2 Saturation", "Beats / min", "Perfusion Index", "Pleth Variability", "Breaths / min"]
    _first   = ["Session", "Index", "Date", "Time"]
    for col in _numeric:
        if col in raw.columns:
            raw[col] = pd.to_numeric(raw[col], errors="coerce")
    agg = {c: "mean" for c in _numeric if c in raw.columns}
    agg.update({c: "first" for c in _first if c in raw.columns})
    raw = raw.groupby("Timestamp", sort=True, as_index=False).agg(agg)
    for col in _numeric:
        if col in raw.columns:
            raw[col] = raw[col].round(1)

    n_unique  = len(raw)
    n_merged  = n_raw - n_unique
    ts_min, ts_max = int(raw["Timestamp"].min()), int(raw["Timestamp"].max())
    n_missing = (ts_max - ts_min + 1) - n_unique
    print(f"Masimo: {n_raw} rows -> {n_unique} epochs ({n_merged} merged, {n_missing} missing seconds)")

    df = raw.rename(columns=_COLUMN_MAP)
    # Drop the human-readable Date/Time strings — we align on epoch only.
    df = df[list(_COLUMN_MAP.values())].copy()

    df["epoch_utc"] = df["epoch_utc"].astype("int64")
    for col in ("pr_bpm", "spo2", "pi", "pvi", "rr_bpm"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.sort_values("epoch_utc").reset_index(drop=True)
    df.index = pd.to_datetime(df["epoch_utc"], unit="s", utc=True)
    df.index.name = "time_utc"

    df.attrs["n_raw_rows"]          = n_raw
    df.attrs["n_unique_epochs"]     = n_unique
    df.attrs["n_duplicates_merged"] = n_merged
    df.attrs["n_missing_seconds"]   = n_missing
    return df


def window(df: pd.DataFrame, start_epoch: float, end_epoch: float) -> pd.DataFrame:
    """Crop the Masimo frame to [start_epoch, end_epoch) half-open (Unix seconds, UTC).

    Raises ValueError if end_epoch <= start_epoch.
    """
    if end_epoch <= start_epoch:
        raise ValueError(
            f"end_epoch must be greater than start_epoch "
            f"(got start={start_epoch}, end={end_epoch})."
        )
    return df[(df["epoch_utc"] >= start_epoch) & (df["epoch_utc"] < end_epoch)]


def reference_br(
    df: pd.DataFrame,
    start_epoch: float,
    end_epoch: float,
) -> float:
    """Window-mean Masimo Breaths/min over [start_epoch, end_epoch).

    Returns NaN if the window is empty or all rr_bpm values are NaN.
    """
    w = window(df, start_epoch, end_epoch)
    if len(w) == 0:
        return float("nan")
    return float(w["rr_bpm"].mean(skipna=True))


def reference_pr(
    df: pd.DataFrame,
    start_epoch: float,
    end_epoch: float,
    min_pi: float = 0.5,
) -> dict:
    """Window-mean reference pulse rate over a capture interval, with a PI quality gate.

    Rows with Perfusion Index below `min_pi` are excluded from the mean and counted as
    low-quality. Returns the mean PR, std, sample counts, and a quality flag so callers
    can decide whether to trust the reference for that window (see CLAUDE.md s.4: do not
    tune the radar to a low-quality reference segment).
    """
    w = window(df, start_epoch, end_epoch)
    if len(w) == 0:
        raise ValueError(
            f"No Masimo rows in [{start_epoch}, {end_epoch}). "
            "Check PC/phone clock sync and that the epochs are UTC seconds."
        )
    good = w[w["pi"] >= min_pi]
    used = good if len(good) > 0 else w
    return {
        "pr_bpm_mean": float(used["pr_bpm"].mean()),
        "pr_bpm_std": float(used["pr_bpm"].std(ddof=0)),
        "rr_bpm_mean": float(used["rr_bpm"].mean()),
        "n_total": int(len(w)),
        "n_good_pi": int(len(good)),
        "low_quality": bool(len(good) < 0.8 * len(w)),  # >20% low-PI => suspect window
    }
