"""Capture acceptance gate — the one callable both capture paths and the offline
verifier import.

A capture can be wrong in ways nothing downstream notices: a mirrored I/Q
convention relocates the subject to ~10 m, a zero-filled UDP gap forges phase, a
clipped ADC distorts it nonlinearly. None of those raise. All of them were, until
2026-07-30, checked only by hand months after the fact.

This module is deliberately layout-agnostic: it takes raw bytes plus explicit
geometry, never a directory shape. `scripts/live_demo.py`, `steps/step_1/capture.py`
and `scripts/verify_capture_integrity.py` each adapt their own on-disk layout onto
it, so there is exactly one implementation of the checks and the capture-time
verdict cannot drift from the offline one (the same reasoning as
`src/warmup_select.py`, M4R-10).

Gating checks (a capture that fails these should be re-taken while the subject is
still seated):

  frame_alignment  file size is an exact multiple of bytes-per-frame.
  packet_loss      no dropped or zero-filled UDP packets.
  saturation       no ADC sample at or near int16 full scale.
  iq_convention    the configured I/Q convention concentrates energy in the
                   protocol distance gate rather than in that gate's mirror image.

Reported but NOT gating:

  scene_margin     how far the in-gate return sits below the strongest non-DC
                   reflector in the room. On 2026-07-30 five captures were found
                   in which the subject sat 3.3-9.6 dB below static reflectors at
                   2.09 m and 2.88 m; nothing had flagged it for two days. This is
                   surfaced loudly at capture time so a repeat is caught in the
                   room. It is NOT a pass/fail gate: no defensible threshold has
                   been established, and inventing one would silently redefine
                   which captures are admissible.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.fft import fft as sp_fft

# Frames read from the head of a capture for the spectral checks. The range
# profile of a static scene is stationary enough that 200 frames (~10 s) resolves
# the reflector layout; reading whole multi-GB captures buys nothing.
N_FRAMES_DEFAULT = 200

# int16 full scale is 32768. Treated as clipped short of true rail, because the
# ADC's own headroom is not exactly 2^15.
CLIP_THRESHOLD = 32700

# Range bins excluded from "strongest reflector" searches. TX-RX leakage and
# close-range clutter put a large near-DC component in every range profile; it is
# a known scene artefact, not a target (src/radar_io.py module docstring).
DC_SKIRT_BINS = 9

# Minimum dB by which in-gate energy must exceed the mirror band. Observed range
# on the 8 canonical captures is 12.8-38.8 dB, so 10 dB rejects a mirrored decode
# (which inverts the sign) with wide margin.
DEFAULT_MIN_MIRROR_DB = 10.0


@dataclass(frozen=True)
class CaptureGeometry:
    """Decode geometry for one capture. Mirrors src.radar_io.ChirpConfig's subset."""

    num_adc_samples: int
    num_rx: int
    num_chirps_per_frame: int
    range_resolution_m: float
    iq_swap: bool

    @property
    def bytes_per_frame(self) -> int:
        return self.num_adc_samples * self.num_rx * self.num_chirps_per_frame * 4


def gate_bins_from_distance(
    distance_range_m: tuple[float, float] | list[float],
    range_resolution_m: float,
    num_adc_samples: int,
) -> tuple[int, int]:
    """Inclusive (lo, hi) range bins spanning the protocol distance gate.

    Arithmetic is identical to `src.warmup_select.derive_candidate_bins` — the two
    are pinned equal by test_capture_integrity.py so the gate this check uses is
    the same gate warmup selection searches.
    """
    lo = int(np.ceil(float(distance_range_m[0]) / range_resolution_m))
    hi = int(np.floor(float(distance_range_m[1]) / range_resolution_m))
    return max(0, lo), min(num_adc_samples - 1, hi)


