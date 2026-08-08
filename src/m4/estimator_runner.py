"""Radar-only stage runner: preflight, dispatch, and Cartesian completeness.

Plan section 4.2. Two properties matter more than anything else here.

**Preflight happens before any capture path is touched.** Not before decoding — before the
first `stat` or `open`. Checking authorization after opening a file would already have
leaked the thing the authorization exists to control, and would let an operator learn
something about the data before deciding whether they were allowed to. `run_radar_stage`
therefore validates the gate, the authorization, and the parent manifest first, and the
tests assert that a rejected preflight performs **zero** filesystem access on any capture
path.

**Every eligible Cartesian tuple must produce exactly one row.** A missing row is a fatal
incomplete run, not a quietly smaller denominator; a duplicate row would double-count. The
ledger asserts this rather than trusting the loop.

This module is handed only the registry's radar scope, so it structurally cannot reach a
Masimo path — see `src/m4/capture_registry.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import subprocess
from typing import Callable, Mapping, Sequence

import numpy as np
import yaml

from src.m4.bundle import BundleWriter, StageBundle, read_manifest, sha256_path, verify_bundle
from src.m4.capture_registry import RadarCapture, RadarScope
from src.m4.estimator_suite import (
    EstimatorArmSpec,
    SuiteWindowResult,
    WindowEstimatorSuite,
    validate_returned_arms,
)
from src.m4.evidence_serialization import (
    EVIDENCE_COMPUTED,
    EVIDENCE_NOT_COMPUTED_BREATHING_UNAVAILABLE,
    array_sha256,
    pack_ahmed_evidence,
    pack_shared_evidence,
    serialize_native_tree,
)
from src.m4.outcome import (
    AHET_MAX_CANDIDATES,
    REJECTION_CODE_NOT_ATTEMPTED,
    RESP_GATE_HI_HZ,
    RESP_GATE_LO_HZ,
)
from src.m4.production_suite import PRODUCTION_ARM_ID
from src.m4.window_grid import FRAME_RATE_HZ, FRAMES_PER_WINDOW, window_frame_span
from src.m8.ahmed_transfer import (
    APPROVED_ARM_IDS,
    FREQUENCY_MAPPING_ID,
    LAYER_B_NORMALIZATION,
    PHASE_EXTRACTION_METHOD,
    RATE_MAPPING_ID,
    SCORE_FORMULA_ID,
    SCORE_FUNCTIONAL,
    SUPPORT_RULE_ID,
    phase_signal_hash,
)
from src.m8.ahmed_provenance import validate_test_attestation
from src.radar_io import ChirpConfig, read_adc_bin
from src.warmup_select import derive_candidate_bins, run_warmup_selection
from src.window_pipeline import run_config_hash

__all__ = [
    "Authorization",
    "CartesianLedger",
    "CANONICAL_ARM_IDS",
    "PairedRadarRun",
    "PreflightError",
    "RunnerContractError",
    "SelectorContractError",
    "execute_paired_runner",
    "load_authorization",
    "persist_radar_artifacts",
    "run_radar_stage",
    "verify_repository_authorization",
    "verify_gate_bundle",
    "verify_preflight",
]

#: Lock estimands are reported separately and never pooled, even when numerically equal.
LOCK_ESTIMANDS = ("recorded_lock_as_captured", "current_production_rerun_lock")
CANONICAL_ARM_IDS = (PRODUCTION_ARM_ID, *APPROVED_ARM_IDS)

REAL_STAGES = ("real-smoke", "real-radar", "score")

# The addendum freezes four prediction families on two declared sampling grids,
# domains, and harmonic counts.  These are the 14 concrete checks emitted by the
# synthetic evaluator; accepting merely a non-empty subset would let an incomplete
# gate authorize real-data access.
_REQUIRED_GATE_PREDICTION_IDS = (
    "P1_P4_selection_fs37.4199_collision_domain_from_fb_h3",
    "P1_P4_selection_fs37.4199_collision_domain_from_fb_h5",
    "P1_P4_selection_fs37.4199_real_representative_domain_h3",
    "P1_P4_selection_fs37.4199_real_representative_domain_h5",
    "P1_P4_selection_fs20_collision_domain_from_fb_h3",
    "P2_subharmonic_degeneracy_h3",
    "P3_collision_ratio_h3",
    "P3_non_divisor_carries_no_signal_h3",
    "P1_P4_selection_fs20_collision_domain_from_fb_h5",
    "P2_subharmonic_degeneracy_h5",
    "P3_collision_ratio_h5",
    "P3_non_divisor_carries_no_signal_h5",
    "P1_P4_selection_fs20_real_representative_domain_h3",
    "P1_P4_selection_fs20_real_representative_domain_h5",
)


class PreflightError(RuntimeError):
    """Raised before any capture access when a precondition is not satisfied."""


class RunnerContractError(RuntimeError):
    """Unexpected decode, schema, DSP, mutation, or serialization failure."""


class SelectorContractError(RunnerContractError):
    """A warmup selector used a fallback, hid a candidate error, or changed its lock."""

    def __init__(self, message: str, evidence: Mapping[str, object]):
        super().__init__(message)
        self.evidence = evidence


@dataclass(frozen=True)
class Authorization:
    """One frozen, comprehensive real-evaluation authorization (plan section 5.1).

    Deliberately covers the whole `real-smoke -> real-radar -> score` chain at once. If
    each stage were authorized separately, an operator could look at the smoke output and
    then decide whether to continue — outcome-adaptive stopping. Abandoning the chain must
    be an explicit, recorded act instead.
    """

    authorization_id: str
    gate_manifest_sha256: str
    source_manifest_sha256: str
    allowed_stages: tuple[str, ...]
    capture_ids: tuple[str, ...]
    lock_estimands: tuple[str, ...]
    arm_ids: tuple[str, ...]
    approved_by: str
    approved_on: str
    continuation_rationale_sha256: str | None = None

    def permits(self, stage: str) -> bool:
        return stage in self.allowed_stages


def load_authorization(path: Path) -> Authorization:
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise PreflightError(f"{path}: authorization must be a mapping")
    missing = [
        key
        for key in (
            "authorization_id",
            "gate_manifest_sha256",
            "source_manifest_sha256",
            "allowed_stages",
            "capture_ids",
            "lock_estimands",
            "arm_ids",
            "approved_by",
            "approved_on",
        )
        if key not in document
    ]
    if missing:
        raise PreflightError(f"{path}: authorization missing {missing}")
    allowed_keys = {
        "authorization_id",
        "gate_manifest_sha256",
        "source_manifest_sha256",
        "allowed_stages",
        "capture_ids",
        "lock_estimands",
        "arm_ids",
        "approved_by",
        "approved_on",
        "continuation_rationale_sha256",
    }
    extra = sorted(set(document) - allowed_keys)
    if extra:
        raise PreflightError(f"{path}: authorization has unsupported fields {extra}")
    for key in ("allowed_stages", "capture_ids", "lock_estimands", "arm_ids"):
        value = document[key]
        if type(value) is not list or not value or any(type(item) is not str for item in value):
            raise PreflightError(f"{path}: authorization {key} must be a non-empty string list")
        if len(set(value)) != len(value):
            raise PreflightError(f"{path}: authorization {key} contains duplicates")
    for key in ("gate_manifest_sha256", "source_manifest_sha256"):
        value = document[key]
        if type(value) is not str or len(value) != 64:
            raise PreflightError(f"{path}: authorization {key} must be a 64-character digest")
    for key in ("authorization_id", "approved_by", "approved_on"):
        if type(document[key]) is not str or not document[key]:
            raise PreflightError(f"{path}: authorization {key} must be a non-empty string")
    return Authorization(
        authorization_id=str(document["authorization_id"]),
        gate_manifest_sha256=str(document["gate_manifest_sha256"]),
        source_manifest_sha256=str(document["source_manifest_sha256"]),
        allowed_stages=tuple(document["allowed_stages"]),
        capture_ids=tuple(document["capture_ids"]),
        lock_estimands=tuple(document["lock_estimands"]),
        arm_ids=tuple(document["arm_ids"]),
        approved_by=str(document["approved_by"]),
        approved_on=str(document["approved_on"]),
        continuation_rationale_sha256=(
            None
            if document.get("continuation_rationale_sha256") is None
            else str(document["continuation_rationale_sha256"])
        ),
    )


def _load_strict_json_mapping(path: Path, label: str) -> dict:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"non-finite token {token}")
            ),
        )
    except Exception as exc:
        raise PreflightError(f"{label} is malformed: {path}") from exc
    if not isinstance(value, dict):
        raise PreflightError(f"{label} must be a JSON mapping: {path}")
    return value


def verify_gate_bundle(
    gate_dir: Path,
    *,
    expected_source_manifest_sha256: str | None = None,
    require_scientific_payloads: bool = False,
) -> dict:
    """A gate may parent a real stage only if complete, passed, and promotion-eligible.

    `status == "complete"` is not sufficient: a complete bundle built from dirty or
    untracked scoped sources is useful draft evidence but is not reproducible, so it can
    never parent real data (plan section 5.1).
    """
    gate_dir = Path(gate_dir)
    if not (gate_dir / "manifest.json").is_file():
        raise PreflightError(f"no gate manifest at {gate_dir}")
    manifest = verify_bundle(gate_dir)
    if manifest.get("stage") != "synthetic":
        raise PreflightError(f"{gate_dir} is stage {manifest.get('stage')!r}, not synthetic")
    if manifest.get("status") != "complete":
        raise PreflightError(f"gate status is {manifest.get('status')!r}, not complete")
    if manifest.get("gate_status") != "passed":
        raise PreflightError(f"gate verdict is {manifest.get('gate_status')!r}, not passed")
    if not manifest.get("promotion_eligible"):
        raise PreflightError(
            "gate is not promotion-eligible: its scoped sources were dirty or untracked, "
            "so it is draft evidence and cannot parent a real-data stage"
        )
    provenance = _load_strict_json_mapping(gate_dir / "provenance.json", "gate provenance")
    provenance_source = provenance.get("source_manifest_sha256")
    if type(provenance_source) is not str:
        raise PreflightError("gate provenance has no source_manifest_sha256")
    if (
        expected_source_manifest_sha256 is not None
        and provenance_source != expected_source_manifest_sha256
    ):
        raise PreflightError(
            "gate provenance source identity does not match the current/authorized source manifest"
        )

    if require_scientific_payloads:
        required_payloads = {
            "gate.json",
            "source_manifest.json",
            "test_attestation.json",
            "environment_attestation.json",
            "conda_explicit.txt",
            "resolved_config.yaml",
            "metrics.json",
            "evidence.npz",
        }
        missing = sorted(required_payloads - set(manifest["payloads"]))
        if missing:
            raise PreflightError(f"synthetic gate is missing scientific payloads {missing}")

    source_document = None
    if "source_manifest.json" in manifest["payloads"]:
        source_document = _load_strict_json_mapping(
            gate_dir / "source_manifest.json", "gate source manifest"
        )
        embedded_source = source_document.get("manifest_sha256")
        if type(embedded_source) is not str or embedded_source != provenance_source:
            raise PreflightError(
                "gate source_manifest identity does not match gate provenance"
            )

    if require_scientific_payloads:
        test_attestation = _load_strict_json_mapping(
            gate_dir / "test_attestation.json", "gate test attestation"
        )
        try:
            validate_test_attestation(test_attestation, source_document)
        except Exception as exc:
            raise PreflightError(f"gate test attestation is invalid: {exc}") from exc
        attestation_sha256 = manifest["payloads"]["test_attestation.json"]["sha256"]
        if (
            manifest.get("test_attestation_sha256") != attestation_sha256
            or provenance.get("test_attestation_sha256") != attestation_sha256
        ):
            raise PreflightError("gate test attestation is not hash-bound by manifest/provenance")

        conda_explicit_sha256 = manifest["payloads"]["conda_explicit.txt"]["sha256"]
        environment_attestation_sha256 = manifest["payloads"][
            "environment_attestation.json"
        ]["sha256"]
        environment_document = _load_strict_json_mapping(
            gate_dir / "environment_attestation.json", "gate environment attestation"
        )
        if environment_document.get("conda_explicit_sha256") != conda_explicit_sha256:
            raise PreflightError(
                "gate environment attestation does not bind conda_explicit.txt"
            )
        if provenance.get("conda_explicit_sha256") != conda_explicit_sha256:
            raise PreflightError("gate provenance does not bind conda_explicit.txt")
        if (
            provenance.get("environment_attestation_sha256")
            != environment_attestation_sha256
        ):
            raise PreflightError(
                "gate provenance does not bind environment_attestation.json"
            )

        gate_document = _load_strict_json_mapping(gate_dir / "gate.json", "gate verdict")
        if gate_document.get("gate_status") != "passed":
            raise PreflightError("gate.json verdict is not passed")
        checks = gate_document.get("checks")
        if not isinstance(checks, list) or not checks:
            raise PreflightError("gate.json has no complete scientific checks")
        prediction_ids: list[str] = []
        for check in checks:
            if not isinstance(check, Mapping):
                raise PreflightError("gate.json contains a malformed scientific check")
            prediction_id = check.get("prediction_id")
            if type(prediction_id) is not str or type(check.get("passed")) is not bool:
                raise PreflightError("gate.json contains an incomplete scientific check")
            if not check["passed"]:
                raise PreflightError(f"gate scientific check {prediction_id!r} did not pass")
            prediction_ids.append(prediction_id)
        if len(set(prediction_ids)) != len(prediction_ids):
            raise PreflightError("gate.json contains duplicate scientific checks")
        if tuple(prediction_ids) != _REQUIRED_GATE_PREDICTION_IDS:
            raise PreflightError(
                "gate.json does not contain the exact ordered P1-P4 scientific checks"
            )
        authority = gate_document.get("authority")
        if not isinstance(authority, Mapping):
            raise PreflightError("gate.json is missing its scientific authority record")
        if tuple(authority.get("lock_ids", ())) != LOCK_ESTIMANDS:
            raise PreflightError("gate scientific authority does not contain both frozen locks")
        if authority.get("production_arm_id") != PRODUCTION_ARM_ID:
            raise PreflightError("gate scientific authority does not contain the production arm")
        if tuple(authority.get("ahmed_arm_ids", ())) != APPROVED_ARM_IDS:
            raise PreflightError("gate scientific authority does not contain the six Ahmed arms")
        if tuple(authority.get("harmonic_counts", ())) != (3, 5):
            raise PreflightError("gate scientific authority does not contain H=3 and H=5")
        frozen_layer_b_identity = {
            "frequency_mapping_id": FREQUENCY_MAPPING_ID,
            "rate_mapping_id": RATE_MAPPING_ID,
            "normalization": LAYER_B_NORMALIZATION,
            "phase_extraction_method": PHASE_EXTRACTION_METHOD,
        }
        for field, expected in frozen_layer_b_identity.items():
            if authority.get(field) != expected:
                raise PreflightError(
                    f"gate scientific authority {field} does not match the frozen Layer B identity"
                )

        # The exact arm IDs plus the verified source manifest already bind these global
        # score definitions.  Repository-generated gates repeat them for direct audit;
        # when repeated, they must agree rather than becoming a second authority.
        optional_score_identity = {
            "score_functional": SCORE_FUNCTIONAL,
            "score_formula_id": SCORE_FORMULA_ID,
            "support_rule": SUPPORT_RULE_ID,
        }
        for field, expected in optional_score_identity.items():
            if field in authority and authority[field] != expected:
                raise PreflightError(
                    f"gate scientific authority {field} does not match the frozen Layer B identity"
                )
    return manifest


@dataclass(frozen=True)
class PreflightResult:
    gate_manifest: Mapping[str, object]
    gate_manifest_sha256: str
    authorization: Authorization
    parent_manifest_sha256: str | None


def verify_preflight(
    *,
    stage: str,
    gate_dir: Path,
    authorization_path: Path,
    source_manifest_sha256: str,
    parent_dir: Path | None = None,
    authorization_validator: Callable[[Path, Authorization], None] | None = None,
    require_scientific_gate: bool = False,
) -> PreflightResult:
    """Validate everything required before the first capture path is touched.

    Ordering is the point. Every check here reads only bundle and authorization files,
    never a capture.
    """
    if stage not in REAL_STAGES:
        raise PreflightError(f"unknown real stage {stage!r}; expected one of {REAL_STAGES}")

    manifest = verify_gate_bundle(
        gate_dir,
        require_scientific_payloads=require_scientific_gate,
    )
    _manifest, gate_digest = read_manifest(Path(gate_dir))

    authorization = load_authorization(authorization_path)
    if authorization_validator is not None:
        try:
            authorization_validator(Path(authorization_path), authorization)
        except PreflightError:
            raise
        except Exception as exc:
            raise PreflightError("authorization trust validation failed") from exc
    if not authorization.permits(stage):
        raise PreflightError(
            f"authorization {authorization.authorization_id!r} does not permit {stage!r}; "
            f"it allows {list(authorization.allowed_stages)}"
        )
    if authorization.gate_manifest_sha256 != gate_digest:
        raise PreflightError(
            "authorization is bound to a different gate: expected "
            f"{authorization.gate_manifest_sha256}, found {gate_digest}"
        )
    if authorization.source_manifest_sha256 != source_manifest_sha256:
        raise PreflightError(
            "scoped sources changed since the authorization was frozen: expected "
            f"{authorization.source_manifest_sha256}, found {source_manifest_sha256}"
        )
    provenance = _load_strict_json_mapping(
        Path(gate_dir) / "provenance.json", "gate provenance"
    )
    if provenance.get("source_manifest_sha256") != source_manifest_sha256:
        raise PreflightError(
            "gate provenance source identity does not match the authorized source manifest"
        )

    parent_digest = None
    if parent_dir is not None:
        parent_manifest = verify_bundle(Path(parent_dir))
        if parent_manifest.get("status") != "complete":
            raise PreflightError(
                f"parent stage {parent_manifest.get('stage')!r} is "
                f"{parent_manifest.get('status')!r}, not complete"
            )
        _pm, parent_digest = read_manifest(Path(parent_dir))
        parent_gate = (parent_manifest.get("parents") or {}).get("synthetic")
        if parent_gate != gate_digest:
            raise PreflightError(
                "parent synthetic gate identity does not match the authorized/current gate"
            )

    return PreflightResult(
        gate_manifest=manifest,
        gate_manifest_sha256=gate_digest,
        authorization=authorization,
        parent_manifest_sha256=parent_digest,
    )


def verify_repository_authorization(
    path: Path,
    authorization: Authorization,
    *,
    repository_root: Path | None = None,
) -> None:
    """Official-path trust check; portable tests inject or omit this policy seam."""
    repository = Path(repository_root or Path(__file__).resolve().parents[2]).resolve()
    approved_root = (
        repository / "experiments" / "m8_ahmed_transfer" / "authorizations"
    ).resolve()
    resolved = Path(path).resolve()
    if resolved.parent != approved_root:
        raise PreflightError(
            "authorization must be the approved repository authorization YAML"
        )
    candidates = sorted((*approved_root.glob("*.yaml"), *approved_root.glob("*.yml")))
    if candidates != [resolved]:
        raise PreflightError("exactly one approved real-evaluation authorization is required")
    if authorization.allowed_stages != REAL_STAGES:
        raise PreflightError("approved authorization must bind real-smoke, real-radar, and score")
    if authorization.lock_estimands != LOCK_ESTIMANDS:
        raise PreflightError("approved authorization does not bind the canonical locks")
    if authorization.arm_ids != CANONICAL_ARM_IDS:
        raise PreflightError("approved authorization does not bind the canonical arms")
    for name, digest in (
        ("gate_manifest_sha256", authorization.gate_manifest_sha256),
        ("source_manifest_sha256", authorization.source_manifest_sha256),
        (
            "continuation_rationale_sha256",
            authorization.continuation_rationale_sha256,
        ),
    ):
        if digest is None:
            continue
        if len(digest) != 64:
            raise PreflightError(f"approved authorization {name} is not a SHA-256")
        if any(char not in "0123456789abcdef" for char in digest):
            raise PreflightError(f"approved authorization {name} is not a lowercase SHA-256")
    relative = resolved.relative_to(repository).as_posix()
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", relative],
        cwd=repository,
        capture_output=True,
        text=True,
    )
    if tracked.returncode != 0:
        raise PreflightError("approved authorization is not committed")
    dirty = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", relative],
        cwd=repository,
        capture_output=True,
        text=True,
    )
    if dirty.returncode != 0 or dirty.stdout.strip():
        raise PreflightError("approved authorization is dirty or untracked")


@dataclass
class CartesianLedger:
    """Tracks that every eligible (capture, lock, k, arm) tuple yields exactly one row."""

    expected: set[tuple[str, str, int, str]] = field(default_factory=set)
    seen: dict[tuple[str, str, int, str], int] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        radar: RadarScope,
        capture_ids: Sequence[str],
        lock_estimands: Sequence[str],
        arm_ids: Sequence[str],
    ) -> "CartesianLedger":
        expected = {
            (capture_id, lock, k, arm)
            for capture_id in capture_ids
            for lock in lock_estimands
            for k in range(radar.capture(capture_id).windows)
            for arm in arm_ids
        }
        return cls(expected=expected)

    def record(self, capture_id: str, lock: str, k: int, arm_id: str) -> None:
        key = (capture_id, lock, k, arm_id)
        if key not in self.expected:
            raise ValueError(f"unexpected row {key}: not in the authorized Cartesian scope")
        self.seen[key] = self.seen.get(key, 0) + 1

    def assert_complete(self) -> int:
        duplicates = sorted(k for k, n in self.seen.items() if n > 1)
        if duplicates:
            raise ValueError(f"duplicate rows emitted: {duplicates[:5]} ({len(duplicates)})")
        missing = sorted(self.expected - set(self.seen))
        if missing:
            raise ValueError(
                f"incomplete run: {len(missing)} rows missing, e.g. {missing[:5]}. "
                "A missing row is a fatal incompleteness, never a smaller denominator."
            )
        return len(self.expected)

    @property
    def expected_count(self) -> int:
        return len(self.expected)


@dataclass(frozen=True)
class PairedRadarRun:
    """In-memory M3 radar artifact, before immutable bundle persistence."""

    stage: str
    run_id: str
    run_hash: str
    source_hash: str
    capture_ids: tuple[str, ...]
    rows: tuple[dict, ...]
    shared_evidence: tuple[dict, ...]
    ahmed_evidence: tuple[dict, ...]
    production_native: tuple[dict, ...]
    selector_evidence: tuple[dict, ...]

    @property
    def source_span_count(self) -> int:
        return len({(r["capture_id"], r["k"]) for r in self.shared_evidence})

    @property
    def shared_count(self) -> int:
        return len(self.shared_evidence)

    @property
    def estimator_count(self) -> int:
        return len(self.rows)

    @property
    def evaluation_k_ge_1(self) -> "PairedRadarRun":
        """Derive the comparative universe without rerunning any selector or estimator."""
        keys = {
            (r["capture_id"], r["lock_estimand_id"], r["k"])
            for r in self.shared_evidence
            if r["k"] >= 1
        }
        return PairedRadarRun(
            stage=self.stage,
            run_id=self.run_id,
            run_hash=self.run_hash,
            source_hash=self.source_hash,
            capture_ids=self.capture_ids,
            rows=tuple(
                r
                for r in self.rows
                if (r["capture_id"], r["lock_estimand_id"], r["k"]) in keys
            ),
            shared_evidence=tuple(r for r in self.shared_evidence if r["k"] >= 1),
            ahmed_evidence=tuple(r for r in self.ahmed_evidence if r["k"] >= 1),
            production_native=tuple(r for r in self.production_native if r["k"] >= 1),
            selector_evidence=self.selector_evidence,
        )


def _canonical_run_hash(run_id: str, source_hash: str) -> str:
    return sha256(f"m8_m3_paired_v1\x00{run_id}\x00{source_hash}".encode("utf-8")).hexdigest()


def _cube_hash(cube: np.ndarray) -> str:
    return array_sha256(np.asarray(cube))


def _load_json_mapping(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RunnerContractError(f"{label} is not valid UTF-8 JSON: {path}") from exc
    if not isinstance(value, dict):
        raise RunnerContractError(f"{label} must be a JSON mapping: {path}")
    return value


def _exact_int(mapping: Mapping[str, object], key: str, label: str) -> int:
    value = mapping.get(key)
    if type(value) is not int:
        raise RunnerContractError(f"{label}.{key} must be an exact integer")
    return value


def _validate_capture_inputs(
    radar: RadarScope, capture: RadarCapture
) -> tuple[dict, dict, ChirpConfig, Path]:
    """Hash and validate one capture after authorization has already passed."""
    metadata_path = capture.metadata_path(radar.root)
    warmup_path = capture.warmup_path(radar.root)
    adc_path = capture.adc_path(radar.root)
    for path, expected, label in (
        (metadata_path, capture.metadata_sha256, "metadata"),
        (warmup_path, capture.warmup_sha256, "recorded warmup"),
        (adc_path, capture.adc_stream_sha256, "raw ADC"),
    ):
        if not path.is_file():
            raise RunnerContractError(f"{capture.capture_id}: missing {label} file {path}")
        actual = sha256_path(path)
        if actual != expected:
            raise RunnerContractError(
                f"{capture.capture_id}: {label} SHA-256 {actual} != registry {expected}"
            )

    metadata = _load_json_mapping(metadata_path, "run metadata")
    recorded_warmup = _load_json_mapping(warmup_path, "recorded warmup")
    config = metadata.get("config")
    if not isinstance(config, dict):
        raise RunnerContractError(f"{capture.capture_id}: metadata.config must be a mapping")
    config_hash = run_config_hash(config)
    if config_hash != capture.capture_config_sha256:
        raise RunnerContractError(
            f"{capture.capture_id}: embedded config hash {config_hash} != registry "
            f"{capture.capture_config_sha256}"
        )

    profile = config.get("profile")
    session = config.get("session")
    if not isinstance(profile, Mapping) or not isinstance(session, Mapping):
        raise RunnerContractError(f"{capture.capture_id}: missing profile/session configuration")
    geometry = radar.geometry
    expected_geometry = {
        "num_adc_samples": int(geometry["adc_samples"]),
        "num_rx": int(geometry["rx"]),
        "num_chirps_per_frame": int(geometry["chirps_per_frame"]),
        "iq_swap": bool(geometry["iq_swap"]),
    }
    for key, expected in expected_geometry.items():
        actual = profile.get(key)
        if type(actual) is not type(expected) or actual != expected:
            raise RunnerContractError(
                f"{capture.capture_id}: profile.{key}={actual!r} != registry {expected!r}"
            )
    fs = session.get("frame_rate_hz")
    if type(fs) not in (int, float) or float(fs) != FRAME_RATE_HZ:
        raise RunnerContractError(
            f"{capture.capture_id}: frame rate {fs!r} != frozen {FRAME_RATE_HZ} Hz"
        )
    metadata_iq_swap = metadata.get("iq_swap")
    if type(metadata_iq_swap) is not bool or metadata_iq_swap != geometry["iq_swap"]:
        raise RunnerContractError(f"{capture.capture_id}: metadata iq_swap mismatch")
    range_resolution_m = profile.get("range_resolution_m")
    if type(range_resolution_m) not in (int, float) or not math.isfinite(
        float(range_resolution_m)
    ) or float(range_resolution_m) != float(geometry["range_resolution_m_approx"]):
        raise RunnerContractError(f"{capture.capture_id}: range-resolution geometry mismatch")
    calculated_bytes_per_frame = (
        expected_geometry["num_adc_samples"]
        * expected_geometry["num_rx"]
        * expected_geometry["num_chirps_per_frame"]
        * 4
    )
    if calculated_bytes_per_frame != int(geometry["bytes_per_frame"]):
        raise RunnerContractError("registry bytes_per_frame is inconsistent with its geometry")
    if geometry.get("sample_dtype") != "complex_int16":
        raise RunnerContractError("registry sample_dtype must be complex_int16")

    recorded_metadata_lock = _exact_int(metadata, "locked_bin", "run_metadata")
    recorded_warmup_lock = recorded_warmup.get("selected_bin")
    if recorded_warmup_lock is None:
        recorded_warmup_lock = recorded_warmup.get("selected_locked_bin")
    if type(recorded_warmup_lock) is not int:
        raise RunnerContractError(f"{capture.capture_id}: recorded warmup has no exact selected bin")
    if not (
        recorded_metadata_lock == recorded_warmup_lock == capture.recorded_lock
    ):
        raise RunnerContractError(
            f"{capture.capture_id}: recorded lock mismatch: registry={capture.recorded_lock}, "
            f"metadata={recorded_metadata_lock}, warmup={recorded_warmup_lock}"
        )

    bytes_per_frame = int(geometry["bytes_per_frame"])
    size = adc_path.stat().st_size
    if size % bytes_per_frame != 0 or size // bytes_per_frame != capture.frames:
        raise RunnerContractError(
            f"{capture.capture_id}: raw size {size} does not encode exactly "
            f"{capture.frames} x {bytes_per_frame}-byte frames"
        )
    chirp = ChirpConfig(
        num_adc_samples=expected_geometry["num_adc_samples"],
        num_rx=expected_geometry["num_rx"],
        num_tx=1,
        num_chirps_per_frame=expected_geometry["num_chirps_per_frame"],
        num_frames=capture.frames,
        frame_rate_hz=FRAME_RATE_HZ,
        range_resolution_m=float(profile["range_resolution_m"]),
        iq_swap=expected_geometry["iq_swap"],
    )
    return metadata, recorded_warmup, chirp, adc_path


def _validate_suite_set(suites: Sequence[WindowEstimatorSuite]) -> dict[str, EstimatorArmSpec]:
    if not suites:
        raise RunnerContractError("at least one estimator suite is required")
    by_arm: dict[str, EstimatorArmSpec] = {}
    for suite in suites:
        for spec in suite.arm_specs:
            if spec.arm_id in by_arm:
                raise RunnerContractError(f"duplicate arm declaration {spec.arm_id!r}")
            by_arm[spec.arm_id] = spec
    if tuple(by_arm) != CANONICAL_ARM_IDS:
        raise RunnerContractError(
            f"suite arm order/identity {tuple(by_arm)} != canonical {CANONICAL_ARM_IDS}"
        )
    return by_arm


def _validate_native_result(spec: EstimatorArmSpec, native: Mapping[str, object]) -> None:
    for key in ("hr_valid", "br_valid"):
        if type(native.get(key)) is not bool:
            raise RunnerContractError(f"{spec.arm_id}: {key} must be an exact bool")
    for valid_key, value_key in (("hr_valid", "hr_raw"), ("br_valid", "br_bpm")):
        value = native.get(value_key)
        if native[valid_key] and (
            type(value) not in (int, float) or not math.isfinite(float(value))
        ):
            raise RunnerContractError(
                f"{spec.arm_id}: {value_key} must be finite when {valid_key}=true"
            )
    if type(native.get("br_confidence")) is not str or type(native.get("rej_reason")) is not str:
        raise RunnerContractError(
            f"{spec.arm_id}: br_confidence and rej_reason must be strings"
        )
    f_r_hz = native.get("f_r_hz")
    if f_r_hz is not None and (
        type(f_r_hz) not in (int, float) or not math.isfinite(float(f_r_hz))
    ):
        raise RunnerContractError(f"{spec.arm_id}: f_r_hz must be finite or null")
    for field, expected in (
        ("arm_id", spec.arm_id),
        ("estimator_id", spec.estimator_id),
        ("run_config_hash", spec.run_config_hash),
    ):
        if field in native and native[field] != expected:
            raise RunnerContractError(
                f"{spec.arm_id}: native {field}={native[field]!r} != declared {expected!r}"
            )


def _production_no_gate_reason(native: Mapping[str, object]) -> str:
    """Map the production producer's exact no-AHET state to its documented reason.

    ``run_window_dsp`` historically leaves ``rej_reason`` empty when respiration does
    not admit an AHET run.  The unchanged native evidence still identifies that state
    exactly: rank -1, all three rejection slots -1, and no in-gate respiration rate.
    This is evidence normalization only; it does not alter the estimator payload or
    infer a reason from a reference value or observed accuracy.
    """
    hr_result = native.get("hr_result")
    if not isinstance(hr_result, Mapping):
        return ""
    accepted_rank = hr_result.get("accepted_candidate_rank")
    rejection_codes = hr_result.get("candidate_rejection_code")
    if (
        type(accepted_rank) is not int
        or accepted_rank != -1
        or type(rejection_codes) is not np.ndarray
        or rejection_codes.shape != (AHET_MAX_CANDIDATES,)
        or not np.issubdtype(rejection_codes.dtype, np.signedinteger)
        or not np.all(rejection_codes == REJECTION_CODE_NOT_ATTEMPTED)
    ):
        return ""
    respiration_hz = native.get("f_r_hz")
    if respiration_hz is not None and (
        type(respiration_hz) not in (int, float)
        or not math.isfinite(float(respiration_hz))
    ):
        return ""
    respiration_is_in_gate = (
        respiration_hz is not None
        and RESP_GATE_LO_HZ <= respiration_hz <= RESP_GATE_HI_HZ
    )
    return "" if respiration_is_in_gate else "gate_not_run"


def _vital_validity_reasons(
    spec: EstimatorArmSpec,
    native: Mapping[str, object],
) -> tuple[str, str]:
    """Return independent HR and BR validity reasons already present in native evidence.

    The legacy ``rej_reason`` belongs to the production/Ahmed heart decision.  Reusing it
    for BR would make BR coverage unreconcilable.  Ahmed exposes its BR reason directly in
    ``breath_evidence``.  Production exposes either its explicit edge veto or its exact
    respiration-confidence state; no new validity rule is introduced here.
    """
    hr_reason = "ok" if native["hr_valid"] else str(native["rej_reason"] or "")
    if not hr_reason and spec.arm_id == PRODUCTION_ARM_ID:
        hr_reason = _production_no_gate_reason(native)
    if not hr_reason:
        raise RunnerContractError("invalid HR estimate is missing its native rejection reason")

    if native["br_valid"]:
        return hr_reason, "ok"

    breath_evidence = native.get("breath_evidence")
    if isinstance(breath_evidence, Mapping):
        br_reason = str(breath_evidence.get("reason", "") or "")
    else:
        br_result = native.get("br_result")
        edge_reason = (
            str(br_result.get("resp_edge_veto_reason", "") or "")
            if isinstance(br_result, Mapping)
            else ""
        )
        br_reason = edge_reason or f"resp_confidence_{native['br_confidence']}"
    if not br_reason:
        raise RunnerContractError("invalid BR estimate is missing its native rejection reason")
    return hr_reason, br_reason


def _selector_lock(
    cube: np.ndarray,
    capture: RadarCapture,
    config: dict,
    selector_fn: Callable,
    diagnostic_sink: Callable[[Mapping[str, object]], None] | None,
) -> tuple[int, dict]:
    k0 = cube[:FRAMES_PER_WINDOW]
    candidates = derive_candidate_bins(config)
    selected, _winning_payload, evidence = selector_fn(
        k0, candidates, config, FRAME_RATE_HZ
    )
    if not isinstance(evidence, Mapping):
        raise RunnerContractError("warmup selector evidence must be a mapping")
    persisted = dict(evidence)
    persisted.update(
        selector_id="production_warmup_selector_v1",
        capture_id=capture.capture_id,
        selected_bin=int(selected),
        selector_config_hash=run_config_hash(config),
    )
    if diagnostic_sink is not None:
        diagnostic_sink(persisted)
    failed = [row for row in evidence.get("candidates", []) if row.get("failed") is True]
    if failed:
        raise SelectorContractError(
            f"{capture.capture_id}: warmup selector hid {len(failed)} candidate DSP failures",
            persisted,
        )
    if evidence.get("fallback_used") is not False:
        raise SelectorContractError(
            f"{capture.capture_id}: warmup selector used or did not explicitly rule out fallback",
            persisted,
        )
    if type(selected) is not int or selected != capture.rerun_lock:
        raise SelectorContractError(
            f"{capture.capture_id}: rerun lock {selected!r} != expected {capture.rerun_lock}",
            persisted,
        )
    return selected, persisted


def _ahmed_evidence_row(base: Mapping[str, object], arm_id: str, native: Mapping[str, object]) -> dict:
    heart_evidence = native.get("heart_evidence")
    breath_evidence = native.get("breath_evidence")
    if not isinstance(breath_evidence, Mapping):
        raise RunnerContractError(f"{arm_id}: missing Ahmed breathing evidence")
    heart_evidence_status = (
        EVIDENCE_COMPUTED
        if isinstance(heart_evidence, Mapping)
        else EVIDENCE_NOT_COMPUTED_BREATHING_UNAVAILABLE
    )
    if not isinstance(heart_evidence, Mapping):
        # Expected invalidity from a required BR dependency must still leave a complete
        # heart-domain evidence row, so the row keeps its shape and stays aligned with
        # the candidate bins rather than being dropped.  Nothing below was measured: no
        # suppression rule was evaluated (there is no breathing bin to suppress around)
        # and no accumulation was run.  ``heart_evidence_status`` above is what says so
        # in the persisted evidence; the arrays alone cannot be read as results.
        candidates = np.asarray(base["heart_candidate_bins"], dtype=np.int64)
        harmonics = int(native["harmonic_count"])
        heart_evidence = {
            "candidate_bins": candidates,
            "harmonic_bins": candidates[:, None]
            * np.arange(1, harmonics + 1, dtype=np.int64)[None, :],
            "supported_mask": candidates * harmonics < int(base["n_fft"]) / 2,
            "nyquist_degenerate_mask": candidates * harmonics == int(base["n_fft"]) / 2,
            "suppression_mask": np.zeros(candidates.size, dtype=bool),
            "eligible_mask": np.zeros(candidates.size, dtype=bool),
            "scores_pre_exclusion": np.full(candidates.size, np.nan),
            "scores": np.full(candidates.size, np.nan),
            "selected_bin": None,
            "selected_hz": None,
            "selected_score": None,
            "runner_up_bin": None,
            "runner_up_hz": None,
            "runner_up_score": None,
        }
    required = (
        "candidate_bins",
        "harmonic_bins",
        "supported_mask",
        "nyquist_degenerate_mask",
        "suppression_mask",
        "eligible_mask",
        "scores_pre_exclusion",
        "scores",
        "selected_bin",
        "selected_hz",
        "selected_score",
        "runner_up_bin",
        "runner_up_hz",
        "runner_up_score",
    )
    for stage, evidence in (("breath", breath_evidence), ("heart", heart_evidence)):
        missing = [key for key in required if key not in evidence]
        if missing:
            raise RunnerContractError(f"{arm_id}: incomplete Ahmed {stage} evidence {missing}")

    row = {
        "capture_id": base["capture_id"],
        "lock_estimand_id": base["lock_estimand_id"],
        "arm_id": arm_id,
        "k": base["k"],
        "cube_hash": base["cube_hash"],
        "signal_hash": base["signal_hash"],
        "config_hash": native["run_config_hash"],
        "source_hash": base["source_hash"],
        "run_hash": base["run_hash"],
    }
    for stage, evidence, valid, reason, reported_bpm, evidence_status in (
        (
            "breath",
            breath_evidence,
            native["br_valid"],
            breath_evidence.get("reason", "") or "ok",
            native["br_bpm"] if native["br_valid"] else None,
            # The breathing stage always runs; a missing payload is a contract error above.
            EVIDENCE_COMPUTED,
        ),
        (
            "heart",
            heart_evidence,
            native["hr_valid"],
            native["rej_reason"] or "ok",
            native["hr_raw"] if native["hr_valid"] else None,
            heart_evidence_status,
        ),
    ):
        row.update(
            {
                f"{stage}_candidate_bins": evidence["candidate_bins"],
                f"{stage}_harmonic_bins": evidence["harmonic_bins"],
                f"{stage}_support_mask": evidence["supported_mask"],
                f"{stage}_nyquist_mask": evidence["nyquist_degenerate_mask"],
                f"{stage}_suppression_mask": evidence["suppression_mask"],
                f"{stage}_eligibility_mask": evidence["eligible_mask"],
                f"{stage}_score_pre_suppression": evidence["scores_pre_exclusion"],
                f"{stage}_score_post_suppression": evidence["scores"],
                f"{stage}_selected_bin": evidence["selected_bin"],
                f"{stage}_selected_frequency_hz": evidence["selected_hz"],
                f"{stage}_selected_score": evidence["selected_score"],
                f"{stage}_runner_up_bin": evidence["runner_up_bin"],
                f"{stage}_runner_up_frequency_hz": evidence["runner_up_hz"],
                f"{stage}_runner_up_score": evidence["runner_up_score"],
                f"{stage}_valid": valid,
                f"{stage}_reason": reason,
                f"{stage}_evidence_status": evidence_status,
                f"{stage}_reported_bpm": reported_bpm,
            }
        )
    return row


def execute_paired_runner(
    *,
    radar: RadarScope,
    capture_ids: Sequence[str],
    suites: Sequence[WindowEstimatorSuite],
    run_id: str,
    source_hash: str,
    stage: str = "real-radar",
    decode_fn: Callable[[Path, ChirpConfig], np.ndarray] = read_adc_bin,
    selector_fn: Callable = run_warmup_selection,
    selector_config: Mapping[str, object] | None = None,
    selector_diagnostic_sink: Callable[[Mapping[str, object]], None] | None = None,
    smoke: bool = False,
) -> PairedRadarRun:
    """Decode each authorized capture once and run the exact paired Cartesian grid.

    This portable core assumes preflight already passed.  External callers should use
    :func:`run_radar_stage`, which performs authorization before calling this function.
    """
    specs = _validate_suite_set(suites)
    if selector_config is not None:
        selector_config = dict(selector_config)
        production_suites = [suite for suite in suites if PRODUCTION_ARM_ID in {
            spec.arm_id for spec in suite.arm_specs
        }]
        if len(production_suites) != 1:
            raise RunnerContractError("exactly one production suite is required")
        if production_suites[0].suite_config_hash != run_config_hash(selector_config):
            raise RunnerContractError(
                "warmup selector and production estimator must use the same resolved config"
            )
    capture_ids = tuple(capture_ids)
    if smoke and capture_ids != ("m1",):
        raise RunnerContractError("real-smoke scope is exactly capture m1")
    run_hash = _canonical_run_hash(run_id, source_hash)
    ledger = CartesianLedger.build(radar, capture_ids, LOCK_ESTIMANDS, CANONICAL_ARM_IDS)
    rows: list[dict] = []
    shared_rows: list[dict] = []
    ahmed_rows: list[dict] = []
    production_native: list[dict] = []
    selector_rows: list[dict] = []

    for capture_id in capture_ids:
        capture = radar.capture(capture_id)
        metadata, _recorded_warmup, chirp, adc_path = _validate_capture_inputs(radar, capture)
        try:
            start_wall = datetime.fromisoformat(str(metadata["start_wall_utc"]))
            if start_wall.tzinfo is None:
                raise ValueError("timestamp is timezone-naive")
            approximate_frame0_epoch = start_wall.timestamp()
        except Exception as exc:
            raise RunnerContractError(
                f"{capture_id}: start_wall_utc must be a timezone-aware ISO timestamp"
            ) from exc
        try:
            cube = np.asarray(decode_fn(adc_path, chirp))
        except Exception as exc:
            raise RunnerContractError(f"{capture_id}: ADC decode failed") from exc
        expected_shape = (
            capture.frames,
            chirp.num_chirps_per_frame,
            chirp.num_rx,
            chirp.num_adc_samples,
        )
        if cube.shape != expected_shape or cube.dtype != np.complex64:
            raise RunnerContractError(
                f"{capture_id}: decoded cube {cube.shape}/{cube.dtype} != "
                f"{expected_shape}/complex64"
            )
        cube.setflags(write=False)
        capture_cube_hash = _cube_hash(cube)
        try:
            selected_rerun, selector_evidence = _selector_lock(
                cube,
                capture,
                metadata["config"] if selector_config is None else dict(selector_config),
                selector_fn,
                selector_diagnostic_sink,
            )
        except SelectorContractError:
            raise
        except Exception as exc:
            raise RunnerContractError(f"{capture_id}: warmup selector failed") from exc
        selector_rows.append(selector_evidence)
        locks = {
            LOCK_ESTIMANDS[0]: capture.recorded_lock,
            LOCK_ESTIMANDS[1]: selected_rerun,
        }
        max_k = 1 if smoke else capture.windows

        for lock_id, locked_bin in locks.items():
            for k in range(max_k):
                frame_start, frame_stop = window_frame_span(k)
                window = cube[frame_start:frame_stop]
                if window.shape[0] != FRAMES_PER_WINDOW:
                    raise RunnerContractError(f"{capture_id}/{k}: incomplete decoded window")
                window.setflags(write=False)
                initial_window_hash = _cube_hash(window)
                suite_results: dict[str, SuiteWindowResult] = {}
                for suite in suites:
                    before = _cube_hash(window)
                    try:
                        result = suite(window, locked_bin, FRAME_RATE_HZ)
                    except Exception as exc:
                        raise RunnerContractError(
                            f"{capture_id}/{lock_id}/k{k}: unexpected {suite.suite_id} failure"
                        ) from exc
                    after = _cube_hash(window)
                    if before != initial_window_hash or after != initial_window_hash:
                        raise RunnerContractError(
                            f"{capture_id}/{lock_id}/k{k}: suite {suite.suite_id} mutated input"
                        )
                    validate_returned_arms(suite.arm_specs, result)
                    suite_results[suite.suite_id] = result
                if _cube_hash(cube) != capture_cube_hash:
                    raise RunnerContractError(f"{capture_id}: decoded base cube was mutated")

                ahmed_result = next(
                    result
                    for result in suite_results.values()
                    if set(result.arm_native_results) == set(APPROVED_ARM_IDS)
                )
                phase = np.asarray(ahmed_result.shared_evidence["phase"], dtype=np.float64)
                production_result = next(
                    result
                    for result in suite_results.values()
                    if set(result.arm_native_results) == {PRODUCTION_ARM_ID}
                )
                production_phase = np.asarray(
                    production_result.arm_native_results[PRODUCTION_ARM_ID].get("phase_raw")
                )
                if (
                    production_phase.dtype == object
                    or production_phase.shape != phase.shape
                    or not np.array_equal(production_phase, phase, equal_nan=True)
                ):
                    raise RunnerContractError(
                        f"{capture_id}/{lock_id}/k{k}: production and Ahmed did not "
                        "reconstruct the same raw phase signal"
                    )
                header = json.dumps(
                    {
                        "schema": "m8_m3_shared_signal_v1",
                        "run_hash": run_hash,
                        "capture_id": capture_id,
                        "lock_estimand_id": lock_id,
                        "locked_bin": locked_bin,
                        "k": k,
                        "frame_start": frame_start,
                        "frame_stop": frame_stop,
                        "fs_hz": FRAME_RATE_HZ,
                        "dtype": "float64",
                        "shape": list(phase.shape),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                signal_hash = phase_signal_hash(header, phase)
                shared = {
                    "capture_id": capture_id,
                    "lock_estimand_id": lock_id,
                    "locked_bin": locked_bin,
                    "k": k,
                    "frame_start": frame_start,
                    "frame_stop": frame_stop,
                    "epoch_start": approximate_frame0_epoch + frame_start / FRAME_RATE_HZ,
                    "epoch_stop": approximate_frame0_epoch + frame_stop / FRAME_RATE_HZ,
                    "sample_time_s": np.arange(FRAMES_PER_WINDOW, dtype=np.float64)
                    / FRAME_RATE_HZ,
                    "phase": phase,
                    "frequency_grid_hz": ahmed_result.shared_evidence[
                        "spectrum_frequencies_hz"
                    ],
                    "spectrum_magnitude": ahmed_result.shared_evidence["spectrum_magnitude"],
                    "heart_candidate_bins": ahmed_result.shared_evidence["heart_candidate_bins"],
                    "n_fft": ahmed_result.shared_evidence["n_fft"],
                    "cube_hash": capture_cube_hash,
                    "window_cube_hash": initial_window_hash,
                    "signal_hash": signal_hash,
                    "config_hash": capture.capture_config_sha256,
                    "source_hash": source_hash,
                    "run_hash": run_hash,
                }
                shared_rows.append(shared)

                for suite in suites:
                    result = suite_results[suite.suite_id]
                    for arm_id, native in result.arm_native_results.items():
                        spec = specs[arm_id]
                        _validate_native_result(spec, native)
                        hr_validity_reason, br_validity_reason = _vital_validity_reasons(
                            spec, native
                        )
                        row = {
                            "schema_version": 2,
                            "run_id": run_id,
                            "run_hash": run_hash,
                            "source_hash": source_hash,
                            "capture_id": capture_id,
                            "lock_estimand_id": lock_id,
                            "locked_bin": locked_bin,
                            "k": k,
                            "frame_start": frame_start,
                            "frame_stop": frame_stop,
                            "epoch_start": approximate_frame0_epoch
                            + frame_start / FRAME_RATE_HZ,
                            "epoch_stop": approximate_frame0_epoch
                            + frame_stop / FRAME_RATE_HZ,
                            "time_origin_id": "start_wall_utc_approximate_v1",
                            "origin_is_approximate": True,
                            "evaluation_status": "exploratory_non_frozen",
                            "window_set": (
                                "full_k0_diagnostic" if k == 0 else "evaluation_k_ge_1"
                            ),
                            "window_origin_role": (
                                "lock_selection_in_sample" if k == 0 else "evaluation"
                            ),
                            "suite_id": suite.suite_id,
                            "suite_config_hash": suite.suite_config_hash,
                            "arm_id": arm_id,
                            "estimator_id": spec.estimator_id,
                            "arm_config_hash": spec.run_config_hash,
                            "cube_hash": capture_cube_hash,
                            "window_cube_hash": initial_window_hash,
                            "shared_signal_hash": signal_hash,
                            "capture_config_hash": capture.capture_config_sha256,
                            "hr_valid": native["hr_valid"],
                            "hr_raw": (
                                float(native["hr_raw"]) if native["hr_valid"] else None
                            ),
                            "br_valid": native["br_valid"],
                            "br_bpm": (
                                float(native["br_bpm"]) if native["br_valid"] else None
                            ),
                            # Retained for old readers; this field has always described HR.
                            "validity_reason": hr_validity_reason,
                            "hr_validity_reason": hr_validity_reason,
                            "br_validity_reason": br_validity_reason,
                            "outcome": result.arm_outcomes.get(arm_id),
                        }
                        rows.append(row)
                        ledger.record(capture_id, lock_id, k, arm_id)
                        if arm_id == PRODUCTION_ARM_ID:
                            production_native.append(
                                {
                                    "capture_id": capture_id,
                                    "lock_estimand_id": lock_id,
                                    "k": k,
                                    "cube_hash": capture_cube_hash,
                                    "signal_hash": signal_hash,
                                    "config_hash": spec.run_config_hash,
                                    "source_hash": source_hash,
                                    "run_hash": run_hash,
                                    "native": native,
                                }
                            )
                        else:
                            ahmed_rows.append(_ahmed_evidence_row(shared, arm_id, native))

        if _cube_hash(cube) != capture_cube_hash:
            raise RunnerContractError(f"{capture_id}: decoded cube changed before release")

    expected = sum((1 if smoke else radar.capture(cid).windows) for cid in capture_ids)
    expected_estimator_rows = expected * len(LOCK_ESTIMANDS) * len(CANONICAL_ARM_IDS)
    if smoke:
        # The ledger was constructed for all m1 windows; smoke intentionally executes k0 only.
        expected_keys = {
            key for key in ledger.expected if key[2] == 0
        }
        ledger.expected = expected_keys
    if ledger.assert_complete() != expected_estimator_rows:
        raise RunnerContractError("Cartesian ledger count disagrees with runner count")
    if not (
        len(shared_rows) == expected * 2
        and len(ahmed_rows) == expected * 2 * 6
        and len(production_native) == expected * 2
    ):
        raise RunnerContractError("shared/Ahmed/production evidence cardinality mismatch")

    result = PairedRadarRun(
        stage=stage,
        run_id=run_id,
        run_hash=run_hash,
        source_hash=source_hash,
        capture_ids=capture_ids,
        rows=tuple(rows),
        shared_evidence=tuple(shared_rows),
        ahmed_evidence=tuple(ahmed_rows),
        production_native=tuple(production_native),
        selector_evidence=tuple(selector_rows),
    )
    # Serialization is part of the run contract, not a best-effort post-processing step.
    pack_shared_evidence(result.shared_evidence)
    pack_ahmed_evidence(result.ahmed_evidence)
    serialize_native_tree(list(result.production_native))
    return result


def persist_radar_artifacts(
    run: PairedRadarRun,
    *,
    out_root: Path,
    provenance: Mapping[str, object],
    parents: Mapping[str, str],
    promotion_eligible: bool,
) -> StageBundle:
    """Persist the complete M3 payload as one immutable stage bundle."""
    shared_arrays = pack_shared_evidence(run.shared_evidence)
    ahmed_arrays = pack_ahmed_evidence(run.ahmed_evidence)
    native_tree = serialize_native_tree(list(run.production_native))
    writer = BundleWriter(stage_root=out_root, stage=run.stage, run_id=run.run_id)
    writer.add_json("rows.json", {"schema_version": 2, "rows": list(run.rows)})
    writer.add_json(
        "selector_evidence.json",
        {"schema_version": 1, "selectors": list(run.selector_evidence)},
    )
    writer.add_npz("shared_evidence.npz", shared_arrays)
    writer.add_npz("ahmed_evidence.npz", ahmed_arrays)
    writer.add_json("production_native_index.json", native_tree.index)
    writer.add_npz("production_evidence.npz", native_tree.arrays)
    writer.add_text(
        "resolved_config.yaml",
        yaml.safe_dump(
            {
                "schema_version": 1,
                "experiment_id": "m8_canonical_two_lock_seven_arm_v1",
                "run_id": run.run_id,
                "run_hash": run.run_hash,
                "source_hash": run.source_hash,
                "capture_ids": list(run.capture_ids),
                "lock_estimands": list(LOCK_ESTIMANDS),
                "arm_ids": list(CANONICAL_ARM_IDS),
                "frames_per_window": FRAMES_PER_WINDOW,
                "frame_rate_hz": FRAME_RATE_HZ,
                "score_status": "disabled_until_m4",
            },
            sort_keys=True,
        ),
    )
    return writer.finalize(
        status="complete",
        provenance=provenance,
        promotion_eligible=promotion_eligible,
        parents=parents,
        extra_manifest={
            "source_span_count": run.source_span_count,
            "shared_row_count": run.shared_count,
            "estimator_row_count": run.estimator_count,
            "ahmed_evidence_count": len(run.ahmed_evidence),
            "production_evidence_count": len(run.production_native),
            "score_status": "disabled_until_m4",
        },
    )


def run_radar_stage(
    *,
    stage: str,
    gate_dir: Path,
    authorization_path: Path,
    source_manifest_sha256: str,
    radar: RadarScope,
    suites: Sequence[WindowEstimatorSuite],
    run_id: str,
    out_root: Path | None = None,
    parent_dir: Path | None = None,
    decode_fn: Callable[[Path, ChirpConfig], np.ndarray] = read_adc_bin,
    selector_fn: Callable = run_warmup_selection,
    selector_config: Mapping[str, object] | None = None,
    authorization_validator: Callable[[Path, Authorization], None] | None = None,
    require_scientific_gate: bool = False,
) -> PairedRadarRun | StageBundle:
    """Authorized M3 entry point. Preflight is complete before capture stat/open."""
    preflight = verify_preflight(
        stage=stage,
        gate_dir=gate_dir,
        authorization_path=authorization_path,
        source_manifest_sha256=source_manifest_sha256,
        parent_dir=parent_dir,
        authorization_validator=authorization_validator,
        require_scientific_gate=require_scientific_gate,
    )
    authorization = preflight.authorization
    if authorization.lock_estimands != LOCK_ESTIMANDS:
        raise PreflightError("authorization does not bind the exact two canonical locks")
    if authorization.arm_ids != CANONICAL_ARM_IDS:
        raise PreflightError("authorization does not bind the exact seven canonical arms")
    all_capture_ids = tuple(sorted(radar.captures))
    if tuple(sorted(authorization.capture_ids)) != all_capture_ids:
        raise PreflightError("authorization does not bind the complete capture registry")
    if stage == "real-radar" and parent_dir is None:
        raise PreflightError("real-radar requires the completed real-smoke parent")
    if stage == "real-radar":
        parent_manifest = verify_bundle(Path(parent_dir))
        if parent_manifest.get("stage") != "smoke":
            raise PreflightError("real-radar parent must be the completed smoke stage")
    capture_ids = ("m1",) if stage == "real-smoke" else all_capture_ids
    run = execute_paired_runner(
        radar=radar,
        capture_ids=capture_ids,
        suites=suites,
        run_id=run_id,
        source_hash=source_manifest_sha256,
        stage="smoke" if stage == "real-smoke" else "radar",
        decode_fn=decode_fn,
        selector_fn=selector_fn,
        selector_config=selector_config,
        smoke=stage == "real-smoke",
    )
    if out_root is None:
        return run
    parents = {"synthetic": preflight.gate_manifest_sha256}
    if preflight.parent_manifest_sha256 is not None:
        parents["smoke"] = preflight.parent_manifest_sha256
    return persist_radar_artifacts(
        run,
        out_root=out_root,
        provenance={
            "schema_version": 1,
            "run_hash": run.run_hash,
            "source_manifest_sha256": source_manifest_sha256,
            "authorization_id": authorization.authorization_id,
            "authorization_sha256": sha256_path(authorization_path),
        },
        parents=parents,
        promotion_eligible=True,
    )
