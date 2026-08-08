"""Tests for the M8 Ahmed real-data arm: the all-bins sweep and its scorer.

This is the first real-data evaluation of a published method, and the result is strongly
negative. A negative result is only worth anything if the harness that produced it cannot
itself be the cause, so these tests target the four ways it could be:

1. **The sweep must not read the reference.** If it did, "no bin, band or threshold was
   chosen by reference agreement" would be false and the whole arm would be circular.
2. **Every cell must be swept.** A silently truncated sweep would let "it fails at every
   bin" mean "it fails at the bins we happened to run".
3. **The scoring maths must be right**, including the constant-session-median baseline
   that makes the HR numbers interpretable at all (HANDOFF §2.2).
4. **The ceiling must stay labelled.** `best_bin_CEILING` picks the bin using the
   reference; if it ever leaked into a headline row it would be an unreachable number
   presented as an achievable one.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import m8_ahmed_all_bins as sweep  # noqa: E402
import m8_ahmed_score as scorer  # noqa: E402
from src.m8.ahmed_transfer import (  # noqa: E402
    COLLISION_DOMAIN_FROM_FB, REAL_REPRESENTATIVE_DOMAIN, AhmedPhaseConfig,
    AhmedPhaseEstimatorSuite,
)


# ── 1. the sweep must not read the reference ─────────────────────────────────


def test_sweep_never_opens_a_masimo_file():
    """Structural: the sweep imports no Masimo parser and names no reference column.

    Checked on the IMPORT LINES rather than by substring, so the module docstring is free
    to say "this script opens no Masimo file" without defeating its own test.
    """
    src = Path(sweep.__file__).read_text(encoding="utf-8")
    imports = [ln for ln in src.splitlines()
               if ln.startswith(("import ", "from ")) or ln.lstrip().startswith(
                   ("import ", "from "))]
    offenders = [ln for ln in imports if "masimo" in ln.lower()]
    assert not offenders, f"the sweep imports a Masimo parser: {offenders}"
    for col in ("hr_ref_bpm", "br_ref_bpm", "hr_admitted", "br_admitted"):
        assert col not in src, f"the sweep names the reference column {col}"


@pytest.mark.optional_artifact_or_mode
def test_sweep_declares_masimo_unopened_in_its_metadata():
    """The claim is recorded in the artifact, not only in the docstring.

    The sweep output lives under the gitignored ``results/`` tree, so this node is a
    declared optional-artifact skip: on a clean clone there is nothing to inspect.
    """
    runs = sorted((REPO_ROOT / "results" / "m8" / "ahmed_all_bins").glob("*/run_meta.json"))
    if not runs:
        pytest.skip("no all-bins run on disk")
    meta = json.loads(runs[-1].read_text(encoding="utf-8"))
    assert meta["masimo_opened"] is False
    assert meta["parent_gate_bundle"]["stage"] == "synthetic"


def test_sweep_refuses_to_run_without_a_frozen_gate(tmp_path):
    """The real-data arm is only meaningful if the synthetic control passed first."""
    with pytest.raises(SystemExit, match="no frozen gate bundle"):
        sweep.gate_parent(tmp_path)


def test_only_the_real_domain_is_eligible_for_real_data():
    """COLLISION_DOMAIN_FROM_FB's heart floor is the KNOWN SYNTHETIC breathing frequency."""
    assert REAL_REPRESENTATIVE_DOMAIN.real_data_eligible is True
    assert COLLISION_DOMAIN_FROM_FB.real_data_eligible is False
    src = Path(sweep.__file__).read_text(encoding="utf-8")
    assert "REAL_REPRESENTATIVE_DOMAIN" in src
    assert "AhmedPhaseConfig(domain=COLLISION" not in src


# ── 2. every cell must be swept ──────────────────────────────────────────────


@pytest.mark.optional_artifact_or_mode
def test_row_count_is_windows_times_bins_times_arms():
    runs = sorted((REPO_ROOT / "results" / "m8" / "ahmed_all_bins").glob("*/run_meta.json"))
    if not runs:
        pytest.skip("no all-bins run on disk")
    meta = json.loads(runs[-1].read_text(encoding="utf-8"))
    expected = sum(c["n_windows"] * c["n_bins"] * c["n_arms"] for c in meta["captures"])
    assert meta["n_rows"] == expected
    df = pd.read_csv(runs[-1].parent / "windows.csv")
    assert len(df) == expected
    # A complete grid: every (capture, k) carries every bin, and every (cell) every arm.
    for (cid, k), g in df.groupby(["capture_id", "k"]):
        assert g["bin"].nunique() == 14, f"{cid} k={k} swept {g['bin'].nunique()} bins"
        for b, bg in g.groupby("bin"):
            assert len(bg) == 6, f"{cid} k={k} bin={b} has {len(bg)} arms"


@pytest.mark.optional_artifact_or_mode
def test_all_bins_of_a_window_share_one_decoded_frame_span():
    """The cube is decoded once per window and reused; the span must therefore match."""
    runs = sorted((REPO_ROOT / "results" / "m8" / "ahmed_all_bins").glob("*/windows.csv"))
    if not runs:
        pytest.skip("no all-bins run on disk")
    df = pd.read_csv(runs[-1])
    for (cid, k), g in df.groupby(["capture_id", "k"]):
        assert g["frame_start"].nunique() == 1 and g["frame_end"].nunique() == 1
        assert int(g["frame_end"].iloc[0]) - int(g["frame_start"].iloc[0]) == 600


