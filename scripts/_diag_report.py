"""One-shot diagnostic report for exp004 new captures (no pipeline re-runs)."""

import math
import numpy as np
import pandas as pd
import yaml
from pathlib import Path

ROOT = Path(".")
SEP  = "=" * 72

CANONICAL = ROOT / "results/exp004_window_length/20260615_113951"
NEW_RUNS  = {
    "cap3_retake": ROOT / "results/exp004_window_length/20260615_181343/cap3_retake",
    "cap4":        ROOT / "results/exp004_window_length/20260615_181821/cap4",
    "cap5":        ROOT / "results/exp004_window_length/20260615_181912/cap5",
}
SEATED = {
    "cap1": CANONICAL / "cap1",
    "cap2": CANONICAL / "cap2",
}
ALL_CAPS = {**SEATED, **NEW_RUNS}
CONDITIONS = ["baseline_20s", "condition_20s", "condition_25s", "condition_30s"]
CARDIAC_LO = 50.0
CARDIAC_HI = 100.0


def load_csv(cap_dir, condition):
    p = cap_dir / condition / "comparison.csv"
    return pd.read_csv(p) if p.exists() else None


def metrics(df):
    if df is None:
        return dict(n_total=0, n_finite=0, n_nan=0,
                    mae=float("nan"), rmse=float("nan"), bias=float("nan"),
                    ahet_pass=0, ahet_rate=float("nan"))
    n_total = len(df)
    fin = df.dropna(subset=["error_bpm"])
    n_finite = len(fin)
    n_nan = n_total - n_finite
    if n_finite == 0:
        return dict(n_total=n_total, n_finite=0, n_nan=n_nan,
                    mae=float("nan"), rmse=float("nan"), bias=float("nan"),
                    ahet_pass=0, ahet_rate=float("nan"))
    mae  = fin["error_bpm"].abs().mean()
    rmse = math.sqrt((fin["error_bpm"] ** 2).mean())
    bias = fin["error_bpm"].mean()
    ahet_pass = int(fin["ahet_verified"].fillna(False).sum())
    ahet_rate = ahet_pass / n_finite * 100
    return dict(n_total=n_total, n_finite=n_finite, n_nan=n_nan,
                mae=mae, rmse=rmse, bias=bias,
                ahet_pass=ahet_pass, ahet_rate=ahet_rate)


# ════════════════════════════════════════════════════════════════════════════
print(SEP)
print("EXP004 DIAGNOSTIC REPORT  —  2026-06-15")
print("Generated from existing results only (no pipeline re-runs)")
print(SEP)

# ─────────────────── TASK 1 ─────────────────────────────────────────────────
print()
print(SEP)
print("TASK 1  —  RESULT FILE LISTING")
print(SEP)

print("\nSeated captures  (canonical run: 20260615_113951)")
for cap_id, cap_dir in SEATED.items():
    print(f"  {cap_id}:")
    for cond in CONDITIONS:
        p   = cap_dir / cond / "comparison.csv"
        tag = "PRESENT" if p.exists() else "MISSING"
        print(f"    {cond}/comparison.csv  [{tag}]")

print("\nNew supine captures")
for cap_id, cap_dir in NEW_RUNS.items():
    ts = cap_dir.parent.name
    print(f"  {cap_id}  (run timestamp: {ts})")
    for cond in CONDITIONS:
        p   = cap_dir / cond / "comparison.csv"
        tag = "PRESENT" if p.exists() else "MISSING"
        print(f"    {cond}/comparison.csv  [{tag}]")

# ─────────────────── TASK 2 ─────────────────────────────────────────────────
print()
print(SEP)
print("TASK 2  —  ALL CAPTURES x ALL CONDITIONS  METRICS")
print(SEP)

for cap_id, cap_dir in ALL_CAPS.items():
    print(f"\n  {cap_id.upper()}")
    hdr = (f"  {'Condition':<16} {'N_tot':>5} {'N_fin':>5} {'N_NaN':>5}  "
           f"{'MAE':>7} {'RMSE':>7} {'Bias':>8}  {'AHET_pass':>9} {'AHET%':>6}")
    print(hdr)
    print("  " + "-" * 72)
    for cond in CONDITIONS:
        m = metrics(load_csv(cap_dir, cond))
        if math.isnan(m["mae"]):
            vals = "    n/a     n/a      n/a         n/a    n/a"
        else:
            vals = (f"{m['mae']:>7.3f} {m['rmse']:>7.3f} {m['bias']:>8.3f}  "
                    f"{m['ahet_pass']:>9} {m['ahet_rate']:>5.1f}%")
        print(f"  {cond:<16} {m['n_total']:>5} {m['n_finite']:>5} "
              f"{m['n_nan']:>5}  {vals}")

