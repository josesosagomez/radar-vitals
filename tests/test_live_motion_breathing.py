"""Synthetic software checks; these thresholds are not physical calibration."""
import copy

import numpy as np
import pytest
from scipy.fft import rfft, irfft

from src.live_motion import breathing as br


@pytest.fixture
def gates():
    return dict(fft_score_min=1., ha_score_min=0., quiet_resp_rms_max=1e-6,
                periodic_resp_rms_min=.1, subband_rms_max=1e-6,
                drift_rms_max=1e-6, persistence_min=.7)


def tone(rate, phase=0., amplitude=1.):
    return amplitude * np.sin(2 * np.pi * (rate / 60) * np.arange(1200) / 20 + phase)


@pytest.mark.parametrize("rate", [3, 4, 5, 6, 12, 30])
@pytest.mark.parametrize("phase", [0., .73, 2.1])
def test_supported_tones_and_boundary_reporting(rate, phase, gates):
    result = br.assess_breathing(tone(rate, phase), 20., gates)
    assert result.state == "positive", result.reason
    assert result.value_bpm == pytest.approx(rate, abs=.02)
    e = result.evidence
    assert e["fft_selected_bin"] == e["ha_selected_bin"] == rate
    assert e["candidate_local_max"]
    assert e["positive_evaluated"] and not e["quiet_evaluated"]
    if rate in (3, 30):
        assert result.value_bpm == rate
        assert not e["refinement_available"]
        assert e["refinement_reason"] == "band_boundary_raw_center"


@pytest.mark.parametrize("rate", [3.2, 10.4])
def test_off_grid_fixed_raw_candidate_policy(rate, gates):
    result = br.assess_breathing(tone(rate), 20., gates)
    e = result.evidence
    assert e["candidate_hz"] == round(rate) / 60
    assert e["persistence_frequency_policy"] == "raw_full_window_bin_center"
    reference = br.persistence_score(e["respiratory_projection"], round(rate) / 60)
    assert e["persistence"] == reference["persistence"]
    if rate == 3.2:
        assert result.value_bpm == 3.
        assert not e["refinement_available"]
    else:
        assert 10 < e["rate_bpm"] < 11
        assert e["refinement_available"]


@pytest.mark.parametrize("rate", [3, 6, 12, 30])
def test_seeded_noise_and_harmonics_remain_positive(rate, gates):
    rng = np.random.default_rng(314)
    phase = tone(rate, .7) + .25*tone(rate*2, .2) + rng.normal(0, .003, 1200)
    result = br.assess_breathing(phase, 20., gates)
    assert result.state == "positive", result.reason
    assert result.value_bpm == pytest.approx(rate, abs=.05)


@pytest.mark.parametrize("key,reason", [("peaks_agree", "peaks_disagree"),
                                       ("candidate_local_max", "not_local_maximum")])
def test_disagreeing_bins_and_non_local_maxima_reject(monkeypatch, gates, key, reason):
    measured = br.measure_breathing(tone(12))
    measured[key] = False
    monkeypatch.setattr(br, "measure_breathing", lambda *args: copy.deepcopy(measured))
    result = br.assess_breathing(tone(12), 20., gates)
    assert result.state == "unresolved"
    assert reason in result.evidence["positive_rejections"]


def test_projection_and_persistence_reconstruct_independently():
    x = tone(12, .3) + .1 * tone(24) + .003 * np.arange(1200)
    e = br.measure_breathing(x)
    t = np.arange(1200, dtype=float)
    trend = np.column_stack((t, np.ones(1200))) @ np.linalg.lstsq(
        np.column_stack((t, np.ones(1200))), x, rcond=None)[0]
    coeff = rfft(x - trend)
    coeff[:3] = 0
    coeff[31:] = 0
    projection = irfft(coeff, n=1200)
    np.testing.assert_allclose(e["respiratory_projection"], projection, atol=2e-14)
    np.testing.assert_allclose(e["respiratory_block_rms"],
                               np.linalg.norm(projection.reshape(6, 200), axis=1) / np.sqrt(200))
    independent = []
    for h in range(2):
        y = projection[h*600:(h+1)*600]
        y = y - y.mean()
        seconds = np.arange(h*600, (h+1)*600) / 20
        basis = np.column_stack((np.sin(2*np.pi*e["candidate_hz"]*seconds),
                                 np.cos(2*np.pi*e["candidate_hz"]*seconds)))
        basis -= basis.mean(axis=0)
        fitted = basis @ np.linalg.lstsq(basis, y, rcond=None)[0]
        independent.append(np.dot(fitted, fitted) / np.dot(y, y))
        saved = e["persistence_bases"][h]
        np.testing.assert_allclose(saved @ saved.T, np.eye(2), atol=2e-14)
        np.testing.assert_allclose(saved.mean(axis=1), 0, atol=2e-16)
    np.testing.assert_allclose(e["persistence_half_scores"], independent, atol=2e-14)
    assert e["persistence"] == min(e["persistence_half_scores"])