@pytest.mark.optional_artifact_or_mode
def test_all_arms_of_a_cell_share_one_extracted_phase():
    """`extract_chest_phase` is called once per cell; all six arms hash to the same signal."""
    runs = sorted((REPO_ROOT / "results" / "m8" / "ahmed_all_bins").glob("*/windows.csv"))
    if not runs:
        pytest.skip("no all-bins run on disk")
    df = pd.read_csv(runs[-1])
    sample = df[(df.capture_id == df.capture_id.iloc[0]) & (df.k == df.k.iloc[0])]
    for b, g in sample.groupby("bin"):
        assert g["shared_signal_hash"].nunique() == 1, f"bin {b} arms disagree on the phase"


def test_suite_produces_six_arms_over_two_h_and_three_profiles():
    suite = AhmedPhaseEstimatorSuite(AhmedPhaseConfig(domain=REAL_REPRESENTATIVE_DOMAIN))
    assert len(suite.arm_specs) == 6
    assert {s.harmonic_count for s in suite.arm_specs} == {3, 5}
    assert len({s.suppression_profile for s in suite.arm_specs}) == 3


# ── 3. the scoring maths ─────────────────────────────────────────────────────


def test_score_computes_mae_rmse_and_hits():
    err = np.array([0.0, 2.0, 4.0, 10.0])
    got = scorer._score(err, n_windows=8, n_emitted=4, hit_bands=(5.0,))
    assert got["mae_bpm"] == pytest.approx(4.0)
    assert got["rmse_bpm"] == pytest.approx(np.sqrt((0 + 4 + 16 + 100) / 4), abs=5e-5)
    assert got["hit_5bpm"] == pytest.approx(0.75)
    assert got["coverage"] == pytest.approx(0.5)
    assert got["n_scored"] == 4


def test_score_condition_excludes_unemitted_and_inadmissible_windows():
    cells = pd.DataFrame({
        "hr_ref_bpm": [80.0, 80.0, np.nan, 80.0],
        "hr_est_bpm": [78.0, 60.0, 80.0, 82.0],
        "hr_emitted": [True, False, True, True],
    })
    got = scorer.score_condition(cells, "hr", (5.0,))
    assert got["n_windows"] == 4
    assert got["n_emitted"] == 3          # emitted flag, independent of admissibility
    assert got["n_scored"] == 2           # the NaN reference window is not scored
    assert got["mae_bpm"] == pytest.approx(2.0)


def test_constant_baseline_ignores_the_radar_entirely():
    """It must depend only on the reference — that is what makes it the honest floor."""
    src = Path(scorer.__file__).read_text(encoding="utf-8")
    # Anchor on the CODE occurrence, not the docstring's first mention.
    i = src.index('"method": "constant_session_median"')
    block = src[max(0, i - 900):i]
    assert "nanmedian" in block, "the baseline must be the reference median"
    for radar_col in ("hr_bpm", "br_bpm", "arm_id"):
        assert radar_col not in block, f"the baseline block reads radar column {radar_col}"


def test_hit_bands_match_the_frozen_comparators():
    """HR ±5 (comparator_prespec §2.2); BR ±2/±3 (comparator_prespec_br §2.3)."""
    assert scorer.HR_HIT_BPM == 5.0
    assert scorer.BR_HIT_BPM == (2.0, 3.0)


# ── 4. the ceiling must stay labelled ────────────────────────────────────────


def test_ceiling_condition_is_named_so_it_cannot_be_quoted_as_a_result():
    src = Path(scorer.__file__).read_text(encoding="utf-8")
    assert '"best_bin_CEILING"' in src
    assert "CEILING, not a result" in src or "CEILING, NOT A RESULT" in src


@pytest.mark.optional_artifact_or_mode
def test_ceiling_is_never_worse_than_the_production_lock():
    """It selects the best bin using the reference, so by construction it bounds the rest."""
    runs = sorted((REPO_ROOT / "results" / "m8" / "ahmed_score").glob("*/scores.csv"))
    if not runs:
        pytest.skip("no scoring run on disk")
    df = pd.read_csv(runs[-1])
    for (cid, vital, method), g in df.groupby(["capture_id", "vital", "method"]):
        ceil = g[g.condition == "best_bin_CEILING"]["mae_bpm"]
        lock = g[g.condition == "production_lock"]["mae_bpm"]
        if len(ceil) and len(lock) and np.isfinite(ceil.iloc[0]) and np.isfinite(lock.iloc[0]):
            assert ceil.iloc[0] <= lock.iloc[0] + 1e-9, f"{cid} {vital} {method}"


@pytest.mark.optional_artifact_or_mode
def test_scoring_run_records_the_hr_interpretation_limit():
    """The §2.2 limit must travel with the numbers, not live only in a chat message."""
    runs = sorted((REPO_ROOT / "results" / "m8" / "ahmed_score").glob("*/run_meta.json"))
    if not runs:
        pytest.skip("no scoring run on disk")
    meta = json.loads(runs[-1].read_text(encoding="utf-8"))
    assert "NOT HR tracking" in meta["hr_limit"]
    assert "constant_session_median" in meta["hr_limit"]
    assert "never a result" in meta["ceiling_note"]
    assert "not comparable" in meta["coverage_note"]