# ─────────────────── TASK 3 ─────────────────────────────────────────────────
print()
print(SEP)
print("TASK 3  —  CROSS-CAPTURE SUMMARY TABLE (condition_20s / 25s / 30s)")
print(SEP)
print("  Monotonic = MAE decreases 20s -> 25s -> 30s (full condition set, not intersection)")

CAP_META = {
    "cap1":        dict(role="dev",      posture="seated", br="~17",   fr4="~68"),
    "cap2":        dict(role="dev",      posture="seated", br="~17",   fr4="~68"),
    "cap3_retake": dict(role="dev",      posture="supine", br="14",    fr4="56"),
    "cap4":        dict(role="dev",      posture="supine", br="17-19", fr4="68-76"),
    "cap5":        dict(role="held-out", posture="supine", br="13",    fr4="52"),
}

hdr = (f"\n  {'Capture':<14} {'Role':<10} {'Posture':<8} "
       f"{'BR':>6} {'4xfr':>6}  "
       f"{'20s MAE':>8} {'25s MAE':>8} {'30s MAE':>8}  "
       f"{'20s bias':>9} {'30s bias':>9}  {'Mono?':6}")
print(hdr)
print("  " + "-" * 110)

for cap_id, meta in CAP_META.items():
    cap_dir = ALL_CAPS[cap_id]
    m20 = metrics(load_csv(cap_dir, "condition_20s"))
    m25 = metrics(load_csv(cap_dir, "condition_25s"))
    m30 = metrics(load_csv(cap_dir, "condition_30s"))
    mae20, mae25, mae30 = m20["mae"], m25["mae"], m30["mae"]
    b20, b30 = m20["bias"], m30["bias"]
    if not any(math.isnan(x) for x in [mae20, mae25, mae30]):
        if mae20 > mae25 > mae30:
            mono = "YES"
        elif mae20 > mae30:
            mono = "PART"
        else:
            mono = "NO"
    else:
        mono = "n/a"
    print(f"  {cap_id:<14} {meta['role']:<10} {meta['posture']:<8} "
          f"{meta['br']:>6} {meta['fr4']:>6}  "
          f"{mae20:>8.3f} {mae25:>8.3f} {mae30:>8.3f}  "
          f"{b20:>9.3f} {b30:>9.3f}  {mono}")

# ─────────────────── TASK 4 ─────────────────────────────────────────────────
print()
print(SEP)
print("TASK 4  —  PER-WINDOW ERROR TABLES (condition_20s, sorted by |error| desc)")
print(SEP)

for cap_id, cap_dir in NEW_RUNS.items():
    df = load_csv(cap_dir, "condition_20s")
    if df is None:
        print(f"\n  {cap_id}: file missing")
        continue
    fin = df.dropna(subset=["error_bpm"]).copy()
    fin["abs_error"] = fin["error_bpm"].abs()
    n_nan = int(df["hr_bpm"].isna().sum())
    print(f"\n  {cap_id.upper()}  (N_finite={len(fin)}, N_NaN={n_nan})")
    print(f"  {'Wi':>4}  {'MasimoPR':>9}  {'RadarHR':>8}  {'Error':>8}  "
          f"{'AbsErr':>7}  {'AHET':<5}  flag")
    print("  " + "-" * 65)
    for _, row in fin.sort_values("abs_error", ascending=False).iterrows():
        flag = "***" if row["abs_error"] > 5 else "   "
        ahet = "PASS" if row["ahet_verified"] else "fail"
        print(f"  {int(row['window_index']):>4}  {row['masimo_pr_bpm']:>9.2f}  "
              f"{row['hr_bpm']:>8.2f}  {row['error_bpm']:>8.2f}  "
              f"{row['abs_error']:>7.2f}  {ahet:<5}  {flag}")
    nan_idx = sorted(df[df["hr_bpm"].isna()]["window_index"].astype(int).tolist())
    print(f"\n  NaN windows ({n_nan}): {nan_idx}")

