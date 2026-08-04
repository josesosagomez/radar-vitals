"""Score a capture at EVERY candidate range bin, not just the warmup-locked one.

Answers one question, cheaply, on radar evidence alone:

    when a capture reports 0% HR coverage, is that a bad bin lock, or does no bin
    inside the 0.8-1.4 m protocol gate carry an AHET-verifiable cardiac signal?

The two answers imply different work. A bad lock is a `src/warmup_select.py` problem.
No signal at any in-gate bin is not — it is an estimator/gate/capture problem, and any
amount of warmup tuning would be wasted on it.

Method
------
For each capture, for each window `k` of the FROZEN grid (`src/m4/window_grid.py`:
30 s = 600 non-overlapping frames at 20 Hz, `k = 0` scored, incomplete tail dropped),
run the production window DSP (`src/window_pipeline.py:run_window_dsp`) once per
candidate bin from `src/warmup_select.py:derive_candidate_bins`. Record the outcome of
every (capture, k, bin) cell.

**No Masimo file is opened by this script, by design.** `HANDOFF.md` section 3.5 forbids
choosing a bin because it agrees better with the reference, so the sweep is kept
structurally unable to do it: it reports radar-side yield (did the chain return an
AHET-verified rate at all) plus a reference-free temporal-continuity statistic, and it
never ranks bins by agreement. Whether a surviving pass is a TRUE cardiac reading is a
separate question this script does not answer -- see "Interpretation" below.

Nothing in the selection rule is changed or re-run by this script. It is read-only with
respect to `results/live_demo/`, `data/`, and every source module; it only writes into
its own output directory.

Interpretation (stated so results are not over-read)
---------------------------------------------------
* `hr_yield = 0` at every candidate bin means: **this estimator, at this config, verified
  no cardiac rate anywhere in the gate.** It is not proof that the chest return carries no
  cardiac modulation -- an estimator-relative negative, not a physical one.
* `hr_yield > 0` at some bin does NOT establish that the bin is correct. AHET verifies a
  second-harmonic relationship; a harmonic of respiration or of a body-motion line can
  satisfy it. `hr_continuity_mad_bpm` is reported as a reference-free plausibility hint
  (a real HR track drifts slowly; scattered false passes do not), not as a verdict.
* The per-capture rejection histogram is usually the most actionable output: if every bin
  fails for the same reason, the binding constraint is that gate, not the bin choice.

Usage
-----
    python -X utf8 scripts/diagnose_bin_sweep.py \\
        --captures results/live_demo/20260728_224902_live_demo_massimo3 \\
                   results/live_demo/20260728_232415_live_demo_massimo5 \\
                   results/live_demo/20260729_004815_live_demo_massimo7 \\
        --out results/diagnose/bin_sweep

Add `--max-windows 2` for a fast smoke run, `--skip-input-hash` to skip hashing the
multi-GB `adc_stream.bin` inputs (faster, but the run is then not provenance-complete).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from src.m4.window_grid import (  # noqa: E402
    FRAME_RATE_HZ,
    FRAMES_PER_WINDOW,
    n_complete_windows,
    window_frame_span,
)
from src.radar_io import ChirpConfig, read_adc_bin  # noqa: E402
from src.warmup_select import derive_candidate_bins, range_energy_by_bin  # noqa: E402
from src.window_pipeline import run_config_hash, run_window_dsp  # noqa: E402

import diagnose_bin_drift as bindrift  # noqa: E402

sha256_file = bindrift.sha256_file
get_git_commit = bindrift.get_git_commit
is_tree_clean = bindrift.is_tree_clean
validate_decode_geometry = bindrift.validate_decode_geometry

DEFAULT_CONFIG = REPO_ROOT / "scripts" / "live_demo_config.yaml"


# ── Frame-range decode ──────────────────────────────────────────────────────
#
# `read_adc_bin` decodes a whole file, which for these captures materialises ~3.1 GB of
# complex64 at once. The sweep only ever needs one 600-frame window at a time, so it
# decodes by frame range instead. The de-interleave below is the same arithmetic as
# `src/radar_io.py:read_adc_bin`; `verify_frame_range_decode` proves the two agree
# bit-for-bit rather than leaving that as a comment, because a silent drift here would
# corrupt every number this script prints.


def words_per_frame(cfg: ChirpConfig) -> int:
    """int16 words per frame. Frame boundaries must land on 4-word LVDS packets."""
    n = cfg.num_chirps_per_frame * cfg.num_rx * cfg.num_adc_samples * 2
    if n % 4 != 0:
        raise ValueError(
            f"frame is {n} int16 words, not a multiple of the 4-word LVDS packet; "
            "frame-range decoding would split a packet and mis-pair I/Q."
        )
    return n


def decode_frame_range(
    raw: np.ndarray, cfg: ChirpConfig, start_frame: int, n_frames: int
) -> np.ndarray:
    """Decode frames `[start_frame, start_frame + n_frames)` into a radar cube."""
    wpf = words_per_frame(cfg)
    lo = start_frame * wpf
    hi = lo + n_frames * wpf
    if hi > raw.size:
        raise ValueError(
            f"frame range [{start_frame}, {start_frame + n_frames}) needs {hi} int16 "
            f"words but the stream holds {raw.size}."
        )
    words = np.asarray(raw[lo:hi]).reshape(-1, 4)
    out = np.empty(words.shape[0] * 2, dtype=np.complex64)
    if cfg.iq_swap:
        # SampleSwap=1: packet = [Q_n, Q_{n+1}, I_n, I_{n+1}]
        out[0::2] = words[:, 2].astype(np.float32) + 1j * words[:, 0].astype(np.float32)
        out[1::2] = words[:, 3].astype(np.float32) + 1j * words[:, 1].astype(np.float32)
    else:
        # SampleSwap=0 (Studio default): packet = [I_n, I_{n+1}, Q_n, Q_{n+1}]
        out[0::2] = words[:, 0].astype(np.float32) + 1j * words[:, 2].astype(np.float32)
        out[1::2] = words[:, 1].astype(np.float32) + 1j * words[:, 3].astype(np.float32)
    return out.reshape(n_frames, cfg.num_chirps_per_frame, cfg.num_rx, cfg.num_adc_samples)


def verify_frame_range_decode(tmp_dir: Path, cfg: ChirpConfig, n_frames: int = 5) -> dict:
    """Assert `decode_frame_range` == `read_adc_bin` on synthetic bytes, both I/Q conventions.

    Runs before any capture is touched. If this fails, every downstream number is
    suspect and the run aborts.
    """
    tmp_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(20260731)
    wpf = words_per_frame(cfg)
    payload = rng.integers(-2048, 2048, size=n_frames * wpf, dtype=np.int16)
    path = tmp_dir / "decode_selfcheck.bin"
    path.write_bytes(payload.astype("<i2").tobytes())

    checked = []
    for iq_swap in (False, True):
        probe = ChirpConfig(
            num_adc_samples=cfg.num_adc_samples,
            num_rx=cfg.num_rx,
            num_tx=cfg.num_tx,
            num_chirps_per_frame=cfg.num_chirps_per_frame,
            num_frames=n_frames,
            frame_rate_hz=cfg.frame_rate_hz,
            range_resolution_m=cfg.range_resolution_m,
            iq_swap=iq_swap,
        )
        reference = read_adc_bin(path, probe)
        raw = np.memmap(path, dtype="<i2", mode="r")
        for start in range(n_frames):
            got = decode_frame_range(raw, probe, start, 1)
            if not np.array_equal(got, reference[start:start + 1]):
                raise AssertionError(
                    f"decode_frame_range disagrees with read_adc_bin at frame {start} "
                    f"(iq_swap={iq_swap}); the frame-range decoder has drifted from "
                    "src/radar_io.py and this run cannot be trusted."
                )
        del raw
        checked.append({"iq_swap": iq_swap, "frames_checked": n_frames})
    path.unlink()
    return {"passed": True, "cases": checked}


# ── Per-capture sweep ───────────────────────────────────────────────────────


def _f(value) -> float | None:
    """A JSON/CSV-safe float: NaN and inf become None rather than 'nan'."""
    if value is None:
        return None
    v = float(value)
    return v if np.isfinite(v) else None


def sweep_capture(
    capture_dir: Path,
    cfg: dict,
    *,
    max_windows: int | None = None,
    verbose: bool = True,
) -> dict:
    """Run every candidate bin over every complete window of one capture."""
    capture_dir = Path(capture_dir)
    raw_path = capture_dir / "adc_stream.bin"
    meta_path = capture_dir / "run_metadata.json"
    if not raw_path.is_file():
        raise FileNotFoundError(f"{capture_dir}: no adc_stream.bin")
    if not meta_path.is_file():
        raise FileNotFoundError(f"{capture_dir}: no run_metadata.json")

    run_metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    capture_id = capture_dir.name
    chirp_cfg = validate_decode_geometry(cfg, run_metadata, capture_id)
    fs = float(chirp_cfg.frame_rate_hz)

    bytes_per_frame = words_per_frame(chirp_cfg) * 2
    file_size = raw_path.stat().st_size
    if file_size % bytes_per_frame != 0:
        raise ValueError(
            f"{capture_id}: adc_stream.bin is {file_size} B, not a whole number of "
            f"{bytes_per_frame} B frames."
        )
    n_frames = file_size // bytes_per_frame
    n_windows = n_complete_windows(int(n_frames), FRAMES_PER_WINDOW)
    if max_windows is not None:
        n_windows = min(n_windows, max_windows)
    if n_windows == 0:
        raise ValueError(f"{capture_id}: {n_frames} frames is under one complete window.")

    candidate_bins = derive_candidate_bins(cfg)
    res_m = float(cfg["profile"]["range_resolution_m"])
    locked_bin = run_metadata.get("locked_bin")

    # The recorded warmup evidence is carried through untouched so the sweep's own
    # per-window energy can be read next to it WITHOUT the two being conflated: warmup
    # eligibility is a settled-sub-window quantity of window 0 only.
    warmup_path = capture_dir / "warmup_bin_selection.json"
    warmup_evidence = (
        json.loads(warmup_path.read_text(encoding="utf-8")) if warmup_path.is_file() else None
    )
    warmup_by_bin = {}
    if warmup_evidence:
        warmup_by_bin = {int(c["bin"]): c for c in warmup_evidence.get("candidates", [])}

    if verbose:
        print(
            f"  {capture_id}: {n_frames} frames -> {n_windows} complete windows "
            f"x {len(candidate_bins)} bins = {n_windows * len(candidate_bins)} DSP calls",
            flush=True,
        )

    raw = np.memmap(raw_path, dtype="<i2", mode="r")
    rows: list[dict] = []
    t0 = time.monotonic()
    try:
        for k in range(n_windows):
            start_frame, end_frame = window_frame_span(k, FRAMES_PER_WINDOW)
            cube = decode_frame_range(raw, chirp_cfg, start_frame, end_frame - start_frame)

            energies = range_energy_by_bin(cube, candidate_bins)
            e_ref = max(energies.values())
            ranked = sorted(candidate_bins, key=lambda b: energies[b], reverse=True)
            energy_rank = {b: i + 1 for i, b in enumerate(ranked)}

            for b in candidate_bins:
                rel = (
                    10.0 * np.log10(energies[b] / e_ref)
                    if energies[b] > 0 and e_ref > 0
                    else float("-inf")
                )
                row = {
                    "capture_id": capture_id,
                    "k": k,
                    "frame_start": start_frame,
                    "frame_end": end_frame,
                    "bin": b,
                    "range_m": round(b * res_m, 4),
                    "is_locked_bin": bool(locked_bin is not None and b == int(locked_bin)),
                    "energy": float(energies[b]),
                    "energy_rank_in_window": energy_rank[b],
                    "rel_db_in_window": _f(rel),
                    "warmup_settled_energy_db": (
                        warmup_by_bin.get(b, {}).get("settled_energy_db")
                    ),
                    "warmup_energy_eligible": warmup_by_bin.get(b, {}).get("energy_eligible"),
                }
                try:
                    dsp = run_window_dsp(cube, b, fs, cfg)
                except Exception as exc:                      # noqa: BLE001
                    row.update({
                        "dsp_failed": True, "dsp_error": str(exc),
                        "ahet_ratio_db_best": None,
                        "hr_valid": None, "hr_bpm": None, "fallback_hr_bpm": None,
                        "rej_reason": None, "accepted_candidate_rank": None,
                        "spectrum_stage": None, "br_bpm": None, "br_confidence": None,
                        "br_valid": None, "f_r_hz": None, "n_eca_skipped": None,
                        "k_max_eff": None,
                    })
                else:
                    # How far the AHET second-harmonic check actually fell short, not just
                    # that it did. `ratio_db_low` a hair under the 1.0 dB gate would mean a
                    # marginal threshold; deeply negative means there is no second harmonic
                    # to verify, which no bin choice can fix.
                    ratios = np.asarray(
                        dsp["hr_result"].get("peak_to_floor_ratio_db", []), dtype=float
                    )
                    finite = ratios[np.isfinite(ratios)] if ratios.size else ratios
                    row.update({
                        "dsp_failed": False, "dsp_error": None,
                        "ahet_ratio_db_best": (
                            round(float(np.max(finite)), 3) if finite.size else None
                        ),
                        "hr_valid": bool(dsp["hr_valid"]),
                        "hr_bpm": _f(dsp["hr_raw"]),
                        "fallback_hr_bpm": _f(dsp["fallback_hr_bpm"]),
                        "rej_reason": dsp["rej_reason"],
                        "accepted_candidate_rank": int(
                            dsp["hr_result"].get("accepted_candidate_rank", -1)
                        ),
                        "spectrum_stage": int(dsp["spectrum_stage"]),
                        "br_bpm": _f(dsp["br_bpm"]),
                        "br_confidence": dsp["br_confidence"],
                        "br_valid": bool(dsp["br_valid"]),
                        "f_r_hz": _f(dsp["f_r_hz"]),
                        "n_eca_skipped": int(dsp["n_eca_skipped"]),
                        "k_max_eff": int(dsp["k_max_eff"]),
                    })
                rows.append(row)

            del cube
            if verbose:
                print(
                    f"    window {k + 1}/{n_windows} done "
                    f"({time.monotonic() - t0:.0f}s elapsed)",
                    flush=True,
                )
    finally:
        del raw

    sweep = {
        "capture_id": capture_id,
        "capture_dir": str(capture_dir),
        "n_frames": int(n_frames),
        "n_windows_complete": int(n_complete_windows(int(n_frames), FRAMES_PER_WINDOW)),
        "n_windows_swept": int(n_windows),
        "candidate_bins": [int(b) for b in candidate_bins],
        "locked_bin": None if locked_bin is None else int(locked_bin),
        "locked_bin_source": run_metadata.get("locked_bin_source"),
        "warmup_selection_confidence": run_metadata.get("warmup_selection_confidence"),
        "fs_hz": fs,
        "range_resolution_m": res_m,
        "elapsed_s": round(time.monotonic() - t0, 1),
        "rows": rows,
    }
    sweep["window0_reproduces_warmup"] = verify_window0_reproduces_warmup(
        sweep, warmup_evidence
    )
    if verbose:
        check = sweep["window0_reproduces_warmup"]
        if not check["checked"]:
            print(f"    warmup reproduction NOT CHECKED: {check['reason']}", flush=True)
        elif check["reproduces"]:
            print(
                f"    window 0 reproduces recorded warmup evidence "
                f"({check['bins_compared']} bins)",
                flush=True,
            )
        else:
            print(
                f"    WARNING: window 0 does NOT reproduce the recorded warmup evidence "
                f"({check['n_mismatches']} mismatched fields). This is a finding, not a "
                f"crash — see run_meta.json.",
                file=sys.stderr, flush=True,
            )
    return sweep


# ── Aggregation ─────────────────────────────────────────────────────────────


def verify_window0_reproduces_warmup(sweep: dict, warmup_evidence: dict | None) -> dict:
    """Check the sweep's own `k = 0` cells against the capture's recorded warmup evidence.

    Warmup ran on the first `FRAMES_PER_WINDOW` frames, which IS window 0 of the frozen
    grid, and it scored every candidate through the same `run_window_dsp`. So the recorded
    `warmup_bin_selection.json` is a free, per-bin, live-captured expected value for this
    sweep's first window: agreement validates the frame-range decoder, the active config,
    and the raw mirror's frame-0 alignment all at once.

    A mismatch does NOT abort. Offline failing to reproduce a session's live numbers is
    itself the finding (M4R-10, `src/warmup_select.py:5-7`) and must be recorded, not
    crashed on -- it already happened once for massimo1 when the M2 respiration fix moved
    its lock from 23 to 27.
    """
    if not warmup_evidence:
        return {"checked": False, "reason": "no warmup_bin_selection.json in capture dir"}

    expected = {int(c["bin"]): c for c in warmup_evidence.get("candidates", [])}
    mismatches = []
    compared = 0
    for row in sweep["rows"]:
        if row["k"] != 0 or row["bin"] not in expected:
            continue
        want = expected[row["bin"]]
        compared += 1
        for field, got, ref in (
            ("hr_valid", row["hr_valid"], bool(want.get("hr_valid"))),
            ("rej_reason", row["rej_reason"], want.get("rej_reason")),
            ("br_bpm", row["br_bpm"], want.get("br_bpm")),
            ("br_confidence", row["br_confidence"], want.get("br_confidence")),
        ):
            same = (
                got is None and ref is None
                or (
                    isinstance(got, float) and isinstance(ref, (int, float))
                    and abs(got - float(ref)) <= 1e-9
                )
                or got == ref
            )
            if not same:
                mismatches.append(
                    {"bin": row["bin"], "field": field, "sweep": got, "warmup_json": ref}
                )

    return {
        "checked": True,
        "bins_compared": compared,
        "reproduces": not mismatches,
        "mismatches": mismatches[:20],
        "n_mismatches": len(mismatches),
    }


def _mad_successive_diff(values: list[float]) -> float | None:
    """Median absolute successive difference of an HR series, in bpm.

    Reference-free plausibility hint only (see module docstring). A real heart-rate
    track moves slowly window to window; a scatter of spurious AHET passes does not.
    Needs at least two values to mean anything, and is NOT a validity criterion.
    """
    if len(values) < 2:
        return None
    diffs = [abs(b - a) for a, b in zip(values, values[1:])]
    return float(statistics.median(diffs))


def summarise(sweep: dict) -> list[dict]:
    """Collapse the per-(k, bin) rows into one row per bin."""
    by_bin: dict[int, list[dict]] = {}
    for row in sweep["rows"]:
        by_bin.setdefault(row["bin"], []).append(row)

    out = []
    for b in sweep["candidate_bins"]:
        cells = sorted(by_bin.get(b, []), key=lambda r: r["k"])
        n = len(cells)
        ok = [c for c in cells if not c["dsp_failed"]]
        hr_cells = [c for c in ok if c["hr_valid"]]
        hr_vals = [c["hr_bpm"] for c in hr_cells if c["hr_bpm"] is not None]
        br_cells = [c for c in ok if c["br_valid"]]
        br_vals = [c["br_bpm"] for c in br_cells if c["br_bpm"] is not None]
        rel = [c["rel_db_in_window"] for c in ok if c["rel_db_in_window"] is not None]
        reasons: dict[str, int] = {}
        for c in ok:
            if not c["hr_valid"]:
                reasons[c["rej_reason"] or "unknown"] = reasons.get(c["rej_reason"] or "unknown", 0) + 1

        out.append({
            "capture_id": sweep["capture_id"],
            "bin": b,
            "range_m": round(b * sweep["range_resolution_m"], 4),
            "is_locked_bin": bool(
                sweep["locked_bin"] is not None and b == sweep["locked_bin"]
            ),
            "n_windows": n,
            "n_dsp_failed": n - len(ok),
            "n_hr_valid": len(hr_cells),
            "hr_yield": round(len(hr_cells) / n, 4) if n else None,
            "hr_windows_k": [c["k"] for c in hr_cells],
            "hr_median_bpm": round(statistics.median(hr_vals), 2) if hr_vals else None,
            "hr_min_bpm": round(min(hr_vals), 2) if hr_vals else None,
            "hr_max_bpm": round(max(hr_vals), 2) if hr_vals else None,
            "hr_continuity_mad_bpm": (
                round(_mad_successive_diff(hr_vals), 2)
                if _mad_successive_diff(hr_vals) is not None else None
            ),
            "n_br_valid": len(br_cells),
            "br_yield": round(len(br_cells) / n, 4) if n else None,
            "br_median_bpm": round(statistics.median(br_vals), 2) if br_vals else None,
            "mean_rel_db_in_window": round(float(np.mean(rel)), 2) if rel else None,
            "ahet_ratio_db_median": (
                round(statistics.median(ratios), 2)
                if (ratios := [
                    c["ahet_ratio_db_best"] for c in ok
                    if c.get("ahet_ratio_db_best") is not None
                ]) else None
            ),
            "rej_reason_histogram": reasons,
        })
    return out


def capture_verdict(sweep: dict, per_bin: list[dict]) -> dict:
    """The radar-side answer to 'bad lock, or no verifiable signal anywhere in the gate?'"""
    yields = {r["bin"]: (r["hr_yield"] or 0.0) for r in per_bin}
    best_bin = max(yields, key=lambda b: (yields[b], -abs(b - np.mean(sweep["candidate_bins"]))))
    best_yield = yields[best_bin]
    locked = sweep["locked_bin"]
    locked_yield = yields.get(locked) if locked is not None else None

    if best_yield == 0.0:
        verdict = "no_verifiable_hr_at_any_in_gate_bin"
        detail = (
            "No candidate bin produced an AHET-verified rate in any window. Under this "
            "estimator and config the coverage loss is NOT attributable to the bin lock; "
            "re-locking cannot recover it."
        )
    elif locked_yield is not None and best_yield > locked_yield:
        verdict = "another_in_gate_bin_yields_more"
        detail = (
            f"bin {best_bin} yields {best_yield:.0%} against the locked bin "
            f"{locked}'s {locked_yield:.0%}. Consistent with a suboptimal lock, but NOT "
            "proof of one: an AHET pass is not by itself evidence of a true cardiac "
            "reading (see module docstring)."
        )
    else:
        verdict = "locked_bin_is_at_the_in_gate_maximum"
        detail = (
            f"No candidate bin beats the locked bin {locked} "
            f"({locked_yield:.0%} yield). Coverage loss is not a bin-choice problem."
        )

    pooled: dict[str, int] = {}
    for r in per_bin:
        for reason, count in r["rej_reason_histogram"].items():
            pooled[reason] = pooled.get(reason, 0) + count

    return {
        "capture_id": sweep["capture_id"],
        "verdict": verdict,
        "detail": detail,
        "locked_bin": locked,
        "locked_bin_hr_yield": locked_yield,
        "best_bin": int(best_bin),
        "best_bin_hr_yield": best_yield,
        "n_windows_swept": sweep["n_windows_swept"],
        "pooled_rejection_histogram": dict(
            sorted(pooled.items(), key=lambda kv: -kv[1])
        ),
    }


# ── Output ──────────────────────────────────────────────────────────────────


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    """Minimal CSV writer with LF endings (HANDOFF section 9: line endings are pinned)."""
    def cell(value) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (dict, list)):
            text = json.dumps(value, separators=(",", ":"), sort_keys=True)
        else:
            text = str(value)
        if any(ch in text for ch in ',"\n'):
            return '"' + text.replace('"', '""') + '"'
        return text

    lines = [",".join(columns)]
    lines += [",".join(cell(r.get(c)) for c in columns) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _reproduction_line(check: dict) -> str:
    if not check.get("checked"):
        return f"Warmup reproduction not checked: {check.get('reason')}."
    if check["reproduces"]:
        return (
            f"Window 0 reproduces the recorded live warmup evidence across all "
            f"{check['bins_compared']} candidate bins (hr_valid, rejection reason, BR rate "
            "and confidence), so the frame-range decode and the active config match what "
            "this capture actually ran."
        )
    return (
        f"**Window 0 does NOT reproduce the recorded live warmup evidence** — "
        f"{check['n_mismatches']} mismatched fields over {check['bins_compared']} bins. "
        "Offline no longer reproduces this session's live numbers (M4R-10); the sweep "
        "below is still valid as a present-day measurement but is not a replay of the run."
    )


def _num(value, digits: int = 1) -> str:
    """Format a table cell, with an explicit dash for 'no value' rather than a blank."""
    return "-" if value is None else f"{value:.{digits}f}"


def render_report(meta: dict, sweeps: list[dict], summaries: dict, verdicts: list[dict]) -> str:
    lines = [
        "# Per-candidate-bin sweep",
        "",
        f"Generated {meta['generated_utc']} - `scripts/diagnose_bin_sweep.py`",
        f"Git commit `{meta['git_commit']}` (tree clean: {meta['git_tree_clean']}) - "
        f"config `{meta['config_path']}` (`run_config_hash` = `{meta['run_config_hash'][:16]}`)",
        "",
        "Every candidate bin scored on every complete 30 s window, against the production "
        "DSP. **No Masimo file was opened** (HANDOFF section 3.5); yields below are "
        "radar-side only.",
        "",
        "## Verdicts",
        "",
        "| capture | windows | locked bin | locked yield | best bin | best yield | verdict |",
        "|---|---|---|---|---|---|---|",
    ]
    for v in verdicts:
        ly = "n/a" if v["locked_bin_hr_yield"] is None else f"{v['locked_bin_hr_yield']:.0%}"
        lines.append(
            f"| {v['capture_id']} | {v['n_windows_swept']} | {v['locked_bin']} | {ly} | "
            f"{v['best_bin']} | {v['best_bin_hr_yield']:.0%} | `{v['verdict']}` |"
        )
    lines += ["", "## Per capture", ""]

    for sweep in sweeps:
        cid = sweep["capture_id"]
        v = next(x for x in verdicts if x["capture_id"] == cid)
        lines += [
            f"### {cid}",
            "",
            f"{v['detail']}",
            "",
            f"Frames {sweep['n_frames']}, complete windows {sweep['n_windows_complete']} "
            f"(swept {sweep['n_windows_swept']}), candidate bins "
            f"{sweep['candidate_bins'][0]}-{sweep['candidate_bins'][-1]}, "
            f"sweep took {sweep['elapsed_s']:.0f} s.",
            "",
            _reproduction_line(sweep["window0_reproduces_warmup"]),
            "",
            "| bin | range m | HR valid | HR yield | HR median | HR cont. MAD | "
            "BR valid | BR median | mean rel dB | median AHET ratio dB | top rejection |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in summaries[cid]:
            top = max(r["rej_reason_histogram"].items(), key=lambda kv: kv[1], default=("-", 0))
            mark = " **(locked)**" if r["is_locked_bin"] else ""
            lines.append(
                "| {bin}{mark} | {rng:.3f} | {nv}/{nw} | {yld:.0%} | {med} | {mad} | "
                "{nbr}/{nw} | {brmed} | {rel} | {ahet} | `{reason}` x{count} |".format(
                    bin=r["bin"], mark=mark, rng=r["range_m"],
                    nv=r["n_hr_valid"], nw=r["n_windows"], yld=r["hr_yield"] or 0.0,
                    med=_num(r["hr_median_bpm"]), mad=_num(r["hr_continuity_mad_bpm"]),
                    nbr=r["n_br_valid"], brmed=_num(r["br_median_bpm"]),
                    rel=_num(r["mean_rel_db_in_window"]),
                    ahet=_num(r["ahet_ratio_db_median"], 2),
                    reason=top[0], count=top[1],
                )
            )
        lines += [
            "",
            "Pooled rejection reasons over all bins x windows: "
            + ", ".join(f"`{k}` x{n}" for k, n in v["pooled_rejection_histogram"].items()),
            "",
        ]
    return "\n".join(lines) + "\n"


ROW_COLUMNS = [
    "capture_id", "k", "frame_start", "frame_end", "bin", "range_m", "is_locked_bin",
    "energy", "energy_rank_in_window", "rel_db_in_window", "warmup_settled_energy_db",
    "warmup_energy_eligible", "dsp_failed", "dsp_error", "hr_valid", "hr_bpm",
    "fallback_hr_bpm", "rej_reason", "ahet_ratio_db_best", "accepted_candidate_rank",
    "spectrum_stage", "br_bpm", "br_confidence", "br_valid", "f_r_hz", "n_eca_skipped",
    "k_max_eff",
]

SUMMARY_COLUMNS = [
    "capture_id", "bin", "range_m", "is_locked_bin", "n_windows", "n_dsp_failed",
    "n_hr_valid", "hr_yield", "hr_windows_k", "hr_median_bpm", "hr_min_bpm", "hr_max_bpm",
    "hr_continuity_mad_bpm", "n_br_valid", "br_yield", "br_median_bpm",
    "mean_rel_db_in_window", "ahet_ratio_db_median", "rej_reason_histogram",
]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--captures", nargs="+", required=True, type=Path)
    ap.add_argument("--config", default=DEFAULT_CONFIG, type=Path)
    ap.add_argument("--out", default=REPO_ROOT / "results" / "diagnose" / "bin_sweep", type=Path)
    ap.add_argument("--max-windows", type=int, default=None,
                    help="sweep only the first N windows per capture (smoke runs)")
    ap.add_argument("--skip-input-hash", action="store_true",
                    help="do not SHA-256 the multi-GB adc_stream.bin inputs")
    args = ap.parse_args(argv)

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    seed = int(cfg.get("seed", 0))
    np.random.seed(seed)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(args.out) / stamp
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Output -> {out_dir}", flush=True)
    print("Self-checking the frame-range decoder against src/radar_io.read_adc_bin ...",
          flush=True)
    probe_cfg = ChirpConfig(
        num_adc_samples=int(cfg["profile"]["num_adc_samples"]),
        num_rx=int(cfg["profile"]["num_rx"]),
        num_tx=1,
        num_chirps_per_frame=int(cfg["profile"]["num_chirps_per_frame"]),
        num_frames=0,
        frame_rate_hz=float(cfg["session"]["frame_rate_hz"]),
        range_resolution_m=float(cfg["profile"]["range_resolution_m"]),
        iq_swap=bool(cfg["profile"]["iq_swap"]),
    )
    decode_check = verify_frame_range_decode(out_dir / "_selfcheck", probe_cfg)
    print("  decoder self-check PASSED", flush=True)

    sweeps, all_rows, all_summary, verdicts = [], [], [], []
    summaries: dict[str, list[dict]] = {}
    input_hashes: dict[str, str | None] = {}

    for capture_dir in args.captures:
        capture_dir = Path(capture_dir)
        print(f"Sweeping {capture_dir.name} ...", flush=True)
        if args.skip_input_hash:
            input_hashes[capture_dir.name] = None
        else:
            print("  hashing adc_stream.bin ...", flush=True)
            input_hashes[capture_dir.name] = sha256_file(capture_dir / "adc_stream.bin")
        sweep = sweep_capture(capture_dir, cfg, max_windows=args.max_windows)
        per_bin = summarise(sweep)
        sweeps.append(sweep)
        summaries[sweep["capture_id"]] = per_bin
        all_rows.extend(sweep["rows"])
        all_summary.extend(per_bin)
        verdicts.append(capture_verdict(sweep, per_bin))

    meta = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "script": "scripts/diagnose_bin_sweep.py",
        "git_commit": get_git_commit(),
        "git_tree_clean": is_tree_clean(),
        "config_path": str(Path(args.config).relative_to(REPO_ROOT)),
        "run_config_hash": run_config_hash(cfg),
        "seed": seed,
        "frame_rate_hz": FRAME_RATE_HZ,
        "frames_per_window": FRAMES_PER_WINDOW,
        "max_windows": args.max_windows,
        "decode_selfcheck": decode_check,
        "masimo_opened": False,
        "input_sha256": input_hashes,
        "captures": [
            {k: v for k, v in s.items() if k != "rows"} for s in sweeps
        ],
        "verdicts": verdicts,
    }

    (out_dir / "run_meta.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    write_csv(out_dir / "windows.csv", all_rows, ROW_COLUMNS)
    write_csv(out_dir / "per_bin.csv", all_summary, SUMMARY_COLUMNS)
    (out_dir / "report.md").write_text(
        render_report(meta, sweeps, summaries, verdicts), encoding="utf-8", newline="\n"
    )

    print("", flush=True)
    for v in verdicts:
        print(f"{v['capture_id']}: {v['verdict']}", flush=True)
        print(f"  {v['detail']}", flush=True)
    print(f"\nWrote {out_dir}", flush=True)


if __name__ == "__main__":
    main()
