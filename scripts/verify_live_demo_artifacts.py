"""Verify live_demo.py warmup-bin run artifacts.

Intended for Goal 5 live rehearsals after running:
    python scripts/live_demo.py --live-session <name> --duration-s 90
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from src.range_coordinates import (
    RANGE_COORDINATE_MODEL, bin_range_m, range_bias_m, range_coordinate_fields,
)


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _parse_float(text: str) -> float | None:
    if text == "":
        return None
    value = float(text)
    return value if math.isfinite(value) else None


def _same_value(csv_text: str, json_value: object, tol: float = 0.01) -> bool:
    csv_value = _parse_float(csv_text)
    if json_value is None:
        return csv_value is None
    if csv_value is None:
        return False
    return abs(csv_value - float(json_value)) <= tol


def _check(name: str, ok: bool, details: str = "") -> tuple[str, bool, str]:
    return name, ok, details


def verify_run(run_dir: Path, expect_mode: str | None = None) -> int:
    checks: list[tuple[str, bool, str]] = []

    meta_path = run_dir / "run_metadata.json"
    warmup_path = run_dir / "warmup_bin_selection.json"
    csv_path = run_dir / "live_estimates.csv"
    npz_path = run_dir / "live_intermediates.npz"

    checks.append(_check("run_dir_exists", run_dir.exists(), str(run_dir)))
    checks.append(_check("metadata_exists", meta_path.exists(), str(meta_path)))
    checks.append(_check("warmup_json_exists", warmup_path.exists(), str(warmup_path)))
    checks.append(_check("csv_exists", csv_path.exists(), str(csv_path)))
    checks.append(_check("intermediates_npz_exists", npz_path.exists(), str(npz_path)))
    if not all(ok for _, ok, _ in checks):
        return _print_results(checks)

    meta = _load_json(meta_path)
    warmup = _load_json(warmup_path)
    with csv_path.open("r", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    selected_bin = warmup.get("selected_bin")
    candidates = warmup.get("candidates", [])
    candidate_bins = [c.get("bin") for c in candidates]
    selected_candidate = next(
        (c for c in candidates if c.get("bin") == selected_bin),
        None,
    )
    locked_bins = sorted({
        int(r["locked_bin"]) for r in rows
        if r.get("locked_bin") not in (None, "")
    })

    checks.extend([
        _check(
            "mode_matches",
            expect_mode is None or meta.get("mode") == expect_mode,
            f"mode={meta.get('mode')!r}",
        ),
        _check(
            "completed",
            meta.get("completion_status") == "completed",
            f"completion_status={meta.get('completion_status')!r}",
        ),
        _check(
            "metadata_source_warmup_auto",
            meta.get("locked_bin_source") == "warmup_auto",
            f"locked_bin_source={meta.get('locked_bin_source')!r}",
        ),
        _check(
            "selected_bin_consistent",
            meta.get("locked_bin") == selected_bin == meta.get("warmup_selected_bin"),
            f"meta.locked_bin={meta.get('locked_bin')!r}, warmup.selected_bin={selected_bin!r}",
        ),
        _check(
            "confidence_consistent",
            meta.get("warmup_selection_confidence") == warmup.get("selected_confidence"),
            f"metadata={meta.get('warmup_selection_confidence')!r}, json={warmup.get('selected_confidence')!r}",
        ),
        _check(
            "warmup_path_recorded",
            Path(str(meta.get("warmup_selection_path", ""))).name
            == "warmup_bin_selection.json",
            str(meta.get("warmup_selection_path")),
        ),
        _check("candidate_bins_present", len(candidate_bins) > 0, str(candidate_bins)),
        _check("selected_candidate_present", selected_candidate is not None, str(selected_bin)),
        _check("csv_has_rows", len(rows) > 0, f"rows={len(rows)}"),
        _check(
            "csv_one_locked_bin",
            locked_bins == [selected_bin],
            f"locked_bins={locked_bins}, selected={selected_bin}",
        ),
        _check("npz_nonempty", npz_path.stat().st_size > 0, f"bytes={npz_path.stat().st_size}"),
    ])

    cfg = meta.get("config", {})
    coordinate_cfg = {"profile": {
        **cfg.get("profile", {}),
        "range_resolution_m": meta["range_resolution_m"],
    }}
    try:
        bias = range_bias_m(coordinate_cfg)
        coordinates = range_coordinate_fields(selected_bin, coordinate_cfg)
    except ValueError as exc:
        checks.append(_check("range_coordinates_valid", False, str(exc)))
        return _print_results(checks)
    if "range_resolution_m" in cfg.get("profile", {}):
        checks.append(_check(
            "range_resolution_consistent",
            cfg["profile"]["range_resolution_m"] == meta["range_resolution_m"],
        ))
    if bias != 0.0:
        provenance = meta.get("range_calibration") or {}
        specification = cfg.get("range_calibration") or {}
        embedded = provenance.get("record") if isinstance(provenance, dict) else None
        bound_path = _ROOT / str(specification.get("record_path", ""))
        provenance_ok = False
        if isinstance(embedded, dict) and bound_path.is_file():
            expected_digest = specification.get("record_sha256")
            provenance_ok = (
                specification.get("scope") == "development_live_demo_only"
                and provenance.get("application") == "python_range_coordinates_only"
                and provenance.get("record_sha256") == expected_digest
                and hashlib.sha256(bound_path.read_bytes()).hexdigest() == expected_digest
                and embedded.get("schema_version") == 1
                and embedded.get("range_bias_m") == bias
                and embedded == _load_json(bound_path)
            )
        checks.append(_check("range_calibration_provenance_bound", provenance_ok))
    if bias != 0.0 or "range_coordinate_model" in warmup or "range_coordinate_model" in meta:
        def matches(value: object, expected: object) -> bool:
            if expected is None:
                return value is None
            try:
                return math.isclose(float(value), float(expected), rel_tol=0, abs_tol=0.00005)
            except (TypeError, ValueError):
                return False

        checks.append(_check(
            "range_coordinate_model_consistent",
            meta.get("range_coordinate_model") == warmup.get("range_coordinate_model")
            == RANGE_COORDINATE_MODEL,
        ))
        for name in ("range_bias_m", "selected_raw_range_m", "selected_corrected_range_m"):
            checks.append(_check(
                name + "_consistent",
                matches(meta.get(name), coordinates[name])
                and matches(warmup.get(name), coordinates[name]),
            ))
        checks.append(_check(
            "selected_range_m_corrected",
            matches(warmup.get("selected_range_m"), coordinates["selected_corrected_range_m"]),
        ))
        checks.append(_check(
            "candidate_ranges_consistent",
            all(
                matches(c.get("raw_range_m"), c["bin"] * float(meta["range_resolution_m"]))
                and matches(c.get("corrected_range_m"), bin_range_m(c["bin"], coordinate_cfg))
                and matches(c.get("range_m"), bin_range_m(c["bin"], coordinate_cfg))
                for c in candidates
            ),
        ))
    explicit_candidates = cfg.get("bin_selection", {}).get("candidate_bins")
    if explicit_candidates is None and selected_bin is not None:
        selected_range_m = bin_range_m(selected_bin, coordinate_cfg)
        lo, hi = cfg["protocol"]["subject_distance_m"]
        checks.append(_check(
            "selected_range_inside_protocol",
            float(lo) <= selected_range_m <= float(hi),
            f"range_m={selected_range_m:.4f}, protocol=[{lo}, {hi}]",
        ))

    if expect_mode == "live":
        packet_stats = meta.get("live_packet_stats") or {}
        packets_received = packet_stats.get("packets_received", packet_stats.get("n_received", 0))
        checks.extend([
            _check(
                "live_raw_mirror_hash_present",
                bool(meta.get("live_raw_mirror_hash")),
                str(meta.get("live_raw_mirror_hash")),
            ),
            _check(
                "live_packet_stats_present",
                bool(packet_stats),
                str(packet_stats),
            ),
            _check(
                "live_packets_received",
                int(packets_received) > 0,
                f"packets_received={packets_received}",
            ),
        ])

        if meta.get("prospective_study_mode"):
            validity_path = run_dir / "frame_validity.npy"
            receipt_path = run_dir / "sealed_radar_receipt.json"
            checks.extend([
                _check(
                    "prospective_frame0_event",
                    meta.get("frame0_epoch_source")
                    == "frame_index_0_start_assignment_pc_utc",
                    str(meta.get("frame0_epoch_source")),
                ),
                _check("frame_validity_exists", validity_path.is_file(), str(validity_path)),
                _check("sealed_radar_receipt_exists", receipt_path.is_file(), str(receipt_path)),
                _check(
                    "capture_commit_clean",
                    meta.get("git_dirty") is False and meta.get("git_commit") not in {None, "unknown"},
                    f"commit={meta.get('git_commit')!r} dirty={meta.get('git_dirty')!r}",
                ),
            ])
            if validity_path.is_file():
                validity = np.load(validity_path, allow_pickle=False)
                checks.extend([
                    _check(
                        "frame_validity_boolean_1d",
                        validity.dtype == np.bool_ and validity.ndim == 1,
                        f"dtype={validity.dtype} shape={validity.shape}",
                    ),
                    _check(
                        "frame_validity_count_matches",
                        validity.size == int(packet_stats.get("n_frames", -1)),
                        f"map={validity.size} n_frames={packet_stats.get('n_frames')}",
                    ),
                    _check(
                        "frame_invalid_count_matches",
                        int(np.count_nonzero(~validity))
                        == int(packet_stats.get("n_invalid_frames", -1)),
                        f"map_invalid={int(np.count_nonzero(~validity))} "
                        f"metadata={packet_stats.get('n_invalid_frames')}",
                    ),
                ])

    if rows and selected_candidate is not None and not selected_candidate.get("failed"):
        first = rows[0]
        checks.extend([
            _check(
                "first_row_hr_matches_cached_dsp",
                _same_value(first.get("hr_bpm_raw", ""), selected_candidate.get("hr_raw")),
                f"csv={first.get('hr_bpm_raw')!r}, json={selected_candidate.get('hr_raw')!r}",
            ),
            _check(
                "first_row_br_matches_cached_dsp",
                _same_value(first.get("br_bpm", ""), selected_candidate.get("br_bpm")),
                f"csv={first.get('br_bpm')!r}, json={selected_candidate.get('br_bpm')!r}",
            ),
        ])

    return _print_results(checks)


def _print_results(checks: list[tuple[str, bool, str]]) -> int:
    for name, ok, details in checks:
        suffix = f" - {details}" if details else ""
        print(f"{name}: {'PASS' if ok else 'FAIL'}{suffix}")
    return 0 if all(ok for _, ok, _ in checks) else 1


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--expect-mode", choices=("live", "replay"), default=None)
    args = ap.parse_args()
    raise SystemExit(verify_run(args.run_dir, args.expect_mode))


if __name__ == "__main__":
    main()
