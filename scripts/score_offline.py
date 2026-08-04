"""Offline HR/BR scoring script (`plans/offline_scoring_script.md`, revision 7 — build authority).

Decodes a real capture's `adc_stream.bin`, runs the shared production DSP
(`src.window_pipeline.run_window_dsp`) per frozen 30 s / 600-frame window
(`src.m4.window_grid`), scores each window against the Masimo reference under the
frozen comparator spec (`src.comparator`), and reports MAE/RMSE/coverage.

This answers two questions with no prior number (`plans/offline_scoring_script.md`
"Context"): (1) whether `guard_cardiac_candidate_v1`'s measured coverage *gain* over
production `skip_forbidden_harmonics_v1` is also *correct* against Masimo, not just
more frequent; and (2) gives M8/M9 a reusable scoring path for their own estimators.

**Known, unavoidable limitation (OSR-01, user decision):** none of the existing
captures has a persisted true `frame0_epoch`. This script anchors every window grid
on `run_metadata.json`'s `start_wall_utc`, which is written *before* capture startup
and is therefore only approximate. Every output artifact is stamped
`origin_source="start_wall_utc_approximate"`, `origin_is_approximate=True`,
`comparator_status="exploratory_non_frozen"` — this is provisional evidence toward
the guard_v1 promotion decision, **never** a final or frozen score.

Output layout (per `plans/offline_scoring_script.md` "Output root"). `--out`
names a ROOT directory; a UTC-timestamped `run_id` subdirectory is always
appended beneath it (CLAUDE.md §3 rule 5 — every run logs to
`results/<experiment>/<timestamp>/`; mirrors `scripts/diagnose_bin_drift.py`'s
identical pattern, refusing to reuse an existing run directory):
    <out>/<run_id>/<capture_label>/<config_label>/<estimand>/window_scores.csv
    <out>/<run_id>/<capture_label>/<config_label>/<estimand>/evidence.npz
    <out>/<run_id>/<capture_label>/<config_label>/<estimand>/summary.json
A cross-config comparison (the incremental-coverage partition, Step 11, and the
supplementary `paired_metrics` report) is not scoped to one config directory, so it
is written one level up, per estimand:
<out>/<run_id>/<capture_label>/comparison_<estimand>.json (this specific filename
is this script's own choice — the plan pins the per-triple output root exactly but
leaves the cross-config comparison's path unspecified).
A run-level manifest aggregating every triple's provenance is written to
<out>/<run_id>/run_summary.json.

Usage: see `plans/offline_scoring_script.md` Verification §3 for the full
production-vs-guard_v1 invocation.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from src import masimo  # noqa: E402
from src.comparator import br_metronome_concordance, br_reference, hr_reference  # noqa: E402
from src.compare import coverage_table, paired_metrics  # noqa: E402
from src.m4.window_grid import FRAMES_PER_WINDOW, FRAME_RATE_HZ, Window, build_window_grid  # noqa: E402
from src.radar_io import read_adc_bin  # noqa: E402
from src.warmup_select import derive_candidate_bins, run_warmup_selection  # noqa: E402
from src.window_pipeline import (  # noqa: E402
    ESTIMATOR_ID,
    WindowEstimate,
    as_window_estimate,
    run_config_hash,
    run_window_dsp,
)

import diagnose_bin_drift as bindrift  # noqa: E402

# The classifier now lives in an importable module rather than in the executable
# diagnose_bin_drift script (M8 Step 1b plan section 4.1). Imported directly here so
# score_offline does not depend on the script for it.
from src.m4.outcome import classify_window_outcome  # noqa: E402

sha256_file = bindrift.sha256_file
sha256_bytes = bindrift.sha256_bytes
get_git_commit = bindrift.get_git_commit
is_tree_clean = bindrift.is_tree_clean
validate_decode_geometry = bindrift.validate_decode_geometry

STRICT_GATE_MODE = "strict_v1"
ORIGIN_CAVEAT = (
    "start_wall_utc is pre-capture-start and approximate; this row is provisional "
    "evidence, not a frozen score"
)
#: Captures taken from 2026-08-04 carry `frame0_epoch_utc`, stamped at receipt of the first
#: UDP packet. `start_wall_utc` is written before the DCA1000 and IWR1642 are configured, so
#: it precedes frame 0 by seconds — a 5-15 s error against a 30 s window grid. Where the true
#: origin exists it is used and the approximation caveat does not apply.
ORIGIN_EXACT_CAVEAT = ""


def resolve_frame0_epoch(capture_run_metadata: dict) -> tuple[float, str, bool, str]:
    """(frame0_epoch, origin_source, is_approximate, caveat).

    Prefers the recorded first-packet origin; falls back to `start_wall_utc` for the eight
    pre-2026-08-04 captures, which have no such field. The fallback is retained rather than
    made an error because those captures are still the project's entire dataset.
    """
    exact = capture_run_metadata.get("frame0_epoch_utc")
    if exact is not None:
        return (
            float(exact),
            str(capture_run_metadata.get("frame0_epoch_source") or "frame0_epoch_utc"),
            False,
            ORIGIN_EXACT_CAVEAT,
        )
    return (
        datetime.fromisoformat(capture_run_metadata["start_wall_utc"]).timestamp(),
        "start_wall_utc_approximate",
        True,
        ORIGIN_CAVEAT,
    )
HR_SENSITIVITY_BPM = (3.0, 5.0, 8.0)
BR_SENSITIVITY_BPM = (2.0, 3.0, 5.0)


# ─────────────────────────────────────────────────────────────────────────────
# CLI value parsing
# ─────────────────────────────────────────────────────────────────────────────

def parse_kv_list(items: Optional[list[str]], label: str) -> dict[str, str]:
    """Parse repeated ``key=value`` CLI tokens into a dict keyed by the LHS.

    Raises on a malformed token (no ``=``) or a duplicate key — a silently
    overwritten duplicate would hide a CLI typo behind whichever value came last.
    """
    out: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"{label}: malformed entry {item!r}, expected key=value")
        k, v = item.split("=", 1)
        if k in out:
            raise ValueError(f"{label}: duplicate key {k!r} (entries: {items})")
        out[k] = v
    return out


def parse_configs(items: list[str]) -> dict[str, Path]:
    raw = parse_kv_list(items, "--configs")
    return {label: Path(p) for label, p in raw.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Step 1b — session-type / paced-schedule / paced-target-unavailable validation
# ─────────────────────────────────────────────────────────────────────────────

def validate_session_type_combination(
    capture_ids: list[str],
    session_type: dict[str, str],
    paced_schedule: dict[str, str],
    paced_target_unavailable: dict[str, str],
) -> None:
    """OSR-19: every capture gets exactly one, explicit, non-inferred disposition.

    `--session-type` is required for every capture. A `natural` capture must get
    neither `--paced-schedule` nor `--paced-target-unavailable` (contradictory — a
    natural session has no commanded rate to be present or unavailable). A `paced`
    capture must get **exactly one** of the two — never neither (the "forgotten
    target" case that must fail loudly) and never both (ambiguous).
    """
    for cap in capture_ids:
        if cap not in session_type:
            raise ValueError(
                f"--session-type is required for every --captures entry; missing for "
                f"{cap!r} (OSR-19)."
            )
        st = session_type[cap]
        if st not in ("natural", "paced"):
            raise ValueError(
                f"--session-type for {cap!r} must be 'natural' or 'paced', got {st!r}."
            )
        has_schedule = cap in paced_schedule
        has_unavailable = cap in paced_target_unavailable
        if st == "natural":
            if has_schedule or has_unavailable:
                raise ValueError(
                    f"capture {cap!r} has --session-type=natural but was also given "
                    "--paced-schedule/--paced-target-unavailable; a natural session has "
                    "no commanded rate to be present or unavailable (contradictory)."
                )
        else:  # paced
            if has_schedule and has_unavailable:
                raise ValueError(
                    f"capture {cap!r} is paced but given BOTH --paced-schedule and "
                    "--paced-target-unavailable; exactly one is required (ambiguous)."
                )
            if not has_schedule and not has_unavailable:
                raise ValueError(
                    f"capture {cap!r} is paced but given NEITHER --paced-schedule nor "
                    "--paced-target-unavailable — this is the 'forgotten target' case "
                    "(OSR-19): it must fail loudly rather than silently score as if "
                    "natural or as if deliberately unavailable."
                )


# ─────────────────────────────────────────────────────────────────────────────
# Paced schedule (OSR-16 R2)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PacedSchedule:
    kind: str  # "constant" or "file"
    constant_bpm: Optional[float]
    #: (interval_start_s, interval_end_s, commanded_rate_bpm), relative to frame0_epoch.
    intervals: tuple[tuple[float, float, float], ...]
    source_path: Optional[Path]
    source_sha256: Optional[str]


def parse_paced_schedule_value(value: str) -> PacedSchedule:
    """A bare constant bpm, or a path to a committed, hashed interval-table file."""
    try:
        bpm = float(value)
        return PacedSchedule(
            kind="constant", constant_bpm=bpm, intervals=(),
            source_path=None, source_sha256=None,
        )
    except ValueError:
        pass
    path = Path(value)
    if not path.exists():
        raise ValueError(f"--paced-schedule value {value!r} is neither a number nor an existing file.")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    intervals = tuple(
        (float(iv["start_s"]), float(iv["end_s"]), float(iv["commanded_rate_bpm"]))
        for iv in data["intervals"]
    )
    return PacedSchedule(
        kind="file", constant_bpm=None, intervals=intervals,
        source_path=path, source_sha256=sha256_file(path),
    )


def lookup_commanded_rate(schedule: PacedSchedule, t_start_s: float, t_end_s: float) -> tuple[str, Optional[float]]:
    """Endpoint rule: a window's FULL span must lie inside one schedule interval.

    Returns (status, rate_or_None); status is "available" or
    "unavailable_transition" (never an arbitrary side's rate for a straddling
    window).
    """
    if schedule.kind == "constant":
        return "available", schedule.constant_bpm
    for lo, hi, rate in schedule.intervals:
        if lo <= t_start_s and t_end_s <= hi:
            return "available", rate
    return "unavailable_transition", None


# ─────────────────────────────────────────────────────────────────────────────
# Masimo CSV auto-discovery (OSR-11)
# ─────────────────────────────────────────────────────────────────────────────

def discover_masimo_csv(capture_dir: Path, override: Optional[str]) -> Path:
    """Exactly one *.csv in `capture_dir` must parse as a Masimo export, excluding
    `live_estimates.csv` by name (belt-and-suspenders even if a future radar output
    file accidentally had compatible-looking headers)."""
    if override is not None:
        p = Path(override)
        if not p.exists():
            raise ValueError(f"--masimo-csv override {p} does not exist.")
        return p
    all_csvs = sorted(capture_dir.glob("*.csv"))
    candidates = []
    for csv_path in all_csvs:
        if csv_path.name == "live_estimates.csv":
            continue
        try:
            masimo.load_masimo(csv_path)
        except Exception:
            continue
        candidates.append(csv_path)
    if len(candidates) != 1:
        raise ValueError(
            f"Masimo CSV auto-discovery for {capture_dir} found {len(candidates)} "
            f"candidate(s) (expected exactly 1): {[c.name for c in candidates]}. "
            f"All *.csv present: {[c.name for c in all_csvs]}. Use "
            f"--masimo-csv {capture_dir}=<path> to disambiguate (OSR-11)."
        )
    return candidates[0]


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — locked-bin resolution for both estimands
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LockProvenance:
    kind: str  # "manual" or "directory"
    locked_bin: int
    source_dir: Optional[str] = None
    matched_raw_hash: Optional[str] = None
    lock_source_eca_mode: Optional[str] = None
    lock_source_run_metadata_sha256: Optional[str] = None
    expected_eca_mode: Optional[str] = None
    expected_lock: Optional[int] = None


def resolve_pinned_lock(
    capture_id: str,
    raw_sha256: str,
    pinned_source_value: str,
    isolate_fields_active: bool,
    reproduction_baseline_eca_mode: dict[str, str],
    reproduction_baseline_lock: dict[str, str],
) -> LockProvenance:
    """Resolve one capture's pinned-estimand lock, per Step 4 / OSR-03 (rounds 1-4).

    A bare int is accepted unconditionally but tagged `kind="manual"` — it may
    never be captioned as reproducing the measured methodology. A directory
    source must hash-bind to the capture being scored (OSR-03 R2), and — whenever
    `--isolate-fields` is active for this run — must additionally match a
    declared `--reproduction-baseline-eca-mode` (OSR-03 R3) and
    `--reproduction-baseline-lock` (OSR-03 round 4) exactly.
    """
    try:
        as_int = int(pinned_source_value)
        return LockProvenance(kind="manual", locked_bin=as_int)
    except ValueError:
        pass

    source_dir = Path(pinned_source_value)
    meta_path = source_dir / "run_metadata.json"
    if not meta_path.exists():
        raise ValueError(
            f"--pinned-lock-source {source_dir} for capture {capture_id!r} has no "
            f"run_metadata.json and is not an integer bin either."
        )
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    mode = meta.get("mode")
    if mode == "replay":
        hashes = list((meta.get("replay_file_hashes") or {}).values())
        if raw_sha256 not in hashes:
            raise ValueError(
                f"--pinned-lock-source {source_dir} for capture {capture_id!r} is a replay "
                f"whose replay_file_hashes does not include this capture's raw SHA-256 "
                f"({raw_sha256}) — refusing to bind an unrelated lock source (OSR-03 R2)."
            )
    elif mode == "live":
        if meta.get("live_raw_mirror_hash") != raw_sha256:
            raise ValueError(
                f"--pinned-lock-source {source_dir} for capture {capture_id!r} is a live "
                f"capture whose live_raw_mirror_hash does not match this capture's raw "
                f"SHA-256 ({raw_sha256}) — refusing to bind an unrelated lock source "
                "(OSR-03 R2)."
            )
    else:
        raise ValueError(
            f"--pinned-lock-source {source_dir}: unrecognised run_metadata.json "
            f"mode={mode!r} (expected 'live' or 'replay')."
        )

    actual_eca_mode = meta["config"]["heart"]["eca_mode"]
    actual_lock = int(meta["locked_bin"])

    has_expected_eca = capture_id in reproduction_baseline_eca_mode
    has_expected_lock = capture_id in reproduction_baseline_lock

    if isolate_fields_active and not has_expected_eca:
        raise ValueError(
            f"--isolate-fields is set and capture {capture_id!r} uses a directory "
            "--pinned-lock-source: --reproduction-baseline-eca-mode is required for it "
            "(OSR-03 R3)."
        )
    if has_expected_eca and not has_expected_lock:
        raise ValueError(
            f"--reproduction-baseline-eca-mode is given for {capture_id!r}: "
            "--reproduction-baseline-lock is also required alongside it (OSR-03 round 4)."
        )
    if has_expected_lock and not has_expected_eca:
        raise ValueError(
            f"--reproduction-baseline-lock is given for {capture_id!r} without "
            "--reproduction-baseline-eca-mode; both must be given together."
        )

    expected_eca_mode = None
    expected_lock = None
    if has_expected_eca:
        expected_eca_mode = reproduction_baseline_eca_mode[capture_id]
        if actual_eca_mode != expected_eca_mode:
            raise ValueError(
                f"--pinned-lock-source {source_dir} for capture {capture_id!r} was "
                f"generated under heart.eca_mode={actual_eca_mode!r}, expected "
                f"{expected_eca_mode!r} (--reproduction-baseline-eca-mode) (OSR-03 R3)."
            )
        expected_lock = int(reproduction_baseline_lock[capture_id])
        if actual_lock != expected_lock:
            raise ValueError(
                f"--pinned-lock-source {source_dir} for capture {capture_id!r} carries "
                f"locked_bin={actual_lock}, expected {expected_lock} "
                "(--reproduction-baseline-lock) (OSR-03 round 4)."
            )

    return LockProvenance(
        kind="directory",
        locked_bin=actual_lock,
        source_dir=str(source_dir),
        matched_raw_hash=raw_sha256,
        lock_source_eca_mode=actual_eca_mode,
        lock_source_run_metadata_sha256=sha256_file(meta_path),
        expected_eca_mode=expected_eca_mode,
        expected_lock=expected_lock,
    )


def resolve_rerun_lock(cube: np.ndarray, candidate_bins: list[int], cfg: dict, validated_fs: float) -> tuple[int, dict]:
    """A fresh warmup selection for THIS config — may select a different bin than
    the pinned source (observed precedent: massimo1 under guard_cardiac_candidate_v1
    locks bin 25, not production's 27)."""
    warmup_cube = cube[:FRAMES_PER_WINDOW]
    selected_bin, _winning_dsp, evidence = run_warmup_selection(
        warmup_cube, candidate_bins, cfg, validated_fs
    )
    return selected_bin, evidence


# ─────────────────────────────────────────────────────────────────────────────
# Step 12 — ECA-isolation field diff (OSR-13/OSR-13 R2)
# ─────────────────────────────────────────────────────────────────────────────

def _flatten_dict(d: dict, prefix: tuple = ()) -> dict:
    out = {}
    for k, v in d.items():
        path = prefix + (str(k),)
        if isinstance(v, dict):
            out.update(_flatten_dict(v, path))
        else:
            out[path] = v
    return out


def diff_configs(cfg_a: dict, cfg_b: dict) -> dict[str, tuple]:
    """{dotted.path: (value_in_a, value_in_b)} for every leaf differing (or present
    in only one config)."""
    flat_a = _flatten_dict(cfg_a)
    flat_b = _flatten_dict(cfg_b)
    diffs: dict[str, tuple] = {}
    for path in set(flat_a) | set(flat_b):
        va = flat_a.get(path, "<missing>")
        vb = flat_b.get(path, "<missing>")
        if va != vb:
            diffs[".".join(path)] = (va, vb)
    return diffs


def assert_isolated_fields(cfg_a: dict, cfg_b: dict, allowlist: list[str]) -> dict[str, tuple]:
    """Raise unless the two configs differ EXACTLY at the declared allowlist paths
    (OSR-13), and unless every declared path actually differs (OSR-13 R2 — closes
    the vacuous-pass case of two identical, or a declared-but-unchanged, configs)."""
    diffs = diff_configs(cfg_a, cfg_b)
    if not diffs:
        raise ValueError(
            "--isolate-fields given but the two --configs are identical (empty diff) — "
            "two configs that do not actually differ cannot claim isolation (OSR-13 R2)."
        )
    outside = {p: v for p, v in diffs.items() if p not in allowlist}
    if outside:
        raise ValueError(
            f"--isolate-fields={allowlist} but the configs differ OUTSIDE the allowlist "
            f"too: {outside} (OSR-13)."
        )
    missing_declared = [f for f in allowlist if f not in diffs]
    if missing_declared:
        raise ValueError(
            f"--isolate-fields declared {missing_declared} but the configs do not "
            "actually differ there (OSR-13 R2 — an unchanged declared field cannot "
            "claim isolation)."
        )
    return {f: diffs[f] for f in allowlist}


# ─────────────────────────────────────────────────────────────────────────────
# Step 9 — evidence.npz: persist every key run_window_dsp returns, per window
# ─────────────────────────────────────────────────────────────────────────────

def _flatten_dsp_leaves(dsp: dict, prefix: str = "") -> dict:
    """Flatten one window's `run_window_dsp` dict (including nested `hr_result`,
    `br_result`, `fft_r`, `ha_r`, `stft_r`) into {flattened_key: value} leaves,
    joined with `__`. Generic over whatever those nested dicts contain, so the
    evidence contract does not silently drift from `run_window_dsp`'s own return
    shape (OSR-07 R2 — "persist every key `run_window_dsp` returns, not a
    hand-picked subset")."""
    out: dict = {}
    for k, v in dsp.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten_dsp_leaves(v, prefix=f"{key}__"))
        else:
            out[key] = v
    return out


def _stack_evidence_field(key: str, values: list) -> np.ndarray:
    """Stack one evidence field across windows. `run_window_dsp`'s `hr_result` does
    NOT carry an identical key set on every window — e.g. `ahet_second_harmonic_hz`
    is only present when a candidate is actually accepted — so a window missing
    this key gets `None` from `dict.get`, filled here with the type-appropriate
    "missing" sentinel (NaN for numeric/array fields, False for bool, "" for str)
    rather than raising on the naive assumption that every window's dict has the
    same keys."""
    sample = next((v for v in values if v is not None), None)
    if sample is None:
        return np.array([np.nan if v is None else float(v) for v in values], dtype=float)
    if isinstance(sample, (bool, np.bool_)):
        return np.array([False if v is None else bool(v) for v in values], dtype=bool)
    if isinstance(sample, (int, np.integer)):
        if any(v is None for v in values):
            return np.array([np.nan if v is None else float(v) for v in values], dtype=float)
        return np.array(values, dtype=np.int64)
    if isinstance(sample, (float, np.floating)):
        return np.array([np.nan if v is None else float(v) for v in values], dtype=float)
    if isinstance(sample, str):
        return np.array([("" if v is None else v) for v in values], dtype=object)
    if isinstance(sample, (tuple, list)):
        sample_arr = np.asarray(sample, dtype=float)
        arrs = [
            np.full(sample_arr.shape, np.nan) if v is None else np.asarray(v, dtype=float)
            for v in values
        ]
        shapes = {a.shape for a in arrs}
        if len(shapes) != 1:
            raise ValueError(
                f"evidence field {key!r} (tuple/list) has varying shape across windows "
                f"({sorted(str(s) for s in shapes)})."
            )
        return np.stack(arrs)
    if isinstance(sample, np.ndarray):
        sample_shape = sample.shape
        arrs = [
            np.full(sample_shape, np.nan) if v is None else np.asarray(v) for v in values
        ]
        shapes = {a.shape for a in arrs}
        if len(shapes) != 1:
            raise ValueError(
                f"evidence field {key!r} has varying shape across windows "
                f"({sorted(str(s) for s in shapes)}); the plan expects a constant "
                "shape per config since window length is fixed at 600 frames."
            )
        return np.stack(arrs)
    raise TypeError(f"evidence field {key!r} has unsupported type {type(sample)!r}")


def build_evidence_arrays(
    raw_dsp_per_window: list[dict],
    k_values: list[int],
    frame_starts: list[int],
    frame_ends: list[int],
    epoch_starts: list[float],
    epoch_ends: list[float],
    locked_bin: int,
) -> dict[str, np.ndarray]:
    """Flatten + stack every window's full `run_window_dsp` dict into one
    savez-able dict, plus the window-index/frame/epoch bookkeeping and the
    (constant, per this triple) locked bin (OSR-07/OSR-07 R2)."""
    if not raw_dsp_per_window:
        return {
            "k": np.array(k_values, dtype=np.int64),
            "frame_start": np.array(frame_starts, dtype=np.int64),
            "frame_end": np.array(frame_ends, dtype=np.int64),
            "epoch_start": np.array(epoch_starts, dtype=float),
            "epoch_end": np.array(epoch_ends, dtype=float),
            "locked_bin": np.array([locked_bin], dtype=np.int64),
        }
    flats = [_flatten_dsp_leaves(dsp) for dsp in raw_dsp_per_window]
    # Union of keys, not just window 0's — some hr_result keys (e.g.
    # ahet_second_harmonic_hz) are only present on windows where a candidate was
    # actually accepted, so a fixed-to-window-0 key set would KeyError on an
    # ordinary gate-not-run/all-rejected window.
    keys: list[str] = []
    seen: set[str] = set()
    for f in flats:
        for k in f.keys():
            if k not in seen:
                seen.add(k)
                keys.append(k)
    stacked: dict[str, np.ndarray] = {}
    for key in keys:
        values = [f.get(key) for f in flats]
        stacked[key] = _stack_evidence_field(key, values)
    stacked["k"] = np.array(k_values, dtype=np.int64)
    stacked["frame_start"] = np.array(frame_starts, dtype=np.int64)
    stacked["frame_end"] = np.array(frame_ends, dtype=np.int64)
    stacked["epoch_start"] = np.array(epoch_starts, dtype=float)
    stacked["epoch_end"] = np.array(epoch_ends, dtype=float)
    stacked["locked_bin"] = np.array([locked_bin], dtype=np.int64)
    return stacked


# ─────────────────────────────────────────────────────────────────────────────
# Step 10 — reference / radar / joint marginals
# ─────────────────────────────────────────────────────────────────────────────

def compute_hr_reference_marginal(hr_refs_by_k: dict[int, dict]) -> dict:
    """Radar/config-independent (Step 10) — same for every config/estimand on a
    given capture. No separate excluded-by-PI bucket (OSR-06 R2)."""
    n_windows = len(hr_refs_by_k)
    n_excluded_coverage = sum(1 for r in hr_refs_by_k.values() if not r["coverage_ok"])
    n_excluded_stationarity = sum(
        1 for r in hr_refs_by_k.values() if r["coverage_ok"] and not r["stationarity_ok"]
    )
    n_admitted = sum(1 for r in hr_refs_by_k.values() if r["admitted"])
    coverage_ok_n = sum(1 for r in hr_refs_by_k.values() if r["coverage_ok"])
    sensitivity = {}
    for t in HR_SENSITIVITY_BPM:
        passing = sum(1 for r in hr_refs_by_k.values() if r["coverage_ok"] and r["sensitivity"][t])
        sensitivity[str(t)] = (passing / coverage_ok_n) if coverage_ok_n > 0 else None
    return {
        "n_windows": n_windows,
        "n_excluded_coverage": n_excluded_coverage,
        "n_excluded_stationarity": n_excluded_stationarity,
        "n_admitted": n_admitted,
        "sensitivity_fraction_at_bpm": sensitivity,
    }


def compute_br_reference_marginal(br_refs_by_k: dict[int, dict]) -> dict:
    n_windows = len(br_refs_by_k)
    n_excluded_availability = sum(1 for r in br_refs_by_k.values() if not r["availability_ok"])
    n_excluded_stationarity = sum(
        1 for r in br_refs_by_k.values() if r["availability_ok"] and not r["stationarity_ok"]
    )
    n_admitted = sum(1 for r in br_refs_by_k.values() if r["admitted"])
    avail_ok_n = sum(1 for r in br_refs_by_k.values() if r["availability_ok"])
    sensitivity = {}
    for t in BR_SENSITIVITY_BPM:
        passing = sum(1 for r in br_refs_by_k.values() if r["availability_ok"] and r["sensitivity"][t])
        sensitivity[str(t)] = (passing / avail_ok_n) if avail_ok_n > 0 else None
    return {
        "n_windows": n_windows,
        "n_excluded_availability": n_excluded_availability,
        "n_excluded_stationarity": n_excluded_stationarity,
        "n_admitted": n_admitted,
        "sensitivity_fraction_at_bpm": sensitivity,
    }


def compute_radar_marginal_hr(estimates_by_k: dict[int, WindowEstimate], outcome_by_k: dict[int, str]) -> dict:
    n_valid = sum(1 for e in estimates_by_k.values() if e.hr_valid)
    n_radar_nan = len(estimates_by_k) - n_valid
    outcome_breakdown: dict[str, int] = {}
    for cls in outcome_by_k.values():
        outcome_breakdown[cls] = outcome_breakdown.get(cls, 0) + 1
    return {"n_valid": n_valid, "n_radar_nan": n_radar_nan, "outcome_breakdown": outcome_breakdown}


def compute_radar_marginal_br(estimates_by_k: dict[int, WindowEstimate]) -> dict:
    n_valid = sum(1 for e in estimates_by_k.values() if e.br_valid)
    n_radar_nan = len(estimates_by_k) - n_valid
    return {"n_valid": n_valid, "n_radar_nan": n_radar_nan}


def compute_joint_hr(hr_refs_by_k: dict[int, dict], estimates_by_k: dict[int, WindowEstimate]) -> dict:
    cross = {"admitted_valid": 0, "admitted_nan": 0, "excluded_valid": 0, "excluded_nan": 0}
    errs = []
    for k, ref in hr_refs_by_k.items():
        est = estimates_by_k[k]
        if ref["admitted"] and est.hr_valid:
            cross["admitted_valid"] += 1
            errs.append(est.hr_bpm - ref["median_pr_bpm"])
        elif ref["admitted"]:
            cross["admitted_nan"] += 1
        elif est.hr_valid:
            cross["excluded_valid"] += 1
        else:
            cross["excluded_nan"] += 1
    final_n = cross["admitted_valid"]
    if final_n > 0:
        a = np.array(errs)
        metrics = {
            "mae_bpm": float(np.mean(np.abs(a))),
            "rmse_bpm": float(np.sqrt(np.mean(a ** 2))),
            "bias_bpm": float(np.mean(a)),
        }
    else:
        metrics = {"mae_bpm": None, "rmse_bpm": None, "bias_bpm": None}
    return {"cross_tab": cross, "final_n": final_n, **metrics}


def compute_joint_br(br_refs_by_k: dict[int, dict], estimates_by_k: dict[int, WindowEstimate]) -> dict:
    cross = {"admitted_valid": 0, "admitted_nan": 0, "excluded_valid": 0, "excluded_nan": 0}
    errs = []
    for k, ref in br_refs_by_k.items():
        est = estimates_by_k[k]
        if ref["admitted"] and est.br_valid:
            cross["admitted_valid"] += 1
            errs.append(est.br_bpm - ref["median_rr_bpm"])
        elif ref["admitted"]:
            cross["admitted_nan"] += 1
        elif est.br_valid:
            cross["excluded_valid"] += 1
        else:
            cross["excluded_nan"] += 1
    final_n = cross["admitted_valid"]
    if final_n > 0:
        a = np.array(errs)
        metrics = {
            "mae_bpm": float(np.mean(np.abs(a))),
            "rmse_bpm": float(np.sqrt(np.mean(a ** 2))),
            "bias_bpm": float(np.mean(a)),
        }
    else:
        metrics = {"mae_bpm": None, "rmse_bpm": None, "bias_bpm": None}
    return {"cross_tab": cross, "final_n": final_n, **metrics}


# ─────────────────────────────────────────────────────────────────────────────
# Step 11 — incremental-coverage partition (OSR-02/OSR-18)
# ─────────────────────────────────────────────────────────────────────────────

def compute_incremental_coverage_partition(
    labels: tuple[str, str],
    estimates_by_config: dict[str, dict[int, WindowEstimate]],
    hr_refs_by_k: dict[int, dict],
) -> dict:
    """Requires exactly two configs (OSR-18). Over the reference-admissible window
    universe, partition by each config's `hr_valid` into `both_pass`/`<A>_only`/
    `<B>_only`/`neither`. The `<B>_only` bucket's own MAE/RMSE is the direct answer
    to "are the extra covered windows also correct" (OSR-02 — `paired_metrics`'s
    finite-in-all-conditions intersection structurally excludes this bucket)."""
    label_a, label_b = labels
    admissible_ks = sorted(k for k, ref in hr_refs_by_k.items() if ref["admitted"])
    buckets: dict[str, list[int]] = {
        "both_pass": [], f"{label_a}_only": [], f"{label_b}_only": [], "neither": [],
    }
    for k in admissible_ks:
        va = estimates_by_config[label_a][k].hr_valid
        vb = estimates_by_config[label_b][k].hr_valid
        if va and vb:
            buckets["both_pass"].append(k)
        elif va:
            buckets[f"{label_a}_only"].append(k)
        elif vb:
            buckets[f"{label_b}_only"].append(k)
        else:
            buckets["neither"].append(k)

    def _metrics_for(ks: list[int], label: str) -> dict:
        errs = []
        for k in ks:
            est = estimates_by_config[label][k]
            if est.hr_valid:
                errs.append(est.hr_bpm - hr_refs_by_k[k]["median_pr_bpm"])
        if not errs:
            return {"n_scored": 0, "mae_bpm": None, "rmse_bpm": None, "bias_bpm": None}
        a = np.array(errs)
        return {
            "n_scored": len(errs),
            "mae_bpm": float(np.mean(np.abs(a))),
            "rmse_bpm": float(np.sqrt(np.mean(a ** 2))),
            "bias_bpm": float(np.mean(a)),
        }

    report = {"n_admissible_windows": len(admissible_ks), "buckets": {}}
    for bucket_name, ks in buckets.items():
        report["buckets"][bucket_name] = {
            "n": len(ks),
            "k_values": ks,
            label_a: _metrics_for(ks, label_a),
            label_b: _metrics_for(ks, label_b),
        }
    return report


# ─────────────────────────────────────────────────────────────────────────────
# Per-window scoring row (Step 7/8)
# ─────────────────────────────────────────────────────────────────────────────

WINDOW_SCORES_COLUMNS = [
    "k", "frame_start", "frame_end", "epoch_start", "epoch_end", "frame0_epoch",
    "origin_source", "origin_is_approximate", "origin_caveat", "comparator_status",
    "hr_valid", "hr_bpm", "outcome_class", "rej_reason",
    "masimo_hr_n_total", "masimo_hr_n_finite_pr", "masimo_hr_n_pi_qualified",
    "masimo_hr_n_usable", "masimo_hr_coverage_ok", "masimo_hr_median",
    "masimo_hr_spread_bpm", "masimo_hr_stationarity_ok", "masimo_hr_admitted",
    "hr_error_bpm",
    "br_valid", "br_bpm", "masimo_br_n_finite", "masimo_br_availability_ok",
    "masimo_br_median", "masimo_br_spread_bpm", "masimo_br_stationarity_ok",
    "masimo_br_admitted", "br_error_bpm", "br_pi_median_flag",
    "br_session_type", "br_metronome_status", "br_commanded_rate_bpm",
    "br_metronome_error_bpm",
]


def score_window(
    w: Window,
    dsp: dict,
    est: WindowEstimate,
    outcome_class: str,
    hr_ref: dict,
    br_ref: dict,
    frame0_epoch: float,
    session_type: str,
    paced_schedule: Optional[PacedSchedule],
    paced_unavailable_reason: Optional[str],
    origin_source: str = "start_wall_utc_approximate",
    origin_is_approximate: bool = True,
    origin_caveat: str = ORIGIN_CAVEAT,
) -> dict:
    """Build one `window_scores.csv` row (Step 7/8). Every row is self-describing
    — the origin caveat and comparator_status travel with every row, not only the
    summary (OSR-01 R2)."""
    row = {
        "k": w.k, "frame_start": w.frame_start, "frame_end": w.frame_end,
        "epoch_start": w.epoch_start, "epoch_end": w.epoch_end,
        "frame0_epoch": frame0_epoch,
        "origin_source": origin_source,
        "origin_is_approximate": origin_is_approximate,
        "origin_caveat": origin_caveat,
        "comparator_status": "exploratory_non_frozen",
        "hr_valid": est.hr_valid,
        "hr_bpm": est.hr_bpm,
        "outcome_class": outcome_class,
        "rej_reason": est.rejection_reason,
        "masimo_hr_n_total": hr_ref["n_total"],
        "masimo_hr_n_finite_pr": hr_ref["n_finite_pr"],
        "masimo_hr_n_pi_qualified": hr_ref["n_pi_qualified"],
        "masimo_hr_n_usable": hr_ref["n_usable"],
        "masimo_hr_coverage_ok": hr_ref["coverage_ok"],
        "masimo_hr_median": hr_ref["median_pr_bpm"],
        "masimo_hr_spread_bpm": hr_ref["spread_bpm"],
        "masimo_hr_stationarity_ok": hr_ref["stationarity_ok"],
        "masimo_hr_admitted": hr_ref["admitted"],
        "hr_error_bpm": (
            est.hr_bpm - hr_ref["median_pr_bpm"] if (hr_ref["admitted"] and est.hr_valid) else float("nan")
        ),
        "br_valid": est.br_valid,
        "br_bpm": est.br_bpm,
        "masimo_br_n_finite": br_ref["n_finite_rr"],
        "masimo_br_availability_ok": br_ref["availability_ok"],
        "masimo_br_median": br_ref["median_rr_bpm"],
        "masimo_br_spread_bpm": br_ref["spread_bpm"],
        "masimo_br_stationarity_ok": br_ref["stationarity_ok"],
        "masimo_br_admitted": br_ref["admitted"],
        "br_error_bpm": (
            est.br_bpm - br_ref["median_rr_bpm"] if (br_ref["admitted"] and est.br_valid) else float("nan")
        ),
        "br_pi_median_flag": br_ref["pi_median"],
        "br_session_type": session_type,
    }

    if session_type == "natural":
        row["br_metronome_status"] = "not_applicable"
        row["br_commanded_rate_bpm"] = float("nan")
        row["br_metronome_error_bpm"] = float("nan")
    elif paced_unavailable_reason is not None:
        row["br_metronome_status"] = paced_unavailable_reason
        row["br_commanded_rate_bpm"] = float("nan")
        row["br_metronome_error_bpm"] = float("nan")
    else:
        assert paced_schedule is not None
        status, rate = lookup_commanded_rate(paced_schedule, w.epoch_start - frame0_epoch, w.epoch_end - frame0_epoch)
        row["br_metronome_status"] = status
        if status == "available":
            conc = br_metronome_concordance(est.br_bpm, rate)
            row["br_commanded_rate_bpm"] = rate
            row["br_metronome_error_bpm"] = conc["target_error_bpm"]
        else:
            row["br_commanded_rate_bpm"] = float("nan")
            row["br_metronome_error_bpm"] = float("nan")

    return row


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--captures", nargs="+", required=True, type=Path)
    ap.add_argument("--configs", nargs="+", required=True)
    ap.add_argument("--pinned-lock-source", nargs="*", default=[])
    ap.add_argument("--estimands", choices=("pinned", "rerun", "both"), default="both")
    ap.add_argument("--isolate-fields", nargs="*", default=[])
    ap.add_argument("--reproduction-baseline-eca-mode", nargs="*", default=[])
    ap.add_argument("--reproduction-baseline-lock", nargs="*", default=[])
    ap.add_argument("--masimo-csv", nargs="*", default=[])
    ap.add_argument("--session-type", nargs="*", default=[])
    ap.add_argument("--paced-schedule", nargs="*", default=[])
    ap.add_argument("--paced-target-unavailable", nargs="*", default=[])
    ap.add_argument("--allow-dirty", action="store_true")
    ap.add_argument("--out", default=None, type=Path)
    return ap


def main(argv: Optional[list[str]] = None) -> None:
    args = _build_arg_parser().parse_args(argv)

    configs = parse_configs(args.configs)
    pinned_lock_source = parse_kv_list(args.pinned_lock_source, "--pinned-lock-source")
    isolate_fields = list(args.isolate_fields)
    reproduction_baseline_eca_mode = parse_kv_list(
        args.reproduction_baseline_eca_mode, "--reproduction-baseline-eca-mode"
    )
    reproduction_baseline_lock = parse_kv_list(
        args.reproduction_baseline_lock, "--reproduction-baseline-lock"
    )
    masimo_csv_override = parse_kv_list(args.masimo_csv, "--masimo-csv")
    session_type = parse_kv_list(args.session_type, "--session-type")
    paced_schedule_raw = parse_kv_list(args.paced_schedule, "--paced-schedule")
    paced_target_unavailable = parse_kv_list(
        args.paced_target_unavailable, "--paced-target-unavailable"
    )

    capture_ids = [p.name for p in args.captures]
    if len(set(capture_ids)) != len(capture_ids):
        raise ValueError(f"--captures has duplicate directory basenames: {capture_ids}")

    if isolate_fields:
        if len(configs) != 2:
            raise ValueError(
                f"--isolate-fields requires EXACTLY TWO --configs, got {len(configs)} "
                f"({list(configs)}) (OSR-13 R2)."
            )
        if args.estimands != "both":
            raise ValueError(
                "--isolate-fields is given: --estimands must be 'both' (this run carries "
                "causal/promotion framing and a single estimand cannot carry that framing "
                "alone) (OSR-04 R2)."
            )

    requested_estimands = ["pinned", "rerun"] if args.estimands == "both" else [args.estimands]

    validate_session_type_combination(
        capture_ids, session_type, paced_schedule_raw, paced_target_unavailable
    )

    if "pinned" in requested_estimands:
        missing = [c for c in capture_ids if c not in pinned_lock_source]
        if missing:
            raise ValueError(
                f"--pinned-lock-source is required for every capture when the pinned "
                f"estimand is requested; missing for {missing}."
            )

    if not args.allow_dirty and not is_tree_clean():
        print(
            "ERROR: working tree has uncommitted changes to tracked files. Commit "
            "first, or pass --allow-dirty to run anyway (the result will be stamped "
            "reproducible: false and must not be cited as evidence).",
            file=sys.stderr,
        )
        sys.exit(1)
    reproducible = is_tree_clean()

    # CLAUDE.md §3 rule 5: every run logs to results/<experiment>/<timestamp>/ — a
    # run_id subdirectory is ALWAYS appended, whether --out was given or defaulted
    # (mirrors scripts/diagnose_bin_drift.py's identical out_dir/run_id pattern),
    # so passing the same --out twice can never silently overwrite a prior run.
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_root = args.out if args.out is not None else (REPO_ROOT / "results" / "score_offline")
    out_dir = out_root / run_id
    if out_dir.exists():
        print(f"ERROR: run directory {out_dir} already exists.", file=sys.stderr)
        sys.exit(1)
    out_dir.mkdir(parents=True)

    scorer_git_commit = get_git_commit()
    scorer_git_dirty = not is_tree_clean()

    parsed_args_normalized = {
        "captures": [str(p) for p in args.captures],
        "configs": {k: str(v) for k, v in configs.items()},
        "pinned_lock_source": pinned_lock_source,
        "estimands": args.estimands,
        "isolate_fields": isolate_fields,
        "reproduction_baseline_eca_mode": reproduction_baseline_eca_mode,
        "reproduction_baseline_lock": reproduction_baseline_lock,
        "masimo_csv": masimo_csv_override,
        "session_type": session_type,
        "paced_schedule": paced_schedule_raw,
        "paced_target_unavailable": paced_target_unavailable,
        "allow_dirty": bool(args.allow_dirty),
        "out": str(out_dir),
    }

    run_manifest: dict = {
        "run_id": run_id,
        "sys_argv": list(sys.argv),
        "parsed_args_normalized": parsed_args_normalized,
        "scorer_git_commit": scorer_git_commit,
        "scorer_git_dirty": scorer_git_dirty,
        "reproducible": reproducible,
        "python_version": sys.version,
        "numpy_version": np.__version__,
        "deterministic": True,
        "seed_affects_output": False,
        "captures": {},
    }

    for capture_dir in args.captures:
        capture_id = capture_dir.name
        result = run_capture(
            capture_dir=capture_dir,
            capture_id=capture_id,
            configs=configs,
            requested_estimands=requested_estimands,
            pinned_lock_source=pinned_lock_source,
            isolate_fields=isolate_fields,
            reproduction_baseline_eca_mode=reproduction_baseline_eca_mode,
            reproduction_baseline_lock=reproduction_baseline_lock,
            masimo_csv_override=masimo_csv_override.get(str(capture_dir)),
            session_type=session_type[capture_id],
            paced_schedule_raw=paced_schedule_raw.get(capture_id),
            paced_target_unavailable=paced_target_unavailable.get(capture_id),
            out_dir=out_dir,
            scorer_git_commit=scorer_git_commit,
            scorer_git_dirty=scorer_git_dirty,
            reproducible=reproducible,
            run_id=run_id,
            sys_argv=list(sys.argv),
            parsed_args_normalized=parsed_args_normalized,
        )
        run_manifest["captures"][capture_id] = result
        print(f"{capture_id}: {result['headline']}")

    with (out_dir / "run_summary.json").open("w", encoding="utf-8") as fh:
        json.dump(run_manifest, fh, indent=2, default=str)
    print(f"Artifacts: {out_dir}")


def run_capture(
    *, capture_dir: Path, capture_id: str, configs: dict[str, Path],
    requested_estimands: list[str], pinned_lock_source: dict[str, str],
    isolate_fields: list[str], reproduction_baseline_eca_mode: dict[str, str],
    reproduction_baseline_lock: dict[str, str], masimo_csv_override: Optional[str],
    session_type: str, paced_schedule_raw: Optional[str],
    paced_target_unavailable: Optional[str], out_dir: Path,
    scorer_git_commit: str, scorer_git_dirty: bool, reproducible: bool,
    run_id: str, sys_argv: list[str], parsed_args_normalized: dict,
) -> dict:
    capture_run_metadata_path = capture_dir / "run_metadata.json"
    capture_run_metadata = json.loads(capture_run_metadata_path.read_text(encoding="utf-8"))
    raw_path = capture_dir / "adc_stream.bin"
    raw_sha256 = sha256_file(raw_path)

    masimo_csv_path = discover_masimo_csv(capture_dir, masimo_csv_override)
    masimo_df = masimo.load_masimo(masimo_csv_path)

    frame0_epoch, origin_source, origin_is_approximate, origin_caveat = resolve_frame0_epoch(
        capture_run_metadata
    )

    paced_schedule = (
        parse_paced_schedule_value(paced_schedule_raw) if paced_schedule_raw is not None else None
    )
    isolate_fields_active = bool(isolate_fields)

    capture_out_dir = out_dir / capture_id
    capture_out_dir.mkdir(parents=True, exist_ok=True)

    # Reference marginal (Step 10) is radar/config-independent — computed ONCE per
    # capture from the window grid of the FIRST config (the frozen grid guarantees
    # every config shares the same fs/frames_per_win/frame0_epoch, so windows are
    # identical across configs for a given capture).
    hr_refs_by_k: Optional[dict[int, dict]] = None
    br_refs_by_k: Optional[dict[int, dict]] = None
    windows_by_k: Optional[dict[int, Window]] = None

    per_config_estimand_summary: dict[str, dict] = {}
    estimates_by_config_estimand: dict[tuple[str, str], dict[int, WindowEstimate]] = {}

    config_dicts: dict[str, dict] = {label: yaml.safe_load(path.read_text(encoding="utf-8")) for label, path in configs.items()}

    isolation_record = None
    if isolate_fields_active:
        (label_a, label_b) = list(configs.keys())
        isolation_record = assert_isolated_fields(config_dicts[label_a], config_dicts[label_b], isolate_fields)

    for config_label, cfg in config_dicts.items():
        chirp_cfg = validate_decode_geometry(cfg, capture_run_metadata, capture_id)
        validated_fs = chirp_cfg.frame_rate_hz
        cube = read_adc_bin(raw_path, chirp_cfg)
        n_frames = cube.shape[0]
        candidate_bins = derive_candidate_bins(cfg)
        cfg_hash = run_config_hash(cfg)
        is_strict = cfg["heart"]["ahet_gate_mode"] == STRICT_GATE_MODE

        windows = build_window_grid(n_frames, frame0_epoch, fs=validated_fs, frames_per_win=FRAMES_PER_WINDOW)
        if windows_by_k is None:
            windows_by_k = {w.k: w for w in windows}
            hr_refs_by_k = {w.k: hr_reference(masimo_df, w.epoch_start, w.epoch_end) for w in windows}
            br_refs_by_k = {w.k: br_reference(masimo_df, w.epoch_start, w.epoch_end) for w in windows}

        for estimand in requested_estimands:
            if estimand == "pinned":
                lock_prov = resolve_pinned_lock(
                    capture_id, raw_sha256, pinned_lock_source[capture_id],
                    isolate_fields_active, reproduction_baseline_eca_mode, reproduction_baseline_lock,
                )
                locked_bin = lock_prov.locked_bin
                lock_prov_dict = asdict(lock_prov)
            else:
                locked_bin, warmup_evidence = resolve_rerun_lock(cube, candidate_bins, cfg, validated_fs)
                lock_prov_dict = {"kind": "rerun_warmup", "locked_bin": locked_bin, "warmup_evidence": warmup_evidence}

            rows = []
            raw_dsp_list = []
            estimates_by_k: dict[int, WindowEstimate] = {}
            outcome_by_k: dict[int, str] = {}

            for w in windows:
                dsp = run_window_dsp(cube[w.frame_start:w.frame_end], locked_bin, validated_fs, cfg)
                est = as_window_estimate(dsp, estimator_id=ESTIMATOR_ID, run_config_hash=cfg_hash)

                if is_strict:
                    f_r_for_outcome = float("nan") if dsp["f_r_hz"] is None else dsp["f_r_hz"]
                    outcome_class = classify_window_outcome(
                        dsp["hr_result"]["accepted_candidate_rank"],
                        dsp["hr_result"]["candidate_rejection_code"],
                        f_r_for_outcome,
                    )
                else:
                    outcome_class = "unavailable_non_strict_gate_mode"

                row = score_window(
                    w, dsp, est, outcome_class, hr_refs_by_k[w.k], br_refs_by_k[w.k],
                    frame0_epoch, session_type, paced_schedule, paced_target_unavailable,
                    origin_source=origin_source,
                    origin_is_approximate=origin_is_approximate,
                    origin_caveat=origin_caveat,
                )
                rows.append(row)
                raw_dsp_list.append(dsp)
                estimates_by_k[w.k] = est
                outcome_by_k[w.k] = outcome_class

            triple_dir = capture_out_dir / config_label / estimand
            triple_dir.mkdir(parents=True, exist_ok=True)

            pd.DataFrame(rows, columns=WINDOW_SCORES_COLUMNS).to_csv(
                triple_dir / "window_scores.csv", index=False
            )

            evidence = build_evidence_arrays(
                raw_dsp_list,
                [w.k for w in windows], [w.frame_start for w in windows],
                [w.frame_end for w in windows], [w.epoch_start for w in windows],
                [w.epoch_end for w in windows], locked_bin,
            )
            np.savez(triple_dir / "evidence.npz", **evidence)

            hr_ref_marginal = compute_hr_reference_marginal(hr_refs_by_k)
            br_ref_marginal = compute_br_reference_marginal(br_refs_by_k)
            hr_radar_marginal = compute_radar_marginal_hr(estimates_by_k, outcome_by_k)
            br_radar_marginal = compute_radar_marginal_br(estimates_by_k)
            hr_joint = compute_joint_hr(hr_refs_by_k, estimates_by_k)
            br_joint = compute_joint_br(br_refs_by_k, estimates_by_k)

            triple_summary = {
                "run_id": run_id, "sys_argv": sys_argv,
                "parsed_args_normalized": parsed_args_normalized,
                "capture_id": capture_id, "config_label": config_label, "estimand": estimand,
                "raw_path": str(raw_path), "raw_sha256": raw_sha256,
                "masimo_csv_path": str(masimo_csv_path), "masimo_csv_sha256": sha256_file(masimo_csv_path),
                "run_metadata_path": str(capture_run_metadata_path),
                "run_metadata_sha256": sha256_file(capture_run_metadata_path),
                "capture_time_git_commit": capture_run_metadata.get("git_commit"),
                "capture_time_git_dirty": capture_run_metadata.get("git_dirty"),
                "scorer_git_commit": scorer_git_commit, "scorer_git_dirty": scorer_git_dirty,
                "reproducible": reproducible,
                "config_path": str(configs[config_label]), "config_sha256": sha256_file(configs[config_label]),
                "run_config_hash": cfg_hash,
                "validated_fs": validated_fs, "n_frames": n_frames, "n_windows": len(windows),
                "frame0_epoch": frame0_epoch, "origin_source": origin_source,
                "origin_is_approximate": origin_is_approximate,
                "comparator_status": "exploratory_non_frozen",
                "estimator_id": ESTIMATOR_ID, "ahet_gate_mode": cfg["heart"]["ahet_gate_mode"],
                "lock_provenance": lock_prov_dict,
                "isolation": (
                    {"allowlist": isolate_fields, "diff": {k: list(v) for k, v in isolation_record.items()}}
                    if isolation_record is not None else None
                ),
                "hr_reference_marginal": hr_ref_marginal,
                "br_reference_marginal": br_ref_marginal,
                "hr_radar_marginal": hr_radar_marginal,
                "br_radar_marginal": br_radar_marginal,
                "hr_joint": hr_joint,
                "br_joint": br_joint,
                "deterministic": True, "seed_affects_output": False,
            }
            with (triple_dir / "summary.json").open("w", encoding="utf-8") as fh:
                json.dump(triple_summary, fh, indent=2, default=str)

            per_config_estimand_summary[f"{config_label}/{estimand}"] = {
                "n_windows": len(windows), "hr_joint": hr_joint, "br_joint": br_joint,
                "lock_provenance": lock_prov_dict,
            }
            estimates_by_config_estimand[(config_label, estimand)] = estimates_by_k

        del cube  # free the decoded cube before moving to the next config

    # Cross-config comparison, per estimand (Step 11/OSR-02/OSR-18; and the
    # supplementary paired_metrics report, Step 10's "supplementary" callout).
    config_labels = list(configs.keys())
    for estimand in requested_estimands:
        conditions: dict[str, dict[int, dict]] = {}
        for config_label in config_labels:
            estimates_by_k = estimates_by_config_estimand[(config_label, estimand)]
            conditions[config_label] = {
                k: {
                    "radar_hr": est.hr_bpm,
                    "masimo_pr": hr_refs_by_k[k]["median_pr_bpm"],
                    "error": est.hr_bpm - hr_refs_by_k[k]["median_pr_bpm"],
                    "ahet_verified": est.hr_valid,
                }
                for k, est in estimates_by_k.items()
            }
        paired = paired_metrics(conditions, reference_condition=config_labels[0])

        comparison: dict = {
            "capture_id": capture_id, "estimand": estimand,
            "config_labels": config_labels,
            "caption": (
                "eca_mode is the only variable (isolation asserted)"
                if isolation_record is not None
                else "general, non-causal multi-config comparison"
            ),
            "paired_metrics": paired,
            "paired_metrics_table_caption": "common-window comparison only, not the incremental-coverage answer",
        }
        if len(config_labels) == 2:
            comparison["incremental_coverage_partition"] = compute_incremental_coverage_partition(
                (config_labels[0], config_labels[1]),
                {lbl: estimates_by_config_estimand[(lbl, estimand)] for lbl in config_labels},
                hr_refs_by_k,
            )
        with (capture_out_dir / f"comparison_{estimand}.json").open("w", encoding="utf-8") as fh:
            json.dump(comparison, fh, indent=2, default=str)

    n_windows_total = len(windows_by_k) if windows_by_k else 0
    headline = f"n_windows={n_windows_total} configs={config_labels} estimands={requested_estimands}"
    return {
        "capture_id": capture_id, "raw_sha256": raw_sha256,
        "masimo_csv_path": str(masimo_csv_path),
        "frame0_epoch": frame0_epoch, "origin_source": origin_source,
        "n_windows": n_windows_total,
        "per_config_estimand_summary": per_config_estimand_summary,
        "headline": headline,
    }


if __name__ == "__main__":
    main()
