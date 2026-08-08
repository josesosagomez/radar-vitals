"""M2 respiration-collapse fix — regression + synthetic controls.

Implements the test plan of plans/m2_respiration_fix.md §3 (cross-reviewed, M2R-01…07):
  §3.1 Class-A (leakage) control          — precondition-asserted, then fallback outcome
  §3.2 Class-B (harmonic-inheritance)     — precondition-asserted, then HA rejection
  §3.3 Validity invariant                 — edge veto (aligned + non-aligned), per-branch
                                            STFT consistency, NaN / 1-subwindow / low-vf
  §3.4 No-regression on good signal
  §3.5 Local-max policy controls          — bin-centre, half-bin, edge line, NaN path,
                                            fallback SNR consistency, weak HA fundamental
  §3.6 Evidence-schema persistence
  §3.7 Live/offline parity (scoped to the M2 invariant) + expected divergence

Every fixture asserts its structural precondition first (M2R-06): a fixture that never
reproduced the mechanism cannot silently pass.

Run: pytest tests/test_respiration_m2_fix.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
from scipy.fft import rfft as sp_rfft, rfftfreq as sp_rfftfreq  # noqa: E402

from src.respiration import (  # noqa: E402
    _detrend,
    _is_local_max,
    fft_estimate_rr,
    fuse_estimates,
    ha_estimate_rr,
    stft_stability,
)

FS   = 20.0           # radar frame rate (Hz)
BAND = (0.10, 0.50)   # respiration band (Hz)
DUR  = 30.0           # window length (s) -> freq_res = 1/30 Hz = 2 bpm/bin
T    = np.arange(int(DUR * FS)) / FS

# The 6 bpm edge bin at 30 s / 20 Hz: 0.10 Hz = bin 3 exactly (bin-aligned config).
EDGE_BIN = 3


def _cfg(**over) -> dict:
    cfg = {
        "fft_ha_agree_bpm_high":   2.0,
        "fft_ha_agree_bpm_medium": 4.0,
        "stft_std_high_bpm":       2.0,
        "stft_std_medium_bpm":     4.0,
        "fft_fallback_snr_db":     6.0,
        "stft_match_bpm":          6.0,
        "stft_min_valid_fraction": 0.5,
        "emit_low_confidence":     False,
    }
    cfg.update(over)
    return cfg


def _spec_of(phase: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Spectrum exactly as fft_estimate_rr computes it (linear detrend + Hann + |rfft|)."""
    x = _detrend(np.asarray(phase, dtype=np.float64), "linear")
    spec  = np.abs(sp_rfft(x * np.hanning(len(x))))
    freqs = sp_rfftfreq(len(x), d=1.0 / FS)
    return freqs, spec


def _band_indices(freqs: np.ndarray, band=BAND) -> np.ndarray:
    return np.where((freqs >= band[0]) & (freqs <= band[1]))[0]


# ---------------------------------------------------------------------------
# §3.1 Class-A (leakage) control
# ---------------------------------------------------------------------------

def _class_a_phase(noise_seed: int | None = None) -> np.ndarray:
    """Large sub-band drift + weak 17 bpm breathing (the Class-A leakage signature)."""
    phase = 8.0 * np.sin(2 * np.pi * 0.05 * T) + 0.5 * np.sin(2 * np.pi * (17.0 / 60.0) * T)
    if noise_seed is not None:
        phase = phase + 0.05 * np.random.default_rng(noise_seed).standard_normal(len(T))
    return phase


