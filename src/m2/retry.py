"""Exact agreement-blind predicates for a same-subject prospective session retry."""
from __future__ import annotations

from enum import Enum
from typing import Mapping

from .common import ContractError, require_bool, require_nonempty_string, require_number


class RetryReason(str, Enum):
    PROTOCOL_ABORT = "protocol_abort"
    CORRUPT_RAW = "corrupt_raw"
    EPOCH_SYNC_FAILURE = "epoch_sync_failure"


_IDENTITY_FIELDS = (
    "subject_id",
    "data_role",
    "cohort_slot",
    "arm",
    "commanded_rate_bpm",
    "protocol_identity_sha256",
    "capture_config_sha256",
)


def validate_retry_pair(
    predecessor: Mapping[str, object], replacement: Mapping[str, object]
) -> RetryReason:
    """Validate one predecessor/replacement edge; no yield/outcome cause is accepted."""
    predecessor_id = require_nonempty_string(predecessor, "session_id")
    replacement_id = require_nonempty_string(replacement, "session_id")
    if predecessor.get("replaced_by_session_id") != replacement_id:
        raise ContractError("predecessor does not point symmetrically to replacement")
    if replacement.get("replaces_session_id") != predecessor_id:
        raise ContractError("replacement does not point symmetrically to predecessor")
    if predecessor.get("label_state") != "sealed" or replacement.get("label_state") != "sealed":
        raise ContractError("retry requires label_state == 'sealed' on both attempts")
    for key in _IDENTITY_FIELDS:
        if predecessor.get(key) != replacement.get(key):
            raise ContractError(f"retry changed immutable same-subject protocol identity field {key}")
    try:
        reason = RetryReason(replacement.get("retry_reason"))
    except ValueError:
        raise ContractError(
            "retry_reason must be protocol_abort, corrupt_raw, or epoch_sync_failure"
        ) from None
    if predecessor.get("retry_reason") != reason.value:
        raise ContractError("both ends of a retry edge must record the same retry_reason")

    evidence = predecessor.get("retry_evidence")
    if type(evidence) is not dict:
        raise ContractError("retry predecessor must contain objective retry_evidence")
    if reason is RetryReason.PROTOCOL_ABORT:
        settle_failed = require_bool(evidence, "settle_gate_failed_before_capture")
        intentional_early_stop = require_bool(evidence, "intentional_early_stop")
        arm = predecessor.get("arm")
        if arm == "recovery" and settle_failed:
            raise ContractError(
                "recovery retries cannot use the normal/paced stable-settle failure predicate"
            )
        if not (settle_failed or intentional_early_stop):
            raise ContractError("protocol_abort retry lacks a permitted objective predicate")
        if intentional_early_stop:
            actual = require_number(evidence, "actual_duration_s", minimum=0.0)
            intended = require_number(evidence, "intended_duration_s", minimum=0.0)
            if actual >= intended:
                raise ContractError("intentional early-stop retry did not end before intended duration")
    elif reason is RetryReason.CORRUPT_RAW:
        checksum_failed = require_bool(evidence, "stored_raw_checksum_failed")
        nonfinal_cut = require_bool(evidence, "raw_truncation_cut_nonfinal_window")
        if not (checksum_failed or nonfinal_cut):
            raise ContractError("corrupt_raw retry lacks checksum/non-final-window evidence")
    else:
        start_offset = require_number(evidence, "clock_offset_start_s")
        end_offset = require_number(evidence, "clock_offset_end_s")
        if abs(start_offset) <= 1.0 and abs(end_offset) <= 1.0:
            raise ContractError("epoch_sync_failure retry requires an offset strictly above 1 second")
    return reason