@pytest.mark.parametrize("signal", [np.zeros(1200), np.ones(1200)*8])
def test_perfect_quiet_has_no_numeric_zero(signal, gates):
    result = br.assess_breathing(signal, 20., gates)
    assert result.state == "quiet"
    assert np.isnan(result.value_bpm)
    assert result.reason == "No breathing motion detected"
    assert result.evidence["quiet_evaluated"]
    assert result.evidence["fft_score"] == result.evidence["ha_score"] == 0


@pytest.mark.parametrize("signal", [tone(2), np.arange(1200)*.01,
                                    tone(40), np.random.default_rng(19).normal(0, .02, 1200)])
def test_unsupported_activity_does_not_become_positive_or_quiet(signal, gates):
    result = br.assess_breathing(signal, 20., gates)
    assert result.state == "unresolved"
    assert np.isnan(result.value_bpm)


@pytest.mark.parametrize("signal", [np.zeros(1199), np.full(1200, np.nan), np.zeros((2, 600))])
def test_invalid_windows_stable_schema(signal, gates):
    result = br.assess_breathing(signal, 20., gates)
    assert result.state == "unresolved"
    for key in ("positive_rejections", "quiet_rejections", "respiratory_fourier_coefficients",
                "subband_fourier_coefficients", "fft_selected_bin", "ha_selected_bin"):
        assert key in result.evidence
    assert not result.evidence["positive_evaluated"]
    assert not result.evidence["quiet_evaluated"]
    assert not result.evidence["persistence_evaluated"]


def test_missing_target_cannot_be_quiet(gates):
    result = br.assess_breathing(np.zeros(1200), 20., gates, eligible=False)
    assert result.state == "unresolved" and result.reason == "physical_quality"
    assert np.isnan(result.value_bpm)


def test_threshold_equality_and_every_block_are_decisive(monkeypatch, gates):
    measured = br.measure_breathing(tone(12))
    gates.update(fft_score_min=measured["fft_score"], ha_score_min=measured["ha_score"],
                 periodic_resp_rms_min=min(measured["respiratory_block_rms"]),
                 persistence_min=measured["persistence"])
    assert br.assess_breathing(tone(12), 20, gates).state == "positive"
    for key in ("fft_score", "ha_score", "persistence"):
        bad = copy.deepcopy(measured)
        bad[key] = np.nextafter(bad[key], -np.inf)
        monkeypatch.setattr(br, "measure_breathing", lambda *args, value=bad: copy.deepcopy(value))
        assert br.assess_breathing(tone(12), 20, gates).state != "positive"
    for block in range(6):
        bad = copy.deepcopy(measured)
        bad["respiratory_block_rms"][block] = gates["periodic_resp_rms_min"] / 2
        monkeypatch.setattr(br, "measure_breathing", lambda *args, value=bad: copy.deepcopy(value))
        assert br.assess_breathing(tone(12), 20, gates).state != "positive"


def test_quiet_equality_and_subband_drift_vetoes(monkeypatch, gates):
    measured = br.measure_breathing(np.zeros(1200))
    measured["respiratory_block_rms"][:] = gates["quiet_resp_rms_max"]
    measured["subband_block_rms"][:] = gates["subband_rms_max"]
    measured["drift_rms"] = gates["drift_rms_max"]
    monkeypatch.setattr(br, "measure_breathing", lambda *args: copy.deepcopy(measured))
    assert br.assess_breathing(np.zeros(1200), 20, gates).state == "quiet"
    for key in ("respiratory_block_rms", "subband_block_rms", "drift_rms"):
        bad = copy.deepcopy(measured)
        if key.endswith("block_rms"):
            bad[key][5] *= 2
        else:
            bad[key] *= 2
        monkeypatch.setattr(br, "measure_breathing", lambda *args, value=bad: copy.deepcopy(value))
        assert br.assess_breathing(np.zeros(1200), 20, gates).state == "unresolved"