class TestClassALeakageControl:
    @pytest.mark.parametrize("noise_seed", [None, 3])
    def test_precondition_then_fallback(self, noise_seed):
        phase = _class_a_phase(noise_seed)
        freqs, spec = _spec_of(phase)
        bidx = _band_indices(freqs)

        # PRECONDITION (M2R-06): the in-band argmax is the edge bin, and that bin is
        # NOT a local max (monotone decay from below the band) — the pre-fix argmax
        # would have pinned at 6.0 bpm.
        argmax_global = int(bidx[np.argmax(spec[bidx])])
        assert argmax_global == EDGE_BIN, (
            f"fixture drifted: band argmax at bin {argmax_global}, expected edge bin {EDGE_BIN}"
        )
        assert not _is_local_max(spec, EDGE_BIN), (
            "fixture drifted: edge bin is a local max — this fixture must reproduce the "
            "monotone-decay leakage signature"
        )

        # POST-FIX: fallback to the genuine 17 bpm line.
        r = fft_estimate_rr(phase, FS, BAND)
        assert abs(r["fft_rr_bpm"] - 17.0) <= 2.0, (
            f"expected ~17 bpm via fallback, got {r['fft_rr_bpm']:.2f}"
        )
        assert r["fft_band_argmax_bin"] == EDGE_BIN
        assert r["fft_band_argmax_is_local_max"] is False
        assert r["fft_selected_bin"] != r["fft_band_argmax_bin"]
        assert r["fft_selected_is_edge_bin"] is False

    def test_fallback_snr_recomputed_for_selected_bin(self):
        """§3.5: after fallback the SNR must describe the SELECTED bin, not the argmax."""
        phase = _class_a_phase()
        freqs, spec = _spec_of(phase)
        bidx = _band_indices(freqs)
        r = fft_estimate_rr(phase, FS, BAND)

        band_spec = spec[bidx]
        sel_local = int(np.where(bidx == r["fft_selected_bin"])[0][0])
        expected_snr = 20.0 * np.log10(
            float(band_spec[sel_local]) / max(float(np.median(band_spec)), 1e-12)
        )
        assert abs(r["fft_peak_snr_db"] - expected_snr) < 1e-9, (
            "fft_peak_snr_db was not recomputed for the fallback-selected bin"
        )


# ---------------------------------------------------------------------------
# §3.2 Class-B (harmonic-inheritance) control
# ---------------------------------------------------------------------------

def _class_b_phase(noise_seed: int | None = None) -> np.ndarray:
    """Sub-band drift lifting the low bins + a strong 18 bpm line, NO 0.10 Hz component.

    3 x 6 bpm = 18 bpm: the 6 bpm HA candidate inherits the 18 bpm line as its 3rd
    harmonic (the Class-B mechanism). The drift must lift the 6 bpm bin enough that
    2*s6 + s12 > s18 (the old 2x-weighted score win) — the precondition assert below
    keeps the fixture honest.
    """
    phase = 8.0 * np.sin(2 * np.pi * 0.05 * T) + 1.0 * np.sin(2 * np.pi * 0.30 * T)
    if noise_seed is not None:
        phase = phase + 0.05 * np.random.default_rng(noise_seed).standard_normal(len(T))
    return phase


class TestClassBInheritanceControl:
    @pytest.mark.parametrize("noise_seed", [None, 3])
    def test_precondition_then_rejection(self, noise_seed):
        phase = _class_b_phase(noise_seed)
        freqs, spec = _spec_of(phase)
        bidx = _band_indices(freqs)
        band_median = float(np.median(spec[bidx]))

        def _bin(f_hz: float) -> int:
            return int(np.argmin(np.abs(freqs - f_hz)))

        s6, s12, s18 = spec[_bin(0.10)], spec[_bin(0.20)], spec[_bin(0.30)]
        s36, s54 = spec[_bin(0.60)], spec[_bin(0.90)]

        # PRECONDITION (M2R-06): under the OLD algorithm the 6 bpm candidate wins —
        # its fundamental clears the old noise-floor guard, its 2x-weighted score beats
        # the 18 bpm candidate's — while its fundamental fails the NEW predicate.
        assert s6 > band_median, "fixture drifted: old fund_power guard would already reject"
        old_score_6  = (2.0 * s6 + s12 + s18) / 4.0
        old_score_18 = (2.0 * s18 + s36 + s54) / 4.0
        assert old_score_6 > old_score_18, (
            f"fixture drifted: old 6 bpm score {old_score_6:.2f} must beat the 18 bpm "
            f"candidate's {old_score_18:.2f} for this to reproduce Class B"
        )
        assert not _is_local_max(spec, EDGE_BIN), (
            "fixture drifted: the 6 bpm fundamental must fail the new local-max predicate"
        )

        # POST-FIX: HA must not select 6 bpm; the 18 bpm line (a genuine local max) wins.
        r = ha_estimate_rr(phase, FS, BAND, max_harmonics=3)
        assert np.isfinite(r["ha_rr_bpm"])
        assert abs(r["ha_rr_bpm"] - 6.0) > 2.0, (
            f"HA still selects the floor: {r['ha_rr_bpm']:.2f} bpm"
        )
        assert abs(r["ha_rr_bpm"] - 18.0) <= 2.0, (
            f"expected ~18 bpm, got {r['ha_rr_bpm']:.2f}"
        )
        # The 6 bpm candidate's rejection is persisted per candidate (M2R-05 r2).
        cand_6 = int(np.argmin(np.abs(r["ha_candidate_freqs_hz"] - 0.10)))
        assert r["ha_fund_is_local_max"][cand_6] == False  # noqa: E712