# ─────────────────── TASK 5 ─────────────────────────────────────────────────
print()
print(SEP)
print("TASK 5  —  RESPIRATORY HARMONIC ANALYSIS (condition_20s intermediates)")
print(SEP)

K_LIST = [2, 3, 4, 5, 6]
COIN_TOL = 5.0

for cap_id, cap_dir in NEW_RUNS.items():
    npz_path = cap_dir / "condition_20s" / "intermediates.npz"
    csv_path = cap_dir / "condition_20s" / "comparison.csv"
    if not npz_path.exists():
        print(f"\n  {cap_id}: intermediates.npz missing")
        continue
    d  = np.load(str(npz_path), allow_pickle=True)
    df = pd.read_csv(csv_path)

    fr_hz  = d["resp_peak_refined_hz"]
    fr_bpm = fr_hz * 60.0
    pr_bpm = df["masimo_pr_bpm"].values

    print(f"\n  {'-' * 60}")
    print(f"  {cap_id.upper()}  --  f_r across 79 windows")
    valid = fr_bpm[~np.isnan(fr_bpm)]
    print(f"  f_r  mean={valid.mean():.2f}  std={valid.std():.2f}  "
          f"min={valid.min():.2f}  max={valid.max():.2f}  (bpm)")

    print(f"\n  Harmonic intrusions into cardiac band ({CARDIAC_LO:.0f}–{CARDIAC_HI:.0f} bpm):")
    print(f"  {'k':>3}  {'k*fr mean':>10}  {'k*fr range':>16}  "
          f"{'N in band':>10}  {'N |diff|<5bpm':>14}")
    for k in K_LIST:
        hb = fr_bpm * k
        n_in   = int(np.sum((hb >= CARDIAC_LO) & (hb <= CARDIAC_HI) & ~np.isnan(hb)))
        n_coin = 0
        for i in range(len(hb)):
            if np.isnan(hb[i]) or np.isnan(pr_bpm[i]):
                continue
            if abs(hb[i] - pr_bpm[i]) < COIN_TOL:
                n_coin += 1
        hm = np.nanmean(hb)
        hlo = np.nanmin(hb)
        hhi = np.nanmax(hb)
        print(f"  {k:>3}  {hm:>10.2f}  {hlo:>7.1f}–{hhi:<7.1f}  "
              f"{n_in:>10}  {n_coin:>14}")

    # Worst-3 windows
    fin  = df.dropna(subset=["error_bpm"]).copy()
    fin["abs_error"] = fin["error_bpm"].abs()
    worst3 = fin.nlargest(3, "abs_error")
    print(f"\n  Worst 3 windows — full harmonic table (* = in cardiac band):")
    harm_hdr = "  ".join(f"k{k}*fr" for k in K_LIST)
    print(f"  {'Wi':>3}  {'MasPR':>6}  {'Error':>7}  {'f_r':>6}  {harm_hdr}")
    print("  " + "-" * 70)
    for _, row in worst3.iterrows():
        wi  = int(row["window_index"])
        fr  = fr_bpm[wi]
        harms = "  ".join(
            f"{fr*k:>6.1f}" + ("*" if CARDIAC_LO <= fr * k <= CARDIAC_HI else " ")
            for k in K_LIST
        )
        print(f"  {wi:>3}  {row['masimo_pr_bpm']:>6.1f}  {row['error_bpm']:>7.2f}  "
              f"{fr:>6.2f}  {harms}")

    # Primary intruders
    primary = [k for k in K_LIST
               if np.sum((fr_bpm * k >= CARDIAC_LO) &
                         (fr_bpm * k <= CARDIAC_HI) &
                         ~np.isnan(fr_bpm)) > len(valid) * 0.5]
    print(f"\n  PRIMARY INTRUDERS (in cardiac band >50% of windows): k = {primary}")
    if not primary:
        print("  (no harmonic lands in cardiac band for majority of windows)")

# ─────────────────── TASK 6 ─────────────────────────────────────────────────
print()
print(SEP)
print("TASK 6  —  ECA CONFIG CHECK")
print(SEP)

with open("experiments/exp004_window_length/config.yaml") as f:
    cfg = yaml.safe_load(f)
k_max    = cfg["eca"]["k_max"]
ahet_dev = cfg["eca"]["ahet_deviation_hz"]

