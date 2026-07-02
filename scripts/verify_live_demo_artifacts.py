"""Verify live_demo.py run artifacts (warmup-auto, pinned, and relock runs).

Check groups (notes/relocking_bin_plan.md):
- Always-applicable: run dir, metadata, CSV, NPZ, completion status, row count.
- Warmup-auto-only: warmup_bin_selection.json present and consistent; first
  CSV locked bin matches warmup_selected_bin; first-row cached-DSP matching.
- Pinned-source (manual/manifest): no warmup JSON required; when relocking is
  disabled for pinned sources, exactly one locked bin throughout the CSV.
- Relock-sequence: every CSV locked-bin change must be explained by an
  accepted event in relock_events.json, in order; the final metadata
  locked_bin must equal the last bin of that accepted sequence.

Usage:
    python scripts/verify_live_demo_artifacts.py <run_dir> [--expect-mode live|replay]
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


def _load_json(path: Path):
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


def _csv_bin_sequence(rows: list[dict]) -> list[int]:
    """Ordered locked-bin sequence with consecutive duplicates collapsed."""
    seq: list[int] = []
    for row in rows:
        text = row.get("locked_bin")
        if text in (None, ""):
            continue
        b = int(text)
        if not seq or seq[-1] != b:
            seq.append(b)
    return seq


def verify_run(run_dir: Path, expect_mode: str | None = None) -> int:
    checks: list[tuple[str, bool, str]] = []

    meta_path = run_dir / "run_metadata.json"
    warmup_path = run_dir / "warmup_bin_selection.json"
    relock_path = run_dir / "relock_events.json"
    csv_path = run_dir / "live_estimates.csv"
    npz_path = run_dir / "live_intermediates.npz"

    # ── Always-applicable existence checks ────────────────────────────────────
    checks.append(_check("run_dir_exists", run_dir.exists(), str(run_dir)))
    checks.append(_check("metadata_exists", meta_path.exists(), str(meta_path)))
    checks.append(_check("csv_exists", csv_path.exists(), str(csv_path)))
    checks.append(_check("intermediates_npz_exists", npz_path.exists(), str(npz_path)))
    if not all(ok for _, ok, _ in checks):
        return _print_results(checks)

    meta = _load_json(meta_path)
    with csv_path.open("r", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    bin_seq = _csv_bin_sequence(rows)
    source = meta.get("locked_bin_source")
    relock_enabled = bool(meta.get("relock_enabled", False))

    events = _load_json(relock_path) if relock_path.exists() else []
    accepted = [e for e in events if e.get("switched")]

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
        _check("csv_has_rows", len(rows) > 0, f"rows={len(rows)}"),
        _check(
            "npz_nonempty",
            npz_path.stat().st_size > 0,
            f"bytes={npz_path.stat().st_size}",
        ),
    ])

    # ── Source-dependent checks ───────────────────────────────────────────────
    initial_bin: int | None = None

    if source == "warmup_auto":
        checks.append(
            _check("warmup_json_exists", warmup_path.exists(), str(warmup_path))
        )
        if not warmup_path.exists():
            return _print_results(checks)

        warmup = _load_json(warmup_path)
        selected_bin = warmup.get("selected_bin")
        candidates = warmup.get("candidates", [])
        candidate_bins = [c.get("bin") for c in candidates]
        selected_candidate = next(
            (c for c in candidates if c.get("bin") == selected_bin), None
        )
        initial_bin = selected_bin

        checks.extend([
            _check(
                "warmup_selected_bin_consistent",
                meta.get("warmup_selected_bin") == selected_bin,
                f"meta={meta.get('warmup_selected_bin')!r}, json={selected_bin!r}",
            ),
            _check(
                "confidence_consistent",
                meta.get("warmup_selection_confidence")
                == warmup.get("selected_confidence"),
                f"metadata={meta.get('warmup_selection_confidence')!r}, "
                f"json={warmup.get('selected_confidence')!r}",
            ),
            _check(
                "warmup_path_recorded",
                Path(str(meta.get("warmup_selection_path", ""))).name
                == "warmup_bin_selection.json",
                str(meta.get("warmup_selection_path")),
            ),
            _check(
                "candidate_bins_present",
                len(candidate_bins) > 0,
                str(candidate_bins),
            ),
            _check(
                "selected_candidate_present",
                selected_candidate is not None,
                str(selected_bin),
            ),
            _check(
                "first_csv_bin_matches_warmup",
                bool(bin_seq) and bin_seq[0] == selected_bin,
                f"first_csv_bin={bin_seq[0] if bin_seq else None}, "
                f"warmup_selected={selected_bin}",
            ),
        ])

        cfg = meta.get("config", {})
        explicit_candidates = cfg.get("bin_selection", {}).get("candidate_bins")
        if explicit_candidates is None and selected_bin is not None:
            res = float(meta["range_resolution_m"])
            selected_range_m = selected_bin * res
            lo, hi = cfg["protocol"]["subject_distance_m"]
            checks.append(_check(
                "selected_range_inside_protocol",
                float(lo) <= selected_range_m <= float(hi),
                f"range_m={selected_range_m:.4f}, protocol=[{lo}, {hi}]",
            ))

        if rows and selected_candidate is not None and not selected_candidate.get("failed"):
            first = rows[0]
            checks.extend([
                _check(
                    "first_row_hr_matches_cached_dsp",
                    _same_value(
                        first.get("hr_bpm_raw", ""), selected_candidate.get("hr_raw")
                    ),
                    f"csv={first.get('hr_bpm_raw')!r}, "
                    f"json={selected_candidate.get('hr_raw')!r}",
                ),
                _check(
                    "first_row_br_matches_cached_dsp",
                    _same_value(
                        first.get("br_bpm", ""), selected_candidate.get("br_bpm")
                    ),
                    f"csv={first.get('br_bpm')!r}, "
                    f"json={selected_candidate.get('br_bpm')!r}",
                ),
            ])

    elif source in ("manual", "manifest"):
        checks.append(_check(
            "pinned_no_warmup_json",
            not warmup_path.exists(),
            f"source={source!r}, warmup_json_present={warmup_path.exists()}",
        ))
        initial_bin = bin_seq[0] if bin_seq else None
        if not relock_enabled:
            checks.append(_check(
                "pinned_single_locked_bin",
                len(set(bin_seq)) == 1 and initial_bin == meta.get("locked_bin"),
                f"csv_bins={sorted(set(bin_seq))}, meta.locked_bin={meta.get('locked_bin')}",
            ))
    else:
        checks.append(_check(
            "locked_bin_source_recognised",
            False,
            f"locked_bin_source={source!r}",
        ))

    # ── Relock-sequence checks ────────────────────────────────────────────────
    if events:
        checks.append(_check(
            "relock_event_counts_consistent",
            meta.get("n_relock_scans") == len(events)
            and meta.get("n_relock_switches") == len(accepted),
            f"meta scans/switches={meta.get('n_relock_scans')}/"
            f"{meta.get('n_relock_switches')}, events={len(events)}, "
            f"accepted={len(accepted)}",
        ))

    if initial_bin is not None:
        expected_seq = [initial_bin]
        chain_ok = True
        for event in accepted:
            if event.get("old_locked_bin") != expected_seq[-1]:
                chain_ok = False
                break
            expected_seq.append(event.get("selected_bin"))
        checks.append(_check(
            "relock_event_chain_ordered",
            chain_ok,
            f"accepted_events={[(e.get('old_locked_bin'), e.get('selected_bin')) for e in accepted]}",
        ))
        checks.append(_check(
            "csv_bins_explained_by_relock_events",
            bin_seq == expected_seq,
            f"csv_seq={bin_seq}, expected={expected_seq}",
        ))
        checks.append(_check(
            "final_locked_bin_matches_sequence",
            meta.get("locked_bin") == expected_seq[-1],
            f"meta.locked_bin={meta.get('locked_bin')}, "
            f"sequence_final={expected_seq[-1]}",
        ))

    # ── Live-mode checks ──────────────────────────────────────────────────────
    if expect_mode == "live":
        packet_stats = meta.get("live_packet_stats") or {}
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
                int(packet_stats.get("n_received", 0)) > 0,
                f"n_received={packet_stats.get('n_received')}",
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