# ---------------------------------------------------------------------------
# §3.3 Validity invariant (regression)
# ---------------------------------------------------------------------------

class TestValidityInvariant:
    def test_edge_bin_agreement_never_valid(self):
        """Both estimators pinned at the edge bin, perfectly agreeing, STFT matching:
        every pre-veto branch condition holds — the veto must still kill validity."""
        fft_r  = {"fft_rr_bpm": 6.0, "fft_peak_hz": 0.10, "fft_peak_snr_db": 12.0,
                  "fft_selected_is_edge_bin": True}
        ha_r   = {"ha_rr_bpm": 6.0, "ha_peak_hz": 0.10, "ha_score": 5.0,
                  "ha_selected_is_edge_bin": True}
        stft_r = {"stft_rr_bpm": 6.0, "stft_rr_std_bpm": 1.0, "stft_valid_fraction": 1.0}
        out = fuse_estimates(fft_r, ha_r, stft_r, _cfg())
        assert out["resp_valid"] is False
        assert out["resp_confidence"] == "low"
        assert out["resp_edge_veto"] is True
        assert out["resp_edge_veto_reason"] == "band_edge_bin"
        assert not np.isfinite(out["radar_rr_bpm"])

    def test_genuine_edge_line_scalar_reported_fusion_invalid(self):
        """§2.3 conservative consequence: a genuine 6 bpm breather (bin-aligned edge)
        is reported as a scalar by the estimators but can never be system-valid."""
        phase = 2.0 * np.sin(2 * np.pi * 0.10 * T)
        fft_r  = fft_estimate_rr(phase, FS, BAND)
        ha_r   = ha_estimate_rr(phase, FS, BAND)
        stft_r = stft_stability(phase, FS, BAND)

        assert abs(fft_r["fft_rr_bpm"] - 6.0) <= 1.0     # scalar still reported
        assert fft_r["fft_selected_is_edge_bin"] is True

        out = fuse_estimates(fft_r, ha_r, stft_r, _cfg())
        assert out["resp_valid"] is False
        assert out["resp_edge_veto"] is True

    def test_edge_veto_non_aligned_band(self):
        """Bin-identity veto on a NON-bin-aligned edge (M2R-02): lo=0.115 Hz puts the
        first in-band bin at 0.1333 Hz — above lo + freq_res/2, where the rejected
        frequency-arithmetic veto would have missed it."""
        band = (0.115, 0.50)
        first_bin_hz = 4.0 / DUR  # bin 4 = 0.1333 Hz is the first bin >= 0.115
        phase = 2.0 * np.sin(2 * np.pi * first_bin_hz * T)
        fft_r  = fft_estimate_rr(phase, FS, band)
        ha_r   = ha_estimate_rr(phase, FS, band)
        stft_r = stft_stability(phase, FS, band)

        assert fft_r["fft_selected_is_edge_bin"] is True
        freq_res = 1.0 / DUR
        assert fft_r["fft_peak_hz"] > band[0] + freq_res / 2.0, (
            "fixture must sit above lo + freq_res/2 to prove arithmetic veto would miss"
        )
        out = fuse_estimates(fft_r, ha_r, stft_r, _cfg())
        assert out["resp_valid"] is False
        assert out["resp_edge_veto"] is True

    def test_high_branch_needs_stft_match(self):
        """FFT/HA agree tightly but the stable STFT median is a DIFFERENT rate:
        high must not fire (falls to agreement-only medium)."""
        fft_r  = {"fft_rr_bpm": 12.0, "fft_peak_hz": 0.20, "fft_peak_snr_db": 12.0}
        ha_r   = {"ha_rr_bpm": 12.2, "ha_peak_hz": 12.2 / 60.0, "ha_score": 5.0}
        stft_r = {"stft_rr_bpm": 19.0, "stft_rr_std_bpm": 1.0, "stft_valid_fraction": 1.0}
        out = fuse_estimates(fft_r, ha_r, stft_r, _cfg())
        assert out["resp_confidence"] == "medium"
        assert out["resp_fusion_branch"] == "medium_agree"

    def test_ha_stft_branch_needs_match_not_just_stability(self):
        """The observed Class-B fusion flaw: STFT stable at ~18 while HA says 8 —
        the HA value must NOT be selected; the matching FFT fallback wins instead."""
        fft_r  = {"fft_rr_bpm": 18.0, "fft_peak_hz": 0.30, "fft_peak_snr_db": 12.0}
        ha_r   = {"ha_rr_bpm": 8.0, "ha_peak_hz": 8.0 / 60.0, "ha_score": 5.0}
        stft_r = {"stft_rr_bpm": 18.0, "stft_rr_std_bpm": 2.0, "stft_valid_fraction": 1.0}
        out = fuse_estimates(fft_r, ha_r, stft_r, _cfg())
        assert out["resp_valid"]
        assert out["resp_fusion_branch"] == "fft_fallback"
        assert abs(out["radar_rr_bpm"] - 18.0) < 0.1, (
            f"selected {out['radar_rr_bpm']:.1f}; STFT stability of 18 must not bless HA's 8"
        )

    def test_fft_fallback_needs_stft_match(self):
        fft_r  = {"fft_rr_bpm": 18.0, "fft_peak_hz": 0.30, "fft_peak_snr_db": 12.0}
        ha_r   = {"ha_rr_bpm": float("nan"), "ha_peak_hz": float("nan"), "ha_score": float("nan")}
        stft_r = {"stft_rr_bpm": 10.0, "stft_rr_std_bpm": 2.0, "stft_valid_fraction": 1.0}
        out = fuse_estimates(fft_r, ha_r, stft_r, _cfg())
        assert out["resp_confidence"] == "low"
        assert not out["resp_valid"]

    def test_stft_nan_blocks_stft_branches(self):
        ha_r   = {"ha_rr_bpm": 15.0, "ha_peak_hz": 0.25, "ha_score": 5.0}
        fft_r  = {"fft_rr_bpm": float("nan"), "fft_peak_hz": float("nan"),
                  "fft_peak_snr_db": float("nan")}
        stft_r = {"stft_rr_bpm": float("nan"), "stft_rr_std_bpm": float("nan"),
                  "stft_valid_fraction": 0.0}
        out = fuse_estimates(fft_r, ha_r, stft_r, _cfg())
        assert out["resp_confidence"] == "low"

    def test_single_valid_subwindow_not_stable(self):
        """One valid subwindow -> std is NaN -> no STFT-dependent branch may fire."""
        ha_r   = {"ha_rr_bpm": 15.0, "ha_peak_hz": 0.25, "ha_score": 5.0}
        fft_r  = {"fft_rr_bpm": float("nan"), "fft_peak_hz": float("nan"),
                  "fft_peak_snr_db": float("nan")}
        stft_r = {"stft_rr_bpm": 15.0, "stft_rr_std_bpm": float("nan"),
                  "stft_valid_fraction": 0.2}
        out = fuse_estimates(fft_r, ha_r, stft_r, _cfg())
        assert out["resp_confidence"] == "low"

    def test_low_valid_fraction_blocks_stft_branches(self):
        """A small surviving subwindow subset with a quiet std is not stability (M2R-03)."""
        ha_r   = {"ha_rr_bpm": 15.0, "ha_peak_hz": 0.25, "ha_score": 5.0}
        fft_r  = {"fft_rr_bpm": float("nan"), "fft_peak_hz": float("nan"),
                  "fft_peak_snr_db": float("nan")}
        stft_r = {"stft_rr_bpm": 15.0, "stft_rr_std_bpm": 1.0, "stft_valid_fraction": 0.3}
        out = fuse_estimates(fft_r, ha_r, stft_r, _cfg())
        assert out["resp_confidence"] == "low"


