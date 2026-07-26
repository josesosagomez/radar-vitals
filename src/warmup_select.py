"""The one warmup / range-bin-selection callable (M4 plan §5.1, Stage 0).

Extracted verbatim from `scripts/live_demo.py:_run_warmup_selection` and its helpers.
Both the live path and the M4 offline harness **import** this — neither reimplements
it. M4 applies these same semantics to its `k = 0` window (plan §6.1), so a duplicate
here would let the offline bin lock drift away from the recorded live one (M4R-10).
"""
from __future__ import annotations

import sys
import time

import numpy as np
from scipy.fft import fft as sp_fft

from .window_pipeline import run_window_dsp


def derive_candidate_bins(cfg: dict) -> list[int]:
    """Candidate range bins from protocol distance + range resolution, or explicit list."""
    bsel = cfg.get("bin_selection", {})
    explicit = bsel.get("candidate_bins")
    if explicit is not None:
        return [int(b) for b in explicit]
    dist_range = cfg["protocol"]["subject_distance_m"]
    res = float(cfg["profile"]["range_resolution_m"])
    n_adc = int(cfg["profile"]["num_adc_samples"])
    lo = int(np.ceil(float(dist_range[0]) / res))
    hi = int(np.floor(float(dist_range[1]) / res))
    return list(range(max(0, lo), min(n_adc - 1, hi) + 1))


def range_energy_by_bin(
    cube: np.ndarray, candidate_bins: list[int]
) -> dict[int, float]:
    """Mean power per range bin — Hann window + sp_fft, same as extract_chest_phase."""
    n_adc = cube.shape[3]
    hann_win = np.hanning(n_adc).astype(np.float32)
    windowed = cube * hann_win                  # broadcast over last dim
    range_fft = sp_fft(windowed, axis=3)
    return {
        b: float(np.mean(np.abs(range_fft[:, :, :, b]) ** 2))
        for b in candidate_bins
    }


def resolve_locked_bin(
    args_locked_bin: int | None,
    manifest_bin: int | None,
    bin_selection_enabled: bool,
) -> tuple[int | None, str | None, bool]:
    """Return (locked_bin_or_none, source_or_none, warmup_pending).

    (None, None, False) is the error sentinel — caller must call sys.exit().
    """
    if args_locked_bin is not None:
        return args_locked_bin, "manual", False
    if manifest_bin is not None:
        return manifest_bin, "manifest", False
    if bin_selection_enabled:
        return None, "warmup_auto", True
    return None, None, False