print(f"\n  ECA parameters (from experiments/exp004_window_length/config.yaml):")
print(f"    k_max             = {k_max}")
print(f"    ahet_deviation_hz = {ahet_dev}")
print(f"    Hard-floor k (always removed on first pass): k = 1..4")
print(f"    Conditional k (removed when no cardiac candidate): k = 5..{k_max}")
print(f"    Note: on the FIRST pass there is never a cardiac candidate,")
print(f"    so k=5..{k_max} are also always removed on first pass.")

CASES_6 = {
    "cap3_retake": [14.0],
    "cap4":        [17.0, 18.0, 19.0],
    "cap5":        [13.0],
}

print(f"\n  Cardiac search band: 50–100 bpm")
for cap_id, fr_list in CASES_6.items():
    print(f"\n  {cap_id.upper()}")
    for fr in fr_list:
        print(f"    f_r = {fr:.0f} bpm  (k_max={k_max}):")
        first_unsup_in_band = None
        for k in range(1, k_max + 3):
            harm = fr * k
            in_band   = CARDIAC_LO <= harm <= CARDIAC_HI
            suppressed = k <= k_max
            if not (suppressed or in_band):
                continue
            sup_label = "SUPPRESSED" if suppressed else "unsuppressed"
            note = ""
            if in_band and suppressed:
                note = "  <-- IN CARDIAC BAND + SUPPRESSED (PROBLEM)"
            elif in_band and not suppressed:
                note = "  <-- in cardiac band, NOT suppressed"
                if first_unsup_in_band is None:
                    first_unsup_in_band = (k, harm)
            print(f"      k={k}: {fr:.0f}*{k} = {harm:>6.1f} bpm  [{sup_label}]{note}")
        if first_unsup_in_band:
            print(f"    First unsuppressed harmonic in cardiac band: "
                  f"k={first_unsup_in_band[0]} at {first_unsup_in_band[1]:.1f} bpm")
        else:
            print(f"    All cardiac-band harmonics are suppressed by k_max={k_max}")

# ─────────────────── TASK 7 ─────────────────────────────────────────────────
print()
print(SEP)
print("TASK 7  —  CANDIDATE FIXES SUMMARY")
print(SEP)

