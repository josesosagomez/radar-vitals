#!/usr/bin/env python
"""Pre-implementation prediction of the M8 Step 1b synthetic gate outcome.

Evidence for `plans/m8_step1b_ahmed_transfer_addendum_a.md`. This script exists so
that every number in that addendum is regenerable from a committed source, per
CLAUDE.md section 3 rule 1.

It replicates the plan's section 2.2 generator and section 2.3 accumulator directly,
independently of `src/m8/` and of any Step 1b implementation, so that it remains a
genuinely independent oracle once the implementation exists.

Reads no project data and writes no files.

Plain `conda` is not on PATH on this machine; use the full conda.bat path:

    & 'C:\\ProgramData\\anaconda3\\condabin\\conda.bat' run -n radar-vitals `
      python scripts/m8_step1b_gate_prediction.py
"""
from __future__ import annotations

import numpy as np

C_M_S = 3.0e8
FC_HZ = 6.7e9
LAMBDA_M = C_M_S / FC_HZ
FB_HZ = 20.0 / 60.0
FH_HZ = 80.0 / 60.0
D_BREATH_M = 0.020
D_HEART_M = 0.010
BETA_B = 4.0 * np.pi * D_BREATH_M / LAMBDA_M
BETA_H = 4.0 * np.pi * D_HEART_M / LAMBDA_M
PRF_HZ = 37.41985916275843
SEED = 42
SNR_DB = 10.0

# Candidate domains are NOT shared between paths (plan section 2.3 table). The synthetic
# heart domain starts at f_b and therefore admits the breathing fundamental as a heart
# candidate; the real heart domain starts at 0.80 Hz and excludes it. Applying the wrong
# domain to a grid silently changes the predicted outcome, so each grid carries its own.
SYNTHETIC_VITALS = (("breath", 0.0, 25.0 / 60.0, 20.0), ("heart", FB_HZ, 100.0 / 60.0, 80.0))
REAL_VITALS = (("breath", 0.10, 0.50, 20.0), ("heart", 0.80, 2.00, 80.0))

# (label, fs_hz, n_samples, domains) — plan primary, plan 20 Hz audit, real 30 s grid.
GRIDS = (
    ("primary_prf", PRF_HZ, 561, SYNTHETIC_VITALS),
    # Same signal and grid as above, differing only in candidate domain. Isolates the
    # domain choice as the sole cause of the primary-gate heart failure.
    ("primary_prf_real_domains", PRF_HZ, 561, REAL_VITALS),
    ("audit_20hz", 20.0, 300, SYNTHETIC_VITALS),
    ("real_30s", 20.0, 600, REAL_VITALS),
)
HARMONICS = (3, 5)


def build(fs: float, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Return (clean_phase, noisy_extracted_phase), both zero-referenced."""
    t = np.arange(n, dtype=np.float64) / fs
    phi_c = BETA_B * np.sin(2 * np.pi * FB_HZ * t) + BETA_H * np.sin(2 * np.pi * FH_HZ * t)
    z_clean = np.exp(1j * phi_c)
    rng = np.random.Generator(np.random.PCG64(SEED))
    u = rng.standard_normal(n)
    v = rng.standard_normal(n)
    sigma = np.sqrt(float(np.mean(np.abs(z_clean) ** 2)) / (2.0 * 10 ** (SNR_DB / 10.0)))
    z = z_clean + sigma * (u + 1j * v)
    delta = np.zeros(n, dtype=np.float64)
    delta[1:] = np.angle(z[1:] * np.conj(z[:-1]))
    return phi_c - phi_c[0], np.cumsum(delta)


def accumulate(phase: np.ndarray, fs: float, n_fft: int, h: int, lo: float, hi: float):
    """Native-length fixed-H accumulation over strict-support rFFT bin candidates."""
    spectrum = np.abs(np.fft.rfft(phase, n=n_fft))
    freqs = np.fft.rfftfreq(n_fft, d=1.0 / fs)
    in_band = (freqs > lo) & (freqs <= hi) if lo == 0.0 else (freqs >= lo) & (freqs <= hi)
    bins = np.flatnonzero(in_band)
    bins = bins[(bins > 0) & (bins * h < n_fft // 2) & (bins * h < spectrum.size)]
    scores = spectrum[bins[:, None] * np.arange(1, h + 1)[None, :]].sum(axis=1) / float(h)
    return freqs, bins, scores


def main() -> None:
    print("M8 Step 1b synthetic gate — pre-implementation prediction")
    print(f"beta_breath={BETA_B:.10f} rad  beta_heart={BETA_H:.10f} rad  "
          f"ratio={BETA_B / BETA_H:.6f}\n")

    for label, fs, n, vitals in GRIDS:
        res_bpm = 60.0 * fs / n
        domain = "synthetic" if vitals is SYNTHETIC_VITALS else "real"
        print(f"== {label}: fs={fs!r} n={n} n_fft={n} bin={res_bpm:.4f} bpm "
              f"tol=+/-{60.0 / (n / fs):.4f} bpm domains={domain} ==")
        clean, noisy = build(fs, n)
        for vital, lo, hi, truth in vitals:
            truth_bin = int(round((truth / 60.0) / (fs / n)))
            for h in HARMONICS:
                _, bins, sc_clean = accumulate(clean, fs, n, h, lo, hi)
                freqs, bins_n, sc_noisy = accumulate(noisy, fs, n, h, lo, hi)
                top = float(sc_clean.max())
                tied = bins[np.flatnonzero(sc_clean == top)]
                sel_clean = int(tied[0])
                sel_noisy = int(bins_n[int(np.argmax(sc_noisy))])
                ok = abs(sel_noisy * res_bpm - truth) <= 60.0 / (n / fs)
                print(f"  {vital:6s} H={h}  truth bin {truth_bin:3d} "
                      f"({truth:5.1f} bpm) | clean tie-set "
                      f"{[int(b) for b in tied[:6]]} -> select bin {sel_clean:3d} "
                      f"({sel_clean * res_bpm:7.3f} bpm) | noisy select "
                      f"{sel_noisy * res_bpm:7.3f} bpm  {'PASS' if ok else 'FAIL'}")
        print()


if __name__ == "__main__":
    main()
