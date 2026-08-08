"""Preflight and completeness tests for the radar-only runner (plan sections 4.2, 5.1, 6.3).

The central test is `test_rejected_preflight_touches_no_capture_path`: authorization that
is checked *after* opening a capture has already leaked the thing it exists to control, so
ordering is asserted with a filesystem guard rather than assumed from reading the code.

All fixtures are temporary. Nothing here opens a real capture or Masimo file.
"""
from __future__ import annotations

import builtins
import json
import os
from pathlib import Path
import sys

import numpy as np
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.m4.bundle import BundleWriter, new_run_id, read_manifest  # noqa: E402
from src.m4.capture_registry import load_registry  # noqa: E402
from src.m4.estimator_runner import (  # noqa: E402
    LOCK_ESTIMANDS,
    Authorization,
    CartesianLedger,
    PreflightError,
    load_authorization,
    verify_gate_bundle,
    verify_preflight,
)

SOURCE_DIGEST = "b" * 64
ARMS = (
    "production_eca_ahet_v1",
    "ahmed_phase_h3_figure_visible_unsuppressed",
    "ahmed_phase_h3_eq26_multiples_suppressed",
    "ahmed_phase_h3_prose_low_or_equal_suppressed",
    "ahmed_phase_h5_figure_visible_unsuppressed",
    "ahmed_phase_h5_eq26_multiples_suppressed",
    "ahmed_phase_h5_prose_low_or_equal_suppressed",
)


def _write_gate(root: Path, *, status="complete", gate_status="passed", eligible=True) -> Path:
    writer = BundleWriter(stage_root=root, stage="synthetic", run_id=new_run_id("c" * 12))
    writer.add_json("gate.json", {"gate_status": gate_status})
    bundle = writer.finalize(
        status=status,
        provenance={"source_manifest_sha256": SOURCE_DIGEST},
        promotion_eligible=eligible,
        extra_manifest={"gate_status": gate_status},
    )
    return bundle.root


def _write_authorization(path: Path, gate_dir: Path, **overrides) -> Path:
    _manifest, digest = read_manifest(gate_dir)
    payload = {
        "authorization_id": "real_evaluation_v1",
        "gate_manifest_sha256": digest,
        "source_manifest_sha256": SOURCE_DIGEST,
        "allowed_stages": list(("real-smoke", "real-radar", "score")),
        "capture_ids": ["m1"],
        "lock_estimands": list(LOCK_ESTIMANDS),
        "arm_ids": list(ARMS),
        "approved_by": "user",
        "approved_on": "2026-07-30",
    }
    payload.update(overrides)
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


@pytest.fixture
def gate_and_auth(tmp_path):
    gate_dir = _write_gate(tmp_path / "synthetic")
    auth = _write_authorization(tmp_path / "auth.yaml", gate_dir)
    return gate_dir, auth


# ── The gate must be complete, passed, AND promotion-eligible ────────────────

def test_a_good_gate_verifies(gate_and_auth):
    gate_dir, _auth = gate_and_auth
    assert verify_gate_bundle(gate_dir)["gate_status"] == "passed"


def test_a_failed_gate_is_refused(tmp_path):
    gate_dir = _write_gate(tmp_path / "s", status="failed", gate_status="failed")
    with pytest.raises(PreflightError, match="not complete"):
        verify_gate_bundle(gate_dir)


def test_a_complete_but_unpassed_gate_is_refused(tmp_path):
    gate_dir = _write_gate(tmp_path / "s", status="complete", gate_status="failed")
    with pytest.raises(PreflightError, match="not passed"):
        verify_gate_bundle(gate_dir)


def test_a_promotion_ineligible_gate_is_refused_even_though_complete(tmp_path):
    """Stricter than status==complete: a dirty-source gate is not reproducible."""
    gate_dir = _write_gate(tmp_path / "s", eligible=False)
    with pytest.raises(PreflightError, match="not promotion-eligible"):
        verify_gate_bundle(gate_dir)


def test_a_tampered_gate_is_refused(gate_and_auth):
    gate_dir, _auth = gate_and_auth
    (gate_dir / "gate.json").write_bytes(b'{"gate_status": "passed", "x": 1}\n')
    with pytest.raises(ValueError, match="sha256"):
        verify_gate_bundle(gate_dir)


def test_a_missing_gate_is_refused(tmp_path):
    with pytest.raises(PreflightError, match="no gate manifest"):
        verify_gate_bundle(tmp_path / "nonexistent")