# ---------------------------------------------------------------------------
# §3.4 No-regression on good signal
# ---------------------------------------------------------------------------

class TestNoRegression:
    def test_clean_16bpm_still_valid(self):
        rng = np.random.default_rng(0)
        phase = 2.0 * np.sin(2 * np.pi * (16.0 / 60.0) * T) + 0.05 * rng.standard_normal(len(T))
        fft_r  = fft_estimate_rr(phase, FS, BAND)
        ha_r   = ha_estimate_rr(phase, FS, BAND)
        stft_r = stft_stability(phase, FS, BAND)
        out = fuse_estimates(fft_r, ha_r, stft_r, _cfg())
        assert out["resp_valid"]
        assert out["resp_edge_veto"] is False
        assert abs(out["radar_rr_bpm"] - 16.0) <= 2.0


# ---------------------------------------------------------------------------
# §3.5 Local-max policy controls
# ---------------------------------------------------------------------------

class TestLocalMaxPolicy:
    def test_bin_centre_peak_accepted(self):
        phase = 2.0 * np.sin(2 * np.pi * (16.0 / 60.0) * T)  # 0.2667 Hz = bin 8 exactly
        r = fft_estimate_rr(phase, FS, BAND)
        assert abs(r["fft_rr_bpm"] - 16.0) <= 1.0
        assert r["fft_band_argmax_is_local_max"] is True

    def test_exact_half_bin_accepted_by_plateau_rule(self):
        """17 bpm = 0.28333 Hz lies exactly between the 16/18 bpm bins (M2R-06):
        the >=-upper plateau tolerance must accept it."""
        phase = 2.0 * np.sin(2 * np.pi * (17.0 / 60.0) * T)
        r = fft_estimate_rr(phase, FS, BAND)
        assert np.isfinite(r["fft_rr_bpm"]), "half-bin genuine line was rejected"
        assert abs(r["fft_rr_bpm"] - 17.0) <= 1.5

    def test_no_local_max_returns_nan(self):
        """An all-zero spectrum has no strict local max anywhere in band: the NaN path.
        (A linear ramp is unusable here — its ~1e-13 detrend residue random-walks into
        spurious strict local maxima.)"""
        phase = np.zeros(len(T))
        r = fft_estimate_rr(phase, FS, BAND)
        assert not np.isfinite(r["fft_rr_bpm"])
        assert r["fft_selected_bin"] == -1
        assert r["fft_peak_bin"] == -1

    def test_ha_weak_but_real_fundamental_kept(self):
        """A weak genuine fundamental (a real line) with a dominant out-of-band
        2nd harmonic must still be recovered — HA's design intent survives the guard."""
        rng = np.random.default_rng(11)
        fb = 16.0 / 60.0  # 2f = 0.5333 Hz, above the band
        phase = (0.3 * np.sin(2 * np.pi * fb * T)
                 + 2.0 * np.sin(2 * np.pi * 2 * fb * T)
                 + 0.02 * rng.standard_normal(len(T)))
        r = ha_estimate_rr(phase, FS, BAND, max_harmonics=3)
        assert np.isfinite(r["ha_rr_bpm"])
        assert abs(r["ha_rr_bpm"] - 16.0) <= 2.0

    def test_ha_absent_fundamental_excluded(self):
        """With NO 16 bpm line (only the 32 bpm harmonic), the 16 bpm sub-harmonic
        candidate must be flagged non-line and never selected (declared residual-risk
        boundary of §2.2)."""
        rng = np.random.default_rng(12)
        phase = (2.0 * np.sin(2 * np.pi * (32.0 / 60.0) * T)
                 + 0.02 * rng.standard_normal(len(T)))
        r = ha_estimate_rr(phase, FS, BAND, max_harmonics=3)
        cand_16 = int(np.argmin(np.abs(r["ha_candidate_freqs_hz"] - 16.0 / 60.0)))
        # The seed is fixed (default_rng(12)) and the 16 bpm line is genuinely absent, so
        # the non-line flag is a deterministic property of this input. Asserting it keeps
        # the sub-harmonic exclusion below in coverage instead of skipping past it.
        assert not r["ha_fund_is_local_max"][cand_16], (
            "the empty 16 bpm bin was flagged a local max, so the non-line guard no "
            "longer excludes an absent fundamental on this deterministic input"
        )
        if np.isfinite(r["ha_rr_bpm"]):
            assert abs(r["ha_rr_bpm"] - 16.0) > 2.0, (
                "HA selected the sub-harmonic despite an absent fundamental line"
            )