@pytest.mark.parametrize("block", range(6))
@pytest.mark.parametrize("field", ["respiratory_block_rms", "subband_block_rms"])
def test_every_quiet_activity_block_must_pass(monkeypatch, gates, block, field):
    measured = br.measure_breathing(np.zeros(1200))
    limit = gates["quiet_resp_rms_max"] if field == "respiratory_block_rms" else gates["subband_rms_max"]
    measured[field][block] = np.nextafter(limit, np.inf)
    monkeypatch.setattr(br, "measure_breathing", lambda *args: copy.deepcopy(measured))
    result = br.assess_breathing(np.zeros(1200), 20, gates)
    assert result.state == "unresolved" and np.isnan(result.value_bpm)


@pytest.mark.parametrize("signal,frequency", [(np.zeros(1200), .2),
    (np.ones(1200), .2), (np.full(1200, np.nan), .2), (np.zeros(1199), .2),
    (tone(12), 0), (tone(12), float("nan"))])
def test_persistence_degenerate_rejections(signal, frequency):
    result = br.persistence_score(signal, frequency)
    assert not result["persistence_valid"] and np.isnan(result["persistence"])


def test_nonstationary_activity_reduces_fixed_candidate_persistence():
    clean = br.persistence_score(tone(12), .2)["persistence"]
    signals = [np.concatenate((tone(12)[:600], tone(18)[600:])),
               np.sin(2*np.pi*(.1*np.arange(1200)/20 + .002*(np.arange(1200)/20)**2)),
               tone(12) * np.concatenate((np.ones(300), np.ones(300)*.1, np.ones(600))),
               np.concatenate((tone(12)[:300], tone(12, 2)[300:]))]
    for signal in signals:
        score = br.persistence_score(signal, .2)
        assert score["persistence_valid"]
        assert score["persistence"] < clean - .01


def test_isolated_large_sigh_is_activity_not_quiet_or_a_supported_rate(gates):
    seconds = np.arange(1200) / 20
    sigh = 12 * np.exp(-.5*((seconds - 35)/1.2)**2)
    result = br.assess_breathing(tone(12) + sigh, 20, gates)
    assert result.state == "unresolved", result.reason
    assert np.isnan(result.value_bpm)
    assert result.evidence["respiratory_block_rms"].max() > gates["quiet_resp_rms_max"]
    assert result.evidence["persistence"] < gates["persistence_min"]


@pytest.mark.parametrize("state,value,fr,reason", [
    ("quiet", np.nan, .2, "quiet_breathing"), ("unresolved", np.nan, .2, ""),
    ("positive", 3., .2, "extended_breathing_below_9_bpm"),
    ("positive", 8.999, .2, "extended_breathing_below_9_bpm"),
    ("positive", 9., 9/60, ""), ("positive", 12., 10/60, ""),
    ("positive", 12.001, 10/60, "extended_ahet_respiration_disagreement"),
    ("positive", 12., np.nan, "missing_ahet_respiration_input"),
    ("positive", np.nan, .2, "invalid_extended_breathing_value")])
def test_hr_coupling_boundaries_and_provenance(state, value, fr, reason):
    dsp = dict(f_r_hz=fr, hr_valid=True, hr_raw=72., hr_no_eca=88.)
    before = dsp.copy()
    decision = br.hr_veto_decision(br.BreathingAssessment(state, value, "", {}), dsp)
    assert decision["hr_veto_reason"] == reason
    assert decision["ahet_respiration_input_bpm"] == pytest.approx(fr*60, nan_ok=True)
    assert dsp == before
    with pytest.raises(TypeError):
        decision["hr_veto_reason"] = "changed"


@pytest.mark.parametrize("key,value", [("persistence_min", 1.1), ("ha_score_min", -1),
    ("quiet_resp_rms_max", .1), ("drift_rms_max", np.nan), ("fft_score_min", True)])
def test_invalid_calibrated_thresholds_rejected(key, value, gates):
    gates[key] = value
    with pytest.raises(ValueError):
        br.assess_breathing(tone(12), 20, gates)
