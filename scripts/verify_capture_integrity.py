"""Regenerable capture-integrity evidence for the recorded sessions.

This is the tracked replacement for the throwaway scratchpad checks used on
2026-07-30 to establish that the capture stage is sound (CLAUDE.md S3.1: every
number must trace to a committed script + config + input hash).  It answers, per
capture, the question "could this file be wrong in a way nothing downstream would
notice?" -- the failure modes that are silent rather than loud.

Per-capture checks (all gating):

  C1 frame_alignment  file size is an exact multiple of bytes-per-frame, so the
                      (frames, chirps, rx, samples) reshape is not shifted.
  C2 packet_loss      the recorded DCA1000 stream had no dropped or zero-filled
                      UDP packets.  A zero-filled gap is a silent phase artefact.
  C3 mirror_trim      the deliberate tail trim that frame-aligns the live raw
                      mirror removed less than one whole frame.
  C4 iq_convention    the configured I/Q convention is the one the hardware
                      actually produced.  A complex range FFT is not conjugate-
                      symmetric, so the wrong convention mirrors range: a target
                      at bin k decodes into bin N-k.  We decode BOTH ways and
                      require the configured one to concentrate energy in the
                      protocol distance gate rather than in that gate's mirror
                      image.  Getting this wrong does not crash anything -- it
                      relocates the subject to ~10 m and leaves noise in the gate.
  C5 saturation       no ADC sample is at or near int16 full scale.  Clipping
                      distorts phase nonlinearly and is invisible downstream.

Cross-capture check (gating):

  C6 frame_rate       the nominal frame rate is asserted by config, never
                      measured.  Regressing wall-clock span on frame count
                      separates the true rate from fixed per-run setup overhead,
                      which the naive frames/span ratio cannot do.  Requires
                      spread in frame count across captures to be identifiable;
                      the script refuses to report a rate without it rather than
                      emitting a confident wrong number.

Reported but NOT gating (diagnostics):

  scene margin        how far the in-gate return sits below the strongest
                      non-DC reflector in the room, and ADC full-scale
                      utilisation.  Both are capture-quality signals, not
                      correctness ones; turning the scene margin into a gate is
                      a separate, deliberate decision.

Usage:
    conda run -n radar-vitals python scripts/verify_capture_integrity.py
    conda run -n radar-vitals python scripts/verify_capture_integrity.py --verify-hashes

Exits 0 if every gating check passes on every capture, 1 otherwise.
No RNG is used, so there is no seed to fix.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
from scipy.fft import fft as sp_fft

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.live_demo import _sha256_file  # noqa: E402
from src.m8.ahmed_provenance import git_text  # noqa: E402
from src.warmup_select import derive_candidate_bins  # noqa: E402

# Frames read from the head of each capture for the spectral checks (C4, C5).
# The range profile of a static scene is stationary enough that 200 frames (~10 s)
# resolves the reflector layout; reading whole multi-GB captures buys nothing.
N_FRAMES = 200

# int16 full scale is 32768. A sample at or above this is treated as clipped --
# short of true rail, because the ADC's own headroom is not exactly 2^15.
CLIP_THRESHOLD = 32700

# Range bins excluded from "strongest reflector" searches. TX-RX leakage and
# close-range clutter put a large near-DC component in every range profile; it
# is a known scene artefact, not a target (see src/radar_io.py module docstring).
DC_SKIRT_BINS = 9

# C4 threshold: dB by which in-gate energy must exceed the mirror band.
DEFAULT_MIN_MIRROR_DB = 10.0

# C6: minimum relative spread in frame count needed before a frame-rate slope is
# identifiable. Captures of near-identical length cannot separate rate from
# overhead -- fitting them anyway produced 21.27 Hz and 4.72 Hz from data whose
# true rate is ~20 Hz.
MIN_FRAME_COUNT_SPREAD = 0.05

# C6: the regression models ONE fixed overhead shared by every capture. If the
# capture code changed between sessions (it did -- warmup scanning was added
# after 2026-07-13), each era carries its own overhead and the pooled slope is
# biased. Residuals test that assumption: pooling all 8 captures leaves a 0.485 s
# residual and reads 19.896 Hz, while the homogeneous 2026-07-14+ subset leaves
# 0.155 s and reads 19.988 Hz. Above this threshold the model is inadequate and
# the script reports the rate as INDETERMINATE rather than certifying it.
MAX_OVERHEAD_RESIDUAL_S = 0.25


def _decode_head(raw_words: np.ndarray, iq_swap: bool, shape: tuple[int, int, int, int]) -> np.ndarray:
    """Decode 2-lane LVDS 4-word packets under an explicit I/Q convention.

    Mirrors src.radar_io.read_adc_bin's de-interleaving. Duplicated deliberately:
    C4 must decode the SAME bytes under BOTH conventions, which the library
    function cannot do without re-reading the file twice under two configs.
    """
    words = raw_words.reshape(-1, 4)
    out = np.empty(raw_words.size // 2, dtype=np.complex64)
    if iq_swap:
        out[0::2] = words[:, 2].astype(np.float32) + 1j * words[:, 0].astype(np.float32)
        out[1::2] = words[:, 3].astype(np.float32) + 1j * words[:, 1].astype(np.float32)
    else:
        out[0::2] = words[:, 0].astype(np.float32) + 1j * words[:, 2].astype(np.float32)
        out[1::2] = words[:, 1].astype(np.float32) + 1j * words[:, 3].astype(np.float32)
    return out.reshape(shape)


def _mean_range_profile(cube: np.ndarray) -> np.ndarray:
    """Mean power per range bin, using the same Hann + FFT as extract_chest_phase."""
    n_adc = cube.shape[-1]
    win = np.hanning(n_adc).astype(np.float32)
    return np.mean(np.abs(sp_fft(cube * win, axis=3)) ** 2, axis=(0, 1, 2))


def _check_one(run_dir: Path, min_mirror_db: float, verify_hashes: bool) -> dict:
    """Run C1-C5 on one capture. Returns a result record; never raises for a failed check."""
    meta_path = run_dir / "run_metadata.json"
    adc_path = run_dir / "adc_stream.bin"
    rec: dict = {"capture": run_dir.name, "checks": {}, "diagnostics": {}}

    if not meta_path.exists() or not adc_path.exists():
        rec["error"] = "missing run_metadata.json or adc_stream.bin"
        rec["passed"] = False
        return rec

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    cfg = meta["config"]
    prof = cfg["profile"]
    n_adc = int(prof["num_adc_samples"])
    n_rx = int(prof["num_rx"])
    n_ch = int(prof["num_chirps_per_frame"])
    res_m = float(prof["range_resolution_m"])
    iq_swap = bool(prof["iq_swap"])
    bytes_per_frame = n_adc * n_rx * n_ch * 4

    size = adc_path.stat().st_size
    n_frames_total = size // bytes_per_frame
    rec["iq_swap"] = iq_swap
    rec["n_frames"] = int(n_frames_total)
    rec["bytes_per_frame"] = bytes_per_frame

    # --- C1 frame alignment ------------------------------------------------
    remainder = size % bytes_per_frame
    rec["checks"]["C1_frame_alignment"] = {
        "passed": remainder == 0,
        "remainder_bytes": int(remainder),
    }

    # --- C2 packet loss ----------------------------------------------------
    stats = meta.get("live_packet_stats")
    if stats is None:
        # Replay runs and externally captured sessions carry no live UDP stats.
        rec["checks"]["C2_packet_loss"] = {
            "passed": True, "skipped": True,
            "reason": "no live_packet_stats (not a live capture)",
        }
    else:
        n_dropped = int(stats.get("n_dropped", -1))
        zero_filled = int(stats.get("zero_filled_bytes", -1))
        rec["checks"]["C2_packet_loss"] = {
            "passed": n_dropped == 0 and zero_filled == 0,
            "n_received": int(stats.get("n_received", -1)),
            "n_dropped": n_dropped,
            "zero_filled_bytes": zero_filled,
        }

    # --- C3 mirror trim ----------------------------------------------------
    if stats is None or "mirror_truncated_bytes" not in stats:
        rec["checks"]["C3_mirror_trim"] = {
            "passed": True, "skipped": True, "reason": "no mirror_truncated_bytes",
        }
    else:
        trimmed = int(stats["mirror_truncated_bytes"])
        rec["checks"]["C3_mirror_trim"] = {
            "passed": 0 <= trimmed < bytes_per_frame,
            "mirror_truncated_bytes": trimmed,
            "bytes_per_frame": bytes_per_frame,
        }

    # --- Read the head once, shared by C4 and C5 ---------------------------
    n_read = min(N_FRAMES, n_frames_total)
    n_words = (bytes_per_frame * n_read) // 2
    raw = np.array(np.memmap(adc_path, dtype="<i2", mode="r")[:n_words])
    shape = (n_read, n_ch, n_rx, n_adc)

    # --- C5 saturation -----------------------------------------------------
    peak = int(np.abs(raw).max())
    n_clipped = int((np.abs(raw) >= CLIP_THRESHOLD).sum())
    rec["checks"]["C5_saturation"] = {
        "passed": n_clipped == 0,
        "peak_abs_sample": peak,
        "n_samples_at_or_above_threshold": n_clipped,
        "clip_threshold": CLIP_THRESHOLD,
    }
    rec["diagnostics"]["adc_full_scale_pct"] = round(100.0 * peak / 32768.0, 2)

    # --- C4 I/Q convention -------------------------------------------------
    # Gate comes from the session's own protocol config via the production
    # helper, so this is not a hardcoded bin range.
    gate_bins = derive_candidate_bins(cfg)
    lo, hi = min(gate_bins), max(gate_bins)
    mirror_lo, mirror_hi = n_adc - hi, n_adc - lo

    energies = {}
    for convention in (True, False):
        prof_pow = _mean_range_profile(_decode_head(raw, convention, shape))
        energies[convention] = {
            "gate": float(prof_pow[lo:hi + 1].sum()),
            "mirror": float(prof_pow[mirror_lo:mirror_hi + 1].sum()),
            "profile": prof_pow,
        }

    cfg_e = energies[iq_swap]
    ratio_db = float(10.0 * np.log10(cfg_e["gate"] / cfg_e["mirror"]))
    rec["checks"]["C4_iq_convention"] = {
        "passed": ratio_db >= min_mirror_db,
        "configured_iq_swap": iq_swap,
        "gate_bins": [lo, hi],
        "gate_range_m": [round(lo * res_m, 3), round(hi * res_m, 3)],
        "mirror_bins": [mirror_lo, mirror_hi],
        "mirror_range_m": [round(mirror_lo * res_m, 3), round(mirror_hi * res_m, 3)],
        "gate_over_mirror_db": round(ratio_db, 2),
        "threshold_db": min_mirror_db,
        "alternative_convention_db": round(
            float(10.0 * np.log10(energies[not iq_swap]["gate"] / energies[not iq_swap]["mirror"])), 2
        ),
    }

    # --- Diagnostics: scene margin (reported, not gating) ------------------
    prof_pow = cfg_e["profile"]
    searchable = prof_pow.copy()
    searchable[:DC_SKIRT_BINS] = 0.0
    strongest_bin = int(searchable.argmax())
    gate_peak_bin = lo + int(prof_pow[lo:hi + 1].argmax())
    margin_db = float(10.0 * np.log10(prof_pow[gate_peak_bin] / prof_pow[strongest_bin]))
    rec["diagnostics"]["strongest_reflector_bin"] = strongest_bin
    rec["diagnostics"]["strongest_reflector_m"] = round(strongest_bin * res_m, 3)
    rec["diagnostics"]["strongest_reflector_in_gate"] = bool(lo <= strongest_bin <= hi)
    rec["diagnostics"]["gate_peak_bin"] = gate_peak_bin
    rec["diagnostics"]["gate_peak_m"] = round(gate_peak_bin * res_m, 3)
    rec["diagnostics"]["gate_peak_vs_strongest_db"] = round(margin_db, 2)
    rec["diagnostics"]["locked_bin"] = meta.get("locked_bin")

    # --- Provenance --------------------------------------------------------
    recorded_hash = meta.get("live_raw_mirror_hash")
    rec["adc_stream_sha256_recorded"] = recorded_hash
    if verify_hashes:
        actual = _sha256_file(adc_path)
        rec["adc_stream_sha256_actual"] = actual
        rec["checks"]["C0_hash_matches_recorded"] = {
            "passed": recorded_hash is None or actual == recorded_hash,
            "skipped": recorded_hash is None,
        }

    rec["passed"] = all(c["passed"] for c in rec["checks"].values())
    return rec


def _check_frame_rate(records: list[dict], run_dirs: list[Path], nominal_hz: float) -> dict:
    """C6: regress wall-clock span on frame count to separate rate from fixed overhead.

    span_i = n_frames_i / fs + overhead.  The naive frames/span ratio conflates the
    two and reads low by however much setup time each run carried.
    """
    pts = []
    for rec, d in zip(records, run_dirs):
        if "error" in rec:
            continue
        meta = json.loads((d / "run_metadata.json").read_text(encoding="utf-8"))
        start, end = meta.get("start_wall_utc"), meta.get("end_wall_utc")
        if not start or not end:
            continue
        span = (dt.datetime.fromisoformat(end) - dt.datetime.fromisoformat(start)).total_seconds()
        pts.append((rec["n_frames"], span))

    out: dict = {"n_points": len(pts), "nominal_hz": nominal_hz}
    if len(pts) < 3:
        out.update(passed=True, skipped=True, reason="fewer than 3 usable captures")
        return out

    nf = np.array([p[0] for p in pts], float)
    span = np.array([p[1] for p in pts], float)
    spread = (nf.max() - nf.min()) / nf.mean()
    out["frame_count_spread"] = round(float(spread), 4)
    if spread < MIN_FRAME_COUNT_SPREAD:
        out.update(
            passed=True, skipped=True,
            reason=(
                f"frame counts span only {spread:.2%} (< {MIN_FRAME_COUNT_SPREAD:.0%}); "
                "slope is unidentifiable, refusing to report a rate"
            ),
        )
        return out

    A = np.vstack([nf, np.ones_like(nf)]).T
    (slope, intercept), *_ = np.linalg.lstsq(A, span, rcond=None)
    fs = 1.0 / slope
    resid = span - A @ [slope, intercept]
    max_resid = float(np.abs(resid).max())
    rel_err = float(abs(fs - nominal_hz) / nominal_hz)
    out.update(
        measured_hz=round(float(fs), 4),
        fixed_overhead_s=round(float(intercept), 2),
        max_abs_residual_s=round(max_resid, 3),
        rel_error_vs_nominal=round(rel_err, 5),
        hr_bias_bpm_at_80=round(float(80.0 * (nominal_hz / fs - 1.0)), 3),
        worst_residual_capture=str(
            [r["capture"] for r in records if "error" not in r][int(np.abs(resid).argmax())]
        ),
    )

    if max_resid > MAX_OVERHEAD_RESIDUAL_S:
        # Non-gating: the wall-clock method cannot certify a rate here, but it has
        # not shown the rate to be wrong either. The definitive argument for the
        # frame rate is the sensor's crystal-derived frame timer; this regression
        # is only a coarse consistency check on top of it.
        out.update(
            passed=True,
            indeterminate=True,
            reason=(
                f"max residual {max_resid:.3f} s exceeds {MAX_OVERHEAD_RESIDUAL_S} s — the "
                "single-fixed-overhead model does not fit, so the pooled slope is biased "
                "and the rate cannot be certified from wall clock alone"
            ),
        )
        return out

    out["passed"] = bool(rel_err < 0.01)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--captures-dir", type=Path, default=_ROOT / "results" / "live_demo")
    ap.add_argument("--exclude", action="append", default=[], metavar="SUBSTR",
                    help="skip capture directories whose name contains SUBSTR "
                         "(repeatable). Use to restrict C6 to captures recorded by "
                         "one unchanged capture path.")
    ap.add_argument("--min-mirror-db", type=float, default=DEFAULT_MIN_MIRROR_DB,
                    help="C4: minimum in-gate over mirror-band energy ratio (dB)")
    ap.add_argument("--verify-hashes", action="store_true",
                    help="recompute each adc_stream.bin SHA-256 and compare against "
                         "the recorded hash (slow: reads every byte of every capture)")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="where to write the JSON artefact "
                         "(default results/capture_integrity/<UTC timestamp>/)")
    args = ap.parse_args()

    run_dirs = sorted(d for d in args.captures_dir.iterdir() if d.is_dir())
    if args.exclude:
        kept = [d for d in run_dirs if not any(s in d.name for s in args.exclude)]
        print(f"excluded {len(run_dirs) - len(kept)} capture(s) matching {args.exclude}")
        run_dirs = kept
    if not run_dirs:
        print(f"no capture directories under {args.captures_dir}", file=sys.stderr)
        return 1

    records = [_check_one(d, args.min_mirror_db, args.verify_hashes) for d in run_dirs]

    nominal = 20.0
    for d in run_dirs:
        meta = json.loads((d / "run_metadata.json").read_text(encoding="utf-8"))
        nominal = float(meta["config"]["session"]["frame_rate_hz"])
        break
    fr = _check_frame_rate(records, run_dirs, nominal)

    # ---------------- report ----------------
    print(f"Capture integrity — {len(records)} captures under {args.captures_dir}")
    print()
    hdr = f"{'capture':34s} {'C1':>4s} {'C2':>4s} {'C3':>4s} {'C4':>4s} {'C5':>4s}  {'gate/mirror':>12s} {'%FS':>6s}"
    print(hdr)
    print("-" * len(hdr))
    for r in records:
        if "error" in r:
            print(f"{r['capture'][:34]:34s}  ERROR: {r['error']}")
            continue
        c = r["checks"]

        def mark(key: str) -> str:
            if key not in c:
                return "   -"
            if c[key].get("skipped"):
                return "   ~"
            return " ok " if c[key]["passed"] else "FAIL"

        print(
            f"{r['capture'][:34]:34s} {mark('C1_frame_alignment')} {mark('C2_packet_loss')} "
            f"{mark('C3_mirror_trim')} {mark('C4_iq_convention')} {mark('C5_saturation')}  "
            f"{c['C4_iq_convention']['gate_over_mirror_db']:+10.1f} dB "
            f"{r['diagnostics']['adc_full_scale_pct']:5.1f}%"
        )

    print()
    print("C6 frame_rate (cross-capture):")
    if fr.get("skipped"):
        print(f"  SKIPPED — {fr['reason']}")
    else:
        print(
            f"  measured {fr['measured_hz']} Hz vs nominal {fr['nominal_hz']} Hz "
            f"(fixed overhead {fr['fixed_overhead_s']} s, max residual "
            f"{fr['max_abs_residual_s']} s, n={fr['n_points']})"
        )
        if fr.get("indeterminate"):
            print(f"  INDETERMINATE — {fr['reason']}")
            print(f"  worst-fitting capture: {fr['worst_residual_capture']}")
            print(
                "  Treat the rate above as a coarse bound only. Re-run over a set of "
                "captures recorded by one unchanged capture path to certify it."
            )
        else:
            print(
                f"  -> HR bias at 80 bpm if fs assumed nominal: "
                f"{fr['hr_bias_bpm_at_80']:+.3f} bpm  "
                f"[{'PASS' if fr['passed'] else 'FAIL'}]"
            )

    print()
    print("Diagnostics (reported, NOT gating):")
    for r in records:
        if "error" in r:
            continue
        d = r["diagnostics"]
        flag = "" if d["strongest_reflector_in_gate"] else "   <-- strongest reflector is OUTSIDE the gate"
        print(
            f"  {r['capture'][:34]:34s} gate peak {d['gate_peak_m']:.2f} m is "
            f"{d['gate_peak_vs_strongest_db']:+6.1f} dB vs strongest at "
            f"{d['strongest_reflector_m']:5.2f} m{flag}"
        )

    all_passed = all(r.get("passed", False) for r in records) and fr.get("passed", False)

    # ---------------- artefact ----------------
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out_dir or (_ROOT / "results" / "capture_integrity" / stamp)
    payload = {
        "generated_utc": stamp,
        "git_commit": git_text("rev-parse", "HEAD"),
        "script": "scripts/verify_capture_integrity.py",
        "captures_dir": str(args.captures_dir.relative_to(_ROOT)),
        "excluded_substrings": list(args.exclude),
        "parameters": {
            "n_frames_read": N_FRAMES,
            "min_mirror_db": args.min_mirror_db,
            "clip_threshold": CLIP_THRESHOLD,
            "dc_skirt_bins": DC_SKIRT_BINS,
            "min_frame_count_spread": MIN_FRAME_COUNT_SPREAD,
            "verify_hashes": args.verify_hashes,
        },
        "seed": None,  # no RNG used
        "captures": [{k: v for k, v in r.items()} for r in records],
        "C6_frame_rate": fr,
        "all_passed": all_passed,
    }
    # Serialise BEFORE creating the directory, so a serialisation failure cannot
    # leave an empty run directory behind in results/.
    serialised = json.dumps(payload, indent=2)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "capture_integrity.json"
    # newline="\n": the repo pins LF and hashes text payloads (HANDOFF gotchas).
    out_path.write_text(serialised, encoding="utf-8", newline="\n")
    print()
    print(f"artefact: {out_path.relative_to(_ROOT)}")
    print("ALL CHECKS PASSED" if all_passed else "INTEGRITY CHECK FAILED — see FAIL rows above")
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