# ---------------------------------------------------------------------------
# §3.6 Evidence-schema persistence
# ---------------------------------------------------------------------------

M2_SCALAR_KEYS = [
    "fft_band_argmax_bin", "fft_band_argmax_is_local_max", "fft_selected_bin",
    "ha_selected_bin", "stft_rr_std_bpm", "stft_valid_fraction",
    "resp_edge_veto", "resp_edge_veto_reason", "resp_fusion_branch", "resp_valid",
]
M2_ARRAY_KEYS = ["ha_candidate_freqs_hz", "ha_candidate_scores", "ha_fund_is_local_max"]


class TestEvidenceSchema:
    def test_estimator_outputs_carry_decision_fields(self):
        phase = _class_a_phase()
        fft_r = fft_estimate_rr(phase, FS, BAND)
        ha_r  = ha_estimate_rr(phase, FS, BAND)
        for k in ("fft_band_argmax_bin", "fft_band_argmax_is_local_max",
                  "fft_selected_bin", "fft_selected_is_edge_bin"):
            assert k in fft_r, f"fft_estimate_rr missing {k}"
        for k in ("ha_candidate_freqs_hz", "ha_candidate_scores",
                  "ha_fund_is_local_max", "ha_selected_bin", "ha_selected_is_edge_bin"):
            assert k in ha_r, f"ha_estimate_rr missing {k}"
        assert len(ha_r["ha_fund_is_local_max"]) == len(ha_r["ha_candidate_freqs_hz"])

    def test_live_writer_persists_m2_fields(self, tmp_path):
        """The live NPZ writer must persist every M2 decision field — a field that
        exists only in an in-memory dict is not evidence (M2R-05 r2). Exercises the
        real _save_intermediates round-trip AND greps the writer source for the keys."""
        from scripts.live_demo import _save_intermediates

        phase = _class_a_phase()
        fft_r  = fft_estimate_rr(phase, FS, BAND)
        ha_r   = ha_estimate_rr(phase, FS, BAND)
        stft_r = stft_stability(phase, FS, BAND)
        fused  = fuse_estimates(fft_r, ha_r, stft_r, _cfg())

        record = {
            "fft_band_argmax_bin": int(fft_r["fft_band_argmax_bin"]),
            "fft_band_argmax_is_local_max": bool(fft_r["fft_band_argmax_is_local_max"]),
            "fft_selected_bin": int(fft_r["fft_selected_bin"]),
            "ha_candidate_freqs_hz": ha_r["ha_candidate_freqs_hz"],
            "ha_candidate_scores": ha_r["ha_candidate_scores"],
            "ha_fund_is_local_max": ha_r["ha_fund_is_local_max"],
            "ha_selected_bin": int(ha_r["ha_selected_bin"]),
            "stft_rr_std_bpm": stft_r["stft_rr_std_bpm"],
            "stft_valid_fraction": stft_r["stft_valid_fraction"],
            "resp_edge_veto": bool(fused["resp_edge_veto"]),
            "resp_edge_veto_reason": str(fused["resp_edge_veto_reason"]),
            "resp_fusion_branch": str(fused["resp_fusion_branch"]),
            "resp_valid": bool(fused["resp_valid"]),
        }
        out = tmp_path / "schema_check.npz"
        _save_intermediates(out, [record, record])
        z = np.load(out, allow_pickle=True)
        for k in M2_SCALAR_KEYS + M2_ARRAY_KEYS:
            assert k in z.files, f"_save_intermediates dropped {k}"
            assert len(np.atleast_1d(z[k])) == 2 or z[k].shape[0] == 2

        # The writer source must actually emit these keys per window.
        src = (_ROOT / "scripts" / "live_demo.py").read_text(encoding="utf-8")
        for k in M2_SCALAR_KEYS + M2_ARRAY_KEYS:
            assert f'"{k}"' in src, f"live_demo.py NPZ writer does not persist {k}"