def test_gate_source_identity_must_match_the_authorized_source(tmp_path):
    """Gate provenance, embedded source manifest, and authorization form one chain."""
    writer = BundleWriter(
        stage_root=tmp_path / "synthetic", stage="synthetic", run_id=new_run_id("e" * 12)
    )
    writer.add_json("gate.json", {"gate_status": "passed"})
    gate = writer.finalize(
        status="complete",
        provenance={"source_manifest_sha256": "a" * 64},
        promotion_eligible=True,
        extra_manifest={"gate_status": "passed"},
    )
    auth = _write_authorization(tmp_path / "auth.yaml", gate.root)
    with pytest.raises(PreflightError, match="source|provenance|gate"):
        verify_preflight(
            stage="real-smoke",
            gate_dir=gate.root,
            authorization_path=auth,
            source_manifest_sha256=SOURCE_DIGEST,
        )


# ── Authorization scope ──────────────────────────────────────────────────────

def test_preflight_accepts_an_authorized_stage(gate_and_auth):
    gate_dir, auth = gate_and_auth
    result = verify_preflight(
        stage="real-radar",
        gate_dir=gate_dir,
        authorization_path=auth,
        source_manifest_sha256=SOURCE_DIGEST,
    )
    assert result.authorization.authorization_id == "real_evaluation_v1"


def test_one_authorization_covers_the_whole_chain(gate_and_auth):
    """Per-stage authorization would permit outcome-adaptive stopping."""
    gate_dir, auth = gate_and_auth
    for stage in ("real-smoke", "real-radar", "score"):
        assert verify_preflight(
            stage=stage,
            gate_dir=gate_dir,
            authorization_path=auth,
            source_manifest_sha256=SOURCE_DIGEST,
        )


def test_an_unauthorized_stage_is_refused(tmp_path):
    gate_dir = _write_gate(tmp_path / "s")
    auth = _write_authorization(
        tmp_path / "auth.yaml", gate_dir, allowed_stages=["real-smoke"]
    )
    with pytest.raises(PreflightError, match="does not permit"):
        verify_preflight(
            stage="real-radar",
            gate_dir=gate_dir,
            authorization_path=auth,
            source_manifest_sha256=SOURCE_DIGEST,
        )


def test_an_authorization_bound_to_another_gate_is_refused(tmp_path):
    gate_dir = _write_gate(tmp_path / "s")
    auth = _write_authorization(
        tmp_path / "auth.yaml", gate_dir, gate_manifest_sha256="0" * 64
    )
    with pytest.raises(PreflightError, match="bound to a different gate"):
        verify_preflight(
            stage="real-radar",
            gate_dir=gate_dir,
            authorization_path=auth,
            source_manifest_sha256=SOURCE_DIGEST,
        )


def test_changed_scoped_sources_invalidate_the_authorization(gate_and_auth):
    gate_dir, auth = gate_and_auth
    with pytest.raises(PreflightError, match="scoped sources changed"):
        verify_preflight(
            stage="real-radar",
            gate_dir=gate_dir,
            authorization_path=auth,
            source_manifest_sha256="f" * 64,
        )


def test_a_malformed_authorization_is_refused(tmp_path):
    gate_dir = _write_gate(tmp_path / "s")
    path = tmp_path / "auth.yaml"
    path.write_text(yaml.safe_dump({"authorization_id": "x"}), encoding="utf-8")
    with pytest.raises(PreflightError, match="missing"):
        load_authorization(path)


def test_an_incomplete_parent_is_refused(tmp_path):
    gate_dir = _write_gate(tmp_path / "s")
    auth = _write_authorization(tmp_path / "auth.yaml", gate_dir)
    writer = BundleWriter(stage_root=tmp_path / "smoke", stage="smoke", run_id=new_run_id("d" * 12))
    writer.add_json("rows.json", {"rows": []})
    parent = writer.finalize(
        status="incomplete_abandoned", provenance={}, promotion_eligible=True
    )
    with pytest.raises(PreflightError, match="not complete"):
        verify_preflight(
            stage="real-radar",
            gate_dir=gate_dir,
            authorization_path=auth,
            source_manifest_sha256=SOURCE_DIGEST,
            parent_dir=parent.root,
        )


# ── Ordering: nothing may touch a capture before preflight passes ────────────