def decode_head(raw_words: np.ndarray, iq_swap: bool, shape: tuple[int, int, int, int]) -> np.ndarray:
    """Decode 2-lane LVDS 4-word packets under an explicit I/Q convention.

    Mirrors src.radar_io.read_adc_bin's de-interleaving. Kept separate because the
    convention check must decode the SAME bytes BOTH ways, which read_adc_bin
    cannot do without re-reading the file under two configs.
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


def mean_range_profile(cube: np.ndarray) -> np.ndarray:
    """Mean power per range bin, using the same Hann + FFT as extract_chest_phase."""
    n_adc = cube.shape[-1]
    win = np.hanning(n_adc).astype(np.float32)
    return np.mean(np.abs(sp_fft(cube * win, axis=3)) ** 2, axis=(0, 1, 2))


def evaluate_capture(
    bin_path: str | Path,
    geometry: CaptureGeometry,
    gate_bins: tuple[int, int],
    *,
    n_dropped: int | None = None,
    zero_filled_bytes: int | None = None,
    mirror_truncated_bytes: int | None = None,
    n_frames_read: int = N_FRAMES_DEFAULT,
    min_mirror_db: float = DEFAULT_MIN_MIRROR_DB,
) -> dict:
    """Run the acceptance gate on one capture file.

    Packet-loss arguments are optional: a capture path that does not own the UDP
    receiver (a replay, or an externally recorded .bin) passes None and that check
    records itself as skipped rather than silently passing.

    Returns a record with "passed" (gating checks only), "checks" and "diagnostics".
    Never raises for a failed check — a failed gate is data, not an exception.
    """
    bin_path = Path(bin_path)
    bpf = geometry.bytes_per_frame
    size = bin_path.stat().st_size
    n_frames_total = size // bpf

    rec: dict = {
        "bin_path": str(bin_path),
        "n_frames": int(n_frames_total),
        "bytes_per_frame": bpf,
        "iq_swap": geometry.iq_swap,
        "checks": {},
        "diagnostics": {},
    }

    # --- frame alignment ---------------------------------------------------
    remainder = size % bpf
    rec["checks"]["frame_alignment"] = {
        "passed": remainder == 0,
        "remainder_bytes": int(remainder),
        "file_size_bytes": int(size),
    }

    # --- packet loss -------------------------------------------------------
    if n_dropped is None and zero_filled_bytes is None:
        rec["checks"]["packet_loss"] = {
            "passed": True, "skipped": True,
            "reason": "no UDP statistics supplied (not a live-received capture)",
        }
    else:
        nd = int(n_dropped or 0)
        zf = int(zero_filled_bytes or 0)
        rec["checks"]["packet_loss"] = {
            "passed": nd == 0 and zf == 0,
            "n_dropped": nd,
            "zero_filled_bytes": zf,
        }

    # --- mirror trim -------------------------------------------------------
    if mirror_truncated_bytes is None:
        rec["checks"]["mirror_trim"] = {
            "passed": True, "skipped": True, "reason": "no mirror trim reported",
        }
    else:
        trimmed = int(mirror_truncated_bytes)
        rec["checks"]["mirror_trim"] = {
            "passed": 0 <= trimmed < bpf,
            "mirror_truncated_bytes": trimmed,
            "bytes_per_frame": bpf,
        }

    if n_frames_total == 0:
        rec["checks"]["saturation"] = {"passed": False, "reason": "file holds no complete frame"}
        rec["checks"]["iq_convention"] = {"passed": False, "reason": "file holds no complete frame"}
        rec["passed"] = False
        return rec

    # --- read the head once, shared by saturation and iq_convention --------
    n_read = min(n_frames_read, n_frames_total)
    raw = np.array(np.memmap(bin_path, dtype="<i2", mode="r")[: (bpf * n_read) // 2])
    shape = (n_read, geometry.num_chirps_per_frame, geometry.num_rx, geometry.num_adc_samples)

    # --- saturation --------------------------------------------------------
    peak = int(np.abs(raw).max())
    n_clipped = int((np.abs(raw) >= CLIP_THRESHOLD).sum())
    rec["checks"]["saturation"] = {
        "passed": n_clipped == 0,
        "peak_abs_sample": peak,
        "n_samples_at_or_above_threshold": n_clipped,
        "clip_threshold": CLIP_THRESHOLD,
    }
    rec["diagnostics"]["adc_full_scale_pct"] = round(100.0 * peak / 32768.0, 2)

    # --- I/Q convention ----------------------------------------------------
    lo, hi = gate_bins
    n_adc = geometry.num_adc_samples
    mirror_lo, mirror_hi = n_adc - hi, n_adc - lo
    res = geometry.range_resolution_m

    energies = {}
    for convention in (True, False):
        prof = mean_range_profile(decode_head(raw, convention, shape))
        energies[convention] = {
            "gate": float(prof[lo:hi + 1].sum()),
            "mirror": float(prof[mirror_lo:mirror_hi + 1].sum()),
            "profile": prof,
        }

    cfg_e = energies[geometry.iq_swap]
    alt_e = energies[not geometry.iq_swap]
    ratio_db = float(10.0 * np.log10(cfg_e["gate"] / cfg_e["mirror"]))
    rec["checks"]["iq_convention"] = {
        "passed": ratio_db >= min_mirror_db,
        "configured_iq_swap": geometry.iq_swap,
        "gate_bins": [lo, hi],
        "gate_range_m": [round(lo * res, 3), round(hi * res, 3)],
        "mirror_bins": [mirror_lo, mirror_hi],
        "mirror_range_m": [round(mirror_lo * res, 3), round(mirror_hi * res, 3)],
        "gate_over_mirror_db": round(ratio_db, 2),
        "threshold_db": min_mirror_db,
        "alternative_convention_db": round(
            float(10.0 * np.log10(alt_e["gate"] / alt_e["mirror"])), 2
        ),
    }

    # --- scene margin (diagnostic, NOT gating) -----------------------------
    prof = cfg_e["profile"]
    searchable = prof.copy()
    searchable[:DC_SKIRT_BINS] = 0.0
    strongest_bin = int(searchable.argmax())
    gate_peak_bin = lo + int(prof[lo:hi + 1].argmax())
    rec["diagnostics"].update(
        strongest_reflector_bin=strongest_bin,
        strongest_reflector_m=round(strongest_bin * res, 3),
        strongest_reflector_in_gate=bool(lo <= strongest_bin <= hi),
        gate_peak_bin=gate_peak_bin,
        gate_peak_m=round(gate_peak_bin * res, 3),
        gate_peak_vs_strongest_db=round(
            float(10.0 * np.log10(prof[gate_peak_bin] / prof[strongest_bin])), 2
        ),
    )

    rec["passed"] = all(c["passed"] for c in rec["checks"].values())
    return rec


def format_report(record: dict, *, title: str = "CAPTURE ACCEPTANCE GATE") -> str:
    """Human-readable capture-time verdict, including the non-gating scene margin."""
    lines = ["", "=" * 64, f"  {title}", "=" * 64]

    for name, chk in record["checks"].items():
        if chk.get("skipped"):
            status = "SKIP"
            detail = chk.get("reason", "")
        else:
            status = "PASS" if chk["passed"] else "FAIL"
            if name == "frame_alignment":
                detail = f"remainder {chk.get('remainder_bytes')} B"
            elif name == "packet_loss":
                detail = (
                    f"{chk.get('n_dropped')} dropped, "
                    f"{chk.get('zero_filled_bytes')} zero-filled B"
                )
            elif name == "mirror_trim":
                detail = f"{chk.get('mirror_truncated_bytes')} B trimmed"
            elif name == "saturation":
                detail = (
                    f"peak {chk.get('peak_abs_sample')} "
                    f"({record['diagnostics'].get('adc_full_scale_pct')}% FS), "
                    f"{chk.get('n_samples_at_or_above_threshold')} clipped"
                )
            elif name == "iq_convention":
                detail = (
                    f"{chk.get('gate_over_mirror_db'):+.1f} dB gate-vs-mirror "
                    f"(need >= {chk.get('threshold_db')})"
                )
            else:
                detail = ""
        lines.append(f"  [{status}] {name:16s} {detail}")

    d = record["diagnostics"]
    if "gate_peak_vs_strongest_db" in d:
        lines.append("")
        lines.append("  Scene margin (reported, NOT a pass/fail gate):")
        lines.append(
            f"    in-gate peak {d['gate_peak_m']:.2f} m is "
            f"{d['gate_peak_vs_strongest_db']:+.1f} dB vs strongest reflector at "
            f"{d['strongest_reflector_m']:.2f} m"
        )
        if not d["strongest_reflector_in_gate"]:
            lines.append("")
            lines.append("    *** The strongest reflector is OUTSIDE the subject gate. ***")
            lines.append("    Something in the room returns more than the subject does.")
            lines.append("    Check for equipment, furniture or a wall behind the chair and")
            lines.append("    record the scene in notes/protocol.md before continuing.")

    lines.append("")
    lines.append("  VERDICT: " + ("ACCEPTED" if record["passed"] else "REJECTED — re-take this capture"))
    lines.append("=" * 64)
    return "\n".join(lines)
