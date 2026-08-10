"""Fixed -1/0/+1 second alignment sensitivity, split by label-access stage."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from src.comparator import br_reference, hr_reference

from .acquisition_metadata import Arm
from .common import ContractError, require_sha256
from .label_firewall import (
    ReferenceOperation,
    ScoringAuthorization,
    require_scoring_authorization,
)

TIME_SHIFTS_S = (-1.0, 0.0, 1.0)
FRAMES_PER_WINDOW = 600
FRAME_RATE_HZ = 20.0


def _finite_or_none(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _hr_reason(result: Mapping[str, object]) -> str | None:
    if bool(result["admitted"]):
        return None
    if not bool(result["coverage_ok"]):
        return "insufficient_hr_usable_samples"
    return "hr_reference_not_stationary"


def _br_reason(result: Mapping[str, object]) -> str | None:
    if bool(result["admitted"]):
        return None
    if not bool(result["availability_ok"]):
        return "insufficient_br_finite_samples"
    return "br_reference_not_stationary"


def _recovery_stage1(medians: list[float]) -> dict[str, object]:
    n_values = len(medians)
    if n_values == 0:
        return {
            "n": 0,
            "range_bpm": None,
            "c_s_bpm": None,
            "within_5_bpm_hit_rate": None,
            "status": "fail_count",
        }
    value_range = float(max(medians) - min(medians))
    centre = float(np.median(np.asarray(medians, dtype=float)))
    hit_rate = float(np.mean(np.abs(np.asarray(medians) - centre) <= 5.0))
    if n_values < 10:
        status = "fail_count"
    elif value_range < 20.0:
        status = "fail_range"
    elif hit_rate >= 0.5:
        status = "fail_constant_predictor"
    else:
        status = "pass"
    return {
        "n": n_values,
        "range_bpm": value_range,
        "c_s_bpm": centre,
        "within_5_bpm_hit_rate": hit_rate,
        "status": status,
    }


def build_reference_only_sensitivity(
    reference: pd.DataFrame,
    *,
    subject_id: str,
    session_id: str,
    arm: Arm | str,
    primary_frame0_epoch: float,
    n_complete_windows: int,
    reference_sha256: str,
    comparator_config_sha256: str,
    authorization: ScoringAuthorization | None = None,
    registry_path: str | Path | None = None,
    audit_path: str | Path | None = None,
    reference_path: str | Path | None = None,
) -> dict[str, object]:
    """Build Stage-1-safe summaries.  No radar argument exists on this interface."""
    require_sha256(reference_sha256, "reference_sha256")
    require_sha256(comparator_config_sha256, "comparator_config_sha256")
    if subject_id.startswith("P") and authorization is None:
        raise ContractError(
            "prospective reference sensitivity requires a label-firewall capability"
        )
    try:
        parsed_arm = Arm(arm)
    except ValueError:
        raise ContractError(f"unknown arm {arm!r}") from None
    if authorization is not None:
        require_scoring_authorization(
            authorization,
            subject_id=subject_id,
            session_id=session_id,
            arm=parsed_arm.value,
            reference_path=reference_path,
            reference_sha256=reference_sha256,
            config_sha256=comparator_config_sha256,
            registry_path=registry_path,
            audit_path=audit_path,
            allowed_operations=frozenset(
                {
                    ReferenceOperation.RECOVERY_STAGE1,
                    ReferenceOperation.REFERENCE_TIME_SENSITIVITY,
                }
            ),
        )
    if type(n_complete_windows) is not int or n_complete_windows < 0:
        raise ContractError("n_complete_windows must be a non-negative exact integer")
    if type(primary_frame0_epoch) not in (int, float) or not math.isfinite(primary_frame0_epoch):
        raise ContractError("primary_frame0_epoch must be finite")

    shifts: list[dict[str, object]] = []
    for shift_s in TIME_SHIFTS_S:
        shifted_origin = float(primary_frame0_epoch) + shift_s
        windows: list[dict[str, object]] = []
        admitted_recovery_medians: list[float] = []
        for window_index in range(n_complete_windows):
            epoch_start = shifted_origin + window_index * FRAMES_PER_WINDOW / FRAME_RATE_HZ
            epoch_stop = epoch_start + FRAMES_PER_WINDOW / FRAME_RATE_HZ
            hr = hr_reference(reference, epoch_start, epoch_stop)
            br = br_reference(reference, epoch_start, epoch_stop)
            median_pr = _finite_or_none(hr["median_pr_bpm"])
            if bool(hr["admitted"]) and median_pr is not None:
                admitted_recovery_medians.append(median_pr)
            windows.append(
                {
                    "window_index": window_index,
                    "epoch_start": epoch_start,
                    "epoch_stop": epoch_stop,
                    "hr_admitted": bool(hr["admitted"]),
                    "hr_reason": _hr_reason(hr),
                    "hr_n_total": int(hr["n_total"]),
                    "hr_n_finite_pr": int(hr["n_finite_pr"]),
                    "hr_n_pi_qualified": int(hr["n_pi_qualified"]),
                    "hr_n_usable": int(hr["n_usable"]),
                    "median_pr_bpm": median_pr,
                    "br_admitted": bool(br["admitted"]),
                    "br_reason": _br_reason(br),
                    "br_n_total": int(br["n_total"]),
                    "br_n_finite_rr": int(br["n_finite_rr"]),
                    "median_rr_bpm": _finite_or_none(br["median_rr_bpm"]),
                }
            )
        shifts.append(
            {
                "shift_s": shift_s,
                "primary_shift_s": 0.0,
                "shifted_frame0_epoch": shifted_origin,
                "windows": windows,
                "totals": {
                    "complete_windows": n_complete_windows,
                    "hr_admitted": sum(bool(row["hr_admitted"]) for row in windows),
                    "br_admitted": sum(bool(row["br_admitted"]) for row in windows),
                },
                "recovery_stage1": (
                    _recovery_stage1(admitted_recovery_medians)
                    if parsed_arm is Arm.RECOVERY
                    else None
                ),
            }
        )
    artifact = {
        "schema": "m2_reference_time_sensitivity_v1",
        "artifact_kind": "reference_only_sensitivity",
        "subject_id": subject_id,
        "session_id": session_id,
        "arm": parsed_arm.value,
        "reference_sha256": reference_sha256,
        "comparator_config_sha256": comparator_config_sha256,
        "primary_frame0_epoch": float(primary_frame0_epoch),
        "fixed_shifts_s": list(TIME_SHIFTS_S),
        "shifts": shifts,
    }
    if authorization is not None:
        artifact["data_role"] = authorization.data_role
        artifact["reference_path"] = authorization.reference_path
    return artifact


def build_agreement_sensitivity(
    reference_artifact: Mapping[str, object],
    radar_rows_by_shift: Mapping[float, Iterable[Mapping[str, object]]],
    *,
    authorization: ScoringAuthorization,
    scorer_config_sha256: str,
    registry_path: str | Path | None = None,
    audit_path: str | Path | None = None,
    reference_path: str | Path | None = None,
) -> dict[str, object]:
    """Compute the later fixed sensitivity after the label firewall authorizes scoring."""
    if reference_artifact.get("schema") != "m2_reference_time_sensitivity_v1":
        raise ContractError("reference_artifact has the wrong schema")
    subject_id = str(reference_artifact.get("subject_id"))
    session_id = str(reference_artifact.get("session_id"))
    arm = str(reference_artifact.get("arm"))
    reference_sha256 = require_sha256(
        reference_artifact.get("reference_sha256"), "reference_artifact.reference_sha256"
    )
    comparator_config_sha256 = require_sha256(
        reference_artifact.get("comparator_config_sha256"),
        "reference_artifact.comparator_config_sha256",
    )
    require_sha256(scorer_config_sha256, "scorer_config_sha256")
    artifact_role = reference_artifact.get("data_role")
    if subject_id.startswith("P"):
        if artifact_role not in {
            "representation_validation",
            "final_evaluation",
        }:
            raise ContractError("prospective reference artifact must bind its data role")
        if artifact_role != authorization.data_role:
            raise ContractError("reference artifact role binding disagrees with authorization")
        artifact_reference_path = reference_artifact.get("reference_path")
        artifact_reference = (
            Path(str(artifact_reference_path)).resolve()
            if artifact_reference_path is not None
            else None
        )
        if artifact_reference is not None and artifact_reference != Path(
            authorization.reference_path
        ):
            raise ContractError("reference artifact path binding disagrees with authorization")
    require_scoring_authorization(
        authorization,
        subject_id=subject_id,
        session_id=session_id,
        arm=arm,
        reference_path=reference_path,
        reference_sha256=reference_sha256,
        config_sha256=comparator_config_sha256,
        scorer_sha256=scorer_config_sha256,
        registry_path=registry_path,
        audit_path=audit_path,
    )
    if set(radar_rows_by_shift) != set(TIME_SHIFTS_S):
        raise ContractError("radar_rows_by_shift must contain exactly -1.0, 0.0, +1.0")
    reference_shifts = {
        float(entry["shift_s"]): entry for entry in reference_artifact.get("shifts", [])
    }
    if set(reference_shifts) != set(TIME_SHIFTS_S):
        raise ContractError("reference artifact does not contain the fixed three shifts")

    frozen_radar_ledger = list(radar_rows_by_shift[0.0])
    for shift_s in TIME_SHIFTS_S:
        if list(radar_rows_by_shift[shift_s]) != frozen_radar_ledger:
            raise ContractError(
                "all shifts must reuse one identical frozen radar ledger"
            )

    expected_window_indices: set[int] | None = None
    shift_outputs: list[dict[str, object]] = []
    for shift_s in TIME_SHIFTS_S:
        reference_window_rows = list(reference_shifts[shift_s]["windows"])
        reference_windows: dict[int, Mapping[str, object]] = {}
        for row in reference_window_rows:
            window_index = row.get("window_index")
            if type(window_index) is not int or window_index < 0:
                raise ContractError("reference ledger contains an invalid window identity")
            if window_index in reference_windows:
                raise ContractError("reference ledger contains a duplicate window identity")
            reference_windows[window_index] = row
        shift_window_indices = set(reference_windows)
        if expected_window_indices is None:
            expected_window_indices = shift_window_indices
        elif shift_window_indices != expected_window_indices:
            raise ContractError(
                "reference shift ledgers must contain the same frozen window identities"
            )

        radar_rows = list(frozen_radar_ledger)
        radar_rows_by_index: dict[int, Mapping[str, object]] = {}
        for radar_row in radar_rows:
            window_index = radar_row.get("window_index", radar_row.get("k"))
            if type(window_index) is not int or window_index not in reference_windows:
                raise ContractError(f"radar ledger has unknown window identity {window_index!r}")
            if window_index in radar_rows_by_index:
                raise ContractError(
                    f"radar ledger contains duplicate window identity {window_index}"
                )
            radar_rows_by_index[window_index] = radar_row
        if set(radar_rows_by_index) != shift_window_indices:
            missing = sorted(shift_window_indices - set(radar_rows_by_index))
            raise ContractError(
                "radar ledger must contain exactly one row per frozen reference window; "
                f"missing identities: {missing}"
            )

        differences: list[float] = []
        severe_count = 0
        radar_valid_count = 0
        for window_index in sorted(shift_window_indices):
            radar_row = radar_rows_by_index[window_index]
            estimate = _finite_or_none(radar_row.get("hr_bpm"))
            if bool(radar_row.get("radar_valid")) and estimate is not None:
                radar_valid_count += 1
            reference_row = reference_windows[window_index]
            reference_bpm = _finite_or_none(reference_row["median_pr_bpm"])
            if bool(reference_row["hr_admitted"]) and estimate is not None and bool(
                radar_row.get("radar_valid")
            ):
                difference = estimate - reference_bpm
                differences.append(difference)
                severe_count += int(abs(difference) > 5.0)
        joint_count = len(differences)
        reference_admitted = sum(
            bool(row["hr_admitted"]) for row in reference_windows.values()
        )
        differences_array = np.asarray(differences, dtype=float)
        shift_outputs.append(
            {
                "shift_s": shift_s,
                "primary_shift_s": 0.0,
                "shifted_frame0_epoch": reference_shifts[shift_s][
                    "shifted_frame0_epoch"
                ],
                "window_ledger": [
                    {
                        "window_index": int(row["window_index"]),
                        "epoch_start": float(row["epoch_start"]),
                        "epoch_stop": float(row["epoch_stop"]),
                    }
                    for row in reference_shifts[shift_s]["windows"]
                ],
                "joint_count": joint_count,
                "radar_valid_count": radar_valid_count,
                "reference_admitted_count": reference_admitted,
                "coverage": joint_count / reference_admitted if reference_admitted else None,
                "mae_bpm": float(np.mean(np.abs(differences_array))) if joint_count else None,
                "rmse_bpm": float(np.sqrt(np.mean(differences_array**2))) if joint_count else None,
                "bias_bpm": float(np.mean(differences_array)) if joint_count else None,
                "hr_severe_error_count": severe_count,
                "arm_specific_loa": None,
                "arm_specific_loa_status": "not_executable_from_one_session",
            }
        )
    return {
        "schema": "m2_agreement_time_sensitivity_v1",
        "artifact_kind": "later_agreement_sensitivity",
        "subject_id": subject_id,
        "session_id": session_id,
        "arm": arm,
        "data_role": authorization.data_role,
        "scoring_operation": authorization.operation.value,
        "reference_sha256": reference_sha256,
        "comparator_config_sha256": comparator_config_sha256,
        "scorer_config_sha256": scorer_config_sha256,
        "fixed_shifts_s": list(TIME_SHIFTS_S),
        "shifts": shift_outputs,
    }