def run_warmup_selection(
    cube: np.ndarray,
    candidate_bins: list[int],
    cfg: dict,
    fs: float,
    dsp_fn=run_window_dsp,
) -> tuple[int, dict | None, dict]:
    """Scan candidate bins, score by radar evidence, return the best bin.

    Returns (selected_bin, winning_dsp_dict_or_none, evidence_for_json).
    winning_dsp_dict is None when every candidate's DSP call raised (all-fail
    case); the caller should skip first-row emission and let the next hop call
    run_window_dsp normally on the fallback bin.

    Energy-eligibility prior: a bin's settled-window energy (energy_eligibility_
    min_settled_db below the strongest candidate) gates whether an hr_valid pass
    can even be CONSIDERED. This is an eligibility partition, not just a score
    bonus: an energy-ineligible bin can never outvote an energy-eligible one
    (via breathing evidence or anything else), it can only win if no
    energy-eligible candidate's DSP call succeeded (see winner_pool below).
    Empirical basis: 4 recorded sessions / 1 subject (2026-07-14/15) — a lone
    AHET pass at a skirt bin, or genuine cardiac leakage into a low-energy skirt
    bin, both otherwise outvoted the true chest bin. Assumes the protocol scene
    (single seated subject is the dominant reflector inside the distance gate);
    not yet validated across subjects/postures/competing reflectors — see
    notes/approach.md and re-check against the 10-subject study.
    """
    if not candidate_bins:
        raise ValueError(
            "warmup bin selection got an empty candidate_bins list — check "
            "protocol.subject_distance_m / bin_selection.candidate_bins against "
            "profile.range_resolution_m / profile.num_adc_samples (the derived "
            "gate may be empty or entirely out of ADC bounds)."
        )

    res = float(cfg["profile"]["range_resolution_m"])
    dist_range = cfg["protocol"]["subject_distance_m"]
    center_m = (float(dist_range[0]) + float(dist_range[1])) / 2.0

    t0 = time.monotonic()

    energies = range_energy_by_bin(cube, candidate_bins)
    sorted_by_energy = sorted(candidate_bins, key=lambda b: energies[b], reverse=True)
    energy_rank = {b: i + 1 for i, b in enumerate(sorted_by_energy)}

    bcfg = cfg.get("bin_selection", {}) or {}

    threshold_db = float(bcfg.get("energy_eligibility_min_settled_db", -12.0))
    if not np.isfinite(threshold_db) or threshold_db > 0:
        raise ValueError(
            "bin_selection.energy_eligibility_min_settled_db must be finite and "
            f"<= 0 (it is a dB deficit below the strongest candidate), got {threshold_db!r}"
        )

    requested_settle_skip_s = float(bcfg.get("settle_skip_s", 5.0))
    if not np.isfinite(requested_settle_skip_s) or requested_settle_skip_s < 0:
        raise ValueError(
            "bin_selection.settle_skip_s must be finite and >= 0, got "
            f"{requested_settle_skip_s!r}"
        )

    # Settled-window energy for the eligibility prior. Measured AFTER the settling
    # transient so the same transient cannot both fake an AHET pass and inflate
    # the bin's energy past the gate (the 20260714 sweep failure had 1 dB of
    # margin on full-window energy vs 18 dB on settled energy).
    skip_frames_requested = int(round(requested_settle_skip_s * fs))
    settle_skip_applied = 0 < skip_frames_requested < cube.shape[0]
    settle_skip_fallback_full_window = skip_frames_requested > 0 and not settle_skip_applied
    settle_skip_frames_applied = skip_frames_requested if settle_skip_applied else 0
    if settle_skip_fallback_full_window:
        print(
            f"  WARNING: settle_skip_s={requested_settle_skip_s:.2f}s "
            f"({skip_frames_requested} frames) is >= the {cube.shape[0]}-frame warmup "
            "window; using the FULL window for the energy-eligibility prior "
            "(settling transient included).",
            file=sys.stderr,
        )
    settled_cube = cube[settle_skip_frames_applied:] if settle_skip_applied else cube
    settled_energies = range_energy_by_bin(settled_cube, candidate_bins)
    e_ref = max(settled_energies.values())
    settled_db = {
        b: float(10.0 * np.log10(e / e_ref)) if (e > 0 and e_ref > 0) else float("-inf")
        for b, e in settled_energies.items()
    }
    # Eligibility depends only on settled energy — computed for every candidate,
    # including ones whose DSP call later raises, so a mixed-fallback session
    # (eligible bins fail DSP, only an ineligible bin succeeds) is diagnosable.
    energy_eligible = {b: settled_db[b] >= threshold_db for b in candidate_bins}
    all_candidates_energy_ineligible = not any(energy_eligible.values())

    results: list[dict] = []
    for b in candidate_bins:
        try:
            dsp = dsp_fn(cube, b, fs, cfg)
            results.append({
                "bin": b, "dsp": dsp,
                "energy": energies[b], "energy_rank": energy_rank[b],
                "failed": False, "error": None,
            })
        except Exception as exc:
            print(f"  WARNING: warmup DSP failed for bin {b}: {exc}", file=sys.stderr)
            results.append({
                "bin": b, "dsp": None,
                "energy": energies[b], "energy_rank": energy_rank[b],
                "failed": True, "error": str(exc),
            })

    t_scan_ms = (time.monotonic() - t0) * 1000.0
    good = [r for r in results if not r["failed"]]
    eligible_dsp_success_count = 0
    fallback_used = False

    if not good:
        fallback = min(results, key=lambda r: r["energy_rank"])
        selected_bin = fallback["bin"]
        winning_dsp = None
        selection_confidence = "low"
        selection_reason = "all_dsp_failed_energy_fallback"
        # Not drawn from a normal energy-eligible + DSP-succeeded pool either —
        # fallback_used must stay a single reliable "don't trust this pick without
        # checking selection_reason" signal across BOTH failure axes (all DSP
        # failed vs. no energy-eligible DSP success), not just the latter.
        fallback_used = True
        print(
            f"  WARNING: warmup DSP failed for every candidate. "
            f"Falling back to highest-energy bin {selected_bin}; no HR for first window.",
            file=sys.stderr,
        )
    else:
        def _br_conf_order(conf: str) -> int:
            return {"high": 0, "medium": 1, "low": 2}.get(conf, 3)

        for r in good:
            dsp = r["dsp"]
            eligible = energy_eligible[r["bin"]]
            # hr_valid only counts if the bin's settled energy is plausibly the
            # chest: a lone AHET pass at a skirt bin >12 dB below the strongest
            # candidate must not outvote the body's dominant return.
            granted = bool(dsp["hr_valid"]) and eligible
            r["hr_bonus_granted"] = granted
            r["hr_bonus_vetoed"] = bool(dsp["hr_valid"]) and not granted
            score = 0
            if granted:
                score += 1000
            br_conf = dsp["br_confidence"]
            if br_conf == "high":
                score += 250
            elif br_conf == "medium":
                score += 100
            else:
                score -= 100
            if dsp["br_valid"]:
                score += 50
            score -= 5 * r["energy_rank"]
            r["score"] = score

        vetoed_hr_candidates = [r for r in good if r["hr_bonus_vetoed"]]
        if vetoed_hr_candidates:
            listing = ", ".join(
                f"bin {r['bin']} ({settled_db[r['bin']]:.1f} dB)"
                for r in vetoed_hr_candidates
            )
            print(
                f"  WARNING: hr_valid candidate(s) energy-ineligible "
                f"(< {threshold_db:.1f} dB rel strongest settled candidate) and "
                f"excluded from the primary energy-eligible pool: {listing}. "
                f"(May still be selected as a low-confidence fallback if no "
                f"eligible candidate's DSP succeeds.)",
                file=sys.stderr,
            )

        # Eligibility partition: an energy-ineligible bin can win ONLY if no
        # energy-eligible candidate's DSP call succeeded — never by outranking
        # an eligible bin on breathing evidence or anything else in the sort key.
        eligible_good = [r for r in good if energy_eligible[r["bin"]]]
        eligible_dsp_success_count = len(eligible_good)
        fallback_used = eligible_dsp_success_count == 0
        winner_pool = eligible_good if eligible_good else good

        winner_pool.sort(key=lambda r: (
            -r["score"],
            int(not r["hr_bonus_granted"]),
            _br_conf_order(r["dsp"]["br_confidence"]),
            int(not r["dsp"]["br_valid"]),
            r["energy_rank"],
            abs(r["bin"] * res - center_m),
            r["bin"],
        ))
        winner = winner_pool[0]
        selected_bin = winner["bin"]
        winning_dsp = winner["dsp"]

        if fallback_used:
            selection_confidence = "low"
        elif winner["hr_bonus_granted"] and winning_dsp["br_valid"]:
            selection_confidence = "high"
        elif winning_dsp["br_valid"] and winning_dsp["br_confidence"] in ("high", "medium"):
            selection_confidence = "medium"
        else:
            selection_confidence = "low"

        selection_reason = (
            f"score={winner['score']}"
            f"_hr={int(winner['hr_bonus_granted'])}"
            f"_br={winning_dsp['br_confidence']}"
        )
        if winner["hr_bonus_vetoed"]:
            selection_reason += "_hr_bonus_vetoed"
        if fallback_used:
            selection_reason += "_no_energy_eligible_dsp_success"

    if selection_confidence == "low":
        print(
            f"  WARNING: warmup selection confidence is low for bin {selected_bin} "
            f"(~{selected_bin * res:.2f} m). Check warmup_bin_selection.json.",
            file=sys.stderr,
        )

    evidence: dict = {
        "selected_bin": int(selected_bin),
        "selected_range_m": round(selected_bin * res, 4),
        "selected_confidence": selection_confidence,
        "selection_reason": selection_reason,
        "t_warmup_scan_ms": round(t_scan_ms, 1),
        "energy_eligibility_min_settled_db": threshold_db,
        "settle_skip_s": requested_settle_skip_s,
        "settle_skip_frames_applied": settle_skip_frames_applied,
        "settle_skip_fallback_full_window": bool(settle_skip_fallback_full_window),
        "all_candidates_energy_ineligible": bool(all_candidates_energy_ineligible),
        "eligible_dsp_success_count": int(eligible_dsp_success_count),
        "fallback_used": bool(fallback_used),
        "candidates": [],
    }
    for r in results:
        _sdb = settled_db[r["bin"]]
        cand: dict = {
            "bin": r["bin"],
            "range_m": round(r["bin"] * res, 4),
            "energy": r["energy"],
            "energy_rank": r["energy_rank"],
            "settled_energy_db": round(_sdb, 1) if np.isfinite(_sdb) else None,
            "energy_eligible": bool(energy_eligible[r["bin"]]),
            "failed": r["failed"],
            "error": r["error"],
        }
        if not r["failed"]:
            dsp = r["dsp"]
            cand.update({
                "score": r.get("score"),
                "hr_bonus_vetoed": bool(r.get("hr_bonus_vetoed", False)),
                "hr_valid": bool(dsp["hr_valid"]),
                "hr_raw": (
                    float(dsp["hr_raw"]) if np.isfinite(dsp["hr_raw"]) else None
                ),
                "fallback_hr_bpm": (
                    float(dsp["fallback_hr_bpm"])
                    if np.isfinite(dsp["fallback_hr_bpm"])
                    else None
                ),
                "br_bpm": (
                    float(dsp["br_bpm"]) if np.isfinite(dsp["br_bpm"]) else None
                ),
                "br_confidence": dsp["br_confidence"],
                "resp_valid": bool(dsp["br_valid"]),
                "f_r_hz": (
                    None if dsp.get("f_r_hz") is None else float(dsp["f_r_hz"])
                ),
                "spectrum_stage": int(dsp["spectrum_stage"]),
                "rej_reason": dsp["rej_reason"],
                "n_eca_skipped": int(dsp["n_eca_skipped"]),
                "accepted_candidate_rank": int(
                    dsp["hr_result"].get("accepted_candidate_rank", -1)
                ),
            })
        evidence["candidates"].append(cand)

    return selected_bin, winning_dsp, evidence
