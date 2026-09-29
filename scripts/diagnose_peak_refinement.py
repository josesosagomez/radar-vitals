#!/usr/bin/env python3
"""Radar-only legacy-vs-safe peak-refinement diagnostic.

The diagnostic deliberately returns the legacy refined frequency to the estimator, so
running it cannot change an estimate.  For every call it also calculates the proposed
safe result and records the fixed-spectrum counterfactual.  It accepts only the radar
scope of the committed development capture registry and never imports a reference loader.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import inspect
import json
from pathlib import Path
import subprocess
import sys
from typing import Callable

import numpy as np
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.m9_kotte_run import RadarGeometry, decode_frame_window  # noqa: E402
from src import vitals  # noqa: E402
from src.m4.bundle import sha256_path  # noqa: E402
from src.m4.capture_registry import DEFAULT_REGISTRY, load_registry  # noqa: E402
from src.m4.estimator_runner import _validate_capture_inputs  # noqa: E402
from src.window_pipeline import (  # noqa: E402
    ESTIMATOR_ID,
    LEGACY_ESTIMATOR_ID,
    SAFE_REFINEMENT_ESTIMATOR_ID,
    run_window_dsp,
)

SCHEMA = "peak_refinement_dual_calculation_v2"
WINDOW_FRAMES = 600
DEFAULT_CONFIG = REPO_ROOT / "scripts" / "live_demo_config.yaml"
DEFAULT_OUTPUT = REPO_ROOT / "reports" / "peak_refinement_diagnostic_2026-09-29.json"


@dataclass(frozen=True)
class SafeRefinement:
    refined_hz: float
    bin_center_hz: float
    delta_bins: float
    applied: bool
    reason: str


def safe_refine_frequency(
    spectrum: np.ndarray,
    freqs_hz: np.ndarray,
    peak_idx: int,
    band_hz: tuple[float, float],
) -> SafeRefinement:
    """Calculate bounded parabolic refinement without changing production behavior."""
    spectrum_arr = np.asarray(spectrum)
    freq_arr = np.asarray(freqs_hz, dtype=np.float64)
    if spectrum_arr.ndim != 1 or freq_arr.ndim != 1 or len(spectrum_arr) != len(freq_arr):
        raise ValueError("spectrum and frequency grid must be equal-length one-dimensional arrays")
    if type(peak_idx) is not int or not 0 <= peak_idx < len(freq_arr):
        raise ValueError("peak_idx must identify a frequency-grid element")
    centre = float(freq_arr[peak_idx])

    def fallback(reason: str) -> SafeRefinement:
        return SafeRefinement(centre, centre, 0.0, False, reason)

    try:
        band_values = tuple(float(value) for value in band_hz)
    except (TypeError, ValueError):
        return fallback("invalid_band")
    if len(band_values) != 2:
        return fallback("invalid_band")
    lo, hi = band_values
    if not np.isfinite(lo) or not np.isfinite(hi) or lo > hi:
        return fallback("invalid_band")

    if len(freq_arr) < 3:
        return fallback("grid_too_short")
    if not np.all(np.isfinite(freq_arr)):
        return fallback("nonfinite_frequency_grid")
    steps = np.diff(freq_arr)
    step = float(steps[0])
    if step <= 0.0 or not np.allclose(steps, step, rtol=1e-12, atol=1e-15):
        return fallback("nonuniform_frequency_grid")
    magnitudes = np.abs(spectrum_arr).astype(np.float64, copy=False)
    if not np.all(np.isfinite(magnitudes)):
        return fallback("nonfinite_magnitude")
    if peak_idx == 0 or peak_idx == len(magnitudes) - 1:
        return fallback("boundary_peak")
    alpha = float(magnitudes[peak_idx - 1])
    beta = float(magnitudes[peak_idx])
    gamma = float(magnitudes[peak_idx + 1])
    if not (beta > alpha and beta > gamma):
        return fallback("not_strict_local_maximum")
    denominator = alpha - 2.0 * beta + gamma
    if not np.isfinite(denominator) or denominator >= 0.0:
        return fallback("nonconcave_parabola")
    delta = 0.5 * (alpha - gamma) / denominator
    if not np.isfinite(delta):
        return fallback("nonfinite_delta")
    if abs(delta) > 0.5:
        return fallback("delta_out_of_bounds")
    refined = centre + float(delta) * step
    if not np.isfinite(refined) or refined < lo or refined > hi:
        return fallback("refined_frequency_out_of_band")
    return SafeRefinement(float(refined), centre, float(delta), True, "applied")


def _callsite_lines() -> dict[int, str]:
    source, first = inspect.getsourcelines(vitals.estimate_rate_from_phase)
    patterns = {
        "candidate_first_pass": "cand_hz = refine_freq_hz",
        "candidate_second_pass": "candidate_refined_hz[candidate_rank] = refine_freq_hz",
        "second_harmonic": "f_h2_ref = refine_freq_hz",
    }
    found: dict[int, str] = {}
    for offset, line in enumerate(source):
        for name, pattern in patterns.items():
            if pattern in line:
                found[first + offset] = name
    if set(found.values()) != set(patterns):
        raise RuntimeError("could not bind every refine_freq_hz call site")
    return found


class RefinementRecorder:
    """Drop-in legacy function that records a safe counterfactual and returns legacy."""

    def __init__(
        self,
        legacy: Callable[[np.ndarray, np.ndarray, int], float],
        *,
        heart_band_hz: tuple[float, float],
        ahet_deviation_hz: float,
    ) -> None:
        self.legacy = legacy
        self.heart_band_hz = heart_band_hz
        self.ahet_deviation_hz = ahet_deviation_hz
        self.callsites = _callsite_lines()
        self.context: dict[str, object] = {}
        self.rows: list[dict[str, object]] = []
        self._rank = -1
        self._legacy_candidate_hz: dict[int, float] = {}

    def begin_window(self, *, capture_id: str, k: int, locked_bin: int) -> None:
        self.context = {"capture_id": capture_id, "k": k, "locked_bin": locked_bin}
        self._rank = -1
        self._legacy_candidate_hz = {}

    def __call__(self, spectrum: np.ndarray, freqs: np.ndarray, peak_idx: int) -> float:
        caller_line = inspect.currentframe().f_back.f_lineno
        callsite = self.callsites.get(caller_line, f"unexpected_line_{caller_line}")
        if callsite == "candidate_first_pass":
            self._rank += 1
        rank = self._rank
        legacy_hz = float(self.legacy(spectrum, freqs, peak_idx))
        if callsite == "candidate_first_pass":
            self._legacy_candidate_hz[rank] = legacy_hz
        if callsite == "second_harmonic" and rank in self._legacy_candidate_hz:
            candidate_hz = self._legacy_candidate_hz[rank]
            band = (
                2.0 * candidate_hz - self.ahet_deviation_hz,
                2.0 * candidate_hz + self.ahet_deviation_hz,
            )
        else:
            band = self.heart_band_hz
        safe = safe_refine_frequency(spectrum, freqs, int(peak_idx), band)
        step = float(freqs[1] - freqs[0]) if len(freqs) > 1 else float("nan")
        legacy_delta = (
            (legacy_hz - float(freqs[peak_idx])) / step
            if np.isfinite(step) and step != 0.0
            else float("nan")
        )
        lo, hi = map(float, band)
        self.rows.append(
            {
                **self.context,
                "callsite": callsite,
                "candidate_rank": rank,
                "peak_idx": int(peak_idx),
                "bin_center_hz": float(freqs[peak_idx]),
                "legacy_refined_hz": legacy_hz,
                "legacy_delta_bins": float(legacy_delta),
                "allowed_band_hz": [lo, hi],
                "legacy_delta_out_of_bounds": bool(
                    np.isfinite(legacy_delta) and abs(legacy_delta) > 0.5
                ),
                "legacy_refined_out_of_band": bool(
                    not np.isfinite(legacy_hz) or legacy_hz < lo or legacy_hz > hi
                ),
                "safe_refined_hz": safe.refined_hz,
                "safe_delta_bins": safe.delta_bins,
                "safe_applied": safe.applied,
                "safe_reason": safe.reason,
                "accepted_candidate": False,
                "potential_final_bpm_difference": None,
            }
        )
        return legacy_hz

    def finish_window(self, hr_result: dict) -> None:
        accepted = int(hr_result.get("accepted_candidate_rank", -1))
        if accepted < 0:
            return
        current = [
            row for row in self.rows
            if row["capture_id"] == self.context["capture_id"] and row["k"] == self.context["k"]
            and row["candidate_rank"] == accepted
        ]
        by_site = {str(row["callsite"]): row for row in current}
        if "candidate_second_pass" not in by_site or "second_harmonic" not in by_site:
            return
        safe_final_hz = (
            0.5 * float(by_site["candidate_second_pass"]["safe_refined_hz"])
            + 0.25 * float(by_site["second_harmonic"]["safe_refined_hz"])
        )
        legacy_bpm = float(hr_result["rate_bpm"])
        difference = safe_final_hz * 60.0 - legacy_bpm
        for row in current:
            row["accepted_candidate"] = True
            row["potential_final_bpm_difference"] = float(difference)


def _git_state() -> tuple[str, bool]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True,
        capture_output=True, check=True,
    ).stdout.strip()
    dirty = bool(subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO_ROOT, text=True,
        capture_output=True, check=True,
    ).stdout.strip())
    return commit, dirty


def run_diagnostic(*, capture_root: Path, output: Path, config_path: Path) -> dict:
    if ESTIMATOR_ID != LEGACY_ESTIMATOR_ID:
        raise RuntimeError(
            "this historical diagnostic instruments the legacy estimator only; "
            "run it from clean commit 4280e34d691537d4465fd3d0a0d50b954692bff1 "
            "and do not overwrite the committed pre-fix artifact from a later estimator"
        )
    commit, dirty = _git_state()
    if dirty:
        raise RuntimeError("diagnostic must run from a clean committed checkout")
    registry = load_registry(DEFAULT_REGISTRY, root=capture_root)
    radar = registry.radar_scope()
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    heart_cfg = cfg["heart"]
    recorder = RefinementRecorder(
        vitals.refine_freq_hz,
        heart_band_hz=tuple(map(float, heart_cfg["band_hz"])),
        ahet_deviation_hz=float(heart_cfg["ahet_deviation_hz"]),
    )
    original = vitals.refine_freq_hz
    captures: list[dict[str, object]] = []
    try:
        vitals.refine_freq_hz = recorder
        for capture_id in sorted(radar.captures):
            capture = radar.capture(capture_id)
            metadata, _warmup, chirp_cfg, raw_path = _validate_capture_inputs(radar, capture)
            geometry = RadarGeometry(
                num_chirps_per_frame=chirp_cfg.num_chirps_per_frame,
                num_rx=chirp_cfg.num_rx,
                num_adc_samples=chirp_cfg.num_adc_samples,
                num_tx=chirp_cfg.num_tx,
                frame_period_s=1.0 / chirp_cfg.frame_rate_hz,
                iq_swap=chirp_cfg.iq_swap,
            )
            for k in range(capture.windows):
                cube = decode_frame_window(
                    raw_path,
                    geometry=geometry,
                    start_frame=k * WINDOW_FRAMES,
                    frame_count=WINDOW_FRAMES,
                )
                recorder.begin_window(
                    capture_id=capture_id, k=k, locked_bin=capture.recorded_lock
                )
                dsp = run_window_dsp(
                    cube, capture.recorded_lock, chirp_cfg.frame_rate_hz, cfg
                )
                recorder.finish_window(dsp["hr_result"])
            captures.append(
                {
                    "capture_id": capture_id,
                    "directory_identity": capture.directory,
                    "subject_reference_accessed": False,
                    "raw_adc_sha256": capture.adc_stream_sha256,
                    "metadata_sha256": capture.metadata_sha256,
                    "warmup_sha256": capture.warmup_sha256,
                    "capture_config_sha256": capture.capture_config_sha256,
                    "recorded_lock": capture.recorded_lock,
                    "windows": capture.windows,
                    "capture_git_commit": metadata.get("git_commit"),
                }
            )
    finally:
        vitals.refine_freq_hz = original

    reason_counts: dict[str, int] = {}
    for row in recorder.rows:
        reason = str(row["safe_reason"])
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    potential = [
        abs(float(row["potential_final_bpm_difference"]))
        for row in recorder.rows
        if row["accepted_candidate"] and row["callsite"] == "candidate_second_pass"
        and row["potential_final_bpm_difference"] is not None
    ]
    safe_fallback_count = sum(not bool(row["safe_applied"]) for row in recorder.rows)
    legacy_delta_out_of_bounds_count = sum(
        bool(row["legacy_delta_out_of_bounds"]) for row in recorder.rows
    )
    legacy_refined_out_of_band_count = sum(
        bool(row["legacy_refined_out_of_band"]) for row in recorder.rows
    )
    artifact = {
        "schema": SCHEMA,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "diagnostic_only": True,
        "returned_estimator_behavior": "legacy_unchanged",
        "counterfactual_scope": "fixed_spectrum_immediate_substitution_only",
        "reference_data_accessed": False,
        "git_commit": commit,
        "git_dirty": False,
        "legacy_estimator_id": LEGACY_ESTIMATOR_ID,
        "corrected_estimator_id_reserved": SAFE_REFINEMENT_ESTIMATOR_ID,
        "registry_path": str(DEFAULT_REGISTRY.relative_to(REPO_ROOT)),
        "registry_sha256": sha256_path(DEFAULT_REGISTRY),
        "config_path": str(config_path.relative_to(REPO_ROOT)),
        "config_sha256": sha256_path(config_path),
        "seed": int(cfg.get("seed", 42)),
        "captures": captures,
        "summary": {
            "capture_count": len(captures),
            "window_count": sum(int(item["windows"]) for item in captures),
            "refinement_call_count": len(recorder.rows),
            "safe_reason_counts": reason_counts,
            "safe_fallback_count": safe_fallback_count,
            "legacy_delta_out_of_bounds_count": legacy_delta_out_of_bounds_count,
            "legacy_refined_out_of_band_count": legacy_refined_out_of_band_count,
            "accepted_window_count_with_counterfactual": len(potential),
            "max_abs_potential_final_bpm_difference": max(potential) if potential else None,
        },
        "calls": recorder.rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture-root", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    artifact = run_diagnostic(
        capture_root=args.capture_root.resolve(),
        output=args.output.resolve(),
        config_path=args.config.resolve(),
    )
    print(json.dumps(artifact["summary"], indent=2))


if __name__ == "__main__":
    main()