def test_rejected_preflight_touches_no_capture_path(tmp_path, monkeypatch):
    """Authorization checked after opening a capture has already leaked the data."""
    gate_dir = _write_gate(tmp_path / "s")
    auth = _write_authorization(
        tmp_path / "auth.yaml", gate_dir, allowed_stages=["real-smoke"]
    )

    touched: list[str] = []
    real_open, real_stat = builtins.open, os.stat

    def guarded_open(file, *args, **kwargs):
        name = str(file).lower()
        if "adc_stream" in name or "masimo" in name or "live_demo" in name:
            touched.append(str(file))
        return real_open(file, *args, **kwargs)

    def guarded_stat(path, *args, **kwargs):
        name = str(path).lower()
        if "adc_stream" in name or "masimo" in name or "live_demo" in name:
            touched.append(str(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(os, "stat", guarded_stat)

    with pytest.raises(PreflightError):
        verify_preflight(
            stage="real-radar",
            gate_dir=gate_dir,
            authorization_path=auth,
            source_manifest_sha256=SOURCE_DIGEST,
        )
    assert touched == [], f"preflight touched capture paths: {touched}"


def test_successful_preflight_also_touches_no_capture_path(gate_and_auth, monkeypatch):
    """Preflight reads only bundle and authorization files, never capture data."""
    gate_dir, auth = gate_and_auth
    touched: list[str] = []
    real_open = builtins.open

    def guarded(file, *args, **kwargs):
        if any(t in str(file).lower() for t in ("adc_stream", "masimo", "live_demo")):
            touched.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded)
    verify_preflight(
        stage="real-radar",
        gate_dir=gate_dir,
        authorization_path=auth,
        source_manifest_sha256=SOURCE_DIGEST,
    )
    assert touched == []


# ── Cartesian completeness ───────────────────────────────────────────────────

@pytest.fixture(scope="module")
def radar():
    return load_registry().radar_scope()


def test_full_scope_matches_the_pinned_1792_rows(radar):
    ledger = CartesianLedger.build(radar, radar.captures.keys(), LOCK_ESTIMANDS, ARMS)
    assert ledger.expected_count == 1792


def test_ahmed_only_scope_matches_the_pinned_1536_evidence_rows(radar):
    ahmed = tuple(a for a in ARMS if a.startswith("ahmed_"))
    ledger = CartesianLedger.build(radar, radar.captures.keys(), LOCK_ESTIMANDS, ahmed)
    assert ledger.expected_count == 1536


def test_a_complete_run_is_accepted(radar):
    ledger = CartesianLedger.build(radar, ["m1"], LOCK_ESTIMANDS, ARMS)
    for capture_id, lock, k, arm in sorted(ledger.expected):
        ledger.record(capture_id, lock, k, arm)
    assert ledger.assert_complete() == 6 * 2 * 7


def test_a_missing_row_is_fatal_not_a_smaller_denominator(radar):
    ledger = CartesianLedger.build(radar, ["m1"], LOCK_ESTIMANDS, ARMS)
    rows = sorted(ledger.expected)
    for capture_id, lock, k, arm in rows[:-1]:
        ledger.record(capture_id, lock, k, arm)
    with pytest.raises(ValueError, match="incomplete run"):
        ledger.assert_complete()


def test_a_duplicate_row_is_fatal(radar):
    ledger = CartesianLedger.build(radar, ["m1"], LOCK_ESTIMANDS, ARMS)
    for capture_id, lock, k, arm in sorted(ledger.expected):
        ledger.record(capture_id, lock, k, arm)
    first = sorted(ledger.expected)[0]
    ledger.record(*first)
    with pytest.raises(ValueError, match="duplicate rows"):
        ledger.assert_complete()


def test_an_out_of_scope_row_is_rejected_immediately(radar):
    ledger = CartesianLedger.build(radar, ["m1"], LOCK_ESTIMANDS, ARMS)
    with pytest.raises(ValueError, match="not in the authorized Cartesian scope"):
        ledger.record("m1", LOCK_ESTIMANDS[0], 999, ARMS[0])
    with pytest.raises(ValueError, match="not in the authorized Cartesian scope"):
        ledger.record("m7", LOCK_ESTIMANDS[0], 0, ARMS[0])


def test_both_lock_estimands_are_tracked_separately_even_when_numerically_equal(radar):
    """m3's recorded and rerun locks are both 26; they must remain distinct rows."""
    capture = radar.capture("m3")
    assert capture.recorded_lock == capture.rerun_lock == 26
    ledger = CartesianLedger.build(radar, ["m3"], LOCK_ESTIMANDS, ARMS[:1])
    assert ledger.expected_count == 20 * 2 * 1