report = """
ROOT CAUSE
----------
All three supine captures (cap3_retake, cap4, cap5) fail: MAE 17-25 bpm.
The seated captures (cap1, cap2) partially work: MAE 4-9 bpm.

The failure is in ECA FIRST-PASS harmonic removal, not in candidate selection.

ECA eca_project() runs two passes. On the FIRST pass, before any cardiac
candidate exists, it removes k=1..k_max=6 harmonics of f_r from the phase
spectrum. On the first pass, "conditional" (k>4) harmonics always fire because
the guard condition (no cardiac candidate found yet) is always True.

Result for each capture:
  cap3_retake  f_r=14 bpm:   6*14 = 84 bpm  -> removed from spectrum
                              (true HR ~75-96 bpm -> cardiac signal erased)
  cap4         f_r=17-19:    4*17 = 68 bpm  -> removed (HR ~72-82 bpm)
                              4*18 = 72 bpm  -> removed
                              4*19 = 76 bpm  -> removed
  cap5         f_r=13 bpm:   6*13 = 78 bpm  -> removed
                              (true HR ~69-94 bpm -> cardiac signal erased)

WHY SEATED WORKED
-----------------
In seated frontal geometry, the chest-wall cardiac pulsation is a strong target
at the locked range bin. Even if a harmonic falls near the cardiac frequency,
the cardiac peak is strong enough to survive as a residual.
In supine OVERHEAD geometry, the dominant motion is vertical respiration.
Cardiac micro-motion is a much weaker contributor. When ECA removes the
spectral region coinciding with the cardiac peak, the cardiac signal
disappears entirely into the noise floor. Nothing is left for candidate selection.

k_max REQUIRED TO AVOID CARDIAC-BAND SUPPRESSION
-------------------------------------------------
  cap3_retake (f_r=14): need k_max <= 5 to leave 6*14=84 bpm unsuppressed
                        need k_max <= 3 to also leave 4*14=56 bpm (not in band anyway)
  cap4 (f_r=17-19):    need k_max <= 3 to leave 4*f_r=68-76 bpm unsuppressed
                        k_max=4 still suppresses 4*f_r for cap4
  cap5 (f_r=13):       need k_max <= 5 to leave 6*13=78 bpm unsuppressed

RISKS OF REDUCING k_max
------------------------
  k_max=4 (remove k=1..4 only):
    - Leaves k=5 and k=6 harmonics IN the spectrum.
    - For cap3_retake (f_r=14): 5*14=70 bpm, 6*14=84 bpm both survive.
      HR is ~75-96 bpm; 5*14=70 bpm is just below band; 6*14=84 bpm IN band
      but now NOT suppressed -> respiratory harmonic contaminates cardiac band.
      Net result: the cardiac signal is no longer suppressed, but a respiratory
      harmonic at 84 bpm competes with the cardiac signal (also ~80-84 bpm).
      Whether the cardiac or respiratory dominates depends on SNR at that frequency.
    - For cap5 (f_r=13): 6*13=78 bpm survives; cardiac is also ~78 bpm.
      Same situation.
    - For cap1/cap2 (seated): regression risk is low because cardiac SNR is higher,
      but 5*f_r and 6*f_r harmonics would add contamination at 85-114 bpm.
      cap1/cap2 have HR ~70-80 bpm, so those harmonics are above typical HR range.
      Regression risk: LOW for seated captures.

  k_max=3 (remove k=1..3 only):
    - Leaves k=4,5,6 in spectrum.
    - cap4 (f_r=17-19): 4*f_r=68-76 bpm would survive but contaminate cardiac band.
      Net benefit for cap4 unclear: the spectral peak at 4*f_r would now be the
      respiratory harmonic, not the cardiac signal. Cardiac SNR determines winner.
    - Seated captures: k=4 harmonic at 4*17=68 bpm left in -> possible regression.
    - Risk: HIGH for seated captures. Not recommended as first test.

RECOMMENDED INVESTIGATION SEQUENCE
-----------------------------------
Step 1 (immediate):
  Run exp004 on cap3_retake with k_max=4 (config change only).
  Rationale: cap3_retake f_r=14 bpm. With k_max=4, the 6th harmonic (84 bpm)
  is NO LONGER suppressed. The cardiac signal (~80 bpm) should reappear.
  Use intermediates.npz to confirm: does heart_spectrum show a peak at ~80-85 bpm?
  Does NaN rate drop? Does MAE improve?

Step 2 (regression check):
  Simultaneously run cap1 with k_max=4 and compare to canonical k_max=6 results.
  cap1 HR is ~71-78 bpm. 5*f_r ~85-90 bpm (above HR). 6*f_r ~100-108 bpm (above HR).
  Expected: no regression (k=5,6 harmonics at 85-108 bpm do not interfere with 71-78 bpm HR).

Step 3 (cap4 with k_max=4):
  cap4 f_r=17-19 bpm. 4*f_r=68-76 bpm = the primary problem harmonic.
  k_max=4 still suppresses k=4, so 4*f_r is STILL removed.
  Expected: cap4 likely does NOT improve at k_max=4.
  This would tell us: cap4 needs a deeper fix than k_max reduction.

Step 4 (if cap4 still broken):
  Design "cardiac-band-aware" first-pass ECA:
    For each harmonic k in 1..k_max:
      if k*f_r falls INSIDE [50, 100] bpm AND k >= 4:
        skip this harmonic in the first-pass subspace
  This requires modifying src/vitals.py and must be cross-reviewed.
  Trade-off: respiratory harmonic at k*f_r survives in cardiac band ->
  may produce false HR reading IF respiratory dominates at that frequency.
  Benefit: cardiac signal is NOT suppressed -> may be recovered after second pass.

Step 5 (Blocker 5):
  Only after clean development captures are available (cap3_retake and ideally cap4),
  implement and tune harmonic exclusion at candidate selection level.

CANNOT DO
---------
Collect captures with BR low enough that 6*f_r < 50 bpm:
  Requires BR < 8.3 bpm. Not a realistic resting breathing rate.

SUMMARY DECISION
----------------
FIRST TEST: change k_max from 6 to 4 in exp004/config.yaml, run on cap3_retake only,
compare NaN rate and MAE against current results (20s MAE=24.78, NaN=32/79).
Inspect heart_spectrum in intermediates.npz to confirm cardiac peak visibility.
This is a config-only change; no DSP code modification required.
"""
print(report)

print(SEP)
print("END OF REPORT")
print(SEP)
