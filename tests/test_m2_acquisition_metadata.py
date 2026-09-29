"""Current capture admission versus immutable historical metadata loading."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent / "fixtures" / "m2"))

from builders import acquisition_metadata  # noqa: E402
from src import protocol  # noqa: E402
from src.m2.acquisition_metadata import (  # noqa: E402
    AcquisitionValidationPurpose,
    ContractError,
    PROTOCOL_DEVIATION_SETTLE_BELOW_120S,
    assess_protocol_compliance,
    validate_acquisition_metadata,
)


@pytest.mark.parametrize("arm", ["natural", "paced"])
def test_new_capture_rejects_settle_just_below_120_seconds(arm: str) -> None:
    metadata = acquisition_metadata(arm)
    metadata["settle_duration_s"] = 119.999
    with pytest.raises(
        ContractError, match=r"settle_duration_s must be >= 120\.0, got 119\.999"
    ):
        validate_acquisition_metadata(metadata)


@pytest.mark.parametrize("arm", ["natural", "paced"])
def test_new_capture_accepts_exactly_120_seconds(arm: str) -> None:
    metadata = acquisition_metadata(arm)
    metadata["settle_duration_s"] = 120.0
    assert validate_acquisition_metadata(metadata)["settle_duration_s"] == 120.0


@pytest.mark.parametrize("arm", ["natural", "paced"])
def test_historical_60_second_record_is_readable_but_noncompliant(arm: str) -> None:
    metadata = acquisition_metadata(arm)
    metadata["settle_duration_s"] = 60.0
    validated = validate_acquisition_metadata(
        metadata, purpose=AcquisitionValidationPurpose.HISTORICAL_RECORD
    )
    assert assess_protocol_compliance(validated) == (
        False,
        (PROTOCOL_DEVIATION_SETTLE_BELOW_120S,),
    )


def test_recovery_is_exempt_from_settle_duration() -> None:
    metadata = validate_acquisition_metadata(acquisition_metadata("recovery"))
    assert assess_protocol_compliance(metadata) == (True, ())


def test_new_capture_rejects_evidence_name_from_another_session() -> None:
    metadata = acquisition_metadata("natural")
    metadata["settle_evidence_path"] = "evidence/P001_natural_settle.json"
    with pytest.raises(ContractError, match="must be exactly.*T001_natural_settle"):
        validate_acquisition_metadata(metadata)


def test_historical_record_preserves_old_evidence_path_without_rewriting() -> None:
    metadata = acquisition_metadata("natural")
    metadata["settle_evidence_path"] = "legacy_settle.json"
    validated = validate_acquisition_metadata(
        metadata, purpose=AcquisitionValidationPurpose.HISTORICAL_RECORD
    )
    assert validated["settle_evidence_path"] == "legacy_settle.json"


def test_shared_protocol_values_cannot_drift_between_m2_and_m4() -> None:
    from src.m2 import acquisition_metadata as m2
    from src.m4 import manifest as m4

    assert m2.MIN_SETTLE_S == protocol.MIN_SETTLE_S == 120.0
    assert m2.SETTLE_EVIDENCE_WINDOW_S == protocol.SETTLE_EVIDENCE_WINDOW_S
    assert m2.SETTLE_SPREAD_MAX_BPM == m4.SETTLE_MAX_PR_SPREAD_BPM
    assert m2.SETTLE_DRIFT_MAX_BPM == m4.SETTLE_MAX_PR_DRIFT_BPM
    assert (m2.DISTANCE_MIN_M, m2.DISTANCE_MAX_M) == (
        m4.DISTANCE_MIN_M,
        m4.DISTANCE_MAX_M,
    )