# ---------------------------------------------------------------------------
# §3.7 Live/offline parity — scoped to the M2 invariant — and expected divergence
# ---------------------------------------------------------------------------

class TestLiveOfflineParity:
    def test_fusion_is_shared_between_callers(self):
        import steps.step_5.extract_breathing_rate as s5
        import src.respiration as resp
        assert s5.fuse_estimates is resp.fuse_estimates, (
            "step-5 no longer shares fuse_estimates — the M2 invariant would diverge"
        )

    def test_invariant_first_bin_invalid_under_both_configs(self):
        """The M2 invariant holds under the live config AND the step-5 config."""
        phase = 2.0 * np.sin(2 * np.pi * 0.10 * T)
        fft_r  = fft_estimate_rr(phase, FS, BAND)
        ha_r   = ha_estimate_rr(phase, FS, BAND)
        stft_r = stft_stability(phase, FS, BAND)

        live_cfg = yaml.safe_load(
            (_ROOT / "scripts" / "live_demo_config.yaml").read_text(encoding="utf-8")
        )["respiration"]
        s5_cfg = yaml.safe_load(
            (_ROOT / "steps" / "step_5" / "config.yaml").read_text(encoding="utf-8")
        )["respiration"]
        for cfg in (live_cfg, s5_cfg):
            out = fuse_estimates(fft_r, ha_r, stft_r, cfg)
            assert out["resp_valid"] is False
            assert out["resp_edge_veto"] is True

    def test_expected_divergence_offline_margins(self):
        """EXPECTED divergence (M2R-07 r2), asserted not papered over: an 8 bpm
        estimate is valid from fusion (not the first bin) but falls inside step-5's
        offline low-edge margin; a 29 bpm estimate likewise at the high edge. The
        offline lock is deliberately preserved on both edges."""
        s5_resp = yaml.safe_load(
            (_ROOT / "steps" / "step_5" / "config.yaml").read_text(encoding="utf-8")
        )["respiration"]
        lo_bpm = float(s5_resp["band_hz"][0]) * 60.0
        hi_bpm = float(s5_resp["band_hz"][1]) * 60.0
        margin = float(s5_resp["edge_lock_margin_bpm"])

        # 8 bpm: valid in fusion...
        fft_r  = {"fft_rr_bpm": 8.0, "fft_peak_hz": 8.0 / 60.0, "fft_peak_snr_db": 12.0,
                  "fft_selected_is_edge_bin": False}
        ha_r   = {"ha_rr_bpm": 8.0, "ha_peak_hz": 8.0 / 60.0, "ha_score": 5.0,
                  "ha_selected_is_edge_bin": False}
        stft_r = {"stft_rr_bpm": 8.0, "stft_rr_std_bpm": 1.0, "stft_valid_fraction": 1.0}
        out = fuse_estimates(fft_r, ha_r, stft_r, _cfg())
        assert out["resp_valid"] is True
        # ...but inside the offline-only low-edge margin -> step-5 invalidates it.
        assert out["radar_rr_bpm"] <= lo_bpm + margin

        # 29 bpm: valid in fusion (no high-edge veto in fusion, by design)...
        fft_r  = {"fft_rr_bpm": 29.0, "fft_peak_hz": 29.0 / 60.0, "fft_peak_snr_db": 12.0,
                  "fft_selected_is_edge_bin": False}
        ha_r   = {"ha_rr_bpm": 29.0, "ha_peak_hz": 29.0 / 60.0, "ha_score": 5.0,
                  "ha_selected_is_edge_bin": False}
        stft_r = {"stft_rr_bpm": 29.0, "stft_rr_std_bpm": 1.0, "stft_valid_fraction": 1.0}
        out = fuse_estimates(fft_r, ha_r, stft_r, _cfg())
        assert out["resp_valid"] is True
        # ...but inside the offline-only high-edge margin -> step-5 invalidates it.
        assert out["radar_rr_bpm"] >= hi_bpm - margin
